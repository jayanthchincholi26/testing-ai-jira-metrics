process.stdout.write('Hello, World!\n', (err) => {
  process.exitCode = err ? 1 : 0;
});

process.stdout.on('error', () => {
  process.exit(1);
});
