import json
import unittest
from pathlib import Path

from tests import localdata
from tracen_replay.training_gain_phases import (
    resolve_full_component_phase,
    source_gain_observations,
)
from tracen_replay.transactions import training_events


def _candidate(field, value, family, *, canonical=True):
    boxes = {
        "speed": [300, 832, 414, 890],
        "wit": [498, 950, 610, 1008],
        "skill_points": [696, 950, 812, 1008],
    }
    wide_boxes = {
        "speed": [250, 812, 462, 909],
        "wit": [448, 930, 660, 1027],
        "skill_points": [646, 930, 858, 1027],
    }
    box = wide_boxes[field] if family == "wide_gain" else boxes[field]
    return {
        "region": f"{family}.{field}",
        "crop_family": family,
        "raw_text": f"+{value}",
        "amount": value,
        "confidence": 99.0,
        "box": box,
        "source_role": "amount_crop_candidate",
        "input_eligible": True,
        "canonical_eligible": canonical,
    }


def _row(timestamp, value, field, shape, *, families=("gain",), evidence=None):
    provenance = {
        "canonical_amount": value,
        "canonical_basis": "source_crop_family_geometry",
        "conflict_state": "resolved_same_amount_across_source_crops",
        "candidate_amounts": [value],
        "candidates": [_candidate(field, value, family) for family in families],
    }
    provenance["canonical_candidate"] = provenance["candidates"][0]
    return {
        "source_timestamp_ms": timestamp,
        "evidence": evidence or f"{timestamp}.png",
        "screen": "training_result",
        "training_option": "wit",
        "facts": {
            "training_gains": {field: value},
            "observed_training_gain_fields": list(shape),
            "training_gain_crop_provenance": {field: provenance},
        },
        "effects": [],
        "stats": {},
    }


class ComponentFirstSourcePhaseTests(unittest.TestCase):
    def test_repeated_full_suffix_overrides_early_component(self):
        rows = [
            _row(100, 2, "wit", ("speed", "wit", "skill_points")),
            _row(133, 21, "wit", ("speed", "wit")),
            _row(166, 21, "wit", ("wit", "skill_points")),
        ]

        result = resolve_full_component_phase(
            source_gain_observations(rows, "wit")
        )

        self.assertEqual(result["accepted_amount"], 21)
        self.assertEqual(
            result["basis"],
            "source_component_prefix_before_stable_full_suffix",
        )
        self.assertEqual(result["source_full_proof"], "repeated_source_frames")
        self.assertEqual(
            [item["value"] for item in result["component_observations"]], [2]
        )
        self.assertEqual(
            [item["source_timestamp_ms"] for item in result["full_observations"]],
            [133, 166],
        )
        self.assertCountEqual(
            result["full_shapes"],
            [["speed", "wit"], ["skill_points", "wit"]],
        )

    def test_single_full_frame_requires_two_canonical_crop_families(self):
        rows = [
            _row(100, 1, "skill_points", ("speed", "wit", "skill_points")),
            _row(
                133,
                13,
                "skill_points",
                ("wit", "skill_points"),
                families=("gain", "wide_gain"),
            ),
        ]

        result = resolve_full_component_phase(
            source_gain_observations(rows, "skill_points")
        )

        self.assertEqual(result["accepted_amount"], 13)
        self.assertEqual(
            result["source_full_proof"],
            "same_frame_source_crop_family_agreement",
        )
        self.assertEqual(result["source_full_observation_count"], 1)

    def test_persistent_full_disagreement_stays_unresolved(self):
        rows = [
            _row(100, 2, "wit", ("speed", "wit", "skill_points")),
            _row(133, 21, "wit", ("speed", "wit")),
            _row(166, 21, "wit", ("speed", "wit")),
            _row(199, 22, "wit", ("speed", "wit")),
            _row(232, 22, "wit", ("speed", "wit")),
        ]

        self.assertIsNone(
            resolve_full_component_phase(source_gain_observations(rows, "wit"))
        )

    def test_equal_shape_component_and_full_stay_unresolved(self):
        rows = [
            _row(100, 2, "wit", ("speed", "wit")),
            _row(133, 21, "wit", ("speed", "wit"), families=("gain", "wide_gain")),
            _row(166, 21, "wit", ("speed", "wit"), families=("gain", "wide_gain")),
        ]

        self.assertIsNone(
            resolve_full_component_phase(source_gain_observations(rows, "wit"))
        )

    def test_single_full_frame_without_dual_crop_support_stays_unresolved(self):
        rows = [
            _row(100, 1, "skill_points", ("speed", "wit", "skill_points")),
            _row(133, 13, "skill_points", ("wit", "skill_points")),
        ]

        self.assertIsNone(
            resolve_full_component_phase(
                source_gain_observations(rows, "skill_points")
            )
        )

    def test_single_full_frame_requires_distinct_physical_family_boxes(self):
        rows = [
            _row(100, 1, "skill_points", ("speed", "wit", "skill_points")),
            _row(
                133,
                13,
                "skill_points",
                ("wit", "skill_points"),
                families=("gain", "wide_gain"),
            ),
        ]
        candidates = rows[1]["facts"]["training_gain_crop_provenance"][
            "skill_points"
        ]["candidates"]
        candidates[1]["box"] = list(candidates[0]["box"])

        self.assertIsNone(
            resolve_full_component_phase(
                source_gain_observations(rows, "skill_points")
            )
        )

    def test_single_full_frame_ignores_missing_or_nonfinite_family_box(self):
        for bad_box in (
            None,
            [float("nan"), 930, 858, 1027],
            [5000, 5000, 5100, 5100],
        ):
            with self.subTest(bad_box=bad_box):
                rows = [
                    _row(100, 1, "skill_points", ("speed", "wit", "skill_points")),
                    _row(
                        133,
                        13,
                        "skill_points",
                        ("wit", "skill_points"),
                        families=("gain", "wide_gain"),
                    ),
                ]
                candidates = rows[1]["facts"][
                    "training_gain_crop_provenance"
                ]["skill_points"]["candidates"]
                candidates[1]["box"] = bad_box

                self.assertIsNone(
                    resolve_full_component_phase(
                        source_gain_observations(rows, "skill_points")
                    )
                )

    def test_single_full_frame_requires_recognized_family_and_chosen_membership(self):
        rows = [
            _row(100, 1, "skill_points", ("speed", "wit", "skill_points")),
            _row(
                133,
                13,
                "skill_points",
                ("wit", "skill_points"),
                families=("gain", "wide_gain"),
            ),
        ]
        provenance = rows[1]["facts"]["training_gain_crop_provenance"][
            "skill_points"
        ]
        candidates = provenance["candidates"]

        candidates[1]["region"] = "fake_gain.skill_points"
        candidates[1]["crop_family"] = "fake_gain"
        self.assertIsNone(
            resolve_full_component_phase(
                source_gain_observations(rows, "skill_points")
            )
        )

        rows = [
            _row(100, 1, "skill_points", ("speed", "wit", "skill_points")),
            _row(
                133,
                13,
                "skill_points",
                ("wit", "skill_points"),
                families=("gain", "wide_gain"),
            ),
        ]
        provenance = rows[1]["facts"]["training_gain_crop_provenance"][
            "skill_points"
        ]
        chosen = dict(provenance["canonical_candidate"])
        chosen["box"] = None
        provenance["canonical_candidate"] = chosen
        self.assertIsNone(
            resolve_full_component_phase(
                source_gain_observations(rows, "skill_points")
            )
        )

        rows = [
            _row(100, 1, "skill_points", ("speed", "wit", "skill_points")),
            _row(
                133,
                13,
                "skill_points",
                ("wit", "skill_points"),
                families=("gain", "wide_gain"),
            ),
        ]
        provenance = rows[1]["facts"]["training_gain_crop_provenance"][
            "skill_points"
        ]
        provenance["canonical_candidate"] = dict(
            provenance["canonical_candidate"], amount=999
        )
        self.assertIsNone(
            resolve_full_component_phase(
                source_gain_observations(rows, "skill_points")
            )
        )

    def test_repeated_physical_path_cannot_create_stable_suffix(self):
        rows = [
            _row(100, 2, "wit", ("speed", "wit", "skill_points"), evidence="component.png"),
            _row(133, 21, "wit", ("speed", "wit"), evidence="full.png"),
            _row(166, 21, "wit", ("wit", "skill_points"), evidence="FULL.PNG"),
        ]

        self.assertIsNone(
            resolve_full_component_phase(source_gain_observations(rows, "wit"))
        )

    def test_result_event_projects_source_phase_when_state_context_exists(self):
        rows = [
            _row(100, 1, "skill_points", ("speed", "wit", "skill_points")),
            _row(
                133,
                13,
                "skill_points",
                ("wit", "skill_points"),
                families=("gain", "wide_gain"),
            ),
        ]
        event = training_events(
            rows,
            [{"last_seen_ms": 0, "values": {}, "evidence": "before.png"}],
        )[0]

        self.assertEqual(event["deltas"]["skill_points"], 13)
        self.assertNotIn("skill_points", event["conflicting_readings"])
        self.assertEqual(
            event["gain_phase_candidates"]["skill_points"]["basis"],
            "source_component_prefix_before_stable_full_suffix",
        )


if __name__ == "__main__":
    unittest.main()
