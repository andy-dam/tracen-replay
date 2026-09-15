# Repository instructions

Read and follow [AGENTS.md](AGENTS.md) before making changes. It is the shared source of repository instructions for coding agents.

All commits must use Conventional Commits: `<type>[optional scope][!]: <description>`. Follow the commit rules and examples in AGENTS.md.

## Local session records

Evaluation inputs, sealed analyzer snapshots, preserved runs and the working
handoff for the analyzer's reliability work live under `.local/` (start with
`.local/final-reliability-v1/CLAUDE-HANDOFF.md`). Read the handoff in full
before running or changing anything in the analyzer's evaluation. It is local
session context: never publish it, the generated artifacts, or the recordings,
and never name its candidate labels or the recordings' creators in committed
code, tests or documentation.
