import math
import unittest

from tracen_replay.transactions import training_events
from tracen_replay.training_gain_phases import (
    resolve_full_component_phase,
    resolve_source_temporal_phase,
    source_gain_observations,
)
from tracen_replay.training_gain_resolution import resolve_candidate_only_gain


def _candidate(field, amount, *, confidence=99.0):
    return {
        "region": f"gain.{field}",
        "crop_family": "gain",
        "raw_text": f"+{amount}",
        "confidence": confidence,
        "box": [498, 950, 610, 1008],
        "source_role": "amount_crop_candidate",
        "input_eligible": True,
        "canonical_eligible": True,
        "amount": amount,
    }


def _result_row(timestamp, amount, *, evidence=None, option="wit", candidate=None):
    if evidence is None:
        evidence = f"frame-{timestamp}.png"
    facts = {
        "training_gains": {"wit": amount},
        "training_gain_crop_provenance": {
            "wit": {"candidates": [candidate] if candidate is not None else []},
        },
        "observed_training_gain_fields": ["wit"],
    }
    return {
        "screen": "training_result",
        "training_option": option,
        "stats": {"training_preview": False},
        "source_timestamp_ms": timestamp,
        "evidence": evidence,
        "facts": facts,
        "effects": [],
    }


def _candidate_row(timestamp, evidence, amount=6):
    return {
        "screen": "training_result",
        "training_option": "wit",
        "stats": {"training_preview": False},
        "source_timestamp_ms": timestamp,
        "evidence": evidence,
        "facts": {
            "training_gain_crop_provenance": {
                "wit": {"candidates": [_candidate("wit", amount)]},
            },
        },
    }


class TrainingGainNormalValidationTests(unittest.TestCase):
    def test_event_group_skips_negative_timestamp_before_direct_repetition(self):
        event = training_events(
            [_result_row(-1, 25), _result_row(33, 25)],
        )[0]

        self.assertEqual(event["first_seen_ms"], 33)
        self.assertNotIn("wit", event["deltas"])

    def test_event_group_skips_string_timestamp_before_arithmetic(self):
        events = training_events(
            [_result_row("33", 25), _result_row(66, 25)],
        )

        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["first_seen_ms"], 66)
        self.assertNotIn("wit", events[0]["deltas"])

    def test_malformed_nan_crop_blocks_legacy_prefix_fallback(self):
        rows = [
            _result_row(0, 2, candidate=_candidate("wit", 2, confidence=math.nan)),
            _result_row(33, 21, candidate=_candidate("wit", 21)),
            _result_row(66, 21, candidate=_candidate("wit", 21)),
        ]

        event = training_events(rows)[0]

        self.assertNotIn("wit", event["deltas"])
        self.assertEqual(event["conflicting_readings"]["wit"], [2, 21])
        self.assertEqual(
            event["source_clipped_gain_resolutions"]["wit"]["reason"],
            "invalid_source_confidence",
        )

    def test_candidate_recovery_rejects_normalized_path_repeated_at_new_timestamp(self):
        rows = [
            _candidate_row(100, "capture/./frame.png"),
            _candidate_row(133, "capture\\frame.png"),
            _candidate_row(166, "capture/other.png"),
        ]

        result = resolve_candidate_only_gain(rows, "wit", phase_key="wit:100:166")

        self.assertEqual(result["status"], "unresolved")
        self.assertEqual(result["reason"], "insufficient_same_phase_corroboration")

    def test_prefix_rejects_normalized_path_repeated_at_new_timestamp(self):
        observations = [
            ({"source_timestamp_ms": 100, "evidence": "capture/./frame.png"}, 2),
            ({"source_timestamp_ms": 133, "evidence": "capture\\frame.png"}, 21),
            ({"source_timestamp_ms": 166, "evidence": "capture/other.png"}, 21),
        ]

        from tracen_replay.training_gain_resolution import resolve_prefix

        self.assertIsNone(resolve_prefix(observations))

    def test_component_phase_rejects_negative_timestamp_and_duplicate_path(self):
        rows = [
            _result_row(-1, 25, evidence="capture/frame.png"),
            _result_row(33, 25, evidence="capture/frame.png"),
            _result_row(66, 2, evidence="capture/component.png"),
        ]
        observations = source_gain_observations(rows, "wit")

        self.assertIsNone(resolve_source_temporal_phase(observations, "wit"))
        self.assertIsNone(resolve_full_component_phase(observations))

    def test_direct_fallback_rejects_normalized_path_repeated_at_new_timestamp(self):
        rows = [
            _result_row(100, 25, evidence="capture/./frame.png"),
            _result_row(133, 25, evidence="capture\\frame.png"),
        ]

        events = training_events(rows)

        self.assertEqual(len(events), 1)
        self.assertNotIn("wit", events[0]["deltas"])
        self.assertEqual(events[0]["conflicting_readings"]["wit"], [25])


if __name__ == "__main__":
    unittest.main()
