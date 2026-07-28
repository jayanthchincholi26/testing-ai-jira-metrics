#!/usr/bin/env python3
# /// script
# requires-python = ">=3.8"
# ///
"""setup-hooks — the single committed installer for all capture hooks (AD-8).

Copies the git-tracked shims in tools/hooks/git/ into .git/hooks/ and merges
the six Claude Code hook entries (SessionStart, SessionEnd, PreToolUse,
PostToolUse, Stop, UserPromptSubmit) into .claude/settings.json, each pointing
at tools/hooks/claude/*.py via `uv run`. Runs once per clone; re-running is
idempotent (our own installs are upgraded in place, nothing duplicates).

Safety rules: a pre-existing .git/hooks file WITHOUT our marker is somebody
else's hook — the installer refuses (exit 2) and touches nothing. A malformed
settings.json is likewise refused, never clobbered. All validation happens
before any write, and every write is atomic.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any

sys.path.insert(
    0, str(Path(__file__).resolve().parent / "hooks")
)  # bridge to the shared git_out() helper
import _events  # noqa: E402 (path must be set up first)

MARKER = "installed by explore-jira-ai-metrics setup-hooks"
GIT_HOOKS = ("post-commit", "post-checkout", "post-merge", "commit-msg")
CLAUDE_EVENTS = {
    "SessionStart": "session_start.py",
    "SessionEnd": "session_end.py",
    "PreToolUse": "pre_tool_use.py",
    "PostToolUse": "post_tool_use.py",
    "Stop": "stop.py",
    "UserPromptSubmit": "user_prompt_submit.py",
}
GITIGNORE_ENTRIES = (
    ".story-events.jsonl",
    ".story-events.pending.jsonl",
    ".active-story",
    ".active-claude-session",
    ".story-close-ack",  # Story 6.8: single-use PreToolUse gate marker, never committed
)


def write_atomic(path: Path, text: str) -> None:
    """Temp + flush + fsync + atomic rename, so a crash never half-writes a file."""
    tmp = path.with_suffix(path.suffix + ".tmp")
    with open(tmp, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


def command_for(root: Path, script: str) -> str:
    """Absolute, quoted path (Story 2.7) — a relative path breaks the moment a
    session cd's elsewhere, since Claude Code's hook invocation reuses whatever
    cwd the session has drifted to rather than always using the repo root.
    Quoting guards against a repo root containing spaces; .as_posix() keeps
    forward slashes for cross-platform consistency."""
    abs_path = (root / "tools" / "hooks" / "claude" / script).resolve()
    return f'uv run "{abs_path.as_posix()}"'


def references_our_script(command: str, script: str) -> bool:
    """True if `command` invokes `script` specifically — the old relative form,
    the new absolute form, or either quoted/padded with incidental whitespace —
    never merely a *substring* collision (PR #22 review): a hand-added hook
    like `my_backstop.py` must never be mistaken for our `stop.py` just
    because the filename happens to end the same way. Requires a path
    boundary (`/`) immediately before the script name, or an exact match."""
    cleaned = command.strip().rstrip('"').strip().replace("\\", "/")
    return cleaned == script or cleaned.endswith("/" + script)


def merge_settings(root: Path, settings: dict[str, Any]) -> dict[str, Any]:
    """Additively wire our six events; user keys and user hook entries are
    preserved. An existing entry for one of our six scripts — old relative-path
    form or current absolute form — is upgraded in place rather than
    duplicated (Story 2.7)."""
    hooks = settings.setdefault("hooks", {})
    for event, script in CLAUDE_EVENTS.items():
        entries = hooks.setdefault(event, [])
        wanted = command_for(root, script)
        upgraded = False
        for entry in entries:
            for hook in entry.get("hooks", []):
                if references_our_script(hook.get("command", ""), script):
                    hook["command"] = wanted
                    upgraded = True
        if not upgraded:
            entries.append({"hooks": [{"type": "command", "command": wanted}]})
    return settings


def ensure_gitignore(root: Path) -> None:
    """Append any of GITIGNORE_ENTRIES missing from .gitignore (Story 2.11);
    create the file if absent. Prevents .story-events.jsonl et al from ever
    being git-tracked, which silently forks/discards captured events the
    moment two story branches diverge and checkout swaps between them.

    Matching is whitespace-tolerant and recognizes a leading-slash-anchored
    form (e.g. `/.story-events.jsonl`) as already covering an entry, so an
    existing hand-written rule isn't redundantly duplicated (review finding,
    PR #23)."""
    path = root / ".gitignore"
    if path.exists() and not path.is_file():
        print(
            f"warning: {path} exists but is not a regular file — skipping .gitignore enforcement",
            file=sys.stderr,
        )
        return
    existing = path.read_text(encoding="utf-8-sig").splitlines() if path.is_file() else []
    covered = {line.strip() for line in existing}
    missing = [
        entry for entry in GITIGNORE_ENTRIES if entry not in covered and f"/{entry}" not in covered
    ]
    if not missing:
        return
    text = "\n".join(existing + missing) + "\n"
    write_atomic(path, text)


def tracked_capture_files(root: Path) -> list[str]:
    """Which of GITIGNORE_ENTRIES is already git-tracked (Story 2.11) — a stale
    commit predating this fix, the exact situation that caused silent
    cross-branch event-log forking in live pilot testing. `git_out()` already
    degrades to None on any failure (not a repo, git unavailable, timeout);
    treat that the same as "not tracked" rather than blocking the install.

    One batched `git ls-files` call rather than one subprocess per entry
    (review finding, PR #23) — `git ls-files -- <paths...>` exits 0 and prints
    just the subset that's actually tracked, never failing for an untracked
    path in the list."""
    result = _events.git_out("ls-files", "--", *GITIGNORE_ENTRIES, cwd=root)
    if not result:
        return []
    tracked_lines = set(result.splitlines())
    return [entry for entry in GITIGNORE_ENTRIES if entry in tracked_lines]


def fail(message: str) -> int:
    print(f"error: {message}", file=sys.stderr)
    return 2


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    p.add_argument("--repo-root", required=True, help="root of the clone to install into")
    args = p.parse_args(argv)

    root = Path(args.repo_root).resolve()
    if not (root / ".git").is_dir():
        return fail(f"{root} is not a git clone (no .git directory)")
    sources = {name: root / "tools" / "hooks" / "git" / f"{name}.sh" for name in GIT_HOOKS}
    missing = [name for name, src in sources.items() if not src.is_file()]
    if missing:
        return fail(f"tracked hook source(s) missing under tools/hooks/git/: {', '.join(missing)}")

    hooks_dir = root / ".git" / "hooks"
    hooks_dir.mkdir(parents=True, exist_ok=True)

    def is_conflict(target: Path) -> bool:
        """Anything we can't prove is ours: a non-file (e.g. a directory) or a
        file without our marker."""
        if not target.exists():
            return False
        if not target.is_file():
            return True
        return MARKER not in target.read_text(encoding="utf-8", errors="replace")

    conflicts = [name for name in GIT_HOOKS if is_conflict(hooks_dir / name)]
    if conflicts:
        return fail(
            f"existing hook(s) not installed by this tool: {', '.join(conflicts)} — "
            "move them aside (or chain them manually) and re-run"
        )

    settings_path = root / ".claude" / "settings.json"
    settings: dict[str, Any] = {}
    if settings_path.exists():
        try:
            settings = json.loads(settings_path.read_text(encoding="utf-8-sig"))
        except json.JSONDecodeError as exc:
            return fail(f"{settings_path} is not valid JSON ({exc}) — fix it and re-run")

    for name in GIT_HOOKS:
        target = hooks_dir / name
        write_atomic(target, sources[name].read_text(encoding="utf-8"))
        os.chmod(target, 0o755)

    settings_path.parent.mkdir(parents=True, exist_ok=True)
    write_atomic(settings_path, json.dumps(merge_settings(root, settings), indent=2) + "\n")

    ensure_gitignore(root)
    for tracked in tracked_capture_files(root):
        print(
            f"warning: {tracked} is tracked by git; this can silently fork your event log "
            f"across story branches — run: git rm --cached {tracked}",
            file=sys.stderr,
        )

    print(
        json.dumps(
            {
                "ok": True,
                "git_hooks": list(GIT_HOOKS),
                "settings": str(settings_path.resolve()),
                "events_wired": list(CLAUDE_EVENTS),
            }
        )
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
