<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref } from "vue";
import { api, ApiError, type Job, type Recording, type Report, type Source } from "../api";
import { bytes, clock, elapsed, when } from "../format";
import UploadBox from "../components/UploadBox.vue";

// One list. A run is a recording, the analysis made from it and the report
// that came out, shown as one row from upload to result.
const reports = ref<Report[]>([]);
const jobs = ref<Job[]>([]);
const recordings = ref<Recording[]>([]);
const sources = ref<Source[]>([]);
const error = ref("");
const busy = ref("");
let timer: number | undefined;

async function load() {
  try {
    const [r, j, u, s] = await Promise.all([api.reports(), api.jobs(), api.recordings().catch(() => []), api.sources().catch(() => [])]);
    reports.value = r;
    jobs.value = j;
    recordings.value = u;
    sources.value = s;
    error.value = "";
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
  kind: "upload" | "shared" | "imported";
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
  for (const s of sources.value) {
    const job = latestJob(s.id);
    const report = reportOf(job);
    if (report) used.add(report.id);
    out.push({ key: "s:" + s.id, kind: "shared", name: s.name, date: job?.created_at ?? "", size: s.size, sourceId: s.id, recording: null, job, report, thumb: report ? api.frameUrl(report.id, 30000) : "" });
  }
  for (const r of reports.value) {
    if (used.has(r.id)) continue;
    out.push({ key: "r:" + r.id, kind: "imported", name: r.source_name, date: r.created_at, size: null, sourceId: "", recording: null, job: null, report: r, thumb: api.frameUrl(r.id, 30000) });
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
  return { text: run.kind === "shared" ? "In the shared folder" : "Uploaded", cls: "" };
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
          <span v-if="run.kind === 'shared'" class="tag grey">shared folder</span>
          <span v-else-if="run.kind === 'imported'" class="tag grey">imported</span>
        </div>
        <div class="muted small">{{ meta(run).join(" · ") }}</div>
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
