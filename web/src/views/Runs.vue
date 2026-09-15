<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref } from "vue";
import { api, ApiError, type Job, type Recording, type Report } from "../api";
import { bytes, clock, elapsed, when } from "../format";
import { turnWarnings } from "../warnings";
import RankBadge from "../components/RankBadge.vue";
import StatBar from "../components/StatBar.vue";
import UploadBox from "../components/UploadBox.vue";

// One list. A run is a recording, the analysis made from it and the report
// that came out, shown as one row from upload to result.
const reports = ref<Report[]>([]);
const jobs = ref<Job[]>([]);
const recordings = ref<Recording[]>([]);
const error = ref("");
const busy = ref("");
let timer: number | undefined;

// What each finished report says about its run: the stats at the end, how
// much of the career's stat changes the report explains, and how many turns
// ask for a look. Read once per report and kept.
interface RunFacts {
  final: Record<string, number | null> | null;
  explained: number;
  total: number;
  toCheck: number;
}
const STATS = ["speed", "stamina", "power", "guts", "wit", "skill_points"] as const;
const facts = ref<Record<string, RunFacts>>({});
const loadingFacts = new Set<string>();
async function loadFacts(report: Report) {
  if (facts.value[report.id] || loadingFacts.has(report.id)) return;
  loadingFacts.add(report.id);
  try {
    const [summary, turns] = await Promise.all([api.summary(report.id), api.turns(report.id)]);
    const counts = summary.summary.field_status_counts ?? {};
    const total = Object.values(counts).reduce((a, b) => a + b, 0);
    const explained = (counts.balanced_observations ?? 0) + (counts.balanced_with_derived_changes ?? 0);
    const toCheck = turns.filter((t) => turnWarnings(t).some((w) => w.serious) || (t.differences ?? []).some((d) => !d.worked_out)).length;
    let final: Record<string, number | null> | null = null;
    const last = [...turns].reverse().find((t) => t.opening.stats);
    if (last) {
      final = { ...(last.opening.stats as Record<string, number | null>) };
      try {
        const detail = await api.turn(report.id, last.id);
        for (const field of STATS) {
          const after = detail.turn.accounting.stats?.[field]?.after;
          if (after !== null && after !== undefined) final[field] = after;
        }
      } catch {
        // the opening of the last observed turn stands
      }
    }
    facts.value = { ...facts.value, [report.id]: { final, explained, total, toCheck } };
  } catch {
    // a report that cannot be summarized simply has no facts on the dashboard
  } finally {
    loadingFacts.delete(report.id);
  }
}
const statTotal = (f: RunFacts) => (f.final ? STATS.filter((s) => s !== "skill_points").reduce((a, s) => a + (f.final![s] ?? 0), 0) : 0);
const dashboard = computed(() => {
  const done = reports.value.filter((r) => facts.value[r.id]);
  const explained = done.reduce((a, r) => a + facts.value[r.id].explained, 0);
  const total = done.reduce((a, r) => a + facts.value[r.id].total, 0);
  const toCheck = done.reduce((a, r) => a + facts.value[r.id].toCheck, 0);
  const turns = reports.value.reduce((a, r) => a + r.turns, 0);
  const minutes = Math.round(reports.value.reduce((a, r) => a + r.duration_ms, 0) / 60000);
  const best = done.map((r) => ({ report: r, facts: facts.value[r.id] })).filter((x) => x.facts.final).sort((a, b) => statTotal(b.facts) - statTotal(a.facts))[0] ?? null;
  return { runs: reports.value.length, turns, minutes, explained, total, pct: total ? Math.round((100 * explained) / total) : 0, toCheck, best, active: jobs.value.filter(active).length };
});

async function load() {
  try {
    const [r, j, u] = await Promise.all([api.reports(), api.jobs(), api.recordings().catch(() => [])]);
    reports.value = r;
    jobs.value = j;
    recordings.value = u;
    error.value = "";
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

async function remove(recording: Recording) {
  if (!window.confirm(`Delete ${recording.name}? Reports made from it stay.`)) return;
  try {
    await api.deleteRecording(recording.id);
    await load();
  } catch (e) {
    error.value = e instanceof ApiError ? e.message : (e as Error).message;
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

onMounted(() => {
  load();
  timer = window.setInterval(load, 5000);
});
onUnmounted(() => window.clearInterval(timer));

interface Run {
  key: string;
  kind: "upload" | "report";
  name: string;
  date: string;
  size: number | null;
  sourceId: string;
  recording: Recording | null;
  job: Job | null;
  report: Report | null;
  thumb: string;
}

const active = (j: Job) => j.status === "queued" || j.status === "running";

const runs = computed<Run[]>(() => {
  const out: Run[] = [];
  const used = new Set<string>();
  const latestJob = (sourceId: string) => jobs.value.filter((j) => j.source_id === sourceId).sort((a, b) => b.created_at.localeCompare(a.created_at))[0] ?? null;
  const reportOf = (job: Job | null) => (job?.report_id ? reports.value.find((r) => r.id === job.report_id) ?? null : null);
  for (const u of recordings.value) {
    const job = latestJob(u.id);
    const report = reportOf(job);
    if (report) used.add(report.id);
    out.push({ key: "u:" + u.id, kind: "upload", name: u.name, date: u.created_at, size: u.size, sourceId: u.id, recording: u, job, report, thumb: report ? api.frameUrl(report.id, 30000) : api.recordingFrameUrl(u.id, 30000) });
  }
  for (const r of reports.value) {
    if (used.has(r.id)) continue;
    out.push({ key: "r:" + r.id, kind: "report", name: r.source_name, date: r.created_at, size: null, sourceId: "", recording: null, job: null, report: r, thumb: api.frameUrl(r.id, 30000) });
  }
  return out.sort((a, b) => b.date.localeCompare(a.date));
});

function status(run: Run): { text: string; cls: string } {
  if (run.job && active(run.job)) {
    const pct = run.job.ocr_total && run.job.stage === "ocr" ? ` ${Math.round((100 * (run.job.ocr_processed ?? 0)) / run.job.ocr_total)}%` : "";
    return { text: run.job.status === "queued" ? "Queued" : `Analyzing${pct}`, cls: "warn" };
  }
  if (run.report) return { text: run.job?.status === "completed_with_stage_failures" ? "Report ready, stages skipped" : "Report ready", cls: "ok" };
  if (run.job?.status === "failed") return { text: "Analysis failed", cls: "bad" };
  if (run.job?.status === "interrupted") return { text: "Analysis interrupted", cls: "bad" };
  if (run.job?.status === "cancelled") return { text: "Analysis cancelled", cls: "" };
  return { text: "Uploaded", cls: "" };
}

function meta(run: Run): string[] {
  const parts: string[] = [];
  if (run.date) parts.push(when(run.date));
  if (run.size !== null) parts.push(bytes(run.size));
  if (run.report) parts.push(clock(run.report.duration_ms), `${run.report.turns} turns`, `${run.report.entries} entries`);
  if (run.job && active(run.job) && run.job.started_at) parts.push(`running ${elapsed(run.job.started_at)}`);
  return parts;
}

function hideBroken(e: Event) {
  (e.target as HTMLImageElement).style.visibility = "hidden";
}
</script>

<template>
  <div class="page-head">
    <div>
      <h1>Your Runs</h1>
      <p>Upload a career recording, analyze it, open the report. One analysis runs at a time; a full career takes about 45 minutes.</p>
    </div>
  </div>
  <p v-if="error" class="error">{{ error }}</p>

  <div v-if="reports.length" class="dash">
    <div class="dash-tiles">
      <div class="dash-tile"><b>{{ dashboard.runs }}</b><span>career{{ dashboard.runs === 1 ? "" : "s" }} analyzed · {{ dashboard.turns }} turns</span></div>
      <div class="dash-tile"><b>{{ dashboard.minutes }}<small>min</small></b><span>of recording read{{ dashboard.active ? `, ${dashboard.active} ${dashboard.active === 1 ? "analysis" : "analyses"} queued or running` : "" }}</span></div>
      <div class="dash-tile up"><b>{{ dashboard.pct }}<small>%</small></b><span>of stat changes fully explained</span><div class="bar"><i :style="{ width: dashboard.pct + '%' }"></i></div></div>
      <div class="dash-tile" :class="{ warn: dashboard.toCheck }"><b>{{ dashboard.toCheck }}</b><span>turn{{ dashboard.toCheck === 1 ? "" : "s" }} waiting for your review</span></div>
    </div>
    <div v-if="dashboard.best" class="best">
      <div class="best-head">
        <span class="overline" style="margin: 0">Best run</span>
        <a class="best-name" :href="`#/reports/${encodeURIComponent(dashboard.best.report.id)}`" :title="dashboard.best.report.source_name">{{ dashboard.best.report.source_name }}</a>
      </div>
      <StatBar :stats="dashboard.best.facts.final" compact />
      <div class="best-foot">{{ statTotal(dashboard.best.facts) }} across the five stats at the end of the run · {{ dashboard.best.report.turns }} turns · {{ clock(dashboard.best.report.duration_ms) }}</div>
    </div>
    <div v-else class="best"><span class="overline" style="margin: 0">Best run</span><span class="muted small">Reading the reports…</span></div>
  </div>
  <div v-else class="dash-empty"><b>Your dashboard fills in with your first report:</b> the stats at the end of each run with their rank letters, how much of the career the report explains, and what still needs a look.</div>

  <UploadBox @uploaded="load" />

  <p v-if="!runs.length" class="muted" style="margin-top: 28px">Nothing here yet. Your first upload appears in this list.</p>
  <ul v-else class="runs">
    <li v-for="run in runs" :key="run.key" class="run">
      <a class="run-thumb" :href="run.report ? `#/reports/${encodeURIComponent(run.report.id)}` : run.job ? `#/jobs/${encodeURIComponent(run.job.id)}` : undefined">
        <img v-if="run.thumb" :src="run.thumb" alt="" loading="lazy" @error="hideBroken" />
      </a>
      <div class="run-body">
        <div class="run-name">
          <a v-if="run.report" :href="`#/reports/${encodeURIComponent(run.report.id)}`">{{ run.name }}</a>
          <span v-else>{{ run.name }}</span>
          <span v-if="run.report?.origin === 'imported'" class="tag grey">imported</span>
        </div>
        <div class="muted small">{{ meta(run).join(" · ") }}</div>
        <div v-if="run.report && facts[run.report.id]?.final" class="run-ranks">
          <span v-for="s in STATS" :key="s" class="rr"><i :class="s"></i><RankBadge v-if="s !== 'skill_points'" :value="facts[run.report.id].final![s]" small />{{ facts[run.report.id].final![s] ?? "?" }}</span>
          <span class="rr total">Σ {{ statTotal(facts[run.report.id]) }}</span>
          <span v-if="facts[run.report.id].toCheck" class="rr" style="color: var(--warn-ink)">{{ facts[run.report.id].toCheck }} to review</span>
        </div>
      </div>
      <div class="run-status"><span class="pill" :class="[status(run).cls, { live: run.job && active(run.job) }]">{{ status(run).text }}</span></div>
      <div class="run-actions">
        <template v-if="run.job && active(run.job)">
          <a class="btn small" :href="`#/jobs/${encodeURIComponent(run.job.id)}`">Progress</a>
          <button class="btn small" @click="cancel(run.job.id)">Cancel</button>
        </template>
        <template v-else>
          <a v-if="run.report" class="btn small primary" :href="`#/reports/${encodeURIComponent(run.report.id)}`">Open</a>
          <button v-else-if="run.sourceId" class="btn small primary" :disabled="busy === run.sourceId" @click="analyze(run.sourceId)">Analyze</button>
          <a v-if="run.job && !run.report" class="btn small" :href="`#/jobs/${encodeURIComponent(run.job.id)}`">Details</a>
          <button v-if="run.report && run.sourceId" class="btn small quiet" :disabled="busy === run.sourceId" title="analyze this recording again" @click="analyze(run.sourceId)">Re-analyze</button>
          <button v-if="run.recording" class="btn small quiet danger" @click="remove(run.recording)">Delete</button>
        </template>
      </div>
    </li>
  </ul>
</template>
