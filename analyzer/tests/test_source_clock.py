"""Stretches a phone's recorder wrote no frame in, and the time measured without them."""
import unittest

from tests.test_neural_transactions import row
from tracen_replay import source_clock
from tracen_replay.gameplay import CURRENCIES
from tracen_replay.pipeline import source_gaps
from tracen_replay.recording_verification import audit
from tracen_replay.transactions import lesson_receipts, outcome_events, training_events


def frames(*times):
    return [dict(source_timestamp_ms=time, source_pts=time, time_base='1/1000') for time in times]


def seconds(*ranges):
    return [time / 1000 for start, stop in ranges for time in range(start, stop, 17)]


class SourceGapTests(unittest.TestCase):
    def test_a_wait_the_source_had_no_frame_in_is_recorded(self):
        # Sampled at 4 per second from a 60 fps source that wrote nothing
        # between 510 and 1450: the sampler waited for the frame at 1450.
        times = seconds((0, 517), (1450, 2000))
        self.assertEqual(source_gaps(frames(0, 255, 510, 1450, 1705), times, 4, 2000, 60), [[510, 1450]])

    def test_a_frame_just_short_of_the_step_is_not_one_the_sampler_missed(self):
        # 249.4 ms after the last kept frame is under the step, so 283 is kept.
        times = [0, 0.2494, 0.283, 0.316]
        self.assertEqual(source_gaps(frames(0, 283), times, 4, 400, 59.94), [[249, 283]])

    def test_a_wait_the_source_does_not_account_for_is_left_to_the_audit(self):
        # The source has a frame at 901 that the sampler did not keep.
        times = seconds((0, 2000))
        self.assertEqual(source_gaps(frames(0, 255, 510, 1450), times, 4, 1500, 60), [])

    def test_a_steady_recording_has_no_stretches(self):
        times = seconds((0, 3000))
        self.assertEqual(source_gaps(frames(*range(0, 3000, 255)), times, 4, 3000, 60), [])

    def test_the_recording_ends_after_its_last_frame(self):
        times = seconds((0, 1100))
        self.assertEqual(source_gaps(frames(0, 255, 510, 765, 1020), times, 4, 1500, 60), [[1088, 1500]])


class ElapsedTests(unittest.TestCase):
    def tearDown(self):
        source_clock.use()

    def test_each_sampling_interval_counts_at_most_one_step(self):
        self.assertEqual(source_clock.Clock([[510, 1450]]).elapsed(500, 1500), 60)
        self.assertEqual(source_clock.Clock([[510, 1450]]).elapsed(1000, 1500), 50)
        self.assertEqual(source_clock.Clock([[0, 100], [510, 1450]]).elapsed(50, 2000), 1950 - 50 - 940)
        self.assertEqual(source_clock.Clock().elapsed(500, 1500), 1000)
        # A 60 fps recording falls on every step: plain time.
        source_clock.use([0, 250, 500, 750], 250)
        self.assertEqual(source_clock.elapsed(0, 750), 750)
        # A phone lags steps by a frame, and waits out a still screen.
        source_clock.use([0, 251, 502, 1450, 1700], 250)
        self.assertEqual(source_clock.elapsed(0, 502), 500)
        self.assertEqual(source_clock.elapsed(502, 1450), 250)
        self.assertEqual(source_clock.elapsed(1450, 1700), 250)
        self.assertEqual(source_clock.elapsed(1450, 502), -250)
        source_clock.use()
        self.assertEqual(source_clock.elapsed(502, 1450), 948)

    def test_a_receipt_seen_on_both_sides_of_a_stretch_is_one_event(self):
        effect = dict(kind='stat_change', field='guts', amount=5, raw_text='Guts went up by 5.', confidence=99)
        readings = [row(1000, 'event_outcome', effects=[effect]), row(1250, 'event_outcome', effects=[effect]),
                    row(2183, 'event_outcome', effects=[effect])]
        self.assertEqual(len(outcome_events(readings)), 2)
        source_clock.use([1000, 1250, 2183], 250)
        self.assertEqual(len(outcome_events(readings)), 1)

    def test_one_training_is_one_event_across_a_lagging_sample(self):
        gains = dict(training_gains=dict(power=17))
        readings = [row(148394, 'training_result', facts=gains), row(148644, 'unknown'),
                    row(148896, 'training_result', facts=gains)]
        for reading in readings:
            reading['training_option'] = 'power'
        self.assertEqual(len(training_events(readings)), 2)
        source_clock.use([148394, 148644, 148896], 250)
        self.assertEqual(len(training_events(readings)), 1)

    def test_a_request_seen_across_a_blank_frame_and_lagging_samples_is_one_request(self):
        # Two frames of the request dialog, two lagging steps apart with the
        # Learn press's blank frame between; each shows the lesson's gain.
        points = dict.fromkeys(CURRENCIES, 110)
        after = dict(points, visual=100)
        request = dict(name_candidates=['Makeup Basics'], projected_performance_points=after,
                       projected_stat_gains=dict(guts=5))
        times = [0, 262, 528, 795, 1050, 1300, 1550, 1800]
        readings = [row(0, 'lesson_selection', dict(performance_points=points)), row(262, 'lesson_confirmation', request),
                    row(528), row(795, 'lesson_confirmation', dict(request)),
                    row(1800, 'lesson_selection', dict(performance_points=after))]

        def receipt():
            return dict(id='outcome-1', first_seen_ms=1050, last_seen_ms=1550, evidence='receipt.png',
                        effects=[dict(kind='named_acquisition', name='Makeup Basics')], deltas={})
        self.assertEqual(lesson_receipts(readings, [receipt()])[0]['projected_stat_gains'], {})
        source_clock.use(times, 250)
        self.assertEqual(lesson_receipts(readings, [receipt()])[0]['projected_stat_gains'], dict(guts=5))


class CoverageTests(unittest.TestCase):
    def report(self, gaps=None):
        times = [0, 250, 500, 1433, 1683]
        sampling = dict(requested_fps=4)
        if gaps:
            sampling['source_frame_gaps_ms'] = gaps
        return dict(source=dict(duration_ms=1800, frame_rate=48.5, sha256='a' * 64), sampling=sampling,
                    frames=[dict(source_timestamp_ms=t, source_pts=t * 90, time_base='1/90000') for t in times],
                    gameplay_tracking=dict(readings=[dict(source_timestamp_ms=t, screen='unknown') for t in times],
                                           intervals=[]))

    def test_a_recorded_stretch_is_not_a_sampling_gap(self):
        self.assertEqual(audit(self.report())['source_coverage_errors'], ['base_sampling_gap'])
        result = audit(self.report([[510, 1433]]))
        self.assertEqual(result['source_coverage_errors'], [])
        self.assertTrue(result['full_source_processed'])


if __name__ == '__main__':
    unittest.main()
