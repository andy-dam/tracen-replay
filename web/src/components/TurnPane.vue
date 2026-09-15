<script setup lang="ts">
import { computed, ref, watch } from "vue";
import type { Correction, Entry, Turn, TurnSummary, Verification } from "../api";
import { clock, repeatsYear, shortLabel, yearOf } from "../format";
import { entryName, entryWarnings, turnWarnings } from "../warnings";
import EntryList from "./EntryList.vue";

// The turn's decision, its caveats and its log. The stats of the turn sit
// under the recording, beside this pane.
const props = defineProps<{ turn: Turn; entries: Entry[]; summaryTurn: TurnSummary | null; reportId: string; correction: Correction | null; verification: Verification | null; videoMs?: number | null }>();
const emit = defineEmits<{ seek: [ms: number]; changed: [] }>();
// Reviewing happens on its own screen; the pane only points there.
const reviewHref = computed(() => `#/reports/${encodeURIComponent(props.reportId)}/${encodeURIComponent(props.turn.id)}/review`);
function editEntry() {
  window.location.hash = reviewHref.value;
}
const differences = computed(() => props.summaryTurn?.differences ?? []);
const OWNER_WORD: Record<string, string> = { training: "training", event: "event whose number was cut off", lesson: "lesson", race: "race" };
function ownerWord(owner?: string) {
  return OWNER_WORD[owner ?? ""] ?? "only event that could have caused it";
}
const DIFF_LABEL: Record<string, string> = { speed: "Speed", stamina: "Stamina", power: "Power", guts: "Guts", wit: "Wit", skill_points: "Skill Pts", dance: "Dance", passion: "Passion", vocal: "Vocal", visual: "Visual", composure: "Composure" };
const filled = computed(() => {
  const c = props.correction;
  if (!c) return null;
  const parts: string[] = [];
  if (c.action) {
    const kind = c.action.kind === "training" ? `${(c.action.training_option ?? "")[0]?.toUpperCase()}${(c.action.training_option ?? "").slice(1)} Training` : c.action.kind[0].toUpperCase() + c.action.kind.slice(1);
    parts.push(c.action.name ? `${kind}: ${c.action.name}` : kind);
    const gains = Object.entries(c.action.gains ?? {}).map(([f, n]) => `${f.replace("_", " ")} ${n > 0 ? "+" : ""}${n}`);
    if (gains.length) parts.push(gains.join(", "));
  }
  for (const ch of c.changes) parts.push(`${ch.field.replace("_", " ")} ${ch.amount > 0 ? "+" : ""}${ch.amount}${ch.note ? ` (${ch.note})` : ""}`);
  const v = props.verification;
  const state = !v ? "na" : v.verified ? "ok" : v.balanced ? "na" : "off";
  return { text: parts.join(" · "), state, summary: v?.summary ?? "" };
});

// The decision of the turn, in the ledger's own words: the first committed
// action that is not the scheduled race.
const action = computed(() => {
  const actions = props.entries.filter((e) => e.kind === "committed_action");
  const chosen = actions.find((e) => e.action_kind !== "race") ?? actions[0];
  if (!chosen) return null;
  const d = (chosen.detail ?? {}) as Record<string, unknown>;
  const name = [d.training_name, d.name, d.race_name, d.title].find((v) => typeof v === "string" && v) as string | undefined;
  const option = chosen.training_option ?? (typeof d.training_option === "string" ? d.training_option : "");
  let text = chosen.action_kind === "training" ? `${option ? option[0].toUpperCase() + option.slice(1) : "Unknown"} Training` : (chosen.action_kind ?? "action").replaceAll("_", " ");
  if (name && chosen.action_kind !== "training") text = `${text[0].toUpperCase() + text.slice(1)}: ${name}`;
  return { text, ms: chosen.first_seen_ms, more: actions.length - 1, option: chosen.action_kind === 'training' ? option : '', kind: chosen.action_kind ?? '' };
});

// The caveats of this window: the turn's own, then each flagged entry.
// Collapsed to three lines until asked.
const showAll = ref(false);
watch(() => props.turn.id, () => (showAll.value = false));
const warnings = computed(() => {
  let turnNotes = props.summaryTurn ? turnWarnings(props.summaryTurn) : [];
  if (props.correction?.action && props.verification?.verified)
    turnNotes = turnNotes.map((w) => (w.text === "no action seen in this window" ? { text: "no action seen by the report; filled in by you and verified", serious: false } : w));
  const items = props.entries
    .map((e) => ({ entry: e, name: entryName(e), notes: entryWarnings(e) }))
    .filter((x) => x.notes.length)
    .sort((a, b) => Number(b.notes.some((w) => w.serious)) - Number(a.notes.some((w) => w.serious)));
  const serious = turnNotes.some((w) => w.serious) || items.some((x) => x.notes.some((w) => w.serious));
  return { turnNotes, items, serious, total: turnNotes.length + items.length };
});
const shownItems = computed(() => (showAll.value ? warnings.value.items : warnings.value.items.slice(0, Math.max(0, 3 - warnings.value.turnNotes.length))));
const hiddenCount = computed(() => warnings.value.items.length - shownItems.value.length);
</script>

<template>
  <div class="pane-inner">
    <div class="pane-fixed">
      <div class="turn-head">
        <div class="row" style="gap: 8px">
          <span v-if="!repeatsYear(turn.label, turn.phase)" class="tag">{{ yearOf(turn.label, turn.phase) }}</span>
          <span v-if="turn.scheduled_race" class="tag pink">{{ turn.scheduled_race }}</span>
        </div>
        <h2>{{ shortLabel(turn.label) }}</h2>
        <p class="muted small" style="margin-bottom: 0">
          <button v-if="turn.start_ms !== null" class="linkish" @click="emit('seek', turn.start_ms)">{{ clock(turn.start_ms) }}</button><span v-else>?</span>
          to {{ clock(turn.end_ms) }}
          <span v-if="turn.window_kind !== 'calendar_turn' && turn.window_kind !== 'phase_race_turn' && turn.window_kind !== 'countdown_segment'"> · window: {{ turn.window_kind.replaceAll("_", " ") }}, its start is the time the state was observed</span>
        </p>
      </div>
      <div class="turn-action" :class="[{ none: !action }, action?.option ? 'opt-' + action.option : '', action?.kind && action.kind !== 'training' ? 'kind-' + action.kind : '']">
        <template v-if="action">
          <span class="ta-label">Chose</span>
          <button class="linkish strong" :disabled="action.ms === null" @click="action.ms !== null && emit('seek', action.ms)">{{ action.text }}</button>
          <span v-if="action.more > 0" class="muted small">and {{ action.more }} more action{{ action.more === 1 ? "" : "s" }}</span>
        </template>
        <template v-else-if="filled && correction?.action">
          <span class="ta-label">Filled in</span>
          <span class="strong">{{ filled.text.split(" · ")[0] }}</span>
          <span class="muted small">{{ filled.state === "ok" ? "verified against the next turn" : filled.state === "off" ? "does not add up" : "not checkable" }}</span>
        </template>
        <template v-else>
          <span class="ta-label">Chose</span>
          <span class="muted">{{ summaryTurn && !summaryTurn.expects_one_action ? "no decision expected in this window" : "no action was seen in this window" }}</span>
        </template>
      </div>
    </div>

    <div class="pane-scroll">
      <div class="review-cta" :class="filled ? filled.state : warnings.serious || differences.length ? 'needs' : 'clear'">
        <div class="review-cta-text">
          <template v-if="filled">
            <b><span class="pill" :class="filled.state">{{ filled.state === "ok" ? "Reviewed · adds up" : filled.state === "off" ? "Reviewed · does not add up" : "Reviewed · not checkable" }}</span></b>
            <span class="small">{{ filled.text }}</span>
          </template>
          <template v-else-if="differences.length || warnings.serious">
            <b>{{ differences.length ? `${differences.length} stat gap${differences.length === 1 ? "" : "s"}` : "" }}{{ differences.length && warnings.items.length ? " · " : "" }}{{ warnings.items.length ? `${warnings.items.length} flagged event${warnings.items.length === 1 ? "" : "s"}` : "" }}</b>
            <span class="small muted">Open the review to see what each one means and how to settle it, with the recording beside you.</span>
          </template>
          <template v-else>
            <b>Nothing flagged in this turn</b>
            <span class="small muted">You can still correct an amount or add an event the report missed.</span>
          </template>
        </div>
        <a class="btn" :class="filled ? '' : 'primary'" :href="reviewHref">{{ filled ? "Edit review" : "Review this turn" }}</a>
      </div>
      <div v-if="differences.length" class="warnbox diffbox">
        <b>Differences Between Turns <span class="muted small" style="font-weight: 700">{{ differences.length }}</span></b>
        <div v-for="d in differences" :key="d.channel + d.field" class="small">
          <b>{{ DIFF_LABEL[d.field] ?? d.field }} {{ d.amount > 0 ? '+' : '' }}{{ d.amount }}</b>
          <template v-if="d.worked_out"> worked out from the difference between turns onto the {{ ownerWord(d.owner) }} (check it)</template>
          <template v-else> not covered by any captured event</template>
          <template v-if="d.window_start_ms != null && d.window_end_ms != null"> · <button class="linkish" @click="emit('seek', d.window_start_ms!)">look between {{ clock(d.window_start_ms) }} and {{ clock(d.window_end_ms) }}</button></template>
        </div>
      </div>
      <div v-if="warnings.total" class="warnbox" :class="{ serious: warnings.serious }">
        <b>{{ warnings.serious ? "Check This Turn" : "Notes on This Turn" }} <span class="muted small" style="font-weight: 700">{{ warnings.total }}</span>
          <button v-if="hiddenCount > 0 || showAll" class="more" @click="showAll = !showAll">{{ showAll ? "Show fewer" : `Show all ${warnings.total}` }}</button>
        </b>
        <div v-for="(w, i) in warnings.turnNotes" :key="'t' + i" class="small">{{ w.text }}</div>
        <div v-for="it in shownItems" :key="it.entry.id" class="small">
          <button class="linkish" :disabled="it.entry.first_seen_ms === null" @click="it.entry.first_seen_ms !== null && emit('seek', it.entry.first_seen_ms)"><span class="tabular">{{ clock(it.entry.first_seen_ms) }}</span> {{ it.name }}</button>
          — {{ it.notes.map((w) => w.text).join("; ") }}
        </div>
      </div>

      <h3 class="pane-h">Log <span class="muted" style="font-weight: 700">{{ entries.length }}</span></h3>
      <EntryList :entries="entries" :edits="correction?.entries ?? null" @seek="(ms) => emit('seek', ms)" @edit="editEntry" />
    </div>
  </div>
</template>
