import copy
import unittest
from tracen_replay.inspect_missing_results import windows,validate_capture
from tracen_replay.pipeline import PipelineError


def report():
    rows=[dict(source_timestamp_ms=t,screen=screen,evidence=f'{t}.png') for t,screen in
          ((1000,'training_preview'),(1250,'training_preview'),(1500,'unknown'),
           (1750,'unknown'),(2000,'event_outcome'),(2250,'training_result'))]
    return dict(source=dict(duration_ms=5000),gameplay_tracking=dict(readings=rows,
        events=[dict(id='empty',kind='training',first_seen_ms=2250,last_seen_ms=2400,
                     training_option=None,deltas={},performance_deltas={})],
        intervals=[dict(start_ms=1000,end_ms=4000,status='unresolved')]))


class MissingResultWindowTests(unittest.TestCase):
    def test_prior_preview_extends_inspection_without_claiming_an_action(self):
        source=report();original=copy.deepcopy(source)
        result=windows(source)
        self.assertEqual([(w['start_ms'],w['end_ms']) for w in result],[(750,2900)])
        self.assertFalse(result[0]['action_verified'])
        self.assertEqual(result[0]['witnesses'][0]['preview_evidence'],'1250.png')
        self.assertEqual(source,original)

    def test_browsing_or_a_known_action_does_not_create_a_recovery(self):
        for change in ('no_result','no_preview','already_resolved','known_option','known_gains','prior_action',
                       'race_confirmation','rest_confirmation','outing_confirmation','lesson_selection','skill_selection'):
            source=report();data=source['gameplay_tracking']
            if change=='no_result':data['events']=[]
            elif change=='no_preview':data['readings']=data['readings'][2:]
            elif change=='already_resolved':data['intervals'][0]['status']='balanced'
            elif change=='known_option':data['events'][0]['training_option']='guts'
            elif change=='known_gains':data['events'][0]['deltas']={'speed':8}
            elif change=='prior_action':data['events'].insert(0,dict(id='observed',kind='training',training_option='wit',first_seen_ms=1700,last_seen_ms=1800,deltas={}))
            else:data['readings'][2]['screen']=change
            with self.subTest(change=change):self.assertEqual(windows(source),[])

    def test_bad_source_order_is_rejected(self):
        for change in ('duplicate','reverse','negative'):
            source=report();rows=source['gameplay_tracking']['readings']
            if change=='duplicate':rows[1]['source_timestamp_ms']=rows[0]['source_timestamp_ms']
            elif change=='reverse':rows.reverse()
            else:rows[0]['source_timestamp_ms']=-1
            with self.subTest(change=change),self.assertRaises(ValueError):windows(source)

    def test_long_gaps_are_not_bridged(self):
        source=report();source['source']['duration_ms']=12000
        data=source['gameplay_tracking'];data['events'][0].update(first_seen_ms=10000,last_seen_ms=10250)
        data['intervals'][0]['end_ms']=11000
        self.assertEqual(windows(source),[])

    def test_invalid_event_bounds_are_rejected(self):
        for first,last in ((-1,100),(100,99),(100,5000),(True,100),(100,float('nan'))):
            source=report();source['gameplay_tracking']['events'][0].update(first_seen_ms=first,last_seen_ms=last)
            with self.subTest(first=first,last=last),self.assertRaises(ValueError):windows(source)

    def test_stat_interval_boundaries_match_accounting_contract(self):
        source=report();gap=source['gameplay_tracking']['intervals'][0]
        gap['end_ms']=2250
        self.assertEqual(len(windows(source)),1)
        gap.update(start_ms=2250,end_ms=4000)
        self.assertEqual(windows(source),[])

    def test_capture_clock_and_frame_paths_are_validated(self):
        frame=dict(id='frame-000001',evidence='frames/000001.jpg',source_timestamp_ms=1000,
                   source_pts=60,time_base='1/60')
        window=dict(start_ms=1000,end_ms=2000)
        validate_capture([frame],window,0)
        for field,value in (('source_timestamp_ms',999),('source_timestamp_ms',2000),
                            ('source_pts',61),('time_base','0/60'),('id','../escape'),
                            ('evidence','../frames/000001.jpg')):
            with self.subTest(field=field,value=value),self.assertRaises(PipelineError):
                validate_capture([dict(frame,**{field:value})],window,0)
        with self.assertRaises(PipelineError):validate_capture([frame,frame],window,0)

    def test_unsorted_events_produce_ordered_bounded_windows(self):
        source=report();source['source']['duration_ms']=15000;data=source['gameplay_tracking']
        data['intervals'][0]['end_ms']=14000
        data['readings'].extend([dict(source_timestamp_ms=6000,screen='training_preview',evidence='6000.png')])
        data['events'].insert(0,dict(id='later',kind='training',first_seen_ms=10000,last_seen_ms=10500,deltas={}))
        result=windows(source)
        self.assertEqual([r['start_ms'] for r in result],[750,5500])
        self.assertTrue(all(r['end_ms']-r['start_ms']<=8000 for r in result))


if __name__=='__main__':unittest.main()
