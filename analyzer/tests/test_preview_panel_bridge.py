import copy
import json
import unittest
from pathlib import Path

from tracen_replay.preview_observations import build_preview_observations


SOURCE_ROOT_V6 = Path(
    ".local/final-reliability-v1/preview-recovery-inputs-v2/loader-probe-root"
)


PHASE_PROOF = {
    "phase": "preview",
    "menu_proven": True,
    "result_proven": False,
    "basis": "current_grid_and_source_failure_and_training_control",
}


def _merged_panel(
    field="vocal", current=63, amount=13, *, confidence=98.7,
    status="resolved_merged_panel_value", band=None,
):
    if band is None:
        band = [190, 402, 335, 447] if field == "vocal" else [190, 346, 335, 391]
    box = [209, 405, 316, 443] if field == "vocal" else [206, 347, 315, 387]
    return {
        "field": field,
        "band": band,
        "status": status,
        "current": None if status.startswith("unresolved_") else {
            "value": current,
            "observation": {
                "text": f"{current}+{amount}",
                "confidence": confidence,
                "box": box,
            },
        },
        "projected": None if status.startswith("unresolved_") else {
            "value": amount,
            "observation": {
                "text": f"{current}+{amount}",
                "confidence": confidence,
                "box": box,
            },
        },
        "raw_observations": [{
            "text": f"{current}+{amount}",
            "confidence": confidence,
            "box": box,
        }],
    }


def _row(
    timestamp=100, *, provenance=None, evidence="capture/frame.png",
    overlay=None, modifiers=None, screen="training_preview",
):
    facts = {
        "preview_phase_proof": copy.deepcopy(PHASE_PROOF),
        "preview_overlay_proven": bool(overlay),
        "preview_overlay_effects": copy.deepcopy(overlay or []),
        "preview_modifier_effects": copy.deepcopy(modifiers or []),
    }
    if provenance is not None:
        facts["performance_panel_provenance"] = copy.deepcopy(provenance)
    return {
        "source_timestamp_ms": timestamp,
        "evidence": evidence,
        "screen": screen,
        "completed_action": None,
        "facts": facts,
        "stats": {},
    }


class PerformancePanelPreviewBridgeTests(unittest.TestCase):
    def test_merged_panel_projection_becomes_preview_observation(self):
        row = _row(provenance={"vocal": _merged_panel()})
        result = build_preview_observations([row])

        self.assertEqual(
            [item["payload"] for item in result["observations"]],
            [{"kind": "performance_change", "field": "vocal", "amount": 13}],
        )
        observation = result["observations"][0]
        self.assertEqual(observation["observation_basis"],
                         "accepted_typed_performance_panel_preview")
        self.assertEqual(observation["evidence"], ["capture/frame.png"])
        self.assertEqual(observation["source_fact_keys"],
                         ["performance_panel_provenance"])
        self.assertEqual(
            observation["source_effect_indices"],
            ["performance_panel_provenance/vocal"],
        )

    def test_low_confidence_merged_panel_can_be_recovered_from_source_geometry(self):
        # The sidebar balance reader intentionally leaves this row unresolved
        # below its balance confidence floor.  The preview bridge has a lower,
        # explicit source-quality floor and still requires the full merged row.
        panel = _merged_panel(
            field="passion", current=36, amount=13, confidence=93.644,
            status="unresolved_low_confidence_merged_panel_value",
        )
        result = build_preview_observations([_row(provenance={"passion": panel})])

        self.assertEqual(
            [item["payload"] for item in result["observations"]],
            [{"kind": "performance_change", "field": "passion", "amount": 13}],
        )
        self.assertEqual(
            result["observations"][0]["payload"]["amount"],
            int(panel["raw_observations"][0]["text"].split("+")[1]),
        )

    def test_existing_typed_effect_and_panel_projection_are_one_observation(self):
        overlay = [{
            "kind": "performance_change", "field": "vocal", "amount": 13,
            "phase": "preview", "preview": True, "awarded": False,
            "source_evidence": ["capture/frame.png"],
        }]
        result = build_preview_observations([
            _row(provenance={"vocal": _merged_panel()}, overlay=overlay)
        ])

        matches = [
            item for item in result["observations"]
            if item["payload"] == {
                "kind": "performance_change", "field": "vocal", "amount": 13,
            }
        ]
        self.assertEqual(len(matches), 1)
        self.assertEqual(matches[0]["source_fact_keys"], [
            "preview_overlay_effects", "performance_panel_provenance",
        ])

    def test_projection_is_not_derived_from_balance_or_projection_mapping(self):
        row = _row(provenance={"vocal": {
            "field": "vocal",
            "band": [190, 402, 335, 447],
            "status": "resolved_current_panel_value",
            "current": {"value": 63},
            "projected": None,
            "raw_observations": [{
                "text": "63", "confidence": 99, "box": [209, 405, 253, 443],
            }],
        }})
        row["facts"]["performance_points"] = {"vocal": 63}
        row["facts"]["projected_performance_gains"] = {"vocal": 13}

        result = build_preview_observations([row])

        self.assertEqual(result["observations"], [])
        self.assertNotIn("vocal", {
            item["payload"].get("field") for item in result["observations"]
        })

        row = _row(provenance={"vocal": _merged_panel()})
        row["facts"]["projected_performance_gains"] = {"vocal": 14}
        result = build_preview_observations([row])
        self.assertEqual(result["observations"], [])
        self.assertEqual(
            result["rejected_counts"],
            {"conflicting_performance_panel_preview_amounts": 1},
        )

    def test_missing_menu_proof_does_not_promote_panel_projection(self):
        row = _row(provenance={"vocal": _merged_panel()})
        row["facts"].pop("preview_phase_proof")

        result = build_preview_observations([row])

        self.assertEqual(result["observations"], [])
        self.assertEqual(
            result["rejected_counts"],
            {"performance_panel_preview_missing_menu_proof": 1},
        )

    def test_result_phase_and_success_conflicts_are_rejected(self):
        for mutate in (
            lambda row: row["facts"]["preview_phase_proof"].update(result_proven=True),
            lambda row: row["facts"].update(result_marker_visible=True),
        ):
            with self.subTest(mutate=mutate):
                row = _row(provenance={"vocal": _merged_panel()})
                mutate(row)
                result = build_preview_observations([row])
                self.assertEqual(result["observations"], [])

    def test_conflicting_merged_rows_are_withheld(self):
        panel = _merged_panel()
        panel["raw_observations"].append({
            "text": "63+14", "confidence": 98.8,
            "box": [209, 405, 316, 443],
        })
        result = build_preview_observations([_row(provenance={"vocal": panel})])

        self.assertEqual(result["observations"], [])
        self.assertEqual(
            result["rejected_counts"],
            {"conflicting_performance_panel_preview_rows": 1},
        )

    def test_projection_with_unrelated_field_or_excluded_observation_is_rejected(self):
        panel = _merged_panel()
        panel["field"] = "passion"
        result = build_preview_observations([_row(provenance={"vocal": panel})])
        self.assertEqual(result["observations"], [])
        self.assertEqual(
            result["rejected_counts"],
            {"conflicting_performance_panel_preview_field": 1},
        )

        panel = _merged_panel()
        panel["raw_observations"][0]["input_eligible"] = False
        result = build_preview_observations([_row(provenance={"vocal": panel})])
        self.assertEqual(result["observations"], [])

        panel = _merged_panel(band=[0, 0, 1920, 1080])
        result = build_preview_observations([_row(provenance={"vocal": panel})])
        self.assertEqual(result["observations"], [])

        panel = _merged_panel()
        panel["raw_observations"][0]["box"] = [float("nan"), 405, 316, 443]
        result = build_preview_observations([_row(provenance={"vocal": panel})])
        self.assertEqual(result["observations"], [])

    def test_source_evidence_is_required(self):
        row = _row(provenance={"vocal": _merged_panel()}, evidence="line:42")
        result = build_preview_observations([row])

        self.assertEqual(result["observations"], [])
        self.assertEqual(
            result["rejected_counts"],
            {"performance_panel_preview_missing_source_evidence": 1},
        )

    def test_actual_v6_source_rows(self):
        report_path = Path(
            ".local/final-reliability-v1/full-worker-candidate-v6/"
            "combined-grading/independent-02-report.json"
        )
        if not report_path.is_file() or not SOURCE_ROOT_V6.is_dir():
            self.skipTest("v6 independent-02 source inputs are not present")
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
                    [row], source_root=SOURCE_ROOT_V6
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


if __name__ == "__main__":
    unittest.main()
