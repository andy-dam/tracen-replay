import copy
from types import SimpleNamespace
import unittest

import numpy as np
from PIL import Image, ImageDraw

from tracen_replay.lesson_offer_refinement import (
    LessonOfferError,
    apply,
    build,
    file_fingerprint,
    fingerprint,
    find_offer_cards,
    gameplay_fingerprint,
    observe,
)
from tests.test_gameplay import workspace_temp


class _TextRecInput:
    def __init__(self, *, img):
        self.img = img


class FakeReader:
    def __init__(self, texts, scores=None):
        self.models = {"det": "det-digest", "rec": "rec-digest"}
        self.fingerprint = "lesson-offer-test-engine"
        self.TextRecInput = _TextRecInput
        self.np = np
        self.calls = []
        self._texts = list(texts)
        self._scores = list(scores if scores is not None else [0.99] * len(self._texts))
        self.engine = SimpleNamespace(text_rec=self._read)

    def _read(self, request):
        self.calls.append(request)
        return SimpleNamespace(txts=self._texts, scores=self._scores)


class LessonOfferRefinementTests(unittest.TestCase):
    def pane(self):
        pane = Image.new("RGB", (810, 1080), (19, 27, 61))
        draw = ImageDraw.Draw(pane)
        for top, color in ((180, (60, 50, 100)), (410, (70, 60, 120)), (650, (80, 70, 140))):
            draw.rectangle((120, top, 690, min(top + 215, 1079)), fill=color)
        return pane

    def raw(self, pane=None):
        pane = self.pane() if pane is None else pane
        lines = [
            {"text": "Ring Ring Diary", "confidence": 99.2, "box": [294, 193, 453, 232]},
            {"text": "Performance Point Cost", "confidence": 99.1, "box": [280, 367, 410, 387]},
            {"text": "Go This Way", "confidence": 99.3, "box": [295, 423, 428, 460]},
            {"text": "Performance Point Cost", "confidence": 99.0, "box": [280, 597, 412, 619]},
            {"text": "Zero Is Where the Center Stands!", "confidence": 99.4, "box": [306, 661, 614, 687]},
            {"text": "Performance Point Cost", "confidence": 99.0, "box": [288, 824, 417, 847]},
        ]
        return {
            "screen": "lesson_selection",
            "source_timestamp_ms": 522750,
            "evidence": "gameplay.png",
            "source_frame_evidence": "frames/000172.jpg",
            "source_frame_sha256": "f" * 64,
            "gameplay_sha256": gameplay_fingerprint(pane),
            "model_sha256": {"source": "source-model-digest"},
            "engine_fingerprint": "source-engine-fingerprint",
            "lines": lines,
        }

    def reader(self, texts=None, scores=None):
        texts = texts or ["0", "21", "0", "21", "0", "0", "0", "21", "0", "21", "21", "0", "0", "21", "0"]
        return FakeReader(texts, scores)

    def build_extra(self, raw=None, texts=None, scores=None):
        pane = self.pane()
        raw = self.raw(pane) if raw is None else raw
        return pane, raw, build(pane, raw, "e" * 64, self.reader(texts, scores))

    def test_three_cards_keep_their_own_five_prices(self):
        pane, raw, extra = self.build_extra()
        self.assertEqual(len(extra["cards"]), 3)
        self.assertEqual([card["title"]["text"] for card in extra["cards"]],
                         ["Ring Ring Diary", "Go This Way", "Zero Is Where the Center Stands!"])
        self.assertEqual([[price["value"] for price in card["prices"]] for card in extra["cards"]],
                         [[0, 21, 0, 21, 0], [0, 0, 21, 0, 21], [21, 0, 0, 21, 0]])
        observed = observe(pane, raw, extra)
        self.assertEqual(observed["complete_offer_count"], 3)
        self.assertEqual(observed["offers"][2]["prices"][0]["field"], "dance")
        self.assertEqual(observed["offers"][2]["prices"][0]["value"], 21)

    def test_numpy_reader_scores_are_preserved_as_percent_confidence(self):
        pane, raw, extra = self.build_extra(scores=[np.float32(0.99)] * 15)
        observed = observe(pane, raw, extra)
        self.assertEqual(observed["complete_offer_count"], 3)
        self.assertEqual(observed["offers"][0]["prices"][0]["confidence"], 99.0)

    def test_apply_attaches_offers_without_booking_a_purchase(self):
        pane, raw, extra = self.build_extra()
        with workspace_temp() as root:
            evidence = root / "gameplay.png"
            pane.save(evidence)
            extra["evidence_sha256"] = file_fingerprint(evidence)
            row = {"screen": "lesson_selection", "facts": {"performance_points": {"dance": 38}}}
            applied = apply(row, raw, extra, evidence)
        self.assertIn("lesson_offer_observations", applied["facts"])
        self.assertNotIn("lesson_purchases", applied)
        self.assertNotIn("performance_cost", applied["facts"])
        self.assertFalse(applied["facts"]["lesson_offer_refinement_provenance"]["independent_observations"])

    def test_confirmation_and_training_preview_are_ineligible(self):
        pane = self.pane()
        for screen in ("lesson_confirmation", "training_preview"):
            raw = self.raw(pane)
            raw["screen"] = screen
            reader = self.reader()
            extra = build(pane, raw, "e" * 64, reader)
            self.assertEqual(find_offer_cards(raw), [])
            self.assertEqual(extra["cards"], [])
            self.assertEqual(reader.calls, [])
            self.assertEqual(observe(pane, raw, extra)["offers"], [])

    def test_low_confidence_zero_remains_unknown(self):
        texts = ["0", "21", "0", "21", "0", "0", "0", "21", "0", "21", "21", "0", "0", "21", "0"]
        scores = [0.99] * len(texts)
        scores[2] = 0.96
        pane, raw, extra = self.build_extra(texts=texts, scores=scores)
        observed = observe(pane, raw, extra)
        price = observed["offers"][0]["prices"][2]
        self.assertIsNone(price["value"])
        self.assertEqual(price["unknown_reason"], "low_confidence")
        self.assertEqual(observed["offers"][0]["status"], "unknown")

    def test_lone_letter_o_reads_as_zero_and_records_the_substitution(self):
        # Price slots hold digits only; the recognizer emits the letter O for
        # a lone zero glyph often enough that the row was dropped.  The value
        # is accepted under the normal confidence gate and the substitution is
        # recorded so the sidecar stays auditable.
        texts = ["0", "21", "O", "21", "0", "0", "0", "21", "0", "21", "21", "0", "0", "21", "0"]
        pane, raw, extra = self.build_extra(texts=texts)
        observed = observe(pane, raw, extra)
        price = observed["offers"][0]["prices"][2]
        self.assertEqual(price["value"], 0)
        self.assertEqual(price["text"], "0")
        self.assertEqual(price["digit_normalization"], {"observed_text": "O", "rule": "letter_o_to_zero"})

    def test_other_letters_stay_ambiguous(self):
        texts = ["0", "21", "D", "21", "0", "0", "0", "21", "0", "21", "21", "0", "0", "21", "0"]
        pane, raw, extra = self.build_extra(texts=texts)
        observed = observe(pane, raw, extra)
        self.assertIsNone(observed["offers"][0]["prices"][2]["value"])
        self.assertEqual(observed["offers"][0]["prices"][2]["unknown_reason"], "ambiguous_digit")

    def test_missing_crop_result_keeps_the_slot_unknown(self):
        texts = ["0", "21", "0", "21", "0", "0", "0", "21", "0", "21", "21", "0", "0", "21"]
        pane, raw, extra = self.build_extra(texts=texts)
        observed = observe(pane, raw, extra)
        last = observed["offers"][2]["prices"][4]
        self.assertIsNone(last["value"])
        self.assertEqual(last["unknown_reason"], "missing_digit")

    def test_missing_title_does_not_turn_prices_into_an_offer(self):
        pane = self.pane()
        raw = self.raw(pane)
        raw["lines"] = [line for line in raw["lines"] if line["text"] != "Go This Way"]
        extra = build(pane, raw, "e" * 64, self.reader())
        observed = observe(pane, raw, extra)
        second = observed["offers"][1]
        self.assertEqual(second["status"], "unknown")
        self.assertIn("ambiguous_or_missing_title", second["unknown_reasons"])
        self.assertTrue(all(price["value"] is not None for price in second["prices"]))

    def test_ambiguous_title_geometry_is_unknown(self):
        pane = self.pane()
        raw = self.raw(pane)
        raw["lines"].append({"text": "Another Candidate", "confidence": 99.0, "box": [520, 200, 650, 225]})
        extra = build(pane, raw, "e" * 64, self.reader())
        observed = observe(pane, raw, extra)
        self.assertEqual(observed["offers"][0]["status"], "unknown")
        self.assertIn("ambiguous_or_missing_title", observed["offers"][0]["unknown_reasons"])

    def test_clipped_cost_row_keeps_five_explicit_unknown_slots(self):
        pane = self.pane()
        raw = self.raw(pane)
        raw["lines"][-2:] = [
            {"text": "Bottom Card", "confidence": 99.0, "box": [294, 890, 450, 920]},
            {"text": "Performance Point Cost", "confidence": 99.0, "box": [280, 1060, 410, 1075]},
        ]
        texts = ["0", "21", "0", "21", "0", "0", "0", "21", "0", "21"]
        extra = build(pane, raw, "e" * 64, self.reader(texts))
        observed = observe(pane, raw, extra)
        third = observed["offers"][2]
        self.assertEqual(third["status"], "unknown")
        self.assertEqual(len(third["prices"]), 5)
        self.assertTrue(all(price["value"] is None for price in third["prices"]))
        self.assertTrue(all(price["unknown_reason"] == "clipped_or_invalid_cost_row" for price in third["prices"]))
        self.assertTrue(all(price["crop_box"] is None for price in third["prices"]))

    def test_changed_source_pixels_are_rejected(self):
        pane, raw, extra = self.build_extra()
        pane.putpixel((0, 0), (255, 0, 0))
        with self.assertRaisesRegex(LessonOfferError, "pixels"):
            observe(pane, raw, extra)

    def test_malformed_huge_geometry_and_confidence_fail_closed(self):
        pane = self.pane()
        raw = self.raw(pane)
        raw["lines"][0]["box"] = [10**400, 193, 10**400 + 1, 232]
        cards = find_offer_cards(raw)
        self.assertEqual(len(cards), 3)
        self.assertIsNone(cards[0]["title"])
        self.assertIn("ambiguous_or_missing_title", cards[0]["unknown_reasons"])

        pane, raw, extra = self.build_extra()
        broken = copy.deepcopy(extra)
        broken["cards"][0]["prices"][0]["confidence"] = 10**400
        broken["cards_sha256"] = fingerprint(broken["cards"])
        with self.assertRaises(LessonOfferError):
            observe(pane, raw, broken)

    def test_price_sidecar_cannot_drop_a_slot_or_change_a_value(self):
        pane, raw, extra = self.build_extra()
        broken = copy.deepcopy(extra)
        broken["cards"][0]["prices"].pop()
        broken["cards_sha256"] = fingerprint(broken["cards"])
        with self.assertRaisesRegex(LessonOfferError, "five price slots"):
            observe(pane, raw, broken)

        broken = copy.deepcopy(extra)
        broken["cards"][0]["prices"][0]["value"] = 21
        broken["cards_sha256"] = fingerprint(broken["cards"])
        with self.assertRaisesRegex(LessonOfferError, "numeric observation"):
            observe(pane, raw, broken)


if __name__ == "__main__":
    unittest.main()
