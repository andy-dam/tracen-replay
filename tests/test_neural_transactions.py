import unittest
from tracen_replay.vision import parse
from tracen_replay.gameplay import effects_from_lines, CURRENCIES
from tracen_replay.transactions import training_events, outcome_events, lesson_receipts, skill_transactions
from tracen_replay.inspect_training import merge, windows


def line(text,box=(306,785,700,814),confidence=99):
    return dict(text=text,box=list(box),confidence=confidence)


def raw(lines=(),**kwargs):
    return dict(lines=list(lines),regions={},header='',current_grid=False,result_grid=False,**kwargs)


def row(t,screen='unknown',facts=None,effects=(),**kwargs):
    return dict(source_timestamp_ms=t,evidence=f'{t}.png',screen=screen,facts=facts or {},effects=list(effects),stats={},**kwargs)


class NeuralParserTests(unittest.TestCase):
    def test_full_counter_does_not_drop_leading_digit(self):
        sample=raw([line('Lessons',(155,0,230,25)),line('Performance Points',(400,40,700,70)),line('110',(747,87,807,122))])
        sample['header']='Lessons'
        sample['regions']['performance.composure']=dict(text='10',confidence=99)
        self.assertEqual(parse(sample)['facts']['performance_points']['composure'],110)

    def test_uncertain_full_counter_does_not_fall_back_to_clipped_digit(self):
        sample=raw([line('Performance Points',(400,40,700,70)),line('110',(747,87,807,122),96)])
        sample['header']='Lessons';sample['regions']['performance.composure']=dict(text='10',confidence=99)
        self.assertIsNone(parse(sample)['facts']['performance_points']['composure'])

    def test_wrapped_song_receipt_preserves_name(self):
        got=parse(raw([line('Learned the song "Full Speed Ahead! Umadol'),line('Power".',(307,810,403,837))]))
        self.assertEqual(got['effects'][0]['name'],'Full Speed Ahead! Umadol Power')

    def test_confirmation_text_cannot_be_an_award(self):
        got=parse(raw([line('Spend performance points to learn this technique?'),line('Speed went up by 5.')]))
        self.assertEqual(got['screen'],'lesson_confirmation');self.assertEqual(got['effects'],[])

    def test_modal_excludes_background_offer(self):
        got=parse(raw([line('Spend performance points to learn this technique?'),line('Power +5',(450,144,555,172)),line('Speed +22',(450,500,555,530))]))
        self.assertEqual([e['raw_text'] for e in got['facts']['projected_effects']],['Power +5'])

    def test_learned_and_hype_max_are_distinct(self):
        effects=effects_from_lines([line('Learned Makeup Basics.'),line('Hype Level is maxed out.')])
        self.assertEqual([e['kind'] for e in effects],['named_acquisition','hype_status'])

    def test_partial_numeric_sentence_is_not_complete_reward(self):
        self.assertEqual(parse(raw([line('Skill Pts went up by 5')]))['effects'],[])
        self.assertEqual(parse(raw([line('Skill Pts went up by 57.')]))['effects'][0]['amount'],57)

    def test_caps_and_future_bonus_are_not_current_stats(self):
        got=parse(raw([line('Skill Pts Bonus went up by 3.'),line('Dance cap went up by 50.'),line('Speed cap went up by 1.')]))
        self.assertEqual([e['kind'] for e in got['effects']],['training_modifier_change','performance_cap_change','stat_cap_change'])


class TransactionTests(unittest.TestCase):
    def test_browsing_never_becomes_selected_training(self):
        self.assertEqual(training_events([row(0,'training_preview',training_option='speed')]),[])

    def test_repeated_training_and_conflicting_field(self):
        events=training_events([row(0,'training_result',{'training_gains':{'speed':10,'power':5}},training_option='speed'),row(33,'training_result',{'training_gains':{'speed':10,'power':6}},training_option='speed')])
        self.assertEqual(events[0]['deltas'],{'speed':10});self.assertEqual(events[0]['conflicting_readings'],{'power':[5,6]})
        self.assertEqual(events[0]['repeated_fields'],['speed'])

    def test_dialogue_separates_identical_event_awards(self):
        effect=dict(kind='stat_change',field='speed',amount=5)
        rows=[row(0,effects=[effect]),row(250,ocr={'neural':[line('A different conversation starts here.',(300,810,800,840))]}),row(500,effects=[effect])]
        self.assertEqual(len(outcome_events(rows)),2)

    def test_confirmation_without_receipt_is_not_purchase(self):
        self.assertEqual(lesson_receipts([row(0,'lesson_confirmation',{'name_candidates':['Makeup Basics']})],[]),[])

    def test_named_receipt_commits_once_and_observed_debit_matches(self):
        points=dict.fromkeys(CURRENCIES,110);after=dict(points,visual=100)
        rows=[row(0,'lesson_selection',{'performance_points':points}),row(250,'lesson_confirmation',{'name_candidates':['Makeup Basics'],'projected_performance_points':after}),row(500,'lesson_confirmation',{'name_candidates':['Makeup Basics'],'projected_performance_points':dict(after,visual=None)}),row(1500,'lesson_selection',{'performance_points':after})]
        event=dict(id='outcome-1',first_seen_ms=750,last_seen_ms=1250,evidence='receipt.png',effects=[dict(kind='named_acquisition',name='Makeup Basics')],deltas={'guts':5})
        purchases=lesson_receipts(rows,[event,event])
        self.assertEqual(len(purchases),1);self.assertEqual(purchases[0]['performance_cost']['visual'],10)
        self.assertEqual(purchases[0]['cost_basis'],'observed_debit');self.assertEqual(purchases[0]['awarded_stats'],{'guts':5})

    def test_specialized_sample_cannot_replace_general_effects(self):
        base=row(0,'event_outcome',effects=[dict(kind='energy_change',amount=5)])
        specialized=row(0,'training_result',{'training_gains':{'speed':5}})
        self.assertEqual(merge([base],[specialized]),[base])

    def test_training_inspection_window_covers_preceding_animation(self):
        got=windows([row(1000,'training_result',training_option='speed')],2000)
        self.assertEqual((got[0]['start_ms'],got[0]['end_ms']),(500,2000))

    def test_skill_charge_requires_receipt_and_matching_post_balance(self):
        states=[dict(first_seen_ms=0,last_seen_ms=0,values={'skill_points':1631},evidence='before.png')]
        rows=[row(250,'skill_selection',{'displayed_skill_points':1631}),row(3000,'skill_selection',{'displayed_skill_points':828}),row(3250,'skill_selection',{'displayed_skill_points':828}),row(4000,'skill_confirmation'),row(4250,'skill_receipt'),row(4500,'skill_receipt'),row(5000,'skill_selection',{'displayed_skill_points':828}),row(5250,'skill_selection',{'displayed_skill_points':828})]
        got=skill_transactions(rows,states)
        self.assertEqual(got[0]['spent_skill_points'],803)
        rows[-1]['facts']['displayed_skill_points']=700
        self.assertIsNone(skill_transactions(rows,states)[0]['spent_skill_points'])

    def test_observed_result_plateau_can_support_hidden_plus_sign(self):
        states=[dict(first_seen_ms=0,last_seen_ms=500,values={'speed':208},evidence='before.png')]
        rows=[row(t,'training_result',{'result_values':{'speed':223}},training_option='speed') for t in (1000,1050,1100)]
        got=training_events(rows,states)[0]
        self.assertEqual(got['deltas']['speed'],15)
        self.assertEqual(got['result_state_derived_fields'],['speed'])
        self.assertEqual(training_events(rows[:2],states)[0]['deltas'],{})


if __name__=='__main__':unittest.main()
