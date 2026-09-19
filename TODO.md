# To do

What comes next, in the order it should happen. Each item says what "done"
looks like. The design behind the learned readers is in
[docs/evaluation.md](docs/evaluation.md); the longer horizon is in
[docs/roadmap.md](docs/roadmap.md).

How an item gets checked decides how long it takes, so an item that has been
measured says which it needs; one that does not say has not been costed yet.
A change to the accounting is checked in seconds by running that module
over a saved report; a change to what is read off the screen can only be
checked by a fresh analysis, about 35 minutes, because the readings are
cached against the reader's fingerprint. Run one with
[docs/analysis-job.md](docs/analysis-job.md), from a snapshot of the analyzer
rather than the working tree: its "output directory after a run" section says
why, since editing the tree mid-run changes the reader fingerprint the caches
carry and fails the run partway.

Where an item quotes a number, it names the report it was measured on. The
current reference report for the Grass Wonder career is
`.local/verify-panel5/run/report.json` (`verify-panel4` is the run before
the panel rereads reached result screens); the recording is under
`~/Downloads/recordings/`.

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

On 2026-09-18 the four careers whose latest reports predated this week's
fixes were analyzed again on the same tree (27 to 36 minutes each on the
GPU), so every career now has a fresh report. Against their September 15
reports, with the settled-badge rule applied: Hishi Amazon 10 unexplained
fields to 0, 11 unreadable receipts to 0, missing actions 5 to 2 (one of
them a window that expects none); Should have been better 18 unreadable
receipts to 2 and the same 5 unexplained fields, all on late turns after the
player stopped opening the stats screen; own 01-59 2 unexplained to 0 and 26
unreadable to 0; own 12-21 64 unreadable to 0 and its 1 unexplained and 1
unresolved field unchanged. Turns a viewer is pointed at fell from 24, 29,
29 and 24 to 15, 18, 13 and 14, and on every career most of what is left is
"race reward on a separate receipt", 12 to 17 turns each, a flag the
recordings' style raises on nearly every race; whether it deserves to be
serious is the next thing to look at.

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
- [x] **A receipt whose wording the recognizer damaged is not counted.** The
      count this item asked about was already met: the latest report of each
      recording listed one or two receipt lines for review, not ten, and every
      numberless line traced back to an award counted from a later frame
      ("Speed went up by" at 481 s of one career, cut off by a loading screen,
      counted at 493 s as `+12`). What was left was damage in the wording. A
      hint receipt at 586 s of another career was read on seven frames and
      cleanly on none, its four levels for Pace Chaser Corners missing from the
      accounting, with a cursor resting on `level(s) for` and the panel's
      sparkles drifting across it while the number and the skill came through
      every time.
      The recognizer reports where each word it read sits, which costs nothing
      and changes no text or score, so the reader keeps those boxes for the
      receipt band. An overlay that touches neither the number nor any word of
      the name can only have damaged the fixed wording between them: that line
      keeps its confidence (`overlay_covers_fixed_hint_wording`) and only then
      may `level(s)` and `for` each be repaired by one glyph, which also lets a
      name that wrapped onto the next line join. A line that reads cleanly is
      not unblocked, and a repaired reading corroborates an award the event
      already holds under a name one glyph away rather than adding a second.
      Two fresh analyses: the award is counted with
      `text_normalization: obstructed_hint_wording`, the career's review list
      goes from one item to none, its turn accounting is unchanged, and the
      duplicate spellings an earlier, broader rule produced are gone. Without
      that geometry a damaged line is blocked as before and still never
      asserts a receipt by itself, which the boundary tests pin.
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
- [x] **A result card only ever called a candidate is never read.** A
      training result frame whose banner stays illegible is classified
      `training_result_candidate`, and both the learned reader and the
      accounting's search for a training's own frames require
      `training_result`, so such a card contributes nothing and every one of
      its gains is worked out from the turn difference. Two trainings across
      two careers lose all their fields this way, seven in total. In one, the
      card is on two sampled frames, at 162.0 s and 162.25 s, both
      candidates, and the dense reread of that window produced no result
      frame either. Both screens now count, and what a read may do is
      unchanged. A fresh analysis of that career reads that training's four
      gains from its own card, speed 2, power 2, guts 10 and skill points 5,
      where every one of them used to be worked out; 24 candidate frames carry
      reads, and four amounts moved from derived to observed.
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
- [x] **A "N more" badge is read as a performance value.** A concert bonus
      draws the badge over the row it talks about, and the detector splits it
      into its number and the word, so the number sat inside that row's band
      and became the row's points. A number the word sits beside is now the
      badge's, and the row whose value the badge covers is reread from under
      it. A fresh analysis of that career: its two unexplained turn fields are
      gone, the boundary reads Vocal 4 and no longer 8, and the run has six
      fewer missing endpoints than before, because rows the badges hid now
      read. On the saved readings of six recordings only that career's 22
      readings change, every one of them dropping the same wrong Vocal 8.
- [x] **A turn spent on anything but training or a race has no action.** The
      six actionless turns of the Grass Wonder career were not what this item
      first said: all six are choices the player made, and none was spent by
      the game. Five are Recreation with the friend card Light Hello, the five
      titled episodes of its outing chain ("Repose in the Lunar Mare" through
      "At Rainbow Cove"), and one is a Rest ("Well-Rested!", energy recovered
      by 61) after three lessons; the inheritance events and the concert sit
      on the same turns as an outing and spend nothing. The analyzer already
      had `outing` and `rest` actions, each needing its confirmation prompt on
      a sampled frame, and this recording has none: the prompt is one click,
      and at four frames a second the player dismissed it between two samples
      every time. Checked against the recording: the Recreation menu at
      596.75 s goes straight to the outing's scene at 597.75 s, and the hub at
      927.75 s to the "Well-Rested!" scene at 928.0 s.
      The request evidence is now what ordinary play does leave on screen. The
      Recreation menu waits for the player, so it stands in as an outing's
      request when no confirmation was sampled; the hub left straight into
      the Rest's own scene stands in for the Rest prompt when its last frame
      is within a second of that scene, nothing else was sampled between, and
      the receipt could be nobody else's (no companion line, no treated
      condition, not the infirmary's scene). Each receipt says so, the ledger
      carries it as `identity_basis` (`outing_menu_and_receipt`,
      `hub_exit_and_receipt`), and the client says the choice itself was not
      seen, as for `result_card_only`. The second career checked, B/Gran
      Concert, has one such turn of a third kind: the summer camp merges Rest
      and Recreation into one "Rest & Recreation" prompt the classifier does
      not label. The Rest rule now knows that prompt by its wording and names
      the Rest after it, but that turn stays `missing_action` because its
      receipt was read on one frame, and a Rest receipt read once proves
      nothing, as before. On the saved readings of twelve reports, nine
      careers, only the Grass Wonder report changes: five outings and two
      rests (the sixth turn and a pre-debut rest at 200.5 s, checked against
      the recording), its six `missing_action` turns all read `one_action`,
      and its accounting summary is unchanged; the other eleven gain no
      receipt. No "spent by the game" shape was needed and none was added;
      `missing_action` still means a turn whose action was never seen.
- [x] **A result screen's panel rows are never reread.** The panel's bounded
      rereads (the component split of a merged line, the fixed-row and glyph
      crops of a doubted row, the slot crop of a read row, the cap crops)
      were requested only beside the career stat grid, and a training
      result's frames have no grid while its cards animate; previews still
      have it and already got them. They are now requested wherever the
      panel's `Performance` heading is at its place, which is the
      identification the result parser already relied on; each builder still
      gates on the panel's own row geometry, so a frame without the rows asks
      for nothing but its absent caps. Checked by a fresh analysis of the
      Grass Wonder recording from a snapshot (`.local/verify-panel5`, now the
      reference report): the result frame that read Vocal 8 where the panel
      says 18 reads 18 from its slot crop; 20 result-card rows that had no
      value now read one (751 panel values on those 179 readings, from 727);
      two turn endpoints stop being missing (68 to 66) and nothing becomes
      unexplained (4 before and after); a preview frame whose line read
      Composure 3 while the slot crop read 31 is now unknown rather than 3,
      because a line that is the head of the wider reading, not its tail,
      proves nothing. The single-digit values left on result frames are the
      current half of a merged `value+award` line (`1+15`, `0+30`, `5+20`)
      and are right. The run took 31.4 minutes against 33.5 for the previous
      run of the same recording, so the extra crops cost nothing visible; the
      six actionless turns of the item above read `one_action` end to end in
      this run.
- [x] **A hint award is counted once per spelling of its skill.** An event's
      effects are keyed by kind, field and name, so a skill spelled two ways
      becomes two awards, and the hint totals a report prints are inflated by
      exactly that. No balance notices, because hint levels are in neither the
      stat nor the performance accounting.
      Where the second spelling comes from is now clear. One event of one
      career holds `Spring Runner ○` from eight base-pass frames and, beside
      it, `Sprir Runner` and `Sp.unner`, each asserted by a single frame of the
      occluded-receipt recovery; the same event pairs `Medium Corners ○` with a
      one-frame `Med ym Corners O` and `Risky Business` with a one-frame
      `usiness`. The receipt log scrolls while an event runs, so the same
      receipt is read at a different height on every frame and its garbled
      re-reads carry no shared slot to match on. Name distance decides nothing
      either: `Sp.unner` is far from `Spring Runner ○` while `Medium
      Corners ○` is close to `Medium Straightaways ○`.
      What separates them is corroboration. Every real award here is read on
      one to eight frames and its name recurs through the run; every duplicate
      is one recovered frame whose spelling appears nowhere else.
      A spelling the run produced once, on a recovery frame and nowhere else,
      now joins an award the event already holds at the same amount under a
      name read more often, keeping the frame as that award's evidence.
      Distance is asked only which of the awards held at that amount the
      garble belongs to, never whether it is one, and it answers only when one
      is strictly nearest: the event carrying `Med ym Corners O` holds two
      awards at one level and two at five, and each garble went to the right
      one. Across every saved report, nine careers, only five awards change:
      the four named above and `Cluner Adept` beside `Corner Adept` in a
      second career. `Come What May`, which only the recovery ever read, keeps
      its own award because nothing in its event stands at that amount, and
      `Cprner Adept`, a garble the ordinary pass read twice, is left alone.
- [x] **A race-day opening is sometimes never observed.** The policy question
      this item left, whether a single stat-bar reading before the action may
      open a turn, turned out to be answered in the code already, and the
      answer stands: one reading opens a turn only when the producer accepted
      that frame as a state observation and the frame carries the turn's own
      identity (`basis: source_bound_single_frame_before_action`, in
      `boundary_state_recovery`), or when a 60 fps probe around it repeats
      it; a reading with no identity opens nothing. What was missing was the
      identity of the finale race windows: the three finale races all count
      down from 1 and are told apart by the race advance that opened each
      window, so those windows carried no calendar identity and the recovery
      stage skipped them, probe and single-frame path alike. A finale race
      window is now identified by its phase label, and a frame showing
      `Finale Underway` inside its bounds is its own
      (`ownership_basis: same_phase_inside_finale_race_window_before_action`).
      Measured first across eleven reports of nine careers: fifteen turns
      lack a stats opening, and only two of them have any complete reading
      before the action, the Grass Wonder finale (a training-preview frame
      the producer had already accepted, with its six values read twice on
      the frame at 99.9) and one recorder A finale turn whose frame shows no
      phase label; the other thirteen are race-day hubs with no stat bar and
      first pre-debut turns, where the report is right to say the opening was
      not observed. Rebuilt from the saved readings: the Grass Wonder
      reference reads 74 of 74 stats openings, the finale's predecessor gains
      its closing, unexplained fields go from 4 to 2 and missing endpoints
      from 66 to 54; the other ten reports gain no projection and their
      accounting is untouched by this change.
- [x] **Ambiguous effects.** Measured on seven careers' latest reports: 16
      candidates, of four reasons. Two now have a rule, decided by the same
      corroboration that settles a garbled hint spelling. A recipient slot
      read as two spellings on adjacent frames (`Agnes Tachyon` beside
      `Ágnes Tachyon`, `Etsuko Otonashi` beside `Etsuk Otonashi`) takes the
      spelling the run produced on other receipts when the other spelling was
      read on this receipt's frames and nowhere else; two spellings that both
      recur, or none, stay candidates. A bare skill spelling beside its
      circle-proven twin at the same amount (`Front Runner Savvy` beside
      `Front Runner Savvy ○`, read on eight and two frames) is that award with
      its glyph unread when the run read the bare spelling nowhere else; the
      one bare spelling the run also read on its own receipt, `Corner
      Recovery` in the Gran Concert career, stays a candidate. Re-assembling
      events from the saved readings, eight candidates fold and nothing else
      changes but the two restored friendship lines and the evidence the
      folded frames add. The eight left are presented, as before, with their
      reason: the three alternate spellings of an accepted spark, that one
      bare hint, and the four Grass Wonder spark slots. Those four are one
      shape: on each inheritance the log shows `Stamina spark activated!`
      and, four lines later, `Speed spark activated!` at the same height,
      with a second `Inspired by Oguri Cap!` line between them that the slot
      tracker takes for the first, so it holds both names as one slot. Both
      sparks are real; settling them needs the tracker to align whole frames
      (the earlier frame's last lines are the later frame's first lines,
      shifted by one amount), which is a change to that module for another
      day, not a reason to guess.
- [x] **Entries before the first turn.** The check screen lists them under
      "Before the Career Starts" and says they belong to the run, not to a
      turn.
- [x] A report keeps the ledger of the analyzer that made it. The analyzer
      names itself (`--worker-version`: its package version and a digest over
      its source), which it can now be asked without a source, an output
      directory or a model, in about a second. That identity was already
      recorded on the job a report came from, so the report page reads it
      there and no column was added; the service asks the installed analyzer
      once, on first use, so serving never waits on an interpreter. A report
      read by an older analyzer says so and offers "Analyze Again", which
      matches its recording by hash and queues it in one click. Each half is
      shown only when it is known and staleness only when both are: an
      imported report never had a job to record one, an unreachable analyzer
      leaves the comparison unmade, and a recording that is gone says so
      instead of offering a button that cannot work.
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

- [x] **A training card read as several gains stays flagged after the stat
      bars settled it.** The biggest reviewer load left on the fresh reports:
      3 to 6 "conflicting readings" per career, nearly all training cards
      whose badge was read as two or three numbers over its 60 fps frames (a
      digit cut by the sparkle, a glyph misread under the glow, beside the
      number the badge settles on). The accounting already settled every such
      field from the stat bars, confirmed on the card by the learned reader
      or standing alone, and the settled amount was one of the card's own
      reads; only the flag never cleared. Now the accounting records
      `settled_conflicting_readings` on the training when the settled amount
      is one of the reads, the timeline document drops the entry's flag when
      every disputed field is settled and nothing else raised it, and the
      other reads are shown beside the amount as `disagreeing_reads` with a
      note instead of a warning. Rebuilt from the seven careers' saved
      reports on 2026-09-18: flagged training cards 26 to 6, unexplained
      fields unchanged at 0 on each. The six left are right to stay: two
      where the learned reader read a different number than the bars settle
      on (Gran Concert Junior Late Feb wit, Senior Late Jul skill points),
      three on final turns with no observed closing state, one flagged for a
      performance-row disagreement. Done.

- [x] **Every race is flagged "reward link: separate receipt".** After the
      settled-badge rule this was most of what a viewer was pointed at, 11 to
      17 turns per career on every recording. It was a constant, not a
      finding: a race receipt never names an outcome event, its rewards live
      on the race's own result record, and the ledger called anything
      without an event id a separate receipt. A race receipt whose race id
      names a race record is now `linked_race`, and the client warns only
      for a receipt tied to neither. On the seven careers' saved reports
      every one of the 90 race actions is linked. Done 2026-09-18.
- [x] **One dialogue box read twice across the award's own animation.** The
      learned reader's disagreement on Gran Concert's Senior Late Sep card
      (it read +18 skill points, the bars left 13) led to the real fault: the
      "+5 Skill Pts" animation covered the outcome box for one sampled frame,
      the box was read again with a friendship line appended, and the same
      +5 was counted twice, so the stat bars balanced with 13 wrongly given
      to the training. The receipt continuity rule now bridges exactly one
      blank frame (nothing at all in the receipt band, the line back on the
      same pixels after it), and a line the parser turned into an effect no
      longer counts as narrative at the chain's end. Over the seven careers'
      saved readings this fires once, on that box, and nowhere else. A rule
      letting the text reader and the bars outvote the learned reader was
      tried the same day and taken out: it settled this very card at the
      wrong 13, because a stray text read equal to a corrupted difference is
      exactly how a wrong number slips through, and the model's disagreement
      was the alarm that found the double count. Done 2026-09-18, checked by
      a fresh Gran Concert analysis the same day: the +5 counted once, the
      card's +18 read straight off the card with nothing left to work out,
      no contradiction on either card, and the turns a viewer is pointed at
      down from 17 to 6 (one unreadable receipt, one ambiguous effect, three
      cut skill lists, one window with no action seen).

- [x] **Every skill purchase is flagged "purchased skill list incomplete".**
      Another constant: the batch builder set `purchased_list_complete` to
      false for every batch, because the confirmation list scrolls and the
      names read off it never prove the whole purchase. The cart does. A
      batch is now complete when every change of the cart counter was
      assigned to a bundle with one named target and those bundles sum to
      the charge the receipt read or the chain between observed balances
      worked out; `purchased_skill_names` then lists every purchased skill,
      and the completeness is worked out again after the chain fills in a
      charge. A partial list with a read charge is a note in the browser
      (the totals are right, some names were not seen); only a partial list
      with no charge read stays a warning. Over the seven careers' saved
      readings: 15 batches, 6 complete, 5 notes, 4 warnings, all four on
      recorder A's careers where the charge itself was never on screen.
      Done 2026-09-18.

- [x] **A hand-checked career.** Gran Concert B's fresh report (74 turns)
      checked by eye against frames cut from the recording on 2026-09-18:
      73 opening stat rows, 62 training cards, 281 outcome boxes, 12 races.
      Numbers: nothing wrong found. Every readable opening matched all six
      values (three frames were transitions), every card's badges or settled
      after-values matched the entry, every visible receipt number was
      parsed to that number, all 12 races right name and placing. Names: 22
      wrong, every one a name read a glyph or two off and kept as its own
      entry beside the name the run read correctly many times (15 supporter
      names on friendship lines, one of them counted three times on one box;
      7 skill hint names, one of them a hint counted twice). Record kept
      locally in `.local/hand-check-v1/`. Done; it opens the item below.
- [ ] **A name read a glyph off is kept as a separate entry.** The hand
      check's only finding. Supporter names on friendship lines recur 11 to
      31 times per run, so the run's own sightings can settle "Daivg
      Scarlet" and "Direct r Akikawa"; skill hint names are mostly seen once
      per run (36 of 39 in that career), so "Cprner Adept" and "Blucarole of
      Blessings" need the game's own skill list. This is the shared,
      vocabulary-based OCR repair the roadmap's section 5 already names, in
      place of the per-rule edit-distance thresholds. Done: the 22 names in
      the hand-check record fold into their real names, with no name that
      was right folded into another, and the count of distinct friendship
      and hint names per career drops to the real ones.

- [ ] **The hub's stat row is not read on two of three recordings.** Found
      on 2026-09-18 when a viewer pointed at 31:20 of Should-have-been-better,
      where the hub shows the six stats and the report has no opening for
      that turn. The reader gates the stat parse on a colour probe of the
      label strip, which expects blue; on recorders A and C the strip is
      pink, so the probe fails on every hub frame, and the geometry fallback
      then rejects most frames for a missing "Skill Pts" label read as "Sill
      Pts", a missing header, or the wit cap merged with the grade glyph
      ("UG/1301"), because it wants every cap although its own docstring says
      four. Counts: Should 894 of 1,369 hub frames with the row unread, Mayano
      802 of 1,148, Gran Concert 0 of 1,257. The openings on those two careers
      therefore come later in the turn from other screens (lesson
      confirmations, result cards) or, on four turns, not at all. Done: the
      probe accepts the strip in either colour or is replaced by the geometry
      proof, the fallback takes four caps and a near "Skill Pts", and on the
      three fresh careers every hub frame with six readable values yields
      them; then the turn openings on A and C are observed at the turn's
      start like B's.

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
- [x] **Pointer-covered digits in the reader's training data.** The pointer
      is the game's own lime arrow, and it rests on the card far more often
      than one case suggested: the dataset builder now flags every result box
      it lies over (lime pixels inside the box proper, past the margin where a
      green stat icon sits), 6,163 of the 68,838 boxes of dataset-v5, with
      546 labeled badges and 546 labeled gains in the training runs and 45
      labeled boxes held out, among them the very `+8` that read as `+9`.
      Flagged boxes keep their own training bucket, so the per-card cap never
      trades them for clean frames, and the held-out judgement reports them
      as a scope of their own. Judged against a control trained the same day
      with the old grouping, since the shipped model's own settings no
      longer reproduce its lower counts: all-frames false reads are level
      (105 and 322 against the control's 100 and 324, within the run-to-run
      band the second seed confirms), pass-frame false values fall from 20 to
      16, and on the 233 held-out pointer boxes false values and gains fall
      from 14 and 3 to 12 and 2, with no labeled pointer frame read wrongly
      where the control reads a covered `625/` as `65/` at full confidence.
      The four runs are tabled in `.local/learned-readers/pointer-v9.md`;
      `reader-final-v2`, the same setting trained on every run, is what the
      service now loads.
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
      analysis. The stages of a fresh Mayano run of 1,841 s: base readings
      635, dense result-card rereads 411, automatic refinement 163, assembly
      149, occluded receipt recovery 114, reload 84. The rereads (3,232
      frames in 85 windows) already read the card through 25 fixed crops,
      but `read_training` then runs the general detector over the whole pane
      on every frame to build the preview panel and keep same-frame header
      anchors: measured on 60 frames of one window, 175 ms per frame, of
      which 90 ms is that full read (34 ms detection, 52 ms recognizing every
      line it found, dialogue included), 38 ms the fixed crops, 17 ms badge
      localization and 11 ms gain refinement. Detecting only the bands its
      consumers use would take about three minutes off that stage at the
      cost of the dense readings' raw lines no longer being the whole pane's;
      decided against on 2026-09-18, three minutes is not worth it. Anything
      that gets to the goal has to come from the base pass. Done: a full
      career analyzes in about 18 minutes, half of 35, with an identical
      report.

## 4. Code and repository hygiene

- [x] Tests that depend on locally preserved evidence now live under
      `analyzer/lab/tests` and run only where the evidence is; the 57 whose
      evidence had been pruned for good were deleted rather than left to
      skip. The main suite runs every one of its tests on a clean checkout
      and skips none, checked by running it with `TRACEN_LOCAL_EVIDENCE`
      pointed at an empty directory.
- [x] The `analyzer/lab` tools are evaluation history; the directory's
      README says so.
- [ ] The analyzer package keeps its name. Revisit only if it is ever
      published on its own.

## 5. Containers

Running the service anywhere but this machine starts here, before any
hosting choice.

- [x] **One image for the local application.** The image is built
      ([docs/container.md](docs/container.md)): the client, the Go binary,
      the analyzer with its `vision` extra, ffmpeg and the OCR models, on a
      configurable port with the data directory on a volume, with the CUDA
      wheel behind a build argument. `docker compose up` serves the front
      page, accepts an upload, and analyzed a 45-second clip to a report on
      the CPU provider. A full career (Mayano CM prep) analyzed in the image
      with its default two and one workers in 2 h 10 min against 31 min on
      the GPU, with the stage split in [docs/ocr-performance.md](docs/ocr-performance.md);
      the report is close to the GPU run's but not identical, since the CPU
      recognizer reads a few frames differently. Done.
- [x] **A worker image.** The Dockerfile's `worker` stage is the analyzer
      alone (1.51 GB, built in about two minutes on top of the cached
      layers), with the analyzer's own command line as its entrypoint, and
      the `app` stage is built on top of it. `internal/runner.Container`
      starts one container per job when the service is given
      `-worker-image`: the recording, the run directory and the learned
      reader are mounted under `/job`, the terminal object's paths are
      mapped back to the host, cancellation removes the container by name,
      and containers carry a label so a restarted service ends the ones a
      dead service left. Checked on 2026-09-18 against real docker from the
      website's API: a two-minute clip uploaded, submitted and analyzed to a
      report in 7.5 minutes on the CPU provider (481 base frames), the
      report's paths and identity right in the browser's summary; a cancel
      removed the running container in under two seconds; killing the
      service mid-job left the container running and the restart removed
      it while marking the job interrupted ([docs/container.md](docs/container.md)).
      Done.
- [x] **Image hygiene.** The three base images are pinned by digest, the
      Python packages by `docker/constraints.txt`, and the downloaded model
      files are checked against `docker/models.sha256` (a changed download
      fails the build). The build context is 40 kB. The image's health
      check asks `/readyz`, which answers 503 when a check fails, so a
      broken image is unhealthy rather than merely up; `/healthz` stays
      the liveness answer. The `image` CI job builds both targets, asks the
      worker for its version, starts the application and waits for
      `/readyz`, `/healthz` and docker's own healthy status, and writes the
      sizes into the run summary. Sizes on 2026-09-18: worker 1.51 GB
      unpacked (424 MB compressed), application 1.53 GB (430 MB); ffmpeg's
      Debian dependencies are 466 MB of that and the Python packages 435 MB
      ([docs/container.md](docs/container.md)). The job's steps were run by
      hand on this machine against the same commands; the workflow itself
      runs on the next push. Done.

## 6. Hosting

- [ ] The four changes in [docs/deployment.md](docs/deployment.md): accounts
      beyond one machine, an object store for uploads and job outputs, a
      queue that outlives one process, and GPU workers that are addressed
      rather than spawned. No provider is chosen yet.
