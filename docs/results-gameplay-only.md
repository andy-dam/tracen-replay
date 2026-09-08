# Gameplay-only development results

The complete English Our Grand Concert recording was processed from 00:00 through 31:06.333. All measurements use this one development source, SHA-256 `a75008eb095a2d37f9d0b49d09f1a54be8285bd9ef69e243037440a580193174`. They do not establish accuracy on another recording. Media, weights and generated reports remain local.

## Source coverage

- 7,466 base frames at 4 FPS, no sampling gap above 250 ms, final sample 83 ms before the end.
- Detected training windows inspected at 30 FPS; six difficult animations additionally inspected at native 60 FPS. A decoder interval rounding bug that previously skipped alternate native frames was fixed and tested against a generated 60 FPS video.
- All 11,569 base and supplemental observations passed the source-frame, crop-pixel and original-OCR hash audit. All 7,936 present refinements matched their original observations and proof images.
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

122 automated tests pass, covering auxiliary-pixel isolation, source tampering, PTS continuity, native sampling, animated digit conflicts, cursor-obscured receipt digits, delayed lesson balances, canceled outings, cart removals/upgrades, circle variants, calendar omissions/duplicates and evaluation penalties.

All 58 reviewed unparsed receipt candidates now have matching accepted evidence in their nearby receipt. Four actual gaps required denser inspection: energy deductions of 18 and 14, unchanged friendship with Sirius Symboli, and maximum friendship with Light Hello. The source-linked fixture preserves the manual statements and screenshot hashes. This candidate-selected check does not measure effects that never became candidates. Recreation unlocks and inheritance inspiration/spark triggers are typed separately without inventing their rewards.

Two contiguous development references pass:

- Opening 00:25-01:22: 5/5 training/rest actions, correct options, no extras.
- Lessons and first concert 05:41-06:54: 11/11 transactions (10 lessons and one concert), exact reviewed costs and stat/fan awards, no extras. A canceled dialog is excluded. Decorative name symbols, choices, hype and complete non-stat effect recall are outside this score.

The earlier 15 reviewed point examples remain narrow fixtures, not a full-run accuracy score.

```powershell
python -m unittest discover -s tests -v
python -m tracen_replay.receipt_review tests/fixtures/full-action-receipt-review-v1.json .local/full-recording/run-01/report.json --evidence-root .local/full-recording/run-01 --output .local/full-action-receipt-evaluation.json
python -m tracen_replay.candidate_review tests/fixtures/unparsed-receipt-review-v1.json .local/full-recording/run-01/report.json --evidence-root .local/full-recording/run-01 --output .local/receipt-candidate-evaluation.json
python -m tracen_replay.transaction_evaluate tests/fixtures/lesson-concert-sequence-v1.json .local/full-recording/run-01/report.json --output .local/lesson-concert-evaluation.json
python -m tracen_replay.verify_evidence "C:/path/to/recording.mp4" --output .local/full-recording/run-01
```

## Remaining boundary

`fully_verified` and `go_ready` remain false. Full non-stat effect recall, choices, complete skill inventory, uncertain song symbols, race item identities and all active concert bonus values are not yet validated. Some fields need additional visible UI evidence rather than more arithmetic. No independent recording has been supplied. See [active gates](gameplay-only.md) and [reproduction guide](full-recording-analysis.md).
