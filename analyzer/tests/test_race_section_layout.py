import hashlib
import json
import unittest
from pathlib import Path

from PIL import Image

from tracen_replay.race_quantity_refinement import (
    QUANTITY_CROP_VARIANT_POLICY,
    SLOT_SPECS,
    _legacy_fixed_geometry_usable,
    apply,
    generate,
    layout_guard,
)
from tracen_replay.race_section_layout import SectionLayoutError, resolve
from tracen_replay.vision import parse
from tests.test_gameplay import workspace_temp


SOURCE_FIXTURE = Path("analyzer/tests/fixtures/race-quantity-refinement-source.png")


def _raw(*, items_box=(270, 751, 329, 778), quantities=None, bonus_box=None, fans=12882, gained=11638):
    lines = [
        {"text": f"Fans {fans:,} (+{gained:,})", "confidence": 99.4, "box": [423, 700, 626, 729]},
        {"text": "Items", "confidence": 99.4, "box": list(items_box)},
    ]
    for text, confidence, box in quantities or [("x400", 99.8, (425, 862, 490, 890))]:
        lines.append({"text": text, "confidence": confidence, "box": list(box)})
    if bonus_box is not None:
        lines.append({"text": "Bonus", "confidence": 99.4, "box": list(bonus_box)})
    return {
        "lines": lines,
        "regions": {},
        "header": "",
        "current_grid": False,
        "result_grid": False,
        "engine_fingerprint": "section-fixture-reader",
        "model_sha256": {"fixture.onnx": "fixture-model"},
    }


def _row(raw):
    row = parse(raw)
    # The production parser supplies these fields for a race result.  Keep
    # tests focused on the section resolver's context contract.
    return row


class RaceSectionLayoutTests(unittest.TestCase):
    def test_lower_items_without_bonus_resolves_only_items(self):
        raw = _raw(quantities=[("x400", 99.891, (425, 862, 490, 890)), ("x1", 95.623, (568, 869, 597, 888))])
        row = _row(raw)

        layout = resolve(raw, row, SLOT_SPECS)

        self.assertIsNotNone(layout)
        self.assertEqual(layout["visible_sections"], ["items"])
        self.assertEqual(set(layout["resolved_slots"]), {"items-0", "items-1", "items-2"})
        self.assertNotIn("bonus-0", layout["resolved_slots"])
        self.assertNotIn("bonus-1", layout["resolved_slots"])
        self.assertEqual(layout["resolved_slots"]["items-1"]["resolved_full_box"], [425, 862, 490, 890])
        self.assertEqual(layout["resolved_slots"]["items-1"]["resolution"], "section_anchor_line")
        self.assertAlmostEqual(
            (layout["resolved_slots"]["items-0"]["resolved_full_box"][1] +
             layout["resolved_slots"]["items-0"]["resolved_full_box"][3]) / 2,
            876,
            delta=1,
        )
        self.assertTrue(layout_guard(raw, row, section_anchor_policy=True))
        self.assertFalse(layout_guard(raw, row))

    def test_quantity_row_far_below_header_is_ignored(self):
        raw = _raw(quantities=[("x400", 99.8, (425, 1010, 490, 1038))])
        row = _row(raw)

        self.assertIsNone(resolve(raw, row, SLOT_SPECS))

    def test_multiple_rows_are_rejected(self):
        raw = _raw(quantities=[
            ("x400", 99.8, (425, 846, 490, 874)),
            ("x1", 99.8, (568, 886, 597, 914)),
        ])
        with self.assertRaisesRegex(SectionLayoutError, "multiple quantity rows"):
            resolve(raw, _row(raw), SLOT_SPECS)

    def test_auxiliary_headers_and_quantities_outside_gameplay_do_not_anchor(self):
        raw = _raw(items_box=(1100, 751, 1159, 778), quantities=[("x400", 99.9, (1125, 862, 1190, 890))])

        self.assertIsNone(resolve(raw, _row(raw), SLOT_SPECS))

    def test_header_must_be_in_left_result_section(self):
        raw = _raw(items_box=(700, 751, 759, 778))

        self.assertIsNone(resolve(raw, _row(raw), SLOT_SPECS))

    def test_quantity_crossing_bonus_header_is_rejected(self):
        raw = _raw(
            quantities=[("x400", 99.8, (425, 862, 490, 875))],
            bonus_box=(270, 880, 329, 908),
        )
        with self.assertRaisesRegex(SectionLayoutError, "crosses the next section"):
            resolve(raw, _row(raw), SLOT_SPECS)

    def test_duplicate_section_header_is_rejected(self):
        raw = _raw()
        raw["lines"].append({"text": "Items", "confidence": 99.5, "box": [270, 752, 329, 779]})

        with self.assertRaisesRegex(SectionLayoutError, "multiple items headers"):
            resolve(raw, _row(raw), SLOT_SPECS)

    def test_shifted_two_section_layout_does_not_use_legacy_geometry(self):
        raw = _raw(
            items_box=(270, 629, 329, 650),
            quantities=[("x400", 99.8, (425, 735, 490, 763))],
            bonus_box=(270, 790, 329, 816),
        )
        raw["lines"].append({"text": "x400", "confidence": 99.8, "box": [310, 900, 378, 928]})
        row = _row(raw)

        self.assertFalse(_legacy_fixed_geometry_usable(raw, row))
        layout = resolve(raw, row, SLOT_SPECS)
        self.assertIsNotNone(layout)
        self.assertEqual(layout["visible_sections"], ["items", "bonus"])

    def test_source_visible_fourth_item_and_third_bonus_get_bounded_dynamic_slots(self):
        raw = _raw(
            items_box=(270, 591, 329, 612),
            quantities=[
                ("x400", 99.8, (425, 699, 490, 727)),
                ("x20", 99.8, (666, 701, 714, 727)),
            ],
            bonus_box=(270, 750, 334, 778),
        )
        raw["lines"].extend([
            {"text": "x400", "confidence": 99.8, "box": [424, 862, 490, 890]},
            {"text": "x20", "confidence": 99.8, "box": [553, 865, 602, 889]},
        ])
        layout = resolve(raw, _row(raw), SLOT_SPECS)

        self.assertEqual(
            set(layout["resolved_slots"]),
            {"items-0", "items-1", "items-2", "items-3", "bonus-0", "bonus-1", "bonus-2"},
        )
        self.assertEqual(layout["resolved_slots"]["items-3"]["resolved_full_box"], [666, 701, 714, 727])
        self.assertEqual(layout["resolved_slots"]["bonus-2"]["resolved_full_box"], [553, 865, 602, 889])


class DynamicRaceQuantityIntegrationTests(unittest.TestCase):
    class Reader:
        models = {"fixture.onnx": "fixture-model"}
        fingerprint = "section-reader"

        def recognize_crops(self, crops):
            return [{"text": "x1", "confidence": 98.4} for _ in crops]

    def _make_run(self, root):
        run = Path(root)
        (run / "neural").mkdir(parents=True)
        (run / "gameplay").mkdir(parents=True)
        (run / "capture").mkdir(parents=True)
        rows = []
        with Image.open(SOURCE_FIXTURE) as original:
            old_pane = original.convert("RGB")
            shifted_pane = Image.new("RGB", old_pane.size, (245, 245, 245))
            shifted_pane.paste(old_pane.crop((0, 0, 810, 919)), (0, 161))
        for index, timestamp in enumerate((1000, 1250, 1500), start=1):
            frame_id = f"part-001-frame-{index:06d}"
            source_path = run / "capture" / f"{index:06d}.png"
            gameplay_path = run / "gameplay" / f"{frame_id}.png"
            full_frame = Image.new("RGB", (1920, 1080), "black")
            full_frame.paste(shifted_pane, (148, 0))
            full_frame.save(source_path, format="PNG")
            shifted_pane.save(gameplay_path, format="PNG")
            source_hash = hashlib.sha256(source_path.read_bytes()).hexdigest()
            gameplay_hash = hashlib.sha256(shifted_pane.tobytes()).hexdigest()
            rows.append({
                "id": frame_id,
                "source_timestamp_ms": timestamp,
                "source_pts": round(timestamp * 60 / 1000),
                "time_base": "1/60",
                "evidence": f"capture/{index:06d}.png",
            })
            raw = _raw(quantities=[("x400", 99.8, (425, 862, 490, 890))])
            raw.update({
                "gameplay_sha256": gameplay_hash,
                "source_timestamp_ms": timestamp,
                "evidence": f"gameplay/{frame_id}.png",
                "source_frame_sha256": source_hash,
            })
            (run / "neural" / f"{frame_id}.json").write_text(json.dumps(raw), encoding="utf-8")
        (run / "capture.json").write_text(json.dumps({"source": {"sha256": "f" * 64}, "frames": rows}), encoding="utf-8")

    def test_items_only_generation_declares_policy_and_applies_resolved_box(self):
        with workspace_temp() as root:
            self._make_run(root)
            summary = generate(root, reader_factory=lambda _: self.Reader())

            self.assertEqual(summary["artifact_count"], 3)
            artifact = json.loads(Path(summary["artifacts"][0]).read_text(encoding="utf-8"))
            self.assertEqual(artifact["section_anchor_policy"]["name"], "section_relative_quantity_row_v1")
            self.assertEqual(artifact["quantity_crop_policy"], QUANTITY_CROP_VARIANT_POLICY)
            self.assertEqual(set(artifact["source_frame_layouts"]), {
                "part-001-frame-000001", "part-001-frame-000002", "part-001-frame-000003",
            })
            self.assertEqual({slot["slot_id"] for slot in artifact["slots"]}, {"items-0", "items-2"})
            self.assertTrue(all(slot["status"] == "accepted" for slot in artifact["slots"]))
            item_zero = next(slot for slot in artifact["slots"] if slot["slot_id"] == "items-0")
            self.assertEqual(
                {observation["crop_variant"] for observation in item_zero["observations"]},
                {"badge", "quantity_focus"},
            )
            self.assertEqual(len(item_zero["observations"]), 18)
            self.assertEqual(
                {tuple(observation["source_crop_box"]) for observation in item_zero["observations"]},
                {(168, 854, 240, 898), (196, 858, 232, 898)},
            )
            self.assertEqual(
                len({observation["crop_evidence"] for observation in item_zero["observations"]}),
                18,
            )
            self.assertTrue(all(
                observation["full_box"][1] >= 850
                for observation in artifact["all_observations"]
            ))

            raw_path = Path(root) / "neural" / "part-001-frame-000001.json"
            raw = json.loads(raw_path.read_text(encoding="utf-8"))
            refined = apply(parse(raw), artifact, raw=raw, root=root)
            quantities = refined["facts"]["visible_item_quantities"]
            self.assertEqual([entry["quantity"] for entry in quantities], [1, 400, 1])
            self.assertEqual([entry["box"][1] for entry in quantities], [856, 862, 856])
            self.assertFalse(refined["facts"]["item_rewards_complete"])

    def test_tampered_base_section_anchor_is_rejected(self):
        with workspace_temp() as root:
            self._make_run(root)
            summary = generate(root, reader_factory=lambda _: self.Reader())
            artifact_path = Path(summary["artifacts"][0])
            artifact = json.loads(artifact_path.read_text(encoding="utf-8"))
            artifact["base_section_layout"]["sections"]["items"]["header"]["box"][1] += 1
            raw = json.loads((Path(root) / "neural" / "part-001-frame-000001.json").read_text(encoding="utf-8"))

            with self.assertRaisesRegex(ValueError, "base section layout changed"):
                apply(parse(raw), artifact, raw=raw, root=root)

    def test_tampered_support_section_anchor_is_rejected(self):
        with workspace_temp() as root:
            self._make_run(root)
            summary = generate(root, reader_factory=lambda _: self.Reader())
            artifact = json.loads(Path(summary["artifacts"][0]).read_text(encoding="utf-8"))
            artifact["source_frame_layouts"]["part-001-frame-000002"]["sections"]["items"]["header"]["box"][1] += 1
            raw = json.loads((Path(root) / "neural" / "part-001-frame-000001.json").read_text(encoding="utf-8"))

            with self.assertRaisesRegex(ValueError, "source section layout changed"):
                apply(parse(raw), artifact, raw=raw, root=root)

    def test_tampered_quantity_focus_geometry_is_rejected(self):
        with workspace_temp() as root:
            self._make_run(root)
            summary = generate(root, reader_factory=lambda _: self.Reader())
            artifact = json.loads(Path(summary["artifacts"][0]).read_text(encoding="utf-8"))
            item_zero = next(slot for slot in artifact["slots"] if slot["slot_id"] == "items-0")
            focused = next(
                observation for observation in item_zero["observations"]
                if observation["crop_variant"] == "quantity_focus"
            )
            focused["source_crop_box"] = [168, 854, 240, 898]
            raw = json.loads((Path(root) / "neural" / "part-001-frame-000001.json").read_text(encoding="utf-8"))

            with self.assertRaisesRegex(ValueError, "source crop geometry changed"):
                apply(parse(raw), artifact, raw=raw, root=root)


if __name__ == "__main__":
    unittest.main()
