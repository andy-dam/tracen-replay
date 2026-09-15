# To do

What comes next, in the order it should happen. Each item says what "done"
looks like. The design behind the learned readers is in
[docs/evaluation.md](docs/evaluation.md); the longer horizon is in
[docs/roadmap.md](docs/roadmap.md).

## 1. Release 0.1.0

- [ ] Decide that the current analyzer and application are the release, then
      tag `v0.1.0` and push. Done: the tag exists on the commit that carries
      the end-to-end acceptance, and the local records name the same commit.
- [x] Stop tracking `analyzer/tests/matrix.json`: the test that wrote it now
      writes a file it owns and removes, and the pattern is ignored. A test
      run leaves the working tree clean.
- [ ] Prune the local evidence that the records no longer need (the largest
      run roots hold frames whose reports are preserved elsewhere). Done: the
      handoff lists what was removed and what stays as the acceptance record.

## 2. Product gaps the end-to-end run showed

- [ ] A race-day turn's opening is carried from the previous turn's entries
      and shown as an estimate. Read the Full Stats panel when the player
      opens it on that screen, so the turn gets a real observation. Done: a
      recording where Full Stats was opened on a race day shows an observed
      opening, not an estimate.
- [ ] A report keeps the ledger of the analyzer that made it. Show the
      analyzer version on the report page and offer "Analyze again" when the
      installed analyzer is newer. Done: an old report says so and the button
      is one click.
- [x] The dashboard tile says "queued or running" for the analyses it
      counts.
- [ ] Re-assemble a finished report without a new OCR pass when the working
      data was kept (`-keep-working-data`), so a parser change refreshes old
      reports in minutes. Done: a job option that runs the analyzer's
      reparse mode against a kept run directory.

## 3. Learned readers (the neural network)

The rules already decide which screen is on frame; what still fails is
reading small fixed crops. The accounting labels them for free. Order:

- [ ] **Dataset builder.** A tool under `analyzer/tools` that walks finished
      reports and writes, per recording, the stat-badge and performance-
      counter crops with the value the accounting confirmed (balanced fields),
      the crops it marked conflicted or unexplained (hard cases), and the
      frame and timestamp each came from. Split by recording and by recorder;
      held-out recordings never feed training. Done: a dataset directory with
      a manifest, built from the six end-to-end reports, and counts per split.
- [ ] **Baseline.** The current OCR reader's accepted-read accuracy and
      coverage on that dataset, per field. Done: one table in the local
      records, reproducible by a tool.
- [ ] **First model: badge and counter reader.** A small CNN that reads a
      digit string and a confidence from a fixed crop, trained on the
      dataset; compare a frozen pretrained backbone with a linear head
      against a fine-tuned one; export to ONNX so it runs beside the OCR
      models on the same providers. Done: the model beats the baseline on the
      held-out split and its latency per crop is measured on CPU.
- [ ] **Integration as a reader.** The model becomes one more reader in the
      analyzer: it yields an observation with a frame, a value and a
      confidence, behind a flag; the accounting stays the arbiter and a model
      value never fills a field on its own. Done: a fresh run of a held-out
      recording with the flag on shows fewer unexplained and conflicted
      fields than without, and no new false values.
- [ ] **Second model: text repair.** Learn the recognizer's character
      confusions from receipt lines paired with their resolved names, and
      replace the hand-set edit distances with one repair that uses the
      learned confusions and a shared word list. Done: the per-rule
      thresholds are gone and the held-out receipt names resolve at least as
      often.
- [ ] **Later:** boundary and animation-state detection, using the current
      rules' decisions as labels.

## 4. Code and repository hygiene

- [ ] Tests that depend on locally preserved evidence now name it through
      `analyzer/tests/localdata.py`. Next: either publish small fixtures for
      the rules they cover, or move the rest under `analyzer/lab/tests` so a
      clean checkout's skip count is expected. Done: the main suite has no
      skips on a clean checkout.
- [x] The `analyzer/lab` tools are evaluation history; the directory's
      README says so.
- [ ] The analyzer package keeps its name. Revisit only if it is ever
      published on its own.

## 5. Containers

Running the service anywhere but this machine starts here, before any
hosting choice.

- [ ] **One image for the local application.** A Dockerfile that builds the
      client and the Go binary, installs Python with the analyzer and its
      `vision` extra, ffmpeg and the OCR models, and runs `tracen` on a
      configurable port with the data directory on a volume. OCR runs on the
      CPU provider inside the image (DirectML is Windows-only); a CUDA
      variant of the image is a build argument. Done: `docker compose up`
      on a clean machine serves the front page, accepts an upload, and a
      full career analyzes to a report on the CPU provider; the time it
      takes is recorded in the OCR performance note.
- [ ] **A worker image.** The analyzer alone, taking the same command line
      the service uses (`docs/analysis-job.md`), so the service can start it
      as a container instead of a child process. Done: the service has a
      runner that talks to a worker container and the end-to-end run passes
      through it.
- [ ] **Image hygiene.** Multi-stage build, pinned base images and model
      files, no recordings or local evidence in the context (`.dockerignore`),
      health checks wired to `/healthz` and `/readyz`, and a CI job that
      builds the image. Done: the image builds in CI and its size is known.

## 6. Hosting

- [ ] The four changes in [docs/deployment.md](docs/deployment.md): accounts
      beyond one machine, an object store for uploads and job outputs, a
      queue that outlives one process, and GPU workers that are addressed
      rather than spawned. No provider is chosen yet.
