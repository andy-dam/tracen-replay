import hashlib
import unittest
from tracen_replay.refine_choices import apply
from tracen_replay.refine_contrast import fingerprint
from tracen_replay.choice_evidence import reconstruct
from tests import test_choice_evidence as choice_tests


class ChoiceProvenanceTests(unittest.TestCase):
    def test_tampered_raw_or_proof_rejected(self):
        from unittest.mock import Mock
        p=Mock();p.read_bytes.return_value=b'proof'
        raw={'original':'ocr'};extra=dict(version=1,raw_sha256=fingerprint(raw),evidence_sha256=hashlib.sha256(p.read_bytes()).hexdigest(),observation={'offered_card_candidates':[]})
        self.assertIn('choice_observation',apply({'screen':'unknown'},raw,extra,p)['facts'])
        self.assertNotIn('facts',apply({'screen':'training_preview'},raw,extra,p))
        with self.assertRaises(ValueError):apply({'screen':'unknown'},{'changed':True},extra,p)
        p.read_bytes.return_value=b'changed'
        with self.assertRaises(ValueError):apply({'screen':'unknown'},raw,extra,p)

    def test_known_screen_interrupts_menu_association(self):
        rows=choice_tests.ChoiceEvidenceTests().observations()
        rows.insert(2,dict(source_timestamp_ms=350,screen_boundary=True))
        self.assertEqual(reconstruct(rows),[])
