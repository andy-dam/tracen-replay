"""A proven spark marker survives an unresolved unmarked OCR variant."""
from copy import deepcopy
import unittest

from tracen_replay.hint_identity_fallback import preserve_valid_circle_effect
from test_hint_identity_fallback import _case


def case():
    event, rows = _case(strong_amount=None, weak_amount=None, weak_name='Example Skill ')
    event['field_evidence'] = {key.replace('skill_hint_change', 'inheritance_spark'): value
                               for key, value in event['field_evidence'].items()}
    for effect in event['effects'] + [effect for row in rows.values() for effect in row['effects']]:
        effect['kind'] = 'inheritance_spark'
        effect['raw_text'] = effect['name'] + ' spark activated!'
        effect['original_text'] = 'Example Skill  spark activated!'
        if 'visual_symbol_observation' in effect:
            proof = effect['visual_symbol_observation']
            proof.pop('box')
            proof.update(method='inline_spark_ring_geometry', center=[300, 825],
                         ocr_line_box=[315, 810, 618, 840],
                         ocr_line_coordinate_space='source_frame', slot_distance_px=170)
    for row in rows.values():
        effect = row['effects'][0]
        row['ocr']['neural'] = [dict(text=effect['raw_text'], confidence=99,
                                   box=[315, 810, 618, 840],
                                   visual_symbol_observation=deepcopy(effect['visual_symbol_observation']))]
    return event, rows


def preserve(event, rows):
    return preserve_valid_circle_effect(event, rows, effect_kind='inheritance_spark')


class SparkIdentityFallbackTests(unittest.TestCase):
    def test_repeated_inline_proof_preserved_without_merging_weak_identity(self):
        event, rows = case()
        before = deepcopy(rows)
        preserve(event, rows)
        self.assertEqual([e['name'] for e in event['effects']], ['Example Skill ○'])
        candidate = event['ambiguous_effect_candidates'][0]
        self.assertEqual(candidate['effect']['name'], 'Example Skill ')
        self.assertIsNone(candidate['occurrence_count'])
        self.assertFalse(candidate['continuity_proven'])
        self.assertIn('inline_spark', candidate['basis'])
        self.assertEqual(rows, before)
        once = deepcopy(event)
        preserve(event, rows)
        self.assertEqual(event, once)

    def test_wrong_method_position_clock_or_source_cannot_preserve(self):
        for field, value in [('method', 'strict_terminal_ring_geometry'),
                             ('method', 'inventory_ring_geometry'),
                             ('center', [450, 825]), ('center', [300, 900]),
                             ('center', [float('nan'), 825]),
                             ('source_timestamp_ms', 0), ('evidence', 'different'),
                             ('source_sha256', 'b' * 64), ('gameplay_sha256', 'bad'),
                             ('ocr_line_box', [310, 810, 618, 840]),
                             ('slot_distance_px', 171), ('ocr_line_coordinate_space', 'gameplay_crop')]:
            with self.subTest(field=field, value=value):
                event, rows = case()
                row = rows['strong-1']
                row['effects'][0]['visual_symbol_observation'][field] = value
                row['ocr']['neural'][0]['visual_symbol_observation'][field] = value
                before = deepcopy(event)
                preserve(event, rows)
                self.assertEqual(event, before)

    def test_single_frame_occlusion_or_conflicting_payload_cannot_preserve(self):
        for change in ('one_frame', 'occlusion', 'payload', 'row_payload', 'kind'):
            with self.subTest(change=change):
                event, rows = case()
                if change == 'one_frame':
                    rows.pop('strong-1')
                elif change == 'occlusion':
                    rows['strong-1']['ocr']['neural'][0]['overlay_occluded'] = True
                elif change == 'payload':
                    event['effects'][1]['amount'] = 2
                elif change == 'row_payload':
                    rows['strong-1']['effects'][0]['value'] = 2
                else:
                    rows['strong-1']['effects'][0]['kind'] = 'skill_hint_change'
                before = deepcopy(event)
                preserve(event, rows)
                self.assertEqual(event, before)

    def test_default_hint_pass_does_not_consume_sparks(self):
        event, rows = case()
        before = deepcopy(event)
        preserve_valid_circle_effect(event, rows)
        self.assertEqual(event, before)
