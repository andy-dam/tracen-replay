import copy
import unittest

from tracen_replay.evaluation_adapters import _payload, report_document, source_document
from tracen_replay.observation_evaluate import evaluate
from tests.test_causal_accounting import fixture
from tests.test_evaluation_adapters import source


class EnergyCapEvaluationTests(unittest.TestCase):
    def test_capacity_alias_matches_without_changing_reports_or_labels(self):
        doc = source()
        doc['labels'][0]['expected'] = dict(kind='stat_cap_change', field='energy', amount=4)
        report = fixture()
        event = report['gameplay_tracking']['events'][0]
        event['effects'][0] = dict(kind='max_energy_change', amount=4,
                                   raw_text='Max Energy increased by 4.')
        event['field_evidence'] = {'max_energy_change||': ['receipt.png']}
        before = copy.deepcopy((doc, report))
        grade = evaluate(source_document(doc, evidence_root='C:/recording'), report_document(report))
        self.assertEqual(grade['results'][0]['status'], 'correct')
        self.assertEqual((doc, report), before)

    def test_recovered_energy_is_not_capacity(self):
        payload, _ = _payload('effect', dict(kind='energy_change', amount=4))
        self.assertEqual(payload['kind'], 'energy_change')
        self.assertNotIn('field', payload)

    def test_alias_keeps_unknown_amount_unknown(self):
        payload, original = _payload('effect', dict(kind='max_energy_change', amount=None))
        self.assertIsNone(payload['amount'])
        self.assertEqual(original['original_kind'], 'max_energy_change')

    def test_contradictory_field_is_not_rewritten(self):
        value = dict(kind='max_energy_change', field='speed', amount=4)
        self.assertEqual(_payload('effect', value)[0], value)


if __name__ == '__main__':
    unittest.main()
