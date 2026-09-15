# Lab

Tools that audit, inventory, diagnose and regrade preserved analyses during
the analyzer's evaluation. They read the locally preserved evidence under
`.local`, which is never published, and they are not part of the analyzer
that the service runs. Nothing here is imported by `tracen_replay`; each
script is run on its own against a preserved run directory it names on the
command line. Keep them working while the evaluation records they produced
are still consulted; they carry no product behaviour.
