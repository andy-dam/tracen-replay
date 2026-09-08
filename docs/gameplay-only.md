# Gameplay-only reconstruction

Status: implemented experimental baseline; the reliability milestone is **not yet complete**. The Go service and Azure deployment remain behind the validation gates below.

See [development results](results-gameplay-only.md) for measured coverage, reviewed examples and unresolved cases.

## Run

Install the analysis extra (`python -m pip install -e ".[analysis]"`), FFmpeg, and English Tesseract. Then:

```powershell
python -m tracen_replay "C:\path\to\recording.mp4" --start 25 --duration 57 --fps 4 --gameplay-only --output .local/runs/gameplay-01
```

`--gameplay-only` and the legacy `--track-stats` mode are mutually exclusive. The legacy mode uses auxiliary log text and is retained for comparison, not as a fallback.

The supported layout is English 1920×1080, with the gameplay rectangle at `(148, 0, 958, 1080)`. Recognition receives only that 810×1080 crop. The stat reader receives a blank canvas populated with those pixels; the original side panel never reaches OCR. Gameplay evidence is exported separately as PNG files. Full source screenshots remain in the gallery for inspection, but are not recognition input.

## Report contract

`gameplay_tracking` contains:

- `readings`: sampled screen observations, raw regional OCR, current-stat readings, typed effects, and mechanics fields.
- `screens`: adjacent observations of the same screen. These are visibility spans, not reliable action counts.
- `training_previews`: browsed options, with no completed-action claim.
- `checkpoints`: repeated six-field state readings, including skill points. These are not automatically career turns.
- `intervals`: observed changes, supported changes, and unexplained residuals. Even arithmetic agreement does not verify event identity or prove that no offsetting changes were missed.
- `lesson_purchases`: a named confirmation whose projected resource balance matches a subsequently observed debit. Evidence includes the before balance, dialog, and after balance. Projected stat bonuses are not promoted into awarded stats.
- `skill_receipts`: visible acquisition receipts. The complete purchased skill list and transaction cost remain unknown.
- `investigation`: initial discrepancies and bounded source resampling. At a base rate below 8 FPS, up to two three-second windows are decoded again at 8 FPS. Additional frames retain original PTS and are merged by timestamp. An unsuccessful investigation leaves the residual unresolved.

The first investigation targets a visible result/outcome near each selected discrepancy. This budget is intentionally finite. It does not search the entire recording or guarantee recovery of short animations.

## Mechanics and counting rules

| Mechanic | What is recorded now | What must not be counted as an award | Remaining work |
|---|---|---|---|
| Training | Preview option separately from the option on a result grid; partial result totals and caps; repeated compatible result values can support a partial state change | Browsing, last highlighted option, preview gains, or stat caps | Robust animated digits, failure results, complete gain recovery and action identity |
| Ordinary events | Explicit attribute/SP changes, energy recovery/loss, mood direction, named friendship changes and skill hints | Dialogue, offered effects, inferred missing rewards | Choice text, actual selection evidence, event titles and duplicate/fragmented receipt identity |
| Races | Result screen, fan total/gain, readable course description | Race entry, strategy choice, fan gain as skill points, item icons as named rewards | Placing, race identity, entry/finish association, item recognition and subsequent stat rewards |
| Skills | Selection, confirmation and acquisition receipt are distinct | Available skills, hint discounts, previously obtained skills, selected-but-uncommitted costs, or final owned-skill lists | Complete purchased names, upgrades, and verified batch SP debit |
| Lessons | Five performance currencies; confirmation name, projected balance and immediate/future/queued effect semantics; matching observed resource debit | Offered cards, preview stat gains, or unchanged menu balances immediately after a dialog | Complete purchase recall, complete current stats, observed stat awards and song inventory |
| Songs | Explicit song-learned receipt with name; acquisition source/cost unknown unless separately established | Automatically learned story songs as paid lessons | Distinguish all acquisition routes and persist activated bonuses |
| Concerts | Start confirmation, context-supported result banner, explicit aftermath stat changes and hype observations | Start dialog, playback settings, future bonus descriptions, or stat-cap increases as current stats | Concert identity, queued-to-active bonus transition, complete reward/skill-point attribution |
| Scenario resources | Explicit Dance/Passion/Vocal/Visual/Composure changes, supporter names and hype direction | Performance points as character stats or hype changes as an assumed numeric amount | Complete resource ledger across training, lessons and concerts |
| Career completion | Summary and finish confirmation separated from skill acquisition | Existing owned skills as new purchases; missing skill points as zero | Final-state cross-check and complete inventory reconciliation |

For Our Grand Concert, lesson effects can apply immediately, modify future training, or wait for a concert. Multiple lessons can occur within one career turn. These distinctions follow the [GameTora scenario guide](https://gametora.com/umamusume/our-grand-concert); the parser uses visible English UI terms. It does not calculate unobserved rewards from guide formulas.

`Speed went up by 6 to new heights.` records **6**. The displayed reduction has already happened; the analyzer does not halve it again. `Training Power Gain +1` is a future modifier, not an immediate Power award. A skill hint is not skill ownership. The skill-selection point counter can show a projected remaining balance, so changing it does not establish an SP transaction.

## Validation gates before the application

- [x] Make the gameplay crop a structural boundary and test that changing auxiliary pixels leaves the recognizer input and report unchanged.
- [x] Separate confirmations, previews, receipts, resource types and future effects.
- [x] Evaluate reviewed examples from training, events, lessons, races, skills and concerts.
- [x] Decode additional source frames when selected stat discrepancies remain unresolved.
- [ ] Explain training gains consistently from independent result evidence, with no auxiliary log.
- [ ] Recover event identity without double-counting fragmented or repeatedly visible outcomes.
- [ ] Validate complete skill/lesson purchase transactions and concert rewards, rather than only screen recognition.
- [ ] Review a densely labeled contiguous sequence for action/effect precision and missed-event recall.
- [ ] Repeat on an independent recording with recording-level evaluation separation.

The currently available recording supports development checks. It cannot establish generalization to another recording, trainee, scenario, layout or language. A finite-state mechanics model and resource accounting are useful safeguards; neither replaces missing visual evidence.
