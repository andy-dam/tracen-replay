<script setup lang="ts">
import { computed } from "vue";
import type { Entry, TurnSummary } from "../api";
import { clock, CORE_STATS, gradeClass, shortLabel, STAT_NAMES, statRank, yearOf } from "../format";
import RankBadge from "./RankBadge.vue";

// How the run was played, counted from what the ledger recorded: the
// turns' committed actions, the trainings' observed gains, the races, the
// shop, the events. Every item opens its turn. Nothing is estimated.
const props = defineProps<{ turns: TurnSummary[]; entries: Entry[]; final: Record<string, number | null> | null }>();
const emit = defineEmits<{ select: [id: string, ms?: number | null] }>();

type Detail = Record<string, unknown>;
const detail = (e: Entry): Detail => (e.detail ?? {}) as Detail;
const num = (v: unknown): number | null => (typeof v === "number" ? v : null);
const str = (v: unknown): string => (typeof v === "string" ? v : "");
const turnOf = (e: Entry) => props.turns.find((t) => t.id === e.turn_id) ?? null;
const where = (e: Entry) => {
  const t = turnOf(e);
  return t ? shortLabel(t.label) : clock(e.first_seen_ms);
};
function go(e: Entry) {
  if (e.turn_id) emit("select", e.turn_id, e.first_seen_ms);
}

const OPTIONS = ["speed", "stamina", "power", "guts", "wit"];
const YEARS = ["Junior", "Classic", "Senior", "Finale"];
const yearKey = (t: TurnSummary) => {
  const y = yearOf(t.label, t.phase);
  return y === "Pre-Debut" ? "Junior" : y.replace(" Year", "");
};
/** A turn named in two short lines: the year, then the half-month or the phase. */
function whenParts(t: TurnSummary): { year: string; when: string } {
  const y = yearOf(t.label, t.phase);
  if (y === "Pre-Debut") return { year: "Junior", when: "Pre-Debut" };
  if (y === "Finale") return { year: "Finale", when: shortLabel(t.label).replace("URA Finale ", "") };
  return { year: y.replace(" Year", ""), when: shortLabel(t.label) };
}

// 1. Turns by what was chosen, overall and per year.
const choices = computed(() => {
  const count = (list: TurnSummary[]) => {
    const c: Record<string, number> = { speed: 0, stamina: 0, power: 0, guts: 0, wit: 0, race: 0, rest: 0, outing: 0, infirmary: 0, other: 0, none: 0 };
    for (const t of list) {
      if (t.action_status === "missing_action" && t.expects_one_action) c.none++;
      else if (t.action_kind === "training" && t.training_option && t.training_option in c) c[t.training_option]++;
      else if (t.action_kind && t.action_kind in c) c[t.action_kind]++;
      else c.other++;
    }
    return c;
  };
  const byYear = YEARS.map((y) => {
    const list = props.turns.filter((t) => yearKey(t) === y);
    return { year: y, counts: count(list), total: list.length };
  }).filter((y) => y.total);
  return { all: count(props.turns), total: props.turns.length, byYear };
});
const trainingTurns = computed(() => OPTIONS.reduce((n, o) => n + choices.value.all[o], 0));
const share = (n: number) => (choices.value.total ? Math.round((100 * n) / choices.value.total) : 0);

// 2. Training efficiency per stat, from the trainings whose gains were read.
const efficiency = computed(() =>
  OPTIONS.map((field) => {
    const sessions = props.entries.filter((e) => e.kind === "training" && (e.training_option ?? str(detail(e).training_option)) === field);
    let total = 0;
    let best: { entry: Entry; gain: number } | null = null;
    for (const e of sessions) {
      const gain = e.changes?.stats?.[field]?.amount ?? 0;
      total += gain;
      if (!best || gain > best.gain) best = { entry: e, gain };
    }
    return { field, sessions: sessions.length, total, average: sessions.length ? total / sessions.length : null, best };
  }),
);
const trainings = computed(() => {
  const list = props.entries.filter((e) => e.kind === "training");
  let gainTotal = 0;
  let failures = 0;
  let best: { entry: Entry; total: number } | null = null;
  for (const e of list) {
    if (detail(e).training_outcome === "failure") failures++;
    let total = 0;
    for (const [field, c] of Object.entries(e.changes?.stats ?? {})) if (c.amount !== null && field !== "skill_points") total += c.amount;
    gainTotal += total;
    if (!best || total > best.total) best = { entry: e, total };
  }
  return { count: list.length, failures, average: list.length ? gainTotal / list.length : null, best };
});

// 3. Growth by year: the value each stat stood at when the year ended (the
// opening of the next year's first observed turn) and the gain in between.
interface YearEnd {
  value: number | null;
  gain: number | null;
}
const growth = computed(() => {
  const start = (year: string, field: string): number | null => {
    const t = props.turns.filter((t) => yearKey(t) === year).find((t) => typeof t.opening.stats?.[field] === "number");
    return t ? (t.opening.stats?.[field] as number) : null;
  };
  const lastObserved = (field: string): number | null => {
    const t = [...props.turns].reverse().find((t) => typeof t.opening.stats?.[field] === "number");
    return t ? (t.opening.stats?.[field] as number) : null;
  };
  const hasFinale = props.turns.some((t) => yearKey(t) === "Finale");
  const cell = (from: number | null, to: number | null): YearEnd => ({ value: to, gain: from === null || to === null ? null : to - from });
  return CORE_STATS.map((field) => {
    const j = start("Junior", field);
    const c = start("Classic", field);
    const s = start("Senior", field);
    const f = hasFinale ? start("Finale", field) : null;
    const end = props.final?.[field] ?? lastObserved(field);
    const seniorEnd = hasFinale && f !== null ? f : end;
    return { field, start: j, cells: [cell(j, c), cell(c, s), cell(s, seniorEnd), ...(hasFinale ? [cell(f, end)] : [])] };
  });
});
const growthHeads = computed(() => (props.turns.some((t) => yearKey(t) === "Finale") ? ["Junior", "Classic", "Senior", "Finale"] : ["Junior", "Classic", "Senior"]));

// 4. Rank milestones: the first turn whose opening reached each threshold.
const MILESTONES: [number, string][] = [[600, "B"], [800, "A"], [1000, "S"], [1100, "SS"], [1200, "UG"]];
const milestones = computed(() =>
  CORE_STATS.map((field) => ({
    field,
    cells: MILESTONES.map(([threshold, rank]) => ({ rank, threshold, turn: props.turns.find((t) => (t.opening.stats?.[field] ?? -1) >= threshold) ?? null })),
    end: props.final?.[field] ?? null,
  })),
);

// 5. Races, in order, each with its turn.
const races = computed(() => {
  const list = props.entries.filter((e) => e.kind === "races").map((e) => {
    const d = detail(e);
    return { entry: e, name: str(d.race_name) || "race", placing: num(d.placing), fans: num(d.fans_gained), grade: str(d.race_grade) };
  });
  const placed = list.filter((r) => r.placing !== null);
  const wins = placed.filter((r) => r.placing === 1).length;
  return {
    list,
    wins,
    rate: placed.length ? Math.round((100 * wins) / placed.length) : null,
    g1wins: list.filter((r) => r.grade.toUpperCase() === "G1" && r.placing === 1).length,
    fans: list.reduce((n, r) => n + (r.fans ?? 0), 0),
    fansKnown: list.every((r) => r.fans !== null),
  };
});

// 6. Skill points and skills.
const skills = computed(() => {
  let earned = 0;
  let spent = 0;
  for (const e of props.entries) {
    if (e.accounting_role === "reference_only_not_an_additional_award") continue;
    const amount = e.changes?.stats?.skill_points?.amount ?? 0;
    if (amount > 0) earned += amount;
    else spent += -amount;
  }
  const bought = props.entries
    .filter((e) => e.kind === "skill_purchases")
    .flatMap((e) => (Array.isArray(detail(e).visible_confirmation_names) ? (detail(e).visible_confirmation_names as string[]).map((name) => ({ name, entry: e })) : []));
  const hintMap = new Map<string, { levels: number; entry: Entry }>();
  for (const e of props.entries.filter((e) => e.kind === "skill_hint_change")) {
    const eff = (e.effect ?? {}) as Detail;
    const name = str(eff.name);
    if (!name) continue;
    const current = hintMap.get(name);
    const levels = num(eff.amount) ?? 0;
    if (current) current.levels += levels;
    else hintMap.set(name, { levels, entry: e });
  }
  const hints = [...hintMap.entries()].map(([name, v]) => ({ name, ...v })).sort((a, b) => b.levels - a.levels);
  return { earned, spent, left: props.final?.skill_points ?? null, bought, hints, hintLevels: hints.reduce((n, h) => n + h.levels, 0) };
});

// 7. Rest and energy.
const energy = computed(() => {
  const rests = props.turns.filter((t) => t.action_kind === "rest").length;
  const outings = props.turns.filter((t) => t.action_kind === "outing").length;
  const infirmary = props.turns.filter((t) => t.action_kind === "infirmary").length;
  let recovered = 0;
  let recoveredKnown = false;
  for (const e of props.entries.filter((e) => e.kind === "committed_action" && e.action_kind === "rest")) {
    const effects = detail(e).recovery_effects;
    if (Array.isArray(effects)) {
      for (const eff of effects as Detail[]) if (eff.kind === "energy_change" && num(eff.amount) !== null) {
        recovered += num(eff.amount) as number;
        recoveredKnown = true;
      }
    }
  }
  let streak = 0;
  let longest = 0;
  for (const t of props.turns) {
    if (t.action_kind === "training" || t.action_kind === "race") streak++;
    else streak = 0;
    if (streak > longest) longest = streak;
  }
  return { rests, outings, infirmary, recovered: recoveredKnown ? recovered : null, longest };
});

// 8. Events: outcomes and what they gave.
const events = computed(() => {
  const list = props.entries.filter((e) => e.kind === "outcome");
  let statTotal = 0;
  let skillTotal = 0;
  const scored = list.map((e) => {
    let total = 0;
    for (const [field, c] of Object.entries(e.changes?.stats ?? {})) {
      if (c.amount === null) continue;
      if (field === "skill_points") skillTotal += c.amount;
      else {
        total += c.amount;
        statTotal += c.amount;
      }
    }
    return { entry: e, title: e.context_title || "event", total };
  });
  return { count: list.length, statTotal, skillTotal, top: scored.filter((s) => s.total > 0).sort((a, b) => b.total - a.total).slice(0, 8) };
});

// 9. The scenario's own economy, only when the ledger recorded it.
const scenario = computed(() => {
  const lessons = props.entries.filter((e) => e.kind === "lesson_purchases");
  let performanceSpent = 0;
  for (const e of lessons) {
    const cost = detail(e).performance_cost;
    if (cost && typeof cost === "object") for (const v of Object.values(cost as Detail)) performanceSpent += num(v) ?? 0;
  }
  const songs = props.entries.filter((e) => e.kind === "song_acquisitions").map((e) => ({ name: str(detail(e).name) || "song", entry: e }));
  const concerts = props.entries.filter((e) => e.kind === "concerts").map((e) => ({ title: str(detail(e).title) || "concert", result: str(detail(e).result).replaceAll("_", " "), entry: e }));
  return { lessons: lessons.length, performanceSpent, songs, concerts, any: lessons.length + songs.length + concerts.length > 0 };
});

const CHOICE_LABELS: Record<string, string> = { speed: "Speed", stamina: "Stamina", power: "Power", guts: "Guts", wit: "Wit", race: "Race", rest: "Rest", outing: "Outing", infirmary: "Infirmary", other: "Other", none: "No Action Seen" };
const CHOICE_ORDER = ["speed", "stamina", "power", "guts", "wit", "race", "rest", "outing", "infirmary", "other", "none"];
const plus = (n: number | null) => (n === null ? "?" : (n > 0 ? "+" : "") + n);
const place = (p: number | null) => (p === null ? "?" : p === 1 ? "1st" : p === 2 ? "2nd" : p === 3 ? "3rd" : `${p}th`);
</script>

<template>
  <div class="analytics">
    <div class="an-card wide">
      <h3>Turn Choices</h3>
      <div class="an-body">
        <div class="growth-grid" style="gap: 18px">
          <div>
            <div class="choice-bar" :title="`${choices.total} turns`">
              <i v-for="k in CHOICE_ORDER" :key="k" v-show="choices.all[k]" :class="k" :style="{ flex: choices.all[k] }" :title="`${CHOICE_LABELS[k]}: ${choices.all[k]}`"></i>
            </div>
            <div class="choice-legend">
              <span v-for="k in CHOICE_ORDER" :key="k" v-show="choices.all[k]"><i :class="k"></i>{{ CHOICE_LABELS[k] }} <b class="tabular">{{ choices.all[k] }}</b> <span class="muted">{{ share(choices.all[k]) }}%</span></span>
            </div>
            <p class="muted small" style="margin-top: 10px">{{ trainingTurns }} of {{ choices.total }} turns went to training; the most trained stat was {{ CHOICE_LABELS[OPTIONS.slice().sort((a, b) => choices.all[b] - choices.all[a])[0]] }}.</p>
          </div>
          <table class="ledger years">
            <thead><tr><th></th><th v-for="k in OPTIONS" :key="k" class="num"><span class="dot" :class="k" style="margin-right: 3px"></span>{{ CHOICE_LABELS[k][0] }}</th><th class="num">Race</th><th class="num">Rest</th><th class="num">Out</th></tr></thead>
            <tbody>
              <tr v-for="y in choices.byYear" :key="y.year">
                <td>{{ y.year }} <span class="muted small">{{ y.total }}</span></td>
                <td v-for="k in OPTIONS" :key="k" class="num tabular">{{ y.counts[k] || "·" }}</td>
                <td class="num tabular">{{ y.counts.race || "·" }}</td>
                <td class="num tabular">{{ y.counts.rest || "·" }}</td>
                <td class="num tabular">{{ y.counts.outing || "·" }}</td>
              </tr>
            </tbody>
          </table>
        </div>
      </div>
    </div>

    <div class="an-card">
      <h3>Training Efficiency</h3>
      <div class="an-body">
        <div class="tiles three">
          <div class="tile"><b class="tabular">{{ trainings.count }}</b><span>sessions read</span></div>
          <div class="tile"><b class="tabular up">{{ trainings.average === null ? "?" : "+" + trainings.average.toFixed(1) }}</b><span>points per session</span></div>
          <div class="tile"><b class="tabular">{{ trainings.failures }}</b><span>failed</span></div>
        </div>
        <table class="ledger" style="margin-top: 10px">
          <thead><tr><th>Stat</th><th class="num">Sessions</th><th class="num">Avg</th><th class="num">Total</th><th class="num">Best</th></tr></thead>
          <tbody>
            <tr v-for="r in efficiency" :key="r.field">
              <td><span class="dot" :class="r.field"></span>{{ STAT_NAMES[r.field] }}</td>
              <td class="num tabular">{{ r.sessions || "·" }}</td>
              <td class="num tabular">{{ r.average === null ? "·" : "+" + r.average.toFixed(1) }}</td>
              <td class="num tabular">{{ r.sessions ? "+" + r.total : "·" }}</td>
              <td class="num tabular"><button v-if="r.best" class="linkish" :title="where(r.best.entry)" @click="go(r.best.entry)">+{{ r.best.gain }}</button><span v-else>·</span></td>
            </tr>
          </tbody>
        </table>
        <p v-if="trainings.best" class="small muted" style="margin-top: 8px">
          Biggest session: <button class="linkish" @click="go(trainings.best.entry)">{{ where(trainings.best.entry) }}</button>, <b class="tabular" style="color: var(--orange)">+{{ trainings.best.total }}</b> across the stats.
        </p>
      </div>
    </div>

    <div class="an-card wide">
      <h3>Growth by Year</h3>
      <div class="an-body">
        <table class="ledger growth">
          <thead><tr><th>Stat</th><th class="num">Start</th><th v-for="h in growthHeads" :key="h" class="num">End of {{ h }}</th></tr></thead>
          <tbody>
            <tr v-for="g in growth" :key="g.field">
              <td><span class="dot" :class="g.field"></span>{{ STAT_NAMES[g.field] }}</td>
              <td class="num yearend"><span class="ye"><RankBadge :value="g.start" small /><b>{{ g.start ?? "?" }}</b></span></td>
              <td v-for="(ye, i) in g.cells" :key="i" class="num yearend">
                <span class="ye"><RankBadge :value="ye.value" small /><b>{{ ye.value ?? "?" }}</b></span>
                <small :class="{ down: (ye.gain ?? 0) < 0 }">{{ ye.gain === null ? "?" : plus(ye.gain) }}</small>
              </td>
            </tr>
          </tbody>
        </table>
        <p class="muted small" style="margin-top: 8px">Where each stat stood when the year ended, with the gain made in that year. The last column is the end of the run.</p>
      </div>
    </div>

    <div class="an-card wide">
      <h3>Rank Milestones</h3>
      <div class="an-body">
        <div class="ms-grid">
          <div class="ms-head"></div>
          <div v-for="[, rank] in MILESTONES" :key="rank" class="ms-head">{{ rank }}</div>
          <div class="ms-head">End</div>
          <template v-for="m in milestones" :key="m.field">
            <div class="ms-stat"><span class="dot" :class="m.field"></span>{{ STAT_NAMES[m.field] }}</div>
            <template v-for="c in m.cells" :key="c.rank">
              <button v-if="c.turn" class="ms-cell" :title="`${STAT_NAMES[m.field]} reached ${c.rank} (${c.threshold}) at ${c.turn.label}`" @click="emit('select', c.turn!.id)">
                <RankBadge :value="c.threshold" small />
                <span class="ms-when"><small>{{ whenParts(c.turn).year }}</small>{{ whenParts(c.turn).when }}</span>
              </button>
              <div v-else class="ms-cell empty" :title="`${STAT_NAMES[m.field]} did not reach ${c.rank}`">—</div>
            </template>
            <div class="ms-cell end"><RankBadge :value="m.end" small /><span class="ms-when"><small>{{ statRank(m.end)?.label ?? "" }}</small>{{ m.end ?? "?" }}</span></div>
          </template>
        </div>
        <p class="muted small" style="margin-top: 10px">The first turn whose opening value reached each rank. Click one to open the turn.</p>
      </div>
    </div>

    <div class="an-card">
      <h3>Races</h3>
      <div class="an-body">
        <div class="tiles">
          <div class="tile"><b class="tabular">{{ races.list.length }}</b><span>raced</span></div>
          <div class="tile"><b class="tabular">{{ races.wins }}</b><span>won{{ races.rate === null ? "" : ` (${races.rate}%)` }}</span></div>
          <div class="tile"><b class="tabular">{{ races.g1wins }}</b><span>G1 wins</span></div>
          <div class="tile"><b class="tabular">{{ races.fans.toLocaleString() }}{{ races.fansKnown ? "" : "+" }}</b><span>fans gained</span></div>
        </div>
        <ul class="race-list">
          <li v-for="r in races.list" :key="r.entry.id">
            <span v-if="r.grade" class="grade" :class="gradeClass(r.grade)">{{ r.grade }}</span>
            <button class="linkish clip" :title="`${r.name} · ${where(r.entry)}`" @click="go(r.entry)">{{ r.name }}</button>
            <span class="muted small clip">{{ where(r.entry) }}</span>
            <span class="place" :class="{ win: r.placing === 1 }">{{ place(r.placing) }}</span>
          </li>
        </ul>
      </div>
    </div>

    <div class="an-card">
      <h3>Skill Points and Skills</h3>
      <div class="an-body">
        <div class="tiles">
          <div class="tile"><b class="tabular up">+{{ skills.earned.toLocaleString() }}</b><span>earned</span></div>
          <div class="tile"><b class="tabular">{{ skills.spent.toLocaleString() }}</b><span>spent</span></div>
          <div class="tile"><b class="tabular">{{ skills.left === null ? "?" : skills.left.toLocaleString() }}</b><span>left at the end</span></div>
          <div class="tile"><b class="tabular">{{ skills.bought.length }}</b><span>skills bought</span></div>
        </div>
        <p v-if="!skills.bought.length" class="muted small" style="margin-top: 10px">No skill purchase was read.</p>
        <div v-else class="chips">
          <button v-for="(s, i) in skills.bought" :key="s.name + i" class="chip skill" :title="`bought at ${where(s.entry)}`" @click="go(s.entry)">{{ s.name }}</button>
        </div>
      </div>
    </div>

    <div class="an-card">
      <h3>Skill Hints</h3>
      <div class="an-body">
        <div class="tiles">
          <div class="tile"><b class="tabular">{{ skills.hints.length }}</b><span>skills hinted</span></div>
          <div class="tile"><b class="tabular up">+{{ skills.hintLevels }}</b><span>hint levels</span></div>
        </div>
        <p v-if="!skills.hints.length" class="muted small" style="margin-top: 10px">No hint was read.</p>
        <div v-else class="chips">
          <button v-for="h in skills.hints" :key="h.name" class="chip hint" :title="`first hint at ${where(h.entry)}`" @click="go(h.entry)">{{ h.name }} <span class="lvl">+{{ h.levels }}</span></button>
        </div>
      </div>
    </div>

    <div class="an-card">
      <h3>Rest and Energy</h3>
      <div class="an-body">
        <div class="tiles">
          <div class="tile"><b class="tabular">{{ energy.rests }}</b><span>rests</span></div>
          <div class="tile"><b class="tabular">{{ energy.outings }}</b><span>outings</span></div>
          <div class="tile"><b class="tabular">{{ energy.longest }}</b><span>turns in a row without a rest</span></div>
          <div v-if="energy.recovered !== null" class="tile"><b class="tabular up">+{{ energy.recovered }}</b><span>energy from rests</span></div>
          <div v-if="energy.infirmary" class="tile"><b class="tabular">{{ energy.infirmary }}</b><span>infirmary</span></div>
        </div>
        <p class="muted small" style="margin-top: 10px">A rest is a turn the ledger recorded as rest; the energy is what the rest's own message stated.</p>
      </div>
    </div>

    <div class="an-card">
      <h3>Events</h3>
      <div class="an-body">
        <div class="tiles three">
          <div class="tile"><b class="tabular">{{ events.count }}</b><span>outcomes</span></div>
          <div class="tile"><b class="tabular up">+{{ events.statTotal }}</b><span>stat points</span></div>
          <div class="tile"><b class="tabular up">+{{ events.skillTotal }}</b><span>skill points</span></div>
        </div>
        <ul v-if="events.top.length" class="event-list">
          <li v-for="t in events.top" :key="t.entry.id">
            <button class="linkish clip" :title="`${t.title} · ${where(t.entry)}`" @click="go(t.entry)">{{ t.title }}</button>
            <span class="muted small clip">{{ where(t.entry) }}</span>
            <span class="gain">+{{ t.total }}</span>
          </li>
        </ul>
      </div>
    </div>

    <div v-if="scenario.any" class="an-card">
      <h3>Grand Concert</h3>
      <div class="an-body">
        <div class="tiles">
          <div class="tile"><b class="tabular">{{ scenario.lessons }}</b><span>lessons</span></div>
          <div class="tile"><b class="tabular">{{ scenario.performanceSpent.toLocaleString() }}</b><span>performance points spent</span></div>
          <div class="tile"><b class="tabular">{{ scenario.songs.length }}</b><span>songs</span></div>
          <div class="tile"><b class="tabular">{{ scenario.concerts.length }}</b><span>concerts</span></div>
        </div>
        <ul v-if="scenario.concerts.length" class="event-list">
          <li v-for="c in scenario.concerts" :key="c.entry.id">
            <button class="linkish clip" @click="go(c.entry)">{{ c.title }}</button>
            <span class="muted small clip">{{ where(c.entry) }}</span>
            <span class="gain" style="color: var(--lilac-ink)">{{ c.result || "" }}</span>
          </li>
        </ul>
        <div v-if="scenario.songs.length" class="chips">
          <button v-for="s in scenario.songs" :key="s.entry.id" class="chip song" :title="`acquired at ${where(s.entry)}`" @click="go(s.entry)">{{ s.name }}</button>
        </div>
      </div>
    </div>
  </div>
</template>
