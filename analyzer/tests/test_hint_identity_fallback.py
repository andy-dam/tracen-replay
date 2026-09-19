"""Focused tests for preserving source-proven hint-circle effects."""

from copy import deepcopy
import unittest

from tracen_replay.hint_identity_fallback import preserve_valid_circle_effect


SOURCE = "a" * 64


def _proof(timestamp, evidence, *, source=SOURCE, symbol="○", kind="single_circle"):
    # The detector stores symbol boxes in the 810px gameplay crop.  OCR line
    # boxes below use source-frame coordinates, so the helper must apply the
    # fixed gameplay-pane x offset before checking containment.
    proof = {
        "kind": kind,
        "symbol": symbol,
        "box": [526, 812, 541, 828],
        "method": "strict_terminal_ring_geometry",
        "coordinate_space": "gameplay_crop",
        "source_timestamp_ms": timestamp,
        "evidence": evidence,
        "gameplay_sha256": f"{timestamp:064x}",
        "source_frame_sha256": f"{timestamp + 1:064x}",
        "evidence_sha256": f"{timestamp + 2:064x}",
    }
    if source is not None:
        proof["source_sha256"] = source
    return proof


def _strong_effect(name="Example Skill ○", amount=1, proof=None):
    proof = proof or _proof(250, "strong-2")
    return {
        "kind": "skill_hint_change",
        "name": name,
        "amount": amount,
        "raw_text": f"Gained {amount} hint level(s) for {name}.",
        "original_text": f"Gained {amount} hint level(s) for {name.rstrip('○').rstrip()} .",
        "confidence": 99.2,
        "visual_symbol_observation": deepcopy(proof),
    }


def _weak_effect(name="Example Skill", amount=1):
    return {
        "kind": "skill_hint_change",
        "name": name,
        "amount": amount,
        "raw_text": f"Gained {amount} hint level(s) for {name} .",
        "confidence": 98.7,
    }


def _row(timestamp, evidence, effect, *, title="A Hint for Growth"):
    proof = effect.get("visual_symbol_observation")
    line = {
        "text": effect["raw_text"],
        "confidence": 99.1,
        # Source-frame coordinates: gameplay-crop proof [526, ...] becomes
        # [674, ...] after adding the pane's x origin of 148.
        "box": [660, 800, 720, 840],
    }
    if proof is not None:
        line["visual_symbol_observation"] = deepcopy(proof)
    return {
        "evidence": evidence,
        "source_timestamp_ms": timestamp,
        "screen": "event_outcome",
        "context_title": title,
        "effects": [deepcopy(effect)],
        "ocr": {"neural": [line]},
    }


def _case(*, strong_amount=1, weak_amount=1, strong_name="Example Skill ○",
          weak_name="Example Skill", proof_count=2, proof_source=SOURCE):
    proofs = [_proof(150 + index * 100, f"strong-{index + 1}", source=proof_source)
              for index in range(proof_count)]
    strong = _strong_effect(strong_name, strong_amount, proofs[-1])
    weak = _weak_effect(weak_name, weak_amount)
    rows = {
        f"strong-{index + 1}": _row(proof["source_timestamp_ms"],
                                    f"strong-{index + 1}",
                                    dict(strong, visual_symbol_observation=proof))
        for index, proof in enumerate(proofs)
    }
    event = {
        "id": "outcome-test",
        "kind": "outcome",
        "first_seen_ms": 100,
        "last_seen_ms": 400,
        "context_title": "A Hint for Growth",
        "effects": [strong, weak],
        "field_evidence": {
            f"skill_hint_change||{strong_name}": list(rows),
            f"skill_hint_change||{weak_name}": ["weak-1", "weak-2"],
        },
        "conflicting_readings": [],
    }
    return event, rows


def _weak_rows(event, weak, timestamps, box=(660, 800, 720, 840)):
    """Rows for the weak spelling's evidence, its line at ``box``."""
    rows = {}
    for path, timestamp in zip(event["field_evidence"]["skill_hint_change||" + weak["name"]], timestamps):
        row = _row(timestamp, path, weak)
        row["ocr"]["neural"][0]["box"] = list(box)
        rows[path] = row
    return rows


class HintIdentityFallbackTests(unittest.TestCase):
    def test_the_same_slot_read_with_and_without_the_glyph_is_one_award(self):
        event, rows = _case()
        weak = event["effects"][1]
        rows.update(_weak_rows(event, weak, (200, 300)))

        preserve_valid_circle_effect(event, rows)

        self.assertEqual([(e["name"], e["amount"]) for e in event["effects"]],
                         [("Example Skill ○", 1)])
        self.assertNotIn("ambiguous_effect_candidates", event)
        strong = event["effects"][0]
        self.assertEqual(strong["name_resolution"], "same_slot_circle_glyph_unread")
        self.assertEqual(strong["circle_glyph_unread_evidence"], ["weak-1", "weak-2"])

    def test_a_weak_read_at_another_slot_or_on_a_strong_frame_stays_unresolved(self):
        # Another slot: the weak line sits a row lower than the strong one.
        event, rows = _case()
        rows.update(_weak_rows(event, event["effects"][1], (200, 300), box=(660, 850, 720, 890)))
        preserve_valid_circle_effect(event, rows)
        self.assertEqual(len(event["ambiguous_effect_candidates"]), 1)
        self.assertNotIn("name_resolution", event["effects"][0])
        # A frame that shows both spellings shows two lines.
        event, rows = _case()
        weak = event["effects"][1]
        event["field_evidence"]["skill_hint_change||Example Skill"] = ["strong-1", "weak-2"]
        rows["weak-2"] = _row(300, "weak-2", weak)
        rows["strong-1"]["effects"].append(deepcopy(weak))
        rows["strong-1"]["ocr"]["neural"].append({"text": weak["raw_text"], "confidence": 98.7, "box": [660, 850, 720, 890]})
        preserve_valid_circle_effect(event, rows)
        self.assertEqual(len(event["ambiguous_effect_candidates"]), 1)
        # Too far apart in time.
        event, rows = _case()
        rows.update(_weak_rows(event, event["effects"][1], (5000, 5300)))
        preserve_valid_circle_effect(event, rows)
        self.assertEqual(len(event["ambiguous_effect_candidates"]), 1)

    def test_repeated_circle_keeps_strong_and_retains_weak_as_unresolved(self):
        event, rows = _case()
        original_rows = deepcopy(rows)
        original_proof = deepcopy(event["effects"][0]["visual_symbol_observation"])

        preserve_valid_circle_effect(event, rows)

        self.assertEqual([(e["name"], e["amount"]) for e in event["effects"]],
                         [("Example Skill ○", 1)])
        self.assertEqual(event["effects"][0]["visual_symbol_observation"], original_proof)
        self.assertEqual(rows, original_rows)
        candidates = event["ambiguous_effect_candidates"]
        self.assertEqual(len(candidates), 1)
        candidate = candidates[0]
        self.assertEqual(candidate["effect"]["name"], "Example Skill")
        self.assertEqual(candidate["effect"]["raw_text"], "Gained 1 hint level(s) for Example Skill .")
        self.assertEqual(candidate["field"], "skill_hint_change||Example Skill")
        self.assertEqual(candidate["possible_duplicate_of"]["name"], "Example Skill ○")
        self.assertEqual(candidate["possible_duplicate_of"]["amount"], 1)
        self.assertFalse(candidate["continuity_proven"])
        self.assertIsNone(candidate["occurrence_count"])
        self.assertEqual(candidate["identity_status"], "possible_duplicate_or_additional_effect")

    def test_literal_o_is_staged_only_with_exact_base_anchor(self):
        event, rows = _case()
        literal_o = _weak_effect("Example Skill O", 1)
        event["effects"].append(literal_o)
        event["field_evidence"]["skill_hint_change||Example Skill O"] = ["o-1", "o-2"]

        preserve_valid_circle_effect(event, rows)

        self.assertEqual([(e["name"], e["amount"]) for e in event["effects"]],
                         [("Example Skill ○", 1)])
        candidates = event["ambiguous_effect_candidates"]
        self.assertEqual({candidate["effect"]["name"] for candidate in candidates},
                         {"Example Skill", "Example Skill O"})
        o_candidate = next(candidate for candidate in candidates
                           if candidate["effect"]["name"] == "Example Skill O")
        self.assertEqual(o_candidate["possible_duplicate_of"]["name"], "Example Skill ○")
        self.assertEqual(o_candidate["evidence"], ["o-1", "o-2"])

        isolated_event, isolated_rows = _case()
        isolated_event["effects"][1] = _weak_effect("Example Skill O", 1)
        isolated_event["field_evidence"].pop("skill_hint_change||Example Skill")
        isolated_event["field_evidence"]["skill_hint_change||Example Skill O"] = ["o-1"]

        preserve_valid_circle_effect(isolated_event, isolated_rows)

        self.assertEqual({effect["name"] for effect in isolated_event["effects"]},
                         {"Example Skill ○", "Example Skill O"})
        self.assertNotIn("ambiguous_effect_candidates", isolated_event)

    def test_continuous_gapped_and_conflicting_weak_observations_stay_unresolved(self):
        for label, weak_evidence, conflicts in (
            ("continuous", ["weak-1", "weak-2"], []),
            ("gapped", ["weak-1", "weak-9"], []),
            ("conflicting", ["weak-1", "weak-2"],
             [{"field": "skill_hint_change||Example Skill", "reason": "name_conflict"}]),
        ):
            with self.subTest(case=label):
                event, rows = _case()
                event["field_evidence"]["skill_hint_change||Example Skill"] = weak_evidence
                event["conflicting_readings"] = conflicts
                preserve_valid_circle_effect(event, rows)
                self.assertEqual(len(event["effects"]), 1)
                self.assertEqual(len(event["ambiguous_effect_candidates"]), 1)
                candidate = event["ambiguous_effect_candidates"][0]
                self.assertFalse(candidate["continuity_proven"])
                self.assertIsNone(candidate["occurrence_count"])

    def test_only_one_valid_symbol_timestamp_leaves_pair_untouched(self):
        event, rows = _case(proof_count=1)
        original = deepcopy(event)

        preserve_valid_circle_effect(event, rows)

        self.assertEqual(event, original)

    def test_legacy_three_image_hash_proofs_are_accepted_without_inventing_source_hash(self):
        event, rows = _case(proof_source=None)
        original_rows = deepcopy(rows)

        preserve_valid_circle_effect(event, rows)

        self.assertEqual(len(event["effects"]), 1)
        self.assertNotIn("source_sha256", event["effects"][0]["visual_symbol_observation"])
        self.assertEqual(rows, original_rows)

    def test_missing_any_image_hash_rejects_proof(self):
        for key in ("gameplay_sha256", "source_frame_sha256", "evidence_sha256"):
            with self.subTest(key=key):
                event, rows = _case()
                proof = rows["strong-2"]["effects"][0]["visual_symbol_observation"]
                proof.pop(key)
                rows["strong-2"]["ocr"]["neural"][0]["visual_symbol_observation"] = deepcopy(proof)
                original = deepcopy(event)

                preserve_valid_circle_effect(event, rows)

                self.assertEqual(event, original)

    def test_mismatched_or_reused_proof_leaves_pair_untouched(self):
        for mutation in ("effect_proof", "recording_hash", "malformed_recording_hash",
                         "source_frame_duplicate", "confidence_above_bound"):
            with self.subTest(mutation=mutation):
                event, rows = _case()
                if mutation == "effect_proof":
                    event["effects"][0]["visual_symbol_observation"] = _proof(
                        999, "foreign", source=SOURCE)
                elif mutation == "recording_hash":
                    proof = rows["strong-2"]["effects"][0]["visual_symbol_observation"]
                    proof["source_sha256"] = "b" * 64
                    rows["strong-2"]["ocr"]["neural"][0]["visual_symbol_observation"] = deepcopy(proof)
                elif mutation == "malformed_recording_hash":
                    proof = rows["strong-2"]["effects"][0]["visual_symbol_observation"]
                    proof["source_sha256"] = "not-a-sha"
                    rows["strong-2"]["ocr"]["neural"][0]["visual_symbol_observation"] = deepcopy(proof)
                elif mutation == "source_frame_duplicate":
                    proof = rows["strong-2"]["effects"][0]["visual_symbol_observation"]
                    first = rows["strong-1"]["effects"][0]["visual_symbol_observation"]
                    proof["source_frame_sha256"] = first["source_frame_sha256"]
                    rows["strong-2"]["ocr"]["neural"][0]["visual_symbol_observation"] = deepcopy(proof)
                    event["effects"][0]["visual_symbol_observation"] = deepcopy(proof)
                else:  # confidence_above_bound
                    rows["strong-1"]["ocr"]["neural"][0]["confidence"] = 101
                original = deepcopy(event)

                preserve_valid_circle_effect(event, rows)

                self.assertEqual(event, original)

    def test_source_rows_must_fall_inside_event_time_bounds(self):
        event, rows = _case()
        proof = rows["strong-1"]["effects"][0]["visual_symbol_observation"]
        proof["source_timestamp_ms"] = 50
        rows["strong-1"]["source_timestamp_ms"] = 50
        rows["strong-1"]["ocr"]["neural"][0]["visual_symbol_observation"] = deepcopy(proof)
        original = deepcopy(event)

        preserve_valid_circle_effect(event, rows)

        self.assertEqual(event, original)

    def test_different_amount_and_double_circle_are_not_discarded(self):
        event, rows = _case(weak_amount=2)
        preserve_valid_circle_effect(event, rows)
        self.assertEqual({(e["name"], e["amount"]) for e in event["effects"]},
                         {("Example Skill ○", 1), ("Example Skill", 2)})

        event, rows = _case(strong_name="Other Skill ◎", weak_name="Other Skill")
        double = event["effects"][0]["visual_symbol_observation"]
        double.update(kind="double_circle", symbol="◎")
        event["effects"][0]["visual_symbol_observation"] = double
        for row in rows.values():
            row["effects"][0]["visual_symbol_observation"].update(kind="double_circle", symbol="◎")
            row["ocr"]["neural"][0]["visual_symbol_observation"].update(kind="double_circle", symbol="◎")
        original = deepcopy(event)

        preserve_valid_circle_effect(event, rows)

        self.assertEqual(event, original)

    def test_separate_valid_hint_award_is_preserved(self):
        event, rows = _case()
        other_proofs = [_proof(350, "other-1"), _proof(400, "other-2")]
        other = _strong_effect("Other Skill ○", 2, other_proofs[-1])
        event["effects"].append(other)
        event["field_evidence"]["skill_hint_change||Other Skill ○"] = ["other-1", "other-2"]
        rows["other-1"] = _row(350, "other-1",
                                dict(other, visual_symbol_observation=other_proofs[0]))
        rows["other-2"] = _row(400, "other-2",
                                dict(other, visual_symbol_observation=other_proofs[1]))

        preserve_valid_circle_effect(event, rows)

        self.assertEqual({(e["name"], e["amount"]) for e in event["effects"]},
                         {("Example Skill ○", 1), ("Other Skill ○", 2)})
        self.assertEqual(len(event["ambiguous_effect_candidates"]), 1)

    def test_invalid_huge_box_does_not_overflow_or_promote(self):
        event, rows = _case()
        line = rows["strong-1"]["ocr"]["neural"][0]
        line["box"] = [10 ** 1000, 800, 720, 840]
        original = deepcopy(event)

        preserve_valid_circle_effect(event, rows)

        self.assertEqual(event, original)

    def test_repeated_application_is_idempotent(self):
        event, rows = _case()
        preserve_valid_circle_effect(event, rows)
        snapshot = deepcopy(event)

        preserve_valid_circle_effect(event, rows)

        self.assertEqual(event, snapshot)


if __name__ == "__main__":
    unittest.main()
