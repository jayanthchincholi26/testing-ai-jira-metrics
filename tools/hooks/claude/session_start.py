#!/usr/bin/env python3
# /// script
# requires-python = ">=3.8"
# ///
"""session_start capture hook - emits `ai.claude-code.session_start` (AD-1a/AD-10) via the shared emitter.

Returns 0 unconditionally: a non-zero Claude Code hook exit can block the
tool call or disrupt the session, and metrics capture must never do that
(the commit-msg precedent, extended). AD-9 visibility comes from the
emitter's stderr surfacing.

AD-7: marks the session live (Story 3.3) so a mid-session `git checkout`
knows to re-point the active story rather than open/close a slice, and opens
the slice itself via update_active_story() (Story 3.1)."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(
    0, str(Path(__file__).resolve().parents[1])
)  # bridge to the shared emitter (spine-sanctioned, Story 2.3)
import _events


def main(argv: list[str] | None = None) -> int:
    data = _events.read_stdin_json()
    session_id = data.get("session_id")
    _events.emit("ai", "ai.claude-code.session_start", {"session_id": session_id})
    root = _events.repo_root()
    _events.mark_session_active(root, session_id)
    _events.update_active_story(root, _events.story_id(root))
    return 0


if __name__ == "__main__":
    sys.exit(main())
