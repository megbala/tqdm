"""
Thin, transparent wrapper around GitHub's REST API. Plain `requests` calls rather than
a full SDK, so it's easy to read and easy to explain in the README.
"""

import base64

import requests

GITHUB_API = "https://api.github.com"


def _headers(token: str) -> dict:
    return {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }


def get_pr_files(owner: str, repo: str, pr_number: int, token: str) -> list[dict]:
    """Return the list of changed files (each including a .patch diff hunk) for a PR."""
    url = f"{GITHUB_API}/repos/{owner}/{repo}/pulls/{pr_number}/files"
    files, page = [], 1
    while True:
        resp = requests.get(url, headers=_headers(token), params={"per_page": 100, "page": page})
        resp.raise_for_status()
        batch = resp.json()
        if not batch:
            break
        files.extend(batch)
        page += 1
    return files


def read_file(owner: str, repo: str, path: str, ref: str, token: str) -> str:
    """
    Fetch a file's full content at a given ref.

    Not called anywhere yet -- this is the hook for the "give the model a read_file
    tool" upgrade discussed in planning: let Claude request full file context beyond
    the diff hunk when a change is hard to judge in isolation.
    """
    url = f"{GITHUB_API}/repos/{owner}/{repo}/contents/{path}"
    resp = requests.get(url, headers=_headers(token), params={"ref": ref})
    resp.raise_for_status()
    data = resp.json()
    return base64.b64decode(data["content"]).decode("utf-8", errors="replace")


def _format_comment_body(c: dict) -> str:
    icon = {"bug": "🐛", "warning": "⚠️", "suggestion": "💡", "nit": "🔹"}.get(c["severity"], "🔹")
    text = f"{icon} **{c['severity'].upper()}**: {c['comment']}"
    if c.get("suggestion"):
        text += f"\n```suggestion\n{c['suggestion']}\n```"
    return text


def post_review(owner: str, repo: str, pr_number: int, token: str, summary: str, comments: list[dict]) -> None:
    """
    Submit one PR review: a summary plus inline comments, using GitHub's line/side
    fields (no legacy diff-position math needed). GitHub will reject a comment if the
    line isn't actually part of the diff -- we catch that and fall back to a
    summary-only review rather than failing the whole job.
    """
    url = f"{GITHUB_API}/repos/{owner}/{repo}/pulls/{pr_number}/reviews"

    review_comments = [
        {
            "path": c["file"],
            "line": c["line"],
            "side": "RIGHT",
            "body": _format_comment_body(c),
        }
        for c in comments
    ]

    body = {
        "body": summary,
        "event": "COMMENT",  # never auto-approve or request changes -- a human still decides
        "comments": review_comments,
    }

    resp = requests.post(url, headers=_headers(token), json=body)
    if resp.status_code >= 300:
        print(f"Inline comments rejected by GitHub ({resp.status_code}): {resp.text}")
        fallback = {
            "body": summary + "\n\n_(Inline comments could not be posted for this PR; "
                                "see summary above.)_",
            "event": "COMMENT",
        }
        resp = requests.post(url, headers=_headers(token), json=fallback)
    resp.raise_for_status()
