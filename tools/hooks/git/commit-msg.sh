#!/bin/sh
# installed by explore-jira-ai-metrics setup-hooks (AD-8) - do not edit; source of truth: tools/hooks/git/
# Story 2.8: commit-msg.py already guarantees exit 0 in all its own logic
# paths - but if `uv` itself isn't on this process's PATH (minimal-PATH GUI
# git clients), the shell fails before Python ever runs, and git treats a
# non-zero commit-msg exit as a real abort signal. Guard against that
# specifically here, visibly (AD-9), never silently.
if command -v uv >/dev/null 2>&1; then
    uv run tools/hooks/git/commit-msg.py "$@"
else
    echo "warning: uv not found on PATH - metrics capture skipped for this commit (commit not blocked)" >&2
fi
exit 0
