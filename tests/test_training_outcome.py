import copy
import unittest

from tracen_replay.training_outcome import banner_facts
from tracen_replay.transactions import training_actions, training_events


def line(text='SUCCESS!', confidence=99, box=(366,684,732,773)):
    return dict(text=text,confidence=confidence,box=list(box))


def row(time, outcome=None, *, option='speed'):
    facts={'training_gains':{'speed':10},'awarded_performance_gains':{'vocal':5}}
    if outcome:
        facts.update(banner_facts([line(outcome)],'training_result'))
    return dict(source_timestamp_ms=time,evidence=f'frame-{time}',screen='training_result',
                training_option=option,facts=facts,stats={'values':{}})


class TrainingOutcomeTests(unittest.TestCase):
    def test_explicit_success_banner_reaches_committed_action_with_evidence(self):
        rows=[row(100),row(350,'SUCCESS!')]
        original=copy.deepcopy(rows)
        events=training_events(rows)
        actions=training_actions(events)
        self.assertEqual(actions[0]['training_outcome'],'success')
        self.assertEqual(actions[0]['success_evidence'],['frame-350'])
        self.assertEqual(actions[0]['outcome_observations'][0]['source_timestamp_ms'],350)
        self.assertEqual(events[0]['performance_deltas'],{'vocal':5})
        self.assertEqual(rows,original)

    def test_gains_alone_do_not_prove_success(self):
        actions=training_actions(training_events([row(100),row(350)]))
        self.assertEqual(actions[0]['training_outcome'],'unknown')
        self.assertEqual(actions[0]['success_evidence'],[])

    def test_failure_preserves_suppression_of_projected_performance(self):
        events=training_events([row(100),row(350,'FAILURE')])
        self.assertEqual(events[0]['training_outcome'],'failure')
        self.assertEqual(events[0]['performance_deltas'],{})
        self.assertEqual(events[0]['unawarded_performance_projection'],{'vocal':5})
        self.assertEqual(training_actions(events)[0]['failure_evidence'],['frame-350'])

    def test_success_and_failure_conflict_remains_unknown(self):
        events=training_events([row(100,'SUCCESS!'),row(350,'FAILURE')])
        self.assertEqual(events[0]['training_outcome'],'unknown')
        self.assertEqual(set(events[0]['training_outcome_conflicts']),{'success','failure'})
        self.assertEqual(events[0]['performance_deltas'],{})
        action=training_actions(events)[0]
        self.assertEqual(len(action['outcome_observations']),2)
        self.assertEqual(action['training_outcome'],'unknown')

    def test_simultaneous_conflicting_banners_are_preserved(self):
        facts=banner_facts([line('SUCCESS!'),line('FAILURE')],'training_result')
        self.assertEqual(facts['training_outcome'],'unknown')
        sample=row(100);sample['facts'].update(facts)
        events=training_events([sample,row(350)])
        self.assertEqual(events[0]['training_outcome'],'unknown')
        self.assertEqual(events[0]['performance_deltas'],{})

    def test_parser_conflict_does_not_award_projected_performance(self):
        from unittest.mock import patch
        from tracen_replay.vision import parse
        sample=dict(lines=[line('SUCCESS!'),line('FAILURE')],regions={},
                    header='Training',current_grid=False,result_grid=True)
        with patch('tracen_replay.vision.performance_panel_facts',
                   return_value={'awarded_performance_gains':{'vocal':5}}):
            parsed=parse(sample)
        self.assertEqual(parsed['screen'],'training_result')
        self.assertEqual(parsed['facts']['training_outcome'],'unknown')
        self.assertNotIn('awarded_performance_gains',parsed['facts'])
        self.assertEqual(parsed['facts']['unawarded_performance_projection'],{'vocal':5})

    def test_preview_small_text_narrative_and_weak_ocr_cannot_award_success(self):
        for screen, candidate in [('training_preview',line()),('event_outcome',line()),
                                  ('training_result',line(confidence=96)),
                                  ('training_result',line(box=(300,820,730,845))),
                                  ('training_result',line('Training will be a SUCCESS!')),
                                  ('training_result',line('FAILURE',box=(300,760,390,790)))]:
            with self.subTest(screen=screen,candidate=candidate):
                facts = banner_facts([candidate],screen)
                self.assertNotIn('training_outcome', facts)
                self.assertNotIn('success_banner', facts)
                self.assertNotIn('failure_banner', facts)
                if candidate['confidence'] != 96:
                    self.assertEqual(facts, {})

    def test_banner_does_not_leak_to_next_training(self):
        events=training_events([row(100,'SUCCESS!'),row(350),row(1000),row(1250)])
        self.assertEqual([e['training_outcome'] for e in events],['success','unknown'])

    def test_different_training_option_starts_separate_outcome_group(self):
        events=training_events([row(100,'SUCCESS!',option='speed'),row(350,option='wit')])
        self.assertEqual([e['training_outcome'] for e in events],['success','unknown'])


if __name__=='__main__':unittest.main()
