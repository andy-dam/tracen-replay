import copy
import unittest
from unittest.mock import patch

from tests.test_report_contract import valid_report
from tests.test_skill_menu_observations import _card_frame
from tracen_replay.full_recording import assemble
from tracen_replay.vision import parse
from tracen_replay.report_contract import validate, ReportContractError


class SkillMenuPipelineTests(unittest.TestCase):


    def test_offer_survives_assembly_and_contract_without_purchase(self):
        raw=_card_frame(name='Example Skill',cost=40)
        raw['regions']={}
        row=parse(raw)
        row.update(source_timestamp_ms=raw['source_timestamp_ms'],evidence=raw['evidence'])
        with patch('tracen_replay.full_recording.audit',return_value={}):
            report=assemble(valid_report(),[row])
        validate(report,require_gameplay=True)
        self.assertEqual(report['gameplay_tracking']['skill_purchases'],[])
        observations=report['gameplay_tracking']['skill_menu_observations']
        self.assertEqual(len(observations),1)
        self.assertEqual(observations[0]['payload']['visible_card_prices'],{'Example Skill':40})
        altered=copy.deepcopy(report)
        altered['gameplay_tracking']['skill_menu_observations'][0]['payload']['visible_card_prices']['Example Skill']=1
        with self.assertRaisesRegex(ReportContractError,'source skill menus'):
            validate(altered,require_gameplay=True)


if __name__=='__main__':
    unittest.main()
