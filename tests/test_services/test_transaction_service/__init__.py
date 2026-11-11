"""
Shekel Budget App -- Transaction Service Tests

Tests for ``app.services.transaction_service`` -- the cross-cutting
helpers used by both the manual ``mark_done`` route and the
carry-forward envelope branch (Phase 4 of
``docs/carry-forward-aftermath-implementation-plan.md``).

Each test verifies exact Decimal values and explicit arithmetic so a
reviewer can recompute the expected values by hand.  Tests are
deliberately kept small and single-purpose so a regression at any
contract boundary surfaces with a precise failure message.
"""
