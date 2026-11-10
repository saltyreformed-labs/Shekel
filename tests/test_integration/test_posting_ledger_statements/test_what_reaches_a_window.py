"""What a window takes: a reverted source, the attribution edges."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from app import ref_cache
from app.enums import StatusEnum
from app.models.pay_period import PayPeriod
from app.services import ledger_report_service, status_seam
from app.services.ledger_report_service import StatementWindow
from app.services.pay_calendar import calendar_for

from tests._test_helpers import create_settled_cash_transaction

from ._helpers import (
    _checking_at_its_opening,
    _Y,
    _assert_ledger_self_consistent,
    _find_line,
    _reader_bs,
    _assert_reader_matches_independent,
)


# ---------------------------------------------------------------------------
# 6. A revert drops cleanly
# ---------------------------------------------------------------------------


class TestRevertAndResidueDropped:
    """A reverted source disappears from both statements."""

    def test_reverted_expense_leaves_no_statement_footprint(
        self, app, db, seed_user,
    ):
        """Settling then reverting a $400 expense removes it from both statements.

        A $400.00 Groceries expense settled in 2099 posts, then is reverted via
        the status seam -- a
        projected row books no line, so the worked-out ledger holds none for
        it.  The income statement shows no Groceries line and the balance
        sheet's Checking is back on its $1000.00 opening, tie-out green.
        """
        with app.app_context():
            user_id = seed_user["user"].id
            scenario_id = seed_user["scenario"].id
            txn = create_settled_cash_transaction(
                seed_user, seed_user["bootstrap_period"],
                Decimal("400.00"), account=seed_user["account"],
                category=seed_user["categories"]["Groceries"],
                settled_on=date(_Y, 7, 1),
            )
            db.session.commit()
            # Sanity: it was present before the revert.
            before = ledger_report_service.compute_income_statement(
                user_id, calendar_for(user_id), StatementWindow("year", year=_Y),
            )
            assert before.expense.total == Decimal("400.00")

            # Through the ONE status door first (plan step X-bi-3a): a settle
            # writes a covering movement that carries the money, and only the
            # seam's revert releases it.
            status_seam.apply_status_change(
                txn, ref_cache.status_id(StatusEnum.PROJECTED),
            )
            db.session.commit()

            after = ledger_report_service.compute_income_statement(
                user_id, calendar_for(user_id), StatementWindow("year", year=_Y),
            )
            assert not after.expense.lines
            assert after.net_income == Decimal("0.00")
            sheet = _checking_at_its_opening(user_id)
            _assert_reader_matches_independent(scenario_id, sheet)
            _assert_ledger_self_consistent()


# ---------------------------------------------------------------------------
# 7. Display-timezone attribution and the NULL / future-period edges
# ---------------------------------------------------------------------------


class TestAttributionEdgeCases:
    """L9's display-timezone rule and the NULL / early-settled attributions."""

    def test_dec31_evening_eastern_attributes_to_prior_year(
        self, app, db, seed_user,
    ):
        """An 8:05pm-ET Dec-31 settle lands in that year on both statements.

        A $500.00 Groceries expense paid at 2099-01-01 01:05 UTC -- 2098-12-31
        8:05pm Eastern (EST, UTC-5).  L9 attributes it to Dec 31 2098 (the user's
        wall clock), so the 2098 income statement includes it and 2099 does not,
        even though the STORED ``entry_date`` is the Jan-1 2099 the instant
        becomes in UTC.  The balance sheet as of 2098-12-31 already reflects it,
        confirming the readers attribute on the SAME display-timezone basis.
        """
        with app.app_context():
            user_id = seed_user["user"].id
            create_settled_cash_transaction(
                seed_user, seed_user["bootstrap_period"],
                Decimal("500.00"), account=seed_user["account"],
                category=seed_user["categories"]["Groceries"],
                settled_on=date(2098, 12, 31),
            )
            db.session.commit()

            in_2098 = ledger_report_service.compute_income_statement(
                user_id, calendar_for(user_id), StatementWindow("year", year=2098),
            )
            assert in_2098.expense.total == Decimal("500.00")
            in_2099 = ledger_report_service.compute_income_statement(
                user_id, calendar_for(user_id), StatementWindow("year", year=2099),
            )
            assert not in_2099.expense.lines

            # The balance sheet dated on that civil day already reflects the
            # spend (Checking 1000 - 500 = 500), the same wall-clock basis.
            sheet = _reader_bs(user_id, date(2098, 12, 31))
            assert _find_line(
                sheet.assets.lines, "Checking",
            ).amount == Decimal("500.00")
            assert sheet.tie_out.in_balance is True
            _assert_ledger_self_consistent()

    def test_a_settle_on_the_period_start_attributes_to_that_day(
        self, app, db, seed_user,
    ):
        """A settle dated on its pay period's start attributes to that day.

        A $150.00 Groceries expense settled 2099-08-03, in a period starting
        2099-08-03.  So the August 2099 month window includes it and July does
        not, and it folds onto an as-of-2099-08-03 balance sheet.

        **It reached that day through a FALLBACK until plan step X-f1** -- the
        row carried no ``paid_at`` and the reader substituted the period's
        ``start_date`` -- and this case was named for the fallback.  The day is
        a stored fact now and the substitution is gone, so the fixture states
        the day it always meant; every figure below is unchanged, because the
        day is.  What the case still grades is real and unrelated to the
        fallback: a settle day sitting exactly ON a window boundary belongs to
        the LATER window, and to the balance sheet as of that day.
        """
        with app.app_context():
            user_id = seed_user["user"].id
            period = PayPeriod(user_id=user_id, start_date=date(_Y, 8, 3))
            db.session.add(period)
            db.session.flush()
            create_settled_cash_transaction(
                seed_user, period, Decimal("150.00"),
                account=seed_user["account"],
                category=seed_user["categories"]["Groceries"],
                settled_on=period.start_date,
            )
            db.session.commit()

            august = ledger_report_service.compute_income_statement(
                user_id, calendar_for(user_id), StatementWindow("month", month=8, year=_Y),
            )
            assert august.expense.total == Decimal("150.00")
            july = ledger_report_service.compute_income_statement(
                user_id, calendar_for(user_id), StatementWindow("month", month=7, year=_Y),
            )
            assert not july.expense.lines

            before = _reader_bs(user_id, date(_Y, 8, 2))
            assert _find_line(
                before.assets.lines, "Checking",
            ).amount == Decimal("1000.00")
            on_day = _reader_bs(user_id, date(_Y, 8, 3))
            assert _find_line(
                on_day.assets.lines, "Checking",
            ).amount == Decimal("850.00")
            _assert_ledger_self_consistent()

    def test_early_settled_source_appears_in_its_paid_year(
        self, app, db, seed_user,
    ):
        """An early-settled future-period source lands in its paid calendar year.

        A $250.00 Groceries expense settled with a paid date in 2099-11 but
        placed in a pay period whose ``start_date`` is 2100-01-05 (an
        early-settled future-period source): the calendar year 2099 window
        attributes it to its PAID date (present, 250.00) while the 2099
        pay-period-less calendar view and the 2100 window differ -- both honest
        answers to different questions (C-2 vs C-3), the documented divergence.
        """
        with app.app_context():
            user_id = seed_user["user"].id
            future_period = PayPeriod(
                user_id=user_id,
                start_date=date(2100, 1, 5),
            )
            db.session.add(future_period)
            db.session.flush()
            create_settled_cash_transaction(
                seed_user, future_period, Decimal("250.00"),
                account=seed_user["account"],
                category=seed_user["categories"]["Groceries"],
                settled_on=date(2099, 11, 20),
            )
            db.session.commit()

            # Calendar 2099 (the paid year) sees it; the pay period lives in 2100.
            paid_year = ledger_report_service.compute_income_statement(
                user_id, calendar_for(user_id), StatementWindow("year", year=2099),
            )
            assert paid_year.expense.total == Decimal("250.00")
            # The pay-period window (its period start is 2100) still sees it,
            # because a pay-period window keys on the entry's period, not a date.
            by_period = ledger_report_service.compute_income_statement(
                user_id, calendar_for(user_id),
                StatementWindow("pay_period", period_id=future_period.id),
            )
            assert by_period.expense.total == Decimal("250.00")
            # The 2100 calendar window does NOT (the paid date is 2099).
            future_year = ledger_report_service.compute_income_statement(
                user_id, calendar_for(user_id), StatementWindow("year", year=2100),
            )
            assert not future_year.expense.lines
            _assert_ledger_self_consistent()
