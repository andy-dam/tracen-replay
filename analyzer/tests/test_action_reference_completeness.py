import unittest

from tracen_replay.action_evaluate import evaluate


class ActionReferenceCompletenessTests(unittest.TestCase):
    def inputs(self):
        reference=dict(source_sha256='source',start_ms=0,end_ms=1000,
                       kinds=['training'],scope='Completed training in sampled source frames.',
                       actions=[dict(kind='training',training_option='wit',start_ms=100,end_ms=200)])
        report=dict(source={'sha256':'source'},gameplay_tracking=dict(
            auxiliary_log_used=False,turn_action_receipts=[dict(kind='training',training_option='wit',source_timestamp_ms=150)]))
        return reference,report

    def test_matching_labels_cannot_make_an_explicit_partial_reference_pass(self):
        reference,report=self.inputs();reference['reference_complete']=False
        score=evaluate(reference,report)
        self.assertEqual(score['matched'],1)
        self.assertEqual(score['recall'],1)
        self.assertEqual(score['extra'],[])
        self.assertFalse(score['passed'])
        self.assertEqual(score['score_blockers'],['incomplete_reference'])

    def test_partial_empty_reference_is_not_a_verified_negative(self):
        reference,report=self.inputs()
        reference.update(actions=[],no_completed_actions=True,independently_reviewed=True,reference_complete=False)
        report['gameplay_tracking']['turn_action_receipts']=[]
        self.assertFalse(evaluate(reference,report)['passed'])
        reference['reference_complete']=True
        self.assertTrue(evaluate(reference,report)['passed'])

    def test_complete_means_declared_scope_and_does_not_require_native_frames(self):
        reference,report=self.inputs()
        reference.update(reference_complete=True,sample_interval_ms=250,full_frame_recall_measured=False)
        score=evaluate(reference,report)
        self.assertTrue(score['passed'])
        self.assertEqual(score['completeness'],'complete')

    def test_legacy_scores_remain_reproducible_without_inventing_a_declaration(self):
        reference,report=self.inputs();score=evaluate(reference,report)
        self.assertTrue(score['passed'])
        self.assertIsNone(score['reference_complete'])
        self.assertEqual(score['completeness'],'legacy_unspecified')

    def test_invalid_declarations_are_not_coerced_to_booleans(self):
        for value in [None,0,1,'false',[],{}]:
            with self.subTest(value=value):
                reference,report=self.inputs();reference['reference_complete']=value
                with self.assertRaises(ValueError):evaluate(reference,report)
