import unittest

from tracen_replay.calendar_coverage import audit
from tracen_replay.transactions import reconstruct


def row(time, *, title=None, text='', effect=False, next_date=False):
    lines = []
    if title:
        lines.append(dict(text=title, confidence=99, box=[300, 205, 600, 235]))
    if text:
        lines.append(dict(text=text, confidence=99, box=[300, 350, 700, 390]))
    effects = [dict(kind='energy_change', amount=20, raw_text='Energy recovered by 20.')]
    if effect:
        lines.append(dict(text='Energy recovered by 20.', confidence=99, box=[300, 800, 700, 830]))
    return dict(source_timestamp_ms=time, evidence=f'{time}.png',
                screen='event_outcome' if effect else 'unknown', facts={},
                stats=dict(calendar_text='Senior Year Early May' if next_date else 'Senior Year Late Apr'),
                context_title=title, effects=effects if effect else [], ocr=dict(neural=lines))


def sequence():
    return [row(0), row(250), row(500, text='Visit the infirmary? This will take up the entire turn.'),
            row(750, text='Visit the infirmary? This will take up the entire turn.'),
            row(1000, title='At the Infirmary'), row(1250, title='At the Infirmary', effect=True),
            row(1500, title='At the Infirmary', effect=True),
            row(1750, next_date=True), row(2000, next_date=True)]


class InfirmaryIntegrationTests(unittest.TestCase):
    def test_recovery_remains_one_effect_and_closes_the_correct_dated_turn(self):
        rows = sequence()
        tracking = reconstruct(rows)
        self.assertEqual([a['kind'] for a in tracking['turn_action_receipts']], ['infirmary'])
        self.assertEqual(tracking['turn_action_receipts'][0]['source_timestamp_ms'], 1250)
        self.assertIsNone(tracking['turn_action_receipts'][0]['click_timestamp_ms'])
        effects = [effect for event in tracking['events'] for effect in event.get('effects', [])]
        self.assertEqual(effects, [dict(kind='energy_change', amount=20, raw_text='Energy recovered by 20.')])
        calendar = audit(rows, tracking['turn_action_receipts'])
        self.assertEqual(calendar['windows'][0]['status'], 'one_action')


if __name__ == '__main__':
    unittest.main()
