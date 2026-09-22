<script setup lang="ts">
import { computed } from "vue";
import type { Entry, TurnSummary } from "../api";
import { CORE_STATS, PERFORMANCE_FIELDS, STAT_NAMES, titleCase } from "../format";
import RunChart, { type Series } from "./RunChart.vue";
import StatChart from "./StatChart.vue";

// The opening value of every stat at every turn as one wide, clickable
// chart, then three more views over the same turns: how much each turn
// added, the skill points earned and held, and the performance points of a
// Grand Concert run.
const props = defineProps<{ turns: TurnSummary[]; entries: Entry[]; selected: string }>();
const emit = defineEmits<{ select: [id: string] }>();

const plotted = computed(() => props.turns.filter((t) => t.window_kind !== "unresolved_phase"));

function opening(t: TurnSummary, field: string): number | null {
  const seen = t.opening.stats?.[field];
  if (typeof seen === "number") return seen;
  const carried = t.opening_estimate?.stats?.[field];
  return typeof carried === "number" ? carried : null;
}

// What each turn added to each stat: the next turn's opening less this one's,
// when both are known. The last turn has no next opening and stays empty.
const growth = computed<Series[]>(() =>
  CORE_STATS.map((field) => ({
    key: field,
    name: STAT_NAMES[field],
    color: `var(--stat-${field})`,
    values: plotted.value.map((t, i) => {
      const next = plotted.value[i + 1];
      const a = opening(t, field);
      const b = next ? opening(next, field) : null;
      return a === null || b === null ? null : b - a;
    }),
  })),
);
const growthSeen = computed(() => growth.value.some((s) => s.values.some((v) => v !== null && v !== 0)));

// Two lines over the same turns: the current balance at the start of the
// turn, and the total gained by then, which is that balance plus everything
// spent before the turn. Until the first purchase the two are the same
// number, and many runs buy nothing until the end. So the total is drawn
// first as a wide, soft band and the balance as a thin line on top of it:
// where they coincide both are still seen, one inside the other.
const skillPoints = computed<Series[]>(() => {
  const spentIn = new Map<string, number>();
  for (const e of props.entries) {
    if (e.accounting_role === "reference_only_not_an_additional_award" || !e.turn_id) continue;
    const amount = e.changes?.stats?.skill_points?.amount ?? 0;
    if (amount < 0) spentIn.set(e.turn_id, (spentIn.get(e.turn_id) ?? 0) - amount);
  }
  let spent = 0;
  const balance: (number | null)[] = [];
  const earned: (number | null)[] = [];
  for (const t of plotted.value) {
    const held = opening(t, "skill_points");
    balance.push(held);
    earned.push(held === null ? null : held + spent);
    spent += spentIn.get(t.id) ?? 0;
  }
  return [
    { key: "earned", name: "Total Skill Points", color: "var(--orange)", values: earned, width: 8, opacity: 0.55 },
    { key: "skill_points", name: "Current Skill Points", color: "var(--stat-skill_points)", values: balance, width: 2.25 },
  ];
});
const skillSeen = computed(() => skillPoints.value[1].values.some((v) => v !== null));

const performance = computed<Series[]>(() =>
  PERFORMANCE_FIELDS.map((field) => ({
    key: field,
    name: titleCase(field),
    color: `var(--perf-${field})`,
    values: plotted.value.map((t) => {
      const v = t.opening.performance?.[field];
      return typeof v === "number" ? v : null;
    }),
  })),
);
const performanceSeen = computed(() => performance.value.some((s) => s.values.some((v) => v !== null)));
</script>

<template>
  <h3 class="pane-h" style="margin-top: 0">Stats Across the Run</h3>
  <p class="muted small" style="margin-bottom: 8px">Opening value at every turn. A click on the chart opens that turn. A hollow point is a turn whose opening was never on screen (a race day) and shows the value carried from the previous turn's entries. A gap is a turn with neither.</p>
  <StatChart :turns="turns" :selected="selected" @select="(id) => emit('select', id)" />

  <template v-if="growthSeen">
    <h3 class="pane-h">Growth Each Turn</h3>
    <p class="muted small" style="margin-bottom: 8px">How much each turn added to the five stats, stacked. A tall bar is a good training or a race. An empty slot is a rest, an outing, or a turn whose next opening was never on screen.</p>
    <RunChart :turns="plotted" :series="growth" :selected="selected" mode="bars" :floor="20" @select="(id) => emit('select', id)" />
  </template>

  <template v-if="skillSeen">
    <h3 class="pane-h">Skill Points Across the Run</h3>
    <p class="muted small" style="margin-bottom: 8px">Total skill points gained by the start of each turn (the wide band) and the current balance (the line). Both are the same until skills are bought.</p>
    <RunChart :turns="plotted" :series="skillPoints" :selected="selected" mode="lines" :floor="100" @select="(id) => emit('select', id)" />
  </template>

  <template v-if="performanceSeen">
    <h3 class="pane-h">Performance Points Across the Run</h3>
    <p class="muted small" style="margin-bottom: 8px">The five performance stats at the start of every turn, as read from the Grand Concert bar. Lessons and songs spend them. Training earns them.</p>
    <RunChart :turns="plotted" :series="performance" :selected="selected" mode="lines" :floor="50" @select="(id) => emit('select', id)" />
  </template>
</template>
