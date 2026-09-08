# Gameplay-only reconstruction

Status: full-recording development analysis implemented; reliability validation remains open. Go and Azure remain behind the gates below. See [measured results](results-gameplay-only.md) and [full-recording setup](full-recording-analysis.md).

## Recognition boundary

The supported profile is English 1920x1080, with gameplay at `(148, 0, 958, 1080)`. Neural recognition receives only that 810x1080 crop. Full source screenshots are retained as evidence but the side log is not recognition input. The older Tesseract `--gameplay-only` clip runner remains available; legacy `--track-stats` uses the auxiliary log and is not a fallback.

## Report contract

`gameplay_tracking` includes:

- `readings` and `screens`: source-timestamped observations and visibility spans, not action counts.
- `training_previews`: browsed options, never completed-action claims.
- `turn_action_receipts`: training result options, rest/outing recovery chains and race results. Receipt time and unknown click time are separate.
- `checkpoints`, `events`, `intervals`: repeated six-field states, supported effects and unexplained residuals. Candidate resolutions retain evidence and whether they depend on surrounding states.
- `lesson_purchases`: matching named receipt and confirmation, five-resource costs, observed debits, actual awards and separate projected effects.
- `skill_purchases`: receipt-backed batch SP debit, cart additions/removals, committed bundles, prerequisite candidates and circle variants. Complete ownership is a separate claim.
- `races`: identity, course, placing, fans and unknown item identities.
- `concerts`: confirmation, result, aftermath, queued effects, update receipts and later observed current-bonus snapshots. The update caption does not prove every value.
- `song_acquisitions`: one acquisition per receipt with alternative observed names and supported paid-lesson or story-event associations.
- `performance_accounting` and `fan_accounting`: separate resource ledgers with unknown observations preserved.

`verification` checks source endpoints, sampling continuity, PTS, processed frames per minute, discrepancies, calendar/action coverage and final-state agreement. Separate `evidence-audit.json` validates source hashes and crop pixels. Neither automatically certifies semantic correctness.

## Counting rules

| Mechanic | Count from | Keep separate or unknown |
|---|---|---|
| Training | Result option and supported displayed gains | Browsing, projections, caps, click time |
| Ordinary events | Explicit receipts with title/geometry/time boundaries | Dialogue, actual selected choice, unreadable effects |
| Rest and outings | Request then recovery without intervening action | Canceled dialogs and unrelated recovery |
| Races | Result identity, placing and fan receipt | Entry menus, unread item identities |
| Skills | Acquisition receipt plus SP balance evidence | Pending/removed selections, bundled prerequisites, next offered rank |
| Lessons | Named receipt, confirmation and balances | Offered cards, delayed unchanged counters, canceled dialogs |
| Songs | Learned receipt and supported acquisition route | OCR alternatives as multiple songs, story songs as paid lessons |
| Concerts | Result/aftermath and explicit bonus update | Planned values as active; missing values as zero |
| Completion | Observed final attributes and remaining SP | Existing skills as fresh purchases; missing inventory as complete |

Immediate effects, future training modifiers and bonuses queued until a concert are distinct. Performance currencies, caps, fans, energy, mood, friendship and hints are distinct resources. `Speed went up by 6 to new heights.` means **6**; the displayed reduction has already happened. No unseen rewards are computed from scenario formulas.

## Gates before the application

- [x] Structurally isolate gameplay pixels and test independence from the auxiliary log.
- [x] Process the complete supplied recording and verify frame/crop/refinement provenance.
- [x] Separate previews, confirmations, receipts, immediate awards and future effects.
- [x] Account for six stat fields and five performance currencies across observed checkpoints.
- [x] Account for race fans, actions on dated turns, and final attributes/SP.
- [x] Recover observed lesson debits, committed skill charges and concert reward/update receipts.
- [x] Review every detected action receipt and evaluate two contiguous development sequences.
- [ ] Measure full action/effect recall against dense references not selected only from predictions.
- [ ] Validate complete skill inventory and active concert bonuses; adjudicate fields the source does not expose.
- [ ] Validate non-stat mechanics and uncertain names, including choice evidence and race item identities.
- [x] Evaluate a separate recording before source-driven changes and preserve initial scores separately from later regression results.

The separate-recording evaluation exposed failures; completing that evaluation does not close the remaining reliability gates. Its reviewed intervals and limitations are reported in [measured results](results-gameplay-only.md).

All clips from one video belong to one recording-level evaluation split. Additional sampling does not create an independent test set. Unknown fields are valid outputs; complete mechanic coverage requires its own reference evidence.
