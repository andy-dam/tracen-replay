"""A receipt spelling variant must not count as a second hint award."""
import unittest
import json
from pathlib import Path
from tracen_replay.receipt_names import collapse_punctuated_hint_variants, collapse_visual_hint_variants


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


if __name__=='__main__':unittest.main()
