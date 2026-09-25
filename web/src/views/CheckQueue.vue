<script setup lang="ts">
import { computed, onMounted, ref } from "vue";
import { api, type Report, type TurnSummary } from "../api";
import { clock, fullLabel } from "../format";
import { reviewSettles, turnAsks } from "../warnings";

// Everything across the viewer's reports that still asks for a look, grouped
// by run and ordered with the most open turns first. Each line opens the
// turn's review screen. The counts come from the reports' own ledgers.
interface Item {
  turn: TurnSummary;
  notes: string[];
  /** A number that does not add up or a flagged line, against a worked-out number that only wants confirming. */
  serious: boolean;
}
interface Group {
  report: Report;
  items: Item[];
  turns: number;
}
const groups = ref<Group[]>([]);
const loading = ref(true);
const error = ref("");
function item(turn: TurnSummary): Item | null {
  const asks = turnAsks(turn);
  if (!asks.serious.length && !asks.confirm.length) return null;
  return { turn, notes: [...asks.serious, ...asks.confirm], serious: asks.serious.length > 0 };
}

onMounted(async () => {
  try {
    const reports = await api.reports();
    const out: Group[] = [];
    await Promise.all(
      reports.map(async (report) => {
        try {
          const [turns, reviews] = await Promise.all([api.turns(report.id), api.corrections(report.id).catch(() => [])]);
          // A turn a saved review settles is not waiting any more.
          const settled = new Set(reviews.filter((r) => reviewSettles(r, [])).map((r) => r.correction.turn_id));
          const items = turns
            .filter((turn) => !settled.has(turn.id))
            .map(item)
            .filter((i): i is Item => i !== null)
            .sort((a, b) => Number(b.serious) - Number(a.serious));
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

const total = computed(() => groups.value.reduce((a, g) => a + g.items.filter((i) => i.serious).length, 0));
const confirms = computed(() => groups.value.reduce((a, g) => a + g.items.filter((i) => !i.serious).length, 0));
const reviewHref = (reportId: string, turnId: string) => `#/reports/${encodeURIComponent(reportId)}/${encodeURIComponent(turnId)}/review`;
</script>

<template>
  <div class="page-head">
    <div>
      <h1>Turns to Check</h1>
      <p>Turns where the numbers don't add up or a line was flagged, plus turns with a number the report worked out instead of reading. Each one opens in the review screen next to the video.</p>
    </div>
    <span v-if="!loading" class="row" style="gap: 8px">
      <span class="ask" :class="total ? 'warn' : 'ok'">{{ total ? `${total} Turn${total === 1 ? "" : "s"} to Check` : "Everything Adds Up" }}</span>
      <span v-if="confirms" class="ask mild">{{ confirms }} to Confirm</span>
    </span>
  </div>
  <p v-if="error" class="error">{{ error }}</p>
  <p v-if="loading" class="muted">Reading your reports…</p>
  <p v-else-if="!groups.length" class="muted">No reports yet. After the first analysis, turns that need checking show up here.</p>
  <div v-for="g in groups" v-else :key="g.report.id" class="queue-group">
    <div class="queue-head">
      <a class="queue-name" :href="`#/reports/${encodeURIComponent(g.report.id)}`">{{ g.report.source_name }}</a>
      <span class="muted small">{{ g.items.length }} of {{ g.turns }} turns</span>
      <span v-if="!g.items.length" class="pill ok">Adds Up</span>
    </div>
    <ul v-if="g.items.length" class="queue-list">
      <li v-for="it in g.items" :key="it.turn.id" :class="{ confirm: !it.serious }">
        <a class="queue-turn" :href="reviewHref(g.report.id, it.turn.id)"><b>{{ fullLabel(it.turn.label, it.turn.phase) }}</b><span class="muted small tabular">{{ clock(it.turn.start_ms) }}</span></a>
        <span class="queue-notes">{{ it.notes.join(" · ") }}</span>
        <a class="btn small" :href="reviewHref(g.report.id, it.turn.id)">{{ it.serious ? "Review" : "Confirm" }}</a>
      </li>
    </ul>
  </div>
</template>
