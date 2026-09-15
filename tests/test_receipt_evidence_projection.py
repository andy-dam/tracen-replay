import copy
import unittest

from tests.test_neural_transactions import line, raw, row
from tracen_replay.receipt_evidence_projection import project_numeric_receipt_evidence
from tracen_replay.transactions import outcome_events
from tracen_replay.vision import parse


def parsed_row(time, text, *, screen="event_outcome", confidence=99,
               context_title=None, facts=None, effects=()):
    parsed = parse(raw([line(text, box=(315, 829, 545, 859),
                        confidence=confidence)]))
    return row(time, screen, facts if facts is not None else parsed["facts"],
               effects if effects else parsed["effects"],
               context_title=context_title,
               ocr={"neural": [line(text, box=(315, 829, 545, 859),
                                     confidence=confidence)]})


class ReceiptEvidenceProjectionTests(unittest.TestCase):
    def test_unterminated_pending_receipt_projects_pointer_only(self):
        pending = parsed_row(1000, "Stamina went up by 3")
        proof = parsed_row(1250, "Stamina went up by 3.")
        event = dict(
            id="outcome-1", kind="outcome", first_seen_ms=1000,
            last_seen_ms=1250, context_title=None,
            effects=[dict(kind="stat_change", field="stamina", amount=3,
                          raw_text="Stamina went up by 3.", confidence=99)],
            field_evidence={"stat_change|stamina|": [proof["evidence"]]},
            conflicting_readings=[],
        )
        before = copy.deepcopy(pending)

        project_numeric_receipt_evidence([event], [pending, proof])

        self.assertEqual(pending, before)
        self.assertEqual(len(event["effects"]), 1)
        self.assertIn(pending["evidence"],
                      event["field_evidence"]["stat_change|stamina|"])
        projection = event["receipt_evidence_projections"][0]
        self.assertEqual(projection["source_timestamp_ms"], 1000)
        self.assertEqual(projection["accepted_amount"], 3)
        self.assertFalse(projection["accepted_as_effect"])
        self.assertEqual(projection["complete_proofs"][0]["source_timestamp_ms"],
                         1250)

    def test_malformed_and_ocr_variant_lines_need_complete_proof(self):
        malformed = parsed_row(1000, "Power went 4.")
        variant = parsed_row(1250, "Power wem up by 4.", confidence=89)
        proof = parsed_row(1500, "Power went up by 4.")
        effect = dict(kind="stat_change", field="power", amount=4,
                      raw_text="Power went up by 4.", confidence=99)
        event = dict(id="outcome-2", kind="outcome", first_seen_ms=1000,
                     last_seen_ms=1500, context_title=None,
                     effects=[effect],
                     field_evidence={"stat_change|power|": [proof["evidence"]]},
                     conflicting_readings=[])

        project_numeric_receipt_evidence([event], [malformed, variant, proof])

        projections = event["receipt_evidence_projections"]
        self.assertEqual(
            [(p["source_timestamp_ms"], p["candidate_variant"],
              p["observed_magnitude"]) for p in projections],
            [(1000, "omitted_connective", 4),
             (1250, "ocr_verb_variant", 4)],
        )
        self.assertEqual(len(event["effects"]), 1)

    def test_unknown_pre_event_receipt_links_only_by_exact_title(self):
        candidate = parsed_row(
            750, "Skill Pts went up by 35", screen="unknown",
            context_title="New Year's Shrine Visit",
            facts={"occluded_receipt_lines": [{
                "text": "Skill Pts went up by 35",
                "box": [316, 823, 554, 853],
                "confidence": 97.332,
                "recipient_name_occluded": False,
            }]},
        )
        proof = parsed_row(1000, "Skill Pts went up by 35.",
                           context_title="New Year's Shrine Visit")
        event = dict(
            id="outcome-3", kind="outcome", first_seen_ms=1000,
            last_seen_ms=1250, context_title="New Year's Shrine Visit",
            effects=[dict(kind="stat_change", field="skill_points", amount=35,
                          raw_text="Skill Pts went up by 35.", confidence=99)],
            field_evidence={"stat_change|skill_points|": [proof["evidence"]]},
            conflicting_readings=[],
        )

        project_numeric_receipt_evidence([event], [candidate, proof])

        self.assertEqual(event["receipt_evidence_projections"][0]["source_timestamp_ms"],
                         750)
        self.assertIn(candidate["evidence"],
                      event["field_evidence"]["stat_change|skill_points|"])

    def test_terminated_canonical_neural_line_already_with_effect_is_ignored(self):
        proof = parsed_row(1000, "Speed went up by 5.")
        effect = copy.deepcopy(proof["effects"][0])
        event = dict(id="outcome-4", kind="outcome", first_seen_ms=1000,
                     last_seen_ms=1000, context_title=None,
                     effects=[effect],
                     field_evidence={"stat_change|speed|": [proof["evidence"]]},
                     conflicting_readings=[])
        before = copy.deepcopy(event)

        project_numeric_receipt_evidence([event], [proof])

        self.assertEqual(event, before)

    def test_auxiliary_screen_and_unproved_candidate_are_ignored(self):
        auxiliary = parsed_row(1000, "Speed went up by 5.",
                               screen="career_profile")
        candidate = parsed_row(1000, "Speed went up by 5", confidence=99)
        effect = dict(kind="stat_change", field="speed", amount=5,
                      raw_text="Speed went up by 5.", confidence=99)
        event = dict(id="outcome-5", kind="outcome", first_seen_ms=1000,
                     last_seen_ms=1000, context_title=None, effects=[effect],
                     field_evidence={"stat_change|speed|": []},
                     conflicting_readings=[])

        project_numeric_receipt_evidence([event], [auxiliary])
        self.assertNotIn("receipt_evidence_projections", event)

        project_numeric_receipt_evidence([event], [candidate])
        self.assertNotIn("receipt_evidence_projections", event)
        self.assertEqual(event["field_evidence"]["stat_change|speed|"], [])

    def test_receipt_box_must_remain_inside_gameplay_crop(self):
        candidate = parsed_row(1000, "Speed went up by 5")
        candidate["facts"] = {}
        candidate["ocr"]["neural"][0]["box"] = [811, 829, 900, 859]
        proof = parsed_row(1250, "Speed went up by 5.")
        effect = copy.deepcopy(proof["effects"][0])
        event = dict(id="outcome-x", kind="outcome", first_seen_ms=1000,
                     last_seen_ms=1250, context_title=None, effects=[effect],
                     field_evidence={"stat_change|speed|": []},
                     conflicting_readings=[])

        project_numeric_receipt_evidence([event], [candidate, proof])

        self.assertNotIn("receipt_evidence_projections", event)
        self.assertEqual(event["field_evidence"]["stat_change|speed|"], [])

    def test_proof_requires_distinct_timestamp_and_physical_path(self):
        candidate = parsed_row(1000, "Speed went up by 5")
        effect = dict(kind="stat_change", field="speed", amount=5,
                      raw_text="Speed went up by 5.", confidence=99)
        event = dict(id="outcome-proof", kind="outcome", first_seen_ms=1000,
                     last_seen_ms=1250, context_title=None, effects=[effect],
                     field_evidence={"stat_change|speed|": []},
                     conflicting_readings=[])
        for proof_time, proof_evidence in ((1000, candidate["evidence"]),
                                           (1000, "other.png"),
                                           (1250, candidate["evidence"])):
            proof = parsed_row(proof_time, "Speed went up by 5.")
            proof["evidence"] = proof_evidence
            project_numeric_receipt_evidence([copy.deepcopy(event)],
                                             [candidate, proof])
            self.assertNotIn("receipt_evidence_projections", event)

    def test_invalid_query_or_traversal_proof_path_is_rejected(self):
        candidate = parsed_row(1000, "Power went up by 4")
        effect = dict(kind="stat_change", field="power", amount=4,
                      raw_text="Power went up by 4.", confidence=99)
        event = dict(id="outcome-path", kind="outcome", first_seen_ms=1000,
                     last_seen_ms=1250, context_title=None, effects=[effect],
                     field_evidence={"stat_change|power|": []},
                     conflicting_readings=[])
        for evidence in ("proof.png?frame=1", "nested/../proof.png"):
            proof = parsed_row(1250, "Power went up by 4.")
            proof["evidence"] = evidence
            trial = copy.deepcopy(event)
            project_numeric_receipt_evidence([trial], [candidate, proof])
            self.assertNotIn("receipt_evidence_projections", trial)

    def test_complete_proof_text_must_match_canonical_field_and_amount(self):
        candidate = parsed_row(1000, "Power went up by 4")
        proof = parsed_row(1250, "Power went up by 4.")
        proof["effects"][0]["raw_text"] = "Power went up by 4. unrelated text."
        event = dict(
            id="outcome-text", kind="outcome", first_seen_ms=1000,
            last_seen_ms=1250, context_title=None,
            effects=[dict(kind="stat_change", field="power", amount=4,
                          raw_text="Power went up by 4.", confidence=99)],
            field_evidence={"stat_change|power|": []},
            conflicting_readings=[],
        )

        project_numeric_receipt_evidence([event], [candidate, proof])

        self.assertNotIn("receipt_evidence_projections", event)

    def test_complete_proof_requires_bounded_source_ocr_line(self):
        candidate = parsed_row(1000, "Speed went up by 5")
        proof = parsed_row(1250, "Speed went up by 5.")
        effect = dict(kind="stat_change", field="speed", amount=5,
                      raw_text="Speed went up by 5.", confidence=99)
        event = dict(id="outcome-proof-geometry", kind="outcome",
                     first_seen_ms=1000, last_seen_ms=1250,
                     context_title=None, effects=[effect],
                     field_evidence={"stat_change|speed|": []},
                     conflicting_readings=[])
        for mutation in ("outside", "missing"):
            trial = copy.deepcopy(event)
            source = copy.deepcopy(proof)
            if mutation == "outside":
                source["ocr"]["neural"][0]["box"] = [811, 829, 900, 859]
            else:
                source["ocr"] = {}
            project_numeric_receipt_evidence([trial], [candidate, source])
            self.assertNotIn("receipt_evidence_projections", trial)

    def test_complete_proof_effect_must_bind_to_source_text(self):
        candidate = parsed_row(1000, "Speed went up by 5")
        proof = parsed_row(1250, "Speed went up by 5.")
        proof["ocr"]["neural"] = [line(
            "A completely unrelated sentence.",
            box=(315, 829, 545, 859), confidence=99)]
        effect = dict(kind="stat_change", field="speed", amount=5,
                      raw_text="Speed went up by 5.", confidence=99)
        event = dict(id="outcome-proof-binding", kind="outcome",
                     first_seen_ms=1000, last_seen_ms=1250,
                     context_title=None, effects=[effect],
                     field_evidence={"stat_change|speed|": []},
                     conflicting_readings=[])

        project_numeric_receipt_evidence([event], [candidate, proof])

        self.assertNotIn("receipt_evidence_projections", event)

    def test_case_variant_physical_path_cannot_be_a_second_proof(self):
        candidate = parsed_row(1000, "Speed went up by 5")
        proof = parsed_row(1250, "Speed went up by 5.")
        candidate["evidence"] = "same/frame.png"
        proof["evidence"] = "SAME/FRAME.PNG"
        effect = dict(kind="stat_change", field="speed", amount=5,
                      raw_text="Speed went up by 5.", confidence=99)
        event = dict(id="outcome-proof-case", kind="outcome",
                     first_seen_ms=1000, last_seen_ms=1250,
                     context_title=None, effects=[effect],
                     field_evidence={"stat_change|speed|": []},
                     conflicting_readings=[])

        project_numeric_receipt_evidence([event], [candidate, proof])

        self.assertNotIn("receipt_evidence_projections", event)

    def test_pre_event_link_rejects_unaccepted_title_and_intervening_modal(self):
        candidate = parsed_row(750, "Skill Pts went up by 35", screen="unknown",
                               context_title=None)
        candidate["context_title_candidate"] = "Shrine"
        candidate["facts"] = {"occluded_receipt_lines": [{
            "text": "Skill Pts went up by 35",
            "box": [316, 823, 554, 853],
            "confidence": 97,
            "recipient_name_occluded": False,
        }]}
        modal = parsed_row(875, "Open a different screen", screen="dialogue")
        proof = parsed_row(1000, "Skill Pts went up by 35.",
                           context_title="Shrine")
        event = dict(
            id="outcome-boundary", kind="outcome", first_seen_ms=1000,
            last_seen_ms=1250, context_title="Shrine",
            effects=[dict(kind="stat_change", field="skill_points", amount=35,
                          raw_text="Skill Pts went up by 35.", confidence=99)],
            field_evidence={"stat_change|skill_points|": []},
            conflicting_readings=[],
        )

        project_numeric_receipt_evidence([event], [candidate, modal, proof])

        self.assertNotIn("receipt_evidence_projections", event)

        candidate["context_title"] = "Shrine"
        second_event = copy.deepcopy(event)
        project_numeric_receipt_evidence([second_event],
                                         [candidate, modal, proof])
        self.assertNotIn("receipt_evidence_projections", second_event)

    def test_pre_event_candidate_boundary_and_title_conflict_are_rejected(self):
        for mutation in ("completed_action", "conflicting_title_candidate"):
            candidate = parsed_row(750, "Speed went up by 5", screen="unknown",
                                   context_title="Shrine")
            if mutation == "completed_action":
                candidate["completed_action"] = True
            else:
                candidate["context_title_candidate"] = "Another title"
            proof = parsed_row(1000, "Speed went up by 5.",
                               context_title="Shrine")
            effect = dict(kind="stat_change", field="speed", amount=5,
                          raw_text="Speed went up by 5.", confidence=99)
            event = dict(id="outcome-pre-boundary", kind="outcome",
                         first_seen_ms=1000, last_seen_ms=1250,
                         context_title="Shrine", effects=[effect],
                         field_evidence={"stat_change|speed|": []},
                         conflicting_readings=[])

            project_numeric_receipt_evidence([event], [candidate, proof])

            self.assertNotIn("receipt_evidence_projections", event)

    def test_pre_event_turn_observation_without_candidate_baseline_is_boundary(self):
        candidate = parsed_row(750, "Speed went up by 5", screen="unknown",
                               context_title="Shrine")
        intervening = row(875, "unknown")
        intervening["stats"]["turns_remaining_to_goal"] = 3
        proof = parsed_row(1000, "Speed went up by 5.",
                           context_title="Shrine")
        effect = dict(kind="stat_change", field="speed", amount=5,
                      raw_text="Speed went up by 5.", confidence=99)
        event = dict(id="outcome-pre-turn", kind="outcome",
                     first_seen_ms=1000, last_seen_ms=1250,
                     context_title="Shrine", effects=[effect],
                     field_evidence={"stat_change|speed|": []},
                     conflicting_readings=[])

        project_numeric_receipt_evidence([event],
                                         [candidate, intervening, proof])

        self.assertNotIn("receipt_evidence_projections", event)

    def test_complete_proof_conflicting_title_is_rejected(self):
        candidate = parsed_row(750, "Speed went up by 5", screen="unknown",
                               context_title="Shrine")
        proof = parsed_row(1000, "Speed went up by 5.",
                           context_title="Another title")
        effect = dict(kind="stat_change", field="speed", amount=5,
                      raw_text="Speed went up by 5.", confidence=99)
        event = dict(id="outcome-proof-title", kind="outcome",
                     first_seen_ms=1000, last_seen_ms=1250,
                     context_title="Shrine", effects=[effect],
                     field_evidence={"stat_change|speed|": []},
                     conflicting_readings=[])

        project_numeric_receipt_evidence([event], [candidate, proof])

        self.assertNotIn("receipt_evidence_projections", event)

    def test_vision_parser_keeps_unterminated_receipt_out_of_effects(self):
        parsed = parse(raw([line("Skill Pts went up by 35")]))
        self.assertEqual(parsed["effects"], [])
        self.assertEqual(parsed["facts"]["effect_candidates"][0]["amount"], 35)

    def test_outcome_pipeline_keeps_projection_separate_from_effect(self):
        rows = [parsed_row(1000, "Stamina went up by 3"),
                parsed_row(1250, "Stamina went up by 3.")]

        event = outcome_events(rows)[0]

        self.assertEqual(event["deltas"], {"stamina": 3})
        self.assertEqual(len(event["effects"]), 1)
        self.assertEqual(event["receipt_evidence_projections"][0]["accepted_as_effect"],
                         False)
        self.assertEqual(event["field_evidence"]["stat_change|stamina|"],
                         ["1250.png", "1000.png"])


if __name__ == "__main__":
    unittest.main()
