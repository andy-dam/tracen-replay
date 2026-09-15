import unittest

from tracen_replay.preview_confirmed_gains import BASIS, BOUNDED_BASIS, preview_amounts, preview_confirmed_gains


def home(time, values, effects=()):
    return dict(source_timestamp_ms=time, screen='unknown', evidence=f'home-{time}.png',
                stats=dict(values=dict(values)), effects=list(effects), facts={})


def preview(time, option, amounts):
    lines = []
    columns = dict(speed=330, stamina=425, power=520, guts=620, wit=705, skill_points=800)
    for field, amount in amounts.items():
        lines.append(dict(text=f'+{amount}', box=[columns[field] - 30, 665, columns[field] + 30, 705], confidence=99.0))
    return dict(source_timestamp_ms=time, screen='unknown', evidence=f'preview-{time}.png', stats=dict(values=None),
                effects=[], facts=dict(preview_option=option), ocr=dict(lines=lines))


def result(time, totals=None, outcome=None, option='speed'):
    facts = dict(training_outcome=outcome)
    if totals is not None:
        facts['result_values'] = dict(totals)
    return dict(source_timestamp_ms=time, screen='training_result', training_option=option,
                evidence=f'result-{time}.png', stats={}, effects=[], facts=facts)


BEFORE = dict(speed=196, stamina=74, power=224, guts=143, wit=182, skill_points=120)
PREVIEW = dict(speed=18, power=7, skill_points=9)


class PreviewAmountTests(unittest.TestCase):
    def test_signed_badges_on_the_preview_row_map_to_fields_by_column(self):
        self.assertEqual(preview_amounts(preview(1000, 'speed', PREVIEW)), PREVIEW)

    def test_recovery_effects_are_used_when_present(self):
        row = preview(1000, 'speed', {})
        row['facts']['preview_recovery'] = dict(effects=[dict(kind='stat_change', field='speed', amount=18, phase='preview')])
        self.assertEqual(preview_amounts(row), dict(speed=18))


class PreviewConfirmedGainTests(unittest.TestCase):
    def group(self, rows):
        return dict(option='speed', first_seen_ms=rows[0]['source_timestamp_ms'], last_seen_ms=rows[-1]['source_timestamp_ms'], rows=rows)

    def test_result_panel_totals_confirm_the_preview_including_unreadable_fields(self):
        # Speed is hidden by the cursor on the result panel; the other cards confirm.
        after = dict(stamina=74, power=231, guts=143, wit=182, skill_points=129)
        rows = [result(55750, after, outcome='success'), result(56000, after)]
        readings = [home(52000, BEFORE), preview(54750, 'speed', PREVIEW), preview(55000, 'speed', PREVIEW), preview(55250, 'speed', PREVIEW), *rows]
        gains = preview_confirmed_gains(readings, self.group(rows), {})
        self.assertEqual({f: p['value'] for f, p in gains.items()}, PREVIEW)
        self.assertEqual(gains['speed']['basis'], BASIS)
        self.assertEqual(sorted(gains['speed']['confirmed_fields']), ['power', 'skill_points'])
        self.assertEqual(gains['speed']['unreadable_fields'], ['speed'])

    def test_next_home_panel_confirms_when_the_result_panel_was_skipped(self):
        rows = [result(722000, dict(skill_points=None), outcome='success')]
        readings = [home(715000, BEFORE), preview(720500, 'speed', PREVIEW), preview(720750, 'speed', PREVIEW), *rows,
                    home(724000, dict(speed=214, stamina=74, power=231, guts=143, wit=182, skill_points=129))]
        gains = preview_confirmed_gains(readings, self.group(rows), {})
        self.assertEqual({f: p['value'] for f, p in gains.items()}, PREVIEW)
        self.assertEqual(gains['speed']['after']['source'], 'home_panel')

    def test_receipts_between_the_panels_are_accounted_for(self):
        rows = [result(55750, dict(skill_points=None), outcome='success')]
        event = home(59000, {}, effects=[dict(kind='stat_change', field='wit', amount=10)])
        event['stats'] = dict(values=None)
        readings = [home(52000, BEFORE), preview(54750, 'speed', PREVIEW), preview(55000, 'speed', PREVIEW), *rows, event,
                    home(60750, dict(speed=214, stamina=74, power=231, guts=143, wit=192, skill_points=129))]
        gains = preview_confirmed_gains(readings, self.group(rows), {})
        self.assertEqual({f: p['value'] for f, p in gains.items()}, PREVIEW)
        self.assertEqual(gains['speed']['intervening_receipts'], dict(wit=10))

    def test_a_contradicting_total_rejects_the_preview(self):
        # A field the preview never touched changed: nothing is accepted.
        after = dict(stamina=80, power=231, guts=143, wit=182, skill_points=129)
        rows = [result(55750, after, outcome='success'), result(56000, after)]
        readings = [home(52000, BEFORE), preview(54750, 'speed', PREVIEW), preview(55000, 'speed', PREVIEW), *rows]
        self.assertEqual(preview_confirmed_gains(readings, self.group(rows), {}), {})
        # A previewed field that rose by less than its preview is a contradiction too.
        after = dict(stamina=74, power=228, guts=143, wit=182, skill_points=129)
        rows = [result(55750, after, outcome='success'), result(56000, after)]
        readings = [home(52000, BEFORE), preview(54750, 'speed', PREVIEW), preview(55000, 'speed', PREVIEW), *rows]
        self.assertEqual(preview_confirmed_gains(readings, self.group(rows), {}), {})

    def test_bonus_above_the_preview_yields_bounded_state_derived_gains(self):
        # Concert bonus: previewed fields rose beyond the preview, others balance.
        after = dict(speed=225, stamina=74, power=232, guts=143, wit=182, skill_points=131)
        rows = [result(55750, dict(skill_points=None), outcome='success')]
        readings = [home(52000, BEFORE), preview(54750, 'speed', PREVIEW), preview(55000, 'speed', PREVIEW), *rows, home(60000, after)]
        gains = preview_confirmed_gains(readings, self.group(rows), {})
        self.assertEqual({f: p['value'] for f, p in gains.items()}, dict(speed=29, power=8, skill_points=11))
        self.assertEqual({f: p['basis'] for f, p in gains.items()}, {f: BOUNDED_BASIS for f in ('speed', 'power', 'skill_points')})
        self.assertEqual({f: p['bonus_excess'] for f, p in gains.items()}, dict(speed=11, power=1, skill_points=2))
        self.assertEqual(gains['speed']['preview_amount'], 18)

    def test_failure_single_frame_preview_or_no_confirming_field_rejects(self):
        after = dict(stamina=74, power=231, guts=143, wit=182, skill_points=129)
        rows = [result(55750, after, outcome='failure'), result(56000, after)]
        readings = [home(52000, BEFORE), preview(54750, 'speed', PREVIEW), preview(55000, 'speed', PREVIEW), *rows]
        self.assertEqual(preview_confirmed_gains(readings, self.group(rows), {}), {})
        # One preview frame is enough when the group's own result rows prove
        # the committed card; a preview-only group needs two frames.
        rows = [result(55750, after, outcome='success'), result(56000, after)]
        readings = [home(52000, BEFORE), preview(55000, 'speed', PREVIEW), *rows]
        self.assertEqual({f: p['value'] for f, p in preview_confirmed_gains(readings, self.group(rows), {}).items()}, PREVIEW)
        readings = [home(52000, BEFORE), preview(55000, 'speed', PREVIEW), home(58000, dict(speed=214, stamina=74, power=231, guts=143, wit=182, skill_points=129))]
        self.assertEqual(preview_confirmed_gains(readings, dict(option='speed', first_seen_ms=55000, last_seen_ms=55000, rows=[], preview_rows=[readings[1]]), {}), {})
        readings = [home(52000, BEFORE), preview(54750, 'speed', PREVIEW), preview(55000, 'speed', PREVIEW), result(55750, dict(guts=143), outcome='success'), result(56000, dict(guts=143))]
        self.assertEqual(preview_confirmed_gains(readings, self.group(readings[-2:]), {}), {})

    def test_fields_already_observed_are_left_alone_and_must_agree(self):
        after = dict(stamina=74, power=231, guts=143, wit=182, skill_points=129)
        rows = [result(55750, after, outcome='success'), result(56000, after)]
        readings = [home(52000, BEFORE), preview(54750, 'speed', PREVIEW), preview(55000, 'speed', PREVIEW), *rows]
        gains = preview_confirmed_gains(readings, self.group(rows), dict(power=7))
        self.assertEqual(sorted(gains), ['skill_points', 'speed'])
        self.assertEqual(preview_confirmed_gains(readings, self.group(rows), dict(power=8)), {})


if __name__ == '__main__':
    unittest.main()


from tracen_replay.preview_confirmed_gains import preview_only_training_groups


def panel_row(time, screen, points, projected=None):
    r = dict(source_timestamp_ms=time, screen=screen, evidence=f'{screen}-{time}.png', stats=dict(values=None), effects=[],
             facts=dict(performance_points=dict(zip(('dance', 'passion', 'vocal', 'visual', 'composure'), points))))
    if projected:
        r['facts']['preview_option'] = 'speed'
        lines = []
        for field, (band, amount) in projected.items():
            cur = r['facts']['performance_points'][field]
            lines.append(dict(text=f'{cur}+{amount}', box=[206, band[0] + 4, 316, band[1] - 4], confidence=99.9))
        r['ocr'] = dict(neural=lines)
        r['facts'].pop('performance_points')
    return r


class PerformancePreviewTests(unittest.TestCase):
    def test_projected_panel_awards_confirm_against_the_next_panel(self):
        before = (36, 45, 3, 18, 23)
        rows = [result(55750, dict(skill_points=None), outcome='success')]
        readings = [panel_row(52000, 'unknown', before),
                    panel_row(54750, 'unknown', before, projected=dict(dance=((290, 335), 26), visual=((458, 503), 26))),
                    panel_row(55000, 'unknown', before, projected=dict(dance=((290, 335), 26), visual=((458, 503), 26))),
                    *rows, panel_row(60000, 'unknown', (62, 45, 3, 44, 23))]
        for r in readings[1:3]:
            r['facts']['performance_points'] = dict(zip(('dance', 'passion', 'vocal', 'visual', 'composure'), before))
        group = dict(option='speed', first_seen_ms=55750, last_seen_ms=55750, rows=rows)
        gains = preview_confirmed_gains(readings, group, {}, channel='performance')
        self.assertEqual({f: p['value'] for f, p in gains.items()}, dict(dance=26, visual=26))
        self.assertEqual(gains['dance']['channel'], 'performance')


class PreviewOnlyTrainingTests(unittest.TestCase):
    def test_a_preview_run_without_a_result_frame_becomes_a_candidate_group(self):
        readings = [home(52000, BEFORE), preview(54750, 'speed', PREVIEW), preview(55000, 'speed', PREVIEW),
                    home(58000, dict(speed=214, stamina=74, power=231, guts=143, wit=182, skill_points=129))]
        groups = preview_only_training_groups(readings, [])
        self.assertEqual([(g['option'], g['first_seen_ms'], g['last_seen_ms']) for g in groups], [('speed', 54750, 55000)])
        gains = preview_confirmed_gains(readings, groups[0], {})
        self.assertEqual({f: p['value'] for f, p in gains.items()}, PREVIEW)

    def test_runs_followed_by_a_result_or_inside_an_event_window_are_not_candidates(self):
        readings = [home(52000, BEFORE), preview(54750, 'speed', PREVIEW), preview(55000, 'speed', PREVIEW), result(55750, dict(skill_points=None), outcome='success')]
        self.assertEqual(preview_only_training_groups(readings, []), [])
        readings = [home(52000, BEFORE), preview(54750, 'speed', PREVIEW), preview(55000, 'speed', PREVIEW)]
        self.assertEqual(preview_only_training_groups(readings, [dict(kind='training', first_seen_ms=56000, last_seen_ms=56500)]), [])

    def test_a_result_arriving_up_to_fifteen_seconds_after_the_run_still_claims_it(self):
        # The result card follows the preview after the training animation,
        # which took 5.25 s on a blind recording; a run followed by any
        # option's result before the next home panel is not a second training.
        for delay in (5250, 12700, 15000):
            readings = [home(52000, BEFORE), preview(54750, 'wit', PREVIEW), preview(55000, 'wit', PREVIEW),
                        result(55000 + delay, dict(skill_points=None), outcome='success', option='speed')]
            self.assertEqual(preview_only_training_groups(readings, []), [], delay)
        readings = [home(52000, BEFORE), preview(54750, 'wit', PREVIEW), preview(55000, 'wit', PREVIEW),
                    result(70250, dict(skill_points=None), outcome='success', option='speed')]
        groups = preview_only_training_groups(readings, [])
        self.assertEqual([(g['option'], g['first_seen_ms'], g['last_seen_ms']) for g in groups], [('wit', 54750, 55000)])

    def test_browsing_other_cards_before_the_commit_does_not_make_a_second_training(self):
        # Blind recording: wit previewed, stamina and power browsed, wit
        # committed; its result came 5.5 s after the first wit run.
        readings = [home(52000, BEFORE), preview(54750, 'wit', PREVIEW), preview(55000, 'wit', PREVIEW),
                    preview(55250, 'stamina', PREVIEW), preview(55500, 'power', PREVIEW), preview(56000, 'wit', PREVIEW),
                    result(60500, dict(skill_points=None), outcome='success', option='wit')]
        self.assertEqual(preview_only_training_groups(readings, []), [])

    def test_a_result_after_the_next_home_panel_belongs_to_a_later_action(self):
        # Blind recording at 984250: the wit card was committed but its result
        # frame was never sampled; the next turn opened 3 s later and a speed
        # training followed 12.7 s after the run.  The home panel between them
        # keeps the run as a candidate.
        after = dict(speed=214, stamina=74, power=231, guts=143, wit=182, skill_points=129)
        readings = [home(52000, BEFORE), preview(54750, 'wit', PREVIEW), preview(55000, 'wit', PREVIEW), home(58000, after),
                    result(67700, dict(skill_points=None), outcome='success', option='speed')]
        groups = preview_only_training_groups(readings, [])
        self.assertEqual([(g['option'], g['first_seen_ms'], g['last_seen_ms']) for g in groups], [('wit', 54750, 55000)])
        # The same holds when the later action is already an assembled training event.
        later = [dict(kind='training', first_seen_ms=67700, last_seen_ms=68200)]
        self.assertEqual([g['option'] for g in preview_only_training_groups(readings[:-1], later)], ['wit'])

    def test_an_earlier_training_window_claims_only_runs_within_five_seconds(self):
        readings = [home(40000, BEFORE), preview(54750, 'guts', PREVIEW), preview(55000, 'guts', PREVIEW)]
        self.assertEqual(preview_only_training_groups(readings, [dict(kind='training', first_seen_ms=49000, last_seen_ms=49750)]), [])
        groups = preview_only_training_groups(readings, [dict(kind='training', first_seen_ms=48000, last_seen_ms=48700)])
        self.assertEqual([g['option'] for g in groups], ['guts'])
