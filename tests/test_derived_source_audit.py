import copy
import hashlib
import json
from pathlib import Path
import shutil
import unittest
import uuid
from contextlib import contextmanager

from PIL import Image

import scripts.inventory_derived_reliability as audit


PERFORMANCE_FIELDS = audit.PERFORMANCE_FIELDS


def _performance_values(**overrides):
    values = {field: 0 for field in PERFORMANCE_FIELDS}
    values.update(overrides)
    return values


def _balance_fixture(*, stored_cost=10, contribution_amount=-10):
    contribution = {
        "event_ref": "/gameplay_tracking/lesson_purchases/0",
        "event_id": "lesson-0001",
        "amount": contribution_amount,
        "field": "dance",
        "evidence": ["before.png", "after.png"],
    }
    purchase = {
        "id": "lesson-0001",
        "source_timestamp_ms": 100,
        "performance_cost": _performance_values(dance=stored_cost),
        "cost_basis": "observed_debit",
        "name": "Song",
        "requested_name": "Song",
        "receipt_name": "Song",
        "receipt_event_id": "outcome-1",
    }
    evidence_rows = [
        {
            "evidence": "before.png",
            "screen": "lesson_selection",
            "source_timestamp_ms": 90,
            "facts": {"performance_points": _performance_values(dance=30)},
        },
        {
            "evidence": "after.png",
            "screen": "lesson_selection",
            "source_timestamp_ms": 110,
            "facts": {"performance_points": _performance_values(dance=20)},
        },
    ]
    receipts = {
        "outcome-1": {
            "id": "outcome-1",
            "first_seen_ms": 100,
            "effects": [{"kind": "named_acquisition", "name": "Song"}],
        }
    }
    return contribution, purchase, evidence_rows, receipts


def _price_fixture():
    contribution = {
        "event_ref": "/gameplay_tracking/lesson_purchases/0",
        "event_id": "lesson-0001",
        "amount": -5,
        "field": "dance",
        "evidence": ["offer-1.png", "offer-2.png", "request-1.png", "request-2.png"],
    }
    readings = {}
    for timestamp, path in ((10, "offer-1.png"), (20, "offer-2.png")):
        readings[path] = {
            "evidence": path,
            "screen": "lesson_selection",
            "source_timestamp_ms": timestamp,
            "facts": {
                "performance_points": {
                    field: 10 for field in PERFORMANCE_FIELDS
                },
                "lesson_offer_observations": [{
                    "card_index": 0,
                    "title": {"text": "Song", "confidence": 99},
                    "prices": [{"field": "dance", "value": 5, "status": "accepted"}],
                }],
            },
        }
    for timestamp, path in ((30, "request-1.png"), (35, "request-2.png")):
        readings[path] = {
            "evidence": path,
            "screen": "lesson_confirmation",
            "source_timestamp_ms": timestamp,
            "facts": {
                "projected_performance_points": {
                    field: (5 if field == "dance" else 10)
                    for field in PERFORMANCE_FIELDS
                },
                "name_candidates": ["Song"],
            },
        }
    purchase = {
        "id": "lesson-0001",
        "source_timestamp_ms": 40,
        "performance_cost": _performance_values(dance=5),
        "cost_basis": "receipt_request_and_observed_offer_prices",
        "name": "Song",
        "requested_name": "Song",
        "receipt_name": "Song",
        "receipt_event_id": "outcome-1",
        "offer_cost_evidence": {
            "cost": _performance_values(dance=5),
            "total_cost": 5,
            "receipt_name": "Song",
            "initial": {"timestamps_ms": [10, 20], "evidence": ["offer-1.png", "offer-2.png"]},
            "offer": {
                "title": "Song",
                "timestamps_ms": [10, 20],
                "evidence": ["offer-1.png", "offer-2.png"],
            },
            "request": {
                "title": "Song",
                "timestamps_ms": [30, 35],
                "evidence": ["request-1.png", "request-2.png"],
            },
            "fields": {
                field: {
                    "cost": 5 if field == "dance" else 0,
                    "initial_value": 10,
                    "projected_value": 5 if field == "dance" else 10,
                    "offer_price": 5 if field == "dance" else None,
                    "projection_evidence": [
                        {"timestamp_ms": 30, "value": 5 if field == "dance" else 10, "evidence": "request-1.png"},
                        {"timestamp_ms": 35, "value": 5 if field == "dance" else 10, "evidence": "request-2.png"},
                    ],
                    "offer_evidence": ([
                        {"timestamp_ms": 10, "value": 5, "evidence": "offer-1.png", "card_index": 0},
                        {"timestamp_ms": 20, "value": 5, "evidence": "offer-2.png", "card_index": 0},
                    ] if field == "dance" else []),
                }
                for field in PERFORMANCE_FIELDS
            },
        },
    }
    # Zero fields are represented by the same source proof, as in the stored
    # report schema; only the positive field needs a repeated offer price.
    receipts = {
        "outcome-1": {
            "id": "outcome-1",
            "first_seen_ms": 40,
            "effects": [{"kind": "named_acquisition", "name": "Song"}],
        }
    }
    return contribution, purchase, receipts, readings


@contextmanager
def _workspace_temp_dir():
    root = Path.cwd() / ".local" / f"derived-source-audit-{uuid.uuid4().hex}"
    root.mkdir()
    try:
        yield root
    finally:
        shutil.rmtree(root)


def _supplemental_index_fixture(root: Path) -> dict:
    source_sha256 = "video-source"
    source_frame_sha256 = "primary-source-frame"
    for name, color, timestamp in (
        ("main.png", (20, 30, 40), 200),
        ("supplemental.png", (50, 60, 70), 200),
    ):
        image = root / name
        pane = Image.new("RGB", (810, 1080), color)
        pane.save(image, format="PNG")
        (root / f"{image.stem}.v2.json").write_text(
            json.dumps(
                {
                    "source_timestamp_ms": timestamp,
                    "evidence": name,
                    "source_frame_sha256": source_frame_sha256,
                    "source_sha256": source_sha256,
                    "gameplay_sha256": hashlib.sha256(pane.tobytes()).hexdigest(),
                }
            ),
            encoding="utf-8",
        )
    return {
        "source": {"sha256": source_sha256},
        "gameplay_tracking": {
            "readings": [
                {
                    "evidence": "main.png",
                    "source_timestamp_ms": 200,
                    "screen": "training_result",
                    "training_option": "wit",
                    "facts": {
                        "result_values": {"wit": 100},
                        "inspection_merge_provenance": {
                            "result_values": {
                                "wit": {
                                    "origin": "supplemental",
                                    "source_timestamp_ms": 200,
                                    "evidence": "main.png",
                                }
                            }
                        },
                    },
                    "stats": {"values": {"wit": 80}},
                    "effects": [{"kind": "source_fact"}],
                    "supplemental_evidence": "supplemental.png",
                    "supplemental_evidence_provenance": {
                        "supplemental.png": {
                            "physical_image_sha256": hashlib.sha256(
                                (root / "supplemental.png").read_bytes()
                            ).hexdigest()
                        }
                    },
                    "inspection_conflicts": {},
                }
            ]
        },
    }


class DerivedSourceAuditTests(unittest.TestCase):
    def test_supplemental_source_view_is_indexed_without_copying_primary_facts(self):
        with _workspace_temp_dir() as root:
            report = _supplemental_index_fixture(root)

            index, errors, stats = audit._index_report_readings(
                report,
                report_source_sha256=report["source"]["sha256"],
                evidence_root=root,
            )

            self.assertEqual(errors, [])
            self.assertEqual(stats["supplemental_indexed_count"], 1)
            supplemental = index["supplemental.png"]
            self.assertEqual(supplemental["_source_view"], "supplemental_bound")
            self.assertNotIn("facts", supplemental)
            self.assertNotIn("stats", supplemental)
            self.assertNotIn("screen", supplemental)
            self.assertEqual(supplemental["source_timestamp_ms"], 200)
            binding = supplemental["_supplemental_binding"]
            self.assertEqual(binding["physical_validation"], "validated_sidecar_and_image")
            self.assertTrue(binding["source_identity_match"])
            self.assertEqual(
                binding["physical_image_sha256"],
                hashlib.sha256((root / "supplemental.png").read_bytes()).hexdigest(),
            )
            self.assertEqual(
                binding["gameplay_sha256"], binding["sidecar_gameplay_sha256"]
            )

    def test_missing_report_source_digest_rejects_supplemental_view(self):
        with _workspace_temp_dir() as root:
            report = _supplemental_index_fixture(root)
            report["source"].pop("sha256")

            index, errors, stats = audit._index_report_readings(
                report,
                report_source_sha256=None,
                evidence_root=root,
            )

            self.assertNotIn("supplemental.png", index)
            self.assertEqual(stats["supplemental_rejected_count"], 1)
            self.assertEqual(
                errors[0]["reason"], "report_source_sha256_missing_or_invalid"
            )

    def test_primary_claimed_frame_hash_cannot_authorize_forged_supplement(self):
        with _workspace_temp_dir() as root:
            report = _supplemental_index_fixture(root)
            report["gameplay_tracking"]["readings"][0][
                "source_frame_sha256"
            ] = "forged-primary-frame"
            sidecar = root / "supplemental.v2.json"
            data = json.loads(sidecar.read_text(encoding="utf-8"))
            data["source_frame_sha256"] = "forged-primary-frame"
            sidecar.write_text(json.dumps(data), encoding="utf-8")

            index, errors, stats = audit._index_report_readings(
                report,
                report_source_sha256=report["source"]["sha256"],
                evidence_root=root,
            )

            self.assertNotIn("supplemental.png", index)
            self.assertEqual(stats["supplemental_rejected_count"], 1)
            self.assertEqual(
                errors[0]["reason"], "supplemental_source_identity_mismatch"
            )

    def test_changed_image_with_unchanged_sidecar_gameplay_hash_is_rejected(self):
        with _workspace_temp_dir() as root:
            report = _supplemental_index_fixture(root)
            report["gameplay_tracking"]["readings"][0][
                "supplemental_evidence_provenance"
            ].pop("supplemental.png")
            Image.new("RGB", (810, 1080), (80, 90, 100)).save(
                root / "supplemental.png", format="PNG"
            )

            index, errors, stats = audit._index_report_readings(
                report,
                report_source_sha256=report["source"]["sha256"],
                evidence_root=root,
            )

            self.assertNotIn("supplemental.png", index)
            self.assertEqual(stats["supplemental_rejected_count"], 1)
            self.assertEqual(
                errors[0]["reason"], "supplemental_gameplay_hash_mismatch"
            )

    def test_missing_sidecar_gameplay_hash_is_rejected(self):
        with _workspace_temp_dir() as root:
            report = _supplemental_index_fixture(root)
            sidecar = root / "supplemental.v2.json"
            data = json.loads(sidecar.read_text(encoding="utf-8"))
            data.pop("gameplay_sha256")
            sidecar.write_text(json.dumps(data), encoding="utf-8")

            index, errors, stats = audit._index_report_readings(
                report,
                report_source_sha256=report["source"]["sha256"],
                evidence_root=root,
            )

            self.assertNotIn("supplemental.png", index)
            self.assertEqual(stats["supplemental_rejected_count"], 1)
            self.assertEqual(
                errors[0]["reason"], "supplemental_sidecar_gameplay_hash_missing"
            )

    def test_supplemental_source_view_missing_image_is_rejected(self):
        with _workspace_temp_dir() as root:
            report = _supplemental_index_fixture(root)
            (root / "supplemental.png").unlink()

            index, errors, stats = audit._index_report_readings(
                report,
                report_source_sha256=report["source"]["sha256"],
                evidence_root=root,
            )

            self.assertNotIn("supplemental.png", index)
            self.assertEqual(stats["supplemental_rejected_count"], 1)
            self.assertEqual(errors[0]["reason"], "supplemental_image_missing")

    def test_supplemental_source_view_wrong_source_hash_is_rejected(self):
        with _workspace_temp_dir() as root:
            report = _supplemental_index_fixture(root)
            sidecar = root / "supplemental.v2.json"
            data = json.loads(sidecar.read_text(encoding="utf-8"))
            data["source_frame_sha256"] = "different-source-frame"
            sidecar.write_text(json.dumps(data), encoding="utf-8")

            index, errors, stats = audit._index_report_readings(
                report,
                report_source_sha256=report["source"]["sha256"],
                evidence_root=root,
            )

            self.assertNotIn("supplemental.png", index)
            self.assertEqual(stats["supplemental_rejected_count"], 1)
            self.assertEqual(errors[0]["reason"], "supplemental_source_identity_mismatch")

    def test_supplemental_source_view_wrong_physical_hash_is_rejected(self):
        with _workspace_temp_dir() as root:
            report = _supplemental_index_fixture(root)
            (root / "supplemental.png").write_bytes(b"different-image")

            index, errors, stats = audit._index_report_readings(
                report,
                report_source_sha256=report["source"]["sha256"],
                evidence_root=root,
            )

            self.assertNotIn("supplemental.png", index)
            self.assertEqual(stats["supplemental_rejected_count"], 1)
            self.assertEqual(errors[0]["reason"], "supplemental_physical_hash_mismatch")

    def test_supplemental_source_view_wrong_timestamp_is_rejected(self):
        with _workspace_temp_dir() as root:
            report = _supplemental_index_fixture(root)
            sidecar = root / "supplemental.v2.json"
            data = json.loads(sidecar.read_text(encoding="utf-8"))
            data["source_timestamp_ms"] = 201
            sidecar.write_text(json.dumps(data), encoding="utf-8")

            index, errors, stats = audit._index_report_readings(
                report,
                report_source_sha256=report["source"]["sha256"],
                evidence_root=root,
            )

            self.assertNotIn("supplemental.png", index)
            self.assertEqual(stats["supplemental_rejected_count"], 1)
            self.assertEqual(errors[0]["reason"], "supplemental_sidecar_timestamp_mismatch")

    def test_supplemental_source_view_merge_provenance_timestamp_is_checked(self):
        with _workspace_temp_dir() as root:
            report = _supplemental_index_fixture(root)
            provenance = report["gameplay_tracking"]["readings"][0]["facts"][
                "inspection_merge_provenance"
            ]
            provenance["result_values"]["wit"]["source_timestamp_ms"] = 201

            index, errors, stats = audit._index_report_readings(
                report,
                report_source_sha256=report["source"]["sha256"],
                evidence_root=root,
            )

            self.assertNotIn("supplemental.png", index)
            self.assertEqual(stats["supplemental_rejected_count"], 1)
            self.assertEqual(
                errors[0]["reason"], "main_merge_provenance_timestamp_mismatch"
            )

    def test_amount_independent_suffix_rejects_intermediate_matching_total(self):
        rows = [
            (index, {"source_timestamp_ms": timestamp, "evidence": f"f{index}.png",
                     "facts": {"result_values": {"speed": value}}})
            for index, (timestamp, value) in enumerate(
                ((100, 10), (120, 12), (160, 12), (200, 12), (240, 14), (280, 14), (320, 14))
            )
        ]

        suffix = audit._stable_result_suffix(rows, "speed")

        self.assertEqual(
            [row[1]["facts"]["result_values"]["speed"] for row in suffix],
            [14, 14, 14],
        )
        self.assertEqual(suffix[0][1]["source_timestamp_ms"], 240)
        self.assertEqual(
            audit._legacy_after_matches(rows, "speed", 12)[0][1]["source_timestamp_ms"],
            120,
        )

    def test_amount_independent_suffix_uses_conflicting_final_stable_total(self):
        rows = [
            (index, {"source_timestamp_ms": timestamp, "evidence": f"f{index}.png",
                     "facts": {"result_values": {"speed": value}}})
            for index, (timestamp, value) in enumerate(
                ((100, 12), (130, 12), (160, 12), (190, 15), (220, 15), (250, 15))
            )
        ]

        suffix = audit._stable_result_suffix(rows, "speed")

        self.assertEqual(suffix[0][1]["source_timestamp_ms"], 190)
        self.assertEqual(suffix[-1][1]["facts"]["result_values"]["speed"], 15)

    def test_three_rows_with_same_physical_timestamp_are_not_three_observations(self):
        rows = [
            (index, {"source_timestamp_ms": 200, "evidence": "same.png",
                     "facts": {"result_values": {"speed": 14}}})
            for index in range(3)
        ]

        self.assertFalse(audit._stable_result_suffix(rows, "speed"))
        identity = audit._observation_identity(
            [row for _, row in rows]
        )
        self.assertEqual(identity["row_count"], 3)
        self.assertEqual(identity["distinct_timestamp_count"], 1)
        self.assertEqual(identity["distinct_evidence_count"], 1)
        self.assertFalse(audit._repeated_source_proof([row for _, row in rows], minimum=3))

    def test_three_paths_at_one_timestamp_are_not_three_observations(self):
        rows = [
            (index, {"source_timestamp_ms": 200, "evidence": f"duplicate-{index}.png",
                     "facts": {"result_values": {"speed": 14}}})
            for index in range(3)
        ]

        self.assertFalse(audit._stable_result_suffix(rows, "speed"))
        identity = audit._observation_identity([row for _, row in rows])
        self.assertEqual(identity["distinct_timestamp_count"], 1)
        self.assertEqual(identity["distinct_evidence_count"], 3)
        self.assertFalse(identity["three_frame_proof"])
        self.assertFalse(audit._repeated_source_proof([row for _, row in rows], minimum=3))

    def test_aggregate_evidence_timestamps_follow_path_order(self):
        readings = {
            "late.png": {"source_timestamp_ms": 503250},
            "early.png": {"source_timestamp_ms": 500000},
        }

        self.assertEqual(
            audit._evidence_timestamp_alignment(
                ["late.png", "early.png"], readings
            ),
            [503250, 500000],
        )

    def test_historical_suffix_guard_rejects_duplicate_physical_observations(self):
        rows = [{
            "basis": "state_derived",
            "source_bound_operands": {
                "after": {
                    "pointer": "/after/0",
                    "source_timestamp_ms": 300,
                },
                "stable_result_suffix": [
                    {"pointer": "/after/0", "source_timestamp_ms": 300, "evidence": ["same.png"]},
                    {"pointer": "/after/1", "source_timestamp_ms": 300, "evidence": ["same.png"]},
                    {"pointer": "/after/2", "source_timestamp_ms": 320, "evidence": ["other.png"]},
                ],
            },
        }]

        result = audit._historical_stability_checks(rows)

        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["rows_with_duplicate_stable_timestamps"], 1)
        self.assertEqual(result["rows_with_duplicate_stable_evidence_paths"], 1)

    def test_wrong_stored_debit_fails_after_source_operand_selection(self):
        contribution, purchase, evidence_rows, receipts = _balance_fixture(stored_cost=9)

        proof = audit._purchase_balance_proof(
            contribution, purchase, evidence_rows, [], [], receipts
        )

        self.assertEqual(proof["observed_difference"], 10)
        self.assertFalse(proof["source_cost_matches"])
        self.assertFalse(proof["source_arithmetic_verified"])

    def test_receipt_outside_purchase_window_fails_temporal_binding(self):
        contribution, purchase, evidence_rows, receipts = _balance_fixture()
        receipts["outcome-1"]["first_seen_ms"] = 80

        proof = audit._purchase_balance_proof(
            contribution, purchase, evidence_rows, [], [], receipts
        )

        self.assertFalse(proof["receipt_temporal_order_ok"])
        self.assertFalse(proof["source_arithmetic_verified"])

    def test_balance_operands_are_not_selected_by_claimed_amount(self):
        contribution, purchase, evidence_rows, receipts = _balance_fixture(
            stored_cost=10, contribution_amount=-20
        )

        proof = audit._purchase_balance_proof(
            contribution, purchase, evidence_rows, [], [], receipts
        )

        self.assertEqual(proof["observed_difference"], 10)
        self.assertTrue(proof["source_arithmetic_verified"])
        self.assertFalse(proof["contribution_amount_match"])

    def test_copied_offer_from_different_purchase_fails_title_binding(self):
        contribution, purchase, receipts, readings = _price_fixture()
        readings["offer-1.png"]["facts"]["lesson_offer_observations"][0]["title"]["text"] = "Other"

        proof = audit._purchase_price_proof(
            contribution, purchase, receipts, readings, []
        )

        self.assertFalse(proof["purchase_identity_ok"] and proof["source_arithmetic_verified"])
        self.assertFalse(proof["source_arithmetic_verified"])

    def test_price_operands_are_not_selected_by_claimed_amount(self):
        contribution, purchase, receipts, readings = _price_fixture()
        contribution["amount"] = -99

        proof = audit._purchase_price_proof(
            contribution, purchase, receipts, readings, []
        )

        self.assertTrue(proof["source_arithmetic_verified"])
        self.assertFalse(proof["contribution_amount_match"])

    def test_stored_initial_price_operand_must_match_pointed_source(self):
        contribution, purchase, receipts, readings = _price_fixture()
        purchase["offer_cost_evidence"]["fields"]["dance"]["initial_value"] = 99

        proof = audit._purchase_price_proof(
            contribution, purchase, receipts, readings, []
        )

        self.assertFalse(
            proof["field_source_audits"]["dance"]["initial_source"]["all_entries_source_bound"]
        )
        self.assertFalse(proof["source_arithmetic_verified"])

    def test_wrong_receipt_owner_is_not_accepted_as_price_proof(self):
        contribution, purchase, receipts, readings = _price_fixture()
        wrong = copy.deepcopy(receipts)
        wrong["outcome-1"]["effects"][0]["name"] = "Other"

        proof = audit._purchase_price_proof(
            contribution, purchase, wrong, readings, []
        )

        self.assertFalse(proof["receipt"]["exact_unconflicted_acquisition"])
        self.assertEqual(proof["source_operand_audit_status"], "compatible_ownership_conflict")
        self.assertFalse(proof["source_arithmetic_verified"])

    def test_state_constraint_uses_recorded_pointer_list_without_claimed_amount(self):
        contribution = {
            "field": "wit",
            "amount": 999,
            "evidence": ["before.png", "result-1.png", "result-2.png"],
        }
        event = {
            "first_seen_ms": 100,
            "last_seen_ms": 200,
        }
        resolution = {
            "field": "wit",
            "amount": 13,
            "visual_candidates": [13],
            "basis": "visible_gain_candidate_and_stable_result_suffix",
            "gain_evidence": ["gain.png"],
            "before_evidence": "before.png",
            "after_evidence": ["result-1.png", "result-2.png"],
        }
        readings = {
            "before.png": {
                "screen": "training_preview",
                "source_timestamp_ms": 90,
                "stats": {"values": {"wit": 100}},
            },
            "gain.png": {
                "screen": "training_result",
                "source_timestamp_ms": 120,
                "facts": {"training_gains": {"wit": 13}},
            },
            "result-1.png": {
                "screen": "training_result",
                "source_timestamp_ms": 150,
                "facts": {"result_values": {"wit": 113}},
            },
            "result-2.png": {
                "screen": "training_result",
                "source_timestamp_ms": 180,
                "facts": {"result_values": {"wit": 113}},
            },
        }

        proof = audit._state_constrained_operand_audit(
            contribution, event, resolution, readings, []
        )

        self.assertEqual(proof["before"]["selected_evidence"], "before.png")
        self.assertEqual(proof["after"]["selected_evidence"], "result-1.png")
        self.assertEqual(proof["endpoint_delta"], 13)
        self.assertTrue(proof["source_arithmetic_verified"])
        self.assertEqual(
            proof["source_operand_audit_status"],
            "constraint_supported_not_independent",
        )
        self.assertFalse(proof["contribution_amount_match"])

    def test_state_constraint_conflicting_candidates_remain_unverified(self):
        contribution = {"field": "guts", "amount": 13, "evidence": ["before.png"]}
        event = {"first_seen_ms": 100, "last_seen_ms": 200}
        resolution = {
            "field": "guts",
            "amount": 13,
            "visual_candidates": [13, 18],
            "basis": "visible_gain_candidate_and_stable_result_suffix",
            "gain_evidence": ["gain.png"],
            "before_evidence": "before.png",
            "after_evidence": ["result.png"],
        }
        readings = {
            "before.png": {
                "source_timestamp_ms": 90,
                "stats": {"values": {"guts": 100}},
            },
            "gain.png": {
                "screen": "training_result",
                "source_timestamp_ms": 120,
                "facts": {"training_gains": {"guts": 13}},
            },
            "result.png": {
                "source_timestamp_ms": 150,
                "facts": {"result_values": {"guts": 113}},
            },
        }

        proof = audit._state_constrained_operand_audit(
            contribution, event, resolution, readings, []
        )

        self.assertEqual(proof["endpoint_delta"], 13)
        self.assertFalse(proof["candidate_constraint_ok"])
        self.assertEqual(proof["source_operand_audit_status"], "unverified_constraint_operands")
        self.assertFalse(proof["source_arithmetic_verified"])


if __name__ == "__main__":
    unittest.main()
