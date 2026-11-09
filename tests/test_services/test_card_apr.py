"""Tests for the card's APR: the loader, its resolution, the two writes.

:mod:`app.services.card_apr` (plan step **credit_card:CC-3**, developer
ruling **R-CC27**).  What is pinned:

* ``apr_in_effect`` is the greatest effective date at or before the day, in
  any input order, and ``None`` before the first row (no flat-backwards);
* the loader maps ONLY this account's rows, newest first, with their ids;
* ``set_apr`` creates the row for a date and REWRITES the same date in place
  (same id, ``created_at``, ``monthly_pi`` and ``notes`` untouched), and is
  idempotent within one transaction; the database admits both bounds of the
  unit interval and refuses one step past;
* ``remove_apr`` deletes only a row this account holds, and says so;
* a card born a card is in no loan-account set, even one holding APR rows,
  while the shared query itself does read them (the design's "the loan
  loaders never see a card row" is the readers' gate, not the query's).

The concurrency claim -- two in-flight submits cannot race to an
``IntegrityError`` because the write is one ``ON CONFLICT`` statement -- holds
by the statement's construction and is NOT graded here: a control needs two
connections, which no test in this suite (the ``ensure_schedule`` precedent
included) holds.
"""

from datetime import date, timedelta
from decimal import Decimal

import pytest
from sqlalchemy.exc import IntegrityError

from app.extensions import db
from app.models.loan_features import RateHistory
from app.services import loan_loaders
from app.services.card_apr import (
    CardApr,
    apr_in_effect,
    load_card_aprs,
    remove_apr,
    set_apr,
)
from tests._test_helpers import create_account_of_type

JAN_1 = date(2026, 1, 1)
MAR_1 = date(2026, 3, 1)
JUN_1 = date(2026, 6, 1)


def _apr(effective_date, apr, row_id=0):
    """A :class:`CardApr` for the pure resolution tests."""
    return CardApr(row_id=row_id, effective_date=effective_date, apr=Decimal(apr))


class TestAprInEffect:
    """The greatest effective date at or before the day; None before the first."""

    #: Deliberately NOT in date order: the resolver must sort for itself.
    SERIES = [_apr(JUN_1, "0.2999"), _apr(JAN_1, "0.1999"), _apr(MAR_1, "0.2499")]

    def test_an_empty_series_has_no_rate(self):
        """No rows: None on any day."""
        assert apr_in_effect([], MAR_1) is None

    def test_before_the_first_row_there_is_no_rate(self):
        """Dec 31, 2025 precedes the Jan 1 row: None, not the Jan 1 rate."""
        assert apr_in_effect(self.SERIES, JAN_1 - timedelta(days=1)) is None

    def test_on_the_first_rows_date_it_applies(self):
        """Jan 1 itself takes the Jan 1 rate (effective from, inclusive)."""
        assert apr_in_effect(self.SERIES, JAN_1) == Decimal("0.1999")

    def test_between_two_rows_the_earlier_applies(self):
        """Feb 15 sits between Jan 1 and Mar 1: the Jan 1 rate."""
        assert apr_in_effect(self.SERIES, date(2026, 2, 15)) == Decimal("0.1999")

    def test_on_a_later_rows_date_it_takes_over(self):
        """Mar 1 takes the Mar 1 rate on its own day."""
        assert apr_in_effect(self.SERIES, MAR_1) == Decimal("0.2499")

    def test_after_the_last_row_the_last_applies(self):
        """Dec 31, 2030 is long after Jun 1: the Jun 1 rate holds forward."""
        assert apr_in_effect(self.SERIES, date(2030, 12, 31)) == Decimal("0.2999")


class TestTheLoader:
    """``load_card_aprs`` maps this account's rows, newest first."""

    def test_a_card_with_no_rows_loads_empty(self, seed_user):
        """A configured card whose APR was never stated."""
        card = create_account_of_type(seed_user, db.session, "Credit Card", "Visa")
        db.session.commit()

        assert load_card_aprs(card.id) == []

    def test_rows_load_newest_first_with_their_ids(self, seed_user):
        """Three rows inserted oldest-first come back newest-first, each with
        the id the remove door will name, and only this card's."""
        card = create_account_of_type(seed_user, db.session, "Credit Card", "Visa")
        other = create_account_of_type(seed_user, db.session, "Credit Card", "Amex")
        db.session.add_all([
            RateHistory(account_id=card.id, effective_date=JAN_1,
                        interest_rate=Decimal("0.1999")),
            RateHistory(account_id=card.id, effective_date=MAR_1,
                        interest_rate=Decimal("0.2499")),
            RateHistory(account_id=other.id, effective_date=JUN_1,
                        interest_rate=Decimal("0.2999")),
        ])
        db.session.commit()
        rows = {
            r.effective_date: r.id
            for r in db.session.query(RateHistory).filter_by(account_id=card.id)
        }

        assert load_card_aprs(card.id) == [
            CardApr(row_id=rows[MAR_1], effective_date=MAR_1, apr=Decimal("0.24990")),
            CardApr(row_id=rows[JAN_1], effective_date=JAN_1, apr=Decimal("0.19990")),
        ]


class TestSetApr:
    """Create the row for a date, or rewrite its rate in place."""

    def test_a_new_date_creates_the_row(self, seed_user):
        """One row, the fraction as stored (``Numeric(7, 5)``)."""
        card = create_account_of_type(seed_user, db.session, "Credit Card", "Visa")
        db.session.commit()

        set_apr(card.id, MAR_1, Decimal("0.2499"))
        db.session.commit()

        rows = db.session.query(RateHistory).filter_by(account_id=card.id).all()
        assert len(rows) == 1
        assert rows[0].effective_date == MAR_1
        assert rows[0].interest_rate == Decimal("0.24990")
        assert rows[0].monthly_pi is None
        assert rows[0].notes is None

    def test_the_same_date_rewrites_the_rate_in_place(self, seed_user):
        """The row's id, created_at and the loan-only columns survive; only
        the rate changes."""
        card = create_account_of_type(seed_user, db.session, "Credit Card", "Visa")
        db.session.add(RateHistory(
            account_id=card.id, effective_date=MAR_1,
            interest_rate=Decimal("0.2499"), notes="promo ends",
        ))
        db.session.commit()
        before = db.session.query(RateHistory).filter_by(account_id=card.id).one()
        row_id, created_at = before.id, before.created_at

        set_apr(card.id, MAR_1, Decimal("0.2799"))
        db.session.commit()
        db.session.expire_all()

        rows = db.session.query(RateHistory).filter_by(account_id=card.id).all()
        assert [(r.id, r.created_at, r.interest_rate, r.notes) for r in rows] == [
            (row_id, created_at, Decimal("0.27990"), "promo ends"),
        ]

    def test_two_sets_in_one_transaction_leave_one_row(self, seed_user):
        """Idempotent within a transaction: the second set for the date
        rewrites the first's rate.  (Not a concurrency control -- a
        query-then-insert would pass this too, its query autoflushing the
        pending row; see the module docstring.)"""
        card = create_account_of_type(seed_user, db.session, "Credit Card", "Visa")
        db.session.commit()

        set_apr(card.id, MAR_1, Decimal("0.2499"))
        set_apr(card.id, MAR_1, Decimal("0.2599"))
        db.session.commit()

        rows = db.session.query(RateHistory).filter_by(account_id=card.id).all()
        assert [(r.effective_date, r.interest_rate) for r in rows] == [
            (MAR_1, Decimal("0.25990")),
        ]

    def test_a_second_date_is_a_second_row(self, seed_user):
        """Two dates, two rows, both this card's."""
        card = create_account_of_type(seed_user, db.session, "Credit Card", "Visa")
        db.session.commit()

        set_apr(card.id, JAN_1, Decimal("0.1999"))
        set_apr(card.id, MAR_1, Decimal("0.2499"))
        db.session.commit()

        assert [
            (a.effective_date, a.apr) for a in load_card_aprs(card.id)
        ] == [(MAR_1, Decimal("0.24990")), (JAN_1, Decimal("0.19990"))]

    def test_another_accounts_same_date_row_is_untouched(self, seed_user):
        """The key is (account, date): the other card's Mar 1 row keeps its
        rate and a new row is created on this one."""
        card = create_account_of_type(seed_user, db.session, "Credit Card", "Visa")
        other = create_account_of_type(seed_user, db.session, "Credit Card", "Amex")
        db.session.add(RateHistory(
            account_id=other.id, effective_date=MAR_1,
            interest_rate=Decimal("0.2999"),
        ))
        db.session.commit()

        set_apr(card.id, MAR_1, Decimal("0.2499"))
        db.session.commit()
        db.session.expire_all()

        assert load_card_aprs(other.id) == [
            CardApr(
                row_id=db.session.query(RateHistory).filter_by(
                    account_id=other.id,
                ).one().id,
                effective_date=MAR_1, apr=Decimal("0.29990"),
            ),
        ]
        assert [a.apr for a in load_card_aprs(card.id)] == [Decimal("0.24990")]

    @pytest.mark.parametrize(
        "bound", [Decimal("0"), Decimal("1")], ids=["zero", "one"],
    )
    def test_both_bounds_of_the_unit_interval_are_admitted(self, seed_user, bound):
        """0% and 100% are inside ``ck_rate_history_valid_interest_rate``: a
        CHECK one step too tight would pass every refusal case."""
        card = create_account_of_type(seed_user, db.session, "Credit Card", "Visa")
        db.session.commit()

        set_apr(card.id, MAR_1, bound)
        db.session.commit()

        assert [a.apr for a in load_card_aprs(card.id)] == [bound]

    def test_a_rate_outside_the_unit_interval_is_refused_by_the_database(
        self, seed_user,
    ):
        """The CHECK stands behind the schema: a percent stored un-divided
        is refused at the tier that keeps the domain."""
        card = create_account_of_type(seed_user, db.session, "Credit Card", "Visa")
        db.session.commit()

        with pytest.raises(IntegrityError, match="ck_rate_history_valid_interest_rate"):
            set_apr(card.id, MAR_1, Decimal("24.99"))
        db.session.rollback()

        assert load_card_aprs(card.id) == []


class TestRemoveApr:
    """Delete only a row this account holds, and report which happened."""

    def test_this_cards_row_is_deleted(self, seed_user):
        """True, and the row is gone; a sibling row stays."""
        card = create_account_of_type(seed_user, db.session, "Credit Card", "Visa")
        db.session.add_all([
            RateHistory(account_id=card.id, effective_date=JAN_1,
                        interest_rate=Decimal("0.1999")),
            RateHistory(account_id=card.id, effective_date=MAR_1,
                        interest_rate=Decimal("0.2499")),
        ])
        db.session.commit()
        doomed = db.session.query(RateHistory).filter_by(
            account_id=card.id, effective_date=JAN_1,
        ).one().id

        assert remove_apr(card.id, doomed) is True
        db.session.commit()

        assert [a.effective_date for a in load_card_aprs(card.id)] == [MAR_1]

    def test_an_unknown_row_is_reported_and_nothing_is_written(self, seed_user):
        """False for an id no row carries."""
        card = create_account_of_type(seed_user, db.session, "Credit Card", "Visa")
        db.session.commit()

        assert remove_apr(card.id, 999_999) is False

    def test_another_accounts_row_is_not_this_cards_to_remove(self, seed_user):
        """False, and the other account's row survives: the scope is BOTH
        ids, not the row id alone."""
        card = create_account_of_type(seed_user, db.session, "Credit Card", "Visa")
        other = create_account_of_type(seed_user, db.session, "Credit Card", "Amex")
        db.session.add(RateHistory(
            account_id=other.id, effective_date=MAR_1,
            interest_rate=Decimal("0.2999"),
        ))
        db.session.commit()
        theirs = db.session.query(RateHistory).filter_by(account_id=other.id).one().id

        assert remove_apr(card.id, theirs) is False
        db.session.commit()

        assert [a.row_id for a in load_card_aprs(other.id)] == [theirs]


class TestACardBornACardIsInNoLoanAccountSet:
    """Design 3.4: the loan pipeline reaches ``rate_history`` only through a
    ``LoanParams`` row, which no door creates for a revolving type.  (A
    re-typed empty-ledger loan KEEPS its row -- the ``*Params`` re-type
    semantics, reported at CC-3 -- so this pins the card as created, not an
    impossibility.)"""

    def test_a_card_holding_apr_rows_is_in_no_loan_account_set(self, seed_user):
        """Every loan-account loader answers without the card, the card has
        no ``LoanParams`` for the loan readers to resolve through, and the
        shared query -- account-keyed -- does see the rows: the gate is the
        readers'."""
        card = create_account_of_type(seed_user, db.session, "Credit Card", "Visa")
        db.session.add(RateHistory(
            account_id=card.id, effective_date=MAR_1,
            interest_rate=Decimal("0.2499"),
        ))
        db.session.commit()

        assert card.id not in loan_loaders.load_loan_account_ids_for_user(
            seed_user["user"].id,
        )
        assert loan_loaders.load_loan_params(card.id) is None
        assert len(loan_loaders.load_rate_history(card.id)) == 1
