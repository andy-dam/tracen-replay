# Lab

Tools that audit, inventory, diagnose and regrade preserved analyses during
the analyzer's evaluation. They read the locally preserved evidence under
`.local`, which is never published, and they are not part of the analyzer
that the service runs. Nothing here is imported by `tracen_replay`; each
script is run on its own against a preserved run directory it names on the
command line. Keep them working while the evaluation records they produced
are still consulted; they carry no product behaviour.

`tests/` holds the tests that replay that same preserved evidence (real
frames, OCR caches and reports). They are kept out of the main suite so that
a clean checkout runs every main test and skips none; run them here, where
the evidence is, with `python -X utf8 -m unittest discover -s analyzer/lab/tests -t analyzer`
from the repository root. A test whose evidence has been pruned for good is
deleted rather than left to skip.
