"""Split the collected suite into disjoint shards, one per CI runner.

CI runs the suite as several parallel jobs (plan step ``bank_import:X-gy``,
ledger BI-496).  Each job exports ``SHEKEL_TEST_SHARD=<index>/<total>`` and
``tests/conftest.py``'s collection hook hands its items to :func:`apply_shard`,
which keeps only this shard's share and reports the rest as deselected.  Unset,
nothing is deselected: a local run is the whole suite, as it always was.

**The shards partition the suite BY CONSTRUCTION.**  An item's shard is a
function of that item alone -- a stable hash of its node id -- never its
position in a list.  A position-based split (round-robin over sorted ids) is only a
partition while every job collects the identical list; one extra item in one
job shifts every index after it, and the shift both runs some tests twice and
runs others nowhere, with every job green.  Here an item the other jobs do not
collect lands in exactly one shard anyway, and no other item moves.

**No test needs another on its shard** (plan#2954).  Each test's rows are in
its own clone of the template, so any item may run in any shard.  This module
once kept each ``xdist_group`` whole on one shard; the suite has none now.

**The hash is SHA-256, not ``hash()``.**  Python salts ``str`` hashing per
process (``PYTHONHASHSEED``), so ``hash()`` would give every CI job a different
split of the same suite.  Every job must compute the same assignment.

Measured 2026-09-22 on the hosted runner: a prototype of this module split the
15,166 items its tree collected into six shards of 2,429-2,602, whose union was
the collection exactly with no item in two (run 35808935180); this module's own
six shards then ran 2,443-2,612 each, 15,236 in all, exactly the local
collection of its tree (run 35815066395).
"""
import hashlib

import pytest

#: The environment variable a CI job sets to ``<index>/<total>``.
SHARD_ENV = "SHEKEL_TEST_SHARD"


def parse_shard(spec):
    """Read ``"<index>/<total>"`` as ``(index, total)``, or refuse it.

    A malformed value is a usage error rather than "no sharding": silently
    running the whole suite, or nothing, in a job that asked for one share
    would be the failure this module exists to make impossible.

    Args:
        spec: The environment variable's value, e.g. ``"0/6"``.

    Returns:
        ``(index, total)`` with ``0 <= index < total``.

    Raises:
        pytest.UsageError: The value is not two decimal integers separated by
            ``/``, or ``total`` is not positive, or ``index`` is out of range.
    """
    parts = spec.split("/")
    if len(parts) != 2 or not all(part.isdecimal() for part in parts):
        raise pytest.UsageError(
            f"{SHARD_ENV}={spec!r} is not '<index>/<total>' (for example '0/6')"
        )
    index, total = int(parts[0]), int(parts[1])
    if total < 1 or index >= total:
        raise pytest.UsageError(
            f"{SHARD_ENV}={spec!r} names shard {index} of {total}; the index "
            f"must be 0 to {total - 1}"
        )
    return index, total


def shard_of(key, total):
    """Return the shard, in ``range(total)``, that ``key`` belongs to.

    Args:
        key: An item's node id.
        total: The number of shards.

    Returns:
        The shard index.
    """
    digest = hashlib.sha256(key.encode("utf-8")).digest()
    return int.from_bytes(digest[:8], "big") % total


def apply_shard(config, items, spec):
    """Keep this shard's items in ``items`` and report the rest as deselected.

    Called from ``tests/conftest.py``'s ``pytest_collection_modifyitems``.

    Args:
        config: The pytest config, whose hook reports the deselection.
        items: The collected items; narrowed IN PLACE, as pytest requires.
        spec: The ``SHEKEL_TEST_SHARD`` value, or ``None`` when unset, in
            which case nothing is deselected.
    """
    if spec is None:
        return
    index, total = parse_shard(spec)
    keep, drop = [], []
    for item in items:
        (keep if shard_of(item.nodeid, total) == index else drop).append(item)
    if drop:
        config.hook.pytest_deselected(items=drop)
        items[:] = keep
