from doc_drift.reporter import generate_json_report, generate_markdown_summary
from doc_drift.semantic_matcher import DriftFinding

def test_generate_report_with_findings():
    findings = [
        DriftFinding(
            doc_file="README.md",
            doc_line=42,
            sentence="Use --timeout to cancel long jobs.",
            changed_symbol="--timeout",
            new_symbol="--deadline",
            code_file="cli.py",
            code_line=88,
            confidence=0.96,
            change_type="renamed",
            match_method="exact",
            explanation="Exact symbol found",
        )
    ]

    report = generate_json_report(findings)
    assert report["drift_detected"] is True
    assert report["total_issues"] == 1
    assert report["findings"][0]["doc_file"] == "README.md"

    md = generate_markdown_summary(findings)
    assert "Documentation Drift Detected" in md
    assert "`--timeout` → `--deadline`" in md

def test_generate_report_clean():
    report = generate_json_report([])
    assert report["drift_detected"] is False
    assert report["total_issues"] == 0

    md = generate_markdown_summary([])
    assert "Check Passed" in md
