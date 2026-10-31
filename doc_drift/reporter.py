"""
reporter.py - Output formatting, JSON report generation, and GitHub PR commenting.
"""

from __future__ import annotations

import json
from typing import Any, Optional

from .autofix import DocFix, generate_github_suggestion_block
from .semantic_matcher import DriftFinding


def generate_json_report(
    findings: list[DriftFinding],
    fixes: Optional[list[DocFix]] = None,
) -> dict[str, Any]:
    """Convert findings and suggested fixes to a structured JSON-serializable dictionary."""
    fixes = fixes or []
    return {
        "drift_detected": len(findings) > 0,
        "total_issues": len(findings),
        "total_fixes_generated": len(fixes),
        "summary": (
            f"Found {len(findings)} documentation drift issue(s)."
            if findings
            else "No documentation drift detected."
        ),
        "findings": [
            {
                "doc_file": f.doc_file,
                "doc_line": f.doc_line,
                "stale_sentence": f.sentence,
                "changed_symbol": f.changed_symbol,
                "new_symbol": f.new_symbol,
                "code_file": f.code_file,
                "code_line": f.code_line,
                "confidence": f.confidence,
                "change_type": f.change_type,
                "match_method": f.match_method,
                "explanation": f.explanation,
            }
            for f in findings
        ],
        "suggested_fixes": [
            {
                "doc_file": fx.doc_file,
                "doc_line": fx.doc_line,
                "original_line": fx.original_line,
                "suggested_line": fx.suggested_line,
                "symbol_replaced": fx.symbol_replaced,
                "new_symbol": fx.new_symbol,
            }
            for fx in fixes
        ],
    }


def generate_markdown_summary(
    findings: list[DriftFinding],
    fixes: Optional[list[DocFix]] = None,
) -> str:
    """Generate GitHub-flavored markdown table and 1-click suggestion blocks for PR comments."""
    if not findings:
        return (
            "### ✅ Documentation Drift Check Passed\n\n"
            "All documentation accurately reflects the code changes in this pull request."
        )

    fixes = fixes or []
    lines = [
        "### ⚠️ Documentation Drift Detected",
        "",
        f"**{len(findings)} potential documentation discrepancy detected** based on the code changes in this PR:",
        "",
        "| File | Line | Outdated Sentence | Modified Symbol | Code Origin | Confidence |",
        "| :--- | :--- | :--- | :--- | :--- | :--- |",
    ]

    for f in findings:
        replacement = f"`{f.new_symbol}`" if f.new_symbol else "*removed*"
        preview = f.sentence[:75] + "..." if len(f.sentence) > 75 else f.sentence
        clean_sentence = preview.replace("|", "\\|")
        lines.append(
            f"| `{f.doc_file}` | {f.doc_line} | {clean_sentence} | `{f.changed_symbol}` → {replacement} | `{f.code_file}:{f.code_line}` | {int(f.confidence * 100)}% |"
        )

    # 1-Click Suggestions
    if fixes:
        lines.extend([
            "",
            "### 🪄 1-Click Proposed Documentation Fixes",
            "Maintainers can review and commit the suggested modifications below:",
            "",
        ])
        for fx in fixes:
            lines.append(generate_github_suggestion_block(fx))
            lines.append("")

    lines.extend([
        "",
        "> **Action Required:** Update the affected documentation files or apply suggestions before merging.",
        "",
        "<details>",
        "<summary>🤖 Analysis Engine Details</summary>",
        "",
        "- **Models:** Local Gemma 4 / Qwen / DeepSeek via Ollama + `all-MiniLM-L6-v2` embeddings",
        "- **Detection Mode:** Unified diff AST parsing + sentence-level semantic verification",
        "- **Auto-Fixer:** AST-guided regex replacement engine",
        "</details>",
    ])

    return "\n".join(lines)


def post_github_comment(
    findings: list[DriftFinding],
    repo_name: str,
    pr_number: int,
    github_token: str,
    fixes: Optional[list[DocFix]] = None,
) -> bool:
    """Post markdown summary as a comment on the target GitHub pull request."""
    try:
        from github import Github
        gh = Github(github_token)
        repo = gh.get_repo(repo_name)
        pr = repo.get_pull(pr_number)
        comment_body = generate_markdown_summary(findings, fixes)
        pr.create_issue_comment(comment_body)
        return True
    except Exception as exc:
        print(f"Warning: Failed to post GitHub comment ({exc})")
        return False
