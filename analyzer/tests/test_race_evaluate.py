import copy
import hashlib
import unittest

from tests.test_gameplay import workspace_temp
from tracen_replay.race_evaluate import SCHEMA_VERSION, evaluate


class RaceEvaluationTests(unittest.TestCase):
    source_hash = "a" * 64

    def pair(self, root):
        proof = root / "race.png"
        proof.write_bytes(b"reviewed race frame")
        digest = hashlib.sha256(proof.read_bytes()).hexdigest()
        proof_row = {"source_timestamp_ms": 500, "evidence": "race.png", "sha256": digest}
        expected = {
            "race_name": "Reviewed Race",
            "placing": 1,
            "fans": 1200,
            "fans_gained": 200,
            "course": {
                "venue": "Tokyo",
                "surface": "turf",
                "distance_m": 1600,
                "distance_category": "mile",
                "direction": "left",
                "variant": "inner",
            },
        }
        reference = {
            "schema_version": SCHEMA_VERSION,
            "source_sha256": self.source_hash,
            "scope": "one reviewed race result",
            "start_ms": 100,
            "end_ms": 1000,
            "races": [
                {
                    "start_ms": 300,
                    "end_ms": 800,
                    "expected": expected,
                    "proofs": [proof_row],
                    "item_snapshots": [
                        {
                            "source_timestamp_ms": 500,
                            "quantities": [200],
                            "proofs": [proof_row],
                        }
                    ],
                }
            ],
            "independently_reviewed": False,
        }
        candidate = {
            "id": "race-1",
            "first_seen_ms": 400,
            "last_seen_ms": 600,
            **copy.deepcopy(expected),
            "conflicting_readings": {},
        }
        reading = {
            "source_timestamp_ms": 500,
            "evidence": "race.png",
            "screen": "race_result",
            "facts": {
                "visible_item_quantities": [{"quantity": 200, "name": None}],
                "item_rewards_complete": False,
            },
        }
        report = {
            "source": {"sha256": self.source_hash, "duration_ms": 2000},
            "gameplay_tracking": {
                "auxiliary_log_used": False,
                "races": [candidate],
                "readings": [reading],
            },
        }
        return reference, report, proof

    def test_typed_race_and_exact_visible_quantity_match(self):
        with workspace_temp() as root:
            reference, report, _ = self.pair(root)
            # A later visible tile is deliberately different.  The evaluator
            # must score only the timestamped item snapshot, never sum frames.
            report["gameplay_tracking"]["readings"].append(
                {
                    "source_timestamp_ms": 750,
                    "evidence": "race.png",
                    "facts": {"visible_item_quantities": [{"quantity": 400}]},
                }
            )
            report["gameplay_tracking"]["races"][0]["visible_item_reward_snapshots"] = [
                {"items": [{"quantity": 600}]}
            ]
            result = evaluate(reference, report, root)
            self.assertTrue(result["passed"])
            self.assertEqual(result["matched_races"], 1)
            self.assertEqual(result["matched_fields"], 10)
            self.assertEqual(result["matched_items"], 1)
            self.assertTrue(result["proof_hashes_checked"])
            self.assertFalse(result["full_recording_race_recall_measured"])
            self.assertFalse(result["complete_race_validation"])

    def test_reward_sections_require_exact_frame_observations(self):
        with workspace_temp() as root:
            reference, report, _ = self.pair(root)
            item = reference['races'][0]['item_snapshots'][0]
            item['quantities'] = [1, 200, 200]
            item['sections'] = {'items': [1, 200], 'bonus': [200]}
            row = report['gameplay_tracking']['readings'][0]
            visible = [dict(quantity=q, box=[320+i*100,700,360+i*100,730])
                       for i,q in enumerate(item['quantities'])]
            row['facts']['visible_item_quantities'] = copy.deepcopy(visible)
            candidate = report['gameplay_tracking']['races'][0]
            observed = [dict(v, section=s) for v,s in zip(visible,['items','items','bonus'])]
            observation = dict(source_timestamp_ms=500,evidence='race.png',items=observed)
            candidate['visible_item_reward_snapshots'] = [dict(item_observations=[observation])]
            self.assertTrue(evaluate(reference,report,root)['passed'])
            for case in ('wrong_section','unknown','different_time','different_proof','different_position','missing_position','conflict'):
                with self.subTest(case=case):
                    changed=copy.deepcopy(report)
                    snap=changed['gameplay_tracking']['races'][0]['visible_item_reward_snapshots'][0]
                    obs=snap['item_observations'][0]
                    if case=='wrong_section': obs['items'][0]['section']='bonus'
                    if case=='unknown': obs['items'][0]['section']=None
                    if case=='different_time': obs['source_timestamp_ms']=750
                    if case=='different_proof': obs['evidence']='another.png'
                    if case=='different_position': obs['items'][0]['box'][0]+=5
                    if case=='missing_position':
                        obs['items'][0].pop('box')
                        changed['gameplay_tracking']['readings'][0]['facts']['visible_item_quantities'][0].pop('box')
                    if case=='conflict':
                        other=copy.deepcopy(obs); other['items'][0]['section']='bonus'
                        snap['item_observations'].append(other)
                    self.assertFalse(evaluate(reference,changed,root)['passed'])

    def test_reference_sections_must_cover_all_quantities(self):
        with workspace_temp() as root:
            reference,report,_=self.pair(root)
            for sections in (None,{}, {'items':[1]}, {'other':[200]}, {'items':['200']}):
                with self.subTest(sections=sections):
                    reference['races'][0]['item_snapshots'][0]['sections']=sections
                    with self.assertRaisesRegex(ValueError,'partition'):
                        evaluate(reference,report,root)

    def test_missing_and_incorrect_fields_do_not_equal_unknown_or_zero(self):
        with workspace_temp() as root:
            reference, report, _ = self.pair(root)
            expected = reference["races"][0]["expected"]
            expected["fans"] = 0
            expected["fans_gained"] = {"unknown": True}
            expected["course"]["variant"] = {"unknown": True}
            candidate = report["gameplay_tracking"]["races"][0]
            candidate["placing"] = 2
            candidate["fans"] = None
            candidate["fans_gained"] = None
            result = evaluate(reference, report, root)
            self.assertFalse(result["passed"])
            checks = {check["field"]: check for check in result["race_results"][0]["checks"]}
            self.assertEqual(checks["placing"]["status"], "incorrect")
            self.assertEqual(checks["fans"]["status"], "missing")
            self.assertEqual(checks["fans_gained"]["status"], "unknown")
            self.assertEqual(checks["course.variant"]["status"], "unscored")
            self.assertEqual(result["missing_fields"], 1)
            self.assertEqual(result["incorrect_fields"], 1)
            # The unknown fields are reported separately and are not silently
            # converted into a zero or counted as concrete matches.
            self.assertEqual(result["unknown_fields"], 1)

    def test_observed_zero_is_a_real_value(self):
        with workspace_temp() as root:
            reference, report, _ = self.pair(root)
            reference["races"][0]["expected"]["fans"] = 0
            report["gameplay_tracking"]["races"][0]["fans"] = 0
            result = evaluate(reference, report, root)
            self.assertTrue(result["passed"])
            self.assertEqual(
                next(check for check in result["race_results"][0]["checks"] if check["field"] == "fans")["status"],
                "matched",
            )

    def test_runtime_numeric_types_must_match_typed_reference(self):
        with workspace_temp() as root:
            reference, report, _ = self.pair(root)
            candidate = report["gameplay_tracking"]["races"][0]
            candidate["placing"] = True
            candidate["course"]["distance_m"] = 1600.0
            result = evaluate(reference, report, root)
            self.assertFalse(result["passed"])
            checks = {check["field"]: check for check in result["race_results"][0]["checks"]}
            self.assertEqual(checks["placing"]["status"], "incorrect")
            self.assertEqual(checks["course.distance_m"]["status"], "incorrect")
            self.assertEqual(result["incorrect_fields"], 2)

    def test_duplicate_time_candidates_are_ambiguous_without_value_selection(self):
        with workspace_temp() as root:
            reference, report, _ = self.pair(root)
            wrong = copy.deepcopy(report["gameplay_tracking"]["races"][0])
            wrong["id"] = "race-wrong-values"
            wrong["race_name"] = "Different Race"
            report["gameplay_tracking"]["races"].append(wrong)
            result = evaluate(reference, report, root)
            self.assertFalse(result["passed"])
            self.assertEqual(result["ambiguous_races"], [{"reference_index": 0, "candidate_indices": [0, 1]}])
            self.assertEqual(result["matched_races"], 0)
            self.assertEqual(result["expected_fields"], 10)
            self.assertEqual(result["unobserved_fields"], 10)
            self.assertEqual(result["expected_items"], 1)
            self.assertEqual(result["unobserved_items"], 1)

    def test_unexpected_race_in_scope_fails(self):
        with workspace_temp() as root:
            reference, report, _ = self.pair(root)
            extra = copy.deepcopy(report["gameplay_tracking"]["races"][0])
            extra["id"] = "race-extra"
            extra["first_seen_ms"], extra["last_seen_ms"] = 820, 900
            report["gameplay_tracking"]["races"].append(extra)
            result = evaluate(reference, report, root)
            self.assertFalse(result["passed"])
            self.assertEqual([race["id"] for race in result["unexpected_races"]], ["race-extra"])

    def test_one_candidate_cannot_be_reused_by_overlapping_references(self):
        with workspace_temp() as root:
            reference, report, _ = self.pair(root)
            second = copy.deepcopy(reference["races"][0])
            second["start_ms"], second["end_ms"] = 450, 900
            reference["races"].append(second)
            with self.assertRaisesRegex(ValueError, "Overlapping race reference windows"):
                evaluate(reference, report, root)

    def test_unmatched_race_keeps_field_and_item_denominators(self):
        with workspace_temp() as root:
            reference, report, _ = self.pair(root)
            report["gameplay_tracking"]["races"] = []
            result = evaluate(reference, report, root)
            self.assertFalse(result["passed"])
            self.assertEqual(result["expected_fields"], 10)
            self.assertEqual(result["unobserved_fields"], 10)
            self.assertEqual(result["expected_items"], 1)
            self.assertEqual(result["unobserved_items"], 1)
            self.assertEqual(result["unobserved_races"], [0])
            self.assertEqual(result["race_results"][0]["expected_field_count"], 10)
            self.assertEqual(result["race_results"][0]["expected_item_count"], 1)

    def test_candidate_spanning_disjoint_reference_windows_is_ambiguous(self):
        with workspace_temp() as root:
            reference, report, _ = self.pair(root)
            first = reference["races"][0]
            first["start_ms"], first["end_ms"] = 300, 450
            first["proofs"][0]["source_timestamp_ms"] = 400
            first["item_snapshots"][0]["source_timestamp_ms"] = 400
            first["item_snapshots"][0]["proofs"][0]["source_timestamp_ms"] = 400
            second = copy.deepcopy(first)
            second["start_ms"], second["end_ms"] = 650, 900
            second["proofs"][0]["source_timestamp_ms"] = 700
            second["item_snapshots"][0]["source_timestamp_ms"] = 700
            second["item_snapshots"][0]["proofs"][0]["source_timestamp_ms"] = 700
            # The same reviewed frame is valid in both windows for this
            # synthetic test; matching must still use time only.
            reference["races"] = [first, second]
            candidate = report["gameplay_tracking"]["races"][0]
            candidate["first_seen_ms"], candidate["last_seen_ms"] = 400, 700
            report["gameplay_tracking"]["readings"] = [
                {
                    "source_timestamp_ms": timestamp,
                    "evidence": "race.png",
                    "facts": {"visible_item_quantities": [{"quantity": 200}]},
                }
                for timestamp in (400, 700)
            ]
            result = evaluate(reference, report, root)
            self.assertFalse(result["passed"])
            self.assertTrue(
                any(
                    entry.get("candidate_index") == 0
                    and entry.get("reference_indices") == [0, 1]
                    for entry in result["ambiguous_races"]
                )
            )
            self.assertEqual(result["expected_fields"], 20)
            self.assertEqual(result["unobserved_fields"], 20)

    def test_missing_annotated_sample_and_changed_proof_are_reported(self):
        with workspace_temp() as root:
            reference, report, proof = self.pair(root)
            report["gameplay_tracking"]["readings"] = []
            result = evaluate(reference, report, root)
            self.assertFalse(result["passed"])
            self.assertTrue(any("missing annotated sample" in error for error in result["errors"]))
            proof.write_bytes(b"tampered")
            report["gameplay_tracking"]["readings"] = [
                {
                    "source_timestamp_ms": 500,
                    "evidence": "race.png",
                    "facts": {"visible_item_quantities": [{"quantity": 200}]},
                }
            ]
            result = evaluate(reference, report, root)
            self.assertTrue(any("proof changed" in error for error in result["errors"]))

    def test_evidence_path_cannot_escape_run_root(self):
        with workspace_temp() as root:
            reference, report, proof = self.pair(root)
            reference["races"][0]["proofs"][0]["evidence"] = "../outside.png"
            reference["races"][0]["item_snapshots"][0]["proofs"][0]["evidence"] = "../outside.png"
            with self.assertRaisesRegex(ValueError, "leaves run directory"):
                evaluate(reference, report, root)

    def test_source_scope_and_gameplay_only_report_are_required(self):
        with workspace_temp() as root:
            reference, report, _ = self.pair(root)
            for mutation, message in (
                (lambda r: r["source"].update(sha256="b" * 64), "Different source"),
                (lambda r: r["gameplay_tracking"].update(auxiliary_log_used=True), "gameplay-only"),
                (lambda r: r["source"].update(duration_ms=900), "exceeds report source duration"),
            ):
                changed = copy.deepcopy(report)
                mutation(changed)
                with self.subTest(message=message), self.assertRaisesRegex(ValueError, message):
                    evaluate(reference, changed, root)

    def test_reference_types_windows_and_proofs_are_validated(self):
        with workspace_temp() as root:
            reference, report, _ = self.pair(root)
            cases = []
            changed = copy.deepcopy(reference)
            changed["races"][0]["expected"]["placing"] = True
            cases.append(changed)
            changed = copy.deepcopy(reference)
            changed["races"][0]["start_ms"] = 50
            cases.append(changed)
            changed = copy.deepcopy(reference)
            changed["races"][0]["proofs"].append(copy.deepcopy(changed["races"][0]["proofs"][0]))
            cases.append(changed)
            changed = copy.deepcopy(reference)
            changed["races"][0]["proofs"] = []
            cases.append(changed)
            for invalid in cases:
                with self.assertRaises(ValueError):
                    evaluate(invalid, report, root)

    def negative_pair(self, root):
        proof = root / "negative.png"
        proof.write_bytes(b"reviewed no-race interval")
        reference = {
            "schema_version": SCHEMA_VERSION,
            "source_sha256": self.source_hash,
            "scope": "reviewed interval with no completed race",
            "start_ms": 0,
            "end_ms": 1000,
            "races": [],
            "no_races": True,
            "independently_reviewed": True,
            "proofs": [
                {
                    "source_timestamp_ms": 500,
                    "evidence": "negative.png",
                    "sha256": hashlib.sha256(proof.read_bytes()).hexdigest(),
                }
            ],
        }
        report = {
            "source": {"sha256": self.source_hash, "duration_ms": 1000},
            "gameplay_tracking": {
                "auxiliary_log_used": False,
                "races": [],
                "readings": [{"source_timestamp_ms": 500, "evidence": "negative.png", "facts": {}}],
            },
        }
        return reference, report

    def test_explicit_reviewed_negative_reference_can_pass(self):
        with workspace_temp() as root:
            reference, report = self.negative_pair(root)
            result = evaluate(reference, report, root)
            self.assertTrue(result["passed"])
            self.assertTrue(result["negative_reference"])
            self.assertEqual(result["expected_races"], 0)
            self.assertEqual(result["predicted_races"], 0)

    def test_empty_reference_cannot_pass_vacuously_and_negative_catches_race(self):
        with workspace_temp() as root:
            reference, report = self.negative_pair(root)
            changed = copy.deepcopy(reference)
            changed["independently_reviewed"] = False
            with self.assertRaisesRegex(ValueError, "independent review"):
                evaluate(changed, report, root)
            changed = copy.deepcopy(reference)
            changed["proofs"] = []
            with self.assertRaisesRegex(ValueError, "at least one timestamped proof"):
                evaluate(changed, report, root)

            positive, positive_report, _ = self.pair(root)
            negative_report = copy.deepcopy(report)
            negative_report["gameplay_tracking"]["races"] = positive_report["gameplay_tracking"]["races"]
            result = evaluate(reference, negative_report, root)
            self.assertFalse(result["passed"])
            self.assertEqual(len(result["unexpected_races"]), 1)

    def test_item_snapshot_is_exact_and_ignores_name_or_completeness(self):
        with workspace_temp() as root:
            reference, report, _ = self.pair(root)
            facts = report["gameplay_tracking"]["readings"][0]["facts"]
            facts["visible_item_quantities"] = [{"quantity": 100}, {"quantity": 100}]
            facts["item_rewards_complete"] = True
            facts["inventory_after_scroll"] = [{"name": "Invented", "quantity": 200}]
            result = evaluate(reference, report, root)
            self.assertFalse(result["passed"])
            item = result["race_results"][0]["item_snapshots"][0]
            self.assertEqual(item["status"], "incorrect")
            self.assertEqual(item["actual_quantities"], [100, 100])

    def test_duplicate_annotated_readings_are_ambiguous(self):
        with workspace_temp() as root:
            reference, report, _ = self.pair(root)
            report["gameplay_tracking"]["readings"].append(copy.deepcopy(report["gameplay_tracking"]["readings"][0]))
            result = evaluate(reference, report, root)
            self.assertFalse(result["passed"])
            self.assertTrue(any("ambiguous annotated sample" in error for error in result["errors"]))


if __name__ == "__main__":
    unittest.main()
