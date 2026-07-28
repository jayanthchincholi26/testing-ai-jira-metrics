#!/usr/bin/env python3
# /// script
# requires-python = ">=3.8"
# ///
"""post-commit capture hook — emits `git.commit` (AD-1a) via the shared emitter.

Payload fields degrade to honest nulls when git can't answer; the event is
emitted regardless. Exit 1 on final append failure is harmless (git ignores
post-hook exit codes) but honest.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(
    0, str(Path(__file__).resolve().parents[1])
)  # bridge to the shared emitter (spine-sanctioned, Story 2.3)
import _events


def main(argv: list[str] | None = None) -> int:
    payload = {
        "hash": _events.git_out("rev-parse", "HEAD"),
        "branch": _events.git_out("rev-parse", "--abbrev-ref", "HEAD"),
        "message_subject": _events.git_out("log", "-1", "--format=%s"),
    }
    return _events.emit("git", "git.commit", payload)


if __name__ == "__main__":
    sys.exit(main())
