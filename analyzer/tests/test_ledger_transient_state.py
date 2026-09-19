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


if __name__ == '__main__':
    unittest.main()
