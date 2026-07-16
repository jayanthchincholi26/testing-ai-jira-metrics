import { test } from 'node:test';
import assert from 'node:assert/strict';
import { spawnSync } from 'node:child_process';
import path from 'node:path';

const scriptPath = path.join(__dirname, 'index.js');

test('TC-001: prints Hello, World! to stdout', () => {
  const result = spawnSync('node', [scriptPath], { encoding: 'utf8' });
  assert.match(result.stdout, /^Hello, World!\n$/);
});

test('TC-002: exits with status code 0', () => {
  const result = spawnSync('node', [scriptPath], { encoding: 'utf8' });
  assert.strictEqual(result.status, 0);
});
