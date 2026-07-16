import { fileURLToPath } from 'node:url';

export function fibonacci(count: number): number[] {
  const series: number[] = [];
  for (let i = 0; i < count; i++) {
    if (i === 0) series.push(0);
    else if (i === 1) series.push(1);
    else series.push(series[i - 1] + series[i - 2]);
  }
  return series;
}

function main(): void {
  console.log(fibonacci(10).join(', '));
}

if (process.argv[1] === fileURLToPath(import.meta.url)) {
  main();
}
