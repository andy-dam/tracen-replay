import unittest

from tracen_replay.concert_bonus_info import BASIS, concert_bonus_events, read_concert_bonus


def line(text, box, confidence=98.0):
    return dict(text=text, box=list(box), confidence=confidence)


INFO_LINES = [
    line('Concert Bonus Changes', (467, 258, 639, 278)),
    line('Bonuses will update after the concert.', (372, 285, 732, 314)),
    line('Friendship Training', (283, 327, 442, 356)), line('Effectiveness', (307, 348, 420, 374)),
    line('Specialty Priority', (483, 339, 622, 366)),
    line('Support Chain', (680, 329, 801, 355)), line('Event Frequency', (673, 348, 811, 378)),
    line('+ 10%+ 15%', (275, 384, 451, 414), 96), line('+10+15', (483, 384, 622, 415)), line('Lvl O', (708, 382, 776, 418), 90),
]


def reading(time, screen, lines=(), facts=None):
    return dict(source_timestamp_ms=time, screen=screen, evidence=f'{screen}-{time}.png', facts=facts or {}, ocr=dict(neural=list(lines)))


class ReadConcertBonusTests(unittest.TestCase):
    def test_current_and_next_values_are_read_per_column(self):
        self.assertEqual(read_concert_bonus(INFO_LINES), {
            'friendship_training_effectiveness': dict(current=10, next=15),
            'specialty_priority': dict(current=10, next=15),
            'support_chain_event_frequency': dict(current=0, next=None),
        })

    def test_block_header_is_required_and_levels_can_change(self):
        self.assertEqual(read_concert_bonus(INFO_LINES[2:]), {})
        lines = INFO_LINES[:-1] + [line('Lvl 0 Lvl 1', (650, 375, 840, 430))]
        self.assertEqual(read_concert_bonus(lines)['support_chain_event_frequency'], dict(current=0, next=1))


class ConcertBonusEventTests(unittest.TestCase):
    def test_a_repeated_info_block_and_the_update_screen_yield_applied_changes(self):
        readings = [reading(661750, 'concert_info', INFO_LINES), reading(662000, 'concert_info', INFO_LINES),
                    reading(700000, 'unknown'), reading(715250, 'concert_bonus_update')]
        events = concert_bonus_events(readings)
        self.assertEqual(len(events), 1)
        event = events[0]
        self.assertEqual(event['first_seen_ms'], 715250)
        self.assertEqual([(e['field'], e['from'], e['to'], e['amount'], e['basis']) for e in event['effects']],
                         [('friendship_training_effectiveness', 10, 15, 5, BASIS), ('specialty_priority', 10, 15, 5, BASIS)])
        self.assertEqual(event['effects'][0]['concert_info_timestamps_ms'], [661750, 662000])

    def test_single_frame_info_or_no_pending_change_yields_nothing(self):
        readings = [reading(661750, 'concert_info', INFO_LINES), reading(715250, 'concert_bonus_update')]
        self.assertEqual(concert_bonus_events(readings), [])
        steady = [l for l in INFO_LINES if not l['text'].startswith('+')] + [line('+ 15%', (275, 384, 451, 414)), line('+15', (483, 384, 622, 415))]
        readings = [reading(661750, 'concert_info', steady), reading(662000, 'concert_info', steady), reading(715250, 'concert_bonus_update')]
        self.assertEqual(concert_bonus_events(readings), [])

    def test_parsed_facts_are_preferred_over_raw_lines(self):
        facts = dict(concert_bonus={'specialty_priority': dict(current=5, next=10)})
        readings = [reading(1000, 'concert_info', facts=facts), reading(1250, 'concert_info', facts=facts), reading(5000, 'concert_bonus_update')]
        events = concert_bonus_events(readings)
        self.assertEqual([(e['field'], e['amount']) for e in events[0]['effects']], [('specialty_priority', 5)])


if __name__ == '__main__':
    unittest.main()
