"""Gains read as the result panel's totals minus the last snapshot before the training."""
import unittest

from tracen_replay.transactions import training_events, training_actions


BEFORE = dict(speed=348, stamina=303, power=225, guts=252, wit=455, skill_points=687)
AFTER = dict(speed=351, stamina=303, power=227, guts=263, wit=455, skill_points=692)


def home(time, values=BEFORE):
    return dict(source_timestamp_ms=time, evidence=f'{time}.png', screen='unknown', facts={},
                stats=dict(values=dict(values), training_preview=False, preview_option=None))


def banner(time, name='Incline', option='Guts'):
    proof = dict(heading=dict(text=f'{option} Lvl 1', confidence=100, box=[230, 169, 318, 193]),
                 name=dict(text=name, confidence=100, box=[226, 201, 296, 228]), basis='same_frame_training_heading_and_name')
    return dict(source_timestamp_ms=time, evidence=f'{time}.png', screen='unknown', stats=dict(values=None),
                facts=dict(training_name=name, training_level=1, training_identity_evidence=proof))


def result(time, values=AFTER, outcome='success', option='guts', gains=None):
    facts = dict(result_values=dict(values), training_outcome=outcome)
    if gains:
        facts['training_gains'] = dict(gains)
    return dict(source_timestamp_ms=time, evidence=f'{time}.png', screen='training_result', training_option=option,
                stats=dict(values=None), facts=facts)


def receipt(time, field, amount):
    return dict(source_timestamp_ms=time, evidence=f'{time}.png', screen='event_outcome', context_title='Fan Letter',
                effects=[dict(kind='stat_change', field=field, amount=amount, raw_text=f'{field} went up by {amount}.', confidence=99)],
                facts={}, stats=dict(values=None))


class ResultTotalGainsTests(unittest.TestCase):
    def test_totals_minus_snapshot_are_the_gains(self):
        rows = [home(647500), home(648000)] + [banner(t) for t in range(654000, 655750, 250)] + [result(656250), result(656500)]
        events = training_events(rows)
        self.assertEqual(len(events), 1)
        event = events[0]
        self.assertEqual(event['training_option'], 'guts')
        self.assertEqual(event['deltas'], dict(speed=3, power=2, guts=11, skill_points=5))
        self.assertEqual(sorted(event['result_state_derived_fields']), ['guts', 'power', 'skill_points', 'speed'])
        self.assertEqual(event['result_total_gains']['guts']['basis'], 'result_panel_totals_minus_prior_snapshot')
        self.assertEqual(event['result_total_gains']['guts']['before']['value'], 252)
        actions = training_actions(events)
        self.assertEqual((actions[0]['training_option'], actions[0]['identity_basis']), ('guts', 'observed_gains'))

    def test_one_result_frame_is_not_enough(self):
        rows = [home(647500)] + [result(656250)]
        self.assertEqual(training_events(rows)[0]['deltas'], {})

    def test_a_receipt_between_is_subtracted(self):
        rows = [home(647500), receipt(650000, 'speed', 3), result(656250), result(656500)]
        self.assertEqual(training_events(rows)[0]['deltas'], dict(power=2, guts=11, skill_points=5))

    def test_a_last_panel_the_frames_after_it_read_otherwise_was_misread(self):
        # The last full panel misread guts 252 as 232; two partial panels
        # after it read 252 again. Guts rose by 11, not by 31.
        misread = dict(BEFORE, guts=232)
        partial = dict(BEFORE, power=None)
        rows = [home(647000), home(647500, misread), home(648000, partial), home(648250, partial),
                result(656250), result(656500)]
        event = training_events(rows)[0]
        self.assertEqual(event['deltas']['guts'], 11)
        self.assertEqual(event['result_total_gains']['guts']['before'],
                         dict(source_timestamp_ms=648250, evidence='648250.png', value=252))
        # A single frame after it is no second reading: the panel stands.
        rows = [home(647000), home(647500, misread), home(648000, partial), result(656250), result(656500)]
        self.assertEqual(training_events(rows)[0]['deltas']['guts'], 31)

    def test_a_disagreeing_badge_cancels_the_totals(self):
        rows = [home(647500), result(656250, gains=dict(guts=12)), result(656500, gains=dict(guts=12))]
        event = training_events(rows)[0]
        self.assertNotIn('result_total_gains', event)
        self.assertNotIn('speed', event['deltas'])

    def test_failure_and_lower_totals_are_left_alone(self):
        rows = [home(647500), result(656250, outcome='failure'), result(656500, outcome='failure')]
        self.assertEqual(training_events(rows)[0]['deltas'], {})
        lower = dict(AFTER, guts=240)
        rows = [home(647500), result(656250, values=lower), result(656500, values=lower)]
        self.assertEqual(training_events(rows)[0]['deltas'], {})


if __name__ == '__main__':
    unittest.main()
