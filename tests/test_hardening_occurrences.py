import copy
import json
from pathlib import Path
import unittest

from tracen_replay.observation_evaluate import evaluate


class HardeningOccurrenceTests(unittest.TestCase):
    def setUp(self):
        self.cases=json.loads(Path('tests/fixtures/evaluation-hardening-occurrences.json').read_text(encoding='utf-8'))['cases']

    def grade(self,case,predictions):
        source=case['reference']
        return evaluate(dict(source_sha256=case['source_sha256'],scope_ms=[0,source['end_ms']+1],
                             reference_complete=False,observations=[source]),
                        dict(source_sha256=case['source_sha256'],auxiliary_log_used=False,observations=predictions))

    def test_real_before_after_fixtures_preserve_expected_improvements(self):
        for case in self.cases:
            for version in ('before','after'):
                with self.subTest(case=case['name'],version=version):
                    result=self.grade(case,case[version])
                    self.assertEqual(result['results'][0]['status'],case[version+'_status'])
                    self.assertFalse(result['passed'])  # Partial fixture cannot certify a whole recording.

    def test_duplicate_recovered_receipt_cannot_pass_one_source_occurrence(self):
        case=next(c for c in self.cases if c['reference']['category']=='effect')
        predictions=copy.deepcopy(case['after'])
        target=next(p for p in predictions if p['payload'].get('name')==case['reference']['payload']['name'])
        duplicate=copy.deepcopy(target);duplicate['id']+='-duplicate';predictions.append(duplicate)
        self.assertEqual(self.grade(case,predictions)['results'][0]['status'],'ambiguous')


if __name__=='__main__':unittest.main()
