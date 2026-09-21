<script setup lang="ts">
import { computed, onMounted, ref, watch } from "vue";
import { api, type Correction, type Entry, type Summary, type Turn, type TurnSummary, type Verification } from "../api";
import { replaceHash } from "../route";
import { clock, fullLabel, PERFORMANCE_FIELDS } from "../format";
import { committedAction, entryWarnings, turnWarnings } from "../warnings";
import CorrectionForm from "../components/CorrectionForm.vue";
import StatBar from "../components/StatBar.vue";
import VideoPanel from "../components/VideoPanel.vue";

// The review screen for one turn: the recording and the turn's stats stay in
// view on the left while the guided editor takes the whole right column. The
// report page only links here; nothing is edited inside its panes.
const props = defineProps<{ reportId: string; turnId: string }>();
const summary = ref<Summary | null>(null);
const turns = ref<TurnSummary[]>([]);
const turn = ref<Turn | null>(null);
const entries = ref<Entry[]>([]);
const correction = ref<Correction | null>(null);
const verification = ref<Verification | null>(null);
const seekMs = ref<number | null>(null);
const error = ref("");

const summaryTurn = computed(() => turns.value.find((t) => t.id === props.turnId) ?? null);
const index = computed(() => turns.value.findIndex((t) => t.id === props.turnId));
const prev = computed(() => (index.value > 0 ? turns.value[index.value - 1] : null));
const next = computed(() => (index.value >= 0 && index.value < turns.value.length - 1 ? turns.value[index.value + 1] : null));
const reportAction = computed(() => committedAction(entries.value));
const hasAction = computed(() => reportAction.value !== null);
const reportHref = computed(() => `#/reports/${encodeURIComponent(props.reportId)}/${encodeURIComponent(props.turnId)}`);
const reviewHref = (id: string) => `#/reports/${encodeURIComponent(props.reportId)}/${encodeURIComponent(id)}/review`;

// What this turn asks of the reviewer, counted for the heading.
const asks = computed(() => {
  const t = summaryTurn.value;
  const gaps = (t?.differences ?? []).length;
  const flaggedEntries = entries.value.filter((e) => entryWarnings(e).some((w) => w.serious)).length;
  const notes = entries.value.filter((e) => entryWarnings(e).length && !entryWarnings(e).some((w) => w.serious)).length;
  const noAction = !hasAction.value;
  return { gaps, flaggedEntries, notes, noAction, turnNotes: t ? turnWarnings(t) : [] };
});

async function loadTurn() {
  try {
    const detail = await api.turn(props.reportId, props.turnId);
    turn.value = detail.turn;
    entries.value = detail.entries;
    correction.value = detail.correction ?? null;
    verification.value = detail.verification ?? null;
    seekMs.value = detail.turn.start_ms;
  } catch (e) {
    error.value = (e as Error).message;
  }
}
onMounted(async () => {
  try {
    const [s, t] = await Promise.all([api.summary(props.reportId), api.turns(props.reportId)]);
    summary.value = s;
    turns.value = t;
    await loadTurn();
  } catch (e) {
    error.value = (e as Error).message;
  }
});
watch(() => props.turnId, loadTurn);

function perf(field: string): number | null {
  const map = turn.value?.opening.performance;
  return map && field in map ? map[field] : null;
}
function perfAfter(field: string): number | null {
  const a = turn.value?.accounting.performance?.[field];
  return a && a.after !== null ? a.after : null;
}
</script>

<template>
  <p v-if="error" class="error">{{ error }}</p>
  <template v-if="summary && turn">
    <header class="review-head">
      <div>
        <a class="back" :href="reportHref">‹ Back to the Report</a>
        <div class="row" style="gap: 8px; margin-top: 8px">
          <span class="overline" style="margin: 0">Check a Turn</span>
          <span v-if="turn.scheduled_race" class="tag pink">{{ turn.scheduled_race }}</span>
        </div>
        <h1>{{ fullLabel(turn.label, turn.phase) }}</h1>
        <p class="muted">
          <button v-if="turn.start_ms !== null" class="linkish" @click="seekMs = turn.start_ms">{{ clock(turn.start_ms) }}</button><span v-else>?</span> to {{ clock(turn.end_ms) }} ·
          {{ summary.report.source_name || summary.source.name }}
        </p>
      </div>
      <nav class="review-nav">
        <button class="btn small" :disabled="!prev" @click="prev && replaceHash(reviewHref(prev.id))">‹ Previous Turn</button>
        <button class="btn small" :disabled="!next" @click="next && replaceHash(reviewHref(next.id))">Next Turn ›</button>
      </nav>
    </header>

    <div class="review-grid">
      <aside class="review-side">
        <VideoPanel :report-id="reportId" :ms="seekMs" :available="summary.video_available" :duration-ms="summary.source.duration_ms" />
        <h3 class="pane-h" style="margin-top: 10px">Stats at the Start of the Turn</h3>
        <p v-if="!turn.opening.stats" class="muted small">No opening observation for this turn. Unknown values are not zeros.</p>
        <StatBar :stats="turn.opening.stats" :after="turn.accounting.stats" compact />
        <div v-if="turn.opening.performance || turn.accounting.performance" class="perf">
          <span v-for="f in PERFORMANCE_FIELDS" :key="f" class="perf-chip" :class="f">
            <b>{{ f[0].toUpperCase() + f.slice(1, 2) }}</b><span class="pv">{{ perf(f) ?? "?" }}</span><template v-if="perfAfter(f) !== null"><span class="pa">→{{ perfAfter(f) }}</span></template>
          </span>
        </div>
        <div class="howto">
          <b>How This Works</b>
          <ol>
            <li>Each gap is a number that doesn't add up between two turns.</li>
            <li>A ▶ chip seeks the video to the gap. The options under it explain the change.</li>
            <li>Action Played This Turn sets the action when the report saw none. Looks Right confirms a line. Events Missing from the Log adds what the report has no entry for.</li>
            <li>The badge at the top turns green once the turn adds up. Save stores the review.</li>
          </ol>
        </div>
      </aside>

      <section class="review-main">
        <div class="review-asks">
          <span v-if="asks.gaps" class="ask warn">{{ asks.gaps }} number{{ asks.gaps === 1 ? "" : "s" }} that don't add up</span>
          <span v-if="asks.noAction" class="ask warn">what was played is missing</span>
          <span v-if="asks.flaggedEntries" class="ask warn">{{ asks.flaggedEntries }} line{{ asks.flaggedEntries === 1 ? "" : "s" }} to check</span>
          <span v-if="asks.notes" class="ask">{{ asks.notes }} note{{ asks.notes === 1 ? "" : "s" }}</span>
          <span v-if="!asks.gaps && !asks.noAction && !asks.flaggedEntries" class="ask ok">Nothing to Check in This Turn</span>
        </div>
        <ul v-if="asks.turnNotes.length" class="review-turn-notes">
          <li v-for="(w, i) in asks.turnNotes" :key="i" :class="{ serious: w.serious }">{{ w.text }}</li>
        </ul>
        <CorrectionForm :report-id="reportId" :turn="turn" :summary-turn="summaryTurn" :entries="entries" :has-action="hasAction" :report-action="reportAction" :correction="correction" :verification="verification" :video-ms="seekMs" guided @saved="loadTurn" @seek="(ms) => (seekMs = ms)" />
      </section>
    </div>
  </template>
</template>
