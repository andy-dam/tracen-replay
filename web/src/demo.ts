// A made-up career used by the home page to show what a report looks like.
// It is not a real run: the numbers are chosen to look like one so the
// timeline, the stat bar and the chart can be shown without a recording.
import type { Entry, TurnSummary } from "./api";

const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
const PLAN = "wswpwsswwsrwsswwosswswrwswwsswrwswwsrwwssowwswrwswwsswwrwsswwswwrwswwsswrwsw";
const OPTION: Record<string, string> = { s: "speed", w: "wit", p: "power", t: "stamina", g: "guts" };

function turn(id: number, label: string, phase: string, ms: number, action: string, stats: Record<string, number>): TurnSummary {
  const kind = action === "r" ? "race" : action === "o" ? "outing" : action === "z" ? "rest" : "training";
  return {
    id: `demo-${id}`,
    label,
    phase,
    calendar_value: id,
    start_ms: ms,
    end_ms: ms + 20000,
    window_kind: "calendar_turn",
    action_status: "one_action",
    action_count: 1,
    expects_one_action: true,
    entry_count: 6,
    opening_observed: true,
    accounting_status_counts: { balanced_observations: 11 },
    action_kind: kind,
    training_option: kind === "training" ? OPTION[action] ?? "speed" : undefined,
    opening: { stats: { ...stats }, performance: null },
  };
}

export function demoTurns(): TurnSummary[] {
  const out: TurnSummary[] = [];
  const stats = { speed: 180, stamina: 170, power: 160, guts: 120, wit: 150, skill_points: 120 };
  let id = 0;
  let ms = 30000;
  const years = ["Junior", "Classic", "Senior"];
  for (let y = 0; y < 3; y++) {
    for (let m = 0; m < 12; m++) {
      for (const half of ["Early", "Late"]) {
        if (y === 0 && m < 6) {
          out.push(turn(id, `Junior Year Pre-Debut · ${12 - (m * 2 + (half === "Late" ? 1 : 0))} turns to goal`, "junior_year_pre_debut", ms, PLAN[id % PLAN.length], stats));
        } else {
          out.push(turn(id, `${years[y]} Year ${half} ${MONTHS[m]}`, "dated", ms, PLAN[id % PLAN.length], stats));
        }
        const action = PLAN[id % PLAN.length];
        const option = OPTION[action];
        const growth = 1 + y * 0.35;
        if (option) {
          (stats as Record<string, number>)[option] += Math.round((14 + (id % 5) * 3) * growth);
          for (const other of ["speed", "stamina", "power", "guts", "wit"]) if (other !== option && (id + other.length) % 3 === 0) (stats as Record<string, number>)[other] += Math.round(4 * growth);
          stats.skill_points += 12;
        } else if (action === "r") {
          stats.skill_points += 40;
          stats.guts += 6;
        }
        id++;
        ms += 24000;
      }
    }
  }
  for (const race of ["URA Finale Qualifier", "URA Finale Semifinal", "URA Finale Finals"]) {
    const t = turn(id, `Finale Underway · ${race}`, "finale_underway", ms, "s", stats);
    t.window_kind = "phase_race_turn";
    t.scheduled_race = race;
    out.push(t);
    stats.speed += 20;
    stats.wit += 15;
    id++;
    ms += 30000;
  }
  return out;
}

export function demoFinal(turns: TurnSummary[]): Record<string, number | null> {
  const last = turns[turns.length - 1].opening.stats ?? {};
  return { speed: (last.speed ?? 0) + 24, stamina: last.stamina ?? 0, power: last.power ?? 0, guts: last.guts ?? 0, wit: (last.wit ?? 0) + 15, skill_points: 640 };
}

export function demoEntries(): Entry[] {
  return [
    { id: "d1", kind: "outcome", turn_id: "demo-40", first_seen_ms: 992000, last_seen_ms: 994000, context_title: "New Year's Resolutions", changes: { stats: { wit: { amount: 20, basis: "observed_receipt" }, skill_points: { amount: 20, basis: "observed_receipt" } } } },
    { id: "d2", kind: "training", turn_id: "demo-40", first_seen_ms: 1001000, last_seen_ms: 1003000, training_option: "speed", changes: { stats: { speed: { amount: 31, basis: "observed_training_gain" }, power: { amount: 12, basis: "observed_training_gain" }, skill_points: { amount: 9, basis: "observed_training_gain" }, dance: { amount: 18, basis: "observed_training_gain" } } }, detail: { training_name: "Sprint Drills" } },
    { id: "d3", kind: "skill_hint_change", turn_id: "demo-40", first_seen_ms: 1008000, last_seen_ms: 1008000, effect: { kind: "skill_hint_change", name: "Straightaway Adept", amount: 2, raw_text: "Gained 2 hint level(s) for Straightaway Adept." } },
    { id: "d4", kind: "races", turn_id: "demo-41", first_seen_ms: 1040000, last_seen_ms: 1046000, detail: { race_name: "Satsuki Sho", placing: 1, fans_gained: 12400, race_grade: "G1", course: { venue: "Nakayama", distance_m: 2000, surface: "turf" } } },
  ];
}
