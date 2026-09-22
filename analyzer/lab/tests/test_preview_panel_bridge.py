"""Tests of ``tests.test_preview_panel_bridge`` that need locally preserved evidence; they run only where it is."""
import json
import unittest
from tests import localdata
from tracen_replay.preview_observations import build_preview_observations
from tests.test_preview_panel_bridge import SOURCE_ROOT_PROBE


class PerformancePanelPreviewBridgeTests(unittest.TestCase):









    def test_actual_batch_source_rows(self):
        report_path = localdata.root(
            "full_worker_candidate_batch_reports",
            "combined-grading/independent-02-report.json",
        )
        if not report_path.is_file() or not SOURCE_ROOT_PROBE.is_dir():
            self.skipTest("preserved independent-02 source inputs are not present")
        report = json.loads(report_path.read_text(encoding="utf-8"))
        rows = report["gameplay_tracking"]["readings"]
        cases = (
            (401000, "initial-baseline/gameplay/part-003-frame-000165.png",
             ("vocal", 13)),
            (1297750, "initial-baseline/gameplay/part-010-frame-000392.png",
             ("passion", 13)),
            (1298500, "initial-baseline/gameplay/part-010-frame-000395.png",
             ("guts", 19)),
        )
        for timestamp, evidence, expected in cases:
            with self.subTest(timestamp=timestamp):
                row = next(item for item in rows
                           if item.get("source_timestamp_ms") == timestamp
                           and item.get("evidence") == evidence)
                result = build_preview_observations(
                    [row], source_root=SOURCE_ROOT_PROBE
                )
                matches = [
                    item["payload"] for item in result["observations"]
                    if item["payload"].get("field") == expected[0]
                    and item["payload"].get("kind") == (
                        "performance_change"
                        if expected[0] in {"dance", "passion", "vocal", "visual", "composure"}
                        else "stat_change"
                    )
                    and item["payload"].get("amount") == expected[1]
                ]
                self.assertEqual(len(matches), 1)
