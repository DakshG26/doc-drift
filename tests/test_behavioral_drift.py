"""
test_behavioral_drift.py - Tests for return type, route change detection, and AI reasoning.
"""

from doc_drift.diff_parser import parse_diff
from doc_drift.doc_scanner import DocSentence
from doc_drift.reporter import generate_json_report, generate_markdown_summary
from doc_drift.semantic_matcher import find_drift, DriftFinding


SAMPLE_RETURN_TYPE_DIFF = """--- a/api/service.py
+++ b/api/service.py
@@ -10,3 +10,3 @@
-def fetch_user_tokens(user_id: str) -> list[str]:
+def fetch_user_tokens(user_id: str) -> dict[str, str]:
     return {}
"""

SAMPLE_ROUTE_DIFF = """--- a/routes/web.py
+++ b/routes/web.py
@@ -15,3 +15,3 @@
-@app.get("/api/v1/checkout")
+@app.post("/api/v1/checkout")
 def checkout():
     pass
"""


def test_detect_return_type_changes():
    changes = parse_diff(SAMPLE_RETURN_TYPE_DIFF)
    assert len(changes) == 1
    c = changes[0]
    assert c.change_type == "return_type_changed"
    assert "list[str]" in c.old_symbol
    assert "dict[str, str]" in c.new_symbol


def test_detect_route_changes():
    changes = parse_diff(SAMPLE_ROUTE_DIFF)
    assert len(changes) == 1
    c = changes[0]
    assert c.change_type == "route_changed"
    assert "GET /api/v1/checkout" in c.old_symbol
    assert "POST /api/v1/checkout" in c.new_symbol


def test_behavioral_drift_matching_and_reasoning():
    changes = parse_diff(SAMPLE_ROUTE_DIFF)
    docs = [
        DocSentence(
            file="api_docs.md",
            line=42,
            text="Send a GET /api/v1/checkout request to finalize the shopping cart order.",
            inline_symbols=["GET /api/v1/checkout"],
        )
    ]

    findings = find_drift(changes, docs, enable_llm_check=True)
    assert len(findings) == 1
    f = findings[0]
    assert f.drift_category == "behavioral"
    assert f.doc_line == 42
    assert "checkout" in f.changed_symbol

    # Verify JSON report includes behavioral fields
    report = generate_json_report(findings)
    assert report["drift_detected"] is True
    assert report["findings"][0]["drift_category"] == "behavioral"

    # Verify Markdown summary includes reasoning
    summary = generate_markdown_summary(findings)
    assert "Documentation Drift Detected" in summary
    assert "api_docs.md" in summary
