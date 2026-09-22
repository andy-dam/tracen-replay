"""Tests of ``tests.test_training_gain_source_parser`` that need locally preserved evidence; they run only where it is."""
import json
import unittest
from tracen_replay.vision import parse, performance_panel_facts
from tests.test_training_gain_source_parser import FIXTURE, raw_training


class TrainingGainSourceParserTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not FIXTURE.exists():
            raise unittest.SkipTest("preserved training-gain source fixtures are not present")
        cls.fixture = json.loads(FIXTURE.read_text(encoding='utf-8'))

    def test_all_frozen_source_contributions_resolve_from_amount_crops(self):
        contributions = []
        for case in self.fixture['numeric_cases']:
            contributions.extend(case.get('contributions') or [case])

        self.assertEqual(len(contributions), 16)
        for case in contributions:
            with self.subTest(fixture_id=case['fixture_id']):
                self.assertTrue(case['observations'])
                observation = case['observations'][0]
                parsed = parse(raw_training(observation))
                facts = parsed['facts']
                field = case['field']
                self.assertEqual(parsed['screen'], 'training_result')
                self.assertEqual(facts['training_gains'][field],
                                 case['source_expected_amount'])
                provenance = facts['training_gain_crop_provenance'][field]
                self.assertEqual(provenance['canonical_amount'],
                                 case['source_expected_amount'])
                self.assertTrue(provenance['canonical_basis'].startswith('source_crop_')
                                or provenance['canonical_basis'].startswith('same_family_'))

    def test_result_only_numeric_badge_is_diagnostic_and_stays_unpromoted(self):
        raw = raw_training({
            'raw_lines': [{'text': 'Training', 'confidence': 99, 'box': [155, 0, 250, 29]}],
            'raw_regions': {
                'result.speed': {
                    'text': '+12', 'confidence': 99, 'box': [322, 834, 448, 876],
                },
            },
        })

        facts = parse(raw)['facts']

        self.assertNotIn('speed', facts['training_gains'])
        self.assertEqual(facts['training_gain_candidates']['speed'], [])
        result = facts['training_gain_crop_provenance']['speed']['candidates'][0]
        self.assertFalse(result['canonical_eligible'])
        self.assertEqual(result['source_role'], 'result_crop_diagnostic_excluded')

    def test_raw_result_total_cannot_fill_a_missing_canonical_gain(self):
        raw = raw_training({
            'raw_lines': [{'text': 'Training', 'confidence': 99, 'box': [155, 0, 250, 29]}],
            'raw_regions': {
                'result.speed': {
                    'text': '512/1300', 'confidence': 99, 'box': [322, 834, 448, 876],
                },
            },
        })

        facts = parse(raw)['facts']

        self.assertNotIn('speed', facts['training_gains'])
        self.assertIsNone(facts['training_gain_crop_provenance']['speed']['canonical_amount'])

    def test_composure_source_fixture_keeps_low_confidence_merge_unresolved(self):
        case = self.fixture['composure_case']
        observation = case['observations'][0]
        facts = performance_panel_facts(observation['raw_lines'], 'training_result', {})

        self.assertNotIn('composure', facts['performance_points'])
        self.assertNotIn('composure', facts['awarded_performance_gains'])
        self.assertEqual(
            facts['performance_panel_provenance']['composure']['status'],
            'unresolved_low_confidence_merged_panel_value',
        )
