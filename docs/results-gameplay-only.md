# Gameplay-only development results

The original English Our Grand Concert recording was processed from 00:00 through 31:06.333. The source coverage and accounting below use development source SHA-256 `a75008eb095a2d37f9d0b49d09f1a54be8285bd9ef69e243037440a580193174`. A separate-recording evaluation appears below. Media, weights and generated reports remain local.

## Source coverage

- 7,466 base frames at 4 FPS, no sampling gap above 250 ms, final sample 83 ms before the end.
- Detected training windows inspected at 30 FPS; six difficult animations additionally inspected at native 60 FPS. A decoder interval rounding bug that previously skipped alternate native frames was fixed and tested against a generated 60 FPS video.
- All 11,569 base and supplemental observations passed the source-frame, crop-pixel and original-OCR hash audit. All 8,526 present refinements matched their original observations and proof images.
- Recognition uses only gameplay rectangle `(148, 0, 958, 1080)`. The auxiliary log is excluded.

These checks establish provenance and sampled coverage. They do not prove that a brief effect between samples was recognized.

## Accounting and actions

| Check | Result | Scope |
|---|---|---|
| Six-field state comparisons | 141/141 balanced | Five attributes and skill points |
| Performance resource comparisons | 137/137 balanced | Dance, Passion, Vocal, Visual and Composure |
| Fan comparisons | 11/11 balanced | Between 12 race-result totals, including concert awards |
| Turn actions | 78 | 59 training, 12 races, 5 outings and 2 rests |
| Dated career turns | 60/60 with exactly one action | No interior calendar gaps |
| Other action phases | 12 pre-debut, 6 finale | Generic phase labels do not individually identify turns |
| Lesson purchases | 77/77 observed debits | Named receipt, confirmation and matching balances |
| Skill batches | 803 SP and 2,237 SP | Both charges assigned across committed cart bundles |
| Concerts | 5/5 reward associations and update receipts | Stat/SP/fan aftermath and activation step |
| Song acquisitions | 20 paid, 2 story event receipts | Alternative OCR names grouped within a receipt |

All 78 detected action receipt screenshots were visually reviewed for identity and training option. The source-linked review is in `tests/fixtures/full-action-receipt-review-v1.json`. Images were selected from analyzer output, so this is **not an independent missed-action recall measurement**. The calendar cross-check supplies an additional omission check: it found five outings that arithmetic alone had not exposed.

Two Speed gains, +12 and +11, were automatically selected from visible complete-number candidates using surrounding state constraints. Separate visual review confirms the displayed gains. The report retains `independent_effect_verification: false` for the automatic resolution because the reconciled state is not independent numeric evidence. Reviewed annotations never become OCR inputs.

Final attributes agree between the completion hub and summary: **1482 Speed, 868 Stamina, 1090 Power, 713 Guts and 1147 Wit**. The completion hub shows **40 skill points**. Whole-career accounting from the first observed checkpoint leaves zero unexplained change in all six fields.

## Mechanics distinctions

The first skill batch has six committed cart targets, the second twelve. Selecting and removing a skill changes a projected counter; only the acquisition receipt establishes the batch. Prerequisite and upgrade prices are not added twice. Circle variants are read separately so an offered next rank is not mistaken for another purchase. Complete owned-skill inventory remains unverified.

All twelve races have readable identity, course and placing; all show first place. Entry screens are not completed races. Item icon identities remain unsupported.

All five concerts have linked stat/SP/fan aftermath and an explicit bonus-update receipt. This verifies activation, not every resulting value. Repeated later Concert Info values are recorded where visible; planned values stay separate. Story song acquisition does not imply a paid lesson or an invented bonus amount. Maximum energy, aptitude changes, acquired conditions, full-energy status and unchanged friendship are separate effects. A review list exposes receipt-like OCR fragments that the parser did not accept; their numbers never enter a ledger.

## Evaluation

249 automated tests pass, covering auxiliary-pixel isolation, source tampering, PTS continuity, native sampling, animated digit conflicts, cursor-obscured receipt digits, delayed lesson balances, canceled outings, cart removals/upgrades, circle variants, calendar omissions/duplicates and evaluation penalties. Skill confirmations accept singular and plural wording. Repeated completion-hub SP counters can support a skill charge even when the five-attribute grid is not visible; projected menu counters remain separate. Direct counter crops recover small digits missed by text detection, abstain on disagreement, and retain source hashes. Finish-confirmation balances are observations, not a completed action or a points deduction.

Consecutive skill receipts can be reconciled jointly between observed balances when repeated final-cart counters explain the full debit and no other SP transaction intervenes. Intermediate counters retain their projected status. Account fan totals after career completion are separate from race results. Repeated completed-training identity remains an action even when its reward digits are unreadable; reward completeness stays false.

All 58 reviewed unparsed receipt candidates now have matching accepted evidence in their nearby receipt. Four actual gaps required denser inspection: energy deductions of 18 and 14, unchanged friendship with Sirius Symboli, and maximum friendship with Light Hello. The source-linked fixture preserves the manual statements and screenshot hashes. This candidate-selected check does not measure effects that never became candidates. Recreation unlocks and inheritance inspiration/spark triggers are typed separately without inventing their rewards.

Two contiguous development references pass:

- Opening 00:25-01:22: 5/5 training/rest actions, correct options, no extras.
- Lessons and first concert 05:41-06:54: 11/11 transactions (10 lessons and one concert), exact reviewed costs and stat/fan awards, no extras. A canceled dialog is excluded. Decorative name symbols, choices, hype and complete non-stat effect recall are outside this score.

A fixed 01:00-01:16 source review checks all explicit bottom-dialogue effects visible in 64 consecutive 250 ms samples. The initial report matched 16 of 20 effects and emitted one false extra song name. Training-appearance unlocks, generic supporter announcements and conservative single-character song-name resolution bring this development reference to 20/20 with no extras. Generic announcements do not add to named-supporter counts. This is sampled interval recall, not full-recording or native-frame recall.

The earlier 15 reviewed point examples remain narrow fixtures, not a full-run accuracy score.

```powershell
python -m unittest discover -s tests -v
python -m tracen_replay.receipt_review tests/fixtures/full-action-receipt-review-v1.json .local/full-recording/run-01/report.json --evidence-root .local/full-recording/run-01 --output .local/full-action-receipt-evaluation.json
python -m tracen_replay.candidate_review tests/fixtures/unparsed-receipt-review-v1.json .local/full-recording/run-01/report.json --evidence-root .local/full-recording/run-01 --output .local/receipt-candidate-evaluation.json
python -m tracen_replay.transaction_evaluate tests/fixtures/lesson-concert-sequence-v1.json .local/full-recording/run-01/report.json --output .local/lesson-concert-evaluation.json
python -m tracen_replay.verify_evidence "C:/path/to/recording.mp4" --output .local/full-recording/run-01
```

## Separate-recording evaluation

A separate 29:36.717 recording was evaluated with revision `40fd1387d83edce0e42d2dde68989999783a51d2` before source-driven code changes. The base and fully refined initial reports, source/model hashes, source archive, environment and manual references are preserved locally under `independent-01/initial-base` and `independent-01/initial-refined`. The initial labels were reviewed directly from video before reading predictions.

The frozen pipeline processed 7,107 base frames, 60 dense training windows, and native-rate gap inspections. Its evidence audit verified 11,274 observations and 9,634 refinements with zero provenance errors. This checks evidence integrity, not interpretation accuracy.

| Frozen refined check | Result | Scope |
|---|---|---|
| Dialogue effects | 74/75 matched, one extra | Four dense intervals totaling 115 seconds; the mismatch is a missing musical-note symbol in a song name |
| Action identities | 4/4 matched | Three trainings and one race, including selected training options |
| Declared action details | Passed | Reviewed training gains, performance gains and race identity/course/fans |
| Lesson transactions | 7/7 found; one cost unresolved | Seven paid lessons in 19:55–20:30 |
| Concert transaction and aftermath | Passed | One concert and 19 aftermath effects in 21:45–22:05 |
| Final attributes | Five matched | Numeric result summary |
| Final skill purchase | Failed | The frozen classifier missed the singular confirmation wording and did not reconstruct the batch |

The effect reference covers approximately 6.47% of this recording, not whole-recording recall. Choice reconstruction now has a separate development evaluation; it does not change the frozen results above. Final inventory, active bonuses and race item identities are outside these passing checks. The initial failures remain preserved; changes guided by this recording and later scores on it are regression work, not a fresh independent evaluation.

Subsequent regression fixes bring the final-state/skill reference from 6/20 to 20/20 checks: final attributes, the two checked remaining-SP observations, the 1,234-SP charge, and twelve skill targets with costs and declared circle variants. This does not establish complete owned-skill inventory. All 62 reconstructed lesson costs are now observed, and all three skill charges (1,455, 60 and 1,234 SP) are assigned to cart bundles. The first two charges use joint receipt accounting, without claiming an independently observed intermediate balance.

The separate recording has all 124 stat intervals balanced, all 113 performance intervals balanced, and all 11 fan intervals balanced. Its consolidated evidence audit verifies 11,631 observations and 14,500 refinements with zero provenance errors, including the added training-currency refinements, native receipt inspections, 3,859 base choice observations and 30 dense choice frames. A subsequent scoped audit also verifies 75 new inheritance frames and 75 receipt refinements with zero errors; these are additional to the consolidated counts. These improvements are regression results. The original recording retains its balanced ledgers and both complete cart-charge assignments.

All 60 dated turns now have one action. The recording contains 60 training attempts, 12 races, five outings and one rest across dated, pre-debut and finale phases. One Wit attempt has a directly reviewed `FAILURE` banner: the action remains counted, while the lingering +17 Dance/+17 Passion display is retained as a projection rather than awarded resources. A support outing without energy recovery is supported by its confirmation, matching narrative, mood/friendship receipt and observed next date. Neither case is inferred solely from arithmetic.

A song receipt with a single uncertain trailing character can be associated with a purchase only when repeated request frames and repeated before/after resource balances support the debit. Both observed names remain in the report, with name identity unverified. This recovers a 42-Passion/21-Vocal purchase without declaring the ambiguous suffix correct. Standalone currency numbers are excluded from confirmation title candidates.

Native receipt frames recover the remaining Composure +10 event reward, corroborated by repeated large labeled award animations. A missing currency-refinement pass on newly sampled training frames exposed Composure +17; its one shorter-prefix OCR outlier is retained beside the repeated complete readings. Failed training still contributes no performance award.

Repeated labeled stat animations now corroborate event receipts. The Mile Championship aftermath recovers 57 SP where the cursor produced shorter numeric readings; both the animation proof and discarded receipt alternatives remain available. The concert reward is additionally supported by repeated 105-SP receipt readings and repeated labeled animation readings; one 135-SP OCR outlier remains recorded. The Arima aftermath now recovers 57 SP using two confident, stationary skill-point label reads bracketing seven confident number reads. Their source timestamps and label boxes remain in the animation evidence, alongside the repeated 57-SP receipts and the discarded 37-SP alternative. Inheritance now distinguishes the visible 54-stamina award from its separate cap increase, using complete spatially distinct labels and repeated nearby normal-stat receipts. Its lone 4-stamina OCR alternative remains recorded. Multiple conflicting receipt observations or an uncorroborated animation still prevent resolution. The 32-wit training award is now corroborated by current-value readings with an obscured cap and a later complete result counter. The partial reads do not establish a cap value or use the later career-hub checkpoint.

The optional receipt-symbol pass now recovers the visible musical-note suffix directly from gameplay pixels in both recordings. It requires stable note geometry and closing quotation marks, preserves the original OCR text, and does not consult a song catalog. Seven original-run frames and five separate-run frames pass scoped source-pixel and refinement-provenance checks. Against the unchanged preserved references, all 75 dialogue effects now match with no extras, including 17/17 in the scenario interval. All other preserved regression checks still pass. This remains a score over the same 115 seconds, not whole-recording recall.

## Remaining boundary

`fully_verified` and `go_ready` remain false. Full non-stat effect recall, complete choice coverage, complete skill inventory, uncertain song symbols, race item identities and all active concert bonus values are not yet validated. Some fields need additional visible UI evidence rather than more arithmetic. Remaining gaps concern full semantic coverage and uncertain identities. See [active gates](gameplay-only.md) and [reproduction guide](full-recording-analysis.md).

## Dialogue choice development

The optional choice-refinement pass stores gameplay-only card and selection-mark observations with raw OCR and screenshot hashes. Source review of the original eight predictions found a missing wrapped option and two selections incorrectly attached to an earlier menu. Version 2 joins text within each card and rejects associations contradicted by readable selection-card text. Six reviewed base candidates remain; the two stale associations now abstain. A bounded 60 FPS inspection adds the brief single response, bringing the report to seven reviewed candidates. The preserved doctor-event reference now scores 3/3 with no extra predictions in its evidence window. Dense frames feed only choice reconstruction and cannot change stat accounting. All 3,859 version 2 observations pass their raw/proof hash checks; version 1 artifacts remain archived. This is bounded regression development, not full choice recall. See [choice reconstruction](choice-reconstruction.md).

## Concert panel development

Eight manually reviewed Concert Info panels provide 48 current/planned field values. The current reports match all 48 with no incorrect values. The initial 46/48 result remains preserved: the two missing values came from one unreadable support-level display in the first recording. A refinement supported by three nearby source frames now recovers that display while retaining the original OCR and proof hashes. This panel-selected review does not establish missed-panel recall or final active bonuses. See [panel evidence and scope](concert-panel-validation.md).

## Final owned-card observations

The final-summary reader retains repeated visible owned-card text independently of skill purchases. Source review finds 14 visible cards on each first page; gameplay-panel refinement now recovers all 14 names in each recording with no extras. The reviewed details also match: four single-circle variants and unique-skill level 4 in the original recording, and six single-circle variants and level 5 in the separate recording. These are first-page observations, not complete inventories. Off-screen cards remain unknown, and unreadable details are not treated as absent. See [ownership evidence and limits](owned-skill-observations.md).

## Source-ordered coverage

The additional source-selected 02:40–04:40 reference contains 480 consecutive 250 ms samples. Its first comparison against the preserved report snapshot matches 50 of 58 labeled effects, with four unmatched predictions and no proof-hash errors. Timing boundaries and incorrect recipient/skill names require separate adjudication; this initial score is retained before those investigations. Together with previous references, the sample-manifest audit covers 365 seconds (20.544%) of the separate recording. This is declared sampled-effect coverage, not full-run accuracy or native-frame recall.

Source inspection of the adjacent frames corrected two receipt onset boundaries without changing any effect labels. The preserved boundary variant scores 52/58 against the same frozen report. A subsequent analyzer fix repairs only the fixed keyword in complete `Friewdship`/`Frendship` receipts, preserving recipient text, amount and confidence. Recipient occlusion checks cover the same spellings. The rebuilt report matches 54/58, with two incorrect-name extras; the two recovered effects are Light Hello friendship changes. All nine source-labeled completed actions match, while browsed options and the blocked race attempt are excluded. Both stat ledgers remain balanced, the frozen `regression-28` checks pass, and 324 unit tests pass. These results do not close full-recording recall or identity-validation gates.

The next reconstruction pass matches 55/58 effects with no extra predictions in this interval. It recovers a hint's single-circle suffix from gameplay pixels and a matching same-frame label. Later contiguous readings of the exact original OCR sentence remain alternate observations of that award, rather than a second hint; only the pixel-backed frames prove the circle. Recipient occlusion now also protects gaps where OCR omitted a hidden letter, removing an unsupported truncated identity.

The three remaining reference misses are energy −20 and two friendship recipient names. Source review finds the energy amount readable despite a cursor-affected glyph; it remains a recognition miss. The two full recipient identities are not source-proven, so a separate reference version records name observability exceptions without changing the 58-effect denominator or making the evaluation pass. All 347 unit tests and all 12 preserved `regression-31` checks pass. Full-recording recall and the Go gate remain open.

A bounded 60 FPS probe of 176500–177000 ms did not recover that energy amount. It decoded 30 frames; all 19 energy-bearing OCR observations still omitted the final zero and sentence terminator. The gameplay pixels show the expected amount, but the automated result remains unresolved. The unsuccessful probe is kept separately and was not added to the canonical recording or counted as improved recall. Repeating the same sampling and OCR method is not a supported next step.

Base frames and native inspection frames now use the same source-bound receipt-symbol path. Rebuilding the separate recording removes another duplicate `O` hint at 335200 ms while retaining its pixel-backed circle award and alternate OCR evidence. The reviewed 2:40–4:40 score remains 55/58 with no extras; all 348 tests and the preserved `regression-32` checks pass. This consistency fix does not add source-review coverage.

A new negative reference reviews all 40 samples in the first 10 seconds of the separate recording: setup menus and no gameplay award receipts. It scores zero extras. Together with the unchanged 115 seconds of earlier references, declared sampled-effect coverage is 125 seconds (7.035%). This is still not full-source or native-frame recall. The coverage auditor checks reference/sample hashes, avoids double-counting overlaps, and identifies 0:10–0:20 as the next unreviewed source interval. See [source review procedure](source-review-coverage.md).

The next two source-ordered intervals, 0:10–0:20 and 0:20–0:30, now pass 8/8 inheritance effects and the 1/1 introduction SP award. Persistent receipt lines across the interval boundary are not counted again. Combined declared sampled-effect coverage reaches 145 seconds (8.161%); the next uncovered window starts at 0:30. No analyzer or threshold changes were required.

The 0:30–0:40 source review exposes a cursor-covered friendship recipient. Initial recognition emitted Super Crek instead of Super Creek. Native sampling repeats the misspelling, so it is not accepted as independent spelling evidence. The occlusion check now protects the recipient-name span as well as the numeric amount. After the fix, the reviewed interval retains two correct effects and one unresolved name, with no extra wrong-name prediction. The source timing bracket was separately adjudicated because native sampling exposed the receipt 50 ms before the first 4 FPS reference sample; both reference versions and scores remain preserved. The Power training action passes 1/1. Both ledgers and the earlier frozen-reference regressions still pass. Declared sampled-effect coverage is 155 seconds (8.724%), including this failing reference; coverage is not accuracy.
