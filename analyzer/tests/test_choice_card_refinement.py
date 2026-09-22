import copy
import hashlib
import json
from types import SimpleNamespace
from pathlib import Path
import unittest
from unittest.mock import Mock

import numpy as np
from PIL import Image, ImageDraw

from tracen_replay.choice_evidence import observe, reconstruct
from tests import test_choice_evidence as choice_tests


class ChoiceCardRefinementTests(unittest.TestCase):
    def test_source_card_text_and_native_collapse_regressions(self):
        fixture=json.loads((Path(__file__).parent/'fixtures/choice-card-temporal-regression-v1.json').read_text(encoding='utf-8'))
        for case in fixture['cases']:
            with self.subTest(case=case['name']):
                events=reconstruct(case['observations'])
                self.assertEqual([{k:e[k] for k in ('options','selected_index','selected_text')} for e in events],case['expected'])

    def scene(self):
        pane = Image.new('RGB', (810, 1080))
        draw = ImageDraw.Draw(pane)
        for top in (603, 714):
            draw.rectangle((120, top, 685, top + 80), fill='white')
        lines = [dict(text=text, confidence=confidence, box=[317, top + 25, 600, top + 53])
                 for text, confidence, top in [('First...', 94, 603), ('Second.', 99, 714)]]
        raw = dict(lines=lines, gameplay_sha256=hashlib.sha256(pane.tobytes()).hexdigest())
        return pane, raw

    def reader(self, text='First...', confidence=.99):
        return SimpleNamespace(models={'test': 'digest'}, fingerprint='test', engine=Mock(return_value=
            SimpleNamespace(txts=[text], scores=[confidence], boxes=[np.array([[3,25],[286,25],[286,53],[3,53]])])))

    def rows(self):
        pane, raw = self.scene()
        rows = []
        # Each slot has two strong observations, but there is only one complete frame.
        for time, confidences in [(0, (99,99)), (250, (99,94)), (500, (94,99))]:
            lines = copy.deepcopy(raw['lines'])
            for line, confidence in zip(lines, confidences):
                line['confidence'] = confidence
            rows.append(dict(observe(pane, lines, include_slots=True), source_timestamp_ms=time, evidence=f'{time}.png'))
        rows.append(dict(source_timestamp_ms=750, evidence='marks.png', offered_card_candidates=[],
                         selection_mark_pairs=[dict(left=dict(box=[258,703,313,736]), right=dict(box=[798,700,853,736]))]))
        return rows

    def test_separate_frames_support_each_slot_before_selection(self):
        event = reconstruct(self.rows())[0]
        self.assertEqual(event['options'], ['First...', 'Second.'])
        self.assertEqual(event['selected_index'], 1)
        self.assertEqual(event['selection_basis'], 'repeated_card_text_and_bilateral_selection_marks')
        self.assertEqual(event['evidence'], ['0.png','250.png','500.png','marks.png'])

    def test_one_source_frame_repeated_is_not_consensus(self):
        rows = self.rows()
        self.assertEqual(reconstruct([rows[0], copy.deepcopy(rows[0]), rows[-1]]), [])

    def test_unreadable_or_weak_only_slot_prevents_incomplete_menu_selection(self):
        for text in (None, 'Second.'):
            rows = self.rows()
            for row in rows[:-1]:
                row['offered_card_slots'][1].update(text=text, confidence=94)
                row['offered_card_candidates'] = row['offered_card_candidates'][:1]
            self.assertEqual(reconstruct(rows), [])

    def test_conflicting_weak_text_and_geometry_change_reset_consensus(self):
        rows = self.rows()
        rows[1]['offered_card_slots'][1]['text'] = 'Different.'
        self.assertEqual(reconstruct(rows), [])
        rows = self.rows()
        rows[1]['offered_card_slots'][1]['card_y'][0] += 10
        self.assertEqual(reconstruct(rows), [])

    def test_interleaved_partial_menu_invalidates_unconfirmed_history(self):
        for rows in (self.rows(),choice_tests.ChoiceEvidenceTests().observations()):
            different=copy.deepcopy(rows[0])
            different.update(source_timestamp_ms=100,evidence='different.png',menu_text_complete=False)
            different.pop('offered_card_slots',None)
            different['offered_card_candidates'][0]['text']='A different menu'
            self.assertEqual(reconstruct(rows[:1]+[different]+rows[1:]),[])

    def test_boundary_staleness_and_missing_marks_abstain(self):
        rows = self.rows()
        rows.insert(2, dict(source_timestamp_ms=300, screen_boundary=True))
        self.assertEqual(reconstruct(rows), [])
        rows = self.rows()
        rows[-1]['source_timestamp_ms'] = 2500
        self.assertEqual(reconstruct(rows), [])
        rows = self.rows()
        rows[-1]['selection_mark_pairs'] = []
        self.assertEqual(reconstruct(rows), [])

    def test_swapped_option_order_invalidates_active_menu_before_marks(self):
        rows = self.rows()
        swapped = copy.deepcopy(rows[0])
        swapped.update(source_timestamp_ms=600,evidence='swapped.png')
        for field in ('offered_card_slots', 'offered_card_candidates'):
            swapped[field][0]['text'], swapped[field][1]['text'] = swapped[field][1]['text'], swapped[field][0]['text']
        self.assertEqual(reconstruct(rows[:-1]+[swapped,rows[-1]]), [])

    def test_selected_card_collapse_cannot_replace_full_menu_with_remaining_cards(self):
        rows = self.rows()
        original = rows[0]['offered_card_slots']
        for time in (550,600,650):
            partial = copy.deepcopy(rows[0])
            partial.update(source_timestamp_ms=time,evidence=f'{time}.png',
                           offered_card_candidates=[copy.deepcopy(original[0])],
                           offered_card_slots=[copy.deepcopy(original[0])],
                           selected_card_candidates=[copy.deepcopy(original[1])] if time==550 else [])
            rows.insert(-1,partial)
        event = reconstruct(rows)[0]
        self.assertEqual(event['options'],['First...','Second.'])
        self.assertEqual(event['selected_index'],1)
        self.assertIn('550.png',event['evidence'])

    def test_shrinking_white_cards_without_readable_green_card_preserve_menu(self):
        rows=self.rows()
        for time in (550,600):
            partial=copy.deepcopy(rows[0])
            partial.update(source_timestamp_ms=time,evidence=f'{time}.png')
            for field in ('offered_card_slots','offered_card_candidates'):
                partial[field]=partial[field][1:]
            rows.insert(-1,partial)
        event=reconstruct(rows)[0]
        self.assertEqual(event['kind'],'dialogue_choice')
        self.assertEqual(event['options'],['First...','Second.'])

    def test_all_unknown_menu_interrupts_active_identity(self):
        rows=self.rows()
        unknown=copy.deepcopy(rows[0])
        unknown.update(source_timestamp_ms=600,evidence='unknown.png',offered_card_candidates=[],menu_text_complete=False)
        for slot in unknown['offered_card_slots']:
            slot.update(text=None,confidence=0)
        rows.insert(-1,unknown)
        self.assertEqual(reconstruct(rows),[])

    def test_horizontal_geometry_changes_do_not_establish_same_menu(self):
        for coordinate in (0,2):
            rows=self.rows()
            for field in ('offered_card_slots','offered_card_candidates'):
                for card in rows[1][field]:
                    card['text_box'][coordinate]+=60
            self.assertEqual(reconstruct(rows),[])
