"""A receipt spelling variant must not count as a second hint award."""
import unittest
import json
from pathlib import Path
from tracen_replay.receipt_names import (collapse_punctuated_hint_variants,
    collapse_separator_hint_variants, collapse_visual_hint_variants)


def sample():
    weak=dict(kind='skill_hint_change',name='An Invented Skill',amount=2,
              raw_text='Gained 2 hint level(s) for An Invented Skill!')
    strong=dict(weak,name=weak['name']+'!',raw_text=weak['raw_text']+'.')
    rows={}
    evidence={}
    for i,effect in enumerate([weak,weak,strong,strong]):
        proof=f'frame-{i}'
        lines=[dict(text=effect['raw_text'],confidence=99,box=[100,890,700,920])]
        if i<2:lines.append(dict(text=strong['name'],confidence=99,box=[200,670,600,700]))
        rows[proof]=dict(source_timestamp_ms=i*250,ocr=dict(neural=lines))
        evidence.setdefault('skill_hint_change||'+effect['name'],[]).append(proof)
    return dict(effects=[weak,strong],field_evidence=evidence,conflicting_readings=[]),rows


class HintReceiptIdentityTests(unittest.TestCase):
    def bridge_sample(self):
        weak=dict(kind='skill_hint_change',name='Example Skill O',amount=3,
                  raw_text='Gained 3 hint level(s) for Example Skill O.')
        strong=dict(weak,name='Example Skill ○',original_text=weak['raw_text'],
                    visual_symbol_observation=dict(method='strict_terminal_ring_geometry'))
        event=dict(effects=[strong,weak],field_evidence={
            'skill_hint_change||Example Skill ○':['s0','s1'],
            'skill_hint_change||Example Skill O':['before','after']})
        timestamps=dict(s0=0,s1=250,before=500,middle=750,after=1000)
        rows={}
        for proof,t in timestamps.items():
            lines=[dict(text='Gained 3 hint level(s) for Example',confidence=99,box=[317,804,640,835]),
                   dict(text='Skill O.',confidence=99,box=[318,832,484,859])]
            rows[proof]=dict(evidence=proof,source_timestamp_ms=t,ocr=dict(neural=lines))
        middle=rows['middle']
        middle['facts']=dict(occluded_receipt_lines=[dict(text='Gained 3 hint level(s) for Exa',
            box=[317,804,640,835],overlay_boxes=[[600,817,611,833]])])
        middle['ocr']['neural'][0].update(text='Gained 3 hint level(s) for Exa',confidence=0)
        return event,timestamps,rows

    def test_explicit_occluded_wrapped_line_bridges_one_missing_observation(self):
        event,times,rows=self.bridge_sample()
        collapse_visual_hint_variants(event,times,rows)
        self.assertEqual(len(event['effects']),1)
        self.assertEqual(event['effects'][0]['occluded_continuity_evidence'],['middle'])
        self.assertEqual(event['field_evidence']['skill_hint_change||Example Skill ○'],['s0','s1'])

    def test_missing_frames_changed_receipts_and_scrolling_do_not_bridge(self):
        for case in ('no_middle','no_occlusion','wrong_amount','wrong_tail','shift','long_gap','wrong_neighbor','duplicate_middle'):
            with self.subTest(case=case):
                event,times,rows=self.bridge_sample()
                if case=='no_middle':del rows['middle']
                if case=='no_occlusion':rows['middle']['facts']={}
                if case=='wrong_amount':rows['middle']['facts']['occluded_receipt_lines'][0]['text']='Gained 4 hint level(s) for Exa'
                if case=='wrong_tail':rows['middle']['ocr']['neural'][1]['text']='Another O.'
                if case=='shift':rows['after']['ocr']['neural'][1]['box']=[318,850,484,877]
                if case=='long_gap':times['after']=1250;rows['after']['source_timestamp_ms']=1250
                if case=='wrong_neighbor':rows['after']['ocr']['neural'][0]['text']='Gained 3 hint level(s) for Different'
                if case=='duplicate_middle':rows['duplicate']=dict(rows['middle'],evidence='duplicate')
                collapse_visual_hint_variants(event,times,rows)
                self.assertEqual(len(event['effects']),2)

    def test_real_wrapped_receipt_survives_cursor_without_double_counting(self):
        fixture=json.loads((Path(__file__).parent/'fixtures/wrapped-hint-occlusion.json').read_text(encoding='utf-8'))
        event=fixture['event'];rows={r['evidence']:r for r in fixture['rows']}
        collapse_visual_hint_variants(event,{p:r['source_timestamp_ms'] for p,r in rows.items()},rows)
        hints=[e for e in event['effects'] if e['kind']=='skill_hint_change']
        self.assertEqual(len(hints),1)
        self.assertEqual((hints[0]['name'],hints[0]['amount']),('Medium Straightaways ○',3))
        self.assertEqual(hints[0]['occluded_continuity_evidence'],['gameplay/part-004-frame-000088.png'])
        self.assertEqual(len(event['effects']),2)

    def test_repeated_label_resolves_one_award_and_retains_alternates(self):
        event,rows=sample()
        collapse_punctuated_hint_variants(event,rows)
        self.assertEqual(len(event['effects']),1)
        effect=event['effects'][0]
        self.assertEqual((effect['name'],effect['amount']),('An Invented Skill!',2))
        self.assertEqual(effect['name_label_evidence'],['frame-0','frame-1'])
        self.assertEqual(effect['alternate_name_evidence'][0]['evidence'],['frame-0','frame-1'])
        self.assertEqual(event['field_evidence']['skill_hint_change||An Invented Skill!'],['frame-2','frame-3'])

    def test_insufficient_or_conflicting_evidence_does_not_merge(self):
        for case in ('one_label','low_confidence','wrong_label','wrong_location',
                     'gap','one_complete','different_amount','conflict','simultaneous'):
            with self.subTest(case=case):
                event,rows=sample()
                if case=='one_label':rows['frame-0']['ocr']['neural'].pop()
                if case=='low_confidence':rows['frame-0']['ocr']['neural'][1]['confidence']=94
                if case=='wrong_label':rows['frame-0']['ocr']['neural'][1]['text']='Other Skill!'
                if case=='wrong_location':rows['frame-0']['ocr']['neural'][1]['box']=[200,800,600,830]
                if case=='gap':rows['frame-3']['source_timestamp_ms']=1250
                if case=='one_complete':del rows['frame-3']
                if case=='different_amount':event['effects'][1]['amount']=3
                if case=='conflict':event['conflicting_readings']=[dict(field='skill_hint_change||An Invented Skill!')]
                if case=='simultaneous':rows['frame-2']['source_timestamp_ms']=250
                collapse_punctuated_hint_variants(event,rows)
                self.assertEqual(len(event['effects']),2)

    def test_separator_variant_merges_one_receipt_and_preserves_rank(self):
        base=dict(kind='skill_hint_change',name='Right Handed ○',amount=1,
                  raw_text='Gained 1 hint level(s) for Right Handed ○.')
        strong=dict(kind='skill_hint_change',name='Right-Handed ○',amount=1,
                    raw_text='Gained 1 hint level(s) for Right-Handed ○.')
        other_rank=dict(kind='skill_hint_change',name='Right-Handed O',amount=1,
                        raw_text='Gained 1 hint level(s) for Right-Handed O.')
        event=dict(effects=[base,strong,other_rank],field_evidence={
            'skill_hint_change||Right Handed ○':['weak'],
            'skill_hint_change||Right-Handed ○':['strong-a','strong-b'],
            'skill_hint_change||Right-Handed O':['other-a','other-b']},
            conflicting_readings=[])
        def row(time,text,box=[316,901,727,930]):
            return dict(source_timestamp_ms=time,screen='event_outcome',
                        context_title='Receipt',ocr={'neural':[
                            dict(text=text,confidence=99,box=list(box))]})
        rows={
            'weak':row(1000,base['raw_text']),
            'strong-a':row(1250,strong['raw_text']),
            'strong-b':row(1500,strong['raw_text']),
            'other-a':row(1000,other_rank['raw_text'],[500,901,800,930]),
            'other-b':row(1250,other_rank['raw_text'],[500,901,800,930]),
        }
        collapse_separator_hint_variants(event,rows)
        self.assertEqual(len(event['effects']),2)
        merged=next(e for e in event['effects'] if e['name']=='Right-Handed ○')
        self.assertEqual(merged['observed_name_candidates'],['Right-Handed ○','Right Handed ○'])
        self.assertEqual(merged['name_resolution'],'same_receipt_separator_variant')
        self.assertEqual(event['field_evidence']['skill_hint_change||Right-Handed ○'],
                         ['strong-a','strong-b','weak'])
        self.assertIn('Right-Handed O',[e['name'] for e in event['effects']])

    def test_separator_variant_requires_distinct_adjacent_source_timestamps(self):
        first=dict(kind='skill_hint_change',name='Example Name',amount=2,
                   raw_text='Gained 2 hint level(s) for Example Name.')
        second=dict(kind='skill_hint_change',name='Example-Name',amount=2,
                    raw_text='Gained 2 hint level(s) for Example-Name.')
        event=dict(effects=[first,second],field_evidence={
            'skill_hint_change||Example Name':['a'],
            'skill_hint_change||Example-Name':['b']},conflicting_readings=[])
        rows={
            'a':dict(source_timestamp_ms=100,screen='event_outcome',context_title='Receipt',
                     ocr={'neural':[dict(text=first['raw_text'],confidence=99,box=[316,901,727,930])]}),
            'b':dict(source_timestamp_ms=100,screen='event_outcome',context_title='Receipt',
                     ocr={'neural':[dict(text=second['raw_text'],confidence=99,box=[316,901,727,930])]}),
        }
        collapse_separator_hint_variants(event,rows)
        self.assertEqual(len(event['effects']),2)

    def test_separator_variant_merges_all_compatible_spellings_transitively(self):
        effects=[
            dict(kind='skill_hint_change',name='Example Name',amount=2,
                 raw_text='Gained 2 hint level(s) for Example Name.'),
            dict(kind='skill_hint_change',name='Example-Name',amount=2,
                 raw_text='Gained 2 hint level(s) for Example-Name.'),
            dict(kind='skill_hint_change',name='Example\u2011Name',amount=2,
                 raw_text='Gained 2 hint level(s) for Example\u2011Name.'),
        ]
        event=dict(effects=effects,field_evidence={
            'skill_hint_change||Example Name':['a1','a2'],
            'skill_hint_change||Example-Name':['b'],
            'skill_hint_change||Example\u2011Name':['c1','c2','c3','c4']},conflicting_readings=[])
        rows={}
        for proof,time,effect in (
            ('a1',100,effects[0]),('a2',350,effects[0]),
            ('b',600,effects[1]),('c1',850,effects[2]),('c2',1100,effects[2]),
            ('c3',1350,effects[2]),('c4',1600,effects[2])):
            rows[proof]=dict(source_timestamp_ms=time,screen='event_outcome',context_title='Receipt',
                             ocr={'neural':[dict(text=effect['raw_text'],confidence=99,
                                                  box=[316,901,727,930])]})
        collapse_separator_hint_variants(event,rows)
        self.assertEqual(len(event['effects']),1)
        winner=event['effects'][0]
        self.assertEqual(winner['name'],'Example\u2011Name')
        self.assertEqual(set(winner['observed_name_candidates']),
                         {'Example Name','Example-Name','Example\u2011Name'})
        self.assertEqual([item['name'] for item in winner['alternate_name_evidence']],
                         ['Example-Name','Example Name'])

    def test_separator_variant_rejects_different_ranks_and_separate_receipts(self):
        first=dict(kind='skill_hint_change',name='Example Name',amount=2,
                   raw_text='Gained 2 hint level(s) for Example Name.')
        rank=dict(kind='skill_hint_change',name='Example Name O',amount=2,
                  raw_text='Gained 2 hint level(s) for Example Name O.')
        separate=dict(kind='skill_hint_change',name='Example-Name',amount=2,
                      raw_text='Gained 2 hint level(s) for Example-Name.')
        event=dict(effects=[first,rank,separate],field_evidence={
            'skill_hint_change||Example Name':['first'],
            'skill_hint_change||Example Name O':['rank'],
            'skill_hint_change||Example-Name':['separate-a','separate-b']},conflicting_readings=[])
        rows={
            'first':dict(source_timestamp_ms=100,screen='event_outcome',context_title='Receipt',
                         ocr={'neural':[dict(text=first['raw_text'],confidence=99,box=[316,901,727,930])]}),
            'rank':dict(source_timestamp_ms=200,screen='event_outcome',context_title='Receipt',
                        ocr={'neural':[dict(text=rank['raw_text'],confidence=99,box=[316,901,727,930])]}),
            'separate-a':dict(source_timestamp_ms=1000,screen='event_outcome',context_title='Receipt',
                            ocr={'neural':[dict(text=separate['raw_text'],confidence=99,box=[316,901,727,930])]}),
            'separate-b':dict(source_timestamp_ms=1500,screen='event_outcome',context_title='Receipt',
                              ocr={'neural':[dict(text=separate['raw_text'],confidence=99,box=[316,901,727,930])]}),
        }
        collapse_separator_hint_variants(event,rows)
        self.assertEqual(len(event['effects']),3)

    def test_separator_variant_rejects_untrusted_source_rows(self):
        first=dict(kind='skill_hint_change',name='Example Name',amount=2,
                   raw_text='Gained 2 hint level(s) for Example Name.')
        second=dict(kind='skill_hint_change',name='Example-Name',amount=2,
                    raw_text='Gained 2 hint level(s) for Example-Name.')
        event=dict(effects=[first,second],field_evidence={
            'skill_hint_change||Example Name':['a'],
            'skill_hint_change||Example-Name':['b']},conflicting_readings=[])
        rows={
            'a':dict(source_timestamp_ms=100,screen='event_outcome',context_title='Receipt',
                     ocr={'neural':[dict(text=first['raw_text'],confidence=94,box=[316,901,727,930])]}),
            'b':dict(source_timestamp_ms=200,screen='event_outcome',context_title='Receipt',
                     ocr={'neural':[dict(text=second['raw_text'],confidence=99,box=[316,901,727,930])]}),
        }
        collapse_separator_hint_variants(event,rows)
        self.assertEqual(len(event['effects']),2)

    def test_separator_variant_rejects_huge_numeric_fields_without_overflow(self):
        first=dict(kind='skill_hint_change',name='Example Name',amount=2,
                   raw_text='Gained 2 hint level(s) for Example Name.')
        second=dict(kind='skill_hint_change',name='Example-Name',amount=2,
                    raw_text='Gained 2 hint level(s) for Example-Name.')
        for field in ('confidence','box'):
            with self.subTest(field=field):
                event=dict(effects=[first.copy(),second.copy()],field_evidence={
                    'skill_hint_change||Example Name':['a'],
                    'skill_hint_change||Example-Name':['b']},conflicting_readings=[])
                first_line=dict(text=first['raw_text'],confidence=99,box=[316,901,727,930])
                second_line=dict(text=second['raw_text'],confidence=99,box=[316,901,727,930])
                if field=='confidence':first_line[field]=10**1000
                else:first_line[field]=[10**1000,901,727,930]
                rows={
                    'a':dict(source_timestamp_ms=100,screen='event_outcome',context_title='Receipt',
                             ocr={'neural':[first_line]}),
                    'b':dict(source_timestamp_ms=200,screen='event_outcome',context_title='Receipt',
                             ocr={'neural':[second_line]}),
                }
                collapse_separator_hint_variants(event,rows)
                self.assertEqual(len(event['effects']),2)


if __name__=='__main__':unittest.main()
