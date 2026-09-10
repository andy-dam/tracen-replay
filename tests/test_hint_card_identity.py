import hashlib
import json
from copy import deepcopy
from pathlib import Path
import shutil
import uuid
import unittest
from unittest.mock import patch

from tracen_replay import hint_card_identity
from tracen_replay.hint_card_identity import recover


try:
    from PIL import Image
except ImportError:  # pragma: no cover - the project test environment has Pillow
    Image = None


SOURCE_SHA = "a" * 64


@unittest.skipIf(Image is None, "Pillow is required for source-bound image tests")
class HintCardIdentityTests(unittest.TestCase):
    def setUp(self):
        # Keep test assets under the repository's workspace test area; managed
        # Windows hosts may deny writes to Python-created temp directories.
        self.root = Path(".local/test-runs") / uuid.uuid4().hex
        self.root.mkdir(parents=True)
        (self.root / "gameplay").mkdir()
        (self.root / "neural").mkdir()
        (self.root / "part-000" / "frames").mkdir(parents=True)

        self.rows = []
        frames = []
        for index, timestamp in enumerate((1000, 1250, 1500)):
            frame_id = f"part-000-frame-{index:06d}"
            evidence = f"gameplay/{frame_id}.png"
            source_frame = f"part-000/frames/{index:06d}.jpg"
            image_path = self.root / evidence
            source_path = self.root / source_frame
            # Distinct pixels make each timestamp a real source observation;
            # the implementation must reject copied PNG bytes as duplicates.
            image = Image.new("RGB", (810, 1080), (index + 1, 20, 30))
            image.save(image_path)
            image.save(source_path, format="JPEG", quality=92)
            with Image.open(image_path) as opened:
                gameplay_sha = hashlib.sha256(opened.convert("RGB").tobytes()).hexdigest()
            source_frame_sha = hashlib.sha256(source_path.read_bytes()).hexdigest()
            lines = self._lines(index)
            raw = {
                "lines": lines,
                "engine_fingerprint": "engine",
                "model_sha256": {"model.onnx": "b" * 64},
                "gameplay_sha256": gameplay_sha,
                "source_timestamp_ms": timestamp,
                "evidence": evidence,
                "source_frame_sha256": source_frame_sha,
            }
            (self.root / "neural" / f"{frame_id}.json").write_text(
                json.dumps(raw), encoding="utf-8"
            )
            frames.append(
                {
                    "id": frame_id,
                    "source_timestamp_ms": timestamp,
                    "evidence": source_frame,
                }
            )
            self.rows.append(self._row(timestamp, evidence, lines, index))

        (self.root / "capture.json").write_text(
            json.dumps({"source": {"sha256": SOURCE_SHA}, "frames": frames}),
            encoding="utf-8",
        )

        self.reader = type("Reader", (), {"fingerprint": "prefix-engine"})()

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)

    @staticmethod
    def _lines(index):
        names = ("Wintr Runner", "Winnter Runner O", "Wilter Runner")
        return [
            {
                "text": "HINT.UP!" if index == 0 else "HINT.LvI UP!",
                "confidence": 96.0,
                "box": [382 + index, 591 + index, 715 - index, 657 - index],
            },
            {
                "text": "Winter Runner",
                "confidence": 99.0,
                "box": [402 + (index > 0), 677 + (index > 0), 566 - index, 704 - (index > 0)],
            },
            {
                "text": "Gained 2 hint level(s) for " + names[index] + ".",
                "confidence": 96.0,
                "box": [316, 852 + (index == 2), 730 + (index == 2), 881],
            },
            {"text": "Event", "confidence": 99.0, "box": [281, 201, 474, 233]},
        ]

    @staticmethod
    def _row(timestamp, evidence, lines, index, *, context="Event"):
        header, card, receipt = lines[:3]
        receipt_box = receipt["box"]
        overlay = [604 - (index * 5), 854 - (index == 2) * 5, 617 - (index * 5), 871 - (index == 2) * 4]
        return {
            "screen": "event_outcome",
            "source_timestamp_ms": timestamp,
            "evidence": evidence,
            "context_title": context,
            "context_title_candidate": context,
            "ocr": {"neural": lines},
            "facts": {
                "occluded_receipt_lines": [
                    {
                        "text": receipt["text"],
                        "confidence": receipt["confidence"],
                        "box": receipt_box,
                        "overlay_boxes": [overlay],
                        "recipient_name_occluded": False,
                    }
                ],
                "receipt_overlay_evidence": {"overlay_boxes": [overlay]},
            },
        }

    def _patched_recover(self, rows=None, *, detect_side_effect=None, prefix_side_effect=None):
        rows = self.rows if rows is None else rows
        if detect_side_effect is None:
            detect_side_effect = ["single_circle"] * 3
        if prefix_side_effect is None:
            prefix_side_effect = []
            for row in rows:
                item = hint_card_identity._static_row(row)
                if item is None:
                    prefix_side_effect.append(None)
                    continue
                prefix_side_effect.append(
                    {
                        "amount": 2,
                        "raw_name": "Wint",
                        "recognized_text": "Gained 2 hint level(s) for Wint",
                        "confidence": 94.0,
                        "crop_box": [
                            item["receipt"]["box"][0],
                            item["receipt"]["box"][1],
                            item["overlay"][0],
                            item["receipt"]["box"][3],
                        ],
                        "basis": "independent_prefix_crop_ocr_to_verified_overlay_boundary",
                    }
                )
        with (
            patch("tracen_replay.vision.NeuralReader", return_value=self.reader),
            patch("tracen_replay.inventory_suffix.detect", side_effect=detect_side_effect),
            patch(
                "tracen_replay.receipt_occlusion.overlay_boxes",
                side_effect=lambda image: [
                    [
                        604 - (index := int(image.getpixel((0, 0))[0]) - 1) * 5,
                        854 - (index == 2) * 5,
                        617 - index * 5,
                        871 - (index == 2) * 4,
                    ]
                ],
            ),
            patch("tracen_replay.hint_card_identity._prefix_amount_ocr", side_effect=prefix_side_effect),
        ):
            return recover(rows, self.root, source_sha256=SOURCE_SHA)

    def _make_wrapped_rows(self, *, card_text="Wrapped Skill", continuation="Skill."):
        rows = deepcopy(self.rows[:2])
        for index, row in enumerate(rows):
            header = {
                "text": "HINT.UP!" if index == 0 else "HINT.LvI UP!",
                "confidence": 96.0,
                "box": [382 + index, 591 + index, 715 - index, 657 - index],
            }
            card = {
                "text": card_text,
                "confidence": 99.0,
                "box": [402 + index, 677 + index, 566 - index, 704 + index],
            }
            prefix = {
                "text": f"Gained 2 hint l:ve Wrapped",
                "confidence": 96.0,
                "box": [316 + index, 852 - index * 10, 688 + index, 884 - index * 10],
            }
            suffix = {
                "text": continuation,
                "confidence": 99.0,
                "box": [316 + index, 878 - index * 10, 418 + index, 906 - index * 10],
            }
            lines = [header, card, prefix, suffix]
            row["ocr"] = {"neural": lines}
            row.pop("context_title", None)
            row.pop("context_title_candidate", None)
            row["facts"] = {}
            frame_id = Path(row["evidence"]).stem
            raw_path = self.root / "neural" / f"{frame_id}.json"
            raw = json.loads(raw_path.read_text(encoding="utf-8"))
            raw["lines"] = lines
            raw_path.write_text(json.dumps(raw), encoding="utf-8")
        return rows

    def _wrapped_amount_proofs(self, rows, *, amount=2, model_fingerprint=None):
        model_fingerprint = model_fingerprint or ("d" * 64)
        proofs = []
        for row in rows:
            item = hint_card_identity._wrapped_static_row(row)
            if item is None:
                proofs.append(None)
                continue
            crop_box = hint_card_identity._wrapped_amount_crop_box(item["receipt"]["box"])
            self.assertIsNotNone(crop_box)
            delimiter_box = hint_card_identity._wrapped_amount_delimiter_crop_box(item["receipt"]["box"])
            self.assertIsNotNone(delimiter_box)
            image_path = self.root / row["evidence"]
            with Image.open(image_path) as opened:
                image = opened.convert("RGB")
                left, top, right, bottom = (int(value) for value in crop_box)
                crop = image.crop((left - 148, top, right - 148, bottom))
                pixel_sha256 = hashlib.sha256(crop.tobytes()).hexdigest()
                left, top, right, bottom = (int(value) for value in delimiter_box)
                delimiter_crop = image.crop((left - 148, top, right - 148, bottom))
                delimiter_sha256 = hashlib.sha256(delimiter_crop.tobytes()).hexdigest()
            proofs.append(
                {
                    "amount": amount,
                    "recognized_text": str(amount),
                    "confidence": 94.0,
                    "crop_box": hint_card_identity._box_list(crop_box),
                    "pixel_rgb_sha256": pixel_sha256,
                    "basis": "source_bound_amount_digit_crop_ocr",
                    "model_fingerprint": model_fingerprint,
                    "delimiter_proof": {
                        "recognized_text": "hint",
                        "confidence": 94.0,
                        "crop_box": hint_card_identity._box_list(delimiter_box),
                        "pixel_rgb_sha256": delimiter_sha256,
                        "basis": "amount_digit_followed_by_hint_delimiter_ocr",
                        "model_fingerprint": model_fingerprint,
                    },
                }
            )
        return proofs

    def _patched_wrapped_recover(self, rows=None, *, proofs=None, detect_side_effect=None):
        rows = self._make_wrapped_rows() if rows is None else rows
        if proofs is None:
            proofs = self._wrapped_amount_proofs(rows)
        if detect_side_effect is None:
            detect_side_effect = [None] * len(rows)
        reader = type("Reader", (), {"fingerprint": "d" * 64})()
        with (
            patch("tracen_replay.vision.NeuralReader", return_value=reader),
            patch("tracen_replay.inventory_suffix.detect", side_effect=detect_side_effect),
            patch("tracen_replay.hint_card_identity._wrapped_amount_ocr", side_effect=proofs),
        ):
            return recover(rows, self.root, source_sha256=SOURCE_SHA)

    def test_valid_source_chain_recovers_card_and_preserves_raw_names(self):
        candidates = self._patched_recover()
        self.assertEqual(len(candidates), 1)
        candidate = candidates[0]
        self.assertEqual(candidate["kind"], "skill_hint_change")
        self.assertEqual(candidate["name"], "Winter Runner")
        self.assertEqual(candidate["amount"], 2)
        self.assertEqual(candidate["source_timestamps_ms"], [1000, 1250, 1500])
        self.assertEqual(
            candidate["raw_receipt_name_candidates"],
            ["Wilter Runner", "Winnter Runner O", "Wintr Runner"],
        )
        self.assertEqual(
            {item["card"]["suffix"] for item in candidate["observations"]},
            {"single_circle"},
        )
        self.assertTrue(all(item["source_frame_sha256"] for item in candidate["observations"]))

    def test_intervening_unreadable_row_breaks_episode(self):
        rows = list(self.rows)
        rows[1] = dict(rows[1], screen="unknown")
        self.assertEqual(self._patched_recover(rows), [])

    def test_intervening_context_change_breaks_episode(self):
        rows = list(self.rows)
        rows[1] = dict(rows[1], context_title="Different", context_title_candidate="Different")
        self.assertEqual(self._patched_recover(rows), [])

    def test_conflicting_suffix_abstains(self):
        self.assertEqual(
            self._patched_recover(detect_side_effect=["single_circle", "double_circle", "single_circle"]),
            [],
        )

    def test_conflicting_prefix_amount_abstains(self):
        proofs = [
            {"amount": 2, "raw_name": "Wint", "recognized_text": "x", "confidence": 94.0, "crop_box": [316, 852, 604, 881], "basis": "test"},
            {"amount": 3, "raw_name": "Wint", "recognized_text": "x", "confidence": 94.0, "crop_box": [316, 852, 604, 881], "basis": "test"},
            {"amount": 2, "raw_name": "Wint", "recognized_text": "x", "confidence": 94.0, "crop_box": [316, 852, 604, 881], "basis": "test"},
        ]
        self.assertEqual(self._patched_recover(prefix_side_effect=proofs), [])

    def test_same_png_bytes_do_not_count_as_distinct_timestamps(self):
        duplicate_path = self.root / self.rows[1]["evidence"]
        shutil.copyfile(self.root / self.rows[0]["evidence"], duplicate_path)
        self.assertEqual(self._patched_recover(), [])

    def test_same_timestamp_with_different_evidence_is_rejected(self):
        duplicate = dict(self.rows[1], source_timestamp_ms=self.rows[0]["source_timestamp_ms"], evidence=self.rows[1]["evidence"])
        self.assertEqual(self._patched_recover(self.rows[:1] + [duplicate] + self.rows[2:]), [])

    def test_tampered_gameplay_proof_is_rejected(self):
        raw_path = self.root / "neural" / "part-000-frame-000001.json"
        raw = json.loads(raw_path.read_text(encoding="utf-8"))
        raw["gameplay_sha256"] = "c" * 64
        raw_path.write_text(json.dumps(raw), encoding="utf-8")
        self.assertEqual(self._patched_recover(), [])

    def test_missing_capture_or_raw_binding_is_rejected(self):
        (self.root / "capture.json").unlink()
        self.assertEqual(self._patched_recover(), [])

    def test_out_of_scope_rows_fail_closed(self):
        malformed = dict(self.rows[1])
        malformed.pop("evidence")
        self.assertEqual(self._patched_recover(self.rows[:1] + [malformed] + self.rows[2:]), [])

    def test_wrapped_receipt_recovers_with_unknown_rank_and_context(self):
        rows = self._make_wrapped_rows()
        candidates = self._patched_wrapped_recover(rows)
        self.assertEqual(len(candidates), 1)
        candidate = candidates[0]
        self.assertEqual(candidate["observation_kind"], "wrapped_hint_receipt")
        self.assertEqual(candidate["name"], "Wrapped Skill")
        self.assertEqual(candidate["amount"], 2)
        self.assertEqual(candidate["source_timestamps_ms"], [1000, 1250])
        self.assertEqual(candidate["identity_proof"]["rank_state"], "undetermined")
        self.assertIsNone(candidate["identity_proof"]["suffix"])
        self.assertIsNone(candidate["identity_proof"]["same_context"])
        self.assertTrue(
            all(
                observation["card"]["suffix_state"] == "undetermined"
                and observation["prefix_amount_proof"]["amount"] == 2
                and observation["receipt_parts"]["continuation"]["text"] == "Skill."
                for observation in candidate["observations"]
            )
        )

    def test_wrapped_receipt_requires_amount_proof_on_every_timestamp(self):
        rows = self._make_wrapped_rows()
        proofs = self._wrapped_amount_proofs(rows)
        proofs[1] = None
        self.assertEqual(self._patched_wrapped_recover(rows, proofs=proofs), [])

    def test_wrapped_amount_boundary_rejects_truncated_two_digit_source(self):
        """A one-digit crop cannot overrule a digit in the adjacent source slot."""
        from types import SimpleNamespace

        for source_text, first_reading, boundary_reading in (
            ('20', '2', '0 hint'),
            ('12', '1', '2 hint'),
        ):
            with self.subTest(source_text=source_text):
                image = Image.new('RGB', (810, 1080), 'white')
                # Coordinates are gameplay-pane pixels; the production crop
                # converts its full-frame source x values by subtracting 148.
                from PIL import ImageDraw
                ImageDraw.Draw(image).text((395 - 148, 852), source_text, fill='black')

                class Engine:
                    def __init__(self):
                        self.calls = 0

                    def text_rec(self, _input):
                        self.calls += 1
                        value = first_reading if self.calls == 1 else boundary_reading
                        return SimpleNamespace(txts=[value], scores=[0.99])

                reader = SimpleNamespace(
                    engine=Engine(), TextRecInput=lambda **kwargs: kwargs,
                    fingerprint='d' * 64,
                )
                receipt = {'amount': int(first_reading), 'box': [316, 852, 688, 884]}
                self.assertIsNone(hint_card_identity._wrapped_amount_ocr(reader, image, receipt))

    def test_wrapped_receipt_rejects_card_change(self):
        rows = self._make_wrapped_rows()
        second = rows[1]["ocr"]["neural"]
        second[1]["text"] = "Other Skill"
        raw_path = self.root / "neural" / f"{Path(rows[1]['evidence']).stem}.json"
        raw = json.loads(raw_path.read_text(encoding="utf-8"))
        raw["lines"] = second
        raw_path.write_text(json.dumps(raw), encoding="utf-8")
        self.assertEqual(self._patched_wrapped_recover(rows), [])

    def test_wrapped_receipt_rejects_continuation_mismatch(self):
        rows = self._make_wrapped_rows()
        rows[1]["ocr"]["neural"][3]["text"] = "Other."
        raw_path = self.root / "neural" / f"{Path(rows[1]['evidence']).stem}.json"
        raw = json.loads(raw_path.read_text(encoding="utf-8"))
        raw["lines"] = rows[1]["ocr"]["neural"]
        raw_path.write_text(json.dumps(raw), encoding="utf-8")
        self.assertEqual(self._patched_wrapped_recover(rows), [])

    def test_wrapped_receipt_rejects_context_or_screen_boundary(self):
        rows = self._make_wrapped_rows()
        rows[1]["context_title"] = "Another event"
        rows[1]["context_title_candidate"] = "Another event"
        self.assertEqual(self._patched_wrapped_recover(rows), [])
        rows = self._make_wrapped_rows()
        rows[1]["screen"] = "training"
        self.assertEqual(self._patched_wrapped_recover(rows), [])

    def test_wrapped_receipt_rejects_overlay_obscured_prefix_and_continuation(self):
        rows = self._make_wrapped_rows()
        prefix = rows[0]["ocr"]["neural"][2]
        prefix["overlay_boxes"] = [[522, 862, 535, 877]]
        raw_path = self.root / "neural" / f"{Path(rows[0]['evidence']).stem}.json"
        raw = json.loads(raw_path.read_text(encoding="utf-8"))
        raw["lines"] = rows[0]["ocr"]["neural"]
        raw_path.write_text(json.dumps(raw), encoding="utf-8")
        lines = hint_card_identity._wrapped_line_records(rows[0])
        retained_prefix = next(line for line in lines if line["text"] == prefix["text"])
        self.assertTrue(retained_prefix["overlay_occluded"])
        self.assertEqual(retained_prefix["overlay_boxes"], [[522, 862, 535, 877]])
        self.assertEqual(self._patched_wrapped_recover(rows), [])

    def test_wrapped_identity_fragment_accepts_source_clear_tail_partition(self):
        item = {
            "card": {"text": "Straightaway Recovery"},
            "receipt": {
                "box": [316, 860, 688, 892],
                "prefix": {
                    "text": "Gained 2 hint l:ve s.wor Straightaway",
                },
                "continuation": {"text": "Recovery."},
            },
            "overlay": [522, 862, 535, 877],
        }
        proof = {
            "basis": "source_bound_identity_fragment_tail_ocr",
            "recognized_text": "or Straightaway",
            "fragment": "Straightaway",
            "confidence": 93.486,
            "crop_box": [538, 858, 688, 894],
            "overlay_box": [522, 862, 535, 877],
            "pixel_rgb_sha256": "a" * 64,
            "model_fingerprint": "b" * 64,
        }
        self.assertEqual(
            hint_card_identity._wrapped_identity_fragment_from_tail(
                proof["recognized_text"], item["card"]["text"]
            ),
            "Straightaway",
        )
        self.assertTrue(
            hint_card_identity._wrapped_identity_fragment_proof_matches(
                proof, item
            )
        )

    def test_wrapped_identity_fragment_rejects_symbol_gap_in_receipt_partition(self):
        item = {
            "card": {"text": "Alpha % Beta"},
            "receipt": {
                "box": [316, 860, 688, 892],
                "prefix": {"text": "Gained 2 hint level(s) for Alpha"},
                "continuation": {"text": "Beta."},
            },
            "overlay": [522, 862, 535, 877],
        }
        proof = {
            "basis": "source_bound_identity_fragment_tail_ocr",
            "recognized_text": "or Alpha",
            "fragment": "Alpha",
            "confidence": 93.486,
            "crop_box": [538, 858, 688, 894],
            "overlay_box": [522, 862, 535, 877],
            "pixel_rgb_sha256": "a" * 64,
            "model_fingerprint": "b" * 64,
        }
        # Token-only matching would accept Alpha as a card prefix.  The
        # receipt fragments do not account for the visible '%' character, so
        # the identity proof must remain unusable.
        self.assertIsNotNone(
            hint_card_identity._wrapped_identity_fragment_from_tail(
                proof["recognized_text"], item["card"]["text"]
            )
        )
        self.assertFalse(
            hint_card_identity._wrapped_identity_fragment_proof_matches(
                proof, item
            )
        )

    def test_wrapped_identity_fragment_rejects_unrelated_leading_words(self):
        card = "Wrapped Skill"
        self.assertEqual(
            hint_card_identity._wrapped_identity_fragment_from_tail(
                "or Wrapped", card
            ),
            "Wrapped",
        )
        for text in ("Other Wrapped", "Alpha Wrapped", "for Other Wrapped"):
            with self.subTest(text=text):
                self.assertIsNone(
                    hint_card_identity._wrapped_identity_fragment_from_tail(
                        text, card
                    )
                )

        rows = self._make_wrapped_rows()
        continuation = rows[0]["ocr"]["neural"][3]
        continuation["overlay_boxes"] = [[316, 858, 410, 882]]
        raw_path = self.root / "neural" / f"{Path(rows[0]['evidence']).stem}.json"
        raw = json.loads(raw_path.read_text(encoding="utf-8"))
        raw["lines"] = rows[0]["ocr"]["neural"]
        raw_path.write_text(json.dumps(raw), encoding="utf-8")
        self.assertEqual(self._patched_wrapped_recover(rows), [])

    def test_wrapped_receipt_rejects_overlay_obscured_card_anchor(self):
        rows = self._make_wrapped_rows()
        rows[0]["ocr"]["neural"][1]["overlay_occluded"] = True
        raw_path = self.root / "neural" / f"{Path(rows[0]['evidence']).stem}.json"
        raw = json.loads(raw_path.read_text(encoding="utf-8"))
        raw["lines"] = rows[0]["ocr"]["neural"]
        raw_path.write_text(json.dumps(raw), encoding="utf-8")
        self.assertEqual(self._patched_wrapped_recover(rows), [])

    def test_wrapped_name_fragments_must_cover_card_without_gaps(self):
        self.assertIsNone(
            hint_card_identity._wrapped_name_fragment(
                "Gained 2 hint Alpha",
                "Gamma.",
                "Alpha Beta Gamma",
            )
        )
        self.assertIsNone(
            hint_card_identity._wrapped_name_fragment(
                "Gained 2 hint A-B",
                "C.",
                "A B C",
            )
        )

    def test_wrapped_name_fragments_preserve_literal_punctuation(self):
        # Token-only matching would drop ``%`` and falsely certify the card.
        self.assertIsNone(
            hint_card_identity._wrapped_name_fragment(
                "Gained 2 hint Alpha",
                "Beta.",
                "Alpha % Beta",
            )
        )
        # The terminal mark in a card name is meaningful; the receipt's own
        # terminal mark may be removed once, but a missing card mark cannot.
        self.assertIsNone(
            hint_card_identity._wrapped_name_fragment(
                "Gained 2 hint Alpha",
                "Beta.",
                "Alpha Beta!",
            )
        )
        self.assertEqual(
            hint_card_identity._wrapped_name_fragment(
                "Gained 2 hint Let's",
                "Go!",
                "Let's Go!",
            ),
            "Let's Go!",
        )


if __name__ == "__main__":
    unittest.main()
