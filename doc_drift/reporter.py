"""
reporter.py - Output formatting, JSON report generation, and GitHub PR commenting.
"""

from __future__ import annotations

import json
from typing import Any

from .semantic_matcher import DriftFinding


def generate_json_report(findings: list[DriftFinding]) -> dict[str, Any]:
    """Convert findings to a structured JSON-serializable dictionary."""
    return {
        "drift_detected": len(findings) > 0,
        "total_issues": len(findings),
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
    }


def generate_markdown_summary(findings: list[DriftFinding]) -> str:
    """Generate GitHub-flavored markdown table for Pull Request comments."""
    if not findings:
        return (
            "### ✅ Documentation Drift Check Passed\n\n"
            "All documentation accurately reflects the code changes in this pull request."
        )

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
        # Escape markdown pipes in sentences
        clean_sentence = preview.replace("|", "\\|")
        lines.append(
            f"| `{f.doc_file}` | {f.doc_line} | {clean_sentence} | `{f.changed_symbol}` → {replacement} | `{f.code_file}:{f.code_line}` | {int(f.confidence * 100)}% |"
        )

    lines.extend([
        "",
        "> **Action Required:** Update the affected documentation files before merging this pull request.",
        "",
        "<details>",
        "<summary>🤖 Analysis Engine Details</summary>",
        "",
        "- **Models:** Local Gemma 4 / Qwen / DeepSeek via Ollama + `all-MiniLM-L6-v2` embeddings",
        "- **Detection Mode:** Unified diff AST parsing + sentence-level semantic verification",
        "</details>",
    ])

    return "\n".join(lines)


def post_github_comment(
    findings: list[DriftFinding],
    repo_name: str,
    pr_number: int,
    github_token: str,
) -> bool:
    """Post markdown summary as a comment on the target GitHub pull request."""
    try:
        from github import Github
        gh = Github(github_token)
        repo = gh.get_repo(repo_name)
        pr = repo.get_pull(pr_number)
        comment_body = generate_markdown_summary(findings)
        pr.create_issue_comment(comment_body)
        return True
    except Exception as exc:
        print(f"Warning: Failed to post GitHub comment ({exc})")
        return False
