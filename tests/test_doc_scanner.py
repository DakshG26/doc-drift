import tempfile
import os
from doc_drift.doc_scanner import scan_docs

def test_scan_markdown_file():
    with tempfile.TemporaryDirectory() as tmpdir:
        doc_path = os.path.join(tmpdir, "README.md")
        with open(doc_path, "w", encoding="utf-8") as f:
            f.write("# Getting Started\n\n")
            f.write("Use the `--timeout` parameter to stop slow jobs.\n")
            f.write("\n```python\n# Code snippet should be skipped\ncode = 123\n```\n")
            f.write("Another explanatory sentence about jobs.\n")

        sentences = scan_docs(tmpdir)
        texts = [s.text for s in sentences]
        assert any("--timeout" in t for t in texts)
        assert not any("code = 123" in t for t in texts)
        assert all(s.line > 0 for s in sentences)
