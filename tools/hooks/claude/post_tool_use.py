#!/usr/bin/env python3
# /// script
# requires-python = ">=3.8"
# ///
"""post_tool_use capture hook - emits `ai.claude-code.tool_use` (AD-1a/AD-10) via the shared emitter.

Returns 0 unconditionally: a non-zero Claude Code hook exit can block the
tool call or disrupt the session, and metrics capture must never do that
(the commit-msg precedent, extended). AD-9 visibility comes from the
emitter's stderr surfacing.

Privacy guard: tool_input is NEVER emitted - tool arguments can carry secrets.
Story 5.4 extends this hook to detect compile/test defects, and keeps this
guard: only a matched pattern NAME (never the actual command text, stdout,
or stderr) is ever written to an event payload.

Also records activity for AD-7's idle-detection (Story 3.2): this is one of
the two signals (with `user_prompt_submit`) that can reveal - in arrears -
that the active slice has been idle past the threshold.

Story 5.4: compile/test defect capture, fully automatic, opt-in via
`.story-config.yaml`'s `test_commands`/`build_commands` (comma-separated
patterns, e.g. `test_commands: pytest, npm test`). A Bash tool call whose
command contains a configured pattern AND exits non-zero appends
`ai.claude-code.defect_test`/`ai.claude-code.defect_compile` - kept within
the existing `ai.<tool>.*` namespace family (AD-1a) rather than a new
top-level `defect.*` namespace. Absent config = unchanged behavior (no new
event ever emitted). MCP tools (e.g. creating a Jira subtask) are NOT
reachable from here - hooks are subprocesses, not a live assistant turn -
so these defects stay local-only by design (see Story 5.4's Dev Notes).

Story 5.7 found the exit code isn't where the docs said (top-level vs
nested) - Story 5.8 then found Claude Code's PostToolUse payload has NO
structured exit-code field at all, for any Bash call, ever (a confirmed
platform gap: anthropics/claude-code#33656, rohitg00/agentmemory#539). This
hook now instead parses the `_events.DEFECT_EXIT_MARKER` value that
pre_tool_use.py injects into the command (via its own `updatedInput`
rewrite) back out of `tool_response.stdout` - never a structured field
Claude Code doesn't actually provide. If no marker is present (e.g. the
pre_tool_use rewrite didn't apply for some reason), this degrades silently
to no defect capture, exactly like absent config does - never a crash."""

from __future__ import annotations

import re
import sys
from pathlib import Path

sys.path.insert(
    0, str(Path(__file__).resolve().parents[1])
)  # bridge to the shared emitter (spine-sanctioned, Story 2.3)
import _events

EXIT_MARKER_RE = re.compile(re.escape(_events.DEFECT_EXIT_MARKER) + r":(-?\d+)")


def extract_exit_code(stdout: str) -> "int | None":
    """The marker may appear more than once if the command itself prints
    something that happens to look similar; the LAST match is the one our
    own injected suffix produced, since it always runs last."""
    matches = EXIT_MARKER_RE.findall(stdout or "")
    if not matches:
        return None
    try:
        return int(matches[-1])
    except ValueError:
        return None


def main(argv: list[str] | None = None) -> int:
    data = _events.read_stdin_json()
    _events.emit(
        "ai",
        "ai.claude-code.tool_use",
        {"session_id": data.get("session_id"), "tool_name": data.get("tool_name")},
    )

    if data.get("tool_name") == "Bash":
        root = _events.repo_root()
        config = _events.read_story_config(root)
        command = data.get("tool_input", {}).get("command", "")
        stdout = (data.get("tool_response") or {}).get("stdout", "")
        exit_code = extract_exit_code(stdout)

        if exit_code not in (None, 0):
            for config_key, event_type in (
                ("test_commands", "ai.claude-code.defect_test"),
                ("build_commands", "ai.claude-code.defect_compile"),
            ):
                patterns = _events.split_config_patterns(config.get(config_key, ""))
                pattern = _events.matched_config_pattern(command, patterns)
                if pattern is not None:
                    _events.emit("ai", event_type, {"matched_pattern": pattern})
                    break  # test_commands wins over build_commands on a double match

    _events.record_activity(_events.repo_root())
    return 0


if __name__ == "__main__":
    sys.exit(main())
