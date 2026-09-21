<script setup lang="ts">
// The desktop window's close button: the application raises
// "tracen:close-request" on the window instead of closing, and this dialog
// sends the answer back (POST /api/desktop/close). Only the desktop
// application mounts it.
import { onMounted, onUnmounted, ref } from "vue";
import { api } from "../api";

const open = ref(false);
const remember = ref(false);
const running = ref(0);
const background = ref<"tray" | "dock">("tray");
const busy = ref(false);

async function ask() {
  open.value = true;
  remember.value = false;
  try {
    const [jobs, settings] = await Promise.all([api.jobs(), api.settings()]);
    running.value = jobs.filter((j) => j.status === "running" || j.status === "queued").length;
    background.value = settings.background;
  } catch {
    // The question is still asked without the count.
  }
}

async function answer(action: "exit" | "background" | "cancel") {
  if (busy.value) return;
  busy.value = true;
  try {
    await api.desktopClose(action, action !== "cancel" && remember.value);
  } catch {
    // The window stays as it is; the close button can be pressed again.
  } finally {
    busy.value = false;
    open.value = false;
  }
}

const onKey = (e: KeyboardEvent) => {
  if (open.value && e.key === "Escape") answer("cancel");
};
onMounted(() => {
  window.addEventListener("tracen:close-request", ask);
  window.addEventListener("keydown", onKey);
});
onUnmounted(() => {
  window.removeEventListener("tracen:close-request", ask);
  window.removeEventListener("keydown", onKey);
});
</script>

<template>
  <div v-if="open" class="dialog-backdrop" @click.self="answer('cancel')">
    <div class="dialog" role="dialog" aria-modal="true" aria-labelledby="close-title">
      <h2 id="close-title">Close Tracen Replay</h2>
      <p v-if="running" class="close-warn">{{ running }} {{ running === 1 ? "analysis is" : "analyses are" }} running or queued. Exit stops {{ running === 1 ? "it" : "them" }}.</p>
      <p class="muted small">{{ background === "dock" ? "Keep Running closes the window and leaves the application in the Dock." : "Keep Running closes the window and leaves the application in the tray." }} Analyses continue.</p>
      <label class="close-remember"><input v-model="remember" type="checkbox" /> Remember this choice</label>
      <div class="row dialog-actions" style="margin-top: 0">
        <button class="btn primary" :disabled="busy" @click="answer('background')">Keep Running</button>
        <button class="btn" :disabled="busy" @click="answer('exit')">Exit</button>
        <button class="btn quiet" :disabled="busy" @click="answer('cancel')">Cancel</button>
      </div>
      <p class="muted small" style="margin-top: 10px">Changeable in Settings under Closing the Window.</p>
    </div>
  </div>
</template>

<style scoped>
.dialog .close-warn { color: var(--warn-ink); font-weight: 700; }
.close-remember { display: flex; align-items: center; gap: 8px; margin: 14px 0 16px; cursor: pointer; user-select: none; }
.close-remember input { width: 16px; height: 16px; accent-color: var(--green); }
</style>
