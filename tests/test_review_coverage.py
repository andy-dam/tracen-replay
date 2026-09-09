import hashlib
import json
import unittest
from tests.test_gameplay import workspace_temp
from tracen_replay.review_coverage import coverage


class ReviewCoverageTests(unittest.TestCase):
    def reference(self,root,name,start,end):
        (root/'proof').write_bytes(b'proof')
        value=dict(source_sha256='source',start_ms=start,end_ms=end,sample_interval_ms=250,scope='test',groups=[],
                   reviewed_samples=[dict(source_timestamp_ms=t,evidence='proof',sha256=hashlib.sha256(b'proof').hexdigest()) for t in range(start,end,250)])
        (root/name).write_text(json.dumps(value),encoding='utf-8');return value

    def test_overlaps_do_not_inflate_coverage_and_gaps_keep_source_order(self):
        with workspace_temp() as root:
            self.reference(root,'a.json',250,750);self.reference(root,'b.json',500,1000)
            result=coverage(dict(source=dict(sha256='source',duration_ms=2000)),['a.json','b.json','a.json'],root)
            self.assertEqual(result['declared_reviewed_duration_ms'],750)
            self.assertEqual(result['unreviewed_intervals'],[[0,250],[1000,2000]])
            self.assertEqual(result['next_source_order_window'],[0,250])
            self.assertFalse(result['full_recording_effect_recall_measured'])

    def test_missing_sample_and_changed_proof_fail(self):
        with workspace_temp() as root:
            ref=self.reference(root,'a.json',0,1000);capture=dict(source=dict(sha256='source',duration_ms=2000))
            ref['reviewed_samples'].pop();(root/'a.json').write_text(json.dumps(ref),encoding='utf-8')
            with self.assertRaisesRegex(ValueError,'Incomplete'):coverage(capture,['a.json'],root)
            self.reference(root,'a.json',0,1000);(root/'proof').write_bytes(b'changed')
            with self.assertRaisesRegex(ValueError,'evidence changed'):coverage(capture,['a.json'],root)

    def test_source_mismatch_and_outside_interval_fail(self):
        with workspace_temp() as root:
            self.reference(root,'a.json',0,1000)
            with self.assertRaisesRegex(ValueError,'Different source'):coverage(dict(source=dict(sha256='other',duration_ms=2000)),['a.json'],root)
            with self.assertRaisesRegex(ValueError,'Invalid reference'):coverage(dict(source=dict(sha256='source',duration_ms=500)),['a.json'],root)


if __name__=='__main__':unittest.main()
