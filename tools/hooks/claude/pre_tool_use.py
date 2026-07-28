#!/usr/bin/env python3
# /// script
# requires-python = ">=3.8"
# ///
"""pre_tool_use capture hook - emits `ai.claude-code.tool_start` (AD-1a/AD-10) via the shared emitter.

Returns 0 unconditionally: a non-zero Claude Code hook exit can block the
tool call or disrupt the session, and metrics capture must never do that
(the commit-msg precedent, extended). AD-9 visibility comes from the
emitter's stderr surfacing. Story 6.8 (below) is a narrow, deliberate
exception to this - it denies a *specific* tool call (the close command,
only for a JIRA-backed story with no ack marker yet) rather than disrupting
the session; it never blocks metrics capture itself.

Privacy guard: tool_input is NEVER emitted - tool arguments can carry secrets.

Story 5.8: also rewrites a matched Bash command (opt-in via
`.story-config.yaml`'s `test_commands`/`build_commands`, same patterns Story
5.4 introduced) to append a harmless `; printf '\\n%s:%s\\n' MARKER "$?"`
suffix, via Claude Code's documented PreToolUse `updatedInput` mechanism.
This doesn't change what the command does - it only makes its exit code
observable in stdout, which post_tool_use.py then parses back out. Needed
because Claude Code's PostToolUse payload never exposes a structured exit
code for Bash (a confirmed platform gap, not something this project's own
code can fix around any other way - see _events.py's DEFECT_EXIT_MARKER
docstring). Absent config = unchanged behavior (no rewrite ever happens).

Story 6.8: also denies the two story-close commands outright for a
JIRA-backed story until `.claude/skills/story-close/SKILL.md` has actually
run (its own step 6 creates a single-use `.story-close-ack` marker
immediately before running the close command). Fixes a real gap found in
live pilot testing (GitHub #52): pasting the raw close command in chat ran
it directly via Bash with zero JIRA sync, since story-close's implicit
trigger depends on the model recognizing intent rather than a deterministic
interceptor. `permissionDecisionReason` is UI-only - the assistant model
never sees it - so the redirect instructions live in `additionalContext`,
the field Claude Code actually surfaces to the model (confirmed against
Claude Code's real hook documentation during this story's authoring, not
assumed). This is a reliability nudge, not tamper-proofing: the hook can
only see that the marker file exists, not that story-close's own steps were
genuinely followed."""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(
    0, str(Path(__file__).resolve().parents[1])
)  # bridge to the shared emitter (spine-sanctioned, Story 2.3)
import _events

CLOSE_ACK_MARKER = ".story-close-ack"


def _is_close_command(command: str) -> bool:
    """The two commands story-close's own step 6 always runs, last.
    --dry-run never gates - it's side-effect-free and touches no JIRA state
    (Story 6.8, AC 4)."""
    if "--dry-run" in command:
        return False
    if "tools/snapshot-assembler/main.py" in command:
        return True
    return "tools/opsx-wrapper/main.py" in command and "archive" in command


def _jira_backed(root: Path) -> bool:
    manifest = _events.read_manifest(root)
    return manifest.get("source_of_truth") == "jira" and bool(manifest.get("jira_issue_key"))


def main(argv: list[str] | None = None) -> int:
    data = _events.read_stdin_json()
    _events.emit(
        "ai",
        "ai.claude-code.tool_start",
        {"session_id": data.get("session_id"), "tool_name": data.get("tool_name")},
    )

    if data.get("tool_name") == "Bash":
        tool_input = data.get("tool_input") or {}
        command = tool_input.get("command", "")
        root = _events.repo_root()

        if command and _is_close_command(command) and _jira_backed(root):
            ack_path = root / CLOSE_ACK_MARKER
            if ack_path.is_file():
                ack_path.unlink()  # single-use - never left behind to wave through a future close
            else:
                print(
                    json.dumps(
                        {
                            "hookSpecificOutput": {
                                "hookEventName": "PreToolUse",
                                "permissionDecision": "deny",
                                "permissionDecisionReason": (
                                    "This story is JIRA-backed - the story-close skill must run "
                                    "first so its sub-tasks and parent ticket sync to Done."
                                ),
                                "additionalContext": (
                                    "This command closes a JIRA-backed story. Before running it, "
                                    "follow .claude/skills/story-close/SKILL.md in full (discovery, "
                                    "the one confirmation, and the JIRA writes) - its own step 6 "
                                    "creates the required .story-close-ack marker immediately "
                                    "before running this exact command. Do that now, then retry "
                                    "this exact command."
                                ),
                            }
                        }
                    )
                )
                return 0

        config = _events.read_story_config(root)
        patterns = _events.split_config_patterns(
            config.get("test_commands", "")
        ) + _events.split_config_patterns(config.get("build_commands", ""))

        if command and _events.matched_config_pattern(command, patterns) is not None:
            rewritten = command + (f"; printf '\\n{_events.DEFECT_EXIT_MARKER}:%s\\n' \"$?\"")
            print(
                json.dumps(
                    {
                        "hookSpecificOutput": {
                            "hookEventName": "PreToolUse",
                            "permissionDecision": "allow",
                            "updatedInput": {**tool_input, "command": rewritten},
                        }
                    }
                )
            )

    return 0


if __name__ == "__main__":
    sys.exit(main())
