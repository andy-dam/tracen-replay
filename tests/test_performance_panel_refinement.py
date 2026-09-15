import copy
import hashlib
import json
import unittest
from pathlib import Path

from PIL import Image

from tests.test_gameplay import workspace_temp
from tests.test_performance_panel_recovery import panel_lines
from tracen_replay.full_recording import _fixed_supplement_folder, cached_readings
from tracen_replay.performance_panel_refinement import apply, fingerprint, load


def _file_sha256(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _component(text, box, component, confidence=99.0):
    return {
        "text": text,
        "confidence": confidence,
        "box": list(box),
        "role": f"panel_{component}_component",
        "input_eligible": True,
        "component": component,
        "geometry_basis": "same_row_merged_panel_line",
    }


class PerformancePanelRefinementTests(unittest.TestCase):
    def setUp(self):
        self.temp = workspace_temp()
        self.root = self.temp.__enter__()
        self.gameplay = self.root / "gameplay.png"
        self.source_frame = self.root / "source.jpg"
        Image.new("RGB", (810, 1080), (16, 24, 40)).save(self.gameplay)
        Image.new("RGB", (1080, 1920), (24, 32, 48)).save(self.source_frame)
        lines = [{"text": "Training", "confidence": 99.0, "box": [155, 0, 250, 29]}] + panel_lines()
        composure = next(item for item in lines if item["text"] == "74")
        composure.update(text="56+19", confidence=85.602,
                         box=[208, 515, 314, 553])
        lines.append({"text": "Failure", "confidence": 99.0, "box": [736, 771, 798, 796]})
        self.raw = {
            "source_timestamp_ms": 1583750,
            "evidence": "gameplay.png",
            "source_frame_sha256": _file_sha256(self.source_frame),
            "gameplay_sha256": hashlib.sha256(
                Image.open(self.gameplay).convert("RGB").tobytes()
            ).hexdigest(),
            "model_sha256": {"model.onnx": "a" * 64},
            "engine_fingerprint": "source-engine",
            "lines": lines,
            "regions": {},
            "header": "Training",
            "current_grid": True,
            "result_grid": False,
        }
        parent = copy.deepcopy(composure)
        self.sidecar = {
            "version": 1,
            "stage": "performance_panel_refinement",
            "source_frame_id": "frame-000096",
            "source_timestamp_ms": self.raw["source_timestamp_ms"],
            "evidence": self.raw["evidence"],
            "evidence_sha256": _file_sha256(self.gameplay),
            "source_frame_evidence": "source.jpg",
            "source_frame_sha256": self.raw["source_frame_sha256"],
            "gameplay_sha256": self.raw["gameplay_sha256"],
            "raw_sha256": fingerprint(self.raw),
            "source_model_sha256": self.raw["model_sha256"],
            "source_engine_fingerprint": self.raw["engine_fingerprint"],
            "refinement_model_sha256": {"model.onnx": "b" * 64},
            "refinement_engine_fingerprint": "refinement-engine",
            "independent_observations": False,
            "fields": {
                "composure": {
                    "parent_line_index": next(
                        index for index, line in enumerate(lines) if line is composure
                    ),
                    "parent_observation": parent,
                    "current": _component("56", [215, 518, 260, 550], "current"),
                    "projected": _component("19", [259, 518, 312, 550], "projected"),
                }
            },
        }

    def tearDown(self):
        self.temp.__exit__(None, None, None)

    def _apply(self, sidecar=None, raw=None, original=None):
        target = self.raw if raw is None else raw
        source = self.raw if original is None else original
        return apply(
            target,
            self.sidecar if sidecar is None else sidecar,
            original=source,
            evidence_path=self.gameplay,
            source_frame_path=self.source_frame,
            source_frame_id="frame-000096",
            source_frame_evidence="source.jpg",
        )

    def test_source_bound_components_apply_without_mutating_raw(self):
        original = copy.deepcopy(self.raw)
        refined = self._apply()

        self.assertEqual(self.raw, original)
        self.assertEqual(refined["lines"], self.raw["lines"])
        self.assertEqual(refined["regions"]["performance_panel_current.composure"]["text"], "56")
        self.assertEqual(refined["regions"]["performance_panel_projected.composure"]["text"], "19")
        self.assertEqual(refined["performance_panel_refinement"]["applied_fields"], ["composure"])

    def test_parent_component_amount_disagreement_is_rejected(self):
        sidecar = copy.deepcopy(self.sidecar)
        sidecar["fields"]["composure"]["current"]["text"] = "55"
        with self.assertRaisesRegex(ValueError, "disagreement"):
            self._apply(sidecar)

    def test_localized_current_requires_neighbour_rows_and_cap_anchor(self):
        sidecar = copy.deepcopy(self.sidecar)
        sidecar["fields"] = {}
        sidecar["localized_fields"] = {
            "vocal": {
                "current": {
                    "text": "27",
                    "confidence": 99.0,
                    "box": [215, 406, 260, 446],
                    "role": "panel_localized_current",
                    "input_eligible": True,
                    "component": "current",
                    "geometry_basis": "fixed_row_panel_geometry",
                },
            },
        }
        raw = copy.deepcopy(self.raw)
        raw["lines"] = [line for line in raw["lines"]
                        if line.get("text") in {"Performance", "Points", "Vo", "27"}]
        sidecar["raw_sha256"] = fingerprint(raw)
        with self.assertRaisesRegex(ValueError, "source-bound panel geometry"):
            self._apply(sidecar, raw=raw, original=raw)

    def test_existing_component_disagreement_is_rejected(self):
        raw = copy.deepcopy(self.raw)
        raw["regions"]["performance_panel_current.composure"] = _component(
            "55", [215, 518, 260, 550], "current"
        )
        with self.assertRaisesRegex(ValueError, "disagrees"):
            self._apply(raw=raw)

    def test_timestamp_and_evidence_hashes_are_source_bound(self):
        for key, value, message in (
            ("source_timestamp_ms", 1583751, "timestamp"),
            ("evidence_sha256", "0" * 64, "gameplay evidence changed"),
            ("source_frame_sha256", "0" * 64, "source-frame hash mismatch"),
        ):
            with self.subTest(key=key):
                sidecar = copy.deepcopy(self.sidecar)
                sidecar[key] = value
                with self.assertRaisesRegex(ValueError, message):
                    self._apply(sidecar)

    def test_load_absent_sidecar_is_identity(self):
        self.assertIs(load(self.raw, self.root / "missing.json"), self.raw)

    def test_load_reads_and_applies_sidecar(self):
        path = self.root / "performance-panel-refinement" / "frame-000096.json"
        path.parent.mkdir()
        path.write_text(json.dumps(self.sidecar), encoding="utf-8")
        refined = load(
            self.raw, path, original=self.raw, evidence_path=self.gameplay,
            source_frame_path=self.source_frame, source_frame_id="frame-000096",
            source_frame_evidence="source.jpg"
        )
        self.assertEqual(refined["regions"]["performance_panel_current.composure"]["text"], "56")

    def test_cached_readings_surfaces_source_bound_composure(self):
        neural = self.root / "neural"
        sidecars = self.root / "performance-panel-refinement"
        neural.mkdir()
        sidecars.mkdir()
        (neural / "frame-000096.json").write_text(json.dumps(self.raw), encoding="utf-8")
        (sidecars / "frame-000096.json").write_text(json.dumps(self.sidecar), encoding="utf-8")
        capture = {
            "source": {"sha256": hashlib.sha256(b"synthetic recording").hexdigest()},
            "frames": [{
                "id": "frame-000096",
                "source_timestamp_ms": self.raw["source_timestamp_ms"],
                "evidence": "source.jpg",
            }],
        }

        row = cached_readings(capture, self.root)[0]

        self.assertEqual(row["source_sha256"], capture["source"]["sha256"])
        self.assertEqual(row["facts"]["performance_points"]["composure"], 56)
        self.assertEqual(row["facts"]["projected_performance_gains"]["composure"], 19)
        self.assertEqual(row["performance_panel_refinement"]["applied_fields"], ["composure"])

    def test_manifest_fixed_sidecar_is_consumed_by_base_cache_loader(self):
        self.assertTrue(_fixed_supplement_folder(
            "raw_sidecars", "performance_panel",
            "initial-baseline/performance-panel-refinement", "initial-baseline"
        ))


if __name__ == "__main__":
    unittest.main()
