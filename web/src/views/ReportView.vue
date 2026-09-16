<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref, watch } from "vue";
import { api, type Correction, type Entry, type Summary, type Turn, type TurnSummary, type Verification } from "../api";
import { replaceHash } from "../route";
import { clock, PERFORMANCE_FIELDS, statusText, when } from "../format";
import { entryWarnings, turnWarnings } from "../warnings";
import Timeline from "../components/Timeline.vue";
import StatBar from "../components/StatBar.vue";
import VideoPanel from "../components/VideoPanel.vue";
import TurnPane from "../components/TurnPane.vue";
import GrowthPane from "../components/GrowthPane.vue";
import CheckPane from "../components/CheckPane.vue";
import RunStats from "../components/RunStats.vue";

// One run, read like a replay: the final stats up top, the season as a
// scrubber, then the recording with the turn's stats beside the turn's
// log, the growth chart and the analytics below.
const props = defineProps<{ reportId: string; turnId: string }>();
const summary = ref<Summary | null>(null);
const turns = ref<TurnSummary[]>([]);
const turn = ref<Turn | null>(null);
// The viewer's fill-in for the selected turn and the server's check of it.
const correction = ref<Correction | null>(null);
const verification = ref<Verification | null>(null);
const verifiedFields = computed(() => (verification.value?.verified ? verification.value.fields.filter((f) => f.channel === "stats" && f.status === "balanced").map((f) => f.field) : []));
const entries = ref<Entry[]>([]);
const allEntries = ref<Entry[]>([]);
const unassigned = ref<Entry[]>([]);
const finalStats = ref<Record<string, number | null> | null>(null);
const seekMs = ref<number | null>(null);
const tab = ref<"turn" | "check">("turn");
const error = ref("");
// A warning link opens a turn and then seeks to the entry once the turn is loaded.
let pendingSeek: number | null = null;
// When the viewer scrubs or plays the recording, the turn follows the video
// instead of the video following the turn.
let followingVideo = false;

const current = computed(() => props.turnId || turns.value[0]?.id || "");
const summaryTurn = computed(() => turns.value.find((t) => t.id === current.value) ?? null);

async function loadTurn(id: string) {
  if (!id) return;
  try {
    const detail = await api.turn(props.reportId, id);
    turn.value = detail.turn;
    entries.value = detail.entries;
    correction.value = detail.correction ?? null;
    verification.value = detail.verification ?? null;
    if (pendingSeek !== null) seekMs.value = pendingSeek;
    else if (!followingVideo) seekMs.value = detail.turn.start_ms;
    pendingSeek = null;
    followingVideo = false;
  } catch (e) {
    error.value = (e as Error).message;
  }
}

// The end of the run: the last turn's accounted end values, falling back to
// the last observed opening. Only what the ledger states.
async function loadFinal() {
  const last = [...turns.value].reverse().find((t) => t.opening.stats);
  if (!last) return;
  try {
    const detail = await api.turn(props.reportId, last.id);
    const out: Record<string, number | null> = {};
    for (const [field, v] of Object.entries(detail.turn.opening.stats ?? {})) {
      const after = detail.turn.accounting.stats?.[field]?.after;
      out[field] = after ?? v;
    }
    finalStats.value = out;
  } catch {
    finalStats.value = last.opening.stats;
  }
}

onMounted(async () => {
  try {
    const [s, t, u, all] = await Promise.all([api.summary(props.reportId), api.turns(props.reportId), api.unassigned(props.reportId), api.entries(props.reportId).catch(() => [] as Entry[])]);
    summary.value = s;
    turns.value = t;
    unassigned.value = u;
    allEntries.value = all;
    await Promise.all([loadTurn(current.value), loadFinal()]);
  } catch (e) {
    error.value = (e as Error).message;
  }
  window.addEventListener("keydown", onKey);
  window.addEventListener("resize", measure);
});
onUnmounted(() => {
  window.removeEventListener("keydown", onKey);
  window.removeEventListener("resize", measure);
  observer?.disconnect();
});
watch(
  () => props.turnId,
  (id) => loadTurn(id || turns.value[0]?.id || ""),
);

// The pane is as tall as the recording column, so the two sit level and the
// log scrolls inside; on narrow screens both flow naturally.
const leftColumn = ref<HTMLElement | null>(null);
const paneHeight = ref<number | null>(null);
let observer: ResizeObserver | undefined;
function measure() {
  const el = leftColumn.value;
  if (!el || window.innerWidth < 960) {
    paneHeight.value = null;
    return;
  }
  paneHeight.value = Math.max(600, Math.round(el.getBoundingClientRect().height));
}
watch(leftColumn, (el) => {
  observer?.disconnect();
  if (el && "ResizeObserver" in window) {
    observer = new ResizeObserver(measure);
    observer.observe(el);
  }
  measure();
});

function select(id: string, ms?: number | null) {
  tab.value = "turn";
  if (ms !== undefined && ms !== null) {
    if (id === current.value) {
      seekMs.value = ms;
      document.querySelector(".replay")?.scrollIntoView({ block: "start", behavior: "smooth" });
      return;
    }
    pendingSeek = ms;
  }
  replaceHash(`#/reports/${encodeURIComponent(props.reportId)}/${encodeURIComponent(id)}`);
  const replay = document.querySelector(".replay");
  if (replay && replay.getBoundingClientRect().top < 0) replay.scrollIntoView({ block: "start", behavior: "smooth" });
}

// The turn whose window holds a source time: the last turn that started
// at or before it (turns are in time order).
function turnAt(ms: number): TurnSummary | null {
  let found: TurnSummary | null = null;
  for (const t of turns.value) {
    if (t.start_ms === null) continue;
    if (t.start_ms <= ms) found = t;
    else break;
  }
  return found;
}

function onVideoTime(ms: number) {
  const t = turnAt(ms);
  if (!t || t.id === current.value) return;
  followingVideo = true;
  replaceHash(`#/reports/${encodeURIComponent(props.reportId)}/${encodeURIComponent(t.id)}`);
}

function perfAfter(field: string): number | null {
  const a = turn.value?.accounting.performance?.[field];
  return a && a.after !== null ? a.after : null;
}
function perfDelta(field: string): number | null {
  const after = perfAfter(field);
  const before = perf(field);
  return after === null || before === null ? null : after - before;
}

function step(delta: number) {
  const index = turns.value.findIndex((t) => t.id === current.value);
  const next = turns.value[index + delta];
  if (next) select(next.id);
}

function onKey(e: KeyboardEvent) {
  const target = e.target as HTMLElement | null;
  if (target && (target.tagName === "INPUT" || target.tagName === "TEXTAREA" || target.tagName === "VIDEO" || target.isContentEditable)) return;
  if (e.key === "ArrowRight" || e.key === "j") {
    e.preventDefault();
    step(1);
  } else if (e.key === "ArrowLeft" || e.key === "k") {
    e.preventDefault();
    step(-1);
  }
}

function perf(field: string): number | null {
  const map = turn.value?.opening.performance;
  if (!map || !(field in map)) return null;
  return map[field];
}

const explained = computed(() => {
  const counts = summary.value?.summary.field_status_counts ?? {};
  const total = Object.values(counts).reduce((a, b) => a + b, 0);
  const ok = (counts.balanced_observations ?? 0) + (counts.balanced_with_derived_changes ?? 0);
  return { total, ok };
});

// The timeline marks turns whose own accounting or action is in doubt; the
// tab counts every turn that has anything to look at, including flagged entries.
const flagged = computed(() => {
  const set = new Set<string>();
  for (const t of turns.value) if (turnWarnings(t).some((w) => w.serious)) set.add(t.id);
  return set;
});
const checkCount = computed(() => {
  const set = new Set<string>();
  for (const t of turns.value) if (turnWarnings(t).length) set.add(t.id);
  for (const e of allEntries.value) if (e.turn_id && entryWarnings(e).length) set.add(e.turn_id);
  return set.size + (unassigned.value.length ? 1 : 0) + (summary.value?.summary.stage_failures.length ? 1 : 0);
});
</script>

<template>
  <p v-if="error" class="error">{{ error }}</p>
  <template v-if="summary">
    <header class="run-head">
      <div class="run-title">
        <a class="back" href="#/runs">‹ Runs</a>
        <div class="overline">Career Run</div>
        <h1 :title="summary.report.source_name || summary.source.name">{{ summary.report.source_name || summary.source.name }}</h1>
        <p class="muted">
          {{ when(summary.report.created_at) }} · {{ clock(summary.source.duration_ms) }} · {{ summary.turns }} turns
          <span v-if="explained.total"> · {{ explained.ok }} of {{ explained.total }} stat changes fully explained</span>
          <span v-if="checkCount"> · <button class="linkish" @click="tab = 'check'">{{ checkCount }} thing{{ checkCount === 1 ? "" : "s" }} to check</button></span>
        </p>
        <p v-if="summary.summary.stage_failures.length" class="error small">Stages skipped during analysis: {{ summary.summary.stage_failures.map((f) => f.stage).join(", ") }}.</p>
      </div>
      <div class="run-final">
        <div class="run-final-label">At the End of the Run</div>
        <StatBar :stats="finalStats" />
      </div>
    </header>

    <Timeline :turns="turns" :selected="current" :flagged="flagged" @select="select" />

    <div class="replay">
      <div ref="leftColumn" class="replay-video">
        <VideoPanel :report-id="reportId" :ms="seekMs" :available="summary.video_available" :duration-ms="summary.source.duration_ms" @time="onVideoTime" />
        <div v-if="turn" class="under-video">
          <h3 class="pane-h" style="margin-top: 8px">Stats at the Start of the Turn</h3>
          <p v-if="!turn.opening.stats && summaryTurn?.opening_estimate" class="muted small">The stat bar was not on screen this turn (a race day). Values carried from the previous turn's entries, marked ≈.</p>
          <p v-else-if="!turn.opening.stats" class="muted small">No opening observation for this turn. Unknown values are not zeros.</p>
          <StatBar v-if="!turn.opening.stats && summaryTurn?.opening_estimate" :stats="summaryTurn.opening_estimate.stats" carried compact />
          <StatBar v-else :stats="turn.opening.stats" :after="turn.accounting.stats" :verified="verifiedFields" compact />
          <div v-if="turn.opening.performance || turn.accounting.performance" class="perf">
            <span v-for="f in PERFORMANCE_FIELDS" :key="f" class="perf-chip" :class="f" :title="`${f}: ${perf(f) ?? '?'}${perfAfter(f) !== null ? ' → ' + perfAfter(f) : ''}${turn.accounting.performance?.[f] ? ' · ' + statusText(turn.accounting.performance[f].status) : ''}`">
              <b>{{ f[0].toUpperCase() + f.slice(1, 2) }}</b><span class="pv">{{ perf(f) ?? "?" }}</span><template v-if="perfAfter(f) !== null"><span class="pa">→{{ perfAfter(f) }}</span><em v-if="perfDelta(f)" :class="perfDelta(f)! > 0 ? 'up' : 'down'">{{ perfDelta(f)! > 0 ? "+" : "" }}{{ perfDelta(f) }}</em></template>
            </span>
          </div>
        </div>
        <p class="muted small" style="margin: 10px 0 0">← and → move between turns. Any time in the log jumps the recording there. <a :href="api.downloadUrl(summary.report.id)">Download report.json</a></p>
      </div>
      <div class="replay-pane" :style="paneHeight ? { height: paneHeight + 'px' } : undefined">
        <div class="tabs">
          <button :class="{ active: tab === 'turn' }" @click="tab = 'turn'">This Turn</button>
          <button :class="{ active: tab === 'check' }" @click="tab = 'check'">Check<span v-if="checkCount" class="tab-count">{{ checkCount }}</span></button>
        </div>
        <div class="pane-body">
          <Transition name="fade" mode="out-in">
            <TurnPane v-if="tab === 'turn' && turn" :key="'turn-' + turn.id" :turn="turn" :entries="entries" :summary-turn="turns.find((t) => t.id === turn!.id) ?? null" :report-id="props.reportId" :correction="correction" :verification="verification" :video-ms="seekMs" @changed="loadTurn(current)" @seek="(ms) => (seekMs = ms)" />
            <CheckPane v-else-if="tab === 'check'" key="check" :report-id="props.reportId" :turns="turns" :entries="allEntries" :unassigned="unassigned" :stage-failures="summary.summary.stage_failures" @select="select" @seek="(ms) => (seekMs = ms)" />
          </Transition>
        </div>
      </div>
    </div>

    <section class="growth-wide">
      <GrowthPane :turns="turns" :selected="current" @select="select" />
    </section>

    <section class="growth-wide">
      <div class="overline" style="margin-bottom: 14px">How the Run Was Played</div>
      <RunStats :turns="turns" :entries="allEntries" :final="finalStats" @select="select" />
    </section>
  </template>
</template>
