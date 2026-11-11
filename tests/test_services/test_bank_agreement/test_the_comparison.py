"""What is compared: no statement, a true-up, the residue, the span to now, the drawn days."""

from datetime import date, timedelta
from decimal import Decimal

import pytest

from app.services import bank_agreement, cash_ledger
from app.services.balance_at import BalanceContext
from app.services.scenario_resolver import get_baseline_scenario
from app.services.statement_match._hero import hero as statement_hero
from tests._test_helpers import append_balance_assertion
from tests.test_services.test_cash_fold import _instant
from tests.test_services.test_outstanding_difference import _settle
from tests.test_services.test_statement_import.test_anchor import _seed_import

from ._helpers import _FILE_CHAIN, _UNCORROBORATED, _ZERO, _settled, _agreement, _day


class TestThereIsNothingToCompareWithoutAStatement:
    """An absence, not an empty comparison."""

    def test_an_account_with_no_recorded_line_answers_NOTHING(
        self, app, seed_user, seed_periods, db,
    ):
        """No outside record exists, so there is no comparison to draw."""
        with app.app_context():
            assert _agreement(seed_user) is None


class TestATrueUpCannotHideADisagreement:
    """The measurement the design turns on."""

    def test_a_day_whose_gap_is_ZERO_still_reports_its_residue(
        self, app, seed_user, seed_periods, db,
    ):
        """The 2026-06-02 shape: real money missing, balances identical.

        The app records ``-100.00`` the bank never posted, and the owner trues
        the balance up by ``+100.00`` the same day.  Both running balances land
        on the same figure, so a report built on the difference of two balances
        prints ``$0.00`` and calls it agreement -- while ``$100.00`` of spending
        exists on exactly one of the two records.
        """
        with app.app_context():
            _seed_import(
                db, seed_user["account"], stated="1000.00",
                effective_on=date(2026, 3, 4), evidence=_FILE_CHAIN,
                lines=[(date(2026, 3, 2), "50.00"),
                       (date(2026, 3, 4), "-25.00")],
            )
            _settled(
                db, seed_user, seed_periods[4], "Card payback", "100.00",
                date(2026, 3, 3),
            )
            append_balance_assertion(
                db.session, seed_user["account"],
                Decimal("1234.00"), _instant(2026, 3, 3),
            )
            db.session.commit()

            row = _day(_agreement(seed_user), date(2026, 3, 3))

            # The instrument sees it...
            assert row.recorded == Decimal("-100.00")
            assert row.bank_lines == _ZERO
            assert row.residue == Decimal("-100.00")
            assert row.agrees is False
            # ...and the true-up is NAMED rather than folded into the movement.
            assert row.asserted != _ZERO

    def test_agrees_is_decided_on_the_RESIDUE_and_never_on_the_gap(
        self, app, seed_user, seed_periods, db,
    ):
        """A FIRING control over the one predicate the design replaced.

        Testing ``agrees`` on the gap instead leaves this the failing case: the
        two balances are engineered equal on the very day the records differ.
        """
        with app.app_context():
            _seed_import(
                db, seed_user["account"], stated="1000.00",
                effective_on=date(2026, 3, 4), evidence=_FILE_CHAIN,
                lines=[(date(2026, 3, 2), "50.00"),
                       (date(2026, 3, 4), "-25.00")],
            )
            _settled(
                db, seed_user, seed_periods[4], "Card payback", "100.00",
                date(2026, 3, 3),
            )
            append_balance_assertion(
                db.session, seed_user["account"],
                Decimal("1025.00"), _instant(2026, 3, 3),
            )
            db.session.commit()

            row = _day(_agreement(seed_user), date(2026, 3, 3))

            assert row.gap == _ZERO
            assert row.residue != _ZERO
            assert row.agrees is False


class TestTheResidueIsWhatTheTwoRecordsDisagreeAbout:
    """What each side has that the other does not."""

    def test_a_line_the_app_never_recorded_shows_as_a_residue(
        self, app, seed_user, seed_periods, db,
    ):
        """The bank moved money the app has no row for, BOTH ways.

        A ``-60.00`` debit nobody entered leaves the app's total ``+60.00``
        AHEAD; a ``+10.00`` deposit nobody entered leaves it ``-10.00``
        behind.  Both are "the bank recorded something the app did not", and
        they land in OPPOSITE totals -- which is why those totals are named for
        which balance ends higher rather than for whose record is missing an
        item.
        """
        with app.app_context():
            _seed_import(
                db, seed_user["account"], stated="1000.00",
                effective_on=date(2026, 3, 3), evidence=_FILE_CHAIN,
                lines=[(date(2026, 3, 2), "-60.00"),
                       (date(2026, 3, 3), "10.00")],
            )
            db.session.commit()

            agreement = _agreement(seed_user)

            assert _day(agreement, date(2026, 3, 2)).residue == Decimal("60.00")
            assert _day(agreement, date(2026, 3, 3)).residue == Decimal("-10.00")
            assert agreement.app_ahead == Decimal("60.00")
            assert agreement.bank_ahead == Decimal("10.00")

    def test_a_matching_day_agrees_and_is_not_counted(
        self, app, seed_user, seed_periods, db,
    ):
        """Both records say the same thing, so nothing is reported."""
        with app.app_context():
            _seed_import(
                db, seed_user["account"], stated="1000.00",
                effective_on=date(2026, 3, 3), evidence=_FILE_CHAIN,
                lines=[(date(2026, 3, 3), "-40.00")],
            )
            _settled(
                db, seed_user, seed_periods[4], "Groceries", "40.00",
                date(2026, 3, 3),
            )
            db.session.commit()

            agreement = _agreement(seed_user)

            assert _day(agreement, date(2026, 3, 3)).agrees is True
            assert agreement.disagreeing == []


class TestTheSpanStopsAtTheReadersNow:
    """Past today the app's balance is a PLAN, and a plan is not a record."""

    def test_a_line_dated_beyond_today_is_not_compared(
        self, app, seed_user, seed_periods, db,
    ):
        """Ruling R-G clamps a still-Projected row to ``as_of + 1``.

        So every day past the reader's NOW carries plan, and putting a plan
        beside a bank line would report agreement where the app has merely
        predicted what the bank did -- or a disagreement about money nobody has
        spent yet.  A FIRING control: without the bound the span runs to the
        last recorded line and those days are compared.
        """
        with app.app_context():
            ctx = BalanceContext.build(seed_user["user"].id)
            beyond = ctx.as_of + timedelta(days=7)
            _seed_import(
                db, seed_user["account"], stated="1000.00",
                effective_on=ctx.as_of, evidence=_FILE_CHAIN,
                lines=[(ctx.as_of, "-10.00"), (beyond, "-99.00")],
            )
            db.session.commit()

            agreement = _agreement(seed_user)

            assert agreement.span.last_day == ctx.as_of
            assert all(row.day <= ctx.as_of for row in agreement.days)

    def test_a_statement_ENTIRELY_in_the_future_is_not_reported_as_ABSENT(
        self, app, seed_user, seed_periods, db,
    ):
        """The lines exist; none of them is comparable yet.

        **A reproduced defect in this step's own first build.** Answering
        ``None`` here made the page say "this account has no recorded bank
        lines" over a file full of them -- a false statement about the owner's
        own data.  Nothing in the importer refuses a future-dated line, so the
        shape is reachable rather than theoretical.
        """
        with app.app_context():
            ctx = BalanceContext.build(seed_user["user"].id)
            beyond = ctx.as_of + timedelta(days=5)
            _seed_import(
                db, seed_user["account"], stated="1000.00",
                effective_on=beyond, evidence=_FILE_CHAIN,
                lines=[(beyond, "-99.00"),
                       (beyond + timedelta(days=1), "-1.00")],
            )
            db.session.commit()

            agreement = _agreement(seed_user)

            assert agreement is not None
            assert agreement.days == []
            assert agreement.span.recorded_through == beyond + timedelta(days=1)
            assert agreement.compared == []
            assert agreement.constant_offset is None

    def test_the_truncation_is_REPORTED_and_never_silent(
        self, app, seed_user, seed_periods, db,
    ):
        """A span stopping short of what is recorded must say so.

        A report of part of the record, presented as a report of the record,
        understates every total on it -- and this page's whole purpose is to be
        the control that other leaves are checked against.
        """
        with app.app_context():
            ctx = BalanceContext.build(seed_user["user"].id)
            beyond = ctx.as_of + timedelta(days=7)
            _seed_import(
                db, seed_user["account"], stated="1000.00",
                effective_on=ctx.as_of, evidence=_FILE_CHAIN,
                lines=[(ctx.as_of, "-10.00"), (beyond, "-99.00")],
            )
            db.session.commit()

            agreement = _agreement(seed_user)

            assert agreement.span.recorded_through == beyond
            assert agreement.span.ends_early

    def test_an_ordinary_span_reports_no_truncation(
        self, app, seed_user, seed_periods, db,
    ):
        """The common case: every recorded line is inside the comparison."""
        with app.app_context():
            _seed_import(
                db, seed_user["account"], stated="1000.00",
                effective_on=date(2026, 3, 3), evidence=_FILE_CHAIN,
                lines=[(date(2026, 3, 3), "-40.00")],
            )
            db.session.commit()

            agreement = _agreement(seed_user)

            assert not agreement.span.ends_early


class TestTheDrawnDaysRunToTheBooksLastRecord:
    """Finding **BAL-651**, rulings plan#2346, plan#2399 and plan#2400.

    The drawn days stopped at the last statement line, so a record made after
    every statement was never drawn and the Reconcile page read Done over it.
    They run to the later of the bank's last line and the books' last record
    now, never past NOW, while the LEVEL summary still reads through the
    bank's last drawn day.
    """

    #: The finding's own dates: the statement ends Feb 2, rows settle Feb 15
    #: and Feb 23, and the reader's NOW is Mar 20.
    _LAST_LINE = date(2026, 2, 2)
    _NOW = date(2026, 3, 20)

    def _agreement_at(self, seed_user, as_of):
        """Return the seed account's comparison with NOW pinned to *as_of*."""
        return bank_agreement.bank_agreement(
            seed_user["account"],
            BalanceContext.build(seed_user["user"].id, as_of=as_of),
        )

    def _statement(self, db, seed_user, *lines):
        """Record one anchored statement on the seed account.

        Args:
            db: The session fixture.
            seed_user: The seeded user bundle.
            *lines: ``(day, amount)`` pairs; the figure is placed on the last.
        """
        _seed_import(
            db, seed_user["account"], stated="1000.00",
            effective_on=lines[-1][0], evidence=_FILE_CHAIN, lines=list(lines),
        )

    def test_rows_settled_after_every_statement_are_drawn_UNPRICED(
        self, app, seed_user, seed_periods, db,
    ):
        """The finding's shape: Feb 15 and Feb 23 are drawn, and the hero counts them.

        Before the fix the span ended on Feb 2, so the hero's ``unpriced_after``
        was 0 and the page read Done.  Feb 3 through Feb 23 is 21 days, every
        one past the only import's window, so the bank neither moves nor
        prices any of them, and none is compared.
        """
        with app.app_context():
            self._statement(
                db, seed_user, (date(2026, 1, 30), "-10.00"),
                (self._LAST_LINE, "-20.00"),
            )
            _settle(seed_user, seed_periods[4], "40.00", date(2026, 2, 15))
            _settle(seed_user, seed_periods[4], "60.00", date(2026, 2, 23))
            db.session.commit()

            agreement = self._agreement_at(seed_user, self._NOW)

            assert agreement.last_record_on == date(2026, 2, 23)
            assert agreement.span.last_day == date(2026, 2, 23)
            assert agreement.span.bank_through == self._LAST_LINE
            tail = [row for row in agreement.days if row.day > self._LAST_LINE]
            assert [row.day for row in tail] == [
                self._LAST_LINE + timedelta(days=offset)
                for offset in range(1, 22)
            ]
            assert all(
                row.bank_lines is None and row.bank_balance is None
                and not row.compared
                for row in tail
            )
            # Signed positive money IN: two expenses leave the account.
            assert _day(agreement, date(2026, 2, 15)).recorded == Decimal("-40.00")
            assert _day(agreement, date(2026, 2, 23)).recorded == Decimal("-60.00")
            assert agreement.unpriced_days == 21
            assert agreement.headline.day == self._LAST_LINE

            hero = statement_hero(agreement, 0)

            assert hero.day == self._LAST_LINE
            assert hero.unpriced_after == 21
            assert hero.records_through == date(2026, 2, 23)

    @pytest.mark.parametrize(("declared", "moved"), [
        ("980.00", "-20.00"),
        ("1000.00", "0.00"),
    ])
    def test_a_TRUE_UP_after_every_statement_is_a_record(
        self, app, seed_user, seed_periods, db, declared, moved,
    ):
        """Ruling plan#2400: a true-up speaks for the books on a day no statement priced.

        No row is recorded after Feb 2, only a true-up on Feb 20 declaring
        *declared*, and the drawn days run to it: Feb 3 through Feb 20 is 18
        unpriced days.  The seeded books hold ``1000.00`` throughout (the
        bank's Feb 2 line is the bank's alone), so the true-up moves
        ``declared - 1000.00``: ``980.00 - 1000.00 = -20.00``, or
        ``1000.00 - 1000.00 = 0.00`` for one agreeing with the records to the
        cent, which is a record all the same because a record is read by its
        day.  The hero stays on the bank's last line, Feb 2, and names Feb 20
        as how far the records run.
        """
        with app.app_context():
            self._statement(db, seed_user, (self._LAST_LINE, "-20.00"))
            append_balance_assertion(
                db.session, seed_user["account"],
                Decimal(declared), _instant(2026, 2, 20),
            )
            db.session.commit()

            agreement = self._agreement_at(seed_user, self._NOW)

            assert agreement.last_record_on == date(2026, 2, 20)
            assert agreement.span.last_day == date(2026, 2, 20)
            assert _day(agreement, date(2026, 2, 20)).asserted == Decimal(moved)
            hero = statement_hero(agreement, 0)
            assert hero.day == self._LAST_LINE
            assert hero.unpriced_after == 18
            assert hero.records_through == date(2026, 2, 20)

    def test_records_that_end_before_the_last_line_leave_the_end_ON_the_line(
        self, app, seed_user, seed_periods, db,
    ):
        """The mirror: a statement reaching past the last record is drawn as before.

        The row on Feb 15 is the books' last record and the statement runs to
        Feb 25, so the drawn days end on Feb 25, the bank's last drawn day is
        the same day, and the hero names no records running past it.
        """
        with app.app_context():
            self._statement(
                db, seed_user, (self._LAST_LINE, "-20.00"),
                (date(2026, 2, 25), "-5.00"),
            )
            _settle(seed_user, seed_periods[4], "40.00", date(2026, 2, 15))
            db.session.commit()

            agreement = self._agreement_at(seed_user, self._NOW)

            assert agreement.last_record_on == date(2026, 2, 15)
            assert agreement.span.last_day == date(2026, 2, 25)
            assert agreement.span.bank_through == date(2026, 2, 25)
            hero = statement_hero(agreement, 0)
            assert hero.day == date(2026, 2, 25)
            assert hero.unpriced_after == 0
            assert hero.records_through is None

    def test_a_record_dated_after_NOW_does_not_move_the_end(
        self, app, seed_user, seed_periods, db,
    ):
        """The NOW bound is load-bearing (ruling R-G), and it binds the records too.

        With NOW pinned to Feb 20, the row settled Feb 23 is not a record yet,
        so the books' last record is Feb 15 and the drawn days end there.
        """
        with app.app_context():
            self._statement(db, seed_user, (self._LAST_LINE, "-20.00"))
            _settle(seed_user, seed_periods[4], "40.00", date(2026, 2, 15))
            _settle(seed_user, seed_periods[4], "60.00", date(2026, 2, 23))
            db.session.commit()

            agreement = self._agreement_at(seed_user, date(2026, 2, 20))

            assert agreement.last_record_on == date(2026, 2, 15)
            assert agreement.span.last_day == date(2026, 2, 15)
            assert all(row.day <= date(2026, 2, 20) for row in agreement.days)

    def test_the_LEVEL_summary_still_reads_through_the_banks_last_day(
        self, app, seed_user, seed_periods, db,
    ):
        """Ruling plan#2399: the summary numbers stay as they were.

        A row settled after the statement draws an unpriced day past it.  Read
        on the last DRAWN day, the standing gap would be ``None`` there and the
        constant offset would refuse over it; both read through the bank's last
        drawn day instead, so the offset the statement's two days share is
        still reported, and the standing gap is that offset.
        """
        with app.app_context():
            walk = cash_ledger.walk_cash_ledger(
                seed_user["account"].id,
                get_baseline_scenario(seed_user["user"].id).id,
            )
            opening = walk.anchor_facts[0].observed_on
            first, second = (
                opening + timedelta(days=1), opening + timedelta(days=2),
            )
            _seed_import(
                db, seed_user["account"], stated="500.00",
                effective_on=second, evidence=_UNCORROBORATED,
                lines=[(first, "-40.00"), (second, "-10.00")],
            )
            _settled(
                db, seed_user, seed_periods[0], "Groceries", "40.00", first,
            )
            _settled(
                db, seed_user, seed_periods[0], "Coffee", "10.00", second,
            )
            later = second + timedelta(days=5)
            _settled(db, seed_user, seed_periods[0], "Lunch", "12.00", later)
            db.session.commit()

            agreement = _agreement(seed_user)

            assert agreement.span.last_day == later
            assert agreement.span.bank_through == second
            assert _day(agreement, later).gap is None
            # The seeded books open at 1000.00: 1000.00 - 40.00 = 960.00 on
            # the first day and 960.00 - 10.00 = 950.00 on the second; the
            # bank states 500.00 on the second, so 500.00 + 10.00 = 510.00 on
            # the first.  960.00 - 510.00 = 950.00 - 500.00 = 450.00 each day.
            assert agreement.constant_offset == Decimal("450.00")
            assert agreement.standing_gap == Decimal("450.00")

    def test_a_record_past_the_last_line_moves_NO_compared_day(
        self, app, seed_user, seed_periods, db,
    ):
        """Ruling plan#2399, past two years: the window counts back from the bank's end.

        The lines run 2024-01-10 to 2026-03-20, longer than the two-year
        bound, and a row settles 2026-04-19, after both.  The window starts
        731 days before the bank's last line: 2026-03-20 less 730 days is
        2024-03-20 (no Feb 29 falls between).  Counted back from the books'
        last record instead it started 2024-04-19, and thirty compared days
        fell off the start of the summary.
        """
        with app.app_context():
            self._statement(
                db, seed_user, (date(2024, 1, 10), "-10.00"),
                (date(2026, 3, 20), "-20.00"),
            )
            _settle(seed_user, seed_periods[4], "40.00", date(2026, 4, 19))
            db.session.commit()

            agreement = self._agreement_at(seed_user, date(2026, 5, 1))

            assert agreement.span.first_day == date(2024, 3, 20)
            assert agreement.span.bank_through == date(2026, 3, 20)
            assert agreement.span.last_day == date(2026, 4, 19)
            assert agreement.span.starts_late is True

    def test_the_days_past_the_last_line_are_BOUNDED_apart(
        self, app, seed_user, seed_periods, db,
    ):
        """The two-year bound's own reason holds for the tail.

        One line on 2024-01-10 and a row settled 2026-04-19: drawn to the
        row, the report would draw 831 days past the line.  The tail stops 731
        days after it: 2024-01-10 plus 366 days (2024 is a leap year) is
        2025-01-10, plus 365 is 2026-01-10.
        """
        with app.app_context():
            self._statement(db, seed_user, (date(2024, 1, 10), "-10.00"))
            _settle(seed_user, seed_periods[4], "40.00", date(2026, 4, 19))
            db.session.commit()

            agreement = self._agreement_at(seed_user, date(2026, 5, 1))

            assert agreement.span.first_day == date(2024, 1, 10)
            assert agreement.span.last_day == date(2026, 1, 10)
            assert len(agreement.days) == 732
