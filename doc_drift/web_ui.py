"""
web_ui.py - Interactive Visual Dashboard for Documentation Drift Detection.
Provides a local browser UI for reviewing pull request diffs side-by-side with
documentation files, visualizing highlighted drift sentences, confidence meters,
and testing 1-click documentation fixes. Supports direct GitHub URL analysis.
"""

from __future__ import annotations

import json
import logging
import os
import sys
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Any
from urllib.parse import parse_qs, urlparse

from .autofix import DocFix, _apply_symbol_replacement, generate_github_suggestion_block
from .benchmark import run_benchmarks
from .diff_parser import parse_diff
from .doc_scanner import DocSentence, _parse_markdown_sentences
from .github_fetcher import fetch_target_from_url
from .reporter import generate_json_report, generate_markdown_summary
from .semantic_matcher import find_drift

logger = logging.getLogger(__name__)

SAMPLE_DIFF = """--- a/worker/cli.py
+++ b/worker/cli.py
@@ -40,3 +40,3 @@ def setup_arguments(parser):
-    parser.add_argument("--timeout", type=int, default=30, help="Job timeout limit")
+    parser.add_argument("--deadline", type=int, default=60, help="Job timeout limit")
"""

SAMPLE_DOC = """# Worker CLI Reference

The worker CLI coordinates background execution tasks.

## Configuration Options

To configure the worker, supply command line arguments during launch:

- You can specify `--timeout` in seconds to terminate stalled worker jobs before failure.
- Default execution timeout is set to 30 seconds across all worker pools.
"""


class DocDriftWebHandler(BaseHTTPRequestHandler):
    """HTTP request handler for Doc Drift visual dashboard and REST API."""

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        path = parsed.path

        if path in ("/", "/index.html"):
            self._serve_html()
        elif path == "/api/samples":
            self._serve_json({
                "diff": SAMPLE_DIFF,
                "doc": SAMPLE_DOC,
                "doc_name": "cli_reference.md",
            })
        elif path == "/api/benchmark":
            results = run_benchmarks()
            self._serve_json(results)
        else:
            self.send_error(404, "Endpoint not found")

    def do_POST(self) -> None:
        parsed = urlparse(self.path)
        path = parsed.path

        content_length = int(self.headers.get("Content-Length", 0))
        raw_body = self.rfile.read(content_length).decode("utf-8") if content_length > 0 else "{}"

        try:
            body = json.loads(raw_body)
        except Exception:
            self.send_error(400, "Invalid JSON payload")
            return

        if path == "/api/analyze":
            self._handle_analyze(body)
        elif path == "/api/analyze-github":
            self._handle_analyze_github(body)
        else:
            self.send_error(404, "Endpoint not found")

    def _serve_html(self) -> None:
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()
        self.wfile.write(HTML_TEMPLATE.encode("utf-8"))

    def _serve_json(self, data: Any, status: int = 200) -> None:
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(json.dumps(data, indent=2).encode("utf-8"))

    def _handle_analyze_github(self, payload: dict[str, Any]) -> None:
        url = payload.get("url", "").strip()
        if not url:
            self._serve_json({"error": "GitHub URL is required"}, status=400)
            return

        try:
            target = fetch_target_from_url(url)
            diff_text = target["diff_text"]
            doc_text = target["doc_text"]
            doc_name = target["doc_name"]

            analysis = self._run_analysis_logic(diff_text, doc_text, doc_name)
            analysis.update({
                "source_url": url,
                "owner": target["owner"],
                "repo": target["repo"],
                "pr_number": target["pr_number"],
                "diff_text": diff_text,
                "doc_text": doc_text,
                "doc_name": doc_name,
            })
            self._serve_json(analysis)
        except Exception as e:
            logger.exception("GitHub URL analysis failed")
            self._serve_json({"error": str(e)}, status=400)

    def _handle_analyze(self, payload: dict[str, Any]) -> None:
        diff_text = payload.get("diff", "")
        doc_text = payload.get("doc", "")
        doc_name = payload.get("doc_name", "README.md")

        report = self._run_analysis_logic(diff_text, doc_text, doc_name)
        self._serve_json(report)

    def _run_analysis_logic(self, diff_text: str, doc_text: str, doc_name: str) -> dict[str, Any]:
        changes = parse_diff(diff_text)
        sentences = _parse_markdown_sentences(doc_name, doc_text)
        findings = find_drift(changes, sentences)

        # Generate in-memory fixes for the supplied doc text
        doc_lines = doc_text.splitlines()
        fixes = []
        for f in findings:
            if f.new_symbol and 0 <= (f.doc_line - 1) < len(doc_lines):
                orig = doc_lines[f.doc_line - 1]
                suggested = _apply_symbol_replacement(orig, f.changed_symbol, f.new_symbol)
                if suggested != orig:
                    fixes.append(DocFix(
                        doc_file=f.doc_file,
                        doc_line=f.doc_line,
                        original_line=orig,
                        suggested_line=suggested,
                        symbol_replaced=f.changed_symbol,
                        new_symbol=f.new_symbol,
                        confidence=f.confidence,
                    ))

        report = generate_json_report(findings, fixes)
        report["markdown_pr_comment"] = generate_markdown_summary(findings, fixes)
        report["parsed_changes"] = [
            {
                "type": c.change_type,
                "old": c.old_symbol,
                "new": c.new_symbol,
                "file": c.file,
                "line": c.line,
                "context": c.context,
            }
            for c in changes
        ]
        return report

    def log_message(self, fmt: str, *args: Any) -> None:
        """Suppress noisy request logs in production."""
        pass


def start_server(host: str = "127.0.0.1", port: int = 8765) -> HTTPServer:
    """Initialize and start the dashboard HTTP server."""
    server = HTTPServer((host, port), DocDriftWebHandler)
    return server


def launch_dashboard(host: str = "127.0.0.1", port: int = 8765, open_browser: bool = True) -> None:
    """Launch the dashboard and optionally open the user's browser."""
    server = start_server(host, port)
    url = f"http://{host}:{port}"
    print("=" * 65)
    print(f"🖥️  Doc Drift Visual Dashboard Live: {url}")
    print("   Press Ctrl+C to terminate the dashboard server.")
    print("=" * 65)

    if open_browser:
        import webbrowser
        threading.Timer(0.6, lambda: webbrowser.open(url)).start()

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopping dashboard server...")
        server.server_close()


HTML_TEMPLATE = """<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Doc Drift Detector — Visual Dashboard</title>
  <script src="https://cdn.tailwindcss.com"></script>
  <style>
    body { background-color: #0d1117; color: #c9d1d9; font-family: ui-sans-serif, system-ui, -apple-system, sans-serif; }
    .glass { background: rgba(22, 27, 34, 0.85); backdrop-filter: blur(12px); border: 1px solid #30363d; }
    .glow-cyan { box-shadow: 0 0 20px rgba(56, 189, 248, 0.2); }
  </style>
</head>
<body class="min-h-screen p-4 md:p-8">
  <div class="max-w-7xl mx-auto space-y-6">
    
    <!-- Header -->
    <header class="flex flex-col md:flex-row items-start md:items-center justify-between pb-6 border-b border-gray-800 gap-4">
      <div>
        <div class="flex items-center gap-3">
          <span class="text-3xl">🔍</span>
          <h1 class="text-2xl font-bold text-white tracking-tight">Doc Drift Detector <span class="text-xs px-2 py-1 rounded bg-sky-950 text-sky-400 border border-sky-800 ml-2">v1.0 Live</span></h1>
        </div>
        <p class="text-sm text-gray-400 mt-1">Autonomous Documentation Drift Detection & 1-Click Repair Engine</p>
      </div>
      <div class="flex items-center gap-3">
        <button id="loadSampleBtn" class="px-3 py-1.5 text-xs font-medium rounded-md bg-gray-800 hover:bg-gray-700 text-gray-200 border border-gray-700 transition">
          📂 Load Sample Diff
        </button>
        <button id="runBenchmarkBtn" class="px-3 py-1.5 text-xs font-medium rounded-md bg-indigo-950 hover:bg-indigo-900 text-indigo-300 border border-indigo-800 transition">
          📊 Benchmark (100% Acc)
        </button>
        <button id="analyzeBtn" class="px-4 py-1.5 text-xs font-semibold rounded-md bg-sky-600 hover:bg-sky-500 text-white transition flex items-center gap-1.5 shadow-lg shadow-sky-600/30">
          <span>⚡ Run Drift Analysis</span>
        </button>
      </div>
    </header>

    <!-- Direct GitHub URL Analyzer Bar -->
    <div class="glass rounded-xl p-4 border border-sky-800/60 bg-sky-950/20 shadow-lg">
      <div class="flex flex-col md:flex-row items-stretch md:items-center gap-3">
        <div class="flex items-center gap-2 text-sky-400 font-semibold text-xs whitespace-nowrap">
          <span class="text-base">🐙</span> GitHub URL:
        </div>
        <input id="githubUrlInput" type="text" placeholder="Paste GitHub PR or Repo link (e.g. https://github.com/DakshG26/doc-drift/pull/2 or pallets/click#2600)" class="flex-1 bg-black/60 border border-gray-700 rounded-lg px-3 py-2 text-xs text-gray-100 placeholder-gray-500 focus:outline-none focus:border-sky-400 font-mono">
        <button id="analyzeGithubBtn" class="px-5 py-2 text-xs font-semibold rounded-lg bg-sky-500 hover:bg-sky-400 text-white transition flex items-center justify-center gap-2 shadow-md shadow-sky-500/20 whitespace-nowrap">
          <span>🚀 Fetch & Analyze GitHub URL</span>
        </button>
      </div>
      <div id="githubStatus" class="hidden text-xs text-sky-300 mt-2 flex items-center gap-2">
        <span class="animate-pulse">⏳</span> Fetching PR diff and documentation from GitHub API...
      </div>
    </div>

    <!-- Benchmark Results Modal / Banner -->
    <div id="benchmarkBanner" class="hidden glass rounded-xl p-4 border-indigo-800 bg-indigo-950/30">
      <div class="flex items-center justify-between mb-2">
        <h3 class="text-sm font-semibold text-indigo-300 flex items-center gap-2">
          <span>📊</span> Historical Open-Source Benchmark Evaluation
        </h3>
        <button onclick="document.getElementById('benchmarkBanner').classList.add('hidden')" class="text-gray-400 hover:text-white text-xs">✕</button>
      </div>
      <div id="benchmarkContent" class="text-xs text-gray-300">Evaluating...</div>
    </div>

    <!-- Main Workspace (Diff vs Doc) -->
    <div class="grid grid-cols-1 lg:grid-cols-2 gap-6">
      
      <!-- Code Diff Panel -->
      <div class="glass rounded-xl p-5 space-y-3">
        <div class="flex items-center justify-between">
          <label class="text-xs font-semibold uppercase tracking-wider text-gray-400 flex items-center gap-2">
            <span>📝</span> Pull Request Diff (.patch)
          </label>
          <span class="text-xs text-gray-500">AST Parser Ready</span>
        </div>
        <textarea id="diffInput" rows="12" class="w-full bg-black/50 border border-gray-800 rounded-lg p-3 text-xs font-mono text-emerald-400 focus:outline-none focus:border-sky-500" placeholder="Paste unified git diff here or use GitHub URL above..."></textarea>
        
        <!-- Detected Code Changes -->
        <div id="detectedSymbols" class="p-3 bg-gray-900/60 rounded-lg border border-gray-800 text-xs">
          <div class="text-gray-400 font-medium mb-1.5">Parsed Code Symbol Changes:</div>
          <div id="symbolList" class="text-gray-400 italic">Click "Run Drift Analysis" to parse diff symbols...</div>
        </div>
      </div>

      <!-- Documentation Panel -->
      <div class="glass rounded-xl p-5 space-y-3">
        <div class="flex items-center justify-between">
          <label class="text-xs font-semibold uppercase tracking-wider text-gray-400 flex items-center gap-2">
            <span>📚</span> Target Documentation
          </label>
          <input id="docNameInput" type="text" value="cli_reference.md" class="bg-black/40 border border-gray-800 text-xs rounded px-2 py-0.5 text-gray-300 w-36">
        </div>
        <textarea id="docInput" rows="12" class="w-full bg-black/50 border border-gray-800 rounded-lg p-3 text-xs font-mono text-gray-300 focus:outline-none focus:border-sky-500" placeholder="Paste documentation Markdown/RST text here..."></textarea>
        
        <!-- 1-Click Fix Actions -->
        <div class="flex items-center justify-between pt-1">
          <span class="text-xs text-gray-500" id="docStats">0 sentences indexed</span>
          <button id="applyFixBtn" class="hidden px-3 py-1 text-xs font-medium bg-emerald-600 hover:bg-emerald-500 text-white rounded transition shadow-sm">
            🪄 Apply 1-Click Fix
          </button>
        </div>
      </div>
    </div>

    <!-- Findings & Results Section -->
    <div class="glass rounded-xl p-6 space-y-4">
      <div class="flex items-center justify-between pb-3 border-b border-gray-800">
        <h2 class="text-sm font-semibold uppercase tracking-wider text-white flex items-center gap-2">
          <span>🚨</span> Detection Findings & Confidence Scores
        </h2>
        <span id="findingsBadge" class="text-xs px-2.5 py-0.5 rounded-full bg-gray-800 text-gray-400">0 Issues</span>
      </div>

      <!-- Results Table -->
      <div class="overflow-x-auto">
        <table class="w-full text-left text-xs">
          <thead class="text-gray-400 border-b border-gray-800">
            <tr>
              <th class="py-2.5 px-3">File:Line</th>
              <th class="py-2.5 px-3">Outdated Sentence</th>
              <th class="py-2.5 px-3">Code Modification</th>
              <th class="py-2.5 px-3">Confidence</th>
              <th class="py-2.5 px-3">Suggested Repair</th>
            </tr>
          </thead>
          <tbody id="findingsBody" class="divide-y divide-gray-800/60 font-mono">
            <tr>
              <td colspan="5" class="py-8 text-center text-gray-500 italic">No analysis run yet. Paste a GitHub URL or click "Run Drift Analysis".</td>
            </tr>
          </tbody>
        </table>
      </div>
    </div>

    <!-- GitHub PR Comment Preview -->
    <div class="glass rounded-xl p-5 space-y-3">
      <div class="flex items-center justify-between">
        <h3 class="text-xs font-semibold uppercase tracking-wider text-gray-400 flex items-center gap-2">
          <span>💬</span> Generated GitHub Pull Request Comment
        </h3>
        <button id="copyCommentBtn" class="text-xs text-sky-400 hover:underline">Copy Markdown</button>
      </div>
      <pre id="commentPreview" class="bg-black/60 p-4 rounded-lg text-xs font-mono text-gray-300 overflow-x-auto whitespace-pre-wrap">Awaiting analysis...</pre>
    </div>

  </div>

  <script>
    let currentAnalysis = null;

    async function loadSamples() {
      const res = await fetch('/api/samples');
      const data = await res.json();
      document.getElementById('diffInput').value = data.diff;
      document.getElementById('docInput').value = data.doc;
      document.getElementById('docNameInput').value = data.doc_name;
    }

    async function runAnalysis() {
      const diff = document.getElementById('diffInput').value;
      const doc = document.getElementById('docInput').value;
      const docName = document.getElementById('docNameInput').value;

      const res = await fetch('/api/analyze', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ diff, doc, doc_name: docName })
      });

      const data = await res.json();
      currentAnalysis = data;
      renderAnalysis(data);
    }

    async function analyzeGithubUrl() {
      const url = document.getElementById('githubUrlInput').value.trim();
      if (!url) {
        alert('Please enter a GitHub repository or pull request URL.');
        return;
      }

      const statusEl = document.getElementById('githubStatus');
      statusEl.classList.remove('hidden');

      try {
        const res = await fetch('/api/analyze-github', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ url })
        });

        const data = await res.json();
        statusEl.classList.add('hidden');

        if (data.error) {
          alert('GitHub Analysis Error: ' + data.error);
          return;
        }

        // Populate fields
        document.getElementById('diffInput').value = data.diff_text || '';
        document.getElementById('docInput').value = data.doc_text || '';
        document.getElementById('docNameInput').value = data.doc_name || 'README.md';

        currentAnalysis = data;
        renderAnalysis(data);
      } catch (err) {
        statusEl.classList.add('hidden');
        alert('Network error connecting to dashboard API: ' + err);
      }
    }

    function renderAnalysis(data) {
      // 1. Render Detected Symbols
      const symbolList = document.getElementById('symbolList');
      if (data.parsed_changes && data.parsed_changes.length > 0) {
        symbolList.innerHTML = data.parsed_changes.map(c => 
          `<div class="text-sky-300">• <span class="text-gray-400">[${c.type}]</span> <span class="text-red-400">${c.old}</span> → <span class="text-emerald-400">${c.new || 'removed'}</span> in <span class="text-gray-400">${c.file}:${c.line}</span></div>`
        ).join('');
      } else {
        symbolList.innerHTML = '<span class="text-gray-500">No symbol changes found.</span>';
      }

      // 2. Render Findings Badge
      const badge = document.getElementById('findingsBadge');
      badge.textContent = `${data.total_issues} Issue(s)`;
      badge.className = data.total_issues > 0 
        ? 'text-xs px-2.5 py-0.5 rounded-full bg-rose-950 text-rose-300 border border-rose-800'
        : 'text-xs px-2.5 py-0.5 rounded-full bg-emerald-950 text-emerald-300 border border-emerald-800';

      // 3. Render Table
      const tbody = document.getElementById('findingsBody');
      if (data.findings && data.findings.length > 0) {
        tbody.innerHTML = data.findings.map(f => {
          const fix = (data.suggested_fixes || []).find(x => x.doc_line === f.doc_line);
          const fixText = fix ? `<span class="text-emerald-400">${fix.suggested_line}</span>` : '<span class="text-gray-500">Manual review</span>';
          const confColor = f.confidence >= 0.9 ? 'text-emerald-400 bg-emerald-950/60 border-emerald-800' : 'text-amber-400 bg-amber-950/60 border-amber-800';
          
          return `
            <tr class="hover:bg-gray-800/30">
              <td class="py-3 px-3 text-sky-400">${f.doc_file}:${f.doc_line}</td>
              <td class="py-3 px-3 text-gray-200 max-w-xs truncate">${f.stale_sentence}</td>
              <td class="py-3 px-3 text-rose-300">${f.changed_symbol} → ${f.new_symbol || 'removed'}</td>
              <td class="py-3 px-3"><span class="px-2 py-0.5 rounded border text-[11px] ${confColor}">${Math.round(f.confidence * 100)}%</span></td>
              <td class="py-3 px-3 max-w-xs truncate">${fixText}</td>
            </tr>
          `;
        }).join('');
      } else {
        tbody.innerHTML = '<tr><td colspan="5" class="py-6 text-center text-emerald-400">✅ All documentation is verified consistent with code changes.</td></tr>';
      }

      // 4. Render Comment Preview
      document.getElementById('commentPreview').textContent = data.markdown_pr_comment || 'Clean.';

      // 5. Show Apply Fix Button if fixes available
      const applyBtn = document.getElementById('applyFixBtn');
      if (data.suggested_fixes && data.suggested_fixes.length > 0) {
        applyBtn.classList.remove('hidden');
      } else {
        applyBtn.classList.add('hidden');
      }
    }

    async function runBenchmark() {
      const banner = document.getElementById('benchmarkBanner');
      const content = document.getElementById('benchmarkContent');
      banner.classList.remove('hidden');
      content.innerHTML = '<span class="text-gray-400">Evaluating against historical test cases (Click, Flask, Requests, Django)...</span>';

      const res = await fetch('/api/benchmark');
      const data = await res.json();

      content.innerHTML = `
        <div class="grid grid-cols-3 gap-3 my-2 text-center">
          <div class="p-2 bg-black/40 rounded border border-gray-800"><div class="text-lg font-bold text-emerald-400">${(data.accuracy * 100).toFixed(0)}%</div><div class="text-[10px] text-gray-400">Accuracy</div></div>
          <div class="p-2 bg-black/40 rounded border border-gray-800"><div class="text-lg font-bold text-sky-400">${(data.precision * 100).toFixed(0)}%</div><div class="text-[10px] text-gray-400">Precision</div></div>
          <div class="p-2 bg-black/40 rounded border border-gray-800"><div class="text-lg font-bold text-indigo-400">${(data.recall * 100).toFixed(0)}%</div><div class="text-[10px] text-gray-400">Recall</div></div>
        </div>
        <div class="space-y-1 mt-2">
          ${data.cases.map(c => `<div class="flex justify-between border-b border-gray-800/40 pb-0.5"><span>${c.repo}: ${c.description}</span><span class="${c.status.includes('PASS') ? 'text-emerald-400' : 'text-rose-400'} font-semibold">${c.status}</span></div>`).join('')}
        </div>
      `;
    }

    function applyFixToDoc() {
      if (!currentAnalysis || !currentAnalysis.suggested_fixes) return;
      let text = document.getElementById('docInput').value;
      const lines = text.split('\\n');
      for (const fix of currentAnalysis.suggested_fixes) {
        const idx = fix.doc_line - 1;
        if (idx >= 0 && idx < lines.length) {
          lines[idx] = fix.suggested_line;
        }
      }
      document.getElementById('docInput').value = lines.join('\\n');
      alert('Applied 1-Click AI Fix! Re-running analysis to verify clean status.');
      runAnalysis();
    }

    document.getElementById('loadSampleBtn').addEventListener('click', loadSamples);
    document.getElementById('analyzeBtn').addEventListener('click', runAnalysis);
    document.getElementById('analyzeGithubBtn').addEventListener('click', analyzeGithubUrl);
    document.getElementById('runBenchmarkBtn').addEventListener('click', runBenchmark);
    document.getElementById('applyFixBtn').addEventListener('click', applyFixToDoc);
    document.getElementById('copyCommentBtn').addEventListener('click', () => {
      navigator.clipboard.writeText(document.getElementById('commentPreview').textContent);
      alert('Markdown comment copied to clipboard!');
    });

    // Auto-load sample on first view
    loadSamples();
  </script>
</body>
</html>
"""

if __name__ == "__main__":
    launch_dashboard()
