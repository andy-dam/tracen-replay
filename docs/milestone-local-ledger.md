# Milestone: evaluated local replay ledger

Produce an inspectable local report that separates observed stat totals, training
previews, explicit outcome evidence, and unexplained changes. This milestone
establishes a regression baseline before adding an application or cloud hosting.

**Status:** completed as a development baseline on 2026-09-08. See the
[measured results and remaining limitations](results-local-ledger.md). This does
not establish a verified full-run event history.

## Acceptance criteria

- Process three source intervals: 00:25–01:22, 01:22–03:22, and 06:20–06:55
  from the initial English 1920×1080 recording (212 seconds total).
- Evaluate six-field stat readings against a versioned set of manually reviewed
  timestamped references, including negative examples where current totals are
  hidden. Report accuracy and abstention separately; never fill missing values
  from references. Every accepted reference field must match, with at least 80%
  complete-reading coverage on readable reference screens.
- Record recognized training options as previews, with evidence. A preview alone
  must never become a completed action. Test switching options and returning to
  the same option without advancing the turn.
- Preserve gaps between stable observations, even when totals before and after
  a gap match. Unknown countdowns must not imply a known turn boundary.
- Show six-field arithmetic for each checkpoint interval, the initial residual,
  the investigation performed, and any remaining residual. Arithmetic balance
  must remain distinct from verified event coverage.
- Expose ambiguous log deduplication decisions and raw candidates. Repeated or
  partial stat deltas must not silently become a verified action history.
- Provide a repeatable evaluation command, focused regression tests, and an
  offline report linking conclusions to source screenshots.

## Validation scope

All three intervals belong to one development recording. References are manual
assistant transcriptions, not independently reviewed ground truth. Passing these
checks demonstrates a working development baseline, not accuracy on unseen runs,
other resolutions, or other scenarios. Videos and screenshots remain local;
small numeric/text references and evaluation code can be versioned.

## Deferred

Full-run ingestion, guaranteed turn reconstruction, general log occurrence
identity, additional layouts, native-frame adaptive decoding, Go service, Azure
deployment, and learned recognition remain separate milestones.
