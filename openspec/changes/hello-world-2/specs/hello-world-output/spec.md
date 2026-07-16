## ADDED Requirements

### Requirement: Deterministic Hello World Output
The system SHALL print the exact string "Hello, World!" followed by a single line feed (`\n`) to stdout, encoded as UTF-8, and SHALL produce byte-identical output on every invocation given identical environment state.

#### Scenario: Successful run
- **WHEN** the program is executed with a healthy stdout stream
- **THEN** stdout SHALL match the regex `^Hello, World!\n$` and the process SHALL exit with status code `0`

### Requirement: Graceful Exit on Stdout Failure
The system SHALL exit with status code `1` if writing to stdout fails or the stream reports an error, instead of hanging or crashing with an unhandled exception.

#### Scenario: Blocked or erroring stdout
- **WHEN** the stdout stream reports a write error (e.g., a broken pipe / EPIPE)
- **THEN** the process SHALL exit with status code `1` and SHALL NOT throw an unhandled exception
