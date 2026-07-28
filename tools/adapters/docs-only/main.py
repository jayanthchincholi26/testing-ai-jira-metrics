#!/usr/bin/env python3
# /// script
# requires-python = ">=3.8"
# ///
"""docs-only kickoff adapter — writes the story manifest `.story.yaml`.

The docs-only backend of the AD-4 source-of-truth adapter contract
({points, goal, sprint, description}): values come from the developer's
confirmed answers (elicited by the story-kickoff skill) instead of a PM-tool
API. It doubles as the common manifest writer for the fetch-only adapters
(jira/confluence pass confirmed values here with --source-of-truth, so the
manifest records which backend supplied them). The manifest this writes is
the sole source of story identity (AD-5) —
producers read `story_id` from it and never infer identity from a branch name
or ticket key. An existing manifest is never overwritten, since a re-kickoff
would change story identity mid-story.

story_id format: `story-{YYYYMMDD}-{6 hex of uuid4}` — unique and date-sortable
with no PII; the format is a Story 1.1 decision (the architecture spine leaves
it open).

AD-6a (Story 2.6): `points_estimated` (the raw AD-6 Phase-1 estimate) is
always a distinct field from `points` (the developer-confirmed value) — never
substituted, never merged. `points_estimated` is null when no Phase-1
estimate was available at kickoff.

Story 1.7: `--sprint` is required for `jira`/`confluence` (unchanged), but
optional for `docs-only` — an ad hoc team with no sprint/milestone concept
gets `sprint: null` rather than being forced to invent one. The manifest's
`sprint` field stays named `sprint` regardless of backend (AD-4 shape
unchanged); only its requiredness is backend-conditional.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any

MANIFEST = ".story.yaml"


def new_story_id(now: datetime) -> str:
    return f"story-{now:%Y%m%d}-{uuid.uuid4().hex[:6]}"


def clean(text: str) -> str:
    """Collapse newlines/whitespace runs so a value is always one manifest line."""
    return " ".join(text.split())


def render(manifest: dict[str, Any]) -> str:
    """Flat YAML by hand (stdlib-only rule): JSON scalars are valid YAML scalars."""
    return "".join(
        f"{key}: {'null' if value is None else json.dumps(value)}\n"
        for key, value in manifest.items()
    )


def write_atomic(path: Path, text: str) -> None:
    """Temp + flush + fsync + atomic rename, so a crash never half-writes the manifest."""
    tmp = path.with_suffix(path.suffix + ".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(text)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


def fail(message: str) -> int:
    print(f"error: {message}", file=sys.stderr)
    return 2


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    p.add_argument(
        "--repo-root",
        required=True,
        help="repository root; the manifest is {repo-root}/.story.yaml",
    )
    p.add_argument(
        "--points",
        required=True,
        help="confirmed story points (number > 0; fractional allowed for teams that use them)",
    )
    p.add_argument(
        "--points-estimated",
        help="the raw AD-6 Phase-1 estimate (Story 2.5), distinct from --points and never "
        "substituted for it (AD-6a); omitted/null when no estimate was available",
    )
    p.add_argument(
        "--name",
        help="short human-readable story name (e.g. 'Auth Module Implementation'); "
        "docs-only only (Story 1.7) — null when omitted",
    )
    p.add_argument("--goal", required=True, help="story goal (one line)")
    p.add_argument(
        "--sprint",
        help="sprint the story belongs to; required for jira/confluence, optional for "
        "docs-only (an omitted/blank value writes sprint: null for ad hoc teams)",
    )
    p.add_argument(
        "--sprint-start-date",
        help="the sprint's start date (Story 6.5), if the JIRA fetch's chosen sprint item "
        "carried one; null when omitted (a future sprint has no dates yet, docs-only/"
        "confluence stories have no sprint object at all)",
    )
    p.add_argument(
        "--sprint-end-date",
        help="the sprint's end date (Story 6.5), same conditions as --sprint-start-date",
    )
    p.add_argument("--description", help="optional longer description")
    p.add_argument(
        "--jira-issue-key",
        help="the parent Jira issue key (e.g. 'AI-139'), Story 5.4 - persisted so a later "
        "defect-logging step can attach a Jira subtask to the right parent; null when omitted "
        "(confluence/docs-only stories have no Jira parent)",
    )
    p.add_argument(
        "--source-of-truth",
        choices=("jira", "confluence", "docs-only"),
        default="docs-only",
        help="which backend supplied the values (default: docs-only)",
    )
    p.add_argument(
        "--ai-tool",
        default="claude-code",
        help="AI tool whose adapter captures this story's sessions (default: claude-code; "
        "lowercase token — it becomes the ai.<tool>.* event namespace)",
    )
    args = p.parse_args(argv)

    if not re.fullmatch(r"[a-z][a-z0-9-]*", args.ai_tool):
        return fail(f"--ai-tool {args.ai_tool!r} must match [a-z][a-z0-9-]*")

    try:
        points = float(args.points)
    except ValueError:
        return fail(f"--points must be a number, got {args.points!r}")
    if not 0 < points < float("inf"):  # also rejects nan: every nan comparison is false
        return fail(f"--points must be a finite number > 0, got {args.points}")
    if points.is_integer():
        points = int(points)

    points_estimated: Any = None
    if args.points_estimated is not None:
        try:
            points_estimated = float(args.points_estimated)
        except ValueError:
            return fail(f"--points-estimated must be a number, got {args.points_estimated!r}")
        if points_estimated.is_integer():
            points_estimated = int(points_estimated)

    name = clean(args.name) if args.name else ""

    goal = clean(args.goal)
    if not goal:
        return fail("--goal must not be empty")
    sprint = clean(args.sprint) if args.sprint else ""
    if not sprint:
        if args.source_of_truth in ("jira", "confluence"):
            return fail("--sprint must not be empty")
        sprint = None  # docs-only: no sprint/milestone concept is a valid answer
    description = clean(args.description) if args.description else ""
    jira_issue_key = clean(args.jira_issue_key) if args.jira_issue_key else ""
    sprint_start_date = clean(args.sprint_start_date) if args.sprint_start_date else ""
    sprint_end_date = clean(args.sprint_end_date) if args.sprint_end_date else ""

    root = Path(args.repo_root)
    if not root.is_dir():
        return fail(f"--repo-root {args.repo_root!r} is not a directory")
    path = root / MANIFEST
    if path.exists():
        return fail(f"{path} already exists; re-running kickoff would change story identity (AD-5)")

    now = datetime.now().astimezone()
    story_id = new_story_id(now)
    write_atomic(
        path,
        render(
            {
                "story_id": story_id,
                "name": name or None,
                "source_of_truth": args.source_of_truth,
                "jira_issue_key": jira_issue_key or None,
                "ai_tool": args.ai_tool,
                "points": points,
                "points_estimated": points_estimated,
                "goal": goal,
                "sprint": sprint,
                "sprint_start_date": sprint_start_date or None,
                "sprint_end_date": sprint_end_date or None,
                "description": description or None,
                "created": now.isoformat(timespec="seconds"),
            }
        ),
    )
    print(json.dumps({"ok": True, "story_yaml": str(path.resolve()), "story_id": story_id}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
