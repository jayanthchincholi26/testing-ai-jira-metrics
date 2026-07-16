## 1. Implementation

- [x] 1.1 Rewrite `src/index.ts` to write via `process.stdout.write('Hello, World!\n', callback)` instead of `console.log`.
- [x] 1.2 In the write callback, set `process.exitCode = 0` on success and `process.exitCode = 1` (or `process.exit(1)`) on error.
- [x] 1.3 Add an `'error'` listener on `process.stdout` that exits `1` without throwing an unhandled exception.

## 2. Tests

- [x] 2.1 Add a test (using Node's built-in `node:test` + `node:assert`) that spawns the compiled program and asserts stdout matches `^Hello, World!\n$` (TC-001).
- [x] 2.2 Assert the spawned process exits with status code `0` (TC-002).
- [x] 2.3 Add a `test` script to `package.json` that runs the new test file.

## 3. Verification

- [x] 3.1 Run `npm run build` and confirm it compiles clean.
- [x] 3.2 Run `npx tsc --noEmit` and confirm no type errors.
- [x] 3.3 Run the new test script and confirm it passes.
