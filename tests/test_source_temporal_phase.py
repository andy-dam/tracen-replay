import unittest
from copy import deepcopy

from tracen_replay.training_gain_phases import (
    resolve_full_component_phase,
    resolve_source_temporal_phase,
    source_gain_observations,
)
from tracen_replay.transactions import training_events


def source_row(timestamp, value, *, field="skill_points", shape=("skill_points",)):
    gains = {field: value}
    candidate = {
        "region": f"wide_gain.{field}",
        "crop_family": "wide_gain",
        "raw_text": f"+{value}",
        "confidence": 99.0,
        "box": [448, 930, 660, 1027],
        "source_role": "amount_crop_candidate",
        "input_eligible": True,
        "canonical_eligible": True,
        "amount": value,
    }
    provenance = {
        "canonical_amount": value,
        "canonical_basis": "source_crop_family_geometry",
        "conflict_state": "resolved_same_amount_across_source_crops",
        "candidate_amounts": [value],
        "candidates": [candidate],
        "canonical_candidate": candidate,
    }
    return {
        "source_timestamp_ms": timestamp,
        "evidence": f"{timestamp}.png",
        "screen": "training_result",
        "training_option": "wit",
        "facts": {
            "training_gains": gains,
            "training_gain_crop_provenance": {field: provenance},
            "observed_training_gain_fields": list(shape),
        },
        "effects": [],
        "stats": {},
    }


def unproven_source_row(timestamp, value, *, field="skill_points", shape=("skill_points",)):
    row = source_row(timestamp, value, field=field, shape=shape)
    row["facts"] = deepcopy(row["facts"])
    row["facts"]["training_gain_crop_provenance"].pop(field, None)
    return row


class SourceTemporalPhaseTests(unittest.TestCase):
    def test_equal_shape_source_proof_uses_chronology(self):
        rows = [
            source_row(1000, 23),
            source_row(1033, 23),
            source_row(1066, 2),
            source_row(1099, 2),
        ]
        observations = source_gain_observations(rows, "skill_points")
        self.assertIsNone(resolve_full_component_phase(observations))
        result = resolve_source_temporal_phase(observations, "skill_points")
        self.assertEqual(result["accepted_amount"], 23)
        self.assertEqual(result["phase_order"], "full_before_component")
        self.assertEqual(result["source_full_observation_count"], 2)

    def test_interleaved_source_full_badge_can_recur_after_component(self):
        rows = [
            source_row(1000, 11),
            source_row(1033, 1),
            source_row(1066, 11),
            source_row(1099, 1),
        ]
        result = resolve_source_temporal_phase(
            source_gain_observations(rows, "skill_points"), "skill_points"
        )
        self.assertEqual(result["accepted_amount"], 11)
        self.assertEqual(result["component_first_seen_ms"], 1033)

    def test_training_event_promotes_source_phase_with_checkpoint(self):
        rows = [
            source_row(1000, 13),
            source_row(1033, 13),
            source_row(1066, 1),
            source_row(1099, 1),
        ]
        event = training_events(
            rows,
            [
                {
                    "last_seen_ms": 0,
                    "values": {"skill_points": 600},
                    "evidence": "before.png",
                }
            ],
        )[0]
        self.assertEqual(event["deltas"]["skill_points"], 13)
        self.assertEqual(
            event["gain_phase_candidates"]["skill_points"]["basis"],
            "source_temporal_full_before_component_phase",
        )

    def test_equal_shape_without_source_proof_stays_ambiguous(self):
        rows = [
            dict(source_row(1000, 11), facts={"training_gains": {"skill_points": 11}, "observed_training_gain_fields": ["skill_points"]}),
            dict(source_row(1033, 11), facts={"training_gains": {"skill_points": 11}, "observed_training_gain_fields": ["skill_points"]}),
            dict(source_row(1066, 14), facts={"training_gains": {"skill_points": 14}, "observed_training_gain_fields": ["skill_points"]}),
            dict(source_row(1099, 14), facts={"training_gains": {"skill_points": 14}, "observed_training_gain_fields": ["skill_points"]}),
        ]
        observations = [(row, row["facts"]["training_gains"]["skill_points"]) for row in rows]
        self.assertIsNone(resolve_full_component_phase(observations))
        self.assertIsNone(resolve_source_temporal_phase(observations, "skill_points"))

    def test_reversed_source_phase_stays_ambiguous(self):
        rows = [source_row(1000, 1), source_row(1033, 1), source_row(1066, 11), source_row(1099, 11)]
        self.assertIsNone(
            resolve_source_temporal_phase(
                source_gain_observations(rows, "skill_points"), "skill_points"
            )
        )

    def test_one_unproven_recovery_outlier_is_diagnostic_only(self):
        rows = [
            source_row(1000, 25, shape=("skill_points", "wit")),
            source_row(1033, 2, shape=("skill_points", "wit")),
            unproven_source_row(1066, 23, shape=("wit",)),
            source_row(1099, 25, shape=("skill_points", "wit")),
        ]
        result = resolve_source_temporal_phase(
            source_gain_observations(rows, "skill_points"), "skill_points"
        )
        self.assertEqual(result["accepted_amount"], 25)
        self.assertEqual(result["ignored_unproven_values"], [23])
        event = training_events(
            [
                dict(row, facts=deepcopy(row["facts"]))
                for row in rows
            ],
            [{"last_seen_ms": 0, "values": {"skill_points": 600}, "evidence": "before.png"}],
        )[0]
        self.assertEqual(event["deltas"]["skill_points"], 25)
        self.assertNotIn("skill_points", event["conflicting_readings"])
        self.assertEqual(
            event["gain_phase_candidates"]["skill_points"]["source_resolution"][
                "ignored_unproven_values"
            ],
            [23],
        )

    def test_source_backed_third_value_keeps_phase_ambiguous(self):
        rows = [
            source_row(1000, 25),
            source_row(1033, 2),
            source_row(1066, 23),
            source_row(1099, 25),
        ]
        self.assertIsNone(
            resolve_source_temporal_phase(
                source_gain_observations(rows, "skill_points"), "skill_points"
            )
        )

    def test_conflicting_canonical_amount_is_rejected(self):
        rows = [source_row(1000, 25), source_row(1033, 25), source_row(1066, 2)]
        for row in rows:
            candidate = row["facts"]["training_gain_crop_provenance"]["skill_points"][
                "canonical_candidate"
            ]
            candidate["amount"] = 999
            row["facts"]["training_gain_crop_provenance"]["skill_points"][
                "candidate_amounts"
            ] = [999]
        self.assertIsNone(
            resolve_source_temporal_phase(
                source_gain_observations(rows, "skill_points"), "skill_points"
            )
        )

    def test_canonical_region_must_match_requested_field(self):
        rows = [source_row(1000, 25), source_row(1033, 25), source_row(1066, 2)]
        for row in rows:
            candidate = row["facts"]["training_gain_crop_provenance"]["skill_points"][
                "canonical_candidate"
            ]
            candidate["region"] = "wide_gain.power"
            candidate["crop_family"] = "wide_gain"
        self.assertIsNone(
            resolve_source_temporal_phase(
                source_gain_observations(rows, "skill_points"), "skill_points"
            )
        )

    def test_unallowlisted_crop_basis_is_rejected(self):
        rows = [source_row(1000, 25), source_row(1033, 25), source_row(1066, 2)]
        for row in rows:
            row["facts"]["training_gain_crop_provenance"]["skill_points"][
                "canonical_basis"
            ] = "source_crop_unrecognized_geometry"
        self.assertIsNone(
            resolve_source_temporal_phase(
                source_gain_observations(rows, "skill_points"), "skill_points"
            )
        )

    def test_source_phase_requires_evidence_identity(self):
        rows = [source_row(1000, 25), source_row(1033, 25), source_row(1066, 2)]
        for row in rows:
            row.pop("evidence")
        self.assertIsNone(
            resolve_source_temporal_phase(
                source_gain_observations(rows, "skill_points"), "skill_points"
            )
        )

    def test_duplicate_physical_source_path_cannot_inflate_proof(self):
        rows = [source_row(1000, 25), source_row(1033, 25), source_row(1066, 2)]
        for row in rows:
            row["evidence"] = "same-frame.png"
        self.assertIsNone(
            resolve_source_temporal_phase(
                source_gain_observations(rows, "skill_points"), "skill_points"
            )
        )

    def test_duplicate_source_frame_hash_cannot_inflate_proof(self):
        rows = [source_row(1000, 25), source_row(1033, 25), source_row(1066, 2)]
        for row in rows:
            row["evidence"] = {
                "path": f"copied/{row['source_timestamp_ms']}.png",
                "source_frame_sha256": "a" * 64,
            }
        self.assertIsNone(
            resolve_source_temporal_phase(
                source_gain_observations(rows, "skill_points"), "skill_points"
            )
        )

    def test_ignored_outlier_is_inside_phase_time_bound(self):
        rows = [
            source_row(1000, 25),
            source_row(1033, 25),
            source_row(1066, 2),
            unproven_source_row(5000, 23),
        ]
        self.assertIsNone(
            resolve_source_temporal_phase(
                source_gain_observations(rows, "skill_points"), "skill_points"
            )
        )


if __name__ == "__main__":
    unittest.main()
