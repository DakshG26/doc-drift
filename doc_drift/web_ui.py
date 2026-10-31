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
        elif path == "/api/ollama-models":
            self._serve_ollama_models()
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

    def _serve_ollama_models(self) -> None:
        """Fetch running Ollama models from local API."""
        import urllib.request
        try:
            with urllib.request.urlopen("http://localhost:11434/api/tags", timeout=3) as r:
                data = json.loads(r.read())
            models = [
                {
                    "name": m["name"],
                    "size_gb": round(m["size"] / 1e9, 1),
                    "params": m["details"].get("parameter_size", "?"),
                    "quant": m["details"].get("quantization_level", "?"),
                    "family": m["details"].get("family", "?"),
                    "capabilities": m.get("capabilities", []),
                }
                for m in data.get("models", [])
            ]
            self._serve_json({"status": "online", "models": models, "count": len(models)})
        except Exception as e:
            self._serve_json({"status": "offline", "models": [], "error": str(e)})

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
  <link href="https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700&family=JetBrains+Mono:wght@400;500&family=Plus+Jakarta+Sans:wght@700;800&display=swap" rel="stylesheet">
  <script src="https://cdn.tailwindcss.com"></script>
  <style>
    body { background:#f5f7fa; color:#1e293b; font-family:'Inter',sans-serif; min-height:100vh; }
    .topbar { background:linear-gradient(135deg,#0f172a 0%,#1e3a5f 60%,#0369a1 100%); }
    .card { background:#fff; border:1px solid #e2e8f0; border-radius:16px; box-shadow:0 1px 4px rgba(0,0,0,.06),0 4px 16px rgba(0,0,0,.04); }
    .card-inset { background:#f8fafc; border:1px solid #e2e8f0; border-radius:12px; }
    .field { background:#fff; border:1.5px solid #cbd5e1; border-radius:10px; color:#1e293b; font-size:13px; transition:border-color .2s,box-shadow .2s; outline:none; padding:10px 14px; width:100%; }
    .field:focus { border-color:#0ea5e9; box-shadow:0 0 0 3px rgba(14,165,233,.12); }
    .field-mono { font-family:'JetBrains Mono',monospace; font-size:12px; }
    .field-code { background:#f8fafc; font-family:'JetBrains Mono',monospace; font-size:11.5px; line-height:1.75; resize:vertical; }
    .field-diff { color:#16a34a; }
    .btn { border-radius:10px; font-weight:600; font-size:13px; padding:9px 18px; cursor:pointer; transition:all .18s; display:inline-flex; align-items:center; gap:7px; border:none; }
    .btn-blue { background:linear-gradient(135deg,#0284c7,#38bdf8); color:#fff; box-shadow:0 2px 12px rgba(14,165,233,.35); }
    .btn-blue:hover { background:linear-gradient(135deg,#0369a1,#0ea5e9); transform:translateY(-1px); box-shadow:0 4px 20px rgba(14,165,233,.4); }
    .btn-blue:disabled { opacity:.6; cursor:not-allowed; transform:none; }
    .btn-outline { background:#fff; border:1.5px solid #cbd5e1; color:#475569; }
    .btn-outline:hover { border-color:#94a3b8; color:#1e293b; background:#f8fafc; }
    .btn-indigo { background:#eef2ff; border:1.5px solid #c7d2fe; color:#4f46e5; }
    .btn-indigo:hover { background:#e0e7ff; border-color:#a5b4fc; }
    .btn-green { background:linear-gradient(135deg,#059669,#10b981); color:#fff; box-shadow:0 2px 10px rgba(16,185,129,.3); }
    .btn-green:hover { transform:translateY(-1px); box-shadow:0 4px 16px rgba(16,185,129,.4); }
    .badge { display:inline-flex; align-items:center; border-radius:6px; font-size:10px; font-weight:700; letter-spacing:.07em; text-transform:uppercase; padding:2px 8px; }
    .b-blue   { background:#e0f2fe; color:#0369a1; border:1px solid #bae6fd; }
    .b-green  { background:#dcfce7; color:#15803d; border:1px solid #bbf7d0; }
    .b-red    { background:#fee2e2; color:#dc2626; border:1px solid #fecaca; }
    .b-amber  { background:#fef9c3; color:#b45309; border:1px solid #fde68a; }
    .b-indigo { background:#eef2ff; color:#4338ca; border:1px solid #c7d2fe; }
    .b-purple { background:#faf5ff; color:#7e22ce; border:1px solid #e9d5ff; }
    .b-gray   { background:#f1f5f9; color:#64748b; border:1px solid #e2e8f0; }
    .b-teal   { background:#f0fdfa; color:#0f766e; border:1px solid #99f6e4; }
    .tbl th { font-size:11px; font-weight:600; letter-spacing:.08em; text-transform:uppercase; color:#94a3b8; padding:11px 16px; background:#f8fafc; }
    .tbl td { padding:13px 16px; vertical-align:top; font-size:12.5px; border-top:1px solid #f1f5f9; }
    .tbl tr:hover td { background:#f8fafc; }
    .cbar-wrap { width:60px; height:5px; background:#e2e8f0; border-radius:999px; overflow:hidden; margin-top:4px; }
    .cbar { height:100%; border-radius:999px; transition:width .5s ease; }
    .model-chip { display:flex; align-items:flex-start; gap:10px; padding:10px 14px; border-radius:10px; background:#f8fafc; border:1px solid #e2e8f0; transition:border-color .15s; }
    .model-chip:hover { border-color:#94a3b8; }
    .model-dot { width:8px; height:8px; border-radius:50%; background:#10b981; flex-shrink:0; margin-top:4px; box-shadow:0 0 0 3px rgba(16,185,129,.2); }
    .stat-pill { background:rgba(255,255,255,.15); border-radius:12px; padding:12px 20px; text-align:center; min-width:88px; }
    .spin { width:14px; height:14px; border:2px solid rgba(255,255,255,.4); border-top-color:#fff; border-radius:50%; animation:sp .6s linear infinite; display:inline-block; }
    .spin-blue { border:2px solid #bae6fd; border-top-color:#0284c7; border-radius:50%; animation:sp .6s linear infinite; width:14px; height:14px; display:inline-block; }
    @keyframes sp { to { transform:rotate(360deg); } }
    .scan-wrap { height:3px; overflow:hidden; border-radius:999px; background:#e2e8f0; }
    .scan-bar { height:100%; width:40%; background:linear-gradient(90deg,transparent,#0ea5e9,transparent); animation:scan 1.2s ease-in-out infinite; }
    @keyframes scan { 0%{transform:translateX(-100%)} 100%{transform:translateX(350%)} }
    .sec-label { font-size:11px; font-weight:700; letter-spacing:.1em; text-transform:uppercase; color:#94a3b8; }
    .reasoning { margin-top:8px; padding:9px 12px; background:#f0f9ff; border:1px solid #bae6fd; border-radius:8px; font-size:11px; line-height:1.6; color:#334155; }
    .pulse { width:8px; height:8px; border-radius:50%; background:#10b981; animation:pg 2s infinite; }
    @keyframes pg { 0%,100%{box-shadow:0 0 0 0 rgba(16,185,129,.5)} 50%{box-shadow:0 0 0 6px rgba(16,185,129,0)} }
    ::-webkit-scrollbar { width:5px; height:5px; }
    ::-webkit-scrollbar-track { background:#f1f5f9; }
    ::-webkit-scrollbar-thumb { background:#cbd5e1; border-radius:999px; }
  </style>
</head>
<body>

  <!-- TOP NAVBAR -->
  <div class="topbar px-6 py-4">
    <div class="max-w-screen-xl mx-auto flex items-center justify-between gap-4">
      <div class="flex items-center gap-3">
        <div class="w-9 h-9 rounded-xl bg-white/10 border border-white/20 flex items-center justify-center text-lg">&#128269;</div>
        <div>
          <div class="text-white font-bold text-base leading-tight" style="font-family:'Plus Jakarta Sans',sans-serif">Doc Drift</div>
          <div class="text-sky-300 text-xs">AI Documentation Guardian</div>
        </div>
      </div>
      <div class="hidden md:flex items-center gap-3">
        <div class="stat-pill"><div class="text-white font-bold text-lg leading-none" id="statIssues">&#8212;</div><div class="text-sky-200 text-[10px] mt-1 uppercase tracking-wider">Issues</div></div>
        <div class="stat-pill"><div class="text-white font-bold text-lg leading-none" id="statSentences">&#8212;</div><div class="text-sky-200 text-[10px] mt-1 uppercase tracking-wider">Sentences</div></div>
        <div class="stat-pill"><div class="text-white font-bold text-lg leading-none" id="statFixes">&#8212;</div><div class="text-sky-200 text-[10px] mt-1 uppercase tracking-wider">Fixes</div></div>
      </div>
      <div class="flex items-center gap-2">
        <div class="flex items-center gap-1.5 text-sky-300 text-xs mr-2"><div class="pulse"></div><span id="engineStatus">Engine Live</span></div>
        <button id="loadSampleBtn" class="btn btn-outline text-xs !px-3 !py-2">&#128194; Sample</button>
        <button id="runBenchmarkBtn" class="btn btn-indigo text-xs !px-3 !py-2">&#128202; Benchmark</button>
        <button id="analyzeBtn" class="btn btn-blue text-xs !px-4 !py-2">
          <span class="spin hidden" id="analyzeSpinner"></span>&#9889; Analyze
        </button>
      </div>
    </div>
  </div>

  <!-- PAGE -->
  <div class="max-w-screen-xl mx-auto px-6 py-6 space-y-5">

    <!-- Hero -->
    <div class="card px-6 py-4 flex flex-col md:flex-row items-start md:items-center justify-between gap-3">
      <div>
        <h1 class="text-lg font-bold text-slate-800" style="font-family:'Plus Jakarta Sans',sans-serif">Automated Documentation Drift Detection</h1>
        <p class="text-slate-500 text-sm mt-0.5">Detects stale sentences when code changes — exact file:line, confidence scores &amp; 1-click AI repair.</p>
      </div>
      <div class="flex flex-wrap gap-2">
        <span class="badge b-blue">AST Parser</span>
        <span class="badge b-teal">Semantic Embeddings</span>
        <span class="badge b-indigo">Behavioral AI</span>
        <span class="badge b-green">1-Click Fix</span>
      </div>
    </div>

    <!-- GitHub URL -->
    <div class="card p-5">
      <div class="flex items-center gap-2 mb-3">
        <span class="text-base">&#128025;</span>
        <span class="font-semibold text-slate-700 text-sm">GitHub Direct Analysis</span>
        <span class="badge b-blue ml-1">Live PR Fetch</span>
      </div>
      <div class="flex flex-col md:flex-row gap-3">
        <div class="relative flex-1">
          <span class="absolute left-3 top-1/2 -translate-y-1/2 text-slate-400 text-xs font-mono pointer-events-none">https://</span>
          <input id="githubUrlInput" type="text" placeholder="github.com/owner/repo/pull/123  or  owner/repo#123" class="field field-mono pl-16">
        </div>
        <button id="analyzeGithubBtn" class="btn btn-blue whitespace-nowrap">
          <span class="spin hidden" id="githubSpinner"></span>
          <span id="githubBtnText">&#128640; Fetch &amp; Analyze</span>
        </button>
      </div>
      <div id="githubStatus" class="hidden mt-3 scan-wrap"><div class="scan-bar"></div></div>
      <div id="githubMsg" class="hidden mt-2 text-xs text-sky-600 flex items-center gap-2">
        <span class="spin-blue"></span> Fetching PR diff and documentation from GitHub...
      </div>
    </div>

    <!-- Benchmark -->
    <div id="benchmarkBanner" class="hidden card p-5">
      <div class="flex items-center justify-between mb-4">
        <div class="flex items-center gap-2">
          <span class="text-base">&#128202;</span>
          <span class="font-semibold text-slate-700 text-sm">Historical Open-Source Benchmark</span>
          <span class="badge b-green">100% Accuracy</span>
        </div>
        <button onclick="document.getElementById('benchmarkBanner').classList.add('hidden')" class="text-slate-400 hover:text-slate-600 w-7 h-7 rounded-lg hover:bg-slate-100 flex items-center justify-center">&#x2715;</button>
      </div>
      <div id="benchmarkContent" class="text-sm text-slate-500">Loading...</div>
    </div>

    <!-- 3-col workspace -->
    <div class="grid grid-cols-1 lg:grid-cols-3 gap-5">

      <!-- Diff -->
      <div class="card overflow-hidden">
        <div class="flex items-center justify-between px-5 py-3 border-b border-slate-100">
          <div class="flex items-center gap-2"><div class="w-2.5 h-2.5 rounded-full bg-sky-500"></div><span class="sec-label">PR Diff</span></div>
          <span class="badge b-blue" style="font-size:9px">AST Parser</span>
        </div>
        <div class="p-4 space-y-3">
          <textarea id="diffInput" rows="12" class="field field-code field-diff" placeholder="Paste unified git diff..."></textarea>
          <div class="card-inset p-3">
            <div class="sec-label mb-2">Detected Changes</div>
            <div id="symbolList" class="text-xs text-slate-400 italic space-y-1">Run analysis to see symbol changes...</div>
          </div>
        </div>
      </div>

      <!-- Doc -->
      <div class="card overflow-hidden">
        <div class="flex items-center justify-between px-5 py-3 border-b border-slate-100">
          <div class="flex items-center gap-2"><div class="w-2.5 h-2.5 rounded-full bg-indigo-500"></div><span class="sec-label">Documentation</span></div>
          <input id="docNameInput" type="text" value="cli_reference.md" class="field field-mono !w-36 !px-2 !py-1 !rounded-lg" style="font-size:11px">
        </div>
        <div class="p-4 space-y-3">
          <textarea id="docInput" rows="12" class="field field-code" placeholder="Paste Markdown / RST doc..."></textarea>
          <div class="flex items-center justify-between">
            <span class="text-xs text-slate-400"><span id="docStats">0</span> sentences indexed</span>
            <button id="applyFixBtn" class="hidden btn btn-green text-xs" style="padding:6px 14px;font-size:12px">&#x1FA84; 1-Click Fix</button>
          </div>
        </div>
      </div>

      <!-- Ollama -->
      <div class="card overflow-hidden">
        <div class="flex items-center justify-between px-5 py-3 border-b border-slate-100">
          <div class="flex items-center gap-2"><div class="w-2.5 h-2.5 rounded-full bg-emerald-500"></div><span class="sec-label">Local Ollama Models</span></div>
          <span id="ollamaStatus" class="badge b-gray">Checking...</span>
        </div>
        <div class="p-4 space-y-3 overflow-y-auto" style="max-height:420px">
          <div id="ollamaList" class="space-y-2"><div class="text-xs text-slate-400 italic">Loading...</div></div>
          <div class="card-inset p-3 mt-3">
            <div class="sec-label mb-2">Inference Stack</div>
            <div class="space-y-1 text-xs text-slate-500">
              <div>&#128279; <span class="font-mono text-slate-700">localhost:11434</span></div>
              <div>&#129504; sentence-transformers/MiniLM-L6</div>
              <div>&#128683; <span class="text-red-500 font-medium">No Cloud APIs</span></div>
            </div>
          </div>
        </div>
      </div>
    </div>

    <!-- Findings -->
    <div class="card overflow-hidden">
      <div class="flex items-center justify-between px-6 py-4 border-b border-slate-100">
        <div class="flex items-center gap-2">
          <div class="w-2.5 h-2.5 rounded-full bg-rose-500"></div>
          <span class="font-bold text-slate-700 text-sm" style="font-family:'Plus Jakarta Sans',sans-serif">Detection Findings</span>
        </div>
        <span id="findingsBadge" class="badge b-gray">Awaiting analysis</span>
      </div>
      <div class="overflow-x-auto">
        <table class="w-full tbl">
          <thead><tr>
            <th class="text-left">Location</th>
            <th class="text-left">Stale Sentence</th>
            <th class="text-left">Change</th>
            <th class="text-left">Confidence</th>
            <th class="text-left">Repair</th>
          </tr></thead>
          <tbody id="findingsBody">
            <tr><td colspan="5" class="py-12 text-center text-slate-400">
              <div class="flex flex-col items-center gap-2">
                <div class="text-3xl">&#128300;</div>
                <div>Paste a GitHub PR URL or load the sample diff to begin.</div>
              </div>
            </td></tr>
          </tbody>
        </table>
      </div>
    </div>

    <!-- PR Comment -->
    <div class="card overflow-hidden">
      <div class="flex items-center justify-between px-6 py-4 border-b border-slate-100">
        <div class="flex items-center gap-2">
          <span class="text-base">&#128172;</span>
          <span class="sec-label">Generated GitHub PR Comment</span>
        </div>
        <button id="copyCommentBtn" class="btn btn-outline" style="font-size:12px;padding:6px 14px">&#128203; Copy Markdown</button>
      </div>
      <div class="p-5">
        <pre id="commentPreview" class="field field-code p-4 text-slate-600 overflow-x-auto whitespace-pre-wrap" style="min-height:48px">Awaiting analysis...</pre>
      </div>
    </div>

    <!-- Footer -->
    <div class="text-center text-xs text-slate-400 py-4">
      Doc Drift Detector &nbsp;&#183;&nbsp; Hacktoberfest 2026 &nbsp;&#183;&nbsp; Hack Day Sonipat &nbsp;&#183;&nbsp; 100% Local &nbsp;&#183;&nbsp; Zero Cloud APIs
    </div>
  </div>

  <script>
    let currentAnalysis = null;

    async function loadOllamaModels() {
      try {
        const res = await fetch('/api/ollama-models');
        const data = await res.json();
        const st = document.getElementById('ollamaStatus');
        const lst = document.getElementById('ollamaList');
        if (data.status === 'offline') {
          st.className = 'badge b-red'; st.textContent = 'Offline';
          lst.innerHTML = '<div class="text-xs text-red-500">Ollama not reachable at localhost:11434</div>';
          return;
        }
        st.className = 'badge b-green'; st.textContent = data.count + ' Models';
        lst.innerHTML = data.models.map(m => {
          const caps = [];
          if (m.capabilities.includes('thinking')) caps.push('<span class="badge b-purple" style="font-size:9px">&#129504; Think</span>');
          if (m.capabilities.includes('vision'))   caps.push('<span class="badge b-amber" style="font-size:9px">&#128065; Vision</span>');
          if (m.capabilities.includes('tools'))    caps.push('<span class="badge b-teal" style="font-size:9px">&#128295; Tools</span>');
          const [name, tag] = m.name.split(':');
          return `<div class="model-chip">
            <div class="model-dot"></div>
            <div class="flex-1 min-w-0">
              <div class="font-mono text-xs font-semibold text-slate-700 truncate">${name}<span class="text-slate-400 font-normal">:${tag||'latest'}</span></div>
              <div class="text-[10px] text-slate-400 mt-0.5">${m.params} &middot; ${m.quant} &middot; ${m.size_gb}GB</div>
              <div class="mt-1 flex flex-wrap gap-1">${caps.join('')}</div>
            </div>
          </div>`;
        }).join('');
      } catch(e) {
        document.getElementById('ollamaStatus').className = 'badge b-red';
        document.getElementById('ollamaStatus').textContent = 'Error';
      }
    }

    function setLoading(btnId, spinnerId, on) {
      const btn = document.getElementById(btnId);
      const sp = document.getElementById(spinnerId);
      if (!btn || !sp) return;
      sp.classList.toggle('hidden', !on);
      btn.disabled = on; btn.style.opacity = on ? '0.7' : '';
    }

    function confStyle(v) {
      if (v >= 0.9) return '#16a34a';
      if (v >= 0.7) return '#d97706';
      return '#ef4444';
    }

    function catBadge(cat) {
      const m = {behavioral:'b-indigo',config:'b-amber',signature:'b-red',flag:'b-blue',semantic:'b-teal'};
      return `<span class="badge ${m[cat]||'b-gray'}">${cat.toUpperCase()}</span>`;
    }

    function updateStats(data) {
      document.getElementById('statIssues').textContent    = data.total_issues ?? '&#8212;';
      document.getElementById('statSentences').textContent = data.total_sentences ?? '&#8212;';
      document.getElementById('statFixes').textContent     = (data.suggested_fixes||[]).length;
      document.getElementById('docStats').textContent      = data.total_sentences ?? 0;
    }

    async function loadSamples() {
      const res = await fetch('/api/samples');
      const d = await res.json();
      document.getElementById('diffInput').value    = d.diff;
      document.getElementById('docInput').value     = d.doc;
      document.getElementById('docNameInput').value = d.doc_name;
    }

    async function runAnalysis() {
      setLoading('analyzeBtn','analyzeSpinner',true);
      document.getElementById('engineStatus').textContent = 'Analyzing...';
      try {
        const res = await fetch('/api/analyze', {
          method:'POST', headers:{'Content-Type':'application/json'},
          body: JSON.stringify({diff:document.getElementById('diffInput').value, doc:document.getElementById('docInput').value, doc_name:document.getElementById('docNameInput').value})
        });
        const data = await res.json();
        currentAnalysis = data; renderAnalysis(data);
      } finally {
        setLoading('analyzeBtn','analyzeSpinner',false);
        document.getElementById('engineStatus').textContent = 'Engine Live';
      }
    }

    async function analyzeGithubUrl() {
      const url = document.getElementById('githubUrlInput').value.trim();
      if (!url) { alert('Enter a GitHub URL first.'); return; }
      setLoading('analyzeGithubBtn','githubSpinner',true);
      document.getElementById('githubBtnText').textContent = 'Fetching...';
      document.getElementById('githubStatus').classList.remove('hidden');
      document.getElementById('githubMsg').classList.remove('hidden');
      try {
        const res = await fetch('/api/analyze-github', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({url})});
        const data = await res.json();
        if (data.error) { alert('Error: '+data.error); return; }
        document.getElementById('diffInput').value    = data.diff_text||'';
        document.getElementById('docInput').value     = data.doc_text||'';
        document.getElementById('docNameInput').value = data.doc_name||'README.md';
        currentAnalysis = data; renderAnalysis(data);
      } catch(e) { alert('Network error: '+e); }
      finally {
        setLoading('analyzeGithubBtn','githubSpinner',false);
        document.getElementById('githubBtnText').textContent = 'Fetch & Analyze';
        document.getElementById('githubStatus').classList.add('hidden');
        document.getElementById('githubMsg').classList.add('hidden');
      }
    }

    function renderAnalysis(data) {
      updateStats(data);
      const sl = document.getElementById('symbolList');
      if (data.parsed_changes && data.parsed_changes.length > 0) {
        sl.innerHTML = data.parsed_changes.map(c=>{
          let symbolHtml = '';
          if (c.type === 'added') {
            symbolHtml = `<span class="badge b-green" style="font-size:9px">ADDED</span> <span class="font-mono text-green-600 font-semibold text-[11px]">+ ${c.new || c.old}</span>`;
          } else if (c.type === 'removed') {
            symbolHtml = `<span class="badge b-red" style="font-size:9px">REMOVED</span> <span class="font-mono text-red-500 line-through text-[11px]">- ${c.old}</span>`;
          } else {
            symbolHtml = `<span class="badge b-blue" style="font-size:9px">${c.type}</span> <span class="font-mono text-red-500 line-through text-[11px]">${c.old}</span> <span class="text-slate-400">&rarr;</span> <span class="font-mono text-green-600 text-[11px]">${c.new || 'removed'}</span>`;
          }
          return `<div class="flex flex-wrap items-baseline gap-1.5 py-0.5">
            ${symbolHtml}
            <span class="text-slate-400 text-[10px] ml-1">${c.file}:${c.line}</span>
          </div>`;
        }).join('');
      } else {
        sl.innerHTML = '<span class="text-slate-400">No symbol changes detected.</span>';
      }
      const badge = document.getElementById('findingsBadge');
      const n = data.total_issues||0;
      badge.className = n>0?'badge b-red':'badge b-green';
      badge.textContent = n>0 ? `${n} Issue${n!==1?'s':''}` : 'All Clean \u2713';

      const tbody = document.getElementById('findingsBody');
      if (data.findings && data.findings.length>0) {
        tbody.innerHTML = data.findings.map(f=>{
          const fix = (data.suggested_fixes||[]).find(x=>x.doc_line===f.doc_line);
          const fixHtml = fix ? `<span class="font-mono text-green-700 text-xs">${fix.suggested_line}</span>` : '<span class="text-slate-400 text-xs">Manual review</span>';
          const pct = Math.round(f.confidence*100);
          const clr = confStyle(f.confidence);
          const cat = (f.drift_category||'flag').toLowerCase();
          const reasoning = f.reasoning ? `<div class="reasoning"><span style="color:#0369a1;font-weight:600">&#129504; ${f.verified_by_model||'Local LLM'}${f.latency_ms?' &middot; '+f.latency_ms+'ms':''}</span><br>${f.reasoning}</div>` : '';
          return `<tr>
            <td><span class="font-mono text-sky-600 text-xs">${f.doc_file}:${f.doc_line}</span><div class="mt-1.5">${catBadge(cat)}</div></td>
            <td class="max-w-sm"><span class="font-mono text-slate-700 text-xs">${f.stale_sentence}</span>${reasoning}</td>
            <td>${
              (!f.new_symbol || f.changed_symbol === f.new_symbol)
                ? `<span class="font-mono text-amber-600 text-xs font-semibold">${f.changed_symbol}</span>`
                : `<span class="font-mono text-red-500 text-xs">${f.changed_symbol}</span> <span class="text-slate-400">&rarr;</span> <span class="font-mono text-green-600 text-xs">${f.new_symbol}</span>`
            }</td>
            <td><span class="font-mono font-bold text-xs" style="color:${clr}">${pct}%</span><div class="cbar-wrap"><div class="cbar" style="width:${pct}%;background:${clr}"></div></div></td>
            <td class="max-w-xs">${fixHtml}</td>
          </tr>`;
        }).join('');
      } else {
        tbody.innerHTML = `<tr><td colspan="5" class="py-12 text-center"><div class="flex flex-col items-center gap-2 text-emerald-600"><div class="text-3xl">&#9989;</div><div class="font-semibold">All documentation is consistent with code changes.</div></div></td></tr>`;
      }
      document.getElementById('commentPreview').textContent = data.markdown_pr_comment||'No drift.';
      const fb = document.getElementById('applyFixBtn');
      (data.suggested_fixes&&data.suggested_fixes.length>0) ? fb.classList.remove('hidden') : fb.classList.add('hidden');
    }

    async function runBenchmark() {
      const banner = document.getElementById('benchmarkBanner');
      const content = document.getElementById('benchmarkContent');
      banner.classList.remove('hidden');
      content.innerHTML = '<div class="text-slate-400">Evaluating against Click, Flask, Requests, Django...</div>';
      const res = await fetch('/api/benchmark');
      const data = await res.json();
      content.innerHTML = `
        <div class="grid grid-cols-3 gap-3 mb-4">
          <div class="card-inset p-3 text-center"><div class="text-xl font-bold text-emerald-600">${(data.accuracy*100).toFixed(0)}%</div><div class="sec-label mt-1">Accuracy</div></div>
          <div class="card-inset p-3 text-center"><div class="text-xl font-bold text-sky-600">${(data.precision*100).toFixed(0)}%</div><div class="sec-label mt-1">Precision</div></div>
          <div class="card-inset p-3 text-center"><div class="text-xl font-bold text-indigo-600">${(data.recall*100).toFixed(0)}%</div><div class="sec-label mt-1">Recall</div></div>
        </div>
        <div class="space-y-1">${data.cases.map(c=>`<div class="flex items-center justify-between py-1.5 border-b border-slate-100 text-sm"><span class="text-slate-500">${c.repo}: <span class="text-slate-700">${c.description}</span></span><span class="${c.status.includes('PASS')?'badge b-green':'badge b-red'}">${c.status}</span></div>`).join('')}</div>`;
    }

    function applyFixToDoc() {
      if (!currentAnalysis||!currentAnalysis.suggested_fixes) return;
      const lines = document.getElementById('docInput').value.split('\n');
      for (const fix of currentAnalysis.suggested_fixes) {
        const idx = fix.doc_line-1;
        if (idx>=0&&idx<lines.length) lines[idx] = fix.suggested_line;
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
      const orig = btn.innerHTML;
      btn.innerHTML = '&#10003; Copied!';
      setTimeout(()=>btn.innerHTML=orig, 2000);
    });

    loadSamples();
    loadOllamaModels();
  </script>
</body>
</html>"""

if __name__ == "__main__":
    launch_dashboard()
