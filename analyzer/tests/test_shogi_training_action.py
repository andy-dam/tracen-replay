"""Regression coverage for the source-backed Shogi training action.

The source sidecars are the detector output from the declared fourth recording;
the test re-runs the normal parser/transaction/adapter boundary without using
the historical report as an oracle.  In particular, a result card proves that
the training completed, while the sampled frames do not prove the click time.
"""

from __future__ import annotations

import copy
import json
from pathlib import Path
import unittest

from tracen_replay.evaluation_adapters import report_document
from tracen_replay.training_result_layout import detect_training_result_layout
from tracen_replay.transactions import training_actions, training_events
from tests import localdata
from tracen_replay.vision import parse


ROOT = Path(__file__).resolve().parents[2]
SOURCE_ROOT = localdata.root("fourth_recording_retest_older", "neural")


def _source_rows():
    """Load the four source frames surrounding the Shogi result."""

    rows = []
    for frame in (244, 245, 246, 247):
        path = SOURCE_ROOT / f"part-015-frame-000{frame}.json"
        raw = json.loads(path.read_text(encoding="utf-8"))
        layout = detect_training_result_layout(
            raw.get("lines", []),
            header=raw.get("header"),
            current_grid=raw.get("current_grid", False),
            result_grid=raw.get("result_grid", False),
        )
        # This is the same promotion performed by NeuralReader.read.  The
        # source sidecar is reused only as an immutable OCR input here.
        raw["result_grid"] = layout.get("result_grid") is True
        row = parse(raw)
        row.update(
            source_timestamp_ms=raw["source_timestamp_ms"],
            evidence=raw["evidence"],
            source_frame_sha256=raw.get("source_frame_sha256"),
        )
        rows.append(row)
    return rows


if __name__ == "__main__":
    unittest.main()
