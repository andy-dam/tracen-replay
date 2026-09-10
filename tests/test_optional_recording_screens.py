"""Ordinary recordings may omit panels or expose them only while scrolling."""
import copy
import unittest

from tracen_replay.inventory import summarize
from tracen_replay.transactions import concerts


def row(time, screen, facts=None):
    return dict(source_timestamp_ms=time, evidence=f'frame-{time}.png',
                screen=screen, facts=facts or {}, effects=[])


class OptionalRecordingScreensTests(unittest.TestCase):
    def concert_rows(self):
        return [row(1000, 'concert_confirmation'),
                row(2000, 'concert_result_candidate'),
                row(3000, 'concert_bonus_update'), row(4000, 'training_hub')]

    def test_absent_final_summary_does_not_invent_empty_complete_inventory(self):
        rows = self.concert_rows()
        before = copy.deepcopy(rows)
        result = summarize(rows)
        self.assertEqual(result['summary_frames'], [])
        self.assertEqual(result['observed_owned_cards'], [])
        self.assertFalse(result['complete'])
        self.assertTrue(result['unresolved'])
        self.assertEqual(rows, before)

    def test_concert_update_survives_without_optional_current_bonus_panel(self):
        result = concerts(self.concert_rows(), [], [])
        self.assertEqual(len(result), 1)
        concert = result[0]
        self.assertTrue(concert['bonus_activation_verified'])
        self.assertEqual(concert['bonus_update_receipt']['evidence'], ['frame-3000.png'])
        snapshot = concert['later_active_bonus_snapshot']
        self.assertEqual(snapshot['values'], {})
        self.assertEqual(set(snapshot['unresolved_fields'].values()), {'not_observed'})
        self.assertFalse(snapshot['complete'])

    def test_brief_single_panel_observation_is_retained_without_confirmation(self):
        rows = self.concert_rows()
        rows.insert(3, row(3500, 'concert_info', dict(current_concert_bonuses={'specialty_priority': 10})))
        snapshot = concerts(rows, [], [])[0]['later_active_bonus_snapshot']
        self.assertEqual(snapshot['values'], {})
        self.assertEqual(snapshot['observations']['specialty_priority'][0]['value'], 10)
        self.assertEqual(snapshot['unresolved_fields']['specialty_priority'], 'insufficient_distinct_timestamps')

    def test_readable_card_can_repeat_while_moving_without_a_pause(self):
        # This tests aggregation of recognized cards. It does not establish
        # detection of blurred or clipped text during arbitrary scrolling.
        rows = []
        for time, slot in ((1000, [4, 0]), (1100, [2, 0])):
            card = dict(name_text='Example Skill', slot=slot, text_evidence=[
                dict(text='Example Skill', confidence=99, box=[315, 510+slot[0]*63, 490, 535+slot[0]*63])])
            rows.append(row(time, 'career_summary', dict(visible_owned_skill_cards=[card])))
        result = summarize(rows)
        self.assertEqual([c['name_text'] for c in result['observed_owned_cards']], ['Example Skill'])
        self.assertEqual([o['slot'] for o in result['observed_owned_cards'][0]['observations']], [[4, 0], [2, 0]])
        self.assertFalse(result['complete'])


if __name__ == '__main__':
    unittest.main()
