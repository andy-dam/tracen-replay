import copy
import hashlib
import json
from pathlib import Path
import unittest

from PIL import Image, ImageDraw

from tracen_replay.refine_concert_panels import (
    CONTRAST_FACTOR,
    RESIZE_SCALE,
    TIGHT_CROP_BOX,
    generate,
)
from tests.test_gameplay import workspace_temp


class FakeReader:
    models = {"fake-recognition.onnx": "fake-model-hash"}
    fingerprint = "fake-reader-fingerprint"

    def __init__(self, text="Lvl 0", confidence=91.25, responses=None):
        self.text = text
        self.confidence = confidence
        self.responses = list(responses or ())
        self.crops = []

    def recognize_crop(self, crop):
        self.crops.append(crop.copy())
        if len(self.crops) <= len(self.responses):
            response = self.responses[len(self.crops) - 1]
            if isinstance(response, dict):
                return response
            return {"text": response[0], "confidence": response[1]}
        return {"text": self.text, "confidence": self.confidence}


class RefineConcertPanelsTests(unittest.TestCase):
    def setUp(self):
        self.template = json.loads(Path("tests/fixtures/concert-panels.json").read_text(encoding="utf-8"))[0]["raw"]

    def _run(self, root, times=(1000, 1250, 1500, 1750), slot_text="Lvl O", confidence=89.528):
        run = Path(root)
        (run / "neural").mkdir(parents=True)
        (run / "gameplay").mkdir(parents=True)
        part = run / "part-001" / "frames"
        part.mkdir(parents=True)
        rows = []
        for index, timestamp in enumerate(times, start=1):
            frame_id = f"part-001-frame-{index:06d}"
            source_path = part / f"{index:06d}.jpg"
            source_image = Image.new("RGB", (1920, 1080), (20, 30, 40))
            ImageDraw.Draw(source_image).rectangle(TIGHT_CROP_BOX, fill=(50 * index, 80, 100))
            source_image.save(source_path, format="JPEG")
            source_hash = hashlib.sha256(source_path.read_bytes()).hexdigest()
            row = {
                "id": frame_id,
                "source_timestamp_ms": timestamp,
                "source_pts": 60 + index * 15,
                "time_base": "1/60",
                "evidence": f"part-001/frames/{index:06d}.jpg",
            }
            rows.append(row)
            raw = copy.deepcopy(self.template)
            raw["source_timestamp_ms"] = timestamp
            raw["evidence"] = f"gameplay/{frame_id}.png"
            raw["source_frame_sha256"] = source_hash
            raw["lines"] = [
                dict(line, text=slot_text, confidence=confidence)
                if line.get("text") == "Lvl O"
                else line
                for line in raw["lines"]
            ]
            (run / raw["evidence"]).parent.mkdir(parents=True, exist_ok=True)
            Image.new("RGB", (810, 1080), (5, 6, 7)).save(run / raw["evidence"], format="PNG")
            (run / "neural" / f"{frame_id}.json").write_text(json.dumps(raw), encoding="utf-8")
        (run / "part-001" / "frames.json").write_text(json.dumps(rows), encoding="utf-8")
        capture = {
            "source": {"sha256": "recording-hash"},
            "frames": rows,
        }
        (run / "capture.json").write_text(json.dumps(capture), encoding="utf-8")
        return rows

    def test_selects_nearby_cached_frames_and_writes_provenance_bound_artifact(self):
        with workspace_temp() as root:
            self._run(root)
            reader = FakeReader()
            summary = generate(root, reader_factory=lambda _: reader)

            self.assertEqual(summary["artifact_count"], 1)
            self.assertEqual(summary["rejected_count"], 0)
            artifact_path = Path(summary["artifacts"][0])
            artifact = json.loads(artifact_path.read_text(encoding="utf-8"))
            self.assertEqual(artifact["source_sha256"], "recording-hash")
            self.assertEqual(artifact["crop_box"], list(TIGHT_CROP_BOX))
            self.assertEqual(artifact["preprocessing"]["resize_scale"], RESIZE_SCALE)
            self.assertEqual(artifact["preprocessing"]["resize_resample"], "LANCZOS")
            self.assertEqual(artifact["preprocessing"]["contrast_factor"], CONTRAST_FACTOR)
            self.assertEqual(artifact["reader_fingerprint"], reader.fingerprint)
            self.assertEqual(artifact["model_sha256"], reader.models)
            self.assertEqual([item["timestamp_ms"] for item in artifact["observations"]], [1000, 1250, 1500, 1750])
            self.assertEqual(
                [item["timestamp_ms"] for item in artifact["all_observations"]],
                [1000, 1000, 1250, 1250, 1500, 1500, 1750, 1750],
            )
            self.assertEqual(artifact["ocr_variants"], ["gray", "contrast"])
            self.assertEqual(artifact["measurement_count"], 8)
            self.assertEqual(len(artifact["observations"]), 4)
            self.assertEqual(len(artifact["rejected_observations"]), 4)
            self.assertEqual({item["variant"] for item in artifact["all_observations"]}, {"gray", "contrast"})
            self.assertEqual(
                {item["preprocessing"]["contrast_factor"] for item in artifact["all_observations"]},
                {1.0, CONTRAST_FACTOR},
            )
            self.assertTrue(all(item["crop_box"] == list(TIGHT_CROP_BOX) for item in artifact["observations"]))
            self.assertTrue(all(Path(root, item["evidence"]).is_file() for item in artifact["all_observations"]))
            self.assertTrue(all(Path(root, item["source_frame_evidence"]).is_file() for item in artifact["observations"]))
            self.assertTrue(all(Path(root, item["panel_raw_evidence"]).is_file() for item in artifact["observations"]))
            self.assertTrue(all(Path(root, item["source_manifest_evidence"]).is_file() for item in artifact["observations"]))
            self.assertEqual([crop.size for crop in reader.crops], [(380, 188)] * 8)
            self.assertEqual({crop.mode for crop in reader.crops}, {"RGB"})
            self.assertEqual(
                [item["variant"] for item in artifact["all_observations"]],
                ["gray", "contrast"] * 4,
            )
            self.assertEqual(
                len({item["evidence"] for item in artifact["all_observations"]}),
                8,
            )
            self.assertTrue(
                all(item["evidence"].endswith(f"-{item['variant']}.png") for item in artifact["all_observations"])
            )

            # The artifact was already passed through apply() by generate();
            # verify its stored crop hashes are still directly reproducible.
            for item in artifact["observations"]:
                self.assertEqual(item["evidence_sha256"], hashlib.sha256((Path(root) / item["evidence"]).read_bytes()).hexdigest())
                self.assertEqual(item["source_frame_sha256"], hashlib.sha256((Path(root) / item["source_frame_evidence"]).read_bytes()).hexdigest())

    def test_transition_panel_is_rejected_without_ocr(self):
        with workspace_temp() as root:
            self._run(root, times=(1000, 1250, 1500), slot_text="Lvl 0 > Lvl 3")
            reader = FakeReader()
            summary = generate(root, reader_factory=lambda _: reader)

            self.assertEqual(summary["artifact_count"], 0)
            self.assertEqual(len(reader.crops), 0)
            rejected = [json.loads(Path(path).read_text(encoding="utf-8")) for path in summary["rejected"]]
            self.assertTrue(any(item["reason"] == "support_slot_transition" for item in rejected))

    def test_unknown_panel_is_retained_as_rejection(self):
        with workspace_temp() as root:
            self._run(root, times=(1000, 1250, 1500), slot_text="Support unknown", confidence=95)
            reader = FakeReader()
            summary = generate(root, reader_factory=lambda _: reader)

            self.assertEqual(summary["artifact_count"], 0)
            self.assertEqual(len(reader.crops), 0)
            rejected = [json.loads(Path(path).read_text(encoding="utf-8")) for path in summary["rejected"]]
            self.assertTrue(any(item["reason"] == "support_slot_unknown" for item in rejected))

    def test_far_frames_are_not_used_as_nearby_support(self):
        with workspace_temp() as root:
            self._run(root, times=(1000, 1250, 1500, 120000))
            reader = FakeReader()
            summary = generate(root, reader_factory=lambda _: reader)

            self.assertEqual(summary["artifact_count"], 1)
            self.assertEqual(len(reader.crops), 6)
            artifact = json.loads(Path(summary["artifacts"][0]).read_text(encoding="utf-8"))
            self.assertNotIn(120000, [item["timestamp_ms"] for item in artifact["observations"]])
            rejected = [json.loads(Path(path).read_text(encoding="utf-8")) for path in summary["rejected"]]
            self.assertTrue(any(item["reason"] == "insufficient_same_context_frames" for item in rejected))

    def test_existing_artifact_is_never_overwritten(self):
        with workspace_temp() as root:
            self._run(root, times=(1000, 1250, 1500))
            output = Path(root) / "concert-panel-refinement"
            output.mkdir()
            existing = output / "part-001-frame-000001.json"
            existing.write_text('{"sentinel": true}\n', encoding="utf-8")
            reader = FakeReader()
            summary = generate(root, reader_factory=lambda _: reader)

            self.assertEqual(summary["artifact_count"], 0)
            self.assertEqual(len(reader.crops), 0)
            self.assertEqual(existing.read_text(encoding="utf-8"), '{"sentinel": true}\n')
            rejected = [json.loads(Path(path).read_text(encoding="utf-8")) for path in summary["rejected"]]
            self.assertTrue(any(item["reason"] == "existing_artifact" for item in rejected))

    def test_capture_row_absence_is_rejected_before_ocr(self):
        with workspace_temp() as root:
            self._run(root, times=(1000, 1250, 1500))
            capture_path = Path(root) / "capture.json"
            capture = json.loads(capture_path.read_text(encoding="utf-8"))
            capture["frames"] = capture["frames"][1:]
            capture_path.write_text(json.dumps(capture), encoding="utf-8")
            reader = FakeReader()

            summary = generate(root, reader_factory=lambda _: reader)

            self.assertEqual(summary["artifact_count"], 0)
            self.assertEqual(len(reader.crops), 0)
            rejected = [json.loads(Path(path).read_text(encoding="utf-8")) for path in summary["rejected"]]
            self.assertTrue(any(item["reason"] == "capture_row_missing" for item in rejected))

    def test_missing_or_malformed_capture_is_rejected_before_ocr(self):
        for mode in ("missing", "malformed"):
            with self.subTest(mode=mode), workspace_temp() as root:
                self._run(root, times=(1000, 1250, 1500))
                capture_path = Path(root) / "capture.json"
                if mode == "missing":
                    capture_path.unlink()
                else:
                    capture_path.write_text("[]", encoding="utf-8")
                reader = FakeReader()

                summary = generate(root, reader_factory=lambda _: reader)

                self.assertEqual(summary["artifact_count"], 0)
                self.assertEqual(len(reader.crops), 0)
                rejected = [json.loads(Path(path).read_text(encoding="utf-8")) for path in summary["rejected"]]
                self.assertTrue(any(item["reason"] == f"capture_{mode}" for item in rejected))

    def test_outside_root_source_path_is_rejected_without_reading_it(self):
        with workspace_temp() as root:
            self._run(root, times=(1000, 1250, 1500))
            outside_evidence = "../outside-source.jpg"
            part_path = Path(root) / "part-001" / "frames.json"
            part_rows = json.loads(part_path.read_text(encoding="utf-8"))
            part_rows[0]["evidence"] = outside_evidence
            part_path.write_text(json.dumps(part_rows), encoding="utf-8")
            capture_path = Path(root) / "capture.json"
            capture = json.loads(capture_path.read_text(encoding="utf-8"))
            capture["frames"][0]["evidence"] = outside_evidence
            capture_path.write_text(json.dumps(capture), encoding="utf-8")
            reader = FakeReader()

            summary = generate(root, reader_factory=lambda _: reader)

            self.assertEqual(summary["artifact_count"], 0)
            self.assertEqual(len(reader.crops), 0)
            rejected = [json.loads(Path(path).read_text(encoding="utf-8")) for path in summary["rejected"]]
            self.assertTrue(any(item["reason"] == "source_identity_mismatch" for item in rejected))

    def test_two_views_of_one_frame_do_not_satisfy_independent_frame_count(self):
        with workspace_temp() as root:
            self._run(root, times=(1000,))
            reader = FakeReader()
            summary = generate(root, reader_factory=lambda _: reader)

            self.assertEqual(summary["artifact_count"], 0)
            # Candidate selection requires three distinct source frames before
            # creating either view; the two views from one frame cannot enter
            # the support sequence as two independent observations.
            self.assertEqual(len(reader.crops), 0)
            rejected = [json.loads(Path(path).read_text(encoding="utf-8")) for path in summary["rejected"]]
            self.assertTrue(any(item["reason"] == "insufficient_same_context_frames" for item in rejected))

    def test_high_confidence_conflicting_views_abstain(self):
        responses = [
            ("Lvl 0", 95),
            ("Lvl 1", 95),
            ("Lvl 0", 95),
            ("Lvl 0", 95),
            ("Lvl 0", 95),
            ("Lvl 0", 95),
        ]
        with workspace_temp() as root:
            self._run(root, times=(1000, 1250, 1500))
            reader = FakeReader(responses=responses)
            summary = generate(root, reader_factory=lambda _: reader)

            self.assertEqual(summary["artifact_count"], 0)
            self.assertEqual(len(reader.crops), 6)
            rejected = [json.loads(Path(path).read_text(encoding="utf-8")) for path in summary["rejected"]]
            self.assertTrue(any(item["reason"] == "refinement_abstained" for item in rejected))


if __name__ == "__main__":
    unittest.main()
