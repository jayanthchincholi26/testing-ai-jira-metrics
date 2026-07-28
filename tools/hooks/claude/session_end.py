#!/usr/bin/env python3
# /// script
# requires-python = ">=3.8"
# ///
"""session_end capture hook - emits `ai.claude-code.session_end` (AD-1a/AD-10) via the shared emitter.

Returns 0 unconditionally: a non-zero Claude Code hook exit can block the
tool call or disrupt the session, and metrics capture must never do that
(the commit-msg precedent, extended). AD-9 visibility comes from the
emitter's stderr surfacing.

Story 5.2: real per-session token usage, read from the session's own
transcript (Claude Code hooks don't report token usage directly, but every
hook input carries `transcript_path`, pointing at a local JSONL transcript
whose assistant-turn lines each carry a real `message.usage.input_tokens`/
`output_tokens`). Any failure reading/parsing that file (missing path,
missing file, malformed JSON, no assistant/usage lines) degrades to
null-with-reason - a real null a dashboard must never read as zero.

AD-7: a session-level slice only closes on SessionEnd (Story 3.3) - closes
whatever story the active-story pointer currently names via
close_active_story_slice(), then clears the live-session marker."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Optional

sys.path.insert(
    0, str(Path(__file__).resolve().parents[1])
)  # bridge to the shared emitter (spine-sanctioned, Story 2.3)
import _events


def token_usage_from_transcript(
    transcript_path: Optional[str],
) -> "tuple[Optional[int], Optional[int], Optional[str]]":
    """(input_tokens, output_tokens, reason) - reason is set only when both token
    counts are None. Never raises: any I/O/parse failure degrades to null-with-reason."""
    if not transcript_path:
        return None, None, "no transcript_path in hook payload"
    path = Path(transcript_path)

    input_total = 0
    output_total = 0
    found_usage = False
    try:
        with open(path, encoding="utf-8") as f:
            for line in f:
                # streamed line-by-line rather than read_text().splitlines() - a
                # long-running session's transcript can be several MB (review
                # finding, PR #26); this keeps peak memory O(1) instead of O(file size)
                stripped = line.strip()
                if not stripped:
                    continue
                try:
                    entry = json.loads(stripped)
                except json.JSONDecodeError:
                    continue
                if not isinstance(entry, dict) or entry.get("type") != "assistant":
                    continue
                usage = entry.get("message", {}).get("usage")
                if not isinstance(usage, dict):
                    continue
                found_usage = True
                input_total += usage.get("input_tokens") or 0
                output_total += usage.get("output_tokens") or 0
    except OSError:
        return None, None, f"transcript file not found or unreadable at {transcript_path}"

    if not found_usage:
        return None, None, "no assistant usage data found in transcript"
    return input_total, output_total, None


def main(argv: list[str] | None = None) -> int:
    data = _events.read_stdin_json()
    input_tokens, output_tokens, reason = token_usage_from_transcript(data.get("transcript_path"))
    _events.emit(
        "ai",
        "ai.claude-code.session_end",
        {
            "session_id": data.get("session_id"),
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "token_cost_reason": reason,
        },
    )
    root = _events.repo_root()
    _events.close_active_story_slice(root)
    _events.mark_session_inactive(root)
    return 0


if __name__ == "__main__":
    sys.exit(main())
