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
          <h2>Use the graphics card</h2>
          <p class="muted small">
            {{ settings.gpu_available ? `Found: ${settings.device}. On, a career analyzes in a fraction of the time the processor needs.` : `Not available: ${settings.device}. The switch turns on when a supported card is found (a DirectX 12 card on Windows, Apple silicon on Mac).` }}
          </p>
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
.switch { display: inline-flex; align-items: center; gap: 10px; cursor: pointer; user-select: none; }
.switch.disabled { cursor: not-allowed; opacity: 0.55; }
.switch input { position: absolute; opacity: 0; width: 0; height: 0; }
.track { width: 44px; height: 24px; border-radius: 12px; background: var(--accent, #3ec9a7); position: relative; transition: background 0.15s; }
.switch.off .track { background: var(--line, #556); }
.knob { position: absolute; top: 3px; left: 23px; width: 18px; height: 18px; border-radius: 50%; background: #fff; transition: left 0.15s; }
.switch.off .knob { left: 3px; }
.state { min-width: 2.2em; }
</style>
