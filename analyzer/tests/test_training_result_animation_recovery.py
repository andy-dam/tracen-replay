import unittest

from tracen_replay.training_gain_recovery import (
    _animation_badge_gains,
    _committed_result_rows,
    animation_totals_context,
)
from tracen_replay.vision import animation_gain_badges


def line(text, box, confidence=99.5):
    return dict(text=text, box=list(box), confidence=confidence)


class AnimationBadgeObservationTests(unittest.TestCase):
    def test_large_signed_badges_over_the_card_rows_are_observed_without_a_field(self):
        lines = [
            line('+18', (205, 787, 510, 933), 100),
            line('+7', (643, 790, 855, 927), 99),
            line('+9', (530, 920, 690, 1015), 98),
            line('+14', (280, 668, 360, 704), 100),   # resting preview badge: too short
            line('+7', (711, 837, 787, 885), 100),    # resting result badge: too short
            line('SJ', (100, 640, 380, 800), 60),
        ]
        badges = animation_gain_badges(lines)
        self.assertEqual([(b['amount'], b['row']) for b in badges], [(18, 'top'), (7, 'top'), (9, 'bottom')])
        self.assertTrue(all('field' not in b for b in badges))

    def test_low_confidence_or_unsigned_text_is_ignored(self):
        self.assertEqual(animation_gain_badges([line('+18', (205, 787, 510, 933), 80)]), [])
        self.assertEqual(animation_gain_badges([line('18', (205, 787, 510, 933), 100)]), [])
        self.assertEqual(animation_gain_badges([line('+18', (205, 300, 510, 450), 100)]), [])


def base_row(time, values, effects=()):
    return dict(source_timestamp_ms=time, screen='unknown', stats=dict(values=dict(values)), effects=list(effects), facts={})


def result_row(time, totals, *, option='speed', outcome=None, badges=()):
    facts = dict(result_values=dict(totals), training_outcome=outcome)
    if badges:
        facts['animation_gain_badges'] = [dict(amount=a, row=r) for a, r in badges]
    return dict(source_timestamp_ms=time, screen='training_result', training_option=option, stats={}, facts=facts, effects=[])


BEFORE = dict(speed=196, stamina=74, power=224, guts=143, wit=182, skill_points=120)
AFTER = dict(stamina=74, power=231, guts=143, wit=182, skill_points=129)


class AnimationTotalsContextTests(unittest.TestCase):
    def window(self):
        return dict(start_ms=55250, end_ms=56500, fields=['speed', 'stamina', 'power', 'guts', 'wit', 'skill_points'],
                    training_option='speed', source_result_projection=True)

    def test_deltas_come_from_stable_totals_against_the_last_clean_snapshot(self):
        readings = [base_row(52000, BEFORE), base_row(52250, BEFORE)]
        fresh = [result_row(55700, AFTER), result_row(55717, AFTER)]
        self.assertEqual(animation_totals_context(readings, fresh, self.window()),
                         dict(stamina=0, power=7, guts=0, wit=0, skill_points=9))

    def test_an_intervening_stat_effect_discards_the_snapshot(self):
        readings = [base_row(52000, BEFORE), base_row(53000, {}, effects=[dict(kind='stat_change', field='wit', amount=10)])]
        fresh = [result_row(55700, AFTER), result_row(55717, AFTER)]
        self.assertIsNone(animation_totals_context(readings, fresh, self.window()))

    def test_a_single_frame_total_is_not_stable(self):
        readings = [base_row(52000, BEFORE)]
        fresh = [result_row(55700, AFTER), result_row(55717, dict(AFTER, power=232))]
        context = animation_totals_context(readings, fresh, self.window())
        self.assertNotIn('power', context)
        self.assertEqual(context['skill_points'], 9)

    def test_badges_resolve_only_to_the_unique_field_that_rose_by_the_amount(self):
        facts = dict(animation_gain_badges=[dict(amount=7, row='top'), dict(amount=9, row='bottom'), dict(amount=18, row='top')])
        window = dict(self.window(), _animation_totals_context=dict(stamina=0, power=7, guts=0, wit=0, skill_points=9))
        gains = _animation_badge_gains(facts, window, {'speed', 'stamina', 'power', 'guts', 'wit', 'skill_points'})
        # +18 has no confirming total (speed was occluded) but every other
        # top-row card is confirmed and excludes it, so elimination owns it.
        self.assertEqual(gains, dict(power=7, skill_points=9, speed=18))

    def test_badge_owned_by_elimination_when_the_only_unread_card_remains(self):
        # The speed total was cursor-occluded (no delta); stamina and power
        # are confirmed and neither rose by 18, so the +18 badge is speed's.
        facts = dict(animation_gain_badges=[dict(amount=18, row='top'), dict(amount=7, row='top')])
        window = dict(self.window(), _animation_totals_context=dict(stamina=0, power=7, guts=0, wit=0, skill_points=9))
        gains = _animation_badge_gains(facts, window, {'speed', 'stamina', 'power', 'guts', 'wit', 'skill_points'})
        self.assertEqual(gains, dict(speed=18, power=7))

    def test_elimination_needs_every_other_card_confirmed(self):
        facts = dict(animation_gain_badges=[dict(amount=18, row='top')])
        window = dict(self.window(), _animation_totals_context=dict(power=7))
        self.assertEqual(_animation_badge_gains(facts, window, {'speed', 'stamina', 'power'}), {})

    def test_two_fields_with_the_same_delta_leave_the_badge_unresolved(self):
        facts = dict(animation_gain_badges=[dict(amount=7, row='top')])
        window = dict(self.window(), _animation_totals_context=dict(stamina=7, power=7))
        self.assertEqual(_animation_badge_gains(facts, window, {'stamina', 'power'}), {})


class SkippedBannerPlanningTests(unittest.TestCase):
    def test_success_banner_without_totals_still_owns_a_reread(self):
        event = dict(training_option='wit', first_seen_ms=722000, last_seen_ms=722000)
        rows = [result_row(722000, dict(skill_points=None), option='wit', outcome='success')]
        self.assertEqual(len(_committed_result_rows(rows, event)), 1)
        rows = [result_row(722000, dict(skill_points=None), option='wit', outcome=None)]
        self.assertEqual(_committed_result_rows(rows, event), [])

    def test_single_frame_row_gains_the_event_declined_still_schedule_a_reread(self):
        from tracen_replay.training_gain_recovery import _missing_result_fields
        row = result_row(675250, AFTER, outcome='success')
        row['facts']['training_gains'] = dict(speed=2, power=1)
        self.assertEqual(_missing_result_fields([row], dict(deltas={})), set(_missing_result_fields([row], dict(deltas={}))))
        self.assertTrue(_missing_result_fields([row], dict(deltas={})))
        self.assertEqual(_missing_result_fields([row], dict(deltas=dict(speed=2))), set())

    def test_state_derived_fills_still_owe_the_reread(self):
        from tracen_replay.training_gain_recovery import _missing_result_fields
        row = dict(source_timestamp_ms=1000, screen='training_result', facts=dict(training_outcome='success', result_values=dict(speed=334)))
        # Every accepted amount came from the preview/state fill: no badge was read.
        derived = dict(deltas=dict(speed=20, power=7, skill_points=11), result_state_derived_fields=['speed', 'power', 'skill_points'])
        self.assertTrue(_missing_result_fields([row], derived))
        # One directly read badge satisfies the committed-result rule as before.
        mixed = dict(deltas=dict(speed=20, power=7), result_state_derived_fields=['power'])
        self.assertEqual(_missing_result_fields([row], mixed), set())

    def test_unresolved_outcome_still_owns_a_reread_but_failure_does_not(self):
        event = dict(training_option='speed', first_seen_ms=55750, last_seen_ms=56000)
        rows = [result_row(55750, AFTER, outcome=None), result_row(56000, AFTER, outcome='unknown')]
        self.assertEqual(len(_committed_result_rows(rows, event)), 2)
        rows.append(result_row(56000, AFTER, outcome='failure'))
        self.assertEqual(_committed_result_rows(rows, event), [])


if __name__ == '__main__':
    unittest.main()
