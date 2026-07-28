#!/usr/bin/env python3
"""Story 5.11: one shared, static field-description source for every generated
artifact (snapshots/*.json, metrics-reports/*.md, dashboard.html) — so a reader
never has to go find a docstring or INSTALL.md to understand a field they're
looking at. Dotted-path keys mirror the snapshot envelope's own nesting
exactly (e.g. "token_cost.reason"); each description states the field's
purpose and, where the value is computed rather than copied verbatim from a
source system, a plain-language summary of the calculation.

Bridge-imported the same way tools/hooks/_events.py already is (by the
snapshot assembler, metrics-report, and dashboard tools) — a shared static
dict, not a new abstraction layer (project-context.md §7).

Dotted-path keys mostly mirror the snapshot envelope's own nesting exactly;
the one exception is the `dashboard.sprint_rollup.*` prefix (Story 6.6),
which documents two dashboard-only aggregate columns (Story Count, Overall
Status) that are computed purely at render time and have no corresponding
snapshot field to mirror.
"""

from __future__ import annotations

FIELD_GUIDE: "dict[str, str]" = {
    "schema_version": (
        "Snapshot envelope format version. Bumped only when an existing field's "
        "meaning changes — new, purely-additive fields (like this field_guide "
        "itself) don't require a bump."
    ),
    "story_id": "Unique identifier for this story, generated at kickoff (.story.yaml).",
    "revision": (
        "Which close of this story this is (AD-3b). 1 on first close; re-closing "
        "(e.g. after a --dry-run check, or a corrected re-run) creates rev2, rev3, "
        "... The highest revision is current; earlier ones are kept as audit "
        "history, never overwritten or deleted."
    ),
    "pm_metrics.name": "Story title, as entered or fetched at kickoff.",
    "pm_metrics.points": (
        "Developer-confirmed story points (a JIRA field, or manually entered for "
        "docs-only stories) — the 'official' estimate, distinct from "
        "story_point_cost's own phase1/phase2 numbers below."
    ),
    "pm_metrics.goal": "One-line goal/summary captured at kickoff.",
    "pm_metrics.sprint": "Sprint or milestone name, if your PM tool tracks one; null if not.",
    "pm_metrics.sprint_start_date": (
        "The sprint's start date (Story 6.5), from the same JIRA sprint object "
        "pm_metrics.sprint's name came from. Null if the story predates Story "
        "6.5, isn't JIRA-backed, or the sprint hadn't started yet at kickoff time."
    ),
    "pm_metrics.sprint_end_date": (
        "The sprint's end date (Story 6.5) — same source and null conditions as "
        "pm_metrics.sprint_start_date."
    ),
    "pm_metrics.source_of_truth": (
        "Which PM system this story's kickoff data came from: jira, confluence, or docs-only."
    ),
    "pm_metrics.ai_tool": (
        "Which AI coding tool's hooks captured this story's activity (e.g. "
        "claude-code) — set once at project level in .story-config.yaml, never "
        "asked per-story."
    ),
    "pm_metrics.created": "Timestamp this story was kicked off (.story.yaml written).",
    "engineering_metrics.commits": "Count of git.commit events captured for this story.",
    "engineering_metrics.checkouts": (
        "Count of git.checkout events (branch switches) captured while this story was active."
    ),
    "engineering_metrics.merges": "Count of git.merge events captured for this story.",
    "engineering_metrics.ai_sessions": (
        "Count of AI session_start events tagged to this story — how many times an "
        "AI coding session began while this story was active. Not the same as "
        "token_cost.sessions_observed: a session that starts but never sends "
        "session_end still counts here."
    ),
    "engineering_metrics.tool_uses": (
        "Count of AI tool-call events (file edits, commands run, etc.) captured for this story."
    ),
    "engineering_metrics.prompts": (
        "Count of user prompts submitted during AI sessions for this story — also "
        "the direct input to story_point_cost.phase2_points's review_cycles calculation."
    ),
    "engineering_metrics.event_count": (
        "Total raw events (of every type) captured for this story — the "
        "denominator underlying every other engineering_metrics count."
    ),
    "engineering_metrics.first_event_at": (
        "Timestamp of the earliest captured event of any kind for this story "
        "(includes bookkeeping events like session_start/opsx.*) — not the same "
        "span estimated_cost.duration_minutes uses, which excludes bookkeeping."
    ),
    "engineering_metrics.last_event_at": (
        "Timestamp of the latest captured event of any kind for this story — same "
        "bookkeeping-inclusive caveat as first_event_at above."
    ),
    "story_point_cost.phase1_points": (
        "Pre-work estimate, read from .story.yaml's points_estimated (auto-computed "
        "at kickoff from a real tasks.md, when using openspec) — null if no "
        "estimator ran at kickoff time."
    ),
    "story_point_cost.phase2_points": (
        "Post-work estimate computed at close: round(review_cycles*1.0 + "
        "verification_files*1.0 + context_files*0.2), where review_cycles = "
        "prompts - 1, and verification/context files are counted from each "
        "commit's changed-file list (a path containing 'test' counts as "
        "verification). A documented formula this project invented — not a "
        "precise 'true' story-point count."
    ),
    "story_point_cost.variance": (
        "phase2_points - phase1_points — how far the after-the-fact estimate "
        "drifted from the pre-work one. Null whenever phase1_points itself is "
        "null (nothing to diff against)."
    ),
    "story_point_cost.reduced_confidence": (
        "True whenever an input to phase2_points had to be approximated rather "
        "than measured directly. Today this is always true, because this "
        "pipeline has no producer for 'agent narrated a decision' events — see "
        "reduced_confidence_reasons."
    ),
    "story_point_cost.reduced_confidence_reasons": (
        "Why reduced_confidence is true — today always because decision_events "
        "(one of the four documented phase2_points inputs) is a fixed 0 rather "
        "than a real measured count."
    ),
    "token_cost.input_tokens": (
        "Sum of input_tokens across every AI session_end event for this story "
        "(from the AI tool's own transcript usage data) — null if no session_end "
        "reported a real count."
    ),
    "token_cost.output_tokens": "Same as input_tokens, but output tokens.",
    "token_cost.sessions_started": (
        "Count of ai.<tool>.session_start events for this story (Story 5.10) — "
        "mirrors engineering_metrics.ai_sessions. Compare against sessions_observed: "
        "if higher, at least one session never sent session_end, and reason "
        "explains the gap rather than surfacing an unrelated closed session's own reason."
    ),
    "token_cost.cost_usd": (
        "(input_tokens x ai_input_rate + output_tokens x ai_output_rate) / "
        "1,000,000, using the rates from .story-config.yaml — null unless both "
        "token counts AND both rates are known (never a fabricated partial number)."
    ),
    "token_cost.sessions_observed": (
        "How many session_end events (of any outcome) were seen for this story. "
        "Compare against engineering_metrics.ai_sessions (how many sessions "
        "started) — a gap between the two means at least one session never sent "
        "session_end (e.g. closing VS Code's chat panel via its 'x' button, which "
        "doesn't reliably fire it)."
    ),
    "token_cost.reason": (
        "Explains a null cost_usd: no session_end at all, a session_end whose own "
        "transcript read failed, or rates not configured in .story-config.yaml. "
        "If this looks like it doesn't match what you actually did, check "
        "sessions_observed against engineering_metrics.ai_sessions above — it may "
        "be describing an unrelated session's outcome."
    ),
    "estimated_cost.usd": (
        "hourly_rate x (duration_minutes / 60) — null unless hourly_rate is "
        "configured in .story-config.yaml and a duration could be computed."
    ),
    "estimated_cost.hourly_rate": (
        "The hourly_rate value read from .story-config.yaml at close time (not locked in at kickoff)."
    ),
    "estimated_cost.duration_minutes": (
        "Active work time for this story, in minutes. Prefers idle-excluded real "
        "active time (from time.slice_* events); falls back to a raw first/last "
        "real-activity timestamp span (bookkeeping events excluded) only when no "
        "completed time slice exists — e.g. a story closed while its AI session "
        "was still open."
    ),
    "estimated_cost.reason": (
        "Explains a null usd/duration_minutes: hourly_rate not configured, or no "
        "events to compute a duration from."
    ),
    "defect_metrics.total_defects": (
        "Count of all compile + test + review defects logged for this story — "
        "null (not 0) if none were ever logged, since 'no defects logged' and "
        "'confirmed zero defects' aren't the same thing."
    ),
    "defect_metrics.compile_defects": (
        "Count of automatically-captured build/compile failures (a configured "
        "build_commands pattern exiting non-zero)."
    ),
    "defect_metrics.test_defects": (
        "Count of automatically-captured test failures (a configured "
        "test_commands pattern exiting non-zero)."
    ),
    "defect_metrics.review_defects": (
        "Count of defects logged from a pasted code review's findings, verified "
        "real and fixed (also creates a JIRA sub-task for JIRA-backed stories)."
    ),
    "defect_metrics.testing_efficiency": (
        "(compile_defects + test_defects) / total_defects x 100 — what share of "
        "this story's defects were caught by automated compile/test, vs. "
        "surviving to review."
    ),
    "defect_metrics.review_efficiency": (
        "review_defects / total_defects x 100 — what share of this story's "
        "defects were only caught by a later code review, not by automated "
        "compile/test."
    ),
    "defect_metrics.reason": (
        "Explains null defect fields: no defects were ever logged for this story "
        "(not evidence of zero real defects — just none captured)."
    ),
    "dashboard.sprint_rollup.story_count": (
        "How many locally-known stories share this sprint name. Every story "
        "shown here is already closed — a snapshot only ever exists after a "
        "story closes (AD-3) — so this is never a count of a sprint's total "
        "planned work, only of what this pipeline has captured so far."
    ),
    "dashboard.sprint_rollup.status": (
        "The sprint's own timeline, not story completion: 'Ended' once the "
        "sprint's end date (Story 6.5) has passed, 'Active or upcoming' "
        "otherwise, or 'Unknown' if no end date was ever captured for it."
    ),
}
