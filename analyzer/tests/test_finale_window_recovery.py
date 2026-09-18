"""A finale race window is identified by its phase, so its one readable frame can open it."""
import copy
import unittest

from tests.test_boundary_state_recovery import (
    STATS, attach_state_proof, missing_states, report_for, row,
)
from tracen_replay.boundary_state_recovery import (
    apply_opening_endpoint_projections,
    candidate_windows,
    promote,
    promote_existing_endpoints,
    turn_boundary_identity,
)


def finale_turn(**overrides):
    turn = dict(id='turn-074', label='Finale Underway · URA Finale Finals', phase='Finale Underway',
                calendar_value=None, window_kind='phase_race_turn', boundary_basis='finale_race_advance',
                scheduled_race='URA Finale Finals', start_ms=100, end_ms=400, states=missing_states())
    turn.update(overrides)
    return turn


class FinaleWindowRecoveryTests(unittest.TestCase):
    def test_a_finale_race_window_is_identified_by_its_phase(self):
        self.assertEqual(turn_boundary_identity(finale_turn()), ('Finale Underway', 'any_countdown'))
        # A numbered phase turn keeps its countdown identity, and an
        # unnumbered phase that is not a race window still has none.
        self.assertEqual(turn_boundary_identity(finale_turn(calendar_value=1)), ('Finale Underway', 1))
        self.assertIsNone(turn_boundary_identity(finale_turn(window_kind='unresolved_phase')))
        self.assertIsNone(turn_boundary_identity(dict(id='pre', phase='Junior Year Pre-Debut', calendar_value=None,
                                                      window_kind='unresolved_phase')))

    def test_the_one_readable_frame_inside_the_window_is_its_candidate(self):
        turn = finale_turn()
        report = report_for(turn)
        readings = [
            # A dated panel and another phase's panel inside the window prove nothing for it.
            row(140, calendar='Classic Year Late Mar', stats=STATS),
            row(150, calendar='Junior Year Pre-Debut', stats=STATS),
            row(160, calendar='Finale Underway', stats=STATS),
        ]
        windows = candidate_windows(report, readings)
        self.assertEqual(len(windows), 1)
        channel = windows[0]['channels'][0]
        self.assertEqual((channel['channel'], channel['candidate_source_timestamp_ms']), ('stats', 160))
        self.assertEqual(channel['candidate_boundary_identity'], ['Finale Underway', 1])
        self.assertEqual(windows[0]['boundary_identity'], ['Finale Underway', 'any_countdown'])
        self.assertEqual(windows[0]['ownership_basis'], 'same_phase_inside_finale_race_window_before_action')
        # A frame that shows the phase without a readable countdown is still the window's own.
        unnumbered = copy.deepcopy(readings[2])
        del unnumbered['stats']['turns_remaining_to_goal']
        self.assertEqual(len(candidate_windows(report, readings[:2] + [unnumbered])), 1)

    def test_the_frame_opens_the_turn_with_its_accepted_source_proof(self):
        turn = finale_turn()
        report = report_for(turn)
        readings = [row(160, calendar='Finale Underway', stats=STATS)]
        attach_state_proof(report, readings, 0, 'stats')

        projections = promote_existing_endpoints(report, readings)
        self.assertEqual([p['channel'] for p in projections], ['stats'])
        ledger, accepted, rejected = apply_opening_endpoint_projections(
            report['turn_ledger'], projections, source_sha256='source')

        self.assertEqual(rejected, [])
        self.assertEqual(accepted[0]['ownership_basis'], 'same_phase_inside_finale_race_window_before_action')
        state = ledger['turns'][0]['states']['stats']
        self.assertEqual(state['opening']['values'], STATS)
        self.assertEqual(state['opening']['basis'], 'source_bound_single_frame_before_action')
        self.assertEqual(state['opening_status'], 'observed')
        # Without the producer's proof the single frame still opens nothing.
        self.assertEqual(promote_existing_endpoints(report_for(finale_turn()), readings), [])

    def test_a_probe_row_showing_the_phase_is_promoted_whatever_its_countdown(self):
        turn = finale_turn()
        readings = [row(160, calendar='Finale Underway', stats=STATS)]
        windows = candidate_windows(report_for(turn), readings)
        fresh = [row(165, calendar='Finale Underway', stats=STATS),
                 row(170, calendar='Classic Year Late Mar', stats=STATS)]
        promoted = promote(readings, fresh, windows)
        self.assertEqual([r['source_timestamp_ms'] for r in promoted], [160, 165])


if __name__ == '__main__':
    unittest.main()
