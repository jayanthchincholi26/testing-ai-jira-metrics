#!/usr/bin/env python3
# /// script
# requires-python = ">=3.8"
# ///
"""stop capture hook - emits `ai.claude-code.stop` (AD-1a/AD-10) via the shared emitter.

Returns 0 unconditionally: a non-zero Claude Code hook exit can block the
tool call or disrupt the session, and metrics capture must never do that
(the commit-msg precedent, extended). AD-9 visibility comes from the
emitter's stderr surfacing."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(
    0, str(Path(__file__).resolve().parents[1])
)  # bridge to the shared emitter (spine-sanctioned, Story 2.3)
import _events


def main(argv: list[str] | None = None) -> int:
    data = _events.read_stdin_json()
    _events.emit("ai", "ai.claude-code.stop", {"session_id": data.get("session_id")})
    return 0


if __name__ == "__main__":
    sys.exit(main())
