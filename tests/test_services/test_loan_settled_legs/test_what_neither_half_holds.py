"""What NEITHER half of a loan's payments holds: a deleted payment, another scenario's, a plain row.

:func:`app.services.loan_loaders.income_legs` partitions the transfers into a
loan into its SETTLED and PROJECTED halves, and ``test_the_two_halves.py``
grades that every payment it holds lands in exactly one.  These three cases
grade the other claim, that a row which is not this scenario's live payment
reaches neither half.  They were graded through the loan's priced payment list
(``loan_payment_service.get_payment_history``, in
``test_loan_payment_service/test_the_payment_history.py``) until plan task
plan#3388 deleted the list (ruling R-R93), and they ask the partition directly
since: it is the producer every surviving reader -- the fold's event stream,
the ledger's per-payment principal reader, the escrow forward-only guard --
reaches.
"""

from decimal import Decimal

import pytest

from app import ref_cache
from app.enums import StatusEnum, TxnTypeEnum
from app.extensions import db
from app.models.scenario import Scenario
from app.services import loan_loaders, transfer_service
from tests._test_helpers import (
    create_settled_transfer,
    create_transfer,
    one_off_row_of,
)

from ._helpers import _PERIOD, _loan


def _halves(loan, scenario_id):
    """Return ``(settled, projected)``: the transfer ids in each half of *loan*'s payments."""
    legs = loan_loaders.income_legs(
        loan.id, scenario_id, options=(), leg_options=(),
    )
    return (
        [leg.transfer.id for leg in legs.settled],
        [leg.transfer.id for leg in legs.projected],
    )


class TestWhatNeitherHalfHolds:
    """A row that is not this scenario's live payment into the loan is in neither half."""

    @pytest.mark.parametrize("status_enum", [
        StatusEnum.PROJECTED, StatusEnum.DONE,
    ])
    def test_a_soft_deleted_payment_is_in_neither_half(
        self, app, seed_user, seed_periods, status_enum,
    ):
        """A payment deleted through the door is in neither half, planned or paid.

        The two halves read the one relation ``budget.transfers``, each
        through its own ``Transfer.is_deleted`` filter, and **both cases
        reach the PLAN half's** (``transfer_legs/_records.py``).  The door's
        soft delete takes a Paid payment's records off and returns it as a
        plan (ruling **R-BAL246**), so the paid case is a deleted plan when
        the partition reads it: one filter, graded on two paths into it.
        Measured 2026-10-15 through the payment list: with the plan half's
        filter dropped both cases failed at the last assertion, and with the
        settled half's dropped (``loan_loaders/_legs.py``) both passed.  The
        one payment the delete leaves settled is a ``$0.00`` close, which held
        nothing, and the settled half's filter is graded on it by
        ``test_the_two_halves.py``'s
        ``test_a_soft_deleted_zero_dollar_close_is_in_neither_half``.

        The payment is asserted in its own half FIRST, so an empty answer
        after the delete is the filter and not a fixture that built nothing.
        """
        with app.app_context():
            loan = _loan(seed_user)
            checking = seed_user["account"]
            scenario_id = seed_user["scenario"].id
            period = seed_periods[_PERIOD]
            if status_enum is StatusEnum.DONE:
                transfer = create_settled_transfer(
                    seed_user, db.session, checking, loan, period,
                    amount=Decimal("1500.00"), settled_on=period.start_date,
                )
                expected = ([transfer.id], [])
            else:
                transfer = create_transfer(
                    seed_user, db.session, checking, loan, period,
                    Decimal("1500.00"),
                )
                expected = ([], [transfer.id])
            db.session.commit()
            assert _halves(loan, scenario_id) == expected, (
                "the payment must be in its half for its deletion to grade"
            )

            transfer_service.delete_transfer(
                transfer.id, seed_user["user"].id, soft=True,
            )
            db.session.commit()

            assert _halves(loan, scenario_id) == ([], [])

    def test_another_scenarios_payment_is_in_neither_half(
        self, app, seed_user, seed_periods,
    ):
        """The partition is scoped to its scenario: another one's sees nothing.

        The baseline's partition holds the payment first, so the empty answer
        for the other scenario is the scenario filter and not a fixture that
        built nothing.
        """
        with app.app_context():
            loan = _loan(seed_user)
            transfer = create_transfer(
                seed_user, db.session, seed_user["account"], loan,
                seed_periods[_PERIOD], Decimal("1500.00"),
            )
            other_scenario = Scenario(
                user_id=seed_user["user"].id,
                name="What-If",
                is_baseline=False,
            )
            db.session.add(other_scenario)
            db.session.commit()

            assert _halves(loan, seed_user["scenario"].id) == ([], [transfer.id])
            assert _halves(loan, other_scenario.id) == ([], [])

    def test_a_plain_row_on_the_loan_account_is_no_payment(
        self, app, seed_user, seed_periods,
    ):
        """An income row on the loan account that no transfer made is in neither half.

        The partition reads transfers into the loan (``budget.transfers``) and
        no transaction row, so a row on the loan account is not a payment.
        """
        with app.app_context():
            loan = _loan(seed_user)
            one_off_row_of(
                seed_periods[_PERIOD],
                name="Manual Income",
                amount=Decimal("500.00"),
                user_id=seed_user["user"].id,
                account_id=loan.id,
                scenario_id=seed_user["scenario"].id,
                transaction_type_id=ref_cache.txn_type_id(TxnTypeEnum.INCOME),
            )
            db.session.commit()

            assert _halves(loan, seed_user["scenario"].id) == ([], [])
