import unittest
import json
from pathlib import Path
from tracen_replay.animated_performance import candidates,reconcile
from tests.test_neural_transactions import line,row


class AnimatedStatTests(unittest.TestCase):
    def test_caption_can_be_visible_in_one_frame_of_repeated_animation(self):
        bare=candidates(self.lines()[:2],'event_outcome',stat=True)
        anchored=candidates(self.lines(),'event_outcome',stat=True)
        rows=[row(t,'event_outcome',{'animated_stat_candidates':bare}) for t in (0,25,50)]
        event=dict(first_seen_ms=0,last_seen_ms=100,effects={},field_evidence={},conflicting_readings=[])
        reconcile(event,rows,stat=True);self.assertEqual(event['effects'],{})
        rows[0]['facts']['animated_stat_candidates']=anchored
        reconcile(event,rows,stat=True)
        self.assertEqual(event['effects']['stat_change|skill_points|']['amount'],57)

    def test_two_repeated_displays_resolve_only_one_receipt_outlier(self):
        got=candidates(self.lines(),'event_outcome',stat=True)
        rows=[row(t,'event_outcome',{'animated_stat_candidates':got}) for t in (0,25,50)]
        key='stat_change|skill_points|'
        for extra,accepted in (([],True),([(75,'e.png',{'amount':51})],False)):
            event=dict(first_seen_ms=0,last_seen_ms=100,effects={key:{'amount':51}},field_evidence={},
                       conflicting_readings=[{'field':key,'reason':'changing_effect_value'}])
            receipts={key:[(t,str(t)+'.png',{'amount':57}) for t in (0,25,50)]+[(10,'outlier.png',{'amount':51})]+extra}
            reconcile(event,rows,stat=True,receipt_observations=receipts)
            self.assertEqual(event['effects'][key]['amount'],57 if accepted else 51)
            if accepted:self.assertEqual(event['resolved_reading_conflicts'][0]['basis'],'repeated_receipt_and_animation_resolve_single_outlier')

    def test_source_animation_observations(self):
        ref=json.loads((Path(__file__).parent/'fixtures/animated-stat-regression-v1.json').read_text(encoding='utf-8'))
        for frame in ref['frames']:
            got=candidates(frame['lines'],'event_outcome',stat=True)
            self.assertEqual([(c['field'],c['amount']) for c in got],[('skill_points',57)])

    def lines(self):
        return [line('+57',(473,560,622,643)),line('Skill Pts',(497,639,647,684)),
                line('Skill Pts went up by',(316,877,501,906))]

    def test_skill_points_animation_keeps_stat_kind(self):
        got=candidates(self.lines(),'event_outcome',stat=True)
        self.assertEqual([(e['kind'],e['field'],e['amount']) for e in got],[('stat_change','skill_points',57)])
        self.assertEqual(candidates(self.lines(),'event_outcome'),[])
        rows=[row(t,'event_outcome',{'animated_stat_candidates':got}) for t in (0,25,50)]
        event=dict(first_seen_ms=0,last_seen_ms=100,effects={},field_evidence={},conflicting_readings=[])
        reconcile(event,rows,stat=True)
        self.assertEqual(event['effects']['stat_change|skill_points|']['amount'],57)
        self.assertEqual(len(event['animated_stat_evidence']['skill_points']),3)

    def test_cap_bonus_and_training_projection_are_not_stat_awards(self):
        for caption in ('Skill Pts Bonus went up by 3.','Skill Pts cap went up by 50.'):
            lines=self.lines();lines[-1]['text']=caption
            self.assertEqual(candidates(lines,'event_outcome',stat=True),[])
        for screen in ('unknown','training_preview','training_result','lesson_confirmation'):
            self.assertEqual(candidates(self.lines(),screen,stat=True),[])

    def test_receipt_disagreement_is_not_resolved_with_arithmetic(self):
        got=candidates(self.lines(),'event_outcome',stat=True)
        rows=[row(t,'event_outcome',{'animated_stat_candidates':got}) for t in (0,25,50)]
        event=dict(first_seen_ms=0,last_seen_ms=100,effects={'stat_change|skill_points|':{'amount':5}},field_evidence={},conflicting_readings=[])
        reconcile(event,rows,stat=True)
        self.assertEqual(event['effects'],{})
        self.assertEqual(event['conflicting_readings'][0]['reason'],'receipt_animation_disagreement')

    def test_repeated_animation_resolves_only_documented_numeric_prefixes(self):
        got=candidates(self.lines(),'event_outcome',stat=True)
        rows=[row(t,'event_outcome',{'animated_stat_candidates':got}) for t in (0,25,50)]
        key='stat_change|skill_points|'
        for amount,accepted in ((5,True),(51,False)):
            event=dict(first_seen_ms=0,last_seen_ms=100,effects={key:{'amount':amount}},field_evidence={},
                       conflicting_readings=[{'field':key,'reason':'changing_effect_value'}])
            receipts={key:[(0,'a.png',{'amount':amount}),(25,'b.png',{'amount':57})]}
            reconcile(event,rows,stat=True,receipt_observations=receipts)
            self.assertEqual(event['effects'][key]['amount'],57 if accepted else amount)
            self.assertEqual(bool(event.get('animated_stat_evidence')),accepted)
