<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref } from "vue";
import { api, type Job } from "../api";
import { elapsed, statusClass, when } from "../format";

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
    if (typeof p.percent === "number") percent.value = p.percent;
    else if (p.stage && p.stage !== "ocr") percent.value = null;
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

const stageText: Record<string, string> = {
  probe: "Checking the recording",
  frames: "Sampling frames",
  ocr: "Reading the screen",
  analysis: "Building the ledger",
  timeline: "Writing the report",
};

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
        <div class="overline">Analysis</div>
        <h1 style="font-size: 30px">{{ job.source_name }}</h1>
        <p class="muted">Queued {{ when(job.created_at) }}<span v-if="job.started_at"> · running {{ running }}</span></p>
      </div>
      <span :class="['pill', statusClass(job.status), { live: !terminal }]" style="font-size: 13px">{{ job.status.replaceAll("_", " ") }}</span>
    </div>

    <div class="card" style="max-width: 720px">
      <template v-if="!terminal">
        <p class="display" style="font-size: 22px">{{ (job.stage && stageText[job.stage]) ?? (job.stage ? job.stage : "Waiting for a free worker") }}</p>
        <div class="bar" style="margin: 10px 0"><i :style="{ width: (percent ?? (job.status === 'running' ? 4 : 0)) + '%' }"></i></div>
        <p class="muted small">
          <span v-if="job.ocr_total">{{ job.ocr_processed }} of {{ job.ocr_total }} frames read.</span>
          <span v-else>Only the reading pass reports a percentage; the other stages show their name and the time elapsed.</span>
          A full career takes about 45 minutes on this machine. You can leave this page; the analysis keeps running.
        </p>
        <p style="margin-top: 16px"><button class="btn" @click="cancel">Cancel analysis</button></p>
      </template>
      <template v-else>
        <p v-if="job.error" class="error">{{ job.error.message }} <span class="muted small">({{ job.error.code }})</span></p>
        <ul v-if="job.stage_failures?.length" class="small">
          <li v-for="f in job.stage_failures" :key="f.stage"><strong>{{ f.stage }}</strong> skipped: {{ f.error }}</li>
        </ul>
        <p v-if="job.status === 'completed_with_stage_failures'" class="muted small">The stages above were skipped; the report is complete for the stages that ran.</p>
        <p v-if="job.status === 'interrupted'" class="muted small">The service restarted while this analysis was running. Queue it again to retry.</p>
        <div class="row" style="margin-top: 8px">
          <a v-if="job.report_id" class="btn primary" :href="`#/reports/${encodeURIComponent(job.report_id)}`">Open the report</a>
          <a class="btn" :href="api.logUrl(job.id)" target="_blank" rel="noopener">Worker log</a>
          <a class="btn quiet" href="#/runs">Back to runs</a>
        </div>
      </template>
    </div>
  </template>
</template>
