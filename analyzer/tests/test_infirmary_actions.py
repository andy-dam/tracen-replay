import copy
import unittest

from tracen_replay.gameplay import classify
from tracen_replay.infirmary_actions import reconstruct


def ocr(*texts):
    return {'neural': [{'text': text, 'confidence': 99.0} for text in texts]}


def row(time, evidence, *, screen='unknown', calendar=None, context=None,
        texts=(), effects=(), completed_action=None, turns=None,
        confidence=99.0):
    return {
        'source_timestamp_ms': time,
        'evidence': evidence,
        'screen': screen,
        'context_title': context,
        'stats': {'calendar_text': calendar, 'turns_remaining_to_goal': turns},
        'ocr': {'neural': [{'text': text, 'confidence': confidence}
                           for text in texts]},
        'effects': [dict(effect) for effect in effects],
        'completed_action': completed_action,
    }


def event(first, last, *, effects=()):
    return {
        'id': 'outcome-infirmary',
        'kind': 'outcome',
        'first_seen_ms': first,
        'last_seen_ms': last,
        'context_title': 'At the Infirmary',
        'effects': [dict(effect) for effect in effects],
        'evidence': 'result.png',
    }


def positive_sequence():
    recovery = {'kind': 'energy_change', 'amount': 20,
                'raw_text': 'Energy recovered by 20.'}
    readings = [
        row(1000, 'hub.png', calendar='Senior Year Late Apr'),
        row(1250, 'confirm-a.png', calendar='Senior Year Late Apr',
            texts=('Infirmary', 'Visit the infirmary?',
                   'This will take up the entire turn.', 'Cancel', 'OK')),
        row(1500, 'confirm-b.png', calendar='Senior Year Late Apr',
            texts=('Visit the infirmary?', 'This will take up the entire turn.',
                   'Cancel', 'OK')),
        row(1750, 'result-a.png', calendar='Senior Year Late Apr',
            context='At the Infirmary', texts=('At the Infirmary',)),
        row(2000, 'result-b.png', calendar='Senior Year Late Apr',
            context='At the Infirmary', texts=('At the Infirmary',), effects=(recovery,)),
        row(2250, 'next.png', calendar='Senior Year Early May'),
        row(2500, 'next-b.png', calendar='Senior Year Early May'),
    ]
    return readings, [event(1750, 2000, effects=(recovery,))]


def phase_sequence():
    recovery = {'kind': 'energy_change', 'amount': 20,
                'raw_text': 'Energy recovered by 20.'}
    readings = [
        row(2100, 'prior.png', calendar='Junior Year Pre-Debut', turns=5),
        row(2200, 'before-a.png', calendar='Junior Year Pre-Debut', turns=4),
        row(2250, 'before-b.png', calendar='Junior Year Pre-Debut', turns=4),
        row(2300, 'confirm-a.png', calendar='Junior Year Pre-Debut',
            texts=('Visit the infirmary?',
                   'This will take up the entire turn.', 'Cancel', 'OK')),
        row(2350, 'confirm-b.png', calendar='Junior Year Pre-Debut',
            texts=('Visit the infirmary?',
                   'This will take up the entire turn.', 'Cancel', 'OK')),
        row(2400, 'result-a.png', calendar='Junior Year Pre-Debut',
            context='At the Infirmary', texts=('At the Infirmary',), turns=4),
        row(2450, 'result-b.png', calendar='Junior Year Pre-Debut',
            context='At the Infirmary', texts=('At the Infirmary',),
            effects=(recovery,), turns=4),
        row(2500, 'after-a.png', calendar='Junior Year Pre-Debut', turns=3),
        row(2550, 'after-b.png', calendar='Junior Year Pre-Debut', turns=3),
        row(2600, 'preview.png', calendar='Junior Year Pre-Debut',
            screen='training_preview', turns=3),
        row(2650, 'later.png', calendar='Junior Year Pre-Debut', turns=2),
    ]
    return readings, [event(2400, 2450, effects=(recovery,))]


class InfirmaryActionTests(unittest.TestCase):
    def test_classifier_requires_infirmary_full_turn_prompt(self):
        self.assertEqual(classify('Visit the infirmary? This will take up the entire turn.', ''),
                         'infirmary_confirmation')
        self.assertNotEqual(classify('Visit the infirmary?', ''), 'infirmary_confirmation')

    def test_parser_screen_label_without_source_prompt_is_not_proof(self):
        readings, events = positive_sequence()
        readings[2]['screen'] = 'infirmary_confirmation'
        readings[2]['ocr'] = ocr('Cancel', 'OK')
        readings[1]['ocr'] = ocr('Cancel', 'OK')
        self.assertEqual(reconstruct(readings, events), [])

    def test_source_backed_infirmary_action_requires_result_recovery_and_date(self):
        readings, events = positive_sequence()
        original = copy.deepcopy(readings)
        actions = reconstruct(readings, events)
        self.assertEqual(len(actions), 1)
        action = actions[0]
        self.assertEqual(action['kind'], 'infirmary')
        self.assertEqual(action['source_timestamp_ms'], 1750)
        self.assertEqual(action['next_calendar'], 'Senior Year Early May')
        self.assertEqual(action['recovery_effects'][0]['amount'], 20)
        self.assertEqual(action['next_date_evidence'], ['next.png', 'next-b.png'])
        self.assertEqual([item['source_timestamp_ms']
                          for item in action['next_boundary_observations']],
                         [2250, 2500])
        self.assertEqual(readings, original)

    def test_phase_countdown_proves_undated_predebut_boundary(self):
        readings, events = phase_sequence()
        actions = reconstruct(readings, events)
        self.assertEqual(len(actions), 1)
        action = actions[0]
        self.assertEqual(action['boundary_kind'], 'phase_countdown')
        self.assertEqual(action['turns_before'], 4)
        self.assertEqual(action['turns_after'], 3)
        self.assertEqual(action['next_date_timestamp_ms'], 2500)
        self.assertEqual(action['turn_transition_evidence'],
                         ['before-a.png', 'before-b.png', 'after-a.png',
                          'after-b.png', 'preview.png'])
        self.assertEqual(
            [(item['state'], item['turns_remaining_to_goal'])
             for item in action['turn_transition_observations']],
            [('before', 4), ('before', 4), ('after', 3), ('after', 3),
             ('after', 3)])

    def test_cancel_or_competing_action_rejects_candidate(self):
        readings, events = positive_sequence()
        readings.insert(3, row(1625, 'cancel.png', texts=('Action cancelled.',)))
        self.assertEqual(reconstruct(readings, events), [])
        readings, events = positive_sequence()
        readings.insert(3, row(1625, 'preview.png', screen='training_preview'))
        self.assertEqual(reconstruct(readings, events), [])
        readings, events = positive_sequence()
        readings.insert(3, row(1625, 'hub.png', screen='career_hub'))
        self.assertEqual(reconstruct(readings, events), [])
        readings, events = positive_sequence()
        readings.insert(3, row(1625, 'other.png', screen='rest_confirmation'))
        self.assertEqual(reconstruct(readings, events), [])
        readings, events = positive_sequence()
        readings.insert(3, row(1625, 'other-result.png', screen='event_outcome',
                                context='Another Event', effects=({'kind': 'energy_change', 'amount': 5},)))
        self.assertEqual(reconstruct(readings, events), [])

    def test_a_story_event_after_the_result_is_not_another_action(self):
        # The visit's result, then a story event on the same date, then the next date.
        readings, events = positive_sequence()
        readings[5:5] = [row(2100, 'story-a.png', calendar='Senior Year Late Apr', context='A Quirky Correspondent?',
                             texts=('An unfamiliar woman is watching our session',)),
                         row(2150, 'story-b.png', screen='event_outcome', calendar='Senior Year Late Apr',
                             context='A Quirky Correspondent?', effects=({'kind': 'stat_change', 'field': 'speed', 'amount': 5},))]
        self.assertEqual([a['next_calendar'] for a in reconstruct(readings, events)], ['Senior Year Early May'])
        # Another action there still breaks the visit.
        readings.insert(7, row(2200, 'preview.png', screen='training_preview'))
        self.assertEqual(reconstruct(readings, events), [])

    def test_status_cancellation_is_not_the_ordinary_cancel_button(self):
        readings, events = positive_sequence()
        readings[1]['ocr'] = ocr('Visit the infirmary?',
                                 'This will take up the entire turn.',
                                 'Action cancelled.', 'Cancel', 'OK')
        readings[2]['ocr'] = ocr('Visit the infirmary?',
                                 'This will take up the entire turn.',
                                 'Action cancelled.', 'Cancel', 'OK')
        self.assertEqual(reconstruct(readings, events), [])

    def test_unrelated_recovery_does_not_become_infirmary(self):
        readings, _ = positive_sequence()
        readings[3]['context_title'] = 'A Different Event'
        readings[3]['ocr'] = ocr('A Different Event')
        readings[4]['context_title'] = 'A Different Event'
        readings[4]['ocr'] = ocr('A Different Event')
        events = [event(1750, 2000, effects=({'kind': 'energy_change', 'amount': 20},))]
        events[0]['context_title'] = 'A Different Event'
        self.assertEqual(reconstruct(readings, events), [])

    def test_candidate_only_title_and_unrelated_result_screen_do_not_count(self):
        readings, events = positive_sequence()
        events[0]['context_title'] = None
        events[0]['context_title_candidate'] = 'At the Infirmary'
        self.assertEqual(reconstruct(readings, events), [])

        readings, events = positive_sequence()
        readings[3]['context_title'] = None
        readings[3]['context_title_candidate'] = 'At the Infirmary'
        self.assertEqual(reconstruct(readings, events), [])

        readings, events = positive_sequence()
        readings[3]['screen'] = 'training_result'
        self.assertEqual(reconstruct(readings, events), [])

        readings, events = positive_sequence()
        readings.insert(4, row(1875, 'competing.png', screen='training_result',
                                context='At the Infirmary'))
        self.assertEqual(reconstruct(readings, events), [])

    def test_condition_recovery_is_explicit_evidence_too(self):
        readings, events = positive_sequence()
        recovery = {'kind': 'condition_removed', 'name': 'Practice Poor',
                    'raw_text': 'Recovered from Practice Poor.'}
        readings[4]['effects'] = [recovery]
        readings[4]['ocr'] = ocr('At the Infirmary', recovery['raw_text'])
        events[0]['effects'] = [recovery]
        actions = reconstruct(readings, events)
        self.assertEqual(len(actions), 1)
        self.assertEqual(actions[0]['recovery_effects'][0]['kind'], 'condition_removed')

    def test_wrong_event_or_date_and_missing_proof_stay_unknown(self):
        readings, events = positive_sequence()
        events[0]['first_seen_ms'] = 900
        events[0]['last_seen_ms'] = 1100
        self.assertEqual(reconstruct(readings, events), [])

        readings, events = positive_sequence()
        readings[-2]['stats']['calendar_text'] = 'Senior Year Late Apr'
        self.assertEqual(reconstruct(readings, events), [])
        readings, events = positive_sequence()
        readings[-2]['stats']['calendar_text'] = 'Senior Year Late Jun'
        self.assertEqual(reconstruct(readings, events), [])
        readings, events = positive_sequence()
        readings[-2]['stats']['calendar_text'] = 'Senior Year Early May'
        readings[-1]['stats']['calendar_text'] = 'Senior Year Late Apr'
        self.assertEqual(reconstruct(readings, events), [])
        readings, events = positive_sequence()
        readings[-1]['stats']['calendar_text'] = 'Senior Year Late Apr'
        self.assertEqual(reconstruct(readings, events), [])

        readings, events = positive_sequence()
        readings[0]['stats']['turns_remaining_to_goal'] = 4
        readings[4]['stats']['turns_remaining_to_goal'] = 1
        self.assertEqual(reconstruct(readings, events), [])

    def test_low_confidence_prompt_and_title_do_not_establish_source_facts(self):
        readings, events = positive_sequence()
        for candidate in readings[1:5]:
            candidate['ocr'] = {'neural': [
                {'text': item['text'], 'confidence': 89.0}
                for item in candidate['ocr']['neural']
            ]}
        self.assertEqual(reconstruct(readings, events), [])

    def test_condition_direction_up_is_not_recovery(self):
        readings, events = positive_sequence()
        effect = {'kind': 'condition_change', 'direction': 'up',
                  'name': 'Practice Poor'}
        readings[4]['effects'] = [effect]
        events[0]['effects'] = [effect]
        self.assertEqual(reconstruct(readings, events), [])
        readings, events = positive_sequence()
        readings[4]['effects'] = []
        events[0]['effects'] = []
        self.assertEqual(reconstruct(readings, events), [])

    def test_event_identity_and_event_only_effects_fail_closed(self):
        readings, events = positive_sequence()
        readings[4]['effects'] = []
        self.assertEqual(reconstruct(readings, events), [])

        readings, events = positive_sequence()
        events[0]['id'] = None
        self.assertEqual(reconstruct(readings, events), [])

        readings, events = positive_sequence()
        events.append(copy.deepcopy(events[0]))
        self.assertEqual(reconstruct(readings, events), [])

        readings, events = positive_sequence()
        events[0]['kind'] = 'training'
        self.assertEqual(reconstruct(readings, events), [])

    def test_one_named_result_or_one_evidence_frame_is_not_repeated_proof(self):
        readings, events = positive_sequence()
        readings[4]['evidence'] = readings[3]['evidence']
        self.assertEqual(reconstruct(readings, events), [])
        readings, events = positive_sequence()
        readings[3]['source_timestamp_ms'] = readings[4]['source_timestamp_ms']
        self.assertEqual(reconstruct(readings, events), [])

    def test_different_event_ids_cannot_count_the_same_source_result_twice(self):
        readings, events = positive_sequence()
        other = copy.deepcopy(events[0])
        other['id'] = 'duplicate-source-result'
        events.append(other)
        self.assertEqual(len(reconstruct(readings, events)), 1)

    def test_calendar_or_phase_change_between_request_and_result_rejects_visit(self):
        for calendar in ('Senior Year Early May', 'Junior Year Pre-Debut', 'not a calendar'):
            readings, events = positive_sequence()
            readings.insert(3, row(1625, 'changed-date.png', calendar=calendar))
            self.assertEqual(reconstruct(readings, events), [], calendar)

    def test_condition_recovery_requires_named_source_text(self):
        for effect in (dict(kind='condition_removed', direction='removed'),
                       dict(kind='condition_removed', name='Practice Poor'),
                       dict(kind='condition_removed', name='Practice Poor', raw_text='Recovered from Practice Poor.')):
            readings, events = positive_sequence()
            readings[4]['effects'] = [effect]
            events[0]['effects'] = [effect]
            self.assertEqual(reconstruct(readings, events), [])

    def test_new_infirmary_request_after_result_breaks_the_visit_boundary(self):
        readings, events = positive_sequence()
        readings.insert(5, row(2125, 'new-request.png', screen='infirmary_confirmation',
                              texts=('Visit the infirmary?', 'This will take up the entire turn.')))
        self.assertEqual(reconstruct(readings, events), [])


if __name__ == '__main__':
    unittest.main()
