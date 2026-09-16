"""The learned reader's targets, decoding, accepted shapes and training mix, without training anything."""
import random
import unittest

from tools.train_reader import MAX_PER_TARGET, ctc_decode, encode, epoch_rows, learned_read, training_groups


class TrainReaderTests(unittest.TestCase):
    def test_targets_and_greedy_decoding_round_trip(self):
        self.assertEqual(encode('190/1341'), [2, 10, 1, 11, 2, 4, 5, 2])
        self.assertEqual(encode('+7'), [12, 8])
        self.assertEqual(encode(''), [])
        self.assertEqual(ctc_decode([0, 2, 2, 0, 10, 1, 1, 0, 11, 0, 2, 4, 5, 2]), '190/1341')
        self.assertEqual(ctc_decode([0, 2, 0, 2, 0]), '11')
        self.assertEqual(ctc_decode([0, 0, 0]), '')
        # Each character's confidence is its peak over the columns it spans.
        self.assertEqual(ctc_decode([0, 2, 2, 0, 2, 12], [0.9, 0.6, 0.8, 0.9, 0.7, 0.5]), ('11+', [0.8, 0.7, 0.5]))

    def test_reads_count_only_in_the_shapes_the_analyzer_accepts(self):
        def c(text, value=0.99):
            return [value] * len(text)
        self.assertEqual(learned_read('speed', '190/1341', c('190/1341', 0.95), 0.9), (190, None))
        self.assertEqual(learned_read('speed', '190/1341', c('190/1341', 0.85), 0.9), (None, None))
        # An uncertain cap does not hold back a certain value; an uncertain value digit does.
        self.assertEqual(learned_read('speed', '190/1341', c('190/', 0.95) + c('1341', 0.4), 0.9), (190, None))
        self.assertEqual(learned_read('speed', '190/1341', [0.95, 0.4, 0.95] + c('/1341'), 0.9), (None, None))
        self.assertEqual(learned_read('speed', '190', c('190'), 0.9), (None, None))
        self.assertEqual(learned_read('speed', '1900/1341', c('1900/1341'), 0.9), (None, None))
        self.assertEqual(learned_read('skill_points', '2097', c('2097'), 0.9), (2097, None))
        self.assertEqual(learned_read('skill_points', '209/7', c('209/7'), 0.9), (None, None))
        self.assertEqual(learned_read('wit', '+16', c('+16'), 0.9), (None, 16))
        self.assertEqual(learned_read('wit', '+16', [0.99, 0.99, 0.5], 0.9), (None, None))
        self.assertEqual(learned_read('wit', '+1/6', c('+1/6'), 0.9), (None, None))
        self.assertEqual(learned_read('wit', '', [], 0.9), (None, None))
        # The game renders no leading zeros and no cap below 1000: a half-covered box is not a read.
        self.assertEqual(learned_read('stamina', '01/1348', c('01/1348'), 0.9), (None, None))
        self.assertEqual(learned_read('speed', '10/160', c('10/160'), 0.9), (None, None))
        self.assertEqual(learned_read('wit', '+07', c('+07'), 0.9), (None, None))
        self.assertEqual(learned_read('skill_points', '0', c('0'), 0.9), (0, None))
        self.assertEqual(learned_read('skill_points', '012', c('012'), 0.9), (None, None))

    def test_training_groups_cap_repeats_and_group_by_content(self):
        def row(visit, target, content, time, split='train'):
            return dict(split=split, kind='result_box', run='r', visit=visit, frame=f'f{time}', field='speed',
                        target=target, content=content, source_timestamp_ms=time)
        rows = [row('c1', '115/1600', 'badge', t) for t in range(20)]
        rows += [row('c1', '+15', 'gain', 100), row('c2', '', 'blank', 200), row('c2', None, 'unknown', 300),
                 row('c3', '130/1600', 'badge', 400, split='holdout')]
        groups = training_groups(rows, {'result_box'})
        self.assertEqual({k: len(v) for k, v in groups.items()}, dict(badge=MAX_PER_TARGET, gain=1, blank=1))
        times = [r['source_timestamp_ms'] for r in groups['badge']]
        self.assertEqual(times[0], 0)
        self.assertGreater(times[-1], 15)
        epoch = epoch_rows(groups, 40, random.Random(0))
        self.assertEqual(len(epoch), 40)
        # Badges count twice: half the epoch, the other groups a quarter each.
        self.assertEqual(sum(r['content'] == 'badge' for r in epoch), 20)
        self.assertEqual(sum(r['content'] == 'gain' for r in epoch), 10)


if __name__ == '__main__':
    unittest.main()
