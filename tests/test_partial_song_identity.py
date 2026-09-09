import unittest
from tracen_replay.transactions import lesson_receipts,partial_song_name
from tracen_replay.gameplay import CURRENCIES
from tests.test_neural_transactions import row, raw, line
from tracen_replay.vision import parse


class PartialSongIdentityTests(unittest.TestCase):
    def test_confirmation_title_excludes_standalone_currency_number(self):
        got=parse(raw([line('Spend performance points to learn this technique?'),
            line('35',(300,90,330,115)),line('Hoppity Sunny Days',(350,90,600,115))]))
        self.assertEqual(got['facts']['name_candidates'],['Hoppity Sunny Days'])

    def sample(self):
        before=dict.fromkeys(CURRENCIES,100);after=dict(before,passion=58,vocal=79)
        rows=[row(t,'lesson_selection',{'performance_points':before}) for t in (0,250)]
        rows += [row(t,'lesson_confirmation',{'name_candidates':['Hoppity Sunny Days'],'projected_performance_points':after}) for t in (500,750)]
        rows += [row(t,'lesson_selection',{'performance_points':after}) for t in (2000,2250)]
        event=dict(id='e',first_seen_ms=1000,last_seen_ms=1500,evidence='receipt.png',
                   effects=[dict(kind='song_learned',name='Hoppity Sunny Days D')],deltas={})
        return rows,event

    def test_observed_charge_does_not_claim_suffix_identity(self):
        rows,event=self.sample();got=lesson_receipts(rows,[event])
        self.assertEqual(len(got),1)
        p=got[0]
        self.assertEqual(p['performance_cost'],dict(dance=0,passion=42,vocal=21,visual=0,composure=0))
        self.assertEqual(p['name_match_basis'],'partial_name_and_repeated_observed_debit')
        self.assertEqual(p['requested_name'],'Hoppity Sunny Days')
        self.assertEqual(p['receipt_name'],'Hoppity Sunny Days D')
        self.assertFalse(p['name_identity_verified'])
        self.assertFalse(p['complete_transaction_verified'])

    def test_partial_name_requires_repeated_balances_and_no_canceled_return(self):
        rows,event=self.sample()
        self.assertEqual(lesson_receipts(rows[1:],[event]),[])
        self.assertEqual(lesson_receipts(rows[:-1],[event]),[])
        self.assertEqual(lesson_receipts(rows[:3]+rows[4:],[event]),[])
        canceled=rows[:4]+[row(t,'lesson_selection') for t in (800,900)]+rows[4:]
        self.assertEqual(lesson_receipts(canceled,[event]),[])
        changed=rows[:4]+[row(900,'lesson_confirmation',{'name_candidates':['Different Song']})]+rows[4:]
        self.assertEqual(lesson_receipts(changed,[event]),[])
        rows[-1]['facts']['performance_points']=dict.fromkeys(CURRENCIES,100)
        self.assertEqual(lesson_receipts(rows,[event]),[])

    def test_name_rule_rejects_other_titles_sequels_and_plural_changes(self):
        self.assertTrue(partial_song_name('Present March','Present March♪'))
        for other in ('Different Song','Hoppity Sunny Days 2','Hoppity Sunny Days Deluxe','Hoppity Sunny Days'):
            self.assertFalse(partial_song_name('Hoppity Sunny Days',other))
        self.assertFalse(partial_song_name('Lovely Song','Lovely Songs'))
        rows,event=self.sample();event['effects'][0]['kind']='named_acquisition'
        self.assertEqual(lesson_receipts(rows,[event]),[])

    def test_source_symbol_alias_preserves_debit_without_claiming_request_identity(self):
        from copy import deepcopy
        rows,event=self.sample()
        for item in rows:
            if item['screen']=='lesson_confirmation':item['facts']['name_candidates']=['Hoppity Sunny Days D']
        original='Learned the song "Hoppity Sunny Days D".'
        effect=event['effects'][0]
        effect.update(name='Hoppity Sunny Days ♪',original_text=original,
            visual_symbol_observation={'method':'strict_note_and_independently_read_title',
                'title_evidence':{'title':'Hoppity Sunny Days','line':{'text':original}}})
        purchases=lesson_receipts(rows,[event])
        self.assertEqual(len(purchases),1)
        self.assertEqual(purchases[0]['performance_cost'],dict(dance=0,passion=42,vocal=21,visual=0,composure=0))
        self.assertEqual(purchases[0]['requested_name'],'Hoppity Sunny Days D')
        self.assertEqual(purchases[0]['receipt_name'],'Hoppity Sunny Days ♪')
        self.assertEqual(purchases[0]['name_match_basis'],'source_symbol_alias_and_repeated_observed_debit')
        self.assertFalse(purchases[0]['name_identity_verified'])
        rows[3]['facts']['name_candidates']=['Hoppity Sunny Days']
        mixed=lesson_receipts(rows,[event])
        self.assertEqual(len(mixed),1)
        self.assertEqual(mixed[0]['observed_request_names'],['Hoppity Sunny Days','Hoppity Sunny Days D'])
        self.assertEqual(mixed[0]['performance_cost'],purchases[0]['performance_cost'])
        rows[3]['facts']['name_candidates']=['Hoppity Sunny Days D']
        self.assertEqual(lesson_receipts(rows[1:],[event]),[])
        self.assertEqual(lesson_receipts(rows[:-1],[event]),[])
        for mutation in ('proof','title','original'):
            changed=deepcopy(event);item=changed['effects'][0]
            if mutation=='proof':item.pop('visual_symbol_observation')
            elif mutation=='title':item['visual_symbol_observation']['title_evidence']['title']='Different Song'
            else:item['original_text']='Learned the song "Different Song D".'
            self.assertEqual(lesson_receipts(rows,[changed]),[],mutation)
