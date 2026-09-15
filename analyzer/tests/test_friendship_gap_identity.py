"""Unknown recipient slots survive mixed-cadence receipt OCR gaps."""
import copy
import unittest
from tracen_replay.receipt_names import flag_friendship_identity_conflicts


class FriendshipGapIdentityTests(unittest.TestCase):
    def fixture(self,end=1350):
        box=[315,830,707,861]
        def line(text,location=None):return dict(text=text,confidence=99,box=location or box.copy())
        first=dict(kind='friendship_change',name='Example Person',amount=5,
                   raw_text='Friendship with Example Person went up by 5.')
        last=dict(first,name='Example Persn',raw_text='Friendship with Example Persn went up by 5.')
        shared=line('Gained 3 hint level(s) for Example Skill.',[315,802,707,826])
        rows={name:dict(source_timestamp_ms=time,evidence=name,screen='event_outcome',facts={},effects=[effect],
                       ocr=dict(neural=[line(effect['raw_text']),copy.deepcopy(shared)]))
              for name,time,effect in [('a.png',1000,first),('b.png',end,last)]}
        middle=line('Friendship with Example Persn e up by 5.')
        rows['gap.png']=dict(source_timestamp_ms=1125,evidence='gap.png',screen='unknown',facts={},effects=[],
                            ocr=dict(neural=[middle,copy.deepcopy(shared)]))
        gap=dict(source_timestamp_ms=1125,evidence='gap.png',raw_texts=[middle['text'],shared['text']],
                 basis='matching_named_amount_receipt_ocr_gap',accepted_as_effect=False)
        event=dict(effects=[first,last],field_evidence={'friendship_change||Example Person':['a.png'],
                   'friendship_change||Example Persn':['b.png']},conflicting_readings=[],receipt_continuity_evidence=[gap])
        return event,rows

    def test_explicit_stationary_gap_extends_identity_uncertainty(self):
        for end in (1267,1350,1500):
            event,rows=self.fixture(end)
            flag_friendship_identity_conflicts(event,rows)
            with self.subTest(end=end):
                self.assertEqual(event['effects'],[])
                self.assertEqual(len(event['ambiguous_effect_candidates']),2)
                self.assertTrue(all(c['receipt_gap_bridge_evidence']==['gap.png'] for c in event['conflicting_readings']))

    def test_missing_or_unproven_gap_cannot_establish_identity(self):
        for mutation in ('no_declaration','no_row','bad_basis','accepted','wrong_timestamp','low_confidence','wrong_amount','different_slot','other_screen','narrative','different_award','scroll'):
            event,rows=self.fixture();gap=event['receipt_continuity_evidence'][0];row=rows['gap.png'];line=row['ocr']['neural'][0]
            if mutation=='no_declaration':event['receipt_continuity_evidence']=[]
            elif mutation=='no_row':del rows['gap.png']
            elif mutation=='bad_basis':gap['basis']='guess'
            elif mutation=='accepted':gap['accepted_as_effect']=True
            elif mutation=='wrong_timestamp':gap['source_timestamp_ms']=1150
            elif mutation=='low_confidence':line['confidence']=94
            elif mutation=='wrong_amount':
                line['text']=line['text'].replace('by 5','by 6');gap['raw_texts'][0]=line['text']
            elif mutation=='different_slot':line['box'][1]+=40;line['box'][3]+=40
            elif mutation=='other_screen':row['screen']='training_preview'
            elif mutation=='narrative':
                line['text']='Friendship with her was challenging today.';gap['raw_texts'][0]=line['text']
            elif mutation=='different_award':row['effects']=[dict(kind='energy_change',amount=5)]
            elif mutation=='scroll':
                row['ocr']['neural'][1]['box'][1]+=24;row['ocr']['neural'][1]['box'][3]+=24
            flag_friendship_identity_conflicts(event,rows)
            with self.subTest(mutation=mutation):
                self.assertEqual(len(event['effects']),2)
                self.assertEqual(event['conflicting_readings'],[])

    def test_long_absence_and_simultaneous_people_stay_separate(self):
        event,rows=self.fixture(1501)
        flag_friendship_identity_conflicts(event,rows)
        self.assertEqual(len(event['effects']),2)
        event,rows=self.fixture()
        event['field_evidence']['friendship_change||Example Persn'].append('a.png')
        rows['a.png']['ocr']['neural'].append(copy.deepcopy(rows['b.png']['ocr']['neural'][0]))
        flag_friendship_identity_conflicts(event,rows)
        self.assertEqual(len(event['effects']),2)


if __name__=='__main__':unittest.main()
