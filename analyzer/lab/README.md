# Lab

Work that needs the locally preserved evidence under `.local`, which is never
published. Nothing here is part of the analyzer the service runs, and nothing
here is imported by `tracen_replay`.

`learn_confusions.py` learns the recognizer's character confusions from
analyzed careers and writes `tracen_replay/data/confusions.json`
(see [docs/evaluation.md](../../docs/evaluation.md)).

`tests/` holds the tests that replay that same preserved evidence (real
frames, OCR caches and reports). They are kept out of the main suite so that
a clean checkout runs every main test and skips none; run them here, where
the evidence is, with `python -X utf8 -m unittest discover -s analyzer/lab/tests -t analyzer`
from the repository root. A test whose evidence has been pruned for good is
deleted rather than left to skip.
