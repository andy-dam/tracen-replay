import unittest

from tracen_replay.training_identity import read_identity, summarize


def _lines(name='Exercise Bike', option='Speed', heading_confidence=97.026,
           name_confidence=98.4):
    return [
        dict(text=f'{option} Lvl 3', confidence=heading_confidence,
             box=[229, 168, 332, 194]),
        dict(text=name, confidence=name_confidence, box=[228, 201, 354, 227]),
    ]


def _transition(timestamp=1000, name='Exercise Bike', option='Speed',
                evidence=None, screen='unknown', heading_confidence=97.026,
                name_confidence=98.4, preview=False):
    facts = read_identity(
        _lines(name, option, heading_confidence, name_confidence),
        'training' if screen == 'unknown' else screen,
        option,
    )
    facts['preview_option'] = option.casefold()
    if preview:
        facts['preview'] = True
    return dict(
        screen=screen,
        training_option=None,
        source_timestamp_ms=timestamp,
        evidence=evidence or f'gameplay/{timestamp}.png',
        stats={'training_preview': False},
        facts=facts,
    )


def _result(timestamp=1017, option='speed', evidence=None):
    return dict(
        screen='training_result',
        training_option=option,
        source_timestamp_ms=timestamp,
        evidence=evidence or f'training-result/{timestamp}.png',
        stats={'training_preview': False},
        facts={},
    )


class TrainingIdentitySourceBridgeTests(unittest.TestCase):
    def test_adjacent_gameplay_transition_binds_to_result_option(self):
        transition = _transition()
        result = summarize([_result()], 'speed', source_rows=[transition, _result()])

        self.assertEqual(result['training_name'], 'Exercise Bike')
        self.assertEqual(result['training_name_evidence'], ['gameplay/1000.png'])
        self.assertEqual(
            result['training_identity_bridge']['result_interval_ms'], [1017, 1017])
        self.assertEqual(
            result['training_identity_bridge']['observations'][0]['basis'],
            'adjacent_gameplay_transition_same_frame_identity',
        )

    def test_preview_screen_or_explicit_preview_phase_cannot_bridge(self):
        transition = _transition(screen='training_preview')
        self.assertNotIn(
            'training_name',
            summarize([_result()], 'speed', source_rows=[transition, _result()]),
        )

    def test_explicit_preview_result_row_cannot_supply_identity(self):
        result = _result()
        result['stats']['training_preview'] = True
        result['facts'] = read_identity(_lines(), 'training_result', 'Speed')
        self.assertNotIn('training_name', summarize([result], 'speed'))
        transition = _transition(preview=True)
        self.assertNotIn(
            'training_name',
            summarize([_result()], 'speed', source_rows=[transition, _result()]),
        )

    def test_preview_overlay_fact_alone_does_not_turn_transition_into_preview(self):
        transition = _transition()
        transition['facts']['preview_overlay_proven'] = True
        result = summarize([_result()], 'speed', source_rows=[transition, _result()])
        self.assertEqual(result['training_name'], 'Exercise Bike')

    def test_heading_option_must_match_explicit_result_option(self):
        transition = _transition(option='Speed')
        result = summarize([_result(option='wit')], 'wit', source_rows=[transition, _result(option='wit')])
        self.assertNotIn('training_name', result)

    def test_declared_option_switch_blocks_bridge(self):
        transition = _transition()
        switch = _transition(timestamp=1008, option='Wit', name='Other Exercise')
        result = summarize([_result()], 'speed', source_rows=[transition, switch, _result()])
        self.assertNotIn('training_name', result)

    def test_result_group_with_option_switch_is_not_a_continuous_interval(self):
        first = _result(timestamp=1017, option='speed')
        second = _result(timestamp=1050, option='wit')
        transition = _transition()
        result = summarize([first, second], 'speed', source_rows=[transition, first, second])
        self.assertNotIn('training_name', result)

    def test_gap_outside_continuous_result_interval_is_unknown(self):
        transition = _transition(timestamp=500)
        result = summarize([_result(timestamp=1001)], 'speed', source_rows=[transition, _result(timestamp=1001)])
        self.assertNotIn('training_name', result)

    def test_conflicting_adjacent_source_names_are_not_promoted(self):
        first = _transition(timestamp=1000, name='Exercise Bike')
        second = _transition(timestamp=1005, name='Other Exercise')
        result = summarize([_result()], 'speed', source_rows=[first, second, _result()])
        self.assertNotIn('training_name', result)
        self.assertEqual(result['training_name_conflicts'], ['Exercise Bike', 'Other Exercise'])

    def test_conflict_from_one_physical_source_is_not_hidden_by_deduplication(self):
        first = _transition(timestamp=1000, name='Exercise Bike', evidence='same.png')
        second = _transition(timestamp=1000, name='Other Exercise', evidence='same.png')
        result_row = _result()
        result = summarize([result_row], 'speed', source_rows=[first, second, result_row])
        self.assertNotIn('training_name', result)
        self.assertEqual(result['training_name_conflicts'], ['Exercise Bike', 'Other Exercise'])

    def test_context_title_is_not_an_identity_source(self):
        transition = _transition()
        transition['facts'].pop('training_name')
        transition['facts'].pop('training_identity_evidence')
        transition['context_title'] = 'Exercise Bike'
        result = summarize([_result()], 'speed', source_rows=[transition, _result()])
        self.assertNotIn('training_name', result)

    def test_continuous_interval_with_internal_result_rows_is_allowed(self):
        transition = _transition(timestamp=1000)
        rows = [_result(timestamp=1017), _result(timestamp=1100)]
        result = summarize(rows, 'speed', source_rows=[transition, *rows])
        self.assertEqual(result['training_name'], 'Exercise Bike')
        self.assertEqual(result['training_identity_bridge']['result_interval_ms'], [1017, 1100])


if __name__ == '__main__':
    unittest.main()
