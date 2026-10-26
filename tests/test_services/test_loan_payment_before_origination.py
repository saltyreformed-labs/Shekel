"""C9b (ruling R-C): a loan cannot receive a payment before it originates.

A payment whose INSTALLMENT falls at or before a loan's origination is erased by
the fold -- it splits against a running balance of zero, so it books $0.00
principal and the whole cash routes to a Refund Receivable, after which the
origination anchor resets the balance over the top of it.  The cash still leaves
the funding account.  Net: the money is gone, the loan is untouched, and the app
models the lender as owing it back (finding FU-5, measured at $1,200.00).

The developer ruled this REJECTED at the transfer write boundary rather than
modeled as a prepayment or left to fail loud on read.  These tests pin the
boundary (which is ``<=``, not ``<``), both write paths (create AND the edit that
could move an existing payment backwards), and the cases that must stay allowed.

All money is ``Decimal`` from strings.
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from app.enums import AcctTypeEnum
from app.exceptions import ValidationError
from app.services import loan_loaders, transfer_service
from tests._test_helpers import (
    create_account_of_type,
    create_loan_account,
    create_settled_transfer,
    create_transfer,
)
from app.models.amount_ownership import AmountOwnership

# The suite's frozen today is 2026-03-20.  This loan closes 2026-03-01 -- in the
# PAST, so periods on both sides of origination exist inside the seeded window.
ORIGINATION = date(2026, 3, 1)


@pytest.fixture(name="loan_and_checking")
def _loan_and_checking(db, seed_user, seed_periods):
    """A mortgage originating 2026-03-01 (payment day 1) and its funding account."""
    acct = create_loan_account(
        seed_user, db.session, name="Probe Mortgage",
        principal=Decimal("200000.00"), rate=Decimal("0.05000"),
        term=360, origination_date=ORIGINATION, payment_day=1,
        account_type=AcctTypeEnum.MORTGAGE,
    )
    checking = create_account_of_type(
        seed_user, db.session, "Checking", "Chk",
        anchor_balance=Decimal("50000.00"),
    )
    db.session.commit()
    return acct, checking


class TestTheBoundaryIsInclusive:
    """``<=`` origination, because that is exactly what the fold erases."""

    @pytest.mark.parametrize("due, erased", [
        (date(2026, 1, 15), True),    # well before
        (date(2026, 2, 28), True),    # the day before
        (ORIGINATION, True),          # ON the origination date -- subsumed
        (date(2026, 3, 2), False),    # the day after -- a real paydown
        (date(2026, 4, 1), False),    # the first contractual installment
    ])
    def test_the_guard_matches_what_the_fold_erases(
        self, app, db, seed_user, seed_periods, loan_and_checking, due, erased,
    ):
        """The rejection boundary IS the erasure boundary, swept day by day.

        A payment due exactly ON the origination date sorts BEFORE that anchor
        in the walk (payments tie-break ahead of anchors), so it is subsumed by
        the reset and erased identically to an earlier one -- which is why the
        comparison is ``<=``.  Measured on this loan: due 2026-03-01 books $0.00
        principal and $1,200.00 excess; due 2026-03-02 books $366.67 principal.

        NEGATIVE CONTROL: relax the guard to ``<`` and the ON-origination case
        stops raising.
        """
        acct, checking = loan_and_checking
        with app.app_context():
            if erased:
                with pytest.raises(ValidationError) as excinfo:
                    create_transfer(
                        seed_user, db.session, checking, acct, seed_periods[5],
                        amount=Decimal("1200.00"), due_date=due,
                    )
                assert "before it originates" in str(excinfo.value)
            else:
                xfer = create_transfer(
                    seed_user, db.session, checking, acct, seed_periods[5],
                    amount=Decimal("1200.00"), due_date=due,
                )
                assert xfer.due_date == due

    def test_the_day_after_origination_really_pays_the_loan_down(
        self, app, db, seed_user, seed_periods, loan_and_checking,
    ):
        """The POSITIVE half of the boundary: one day later, the money lands.

        The sweep above proves the guard refuses at ``<= origination``; this
        proves it is not one day too aggressive, by folding the payment it
        ALLOWS and asserting the split.

        Arithmetic on this loan (200,000 @ 5.000%, opening balance, $1,200 cash):
          interest  = round(200,000 * 0.05 / 12) = 833.33
          principal = 1,200.00 - 833.33 - 0.00 escrow = 366.67
        """
        # pylint: disable=import-outside-toplevel
        from app.services.balance_at import BalanceContext

        acct, checking = loan_and_checking
        with app.app_context():
            create_settled_transfer(
                seed_user, db.session, checking, acct, seed_periods[5],
                amount=Decimal("1200.00"),
                settled_on=date(2026, 3, 18),
            )
            db.session.commit()

            bctx = BalanceContext.build(seed_user["user"].id)
            split = bctx.loan_walk(acct).payment_splits[0]
            assert split.interest == Decimal("833.33")
            assert split.principal == Decimal("366.67")
            assert split.excess == Decimal("0.00")

    def test_the_installment_derivation_is_shared_with_the_fold(self):
        """The guard keys on ``loan_loaders.installment_for``, not its own rule.

        A guard with a private derivation would refuse a different set of
        payments than the fold erases -- the boundary-predicate drift this
        architecture keeps paying for.  Pin the two facts the shared helper
        encodes: a stored due date wins, and a payment without one falls back to
        its pay-period start.
        """
        assert loan_loaders.installment_for(
            date(2026, 5, 9), date(2026, 4, 24), 1,
        ) == date(2026, 5, 9)
        assert loan_loaders.installment_for(
            None, date(2026, 4, 24), 1,
        ) == date(2026, 5, 1)


class TestNoDueDateUsesThePeriodFallback:
    """The ad-hoc shape FU-5 was originally found in."""

    def test_an_ad_hoc_transfer_with_no_due_date_is_still_caught(
        self, app, db, seed_user, seed_periods, loan_and_checking,
    ):
        """A transfer carrying NO due date is keyed on its pay-period start.

        This is the exact FU-5 shape: a $1,200 "down payment" typed into a loan
        through the ordinary transfer form, which sets no due date.  A guard
        keying only on the stored column would wave it straight through, because
        the column is NULL -- the fold, which falls back to the period start,
        would still erase it.

        NEGATIVE CONTROL: key the guard on ``due_date`` alone and this passes
        the write, and the payment lands with $0.00 principal.
        """
        acct, checking = loan_and_checking
        with app.app_context():
            # periods[2] starts 2026-01-30 -> installment 2026-02-01, before the
            # 2026-03-01 origination.
            assert loan_loaders.installment_for(
                None, seed_periods[2].start_date, 1,
            ) <= ORIGINATION
            with pytest.raises(ValidationError) as excinfo:
                create_transfer(
                    seed_user, db.session, checking, acct, seed_periods[2],
                    amount=Decimal("1200.00"),
                )
            assert "2026-02-01" in str(excinfo.value)

    def test_a_later_period_with_no_due_date_is_allowed(
        self, app, db, seed_user, seed_periods, loan_and_checking,
    ):
        """The same shape after origination writes normally.

        Guards that over-refuse are as damaging as guards that under-refuse, and
        the fallback is the easy place to get that wrong.
        """
        acct, checking = loan_and_checking
        with app.app_context():
            xfer = create_transfer(
                seed_user, db.session, checking, acct, seed_periods[8],
                amount=Decimal("1200.00"),
            )
            assert xfer.id is not None


class TestTheEditPathIsGuardedToo:
    """Ruling: extend the guard to the update path (a second door)."""

    def test_moving_a_payments_due_date_before_origination_is_refused(
        self, app, db, seed_user, seed_periods, loan_and_checking,
    ):
        """A legally-created payment cannot be edited backwards past origination.

        Create is not the only door: the transfers PATCH route forwards
        ``due_date`` to ``update_transfer``, so a payment created legitimately
        after origination could be dragged behind it and then settled -- landing
        in exactly the erased state the create guard refuses.

        NEGATIVE CONTROL: drop the ``_reject_payment_before_origination`` call
        from ``update_transfer`` and the edit succeeds.
        """
        acct, checking = loan_and_checking
        with app.app_context():
            xfer = create_transfer(
                seed_user, db.session, checking, acct, seed_periods[8],
                amount=Decimal("1200.00"), due_date=date(2026, 5, 1),
            )
            db.session.commit()

            with pytest.raises(ValidationError):
                transfer_service.update_transfer(
                    xfer.id, seed_user["user"].id,
                    due_date=date(2026, 2, 1),
                )

    def test_the_refused_edit_leaves_the_transfer_untouched(
        self, app, db, seed_user, seed_periods, loan_and_checking,
    ):
        """A rejected edit is atomic -- the transfer is unchanged.

        The check runs before any field is applied, the same discipline
        ``apply_status_to_all_three`` follows for an illegal status transition.  A
        guard that raised halfway through would leave part of the edit on the
        transfer while reporting an error.

        The edit deliberately carries an ``amount`` alongside the illegal
        ``due_date``, and the assertions read the IN-MEMORY transfer with NO
        rollback first.  That ordering is the whole test: ``amount`` is applied
        before ``due_date`` in ``update_transfer``, so a guard placed after the
        amount block would leave $999.00 on the transfer even though the call
        raised.  Rolling back before asserting would erase that evidence and
        leave the test proving only that the caller's rollback works.  The case
        read the transfer's two shadow rows beside it until plan step
        ``balance:X-bi-6-4d-2`` deleted them.  Renamed from
        ``test_the_refused_edit_leaves_all_three_rows_untouched``.

        NEGATIVE CONTROL: move the ``_reject_installment_move_before_loan`` call
        below the ``amount`` block in ``update_transfer`` and this goes red on
        the $1,200.00 assertions.
        """
        acct, checking = loan_and_checking
        with app.app_context():
            xfer = create_transfer(
                seed_user, db.session, checking, acct, seed_periods[8],
                amount=Decimal("1200.00"), due_date=date(2026, 5, 1),
            )
            db.session.commit()

            with pytest.raises(ValidationError):
                transfer_service.update_transfer(
                    xfer.id, seed_user["user"].id,
                    due_date=date(2026, 2, 1),
                    amount_ownership=AmountOwnership.own(Decimal("999.00")),
                )

            # No rollback: nothing may have been applied in the first place.
            assert xfer.due_date == date(2026, 5, 1)
            assert xfer.amount == Decimal("1200.00")

            # ...and the same holds once the session is re-read from the DB.
            db.session.rollback()
            db.session.expire_all()
            assert xfer.amount == Decimal("1200.00")
            assert xfer.due_date == date(2026, 5, 1)

    def test_moving_a_payment_to_an_earlier_period_is_refused(
        self, app, db, seed_user, seed_periods, loan_and_checking,
    ):
        """``pay_period_id`` moves the installment too, when there is no due date.

        The period is the fallback basis, so a period move is a second way to
        drag a payment behind origination -- and the carry-forward service moves
        periods without touching ``due_date``.
        """
        acct, checking = loan_and_checking
        with app.app_context():
            xfer = create_transfer(
                seed_user, db.session, checking, acct, seed_periods[8],
                amount=Decimal("1200.00"),
            )
            db.session.commit()

            with pytest.raises(ValidationError):
                transfer_service.update_transfer(
                    xfer.id, seed_user["user"].id,
                    pay_period_id=seed_periods[2].id,
                )

    def test_an_unrelated_edit_of_a_legacy_row_still_works(
        self, app, db, seed_user, seed_periods, loan_and_checking,
    ):
        """A pre-existing pre-origination row stays editable in other respects.

        The C9a purge deliberately leaves some rows behind (a SETTLED payment,
        whose cash really moved).  Re-checking the installment on every edit
        would strand those rows -- unable to rename or re-categorise them --
        which is why the guard fires only when ``due_date`` / ``pay_period_id``
        is actually being moved.
        """
        acct, checking = loan_and_checking
        with app.app_context():
            # Build the legacy row the only way still possible: create it
            # legally, then plant the pre-origination date underneath the guard.
            xfer = create_transfer(
                seed_user, db.session, checking, acct, seed_periods[8],
                amount=Decimal("1200.00"), due_date=date(2026, 5, 1),
            )
            db.session.commit()
            xfer.due_date = date(2026, 2, 1)
            db.session.commit()

            # An edit that does not move the installment is unaffected.
            transfer_service.update_transfer(
                xfer.id, seed_user["user"].id, name="Renamed",
            )
            assert xfer.name == "Renamed"
            assert xfer.due_date == date(2026, 2, 1)


class TestWhatMustStayAllowed:
    """The guard is about the loan's EXISTENCE, not its schedule."""

    def test_a_payment_between_origination_and_the_first_installment(
        self, app, db, seed_user, seed_periods, loan_and_checking,
    ):
        """An early extra payment is legitimate and must not be refused.

        The loan originates 2026-03-01 and first bills 2026-04-01, so 2026-03-15
        is after the loan exists but before any contractual installment.  The
        fold splits it correctly against the opening balance; refusing it would
        block a real user action (an extra principal payment at closing).  This
        is the line between C9b (existence) and C9a's recurrence bound
        (the contract).
        """
        acct, checking = loan_and_checking
        with app.app_context():
            xfer = create_transfer(
                seed_user, db.session, checking, acct, seed_periods[5],
                amount=Decimal("500.00"), due_date=date(2026, 3, 15),
            )
            assert xfer.id is not None

    def test_a_non_loan_destination_is_never_checked(
        self, app, db, seed_user, seed_periods,
    ):
        """An ordinary savings transfer is untouched by the loan guard."""
        with app.app_context():
            checking = create_account_of_type(
                seed_user, db.session, "Checking", "Chk",
                anchor_balance=Decimal("5000.00"),
            )
            savings = create_account_of_type(
                seed_user, db.session, "Savings", "Sav",
                anchor_balance=Decimal("100.00"),
            )
            db.session.commit()
            xfer = create_transfer(
                seed_user, db.session, checking, savings, seed_periods[0],
                amount=Decimal("100.00"), due_date=date(2026, 1, 5),
            )
            assert xfer.id is not None

    def test_an_amortizing_account_with_no_params_is_not_checked(
        self, app, db, seed_user, seed_periods,
    ):
        """A Mortgage-typed account with no LoanParams has no origination to compare.

        ``classify_account`` reads the account TYPE only, so an unconfigured
        mortgage classifies AMORTIZING.  There is no origination date to test
        against, and refusing every payment into it would block the ordinary
        configure-later flow.
        """
        with app.app_context():
            checking = create_account_of_type(
                seed_user, db.session, "Checking", "Chk",
                anchor_balance=Decimal("5000.00"),
            )
            # A Mortgage-typed account with NO LoanParams: created but not yet
            # configured, which the setup form leaves behind between steps.
            bare_loan = create_account_of_type(
                seed_user, db.session, "Mortgage", "Unconfigured",
                anchor_balance=Decimal("0.00"),
            )
            db.session.commit()
            assert loan_loaders.load_loan_params(bare_loan.id) is None

            xfer = create_transfer(
                seed_user, db.session, checking, bare_loan, seed_periods[0],
                amount=Decimal("100.00"), due_date=date(2026, 1, 5),
            )
            assert xfer.id is not None


class TestTheMoneyItProtects:
    """What the refused write would have done, measured."""

    def test_a_settled_pre_origination_payment_books_no_principal(
        self, app, db, seed_user, seed_periods, loan_and_checking,
    ):
        """The erasure itself, demonstrated on a row planted under the guard.

        This is why the guard exists, and it is asserted rather than described:
        with the payment's installment before origination, the fold books $0.00
        principal and routes the entire $1,200.00 to ``excess`` (a Refund
        Receivable) -- while the cash has left checking.
        """
        # pylint: disable=import-outside-toplevel
        from app.services.balance_at import BalanceContext

        acct, checking = loan_and_checking
        with app.app_context():
            xfer = create_settled_transfer(
                seed_user, db.session, checking, acct, seed_periods[8],
                amount=Decimal("1200.00"),
                settled_on=date(2026, 3, 10),
            )
            db.session.commit()
            # Plant the pre-origination installment underneath the guard, which
            # is the only way this state can still arise (legacy data).
            xfer.due_date = date(2026, 2, 1)
            db.session.commit()

            bctx = BalanceContext.build(seed_user["user"].id)
            walk = bctx.loan_walk(acct)
            split = walk.payment_splits[0]
            assert split.principal == Decimal("0.00")
            assert split.interest == Decimal("0.00")
            assert split.excess == Decimal("1200.00")
