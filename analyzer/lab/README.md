# Lab

Work that needs files under `.local`, which is never published. Nothing here
is part of the analyzer the service runs, and nothing here is imported by
`tracen_replay`.

`learn_confusions.py` learns the recognizer's character confusions from
analyzed careers and writes `tracen_replay/data/confusions.json`
(see [docs/evaluation.md](../../docs/evaluation.md)).

`tests/` holds the test that needs the installed OCR models in
`.local/models/rapidocr` (the reader's fingerprint). It is kept out of the
main suite so that a clean checkout runs every main test and skips none; run
it with `python -X utf8 -m unittest discover -s analyzer/lab/tests -t analyzer`
from the repository root. A test whose local files are gone for good is
deleted rather than left to skip.
