<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref } from "vue";
import { api, ApiError, crossOrigin, hosted, type Job, type Recording, type Report } from "../api";
import { bytes, clock, runTime, skillPointsEarned, STAT_NAMES, when } from "../format";
import { reviewSettles, turnAsks } from "../warnings";
import { progressView } from "../phases";
import { desktop, openExternal } from "../mode";
import RankBadge from "../components/RankBadge.vue";
import UploadBox from "../components/UploadBox.vue";
import ConfirmDialog from "../components/ConfirmDialog.vue";

// One list. A run is a recording, the analysis made from it and the report
// that came out, shown as one row from upload to result.
const reports = ref<Report[]>([]);
const jobs = ref<Job[]>([]);
const recordings = ref<Recording[]>([]);
const error = ref("");
// True once the recordings list has really been read.
const loaded = ref(false);
const busy = ref("");
// The local application says what the analyzer will run on and whether a
// newer release exists; the hosted service has neither to say.
const device = ref("");
const update = ref<{ current: string; latest: string; url: string } | null>(null);
let timer: number | undefined;

// What each finished report says about its run: the stats at the end, how
// much of the career's stat changes the report explains, and how many turns
// ask for a look. Read once per report and kept.
interface RunFacts {
  final: Record<string, number | null> | null;
  // Fields whose end-of-run value was never read; `final` holds the last value read for them.
  open: string[];
  explained: number;
  total: number;
  toCheck: number;
  /** Skill points the whole run earned, when the report's entries say. */
  earned: number | null;
}
const STATS = ["speed", "stamina", "power", "guts", "wit", "skill_points"] as const;
const facts = ref<Record<string, RunFacts>>({});
const loadingFacts = new Set<string>();
async function loadFacts(report: Report) {
  if (facts.value[report.id] || loadingFacts.has(report.id)) return;
  loadingFacts.add(report.id);
  try {
    const [summary, turns, reviews] = await Promise.all([api.summary(report.id), api.turns(report.id), api.corrections(report.id).catch(() => [])]);
    const counts = summary.summary.field_status_counts ?? {};
    const total = Object.values(counts).reduce((a, b) => a + b, 0);
    const explained = (counts.balanced_observations ?? 0) + (counts.balanced_with_derived_changes ?? 0);
    // A turn a saved review settles is not waiting any more.
    const settled = new Set(reviews.filter((r) => reviewSettles(r, [])).map((r) => r.correction.turn_id));
    const toCheck = turns.filter((t) => !settled.has(t.id) && (turnAsks(t).serious.length > 0 || turnAsks(t).confirm.length > 0)).length;
    let final: Record<string, number | null> | null = null;
    let open: string[] = [];
    const last = [...turns].reverse().find((t) => t.opening.stats);
    if (last) {
      final = { ...(last.opening.stats as Record<string, number | null>) };
      open = [...STATS];
      try {
        const detail = await api.turn(report.id, last.id);
        for (const field of STATS) {
          const after = detail.turn.accounting.stats?.[field]?.after;
          if (after !== null && after !== undefined) {
            final[field] = after;
            open = open.filter((f) => f !== field);
          }
        }
      } catch {
        // the opening of the last observed turn stands, marked as such
      }
    }
    const earned = skillPointsEarned(summary.skill_points, open.includes("skill_points") ? null : final?.skill_points);
    facts.value = { ...facts.value, [report.id]: { final, open, explained, total, toCheck, earned } };
  } catch {
    // a report that cannot be summarized simply has no facts on the dashboard
  } finally {
    loadingFacts.delete(report.id);
  }
}
// A run is one recording: the analysis last made from it and the report that
// came out. Reports from earlier analyses of the same recording stay with the
// run as its history rather than becoming rows of their own. A report whose
// recording is gone (deleted, or imported from elsewhere) is a run by itself.
interface Run {
  key: string;
  name: string;
  /** When the shown report was made, or when the recording was uploaded if there is none yet. */
  date: string;
  analyzed: boolean;
  size: number | null;
  sourceId: string;
  recording: Recording | null;
  /** The recording's latest analysis, whatever its state. */
  job: Job | null;
  /** The analysis that made the shown report. */
  madeBy: Job | null;
  report: Report | null;
  earlier: Report[];
  thumb: string;
}

const active = (j: Job) => j.status === "queued" || j.status === "running";
// A paused analysis, or one recorded as interrupted before interruptions were pauses: both resume.
const paused = (j: Job | null) => j?.status === "paused" || j?.status === "interrupted";
// An analysis left paused until its progress was deleted: the recording
// reads as never analyzed, so the job is left out of the list.
const expiredPause = (j: Job) => j.status === "cancelled" && j.error?.code === "pause_expired";
const newestFirst = (a: { created_at: string }, b: { created_at: string }) => b.created_at.localeCompare(a.created_at);

const runs = computed<Run[]>(() => {
  const out: Run[] = [];
  const used = new Set<string>();
  const reportById = new Map(reports.value.map((r) => [r.id, r]));
  for (const u of recordings.value) {
    const analyses = jobs.value.filter((j) => j.source_id === u.id && !expiredPause(j)).sort(newestFirst);
    const made = analyses
      .filter((j) => j.report_id && reportById.has(j.report_id))
      .map((j) => ({ job: j, report: reportById.get(j.report_id!)! }))
      .sort((a, b) => newestFirst(a.report, b.report));
    for (const m of made) used.add(m.report.id);
    const latest = made[0] ?? null;
    out.push({
      key: "u:" + u.id,
      name: u.name,
      date: latest ? latest.report.created_at : u.created_at,
      analyzed: latest !== null,
      size: u.size,
      sourceId: u.id,
      recording: u,
      job: analyses[0] ?? null,
      madeBy: latest?.job ?? null,
      report: latest?.report ?? null,
      earlier: made.slice(1).map((m) => m.report),
      thumb: latest ? api.frameUrl(latest.report.id, 30000) : api.recordingFrameUrl(u.id, 30000),
    });
  }
  for (const r of reports.value) {
    if (used.has(r.id)) continue;
    out.push({ key: "r:" + r.id, name: r.source_name, date: r.created_at, analyzed: true, size: null, sourceId: "", recording: null, job: null, madeBy: null, report: r, earlier: [], thumb: api.frameUrl(r.id, 30000) });
  }
  return out.sort((a, b) => b.date.localeCompare(a.date));
});

// Which runs show their earlier analyses.
const opened = ref<Record<string, boolean>>({});

const statTotal = (f: RunFacts) => (f.final ? STATS.filter((s) => s !== "skill_points").reduce((a, s) => a + (f.final![s] ?? 0), 0) : 0);
const dashboard = computed(() => {
  // The latest report of each run; earlier analyses of the same recording do not count twice.
  const latest = runs.value.map((r) => r.report).filter((r): r is Report => r !== null);
  const done = latest.filter((r) => facts.value[r.id]);
  const explained = done.reduce((a, r) => a + facts.value[r.id].explained, 0);
  const total = done.reduce((a, r) => a + facts.value[r.id].total, 0);
  const toCheck = done.reduce((a, r) => a + facts.value[r.id].toCheck, 0);
  const turns = latest.reduce((a, r) => a + r.turns, 0);
  const minutes = Math.round(latest.reduce((a, r) => a + r.duration_ms, 0) / 60000);
  const stored = recordings.value.reduce((a, u) => a + u.size, 0);
  return { runs: latest.length, turns, minutes, explained, total, pct: total ? Math.round((100 * explained) / total) : 0, toCheck, active: jobs.value.filter(active).length, stored, recordings: recordings.value.length };
});

// The list can be narrowed by name and put in another order.
const query = ref("");
const order = ref<"newest" | "oldest" | "name" | "stats">("newest");
const shown = computed(() => {
  const needle = query.value.trim().toLowerCase();
  const list = needle ? runs.value.filter((r) => r.name.toLowerCase().includes(needle)) : [...runs.value];
  const total = (r: Run) => (r.report && facts.value[r.report.id]?.final ? statTotal(facts.value[r.report.id]) : -1);
  switch (order.value) {
    case "oldest":
      return list.sort((a, b) => a.date.localeCompare(b.date));
    case "name":
      return list.sort((a, b) => a.name.localeCompare(b.name, undefined, { sensitivity: "base" }));
    case "stats":
      return list.sort((a, b) => total(b) - total(a) || b.date.localeCompare(a.date));
  }
  return list;
});

// The desktop application opens a run's directory in the file manager.
async function reveal(run: Run) {
  try {
    await api.reveal(run.report && run.report.origin === "job" ? { report: run.report.id } : { job: run.job!.id });
  } catch (e) {
    error.value = e instanceof ApiError ? e.message : (e as Error).message;
  }
}

async function load() {
  try {
    // A failed recordings request keeps the list already shown: an empty
    // list would say "nothing here" about recordings that are there.
    const [r, j, u] = await Promise.all([api.reports(), api.jobs(), api.recordings().catch(() => null)]);
    reports.value = r;
    jobs.value = j;
    if (u) recordings.value = u;
    loaded.value = u !== null || loaded.value;
    error.value = u === null && !loaded.value ? "The list of recordings could not be loaded; trying again." : "";
    for (const report of r) loadFacts(report);
  } catch (e) {
    error.value = (e as Error).message;
  }
}

async function analyze(sourceId: string) {
  busy.value = sourceId;
  try {
    const job = await api.submit(sourceId);
    window.location.hash = `#/jobs/${encodeURIComponent(job.id)}`;
  } catch (e) {
    error.value = e instanceof ApiError ? e.message : (e as Error).message;
  } finally {
    busy.value = "";
  }
}

// Deleting a run removes the recording and every report made from it; a
// run that is only a report (its recording already gone) removes the report.
// The question is a dialog of the page's own (ConfirmDialog): the desktop
// application's web view on a Mac never shows the browser's confirm().
const deleting = ref<Run | null>(null);
const deleteBusy = ref(false);
const deleteQuestion = computed(() => {
  const run = deleting.value;
  if (!run) return { title: "", message: "" };
  const reports = run.report ? [run.report, ...run.earlier] : [];
  return run.recording
    ? { title: `Delete ${run.name}?`, message: `Removes the recording${reports.length ? ` and ${reports.length === 1 ? "its report" : `its ${reports.length} reports`}` : ""}. This cannot be undone.` }
    : { title: `Delete the report for ${run.name}?`, message: "Removes the report. Getting it back means analyzing the recording again." };
});

function remove(run: Run) {
  deleting.value = run;
}

async function confirmRemove() {
  const run = deleting.value;
  if (!run || deleteBusy.value) return;
  deleteBusy.value = true;
  try {
    for (const r of run.report ? [run.report, ...run.earlier] : []) await api.deleteReport(r.id);
    if (run.recording) await api.deleteRecording(run.recording.id);
    await load();
  } catch (e) {
    error.value = e instanceof ApiError ? e.message : (e as Error).message;
  } finally {
    deleteBusy.value = false;
    deleting.value = null;
  }
}

async function cancel(id: string) {
  try {
    await api.cancel(id);
    await load();
  } catch (e) {
    error.value = (e as Error).message;
  }
}

// Pause keeps what the analysis has done. Resume queues it again, and it
// continues from there.
async function move(id: string, to: "pause" | "resume") {
  busy.value = id;
  try {
    await api[to](id);
  } catch (e) {
    error.value = e instanceof ApiError ? e.message : (e as Error).message;
  } finally {
    busy.value = "";
    await load();
  }
}

onMounted(() => {
  load();
  timer = window.setInterval(load, 5000);
  if (!hosted) {
    api.ready().then((r) => { device.value = r.checks.find((c) => c.name === "ocr-device")?.note ?? ""; }).catch(() => {});
    // An installed application is told of a newer release. The service
    // first looks a little after it starts, so the page asks again rather
    // than once. The hosted site is never asked: it updates itself and its
    // visitors install nothing.
    checkVersion();
    versionTimer = window.setInterval(checkVersion, 30000);
  }
});
onUnmounted(() => {
  window.clearInterval(timer);
  window.clearInterval(versionTimer);
});

let versionTimer: number | undefined;
function checkVersion() {
  api.version()
    .then((v) => (update.value = v.latest && v.url ? { current: v.version, latest: v.latest, url: v.url } : null))
    .catch(() => {});
}

// What a run is waiting on. A run whose report is ready says nothing here:
// the Open button is the state.
function status(run: Run): { text: string; cls: string } | null {
  if (run.job && active(run.job)) {
    // The same figure the job page shows: progress through the whole
    // analysis, of which OCR is the first stage.
    const ocr = run.job.ocr_total && run.job.stage === "ocr" ? (100 * (run.job.ocr_processed ?? 0)) / run.job.ocr_total : null;
    const pct = run.job.status === "running" ? ` ${Math.round(progressView(run.job, ocr).overall)}%` : "";
    return { text: run.job.status === "queued" ? "Queued" : `Analyzing${pct}`, cls: "warn" };
  }
  if (paused(run.job)) return { text: run.job!.error?.code === "interrupted" ? "Paused by a Restart" : "Paused", cls: "" };
  if (run.report) return run.madeBy?.status === "completed_with_stage_failures" ? { text: "Some Stages Were Skipped", cls: "warn" } : null;
  if (run.job?.status === "failed") return { text: "Analysis Failed", cls: "bad" };
  if (run.job?.status === "cancelled") return { text: "Analysis Cancelled", cls: "" };
  return { text: "Not Analyzed Yet", cls: "" };
}

function meta(run: Run): string[] {
  const parts: string[] = [];
  if (run.date) parts.push(`${run.analyzed ? "Analyzed" : "Uploaded"} ${when(run.date)}`);
  if (run.report) parts.push(clock(run.report.duration_ms), `${run.report.turns} turns`, `${run.report.entries} entries`);
  if (run.size !== null) parts.push(bytes(run.size));
  if (run.job && active(run.job) && run.job.started_at) parts.push(`running ${runTime(run.job)}`);
  if (paused(run.job) && run.job!.paused_until) parts.push(`progress kept until ${when(run.job!.paused_until)}`);
  if (run.recording?.original_until) parts.push(expired(run) ? "original no longer kept" : `can be analyzed again until ${when(run.recording.original_until)}`);
  return parts;
}

/** Whether the recording's original is past the time it is kept for, so another analysis needs a new upload. */
function expired(run: Run): boolean {
  const until = run.recording?.original_until;
  return !!until && new Date(until).getTime() < Date.now();
}

function hideBroken(e: Event) {
  (e.target as HTMLImageElement).style.visibility = "hidden";
}
</script>

<template>
  <div class="page-head">
    <div>
      <h1>Runs</h1>
      <p>Upload a career recording and open its report when the analysis is done. {{ hosted ? "Analyses on this website run one at a time, so a new one may wait in the queue." : "Queued analyses start as soon as a running one finishes." }}</p>
      <p v-if="device" class="muted small">Analyses run on: {{ device }}.</p>
    </div>
  </div>
  <div v-if="update" class="update-banner" role="status">
    <div>
      <b>Version {{ update.latest }} Is Available</b>
      <span class="muted small">Installed: {{ update.current }}</span>
    </div>
    <a class="btn small primary" :href="update.url" target="_blank" rel="noopener noreferrer" @click="openExternal($event, update.url)">Download</a>
  </div>
  <p v-if="error" class="error">{{ error }}</p>

  <div v-if="reports.length" class="dash">
    <div class="dash-tiles">
      <div class="dash-tile"><b>{{ dashboard.runs }}</b><span>career{{ dashboard.runs === 1 ? "" : "s" }} analyzed · {{ dashboard.turns }} turns</span></div>
      <div class="dash-tile"><b>{{ dashboard.minutes }}<small>min</small></b><span>of recording read{{ dashboard.active ? `, ${dashboard.active} ${dashboard.active === 1 ? "analysis" : "analyses"} queued or running` : "" }}</span></div>
      <div class="dash-tile up"><b>{{ dashboard.pct }}<small>%</small></b><span>of stat changes fully explained</span><div class="bar"><i :style="{ width: dashboard.pct + '%' }"></i></div></div>
      <div class="dash-tile" :class="{ warn: dashboard.toCheck }"><b>{{ dashboard.toCheck }}</b><a href="#/check" class="dash-link">turn{{ dashboard.toCheck === 1 ? "" : "s" }} waiting for your review&nbsp;›</a></div>
      <div class="dash-tile"><b>{{ bytes(dashboard.stored) }}</b><span>in {{ dashboard.recordings }} recording{{ dashboard.recordings === 1 ? "" : "s" }}{{ hosted ? " on the service" : " on this computer" }}</span></div>
    </div>
  </div>
  <div v-else class="dash-empty"><b>Dashboard.</b> Appears after the first report, with each run's final stats and ranks, how much of each career the report explains, and the turns left to check.</div>

  <UploadBox @uploaded="load" />

  <p v-if="loaded && !runs.length" class="muted" style="margin-top: 28px">Nothing here yet. Your first upload appears in this list.</p>
  <template v-else>
    <div v-if="runs.length > 1" class="run-tools">
      <input v-model="query" type="search" placeholder="Search runs by name" aria-label="Search runs by name" />
      <select v-model="order" aria-label="Order">
        <option value="newest">Newest First</option>
        <option value="oldest">Oldest First</option>
        <option value="name">By Name</option>
        <option value="stats">Highest Stats First</option>
      </select>
      <span v-if="query" class="count">{{ shown.length }} of {{ runs.length }}</span>
    </div>
    <p v-if="query && !shown.length" class="muted" style="margin-top: 16px">No run is named that.</p>
  <ul class="runs">
    <li v-for="run in shown" :key="run.key" class="run">
      <a class="run-thumb" :href="run.report ? `#/reports/${encodeURIComponent(run.report.id)}` : run.job ? `#/jobs/${encodeURIComponent(run.job.id)}` : undefined">
        <img v-if="run.thumb" :src="run.thumb" :crossorigin="crossOrigin" alt="" loading="lazy" @error="hideBroken" />
      </a>
      <div class="run-body">
        <div class="run-name">
          <a v-if="run.report" :href="`#/reports/${encodeURIComponent(run.report.id)}`">{{ run.name }}</a>
          <span v-else>{{ run.name }}</span>
          <span v-if="run.report?.origin === 'imported'" class="tag grey">Imported</span>
        </div>
        <div class="run-meta">{{ meta(run).join(" · ") }}</div>
        <div v-if="run.report && facts[run.report.id]?.final" class="run-final">
          <span v-for="s in STATS" :key="s" class="rf">
            <RankBadge v-if="s !== 'skill_points'" :value="facts[run.report.id].final![s]" small />
            <b :title="s === 'skill_points' && facts[run.report.id].earned !== null ? `${facts[run.report.id].final![s] ?? '?'} left at the end` : undefined">{{ s === "skill_points" && facts[run.report.id].earned !== null ? facts[run.report.id].earned : (facts[run.report.id].final![s] ?? "?") }}</b>
            <small>{{ s === "skill_points" && facts[run.report.id].earned !== null ? "Total Skill Pts" : STAT_NAMES[s] }}</small>
          </span>
          <span class="rf total"><b>{{ statTotal(facts[run.report.id]) }}</b><small>total</small></span>
        </div>
        <a v-if="run.report && facts[run.report.id]?.toCheck" class="run-review" :href="`#/reports/${encodeURIComponent(run.report.id)}`">{{ facts[run.report.id].toCheck }} turn{{ facts[run.report.id].toCheck === 1 ? "" : "s" }} to review</a>
        <div v-if="run.earlier.length" class="run-earlier">
          <button class="linkish" @click="opened[run.key] = !opened[run.key]">{{ opened[run.key] ? "Hide" : "Show" }} {{ run.earlier.length }} earlier {{ run.earlier.length === 1 ? "analysis" : "analyses" }}</button>
          <ul v-if="opened[run.key]">
            <li v-for="r in run.earlier" :key="r.id">
              <a :href="`#/reports/${encodeURIComponent(r.id)}`">Analyzed {{ when(r.created_at) }}</a>
              <span class="muted"> · {{ r.turns }} turns · {{ r.entries }} entries<template v-if="facts[r.id]?.toCheck"> · {{ facts[r.id].toCheck }} to review</template></span>
            </li>
          </ul>
        </div>
      </div>
      <div class="run-side">
        <span v-if="status(run)" class="run-state" :class="[status(run)!.cls, { live: run.job && active(run.job) }]">{{ status(run)!.text }}</span>
        <div class="run-actions">
          <a v-if="run.report" class="btn small primary" :href="`#/reports/${encodeURIComponent(run.report.id)}`">Open</a>
          <template v-if="run.job && active(run.job)">
            <a class="btn small" :href="`#/jobs/${encodeURIComponent(run.job.id)}`">Progress</a>
            <button class="btn small" :disabled="busy === run.job.id || run.job.pause_requested" @click="move(run.job.id, 'pause')">{{ run.job.pause_requested ? "Pausing" : "Pause" }}</button>
            <button class="btn small" @click="cancel(run.job.id)">Cancel</button>
          </template>
          <template v-else-if="run.job && paused(run.job)">
            <button class="btn small primary" :disabled="busy === run.job.id" @click="move(run.job.id, 'resume')">Resume</button>
            <a class="btn small" :href="`#/jobs/${encodeURIComponent(run.job.id)}`">Progress</a>
            <button class="btn small" @click="cancel(run.job.id)">Cancel</button>
          </template>
          <template v-else>
            <button v-if="!run.report && run.sourceId" class="btn small primary" :disabled="busy === run.sourceId || expired(run)" @click="analyze(run.sourceId)">Analyze</button>
            <a v-if="run.job && !run.report" class="btn small" :href="`#/jobs/${encodeURIComponent(run.job.id)}`">Details</a>
            <button v-if="run.report && run.sourceId" class="btn small" :disabled="busy === run.sourceId || expired(run)" :title="expired(run) ? 'The original is no longer kept. Upload it again to analyze it.' : ''" @click="analyze(run.sourceId)">Analyze Again</button>
            <button v-if="desktop && (run.job || run.report?.origin === 'job')" class="btn small" title="Open the run's folder" @click="reveal(run)">Folder</button>
            <button v-if="run.recording || run.report" class="btn small danger" @click="remove(run)">Delete</button>
          </template>
        </div>
      </div>
    </li>
  </ul>
  </template>
  <ConfirmDialog v-if="deleting" :title="deleteQuestion.title" :message="deleteQuestion.message" confirm-label="Delete" danger :busy="deleteBusy" @confirm="confirmRemove" @cancel="deleting = null" />
</template>
