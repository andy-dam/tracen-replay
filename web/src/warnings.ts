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
}

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
  if (e.conflicts_present) out.push({ text: "conflicting readings", serious: true });
  if (e.accepted_award === false && e.kind !== "unparsed_receipt" && e.kind !== "ambiguous_effect") out.push({ text: "not an accepted award", serious: false });
  if (e.accounting_role === "reference_only_not_an_additional_award") out.push({ text: "reference only, not an additional award", serious: false });
  if (e.reward_link_status && e.reward_link_status !== "linked_event") out.push({ text: `reward link: ${e.reward_link_status.replaceAll("_", " ")}`, serious: true });
  if (e.assignment_basis && e.assignment_basis !== "observed_within_calendar_window") out.push({ text: `assigned by ${e.assignment_basis.replaceAll("_", " ")}`, serious: false });
  if (e.kind === "ambiguous_effect") out.push({ text: `ambiguous effect${typeof d.reason === "string" ? `: ${d.reason.replaceAll("_", " ")}` : ""}`, serious: true });
  if (e.kind === "unparsed_receipt") out.push({ text: "receipt could not be parsed", serious: true });
  if (e.kind === "lesson_purchases") {
    if (d.cost_basis === "unresolved") out.push({ text: "lesson cost unresolved", serious: true });
    else if (typeof d.cost_basis === "string" && !d.cost_basis.startsWith("observed")) out.push({ text: `lesson cost ${d.cost_basis.replaceAll("_", " ")}`, serious: false });
    if (d.after_balance_observed === false) out.push({ text: "balance after the purchase not observed", serious: false });
  }
  if (e.kind === "song_acquisitions" && d.name_conflicted) out.push({ text: "song name conflicted", serious: true });
  if ((e.kind === "skill_purchases" || e.kind === "skill_purchase_batch") && d.purchased_list_complete === false) out.push({ text: "purchased skill list incomplete", serious: true });
  if (e.kind === "training" && d.training_outcome === "failure") out.push({ text: "training failed", serious: false });
  const derived: string[] = [];
  for (const fields of Object.values(e.changes ?? {})) {
    for (const [field, c] of Object.entries(fields)) {
      if (c.basis === "turn_difference") derived.push(`${field.replace("_", " ")} (worked out from the difference between turns)`);
      else if (!c.basis || !c.basis.startsWith("observed_")) derived.push(`${field.replace("_", " ")} (${(c.basis ?? "basis unknown").replaceAll("_", " ")})`);
    }
  }
  if (derived.length) out.push({ text: `amount not read directly: ${derived.join(", ")}`, serious: false });
  return out;
}

export function entryName(e: Entry): string {
  const d = (e.detail ?? {}) as Detail;
  const name = [d.training_name, d.name, d.race_name, d.title].find((v) => typeof v === "string" && v) as string | undefined;
  return name || e.context_title || e.kind.replaceAll("_", " ");
}
