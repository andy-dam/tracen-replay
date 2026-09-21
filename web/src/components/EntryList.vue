<script setup lang="ts">
import { computed, ref, watch } from "vue";
import type { Entry, EntryEdit } from "../api";
import { basisClass, clock, signed } from "../format";
import { entryWarnings } from "../warnings";

// The log of a window, read the way a player thinks about it: one card per
// thing that happened. A training's action, its result and its receipt are
// one card; a lesson's purchase, its receipt and the song it taught are one
// card; a race and its result are one card. Receipt lines the reader caught
// mid-scroll (cut short or garbled repeats of a line already in the log)
// fold into one quiet line. Every card still opens to its report entries,
// each with its own time and pencil, so nothing the report states is hidden.
// trainingNames: the names the report read on training result cards, per training.
const props = defineProps<{ entries: Entry[]; edits?: Record<string, EntryEdit> | null; trainingNames?: Record<string, string[]> }>();
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
const OPTION_NAME: Record<string, string> = { speed: "Speed", stamina: "Stamina", power: "Power", guts: "Guts", wit: "Wit" };

function kindName(kind: string): string {
  return KIND_NAMES[kind] ?? kind.replaceAll("_", " ").replace(/^./, (c) => c.toUpperCase());
}
// One entry's kind. Spelled out: not every group name is a plural ("Ambiguous").
const KIND_SINGULAR: Record<string, string> = {
  committed_action: "Action",
  training: "Training",
  outcome: "Outcome",
  lesson_purchases: "Lesson",
  song_acquisitions: "Song",
  races: "Race",
  concerts: "Concert",
  skill_purchases: "Skill",
  skill_purchase_batch: "Skill",
  skill_hint_change: "Hint",
  dialogue_choices: "Choice",
  ambiguous_effect: "Ambiguous",
  unparsed_receipt: "Unparsed",
};
function singularKind(kind: string): string {
  return KIND_SINGULAR[kind] ?? kindName(kind);
}

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
function nameOf(e: Entry): string {
  const d = detail(e);
  return str(d.training_name) || str(d.name) || str(d.race_name) || str(d.title) || e.context_title || "";
}

/** The record's own name; nothing is inferred beyond what the entry carries. */
function title(e: Entry): string {
  const d = detail(e);
  const option = e.training_option ?? str(d.training_option);
  const name = nameOf(e);
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
      // A hint has its own entry right after this one; repeating its line
      // here only makes a wall of text.
      for (const eff of effects) if (eff.kind !== "stat_change" && eff.kind !== "skill_hint_change" && eff.raw_text) parts.push(eff.raw_text);
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
      const names = Array.isArray(d.purchased_skill_names) ? (d.purchased_skill_names as string[])
        : Array.isArray(d.visible_confirmation_names) ? (d.visible_confirmation_names as string[]) : [];
      if (names.length) parts.push(names.join(", "));
      if (d.purchased_list_complete === false) parts.push(names.length ? "more names not seen" : "names not seen");
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
  read_amount?: number | null;
}

/** Changes as the timeline states them, plus a lesson's performance cost with its basis. */
function changes(e: Entry): Shown[] {
  const out: Shown[] = [];
  for (const fields of Object.values(e.changes ?? {})) {
    for (const [field, c] of Object.entries(fields)) out.push({ field, amount: c.amount, basis: c.basis, read_amount: c.read_amount });
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
function groupedOf(all: Shown[]): { stats: Shown[]; other: Shown[] } {
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

// ---- folding ----
// A card is one thing that happened, shown once. Members keep their own
// identity for editing; the card's line is built from the member that
// carries the numbers.
type CardKind = "training" | "lesson" | "race" | "fragments" | "single";
interface Card {
  key: string;
  kind: CardKind;
  label: string;
  name: string;
  ms: number | null;
  members: Entry[];
  primary: Entry;
  changes: Shown[];
  note: string;
  flags: string[];
}

const near = (a: Entry, b: Entry, ms: number) => a.first_seen_ms !== null && b.first_seen_ms !== null && Math.abs(a.first_seen_ms - b.first_seen_ms) <= ms;
const receiptKey = (s: string) => s.replace(/\s+/g, " ").replace(/[ .,;:]+$/, "").toLowerCase();

/** The receipt lines an entry carries in its own words, for fragment matching. */
function receiptTexts(e: Entry): string[] {
  const d = detail(e);
  const out: string[] = [];
  if (e.raw_text) out.push(e.raw_text);
  const effects = Array.isArray(d.effects) ? (d.effects as Effect[]) : [];
  for (const eff of effects) if (eff.raw_text) out.push(eff.raw_text);
  const eff = (e.effect ?? d.effect ?? null) as Effect | null;
  if (eff?.raw_text) out.push(eff.raw_text);
  return out;
}
/** A cut or garbled repeat of a line another entry already states within a few seconds. */
function isFragment(e: Entry, all: Entry[]): boolean {
  if (e.kind !== "unparsed_receipt" || !e.raw_text) return false;
  if (str(detail(e).status) === "ocr_fragment") return true;
  const key = receiptKey(e.raw_text);
  if (key.length < 8) return false;
  const digits = key.match(/\d+/g) ?? [];
  for (const other of all) {
    if (other === e || !near(e, other, 15000)) continue;
    for (const text of receiptTexts(other)) {
      const full = receiptKey(text);
      if (full === key || (full.length > key.length && full.startsWith(key))) return true;
      if (full.split(" ")[0] !== key.split(" ")[0]) continue;
      if (digits.length && digits.join(",") !== (full.match(/\d+/g) ?? []).join(",")) continue;
      const bound = 2 + Math.floor(full.length / 12);
      if (Math.abs(full.length - key.length) <= 3 && editDistance(key, full) <= bound) return true;
      if (full.length > key.length && editDistance(key, full.slice(0, key.length)) <= bound) return true;
    }
  }
  return false;
}
function editDistance(a: string, b: string): number {
  let prev = Array.from({ length: b.length + 1 }, (_, i) => i);
  for (let i = 1; i <= a.length; i++) {
    const cur = [i];
    for (let j = 1; j <= b.length; j++) cur.push(Math.min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (a[i - 1] === b[j - 1] ? 0 : 1)));
    prev = cur;
  }
  return prev[b.length];
}

function outcomeMentions(e: Entry, name: string): boolean {
  if (e.kind !== "outcome" || !name) return false;
  const lower = name.toLowerCase();
  return receiptTexts(e).some((t) => t.toLowerCase().includes(lower)) || (e.context_title ?? "").toLowerCase() === lower;
}

const cards = computed<Card[]>(() => {
  const list = props.entries;
  const used = new Set<string>();
  const out: Card[] = [];
  const fragments: Entry[] = [];
  const isSame = (a: Entry, b: Entry) => nameOf(a) !== "" && nameOf(a) === nameOf(b);
  const card = (kind: CardKind, label: string, name: string, members: Entry[], primary: Entry, extra: Shown[] = [], note = ""): Card => {
    for (const m of members) used.add(m.id);
    const ms = members.reduce<number | null>((acc, m) => (m.first_seen_ms === null ? acc : acc === null ? m.first_seen_ms : Math.min(acc, m.first_seen_ms)), null);
    const seen = new Set<string>();
    const merged = [...changes(primary), ...extra].filter((c) => (seen.has(c.field) ? false : (seen.add(c.field), true)));
    // A card carries only the flags that ask for a look; the notes stay on the member lines.
    const serious = [...new Set(members.flatMap((m) => entryWarnings(m).filter((w) => w.serious).map((w) => w.text)))];
    return { key: primary.id, kind, label, name, ms, members, primary, changes: merged, note, flags: serious };
  };
  const fold = (e: Entry) => {
    if (used.has(e.id)) return;
    if (isFragment(e, list)) {
      used.add(e.id);
      fragments.push(e);
      return;
    }
    // A training: the action, its result card and its own receipt. The result card can
    // come several seconds after the action, and the titled message box several seconds
    // after the card; a turn holds one training, so these windows cannot reach another.
    if (e.kind === "training" || (e.kind === "committed_action" && e.action_kind === "training")) {
      const partnerKind = e.kind === "training" ? "committed_action" : "training";
      const partner = list.find((o) => !used.has(o.id) && o !== e && o.kind === partnerKind && (o.kind !== "committed_action" || o.action_kind === "training") && near(e, o, 6000) && (isSame(e, o) || !nameOf(e) || !nameOf(o)));
      const result = e.kind === "training" ? e : partner?.kind === "training" ? partner : null;
      const action = e.kind === "committed_action" ? e : partner?.kind === "committed_action" ? partner : null;
      const members = [action, result].filter((x): x is Entry => !!x);
      const read = nameOf(result ?? action!);
      const option = (result ?? action!).training_option ?? (str(detail(result ?? action!).training_option) || str(detail(action ?? result!).training_option));
      // Its message box is titled with the training's name. A card whose name was not
      // read takes a box titled with a name the report read on another card of the same
      // training, or of any training when which one was not read either.
      const known = props.trainingNames ?? {};
      const titles = read ? [read] : option ? known[option] ?? [] : Object.values(known).flat();
      const receipt = list.find((o) => !used.has(o.id) && o.kind === "outcome" && !changes(o).length && near(o, result ?? action!, 6000) && titles.includes(o.context_title ?? ""));
      if (receipt) members.push(receipt);
      const name = read || receipt?.context_title || "";
      const outcome = str(detail(result ?? action!).training_outcome) || str(detail(action ?? result!).training_outcome);
      const label = `${OPTION_NAME[option] ?? (option ? option[0].toUpperCase() + option.slice(1) : "")} Training`.trim();
      const note = outcome === "failure" ? "Failed: the shown gains were not applied" : outcome === "success" ? "" : outcome ? words(outcome) : "";
      out.push(card("training", label, name, members, result ?? action!, [], note));
      return;
    }
    // A lesson: the purchase, the receipt that carries its award, the song it taught.
    if (e.kind === "lesson_purchases") {
      const name = nameOf(e);
      const members = [e];
      const song = list.find((o) => !used.has(o.id) && o.kind === "song_acquisitions" && near(e, o, 5000) && isSame(e, o));
      const receipt = list.find((o) => !used.has(o.id) && o.kind === "outcome" && near(e, o, 5000) && (outcomeMentions(o, name) || o.first_seen_ms === e.first_seen_ms));
      if (receipt) members.push(receipt);
      if (song) members.push(song);
      const extra = receipt ? changes(receipt) : [];
      const note = [receipt ? receiptTexts(receipt).filter((t) => !t.toLowerCase().includes(name.toLowerCase())).join(" · ") : "", song ? "song learned" : ""].filter(Boolean).join(" · ");
      out.push(card("lesson", song ? "Song Lesson" : "Lesson", name, members, e, extra, note));
      return;
    }
    // A race: the action and its result screen.
    if (e.kind === "committed_action" && e.action_kind === "race") {
      const name = nameOf(e);
      const result = list.find((o) => !used.has(o.id) && o.kind === "races" && near(e, o, 90000) && isSame(e, o));
      const members = result ? [e, result] : [e];
      const d = detail(result ?? e);
      const placing = num(d.placing) ?? Number(str(d.placing)) ?? null;
      const fans = num(d.fans_gained);
      const note = [placing && placing > 0 ? `${placing}${placing === 1 ? "st" : placing === 2 ? "nd" : placing === 3 ? "rd" : "th"} place` : "", fans !== null ? `fans ${signed(fans)}` : "", description(result ?? e).replace(/placing \d+ ?·? ?/, "").replace(/fans [+-]?\d+ ?·? ?/, "")].filter(Boolean).join(" · ");
      out.push(card("race", "Race", name, members, result ?? e, [], note));
      return;
    }
    if (e.kind === "races") {
      const action = list.find((o) => !used.has(o.id) && o.kind === "committed_action" && o.action_kind === "race" && near(e, o, 90000) && isSame(e, o));
      const members = action ? [action, e] : [e];
      out.push(card("race", "Race", nameOf(e), members, e, [], description(e)));
      return;
    }
    // A concert: the result and the receipt that pays its rewards under the same title.
    if (e.kind === "concerts") {
      const name = nameOf(e);
      const receipt = list.find((o) => !used.has(o.id) && o.kind === "outcome" && near(e, o, 60000) && (o.context_title ?? "") === name && name);
      const members = receipt ? [e, receipt] : [e];
      const note = [words(detail(e).result), receipt ? receiptTexts(receipt).join(" · ") : ""].filter(Boolean).join(" · ");
      out.push(card("race", "Concert", name, members, receipt ?? e, receipt ? [] : [], note));
      return;
    }
    out.push(card("single", singularKind(e.kind), title(e), [e], e, [], description(e)));
  };
  // Things that happened claim their partner entries first, whatever the order the
  // report lists them in; whatever is left stands on its own.
  const primary = (e: Entry) => e.kind === "training" || e.kind === "lesson_purchases" || e.kind === "races" || e.kind === "concerts" || (e.kind === "committed_action" && (e.action_kind === "training" || e.action_kind === "race"));
  for (const e of list) if (primary(e)) fold(e);
  for (const e of list) fold(e);
  if (fragments.length) {
    const first = fragments[0];
    out.push({ key: "fragments", kind: "fragments", label: "OCR Fragments", name: `${fragments.length} cut or garbled repeat${fragments.length === 1 ? "" : "s"} of lines already in the log`, ms: first.first_seen_ms, members: fragments, primary: first, changes: [], note: "", flags: [] });
  }
  return out.sort((a, b) => (a.ms ?? Infinity) - (b.ms ?? Infinity));
});

const groups = computed(() => {
  const counts = new Map<string, number>();
  for (const c of cards.value) counts.set(c.label, (counts.get(c.label) ?? 0) + 1);
  return [...counts.entries()].sort((a, b) => b[1] - a[1]);
});
const shown = computed(() => (filter.value === "all" ? cards.value : cards.value.filter((c) => c.label === filter.value)));

// A new window resets the filter; a filter that no longer matches anything would hide the whole log.
watch(
  () => props.entries,
  () => {
    filter.value = "all";
    open.value = null;
  },
);

function editedMark(e: Entry): string {
  return props.edits?.[e.id]?.deleted ? "✕" : "✎";
}
</script>

<template>
  <p v-if="!entries.length" class="muted">No entries in this window.</p>
  <template v-else>
    <div v-if="cards.length > 8" class="kinds">
      <button :class="{ active: filter === 'all' }" @click="filter = 'all'">All <span class="n">{{ cards.length }}</span></button>
      <button v-for="[name, n] in groups" :key="name" :class="{ active: filter === name }" @click="filter = filter === name ? 'all' : name">{{ name }} <span class="n">{{ n }}</span></button>
    </div>
    <ul class="entries">
      <li v-for="c in shown" :key="c.key" class="entry" :class="['card-' + c.kind, { open: open === c.key, quiet: c.kind === 'fragments' }]">
        <div class="t">
          <button v-if="c.ms !== null" type="button" :title="'go to ' + clock(c.ms)" @click="emit('seek', c.ms)">{{ clock(c.ms) }}</button>
          <span v-else class="muted">?</span>
          <button v-if="c.kind !== 'fragments'" type="button" class="edit-btn" :class="{ edited: edits?.[c.primary.id] }" :title="edits?.[c.primary.id]?.deleted ? 'removed by you' : edits?.[c.primary.id] ? 'edited by you' : 'edit this event'" @click="emit('edit', c.primary.id)">{{ editedMark(c.primary) }}</button>
        </div>
        <div class="entry-body">
          <div class="entry-line">
            <span class="kind">{{ c.label }}</span>
            <span class="what">{{ c.name }}</span>
            <button class="more" :aria-expanded="open === c.key" @click="open = open === c.key ? null : c.key">{{ open === c.key ? "less" : c.members.length > 1 ? `${c.members.length} lines` : "more" }}</button>
          </div>
          <div v-if="groupedOf(c.changes).stats.length || groupedOf(c.changes).other.length" class="changes">
            <span v-for="(ch, i) in groupedOf(c.changes).stats" :key="'s' + ch.field + i" class="chg" :class="direction(ch.amount)" :title="(ch.basis ?? 'basis unknown').replaceAll('_', ' ')">
              <i class="sd" :class="ch.field"></i>{{ fieldName(ch.field) }} <b>{{ signed(ch.amount) }}</b><i v-if="ch.basis === 'turn_difference'" class="basis-mark" :title="ch.read_amount != null ? `the panel showed ${ch.read_amount}; completed from the difference between turns` : 'worked out from the difference between turns'">≈</i><i v-else-if="ch.basis === 'observed_learned_training_gain'" class="basis-mark learned" title="read on the card by the learned reader, and it matches the difference between turns">✦</i><i v-else-if="basisClass(ch.basis)" class="basis-mark">◌</i>
            </span>
            <span v-if="groupedOf(c.changes).stats.length && groupedOf(c.changes).other.length" class="sep"></span>
            <span v-for="(ch, i) in groupedOf(c.changes).other" :key="'o' + ch.field + i" class="chg scenario" :class="direction(ch.amount)" :title="(ch.basis ?? 'basis unknown').replaceAll('_', ' ')">
              {{ fieldName(ch.field) }} <b>{{ signed(ch.amount) }}</b><i v-if="ch.basis === 'turn_difference'" class="basis-mark" :title="ch.read_amount != null ? `the panel showed ${ch.read_amount}; completed from the difference between turns` : 'worked out from the difference between turns'">≈</i><i v-else-if="basisClass(ch.basis)" class="basis-mark">◌</i>
            </span>
          </div>
          <div v-if="c.note" class="desc">{{ c.note }}</div>
          <div v-if="c.flags.length" class="flags">{{ c.flags.join(" · ") }}</div>
          <div v-if="open === c.key" class="entry-more">
            <ul class="members">
              <li v-for="e in c.members" :key="e.id" class="member">
                <div class="member-head">
                  <button v-if="e.first_seen_ms !== null" type="button" class="tchip" @click="emit('seek', e.first_seen_ms)">{{ clock(e.first_seen_ms) }}</button>
                  <span class="kind">{{ singularKind(e.kind) }}</span>
                  <b>{{ title(e) }}</b>
                  <button v-if="c.kind !== 'fragments'" type="button" class="edit-btn" :class="{ edited: edits?.[e.id] }" :title="edits?.[e.id]?.deleted ? 'removed by you' : edits?.[e.id] ? 'edited by you' : 'edit this event'" @click="emit('edit', e.id)">{{ editedMark(e) }}</button>
                </div>
                <div v-if="changes(e).length" class="small muted">
                  <span v-for="(ch, i) in changes(e)" :key="'b' + ch.field + i">{{ fieldName(ch.field) }} {{ signed(ch.amount) }} = {{ (ch.basis ?? "basis unknown").replaceAll("_", " ") }}; </span>
                </div>
                <div v-if="description(e)" class="desc">{{ description(e) }}</div>
                <div v-if="flags(e).length" class="flags">{{ flags(e).join(" · ") }}</div>
                <pre>{{ JSON.stringify(e.detail ?? {}, null, 1) }}</pre>
              </li>
            </ul>
          </div>
        </div>
      </li>
    </ul>
  </template>
</template>
