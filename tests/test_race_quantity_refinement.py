import copy
import hashlib
import json
import unittest
from contextlib import contextmanager
from pathlib import Path
from PIL import Image

from tracen_replay.race_quantity_refinement import (
    FIXED_SLOT_FALLBACK_POLICY,
    MIN_CONFIDENCE,
    SLOT_BY_ID,
    _fallback_quantity_crop_box,
    _fixed_slot_pixel_guard,
    _source_crop_box,
    aggregate_slot,
    apply,
    generate,
    layout_guard,
)
from tracen_replay.vision import parse
from tests.test_gameplay import workspace_temp


FIXTURE = Path("tests/fixtures/race-quantity-refinement-v1.json")
SOURCE_FIXTURE = Path("tests/fixtures/race-quantity-refinement-source.png")


class FakeReader:
    models = {"fixture-recognition.onnx": "fixture-model-sha256"}
    fingerprint = "fixture-reader-fingerprint"

    def __init__(self, *args):
        self.calls = []

    def recognize_crops(self, crops):
        self.calls.append([crop.size for crop in crops])
        width = crops[0].width
        if crops[0].height == SLOT_BY_ID["items-0"]["source_box"][3] - SLOT_BY_ID["items-0"]["source_box"][1]:
            text, confidence = "x1", 98.4
        elif width <= 40:
            text, confidence = "x1", 98.2
        else:
            text, confidence = "x20", 96.5
        return [{"text": text, "confidence": confidence} for _ in crops]


class RaceQuantityRefinementTests(unittest.TestCase):
    def setUp(self):
        self.fixture = json.loads(FIXTURE.read_text(encoding="utf-8"))

    def _observation(self, timestamp, text, quantity, confidence):
        return {
            "timestamp_ms": timestamp,
            "text": text,
            "quantity": quantity,
            "confidence": confidence,
            "crop_evidence": f"crop-{timestamp}.png",
        }

    @contextmanager
    def _strict_generated_case(self):
        """Build a small source-bound run for mutation tests."""

        with workspace_temp() as root:
            run = Path(root)
            (run / "neural").mkdir(parents=True)
            (run / "gameplay").mkdir(parents=True)
            (run / "capture").mkdir(parents=True)
            rows = []
            for index, timestamp in enumerate((1000, 1250, 1500), start=1):
                frame_id = f"part-001-frame-{index:06d}"
                source_path = run / "capture" / f"{index:06d}.png"
                gameplay_path = run / "gameplay" / f"{frame_id}.png"
                with Image.open(SOURCE_FIXTURE) as fixture_image:
                    pane = fixture_image.convert("RGB")
                    full_frame = Image.new("RGB", (1920, 1080), "black")
                    full_frame.paste(pane, (148, 0))
                    full_frame.save(source_path, format="PNG")
                    pane.save(gameplay_path, format="PNG")
                source_hash = hashlib.sha256(source_path.read_bytes()).hexdigest()
                gameplay_pixels_hash = hashlib.sha256(pane.tobytes()).hexdigest()
                rows.append({
                    "id": frame_id,
                    "source_timestamp_ms": timestamp,
                    "source_pts": round(timestamp * 60 / 1000),
                    "time_base": "1/60",
                    "evidence": f"capture/{index:06d}.png",
                })
                raw = {
                    "lines": [
                        {"text": "Fans 12,882 (+11,638)", "confidence": 99, "box": [423, 539, 626, 566]},
                        {"text": "Items", "confidence": 99, "box": [278, 591, 328, 612]},
                        {"text": "x400", "confidence": 99, "box": [425, 699, 489, 727]},
                        {"text": "x1", "confidence": 90, "box": [566, 705, 597, 725]},
                        {"text": "Bonus", "confidence": 99, "box": [270, 750, 334, 778]},
                        {"text": "x400", "confidence": 99, "box": [310, 861, 378, 892]},
                    ],
                    "regions": {},
                    "header": "",
                    "current_grid": False,
                    "result_grid": False,
                    "engine_fingerprint": "base-reader",
                    "model_sha256": {"base.onnx": "base-model"},
                    "gameplay_sha256": gameplay_pixels_hash,
                    "source_timestamp_ms": timestamp,
                    "evidence": f"gameplay/{frame_id}.png",
                    "source_frame_sha256": source_hash,
                }
                (run / "neural" / f"{frame_id}.json").write_text(json.dumps(raw), encoding="utf-8")
            (run / "capture.json").write_text(json.dumps({"source": {"sha256": "f" * 64}, "frames": rows}), encoding="utf-8")
            reader = FakeReader()
            summary = generate(run, reader_factory=lambda _: reader)
            artifact = json.loads(Path(summary["artifacts"][0]).read_text(encoding="utf-8"))
            raw_path = run / "neural" / "part-001-frame-000001.json"
            raw = json.loads(raw_path.read_text(encoding="utf-8"))
            yield run, raw, parse(raw), artifact

    def test_same_quantity_on_distinct_timestamps_is_accepted(self):
        result = aggregate_slot([
            self._observation(1000, "x1", 1, 98.2),
            self._observation(1250, "x1", 1, 97.4),
            self._observation(1500, "x1", 1, 98.0),
        ])

        self.assertEqual(result["status"], "accepted")
        self.assertEqual(result["accepted"]["quantity"], 1)
        self.assertEqual(result["accepted"]["support_timestamps_ms"], [1000, 1250, 1500])
        self.assertEqual(result["accepted"]["independent_frame_count"], 3)
        self.assertEqual(result["accepted"]["confidence"], 97.4)

    def test_scale_views_from_one_timestamp_do_not_count_as_frames(self):
        result = aggregate_slot([
            self._observation(1000, "x1", 1, 99.0),
            self._observation(1000, "x1", 1, 99.1),
            self._observation(1000, "x1", 1, 99.2),
        ])

        self.assertEqual(result["status"], "insufficient_distinct_timestamps")
        self.assertIsNone(result["accepted"])

    def test_conflicting_high_confidence_views_abstain(self):
        result = aggregate_slot([
            self._observation(1000, "x1", 1, 98.5),
            self._observation(1000, "x400", 400, 98.1),
            self._observation(1250, "x1", 1, 98.5),
        ])

        self.assertEqual(result["status"], "conflict")
        self.assertIsNone(result["accepted"])
        self.assertEqual(result["conflicts"][0]["quantities"], [1, 400])

    def test_layout_guard_requires_race_items_and_bonus_sections(self):
        raw = {
            "lines": [
                {"text": "Items", "confidence": 99, "box": [278, 591, 328, 612]},
                {"text": "Bonus", "confidence": 99, "box": [270, 750, 334, 778]},
            ]
        }
        row = copy.deepcopy(self.fixture["row"])
        self.assertTrue(layout_guard(raw, row))
        self.assertFalse(layout_guard(raw, dict(row, screen="training_result")))
        self.assertFalse(layout_guard({"lines": raw["lines"][:1]}, row))

    def test_apply_adds_only_accepted_quantity_and_keeps_identity_incomplete(self):
        row = copy.deepcopy(self.fixture["row"])
        artifact = {
            "minimum_confidence": MIN_CONFIDENCE,
            "slots": [{
                "slot_id": "items-0",
                "full_box": list(SLOT_BY_ID["items-0"]["full_box"]),
                "accepted": {
                    "quantity": 1,
                    "text": "x1",
                    "confidence": 97.4,
                    "support_timestamps_ms": [1000, 1250],
                },
            }],
        }

        refined = apply(row, artifact)
        self.assertEqual([entry["quantity"] for entry in refined["facts"]["visible_item_quantities"]], [1, 400])
        self.assertIsNone(refined["facts"]["visible_item_quantities"][0]["name"])
        self.assertFalse(refined["facts"]["item_identity_verified"])
        self.assertFalse(refined["facts"]["item_rewards_complete"])

    def test_apply_does_not_duplicate_existing_position(self):
        row = copy.deepcopy(self.fixture["row"])
        row["facts"]["visible_item_quantities"].append({
            "quantity": 1,
            "name": None,
            "box": list(SLOT_BY_ID["items-0"]["full_box"]),
        })
        artifact = {
            "minimum_confidence": MIN_CONFIDENCE,
            "slots": [{
                "slot_id": "items-0",
                "full_box": list(SLOT_BY_ID["items-0"]["full_box"]),
                "accepted": {
                    "quantity": 1,
                    "text": "x1",
                    "confidence": 98,
                    "support_timestamps_ms": [1000, 1250],
                },
            }],
        }

        refined = apply(row, artifact)
        self.assertEqual(len(refined["facts"]["visible_item_quantities"]), 2)

    def test_production_threshold_cannot_be_lowered(self):
        with self.assertRaisesRegex(ValueError, "lowers the production confidence"):
            apply(self.fixture["row"], {"minimum_confidence": 95, "slots": []})

    def test_detector_slot_can_use_occupied_fixed_geometry_only_when_enabled(self):
        raw = {
            "lines": [
                {"text": "Items", "confidence": 99, "box": [278, 591, 328, 612]},
                {"text": "Bonus", "confidence": 99, "box": [270, 750, 334, 778]},
            ],
        }
        spec = SLOT_BY_ID["items-2"]

        self.assertEqual(_source_crop_box(raw, spec), (None, None))
        source_box, detector = _source_crop_box(
            raw,
            spec,
            allow_fixed_fallback=True,
            gameplay_path=SOURCE_FIXTURE,
        )
        self.assertEqual(source_box, _fallback_quantity_crop_box(spec["source_box"]))
        self.assertEqual(source_box, (425, 699, 462, 734))
        self.assertIsNone(detector)
        self.assertTrue(_fixed_slot_pixel_guard(SOURCE_FIXTURE, spec["source_box"]))

    def test_fixed_geometry_fallback_rejects_empty_background(self):
        with workspace_temp() as root:
            blank = Path(root) / "blank.png"
            Image.new("RGB", (810, 1080), (245, 245, 245)).save(blank)
            spec = SLOT_BY_ID["items-2"]
            raw = {"lines": []}

            self.assertFalse(_fixed_slot_pixel_guard(blank, spec["source_box"]))
            self.assertEqual(
                _source_crop_box(raw, spec, allow_fixed_fallback=True, gameplay_path=blank),
                (None, None),
            )

    def test_fixed_geometry_fallback_rejects_corrupt_geometry_and_pixels(self):
        spec = SLOT_BY_ID["items-2"]
        raw = {"lines": []}
        corrupt_spec = copy.deepcopy(spec)
        corrupt_spec["source_box"] = (385, 695, 385, 734)

        self.assertFalse(_fixed_slot_pixel_guard(SOURCE_FIXTURE, corrupt_spec["source_box"]))
        self.assertEqual(
            _source_crop_box(raw, corrupt_spec, allow_fixed_fallback=True, gameplay_path=SOURCE_FIXTURE),
            (None, None),
        )

    def test_generator_uses_stable_first_slot_and_detector_padding_for_third(self):
        with workspace_temp() as root:
            run = Path(root)
            (run / "neural").mkdir(parents=True)
            (run / "gameplay").mkdir(parents=True)
            (run / "capture").mkdir(parents=True)
            rows = []
            for index, timestamp in enumerate((1000, 1250, 1500), start=1):
                frame_id = f"part-001-frame-{index:06d}"
                source_path = run / "capture" / f"{index:06d}.png"
                gameplay_path = run / "gameplay" / f"{frame_id}.png"
                with Image.open(SOURCE_FIXTURE) as fixture_image:
                    pane = fixture_image.convert("RGB")
                    full_frame = Image.new("RGB", (1920, 1080), "black")
                    full_frame.paste(pane, (148, 0))
                    full_frame.save(source_path, format="PNG")
                    pane.save(gameplay_path, format="PNG")
                source_hash = hashlib.sha256(source_path.read_bytes()).hexdigest()
                gameplay_pixels_hash = hashlib.sha256(pane.tobytes()).hexdigest()
                capture_row = {
                    "id": frame_id,
                    "source_timestamp_ms": timestamp,
                    "source_pts": round(timestamp * 60 / 1000),
                    "time_base": "1/60",
                    "evidence": f"capture/{index:06d}.png",
                }
                rows.append(capture_row)
                detector_lines = [] if index > 1 else [{
                    "text": "x1", "confidence": 90, "box": [566, 705, 597, 725],
                }]
                raw = {
                    "lines": [
                        {"text": "Fans 12,882 (+11,638)", "confidence": 99, "box": [423, 539, 626, 566]},
                        {"text": "Items", "confidence": 99, "box": [278, 591, 328, 612]},
                        {"text": "x400", "confidence": 99, "box": [425, 699, 489, 727]},
                        *detector_lines,
                        {"text": "Bonus", "confidence": 99, "box": [270, 750, 334, 778]},
                        {"text": "x400", "confidence": 99, "box": [310, 861, 378, 892]},
                    ],
                    "regions": {},
                    "header": "",
                    "current_grid": False,
                    "result_grid": False,
                    "engine_fingerprint": "base-reader",
                    "model_sha256": {"base.onnx": "base-model"},
                    "gameplay_sha256": gameplay_pixels_hash,
                    "source_timestamp_ms": timestamp,
                    "evidence": f"gameplay/{frame_id}.png",
                    "source_frame_sha256": source_hash,
                }
                (run / "neural" / f"{frame_id}.json").write_text(json.dumps(raw), encoding="utf-8")
            capture = {"source": {"sha256": "f" * 64}, "frames": rows}
            (run / "capture.json").write_text(json.dumps(capture), encoding="utf-8")

            reader = FakeReader()
            summary = generate(run, reader_factory=lambda _: reader)

            self.assertEqual(summary["artifact_count"], 3)
            artifact = json.loads(Path(summary["artifacts"][0]).read_text(encoding="utf-8"))
            slots = {slot["slot_id"]: slot for slot in artifact["slots"]}
            self.assertEqual(set(slots), {"items-0", "items-2", "bonus-1"})
            self.assertEqual(slots["items-0"]["strategy"], "stable_badge_geometry")
            self.assertEqual(slots["items-0"]["status"], "accepted")
            self.assertEqual(slots["items-2"]["strategy"], "detector_box_padding_2")
            self.assertEqual(slots["items-2"]["status"], "accepted")
            self.assertEqual(slots["bonus-1"]["status"], "no_high_confidence_consensus")
            self.assertEqual(artifact["crop_resolution_policy"], FIXED_SLOT_FALLBACK_POLICY)
            self.assertFalse(artifact["identity_verified"])
            self.assertFalse(artifact["list_complete"])
            self.assertEqual(artifact["source_sha256"], "f" * 64)
            self.assertEqual(artifact["reader_fingerprint"], reader.fingerprint)
            self.assertEqual(artifact["model_sha256"], reader.models)
            self.assertEqual(
                artifact["capture_manifest_sha256"],
                hashlib.sha256((run / "capture.json").read_bytes()).hexdigest(),
            )
            self.assertTrue(all(Path(run, observation["crop_evidence"]).is_file() for observation in artifact["all_observations"]))
            self.assertTrue(all(
                observation["crop_sha256"] == hashlib.sha256((run / observation["crop_evidence"]).read_bytes()).hexdigest()
                for observation in artifact["all_observations"]
            ))
            self.assertTrue(all(
                observation["source_frame_sha256"] == hashlib.sha256((run / observation["source_frame_evidence"]).read_bytes()).hexdigest()
                for observation in artifact["all_observations"]
            ))
            third_observations = slots["items-2"]["observations"]
            detector_observations = [observation for observation in third_observations if observation["detector_box"] is not None]
            fallback_observations = [observation for observation in third_observations if observation["detector_box"] is None]
            self.assertEqual(len(detector_observations), 3)
            self.assertEqual(len(fallback_observations), 6)
            self.assertTrue(all(observation["detector_box"] == [566, 705, 597, 725] for observation in detector_observations))
            self.assertTrue(all(observation["source_crop_box"] == [416, 703, 451, 727] for observation in detector_observations))
            self.assertTrue(all(observation["crop_resolution"] == "detector" for observation in detector_observations))
            self.assertTrue(all(observation["source_crop_box"] == list(_fallback_quantity_crop_box(SLOT_BY_ID["items-2"]["source_box"]))
                                for observation in fallback_observations))
            self.assertTrue(all(observation["crop_resolution"] == "fixed_fallback_quantity_text" for observation in fallback_observations))

            for artifact_path in summary["artifacts"]:
                artifact = json.loads(Path(artifact_path).read_text(encoding="utf-8"))
                raw_path = run / "neural" / f"{artifact['base_frame_id']}.json"
                raw = json.loads(raw_path.read_text(encoding="utf-8"))
                refined = apply(parse(raw), artifact, raw=raw, root=run)
                quantities = refined["facts"]["visible_item_quantities"]
                self.assertEqual([entry["quantity"] for entry in quantities], [1, 400, 1, 400])
                self.assertTrue(all(entry["name"] is None for entry in quantities))
                self.assertFalse(refined["facts"]["item_rewards_complete"])

    def test_strict_apply_recomputes_tampered_accepted_quantity(self):
        with self._strict_generated_case() as (run, raw, row, artifact):
            mutated = copy.deepcopy(artifact)
            next(slot for slot in mutated["slots"] if slot["slot_id"] == "items-0")["accepted"]["quantity"] = 400
            with self.assertRaisesRegex(ValueError, "consensus changed"):
                apply(row, mutated, raw=raw, root=run)

    def test_strict_apply_rejects_fabricated_support_timestamp(self):
        with self._strict_generated_case() as (run, raw, row, artifact):
            mutated = copy.deepcopy(artifact)
            next(slot for slot in mutated["slots"] if slot["slot_id"] == "items-0")["accepted"]["support_timestamps_ms"] = [1000, 999999]
            with self.assertRaisesRegex(ValueError, "consensus changed"):
                apply(row, mutated, raw=raw, root=run)

    def test_strict_apply_rejects_empty_observation_provenance(self):
        with self._strict_generated_case() as (run, raw, row, artifact):
            mutated = copy.deepcopy(artifact)
            mutated["all_observations"] = []
            with self.assertRaisesRegex(ValueError, "observations are missing"):
                apply(row, mutated, raw=raw, root=run)

    def test_strict_apply_rejects_changed_capture_manifest(self):
        with self._strict_generated_case() as (run, raw, row, artifact):
            capture_path = run / "capture.json"
            capture = json.loads(capture_path.read_text(encoding="utf-8"))
            capture["source"]["sha256"] = "e" * 64
            capture_path.write_text(json.dumps(capture), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "capture manifest changed"):
                apply(row, artifact, raw=raw, root=run)

    def test_strict_apply_rejects_changed_source_frame(self):
        with self._strict_generated_case() as (run, raw, row, artifact):
            source_path = run / "capture" / "000001.png"
            source_path.write_bytes(b"tampered source frame")
            with self.assertRaisesRegex(ValueError, "source-frame hash mismatch"):
                apply(row, artifact, raw=raw, root=run)

    def test_strict_apply_rejects_tampered_observation_pts(self):
        with self._strict_generated_case() as (run, raw, row, artifact):
            mutated = copy.deepcopy(artifact)
            observation = next(slot for slot in mutated["slots"] if slot["slot_id"] == "items-0")["observations"][0]
            observation["source_pts"] += 1
            with self.assertRaisesRegex(ValueError, "source PTS differs from capture"):
                apply(row, mutated, raw=raw, root=run)

    def test_strict_apply_rejects_tampered_reader_binding(self):
        with self._strict_generated_case() as (run, raw, row, artifact):
            mutated = copy.deepcopy(artifact)
            observation = next(slot for slot in mutated["slots"] if slot["slot_id"] == "items-0")["observations"][0]
            observation["reader_fingerprint"] = "different-reader"
            with self.assertRaisesRegex(ValueError, "model binding changed"):
                apply(row, mutated, raw=raw, root=run)

    def test_strict_apply_rejects_changed_fixed_fallback_policy(self):
        with self._strict_generated_case() as (run, raw, row, artifact):
            mutated = copy.deepcopy(artifact)
            mutated["crop_resolution_policy"]["fallback_requires_pixel_guard"] = False
            with self.assertRaisesRegex(ValueError, "fixed fallback policy changed"):
                apply(row, mutated, raw=raw, root=run)


if __name__ == "__main__":
    unittest.main()
