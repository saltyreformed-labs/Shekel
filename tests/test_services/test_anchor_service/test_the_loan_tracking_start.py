"""A loan's tracking start: the opening it appends, ruling R-EQ's per-source duplicate rule, and
the refusal of a start on or after a day a payment moved money (rulings R-R114, R-R115 and
R-BAL155).
"""

from datetime import date, timedelta
from decimal import Decimal

import pytest

from app import ref_cache
from app.enums import LoanAnchorSourceEnum
from app.exceptions import TrackingStartRefused
from app.extensions import db
from app.models.loan_anchor_event import LoanAnchorEvent
from app.models.loan_params import LoanParams
from app.models.scenario import Scenario
from app.services import loan_loaders
from app.services.anchor_service import AnchorTrueUpOutcome
from app.services.loan_anchor_service import (
    apply_loan_anchor_true_up,
    record_loan_tracking_start,
    stage_loan_tracking_start,
)
from tests._test_helpers import create_loan_account, create_settled_transfer

from ._helpers import _make_loan_account


def _loan_with_open_books(seed_user):
    """A $20,000.00 loan from 2025-01-01, due the 1st, whose books open at its origination.

    :func:`_make_loan_account` opens the account's books TODAY, so the
    ledger refuses a payment dated before it; ruling R-R114's refusal is
    about payments recorded before a tracking start, which need books open
    earlier (:func:`tests._test_helpers.create_loan_account`).
    """
    account = create_loan_account(
        seed_user, db.session, name="Tracked Loan",
        principal=Decimal("20000.00"), rate=Decimal("0.05000"), term=60,
        origination_date=date(2025, 1, 1), payment_day=1,
    )
    db.session.commit()
    return account


def _anchor_rows(account):
    """Return ``[(date, balance, source id)]`` for every stored anchor row, by id.

    A query, so it autoflushes the session first: a row the code under test
    staged is counted whether or not it was flushed.
    """
    return [
        (row.anchor_date, row.anchor_balance, row.source_id)
        for row in db.session.query(LoanAnchorEvent)
        .filter_by(account_id=account.id)
        .order_by(LoanAnchorEvent.id)
    ]


class TestRecordLoanTrackingStart:
    """The tracking-start opening flow appends a tracking_start event."""

    def test_commits_appends_tracking_start_opening(
        self, app, db, seed_user, seed_periods_today,
    ):
        """record_loan_tracking_start appends one tracking_start event as the opening.

        The fixture loan opens at $20,000 origination.  After a tracking-start of
        $18,000 dated today:
          * outcome is COMMITTED
          * exactly one tracking_start event is appended (origination + tracking = 2)
          * it carries the TRACKING_START source, balance, and date
          * the loan still OPENS at its $20,000 origination (step C1); the
            tracking-start loads as a non-opening assertion (``is_opening`` False,
            ``is_tracking_start`` True, balance 18000) that RESETS the walk.
        """
        with app.app_context():
            account = _make_loan_account(seed_user)
            db.session.commit()

            outcome = record_loan_tracking_start(
                account=account,
                anchor_balance=Decimal("18000.00"),
                anchor_date=date.today(),
            )
            assert outcome is AnchorTrueUpOutcome.COMMITTED

            db.session.expire_all()
            events = (
                db.session.query(LoanAnchorEvent)
                .filter_by(account_id=account.id)
                .all()
            )
            assert len(events) == 2
            tracking_source_id = ref_cache.loan_anchor_source_id(
                LoanAnchorSourceEnum.TRACKING_START,
            )
            tracking = next(
                e for e in events if e.source_id == tracking_source_id
            )
            assert tracking.anchor_balance == Decimal("18000.00")
            assert tracking.anchor_date == date.today()

            params = (
                db.session.query(LoanParams)
                .filter_by(account_id=account.id)
                .one()
            )
            facts = loan_loaders.load_loan_anchor_facts(params)
            (opening,) = [fact for fact in facts if fact.is_opening]
            assert opening.is_tracking_start is False
            assert opening.anchor_balance == Decimal("20000.00")
            (tracking_fact,) = [
                fact for fact in facts if fact.is_tracking_start
            ]
            assert tracking_fact.is_opening is False
            assert tracking_fact.anchor_balance == Decimal("18000.00")

    def test_double_call_same_returns_unchanged(
        self, app, db, seed_user, seed_periods_today,
    ):
        """A resubmit of the governing (date, balance) is idempotent."""
        with app.app_context():
            account = _make_loan_account(seed_user)
            db.session.commit()

            first = record_loan_tracking_start(
                account=account,
                anchor_balance=Decimal("18000.00"),
                anchor_date=date.today(),
            )
            second = record_loan_tracking_start(
                account=account,
                anchor_balance=Decimal("18000.00"),
                anchor_date=date.today(),
            )
            assert first is AnchorTrueUpOutcome.COMMITTED
            assert second is AnchorTrueUpOutcome.UNCHANGED

    def test_resubmit_is_recognised_after_a_later_trueup(
        self, app, db, seed_user, seed_periods_today,
    ):
        """A re-submitted OPENING is recognised even under a later true-up.

        Ruling R-EQ scopes the loan comparison to the submission's own SOURCE,
        and this is why.  A ``tracking_start`` is by definition the earliest
        anchor, so once any ``user_trueup`` is recorded the latest row of ANY
        source is that true-up -- and comparing against it would let a
        double-submitted opening append a second ``tracking_start``, silently
        duplicating the assertion the operator meant to record once.

        *The reason stated here used to be that
        ``loan_loaders._opening_anchor_fact`` reads a tracking-start as the
        loan's opening whatever else was recorded later.  That function was
        deleted at step C1 and origination is the opening ALWAYS; the test is
        unchanged because the per-source scoping is right either way (plan step
        X-an-b).*
        """
        with app.app_context():
            account = _make_loan_account(seed_user)
            db.session.commit()
            opening_day = date.today() - timedelta(days=30)

            assert record_loan_tracking_start(
                account=account,
                anchor_balance=Decimal("18000.00"),
                anchor_date=opening_day,
            ) is AnchorTrueUpOutcome.COMMITTED
            assert apply_loan_anchor_true_up(
                account=account,
                anchor_balance=Decimal("17000.00"),
                anchor_date=date.today(),
            ) is AnchorTrueUpOutcome.COMMITTED

            # The latest row of ANY source is now the true-up; the opening is
            # still what a re-submitted opening duplicates.
            assert record_loan_tracking_start(
                account=account,
                anchor_balance=Decimal("18000.00"),
                anchor_date=opening_day,
            ) is AnchorTrueUpOutcome.UNCHANGED

            db.session.expire_all()
            tracking_source_id = ref_cache.loan_anchor_source_id(
                LoanAnchorSourceEnum.TRACKING_START,
            )
            openings = (
                db.session.query(LoanAnchorEvent)
                .filter_by(account_id=account.id, source_id=tracking_source_id)
                .all()
            )
            assert len(openings) == 1, (
                f"A re-submitted opening must append nothing; found "
                f"{len(openings)} tracking_start rows."
            )

    def test_refuses_on_or_after_the_day_a_payment_moved_money_and_writes_nothing(
        self, app, db, seed_user, seed_periods_today,
    ):
        """Rulings R-R114, R-BAL155: a tracking start on or after a payment's money day is refused.

        A $400.00 payment settled on period 1's first day moved money that
        day.  A tracking start that day or ten days later raises
        :class:`TrackingStartRefused` naming both days and appends nothing; the
        day before is a start and is recorded.
        """
        with app.app_context():
            account = _loan_with_open_books(seed_user)
            paid_on = seed_periods_today[1].start_date
            create_settled_transfer(
                seed_user, db.session, seed_user["account"], account,
                seed_periods_today[1], amount=Decimal("400.00"),
                settled_on=paid_on,
            )
            db.session.commit()
            stored = _anchor_rows(account)

            for asked in (paid_on, paid_on + timedelta(days=10)):
                with pytest.raises(TrackingStartRefused) as refused:
                    record_loan_tracking_start(
                        account=account,
                        anchor_balance=Decimal("18000.00"),
                        anchor_date=asked,
                    )
                assert (refused.value.asked, refused.value.moved_on) == (
                    asked, paid_on,
                )
                # Counted in the refused transaction, BEFORE any rollback: the
                # query autoflushes, so a row staged ahead of the raise shows.
                assert _anchor_rows(account) == stored
                db.session.rollback()
            assert record_loan_tracking_start(
                account=account,
                anchor_balance=Decimal("18000.00"),
                anchor_date=paid_on - timedelta(days=1),
            ) is AnchorTrueUpOutcome.COMMITTED

            db.session.expire_all()
            tracking_source_id = ref_cache.loan_anchor_source_id(
                LoanAnchorSourceEnum.TRACKING_START,
            )
            assert [
                event.anchor_date
                for event in db.session.query(LoanAnchorEvent)
                .filter_by(account_id=account.id, source_id=tracking_source_id)
                .all()
            ] == [paid_on - timedelta(days=1)]

    def test_a_true_up_after_a_recorded_payment_is_not_refused(
        self, app, db, seed_user, seed_periods_today,
    ):
        """The refusal is the tracking-start source's alone: a correction there is recorded."""
        with app.app_context():
            account = _loan_with_open_books(seed_user)
            create_settled_transfer(
                seed_user, db.session, seed_user["account"], account,
                seed_periods_today[1], amount=Decimal("400.00"),
                settled_on=seed_periods_today[1].start_date,
            )
            db.session.commit()

            assert apply_loan_anchor_true_up(
                account=account,
                anchor_balance=Decimal("18000.00"),
                anchor_date=date.today(),
            ) is AnchorTrueUpOutcome.COMMITTED

    def test_a_zero_close_counts_from_no_day(
        self, app, db, seed_user, seed_periods_today,
    ):
        """Ruling R-BAL155: a $0.00 close moved no money, so only the real payment refuses.

        Period 1's payment is closed at $0.00 (no movement) and period 3's
        $400.00 payment settles on its first day.  A tracking start the day
        after the close is recorded -- the close counts from no day, neither
        its settle day nor the installment it skips -- and one on the $400.00
        payment's day is refused, naming THAT day.
        """
        with app.app_context():
            account = _loan_with_open_books(seed_user)
            closed_on = seed_periods_today[1].start_date
            paid_on = seed_periods_today[3].start_date
            create_settled_transfer(
                seed_user, db.session, seed_user["account"], account,
                seed_periods_today[1], amount=Decimal("400.00"),
                settled_amount=Decimal("0.00"), settled_on=closed_on,
            )
            create_settled_transfer(
                seed_user, db.session, seed_user["account"], account,
                seed_periods_today[3], amount=Decimal("400.00"),
                settled_on=paid_on,
            )
            db.session.commit()

            assert record_loan_tracking_start(
                account=account,
                anchor_balance=Decimal("18000.00"),
                anchor_date=closed_on + timedelta(days=1),
            ) is AnchorTrueUpOutcome.COMMITTED
            with pytest.raises(TrackingStartRefused) as refused:
                record_loan_tracking_start(
                    account=account,
                    anchor_balance=Decimal("17500.00"),
                    anchor_date=paid_on,
                )
            assert refused.value.moved_on == paid_on

    def test_a_payment_in_another_scenario_refuses(
        self, app, db, seed_user, seed_periods_today,
    ):
        """A tracking start is the ACCOUNT's, so a payment in any scenario refuses it.

        The $400.00 payment settles in a what-if scenario only; the baseline
        holds none.  The tracking start re-bases the walk in every scenario
        the loan has a payment in, so a date after the what-if payment is
        refused all the same.
        """
        with app.app_context():
            account = _loan_with_open_books(seed_user)
            whatif = Scenario(
                user_id=seed_user["user"].id, name="What-if", is_baseline=False,
            )
            db.session.add(whatif)
            db.session.flush()
            paid_on = seed_periods_today[1].start_date
            create_settled_transfer(
                seed_user, db.session, seed_user["account"], account,
                seed_periods_today[1], amount=Decimal("400.00"),
                settled_on=paid_on, scenario=whatif,
            )
            db.session.commit()

            with pytest.raises(TrackingStartRefused) as refused:
                record_loan_tracking_start(
                    account=account,
                    anchor_balance=Decimal("18000.00"),
                    anchor_date=paid_on + timedelta(days=1),
                )
            assert refused.value.moved_on == paid_on

    def test_a_resubmit_a_payment_now_contradicts_is_refused_not_unchanged(
        self, app, db, seed_user, seed_periods_today,
    ):
        """The refusal precedes ruling R-EQ's duplicate rule.

        A tracking start recorded on period 1's first day stands; a payment
        is then settled that same day.  Resubmitting the identical tracking
        start is refused, not answered UNCHANGED: the statement it repeats is
        one the payment now contradicts.
        """
        with app.app_context():
            account = _loan_with_open_books(seed_user)
            started_on = seed_periods_today[1].start_date
            assert record_loan_tracking_start(
                account=account,
                anchor_balance=Decimal("18000.00"),
                anchor_date=started_on,
            ) is AnchorTrueUpOutcome.COMMITTED
            create_settled_transfer(
                seed_user, db.session, seed_user["account"], account,
                seed_periods_today[1], amount=Decimal("400.00"),
                settled_on=started_on,
            )
            db.session.commit()

            with pytest.raises(TrackingStartRefused):
                record_loan_tracking_start(
                    account=account,
                    anchor_balance=Decimal("18000.00"),
                    anchor_date=started_on,
                )

    def test_the_setup_door_stages_by_the_same_rule(
        self, app, db, seed_user, seed_periods_today,
    ):
        """Ruling R-R115: the setup door's staging refuses as the dashboard's door does.

        :func:`stage_loan_tracking_start` shares the one constructor, so a
        day on or after the payment's is refused before anything is staged,
        and the day before is staged.
        """
        with app.app_context():
            account = _loan_with_open_books(seed_user)
            paid_on = seed_periods_today[1].start_date
            create_settled_transfer(
                seed_user, db.session, seed_user["account"], account,
                seed_periods_today[1], amount=Decimal("400.00"),
                settled_on=paid_on,
            )
            db.session.commit()
            stored = _anchor_rows(account)

            with pytest.raises(TrackingStartRefused) as refused:
                stage_loan_tracking_start(
                    account=account,
                    anchor_balance=Decimal("18000.00"),
                    anchor_date=paid_on,
                )
            assert refused.value.moved_on == paid_on
            # Counted in the refused transaction, BEFORE the rollback: the
            # query autoflushes, so a row staged (or staged and flushed) ahead
            # of the raise shows, which ``session.new`` alone cannot see once
            # it is flushed.
            assert _anchor_rows(account) == stored
            db.session.rollback()

            assert stage_loan_tracking_start(
                account=account,
                anchor_balance=Decimal("18000.00"),
                anchor_date=paid_on - timedelta(days=1),
            ) is True
            assert [
                (row.anchor_date, row.anchor_balance)
                for row in db.session.new
                if isinstance(row, LoanAnchorEvent)
            ] == [(paid_on - timedelta(days=1), Decimal("18000.00"))]
            db.session.rollback()
