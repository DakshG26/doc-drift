"""
github_action.py - CLI & GitHub Action Entrypoint.
Orchestrates diff retrieval, doc scanning, semantic verification, auto-repair, and reporting.
"""

from __future__ import annotations

import argparse
import json
import os
import sys

from .autofix import apply_fixes_in_place, generate_fixes
from .benchmark import print_benchmark_report, run_benchmarks
from .diff_parser import parse_diff
from .doc_scanner import scan_docs
from .reporter import generate_json_report, generate_markdown_summary, post_github_comment
from .semantic_matcher import find_drift


def run_pipeline(
    diff_content: str,
    target_dir: str,
    repo_name: str | None = None,
    pr_number: int | None = None,
    github_token: str | None = None,
    output_json: str = "drift_report.json",
    fail_on_drift: bool = True,
    apply_fixes: bool = False,
) -> int:
    """Execute the full documentation drift detection & auto-fix pipeline."""
    print("=" * 65)
    print("🚀 Doc Drift Detector - Starting Analysis")
    print("=" * 65)

    # 1. Parse Diff
    print("\n[1/5] Parsing code diff for symbol modifications...")
    changes = parse_diff(diff_content)
    print(f"      Detected {len(changes)} code modification(s):")
    for c in changes:
        target = f" -> {c.new_symbol}" if c.new_symbol else ""
        print(f"      • [{c.change_type}] {c.old_symbol}{target} in {c.file}:{c.line}")

    if not changes:
        print("\n✅ No functional code symbol modifications found in diff. Exiting cleanly.")
        with open(output_json, "w", encoding="utf-8") as f:
            json.dump(generate_json_report([]), f, indent=2)
        return 0

    # 2. Scan Documentation
    print(f"\n[2/5] Scanning documentation in '{target_dir}'...")
    sentences = scan_docs(target_dir)
    print(f"      Indexed {len(sentences)} sentence(s) across Markdown and RST files.")

    # 3. Match and Verify Drift
    print("\n[3/5] Running semantic matching and verification...")
    findings = find_drift(changes, sentences)
    print(f"      Identified {len(findings)} documentation drift issue(s).")

    # 4. Generate Auto-Fixes
    print("\n[4/5] Synthesizing 1-click documentation repairs...")
    fixes = generate_fixes(findings, target_dir)
    print(f"      Generated {len(fixes)} proposed documentation fix(es).")

    if apply_fixes and fixes:
        updated = apply_fixes_in_place(fixes, target_dir)
        print(f"      ✓ Automatically applied {updated} repair(s) directly to documentation files.")

    # 5. Save and Report
    print(f"\n[5/5] Generating reports...")
    report_dict = generate_json_report(findings, fixes)
    with open(output_json, "w", encoding="utf-8") as f:
        json.dump(report_dict, f, indent=2)
    print(f"      Saved structured JSON report to: {output_json}")

    markdown_summary = generate_markdown_summary(findings, fixes)
    print("\n" + markdown_summary)

    # Post GitHub Comment if credentials supplied
    if repo_name and pr_number and github_token and findings:
        print("\nPosting evaluation comment to GitHub Pull Request...")
        success = post_github_comment(findings, repo_name, pr_number, github_token, fixes)
        if success:
            print("✓ Comment successfully posted to PR.")

    print("\n" + "=" * 65)
    if findings and fail_on_drift and not apply_fixes:
        print("❌ FAILED: Documentation drift detected. Update docs or apply fixes to pass CI.")
        return 1
    else:
        print("✅ SUCCESS: Documentation is consistent with code changes.")
        return 0


def fetch_pr_diff(repo: str, pr_num: int, token: str) -> str:
    """Fetch unified diff from GitHub REST API."""
    import requests
    url = f"https://api.github.com/repos/{repo}/pulls/{pr_num}"
    headers = {
        "Authorization": f"token {token}",
        "Accept": "application/vnd.github.v3.diff",
    }
    resp = requests.get(url, headers=headers, timeout=25)
    resp.raise_for_status()
    return resp.text


def main() -> None:
    parser = argparse.ArgumentParser(description="Doc Drift Detector")
    parser.add_argument("--diff-file", help="Path to unified diff / patch file")
    parser.add_argument("--docs-dir", default=".", help="Root directory containing docs to inspect")
    parser.add_argument("--output", default="drift_report.json", help="Path to write JSON report")
    parser.add_argument("--fix", action="store_true", help="Automatically repair documentation files in place")
    parser.add_argument("--benchmark", action="store_true", help="Execute the historical open-source benchmark suite")
    parser.add_argument("--ui", action="store_true", help="Launch the interactive visual web dashboard")
    parser.add_argument("--port", type=int, default=8765, help="Port to serve web dashboard on (default: 8765)")
    parser.add_argument("--no-fail", action="store_true", help="Do not exit with code 1 if drift found")
    args = parser.parse_args()

    if args.ui:
        from .web_ui import launch_dashboard
        launch_dashboard(port=args.port)
        sys.exit(0)

    if args.benchmark:
        res = run_benchmarks()
        print_benchmark_report(res)
        sys.exit(0)

    # Detect CI environment variables
    env_token = os.environ.get("GITHUB_TOKEN")
    env_repo = os.environ.get("GITHUB_REPOSITORY")
    env_pr = os.environ.get("PR_NUMBER")

    if args.diff_file:
        with open(args.diff_file, "r", encoding="utf-8") as f:
            diff_text = f.read()
    elif env_token and env_repo and env_pr:
        diff_text = fetch_pr_diff(env_repo, int(env_pr), env_token)
    else:
        # Default fallback: inspect git diff HEAD~1
        try:
            import subprocess
            diff_text = subprocess.check_output(["git", "diff", "HEAD~1"], text=True)
        except Exception:
            diff_text = ""

    exit_code = run_pipeline(
        diff_content=diff_text,
        target_dir=args.docs_dir,
        repo_name=env_repo,
        pr_number=int(env_pr) if env_pr else None,
        github_token=env_token,
        output_json=args.output,
        fail_on_drift=not args.no_fail,
        apply_fixes=args.fix,
    )
    sys.exit(exit_code)


if __name__ == "__main__":
    main()
