import hashlib
import json
import unittest
from pathlib import Path

from tests import localdata
from tracen_replay.numeric_cap_refinement import (
    candidate_fields,
    fingerprint,
    parse_cap,
    parse_ratio,
    performance_panel_geometry,
    read_stat_caps,
)
from tracen_replay.vision import _main_stat_cap_requests


REPO = Path(__file__).resolve().parents[2]


def _sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _real_raw(name):
    path = localdata.root("development_second_recording", "neural", f"{name}.json")
    if not path.exists():
        raise unittest.SkipTest("preserved independent-01 caches are not present")
    return json.loads(path.read_text())


def _source_paths(name):
    part, frame = name.split("-")[1], name.split("-")[-1]
    # The source capture uses the same part/frame names as the neural cache.
    source = localdata.root("development_second_recording", f"part-{part}", "frames", f"{frame}.jpg")
    raw = _real_raw(name)
    evidence = localdata.root("development_second_recording", raw["evidence"])
    return raw, evidence, source


def _provenance(raw, evidence, source, fields):
    return {
        "version": 1,
        "stage": "numeric_cap_refinement",
        "source_frame_id": "part-006-frame-000261",
        "source_timestamp_ms": raw["source_timestamp_ms"],
        "evidence": raw["evidence"],
        "evidence_sha256": _sha(evidence),
        "source_frame_evidence": "part-006/frames/000261.jpg",
        "source_frame_sha256": _sha(source),
        "gameplay_sha256": raw["gameplay_sha256"],
        "raw_sha256": fingerprint(raw),
        "source_model_sha256": raw["model_sha256"],
        "source_engine_fingerprint": raw["engine_fingerprint"],
        "refinement_model_sha256": {"review": "b" * 64},
        "refinement_engine_fingerprint": "bounded-source-review",
        "independent_observations": False,
        "fields": fields,
    }


class NumericCapRefinementTests(unittest.TestCase):
    def test_parsers_keep_slash_variant_diagnostic_and_require_complete_ratio(self):
        self.assertEqual(parse_cap("/300"), (300, None))
        self.assertEqual(parse_cap("V1500"), (1500, "ocr_slash_variant"))
        self.assertIsNone(parse_cap("1400"))
        self.assertEqual(parse_ratio("599/1600"), (599, 1600))
        self.assertIsNone(parse_ratio("599/16"))
        # A syntactically short prefix must remain unresolved when the
        # apparent current exceeds the apparent cap.
        self.assertIsNone(parse_ratio("599/160"))
        self.assertIsNone(parse_ratio("115/84"))


    def test_candidates_require_source_performance_panel_geometry(self):
        # Slash-shaped numbers alone occur in other screens.  With no stable
        # panel rows or headings, discovery must not schedule five OCR crops.
        lines = [
            {"text": "/400", "confidence": 99,
             "box": [202, 321, 252, 345]},
            {"text": "/400", "confidence": 99,
             "box": [203, 377, 252, 401]},
            {"text": "/400", "confidence": 99,
             "box": [202, 433, 252, 457]},
            {"text": "Story menu", "confidence": 99,
             "box": [400, 300, 550, 340]},
        ]
        raw = {"lines": lines, "regions": {}, "result_grid": False}
        self.assertIsNone(performance_panel_geometry(raw))
        self.assertFalse([item for item in candidate_fields(raw)
                          if item["kind"] == "performance_cap"])


    def test_stat_cap_candidate_is_labeled_and_bounded_when_one_row_is_missing(self):
        columns = ((309, 721, 366, 747), (406, 721, 463, 747),
                   (503, 721, 560, 747), (598, 721, 655, 749),
                   (692, 721, 747, 748))
        lines = []
        for field, (left, _top, right, _bottom) in zip(
                ("speed", "stamina", "power", "guts", "wit"), columns):
            lines.append({"text": field.title(), "confidence": 99,
                          "box": [left - 8, 696, right - 4, 720]})
            if field != "speed":
                lines.append({"text": "/1600", "confidence": 99,
                              "box": [left - 4, 742, right - 2, 765]})
        requests = _main_stat_cap_requests(lines)
        self.assertEqual([item[0] for item in requests], ["stats_cap.speed"])
        self.assertEqual(requests[0][1], (297, 728.0, 378, 808.0))
        self.assertEqual(requests[0][2]["geometry_basis"],
                         "labeled_main_stat_bar_cap")
        self.assertEqual(read_stat_caps(lines, {}, current_grid=False), ({}, {}))


if __name__ == "__main__":
    unittest.main()
