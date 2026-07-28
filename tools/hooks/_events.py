#!/usr/bin/env python3
# /// script
# requires-python = ">=3.8"
# ///
"""Shared event emitter for ALL capture hooks (git + Claude Code families).

Extracted from tools/hooks/git/_events.py in Story 2.3 (user-approved spine
amendment resolving review Issue #7): one emitter, one retry ladder, one
manifest parser. Hook scripts reach it via a one-line documented sys.path
bridge to this parent directory — the sanctioned exception to strict
single-file self-containment.

Invariants implemented here:
- AD-1: producers only append — one os.write() of a whole line to a fd opened
  with O_APPEND, so concurrent producers can never interleave a line. The log
  is never read, never rewritten.
- AD-1a: `type` is always namespaced (`git.*`, `ai.<tool>.*`); the envelope is
  the fixed `{story_id, source, type, timestamp, payload}` shape with `source`
  supplied by the calling family ("git" | "ai").
- AD-1b: events firing before `.story.yaml` exists are appended to a pending
  spool with story_id null — buffered, never dropped. Backfill is the snapshot
  assembler's job (Story 2.4), not ours.
- AD-9: a failed append retries (4 attempts total = 1 + 3 retries), then
  surfaces a visible stderr error. Never a silent loss.

Namespace note (Story 1.5 vs facts): Claude Code hooks always emit under
`ai.claude-code.*` because they ARE the Claude Code adapter — the manifest's
`ai_tool` field tells the kickoff skill and assembler which adapter family is
expected, and a declared/actual mismatch stays detectable, never falsified.

Exit-code table (AD-9 visibility is stderr in every case):
- git post-commit/post-checkout/post-merge → 1 on final failure (git ignores
  post-hook exits; honest without harm)
- git commit-msg → 0 always (non-zero aborts the developer's commit)
- claude hooks (all six) → 0 always (non-zero can block a tool call or
  disrupt the session)
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

EVENTS_FILE = ".story-events.jsonl"
PENDING_FILE = ".story-events.pending.jsonl"
MANIFEST = ".story.yaml"
CONFIG = ".story-config.yaml"
ACTIVE_STORY_FILE = ".active-story"
ACTIVE_SESSION_FILE = ".active-claude-session"
ATTEMPTS = 4  # 1 initial + 3 retries (AD-9)
RETRY_DELAY_SECONDS = 0.1


def _idle_threshold_seconds() -> int:
    """A malformed override must never break import - every hook (incl. commit-msg,
    which must always exit 0) imports this module, so a bad env var falls back
    to the default rather than raising at module scope."""
    try:
        return int(os.environ.get("STORY_IDLE_THRESHOLD_SECONDS", "900"))
    except ValueError:
        return 900


IDLE_THRESHOLD_SECONDS = _idle_threshold_seconds()  # 15 min default (AD-7)


def _now() -> datetime:
    return datetime.now().astimezone()


def git_out(*args: str, cwd: Optional[Path] = None) -> Optional[str]:
    """Run a git query (argument list, never shell=True); any failure degrades to None.

    `cwd` defaults to the ambient process cwd (correct for git hooks, which git
    itself invokes with cwd already at the repo — AD-8). A caller addressed by an
    explicit --repo-root (e.g. the snapshot assembler) MUST pass cwd=<that root>,
    or git runs against whatever directory the process happened to start in
    instead of the repo being operated on (§3 explicit-addressing).
    """
    try:
        proc = subprocess.run(["git", *args], capture_output=True, text=True, timeout=10, cwd=cwd)
    except (OSError, subprocess.TimeoutExpired):
        return None
    if proc.returncode != 0:
        return None
    return proc.stdout.strip() or None


def repo_root() -> Path:
    """Story 2.9: when `git rev-parse` itself fails (git unavailable, a
    transient subprocess failure, etc. - Story 2.7 already fixed the far more
    common cause of cwd drift, a hook command's own relative path), walk up
    from cwd looking for a `.git` directory-or-file (worktrees/submodules use
    a file) before giving up - smarter than blindly trusting cwd, which could
    be a subdirectory of the real repo. Only falls back to bare cwd if no
    `.git` is found anywhere in the parent chain (genuinely not in a repo)."""
    top = git_out("rev-parse", "--show-toplevel")
    if top:
        return Path(top)
    for candidate in (Path.cwd(), *Path.cwd().parents):
        if (candidate / ".git").exists():
            return candidate
    return Path.cwd()


def read_stdin_json() -> dict[str, Any]:
    """Claude Code passes hook input as JSON on stdin; tolerate empty/malformed input.

    Windows reality (the third BOM bug this project has caught): pipes prepend
    a UTF-8 BOM, and worse, Python may decode stdin with the locale codepage
    (cp1252), turning the BOM bytes into three mojibake chars — every piped
    payload then silently degrades to nulls. Reconfiguring stdin to utf-8-sig
    fixes the decode layer; the strip fallbacks cover streams that cannot
    reconfigure (e.g. StringIO in tests).
    """
    stdin = sys.stdin
    try:
        stdin.reconfigure(encoding="utf-8-sig")
    except (AttributeError, ValueError, OSError):
        pass
    try:
        raw = stdin.read()
    except OSError:
        return {}
    raw = raw.lstrip("\ufeff")
    if raw.startswith("\u00ef\u00bb\u00bf"):  # UTF-8 BOM bytes decoded as cp1252 mojibake
        raw = raw[3:]
    try:
        data = json.loads(raw or "{}")
    except json.JSONDecodeError:
        return {}
    return data if isinstance(data, dict) else {}


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


def read_story_config(root: Path) -> "dict[str, str]":
    """Story 5.4: shared here (unlike tools/snapshot-assembler/main.py's own
    duplicate of the same reader, kept standalone by design) because hook
    scripts already import this module rather than staying self-contained -
    no new duplication is introduced by reusing it here."""
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


def read_manifest(root: Path) -> "dict[str, str]":
    """Story 6.8: the same flat-scalar shape as read_story_config() above, just
    for .story.yaml instead - used by pre_tool_use.py's close-command gate to
    check source_of_truth/jira_issue_key without a second parsing style.
    story_id() above stays as its own narrow single-key reader; this is a
    separate, general one for callers that need more than just story_id."""
    path = root / MANIFEST
    if not path.is_file():
        return {}
    manifest: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8-sig").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or ":" not in stripped:
            continue
        key, raw = stripped.split(":", 1)
        manifest[key.strip()] = parse_scalar(raw)
    return manifest


DEFECT_EXIT_MARKER = "__AI_METRICS_EXIT__"
"""Story 5.8: Claude Code's PostToolUse payload never exposes a Bash exit
code (confirmed platform gap, not fixable in this project - see
anthropics/claude-code#33656 / rohitg00/agentmemory#539). Worked around by
having pre_tool_use.py rewrite a matched command to echo this marker plus its
own exit code to stdout, which post_tool_use.py then parses back out - never
relying on a structured exit-code field that Claude Code doesn't send."""


def split_config_patterns(raw: str) -> "list[str]":
    """test_commands/build_commands are comma-separated substring patterns
    (e.g. "pytest, npm test") - shared between pre_tool_use.py (command
    rewriting) and post_tool_use.py (defect matching) so both stay in sync."""
    return [p.strip() for p in raw.split(",") if p.strip()]


def matched_config_pattern(command: str, patterns: "list[str]") -> "str | None":
    for pattern in patterns:
        if pattern in command:
            return pattern
    return None


def story_id(root: Path) -> Optional[str]:
    """The manifest is the sole source of story identity (AD-5); absent → None."""
    path = root / MANIFEST
    if not path.is_file():
        return None
    for line in path.read_text(encoding="utf-8-sig").splitlines():
        stripped = line.strip()
        if stripped.startswith("story_id") and ":" in stripped:
            value = parse_scalar(stripped.split(":", 1)[1])
            return value or None
    return None


def envelope(
    story: Optional[str], source: str, event_type: str, payload: dict[str, Any]
) -> dict[str, Any]:
    return {
        "story_id": story,
        "source": source,
        "type": event_type,
        "timestamp": datetime.now().astimezone().isoformat(timespec="seconds"),
        "payload": payload,
    }


def append_line(path: Path, line: str) -> None:
    """The literal AD-1 append: O_APPEND fd, one os.write of the whole line."""
    fd = os.open(str(path), os.O_APPEND | os.O_CREAT | os.O_WRONLY, 0o644)
    try:
        os.write(fd, line.encode("utf-8"))
    finally:
        os.close(fd)


def emit(
    source: str, event_type: str, payload: dict[str, Any], story_override: Optional[str] = None
) -> int:
    root = repo_root()
    story = story_override if story_override is not None else story_id(root)
    target = root / (EVENTS_FILE if story else PENDING_FILE)
    line = json.dumps(envelope(story, source, event_type, payload)) + "\n"
    last_error: Optional[BaseException] = None
    for attempt in range(ATTEMPTS):
        try:
            append_line(target, line)
            return 0
        except OSError as exc:
            last_error = exc
            if attempt < ATTEMPTS - 1:
                time.sleep(RETRY_DELAY_SECONDS)
    print(
        f"METRICS CAPTURE FAILED: {last_error} — event lost: {event_type}",
        file=sys.stderr,
    )
    return 1


def write_atomic_json(path: Path, data: dict[str, Any]) -> None:
    """Temp file -> flush -> fsync -> os.replace (project-context.md §2); never an in-place write."""
    tmp = path.with_suffix(path.suffix + ".tmp")
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(data, fh)
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, path)


def read_active_story(root: Path) -> Optional[dict[str, Any]]:
    path = root / ACTIVE_STORY_FILE
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def update_active_story(root: Path, incoming_story_id: Optional[str]) -> None:
    """AD-7: one pointer tracks the currently active story.

    Changing the pointer closes the outgoing story's time slice
    (`time.slice_closed`, with `duration_seconds`) and opens a new one for the
    incoming story (`time.slice_opened`). A story-id of None (nothing to
    attribute time to) or a story-id matching the current pointer is a no-op —
    mirrors AD-1b's "buffer/skip rather than corrupt" philosophy rather than
    writing a meaningless or duplicate slice.
    """
    if incoming_story_id is None:
        return

    current = read_active_story(root)
    if current is not None and current.get("story_id") == incoming_story_id:
        return

    now = _now()
    if current is not None:
        opened_at = current.get("opened_at")
        duration_seconds = None
        if opened_at:
            try:
                duration_seconds = (now - datetime.fromisoformat(opened_at)).total_seconds()
            except (ValueError, TypeError):
                duration_seconds = None
        emit(
            "time",
            "time.slice_closed",
            {
                "opened_at": opened_at,
                "closed_at": now.isoformat(timespec="seconds"),
                "duration_seconds": duration_seconds,
            },
            story_override=current.get("story_id"),
        )

    write_atomic_json(
        root / ACTIVE_STORY_FILE,
        {"story_id": incoming_story_id, "opened_at": now.isoformat(timespec="seconds")},
    )
    emit(
        "time",
        "time.slice_opened",
        {"opened_at": now.isoformat(timespec="seconds")},
        story_override=incoming_story_id,
    )


def record_activity(root: Path) -> None:
    """AD-7: a `PostToolUse`/prompt signal stamps the active slice's `last_activity_at`.

    Idle detection is retrospective, not a running timer (this architecture has
    no background service, AD-2): a gap exceeding `IDLE_THRESHOLD_SECONDS` since
    the previous activity signal is only discovered the next time an activity
    signal fires, and is recorded as `time.slice_paused` — the story stays
    active (no story_id change, no slice_closed/opened) since only Story 3.1's
    `update_active_story()` owns switching stories. No pointer means nothing to
    attribute time to, so this is a no-op.
    """
    current = read_active_story(root)
    if current is None:
        return

    now = _now()
    last_activity_raw = current.get("last_activity_at") or current.get("opened_at")
    if last_activity_raw:
        try:
            gap_seconds = (now - datetime.fromisoformat(last_activity_raw)).total_seconds()
        except (ValueError, TypeError):
            gap_seconds = None
        if gap_seconds is not None and gap_seconds > IDLE_THRESHOLD_SECONDS:
            emit(
                "time",
                "time.slice_paused",
                {
                    "quiet_since": last_activity_raw,
                    "resumed_at": now.isoformat(timespec="seconds"),
                    "idle_seconds": gap_seconds,
                },
                story_override=current.get("story_id"),
            )

    current["last_activity_at"] = now.isoformat(timespec="seconds")
    write_atomic_json(root / ACTIVE_STORY_FILE, current)


def is_session_active(root: Path) -> bool:
    return (root / ACTIVE_SESSION_FILE).is_file()


def mark_session_active(root: Path, session_id: Optional[str]) -> None:
    write_atomic_json(root / ACTIVE_SESSION_FILE, {"session_id": session_id})


def mark_session_inactive(root: Path) -> None:
    path = root / ACTIVE_SESSION_FILE
    try:
        path.unlink()
    except FileNotFoundError:
        pass


def repoint_active_story(root: Path, incoming_story_id: Optional[str]) -> None:
    """AD-7 precedence rule: a mid-session checkout re-points which story activity
    counts toward, without opening or closing a session-level slice - that stays
    exclusively `SessionStart`/`SessionEnd`'s job (`update_active_story()` /
    `close_active_story_slice()`). Only `story_id` changes; `opened_at` and any
    `last_activity_at` idle-tracking (Story 3.2) are preserved untouched, and no
    `time.*` event is emitted."""
    if incoming_story_id is None:
        return

    current = read_active_story(root)
    if current is None or current.get("story_id") == incoming_story_id:
        return

    current["story_id"] = incoming_story_id
    write_atomic_json(root / ACTIVE_STORY_FILE, current)


def close_active_story_slice(root: Path) -> None:
    """AD-7: a session-level slice only closes on `SessionEnd`. Emits
    `time.slice_closed` for whatever story the pointer currently names, then
    clears the pointer entirely - nothing should silently keep accruing time
    once the session that owned it has ended."""
    current = read_active_story(root)
    if current is None:
        return

    now = _now()
    opened_at = current.get("opened_at")
    duration_seconds = None
    if opened_at:
        try:
            duration_seconds = (now - datetime.fromisoformat(opened_at)).total_seconds()
        except (ValueError, TypeError):
            duration_seconds = None

    emit(
        "time",
        "time.slice_closed",
        {
            "opened_at": opened_at,
            "closed_at": now.isoformat(timespec="seconds"),
            "duration_seconds": duration_seconds,
        },
        story_override=current.get("story_id"),
    )

    try:
        (root / ACTIVE_STORY_FILE).unlink()
    except FileNotFoundError:
        pass
