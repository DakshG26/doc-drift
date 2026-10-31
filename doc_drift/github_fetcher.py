"""
github_fetcher.py - Automated GitHub Repository and Pull Request Data Fetcher.
Retrieves pull request diffs and repository documentation files directly
from GitHub public URLs or the GitHub REST API.
"""

from __future__ import annotations

import base64
import os
import re
from typing import Optional, Tuple
import requests

GITHUB_API = "https://api.github.com"


def parse_github_url(url: str) -> Tuple[str, str, Optional[int]]:
    """
    Parse a GitHub URL into (owner, repo, pr_number).

    Supports:
      - https://github.com/owner/repo/pull/123
      - https://github.com/owner/repo
      - owner/repo#123
      - owner/repo
    """
    cleaned = url.strip().rstrip("/")

    # Match pull request: .../owner/repo/pull/123
    pr_match = re.search(r"github\.com/([^/]+)/([^/]+)/pull/(\d+)", cleaned)
    if pr_match:
        return pr_match.group(1), pr_match.group(2), int(pr_match.group(3))

    # Match shorthand: owner/repo#123
    short_pr_match = re.match(r"^([^/]+)/([^#]+)#(\d+)$", cleaned)
    if short_pr_match:
        return short_pr_match.group(1), short_pr_match.group(2), int(short_pr_match.group(3))

    # Match repo url: .../owner/repo
    repo_match = re.search(r"github\.com/([^/]+)/([^/]+)", cleaned)
    if repo_match:
        return repo_match.group(1), repo_match.group(2), None

    # Match shorthand: owner/repo
    short_match = re.match(r"^([^/]+)/([^/]+)$", cleaned)
    if short_match:
        return short_match.group(1), short_match.group(2), None

    raise ValueError(f"Unrecognized GitHub repository or pull request URL: {url!r}")


def fetch_github_pr_diff(owner: str, repo: str, pr_number: int, token: Optional[str] = None) -> str:
    """Fetch the unified diff of a pull request from GitHub API."""
    url = f"{GITHUB_API}/repos/{owner}/{repo}/pulls/{pr_number}"
    headers = {
        "Accept": "application/vnd.github.v3.diff",
        "User-Agent": "DocDriftDetector/1.0",
    }
    auth_token = token or os.environ.get("GITHUB_TOKEN")
    if auth_token:
        headers["Authorization"] = f"token {auth_token}"

    res = requests.get(url, headers=headers, timeout=20)
    res.raise_for_status()
    return res.text


def fetch_github_readme(owner: str, repo: str, token: Optional[str] = None) -> Tuple[str, str]:
    """
    Fetch the default README file for a repository.

    Returns:
        (filename, markdown_content)
    """
    url = f"{GITHUB_API}/repos/{owner}/{repo}/readme"
    headers = {
        "Accept": "application/vnd.github.v3+json",
        "User-Agent": "DocDriftDetector/1.0",
    }
    auth_token = token or os.environ.get("GITHUB_TOKEN")
    if auth_token:
        headers["Authorization"] = f"token {auth_token}"

    res = requests.get(url, headers=headers, timeout=20)
    res.raise_for_status()
    data = res.json()

    filename = data.get("name", "README.md")
    encoded_content = data.get("content", "")
    content = base64.b64decode(encoded_content).decode("utf-8", errors="replace")

    return filename, content


def fetch_latest_pull_request_number(owner: str, repo: str, token: Optional[str] = None) -> Optional[int]:
    """Retrieve the most recent pull request number for a repository."""
    url = f"{GITHUB_API}/repos/{owner}/{repo}/pulls?state=all&per_page=1"
    headers = {
        "Accept": "application/vnd.github.v3+json",
        "User-Agent": "DocDriftDetector/1.0",
    }
    auth_token = token or os.environ.get("GITHUB_TOKEN")
    if auth_token:
        headers["Authorization"] = f"token {auth_token}"

    res = requests.get(url, headers=headers, timeout=20)
    if res.status_code == 200:
        pulls = res.json()
        if pulls and isinstance(pulls, list) and len(pulls) > 0:
            return pulls[0].get("number")
    return None


def fetch_target_from_url(
    github_url: str,
    token: Optional[str] = None,
) -> dict[str, Any]:
    """
    Comprehensive loader: given any GitHub URL, returns diff and documentation content.

    Returns:
        dict with keys: 'owner', 'repo', 'pr_number', 'diff_text', 'doc_name', 'doc_text'
    """
    owner, repo, pr_number = parse_github_url(github_url)

    if pr_number is None:
        pr_number = fetch_latest_pull_request_number(owner, repo, token)
        if pr_number is None:
            raise ValueError(f"No pull requests found for repository '{owner}/{repo}'. Please specify a PR URL directly.")

    diff_text = fetch_github_pr_diff(owner, repo, pr_number, token)
    doc_name, doc_text = fetch_github_readme(owner, repo, token)

    return {
        "owner": owner,
        "repo": repo,
        "pr_number": pr_number,
        "diff_text": diff_text,
        "doc_name": doc_name,
        "doc_text": doc_text,
    }
