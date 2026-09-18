"""Tests of ``tests.test_numeric_cap_refinement`` that need locally preserved evidence; they run only where it is."""
import copy
import hashlib
import json
import unittest
from pathlib import Path
from tests import localdata
from tests.test_gameplay import workspace_temp
from tracen_replay.numeric_cap_refinement import (
    apply,
    candidate_fields,
    fingerprint,
    load,
    parse_cap,
    parse_ratio,
    performance_panel_geometry,
    read_performance_caps,
    read_stat_caps,
)
from tracen_replay.stat_state_details import read_main_stat_caps
from tracen_replay.vision import _main_stat_cap_requests, parse
from tests.test_numeric_cap_refinement import REPO, _provenance, _real_raw, _sha, _source_paths


class NumericCapRefinementTests(unittest.TestCase):

    def test_candidates_expose_only_geometry_and_no_amounts(self):
        raw = _real_raw("part-006-frame-000261")
        requests = candidate_fields(raw)
        self.assertEqual(
            {(item["field"], item["kind"]) for item in requests},
            {("vocal", "performance_cap"), ("speed", "result_total"),
             ("wit", "result_total")},
        )
        for item in requests:
            self.assertNotIn("value", item)
            self.assertNotIn("expected", item)


    def test_candidates_recover_headerless_source_panel_rows(self):
        raw = _real_raw("part-011-frame-000093")
        geometry = performance_panel_geometry(raw)
        self.assertIsNotNone(geometry)
        self.assertEqual(geometry["basis"],
                         "fixed_row_performance_panel_geometry")
        requests = candidate_fields(raw)
        self.assertEqual(
            {(item["field"], item["kind"]) for item in requests
             if item["kind"] == "performance_cap"},
            {(field, "performance_cap")
             for field in ("dance", "passion", "vocal", "visual")},
        )
        for item in requests:
            if item["kind"] == "performance_cap":
                self.assertEqual(item["geometry_basis"],
                                 "fixed_row_performance_panel_geometry")

    def test_composed_sidecars_share_immutable_original_and_keep_regions(self):
        raw, evidence, source = _source_paths("part-006-frame-000261")
        lines = {line["text"]: line for line in raw["lines"]}
        speed = {
            "result_total.speed": {
                "observation": {
                    "text": "599/1600", "confidence": 96.0,
                    "box": [322, 834, 448, 876],
                },
                "source_anchor": {
                    "text": "599/16", "box": lines["599/16"]["box"]},
            },
        }
        wit = {
            "result_total.wit": {
                "observation": {
                    "text": "576/1300", "confidence": 96.0,
                    "box": [518, 952, 644, 994],
                },
                "source_anchor": {
                    "text": "576/130", "box": lines["576/130"]["box"]},
            },
        }
        first = _provenance(raw, evidence, source, speed)
        second = _provenance(raw, evidence, source, wit)
        refined = apply(raw, first, evidence_path=evidence,
                        source_frame_path=source,
                        source_frame_id=first["source_frame_id"],
                        source_frame_evidence=first["source_frame_evidence"])
        composed = apply(refined, second, original=raw,
                         evidence_path=evidence, source_frame_path=source,
                         source_frame_id=second["source_frame_id"],
                         source_frame_evidence=second["source_frame_evidence"])
        self.assertEqual(composed["regions"]["numeric_result.speed"]["text"],
                         "599/1600")
        self.assertEqual(composed["regions"]["numeric_result.wit"]["text"],
                         "576/1300")
        self.assertEqual(composed["numeric_cap_refinement"]["applied_fields"],
                         ["result_total.speed", "result_total.wit"])
        self.assertNotIn(
            ("speed", "result_total"),
            {(item["field"], item["kind"]) for item in candidate_fields(composed)},
        )
        self.assertNotIn(
            ("wit", "result_total"),
            {(item["field"], item["kind"]) for item in candidate_fields(composed)},
        )

    def test_composed_sidecar_cannot_overwrite_conflicting_region(self):
        raw, evidence, source = _source_paths("part-006-frame-000261")
        source_line = next(line for line in raw["lines"]
                           if line["text"] == "599/16")
        first_fields = {
            "result_total.speed": {
                "observation": {"text": "599/1600", "confidence": 96,
                                 "box": [322, 834, 448, 876]},
                "source_anchor": {"text": "599/16",
                                   "box": source_line["box"]},
            },
        }
        first = _provenance(raw, evidence, source, first_fields)
        refined = apply(raw, first, evidence_path=evidence,
                        source_frame_path=source,
                        source_frame_id=first["source_frame_id"],
                        source_frame_evidence=first["source_frame_evidence"])
        conflicting_fields = copy.deepcopy(first_fields)
        conflicting_fields["result_total.speed"]["observation"]["text"] = "599/1500"
        conflicting = _provenance(raw, evidence, source, conflicting_fields)
        with self.assertRaisesRegex(ValueError, "disagrees with an existing region"):
            apply(refined, conflicting, original=raw, evidence_path=evidence,
                  source_frame_path=source,
                  source_frame_id=conflicting["source_frame_id"],
                  source_frame_evidence=conflicting["source_frame_evidence"])

    def test_wrong_immutable_original_is_rejected(self):
        raw, evidence, source = _source_paths("part-006-frame-000261")
        source_line = next(line for line in raw["lines"]
                           if line["text"] == "599/16")
        fields = {
            "result_total.speed": {
                "observation": {"text": "599/1600", "confidence": 96,
                                 "box": [322, 834, 448, 876]},
                "source_anchor": {"text": "599/16",
                                   "box": source_line["box"]},
            },
        }
        sidecar = _provenance(raw, evidence, source, fields)
        wrong_original = copy.deepcopy(raw)
        wrong_original["lines"][0]["text"] = "Career"
        with self.assertRaisesRegex(ValueError, "source JSON changed"):
            apply(raw, sidecar, original=wrong_original,
                  evidence_path=evidence, source_frame_path=source,
                  source_frame_id=sidecar["source_frame_id"],
                  source_frame_evidence=sidecar["source_frame_evidence"])

    def test_t063_source_bound_caps_add_missing_rows_without_mutating_raw(self):
        raw = _real_raw("part-011-frame-000093")
        evidence = localdata.root("development_second_recording", raw["evidence"])
        source = localdata.root("development_second_recording", "part-011", "frames", "000093.jpg")
        fields = {}
        boxes = {
            "dance": [176, 323, 226, 344],
            "passion": [176, 379, 226, 400],
            "vocal": [176, 435, 226, 456],
            "visual": [176, 491, 226, 512],
            "composure": [176, 547, 226, 568],
        }
        for field, box in boxes.items():
            fields[f"performance_cap.{field}"] = {
                "observation": {
                    "text": "/400",
                    "confidence": 96.0,
                    "box": box,
                    "role": "panel_cap",
                    "input_eligible": True,
                    "component": "cap",
                    "geometry_basis": "fixed_row_panel_cap_geometry",
                    "source_review": "bounded_source_image",
                }
            }
        sidecar = _provenance(raw, evidence, source, fields)
        sidecar["source_frame_id"] = "part-011-frame-000093"
        sidecar["source_frame_evidence"] = "part-011/frames/000093.jpg"
        original = copy.deepcopy(raw)
        refined = apply(raw, sidecar, evidence_path=evidence,
                        source_frame_path=source,
                        source_frame_id=sidecar["source_frame_id"],
                        source_frame_evidence=sidecar["source_frame_evidence"])
        self.assertEqual(raw, original)
        caps, proof = read_performance_caps(refined["lines"], refined["regions"])
        self.assertEqual(caps, dict.fromkeys(
            ("dance", "passion", "vocal", "visual", "composure"), 400))
        self.assertEqual(proof["vocal"]["basis"], "labeled_performance_panel_cap")
        self.assertEqual(parse(refined)["facts"]["performance_caps"], caps)

    def test_t042_partial_result_ratios_are_completed_only_by_source_bound_rows(self):
        raw, evidence, source = _source_paths("part-006-frame-000261")
        line_by_text = {line["text"]: line for line in raw["lines"]}
        fields = {
            "result_total.speed": {
                "observation": {
                    "text": "599/1600", "confidence": 96.0,
                    "box": [322, 834, 448, 876],
                    "role": "result_total_reread", "input_eligible": True,
                    "geometry_basis": "fixed_result_card_row_geometry",
                    "source_review": "bounded_source_image",
                },
                "source_anchor": {"text": "599/16", "box": line_by_text["599/16"]["box"]},
            },
            "result_total.wit": {
                "observation": {
                    "text": "576/1300", "confidence": 96.0,
                    "box": [518, 952, 644, 994],
                    "role": "result_total_reread", "input_eligible": True,
                    "geometry_basis": "fixed_result_card_row_geometry",
                    "source_review": "bounded_source_image",
                },
                "source_anchor": {"text": "576/130", "box": line_by_text["576/130"]["box"]},
            },
        }
        sidecar = _provenance(raw, evidence, source, fields)
        refined = apply(raw, sidecar, evidence_path=evidence,
                        source_frame_path=source,
                        source_frame_id=sidecar["source_frame_id"],
                        source_frame_evidence=sidecar["source_frame_evidence"])
        facts = parse(refined)["facts"]
        self.assertEqual(facts["result_values"]["speed"], 599)
        self.assertEqual(facts["result_values"]["wit"], 576)
        self.assertEqual(facts["result_snapshot_refinement"]["speed"]["text"], "599/1600")
        self.assertEqual(facts["result_snapshot_refinement"]["wit"]["text"], "576/1300")
        self.assertEqual(raw["regions"]["result.speed"]["text"], "599/160")

    def test_complete_source_result_rejects_conflicting_refinement(self):
        raw, evidence, source = _source_paths("part-006-frame-000261")
        raw["lines"].append({"text": "599/1600", "confidence": 99.0,
                              "box": [322, 834, 448, 876]})
        anchor = raw["lines"][-1]
        fields = {"result_total.speed": {
            "observation": {"text": "599/1500", "confidence": 96.0,
                             "box": [322, 834, 448, 876]},
            "source_anchor": {"text": anchor["text"], "box": anchor["box"]},
        }}
        sidecar = _provenance(raw, evidence, source, fields)
        with self.assertRaisesRegex(ValueError, "complete source ratio"):
            apply(raw, sidecar, evidence_path=evidence,
                  source_frame_path=source,
                  source_frame_id=sidecar["source_frame_id"],
                  source_frame_evidence=sidecar["source_frame_evidence"])

    def test_refinement_hash_and_frame_binding_are_mandatory(self):
        raw, evidence, source = _source_paths("part-006-frame-000261")
        line = next(line for line in raw["lines"] if line["text"] == "599/16")
        fields = {"result_total.speed": {
            "observation": {"text": "599/1600", "confidence": 96.0,
                             "box": [322, 834, 448, 876]},
            "source_anchor": {"text": "599/16", "box": line["box"]},
        }}
        sidecar = _provenance(raw, evidence, source, fields)
        sidecar["source_frame_id"] = "wrong-frame"
        with self.assertRaisesRegex(ValueError, "frame identity"):
            apply(raw, sidecar, evidence_path=evidence,
                  source_frame_path=source,
                  source_frame_id="part-006-frame-000261",
                  source_frame_evidence=sidecar["source_frame_evidence"])

    def test_published_sidecars_recover_the_two_reviewed_source_cases(self):
        root = localdata.root("numeric_cap_refinements")
        if not (root / "manifest.json").exists():
            self.skipTest("published numeric-cap refinements are not present")
        manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
        self.assertEqual({record["frame_id"] for record in manifest["records"]},
                         {"part-006-frame-000261", "part-011-frame-000093"})
        for record in manifest["records"]:
            frame_id = record["frame_id"]
            raw, evidence, source = _source_paths(frame_id)
            part, frame = frame_id.split("-")[1], frame_id.split("-")[-1]
            source_evidence = f"part-{part}/frames/{frame}.jpg"
            sidecar_path = REPO / record["path"]
            self.assertEqual(_sha(sidecar_path), record["sha256"])
            refined = load(raw, sidecar_path, evidence_path=evidence,
                            source_frame_path=source, source_frame_id=frame_id,
                            source_frame_evidence=source_evidence)
            facts = parse(refined)["facts"]
            if frame_id == "part-006-frame-000261":
                self.assertEqual(facts["result_values"]["speed"], 599)
                self.assertEqual(facts["result_values"]["wit"], 576)
                self.assertEqual(facts["performance_points"]["vocal"], 84)
                self.assertEqual(facts["performance_points"]["composure"], 100)
                self.assertEqual(
                    facts["performance_panel_provenance"]["vocal"]["current"]["value"], 84)
                self.assertEqual(
                    facts["performance_panel_provenance"]["composure"]["current"]["value"], 100)
            else:
                self.assertEqual(facts["performance_caps"],
                                 dict.fromkeys(("dance", "passion", "vocal", "visual", "composure"), 400))

    def test_visible_frozen_cap_gaps_use_shared_rows_without_expected_values(self):
        expected = {
            "part-001-frame-000393": (200, None),
            "part-004-frame-000280": (250, {
                "speed": 1600, "stamina": 1348, "power": 1355,
                "guts": 1500, "wit": 1300,
            }),
            "part-006-frame-000239": (300, {
                "speed": 1600, "stamina": 1348, "power": 1355,
                "guts": 1500, "wit": 1300,
            }),
        }
        for frame_id, (cap, stat_caps) in expected.items():
            raw = _real_raw(frame_id)
            performance_caps, _ = read_performance_caps(raw["lines"], raw.get("regions"))
            self.assertEqual(performance_caps,
                             dict.fromkeys(("dance", "passion", "vocal", "visual", "composure"), cap))
            main_stat_caps, main_stat_proof = read_main_stat_caps(raw)
            if stat_caps is None:
                self.assertEqual(main_stat_caps, {})
            else:
                self.assertEqual(main_stat_caps, stat_caps)
                if frame_id == "part-004-frame-000280":
                    self.assertEqual(main_stat_proof["guts"]["slash_normalization"],
                                     "ocr_slash_variant")
