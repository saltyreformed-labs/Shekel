"""The loan setup door refuses a start on or after money the account records.

Card plan#2436, rulings plan#3954 and plan#4040, task plan#4090.  Setting up a
loan's terms moves its account's books start to the loan's origination, and a
loan's original amount is what it owed at the end of that day, so money that
moved by then is already inside it.  Payments can be recorded before a loan is
set up, so the setup door asks first and refuses with a sentence; the database
refuses the same state when the terms are inserted, at COMMIT, with none.

Every case here is an Auto Loan account whose opening record is Jan 2, 2026,
set up as originating Feb 27, 2026: the record holds every day below, so only
the origination can bound them.
"""

from datetime import date, timedelta
from decimal import Decimal

import pytest
from markupsafe import escape

from app.models.loan_anchor_event import LoanAnchorEvent
from app.models.loan_features import RateHistory
from app.models.loan_params import LoanParams
from app.services.cash_ledger import (
    BooksStart,
    books_start,
    earliest_recorded_movement_day,
)

from tests._test_helpers import (
    create_account_via_service,
    create_settled_transfer,
    match_two_lines,
)

#: The day every account here opens its record on, and the origination every
#: setup here submits.
_RECORD_OPENS = date(2026, 1, 2)
_ORIGINATION = date(2026, 2, 27)


def _setup_form(stated_on: date) -> dict[str, str]:
    """Return a $30,000 Auto Loan setup originating Feb 27, stating $25,000 as of *stated_on*."""
    return {
        "original_principal": "30000.00",
        "anchor_balance": "25000.00",
        "anchor_date": stated_on.isoformat(),
        "interest_rate": "5.000",
        "term_months": "60",
        "origination_date": _ORIGINATION.isoformat(),
        "payment_day": "15",
    }


def _unconfigured_loan(seed_user, db, name):
    """Return a committed Auto Loan account, not yet set up, whose record opens Jan 2.

    Through the factory alone (``create_account_via_service``): its one
    opening record is the books this door moves, so a second one opened
    earlier would grade nothing this file names.
    """
    account = create_account_via_service(
        seed_user, db.session, "Auto Loan", name,
        anchor_balance=Decimal("0"), observed_on=_RECORD_OPENS,
    )
    db.session.commit()
    return account


def _paid_before_setup(seed_user, db, period, paid_on, name):
    """Return an unset-up loan account $500 was paid into on *paid_on*, settled and committed.

    The payment is a transfer from the owner's checking account, the shape
    a loan payment recorded before the loan's terms has.
    """
    account = _unconfigured_loan(seed_user, db, name)
    create_settled_transfer(
        seed_user, db.session, seed_user["account"], account, period,
        amount=Decimal("500.00"), settled_on=paid_on,
    )
    db.session.commit()
    return account


def _assert_nothing_written(db, account):
    """Assert the refused setup left no params, origination rate or stated balance."""
    assert db.session.query(LoanParams).filter_by(
        account_id=account.id,
    ).count() == 0, "a refused setup left LoanParams behind"
    assert db.session.query(RateHistory).filter_by(
        account_id=account.id,
    ).count() == 0, "a refused setup left the origination rate behind"
    assert db.session.query(LoanAnchorEvent).filter_by(
        account_id=account.id,
    ).count() == 0, "a refused setup left the stated balance behind"


class TestALoansStartIsRefusedOnItsRecords:
    """``create_params`` asks ``cash_ledger.reject_loan_start_on_or_after_records``."""

    @pytest.mark.parametrize(("paid_on", "period_index"), [
        (date(2026, 2, 19), 3),  # before the origination
        (date(2026, 2, 27), 4),  # ON the origination day (ruling plan#4040)
    ])
    def test_a_start_on_or_after_a_payment_is_refused_whole(  # pylint: disable=too-many-arguments,too-many-positional-arguments
        self, auth_client, seed_user, db, seed_periods, paid_on, period_index,
    ):
        """$500 moved into the account on or before Feb 27, so a loan starting Feb 27 is refused.

        The stated day does not matter, and both are tried: Mar 1, after the
        payment, would also meet the tracking-start refusal (ruling R-R115),
        and Feb 27, the origination itself, states nothing beyond the loan's
        original amount, which is what that refusal's own remedy offers.
        Neither can make the loan hold a payment it started after, so the
        start's sentence is the one the owner reads, and the tracking start's
        is not.  Nothing is written: the refusal is asked before the params
        are added.

        Pylint: ``too-many-arguments`` / ``too-many-positional-arguments``
        (6/5) -- four fixtures, each read (the signed-in client, the owner
        and their checking account, the session, the seeded paychecks the
        payment lands in), and the two parameters of the case.
        """
        account = _paid_before_setup(
            seed_user, db, seed_periods[period_index], paid_on,
            "Paid Before It Started",
        )
        refusal = escape(
            "This loan cannot start on 2026-02-27: this account already "
            f"records money moving on {paid_on.isoformat()}, and a loan's "
            "original amount is what it owed at the end of its first day -- "
            "so that money would be counted twice.  Enter an origination date "
            f"before {paid_on.isoformat()}, or re-date the movement first."
        ).encode()

        for stated_on in (date(2026, 3, 1), _ORIGINATION):
            resp = auth_client.post(
                f"/accounts/{account.id}/loan/setup",
                data=_setup_form(stated_on),
            )
            assert resp.status_code == 200, stated_on
            assert refusal in resp.data, stated_on
            assert b"A payment into this loan already moved money" not in resp.data
            _assert_nothing_written(db, account)

    def test_a_start_the_day_before_a_payment_is_accepted(
        self, auth_client, seed_user, db, seed_periods,
    ):
        """$500 moved Feb 28, the day after the origination: the loan sets up and starts Feb 27.

        The refusal's other side, one day out.  The payment stays where it
        was recorded, and the account's books now start on the loan's
        origination owing its original amount
        (``cash_ledger.books_start``), not on its Jan 2 record.
        ``test_setup.py``'s origination-day sentence case submits the same
        setup for what the tracking start records; this one grades the start.
        """
        paid_on = date(2026, 2, 28)
        account = _paid_before_setup(
            seed_user, db, seed_periods[4], paid_on, "Paid After It Started",
        )

        resp = auth_client.post(
            f"/accounts/{account.id}/loan/setup",
            data=_setup_form(_ORIGINATION),
        )
        assert resp.status_code == 302
        assert db.session.query(LoanParams).filter_by(
            account_id=account.id,
        ).count() == 1
        assert earliest_recorded_movement_day(account.id) == paid_on
        assert books_start(account.id) == BooksStart(
            opened_on=_ORIGINATION,
            figure=Decimal("30000.00"),
            is_loan_start=True,
        )

    @pytest.mark.usefixtures("seed_periods")
    def test_a_start_on_or_after_a_matched_line_is_refused_whole(
        self, auth_client, seed_user, db,
    ):
        """A bank line matched on Feb 19 bounds the start as a payment would.

        FIRING CONTROL for the second bound: the account records no settled
        movement, so the movement bound returns without counting anything,
        and the two matched lines (Feb 19 and Mar 3) sit either side of the
        origination.  The match is built raw (``match_two_lines``) because no
        door builds one on an amortizing account: the reconcile pages refuse
        a loan.  The database counts the state anyway, so the door has to
        refuse it first.  ``seed_periods`` (by mark) gives the owner the
        calendar a setup past the refusal runs on, so with the refusal
        deleted this case reaches the database's own refusal at COMMIT
        (measured), not some earlier error.
        """
        early = date(2026, 2, 19)
        account = _unconfigured_loan(seed_user, db, "Matched Before It Started")
        match_two_lines(
            db.session, account, seed_user["user"].id,
            early, early + timedelta(days=12),
        )
        assert earliest_recorded_movement_day(account.id) is None, (
            "this case isolates the MATCHED-LINE bound; a movement would make "
            "the bound before it answer first"
        )

        resp = auth_client.post(
            f"/accounts/{account.id}/loan/setup",
            data=_setup_form(_ORIGINATION),
        )
        assert resp.status_code == 200
        assert escape(
            "This loan cannot start on 2026-02-27: you have matched a bank "
            "line your bank posted on 2026-02-19 in this account, and a "
            "loan's original amount is what it owed at the end of its first "
            "day -- so that money would be counted twice, once in the loan's "
            "amount and once in the row that explains the line.  Enter an "
            "origination date before 2026-02-19, or undo that match first."
        ).encode() in resp.data
        _assert_nothing_written(db, account)
