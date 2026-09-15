"""A training whose animation banner was read but whose result card never classified."""
import unittest

from tracen_replay.transactions import training_events, training_actions


def banner(time, name='Breaststroke', option='Stamina', screen='unknown'):
    proof = dict(heading=dict(text=f'{option} Lvl 1', confidence=100, box=[230, 169, 318, 193]),
                 name=dict(text=name, confidence=100, box=[226, 201, 296, 228]), basis='same_frame_training_heading_and_name')
    return dict(source_timestamp_ms=time, evidence=f'{time}.png', screen=screen, stats=dict(values=None),
                facts=dict(training_name=name, training_level=1, training_identity_evidence=proof))


def screen(time, name, title=None):
    return dict(source_timestamp_ms=time, evidence=f'{time}.png', screen=name, context_title=title, effects=[], facts={}, stats=dict(values=None))


def result(time, option=None):
    return dict(source_timestamp_ms=time, evidence=f'{time}.png', screen='training_result', training_option=option,
                stats=dict(values=None), facts=dict(training_outcome='success'))


class BannerOnlyTrainingTests(unittest.TestCase):
    def test_banner_then_result_candidate_is_the_action(self):
        rows = [banner(t) for t in range(335250, 338500, 250)] + [screen(339000, 'training_result_candidate'), screen(340250, 'event_outcome')]
        events = training_events(rows)
        self.assertEqual(len(events), 1)
        event = events[0]
        self.assertEqual((event['training_option'], event['training_name'], event['banner_only']), ('stamina', 'Breaststroke', True))
        self.assertEqual(len(event['training_name_observations']), 13)
        actions = training_actions(events)
        self.assertEqual((actions[0]['identity_basis'], actions[0]['training_option'], actions[0]['source_timestamp_ms']),
                         ('training_banner', 'stamina', 335250))

    def test_banner_then_own_after_event_is_the_action(self):
        rows = [banner(t, name='Incline', option='Guts') for t in (1000, 1250, 1500)] + [screen(2000, 'event_outcome', title='Incline')]
        self.assertEqual(training_events(rows)[0]['training_option'], 'guts')

    def test_two_frames_or_a_menu_after_it_are_not_enough(self):
        rows = [banner(1000), banner(1250), screen(2000, 'training_result_candidate')]
        self.assertEqual(training_events(rows), [])
        rows = [banner(t) for t in (1000, 1250, 1500)] + [screen(2000, 'training_preview')]
        self.assertEqual(training_events(rows), [])
        rows = [banner(t) for t in (1000, 1250, 1500)] + [screen(2000, 'event_outcome', title='Fan Letter')]
        self.assertEqual(training_events(rows), [])

    def test_a_browse_before_another_trainings_result_is_not_an_action(self):
        # The player hovers Incline (guts), then trains power: the guts heading run is a browse.
        rows = [banner(t, name='Incline', option='Guts') for t in (1000, 1250, 1500)]
        rows += [banner(t, name='Squats', option='Power') for t in (2000, 2250, 2500)]
        rows += [result(3000, option='power'), result(3250, option='power')]
        events = training_events(rows)
        self.assertEqual([(e['training_option'], e.get('banner_only', False)) for e in events], [('power', False)])
        self.assertEqual(events[0].get('training_name'), 'Squats')

    def test_a_result_of_another_option_right_after_the_run_cancels_it(self):
        rows = [banner(t, name='Incline', option='Guts') for t in (1000, 1250, 1500)] + [result(2000, option='power'), result(2250, option='power')]
        events = training_events(rows)
        self.assertEqual([(e['training_option'], e.get('training_name')) for e in events], [('power', None)])

    def test_the_heading_after_the_result_is_the_same_training(self):
        # The heading stays on screen after the result frames, then the training's own event follows.
        rows = [banner(t, name='Turf', option='Speed') for t in (143000, 143250, 143500)]
        rows += [result(144250, option='speed'), result(144500, option='speed')]
        rows += [banner(t, name='Turf', option='Speed') for t in (145000, 145250, 145500)] + [screen(145750, 'event_outcome', title='Turf')]
        events = training_events(rows)
        self.assertEqual([(e['training_option'], e.get('banner_only', False), e.get('training_name')) for e in events], [('speed', False, 'Turf')])

    def test_a_candidate_before_the_classified_result_is_the_same_training(self):
        rows = [banner(t, name='Studying', option='Wit') for t in (1000, 1250, 1500)] + [screen(1750, 'training_result_candidate')]
        rows += [result(2500, option='wit'), result(2750, option='wit')]
        events = training_events(rows)
        self.assertEqual([(e['training_option'], e.get('banner_only', False)) for e in events], [('wit', False)])

    def test_sparse_identity_frames_with_a_candidate_in_the_run(self):
        # The heading was read on one frame in four; the last of them is a result candidate.
        rows = [banner(160250, name='Incline', option='Guts'), banner(161250, name='Incline', option='Guts'),
                banner(162000, name='Incline', option='Guts', screen='training_result_candidate'),
                screen(163250, 'event_outcome')]
        rows[-1]['effects'] = [dict(kind='energy_change', amount=-18)]
        events = training_events(rows)
        self.assertEqual([(e['training_option'], e['banner_only']) for e in events], [('guts', True)])

    def test_two_frames_and_the_trainings_own_titled_event(self):
        rows = [banner(303500, name='Quiz', option='Wit'), banner(304250, name='Quiz', option='Wit'), screen(305000, 'event_outcome', title='Quiz')]
        self.assertEqual([e['training_option'] for e in training_events(rows)], ['wit'])
        rows = [banner(303500, name='Quiz', option='Wit'), banner(304250, name='Quiz', option='Wit'), screen(305000, 'event_outcome', title='Fan Letter')]
        self.assertEqual(training_events(rows), [])

    def test_the_energy_cost_receipt_completes_three_frames(self):
        rows = [banner(t, name='Incline', option='Guts') for t in (1000, 1250, 1500)] + [screen(2500, 'event_outcome')]
        rows[-1]['effects'] = [dict(kind='energy_change', amount=-20)]
        self.assertEqual([e['training_option'] for e in training_events(rows)], ['guts'])
        rows[-1]['effects'] = [dict(kind='energy_change', amount=20)]
        self.assertEqual(training_events(rows), [])

    def test_a_banner_dates_a_preview_only_training_at_its_first_frame(self):
        # The preview-only rule dates the training at the first reading after the previews; the banner is earlier.
        rows = [banner(t, name='Quiz', option='Wit') for t in (303500, 304250)] + [screen(305000, 'event_outcome', title='Quiz')]
        events = training_events(rows)
        self.assertEqual(events[0]['action_time_ms'], 303500)
        owner = dict(id='training-0055', kind='training', training_option='wit', first_seen_ms=305500, last_seen_ms=305500, deltas=dict(wit=14),
                     preview_only=True, training_name=None, evidence='305500.png', effect_coverage_verified=False)
        from tracen_replay.transactions import _banner_only_training_events
        found = _banner_only_training_events(rows, [owner])
        self.assertEqual(found, [])
        self.assertEqual((owner['first_seen_ms'], owner['action_time_ms'], owner['action_time_basis'], owner['training_name']), (305500, 303500, 'training_banner', 'Quiz'))
        self.assertEqual(training_actions([owner])[0]['source_timestamp_ms'], 303500)

    def test_a_result_seven_seconds_after_a_candidate_closed_run_is_another_training(self):
        rows = [banner(t, name='Push-Button Quiz', option='Wit') for t in (1077750, 1078750, 1079500 - 250)]
        rows.append(banner(1079500, name='Push-Button Quiz', option='Wit', screen='training_result_candidate'))
        rows += [result(1086750, option='wit'), result(1087000, option='wit')]
        events = training_events(rows)
        self.assertEqual([(e['first_seen_ms'], e.get('banner_only', False)) for e in events], [(1077750, True), (1086750, False)])

    def test_the_previewed_card_confirmed_by_the_next_panel_supplies_the_gains(self):
        before = dict(speed=573, stamina=300, power=250, guts=200, wit=720, skill_points=500)
        after = dict(before, wit=740, speed=582, skill_points=508)
        def home(time, values):
            return dict(source_timestamp_ms=time, evidence=f'{time}.png', screen='unknown', facts={}, stats=dict(values=dict(values), training_preview=False, preview_option=None))
        def preview(time):
            effects = [dict(kind='stat_change', field='wit', amount=20, phase='preview'), dict(kind='stat_change', field='speed', amount=9, phase='preview'),
                       dict(kind='stat_change', field='skill_points', amount=8, phase='preview')]
            return dict(source_timestamp_ms=time, evidence=f'{time}.png', screen='training_preview', stats=dict(values=dict(before), training_preview=True, preview_option='wit'),
                        facts=dict(preview_option='wit', preview_recovery=dict(effects=effects)))
        rows = [home(1070000, before), home(1070250, before), preview(1072750), preview(1073000), preview(1073250)]
        rows += [banner(t, name='Push-Button Quiz', option='Wit') for t in (1077750, 1078000, 1078250)]
        rows.append(banner(1079500, name='Push-Button Quiz', option='Wit', screen='training_result_candidate'))
        rows += [home(1081500, after), home(1081750, after)]
        events = training_events(rows)
        self.assertEqual(len(events), 1)
        # Whether the preview-only rule or the banner rule created it, the training is one event dated at the banner with the previewed gains.
        self.assertEqual((events[0]['training_option'], events[0]['action_time_ms']), ('wit', 1077750))
        self.assertEqual(training_actions(events)[0]['source_timestamp_ms'], 1077750)
        self.assertEqual(events[0]['deltas'], dict(wit=20, speed=9, skill_points=8))
        self.assertEqual(training_actions(events)[0]['identity_basis'], 'observed_gains')

    def test_a_preview_only_training_dated_seconds_after_the_banner_is_the_same_training(self):
        before = dict(speed=573, stamina=300, power=250, guts=200, wit=720, skill_points=500)
        after = dict(before, guts=213, power=256, skill_points=508, speed=579)
        def home(time, values):
            return dict(source_timestamp_ms=time, evidence=f'{time}.png', screen='unknown', facts={}, stats=dict(values=dict(values), training_preview=False, preview_option=None))
        def preview(time):
            effects = [dict(kind='stat_change', field=f, amount=a, phase='preview') for f, a in (('guts', 13), ('power', 6), ('skill_points', 8), ('speed', 6))]
            return dict(source_timestamp_ms=time, evidence=f'{time}.png', screen='training_preview', stats=dict(values=dict(before), training_preview=True, preview_option='guts'),
                        facts=dict(preview_option='guts', preview_recovery=dict(effects=effects)))
        rows = [home(1460000, before), home(1460250, before), preview(1466000), preview(1466250), preview(1467000)]
        rows += [banner(t, name='Incline', option='Guts') for t in (1467250, 1467500, 1467750, 1468000)]
        rows += [dict(source_timestamp_ms=1469250, evidence='1469250.png', screen='event_outcome', context_title=None, effects=[dict(kind='energy_change', amount=-20)], facts={}, stats=dict(values=None))]
        rows += [home(1471500, after), home(1471750, after)]
        events = training_events(rows)
        self.assertEqual(len(events), 1, [(e['id'], e['first_seen_ms'], e.get('banner_only'), e.get('preview_only')) for e in events])
        self.assertEqual(events[0]['deltas'], dict(guts=13, power=6, skill_points=8, speed=6))
        self.assertEqual(training_actions(events)[0]['source_timestamp_ms'], 1467250)

    def test_banner_before_a_nameless_result_group_lends_its_name(self):
        rows = [banner(t, name='Incline', option='Guts') for t in range(654000, 655750, 250)] + [result(656250), result(656500)]
        events = training_events(rows)
        self.assertEqual(len(events), 1)
        event = events[0]
        self.assertFalse(event.get('banner_only'))
        self.assertEqual((event['training_option'], event['training_name'], event.get('training_option_basis')), ('guts', 'Incline', 'training_banner'))
        actions = training_actions(events)
        self.assertEqual(actions[0]['identity_basis'], 'repeated_training_name')

    def test_a_result_group_with_another_name_keeps_its_own(self):
        rows = [banner(t, name='Incline', option='Guts') for t in (1000, 1250, 1500)] + [result(2000, option='guts'), result(2250, option='guts')]
        rows[-1]['facts']['training_name'] = 'Squats'
        events = training_events(rows)
        self.assertEqual(len(events), 1)


if __name__ == '__main__':
    unittest.main()
