#!/usr/bin/env python3
# /// script
# requires-python = ">=3.8"
# ///
"""JIRA source-of-truth adapter — fetches kickoff fields from a JIRA issue.

The jira backend of the AD-4 adapter contract: fetch-only. It returns the
normalized {points, goal, sprint, description} shape as a one-line JSON ack
and never writes any file — the story-kickoff skill passes confirmed values
to the manifest writer (tools/adapters/docs-only/main.py --source-of-truth
jira). Credentials come from JIRA_BASE_URL / JIRA_EMAIL / JIRA_API_TOKEN at
call time only and are never persisted or echoed (NFR4).

Story points and sprint live in per-instance custom fields; `.story-config.yaml`
may override the common Jira Cloud defaults via `jira_points_field` /
`jira_sprint_field`. A field the issue doesn't carry is returned as null,
never invented — the kickoff skill resolves nulls with the developer.
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import re
import sys
from pathlib import Path
from typing import Any, Optional
from urllib import error, parse, request

CONFIG = ".story-config.yaml"
DEFAULT_POINTS_FIELD = "customfield_10016"
DEFAULT_SPRINT_FIELD = "customfield_10020"
ENV_VARS = ("JIRA_BASE_URL", "JIRA_EMAIL", "JIRA_API_TOKEN")
TIMEOUT_SECONDS = 15


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
    """Flat YAML by hand (stdlib-only rule); utf-8-sig strips the BOM Windows tools write."""
    config: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8-sig").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or ":" not in stripped:
            continue
        key, raw = stripped.split(":", 1)
        config[key.strip()] = parse_scalar(raw)
    return config


def fetch(base_url: str, email: str, token: str, issue: str, fields: list[str]) -> dict:
    url = f"{base_url.rstrip('/')}/rest/api/2/issue/{parse.quote(issue, safe='')}?fields={','.join(fields)}"
    auth = base64.b64encode(f"{email}:{token}".encode("utf-8")).decode("ascii")
    req = request.Request(
        url, headers={"Authorization": f"Basic {auth}", "Accept": "application/json"}
    )
    with request.urlopen(req, timeout=TIMEOUT_SECONDS) as resp:
        return json.loads(resp.read().decode("utf-8"))


def extract_points(value: Any) -> Optional[float]:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"points field holds a non-numeric value: {value!r}")
    if isinstance(value, float) and value.is_integer():
        return int(value)
    return value


def _select_sprint_item(value: Any) -> Any:
    """The active sprint object wins; otherwise the last one. Shared by extract_sprint()
    (name) and extract_sprint_dates() (Story 6.5, start/end dates) so both agree on which
    sprint an issue "belongs to" - an issue can carry multiple sprint entries (its full
    history), confirmed via live research, so this selection must never be duplicated
    and risk drifting between the two extractors."""
    if not isinstance(value, list) or not value:
        return None
    chosen = None
    for item in value:
        if isinstance(item, dict) and str(item.get("state", "")).lower() == "active":
            chosen = item
            break
    return chosen if chosen is not None else value[-1]


def extract_sprint(value: Any) -> Optional[str]:
    """The sprint custom field appears in three real-world shapes: a list of sprint
    objects (active one wins, else the last), legacy Greenhopper strings, or a plain string."""
    if value is None:
        return None
    if isinstance(value, str):
        return value
    item = _select_sprint_item(value)
    if isinstance(item, dict):
        name = item.get("name")
        return str(name) if name is not None else None
    if isinstance(item, str):
        match = re.search(r"name=([^,\]]+)", item)
        return match.group(1) if match else item
    return None


def extract_sprint_dates(value: Any) -> "tuple[Optional[str], Optional[str]]":
    """(start_date, end_date) from the same chosen sprint item extract_sprint() uses
    (Story 6.5). Real-world confirmed: a "future" sprint (not yet started) carries no
    startDate/endDate keys at all - .get() returns None for both, never a KeyError.
    Legacy Greenhopper strings and plain-string values carry no structured dates -
    both null, never invented (AD-10)."""
    item = _select_sprint_item(value)
    if not isinstance(item, dict):
        return None, None
    return item.get("startDate"), item.get("endDate")


def normalize(payload: Any, points_field: str, sprint_field: str) -> dict[str, Any]:
    """Validate the untrusted API response shape before anything downstream sees it."""
    if not isinstance(payload, dict) or not isinstance(payload.get("fields"), dict):
        raise ValueError("response has no 'fields' object")
    fields = payload["fields"]
    summary = fields.get("summary")
    if not isinstance(summary, str) or not summary.strip():
        raise ValueError("issue has no usable summary")
    description = fields.get("description")
    if description is not None and not isinstance(description, str):
        raise ValueError("description is not plain text (is the instance on API v2?)")
    sprint_start_date, sprint_end_date = extract_sprint_dates(fields.get(sprint_field))
    return {
        "points": extract_points(fields.get(points_field)),
        "goal": summary,
        "sprint": extract_sprint(fields.get(sprint_field)),
        "sprint_start_date": sprint_start_date,
        "sprint_end_date": sprint_end_date,
        "description": description,
    }


def fail(message: str) -> int:
    print(f"error: {message}", file=sys.stderr)
    return 2


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    p.add_argument(
        "--repo-root", required=True, help="repository root (locates .story-config.yaml)"
    )
    p.add_argument("--issue", required=True, help="JIRA issue key, e.g. PROJ-123")
    args = p.parse_args(argv)

    root = Path(args.repo_root)
    if not root.is_dir():
        return fail(f"--repo-root {args.repo_root!r} is not a directory")

    missing = [name for name in ENV_VARS if not os.environ.get(name)]
    if missing:
        return fail(f"missing environment variable(s): {', '.join(missing)}")
    base_url, email, token = (os.environ[name] for name in ENV_VARS)

    config_path = root / CONFIG
    config = read_config(config_path) if config_path.exists() else {}
    points_field = config.get("jira_points_field", DEFAULT_POINTS_FIELD)
    sprint_field = config.get("jira_sprint_field", DEFAULT_SPRINT_FIELD)

    try:
        payload = fetch(
            base_url,
            email,
            token,
            args.issue,
            ["summary", "description", points_field, sprint_field],
        )
    except error.HTTPError as exc:
        if exc.code in (401, 403):
            return fail(
                f"JIRA rejected the request (HTTP {exc.code}) — check JIRA_EMAIL and JIRA_API_TOKEN"
            )
        if exc.code == 404:
            return fail(f"issue {args.issue!r} not found at {base_url}")
        return fail(f"JIRA request failed (HTTP {exc.code})")
    except error.URLError as exc:
        return fail(f"could not reach JIRA at {base_url}: {exc.reason}")
    except json.JSONDecodeError:
        return fail("JIRA returned a non-JSON response")

    try:
        normalized = normalize(payload, points_field, sprint_field)
    except ValueError as exc:
        return fail(f"unexpected JIRA response shape: {exc}")

    print(json.dumps({"ok": True, **normalized}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
