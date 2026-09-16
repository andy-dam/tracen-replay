// The analysis as a viewer sees it: five phases over the worker's stages.
// The worker reports a stage when it finishes it (and a percentage only while
// it reads frames), so the phase bar fills by finished stages, with the
// reading phase driven by the frame count. Weights are the share of a full
// career's wall time each phase takes on a desktop machine; they only shape
// the bar, nothing is computed from them elsewhere.
import type { Job } from "./api";

export interface Phase {
  id: string;
  label: string;
  /** What the phase does, for the caption under the bar. */
  doing: string;
  stages: string[];
  weight: number;
}

export const PHASES: Phase[] = [
  { id: "read", label: "Reading frames", doing: "sampling the recording and reading every frame", stages: ["capture", "ocr"], weight: 48 },
  { id: "understand", label: "Understanding screens", doing: "turning the readings into screens, values and receipts", stages: ["base_readings", "automatic_refinement", "race_quantity_refinement", "currency_refinement", "reload_readings", "hint_card_preparation", "inspections_merged", "numeric_receipt_recovery"], weight: 12 },
  { id: "closer", label: "Looking closer", doing: "re-reading result animations and receipts at 60 frames a second where a number was unclear", stages: ["training_gain_recovery", "receipt_inspection", "inspection_loads", "occluded_receipt_recovery"], weight: 20 },
  { id: "assemble", label: "Assembling the career", doing: "building turns, events, purchases and the accounting", stages: ["assemble", "boundary_state_recovery", "assemble_after_boundary", "validate_output"], weight: 10 },
  { id: "finish", label: "Writing the report", doing: "saving the report, the timeline and the viewer page", stages: ["save_report", "timeline_document", "viewer", "complete"], weight: 10 },
];

const ORDER: string[] = PHASES.flatMap((p) => p.stages);

export interface PhaseView {
  id: string;
  label: string;
  doing: string;
  weight: number;
  state: "done" | "active" | "todo";
  /** 0..1 of the phase completed. */
  fill: number;
}

export interface ProgressView {
  phases: PhaseView[];
  /** 0..100 across the whole analysis. */
  overall: number;
  /** The phase in progress, if any. */
  current: PhaseView | null;
  /** The last stage the worker finished, as words. */
  lastDone: string;
}

export function stageWords(stage: string): string {
  return stage.replaceAll("_", " ");
}

/** Where an analysis stands, from the job's last finished stage and the frame count. */
export function progressView(job: Job | null, ocrPercent: number | null): ProgressView {
  const stage = job?.stage ?? "";
  const reading = stage === "ocr";
  // The index of the last finished stage; "ocr" is in progress rather than finished.
  let doneIndex = ORDER.indexOf(stage);
  if (reading) doneIndex = ORDER.indexOf("capture");
  if (job?.status && !["queued", "running"].includes(job.status) && job.status !== "failed" && job.status !== "cancelled" && job.status !== "interrupted") doneIndex = ORDER.length - 1;
  let cursor = 0;
  let overall = 0;
  let current: PhaseView | null = null;
  const phases: PhaseView[] = PHASES.map((p) => {
    const first = cursor;
    cursor += p.stages.length;
    const finished = Math.max(0, Math.min(p.stages.length, doneIndex + 1 - first));
    let fill = finished / p.stages.length;
    let state: PhaseView["state"] = finished === p.stages.length ? "done" : finished > 0 || (current === null && doneIndex + 1 === first && job?.status === "running") ? "active" : "todo";
    if (p.id === "read" && reading) {
      state = "active";
      fill = Math.max(fill, ocrPercent !== null ? (0.5 + ocrPercent / 100 * (p.stages.length - 0.5)) / p.stages.length : 0.25);
    }
    if (state === "active" && current === null) current = { id: p.id, label: p.label, doing: p.doing, weight: p.weight, state, fill };
    overall += p.weight * fill;
    return { id: p.id, label: p.label, doing: p.doing, weight: p.weight, state, fill };
  });
  // A phase whose stages have all finished but whose successor has not started is still in progress.
  if (current === null && job?.status === "running") {
    const next = phases.find((p) => p.state !== "done");
    if (next) {
      next.state = "active";
      current = next;
    }
  }
  return { phases, overall: Math.min(100, Math.round(overall)), current, lastDone: doneIndex >= 0 ? stageWords(ORDER[doneIndex]) : "" };
}
