import unittest
from copy import deepcopy
from tracen_replay.choice_evaluate import evaluate


class ChoiceEvaluateTests(unittest.TestCase):
    def sample(self):
        choice=dict(options=['Yes','No'],selected_index=1,selected_text='No',evidence=[dict(source_timestamp_ms=0),dict(source_timestamp_ms=100)])
        ref=dict(source_sha256='source',scope='bounded test',choices=[choice])
        pred=dict(choice,kind='dialogue_choice',selection_observed_ms=50,selection_marks={'left':{},'right':{}})
        report=dict(source={'sha256':'source'},gameplay_tracking=dict(auxiliary_log_used=False,dialogue_choices=[pred]))
        return ref,report

    def test_match_and_missing(self):
        ref,report=self.sample();self.assertTrue(evaluate(ref,report)['passed'])
        report['gameplay_tracking']['dialogue_choices']=[]
        self.assertEqual(evaluate(ref,report)['matched'],0)

    def test_wrong_option_kind_or_evidence_fails(self):
        ref,report=self.sample()
        for key,value in [('selected_text','Yes'),('kind','dialogue_response'),('selection_marks',None)]:
            changed=deepcopy(report);changed['gameplay_tracking']['dialogue_choices'][0][key]=value
            self.assertFalse(evaluate(ref,changed)['passed'])

    def test_extra_prediction_fails_and_different_source_rejected(self):
        ref,report=self.sample();report['gameplay_tracking']['dialogue_choices']*=2
        self.assertFalse(evaluate(ref,report)['passed'])
        report['source']['sha256']='different'
        with self.assertRaises(ValueError):evaluate(ref,report)
