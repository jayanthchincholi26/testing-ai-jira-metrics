import test from 'node:test';
import assert from 'node:assert/strict';
import { fibonacci } from './fib.ts';

test('fibonacci returns an empty series for count 0', () => {
  assert.deepEqual(fibonacci(0), []);
});

test('fibonacci returns the first 5 terms starting at 0', () => {
  assert.deepEqual(fibonacci(5), [0, 1, 1, 2, 3]);
});

test('fibonacci returns the first 10 terms', () => {
  assert.deepEqual(fibonacci(10), [0, 1, 1, 2, 3, 5, 8, 13, 21, 34]);
});
