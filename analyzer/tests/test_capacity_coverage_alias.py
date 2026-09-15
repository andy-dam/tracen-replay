import unittest

from tools.evaluate_final_reliability import _coverage_allows


class CapacityCoverageAliasTests(unittest.TestCase):
    def test_capacity_alias_preserves_frozen_scope_without_energy_recovery(self):
        coverage = dict(explicit_time=True, categories={'effect'},
                        intervals_ms=[[0, 100]], fields={'max_energy_change'})
        row = dict(category='effect', start_ms=20, end_ms=20,
                   payload=dict(kind='stat_cap_change', field='energy', amount=4))
        self.assertTrue(_coverage_allows(row, coverage))
        row['payload'] = dict(kind='energy_change', field='energy', amount=4)
        self.assertFalse(_coverage_allows(row, coverage))
        row['payload'] = dict(kind='stat_cap_change', field='speed', amount=4)
        self.assertFalse(_coverage_allows(row, coverage))
        row['payload'] = dict(kind='stat_cap_change', field='energy', amount=4)
        row['start_ms'] = row['end_ms'] = 101
        self.assertFalse(_coverage_allows(row, coverage))


if __name__ == '__main__':
    unittest.main()
