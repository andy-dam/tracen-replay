import copy
import json
import unittest
from pathlib import Path

from tests import localdata
from tracen_replay.transactions import training_events
from tracen_replay.evaluation_adapters import report_document


REPORT = localdata.root("full_worker_candidate_batch_reports", "independent-01/report.json")


if __name__ == '__main__':
    unittest.main()
