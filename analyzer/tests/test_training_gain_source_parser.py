import unittest

from tests import localdata


FIXTURE = localdata.root("final_reliability_artifacts", "training-gain-source-fixtures.json")


def raw_training(observation):
    return dict(
        lines=observation['raw_lines'],
        regions=observation['raw_regions'],
        header='Training',
        current_grid=False,
        result_grid=True,
        inspection='training_result_only',
    )


if __name__ == '__main__':
    unittest.main()

