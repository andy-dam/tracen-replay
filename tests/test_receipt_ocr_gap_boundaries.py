"""Receipt OCR dropouts must not become positive narrative boundaries."""
import unittest

from tests.test_neural_transactions import line, raw
from tracen_replay.vision import parse
from tracen_replay.transactions import outcome_events


def reading(time, texts, screen=None):
    value=parse(raw([line(text,(316,805+index*28,750,837+index*28)) for index,text in enumerate(texts)]))
    value.update(source_timestamp_ms=time,evidence=f'{time}.png')
    if screen:value['screen']=screen
    return value


class ReceiptContinuityTests(unittest.TestCase):
    def award(self,time=1000):
        return reading(time,['Gained 3 hint level(s) for Example Skill.'])

    def test_malformed_receipt_between_repeated_awards_does_not_duplicate(self):
        for text in ('Gained 3 hint level(s) fr Example Skill.',
                     'Gained 3 hint level(for Example Skill.',
                     'Gained 3 hint level(s) foExample Skill.'):
            with self.subTest(text=text):
                gap=reading(1125,[text])
                self.assertEqual(gap['effects'],[])
                events=outcome_events([self.award(),gap,self.award(1250)])
                self.assertEqual(len(events),1)
                self.assertEqual(len(events[0]['effects']),1)
                self.assertEqual(events[0]['effects'][0]['amount'],3)
                self.assertEqual(events[0]['field_evidence']['skill_hint_change||Example Skill'],['1000.png','1250.png'])

    def test_unknown_receipt_does_not_supply_names_or_values(self):
        gap=reading(1125,['Gained 8 hint level(s) fr Unknown Identity.'])
        self.assertEqual(outcome_events([gap]),[])
        similar=reading(1125,['Gained 8 hint level(s) fr Example Skil.'])
        self.assertEqual(similar['effects'],[])
        events=outcome_events([self.award(),similar,self.award(1250)])
        self.assertEqual(len(events),2)
        self.assertEqual({e['name'] for ev in events for e in ev['effects']},{'Example Skill'})
        self.assertEqual(events[0]['effects'][0]['amount'],3)

    def test_different_visible_identity_or_amount_cannot_anchor(self):
        for text in ('Gained 2 hint level(s) fr Example Skill.',
                     'Gained 8 hint level(s) fr Example Skill.',
                     'Gained 3 hint level(s) fr Example Race.',
                     'Gained 3 hint level(s) fr New Example Skill.'):
            with self.subTest(text=text):
                self.assertEqual(len(outcome_events([self.award(),reading(1125,[text]),self.award(1250)])),2)

    def test_anchor_can_preserve_an_uncertain_companion_without_accepting_it(self):
        gap=reading(1125,['Gained 3 hint level(for Example Skill.',
                          'Friendship with Example Trner w nt up by 5.'])
        self.assertEqual(gap['effects'],[])
        events=outcome_events([self.award(),gap,self.award(1250)])
        self.assertEqual(len(events),1)
        self.assertEqual(len(events[0]['effects']),1)
        self.assertFalse(events[0]['receipt_continuity_evidence'][0]['accepted_as_effect'])

    def test_unresolved_amount_conflict_cannot_anchor(self):
        changed=reading(1050,['Gained 2 hint level(s) for Example Skill.'])
        gap=reading(1125,['Gained 3 hint level(s) fr Example Skill.'])
        events=outcome_events([self.award(),changed,gap,self.award(1250)])
        self.assertEqual(len(events),2)

    def test_anchor_does_not_hide_narrative_or_incompatible_receipt(self):
        for text in ('Learned something valuable about our next race',
                     'Acquired a new perspective on our training session',
                     'Friendship with her has been a real challenge today',
                     'Gained 3 supporters during the concert preparation',
                     'Gained 8 hint level(s) fr Example Skill.',
                     'Gained 3 hint level(s) fr Example Race.'):
            with self.subTest(text=text):
                gap=reading(1125,['Gained 3 hint level(s) fr Example Skill.',text])
                self.assertEqual(len(outcome_events([self.award(),gap,self.award(1250)])),2)

    def test_actual_narrative_and_other_screens_still_split(self):
        for gap in (
            reading(1125,['We should go back to training tomorrow.']),
            reading(1125,['Learned something valuable about our next race']),
            reading(1125,['Acquired a new perspective on our training session']),
            reading(1125,['Friendship with her has been a real challenge today']),
            reading(1125,['Gained 3 supporters during the concert preparation']),
            reading(1125,['Gained 3 hint level(s) fr Example Skill.',
                          'We should go back to training tomorrow.']),
            reading(1125,['Gained 3 hint level(s) fr Example Skill.'],screen='training_preview'),
        ):
            with self.subTest(gap=gap):
                self.assertEqual(len(outcome_events([self.award(),gap,self.award(1250)])),2)

    def test_changed_title_still_marks_a_boundary(self):
        first=self.award()
        first['context_title']='First event'
        gap=reading(1125,['Gained 3 hint level(s) fr Example Skill.'])
        gap['context_title']='Next event'
        self.assertEqual(len(outcome_events([first,gap,self.award(1250)])),2)

    def test_receipt_fragments_cannot_bridge_long_absence(self):
        events=outcome_events([self.award(),reading(1501,['Gained 3 hint level(s) fr Example Skill.']),self.award(1750)])
        self.assertEqual(len(events),2)


if __name__=='__main__':unittest.main()
