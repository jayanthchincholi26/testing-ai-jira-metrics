## Context

The current implementation (`src/index.ts` from the prior "Hello world! using TypeScript" story) is a single `console.log('Hello, World!')` call. It satisfies the happy path but gives no hook for the exit-code and fault-tolerance requirements in `hello-world-2.md` (FR-002, NFR 4.2): `console.log` doesn't surface write failures, and the process's exit code is left entirely to Node's default behavior.

## Goals / Non-Goals

**Goals:**
- Print `Hello, World!\n` to stdout deterministically, in UTF-8.
- Exit `0` on a successful write, `1` if the write fails or the stream errors (e.g., a blocked/broken pipe).
- Add automated tests covering the PRD's verification matrix (TC-001, TC-002).

**Non-Goals:**
- No CLI argument parsing, configuration, or additional output channels — this stays a single-purpose script.
- No new runtime dependencies beyond what's already installed (`typescript`, `@types/node`).

## Decisions

- **Use `process.stdout.write(str, callback)` instead of `console.log`.** The callback fires once the write completes (or errors), giving an explicit point to set the process's exit code. `console.log` doesn't expose that per FR-002.
  - Alternative considered: keep `console.log` — rejected, no hook to react to a failed/blocked write.
- **Handle the `'error'` event on `process.stdout`** in addition to the write callback, since stream errors (e.g. EPIPE) can arrive asynchronously outside the callback. On error, exit `1`.
  - Alternative considered: wrap the call in `try/catch` — rejected, stream write errors are delivered asynchronously via callback/event, not as synchronous thrown exceptions, so `try/catch` wouldn't reliably catch them.
- **Prefer `process.exitCode = n` over `process.exit(n)`** on the success path, letting Node's event loop drain naturally; only call `process.exit(1)` directly on the error path to guarantee a prompt non-zero exit.
  - Alternative considered: always `process.exit()` immediately — rejected, risks truncating an in-flight write on some platforms.
- **Use Node's built-in `node:test` + `node:assert` runner** for the new tests rather than adding Jest/Vitest.
  - Alternative considered: Jest — rejected as unnecessary weight for a single trivial script; Node 24 (already in use here) ships a built-in test runner.

## Risks / Trade-offs

- [Risk] The `'error'` event may not fire promptly on every platform if stdout is blocked → [Mitigation] the write callback already carries its own error branch, so exit-code correctness doesn't depend solely on the event firing.
- [Risk] Simulating a genuinely blocked/broken stdout pipe in an automated test is awkward in a headless CI environment → [Mitigation] cover the happy path (TC-001/TC-002) with a spawned-process test; treat the stdout-error branch as manually verified rather than blocking on a hard-to-construct integration test.

## Migration Plan

Code-only change to a single script plus new tests; no data migration. Deploy is a normal merge once `npm run build`, `npx tsc --noEmit`, and the new test pass. Rollback is a plain revert of the commit.
