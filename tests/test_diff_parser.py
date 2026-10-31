import pytest
from doc_drift.diff_parser import parse_diff

SAMPLE_FLAG_DIFF = """--- a/cli.py
+++ b/cli.py
@@ -85,3 +85,3 @@ def build_parser():
-    parser.add_argument("--timeout", type=int, default=30)
+    parser.add_argument("--deadline", type=int, default=30)
"""

SAMPLE_DEFAULT_DIFF = """--- a/config.py
+++ b/config.py
@@ -10,3 +10,3 @@
-timeout = 30
+timeout = 60
"""

SAMPLE_FUNC_DIFF = """--- a/service.py
+++ b/service.py
@@ -20,3 +20,3 @@
-def fetch_user_data(user_id):
+def get_user_profile(user_id):
     pass
"""

def test_parse_cli_flag_rename():
    changes = parse_diff(SAMPLE_FLAG_DIFF)
    assert len(changes) == 1
    assert changes[0].change_type == "renamed"
    assert changes[0].old_symbol == "--timeout"
    assert changes[0].new_symbol == "--deadline"
    assert "cli.py" in changes[0].file

def test_parse_default_value_change():
    changes = parse_diff(SAMPLE_DEFAULT_DIFF)
    assert len(changes) == 1
    assert changes[0].change_type == "default_changed"
    assert "timeout=30" in changes[0].old_symbol
    assert "timeout=60" in changes[0].new_symbol

def test_parse_callable_rename():
    changes = parse_diff(SAMPLE_FUNC_DIFF)
    assert len(changes) == 1
    assert changes[0].change_type == "renamed"
    assert "fetch_user_data" in changes[0].old_symbol
    assert "get_user_profile" in changes[0].new_symbol
