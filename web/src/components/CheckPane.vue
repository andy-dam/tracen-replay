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

interface Note {
  text: string;
  serious: boolean;
}

interface Group {
  turn: TurnSummary;
  notes: Note[];
  items: Item[];
}

// Every caveat, by turn. A serious one is a number that does not add up or
// a line the report is not sure of; the rest are things the report is
// confident about and says anyway, so nothing is hidden.
const groups = computed<Group[]>(() => {
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
      const notes: Note[] = turnWarnings(t).map((x) => ({ text: x.text, serious: x.serious }));
      for (const d of t.differences ?? []) {
        const text = `${DIFF_LABEL[d.field] ?? d.field} ${d.amount > 0 ? "+" : ""}${d.amount} ${d.worked_out ? `worked out onto the ${OWNER_WORD[d.owner ?? ""] ?? "only possible event"}` : "not covered by any event"}${d.window_start_ms != null && d.window_end_ms != null ? ` between ${clock(d.window_start_ms)} and ${clock(d.window_end_ms)}` : ""}`;
        notes.push({ text, serious: !d.worked_out });
      }
      return { turn: t, notes, items: byTurn.get(t.id) ?? [] };
    })
    .filter((g) => g.notes.length || g.items.length);
});

function part(serious: boolean): Group[] {
  return groups.value
    .map((g) => ({ turn: g.turn, notes: g.notes.filter((n) => n.serious === serious), items: g.items.filter((it) => it.serious === serious) }))
    .filter((g) => g.notes.length || g.items.length);
}
const checks = computed(() => part(true));
const notes = computed(() => part(false));
const noteCount = computed(() => notes.value.reduce((n, g) => n + g.notes.length + g.items.length, 0));

// Entries before the first turn are the run's inheritance and starting
// hints; the rest sit in gaps between observed windows.
const firstStart = computed(() => props.turns.find((t) => t.start_ms !== null)?.start_ms ?? null);
const beforeStart = computed(() => props.unassigned.filter((e) => firstStart.value !== null && e.first_seen_ms !== null && e.first_seen_ms < firstStart.value));
const outside = computed(() => props.unassigned.filter((e) => !beforeStart.value.includes(e)));

</script>

<template>
  <div class="pane-inner">
    <div class="pane-fixed">
      <p class="muted small" style="margin: 4px 0 6px">
        <template v-if="checks.length">{{ checks.length }} turn{{ checks.length === 1 ? "" : "s" }} to check: a number that does not add up, or a line the report is not sure of.</template>
        <template v-else>Nothing to check: every number adds up and no line is in doubt.</template>
        <template v-if="noteCount">{{ " " }}{{ noteCount }} note{{ noteCount === 1 ? "" : "s" }} the report is confident about {{ noteCount === 1 ? "is" : "are" }} kept below, so nothing is hidden.</template>
        <template v-if="unassigned.length">{{ " " }}{{ unassigned.length }} entr{{ unassigned.length === 1 ? "y sits" : "ies sit" }} outside every turn.</template>
        {{ " " }}Click a line to go there.
      </p>
    </div>
    <div class="pane-scroll">
      <div v-if="stageFailures.length" class="warnbox">
        <b>Stages Skipped During Analysis</b>
        <div v-for="f in stageFailures" :key="f.stage" class="small">{{ f.stage }}: {{ f.error }}</div>
      </div>

      <h3 class="pane-h" style="margin-top: 6px">To Check</h3>
      <p v-if="!checks.length" class="muted small">Nothing to check: every number adds up, every line the report kept is one it is sure of.</p>
      <ul v-else class="checklist">
        <li v-for="g in checks" :key="g.turn.id">
          <div class="check-turn">
            <button class="linkish strong" @click="emit('select', g.turn.id)">{{ fullLabel(g.turn.label, g.turn.phase) }}</button>
            <span class="muted small">{{ clock(g.turn.start_ms) }}</span>
            <span class="tag pink">check</span>
            <a class="btn small" style="margin-left: auto" :href="reviewHref(g.turn.id)">Review</a>
          </div>
          <div v-if="g.notes.length" class="small warn-text">{{ g.notes.map((n) => n.text).join(" · ") }}</div>
          <ul v-if="g.items.length" class="check-items">
            <li v-for="it in g.items" :key="it.entry.id">
              <button class="linkish" @click="emit('select', g.turn.id, it.entry.first_seen_ms)"><span class="tabular">{{ clock(it.entry.first_seen_ms) }}</span> {{ it.name }}</button>
              <span class="small warn-text"> — {{ it.notes.join("; ") }}</span>
            </li>
          </ul>
        </li>
      </ul>

      <details v-if="noteCount" class="notes-fold">
        <summary><span class="pane-h">Notes</span> <span class="muted small">{{ noteCount }} · what the report is confident about and says anyway; nothing to do</span></summary>
        <ul class="checklist">
          <li v-for="g in notes" :key="g.turn.id">
            <div class="check-turn">
              <button class="linkish strong" @click="emit('select', g.turn.id)">{{ fullLabel(g.turn.label, g.turn.phase) }}</button>
              <span class="muted small">{{ clock(g.turn.start_ms) }}</span>
              <a class="btn small" style="margin-left: auto" :href="reviewHref(g.turn.id)">Review</a>
            </div>
            <div v-if="g.notes.length" class="small muted">{{ g.notes.map((n) => n.text).join(" · ") }}</div>
            <ul v-if="g.items.length" class="check-items">
              <li v-for="it in g.items" :key="it.entry.id">
                <button class="linkish" @click="emit('select', g.turn.id, it.entry.first_seen_ms)"><span class="tabular">{{ clock(it.entry.first_seen_ms) }}</span> {{ it.name }}</button>
                <span class="small muted"> — {{ it.notes.join("; ") }}</span>
              </li>
            </ul>
          </li>
        </ul>
      </details>

      <template v-if="beforeStart.length">
        <h3 class="pane-h">Before the Career Starts</h3>
        <p class="muted small">The inheritance and the starting hints the game shows before the first turn. They belong to the run, not to any turn, and need no check.</p>
        <EntryList :entries="beforeStart" @seek="(ms) => emit('seek', ms)" />
      </template>
      <template v-if="outside.length">
        <h3 class="pane-h">Outside Every Observed Turn</h3>
        <p class="muted small">Seen at times no turn window covers. They stay separate; they are never folded into a neighbouring turn.</p>
        <EntryList :entries="outside" @seek="(ms) => emit('seek', ms)" />
      </template>
    </div>
  </div>
</template>

<style scoped>
.notes-fold {
  margin-top: 16px;
}
.notes-fold > summary {
  cursor: pointer;
  list-style: none;
  display: flex;
  align-items: baseline;
  gap: 8px;
}
.notes-fold > summary::-webkit-details-marker {
  display: none;
}
.notes-fold > summary::before {
  content: "▸";
  opacity: 0.6;
}
.notes-fold[open] > summary::before {
  content: "▾";
}
</style>
