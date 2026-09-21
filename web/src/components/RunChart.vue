<script setup lang="ts">
import { computed, ref } from "vue";
import type { TurnSummary } from "../api";
import { shortLabel, yearOf } from "../format";

// One chart over the turns of a run, drawn either as lines (a value at
// every turn) or as stacked bars (an amount per turn). Every series is a
// list of values in turn order; null leaves a gap in a line and an empty
// slot in a bar. Hovering reads the values at that turn and clicking opens
// it, the same as the stat chart above it.
export interface Series {
  key: string;
  name: string;
  color: string;
  values: (number | null)[];
  /** Line width and opacity in the lines mode. A wide, soft line under a thin one keeps both visible where they coincide. */
  width?: number;
  opacity?: number;
}
const props = defineProps<{ turns: TurnSummary[]; series: Series[]; selected: string; mode: "lines" | "bars"; floor?: number }>();
const emit = defineEmits<{ select: [id: string] }>();

const W = 960;
const H = 180;
const PAD = { l: 40, r: 12, t: 10, b: 22 };

function niceMax(v: number): number {
  if (v <= 0) return 10;
  const step = Math.pow(10, Math.floor(Math.log10(v)));
  const m = v / step;
  const nice = m <= 1 ? 1 : m <= 2 ? 2 : m <= 2.5 ? 2.5 : m <= 3 ? 3 : m <= 4 ? 4 : m <= 5 ? 5 : m <= 6 ? 6 : m <= 8 ? 8 : 10;
  return nice * step;
}

const chart = computed(() => {
  const n = props.turns.length;
  let max = props.floor ?? 0;
  if (props.mode === "bars") {
    for (let i = 0; i < n; i++) {
      let sum = 0;
      for (const s of props.series) sum += Math.max(0, s.values[i] ?? 0);
      if (sum > max) max = sum;
    }
  } else {
    for (const s of props.series) for (const v of s.values) if (v !== null && v > max) max = v;
  }
  max = niceMax(max);
  const inner = W - PAD.l - PAD.r;
  const x = (i: number) => PAD.l + (n <= 1 ? inner / 2 : (i * inner) / (n - 1));
  const y = (v: number) => PAD.t + (H - PAD.t - PAD.b) * (1 - v / max);
  const slot = n <= 1 ? inner : inner / (n - 1);
  const barW = Math.max(3, Math.min(22, slot * 0.62));
  const lines = props.series.map((s) => {
    const segments: string[] = [];
    let current: string[] = [];
    s.values.forEach((v, i) => {
      if (v !== null) current.push(`${x(i).toFixed(1)},${y(v).toFixed(1)}`);
      else if (current.length) {
        segments.push(current.join(" "));
        current = [];
      }
    });
    if (current.length) segments.push(current.join(" "));
    return { ...s, segments };
  });
  const bars: { key: string; x: number; y: number; h: number; color: string }[] = [];
  for (let i = 0; i < n; i++) {
    let acc = 0;
    for (const s of props.series) {
      const v = Math.max(0, s.values[i] ?? 0);
      if (v <= 0) continue;
      const top = y(acc + v);
      bars.push({ key: `${s.key}-${i}`, x: x(i) - barW / 2, y: top, h: y(acc) - top, color: s.color });
      acc += v;
    }
  }
  const gridlines = [0.25, 0.5, 0.75, 1].map((p) => ({ v: Math.round(max * p), y: y(max * p) }));
  const years: { x: number; label: string }[] = [];
  let lastYear = "";
  props.turns.forEach((t, i) => {
    const yr = yearOf(t.label, t.phase);
    if (yr !== lastYear) {
      years.push({ x: x(i), label: yr });
      lastYear = yr;
    }
  });
  const selectedIndex = props.turns.findIndex((t) => t.id === props.selected);
  return { lines, bars, barW, gridlines, years, x, y, selectedX: selectedIndex >= 0 ? x(selectedIndex) : null, max };
});

const hover = ref<number | null>(null);
function nearest(event: MouseEvent): number | null {
  const svg = event.currentTarget as SVGSVGElement;
  const rect = svg.getBoundingClientRect();
  const px = ((event.clientX - rect.left) / rect.width) * W;
  const { x } = chart.value;
  if (!props.turns.length) return null;
  let best = 0;
  for (let i = 1; i < props.turns.length; i++) if (Math.abs(x(i) - px) < Math.abs(x(best) - px)) best = i;
  return best;
}
function pick(event: MouseEvent) {
  const i = nearest(event);
  if (i !== null) emit("select", props.turns[i].id);
}
const hoverTurn = computed(() => (hover.value === null ? null : props.turns[hover.value] ?? null));
const hoverX = computed(() => (hover.value === null ? null : chart.value.x(hover.value)));
const labelLeft = computed(() => (hoverX.value === null ? 0 : hoverX.value > W * 0.7 ? hoverX.value - 180 : hoverX.value + 10));
function at(s: Series): number | null {
  return hover.value === null ? null : s.values[hover.value] ?? null;
}
</script>

<template>
  <div class="rise">
    <svg :viewBox="`0 0 ${W} ${H}`" style="width: 100%; height: auto; display: block; cursor: crosshair" @mousemove="hover = nearest($event)" @mouseleave="hover = null" @click="pick">
      <g v-for="g in chart.gridlines" :key="g.v">
        <line :x1="PAD.l" :x2="W - PAD.r" :y1="g.y" :y2="g.y" stroke="var(--line-2)" stroke-width="1" />
        <text :x="PAD.l - 6" :y="g.y + 3.5" font-size="10" text-anchor="end" fill="var(--ink-3)" class="tabular">{{ g.v }}</text>
      </g>
      <g v-for="yr in chart.years" :key="yr.label + yr.x">
        <line :x1="yr.x" :x2="yr.x" :y1="PAD.t" :y2="H - PAD.b" stroke="var(--line)" stroke-width="1" stroke-dasharray="3 4" />
        <text :x="yr.x + 4" :y="H - 8" font-size="10" fill="var(--ink-3)" font-weight="700">{{ yr.label }}</text>
      </g>
      <line v-if="chart.selectedX !== null" :x1="chart.selectedX" :x2="chart.selectedX" :y1="PAD.t" :y2="H - PAD.b" stroke="var(--ink)" stroke-width="1.5" stroke-dasharray="2 3" />
      <template v-if="mode === 'bars'">
        <rect v-for="b in chart.bars" :key="b.key" :x="b.x" :y="b.y" :width="chart.barW" :height="b.h" :fill="b.color" rx="1.5" />
      </template>
      <g v-for="line in chart.lines" v-else :key="line.key">
        <polyline v-for="(seg, i) in line.segments" :key="i" :points="seg" fill="none" :stroke="line.color" :stroke-width="line.width ?? 2" :stroke-opacity="line.opacity ?? 1" stroke-linejoin="round" stroke-linecap="round" />
      </g>
      <g v-if="hoverX !== null && hoverTurn">
        <line :x1="hoverX" :x2="hoverX" :y1="PAD.t" :y2="H - PAD.b" stroke="var(--ink-3)" stroke-width="1" />
        <template v-if="mode === 'lines'">
          <circle v-for="s in series" :key="'c' + s.key" v-show="at(s) !== null" :cx="hoverX" :cy="chart.y(at(s) ?? 0)" r="4" :fill="s.color" stroke="var(--bg-2)" stroke-width="1.5" />
        </template>
        <foreignObject :x="labelLeft" :y="PAD.t" width="176" :height="H - PAD.t - PAD.b">
          <div xmlns="http://www.w3.org/1999/xhtml" style="font: 700 11px/1.35 var(--font-text); color: var(--ink); background: var(--bg-2); border: 1px solid var(--line); border-radius: 8px; padding: 6px 8px; display: inline-block; box-shadow: var(--shadow-lift)">
            <div style="font-family: var(--font-display); font-weight: 800; font-size: 12px">{{ yearOf(hoverTurn.label, hoverTurn.phase) }} · {{ shortLabel(hoverTurn.label) }}</div>
            <div v-for="s in series" :key="'l' + s.key" style="display: flex; justify-content: space-between; gap: 10px">
              <span><i :style="{ display: 'inline-block', width: '8px', height: '8px', borderRadius: '2px', background: s.color, marginRight: '5px' }"></i>{{ s.name }}</span>
              <span class="tabular">{{ at(s) === null ? "?" : mode === "bars" ? (at(s)! > 0 ? "+" : "") + at(s) : at(s) }}</span>
            </div>
          </div>
        </foreignObject>
      </g>
    </svg>
    <div class="legend" style="margin-top: 6px">
      <span v-for="s in series" :key="s.key"><i :style="{ background: s.color, opacity: s.opacity ?? 1 }"></i>{{ s.name }}</span>
    </div>
  </div>
</template>
