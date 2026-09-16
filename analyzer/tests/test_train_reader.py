"""The learned reader's targets, decoding and scoring, without training anything."""
import unittest

from tools.train_reader import balance_rows, ctc_decode, encode, score, select_rows, value_of


class TrainReaderTests(unittest.TestCase):
    def test_targets_and_greedy_decoding_round_trip(self):
        self.assertEqual(encode(137), [2, 4, 8])
        self.assertEqual(encode(0), [])
        self.assertEqual(encode(None), [])
        self.assertEqual(ctc_decode([0, 2, 2, 0, 4, 0, 8, 8]), '137')
        self.assertEqual(ctc_decode([0, 2, 0, 2, 0]), '11')
        self.assertEqual(ctc_decode([0, 0, 0]), '')
        self.assertEqual(value_of(''), 0)
        self.assertEqual(value_of('137'), 137)

    def test_training_uses_confirmed_train_crops_and_scores_per_kind(self):
        rows = [
            dict(kind='stat_badge', split='train', status='confirmed', label=137),
            dict(kind='stat_badge', split='train', status='hard', label=137),
            dict(kind='gain_overlay', split='train', status='confirmed', label=0),
            dict(kind='stat_badge', split='holdout', status='confirmed', label=150),
            dict(kind='stat_badge', split='holdout', status='hard', label=160),
            dict(kind='performance_counter', split='train', status='confirmed', label=30),
        ]
        train, holdout = select_rows(rows, {'stat_badge', 'gain_overlay'})
        self.assertEqual([r['label'] for r in train], [137, 0])
        self.assertEqual([r['label'] for r in holdout['confirmed']], [150])
        self.assertEqual([r['label'] for r in holdout['hard']], [160])
        scored = score(holdout['confirmed'] + holdout['hard'], [('150', 0.95), ('161', 0.5)])
        self.assertEqual(scored['stat_badge'], dict(n=2, exact=0.5, confident_coverage=0.5, confident_accuracy=1.0))

    def test_balancing_caps_zero_gains_and_repeats_small_kinds(self):
        rows = [dict(kind='gain_overlay', label=0)] * 20 + [dict(kind='gain_overlay', label=5)] * 4 + [dict(kind='stat_badge', label=137)] * 2
        balanced = balance_rows(rows, 0)
        gains = [r for r in balanced if r['kind'] == 'gain_overlay']
        self.assertEqual(sum(r['label'] == 0 for r in gains), 4)
        self.assertEqual(len(gains), 8)
        self.assertEqual(sum(r['kind'] == 'stat_badge' for r in balanced), 8)


if __name__ == '__main__':
    unittest.main()
