from copy import deepcopy
import hashlib
import json
from pathlib import Path
import unittest

from tracen_replay.lesson_offer_adapter import (
    SCHEMA,
    LessonOfferSourceError,
    adapt_lesson_offer_frame,
)


BASE = Path(".local/full-recording/independent-02/initial-baseline")
RAW_094 = BASE / "neural/part-005-frame-000094.json"
RAW_121 = BASE / "neural/part-005-frame-000121.json"
GAMEPLAY_094 = BASE / "gameplay/part-005-frame-000094.png"
GAMEPLAY_121 = BASE / "gameplay/part-005-frame-000121.png"
SOURCE_094 = BASE / "part-005/frames/000094.jpg"
SOURCE_121 = BASE / "part-005/frames/000121.jpg"
SOURCE_SHA256 = "a10bd8261176e2aa79eb991ab0eb6b4b23dc4c8a6ffe13edcb799d43e8982313"


def _load(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def _values(offer):
    return [price["value"] for price in offer["prices"]]


def _offer(result, name):
    return next(item for item in result["offers"] if item["name"] == name)


class LessonOfferAdapterTests(unittest.TestCase):
    def setUp(self):
        required = (
            RAW_094, RAW_121, GAMEPLAY_094, GAMEPLAY_121, SOURCE_094, SOURCE_121,
        )
        if not all(path.is_file() for path in required):
            self.skipTest("frozen independent-02 T029 source is not present")

    def test_actual_t029_frame_094_groups_three_offers_and_keeps_preview_boundary(self):
        raw = _load(RAW_094)
        result = adapt_lesson_offer_frame(
            raw, gameplay_path=GAMEPLAY_094, source_frame_path=SOURCE_094,
            source_sha256=SOURCE_SHA256,
        )

        self.assertEqual(result["schema_version"], SCHEMA)
        self.assertEqual(result["screen"], "lesson_selection")
        self.assertTrue(result["preview_only"])
        self.assertFalse(result["committed"])
        self.assertTrue(result["receipt_required"])
        self.assertEqual(
            [offer["name"] for offer in result["offers"]],
            [
                "Audience Involvement Basics",
                "Group Lesson Basics",
                "Vocal Training Basics",
            ],
        )
        self.assertEqual(
            _offer(result, "Audience Involvement Basics")["effects"],
            [{"kind": "stat_change", "field": "stamina", "amount": 5}],
        )
        hint = _offer(result, "Group Lesson Basics")["effects"]
        self.assertEqual(hint[0]["kind"], "skill_hint_change")
        self.assertEqual(hint[0]["category"], "skill_hint")
        self.assertEqual(hint[0]["level"], "medium")
        self.assertEqual(hint[0]["amount"], 1)
        self.assertNotIn("name", hint[0])
        self.assertEqual(
            _offer(result, "Vocal Training Basics")["effects"],
            [{"kind": "stat_change", "field": "power", "amount": 5}],
        )

        # The raw sidecar omitted some zero glyphs.  They remain unknown here;
        # no zero is manufactured from the current performance-point panel.
        self.assertEqual(_values(_offer(result, "Audience Involvement Basics")), [0, 10, 0, 0, None])
        self.assertEqual(_values(_offer(result, "Group Lesson Basics")), [0, 15, 0, None, None])
        self.assertEqual(_values(_offer(result, "Vocal Training Basics")), [0, 0, 10, 0, None])
        self.assertTrue(all(offer["phase"] == "preview" for offer in result["offers"]))
        self.assertTrue(all("receipt" not in offer for offer in result["offers"]))

        proof = result["source_proof"]
        self.assertEqual(proof["source_sha256"], SOURCE_SHA256)
        self.assertEqual(proof["evidence_sha256"], "5ba9dede75cf3f3363c21c023dbcc484c2db9dd0fc1a56ac0a42043667f7bef2")
        self.assertEqual(proof["gameplay_sha256"], "6000231166352cf4a25b73c8ea934e31110a30d69d7d2ccf4300b81d7c72c9ee")
        self.assertEqual(proof["source_frame_sha256"], "c8712be6af0dd7dbfd98bb0e0f6fc446ab9e0f15da45a3c6d6e990a8ff161f8a")
        self.assertTrue(proof["source_frame_verified"])

    def test_actual_t029_frame_121_preserves_two_effects_on_one_offer(self):
        result = adapt_lesson_offer_frame(
            _load(RAW_121), gameplay_path=GAMEPLAY_121, source_frame_path=SOURCE_121,
            source_sha256=SOURCE_SHA256,
        )
        self.assertEqual(
            [offer["name"] for offer in result["offers"]],
            [
                "Group Lesson Intermediate",
                "Audience Involvement Intermediate Class",
                "Isolation Basics",
            ],
        )
        isolation = _offer(result, "Isolation Basics")
        self.assertEqual(
            isolation["effects"],
            [
                {"kind": "stat_change", "field": "speed", "amount": 4},
                {"kind": "stat_change", "field": "wit", "amount": 4},
            ],
        )
        self.assertEqual(isolation["cost"], {
            "dance": 8, "passion": 0, "vocal": 0, "visual": 0, "composure": 8,
        })
        self.assertEqual(_values(_offer(result, "Group Lesson Intermediate")), [0, 0, 0, 0, 25])
        self.assertEqual(
            _offer(result, "Group Lesson Intermediate")["effects"][0],
            {
                "kind": "skill_hint_change",
                "category": "skill_hint",
                "amount": 2,
                "level": "medium",
                "name_visible": False,
                "raw_label": "Skill Hint Lvl +2 (Medium)",
            },
        )
        self.assertEqual(
            result["source_proof"]["evidence_sha256"],
            "5f386622ea07f48f9808708ef99c6ed9c197e70b6f981baf00b358c94e8c8041",
        )
        self.assertEqual(
            result["source_proof"]["source_frame_sha256"],
            "69d716e737d974c5fb02c278938f256e6e909f66546ca1d3e7cdf64c3267b145",
        )

        # The grouped card is one offer identity even though it has two
        # effect fields; it cannot turn into two purchase observations.
        self.assertEqual(len(result["observations"]), 3)
        self.assertEqual(
            len({item["offer_id"] for item in result["offers"]}),
            3,
        )
        self.assertEqual(
            _offer(result, "Isolation Basics")["offer_id"],
            next(item for item in result["observations"] if item["payload"]["name"] == "Isolation Basics")["offer_id"],
        )

    def test_actual_negative_frames_do_not_promote_receipt_or_non_menu(self):
        for frame_id in ("000119", "000120", "000123", "000124"):
            raw_path = BASE / f"neural/part-005-frame-{frame_id}.json"
            gameplay_path = BASE / f"gameplay/part-005-frame-{frame_id}.png"
            if not raw_path.is_file() or not gameplay_path.is_file():
                continue
            result = adapt_lesson_offer_frame(
                _load(raw_path), gameplay_path=gameplay_path,
            )
            self.assertEqual(result["offers"], [], frame_id)
            self.assertEqual(result["observations"], [], frame_id)
            self.assertTrue(result["preview_only"], frame_id)
            self.assertFalse(result["committed"], frame_id)

    def test_source_hash_mismatch_and_committed_phase_fail_closed(self):
        raw = _load(RAW_094)
        stale = deepcopy(raw)
        stale["gameplay_sha256"] = "0" * 64
        with self.assertRaisesRegex(LessonOfferSourceError, "gameplay"):
            adapt_lesson_offer_frame(stale, gameplay_path=GAMEPLAY_094)

        bound = deepcopy(raw)
        bound["source_sha256"] = SOURCE_SHA256
        with self.assertRaisesRegex(LessonOfferSourceError, "source hash"):
            adapt_lesson_offer_frame(bound, source_sha256="f" * 64)

        committed = deepcopy(raw)
        committed["phase"] = "committed"
        result = adapt_lesson_offer_frame(committed)
        self.assertEqual(result["offers"], [])
        self.assertEqual(result["rejected"], ["not_lesson_selection"])

    def test_low_confidence_effect_is_unknown_without_splitting_card(self):
        raw = _load(RAW_121)
        changed = deepcopy(raw)
        line = next(item for item in changed["lines"] if item["text"] == "Wit +4")
        line["confidence"] = 20
        result = adapt_lesson_offer_frame(changed)
        isolation = _offer(result, "Isolation Basics")
        self.assertEqual(
            isolation["effects"],
            [{"kind": "stat_change", "field": "speed", "amount": 4}],
        )
        self.assertIn("unreadable_effect", isolation["unknown_reasons"])
        self.assertEqual(len(result["observations"]), 3)

    def test_result_has_no_timestamps_run_ids_or_balance_guidance(self):
        raw = _load(RAW_094)
        result = adapt_lesson_offer_frame(raw)

        def walk(value):
            if isinstance(value, dict):
                for key, child in value.items():
                    yield key
                    yield from walk(child)
            elif isinstance(value, list):
                for child in value:
                    yield from walk(child)

        keys = set(walk(result))
        self.assertNotIn("source_timestamp_ms", keys)
        self.assertNotIn("run", keys)
        self.assertNotIn("run_id", keys)
        self.assertNotIn("expected", keys)
        self.assertNotIn("balance", keys)
        self.assertNotIn("performance_points_current", keys)

    def test_input_and_source_line_proof_are_immutable_copies(self):
        raw = _load(RAW_094)
        before = deepcopy(raw)
        result = adapt_lesson_offer_frame(raw)
        self.assertEqual(raw, before)
        raw["lines"][10]["text"] = "Changed"
        self.assertEqual(result["offers"][0]["title"]["text"], "Audience Involvement Basics")
        self.assertEqual(
            result["source_proof"]["raw_sha256"],
            hashlib.sha256(
                json.dumps(before, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
            ).hexdigest(),
        )

    def test_actual_late_song_title_keeps_raw_ocr_and_source_note_identity(self):
        root = Path('.local/final-reliability-v1/worker-runs/fourth-declared-retest-v10')
        raw_path = root / 'neural/part-015-frame-000216.json'
        gameplay_path = root / 'gameplay/part-015-frame-000216.png'
        if not raw_path.is_file() or not gameplay_path.is_file():
            self.skipTest('late fourth source pixels are unavailable')
        raw = _load(raw_path)
        result = adapt_lesson_offer_frame(raw, gameplay_path=gameplay_path)
        offer = next(item for item in result['offers'] if item['title']['text'] == 'Present March >')
        self.assertEqual(offer['name'], 'Present March ♪')
        self.assertEqual(offer['payload']['name'], 'Present March ♪')
        self.assertEqual(offer['title']['text'], 'Present March >')
        self.assertEqual(offer['source_symbol']['symbol'], '♪')
        self.assertEqual(offer['source_symbol']['coordinate_space'], 'gameplay_crop')
        self.assertNotIn('>', offer['name'])

        # The raw neural row alone cannot authorize an arrow cleanup.  The
        # source pane is the proof boundary for both the marker and the UI
        # glyph removal.
        unbound = adapt_lesson_offer_frame(raw)
        raw_offer = next(item for item in unbound['offers'] if item['title']['text'] == 'Present March >')
        self.assertEqual(raw_offer['name'], 'Present March >')
        self.assertNotIn('source_symbol', raw_offer)


if __name__ == "__main__":
    unittest.main()
