import copy
import hashlib
import unittest
from pathlib import Path

from tracen_replay.gameplay import CURRENCIES
from tracen_replay.transaction_evaluate import evaluate
from tests.test_gameplay import workspace_temp


class SourceLessonBalanceTests(unittest.TestCase):
    def pair(self):
        before=dict(zip(CURRENCIES,[159,88,71,108,105]))
        after=dict(zip(CURRENCIES,[147,88,59,108,105]))
        cost=dict(dance=12,vocal=12)
        expected=dict(kind='lesson',start_ms=250,end_ms=500,performance_cost=cost,
            source_performance_balance=dict(
                before=dict(source_timestamp_ms=0,values=before,evidence='before.png',sha256=hashlib.sha256(b'before').hexdigest()),
                after=dict(source_timestamp_ms=750,values=after,evidence='after.png',sha256=hashlib.sha256(b'after').hexdigest()),
                other_changes=dict.fromkeys(CURRENCIES,0)))
        ref=dict(source_sha256='source',scope='lesson',start_ms=0,end_ms=1000,kinds=['lesson'],transactions=[expected])
        report=dict(source=dict(sha256='source'),gameplay_tracking=dict(auxiliary_log_used=False,
            lesson_purchases=[dict(id='lesson',source_timestamp_ms=300,performance_cost=copy.deepcopy(cost),awarded_stats={})]))
        return ref,report

    def test_omitted_currency_in_reference_is_rejected_even_if_prediction_agrees(self):
        ref,report=self.pair()
        del ref['transactions'][0]['performance_cost']['vocal']
        del report['gameplay_tracking']['lesson_purchases'][0]['performance_cost']['vocal']
        with self.assertRaisesRegex(ValueError,'disagrees'):evaluate(ref,report)

    def test_complete_balance_checks_and_evidence_hashes(self):
        ref,report=self.pair()
        with workspace_temp() as directory:
            root=Path(directory)
            (root/'before.png').write_bytes(b'before');(root/'after.png').write_bytes(b'after')
            result=evaluate(ref,report,root)
            self.assertTrue(result['passed'])
            self.assertEqual(result['source_balance_checks'],1)
            self.assertTrue(result['source_balance_hashes_checked'])
            (root/'after.png').write_bytes(b'changed')
            with self.assertRaisesRegex(ValueError,'hash or path'):evaluate(ref,report,root)

    def test_incomplete_unknown_or_misordered_source_labels_are_rejected(self):
        for case in ('missing_currency','unknown_currency','missing_other_changes','same_time','after_before_purchase','negative_balance','invalid_hash'):
            with self.subTest(case=case):
                ref,report=self.pair();check=ref['transactions'][0]['source_performance_balance']
                if case=='missing_currency':del check['before']['values']['vocal']
                if case=='unknown_currency':check['before']['values']['vocal']=None
                if case=='missing_other_changes':del check['other_changes']
                if case=='same_time':check['after']['source_timestamp_ms']=0
                if case=='after_before_purchase':check['after']['source_timestamp_ms']=100
                if case=='negative_balance':check['after']['values']['vocal']=-1
                if case=='invalid_hash':check['before']['sha256']='unknown'
                with self.assertRaises(ValueError):evaluate(ref,report)

    def test_other_observed_changes_are_accounted_and_legacy_references_stay_supported(self):
        ref,report=self.pair();check=ref['transactions'][0]['source_performance_balance']
        check['other_changes']['vocal']=5;check['after']['values']['vocal']+=5
        self.assertTrue(evaluate(ref,report)['passed'])
        del ref['transactions'][0]['source_performance_balance']
        result=evaluate(ref,report)
        self.assertTrue(result['passed']);self.assertEqual(result['source_balance_checks'],0)
        self.assertFalse(result['source_balance_hashes_checked'])


if __name__=='__main__':unittest.main()
