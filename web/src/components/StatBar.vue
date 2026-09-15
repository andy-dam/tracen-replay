<script setup lang="ts">
import { onUnmounted, reactive, watch } from "vue";
import type { FieldAccounting } from "../api";
import { CORE_STATS, STAT_NAMES, statusText } from "../format";
import RankBadge from "./RankBadge.vue";

// The five stats plus skill points in one row, the way the game's own panel
// reads: name on top, rank badge and value below. `after` adds the accounted
// end-of-turn value and its status under each stat. Values count up to
// their new number when the turn changes.
const props = defineProps<{
  stats: Record<string, number | null> | null | undefined;
  after?: Record<string, FieldAccounting> | null;
  compact?: boolean;
  // Fields the viewer filled in and the server verified against the next observed value.
  verified?: string[] | null;
}>();

const FIELDS = [...CORE_STATS, "skill_points"];

function value(field: string): number | null {
  const map = props.stats;
  if (!map || !(field in map)) return null;
  return map[field];
}
function acct(field: string): FieldAccounting | null {
  return props.after?.[field] ?? null;
}
function delta(field: string): number | null {
  const a = acct(field);
  const v = value(field);
  if (!a || a.after === null || v === null) return null;
  return a.after - v;
}
function isWarn(field: string): boolean {
  if (props.verified?.includes(field)) return false;
  const a = acct(field);
  if (a?.turn_difference) return true;
  return !!a && a.status !== "balanced_observations" && a.status !== "balanced_with_derived_changes";
}

// Displayed numbers ease toward the real ones; the real ones are always in the title.
const shown = reactive<Record<string, number | null>>({});
const frames: Record<string, number> = {};
const reduced = window.matchMedia?.("(prefers-reduced-motion: reduce)").matches ?? false;
function animate(field: string, target: number | null) {
  if (frames[field]) cancelAnimationFrame(frames[field]);
  const from = shown[field];
  if (target === null || from === null || from === undefined || reduced) {
    shown[field] = target;
    return;
  }
  const start = performance.now();
  const duration = 450;
  const tick = (now: number) => {
    const t = Math.min(1, (now - start) / duration);
    const eased = 1 - Math.pow(1 - t, 3);
    shown[field] = Math.round(from + (target - from) * eased);
    if (t < 1) frames[field] = requestAnimationFrame(tick);
  };
  frames[field] = requestAnimationFrame(tick);
}
watch(
  () => props.stats,
  () => {
    for (const f of FIELDS) animate(f, value(f));
  },
  { immediate: true, deep: true },
);
onUnmounted(() => {
  for (const id of Object.values(frames)) cancelAnimationFrame(id);
});
</script>

<template>
  <div class="statbar" :class="{ compact }">
    <div v-for="f in FIELDS" :key="f" class="sb-cell" :class="f">
      <div class="sb-name">{{ STAT_NAMES[f] }}</div>
      <div class="sb-body" :title="value(f) === null ? 'not observed' : String(value(f))">
        <RankBadge v-if="f !== 'skill_points'" :value="value(f)" :small="compact" />
        <span class="sb-value" :class="{ unknown: value(f) === null }">{{ shown[f] ?? "?" }}</span>
      </div>
      <div v-if="acct(f)" class="sb-after" :class="{ warn: isWarn(f) }" :title="statusText(acct(f)!.status)">
        <span v-if="acct(f)!.after !== null">→ {{ acct(f)!.after }}<span v-if="delta(f)" class="sb-delta" :class="delta(f)! > 0 ? 'up' : 'down'"> {{ delta(f)! > 0 ? "+" : "" }}{{ delta(f) }}</span></span>
        <span v-else>→ ?</span>
        <span v-if="verified?.includes(f)" class="sb-flag">filled in by you</span>
        <span v-else-if="acct(f)!.turn_difference" class="sb-flag">worked out from the turn difference</span>
        <span v-else-if="acct(f)!.status !== 'balanced_observations'" class="sb-flag">{{ statusText(acct(f)!.status) }}</span>
      </div>
    </div>
  </div>
</template>
