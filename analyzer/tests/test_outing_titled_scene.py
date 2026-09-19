"""The outing's own titled scene over the hub is not a return to the hub."""
import unittest

from tracen_replay.transactions import outing_actions


def row(time, screen, title=None, values=None, effects=()):
    r = dict(source_timestamp_ms=time, evidence=f'{time}.png', screen=screen, context_title=title, effects=list(effects), facts={},
             stats=dict(values=values))
    return r


VALUES = dict(speed=500, stamina=400, power=300, guts=250, wit=350, skill_points=100)


class OutingTitledSceneTests(unittest.TestCase):
    def test_titled_scene_frames_do_not_block(self):
        rows = [row(1743500, 'outing_confirmation'), row(1743750, 'outing_confirmation'), row(1744000, 'outing_selection'),
                row(1745500, 'unknown', title='At Rainbow Cove', values=VALUES), row(1745750, 'unknown', title='At Rainbow Cove', values=VALUES),
                row(1747750, 'event_outcome', title='At Rainbow Cove', effects=[dict(kind='energy_change', amount=45)])]
        event = dict(id='outcome-0227', kind='outcome', first_seen_ms=1747750, last_seen_ms=1750000, context_title='At Rainbow Cove',
                     evidence='1747750.png', effects=[dict(kind='energy_change', amount=45), dict(kind='stat_change', field='guts', amount=13)])
        actions = outing_actions(rows, [event])
        self.assertEqual([(a['kind'], a.get('name'), a['request_observed_at_ms']) for a in actions], [('outing', 'At Rainbow Cove', 1743750)])

    def test_a_scenario_outing_awarding_stats_is_proven_by_its_scene_and_the_next_date(self):
        # The confirmation sampled, the hub for a moment, then "Tycho's
        # Radiance" awarding guts and no energy, then the next date.
        def dated(r, text):
            r['stats']['calendar_text'] = text
            return r
        award = [dict(kind='stat_change', field='guts', amount=13)]
        rows = [row(988000, 'outing_confirmation'), row(988250, 'outing_confirmation'), row(988750, 'outing_selection'),
                dated(row(989000, 'unknown', values=VALUES), 'Classic Year Early Sep'),
                dated(row(989250, 'unknown', values=VALUES), 'Classic Year Early Sep'),
                dated(row(990250, 'unknown', title="Tycho's Radiance"), 'Classic Year Early Sep'),
                row(991250, 'unknown', title="Tycho's Radiance"),
                dated(row(993250, 'event_outcome', title="Tycho's Radiance", effects=award), 'Classic Year Early Sep'),
                dated(row(995000, 'unknown', values=VALUES), 'Classic Year Late Sep'),
                dated(row(995250, 'unknown', values=VALUES), 'Classic Year Late Sep')]
        event = dict(id='outcome-1', kind='outcome', first_seen_ms=993250, last_seen_ms=994750, context_title="Tycho's Radiance",
                     evidence='993250.png', effects=list(award))
        self.assertEqual([(a['kind'], a.get('name'), a['basis']) for a in outing_actions(rows, [event])],
                         [('outing', "Tycho's Radiance", 'outing_request_support_event_and_observed_next_date')])
        # Without the next date after the scene, or with only the menu sampled, nothing.
        self.assertEqual(outing_actions(rows[:-2], [event]), [])
        self.assertEqual(outing_actions([row(988000, 'outing_selection')] + rows[2:], [event]), [])

    def test_an_untitled_hub_between_blocks_only_a_menu_request(self):
        # The game shows the hub for a moment after the confirmation, before
        # the outing's own scene: with the confirmation sampled, the recovery
        # receipt that follows is the outing.
        rows = [row(1000, 'outing_confirmation'), row(2000, 'unknown', values=VALUES), row(2250, 'unknown', values=VALUES),
                row(5000, 'event_outcome', title='At Rainbow Cove', effects=[dict(kind='energy_change', amount=45)])]
        event = dict(id='outcome-1', kind='outcome', first_seen_ms=5000, last_seen_ms=5500, context_title='At Rainbow Cove', evidence='5000.png',
                     effects=[dict(kind='energy_change', amount=45)])
        self.assertEqual([(a['kind'], a['request_observed_at_ms']) for a in outing_actions(rows, [event])], [('outing', 1000)])
        # A menu the player may have backed out of, with the hub seen after it, is not a request.
        rows[0] = row(1000, 'outing_selection')
        self.assertEqual(outing_actions(rows, [event]), [])


if __name__ == '__main__':
    unittest.main()
