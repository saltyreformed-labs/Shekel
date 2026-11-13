"""
Shekel Budget App -- Transaction Service Tests

Tests for ``app.services.transaction_service``: what settling a row
means, the route layer's one status door, what a revert keeps, and the
delete door's owner check.  Each part's module docstring names which of
these it grades.  The package began as one helper,
``settle_from_entries``, shared by the ``mark_done`` route and the
carry-forward envelope branch (Decision G of
``docs/historical/carry-forward-aftermath-implementation-plan.md``, a
record that governs nothing).

Each test verifies exact Decimal values and explicit arithmetic so a
reviewer can recompute the expected values by hand.  Tests are
deliberately kept small and single-purpose so a regression at any
contract boundary surfaces with a precise failure message.
"""
