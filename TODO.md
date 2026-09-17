# To do

What comes next, in the order it should happen. Each item says what "done"
looks like. The design behind the learned readers is in
[docs/evaluation.md](docs/evaluation.md); the longer horizon is in
[docs/roadmap.md](docs/roadmap.md).

## 1. Release 0.1.0

- [x] Decide that the current analyzer and application are the release, then
      tag `v0.1.0` and push. Done: the tag exists on the commit that carries
      the end-to-end acceptance, and the local records name the same commit.
- [x] Stop tracking `analyzer/tests/matrix.json`: the test that wrote it now
      writes a file it owns and removes, and the pattern is ignored. A test
      run leaves the working tree clean.
- [x] Prune the local evidence that the records no longer need (the largest
      run roots hold frames whose reports are preserved elsewhere). Done: the
      handoff lists what was removed and what stays as the acceptance record.

## 2. Gaps the six end-to-end reports show

Read from the latest report of each recording in the acceptance account
(four recorders, six careers), ordered by how much reviewer work each item
removes. The four this list opened with are closed; each item states what
its own measurement of the latest reports showed.

- [x] **Performance points earned by training are not read.** The real cause
      was narrower than this item claimed. A training can raise two
      performance currencies by the same amount, and the sidebar award crop
      was requested only by the dense result inspection, never by the
      ordinary reading pass. So one row was awarded and its equal partner
      became an unexplained change of training size. The ordinary pass now
      requests those rows. A full re-analysis of the Hishi Amazon recording
      closed all three of its affected turns: six readings took an award
      through the crop, on basis
      `same_frame_dedicated_result_performance_region`, with no crop/panel
      conflict anywhere in the run. Re-reading frames for the other six turns
      recovers four more. The two left over are held by rules that are right
      to abstain: one row whose merged panel line was misread as `5+200`
      conflicts with its own `+20` crop, and one whose panel row was not read
      at all, leaving the crop as sole evidence.
- [x] **Purchases are not always debited.** Only the skill half was still
      open: every lesson bought across the six reports already had an
      observed cost, and the performance gaps left on lesson turns have other
      causes. A skill batch whose price was shown already balanced; every one
      whose charge was never read left a bare negative skill-point gap,
      because no issue named it and no turn difference could reach it (a race
      in the turn short-circuited the skill-point field before any owner was
      considered). The batch now carries `unobserved_purchase_debit` whether
      or not it is attributable, takes the negative difference when it is the
      window's only unpriced batch
      (`sole_unpriced_skill_batch_takes_turn_residual`), and a race no
      longer swallows a negative difference it cannot have caused. This
      closes all three such turns in the six reports. Rebuilding the Hishi
      Amazon re-analysis with only this change reverted puts its two gaps
      back, which is what attributes them to it.
- [ ] **A receipt whose wording the recognizer damaged is not counted.** The
      count this item asked about is met: the latest report of each recording
      lists one or two receipt lines for review, not ten, and every numberless
      line traced back to an award counted from a later frame ("Speed went up
      by" at 481 s of one career, cut off by a loading screen, counted at
      493 s as `+12`). What is left is damage in the wording rather than in
      the number. A hint receipt at 586 s of another career is read on seven
      frames and cleanly on none, so its four hint levels for Pace Chaser
      Corners are missing from the accounting. The cursor rests on
      `level(s) for` with the panel's sparkles drifting over it, while the
      number and the skill come through every time. Six frames are zeroed by
      the occlusion gate, five of them read at 95 or better before it, and the
      seventh is not occluded at all but reads `or` for `for`.
      Two invariants close the shortcuts. Occlusion runs before the reader
      parses and a blocked line may not regain confidence later
      (`full_recording.parse_receipt_pixels`), so the five zeroed readings
      cannot be used downstream; and a damaged line may confirm a receipt that
      was accepted but never assert one, which
      `tests/test_receipt_ocr_gap_boundaries.py` pins by keeping `fr` for
      `for` unparsed so a malformed line between two awards cannot become a
      third. The sanctioned route is therefore the occlusion stage's own
      resolution path: give hint receipt lines the source-validated per-word
      alignment friendship receipts already get, resolve a line whose overlay
      boxes provably cover only the fixed wording between the amount and the
      name (as `boundary_repair_supported` does for a separator), and let the
      existing one-glyph tolerance repair `level`. Done: that hint is counted
      from the frame whose wording the overlay alone damaged, no receipt is
      counted from a line whose own number or name was not read, and the
      boundary policy above still holds.
- [x] **Stat badges partly unread on some training turns.** The learned
      reader closed these. Across the two careers analyzed with it, every
      training gain the report still works out from the turn difference is one
      of three things, and none is a badge the reader could see: a card no
      frame was ever classified as a result (7 fields over 2 trainings, the
      item below), a badge zoomed to its leading digit and completed from the
      difference (2, recorded as completions), and a card whose read
      contradicts the amount (2, now listed for review). The careers read
      166 and 168 gains from the cards themselves, 19 and 7 of them through
      the model.
- [ ] **A result card only ever called a candidate is never read.** A
      training result frame whose banner stays illegible is classified
      `training_result_candidate`, and both the learned reader and the
      accounting's search for a training's own frames require
      `training_result`, so such a card contributes nothing and every one of
      its gains is worked out from the turn difference. Two trainings across
      two careers lose all their fields this way, seven in total. In one, the
      card is on two sampled frames, at 162.0 s and 162.25 s, both
      candidates, and the dense reread of that window produced no result
      frame either. Done: those trainings' gains come from their own cards,
      and a candidate frame still supplies no gain the stat bars do not
      confirm.
- [x] **A completed lesson purchase is not recorded, so nothing charges it.**
      The receipt's name was damaged in every spelling, so the repair that
      adopts the confirmed request name refused it and the purchase was
      dropped with its cost. Two faults: a space the recognizer put inside a
      word made the receipt one word longer than the canonical and tripped a
      guard meant for an extra word, and the repair demanded a single
      confirmed name anywhere in the five seconds before the receipt, so a
      card the player opened and left made it ambiguous. A differing word
      count is now allowed while the variant carries no more characters, and
      only the last request run before a receipt counts. Confirmed against the
      recording first: each recovered purchase's dialog prints its own cost.
      End to end on B/Gran Concert, unexplained fields 17 to 8, nine closed,
      none newly unexplained. See
      `.local/final-reliability-v1/e2e-v48-remaining-gaps.md` gap 10.
- [x] **Receipts that need no review are listed for review.** A cut or
      garbled repeat of a receipt read within five seconds is marked
      `ocr_fragment`; a rejected friendship, joining or appearance line is
      marked `out_of_scope`; neither becomes an entry or a review item. The
      log folds a lesson's purchase, receipt and song into one card, so the
      song no longer shows twice.
- [x] **A training result without a committed action.** A window that
      expects one decision and holds exactly one training result and no
      committed action now takes that result as its action
      (`identity_basis: result_card_only`) and says the choice was not seen.
      Three such turns in one of the six reports. Its training owns the
      turn's remaining difference like any committed training.
- [x] **A receipt read again counts twice.** One frame between two reads of
      the same stat receipt parsed nothing, its line cut short or read just
      under the parse confidence, so the receipt became two awards. Four on
      B/Gran Concert: Skill Pts +120, Speed +5, Skill Pts +4 and Guts +5. Two
      left a gap the size of the award; the Speed one hid a wrong worked-out
      gain, 7 where the card shows +12, on a turn that looked balanced. Such a
      receipt now counts once; on the saved readings of all nine reports only
      those four change, and a fresh website analysis of B/Gran Concert drops
      exactly them and balances both turns, with the card's +12 read directly.
- [x] **A rest followed by lessons or the concert was dropped.** The next
      date came a minute after the recovery result, past the 30-second wait.
      Point-spending screens now extend the wait; two rests recovered in the
      Hishi Amazon recording.
- [ ] **A "N more" badge is read as a performance value.** On the training
      screen a small "8 more" badge sits above the Vocal row; one turn's
      opening read Vocal 8 where the row shows 4, which leaves +4 unexplained
      on the turn before and -4 on that turn. These are the only two
      unexplained fields left in recorder C's fresh website report. Done: that
      opening reads 4 and both turns balance.
- [ ] **Race-day and finale openings are estimates.** A race-day turn's
      opening is carried from the previous turn's entries. Read the Full
      Stats panel when the player opens it on that screen, so the turn gets
      a real observation. Where the recorder never opens it, the estimate is
      the right answer. Done: a recording where Full Stats was opened on a
      race day shows an observed opening, not an estimate.
- [ ] **Ambiguous effects.** Circle base variants, recipient identity and
      inheritance spark identity are the three reasons left. Done: each has
      a rule or is presented with the two candidates to choose from.
- [x] **Entries before the first turn.** The check screen lists them under
      "Before the Career Starts" and says they belong to the run, not to a
      turn.
- [ ] A report keeps the ledger of the analyzer that made it. Show the
      analyzer version on the report page and offer "Analyze again" when the
      installed analyzer is newer. Done: an old report says so and the button
      is one click.
- [x] The dashboard tile says "queued or running" for the analyses it
      counts.
- [x] The runs page shows one row per recording, with earlier analyses of
      the same recording kept under it rather than as rows of their own.
- [x] ~~Re-assemble a finished report without a new OCR pass~~ Decided
      against (2026-09-16). A rebuild reuses the saved readings, so a change
      to what is read off the screen can only be checked by a fresh run; a
      change to the accounting is checked in seconds by running that module
      against the saved report; only the recovery and assembly stages in
      between would gain, about 17 minutes of a 40-minute run, at the cost
      of keeping ~5 GB of working data per recording and a job, endpoint and
      store column to maintain. The analyzer's own `--reparse-only` mode
      stays for evaluation work. Do not re-add this as a product feature.

## 3. Learned readers (the neural network)

The rules already decide which screen is on frame; what still fails is
reading small fixed crops. The accounting labels them for free. Order:

- [x] **Dataset builder.** `analyzer/tools/build_reader_dataset.py` cuts
      every result box of every training result frame, and the lesson menu's
      counters, and labels each by what it shows (`badge`, `gain`, `blank`
      or `unknown`) from the stat bars around the card and the receipts
      between them, not from the reader's own output. Split by run, with
      held-out runs and recorder groups in the manifest. The six end-to-end
      job runs were pruned by the service, so the first build comes from the
      three preserved full-recording runs (22,279 boxes, one run held out);
      the six recordings need `--rehydrate-frames` first to join it. See
      [docs/evaluation.md](docs/evaluation.md).
- [x] **Baseline.** `analyzer/tools/reader_baseline.py` judges a reader per
      card (value read, gain recovered) and counts false reads per frame. On
      the held-out run's 4-per-second frames the analyzer's own reader reads
      the value on 72% of 336 cards and recovers 71% of 174 gains, with 12
      false reads. On the training runs, where it also rereads each card at
      60 frames per second, it reaches 95% and 100%. Most frames it misses
      show the box blank or covered by the card's animation. The table is in
      the local records beside the dataset.
- [x] **First model: result box reader.** Our own network, trained from
      nothing on the GPU with `analyzer/tools/train_reader.py`, transcribes
      a result box (`value/`, `+N` or nothing) with a confidence per
      character, and is exported to ONNX. The dataset covers thirteen
      careers decoded from their recordings, with wider boxes. Two recorders
      and one of the user's recordings never feed training. Round 2 at
      confidence 0.9 over those three recordings reads the value on 750 of
      888 cards (current reader 643) and recovers 426 of 461 gains (370): 87%
      and 96% for one unseen recorder, 89% and 87% for the other, 79% and 92%
      for the user's recording. When two frames at least 200 ms apart must
      agree, it settles 337 values against 211, and both are wrong on 3 or 4
      cards. It transcribes 99.8% of labeled badges and 98.6% of gains
      exactly. The remaining misses are mostly frames that never show the
      number: of the 138 unread values, over a hundred are blank or covered
      by the gain overlay on every sampled frame. A by-eye audit of 50 random
      crops per label source found 0 wrong badge labels, 0 wrong gain
      numbers, 2 of 50 blanks with faded text, 1 of 50 hard frames missing a
      digit, and 1 of 50 held-out answers off. 1.28 ms per box in onnxruntime
      on the CPU, 0.54 ms on DirectML. The model to ship is trained on every
      run with `--final`.
- [x] **Integration as a reader.** The model becomes one more reader in the
      analyzer: it yields an observation with a frame, a value and a
      confidence, behind a flag; the accounting stays the arbiter and a model
      value never fills a field on its own. Done: a fresh run of a held-out
      recording with the flag on shows fewer unexplained and conflicted
      fields than without, and no new false values. `--learned-reader` (the
      service's `-learned-reader`) reads every training result frame, and a
      read gain equal to a turn's difference, or the value the stat lands on
      with it, makes that amount observed. A fresh website analysis of
      B/Gran Concert with the model that never saw recorder B: 2 unexplained
      turn fields with the reads and 3 without, both left being performance
      points, which the model does not read; 19 amounts observed by the
      model, all right by eye. A fresh website analysis of recorder C's
      career: 2 against 3, with 7 observed, all right by eye; its two left
      are one misread performance opening (section 2). One false read was seen:
      the recorder's mouse pointer over a `+8` read as `+9` at 0.998, which
      the value 138 on the same card contradicts, so nothing was confirmed
      from it.
- [x] **Ship the model trained on every recording.** Both held-out careers
      checked out on the website, and the local service now runs the model
      trained on every run. A website analysis of recorder C's career records
      its fingerprint, and its accounting is identical to the held-out
      model's run of the same recording.
- [x] **Flag a worked-out amount the card contradicts.** A difference worked
      out for a training whose card the model read as another gain is
      recorded on the training with the reads and their frames, raised as an
      accounting issue and listed in the review queue. The amount itself does
      not change: the stat bars decide it, and a read cut to its leading
      digits disagrees with nothing. Rebuilding the accounting of six saved
      reports raises two findings, both in one career, a card reading `+18`
      skill points where 13 was worked out and one reading `+9` where 8 was;
      the `+18` is what the card shows, checked against the recording.
- [ ] **Pointer-covered digits in the reader's training data.** Recordings
      show the mouse pointer over the result card, and one covered `8` read
      as `9` with high confidence. Done: boxes with the pointer over a digit
      are in the dataset, and the held-out false reads do not rise.
- [ ] **Second model: text repair.** Learn the recognizer's character
      confusions from receipt lines paired with their resolved names, and
      replace the hand-set edit distances with one repair that uses the
      learned confusions and a shared word list. Done: the per-rule
      thresholds are gone and the held-out receipt names resolve at least as
      often.
- [ ] **Later:** boundary and animation-state detection, using the current
      rules' decisions as labels.

- [ ] **Faster analyses.** A full career took 48 minutes on this machine;
      with the same 3 workers and 2 dense workers it now takes 32, and with 5
      and 4 it takes 24, every report identical (docs/ocr-performance.md).
      In place: the OCR engine gets compact images, a pane's PNG is written
      while the pane is read, repeated pixel checks of one file are
      remembered, and OCR workers collect garbage every 25 frames, which
      holds each near 2 GB where they reached 6 to 9 GB. Left: the service
      still runs 3 and 2 workers, chosen so the machine stays usable during an
      analysis; the training result rereads read the whole screen on about
      2,300 frames where the card's fixed boxes might do. Done: a full career
      analyzes in about 18 minutes, half of 35, with an identical report.

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

- [ ] **One image for the local application.** The image is built
      ([docs/container.md](docs/container.md)): the client, the Go binary,
      the analyzer with its `vision` extra, ffmpeg and the OCR models, on a
      configurable port with the data directory on a volume, with the CUDA
      wheel behind a build argument. `docker compose up` serves the front
      page, accepts an upload, and analyzed a 45-second clip to a report on
      the CPU provider. Left: a full career analyzed in the image, with the
      time it takes recorded in the OCR performance note.
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
