import hashlib
import json
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


if __name__ == "__main__":
    unittest.main()
