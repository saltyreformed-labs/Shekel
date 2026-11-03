"""Tests for commit C-18: stale-form prevention across every PATCH endpoint.

Mirrors the structure of the C-17 tests in test_accounts/test_stale_data.py:
schema invariants, lifecycle behaviour, true concurrent races, and per-route
stale-form / StaleDataError handling.  Every model that grew a
``version_id`` column in commit C-18 is exercised against the same
contract:

  1. The live database carries a NOT NULL ``version_id`` column with
     a positive CHECK constraint.
  2. The mapper declares ``version_id_col`` so SQLAlchemy narrows
     UPDATE/DELETE statements with ``WHERE version_id = ?``.
  3. Pure SELECT operations leave ``version_id`` unchanged; UPDATE
     bumps it by exactly one.
  4. A truly concurrent commit that bumps the row out from under
     the test session raises :class:`StaleDataError`.
  5. PATCH/POST endpoints that ship the row's ``version_id`` reject
     stale submissions.  HTMX endpoints render a 409 conflict cell;
     full-page form endpoints flash + redirect.
  6. Edit-form templates ship a hidden ``version_id`` input set to
     the current row's counter so the round-trip closes.

Together, these tests are the load-bearing automated check that
the optimistic-lock contract holds end-to-end.  Audit reference:
F-010 (High) / commit C-18 of the 2026-04-15 security remediation
plan.
"""
