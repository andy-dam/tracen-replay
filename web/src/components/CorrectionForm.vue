<script setup lang="ts">
import { computed, nextTick, reactive, ref, watch } from "vue";
import { api, ApiError, type AddedEvent, type Correction, type CorrectionChange, type Entry, type EntryEdit, type Turn, type TurnSummary, type Verification } from "../api";
import { clock } from "../format";
import { entryName, entryWarnings } from "../warnings";

// The review editor for one turn. The report keeps its own reading; the
// viewer's edits are stored beside it and checked by the server against the
// values observed at the start of the next turn.
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
}>();
const emit = defineEmits<{ saved: []; close: []; seek: [ms: number] }>();

const STAT_FIELDS = ["speed", "stamina", "power", "guts", "wit", "skill_points"] as const;
const PERF_FIELDS = ["dance", "passion", "vocal", "visual", "composure"] as const;
const LABEL: Record<string, string> = { speed: "Speed", stamina: "Stamina", power: "Power", guts: "Guts", wit: "Wit", skill_points: "Skill Pts", dance: "Dance", passion: "Passion", vocal: "Vocal", visual: "Visual", composure: "Composure" };
const OPTIONS = ["speed", "stamina", "power", "guts", "wit"] as const;
const ADDED_KINDS = ["event", "training", "race", "rest", "outing", "purchase"] as const;

const actionKind = ref("");
const option = ref("speed");
const name = ref("");
const gains = reactive<Record<string, string>>({});
const amounts = reactive<Record<string, string>>({});
const notes = reactive<Record<string, string>>({});
const note = ref("");
const busy = ref(false);
const error = ref("");
const result = ref<Verification | null>(props.verification);
const showAllEntries = ref(false);

interface EntryRow {
  id: string;
  amounts: Record<string, string>; // "channel:field" -> text
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
  amounts: Record<string, string>;
  note: string;
}
const added = ref<AddedRow[]>([]);

const canFillAction = computed(() => !!props.summaryTurn?.expects_one_action && (!props.hasAction || !!props.correction?.action));

// The differences: stats whose values at this turn and the next do not add
// up, or that were worked out onto the training from the difference.
const rows = computed(() => {
  const existing = new Set((props.correction?.changes ?? []).map((c) => c.field));
  const out: { field: string; before: number | null; after: number | null; recorded: number; residual: number | null; workedOut: number; windowStart: number | null; windowEnd: number | null }[] = [];
  for (const field of STAT_FIELDS) {
    const fa = props.turn.accounting?.stats?.[field];
    const before = fa?.before ?? null;
    const after = fa?.after ?? null;
    const workedOut = fa?.turn_difference ?? 0;
    const recorded = (fa?.direct ?? 0) + (fa?.derived ?? 0) - workedOut;
    const residual = before !== null && after !== null ? after - before - recorded : null;
    const status = fa?.status ?? "";
    const open = (residual !== null && residual !== 0) || ["unexplained_change", "unresolved_attribution", "missing_endpoint"].includes(status);
    if (open || existing.has(field) || workedOut) out.push({ field, before, after, recorded, residual, workedOut, windowStart: fa?.window_start_ms ?? null, windowEnd: fa?.window_end_ms ?? null });
  }
  return out;
});

function reportedAmount(e: Entry, channel: string, field: string): number | null {
  const c = e.changes?.[channel]?.[field];
  return c && typeof c.amount === "number" ? c.amount : null;
}
function fieldsOf(e: Entry): { channel: string; field: string }[] {
  const out: { channel: string; field: string }[] = [];
  for (const channel of ["stats", "performance"]) for (const field of Object.keys(e.changes?.[channel] ?? {})) out.push({ channel, field });
  return out;
}
function flagged(e: Entry): boolean {
  return entryWarnings(e).length > 0;
}
const editableEntries = computed(() => props.entries.filter((e) => fieldsOf(e).length || flagged(e)));
const shownEntries = computed(() => {
  if (showAllEntries.value) return editableEntries.value;
  return editableEntries.value.filter((e) => flagged(e) || props.correction?.entries?.[e.id] || e.id === props.focusEntry);
});

function load() {
  const c = props.correction;
  actionKind.value = c?.action?.kind ?? "";
  option.value = c?.action?.training_option || "speed";
  name.value = c?.action?.name ?? "";
  for (const f of STAT_FIELDS) {
    gains[f] = c?.action?.gains?.[f] !== undefined ? String(c.action!.gains![f]) : "";
    const change = c?.changes.find((x) => x.field === f && (x.channel ?? "stats") === "stats");
    amounts[f] = change ? String(change.amount) : "";
    notes[f] = change?.note ?? "";
  }
  for (const key of Object.keys(entryRows)) delete entryRows[key];
  for (const e of props.entries) {
    const edit = c?.entries?.[e.id];
    const row: EntryRow = { id: e.id, amounts: {}, deleted: !!edit?.deleted, reviewed: !!edit?.reviewed, note: edit?.note ?? "" };
    for (const { channel, field } of fieldsOf(e)) {
      const override = edit?.changes?.[channel]?.[field];
      const reported = reportedAmount(e, channel, field);
      row.amounts[`${channel}:${field}`] = override !== undefined ? String(override) : reported !== null ? String(reported) : "";
    }
    for (const [channel, fields] of Object.entries(edit?.changes ?? {})) for (const [field, v] of Object.entries(fields)) row.amounts[`${channel}:${field}`] = String(v);
    entryRows[e.id] = row;
  }
  added.value = (c?.added ?? []).map((a) => ({
    id: a.id, kind: a.kind, title: a.title ?? "", time: a.time_ms != null ? clock(a.time_ms) : "",
    amounts: Object.fromEntries(Object.entries(a.changes ?? {}).flatMap(([ch, fs]) => Object.entries(fs).map(([f, v]) => [`${ch}:${f}`, String(v)]))),
    note: a.note ?? "",
  }));
  note.value = c?.note ?? "";
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
function window(a: number | null | undefined, b: number | null | undefined) {
  return a != null && b != null ? `between ${clock(a)} and ${clock(b)}` : "";
}

function addEvent() {
  added.value.push({ id: "user-" + Date.now().toString(36), kind: "event", title: "", time: props.videoMs != null ? clock(props.videoMs) : clock(props.turn.start_ms ?? 0), amounts: {}, note: "" });
}
function removeAdded(id: string) {
  added.value = added.value.filter((a) => a.id !== id);
}
function addField(row: { amounts: Record<string, string> }, key: string) {
  if (key && !(key in row.amounts)) row.amounts[key] = "";
}

async function save() {
  error.value = "";
  const changes: CorrectionChange[] = [];
  for (const f of STAT_FIELDS) {
    const n = num(amounts[f] ?? "");
    if (amounts[f]?.trim() && n === null) return void (error.value = `${LABEL[f]}: enter a whole number`);
    if (n) changes.push({ field: f, amount: n, note: notes[f]?.trim() || undefined });
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
      const [channel, field] = key.split(":");
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
      const [channel, field] = key.split(":");
      const n = num(text);
      if (text.trim() && n === null) return void (error.value = `${a.title || "added event"} ${LABEL[field] ?? field}: enter a whole number`);
      if (n) (ch[channel] ??= {})[field] = n;
    }
    const ms = a.time.trim() ? parseClock(a.time) : null;
    if (a.time.trim() && ms === null) return void (error.value = `${a.title || "added event"}: time as m:ss`);
    addedOut.push({ id: a.id, kind: a.kind, title: a.title.trim() || undefined, time_ms: ms, changes: Object.keys(ch).length ? ch : undefined, note: a.note.trim() || undefined });
  }
  if (!action && !changes.length && !Object.keys(entries).length && !addedOut.length) return void (error.value = "Nothing to save yet.");
  busy.value = true;
  try {
    const saved = await api.saveCorrection(props.reportId, props.turn.id, { action, changes, entries: Object.keys(entries).length ? entries : undefined, added: addedOut.length ? addedOut : undefined, note: note.value.trim() || undefined });
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
  <div class="fillin">
    <div class="row" style="justify-content: space-between; align-items: baseline">
      <b>Review This Turn</b>
      <button class="btn quiet small" @click="emit('close')">Close</button>
    </div>
    <p class="muted small" style="margin: 4px 0 10px">The report keeps its own reading. What you enter here is stored beside it and checked against the stats observed at the start of the next turn.</p>

    <div v-if="rows.length" class="fillin-block">
      <b class="small">Differences</b>
      <div v-for="r in rows" :key="r.field" class="diff-line">
        <div class="diff-text">
          <span class="strong">{{ LABEL[r.field] }} {{ r.residual !== null ? signed(r.residual) : r.workedOut ? signed(r.workedOut) : "?" }}</span>
          <template v-if="r.workedOut"> was worked out from the difference between turns onto the training</template>
          <template v-else-if="r.residual === null"> cannot be checked: a value before or after this turn was not observed</template>
          <template v-else> is not covered by any captured event</template>
          <template v-if="window(r.windowStart, r.windowEnd)"> · <button class="linkish" @click="emit('seek', r.windowStart!)">{{ window(r.windowStart, r.windowEnd) }}</button></template>
          <span class="muted"> · observed {{ r.before ?? "?" }} → {{ r.after ?? "?" }}, report explains {{ signed(r.recorded) }}</span>
        </div>
        <div class="diff-inputs">
          <input v-model="amounts[r.field]" type="text" inputmode="numeric" :placeholder="r.workedOut ? `${signed(r.workedOut)} worked out` : 'amount you saw'" />
          <input v-model="notes[r.field]" type="text" placeholder="where it came from (e.g. an event's receipt)" maxlength="500" />
        </div>
      </div>
    </div>

    <div v-if="canFillAction" class="fillin-block">
      <b class="small">Action</b>
      <div class="fillin-row">
        <select v-model="actionKind">
          <option value="">Not filled in</option>
          <option value="training">Training</option>
          <option value="rest">Rest</option>
          <option value="outing">Outing</option>
          <option value="race">Race</option>
        </select>
        <template v-if="actionKind === 'training'">
          <select v-model="option">
            <option v-for="o in OPTIONS" :key="o" :value="o">{{ LABEL[o] }}</option>
          </select>
          <input v-model="name" type="text" placeholder="training name (optional)" maxlength="80" />
        </template>
        <input v-else-if="actionKind" v-model="name" type="text" placeholder="name (optional)" maxlength="80" />
      </div>
      <div v-if="actionKind === 'training'" class="fillin-gains">
        <label v-for="f in STAT_FIELDS" :key="f"><span>{{ LABEL[f] }}</span><input v-model="gains[f]" type="text" inputmode="numeric" placeholder="+0" /></label>
      </div>
    </div>

    <div class="fillin-block">
      <div class="row" style="justify-content: space-between; align-items: baseline">
        <b class="small">Events in this turn <span class="muted">{{ shownEntries.length }} of {{ editableEntries.length }}</span></b>
        <button v-if="editableEntries.length > shownEntries.length || showAllEntries" class="linkish small" @click="showAllEntries = !showAllEntries">{{ showAllEntries ? "Show flagged only" : "Show all" }}</button>
      </div>
      <p v-if="!shownEntries.length" class="muted small">Nothing flagged. "Show all" lists every event with an amount.</p>
      <div v-for="e in shownEntries" :id="'review-' + e.id" :key="e.id" class="review-entry" :class="{ deleted: entryRows[e.id]?.deleted, focus: e.id === focusEntry }">
        <div class="review-head">
          <button class="linkish" :disabled="e.first_seen_ms === null" @click="e.first_seen_ms !== null && emit('seek', e.first_seen_ms)"><span class="tabular">{{ clock(e.first_seen_ms) }}</span></button>
          <span class="strong">{{ entryName(e) }}</span>
          <span v-if="flagged(e)" class="tag pink small">{{ entryWarnings(e).map((w) => w.text).join("; ") }}</span>
        </div>
        <div v-if="entryRows[e.id]" class="review-fields">
          <label v-for="key in Object.keys(entryRows[e.id].amounts)" :key="key"><span>{{ LABEL[key.split(':')[1]] ?? key.split(':')[1] }}</span><input v-model="entryRows[e.id].amounts[key]" type="text" inputmode="numeric" :disabled="entryRows[e.id].deleted" /></label>
          <select class="add-field" @change="addField(entryRows[e.id], ($event.target as HTMLSelectElement).value); ($event.target as HTMLSelectElement).value = ''">
            <option value="">+ stat</option>
            <option v-for="f in STAT_FIELDS" :key="f" :value="'stats:' + f">{{ LABEL[f] }}</option>
            <option v-for="f in PERF_FIELDS" :key="f" :value="'performance:' + f">{{ LABEL[f] }}</option>
          </select>
          <label class="check"><input v-model="entryRows[e.id].reviewed" type="checkbox" /> reviewed</label>
          <label class="check"><input v-model="entryRows[e.id].deleted" type="checkbox" /> not real, remove</label>
          <input v-model="entryRows[e.id].note" type="text" placeholder="note" maxlength="500" class="review-note" />
        </div>
      </div>
    </div>

    <div class="fillin-block">
      <div class="row" style="justify-content: space-between; align-items: baseline">
        <b class="small">Events the report missed</b>
        <button class="btn quiet small" @click="addEvent">Add an Event</button>
      </div>
      <div v-for="a in added" :key="a.id" class="review-entry added">
        <div class="review-fields">
          <select v-model="a.kind">
            <option v-for="k in ADDED_KINDS" :key="k" :value="k">{{ k[0].toUpperCase() + k.slice(1) }}</option>
          </select>
          <input v-model="a.title" type="text" placeholder="title (e.g. Shine On Forever)" maxlength="80" style="min-width: 160px" />
          <label><span>at</span><input v-model="a.time" type="text" placeholder="m:ss" style="width: 70px" /></label>
          <label v-for="key in Object.keys(a.amounts)" :key="key"><span>{{ LABEL[key.split(':')[1]] ?? key.split(':')[1] }}</span><input v-model="a.amounts[key]" type="text" inputmode="numeric" /></label>
          <select class="add-field" @change="addField(a, ($event.target as HTMLSelectElement).value); ($event.target as HTMLSelectElement).value = ''">
            <option value="">+ stat</option>
            <option v-for="f in STAT_FIELDS" :key="f" :value="'stats:' + f">{{ LABEL[f] }}</option>
            <option v-for="f in PERF_FIELDS" :key="f" :value="'performance:' + f">{{ LABEL[f] }}</option>
          </select>
          <input v-model="a.note" type="text" placeholder="note" maxlength="500" class="review-note" />
          <button class="linkish small" @click="removeAdded(a.id)">remove</button>
        </div>
      </div>
    </div>

    <div class="fillin-row" style="margin-top: 8px">
      <label>Note</label>
      <input v-model="note" type="text" placeholder="optional" maxlength="500" style="flex: 1" />
    </div>
    <div class="row" style="gap: 8px; margin-top: 10px; flex-wrap: wrap">
      <button class="btn primary small" :disabled="busy" @click="save">Save and Check</button>
      <button v-if="correction" class="btn small" :disabled="busy" @click="remove">Remove My Edits</button>
      <span v-if="error" class="warn small">{{ error }}</span>
    </div>

    <div v-if="result" class="verify" :class="result.verified ? 'ok' : result.balanced ? 'na' : 'off'">
      <b>{{ result.summary }}</b>
      <div v-for="f in result.fields" :key="f.channel + f.field" class="small tabular">
        {{ LABEL[f.field] ?? f.field }}:<template v-if="f.status === 'balanced' || f.status === 'off'"> {{ f.before ?? "?" }} + {{ signed(f.recorded) }} report + {{ signed(f.supplied) }} yours</template>
        <template v-if="f.status === 'balanced'"> = {{ f.after }} observed ✓</template>
        <template v-else-if="f.status === 'off'"> ≠ {{ f.after }} observed (off by {{ signed((f.residual ?? 0) - f.supplied) }})</template>
        <template v-else-if="f.status === 'open'"> {{ signed(f.residual ?? 0) }} still not covered by any event {{ window(f.window_start_ms, f.window_end_ms) }}</template>
        <template v-else-if="f.status === 'turn_difference'"> {{ signed(f.turn_difference) }} worked out from the difference between turns; enter your own amount above to replace it</template>
        <template v-else> the value before or after was not observed, so this cannot be checked</template>
      </div>
    </div>
  </div>
</template>
