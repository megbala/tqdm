"""
The actual "agent" logic: take diff data in, get structured review findings out.

Deliberately has zero GitHub API calls in it. That means:
  - You can test it completely locally with a fake diff (see local_test.py).
  - If you later add an eval harness, it can import review_pr() directly and feed it
    synthetic test cases instead of live PR data -- no refactor needed.
"""

import re
from typing import Callable

import anthropic

from prompts import SYSTEM_PROMPT, REVIEW_TOOL, READ_FILE_TOOL, build_diff_context

MODEL = "claude-sonnet-5"
MAX_READ_FILE_CALLS = 4  # cap tool round-trips so a confused model can't loop forever
MAX_READ_FILE_CHARS = 50_000  # cap a single file's content injected into the conversation

# read_file can fetch ANY file in the repo (not just ones in the diff) -- these patterns
# block obviously-sensitive paths as a partial safety net against a malicious diff trying
# to use the review bot as an arbitrary-file-read/exfiltration primitive. This is NOT a
# complete guarantee: a file with a generic name (e.g. config.py) that happens to contain
# a secret isn't caught by a filename-based denylist.
DENIED_PATH_PATTERNS = [
    r"\.env(\..+)?$", r"\.pem$", r"\.key$", r"\.crt$",
    r"secret", r"credential", r"\.git/", r"\.ssh/",
    r"id_rsa", r"id_ed25519", r"\.npmrc$", r"\.netrc$",
]


def review_pr(
    files: list[dict],
    client: anthropic.Anthropic | None = None,
    read_file: Callable[[str], str] | None = None,
) -> dict:
    """
    files: GitHub's "list PR files" API shape -- each item needs at least
           {"filename": str, "status": str, "patch": str}.
    read_file: optional callable(path) -> full file content, letting the model see beyond
               the diff hunk when it asks to. If omitted, the read_file tool isn't offered
               at all and this behaves exactly like a single forced structured-output call.

    Returns a dict shaped like:
        {"summary": str, "comments": [{"file", "line", "severity", "comment", "suggestion"}, ...],
         "usage": {"input_tokens": int, "output_tokens": int, "api_calls": int}}
    """
    client = client or anthropic.Anthropic()  # reads ANTHROPIC_API_KEY from env by default

    zero_usage = {"input_tokens": 0, "output_tokens": 0, "api_calls": 0}
    diff_context = build_diff_context(files)
    if not diff_context:
        return {"summary": "No reviewable text changes found in this PR.", "comments": [], "usage": zero_usage}

    tools = [REVIEW_TOOL] + ([READ_FILE_TOOL] if read_file else [])
    messages = [
        {"role": "user", "content": f"Review this pull request diff:\n\n{diff_context}"},
    ]

    reads_used = 0
    usage = dict(zero_usage)
    while True:
        # Once the read_file budget is spent (or it was never offered), force the final
        # answer so this loop is guaranteed to terminate.
        force_submit = not read_file or reads_used >= MAX_READ_FILE_CALLS

        message = client.messages.create(
            model=MODEL,
            max_tokens=4000,
            system=SYSTEM_PROMPT,
            tools=[REVIEW_TOOL] if force_submit else tools,
            tool_choice={"type": "tool", "name": "submit_review"} if force_submit else {"type": "auto"},
            messages=messages,
        )
        usage["input_tokens"] += message.usage.input_tokens
        usage["output_tokens"] += message.usage.output_tokens
        usage["api_calls"] += 1

        submit = next(
            (b for b in message.content if b.type == "tool_use" and b.name == "submit_review"), None
        )
        if submit:
            result = _validate_result(submit.input)
            result["usage"] = usage
            return result

        reads = [b for b in message.content if b.type == "tool_use" and b.name == "read_file"]
        if not reads:
            # Shouldn't happen with tool_choice forcing a call, but fail soft rather than crash.
            return {"summary": "Review agent did not return structured output.", "comments": [], "usage": usage}

        messages.append({"role": "assistant", "content": message.content})
        messages.append({
            "role": "user",
            "content": [_run_read_file(block, read_file) for block in reads],
        })
        reads_used += len(reads)


def _run_read_file(block, read_file: Callable[[str], str]) -> dict:
    path = block.input.get("path", "")
    if _is_denied_path(path):
        content = f"Error: reading '{path}' is not permitted (looks like a secret/credential path)."
    else:
        try:
            content = read_file(path)
            if len(content) > MAX_READ_FILE_CHARS:
                content = (
                    content[:MAX_READ_FILE_CHARS]
                    + f"\n\n... [truncated: file is {len(content)} chars, showing first {MAX_READ_FILE_CHARS}]"
                )
        except Exception as e:
            content = f"Error reading '{path}': {e}"
    return {"type": "tool_result", "tool_use_id": block.id, "content": content}


def _is_denied_path(path: str) -> bool:
    return any(re.search(p, path, re.IGNORECASE) for p in DENIED_PATH_PATTERNS)


def _validate_result(result: dict) -> dict:
    """
    Forced tool_choice guarantees the model calls submit_review, but not that every
    field inside it actually matches the schema's declared types. Observed failure
    modes so far: `comments` coming back as a malformed string instead of a list, and
    (rarer, seen in longer multi-turn read_file conversations) stray tool-call-like
    tags such as `<parameter name="...">` leaking into `summary` while `comments`
    still happens to be a technically-valid empty list. Neither is fully eliminated by
    prompting alone -- fail soft here rather than let a bad shape crash main.py or
    github_client.py downstream with a confusing traceback.
    """
    summary = result.get("summary", "")
    comments = result.get("comments", [])
    malformed = (
        not isinstance(summary, str)
        or not isinstance(comments, list)
        or not all(isinstance(c, dict) for c in comments)
        or "<parameter" in summary
    )
    if malformed:
        return {
            "summary": "Review agent returned malformed output; comments were dropped.",
            "comments": [],
        }
    return {"summary": summary, "comments": comments}
