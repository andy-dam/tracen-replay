// The analysis as a viewer sees it: five phases over the worker's stages.
// The worker reports a stage when it finishes it (and a percentage only while
// it reads frames), so the phase bar fills by finished stages, with the
// reading phase driven by the frame count. Weights are the share of a full
// career's wall time each phase takes on a desktop machine; they only shape
// the bar, nothing is computed from them elsewhere.
import type { Job } from "./api";

interface Phase {
  id: string;
  label: string;
  /** What the phase does, for the caption under the bar. */
  doing: string;
  stages: string[];
  weight: number;
}

const PHASES: Phase[] = [
  { id: "read", label: "Capture & OCR", doing: "sampling the recording and running OCR on every frame", stages: ["capture", "ocr"], weight: 48 },
  { id: "understand", label: "Base Readings & Refinement", doing: "classifying every frame and refining weak readings", stages: ["base_readings", "automatic_refinement", "race_quantity_refinement", "currency_refinement", "currency_refinement_complete", "currency_padding_refinement_complete", "reload_readings", "hint_card_preparation", "inspections_merged"], weight: 12 },
  { id: "closer", label: "Recovery Rereads", doing: "re-reading result animations and receipts at 60 fps where a number was unclear", stages: ["receipt_inspection", "numeric_receipt_recovery", "training_gain_recovery", "inspection_loads", "occluded_receipt_recovery"], weight: 20 },
  { id: "assemble", label: "Assembly & Accounting", doing: "assembling turns, events and purchases, then balancing the accounting", stages: ["assemble", "boundary_state_recovery", "assemble_after_boundary", "validate_output"], weight: 10 },
  { id: "finish", label: "Report & Timeline", doing: "validating and saving the report, the timeline and the viewer page", stages: ["save_report", "timeline_document", "viewer", "complete"], weight: 10 },
];

const ORDER: string[] = PHASES.flatMap((p) => p.stages);
// Stage names the hosted worker sets itself (internal/jobs/remote.go).
const COPY_STAGE = "keeping a playback copy";
const FETCH_STAGE = "fetching the recording";

interface PhaseView {
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

function stageWords(stage: string): string {
  return stage === "ocr" ? "OCR" : stage.split("_").map((w) => w[0].toUpperCase() + w.slice(1)).join(" ");
}

/** Where an analysis stands, from the job's last finished stage and the frame count. */
export function progressView(job: Job | null, ocrPercent: number | null): ProgressView {
  const stage = job?.stage ?? "";
  const reading = stage === "ocr";
  // The index of the last finished stage; "ocr" is in progress rather than finished.
  let doneIndex = ORDER.indexOf(stage);
  if (reading) doneIndex = ORDER.indexOf("capture");
  // The hosted worker names two steps of its own around the analysis: it
  // fetches the recording before, and makes the smaller playback copy after.
  // The copy comes when every analysis stage is done, so the bar stays at
  // the end instead of falling back to the start.
  const copying = stage === COPY_STAGE && job?.status === "running";
  if (copying) doneIndex = ORDER.indexOf("viewer");
  if (job?.status && !["queued", "running", "paused"].includes(job.status) && job.status !== "failed" && job.status !== "cancelled" && job.status !== "interrupted") doneIndex = ORDER.length - 1;
  let cursor = 0;
  let overall = 0;
  let current: PhaseView | null = null;
  const phases: PhaseView[] = PHASES.map((p) => {
    const first = cursor;
    cursor += p.stages.length;
    const finished = Math.max(0, Math.min(p.stages.length, doneIndex + 1 - first));
    let fill = finished / p.stages.length;
    let state: PhaseView["state"] = finished === p.stages.length ? "done" : finished > 0 || (current === null && doneIndex + 1 === first && job?.status === "running") ? "active" : "todo";
    if (p.id === "read" && state !== "done") {
      // Capture takes seconds and OCR the rest of the phase: the frame count
      // is the measure, and a finished capture alone is only the first sliver.
      // The sliver stays when the reading starts, with the frame count on
      // top of it, so the number never steps back from 1% to 0%.
      const sliver = 0.02;
      fill = reading ? sliver + (1 - sliver) * ((ocrPercent ?? 0) / 100) : finished > 0 ? sliver : 0;
      if (reading) state = "active";
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
  if (current && copying) current = { ...current, label: "Playback Copy", doing: "making the smaller copy of the recording that the report plays" };
  if (current && stage === FETCH_STAGE) current = { ...current, label: "Fetching the Recording", doing: "bringing the recording to the worker" };
  return { phases, overall: Math.min(copying ? 99 : 100, Math.round(overall)), current, lastDone: copying ? "Complete" : doneIndex >= 0 ? stageWords(ORDER[doneIndex]) : "" };
}
