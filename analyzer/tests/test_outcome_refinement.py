import copy
import hashlib
import json
import unittest

from tracen_replay.refine_outcomes import apply_refinements


class OutcomeRefinementTests(unittest.TestCase):
    def setUp(self):
        self.raw = {'lines': [dict(text='Energy recovered by 10.', confidence=90,
                                   box=[300, 800, 600, 830])]}
        self.extra = dict(
            raw_sha256=hashlib.sha256(json.dumps(self.raw, sort_keys=True).encode()).hexdigest(),
            lines=[dict(index=0, text='Energy recovered by 10.', confidence=98,
                        original_confidence=90, accepted=True)])

    def test_valid_reread_preserves_text_and_original_without_mutating_input(self):
        before = copy.deepcopy(self.raw)
        result = apply_refinements(self.raw, self.extra)
        self.assertEqual(result['lines'][0]['confidence'], 98)
        self.assertEqual(result['lines'][0]['original_confidence'], 90)
        self.assertEqual(result['lines'][0]['text'], before['lines'][0]['text'])
        self.assertEqual(self.raw, before)

    def test_saved_acceptance_cannot_authorize_changed_text_or_low_confidence(self):
        for update in ({'text': 'Energy recovered by 20.'}, {'confidence': 96}):
            with self.subTest(update=update):
                extra = copy.deepcopy(self.extra)
                extra['lines'][0].update(update)
                self.assertEqual(apply_refinements(self.raw, extra), self.raw)

    def test_decision_is_recomputed_even_when_cached_flag_is_false(self):
        self.extra['lines'][0]['accepted'] = False
        self.assertTrue(apply_refinements(self.raw, self.extra)['lines'][0]['refined'])

    def test_invalid_indices_scores_and_original_confidence_are_rejected(self):
        for update in ({'index': -1}, {'index': True}, {'index': 1},
                       {'confidence': float('nan')}, {'confidence': float('inf')},
                       {'confidence': 101}, {'confidence': True},
                       {'original_confidence': 89}):
            with self.subTest(update=update):
                extra = copy.deepcopy(self.extra)
                extra['lines'][0].update(update)
                with self.assertRaises(ValueError):
                    apply_refinements(self.raw, extra)
        self.extra['lines'].append(dict(self.extra['lines'][0]))
        with self.assertRaises(ValueError):
            apply_refinements(self.raw, self.extra)

    def test_wrong_original_hash_is_rejected(self):
        self.extra['raw_sha256'] = 'wrong'
        with self.assertRaises(ValueError):
            apply_refinements(self.raw, self.extra)

    def test_outside_receipt_area_is_not_promoted(self):
        self.raw['lines'][0]['box'] = [300, 100, 600, 130]
        self.extra['raw_sha256'] = hashlib.sha256(json.dumps(self.raw, sort_keys=True).encode()).hexdigest()
        self.assertEqual(apply_refinements(self.raw, self.extra), self.raw)


if __name__ == '__main__':
    unittest.main()
