"""Focused tests for source-bound same-slot hint identity quarantine."""

from copy import deepcopy
import unittest

from tracen_replay.hint_identity_quarantine import (
    quarantine_receipt_identity_conflicts,
)


_SOURCE_SHA = "a" * 64


def _hex(seed: int) -> str:
    return f"{seed:064x}"


def _effect(name: str, amount: int = 1, **extra):
    raw = f"Gained {amount} hint level(s) for {name}."
    result = dict(kind="skill_hint_change", name=name, amount=amount,
                  raw_text=raw, original_text=raw)
    result.update(extra)
    return result


def _proof(timestamp: int, path: str, seed: int, source_sha: str = _SOURCE_SHA):
    return {
        "kind": "single_circle",
        "symbol": "○",
        "box": [595, 829, 611, 845],
        "method": "strict_terminal_ring_geometry",
        "coordinate_space": "gameplay_crop",
        "source_timestamp_ms": timestamp,
        "evidence": path,
        "gameplay_sha256": _hex(seed * 3 + 1),
        "source_frame_sha256": _hex(seed * 3 + 2),
        "evidence_sha256": _hex(seed * 3 + 3),
        "source_sha256": source_sha,
    }


def _fixture(names=("Alpha Skill ○", "Alp ha Skill ○"), times=None,
             title="A Hint for Growth"):
    if times is None:
        times = list(range(100, 100 + len(names) * 100, 100))
    effects = [_effect(name) for name in names]
    rows = {}
    field_evidence = {}
    for index, (effect, timestamp) in enumerate(zip(effects, times)):
        path = f"receipt/frame-{index}.png"
        proof = _proof(timestamp, path, index + 1)
        row_effect = deepcopy(effect)
        row_effect["visual_symbol_observation"] = deepcopy(proof)
        line = {
            "text": effect["raw_text"],
            "confidence": 99.0,
            "box": [316, 823, 768, 851],
            "visual_symbol_observation": deepcopy(proof),
        }
        rows[path] = {
            "evidence": path,
            "source_timestamp_ms": timestamp,
            "screen": "event_outcome",
            "context_title": title,
            "effects": [row_effect],
            "ocr": {"neural": [line]},
        }
        field_evidence[f"skill_hint_change||{effect['name']}"] = [path]
    event = {
        "first_seen_ms": min(times),
        "last_seen_ms": max(times),
        "context_title": title,
        "effects": effects,
        "field_evidence": field_evidence,
        "conflicting_readings": [],
    }
    return event, rows


class HintIdentityQuarantineTests(unittest.TestCase):
    def test_production_aggregation_keeps_quarantine_after_all_hint_fallbacks(self):
        from tracen_replay.transactions import outcome_events
        _, mapping = _fixture(('Alpha Skill ○', 'Alpho Skill ○'))
        readings = list(mapping.values())
        for row in readings:
            row.update(stats={}, facts={})
            for effect in row['effects']:
                effect['confidence'] = 99
        original = deepcopy(readings)
        event = outcome_events(readings)[0]
        self.assertEqual(event['effects'], [])
        self.assertEqual({c['effect']['name'] for c in event['ambiguous_effect_candidates']},
                         {'Alpha Skill ○', 'Alpho Skill ○'})
        self.assertTrue(all(c['accepted_award'] is False for c in event['ambiguous_effect_candidates']))
        self.assertEqual(readings, original)

    def test_quarantines_all_same_slot_variants_and_preserves_proofs(self):
        event, rows = _fixture((
            "Pace Chaser Savvy ○", "Pace Cha.er Savvy ○",
            "PaChaser Savvy ○", "Pare Chaser Savvy ○",
        ))
        original_rows = deepcopy(rows)
        original_amounts = [effect["amount"] for effect in event["effects"]]

        quarantine_receipt_identity_conflicts(event, rows,
                                              effect_kind="skill_hint_change")

        self.assertEqual(event["effects"], [])
        candidates = event["ambiguous_effect_candidates"]
        self.assertEqual({item["effect"]["name"] for item in candidates}, {
            "Pace Chaser Savvy ○", "Pace Cha.er Savvy ○",
            "PaChaser Savvy ○", "Pare Chaser Savvy ○",
        })
        self.assertEqual([item["effect"]["amount"] for item in candidates],
                         original_amounts)
        self.assertTrue(all(item["accepted_award"] is False
                            and item["occurrence_count"] is None
                            and item["continuity_proven"] is False
                            for item in candidates))
        self.assertTrue(all(item["source_continuity"]["observations"]
                            for item in candidates))
        self.assertEqual(len(event["conflicting_readings"]), 4)
        self.assertEqual(rows, original_rows)

    def test_is_idempotent_and_keeps_existing_candidate(self):
        event, rows = _fixture()
        quarantine_receipt_identity_conflicts(event, rows)
        first = deepcopy(event)
        quarantine_receipt_identity_conflicts(event, rows)
        self.assertEqual(event, first)

    def test_context_free_inheritance_style_scroll_stays_active(self):
        event, rows = _fixture(("First Hint ○", "Second Hint ○"), title=None)
        event["context_title"] = None
        for row in rows.values():
            row["context_title"] = None
        before = deepcopy(event)
        quarantine_receipt_identity_conflicts(event, rows)
        self.assertEqual(event, before)

    def test_changed_context_is_not_continuity(self):
        event, rows = _fixture()
        rows["receipt/frame-1.png"]["context_title"] = "Other Event"
        before = deepcopy(event)
        quarantine_receipt_identity_conflicts(event, rows)
        self.assertEqual(event, before)

    def test_known_navigation_between_rows_blocks_quarantine(self):
        event, rows = _fixture()
        rows["navigation"] = {
            "evidence": "navigation",
            "source_timestamp_ms": 150,
            "screen": "career_hub",
            "context_title": None,
        }
        before = deepcopy(event)
        quarantine_receipt_identity_conflicts(event, rows)
        self.assertEqual(event, before)

    def test_different_vertical_slots_remain_separate(self):
        event, rows = _fixture()
        row = rows["receipt/frame-1.png"]
        row["ocr"]["neural"][0]["box"] = [316, 880, 768, 908]
        before = deepcopy(event)
        quarantine_receipt_identity_conflicts(event, rows)
        self.assertEqual(event, before)

    def test_different_amounts_and_ranks_remain_separate(self):
        for mutation in ("amount", "value"):
            with self.subTest(mutation=mutation):
                event, rows = _fixture()
                changed = event["effects"][1]
                row_effect = rows["receipt/frame-1.png"]["effects"][0]
                if mutation == "amount":
                    changed["amount"] = row_effect["amount"] = 2
                    changed["raw_text"] = row_effect["raw_text"] = (
                        "Gained 2 hint level(s) for Alp ha Skill ○.")
                    rows["receipt/frame-1.png"]["ocr"]["neural"][0]["text"] = changed["raw_text"]
                else:
                    changed["value"] = row_effect["value"] = "rank-2"
                before = deepcopy(event)
                quarantine_receipt_identity_conflicts(event, rows)
                self.assertEqual(event, before)

    def test_same_timestamp_does_not_establish_continuity(self):
        event, rows = _fixture(times=[100, 100])
        before = deepcopy(event)
        quarantine_receipt_identity_conflicts(event, rows)
        self.assertEqual(event, before)

    def test_long_gap_does_not_merge_separate_receipt_visits(self):
        event, rows = _fixture(times=[100, 851])
        before = deepcopy(event)
        quarantine_receipt_identity_conflicts(event, rows)
        self.assertEqual(event, before)

    def test_reused_source_frame_proof_does_not_supply_two_observations(self):
        event, rows = _fixture()
        first = rows["receipt/frame-0.png"]["effects"][0]["visual_symbol_observation"]
        second = rows["receipt/frame-1.png"]["effects"][0]["visual_symbol_observation"]
        second["source_frame_sha256"] = first["source_frame_sha256"]
        rows["receipt/frame-1.png"]["ocr"]["neural"][0]["visual_symbol_observation"] = deepcopy(second)
        before = deepcopy(event)
        quarantine_receipt_identity_conflicts(event, rows)
        self.assertEqual(event, before)

    def test_source_hash_mismatch_blocks_group(self):
        event, rows = _fixture()
        proof = rows["receipt/frame-1.png"]["effects"][0]["visual_symbol_observation"]
        proof["source_sha256"] = "b" * 64
        rows["receipt/frame-1.png"]["ocr"]["neural"][0]["visual_symbol_observation"] = deepcopy(proof)
        before = deepcopy(event)
        quarantine_receipt_identity_conflicts(event, rows)
        self.assertEqual(event, before)

    def test_missing_image_hash_blocks_observation(self):
        event, rows = _fixture()
        proof = rows["receipt/frame-1.png"]["effects"][0]["visual_symbol_observation"]
        del proof["source_frame_sha256"]
        rows["receipt/frame-1.png"]["ocr"]["neural"][0]["visual_symbol_observation"] = deepcopy(proof)
        before = deepcopy(event)
        quarantine_receipt_identity_conflicts(event, rows)
        self.assertEqual(event, before)

    def test_preexisting_field_conflict_is_not_overridden(self):
        event, rows = _fixture()
        event["conflicting_readings"] = [{
            "field": "skill_hint_change||Alpha Skill ○",
            "reason": "existing_conflict",
        }]
        before = deepcopy(event)
        quarantine_receipt_identity_conflicts(event, rows)
        self.assertEqual(event, before)

    def test_low_confidence_and_missing_symbol_proof_do_not_pass(self):
        for mutation in ("confidence", "proof"):
            with self.subTest(mutation=mutation):
                event, rows = _fixture()
                line = rows["receipt/frame-1.png"]["ocr"]["neural"][0]
                if mutation == "confidence":
                    line["confidence"] = 94.99
                else:
                    line.pop("visual_symbol_observation")
                before = deepcopy(event)
                quarantine_receipt_identity_conflicts(event, rows)
                self.assertEqual(event, before)


if __name__ == "__main__":
    unittest.main()
