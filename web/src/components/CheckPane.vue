<script setup lang="ts">
import { computed } from "vue";
import type { Entry, TurnSummary } from "../api";
import { clock, fullLabel } from "../format";
import { entryName, entryWarnings, turnWarnings } from "../warnings";
import EntryList from "./EntryList.vue";

// The index of everything the report states with a caveat, grouped by turn.
// Every line is a link: a turn line opens the turn, an entry line opens the
// turn and seeks the recording to that entry.
const props = defineProps<{ reportId: string; turns: TurnSummary[]; entries: Entry[]; unassigned: Entry[]; stageFailures: { stage: string; error: string }[] }>();
const reviewHref = (id: string) => `#/reports/${encodeURIComponent(props.reportId)}/${encodeURIComponent(id)}/review`;
const emit = defineEmits<{ select: [id: string, ms?: number | null]; seek: [ms: number] }>();
const OWNER_WORD: Record<string, string> = { training: "training", event: "event whose number was cut off", lesson: "lesson", race: "race" };
const DIFF_LABEL: Record<string, string> = { speed: "Speed", stamina: "Stamina", power: "Power", guts: "Guts", wit: "Wit", skill_points: "Skill Pts", dance: "Dance", passion: "Passion", vocal: "Vocal", visual: "Visual", composure: "Composure" };

interface Item {
  entry: Entry;
  name: string;
  notes: string[];
  serious: boolean;
}

const groups = computed(() => {
  const byTurn = new Map<string, Item[]>();
  for (const e of props.entries) {
    if (!e.turn_id) continue;
    const w = entryWarnings(e);
    if (!w.length) continue;
    const list = byTurn.get(e.turn_id) ?? [];
    list.push({ entry: e, name: entryName(e), notes: w.map((x) => x.text), serious: w.some((x) => x.serious) });
    byTurn.set(e.turn_id, list);
  }
  return props.turns
    .map((t) => {
      const tw = turnWarnings(t);
      const diffs = (t.differences ?? []).map((d) => `${DIFF_LABEL[d.field] ?? d.field} ${d.amount > 0 ? "+" : ""}${d.amount} ${d.worked_out ? `worked out onto the ${OWNER_WORD[d.owner ?? ""] ?? "only possible event"}` : "not covered by any event"}${d.window_start_ms != null && d.window_end_ms != null ? ` between ${clock(d.window_start_ms)} and ${clock(d.window_end_ms)}` : ""}`);
      return { turn: t, notes: [...tw.map((x) => x.text), ...diffs], serious: tw.some((x) => x.serious) || (t.differences ?? []).some((d) => !d.worked_out), items: byTurn.get(t.id) ?? [] };
    })
    .filter((g) => g.notes.length || g.items.length);
});

const totals = computed(() => {
  let turns = 0;
  let entries = 0;
  for (const g of groups.value) {
    if (g.notes.length) turns++;
    entries += g.items.length;
  }
  return { turns, entries };
});
</script>

<template>
  <div class="pane-inner">
    <div class="pane-fixed">
      <p class="muted small" style="margin: 4px 0 6px">
        Every caveat in the report: {{ totals.turns }} turn{{ totals.turns === 1 ? "" : "s" }} with accounting or observation gaps, {{ totals.entries }} entr{{ totals.entries === 1 ? "y" : "ies" }} the analyzer flagged, {{ unassigned.length }} outside every turn.
        Click a line to go there.
      </p>
    </div>
    <div class="pane-scroll">
      <div v-if="stageFailures.length" class="warnbox">
        <b>Stages Skipped During Analysis</b>
        <div v-for="f in stageFailures" :key="f.stage" class="small">{{ f.stage }}: {{ f.error }}</div>
      </div>

      <h3 class="pane-h" style="margin-top: 6px">By Turn</h3>
      <p v-if="!groups.length" class="muted small">Nothing to check: every turn's accounting is balanced, every opening was observed and no entry was flagged.</p>
      <ul v-else class="checklist">
        <li v-for="g in groups" :key="g.turn.id">
          <div class="check-turn">
            <button class="linkish strong" @click="emit('select', g.turn.id)">{{ fullLabel(g.turn.label, g.turn.phase) }}</button>
            <span class="muted small">{{ clock(g.turn.start_ms) }}</span>
            <span v-if="g.serious" class="tag pink">check</span>
            <a class="btn small" style="margin-left: auto" :href="reviewHref(g.turn.id)">Review</a>
          </div>
          <div v-if="g.notes.length" class="small" :class="g.serious ? 'warn-text' : 'muted'">{{ g.notes.join(" · ") }}</div>
          <ul v-if="g.items.length" class="check-items">
            <li v-for="it in g.items" :key="it.entry.id">
              <button class="linkish" @click="emit('select', g.turn.id, it.entry.first_seen_ms)"><span class="tabular">{{ clock(it.entry.first_seen_ms) }}</span> {{ it.name }}</button>
              <span class="small" :class="it.serious ? 'warn-text' : 'muted'"> — {{ it.notes.join("; ") }}</span>
            </li>
          </ul>
        </li>
      </ul>

      <h3 class="pane-h">Outside Every Observed Turn</h3>
      <p class="muted small">Seen at times no turn window covers. They stay separate; they are never folded into a neighbouring turn.</p>
      <EntryList :entries="unassigned" @seek="(ms) => emit('seek', ms)" />
    </div>
  </div>
</template>
