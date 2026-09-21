# Tracen Replay

Tracen Replay turns a recording of an Umamusume: Pretty Derby career into a
turn-by-turn report: every half-month turn with its stats and rank letters,
every training, race, purchase, song, skill and event, each linked to the
second of the recording it was read from. The report is shown beside the
video, and anything the analyzer flags can be checked and corrected there.

Recordings must be the English game at 1920×1080 with the log panel open.

## Ways to use it

| | What it is | Where the recording goes |
| --- | --- | --- |
| Website | https://tracen-replay.bluebay-878de3c5.northcentralus.azurecontainerapps.io | Uploaded and analyzed on the service. Analyses are limited per day and run on a processor, which is much slower than a graphics card. |
| Desktop application | Windows installer or zip, and a macOS disk image for Apple silicon, on the [Releases](https://github.com/andy-dam/tracen-replay/releases) page | Stays on the computer. No account. Uses the graphics card when there is one. Python, the OCR models and ffmpeg are included. |
| From source | The steps under [Run it from source](#run-it-from-source) | Stays on the computer. |

The desktop applications are not code-signed yet. Windows shows a
SmartScreen warning on first start ("More info", then "Run anyway"). macOS
refuses the application until the quarantine flag is removed:
`xattr -dr com.apple.quarantine "/Applications/Tracen Replay.app"`.

## Parts

| Part | Where | What it does |
| --- | --- | --- |
| The service | `cmd/tracen`, `internal/`, `api/openapi.yaml` | One Go binary: accounts, uploads, the analysis queue, reports, corrections, and the HTTP API. `tracen worker` runs analyses from a shared queue for the hosted deployment. |
| The client | `web/` | The Vue client, built by Vite and embedded into the binary. |
| The analyzer | `analyzer/tracen_replay` | The Python worker that reads the recording: frame capture, OCR, bounded 60 fps rereads, assembly into turns and entries, accounting. |
| The desktop application | `cmd/tracen-desktop`, `desktop/` | A Wails window around the same service, with the build scripts for the Windows and macOS bundles and the project's small ffmpeg build. |
| Deployment | `deploy/`, `Dockerfile`, `.github/workflows/` | The Azure template, the container images and the workflows that build, test, release and deploy. |

## Run it from source

Prerequisites: Go, Node.js, Python 3.11 or newer with the analyzer installed
(`pip install -e ./analyzer[vision]`), ffmpeg on `PATH`, and the OCR models
under `.local/models/rapidocr`.

```powershell
cd web && npm ci && npm run build && cd ..
go build -o tracen.exe ./cmd/tracen
.\tracen.exe
```

Open http://127.0.0.1:8765/, create an account, upload a recording and press
Analyze. The [local application guide](docs/local-app.md) covers the flags,
the report page, the review editor and the error messages.

The desktop applications are built with `desktop/build-windows.ps1` and
`desktop/build-macos.sh`. A version tag builds both and attaches them to the
release, and the "Desktop test build" workflow builds either one from any
branch.

The analyzer also runs on its own from the repository root:

```powershell
python -m tracen_replay.analysis_job "C:\path\to\recording.mp4" --output .local\runs\one --workers 4 --model-dir .local\models\rapidocr
```

## Tests

```powershell
python -X utf8 -m unittest discover -s analyzer/tests -t analyzer
go test ./...
cd web && npm run build
```

Tests that depend on locally preserved recordings skip when those files are
absent. Two tests create symbolic links and skip on Windows unless the
account may create them (Developer Mode or an elevated shell); they run on
CI, so a green local run on Windows is not the whole suite.

## Documentation

- [Local application guide](docs/local-app.md): build, start, accounts, uploads, the report page, review and corrections.
- [Architecture](docs/architecture.md): the service, the client and the analyzer, and how a recording becomes a report.
- [Analyzer pipeline](docs/analyzer-pipeline.md): the stages from frame capture to the timeline, and the evidence policy.
- [Recognition rules](docs/recognition-rules.md): what is read from each screen, what proves it, and what is never inferred.
- [Report contract](docs/report-contract.md): the fields of `report.json` and `timeline.json`.
- [Turn ledger](docs/turn-ledger.md): turn windows and the action each turn holds.
- [Analysis job](docs/analysis-job.md): the process contract between the service and the analyzer.
- [Evaluation](docs/evaluation.md): how the analyzer is validated, and the plan for learned readers.
- [OCR performance](docs/ocr-performance.md): sampling rates, worker pools and device selection.
- [The application image](docs/container.md): the whole application in one container, and what changes inside it.
- [Continuous integration and deployment](docs/ci-cd.md): what runs on a push, and how a merge to `main` updates the hosted site.
- [Deployment](docs/deployment.md): the hosted setup on Azure, its limits and its costs.
- [To do](TODO.md) and the [roadmap](docs/roadmap.md).

Repository conventions for contributors and coding agents are in
[AGENTS.md](AGENTS.md).

## License

Tracen Replay is source-available under the [PolyForm Noncommercial License 1.0.0](LICENSE.md): you may use, change and share it for personal, academic, hobby and other noncommercial purposes; commercial use is not permitted. The models it downloads and the libraries it depends on keep their own licenses (RapidOCR and the PP-OCR models under Apache 2.0, ONNX Runtime under MIT, OpenCV under Apache 2.0).

Tracen Replay is a fan-made tool. It is not affiliated with, endorsed by or connected to Cygames; Umamusume and its characters are the property of their owners.
