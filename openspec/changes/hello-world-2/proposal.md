## Why

The existing hello world program (`src/index.ts`) uses a plain `console.log` call, which satisfies the happy path but doesn't fully meet `hello-world-2.md`'s reliability requirements: it has no explicit success/failure exit code handling and no graceful handling of a blocked stdout stream (FR-002, NFR 4.2). This change brings the implementation into full compliance with that spec and adds automated test coverage matching its verification matrix (TC-001, TC-002).

## What Changes

- Replace the implicit `console.log` call with an explicit `process.stdout.write('Hello, World!\n')` call, so the output string and its termination (UTF-8, newline) are controlled directly rather than left to `console.log`'s formatting.
- Add explicit exit-code handling: exit `0` on a successful write, exit `1` if the write reports an error (covers stdout-blocking / fault-tolerance per NFR 4.2).
- Add automated tests covering TC-001 (stdout matches `^Hello, World!\n$`) and TC-002 (process exits with status `0`).

## Capabilities

### New Capabilities
- `hello-world-output`: deterministic stdout "Hello, World!" output with explicit success/failure exit-code handling, per `hello-world-2.md`'s functional and non-functional requirements.

### Modified Capabilities
(none — no existing specs are being changed)

## Impact

- `src/index.ts`: rewritten to add explicit write/exit handling.
- New test file (e.g. `src/index.test.ts` or equivalent) covering TC-001/TC-002.
- `package.json`: adds a test script and a test runner dependency if one isn't already present.
