"""get_payment_history, the loan's payment feed: which transfers into the loan it reads and which
it excludes, the figure and the three dates each payment carries, its order and scenario, and a
payment that stores no figure, priced through the amount model rather than refused.
"""

from datetime import timedelta
from decimal import Decimal

import pytest

from app import ref_cache
from app.enums import StatusEnum, TxnTypeEnum
from app.exceptions import AmountUnresolvable
from app.extensions import db
from app.models.transfer import Transfer
from app.utils.balance_predicates import settled_status_ids
from tests._test_helpers import (
    generate_transfer_of,
    loan_params_for,
    make_transfer_template,
    one_off_row_of,
    typed,
)
from app.services.cash_ledger import derived_amount_basis
from app.services.row_valuation import settled_contribution
from app.services.loan_payment_service import get_payment_history
from app.services.transfer_service import delete_transfer, update_transfer
from app.services.transfer_legs import parent_of, transfer_side_leg

from ._helpers import _basis, _create_loan_account, _create_transfer_to_loan


# ── Tests ────────────────────────────────────────────────────────────


class TestGetPaymentHistory:
    """Tests for get_payment_history() query and transformation logic."""

    def test_returns_empty_for_no_transfers(
        self, app, db, seed_user, seed_periods,
    ):
        """A loan account no transfer pays into returns an empty list."""
        with app.app_context():
            loan = _create_loan_account(seed_user)
            db.session.commit()

            result = get_payment_history(
                loan.id, _basis(seed_user), loan_params_for(db.session, loan.id),
            )
            assert result == []

    def test_returns_a_transfer_into_the_loan_as_one_payment(
        self, app, db, seed_user, seed_periods,
    ):
        """A transfer into the loan is ONE payment, its income side's.

        Its expense side is on the checking (source) account and should
        not appear in the loan account's payment history.
        """
        with app.app_context():
            loan = _create_loan_account(seed_user)
            _create_transfer_to_loan(
                seed_user, loan, seed_periods[1], Decimal("1500.00"),
            )
            db.session.commit()

            result = get_payment_history(
                loan.id, _basis(seed_user), loan_params_for(db.session, loan.id),
            )
            assert len(result) == 1
            assert result[0].amount == Decimal("1500.00")

    def test_excludes_non_transfer_transactions(
        self, app, db, seed_user, seed_periods,
    ):
        """Regular (non-transfer) income transactions are excluded.

        The feed reads transfers into the loan (``budget.transfers``) and no
        row, so a row on the loan account is not payment history.
        """
        with app.app_context():
            loan = _create_loan_account(seed_user)

            # Create a regular income transaction on the loan account
            # (not via transfer -- no transfer_id).
            income_type_id = ref_cache.txn_type_id(TxnTypeEnum.INCOME)
            one_off_row_of(
                seed_periods[0],
                name="Manual Income",
                amount=Decimal("500.00"),
                user_id=seed_periods[0].user_id,
                account_id=loan.id,
                scenario_id=seed_user["scenario"].id,
                transaction_type_id=income_type_id,
            )
            db.session.commit()

            result = get_payment_history(
                loan.id, _basis(seed_user), loan_params_for(db.session, loan.id),
            )
            # Not a transfer -> not in the feed.
            assert result == []

    @pytest.mark.parametrize("status_enum", [
        StatusEnum.PROJECTED, StatusEnum.DONE,
    ])
    def test_excludes_deleted_transactions(
        self, app, db, seed_user, seed_periods, status_enum,
    ):
        """A soft-deleted payment is excluded, whether it was planned or paid.

        The feed's two halves read the one relation ``budget.transfers``,
        each through its own ``Transfer.is_deleted`` filter, and **both cases
        reach the PLAN half's** (``transfer_legs/_records.py``).  The door's
        soft delete takes a Paid payment's records off and returns it as a
        plan (ruling **R-BAL246**), so the paid case is a deleted plan when
        the feed reads it: one filter, graded on two paths into it.
        Measured 2026-10-15: with the plan half's filter dropped both cases
        fail at the last assertion, and with the settled half's dropped
        (``loan_loaders/_legs.py``) both pass.  The one payment the
        delete leaves settled is a ``$0.00`` close, which held nothing, and
        the settled half's filter is graded on it by
        ``test_loan_settled_legs/test_the_two_halves.py``'s
        ``TestTheSettledHalfComplementsThePlanHalf`` case
        ``test_a_soft_deleted_zero_dollar_close_is_in_neither_half``.
        """
        with app.app_context():
            loan = _create_loan_account(seed_user)
            settled = ref_cache.status_id(status_enum) in settled_status_ids()
            transfer = _create_transfer_to_loan(
                seed_user, loan, seed_periods[1], Decimal("1500.00"),
                status_enum=status_enum,
                settled_on=seed_periods[1].start_date if settled else None,
            )
            db.session.commit()
            assert len(get_payment_history(
                loan.id, _basis(seed_user), loan_params_for(db.session, loan.id),
            )) == 1, "the payment must be in the feed for its deletion to grade"

            # Soft-delete the payment THROUGH THE DOOR, which takes a Paid
            # payment's records off, hides the transfer and returns it as a
            # plan (ruling R-BAL246).  *It set ``is_deleted`` on the income
            # shadow alone until plan step balance:X-bi-6a*, a write past
            # Transfer Invariant 4 that left the parent live; the feed reads
            # the TRANSFER, so only a deletion the transfer carries removes a
            # payment from it.
            delete_transfer(transfer.id, seed_user["user"].id, soft=True)
            db.session.commit()

            result = get_payment_history(
                loan.id, _basis(seed_user), loan_params_for(db.session, loan.id),
            )
            assert result == []

    def test_excludes_cancelled_transactions(
        self, app, db, seed_user, seed_periods,
    ):
        """A Cancelled payment is excluded.

        A cancelled transfer means the payment did not happen.  Including
        it would tell the engine a payment was made when it was not.
        """
        with app.app_context():
            loan = _create_loan_account(seed_user)
            transfer = _create_transfer_to_loan(
                seed_user, loan, seed_periods[1], Decimal("1500.00"),
                status_enum=StatusEnum.CANCELLED,
            )
            db.session.commit()

            result = get_payment_history(
                loan.id, _basis(seed_user), loan_params_for(db.session, loan.id),
            )
            assert result == []

    def test_uses_effective_amount_with_actual(
        self, app, db, seed_user, seed_periods,
    ):
        """A payment with a CORRECTED figure: PaymentRecord uses the correction.

        The figure is the transfer's own since plan step
        ``balance:X-bi-6-4d-2`` (rulings **R-BAL365**, **R-BAL366**), so the
        correction is stated through the transfer's own door -- a person
        typing ``$1,450.00`` over the ``$1,500.00`` plan -- which stores it
        there.  Until that step the leg's covering movement held it (plan
        step ``balance:X-bi-4b-1``, ruling **R-BAL80**), and the door
        re-recorded both legs' movements.  This wrote
        ``shadow.settled_amount`` straight at the row's own column through
        ``X-bi-4a``, deleted at ``X-bi-4b-2``.
        """
        with app.app_context():
            loan = _create_loan_account(seed_user)
            transfer = _create_transfer_to_loan(
                seed_user, loan, seed_periods[1], Decimal("1500.00"),
                status_enum=StatusEnum.DONE,
            )
            db.session.commit()

            update_transfer(
                transfer.id, seed_user["user"].id,
                figure=typed(Decimal("1450.00")),
            )
            db.session.commit()

            result = get_payment_history(
                loan.id, _basis(seed_user), loan_params_for(db.session, loan.id),
            )
            assert len(result) == 1
            # A settled payment is worth the figure its transfer stores.
            assert result[0].amount == Decimal("1450.00")

    def test_uses_effective_amount_without_actual(
        self, app, db, seed_user, seed_periods,
    ):
        """A Projected payment with no recorded figure: PaymentRecord uses its plan.

        Nothing has moved, so the feed answers the $1,500.00 the transfer
        was created for.
        """
        with app.app_context():
            loan = _create_loan_account(seed_user)
            _create_transfer_to_loan(
                seed_user, loan, seed_periods[1], Decimal("1500.00"),
            )
            db.session.commit()

            result = get_payment_history(
                loan.id, _basis(seed_user), loan_params_for(db.session, loan.id),
            )
            assert len(result) == 1
            assert result[0].amount == Decimal("1500.00")

    def test_is_confirmed_settled_statuses(
        self, app, db, seed_user, seed_periods,
    ):
        """A Paid payment produces is_confirmed=True.

        Paid has is_settled=True on the Status model.
        """
        with app.app_context():
            loan = _create_loan_account(seed_user)
            _create_transfer_to_loan(
                seed_user, loan, seed_periods[1], Decimal("1500.00"),
                status_enum=StatusEnum.DONE,  # "Paid" -- is_settled=True
            )
            db.session.commit()

            result = get_payment_history(
                loan.id, _basis(seed_user), loan_params_for(db.session, loan.id),
            )
            assert len(result) == 1
            assert result[0].dates.is_confirmed is True

    def test_is_confirmed_projected_status(
        self, app, db, seed_user, seed_periods,
    ):
        """A Projected payment produces is_confirmed=False.

        Projected has is_settled=False on the Status model.
        """
        with app.app_context():
            loan = _create_loan_account(seed_user)
            _create_transfer_to_loan(
                seed_user, loan, seed_periods[1], Decimal("1500.00"),
                status_enum=StatusEnum.PROJECTED,
            )
            db.session.commit()

            result = get_payment_history(
                loan.id, _basis(seed_user), loan_params_for(db.session, loan.id),
            )
            assert len(result) == 1
            assert result[0].dates.is_confirmed is False

    def test_period_start_from_pay_period(
        self, app, db, seed_user, seed_periods,
    ):
        """PaymentDates.period_start matches txn.pay_period.start_date."""
        with app.app_context():
            loan = _create_loan_account(seed_user)
            _create_transfer_to_loan(
                seed_user, loan, seed_periods[2], Decimal("1500.00"),
            )
            db.session.commit()

            result = get_payment_history(
                loan.id, _basis(seed_user), loan_params_for(db.session, loan.id),
            )
            assert len(result) == 1
            assert result[0].dates.period_start == seed_periods[2].start_date

    def test_settled_on_is_the_payments_own_stored_day_not_its_pay_period(
        self, app, db, seed_user, seed_periods,
    ):
        """``settled_on`` is the day the CASH moved, read off the payment's record.

        Plan step **X-an** / finding **N-187**: the resolver's
        replay-vs-projection cut keys on this day, and it must be the loan
        side's own stored fact rather than anything derived from the pay period
        funding it.  Here the cash left three days BEFORE that period opened -- an
        ordinary early payment -- which is exactly the case the derived answer
        got wrong.
        """
        with app.app_context():
            loan = _create_loan_account(seed_user)
            period = seed_periods[2]
            cash_day = period.start_date - timedelta(days=3)
            _create_transfer_to_loan(
                seed_user, loan, period, Decimal("1500.00"),
                status_enum=StatusEnum.DONE, settled_on=cash_day,
            )
            db.session.commit()

            result = get_payment_history(
                loan.id, _basis(seed_user), loan_params_for(db.session, loan.id),
            )
            assert len(result) == 1
            assert result[0].dates.settled_on == cash_day
            assert result[0].dates.period_start == period.start_date
            assert result[0].dates.is_confirmed is True

    def test_a_projected_payment_carries_no_settle_day(
        self, app, db, seed_user, seed_periods,
    ):
        """A payment that has not happened has no day, and is not confirmed.

        ``is_confirmed`` IS that absence since plan step X-an, so this pins the
        derived property against the database rather than only against the
        value object.
        """
        with app.app_context():
            loan = _create_loan_account(seed_user)
            _create_transfer_to_loan(
                seed_user, loan, seed_periods[2], Decimal("1500.00"),
                status_enum=StatusEnum.PROJECTED,
            )
            db.session.commit()

            result = get_payment_history(
                loan.id, _basis(seed_user), loan_params_for(db.session, loan.id),
            )
            assert len(result) == 1
            assert result[0].dates.settled_on is None
            assert result[0].dates.is_confirmed is False

    def test_ordered_by_pay_period_date(
        self, app, db, seed_user, seed_periods,
    ):
        """Results returned in chronological order by pay period date."""
        with app.app_context():
            loan = _create_loan_account(seed_user)
            # Create transfers in reverse period order.
            _create_transfer_to_loan(
                seed_user, loan, seed_periods[3], Decimal("1500.00"),
            )
            _create_transfer_to_loan(
                seed_user, loan, seed_periods[1], Decimal("1200.00"),
            )
            db.session.commit()

            result = get_payment_history(
                loan.id, _basis(seed_user), loan_params_for(db.session, loan.id),
            )
            assert len(result) == 2
            assert result[0].dates.period_start < result[1].dates.period_start
            # First payment (earlier period) has the $1,200 amount.
            assert result[0].amount == Decimal("1200.00")
            assert result[1].amount == Decimal("1500.00")

    def test_filters_by_scenario(
        self, app, db, seed_user, seed_periods,
    ):
        """A basis pinned to another scenario sees none of this one's payments.

        **The scope and the pricing are ONE object since plan step
        X-au-g-2c**, so this case grades more than it used to. The feed is
        scoped by ``basis.scenario_id``, and that is the same id
        ``resolve_transaction_amount`` refuses a foreign row for -- so a caller
        holding the wrong basis gets an EMPTY feed rather than this scenario's
        rows priced against another scenario's loans. There is no longer a way
        to state the two differently.
        """
        from app.models.scenario import Scenario  # pylint: disable=import-outside-toplevel

        with app.app_context():
            loan = _create_loan_account(seed_user)
            _create_transfer_to_loan(
                seed_user, loan, seed_periods[1], Decimal("1500.00"),
            )
            db.session.commit()

            other_scenario = Scenario(
                user_id=seed_user["user"].id,
                name="What-If",
                is_baseline=False,
            )
            db.session.add(other_scenario)
            db.session.commit()

            # The baseline basis DOES see the payment, so the emptiness below
            # is this scenario filter and not a fixture that built nothing.
            assert len(get_payment_history(
                loan.id, _basis(seed_user), loan_params_for(db.session, loan.id),
            )) == 1

            result = get_payment_history(
                loan.id,
                derived_amount_basis(seed_user["user"].id, other_scenario.id),
                loan_params_for(db.session, loan.id),
            )
            assert result == []

    def test_returns_decimal_amounts(
        self, app, db, seed_user, seed_periods,
    ):
        """PaymentRecord.amount is Decimal, not float.

        **It grades the amount model's TOTALITY now, where it used to grade a
        coercion** (plan step X-au-g-2c). ``get_payment_history`` wrapped every
        figure in ``Decimal(str(amount))`` "even if the stored column somehow
        yields a non-Decimal"; the resolver returns a ``Decimal`` or raises, so
        the padding is gone and this asserts the contract directly rather than
        the guard that used to stand in front of it.
        """
        with app.app_context():
            loan = _create_loan_account(seed_user)
            _create_transfer_to_loan(
                seed_user, loan, seed_periods[1], Decimal("1500.00"),
            )
            db.session.commit()

            result = get_payment_history(
                loan.id, _basis(seed_user), loan_params_for(db.session, loan.id),
            )
            assert len(result) == 1
            assert isinstance(result[0].amount, Decimal)

    def test_multiple_payments_returned(
        self, app, db, seed_user, seed_periods,
    ):
        """Multiple transfers produce multiple PaymentRecords."""
        with app.app_context():
            loan = _create_loan_account(seed_user)
            _create_transfer_to_loan(
                seed_user, loan, seed_periods[1], Decimal("1500.00"),
            )
            _create_transfer_to_loan(
                seed_user, loan, seed_periods[2], Decimal("1500.00"),
            )
            _create_transfer_to_loan(
                seed_user, loan, seed_periods[3], Decimal("1500.00"),
            )
            db.session.commit()

            result = get_payment_history(
                loan.id, _basis(seed_user), loan_params_for(db.session, loan.id),
            )
            assert len(result) == 3


def _generated_loan_payment(seed_user, loan, period):
    """Return a payment into *loan* GENERATED from its definition: it stores no figure.

    The definition states $1,500.00 (:func:`~tests._test_helpers.make_transfer_template`
    states its price), and a transfer the generate pass writes from it is
    DERIVED -- its ``amount`` is ``None`` and the definition's series answers
    for it (plan step X-au-f; :func:`~tests._test_helpers.generate_transfer_of`).
    Projected, from the seeded checking account.  Flushed; the caller commits.
    """
    template = make_transfer_template(db.session, seed_user, loan, amount="1500.00")
    return generate_transfer_of(template, period)


class TestTheFeedPricesADerivedRow:
    """A loan payment that stores NO figure is priced, not refused.

    **The whole point of plan step balance:X-au-g-2c's first leaf**, and the
    thing finding **N-266**(a) was.  ``get_payment_history`` priced every row
    through :func:`~app.services.row_valuation.settled_contribution` -- then
    named ``owned_contribution`` for the assertion that the row owned its
    figure, and REFUSING a row
    whose plan is DERIVED.  So the loan-side INCOME leg of a loan payment could
    not be declared derived while that call stood: emptying
    ``estimated_amount`` broke the feed the amortization engine replays.  Not a
    cycle, which is what the finding recorded for three weeks; ONE unrouted
    reader.

    The cases below have **disjoint fail sets on purpose**.  The first says the
    feed answers for a payment that stores no figure, and asserts that the
    payment stores none before it asks, so it cannot pass on one that stored
    its figure all along.  The second says the accessor that values the
    feed's SETTLED half still refuses that same payment, so the first is
    passing because the ROUTE prices the projected half through the amount
    model and not through that accessor.

    **Re-expressed onto the TRANSFER at plan step balance:X-bi-6-4d-2**
    (ruling **R-BAL167** class 1).  The figure-less row was the loan-side
    income shadow declared derived (``PARENT_TRANSFER``), and the accessor
    the ROW accessor :func:`~app.services.row_valuation.settled_contribution`.
    A transfer is one row since that step (ruling **R-BAL258**), so that
    shadow cannot be stored: the database refused it on the income side,
    pinned in ``test_a_transfer_shadow_is_derived/test_the_declaration.py``
    and ``test_loan_plan_assembly.py``, until plan step balance:X-bi-6-5
    dropped the column it named its transfer by, after which no statement
    can write one (ruling plan#1850).  The payment that stores no figure today is one GENERATED from its
    definition (:func:`_generated_loan_payment`), priced at the same
    ``$1,500.00``, and the settled half's accessor is
    :func:`~app.services.row_valuation.settled_contribution` asked of the
    leg's parent (ruling **plan#1983**).
    """

    def test_a_derived_payment_resolves_through_the_amount_model(
        self, app, db, seed_user, seed_periods,
    ):
        """A payment storing no figure is priced through the amount model: $1,500.00.

        The transfer stores nothing -- asserted FIRST, because a feed that
        read the transfer's own ``amount`` column, past the amount model,
        would answer a STORED ``$1,500.00`` and pass, where on a payment
        storing none only the amount model can answer -- and the feed's
        projected half resolves it from its definition through
        :func:`~app.services.cash_ledger.planned_leg_contribution`, at the
        $1,500.00 the definition states.

        It declared the LOAN-side income shadow derived and asserted the feed
        carried its parent's $1,500.00 until plan step balance:X-bi-6-4d-2
        (see the class).  The transfer has no ``LoanPaymentSettings``, so it
        is priced from its definition's $1,500.00, not from the loan's own
        installment.
        """
        with app.app_context():
            loan = _create_loan_account(seed_user)
            xfer = _generated_loan_payment(seed_user, loan, seed_periods[1])
            db.session.commit()
            assert db.session.get(Transfer, xfer.id).amount is None, (
                "the payment stores a figure, so a feed reading the plan "
                "column past the amount model would pass this case"
            )

            result = get_payment_history(
                loan.id, _basis(seed_user), loan_params_for(db.session, loan.id),
            )
            assert len(result) == 1
            assert result[0].amount == Decimal("1500.00")

    def test_the_accessor_it_used_to_call_still_refuses_that_row(
        self, app, db, seed_user, seed_periods,
    ):
        """The settled half's accessor refuses the payment the feed above prices.

        The other half of the pair, and it is what makes the pair a
        measurement.  Same fixture:
        :func:`~app.services.row_valuation.settled_contribution` of the leg's
        parent, which values the feed's SETTLED half, raises
        :class:`~app.exceptions.AmountUnresolvable` on this payment's
        loan-side leg, because its transfer has not settled and its money has
        not moved.  If a future change priced the projected half through it,
        this test would still pass and its sibling would fail -- which is the
        direction that matters.  The ``match`` names that refusal.

        It asked the ROW accessor
        :func:`~app.services.row_valuation.settled_contribution` of the
        loan-side income shadow declared derived until plan step
        balance:X-bi-6-4d-2 (see the class); that accessor's refusal of a
        derived Projected row is graded on an ordinary row by
        ``test_amount_ownership/test_the_cheap_accessor.py``'s
        ``test_a_derived_transaction_refuses``.
        """
        with app.app_context():
            loan = _create_loan_account(seed_user)
            xfer = _generated_loan_payment(seed_user, loan, seed_periods[1])
            db.session.commit()
            leg = transfer_side_leg(
                db.session.get(Transfer, xfer.id), is_income=True,
            )
            assert leg.account_id == loan.id

            with pytest.raises(AmountUnresolvable, match="has not settled"):
                settled_contribution(parent_of(leg))
