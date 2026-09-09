import unittest

from tracen_replay.vision import parse
from tracen_replay.transactions import races


def line(text, box, confidence=99):
    return dict(text=text, box=list(box), confidence=confidence)


class RaceItemsTests(unittest.TestCase):
    def parse_items(self, extras):
        return parse(dict(lines=[line('Fans 1,244 (+1,243)', (270,700,600,730)), *extras],
                          regions={}, header='', current_grid=False, result_grid=False))

    def test_visible_quantity_requires_result_heading_region_and_confidence(self):
        header=line('Items',(270,751,329,779))
        quantity=line('x200',(312,863,377,890))
        parsed=self.parse_items([header,quantity])
        self.assertEqual(parsed['screen'],'race_result')
        self.assertEqual(parsed['facts']['visible_item_quantities'][0]['quantity'],200)
        self.assertIsNone(parsed['facts']['visible_item_quantities'][0]['name'])
        self.assertFalse(parsed['facts']['item_rewards_complete'])
        for extras in ([quantity], [header,line('x200',quantity['box'],96)],
                       [header,line('200',quantity['box'])],
                       [header,line('x200',(312,600,377,630))],
                       [header,line('x200',(312,970,377,995))]):
            self.assertEqual(self.parse_items(extras)['facts']['visible_item_quantities'],[])

    def row(self,time,quantity=200):
        return dict(screen='race_result',source_timestamp_ms=time,evidence=f'{time}.png',
                    facts=dict(fans=1244,fans_gained=1243,visible_item_quantities=[
                        dict(quantity=quantity,box=[312,863,377,890])]))

    def test_distinct_stable_frames_and_no_double_counting(self):
        result=races([self.row(0),self.row(250),self.row(500)])[0]
        snapshots=result['visible_item_reward_snapshots']
        self.assertEqual(len(snapshots),1)
        self.assertEqual(snapshots[0]['items'],[dict(quantity=200,name=None)])
        self.assertEqual(len(snapshots[0]['evidence']),3)
        self.assertFalse(result['item_rewards_complete'])
        for rows in ([self.row(0)], [self.row(0),self.row(0)],
                     [self.row(0),self.row(750)], [self.row(0),self.row(250,300)]):
            self.assertEqual(races(rows)[0]['visible_item_reward_snapshots'],[])

    def test_scroll_and_occlusion_break_continuity(self):
        middle=self.row(250);middle['facts']['visible_item_quantities']=[]
        self.assertEqual(races([self.row(0),middle,self.row(500)])[0]['visible_item_reward_snapshots'],[])
        moved=self.row(250);moved['facts']['visible_item_quantities'][0]['box']=[312,800,377,827]
        self.assertEqual(races([self.row(0),moved])[0]['visible_item_reward_snapshots'],[])

    def test_snapshot_preserves_per_frame_quantity_provenance(self):
        first,second=self.row(0),self.row(250)
        first['facts']['visible_item_quantities'][0].update(raw_text='x200',confidence=99)
        second['facts']['visible_item_quantities'][0].update(raw_text='x200',confidence=98)
        snapshot=races([first,second])[0]['visible_item_reward_snapshots'][0]
        self.assertEqual([x['source_timestamp_ms'] for x in snapshot['item_observations']],[0,250])
        self.assertEqual(snapshot['item_observations'][1]['evidence'],'250.png')
        self.assertEqual(snapshot['item_observations'][1]['items'][0]['confidence'],98)
        self.assertEqual(snapshot['item_observations'][0]['items'][0]['box'],[312,863,377,890])
        self.assertFalse(snapshot['identity_verified'])
        self.assertFalse(snapshot['list_complete'])


if __name__=='__main__':unittest.main()
