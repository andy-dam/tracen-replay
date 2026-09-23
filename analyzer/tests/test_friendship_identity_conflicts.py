import copy
import json
from pathlib import Path
import unittest

from tracen_replay.transactions import outcome_events


class FriendshipIdentityConflictsTests(unittest.TestCase):
    def bridge_fixture(self):
        return json.loads(Path('analyzer/tests/fixtures/friendship-occluded-bridge-1181000.json').read_text(encoding='utf-8'))

    def test_explicit_occluded_middle_frame_connects_conflicting_source_names(self):
        from tracen_replay.receipt_names import flag_friendship_identity_conflicts
        fixture=self.bridge_fixture();event=fixture['event']
        flag_friendship_identity_conflicts(event,fixture['rows_by_evidence'])
        self.assertEqual([e['kind'] for e in event['effects']],['stat_change'])
        self.assertEqual({c['effect']['name'] for c in event['ambiguous_effect_candidates']},
                         {'Director Akikawa','Directo Akikawa'})
        self.assertTrue(all(c['occluded_bridge_evidence']==['gameplay/part-009-frame-000407.png'] for c in event['conflicting_readings']))

    def test_unobserved_unrelated_or_moved_middle_frame_cannot_bridge(self):
        from tracen_replay.receipt_names import flag_friendship_identity_conflicts
        for mutation in ('missing','not_name_occlusion','different_award','different_slot','scroll'):
            fixture=self.bridge_fixture();event=fixture['event'];rows=fixture['rows_by_evidence']
            key='gameplay/part-009-frame-000407.png';row=rows[key]
            candidate=row['facts']['occluded_receipt_lines'][0]
            if mutation=='missing':del rows[key]
            elif mutation=='not_name_occlusion':candidate['recipient_name_occluded']=False
            elif mutation=='different_award':candidate['text']=candidate['text'].replace('by 5','by 6')
            elif mutation=='different_slot':candidate['box'][1]+=40;candidate['box'][3]+=40
            else:
                line=next(l for l in row['ocr']['neural'] if l['text'].startswith('Skill Pts'))
                line['box'][1]+=24;line['box'][3]+=24
            flag_friendship_identity_conflicts(event,rows)
            with self.subTest(mutation=mutation):
                self.assertEqual(len(event['effects']),3)
                self.assertFalse(event['conflicting_readings'])

    def test_scrolling_source_dialogue_does_not_make_distinct_people_ambiguous(self):
        from tracen_replay.receipt_names import flag_friendship_identity_conflicts
        fixture=json.loads(Path('analyzer/tests/fixtures/friendship-status-scroll-1447750.json').read_text(encoding='utf-8'))
        before=copy.deepcopy(fixture['event'])
        flag_friendship_identity_conflicts(fixture['event'],fixture['rows_by_evidence'])
        self.assertEqual(fixture['event'],before)

    def status_fixture(self):
        return json.loads(Path('analyzer/tests/fixtures/friendship-status-slot-796000.json').read_text(encoding='utf-8'))

    def test_source_status_spellings_do_not_become_two_people(self):
        from tracen_replay.receipt_names import flag_friendship_identity_conflicts
        fixture=self.status_fixture();event=fixture['event']
        flag_friendship_identity_conflicts(event,fixture['rows_by_evidence'])
        self.assertEqual([e['kind'] for e in event['effects']],['skill_hint_change'])
        self.assertEqual({c['effect']['name'] for c in event['ambiguous_effect_candidates']},
                         {'Nishino Flower','Nishino Fl5wer'})
        self.assertTrue(all(c['evidence'] for c in event['ambiguous_effect_candidates']))
        self.assertTrue(all(c['field'].startswith('friendship_status||') for c in event['conflicting_readings']))

    def test_different_statuses_and_separate_receipt_kinds_are_not_identity_conflicts(self):
        from tracen_replay.receipt_names import flag_friendship_identity_conflicts
        for change in ('value','kind'):
            fixture=self.status_fixture();event=fixture['event']
            last=event['effects'][-1]
            if change=='value':last['value']='unchanged'
            else:
                old_key=last['kind']+'||'+last['name'];last['kind']='friendship_change'
                event['field_evidence'][last['kind']+'||'+last['name']]=event['field_evidence'].pop(old_key)
            flag_friendship_identity_conflicts(event,fixture['rows_by_evidence'])
            with self.subTest(change=change):
                self.assertEqual(len(event['effects']),3)
                self.assertFalse(event['conflicting_readings'])

    def test_severely_corrupted_source_names_remain_candidates_not_people(self):
        from tracen_replay.receipt_names import flag_friendship_identity_conflicts
        fixture=json.loads(Path('analyzer/tests/fixtures/friendship-slot-894250.json').read_text(encoding='utf-8'))
        event=fixture['event']
        names={e['name'] for e in event['effects']}
        self.assertEqual(len(names),15)
        flag_friendship_identity_conflicts(event,fixture['rows_by_evidence'])
        self.assertEqual(event['effects'],[])
        self.assertEqual({c['effect']['name'] for c in event['ambiguous_effect_candidates']},names)
        self.assertTrue(all(c['evidence'] for c in event['ambiguous_effect_candidates']))
        self.assertTrue(all(c['evidence_pairs'] for c in event['conflicting_readings']))

    def slot_views(self,names,times):
        """One receipt slot read with ``names`` at ``times``, as an event and its rows."""
        rows={};effects={};evidence={}
        for time,name in zip(times,names):
            text=f'Friendship with {name} is maxed out.'
            rows[f'{time}.png']=dict(source_timestamp_ms=time,screen='event_outcome',evidence=f'{time}.png',
                                     ocr=dict(neural=[dict(text=text,confidence=99,box=[300,800,900,830])]))
            effects.setdefault(name,dict(kind='friendship_status',name=name,value='maximum',amount=None,raw_text=text,confidence=99))
            evidence.setdefault(f'friendship_status||{name}',[]).append(f'{time}.png')
        return dict(effects=list(effects.values()),field_evidence=evidence,conflicting_readings=[]),rows

    def test_a_misread_between_two_reads_of_one_name_is_that_name(self):
        from tracen_replay import source_clock
        from tracen_replay.receipt_names import collapse_uncorroborated_recipient_variants, flag_friendship_identity_conflicts
        self.addCleanup(source_clock.use)

        def settle(names,times):
            # None repairs to a known name, and the run reads the clean name
            # and one of the misreads elsewhere too, so no spelling alone
            # recurs.
            source_clock.use(times,250)
            event,rows=self.slot_views(names,times)
            flag_friendship_identity_conflicts(event,rows)
            sightings={('friendship_status',n):names.count(n) for n in names}
            sightings[('friendship_status','Light Hello')]+=40;sightings[('friendship_status','Light Hell')]+=1
            collapse_uncorroborated_recipient_variants(event,sightings,None,rows)
            return event
        # Four 30 fps samples of one slot: the name read clean on the first
        # and last, misread on the two between.
        names,times=['Light Hello','Light He!','Light Hell','Light Hello'],[0,267,533,800]
        event=settle(names,times)
        self.assertEqual([(e['name'],e['name_resolution']) for e in event['effects']],
                         [('Light Hello','misread_between_reads_of_one_name_in_one_slot')])
        self.assertEqual(sorted(a['name'] for a in event['effects'][0]['alternate_name_evidence']),['Light He!','Light Hell'])
        self.assertEqual((event['conflicting_readings'],event['ambiguous_effect_candidates']),([],[]))
        # A spelling also read after the last clean read settles nothing.
        event=settle(names+['Light He!'],times+[1067])
        self.assertEqual(event['effects'],[])
        self.assertEqual({c['effect']['name'] for c in event['ambiguous_effect_candidates']},
                         {'Light Hello','Light He!','Light Hell'})

    def setUp(self):
        self.rows=json.loads(Path('analyzer/tests/fixtures/friendship-name-conflict-293250.json').read_text(encoding='utf-8'))

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
