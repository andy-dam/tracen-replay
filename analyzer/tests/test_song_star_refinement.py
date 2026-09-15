from copy import deepcopy
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from PIL import Image, ImageDraw

from tests.test_gameplay import workspace_temp
from tracen_replay.song_star_refinement import apply, prepare
from tracen_replay.vision import parse


class _Reader:
    def __init__(self, readings=None, confidence=.9991):
        self.readings = list(readings or ("Full Speed Ahead! Umadol", "Power"))
        self.confidence = confidence
        self.calls = 0
        self.models = {"test-rec.onnx": "b" * 64}
        self.fingerprint = "c" * 64
        self.TextRecInput = lambda **kwargs: kwargs
        self.engine = SimpleNamespace(text_rec=self.text_rec)

    def text_rec(self, request):
        del request
        if self.calls >= len(self.readings):
            raise AssertionError("unexpected OCR call")
        text = self.readings[self.calls]
        self.calls += 1
        return SimpleNamespace(txts=[text], scores=[self.confidence])


class SongStarRefinementTests(unittest.TestCase):
    def setUp(self):
        self.temp = workspace_temp()
        self.root = self.temp.__enter__()
        fixture = Path("analyzer/tests/fixtures/song-star-wrapped.png")
        self.meta = json.loads(fixture.with_suffix(".json").read_text(encoding="utf-8"))
        self.assertEqual(hashlib.sha256(fixture.read_bytes()).hexdigest(),
                         self.meta["crop_png_sha256"])
        self.pane = Image.new("RGB", (810, 1080), "white")
        with Image.open(fixture) as cropped:
            self.pane.paste(cropped, self.meta["crop_box"][:2])
        self.proof = self.root / "proof.png"
        self.pane.save(self.proof)
        self.raw = dict(
            lines=[self.meta["prefix_line"], self.meta["continuation_line"]],
            regions={}, header="", current_grid=False, result_grid=False,
            source_timestamp_ms=self.meta["source_timestamp_ms"],
            evidence="proof.png", source_frame_sha256="a" * 64,
            gameplay_sha256=hashlib.sha256(self.pane.tobytes()).hexdigest(),
        )

    def tearDown(self):
        self.temp.__exit__(None, None, None)

    def test_strict_star_recovery_keeps_both_raw_lines_and_parses_symbol(self):
        before = deepcopy(self.raw)
        reader = _Reader()
        extra = prepare(self.raw, self.proof, reader)
        self.assertEqual(reader.calls, 2)
        self.assertEqual(len(extra["observations"]), 1)
        observation = extra["observations"][0]
        self.assertEqual(observation["prefix_line"], self.raw["lines"][0])
        self.assertEqual(observation["continuation_line"], self.raw["lines"][1])
        self.assertEqual(observation["raw_lines"], self.raw["lines"])
        self.assertEqual(observation["layout"]["star_crop_box"], [222, 816, 237, 830])
        self.assertEqual(observation["layout"]["star"]["symbol"], "☆")
        self.assertTrue(all(len(item["outer_polygon"]) == 10
                            for item in observation["layout"]["star"]["geometry"]))
        self.assertEqual(observation["min_confidence"], 99.26)

        with patch("tracen_replay.vision.NeuralReader", side_effect=AssertionError("OCR during replay")):
            fixed = apply(self.raw, extra, self.proof)
        self.assertEqual(self.raw, before)
        self.assertEqual(fixed["lines"][0], self.raw["lines"][0])
        self.assertEqual(fixed["lines"][1]["text"], "Power☆\".")
        self.assertEqual(fixed["lines"][1]["original_symbol_text"], "Power\".")
        effect = parse(fixed)["effects"][0]
        self.assertEqual(effect["name"], "Full Speed Ahead! Umadol Power☆")
        self.assertEqual(fixed["lines"][1]["original_symbol_text"], "Power\".")
        self.assertEqual(fixed["lines"][1]["visual_symbol_observation"]["symbol"], "☆")

    def test_lower_confidence_source_line_is_retained_with_minimum_recorded(self):
        raw = deepcopy(self.raw)
        raw["lines"][1]["confidence"] = 94.791
        reader = _Reader()
        extra = prepare(raw, self.proof, reader)
        self.assertEqual(len(extra["observations"]), 1)
        self.assertEqual(extra["observations"][0]["min_confidence"], 94.791)
        fixed = apply(raw, extra, self.proof)
        self.assertEqual(fixed["lines"][1]["confidence"], 94.791)

    def _mutated_star(self, kind):
        pane = self.pane.copy()
        draw = ImageDraw.Draw(pane)
        draw.rectangle((221, 814, 238, 832), fill="white")
        points = [(229, 815), (227, 820), (222, 821), (226, 824),
                  (224, 830), (229, 826), (233, 829), (232, 824),
                  (236, 821), (231, 820)]
        if kind == "filled":
            draw.polygon(points, fill="black")
        elif kind == "asterisk":
            center = (229, 823)
            for point in ((229, 815), (236, 821), (233, 829),
                          (224, 830), (222, 821)):
                draw.line((center, point), fill="black", width=2)
        elif kind == "letter":
            draw.line((223, 829, 235, 816), fill="black", width=2)
            draw.line((223, 816, 235, 829), fill="black", width=2)
        elif kind == "open":
            for first, second in zip(points, points[1:-1]):
                draw.line((first, second), fill="black", width=1)
        else:
            raise AssertionError(kind)
        return pane

    def test_filled_asterisk_letter_and_open_outline_abstain(self):
        for kind in ("filled", "asterisk", "letter", "open"):
            pane = self._mutated_star(kind)
            proof = self.root / f"{kind}.png"
            pane.save(proof)
            raw = dict(self.raw, gameplay_sha256=hashlib.sha256(pane.tobytes()).hexdigest())
            with self.subTest(kind=kind):
                self.assertEqual(prepare(raw, proof, _Reader())["observations"], [])

    def test_missing_quotes_and_nonadjacent_lines_abstain(self):
        pane = self.pane.copy()
        pane.paste("white", (238, 813, 247, 823))
        proof = self.root / "missing-quotes.png"
        pane.save(proof)
        raw = dict(self.raw, gameplay_sha256=hashlib.sha256(pane.tobytes()).hexdigest())
        self.assertEqual(prepare(raw, proof, _Reader())["observations"], [])

        raw = deepcopy(self.raw)
        raw["lines"].insert(1, {"text": "Other receipt.", "confidence": 99.9,
                                "box": [306, 811, 400, 835]})
        self.assertEqual(prepare(raw, self.proof, _Reader())["observations"], [])

    def test_independent_text_witness_and_confidence_are_required(self):
        for readings, confidence in [
            (("Wrong title", "Power"), .999),
            (("Full Speed Ahead! Umadol", "Wrong"), .999),
            (("Full Speed Ahead! Umadol", "Power"), .949),
        ]:
            with self.subTest(readings=readings, confidence=confidence):
                self.assertEqual(prepare(self.raw, self.proof,
                                         _Reader(readings, confidence))["observations"], [])

    def test_model_free_replay_rejects_tampering_and_is_idempotent(self):
        extra = prepare(self.raw, self.proof, _Reader())
        fixed = apply(self.raw, extra, self.proof)
        # Replaying the same validated artifact against its own result still
        # validates geometry, crop hashes, coverage, and the complete refined
        # line before accepting the idempotent result.
        self.assertEqual(apply(fixed, extra, self.proof, original=self.raw), fixed)
        for field, value in (("confidence", 80), ("box", [308, 840, 403, 864]),
                             ("unverified_metadata", True)):
            changed = deepcopy(fixed)
            changed["lines"][1][field] = value
            with self.subTest(refined_field=field), self.assertRaises(ValueError):
                apply(changed, extra, self.proof, original=self.raw)
        for mutation in ("raw", "proof", "prefix", "continuation", "layout",
                         "crop", "coverage", "minimum", "reading"):
            raw = deepcopy(self.raw)
            cache = deepcopy(extra)
            observation = cache["observations"][0]
            if mutation == "raw":
                cache["raw_sha256"] = "z" * 64
            elif mutation == "proof":
                cache["evidence_sha256"] = "z" * 64
            elif mutation == "prefix":
                observation["prefix_line"]["text"] = "Other"
            elif mutation == "continuation":
                observation["continuation_line"]["text"] = "Other\"."
            elif mutation == "layout":
                observation["layout"]["star_crop_box"][0] += 1
            elif mutation == "crop":
                observation["continuation_crop_sha256"] = "e" * 64
            elif mutation == "coverage":
                observation["prefix_pixel_coverage"]["letter_count"] += 1
            elif mutation == "minimum":
                observation["min_confidence"] = 90
            else:
                observation["continuation_reading"]["text"] = "Wrong"
            with self.subTest(mutation=mutation), self.assertRaises(ValueError):
                apply(raw, cache, self.proof)

    def test_malformed_geometry_and_provenance_fail_closed(self):
        extra = prepare(self.raw, self.proof, _Reader())
        for box in (None, [], [222, 816, 237], [222, 816, "237", 830],
                    [True, 816, 237, 830], [222, 816, 810, 830]):
            raw = deepcopy(self.raw)
            raw["lines"][1]["box"] = box
            with self.subTest(box=box):
                self.assertEqual(prepare(raw, self.proof, _Reader())["observations"], [])
        for key, value in (("version", True), ("policy", "other"),
                           ("models", "not models"),
                           ("engine_fingerprint", "short"),
                           ("observations", ["not an observation"])):
            bad = deepcopy(extra)
            bad[key] = value
            with self.subTest(key=key), self.assertRaises(ValueError):
                apply(self.raw, bad, self.proof)

    def test_cached_replay_and_evidence_verification_are_model_free(self):
        from tracen_replay.full_recording import cached_readings
        source = Image.new("RGB", (1920, 1080), "black")
        source.paste(self.pane, (148, 0))
        source_path = self.root / "source.png"
        source.save(source_path)
        raw = dict(self.raw,
                   model_sha256={"test-rec.onnx": "b" * 64},
                   engine_fingerprint="c" * 64,
                   source_frame_sha256=hashlib.sha256(source_path.read_bytes()).hexdigest(),
                   evidence="gameplay.png")
        proof = self.root / "gameplay.png"
        self.pane.save(proof)
        extra = prepare(raw, proof, _Reader())
        (self.root / "neural").mkdir()
        (self.root / "song-star-refinement").mkdir()
        (self.root / "neural" / "one.json").write_text(
            json.dumps(raw, ensure_ascii=False), encoding="utf-8")
        (self.root / "song-star-refinement" / "one.json").write_text(
            json.dumps(extra, ensure_ascii=False), encoding="utf-8")
        capture = {
            "source": {"sha256": "d" * 64},
            "frames": [{"id": "one", "evidence": "source.png",
                        "source_timestamp_ms": 377000}],
        }
        with patch("tracen_replay.vision.NeuralReader",
                   side_effect=AssertionError("OCR during cached replay")):
            rows = cached_readings(capture, self.root)
        self.assertEqual(rows[0]["effects"][0]["name"], "Full Speed Ahead! Umadol Power☆")
        self.assertEqual(rows[0]["ocr"]["neural"][0]["text"], raw["lines"][0]["text"])
        self.assertEqual(rows[0]["ocr"]["neural"][1]["original_symbol_text"], "Power\".")

        from tracen_replay.verify_evidence import verify
        capture["source"].update(sha256=raw["source_frame_sha256"], duration_ms=2000000)
        capture["frames"][0].update(source_pts=377000, time_base="1/1000")
        (self.root / "capture.json").write_text(json.dumps(capture), encoding="utf-8")
        with patch("builtins.print"):
            audit = verify(self.root, source_path)
        self.assertTrue(audit["evidence_integrity_verified"])
        self.assertGreaterEqual(audit["verified_refinements"], 1)

    def test_rehashed_gameplay_crop_cannot_disagree_with_decoded_source(self):
        from tracen_replay.full_recording import cached_readings
        from tracen_replay.pipeline import PipelineError
        source = Image.new("RGB", (1920, 1080), "black")
        source.paste(self.pane, (148, 0))
        source_path = self.root / "source.png"
        source.save(source_path)
        altered = self.pane.copy()
        altered.putpixel((10, 10), (255, 0, 0))
        proof = self.root / "gameplay.png"
        altered.save(proof)
        raw = dict(self.raw, model_sha256={"test-rec.onnx": "b" * 64},
                   engine_fingerprint="c" * 64, evidence="gameplay.png",
                   source_frame_sha256=hashlib.sha256(source_path.read_bytes()).hexdigest(),
                   gameplay_sha256=hashlib.sha256(altered.tobytes()).hexdigest())
        extra = prepare(raw, proof, _Reader())
        self.assertEqual(len(extra["observations"]), 1)
        for folder, payload in (("neural", raw), ("song-star-refinement", extra)):
            (self.root / folder).mkdir()
            (self.root / folder / "one.json").write_text(json.dumps(payload), encoding="utf-8")
        capture = {"source": {"sha256": "d" * 64}, "frames": [
            {"id": "one", "evidence": "source.png", "source_timestamp_ms": raw["source_timestamp_ms"]}]}
        with self.assertRaisesRegex(PipelineError, "decoded source crop"):
            cached_readings(capture, self.root)


if __name__ == "__main__":
    unittest.main()
