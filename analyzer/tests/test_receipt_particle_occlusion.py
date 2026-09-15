import hashlib
import json
from pathlib import Path
import unittest

from PIL import Image, ImageDraw

from tests import localdata
from tracen_replay.receipt_occlusion import annotate, annotate_path, animated_overlay_boxes
from tracen_replay.transactions import outcome_events
from tracen_replay.vision import parse


def receipt_line(text, box, confidence=99):
    return {"text": text, "box": list(box), "confidence": confidence}


def raw(lines, pane):
    return {
        "lines": [dict(line) for line in lines],
        "regions": {},
        "header": "",
        "current_grid": False,
        "result_grid": False,
        "gameplay_sha256": hashlib.sha256(pane.tobytes()).hexdigest(),
    }


class ReceiptParticleOcclusionTests(unittest.TestCase):
    def particle_pane(self, *, left=325, top=819, right=360, bottom=853):
        pane = Image.new("RGB", (810, 1080), "white")
        # Global OCR x coordinates start at 148; this local rectangle therefore
        # appears in gameplay space as x=473..508.
        ImageDraw.Draw(pane).rectangle((left, top, right, bottom), fill=(240, 230, 100))
        return pane

    def test_pastel_particle_crossing_receipt_is_source_obstruction(self):
        pane = self.particle_pane()
        line = receipt_line(
            "Friendship with Example Name went up by 5.",
            [316, 831, 766, 860],
        )
        source = raw([line], pane)

        boxes = animated_overlay_boxes(pane, [line["box"]])
        self.assertEqual(boxes, [[473, 819, 509, 854]])
        marked = annotate(source, pane)
        self.assertEqual(parse(marked)["effects"], [])
        self.assertEqual(marked["lines"][0]["confidence"], 0)
        blocked = marked["occluded_receipt_lines"][0]
        self.assertTrue(blocked["animated_overlay_occluded"])
        self.assertEqual(blocked["animated_overlay_boxes"], boxes)
        self.assertEqual(source["lines"][0]["confidence"], 99)
        self.assertEqual(marked["receipt_overlay_evidence"]["overlay_boxes"], [])
        self.assertEqual(marked["receipt_overlay_evidence"]["animated_overlay_boxes"], boxes)

    def test_particle_outside_receipt_window_is_ignored(self):
        pane = self.particle_pane(left=20, top=819, right=55, bottom=853)
        line = receipt_line("Power went up by 3.", [316, 831, 540, 860])
        source = raw([line], pane)

        self.assertEqual(animated_overlay_boxes(pane, [line["box"]]), [])
        marked = annotate(source, pane)
        self.assertEqual(marked, source)
        self.assertEqual(parse(marked)["effects"][0]["amount"], 3)

    def test_thin_coloured_text_fringe_is_not_a_particle(self):
        pane = Image.new("RGB", (810, 1080), "white")
        draw = ImageDraw.Draw(pane)
        # A connected antialias fringe exceeds the old size threshold but
        # contains no solid pastel patch. Vary its position and receipt text.
        draw.rectangle((325, 819, 326, 840), fill=(190, 225, 255))
        draw.rectangle((327, 839, 330, 840), fill=(190, 225, 255))
        line = receipt_line("Power went up by 3.", [316, 810, 700, 850])
        self.assertEqual(animated_overlay_boxes(pane, [line["box"]]), [])
        self.assertEqual(parse(annotate(raw([line], pane), pane))["effects"][0]["amount"], 3)

    def test_solid_pastel_fringe_attached_to_blue_ink_is_not_a_particle(self):
        pane = Image.new('RGB', (810, 1080), 'white')
        draw = ImageDraw.Draw(pane)
        draw.rectangle((324, 818, 329, 839), fill=(90, 160, 220))
        draw.rectangle((325, 819, 328, 838), fill=(195, 244, 255))
        line = receipt_line('Energy went down by 19.', [316, 810, 700, 850])
        self.assertEqual(animated_overlay_boxes(pane, [line['box']]), [])
        self.assertEqual(parse(annotate(raw([line], pane), pane))['effects'][0]['amount'], -19)
        # A real cyan particle crossing the same ink still has an independent
        # colour core and must continue to block the receipt.
        draw.rectangle((320, 824, 346, 847), fill=(195, 244, 255))
        self.assertTrue(animated_overlay_boxes(pane, [line['box']]))
        self.assertEqual(parse(annotate(raw([line], pane), pane))['effects'], [])

    @localdata.needs("fourth_recording_retest_older",
                      "numeric-receipt-recovery/receipt-inspection/55500-56750-30/frame-000024.png")
    def test_fourth_blue_receipt_ink_keeps_source_energy_decreases(self):
        root = localdata.root("fourth_recording_retest_older",
                              "numeric-receipt-recovery/receipt-inspection")
        for window in ('55500-56750-30', '78000-79250-30', '133000-134250-30'):
            with self.subTest(window=window):
                image = root / window / 'frame-000024.png'
                observation = image.with_suffix('.v2.json')
                if not observation.exists():
                    observation = image.with_suffix('.json')
                payload = json.loads(observation.read_text(encoding='utf-8'))
                effects = parse(annotate_path(payload, image))['effects']
                self.assertEqual([e['amount'] for e in effects if e['kind'] == 'energy_change'], [-19])

    @localdata.needs("prepared_snapshot_late", "v1/gameplay/part-011-frame-000348.png")
    def test_blue_receipt_ink_preserves_original_energy_evidence(self):
        root = localdata.root("prepared_snapshot_late", "v1")
        rows = []
        for number in (348, 349):
            with self.subTest(frame=number):
                name = f"part-011-frame-{number:06d}"
                payload = json.loads((root / "neural" / f"{name}.json").read_text(encoding="utf-8"))
                marked = annotate_path(payload, root / "gameplay" / f"{name}.png")
                reading = parse(marked)
                effects = reading["effects"]
                self.assertTrue(any(effect.get("kind") == "energy_change" and
                                    effect.get("amount") == -13 for effect in effects))
                reading.update(source_timestamp_ms=1406750 + (number - 348) * 250,
                               evidence=f"gameplay/{name}.png")
                rows.append(reading)
        events = outcome_events(rows)
        self.assertEqual(len(events), 1)
        self.assertEqual([effect["amount"] for effect in events[0]["effects"]
                          if effect.get("kind") == "energy_change"], [-13])

    def test_particle_does_not_cross_assign_distinct_receipt_slots(self):
        pane = self.particle_pane()
        lines = [
            receipt_line("Friendship with First Person went up by 5.", [316, 831, 766, 860]),
            receipt_line("Friendship with Second Person went up by 5.", [316, 871, 766, 900]),
        ]
        marked = annotate(raw(lines, pane), pane)

        self.assertEqual(marked["lines"][0]["confidence"], 0)
        self.assertEqual(marked["lines"][1]["confidence"], 99)
        effects = parse(marked)["effects"]
        self.assertEqual([(effect["kind"], effect.get("name"), effect.get("amount")) for effect in effects],
                         [("friendship_change", "Second Person", 5)])

    @localdata.needs(
        "prepared_snapshot_early",
        "independent-02/numeric-receipt-recovery/receipt-inspection/"
        "1652000-1653750-30/frame-000027.png",
    )
    def test_g7_particle_sequence_keeps_only_later_clear_identity(self):
        root = localdata.root(
            "prepared_snapshot_early",
            "independent-02/numeric-receipt-recovery/receipt-inspection/"
            "1652000-1653750-30",
        )
        rows = []
        for number in range(27, 47):
            evidence = root / f"frame-{number:06d}.png"
            raw_reading = json.loads(evidence.with_suffix(".v2.json").read_text(encoding="utf-8"))
            marked = annotate_path(raw_reading, evidence)
            reading = parse(marked)
            reading.update(
                source_timestamp_ms=raw_reading["source_timestamp_ms"],
                evidence=evidence.name,
            )
            rows.append(reading)

        first = parse(annotate_path(
            json.loads((root / "frame-000027.v2.json").read_text(encoding="utf-8")),
            root / "frame-000027.png",
        ))
        last = parse(annotate_path(
            json.loads((root / "frame-000045.v2.json").read_text(encoding="utf-8")),
            root / "frame-000045.png",
        ))
        self.assertFalse(any(effect.get("kind") == "friendship_change" for effect in first["effects"]))
        self.assertTrue(any(
            effect.get("kind") == "friendship_change"
            and effect.get("name") == "Director Akikawa"
            and effect.get("amount") == 5
            for effect in last["effects"]
        ))
        self.assertEqual(len(animated_overlay_boxes(
            Image.open(root / "frame-000027.png"),
            [line["box"] for line in json.loads((root / "frame-000027.v2.json").read_text(encoding="utf-8"))["lines"]
             if 770 <= line["box"][1] < 1000],
        )), 2)
        self.assertEqual(animated_overlay_boxes(
            Image.open(root / "frame-000045.png"),
            [line["box"] for line in json.loads((root / "frame-000045.v2.json").read_text(encoding="utf-8"))["lines"]
             if 770 <= line["box"][1] < 1000],
        ), [])

        events = outcome_events(rows)
        self.assertEqual(len(events), 1)
        event = events[0]
        friendship = [effect for effect in event["effects"] if effect.get("kind") == "friendship_change"]
        self.assertEqual([(effect.get("name"), effect.get("amount")) for effect in friendship],
                         [("Director Akikawa", 5)])
        self.assertEqual(event.get("conflicting_readings"), [])
        self.assertFalse(event.get("ambiguous_effect_candidates"))


if __name__ == "__main__":
    unittest.main()
