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
        findings = find_drift(changes, sentences, enable_llm_check=True)

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
        report["total_sentences"] = len(sentences)
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


HTML_TEMPLATE = r"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Doc Drift — AI Documentation Guardian</title>
  <link rel="preconnect" href="https://fonts.googleapis.com">
  <link href="https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700&family=JetBrains+Mono:wght@400;500&family=Plus+Jakarta+Sans:wght@600;700;800&display=swap" rel="stylesheet">
  <script src="https://cdn.tailwindcss.com"></script>
  <style>
    :root {
      --brand: #38bdf8;
      --glass: rgba(22,27,34,0.80);
      --border: rgba(48,54,61,0.9);
    }
    * { box-sizing: border-box; }
    html { scroll-behavior: smooth; }
    body {
      background: #060b12;
      color: #c9d1d9;
      font-family: 'Inter', system-ui, sans-serif;
      min-height: 100vh;
      overflow-x: hidden;
    }
    .bg-mesh {
      position: fixed; inset: 0; z-index: 0; pointer-events: none;
      background:
        radial-gradient(ellipse 80% 60% at 10% 10%, rgba(56,189,248,0.07) 0%, transparent 60%),
        radial-gradient(ellipse 60% 50% at 90% 80%, rgba(99,102,241,0.06) 0%, transparent 55%),
        linear-gradient(180deg, #060b12 0%, #0d1117 100%);
    }
    .grid-overlay {
      position: fixed; inset: 0; z-index: 0; pointer-events: none;
      background-image:
        linear-gradient(rgba(56,189,248,0.025) 1px, transparent 1px),
        linear-gradient(90deg, rgba(56,189,248,0.025) 1px, transparent 1px);
      background-size: 48px 48px;
    }
    .particle {
      position: fixed; border-radius: 50%; pointer-events: none; z-index: 0;
      animation: float-particle linear infinite;
    }
    @keyframes float-particle {
      0% { transform: translateY(100vh) scale(0); opacity: 0; }
      10% { opacity: 1; }
      90% { opacity: 0.4; }
      100% { transform: translateY(-10vh) scale(1); opacity: 0; }
    }
    .glass {
      background: var(--glass);
      backdrop-filter: blur(20px) saturate(180%);
      -webkit-backdrop-filter: blur(20px) saturate(180%);
      border: 1px solid var(--border);
    }
    .glass-bright {
      background: rgba(30,36,46,0.90);
      backdrop-filter: blur(24px);
      border: 1px solid rgba(56,189,248,0.15);
    }
    .glow-brand { box-shadow: 0 0 0 1px rgba(56,189,248,0.2), 0 8px 32px rgba(56,189,248,0.12); }
    .btn-primary {
      background: linear-gradient(135deg, #0ea5e9 0%, #38bdf8 100%);
      color: #fff; font-weight: 600; letter-spacing: 0.01em;
      box-shadow: 0 4px 20px rgba(56,189,248,0.35);
      transition: all 0.2s; border: none; cursor: pointer;
    }
    .btn-primary:hover { background: linear-gradient(135deg, #38bdf8 0%, #7dd3fc 100%); transform: translateY(-1px); box-shadow: 0 6px 28px rgba(56,189,248,0.5); }
    .btn-secondary {
      background: rgba(30,36,48,0.9); border: 1px solid rgba(56,189,248,0.2);
      color: #94a3b8; font-weight: 500; transition: all 0.2s; cursor: pointer;
    }
    .btn-secondary:hover { border-color: rgba(56,189,248,0.5); color: #e2e8f0; background: rgba(56,189,248,0.06); }
    .btn-ghost {
      background: rgba(30,36,48,0.7); border: 1px solid rgba(99,102,241,0.25);
      color: #818cf8; font-weight: 500; transition: all 0.2s; cursor: pointer;
    }
    .btn-ghost:hover { border-color: rgba(99,102,241,0.5); background: rgba(99,102,241,0.08); color: #a5b4fc; }
    .btn-success {
      background: linear-gradient(135deg, #059669 0%, #10b981 100%);
      color: #fff; font-weight: 600; box-shadow: 0 4px 16px rgba(16,185,129,0.3);
      transition: all 0.2s; border: none; cursor: pointer;
    }
    .btn-success:hover { transform: translateY(-1px); box-shadow: 0 6px 24px rgba(16,185,129,0.45); }
    .input-field {
      background: rgba(6,11,18,0.8); border: 1px solid rgba(48,54,61,0.9); color: #e2e8f0;
      transition: border-color 0.2s, box-shadow 0.2s;
    }
    .input-field:focus { outline: none; border-color: rgba(56,189,248,0.6); box-shadow: 0 0 0 3px rgba(56,189,248,0.08); }
    .code-area {
      background: rgba(4,8,14,0.95); border: 1px solid rgba(48,54,61,0.8); color: #e2e8f0;
      font-family: 'JetBrains Mono', monospace; font-size: 11.5px; line-height: 1.7;
      resize: vertical; transition: border-color 0.2s;
    }
    .code-area:focus { outline: none; border-color: rgba(56,189,248,0.4); }
    .code-area.diff-area { color: #4ade80; }
    .badge { display: inline-flex; align-items: center; gap: 4px; border-radius: 6px; font-size: 10px; font-weight: 700; letter-spacing: 0.08em; text-transform: uppercase; padding: 2px 8px; }
    .badge-behavioral { background: rgba(99,102,241,0.15); color: #a5b4fc; border: 1px solid rgba(99,102,241,0.3); }
    .badge-config     { background: rgba(251,191,36,0.12); color: #fcd34d; border: 1px solid rgba(251,191,36,0.25); }
    .badge-signature  { background: rgba(244,63,94,0.12);  color: #fb7185; border: 1px solid rgba(244,63,94,0.25); }
    .badge-flag       { background: rgba(56,189,248,0.12); color: #7dd3fc; border: 1px solid rgba(56,189,248,0.25); }
    .badge-semantic   { background: rgba(16,185,129,0.12); color: #6ee7b7; border: 1px solid rgba(16,185,129,0.25); }
    .badge-issues     { background: rgba(248,113,113,0.12); color: #fca5a5; border: 1px solid rgba(248,113,113,0.3); }
    .badge-clean      { background: rgba(52,211,153,0.12); color: #6ee7b7; border: 1px solid rgba(52,211,153,0.3); }
    .badge-neutral    { background: rgba(100,116,139,0.15); color: #94a3b8; border: 1px solid rgba(100,116,139,0.3); }
    .conf-bar-wrap { width: 56px; height: 5px; background: rgba(30,36,46,0.9); border-radius: 999px; overflow: hidden; }
    .conf-bar { height: 100%; border-radius: 999px; transition: width 0.6s ease; }
    .findings-table th { font-size: 10px; font-weight: 600; letter-spacing: 0.1em; text-transform: uppercase; color: #64748b; padding: 12px 16px; }
    .findings-table td { padding: 14px 16px; vertical-align: top; }
    .findings-table tbody tr { border-top: 1px solid rgba(30,40,55,0.8); transition: background 0.15s; }
    .findings-table tbody tr:hover { background: rgba(56,189,248,0.03); }
    .reasoning-box { margin-top: 10px; padding: 10px 12px; background: rgba(6,11,18,0.9); border: 1px solid rgba(56,189,248,0.12); border-radius: 8px; font-size: 11px; line-height: 1.6; color: #94a3b8; }
    .pulse-dot { width: 8px; height: 8px; border-radius: 50%; background: #34d399; animation: pulse-glow 2s infinite; }
    @keyframes pulse-glow { 0%, 100% { box-shadow: 0 0 0 0 rgba(52,211,153,0.6); } 50% { box-shadow: 0 0 0 6px rgba(52,211,153,0); } }
    .scan-line { height: 2px; width: 100%; background: linear-gradient(90deg, transparent 0%, #38bdf8 50%, transparent 100%); animation: scan 1.5s linear infinite; }
    @keyframes scan { from { transform: translateX(-100%); } to { transform: translateX(100%); } }
    ::-webkit-scrollbar { width: 6px; height: 6px; }
    ::-webkit-scrollbar-track { background: transparent; }
    ::-webkit-scrollbar-thumb { background: rgba(56,189,248,0.2); border-radius: 999px; }
    .reveal { opacity: 0; transform: translateY(16px); animation: reveal 0.5s ease forwards; }
    @keyframes reveal { to { opacity: 1; transform: translateY(0); } }
    .shimmer { background: linear-gradient(90deg, rgba(30,40,55,0.5) 25%, rgba(56,189,248,0.08) 50%, rgba(30,40,55,0.5) 75%); background-size: 200% 100%; animation: shimmer 1.5s infinite; border-radius: 6px; }
    @keyframes shimmer { to { background-position: -200% 0; } }
    .stat-card { background: rgba(13,17,23,0.95); border: 1px solid rgba(48,54,61,0.8); border-radius: 12px; padding: 16px 20px; text-align: center; min-width: 96px; }
    .spinner { width: 14px; height: 14px; border: 2px solid rgba(56,189,248,0.3); border-top-color: #38bdf8; border-radius: 50%; animation: spin 0.7s linear infinite; display: inline-block; }
    @keyframes spin { to { transform: rotate(360deg); } }
  </style>
</head>
<body>
  <div class="bg-mesh"></div>
  <div class="grid-overlay"></div>
  <div id="particles"></div>

  <div class="relative z-10 min-h-screen">

    <!-- NAVBAR -->
    <nav class="sticky top-0 z-50 glass border-b border-white/5 px-6 py-3.5">
      <div class="max-w-screen-xl mx-auto flex items-center justify-between">
        <div class="flex items-center gap-3">
          <div class="w-8 h-8 rounded-lg bg-gradient-to-br from-sky-500 to-indigo-600 flex items-center justify-center text-base shadow-lg shadow-sky-500/30">🔍</div>
          <div>
            <span class="font-bold text-white text-sm tracking-tight" style="font-family:'Plus Jakarta Sans',sans-serif">Doc Drift</span>
            <span class="ml-2 badge badge-behavioral" style="font-size:9px">AI Guardian</span>
          </div>
        </div>
        <div class="hidden md:flex items-center gap-5">
          <div class="flex items-center gap-2 text-xs text-gray-400">
            <div class="pulse-dot"></div>
            <span id="engineStatus">Engine Live</span>
          </div>
          <div class="h-4 w-px bg-white/10"></div>
          <div class="text-xs text-gray-500">🤖 Local LLM · No Cloud APIs</div>
          <div class="h-4 w-px bg-white/10"></div>
          <div class="text-xs text-gray-500">⚡ AST + Semantic + Behavioral</div>
        </div>
        <div class="flex items-center gap-2">
          <button id="loadSampleBtn" class="btn-secondary text-xs rounded-lg px-3 py-1.5">📂 Sample</button>
          <button id="runBenchmarkBtn" class="btn-ghost text-xs rounded-lg px-3 py-1.5">📊 Benchmark</button>
          <button id="analyzeBtn" class="btn-primary text-xs rounded-xl px-4 py-1.5 flex items-center gap-2">
            <span class="spinner hidden" id="analyzeSpinner"></span>⚡ Analyze
          </button>
        </div>
      </div>
    </nav>

    <!-- MAIN -->
    <div class="max-w-screen-xl mx-auto px-6 py-8 space-y-6">

      <!-- Hero strip -->
      <div class="reveal glass-bright rounded-2xl px-6 py-5 glow-brand">
        <div class="flex flex-col md:flex-row items-start md:items-center justify-between gap-4">
          <div>
            <h1 class="text-xl font-extrabold text-white tracking-tight" style="font-family:'Plus Jakarta Sans',sans-serif">Automated Documentation Drift Detection</h1>
            <p class="text-sm text-gray-400 mt-1 max-w-xl">Catches stale sentences in your docs when code changes. Exact file:line with confidence scores &amp; 1-click AI repairs.</p>
          </div>
          <div class="flex items-center gap-3 flex-shrink-0">
            <div class="stat-card"><div class="text-xl font-bold text-sky-400" id="statIssues">—</div><div class="text-[10px] text-gray-500 mt-0.5 uppercase tracking-wider">Issues</div></div>
            <div class="stat-card"><div class="text-xl font-bold text-indigo-400" id="statSentences">—</div><div class="text-[10px] text-gray-500 mt-0.5 uppercase tracking-wider">Sentences</div></div>
            <div class="stat-card"><div class="text-xl font-bold text-emerald-400" id="statFixes">—</div><div class="text-[10px] text-gray-500 mt-0.5 uppercase tracking-wider">Fixes</div></div>
          </div>
        </div>
      </div>

      <!-- GitHub URL bar -->
      <div class="reveal glass-bright rounded-2xl p-5" style="animation-delay:0.05s">
        <div class="flex items-center gap-2 mb-3">
          <span class="text-sm font-semibold text-white" style="font-family:'Plus Jakarta Sans',sans-serif">🐙 GitHub Direct Analysis</span>
          <span class="badge badge-flag">Live PR Fetch</span>
        </div>
        <div class="flex flex-col md:flex-row gap-3">
          <div class="relative flex-1">
            <div class="absolute left-3 top-1/2 -translate-y-1/2 text-gray-500 text-xs font-mono select-none">https://</div>
            <input id="githubUrlInput" type="text" placeholder="github.com/owner/repo/pull/123  or  owner/repo#123" class="input-field w-full rounded-xl px-4 py-3 pl-16 text-sm font-mono">
          </div>
          <button id="analyzeGithubBtn" class="btn-primary rounded-xl px-5 py-3 text-sm flex items-center justify-center gap-2 whitespace-nowrap">
            <span class="spinner hidden" id="githubSpinner"></span>
            <span id="githubBtnText">🚀 Fetch &amp; Analyze</span>
          </button>
        </div>
        <div id="githubStatus" class="hidden mt-3 overflow-hidden rounded-lg" style="height:3px"><div class="scan-line"></div></div>
        <div id="githubMsg" class="hidden mt-2 text-xs text-sky-400 flex items-center gap-2"><span class="animate-pulse">⏳</span> Fetching PR diff and documentation from GitHub...</div>
      </div>

      <!-- Benchmark Banner -->
      <div id="benchmarkBanner" class="hidden reveal glass rounded-2xl p-5 border border-indigo-500/20" style="animation-delay:0.1s">
        <div class="flex items-center justify-between mb-4">
          <h3 class="text-sm font-semibold text-white flex items-center gap-2" style="font-family:'Plus Jakarta Sans',sans-serif">
            📊 Historical Open-Source Benchmark <span class="badge badge-clean ml-2">100% Acc</span>
          </h3>
          <button onclick="document.getElementById('benchmarkBanner').classList.add('hidden')" class="text-gray-500 hover:text-white text-sm w-7 h-7 flex items-center justify-center hover:bg-white/5 rounded-full">✕</button>
        </div>
        <div id="benchmarkContent"><div class="shimmer h-20 w-full"></div></div>
      </div>

      <!-- Workspace -->
      <div class="grid grid-cols-1 lg:grid-cols-2 gap-5 reveal" style="animation-delay:0.1s">
        <!-- Diff Panel -->
        <div class="glass rounded-2xl overflow-hidden">
          <div class="flex items-center justify-between px-5 py-3.5 border-b border-white/5">
            <div class="flex items-center gap-2"><div class="w-2.5 h-2.5 rounded-full bg-sky-400"></div><span class="text-xs font-semibold uppercase tracking-widest text-gray-400">Pull Request Diff</span></div>
            <span class="badge badge-flag" style="font-size:9px">AST Parser</span>
          </div>
          <div class="p-4 space-y-3">
            <textarea id="diffInput" rows="14" class="code-area diff-area w-full rounded-xl p-4" placeholder="Paste unified git diff or use GitHub URL above..."></textarea>
            <div class="rounded-xl border border-white/5 bg-black/40 p-4">
              <div class="text-[10px] font-bold uppercase tracking-widest text-gray-500 mb-3">Detected Symbol Changes</div>
              <div id="symbolList" class="text-xs text-gray-500 italic space-y-1">Run analysis to detect code symbol changes...</div>
            </div>
          </div>
        </div>
        <!-- Doc Panel -->
        <div class="glass rounded-2xl overflow-hidden">
          <div class="flex items-center justify-between px-5 py-3.5 border-b border-white/5">
            <div class="flex items-center gap-2"><div class="w-2.5 h-2.5 rounded-full bg-indigo-400"></div><span class="text-xs font-semibold uppercase tracking-widest text-gray-400">Documentation</span></div>
            <input id="docNameInput" type="text" value="cli_reference.md" class="input-field text-[11px] font-mono rounded-lg px-2 py-1 w-36 text-gray-300">
          </div>
          <div class="p-4 space-y-3">
            <textarea id="docInput" rows="14" class="code-area w-full rounded-xl p-4" placeholder="Paste Markdown / RST documentation here..."></textarea>
            <div class="flex items-center justify-between">
              <div class="flex items-center gap-2">
                <span class="text-[10px] text-gray-500 uppercase tracking-widest">Indexed:</span>
                <span class="text-xs font-mono text-sky-400" id="docStats">0 sentences</span>
              </div>
              <button id="applyFixBtn" class="hidden btn-success text-xs rounded-lg px-3 py-1.5 flex items-center gap-1.5">🪄 1-Click Fix</button>
            </div>
          </div>
        </div>
      </div>

      <!-- Findings -->
      <div class="glass rounded-2xl overflow-hidden reveal" style="animation-delay:0.15s">
        <div class="flex items-center justify-between px-6 py-4 border-b border-white/5">
          <div class="flex items-center gap-3">
            <div class="w-2.5 h-2.5 rounded-full bg-rose-400"></div>
            <span class="text-xs font-bold text-white uppercase tracking-widest" style="font-family:'Plus Jakarta Sans',sans-serif">Drift Findings</span>
          </div>
          <span id="findingsBadge" class="badge badge-neutral">Awaiting analysis</span>
        </div>
        <div class="overflow-x-auto">
          <table class="w-full findings-table">
            <thead><tr class="border-b border-white/5">
              <th>Location</th><th>Stale Sentence</th><th>Change</th><th>Confidence</th><th>Repair</th>
            </tr></thead>
            <tbody id="findingsBody">
              <tr><td colspan="5" class="py-12 text-center">
                <div class="flex flex-col items-center gap-3 text-gray-600">
                  <div class="text-3xl">🔬</div>
                  <div class="text-sm">Paste a GitHub PR URL or load a sample diff to begin analysis.</div>
                </div>
              </td></tr>
            </tbody>
          </table>
        </div>
      </div>

      <!-- PR Comment -->
      <div class="glass rounded-2xl overflow-hidden reveal" style="animation-delay:0.2s">
        <div class="flex items-center justify-between px-6 py-4 border-b border-white/5">
          <div class="flex items-center gap-2"><span>💬</span><span class="text-xs font-semibold uppercase tracking-widest text-gray-400">Generated PR Comment</span></div>
          <button id="copyCommentBtn" class="btn-secondary text-xs rounded-lg px-3 py-1.5 flex items-center gap-1.5">📋 Copy Markdown</button>
        </div>
        <div class="p-5"><pre id="commentPreview" class="code-area rounded-xl p-5 text-xs text-gray-400 overflow-x-auto whitespace-pre-wrap min-h-16">Awaiting analysis...</pre></div>
      </div>

      <!-- Footer -->
      <footer class="text-center py-6 text-xs text-gray-700 space-y-1">
        <div>Doc Drift Detector · Built for Hacktoberfest 2026 · Hack Day Sonipat</div>
        <div class="flex items-center justify-center gap-4 text-gray-800">
          <span>100% Local Inference</span><span>·</span><span>Zero Cloud APIs</span><span>·</span><span>AST + Semantic + Behavioral</span>
        </div>
      </footer>
    </div>
  </div>

  <script>
    // Particles
    (function() {
      const c = document.getElementById('particles');
      const colors = ['#38bdf8','#818cf8','#34d399','#7dd3fc'];
      for (let i = 0; i < 18; i++) {
        const p = document.createElement('div');
        p.className = 'particle';
        const sz = Math.random() * 3 + 1;
        const dur = Math.random() * 18 + 12;
        const delay = Math.random() * 16;
        p.style.cssText = `width:${sz}px;height:${sz}px;left:${Math.random()*100}vw;bottom:0;background:${colors[Math.floor(Math.random()*colors.length)]};opacity:${Math.random()*0.4+0.1};animation-duration:${dur}s;animation-delay:${delay}s;`;
        c.appendChild(p);
      }
    })();

    let currentAnalysis = null;

    function setLoading(btnId, spinnerId, on) {
      const btn = document.getElementById(btnId);
      const sp = document.getElementById(spinnerId);
      if (!btn || !sp) return;
      sp.classList.toggle('hidden', !on);
      btn.disabled = on;
      btn.style.opacity = on ? '0.75' : '';
    }

    function updateStats(data) {
      document.getElementById('statIssues').textContent = data.total_issues ?? '—';
      document.getElementById('statSentences').textContent = data.total_sentences ?? '—';
      document.getElementById('statFixes').textContent = (data.suggested_fixes || []).length;
      document.getElementById('docStats').textContent = (data.total_sentences || 0) + ' sentences';
    }

    function confColor(v) {
      if (v >= 0.9) return ['#34d399','rgba(52,211,153,0.15)'];
      if (v >= 0.7) return ['#fbbf24','rgba(251,191,36,0.12)'];
      return ['#f87171','rgba(248,113,113,0.12)'];
    }

    function categoryBadge(cat) {
      const cls = {behavioral:'badge-behavioral',config:'badge-config',signature:'badge-signature',flag:'badge-flag',semantic:'badge-semantic'}[cat.toLowerCase()] || 'badge-flag';
      return `<span class="badge ${cls}">${cat.toUpperCase()}</span>`;
    }

    async function loadSamples() {
      const res = await fetch('/api/samples');
      const data = await res.json();
      document.getElementById('diffInput').value = data.diff;
      document.getElementById('docInput').value = data.doc;
      document.getElementById('docNameInput').value = data.doc_name;
    }

    async function runAnalysis() {
      setLoading('analyzeBtn','analyzeSpinner',true);
      document.getElementById('engineStatus').textContent = 'Analyzing...';
      try {
        const res = await fetch('/api/analyze', {
          method: 'POST', headers: {'Content-Type':'application/json'},
          body: JSON.stringify({diff: document.getElementById('diffInput').value, doc: document.getElementById('docInput').value, doc_name: document.getElementById('docNameInput').value})
        });
        const data = await res.json();
        currentAnalysis = data;
        renderAnalysis(data);
      } finally {
        setLoading('analyzeBtn','analyzeSpinner',false);
        document.getElementById('engineStatus').textContent = 'Engine Live';
      }
    }

    async function analyzeGithubUrl() {
      const url = document.getElementById('githubUrlInput').value.trim();
      if (!url) { alert('Enter a GitHub URL first.'); return; }
      setLoading('analyzeGithubBtn','githubSpinner',true);
      document.getElementById('githubBtnText').textContent = 'Fetching…';
      document.getElementById('githubStatus').classList.remove('hidden');
      document.getElementById('githubMsg').classList.remove('hidden');
      try {
        const res = await fetch('/api/analyze-github', {
          method:'POST', headers:{'Content-Type':'application/json'},
          body: JSON.stringify({url})
        });
        const data = await res.json();
        if (data.error) { alert('Error: ' + data.error); return; }
        document.getElementById('diffInput').value = data.diff_text || '';
        document.getElementById('docInput').value = data.doc_text || '';
        document.getElementById('docNameInput').value = data.doc_name || 'README.md';
        currentAnalysis = data;
        renderAnalysis(data);
      } catch(e) {
        alert('Network error: ' + e);
      } finally {
        setLoading('analyzeGithubBtn','githubSpinner',false);
        document.getElementById('githubBtnText').textContent = '🚀 Fetch & Analyze';
        document.getElementById('githubStatus').classList.add('hidden');
        document.getElementById('githubMsg').classList.add('hidden');
      }
    }

    function renderAnalysis(data) {
      updateStats(data);

      const sl = document.getElementById('symbolList');
      if (data.parsed_changes && data.parsed_changes.length > 0) {
        sl.innerHTML = data.parsed_changes.map(c => `
          <div class="flex items-baseline gap-2 py-0.5">
            <span class="badge badge-flag" style="font-size:9px">${c.type}</span>
            <span class="font-mono text-rose-400 line-through opacity-70">${c.old}</span>
            <span class="text-gray-600">→</span>
            <span class="font-mono text-emerald-400">${c.new || 'removed'}</span>
            <span class="text-gray-600 text-[10px]">${c.file}:${c.line}</span>
          </div>`).join('');
      } else {
        sl.innerHTML = '<span class="text-gray-600">No symbol changes detected.</span>';
      }

      const badge = document.getElementById('findingsBadge');
      const issues = data.total_issues || 0;
      if (issues > 0) { badge.className = 'badge badge-issues'; badge.textContent = issues + ' Issue' + (issues !== 1 ? 's' : ''); }
      else { badge.className = 'badge badge-clean'; badge.textContent = 'All Clean ✓'; }

      const tbody = document.getElementById('findingsBody');
      if (data.findings && data.findings.length > 0) {
        tbody.innerHTML = data.findings.map(f => {
          const fix = (data.suggested_fixes || []).find(x => x.doc_line === f.doc_line);
          const fixHtml = fix ? `<span class="font-mono text-emerald-400 text-[11px]">${fix.suggested_line}</span>` : '<span class="text-gray-600 text-[11px]">Manual review</span>';
          const [clr] = confColor(f.confidence);
          const pct = Math.round(f.confidence * 100);
          const cat = (f.drift_category || 'flag').toLowerCase();
          const reasoning = f.reasoning ? `<div class="reasoning-box"><span style="color:#38bdf8;font-weight:600">🧠 ${f.verified_by_model || 'Local LLM'}${f.latency_ms ? ' · ' + f.latency_ms + 'ms' : ''}</span><br>${f.reasoning}</div>` : '';
          return `<tr>
            <td><div class="font-mono text-sky-400 text-[11px]">${f.doc_file}:${f.doc_line}</div><div class="mt-1.5">${categoryBadge(cat)}</div></td>
            <td><div class="font-mono text-gray-200 text-[11px] leading-relaxed">${f.stale_sentence}</div>${reasoning}</td>
            <td><div class="font-mono text-[11px]"><span class="text-rose-400">${f.changed_symbol}</span> <span class="text-gray-600">→</span> <span class="text-emerald-400">${f.new_symbol || 'removed'}</span></div></td>
            <td><div class="flex flex-col gap-1.5"><span class="font-mono text-xs font-bold" style="color:${clr}">${pct}%</span><div class="conf-bar-wrap"><div class="conf-bar" style="width:${pct}%;background:${clr}"></div></div></div></td>
            <td>${fixHtml}</td>
          </tr>`;
        }).join('');
      } else {
        tbody.innerHTML = `<tr><td colspan="5" class="py-12 text-center"><div class="flex flex-col items-center gap-2 text-emerald-500"><div class="text-3xl">✅</div><div class="text-sm font-semibold">All documentation is consistent with code changes.</div></div></td></tr>`;
      }

      document.getElementById('commentPreview').textContent = data.markdown_pr_comment || '✅ No drift detected.';
      const fb = document.getElementById('applyFixBtn');
      (data.suggested_fixes && data.suggested_fixes.length > 0) ? fb.classList.remove('hidden') : fb.classList.add('hidden');
    }

    async function runBenchmark() {
      const banner = document.getElementById('benchmarkBanner');
      const content = document.getElementById('benchmarkContent');
      banner.classList.remove('hidden');
      content.innerHTML = '<div class="shimmer h-24 w-full rounded-xl"></div>';
      const res = await fetch('/api/benchmark');
      const data = await res.json();
      content.innerHTML = `
        <div class="grid grid-cols-3 gap-3 mb-4">
          <div class="stat-card"><div class="text-lg font-bold text-emerald-400">${(data.accuracy*100).toFixed(0)}%</div><div class="text-[10px] text-gray-500 mt-0.5 uppercase tracking-wider">Accuracy</div></div>
          <div class="stat-card"><div class="text-lg font-bold text-sky-400">${(data.precision*100).toFixed(0)}%</div><div class="text-[10px] text-gray-500 mt-0.5 uppercase tracking-wider">Precision</div></div>
          <div class="stat-card"><div class="text-lg font-bold text-indigo-400">${(data.recall*100).toFixed(0)}%</div><div class="text-[10px] text-gray-500 mt-0.5 uppercase tracking-wider">Recall</div></div>
        </div>
        <div class="space-y-1">${data.cases.map(c=>`<div class="flex items-center justify-between py-1.5 border-b border-white/5 text-xs"><span class="text-gray-400">${c.repo}: <span class="text-gray-300">${c.description}</span></span><span class="${c.status.includes('PASS')?'badge badge-clean':'badge badge-issues'}">${c.status}</span></div>`).join('')}</div>`;
    }

    function applyFixToDoc() {
      if (!currentAnalysis || !currentAnalysis.suggested_fixes) return;
      const lines = document.getElementById('docInput').value.split('\n');
      for (const fix of currentAnalysis.suggested_fixes) {
        const idx = fix.doc_line - 1;
        if (idx >= 0 && idx < lines.length) lines[idx] = fix.suggested_line;
      }
      document.getElementById('docInput').value = lines.join('\n');
      runAnalysis();
    }

    document.getElementById('loadSampleBtn').addEventListener('click', loadSamples);
    document.getElementById('analyzeBtn').addEventListener('click', runAnalysis);
    document.getElementById('analyzeGithubBtn').addEventListener('click', analyzeGithubUrl);
    document.getElementById('runBenchmarkBtn').addEventListener('click', runBenchmark);
    document.getElementById('applyFixBtn').addEventListener('click', applyFixToDoc);
    document.getElementById('copyCommentBtn').addEventListener('click', () => {
      navigator.clipboard.writeText(document.getElementById('commentPreview').textContent);
      const btn = document.getElementById('copyCommentBtn');
      const orig = btn.textContent;
      btn.textContent = '✓ Copied!';
      setTimeout(() => btn.textContent = orig, 2000);
    });

    loadSamples();
  </script>
</body>
</html>"""

if __name__ == "__main__":
    launch_dashboard()
