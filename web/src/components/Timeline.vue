<script setup lang="ts">
import { computed, ref } from "vue";
import type { TurnSummary } from "../api";
import { actionClass, clock, CORE_STATS, describeAction, repeatsYear, shortLabel, STAT_NAMES, stripPosition, yearOf } from "../format";

// The run as one scrubber: 24 half-month cells per year in a single row
// (Junior, Classic, Senior) and the three finale races, wrapping into one
// row per year on narrow screens. A cell holds every window the ledger put
// there; the first one is what a click opens, a dot marks that there are
// more. Hovering shows the turn, its action and its opening stats.
const props = defineProps<{ turns: TurnSummary[]; selected: string; flagged?: Set<string> }>();
const emit = defineEmits<{ select: [id: string] }>();

interface Cell {
  key: string;
  turns: TurnSummary[];
  finale: boolean;
}

const cells = computed(() => {
  const out: Cell[] = [];
  for (let row = 0; row < 3; row++) for (let col = 0; col < 24; col++) out.push({ key: `${row}-${col}`, turns: [], finale: false });
  for (let col = 0; col < 3; col++) out.push({ key: `3-${col}`, turns: [], finale: true });
  const unplaced: TurnSummary[] = [];
  for (const turn of props.turns) {
    const pos = stripPosition(turn);
    if (!pos) unplaced.push(turn);
    else out[pos.row === 3 ? 72 + pos.col : pos.row * 24 + pos.col].turns.push(turn);
  }
  return { cells: out, unplaced };
});

const current = computed(() => props.turns.find((t) => t.id === props.selected) ?? null);
const index = computed(() => props.turns.findIndex((t) => t.id === props.selected));

function cellClass(cell: Cell): string {
  if (!cell.turns.length) return "empty";
  const classes = [actionClass(cell.turns[0])];
  if (cell.turns.length > 1) classes.push("multi");
  if (cell.turns.some((t) => t.id === props.selected)) classes.push("selected");
  if (props.flagged && cell.turns.some((t) => props.flagged!.has(t.id))) classes.push("flag");
  return classes.join(" ");
}

function cellTitle(cell: Cell): string {
  if (!cell.turns.length) return "not observed";
  const first = cell.turns[0];
  const extra = cell.turns.length > 1 ? ` (+${cell.turns.length - 1} more)` : "";
  return `${first.label} · ${clock(first.start_ms)} · ${describeAction(first)}${extra}`;
}

function step(delta: number) {
  const next = props.turns[index.value + delta];
  if (next) emit("select", next.id);
}

// Hover card, positioned inside the timeline box.
const root = ref<HTMLElement | null>(null);
const tip = ref<{ cell: Cell; x: number; y: number } | null>(null);
function showTip(cell: Cell, event: MouseEvent) {
  if (!cell.turns.length || !root.value) return;
  const box = root.value.getBoundingClientRect();
  const el = (event.currentTarget as HTMLElement).getBoundingClientRect();
  const width = 250;
  let x = el.left - box.left + el.width / 2 - width / 2;
  x = Math.max(0, Math.min(box.width - width, x));
  tip.value = { cell, x, y: el.bottom - box.top + 8 };
}
function hideTip() {
  tip.value = null;
}
const tipTurn = computed(() => tip.value?.cell.turns[0] ?? null);
</script>

<template>
  <div ref="root" class="timeline" @mouseleave="hideTip">
    <div class="tl-years"><span>Junior</span><span>Classic</span><span>Senior</span><span class="tl-finale">Finale</span></div>
    <div class="tl-cells">
      <button v-for="cell in cells.cells" :key="cell.key" type="button" class="cell" :class="[cellClass(cell), { finale: cell.finale }]" :aria-label="cellTitle(cell)" @mouseenter="showTip(cell, $event)" @mouseleave="hideTip" @focus="showTip(cell, $event as unknown as MouseEvent)" @blur="hideTip" @click="hideTip(); cell.turns.length && emit('select', cell.turns[0].id)"></button>
    </div>
    <div v-if="tip && tipTurn" class="tl-tip" :style="{ left: tip.x + 'px', top: tip.y + 'px', width: '250px' }">
      <div class="tt-title">{{ repeatsYear(tipTurn.label, tipTurn.phase) ? shortLabel(tipTurn.label) : `${yearOf(tipTurn.label, tipTurn.phase)} · ${shortLabel(tipTurn.label)}` }}</div>
      <div class="muted small">{{ clock(tipTurn.start_ms) }} · {{ describeAction(tipTurn) }}<span v-if="tip.cell.turns.length > 1"> · +{{ tip.cell.turns.length - 1 }} more window{{ tip.cell.turns.length > 2 ? "s" : "" }}</span></div>
      <div v-if="tipTurn.opening.stats" class="tt-stats">
        <span v-for="f in CORE_STATS" :key="f" :title="STAT_NAMES[f]"><i :style="{ background: `var(--stat-${f})` }"></i>{{ tipTurn.opening.stats?.[f] ?? "?" }}</span>
      </div>
      <div v-else class="muted small">opening state not observed</div>
      <div v-if="flagged?.has(tipTurn.id)" class="small warn-text" style="margin-top: 4px">something to check in this turn</div>
    </div>
    <div class="tl-now">
      <button class="btn small" :disabled="index <= 0" @click="step(-1)">‹ Previous</button>
      <div v-if="current" class="tl-current">
        <span v-if="!repeatsYear(current.label, current.phase)" class="tag">{{ yearOf(current.label, current.phase) }}</span>
        <strong>{{ shortLabel(current.label) }}</strong>
        <span class="muted">{{ clock(current.start_ms) }} · {{ describeAction(current) }}</span>
      </div>
      <button class="btn small" :disabled="index < 0 || index >= turns.length - 1" @click="step(1)">Next ›</button>
    </div>
    <div class="legend">
      <span><i class="speed"></i>Speed</span>
      <span><i class="stamina"></i>Stamina</span>
      <span><i class="power"></i>Power</span>
      <span><i class="guts"></i>Guts</span>
      <span><i class="wit"></i>Wit</span>
      <span><i class="race"></i>Race</span>
      <span><i class="rest"></i>Rest</span>
      <span><i class="outing"></i>Outing</span>
      <span><i class="missing"></i>Not Seen by the Report</span>
      <span><i class="empty"></i>Not Observed</span>
      <span><i class="flagged"></i>Something to Check</span>
      <span v-if="cells.unplaced.length" class="muted">· {{ cells.unplaced.length }} window{{ cells.unplaced.length === 1 ? "" : "s" }} without a calendar position:
        <button v-for="t in cells.unplaced" :key="t.id" class="btn quiet small" @click="emit('select', t.id)">{{ t.label }}</button>
      </span>
    </div>
  </div>
</template>
