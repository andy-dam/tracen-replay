import unittest
import uuid

from contextlib import contextmanager
import shutil
from PIL import Image
from tests import localdata
from tracen_replay import layout
from tracen_replay.gameplay import effects_from_lines, preview_effects, classify, lesson_transitions, CURRENCIES, screen_summary


def line(text, confidence=95):
    return {'text':text,'confidence':confidence}

# Other test modules import this scratch-folder helper from here.
@contextmanager
def workspace_temp():
    root = localdata.scratch(uuid.uuid4().hex)
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
    def test_an_aptitude_already_mastered_is_a_status_line(self):
        effect,=effects_from_lines([line('Medium Aptitude has already been mastered.')])
        self.assertEqual((effect['kind'],effect['name'],effect['value']),('aptitude_status','Medium','mastered'))
    def test_a_change_of_nothing_is_a_number_with_its_leading_digit_hidden(self):
        # The game never reports "went up by 0": the cursor over the 2 of 20.
        for text in ['Visuals went up by 0.','Speed went up by 0.','Skill Pts went down by 0.']:
            self.assertEqual(effects_from_lines([line(text)]),[])
        self.assertEqual(effects_from_lines([line('Visuals went up by 20.')])[0]['amount'],20)
    def test_energy_song_and_hype_are_separate_resources(self):
        effects=effects_from_lines([line('Energy recovered by 50.'),line('Learned the song "Make Debut!".'),line('Hype Level went up.')])
        self.assertEqual([e['kind'] for e in effects],['energy_change','song_learned','hype_increased'])
        self.assertIsNone(effects[1]['cost'])

    def test_clipped_song_heading_is_a_repaired_song_never_a_named_acquisition(self):
        # The quoted title anchors the receipt; the misread fixed phrase is
        # repaired by bounded distance and marked, never left as a lesson name.
        for text in (
            'Learned e song "Present March ".',
            'Learned song "Present March ".',
            'Learned a song "Present March ".',
        ):
            with self.subTest(text=text):
                self.assertEqual([(e['kind'], e['name'], e.get('text_normalization')) for e in effects_from_lines([line(text)])],
                                 [('song_learned', 'Present March', 'fixed_phrase_repair')])
        generic = effects_from_lines([line('Learned Makeup Basics.')])
        self.assertEqual([effect['kind'] for effect in generic], ['named_acquisition'])
    def test_uncertain_text_abstains(self):
        self.assertEqual(effects_from_lines([line('Speed went up by 10.',59)]),[])
    def test_malformed_confidence_abstains_without_type_error(self):
        for confidence in ('99', None, float('nan'), 10**1000):
            with self.subTest(confidence=repr(confidence)):
                self.assertEqual(
                    effects_from_lines([line('Speed went up by 10.', confidence)]), [])
                self.assertEqual(
                    preview_effects([line('Speed +10', confidence)]), [])
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
    def test_singular_skill_confirmation_and_word_boundaries(self):
        self.assertEqual(classify('Confirmation Learn the above skill? Cancel Lsarn','Learn'),'skill_confirmation')
        self.assertEqual(classify('Learn the above\nskills?','Learn'),'skill_confirmation')
        self.assertNotEqual(classify('Learn the above skillset','Learn'),'skill_confirmation')
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
    def test_the_trainees_details_popup_is_the_final_summary(self):
        self.assertEqual(classify('Umamusume Details [Ashen Miracle] Oguri Cap Speed Stamina Power Guts Wit 1570 726 1045 545 1138 Track Turf Dirt',''),'career_summary')
        self.assertEqual(classify('Umamusume Details Oguri Cap Epithet Change Skills Inspiration',''),'unknown')
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
    def test_completion_context_does_not_invent_skill_names_or_cost(self):
        rows=[dict(screen=screen,source_timestamp_ms=i*1000,evidence=f'{i}.png') for i,screen in enumerate(['skill_confirmation','skill_receipt'])]
        spans=screen_summary(rows)
        self.assertIsNone(spans[0]['completed_action'])
        self.assertEqual(spans[1]['completed_action'],'skill_purchase_batch')
        self.assertIsNone(spans[1]['spent_skill_points'])
        self.assertEqual(spans[1]['confirmation_evidence'],'0.png')
    def test_auxiliary_pixels_never_reach_recognizer(self):
        # Every reader of a PC recording is handed this crop and nothing
        # else, so a change outside it cannot reach a reading. The side panel
        # lives there.
        pane_box=layout.PC.pane
        self.assertEqual(pane_box,(148,0,958,1080))
        im=Image.new('RGB',(1920,1080),'red');im.paste('blue',pane_box)
        pane=im.crop(pane_box)
        self.assertEqual(pane.size,(810,1080))
        im.paste('green',(958,0,1920,1080))
        self.assertEqual(im.crop(pane_box).tobytes(),pane.tobytes())

if __name__=='__main__':unittest.main()
