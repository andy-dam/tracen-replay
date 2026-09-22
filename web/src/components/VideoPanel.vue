<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref, watch } from "vue";
import { api, crossOrigin } from "../api";
import { clockMs } from "../format";

// Shows the recording at a requested source timestamp and reports where the
// viewer takes it. With the recording on this machine it is a player with
// its own control bar: play and pause, a scrub bar, the time, playback
// speed, one-frame and one-second steps, volume and full screen. Otherwise
// it falls back to the analyzer's extracted frame, which is the same image
// the readings came from.
const props = defineProps<{ reportId: string; ms: number | null; available: boolean; durationMs: number }>();
const emit = defineEmits<{ time: [ms: number] }>();
const video = ref<HTMLVideoElement | null>(null);
const panel = ref<HTMLElement | null>(null);
const shown = ref<number | null>(null);
const ready = ref(false);
const failed = ref("");
const playing = ref(false);
const muted = ref(false);
const volume = ref(1);
const fullscreen = ref(false);
const scrubbing = ref(false);
const FRAME_MS = 1000 / 60;
const SPEEDS = [0.25, 0.5, 0.75, 1, 1.25, 1.5, 2, 3];
const speed = ref(1);
try {
  const stored = Number(localStorage.getItem("tracen-speed"));
  if (SPEEDS.includes(stored)) speed.value = stored;
} catch {
  // storage unavailable
}

const src = computed(() => api.videoUrl(props.reportId));
const position = computed(() => (shown.value ?? 0) / Math.max(1, props.durationMs));

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
watch(speed, (s) => {
  if (video.value) video.value.playbackRate = s;
  try {
    localStorage.setItem("tracen-speed", String(s));
  } catch {
    // storage unavailable
  }
});

function onReady() {
  ready.value = true;
  const el = video.value;
  if (!el) return;
  el.playbackRate = speed.value;
  el.volume = volume.value;
  el.muted = muted.value;
  if (shown.value !== null) el.currentTime = shown.value / 1000;
}

// The viewer's position, whether from playing or from the scrub bar.
function onTime() {
  const el = video.value;
  if (!el || scrubbing.value) return;
  const ms = Math.round(el.currentTime * 1000);
  if (ms === shown.value) return;
  shown.value = ms;
  emit("time", ms);
}

function onError() {
  failed.value = "the recording could not be played in this browser";
}

function toggle() {
  const el = video.value;
  if (!el) return;
  if (el.paused) el.play().catch(() => undefined);
  else el.pause();
}

// A step moves the position and leaves the recording playing or paused as
// it was.
function nudge(delta: number) {
  const next = Math.round(Math.max(0, Math.min(props.durationMs, (shown.value ?? 0) + delta)));
  seekTo(next);
  emit("time", next);
}

// The size of a step of the viewer's own: a number of seconds or frames.
const stepText = ref("5");
const stepSize = computed(() => {
  const n = Number(stepText.value.trim());
  return Number.isFinite(n) && n > 0 ? n : 0;
});

// The scrub bar shows the position while dragging and seeks on release.
function onScrub(event: Event) {
  scrubbing.value = true;
  shown.value = Math.round(Number((event.target as HTMLInputElement).value));
}
function onScrubEnd(event: Event) {
  scrubbing.value = false;
  const ms = Math.round(Number((event.target as HTMLInputElement).value));
  seekTo(ms);
  emit("time", ms);
}

// The time readout is also a field: click it, type a time such as 2:38,
// 02:38.500 or 1:02:38, and Enter seeks there. Escape leaves it alone.
const editing = ref(false);
const typed = ref("");
const timeField = ref<HTMLInputElement | null>(null);
function parseClock(text: string): number | null {
  const parts = text.trim().split(":");
  if (!parts.length || parts.length > 3 || parts.some((p) => !/^\d*(\.\d*)?$/.test(p) || p === "")) return null;
  const numbers = parts.map(Number);
  let seconds = 0;
  for (const n of numbers) seconds = seconds * 60 + n;
  if (!Number.isFinite(seconds)) return null;
  return Math.round(Math.max(0, Math.min(props.durationMs, seconds * 1000)));
}
function startEdit() {
  typed.value = clockMs(shown.value ?? 0);
  editing.value = true;
  requestAnimationFrame(() => {
    timeField.value?.focus();
    timeField.value?.select();
  });
}
function commitEdit() {
  const ms = parseClock(typed.value);
  editing.value = false;
  if (ms === null) return;
  seekTo(ms);
  emit("time", ms);
}

function setVolume(event: Event) {
  volume.value = Number((event.target as HTMLInputElement).value);
  muted.value = volume.value === 0;
  if (video.value) {
    video.value.volume = volume.value;
    video.value.muted = muted.value;
  }
}
function toggleMute() {
  muted.value = !muted.value;
  if (video.value) video.value.muted = muted.value;
}

function toggleFullscreen() {
  const el = panel.value;
  if (!el) return;
  if (document.fullscreenElement) document.exitFullscreen().catch(() => undefined);
  else el.requestFullscreen?.().catch(() => undefined);
}
const onFullscreen = () => (fullscreen.value = !!document.fullscreenElement);
onMounted(() => document.addEventListener("fullscreenchange", onFullscreen));
onUnmounted(() => document.removeEventListener("fullscreenchange", onFullscreen));

// Space plays and pauses when nothing else has the keyboard; ← and → stay
// with the report page, which moves between turns.
function onKey(event: KeyboardEvent) {
  const target = event.target as HTMLElement | null;
  if (target && (target.tagName === "INPUT" || target.tagName === "TEXTAREA" || target.tagName === "SELECT" || target.isContentEditable)) return;
  if (event.key === " " && props.available && !failed.value) {
    event.preventDefault();
    toggle();
  }
}
onMounted(() => window.addEventListener("keydown", onKey));
onUnmounted(() => window.removeEventListener("keydown", onKey));
</script>

<template>
  <div ref="panel" class="video-panel" :class="{ fullscreen }">
    <div class="video" @click="available && !failed && toggle()">
      <template v-if="available && !failed">
        <video ref="video" :src="src" :crossorigin="crossOrigin" preload="metadata" playsinline @loadedmetadata="onReady" @timeupdate="onTime" @seeked="onTime" @play="playing = true" @pause="playing = false" @error="onError"></video>
        <button v-if="!playing" class="play-big" type="button" title="play" @click.stop="toggle">▶</button>
      </template>
      <img v-else-if="shown !== null" :src="api.frameUrl(reportId, shown)" :crossorigin="crossOrigin" :alt="`frame at ${clockMs(shown)}`" />
      <p v-else class="small" style="padding: 24px; text-align: center">Pick a turn or an entry to see that moment.</p>
    </div>
    <div v-if="available && !failed" class="player">
      <input class="scrub" type="range" min="0" :max="durationMs" step="1" :value="shown ?? 0" :style="{ '--pos': position * 100 + '%' }" aria-label="position in the recording" @input="onScrub" @change="onScrubEnd" />
      <div class="player-row">
        <button class="pbtn" type="button" :title="playing ? 'pause' : 'play'" @click="toggle">{{ playing ? "❚❚" : "▶" }}</button>
        <span class="ptime tabular">
          <input v-if="editing" ref="timeField" v-model="typed" class="ptime-field" type="text" inputmode="decimal" spellcheck="false" aria-label="go to a time" @keydown.enter.prevent="commitEdit" @keydown.esc.prevent="editing = false" @blur="commitEdit" />
          <button v-else class="ptime-btn" type="button" title="Click to type a time to go to" @click="startEdit">{{ clockMs(shown) }}</button>
          <span class="muted">/ {{ clockMs(durationMs) }}</span>
        </span>
        <span class="spacer"></span>
        <label class="speed" title="playback speed">
          <select v-model.number="speed">
            <option v-for="s in SPEEDS" :key="s" :value="s">{{ s }}×</option>
          </select>
        </label>
        <button class="pbtn" type="button" :title="muted ? 'unmute' : 'mute'" @click="toggleMute">{{ muted || volume === 0 ? "🔇" : volume < 0.5 ? "🔉" : "🔊" }}</button>
        <input class="vol" type="range" min="0" max="1" step="0.05" :value="muted ? 0 : volume" aria-label="volume" @input="setVolume" />
        <button class="pbtn" type="button" :title="fullscreen ? 'exit full screen' : 'full screen'" @click="toggleFullscreen">{{ fullscreen ? "⤡" : "⛶" }}</button>
      </div>
      <div class="player-row steps">
        <span class="steps-label">Step</span>
        <span class="video-nudge">
          <button class="btn small" :disabled="shown === null" title="back one second" @click="nudge(-1000)">−1 s</button>
          <button class="btn small" :disabled="shown === null" title="back one frame" @click="nudge(-FRAME_MS)">−1 f</button>
          <button class="btn small" :disabled="shown === null" title="forward one frame" @click="nudge(FRAME_MS)">+1 f</button>
          <button class="btn small" :disabled="shown === null" title="forward one second" @click="nudge(1000)">+1 s</button>
        </span>
        <span class="video-nudge custom">
          <input v-model="stepText" type="text" inputmode="decimal" aria-label="size of a step, in seconds or frames" title="size of a step, in seconds or frames" />
          <button class="btn small" :disabled="shown === null || !stepSize" :title="`back ${stepSize} seconds`" @click="nudge(-stepSize * 1000)">−s</button>
          <button class="btn small" :disabled="shown === null || !stepSize" :title="`back ${stepSize} frames`" @click="nudge(-stepSize * FRAME_MS)">−f</button>
          <button class="btn small" :disabled="shown === null || !stepSize" :title="`forward ${stepSize} frames`" @click="nudge(stepSize * FRAME_MS)">+f</button>
          <button class="btn small" :disabled="shown === null || !stepSize" :title="`forward ${stepSize} seconds`" @click="nudge(stepSize * 1000)">+s</button>
        </span>
        <span class="muted small steps-hint">Click the time to type where to go.</span>
      </div>
    </div>
    <div v-else class="video-meta">
      <span class="tabular display" style="font-size: 18px">{{ clockMs(shown) }}</span>
      <span class="video-nudge">
        <button class="btn small" :disabled="shown === null" title="back one second" @click="nudge(-1000)">−1 s</button>
        <button class="btn small" :disabled="shown === null" title="back one frame" @click="nudge(-FRAME_MS)">−1 f</button>
        <button class="btn small" :disabled="shown === null" title="forward one frame" @click="nudge(FRAME_MS)">+1 f</button>
        <button class="btn small" :disabled="shown === null" title="forward one second" @click="nudge(1000)">+1 s</button>
      </span>
      <span class="video-nudge custom">
        <input v-model="stepText" type="text" inputmode="decimal" aria-label="size of a step, in seconds or frames" title="size of a step, in seconds or frames" />
        <button class="btn small" :disabled="shown === null || !stepSize" :title="`back ${stepSize} seconds`" @click="nudge(-stepSize * 1000)">−s</button>
        <button class="btn small" :disabled="shown === null || !stepSize" :title="`back ${stepSize} frames`" @click="nudge(-stepSize * FRAME_MS)">−f</button>
        <button class="btn small" :disabled="shown === null || !stepSize" :title="`forward ${stepSize} frames`" @click="nudge(stepSize * FRAME_MS)">+f</button>
        <button class="btn small" :disabled="shown === null || !stepSize" :title="`forward ${stepSize} seconds`" @click="nudge(stepSize * 1000)">+s</button>
      </span>
    </div>
    <p v-if="failed" class="error small">{{ failed }}. Showing extracted frames instead.</p>
    <p v-else-if="!available" class="muted small">This report's recording is not on this machine, so the panel shows the analyzer's extracted frame at each moment.</p>
    <p v-else class="muted small">Scrubbing or playing the recording moves the turn on the right with it. Space plays and pauses. Picking a turn or a log entry seeks the recording there.</p>
  </div>
</template>
