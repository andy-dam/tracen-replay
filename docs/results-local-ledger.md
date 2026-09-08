# Local ledger development results

The local ledger milestone passed its scoped development checks on 2026-09-08.
These are the original baseline results. The subsequent
[log-identity milestone](milestone-log-identity.md) resolves the historical-entry
failure and adds sampled outcome visibility.
The pipeline processes source video without reference annotations, exports
timestamped evidence, reads current stats, and shows unresolved accounting.

## Evaluation

All clips come from the same English 1920×1080 recording. The 38 timestamped
references were transcribed from screenshots by an assistant and used during
development; they are not independent test data or independently reviewed labels.
Source SHA-256 binding prevents applying them to a different recording.

| Check | Result |
|---|---:|
| Source video processed | 212 seconds across three clips |
| Sampled evidence frames | 848 |
| Readable reference screens | 14 |
| Correct accepted stat fields | 84 / 84 |
| Complete correct readings on readable references | 14 / 14 |
| Negative reference screens with no accepted stat fields | 24 / 24 |
| Correct accepted goal countdowns | 8 / 8; abstained on 6 additional references |
| Correct preview/non-preview labels checked | 4 / 4 |
| Correct preview options checked | 3 / 3, all Speed |
| Reference timestamps covered by matching checkpoints | 14 |
| Regression tests | 26 passed |

These metrics cover sparse reference timestamps. They do not measure every
emitted field, training-option recognition for all five options, event recall,
or accuracy on other runs. The first supporting screenshot of each of the 16
emitted checkpoint spans was also visually inspected; its six totals matched.
Automatic reports retain `human_verified: false`.

| Clip | Frames | Checkpoint spans | Arithmetic balanced | Unresolved |
|---|---:|---:|---:|---:|
| 00:25–01:22 | 228 | 8 | 7 | 0 |
| 01:22–03:22 | 480 | 8 | 6 | 1 |
| 06:20–06:55 | 140 | 0 | 0 | 0 |

Equal totals separated by an OCR/visibility gap remain separate spans. Therefore
16 checkpoint spans do not mean 16 turns. The concert clip correctly provides
evidence without claiming current-stat checkpoints on the sampled lesson and
concert screens. Its absence of checkpoints is not successful event extraction.

End-to-end processing took approximately 56, 172, and 3 seconds respectively on
the development machine, including source hashing and extraction. These are
single-run timings, not a performance benchmark. Python 3.14, Pillow 12.3.0,
FFmpeg 9.0, and Tesseract 5.5.0.20241111 were used.

## Discrepancy investigation

In the opening clip, the initial log pass missed a +6 Wit / +5 Skill Pts outcome.
The denser pass found explicit main-pane outcome text at 00:50.250. The values
were read from that evidence rather than invented from the residual.

The 02:33.250–03:09.750 interval remains unresolved with a Stamina residual of
−5. The starting total is 256 and the ending total is 264, an observed +8. OCR
recognized a +8 training result and an additional +5 log entry, producing +13.
The screenshot at 02:35.500 visibly contains both entries. The +5 belongs to
earlier history already reflected in the starting total. Follow-up inspection
found that it was visible at the checkpoint, but a trailing OCR `|` caused the
parser to reject it. A later clean reading was consequently treated as new.
This illustrates why observation time
cannot be used as event time. The automatic result remains unresolved; the
manual diagnosis is not silently inserted into predictions.

The viewer exposes baseline blocks, repeated observations, provisional merge
decisions, initial/final residuals, and the sampled frames inspected during the
retry. Arithmetic balance is always marked for review; it is not verified event
coverage. No additional native-rate frames are decoded by the retry.

## Reproduction and next work

See [the pipeline guide](local-pipeline.md#development-reference-evaluation) for
the three extraction commands and the separate evaluator. Video and screenshot
bundles remain outside version control; numeric references and tests are
versioned. Reproduction of recording-specific results requires the matching
source file. The regression suite generates its own small synthetic media and
does not require that recording.

The next accuracy milestone should establish log occurrence identity using
surrounding entries and temporal continuity, handle history already reflected
in a checkpoint, and validate multiple training options. A second independent
recording is needed before reporting generalization. Go application work can
then consume the existing report contract without conflating preview evidence
with completed actions.
