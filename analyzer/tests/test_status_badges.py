import unittest
from copy import deepcopy

from tracen_replay.status_badges import read_badges, build_observations


def source_lines():
    return [dict(text='Energy', confidence=99.757, box=[382,119,440,154]),
            dict(text='GREAT', confidence=99.737, box=[733,122,804,151]),
            dict(text='Hype Level', confidence=99.981, box=[172,159,262,183]),
            dict(text='Mild', confidence=95.417, box=[181,180,254,219]),
            dict(text='Hype', confidence=90.882, box=[175,205,259,252])]


class StatusBadgeTests(unittest.TestCase):
    def test_reads_source_status_values_without_deltas(self):
        result = read_badges(source_lines())
        self.assertEqual([(r['kind'],r['value']) for r in result], [('mood_status','great'),('hype_status','mild')])
        self.assertTrue(all('amount' not in row for row in result))

    def test_values_follow_source_and_conflicts_stay_unknown(self):
        lines = source_lines()
        lines[1]['text'] = 'BAD'
        lines[3]['text'] = 'Maximum'
        self.assertEqual([r['value'] for r in read_badges(lines)], ['bad','maximum'])
        lines.append(dict(lines[1], text='GOOD'))
        self.assertFalse(any(r['kind']=='mood_status' for r in read_badges(lines)))

    def test_wrong_geometry_and_missing_anchors_cannot_supply_status(self):
        lines = source_lines()
        lines[1]['box'] = [600,450,700,480]
        self.assertFalse(any(r['kind']=='mood_status' for r in read_badges(lines)))
        self.assertEqual(read_badges([source_lines()[1], source_lines()[3]]), [])

    def test_hype_level_label_anchors_value_without_redundant_hype_word(self):
        lines = source_lines()
        lines[-1]['confidence'] = 80
        self.assertTrue(any(row['kind']=='hype_status' and row['value']=='mild' for row in read_badges(lines)))
        lines = [line for line in lines if line['text'] != 'Hype Level']
        self.assertFalse(any(row['kind']=='hype_status' for row in read_badges(lines)))

    def test_states_merge_only_across_supported_contiguous_observations(self):
        facts={'status_badges':read_badges(source_lines())}
        rows=[dict(source_timestamp_ms=t,evidence=f'{t}.png',facts=facts) for t in (0,250,2000)]
        result=build_observations(rows)
        self.assertEqual(len(result),4)
        self.assertEqual(result[0]['end_ms'],250)
        self.assertEqual(result[0]['evidence'],['0.png','250.png'])
        self.assertTrue(all(r['phase']=='observed' for r in result))
        self.assertTrue(all('amount' not in r['payload'] for r in result))

    def test_unparsed_reread_rows_between_parsed_frames_do_not_split_a_run(self):
        # Dense reread rows carry no status_badges fact at all (never parsed
        # for badges); they neither extend nor break the surrounding run.
        facts={'status_badges':read_badges(source_lines())}
        rows=[dict(source_timestamp_ms=0,evidence='0.png',facts=facts),
              dict(source_timestamp_ms=100,evidence='dense-100.png',facts={'training_gains':{'speed':5}}),
              dict(source_timestamp_ms=250,evidence='250.png',facts=facts)]
        result=build_observations(rows)
        mood=[r for r in result if r['payload']['kind']=='mood_status']
        self.assertEqual([(r['start_ms'],r['end_ms'],r['evidence']) for r in mood],[(0,250,['0.png','250.png'])])
        # A parsed frame with no badge still ends the run.
        rows[1]=dict(source_timestamp_ms=100,evidence='parsed-100.png',facts={'status_badges':[]})
        mood=[r for r in build_observations(rows) if r['payload']['kind']=='mood_status']
        self.assertEqual([(r['start_ms'],r['end_ms']) for r in mood],[(0,0),(250,250)])

    def test_equal_status_splits_at_visible_calendar_or_countdown_change(self):
        facts={'status_badges':read_badges(source_lines())[:1]}
        rows=[dict(source_timestamp_ms=t,evidence=f'{t}.png',facts=facts,
                   stats=dict(calendar_text=calendar,turns_remaining_to_goal=countdown))
              for t,calendar,countdown in [(0,'Senior Year Early Jan',5),
                                          (250,'Senior Year Early Jan',5),
                                          (500,'Senior Year Late Jan',4),
                                          (750,'Senior Year Late Jan',3)]]
        result=build_observations(rows)
        self.assertEqual(len(result),3)
        self.assertEqual(result[0]['evidence'],['0.png','250.png'])
        self.assertEqual(result[1]['start_ms'],500)
        self.assertEqual(result[2]['observed_context']['turns_remaining_to_goal'],3)

    def test_missing_calendar_is_not_filled_from_neighbor(self):
        facts={'status_badges':read_badges(source_lines())[:1]}
        rows=[dict(source_timestamp_ms=t,evidence=f'{t}.png',facts=facts,stats=stats)
              for t,stats in [(0,{'calendar_text':'Senior Year Early Jan'}),(250,{})]]
        result=build_observations(rows)
        self.assertEqual(len(result),2)
        self.assertEqual(result[1]['observed_context'],{})

    def test_report_contract_preserves_value_time_and_observed_phase(self):
        from tests.test_report_contract import valid_report
        from tracen_replay.report_contract import validate, ReportContractError
        report = valid_report()
        data = report['gameplay_tracking']
        data['readings'] = [dict(source_timestamp_ms=250, evidence='status.png',
                                facts={'status_badges':read_badges(source_lines())})]
        data['status_observations'] = build_observations(data['readings'])
        validate(report, require_gameplay=True)
        for key, value in [('phase','applied'), ('start_ms',0), ('evidence',['other.png'])]:
            changed = deepcopy(report)
            changed['gameplay_tracking']['status_observations'][0][key] = value
            with self.assertRaisesRegex(ReportContractError, 'source status badges'):
                validate(changed, require_gameplay=True)
        changed = deepcopy(report)
        changed['gameplay_tracking']['status_observations'][0]['payload']['value'] = 'bad'
        with self.assertRaisesRegex(ReportContractError, 'source status badges'):
            validate(changed, require_gameplay=True)


if __name__ == '__main__':
    unittest.main()
