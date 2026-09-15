<script setup lang="ts">
import { computed, ref, watch } from "vue";
import type { Entry, EntryEdit } from "../api";
import { basisClass, clock, signed } from "../format";
import { entryWarnings } from "../warnings";

// The log of a window. Long windows get a row of kind filters so a reader
// can see just the purchases, or just the outcomes, without losing the
// order; rows stay compact and every time seeks the recording. Changes are
// shown in two groups: the five stats and skill points first, then the
// scenario's own points (dance, passion, ...).
const props = defineProps<{ entries: Entry[]; edits?: Record<string, EntryEdit> | null }>();
const emit = defineEmits<{ seek: [ms: number]; edit: [id: string] }>();
const open = ref<string | null>(null);
const filter = ref("all");

type Detail = Record<string, unknown>;
interface Effect {
  kind?: string;
  field?: string;
  name?: string;
  amount?: number | null;
  raw_text?: string;
}

const KIND_NAMES: Record<string, string> = {
  committed_action: "Actions",
  training: "Trainings",
  outcome: "Outcomes",
  lesson_purchases: "Lessons",
  song_acquisitions: "Songs",
  races: "Races",
  concerts: "Concerts",
  skill_purchases: "Skills",
  skill_purchase_batch: "Skills",
  skill_hint_change: "Hints",
  dialogue_choices: "Choices",
  ambiguous_effect: "Ambiguous",
  unparsed_receipt: "Unparsed",
};
const CORE = new Set(["speed", "stamina", "power", "guts", "wit", "skill_points"]);
const CORE_ORDER = ["speed", "stamina", "power", "guts", "wit", "skill_points"];

function kindName(kind: string): string {
  return KIND_NAMES[kind] ?? kind.replaceAll("_", " ").replace(/^./, (c) => c.toUpperCase());
}

const groups = computed(() => {
  const counts = new Map<string, number>();
  for (const e of props.entries) counts.set(kindName(e.kind), (counts.get(kindName(e.kind)) ?? 0) + 1);
  return [...counts.entries()].sort((a, b) => b[1] - a[1]);
});

const shown = computed(() => (filter.value === "all" ? props.entries : props.entries.filter((e) => kindName(e.kind) === filter.value)));

// A new window resets the filter; a filter that no longer matches anything would hide the whole log.
watch(
  () => props.entries,
  () => {
    filter.value = "all";
    open.value = null;
  },
);

function str(v: unknown): string {
  return typeof v === "string" ? v : "";
}
function num(v: unknown): number | null {
  return typeof v === "number" ? v : null;
}
function words(v: unknown): string {
  return str(v).replaceAll("_", " ");
}
function detail(e: Entry): Detail {
  return (e.detail ?? {}) as Detail;
}

/** The record's own name; nothing is inferred beyond what the entry carries. */
function title(e: Entry): string {
  const d = detail(e);
  const option = e.training_option ?? str(d.training_option);
  const name = str(d.training_name) || str(d.name) || str(d.race_name) || str(d.title) || e.context_title || "";
  switch (e.kind) {
    case "committed_action":
      return `${name || words(e.action_kind) || "action"}${option ? ` (${option})` : ""}`;
    case "training":
      return `${name || "training"}${option ? ` (${option})` : ""}`;
    case "skill_purchases":
    case "skill_purchase_batch": {
      const spent = num(d.spent_skill_points);
      return spent === null ? "skill purchase" : `spent ${spent} skill points`;
    }
    case "skill_hint_change":
    case "ambiguous_effect": {
      const eff = (e.effect ?? d.effect ?? {}) as unknown as Effect;
      if (!eff.name) return name;
      return eff.amount === undefined || eff.amount === null ? eff.name : `${eff.name} ${signed(eff.amount)}`;
    }
  }
  return name;
}

/** The second line: what the record says beyond its stat changes, in its own text. */
function description(e: Entry): string {
  const d = detail(e);
  const parts: string[] = [];
  switch (e.kind) {
    case "outcome": {
      const effects = Array.isArray(d.effects) ? (d.effects as Effect[]) : [];
      for (const eff of effects) if (eff.kind !== "stat_change" && eff.raw_text) parts.push(eff.raw_text);
      break;
    }
    case "lesson_purchases":
      if (d.cost_basis) parts.push(`cost basis: ${words(d.cost_basis)}`);
      if (d.after_balance_observed === false) parts.push("after-balance not observed");
      break;
    case "song_acquisitions":
      if (d.acquisition) parts.push(`acquired by ${words(d.acquisition)}`);
      if (d.name_conflicted) parts.push("name conflicted");
      break;
    case "races": {
      const placing = num(d.placing);
      const fans = num(d.fans_gained);
      const course = (d.course ?? {}) as Detail;
      if (placing !== null) parts.push(`placing ${placing}`);
      if (fans !== null) parts.push(`fans ${signed(fans)}`);
      const distance = num(course.distance_m);
      const where = [str(course.venue), distance === null ? "" : `${distance} m`, str(course.surface)].filter(Boolean).join(" ");
      if (where) parts.push(where);
      break;
    }
    case "concerts":
      if (d.result) parts.push(words(d.result));
      if (Array.isArray(d.reward_event_ids) && d.reward_event_ids.length) parts.push("rewards recorded on the linked outcome");
      break;
    case "skill_purchases":
    case "skill_purchase_batch": {
      const names = Array.isArray(d.visible_confirmation_names) ? (d.visible_confirmation_names as string[]) : [];
      if (names.length) parts.push(names.join(", "));
      if (d.purchased_list_complete === false) parts.push("list incomplete");
      break;
    }
    case "unparsed_receipt":
      if (e.raw_text) parts.push(e.raw_text);
      if (d.scope) parts.push(str(d.scope));
      break;
    case "ambiguous_effect": {
      const eff = (d.effect ?? {}) as unknown as Effect;
      if (eff.raw_text) parts.push(eff.raw_text);
      if (d.reason) parts.push(words(d.reason));
      break;
    }
    case "skill_hint_change": {
      const eff = (e.effect ?? {}) as unknown as Effect;
      if (eff.raw_text) parts.push(eff.raw_text);
      break;
    }
    case "committed_action":
      if (d.training_outcome) parts.push(`outcome ${words(d.training_outcome)}`);
      break;
    case "training":
      if (d.training_outcome === "failure") parts.push("training failed");
      break;
  }
  return parts.join(" · ");
}

interface Shown {
  field: string;
  amount: number | null;
  basis?: string;
}

/** Changes as the timeline states them, plus a lesson's performance cost with its basis. */
function changes(e: Entry): Shown[] {
  const out: Shown[] = [];
  for (const fields of Object.values(e.changes ?? {})) {
    for (const [field, c] of Object.entries(fields)) out.push({ field, amount: c.amount, basis: c.basis });
  }
  if (e.kind === "lesson_purchases") {
    const d = detail(e);
    const cost = (d.performance_cost ?? null) as Detail | null;
    if (cost) {
      for (const [field, v] of Object.entries(cost)) {
        const amount = num(v);
        if (amount !== null && amount !== 0) out.push({ field, amount: -amount, basis: str(d.cost_basis) || "cost" });
      }
    } else if (d.cost_basis === "unresolved") {
      out.push({ field: "cost", amount: null, basis: "unresolved" });
    }
  }
  return out;
}

/** The five stats and skill points first, in the game's order; the scenario's points after. */
function grouped(e: Entry): { stats: Shown[]; other: Shown[] } {
  const all = changes(e);
  const stats = all.filter((c) => CORE.has(c.field)).sort((a, b) => CORE_ORDER.indexOf(a.field) - CORE_ORDER.indexOf(b.field));
  const other = all.filter((c) => !CORE.has(c.field));
  return { stats, other };
}

function flags(e: Entry): string[] {
  // Derived amounts are already marked on the amount itself; the rest is listed.
  return entryWarnings(e).filter((w) => !w.text.startsWith("amount not read directly")).map((w) => w.text);
}

function direction(amount: number | null): string {
  if (amount === null) return "";
  return amount > 0 ? "up" : amount < 0 ? "down" : "";
}

function fieldName(field: string): string {
  return field === "skill_points" ? "skill pts" : field.replace("_", " ");
}
</script>

<template>
  <p v-if="!entries.length" class="muted">No entries in this window.</p>
  <template v-else>
    <div v-if="entries.length > 8" class="kinds">
      <button :class="{ active: filter === 'all' }" @click="filter = 'all'">All <span class="n">{{ entries.length }}</span></button>
      <button v-for="[name, n] in groups" :key="name" :class="{ active: filter === name }" @click="filter = filter === name ? 'all' : name">{{ name }} <span class="n">{{ n }}</span></button>
    </div>
    <ul class="entries">
      <li v-for="e in shown" :key="e.id" class="entry" :class="{ open: open === e.id }">
        <div class="t">
          <button v-if="e.first_seen_ms !== null" type="button" :title="'go to ' + clock(e.first_seen_ms)" @click="emit('seek', e.first_seen_ms)">{{ clock(e.first_seen_ms) }}</button>
          <span v-else class="muted">?</span>
          <button type="button" class="edit-btn" :class="{ edited: edits?.[e.id] }" :title="edits?.[e.id]?.deleted ? 'removed by you' : edits?.[e.id] ? 'edited by you' : 'edit this event'" @click="emit('edit', e.id)">{{ edits?.[e.id]?.deleted ? "✕" : "✎" }}</button>
        </div>
        <div class="entry-body">
          <div class="entry-line">
            <span class="kind">{{ kindName(e.kind).replace(/s$/, "") }}</span>
            <span class="what">{{ title(e) }}</span>
            <button class="more" :aria-expanded="open === e.id" @click="open = open === e.id ? null : e.id">{{ open === e.id ? "less" : "more" }}</button>
          </div>
          <div v-if="grouped(e).stats.length || grouped(e).other.length" class="changes">
            <span v-for="(c, i) in grouped(e).stats" :key="'s' + c.field + i" class="chg" :class="direction(c.amount)" :title="(c.basis ?? 'basis unknown').replaceAll('_', ' ')">
              <i class="sd" :class="c.field"></i>{{ fieldName(c.field) }} <b>{{ signed(c.amount) }}</b><i v-if="c.basis === 'turn_difference'" class="basis-mark" title="worked out from the difference between turns">≈</i><i v-else-if="basisClass(c.basis)" class="basis-mark">◌</i>
            </span>
            <span v-if="grouped(e).stats.length && grouped(e).other.length" class="sep"></span>
            <span v-for="(c, i) in grouped(e).other" :key="'o' + c.field + i" class="chg scenario" :class="direction(c.amount)" :title="(c.basis ?? 'basis unknown').replaceAll('_', ' ')">
              {{ fieldName(c.field) }} <b>{{ signed(c.amount) }}</b><i v-if="c.basis === 'turn_difference'" class="basis-mark" title="worked out from the difference between turns">≈</i><i v-else-if="basisClass(c.basis)" class="basis-mark">◌</i>
            </span>
          </div>
          <div v-if="description(e)" class="desc">{{ description(e) }}</div>
          <div v-if="flags(e).length" class="flags">{{ flags(e).join(" · ") }}</div>
          <div v-if="open === e.id" class="entry-more">
            <div v-if="changes(e).length" class="small muted">
              Basis of each amount:
              <span v-for="(c, i) in changes(e)" :key="'b' + c.field + i"> {{ fieldName(c.field) }} {{ signed(c.amount) }} = {{ (c.basis ?? "basis unknown").replaceAll("_", " ") }};</span>
            </div>
            <pre>{{ JSON.stringify(e.detail ?? {}, null, 1) }}</pre>
          </div>
        </div>
      </li>
    </ul>
  </template>
</template>
