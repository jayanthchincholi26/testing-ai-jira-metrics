#!/usr/bin/env python3
# /// script
# requires-python = ">=3.8"
# ///
"""dashboard — renders snapshots/*.json into a single self-contained
metrics-reports/dashboard.html leadership summary (Story 5.5).

Read-only, same as tools/metrics-report/main.py — never writes to snapshots/
or any manifest/event file, and this tool adds no publishing/hosting/upload
mechanism of any kind: the file is written locally only, and the developer
decides how/whether to share it (potentially sensitive leadership/cost data).

Unlike Story 5.3's per-day reports, this dashboard aggregates every story
into ONE flat table (sorted by date, most recent first) with a few headline
stat figures above it. Snapshot discovery and highest-revision selection
(AD-3b) are reused directly from tools/metrics-report/main.py's
discover_snapshots() via bridge-import — not re-implemented here.

No chart of any kind: the user's own request was "an HTML table format," and
a table is the right form for "scan every story's cost/points," per this
project's dataviz guidance on choosing a form. A stat-tile row for the
headline totals is explicitly sanctioned as "not a chart."

Fully self-contained: all CSS inlined, no CDN links, no external fonts or
network calls of any kind — openable by double-clicking, no server needed.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "metrics-report"))
import main as metrics_report  # noqa: E402 (path must be set up first) - discover_snapshots() reuse

sys.path.insert(
    0, str(Path(__file__).resolve().parents[1] / "hooks")
)  # bridge to the shared field-descriptions dict (Story 5.11)
import _field_guide  # noqa: E402 (path must be set up first)

REPORTS_DIR = "metrics-reports"
OUTPUT_FILE = "dashboard.html"

# Story 5.11: which FIELD_GUIDE entry best explains each table column / stat tile.
COLUMN_FIELD_GUIDE_KEYS = {
    "Story": "pm_metrics.name",
    "Date": "engineering_metrics.last_event_at",
    "Points": "pm_metrics.points",
    "Duration": "estimated_cost.duration_minutes",
    "Estimated Cost": "estimated_cost.usd",
    "AI Token Cost": "token_cost.cost_usd",
    "Defects": "defect_metrics.total_defects",
}
TILE_FIELD_GUIDE_KEYS = {
    "Total Story Points": "pm_metrics.points",
    "Total Estimated Cost": "estimated_cost.usd",
    "Total AI Token Cost": "token_cost.cost_usd",
}
# Story 6.6: the sprint-rollup table's own column tooltips. Story Count/Overall
# Status have no snapshot-envelope analog (both are computed only at render
# time) - see the dashboard.sprint_rollup.* entries in _field_guide.py.
SPRINT_COLUMN_FIELD_GUIDE_KEYS = {
    "Sprint": "pm_metrics.sprint",
    "Start Date": "pm_metrics.sprint_start_date",
    "End Date": "pm_metrics.sprint_end_date",
    "Story Count": "dashboard.sprint_rollup.story_count",
    "Overall Status": "dashboard.sprint_rollup.status",
}


def format_usd(value: float) -> str:
    return f"${value:.2f}"


def format_token_usd(value: float) -> str:
    """AI token cost is often fractions of a cent - 2 decimals would collapse
    small-but-real values to a misleading $0.00, so this stays at 4."""
    return f"${value:.4f}"


def aggregate_stats(snapshots: "list[dict]") -> "dict[str, Any]":
    total_stories = len(snapshots)

    def known_sum(getter) -> "tuple[Optional[float], int]":
        known = [getter(s) for s in snapshots if isinstance(getter(s), (int, float))]
        return (sum(known) if known else None), len(known)

    # (s.get("key") or {}) not s.get("key", {}) - a corrupted/hand-edited snapshot
    # could have the key present but explicitly null, and dict.get()'s default only
    # covers an absent key, not a present-but-None value (review finding, PR #28)
    points_sum, points_known = known_sum(lambda s: (s.get("pm_metrics") or {}).get("points"))
    cost_sum, cost_known = known_sum(lambda s: (s.get("estimated_cost") or {}).get("usd"))
    token_sum, token_known = known_sum(lambda s: (s.get("token_cost") or {}).get("cost_usd"))

    return {
        "total_stories": total_stories,
        "points_sum": points_sum,
        "points_known": points_known,
        "cost_sum": cost_sum,
        "cost_known": cost_known,
        "token_sum": token_sum,
        "token_known": token_known,
    }


def stat_value(total_stories: int, value_sum: Optional[float], known: int, fmt) -> str:
    """A stat tile's value text, honest about how many stories fed the sum
    (AD-10: never a fabricated total, always visible how much is excluded)."""
    if value_sum is None:
        return "not tracked"
    text = fmt(value_sum)
    if known < total_stories:
        text += f" ({known} of {total_stories} stories — {total_stories - known} not tracked)"
    return text


def render_stat_tiles(stats: "dict[str, Any]") -> str:
    total = stats["total_stories"]
    tiles = [
        ("Total Stories", str(total)),
        ("Total Story Points", stat_value(total, stats["points_sum"], stats["points_known"], str)),
        (
            "Total Estimated Cost",
            stat_value(total, stats["cost_sum"], stats["cost_known"], format_usd),
        ),
        (
            "Total AI Token Cost",
            stat_value(total, stats["token_sum"], stats["token_known"], format_token_usd),
        ),
    ]

    def tile_title(label: str) -> str:
        key = TILE_FIELD_GUIDE_KEYS.get(label)
        if key is None:
            return "Count of every story represented in this dashboard (highest revision per story only, AD-3b)."
        return (
            _field_guide.FIELD_GUIDE[key]
            + " This tile sums the value across every story with a known figure."
        )

    cards = "\n".join(
        f'<div class="tile" title="{tile_title(label)}"><div class="tile-label">{label}</div>'
        f'<div class="tile-value">{value}</div></div>'
        for label, value in tiles
    )
    return f'<div class="tiles">{cards}</div>'


def render_row(snapshot: dict) -> str:
    # (s.get("key") or {}) - same present-but-null guard as aggregate_stats() above
    pm = snapshot.get("pm_metrics") or {}
    eng = snapshot.get("engineering_metrics") or {}
    est = snapshot.get("estimated_cost") or {}
    tok = snapshot.get("token_cost") or {}
    defects = snapshot.get("defect_metrics") or {}

    name = pm.get("name") or snapshot.get("story_id", "unknown-story")
    date = (eng.get("last_event_at") or pm.get("created") or "")[:10] or "unknown"
    points = pm.get("points") if pm.get("points") is not None else "not set"
    duration = metrics_report.humanize_minutes(
        est.get("duration_minutes") or metrics_report.duration_minutes_of(eng)
    )
    estimated_cost = (
        format_usd(est["usd"])
        if est.get("usd") is not None
        else f"not tracked — {est.get('reason') or 'no reason given'}"
    )
    token_cost = (
        format_token_usd(tok["cost_usd"])
        if tok.get("cost_usd") is not None
        else f"not tracked — {tok.get('reason') or 'no reason given'}"
    )
    defects_cell = (
        f"{defects['total_defects']} total "
        f"(testing {defects['testing_efficiency']:.2f}% / review {defects['review_efficiency']:.2f}%)"
        if defects.get("total_defects") is not None
        else f"not tracked — {defects.get('reason') or 'no reason given'}"
    )

    cells = [name, date, str(points), duration, estimated_cost, token_cost, defects_cell]
    return "<tr>" + "".join(f"<td>{cell}</td>" for cell in cells) + "</tr>"


def render_table(snapshots: "list[dict]") -> str:
    ordered = sorted(
        snapshots,
        key=lambda s: (
            (s.get("engineering_metrics") or {}).get("last_event_at")
            or (s.get("pm_metrics") or {}).get("created")
            or ""
        ),
        reverse=True,
    )

    def th(label: str) -> str:
        description = _field_guide.FIELD_GUIDE.get(COLUMN_FIELD_GUIDE_KEYS.get(label), "")
        title_attr = f' title="{description}"' if description else ""
        return f"<th{title_attr}>{label}</th>"

    header = (
        "<tr>"
        + "".join(
            th(label)
            for label in [
                "Story",
                "Date",
                "Points",
                "Duration",
                "Estimated Cost",
                "AI Token Cost",
                "Defects",
            ]
        )
        + "</tr>"
    )
    rows = "\n".join(render_row(s) for s in ordered)
    return f"<table>\n<thead>{header}</thead>\n<tbody>\n{rows}\n</tbody>\n</table>"


def _parse_iso(value: Any) -> Optional[datetime]:
    """JIRA's real sprint dates carry a trailing Z + milliseconds (e.g.
    "2026-06-26T06:19:49.000Z", confirmed live during Story 6.5's research) -
    datetime.fromisoformat() only accepts a bare "Z" from Python 3.11 onward,
    and this project's tool scripts declare requires-python >=3.8. Never
    raises: a corrupted/hand-edited snapshot must degrade, not crash."""
    if not isinstance(value, str) or not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def sprint_status(end_date: Any) -> str:
    """Describes the SPRINT's own timeline, not story completion - every
    snapshot already represents a closed story (AD-3), so "done vs. still
    open" can never be computed honestly from this pipeline's own data."""
    parsed = _parse_iso(end_date)
    if parsed is None:
        return "Unknown"
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return "Ended" if parsed < datetime.now(timezone.utc) else "Active or upcoming"


def group_by_sprint(snapshots: "list[dict]") -> "tuple[dict[str, list[dict]], int]":
    """Sprint name -> its snapshots, plus a count of stories with no sprint at
    all (grouped separately - AD-10, never silently dropped)."""
    groups: "dict[str, list[dict]]" = {}
    no_sprint_count = 0
    for snap in snapshots:
        sprint = (snap.get("pm_metrics") or {}).get("sprint")
        if sprint is None:
            no_sprint_count += 1
            continue
        groups.setdefault(sprint, []).append(snap)
    return groups, no_sprint_count


def _first_non_null(snapshots: "list[dict]", key: str) -> Any:
    for snap in snapshots:
        value = (snap.get("pm_metrics") or {}).get(key)
        if value is not None:
            return value
    return None


def sprint_rollup_row(sprint_name: str, snapshots: "list[dict]") -> "dict[str, Any]":
    """A sprint's dates should agree across every story that shares it, but a
    snapshot predating Story 6.5 could sit alongside one that does carry
    dates - the first non-null value found wins, same pragmatic posture
    metrics_report.date_key_of() already uses elsewhere in this codebase."""
    end_date = _first_non_null(snapshots, "sprint_end_date")
    return {
        "name": sprint_name,
        "start_date": _first_non_null(snapshots, "sprint_start_date"),
        "end_date": end_date,
        "story_count": len(snapshots),
        "status": sprint_status(end_date),
    }


def render_sprint_rollups(snapshots: "list[dict]") -> str:
    groups, no_sprint_count = group_by_sprint(snapshots)
    if not groups:
        return ""  # additive only - no section at all when nothing is sprint-tagged

    rows = sorted(
        (sprint_rollup_row(name, group) for name, group in groups.items()),
        key=lambda r: r["name"],
    )

    def th(label: str) -> str:
        description = _field_guide.FIELD_GUIDE.get(SPRINT_COLUMN_FIELD_GUIDE_KEYS.get(label), "")
        title_attr = f' title="{description}"' if description else ""
        return f"<th{title_attr}>{label}</th>"

    header = (
        "<tr>"
        + "".join(
            th(label)
            for label in ["Sprint", "Start Date", "End Date", "Story Count", "Overall Status"]
        )
        + "</tr>"
    )

    def date_cell(value: Any) -> str:
        return value if isinstance(value, str) and value else "unknown"

    def row_html(row: "dict[str, Any]") -> str:
        cells = [
            row["name"],
            date_cell(row["start_date"]),
            date_cell(row["end_date"]),
            str(row["story_count"]),
            row["status"],
        ]
        return "<tr>" + "".join(f"<td>{cell}</td>" for cell in cells) + "</tr>"

    body_rows = [row_html(row) for row in rows]
    if no_sprint_count:
        body_rows.append(
            "<tr>"
            + "".join(
                f"<td>{cell}</td>" for cell in ["No Sprint", "—", "—", str(no_sprint_count), "—"]
            )
            + "</tr>"
        )

    table = (
        f"<table>\n<thead>{header}</thead>\n<tbody>\n{chr(10).join(body_rows)}\n</tbody>\n</table>"
    )
    return f'<h2 class="section-heading">Sprint Rollups</h2>\n{table}'


CSS = """
:root{
  --bg:#0a0e14; --panel:#111927; --panel2:#151f30; --line:#22304a;
  --text:#dbe4f3; --dim:#8fa2c2; --dim2:#5f7396; --accent:#3ddc97; --accent2:#5eb6ff;
}
@media (prefers-color-scheme: light){
  :root{
    --bg:#f6f8fb; --panel:#ffffff; --panel2:#f1f4f9; --line:#dde3ee;
    --text:#1b2434; --dim:#4d5b74; --dim2:#7d89ba; --accent:#0b8f57; --accent2:#1f6fd1;
  }
}
:root[data-theme="dark"]{
  --bg:#0a0e14; --panel:#111927; --panel2:#151f30; --line:#22304a;
  --text:#dbe4f3; --dim:#8fa2c2; --dim2:#5f7396; --accent:#3ddc97; --accent2:#5eb6ff;
}
:root[data-theme="light"]{
  --bg:#f6f8fb; --panel:#ffffff; --panel2:#f1f4f9; --line:#dde3ee;
  --text:#1b2434; --dim:#4d5b74; --dim2:#7d89ba; --accent:#0b8f57; --accent2:#1f6fd1;
}
*{box-sizing:border-box;}
body{
  margin:0; background:var(--bg); color:var(--text); min-height:100vh;
  font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;
  padding:40px 4vw 64px;
}
.wrap{max-width:1100px; margin:0 auto;}
h1{font-size:1.8rem; margin:0 0 24px;}
.section-heading{font-size:1.1rem; color:var(--dim); margin:0 0 12px;}
.section-heading + table{margin-bottom:32px;}
.tiles{display:flex; gap:14px; flex-wrap:wrap; margin-bottom:32px;}
.tile{
  background:var(--panel); border:1px solid var(--line); border-radius:10px;
  padding:16px 18px; min-width:200px; flex:1;
}
.tile-label{font-size:0.78rem; color:var(--dim2); text-transform:uppercase; letter-spacing:0.05em;}
.tile-value{font-size:1.1rem; margin-top:6px; color:var(--text);}
table{width:100%; border-collapse:collapse; background:var(--panel); border:1px solid var(--line);}
th, td{text-align:left; padding:10px 12px; border-bottom:1px solid var(--line); font-size:0.9rem;}
th{color:var(--dim2); font-weight:600; background:var(--panel2);}
td{color:var(--text);}
"""


def render_dashboard(snapshots: "list[dict]") -> str:
    stats = aggregate_stats(snapshots)
    # a full HTML5 document, not a bare fragment (found via live E2E: browsers render
    # a fragment leniently, but a real <!doctype html>/<head>/<title> matches this
    # repo's own established HTML-doc convention, e.g. docs/architecture-diagram-leadership.html)
    return (
        "<!doctype html>\n"
        '<html lang="en">\n'
        "<head>\n"
        '<meta charset="utf-8">\n'
        '<meta name="viewport" content="width=device-width, initial-scale=1">\n'
        "<title>Metrics Dashboard</title>\n"
        f"<style>{CSS}</style>\n"
        "</head>\n"
        "<body>\n"
        '<div class="wrap">\n'
        "<h1>Metrics Dashboard</h1>\n"
        f"{render_stat_tiles(stats)}\n"
        f"{render_sprint_rollups(snapshots)}\n"
        f"{render_table(snapshots)}\n"
        "</div>\n"
        "</body>\n"
        "</html>\n"
    )


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

    snapshots = metrics_report.discover_snapshots(root)

    reports_dir = root / REPORTS_DIR
    reports_dir.mkdir(parents=True, exist_ok=True)
    target = reports_dir / OUTPUT_FILE
    with open(target, "w", encoding="utf-8", newline="\n") as f:
        f.write(render_dashboard(snapshots))

    print(
        json.dumps(
            {"ok": True, "dashboard": str(target.resolve()), "stories_rendered": len(snapshots)}
        )
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
