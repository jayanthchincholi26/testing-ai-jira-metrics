#!/usr/bin/env python3
# /// script
# requires-python = ">=3.8"
# ///
"""commit-msg capture hook — emits `git.commit_msg` (AD-1a) via the shared emitter.

git passes the path to the commit-message file; the payload carries the first
non-comment line (the subject).

DELIBERATE TRADE-OFF: this hook returns 0 unconditionally. A non-zero
commit-msg exit ABORTS the developer's commit, and a metrics-capture failure
must never do that (CAP-1: capture is a silent byproduct, never a burden).
AD-9's visibility requirement is still met — a final append failure surfaces
loudly on stderr inside `_events.emit`.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Optional

sys.path.insert(
    0, str(Path(__file__).resolve().parents[1])
)  # bridge to the shared emitter (spine-sanctioned, Story 2.3)
import _events


def subject_of(message_file: str) -> Optional[str]:
    try:
        for line in Path(message_file).read_text(encoding="utf-8", errors="replace").splitlines():
            stripped = line.strip()
            if stripped and not stripped.startswith("#"):
                return stripped
    except OSError:
        pass
    return None


def main(argv: list[str] | None = None) -> int:
    args = list(argv) if argv is not None else sys.argv[1:]
    subject = subject_of(args[0]) if args else None
    _events.emit("git", "git.commit_msg", {"message_subject": subject})
    return 0


if __name__ == "__main__":
    sys.exit(main())
