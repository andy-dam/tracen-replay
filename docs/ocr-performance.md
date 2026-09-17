# OCR performance

## Sampling rate

`tracen_replay.full_recording` decodes the whole recording once at a base
rate of 1 to 8 sampled frames per second; the default, and the rate the
service asks for, is 4 fps (`analysis_job --fps`, default `4.0`). Within that
base pass, bounded windows the analyzer flags as needing a denser look (a
training result animation, an unresolved boundary) get a second, narrower
pass decoded at 60 fps instead of resampling the same base-pass frames; the
report labels this a "reread".

## OCR worker pool

`analysis_job` and the underlying `full_recording` CLI take `--workers`
(1-8, default 4) for the base OCR pass and `--dense-workers` (1-8, default
`--workers` minus one, at least one) for the dense reread passes. The Go
service exposes the same controls as `-workers` and `-dense-workers` on
`cmd/tracen`. Every OCR worker collects garbage every 25 frames: under Python
3.14 a worker otherwise piles up cyclic garbage holding decoded images and
climbs from about 1.4 GB past 6 GB before the collector frees it. With the
collection, on a full career no worker went past about 2.2 GB with 5 base and
4 dense workers, so the worker counts are bounded by free cores rather than
memory.

## Device selection

`tracen_replay.vision.ocr_device()` resolves the OCR execution provider.
`TRACEN_REPLAY_OCR_DEVICE` can force `cpu`, `dml` or `cuda`; the default,
`auto`, asks the installed `onnxruntime` for its available execution
providers and picks the first of `DmlExecutionProvider` (DirectML) then
`CUDAExecutionProvider` (CUDA), falling back to the CPU provider if neither
is present. The Go service passes this through as `-ocr-device` (default
`auto`) on `cmd/tracen`. DirectML sessions are not safe to share across
threads of one process, so when `dml` is selected, `full_recording` gives
each OCR worker its own process.

## What a report says about the device

Every report's `recognition` object records `enabled`, `model` (the RapidOCR
detector/recognizer model names) and `device`, the resolved value from
`ocr_device()` (`dml`, `cuda` or `cpu`). This is the actual provider the run
used, not the requested setting; `cmd/tracen`'s readiness check reports the
requested `-ocr-device` value and notes that the resolved device is the
report's own field.

## Compact images

`NeuralReader` wraps the RapidOCR engine in `CompactInputEngine`, which hands
it every image as one compact array. Callers pass BGR views flipped from RGB
arrays, and the engine cuts each detected text box out with OpenCV's
`warpPerspective`, which copies such a view in full for every cut. On a
gameplay pane with about 30 text lines that was more time than detection and
recognition together. The same pixels read identically either way, so the
wrapper leaves every observation and the reader's cache fingerprint as they
were. Recognition-only calls on small crops were not affected.

## Where the time goes

Measured on this machine (DirectML on an RTX 5060 Ti) over 120 panes of a
35-minute recording, one process at a time:

| One base frame | Time |
|---|---|
| Detection and recognition of the full pane | about 95 ms (375 ms on the CPU provider) |
| The rest of `NeuralReader.read` (fixed regions, layout) | about 20 ms |
| Saving the pane as PNG | about 46 ms |
| Parsing the observation | about 28 ms |
| Hashing, decoding and cropping the frame | about 10 ms |

Larger recognition batches (24 lines instead of 6) did not help. The saved
pane PNGs are hashed byte for byte by later stages, so their compression is
part of the evidence, not a free setting; instead a pane's PNG is encoded on a
background thread while the pane is read (`proof_writer`), since PNG encoding
releases the GIL. Later stages check the same proof files many times over: a
rebuild of one career decoded 41,506 images. Pixel fingerprints are therefore
kept per file path, size and modification time (`frame_cache.rgb_digest`), and
the choice observations built before and after boundary recovery are
remembered per unchanged proof file.

The base OCR pass scales with worker processes while the GPU stays mostly
idle, each worker running the whole per-frame loop:

| Base OCR workers | Frames per second |
|---|---|
| 3 | 13.2 |
| 4 | 16.2 |
| 5 | 18.7 |

End to end on the same 35-minute recording, every report identical:

| Analysis | Minutes |
|---|---|
| Before the compact images, 3 workers and 2 dense workers | 48.6 and 47.5 |
| Compact images | 35.5 |
| Proofs written while reading, repeated checks remembered | 32.0 |
| The same with 5 workers and 4 dense workers | 24.0 |

These are observed runs, not a controlled benchmark, and not a throughput
guarantee for another machine or recording.

## In the application image

The [application image](container.md) installs the CPU provider, so the same
per-frame work costs more. Measured on this machine inside the container, with
two base workers and one dense worker, a 45-second clip read its 181 base
frames in 57.7 seconds: 3.1 frames per second, or 1.6 per worker against the
4.4 per worker of the DirectML runs above. The whole clip, from capture to a
saved report, took 132 seconds, and the worker's main process stayed under
1 GB. No full career has been analyzed in the image yet, so the end-to-end
table has no container row.

See [analysis-job.md](analysis-job.md) for the full set of worker
controls and [evaluation.md](evaluation.md) for how OCR reading errors are
tracked and, eventually, reduced with learned readers.
