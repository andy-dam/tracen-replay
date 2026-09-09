import copy
import hashlib
import json
from pathlib import Path
import shutil
import unittest

from PIL import Image

from tests.test_gameplay import workspace_temp
from tracen_replay.full_recording import parse_receipt_pixels
from tracen_replay.receipt_symbols import annotate, detect_circle_marker
from tracen_replay.vision import parse


FIXTURE_ROOT = Path(__file__).parent / "fixtures"
SOURCE_MANIFEST = FIXTURE_ROOT / "receipt-symbol-source-221250.json"
REAL_MANIFEST = FIXTURE_ROOT / "receipt-symbol-real.json"
TERMINAL_CONTROLS = FIXTURE_ROOT / "inventory-suffix-terminal-controls.json"
NORMAL_O_MANIFEST = FIXTURE_ROOT / "receipt-symbol-terminal-o-negative.json"


class ReceiptSymbolTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.manifest = json.loads(SOURCE_MANIFEST.read_text(encoding="utf-8"))
        cls.fixture_path = FIXTURE_ROOT / cls.manifest["fixture"]
        cls.pane = Image.open(cls.fixture_path).convert("RGB")
        cls.real_manifest = json.loads(REAL_MANIFEST.read_text(encoding="utf-8"))

    def raw_observation(self, companion="Firm Conditions"):
        companion_line = dict(self.manifest["companion_line"], text=companion)
        receipt_line = dict(self.manifest["receipt_line"])
        return {
            "lines": [companion_line, receipt_line],
            "source_timestamp_ms": self.manifest["source_timestamp_ms"],
            "evidence": self.manifest["evidence"],
            "source_sha256": self.manifest["source_sha256"],
            "source_frame_sha256": self.manifest["source_frame_sha256"],
            "gameplay_sha256": self.manifest["pane_gameplay_sha256"],
        }

    def proof(self, **changes):
        proof = {
            "gameplay_sha256": self.manifest["pane_gameplay_sha256"],
            "source_timestamp_ms": self.manifest["source_timestamp_ms"],
            "evidence": self.manifest["evidence"],
            "source_sha256": self.manifest["source_sha256"],
            "source_frame_sha256": self.manifest["source_frame_sha256"],
            "evidence_sha256": self.manifest["evidence_sha256"],
            # The copied fixture is byte-identical to the source evidence PNG.
            # Binding this path exercises file-level proof validation without
            # requiring the source recording in CI.
            "evidence_path": str(self.fixture_path),
            "source_frame_path": str(FIXTURE_ROOT / self.manifest["source_frame_fixture"]),
        }
        proof.update(changes)
        return proof

    def real_case(self, name):
        case = next(item for item in self.real_manifest["cases"] if item["name"] == name)
        fixture = FIXTURE_ROOT / case["fixture"]
        pane = Image.open(fixture).convert("RGB")
        lines = [dict(case["companion_line"])] + [dict(line) for line in case["receipt_lines"]]
        raw = {
            "lines": lines,
            "regions": {},
            "header": "Career",
            "result_grid": False,
            "current_grid": False,
            "source_timestamp_ms": case["source_timestamp_ms"],
            "evidence": case["fixture"],
            "source_sha256": self.real_manifest["source_sha256"],
            "source_frame_sha256": case["source_frame_sha256"],
            "gameplay_sha256": case["pane_gameplay_sha256"],
        }
        proof = {
            "source_timestamp_ms": case["source_timestamp_ms"],
            "evidence": case["fixture"],
            "source_sha256": self.real_manifest["source_sha256"],
            "source_frame_sha256": case["source_frame_sha256"],
            "gameplay_sha256": case["pane_gameplay_sha256"],
            "evidence_sha256": case["fixture_sha256"],
            "evidence_path": str(fixture),
        }
        return case, pane, raw, proof

    def run_real_hook(self, name, mutate=None):
        case, _pane, raw, _proof = self.real_case(name)
        if mutate:
            mutate(raw)
        with workspace_temp() as root:
            root = Path(root)
            evidence_rel = Path("gameplay") / case["fixture"]
            source_rel = Path(case["source_frame"])
            (root / evidence_rel).parent.mkdir(parents=True)
            (root / source_rel).parent.mkdir(parents=True)
            shutil.copyfile(FIXTURE_ROOT / case["fixture"], root / evidence_rel)
            shutil.copyfile(FIXTURE_ROOT / case["source_frame_fixture"], root / source_rel)
            raw["evidence"] = evidence_rel.as_posix()
            frame = {
                "source_timestamp_ms": case["source_timestamp_ms"],
                "evidence": source_rel.as_posix(),
            }
            return raw, parse_receipt_pixels(raw, root, frame)

    def test_source_pixels_recover_single_circle_from_same_receipt(self):
        result = detect_circle_marker(self.pane, self.manifest["receipt_box"])
        self.assertIsNotNone(result)
        self.assertEqual(result["kind"], "single_circle")
        self.assertEqual(result["symbol"], "\u25cb")
        self.assertEqual(result["coordinate_space"], "gameplay_crop")
        self.assertGreaterEqual(result["votes"], 3)

    def test_annotate_requires_companion_and_preserves_ocr_provenance(self):
        raw = self.raw_observation()
        original = copy.deepcopy(raw)
        result = annotate(raw, self.pane, self.proof())
        line = result["lines"][1]
        self.assertEqual(line["text"], "Gained 2 hint level(s) for Firm Conditions \u25cb.")
        self.assertEqual(line["original_text"], original["lines"][1]["text"])
        self.assertEqual(line["confidence"], original["lines"][1]["confidence"])
        self.assertEqual(line["text_normalization"], "receipt_circle_suffix")
        self.assertEqual(line["visual_symbol_observation"]["source_timestamp_ms"], 221250)
        self.assertEqual(line["visual_symbol_observation"]["source_sha256"], self.manifest["source_sha256"])
        self.assertEqual(line["visual_symbol_observation"]["evidence_sha256"], self.manifest["evidence_sha256"])
        self.assertEqual(raw, original)

    def test_vision_parse_accepts_source_annotated_line(self):
        neural_path = FIXTURE_ROOT / self.manifest["neural_fixture"]
        self.assertEqual(
            hashlib.sha256(neural_path.read_bytes()).hexdigest(),
            self.manifest["neural_fixture_sha256"],
        )
        raw = json.loads(neural_path.read_text(encoding="utf-8"))
        proof = self.proof()
        # This cached neural fixture predates per-row recording hashes; its
        # source-frame hash and gameplay proof still bind the source evidence.
        proof.pop("source_sha256")
        result = annotate(raw, self.pane, proof)
        effects = [effect for effect in parse(result)["effects"] if effect["kind"] == "skill_hint_change"]
        self.assertEqual(len(effects), 1)
        self.assertEqual(effects[0]["original_text"], self.manifest["receipt_line"]["text"])
        self.assertEqual(effects[0]["visual_symbol_observation"]["symbol"], "\u25cb")

    def test_real_single_line_suffix_recovery_uses_midrange_votes(self):
        case, pane, raw, proof = self.real_case("fall-runner-single-line")
        original = copy.deepcopy(raw)
        result = annotate(raw, pane, proof)
        line = result["lines"][1]
        self.assertEqual(line["text"], "Gained 1 hint level(s) for Fall Runner \u25cb.")
        self.assertEqual(line["original_text"], original["lines"][1]["text"])
        self.assertEqual(line["confidence"], original["lines"][1]["confidence"])
        self.assertEqual(line["text_normalization"], "receipt_circle_suffix")
        self.assertGreaterEqual(line["visual_symbol_observation"]["votes"], 5)
        self.assertEqual(line["visual_symbol_observation"]["source_timestamp_ms"], case["source_timestamp_ms"])
        effects = [effect for effect in parse(result)["effects"] if effect["kind"] == "skill_hint_change"]
        self.assertEqual([(effect["name"], effect["amount"]) for effect in effects], [("Fall Runner \u25cb", 1)])

    def test_real_wrapped_suffix_recovery_merges_only_the_receipt_parts(self):
        case, pane, raw, proof = self.real_case("front-runner-straightaways-wrapped")
        original = copy.deepcopy(raw)
        result = annotate(raw, pane, proof)
        self.assertEqual(len(result["lines"]), 2)
        line = result["lines"][1]
        self.assertEqual(line["text"], "Gained 3 hint level(s) for Front Runner Straightaways \u25cb.")
        self.assertEqual(line["original_text"], "Gained 3 hint level(s) for Front Runner Straightaways .")
        self.assertEqual(line["confidence"], min(original["lines"][1]["confidence"], original["lines"][2]["confidence"]))
        self.assertEqual(line["text_normalization"], "receipt_circle_suffix_wrapped")
        self.assertEqual([part["text"] for part in line["wrapped_receipt_parts"]],
                         [part["text"] for part in original["lines"][1:]])
        self.assertGreaterEqual(line["visual_symbol_observation"]["votes"], 5)
        effects = [effect for effect in parse(result)["effects"] if effect["kind"] == "skill_hint_change"]
        self.assertEqual([(effect["name"], effect["amount"]) for effect in effects],
                         [("Front Runner Straightaways \u25cb", 3)])
        self.assertEqual(effects[0]["original_text"], "Gained 3 hint level(s) for Front Runner Straightaways .")
        self.assertEqual(effects[0]["visual_symbol_observation"]["source_timestamp_ms"], case["source_timestamp_ms"])

    def test_real_wrapped_suffix_requires_exact_layout_and_heading(self):
        _case, pane, raw, proof = self.real_case("front-runner-straightaways-wrapped")
        missing_continuation = copy.deepcopy(raw)
        missing_continuation["lines"].pop()
        self.assertEqual(annotate(missing_continuation, pane, proof)["lines"], missing_continuation["lines"])

        wrong_heading = copy.deepcopy(raw)
        wrong_heading["lines"][0]["text"] = "Front Runner Straightawayss"
        self.assertEqual(annotate(wrong_heading, pane, proof)["lines"], wrong_heading["lines"])

        no_separator = copy.deepcopy(raw)
        no_separator["lines"][2]["text"] = "Straightaways."
        self.assertEqual(annotate(no_separator, pane, proof)["lines"], no_separator["lines"])

        low_confidence = copy.deepcopy(raw)
        low_confidence["lines"][2]["confidence"] = 94.99
        self.assertEqual(annotate(low_confidence, pane, proof)["lines"], low_confidence["lines"])

    def test_full_recording_hook_uses_cached_source_proof(self):
        neural_path = FIXTURE_ROOT / self.manifest["neural_fixture"]
        raw = json.loads(neural_path.read_text(encoding="utf-8"))
        with workspace_temp() as root:
            root = Path(root)
            evidence_rel = Path("gameplay/part-001-frame-000406.png")
            source_rel = Path("part-001/frames/000406.jpg")
            (root / evidence_rel).parent.mkdir(parents=True)
            (root / source_rel).parent.mkdir(parents=True)
            shutil.copyfile(self.fixture_path, root / evidence_rel)
            shutil.copyfile(FIXTURE_ROOT / self.manifest["source_frame_fixture"], root / source_rel)
            raw["evidence"] = evidence_rel.as_posix()
            frame = {"source_timestamp_ms": 221250, "evidence": source_rel.as_posix()}
            row = parse_receipt_pixels(raw, root, frame)
        effects = [effect for effect in row["effects"] if effect["kind"] == "skill_hint_change"]
        self.assertEqual(len(effects), 1)
        self.assertEqual(effects[0]["original_text"], self.manifest["receipt_line"]["text"])
        self.assertEqual(effects[0]["visual_symbol_observation"]["coordinate_space"], "gameplay_crop")

    def test_full_recording_hook_recovers_real_single_and_wrapped_receipts(self):
        expected = {
            "fall-runner-single-line": ("Fall Runner ○", 1),
            "front-runner-straightaways-wrapped": ("Front Runner Straightaways ○", 3),
        }
        for name, expected_effect in expected.items():
            with self.subTest(case=name):
                _raw, row = self.run_real_hook(name)
                effects = [effect for effect in row["effects"] if effect["kind"] == "skill_hint_change"]
                self.assertEqual([(effect["name"], effect["amount"]) for effect in effects], [expected_effect])
                self.assertEqual(effects[0]["visual_symbol_observation"]["symbol"], "○")

    def test_full_recording_hook_skips_unaccepted_shape_after_occlusion_stage(self):
        def reject_shape(raw):
            raw["lines"][1]["text"] = "Gained 1 hint level(s) for Fall Runner."

        _raw, row = self.run_real_hook("fall-runner-single-line", reject_shape)
        effects = [effect for effect in row["effects"] if effect["kind"] == "skill_hint_change"]
        self.assertEqual([(effect["name"], effect["amount"]) for effect in effects], [("Fall Runner", 1)])
        self.assertNotIn("visual_symbol_observation", effects[0])

        def blocked_line(raw):
            raw["lines"][1]["confidence"] = 0
            raw["lines"][1]["overlay_occluded"] = True

        _raw, row = self.run_real_hook("fall-runner-single-line", blocked_line)
        effects = [effect for effect in row["effects"] if effect["kind"] == "skill_hint_change"]
        self.assertEqual(effects, [])

    def test_ordinary_terminal_letters_do_not_become_receipt_circles(self):
        controls = json.loads(TERMINAL_CONTROLS.read_text(encoding="utf-8"))
        path = FIXTURE_ROOT / controls["fixture"]
        self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(), controls["fixture_sha256"])
        image = Image.open(path).convert("RGB")
        for control in controls["controls"]:
            with self.subTest(word=control["word"], size=control["size"]):
                self.assertIsNone(detect_circle_marker(image, control["box"]))

    def test_native_inspection_uses_same_pixel_recovery_and_recording_proof(self):
        from tracen_replay.inspect_training import reparse_inspection
        raw=json.loads((FIXTURE_ROOT/self.manifest['neural_fixture']).read_text(encoding='utf-8'))
        raw['source_sha256']=self.manifest['source_sha256']
        raw['evidence']='receipt-inspection/probe/frame-000001.png'
        with workspace_temp() as root:
            root=Path(root); directory=root/'receipt-inspection/probe'
            (directory/'frames').mkdir(parents=True)
            shutil.copyfile(self.fixture_path,directory/'frame-000001.png')
            shutil.copyfile(FIXTURE_ROOT/self.manifest['source_frame_fixture'],directory/'frames/frame-000001.jpg')
            (directory/'frame-000001.v2.json').write_text(json.dumps(raw),encoding='utf-8')
            frames=[dict(id='frame-000001',source_timestamp_ms=221250,evidence='frames/frame-000001.jpg')]
            (directory/'frames.json').write_text(json.dumps(frames),encoding='utf-8')
            inspection=dict(source_sha256=raw['source_sha256'],readings=[dict(evidence=raw['evidence'],source_timestamp_ms=221250)])
            rows=reparse_inspection(inspection,root)
        hints=[e for e in rows[0]['effects'] if e['kind']=='skill_hint_change']
        self.assertEqual(len(hints),1)
        self.assertEqual(hints[0]['name'],'Firm Conditions ○')
        self.assertEqual(hints[0]['visual_symbol_observation']['source_sha256'],raw['source_sha256'])

    def test_normal_terminal_o_with_period_and_companion_abstains(self):
        manifest = json.loads(NORMAL_O_MANIFEST.read_text(encoding="utf-8"))
        path = FIXTURE_ROOT / manifest["fixture"]
        self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(), manifest["fixture_sha256"])
        pane = Image.open(path).convert("RGB")
        self.assertEqual(pane.size, tuple(manifest["canvas_size"]))
        gameplay_hash = hashlib.sha256(pane.tobytes()).hexdigest()
        raw = {
            "lines": [dict(manifest["companion"]), dict(manifest["receipt"])],
            "source_timestamp_ms": 1,
            "evidence": manifest["fixture"],
            "source_frame_sha256": "0" * 64,
            "gameplay_sha256": gameplay_hash,
        }
        proof = {
            "source_timestamp_ms": 1,
            "evidence": manifest["fixture"],
            "source_frame_sha256": "0" * 64,
            "gameplay_sha256": gameplay_hash,
            "evidence_sha256": manifest["fixture_sha256"],
            "evidence_path": str(path),
        }
        result = annotate(raw, pane, proof)
        self.assertEqual(result["lines"], raw["lines"])

    def test_wrong_same_receipt_companion_abstains(self):
        raw = self.raw_observation(companion="Firm Condition")
        result = annotate(raw, self.pane, self.proof())
        self.assertEqual(result["lines"], raw["lines"])

    def test_low_confidence_or_duplicate_companion_abstains(self):
        low_confidence = self.raw_observation()
        low_confidence["lines"][0]["confidence"] = 94.99
        self.assertEqual(annotate(low_confidence, self.pane, self.proof())["lines"], low_confidence["lines"])

        duplicate = self.raw_observation()
        duplicate["lines"].append(dict(duplicate["lines"][0]))
        self.assertEqual(annotate(duplicate, self.pane, self.proof())["lines"], duplicate["lines"])

    def test_clipped_marker_abstains(self):
        pane = self.pane.copy()
        pane.paste((255, 255, 255), (577, 807, 595, 833))
        self.assertIsNone(detect_circle_marker(pane, self.manifest["receipt_box"]))

    def test_changed_gameplay_pixels_are_rejected(self):
        pane = self.pane.copy()
        original_pixel = pane.getpixel((0, 0))
        replacement = (0, 0, 0) if original_pixel != (0, 0, 0) else (255, 255, 255)
        pane.putpixel((0, 0), replacement)
        with self.assertRaisesRegex(ValueError, "gameplay pixels"):
            annotate(self.raw_observation(), pane, self.proof())

    def test_changed_evidence_file_is_rejected(self):
        proof = self.proof(evidence_sha256="0" * 64)
        with self.assertRaisesRegex(ValueError, "proof file mismatch"):
            annotate(self.raw_observation(), self.pane, proof)

    def test_source_frame_and_timestamp_proof_drift_are_rejected(self):
        with self.assertRaisesRegex(ValueError, "source_timestamp_ms"):
            annotate(self.raw_observation(), self.pane, self.proof(source_timestamp_ms=221251))
        with self.assertRaisesRegex(ValueError, "source_frame_sha256"):
            annotate(self.raw_observation(), self.pane, self.proof(source_frame_sha256="0" * 64))
        with self.assertRaisesRegex(ValueError, "source recording hash"):
            annotate(self.raw_observation(), self.pane, self.proof(source_sha256="0" * 64))

    def test_fixture_hashes_and_source_scope_are_pinned(self):
        self.assertEqual(hashlib.sha256(self.fixture_path.read_bytes()).hexdigest(), self.manifest["fixture_sha256"])
        source_frame_path = FIXTURE_ROOT / self.manifest["source_frame_fixture"]
        self.assertEqual(
            hashlib.sha256(source_frame_path.read_bytes()).hexdigest(),
            self.manifest["source_frame_fixture_sha256"],
        )
        self.assertEqual(self.pane.size, tuple(self.manifest["pane_size"]))
        self.assertEqual(hashlib.sha256(self.pane.tobytes()).hexdigest(), self.manifest["pane_gameplay_sha256"])
        self.assertRegex(self.manifest["source_sha256"], r"^[0-9a-f]{64}$")
        self.assertRegex(self.manifest["source_frame_sha256"], r"^[0-9a-f]{64}$")
        self.assertRegex(self.manifest["evidence_sha256"], r"^[0-9a-f]{64}$")


if __name__ == "__main__":
    unittest.main()
