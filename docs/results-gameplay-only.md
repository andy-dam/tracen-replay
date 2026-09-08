# Gameplay-only development results

Evaluated against one English Our Grand Concert recording. These are development measurements, not an independent test set. The source hash is recorded in `tests/fixtures/gameplay-reference-v1.json`; source media and generated screenshots remain local.

## Checks

- 68 automated tests pass, including auxiliary-pixel isolation, preview/receipt semantics, delayed lesson counter updates, bounded investigation selection, and literal quotation marks in Tesseract TSV.
- 15/15 reviewed point examples match their specified screen/field expectations. Examples cover training, energy, songs, lessons, concert dialogs/results, race fans/course, skill confirmation/receipt, and career summary. This score does not measure all detected fields or event recall.
- A real CLI invocation analyzed 00:25–00:37 at 4 FPS and decoded 21 additional frames during discrepancy investigation, producing 69 samples with original timestamps.
- Five disjoint clips cover 144 seconds. The 576 base samples plus 42 additional opening samples produce 618 observations.

| Clip | Observations | Stable six-field checkpoints | Accounting result |
|---|---:|---:|---|
| 00:25–01:22, opening | 270 | 8 | 7 comparisons; 3 unresolved |
| 06:20–07:00, lessons/first concert | 160 | 0 | No complete checkpoint pair |
| 30:18–30:48, skills/completion | 120 | 0 | No complete checkpoint pair |
| 18:27–18:34, race | 28 | 0 | No complete checkpoint pair |
| 26:35–26:45, concert aftermath | 40 | 0 | No complete checkpoint pair |

No checkpoint pairs in a clip means **no stat-accounting result**, not zero errors. Ordinary dialogue, transitions and unsupported screens often remain unknown. The reports are not a complete action timeline.

## What the evidence establishes

- Training results identify the option independently of the browsed preview. Animated gain digits still cause substantial abstention.
- The opening investigation decoded 42 additional source frames in two three-second windows. Repeated result totals supported the first training's five skill points. It did not recover all remaining training gains, and three intervals remain unresolved.
- Makeup Basics has a supported 10-Visual-point purchase at 06:27.750, backed by an observed balance, the named confirmation, and the matching debit. The counter updates after the menu reappears. Its projected Guts gain remains separate from verified awards. Other lesson purchases in the clip are not all recovered.
- A Skills Learned receipt follows the final skill confirmation. The report does not claim a complete acquired-name list or verified SP cost. Selection-counter changes are not purchases.
- The reviewed Tenno Sho (Spring) result yields 135,920 fans and a gain of 27,384, with Kyoto/Turf/3200m/Long/Right/Outer course fields. Placing and item identities remain unread.
- The final concert aftermath yields displayed changes of Speed +6 and Stamina/Power/Guts/Wit +12 each. Playback settings are a separate screen. Concert bonus activation and complete reward attribution are not reconstructed.

## Repeating checks

```powershell
python -m unittest discover -s tests -v
python -m tracen_replay.gameplay_evaluate tests/fixtures/gameplay-reference-v1.json .local/gameplay-point-report.json --output .local/gameplay-evaluation.json
```

The second command requires a locally generated gameplay report covering the reference timestamps. It rejects missing samples, overlapping reports and a different source hash. It does not fabricate or redistribute the recording. Selected audited screenshots were used for the point checks; contiguous clips exercise the timeline separately.

Pixel-identical gameplay crops were reused when repeating the opening analysis; newly decoded investigation frames went through OCR. No expected labels, target residuals, or auxiliary log text were recognition inputs. These measurements are not a cold-run performance benchmark.

See [the active milestone](gameplay-only.md) for the remaining gates. Training-result reliability, event identity, complete transaction accounting and independent-recording evaluation remain ahead of the Go/Azure phase.
