import unittest
from tests.test_neural_transactions import raw,line,row
from tracen_replay.vision import parse
from tracen_replay.transactions import outcome_events


class TitleContinuityTests(unittest.TestCase):
    def test_wrapped_technique_name_preserves_the_acquisition_receipt(self):
        source=raw([line("Learned Watch an Up-and-Coming Idol's",(306,785,699,814)),
                    line('Concert.',(309,811,396,835))])
        effect=parse(source)['effects'][0]
        self.assertEqual(effect['kind'],'named_acquisition')
        self.assertEqual(effect['name'],"Watch an Up-and-Coming Idol's Concert")
        source['lines'][1]=line('Wit went up by 5.',(309,811,500,835))
        self.assertFalse(any(e['kind']=='named_acquisition' for e in parse(source)['effects']))
        source['lines'][1]=line('Concert.',(600,811,690,835))
        self.assertFalse(any(e['kind']=='named_acquisition' for e in parse(source)['effects']))

    def pair(self):
        effect=dict(kind='stat_change',field='skill_points',amount=57)
        return [row(100,'event_outcome',effects=[effect],context_title='Race: The Decisive Match',context_title_candidate='Race: The Decisive Match'),
                row(350,'event_outcome',effects=[effect],context_title='Decisive Match',context_title_candidate='Race: The Decisive Match')]

    def test_weak_caption_is_a_candidate_not_the_reported_title(self):
        result=parse(raw([line('Race: The',(241,192,562,219),94),line('Decisive Match',(242,217,383,241),99)]))
        self.assertEqual(result['context_title'],'Decisive Match')
        self.assertEqual(result['context_title_candidate'],'Race: The Decisive Match')

    def test_continuous_wrapped_title_does_not_award_again(self):
        events=outcome_events(self.pair())
        self.assertEqual(len(events),1)
        self.assertEqual(events[0]['deltas'],{'skill_points':57})
        self.assertEqual(events[0]['context_title'],'Race: The Decisive Match')
        self.assertEqual(events[0]['title_continuation_evidence'][0]['evidence'],'350.png')

    def test_suffix_alone_gap_or_new_effect_cannot_merge_events(self):
        rows=self.pair();rows[1]['context_title_candidate']='Decisive Match'
        self.assertEqual(len(outcome_events(rows)),2)
        rows=self.pair();rows[1]['source_timestamp_ms']=601
        self.assertEqual(len(outcome_events(rows)),2)
        rows=self.pair();rows[1]['effects']=[dict(kind='stat_change',field='skill_points',amount=58)]
        self.assertEqual(len(outcome_events(rows)),2)
        rows=self.pair();rows.insert(1,row(200,'training_preview'))
        self.assertEqual(len(outcome_events(rows)),2)

    def test_a_caption_read_with_its_head_gone_on_the_same_pixels_is_the_same_caption(self):
        # The event's last frame wipes the caption off from the left: 'Ready
        # for a Challenge' reads 'Challenge' at the same right edge and rows,
        # over the same receipt. The text alone could be another event's.
        effect=dict(kind='stat_change',field='speed',amount=5)
        rows=[row(100,'event_outcome',effects=[effect],context_title='Ready for a Challenge',context_title_box=[240,204,446,234]),
              row(350,'event_outcome',effects=[effect],context_title='Challenge',context_title_box=[352,207,442,231])]
        events=outcome_events(rows)
        self.assertEqual(len(events),1)
        self.assertEqual(events[0]['deltas'],{'speed':5})
        self.assertEqual(events[0]['context_title'],'Ready for a Challenge')
        self.assertEqual(events[0]['title_continuation_evidence'][0]['basis'],'caption_head_cut_on_same_pixels')
        self.assertNotIn('context_title_box',events[0])
        # Elsewhere on the line, or without the boxes, a suffix is its own caption.
        rows[1]['context_title_box']=[240,204,330,234]
        self.assertEqual(len(outcome_events(rows)),2)
        for r in rows:r.pop('context_title_box')
        self.assertEqual(len(outcome_events(rows)),2)

    def test_the_caption_box_is_where_its_lines_sat(self):
        self.assertEqual(parse(raw([line('Ready for a Challenge',(240,204,446,234),99)]))['context_title_box'],[240,204,446,234])
        self.assertIsNone(parse(raw([line('Wit went up by 5.',(309,811,500,835))]))['context_title_box'])

    def test_later_clear_caption_can_complete_an_earlier_fragment(self):
        rows=self.pair();rows[0]['context_title']='Decisive Match';rows[1]['context_title']='Race: The Decisive Match'
        events=outcome_events(rows)
        self.assertEqual(len(events),1)
        self.assertEqual(events[0]['context_title'],'Race: The Decisive Match')
