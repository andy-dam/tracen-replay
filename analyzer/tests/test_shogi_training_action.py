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
from tracen_replay.vision import parse


ROOT = Path(__file__).resolve().parents[2]
SOURCE_ROOT = (
    ROOT
    / ".local/final-reliability-v1/worker-runs/fourth-declared-retest-v10/neural"
)


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


@unittest.skipUnless(
    all(
        (SOURCE_ROOT / f"part-015-frame-000{frame}.json").is_file()
        for frame in (244, 245, 246, 247)
    ),
    "declared fourth source sidecars are unavailable",
)
class ShogiTrainingActionTests(unittest.TestCase):
    def test_result_layout_and_adjacent_identity_emit_one_canonical_action(self):
        rows = _source_rows()

        # The transition frames identify the selected Wit training and the
        # two result frames establish one completed result group.  Browsing a
        # training option is not treated as a selected action by parse().
        self.assertEqual(rows[0]["screen"], "unknown")
        self.assertIsNone(rows[0]["training_option"])
        self.assertEqual(rows[1]["screen"], "training_result")
        self.assertEqual(rows[2]["screen"], "training_result")
        self.assertEqual(rows[1]["training_option"], "wit")
        self.assertEqual(rows[2]["training_option"], "wit")
        self.assertEqual(rows[1]["facts"]["training_outcome"], "success")
        self.assertEqual(rows[2]["facts"]["training_name"], "Shogi")

        events = training_events(rows, [])
        actions = training_actions(events)

        self.assertEqual(len(events), 1)
        self.assertEqual(len(actions), 1)
        action = actions[0]
        self.assertEqual(action["training_option"], "wit")
        self.assertEqual(action["training_name"], "Shogi")
        self.assertEqual(action["training_outcome"], "success")
        self.assertEqual(action["result_group"]["training_option"], "wit")
        self.assertIsNone(action["click_timestamp_ms"])

    def test_adapter_exposes_result_supported_committed_action(self):
        from tests.test_causal_accounting import fixture

        rows = _source_rows()
        events = training_events(rows, [])
        actions = training_actions(events)
        report = copy.deepcopy(fixture())
        data = report["gameplay_tracking"]
        data["readings"] = rows
        data["events"] = events
        data["turn_action_receipts"] = actions

        adapted = report_document(report)
        action_rows = [
            row for row in adapted["observations"] if row["category"] == "action"
        ]

        self.assertEqual(len(action_rows), 1)
        row = action_rows[0]
        self.assertEqual(row["phase"], "committed")
        self.assertEqual(
            row["payload"],
            {
                "kind": "training",
                "training_option": "wit",
                "training_name": "Shogi",
                "result": "success",
            },
        )
        self.assertIsNone(actions[0]["click_timestamp_ms"])


if __name__ == "__main__":
    unittest.main()
