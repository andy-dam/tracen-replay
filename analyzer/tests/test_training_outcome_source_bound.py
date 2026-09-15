"""Tests for semantic promotion of source-bound training-result crops."""

from __future__ import annotations

import copy
import json
from pathlib import Path
import unittest

from tracen_replay.training_outcome import (
    SOURCE_BOUND_BASIS,
    source_bound_banner_facts,
    summarize,
)
from tracen_replay.transactions import training_events
from tracen_replay.vision import parse
from tracen_replay.weak_state_recovery import load

from tests import localdata

BASE = localdata.root("development_second_recording")
RAW_PATH = BASE / 'neural/part-011-frame-000114.json'
EVIDENCE_PATH = BASE / 'gameplay/part-011-frame-000114.png'
SOURCE_PATH = BASE / 'part-011/frames/000114.jpg'
SIDECAR_PATH = localdata.root(
    "weak_state_recovery_inputs", 'independent-01-t063-training-success.json'
)


class SourceBoundTrainingOutcomeTests(unittest.TestCase):
    def setUp(self):
        if not all(path.is_file() for path in (
                RAW_PATH, EVIDENCE_PATH, SOURCE_PATH, SIDECAR_PATH)):
            self.skipTest('frozen source banner case is not present in this checkout')
        self.raw = json.loads(RAW_PATH.read_text(encoding='utf-8'))
        self.refined = load(
            self.raw,
            SIDECAR_PATH,
            evidence_path=EVIDENCE_PATH,
            source_frame_path=SOURCE_PATH,
            source_frame_id=RAW_PATH.stem,
            source_frame_evidence='independent-01/part-011/frames/000114.jpg',
        )

    def _facts(self, refined=None):
        return source_bound_banner_facts(
            self.refined if refined is None else refined,
            screen='training_result',
        )

    def _row(self, facts=None):
        facts = copy.deepcopy(self._facts() if facts is None else facts)
        facts.setdefault('training_gains', {})
        facts.setdefault('awarded_performance_gains', {})
        return {
            'source_timestamp_ms': self.raw['source_timestamp_ms'],
            'evidence': self.raw['evidence'],
            'screen': 'training_result',
            'training_option': 'speed',
            'facts': facts,
            'stats': {'values': {}},
        }

    def test_actual_cached_banner_promotes_one_physical_source(self):
        facts = self._facts()
        self.assertEqual(facts['training_outcome'], 'success')
        self.assertEqual(facts['outcome_observations'][0]['basis'], SOURCE_BOUND_BASIS)
        self.assertEqual(facts['outcome_observations'][0]['physical_source_count'], 1)
        self.assertEqual(
            [item['confidence'] for item in facts['source_bound_training_outcome']['views']],
            [96.15, 91.864, 92.193],
        )
        event = training_events([self._row(facts)])[0]
        self.assertEqual(event['training_outcome'], 'success')
        self.assertEqual(event['success_evidence'], [self.raw['evidence']])
        self.assertEqual(event['outcome_observations'][0]['basis'], SOURCE_BOUND_BASIS)
        self.assertEqual(event['outcome_observations'][0]['physical_source_count'], 1)

    def test_actual_cached_banner_promotes_through_normal_parse(self):
        parsed = parse(self.refined)
        self.assertEqual(parsed['screen'], 'training_result')
        self.assertEqual(parsed['facts']['training_outcome'], 'success')
        self.assertEqual(
            parsed['facts']['outcome_observations'][0]['basis'], SOURCE_BOUND_BASIS)
        row = dict(parsed, source_timestamp_ms=self.raw['source_timestamp_ms'],
                   evidence=self.raw['evidence'])
        event = training_events([row])[0]
        self.assertEqual(event['training_outcome'], 'success')
        self.assertEqual(event['outcome_observations'][0]['physical_source_count'], 1)

    def test_preview_and_unknown_source_are_rejected(self):
        preview = copy.deepcopy(self.refined)
        preview['current_grid'] = True
        preview['result_grid'] = False
        self.assertEqual(source_bound_banner_facts(preview, screen='training_result'), {})

        unavailable = copy.deepcopy(self.refined)
        unavailable['weak_state_recovery']['source_frame_verification'] = {
            'status': 'unavailable',
            'expected_sha256': unavailable['weak_state_recovery']['source_frame_sha256'],
        }
        self.assertEqual(source_bound_banner_facts(unavailable, screen='training_result'), {})

    def test_conflicting_or_duplicate_views_are_rejected(self):
        duplicate = copy.deepcopy(self.refined)
        proof = duplicate['weak_state_recovery']['source_bound_observations'][
            'weak_state_recovery.training_result_banner']
        proof['views'].append(copy.deepcopy(proof['views'][0]))
        self.assertEqual(source_bound_banner_facts(duplicate, screen='training_result'), {})

        conflict = copy.deepcopy(self.refined)
        proof = conflict['weak_state_recovery']['source_bound_observations'][
            'weak_state_recovery.training_result_banner']
        proof['views'][1]['text'] = 'FAILURE!'
        proof['views'][1]['parsed_value'] = 'FAILURE'
        self.assertEqual(source_bound_banner_facts(conflict, screen='training_result'), {})

    def test_summary_does_not_trust_a_forged_source_bound_marker(self):
        facts = self._facts()
        facts['source_bound_training_outcome']['physical_source_count'] = 2
        result = summarize([self._row(facts)])
        self.assertEqual(result['training_outcome'], 'unknown')
        self.assertNotIn('success_evidence', result)


if __name__ == '__main__':
    unittest.main()
