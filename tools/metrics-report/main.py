#!/usr/bin/env python3
# /// script
# requires-python = ">=3.8"
# ///
"""metrics-report — renders snapshots/*.json into human-readable
metrics-reports/metrics-<MMDDYYYY>.md files (Story 5.3).

Read-only consumer of the pipeline's canonical snapshots (AD-3) — never writes
to snapshots/ or any manifest/event file. Stories are grouped by the calendar
date portion of engineering_metrics.last_event_at (the day a story's most
recent captured activity happened — i.e., effectively the day it closed),
falling back to pm_metrics.created if a snapshot's event log is empty. One
markdown file is written per represented day, mirroring this repo's own
docs/metrics.md format but populated purely from real snapshot fields.

Only the highest revision per story_id is rendered (AD-3b: latest revision is
current, priors are audit history). Each date's file is fully regenerated
from scratch every run — never appended to — since the JSON snapshots are the
sole source of truth and are immutable; the markdown is a disposable,
always-reproducible rendering, so a plain (non-atomic) write is sufficient
here (unlike .story.yaml/the event log/a snapshot itself, where a partial
write would corrupt kickoff-critical state — that risk doesn't apply to a
file this tool can always regenerate identically on the next run).

Total Defects / Testing Efficiency / Review Efficiency (Story 5.4) render real
values from defect_metrics when present, or "not tracked — <reason>" when zero
defects were ever logged for a story — AD-10: never a fabricated 0/100%, and
never silently omitted.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

sys.path.insert(
    0, str(Path(__file__).resolve().parents[1] / "hooks")
)  # bridge to the shared field-descriptions dict (Story 5.11)
import _field_guide

SNAPSHOTS_DIR = "snapshots"
REPORTS_DIR = "metrics-reports"
FILENAME_RE = re.compile(r"^(?P<story_id>.+)\.v(?P<schema>\d+)\.rev(?P<rev>\d+)\.json$")


def discover_snapshots(root: Path) -> "list[dict[str, Any]]":
    """One snapshot dict per story_id — the highest revision only (AD-3b)."""
    snapshots_dir = root / SNAPSHOTS_DIR
    if not snapshots_dir.is_dir():
        return []
    best: "dict[str, tuple[int, dict]]" = {}
    for path in sorted(snapshots_dir.glob("*.json")):
        match = FILENAME_RE.match(path.name)
        if not match:
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        story_id = match.group("story_id")
        revision = int(match.group("rev"))
        current = best.get(story_id)
        if current is None or revision > current[0]:
            best[story_id] = (revision, data)
    return [data for _, data in best.values()]


def date_key_of(snapshot: dict) -> Optional[str]:
    """YYYY-MM-DD grouping key — last_event_at, falling back to pm_metrics.created.
    A corrupted/hand-edited snapshot could carry a non-string here (review finding,
    PR #27) — isinstance guards against a TypeError from len()/slicing on that."""
    last_at = snapshot.get("engineering_metrics", {}).get("last_event_at")
    created = snapshot.get("pm_metrics", {}).get("created")
    source = last_at or created
    if not isinstance(source, str) or len(source) < 10:
        return None
    date_key = source[:10]
    parts = date_key.split("-")
    if len(parts) != 3 or not all(p.isdigit() for p in parts):
        return None
    return date_key


def mmddyyyy(date_key: str) -> str:
    # date_key_of() already validated this splits into exactly 3 numeric parts
    # (review finding, PR #27) - this unpack is safe given that precondition.
    year, month, day = date_key.split("-")
    return f"{month}{day}{year}"


def group_by_date(snapshots: "list[dict]") -> "dict[str, list[dict]]":
    groups: "dict[str, list[dict]]" = {}
    for snap in snapshots:
        key = date_key_of(snap) or "unknown-date"
        groups.setdefault(key, []).append(snap)
    return groups


def humanize_minutes(minutes: Optional[float]) -> str:
    if minutes is None:
        return "unknown duration"
    total_minutes = round(minutes)
    if total_minutes < 60:
        return f"~{total_minutes} minute{'s' if total_minutes != 1 else ''}"
    hours, mins = divmod(total_minutes, 60)
    if mins == 0:
        return f"~{hours} hour{'s' if hours != 1 else ''}"
    return f"~{hours} hour{'s' if hours != 1 else ''} {mins} minute{'s' if mins != 1 else ''}"


def format_usd(value: Optional[float]) -> str:
    return f"${value:.2f}"


def format_token_usd(value: Optional[float]) -> str:
    """AI token cost is often fractions of a cent - 2 decimals would collapse
    small-but-real values to a misleading $0.00, so this stays at 4."""
    return f"${value:.4f}"


def duration_minutes_of(eng: dict) -> Optional[float]:
    """A fallback used when estimated_cost.duration_minutes is unavailable — e.g.
    a snapshot from before Story 5.2 added that section at all (found via live
    E2E against real snapshots from earlier pilot testing, 2026-07-14)."""
    first_at = eng.get("first_event_at")
    last_at = eng.get("last_event_at")
    if not first_at or not last_at:
        return None
    try:
        return (
            datetime.fromisoformat(last_at) - datetime.fromisoformat(first_at)
        ).total_seconds() / 60
    except (ValueError, TypeError):
        return None


def render_story(snapshot: dict) -> str:
    pm = snapshot.get("pm_metrics", {})
    eng = snapshot.get("engineering_metrics", {})
    spc = snapshot.get("story_point_cost", {})
    tok = snapshot.get("token_cost", {})
    est = snapshot.get("estimated_cost", {})
    defects = snapshot.get("defect_metrics", {})

    title = pm.get("name") or snapshot.get("story_id", "unknown-story")
    date = (pm.get("created") or "")[:10] or "unknown-date"
    duration = humanize_minutes(est.get("duration_minutes") or duration_minutes_of(eng))

    points_line = f"{pm.get('points')} SP" if pm.get("points") is not None else "not set"
    phase_bits = []
    if spc.get("phase1_points") is not None:
        phase_bits.append(f"Phase-1 estimate: {spc['phase1_points']}")
    if spc.get("phase2_points") is not None:
        phase_bits.append(f"Phase-2 actual: {spc['phase2_points']}")
    if spc.get("variance") is not None:
        phase_bits.append(f"variance: {spc['variance']}")
    if phase_bits:
        points_line += " (" + ", ".join(phase_bits) + ")"

    if est.get("usd") is not None:
        estimated_cost_line = format_usd(est["usd"])
    else:
        estimated_cost_line = f"not tracked — {est.get('reason') or 'no reason given'}"

    if tok.get("cost_usd") is not None:
        token_cost_line = (
            f"{format_token_usd(tok['cost_usd'])} "
            f"({tok.get('input_tokens')} in, {tok.get('output_tokens')} out)"
        )
    else:
        token_cost_line = f"not tracked — {tok.get('reason') or 'no reason given'}"

    if defects.get("total_defects") is not None:
        total_defects_line = str(defects["total_defects"])
        testing_efficiency_line = f"{defects['testing_efficiency']:.2f}%"
        review_efficiency_line = f"{defects['review_efficiency']:.2f}%"
    else:
        not_tracked = f"not tracked — {defects.get('reason') or 'no reason given'}"
        total_defects_line = not_tracked
        testing_efficiency_line = not_tracked
        review_efficiency_line = not_tracked

    notes_bits = [
        f"{eng.get('commits', 0)} commits",
        f"{eng.get('ai_sessions', 0)} AI sessions",
        f"{eng.get('tool_uses', 0)} tool uses",
        f"{eng.get('prompts', 0)} prompts",
    ]
    notes = ", ".join(notes_bits)
    if spc.get("reduced_confidence"):
        reasons = spc.get("reduced_confidence_reasons") or []
        if reasons:
            notes += ". Reduced confidence: " + "; ".join(reasons)

    lines = [
        f"## {title}",
        "",
        f"- **Date**: {date}",
        f"- **Goal**: {pm.get('goal') or 'not set'}",
        f"- **Duration**: {duration}",
        f"- **Story Points**: {points_line}",
        f"- **Estimated Cost**: {estimated_cost_line}",
        f"- **AI Token Cost**: {token_cost_line}",
        f"- **Total Defects**: {total_defects_line}",
        f"- **Testing Efficiency**: {testing_efficiency_line}",
        f"- **Review Efficiency**: {review_efficiency_line}",
        f"- **Notes**: {notes}",
    ]
    return "\n".join(lines)


def render_field_guide() -> str:
    """Story 5.11: one "Field Guide" appendix per report, grouped by the same
    top-level section each field belongs to in the snapshot JSON. Group order
    follows FIELD_GUIDE's own definition order (top-level keys first, then one
    section at a time) - not re-sorted, so it reads in the same order fields
    appear in render_story() above."""
    groups: "dict[str, list[tuple[str, str]]]" = {}
    for key, description in _field_guide.FIELD_GUIDE.items():
        section = key.split(".", 1)[0] if "." in key else "top level"
        groups.setdefault(section, []).append((key, description))

    lines = ["## Field Guide", "", "What each field above means and how it's calculated.", ""]
    for section, entries in groups.items():
        lines.append(f"### {section}")
        lines.append("")
        for key, description in entries:
            lines.append(f"- **{key}** — {description}")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def render_report(date_key: str, snapshots: "list[dict]") -> str:
    header = f"# Metrics Report — {date_key}\n\n"
    ordered = sorted(snapshots, key=lambda s: s.get("story_id") or "")
    blocks = [render_story(s) for s in ordered]
    body = header + "\n\n---\n\n".join(blocks) + "\n"
    return body + "\n---\n\n" + render_field_guide()


def main(argv: "list[str] | None" = None) -> int:
    p = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    p.add_argument("--repo-root", required=True, help="repository root")
    args = p.parse_args(argv)

    root = Path(args.repo_root)
    if not root.is_dir():
        print(f"error: --repo-root {args.repo_root!r} is not a directory", file=sys.stderr)
        return 2

    snapshots = discover_snapshots(root)
    groups = group_by_date(snapshots)

    written: "list[str]" = []
    if groups:
        reports_dir = root / REPORTS_DIR
        reports_dir.mkdir(parents=True, exist_ok=True)
        for date_key, group_snapshots in groups.items():
            filename = (
                f"metrics-{mmddyyyy(date_key)}.md"
                if date_key != "unknown-date"
                else "metrics-unknown-date.md"
            )
            target = reports_dir / filename
            with open(target, "w", encoding="utf-8", newline="\n") as f:
                f.write(render_report(date_key, group_snapshots))
            written.append(str(target.resolve()))

    print(json.dumps({"ok": True, "reports_written": written, "stories_rendered": len(snapshots)}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
