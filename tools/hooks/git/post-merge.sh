#!/bin/sh
# installed by explore-jira-ai-metrics setup-hooks (AD-8) - do not edit; source of truth: tools/hooks/git/
# Story 2.8: git already ignores this hook's exit code (post-merge is
# advisory only), so a missing `uv` was never able to block anything here -
# this check exists purely to replace a raw "command not found" stderr line
# with a clearer message, consistent with commit-msg.sh's guard.
if command -v uv >/dev/null 2>&1; then
    uv run tools/hooks/git/post-merge.py "$@"
else
    echo "warning: uv not found on PATH - metrics capture skipped for this post-merge hook" >&2
fi
