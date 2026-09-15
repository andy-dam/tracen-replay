import unittest

from tracen_replay.vision import parse


def line(text, box, confidence=99):
    return dict(text=text, confidence=confidence, box=list(box))


def attributes_card(change=True):
    lines = [
        line('G1', (300, 27, 326, 47)),
        line('Hopeful Stakes', (494, 23, 639, 54)),
        line('Attributes', (671, 114, 759, 140)),
        line('Stats', (649, 150, 693, 172)),
        line('Speed', (667, 190, 767, 225)), line('377', (835, 189, 898, 225)),
        line('Stamina', (672, 224, 770, 252)), line('269', (836, 219, 898, 256)),
        line('Power', (698, 254, 765, 284)), line('311', (836, 253, 896, 287)),
        line('Guts', (708, 288, 759, 314)), line('195', (838, 282, 899, 319)),
        line('Wit', (709, 316, 756, 345)), line('285', (835, 314, 898, 347)),
        line('Aptitude', (653, 373, 721, 394)),
        line('Turf', (775, 375, 819, 402)), line('A', (846, 376, 873, 403)),
        line('Medium', (759, 406, 836, 431)), line('A', (845, 406, 872, 432)),
        line('Pace', (771, 436, 822, 462)), line('A', (846, 435, 872, 463)),
        line('Mood', (655, 478, 703, 500)), line('GREAT', (810, 490, 882, 518)),
        line('END', (750, 565, 782, 585)), line('LATE', (798, 566, 835, 585)),
        line('PACE', (849, 566, 886, 585)), line('FRONT', (897, 565, 940, 586)),
        line('4', (851, 589, 867, 607)),
        line('Oguri Cap', (506, 695, 618, 729)),
        line('Runners', (847, 745, 918, 774)),
        line('Commentary : All eyes are on the favorite: No. 17,',
             (170, 883, 625, 913)),
    ]
    if change:
        lines.append(line('Change', (794, 613, 863, 645)))
    return lines


class RaceRunnerVisionTests(unittest.TestCase):
    def test_actual_gameplay_card_geometry_emits_runner_scope_without_owner_promotion(self):
        raw = dict(
            lines=attributes_card(), regions={}, header='',
            current_grid=False, result_grid=False,
            source_timestamp_ms=320500,
            source_frame_sha256='a' * 64,
            evidence='gameplay/part-002-frame-000323.png',
        )

        parsed = parse(raw)
        card = parsed['facts']['race_runner_attributes']

        self.assertEqual(card['event'], 'race_runner_attributes')
        self.assertTrue(card['runner_scoped'])
        self.assertEqual(card['race_name'], 'Hopeful Stakes')
        self.assertEqual(card['race_grade'], 'G1')
        self.assertEqual(card['runner_name'], 'Oguri Cap')
        self.assertEqual(card['bib_number'], 17)
        self.assertEqual(card['stats'], {
            'speed': 377, 'stamina': 269, 'power': 311, 'guts': 195, 'wit': 285,
        })
        self.assertEqual(card['aptitude'], {'turf': 'A', 'medium': 'A', 'pace': 'A'})
        self.assertEqual(card['mood'], 'GREAT')
        self.assertEqual(card['strategy_counts'], {
            'end': None, 'late': None, 'pace': 4, 'front': None,
        })
        self.assertTrue(card['selected_card_controls']['change']['visible'])
        self.assertEqual(
            card['selected_card_controls']['change']['owner_affordance_status'],
            'candidate_only',
        )
        self.assertFalse(card['owner_verified'])
        self.assertFalse(card['promotion']['allowed'])

    def test_transition_card_without_change_keeps_control_unknown(self):
        raw = dict(lines=attributes_card(change=False), regions={}, header='',
                   current_grid=False, result_grid=False)

        card = parse(raw)['facts']['race_runner_attributes']

        self.assertIsNone(card['selected_card_controls']['change']['visible'])
        self.assertFalse(card['owner_verified'])

    def test_result_rows_without_attributes_do_not_create_runner_card(self):
        raw = dict(lines=[
            line('Hopeful Stakes', (494, 23, 639, 54)),
            line('1st No. 17 Oguri Cap', (300, 425, 700, 456)),
        ], regions={}, header='', current_grid=False, result_grid=False)

        self.assertNotIn('race_runner_attributes', parse(raw)['facts'])


if __name__ == '__main__':
    unittest.main()
