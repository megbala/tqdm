"""
Entry point run inside the GitHub Actions job. Reads the PR event, fetches the diff,
runs the review agent, posts the result back to the PR.
"""

import json
import os

from github_client import get_pr_files, post_review, read_file
from review_agent import review_pr


def get_pr_info_from_event() -> tuple[int, str]:
    """Returns (pr_number, head_sha) -- head_sha is needed to read files at the PR's ref."""
    event_path = os.environ["GITHUB_EVENT_PATH"]
    with open(event_path) as f:
        event = json.load(f)
    pr = event["pull_request"]
    return pr["number"], pr["head"]["sha"]


def main() -> None:
    token = os.environ["GITHUB_TOKEN"]
    owner, repo = os.environ["GITHUB_REPOSITORY"].split("/")
    pr_number, head_sha = get_pr_info_from_event()

    print(f"Reviewing {owner}/{repo} PR #{pr_number}...")

    files = get_pr_files(owner, repo, pr_number, token)
    print(f"Found {len(files)} changed file(s).")

    def read_file_at_head(path: str) -> str:
        return read_file(owner, repo, path, head_sha, token)

    result = review_pr(files, read_file=read_file_at_head)
    summary = result.get("summary", "")
    comments = result.get("comments", [])
    usage = result.get("usage", {})
    print(f"Agent produced {len(comments)} inline comment(s).")
    print(
        f"Usage: {usage.get('api_calls', '?')} API call(s), "
        f"{usage.get('input_tokens', '?')} input / {usage.get('output_tokens', '?')} output tokens."
    )

    if not summary and not comments:
        print("Nothing to post.")
        return

    post_review(owner, repo, pr_number, token, summary, comments)
    print("Review posted.")


if __name__ == "__main__":
    main()
