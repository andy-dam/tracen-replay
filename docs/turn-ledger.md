# Turn ledger

The Python report now includes a `turn_ledger` projection with schema
`tracen-replay/turn-ledger-v1`. It joins the existing evidence collections for
the Go consumer; it does not rerun OCR or invent missing changes. The report
validator recomputes the projection and rejects stale or altered ledger data.

Each window contains its observed calendar label, boundaries, committed action
count, starting and later observed states, chronological entry references, and
stat/performance comparison references. Entries point into `gameplay_tracking`
with JSON pointers. Consumers resolve those pointers in the same report.

- Dated windows use repeated half-month observations. Pre-debut and finale
  countdowns identify segments that may contain more than one action. A phase
  without a readable countdown remains an unresolved phase, not a fabricated
  turn. `expects_one_action` distinguishes these cases.
- `states.stats` contains all five stats and skill points when observed.
  `states.performance` contains Dance, Passion, Vocal, Visual and Composure.
  A missing state is `null`, not zeros or a carried-forward value.
- An opening state is the first observed state before the action in that
  window. Its timestamp and `exact_turn_boundary` flag remain visible. A later
  state's values are not moved backward to fill an opening. Matching explicit
  source observations can link an unchanged stat checkpoint across dates.
- Repeated complete source readings can also establish an opening when their
  short duration does not qualify them as accounting checkpoints. Two distinct
  timestamps must agree; conflicting, singleton and uncertain-calendar readings
  remain unresolved. `source_ref` identifies the source record and `values_ref`
  points directly to its numeric mapping. Use `values_ref` for both checkpoint
  and reading-backed states; `supporting_source_refs` preserves corroborating
  observations. This does not add a synthetic accounting interval.
- A closing state can refer to the next window's first observed state or the
  last observed state after the final action. Neither promises the value at an
  unseen exact boundary. Events before the next first state remain visible in
  the timeline; this observation link does not reassign their cause.
- Events retain changes and field evidence. Hints have separate entries with
  their first linked receipt time when available. An event spanning two
  windows remains unassigned with both candidate windows listed. Temporal
  proximity never establishes that an event was caused by training.
- Race, lesson, song, concert and skill records are references to the original
  transactions. They are not additional awards to sum on top of their event
  receipts. Unparsed receipt candidates stay visible and unaccepted.
- Ambiguous effect candidates have their own entries, including uncertain hint
  names. Their observed alternatives and amounts are retained, but
  `accepted_award` is false and `occurrence_count` is unknown. Do not count each
  spelling alternative as a separate hint gain.

Resource comparisons link adjacent observed states and retain their original
supported changes and residuals. A comparison spanning multiple turns is one
shared object. Count it once by `source_ref`; never split its amount or assign
its residual to one of those turns. Stat comparisons link their supporting
events, and performance comparisons link the original transaction records.

The ledger checks types, source bounds, key references and arithmetic
consistency. It does not certify interpretation of the source video. In
particular, `complete_event_history` stays false until coverage has been
verified independently. The completed bounded source and attribution checks
are recorded in [the first integration acceptance](first-go-acceptance.md).
