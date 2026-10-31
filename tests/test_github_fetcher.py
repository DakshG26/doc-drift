import unittest.mock as mock
import pytest
from doc_drift.github_fetcher import parse_github_url, fetch_target_from_url

def test_parse_github_url_full_pr():
    owner, repo, pr = parse_github_url("https://github.com/pallets/flask/pull/4900")
    assert owner == "pallets"
    assert repo == "flask"
    assert pr == 4900

def test_parse_github_url_shorthand_pr():
    owner, repo, pr = parse_github_url("pallets/click#2600")
    assert owner == "pallets"
    assert repo == "click"
    assert pr == 2600

def test_parse_github_url_repo_only():
    owner, repo, pr = parse_github_url("https://github.com/psf/requests")
    assert owner == "psf"
    assert repo == "requests"
    assert pr is None

def test_parse_github_url_invalid():
    with pytest.raises(ValueError):
        parse_github_url("invalid-url-string")

@mock.patch("doc_drift.github_fetcher.fetch_github_pr_diff")
@mock.patch("doc_drift.github_fetcher.fetch_github_readme")
def test_fetch_target_from_url_mocked(mock_readme, mock_diff):
    mock_diff.return_value = "--- a/cli.py\n+++ b/cli.py\n@@ -1,1 +1,1 @@\n-old\n+new\n"
    mock_readme.return_value = ("README.md", "# Test Docs\nReference line.")

    target = fetch_target_from_url("https://github.com/testowner/testrepo/pull/42")
    assert target["owner"] == "testowner"
    assert target["repo"] == "testrepo"
    assert target["pr_number"] == 42
    assert "old" in target["diff_text"]
    assert "Test Docs" in target["doc_text"]
