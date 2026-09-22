import copy
import unittest

from tracen_replay.vision import parse
from tracen_replay.transactions import races


class RaceCourseConditionTests(unittest.TestCase):
    def raw(self, label='Heavy'):
        return dict(header='', regions={}, current_grid=False, result_grid=False,
                    lines=[dict(text='Fans 1,200 (+200)', confidence=99, box=[270,700,700,730]),
                           dict(text='Example Turf 1800m (Mile) Right / Outer', confidence=99,
                                box=[276,463,649,490]),
                           dict(text=label, confidence=99, box=[723,461,787,494])])

    def row(self, time, label='Heavy'):
        result = parse(self.raw(label))
        result.update(source_timestamp_ms=time, evidence=f'{time}.png')
        return result

    def test_supported_visible_labels_require_course_context(self):
        for label in ('Heavy', 'Good', 'Firm'):
            self.assertEqual(parse(self.raw(label))['facts']['course_condition'], label.lower())
        raw = self.raw()
        raw['lines'].pop(1)
        self.assertIsNone(parse(raw)['facts']['course_condition'])

    def test_weak_wrong_position_partial_and_duplicate_labels_abstain(self):
        for replacement in ({'confidence':96.9}, {'box':[300,700,365,730]},
                            {'text':'Heav'}, {'text':'Great'}):
            raw = self.raw()
            raw['lines'][-1].update(replacement)
            self.assertIsNone(parse(raw)['facts']['course_condition'])
        raw = self.raw()
        raw['lines'].append(copy.deepcopy(raw['lines'][-1]))
        self.assertIsNone(parse(raw)['facts']['course_condition'])

    def test_missing_condition_does_not_conflict_with_known_course(self):
        rows = [self.row(0), self.row(250, 'unreadable')]
        result = races(rows)[0]
        self.assertEqual(result['course']['condition'], 'heavy')
        self.assertEqual(result['course']['distance_m'], 1800)
        self.assertEqual(result['conflicting_readings'], {})

    def test_conflicting_conditions_keep_other_course_fields(self):
        result = races([self.row(0), self.row(250,'Firm')])[0]
        self.assertIsNone(result['course']['condition'])
        self.assertEqual(result['course']['distance_m'],1800)
        self.assertEqual(result['conflicting_readings']['course.condition'],['heavy','firm'])
