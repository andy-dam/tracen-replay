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

## Memory

Measured on Windows, sampling the whole process tree once a
second. The analyzer's own process holds about 1.3 GB while frames are read,
3.4 GB while it assembles a full career and 4.8 GB at its peak, writing the
viewer page. A reader process holds about half a gigabyte. The tree committed
6.5 GB with three readers on the graphics card; the worst run seen committed
8.4 GB, with a single reader. The readers are the small part: the number of
analyses at once decides the memory, not the number of readers.

`internal/loadplan` plans the desktop application's load from these figures
(6 GB for an analysis, 0.5 to 0.6 GB for each reader, rounded up): the memory
limit in Settings sets how many analyses may run at once, and each analysis
gets the readers its share of the memory and of the processor allows. The
limit is a plan, not a fence. Nothing is stopped for crossing it.

## In the application image

The [application image](container.md) installs the CPU provider, so the same
per-frame work costs more. Measured on this machine inside the container
(Docker Desktop with 12 CPUs and 16 GB available to it), with the image's
default two base workers and one dense worker, a 45-second clip read its 181
base frames in 57.7 seconds, 3.1 frames per second, and reached a saved
report in 132 seconds. A full career, the 33-minute Mayano CM-prep
recording with 8,513 base frames, analyzed in the image the same day it was
analyzed on this machine's GPU with three base workers and two dense ones:

| Stage | In the image (CPU provider) | On the GPU (DirectML) |
|---|---:|---:|
| Base readings | 3,093 s (2.8 frames per second) | 712 s |
| Dense result-card rereads | 1,466 s | 411 s |
| Reload of cached readings | 623 s | 84 s |
| Refinement passes and recovery | 1,633 s | 393 s |
| Assembly, boundary recovery and output | 872 s | 225 s |
| Whole analysis | 7,818 s (2 h 10 min) | 1,841 s (31 min) |

The worker's main process peaked at 4.3 GB and each OCR worker stayed near
1.5 GB. The two reports are not identical: the CPU recognizer reads a few
frames differently, and those differences carry through the dense rereads
(9,434 readings against 9,453) into the accounting, which left one turn
field unexplained where the GPU run left none and took 15 amounts from the
learned reader against 11. Both runs found the action and the opening state
of all 74 turns and listed nothing for review. Reports are identical only
across runs on one provider; a container row cannot be compared frame for
frame with the table above.

See [analysis-job.md](analysis-job.md) for the full set of worker
controls and [evaluation.md](evaluation.md) for how OCR reading errors are
tracked and, eventually, reduced with learned readers.
