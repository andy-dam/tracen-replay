import unittest
from tracen_replay.choice_evidence import reconstruct
from tests import test_choice_evidence as choice_tests


class ChoiceProvenanceTests(unittest.TestCase):

    def test_known_screen_interrupts_menu_association(self):
        rows=choice_tests.ChoiceEvidenceTests().observations()
        rows.insert(2,dict(source_timestamp_ms=350,screen_boundary=True))
        self.assertEqual(reconstruct(rows),[])
