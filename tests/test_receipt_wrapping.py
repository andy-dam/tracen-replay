import hashlib
import unittest
from types import SimpleNamespace

from PIL import Image

from tracen_replay.receipt_wrapping import (
    continuation_crop_box,
    enrich,
    join,
    prefix,
    status_prefix,
)
from tracen_replay.vision import parse


_PREFIX_BOX = [313, 831, 762, 860]
_AMOUNT_BOX = [320, 852, 345, 878]


def line(text, box, confidence=99):
    return {"text": text, "box": list(box), "confidence": confidence}


def raw(lines, **extra):
    return dict(
        lines=list(lines),
        regions={},
        header="Training",
        current_grid=False,
        result_grid=False,
        **extra,
    )


class _Reader:
    fingerprint = "a" * 64

    def __init__(self, texts=("9.",), scores=(0.97,)):
        self.texts = texts
        self.scores = scores
        self.requests = []
        self.engine = SimpleNamespace(text_rec=self._recognize)

    def _recognize(self, request):
        self.requests.append(request)
        return SimpleNamespace(txts=list(self.texts), scores=list(self.scores))

    TextRecInput = lambda self, **kwargs: SimpleNamespace(**kwargs)


class _FallbackReader(_Reader):
    """Leave the ordinary crop unresolved, then agree on both color views."""

    def _recognize(self, request):
        self.requests.append(request)
        if len(self.requests) == 1:
            return SimpleNamespace(txts=[], scores=[])
        return SimpleNamespace(txts=["9."], scores=[0.97])


class _ConflictingColorReader(_FallbackReader):
    def _recognize(self, request):
        self.requests.append(request)
        if len(self.requests) == 1:
            return SimpleNamespace(txts=[], scores=[])
        amount = "9." if len(self.requests) == 2 else "8."
        return SimpleNamespace(txts=[amount], scores=[0.97])


class ReceiptWrappingTests(unittest.TestCase):
    def _source(self, prefix_text="Friendship with Matikanefukukitaru went up by"):
        pane = Image.new("RGB", (810, 1080), "white")
        value = raw(
            [
                line(prefix_text, _PREFIX_BOX, 98.7),
                # This is a separate lower receipt and must never provide the
                # wrapped amount for the first line.
                line(
                    "Friendship with Agnes Tachyon went up by 9.",
                    [317, 878, 747, 908],
                    98.6,
                ),
            ],
            source_timestamp_ms=78767,
            evidence="receipt-inspection/frame-000024.png",
            source_sha256="c" * 64,
            source_frame_sha256="b" * 64,
            gameplay_sha256=hashlib.sha256(pane.tobytes()).hexdigest(),
            engine_fingerprint="a" * 64,
            model_sha256={"rec.onnx": "d" * 64},
        )
        return value, pane

    def test_continuation_crop_is_derived_from_prefix_geometry(self):
        self.assertEqual(continuation_crop_box(_PREFIX_BOX), [320, 852, 368, 878])
        self.assertIsNone(continuation_crop_box([313, 990, 762, 1010]))

    def test_detector_visible_standalone_amount_joins_same_receipt_only(self):
        source = raw(
            [
                line("Friendship with Matikanefukukitaru went up by", _PREFIX_BOX),
                line("9.", _AMOUNT_BOX),
                line("Friendship with Agnes Tachyon went up by 9.", [317, 878, 747, 908]),
            ]
        )
        effects = parse(source)["effects"]
        self.assertEqual(
            [(effect["kind"], effect["name"], effect["amount"]) for effect in effects],
            [
                ("friendship_change", "Matikanefukukitaru", 9),
                ("friendship_change", "Agnes Tachyon", 9),
            ],
        )

    def test_source_crop_proof_supplies_missing_continuation_without_neighbor_borrow(self):
        source, pane = self._source()
        reader = _Reader()
        enriched = enrich(source, pane, reader)
        self.assertEqual(len(reader.requests), 1)
        self.assertEqual(enriched["wrapped_receipt_observations"][0]["amount"], 9)
        proof = enriched["wrapped_receipt_observations"][0]["amount_proof"]
        self.assertEqual(proof["crop_box"], [320, 852, 368, 878])
        self.assertTrue(proof["pixel_rgb_sha256"])
        effects = parse(enriched)["effects"]
        self.assertEqual(
            [(effect["name"], effect["amount"]) for effect in effects],
            [("Matikanefukukitaru", 9), ("Agnes Tachyon", 9)],
        )
        wrapped = effects[0]
        self.assertEqual(wrapped["wrapped_receipt_proof"]["amount"], 9)
        self.assertEqual(wrapped["wrapped_receipt_parts"][1]["text"], "9.")

    def test_wrapped_fixed_keyword_deletion_keeps_original_text(self):
        source, pane = self._source("riendship with Matikanefukukitaru went up by")
        enriched = enrich(source, pane, _Reader())
        effects = parse(enriched)["effects"]
        wrapped = effects[0]
        self.assertEqual((wrapped["name"], wrapped["amount"]), ("Matikanefukukitaru", 9))
        self.assertEqual(wrapped["original_text"], "riendship with Matikanefukukitaru went up by 9.")
        self.assertEqual(wrapped["text_normalization"], "source_wrapped_friendship_receipt")

    def test_wrapped_maxed_status_keeps_identity_and_source_parts(self):
        source = raw(
            [
                line("Friendship with Matikanefukukitaru is maxed", [317, 878, 745, 906]),
                line("out.", [318, 905, 362, 928]),
                line("Friendship with Mihono Bourbon is maxed out.", [317, 855, 758, 880]),
            ]
        )
        effects = parse(source)["effects"]
        status = next(effect for effect in effects if effect["kind"] == "friendship_status"
                      and effect["name"] == "Matikanefukukitaru")
        self.assertEqual(status["value"], "maximum")
        self.assertEqual(status["original_text"],
                         "Friendship with Matikanefukukitaru is maxed out.")
        self.assertEqual(status["text_normalization"], "source_wrapped_friendship_status")
        self.assertEqual([part["text"] for part in status["wrapped_receipt_parts"]],
                         ["Friendship with Matikanefukukitaru is maxed", "out."])

    def test_wrapped_maxed_status_repairs_two_deleted_fixed_keyword_glyphs(self):
        source = raw(
            [
                line("Fendship with Matikanefukukitaru is maxed", [317, 878, 745, 906]),
                line("out.", [318, 905, 362, 928]),
            ]
        )
        effects = parse(source)["effects"]
        self.assertEqual(
            [(effect["kind"], effect["name"], effect["value"]) for effect in effects],
            [("friendship_status", "Matikanefukukitaru", "maximum")],
        )
        self.assertEqual(
            effects[0]["original_text"],
            "Fendship with Matikanefukukitaru is maxed out.",
        )
        self.assertEqual(
            effects[0]["text_normalization"],
            "source_wrapped_friendship_status",
        )

    def test_wrapped_fixed_keyword_rejects_unbounded_damage(self):
        for keyword in ("Fndshp", "Friendzzzip", "Xrienzzhip"):
            with self.subTest(keyword=keyword):
                source = raw(
                    [
                        line(f"{keyword} with Example Name is maxed", [317, 878, 745, 906]),
                        line("out.", [318, 905, 362, 928]),
                    ]
                )
                self.assertEqual(parse(source)["effects"], [])

    def test_wrapped_maxed_status_requires_one_adjacent_tail(self):
        cases = [
            [line("Friendship with Matikanefukukitaru is maxed", [317, 878, 745, 906])],
            [
                line("Friendship with Matikanefukukitaru is maxed", [317, 878, 745, 906]),
                line("out", [318, 905, 362, 928]),
            ],
            [
                line("Friendship with Matikanefukukitaru is maxed", [317, 878, 745, 906]),
                line("out.", [500, 905, 544, 928]),
            ],
            [
                line("Friendship with Matikanefukukitaru is maxed", [317, 878, 745, 906]),
                line("out.", [318, 905, 362, 928]),
                line("out!", [319, 932, 363, 955]),
            ],
            [
                line("Friendship with Matikanefukukitaru is maxed", [317, 878, 745, 906]),
                line("Energy went down by 1.", [317, 900, 560, 928]),
                line("out.", [318, 905, 362, 928]),
            ],
        ]
        for lines in cases:
            with self.subTest(lines=lines):
                effects = parse(raw(lines))["effects"]
                self.assertFalse(any(effect.get("kind") == "friendship_status"
                                     for effect in effects))

    def test_receipt_confidence_rejects_overflow_and_out_of_range_values(self):
        self.assertIsNone(prefix(line("Friendship with A went up by", _PREFIX_BOX, 101)))
        self.assertIsNone(status_prefix(line("Friendship with A is maxed", _PREFIX_BOX, 101)))
        self.assertIsNone(status_prefix(line("Friendship with A is maxed", _PREFIX_BOX, 10 ** 1000)))

    def test_color_views_recover_pointer_obscured_lower_band(self):
        source, pane = self._source("riendship with Matikanefukukitaru went up by")
        source["lines"][1]["confidence"] = 90
        reader = _FallbackReader()
        enriched = enrich(source, pane, reader)
        observations = enriched["wrapped_receipt_observations"]
        self.assertEqual(len(reader.requests), 3)
        self.assertEqual(observations[0]["amount"], 9)
        proof = observations[0]["amount_proof"]
        self.assertEqual(proof["basis"], "source_bound_wrapped_friendship_amount_color_consensus")
        self.assertEqual(proof["crop_box"], [310, 860, 348, 878])
        self.assertEqual({view["view"] for view in proof["views"]}, {"warm", "brown"})
        effects = parse(enriched)["effects"]
        self.assertEqual(
            [(effect["name"], effect["amount"]) for effect in effects],
            [("Matikanefukukitaru", 9)],
        )

    def test_unterminated_or_non_numeric_crop_does_not_project(self):
        for texts, scores in ((["9"], [0.99]), (["by 9."], [0.99]), (["9.", "8."] , [0.99, 0.99])):
            with self.subTest(texts=texts):
                source, pane = self._source()
                enriched = enrich(source, pane, _Reader(texts, scores))
                self.assertNotIn("wrapped_receipt_observations", enriched)
                self.assertEqual(
                    [effect["name"] for effect in parse(enriched)["effects"]],
                    ["Agnes Tachyon"],
                )

    def test_conflicting_color_views_abstain(self):
        source, pane = self._source("riendship with Matikanefukukitaru went up by")
        enriched = enrich(source, pane, _ConflictingColorReader())
        self.assertNotIn("wrapped_receipt_observations", enriched)

    def test_conflicting_visible_continuations_abstain(self):
        source = raw(
            [
                line("Friendship with Matikanefukukitaru went up by", _PREFIX_BOX),
                line("9.", _AMOUNT_BOX),
                line("8.", [320, 852, 345, 878]),
            ]
        )
        self.assertEqual(parse(source)["effects"], [])

    def test_invalid_metadata_cannot_inject_a_wrapped_effect(self):
        source, _pane = self._source()
        source["wrapped_receipt_observations"] = [{
            "version": "friendship-wrapped-receipt-v1",
            "method": "source_bound_wrapped_friendship_receipt",
            "source_pixel_verified": True,
            "complete_grammar": True,
            "gameplay_sha256": source["gameplay_sha256"],
            "prefix": line("Friendship with Matikanefukukitaru went up by", _PREFIX_BOX),
            "continuation_box": [320, 852, 368, 878],
            "amount": 9,
            "amount_proof": {
                "recognized_text": "9.",
                "amount": 9,
                "confidence": 99,
                "crop_box": [320, 852, 368, 878],
                "pixel_rgb_sha256": "c" * 64,
                "model_fingerprint": "a" * 64,
                "basis": "source_bound_wrapped_friendship_amount_crop_ocr",
            },
        }]
        # Missing source-frame binding means the facts-only object is ignored.
        self.assertEqual([effect["name"] for effect in parse(source)["effects"]], ["Agnes Tachyon"])

    def test_persisted_wrapped_proof_requires_source_namespace(self):
        source, pane = self._source()
        enriched = enrich(source, pane, _Reader())
        observation = enriched["wrapped_receipt_observations"][0]
        observation.pop("source_sha256")
        source["wrapped_receipt_observations"] = [observation]
        self.assertEqual(
            [effect["name"] for effect in parse(source)["effects"]],
            ["Agnes Tachyon"],
        )

    def test_persisted_wrapped_proof_requires_matching_reader_fingerprint(self):
        source, pane = self._source()
        enriched = enrich(source, pane, _Reader())
        observation = enriched["wrapped_receipt_observations"][0]
        observation["amount_proof"]["model_fingerprint"] = "b" * 64
        source["wrapped_receipt_observations"] = [observation]
        self.assertEqual(
            [effect["name"] for effect in parse(source)["effects"]],
            ["Agnes Tachyon"],
        )

    def test_persisted_wrapped_proof_requires_model_envelope(self):
        source, pane = self._source()
        enriched = enrich(source, pane, _Reader())
        observation = enriched["wrapped_receipt_observations"][0]
        observation.pop("model_sha256")
        source["wrapped_receipt_observations"] = [observation]
        self.assertEqual(
            [effect["name"] for effect in parse(source)["effects"]],
            ["Agnes Tachyon"],
        )


if __name__ == "__main__":
    unittest.main()
