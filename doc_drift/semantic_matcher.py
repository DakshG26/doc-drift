"""
semantic_matcher.py - Hybrid Semantic and Exact Matching Engine.
Combines literal token matching, vector embeddings (sentence-transformers),
and optional local LLM verification to detect documentation drift with high precision.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional

from .diff_parser import SymbolChange
from .doc_scanner import DocSentence
from .llm_verifier import verify_drift_with_llm

_embedding_model = None


def _get_embedding_model():
    """Lazy loader for sentence-transformers embedding model."""
    global _embedding_model
    if _embedding_model is None:
        try:
            from sentence_transformers import SentenceTransformer
            _embedding_model = SentenceTransformer("all-MiniLM-L6-v2")
        except Exception:
            _embedding_model = False
    return _embedding_model


@dataclass
class DriftFinding:
    """A verified documentation drift finding."""
    doc_file: str
    doc_line: int
    sentence: str
    changed_symbol: str
    new_symbol: Optional[str]
    code_file: str
    code_line: int
    confidence: float
    change_type: str
    match_method: str
    explanation: str = ""


def find_drift(
    changes: list[SymbolChange],
    doc_sentences: list[DocSentence],
    threshold: float = 0.65,
    enable_llm_check: bool = False,
) -> list[DriftFinding]:
    """
    Match code symbol changes against scanned documentation sentences.

    Args:
        changes: List of code modifications from diff_parser.
        doc_sentences: Scanned sentences from documentation files.
        threshold: Minimum cosine similarity score for semantic matches.
        enable_llm_check: Whether to verify findings with local Ollama models.

    Returns:
        Deduplicated list of DriftFinding instances sorted by confidence.
    """
    if not changes or not doc_sentences:
        return []

    findings: list[DriftFinding] = []
    matched_positions: set[tuple[str, int]] = set()

    # Pass 1: Exact and Substring Matching
    for change in changes:
        old_sym = change.old_symbol.strip('"\'')
        for doc in doc_sentences:
            if _is_exact_or_inline_match(old_sym, doc):
                finding = DriftFinding(
                    doc_file=doc.file,
                    doc_line=doc.line,
                    sentence=doc.text,
                    changed_symbol=change.old_symbol,
                    new_symbol=change.new_symbol,
                    code_file=change.file,
                    code_line=change.line,
                    confidence=0.96,
                    change_type=change.change_type,
                    match_method="exact",
                    explanation=f"Exact symbol '{old_sym}' detected in documentation.",
                )
                findings.append(finding)
                matched_positions.add((doc.file, doc.line))

    # Pass 2: Vector Embedding Matching (all-MiniLM-L6-v2)
    unmatched_docs = [d for d in doc_sentences if (d.file, d.line) not in matched_positions]
    model = _get_embedding_model()

    if model and unmatched_docs:
        from sentence_transformers import util

        change_texts = [f"{c.old_symbol} {c.context}" for c in changes]
        doc_texts = [d.text for d in unmatched_docs]

        c_emb = model.encode(change_texts, convert_to_tensor=True)
        d_emb = model.encode(doc_texts, convert_to_tensor=True)

        scores = util.cos_sim(c_emb, d_emb)

        for c_idx, change in enumerate(changes):
            for d_idx, doc in enumerate(unmatched_docs):
                score = float(scores[c_idx][d_idx])
                if score >= threshold:
                    findings.append(DriftFinding(
                        doc_file=doc.file,
                        doc_line=doc.line,
                        sentence=doc.text,
                        changed_symbol=change.old_symbol,
                        new_symbol=change.new_symbol,
                        code_file=change.file,
                        code_line=change.line,
                        confidence=round(score, 2),
                        change_type=change.change_type,
                        match_method="semantic_embedding",
                        explanation=f"Semantic vector match (cosine similarity {score:.2f})",
                    ))

    # Optional Pass 3: Verification with Local Ollama LLM
    if enable_llm_check and findings:
        for f in findings:
            res = verify_drift_with_llm(
                old_symbol=f.changed_symbol,
                new_symbol=f.new_symbol,
                code_context=f"{f.change_type} in {f.code_file}:{f.code_line}",
                doc_sentence=f.sentence,
            )
            if res.get("is_drift", True):
                f.confidence = max(f.confidence, res.get("confidence", 0.90))
                f.explanation = res.get("explanation", f.explanation)

    # Deduplicate and sort
    seen = set()
    unique_findings = []
    for f in sorted(findings, key=lambda x: x.confidence, reverse=True):
        ident = (f.doc_file, f.doc_line, f.changed_symbol)
        if ident not in seen:
            seen.add(ident)
            unique_findings.append(f)

    return unique_findings


def _is_exact_or_inline_match(symbol: str, doc: DocSentence) -> bool:
    """Check if the symbol or flag is present in doc sentence or inline symbols."""
    # Split on '=' in case of parameters like timeout=30
    symbol_name = symbol.split("=")[0].strip()

    # Exact token or inline symbol match
    if symbol in doc.inline_symbols or symbol_name in doc.inline_symbols:
        return True

    # Word boundary match in text
    pattern = rf'(?:\b|_){re.escape(symbol_name)}(?:\b|_)'
    if re.search(pattern, doc.text, re.IGNORECASE):
        return True

    return False
