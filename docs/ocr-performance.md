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

## One observed run

In one end-to-end run of the service on this machine, a 40-minute 1080p
recording produced 9,556 sampled frames at the base rate, and the OCR pass
over them took about 25 minutes with 3 workers on the auto-selected device.
This is a single observed run, taken from the service's job record, not a
controlled benchmark: it does not isolate device, worker count, resolution
or recording length as separate variables, and it should not be read as a
throughput guarantee for a different machine or recording.

See [analysis-job.md](analysis-job.md) for the full set of worker
controls and [evaluation.md](evaluation.md) for how OCR reading errors are
tracked and, eventually, reduced with learned readers.
