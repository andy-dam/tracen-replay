from copy import deepcopy
import unittest
from tracen_replay.hint_card_events import apply


SOURCE = 'a'*64


def fixture():
    rows=[]
    observations=[]
    for time in (1000,1250):
        evidence=f'gameplay/{time}.png'
        card=dict(text='Example Skill',box=[400,678,565,703],confidence=99,suffix='single_circle')
        receipt=dict(text='Gained 2 hint level(s) for Exmple Skill.',box=[316,853,731,881],amount=2)
        rows.append(dict(source_timestamp_ms=time,evidence=evidence,screen='event_outcome',
                         context_title='A Present',effects=[],
                         ocr={'neural':[deepcopy(card),dict(deepcopy(receipt),confidence=0,overlay_occluded=True)]}))
        observations.append(dict(timestamp_ms=time,evidence=evidence,card=card,receipt=receipt,
                                 prefix_amount_proof={'amount':2}))
    candidate=dict(kind='skill_hint_change',name='Example Skill',amount=2,source_sha256=SOURCE,
                   source_timestamps_ms=[1000,1250],raw_receipt_name_candidates=['Exmple Skill'],
                   identity_proof={'same_context':'A Present','suffix':'single_circle'},observations=observations)
    event=dict(id='outcome-1',kind='outcome',first_seen_ms=900,last_seen_ms=1500,
               context_title='A Present',effects=[],field_evidence={},conflicting_readings=[],deltas={})
    return [event],rows,[candidate]


class HintCardEventTests(unittest.TestCase):
    def test_report_reconstruction_reuses_observations_without_running_ocr(self):
        from unittest.mock import patch
        from tests.test_report_contract import valid_report
        from tracen_replay.full_recording import assemble
        from tracen_replay.report_contract import validate
        _,rows,candidates=fixture()
        for row in rows:
            row.update(stats={},facts={},training_option=None,context_title_candidate='A Present')
            row['effects']=[dict(kind='energy_change',amount=5,raw_text='Energy went up by 5.',confidence=99)]
        report=valid_report()
        with patch('tracen_replay.hint_card_identity.recover',side_effect=AssertionError('OCR in reconstruction')):
            assemble(report,rows,hint_card_observations=candidates)
            validate(report,require_gameplay=True)
            self.assertEqual(len(report['gameplay_tracking']['hint_card_recovery']['accepted']),1)
            snapshot=deepcopy(report)
            assemble(report,rows)
            self.assertEqual(report,snapshot)
        self.assertFalse(any(e['kind']=='skill_hint_change' for row in rows for e in row['effects']))

    def test_attaches_recovered_identity_and_preserves_raw_inputs(self):
        events,rows,candidates=fixture()
        originals=deepcopy((rows,candidates))
        result=apply(events,rows,candidates,source_sha256=SOURCE)
        self.assertEqual(result['rejected'],[])
        effect=events[0]['effects'][0]
        self.assertEqual((effect['name'],effect['amount']),('Example Skill ○',2))
        self.assertIn('Exmple',effect['raw_text'])
        self.assertTrue(effect['circle_variant_verified'])
        self.assertEqual((rows,candidates),originals)
        before=deepcopy(events)
        apply(events,rows,candidates,source_sha256=SOURCE)
        self.assertEqual(events,before)

    def test_rejects_foreign_source_or_changed_text_and_amount(self):
        for change in ('source','card','receipt','amount','prefix'):
            with self.subTest(change=change):
                events,rows,candidates=fixture()
                if change=='source':candidates[0]['source_sha256']='b'*64
                if change=='card':rows[0]['ocr']['neural'][0]['text']='Different Skill'
                if change=='receipt':rows[0]['ocr']['neural'][1]['box'][1]+=1
                if change=='amount':candidates[0]['amount']=3
                if change=='prefix':candidates[0]['observations'][0]['prefix_amount_proof']['amount']=True
                before=deepcopy(events)
                result=apply(events,rows,candidates,source_sha256=SOURCE)
                self.assertEqual(result['accepted'],[])
                self.assertEqual(events,before)

    def test_duplicate_timestamp_and_intervening_screen_break_support(self):
        for duplicate in (True,False):
            events,rows,candidates=fixture()
            if duplicate:
                candidates[0]['observations'][1]['timestamp_ms']=1000
            else:
                middle=deepcopy(rows[0]);middle.update(source_timestamp_ms=1125,evidence='1125.png',screen='career_hub')
                rows.insert(1,middle)
            self.assertFalse(apply(events,rows,candidates,source_sha256=SOURCE)['accepted'])
            self.assertEqual(events[0]['effects'],[])

    def test_support_cannot_cross_or_create_outcome_boundaries(self):
        events,rows,candidates=fixture()
        second=deepcopy(events[0]);second.update(id='outcome-2',first_seen_ms=1200)
        events[0]['last_seen_ms']=1100;events.append(second)
        self.assertFalse(apply(events,rows,candidates,source_sha256=SOURCE)['accepted'])
        self.assertEqual([e['effects'] for e in events],[[],[]])

    def test_replaces_only_fully_supported_same_receipt_alternative(self):
        for outside in (False,True):
            events,rows,candidates=fixture()
            old=dict(kind='skill_hint_change',name='Exmple Skill',amount=2,
                     raw_text=candidates[0]['observations'][0]['receipt']['text'])
            events[0]['effects']=[old]
            events[0]['field_evidence']['skill_hint_change||Exmple Skill']=[rows[0]['evidence']]
            if outside:events[0]['field_evidence']['skill_hint_change||Exmple Skill'].append('earlier.png')
            apply(events,rows,candidates,source_sha256=SOURCE)
            self.assertEqual(old in events[0]['effects'],outside)
            self.assertEqual(len(events[0]['effects']),2 if outside else 1)
            if not outside:
                self.assertNotIn('skill_hint_change||Exmple Skill',events[0]['field_evidence'])
                archived=events[0]['effects'][0]['replaced_receipt_readings'][0]
                self.assertEqual(archived['field_evidence'],[rows[0]['evidence']])
                self.assertEqual(archived['raw_text'],old['raw_text'])

    def test_existing_amount_conflict_prevents_promotion(self):
        for name in ('Example Skill ○','Example Skill','Example Skill O'):
            with self.subTest(name=name):
                events,rows,candidates=fixture()
                events[0]['effects']=[dict(kind='skill_hint_change',name=name,amount=3)]
                before=deepcopy(events)
                self.assertFalse(apply(events,rows,candidates,source_sha256=SOURCE)['accepted'])
                self.assertEqual(events,before)

    def test_disjoint_conflicting_candidates_abstain_in_either_order(self):
        for conflict in ('amount','rank'):
            for reverse in (False,True):
                with self.subTest(conflict=conflict,reverse=reverse):
                    events,rows,candidates=fixture()
                    _,more_rows,more_candidates=fixture()
                    second=more_candidates[0]
                    for row,observation in zip(more_rows,second['observations']):
                        time=row['source_timestamp_ms']+500
                        row.update(source_timestamp_ms=time,evidence=f'gameplay/{time}.png')
                        observation.update(timestamp_ms=time,evidence=row['evidence'])
                        if conflict=='amount':
                            observation['receipt']['amount']=3
                            observation['receipt']['text']='Gained 3 hint level(s) for Exmple Skill.'
                            row['ocr']['neural'][1]['text']=observation['receipt']['text']
                            observation['prefix_amount_proof']['amount']=3
                        else:
                            observation['card']['suffix']='double_circle'
                    second['source_timestamps_ms']=[1500,1750]
                    if conflict=='amount':second['amount']=3
                    else:second['identity_proof']['suffix']='double_circle'
                    rows.extend(more_rows);candidates.extend(more_candidates)
                    events[0]['last_seen_ms']=1800
                    if reverse:candidates.reverse()
                    before=deepcopy(events)
                    result=apply(events,rows,candidates,source_sha256=SOURCE)
                    self.assertEqual(result['accepted'],[])
                    self.assertEqual([r['reason'] for r in result['rejected']],['conflicting_hint_candidates']*2)
                    self.assertEqual(events,before)

    def test_overlapping_outcome_targets_and_existing_rank_conflict_abstain(self):
        for conflict in ('target','rank'):
            events,rows,candidates=fixture()
            if conflict=='target':
                extra=deepcopy(events[0]);extra['id']='outcome-2';events.append(extra)
            else:
                events[0]['effects']=[dict(kind='skill_hint_change',name='Example Skill ◎',amount=2)]
            before=deepcopy(events)
            self.assertFalse(apply(events,rows,candidates,source_sha256=SOURCE)['accepted'])
            self.assertEqual(events,before)

    def test_explicit_circle_is_not_duplicated_and_conflicting_rank_rejected(self):
        for glyph,accepted in (('○',True),('◎',False)):
            events,rows,candidates=fixture()
            name='Example Skill '+glyph
            candidates[0]['name']=name
            for row,observation in zip(rows,candidates[0]['observations']):
                row['ocr']['neural'][0]['text']=name;observation['card']['text']=name
            result=apply(events,rows,candidates,source_sha256=SOURCE)
            self.assertEqual(bool(result['accepted']),accepted)
            if accepted:self.assertEqual(events[0]['effects'][0]['name'],name)
