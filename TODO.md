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
(four recorders, six careers). Across them 33 turns ask for a review; the
first four items below account for 31 of those. Ordered by how much
reviewer work each removes.

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
- [ ] **The digits at the end of an award receipt are not read.** "Skill
      Pts went up by", "Stamina went up by", "Power cap went up by": the
      line is recognized and its number is missing, so the award becomes an
      unparsed receipt and the field an unexplained change. This is the
      first target of the learned readers below. Done: award receipts with
      a visible number are counted; the unparsed count per career drops
      below ten.
- [ ] **Stat badges partly unread on some training turns.** One or two of
      the five badges on a training result are missed or misread, leaving
      a small stat gap on a turn whose training was committed. Done: the
      dataset builder below reports these as hard cases, and the badge
      reader closes them.
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
- [ ] **Ship the model trained on every recording.** Both held-out careers
      checked out on the website, and the local service now runs the model
      trained on every run. Done: a new report records that model's
      fingerprint.
- [ ] **Flag a worked-out amount the card contradicts.** The accounting
      worked out a gain of 7 on a turn the counted-twice receipt made look
      balanced, while the model read `+12` on five frames of that card and
      the value 876 over 864. Done: a worked-out amount that a confident read
      on its own card contradicts is listed for review.
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
