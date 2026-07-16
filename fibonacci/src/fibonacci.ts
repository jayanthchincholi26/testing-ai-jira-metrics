export function fibonacci(n: number): number {
  if (n < 0) {
    throw new RangeError("n must be a non-negative integer");
  }
  if (n <= 1) {
    return n;
  }
  return fibonacci(n - 1) + fibonacci(n - 2);
}

export function fibonacciSeries(count: number): number[] {
  return Array.from({ length: count }, (_, i) => fibonacci(i));
}
