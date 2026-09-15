import copy
import hashlib
import json
from types import SimpleNamespace
from pathlib import Path
import unittest
from unittest.mock import Mock

import numpy as np
from PIL import Image, ImageDraw

from tracen_replay.choice_card_refinement import build, observation, apply
from tracen_replay.choice_evidence import observe, reconstruct
from tracen_replay.refine_contrast import fingerprint
from tracen_replay.full_recording import cached_readings
from tracen_replay.verify_evidence import verify
from tests.test_gameplay import workspace_temp
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

    def test_exact_weak_text_promoted_without_mutating_original(self):
        pane, raw = self.scene()
        original = copy.deepcopy(raw)
        reader = self.reader()
        extra = build(pane, raw, 'proof', reader)
        result = observation(pane, raw, extra)
        self.assertTrue(result['menu_text_complete'])
        self.assertEqual(result['offered_card_slots'][0]['confidence'], 99)
        self.assertEqual(raw, original)
        self.assertEqual(reader.engine.call_count, 1)  # Accepted second card is preserved.
        self.assertFalse(extra['independent_observations'])

    def test_different_punctuation_and_low_crop_confidence_abstain(self):
        pane, raw = self.scene()
        for reader in (self.reader('First..'), self.reader(confidence=.96)):
            with self.subTest(reader=reader):
                result = observation(pane, raw, build(pane, raw, 'proof', reader))
                self.assertFalse(result['menu_text_complete'])
                self.assertEqual(result['offered_card_slots'][0]['confidence'], 94)

    def test_missing_base_text_is_not_invented_from_crop(self):
        pane, raw = self.scene()
        raw['lines'] = raw['lines'][1:]
        reader = self.reader()
        result = observation(pane, raw, build(pane, raw, 'proof', reader))
        self.assertIsNone(result['offered_card_slots'][0]['text'])
        self.assertFalse(result['menu_text_complete'])
        reader.engine.assert_not_called()

    def test_cached_pipeline_and_source_verifier_load_card_refinement(self):
        with workspace_temp() as root:
            pane, raw = self.scene()
            source = root/'source.mp4'
            source.write_bytes(b'source')
            pane.save(root/'proof.png')
            full = Image.new('RGB',(1920,1080),'red')
            full.paste(pane,(148,0))
            full.save(root/'frame.png')
            raw.update(evidence='proof.png', source_timestamp_ms=0,
                       model_sha256={'test':'digest'},engine_fingerprint='test',
                       source_frame_sha256=hashlib.sha256((root/'frame.png').read_bytes()).hexdigest(),
                       regions={},header='',current_grid=False,result_grid=False)
            capture=dict(source=dict(sha256=hashlib.sha256(source.read_bytes()).hexdigest(),duration_ms=250),
                         frames=[dict(id='one',evidence='frame.png',source_timestamp_ms=0,source_pts=0,time_base='1/60')])
            (root/'capture.json').write_text(json.dumps(capture),encoding='utf-8')
            (root/'neural').mkdir()
            (root/'neural/one.json').write_text(json.dumps(raw),encoding='utf-8')
            extra = build(pane,raw,hashlib.sha256((root/'proof.png').read_bytes()).hexdigest(),self.reader())
            (root/'choice-card-refinement').mkdir()
            path = root/'choice-card-refinement/one.json'
            path.write_text(json.dumps(extra),encoding='utf-8')
            observed=cached_readings(capture,root)[0]['facts']['choice_observation']
            self.assertTrue(observed['menu_text_complete'])
            provenance=observed['refinement_provenance']
            self.assertFalse(provenance['independent_observations'])
            self.assertEqual(provenance['refinement_content_sha256'],fingerprint(extra))
            self.assertEqual(provenance['crops'][0]['crop_rgb_sha256'],extra['views'][0]['crop_rgb_sha256'])
            audit=verify(root,source)
            self.assertTrue(audit['evidence_integrity_verified'])
            self.assertEqual(audit['verified_refinements'],1)
            extra['views'][0]['crop_rgb_sha256']='tampered'
            path.write_text(json.dumps(extra),encoding='utf-8')
            with self.assertRaisesRegex(ValueError,'changed'):
                cached_readings(capture,root)
            self.assertFalse(verify(root,source)['evidence_integrity_verified'])
            original_view=copy.deepcopy(extra['views'][0])
            huge_line=copy.deepcopy(original_view['lines'][0])
            huge_line['confidence']=10**400
            for malformed in ([None], [dict(original_view,lines=[None])],
                              [dict(original_view,crop_box=[10**400,0,1,1])],
                              [dict(original_view,lines=[huge_line])]):
                extra['views']=malformed
                extra['views_sha256']=fingerprint(malformed)
                path.write_text(json.dumps(extra),encoding='utf-8')
                self.assertFalse(verify(root,source)['evidence_integrity_verified'])

    def test_changed_native_text_is_detected_before_confidence_promotion(self):
        pane,raw=self.scene()
        extra=build(pane,raw,'proof',self.reader('First..'))
        extra['views'][0]['lines'][0]['text']='First...'
        with self.assertRaisesRegex(ValueError,'OCR contents changed'):
            observation(pane,raw,extra)

    def test_provenance_and_geometry_tampering_rejected(self):
        pane, raw = self.scene()
        extra = build(pane, raw, 'proof', self.reader())
        mutations = [lambda e: e.update(raw_sha256='changed'),
                     lambda e: e.update(policy='unknown'),
                     lambda e: e['views'][0].update(crop_box=[167,603,684,684]),
                     lambda e: e['views'][0].update(crop_rgb_sha256='changed'),
                     lambda e: e['views'].clear()]
        for mutate in mutations:
            broken = copy.deepcopy(extra)
            mutate(broken)
            with self.assertRaises(ValueError):
                observation(pane, raw, broken)
        pane.putpixel((0,0), (1,2,3))
        with self.assertRaisesRegex(ValueError, 'pixels'):
            observation(pane, raw, extra)
        proof = Mock()
        proof.read_bytes.return_value = b'changed'
        with self.assertRaisesRegex(ValueError, 'provenance'):
            apply({'screen': 'unknown'}, raw, extra, proof)

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
