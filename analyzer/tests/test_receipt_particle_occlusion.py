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


if __name__ == "__main__":
    unittest.main()
