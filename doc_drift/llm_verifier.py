"""
llm_verifier.py - Local Open-Weight LLM Verification via Ollama.
Integrates local models (Gemma 4, Qwen, DeepSeek) to perform deep semantic
reasoning and verify whether a code diff truly invalidates a documentation claim.
"""

from __future__ import annotations

import json
import logging
import requests
from typing import Optional

logger = logging.getLogger(__name__)

OLLAMA_ENDPOINT = "http://localhost:11434/api/generate"
DEFAULT_MODELS = ["gemma4:e4b", "gemma4:12b", "qwen2.5-coder-64k", "qwen", "deepseek"]


def verify_drift_with_llm(
    old_symbol: str,
    new_symbol: Optional[str],
    code_context: str,
    doc_sentence: str,
    model_name: Optional[str] = None,
    timeout_sec: float = 5.0,
) -> dict[str, any]:
    """
    Query a locally running Ollama model to verify if code change causes doc drift.

    Args:
        old_symbol: The previous code symbol (e.g. '--timeout')
        new_symbol: The replacement symbol (e.g. '--deadline')
        code_context: Description of the code modification
        doc_sentence: The documentation sentence under review
        model_name: Specific Ollama model name, or auto-detect
        timeout_sec: Timeout for local inference call

    Returns:
        dict with keys: 'is_drift' (bool), 'confidence' (float), 'explanation' (str), 'model' (str)
    """
    prompt = f"""You are a precise Documentation Drift Analyzer.
Determine whether the following code change makes the documentation sentence factually INCORRECT or OBSOLETE.

CODE CHANGE:
{code_context} (Symbol '{old_symbol}' -> '{new_symbol}')

DOCUMENTATION SENTENCE:
"{doc_sentence}"

Respond in valid JSON format only:
{{"is_drift": true/false, "confidence": 0.0 to 1.0, "explanation": "brief reason"}}"""

    chosen_model = model_name or "gemma4:e4b"

    try:
        payload = {
            "model": chosen_model,
            "prompt": prompt,
            "stream": False,
            "format": "json"
        }
        res = requests.post(OLLAMA_ENDPOINT, json=payload, timeout=timeout_sec)
        if res.status_code == 200:
            data = res.json()
            response_text = data.get("response", "{}")
            parsed = json.loads(response_text)
            return {
                "is_drift": bool(parsed.get("is_drift", True)),
                "confidence": float(parsed.get("confidence", 0.90)),
                "explanation": str(parsed.get("explanation", "Verified via local LLM")),
                "model": chosen_model,
            }
    except Exception as e:
        logger.debug(f"Ollama local inference unavailable ({e}), using fallback validator.")

    # Graceful fallback: rule-based semantic verification
    old_clean = old_symbol.strip('"\'')
    sentence_clean = doc_sentence.lower()
    is_drift = old_clean.lower() in sentence_clean
    confidence = 0.95 if is_drift else 0.70

    return {
        "is_drift": is_drift,
        "confidence": confidence,
        "explanation": f"Validated symbol match for {old_symbol}",
        "model": "offline-heuristic-validator",
    }
