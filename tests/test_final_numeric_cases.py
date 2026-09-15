from __future__ import annotations

import copy
import sys
from pathlib import Path
import unittest


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.evaluate_final_numeric_cases import evaluate_report
from tests.test_training_gain_resolution import g7_wit_rows
from tracen_replay.transactions import training_events


RUN = "run-a"
SOURCE = ".local/full-recording/run-a/training-inspection/100/frame-000001.png"
REPORT_SOURCE = "training-inspection/100/frame-000001.png"


def _cause(*, amount=12, field="speed", turn="turn-1", evidence=SOURCE,
           start=100, end=200, event_id="training-frozen-1"):
    return {
        "id": f"{RUN}/{turn}/{field}",
        "recording": RUN,
        "turn_id": turn,
        "field": field,
        "source_amount": amount,
        "event_id": event_id,
        "observation_window_ms": [start, end],
        "source_evidence": [{"path": evidence, "sha256": "a" * 64}],
    }


def _causes(case, *, extra=None):
    rows = [case]
    if extra:
        rows.extend(extra)
    return {
        "schema_version": "tracen-replay/final-numeric-causes-v1",
        "cases": rows,
        "additional_targeted_cases": [],
    }


def _event(*, amount=12, field="speed", turn="turn-1", evidence=REPORT_SOURCE,
           start=100, end=200, event_id="training-report-77",
           basis="observed_training_gain", independent_effect_verification=True,
           conflicts_present=False, conflicting_readings=None,
           gain_prefix_resolutions=None, gain_phase_candidates=None):
    row = {
        "id": event_id,
        "kind": "training",
        "first_seen_ms": start,
        "last_seen_ms": end,
        "evidence": evidence,
        "field_evidence": {field: [evidence]},
        "deltas": {field: amount} if amount is not None else {},
        "basis": basis,
        "independent_effect_verification": independent_effect_verification,
        "conflicts_present": conflicts_present,
    }
    if amount is None:
        # Preserve the canonical event's field identity while making its gain
        # absent; a raw reading must not promote this to a success.
        row["field_evidence"][field] = [evidence]
    if turn is not None:
        row["turn_id"] = turn
    if conflicting_readings is not None:
        row["conflicting_readings"] = {field: conflicting_readings}
    if gain_prefix_resolutions is not None:
        row["gain_prefix_resolutions"] = {field: gain_prefix_resolutions}
    if gain_phase_candidates is not None:
        row["gain_phase_candidates"] = {field: gain_phase_candidates}
    return row


def _phase_candidate():
    """Coherent repeated full/component proof used by evaluator regressions."""
    return {
        "value": 12,
        "shape": ["skill_points", "speed"],
        "component_shapes": {"1": [["speed"]]},
        "observed_values": [1, 12],
        "phase_order": "full_before_component",
        "basis": "repeated_full_gain_before_repeated_component_phase",
        "source_resolution": {
            "accepted_amount": 12,
            "observed_amounts": [1, 12],
            "basis": "repeated_full_gain_before_repeated_component_phase",
            "full_shape": ["skill_points", "speed"],
            "component_shapes": [["speed"]],
            "full_observations": [
                {"source_timestamp_ms": 100, "evidence": REPORT_SOURCE,
                 "value": 12, "shape": ["skill_points", "speed"]},
                {"source_timestamp_ms": 120, "evidence": "full-120.png",
                 "value": 12, "shape": ["skill_points", "speed"]},
            ],
            "component_observations": [
                {"source_timestamp_ms": 150, "evidence": "component-150.png",
                 "value": 1, "shape": ["speed"]},
                {"source_timestamp_ms": 170, "evidence": "component-170.png",
                 "value": 1, "shape": ["speed"]},
            ],
            "full_last_seen_ms": 120,
            "component_first_seen_ms": 150,
            "phase_order": "full_before_component",
        },
        "accepted": True,
    }


def _source_temporal_candidate(*, full_count=1, component_count=2):
    """Coherent source-crop proof for the equal-shape temporal exception."""
    full_observations = [
        {
            "source_timestamp_ms": 100 + index * 33,
            "evidence": REPORT_SOURCE if index == 0 else f"source-full-{index}.png",
            "value": 12,
            "shape": ["speed"],
            "crop_basis": "source_crop_family_geometry",
            "crop_conflict_state": "resolved_same_amount_across_source_crops",
            "crop_region": "wide_gain.speed",
            "crop_candidate_amounts": [12],
        }
        for index in range(full_count)
    ]
    component_observations = [
        {
            "source_timestamp_ms": 150 + index * 33,
            "evidence": f"source-component-{index}.png",
            "value": 1,
            "shape": ["speed"],
            "crop_basis": "source_crop_family_geometry",
            "crop_conflict_state": "resolved_same_amount_across_source_crops",
            "crop_region": "wide_gain.speed",
            "crop_candidate_amounts": [1],
        }
        for index in range(component_count)
    ]
    return {
        "value": 12,
        "shape": ["speed"],
        "component_shapes": {"1": [["speed"]]},
        "observed_values": [1, 12],
        "phase_order": "full_before_component",
        "basis": "source_temporal_full_before_component_phase",
        "source_resolution": {
            "accepted_amount": 12,
            "observed_amounts": [1, 12],
            "basis": "source_temporal_full_before_component_phase",
            "full_shape": ["speed"],
            "component_shapes": [["speed"]],
            "full_observations": full_observations,
            "component_observations": component_observations,
            "full_last_seen_ms": full_observations[-1]["source_timestamp_ms"],
            "component_first_seen_ms": component_observations[0]["source_timestamp_ms"],
            "phase_order": "full_before_component",
            "source_field": "speed",
            "source_full_observation_count": full_count,
            "source_phase_rule": "canonical_source_crop_then_later_component_only",
            "ignored_unproven_observations": [],
            "ignored_unproven_values": [],
        },
        "evidence": [observation["evidence"] for observation in full_observations],
        "accepted": True,
    }


def _prefix_candidate():
    """Coherent repeated complete badge plus transient prefix proof."""
    return {
        "accepted_amount": 12,
        "observed_amounts": [1, 12],
        "complete_observations": [
            {"source_timestamp_ms": 100, "evidence": REPORT_SOURCE, "value": 12},
            {"source_timestamp_ms": 140, "evidence": "full-140.png", "value": 12},
        ],
        "prefix_observations": [
            {"source_timestamp_ms": 120, "evidence": "prefix-120.png", "value": 1},
        ],
        "basis": "repeated_complete_badge_with_brief_prefix_observations",
        "counter_crosscheck_evidence": [REPORT_SOURCE, "full-140.png"],
    }


def _result_group(phase, *, option="speed", interval=(100, 200)):
    resolution = phase.get("source_resolution", {})
    observations = []
    for group_name in ("full_observations", "component_observations"):
        for observation in resolution.get(group_name, []):
            observations.append({
                "source_timestamp_ms": observation["source_timestamp_ms"],
                "evidence": observation["evidence"],
                "training_option": option,
                "screen": "training_result",
            })
    return {
        "interval_ms": list(interval),
        "training_option": option,
        "observations": observations,
    }


def _actual_result_readings(group):
    """Materialize the report-owned readings represented by a result group."""
    return [
        {
            "source_timestamp_ms": member["source_timestamp_ms"],
            "evidence": member["evidence"],
            "screen": member["screen"],
            "training_option": member.get("training_option"),
        }
        for member in group["observations"]
    ]


def _bound_phase_fixture():
    phase = _source_temporal_candidate()
    event = _event(
        conflicting_readings=[1, 12],
        gain_phase_candidates=phase,
    )
    event["training_option"] = "speed"
    event["action_identity_evidence"] = ["option-heading.png"]
    event["result_group"] = _result_group(phase)
    return event, event["result_group"], _actual_result_readings(event["result_group"])


def _source_clipped_fixture():
    rows = g7_wit_rows()
    events = training_events(copy.deepcopy(rows), [])
    event = events[0]
    report = _report(events, readings=copy.deepcopy(rows), turns=[
        {"id": "turn-1", "start_ms": 0, "end_ms": 2_000_000},
    ])
    contribution = report["causal_accounting"]["contributions"][0]
    contribution.update(
        basis="observed_training_gain",
        turn_id="turn-1",
        independent_effect_verification=False,
    )
    source_evidence = [
        {"path": row["evidence"], "sha256": "a" * 64}
        for row in rows
    ]
    case = _cause(
        amount=65,
        field="wit",
        turn="turn-1",
        start=event["first_seen_ms"],
        end=event["last_seen_ms"],
        evidence=source_evidence[0]["path"],
    )
    case["source_evidence"] = source_evidence
    return report, case, event


def _report(events, *, readings=None, turns=None):
    contributions = []
    for index, event in enumerate(events):
        for field, amount in event.get("deltas", {}).items():
            field_evidence = event.get("field_evidence", {}).get(field, [])
            contributions.append({
                "id": f"/gameplay_tracking/events/{index}/deltas/{field}",
                "event_ref": f"/gameplay_tracking/events/{index}",
                "event_id": event.get("id"),
                "field": field,
                "amount": amount,
                "basis": event.get("basis"),
                "evidence": field_evidence,
                "turn_id": event.get("turn_id"),
                "candidate_turn_ids": [],
                "turn_assignment_basis": "synthetic_test",
                "independent_effect_verification": event.get(
                    "independent_effect_verification"
                ),
                "conflicts_present": event.get("conflicts_present", False),
            })
    return {
        "run": RUN,
        "source": {"sha256": "b" * 64},
        "gameplay_tracking": {
            "events": events,
            "readings": readings or [],
            "turn_action_receipts": [],
        },
        "causal_accounting": {"contributions": contributions},
        "turn_ledger": {"turns": turns or []},
    }


class FinalNumericCaseTests(unittest.TestCase):
    def test_event_id_renumber_uses_source_evidence_and_turn_window(self):
        case = _cause(event_id="training-frozen-1")
        event = _event(event_id="training-final-901", turn=None)
        result = evaluate_report(
            _causes(case),
            _report([event], turns=[{"id": "turn-1", "start_ms": 0, "end_ms": 300}]),
        )

        graded = result["cases"][0]
        self.assertEqual(graded["verdict"], "correct_direct")
        candidate = graded["units"][0]["matched_candidates"][0]
        self.assertEqual(candidate["report_event_id"], "training-final-901")
        self.assertEqual(candidate["match_basis"], "source_evidence")
        self.assertTrue(result["matching_rules"]["event_id_policy"].startswith(
            "frozen event ids"
        ))

    def test_wrong_amount_is_reported_after_source_join(self):
        result = evaluate_report(_causes(_cause(amount=12)), _report([_event(amount=11)]))

        graded = result["cases"][0]
        self.assertEqual(graded["verdict"], "wrong_amount")
        self.assertEqual(graded["predicted_amount"], 11)
        self.assertEqual(graded["units"][0]["amount_status"], "wrong")

    def test_missing_field_does_not_become_zero(self):
        result = evaluate_report(_causes(_cause(amount=12)), _report([]))

        graded = result["cases"][0]
        self.assertEqual(graded["verdict"], "missing")
        self.assertIsNone(graded["predicted_amount"])
        self.assertEqual(graded["units"][0]["conflict_state"], ["missing_candidate"])

    def test_duplicate_candidates_are_not_collapsed_by_matching_amount(self):
        result = evaluate_report(
            _causes(_cause()),
            _report([_event(event_id="training-a"), _event(event_id="training-b")]),
        )

        graded = result["cases"][0]
        self.assertEqual(graded["verdict"], "duplicate")
        self.assertEqual(len(graded["units"][0]["matched_candidates"]), 2)
        self.assertEqual(result["counts"]["duplicate"], 1)

    def test_offsetting_candidates_are_reported_as_cancellation(self):
        result = evaluate_report(
            _causes(_cause()),
            _report([_event(amount=12, event_id="training-plus"),
                     _event(amount=-12, event_id="training-minus")]),
        )

        graded = result["cases"][0]
        self.assertEqual(graded["verdict"], "cancellation")
        self.assertIn("cancellation_candidates", graded["conflict_state"])
        self.assertEqual(result["counts"]["cancellation"], 1)

    def test_wrong_turn_is_reported_even_when_amount_matches(self):
        result = evaluate_report(_causes(_cause(turn="turn-1")),
                                 _report([_event(turn="turn-2")]))

        graded = result["cases"][0]
        self.assertEqual(graded["verdict"], "wrong_turn")
        self.assertEqual(graded["predicted_amount"], 12)
        self.assertEqual(graded["units"][0]["ownership_status"], "wrong")

    def test_state_derived_agreement_is_not_direct_pass(self):
        result = evaluate_report(
            _causes(_cause()),
            _report([_event(basis="state_derived")]),
        )

        graded = result["cases"][0]
        self.assertEqual(graded["verdict"], "correct_derived")
        self.assertFalse(graded["passed"])
        self.assertFalse(graded["direct_evidence"])
        self.assertEqual(graded["units"][0]["direct_evidence_basis"], "state_derived")

    def test_multi_contribution_case_sums_assigned_predictions_only(self):
        first = _cause(amount=4, start=100, end=120, event_id="frozen-a")
        second = _cause(amount=5, start=300, end=320, event_id="frozen-b")
        second["id"] = f"{RUN}/turn-1/speed-multi-b"
        second["source_evidence"] = [{
            "path": ".local/full-recording/run-a/training-inspection/300/frame-000001.png",
            "sha256": "c" * 64,
        }]
        case = _cause(amount=None)
        case.pop("source_amount")
        case["id"] = f"{RUN}/turn-1/speed-multi"
        case["contributions"] = [
            {"source_amount": 4, "event_id": "frozen-a",
             "observation_window_ms": [100, 120],
             "source_evidence": first["source_evidence"]},
            {"source_amount": 5, "event_id": "frozen-b",
             "observation_window_ms": [300, 320],
             "source_evidence": second["source_evidence"]},
        ]
        event_a = _event(amount=4, evidence="training-inspection/100/frame-000001.png",
                         event_id="renumbered-a", start=100, end=120)
        event_b = _event(amount=5, evidence="training-inspection/300/frame-000001.png",
                         event_id="renumbered-b", start=300, end=320)
        result = evaluate_report(_causes(case), _report([event_a, event_b]))

        graded = result["cases"][0]
        self.assertEqual(graded["source_amount"], 9)
        self.assertEqual(graded["predicted_amount"], 9)
        self.assertEqual(graded["verdict"], "correct_direct")
        self.assertEqual(result["counts"]["comparisons"], 1)
        self.assertEqual(result["counts"]["contributions"], 2)

    def test_unresolved_correct_amount_with_screenshot_cannot_pass(self):
        event = _event(
            independent_effect_verification=False,
            conflicts_present=True,
            conflicting_readings=[1, 12],
        )
        result = evaluate_report(
            _causes(_cause()),
            _report([event]),
        )

        graded = result["cases"][0]
        self.assertEqual(graded["verdict"], "ambiguous_attribution")
        self.assertFalse(graded["passed"])
        self.assertFalse(graded["direct_evidence"])
        self.assertIn("reported_conflict", graded["conflict_state"])
        self.assertEqual(
            graded["units"][0]["matched_candidates"][0][
                "independent_effect_verification"
            ],
            [False],
        )

    def test_accepted_complete_badge_can_resolve_raw_prefix_alternative(self):
        event = _event(
            conflicting_readings=[1, 12],
            gain_prefix_resolutions={
                "accepted_amount": 12,
                "observed_amounts": [1, 12],
                "complete_observations": [
                    {"source_timestamp_ms": 100, "evidence": REPORT_SOURCE, "value": 12},
                    {"source_timestamp_ms": 140, "evidence": "full-140.png", "value": 12},
                ],
                "prefix_observations": [
                    {"source_timestamp_ms": 120, "evidence": "prefix-120.png", "value": 1},
                ],
                "basis": "repeated_complete_badge_with_brief_prefix_observations",
                "counter_crosscheck_evidence": [REPORT_SOURCE, "full-140.png"],
            },
        )
        result = evaluate_report(_causes(_cause()), _report([event]))

        graded = result["cases"][0]
        self.assertEqual(graded["verdict"], "correct_direct")
        self.assertTrue(graded["passed"])
        candidate = graded["units"][0]["matched_candidates"][0]
        self.assertEqual(candidate["conflict_values"], [1, 12])
        self.assertEqual(candidate["field_resolution_proof"], ["gain_prefix_resolutions"])

    def test_accepted_phase_candidate_requires_complete_component_proof(self):
        phase = {
            "value": 12,
            "shape": ["skill_points", "speed"],
            "component_shapes": {"1": [["speed"]]},
            "observed_values": [1, 12],
            "phase_order": "full_before_component",
            "basis": "repeated_full_gain_before_repeated_component_phase",
            "source_resolution": {
                "accepted_amount": 12,
                "observed_amounts": [1, 12],
                "basis": "repeated_full_gain_before_repeated_component_phase",
                "full_shape": ["skill_points", "speed"],
                "component_shapes": [["speed"]],
                "full_observations": [
                    {"source_timestamp_ms": 100, "evidence": REPORT_SOURCE,
                     "value": 12, "shape": ["skill_points", "speed"]},
                    {"source_timestamp_ms": 120, "evidence": "full-120.png",
                     "value": 12, "shape": ["skill_points", "speed"]},
                ],
                "component_observations": [
                    {"source_timestamp_ms": 150, "evidence": "component-150.png",
                     "value": 1, "shape": ["speed"]},
                    {"source_timestamp_ms": 170, "evidence": "component-170.png",
                     "value": 1, "shape": ["speed"]},
                ],
                "full_last_seen_ms": 120,
                "component_first_seen_ms": 150,
                "phase_order": "full_before_component",
            },
            "accepted": True,
        }
        reading = {
            "source_timestamp_ms": 150,
            "evidence": REPORT_SOURCE,
            "facts": {"training_gains": {"speed": 1}},
        }
        result = evaluate_report(
            _causes(_cause(amount=12)),
            _report([_event(amount=12, gain_phase_candidates=phase)], readings=[reading]),
        )

        graded = result["cases"][0]
        self.assertEqual(graded["verdict"], "correct_direct")
        candidate = graded["units"][0]["matched_candidates"][0]
        self.assertEqual(candidate["field_resolution_proof"], ["gain_phase_candidates"])
        self.assertEqual(candidate["conflict_state"], [])

    def test_phase_candidate_accepted_flag_without_source_proof_stays_ambiguous(self):
        phase = {
            "value": 12,
            "shape": ["speed"],
            "component_shapes": {"1": [["speed"]]},
            "observed_values": [1, 12],
            "accepted": True,
            "source_resolution": {"accepted_amount": 12},
        }
        reading = {
            "source_timestamp_ms": 150,
            "evidence": REPORT_SOURCE,
            "facts": {"training_gains": {"speed": 1}},
        }
        result = evaluate_report(
            _causes(_cause(amount=12)),
            _report([_event(amount=12, gain_phase_candidates=phase)], readings=[reading]),
        )

        graded = result["cases"][0]
        self.assertEqual(graded["verdict"], "ambiguous_attribution")
        self.assertFalse(graded["passed"])
        candidate = graded["units"][0]["matched_candidates"][0]
        self.assertEqual(candidate["field_resolution_proof"], [])
        self.assertIn("reported_conflict", candidate["conflict_state"])

    def test_phase_proof_requires_repeated_distinct_full_and_component_frames(self):
        for group in ("full_observations", "component_observations"):
            phase = copy.deepcopy(_phase_candidate())
            observations = phase["source_resolution"][group]
            observations.pop()
            result = evaluate_report(
                _causes(_cause()),
                _report([_event(
                    conflicts_present=True,
                    conflicting_readings=[1, 12],
                    gain_phase_candidates=phase,
                )]),
            )

            graded = result["cases"][0]
            self.assertEqual(graded["verdict"], "ambiguous_attribution", group)
            self.assertEqual(
                graded["units"][0]["matched_candidates"][0]["field_resolution_proof"],
                [],
                group,
            )

    def test_phase_proof_rejects_reused_frame_and_unlisted_component_amount(self):
        for mutation in (
            lambda phase: phase["source_resolution"]["component_observations"][1].update(
                evidence="component-150.png"
            ),
            lambda phase: phase["source_resolution"]["component_observations"][0].update(
                value=99
            ),
        ):
            phase = copy.deepcopy(_phase_candidate())
            mutation(phase)
            result = evaluate_report(
                _causes(_cause()),
                _report([_event(
                    conflicts_present=True,
                    conflicting_readings=[1, 12],
                    gain_phase_candidates=phase,
                )]),
            )
            graded = result["cases"][0]
            self.assertEqual(graded["verdict"], "ambiguous_attribution")
            self.assertFalse(graded["units"][0]["matched_candidates"][0][
                "field_resolution_proof"
            ])

    def test_phase_proof_rejects_foreign_recording_evidence(self):
        phase = copy.deepcopy(_phase_candidate())
        phase["source_resolution"]["full_observations"][0]["evidence"] = (
            ".local/full-recording/other-run/full-100.png"
        )
        event = _event(
            conflicting_readings=[1, 12],
            gain_phase_candidates=phase,
        )
        event["action_identity_evidence"] = [
            observation["evidence"]
            for group in ("full_observations", "component_observations")
            for observation in phase["source_resolution"][group]
        ]
        result = evaluate_report(
            _causes(_cause()),
            _report([event]),
        )
        self.assertEqual(result["cases"][0]["verdict"], "unobservable")

    def test_prefix_proof_binds_nested_amounts_and_distinct_frames(self):
        for mutation in (
            lambda prefix: prefix["complete_observations"][1].update(
                evidence=REPORT_SOURCE
            ),
            lambda prefix: prefix.update(
                source_resolution={"observed_amounts": [1, 99]}
            ),
            lambda prefix: prefix["complete_observations"][0].update(
                observed_amounts=[99]
            ),
        ):
            prefix = copy.deepcopy(_prefix_candidate())
            mutation(prefix)
            result = evaluate_report(
                _causes(_cause()),
                _report([_event(
                    conflicts_present=True,
                    conflicting_readings=[1, 12],
                    gain_prefix_resolutions=prefix,
                )]),
            )
            graded = result["cases"][0]
            self.assertEqual(graded["verdict"], "ambiguous_attribution")
            self.assertEqual(
                graded["units"][0]["matched_candidates"][0]["field_resolution_proof"],
                [],
            )

    def test_foreign_source_evidence_cannot_fall_back_to_time_join(self):
        event = _event(
            evidence=".local/full-recording/other-run/training-inspection/100/frame-000001.png"
        )
        result = evaluate_report(_causes(_cause()), _report([event]))

        graded = result["cases"][0]
        self.assertEqual(graded["verdict"], "unobservable")
        self.assertEqual(graded["units"][0]["matched_candidates"], [])

    def test_foreign_frozen_source_evidence_cannot_match_local_event(self):
        case = _cause(
            evidence=".local/full-recording/other-run/training-inspection/100/frame-000001.png"
        )
        result = evaluate_report(_causes(case), _report([_event()]))

        graded = result["cases"][0]
        self.assertEqual(graded["verdict"], "unobservable")
        self.assertEqual(graded["units"][0]["matched_candidates"], [])

    def test_phase_proof_does_not_hide_an_unlisted_raw_amount(self):
        phase = {
            "value": 12,
            "shape": ["skill_points", "speed"],
            "component_shapes": {"1": [["speed"]]},
            "observed_values": [1, 12],
            "phase_order": "full_before_component",
            "basis": "repeated_full_gain_before_repeated_component_phase",
            "source_resolution": {
                "accepted_amount": 12,
                "observed_amounts": [1, 12],
                "basis": "repeated_full_gain_before_repeated_component_phase",
                "full_shape": ["skill_points", "speed"],
                "component_shapes": [["speed"]],
                "full_observations": [
                    {"source_timestamp_ms": 100, "evidence": REPORT_SOURCE,
                     "value": 12, "shape": ["skill_points", "speed"]},
                    {"source_timestamp_ms": 120, "evidence": "full-120.png",
                     "value": 12, "shape": ["skill_points", "speed"]},
                ],
                "component_observations": [
                    {"source_timestamp_ms": 150, "evidence": "component-150.png",
                     "value": 1, "shape": ["speed"]},
                    {"source_timestamp_ms": 170, "evidence": "component-170.png",
                     "value": 1, "shape": ["speed"]},
                ],
                "full_last_seen_ms": 120,
                "component_first_seen_ms": 150,
                "phase_order": "full_before_component",
            },
            "accepted": True,
        }
        reading = {
            "source_timestamp_ms": 150,
            "evidence": REPORT_SOURCE,
            "facts": {"training_gains": {"speed": 99}},
        }
        result = evaluate_report(
            _causes(_cause(amount=12)),
            _report([_event(amount=12, gain_phase_candidates=phase)], readings=[reading]),
        )

        graded = result["cases"][0]
        self.assertEqual(graded["verdict"], "ambiguous_attribution")
        candidate = graded["units"][0]["matched_candidates"][0]
        self.assertEqual(candidate["field_resolution_proof"], ["gain_phase_candidates"])
        self.assertIn("reported_conflict", candidate["conflict_state"])

    def test_source_temporal_proof_accepts_one_canonical_full_and_repeated_components(self):
        phase = _source_temporal_candidate()
        event = _event(
            conflicting_readings=[1, 12],
            gain_phase_candidates=phase,
        )
        event["action_identity_evidence"] = [
            observation["evidence"]
            for group in ("full_observations", "component_observations")
            for observation in phase["source_resolution"][group]
        ]
        result = evaluate_report(
            _causes(_cause()),
            _report([event]),
        )

        graded = result["cases"][0]
        self.assertEqual(graded["verdict"], "correct_direct")
        self.assertTrue(graded["passed"])
        self.assertEqual(
            graded["units"][0]["matched_candidates"][0]["field_resolution_proof"],
            ["gain_phase_candidates"],
        )

    def test_source_temporal_accepts_pixel_localized_full_phase_basis(self):
        phase = copy.deepcopy(_source_temporal_candidate())
        for observation in phase["source_resolution"]["full_observations"]:
            observation.update(
                crop_basis="source_crop_pixel_localized_training_badge_agreement",
                crop_conflict_state="resolved_source_pixel_localized",
                crop_region="localized_gain.speed",
                crop_candidate_amounts=[12],
            )
        event = _event(
            conflicting_readings=[1, 12],
            gain_phase_candidates=phase,
        )
        event["action_identity_evidence"] = [
            observation["evidence"]
            for group in ("full_observations", "component_observations")
            for observation in phase["source_resolution"][group]
        ]
        result = evaluate_report(
            _causes(_cause()),
            _report([event]),
        )

        graded = result["cases"][0]
        self.assertEqual(graded["verdict"], "correct_direct")
        self.assertTrue(graded["passed"])

    def test_result_group_binds_phase_proof_when_identity_is_only_option_subset(self):
        phase = _source_temporal_candidate()
        event = _event(
            conflicting_readings=[1, 12],
            gain_phase_candidates=phase,
        )
        event["training_option"] = "speed"
        event["action_identity_evidence"] = ["option-heading.png"]
        event["result_group"] = _result_group(phase)
        result = evaluate_report(
            _causes(_cause()),
            _report([event], readings=_actual_result_readings(event["result_group"])),
        )

        graded = result["cases"][0]
        self.assertEqual(graded["verdict"], "correct_direct")
        self.assertTrue(graded["passed"])

    def test_result_group_rejects_foreign_or_missing_phase_member(self):
        phase = _source_temporal_candidate()
        event = _event(conflicting_readings=[1, 12], gain_phase_candidates=phase)
        event["training_option"] = "speed"
        event["action_identity_evidence"] = ["option-heading.png"]
        group = _result_group(phase)
        group["observations"][-1]["evidence"] = (
            ".local/full-recording/other-run/foreign/frame.png"
        )
        event["result_group"] = group
        result = evaluate_report(
            _causes(_cause()),
            _report([event], readings=_actual_result_readings(event["result_group"])),
        )

        graded = result["cases"][0]
        self.assertEqual(graded["verdict"], "unobservable")
        self.assertEqual(graded["units"][0]["matched_candidates"], [])

    def test_result_group_binds_prefix_proof_without_promoting_identity_frames(self):
        prefix = _prefix_candidate()
        event = _event(gain_prefix_resolutions=prefix)
        event["training_option"] = "speed"
        event["action_identity_evidence"] = ["option-heading.png"]
        event["result_group"] = {
            "interval_ms": [100, 200],
            "training_option": "speed",
            "observations": [
                {
                    "source_timestamp_ms": observation["source_timestamp_ms"],
                    "evidence": observation["evidence"],
                    "training_option": "speed",
                    "screen": "training_result",
                }
                for observation in (
                    prefix["complete_observations"] + prefix["prefix_observations"]
                )
            ],
        }
        result = evaluate_report(
            _causes(_cause()),
            _report([event], readings=_actual_result_readings(event["result_group"])),
        )

        graded = result["cases"][0]
        self.assertEqual(graded["verdict"], "correct_direct")
        self.assertTrue(graded["passed"])

    def test_result_group_requires_report_owned_readings(self):
        event, _group, _readings = _bound_phase_fixture()
        result = evaluate_report(_causes(_cause()), _report([event], readings=[]))

        graded = result["cases"][0]
        self.assertFalse(graded["passed"])
        self.assertNotEqual(graded["verdict"], "correct_direct")
        self.assertEqual(
            graded["units"][0]["matched_candidates"][0]["field_resolution_proof"],
            [],
        )

    def test_result_group_rejects_fabricated_same_recording_path(self):
        event, group, readings = _bound_phase_fixture()
        group["observations"][0]["evidence"] = "training-inspection/100/fabricated.png"
        result = evaluate_report(_causes(_cause()), _report([event], readings=readings))

        graded = result["cases"][0]
        self.assertFalse(graded["passed"])
        self.assertNotEqual(graded["verdict"], "correct_direct")

    def test_result_group_rejects_duplicate_actual_reading_identity(self):
        event, _group, readings = _bound_phase_fixture()
        readings.append(copy.deepcopy(readings[0]))
        result = evaluate_report(_causes(_cause()), _report([event], readings=readings))

        graded = result["cases"][0]
        self.assertFalse(graded["passed"])
        self.assertNotEqual(graded["verdict"], "correct_direct")

    def test_result_group_rejects_actual_reading_with_wrong_option(self):
        event, _group, readings = _bound_phase_fixture()
        readings[0]["training_option"] = "stamina"
        result = evaluate_report(_causes(_cause()), _report([event], readings=readings))

        graded = result["cases"][0]
        self.assertFalse(graded["passed"])
        self.assertNotEqual(graded["verdict"], "correct_direct")

    def test_result_group_rejects_actual_reading_with_wrong_screen(self):
        event, _group, readings = _bound_phase_fixture()
        readings[0]["screen"] = "training_preview"
        result = evaluate_report(_causes(_cause()), _report([event], readings=readings))

        graded = result["cases"][0]
        self.assertFalse(graded["passed"])
        self.assertNotEqual(graded["verdict"], "correct_direct")

    def test_result_group_checks_turn_ids_when_report_exposes_them(self):
        event, group, readings = _bound_phase_fixture()
        event["turn_id"] = "turn-1"
        group["turn_id"] = "turn-1"
        readings[0]["turn_id"] = "turn-2"
        result = evaluate_report(_causes(_cause()), _report([event], readings=readings))

        graded = result["cases"][0]
        self.assertFalse(graded["passed"])
        self.assertNotEqual(graded["verdict"], "correct_direct")

    def test_source_temporal_proof_requires_repeated_physical_support(self):
        phase = _source_temporal_candidate(component_count=1)
        result = evaluate_report(
            _causes(_cause()),
            _report([_event(
                conflicting_readings=[1, 12],
                gain_phase_candidates=phase,
            )]),
        )

        graded = result["cases"][0]
        self.assertEqual(graded["verdict"], "ambiguous_attribution")
        self.assertFalse(graded["units"][0]["matched_candidates"][0][
            "field_resolution_proof"
        ])

    def test_source_temporal_proof_requires_complete_action_evidence_binding(self):
        phase = copy.deepcopy(_source_temporal_candidate())
        action_evidence = [
            observation["evidence"]
            for group in ("full_observations", "component_observations")
            for observation in phase["source_resolution"][group]
        ]
        phase["source_resolution"]["component_observations"][1][
            "evidence"
        ] = "unrelated/component.png"
        event = _event(
            conflicting_readings=[1, 12],
            gain_phase_candidates=phase,
        )
        event["action_identity_evidence"] = action_evidence
        result = evaluate_report(_causes(_cause()), _report([event]))

        graded = result["cases"][0]
        self.assertEqual(graded["verdict"], "ambiguous_attribution")
        self.assertFalse(graded["units"][0]["matched_candidates"][0][
            "field_resolution_proof"
        ])

    def test_source_temporal_proof_requires_canonical_crop_count_and_identity(self):
        for mutation in (
            lambda phase: phase["source_resolution"].update(
                source_full_observation_count=2
            ),
            lambda phase: phase["source_resolution"]["full_observations"][0].update(
                crop_region=None
            ),
            lambda phase: phase["source_resolution"]["component_observations"][0].update(
                crop_candidate_amounts=[99]
            ),
        ):
            phase = copy.deepcopy(_source_temporal_candidate())
            mutation(phase)
            result = evaluate_report(
                _causes(_cause()),
                _report([_event(
                    conflicting_readings=[1, 12],
                    gain_phase_candidates=phase,
                )]),
            )
            graded = result["cases"][0]
            self.assertEqual(graded["verdict"], "ambiguous_attribution")
            self.assertEqual(
                graded["units"][0]["matched_candidates"][0][
                    "field_resolution_proof"
                ],
                [],
            )

    def test_source_temporal_rejects_source_backed_ignored_third_value(self):
        phase = copy.deepcopy(_source_temporal_candidate(full_count=2, component_count=1))
        resolution = phase["source_resolution"]
        resolution["source_phase_rule"] = (
            "canonical_source_pair_with_single_unproven_outlier_diagnostic"
        )
        resolution["observed_amounts"] = [1, 12, 23]
        phase["observed_values"] = [1, 12, 23]
        resolution["ignored_unproven_values"] = [23]
        resolution["ignored_unproven_observations"] = [{
            "source_timestamp_ms": 166,
            "evidence": "source-third.png",
            "value": 23,
            "shape": ["speed"],
            "crop_basis": "source_crop_family_geometry",
            "crop_conflict_state": "resolved_same_amount_across_source_crops",
            "crop_region": "wide_gain.speed",
            "crop_candidate_amounts": [23],
        }]
        result = evaluate_report(
            _causes(_cause()),
            _report([_event(
                conflicting_readings=[1, 12, 23],
                gain_phase_candidates=phase,
            )]),
        )

        graded = result["cases"][0]
        self.assertEqual(graded["verdict"], "ambiguous_attribution")
        self.assertFalse(graded["units"][0]["matched_candidates"][0][
            "field_resolution_proof"
        ])

    def test_transient_prefix_phase_proof_is_checked_by_its_stable_suffix(self):
        phase = {
            "value": 12,
            "shape": ["skill_points", "speed", "power"],
            "component_shapes": {"1": [["skill_points", "speed"]]},
            "observed_values": [1, 12],
            "phase_order": "component_prefix_before_stable_full_suffix",
            "basis": "repeated_full_gain_prefix_phase_with_stable_suffix",
            "source_resolution": {
                "accepted_amount": 12,
                "observed_amounts": [1, 12],
                "basis": "repeated_full_gain_prefix_phase_with_stable_suffix",
                "full_shape": ["skill_points", "speed", "power"],
                "component_shapes": [["skill_points", "speed"]],
                "full_observations": [
                    {"source_timestamp_ms": 100, "evidence": REPORT_SOURCE,
                     "value": 12, "shape": ["skill_points", "speed"]},
                    {"source_timestamp_ms": 120, "evidence": "full-120.png",
                     "value": 12, "shape": ["skill_points", "speed"]},
                    {"source_timestamp_ms": 180, "evidence": "full-180.png",
                     "value": 12, "shape": ["skill_points", "speed", "power"]},
                    {"source_timestamp_ms": 220, "evidence": "full-220.png",
                     "value": 12, "shape": ["skill_points", "speed", "power"]},
                ],
                "component_observations": [
                    {"source_timestamp_ms": 140, "evidence": "component-140.png",
                     "value": 1, "shape": ["skill_points", "speed"]},
                    {"source_timestamp_ms": 160, "evidence": "component-160.png",
                     "value": 1, "shape": ["skill_points", "speed"]},
                ],
                "full_last_seen_ms": 220,
                "component_first_seen_ms": 140,
                "phase_order": "component_prefix_before_stable_full_suffix",
            },
            "accepted": True,
        }
        reading = {
            "source_timestamp_ms": 140,
            "evidence": REPORT_SOURCE,
            "facts": {"training_gains": {"speed": 1}},
        }
        result = evaluate_report(
            _causes(_cause(amount=12)),
            _report([_event(amount=12, gain_phase_candidates=phase)], readings=[reading]),
        )

        self.assertEqual(result["cases"][0]["verdict"], "correct_direct")

    def test_frozen_source_comparison_can_verify_production_false_flag(self):
        event = _event(independent_effect_verification=False)
        result = evaluate_report(_causes(_cause()), _report([event]))

        graded = result["cases"][0]
        self.assertEqual(graded["verdict"], "correct_direct")
        self.assertTrue(graded["passed"])
        candidate = graded["units"][0]["matched_candidates"][0]
        self.assertEqual(candidate["independent_effect_verification"], [False])
        self.assertEqual(candidate["source_comparison"]["status"], "matched")
        self.assertEqual(candidate["source_comparison"]["basis"], "source_evidence")

    def test_source_clipped_gain_resolves_raw_prefix_from_report_owned_crops(self):
        report, case, _event_row = _source_clipped_fixture()
        result = evaluate_report(_causes(case), report)

        graded = result["cases"][0]
        self.assertEqual(graded["verdict"], "correct_direct")
        self.assertTrue(graded["passed"])
        candidate = graded["units"][0]["matched_candidates"][0]
        self.assertEqual(candidate["predicted_amount"], 65)
        self.assertEqual(candidate["observed_amounts"], [6, 65])
        self.assertEqual(candidate["field_resolution_proof"], [
            "source_clipped_gain_resolutions",
        ])
        self.assertEqual(candidate["conflict_state"], [])

    def test_source_clipped_gain_rejects_tampered_amount_evidence_phase_or_proof(self):
        mutations = (
            lambda event: event["deltas"].update(wit=64),
            lambda event: event["direct_gain_provenance"]["wit"][
                "observations"
            ][0].update(evidence="tampered/frame.png"),
            lambda event: event["source_clipped_gain_resolutions"]["wit"].update(
                phase_key="wit:1:2"
            ),
            lambda event: event["result_group"]["observations"][0].update(
                evidence="tampered/group.png"
            ),
        )
        for mutate in mutations:
            report, case, event = _source_clipped_fixture()
            mutate(event)
            result = evaluate_report(_causes(case), report)
            graded = result["cases"][0]
            self.assertNotEqual(graded["verdict"], "correct_direct")
            self.assertFalse(graded["passed"])

    def test_missing_production_verification_metadata_stays_unobservable(self):
        event = _event(independent_effect_verification=None)
        result = evaluate_report(_causes(_cause()), _report([event]))

        graded = result["cases"][0]
        self.assertEqual(graded["verdict"], "unobservable")
        self.assertFalse(graded["passed"])
        self.assertEqual(graded["units"][0]["direct_evidence_basis"],
                         "independent_effect_unverified")

    def test_conflicting_production_verification_flags_stay_unobservable(self):
        report = _report([_event(independent_effect_verification=True)])
        report["causal_accounting"]["contributions"][0][
            "independent_effect_verification"
        ] = False

        result = evaluate_report(_causes(_cause()), report)

        graded = result["cases"][0]
        self.assertEqual(graded["verdict"], "unobservable")
        self.assertFalse(graded["passed"])
        candidate = graded["units"][0]["matched_candidates"][0]
        self.assertEqual(candidate["independent_effect_verification"], [True, False])

    def test_raw_gain_does_not_promote_missing_canonical_delta(self):
        case = _cause(amount=12)
        event = _event(amount=None)
        reading = {
            "source_timestamp_ms": 150,
            "evidence": REPORT_SOURCE,
            "facts": {"training_gains": {"speed": 12}},
        }
        result = evaluate_report(_causes(case), _report([event], readings=[reading]))

        graded = result["cases"][0]
        self.assertEqual(graded["verdict"], "missing")
        self.assertIsNone(graded["predicted_amount"])
        self.assertFalse(graded["passed"])

    @staticmethod
    def _composure_causes():
        return {
            "schema_version": "tracen-replay/final-numeric-causes-v1",
            "cases": [_cause()],
            "additional_targeted_cases": [{
                "id": "run-a/composure-current-plus-projection/1000",
                "recording": RUN,
                "kind": "performance_panel_current_plus_projection",
                "timestamp_ms": 1000,
                "source_amounts": {"current_composure": 56,
                                    "projected_composure_gain": 19},
                "source_evidence": {"sha256": "d" * 64},
            }],
        }

    def test_composure_current_and_projection_are_separate_fields(self):
        readings = [
            {"source_timestamp_ms": 1000, "evidence": "boundary/frame-current.png",
             "facts": {"performance_points": {"composure": 56}}},
            {"source_timestamp_ms": 1033, "evidence": "boundary/frame-preview.png",
             "facts": {"projected_performance_gains": {"composure": 19}}},
        ]
        result = evaluate_report(self._composure_causes(),
                                 _report([_event()], readings=readings))

        composure = result["composure"]
        self.assertEqual(composure["verdict"], "correct_direct")
        self.assertTrue(composure["not_combined"])
        self.assertEqual(composure["current"]["actual"], 56)
        self.assertEqual(composure["projected"]["actual"], 19)
        self.assertNotIn(75, {composure["current"]["actual"],
                               composure["projected"]["actual"]})

    def test_merged_composure_ocr_stays_ambiguous_and_is_not_split(self):
        readings = [{
            "source_timestamp_ms": 1000,
            "evidence": "boundary/frame-merged.png",
            "ocr": {"neural": [{"text": "56+19", "confidence": 85.6,
                                  "box": [1, 2, 3, 4]}]},
            "facts": {"performance_points": {"dance": 21}},
        }]
        result = evaluate_report(self._composure_causes(),
                                 _report([_event()], readings=readings))

        composure = result["composure"]
        self.assertEqual(composure["verdict"], "ambiguous")
        self.assertEqual(composure["current"]["status"], "missing")
        self.assertEqual(composure["projected"]["status"], "missing")
        self.assertEqual(composure["merged_ocr"][0]["text"], "56+19")
        self.assertIsNone(composure["current"]["actual"])
        self.assertIsNone(composure["projected"]["actual"])


if __name__ == "__main__":
    unittest.main()
