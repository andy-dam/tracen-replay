import copy
import hashlib
import json
from pathlib import Path
import unittest

from PIL import Image, ImageDraw

from tracen_replay.receipt_symbols import annotate, detect_spark_circle, has_eligible_receipt, spark_slot_geometry
from tracen_replay.vision import parse


class SparkSymbolTests(unittest.TestCase):
    def fixture(self, text='Example Skill  spark activated!'):
        pane = Image.new('RGB', (810, 1080), 'white')
        ImageDraw.Draw(pane).ellipse((292, 817, 308, 833), outline=(80, 80, 80), width=1)
        raw = dict(lines=[dict(text=text, confidence=98, box=[315, 810, 618, 840])],
                   regions={}, header='', current_grid=False, result_grid=False,
                   source_timestamp_ms=250, evidence='source.png', source_frame_sha256='a' * 64,
                   gameplay_sha256=hashlib.sha256(pane.tobytes()).hexdigest())
        proof = {key: raw[key] for key in ('source_timestamp_ms', 'evidence',
                                         'source_frame_sha256', 'gameplay_sha256')}
        proof['evidence_sha256'] = 'b' * 64
        return pane, raw, proof

    def test_source_bound_ring_fills_only_the_explicit_symbol_slot(self):
        for text in ('Example Skill  spark activated!', 'Example Skill O spark activated!'):
            pane, raw, proof = self.fixture(text)
            before = copy.deepcopy(raw)
            self.assertTrue(has_eligible_receipt(raw['lines']))
            fixed = annotate(raw, pane, proof)
            line = fixed['lines'][0]
            self.assertEqual(line['text'], 'Example Skill ○ spark activated!')
            self.assertEqual(line['confidence'], 98)
            self.assertEqual(line['original_text'], text)
            self.assertEqual(line['visual_symbol_observation']['ocr_line_box'], raw['lines'][0]['box'])
            self.assertAlmostEqual(line['visual_symbol_observation']['slot_distance_px'], 170, delta=1)
            self.assertEqual(raw, before)
            effect = next(e for e in parse(fixed)['effects'] if e['kind'] == 'inheritance_spark')
            self.assertEqual(effect['name'], 'Example Skill ○')
            self.assertEqual(effect['visual_symbol_observation']['source_timestamp_ms'], 250)

    def test_no_slot_or_already_marked_receipt_is_unchanged(self):
        for text in ('Example Skill spark activated!', 'Example Skill ○ spark activated!',
                     'Example Skill ◎ spark activated!', 'Example Skill  spark activate',
                     'Inspired by Example Skill O!', 'Example Skill   spark activated!',
                     'Example Skill    spark activated!', 'Example Skill  O spark activated!'):
            pane, raw, proof = self.fixture(text)
            self.assertFalse(has_eligible_receipt(raw['lines']))
            self.assertEqual(annotate(raw, pane, proof), raw)

    def test_missing_filled_double_or_multiple_rings_abstain(self):
        for kind in ('missing', 'filled', 'double', 'multiple', 'mixed'):
            pane, raw, proof = self.fixture()
            draw = ImageDraw.Draw(pane)
            if kind == 'missing':
                draw.rectangle((292, 817, 308, 833), fill='white')
            elif kind == 'filled':
                draw.ellipse((292, 817, 308, 833), fill=(80, 80, 80))
            elif kind in ('double', 'mixed'):
                draw.ellipse((295, 820, 305, 830), outline=(80, 80, 80), width=1)
                if kind == 'mixed':
                    draw.ellipse((320, 817, 336, 833), outline=(80, 80, 80), width=1)
            else:
                draw.ellipse((320, 817, 336, 833), outline=(80, 80, 80), width=1)
            self.assertIsNone(detect_spark_circle(pane, raw['lines'][0]['box']), kind)

    def test_wrong_tail_location_and_other_screen_regions_abstain(self):
        pane, raw, _ = self.fixture()
        for box in ([315, 810, 720, 840], [315, 610, 618, 640], [315, 810, 618, 880],
                    [315, 810, 583, 840], [315, 810, 653, 840],
                    [315, 810, 598, 840], [315, 810, 638, 840]):
            self.assertIsNone(detect_spark_circle(pane, box))
        for center in ([10 ** 1000, 825], [300, 10 ** 1000], [float('nan'), 825]):
            self.assertIsNone(spark_slot_geometry(raw['lines'][0]['box'], center))

    def test_occluded_or_low_confidence_receipt_stays_unchanged(self):
        for change in (dict(overlay_occluded=True), dict(confidence=94)):
            pane, raw, proof = self.fixture()
            raw['lines'][0].update(change)
            self.assertEqual(annotate(raw, pane, proof), raw)

    def test_stale_pixels_and_source_metadata_reject_annotation(self):
        pane, raw, proof = self.fixture()
        for key in ('gameplay_sha256', 'source_frame_sha256', 'source_timestamp_ms'):
            wrong = dict(proof, **{key: 'changed'})
            with self.assertRaises(ValueError):
                annotate(raw, pane, wrong)
        pane.putpixel((0, 0), (1, 2, 3))
        with self.assertRaises(ValueError):
            annotate(raw, pane, proof)

    def test_existing_terminal_letter_controls_do_not_become_inline_markers(self):
        root = Path(__file__).parent / 'fixtures'
        metadata = json.loads((root / 'inventory-suffix-terminal-controls.json').read_text(encoding='utf-8'))
        path = root / metadata['fixture']
        self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(), metadata['fixture_sha256'])
        with Image.open(path) as image:
            original = image.convert('RGB')
        for control in metadata['controls']:
            left, top, right, bottom = control['box']
            crop = original.crop((left - 148 - 3, top - 3, right - 148 + 3, bottom + 3))
            pane = Image.new('RGB', (810, 1080), 'white')
            pane.paste(crop, (270, 815))
            line_box = [315, 818, 270 + crop.width - 3 + 148 + 165, 818 + bottom - top]
            self.assertIsNone(detect_spark_circle(pane, line_box), control)


if __name__ == '__main__':
    unittest.main()
