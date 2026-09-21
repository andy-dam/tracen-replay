<script setup lang="ts">
// A question with two answers, drawn by the page. The browser's own
// confirm() is not used: the desktop application's web view on a Mac never
// shows it and answers "no", so a button behind it does nothing.
import { nextTick, onMounted, onUnmounted, ref } from "vue";

const props = withDefaults(defineProps<{ title: string; message: string; confirmLabel?: string; cancelLabel?: string; danger?: boolean; busy?: boolean }>(), {
  confirmLabel: "Confirm",
  cancelLabel: "Cancel",
  danger: false,
  busy: false,
});
const emit = defineEmits<{ confirm: []; cancel: [] }>();
const cancelButton = ref<HTMLButtonElement | null>(null);

const onKey = (e: KeyboardEvent) => {
  if (e.key === "Escape" && !props.busy) emit("cancel");
};
onMounted(async () => {
  window.addEventListener("keydown", onKey);
  await nextTick();
  // The safe answer has the focus, so Enter does not delete anything.
  cancelButton.value?.focus();
});
onUnmounted(() => window.removeEventListener("keydown", onKey));
</script>

<template>
  <div class="dialog-backdrop" @click.self="!busy && emit('cancel')">
    <div class="dialog" role="alertdialog" aria-modal="true" aria-labelledby="confirm-title" aria-describedby="confirm-message">
      <h2 id="confirm-title">{{ title }}</h2>
      <p id="confirm-message">{{ message }}</p>
      <div class="row dialog-actions">
        <button class="btn" :class="danger ? 'danger' : 'primary'" :disabled="busy" @click="emit('confirm')">{{ confirmLabel }}</button>
        <button ref="cancelButton" class="btn quiet" :disabled="busy" @click="emit('cancel')">{{ cancelLabel }}</button>
      </div>
    </div>
  </div>
</template>
