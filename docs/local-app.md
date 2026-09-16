# Running the local application

The local application is one binary, `tracen`, that serves the browser client
and the HTTP API, keeps a SQLite database of jobs and reports, and runs the
Python analyzer as a child process for each submitted recording. Nothing
leaves the machine; the server listens on the loopback interface only.

## Prerequisites

- Go 1.25 (`go.mod` pins the toolchain; `GOTOOLCHAIN=local` avoids a download).
- Node 22 with npm (builds the browser client once).
- Python 3.12 or newer in a virtual environment with the analyzer installed:
  `python -m pip install -e "./analyzer[vision]"` (RapidOCR, onnxruntime with DirectML on
  Windows, Pillow, psutil). The CPU provider is used when no supported GPU exists
  (`docs/analysis-job.md`, `TRACEN_REPLAY_OCR_DEVICE`).
- ffmpeg on `PATH` (or named with `-ffmpeg`), used for frame extraction.
- The OCR models in `.local/models/rapidocr` (or the folder named with
  `-model-dir`); readiness checks for `PP-OCRv6_det_small.onnx` there.

## Build

```powershell
cd web
npm ci
npm run build          # type-checks and writes internal/webassets/dist
cd ..
go build -o tracen.exe ./cmd/tracen
```

`npm run build` must run before `go build`; the client is embedded into the
binary. Without it the binary still serves the API and shows a page that says
the client has not been built.

## Start

One command, run from the repository root so the analyzer package and the
default paths resolve:

```powershell
.\tracen.exe
```

Then open http://127.0.0.1:8765/ and create an account (see "Accounts and
uploads"). Recordings reach the service only through the browser upload; the
browser refers to them by a server-issued identifier, never by a path, so the
page does not depend on where or how the service stores them.

Flags and their defaults (all relative paths resolve against the working
directory, so no user path is hardcoded):

| Flag | Default | Meaning |
| --- | --- | --- |
| `-addr` | `127.0.0.1:8765` | listen address; keep it on loopback |
| `-data` | `.local/tracen-data` | database, uploads (`recordings/<user>/`), job outputs (`jobs/<id>/`), frame cache |
| `-python` | `.venv/Scripts/python.exe` (`.venv/bin/python` elsewhere) | interpreter with the analyzer installed |
| `-workdir` | `analyzer` | directory containing the `tracen_replay` package |
| `-model-dir` | `.local/models/rapidocr` | OCR models |
| `-ffmpeg` | `ffmpeg` | ffmpeg executable |
| `-workers` | `4` | OCR worker processes per job |
| `-dense-workers` | `0` (= workers minus one) | processes for the dense re-read passes; each can peak near 5-6 GB |
| `-queue` | `4` | maximum queued jobs; one job runs at a time |
| `-ocr-device` | `auto` | `auto` (DirectML, then CUDA, then CPU), `cpu`, `dml`, `cuda` |
| `-keep-working-data` | off | keep the analyzer's OCR caches, crops and recovery inputs in the job directory (about 1 GB per analysis); off keeps only the report, timeline, viewer page and log |

Example with everything explicit, as a configuration you can keep in a script:

```powershell
.\tracen.exe -addr 127.0.0.1:8765 -data .local\tracen-data `
  -python .venv\Scripts\python.exe -workdir analyzer -model-dir .local\models\rapidocr `
  -workers 4 -dense-workers 2 -queue 2 -ocr-device auto
```

`/readyz` lists the checks the server makes (python, ffmpeg, model-dir,
analyzer, ocr-device); the client shows the result as the pill in
the header. A failed check names the path it looked at. The checks are
advisory: a job submitted while one fails is accepted and then ends as
`failed` with the worker's own message (for example `invalid_paths` when the
model directory is missing), so fix the check before queueing work.

Stop the server with Ctrl+C: admission stops, the running worker's whole
process tree is ended and recorded as interrupted, and the server exits within
20 seconds. On the next start the interrupted job is listed as such; it is never
replayed automatically. If the server dies without shutting down (a crash, a
forced stop), the worker still ends within seconds: on Windows it lives in a
job object that the operating system closes with the server, and on every
platform the worker watches the server's process id (`--owner-pid`) and stops
itself, with its OCR processes, once that process is gone.

## Accounts and uploads

The front page leads to the sign-in screen (`#/signin`, or `#/signup` to
create an account with a display name, an email address and a password of at
least eight characters); any page that needs an account shows the sign-in
screen while nobody is signed in. The top bar has Home, Runs, Guide (how to
record, what the report shows, how to review, and what every flag means) and
About (what the application does, its parts, its scope and where the data
lives). The runs dashboard's "turns waiting for your review" tile opens
`#/check`, every turn across the account's reports that asks for a review,
each linking to its review screen. Accounts
are local to this machine: the password is stored as a PBKDF2-HMAC-SHA256 hash
(600,000 iterations, per-user salt) in the same SQLite database, the session is
an HttpOnly cookie (`tracen_session`, 30 days), and sign-in attempts are rate
limited per address. There is no password reset; delete the row in the
`users` table to start over.

## Reviewing and editing a report

The analyzer does the bulk of the work; the viewer checks what it flags and
can edit anything. Each turn's pane says what the turn asks for ("2 stat gaps
· 1 flagged event") with a "Review this turn" button; the Check tab has the
same button per turn. It opens the review screen
(`#/reports/{id}/{turn}/review`): the recording and the turn's stats stay on
the left, and the guided editor takes the right column, with a four-step
strip (explain the gaps, check the flagged events, add what the report
missed, save) and a live verdict ("Adds up", "2 gaps still open", "Off by
Power +3") recomputed as the viewer types with the same arithmetic the server
applies on save. Every gap and every flag carries a sentence on what it means
and what to do about it. The editor's parts:

- **Gaps to explain.** One card per stat whose values at this turn and the
  next do not add up: the amount, the values before and after, what the
  report already explains, and a chip that seeks the recording to the window
  between the two observations. Each gap offers three ways to explain it:
  "Belongs to an event" adds the amount to one of the turn's events (chosen
  from a list), "Missed event" creates a new event below with the amount
  filled in, and "Enter amount" takes a number and a note. When the report has
  already worked the difference out onto its only possible owner (the turn's
  only training whose result was not read or whose badge was read clipped,
  the one receipt that named the stat but lost its number, the one lesson
  whose cost was not observed, or the one race of a race turn for its skill
  points), the card says so and offers "Looks right" beside the three ways;
  a viewer's own amount replaces the worked-out one. A card turns green once
  the live check covers it.
- **What did you play this turn?** On every turn, including the pre-debut
  ones: the report's own reading is shown and preselected when it saw one,
  and the viewer can pick training (with its option and gains), rest, outing
  or race instead, or fill it in when the report saw nothing.
- **Events in this turn.** Flagged events by default, every event with an
  amount on request. Each amount is a chip with a stepper; a stat can be
  added or removed; "Reviewed" and "Not real" are toggles; a note folds out.
- **Events the report missed**, each with a kind, a title, a time (one click
  takes the recording's current time) and its changes.
- **Save and check** at the bottom stores the edits and shows the server's
  verdict per field.

Edits are stored per viewer beside the report and never written into it.
On every read the service checks them against the turn's observed
endpoints: the value at the start of the turn plus what the report explains,
with the viewer's edits applied, plus what the viewer added must equal the
value observed at the start of the next turn. The pane shows "adds up",
"does not add up" with the difference per stat, or "not checkable" when the
next value was not observed; differences the viewer left alone are listed as
still open. The Check tab is the review queue: every flagged entry and every
difference, per turn, each a link.

Routes: `GET/PUT/DELETE /api/reports/{id}/turns/{turn}/correction` and
`GET /api/reports/{id}/corrections`; the turn detail carries `correction`
and `verification` when the viewer filled the turn in.

The front page (`#/`) is a short introduction: frames from the owner's own
recording, each a different screen (a training turn's home screen, a support
event with its stat popup and the same log line, a race result, the Grand
Concert lesson menu) alternate with captures of the report (the replay row and
the analytics, light and dark) under `web/public/shots/`; the sign-in card
is at the bottom. "Runs" (`#/runs`) is the signed-in home, one row per
recording:

- **Upload.** Drop a recording (mp4, mov, webm or mkv) on the page or choose a
  file; the upload streams to `<data>/recordings/<user>/` with a progress bar
  and can be cancelled. The stored file is hashed on arrival, and its size,
  name and hash are shown on the recording card. Uploads and the reports made
  from them are visible only to the account that made them.
- **Analyze.** Queues one analysis of the recording. The job page shows the
  analysis as five phases named after the worker's stages (Capture & OCR,
  Base Readings & Refinement, Recovery Rereads, Assembly & Accounting, Report
  & Timeline), each a segment of one bar sized by its usual share of the
  time: the first phase moves with the frame count, the others fill as the
  worker finishes their stages, the caption names the last stage finished,
  and a rough time left is shown once the analysis is far enough in. It can be
  cancelled, and the page links back to Runs.
- **Dashboard.** Above the list, once a report exists: careers analyzed and
  their turns, minutes of recording read, the share of stat changes the
  reports fully explain, the number of turns waiting for a review, and the
  best run (the highest five-stat total at the end of a career) with its
  stat bar. Each finished run's row shows its final stats with rank letters
  and how many of its turns ask for a review.
- **Runs.** A row is a recording with the latest report made from it;
  reports from earlier analyses of the same recording are listed under the
  row, not as rows of their own. "Analyze again" queues a new analysis and
  the previous report moves into that list. "Delete" removes the recording
  and every report made from it (`DELETE /api/reports/{id}` removes a
  report with its corrections, cached frames and run directory). A report
  whose recording is gone is a row by itself with only Open and Delete.
  Reports imported with `tracen import` belong to no account, are visible
  to all and cannot be deleted from the page.

## The report page

The turn's log shows one card per thing that happened, the way a player
thinks of it: a training with its option, its result and its gains; a
lesson with its cost, the award its receipt paid and the song it taught; a
race with its placing and fans; a concert with its rewards. Each card opens
to the report entries behind it, each with its own time and pencil. Receipt
lines the reader caught mid-scroll, cut short or garbled repeats of a line
already in the log, fold into one quiet "OCR Fragments" line. A card's own
line carries only the flags that ask for a look.

The page is built from the timeline document only and shows what the analyzer
recorded; it never derives a value of its own. It reads like a replay:

- **Header.** The recording, its length and turn count, how many stat changes
  the accounting explained, and the stats at the end of the run (the last
  turn's accounted end values, or its opening values when the end was not
  observed) with the game's rank letter beside each.
- **Timeline.** One scrubber for the whole career: 24 half-month cells per
  year for Junior, Classic and Senior (the pre-debut countdown fills January
  to June of the Junior year) and three cells for the finale races. A cell
  takes the colour of the turn's training option (speed, stamina, power,
  guts, wit) or marks a race, rest or outing; hatched cells are turns where no
  action was seen, dashed cells were not observed at all, and a dot marks a
  cell holding more than one observed window. Clicking a cell, `←`/`→`, or
  the Previous/Next buttons select a turn; the turn is in the address so it
  can be linked to, but moving between turns replaces the history entry, so
  the browser's Back button leaves the report rather than retracing turns. On narrow screens the scrubber
  wraps into one row per year.
- **Recording.** A normal player (scrub bar, play, pause, volume, full
  screen) with one-second and one-frame steps beside it. Selecting a turn
  seeks the video to the turn's start and any time in the log seeks there;
  scrubbing or playing the video selects the turn whose window holds that
  moment, so the log follows the recording. The video is served from the
  upload with range requests, so nothing is copied. A
  report whose recording is not on this machine (an import without
  `-recording`) shows the analyzer's extracted frame instead.
- **This turn.** The action the ledger recorded for the turn (with a link to
  its moment), any caveats of the window (accounting gaps, flagged entries,
  each linking to its moment), the stats at the start of the turn with rank
  letters and the accounted end value of each, the performance values, and
  the log of every entry in the window with its changes (the five stats and
  skill points first, the scenario's own points after; gains in orange,
  losses in blue; an amount that was derived rather than read carries a
  mark, and "more" shows its basis and the raw record). The pane has a
  fixed height: the title and the chosen action stay put and everything
  below scrolls; a long window gets a row of kind filters, and a long list
  of caveats is collapsed to three lines until asked.
- **Check.** Every caveat in the report, grouped by turn: fields whose
  accounting is not balanced, openings not observed, windows with no or
  several actions, and each flagged entry (conflicting readings, ambiguous
  effects, unparsed receipts, unresolved lesson costs, incomplete purchase
  lists, derived amounts). Every line is a link: a turn line opens the turn,
  an entry line opens the turn and seeks the recording to that entry. Turns
  with a serious caveat carry an orange mark on the timeline. Skipped
  analysis stages and the entries outside every observed window are listed
  here too; those entries are never folded into a neighbouring turn.
- **Stats across the run.** Below the replay, the opening value of each stat
  at every turn as a wide line chart. A turn whose opening was never on
  screen (a race-day hub shows no stat bar) is drawn as a hollow point at the
  value carried from the previous turn's entries, marked ≈ in the tooltip and
  under the recording; a turn with neither breaks the line rather than being
  interpolated. Clicking the chart opens
  the nearest turn. Beside it, each stat's first and last value, rank, gain
  and the number of turns that trained it.
- **How the run was played.** Counted from the ledger only: turn choices
  (per training option, races, rests, outings, overall and per year),
  training efficiency per stat (sessions, average and total gain, best
  session), growth by year, the first turn each stat reached B, A, S, SS
  and UG, the races with grade, placing and fans, skill points earned,
  spent and left with the skills bought, the skills hinted with their
  levels, rests, outings and the longest run of turns without a rest, the
  event outcomes and the biggest ones, and the scenario's own economy
  (lessons, performance points, songs, concerts) when the ledger recorded
  it. Every item opens its turn.
- **Theme.** The page follows the system light or dark setting; the button
  in the top bar switches it, and the choice is kept per browser. The mark
  in the top bar and the favicon (`web/public/favicon.svg`) are drawn for
  this app: a green tile, a white loop for the track, a pink play mark.

### Stat rank letters

The rank beside a stat is the letter the game shows for that value. The
bands were read from the game's own badges in the local recordings (F at 137,
F+ at 150, E at 239, E+ at 292, D at 325, D+ at 351, C at 403, C+ at 500, B
at 621, B+ at 744, A at 851, A+ at 940, S at 1008, S+ at 1073, SS at 1130,
SS+ at 1159, UG1 at 1215, UF9 at 1390, UE6 at 1461); G, G+ and the U letters
above UE follow the same pattern but were not observed.

| Value | Rank |
| --- | --- |
| 1–49 / 50–99 | G / G+ |
| 100–149 / 150–199 | F / F+ |
| 200–249 / 250–299 | E / E+ |
| 300–349 / 350–399 | D / D+ |
| 400–499 / 500–599 | C / C+ |
| 600–699 / 700–799 | B / B+ |
| 800–899 / 900–999 | A / A+ |
| 1000–1049 / 1050–1099 | S / S+ |
| 1100–1149 / 1150–1199 | SS / SS+ |
| 1200–1299 | UG, with a digit for each ten above 1200 (1215 is UG1) |
| 1300–1399, 1400–1499, … | UF, UE, UD, UC, UB, UA, US, each with the same digit |

There are no minus ranks. The mapping lives in `web/src/format.ts` (`statRank`).

## Import an existing analysis

A report produced by a direct Python run (`report.json` plus `timeline.json`
in one directory) can be registered without copying it:

```powershell
.\tracen.exe import -data .local\tracen-data -recording D:\recordings\run.mp4 RUN_DIR
```

The report is hashed and the timeline validated; the record points at the
originals in place, so moving or editing them later makes the report
unavailable rather than silently different. `-recording` enables frame
extraction for that report.

## Messages you can expect

API errors are JSON `{"error": {"code", "message"}}` and the client shows the
message next to the control that caused it.

| Situation | Code | What to do |
| --- | --- | --- |
| a required tool or folder is missing | `/readyz` check `ok: false` with the path | fix the path or install the tool before queueing work |
| the chosen recording was deleted or belongs to another account | `unknown_source` | refresh the list |
| too many jobs are waiting | `queue_full` (HTTP 429) | wait for a job to finish or start with a larger `-queue` |
| the analyzer refused its inputs or failed | job status `failed` with `error.code` and `error.message` from the worker (`source_not_found`, `invalid_paths`, `source_unreadable`, `producer_failed`, `report_missing`, `source_mismatch`) | open the job's log (`/api/jobs/{id}/log`, the "Worker log" link on the job page) |
| the worker's answer did not meet the contract | job status `failed` with a verification code (`bad_terminal_output`, `schema_mismatch`, `status_exit_mismatch`, `report_missing`, `report_hash_mismatch`, `timeline_missing`, `timeline_invalid`) | the report is never shown as successful in that case; the log tells what the worker wrote |
| the analyzer finished but skipped stages | job status `completed_with_stage_failures` and the list of skipped stages | the report is usable; the skipped stages name what is missing |
| the report file was moved or edited | `report_unavailable` | re-import or restore the file; hashes are checked on every access |
| the recording is gone | `recording_unavailable` | frames cannot be extracted and the video panel falls back to frames; the report itself still loads |
| the credentials are wrong or the session expired | `bad_credentials`, `unauthenticated` (HTTP 401) | sign in again; after repeated failures the address is held off for a while (`too_many_attempts`, HTTP 429) |
| the email is already registered | `email_taken` (HTTP 409) | sign in with it instead |
| the upload is not a video or is too large | `unsupported_recording`, `upload_too_large` (HTTP 413) | the accepted extensions and the size limit are stated in the message |
| the upload is being analyzed | `recording_in_use` (HTTP 409) | cancel or wait for the analysis before deleting it |
| no frame at that time | `frame_unavailable` | ffmpeg could not decode a frame there |

## Limits

- One analysis at a time; a full career recording takes roughly 40 to 60
  minutes on a GeForce RTX 5060 Ti with four OCR workers.
- Job outputs keep the report, the timeline, the viewer page and the worker
  log (a few hundred MB, most of it the report); frame images and the OCR
  working data are deleted when the job ends unless `-keep-working-data` is
  set, so evidence frames are re-extracted from the recording on demand and a
  finished job cannot be re-parsed.
- The viewer shows what the report records and marks unknown values as
  unknown; it never derives values of its own.
- Accounts are a convenience for one machine shared by a few people, not a
  security boundary: the server listens on loopback only, and anyone with
  access to the data directory can read every recording and report.
