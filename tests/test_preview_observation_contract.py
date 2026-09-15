from copy import deepcopy
import unittest

from tests.test_report_contract import valid_report
from tracen_replay.preview_observations import build_preview_observations
from tracen_replay.report_contract import ReportContractError, validate


def preview_report():
    report = valid_report()
    data = report['gameplay_tracking']
    data['readings'] = [dict(screen='training_preview', source_timestamp_ms=250,
        evidence='gameplay/preview.png', stats={'preview_option': 'speed'},
        facts={'training_preview_effects': [dict(kind='stat_change', field='speed', amount=7)]})]
    data['preview_observations'] = build_preview_observations(data['readings'])
    return report


class PreviewObservationContractTests(unittest.TestCase):
    def test_valid_preview_and_legacy_absence(self):
        report = preview_report()
        self.assertTrue(report['gameplay_tracking']['preview_observations']['observations'])
        validate(report, require_gameplay=True)
        del report['gameplay_tracking']['preview_observations']
        validate(report, require_gameplay=True)

    def test_cannot_change_preview_into_an_awarded_effect(self):
        original = preview_report()
        for key, value in [('phase', 'applied'), ('start_ms', 0), ('evidence', ['different.png'])]:
            report = deepcopy(original)
            report['gameplay_tracking']['preview_observations']['observations'][0][key] = value
            with self.assertRaisesRegex(ReportContractError, 'source preview facts'):
                validate(report, require_gameplay=True)
        report = deepcopy(original)
        report['gameplay_tracking']['preview_observations']['observations'][0]['payload']['amount'] = 70
        with self.assertRaisesRegex(ReportContractError, 'source preview facts'):
            validate(report, require_gameplay=True)


if __name__ == '__main__':
    unittest.main()
