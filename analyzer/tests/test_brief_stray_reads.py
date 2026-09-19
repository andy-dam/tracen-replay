"""A read that lasted one moment beside a number read whole on many frames."""
import unittest

from tests.test_neural_transactions import row
from tracen_replay.preview_confirmed_gains import badge_contradictions
from tracen_replay.transactions import outcome_events


class OutlierMomentTests(unittest.TestCase):
    def test_a_cut_receipt_read_on_adjacent_dense_frames_is_one_sighting(self):
        # "Visuals went up by 20." on three sparse frames; "by 2." on two dense
        # frames 33 ms apart while a particle crossed the zero.
        whole = dict(kind='performance_change', field='visual', amount=20, raw_text='Visuals went up by 20.', confidence=98)
        cut = dict(kind='performance_change', field='visual', amount=2, raw_text='Visuals went up by 2.', confidence=98)
        readings = [row(1000, 'event_outcome', effects=[whole]), row(1250, 'event_outcome', effects=[whole]),
                    row(1500, 'event_outcome', effects=[whole]), row(1517, 'event_outcome', effects=[cut]),
                    row(1550, 'event_outcome', effects=[cut])]
        event, = outcome_events(readings)
        self.assertEqual([(e['field'], e['amount']) for e in event['effects']], [('visual', 20)])
        self.assertEqual(event['resolved_reading_conflicts'][0]['basis'], 'repeated_complete_digits_with_single_truncated_outlier')
        # The same cut read spread over more than a moment still disputes the number.
        readings[-1] = row(2000, 'event_outcome', effects=[cut])
        event, = outcome_events(readings)
        self.assertEqual([c['reason'] for c in event['conflicting_readings']], ['changing_effect_value'] * 2)


class NegativeBoundTests(unittest.TestCase):
    def test_a_panel_that_fell_across_a_training_contradicts_no_badge(self):
        def home(time, wit):
            return dict(source_timestamp_ms=time, evidence=f'home-{time}.png', screen='unknown', facts={}, effects=[],
                        stats=dict(values=dict(speed=713, stamina=165, power=521, guts=273, wit=wit, skill_points=1232)))
        result = dict(source_timestamp_ms=1500, evidence='card.png', screen='training_result', facts=dict(training_gains=dict(wit=30)), stats={}, effects=[])
        group = dict(first_seen_ms=1500, last_seen_ms=1500, rows=[result], option='wit')
        # The next hub read 876 as 58: the bound would be negative.
        self.assertEqual(badge_contradictions([home(1000, 828), result, home(2000, 58)], group, dict(wit=30)), {})
        # A real bound below the badge is still a contradiction.
        found = badge_contradictions([home(1000, 828), result, home(2000, 850)], group, dict(wit=30))
        self.assertEqual((found['wit']['badge'], found['wit']['allowed']), (30, 22))


if __name__ == '__main__':
    unittest.main()
