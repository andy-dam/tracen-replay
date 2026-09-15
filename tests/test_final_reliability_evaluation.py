import copy
import hashlib
from pathlib import Path
import unittest
from unittest.mock import patch

from scripts.evaluate_final_reliability import (
    SOURCE_SCHEMA,
    adapt_report,
    evaluate_case,
    evaluate_document,
    validate_selection,
    validate_source_reference,
)
from scripts.build_final_failure_taxonomy import _payload_summary
from tests.test_causal_accounting import fixture


SOURCE = "a" * 64
IMAGE = "b" * 64


def observation(identity, *, amount=10, key=None, turn="turn-001", start=20,
                end=None, category="effect", phase="applied", field="speed",
                status="observed", value_marker=True):
    if end is None:
        end = start
    row = {
        "id": identity,
        "category": category,
        "phase": phase,
        "start_ms": start,
        "end_ms": end,
        "evidence": [f"frame-{start}.png"],
        "payload": {"kind": "stat_change", "field": field, "amount": amount},
        "expected_turn_id": turn,
        "status": status,
    }
    if key is not None:
        row["occurrence_key"] = key
    if not value_marker:
        row["payload"].pop("amount")
    return row


def source_document(*observations_, complete=True, scope=(0, 100), fields=None,
                    turn="turn-001", case_id="run-turn-001"):
    if fields is None:
        fields = ["stat_change", "speed"]
    return {
        "schema_version": SOURCE_SCHEMA,
        "run": "run",
        "source_sha256": SOURCE,
        "image_sha256": IMAGE,
        "cases": [{
            "case_id": case_id,
            "turn_id": turn,
            "scope_ms": list(scope),
            "reference_complete": complete,
            "review_coverage": {"categories": ["effect"], "fields": fields,
                                 "intervals_ms": [list(scope)]},
            "observations": list(observations_),
            "unobservable_intervals": [],
            "notes": [],
        }],
    }


def prediction(*observations_):
    rows = []
    for row in observations_:
        row = copy.deepcopy(row)
        if "expected_turn_id" in row:
            row["turn_id"] = row["expected_turn_id"]
        rows.append(row)
    return {"source_sha256": SOURCE, "auxiliary_log_used": False,
            "observations": rows}


def checkpoint_ownership_report(*, derived_closing):
    """Build a small report exposing one shared or independent state endpoint."""
    values = {field: 10 for field in
              ("speed", "stamina", "power", "guts", "wit", "skill_points")}
    checkpoints = [
        {"id": "checkpoint-001", "first_seen_ms": 1000,
         "last_seen_ms": 1000, "values": copy.deepcopy(values),
         "evidence": "state-001.png"},
    ]
    if not derived_closing:
        checkpoints.append(
            {"id": "checkpoint-002", "first_seen_ms": 2000,
             "last_seen_ms": 2000, "values": dict(values, speed=11),
             "evidence": "state-002.png"})
    shared_ref = "/gameplay_tracking/checkpoints/0"
    second_ref = "/gameplay_tracking/checkpoints/1"
    first_closing = {
        "source_ref": shared_ref if derived_closing else shared_ref,
        "values": copy.deepcopy(values),
        "observed_at_ms": 1000,
    }
    second_opening = {
        "source_ref": shared_ref if derived_closing else second_ref,
        "values": copy.deepcopy(values) if derived_closing else dict(values, speed=11),
        "observed_at_ms": 1000 if derived_closing else 2000,
    }
    first_state = {
        "opening": None,
        "closing": first_closing,
        "closing_basis": (
            "next_turn_first_observed_state"
            if derived_closing else "last_observed_state_not_inferred_completion"
        ),
    }
    second_state = {"opening": second_opening, "closing": None}
    return {
        "source": {"sha256": SOURCE, "duration_ms": 3000},
        "gameplay_tracking": {
            "auxiliary_log_used": False,
            "readings": [
                {"source_timestamp_ms": 1000, "evidence": "state-001.png"},
                {"source_timestamp_ms": 2000, "evidence": "state-002.png"},
            ],
            "events": [], "turn_action_receipts": [],
            "checkpoints": checkpoints,
            "intervals": [],
            "performance_accounting": {"checkpoints": [], "intervals": []},
        },
        "turn_ledger": {
            "turns": [
                {"id": "turn-001", "start_ms": 0, "end_ms": 1500,
                 "timeline_refs": [], "comparisons": {"stats": [], "performance": []},
                 "states": {"stats": first_state}},
                {"id": "turn-002", "start_ms": 1500, "end_ms": 3000,
                 "timeline_refs": [], "comparisons": {"stats": [], "performance": []},
                 "states": {"stats": second_state}},
            ],
            "timeline": [],
        },
    }


class FinalReliabilityEvaluationTests(unittest.TestCase):
    def test_scoped_correctness_reports_outcome_not_just_evaluation_scope(self):
        source = source_document(observation("s", amount=10, key="award"))
        for actual, expected in (
            (prediction(observation("p", amount=10, key="award")), True),
            (prediction(observation("p", amount=12, key="award")), False),
            (prediction(), False),
        ):
            with self.subTest(actual=actual):
                result = evaluate_document(source, actual)
                self.assertEqual(result["scoped_readable_correctness"], expected)
                self.assertEqual(result["scoped_readable_correctness"],
                                 result["observed_scope_passed"])
                self.assertFalse(result["complete_event_history"])
                self.assertFalse(result["full_recording_recall_measured"])

    def test_energy_capacity_alias_is_symmetric_and_preserves_source(self):
        payloads = ({"kind": "max_energy_change", "amount": 4},
                    {"kind": "stat_cap_change", "field": "energy", "amount": 4})
        for expected, actual in (payloads, tuple(reversed(payloads))):
            with self.subTest(expected=expected):
                source_row = observation("s")
                source_row["payload"] = copy.deepcopy(expected)
                actual_row = observation("p")
                actual_row["payload"] = copy.deepcopy(actual)
                source = source_document(source_row, fields=["energy", "stat_cap_change"])
                before = copy.deepcopy(source)
                result = evaluate_document(source, prediction(actual_row))
                self.assertEqual(result["status_counts"], {"correct": 1})
                self.assertEqual(source, before)

    def test_capacity_alias_does_not_hide_wrong_amount_or_recovered_energy(self):
        source_row = observation("s")
        source_row["payload"] = {"kind": "max_energy_change", "amount": 4}
        source = source_document(source_row, fields=["energy", "stat_cap_change", "energy_change"])
        for actual in ({"kind": "stat_cap_change", "field": "energy", "amount": 5},
                       {"kind": "energy_change", "amount": 4}):
            with self.subTest(actual=actual):
                row = observation("p")
                row["payload"] = actual
                result = evaluate_document(source, prediction(row))
                self.assertEqual(result["status_counts"].get("correct", 0), 0)
                self.assertFalse(result["passed"])

    def test_worker_evidence_hash_map_is_preserved(self):
        source = source_document(observation("s"))
        source["image_sha256"] = {"frame-20.png": IMAGE}
        normalized = validate_source_reference(source)
        self.assertEqual(normalized["image_sha256"], {"frame-20.png": IMAGE})

    def test_source_schema_and_selection_are_checked_before_grading(self):
        source = source_document(observation("s"))
        selected = {"schema_version": "final-reliability-source-selection-v1",
                    "cases": [{"id": "run-turn-001", "run": "run",
                               "turn_id": "turn-001", "start_ms": 0,
                               "end_ms": 100, "source_sha256": SOURCE}]}
        normalized = validate_source_reference(source, validate_selection(selected))
        self.assertEqual(normalized["source_sha256"], SOURCE)
        source["cases"][0]["scope_ms"] = [1, 100]
        with self.assertRaisesRegex(ValueError, "selected turn scope"):
            validate_source_reference(source, validate_selection(selected))

    def test_freeze_manifest_binds_the_reference_path_that_is_actually_graded(self):
        root = Path.cwd()
        selection = root / ".local" / "final-reliability-v1" / "source-case-selection.json"
        reference_a = root / "scripts" / "evaluate_final_reliability.py"
        reference_b = root / "tests" / "test_final_reliability_evaluation.py"
        reference_a_hash = hashlib.sha256(reference_a.read_bytes()).hexdigest()
        manifest = {
            "selection_path": str(selection.resolve()),
            "reference_paths": [str(reference_a.resolve())],
            "immutable_files_sha256": {str(reference_a.resolve()): reference_a_hash},
        }
        with patch("scripts.freeze_final_reliability.verify_manifest", return_value=manifest):
            with self.assertRaisesRegex(ValueError, "not bound by the freeze manifest"):
                evaluate_document(source_document(observation("s")),
                                  prediction(observation("p")),
                                  reference_path=reference_b,
                                  freeze_manifest_path=root / "manifest.json")

    def test_matching_is_structural_and_wrong_amount_is_reported(self):
        source = source_document(observation("s", amount=10, key="award"))
        actual = observation("p", amount=12, key="award")
        result = evaluate_document(source, prediction(actual))
        case = result["cases"][0]
        self.assertEqual(case["status_counts"], {"incorrect": 1})
        self.assertEqual(case["results"][0]["prediction_id"], "p")
        self.assertFalse(result["passed"])

    def test_offsetting_plus_and_minus_errors_do_not_pass_on_net_zero(self):
        source = source_document(
            observation("s1", amount=10, key="award-1", start=20),
            observation("s2", amount=-10, key="award-2", start=30),
        )
        actual = prediction(
            observation("p1", amount=15, key="award-1", start=20),
            observation("p2", amount=-15, key="award-2", start=30),
        )
        result = evaluate_document(source, actual)
        self.assertEqual(result["cases"][0]["status_counts"], {"incorrect": 2})
        self.assertFalse(result["passed"])

    def test_unknown_and_zero_are_not_interchangeable(self):
        source = source_document(observation("s", amount=0, key="zero"))
        unknown = observation("p", key="zero")
        unknown["payload"]["amount"] = "unknown"
        score = evaluate_document(source, prediction(unknown))
        self.assertEqual(score["cases"][0]["status_counts"], {"partial": 1})
        zero = observation("p", amount=0, key="zero")
        self.assertTrue(evaluate_document(source, prediction(zero))["passed"])

    def test_wrong_turn_is_detected_even_when_payload_amount_matches(self):
        source = source_document(observation("s", amount=10, key="award"))
        actual = observation("p", amount=10, key="award", turn="turn-002")
        result = evaluate_document(source, prediction(actual))
        case = result["cases"][0]
        self.assertEqual(case["results"][0]["status"], "correct")
        self.assertEqual(case["results"][0]["turn_status"], "incorrect")
        self.assertEqual(len(case["wrong_turn_effects"]), 1)
        self.assertFalse(result["passed"])

    def test_effect_moved_outside_case_scope_is_still_reported_as_wrong_turn(self):
        source = source_document(observation("s", amount=10, key="award"))
        actual = observation("p", amount=10, key="award", turn="turn-002", start=120)
        result = evaluate_document(source, prediction(actual))
        case = result["cases"][0]
        self.assertEqual(case["status_counts"], {"missed": 1})
        self.assertEqual(case["wrong_turn_effects"][0]["match_basis"],
                         "structural_identity_outside_case_scope")

    def test_preview_and_applied_are_distinct_and_duplicate_transactions_fail(self):
        source = source_document(observation("s", amount=10, key="tx-1"))
        preview = observation("preview", amount=10, key="tx-1", phase="preview")
        result = evaluate_document(source, prediction(preview))
        case = result["cases"][0]
        self.assertEqual(case["status_counts"], {"missed": 1})
        self.assertFalse(result["passed"])

        applied = observation("applied", amount=10, key="tx-1")
        duplicate = observation("duplicate", amount=10, key="tx-1")
        result = evaluate_document(source, prediction(applied, duplicate))
        self.assertEqual(result["cases"][0]["duplicate_transactions"], [["applied", "duplicate"]])
        self.assertFalse(result["passed"])

        first = observation("first", amount=10)
        second = observation("second", amount=11)
        first["transaction_id"] = second["transaction_id"] = "same-receipt"
        result = evaluate_document(source_document(first), prediction(first, second))
        self.assertEqual(result["cases"][0]["duplicate_transactions"], [["first", "second"]])

        first = observation("stat", amount=10, field="speed")
        second = observation("cap", amount=20, field="stamina")
        first["transaction_id"] = second["transaction_id"] = "same-receipt"
        source = source_document(first, second)
        result = evaluate_document(source, prediction(first, second))
        self.assertEqual(result["cases"][0]["duplicate_transactions"], [])

    def test_out_of_coverage_extra_is_ungraded_but_core_extra_is_false_positive(self):
        source = source_document(observation("s", amount=10, key="award"), fields=["speed"])
        out_of_scope = observation("out", amount=3, field="stamina", key=None, start=40)
        result = evaluate_document(source, prediction(observation("p", amount=10, key="award"), out_of_scope))
        case = result["cases"][0]
        self.assertEqual(case["unmatched_predictions"][0]["status"], "ungraded")
        self.assertEqual(case["gradeable_extra_predictions"], [])
        self.assertTrue(result["passed"])

        outside_a = observation("outside-a", field="stamina", key="outside-tx", start=40)
        outside_b = observation("outside-b", field="stamina", key="outside-tx", start=41)
        result = evaluate_document(source, prediction(observation("p", amount=10, key="award"),
                                                       outside_a, outside_b))
        case = result["cases"][0]
        self.assertEqual(case["duplicate_transactions"], [])
        self.assertEqual(case["ungraded_duplicate_transactions"][0]["prediction_ids"],
                         ["outside-a", "outside-b"])

        in_scope = observation("extra", amount=3, field="speed", start=40)
        result = evaluate_document(source, prediction(observation("p", amount=10, key="award"), in_scope))
        self.assertEqual(result["cases"][0]["gradeable_extra_predictions"], ["extra"])
        self.assertFalse(result["passed"])

        source = source_document(observation("s"))
        source["cases"][0]["unobservable_intervals"] = [[40, 60]]
        extra = observation("hidden-extra", field="speed", start=45)
        result = evaluate_document(source, prediction(observation("p"), extra))
        self.assertEqual(result["cases"][0]["unmatched_predictions"][0]["reason"],
                         "unobservable_interval")
        self.assertTrue(result["passed"])

    def test_broad_unknown_interval_does_not_excuse_duplicate_of_readable_effect(self):
        source = source_document(observation("source", key=None))
        source["cases"][0]["observations"][0]["cause_id"] = "readable-source"
        source["cases"][0]["unobservable_intervals"] = [[20, 30]]
        matched = observation("matched", key=None)
        matched["cause_id"] = "readable-source"
        duplicate = observation("duplicate", key=None)
        duplicate["cause_id"] = "different-cause"

        result = evaluate_document(source, prediction(matched, duplicate))

        case = result["cases"][0]
        self.assertEqual(case["results"][0]["status"], "correct")
        self.assertEqual(case["unmatched_predictions"][0]["status"], "extra")
        self.assertEqual(case["gradeable_extra_predictions"], ["duplicate"])
        self.assertFalse(result["passed"])

    def test_different_effect_in_unknown_interval_remains_ungraded(self):
        source = source_document(observation("source", field="speed"), fields=["speed", "stamina"])
        source["cases"][0]["unobservable_intervals"] = [[20, 30]]
        hidden = observation("hidden", field="stamina")

        result = evaluate_document(source, prediction(observation("matched"), hidden))

        case = result["cases"][0]
        self.assertEqual(case["unmatched_predictions"][0]["prediction_id"], "hidden")
        self.assertEqual(case["unmatched_predictions"][0]["status"], "ungraded")
        self.assertEqual(case["unmatched_predictions"][0]["reason"], "unobservable_interval")
        self.assertEqual(case["gradeable_extra_predictions"], [])
        self.assertTrue(result["passed"])

    def test_hidden_field_does_not_excuse_known_field_duplicate(self):
        source = source_document(
            observation("known-speed", field="speed"),
            observation("hidden-stamina", field="stamina", status="unobservable"),
            fields=["speed", "stamina"],
        )
        source["cases"][0]["unobservable_intervals"] = [[20, 30]]
        matched = observation("matched-speed", field="speed", key=None)
        matched["cause_id"] = "speed-source"
        source["cases"][0]["observations"][0]["cause_id"] = "speed-source"
        duplicate = observation("duplicate-speed", field="speed", key=None)
        duplicate["cause_id"] = "other-source"
        hidden = observation("hidden-stamina", field="stamina")

        result = evaluate_document(source, prediction(matched, duplicate, hidden))

        case = result["cases"][0]
        self.assertIn("duplicate-speed", case["gradeable_extra_predictions"])
        statuses = {row["prediction_id"]: row["status"] for row in case["unmatched_predictions"]}
        self.assertEqual(statuses["hidden-stamina"], "ungraded")
        self.assertFalse(result["passed"])

    def test_incomplete_reference_never_claims_precision_and_ambiguous_source_is_retained(self):
        source = source_document(observation("s", status="ambiguous"), complete=False)
        result = evaluate_document(source, prediction(observation("p")))
        case = result["cases"][0]
        self.assertEqual(case["results"][0]["status"], "ambiguous")
        self.assertEqual(case["unmatched_predictions"][0]["status"], "ungraded")
        self.assertFalse(result["passed"])
        self.assertFalse(result["complete_event_history"])

    def test_explicit_unobservable_source_is_not_reported_as_parser_attribution_failure(self):
        source = source_document(observation("s", status="unobservable"))
        result = evaluate_document(source, prediction(observation("p")))
        case = result["cases"][0]
        self.assertEqual(case["results"][0]["turn_status"], "unobservable")
        self.assertNotIn("unobservable_turn_attribution", case["score_blockers"])
        self.assertFalse(case["observed_scope_passed"])
        self.assertFalse(result["passed"])

    def test_unknown_owner_keeps_source_observation_but_blocks_attribution_claim(self):
        source = source_document(observation("s", turn=None), complete=True)
        result = evaluate_document(source, prediction(observation("p")))
        case = result["cases"][0]
        self.assertEqual(case["results"][0]["turn_status"], "ambiguous")
        self.assertFalse(result["passed"])

    def test_missing_prediction_turn_provenance_is_unobservable(self):
        source = source_document(observation("s"))
        actual = observation("p")
        actual.pop("expected_turn_id")
        actual.pop("status")
        result = evaluate_document(source, prediction(actual))
        self.assertEqual(result["cases"][0]["results"][0]["turn_status"], "unobservable")
        self.assertFalse(result["passed"])

    def test_turn_refs_use_report_provenance_and_do_not_copy_case_turn(self):
        source = source_document(observation("s", amount=10, key="award"))
        actual = observation("p", amount=10, key="award")
        actual.pop("expected_turn_id")
        actual.pop("status")
        report = prediction(actual)
        report["turn_refs"] = {"p": {"turn_id": "turn-002"}}
        adapted = adapt_report(report)
        self.assertEqual(adapted["observations"][0]["actual_turn_id"], "turn-002")
        result = evaluate_document(source, report)
        self.assertEqual(result["cases"][0]["wrong_turn_effects"][0]["actual_turn_id"], "turn-002")

    def test_state_checkpoint_adapter_supplies_canonical_state_kind(self):
        report = prediction({
            "id": "checkpoint",
            "category": "state",
            "phase": "observed",
            "start_ms": 20,
            "end_ms": 20,
            "evidence": ["frame-20.png"],
            "payload": {"channel": "stats", "values": {"speed": 10}},
        })
        adapted = adapt_report(report)
        self.assertEqual(adapted["observations"][0]["payload"]["kind"], "state")

    def test_persisted_state_turn_uses_underlying_reading_provenance(self):
        report = fixture()
        report["source"]["sha256"] = SOURCE
        data = report["gameplay_tracking"]
        data["readings"][0]["turn_id"] = "turn-002"
        data["state_observations"] = [{
            "id": "state-0",
            "source_ref": "/gameplay_tracking/state_observations/0",
            "category": "state",
            "phase": "observed",
            "start_ms": 20,
            "end_ms": 20,
            "evidence": ["receipt.png"],
            "payload": {"kind": "state", "channel": "stats",
                        "values": {"speed": 100}},
            "source_observations": [{
                "source_ref": "/gameplay_tracking/readings/0",
                "evidence": "receipt.png",
                "timestamp_ms": 150,
            }],
            "status": "observed",
        }]

        adapted = adapt_report(report)
        row = next(row for row in adapted["observations"]
                   if row["source_ref"] == "/gameplay_tracking/state_observations/0")
        self.assertEqual(row["actual_turn_id"], "turn-002")
        self.assertEqual(row["candidate_turn_ids"], ["turn-002"])

    def test_derived_next_turn_closing_does_not_make_shared_checkpoint_ambiguous(self):
        report = checkpoint_ownership_report(derived_closing=True)

        adapted = adapt_report(report)

        row = adapted["observations"][0]
        self.assertEqual(row["actual_turn_id"], "turn-002")
        self.assertEqual(row["candidate_turn_ids"], ["turn-002"])
        self.assertEqual(
            report["turn_ledger"]["turns"][0]["states"]["stats"]["closing"]["source_ref"],
            "/gameplay_tracking/checkpoints/0",
        )

    def test_unmatched_derived_closing_does_not_fall_back_to_previous_turn(self):
        report = checkpoint_ownership_report(derived_closing=True)
        report["turn_ledger"]["turns"][1]["states"]["stats"]["opening"] = None

        adapted = adapt_report(report)

        row = adapted["observations"][0]
        self.assertIsNone(row["actual_turn_id"])
        self.assertEqual(row["candidate_turn_ids"], [])

    def test_derived_closing_with_different_following_reference_stays_unowned(self):
        report = checkpoint_ownership_report(derived_closing=True)
        report["turn_ledger"]["turns"][1]["states"]["stats"]["opening"][
            "source_ref"
        ] = "/gameplay_tracking/checkpoints/999"

        adapted = adapt_report(report)

        row = adapted["observations"][0]
        self.assertIsNone(row["actual_turn_id"])
        self.assertEqual(row["candidate_turn_ids"], [])

    def test_independent_closing_checkpoint_keeps_previous_turn_owner(self):
        report = checkpoint_ownership_report(derived_closing=False)

        adapted = adapt_report(report)

        rows = {row["source_ref"]: row for row in adapted["observations"]}
        self.assertEqual(
            rows["/gameplay_tracking/checkpoints/0"]["actual_turn_id"],
            "turn-001",
        )
        self.assertEqual(
            rows["/gameplay_tracking/checkpoints/1"]["actual_turn_id"],
            "turn-002",
        )

    def test_status_value_is_a_scored_field_after_adapter_projection(self):
        source = source_document(observation("status-source", start=150), complete=False,
                                 scope=(0, 300))
        source["source_sha256"] = SOURCE
        source_row = source["cases"][0]["observations"][0]
        source_row["phase"] = "observed"
        source_row["payload"] = {
            "kind": "mood_status", "value": "great",
        }
        report = fixture()
        report["source"]["sha256"] = SOURCE
        report["gameplay_tracking"]["status_observations"] = [{
            "id": "status-0",
            "source_ref": "/gameplay_tracking/status_observations/0",
            "category": "effect",
            "phase": "observed",
            "start_ms": 150,
            "end_ms": 150,
            "evidence": ["receipt.png"],
            "payload": {"kind": "mood_status", "value": "great"},
            "status": "observed",
        }]

        result = evaluate_document(source, report)
        fields = result["cases"][0]["results"][0]["fields"]
        self.assertEqual(next(field for field in fields if field["field"] == "/value")["status"],
                         "correct")

        report["gameplay_tracking"]["status_observations"][0]["payload"]["value"] = "good"
        result = evaluate_document(source, report)
        self.assertEqual(next(field for field in result["cases"][0]["results"][0]["fields"]
                              if field["field"] == "/value")["status"], "incorrect")

    def test_failure_taxonomy_keeps_status_value_in_payload_summary(self):
        self.assertEqual(
            _payload_summary({"kind": "mood_status", "value": "great"}),
            {"kind": "mood_status", "value": "great"},
        )


if __name__ == "__main__":
    unittest.main()
