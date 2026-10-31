from doc_drift.diff_parser import SymbolChange
from doc_drift.doc_scanner import DocSentence
from doc_drift.semantic_matcher import find_drift

def test_find_drift_exact():
    changes = [
        SymbolChange(
            change_type="renamed",
            old_symbol="--timeout",
            new_symbol="--deadline",
            file="cli.py",
            line=88,
        )
    ]

    docs = [
        DocSentence(
            file="README.md",
            line=42,
            text="Pass --timeout to terminate jobs taking longer than expected.",
            inline_symbols=["--timeout"],
        ),
        DocSentence(
            file="docs/intro.md",
            line=10,
            text="Welcome to the platform documentation.",
            inline_symbols=[],
        ),
    ]

    findings = find_drift(changes, docs)
    assert len(findings) == 1
    assert findings[0].doc_file == "README.md"
    assert findings[0].doc_line == 42
    assert findings[0].changed_symbol == "--timeout"
    assert findings[0].confidence >= 0.90
