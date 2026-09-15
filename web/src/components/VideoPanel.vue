<script setup lang="ts">
import { computed, ref, watch } from "vue";
import { api } from "../api";
import { clockMs } from "../format";

// Shows the recording at a requested source timestamp and reports where the
// viewer takes it. With the recording on this machine it is a normal video
// player (scrub, play, pause, volume, full screen) plus one-frame steps;
// otherwise it falls back to the analyzer's extracted frame, which is the
// same image the readings came from.
const props = defineProps<{ reportId: string; ms: number | null; available: boolean; durationMs: number }>();
const emit = defineEmits<{ time: [ms: number] }>();
const video = ref<HTMLVideoElement | null>(null);
const shown = ref<number | null>(null);
const ready = ref(false);
const failed = ref("");
const FRAME_MS = 1000 / 60;

const src = computed(() => api.videoUrl(props.reportId));

function seekTo(ms: number) {
  shown.value = ms;
  const el = video.value;
  if (!el || !props.available) return;
  if (ready.value && Math.abs(el.currentTime * 1000 - ms) > 40) el.currentTime = ms / 1000;
}

watch(
  () => props.ms,
  (ms) => {
    if (ms !== null) seekTo(ms);
  },
  { immediate: true },
);

function onReady() {
  ready.value = true;
  if (shown.value !== null && video.value) video.value.currentTime = shown.value / 1000;
}

// The viewer's position, whether from playing or from the scrub bar.
function onTime() {
  const el = video.value;
  if (!el) return;
  const ms = Math.round(el.currentTime * 1000);
  if (ms === shown.value) return;
  shown.value = ms;
  emit("time", ms);
}

function onError() {
  failed.value = "the recording could not be played in this browser";
}

function nudge(delta: number) {
  const next = Math.round(Math.max(0, Math.min(props.durationMs, (shown.value ?? 0) + delta)));
  video.value?.pause();
  seekTo(next);
  emit("time", next);
}
</script>

<template>
  <div class="video-panel">
    <div class="video">
      <template v-if="available && !failed">
        <video ref="video" :src="src" preload="metadata" playsinline controls @loadedmetadata="onReady" @timeupdate="onTime" @seeked="onTime" @error="onError"></video>
      </template>
      <img v-else-if="shown !== null" :src="api.frameUrl(reportId, shown)" :alt="`frame at ${clockMs(shown)}`" />
      <p v-else class="small" style="padding: 24px; text-align: center">Pick a turn or an entry to see that moment.</p>
    </div>
    <div class="video-meta">
      <span class="tabular display" style="font-size: 18px">{{ clockMs(shown) }}</span>
      <span class="video-nudge">
        <button class="btn small" :disabled="shown === null" title="back one second" @click="nudge(-1000)">−1 s</button>
        <button class="btn small" :disabled="shown === null" title="back one frame" @click="nudge(-FRAME_MS)">−1 f</button>
        <button class="btn small" :disabled="shown === null" title="forward one frame" @click="nudge(FRAME_MS)">+1 f</button>
        <button class="btn small" :disabled="shown === null" title="forward one second" @click="nudge(1000)">+1 s</button>
      </span>
    </div>
    <p v-if="failed" class="error small">{{ failed }}. Showing extracted frames instead.</p>
    <p v-else-if="!available" class="muted small">This report's recording is not on this machine, so the panel shows the analyzer's extracted frame at each moment.</p>
    <p v-else class="muted small">Scrub or play the recording and the turn on the right follows it. Picking a turn or a log entry seeks the recording there.</p>
  </div>
</template>
