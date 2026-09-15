import unittest
from tests.test_neural_transactions import raw,line,row
from tracen_replay.vision import parse
from tracen_replay.transactions import training_events,training_actions


class TrainingFailureTests(unittest.TestCase):
    def test_failure_banner_is_not_a_failure_probability(self):
        sample=raw([line('FAILURE',(397,684,714,769)),line('Performance',(160,255,250,280)),
                    line('159+17',(202,292,315,326))])
        sample.update(header='Training',result_grid=True)
        result=parse(sample)
        self.assertEqual(result['facts']['training_outcome'],'failure')
        self.assertEqual(result['facts']['unawarded_performance_projection'],{'dance':17})
        self.assertNotIn('awarded_performance_gains',result['facts'])
        sample['lines'][0]=line('Failure',(300,775,390,795))
        self.assertNotIn('training_outcome',parse(sample)['facts'])

    def test_failed_action_keeps_identity_but_rejects_lingering_projections(self):
        rows=[row(100,'training_result',{'awarded_performance_gains':{'dance':17,'passion':17}},training_option='wit'),
              row(350,'training_result',{'training_outcome':'failure','unawarded_performance_projection':{'dance':17,'passion':17}},training_option='wit')]
        event=training_events(rows)[0]
        self.assertEqual(event['performance_deltas'],{})
        self.assertEqual(event['unawarded_performance_projection'],{'dance':17,'passion':17})
        action=training_actions([event])[0]
        self.assertEqual(action['training_option'],'wit')
        self.assertEqual(action['training_outcome'],'failure')
        self.assertEqual(action['failure_evidence'],['350.png'])
        self.assertEqual(event['deltas'],{})
