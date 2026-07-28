#!/usr/bin/env python3
# /// script
# requires-python = ">=3.8"
# ///
"""Confluence source-of-truth adapter — fetches kickoff fields from a Confluence page.

The confluence backend of the AD-4 adapter contract: fetch-only, returning the
same normalized {points, goal, sprint, description} shape as the jira adapter
and never writing any file. Credentials come from CONFLUENCE_BASE_URL /
CONFLUENCE_EMAIL / CONFLUENCE_API_TOKEN at call time only and are never
persisted or echoed (NFR4). CONFLUENCE_BASE_URL includes the site prefix
(e.g. https://org.atlassian.net/wiki for Cloud).

Confluence pages carry no native points/sprint fields, so the pilot convention
is page labels: `points-<number>` and `sprint-<name>` (e.g. points-5,
sprint-2026-Q3-S4). goal comes from the page title; description from the
HTML-stripped body, truncated to 500 chars. Anything absent is returned as
null, never invented — the kickoff skill elicits missing fields and always
confirms points with the developer.
"""

from __future__ import annotations

import argparse
import base64
import html
import json
import os
import re
import sys
from pathlib import Path
from typing import Any, Optional
from urllib import error, parse, request

ENV_VARS = ("CONFLUENCE_BASE_URL", "CONFLUENCE_EMAIL", "CONFLUENCE_API_TOKEN")
EXPAND = "body.storage,metadata.labels"
DESCRIPTION_LIMIT = 500
TIMEOUT_SECONDS = 15


def fetch(base_url: str, email: str, token: str, page: str) -> dict:
    url = (
        f"{base_url.rstrip('/')}/rest/api/content/{parse.quote(page, safe='')}"
        f"?expand={parse.quote(EXPAND, safe='.,')}"
    )
    auth = base64.b64encode(f"{email}:{token}".encode("utf-8")).decode("ascii")
    req = request.Request(
        url, headers={"Authorization": f"Basic {auth}", "Accept": "application/json"}
    )
    with request.urlopen(req, timeout=TIMEOUT_SECONDS) as resp:
        return json.loads(resp.read().decode("utf-8"))


def labels_of(payload: dict) -> list[str]:
    results = (payload.get("metadata") or {}).get("labels", {}).get("results", [])
    if not isinstance(results, list):
        return []
    return [item["name"] for item in results if isinstance(item, dict) and "name" in item]


def extract_points(labels: list[str]) -> Optional[float]:
    """First valid `points-<number>` label wins; malformed ones are human typos, skipped."""
    for name in labels:
        if name.startswith("points-"):
            try:
                value = float(name[len("points-") :])
            except ValueError:
                continue
            return int(value) if value.is_integer() else value
    return None


def extract_sprint(labels: list[str]) -> Optional[str]:
    """First non-empty `sprint-<name>` label wins; a bare `sprint-` typo is skipped."""
    for name in labels:
        if name.startswith("sprint-"):
            remainder = name[len("sprint-") :]
            if remainder:
                return remainder
    return None


def strip_html(markup: str) -> Optional[str]:
    text = " ".join(html.unescape(re.sub(r"<[^>]+>", " ", markup)).split())
    return text[:DESCRIPTION_LIMIT] or None


def normalize(payload: Any) -> dict[str, Any]:
    """Validate the untrusted API response shape before anything downstream sees it."""
    if not isinstance(payload, dict):
        raise ValueError("response is not a JSON object")
    title = payload.get("title")
    if not isinstance(title, str) or not title.strip():
        raise ValueError("page has no usable title")
    body_value = ((payload.get("body") or {}).get("storage") or {}).get("value")
    labels = labels_of(payload)
    return {
        "points": extract_points(labels),
        "goal": title,
        "sprint": extract_sprint(labels),
        "description": strip_html(body_value) if isinstance(body_value, str) else None,
    }


def fail(message: str) -> int:
    print(f"error: {message}", file=sys.stderr)
    return 2


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    p.add_argument("--repo-root", required=True, help="repository root")
    p.add_argument("--page", required=True, help="Confluence content id, e.g. 123456")
    args = p.parse_args(argv)

    root = Path(args.repo_root)
    if not root.is_dir():
        return fail(f"--repo-root {args.repo_root!r} is not a directory")

    missing = [name for name in ENV_VARS if not os.environ.get(name)]
    if missing:
        return fail(f"missing environment variable(s): {', '.join(missing)}")
    base_url, email, token = (os.environ[name] for name in ENV_VARS)

    try:
        payload = fetch(base_url, email, token, args.page)
    except error.HTTPError as exc:
        if exc.code in (401, 403):
            return fail(
                f"Confluence rejected the request (HTTP {exc.code}) — "
                "check CONFLUENCE_EMAIL and CONFLUENCE_API_TOKEN"
            )
        if exc.code == 404:
            return fail(f"page {args.page!r} not found at {base_url}")
        return fail(f"Confluence request failed (HTTP {exc.code})")
    except error.URLError as exc:
        return fail(f"could not reach Confluence at {base_url}: {exc.reason}")
    except json.JSONDecodeError:
        return fail("Confluence returned a non-JSON response")

    try:
        normalized = normalize(payload)
    except ValueError as exc:
        return fail(f"unexpected Confluence response shape: {exc}")

    print(json.dumps({"ok": True, **normalized}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
