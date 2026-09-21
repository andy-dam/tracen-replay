<script setup lang="ts">
import { onMounted, ref } from "vue";
import { api, ApiError, type Settings } from "../api";

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

async function toggle(on: boolean) {
  if (!settings.value || saving.value) return;
  saving.value = true;
  error.value = "";
  try {
    settings.value = await api.saveSettings({ gpu: on });
  } catch (e) {
    error.value = e instanceof ApiError ? e.message : (e as Error).message;
  } finally {
    saving.value = false;
  }
}

onMounted(load);
</script>

<template>
  <section>
    <div class="page-head">
      <div>
        <h1>Settings</h1>
        <p>How analyses run on this computer. A change applies to the next analysis you start.</p>
      </div>
    </div>
    <p v-if="error" class="error">{{ error }}</p>
    <div v-if="settings" class="card">
      <div class="setting">
        <div>
          <h2>Graphics Acceleration</h2>
          <template v-if="settings.gpu_available">
            <p class="muted small">
              Most of an analysis is reading text off the recording's frames. Your <strong>{{ settings.device }}</strong> can do that reading, and it is a lot quicker at it than the processor.
            </p>
            <p class="muted small">Switch it off if an analysis fails, or if the computer gets too sluggish to use while one runs. The processor does the reading then.</p>
          </template>
          <template v-else>
            <p class="muted small" :title="settings.device">
              Most of an analysis is reading text off the recording's frames. On this computer the processor does that, because no graphics hardware the app can use was found.
            </p>
            <p class="muted small">It needs a graphics card that supports DirectX 12 on Windows, or a Mac with an Apple chip (M1 or later).</p>
          </template>
        </div>
        <label class="switch" :class="{ off: !settings.gpu, disabled: !settings.gpu_available || saving }">
          <input type="checkbox" :checked="settings.gpu" :disabled="!settings.gpu_available || saving" @change="toggle(($event.target as HTMLInputElement).checked)" />
          <span class="track"><span class="knob"></span></span>
          <span class="state">{{ settings.gpu ? "On" : "Off" }}</span>
        </label>
      </div>
    </div>
  </section>
</template>

<style scoped>
.setting { display: flex; align-items: center; justify-content: space-between; gap: 24px; }
.setting h2 { margin: 0 0 6px; font-size: 18px; }
.setting p { margin: 0 0 6px; max-width: 64ch; }
.setting p:last-child { margin-bottom: 0; }
.setting strong { color: var(--ink); font-weight: 700; }
.switch { display: inline-flex; align-items: center; gap: 10px; cursor: pointer; user-select: none; }
.switch.disabled { cursor: not-allowed; opacity: 0.55; }
.switch input { position: absolute; opacity: 0; width: 0; height: 0; }
.track { width: 44px; height: 24px; border-radius: 12px; background: var(--accent, #3ec9a7); position: relative; transition: background 0.15s; }
.switch.off .track { background: var(--line, #556); }
.knob { position: absolute; top: 3px; left: 23px; width: 18px; height: 18px; border-radius: 50%; background: #fff; transition: left 0.15s; }
.switch.off .knob { left: 3px; }
.state { min-width: 2.2em; }
</style>
