import hashlib
import json
import shutil
import subprocess
import unittest
from pathlib import Path
from tests.test_gameplay import workspace_temp
from tests.test_neural_transactions import raw,line,row
from tracen_replay.vision import parse
from tracen_replay.transactions import training_events,outcome_events,lesson_receipts,reconcile_visible_training_candidates,skill_transactions
from tracen_replay.gameplay import CURRENCIES
from tracen_replay.pipeline import decode_frames
from tracen_replay.refine_contrast import apply_contrast_refinement,fingerprint


class RecordingRegressions(unittest.TestCase):
    def test_completion_hub_sp_can_verify_charge_without_attribute_grid(self):
        before=[row(t,'career_completion_hub',{'current_skill_points':1242}) for t in (0,250,500)]
        purchase=[row(1000,'skill_confirmation'),row(1500,'skill_receipt')]
        after=[row(t,'career_completion_hub',{'current_skill_points':8}) for t in (2000,2250,2500)]
        batch=skill_transactions(before+purchase+after,[])[0]
        self.assertEqual(batch['spent_skill_points'],1234)
        self.assertEqual(batch['deltas'],{'skill_points':-1234})
        self.assertEqual([e['skill_points'] for e in batch['balance_evidence']],[1242,8])
        self.assertTrue(all(len(e['evidence'])==3 and e['basis']=='repeated_completion_hub_skill_points'
                            for e in batch['balance_evidence']))
        self.assertFalse(batch['purchased_list_complete'])
        # The counter is static text: two consecutive frames agreeing are the balance.
        self.assertEqual(skill_transactions(before[:2]+purchase+after,[])[0]['spent_skill_points'],1234)
        # Projected menu counters are not interchangeable with actual hub SP.
        projected=[dict(r,screen='skill_selection') for r in before+after]
        self.assertIsNone(skill_transactions(projected[:3]+purchase+projected[3:],[])[0]['spent_skill_points'])
        # A single observation, inconsistent counters, and another intervening
        # SP award cannot certify a debit.
        self.assertIsNone(skill_transactions(before[:1]+purchase+after,[])[0]['spent_skill_points'])
        conflicting=[row(0,'career_completion_hub',{'current_skill_points':1242}),
                     row(250,'career_completion_hub',{'current_skill_points':1243}),before[-1]]
        self.assertIsNone(skill_transactions(conflicting+purchase+after,[])[0]['spent_skill_points'])
        award=row(750,effects=[{'kind':'stat_change','field':'skill_points','amount':10}])
        self.assertIsNone(skill_transactions(before+[award]+purchase+after,[])[0]['spent_skill_points'])

    def test_singular_confirmation_requires_a_receipt_before_creating_batch(self):
        # The separate recording's final modal uses singular "skill" even
        # though several skills are selected. A button OCR error is unrelated.
        parsed=parse(raw([line('Confirmation',(480,38,626,68)),
                          line('Learn the above skill?',(427,903,678,936)),
                          line('Cancel',(377,979,463,1014)),
                          line('Lsarn',(653,982,722,1014),74)]))
        confirmation=row(1000,parsed['screen'],parsed['facts'])
        self.assertEqual(skill_transactions([confirmation],[]),[])
        receipt=row(2000,'skill_receipt')
        self.assertEqual(skill_transactions([receipt],[]),[])
        batches=skill_transactions([confirmation,receipt],[])
        self.assertEqual(len(batches),1)
        self.assertIsNone(batches[0]['spent_skill_points'])
        self.assertFalse(batches[0]['complete_transaction_verified'])

    def test_state_constraints_do_not_choose_between_same_shape_candidates(self):
        before=dict(id='before',last_seen_ms=0,values={f:100 for f in ('speed','stamina','power','guts','wit','skill_points')},evidence='before.png')
        after=dict(id='after',first_seen_ms=1000,values=dict(before['values'],speed=112),evidence='after.png')
        event=dict(id='training',kind='training',training_option='wit',first_seen_ms=100,last_seen_ms=300,deltas={},field_evidence={})
        rows=[row(100,'training_result',{'training_gains':{'speed':1}}),row(200,'training_result',{'training_gains':{'speed':12}})]
        result=reconcile_visible_training_candidates(before,after,[event],rows)
        self.assertEqual(event['deltas'],{});self.assertEqual(result,[])
        event['deltas']={}
        self.assertEqual(reconcile_visible_training_candidates(before,after,[event],rows[:1]),[])
        self.assertEqual(event['deltas'],{})
        self.assertEqual(reconcile_visible_training_candidates(before,after,[event,dict(event,id='another')],rows),[])

    def test_upgrade_and_prerequisite_share_one_cart_charge(self):
        states=[dict(first_seen_ms=0,last_seen_ms=0,values={'skill_points':1000},evidence='before.png')]
        def card(name,cost,status):return dict(name=name,displayed_cost=cost,menu_status=status)
        available=[card('Upgrade',300,'available'),card('Base',180,'available')]
        selected=[card('Upgrade',None,'obtained_or_selected'),card('Base',None,'obtained_or_selected')]
        rows=[row(t,'skill_selection',{'displayed_skill_points':n,'skill_cards':cards}) for t,n,cards in ((250,1000,available),(500,1000,available),(750,700,selected),(1000,700,selected))]
        rows += [row(1250,'skill_confirmation',{'visible_skill_names':['Upgrade','Base']}),row(1500,'skill_receipt'),row(1750,'skill_receipt'),row(2000,'skill_selection',{'displayed_skill_points':700}),row(2250,'skill_selection',{'displayed_skill_points':700})]
        purchase=skill_transactions(rows,states)[0]
        self.assertEqual(purchase['spent_skill_points'],300)
        self.assertTrue(purchase['bundle_charge_assignment_complete'])
        self.assertEqual(purchase['committed_cart_bundles'][0]['prerequisite_candidates'],['Base'])
        self.assertEqual(purchase['bundle_net_cost'],300)

    def test_circle_suffix_does_not_confuse_offered_next_rank(self):
        import importlib.util
        if importlib.util.find_spec('cv2') is None:self.skipTest('Vision extra required')
        from PIL import Image,ImageDraw
        from tracen_replay.skill_variants import circle_suffix
        pane=Image.new('RGB',(810,1080),'white');draw=ImageDraw.Draw(pane)
        box=[362,534,537,560];x=537-148-32+12;y=534-2+8
        draw.ellipse((x,y,x+14,y+14),outline=(120,85,60),width=1)
        self.assertEqual(circle_suffix(pane,box),'single_circle')
        draw.ellipse((x+3,y+3,x+11,y+11),outline=(120,85,60),width=1)
        self.assertEqual(circle_suffix(pane,box),'double_circle')
        self.assertIsNone(circle_suffix(Image.new('RGB',(810,1080),'white'),box))

    @unittest.skipUnless(shutil.which('ffmpeg'),'FFmpeg required')
    def test_native_sixty_fps_does_not_skip_every_other_frame(self):
        with workspace_temp() as root:
            source=root/'native.mp4';dest=root/'frames';dest.mkdir()
            subprocess.run(['ffmpeg','-hide_banner','-loglevel','error','-f','lavfi','-i',
                'testsrc2=size=64x64:rate=60:duration=1','-c:v','libx264','-video_track_timescale','60000',str(source)],check=True,capture_output=True)
            frames=decode_frames(source,dest,0,.8,60,0)
            self.assertEqual(len(frames),48)
            self.assertLessEqual(max(b['source_timestamp_ms']-a['source_timestamp_ms'] for a,b in zip(frames,frames[1:])),17)

    def test_weak_exact_header_needs_independent_result_geometry(self):
        sample=raw(inspection='training_result_only')
        sample['regions']={'header':line('Training',confidence=89),'result.stamina':line('422/1300'),'result.guts':line('289/1500')}
        self.assertEqual(parse(sample)['screen'],'training_result')
        sample['regions'].pop('result.guts')
        self.assertNotEqual(parse(sample)['screen'],'training_result')

    def test_badge_cannot_split_an_event(self):
        sample=raw([line('MAX',(670,214,774,265)),line('Dance went up by 10.')])
        self.assertIsNone(parse(sample)['context_title'])

    def test_wrapped_technique_and_missing_space_keep_original_text(self):
        sample=raw([line('Learned Audience Involvement Intermediate'),line('Class.',(308,815,369,840),93),line('Passion went up by10.',(307,850,600,876))])
        parsed=parse(sample)
        self.assertEqual(parsed['effects'][0]['name'],'Audience Involvement Intermediate Class')
        self.assertEqual(parsed['effects'][1]['amount'],10)
        self.assertEqual(parsed['effects'][1]['original_text'],'Passion went up by10.')
        self.assertEqual(sample['lines'][-1]['text'],'Passion went up by10.')

    def test_verb_repair_never_changes_digits(self):
        parsed=parse(raw([line('Skill Pts wet up by 3.')]))
        self.assertEqual(parsed['effects'][0]['amount'],3)
        self.assertEqual(parsed['effects'][0]['original_text'],'Skill Pts wet up by 3.')

    def test_unterminated_receipt_needs_temporal_consensus(self):
        effect=dict(kind='stat_change',field='skill_points',amount=57)
        rows=[row(t,facts={'effect_candidates':[effect]}) for t in (0,250,500)]
        self.assertEqual(outcome_events(rows)[0]['deltas'],{'skill_points':57})
        self.assertEqual(outcome_events(rows[:2])[0]['deltas'],{})

    def test_numeric_outlier_resolution_retains_audit(self):
        rows=[row(t,effects=[dict(kind='performance_change',field='passion',amount=n)]) for t,n in ((0,0),(250,10),(500,10))]
        event=outcome_events(rows)[0]
        self.assertEqual(event['effects'][0]['amount'],10)
        self.assertEqual(event['resolved_reading_conflicts'][0]['observed_amounts'],[0,10])
        rows[-1]['effects'][0]['amount']=20
        self.assertTrue(outcome_events(rows)[0]['conflicting_readings'])

    def test_training_occlusion_prefix_does_not_erase_complete_award(self):
        rows=[row(t,'training_result',{'training_gains':{'wit':n}},training_option='wit') for t,n in enumerate((46,4,4,46,46))]
        event=training_events(rows)[0]
        self.assertEqual(event['deltas']['wit'],46)
        rows[1]['facts']['training_gains']['wit']=43
        self.assertNotIn('wit',training_events(rows)[0]['deltas'])

    def test_one_gain_and_later_total_need_independent_before_state(self):
        states=[dict(last_seen_ms=0,values={'speed':1396},evidence='before.png')]
        rows=[row(100,'training_result',{'training_gains':{'speed':14},'training_gain_candidates':{'speed':[14]}},training_option='wit'),row(200,'training_result',{'result_values':{'speed':1410},'result_value_candidates':{'speed':1410}},training_option='wit')]
        self.assertEqual(training_events(rows,states)[0]['deltas']['speed'],14)
        self.assertEqual(training_events(rows)[0]['deltas'],{})
        rows[1]['facts']['result_value_candidates']['speed']=1400
        self.assertEqual(training_events(rows,states)[0]['deltas'],{})

    def test_contrast_views_are_correlated_and_reject_disagreement(self):
        sample=raw();views=[dict(text='1410/1601',confidence=98),dict(text='1410/1601',confidence=99)]
        refinement=dict(raw_sha256=fingerprint(sample),regions={'result.speed':views})
        self.assertTrue(apply_contrast_refinement(sample,refinement)['regions']['result.speed']['contrast_consensus'])
        views[1]['text']='410/1601'
        self.assertEqual(apply_contrast_refinement(sample,refinement)['regions'],{})
        refinement['raw_sha256']='changed'
        with self.assertRaises(ValueError):apply_contrast_refinement(sample,refinement)

    def test_purchase_baseline_cannot_cross_a_different_menu_visit(self):
        points=dict.fromkeys(CURRENCIES,100);current=dict(points,vocal=84);projected=dict(current,dance=84)
        rows=[row(0,'lesson_selection',{'performance_points':points}),row(250,'lesson_confirmation'),row(1000,'lesson_selection',{'performance_points':current}),row(1250,'lesson_selection',{'performance_points':current}),row(1500,'lesson_confirmation',{'name_candidates':['Dance'],'projected_performance_points':projected})]
        event=dict(id='receipt',first_seen_ms=1750,last_seen_ms=2000,effects=[dict(kind='named_acquisition',name='Dance')],deltas={},evidence='receipt.png')
        purchase=lesson_receipts(rows,[event])[0]
        self.assertEqual(purchase['performance_cost'],dict.fromkeys(CURRENCIES,0)|{'dance':16})


if __name__=='__main__':unittest.main()
