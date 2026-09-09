# Tracen Replay

Replay analysis for Umamusume: Pretty Derby.

Tracen Replay is being developed to turn gameplay recordings into editable timelines. Screen recognition and field extraction will connect each observation to a source frame, helping players review training runs and compare their progress.

**Status:** a local Python pipeline analyzes full recordings into source-linked actions, six-field stat accounting, lesson/skill transactions, races and concerts. It targets one English 1080p layout. Checkpoint accounting has been checked on two development recordings; complete action/effect recall remains open. The Go application and Azure deployment remain planned.

## Run locally

With Python 3.11+ and FFmpeg/ffprobe on `PATH`, run from the repository root:

```powershell
python -m tracen_replay "C:\path\to\recording.mp4" --start 25 --duration 57 --output .local/runs/pilot-01
```

Open the generated `index.html` to inspect the frames. The CLI leaves screen labels unknown and can import source-linked reference annotations. See the [local pipeline guide](docs/local-pipeline.md) for requirements, output format, annotation support, and tests.

Install the `analysis` extra and Tesseract, then add `--gameplay-only` to analyze the five attributes, skill points, and supported mechanics without reading the side log. Training previews do not count as completed actions, and unresolved differences trigger bounded additional sampling. See the [gameplay-only guide](docs/gameplay-only.md) for supported fields and remaining reliability gates. Legacy `--track-stats` uses the auxiliary log and cannot be combined with this mode.

## Planned features

- Timestamped timelines of training screens, events, and race results.
- Extracted stats and turn labels with source frames and unreadable values marked explicitly.
- Corrections that preserve the original prediction.
- Side-by-side run comparison and report export.
- Background video processing on Azure.

The full-recording neural runner processes video locally and investigates short training animations at higher frame rates. See [setup and commands](docs/full-recording-analysis.md) and [measured results and limitations](docs/results-gameplay-only.md).

## Design

A Go service will handle the web application, uploads, and job lifecycle. A Python worker will process video and run the vision model. Azure Blob Storage, SQL Database, and Queue Storage will provide media storage, durable state, and work delivery.

See the [architecture](docs/architecture.md) for the data flow and recovery model.

## Documentation

- [Roadmap](docs/roadmap.md)
- [Gameplay-only mechanics and validation](docs/gameplay-only.md)
- [Full-recording analysis](docs/full-recording-analysis.md)
- [Development results](docs/results-gameplay-only.md)
- [Local pipeline](docs/local-pipeline.md)
- [Local ledger milestone](docs/milestone-local-ledger.md)
- [Log identity and outcome visibility](docs/milestone-log-identity.md)
- [Architecture](docs/architecture.md)
- [Deployment design](docs/deployment.md)
- [Model evaluation](docs/evaluation.md)

Recognition results and a hosted demo will be added as those capabilities become available.
