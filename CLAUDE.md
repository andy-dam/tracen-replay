# Repository instructions

Read and follow [AGENTS.md](AGENTS.md) before making changes. It is the shared source of repository instructions for coding agents.

All commits must use Conventional Commits: `<type>[optional scope][!]: <description>`. Follow the commit rules and examples in AGENTS.md.

## Current local continuation

For the analyzer reliability work, read [.local/final-reliability-v1/CLAUDE-HANDOFF.md](.local/final-reliability-v1/CLAUDE-HANDOFF.md) in full before running or changing anything. It contains the stopping point, remaining failures, immutable evaluation inputs, and ordered resume commands. This is local session context; do not publish it or the generated artifacts. The reliability goal is at its judgement step (G9, candidate v38 pending its batch and the untouched-set pass); the Go application milestone is in progress with the user's go-ahead of 2026-09-14 (see docs/go-api-plan.md and docs/milestone-go-app.md).
