import unittest
from unittest.mock import patch
from pathlib import Path
from types import SimpleNamespace
import uuid
from contextlib import contextmanager
import shutil
from PIL import Image
from tracen_replay.gameplay import effects_from_lines, preview_effects, classify, lesson_transitions, investigation_windows, GameplayReader, track, CURRENCIES, ledger, FIELDS, screen_summary
from tracen_replay.pipeline import analyze, PipelineError
from tracen_replay.gameplay_evaluate import evaluate
from tracen_replay.stats import Reader


def line(text, confidence=95):
    return {'text':text,'confidence':confidence}

@contextmanager
def workspace_temp():
    root=Path('.local/test-runs')/uuid.uuid4().hex
    root.mkdir(parents=True)
    try:
        yield root
    finally:
        shutil.rmtree(root)


class GameplayTests(unittest.TestCase):
    def test_actual_high_stat_gain_not_halved_again(self):
        self.assertEqual(effects_from_lines([line('Speed went up by 6 to new heights.')])[0]['amount'],6)
    def test_previews_modifiers_and_caps_are_not_stat_awards(self):
        for text in ['Speed +12','Training Power Gain +1','Friendship Training Effectiveness +5%', 'Skill Pts 160','Speed went up by 5 maybe.']:
            self.assertEqual(effects_from_lines([line(text)]),[])
    def test_energy_song_and_hype_are_separate_resources(self):
        effects=effects_from_lines([line('Energy recovered by 50.'),line('Learned the song "Make Debut!".'),line('Hype Level went up.')])
        self.assertEqual([e['kind'] for e in effects],['energy_change','song_learned','hype_increased'])
        self.assertIsNone(effects[1]['cost'])
    def test_uncertain_text_abstains(self):
        self.assertEqual(effects_from_lines([line('Speed went up by 10.',59)]),[])
    def test_other_resources_do_not_enter_stat_accounting(self):
        got=effects_from_lines([line('Energy went down by 19.'),line('Friendship with Fine Motion went up by 5.'),line('Gained 2 hint level(s) for Hydrate.'),line('Vocals went up by 10.'),line('Mood went up.')])
        self.assertEqual([e['kind'] for e in got],['energy_change','friendship_change','skill_hint_change','performance_change','mood_change'])
        self.assertEqual(got[2]['name'],'Hydrate')
        self.assertEqual(got[3]['field'],'vocal')
    def test_future_effects_are_typed_without_awards(self):
        got=preview_effects([line('Guts +5'),line('Training Power Gain +1'),line('Friendship Training Effectiveness +5%')])
        self.assertEqual([e['kind'] for e in got],['immediate_on_purchase','future_training_modifier','queued_concert_bonus'])
        self.assertTrue(all(e['awarded'] is False for e in got))
    def test_modal_overrides_background(self):
        self.assertEqual(classify('Learn the above skills? Skills Learned','Learn'),'skill_confirmation')
        self.assertEqual(classify('Spend performance points to learn this technique?','Lessons'),'lesson_confirmation')
    def test_training_preview_not_completion(self):
        self.assertEqual(classify('Training Speed Lvl 1','Training',False,True),'training_preview')
        self.assertEqual(classify('Training Speed Lvl 1','Training',True,False),'training_result')
    def test_concert_banner_requires_later_context_validation(self):
        self.assertEqual(classify('GREAT SUCCESS!',''),'concert_result_candidate')
        self.assertNotEqual(classify('GREAT SUCCESS!','Training',True),'concert_result_candidate')
    def test_playback_not_performance(self):
        self.assertEqual(classify('Landscape Portrait Girls Legend U',''),'playback_confirmation')
    def test_owned_summary_not_skill_purchase(self):
        self.assertEqual(classify('Complete a Career playthrough Professor of Curvature',''),'career_summary')
    def test_skill_receipt_separate_from_selection(self):
        self.assertEqual(classify('Skills Learned','Learn'),'skill_receipt')
        self.assertEqual(classify('Obtained Skill Points 40','Learn'),'skill_selection')
    def rows(self, after=9, projection=9):
        points=lambda n:dict(zip(CURRENCIES,[n,10,10,10,10]))
        return [dict(screen='lesson_selection',source_timestamp_ms=0,evidence='before',facts={'performance_points':points(10)}),dict(screen='lesson_confirmation',source_timestamp_ms=500,evidence='dialog',facts={'name_candidates':['Makeup Basics'],'projected_performance_points':points(projection)}),dict(screen='lesson_selection',source_timestamp_ms=1000,evidence='after',facts={'performance_points':points(after)})]
    def test_lesson_requires_matching_observed_debit(self):
        got=lesson_transitions(self.rows())
        self.assertEqual(len(got),1)
        self.assertEqual(got[0]['performance_cost']['dance'],1)
        self.assertIsNone(got[0]['awarded_stats'])
        self.assertEqual(got[0]['evidence'],['before','dialog','after'])
    def test_cancel_mismatch_and_unknown_do_not_purchase(self):
        self.assertEqual(lesson_transitions(self.rows(after=10)),[])
        self.assertEqual(lesson_transitions(self.rows(after=8)),[])
        rows=self.rows(); rows[0]['facts']['performance_points']['dance']=None
        self.assertEqual(lesson_transitions(rows),[])
    def test_repeated_menu_frames_do_not_duplicate_purchase(self):
        rows=self.rows(); rows.append(dict(rows[-1],source_timestamp_ms=1250))
        self.assertEqual(len(lesson_transitions(rows)),1)
    def test_delayed_counter_update_preserves_pending_request(self):
        rows=self.rows(); unchanged=dict(rows[0],source_timestamp_ms=750)
        rows.insert(2,unchanged)
        self.assertEqual(len(lesson_transitions(rows)),1)
    def test_expired_confirmation_does_not_explain_later_debit(self):
        rows=self.rows();rows[-1]['source_timestamp_ms']=10000
        self.assertEqual(lesson_transitions(rows),[])
    def test_evaluation_rejects_missing_examples_and_other_source(self):
        ref={'source_sha256':'a','observations':[{'source_timestamp_ms':0,'screen':'unknown'}]}
        self.assertFalse(evaluate(ref,[])['passed'])
        with self.assertRaises(ValueError):evaluate(ref,[{'source':{'sha256':'b'}}])
    def test_literal_quotes_do_not_corrupt_tesseract_tsv(self):
        reader=Reader.__new__(Reader);reader.cache={};reader.executable='unused'
        header='level\tpage_num\tblock_num\tpar_num\tline_num\tword_num\tleft\ttop\twidth\theight\tconf\ttext\n'
        payload=header+'5\t1\t1\t1\t1\t1\t0\t0\t10\t10\t95\t"Make\n'+'5\t1\t1\t1\t1\t2\t20\t0\t10\t10\t95\tDebut!"\n'
        with patch('tracen_replay.stats.subprocess.run',return_value=SimpleNamespace(stdout=payload.encode())):
            words=reader.ocr(Image.new('RGB',(10,10)))
        self.assertEqual([w['text'] for w in words],['"Make','Debut!"'])
    def training_interval(self, values):
        before=dict(id='a',last_seen_ms=0,values={f:100 for f in FIELDS})
        after=dict(id='b',first_seen_ms=1000,values={f:130 if f=='skill_points' else 100 for f in FIELDS})
        rows=[dict(source_timestamp_ms=200+i*200,screen='training_result',training_option='speed',effects=[],facts={'result_values':{'skill_points':v}},evidence=f'{i}.png') for i,v in enumerate(values)]
        return ledger(rows,[before,after])[0]
    def test_repeated_result_total_supports_only_observed_partial_gain(self):
        result=self.training_interval([125,125])
        self.assertEqual(result['supported_change']['skill_points'],25)
        self.assertEqual(result['unexplained_change']['skill_points'],5)
        self.assertFalse(result['event_assignment_verified'])
    def test_single_or_animating_result_total_cannot_explain_change(self):
        for values in ([125],[124,125]):
            self.assertEqual(self.training_interval(values)['supported_change']['skill_points'],0)
    def test_training_proof_links_to_a_readable_awarded_field(self):
        result=self.training_interval([None,125,125])
        self.assertEqual(result['events'][0]['evidence'],'1.png')
        self.assertNotIn('0.png',result['events'][0]['supporting_frames'])
    def test_completion_context_does_not_invent_skill_names_or_cost(self):
        rows=[dict(screen=screen,source_timestamp_ms=i*1000,evidence=f'{i}.png') for i,screen in enumerate(['skill_confirmation','skill_receipt'])]
        spans=screen_summary(rows)
        self.assertIsNone(spans[0]['completed_action'])
        self.assertEqual(spans[1]['completed_action'],'skill_purchase_batch')
        self.assertIsNone(spans[1]['spent_skill_points'])
        self.assertEqual(spans[1]['confirmation_evidence'],'0.png')
    def test_investigation_is_bounded_and_clipped(self):
        intervals=[dict(status='unresolved',start_ms=t,end_ms=t+5000,before_id='a',after_id='b') for t in [0,1000,6000,12000]]
        windows=investigation_windows(intervals,[],{'source_start_ms':0,'duration_ms':14000})
        self.assertEqual(len(windows),2)
        for w in windows:
            self.assertGreaterEqual(w['start_ms'],0);self.assertLessEqual(w['end_ms']-w['start_ms'],3000)
    def test_log_mode_and_gameplay_mode_cannot_mix(self):
        with self.assertRaises(PipelineError):
            analyze('unused','unused',track_stats=True,gameplay_only=True)
    def test_auxiliary_pixels_never_reach_recognizer(self):
        with workspace_temp() as temp:
            root=Path(temp);(root/'frames').mkdir()
            report={'frames':[{'id':'a','evidence':'frames/a.png','source_timestamp_ms':0}], 'sampling':{'requested_fps':4},'clip':{'source_start_ms':0,'duration_ms':1000}}
            im=Image.new('RGB',(1920,1080),'red');im.paste('blue',(148,0,958,1080));im.save(root/'frames/a.png')
            received=[]
            def read(_self,pane):
                received.append(pane.tobytes());self.assertEqual(pane.size,(810,1080))
                return dict(screen='unknown',training_option=None,completed_action=None,stats={'values':None},effects=[],facts={})
            with patch.object(GameplayReader,'__init__',lambda self, executable=None: setattr(self,'reader',SimpleNamespace(Image=Image))), patch.object(GameplayReader,'read_pane',read):
                a=track(report,root)
                im.paste('green',(958,0,1920,1080));im.save(root/'frames/a.png')
                b=track(report,root)
            self.assertEqual(received[0],received[1]);self.assertEqual(a,b)

if __name__=='__main__':unittest.main()
