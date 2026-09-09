import copy
import unittest
from tests.test_gameplay import workspace_temp
from tracen_replay.review_decisions import apply,decision


def queue():
    return dict(source_sha256='source',report_sha256='report',source_duration_ms=10000,context_ms=100,
        findings=[dict(id='a',reason='unparsed',start_ms=1000,end_ms=1100,evidence=['a.png'],details='fragment'),
                  dict(id='b',reason='unparsed',start_ms=1150,end_ms=1200,evidence=['b.png'],details='other')],summary={},
        source_sweep=[dict(start_ms=0,end_ms=10000)],go_ready=False)


class ReviewDecisionTests(unittest.TestCase):
    def test_recovery_does_not_hide_adjacent_issue_or_change_source_sweep(self):
        with workspace_temp() as root:
            for name in ['a.png','b.png','support.png']:(root/name).write_bytes(name.encode())
            q=queue();d=decision(q,'a','recovered','Explicit alternate receipt',['support.png'],root)
            apply(q,[d],root)
            self.assertEqual(q['summary']['recovered_findings'],1)
            self.assertEqual(q['pending_triage_windows'],[dict(start_ms=1050,end_ms=1300,finding_ids=['b'])])
            self.assertEqual(q['source_sweep'],queue()['source_sweep']);self.assertFalse(q['go_ready'])
            for status in ['confirmed_issue','unobservable']:
                alternate=dict(d,status=status);apply(q,[alternate],root)
                self.assertEqual(q['summary']['pending_findings'],2)

    def test_either_proof_or_report_changes_reopen_finding(self):
        with workspace_temp() as root:
            (root/'a.png').write_bytes(b'a');(root/'support.png').write_bytes(b's')
            d=decision(queue(),'a','recovered','Readable full receipt',['support.png'],root)
            for filename in ['a.png','support.png']:
                path=root/filename;old=path.read_bytes();path.write_bytes(b'changed')
                q=apply(queue(),[d],root);self.assertEqual(q['findings'][0]['disposition'],'stale');path.write_bytes(old)
            q=queue();q['report_sha256']='new';apply(q,[d],root)
            self.assertEqual(q['summary']['stale_decisions'],1)
            q=apply(queue(),[d,copy.deepcopy(d)],root);self.assertEqual(q['summary']['stale_decisions'],1)

    def test_missing_proof_and_escape_are_rejected(self):
        with workspace_temp() as root:
            (root/'a.png').write_bytes(b'a')
            for paths in [[],['missing.png'],['../outside.png']]:
                with self.assertRaises(ValueError):decision(queue(),'a','recovered','Reason',paths,root)
