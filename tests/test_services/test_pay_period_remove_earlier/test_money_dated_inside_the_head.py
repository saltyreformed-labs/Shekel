"""MONEY DATED inside the removed span (R-PC109's dated half), one case per arm, and the case the
span excludes.
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

import pytest

from app.enums import StatusEnum
from app.exceptions import ValidationError
from app.extensions import db as _db
from app.models.transfer import Transfer
from app.services import pay_period_admin, transfer_service
from tests._test_helpers import (
    add_entry,
    add_txn,
    create_loan_account,
    create_loan_with_trueup,
    an_entered_day,
    an_observed_day,
    create_savings_account,
    create_transfer,
    on_both_sides,
    open_books_before_the_first_assertion,
    reassert_balance_on,
    transfer_side_record,
    typed,
)

from ._helpers import _paydays, _stored_eras, _unchanged, _added_head, _held


EASTERN = ZoneInfo("America/New_York")


class TestMoneyDatedInsideTheHead:
    """R-PC109's dated half: the removal may not raise the floor over recorded money.

    The seeded owner's 10 paychecks open 2026-01-02; two are added
    (2025-12-05, 2025-12-19), which moves the recordable floor to 12-05.
    Money recorded on 2025-12-20 in a KEPT paycheck is below 01-02, so
    starting from 01-02 is refused, naming the 12-19 paycheck that holds
    the day.
    """

    DAY = date(2025, 12, 20)

    def _refusal(self, what):
        """The ruled shape for money dated on :attr:`DAY`."""
        return (
            f"{what} 2025-12-20, inside the paychecks you would remove, and "
            f"money can't be dated before your schedule starts. Start from "
            f"2025-12-19 or earlier."
        )

    def test_a_row_in_a_kept_paycheck_marked_paid_inside_is_refused(
        self, app, db, seed_user, seed_periods,
    ):
        """Rent in the 01-02 paycheck, paid 12-20 once the floor allowed it."""
        with app.app_context():
            user_id = seed_user["user"].id
            _added_head(user_id, 2)
            add_txn(
                db.session, seed_user, seed_periods[0], "Water", "40.00",
                status_enum=StatusEnum.DONE, settled_on=self.DAY,
            )
            db.session.commit()
            paydays, eras = _paydays(user_id), _stored_eras(user_id)

            assert _held(user_id, seed_periods[0]) == self._refusal(
                "Water is marked paid on",
            )
            _unchanged(user_id, paydays, eras)

    def test_a_purchase_in_a_kept_paycheck_paid_inside_is_refused(
        self, app, db, seed_user, seed_periods,
    ):
        """A purchase's own bank day is money dated, whatever its row's status."""
        with app.app_context():
            user_id = seed_user["user"].id
            _added_head(user_id, 2)
            row = add_txn(db.session, seed_user, seed_periods[0], "Food", "90.00")
            add_entry(
                db.session, seed_user, row, Decimal("25.00"), self.DAY,
                settled_on=self.DAY, description="Market",
            )
            db.session.commit()

            assert _held(user_id, seed_periods[0]) == self._refusal(
                "The purchase Market is marked paid on",
            )

    def _transfer_to_savings(self, seed_user, seed_periods, *, due_date=None):
        """A Projected Checking -> Savings transfer in the 01-02 paycheck.

        Savings' books open before :attr:`DAY`, so the only thing that can
        refuse a day there is the floor under test.  *due_date* is stored on
        the transfer (``None``, the default, leaves it unset).
        """
        savings = create_savings_account(
            seed_user, _db.session, "Savings", Decimal("0.00"),
        )
        open_books_before_the_first_assertion(_db.session, savings)
        xfer = create_transfer(
            seed_user, _db.session, seed_user["account"], savings,
            seed_periods[0], amount=Decimal("300.00"), due_date=due_date,
        )
        _db.session.commit()
        return xfer

    def test_a_transfer_side_paid_inside_is_refused_by_ITS_day(
        self, app, db, seed_user, seed_periods,
    ):
        """Each side's own day is money dated: the gate names the side that is inside.

        Plan step ``balance:X-bi-6-4c-3``: a transfer's sides keep their own
        days (ruling **R-BAL142**), and the gate reads each side's record
        (ledger row **BAL-568**).  Checking's statement shows the money leave
        on the new first payday, which the removal admits; Savings' own day is
        :attr:`DAY`, inside the span -- so the refusal names SAVINGS' side,
        the only one holding money there.
        """
        with app.app_context():
            user_id = seed_user["user"].id
            _added_head(user_id, 2)
            xfer = self._transfer_to_savings(seed_user, seed_periods)
            transfer_service.settle_transfer(
                xfer.id, user_id,
                side_days=(
                    transfer_service.SideDay(
                        xfer.from_account_id,
                        an_observed_day(seed_periods[0].start_date),
                    ),
                    transfer_service.SideDay(
                        xfer.to_account_id, an_entered_day(self.DAY),
                    ),
                ),
            )
            db.session.commit()
            paydays, eras = _paydays(user_id), _stored_eras(user_id)

            assert _held(user_id, seed_periods[0]) == self._refusal(
                "Transfer from Checking is marked paid on",
            )
            _unchanged(user_id, paydays, eras)

    def test_a_transfer_closed_at_zero_inside_does_not_hold_the_removal(
        self, app, db, seed_user, seed_periods,
    ):
        """A ``$0.00`` close moved no money, so it dates nothing (ledger row BAL-568).

        It records no covering movement (ruling **R-BAL90**: a close of
        nothing has no day of money), and R-PC109 names money dated inside the
        span, so it does not hold the removal back -- though its DUE DATE,
        :attr:`DAY`, is inside the span.  The due date is the day a later
        correction of its figure dates it by (rulings **R-BAL169**,
        **R-BAL231**), never a day the close itself records.

        The close was pressed with :attr:`DAY` typed on both sides until plan
        step ``balance:X-bi-6-4d-2``, whose door refuses a day beside a
        ``$0.00`` close (ruling **R-BAL230**), so that press is asserted
        refused and the close is pressed with no day.  Its two shadows carried
        :attr:`DAY` (planted Paid on it, as that press wrote them), and the
        gate read the shadows until plan step ``balance:X-bi-6-4c-3`` -- so
        this removal was REFUSED then.  A transfer has no twin to plant since
        plan step ``balance:X-bi-6-4d-2`` made it one row (ruling
        **R-BAL258**; the plant updated no row, measured), so the day inside
        the span is the transfer's due date instead (ruling **R-BAL167** class
        1): a gate that dated a ``$0.00`` close by its due date refuses here,
        and with no due date it would admit the removal as the right gate
        does.
        """
        with app.app_context():
            user_id = seed_user["user"].id
            _added_head(user_id, 2)
            xfer = self._transfer_to_savings(
                seed_user, seed_periods, due_date=self.DAY,
            )
            with pytest.raises(
                ValidationError, match=r"A \$0\.00 close moved no money",
            ):
                transfer_service.settle_transfer(
                    xfer.id, user_id, submitted=typed(Decimal("0.00")),
                    side_days=on_both_sides(
                        xfer.from_account_id, xfer.to_account_id,
                        an_entered_day(self.DAY),
                    ),
                )
            db.session.rollback()
            transfer_service.settle_transfer(
                xfer.id, user_id, submitted=typed(Decimal("0.00")),
            )
            db.session.commit()
            # The setup's own tell: the shape is the $0.00 close this case is
            # about -- a Paid transfer holding no record on either side, its
            # due date inside the span -- or the admit below would pass for a
            # different reason.
            assert _db.session.get(Transfer, xfer.id).due_date == self.DAY
            assert _db.session.get(Transfer, xfer.id).status.is_settled
            assert [
                transfer_side_record(db.session, xfer.id, account_id)
                for account_id in (xfer.from_account_id, xfer.to_account_id)
            ] == [None, None]

            assert pay_period_admin.remove_earlier_pay_periods(
                user_id, seed_periods[0].id,
            ) == 2

    def test_a_balance_recorded_inside_is_refused(
        self, app, db, seed_user, seed_periods,
    ):
        """An account's assertion observed 12-20: the back-dated state the floor refuses."""
        with app.app_context():
            user_id = seed_user["user"].id
            _added_head(user_id, 2)
            reassert_balance_on(
                db.session, seed_user["account"],
                at=datetime(2025, 12, 20, 12, tzinfo=EASTERN),
            )
            db.session.commit()

            assert _held(user_id, seed_periods[0]) == self._refusal(
                f"{seed_user['account'].name}'s balance is recorded for",
            )

    def test_a_loan_balance_recorded_inside_is_refused(
        self, app, db, seed_user, seed_periods,
    ):
        """A loan true-up dated 12-20; the loan's origination in 2020 is not money dated."""
        with app.app_context():
            user_id = seed_user["user"].id
            create_loan_with_trueup(
                seed_user, db.session,
                origination_principal=Decimal("20000.00"),
                anchor_balance=Decimal("15000.00"), anchor_date=self.DAY,
                rate=Decimal("0.05"), origination_date=date(2020, 1, 1),
                name="Car Loan",
            )
            _added_head(user_id, 2)

            assert _held(user_id, seed_periods[0]) == self._refusal(
                "Car Loan's balance is recorded for",
            )

    def test_money_already_below_the_floor_is_not_the_removals(
        self, app, db, seed_user, seed_periods,
    ):
        """A settle day before 12-05 was below the floor before; it does not refuse.

        The span is where the removal MOVES the floor, [12-05, 01-02); a
        legacy day below 12-05 stays below either way.
        """
        with app.app_context():
            user_id = seed_user["user"].id
            _added_head(user_id, 2)
            add_txn(
                db.session, seed_user, seed_periods[0], "Water", "40.00",
                status_enum=StatusEnum.DONE, settled_on=date(2025, 11, 30),
            )
            db.session.commit()

            assert pay_period_admin.remove_earlier_pay_periods(
                user_id, seed_periods[0].id,
            ) == 2


    def test_a_purchase_a_companion_recorded_inside_is_refused(
        self, app, db, seed_user, seed_periods, seed_companion,
    ):
        """The purchase is the owner's whoever typed it (review 1's M1)."""
        with app.app_context():
            user_id = seed_user["user"].id
            _added_head(user_id, 2)
            row = add_txn(db.session, seed_user, seed_periods[0], "Food", "90.00")
            add_entry(
                db.session, seed_companion, row, Decimal("25.00"), self.DAY,
                settled_on=self.DAY, description="Market",
            )
            db.session.commit()

            assert _held(user_id, seed_periods[0]) == self._refusal(
                "The purchase Market is marked paid on",
            )

    def test_a_loans_tracking_start_inside_is_refused(
        self, app, db, seed_user, seed_periods,
    ):
        """The balance stated at a loan's setup stands like a true-up (review 1's M2)."""
        with app.app_context():
            user_id = seed_user["user"].id
            create_loan_account(
                seed_user, db.session, name="Car Loan",
                principal=Decimal("20000.00"), rate=Decimal("0.05"),
                origination_date=date(2020, 1, 1),
                tracked_balance=Decimal("15000.00"), tracked_from=self.DAY,
            )
            db.session.commit()
            _added_head(user_id, 2)

            assert _held(user_id, seed_periods[0]) == self._refusal(
                "Car Loan's balance is recorded for",
            )

    def test_money_on_the_new_first_payday_is_admitted(
        self, app, db, seed_user, seed_periods,
    ):
        """The span stops BEFORE the paycheck kept first: its own payday is inside the schedule."""
        with app.app_context():
            user_id = seed_user["user"].id
            _added_head(user_id, 2)
            add_txn(
                db.session, seed_user, seed_periods[0], "Water", "40.00",
                status_enum=StatusEnum.DONE, settled_on=seed_periods[0].start_date,
            )
            db.session.commit()

            assert pay_period_admin.remove_earlier_pay_periods(
                user_id, seed_periods[0].id,
            ) == 2
