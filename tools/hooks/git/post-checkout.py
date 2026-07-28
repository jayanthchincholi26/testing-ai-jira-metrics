#!/usr/bin/env python3
# /// script
# requires-python = ">=3.8"
# ///
"""post-checkout capture hook — emits `git.checkout` (AD-1a) via the shared emitter.

git passes `<previous_head> <new_head> <flag>`; flag "1" means a branch
checkout, "0" a file checkout. The producer emits unconditionally — filtering
(e.g. AD-7 caring only about branch checkouts) is the consumer's concern.
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
    branch_checkout = (args[2] == "1") if len(args) > 2 else False
    payload = {
        "previous_head": args[0] if len(args) > 0 else None,
        "new_head": args[1] if len(args) > 1 else None,
        "branch_checkout": branch_checkout,
        "branch": _events.git_out("rev-parse", "--abbrev-ref", "HEAD"),
    }
    exit_code = _events.emit("git", "git.checkout", payload)
    if branch_checkout:
        # git has already updated the working tree by the time post-checkout
        # fires, so .story.yaml here already reflects the incoming branch (AD-7).
        root = _events.repo_root()
        incoming = _events.story_id(root)
        if _events.is_session_active(root):
            # AD-7 precedence: a live Claude Code session's SessionStart/SessionEnd
            # boundaries govern time-slice accounting - a mid-session checkout only
            # re-points which story counts, it never opens/closes a slice itself.
            _events.repoint_active_story(root, incoming)
        else:
            _events.update_active_story(root, incoming)
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
