function fibonacciSeries(count: number): number[] {
  const series: number[] = [];
  let [a, b] = [0, 1];
  for (let i = 0; i < count; i++) {
    series.push(a);
    [a, b] = [b, a + b];
  }
  return series;
}

const count = 10;
console.log(fibonacciSeries(count).join(", "));
