// Everything the report says with less than full confidence, in one place:
// per turn (accounting and observation gaps) and per entry (readings the
// analyzer flagged, amounts it derived rather than observed). The wording
// is the ledger's own status, made readable; nothing is judged here.
import type { Entry, TurnSummary } from "./api";
import { statusText } from "./format";

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
  skillList: "The list of purchased skills was cut off, so some skills or their point costs may be missing. Seek to the skill screen and add any purchase the report lacks as a missed event.",
  failed: "The training failed, so its displayed gains were not applied. Nothing to do unless the stat bar shows they were.",
  derived: "This amount was not read from a badge; the report worked it out from other observations. Seek to the result screen: if the badge shows a different number, type that number in; if it matches, mark it Reviewed.",
} as const;

export function turnWarnings(t: TurnSummary): Warning[] {
  const out: Warning[] = [];
  for (const [status, n] of Object.entries(t.accounting_status_counts)) {
    if (status === "balanced_observations" || status === "balanced_with_derived_changes") continue;
    out.push({ text: `${n} field${n === 1 ? "" : "s"}: ${statusText(status)}`, serious: status === "unexplained_change" || status === "unresolved_attribution" });
  }
  if (!t.opening_observed) out.push({ text: "opening state not observed", serious: false });
  if (t.action_status === "missing_action") {
    if (!t.expects_one_action) out.push({ text: "no decision expected in this window", serious: false });
    else out.push({ text: "no action seen in this window", serious: true });
  }
  if (t.action_status === "multiple_actions") out.push({ text: `${t.action_count} actions in one window`, serious: true });
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
  if (e.reward_link_status && e.reward_link_status !== "linked_event") out.push({ text: `reward link: ${e.reward_link_status.replaceAll("_", " ")}`, serious: true, advice: ADVICE.rewardLink });
  if (e.assignment_basis && e.assignment_basis !== "observed_within_calendar_window") out.push({ text: `assigned by ${e.assignment_basis.replaceAll("_", " ")}`, serious: false, advice: ADVICE.assignedBy });
  if (e.kind === "ambiguous_effect") out.push({ text: `ambiguous effect${typeof d.reason === "string" ? `: ${d.reason.replaceAll("_", " ")}` : ""}`, serious: true, advice: ADVICE.ambiguous });
  if (e.kind === "unparsed_receipt") out.push({ text: "receipt could not be parsed", serious: true, advice: ADVICE.unparsed });
  if (e.kind === "lesson_purchases") {
    if (d.cost_basis === "unresolved") out.push({ text: "lesson cost unresolved", serious: true, advice: ADVICE.lessonUnresolved });
    else if (typeof d.cost_basis === "string" && !d.cost_basis.startsWith("observed")) out.push({ text: `lesson cost ${d.cost_basis.replaceAll("_", " ")}`, serious: false, advice: ADVICE.lessonDerived });
    if (d.after_balance_observed === false) out.push({ text: "balance after the purchase not observed", serious: false, advice: ADVICE.balanceAfter });
  }
  if (e.kind === "song_acquisitions" && d.name_conflicted) out.push({ text: "song name conflicted", serious: true, advice: ADVICE.songName });
  if ((e.kind === "skill_purchases" || e.kind === "skill_purchase_batch") && d.purchased_list_complete === false) out.push({ text: "purchased skill list incomplete", serious: true, advice: ADVICE.skillList });
  if (e.kind === "training" && d.training_outcome === "failure") out.push({ text: "training failed", serious: false, advice: ADVICE.failed });
  const derived: string[] = [];
  for (const fields of Object.values(e.changes ?? {})) {
    for (const [field, c] of Object.entries(fields)) {
      if (c.basis === "turn_difference") derived.push(`${field.replace("_", " ")} (worked out from the difference between turns)`);
      else if (!c.basis || !c.basis.startsWith("observed_")) derived.push(`${field.replace("_", " ")} (${(c.basis ?? "basis unknown").replaceAll("_", " ")})`);
    }
  }
  if (derived.length) out.push({ text: `amount not read directly: ${derived.join(", ")}`, serious: false, advice: ADVICE.derived });
  return out;
}

/** How to explain a stat gap, in the order a viewer would try. */
export function gapAdvice(field: string, amount: number, workedOut: boolean, window: string): string {
  const what = `${field.replace("_", " ")} ${amount > 0 ? "went up" : "went down"} by ${Math.abs(amount)}`;
  const where = window ? ` between ${window}` : "";
  if (workedOut) return `${what}${where}, and the report has already put it on the one event that could have caused it. Seek there and check the number on screen: press Looks right if it matches, or pick another way to explain it if it does not.`;
  return `${what}${where}, and no event in the report accounts for it. Seek there and watch for what changed it. If it was one of the events listed below, choose Belongs to an event. If the game showed something the report has no entry for, choose Missed event. If you can read the number but cannot tell which event, choose Enter amount.`;
}

export function entryName(e: Entry): string {
  const d = (e.detail ?? {}) as Detail;
  const name = [d.training_name, d.name, d.race_name, d.title].find((v) => typeof v === "string" && v) as string | undefined;
  return name || e.context_title || e.kind.replaceAll("_", " ");
}
