"""
Shekel Budget App -- The app's books beside the BANK's own record.

Plan step **bank_import:X-f6e-2**, ruling **R-GF**.  The comparison is an
INSTRUMENT and never a gate, so nothing here refuses anything; what these tests
pin is that it SEES what it is for.

**The load-bearing case is ``TestATrueUpCannotHideADisagreement``**, and it is
the measurement the whole design turns on.  On the developer's own Checking
account, over the 149 days his statement and his records both cover, 35 days
carry a real disagreement between his rows and the bank's lines -- and ELEVEN of
them read as EXACT agreement in the balance difference, because a same-day
balance assertion cancels the error to the cent.  Among the eleven:

* 2026-06-02, ``-$943.41`` -- six ``CC Payback`` rows against a day his bank
  posted no line at all.  That is finding **N-337**, which this step is
  specified to detect, on a day whose two running balances are identical.
* 2026-07-31, ``-$2,090.47``; 2026-08-18, ``-$1,463.04``.
* 2026-05-21 and 2026-06-04, ``-$0.05`` each -- the payroll residue finding
  **N-239** measures.

A report built on the balance difference alone prints "agrees" on every one of
those days.  So the residue is what ``agrees`` is tested on, and this suite
asserts a day whose gap is ZERO and whose residue is not is reported as a
disagreement.
"""

from datetime import date, timedelta
from decimal import Decimal

import pytest

from app import ref_cache
from app.enums import (
    StatementBalanceEvidenceEnum,
    StatusEnum,
    TxnTypeEnum,
)
from app.models.statement_import import BankStatementLine
from app.models.statement_match import (
    StatementMatch,
    StatementMatchMember,
)
from app.services import (
    bank_agreement,
    cash_ledger,
    transaction_service,
)
from app.services.balance_at import BalanceContext
from app.services.scenario_resolver import get_baseline_scenario
from app.services.statement_match._hero import hero as statement_hero
from tests._test_helpers import (
    add_entry,
    an_entered_day,
    append_balance_assertion,
    create_account_of_type,
    generate_row_of,
    make_expense_template,
    make_income_template,
    one_off_row_of,
    settle_day_columns,
    cover_bare_settled_row,
)
from tests.test_services.test_cash_fold import _instant
from tests.test_services.test_outstanding_difference import (
    _assert_balance,
    _opened_on,
    _settle,
)
from tests.test_services.test_statement_import.test_anchor import _seed_import

_FILE_CHAIN = StatementBalanceEvidenceEnum.FILE_CHAIN
_UNCORROBORATED = StatementBalanceEvidenceEnum.UNCORROBORATED
_ZERO = Decimal("0.00")


def _settled(db, seed_user, period, name, amount, day, *, is_income=False):
    """Insert one SETTLED row whose cash moved on *day*."""
    status_id = ref_cache.status_id(StatusEnum.DONE)
    txn = one_off_row_of(
        period,
        name=name,
        amount=Decimal(str(amount)),
        user_id=period.user_id,
        account_id=seed_user["account"].id,
        scenario_id=seed_user["scenario"].id,
        transaction_type_id=ref_cache.txn_type_id(
            TxnTypeEnum.INCOME if is_income else TxnTypeEnum.EXPENSE,
        ),
    )
    txn.status_id = status_id
    # The settle day laid on BARE, as ``add_txn`` lays it (X-f1); the
    # record is the covering movement written after the flush (X-bi-4b-2).
    for _column, _value in settle_day_columns(day).items():
        setattr(txn, _column, _value)
    db.session.flush()
    cover_bare_settled_row(db.session, txn, amount, amount)
    return txn


def _agreement(seed_user):
    """Return the comparison for the seed user's account."""
    return bank_agreement.bank_agreement(
        seed_user["account"], BalanceContext.build(seed_user["user"].id),
    )


def _day(agreement, on):
    """Return the one :class:`AgreementDay` for *on*."""
    return next(row for row in agreement.days if row.day == on)


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
            _settle(seed_user, db, seed_periods[4], "40.00", date(2026, 2, 15))
            _settle(seed_user, db, seed_periods[4], "60.00", date(2026, 2, 23))
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
            _settle(seed_user, db, seed_periods[4], "40.00", date(2026, 2, 15))
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
            _settle(seed_user, db, seed_periods[4], "40.00", date(2026, 2, 15))
            _settle(seed_user, db, seed_periods[4], "60.00", date(2026, 2, 23))
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
            _settle(seed_user, db, seed_periods[4], "40.00", date(2026, 4, 19))
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
            _settle(seed_user, db, seed_periods[4], "40.00", date(2026, 4, 19))
            db.session.commit()

            agreement = self._agreement_at(seed_user, date(2026, 5, 1))

            assert agreement.span.first_day == date(2024, 1, 10)
            assert agreement.span.last_day == date(2026, 1, 10)
            assert len(agreement.days) == 732


class TestDaysBeforeTheRecordsBeginAreLabelledNotCounted:
    """Finding N-314 is the balance arc's, not this report's.

    "Before the records begin" means ON OR BEFORE the day the books open
    (ruling **R-HG**, :func:`~app.utils.books_boundary.books_hold`); the
    seeded account's books open 2024-01-04, the day before its origination
    assertion, so both cases below date their lines before that.
    """

    def test_a_day_before_the_books_open_is_out_of_the_totals(
        self, app, seed_user, seed_periods, db,
    ):
        """The developer's own shape: months of statement before the books.

        The bank's line is SHOWN -- hiding a day the bank describes would be
        the report deciding what may be seen -- and excluded from the counts,
        because the app holding nothing that far back is not a disagreement
        this arc can act on.
        """
        with app.app_context():
            walk = cash_ledger.walk_cash_ledger(
                seed_user["account"].id,
                get_baseline_scenario(seed_user["user"].id).id,
            )
            before = walk.anchor_facts[0].observed_on - timedelta(days=5)
            _seed_import(
                db, seed_user["account"], stated="1000.00",
                effective_on=before, evidence=_FILE_CHAIN,
                lines=[(before, "-77.00")],
            )
            db.session.commit()

            agreement = _agreement(seed_user)
            row = _day(agreement, before)

            # The case is about a day the books do not cover, so it must be one.
            assert before < walk.opening.opened_on
            # Before the books open they state neither a movement nor a
            # balance, so both are ABSENT rather than 0.00 (rulings R-BAL286,
            # R-BAL291) and the day is not compared.
            assert row.recorded is None
            assert row.app_balance is None
            assert row.compared is False
            assert row.bank_lines == Decimal("-77.00")
            assert agreement.compared == []
            assert agreement.disagreeing == []
            assert agreement.app_ahead == _ZERO


    def test_a_statement_ENTIRELY_before_the_records_compares_nothing(
        self, app, seed_user, seed_periods, db,
    ):
        """Every day is outside the records, so every total is zero.

        The far edge of the developer's own shape: his statement reaches 83
        days before his records, and a statement reaching back further still
        would reach before ALL of them.  The report must then say it compared
        nothing rather than report the whole span as disagreement -- which is
        the difference between "your books are wrong by $15,000" and "your
        books do not go back this far".
        """
        with app.app_context():
            walk = cash_ledger.walk_cash_ledger(
                seed_user["account"].id,
                get_baseline_scenario(seed_user["user"].id).id,
            )
            begins = walk.anchor_facts[0].observed_on
            _seed_import(
                db, seed_user["account"], stated="1000.00",
                effective_on=begins - timedelta(days=10),
                evidence=_FILE_CHAIN,
                lines=[(begins - timedelta(days=20), "-500.00"),
                       (begins - timedelta(days=10), "-77.00")],
            )
            db.session.commit()

            agreement = _agreement(seed_user)

            assert agreement.days != []
            assert agreement.compared == []
            assert agreement.disagreeing == []
            assert agreement.app_ahead == _ZERO
            assert agreement.bank_ahead == _ZERO
            assert agreement.asserted_total == _ZERO
            assert agreement.constant_offset is None


class TestTheComparisonStartsTheDayAfterTheBooksOpen:
    """Finding **BAL-616**, plan step balance:X-f3c-2b-2c, rulings **R-BAL285** / **R-BAL291**.

    The books hold MOVEMENT from the day AFTER the one they open on (ruling
    **R-HG**: the opening equity is that day's closing balance) -- the days
    :func:`~app.utils.books_boundary.books_hold` admits -- so on an imported
    day the comparison takes exactly those.  It started at the account's
    first CASH FACT until this step, which was wrong in BOTH directions, and
    each case below is one of them: the first is the day a books-side
    boundary one day EARLY would count, the second the day one a day LATE
    would drop.  Every figure is made up.
    """

    def test_a_bank_line_ON_the_opening_day_is_inside_the_opening(
        self, app, seed_user, seed_periods, db,
    ):
        """Books open 2026-02-02; the bank's ``-25.00`` that day is not counted.

        The production shape: ``account_service.create_account`` writes the
        opening and the origination assertion on ONE day, so that assertion
        was the first cash fact and the old boundary put the opening day
        itself into the totals -- a ``+25.00`` residue (``0.00`` recorded less
        ``-25.00`` banked) for money the ``$500.00`` opening already holds.
        Feb 4's ``-40.00`` has its row and agrees, so the comparison is not
        empty and the only thing that could disagree is the opening day.
        """
        with app.app_context():
            account = _opened_on(seed_user, date(2026, 2, 2))
            _seed_import(
                db, account,
                lines=[(date(2026, 2, 2), "-25.00"),
                       (date(2026, 2, 4), "-40.00")],
            )
            _settle(
                seed_user, db, seed_periods[2], "40.00", date(2026, 2, 4),
                account=account,
            )
            db.session.commit()

            agreement = bank_agreement.bank_agreement(
                account, BalanceContext.build(seed_user["user"].id),
            )
            opening_day = _day(agreement, date(2026, 2, 2))

            assert agreement.books_opened_on == date(2026, 2, 2)
            # Shown, never hidden: the bank's line is on the row.
            assert opening_day.bank_lines == Decimal("-25.00")
            # Inside the opening: the books hold no movement for it.
            assert opening_day.recorded is None
            assert opening_day.compared is False
            assert [day.day for day in agreement.compared] == [
                date(2026, 2, 3), date(2026, 2, 4),
            ]
            assert agreement.disagreeing == []
            assert agreement.app_ahead == _ZERO
            assert agreement.bank_ahead == _ZERO

    def test_a_books_day_BEFORE_the_first_entry_is_compared(
        self, app, seed_user, seed_periods, db,
    ):
        """Books open 2026-01-01; the first entry is a row on Jan 3.

        ``create_account_of_type`` restates the books back to 2026-01-01,
        before the owner's calendar, while the first thing recorded is the
        ``10.00`` row settled on Jan 3.  The bank's ``-40.00`` on Jan 2 has no
        row: the books claim Jan 2, so that is a real disagreement -- a
        ``+40.00`` residue, ``0.00`` recorded less ``-40.00`` banked.  The old
        boundary started at Jan 3 and never compared Jan 2 at all, so the
        ``$40.00`` was shown on its row and counted in no total.
        """
        with app.app_context():
            late = create_account_of_type(
                seed_user, db.session, "Checking", "Late Checking",
                anchor_balance=Decimal("500.00"),
            )
            _seed_import(
                db, late,
                lines=[(date(2026, 1, 2), "-40.00"),
                       (date(2026, 1, 3), "-10.00")],
            )
            _settle(
                seed_user, db, seed_periods[0], "10.00", date(2026, 1, 3),
                account=late,
            )
            db.session.commit()

            agreement = bank_agreement.bank_agreement(
                late, BalanceContext.build(seed_user["user"].id),
            )
            day_after_open = _day(agreement, date(2026, 1, 2))

            assert agreement.books_opened_on == date(2026, 1, 1)
            assert day_after_open.compared is True
            assert day_after_open.recorded == _ZERO
            assert day_after_open.residue == Decimal("40.00")
            assert [day.day for day in agreement.disagreeing] == [
                date(2026, 1, 2),
            ]
            assert agreement.app_ahead == Decimal("40.00")
            assert _day(agreement, date(2026, 1, 3)).agrees is True


class TestTheBooksStateABalanceFromTheirOpeningDay:
    """Ruling **R-BAL286**, plan step balance:X-f3c-2b-2c.

    The books open at the CLOSE of their opening day (ruling **R-HG**), so
    their BALANCE is known from that day's close and their MOVEMENT from the
    day after.  So the opening day is compared for its balance and not for its
    movement, and a day before it has no books balance at all.  The shape is
    the natural first import: an account created on Feb 2
    from a statement ending Feb 2, whose bank states ``$500.00`` that day.  Its
    lines are Jan 30 ``-25.00`` and Feb 2 ``-10.00``, both inside the opening.
    Every figure is made up; they are the ones the ruling's question showed.
    """

    def _first_import(self, seed_user, db, typed):
        """Create the account at *typed* on Feb 2 and import the statement.

        Args:
            seed_user: The seeded user bundle.
            db: The database fixture.
            typed: The opening balance the owner typed, as a string.

        Returns:
            The new account.
        """
        account = _opened_on(seed_user, date(2026, 2, 2), typed)
        _seed_import(
            db, account, stated="500.00", effective_on=date(2026, 2, 2),
            evidence=_FILE_CHAIN,
            lines=[(date(2026, 1, 30), "-25.00"),
                   (date(2026, 2, 2), "-10.00")],
            file_name="first.csv",
        )
        return account

    def test_the_opening_day_is_the_HEADLINE_and_earlier_days_state_no_balance(
        self, app, seed_user, seed_periods, db,
    ):
        """Feb 2 shows $500.00 against $500.00, off by $0.00.

        The headline and standing gap are the same on the app before plan
        step balance:X-f3c-2b-2c: the origination assertion made Feb 2 the
        first cash fact, so it was compared.  *What that app did differently
        here: the days before Feb 2 showed a "Your balance" the books never
        stated (now none), and Feb 2's ``-10.00`` line, inside the opening,
        counted as a ``+10.00`` disagreement (now not compared).*
        """
        with app.app_context():
            account = self._first_import(seed_user, db, "500.00")
            db.session.commit()

            agreement = bank_agreement.bank_agreement(
                account, BalanceContext.build(seed_user["user"].id),
            )

            assert agreement.books_opened_on == date(2026, 2, 2)
            headline = agreement.headline
            assert headline.day == date(2026, 2, 2)
            assert headline.bank_balance == Decimal("500.00")
            assert headline.app_balance == Decimal("500.00")
            # 500.00 - 500.00.
            assert headline.gap == _ZERO
            assert agreement.standing_gap == _ZERO
            for day in (date(2026, 1, 30), date(2026, 1, 31),
                        date(2026, 2, 1)):
                assert _day(agreement, day).app_balance is None
                assert _day(agreement, day).gap is None
            # The MOVEMENT half is unchanged: every drawn day is on or before
            # the opening, so nothing is compared and nothing disagrees.
            assert agreement.compared == []
            assert agreement.disagreeing == []

    def test_a_TYPO_in_the_opening_shows_on_its_first_day(
        self, app, seed_user, seed_periods, db,
    ):
        """Typed $510.00 against the bank's $500.00: off by +$10.00 on Feb 2.

        Positive is the books standing higher,
        :attr:`~app.services.bank_agreement.AgreementDay.gap`'s own
        convention.  *The app before plan step balance:X-f3c-2b-2c showed the
        same +10.00 here, beside a +10.00 disagreement on Feb 2's line that
        the opening already holds.*
        """
        with app.app_context():
            account = self._first_import(seed_user, db, "510.00")
            db.session.commit()

            agreement = bank_agreement.bank_agreement(
                account, BalanceContext.build(seed_user["user"].id),
            )

            assert agreement.headline.day == date(2026, 2, 2)
            # 510.00 - 500.00.
            assert agreement.headline.gap == Decimal("10.00")
            assert agreement.standing_gap == Decimal("10.00")
            assert agreement.compared == []

    def test_the_opening_day_counts_toward_a_CONSTANT_offset(
        self, app, seed_user, seed_periods, db,
    ):
        """The typo, then a later statement: +$10.00 on Feb 2 AND Feb 3.

        The later statement's one line is Feb 3's ``-40.00``, entered, and it
        states ``$460.00``.  Books: ``510.00`` on Feb 2, ``470.00`` on Feb 3;
        bank: ``500.00`` and ``460.00`` -- apart by ``+10.00`` both days, the
        signature of a wrong starting figure.  The days before Feb 2 are not
        among the days the offset is taken over: the books state no balance
        there.  *The app before plan step balance:X-f3c-2b-2c reported the
        same +10.00 offset here, over the same two days, because the
        origination assertion made Feb 2 its first cash fact; what changed is
        that books restated back before their first cash fact now count their
        opening day too.*
        """
        with app.app_context():
            account = self._first_import(seed_user, db, "510.00")
            _settle(
                seed_user, db, seed_periods[2], "40.00", date(2026, 2, 3),
                account=account,
            )
            _seed_import(
                db, account, stated="460.00", effective_on=date(2026, 2, 3),
                evidence=_FILE_CHAIN, lines=[(date(2026, 2, 3), "-40.00")],
                file_name="later.csv",
            )
            db.session.commit()

            agreement = bank_agreement.bank_agreement(
                account, BalanceContext.build(seed_user["user"].id),
            )

            assert _day(agreement, date(2026, 2, 2)).gap == Decimal("10.00")
            assert _day(agreement, date(2026, 2, 3)).gap == Decimal("10.00")
            assert agreement.constant_offset == Decimal("10.00")
            # Feb 3's row matches its line, so the movement half is clean.
            assert [day.day for day in agreement.compared] == [
                date(2026, 2, 3),
            ]
            assert agreement.disagreeing == []

    def test_a_books_day_the_bank_cannot_PRICE_withholds_the_constant(
        self, app, seed_user, seed_periods, db,
    ):
        """The constant fixture plus a third import that states no balance.

        Feb 2 and Feb 3 are apart by ``+10.00`` exactly as above.  A third
        statement's one line is Feb 6's ``-5.00``, entered, and it places no
        balance, so no anchor reaches Feb 4 - Feb 6: the books state a
        balance on each of those days and the bank prices none.  The offset
        was not seen to hold there, so it is not called constant -- reading
        only the priced days would report ``+10.00`` over a span it never
        checked.
        """
        with app.app_context():
            account = self._first_import(seed_user, db, "510.00")
            for amount, day in (("40.00", date(2026, 2, 3)),
                                ("5.00", date(2026, 2, 6))):
                _settle(
                    seed_user, db, seed_periods[2], amount, day,
                    account=account,
                )
            _seed_import(
                db, account, stated="460.00", effective_on=date(2026, 2, 3),
                evidence=_FILE_CHAIN, lines=[(date(2026, 2, 3), "-40.00")],
                file_name="later.csv",
            )
            _seed_import(
                db, account, stated=None,
                lines=[(date(2026, 2, 6), "-5.00")], file_name="unpriced.csv",
            )
            db.session.commit()

            agreement = bank_agreement.bank_agreement(
                account, BalanceContext.build(seed_user["user"].id),
            )

            assert _day(agreement, date(2026, 2, 2)).gap == Decimal("10.00")
            assert _day(agreement, date(2026, 2, 3)).gap == Decimal("10.00")
            for day in (date(2026, 2, 4), date(2026, 2, 5), date(2026, 2, 6)):
                assert _day(agreement, day).app_balance is not None
                assert _day(agreement, day).bank_balance is None
            assert agreement.constant_offset is None


class TestTheOpeningDifferencePlusEveryDaysMovementIsTheClosingDifference:
    """The identity that says the two halves of the boundary are one boundary.

    Ruling **R-BAL286** asked for it by name.  Inside one priced run, each
    day's change in the gap is that day's residue plus its correction -- the
    books moved ``recorded + asserted``, the bank moved ``bank_lines``, and
    ``residue = recorded - bank_lines`` -- so the gap on the LAST day equals
    the gap on the OPENING day plus the sum over the days after it.  It holds
    exactly when the level is read FROM the opening day and the movement from
    the day AFTER: a level starting a day late has no opening gap to start
    from, and a movement starting on the opening day adds that day's lines
    twice, once inside the opening and once as a residue.
    """

    def test_the_gaps_telescope_from_the_opening_day(
        self, app, seed_user, seed_periods, db,
    ):
        """Books open Feb 2 at a typed $510.00; the bank says $500.00.

        Bank lines: Feb 2 ``-10.00`` (inside the opening), Feb 3 ``-40.00``
        (entered), Feb 4 ``-25.00`` (NOT entered), Feb 5 ``-5.00`` (entered);
        it states ``$430.00`` on Feb 5, so it walks back to 435.00, 460.00 and
        500.00.  The owner declares ``$440.00`` on Feb 4, a correction of
        ``440.00 - 470.00 = -30.00``.  Books: 510.00, 470.00, 440.00, 435.00.

        * Gaps: Feb 2 ``+10.00``, Feb 3 ``+10.00``, Feb 4 ``+5.00``, Feb 5
          ``+5.00``.
        * Feb 3: residue ``0.00``.  Feb 4: residue ``0.00 - (-25.00) =
          +25.00``, correction ``-30.00``.  Feb 5: residue ``0.00``.
        * ``+10.00 + (0.00) + (25.00 - 30.00) + (0.00) = +5.00``, Feb 5's gap.
        """
        with app.app_context():
            account = _opened_on(seed_user, date(2026, 2, 2), "510.00")
            _seed_import(
                db, account, stated="430.00", effective_on=date(2026, 2, 5),
                evidence=_FILE_CHAIN,
                lines=[(date(2026, 2, 2), "-10.00"),
                       (date(2026, 2, 3), "-40.00"),
                       (date(2026, 2, 4), "-25.00"),
                       (date(2026, 2, 5), "-5.00")],
            )
            for day, amount in ((date(2026, 2, 3), "40.00"),
                                (date(2026, 2, 5), "5.00")):
                _settle(
                    seed_user, db, seed_periods[2], amount, day,
                    account=account,
                )
            _assert_balance(
                seed_user, db, "440.00", date(2026, 2, 4), account=account,
            )
            db.session.commit()

            agreement = bank_agreement.bank_agreement(
                account, BalanceContext.build(seed_user["user"].id),
            )
            opening = _day(agreement, agreement.books_opened_on)

            # One run, priced on every drawn day, so the walk is continuous.
            assert len(agreement.runs) == 1
            assert all(day.bank_balance is not None for day in agreement.days)
            assert opening.gap == Decimal("10.00")
            assert _day(agreement, date(2026, 2, 4)).asserted == Decimal(
                "-30.00",
            )
            assert agreement.days[-1].gap == Decimal("5.00")
            # The level is read ON the opening day and the movement over the
            # days the report COMPARES, each from the producer itself -- so a
            # level starting a day late has no opening gap, and a movement
            # starting on the opening day adds its +10.00 residue.
            assert [day.day for day in agreement.compared] == [
                date(2026, 2, 3), date(2026, 2, 4), date(2026, 2, 5),
            ]
            assert agreement.days[-1].gap == opening.gap + sum(
                (day.residue + day.asserted for day in agreement.compared),
                _ZERO,
            )


class TestADayNoImportCoversIsNotCompared:
    """Finding **BAL-650**, ruling **R-BAL291**, plan step balance:X-f3c-2b-2c.

    The bank covers a day's MOVEMENT only inside the window an import
    declares.  A drawn day between two imports used to read ``0.00`` for the
    bank and be compared against it, so a row the owner entered there counted
    as money the bank's total "ran ahead" on, which the bank never said.  It
    is shown now, with the books' movement and no bank movement, and counted
    in no total.
    """

    def test_a_row_between_two_imports_is_shown_and_never_counted(
        self, app, seed_user, seed_periods, db,
    ):
        """The ruling's own example: 5 compared days, 1 disagreeing.

        Books open Jan 1 (``create_account_of_type`` restates them back).
        Statement A: Dec 30 ``-20.00``, Jan 1 ``-25.00``, Jan 2 ``-40.00``
        (never entered), Jan 5 ``-10.00`` (entered).  Statement B: Jan 20
        ``-15.00`` (entered).  A ``30.00`` purchase is entered on Jan 10, and
        nothing is imported for Jan 6-19.  Compared: Jan 2-5 (inside A, after
        the opening) and Jan 20 (B), so 5; Jan 2 disagrees by ``0.00 -
        (-40.00) = +40.00``, your total running ahead of your bank's.  Jan 10
        is not compared, so the bank's total ran ahead by nothing.  *On the
        app before plan step balance:X-f3c-2b-2c: 16 compared (Jan 5-20, from
        the first entry on, the gap included), Jan 10 disagreeing by -30.00,
        the bank ahead by $30.00, and Jan 2 not compared at all.*
        """
        with app.app_context():
            late = create_account_of_type(
                seed_user, db.session, "Checking", "Late Checking",
                anchor_balance=Decimal("500.00"),
            )
            _seed_import(
                db, late,
                lines=[(date(2025, 12, 30), "-20.00"),
                       (date(2026, 1, 1), "-25.00"),
                       (date(2026, 1, 2), "-40.00"),
                       (date(2026, 1, 5), "-10.00")],
                file_name="a.csv",
            )
            _seed_import(
                db, late, lines=[(date(2026, 1, 20), "-15.00")],
                file_name="b.csv",
            )
            for period, amount, day in (
                (seed_periods[0], "10.00", date(2026, 1, 5)),
                (seed_periods[0], "30.00", date(2026, 1, 10)),
                (seed_periods[1], "15.00", date(2026, 1, 20)),
            ):
                _settle(seed_user, db, period, amount, day, account=late)
            db.session.commit()

            agreement = bank_agreement.bank_agreement(
                late, BalanceContext.build(seed_user["user"].id),
            )
            jan_10 = _day(agreement, date(2026, 1, 10))

            assert agreement.books_opened_on == date(2026, 1, 1)
            # Shown with the books' movement; the bank's is unknown, not 0.00.
            assert jan_10.recorded == Decimal("-30.00")
            assert jan_10.bank_lines is None
            assert jan_10.residue is None
            assert jan_10.compared is False
            # A quiet day inside statement A IS the bank saying nothing moved.
            assert _day(agreement, date(2026, 1, 3)).bank_lines == _ZERO
            assert [day.day for day in agreement.compared] == [
                date(2026, 1, 2), date(2026, 1, 3), date(2026, 1, 4),
                date(2026, 1, 5), date(2026, 1, 20),
            ]
            assert [day.day for day in agreement.disagreeing] == [
                date(2026, 1, 2),
            ]
            assert agreement.app_ahead == Decimal("40.00")
            assert agreement.bank_ahead == _ZERO


class TestWhichSideRestsOnAnAssumption:
    """Ruling R-GF, finding N-342."""

    def test_no_anchor_means_no_bank_balance_and_no_constant_offset(
        self, app, seed_user, seed_periods, db,
    ):
        """The state BOTH of the developer's real imports are in.

        The movement half still answers -- it needs no anchor -- which is why
        the report is worth rendering at all on an account whose files state no
        balance.
        """
        with app.app_context():
            _seed_import(
                db, seed_user["account"], stated=None,
                lines=[(date(2026, 3, 2), "-60.00")],
            )
            db.session.commit()

            agreement = _agreement(seed_user)
            row = _day(agreement, date(2026, 3, 2))

            assert not agreement.anchored
            assert [run.anchor for run in agreement.runs] == [None]
            assert row.bank_balance is None
            assert row.gap is None
            assert agreement.constant_offset is None
            assert row.residue == Decimal("60.00")

    def test_a_CONSTANT_offset_is_reported_when_every_movement_agrees(
        self, app, seed_user, seed_periods, db,
    ):
        """A level wrong by K shifts every day equally; movements stay right.

        **Two days that both move, not one.**  Its first form seeded a single
        day, where "every gap is equal" is satisfied vacuously and the
        assertion could not distinguish a constant offset from there being
        nothing to be constant across -- and the population that hits that is
        exactly N-342's, an owner importing a statement the week they start
        recording.  Measured by adversarial review 2026-08-24.
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
            db.session.commit()

            agreement = _agreement(seed_user)

            assert len(agreement.compared) >= 2
            assert agreement.disagreeing == []
            assert agreement.constant_offset is not None
            assert agreement.constant_offset != _ZERO
            (run,) = agreement.runs
            assert run.anchor.evidence is _UNCORROBORATED

    def test_ONE_compared_day_cannot_demonstrate_a_constant_offset(
        self, app, seed_user, seed_periods, db,
    ):
        """A FIRING control over the vacuous case.

        One day satisfies "every gap is equal" whatever the gap is, so
        reporting it as the signature of a wrong starting figure is an
        inference from a single observation -- rendered on screen as *"Every
        compared day is out by exactly $X"*.
        """
        with app.app_context():
            walk = cash_ledger.walk_cash_ledger(
                seed_user["account"].id,
                get_baseline_scenario(seed_user["user"].id).id,
            )
            only = walk.anchor_facts[0].observed_on + timedelta(days=1)
            _seed_import(
                db, seed_user["account"], stated="500.00",
                effective_on=only, evidence=_UNCORROBORATED,
                lines=[(only, "-40.00")],
            )
            _settled(
                db, seed_user, seed_periods[0], "Groceries", "40.00", only,
            )
            db.session.commit()

            agreement = _agreement(seed_user)

            assert len(agreement.compared) == 1
            assert agreement.constant_offset is None


class TestThePageCannotClaimAWalkItDidNotPerform:
    """A derivation named over a column of dashes."""

    def test_days_in_the_GAP_between_two_anchored_runs_are_counted(
        self, app, seed_user, seed_periods, db,
    ):
        """Reproduced by adversarial review 2026-08-24; half of it since fixed.

        A ``file_chain`` anchor in an old run was the STRONGEST, so it was
        the one walked from for the whole account -- and it reached nothing
        in a later, disconnected run, including that run's own anchored day
        whose crossing is empty (finding **N-343**).  Since plan step
        ``balance:X-bj-1b`` (ruling **R-BAL63**) each run walks from its own
        anchor, so the March day is PRICED -- the developer flipped this
        assertion with that ruling -- and what remains unpriced is the gap
        between the runs, which the report must still count rather than name
        a figure over dashes.  The day BEFORE the March run's first line is
        reached from the March anchor, so the gap counts 02-01..03-13.
        """
        with app.app_context():
            _seed_import(
                db, seed_user["account"], stated="1000.00",
                effective_on=date(2026, 1, 31), evidence=_FILE_CHAIN,
                lines=[(date(2026, 1, 31), "10.00")],
                period=(date(2026, 1, 1), date(2026, 1, 31)),
            )
            _seed_import(
                db, seed_user["account"], stated="500.00",
                effective_on=date(2026, 3, 20), evidence=_UNCORROBORATED,
                file_name="march.csv",
                lines=[(date(2026, 3, 20), "-5.00")],
                period=(date(2026, 3, 15), date(2026, 3, 20)),
            )
            db.session.commit()

            agreement = _agreement(seed_user)

            assert agreement.anchored
            assert [run.anchor.day for run in agreement.runs] == [
                date(2026, 1, 31), date(2026, 3, 20),
            ]
            # 02-01..03-13 inclusive: 28 February days and 13 March days.
            assert agreement.unpriced_days == 41
            assert _day(agreement, date(2026, 3, 13)).bank_balance is None
            # The day before the March run's first line is the balance its
            # lines start from, walked back from the March anchor.
            assert _day(agreement, date(2026, 3, 14)).bank_balance == Decimal(
                "505.00",
            )
            # The later run's own anchored day, priced from its own anchor.
            assert _day(agreement, date(2026, 3, 20)).bank_balance == Decimal(
                "500.00",
            )

    def test_an_account_with_NO_anchor_reports_zero_unpriced_days(
        self, app, seed_user, seed_periods, db,
    ):
        """One absence must not be reported as two.

        With no anchor the page already says no statement places a balance;
        adding "232 days not priced" beside it would name the same fact twice
        and read as a second, separate problem.
        """
        with app.app_context():
            _seed_import(
                db, seed_user["account"], stated=None,
                lines=[(date(2026, 3, 2), "-60.00")],
            )
            db.session.commit()

            agreement = _agreement(seed_user)

            assert not agreement.anchored
            assert agreement.unpriced_days == 0


class TestTheReportCarriesTheRuns:
    """Plan step ``balance:X-bj-1b``: the runs are the ONE home of coverage.

    Ruling **R-BAL65**: ``BankAgreement.runs`` carries every recorded run
    with its anchor and checkpoints, and the per-day bank balances are each
    run's own walk.  The ``imports`` list derived from the runs was deleted at
    plan step balance:X-f3c-2b-2c, which removed its last reader in ``app/``,
    so the spans are read off the runs themselves.
    """

    def test_runs_carry_each_anchor_and_each_span(
        self, app, seed_user, seed_periods, db,
    ):
        """Two anchored runs: two anchors, two spans, and one source of both.

        The hero (``headline``) walks back to the latest day both records
        state a balance for, which is now the later run's own anchored day
        rather than the older run's, and the standing gap on the span's last
        day is a figure.
        """
        with app.app_context():
            _seed_import(
                db, seed_user["account"], stated="1000.00",
                effective_on=date(2026, 1, 31), evidence=_FILE_CHAIN,
                lines=[(date(2026, 1, 31), "10.00")],
                period=(date(2026, 1, 1), date(2026, 1, 31)),
                file_name="january.csv",
            )
            _seed_import(
                db, seed_user["account"], stated="500.00",
                effective_on=date(2026, 3, 20), evidence=_UNCORROBORATED,
                file_name="march.csv",
                lines=[(date(2026, 3, 15), "50.00"),
                       (date(2026, 3, 20), "-5.00")],
            )
            db.session.commit()

            agreement = _agreement(seed_user)

            assert [
                (run.first_day, run.last_day) for run in agreement.runs
            ] == [
                (date(2026, 1, 1), date(2026, 1, 31)),
                (date(2026, 3, 15), date(2026, 3, 20)),
            ]
            assert [run.anchor.file_name for run in agreement.runs] == [
                "january.csv", "march.csv",
            ]
            assert agreement.headline.day == date(2026, 3, 20)
            assert agreement.headline.bank_balance == Decimal("500.00")
            # The level on the span's last day: the books less the bank's
            # 500.00 there, a figure now rather than the ``None`` the
            # account-wide walk left on every day it could not reach.
            assert agreement.standing_gap == (
                _day(agreement, date(2026, 3, 20)).app_balance
                - Decimal("500.00")
            )

    def test_a_checkpoint_rides_on_its_run_and_moves_no_day(
        self, app, seed_user, seed_periods, db,
    ):
        """The disagreeing statement is listed; the column is the anchor's walk."""
        with app.app_context():
            _seed_import(
                db, seed_user["account"], stated="1000.00",
                effective_on=date(2026, 3, 5), evidence=_FILE_CHAIN,
                file_name="ytd.csv",
                lines=[(date(2026, 3, 1), "100.00"),
                       (date(2026, 3, 3), "-40.00"),
                       (date(2026, 3, 5), "25.00")],
            )
            _seed_import(
                db, seed_user["account"], stated="1075.00",
                effective_on=date(2026, 3, 3), evidence=_UNCORROBORATED,
                file_name="guessed.csv", lines=[],
                period=(date(2026, 3, 1), date(2026, 3, 3)),
            )
            db.session.commit()

            agreement = _agreement(seed_user)

            (run,) = agreement.runs
            (checkpoint,) = run.checkpoints
            assert checkpoint.file_name == "guessed.csv"
            assert checkpoint.difference == Decimal("-100.00")
            assert _day(agreement, date(2026, 3, 3)).bank_balance == Decimal(
                "975.00",
            )

    def test_a_run_with_no_level_counts_its_days_unpriced_beside_an_anchored_one(
        self, app, seed_user, seed_periods, db,
    ):
        """Ruling **R-BAL64** on the report: the count names both causes.

        February (02-20) is anchored; March 1..5 holds lines and no figure.
        Unpriced: the gap 02-21..02-28 (8 days) and March's 5 days, 13 --
        March's day-before-first-line among the gap's, since no anchor
        reaches it either.  (The service suite's today is 2026-03-20.)
        """
        with app.app_context():
            _seed_import(
                db, seed_user["account"], stated="500.00",
                effective_on=date(2026, 2, 20), evidence=_UNCORROBORATED,
                file_name="february.csv",
                lines=[(date(2026, 2, 20), "-5.00")],
            )
            _seed_import(
                db, seed_user["account"], stated=None, file_name="march.csv",
                lines=[(date(2026, 3, 1), "50.00"),
                       (date(2026, 3, 5), "-20.00")],
            )
            db.session.commit()

            agreement = _agreement(seed_user)

            february, march = agreement.runs
            assert february.anchor is not None
            assert march.anchor is None
            assert agreement.anchored
            assert agreement.unpriced_days == 13
            assert _day(agreement, date(2026, 2, 28)).bank_balance is None
            assert _day(agreement, date(2026, 3, 5)).bank_balance is None


class TestTheDrillDownNamesWhatMakesUpADay:
    """A number a reader can act on, rather than one they must go and chase."""

    def test_it_lists_both_sides_with_their_match_state(
        self, app, seed_user, seed_periods, db,
    ):
        """The 2026-06-02 shape again: six app rows, no bank line.

        Every row and every line is listed rather than only the unmatched
        ones -- a row matched to a line the bank posted on a DIFFERENT day
        contributes to both days' residues while being unmatched on neither.
        """
        with app.app_context():
            _seed_import(
                db, seed_user["account"], stated="1000.00",
                effective_on=date(2026, 3, 3), evidence=_FILE_CHAIN,
                lines=[(date(2026, 3, 3), "-40.00")],
            )
            _settled(
                db, seed_user, seed_periods[4], "Card payback", "100.00",
                date(2026, 3, 3),
            )
            db.session.commit()

            detail = bank_agreement.day_detail(
                seed_user["account"],
                BalanceContext.build(seed_user["user"].id),
                date(2026, 3, 3),
            )

            assert [line.amount for line in detail.lines] == [
                Decimal("-40.00"),
            ]
            assert detail.lines[0].matched is False
            assert [(row.description, row.amount) for row in detail.rows] == [
                ("Card payback", Decimal("-100.00")),
            ]
            assert detail.rows[0].matched is False

    def test_each_item_names_the_record_it_is(
        self, app, seed_user, seed_periods, db,
    ):
        """A line carries its statement line's id and a row its movement's.

        Plan step ``balance:X-bk-2`` (plan#1192): the reconcile's act sheet
        cancels or matches exactly the movement and the line this lists, so
        each item names its own record -- two lines of one day and figure, or
        two rows of one name, could not otherwise be told apart.  The second
        line and the second row share the first's day and figure, so an id
        read off the wrong record of a pair cannot pass.
        """
        with app.app_context():
            _seed_import(
                db, seed_user["account"], stated="1000.00",
                effective_on=date(2026, 3, 3), evidence=_FILE_CHAIN,
                lines=[(date(2026, 3, 3), "-40.00"),
                       (date(2026, 3, 3), "-40.00")],
            )
            first = _settled(
                db, seed_user, seed_periods[4], "Gas", "40.00",
                date(2026, 3, 3),
            )
            second = _settled(
                db, seed_user, seed_periods[4], "Gas", "40.00",
                date(2026, 3, 3),
            )
            db.session.commit()
            line_ids = [
                line_id for (line_id,) in db.session.query(
                    BankStatementLine.id,
                ).filter(
                    BankStatementLine.account_id == seed_user["account"].id,
                ).order_by(BankStatementLine.id)
            ]
            entry_ids = {
                movement.id
                for txn in (first, second)
                for movement in txn.covering_movements
            }

            detail = bank_agreement.day_detail(
                seed_user["account"],
                BalanceContext.build(seed_user["user"].id),
                date(2026, 3, 3),
            )

            # The lines are listed in recording order (``_lines_on``), so the
            # ids read back in the order the statement recorded them.
            assert [line.line_id for line in detail.lines] == line_ids
            assert len(line_ids) == 2
            # The rows are ordered by size and the two tie, so the ids are
            # compared as a set: each row names one movement, and together
            # they name both.
            assert {row.entry_id for row in detail.rows} == entry_ids
            assert len(entry_ids) == 2

    def test_the_rows_it_lists_SUM_to_the_days_recorded_total(
        self, app, seed_user, seed_periods, db,
    ):
        """The items shown explain exactly the figure they sit under.

        Listing the account's transactions by ``settled_on`` instead of reading
        the walk would be a second statement of which rows count as settled
        cash and on what day, and a drill-down that did not add up is how a
        reader learns to stop trusting the number above it.
        """
        with app.app_context():
            _seed_import(
                db, seed_user["account"], stated="1000.00",
                effective_on=date(2026, 3, 3), evidence=_FILE_CHAIN,
                lines=[(date(2026, 3, 3), "-40.00")],
            )
            for name, amount in (("A", "10.00"), ("B", "25.50"),
                                 ("C", "4.50")):
                _settled(
                    db, seed_user, seed_periods[4], name, amount,
                    date(2026, 3, 3),
                )
            # Rows on OTHER days, so the day filter is exercised.  Without
            # them every settled row in the account sat on the one day asked
            # about, and deleting ``fact.settled_on == day`` from ``_rows_on``
            # left the suite green -- the drill-down would list the whole
            # account under every day.  Found by adversarial review
            # 2026-08-24: setup that does not create the condition it names.
            _settled(
                db, seed_user, seed_periods[4], "Elsewhere", "99.00",
                date(2026, 3, 2),
            )
            _settled(
                db, seed_user, seed_periods[4], "Later", "77.00",
                date(2026, 3, 5),
            )
            db.session.commit()

            ctx = BalanceContext.build(seed_user["user"].id)
            detail = bank_agreement.day_detail(
                seed_user["account"], ctx, date(2026, 3, 3),
            )
            row = _day(_agreement(seed_user), date(2026, 3, 3))

            assert sum(item.amount for item in detail.rows) == row.recorded
            assert sum(item.amount for item in detail.lines) == row.bank_lines
            assert {item.description for item in detail.rows} == {
                "A", "B", "C",
            }

    def test_a_day_with_nothing_on_it_answers_with_empty_lists(
        self, app, seed_user, seed_periods, db,
    ):
        """"Neither of us has anything" is an answer a reader asked for."""
        with app.app_context():
            _seed_import(
                db, seed_user["account"], stated="1000.00",
                effective_on=date(2026, 3, 3), evidence=_FILE_CHAIN,
                lines=[(date(2026, 3, 3), "-40.00")],
            )
            db.session.commit()

            detail = bank_agreement.day_detail(
                seed_user["account"],
                BalanceContext.build(seed_user["user"].id),
                date(2026, 3, 9),
            )

            assert detail.lines == []
            assert detail.rows == []


class TestTheDrillDownSaysWhatIsALREADYEXPLAINED:
    """The match flag, and the envelope-purchase branch beside it."""

    def test_a_MATCHED_line_and_a_MATCHED_row_are_marked(
        self, app, seed_user, seed_periods, db,
    ):
        """Both flags, against a real accepted match.

        A FIRING control: with the flag hardcoded ``False`` the whole
        ``matched_subjects`` claim read, both template branches and their copy
        are dead, and the screen tells the owner nothing is explained when
        everything is.  The axis no other test in this file varies.

        The app-side member names the row's PAYMENT, its covering movement
        (rulings **R-CC43**, **R-CC45**): since plan step
        ``credit_card:CC-5-4a-2`` the member table has no row column, and
        this case named the row through it until then.
        Developer confirmation 2026-09-22 (rule 5): "Confirm all four groups -- all four are
        rule-5 re-expressions under R-CC45."
        """
        with app.app_context():
            _seed_import(
                db, seed_user["account"], stated="1000.00",
                effective_on=date(2026, 3, 3), evidence=_FILE_CHAIN,
                lines=[(date(2026, 3, 3), "-40.00")],
            )
            txn = _settled(
                db, seed_user, seed_periods[4], "Groceries", "40.00",
                date(2026, 3, 3),
            )
            line = db.session.query(BankStatementLine).filter(
                BankStatementLine.account_id == seed_user["account"].id,
            ).one()
            match = StatementMatch(
                account_id=seed_user["account"].id,
                user_id=seed_user["user"].id,
                applied_by_rule=False,
            )
            db.session.add(match)
            db.session.flush()
            db.session.add(StatementMatchMember(
                match_id=match.id, account_id=seed_user["account"].id,
                bank_statement_line_id=line.id,
            ))
            (payment,) = txn.covering_movements
            db.session.add(StatementMatchMember(
                match_id=match.id, account_id=seed_user["account"].id,
                transaction_entry_id=payment.id,
            ))
            db.session.commit()

            detail = bank_agreement.day_detail(
                seed_user["account"],
                BalanceContext.build(seed_user["user"].id),
                date(2026, 3, 3),
            )

            assert [line.matched for line in detail.lines] == [True]
            assert [row.matched for row in detail.rows] == [True]

    @pytest.mark.parametrize(
        ("is_income", "amount", "line_amount"),
        [(False, "40.00", "-40.00"), (True, "2572.78", "2572.78")],
        ids=["a covered bill", "a covered paycheck"],
    )
    def test_a_COVERED_row_matched_by_its_PAYMENT_reads_matched(
        self, app, seed_user, seed_periods, db, is_income, amount, line_amount,
    ):
        """The mirror's match state is its own: the act names the payment.

        Settled through the seam, a bill's (X-bi-3a) or a paycheck's
        (X-bi-3b) money walks as its covering movement's fact, and the match
        names that movement (rulings **R-CC43**, **R-CC45**), in either
        direction.  Until plan step ``credit_card:CC-5-4a-2`` this case named
        the ROW -- the shape the acts before ``CC-5-4a-1`` held, under which
        asking the entry's own claim alone read every matched bill and
        paycheck as unexplained here (adversarial review of X-bi-3b,
        2026-09-16); that migration re-keyed each such member onto the
        payment and dropped the row column, so the case now grades the one
        shape there is.
        Developer confirmation 2026-09-22 (rule 5): "Confirm all four groups -- all four are
        rule-5 re-expressions under R-CC45."
        """
        with app.app_context():
            _seed_import(
                db, seed_user["account"], stated="1000.00",
                effective_on=date(2026, 3, 3), evidence=_FILE_CHAIN,
                lines=[(date(2026, 3, 3), line_amount)],
            )
            make = make_income_template if is_income else make_expense_template
            txn = generate_row_of(
                make(db.session, seed_user, amount=amount, name="Row"),
                seed_periods[4],
            )
            transaction_service.settle_transaction(
                txn, settle_day=an_entered_day(date(2026, 3, 3)),
            )
            db.session.flush()
            assert len(txn.covering_movements) == 1
            line = db.session.query(BankStatementLine).filter(
                BankStatementLine.account_id == seed_user["account"].id,
            ).one()
            match = StatementMatch(
                account_id=seed_user["account"].id,
                user_id=seed_user["user"].id,
                applied_by_rule=False,
            )
            db.session.add(match)
            db.session.flush()
            db.session.add(StatementMatchMember(
                match_id=match.id, account_id=seed_user["account"].id,
                bank_statement_line_id=line.id,
            ))
            db.session.add(StatementMatchMember(
                match_id=match.id, account_id=seed_user["account"].id,
                transaction_entry_id=txn.covering_movements[0].id,
            ))
            db.session.commit()

            detail = bank_agreement.day_detail(
                seed_user["account"],
                BalanceContext.build(seed_user["user"].id),
                date(2026, 3, 3),
            )

            assert [row.amount for row in detail.rows] == [Decimal(line_amount)]
            assert [row.matched for row in detail.rows] == [True]

    def test_an_ENVELOPE_PURCHASE_is_named_from_its_ENTRY(
        self, app, seed_user, seed_periods, db,
    ):
        """The two-subject split, which no other test reaches.

        A purchase is a cash fact in its own right, carrying an ``entry_id``,
        so its name comes from ``transaction_entries.description`` and its
        match state from the ENTRY rather than the parent.  With the entry
        branch dead the row renders as ``(unnamed)`` -- and that branch is
        precisely what ``_rows_on``'s docstring names as the reason the lookup
        is keyed on the pair.
        """
        with app.app_context():
            _seed_import(
                db, seed_user["account"], stated="1000.00",
                effective_on=date(2026, 3, 3), evidence=_FILE_CHAIN,
                lines=[(date(2026, 3, 3), "-40.00")],
            )
            envelope = _settled(
                db, seed_user, seed_periods[4], "Groceries", "100.00",
                date(2026, 3, 3),
            )
            add_entry(
                db.session, seed_user, envelope, Decimal("31.00"),
                date(2026, 3, 3), settled_on=date(2026, 3, 3),
                description="Food Lion",
            )
            db.session.commit()

            detail = bank_agreement.day_detail(
                seed_user["account"],
                BalanceContext.build(seed_user["user"].id),
                date(2026, 3, 3),
            )

            named = {row.description for row in detail.rows}
            assert "Food Lion" in named
            assert "(unnamed)" not in named

    def test_the_biggest_movement_reads_FIRST(
        self, app, seed_user, seed_periods, db,
    ):
        """The item explaining most of a day's difference leads.

        Unpinned, the ordering is whatever the walk happened to produce, and
        the drill-down's whole job is putting the explanation in front of the
        reader.
        """
        with app.app_context():
            _seed_import(
                db, seed_user["account"], stated="1000.00",
                effective_on=date(2026, 3, 3), evidence=_FILE_CHAIN,
                lines=[(date(2026, 3, 3), "-40.00")],
            )
            for name, amount in (("Small", "5.00"), ("Biggest", "900.00"),
                                 ("Middle", "50.00")):
                _settled(
                    db, seed_user, seed_periods[4], name, amount,
                    date(2026, 3, 3),
                )
            db.session.commit()

            detail = bank_agreement.day_detail(
                seed_user["account"],
                BalanceContext.build(seed_user["user"].id),
                date(2026, 3, 3),
            )

            assert [row.description for row in detail.rows] == [
                "Biggest", "Middle", "Small",
            ]
