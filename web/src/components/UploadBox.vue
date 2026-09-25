<script setup lang="ts">
import { ref } from "vue";
import { ApiError, hosted, uploadRecording, type Recording } from "../api";
import { bytes } from "../format";

const emit = defineEmits<{ uploaded: [recording: Recording] }>();
const over = ref(false);
const sent = ref(0);
const total = ref(0);
const name = ref("");
const error = ref("");
const input = ref<HTMLInputElement | null>(null);
let abort: (() => void) | null = null;

async function start(file: File) {
  error.value = "";
  name.value = file.name;
  sent.value = 0;
  total.value = file.size;
  const upload = uploadRecording(file, (s, t) => {
    sent.value = s;
    total.value = t;
  });
  abort = upload.abort;
  try {
    const recording = await upload.promise;
    emit("uploaded", recording);
  } catch (e) {
    error.value = e instanceof ApiError ? e.message : (e as Error).message;
  } finally {
    abort = null;
    name.value = "";
  }
}

function pick(e: Event) {
  const file = (e.target as HTMLInputElement).files?.[0];
  if (file) start(file);
  (e.target as HTMLInputElement).value = "";
}

function drop(e: DragEvent) {
  over.value = false;
  const file = e.dataTransfer?.files?.[0];
  if (file) start(file);
}

function cancel() {
  abort?.();
}
</script>

<template>
  <div class="drop" :class="{ over }" @dragover.prevent="over = true" @dragleave="over = false" @drop.prevent="drop">
    <template v-if="name">
      <p><strong>{{ name }}</strong> <span class="muted">{{ bytes(sent) }} of {{ bytes(total) }}</span></p>
      <div class="bar"><i :style="{ width: total ? (100 * sent) / total + '%' : '0%' }"></i></div>
      <p style="margin-top: 12px"><button class="btn small" @click="cancel">Cancel Upload</button></p>
    </template>
    <template v-else>
      <p><strong>Drop a Career Recording Here</strong></p>
      <p class="muted small">MP4, MOV, WebM or MKV, usually 1 to 4 GB for a full career. PC recordings: 16:9, 720p to 4K, best at 1080p or higher. Phone and tablet recordings: portrait, on their own or inside a landscape video.{{ hosted ? "" : " The file stays on this computer." }}</p>
      <button class="btn" @click="input?.click()">Choose a File</button>
      <input ref="input" type="file" accept=".mp4,.m4v,.mov,.webm,.mkv,video/*" @change="pick" />
    </template>
    <p v-if="error" class="error small" style="margin-top: 10px">{{ error }}</p>
  </div>
</template>
