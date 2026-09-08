import unittest
import json
from pathlib import Path
from tracen_replay.animated_performance import candidates,reconcile
from tests.test_neural_transactions import line,row


class AnimatedPerformanceTests(unittest.TestCase):
    def test_source_regression_observations(self):
        ref=json.loads((Path(__file__).parent/'fixtures/animated-performance-regression-v1.json').read_text(encoding='utf-8'))
        for frame in ref['frames']:
            got=candidates(frame['lines'],'event_outcome')
            self.assertEqual([(c['field'],c['amount']) for c in got],[(ref['expected_field'],ref['expected_amount'])])

    def lines(self):
        return [line('+10',(646,277,796,360)),line('Composure',(648,361,832,408)),
                line('Composure went up by',(317,919,551,949))]

    def event(self):
        return dict(first_seen_ms=0,last_seen_ms=1000,effects={},field_evidence={},conflicting_readings=[])

    def rows(self):
        c=candidates(self.lines(),'event_outcome')
        return [row(t,'event_outcome',{'animated_performance_candidates':c}) for t in (100,133,167)]

    def test_repeated_labeled_award_with_receipt_caption(self):
        event=self.event();reconcile(event,self.rows())
        self.assertEqual(event['effects']['performance_change|composure|']['amount'],10)
        self.assertEqual(len(event['animated_performance_evidence']['composure']),3)

    def test_rejects_previews_caps_missing_labels_and_geometry(self):
        for screen in ('unknown','training_preview','lesson_confirmation','training_result'):
            self.assertEqual(candidates(self.lines(),screen),[])
        self.assertEqual(candidates(self.lines()[:2],'event_outcome'),[])
        lines=self.lines();lines[2]['text']='Composure cap went up by 50.'
        self.assertEqual(candidates(lines,'event_outcome'),[])
        lines=self.lines();lines[0]['box']=[250,277,400,360]
        self.assertEqual(candidates(lines,'event_outcome'),[])
        lines=self.lines();lines[0]['confidence']=96
        self.assertEqual(candidates(lines,'event_outcome'),[])

    def test_requires_distinct_frames_and_time_span(self):
        for rows in (self.rows()[:2],[self.rows()[0]]*3):
            event=self.event();reconcile(event,rows);self.assertEqual(event['effects'],{})
        rows=self.rows()
        for i,r in enumerate(rows):r['source_timestamp_ms']=i*16
        event=self.event();reconcile(event,rows);self.assertEqual(event['effects'],{})

    def test_conflicting_amounts_abstain(self):
        rows=self.rows();rows[-1]['facts']={'animated_performance_candidates':[dict(rows[0]['facts']['animated_performance_candidates'][0],amount=1)]}
        event=self.event();reconcile(event,rows);self.assertEqual(event['effects'],{})
        event=self.event();event['effects']['performance_change|composure|']={'amount':1}
        reconcile(event,self.rows())
        self.assertEqual(event['effects'],{})
        self.assertEqual(event['conflicting_readings'][0]['reason'],'receipt_animation_disagreement')
