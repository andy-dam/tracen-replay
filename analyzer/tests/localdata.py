"""Locally preserved evidence the tests may use when it is present.

Some tests replay real frames, OCR caches and reports preserved from earlier
analyses. Those files live under the repository's ``.local`` directory, are
never published, and are absent on a clean checkout. Such tests live in
``analyzer/lab/tests``, not in the main suite, and skip rather than fail
when the evidence is missing there too. This module is the only place that
knows where the evidence is: every test names a root here by what it is,
not by where it sits.

``TRACEN_LOCAL_EVIDENCE`` overrides the base directory (default ``.local``,
resolved against the current working directory, which the suite runs from
the repository root). Evidence roots stay relative, as the analyzer's own
inputs are; only ``scratch()`` directories are absolute, because tests create
symbolic links there and a link whose target is a relative path resolves
against the link's own directory, not the working directory.
"""
import os
import unittest
from pathlib import Path

BASE = Path(os.environ.get("TRACEN_LOCAL_EVIDENCE", ".local"))

# The OCR models the analyzer runs with; tests that read frames need them.
MODEL_DIR = BASE / "models" / "rapidocr"

# Named roots. A key says what the data is; the value is where it is kept.
# The three development recordings are the repository owner's own captures
# (first, second and third); "held-out" recordings came from other players
# and were only ever used blind. Where two preserved implementations of the
# same artifact exist, the older is suffixed OLDER and the newer NEWER.
ROOTS: dict[str, str] = {
    # A stub OCR reader's models, distinct from the real rapidocr models above.
    "stub_reader_models": "models/test-reader",

    # The three development recordings (the repository owner's own captures).
    "development_first_recording": "full-recording/v1",
    "development_second_recording": "full-recording/independent-01",
    "development_third_recording_baseline": "full-recording/independent-02/initial-baseline",
    # The umbrella directory holding all preserved recording caches.
    "full_recording_archive": "full-recording",

    # The fourth recording (a declared retest after tuning, not blind evidence).
    "fourth_recording_untouched_baseline": "final-reliability-v1/worker-runs/untouched-fourth-v1",
    "fourth_recording_source_controls_early": "final-reliability-v1/fourth-source-controls-early-v1",
    "fourth_recording_source_controls_middle": "final-reliability-v1/fourth-source-controls-middle-v1",
    "fourth_recording_retest_older": "final-reliability-v1/worker-runs/fourth-declared-retest-v10",
    "fourth_recording_retest_newer": "final-reliability-v1/worker-runs/fourth-declared-retest-v11",

    # A held-out recording from another player, used only for blind grading.
    "held_out_recording_report": "final-reliability-v1/worker-runs/untouched-creatorB-gran-concert-v28-logs",

    # Prepared worker-input snapshots, oldest to newest; each is shared across
    # whichever recordings were re-prepared into it.
    "prepared_snapshot_initial": "final-reliability-v1/worker-runs/post-recognition-g8-v2-prepared",
    "prepared_snapshot_early": "final-reliability-v1/worker-runs/post-recognition-g8-v3-prepared",
    "prepared_snapshot_mid": "final-reliability-v1/worker-runs/post-recognition-g8-v7-prepared",
    "prepared_snapshot_late": "final-reliability-v1/worker-runs/post-recognition-g8-v8-prepared",
    "prepared_snapshot_final": "final-reliability-v1/worker-runs/post-recognition-g8-v11-prepared",

    # Preserved worker-run report logs, distinct from the prepared snapshots above.
    "third_recording_hint_recovery_report": "final-reliability-v1/worker-runs/post-recognition-g8-v10-independent-02-logs",
    "third_recording_receipt_logs": "final-reliability-v1/worker-runs/post-recognition-g8-v11-independent-02-logs",
    "first_recording_t028_logs": "final-reliability-v1/worker-runs/post-recognition-g8-v12-v1-logs",

    # Preserved full-worker candidate reports used as regression fixtures.
    "full_worker_candidate_first_recording_report": "final-reliability-v1/full-worker-candidate-v4/v1",
    "full_worker_candidate_first_recording_prepared_root_report": "final-reliability-v1/full-worker-candidate-v5/v1",
    "full_worker_candidate_batch_reports": "final-reliability-v1/full-worker-candidate-v6",
    "adapter_grading_batch_fixture": "final-reliability-v1/full-worker-candidate-v10-batch-v1",

    # Diagnostic scratch artifacts kept for regression coverage.
    "diagnostic_friendship_receipt_frame": "final-reliability-v1/diagnostic-scratch/friendship-155750-source",
    "diagnostic_numeric_dense_registration": "final-reliability-v1/diagnostic-scratch/numeric-dense-registration-v7",
    "fourth_recording_matikane_package": "final-reliability-v1/diagnostic-scratch/fourth-package2-matikane-prepared-v1",

    "preview_recovery_loader_probe": "final-reliability-v1/preview-recovery-inputs-v2/loader-probe-root",

    "second_recording_integration_replay": "final-reliability-v1/integration-replay-v4/independent-01",

    "numeric_cap_refinements": "final-reliability-v1/numeric-cap-refinements-v1",
    "weak_state_recovery_inputs": "final-reliability-v1/weak-state-recovery",

    "turn_explanation_baseline": "turn-explanations-v1/before",

    # A preserved standalone audit script, loaded dynamically by its test.
    "occurrence_crosswalk_module": "final-reliability-v1/audit_occurrence_crosswalk_v11.py",

    # The final-reliability working directory itself, for one-off artifacts
    # that do not warrant their own narrower root.
    "final_reliability_artifacts": "final-reliability-v1",
}


def local(*parts: str) -> Path:
    """A path under the local evidence base, for one-off files."""
    return BASE.joinpath(*parts)


def root(key: str, *parts: str) -> Path:
    """A path under a named root."""
    return BASE.joinpath(ROOTS[key], *parts)


def available(key: str, *parts: str) -> bool:
    return root(key, *parts).exists()


def needs(key: str, *parts: str):
    """Skip a test or class when the named local evidence is absent."""
    return unittest.skipUnless(available(key, *parts), f"local evidence '{key}' is not present")


def scratch(name: str) -> Path:
    """A writable directory for a test's own outputs under the local base.

    The path is absolute so that symbolic links a test makes inside it point
    where the test means them to.
    """
    path = (BASE / "test-runs" / name).resolve()
    path.mkdir(parents=True, exist_ok=True)
    return path
