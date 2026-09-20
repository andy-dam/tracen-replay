"""A repeated raw state the checkpoints on both sides contradict is a cut value, not a state."""
import unittest

from tracen_replay.turn_ledger import _transient_against_checkpoints


def checkpoint(first, last, **values):
    return dict(first_seen_ms=first, last_seen_ms=last, values=dict(dict(speed=713, stamina=165, power=521, guts=273, wit=828, skill_points=1232), **values))


class LedgerTransientStateTests(unittest.TestCase):
    def test_a_value_far_from_agreeing_neighbours_is_transient(self):
        checkpoints = [checkpoint(1000, 1500), checkpoint(3000, 3500, wit=876)]
        # 876 read as 76 for nine frames under the cursor.
        self.assertTrue(_transient_against_checkpoints(dict(observed_at_ms=2000, values=dict(checkpoint(0, 0)['values'], wit=76)), checkpoints))
        # A plausible value between them, or no neighbour on one side, is left alone.
        self.assertFalse(_transient_against_checkpoints(dict(observed_at_ms=2000, values=dict(checkpoint(0, 0)['values'], wit=850)), checkpoints))
        self.assertFalse(_transient_against_checkpoints(dict(observed_at_ms=2000, values=dict(checkpoint(0, 0)['values'], wit=76)), checkpoints[:1]))
        # Neighbours that disagree with each other decide nothing.
        self.assertFalse(_transient_against_checkpoints(dict(observed_at_ms=2000, values=dict(checkpoint(0, 0)['values'], wit=76)),
                                                        [checkpoint(1000, 1500), checkpoint(3000, 3500, wit=1100)]))

    def test_no_opening_fallback_takes_the_cut_value(self):
        # Between two checkpoints at 828 and 879 the Late Nov hub read wit as
        # 76 on every frame (876 under the cursor): the repeated state, the
        # corroborated snapshot and the single-frame projection all carry it,
        # and none may open the turn with it.
        from tests.test_turn_ledger import report
        from tracen_replay.turn_ledger import build
        from tracen_replay.reconcile import FIELDS

        def hub(time, date, wit):
            values = dict(speed=725, stamina=165, power=521, guts=273, wit=wit, skill_points=1232)
            return dict(source_timestamp_ms=time, evidence=f'{time}.png', screen='unknown',
                        stats=dict(calendar_text=date, turns_remaining_to_goal=None, values=values), facts={}, effects=[])

        source = report()
        data = source['gameplay_tracking']
        data['readings'] = ([hub(t, 'Classic Year Early Nov', 828) for t in (1000, 1250, 1500, 1750)]
                            + [hub(t, 'Classic Year Late Nov', 76) for t in (3000, 3250, 3500, 3750)]
                            + [hub(t, 'Classic Year Early Dec', 879) for t in (6000, 6250, 6500, 6750)])
        data['checkpoints'] = [checkpoint(1000, 1750), dict(checkpoint(6000, 6750, wit=879), id='checkpoint-002')]
        data['checkpoints'][0]['id'] = 'checkpoint-001'
        for c in data['checkpoints']:
            c['evidence'] = f"{c['first_seen_ms']}.png"
            c['values'] = dict(c['values'], speed=725, guts=273, skill_points=1232)
        source['source']['duration_ms'] = 8000
        ledger = build(source)
        late = next(t for t in ledger['turns'] if t['label'] == 'Classic Year Late Nov')
        opening = late['states']['stats'].get('opening')
        self.assertTrue(opening is None or opening['values'].get('wit') is None, opening)
        for projection in ledger.get('opening_endpoint_projection', {}).get('rejected', []):
            if projection.get('owner_turn_id') == late['id'] and projection.get('channel') == 'stats':
                self.assertEqual(projection['reason'], 'transient_against_checkpoints')


if __name__ == '__main__':
    unittest.main()
