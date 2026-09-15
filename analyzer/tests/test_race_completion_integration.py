import unittest

from tracen_replay.transactions import reconstruct


def row(time, screen='unknown', lines=(), **facts):
    return dict(source_timestamp_ms=time, evidence=f'{time}.png', screen=screen,
                facts=facts, stats={}, effects=[], ocr={'neural': list(lines)})


def badge(text='1st'):
    return dict(text=text, confidence=99, box=[430,580,680,775])


class RaceCompletionIntegrationTests(unittest.TestCase):
    def test_animation_dates_action_without_moving_reward_panel_or_fan_award(self):
        rows = [row(1000, lines=[badge()]), row(1250, lines=[badge()]),
                row(1500), row(1750),
                row(2000, 'race_result', placing=1, race_name='Example Cup', fans=3000, fans_gained=2000)]
        report = reconstruct(rows)
        race = report['races'][0]
        self.assertEqual(race['first_seen_ms'], 2000)
        self.assertEqual(race['last_seen_ms'], 2000)
        self.assertEqual(race['fans_gained'], 2000)
        self.assertEqual(race['completion_first_seen_ms'], 1000)
        self.assertEqual(report['turn_action_receipts'], [dict(
            kind='race', source_timestamp_ms=1000, evidence=['1000.png','1250.png','2000.png'],
            race_id=race['id'], click_timestamp_ms=None,
            race_name='Example Cup', placing=1)])
        without = reconstruct(rows[2:])
        self.assertEqual(report['fan_accounting'], without['fan_accounting'])
        self.assertEqual(without['turn_action_receipts'][0]['source_timestamp_ms'], 2000)

    def test_animation_alone_does_not_create_a_completed_race(self):
        report = reconstruct([row(1000, lines=[badge()]), row(1250, lines=[badge()])])
        self.assertEqual(report['races'], [])
        self.assertEqual(report['turn_action_receipts'], [])

    def test_unrelated_menu_retains_detail_panel_timing(self):
        report = reconstruct([row(1000, lines=[badge()]), row(1250, lines=[badge()]),
                              row(1500, 'training_preview'),
                              row(1750, 'race_result', placing=1, fans=3000, fans_gained=2000)])
        self.assertNotIn('completion_first_seen_ms', report['races'][0])
        self.assertEqual(report['turn_action_receipts'][0]['source_timestamp_ms'], 1750)


if __name__ == '__main__':
    unittest.main()
