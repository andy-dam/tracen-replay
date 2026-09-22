import unittest

from tracen_replay.training_gain_phases import (
    resolve_full_component_phase,
    stable_trailing_result_counter_suffix,
    stable_trailing_result_suffix,
)
from tracen_replay.transactions import training_actions, training_events
from tracen_replay.training_identity import read_identity


def row(timestamp, *, value=None, shape=("speed", "wit"), evidence=None):
    facts = {"observed_training_gain_fields": list(shape)}
    if value is not None:
        facts["training_gains"] = {"skill_points": value}
    return {
        "source_timestamp_ms": timestamp,
        "evidence": evidence or f"{timestamp}.png",
        "facts": facts,
    }


def result_row(timestamp, gains, option="wit", *, result_values=None):
    facts = {
        "training_gains": dict(gains),
        "training_gain_candidates": {field: [amount] for field, amount in gains.items()},
    }
    if result_values is not None:
        facts["result_values"] = dict(result_values)
    return dict(
        source_timestamp_ms=timestamp,
        evidence=f"{timestamp}.png",
        screen="training_result",
        training_option=option,
        facts=facts,
        effects=[],
        stats={},
    )


def before_state(skill_points=606):
    return dict(
        first_seen_ms=0,
        last_seen_ms=0,
        values=dict(speed=100, stamina=100, power=100, guts=100, wit=100, skill_points=skill_points),
        evidence="before.png",
    )


def identity_facts(name, option="Wit"):
    lines = [
        dict(text=f"{option} Lvl 1", confidence=99, box=[232, 171, 304, 192]),
        dict(text=name, confidence=99, box=[224, 197, 317, 234]),
    ]
    return read_identity(lines, "training_result", option)


class TrainingGainPhaseHelperTests(unittest.TestCase):
    def test_full_phase_is_selected_by_shape_and_time(self):
        observations = [
            (row(1000, value=11, shape=("skill_points", "speed", "wit")), 11),
            (row(1033, value=11, shape=("skill_points", "speed", "wit")), 11),
            (row(1066, value=1, shape=("skill_points", "speed")), 1),
            (row(1099, value=1, shape=("skill_points", "speed")), 1),
        ]
        result = resolve_full_component_phase(observations)
        self.assertEqual(result["accepted_amount"], 11)
        self.assertEqual(result["phase_order"], "full_before_component")
        self.assertEqual(result["full_shape"], ["skill_points", "speed", "wit"])
        self.assertEqual(result["component_shapes"], [["skill_points", "speed"]])

    def test_transient_prefix_then_stable_multishape_suffix_is_source_resolved(self):
        observations = [
            (row(1000, value=21, shape=("skill_points", "speed", "wit")), 21),
            (row(1033, value=21, shape=("skill_points", "speed", "wit")), 21),
            (row(1066, value=2, shape=("skill_points", "speed", "wit")), 2),
            (row(1099, value=21, shape=("speed", "wit")), 21),
            (row(1132, value=21, shape=("speed", "wit")), 21),
            (row(1165, value=21, shape=("skill_points", "speed", "wit")), 21),
        ]
        result = resolve_full_component_phase(observations)
        self.assertEqual(result["accepted_amount"], 21)
        self.assertEqual(
            result["basis"],
            "repeated_full_gain_prefix_phase_with_stable_suffix",
        )
        self.assertEqual(result["phase_order"], "component_prefix_before_stable_full_suffix")

    def test_transient_prefix_fallback_requires_shape_change(self):
        observations = [
            (row(1000, value=4, shape=("guts", "skill_points", "stamina")), 4),
            (row(1033, value=44, shape=("guts", "skill_points", "stamina")), 44),
            (row(1066, value=44, shape=("guts", "stamina")), 44),
            (row(1099, value=44, shape=("guts", "stamina")), 44),
        ]
        result = resolve_full_component_phase(observations)
        self.assertEqual(result["accepted_amount"], 44)

        no_shape_change = [
            (row(1000, value=4, shape=("speed", "wit")), 4),
            (row(1033, value=44, shape=("speed", "wit")), 44),
            (row(1066, value=44, shape=("speed", "wit")), 44),
            (row(1099, value=44, shape=("speed", "wit")), 44),
        ]
        self.assertIsNone(resolve_full_component_phase(no_shape_change))

    def test_phase_does_not_choose_equal_shape_larger_value(self):
        observations = [
            (row(1000, value=11), 11),
            (row(1033, value=11), 11),
            (row(1066, value=14), 14),
            (row(1099, value=14), 14),
        ]
        self.assertIsNone(resolve_full_component_phase(observations))

    def test_duplicate_timestamp_does_not_count_as_repetition(self):
        observations = [
            (row(1000, value=11), 11),
            (row(1000, value=11, evidence="other-view.png"), 11),
            (row(1066, value=1), 1),
            (row(1099, value=1), 1),
        ]
        self.assertIsNone(resolve_full_component_phase(observations))

    def test_stable_suffix_uses_last_total_without_expected_amount(self):
        rows = [
            {"source_timestamp_ms": 1000, "evidence": "a.png", "facts": {"result_values": {"speed": 108}}},
            {"source_timestamp_ms": 1033, "evidence": "b.png", "facts": {"result_values": {"speed": 108}}},
            {"source_timestamp_ms": 1066, "evidence": "c.png", "facts": {"result_values": {"speed": 109}}},
            {"source_timestamp_ms": 1100, "evidence": "d.png", "facts": {"result_values": {"speed": 109}}},
            {"source_timestamp_ms": 1133, "evidence": "e.png", "facts": {"result_values": {"speed": 109}}},
        ]
        result = stable_trailing_result_suffix(rows, "speed")
        self.assertEqual(result["value"], 109)
        self.assertEqual(result["observation_count"], 3)

    def test_same_timestamp_rows_do_not_claim_three_frame_proof(self):
        rows = [
            {"source_timestamp_ms": 1000, "evidence": "a.png", "facts": {"result_values": {"speed": 109}}},
            {"source_timestamp_ms": 1000, "evidence": "a.png", "facts": {"result_values": {"speed": 109}}},
            {"source_timestamp_ms": 1033, "evidence": "b.png", "facts": {"result_values": {"speed": 109}}},
        ]
        self.assertIsNone(stable_trailing_result_suffix(rows, "speed"))

    def test_repeated_source_hash_does_not_claim_three_frame_proof(self):
        rows = [
            {
                "source_timestamp_ms": timestamp,
                "evidence": {
                    "path": f"alias-{index}.png",
                    "source_frame_sha256": "same-frame",
                },
                "facts": {"result_values": {"speed": 109}},
            }
            for index, timestamp in enumerate((1000, 1033, 1066))
        ]
        self.assertIsNone(stable_trailing_result_suffix(rows, "speed"))

    def test_counter_suffix_is_selected_before_any_claimed_amount(self):
        rows = [
            {
                "source_timestamp_ms": timestamp,
                "evidence": f"counter-{index}.png",
                "facts": {
                    "result_numerator_candidates": {"speed": [value]},
                },
            }
            for index, (timestamp, value) in enumerate(
                ((1000, 108), (1033, 108), (1066, 109), (1100, 109), (1133, 109))
            )
        ]
        result = stable_trailing_result_counter_suffix(rows, "speed")
        self.assertEqual(result["value"], 109)
        self.assertEqual(result["first_seen_ms"], 1066)


class TrainingGainPhaseTransactionTests(unittest.TestCase):
    def test_committed_result_identity_is_carried_to_event_and_action(self):
        first = result_row(1000, {}, option="wit")
        first["facts"].update(identity_facts("Studying"))
        second = result_row(1033, {}, option="wit")
        second["facts"].update(identity_facts("Studying"))

        event = training_events([first, second])[0]
        self.assertEqual(event["training_name"], "Studying")
        self.assertEqual(event["training_name_evidence"], ["1000.png", "1033.png"])
        action = training_actions([event])[0]
        self.assertEqual(action["training_name"], "Studying")
        self.assertEqual(action["training_name_evidence"], ["1000.png", "1033.png"])

    def test_conflicting_committed_result_identity_stays_explicit(self):
        first = result_row(1000, {}, option="wit")
        first["facts"].update(identity_facts("Studying"))
        second = result_row(1033, {}, option="wit")
        second["facts"].update(identity_facts("Other"))

        event = training_events([first, second])[0]
        self.assertNotIn("training_name", event)
        self.assertEqual(event["training_name_conflicts"], ["Other", "Studying"])
        action = training_actions([event])[0]
        self.assertNotIn("training_name", action)
        self.assertEqual(action["training_name_conflicts"], ["Other", "Studying"])

    def test_unknown_option_does_not_count_as_observed_action_identity(self):
        rows = [result_row(t, {"speed": 8}, option=None) for t in (1000, 1033)]
        event = training_events(rows)[0]
        self.assertIsNone(event["training_option"])
        self.assertEqual(event["action_identity_evidence"], [])
        self.assertEqual(event["action_identity_observations"], 0)
        self.assertEqual(event["deltas"]["speed"], 8)

    def test_duplicate_direct_views_do_not_count_as_repeated_gain(self):
        first = result_row(1000, {"speed": 8})
        duplicate = result_row(1000, {"speed": 8})
        duplicate["evidence"] = "wide-1000.png"
        event = training_events([first, duplicate])[0]
        self.assertNotIn("speed", event["deltas"])

    def test_repeated_full_result_promotes_unique_component_phase(self):
        rows = [
            result_row(1000, {"speed": 8, "wit": 21, "skill_points": 11}),
            result_row(1033, {"speed": 8, "wit": 21, "skill_points": 11}),
            result_row(1066, {"speed": 8, "skill_points": 1}, option=None),
            result_row(1099, {"speed": 8, "skill_points": 1}),
        ]
        event = training_events(rows, [before_state()])[0]
        self.assertEqual(event["deltas"]["skill_points"], 11)
        candidate = event["gain_phase_candidates"]["skill_points"]
        self.assertEqual(candidate["basis"], "repeated_full_gain_shape_strictly_contains_all_component_shapes")
        self.assertTrue(event["direct_gain_provenance"]["skill_points"]["observations"])

    def test_transient_prefix_phase_promotes_direct_gain_with_checkpoint(self):
        rows = []
        for timestamp, gains, shape in (
            (1000, {"speed": 8, "wit": 21, "skill_points": 13},
             ("skill_points", "speed", "wit")),
            (1033, {"speed": 8, "wit": 21, "skill_points": 13},
             ("skill_points", "speed", "wit")),
            (1066, {"speed": 8, "wit": 2, "skill_points": 1},
             ("skill_points", "speed", "wit")),
            (1099, {"speed": 8, "wit": 21, "skill_points": 13},
             ("speed", "wit")),
            (1132, {"speed": 8, "wit": 21, "skill_points": 13},
             ("speed", "wit")),
        ):
            item = result_row(timestamp, gains)
            item["facts"]["observed_training_gain_fields"] = list(shape)
            rows.append(item)
        event = training_events(rows, [before_state()])[0]
        self.assertEqual(event["deltas"]["wit"], 21)
        self.assertEqual(event["deltas"]["skill_points"], 13)
        self.assertEqual(
            event["gain_phase_candidates"]["wit"]["basis"],
            "repeated_full_gain_prefix_phase_with_stable_suffix",
        )
        self.assertEqual(
            event["direct_gain_provenance"]["wit"]["basis"],
            "training_gain_full_phase",
        )

    def test_equal_shape_alternatives_remain_ambiguous_even_when_larger(self):
        rows = [
            result_row(1000, {"speed": 8, "wit": 21, "skill_points": 11}),
            result_row(1033, {"speed": 8, "wit": 21, "skill_points": 11}),
            result_row(1066, {"speed": 8, "wit": 21, "skill_points": 14}),
            result_row(1099, {"speed": 8, "wit": 21, "skill_points": 14}),
        ]
        event = training_events(rows, [before_state()])[0]
        self.assertNotIn("skill_points", event["deltas"])
        self.assertEqual(event["conflicting_readings"]["skill_points"], [11, 14])
        self.assertNotIn("gain_phase_candidates", event)

    def test_two_full_gain_candidates_stay_ambiguous_with_a_component_present(self):
        rows = [
            result_row(1000, {"speed": 8, "wit": 21, "skill_points": 11}),
            result_row(1033, {"speed": 8, "wit": 21, "skill_points": 11}),
            result_row(1066, {"speed": 8, "skill_points": 1}, option=None),
            result_row(1099, {"speed": 8, "wit": 21, "skill_points": 14}),
            result_row(1132, {"speed": 8, "wit": 21, "skill_points": 14}),
        ]
        event = training_events(rows, [before_state()])[0]
        self.assertNotIn("skill_points", event["deltas"])
        self.assertEqual(event["conflicting_readings"]["skill_points"], [1, 11, 14])
        self.assertNotIn("gain_phase_candidates", event)

    def test_single_component_observation_is_not_enough_phase_evidence(self):
        rows = [
            result_row(1000, {"speed": 8, "wit": 21, "skill_points": 11}),
            result_row(1033, {"speed": 8, "wit": 21, "skill_points": 11}),
            result_row(1066, {"speed": 8, "skill_points": 1}, option=None),
        ]
        event = training_events(rows)[0]
        self.assertNotIn("skill_points", event["deltas"])
        self.assertEqual(event["conflicting_readings"]["skill_points"], [1, 11])
        self.assertNotIn("gain_phase_candidates", event)

    def test_reversed_full_and_component_phases_remain_ambiguous(self):
        rows = [
            result_row(1000, {"speed": 8, "skill_points": 1}, option=None),
            result_row(1033, {"speed": 8, "skill_points": 1}, option=None),
            result_row(1066, {"speed": 8, "wit": 21, "skill_points": 11}),
            result_row(1099, {"speed": 8, "wit": 21, "skill_points": 11}),
        ]
        event = training_events(rows)[0]
        self.assertNotIn("skill_points", event["deltas"])
        self.assertEqual(event["conflicting_readings"]["skill_points"], [1, 11])
        self.assertNotIn("gain_phase_candidates", event)

    def test_state_crosscheck_preserves_direct_gain_proof_when_amount_agrees(self):
        rows = [
            result_row(1000, {"speed": 8}, result_values={"speed": 108}),
            result_row(1033, {"speed": 8}, result_values={"speed": 108}),
            result_row(1066, {"speed": 8}, result_values={"speed": 108}),
        ]
        event = training_events(rows, [before_state()])[0]
        self.assertEqual(event["deltas"]["speed"], 8)
        self.assertNotIn("speed", event.get("result_state_derived_fields", []))
        self.assertEqual(event["direct_gain_provenance"]["speed"]["value"], 8)
        self.assertEqual(event["state_crosschecks"]["speed"]["status"], "agrees_with_direct")

    def test_state_only_result_suffix_is_explicitly_derived(self):
        rows = [
            result_row(1000, {}, result_values={"speed": 108}),
            result_row(1033, {}, result_values={"speed": 108}),
            result_row(1066, {}, result_values={"speed": 108}),
        ]
        event = training_events(rows, [before_state()])[0]
        self.assertEqual(event["deltas"]["speed"], 8)
        self.assertEqual(event["result_state_derived_fields"], ["speed"])
        self.assertEqual(event["state_derived_provenance"]["speed"]["recomputed_amount"], 8)

    def test_state_suffix_does_not_replace_a_wrong_direct_amount(self):
        rows = [
            result_row(1000, {"speed": 8}, result_values={"speed": 109}),
            result_row(1033, {"speed": 8}, result_values={"speed": 109}),
            result_row(1066, {"speed": 8}, result_values={"speed": 109}),
        ]
        event = training_events(rows, [before_state()])[0]
        self.assertEqual(event["deltas"]["speed"], 8)
        self.assertNotIn("speed", event.get("result_state_derived_fields", []))
        self.assertEqual(event["state_crosschecks"]["speed"]["status"], "disagrees_with_direct")

    def test_intermediate_result_does_not_select_candidate_amount(self):
        rows = [
            result_row(1000, {}, result_values={"speed": 108}),
            result_row(1033, {}, result_values={"speed": 108}),
            result_row(1066, {}, result_values={"speed": 109}),
            result_row(1100, {}, result_values={"speed": 109}),
            result_row(1133, {}, result_values={"speed": 109}),
        ]
        event = training_events(rows, [before_state()])[0]
        self.assertEqual(event["deltas"]["speed"], 9)
        self.assertEqual(event["state_derived_provenance"]["speed"]["after"]["value"], 109)


if __name__ == "__main__":
    unittest.main()
