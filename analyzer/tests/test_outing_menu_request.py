"""With no confirmation on any sampled frame, the Recreation menu is the outing's request."""
import unittest

from tracen_replay.transactions import outing_actions


def row(time, screen, title=None, values=None, effects=()):
    return dict(source_timestamp_ms=time, evidence=f'{time}.png', screen=screen, context_title=title,
                effects=list(effects), facts={}, stats=dict(values=values))


VALUES = dict(speed=840, stamina=241, power=474, guts=328, wit=425, skill_points=948)
RECOVERY = dict(kind='energy_change', amount=38)
COMPANION = dict(kind='friendship_change', name='Light Hello', amount=5)


def outing(first=600500, title='Repose in the Lunar Mare'):
    return dict(id='outcome-0089', kind='outcome', first_seen_ms=first, last_seen_ms=first + 1500, context_title=title,
                evidence=f'{first}.png', effects=[RECOVERY, dict(kind='max_energy_change', amount=4), COMPANION])


class OutingMenuRequestTests(unittest.TestCase):
    def test_the_menu_stands_in_when_no_confirmation_was_sampled(self):
        # The Grass Wonder career, Classic Year Early Feb: the menu on two
        # frames, the hub without its stat bar while the game connects, the
        # outing's own scene, then its receipt. No confirmation on any frame.
        rows = [row(595750, 'unknown', values=VALUES), row(596000, 'unknown', values=VALUES),
                row(596250, 'outing_selection'), row(596750, 'outing_selection'),
                row(597000, 'unknown'), row(597250, 'unknown'), row(597500, 'unknown'),
                row(598000, 'unknown', title='Repose in the Lunar Mare'),
                row(600250, 'unknown', title='Repose in the Lunar Mare'),
                row(600500, 'event_outcome', title='Repose in the Lunar Mare', effects=[RECOVERY])]
        actions = outing_actions(rows, [outing()])
        self.assertEqual(len(actions), 1)
        action = actions[0]
        self.assertEqual((action['kind'], action['companion'], action['name']),
                         ('outing', 'Light Hello', 'Repose in the Lunar Mare'))
        self.assertEqual(action['request_observed_at_ms'], 596750)
        self.assertEqual(action['basis'], 'outing_menu_followed_by_recovery_receipt_without_a_sampled_confirmation')
        self.assertEqual(action['identity_basis'], 'outing_menu_and_receipt')
        self.assertEqual(action['evidence'][:2], ['596750.png', '600500.png'])

    def test_a_sampled_confirmation_is_still_the_request(self):
        rows = [row(596250, 'outing_selection'), row(596750, 'outing_selection'), row(597000, 'outing_confirmation'),
                row(600500, 'event_outcome', title='Repose in the Lunar Mare', effects=[RECOVERY])]
        actions = outing_actions(rows, [outing()])
        self.assertEqual([(a['request_observed_at_ms'], a['basis']) for a in actions],
                         [(597000, 'outing_request_followed_by_recovery_receipt_without_another_turn_action')])
        self.assertNotIn('identity_basis', actions[0])

    def test_a_menu_the_player_backed_out_of_is_not_a_request(self):
        # The hub comes back with its stat bar, read twice within half a second.
        rows = [row(596250, 'outing_selection'), row(596750, 'outing_selection'),
                row(597000, 'unknown', values=VALUES), row(597250, 'unknown', values=VALUES),
                row(600500, 'event_outcome', title='Repose in the Lunar Mare', effects=[RECOVERY])]
        self.assertEqual(outing_actions(rows, [outing()]), [])

    def test_a_menu_that_offered_the_supporter_is_the_request_across_the_hub_before_the_scene(self):
        # The hub shows with its stat bar while the game connects, then the
        # supporter's own scene: a menu that offered that supporter was not
        # backed out of. One that offered only someone else may have been.
        def menu(time, name):
            r = row(time, 'outing_selection')
            r['ocr'] = dict(neural=[dict(text=text, confidence=97, box=[0, 0, 1, 1])
                                    for text in ('Recreation', name, 'Event Progress')])
            return r
        for offered, requests in (('Light Hello', [596750]), ('Mejiro Ramonu', [])):
            rows = [menu(596250, offered), menu(596750, offered),
                    row(597000, 'unknown', values=VALUES), row(597250, 'unknown', values=VALUES),
                    row(598000, 'unknown', title='Repose in the Lunar Mare'),
                    row(600500, 'event_outcome', title='Repose in the Lunar Mare', effects=[RECOVERY])]
            with self.subTest(offered=offered):
                self.assertEqual([a['request_observed_at_ms'] for a in outing_actions(rows, [outing()])], requests)

    def test_the_menu_does_not_stand_in_for_an_outing_without_a_recovery_line(self):
        # A support outing that awards no energy still needs the confirmation frames.
        effects = [dict(kind='mood_change', direction='up'), COMPANION]
        rows = [row(596250, 'outing_selection'), row(596750, 'outing_selection'),
                row(600500, 'event_outcome', title='Bonding', effects=effects)]
        event = dict(id='outcome-1', kind='outcome', first_seen_ms=600500, last_seen_ms=601000, context_title='Bonding',
                     evidence='600500.png', effects=effects)
        self.assertEqual(outing_actions(rows, [event]), [])


if __name__ == '__main__':
    unittest.main()
