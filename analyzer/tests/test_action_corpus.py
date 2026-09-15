import hashlib
import json
from pathlib import Path
import shutil
import unittest
import uuid

from tracen_replay.action_corpus import SCHEMA, score_corpus


class ActionCorpusTests(unittest.TestCase):
    def setUp(self):
        self.test_parent = Path('.local/test-runs').resolve()
        self.root = self.test_parent / uuid.uuid4().hex
        self.root.mkdir(parents=True)
        self.addCleanup(self.remove_test_directory)
        self.report = {
            'source': {'sha256': 'a' * 64, 'duration_ms': 1000},
            'gameplay_tracking': {'auxiliary_log_used': False, 'turn_action_receipts': [
                {'kind': 'training', 'source_timestamp_ms': 100, 'training_option': 'speed'},
                {'kind': 'outing', 'source_timestamp_ms': 200},
                {'kind': 'outing', 'source_timestamp_ms': 800},
            ]},
        }
        self.index = {'schema': SCHEMA, 'source_sha256': 'a' * 64,
                      'source_duration_ms': 1000, 'references': []}

    def remove_test_directory(self):
        target = self.root.resolve()
        assert target != self.test_parent and target.is_relative_to(self.test_parent)
        shutil.rmtree(target)

    def write(self, name, value):
        path = self.root / name
        path.write_text(json.dumps(value), encoding='utf-8')
        return path

    def add(self, name, kinds, actions, *, start=0, end=500, selected=None, complete=True):
        reference = {'source_sha256': 'a' * 64, 'start_ms': start, 'end_ms': end,
                     'kinds': kinds, 'actions': actions, 'scope': 'source-reviewed test interval',
                     'independently_reviewed': True, 'reference_complete': complete}
        if not actions:
            reference['no_completed_actions'] = True
        path = self.write(name, reference)
        entry = {'path': name, 'sha256': hashlib.sha256(path.read_bytes()).hexdigest()}
        if selected is not None:
            entry['selected_kinds'] = selected
        self.index['references'].append(entry)
        return entry

    def score(self):
        return score_corpus(self.write('index.json', self.index), self.write('report.json', self.report), self.root)

    def training(self):
        return {'kind': 'training', 'training_option': 'speed', 'start_ms': 90, 'end_ms': 110}

    def test_different_kinds_can_overlap_without_counting_outside_predictions(self):
        self.add('training.json', ['training'], [self.training()])
        self.add('outing.json', ['outing'], [{'kind': 'outing', 'start_ms': 190, 'end_ms': 210}])

        result = self.score()

        self.assertEqual(result['channels']['training']['matched'], 1)
        outing = result['channels']['outing']
        self.assertEqual(outing['matched'], 1)
        self.assertEqual(outing['extra_scoped_predictions'], [])
        self.assertEqual(outing['predictions_outside_declared_scope'], [self.report['gameplay_tracking']['turn_action_receipts'][2]])
        self.assertEqual(outing['uncovered_kind_intervals_ms'], [[500, 1000]])
        self.assertFalse(result['full_recording_action_recall_measured'])
        self.assertFalse(result['channels']['rest']['scoped_agreement_passed'])

    def test_same_kind_overlap_is_rejected_even_when_it_contains_no_labels(self):
        self.add('one.json', ['race'], [], start=0, end=500)
        self.add('two.json', ['race'], [], start=400, end=600)
        with self.assertRaisesRegex(ValueError, 'Overlapping race scopes'):
            self.score()

    def test_channel_selection_replaces_one_kind_without_rewriting_source(self):
        parent = self.add('original.json', ['training', 'rest'], [self.training()], selected=['training'])
        self.add('replacement.json', ['rest'], [])

        result = self.score()

        self.assertEqual(result['channels']['training']['matched'], 1)
        self.assertTrue(result['channels']['rest']['scoped_agreement_passed'])
        self.assertEqual(result['per_reference_scores'][0]['original_reference_score']['kinds'], ['training', 'rest'])
        self.assertEqual(hashlib.sha256((self.root / parent['path']).read_bytes()).hexdigest(), parent['sha256'])

    def test_kind_cannot_be_added_to_a_reference_that_did_not_review_it(self):
        self.add('training.json', ['training'], [self.training()], selected=['outing'])
        with self.assertRaisesRegex(ValueError, 'subset'):
            self.score()

    def test_adjacent_scopes_partition_predictions_at_the_boundary(self):
        self.report['gameplay_tracking']['turn_action_receipts'] = [{'kind': 'rest', 'source_timestamp_ms': 500}]
        self.add('before.json', ['rest'], [])
        self.add('after.json', ['rest'], [{'kind': 'rest', 'start_ms': 500, 'end_ms': 510}], start=500, end=1000)

        result = self.score()['channels']['rest']

        self.assertEqual(result['matched'], 1)
        self.assertEqual(result['scoped_prediction_count'], 1)
        self.assertEqual(result['uncovered_kind_intervals_ms'], [])
        self.assertFalse(result['full_recording_recall_established'])

    def test_explicit_partial_reference_cannot_pass_scoped_agreement(self):
        self.add('training.json', ['training'], [self.training()], complete=False)
        result = self.score()['channels']['training']
        self.assertEqual(result['matched'], 1)
        self.assertFalse(result['scoped_agreement_passed'])
        self.assertEqual(result['explicit_incomplete_references'], ['training.json'])

    def test_reference_mutation_and_path_escape_are_rejected(self):
        entry = self.add('training.json', ['training'], [self.training()])
        (self.root / entry['path']).write_text('{}', encoding='utf-8')
        with self.assertRaisesRegex(ValueError, 'hash mismatch'):
            self.score()
        entry['path'] = '../escape.json'
        with self.assertRaisesRegex(ValueError, 'relative to the evidence root'):
            self.score()

    def test_unrelated_source_and_invalid_duration_are_rejected(self):
        self.add('training.json', ['training'], [self.training()])
        self.index['source_sha256'] = 'b' * 64
        with self.assertRaisesRegex(ValueError, 'Different source'):
            self.score()
        self.index['source_sha256'] = 'a' * 64
        self.index['source_duration_ms'] = True
        with self.assertRaisesRegex(ValueError, 'same positive source duration'):
            self.score()


if __name__ == '__main__':
    unittest.main()
