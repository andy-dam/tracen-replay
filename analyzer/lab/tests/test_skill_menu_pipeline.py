"""Tests of ``tests.test_skill_menu_pipeline`` that need locally preserved evidence; they run only where it is."""
import copy
import json
import unittest
from unittest.mock import patch
from tests.test_report_contract import valid_report
from tracen_replay.full_recording import assemble, parse_receipt_pixels, _rebase_paths
from tracen_replay.report_contract import validate
from tests import localdata


class SkillMenuPipelineTests(unittest.TestCase):
    def test_full_source_menu_keeps_draft_separate_from_unrelated_price_conflict(self):
        from tracen_replay.skill_menu_observations import build_observations
        root=localdata.root("development_third_recording_baseline")
        if not (root/'neural/part-010-frame-000329.json').is_file():
            self.skipTest('Recorded menu sequence is unavailable')
        rows=[]
        for number in range(329,381):
            identity=f'part-010-frame-{number:06d}'
            raw=json.loads((root/'neural'/f'{identity}.json').read_text(encoding='utf-8'))
            frame=dict(id=identity,source_timestamp_ms=raw['source_timestamp_ms'],
                       evidence=f'part-010/frames/{number:06d}.jpg')
            row=parse_receipt_pixels(raw,root,frame)
            row.update(source_timestamp_ms=raw['source_timestamp_ms'],evidence=raw['evidence'])
            rows.append(row)
        rows=_rebase_paths(rows,root,root.parent)
        menus=build_observations(rows)
        drafts=[menu for menu in menus if menu['payload'].get('selected_draft_names')]
        self.assertEqual(len(drafts),1)
        draft=drafts[0]
        self.assertEqual(draft['payload']['selected_draft_names'],['Pace Chaser Savvy'])
        self.assertFalse(draft['uncertain'])
        offers=menus[0]
        self.assertTrue(offers['uncertain'])
        self.assertEqual(offers['uncertainty_fields'],['/visible_card_prices/Front Runner Straightaways'])
        self.assertNotIn('Front Runner Straightaways',offers['payload']['visible_card_prices'])
        self.assertLess(offers['end_ms'],draft['start_ms'])
        self.assertFalse(set(offers['evidence']) & set(draft['evidence']))
        self.assertEqual(draft['payload']['skill_points_after'],25)
        self.assertEqual(draft['phase'],'preview')
        self.assertTrue(all(path.startswith('initial-baseline/') for path in draft['evidence']))

    def test_source_pixel_draft_survives_normal_parser_and_contract(self):
        root=localdata.root("development_third_recording_baseline")
        path=root/'neural/part-010-frame-000375.json'
        if not path.is_file():
            self.skipTest('Recorded source fixture is unavailable')
        raw=json.loads(path.read_text(encoding='utf-8'))
        frame=dict(id='part-010-frame-000375',
                   source_timestamp_ms=raw['source_timestamp_ms'],
                   evidence='part-010/frames/000375.jpg')
        source='a10bd8261176e2aa79eb991ab0eb6b4b23dc4c8a6ffe13edcb799d43e8982313'
        row=parse_receipt_pixels(raw,root,frame,source_sha256=source)
        row.update(source_timestamp_ms=raw['source_timestamp_ms'],evidence=raw['evidence'])
        capture=valid_report()
        capture['source']['sha256']=source
        capture['source']['duration_ms']=raw['source_timestamp_ms']+1000
        capture['clip']['duration_ms']=capture['source']['duration_ms']
        with patch('tracen_replay.full_recording.audit',return_value={}):
            report=assemble(capture,[row])
        validate(report,require_gameplay=True)
        self.assertEqual(report['gameplay_tracking']['skill_purchases'],[])
        menus=report['gameplay_tracking']['skill_menu_observations']
        self.assertEqual(len(menus),1)
        payload=menus[0]['payload']
        self.assertEqual(payload['selected_draft_names'],['Pace Chaser Savvy'])
        self.assertEqual(payload['skill_points_after'],25)
        self.assertEqual(payload['selection_status'],'not_yet_confirmed')
        self.assertTrue(payload['selected_draft_card_proof'])
        altered=copy.deepcopy(row)
        altered['source_timestamp_ms']+=250
        with patch('tracen_replay.full_recording.audit',return_value={}):
            stale=assemble(capture,[altered])
        self.assertNotIn('selected_draft_names',stale['gameplay_tracking']['skill_menu_observations'][0]['payload'])
