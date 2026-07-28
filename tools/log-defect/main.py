#!/usr/bin/env python3
# /// script
# requires-python = ">=3.8"
# ///
"""log-defect — the local ledger writer for review defects (Story 5.4).

Appends an `ai.claude-code.defect_review` event to `.story-events.jsonl`
(or the pending spool, AD-1b, when `.story.yaml` doesn't exist yet).

This script never calls any MCP tool itself — MCP tools (e.g. creating a
real Jira subtask) are only reachable from a live Claude Code assistant
turn, never from a subprocess like this one (see Story 5.4's Dev Notes).
The convention: the assistant creates the Jira subtask first (only when
`source_of_truth: jira` and `.story.yaml`'s `jira_issue_key` is set), then
invokes this script with the resulting key via --jira-subtask-key so the
local event records it too. Compile/test defects are captured automatically
by the extended `PostToolUse` hook — never logged via this script.

Same AD-9 append-with-retry contract as every other producer (mirrors
tools/hooks/_events.py's emit(), parameterized on an explicit --repo-root
rather than a cwd-derived one, since this is a standalone CLI tool, not a
hook)."""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Any

sys.path.insert(
    0, str(Path(__file__).resolve().parents[1] / "hooks")
)  # bridge to the shared emitter helpers (same pattern as snapshot-assembler)
import _events

EVENT_TYPE = "ai.claude-code.defect_review"
ATTEMPTS = 4  # 1 initial + 3 retries (AD-9), same cadence as _events.emit()


def fail(message: str) -> int:
    print(f"error: {message}", file=sys.stderr)
    return 2


def append_with_retry(target: Path, line: str) -> bool:
    last_error: "BaseException | None" = None
    for attempt in range(ATTEMPTS):
        try:
            _events.append_line(target, line)
            return True
        except OSError as exc:
            last_error = exc
            if attempt < ATTEMPTS - 1:
                time.sleep(_events.RETRY_DELAY_SECONDS)
    print(f"METRICS CAPTURE FAILED: {last_error} — event lost: {EVENT_TYPE}", file=sys.stderr)
    return False


def main(argv: "list[str] | None" = None) -> int:
    p = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    p.add_argument("--repo-root", required=True, help="repository root")
    p.add_argument(
        "--type",
        required=True,
        choices=("review",),
        help="defect type — only 'review' today; compile/test defects are captured "
        "automatically by the PostToolUse hook, never logged via this script",
    )
    p.add_argument("--summary", required=True, help="one-line defect summary")
    p.add_argument("--description", required=True, help="defect description")
    p.add_argument(
        "--points",
        default="1",
        help="story points for this defect (default: 1, matching the reference tool's "
        "bug-subtask default)",
    )
    p.add_argument(
        "--jira-subtask-key",
        help="the Jira subtask key already created via MCP, if any — this script never "
        "creates one itself (MCP tools aren't reachable from a subprocess)",
    )
    args = p.parse_args(argv)

    summary = " ".join(args.summary.split())
    if not summary:
        return fail("--summary must not be empty")
    description = " ".join(args.description.split())
    if not description:
        return fail("--description must not be empty")

    try:
        points: Any = float(args.points)
    except ValueError:
        return fail(f"--points must be a number, got {args.points!r}")
    if not 0 < points < float("inf"):
        return fail(f"--points must be a finite number > 0, got {args.points}")
    if points.is_integer():
        points = int(points)

    root = Path(args.repo_root)
    if not root.is_dir():
        return fail(f"--repo-root {args.repo_root!r} is not a directory")

    story = _events.story_id(root)
    target = root / (_events.EVENTS_FILE if story else _events.PENDING_FILE)
    jira_subtask_key = args.jira_subtask_key.strip() if args.jira_subtask_key else None
    payload = {
        "summary": summary,
        "description": description,
        "points": points,
        "jira_subtask_key": jira_subtask_key,
    }
    line = json.dumps(_events.envelope(story, "ai", EVENT_TYPE, payload)) + "\n"

    if not append_with_retry(target, line):
        return 1

    print(json.dumps({"ok": True, "story_id": story, "jira_subtask_key": jira_subtask_key}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
