import unittest
from tracen_replay.gameplay import effects_from_lines
from tracen_replay.receipt_names import collapse_song_variants


class ReceiptEffectTests(unittest.TestCase):
    def test_unlock_and_announcement_are_not_supporter_counts(self):
        effects=effects_from_lines([dict(text=t,confidence=99) for t in
            ('New supporters joined!','Light Hello will now appear in training.','Oguri Cap joined your cause!')])
        self.assertEqual([e['kind'] for e in effects],['supporters_joined_announcement','training_appearance_unlocked','supporter_joined'])
        self.assertIsNone(effects[0]['amount']);self.assertIsNone(effects[1]['amount'])

    def names(self):
        return dict(effects=[dict(kind='song_learned',name=n) for n in ('Make Debut!','Make ebut!')],
            field_evidence={'song_learned||Make Debut!':['a','b','c'],'song_learned||Make ebut!':['d']})

    def test_interleaved_single_missing_character_preserves_original_candidate(self):
        event=self.names();collapse_song_variants(event,dict(a=0,b=500,c=1000,d=750))
        self.assertEqual(len(event['effects']),1)
        self.assertEqual(event['effects'][0]['observed_name_candidates'],['Make Debut!','Make ebut!'])

    def test_separate_acquisitions_or_repeated_alternatives_remain_separate(self):
        event=self.names();collapse_song_variants(event,dict(a=0,b=500,c=1000,d=1500));self.assertEqual(len(event['effects']),2)
        event=self.names();collapse_song_variants(event,dict(a=0,b=500,c=1000,d=1250));self.assertEqual(len(event['effects']),1)
        event=self.names();event['field_evidence']['song_learned||Make ebut!'].append('e')
        collapse_song_variants(event,dict(a=0,b=500,c=1000,d=750,e=800));self.assertEqual(len(event['effects']),2)
        event=self.names();event['effects'][1]['name']='Make Rebut!'
        collapse_song_variants(event,dict(a=0,b=500,c=1000,d=750));self.assertEqual(len(event['effects']),2)
