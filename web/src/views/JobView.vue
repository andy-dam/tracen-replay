<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref } from "vue";
import { api, type Job } from "../api";
import { elapsed, statusClass, when } from "../format";
import { progressView } from "../phases";

const props = defineProps<{ jobId: string }>();
const job = ref<Job | null>(null);
const percent = ref<number | null>(null);
const error = ref("");
const now = ref(Date.now());
let stream: EventSource | undefined;
let tick: number | undefined;

function apply(j: Job) {
  job.value = j;
  if (j.ocr_total && j.stage === "ocr") percent.value = (100 * (j.ocr_processed ?? 0)) / j.ocr_total;
}

function listen() {
  stream?.close();
  stream = api.events(props.jobId);
  stream.addEventListener("job", (e) => apply(JSON.parse((e as MessageEvent).data)));
  stream.addEventListener("progress", (e) => {
    const p = JSON.parse((e as MessageEvent).data);
    if (job.value) job.value = { ...job.value, status: p.status, stage: p.stage ?? job.value.stage };
    if (typeof p.percent === "number") {
      percent.value = p.percent;
      // The event carries the share done; the frame count the text shows
      // follows it, so the words move with the bar.
      if (job.value?.ocr_total) job.value = { ...job.value, ocr_processed: Math.round((p.percent / 100) * job.value.ocr_total) };
    } else if (p.stage && p.stage !== "ocr") percent.value = null;
  });
  stream.onerror = () => {
    // The stream closes after the terminal event; refresh the record once.
    stream?.close();
    api.job(props.jobId).then(apply).catch((e) => (error.value = (e as Error).message));
  };
}

onMounted(async () => {
  try {
    apply(await api.job(props.jobId));
    if (job.value && !terminal.value) listen();
  } catch (e) {
    error.value = (e as Error).message;
  }
  tick = window.setInterval(() => (now.value = Date.now()), 1000);
});
onUnmounted(() => {
  stream?.close();
  window.clearInterval(tick);
});

const terminal = computed(() => !!job.value && !["queued", "running"].includes(job.value.status));
const running = computed(() => (now.value ? elapsed(job.value?.started_at, job.value?.finished_at) : ""));

// The five phases of an analysis and how far each has come; the reading
// phase moves with the frame count, the others with the stages finished.
const view = computed(() => progressView(job.value, percent.value));
// A rough time left, from how long the finished share took; only once the
// analysis is far enough in for the share to mean something.
const remaining = computed(() => {
  const j = job.value;
  if (!j?.started_at || j.status !== "running" || view.value.overall < 8) return "";
  const elapsedMs = now.value - new Date(j.started_at).getTime();
  const left = (elapsedMs * (100 - view.value.overall)) / view.value.overall;
  const minutes = Math.max(1, Math.round(left / 60000));
  return `roughly ${minutes} min left`;
});

async function cancel() {
  try {
    apply(await api.cancel(props.jobId));
  } catch (e) {
    error.value = (e as Error).message;
  }
}
</script>

<template>
  <p v-if="error" class="error">{{ error }}</p>
  <template v-if="job">
    <div class="page-head">
      <div>
        <a class="back" href="#/runs">‹ Runs</a>
        <div class="overline" style="margin-top: 8px">Analysis</div>
        <h1 style="font-size: 30px">{{ job.source_name }}</h1>
        <p class="muted">Queued {{ when(job.created_at) }}<span v-if="job.started_at"> · running {{ running }}</span></p>
      </div>
      <span :class="['pill', statusClass(job.status), { live: !terminal }]" style="font-size: 13px">{{ job.status.replaceAll("_", " ") }}</span>
    </div>

    <div class="card" style="max-width: 720px">
      <template v-if="!terminal">
        <div class="row between" style="align-items: baseline">
          <p class="display" style="font-size: 22px; margin: 0">{{ view.current?.label ?? (job.status === "running" ? "Starting" : "Waiting for a Free Worker") }}</p>
          <span class="display num" style="font-size: 22px; color: var(--ink-2)">{{ view.overall }}%</span>
        </div>
        <div class="phases" style="margin: 12px 0 8px" role="progressbar" :aria-valuenow="view.overall" aria-valuemin="0" aria-valuemax="100">
          <div v-for="p in view.phases" :key="p.id" class="phase" :class="p.state" :style="{ flex: p.weight }" :title="`${p.label}: ${p.state === 'done' ? 'done' : p.state === 'active' ? Math.round(p.fill * 100) + '%' : 'not yet'}`">
            <i :style="{ width: Math.round(p.fill * 100) + '%' }"></i>
          </div>
        </div>
        <ol class="phase-legend">
          <li v-for="p in view.phases" :key="p.id" :class="p.state"><i></i>{{ p.label }}</li>
        </ol>
        <p class="muted small" style="margin-top: 10px">
          <template v-if="view.current">Now {{ view.current.doing }}. </template>
          <template v-if="job.stage === 'ocr' && job.ocr_total">{{ job.ocr_processed }} of {{ job.ocr_total }} frames read. </template>
          <template v-else-if="view.lastDone">Last stage finished: {{ view.lastDone }}. </template>
          <template v-if="remaining">{{ remaining[0].toUpperCase() + remaining.slice(1) }}, going by the time so far. </template>
          You can leave this page; the analysis keeps running.
        </p>
        <p style="margin-top: 16px" class="row"><button class="btn" @click="cancel">Cancel Analysis</button><a class="btn quiet" href="#/runs">Back to Runs</a></p>
      </template>
      <template v-else>
        <p v-if="job.error" class="error">{{ job.error.message }} <span class="muted small">({{ job.error.code }})</span></p>
        <ul v-if="job.stage_failures?.length" class="small">
          <li v-for="f in job.stage_failures" :key="f.stage"><strong>{{ f.stage }}</strong> skipped: {{ f.error }}</li>
        </ul>
        <p v-if="job.status === 'completed_with_stage_failures'" class="muted small">The stages above were skipped; the report is complete for the stages that ran.</p>
        <p v-if="job.status === 'interrupted'" class="muted small">The service restarted while this analysis was running. Queue it again to retry.</p>
        <div class="row" style="margin-top: 8px">
          <a v-if="job.report_id" class="btn primary" :href="`#/reports/${encodeURIComponent(job.report_id)}`">Open the Report</a>
          <a class="btn" :href="api.logUrl(job.id)" target="_blank" rel="noopener">Worker Log</a>
          <a class="btn quiet" href="#/runs">Back to Runs</a>
        </div>
      </template>
    </div>
  </template>
</template>
