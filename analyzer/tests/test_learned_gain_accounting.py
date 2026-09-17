"""The accounting takes a learned reader's gain only where it equals the unexplained difference."""
import unittest

from tracen_replay.causal_accounting import _learned_gain_frames


def result(time, evidence, **gains):
    fields = {field: dict(text=f'+{gain}', confidence=0.99, value=None, gain=gain) for field, gain in gains.items()}
    return dict(screen='training_result', source_timestamp_ms=time, evidence=evidence,
                facts=dict(learned_result_reads=dict(model_sha256='f' * 64, threshold=0.9, fields=fields)))


class LearnedGainAccountingTests(unittest.TestCase):
    def test_frames_that_read_exactly_the_difference(self):
        training = dict(kind='training', first_seen_ms=1000, last_seen_ms=1500)
        readings = [
            result(1000, 'a.png', speed=13),
            result(1250, 'b.png', speed=13, wit=5),
            result(1400, 'c.png', speed=1),  # a zooming overlay read short
            result(1900, 'd.png', speed=13),  # after the card, beyond the slack
            dict(screen='event_outcome', source_timestamp_ms=1200, evidence='e.png',
                 facts=dict(learned_result_reads=dict(fields=dict(speed=dict(gain=13))))),
        ]
        self.assertEqual(_learned_gain_frames(training, readings, 'speed', 13), ['a.png', 'b.png'])
        self.assertEqual(_learned_gain_frames(training, readings, 'speed', 14), [])
        self.assertEqual(_learned_gain_frames(training, readings, 'wit', 5), ['b.png'])
        # Nothing to match for a missing, zero or negative difference, or a training without times.
        self.assertEqual(_learned_gain_frames(training, readings, 'speed', 0), [])
        self.assertEqual(_learned_gain_frames(training, readings, 'speed', -13), [])
        self.assertEqual(_learned_gain_frames(dict(kind='training'), readings, 'speed', 13), [])
        # Readings without learned reads contribute nothing.
        self.assertEqual(_learned_gain_frames(training, [dict(screen='training_result', source_timestamp_ms=1000,
                                                              evidence='x.png', facts={})], 'speed', 13), [])


if __name__ == '__main__':
    unittest.main()
