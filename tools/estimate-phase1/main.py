#!/usr/bin/env python3
# /// script
# requires-python = ">=3.8"
# ///
"""AD-6 Phase-1 story-point estimator — read-only, side-effect-free.

Computes a *suggested* points value from openspec state alone, for the
kickoff skill to present to the developer (who always confirms or overrides
it — CAP-1's human-confirmation guarantee is untouched by this script). This
estimator never writes `.story.yaml`, the event log, or any snapshot, and its
output never gates, skips, or shortens any capture behavior (FR5) — a null
estimate or a `must_split` flag are informational only.

Formula (base + volatility) * novelty, per AD-6:

- Base points from `tasks.md` checkbox count (`- [ ]` / `- [x]`, any state —
  total scope, not remaining work): 1-5 -> 2, 6-15 -> 5, 16-30 -> 13, 31+ -> 20
  (with must_split=True, informational only).
- Volatility bonus from how many of {proposal.md, design.md, specs/} exist
  under the change dir. AD-6 names only the two endpoints (explore-only = +5,
  proposal+specs+design = 0); the linear fill between them
  (bonus = round(5 * (3 - present_count) / 3)) is this script's own
  documented choice, not copied from the architecture doc.
- Novelty modifier: `.story.yaml` itself never persists across stories (AD-5
  refuses to overwrite; nothing yet clears it after archive), so "prior
  .story.yaml records" is reinterpreted here as the pm_metrics of every
  committed snapshots/*.json (Story 2.4) instead: no prior snapshots -> 1.5
  (first-time); prior snapshots exist but none share this kickoff's
  source_of_truth -> 1.0 (standard); a prior snapshot shares both
  source_of_truth and this estimate's base-points bucket -> 0.8
  (existing-pattern-reuse).

Change discovery: scans {repo-root}/openspec/changes/*/ directly (no
`openspec` CLI invocation - NFR2 local-first). Zero or ambiguous artifacts
degrade to a null-with-reason estimate rather than guessing; two or more
candidate change directories without an explicit --change-dir is refused
(exit 2) rather than silently picking one.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Optional

ARTIFACTS = ("proposal.md", "design.md", "specs")


def count_tasks(tasks_md: Path) -> int:
    count = 0
    for line in tasks_md.read_text(encoding="utf-8", errors="replace").splitlines():
        stripped = line.strip()
        if stripped.startswith("- [ ]") or stripped.startswith("- [x]"):
            count += 1
    return count


def base_points_for(task_count: int) -> "tuple[int, bool]":
    if task_count <= 5:
        return 2, False
    if task_count <= 15:
        return 5, False
    if task_count <= 30:
        return 13, False
    return 20, True


def volatility_bonus_for(change_dir: Path) -> int:
    present = 0
    for name in ARTIFACTS:
        path = change_dir / name
        if name == "specs":
            if path.is_dir() and any(path.iterdir()):
                present += 1
        elif path.is_file() and path.stat().st_size > 0:
            present += 1
    return round(5 * (3 - present) / 3)


def load_prior_snapshots(root: Path) -> "list[dict[str, Any]]":
    snapshots_dir = root / "snapshots"
    if not snapshots_dir.is_dir():
        return []
    records = []
    for path in snapshots_dir.glob("*.json"):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if isinstance(data, dict) and isinstance(data.get("pm_metrics"), dict):
            records.append(data["pm_metrics"])
    return records


def novelty_modifier_for(
    prior_pm_metrics: "list[dict[str, Any]]", source_of_truth: Optional[str], base_points: int
) -> float:
    """`base_points` is always one of the bucket values {2, 5, 13, 20} (never a task
    count), so a prior story's recorded `points` is compared directly against it —
    there is nothing to "re-derive," a prior task count was never stored."""
    if not prior_pm_metrics:
        return 1.5
    same_source = [pm for pm in prior_pm_metrics if pm.get("source_of_truth") == source_of_truth]
    if not same_source:
        return 1.0
    for pm in same_source:
        if pm.get("points") == base_points:
            return 0.8
    return 1.0


def find_change_dir(root: Path, override: Optional[str]) -> "tuple[Optional[Path], Optional[str]]":
    """Return (change_dir, error_message). error_message set means: exit 2."""
    if override is not None:
        return Path(override), None
    changes_root = root / "openspec" / "changes"
    if not changes_root.is_dir():
        return None, None
    candidates = sorted(p for p in changes_root.iterdir() if p.is_dir())
    if not candidates:
        return None, None
    if len(candidates) == 1:
        return candidates[0], None
    names = ", ".join(p.name for p in candidates)
    return None, f"multiple openspec changes found ({names}) — pass --change-dir to disambiguate"


def fail(message: str) -> int:
    print(f"error: {message}", file=sys.stderr)
    return 2


def degraded_ack(reason: str) -> dict[str, Any]:
    return {
        "ok": True,
        "phase1_points": None,
        "phase1_points_reason": reason,
        "task_count": None,
        "base_points": None,
        "volatility_bonus": None,
        "novelty_modifier": None,
        "must_split": False,
        "change_dir": None,
    }


def main(argv: "list[str] | None" = None) -> int:
    p = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    p.add_argument("--repo-root", required=True, help="repository root")
    p.add_argument(
        "--change-dir", help="explicit openspec change directory (overrides auto-detection)"
    )
    p.add_argument(
        "--source-of-truth",
        default="docs-only",
        help="this kickoff's source_of_truth, for the novelty modifier (default: docs-only)",
    )
    args = p.parse_args(argv)

    root = Path(args.repo_root)
    if not root.is_dir():
        return fail(f"--repo-root {args.repo_root!r} is not a directory")

    change_dir, error = find_change_dir(root, args.change_dir)
    if error:
        return fail(error)
    if change_dir is None:
        print(json.dumps(degraded_ack("no openspec change found — task count unknowable")))
        return 0
    tasks_md = change_dir / "tasks.md"
    if not tasks_md.is_file():
        print(json.dumps(degraded_ack(f"no tasks.md under {change_dir} — task count unknowable")))
        return 0

    task_count = count_tasks(tasks_md)
    base_points, must_split = base_points_for(task_count)
    volatility_bonus = volatility_bonus_for(change_dir)
    prior = load_prior_snapshots(root)
    novelty_modifier = novelty_modifier_for(prior, args.source_of_truth, base_points)
    phase1_points = round((base_points + volatility_bonus) * novelty_modifier)

    print(
        json.dumps(
            {
                "ok": True,
                "phase1_points": phase1_points,
                "phase1_points_reason": None,
                "task_count": task_count,
                "base_points": base_points,
                "volatility_bonus": volatility_bonus,
                "novelty_modifier": novelty_modifier,
                "must_split": must_split,
                "change_dir": str(change_dir.resolve()),
            }
        )
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
