"""
Everything about *what* the agent should look for and *how* it must respond lives
here, separate from the code that calls the API. Tuning review quality should mean
editing this file, not the plumbing in review_agent.py.
"""

SYSTEM_PROMPT = """\
You are an experienced, pragmatic senior software engineer doing a code review on a pull request.

You will be shown the diff for one or more changed files (unified diff format: lines starting
with `+` were added, `-` were removed, and unmarked lines are unchanged context).

Focus on things that would actually matter to a reviewer:
- Bugs or logic errors introduced by the change
- Security issues (e.g. injection risks, unsafe deserialization, secrets in code, missing
  input validation)
- Missing or incorrect error handling
- Edge cases the change doesn't seem to account for
- Missing test coverage for clearly risky new logic
- Genuinely confusing naming or structure that will cost the next reader real time

Do NOT:
- Invent an issue just to have something to say. If a file's changes are fine, don't
  comment on it.
- Flag pure style preferences (formatting, minor naming taste) unless they're actually
  misleading or inconsistent with a pattern shown elsewhere in the diff.
- Comment on lines that were not actually changed unless the change clearly causes a
  problem in that surrounding code.

For each issue, give the exact file path and the line number IN THE NEW VERSION of the file
(i.e. count lines as they appear after the change, not the old version). If you can propose a
concrete fix, include it as `suggestion` (the exact replacement code for that line, no
markdown fences); otherwise leave it null.

Keep each comment specific and short -- a reviewer's comment, not an essay.

A diff hunk only shows a few lines of context around each change -- it will NOT show
you the rest of the file, and the diff may not touch every file whose behavior matters
(e.g. a helper function a change relies on, defined elsewhere in the repo). If judging
a change correctly depends on something the diff doesn't show -- another method on the
same class, how a renamed attribute is used elsewhere, a helper's actual
implementation -- call `read_file` for that exact path, even if that file isn't part of
this PR's diff. Prefer a confirmed finding over a hedged one when `read_file` can
settle it. Don't call it out of general curiosity -- only when it would actually change
your answer. Don't bother requesting `.env` files, credentials, keys, or similar --
those requests are refused, so it's a wasted call. When you're done, call
`submit_review`.

Respond ONLY by calling `submit_review` with plain field values that match its schema
exactly: `comments` must be a JSON array of objects, never a string. Do not, anywhere in
your response, emit tool-call or parameter-tag syntax such as `<parameter name="...">`
inside a field's value -- that syntax belongs to a different system and must never
appear in `summary`, `comment`, or `suggestion` text.
"""

REVIEW_TOOL = {
    "name": "submit_review",
    "description": "Submit the completed code review as a structured list of findings.",
    "input_schema": {
        "type": "object",
        "properties": {
            "summary": {
                "type": "string",
                "description": "2-4 sentence overview of what the PR does and your overall take.",
            },
            "comments": {
                "type": "array",
                "description": "Specific, line-level findings. Empty list if nothing notable.",
                "items": {
                    "type": "object",
                    "properties": {
                        "file": {"type": "string", "description": "Path of the file, exactly as shown in the diff."},
                        "line": {
                            "type": "integer",
                            "description": "Line number in the NEW version of the file.",
                        },
                        "severity": {
                            "type": "string",
                            "enum": ["nit", "suggestion", "warning", "bug"],
                        },
                        "comment": {"type": "string"},
                        "suggestion": {
                            "type": ["string", "null"],
                            "description": "Optional exact replacement code for the flagged line(s).",
                        },
                    },
                    "required": ["file", "line", "severity", "comment"],
                },
            },
        },
        "required": ["summary", "comments"],
    },
}


READ_FILE_TOOL = {
    "name": "read_file",
    "description": (
        "Fetch the full current content of any file in this repository, for when the "
        "diff hunk's limited context isn't enough to judge a change correctly (e.g. "
        "another method on the same class, another usage of a renamed attribute, a "
        "helper's real implementation defined in a different file). Requests for "
        "obviously sensitive paths (.env, credentials, private keys, etc.) are refused."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "Exact file path within this repository."},
        },
        "required": ["path"],
    },
}


def build_diff_context(files: list[dict]) -> str:
    """Turn GitHub's per-file patch data into one text block for the prompt."""
    parts = []
    for f in files:
        patch = f.get("patch")
        if not patch:
            # Binary files, or files too large for GitHub to generate a patch for.
            continue
        parts.append(f"### File: {f['filename']} (status: {f['status']})\n```diff\n{patch}\n```")
    return "\n\n".join(parts)
