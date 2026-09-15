from __future__ import annotations

import json
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
import shutil
from types import SimpleNamespace
import unittest

from tests import localdata
from tests.test_gameplay import workspace_temp
from tracen_replay.lesson_offer_preparation import (
    LessonOfferPreparationError,
    discover,
    group_lesson_offer_candidates,
    prepare,
    select_lesson_offer_occurrences,
)


SOURCE_ROOT = localdata.root("development_third_recording_baseline")
RAW_PATH = SOURCE_ROOT / "neural/part-005-frame-000094.json"
GAMEPLAY_PATH = SOURCE_ROOT / "gameplay/part-005-frame-000094.png"
SOURCE_FRAME_PATH = SOURCE_ROOT / "part-005/frames/000094.jpg"


class _TextRecInput:
    def __init__(self, *, img):
        self.img = img


class _SourceCropReader:
    def __init__(self):
        self.models = {"det": "det-digest", "rec": "rec-digest"}
        self.fingerprint = "lesson-offer-preparation-test-engine"
        self.TextRecInput = _TextRecInput
        self.engine = SimpleNamespace(text_rec=self._read)

    def _read(self, request):
        values = [
            "0", "10", "0", "0", "0",
            "0", "15", "0", "0", "0",
            "0", "0", "10", "0", "0",
        ]
        return SimpleNamespace(txts=values, scores=[0.99] * len(values))


class LessonOfferPreparationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not all(path.is_file() for path in (RAW_PATH, GAMEPLAY_PATH, SOURCE_FRAME_PATH)):
            raise unittest.SkipTest("Independent-02 lesson source fixture is unavailable")

    def _source(self, root: Path) -> Path:
        base = root / "source"
        (base / "neural").mkdir(parents=True)
        (base / "gameplay").mkdir()
        (base / "part-005/frames").mkdir(parents=True)
        shutil.copy2(RAW_PATH, base / "neural/part-005-frame-000094.json")
        shutil.copy2(GAMEPLAY_PATH, base / "gameplay/part-005-frame-000094.png")
        shutil.copy2(SOURCE_FRAME_PATH, base / "part-005/frames/000094.jpg")
        raw = json.loads(RAW_PATH.read_text(encoding="utf-8"))
        capture = {
            "source": {"sha256": "a" * 64},
            "frames": [
                {
                    "id": "part-005-frame-000094",
                    "source_timestamp_ms": raw["source_timestamp_ms"],
                    "evidence": "part-005/frames/000094.jpg",
                }
            ],
        }
        (base / "capture.json").write_text(json.dumps(capture), encoding="utf-8")
        return base

    def test_dry_run_selects_only_missing_source_costs_without_reader(self):
        with workspace_temp() as root:
            source = self._source(root)
            inventory = discover(source, name="fixture")
            self.assertEqual(inventory["capture_frame_count"], 1)
            self.assertEqual(inventory["selected_candidates"], ["part-005-frame-000094"])
            self.assertEqual(inventory["estimated_ocr_batches"], 3)
            self.assertEqual(inventory["estimated_ocr_crops"], 45)
            candidate = inventory["candidates"][0]
            self.assertEqual(candidate["status"], "candidate")
            self.assertIn("visual", candidate["missing_fields"])
            self.assertIn("composure", candidate["missing_fields"])

    def test_write_copies_triplet_generates_sidecar_and_validates_cached_path(self):
        with workspace_temp() as root:
            source = self._source(root)
            output = root / "output"
            result = prepare(
                source,
                output,
                name="fixture",
                reader=_SourceCropReader(),
                write=True,
            )
            self.assertEqual(len(result["generated"]), 1)
            target = output / "fixture"
            self.assertTrue((target / "capture.json").is_file())
            self.assertTrue((target / "neural/part-005-frame-000094.json").is_file())
            self.assertTrue((target / "gameplay/part-005-frame-000094.png").is_file())
            self.assertTrue((target / "part-005/frames/000094.jpg").is_file())
            sidecar = target / "lesson-offer-refinement/part-005-frame-000094.json"
            self.assertTrue(sidecar.is_file())
            self.assertEqual(result["generated"][0]["cached_offer_count"], 3)
            self.assertEqual(result["generated"][0]["cached_complete_offer_count"], 3)
            self.assertFalse(result["unresolved"])

    def test_output_inside_source_cache_is_rejected(self):
        with workspace_temp() as root:
            source = self._source(root)
            with self.assertRaisesRegex(LessonOfferPreparationError, "inside an input cache"):
                prepare(source, source / "scratch", write=False)

    def test_cli_module_entrypoint_writes_inventory_without_ocr(self):
        with workspace_temp() as root:
            source = self._source(root)
            inventory_path = root / "inventory.json"
            output = root / "output"
            from tracen_replay.lesson_offer_preparation import main

            stdout = StringIO()
            with redirect_stdout(stdout):
                main(
                    [
                        "--output",
                        str(output),
                        "--recording",
                        f"fixture={source}",
                        "--inventory-json",
                        str(inventory_path),
                    ]
                )
            self.assertTrue(inventory_path.is_file())
            payload = json.loads(inventory_path.read_text(encoding="utf-8"))
            self.assertEqual(payload["recordings"][0]["name"], "fixture")
            self.assertEqual(payload["recordings"][0]["runtime"], None)
            self.assertIn('"candidate_count": 1', stdout.getvalue())

    @staticmethod
    def _candidate(index, *, name="Group Lesson Basics", status="candidate",
                   missing=("visual",), effects=(), part="part-001"):
        return {
            "frame_id": f"{part}-frame-{index:06d}",
            "capture_index": index,
            "source_timestamp_ms": index * 250,
            "source_frame_evidence": f"{part}/frames/{index:06d}.jpg",
            "source_frame_sha256": f"{index:064x}"[-64:],
            "gameplay_evidence": f"gameplay/{part}-frame-{index:06d}.png",
            "raw_evidence": f"neural/{part}-frame-{index:06d}.json",
            "sidecar": f"lesson-offer-refinement/{part}-frame-{index:06d}.json",
            "status": status,
            "missing_fields": list(missing),
            "estimated_ocr_batches": 3,
            "estimated_ocr_crops": 45,
            "offers": [
                {
                    "offer_id": f"volatile-{index}",
                    "card_index": 0,
                    "name": name,
                    "status": "unknown",
                    "missing_fields": list(missing),
                    "effects": [dict(effect) for effect in effects],
                }
            ],
        }

    def test_grouping_ignores_volatile_ids_and_missing_slot_fluctuation(self):
        rows = [
            self._candidate(1, missing=("visual", "composure")),
            self._candidate(2, missing=("visual",)),
            self._candidate(3, name="Dance Step Basics"),
        ]
        groups = group_lesson_offer_candidates(rows)
        self.assertEqual(len(groups), 2)
        self.assertEqual(groups[0]["member_frame_ids"], [rows[0]["frame_id"], rows[1]["frame_id"]])
        self.assertEqual(groups[1]["member_frame_ids"], [rows[2]["frame_id"]])
        self.assertEqual(groups[0]["members"][0]["source_frame_sha256"], rows[0]["source_frame_sha256"])

    def test_grouping_preserves_capture_gaps_and_source_segment_transitions(self):
        rows = [
            self._candidate(1),
            self._candidate(3),
            self._candidate(4),
            self._candidate(5, part="part-002"),
        ]
        groups = group_lesson_offer_candidates(rows)
        self.assertEqual(
            [group["member_frame_ids"] for group in groups],
            [[rows[0]["frame_id"]], [rows[1]["frame_id"], rows[2]["frame_id"]], [rows[3]["frame_id"]]],
        )

    def test_selection_budget_keeps_deferred_reasons_and_member_proofs(self):
        rows = [
            self._candidate(1),
            self._candidate(2, name="Dance Step Basics"),
            self._candidate(3, name="Vocal Training Basics"),
        ]
        result = select_lesson_offer_occurrences(
            rows,
            max_occurrences=1,
            max_ocr_crops=45,
            max_ocr_batches=3,
        )
        self.assertEqual(result["selected_ids"], [rows[0]["frame_id"]])
        self.assertEqual(result["budget"]["selected_occurrences"], 1)
        self.assertEqual(len(result["deferred"]), 2)
        self.assertTrue(all(item["reason"] == "occurrence_budget_exhausted" for item in result["deferred"]))
        self.assertEqual(
            result["occurrences"][1]["representative_frame_id"],
            rows[1]["frame_id"],
        )
        self.assertEqual(
            result["occurrences"][1]["members"][0]["source_frame_evidence"],
            rows[1]["source_frame_evidence"],
        )

    def test_existing_valid_sidecar_is_reused_without_consuming_ocr_budget(self):
        rows = [
            self._candidate(1, status="existing_valid"),
            self._candidate(2, status="candidate"),
        ]
        result = select_lesson_offer_occurrences(rows, max_occurrences=0, max_ocr_crops=0, max_ocr_batches=0)
        self.assertEqual(result["selected_ids"], [rows[0]["frame_id"]])
        self.assertEqual(result["budget"]["selected_occurrences"], 0)
        self.assertEqual(result["occurrences"][0]["selection_reason"], "existing_sidecar_reuse")
        self.assertEqual(result["deferred"][0]["reason"], "occurrence_deduplicated_existing_sidecar")


if __name__ == "__main__":
    unittest.main()
