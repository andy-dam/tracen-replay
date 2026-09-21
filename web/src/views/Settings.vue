<script setup lang="ts">
import { computed, onMounted, ref } from "vue";
import { api, ApiError, type OnClose, type Settings, type SettingsChange } from "../api";

const settings = ref<Settings | null>(null);
const error = ref("");
const saving = ref(false);

async function load() {
  try {
    settings.value = await api.settings();
  } catch (e) {
    error.value = e instanceof ApiError ? e.message : (e as Error).message;
  }
}

async function change(c: SettingsChange) {
  if (!settings.value || saving.value) return;
  saving.value = true;
  error.value = "";
  try {
    settings.value = await api.saveSettings(c);
  } catch (e) {
    error.value = e instanceof ApiError ? e.message : (e as Error).message;
  } finally {
    saving.value = false;
  }
}

const recommended = computed(() => {
  const s = settings.value;
  return !!s && s.parallel === s.recommended.parallel && s.memory_limit_gb === s.recommended.memory_limit_gb;
});
const backgroundLabel = computed(() => (settings.value?.background === "dock" ? "Keep running in the Dock" : "Keep running in the tray"));

onMounted(load);
</script>

<template>
  <section>
    <div class="page-head">
      <div>
        <h1>Settings</h1>
        <p>Changes apply to the next analysis started.</p>
      </div>
    </div>
    <p v-if="error" class="error">{{ error }}</p>
    <template v-if="settings">
      <div class="card">
        <div class="setting">
          <div>
            <h2>Graphics Acceleration</h2>
            <p class="muted small">Runs text recognition on the graphics hardware instead of the processor. Off, the processor runs it.</p>
            <p v-if="settings.gpu_available" class="muted small">Detected: <strong>{{ settings.device }}</strong></p>
            <p v-else class="muted small" :title="settings.device">No supported hardware detected. Requires a DirectX 12 graphics card (Windows) or Apple silicon (Mac).</p>
          </div>
          <label class="switch" :class="{ off: !settings.gpu, disabled: !settings.gpu_available || saving }">
            <input type="checkbox" :checked="settings.gpu" :disabled="!settings.gpu_available || saving" @change="change({ gpu: ($event.target as HTMLInputElement).checked })" />
            <span class="track"><span class="knob"></span></span>
            <span class="state">{{ settings.gpu ? "On" : "Off" }}</span>
          </label>
        </div>

        <div class="setting">
          <div>
            <h2>Parallel Analyses</h2>
            <p class="muted small">Number of analyses that run at the same time. The rest wait in the queue.</p>
            <p class="muted small">Maximum with this memory limit: <strong>{{ settings.parallel_max }}</strong>. Recommended: <strong>{{ settings.recommended.parallel }}</strong>. Reader processes per analysis: <strong>{{ settings.workers }}</strong>.</p>
          </div>
          <div class="stepper" role="group" aria-label="Parallel analyses">
            <button class="btn small" :disabled="saving || settings.parallel <= 1" aria-label="Fewer" @click="change({ parallel: settings.parallel - 1 })">−</button>
            <span class="value num">{{ settings.parallel }}</span>
            <button class="btn small" :disabled="saving || settings.parallel >= settings.parallel_max" aria-label="More" @click="change({ parallel: settings.parallel + 1 })">+</button>
          </div>
        </div>

        <div class="setting">
          <div>
            <h2>Memory Limit</h2>
            <p class="muted small">Memory that running analyses are planned within. One analysis needs about {{ settings.memory_min_gb }} GB. Sets the maximum for parallel analyses. Not a hard cap.</p>
            <p class="muted small">Installed: <strong>{{ settings.memory_total_gb ? settings.memory_total_gb + " GB" : "unknown" }}</strong>. Recommended: <strong>{{ settings.recommended.memory_limit_gb }} GB</strong>.</p>
          </div>
          <div class="stepper" role="group" aria-label="Memory limit">
            <button class="btn small" :disabled="saving || settings.memory_limit_gb <= settings.memory_min_gb" aria-label="Less" @click="change({ memory_limit_gb: settings.memory_limit_gb - 1 })">−</button>
            <span class="value num">{{ settings.memory_limit_gb }} GB</span>
            <button class="btn small" :disabled="saving || (settings.memory_total_gb > 0 && settings.memory_limit_gb >= settings.memory_total_gb)" aria-label="More" @click="change({ memory_limit_gb: settings.memory_limit_gb + 1 })">+</button>
          </div>
        </div>

        <div class="setting">
          <div>
            <h2>Recommended Load</h2>
            <p class="muted small">{{ settings.recommended.parallel }} parallel, {{ settings.recommended.memory_limit_gb }} GB, from {{ settings.memory_total_gb || "unknown" }} GB of memory and {{ settings.cores }} logical processors.</p>
          </div>
          <button class="btn small" :disabled="saving || recommended" @click="change({ parallel: settings.recommended.parallel, memory_limit_gb: settings.recommended.memory_limit_gb })">{{ recommended ? "In Use" : "Use Recommended" }}</button>
        </div>
      </div>

      <div class="card" style="margin-top: 16px">
        <div class="setting">
          <div>
            <h2>Closing the Window</h2>
            <p class="muted small">Action of the window's close button. {{ backgroundLabel }}: the window closes, analyses continue.</p>
          </div>
          <label class="field" style="margin: 0; min-width: 240px">
            <select :value="settings.on_close" :disabled="saving" aria-label="Closing the window" @change="change({ on_close: ($event.target as HTMLSelectElement).value as OnClose })">
              <option value="ask">Ask every time</option>
              <option value="background">{{ backgroundLabel }}</option>
              <option value="exit">Exit</option>
            </select>
          </label>
        </div>
      </div>
    </template>
  </section>
</template>

<style scoped>
.setting { display: flex; align-items: center; justify-content: space-between; gap: 24px; }
.setting + .setting { margin-top: 18px; padding-top: 18px; border-top: 1px solid var(--line); }
.setting h2 { margin: 0 0 6px; font-size: 18px; }
.setting p { margin: 0 0 6px; max-width: 78ch; }
.setting p:last-child { margin-bottom: 0; }
.setting strong { color: var(--ink); font-weight: 700; }
.stepper { display: inline-flex; align-items: center; gap: 10px; flex: none; }
.stepper .value { min-width: 4.5em; text-align: center; font-family: var(--font-display); font-weight: 800; font-size: 18px; }
.stepper .btn { min-width: 38px; padding: 6px 0; font-size: 18px; line-height: 1.2; }
.switch { display: inline-flex; align-items: center; gap: 10px; cursor: pointer; user-select: none; flex: none; }
.switch.disabled { cursor: not-allowed; opacity: 0.55; }
.switch input { position: absolute; opacity: 0; width: 0; height: 0; }
.track { width: 44px; height: 24px; border-radius: 12px; background: var(--accent, #3ec9a7); position: relative; transition: background 0.15s; }
.switch.off .track { background: var(--line, #556); }
.knob { position: absolute; top: 3px; left: 23px; width: 18px; height: 18px; border-radius: 50%; background: #fff; transition: left 0.15s; }
.switch.off .knob { left: 3px; }
.state { min-width: 2.2em; }
@media (max-width: 720px) { .setting { flex-direction: column; align-items: flex-start; } }
</style>
