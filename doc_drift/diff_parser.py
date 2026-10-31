"""
diff_parser.py - Robust code diff and AST change extractor.
Parses unified diffs to extract renamed CLI flags, changed default values,
renamed functions/methods, altered constants, and removed parameters.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional
from unidiff import PatchSet


@dataclass
class SymbolChange:
    """Represents a code modification detected in a pull request diff."""
    change_type: str         # "renamed", "default_changed", "removed", "added"
    old_symbol: str
    new_symbol: Optional[str]
    file: str
    line: int
    context: str = ""


def parse_diff(diff_text: str) -> list[SymbolChange]:
    """
    Parse unified diff text and extract meaningful symbol modifications.

    Args:
        diff_text: Unified diff string (e.g., git diff output or .patch file)

    Returns:
        List of detected SymbolChange instances
    """
    changes: list[SymbolChange] = []
    file_chunks = _extract_file_diffs(diff_text)

    for filename, removed_lines, added_lines in file_chunks:
        # 1. CLI Flags (argparse, click, etc.)
        changes.extend(_detect_cli_flag_changes(filename, removed_lines, added_lines))

        # 2. Default Values (functions, configs)
        changes.extend(_detect_default_value_changes(filename, removed_lines, added_lines))

        # 3. Function & Class definitions
        changes.extend(_detect_callable_changes(filename, removed_lines, added_lines))

        # 4. Global constants (UPPERCASE_CONFIG = val)
        changes.extend(_detect_constant_changes(filename, removed_lines, added_lines))

    return changes


def _extract_file_diffs(diff_text: str) -> list[tuple[str, list[tuple[int, str]], list[tuple[int, str]]]]:
    """
    Extract (filename, removed_lines, added_lines) from diff text,
    using unidiff when possible, and falling back to direct line parsing.
    """
    results = []

    try:
        patch = PatchSet(diff_text)
        for patched_file in patch:
            removed = []
            added = []
            for hunk in patched_file:
                for line in hunk:
                    if line.is_removed:
                        removed.append((line.source_line_no or 0, line.value.strip()))
                    elif line.is_added:
                        added.append((line.target_line_no or 0, line.value.strip()))
            results.append((patched_file.path, removed, added))
        if results:
            return results
    except Exception:
        pass

    # Fallback line-by-line diff parser
    current_file = "unknown"
    removed = []
    added = []
    lineno = 1

    for line in diff_text.splitlines():
        if line.startswith("+++ b/") or line.startswith("+++ "):
            if current_file != "unknown" and (removed or added):
                results.append((current_file, removed, added))
                removed, added = [], []
            current_file = line[6:].strip() if line.startswith("+++ b/") else line[4:].strip()
        elif line.startswith("--- "):
            continue
        elif line.startswith("@@"):
            m = re.search(r'\+(\d+)', line)
            if m:
                lineno = int(m.group(1))
        elif line.startswith("-") and not line.startswith("---"):
            removed.append((lineno, line[1:].strip()))
        elif line.startswith("+") and not line.startswith("+++"):
            added.append((lineno, line[1:].strip()))
            lineno += 1
        else:
            lineno += 1

    if current_file != "unknown" and (removed or added):
        results.append((current_file, removed, added))

    return results


def _extract_flag_names(lines: list[tuple[int, str]]) -> dict[str, int]:
    """Extract CLI flag identifiers (e.g., '--timeout', '-t') and line numbers."""
    flags: dict[str, int] = {}
    pattern = re.compile(r'add_argument\(\s*["\'](-{1,2}[\w-]+)["\']')
    for lineno, text in lines:
        for match in pattern.finditer(text):
            flags[match.group(1)] = lineno
    return flags


def _detect_cli_flag_changes(
    filename: str,
    removed: list[tuple[int, str]],
    added: list[tuple[int, str]],
) -> list[SymbolChange]:
    """Identify renamed, removed, or added CLI arguments."""
    changes: list[SymbolChange] = []
    old_flags = _extract_flag_names(removed)
    new_flags = _extract_flag_names(added)

    removed_set = set(old_flags.keys()) - set(new_flags.keys())
    added_set = set(new_flags.keys()) - set(old_flags.keys())

    # Detect 1-to-1 flag renaming
    if len(removed_set) == 1 and len(added_set) == 1:
        old = next(iter(removed_set))
        new = next(iter(added_set))
        changes.append(SymbolChange(
            change_type="renamed",
            old_symbol=old,
            new_symbol=new,
            file=filename,
            line=new_flags[new],
            context=f"CLI flag {old} renamed to {new}",
        ))
    else:
        for flag in removed_set:
            changes.append(SymbolChange(
                change_type="removed",
                old_symbol=flag,
                new_symbol=None,
                file=filename,
                line=old_flags[flag],
                context=f"CLI flag {flag} removed",
            ))
        for flag in added_set:
            changes.append(SymbolChange(
                change_type="added",
                old_symbol=flag,
                new_symbol=flag,
                file=filename,
                line=new_flags[flag],
                context=f"CLI flag {flag} added",
            ))

    return changes


def _detect_default_value_changes(
    filename: str,
    removed: list[tuple[int, str]],
    added: list[tuple[int, str]],
) -> list[SymbolChange]:
    """Identify changes in default parameters (e.g., timeout=30 -> timeout=60)."""
    changes: list[SymbolChange] = []
    param_pattern = re.compile(r'(\b[a-zA-Z_]\w*)\s*[:=]\s*(["\']?[\w./-]+["\']?)')

    old_params: dict[str, tuple[str, int]] = {}
    new_params: dict[str, tuple[str, int]] = {}

    ignored = {"self", "cls", "True", "False", "None", "int", "str", "float", "bool"}

    for lineno, text in removed:
        for m in param_pattern.finditer(text):
            k, v = m.group(1), m.group(2)
            if k not in ignored:
                old_params[k] = (v, lineno)

    for lineno, text in added:
        for m in param_pattern.finditer(text):
            k, v = m.group(1), m.group(2)
            if k not in ignored:
                new_params[k] = (v, lineno)

    common_keys = set(old_params.keys()) & set(new_params.keys())
    for k in common_keys:
        old_val, _ = old_params[k]
        new_val, line = new_params[k]
        if old_val != new_val:
            changes.append(SymbolChange(
                change_type="default_changed",
                old_symbol=f"{k}={old_val}",
                new_symbol=f"{k}={new_val}",
                file=filename,
                line=line,
                context=f"Default parameter {k} changed from {old_val} to {new_val}",
            ))

    return changes


def _detect_callable_changes(
    filename: str,
    removed: list[tuple[int, str]],
    added: list[tuple[int, str]],
) -> list[SymbolChange]:
    """Identify renamed or removed functions and classes."""
    changes: list[SymbolChange] = []
    func_regex = re.compile(r'def\s+([a-zA-Z_]\w*)\s*\(')

    old_funcs: dict[str, int] = {}
    new_funcs: dict[str, int] = {}

    for lineno, text in removed:
        m = func_regex.search(text)
        if m:
            old_funcs[m.group(1)] = lineno

    for lineno, text in added:
        m = func_regex.search(text)
        if m:
            new_funcs[m.group(1)] = lineno

    removed_set = set(old_funcs.keys()) - set(new_funcs.keys())
    added_set = set(new_funcs.keys()) - set(old_funcs.keys())

    if len(removed_set) == 1 and len(added_set) == 1:
        old = next(iter(removed_set))
        new = next(iter(added_set))
        changes.append(SymbolChange(
            change_type="renamed",
            old_symbol=f"{old}()",
            new_symbol=f"{new}()",
            file=filename,
            line=new_funcs[new],
            context=f"Function {old}() renamed to {new}()",
        ))
    else:
        for f in removed_set:
            changes.append(SymbolChange(
                change_type="removed",
                old_symbol=f"{f}()",
                new_symbol=None,
                file=filename,
                line=old_funcs[f],
                context=f"Function {f}() removed",
            ))

    return changes


def _detect_constant_changes(
    filename: str,
    removed: list[tuple[int, str]],
    added: list[tuple[int, str]],
) -> list[SymbolChange]:
    """Identify changed module-level uppercase constants."""
    changes: list[SymbolChange] = []
    const_regex = re.compile(r'^([A-Z][A-Z0-9_]{2,})\s*=\s*(.+)$')

    old_consts: dict[str, tuple[str, int]] = {}
    new_consts: dict[str, tuple[str, int]] = {}

    for lineno, text in removed:
        m = const_regex.match(text)
        if m:
            old_consts[m.group(1)] = (m.group(2).strip(), lineno)

    for lineno, text in added:
        m = const_regex.match(text)
        if m:
            new_consts[m.group(1)] = (m.group(2).strip(), lineno)

    for k in set(old_consts.keys()) & set(new_consts.keys()):
        old_val, _ = old_consts[k]
        new_val, line = new_consts[k]
        if old_val != new_val:
            changes.append(SymbolChange(
                change_type="default_changed",
                old_symbol=f"{k}={old_val}",
                new_symbol=f"{k}={new_val}",
                file=filename,
                line=line,
                context=f"Constant {k} updated from {old_val} to {new_val}",
            ))

    return changes
