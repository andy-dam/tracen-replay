import copy
import json
from pathlib import Path
import unittest

from tracen_replay.transactions import outcome_events


class FriendshipIdentityConflictsTests(unittest.TestCase):
    def setUp(self):
        self.rows=json.loads(Path('tests/fixtures/friendship-name-conflict-293250.json').read_text(encoding='utf-8'))

    def friendship(self,event):
        return [e for e in event['effects'] if e['kind']=='friendship_change']

    def test_source_slot_conflict_abstains_without_choosing_spelling(self):
        event=outcome_events(self.rows)[0]
        self.assertEqual(self.friendship(event),[])
        candidates=event['ambiguous_effect_candidates']
        self.assertEqual({c['effect']['name'] for c in candidates},{'Etsuko Otonashi','Etsuko Stonashi'})
        self.assertTrue(all(c['evidence'] for c in candidates))
        self.assertEqual(event['deltas'],{'wit':2})

    def test_separate_receipt_rows_remain_distinct(self):
        rows=copy.deepcopy(self.rows)
        for line in rows[-1]['ocr']['neural']:
            if 'Stonashi' in line['text']:
                line['box'][1]+=40;line['box'][3]+=40
        self.assertEqual(len(self.friendship(outcome_events(rows)[0])),2)

    def test_different_amounts_are_not_identity_equivalence(self):
        rows=copy.deepcopy(self.rows)
        for effect in rows[-1]['effects']:
            if effect['kind']=='friendship_change':effect['amount']=5
        self.assertEqual(len(self.friendship(outcome_events(rows)[0])),2)

    def test_no_geometry_or_no_adjacent_frame_cannot_establish_conflict(self):
        rows=copy.deepcopy(self.rows)
        rows[-1]['ocr']['neural']=[]
        self.assertEqual(len(self.friendship(outcome_events(rows)[0])),2)
        rows=copy.deepcopy(self.rows)
        rows[-1]['source_timestamp_ms']+=250
        self.assertEqual(len(self.friendship(outcome_events(rows)[0])),2)

    def test_unchanged_name_remains_one_supported_receipt(self):
        event=outcome_events(self.rows[:-1])[0]
        self.assertEqual(len(self.friendship(event)),1)
        self.assertNotIn('ambiguous_effect_candidates',event)

    def test_names_visible_together_remain_distinct(self):
        rows=copy.deepcopy(self.rows)
        rows[-1]['effects'].append(copy.deepcopy(rows[0]['effects'][-1]))
        rows[-1]['ocr']['neural'].extend(copy.deepcopy([
            l for l in rows[0]['ocr']['neural'] if 'Friendship with' in l['text']]))
        self.assertEqual(len(self.friendship(outcome_events(rows)[0])),2)
