# Local video evidence pipeline

The local pipeline extracts timestamped screenshots and exports a JSON report with an offline HTML gallery. Optional experimental OCR reads current stats, identifies stable checkpoints, and reconciles their differences with explicit outcome text. General screen recognition is not yet implemented. Reference annotations remain separate and are never used as OCR inputs.

## Requirements

- Python 3.11 or newer.
- FFmpeg and ffprobe available on `PATH`.
- A local video file. This pilot accepts at most 1920 × 1080 pixels, up to 120 seconds per invocation, and 1–8 sampled frames per second. A longer source recording is allowed when selecting a short interval.

Evidence extraction has no third-party Python runtime dependencies. Stat analysis additionally requires Pillow and Tesseract with English language data. Run commands from the repository root. Check the installed tools with `python --version`, `ffmpeg -version`, and `ffprobe -version`.

## Run

```powershell
python -m tracen_replay "C:\path\to\recording.mp4" --start 25 --duration 57 --fps 4 --output .local/runs/pilot-01
```

Times are source-relative seconds. This selects the half-open interval 00:25–01:22 without copying or modifying the source video. Four samples per second is the initial inspection setting, not a guarantee of capturing every screen. Use `--fps 8` for a denser bounded pass.

The output directory must not already exist. Choose a new name for each run. An unsuccessful run does not publish a report; temporary artifacts are cleaned up when possible. Each FFmpeg/ffprobe process has a 180-second timeout. This is a trusted local tool, not a hardened public upload service.

Optional: install the CLI in a virtual environment with `python -m pip install -e .`, then use `tracen-replay` in place of `python -m tracen_replay`.

## Output

```text
pilot-01/
  report.json
  index.html
  frames/
    000001.jpg
    ...
```

Open `index.html` in a browser. It works offline and links to full-resolution JPEG evidence. Move the entire directory to retain those links. The video itself is not embedded or copied into the bundle. Filenames and screenshots can contain personal information; review a bundle before sharing it.

`report.json` records:

- Source SHA-256, dimensions, codec, size, duration, and media time origin. It omits the absolute source path.
- Selected source interval and sampling configuration.
- Each frame's integer source PTS, time base, source timestamp in milliseconds, clip-relative timestamp, and relative evidence path.
- Optional imported point annotations, their provenance, and an annotation-file hash.
- Explicitly disabled recognition, unknown screen labels, and sampling limitations.

The selector keeps original decoded frames separated by at least the requested sampling interval. It uses presentation timestamps, rather than frame index divided by an assumed frame rate. Variable-frame-rate input may produce fewer samples. Raw PTS and time base are retained because millisecond timestamps are rounded.

## Reference annotations

An optional JSON file must be tied to the exact source file's SHA-256:

```json
{
  "source_sha256": "replace-with-the-source-file-sha256",
  "observations": [
    {
      "timestamp_seconds": 30,
      "screen_label": "training_result",
      "title": "Training success",
      "note": "Result animation; totals may still be changing.",
      "annotation_method": "human_visual_review"
    }
  ]
}
```

Use `Get-FileHash -Algorithm SHA256 "C:\path\to\recording.mp4"` on Windows, or read the lowercase hash from a generated report. Hash strings in the annotation input are case-insensitive.

```powershell
python -m tracen_replay "C:\path\to\recording.mp4" --start 25 --duration 57 --fps 4 --annotations .local/annotations.json --output .local/runs/pilot-02
```

Only observations inside the selected interval are imported. Existing recording-audit manifests containing `source_sha256` and `observations` can also be used. Annotation text is escaped in the HTML output.

An annotation links to a sample only when their source timestamps match at millisecond precision. Otherwise it is marked `needs_exact_frame`; a nearby frame may already show a different screen. Imported labels never turn into automatic frame classifications. Event boundaries and confidence remain null. Re-encoding a recording changes its hash, so source annotations cannot silently attach to a different video.

## Verify

```powershell
python -m unittest discover -s tests -v
```

The tests generate a tiny synthetic variable-frame-rate video locally. They check source-time offsets, evidence links, annotation provenance, wrong-source rejection, invalid media, output preservation, and failure cleanup. No gameplay recordings are required or downloaded. Temporary test files stay under ignored `.local/test-runs/`.

## Next increment

Expand reviewed recordings and measure checkpoint accuracy and event coverage. Improve the identity of scrolling log entries, recognize more layouts, and read phase/goal information reliably. The Go service and hosted pipeline follow a useful local analysis result.

## Experimental stat tracking

Set up an isolated environment and install the analysis extra:

```powershell
python -m venv .venv
.venv\Scripts\python.exe -m pip install -e ".[analysis]"
.venv\Scripts\python.exe -m tracen_replay "C:\path\to\recording.mp4" --start 25 --duration 57 --fps 4 --track-stats --output .local/runs/stats-01
```

Tesseract must be installed separately with `eng` language data. The reader checks `PATH` and the standard Windows `Program Files/Tesseract-OCR/tesseract.exe` location. Use `--tesseract "C:\other\location\tesseract.exe"` to override it. Processing stays local and uses no paid API or cloud service.

The supported profile is a 1920 × 1080 English landscape capture with the gameplay pane on the left and a game-log panel on the right. Merely matching the dimensions does not establish layout compatibility. Other layouts are not validated. The full source remains unchanged.

The `stat_tracking` report section contains:

- Per-frame readings of Speed, Stamina, Power, Guts, Wit and Skill Points, with unknown values preserved as null. A fixed crop and color mask isolate current digits from grades, caps and preview gains. OCR scores are engine scores, not calibrated probabilities.
- Stable checkpoints requiring at least three consecutive complete matching readings spanning at least 500 ms. One-frame errors are not accepted as checkpoints. Agreement can still repeat a systematic OCR error, so checkpoints remain marked `human_verified: false`.
- Independent tracking of training-preview frames. Switching among options never contributes a gain or creates a completed action. An action type is only assigned from explicit, sufficiently readable `Training <option> Lvl ...` outcome-log headings. Otherwise it remains unknown; the last preview is never assumed to be the chosen action.
- Between-checkpoint observed differences, sums of independently OCR-read changes, and residual unexplained differences for all six fields. These checkpoints do not guarantee one interval per turn. The visible goal countdown is attempted separately and may remain unknown; it is not a global turn index.
- A provisional first log pass at approximately 1 fps, followed by all remaining intervening samples and main-outcome text. The full pass runs even when the initial arithmetic balances, because context identity and outcome visibility require ordered observations. It does not decode additional video beyond the configured rate.
- Raw log/outcome readings and candidate changes, supporting screenshot paths, initial residuals, and final residuals. Annotation inputs do not participate in this process.

The accounting is `observed difference - recognized awarded changes = unexplained difference`. Negative changes are supported, but skill purchase receipts and the game's cap/rounding rules are not modeled yet. Previews such as `Speed +13` and possible-choice effects are not parsed as awarded changes.

Final accounting tracks occurrences through unique ordered text and surrounding context. Equal stat changes alone do not establish log identity. An occurrence observed at or before the starting checkpoint is excluded from that interval. Reappearance after a gap remains provisional. A balanced interval only establishes arithmetic agreement for these six fields; it does not prove a complete event history or identify all causes.

The report now shows contiguous preview spans with recognized options or an
explicit unknown. These remain separate from outcome-log entries. Logged training
headings describe visible log content; assigning that entry to the current
interval is not verified. Every interval retains baseline blocks, all raw block
observations, partial/baseline merge decisions, and counts of inspected frames.
The audit details can be expanded in the offline viewer.

The `log_identity` section retains occurrence IDs, first/last observations, raw
delta readings, and paired evidence for context matches. `outcome_episodes` holds
separate main-pane visibility spans. Interval `outcome_associations` records
unique but unverified cross-pane matches or ambiguous multiple candidates.
`event_time_ms` and `action_time_ms` remain null: sampled outcome visibility is
not a click timestamp. See [the identity milestone](milestone-log-identity.md)
for matching thresholds and limitations.

Matching stat totals separated by unreadable samples remain separate checkpoints.
This prevents a period with hidden totals from being represented as continuous
observation. A zero residual across that gap does not establish that nothing
happened. The goal countdown is not an absolute turn number and can reset after
a goal; uncertain digits remain null.

## Development reference evaluation

The versioned reference file contains 38 sparse timestamped examples from one
development recording: 14 readable six-field screens and 24 screens without a
readable current-stat strip. References were transcribed from screenshots and
have not been independently reviewed. They are evaluation inputs only; the OCR
pipeline never reads this file.

With the matching local recording, generate fresh reports:

```powershell
python -m tracen_replay "C:\path\to\recording.mp4" --start 25 --duration 57 --fps 4 --track-stats --output .local/runs/m1-opening
python -m tracen_replay "C:\path\to\recording.mp4" --start 82 --duration 120 --fps 4 --track-stats --output .local/runs/m1-early-career
python -m tracen_replay "C:\path\to\recording.mp4" --start 380 --duration 35 --fps 4 --track-stats --output .local/runs/m1-concert
python -m tracen_replay.evaluate tests/fixtures/stat-reference-v1.json .local/runs/m1-opening/report.json .local/runs/m1-early-career/report.json .local/runs/m1-concert/report.json --output .local/evaluation.json
```

The evaluator refuses mismatched source hashes and overlapping prediction
timestamps, reports missing samples, and preserves existing output files. Exit
code 0 means all checked predictions are correct, no fields were accepted on
negative examples, and at least 80% of readable reference screens have all six
correct fields. Exit code 1 means these checks failed; 2 means invalid inputs.
Abstaining is not counted as a correct field prediction. Countdown and preview
results have separate counts. Checkpoint spans crossing a contradictory reference
are reported as errors.

This evaluates sparse development examples, not every frame or event. It does
not measure event recall, prove log occurrence identity, or establish performance
on unseen recordings. Additional independent recordings remain necessary.

Run extraction and accounting tests with `.venv\Scripts\python.exe -m unittest discover -s tests -v`. Default tests do not require gameplay recordings. Recognition on actual recordings still needs separate visual validation and independent sessions; unit-test success is not an OCR accuracy score.
