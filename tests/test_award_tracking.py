import copy
import json
import unittest
from pathlib import Path
from tracen_replay.award_tracking import tracked_stats


class AwardTrackingTests(unittest.TestCase):
    def rows(self):
        return [dict(source_timestamp_ms=t,evidence=f'{t}.png',screen='event_outcome',ocr=dict(neural=[
            dict(text='+57',confidence=99 if t<75 else 90,box=[468,562,618,643]),
            dict(text='SkillPts' if t in (0,75) else 'Skill-Pts',confidence=99 if t in (0,75) else 91,box=[495,633,650,689]),
            dict(text='Skill Pts went up by 57.',confidence=99,box=[315,900,620,930])])) for t in (0,25,50,75)]

    def test_two_label_anchors_support_repeated_visible_values(self):
        result=tracked_stats(self.rows(),0,75)
        self.assertEqual([(r['source_timestamp_ms'],c['field'],c['amount']) for r,c in result],[(0,'skill_points',57),(25,'skill_points',57),(50,'skill_points',57)])
        self.assertEqual(len(result[1][1]['label_anchors']),2)
        self.assertEqual(result[1][1]['confidence'],99)

    def test_no_single_anchor_or_unbracketed_values(self):
        rows=self.rows();rows[-1]['ocr']['neural'][1]['confidence']=90
        self.assertEqual(tracked_stats(rows,0,75),[])
        rows=self.rows();rows[0]['ocr']['neural'][0]['confidence']=90
        self.assertEqual(tracked_stats(rows,0,75),[])

    def test_different_amount_or_label_interrupts_tracking(self):
        for text,index in (('+37',0),('Stamina',1)):
            rows=self.rows();rows[1]['ocr']['neural'][index]['text']=text
            self.assertEqual(tracked_stats(rows,0,75),[])

    def test_ambiguous_gain_cannot_be_hidden_by_other_agreeing_frames(self):
        rows=self.rows();extra=copy.deepcopy(rows[2]);extra['source_timestamp_ms']=62;rows.insert(3,extra)
        conflict=copy.deepcopy(rows[1]['ocr']['neural'][0]);conflict['text']='+37'
        rows[1]['ocr']['neural'].append(conflict)
        self.assertEqual(tracked_stats(rows,0,75),[])

    def test_moving_label_or_screen_boundary_rejects_track(self):
        rows=self.rows();rows[1]['ocr']['neural'][1]['box'][1]+=12
        self.assertEqual(tracked_stats(rows,0,75),[])
        rows=self.rows();rows[1]['screen']='training_preview'
        self.assertEqual(tracked_stats(rows,0,75),[])

    def test_duplicate_timestamps_and_long_gaps_do_not_support_tracking(self):
        rows=self.rows();rows[1]['source_timestamp_ms']=0;rows[3]['source_timestamp_ms']=50
        self.assertEqual(tracked_stats(rows,0,75),[])
        rows=self.rows();rows[-1]['source_timestamp_ms']=400
        self.assertEqual(tracked_stats(rows,0,400),[])

    def test_far_apart_anchors_do_not_license_continuous_weak_labels(self):
        template=self.rows()[1];rows=[]
        for t in range(0,301,25):
            r=copy.deepcopy(template);r['source_timestamp_ms']=t
            if t in (0,300):r['ocr']['neural'][1].update(text='SkillPts',confidence=99)
            rows.append(r)
        self.assertEqual(tracked_stats(rows,0,300),[])

    def test_award_caption_required_and_caps_excluded(self):
        for text in ('Skill Pts cap went up by 57.','Skill Pts Bonus went up by 57.','Unrelated dialogue'):
            rows=self.rows()
            for r in rows:r['ocr']['neural'][-1]['text']=text
            self.assertEqual(tracked_stats(rows,0,75),[])

    def test_source_bracketed_animation(self):
        fixture=json.loads((Path(__file__).parent/'fixtures/award-label-tracking-v1.json').read_text(encoding='utf-8'))
        result=tracked_stats(fixture['readings'],1480750,1481500)
        self.assertEqual([(r['source_timestamp_ms'],c['field'],c['amount']) for r,c in result],
                         [tuple(v) for v in fixture['expected']])
