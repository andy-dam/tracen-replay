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
`cmd/tracen`; its flag help notes that each dense worker can peak near
5-6 GB of memory, so `-dense-workers` is effectively the memory knob of a
run, separate from `-workers`.

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

Three OCR processes running together each slow down by about a third while
the GPU stays mostly idle; larger recognition batches (24 lines instead of 6)
did not help. The saved pane PNGs are hashed byte for byte by later stages,
so their compression is part of the evidence, not a free setting.

End to end, three service analyses of the same 35-minute recording with 3
workers and 2 dense workers took 48.6 and 47.5 minutes before the compact
images and 35.5 minutes after, with an identical report; the base OCR pass
went from about 17 to 11 minutes and the training result rereads from 11 to
7.5. These are observed runs from the service's job records, not a controlled
benchmark, and not a throughput guarantee for another machine or recording.

See [analysis-job.md](analysis-job.md) for the full set of worker
controls and [evaluation.md](evaluation.md) for how OCR reading errors are
tracked and, eventually, reduced with learned readers.
