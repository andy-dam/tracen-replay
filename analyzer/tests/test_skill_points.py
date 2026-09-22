import unittest
from tests.test_neural_transactions import raw,line
from tracen_replay.refine_skill_points import counter_reading
from tracen_replay.vision import parse


class SkillPointRefinementTests(unittest.TestCase):
    def test_currency_padding_recovers_full_number_without_legacy_clipping(self):
        sample=raw([line('Performance Points',(400,40,700,70)),line('0153',(635,88,701,121),95)],
                   currency_padding={'visual':[line('153'),line('153')]})
        sample['header']='Lessons'
        sample['regions']={'performance.visual':line('53'),
                           'wide_performance.visual':line('153',confidence=96.7)}
        self.assertEqual(parse(sample)['facts']['performance_points']['visual'],153)
        sample['regions']['wide_performance.visual']=line('154')
        self.assertIsNone(parse(sample)['facts']['performance_points']['visual'])

    def test_currency_padding_disagreement_and_out_of_range_abstain(self):
        for views in ([line('153'),line('154')],[line('1000'),line('1000')],[line('153')]):
            sample=raw([line('Performance Points',(400,40,700,70))],currency_padding={'visual':views})
            sample['header']='Lessons'
            self.assertIsNone(parse(sample)['facts']['performance_points']['visual'])

    def test_menu_padding_cannot_override_a_modal_balance(self):
        sample=raw([line('Spend performance points to learn this technique?')],
                   currency_padding={'visual':[line('153'),line('153')]})
        self.assertIsNone(parse(sample)['facts']['projected_performance_points']['visual'])

    def test_finish_confirmation_reports_balances_not_spending(self):
        lines=[line('Finish this Career playthrough?',(406,511,706,543)),
               line('Remaining Skill Points',(417,563,581,587)),line('8 pt(s)',(595,560,669,593)),
               line('Remaining Performance Points',(421,604,684,629)),
               line('9',(363,640,388,671)),line('18',(450,638,495,674)),line('2',(569,639,599,672)),
               line('18',(657,639,701,672)),line('118',(738,639,804,672))]
        result=parse(raw(lines))
        self.assertEqual(result['facts']['current_skill_points'],8)
        self.assertEqual(result['facts']['remaining_performance_points'],
                         {'dance':9,'passion':18,'vocal':2,'visual':18,'composure':118})
        self.assertEqual(result['effects'],[])
        self.assertIsNone(result.get('completed_action'))
        self.assertNotIn('current_skill_points',parse(raw([lines[0]]+lines[2:]))['facts'])
        lines.append(line('9 pt(s)',(595,560,669,593)))
        self.assertIsNone(parse(raw(lines))['facts']['current_skill_points'])

    def test_small_counter_and_zero_need_matching_views(self):
        for value in ('8','0','1242'):
            self.assertEqual(counter_reading([line(value),line(value)]),(int(value),[]))
        self.assertEqual(counter_reading([line('8')]),(None,[]))
        self.assertEqual(counter_reading([line('8'),line('8',confidence=90)]),(None,[]))
        self.assertEqual(counter_reading([line('8?'),line('8?')]),(None,[]))

    def test_disagreement_does_not_choose_a_convenient_balance(self):
        self.assertEqual(counter_reading([line('8'),line('3')]),(None,[3,8]))
        self.assertEqual(counter_reading([line('8'),line('8')],3),(None,[3,8]))

    def test_counter_remains_projected_and_does_not_override_receipt(self):
        sample=raw([line('Skill Points',(525,339,620,366))],
                   skill_point_refinement=[line('8'),line('8')])
        sample['header']='Learn'
        facts=parse(sample)['facts']
        self.assertEqual(facts['displayed_skill_points'],8)
        self.assertIsNone(facts['spent_skill_points'])
        sample['lines'].append(line('Skills Learned'))
        self.assertNotIn('displayed_skill_points',parse(sample)['facts'])

    def test_parser_exposes_conflicting_counter_values(self):
        sample=raw([line('Skill Points',(525,339,620,366)),line('3',(750,338,770,366))],
                   skill_point_refinement=[line('8'),line('8')])
        sample['header']='Learn'
        facts=parse(sample)['facts']
        self.assertNotIn('displayed_skill_points',facts)
        self.assertEqual(facts['skill_point_conflict'],[3,8])
