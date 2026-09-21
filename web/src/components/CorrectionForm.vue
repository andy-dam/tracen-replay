<script setup lang="ts">
import { computed, nextTick, reactive, ref, watch } from "vue";
import { api, ApiError, type AddedEvent, type Correction, type CorrectionChange, type Entry, type EntryEdit, type Turn, type TurnSummary, type Verification } from "../api";
import { clock, titleCase } from "../format";
import { entryName, entryWarnings, gapAdvice } from "../warnings";

// The review editor for one turn. The report keeps its own reading; the
// viewer's edits are stored beside it and checked by the server against the
// values observed at the start of the next turn. The editor mirrors that
// check live, so the viewer sees the turn add up while typing.
const props = defineProps<{
  reportId: string;
  turn: Turn;
  summaryTurn: TurnSummary | null;
  entries: Entry[];
  hasAction: boolean;
  correction: Correction | null;
  verification: Verification | null;
  focusEntry?: string | null;
  videoMs?: number | null;
  /** On the review screen: show the steps and the advice, and no close button. */
  guided?: boolean;
  /** What the report says was played this turn, when it saw it. */
  reportAction?: { text: string; option: string; kind: string } | null;
}>();
const emit = defineEmits<{ saved: []; close: []; seek: [ms: number] }>();

const STAT_FIELDS = ["speed", "stamina", "power", "guts", "wit", "skill_points"] as const;
const PERF_FIELDS = ["dance", "passion", "vocal", "visual", "composure"] as const;
const LABEL: Record<string, string> = { speed: "Speed", stamina: "Stamina", power: "Power", guts: "Guts", wit: "Wit", skill_points: "Skill Pts", dance: "Dance", passion: "Passion", vocal: "Vocal", visual: "Visual", composure: "Composure" };
const OPTIONS = ["speed", "stamina", "power", "guts", "wit"] as const;
const ADDED_KINDS = ["event", "training", "race", "rest", "outing", "purchase"] as const;
const KIND_LABEL: Record<string, string> = { event: "Event", training: "Training", race: "Race", rest: "Rest", outing: "Outing", purchase: "Purchase" };
const OWNER_WORD: Record<string, string> = { training: "the training", event: "the event whose number was cut off", lesson: "the lesson", race: "the race" };

// A field key is "channel:field"; every amount map below is keyed that way.
type Key = string;
const keyOf = (channel: string, field: string): Key => `${channel}:${field}`;
const split = (key: Key) => {
  const [channel, field] = key.split(":");
  return { channel, field };
};

const actionKind = ref("");
const option = ref("speed");
const name = ref("");
const gains = reactive<Record<string, string>>({});
const amounts = reactive<Record<Key, string>>({}); // the viewer's own amount per gap
const notes = reactive<Record<Key, string>>({});
const mode = reactive<Record<Key, "" | "entry" | "added" | "amount">>({});
const assigned = reactive<Record<Key, string>>({}); // gap -> entry id it was added to
const confirmed = reactive<Record<Key, boolean>>({}); // worked-out gaps the viewer accepted
const note = ref("");
// The viewer's own mark that the turn needs no more checking.
const resolved = ref(false);
const busy = ref(false);
const error = ref("");
const result = ref<Verification | null>(props.verification);
const showAllEntries = ref(false);
const openNotes = reactive<Record<string, boolean>>({});

interface EntryRow {
  id: string;
  amounts: Record<Key, string>;
  deleted: boolean;
  reviewed: boolean;
  note: string;
}
const entryRows = reactive<Record<string, EntryRow>>({});
interface AddedRow {
  id: string;
  kind: string;
  title: string;
  time: string; // m:ss
  amounts: Record<Key, string>;
  note: string;
}
const added = ref<AddedRow[]>([]);

// Every turn was played somehow; the viewer may always say what, whether the
// report saw nothing or read it wrong.
const canFillAction = computed(() => true);
// What the saved answer says was played, for the confirmation line.
const savedAction = computed(() => {
  const a = props.correction?.action;
  if (!a) return "";
  const kind = a.kind === "training" ? `${LABEL[a.training_option ?? ""] ?? ""} training`.trim() : KIND_LABEL[a.kind] ?? a.kind;
  return a.name ? `${kind} (${a.name})` : kind;
});

// The gaps: fields whose values at this turn and the next do not add up, or
// that the report worked out from the difference between turns.
interface Gap {
  key: Key;
  channel: string;
  field: string;
  before: number | null;
  after: number | null;
  recorded: number;
  residual: number | null;
  workedOut: number;
  owner: string;
  windowStart: number | null;
  windowEnd: number | null;
}
const gaps = computed<Gap[]>(() => {
  const existing = new Set((props.correction?.changes ?? []).map((c) => keyOf(c.channel ?? "stats", c.field)));
  const owners = new Map((props.summaryTurn?.differences ?? []).map((d) => [keyOf(d.channel, d.field), d.owner ?? ""]));
  const out: Gap[] = [];
  for (const [channel, fields] of [["stats", STAT_FIELDS], ["performance", PERF_FIELDS]] as const) {
    for (const field of fields) {
      const fa = props.turn.accounting?.[channel]?.[field];
      const before = fa?.before ?? null;
      const after = fa?.after ?? null;
      const workedOut = fa?.turn_difference ?? 0;
      const recorded = (fa?.direct ?? 0) + (fa?.derived ?? 0) - workedOut;
      const residual = before !== null && after !== null ? after - before - recorded : null;
      const status = fa?.status ?? "";
      const open = (residual !== null && residual !== 0) || ["unexplained_change", "unresolved_attribution"].includes(status);
      const key = keyOf(channel, field);
      if (open || existing.has(key) || workedOut) out.push({ key, channel, field, before, after, recorded, residual, workedOut, owner: owners.get(key) ?? "", windowStart: fa?.window_start_ms ?? null, windowEnd: fa?.window_end_ms ?? null });
    }
  }
  return out;
});
const gapAmount = (g: Gap) => (g.residual !== null ? g.residual : g.workedOut);

function reportedAmount(e: Entry, channel: string, field: string): number | null {
  const c = e.changes?.[channel]?.[field];
  return c && typeof c.amount === "number" ? c.amount : null;
}
// Whether the report counted this change in its accounting, as the server sees it.
function counted(e: Entry, channel: string, field: string): boolean {
  const c = e.changes?.[channel]?.[field];
  return !!c && typeof c.amount === "number" && !!c.basis && !e.conflicts_present;
}
function fieldsOf(e: Entry): Key[] {
  const out: Key[] = [];
  for (const channel of ["stats", "performance"]) for (const field of Object.keys(e.changes?.[channel] ?? {})) out.push(keyOf(channel, field));
  return out;
}
function flagged(e: Entry): boolean {
  return entryWarnings(e).length > 0;
}
function edited(e: Entry): boolean {
  const row = entryRows[e.id];
  if (!row) return false;
  if (row.deleted || row.reviewed || row.note.trim()) return true;
  return Object.entries(row.amounts).some(([key, text]) => {
    const { channel, field } = split(key);
    const reported = reportedAmount(e, channel, field);
    return text.trim() !== (reported === null ? "" : String(reported));
  });
}
const editableEntries = computed(() => props.entries.filter((e) => fieldsOf(e).length || flagged(e)));
const shownEntries = computed(() => {
  if (showAllEntries.value) return editableEntries.value;
  return editableEntries.value.filter((e) => flagged(e) || props.correction?.entries?.[e.id] || e.id === props.focusEntry || edited(e) || Object.values(assigned).includes(e.id));
});
// Entries a gap can be added to: the turn's events with a time, most recent first.
const assignable = computed(() => props.entries.filter((e) => e.kind !== "committed_action" && e.first_seen_ms !== null));

function load() {
  const c = props.correction;
  actionKind.value = c?.action?.kind ?? "";
  option.value = c?.action?.training_option || "speed";
  name.value = c?.action?.name ?? "";
  for (const f of STAT_FIELDS) gains[f] = c?.action?.gains?.[f] !== undefined ? String(c.action!.gains![f]) : "";
  for (const key of Object.keys(amounts)) delete amounts[key];
  for (const key of Object.keys(notes)) delete notes[key];
  for (const key of Object.keys(mode)) delete mode[key];
  for (const key of Object.keys(assigned)) delete assigned[key];
  for (const key of Object.keys(confirmed)) delete confirmed[key];
  for (const ch of c?.changes ?? []) {
    const key = keyOf(ch.channel ?? "stats", ch.field);
    amounts[key] = String(ch.amount);
    notes[key] = ch.note ?? "";
    mode[key] = "amount";
  }
  for (const key of Object.keys(entryRows)) delete entryRows[key];
  for (const e of props.entries) {
    const edit = c?.entries?.[e.id];
    const row: EntryRow = { id: e.id, amounts: {}, deleted: !!edit?.deleted, reviewed: !!edit?.reviewed, note: edit?.note ?? "" };
    for (const key of fieldsOf(e)) {
      const { channel, field } = split(key);
      const override = edit?.changes?.[channel]?.[field];
      const reported = reportedAmount(e, channel, field);
      row.amounts[key] = override !== undefined ? String(override) : reported !== null ? String(reported) : "";
    }
    for (const [channel, fields] of Object.entries(edit?.changes ?? {})) for (const [field, v] of Object.entries(fields)) row.amounts[keyOf(channel, field)] = String(v);
    entryRows[e.id] = row;
  }
  added.value = (c?.added ?? []).map((a) => ({
    id: a.id, kind: a.kind, title: a.title ?? "", time: a.time_ms != null ? clock(a.time_ms) : "",
    amounts: Object.fromEntries(Object.entries(a.changes ?? {}).flatMap(([ch, fs]) => Object.entries(fs).map(([f, v]) => [keyOf(ch, f), String(v)]))),
    note: a.note ?? "",
  }));
  note.value = c?.note ?? "";
  resolved.value = c?.resolved ?? false;
  result.value = props.verification;
}
watch(() => props.correction, load, { immediate: true });
watch(
  () => props.focusEntry,
  async (id) => {
    if (!id) return;
    await nextTick();
    document.getElementById("review-" + id)?.scrollIntoView({ block: "center", behavior: "smooth" });
  },
  { immediate: true },
);

function num(v: string): number | null {
  const t = v.trim();
  if (!t) return null;
  const n = Number(t);
  return Number.isInteger(n) ? n : null;
}
function parseClock(v: string): number | null {
  const m = v.trim().match(/^(\d+):(\d{1,2})(?:\.(\d{1,3}))?$/);
  if (!m) return null;
  return (Number(m[1]) * 60 + Number(m[2])) * 1000 + Number((m[3] ?? "0").padEnd(3, "0"));
}
function signed(n: number) {
  return n > 0 ? `+${n}` : String(n);
}
function windowText(a: number | null | undefined, b: number | null | undefined) {
  return a != null && b != null ? `${clock(a)} to ${clock(b)}` : "";
}

// ---- resolving a gap ----
function chooseMode(g: Gap, m: "entry" | "added" | "amount") {
  if (mode[g.key] === m) {
    clearGap(g);
    return;
  }
  clearGap(g);
  mode[g.key] = m;
  if (m === "amount") amounts[g.key] = signed(gapAmount(g)).replace("+", "");
  if (m === "added") {
    const row = newAdded();
    row.amounts[g.key] = String(gapAmount(g));
    assigned[g.key] = row.id;
    scrollTo("review-" + row.id);
  }
}
// Undo whatever this gap's current mode put elsewhere.
function clearGap(g: Gap) {
  const m = mode[g.key];
  if (m === "entry" && assigned[g.key]) {
    const row = entryRows[assigned[g.key]];
    const e = props.entries.find((x) => x.id === assigned[g.key]);
    if (row && e) {
      const reported = reportedAmount(e, g.channel, g.field);
      if (reported === null && !fieldsOf(e).includes(g.key)) delete row.amounts[g.key];
      else row.amounts[g.key] = reported === null ? "" : String(reported);
    }
  }
  if (m === "added" && assigned[g.key]) added.value = added.value.filter((a) => a.id !== assigned[g.key]);
  delete assigned[g.key];
  delete confirmed[g.key];
  amounts[g.key] = "";
  mode[g.key] = "";
}
function assignTo(g: Gap, entryId: string) {
  if (mode[g.key] === "entry" && assigned[g.key]) clearGap(g);
  mode[g.key] = "entry";
  if (!entryId) return;
  const e = props.entries.find((x) => x.id === entryId);
  const row = entryRows[entryId];
  if (!e || !row) return;
  const reported = reportedAmount(e, g.channel, g.field) ?? 0;
  row.amounts[g.key] = String(reported + gapAmount(g));
  assigned[g.key] = entryId;
  scrollTo("review-" + entryId);
}
function confirmWorkedOut(g: Gap) {
  // "Looks Right": the worked-out amount stands; mark its owner reviewed.
  const was = confirmed[g.key];
  clearGap(g);
  const owner = props.entries.find((e) => (g.owner === "training" ? e.kind === "training" : e.kind !== "committed_action") && e.changes?.[g.channel]?.[g.field]);
  if (was) return;
  confirmed[g.key] = true;
  if (owner && entryRows[owner.id]) entryRows[owner.id].reviewed = true;
}
async function scrollTo(id: string) {
  await nextTick();
  document.getElementById(id)?.scrollIntoView({ block: "center", behavior: "smooth" });
}

function newAdded(): AddedRow {
  const row: AddedRow = { id: "user-" + Date.now().toString(36) + Math.random().toString(36).slice(2, 5), kind: "event", title: "", time: props.videoMs != null ? clock(props.videoMs) : clock(props.turn.start_ms ?? 0), amounts: {}, note: "" };
  added.value.push(row);
  return row;
}
function removeAdded(id: string) {
  added.value = added.value.filter((a) => a.id !== id);
  for (const key of Object.keys(assigned)) if (assigned[key] === id) clearGap(gaps.value.find((g) => g.key === key)!);
}
function addField(row: { amounts: Record<Key, string> }, key: Key) {
  if (key && !(key in row.amounts)) row.amounts[key] = "";
}
function dropField(row: { amounts: Record<Key, string> }, key: Key) {
  delete row.amounts[key];
}
function useVideoTime(row: AddedRow) {
  if (props.videoMs != null) row.time = clock(props.videoMs);
}
function bump(row: { amounts: Record<Key, string> }, key: Key, delta: number) {
  const n = num(row.amounts[key] ?? "") ?? 0;
  row.amounts[key] = String(n + delta);
}

// ---- the live check, the same arithmetic the server applies on save ----
interface LiveField {
  key: Key;
  field: string;
  before: number | null;
  after: number | null;
  recorded: number;
  supplied: number;
  residual: number | null;
  status: "balanced" | "off" | "open" | "unverifiable" | "turn_difference";
  off: number;
  /** what a reader saw on the training's own card while this amount was worked out */
  contradictedBy?: number[];
}
const live = computed(() => {
  const supplied: Record<Key, number> = {};
  const adjust: Record<Key, number> = {};
  const add = (map: Record<Key, number>, key: Key, n: number) => (map[key] = (map[key] ?? 0) + n);
  if (actionKind.value) for (const f of STAT_FIELDS) add(supplied, keyOf("stats", f), num(gains[f] ?? "") ?? 0);
  for (const [key, text] of Object.entries(amounts)) add(supplied, key, num(text) ?? 0);
  for (const a of added.value) for (const [key, text] of Object.entries(a.amounts)) add(supplied, key, num(text) ?? 0);
  for (const e of props.entries) {
    const row = entryRows[e.id];
    if (!row) continue;
    for (const key of fieldsOf(e)) {
      const { channel, field } = split(key);
      const reported = reportedAmount(e, channel, field);
      const isCounted = counted(e, channel, field);
      if (row.deleted) {
        if (isCounted) add(adjust, key, -(reported ?? 0));
        continue;
      }
      const text = row.amounts[key] ?? "";
      const override = text.trim() === "" ? (reported !== null ? 0 : null) : num(text);
      if (override === null || override === reported) continue;
      add(adjust, key, isCounted ? override - (reported ?? 0) : override);
    }
    if (!row.deleted) for (const [key, text] of Object.entries(row.amounts)) if (!fieldsOf(e).includes(key)) add(adjust, key, num(text) ?? 0);
  }
  // What a reader saw on a training's own card where the report worked the
  // amount out from the turn difference instead.
  const contradicted: Record<Key, number[]> = {};
  for (const e of props.entries)
    for (const [channel, byField] of Object.entries(e.changes ?? {}))
      for (const [field, change] of Object.entries(byField ?? {}))
        if (change?.contradicted_by?.length) contradicted[keyOf(channel, field)] = change.contradicted_by;
  const fields: LiveField[] = [];
  let balanced = true;
  let observed = 0;
  for (const [channel, names] of [["stats", STAT_FIELDS], ["performance", PERF_FIELDS]] as const) {
    for (const field of names) {
      const key = keyOf(channel, field);
      const fa = props.turn.accounting?.[channel]?.[field];
      const give = (supplied[key] ?? 0) + (adjust[key] ?? 0);
      const touchedByEdit = (adjust[key] ?? 0) !== 0;
      let recorded = 0;
      let extra = 0;
      let residual: number | null = null;
      if (fa) {
        recorded = (fa.direct ?? 0) + (fa.derived ?? 0);
        extra = fa.turn_difference ?? 0;
        if ((supplied[key] ?? 0) !== 0 && extra !== 0) recorded -= extra;
        if (fa.before !== null && fa.before !== undefined && fa.after !== null && fa.after !== undefined) residual = fa.after - fa.before - recorded;
      }
      const touched = give !== 0 || touchedByEdit || (residual !== null && residual !== 0) || extra !== 0;
      if (!touched) continue;
      let status: LiveField["status"];
      if (give === 0 && !touchedByEdit && extra !== 0 && residual === 0) status = "turn_difference";
      else if (give === 0 && !touchedByEdit) status = "open";
      else if (residual === null) status = "unverifiable";
      else if (residual === give) {
        status = "balanced";
        observed++;
      } else {
        status = "off";
        balanced = false;
      }
      fields.push({ key, field, before: fa?.before ?? null, after: fa?.after ?? null, recorded, supplied: give, residual, status, off: residual === null ? 0 : residual - give, contradictedBy: contradicted[key] });
    }
  }
  const off = fields.filter((f) => f.status === "off");
  const open = fields.filter((f) => f.status === "open" && f.residual !== null && f.residual !== 0);
  const text = off.length
    ? `Off by ${off.map((f) => `${LABEL[f.field]} ${signed(f.off)}`).join(", ")}`
    : open.length
      ? `${open.length} number${open.length === 1 ? "" : "s"} still off`
      : balanced && observed > 0
        ? "Adds Up"
        : "Nothing to Check Yet";
  const cls = off.length ? "off" : open.length ? "warn" : balanced && observed > 0 ? "ok" : "na";
  return { fields, text, cls, byKey: Object.fromEntries(fields.map((f) => [f.key, f])) as Record<Key, LiveField> };
});
function gapState(g: Gap): { text: string; cls: string } {
  const f = live.value.byKey[g.key];
  if (!f) return { text: "", cls: "" };
  if (f.status === "balanced") return { text: "adds up", cls: "ok" };
  if (f.status === "off") return { text: `${signed(f.off)} still off`, cls: "warn" };
  if (f.status === "turn_difference")
    return f.contradictedBy?.length
      ? { text: `the report's guess; the card shows ${f.contradictedBy.map(signed).join(" or ")}`, cls: "warn" }
      : { text: "the report's guess", cls: "na" };
  if (f.status === "unverifiable") return { text: "cannot be checked", cls: "na" };
  return { text: "needs a look", cls: "warn" };
}

async function save() {
  error.value = "";
  const changes: CorrectionChange[] = [];
  for (const [key, text] of Object.entries(amounts)) {
    const { channel, field } = split(key);
    const n = num(text);
    if (text.trim() && n === null) return void (error.value = `${LABEL[field]}: enter a whole number`);
    if (n) changes.push({ field, channel: channel === "stats" ? undefined : channel, amount: n, note: notes[key]?.trim() || undefined });
  }
  let action = null;
  if (actionKind.value) {
    const g: Record<string, number> = {};
    for (const f of STAT_FIELDS) {
      const n = num(gains[f] ?? "");
      if (gains[f]?.trim() && n === null) return void (error.value = `${LABEL[f]} gain: enter a whole number`);
      if (n) g[f] = n;
    }
    action = { kind: actionKind.value, training_option: actionKind.value === "training" ? option.value : undefined, name: name.value.trim() || undefined, gains: Object.keys(g).length ? g : undefined };
  }
  const entries: Record<string, EntryEdit> = {};
  for (const e of props.entries) {
    const row = entryRows[e.id];
    if (!row) continue;
    const edit: EntryEdit = {};
    const ch: Record<string, Record<string, number>> = {};
    for (const [key, text] of Object.entries(row.amounts)) {
      const { channel, field } = split(key);
      const n = num(text);
      if (text.trim() && n === null) return void (error.value = `${entryName(e)} ${LABEL[field] ?? field}: enter a whole number`);
      const reported = reportedAmount(e, channel, field);
      if (n !== null && n !== reported) (ch[channel] ??= {})[field] = n;
      if (n === null && reported !== null && text.trim() === "") (ch[channel] ??= {})[field] = 0;
    }
    if (Object.keys(ch).length) edit.changes = ch;
    if (row.deleted) edit.deleted = true;
    if (row.reviewed) edit.reviewed = true;
    if (row.note.trim()) edit.note = row.note.trim();
    if (Object.keys(edit).length) entries[e.id] = edit;
  }
  const addedOut: AddedEvent[] = [];
  for (const a of added.value) {
    const ch: Record<string, Record<string, number>> = {};
    for (const [key, text] of Object.entries(a.amounts)) {
      const { channel, field } = split(key);
      const n = num(text);
      if (text.trim() && n === null) return void (error.value = `${a.title || "added event"} ${LABEL[field] ?? field}: enter a whole number`);
      if (n) (ch[channel] ??= {})[field] = n;
    }
    const ms = a.time.trim() ? parseClock(a.time) : null;
    if (a.time.trim() && ms === null) return void (error.value = `${a.title || "added event"}: time as m:ss`);
    addedOut.push({ id: a.id, kind: a.kind, title: a.title.trim() || undefined, time_ms: ms, changes: Object.keys(ch).length ? ch : undefined, note: a.note.trim() || undefined });
  }
  if (!action && !changes.length && !Object.keys(entries).length && !addedOut.length && !resolved.value) return void (error.value = "Nothing to save yet.");
  busy.value = true;
  try {
    const saved = await api.saveCorrection(props.reportId, props.turn.id, { action, changes, entries: Object.keys(entries).length ? entries : undefined, added: addedOut.length ? addedOut : undefined, note: note.value.trim() || undefined, resolved: resolved.value || undefined });
    result.value = saved.verification;
    emit("saved");
  } catch (e) {
    error.value = e instanceof ApiError ? e.message : String(e);
  } finally {
    busy.value = false;
  }
}

async function remove() {
  busy.value = true;
  error.value = "";
  try {
    await api.deleteCorrection(props.reportId, props.turn.id);
    result.value = null;
    emit("saved");
  } catch (e) {
    error.value = e instanceof ApiError ? e.message : String(e);
  } finally {
    busy.value = false;
  }
}
</script>

<template>
  <div class="rv">
    <header class="rv-head">
      <div class="rv-title">
        <span class="overline" style="margin: 0">{{ guided ? "Guided Check" : "Check" }}</span>
        <span class="pill" :class="live.cls">{{ live.text }}</span>
      </div>
      <button v-if="!guided" class="icon-btn small" title="Close the review" @click="emit('close')">✕</button>
    </header>
    <p class="rv-lede">The report keeps what it read. Your answers are saved next to it and checked against the numbers the game showed at the start of the next turn.</p>
    <ol v-if="guided" class="steps-strip">
      <li :class="{ done: gaps.length && gaps.every((g) => gapState(g).cls !== 'warn'), off: !gaps.length }"><b>1</b> Fix the numbers</li>
      <li :class="{ done: !!actionKind || hasAction }"><b>2</b> Say what you played</li>
      <li :class="{ done: shownEntries.length && shownEntries.every((e) => entryRows[e.id]?.reviewed || entryRows[e.id]?.deleted), off: !shownEntries.length }"><b>3</b> Check the lines</li>
      <li :class="{ done: !!result }"><b>4</b> Save</li>
    </ol>

    <section v-if="gaps.length" class="rv-section">
      <h4 class="rv-h">Numbers That Don't Add Up <span class="rv-count">{{ gaps.length }}</span></h4>
      <div v-for="g in gaps" :key="g.key" class="gap" :class="[gapState(g).cls, { resolved: gapState(g).cls === 'ok' }]">
        <div class="gap-top">
          <i class="sd" :class="g.field"></i>
          <b class="gap-name">{{ LABEL[g.field] }}</b>
          <span class="gap-amt" :class="gapAmount(g) > 0 ? 'up' : 'down'">{{ signed(gapAmount(g)) }}</span>
          <span class="gap-eq">{{ g.before ?? "?" }} → {{ g.after ?? "?" }}<template v-if="g.recorded"> · report explains {{ signed(g.recorded) }}</template></span>
          <button v-if="windowText(g.windowStart, g.windowEnd)" class="gap-seek" title="Seek the recording to where the change happened" @click="emit('seek', g.windowStart!)">▶ {{ windowText(g.windowStart, g.windowEnd) }}</button>
          <span class="gap-state" :class="gapState(g).cls">{{ gapState(g).text }}</span>
        </div>
        <p v-if="g.residual === null && !g.workedOut" class="gap-note">A value before or after this turn was not observed, so this field cannot be checked.</p>
        <p v-else-if="guided" class="gap-how">{{ gapAdvice(g.field, gapAmount(g), !!g.workedOut, windowText(g.windowStart, g.windowEnd)) }}<template v-if="g.workedOut"> The report put it on {{ OWNER_WORD[g.owner] ?? "its only possible source" }}.</template></p>
        <p v-else-if="g.workedOut" class="gap-note">The report worked {{ signed(g.workedOut) }} onto {{ OWNER_WORD[g.owner] ?? "its only possible source" }} from the difference between turns. Confirm it, or say where it really came from.</p>
        <p class="gap-q">{{ g.workedOut ? "Is the report's guess right?" : "Where did this come from?" }}</p>
        <div class="seg">
          <button v-if="g.workedOut" :class="{ on: confirmed[g.key] }" @click="confirmWorkedOut(g)">Correct</button>
          <button :class="{ on: mode[g.key] === 'entry' }" :disabled="!assignable.length" @click="chooseMode(g, 'entry')">Belongs to an Event</button>
          <button :class="{ on: mode[g.key] === 'added' }" @click="chooseMode(g, 'added')">Missed Event</button>
          <button :class="{ on: mode[g.key] === 'amount' }" @click="chooseMode(g, 'amount')">Enter Amount</button>
        </div>
        <div v-if="mode[g.key] === 'entry'" class="gap-detail">
          <select :value="assigned[g.key] ?? ''" @change="assignTo(g, ($event.target as HTMLSelectElement).value)">
            <option value="">Which Line?</option>
            <option v-for="e in assignable" :key="e.id" :value="e.id">{{ clock(e.first_seen_ms) }} · {{ entryName(e) }}</option>
          </select>
          <span v-if="assigned[g.key]" class="muted small">{{ signed(gapAmount(g)) }} {{ LABEL[g.field] }} added to that line below</span>
        </div>
        <div v-else-if="mode[g.key] === 'amount'" class="gap-detail">
          <label class="amt"><span>{{ LABEL[g.field] }}</span><input v-model="amounts[g.key]" type="text" inputmode="numeric" placeholder="amount" /></label>
          <input v-model="notes[g.key]" type="text" class="grow" placeholder="where you saw it (optional)" maxlength="500" />
        </div>
        <div v-else-if="mode[g.key] === 'added'" class="gap-detail muted small">Fill in the event under Events Missing from the Log. Its {{ LABEL[g.field] }} is set to {{ signed(gapAmount(g)) }}.</div>
      </div>
    </section>

    <section v-if="canFillAction" class="rv-section">
      <h4 class="rv-h">Action Played This Turn</h4>
      <p v-if="reportAction" class="gap-q" style="margin-top: 0">The report saw <b>{{ reportAction.text }}</b>. Pick another action only if that is wrong.</p>
      <p v-else class="gap-q" style="margin-top: 0">The report didn't see what you played. Pick it.</p>
      <div class="seg">
        <button :class="{ on: actionKind === '' }" @click="actionKind = ''">{{ reportAction ? "As the Report Says" : "Leave It Blank" }}</button>
        <button v-for="k in ['training', 'rest', 'outing', 'race']" :key="k" :class="{ on: actionKind === k }" @click="actionKind = k">{{ KIND_LABEL[k] }}</button>
      </div>
      <div v-if="actionKind" class="gap-detail">
        <select v-if="actionKind === 'training'" v-model="option">
          <option v-for="o in OPTIONS" :key="o" :value="o">{{ LABEL[o] }} training</option>
        </select>
        <input v-model="name" type="text" class="grow" :placeholder="actionKind === 'training' ? 'name of the training, if you like' : actionKind === 'race' ? 'name of the race, if you like' : 'a note, if you like'" maxlength="80" />
      </div>
      <div v-if="actionKind === 'training'" class="rv-amounts">
        <span class="muted small" style="flex-basis: 100%">What the training gave, if you saw it:</span>
        <label v-for="f in STAT_FIELDS" :key="f" class="amt"><i class="sd" :class="f"></i><span>{{ LABEL[f] }}</span><input v-model="gains[f]" type="text" inputmode="numeric" placeholder="+0" /></label>
      </div>
    </section>

    <section class="rv-section">
      <h4 class="rv-h">
        Lines to Check <span class="rv-count">{{ shownEntries.length }}<template v-if="shownEntries.length !== editableEntries.length"> of {{ editableEntries.length }}</template></span>
        <button v-if="editableEntries.length > shownEntries.length || showAllEntries" class="linkish small" style="margin-left: auto" @click="showAllEntries = !showAllEntries">{{ showAllEntries ? "Only the Flagged Ones" : "Show Every Line" }}</button>
      </h4>
      <p v-if="!shownEntries.length" class="muted small">No line in this turn needs a look. "Show Every Line" lists each one with a number, in case you want to fix one.</p>
      <div v-for="e in shownEntries" :id="'review-' + e.id" :key="e.id" class="rv-entry" :class="{ deleted: entryRows[e.id]?.deleted, focus: e.id === focusEntry, edited: edited(e) }">
        <div class="rv-entry-head">
          <button class="tchip" :disabled="e.first_seen_ms === null" @click="e.first_seen_ms !== null && emit('seek', e.first_seen_ms)">{{ clock(e.first_seen_ms) }}</button>
          <b class="rv-entry-name">{{ entryName(e) }}</b>
          <span v-for="w in entryWarnings(e)" :key="w.text" class="tag small" :class="w.serious ? 'pink' : 'grey'">{{ titleCase(w.text) }}</span>
          <span v-if="entryRows[e.id]?.deleted" class="tag grey small">Removed</span>
        </div>
        <ul v-if="guided && entryWarnings(e).some((w) => w.advice)" class="advice">
          <li v-for="w in entryWarnings(e).filter((w) => w.advice)" :key="w.text">{{ w.advice }}</li>
        </ul>
        <div v-if="entryRows[e.id]" class="rv-amounts">
          <label v-for="key in Object.keys(entryRows[e.id].amounts)" :key="key" class="amt" :class="{ changed: entryRows[e.id].amounts[key].trim() !== String(reportedAmount(e, split(key).channel, split(key).field) ?? '') }">
            <i class="sd" :class="split(key).field"></i><span>{{ LABEL[split(key).field] ?? split(key).field }}</span>
            <button class="step" tabindex="-1" :disabled="entryRows[e.id].deleted" @click="bump(entryRows[e.id], key, -1)">−</button>
            <input v-model="entryRows[e.id].amounts[key]" type="text" inputmode="numeric" :disabled="entryRows[e.id].deleted" />
            <button class="step" tabindex="-1" :disabled="entryRows[e.id].deleted" @click="bump(entryRows[e.id], key, 1)">+</button>
            <button v-if="reportedAmount(e, split(key).channel, split(key).field) === null" class="step x" title="remove this stat" @click="dropField(entryRows[e.id], key)">✕</button>
          </label>
          <select class="add-field" :disabled="entryRows[e.id].deleted" @change="addField(entryRows[e.id], ($event.target as HTMLSelectElement).value); ($event.target as HTMLSelectElement).value = ''">
            <option value="">+ Stat</option>
            <option v-for="f in STAT_FIELDS" :key="f" :value="'stats:' + f">{{ LABEL[f] }}</option>
            <option v-for="f in PERF_FIELDS" :key="f" :value="'performance:' + f">{{ LABEL[f] }}</option>
          </select>
        </div>
        <div v-if="entryRows[e.id]" class="rv-entry-foot">
          <button class="toggle" :class="{ on: entryRows[e.id].reviewed }" @click="entryRows[e.id].reviewed = !entryRows[e.id].reviewed">✓ Looks Right</button>
          <button class="toggle danger" :class="{ on: entryRows[e.id].deleted }" @click="entryRows[e.id].deleted = !entryRows[e.id].deleted">Didn't Happen</button>
          <button v-if="!openNotes[e.id] && !entryRows[e.id].note" class="linkish small" @click="openNotes[e.id] = true">Add a Note</button>
          <input v-else v-model="entryRows[e.id].note" type="text" class="grow" placeholder="note" maxlength="500" />
        </div>
      </div>
    </section>

    <section class="rv-section">
      <h4 class="rv-h">Events Missing from the Log <span v-if="added.length" class="rv-count">{{ added.length }}</span><button class="btn small" style="margin-left: auto" @click="newAdded()">+ Add Event</button></h4>
      <p v-if="!added.length" class="muted small">An event, a race, a lesson the report has no line for? Add it with the time it happened and what it changed.</p>
      <div v-for="a in added" :id="'review-' + a.id" :key="a.id" class="rv-entry added">
        <div class="seg small">
          <button v-for="k in ADDED_KINDS" :key="k" :class="{ on: a.kind === k }" @click="a.kind = k">{{ KIND_LABEL[k] }}</button>
        </div>
        <div class="rv-entry-head">
          <input v-model="a.title" type="text" class="grow" placeholder="what the game called it" maxlength="80" />
          <label class="amt time"><span>at</span><input v-model="a.time" type="text" placeholder="m:ss" /></label>
          <button class="btn small quiet" :disabled="videoMs == null" title="use the recording's current time" @click="useVideoTime(a)">Use the Video's Time</button>
        </div>
        <div class="rv-amounts">
          <label v-for="key in Object.keys(a.amounts)" :key="key" class="amt changed">
            <i class="sd" :class="split(key).field"></i><span>{{ LABEL[split(key).field] ?? split(key).field }}</span>
            <button class="step" tabindex="-1" @click="bump(a, key, -1)">−</button>
            <input v-model="a.amounts[key]" type="text" inputmode="numeric" />
            <button class="step" tabindex="-1" @click="bump(a, key, 1)">+</button>
            <button class="step x" title="remove this stat" @click="dropField(a, key)">✕</button>
          </label>
          <select class="add-field" @change="addField(a, ($event.target as HTMLSelectElement).value); ($event.target as HTMLSelectElement).value = ''">
            <option value="">+ Stat</option>
            <option v-for="f in STAT_FIELDS" :key="f" :value="'stats:' + f">{{ LABEL[f] }}</option>
            <option v-for="f in PERF_FIELDS" :key="f" :value="'performance:' + f">{{ LABEL[f] }}</option>
          </select>
        </div>
        <div class="rv-entry-foot">
          <input v-model="a.note" type="text" class="grow" placeholder="note (optional)" maxlength="500" />
          <button class="linkish small danger" @click="removeAdded(a.id)">Remove</button>
        </div>
      </div>
    </section>

    <footer class="rv-foot">
      <input v-model="note" type="text" class="grow" placeholder="anything else about this turn (optional)" maxlength="500" />
      <div class="rv-foot-actions">
        <label class="rv-resolved" title="Removes the turn from the Check list even when its numbers cannot be proven from the recording."><input v-model="resolved" type="checkbox" /> Resolved</label>
        <span v-if="error" class="warn-text small">{{ error }}</span>
        <button v-if="correction" class="btn small" :disabled="busy" @click="remove">Undo All Answers</button>
        <button class="btn primary" :disabled="busy" @click="save">Save</button>
      </div>
    </footer>

    <div v-if="result" class="verify" :class="result.verified ? 'ok' : result.balanced ? 'na' : 'off'">
      <b>Saved.<template v-if="savedAction"> You played {{ savedAction }}.</template> {{ result.summary === "Nothing to Check" ? "There were no next-turn numbers to check this against." : result.summary }}</b>
      <ul class="verify-list">
        <li v-for="f in result.fields" :key="f.channel + f.field" :class="f.status">
          <i class="sd" :class="f.field"></i><b>{{ LABEL[f.field] ?? f.field }}</b>
          <template v-if="f.status === 'balanced' || f.status === 'off'"><span class="tabular">{{ f.before ?? "?" }} {{ signed(f.recorded) }} report {{ signed(f.supplied) }} yours</span></template>
          <span v-if="f.status === 'balanced'" class="vs ok">= {{ f.after }} ✓</span>
          <span v-else-if="f.status === 'off'" class="vs off">≠ {{ f.after }}, off by {{ signed((f.residual ?? 0) - f.supplied) }}</span>
          <span v-else-if="f.status === 'open'" class="vs warn">{{ signed(f.residual ?? 0) }} still not covered<template v-if="windowText(f.window_start_ms, f.window_end_ms)"> · {{ windowText(f.window_start_ms, f.window_end_ms) }}</template></span>
          <span v-else-if="f.status === 'turn_difference'" class="vs na">{{ signed(f.turn_difference) }} worked out by the report. An entered amount replaces it.</span>
          <span v-else class="vs na">a value before or after was not observed</span>
        </li>
      </ul>
    </div>
  </div>
</template>
