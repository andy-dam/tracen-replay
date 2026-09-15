import hashlib
import unittest
from unittest.mock import patch

from PIL import Image
from tests.test_neural_transactions import raw, line
from tracen_replay.refine_overlay import alignment_boxes


class OverlayAlignmentSelectionTests(unittest.TestCase):
    def test_name_edge_gets_alignment_even_when_cursor_misses_line_center(self):
        pane=Image.new('RGB',(810,1080),'white')
        box=[314,828,768,862]
        source=raw([line('Friendship with Example Name went up by 5.',box)])
        source['gameplay_sha256']=hashlib.sha256(pane.tobytes()).hexdigest()
        with patch('tracen_replay.refine_overlay.overlay_boxes',return_value=[[621,846,633,863]]):
            self.assertEqual(alignment_boxes(source,pane,[]),[box])
            self.assertEqual(alignment_boxes(source,pane,[dict(line_box=box)]),[])
        self.assertEqual(source['lines'][0]['confidence'],99)

    def test_status_receipts_are_eligible_but_narrative_and_uncovered_lines_are_not(self):
        pane=Image.new('RGB',(810,1080),'white')
        source=raw([line('Friendship with Example Name is maxed out.',[300,830,750,865]),
                    line('Friendship is a wonderful thing.',[300,870,750,900]),
                    line('Power went up by 5.',[300,780,750,810])])
        source['gameplay_sha256']=hashlib.sha256(pane.tobytes()).hexdigest()
        with patch('tracen_replay.refine_overlay.overlay_boxes',return_value=[[620,850,634,880]]):
            self.assertEqual(alignment_boxes(source,pane,[]),[[300,830,750,865]])
        with patch('tracen_replay.refine_overlay.overlay_boxes',return_value=[]):
            self.assertEqual(alignment_boxes(source,pane,[]),[])

    def test_changed_pixels_are_rejected(self):
        pane=Image.new('RGB',(810,1080),'white')
        source=raw([line('Power went up by 5.')],gameplay_sha256='changed')
        with self.assertRaises(ValueError):alignment_boxes(source,pane,[])

    def test_inheritance_receipts_use_physical_overlap_not_name_similarity(self):
        pane=Image.new('RGB',(810,1080),'white')
        source=raw([line('Inspired by Seiun S!',[317,806,550,834]),
                    line('Inspired by Mihono Sourbon!',[318,836,596,863])])
        source['gameplay_sha256']=hashlib.sha256(pane.tobytes()).hexdigest()
        with patch('tracen_replay.refine_overlay.overlay_boxes',return_value=[[528,818,540,838]]):
            self.assertEqual(alignment_boxes(source,pane,[]),[[317,806,550,834]])
        with patch('tracen_replay.refine_overlay.overlay_boxes',return_value=[[528,867,540,884]]):
            self.assertEqual(alignment_boxes(source,pane,[]),[])
