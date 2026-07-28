#!/usr/bin/env python3
# /// script
# requires-python = ">=3.8"
# ///
"""post-merge capture hook — emits `git.merge` (AD-1a) via the shared emitter.

git passes a single `<squash_flag>` argument ("1" for --squash merges).
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(
    0, str(Path(__file__).resolve().parents[1])
)  # bridge to the shared emitter (spine-sanctioned, Story 2.3)
import _events


def main(argv: list[str] | None = None) -> int:
    args = list(argv) if argv is not None else sys.argv[1:]
    payload = {
        "squash": (args[0] == "1") if args else False,
        "branch": _events.git_out("rev-parse", "--abbrev-ref", "HEAD"),
    }
    return _events.emit("git", "git.merge", payload)


if __name__ == "__main__":
    sys.exit(main())
