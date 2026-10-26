"""The fixtures every part of this package shares: the module-level fixtures of
``tests/test_integration/test_posting_ledger_loan_reconciliation.py``, moved verbatim by
``tools/split``.  A fixture here reaches exactly the tests that module held, at the scope it
declared.
"""

from __future__ import annotations

import pytest

from tests._test_helpers import freeze_today

from ._helpers import _AS_OF


@pytest.fixture(autouse=True)
def _freeze_today(monkeypatch):
    """Freeze today to 2026-05-15 so the wiring's and resolver's as-of is fixed.

    ``create_settled_transfer`` fires the Commit-5 wiring, which syncs as of
    ``date.today()``; the parallel run resolves as of the same date.  2026-05-15
    is after every payment period used (P1/P2/P3 in Jan-Mar), so each settled
    payment is historical regardless of the wall-clock date.
    """
    freeze_today(monkeypatch, _AS_OF)
