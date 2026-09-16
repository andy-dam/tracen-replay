<script setup lang="ts">
import { computed, onMounted, ref } from "vue";
import { api, type Report, type TurnSummary } from "../api";
import { clock, fullLabel } from "../format";
import { turnWarnings } from "../warnings";

// Everything across the viewer's reports that still asks for a look, grouped
// by run and ordered with the most open turns first. Each line opens the
// turn's review screen. The counts come from the reports' own ledgers.
interface Item {
  turn: TurnSummary;
  notes: string[];
}
interface Group {
  report: Report;
  items: Item[];
  turns: number;
}
const groups = ref<Group[]>([]);
const loading = ref(true);
const error = ref("");
const DIFF_LABEL: Record<string, string> = { speed: "Speed", stamina: "Stamina", power: "Power", guts: "Guts", wit: "Wit", skill_points: "Skill Pts", dance: "Dance", passion: "Passion", vocal: "Vocal", visual: "Visual", composure: "Composure" };

function needs(t: TurnSummary): string[] {
  const notes = turnWarnings(t).filter((w) => w.serious).map((w) => w.text);
  for (const d of t.differences ?? []) if (!d.worked_out) notes.push(`${DIFF_LABEL[d.field] ?? d.field} ${d.amount > 0 ? "+" : ""}${d.amount} not covered by any event`);
  return notes;
}

onMounted(async () => {
  try {
    const reports = await api.reports();
    const out: Group[] = [];
    await Promise.all(
      reports.map(async (report) => {
        try {
          const turns = await api.turns(report.id);
          const items = turns.map((turn) => ({ turn, notes: needs(turn) })).filter((i) => i.notes.length);
          out.push({ report, items, turns: turns.length });
        } catch {
          // a report that cannot be listed is left out of the queue
        }
      }),
    );
    groups.value = out.sort((a, b) => b.items.length - a.items.length || b.report.created_at.localeCompare(a.report.created_at));
  } catch (e) {
    error.value = (e as Error).message;
  } finally {
    loading.value = false;
  }
});

const total = computed(() => groups.value.reduce((a, g) => a + g.items.length, 0));
const reviewHref = (reportId: string, turnId: string) => `#/reports/${encodeURIComponent(reportId)}/${encodeURIComponent(turnId)}/review`;
</script>

<template>
  <div class="page-head">
    <div>
      <h1>To check</h1>
      <p>Every turn across your runs where a number does not add up or a line was flagged. Each one opens the review screen with the recording beside it.</p>
    </div>
    <span v-if="!loading" class="ask" :class="total ? 'warn' : 'ok'">{{ total ? `${total} turn${total === 1 ? "" : "s"} to check` : "Everything adds up" }}</span>
  </div>
  <p v-if="error" class="error">{{ error }}</p>
  <p v-if="loading" class="muted">Reading your reports…</p>
  <p v-else-if="!groups.length" class="muted">No reports yet. Upload a recording under Runs and the turns that need a look will collect here.</p>
  <div v-for="g in groups" v-else :key="g.report.id" class="queue-group">
    <div class="queue-head">
      <a class="queue-name" :href="`#/reports/${encodeURIComponent(g.report.id)}`">{{ g.report.source_name }}</a>
      <span class="muted small">{{ g.items.length }} of {{ g.turns }} turns</span>
      <span v-if="!g.items.length" class="pill ok">adds up</span>
    </div>
    <ul v-if="g.items.length" class="queue-list">
      <li v-for="it in g.items" :key="it.turn.id">
        <a class="queue-turn" :href="reviewHref(g.report.id, it.turn.id)"><b>{{ fullLabel(it.turn.label, it.turn.phase) }}</b><span class="muted small tabular">{{ clock(it.turn.start_ms) }}</span></a>
        <span class="queue-notes">{{ it.notes.join(" · ") }}</span>
        <a class="btn small" :href="reviewHref(g.report.id, it.turn.id)">Review</a>
      </li>
    </ul>
  </div>
</template>
