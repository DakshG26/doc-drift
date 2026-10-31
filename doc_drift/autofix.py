"""
autofix.py - Automated Documentation Repair and Suggestion Engine.
Generates proposed documentation corrections, GitHub suggestion blocks,
and unified patches to resolve documentation drift with one click.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from typing import Optional

from .semantic_matcher import DriftFinding


@dataclass
class DocFix:
    """Represents a concrete proposed replacement for an outdated documentation line."""
    doc_file: str
    doc_line: int
    original_line: str
    suggested_line: str
    symbol_replaced: str
    new_symbol: str
    confidence: float


def generate_fixes(findings: list[DriftFinding], repo_dir: str = ".") -> list[DocFix]:
    """
    Generate actionable text corrections for detected documentation drift findings.

    Args:
        findings: List of detected DriftFinding instances.
        repo_dir: Base directory containing documentation files.

    Returns:
        List of DocFix instances.
    """
    fixes: list[DocFix] = []

    for f in findings:
        if not f.new_symbol:
            continue

        file_path = os.path.join(repo_dir, f.doc_file)
        if not os.path.isfile(file_path):
            continue

        try:
            with open(file_path, "r", encoding="utf-8", errors="replace") as fh:
                lines = fh.readlines()
        except OSError:
            continue

        line_idx = f.doc_line - 1
        if 0 <= line_idx < len(lines):
            original = lines[line_idx].rstrip("\r\n")
            suggested = _apply_symbol_replacement(original, f.changed_symbol, f.new_symbol)
            if suggested != original:
                fixes.append(DocFix(
                    doc_file=f.doc_file,
                    doc_line=f.doc_line,
                    original_line=original,
                    suggested_line=suggested,
                    symbol_replaced=f.changed_symbol,
                    new_symbol=f.new_symbol,
                    confidence=f.confidence,
                ))

    return fixes


def _apply_symbol_replacement(line: str, old_sym: str, new_sym: str) -> str:
    """Apply replacement preserving case, quotes, and punctuation."""
    # Handle parameter assignment like timeout=30 -> deadline=60 or timeout=30 -> timeout=60
    if "=" in old_sym and "=" in new_sym:
        old_k, old_v = old_sym.split("=", 1)
        new_k, new_v = new_sym.split("=", 1)

        # Replace full pattern if present
        pattern_full = re.escape(old_sym)
        if re.search(pattern_full, line):
            return re.sub(pattern_full, new_sym, line)

        # Replace parameter name if renamed
        if old_k != new_k and re.search(rf'\b{re.escape(old_k)}\b', line):
            line = re.sub(rf'\b{re.escape(old_k)}\b', new_k, line)

        # Replace parameter value if changed (e.g. 30 -> 60)
        old_v_clean = old_v.strip('"\'')
        new_v_clean = new_v.strip('"\'')
        if old_v_clean != new_v_clean:
            # Match value with word boundary
            line = re.sub(rf'\b{re.escape(old_v_clean)}\b', new_v_clean, line)

        return line

    # Standard flag or function rename (e.g., --timeout -> --deadline)
    old_clean = old_sym.strip('"\'')
    new_clean = new_sym.strip('"\'')
    return re.sub(re.escape(old_clean), new_clean, line)


def generate_github_suggestion_block(fix: DocFix) -> str:
    """Format a GitHub PR suggestion block that allows 1-click commits."""
    return (
        f"**Suggested fix for `{fix.doc_file}` (line {fix.doc_line}):**\n"
        f"```suggestion\n"
        f"{fix.suggested_line}\n"
        f"```"
    )


def apply_fixes_in_place(fixes: list[DocFix], repo_dir: str = ".") -> int:
    """
    Write proposed fixes directly into documentation files on disk.

    Args:
        fixes: List of DocFix objects to apply.
        repo_dir: Base repository directory.

    Returns:
        Number of lines updated.
    """
    by_file: dict[str, list[DocFix]] = {}
    for fix in fixes:
        by_file.setdefault(fix.doc_file, []).append(fix)

    updated_count = 0
    for rel_path, file_fixes in by_file.items():
        full_path = os.path.join(repo_dir, rel_path)
        if not os.path.isfile(full_path):
            continue

        with open(full_path, "r", encoding="utf-8") as fh:
            lines = fh.readlines()

        # Sort fixes by line descending to preserve line indices
        for fix in sorted(file_fixes, key=lambda x: x.doc_line, reverse=True):
            idx = fix.doc_line - 1
            if 0 <= idx < len(lines):
                lines[idx] = fix.suggested_line + "\n"
                updated_count += 1

        with open(full_path, "w", encoding="utf-8") as fh:
            fh.writelines(lines)

    return updated_count
