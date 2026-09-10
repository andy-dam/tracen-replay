import copy
import unittest

from tracen_replay.effect_evaluate import evaluate


SOURCE_SHA = "source"


def inheritance_effect(name="Power", **values):
    effect = {
        "kind": "inheritance_spark",
        "field": None,
        "name": name,
        "amount": None,
        "direction": None,
        "value": None,
        "raw_text": f"{name} spark activated!",
        "confidence": 99,
    }
    effect.update(values)
    return effect


def line(name="Power", top=800):
    return {
        "text": f"{name} spark activated!",
        "confidence": 99,
        "box": [315, top, 555, top + 28],
    }


def row(timestamp, evidence, effects, lines=None):
    if lines is None:
        lines = [line(effects[0]["name"])]
    return {
        "source_timestamp_ms": timestamp,
        "evidence": evidence,
        "screen": "event_outcome",
        "ocr": {"neural": copy.deepcopy(lines)},
        "effects": copy.deepcopy(effects),
    }


def event(effect, proofs, *, first_seen=250, last_seen=750):
    key = f"{effect['kind']}||{effect.get('name', '')}"
    return {
        "id": "outcome-1",
        "first_seen_ms": first_seen,
        "last_seen_ms": last_seen,
        "effects": [effect],
        "field_evidence": {key: list(proofs)},
        "conflicting_readings": [],
    }


def reference(effects, *, timing_basis="event_start", group_start=250, group_end=500,
              start_ms=0, end_ms=1000):
    return {
        "source_sha256": SOURCE_SHA,
        "scope": "inheritance occurrence test",
        "start_ms": start_ms,
        "end_ms": end_ms,
        "sample_interval_ms": 250,
        "reviewed_samples": [
            {"source_timestamp_ms": timestamp}
            for timestamp in range(start_ms, end_ms, 250)
        ],
        "groups": [
            {"start_ms": group_start, "end_ms": group_end, "effects": effects}
        ],
        "reference_complete": True,
        "timing_basis": timing_basis,
    }


def report(events, rows):
    return {
        "source": {"sha256": SOURCE_SHA},
        "gameplay_tracking": {
            "auxiliary_log_used": False,
            "events": events,
            "readings": list(rows.values()),
        },
    }


class InheritanceEffectEvaluationTests(unittest.TestCase):
    def test_source_proven_lower_bound_matches_two_without_mutating_event(self):
        effect = inheritance_effect()
        rows = {"frame.png": row(250, "frame.png", [effect, effect], [line(), line(top=850)])}
        event_value = event(effect, ["frame.png"])
        original = copy.deepcopy(event_value)
        result = evaluate(
            dict(
                reference([copy.deepcopy(effect), copy.deepcopy(effect)]),
                include_inheritance_occurrences=True,
            ),
            report([event_value], rows),
        )

        self.assertEqual(result["expected"], 2)
        self.assertEqual(result["predicted"], 2)
        self.assertEqual(result["matched"], 2)
        self.assertTrue(result["passed"])
        self.assertEqual(event_value, original)
        diagnostics = result["inheritance_occurrence_diagnostics"]
        self.assertEqual([item["occurrence"]["ordinal"] for item in diagnostics], [1, 2])
        self.assertTrue(all(item["occurrence"]["count_complete"] is False for item in diagnostics))
        self.assertTrue(all(item["occurrence"]["witness"]["minimum_observed_count"] == 2 for item in diagnostics))

    def test_lower_bound_does_not_claim_a_third_occurrence(self):
        effect = inheritance_effect()
        rows = {"frame.png": row(250, "frame.png", [effect, effect], [line(), line(top=850)])}
        result = evaluate(
            dict(
                reference([copy.deepcopy(effect) for _ in range(3)]),
                include_inheritance_occurrences=True,
            ),
            report([event(effect, ["frame.png"])], rows),
        )

        self.assertEqual(result["predicted"], 2)
        self.assertEqual(result["matched"], 2)
        self.assertEqual(len(result["missing"]), 1)
        self.assertFalse(result["passed"])
        self.assertTrue(all(item["occurrence"]["count_complete"] is False
                            for item in result["inheritance_occurrence_diagnostics"]))

    def test_repeated_frames_do_not_sum_into_occurrence_units(self):
        effect = inheritance_effect()
        rows = {
            "first.png": row(250, "first.png", [effect, effect], [line(), line(top=850)]),
            "second.png": row(500, "second.png", [effect, effect], [line(top=805), line(top=855)]),
        }
        result = evaluate(
            dict(reference([copy.deepcopy(effect), copy.deepcopy(effect)]),
                 include_inheritance_occurrences=True),
            report([event(effect, list(rows))], rows),
        )

        self.assertEqual(result["predicted"], 2)
        self.assertEqual(result["matched"], 2)
        self.assertEqual(
            {item["occurrence"]["witness"]["source_timestamp_ms"]
             for item in result["inheritance_occurrence_diagnostics"]},
            {250},
        )

    def test_alternate_crops_at_one_timestamp_are_not_unioned(self):
        effect = inheritance_effect()
        rows = {
            "left.png": row(250, "left.png", [effect], [line()]),
            "right.png": row(250, "right.png", [effect], [line(top=850)]),
        }
        result = evaluate(
            dict(reference([copy.deepcopy(effect), copy.deepcopy(effect)]),
                 include_inheritance_occurrences=True),
            report([event(effect, list(rows))], rows),
        )

        self.assertEqual(result["predicted"], 1)
        self.assertEqual(result["matched"], 1)
        self.assertEqual(len(result["missing"]), 1)
        self.assertEqual(len(result["inheritance_occurrence_diagnostics"]), 1)

    def test_forged_cached_count_is_ignored_in_favor_of_rederived_summary(self):
        effect = inheritance_effect()
        rows = {"frame.png": row(250, "frame.png", [effect])}
        event_value = event(effect, ["frame.png"])
        event_value["inheritance_occurrence_evidence"] = {
            "by_key": {
                "inheritance_spark||Power": {
                    "minimum_observed_count": 99,
                    "uncertain": False,
                    "count_complete": False,
                }
            }
        }
        result = evaluate(
            dict(reference([copy.deepcopy(effect)]), include_inheritance_occurrences=True),
            report([event_value], rows),
        )

        self.assertEqual(result["predicted"], 1)
        self.assertEqual(result["inheritance_occurrence_diagnostics"][0]["occurrence"]["minimum_observed_count"], 1)

    def test_first_exact_extra_unit_uses_later_witness_without_backdating(self):
        effect = inheritance_effect()
        rows = {
            "first.png": row(250, "first.png", [effect], [line()]),
            "later.png": row(500, "later.png", [effect, effect], [line(), line(top=850)]),
        }
        result = evaluate(
            dict(
                reference([copy.deepcopy(effect), copy.deepcopy(effect)],
                          timing_basis="first_exact_effect_observation", group_end=300,
                          end_ms=500),
                include_inheritance_occurrences=True,
            ),
            report([event(effect, list(rows), last_seen=600)], rows),
        )

        self.assertEqual(result["predicted"], 1)
        self.assertEqual(result["matched"], 1)
        self.assertEqual(len(result["missing"]), 1)
        self.assertEqual(len(result["inheritance_occurrence_diagnostics"]), 1)

    def test_first_exact_later_unit_survives_when_base_observation_is_before_scope(self):
        effect = inheritance_effect()
        rows = {
            "before.png": row(250, "before.png", [effect], [line()]),
            "inside.png": row(500, "inside.png", [effect, effect], [line(), line(top=850)]),
        }
        result = evaluate(
            dict(
                reference([copy.deepcopy(effect)],
                          timing_basis="first_exact_effect_observation",
                          group_start=500, group_end=750, start_ms=500),
                include_inheritance_occurrences=True,
            ),
            report([event(effect, list(rows), first_seen=250, last_seen=600)], rows),
        )

        self.assertEqual(result["predicted"], 1)
        self.assertEqual(result["matched"], 1)
        self.assertTrue(result["passed"])
        diagnostic = result["inheritance_occurrence_diagnostics"][0]
        self.assertEqual(diagnostic["occurrence"]["ordinal"], 2)
        self.assertEqual(diagnostic["time"], 500)

    def test_already_proven_multiplicity_is_not_recounted_in_a_later_window(self):
        effect = inheritance_effect()
        rows = {
            "before.png": row(250, "before.png", [effect, effect], [line(), line(top=850)]),
            "inside.png": row(500, "inside.png", [effect, effect], [line(), line(top=850)]),
        }
        ref = reference([], timing_basis="first_exact_effect_observation",
                        group_start=500, group_end=750, start_ms=500)
        ref.update(groups=[], include_inheritance_occurrences=True)
        result = evaluate(ref, report([event(effect, list(rows), last_seen=600)], rows))

        # The second snapshot repeats the already-proven lower bound. Moving
        # ordinal 2 into this window would count the same receipt twice.
        self.assertEqual(result["predicted"], 0)
        self.assertEqual(result["matched"], 0)
        self.assertEqual(result["inheritance_occurrence_diagnostics"], [])
        self.assertTrue(result["passed"])

    def test_conflicting_payloads_do_not_expand(self):
        first = inheritance_effect(value=None)
        second = inheritance_effect(value=1)
        rows = {"frame.png": row(250, "frame.png", [first, second], [line()])}
        event_value = event(first, ["frame.png"])
        event_value["effects"] = [first, second]
        result = evaluate(
            dict(reference([copy.deepcopy(first), copy.deepcopy(first)]),
                 include_inheritance_occurrences=True),
            report([event_value], rows),
        )

        self.assertEqual(result["predicted"], 2)
        self.assertEqual(result["matched"], 1)
        self.assertEqual(len(result["extra_predictions"]), 1)

    def test_active_conflict_does_not_expand(self):
        effect = inheritance_effect()
        rows = {"frame.png": row(250, "frame.png", [effect, effect], [line(), line(top=850)])}
        event_value = event(effect, ["frame.png"])
        event_value["conflicting_readings"] = [
            {"field": "inheritance_spark||Power", "reason": "multiple_same_field_lines"}
        ]
        result = evaluate(
            dict(reference([copy.deepcopy(effect)]), include_inheritance_occurrences=True),
            report([event_value], rows),
        )

        self.assertEqual(result["predicted"], 1)
        self.assertEqual(result["matched"], 0)
        self.assertEqual(len(result["extra_predictions"]), 1)

    def test_noninheritance_effect_ignores_occurrence_metadata(self):
        effect = {
            "kind": "stat_change",
            "field": "power",
            "amount": 7,
            "raw_text": "Power went up by 7.",
        }
        source_row = {
            "source_timestamp_ms": 250,
            "evidence": "frame.png",
            "screen": "event_outcome",
            "ocr": {"neural": [{"text": effect["raw_text"], "confidence": 99,
                                  "box": [315, 800, 555, 828]}]},
            "effects": [copy.deepcopy(effect)],
        }
        event_value = event(effect, ["frame.png"])
        event_value["inheritance_occurrence_evidence"] = {"by_key": {"anything": {
            "minimum_observed_count": 99
        }}}
        result = evaluate(
            dict(reference([copy.deepcopy(effect)]), include_inheritance_occurrences=True),
            report([event_value], {"frame.png": source_row}),
        )

        self.assertEqual(result["predicted"], 1)
        self.assertEqual(result["matched"], 1)
        self.assertEqual(result["inheritance_occurrence_diagnostics"], [])

    def test_opt_in_false_and_legacy_reference_outputs_are_identical(self):
        effect = inheritance_effect()
        rows = {"frame.png": row(250, "frame.png", [effect, effect], [line(), line(top=850)])}
        event_value = event(effect, ["frame.png"])
        legacy = evaluate(reference([copy.deepcopy(effect), copy.deepcopy(effect)]),
                          report([event_value], rows))
        explicit_false = evaluate(
            dict(reference([copy.deepcopy(effect), copy.deepcopy(effect)]),
                 include_inheritance_occurrences=False),
            report([event_value], rows),
        )

        self.assertEqual(legacy, explicit_false)
        self.assertNotIn("inheritance_occurrence_diagnostics", legacy)


if __name__ == "__main__":
    unittest.main()
