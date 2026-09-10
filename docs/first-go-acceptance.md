# First Go integration acceptance

This record covers the bounded analyzer milestone in
[the application checklist](gameplay-only.md#first-go-integration-milestone).
It does not certify complete recognition of a run. The report retains
`fully_verified: false` and incomplete event histories.

The analyzer is accepted for its first local Go integration. All six items in
the bounded checklist are complete. No Go application code is included here.
The [acceptance manifest](first-go-acceptance.json) binds the implementation,
three final reports, tests, regression scores and local source-evidence audits
by SHA-256. Recordings and generated frame caches remain local.

## Validation scope

All three development recordings were processed from start to end at the
configured sampling rate. The final replay reconstructs reports from preserved,
source-paired observations; it does not repeat whole-video OCR. Initial reports
and reference labels remain preserved. All three recordings have now informed
development and must not be described as untouched evaluation data.

The integrated candidates reconstruct exactly and preserve all numeric event
changes and accounting intervals. The Rest fix adds the previously missing
Sleep Deprived action in the third recording without removing existing Rest
actions. Four conflicting spellings of one hint in that recording remain
unaccepted alternatives. The shared event parser supplies the effects;
action-specific modules establish the committed action from its surrounding
confirmation, result and turn transition.

The regression suite passes 1,165 tests with one Windows symlink skip. A real
three-second worker job exercises extraction, OCR, report validation and JSON
output. Cached reparse produces the same report and terminal response. This
smoke test is distinct from the full-recording report replays.

## Observed coverage

| Recording | Dated turns with one action | Pre-action stats/SP states | Pre-action performance states | Unassigned entries | Linked evidence files |
| --- | ---: | ---: | ---: | ---: | ---: |
| Original | 60/60 | 59/60 | 57/60 | 16 | 4,110 |
| Second | 60/60 | 59/60 | 54/60 | 3 | 4,611 |
| Third | 60/60 | 59/60 | 57/60 | 10 | 4,536 |

These counts concern dated windows. Pre-debut, finale and uncertain phase
segments are also retained. Across all windows, each recording has one segment
with no attributed action; the original, second and third have respectively
one, three and two segments with multiple actions. Those segments are not
silently treated as ordinary half-month turns.

Every ledger entry and resource comparison retains its source reference.
All 13,257 per-recording evidence-file references exist and have recorded
hashes. This establishes preservation and availability, not image-reading
accuracy. Matching accepted fields to saved source observations is a separate
audit; it also does not measure missed-event recall.

That attribution audit covers every accepted event field and all five currency
fields of all 184 lesson purchases. Training changes trace to result-screen
gains or documented before/result counter derivations. Five recovered event
fields use nested hint proofs, dense receipt candidates or repeated award
animations rather than exact base-row effect matches. All seven accepted skill
debits have supporting purchase evidence; one remaining batch has no accepted
debit. No unsupported accepted change was found in this bounded review.

The final boundary fix reads the existing performance sidebar on validated
race-day screens and admits short, repeated source states into the ledger.
It creates no synthetic accounting checkpoint and changes no accepted action,
event reward or purchase. The original Early May opening is observed at the
boundary; its Senior Late December opening is observed later, before the race,
and remains marked as different from the exact boundary. The Junior Late
December opening remains unavailable.

Observed stat/SP intervals balance 404/404, performance intervals 443/444,
and fan intervals 33/33. These are interval counts, not percentages of event
recognition. The original recording retains an unexplained Passion +10 over
914250–970750 ms. No value is invented to close it.

## Fixed reference results

| Check | Result and scope |
| --- | --- |
| Original opening actions | 5/5 |
| Original reviewed action receipts | 77/78 exact timing matches; the remaining Stamina action differs by 33 ms |
| Original lesson/concert checks | 11/11 |
| Original fixed effects | 20/20 |
| Original visible inventory | 14/14 base names, 5/5 details; complete inventory remains unknown |
| Reviewed concert panels | 24/24 fields across eight panels from two recordings; final active totals remain unverified |
| Second recording preserved checks | 11/12; the remaining exact-match failure is `Present March♪` versus `Present March ♪` |
| Third recording preserved windows | All 15 scores retained, including failures and incomplete references; 37/40 middle-window effect matches remain unchanged after quarantining four name alternatives |
| Third recording adjudicated quarter window | 39/41 occurrence matches with the preserved occurrence-scoring option; two obscured friendship recipients remain missed and two source-real predictions are outside the labels |

The early third-recording quarter reference in `comparison-v1.json` still
scores 10/41 under its original event-start timing rules. The separately
preserved `quarter-occurrences-regression-v2.json` produces 39/41 with timing
adjudication and occurrence scoring. These are separate scoring configurations.
Neither reference nor its
failure is overwritten, and the two configurations must not be compared as a
recognizer improvement. Incomplete action/effect references retain failed
`passed` flags even where the listed labels match.

## Requirements for the Go consumer

- Use [the analysis job interface](analysis-job.md) to launch Python and read
  its single terminal JSON response. Job success means a valid report was
  produced; it does not establish complete analysis.
- Render [the turn ledger](turn-ledger.md) with observation timestamps,
  source evidence and unknown opening/closing states. Do not backfill a missing
  opening from a later state or attach an unassigned entry to the nearest turn.
- Keep direct receipts, state-derived training changes, projected options and
  ambiguous candidates distinct. A preview is never an additional award.
- Count shared comparisons and transaction references once. Display residuals
  over their actual observed interval, including intervals spanning turns.
- Expose partial skill purchases, unknown costs and incomplete inventories.
  A confirmed purchase does not imply a complete list of purchased skills.
- Respect field timestamps on final observations. The third recording's
  reported 953 SP observation precedes its last purchase; it must not be shown
  as the post-purchase final balance. That purchase's cost remains unresolved.
- Keep unavailable numeric completion-hub cross-checks unknown on the second
  and third recordings. Do not convert rank letters to numbers or require
  users to open optional panels or pause their recordings.

`verification.go_ready` is a legacy placeholder that currently remains false;
it is not a computed result for this bounded integration milestone. This
acceptance record governs the start of integration. Neither it nor worker
success changes `fully_verified` or `complete_event_history`.

## Remaining work after integration

Improve recognition of obscured names, optional inventory pages and concert
totals; reduce missing boundary observations and unresolved transactions; and
measure whole-run semantic recall on a separately preserved future recording.
Readable recognition misses remain defects. Unknowns identify evidence limits,
not permission to describe the analyzer as complete.
