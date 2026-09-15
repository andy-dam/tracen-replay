"""Hardening tests for the preview phase and performance provenance gates."""

from __future__ import annotations

import copy
import math
import unittest

from tests.test_preview_observations import song_modifier_source
from tracen_replay.preview_observations import (
    _confidence,
    _explicit_phase_proof,
    _line_box,
    _phase_box,
    _phase_finite_number,
    _performance_panel_projection,
    parse_preview_overlay,
    produce_preview_panel_from_lines,
)


def _phase(**overrides):
    value = {
        "phase": "preview",
        "menu_proven": True,
        "result_proven": False,
        "basis": "current_training_grid",
        "option": "speed",
    }
    value.update(overrides)
    return value


def _observation(text, box=(209, 405, 316, 443)):
    return {
        "text": text,
        "confidence": 98.0,
        "box": list(box),
    }


def _merged_provenance(*, signed=()):
    raw = [_observation("63+13")]
    raw.extend(_observation(value) for value in signed)
    return {
        "field": "vocal",
        "band": [190, 402, 335, 447],
        "status": "resolved_merged_panel_value",
        "current": {"value": 63, "observation": _observation("63+13")},
        "projected": {"value": 13, "observation": _observation("63+13")},
        "raw_observations": raw,
    }


def _separate_provenance(*, signed=()):
    raw = [_observation("+13")]
    raw.extend(_observation(value) for value in signed)
    return {
        "field": "vocal",
        "band": [190, 402, 335, 447],
        "status": "resolved_separate_panel_values",
        "current": {"value": 63},
        "projected": {"value": 13, "observation": _observation("+13")},
        "raw_observations": raw,
    }


class PreviewPhaseProofHardeningTests(unittest.TestCase):
    def test_empty_geometry_is_not_phase_proof(self):
        self.assertIsNone(_explicit_phase_proof({
            "preview_phase_proof": _phase(geometry={}),
        }))

    def test_arbitrary_basis_without_parser_proof_is_not_phase_proof(self):
        self.assertIsNone(_explicit_phase_proof({
            "preview_phase_proof": _phase(basis="claimed_menu_geometry"),
        }))

    def test_registered_basis_only_proof_remains_compatible(self):
        proof = _explicit_phase_proof({
            "preview_phase_proof": _phase(),
        })
        self.assertIsNotNone(proof)
        self.assertEqual(proof["basis"], "current_training_grid")

    def test_parser_geometry_is_validated_by_shape_and_finiteness(self):
        panel = produce_preview_panel_from_lines(song_modifier_source())
        self.assertIsNotNone(panel)
        proof = _explicit_phase_proof({"preview_panel": panel})
        self.assertIsNotNone(proof)
        self.assertEqual(proof["basis"],
                         "source_training_failure_badge_and_preview_row_geometry")

        malformed = copy.deepcopy(panel)
        malformed["geometry"]["failure"]["box"][0] = math.nan
        self.assertIsNone(_explicit_phase_proof({"preview_panel": malformed}))

        malformed = copy.deepcopy(panel)
        malformed["geometry"]["layout"] = "invented_layout"
        self.assertIsNone(_explicit_phase_proof({"preview_panel": malformed}))

    def test_competing_explicit_result_proof_abstains(self):
        self.assertIsNone(_explicit_phase_proof({
            "preview_phase_proof": _phase(),
            "training_preview_phase_proof": _phase(
                basis="current_training_grid_and_failure_badge",
                result_proven=True,
            ),
        }))
        self.assertIsNone(_explicit_phase_proof({
            "preview_phase_proof": _phase(),
            "training_preview_phase_proof": {"result_proven": True},
        }))

    def test_malformed_positive_proof_cannot_be_hidden_by_valid_panel(self):
        panel = produce_preview_panel_from_lines(song_modifier_source())
        self.assertIsNotNone(panel)
        self.assertIsNone(_explicit_phase_proof({
            "preview_phase_proof": _phase(geometry={}),
            "preview_panel": panel,
        }))

    def test_unknown_basis_and_out_of_pane_geometry_cannot_promote_typed_panel(self):
        source = {
            "header": "Training",
            "evidence": "gameplay/fabricated-panel.png",
            "current_grid": True,
            "preview_panel": {
                "menu_proven": True,
                "result_proven": False,
                "basis": "fabricated_preview_geometry",
                "geometry": {
                    "layout": "fabricated_layout",
                    "evidence": "fabricated",
                    "box": [10000, 10000, 10001, 10002],
                },
                "option": "Speed Lvl 1",
                "effects": [{
                    "kind": "stat_change", "field": "speed", "amount": 5,
                }],
            },
            "lines": [],
        }
        self.assertIsNone(_explicit_phase_proof(source))
        parsed = parse_preview_overlay(source)
        self.assertFalse(parsed["preview_overlay_proven"])
        self.assertEqual(parsed["preview_overlay_effects"], [])

    def test_legitimate_parent_panel_binds_child_without_redundant_child_box(self):
        source = {
            "header": "Training",
            "evidence": "gameplay/typed-panel.png",
            "result_grid": False,
            "current_grid": True,
            "preview_panel": {
                "menu_proven": True,
                "result_proven": False,
                "basis": "accepted_current_menu_geometry",
                "option": "Speed Lvl 1",
                "effects": [{
                    "kind": "stat_change", "field": "speed", "amount": 5,
                }],
            },
            "lines": [],
        }
        parsed = parse_preview_overlay(source)
        self.assertTrue(parsed["preview_overlay_proven"])
        self.assertEqual(parsed["preview_overlay_effects"][0]["amount"], 5)

    def test_present_result_markers_are_strict_booleans(self):
        for marker in (1, "true", {}):
            with self.subTest(marker=marker):
                self.assertIsNone(_explicit_phase_proof({
                    "preview_phase_proof": _phase(result_proven=marker),
                }))
                source = {
                    "header": "Training",
                    "current_grid": True,
                    "result_marker_visible": marker,
                    "lines": [{
                        "text": "Speed Lvl 1", "confidence": 99,
                        "box": [230, 165, 365, 194],
                    }],
                }
                self.assertFalse(parse_preview_overlay(source).get("preview_phase_proof"))

    def test_malformed_geometry_and_confidence_fail_closed_without_overflow(self):
        self.assertFalse(_phase_finite_number(10 ** 10000))
        self.assertFalse(_phase_box([10 ** 10000, 1, 10 ** 10001, 2]))
        self.assertIsNone(_line_box({
            "box": [10 ** 10000, 1, 10 ** 10001, 2],
        }))
        self.assertIsNone(_line_box({
            "box": [float("inf"), 1, float("inf"), 2],
        }))
        self.assertIsNone(_line_box({"box": [10000, 100, 10001, 101]}))
        self.assertEqual(_confidence({"confidence": 10 ** 10000}), 0.0)
        self.assertEqual(_confidence({"confidence": float("inf")}), 0.0)
        self.assertEqual(_confidence({"confidence": -float("inf")}), 0.0)

    def test_typed_child_phase_awarded_and_applied_markers_are_strict(self):
        base = {
            "header": "Training",
            "evidence": "gameplay/typed-child.png",
            "result_grid": True,
            "current_grid": False,
            "preview_panel": {
                "menu_proven": True,
                "result_proven": False,
                "basis": "accepted_current_menu_geometry",
                "option": "Speed Lvl 1",
                "effects": [{
                    "kind": "stat_change", "field": "speed", "amount": 5,
                }],
            },
            "lines": [],
        }
        for key, value in (("phase", 0), ("awarded", 0), ("applied", 1)):
            source = copy.deepcopy(base)
            source["preview_panel"]["effects"][0][key] = value
            with self.subTest(key=key, value=value):
                parsed = parse_preview_overlay(source)
                self.assertFalse(parsed["preview_overlay_proven"])
                self.assertEqual(parsed["preview_overlay_effects"], [])


class PerformancePanelConflictHardeningTests(unittest.TestCase):
    def test_conflicting_signed_row_with_merged_projection_is_unknown(self):
        amount, reason, _status = _performance_panel_projection(
            "vocal", _merged_provenance(signed=("+14",)),
        )
        self.assertIsNone(amount)
        self.assertEqual(reason,
                         "conflicting_performance_panel_preview_amounts")

    def test_conflicting_signed_row_with_typed_projection_is_unknown(self):
        amount, reason, _status = _performance_panel_projection(
            "vocal", _separate_provenance(signed=("+14",)),
        )
        self.assertIsNone(amount)
        self.assertEqual(reason,
                         "conflicting_performance_panel_preview_amounts")

    def test_agreeing_signed_row_can_corroborate_projection(self):
        amount, _observation, mode = _performance_panel_projection(
            "vocal", _separate_provenance(signed=("+13",)),
        )
        self.assertEqual((amount, mode), (13, "separate"))


if __name__ == "__main__":
    unittest.main()
