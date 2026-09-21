// Display helpers. Nothing here changes a value; unknown stays unknown.
import type { TurnSummary } from "./api";

export function clock(ms: number | null | undefined): string {
  if (ms === null || ms === undefined) return "?";
  const total = Math.floor(ms / 1000);
  const h = Math.floor(total / 3600);
  const m = Math.floor((total % 3600) / 60);
  const s = total % 60;
  return (h > 0 ? `${h}:` : "") + `${String(m).padStart(2, "0")}:${String(s).padStart(2, "0")}`;
}

export function clockMs(ms: number | null | undefined): string {
  if (ms === null || ms === undefined) return "?";
  return clock(ms) + "." + String(ms % 1000).padStart(3, "0");
}

export function signed(n: number | null | undefined): string {
  if (n === null || n === undefined) return "?";
  return n > 0 ? `+${n}` : String(n);
}

export function bytes(n: number): string {
  if (n >= 1e9) return `${(n / 1e9).toFixed(2)} GB`;
  if (n >= 1e6) return `${(n / 1e6).toFixed(1)} MB`;
  if (n >= 1e3) return `${(n / 1e3).toFixed(0)} KB`;
  return `${n} B`;
}

export function when(iso: string | undefined): string {
  if (!iso) return "";
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? iso : d.toLocaleString(undefined, { dateStyle: "medium", timeStyle: "short" });
}

export function elapsed(start?: string, end?: string): string {
  if (!start) return "";
  const a = new Date(start).getTime();
  const b = end ? new Date(end).getTime() : Date.now();
  if (Number.isNaN(a) || Number.isNaN(b)) return "";
  const total = Math.max(0, Math.floor((b - a) / 1000));
  const m = Math.floor(total / 60);
  const s = total % 60;
  return `${m}:${String(s).padStart(2, "0")}`;
}

/**
 * How long an analysis has run, pauses left out: the time before its latest
 * start plus, once it has started again, the time since.
 */
export function runTime(job: { status: string; started_at?: string; finished_at?: string; ran_seconds?: number }): string {
  let total = job.ran_seconds ?? 0;
  if (job.status !== "paused" && job.status !== "queued" && job.started_at) {
    const a = new Date(job.started_at).getTime();
    const b = job.finished_at ? new Date(job.finished_at).getTime() : Date.now();
    if (!Number.isNaN(a) && !Number.isNaN(b)) total += Math.max(0, Math.floor((b - a) / 1000));
  }
  if (!total && !job.started_at) return "";
  return `${Math.floor(total / 60)}:${String(total % 60).padStart(2, "0")}`;
}

/**
 * The skill points a career earned. The balance left at the end plus what was
 * spent is the figure a player means, and it holds even where a gain went
 * unread. Without an end balance it is the gains the entries add up to.
 */
export function skillPointsEarned(totals: { earned: number; spent: number } | null | undefined, left: number | null | undefined): number | null {
  if (!totals) return null;
  return typeof left === "number" ? left + totals.spent : totals.earned;
}

/** The pill class for a job status. */
export function statusClass(status: string): string {
  switch (status) {
    case "succeeded":
      return "ok";
    case "completed_with_stage_failures":
    case "queued":
    case "running":
      return "warn";
    case "failed":
    case "interrupted":
      return "bad";
    case "cancelled":
      return "";
  }
  return "";
}

/** Directly observed amounts keep the plain style; everything else is marked. */
export function basisClass(basis: string | undefined): string {
  if (!basis) return "derived";
  return basis.startsWith("observed_") ? "" : "derived";
}

export const STAT_FIELDS = ["speed", "stamina", "power", "guts", "wit", "skill_points"];
export const CORE_STATS = ["speed", "stamina", "power", "guts", "wit"];
export const PERFORMANCE_FIELDS = ["dance", "passion", "vocal", "visual", "composure"];
export const STAT_NAMES: Record<string, string> = { speed: "Speed", stamina: "Stamina", power: "Power", guts: "Guts", wit: "Wit", skill_points: "Skill Pts" };

// Stat rank letters as the game shows them beside each stat. The bands were
// read off the game's own badges in the local recordings (F at 137, F+ at 150,
// E at 239, E+ at 292, D at 325, D+ at 351, C at 403, C+ at 500, B at 621, B+ at
// 744, A at 851, A+ at 940, S at 1008, S+ at 1073, SS at 1130, SS+ at 1159, UG1
// at 1215, UF9 at 1390, UE6 at 1461). Below 400 the "+" half starts at 50; C, B
// and A are 100 wide with the "+" at the next hundred; S and SS split at 50
// again; from 1200 the badge is U plus a letter per hundred (G, F, E, D, C, B,
// A, S) and a digit per ten. G, G+ and the letters above UE follow the same
// pattern but were not observed.
export interface StatRank {
  label: string;
  base: string;
  plus: boolean;
  digit: number | null;
  tier: string;
}

const BANDS: [number, number, string][] = [
  [1, 49, "G"], [50, 99, "G+"], [100, 149, "F"], [150, 199, "F+"], [200, 249, "E"], [250, 299, "E+"],
  [300, 349, "D"], [350, 399, "D+"], [400, 499, "C"], [500, 599, "C+"], [600, 699, "B"], [700, 799, "B+"],
  [800, 899, "A"], [900, 999, "A+"], [1000, 1049, "S"], [1050, 1099, "S+"], [1100, 1149, "SS"], [1150, 1199, "SS+"],
];
const U_LETTERS = ["G", "F", "E", "D", "C", "B", "A", "S"];

export function statRank(value: number | null | undefined): StatRank | null {
  if (value === null || value === undefined || !Number.isFinite(value) || value < 1) return null;
  if (value >= 1200) {
    const index = Math.min(U_LETTERS.length - 1, Math.floor((value - 1200) / 100));
    const digit = Math.floor((value % 100) / 10);
    const base = "U" + U_LETTERS[index];
    return { label: digit ? `${base}${digit}` : base, base, plus: false, digit: digit || null, tier: "u" };
  }
  for (const [lo, hi, label] of BANDS) {
    if (value >= lo && value <= hi) {
      const plus = label.endsWith("+");
      const base = plus ? label.slice(0, -1) : label;
      return { label, base, plus, digit: null, tier: base.toLowerCase() };
    }
  }
  return null;
}

const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
export const MONTH_LABELS = MONTHS;

/** The cells of each strip row. A junior year is 23 turns: 11 before the debut, then Early July to Late December. */
export const STRIP_ROWS = [23, 24, 24, 3];

/** Where a turn sits on the calendar: year row (0 junior, 1 classic, 2 senior) and its column, or the finale row 3 (0-2). */
export function stripPosition(turn: TurnSummary): { row: number; col: number } | null {
  const label = turn.label;
  const dated = /^(Junior|Classic|Senior) Year (Early|Late) (Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)$/.exec(label);
  if (dated) {
    const row = { Junior: 0, Classic: 1, Senior: 2 }[dated[1]] ?? 0;
    const half = MONTHS.indexOf(dated[3]) * 2 + (dated[2] === "Late" ? 1 : 0);
    // The junior year has no Early January turn: its first dated turn, Early July, is the twelfth cell.
    return { row, col: row === 0 ? Math.max(0, half - 1) : half };
  }
  if (label.startsWith("Junior Year Pre-Debut")) {
    // The run opens on "11 turns to goal", the first cell, and counts down to 1.
    const value = typeof turn.calendar_value === "number" ? turn.calendar_value : null;
    return { row: 0, col: value === null ? 0 : Math.max(0, Math.min(10, 11 - value)) };
  }
  if (label.startsWith("Finale Underway")) {
    // "Finale" itself contains "Final", so the last race is matched as a whole word.
    const race = turn.scheduled_race ?? label;
    if (race.includes("Qualifier")) return { row: 3, col: 0 };
    if (race.includes("Semifinal")) return { row: 3, col: 1 };
    if (/\bFinals?\b/.test(race)) return { row: 3, col: 2 };
    return { row: 3, col: 0 };
  }
  return null;
}

const SMALL_WORDS = new Set(["a", "an", "and", "as", "at", "by", "for", "from", "in", "of", "on", "or", "per", "the", "to", "vs", "with"]);

/**
 * Title Case, the way every label in the application is written: every word
 * capitalised except short joining words inside the phrase. An underscore in
 * a status or a stage name reads as a space.
 */
export function titleCase(text: string): string {
  return text
    .replaceAll("_", " ")
    .split(" ")
    .map((word, i) => (i > 0 && SMALL_WORDS.has(word.toLowerCase()) ? word.toLowerCase() : word.charAt(0).toUpperCase() + word.slice(1)))
    .join(" ");
}

/** The year the ledger's own label names; nothing is re-dated. */
export function yearOf(label: string, phase: string): string {
  const year = /^(Junior|Classic|Senior) Year (Early|Late) /.exec(label);
  if (year) return `${year[1]} Year`;
  if (label.startsWith("Junior Year Pre-Debut")) return "Pre-Debut";
  if (label.startsWith("Finale Underway")) return "Finale";
  return titleCase(phase.replaceAll("_", " "));
}

/** The race grade badge class: G1 gold, G2 silver, G3 bronze, the rest by kind. */
export function gradeClass(grade: string): string {
  const g = grade.toUpperCase();
  if (g === "G1") return "g1";
  if (g === "G2") return "g2";
  if (g === "G3") return "g3";
  if (g === "DEBUT" || g === "MAIDEN" || g === "EX") return "special";
  return "open";
}

/** The short form of a turn label once the year is shown elsewhere. */
export function shortLabel(label: string): string {
  return label.replace(/^(Junior|Classic|Senior) Year /, "").replace("Pre-Debut · ", "").replace("Finale Underway · ", "");
}

/** Year and short label together, without saying the same word twice. */
export function fullLabel(label: string, phase: string): string {
  const year = yearOf(label, phase);
  const short = shortLabel(label);
  return short.toLowerCase() === year.toLowerCase() ? short : `${year} · ${short}`;
}

/** True when the short label would only repeat the year tag. */
export function repeatsYear(label: string, phase: string): boolean {
  return shortLabel(label).toLowerCase() === yearOf(label, phase).toLowerCase();
}

/** The strip colour class of a turn: the training option, the action kind, or missing. */
export function actionClass(turn: TurnSummary): string {
  if (turn.action_status === "missing_action") return "missing";
  const classes: string[] = [];
  if (turn.action_kind === "training" && turn.training_option) classes.push(turn.training_option);
  else if (turn.action_kind) classes.push(turn.action_kind);
  if (turn.action_status === "multiple_actions") classes.push("multi");
  return classes.join(" ");
}

/** What stood in for a commit the report never saw, by `action_basis`. */
export const BASIS_NOTE: Record<string, string> = {
  result_card_only: "from its result card",
  outing_menu_and_receipt: "from the Recreation menu and its receipt",
  hub_exit_and_receipt: "from the hub and its receipt",
};

export function describeAction(turn: TurnSummary): string {
  if (turn.action_status === "missing_action") return "not seen by the report";
  const base = turn.action_kind === "training" && turn.training_option ? `${turn.training_option} training` : turn.action_kind === "training" ? "training (option not read)" : turn.action_kind ?? "action";
  const text = turn.action_status === "multiple_actions" ? `${base} and more` : base;
  if (turn.action_filled_in) return `${text} (filled in by you)`;
  const note = turn.action_basis ? BASIS_NOTE[turn.action_basis] : undefined;
  return note ? `${text} (${note})` : text;
}

/** Count of fields whose accounting across the turn is not balanced. */
export function unbalanced(turn: TurnSummary): number {
  return Object.entries(turn.accounting_status_counts)
    .filter(([k]) => k !== "balanced_observations")
    .reduce((sum, [, v]) => sum + v, 0);
}

export const STATUS_TEXT: Record<string, string> = {
  balanced_observations: "balanced",
  balanced_with_derived_changes: "balanced with derived changes",
  unexplained_change: "unexplained change",
  unresolved_attribution: "not fully attributed",
  unobserved_boundary: "boundary not observed",
  missing_endpoint: "end not observed",
  not_yet_shown: "not on screen yet",
  career_end: "career over, no closing screen read for it",
  balanced_across_unread_stretch: "balances across turns with no reading",
  unexplained_across_unread_stretch: "unexplained across turns with no reading",
};

export function statusText(status: string): string {
  return STATUS_TEXT[status] ?? status.replaceAll("_", " ");
}
