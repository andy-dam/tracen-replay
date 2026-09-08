# Tracen Replay

Replay analysis for Umamusume: Pretty Derby.

Tracen Replay is being developed to turn gameplay recordings into editable timelines. Screen recognition and field extraction will connect each observation to a source frame, helping players review training runs and compare their progress.

**Status:** early development. This repository currently contains the project design and roadmap; there is no runnable application or hosted demo yet.

## Planned features

- Timestamped timelines of training screens, events, and race results.
- Extracted stats and turn labels with source frames and unreadable values marked explicitly.
- Corrections that preserve the original prediction.
- Side-by-side run comparison and report export.
- Background video processing on Azure.

The first release targets short clips from one supported recording layout. Longer recordings and additional layouts will follow validation of the initial pipeline.

## Design

A Go service will handle the web application, uploads, and job lifecycle. A Python worker will process video and run the vision model. Azure Blob Storage, SQL Database, and Queue Storage will provide media storage, durable state, and work delivery.

See the [architecture](docs/architecture.md) for the data flow and recovery model.

## Documentation

- [Roadmap](docs/roadmap.md)
- [Architecture](docs/architecture.md)
- [Deployment design](docs/deployment.md)
- [Model evaluation](docs/evaluation.md)

Setup instructions, example reports, and measured results will be added as the implementation becomes available.
