"""A stable-looking stat reading that contradicts both neighbours is a misread, not a checkpoint."""
import unittest

from tracen_replay.reconcile import FIELDS, stable_checkpoints


def reading(time, **values):
    base = dict(speed=713, stamina=165, power=521, guts=258, wit=828, skill_points=1219)
    base.update(values)
    return dict(source_timestamp_ms=time, evidence=f'{time}.png', values=base, calendar_text='Classic Year Late Oct')


def block(start, **values):
    return [reading(start + i * 250, **values) for i in range(3)]


class TransientCheckpointTests(unittest.TestCase):
    def test_a_hidden_digit_repeated_on_three_frames_is_dropped(self):
        rows = block(1000) + block(5000, wit=76, guts=273) + block(9000, wit=879, guts=276)
        found = stable_checkpoints(rows)
        self.assertEqual([c['values']['wit'] for c in found], [828, 879])
        self.assertEqual([c['id'] for c in found], ['checkpoint-001', 'checkpoint-002'])

    def test_a_real_step_between_agreeing_neighbours_is_kept(self):
        rows = block(1000) + block(5000, wit=848) + block(9000, wit=879)
        self.assertEqual([c['values']['wit'] for c in stable_checkpoints(rows)], [828, 848, 879])

    def test_a_large_but_consistent_change_is_kept(self):
        # The stats really did move by a lot and stayed there.
        rows = block(1000) + block(5000, wit=1000) + block(9000, wit=1010)
        self.assertEqual([c['values']['wit'] for c in stable_checkpoints(rows)], [828, 1000, 1010])

    def test_the_last_and_first_checkpoints_are_never_judged(self):
        rows = block(1000) + block(5000, wit=76)
        self.assertEqual([c['values']['wit'] for c in stable_checkpoints(rows)], [828, 76])


if __name__ == '__main__':
    unittest.main()
