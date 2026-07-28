#!/usr/bin/env python3
# /// script
# requires-python = ">=3.8"
# ///
"""Snapshot assembler — reduces the event log into an immutable versioned snapshot.

The pipeline's ONLY reducer (AD-3): the single component allowed to read
`.story-events.jsonl` and to resolve the AD-1b pending spool. Producers stay
append-only; only the snapshot ever crosses to a central layer, never the raw
log (NFR3).

Envelope (AD-3a, fixed): {schema_version, story_id, revision, pm_metrics,
engineering_metrics, story_point_cost, token_cost}. story_point_cost holds
{phase1_points, phase2_points, variance, reduced_confidence,
reduced_confidence_reasons} (Stories 2.5/2.6).

Phase-1 (Story 2.5) is read back from the manifest's `points_estimated`
(AD-6a: always distinct from the developer-confirmed `points`, written by
Story 2.5's kickoff-time estimator, never recomputed here).

Phase-2 (Story 2.6), computed at close from the event log:
- review_cycles = max(0, ai.*.prompt event count - 1) — AD-6's literal
  "UserPromptSubmit follow-up count", a direct mapping onto our own prompt
  events, no invention needed.
- decision_events = 0, always, today: no producer in this pipeline emits an
  "agent-narrated decision" event (Stories 2.2/2.3 fixed the full producer
  set) — rather than faking this signal, story_point_cost.reduced_confidence
  is set true with a stated reason (AD-10's vocabulary, applied here to a
  same-tool gap rather than a cross-tool one).
- verification/context files: for each git.commit event's hash, bridge-import
  the shared git_out() helper from tools/hooks/_events.py (reused, not
  reimplemented — subprocess argument-list safety, §4) and run
  `git show --stat --format= <hash>`; a path containing "test" (case
  insensitive) counts as a verification file, everything else as a context
  file, deduped by path across all commits. AD-6 asks for sub-type weights
  (unit/integration/manual/perf) this event schema cannot distinguish; a
  uniform x1 (integration-equivalent) is applied instead — a documented
  simplification, not a fabricated classifier. A commit whose hash git can't
  resolve (no git, rewritten history, etc.) contributes 0 and is skipped
  silently — it still counts in engineering_metrics.commits.
- Combination (AD-6 lists these four inputs but gives no arithmetic — this
  formula is this story's own documented invention, exactly like Phase-1's
  volatility fill was): phase2_points = round(review_cycles*1.0 +
  verification_files*1.0 + context_files*0.2). decision_events contributes 0.
- variance = phase2_points - phase1_points, only when phase1_points is not
  null (honest null otherwise — can't diff against nothing).

Revisions (AD-3b): snapshots/{story_id}.v{schema}.rev{N}.json, N = max+1;
files are created exclusively ("x" mode) — an existing revision is refused,
never replaced. Consumers take the highest revision as current; priors are
audit history. Snapshot files are committed artifacts (spine § Deployment).

Backfill (AD-1b): pending-spool events get the manifest's story_id, join the
reduction, are appended to the main log, and the spool is deleted — after a
successful snapshot write, so a failed close never consumes the buffer.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

sys.path.insert(
    0, str(Path(__file__).resolve().parents[1] / "hooks")
)  # bridge to shared helpers (spine-sanctioned, reused not reimplemented)
import _events
import _field_guide  # Story 5.11: shared field-descriptions dict

EVENTS_FILE = ".story-events.jsonl"
PENDING_FILE = ".story-events.pending.jsonl"
MANIFEST = ".story.yaml"
CONFIG = ".story-config.yaml"
SNAPSHOTS_DIR = "snapshots"
SCHEMA_VERSION = 1
DECISION_EVENTS_REASON = (
    "no decision-narration producer implemented (out of scope through Story 2.6)"
)


def parse_scalar(raw: str) -> str:
    """One flat-YAML scalar: paired quotes shield `#`; bare values end at ` #`."""
    value = raw.strip()
    if value[:1] in ("'", '"'):
        quote, body = value[0], value[1:]
        end = body.find(quote)
        return body[:end] if end != -1 else body
    if value.startswith("#"):
        return ""
    if " #" in value:
        value = value.split(" #", 1)[0].strip()
    return value


def read_manifest(path: Path) -> dict[str, Any]:
    """Values are strings, except the bare (unquoted) YAML `null` token, which the
    writer (docs-only/main.py's render()) uses to represent Python None — read back
    as None here too, or any nullable numeric field (e.g. points_estimated) would
    receive the literal string "null" instead."""
    manifest: dict[str, Any] = {}
    for line in path.read_text(encoding="utf-8-sig").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or ":" not in stripped:
            continue
        key, raw = stripped.split(":", 1)
        raw_value = raw.strip()
        value = parse_scalar(raw)
        is_bare_null = value == "null" and raw_value[:1] not in ("'", '"')
        manifest[key.strip()] = None if is_bare_null else value
    return manifest


def read_story_config(root: Path) -> "dict[str, str]":
    """Story 5.2: the optional cost-rate keys (hourly_rate, ai_input_rate,
    ai_output_rate) from .story-config.yaml, reusing this file's own parse_scalar()
    (already used for read_manifest()) - not tools/adapters/resolve.py, which is
    scoped to kickoff-time source_of_truth/ai_tool resolution, not general config
    reading (project-context.md §2 - no premature abstraction/extraction here)."""
    path = root / CONFIG
    if not path.is_file():
        return {}
    config: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8-sig").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or ":" not in stripped:
            continue
        key, raw = stripped.split(":", 1)
        config[key.strip()] = parse_scalar(raw)
    return config


def as_number(value: Optional[str]) -> Any:
    if value is None:
        return None
    try:
        number = float(value)
    except ValueError:
        return value
    return int(number) if number.is_integer() else number


def read_jsonl(path: Path) -> "tuple[list[dict], int]":
    """Return (valid events, malformed line count); a missing file is an empty log."""
    if not path.is_file():
        return [], 0
    events: list[dict] = []
    malformed = 0
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            parsed = json.loads(line)
        except json.JSONDecodeError:
            malformed += 1
            continue
        if isinstance(parsed, dict):
            events.append(parsed)
        else:
            malformed += 1
    return events, malformed


def reduce_events(events: "list[dict]") -> dict[str, Any]:
    def count(predicate) -> int:
        return sum(1 for e in events if predicate(e.get("type") or ""))

    timestamps = sorted(e["timestamp"] for e in events if isinstance(e.get("timestamp"), str))
    return {
        "commits": count(lambda t: t == "git.commit"),
        "checkouts": count(lambda t: t == "git.checkout"),
        "merges": count(lambda t: t == "git.merge"),
        "ai_sessions": count(lambda t: t.startswith("ai.") and t.endswith(".session_start")),
        "tool_uses": count(lambda t: t.startswith("ai.") and t.endswith(".tool_use")),
        "prompts": count(lambda t: t.startswith("ai.") and t.endswith(".prompt")),
        "event_count": len(events),
        # ISO-8601 strings with a fixed local offset sort correctly lexicographically
        # for same-offset producers; cross-timezone teams are out of pilot scope.
        "first_event_at": timestamps[0] if timestamps else None,
        "last_event_at": timestamps[-1] if timestamps else None,
    }


def activity_span_of(events: "list[dict]") -> "tuple[Optional[str], Optional[str]]":
    """Story 3.5: like reduce_events()'s own first/last timestamp scan, but excludes
    bookkeeping event types (opsx.*, ai.<tool>.session_start/session_end, time.*) so a
    later administrative action (e.g. a re-run of `opsx archive` well after real work
    ended) can't stretch estimated_cost_of()'s raw-span fallback. Deliberately NOT an
    include-list of just git.*/prompt/tool_use - that would wrongly undercount real
    activity like defect-capture events (tool_start/stop/defect_*). Only used by
    estimated_cost_of()'s fallback branch - engineering_metrics.first_event_at/
    last_event_at (reduce_events(), above) are untouched and keep their existing
    "first/last event of any kind" meaning."""

    def is_bookkeeping(event_type: str) -> bool:
        return (
            event_type.startswith("opsx.")
            or event_type.startswith("time.")
            or (
                event_type.startswith("ai.")
                and event_type.endswith((".session_start", ".session_end"))
            )
        )

    timestamps = sorted(
        e["timestamp"]
        for e in events
        if isinstance(e.get("timestamp"), str) and not is_bookkeeping(e.get("type") or "")
    )
    return (timestamps[0], timestamps[-1]) if timestamps else (None, None)


def token_cost_of(events: "list[dict]", config: "dict[str, str]") -> dict[str, Any]:
    """Story 5.2: real input/output token sums (from session_end.py's transcript
    parsing) plus a computed cost_usd - only when both token counts AND both rates
    are known (AD-10: never a fabricated number from partial inputs)."""
    session_starts = [
        e
        for e in events
        if (e.get("type") or "").startswith("ai.") and e["type"].endswith(".session_start")
    ]
    session_ends = [
        e
        for e in events
        if (e.get("type") or "").startswith("ai.") and e["type"].endswith(".session_end")
    ]
    input_known = [
        e.get("payload", {}).get("input_tokens")
        for e in session_ends
        if isinstance(e.get("payload", {}).get("input_tokens"), (int, float))
    ]
    output_known = [
        e.get("payload", {}).get("output_tokens")
        for e in session_ends
        if isinstance(e.get("payload", {}).get("output_tokens"), (int, float))
    ]
    reasons = [
        e.get("payload", {}).get("token_cost_reason")
        for e in session_ends
        if e.get("payload", {}).get("token_cost_reason")
    ]

    input_tokens = sum(input_known) if input_known else None
    output_tokens = sum(output_known) if output_known else None
    input_rate = as_number(config.get("ai_input_rate"))
    output_rate = as_number(config.get("ai_output_rate"))
    cost_usd = None
    if (
        input_tokens is not None
        and output_tokens is not None
        and isinstance(input_rate, (int, float))
        and isinstance(output_rate, (int, float))
    ):
        cost_usd = round(
            (input_tokens * input_rate / 1_000_000) + (output_tokens * output_rate / 1_000_000),
            4,
        )

    if input_tokens is not None:
        reason = None
    elif session_ends and len(session_starts) > len(session_ends):
        # Story 5.10: some session(s) closed cleanly (and would otherwise hand their
        # own reasons[0] here) but at least one other session_start for this story
        # never got a matching session_end at all - surfacing a closed session's own
        # reason would misleadingly imply *that* session's failure explains the
        # missing tokens, when the real cause may well be the session that never
        # closed (found live, 2026-07-16: the session with 96 of 98 real events for
        # a story never sent session_end, while reasons[0] came from an unrelated
        # 2-minute reconnect blip). Name the gap explicitly instead of guessing.
        missing = len(session_starts) - len(session_ends)
        reason = (
            f"{missing} of {len(session_starts)} AI session(s) for this story never sent "
            "session_end (still open, or closed without firing it) - token usage for "
            "that session is not reflected here"
        )
    elif session_ends:
        # only surface a reason when token counts are actually null - real data from
        # a later session must never be shadowed by a stale reason from an earlier,
        # unrelated session that happened to fail (caught via live E2E, Story 5.2)
        reason = reasons[0] if reasons else None
    else:
        # distinct from the case above (AD-10, Story 5.6): zero session_end events
        # means no AI session was ever observed closing cleanly for this story -
        # e.g. the editor was closed abruptly instead of /exit or Ctrl+C - which is
        # not the same gap as "a session ended but its own transcript read failed"
        reason = "no AI session_end event observed for this story"

    return {
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "reason": reason,
        "sessions_started": len(session_starts),
        "sessions_observed": len(session_ends),
        "cost_usd": cost_usd,
    }


def defect_metrics_of(events: "list[dict]") -> dict[str, Any]:
    """Story 5.4: reduces ai.claude-code.defect_{compile,test,review} events into
    counts and QA efficiency percentages (formulas match aep-orchestrator's
    reference tool exactly - only its 100%-manual capture mechanism was not
    copied, see the story's Background). Null-with-reason when zero defect
    events exist (AD-10) - deliberately NOT the reference tool's fabricated
    100%/0% default, since "no defects logged" and "confirmed zero defects"
    are indistinguishable without a stronger signal this story doesn't add."""

    def count(event_type: str) -> int:
        return sum(1 for e in events if e.get("type") == event_type)

    compile_defects = count("ai.claude-code.defect_compile")
    test_defects = count("ai.claude-code.defect_test")
    review_defects = count("ai.claude-code.defect_review")
    total_defects = compile_defects + test_defects + review_defects

    if total_defects == 0:
        return {
            "total_defects": None,
            "compile_defects": None,
            "test_defects": None,
            "review_defects": None,
            "testing_efficiency": None,
            "review_efficiency": None,
            "reason": "no defects logged for this story",
        }

    return {
        "total_defects": total_defects,
        "compile_defects": compile_defects,
        "test_defects": test_defects,
        "review_defects": review_defects,
        "testing_efficiency": round((compile_defects + test_defects) / total_defects * 100, 2),
        "review_efficiency": round(review_defects / total_defects * 100, 2),
        "reason": None,
    }


def active_time_seconds_of(events: "list[dict]") -> dict[str, Any]:
    """Story 3.4: reduces Epic 3's time.slice_opened/paused/closed events into a
    real, idle-excluded active-time total - the reducer half of AD-7 left
    unwired when Story 3.3 completed the capture side. Walks all `time.*`
    events for this story in chronological order, accumulating idle seconds
    seen since the last slice_opened and subtracting them from each
    slice_closed's own duration_seconds. A dangling slice_opened with no later
    slice_closed contributes nothing yet - not lost, just not observable until
    the session that owns it actually ends (same philosophy as token_cost's
    session-boundary null). Malformed duration_seconds/idle_seconds degrade
    that one contribution to 0 rather than raising."""
    time_events = sorted(
        (e for e in events if (e.get("type") or "").startswith("time.")),
        key=lambda e: e.get("timestamp") or "",
    )

    total_active = 0.0
    idle_since_open = 0.0
    any_closed = False

    for e in time_events:
        event_type = e.get("type")
        payload = e.get("payload") or {}
        if event_type == "time.slice_opened":
            idle_since_open = 0.0
        elif event_type == "time.slice_paused":
            idle = payload.get("idle_seconds")
            if isinstance(idle, (int, float)):
                idle_since_open += idle
        elif event_type == "time.slice_closed":
            any_closed = True
            duration = payload.get("duration_seconds")
            if isinstance(duration, (int, float)):
                total_active += max(0.0, duration - idle_since_open)
            idle_since_open = 0.0

    if not any_closed:
        return {
            "active_seconds": None,
            "reason": "no completed time slice observed for this story",
        }
    return {"active_seconds": total_active, "reason": None}


def estimated_cost_of(config: "dict[str, str]", events: "list[dict]") -> dict[str, Any]:
    """Story 5.2: hourly_rate x duration, both read at close time (not locked at
    kickoff - a documented limitation, see the story's Dev Notes) - null-with-reason
    whenever the rate is absent or duration can't be computed (AD-10).

    Story 3.4: duration prefers idle-excluded active time (active_time_seconds_of)
    when at least one completed time.slice_closed exists for this story; falls
    back to the original raw first/last-event span otherwise (older snapshots,
    an ai_tool whose hooks don't emit time.slice_* yet, or a session still open
    at story-close time) - silently, exactly as before this story existed.

    Story 3.5: the fallback span comes from activity_span_of(), not
    engineering_metrics.first_event_at/last_event_at - excludes bookkeeping events
    (opsx.*/session_start/session_end/time.*) so a later administrative re-run
    can't inflate a story's reported duration."""
    active_time = active_time_seconds_of(events)
    duration_minutes = None
    if active_time["active_seconds"] is not None:
        duration_minutes = active_time["active_seconds"] / 60
    else:
        first_at, last_at = activity_span_of(events)
        if first_at and last_at:
            try:
                start = datetime.fromisoformat(first_at)
                end = datetime.fromisoformat(last_at)
                duration_minutes = (end - start).total_seconds() / 60
            except (ValueError, TypeError):
                # TypeError: e.g. one timestamp offset-aware and the other offset-naive
                # (a hand-edited or corrupted event log) - subtraction itself raises this,
                # not fromisoformat(), so ValueError alone doesn't cover it (review finding, PR #26)
                duration_minutes = None

    # usd is computed from the full-precision duration_minutes above; only the
    # displayed/returned value is rounded, so rounding here never skews the cost.
    duration_minutes_display = None if duration_minutes is None else round(duration_minutes, 2)

    hourly_rate = as_number(config.get("hourly_rate"))
    if not isinstance(hourly_rate, (int, float)):
        return {
            "usd": None,
            "hourly_rate": None,
            "duration_minutes": duration_minutes_display,
            "reason": "hourly_rate not configured in .story-config.yaml",
        }
    if duration_minutes is None:
        return {
            "usd": None,
            "hourly_rate": hourly_rate,
            "duration_minutes": None,
            "reason": "no events to compute duration from",
        }
    return {
        "usd": round(hourly_rate * (duration_minutes / 60), 2),
        "hourly_rate": hourly_rate,
        "duration_minutes": duration_minutes_display,
        "reason": None,
    }


def touched_files(root: Path, hash_: str) -> "list[str]":
    """Files changed by one commit, via the shared git_out() helper; [] if unresolvable.

    cwd=root is required (not the default None) — the assembler is explicitly
    addressed by --repo-root, which may differ from the ambient process cwd
    (§3); without pinning cwd, `git show` would run against the wrong repo.

    Splitting on the first "|" (rather than requiring a digit immediately
    after it) also captures binary-file lines, e.g. `image.png | Bin 0 -> 4521
    bytes` — those would otherwise silently vanish from the touched-file set.
    """
    output = _events.git_out("show", "--stat", "--format=", hash_, cwd=root)
    if not output:
        return []
    paths = []
    for line in output.splitlines():
        if "|" not in line or "changed" in line:
            continue
        paths.append(line.split("|", 1)[0].strip())
    return paths


def verification_and_context_counts(root: Path, events: "list[dict]") -> "tuple[int, int]":
    """Union of files touched across all git.commit hashes, classified test vs. context."""
    seen: set[str] = set()
    for e in events:
        if e.get("type") != "git.commit":
            continue
        hash_ = e.get("payload", {}).get("hash")
        if not hash_:
            continue
        seen.update(touched_files(root, hash_))
    verification = sum(1 for path in seen if "test" in path.lower())
    context = len(seen) - verification
    return verification, context


def review_cycles_of(events: "list[dict]") -> int:
    prompts = sum(
        1
        for e in events
        if (e.get("type") or "").startswith("ai.") and e["type"].endswith(".prompt")
    )
    return max(0, prompts - 1)


def story_point_cost_of(
    root: Path, events: "list[dict]", manifest: dict[str, str]
) -> dict[str, Any]:
    phase1_points = as_number(manifest.get("points_estimated"))

    review_cycles = review_cycles_of(events)
    verification_files, context_files = verification_and_context_counts(root, events)
    decision_events = 0  # always, today — see DECISION_EVENTS_REASON

    phase2_points = round(
        review_cycles * 1.0 + verification_files * 1.0 + context_files * 0.2 + decision_events
    )
    variance = (phase2_points - phase1_points) if phase1_points is not None else None

    return {
        "phase1_points": phase1_points,
        "phase2_points": phase2_points,
        "variance": variance,
        "reduced_confidence": True,
        "reduced_confidence_reasons": [DECISION_EVENTS_REASON],
    }


def next_revision(snapshots: Path, story_id: str) -> int:
    prefix = f"{story_id}.v{SCHEMA_VERSION}.rev"
    highest = 0
    if snapshots.is_dir():
        for path in snapshots.glob(f"{prefix}*.json"):
            suffix = path.name[len(prefix) : -len(".json")]
            if suffix.isdigit():
                highest = max(highest, int(suffix))
    return highest + 1


def append_line(path: Path, line: str) -> None:
    fd = os.open(str(path), os.O_APPEND | os.O_CREAT | os.O_WRONLY, 0o644)
    try:
        os.write(fd, line.encode("utf-8"))
    finally:
        os.close(fd)


def fail(message: str) -> int:
    print(f"error: {message}", file=sys.stderr)
    return 2


def main(argv: "list[str] | None" = None) -> int:
    p = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    p.add_argument("--repo-root", required=True, help="repository root of the story being closed")
    p.add_argument(
        "--dry-run",
        action="store_true",
        help=(
            "compute and print the snapshot without writing it or consuming the "
            "pending spool - preview current metrics without closing the story (Story 2.12)"
        ),
    )
    args = p.parse_args(argv)

    root = Path(args.repo_root)
    if not root.is_dir():
        return fail(f"--repo-root {args.repo_root!r} is not a directory")
    manifest_path = root / MANIFEST
    if not manifest_path.is_file():
        return fail("no .story.yaml found — kick off the story before closing it (AD-5)")
    manifest = read_manifest(manifest_path)
    story = manifest.get("story_id")
    if not story:
        return fail(f"{manifest_path} carries no story_id")

    log_events, malformed_main = read_jsonl(root / EVENTS_FILE)
    pending_events, malformed_pending = read_jsonl(root / PENDING_FILE)
    malformed = malformed_main + malformed_pending
    if malformed:
        print(f"warning: skipped {malformed} malformed event line(s)", file=sys.stderr)

    for pending in pending_events:
        pending["story_id"] = story
    ours = [e for e in log_events if e.get("story_id") == story] + pending_events
    config = read_story_config(root)
    engineering_metrics = reduce_events(ours)

    snapshot = {
        "field_guide": _field_guide.FIELD_GUIDE,
        "schema_version": SCHEMA_VERSION,
        "story_id": story,
        "revision": next_revision(root / SNAPSHOTS_DIR, story),
        "pm_metrics": {
            "name": manifest.get("name"),
            "points": as_number(manifest.get("points")),
            "goal": manifest.get("goal"),
            "sprint": manifest.get("sprint"),
            "sprint_start_date": manifest.get("sprint_start_date"),
            "sprint_end_date": manifest.get("sprint_end_date"),
            "source_of_truth": manifest.get("source_of_truth"),
            "ai_tool": manifest.get("ai_tool"),
            "created": manifest.get("created"),
        },
        "engineering_metrics": engineering_metrics,
        "story_point_cost": story_point_cost_of(root, ours, manifest),
        "token_cost": token_cost_of(ours, config),
        "estimated_cost": estimated_cost_of(config, ours),
        "defect_metrics": defect_metrics_of(ours),
    }

    if args.dry_run:
        print(
            json.dumps(
                {
                    "ok": True,
                    "dry_run": True,
                    "snapshot": snapshot,
                    "would_be_revision": snapshot["revision"],
                    "events_reduced": len(ours),
                },
                indent=2,
            )
        )
        return 0

    snapshots_dir = root / SNAPSHOTS_DIR
    snapshots_dir.mkdir(parents=True, exist_ok=True)
    target = snapshots_dir / f"{story}.v{SCHEMA_VERSION}.rev{snapshot['revision']}.json"
    try:
        with open(target, "x", encoding="utf-8", newline="\n") as f:
            f.write(json.dumps(snapshot, indent=2) + "\n")
            f.flush()
            os.fsync(f.fileno())
    except FileExistsError:
        return fail(
            f"{target} already exists — revisions are immutable (AD-3), refusing to replace"
        )

    if pending_events:
        main_log = root / EVENTS_FILE
        for pending in pending_events:
            append_line(main_log, json.dumps(pending) + "\n")
        (root / PENDING_FILE).unlink()

    print(
        json.dumps(
            {
                "ok": True,
                "snapshot": str(target.resolve()),
                "revision": snapshot["revision"],
                "events_reduced": len(ours),
                "pending_backfilled": len(pending_events),
            }
        )
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
