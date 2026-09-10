# OCR performance measurements

The full third-recording baseline processed 10,099 samples at 4 FPS across
42:04.583 of footage. Its OCR stage took 5,489 seconds, approximately 91.5 minutes,
using four CPU workers. This excludes subsequent semantic validation and fixes.
It is a measured stage duration, not a promise of total analysis time.

## CPU worker trial

A bounded trial on a Ryzen 5 9600X used the same 48 evenly spaced frames from the
original development recording for each configuration. Each worker initialized
and warmed its own OCR reader before timing. The measured section includes image
loading, crop preparation and OCR. It excludes video decoding, evidence/cache
writes, later refinement passes and source review. Each ONNX Runtime session used
two intra-operation threads and one inter-operation thread.

| Workers | Time for 48 frames | Frames/second | Average logical CPU cores used |
| --- | ---: | ---: | ---: |
| 2 | 24.00 s | 2.00 | 3.55 |
| 4 | 26.45 s | 1.81 | 5.25 |
| 6 | 25.81 s | 1.86 | 6.79 |
| 8 | 27.96 s | 1.72 | 5.90 |

All four configurations produced exactly equal raw OCR outputs. This checks
consistency across worker counts, not correctness against source labels.
Increasing workers did not improve throughput in this single small trial.
Contention and per-frame processing overhead remain candidates for profiling;
the trial does not isolate their relative contributions. No production default
was changed on this evidence alone.

Local artifacts are stored under `.local/ocr-worker-benchmark-v1/`: the selection
manifest binds source-frame hashes and reader code, each worker result retains
its complete observations, and `comparison.json` contains timing and equality
results. The trial changes no recording cache or frozen evaluation output.

## GPU path

The installed runtime exposes `CPUExecutionProvider` and
`AzureExecutionProvider`, but no CUDA provider. The Azure provider name does not
mean this pipeline is using Azure cloud compute. The local RapidOCR configuration
has CUDA disabled, so the RTX 5060 Ti is not currently used for OCR.

RapidOCR supports ONNX Runtime's CUDA provider. A GPU experiment should use a
separate environment with compatible ONNX Runtime, CUDA and cuDNN versions, then
verify that the actual detector and recognizer sessions use CUDA without silent
CPU fallback. Merely installing a GPU package is not sufficient evidence.
See the [ONNX Runtime CUDA provider documentation](https://onnxruntime.ai/docs/execution-providers/CUDA-ExecutionProvider.html).

Benchmark the same source-bound frames, include startup and transfer overhead,
and compare parsed fields and source-labeled results as well as speed. GPU batch
size and CPU worker count need separate tuning. GPU acceleration may improve
inference; it does not remove source review or resolve ambiguous observations.
