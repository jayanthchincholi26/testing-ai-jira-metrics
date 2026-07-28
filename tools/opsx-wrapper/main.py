#!/usr/bin/env python3
# /// script
# requires-python = ">=3.8"
# ///
"""opsx CLI wrapper — intercepts `archive` to close the metrics loop (NFR1: wrap, never modify).

Any subcommand other than `archive` is a pure passthrough to the underlying
openspec/opsx CLI, exit code mirrored, no capture. On a SUCCESSFUL `archive`:
emit an `opsx.archive` event (the third producer family) and invoke the
snapshot assembler.

Exit-code table:
- passthrough → mirrors the underlying CLI, always
- failed archive → mirrored, NO event, NO snapshot (a failed archive is not a close)
- underlying CLI missing on archive → visible note, capture proceeds anyway
- successful archive but snapshot failure → exit 1, loudly (a close without a
  snapshot is exactly what must never pass silently — AD-9 philosophy)
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path
from typing import Optional

sys.path.insert(
    0, str(Path(__file__).resolve().parents[1] / "hooks")
)  # bridge to the shared emitter (spine-sanctioned)
import _events

ASSEMBLER = Path(__file__).resolve().parents[1] / "snapshot-assembler" / "main.py"


def find_cli() -> Optional[str]:
    return shutil.which("openspec") or shutil.which("opsx")


def main(argv: "list[str] | None" = None) -> int:
    args = list(argv) if argv is not None else sys.argv[1:]
    subcommand = next((a for a in args if not a.startswith("-")), None)
    cli = find_cli()

    if subcommand != "archive":
        if cli is None:
            print("error: no openspec/opsx CLI found on PATH", file=sys.stderr)
            return 2
        return subprocess.run([cli, *args]).returncode

    if cli is None:
        print(
            "note: no openspec/opsx CLI found — capture proceeding without passthrough",
            file=sys.stderr,
        )
    else:
        returncode = subprocess.run([cli, *args]).returncode
        if returncode != 0:
            return returncode

    _events.emit("opsx", "opsx.archive", {"args": args})

    root = _events.repo_root()
    result = subprocess.run(["uv", "run", str(ASSEMBLER), "--repo-root", str(root)])
    if result.returncode != 0:
        print(
            "error: archive succeeded but the snapshot could not be produced — "
            "fix and re-run the assembler; this must not pass silently",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
