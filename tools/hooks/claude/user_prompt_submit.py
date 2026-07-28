#!/usr/bin/env python3
# /// script
# requires-python = ">=3.8"
# ///
"""user_prompt_submit capture hook - emits `ai.claude-code.prompt` (AD-1a/AD-10) via the shared emitter.

Returns 0 unconditionally: a non-zero Claude Code hook exit can block the
tool call or disrupt the session, and metrics capture must never do that
(the commit-msg precedent, extended). AD-9 visibility comes from the
emitter's stderr surfacing.

Privacy guard: only the prompt LENGTH is emitted, never its content.

Also records activity for AD-7's idle-detection (Story 3.2): this is one of
the two signals (with `post_tool_use`) that can reveal - in arrears - that
the active slice has been idle past the threshold."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(
    0, str(Path(__file__).resolve().parents[1])
)  # bridge to the shared emitter (spine-sanctioned, Story 2.3)
import _events


def main(argv: list[str] | None = None) -> int:
    data = _events.read_stdin_json()
    _events.emit(
        "ai",
        "ai.claude-code.prompt",
        {
            "session_id": data.get("session_id"),
            "prompt_chars": len(data["prompt"]) if isinstance(data.get("prompt"), str) else None,
        },
    )
    _events.record_activity(_events.repo_root())
    return 0


if __name__ == "__main__":
    sys.exit(main())
