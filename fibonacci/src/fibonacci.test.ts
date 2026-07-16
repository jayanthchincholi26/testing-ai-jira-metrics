import { test } from "node:test";
import assert from "node:assert/strict";
import { fibonacci, fibonacciSeries } from "./fibonacci.ts";

test("fibonacci(0) is 0", () => {
  assert.strictEqual(fibonacci(0), 0);
});

test("fibonacci(1) is 1", () => {
  assert.strictEqual(fibonacci(1), 1);
});

test("fibonacci(7) is 13", () => {
  assert.strictEqual(fibonacci(7), 13);
});

test("fibonacciSeries(9) matches the known sequence", () => {
  assert.deepStrictEqual(fibonacciSeries(9), [0, 1, 1, 2, 3, 5, 8, 13, 21]);
});
