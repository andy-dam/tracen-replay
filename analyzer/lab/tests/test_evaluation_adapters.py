"""Tests of ``tests.test_evaluation_adapters`` that need locally preserved evidence; they run only where it is."""
import copy
import json
from pathlib import Path
import unittest
from tests import localdata
from tracen_replay.evaluation_adapters import evidence_ids, report_document, source_document
from tracen_replay.observation_evaluate import evaluate
from tests.test_causal_accounting import fixture
from tests.test_skill_batch_metadata import _batch_rows, _batch_states
from tests.test_training_gain_resolution import g7_wit_rows
from tracen_replay.transactions import skill_transactions
from tracen_replay.transactions import training_events


class AdapterTests(unittest.TestCase):




    def test_actual_localized_t016_guts_binds_applied_timing_to_frame19(self):
        """Localized same-frame crops must still expose the later result frame."""
        path = localdata.root("adapter_grading_batch_fixture", 'independent-01-report.json')
        if not path.is_file():
            self.skipTest('preserved independent-01 report fixture is unavailable')
        report = json.loads(path.read_text(encoding='utf-8'))
        prediction = report_document(report)
        effect = next(
            row for row in prediction['observations']
            if row['id'] == '/gameplay_tracking/events/49/deltas/guts'
        )
        self.assertEqual(effect['payload']['amount'], 11)
        self.assertEqual((effect['start_ms'], effect['end_ms']), (218100, 218500))
        self.assertEqual(
            effect['amount_evidence'],
            ['training-inspection/217500/frame-000018.png'],
        )
        self.assertIn('training-inspection/217500/frame-000019.png', effect['phase_evidence'])
        self.assertEqual(
            effect['observation_basis'],
            'accepted_training_result_group_after_direct_gain',
        )

    def test_duplicate_localized_crop_does_not_supply_applied_timing(self):
        """Repeated proof from one crop family remains an invalid binding."""
        path = localdata.root("adapter_grading_batch_fixture", 'independent-01-report.json')
        if not path.is_file():
            self.skipTest('preserved independent-01 report fixture is unavailable')
        report = json.loads(path.read_text(encoding='utf-8'))
        resolution = report['gameplay_tracking']['events'][49][
            'source_clipped_gain_resolutions'
        ]['guts']
        resolution['accepted_observations'].append(
            copy.deepcopy(resolution['accepted_observations'][0])
        )
        prediction = report_document(report)
        effect = next(
            row for row in prediction['observations']
            if row['id'] == '/gameplay_tracking/events/49/deltas/guts'
        )
        self.assertNotIn('amount_evidence', effect)
        self.assertNotIn('phase_evidence', effect)
        self.assertNotIn('observation_basis', effect)

    def test_stale_accepted_crop_metadata_does_not_supply_applied_timing(self):
        """A stored accepted crop must still match the fresh source resolver."""
        path = localdata.root("adapter_grading_batch_fixture", 'independent-01-report.json')
        if not path.is_file():
            self.skipTest('preserved independent-01 report fixture is unavailable')
        report = json.loads(path.read_text(encoding='utf-8'))
        resolution = report['gameplay_tracking']['events'][49][
            'source_clipped_gain_resolutions'
        ]['guts']
        resolution['accepted_observations'][0]['raw_text'] = '+99'
        prediction = report_document(report)
        effect = next(
            row for row in prediction['observations']
            if row['id'] == '/gameplay_tracking/events/49/deltas/guts'
        )
        self.assertNotIn('amount_evidence', effect)
        self.assertNotIn('phase_evidence', effect)
        self.assertNotIn('observation_basis', effect)
