import hashlib
import json
import unittest
from PIL import Image
from tests.test_gameplay import workspace_temp
from tests.test_neural_transactions import row
from tracen_replay.calendar_coverage import audit
from tracen_replay.mechanics_audit import fan_accounting, song_acquisitions
from tracen_replay.transactions import outing_actions,outcome_events,training_actions,training_events
from tracen_replay.transaction_evaluate import evaluate
from tracen_replay.verify_evidence import verify, inside
from tracen_replay.vision import parse
from tests.test_neural_transactions import raw, line
from tracen_replay.receipt_review import evaluate as evaluate_review
from tracen_replay.mechanics_audit import unparsed_receipt_candidates


class RecordingCoverageTests(unittest.TestCase):
    def test_completed_training_survives_unreadable_rewards(self):
        rows=[row(t,'training_result',training_option='wit') for t in (100,350)]
        events=training_events(rows)
        actions=training_actions(events)
        self.assertEqual(len(actions),1)
        self.assertEqual(actions[0]['training_option'],'wit')
        self.assertFalse(actions[0]['effect_coverage_verified'])
        self.assertEqual(events[0]['deltas'],{})
        self.assertEqual(actions[0]['action_identity_evidence'],['100.png','350.png'])
        self.assertEqual(training_actions(training_events(rows[:1])),[])
        self.assertEqual(training_actions(training_events([rows[0],rows[0]])),[])
        for r in rows:r['screen']='training_preview'
        self.assertEqual(training_actions(training_events(rows)),[])

    def test_receipt_review_does_not_claim_recall_and_rejects_changed_option(self):
        ref=dict(source_sha256='s',scope='selected receipts',actions=[dict(source_timestamp_ms=100,expected=dict(kind='training',training_option='wit'))])
        action=dict(source_timestamp_ms=100,kind='training',training_option='wit')
        report=dict(source={'sha256':'s'},gameplay_tracking=dict(auxiliary_log_used=False,turn_action_receipts=[action]))
        self.assertTrue(evaluate_review(ref,report)['passed'])
        self.assertFalse(evaluate_review(ref,report)['missed_action_recall_measured'])
        action['training_option']='speed'
        self.assertFalse(evaluate_review(ref,report)['passed'])

    def test_unparsed_receipt_is_review_candidate_not_numeric_effect(self):
        rows=[row(t,'event_outcome',ocr={'neural':[line('Skill Pts went up by IU.')]}) for t in (100,350)]
        candidates=unparsed_receipt_candidates(rows)
        self.assertEqual(len(candidates),1)
        self.assertEqual(candidates[0]['observations'],2)
        self.assertNotIn('amount',candidates[0])
        rows[0]['screen']=rows[1]['screen']='skill_selection'
        self.assertEqual(unparsed_receipt_candidates(rows),[])

    def test_hint_circle_ocr_alternatives_do_not_double_award(self):
        effects=[dict(kind='skill_hint_change',name=name,amount=1) for name in ('Nakayama Racecourse','Nakayama Racecourse O')]
        event=outcome_events([row(100,'event_outcome',effects=effects)])[0]
        self.assertEqual(len(event['effects']),1)
        self.assertFalse(event['effects'][0]['circle_variant_verified'])
        self.assertEqual(len(event['effects'][0]['observed_name_candidates']),2)
        effects[1]['amount']=2
        self.assertEqual(len(outcome_events([row(100,'event_outcome',effects=effects)])[0]['effects']),2)

    def test_non_stat_receipts_keep_distinct_resource_types(self):
        effects=parse(raw([line('Max Energy increased by 4.'),line('Acquired Charming .'),
                           line('Pace Chaser Aptitude went up.')]))['effects']
        self.assertEqual([e['kind'] for e in effects],['max_energy_change','condition_acquired','aptitude_change'])
        self.assertEqual(effects[0]['amount'],4)
        self.assertEqual(effects[1]['name'],'Charming')
        self.assertIsNone(effects[1]['mechanical_effect'])
        self.assertIsNone(effects[2]['rank'])

    def test_concert_plan_and_activation_are_different_observations(self):
        observation=raw([line('Concert Info',(400,100,600,130)),line('Concert Bonus Changes',(300,300,700,330)),
                         line('+10% +15%',(300,380,430,420)),line('+5 +10',(480,380,610,420)),line('Lvl O Lvl 3',(670,380,810,420))])
        info=parse(observation)
        self.assertEqual(info['screen'],'concert_info')
        self.assertEqual(info['facts']['current_concert_bonuses']['friendship_training_effectiveness'],10)
        self.assertEqual(info['facts']['planned_concert_bonuses']['friendship_training_effectiveness'],15)
        self.assertEqual(info['facts']['current_concert_bonuses']['support_chain_event_frequency'],0)
        self.assertEqual(info['facts']['planned_concert_bonuses']['support_chain_event_frequency'],3)
        self.assertEqual(info['facts']['concert_bonus_evidence']['support_chain_event_frequency']['text'],'Lvl O Lvl 3')
        update=parse(raw([line('Concert bonuses updated!')]))
        self.assertEqual(update['screen'],'concert_bonus_update')
        self.assertNotIn('current_concert_bonuses',update['facts'])

    def test_calendar_detects_missing_dates_and_duplicate_actions(self):
        rows = []
        for time, date in ((0, 'Junior Year Early Jul'), (100, 'Junior Year Early Jul'),
                           (1000, 'Junior Year Early Aug'), (1100, 'Junior Year Early Aug'),
                           (2000, 'Finale Underway')):
            r = row(time)
            r['stats']['calendar_text'] = date
            rows.append(r)
        actions = [dict(kind='training', source_timestamp_ms=t) for t in (500, 700, 1500)]
        result = audit(rows, actions)
        self.assertFalse(result['calendar_action_coverage_complete'])
        self.assertEqual(result['missing_calendar_ordinals'], [13])
        self.assertEqual(result['date_action_statuses'], {'multiple_actions': 1, 'one_action': 1})
        rows[2]['stats']['calendar_text'] = rows[3]['stats']['calendar_text'] = 'Junior Year Late Jul'
        self.assertTrue(audit(rows, actions[1:])['calendar_action_coverage_complete'])

    def test_outing_requires_recovery_and_rejects_intervening_action(self):
        events = [dict(id='e', kind='outcome', first_seen_ms=5000, evidence='e.png',
                       effects=[dict(kind='energy_change', amount=30)])]
        request = row(1000, 'outing_confirmation')
        self.assertEqual(len(outing_actions([request], events)), 1)
        self.assertEqual(outing_actions([request, row(2000, 'training_result')], events), [])
        self.assertEqual(outing_actions([request], [dict(events[0], effects=[])]), [])
        self.assertEqual(len(outing_actions([request], events + [dict(events[0], id='second', first_seen_ms=6000)])), 1)

    def test_outing_name_uses_linked_receipt_and_does_not_claim_request_click(self):
        request=row(1000,'outing_confirmation',context_title='Unselected menu label')
        event=dict(id='e',kind='outcome',first_seen_ms=5000,evidence='receipt.png',
                   context_title='Observed outing story',effects=[dict(kind='energy_change',amount=30)])
        action=outing_actions([request],[event])[0]
        self.assertEqual(action['name'],'Observed outing story')
        self.assertEqual(action['name_evidence'],['receipt.png'])
        self.assertEqual(action['request_observed_at_ms'],1000)
        self.assertEqual(action['execution_observed_at_ms'],5000)
        self.assertEqual(action['source_timestamp_ms'],5000)
        self.assertIsNone(action['click_timestamp_ms'])
        event.pop('context_title')
        event['context_title_candidate']='Unverified title'
        self.assertNotIn('name',outing_actions([request],[event])[0])

    def test_support_outing_without_energy_requires_narrative_and_next_date(self):
        rows=[row(t,screen,context_title=title) for t,screen,title in
              ((750,'outing_confirmation',None),(1000,'outing_confirmation',None),
               (2000,'unknown','Support story'),(2250,'unknown','Support story'),
               (6250,'unknown',None),(6500,'unknown',None))]
        for r in rows:r['stats']['calendar_text']='Senior Year Late Jan' if r['source_timestamp_ms']<6000 else 'Senior Year Early Feb'
        event=dict(id='e',kind='outcome',first_seen_ms=5000,last_seen_ms=5500,evidence='receipt.png',
                   context_title='Support story',effects=[dict(kind='mood_change',direction='up'),
                   dict(kind='friendship_status',name='Companion',value='maximum')])
        actions=outing_actions(rows,[event])
        self.assertEqual(len(actions),1)
        self.assertEqual(actions[0]['companion'],'Companion')
        self.assertIn('6500.png',actions[0]['evidence'])
        self.assertEqual(outing_actions(rows[:-1],[event]),[])
        self.assertEqual(outing_actions(rows[1:],[event]),[])
        self.assertEqual(outing_actions(rows,[dict(event,context_title='Unrelated')]),[])
        for screen in ('training_preview','training_result','race_result'):
            self.assertEqual(outing_actions(rows[:4]+[row(3000,screen)]+rows[4:],[event]),[])
            self.assertEqual(outing_actions(rows[:4]+[row(6000,screen)]+rows[4:],[event]),[])
        hub=row(3000);hub['stats']['values']={'speed':100}
        second=dict(hub,source_timestamp_ms=3250,evidence='3250.png')
        # The game shows the hub for a moment between the confirmation and the
        # outing's scene; with the confirmation sampled, the scene following
        # within moments and the next date after it, that is the outing.
        self.assertEqual(len(outing_actions(rows[:4]+[hub,second]+rows[4:],[event])),1)
        rows[-1]['stats']['calendar_text']='Senior Year Late Feb'
        self.assertEqual(outing_actions(rows,[event]),[])

    def test_fans_include_concert_receipts_and_unknown_is_not_zero(self):
        races = [dict(id='r1', first_seen_ms=0, last_seen_ms=100, fans=100, fans_gained=99, evidence=['a.png']),
                 dict(id='r2', first_seen_ms=1000, last_seen_ms=1100, fans=160, fans_gained=50, evidence=['b.png'])]
        events = [dict(first_seen_ms=500, effects=[dict(kind='fan_change', amount=10)], evidence='c.png')]
        self.assertEqual(fan_accounting(races, events)['statuses'], {'balanced': 1})
        self.assertEqual(fan_accounting(races, [])['statuses'], {'unresolved': 1})
        races[1]['fans_gained'] = None
        result = fan_accounting(races, events)['intervals'][0]
        self.assertEqual(result['status'], 'unknown')
        self.assertIsNone(result['supported_change'])

    def test_song_name_variants_are_one_receipt_not_two_purchases(self):
        event = dict(id='e', first_seen_ms=100, evidence='e.png', context_title='Story',
                     effects=[dict(kind='song_learned', name=n) for n in ('Make Debut!', 'Make ebut!')])
        result = song_acquisitions([event], [])
        self.assertEqual(len(result), 1)
        self.assertIsNone(result[0]['name'])
        self.assertIsNone(result[0]['performance_cost'])
        self.assertEqual(result[0]['acquisition'], 'story_event_receipt')

    def test_transaction_evaluation_penalizes_extras_and_wrong_amount(self):
        reference = dict(source_sha256='s', start_ms=0, end_ms=1000, scope='test', kinds=['lesson'],
                         transactions=[dict(kind='lesson', start_ms=100, end_ms=300, performance_cost={'dance': 10})])
        purchase = dict(id='p', source_timestamp_ms=200, performance_cost={'dance': 10}, awarded_stats={})
        report = dict(source={'sha256': 's'}, gameplay_tracking={'auxiliary_log_used':False, 'lesson_purchases': [purchase]})
        self.assertTrue(evaluate(reference, report)['passed'])
        purchase['performance_cost']['dance'] = 20
        self.assertEqual(len(evaluate(reference, report)['field_errors']), 1)
        report['gameplay_tracking']['lesson_purchases'].append(dict(purchase, id='extra', source_timestamp_ms=500))
        self.assertEqual(evaluate(reference, report)['precision'], .5)
        report['source']['sha256'] = 'different'
        with self.assertRaises(ValueError): evaluate(reference, report)

    def test_evidence_audit_detects_crop_tampering(self):
        with workspace_temp() as root:
            (root/'neural').mkdir()
            source = root/'source.mp4'
            source.write_bytes(b'test source identity')
            Image.new('RGB', (1920,1080), 'white').save(root/'frame.jpg')
            with Image.open(root/'frame.jpg') as frame:
                pane = frame.convert('RGB').crop((148,0,958,1080))
                pane.save(root/'crop.png')
            capture = dict(source=dict(sha256=hashlib.sha256(source.read_bytes()).hexdigest(), duration_ms=250),
                           frames=[dict(id='f', evidence='frame.jpg', source_timestamp_ms=0,source_pts=0,time_base='1/60')])
            raw = dict(source_timestamp_ms=0, evidence='crop.png',
                       source_frame_sha256=hashlib.sha256((root/'frame.jpg').read_bytes()).hexdigest(),
                       gameplay_sha256=hashlib.sha256(pane.tobytes()).hexdigest())
            (root/'capture.json').write_text(json.dumps(capture), encoding='utf-8')
            (root/'neural/f.json').write_text(json.dumps(raw), encoding='utf-8')
            self.assertTrue(verify(root, source)['evidence_integrity_verified'])
            pane.putpixel((0,0), (0,0,0)); pane.save(root/'crop.png')
            result = verify(root, source)
            self.assertFalse(result['evidence_integrity_verified'])
            self.assertIn('crop', result['errors'][0]['reason'])
            with self.assertRaises(ValueError): inside(root, '../outside.png')


if __name__ == '__main__':
    unittest.main()
