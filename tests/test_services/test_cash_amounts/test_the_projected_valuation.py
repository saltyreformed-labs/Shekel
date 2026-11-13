"""What a still-projected row contributes, by the rule that prices it."""

from decimal import Decimal

import pytest

from app.exceptions import AmountUnresolvable
from app.services.cash_ledger import _amounts
from app.services.cash_ledger._amounts import _entry_aware_amount, contribution_of

from ._helpers import _basis, _FakeRow


class TestTheProjectedValuation:
    """What a still-PROJECTED row contributes, by the rule that prices it.

    **This class was ``TestTheLiveOverride`` until plan step X-au-d**, and the
    rename is the change: there is no override.  A projected salary paycheck
    still reflects the CURRENT salary profile rather than a figure a later
    profile, calibration or code change invalidated -- but it does so because
    the profile is the ONLY producer of that figure, not because a read-time
    repair lays one over a stored copy.  ``income_amount``, which consumed that
    repair, collapsed onto :func:`contribution_of` and was deleted rather than
    kept as a second spelling (``CLAUDE.md`` rule 14).

    The cases below are the same three claims restated against the model that
    replaced the seam, and the THIRD is a behaviour change rather than a
    rewording: an unanswered salary row used to fall back to its stored figure
    and now REFUSES, because there is no figure to fall back to.

    Moved from ``TestIncomeOverridesSeam`` (X-c2c2a) and from
    ``test_balance_resolver.py`` (the expense leg's precedence and the guard
    ordering, which arrived there at plan step X-c2c1).  The fourth
    ``TestIncomeOverridesSeam`` test did NOT move: it pins that the override is
    honoured in the POST-ANCHOR period specifically, which is a ``_calculator``
    branch rather than a valuation rule.
    """

    def test_a_declared_paycheck_contributes_its_PROFILES_net(self, app):
        """A salary row is worth what its profile pays for that period.

        The valuation reaches amount rule 2, which asks the pass's salary
        derivation.  The discriminator is the row's own column: a declared row
        holds none, so a valuation reading it would answer ``None`` rather than
        a figure.
        """
        with app.app_context():
            row = _FakeRow(
                txn_id=101, effective_amount="2000.00",
                salary_shaped=True, pay_period_id=7,
            )
            basis = _basis(row, overrides={(101, 7): Decimal("2473.38")})

            assert row.estimated_amount is None
            assert contribution_of(row, basis) == Decimal("2473.38")

    def test_a_row_that_OWNS_its_figure_contributes_that_figure(self):
        """Rule 1, and the non-vacuity partner for the case above.

        Without it, a valuation that answered the salary derivation for EVERY
        row would pass -- and that valuation prices a haircut at a paycheck.
        """
        row = _FakeRow(txn_id=101, effective_amount="2000.00")
        basis = _basis(row)

        assert contribution_of(row, basis) == Decimal("2000.00")

    def test_a_paycheck_the_projection_does_not_cover_is_REFUSED(self, app):
        """There is no fallback left, and that is plan step X-au-d's point.

        The derivation answers for ``(999, 7)`` and this row is ``(101, 7)``.
        Before the cutover such a row kept its stored figure -- which is
        precisely the stale cache ruling **R-FI** deletes -- and a valuation
        that still substituted one would be publishing it.  A refusal is never
        a fallback, so the rule raises and names the row.
        """
        with app.app_context():
            row = _FakeRow(
                txn_id=101, effective_amount="2000.00",
                salary_shaped=True, pay_period_id=7,
            )
            basis = _basis(row, overrides={(999, 7): Decimal("5.00")})

            with pytest.raises(
                AmountUnresolvable, match="live recompute answered nothing",
            ):
                contribution_of(row, basis)

    # ``test_an_override_wins_over_the_entry_formula`` lived here until plan
    # step X-au-g-2c-2, and the RULE it graded is deleted rather than the test
    # being weakened.  It asserted that on the EXPENSE leg a live-derived
    # amount short-circuits the three-bucket reservation, returning $123.45
    # verbatim instead of the $50.00 the entry formula gives.
    #
    # The seam had two halves and the LOAN one was the only one an expense row
    # could ever take -- a loan payment's cash debit sits on the funding
    # account.  That half went first: a transfer shadow is DERIVED, so there is
    # no stored figure for an override to supersede.  Plan step **X-au-d** then
    # took the salary half and the seam with it, and ``_expense_amount`` -- a
    # one-line forward that existed only for symmetry with ``income_amount`` --
    # went too.  ``sum_projected``'s expense leg calls
    # ``_entry_aware_amount`` directly now, which is exactly what
    # ``test_the_reservation.py``'s ``TestTheEntryAwareReservation`` grades, so
    # the successor case this
    # note used to point at has no separate subject left and is deleted with
    # the wrapper.  A test whose only possible outcome is a pass is not a test
    # (finding **N-184**'s rule).

    def test_no_entries_short_circuits_before_the_status_read(
        self, monkeypatch,
    ):
        """The guard ORDER holds: no entries returns before ``is_projected``.

        ``_entry_aware_amount`` checks ``not entries`` FIRST, so an entry-less
        row is valued without the status read the entry-aware branch needs.
        That is load-bearing rather than stylistic: ``is_projected`` resolves a
        ``ref_cache`` id, which is work an entry-less row has no reason to pay.

        **It is graded by a SPY rather than by a missing attribute** (plan step
        X-au-c3).  The proof used to be that ``_FakeRow`` omitted ``status_id``,
        so a swapped order raised ``AttributeError``.  That stopped being a
        legal input: every valuation now asks the status whether a row is worth
        what it RECORDED or what it PLANS, so a row without the column is one no
        valuation could ever see, and a control whose input is impossible grades
        nothing.  Counting the call states the ordering claim directly and still
        fails on a swap -- the entry-aware branch below calls ``is_projected``
        exactly once, so a reordered guard makes this count 1.
        """
        calls = []
        real = _amounts.is_projected
        monkeypatch.setattr(
            _amounts, "is_projected",
            lambda row: (calls.append(row), real(row))[1],
        )

        row = _FakeRow(txn_id=1, statusless=True)
        assert _entry_aware_amount(row, _basis(row)) == Decimal("77.00")
        assert calls == []
