<script setup lang="ts">
import { computed, ref } from "vue";
import type { TurnSummary } from "../api";
import { shortLabel, STAT_FIELDS, STAT_NAMES, yearOf } from "../format";

const props = defineProps<{ turns: TurnSummary[]; selected: string }>();
const emit = defineEmits<{ select: [id: string] }>();

const W = 960;
const H = 220;
const PAD = { l: 36, r: 12, t: 10, b: 22 };
const LINES = STAT_FIELDS.filter((f) => f !== "skill_points");

// One line per stat from the opening values the analyzer observed. A turn
// whose opening was never on screen (a race-day hub has no stat bar) is
// drawn from the value the service carries from the previous turn's
// entries, as a hollow point; a turn with neither breaks the line, and the
// gap is the fact. Hovering reads the values at that turn.
function observed(t: TurnSummary, field: string): number | null {
  const v = t.opening.stats?.[field];
  return typeof v === "number" ? v : null;
}
function carried(t: TurnSummary, field: string): number | null {
  if (observed(t, field) !== null) return null;
  const v = t.opening_estimate?.stats?.[field];
  return typeof v === "number" ? v : null;
}
function plotted(t: TurnSummary, field: string): number | null {
  return observed(t, field) ?? carried(t, field);
}
const chart = computed(() => {
  const turns = props.turns.filter((t) => t.window_kind !== "unresolved_phase");
  const n = turns.length;
  let max = 0;
  for (const t of turns) for (const f of LINES) {
    const v = plotted(t, f);
    if (v !== null && v > max) max = v;
  }
  max = Math.max(100, Math.ceil(max / 100) * 100);
  const x = (i: number) => PAD.l + (n <= 1 ? 0 : (i * (W - PAD.l - PAD.r)) / (n - 1));
  const y = (v: number) => PAD.t + (H - PAD.t - PAD.b) * (1 - v / max);
  const lines = LINES.map((field) => {
    const segments: string[] = [];
    let current: string[] = [];
    const estimates: { x: number; y: number }[] = [];
    turns.forEach((t, i) => {
      const v = plotted(t, field);
      if (v !== null) {
        current.push(`${x(i).toFixed(1)},${y(v).toFixed(1)}`);
        if (carried(t, field) !== null) estimates.push({ x: x(i), y: y(v) });
      } else if (current.length) {
        segments.push(current.join(" "));
        current = [];
      }
    });
    if (current.length) segments.push(current.join(" "));
    return { field, segments, estimates };
  });
  const gridlines = [0.25, 0.5, 0.75, 1].map((p) => ({ v: Math.round(max * p), y: y(max * p) }));
  // Year boundaries as faint markers.
  const years: { x: number; label: string }[] = [];
  let lastYear = "";
  turns.forEach((t, i) => {
    const yr = yearOf(t.label, t.phase);
    if (yr !== lastYear) {
      years.push({ x: x(i), label: yr });
      lastYear = yr;
    }
  });
  const selectedIndex = turns.findIndex((t) => t.id === props.selected);
  return { turns, lines, gridlines, years, x, y, selectedX: selectedIndex >= 0 ? x(selectedIndex) : null, max };
});

const hover = ref<number | null>(null);
function nearest(event: MouseEvent): number | null {
  const svg = event.currentTarget as SVGSVGElement;
  const rect = svg.getBoundingClientRect();
  const px = ((event.clientX - rect.left) / rect.width) * W;
  const { turns, x } = chart.value;
  if (!turns.length) return null;
  let best = 0;
  for (let i = 1; i < turns.length; i++) if (Math.abs(x(i) - px) < Math.abs(x(best) - px)) best = i;
  return best;
}
function onMove(event: MouseEvent) {
  hover.value = nearest(event);
}
function pick(event: MouseEvent) {
  const i = nearest(event);
  if (i !== null) emit("select", chart.value.turns[i].id);
}
const hoverTurn = computed(() => (hover.value === null ? null : chart.value.turns[hover.value] ?? null));
const hoverX = computed(() => (hover.value === null ? null : chart.value.x(hover.value)));
const labelLeft = computed(() => (hoverX.value === null ? 0 : hoverX.value > W * 0.7 ? hoverX.value - 180 : hoverX.value + 10));
</script>

<template>
  <div class="rise">
    <svg :viewBox="`0 0 ${W} ${H}`" style="width: 100%; height: auto; display: block; cursor: crosshair" @mousemove="onMove" @mouseleave="hover = null" @click="pick">
      <g v-for="g in chart.gridlines" :key="g.v">
        <line :x1="PAD.l" :x2="W - PAD.r" :y1="g.y" :y2="g.y" stroke="var(--line-2)" stroke-width="1" />
        <text :x="PAD.l - 6" :y="g.y + 3.5" font-size="10" text-anchor="end" fill="var(--ink-3)" class="tabular">{{ g.v }}</text>
      </g>
      <g v-for="yr in chart.years" :key="yr.label + yr.x">
        <line :x1="yr.x" :x2="yr.x" :y1="PAD.t" :y2="H - PAD.b" stroke="var(--line)" stroke-width="1" stroke-dasharray="3 4" />
        <text :x="yr.x + 4" :y="H - 8" font-size="10" fill="var(--ink-3)" font-weight="700">{{ yr.label }}</text>
      </g>
      <line v-if="chart.selectedX !== null" :x1="chart.selectedX" :x2="chart.selectedX" :y1="PAD.t" :y2="H - PAD.b" stroke="var(--ink)" stroke-width="1.5" stroke-dasharray="2 3" />
      <g v-for="line in chart.lines" :key="line.field">
        <polyline v-for="(seg, i) in line.segments" :key="i" :points="seg" fill="none" :stroke="`var(--stat-${line.field})`" stroke-width="2" stroke-linejoin="round" stroke-linecap="round" />
        <circle v-for="(p, i) in line.estimates" :key="'e' + i" :cx="p.x" :cy="p.y" r="3.5" fill="var(--bg-2)" :stroke="`var(--stat-${line.field})`" stroke-width="2" />
      </g>
      <g v-if="hoverX !== null && hoverTurn">
        <line :x1="hoverX" :x2="hoverX" :y1="PAD.t" :y2="H - PAD.b" stroke="var(--ink-3)" stroke-width="1" />
        <circle v-for="line in chart.lines" :key="'c' + line.field" v-show="plotted(hoverTurn, line.field) !== null" :cx="hoverX" :cy="chart.y(plotted(hoverTurn, line.field) ?? 0)" r="4" :fill="`var(--stat-${line.field})`" stroke="var(--bg-2)" stroke-width="1.5" />
        <foreignObject :x="labelLeft" :y="PAD.t" width="176" :height="H - PAD.t - PAD.b">
          <div xmlns="http://www.w3.org/1999/xhtml" style="font: 700 11px/1.35 var(--font-text); color: var(--ink); background: var(--bg-2); border: 1px solid var(--line); border-radius: 8px; padding: 6px 8px; display: inline-block; box-shadow: var(--shadow-lift)">
            <div style="font-family: var(--font-display); font-weight: 800; font-size: 12px">{{ yearOf(hoverTurn.label, hoverTurn.phase) }} · {{ shortLabel(hoverTurn.label) }}</div>
            <div v-for="line in chart.lines" :key="'l' + line.field" style="display: flex; justify-content: space-between; gap: 10px">
              <span><i :style="{ display: 'inline-block', width: '8px', height: '8px', borderRadius: '2px', background: `var(--stat-${line.field})`, marginRight: '5px' }"></i>{{ STAT_NAMES[line.field] }}</span>
              <span class="tabular">{{ carried(hoverTurn, line.field) !== null ? "≈ " : "" }}{{ plotted(hoverTurn, line.field) ?? "?" }}</span>
            </div>
            <div v-if="hoverTurn.opening_estimate && !hoverTurn.opening.stats" style="margin-top: 4px; font-weight: 600; color: var(--ink-3)">≈ not on screen this turn, carried from the previous turn's entries</div>
          </div>
        </foreignObject>
      </g>
    </svg>
    <div class="legend" style="margin-top: 6px">
      <span v-for="line in chart.lines" :key="line.field"><i :class="line.field"></i>{{ STAT_NAMES[line.field] }}</span>
    </div>
  </div>
</template>
