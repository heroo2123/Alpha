"""Pure offline primitive: the latest READY run as of a caller-supplied lower
UTC bound.

Extracted, verbatim in behavior, from the identical inline computation that
previously lived separately inside ``validate_manifest``
(``tools/v11_r09_gate3_launch.py``) and ``validate_manifest_v4``
(``tools/v11_r09_gate3_launch_v4.py``), so both validators -- and any future
caller -- share one reviewed definition instead of two copies of the same
list comprehension. This module has no transport, decoder, credential,
clock-of-record, or launch entrypoint: ``lower_utc`` is always a value the
caller already computed/validated, never a live clock read here.

It performs exactly the selection the two validators already assert and
nothing more: the largest ``run_utc`` among rows whose ``status`` is
``'READY'`` and whose ``ready_upper_utc`` is at or before ``lower_utc``.
There is no ``max_run_age_seconds`` (or any other age-gate) parameter here,
deliberately -- the manifests' own ``RUN_POLICY`` check pins that field to a
fixed literal and never feeds it into this selection, and this extraction
must not change that. This function also performs no shape/type validation
of its own; that remains the validators' job (``RUN_CANDIDATE_SCHEMA`` /
``RUN_CANDIDATE_VALUE`` / ``RUN_CANDIDATES``), applied before this function
is ever called. A result from this function is only a candidate for the
same exact-digest review as the rest of a manifest; it grants no G3-L
identity, qualification, or launch credit.
"""
from __future__ import annotations


def latest_ready_run(inventory, lower_utc):
    """Return the largest ``run_utc`` among READY rows with
    ``ready_upper_utc <= lower_utc``, or ``None`` if no row qualifies.

    ``inventory`` is an iterable of candidate rows for a single provider,
    each already shaped like ``{'run_utc': int, 'status': str,
    'ready_upper_utc': int, ...}`` by the caller's own validation. Ties
    (more than one qualifying row sharing the same maximal ``run_utc``) and
    input order are both immaterial: the result depends only on the set of
    qualifying ``run_utc`` values.
    """
    eligible = [row['run_utc'] for row in inventory
                if row['status'] == 'READY' and row['ready_upper_utc'] <= lower_utc]
    return max(eligible) if eligible else None
