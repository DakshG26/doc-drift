"""
doc_scanner.py - Precise documentation sentence and symbol extractor.
Walks Markdown (.md), reStructuredText (.rst), and documentation files,
extracting distinct sentences while preserving exact file paths and line numbers.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass


DOC_EXTENSIONS = {".md", ".rst", ".txt"}
EXCLUDE_DIRS = {".git", ".github", "node_modules", "venv", ".venv", "__pycache__", "build", "dist"}


@dataclass
class DocSentence:
    """A single sentence extracted from project documentation."""
    file: str
    line: int
    text: str
    inline_symbols: list[str]


def scan_docs(repo_dir: str) -> list[DocSentence]:
    """
    Scan repo_dir for documentation files and index all explanatory sentences.

    Args:
        repo_dir: Root directory of the repository or docs folder.

    Returns:
        List of extracted DocSentence objects.
    """
    sentences: list[DocSentence] = []

    for root, dirs, files in os.walk(repo_dir):
        dirs[:] = [d for d in dirs if d not in EXCLUDE_DIRS]

        for fname in files:
            ext = os.path.splitext(fname)[1].lower()
            if ext not in DOC_EXTENSIONS:
                continue

            filepath = os.path.join(root, fname)
            rel_path = os.path.relpath(filepath, repo_dir)

            try:
                with open(filepath, "r", encoding="utf-8", errors="replace") as f:
                    content = f.read()
            except OSError:
                continue

            if ext == ".md":
                sentences.extend(_parse_markdown_sentences(rel_path, content))
            elif ext == ".rst":
                sentences.extend(_parse_rst_sentences(rel_path, content))
            else:
                sentences.extend(_parse_plaintext_sentences(rel_path, content))

    return sentences


def _extract_inline_symbols(text: str) -> list[str]:
    """Extract backtick code fragments and CLI flag patterns."""
    codes = re.findall(r"`([^`]+)`", text)
    flags = re.findall(r"(?:\s|^)(-{1,2}[a-zA-Z0-9_\-]+)", text)
    return list(set(codes + [f.strip() for f in flags]))


def _parse_markdown_sentences(filepath: str, content: str) -> list[DocSentence]:
    """Tokenize markdown files, skipping code fences, preserving line numbers."""
    sentences: list[DocSentence] = []
    lines = content.split("\n")
    in_code_fence = False

    for lineno, raw_line in enumerate(lines, start=1):
        stripped = raw_line.strip()

        # Handle code fence toggling (``` or ~~~)
        if stripped.startswith("```") or stripped.startswith("~~~"):
            in_code_fence = not in_code_fence
            continue

        if in_code_fence or not stripped:
            continue

        # Strip markdown syntax markup
        clean = raw_line
        clean = re.sub(r"^#{1,6}\s*", "", clean)                  # Headers
        clean = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", clean)     # Links [text](url) -> text
        clean = re.sub(r"\*\*([^*]+)\*\*", r"\1", clean)          # Bold **text** -> text
        clean = re.sub(r"\*([^*]+)\*", r"\1", clean)              # Italic *text* -> text
        clean = re.sub(r"^[-*+]\s+", "", clean)                    # Bullet lists
        clean = re.sub(r"^\d+\.\s+", "", clean)                    # Numbered lists
        clean = re.sub(r"^>\s*", "", clean)                        # Blockquotes
        clean = clean.strip()

        if len(clean) >= 12:
            symbols = _extract_inline_symbols(raw_line)
            sentences.append(DocSentence(
                file=filepath,
                line=lineno,
                text=clean,
                inline_symbols=symbols,
            ))

    return sentences


def _parse_rst_sentences(filepath: str, content: str) -> list[DocSentence]:
    """Tokenize reStructuredText (.rst) files."""
    sentences: list[DocSentence] = []
    lines = content.split("\n")
    in_literal = False

    for lineno, raw_line in enumerate(lines, start=1):
        stripped = raw_line.strip()

        if stripped.startswith(".. code") or stripped.endswith("::"):
            in_literal = True
            continue

        if not stripped:
            in_literal = False
            continue

        if in_literal:
            continue

        # Strip common RST directives
        clean = re.sub(r":[\w]+:`([^`]+)`", r"\1", stripped)
        clean = re.sub(r"``([^`]+)``", r"\1", clean)
        clean = re.sub(r"^[=\-~^\"*+#<>`]{3,}$", "", clean).strip()

        if len(clean) >= 12:
            symbols = _extract_inline_symbols(raw_line)
            sentences.append(DocSentence(
                file=filepath,
                line=lineno,
                text=clean,
                inline_symbols=symbols,
            ))

    return sentences


def _parse_plaintext_sentences(filepath: str, content: str) -> list[DocSentence]:
    """Tokenize plain text documentation."""
    sentences: list[DocSentence] = []
    lines = content.split("\n")

    for lineno, raw_line in enumerate(lines, start=1):
        clean = raw_line.strip()
        if len(clean) >= 12:
            symbols = _extract_inline_symbols(raw_line)
            sentences.append(DocSentence(
                file=filepath,
                line=lineno,
                text=clean,
                inline_symbols=symbols,
            ))

    return sentences
