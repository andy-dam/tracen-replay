# Deployment

Tracen Replay runs entirely in the cloud: the browser client, the API, the
uploads, the database and the analyses. No personal machine is part of the
hosted service. This document is the plan: what runs where, why, what it
costs, how much it can do, how to set it up, and how to keep it running for
a year on a student budget. The parts that exist today are marked as such;
the parts still to build are listed under "Code changes" with their order.

Two clouds are used, each for what it gives away:

- **Azure**, on the Azure for Students offer: the client, the API, the
  database, the kept recordings and reports, the job queue, and an overflow
  analysis worker. This is where the managed-service learning happens.
- **Oracle Cloud**, on its Always Free tier: the always-on analysis worker,
  because Oracle gives away far more compute for nothing than Azure does,
  and the analysis is the whole cost of this application.

## 1. The numbers everything else rests on

Measured on this codebase unless marked as an estimate.

**One analysis.** A career recording is 30 to 42 minutes of 1080p video,
0.7 to 1.9 GB. The analyzer reads about 9,000 frames at 4 per second, and
the cost is recognising about 50 text lines on each: on the CPU provider a
5-minute clip took 1,147 s with two OCR workers and one dense worker on a
6-core desktop CPU (measured 2026-09-19), so a 40-minute career is about
2.6 hours on that CPU. Estimates for the cloud workers, to be replaced by
the first measured job of each:

| Worker | Cores | Estimated time per career | Memory it needs |
| --- | --- | --- | --- |
| Azure Container Apps consumption job | 4 vCPU | 3 to 4 hours | 5 GB main process + 2 GB per OCR worker: two workers fit 8 GiB |
| Oracle A1 Always Free | 2 Arm OCPU, 12 GB | 6 to 8 hours | one OCR worker, or two with 12 GB |
| Oracle A1 with the account upgraded | 4 Arm OCPU, 24 GB | 3 to 4 hours | two OCR workers |

**What is kept.** After the analysis the original is not needed. A 720p,
30 fps H.264 copy of a career is 0.17 to 0.21 GB (measured on a 0.94 GB
original: CRF 26 gives 211 MB in 152 s of CPU, CRF 28 gives 167 MB), and
the viewer needs nothing more for playback and evidence frames. A report
is a few MB. So a kept career costs about 0.2 GB, and 100 GB holds about
500 of them.

**Azure for Students** ([offer](https://azure.microsoft.com/en-us/free/students/),
checked 2026-09-20): $100 of credit for 12 months, no card, renewed by
signing up again each year while a student, unused credit lost at the
anniversary. Free regardless of the credit: Container Apps 180,000
vCPU-seconds, 360,000 GiB-seconds and 2 million requests a month; Static
Web Apps with a hard 100 GB a month bandwidth cap; App Service F1 with one
CPU hour a day; Functions 1 million requests; Cosmos DB 1,000 RU/s and 25
GB; Azure SQL serverless 100,000 vCore-seconds and 32 GB. Free for the 12
months: PostgreSQL Flexible Server B1ms 750 hours a month, a Standard
container registry, Blob 5 GB. For everyone: the first 100 GB a month of
internet egress free, then $0.087 per GB; Blob hot LRS about $0.017 per
GB-month; a consumption job at $0.000024 per vCPU-second and $0.000003 per
GiB-second past the grant; an always-on Container Apps replica of 0.25
vCPU and 0.5 GiB about $6.60 a month. Traffic between Blob and a Container
Apps job in the same region is not internet egress.

**Oracle Cloud Always Free** ([limits](https://docs.oracle.com/en-us/iaas/Content/FreeTier/freetier_topic-Always_Free_Resources.htm),
checked 2026-09-20): Ampere A1 compute of 2 OCPUs and 12 GB total (halved
from 4 and 24 on 2026-06-15; an account upgraded to pay-as-you-go keeps 4
OCPUs and 24 GB free and is not charged while within the free limits), two
AMD micro VMs of 1 GB, 200 GB of block storage, 20 GB of object storage, 10
TB a month of outbound transfer, one flexible load balancer, two Autonomous
Databases of 20 GB. Two rules matter: an Always Free instance idle for 7
days (CPU below 20 percent at the 95th percentile, network and memory
likewise) may be reclaimed, and upgrading to pay-as-you-go ends that; and
"out of host capacity" on creating an A1 instance is common and clears
with retries over days. A card is needed to open the account and for the
upgrade; nothing is charged within the free limits.

## 2. What runs where

| Part | Where | Tier | Cost a year | State |
| --- | --- | --- | --- | --- |
| Browser client (Vue, static) | Azure Static Web Apps | free | $0 | exists, built by `web/` |
| API (the Go service, `-api-only`) | Azure Container Apps, consumption, scale to zero, 0.5 vCPU / 1 GiB | free grant | $0 to $20 | exists; needs the store, object store and queue adapters below |
| Database (users, sessions, recordings, jobs, reports, corrections) | Azure Database for PostgreSQL Flexible Server B1ms | free for 12 months | $0 the first year, about $13 a month after unless the renewal restarts it | store port to build |
| Job queue | Azure Storage Queue in the same storage account | pennies | $0 | to build |
| Originals | Azure Blob: hot until the analysis is done, then the cold tier for 90 days (a lifecycle rule moves and deletes them) | $0.017 per GB-month hot, about $0.0036 cold | about $13 for a rolling 300 GB of cold originals | to build |
| Kept 720p copies, reports, frame cache | Azure Blob, hot LRS | $0.017 per GB-month | about $22 per 100 GB; a year of copies at 100 a month is about $24 | to build |
| Always-on analysis worker | Oracle A1 VM (2 OCPU / 12 GB; 4 / 24 upgraded) running the worker image, arm64 | free | $0 | worker mode to build; image exists for amd64 |
| Overflow analysis worker | Azure Container Apps job, consumption, 4 vCPU / 8 GiB, triggered by queue length | free grant then credit | about $1.50 per analysis past the grant | job wiring to build; image exists |
| Container images | GitHub Container Registry | free | $0 | multi-arch build to add to CI |
| Domain and TLS | one domain, `app.` and `api.` subdomains, managed certificates on both Azure services | domain only | about $12 | to buy |
| Logs, metrics, cost alert | Azure Monitor and Log Analytics free tier; an Azure budget alert; Oracle instance metrics | free | $0 | to configure |

Registration is open. What bounds cost is the per-account budgets and the
service-wide budgets in section 5, not who may sign up.

## 3. How a recording becomes a report, in the cloud

1. **Sign in.** The client at `app.<domain>` calls `api.<domain>`. The two
   are one site, so the session cookie is `SameSite=Lax`, `Secure`,
   `HttpOnly` (`-cookie-samesite lax`, `-allowed-origin
   https://app.<domain>`, `-allowed-host api.<domain>`). Container Apps
   ingress terminates TLS and forwards the client address in
   `X-Forwarded-For`; the ingress is named in `-trusted-proxy` so the
   sign-in and registration limits count real clients.
2. **Upload.** The client asks the API for an upload target. The API checks
   the account's quotas and answers with a SAS URL for one blob in the
   `originals` container (a few minutes' validity, a name of the API's
   choosing). The browser uploads straight to Blob; the API never carries
   the bytes. The client then tells the API the upload is complete; the
   API runs ffprobe against the blob's URL (ffprobe reads headers over
   HTTP range requests, a few MB), refuses what is not a video or is over
   the limits, and records the recording.
3. **Queue.** A job row is created and a message with the job id goes on
   the Azure Storage Queue. The per-account and service-wide budgets are
   checked here, as today.
4. **Analyze.** A worker takes the message (the Oracle VM's worker loop
   polls; the Azure job is started by the queue scaler when messages wait
   and the monthly compute budget allows), downloads the original from
   Blob (for the Oracle worker that is 1 to 2 GB of Azure egress, counted
   in section 4), runs the analyzer exactly as the local service does
   (same image, same arguments, `docs/analysis-job.md`), and reports
   progress by updating the job row. The message's visibility timeout is
   renewed while the analysis runs; a worker that dies lets the message
   reappear, and the job is retried once.
5. **Finish.** The worker encodes the 720p copy, uploads the copy, the
   report and the timeline to Blob, writes the report row and acknowledges
   the message. The original stays: a lifecycle rule on the `originals`
   container moves a blob to the cold tier a day after it was last
   modified and deletes it 90 days after. Within those 90 days the
   recording can be analyzed again from its page (a new job like any
   other, reading the cold blob back at about a cent per GB); after them
   a new analysis means a new upload, and the recording's page says so.
6. **View.** The client reads the report through the API as today. Video
   playback and evidence frames come from the kept copy: the API answers
   the video route with a short-lived SAS URL to the copy in Blob, and
   frames are extracted from the copy by the API's ffmpeg into the frame
   cache container. A full watch of a career is about 0.2 GB of egress.
7. **Sweep.** The hourly sweep (exists today) deletes kept copies past
   their retention when nothing is analyzing them, drops expired sessions
   and prunes the frame cache. Originals are the lifecycle rule's job, so
   the sweep only removes the rows of originals the rule has deleted.

## 4. Capacity

Analyses are the whole cost; storage and traffic are small beside them.

| Source of capacity | Analyses a month | What it costs |
| --- | --- | --- |
| Oracle A1, 2 OCPU, around the clock | 90 to 120 (one at a time, 6 to 8 hours each) | $0 |
| Oracle A1 upgraded to 4 OCPU | 180 to 240 (one at a time, 3 to 4 hours each; two at a time with 24 GB) | $0, card on file |
| Azure job inside the free grant | 3 to 4 | $0 |
| Azure job from the credit | about 65 a year, so 5 to 6 a month | $1.50 each, the credit pays |

So the plan carries roughly 100 analyses a month with the Always Free
Oracle account, plus a few from Azure. Two things bound it besides the
worker:

- **Egress.** Every analysis on the Oracle worker pulls its original out
  of Azure, 1 to 2 GB, and every full watch of a kept copy is 0.2 GB. The
  first 100 GB a month are free, then $0.087 per GB. At 100 analyses a
  month the pulls alone are about the free amount, so watching costs a
  few dollars a month; at 200 a month (the upgraded Oracle account) the
  overage is about $9 a month, which is the whole credit. If capacity is
  ever pushed that far, originals move to an Oracle Object Storage
  transit bucket next to the worker (20 GB free) and are copied to Azure
  cold storage after the analysis, which is free in both directions and
  removes the pull from the bill; that is one more store to build, and it
  is not part of the first deployment.
- **Storage.** Originals: a rolling 90 days at 100 uploads a month is
  about 300 GB cold, $1 a month. Kept copies: 0.2 GB each, never deleted
  within the year, about $2 a month by the end of it.

**Spending the credit.** The credit is this project's whole budget for the
year, and it should be spent, not saved. About $13 keeps every original
for 90 days; about $24 keeps every 720p copy for the year; $12 to $24
covers egress overages; the rest, about $40, buys 25 to 30 overflow
analyses on Azure jobs, which is what `-monthly-total` fences. The $8
monthly budget alert is the guard against spending it early.

The budgets in the service (section 5) must be set from this table, not
from the local machine's defaults. The first thing to do after provisioning
is one measured job on each worker, which replaces the estimates above.

### Levers that raise capacity

Tested on 2026-09-20 on a five-minute clip against the current code:

- **Skip lines no rule reads.** 30 percent of the text lines the detector
  finds lie outside every fixed box a parser opens, but the parsers also
  find lines by their words anywhere on the pane (the turn counter, Energy,
  Goal, the performance panel), and skipping by position dropped those.
  Not safe as a static filter; the gain would be a few percent once the
  needed lines are kept. Not pursued.
- **Reuse a line's recognition when its pixels did not change.** 40 percent
  of detected lines are the same pixels, within noise, as a line in the
  same place on the previous frame this process read (mean difference at
  most one level and at most two pixels changed by more than 40), and the
  recogniser is deterministic on identical pixels. Reusing that text
  instead of recognising again (`TRACEN_REPLAY_OCR_LEAN=reuse`,
  `vision.NeuralReader._engine_lines`) ran the clip in 1,124 s against
  1,370 s (base OCR 388 s against 558 s, the dense rereads 543 against
  578), an 18 percent saving end to end. The report lost nothing: every
  turn's opening and counted changes identical, no unexplained field, no
  serious flag in either run; frame-level OCR text differs on most
  frames because the reused read is an earlier frame's jitter rather than
  this frame's, which the event grouping absorbs, and two trainings and
  one performance award came out better named or observed rather than
  derived. It is off by default until one full career has been run both
  ways; then it becomes the default and the cloud capacity figures above
  rise by about a fifth.
- **A GPU worker.** Not available on either free tier. The CUDA image
  exists; a paid GPU job is the only way to bring an analysis under an
  hour, at several dollars each on Azure, so it is not part of this plan.

## 5. Budgets and limits in the service

The service already carries every bound below as a flag
([local-app.md](local-app.md) lists them). Hosted values:

| Bound | Flag | Hosted value | Why |
| --- | --- | --- | --- |
| Accounts per address | built in | 5 an hour | a script cannot fill the user table |
| Upload size | `-upload-limit-gb` | 3 | a 1080p career is 0.7 to 1.9 GB |
| Recording length | `-max-duration` | 3h | a career is 30 to 45 minutes; a stream VOD of hours is not a career |
| Uploads kept per account | `-max-recordings` | 10 | originals live 90 days, so an account holds a few weeks of play |
| Bytes kept per account | `-max-recording-gb` | 20 | ten originals |
| All uploads together | `-max-storage-gb` | 400 | a rolling 90 days at the planned rate, with room; past it uploads are refused as temporary |
| Active analyses per account | `-max-active-per-user` | 1 | one place in the queue each |
| Analyses per account per day | `-daily-per-user` | 2 | a career and a retry |
| Analyses for everyone per day | `-daily-total` | 4 | what the Always Free worker finishes in a day; queued work past this waits until tomorrow |
| Analysis wall clock | `-max-analysis` | 10h | the slowest worker's time with margin |
| Original retention | lifecycle rule on `originals` | 90 days, cold after a day | re-analysis within them, a new upload after |
| Kept-copy retention | `-recording-retention` | 0 (kept for the year) | the credit pays for it; the report and its video stay together |
| Frame cache | `-frame-cache-gb` | 2 | |

To add: a monthly service-wide analysis budget (`-monthly-total`) that
fences the Azure overflow job's spend, and an account-level "copies kept"
count separate from originals.

## 6. Provisioning

Azure, one resource group, one region (East US for the cheapest Blob):

1. Storage account (LRS): blob containers `originals`, `kept`, `reports`,
   `frames`; queue `analyses`; lifecycle rules: `originals` to the cold
   tier one day after last modification and deleted after 90 days,
   `frames` deleted after 30 days.
2. PostgreSQL Flexible Server B1ms, 32 GB, the free-tier SKU; a database
   `tracen`; firewall to the Container Apps environment only.
3. Container Apps environment with Log Analytics; a container app for the
   API (`-api-only`, min replicas 0, max 2, 0.5 vCPU / 1 GiB, managed
   identity granted Storage Blob Data Contributor and Queue Data
   Contributor on the storage account, the Postgres password and the
   Oracle API key as secrets); a container app job for the worker (4 vCPU /
   8 GiB, event trigger on the `analyses` queue, max one parallel
   execution, the same identity and secrets).
4. Static Web App (free) bound to the `web/` build; custom domain
   `app.<domain>`; the API's custom domain `api.<domain>` with the managed
   certificate.
5. A budget of $8 a month on the subscription with alerts at 50 and 80
   percent; a Log Analytics alert on failed jobs.

Oracle, one tenancy in a home region near Azure's:

1. Compartment, VCN with one public subnet, security list allowing only
   outbound (the worker needs no inbound port).
2. An A1.Flex instance at the free limit (2 OCPU / 12 GB, or 4 / 24 after
   the upgrade), Oracle Linux or Ubuntu arm64, a 100 GB boot volume; retry
   creation until capacity appears.
3. On the instance: docker, the arm64 worker image from GHCR, and the
   worker as a systemd service with the Azure queue and storage
   credentials (a service principal limited to the queue and the four
   containers) in its environment; a health timer that records a heartbeat
   to the API. The worker's own load keeps the instance above the idle
   thresholds; on an Always Free account an empty queue for a week would
   not.
4. Later, only if capacity is pushed past what egress allows: an Object
   Storage bucket as the upload transit next to the worker (section 4).

## 7. Building and shipping

[ci-cd.md](ci-cd.md) describes the pipeline shape; the changes for this
plan are:

- `docker buildx` builds the worker image for `linux/amd64` and
  `linux/arm64` and the service image for `linux/amd64`, tagged by commit,
  pushed to GHCR.
- The API deploy updates the container app to the new image; the job is
  updated to the new worker image; the Oracle worker pulls the tag the
  API reports as current on its next idle moment and restarts itself.
- The client deploy is the Static Web Apps GitHub action on the `web/`
  build.
- A report records the analyzer identity that made it, so a report made
  by an older image is shown as such (exists today).

## 8. Code changes, in order

Each step keeps the local single-binary application working, because the
local application is the development loop.

1. **Object store interface.** `Put`, `Get`, `Delete`, `Presign` for
   upload and for read, `List` by prefix and age. Implementations: the
   local directory (today's behaviour) and Azure Blob. Uploads, kept
   copies, reports and the frame cache go through it.
2. **Postgres store.** The `store` package behind the same interfaces on
   Postgres, with the SQLite implementation kept; migrations shared.
3. **Queue interface and worker mode.** The manager splits into the
   submitter (in the API) and the worker loop (`tracen worker`), joined by
   a `Queue` with in-process (local) and Azure Storage Queue
   implementations. The worker loop pulls, renews visibility, runs the
   analyzer through the existing `Runner`, uploads results, acknowledges.
4. **Upload by SAS URL** with the ffprobe gate run against the blob URL,
   and a recording page that says until when the original can be analyzed
   again.
5. **Keep-copy encode** on the worker after a successful analysis, and the
   video and frame routes served from the copy. Re-analysis reads the
   cold original (a rehydration is not needed for the cold tier, only a
   read charge).
6. **Monthly budget flag** and the Azure job's own budget check.
7. **Deployment definitions** (Bicep for Azure, a Terraform or shell script
   for Oracle) and the CI changes above.

## 9. Running it for a year

- **Renewal.** The Azure for Students credit ends on the anniversary and
  is not automatic: sign up again a week before, with the school email.
  Whether the 12-month free services (Postgres) restart with the renewal
  is to be checked on that day; if not, the database moves to Azure SQL
  serverless (always free) or Postgres is paid at about $13 a month.
- **Credit exhaustion.** If the credit runs out the subscription is
  disabled and the site goes down. The $8 budget alert exists for this;
  the monthly analysis budget is the control.
- **Oracle capacity.** A1 creation can fail for days. Keep the instance
  once it exists; never delete it to recreate it.
- **Oracle idle reclaim.** On an Always Free account the worker must stay
  busy or the instance may be reclaimed after a quiet week; upgrading the
  account removes the rule and doubles the free compute.
- **Secrets.** The worker's service principal and the Postgres password
  live in Container Apps secrets and on the Oracle instance only; rotate
  both when anyone else has had access to either place.
- **Backups.** Postgres keeps 7 days of automated backups on the free
  tier; Blob soft delete for 14 days on `kept` and `reports`. A report can
  be regenerated from its original for 90 days at the cost of one
  analysis; after that only from a new upload.
- **Stale analyzer.** A report made by an older image is marked stale in
  the viewer; re-analysis is a normal job and spends budget like any
  other.

## 10. Security in the hosted shape

Everything in [local-app.md](local-app.md)'s "Limits" and the abuse bounds
below hold unchanged; the hosted additions are TLS on both hosts,
`-cookie-samesite lax` across the two subdomains, `-allowed-host` and
`-trusted-proxy` set for the ingress, the worker's identity limited to the
`analyses` queue and the four containers, and the `originals` container
writable only through the SAS URLs the API issues. The worker container
cannot run with `--network none` here, since it reads and writes object
storage; its network is the VCN's outbound rule only.

## Abuse and limits

A hosted service is asked for two things it is not asked for on one
machine: to refuse what a stranger can do to it, and to spend no more than
its owner can afford for a year. Both are in the service today, as flags
whose defaults are set for a small hosted deployment; one machine used by its
owner turns the budgets off ([local-app.md](local-app.md) lists every flag).

What a stranger cannot do:

- **Create accounts at will.** Registration is open, and what bounds it is
  that one address makes at most five accounts an hour and that an account
  buys nothing by itself: what costs money is bounded per account below,
  so a crowd of accounts from one person is a crowd of daily budgets, not
  a crowd of machines. Sign-ins stay limited to ten attempts per address
  per five minutes. Behind a reverse proxy the client is the address the
  proxy forwarded (`-trusted-proxy`), so one proxy does not make everyone
  one address. Should open registration ever be abused, `-registration
  closed` stops new accounts and `-registration invite` with `-invite-code`
  takes them by code (the API accepts an `invite` field; the sign-up form
  does not show one).
- **Reach another user's data.** Every recording, job and report is looked
  up under the signed-in user; another user's id is "not found". The client
  never names a path; every file the service opens is confined under its
  own directory. A 5xx tells the client one fixed sentence and the log the
  detail.
- **Fill the disk.** One upload is at most 3 GB; a user keeps at most five
  uploads and 8 GB; all uploads together at most the configured total,
  past which uploads are refused as temporary; uploads are probed with
  ffprobe before they are recorded and refused when they are not a video,
  longer than three hours, larger than 4096x2304 or faster than 120
  frames a second; uploads older than the retention that nothing is
  analyzing are deleted by the hourly sweep (their reports stay); the
  extracted-frame cache is pruned to 2 GB; expired sessions are dropped.
- **Burn the workers.** A user has one analysis queued or running at a time
  and a daily budget; the service has a daily budget; an analysis is
  stopped at its wall-clock bound; frame extractions are at most 120 a
  minute per user and two at once; a worker container has bounded memory
  and processes.
- **Talk the service into something.** Only the configured hosts are
  answered (`-allowed-host`); a cross-origin request that could change
  state is refused unless its origin is an allowed client; the session
  cookie is HttpOnly, SameSite and, over HTTPS, Secure; every response says
  what it is and refuses framing, and the client's page is pinned to its own
  origin by a content security policy; headers are read within ten seconds
  and bounded at 64 KiB, idle connections closed after two minutes, JSON
  bodies bounded at a few KiB.

## What already is, and what is still local

The client is a static bundle and never learns storage layout; the worker
contract is a process boundary (`docs/analysis-job.md`); jobs and reports
are rows behind store interfaces; uploads are one directory behind the
API; the OCR device is a worker flag; the application and worker images
exist ([container.md](container.md)). What is still local, and what the
steps in section 8 replace: the store is SQLite on the service's disk, the
queue is in the service's process, uploads and outputs are local
directories, and the worker is spawned by the service on its own host.
