import unittest

from tracen_replay.transactions import training_events


COLUMNS = dict(speed=330, stamina=425, power=520, guts=620, wit=705, skill_points=800)
BEFORE = dict(speed=196, stamina=74, power=224, guts=143, wit=182, skill_points=120)
PREVIEW = dict(speed=18, power=7, skill_points=9)
AFTER = dict(speed=214, stamina=74, power=231, guts=143, wit=182, skill_points=129)


def home(time, values):
    return dict(source_timestamp_ms=time, screen='unknown', evidence=f'home-{time}.png', stats=dict(values=dict(values)), effects=[], facts={})


def preview(time):
    lines = [dict(text=f'+{a}', box=[COLUMNS[f] - 30, 665, COLUMNS[f] + 30, 705], confidence=99.0) for f, a in PREVIEW.items()]
    return dict(source_timestamp_ms=time, screen='unknown', evidence=f'preview-{time}.png', stats=dict(values=None), effects=[], facts=dict(preview_option='speed'), ocr=dict(lines=lines))


def result(time, gains):
    return dict(source_timestamp_ms=time, screen='training_result', training_option='speed', evidence=f'result-{time}.png', stats={}, effects=[],
                facts=dict(training_outcome='success', training_gains=dict(gains), result_values=dict(AFTER)))


class PreviewSupersedesContradictedBadgeTests(unittest.TestCase):
    def test_a_totals_confirmed_preview_matching_one_badge_reading_supersedes_the_conflict(self):
        # The speed badge was read as 8 (leading digit hidden) on two frames
        # and as 18 on two others; the card preview says 18 and the totals
        # rose by 18.  Power and skill points are read cleanly.
        rows = [result(55750, dict(speed=8, power=7, skill_points=9)), result(55767, dict(speed=8, power=7, skill_points=9)),
                result(56000, dict(speed=18, power=7, skill_points=9)), result(56017, dict(speed=18, power=7, skill_points=9))]
        readings = [home(52000, BEFORE), preview(54750), preview(55000), *rows, home(60000, AFTER)]
        events = training_events(readings)
        self.assertEqual(len(events), 1)
        event = events[0]
        self.assertEqual(event['deltas'], dict(speed=18, power=7, skill_points=9))
        self.assertEqual(event['conflicting_readings'], {})
        self.assertEqual(event['conflicting_readings_superseded_by_preview_confirmation'], dict(speed=[8, 18]))
        self.assertEqual(event['direct_gain_provenance']['speed']['superseded_conflicting_readings'], [8, 18])
        self.assertIn('speed', event['result_state_derived_fields'])

    def test_a_reading_the_card_repeated_against_one_misread_frame_stays_read(self):
        # Speed read 18 on three frames and misread as 13 on one; the preview
        # and the totals confirm 18. With the 18 on two frames only, the
        # totals decide and the amount is worked out, as above.
        for times, derived in (((55750, 55800, 55850), False), ((55750, 55800), True)):
            rows = [result(t, dict(speed=18, power=7, skill_points=9)) for t in times]
            rows.append(result(56000, dict(speed=13, power=7, skill_points=9)))
            readings = [home(52000, BEFORE), preview(54750), preview(55000), *rows, home(60000, AFTER)]
            event = training_events(readings)[0]
            with self.subTest(frames=len(times)):
                self.assertEqual(event['deltas']['speed'], 18)
                self.assertEqual(event['conflicting_readings_superseded_by_preview_confirmation'], dict(speed=[13, 18]))
                self.assertEqual('speed' in event.get('result_state_derived_fields', []), derived)
                if not derived:
                    provenance = event['direct_gain_provenance']['speed']
                    self.assertEqual(provenance['basis'], 'repeated_read_confirmed_by_totals')
                    self.assertEqual(provenance['evidence'], [f'result-{t}.png' for t in times])

    def test_a_performance_award_the_card_repeated_against_one_misread_frame_stays_read(self):
        # Composure read +23 on three frames and +20 on one; the menu
        # projected +23 and the panel rose by 23.
        points = dict(dance=31, passion=38, vocal=27, visual=26, composure=20)
        rows = [result(t, dict(speed=18, power=7, skill_points=9)) for t in (55750, 55800, 55850, 56000)]
        for row, award in zip(rows, (23, 23, 20, 23)):
            row['facts']['awarded_performance_gains'] = dict(composure=award)
        before, after = home(52000, BEFORE), home(60000, AFTER)
        before['facts']['performance_points'] = dict(points)
        after['facts']['performance_points'] = dict(points, composure=43)
        previews = [preview(54750), preview(55000)]
        for row in previews:
            row['facts']['projected_performance_gains'] = dict(composure=23)
        event = training_events([before, *previews, *rows, after])[0]
        self.assertEqual(event['performance_deltas'], dict(composure=23))
        self.assertNotIn('composure', event.get('result_state_derived_fields', []))
        self.assertEqual(event['performance_reading_resolutions']['composure']['basis'], 'repeated_read_confirmed_by_totals')
        self.assertEqual(event['performance_evidence']['composure'], ['result-55750.png', 'result-55800.png', 'result-56000.png'])

    def test_a_preview_matching_no_badge_reading_leaves_the_conflict_open(self):
        rows = [result(55750, dict(speed=5, power=7, skill_points=9)), result(55767, dict(speed=5, power=7, skill_points=9)),
                result(56000, dict(speed=500, power=7, skill_points=9)), result(56017, dict(speed=500, power=7, skill_points=9))]
        readings = [home(52000, BEFORE), preview(54750), preview(55000), *rows, home(60000, AFTER)]
        event = training_events(readings)[0]
        self.assertNotIn('speed', event['deltas'])
        self.assertEqual(event['conflicting_readings'], dict(speed=[5, 500]))
        self.assertNotIn('conflicting_readings_superseded_by_preview_confirmation', event)


if __name__ == '__main__':
    unittest.main()
