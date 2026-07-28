#!/usr/bin/env python3
# /// script
# requires-python = ">=3.8"
# ///
"""source-of-truth config resolver — which kickoff backend applies to this project.

Implements AD-4's declare-once rule: a project states `source_of_truth:
jira | confluence | docs-only` a single time in `.story-config.yaml` at the
repo root (flat YAML, committed), and the story-kickoff skill reads it via
this script instead of ever asking the developer per story. An absent file or
key defaults to docs-only (Story 1.1's behavior). A declared-but-unbuilt
backend would be reported with `implemented: false` so the caller can surface
it honestly — never a silent docs-only fallback, and never treated as invalid
config. As of Story 1.4 all three backends are implemented.

Story 1.5: the same config declares `ai_tool` (default claude-code — the only
implemented capture adapter, AD-10). The value must be a lowercase token
because it becomes the `ai.<tool>.*` event-namespace segment (AD-1a); an
unimplemented-but-valid tool resolves with `ai_tool_implemented: false`.

Read-only: this script never writes or creates any file.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

CONFIG = ".story-config.yaml"
BACKENDS = ("jira", "confluence", "docs-only")
IMPLEMENTED = ("docs-only", "jira", "confluence")
DEFAULT_AI_TOOL = "claude-code"
AI_TOOLS_IMPLEMENTED = ("claude-code",)
AI_TOOL_FORMAT = re.compile(r"^[a-z][a-z0-9-]*$")


def parse_scalar(raw: str) -> str:
    """One flat-YAML scalar: a paired quote (single or double) wins and shields any `#`
    inside it; a bare value ends at the first ` #` inline comment."""
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


def read_config(path: Path) -> dict[str, str]:
    """Flat YAML by hand (stdlib-only rule), one `key: value` per line.

    utf-8-sig: Windows editors and PowerShell 5.1 commonly write a UTF-8 BOM; without
    stripping it the first key silently fails to match and the declared backend is lost.
    """
    config: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8-sig").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or ":" not in stripped:
            continue
        key, raw = stripped.split(":", 1)
        config[key.strip()] = parse_scalar(raw)
    return config


def ack(payload: dict[str, Any]) -> int:
    print(json.dumps(payload))
    return 0


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
        help="repository root; the config is {repo-root}/.story-config.yaml",
    )
    args = p.parse_args(argv)

    root = Path(args.repo_root)
    if not root.is_dir():
        return fail(f"--repo-root {args.repo_root!r} is not a directory")

    path = root / CONFIG
    config = read_config(path) if path.exists() else {}

    source = config.get("source_of_truth")
    if source is not None and source not in BACKENDS:
        return fail(f"source_of_truth {source!r} in {path} is not one of: {', '.join(BACKENDS)}")

    ai_tool = config.get("ai_tool")
    if ai_tool is not None and not AI_TOOL_FORMAT.match(ai_tool):
        return fail(
            f"ai_tool {ai_tool!r} in {path} must match [a-z][a-z0-9-]* — "
            "it becomes the ai.<tool>.* event namespace (AD-1a/AD-10)"
        )

    return ack(
        {
            "ok": True,
            "source_of_truth": source or "docs-only",
            "declared": source is not None,
            "implemented": (source or "docs-only") in IMPLEMENTED,
            "config": str(path.resolve()) if path.exists() else None,
            "ai_tool": ai_tool or DEFAULT_AI_TOOL,
            "ai_tool_declared": ai_tool is not None,
            "ai_tool_implemented": (ai_tool or DEFAULT_AI_TOOL) in AI_TOOLS_IMPLEMENTED,
        }
    )


if __name__ == "__main__":
    sys.exit(main())
