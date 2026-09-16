# Weather PAPER host authority v2

This directory is **reference/review source**, not candidate deployment code.  The
application candidate is forbidden from installing or replacing host authority.

Bootstrap trust is anchored outside the candidate: obtain the reviewed `authority.py`
through a separately controlled channel, record its SHA-256 independently, verify that
digest with the host `/usr/bin/sha256sum`, then run `bootstrap-host-authority.sh` with
that independently supplied digest.  The installed root-owned anchor stores that
digest.  Ordinary releases can submit candidate SHA/tree/environment data, but cannot
change `/usr/local/libexec/polymarket-weather-paper-v2/authority.py` or its anchor.

Cutovers use immutable generation-specific directories.  `create-cutover` captures the
predecessor before candidate mutation and creates one root-owned active generation.
A second snapshot is rejected while a cutover is active.  Candidate environment
sealing is a separate one-shot record inside that generation.  Recovery takes an
explicit generation ID and restores only the predecessor bound into that generation.
