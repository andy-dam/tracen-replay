// Everything the report says with less than full confidence, in one place:
// per turn (accounting and observation gaps) and per entry (readings the
// analyzer flagged, amounts it derived rather than observed). The wording
// is the ledger's own status, made readable; nothing is judged here.
import type { Correction, Entry, TurnSummary, Verification } from "./api";
import { BASIS_NOTE, signed, statusText } from "./format";

export interface SavedReview {
  correction: Correction;
  verification: Verification;
}

/**
 * Whether a saved review settles a turn, so it leaves the list of turns to
 * check. Either the viewer marked it resolved, or the review closes what the
 * report asked about: every number the review touches adds up, none is left
 * open, and every entry the report doubted was reviewed, corrected or removed.
 */
export function reviewSettles(saved: SavedReview | undefined, doubtedEntryIds: string[]): boolean {
  if (!saved) return false;
  const { correction, verification } = saved;
  if (correction.resolved) return true;
  if (!verification.balanced || verification.fields.some((f) => f.status === "off" || f.status === "open")) return false;
  return doubtedEntryIds.every((id) => {
    const edit = correction.entries?.[id];
    return !!edit && (edit.reviewed === true || edit.deleted === true || Object.keys(edit.changes ?? {}).length > 0);
  });
}

/** The turns of a report that saved reviews settle. */
export function settledTurns(saved: SavedReview[], entries: Entry[]): Set<string> {
  const doubted = new Map<string, string[]>();
  for (const e of entries) {
    if (!e.turn_id || !entryWarnings(e).some((w) => w.serious)) continue;
    doubted.set(e.turn_id, [...(doubted.get(e.turn_id) ?? []), e.id]);
  }
  const out = new Set<string>();
  for (const s of saved) if (reviewSettles(s, doubted.get(s.correction.turn_id) ?? [])) out.add(s.correction.turn_id);
  return out;
}

export interface Warning {
  text: string;
  /** Warnings that mark a genuine gap or doubt rather than a note. */
  serious: boolean;
  /** What the flag means and what a viewer can do about it, in plain words. */
  advice?: string;
}

// The advice behind each flag: what it means, then what to do. The reviewer
// is looking at the recording beside these words, so every step names what
// to look for on screen.
const ADVICE = {
  conflict: "Two frames showed different numbers for this entry, so the report kept both instead of picking one. Seek to it, read the number the game shows, and type it into the amount; then mark it Reviewed.",
  notAccepted: "The report saw this award but did not count it, usually because it could not prove it was applied. If the log or the stat bar shows it took effect, add the stat with its amount; if it never applied, mark it Reviewed and move on.",
  referenceOnly: "This line repeats an award that another entry already counts, so it adds nothing to the totals. Nothing to do unless the game shows it as a separate award; then add the stat with its amount.",
  rewardLink: "This race's reward was read from a receipt that could not be tied to the result screen. Seek to the result and the receipt after it and check they describe the same race; if the amounts are right, mark it Reviewed, otherwise correct them.",
  assignedBy: "This entry was placed in this turn by its time, not by a calendar frame around it. If it clearly belongs to the previous or next turn, mark it Not real here and add it there as a missed event.",
  ambiguous: "The receipt named an effect but the report could not decide what it changed. Seek to it and read the line in the game's log; add the stat it changed with the amount, then mark it Reviewed.",
  unparsed: "A receipt appeared that the analyzer could not read at all. Seek to it and read the line in the game's log. If it changed a stat or a performance point, add that stat with its amount; if it was text only, mark it Reviewed.",
  lessonUnresolved: "The lesson was bought but its price was never observed, so the performance points it cost are unknown. Seek to the purchase, read the price on the card or the balance before and after, and enter the cost as negative performance amounts.",
  lessonDerived: "The lesson's price was worked out from surrounding evidence rather than read from the balance. If the card in the recording shows a different price, correct the amounts.",
  balanceAfter: "The balance after this purchase was never on screen, so the price could not be confirmed against it. If a later menu visit shows the balance, compare it and correct the amounts if needed.",
  songName: "The song's name was read two different ways. Nothing changes in the totals; add a note with the right title if you want it recorded.",
  skillList: "The list of purchased skills was cut off and the points charged were not read, so some skills or their point costs may be missing. Seek to the skill screen and add any purchase the report lacks as a missed event.",
  skillNames: "The points charged were read, so the totals are right; the list of purchased skills scrolled and only some names were seen. Nothing to do unless you want the missing names on record.",
  failed: "The training failed, so its displayed gains were not applied. Nothing to do unless the stat bar shows they were.",
  settledReads: "The badge was read as more than one number while the card animated, and the stat bars before and after the turn settle it at the amount shown, which the card showed too. Nothing to do; seek to the card if you want to see it.",
  derived: "This amount was not read from a badge; the report worked it out from other observations. Seek to the result screen: if the badge shows a different number, type that number in; if it matches, mark it Reviewed.",
} as const;

export function turnWarnings(t: TurnSummary): Warning[] {
  const out: Warning[] = [];
  for (const [status, n] of Object.entries(t.accounting_status_counts)) {
    if (status === "balanced_observations" || status === "balanced_with_derived_changes" || status === "balanced_across_unread_stretch") continue;
    out.push({ text: `${n} field${n === 1 ? "" : "s"}: ${statusText(status)}`, serious: status === "unexplained_change" || status === "unresolved_attribution" || status === "unexplained_across_unread_stretch" });
  }
  if (!t.opening_observed) out.push({ text: "opening state not observed", serious: false });
  if (t.action_status === "missing_action") {
    if (!t.expects_one_action) out.push({ text: "no decision expected in this window", serious: false });
    else out.push({ text: "no action seen in this window", serious: true });
  }
  if (t.action_status === "multiple_actions") out.push({ text: `${t.action_count} actions in one window`, serious: true });
  const basis = t.action_basis ? BASIS_NOTE[t.action_basis] : undefined;
  if (basis && !t.action_filled_in) out.push({ text: `action taken ${basis}; the choice itself was not seen`, serious: false });
  if (t.window_kind !== "calendar_turn" && t.window_kind !== "phase_race_turn" && t.window_kind !== "countdown_segment") {
    out.push({ text: `window is ${t.window_kind.replaceAll("_", " ")}, not a calendar boundary`, serious: false });
  }
  return out;
}

type Detail = Record<string, unknown>;

export function entryWarnings(e: Entry): Warning[] {
  const out: Warning[] = [];
  const d = (e.detail ?? {}) as Detail;
  if (e.conflicts_present) out.push({ text: "conflicting readings", serious: true, advice: ADVICE.conflict });
  if (e.accepted_award === false && e.kind !== "unparsed_receipt" && e.kind !== "ambiguous_effect") out.push({ text: "not an accepted award", serious: false, advice: ADVICE.notAccepted });
  if (e.accounting_role === "reference_only_not_an_additional_award") out.push({ text: "reference only, not an additional award", serious: false, advice: ADVICE.referenceOnly });
  if (e.reward_link_status && e.reward_link_status !== "linked_event" && e.reward_link_status !== "linked_race") out.push({ text: `reward link: ${e.reward_link_status.replaceAll("_", " ")}`, serious: true, advice: ADVICE.rewardLink });
  if (e.assignment_basis && e.assignment_basis !== "observed_within_calendar_window") out.push({ text: `assigned by ${e.assignment_basis.replaceAll("_", " ")}`, serious: false, advice: ADVICE.assignedBy });
  if (e.kind === "ambiguous_effect") out.push({ text: `ambiguous effect${typeof d.reason === "string" ? `: ${d.reason.replaceAll("_", " ")}` : ""}`, serious: true, advice: ADVICE.ambiguous });
  if (e.kind === "unparsed_receipt" && d.worked_out) out.push({ text: "receipt number worked out from the turn difference", serious: false, advice: ADVICE.derived });
  else if (e.kind === "unparsed_receipt") out.push({ text: "receipt could not be parsed", serious: true, advice: ADVICE.unparsed });
  if (e.kind === "lesson_purchases") {
    if (d.cost_basis === "unresolved" && d.turn_difference_cost) out.push({ text: "lesson cost worked out from the turn difference", serious: false, advice: ADVICE.lessonDerived });
    else if (d.cost_basis === "unresolved") out.push({ text: "lesson cost unresolved", serious: true, advice: ADVICE.lessonUnresolved });
    else if (typeof d.cost_basis === "string" && !d.cost_basis.startsWith("observed")) out.push({ text: `lesson cost ${d.cost_basis.replaceAll("_", " ")}`, serious: false, advice: ADVICE.lessonDerived });
    if (d.after_balance_observed === false) out.push({ text: "balance after the purchase not observed", serious: false, advice: ADVICE.balanceAfter });
  }
  if (e.kind === "song_acquisitions" && d.name_conflicted) out.push({ text: "song name conflicted", serious: true, advice: ADVICE.songName });
  if ((e.kind === "skill_purchases" || e.kind === "skill_purchase_batch") && d.purchased_list_complete === false) {
    if (d.spent_skill_points === null || d.spent_skill_points === undefined) out.push({ text: "purchased skill list incomplete", serious: true, advice: ADVICE.skillList });
    else out.push({ text: "purchased skills partly named", serious: false, advice: ADVICE.skillNames });
  }
  if (e.kind === "training" && d.training_outcome === "failure") out.push({ text: "training failed", serious: false, advice: ADVICE.failed });
  const derived: string[] = [];
  const settled: string[] = [];
  for (const fields of Object.values(e.changes ?? {})) {
    for (const [field, c] of Object.entries(fields)) {
      if (c.disagreeing_reads?.length) settled.push(`${field.replace("_", " ")} also read as ${c.disagreeing_reads.map(signed).join(", ")}, settled at ${signed(c.amount)}`);
      if (c.basis === "turn_difference") derived.push(`${field.replace("_", " ")} (worked out from the difference between turns)`);
      else if (!c.basis || !c.basis.startsWith("observed_")) derived.push(`${field.replace("_", " ")} (${(c.basis ?? "basis unknown").replaceAll("_", " ")})`);
    }
  }
  if (derived.length) out.push({ text: `amount not read directly: ${derived.join(", ")}`, serious: false, advice: ADVICE.derived });
  if (settled.length) out.push({ text: `badge settled by the stat bars: ${settled.join("; ")}`, serious: false, advice: ADVICE.settledReads });
  return out;
}

/** Every flag the report can raise on a line, with its advice, for the guide. */
export const FLAG_GUIDE: { text: string; serious: boolean; advice: string }[] = [
  { text: "conflicting readings", serious: true, advice: ADVICE.conflict },
  { text: "receipt could not be parsed", serious: true, advice: ADVICE.unparsed },
  { text: "ambiguous effect", serious: true, advice: ADVICE.ambiguous },
  { text: "reward link", serious: true, advice: ADVICE.rewardLink },
  { text: "lesson cost unresolved", serious: true, advice: ADVICE.lessonUnresolved },
  { text: "song name conflicted", serious: true, advice: ADVICE.songName },
  { text: "purchased skill list incomplete", serious: true, advice: ADVICE.skillList },
  { text: "purchased skills partly named", serious: false, advice: ADVICE.skillNames },
  { text: "amount not read directly", serious: false, advice: ADVICE.derived },
  { text: "badge settled by the stat bars", serious: false, advice: ADVICE.settledReads },
  { text: "not an accepted award", serious: false, advice: ADVICE.notAccepted },
  { text: "reference only, not an additional award", serious: false, advice: ADVICE.referenceOnly },
  { text: "assigned by time", serious: false, advice: ADVICE.assignedBy },
  { text: "lesson cost worked out", serious: false, advice: ADVICE.lessonDerived },
  { text: "balance after the purchase not observed", serious: false, advice: ADVICE.balanceAfter },
  { text: "training failed", serious: false, advice: ADVICE.failed },
];

/** How to explain a stat gap, in the order a viewer would try. */
export function gapAdvice(field: string, amount: number, workedOut: boolean, window: string): string {
  const what = `${field.replace("_", " ")} ${amount > 0 ? "went up" : "went down"} by ${Math.abs(amount)}`;
  const where = window ? ` between ${window}` : "";
  if (workedOut) return `${what}${where}, and the report has already put it on the one event that could have caused it. Seek there and check the number on screen: press Looks right if it matches, or pick another way to explain it if it does not.`;
  return `${what}${where}, and no event in the report accounts for it. Seek there and watch for what changed it. If it was one of the events listed below, choose Belongs to an event. If the game showed something the report has no entry for, choose Missed event. If you can read the number but cannot tell which event, choose Enter amount.`;
}

/** The turn's decision as the report read it: the first committed action that is not the scheduled race. */
export function committedAction(entries: Entry[]): { text: string; ms: number | null; more: number; option: string; kind: string } | null {
  const actions = entries.filter((e) => e.kind === "committed_action");
  const chosen = actions.find((e) => e.action_kind !== "race") ?? actions[0];
  if (!chosen) return null;
  const d = (chosen.detail ?? {}) as Detail;
  const name = [d.training_name, d.name, d.race_name, d.title].find((v) => typeof v === "string" && v) as string | undefined;
  const option = chosen.training_option ?? (typeof d.training_option === "string" ? d.training_option : "");
  let text = chosen.action_kind === "training" ? `${option ? option[0].toUpperCase() + option.slice(1) : "Unknown"} Training` : (chosen.action_kind ?? "action").replaceAll("_", " ");
  if (name && chosen.action_kind !== "training") text = `${text[0].toUpperCase() + text.slice(1)}: ${name}`;
  return { text, ms: chosen.first_seen_ms, more: actions.length - 1, option: chosen.action_kind === "training" ? option : "", kind: chosen.action_kind ?? "" };
}

export function entryName(e: Entry): string {
  const d = (e.detail ?? {}) as Detail;
  const name = [d.training_name, d.name, d.race_name, d.title].find((v) => typeof v === "string" && v) as string | undefined;
  return name || e.context_title || e.kind.replaceAll("_", " ");
}
