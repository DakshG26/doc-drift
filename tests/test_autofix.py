import os
import tempfile
from doc_drift.autofix import apply_fixes_in_place, generate_fixes, generate_github_suggestion_block
from doc_drift.semantic_matcher import DriftFinding

def test_generate_and_apply_fixes():
    with tempfile.TemporaryDirectory() as tmpdir:
        doc_name = "config.md"
        doc_path = os.path.join(tmpdir, doc_name)
        with open(doc_path, "w", encoding="utf-8") as f:
            f.write("# Configuration Guide\n")
            f.write("Pass --timeout to stop slow tasks.\n")

        findings = [
            DriftFinding(
                doc_file=doc_name,
                doc_line=2,
                sentence="Pass --timeout to stop slow tasks.",
                changed_symbol="--timeout",
                new_symbol="--deadline",
                code_file="cli.py",
                code_line=40,
                confidence=0.96,
                change_type="renamed",
                match_method="exact",
            )
        ]

        fixes = generate_fixes(findings, repo_dir=tmpdir)
        assert len(fixes) == 1
        assert "--deadline" in fixes[0].suggested_line
        assert fixes[0].symbol_replaced == "--timeout"

        block = generate_github_suggestion_block(fixes[0])
        assert "```suggestion" in block
        assert "--deadline" in block

        # Test applying in place
        updated = apply_fixes_in_place(fixes, repo_dir=tmpdir)
        assert updated == 1

        with open(doc_path, "r", encoding="utf-8") as f:
            content = f.read()
        assert "--deadline" in content
        assert "--timeout" not in content
