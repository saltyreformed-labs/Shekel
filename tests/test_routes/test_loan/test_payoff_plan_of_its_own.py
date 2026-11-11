"""The payoff calculator compares a plan with the contract only when the loan has one.

Plan step recurrence:R16-e (ruling R-R93): the route reads the seam's
:attr:`~app.services.balance_at.LoanFigures.has_own_plan` -- a payment in the loan's
record, a recurring definition into it, or a projected row its forward plan folds --
where it read the loan's payment list and a second query for its definitions.  The
seam's own cases are ``tests/test_services/test_balance_at/test_the_two_payment_checks.py``;
these show the page answering on them.  A settled payment and no payment at all are
``test_payoff_chart.py``'s ``test_payoff_results_committed_metrics`` and
``test_payoff_no_transfer_degrades``.

The mortgage is this package's ``_create_mortgage`` (asserted at ``$250,000.00`` on
2026-01-01), read on the package's frozen today, 2026-03-20.
"""

from datetime import date
from decimal import Decimal

import pytest

from tests._test_helpers import insert_trueup_event, make_loan_payment_template

from ._helpers import _create_mortgage, _create_transfer_to_loan

#: The line the payoff partial prints only for a loan with a plan of its own.
_COMPARISON = "Current plan vs. original"


def _payoff_html(auth_client, acct):
    """POST the extra-payment payoff for *acct* and return the partial's HTML."""
    resp = auth_client.post(
        f"/accounts/{acct.id}/loan/payoff",
        data={"mode": "extra_payment", "extra_monthly": "200"},
    )
    assert resp.status_code == 200
    return resp.data.decode()


class TestThePayoffComparisonReadsTheSeam:
    """The comparison line follows ``LoanFigures.has_own_plan``, case by case."""

    def test_a_projected_row_only_is_compared(
        self, auth_client, seed_user, db, seed_periods,
    ):
        """A projected payment in period 7 (2026-04-10), after the 2026-01-01
        assertion, is folded by the plan, so the plan is the owner's own."""
        acct = _create_mortgage(seed_user, db.session)
        _create_transfer_to_loan(
            seed_user, acct, seed_periods[7], Decimal("1580.00"),
        )
        db.session.commit()

        assert _COMPARISON in _payoff_html(auth_client, acct)

    @pytest.mark.usefixtures("seed_periods")
    def test_a_definition_only_is_compared(self, auth_client, seed_user, db):
        """A recurring payment with no row generated is the owner's plan too."""
        acct = _create_mortgage(seed_user, db.session)
        make_loan_payment_template(db.session, seed_user, acct)
        db.session.commit()

        assert _COMPARISON in _payoff_html(auth_client, acct)

    def test_a_row_its_true_up_covers_is_not_compared(
        self, auth_client, seed_user, db, seed_periods,
    ):
        """A projected payment in period 1 (2026-01-16) inside a true-up dated
        2026-02-15 is not folded (ruling R-R72), so the plan is the contract and
        no comparison is drawn.  The payment list counted the row and drew one."""
        acct = _create_mortgage(seed_user, db.session)
        _create_transfer_to_loan(
            seed_user, acct, seed_periods[1], Decimal("1580.00"),
        )
        insert_trueup_event(
            acct.loan_params, Decimal("249000.00"), date(2026, 2, 15),
        )
        db.session.commit()

        assert _COMPARISON not in _payoff_html(auth_client, acct)
