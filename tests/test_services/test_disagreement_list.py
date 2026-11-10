"""Where an account's bank record and its books part -- plan step balance:X-bk-1.

``outstanding_difference.disagreement_list``: the books span the outstanding
difference grades as ONE verdict, cut into back-to-back stretches of one kind
each (agrees, disagrees, no statement yet, not compared), every one graded by
the same grader, with each disagreeing day's bank lines and app rows -- and the
imported days either side of the span set apart.  Ruling **R-BAL232**, the
developer's "Tested report + script": the list the one-time reconcile (plan
step X-bk-2) is written from and re-measured against.

**The first class is the ruling's worked example, built as a real CSV
statement would hold it**: a statement from Jan 1 to Mar 31, books from Feb 2
to Mar 31 (58 days), a ``$400.00`` card payment the bank shows on Feb 10 and
the app does not, three card paybacks totalling ``$395.00`` the app shows on
Feb 11 and the bank does not, and a ``$120.00`` electric bill the app dates
Mar 5 and the bank Mar 7.  The ruling's own words fix what it must answer:
Feb 10-11 one two-day disagreeing stretch, Mar 5 and Mar 7 two one-day
stretches, and the stretches adding up to all 58 days.  **Two lines the
ruling does not name are added, and why is the builder's first paragraph**:
the only adapter declares a statement's window as its own first..last line
day, so a CSV statement declaring Jan 1 - Mar 31 has a line on each end.  The same
example after the reconcile's acts is graded too: it reconciles.  Every
figure here is MADE UP (ruling **R-BAL132**).

**Every case asserts the PARTITION** (:func:`_assert_partition`): the
stretches begin on the span's first day, run back to back with no two
neighbours of one kind, end on its last day, and their verdicts add up to the
whole span's count by count -- and every stretch names exactly as many days
as its verdict counts as disagreeing.  That is what makes the list a
decomposition of the verdict the reconcile is done against rather than a
second opinion beside it.
"""

from datetime import date, timedelta
from decimal import Decimal

import pytest

from app.services import outstanding_difference as service
from app.services.balance_at import BalanceContext
from app.services.outstanding_difference import StretchKind
from tests._test_helpers import (
    account_never_asserted,
    create_account_of_type,
    freeze_today,
)
from tests.test_services.test_outstanding_difference import (
    _assert_balance,
    _opened_on,
    _settle,
)
from tests.test_services.test_statement_import.test_anchor import _seed_import

_AGREES = StretchKind.AGREES
_DISAGREES = StretchKind.DISAGREES
_NO_STATEMENT = StretchKind.NO_STATEMENT
_NOT_COMPARED = StretchKind.NOT_COMPARED

_ONE_DAY = timedelta(days=1)

#: The counts a stretch's verdict carries, every one of which must add up
#: across the stretches to the whole span's.
_COUNTS = ("day_count", "compared", "disagreeing", "imported")


def _listed(seed_user, account=None):
    """Return the disagreement list for an account of the seeded owner."""
    return service.disagreement_list(
        account or seed_user["account"],
        BalanceContext.build(seed_user["user"].id),
    )


def _shape(listing):
    """Return each stretch as ``(kind, first day, last day)``."""
    return [
        (stretch.kind, stretch.verdict.first_day, stretch.verdict.last_day)
        for stretch in listing.stretches
    ]


def _assert_partition(listing):
    """Assert the stretches cut the books span exactly, and add up to it."""
    books = listing.books
    stretches = listing.stretches
    if books.day_count == 0:
        assert not stretches
        return
    assert stretches[0].verdict.first_day == books.first_day
    assert stretches[-1].verdict.last_day == books.last_day
    for before, after in zip(stretches, stretches[1:]):
        assert after.verdict.first_day == before.verdict.last_day + _ONE_DAY
        assert after.kind is not before.kind
    for count in _COUNTS:
        assert sum(
            getattr(stretch.verdict, count) for stretch in stretches
        ) == getattr(books, count), count
    for stretch in stretches:
        assert len(stretch.days) == stretch.verdict.disagreeing


def _amounts(items):
    """Return each item's amount, in the order the day detail lists them."""
    return [item.amount for item in items]


class TestTheRulingsWorkedExample:
    """R-BAL232's own example, every answer the ruling names asserted."""

    @pytest.fixture(autouse=True)
    def _after_the_books_end(self, monkeypatch):
        """Freeze today to 2026-04-01, the day after the example's books end.

        The services suite freezes today to 2026-03-20, and two things stop
        there: the comparison draws no day past the reader's NOW, and a
        balance declared for Mar 31 would be a FUTURE day, which the
        assertion door refuses -- so the example's own dates would build a
        state production cannot.  The suite's own helper, as
        ``test_loan_interest_in_year`` re-freezes it.
        """
        freeze_today(monkeypatch, date(2026, 4, 1))

    def _build(self, seed_user, seed_periods, db, *, reconciled=False):
        """Books open 2026-02-01; a statement whose lines run Jan 1 - Mar 31.

        **The two edge lines are the ruling's "statement Jan 1 - Mar 31" as
        the only adapter writes one**: the SECU CSV declares its window as its
        own first..last line day, so ``-10.00`` on Jan 1 (before the books)
        and ``-5.00`` on Mar 31 (with its ``5.00`` row) are what make that
        window.  Without them Feb 2-9 and Mar 8-31 would lie in no window and
        read as no statement yet; and a source DECLARING Jan 1 - Mar 31
        without them would leave those days covered and never compared,
        because the comparison draws only from the first recorded line to
        the last -- finding **bank_import:BI-513**'s shape.  Either way, a
        different list.

        Between them: the bank's ``-400.00`` card payment on Feb 10 and its
        ``-120.00`` electric bill on Mar 7.  As BUILT, the app holds three
        paybacks of ``110.00``, ``135.00`` and ``150.00`` on Feb 11 and the
        electric bill on Mar 5.  **Reconciled**, it holds what the
        reconcile's acts would leave: the card payment on Feb 10 in place of
        the paybacks, and the bill on the day the bank paid it.  The balance
        declared on Mar 31 is what the books then produce.
        """
        account = _opened_on(seed_user, date(2026, 2, 1), balance="1000.00")
        _seed_import(
            db, account,
            lines=[(date(2026, 1, 1), "-10.00"),
                   (date(2026, 2, 10), "-400.00"),
                   (date(2026, 3, 7), "-120.00"),
                   (date(2026, 3, 31), "-5.00")],
        )
        if reconciled:
            rows = [(seed_periods[2], "400.00", date(2026, 2, 10)),
                    (seed_periods[4], "120.00", date(2026, 3, 7))]
        else:
            rows = [(seed_periods[2], amount, date(2026, 2, 11))
                    for amount in ("110.00", "135.00", "150.00")]
            rows.append((seed_periods[4], "120.00", date(2026, 3, 5)))
        rows.append((seed_periods[6], "5.00", date(2026, 3, 31)))
        for period, amount, day in rows:
            _settle(seed_user, period, amount, day, account=account)
        _assert_balance(
            seed_user, db, "475.00" if reconciled else "480.00",
            date(2026, 3, 31), account=account,
        )
        db.session.commit()
        return account

    def test_the_stretches_are_the_ones_the_ruling_names(
        self, app, seed_user, seed_periods, db,
    ):
        """Feb 10-11 one stretch; Mar 5 and Mar 7 two; 58 days in all."""
        with app.app_context():
            listing = _listed(
                seed_user, self._build(seed_user, seed_periods, db),
            )

            assert _shape(listing) == [
                (_AGREES, date(2026, 2, 2), date(2026, 2, 9)),
                (_DISAGREES, date(2026, 2, 10), date(2026, 2, 11)),
                (_AGREES, date(2026, 2, 12), date(2026, 3, 4)),
                (_DISAGREES, date(2026, 3, 5), date(2026, 3, 5)),
                (_AGREES, date(2026, 3, 6), date(2026, 3, 6)),
                (_DISAGREES, date(2026, 3, 7), date(2026, 3, 7)),
                (_AGREES, date(2026, 3, 8), date(2026, 3, 31)),
            ]
            # 8 + 2 + 21 + 1 + 1 + 1 + 24.
            assert [s.verdict.day_count for s in listing.stretches] == [
                8, 2, 21, 1, 1, 1, 24,
            ]
            assert listing.books.day_count == 58
            assert listing.books.compared == 58
            assert listing.books.imported == 58
            assert listing.books.disagreeing == 4
            assert listing.books.reconciles is False
            _assert_partition(listing)

    def test_each_disagreeing_day_names_what_differs(
        self, app, seed_user, seed_periods, db,
    ):
        """The lines and rows on each day, and the residue they leave.

        Positive residue is the app moving MORE into the account than the
        bank: Feb 10's card payment left the bank and the app never recorded
        it.  Rows list largest movement first, as the day detail orders them.
        """
        with app.app_context():
            listing = _listed(
                seed_user, self._build(seed_user, seed_periods, db),
            )
            listed = {
                day.comparison.day: day
                for stretch in listing.stretches
                for day in stretch.days
            }

            assert sorted(listed) == [
                date(2026, 2, 10), date(2026, 2, 11),
                date(2026, 3, 5), date(2026, 3, 7),
            ]
            feb_10 = listed[date(2026, 2, 10)]
            assert feb_10.comparison.residue == Decimal("400.00")
            assert _amounts(feb_10.detail.lines) == [Decimal("-400.00")]
            assert [line.matched for line in feb_10.detail.lines] == [False]
            assert not feb_10.detail.rows
            feb_11 = listed[date(2026, 2, 11)]
            assert feb_11.comparison.residue == Decimal("-395.00")
            assert not feb_11.detail.lines
            assert _amounts(feb_11.detail.rows) == [
                Decimal("-150.00"), Decimal("-135.00"), Decimal("-110.00"),
            ]
            assert listed[date(2026, 3, 5)].comparison.residue == Decimal(
                "-120.00",
            )
            assert listed[date(2026, 3, 7)].comparison.residue == Decimal(
                "120.00",
            )

    def test_only_a_DISAGREEING_stretch_carries_days_here(
        self, app, seed_user, seed_periods, db,
    ):
        """Every day of a disagreeing stretch is named; no agreeing day is."""
        with app.app_context():
            listing = _listed(
                seed_user, self._build(seed_user, seed_periods, db),
            )

            for stretch in listing.stretches:
                if stretch.kind is _DISAGREES:
                    assert len(stretch.days) == stretch.verdict.day_count
                else:
                    assert stretch.days == ()

    def test_after_the_reconcile_s_acts_the_books_span_RECONCILES(
        self, app, seed_user, seed_periods, db,
    ):
        """The completion test is reachable: one agreeing stretch, 58 days.

        The card payment recorded on Feb 10 and the bill on Mar 7: every day
        of the span covered, compared and agreeing.
        """
        with app.app_context():
            listing = _listed(
                seed_user,
                self._build(seed_user, seed_periods, db, reconciled=True),
            )

            assert _shape(listing) == [
                (_AGREES, date(2026, 2, 2), date(2026, 3, 31)),
            ]
            assert listing.books.day_count == 58
            assert listing.books.shortfall is None
            assert listing.books.reconciles is True
            _assert_partition(listing)

    def test_the_days_before_the_books_are_set_apart(
        self, app, seed_user, seed_periods, db,
    ):
        """Jan 1 - Feb 1: 32 imported days, outside the span and its counts.

        Feb 1 is the day the books OPEN -- its close is the opening level
        (ruling R-HG) -- so it belongs to the days set apart, not the span.
        """
        with app.app_context():
            listing = _listed(
                seed_user, self._build(seed_user, seed_periods, db),
            )

            before = listing.before_books
            assert (before.first_day, before.last_day) == (
                date(2026, 1, 1), date(2026, 2, 1),
            )
            assert before.day_count == 32
            assert before.imported == 32
            # Jan 1's line has no row, yet it falls before the books open, so
            # the day is counted as imported and never compared -- and never
            # as a disagreement.
            assert before.disagreeing == 0
            assert listing.after_books is None

    def test_the_whole_span_is_the_verdict_the_card_already_carries(
        self, app, seed_user, seed_periods, db,
    ):
        """One grader, one span: the list's verdict IS the card's."""
        with app.app_context():
            account = self._build(seed_user, seed_periods, db)
            ctx = BalanceContext.build(seed_user["user"].id)

            assert service.disagreement_list(account, ctx).books == (
                service.outstanding_difference(account, ctx).reconciliation
            )


class TestDaysNoStatementCovers:
    """A gap between two imports, and the tail after the last one."""

    def test_a_GAP_and_a_TAIL_are_no_statement_yet(
        self, app, seed_user, seed_periods, db,
    ):
        """Books open 2026-02-28; the latest balance is dated 2026-03-08.

        One import's lines run Mar 1-2 and the next's Mar 5-6, each with a
        matching row, so Mar 3-4 lie in no window.  **The app records
        ``7.00`` on Mar 3, inside the gap** -- the comparison draws that day,
        but only one record covers it: the bank's movement there is UNKNOWN,
        so the day is not compared and counts neither way (finding
        **BAL-650**, ruling **R-BAL291**).  The STRETCH is of the kind no
        statement yet -- importing one is the act it calls for -- and it names
        no day, because a disagreement needs two movements to differ; the
        ``7.00`` stays on the day-by-day comparison page.  Mar 7-8 come after
        every window: the shape of a balance declared after the last import.
        *Until plan step balance:X-f3c-2b-2c the gap's two days were compared
        against a bank movement of ``0.00`` nobody imported: the gap read 2
        compared / 1 disagreeing and named Mar 3's row, and the books span 6
        compared / 1 disagreeing.*
        """
        with app.app_context():
            account = _opened_on(seed_user, date(2026, 2, 28))
            _seed_import(
                db, account,
                lines=[(date(2026, 3, 1), "-50.00"),
                       (date(2026, 3, 2), "-20.00")],
                file_name="first.csv",
            )
            _seed_import(
                db, account,
                lines=[(date(2026, 3, 5), "-25.00"),
                       (date(2026, 3, 6), "-10.00")],
                file_name="second.csv",
            )
            for day, amount in (
                (date(2026, 3, 1), "50.00"), (date(2026, 3, 2), "20.00"),
                (date(2026, 3, 3), "7.00"),
                (date(2026, 3, 5), "25.00"), (date(2026, 3, 6), "10.00"),
            ):
                _settle(
                    seed_user, seed_periods[4], amount, day,
                    account=account,
                )
            _assert_balance(
                seed_user, db, "388.00", date(2026, 3, 8), account=account,
            )
            db.session.commit()

            listing = _listed(seed_user, account)

            assert _shape(listing) == [
                (_AGREES, date(2026, 3, 1), date(2026, 3, 2)),
                (_NO_STATEMENT, date(2026, 3, 3), date(2026, 3, 4)),
                (_AGREES, date(2026, 3, 5), date(2026, 3, 6)),
                (_NO_STATEMENT, date(2026, 3, 7), date(2026, 3, 8)),
            ]
            gap = listing.stretches[1]
            assert gap.verdict.imported == 0
            assert gap.verdict.compared == 0
            assert gap.verdict.disagreeing == 0
            assert gap.days == ()
            tail = listing.stretches[3]
            assert tail.verdict.imported == 0
            assert tail.verdict.compared == 0
            # 8 days; the 4 inside a window are the 4 compared, and all agree.
            assert listing.books.day_count == 8
            assert listing.books.imported == 4
            assert listing.books.compared == 4
            assert listing.books.disagreeing == 0
            assert listing.before_books is None
            assert listing.after_books is None
            _assert_partition(listing)


class TestEveryImportedDayTheBooksCoverIsCompared:
    """Finding **BAL-616**, plan step balance:X-f3c-2b-2c, ruling **R-BAL285**.

    The comparison takes every drawn day after the books open that an import
    covers (ruling **R-BAL291**), so the books span holds no day it skips for
    being "before the records" -- which is what lets a span reconcile where the
    books open before the first entry and nothing moved in between.  It
    started at the account's first cash fact until this step, and the first
    case below is the fixture that used to read as 76 days not compared.
    """

    def test_a_books_day_before_the_first_entry_DISAGREES_rather_than_going_unread(
        self, app, seed_user, seed_periods, db,
    ):
        """Books open 2026-01-01; nothing is recorded until Mar 19.

        ``create_account_of_type`` restates the books back to 2026-01-01
        while its origination assertion -- the first entry -- sits on
        2026-03-19.  An import covers Jan 2 - Mar 19 with a ``-40.00`` line on
        Jan 2 and a ``-15.00`` line on Mar 19, and the app records only the
        ``15.00`` on Mar 19.  The books claim Jan 2, so the ``-40.00`` nobody
        recorded is a real disagreement: residue ``0.00 - (-40.00) = +40.00``.
        Every other day agrees -- quiet on both sides, or Mar 19's matching
        pair -- so 77 days are compared and one disagrees.  *Before this step
        the same fixture read Jan 2 - Mar 18 as 76 days not compared and Mar
        19 as agreeing, with the ``$40.00`` named on no day of the list.*
        """
        with app.app_context():
            late = create_account_of_type(
                seed_user, db.session, "Checking", "Late Checking",
                anchor_balance=Decimal("500.00"),
            )
            _seed_import(
                db, late,
                period=(date(2026, 1, 2), date(2026, 3, 19)),
                lines=[(date(2026, 1, 2), "-40.00"),
                       (date(2026, 3, 19), "-15.00")],
            )
            _settle(
                seed_user, seed_periods[5], "15.00", date(2026, 3, 19),
                account=late,
            )
            db.session.commit()

            listing = _listed(seed_user, late)

            assert _shape(listing) == [
                (_DISAGREES, date(2026, 1, 2), date(2026, 1, 2)),
                (_AGREES, date(2026, 1, 3), date(2026, 3, 19)),
            ]
            (jan_2,) = listing.stretches[0].days
            assert jan_2.comparison.residue == Decimal("40.00")
            assert _amounts(jan_2.detail.lines) == [Decimal("-40.00")]
            assert not jan_2.detail.rows
            # 30 days of January from the 2nd, 28 of February, 19 of March.
            assert listing.books.day_count == 77
            assert listing.books.compared == 77
            assert listing.books.unchecked == 0
            assert listing.books.disagreeing == 1
            assert listing.books.reconciles is False
            assert listing.before_books is None
            _assert_partition(listing)

    def test_QUIET_books_days_before_the_first_entry_let_the_span_RECONCILE(
        self, app, seed_user, seed_periods, db,
    ):
        """A statement starting BEFORE the books, and nothing moving Jan 2-9.

        The developer's own shape -- his statement reaches back before his
        books open.  Books open 2026-01-01; the statement's lines run Dec 31
        (``-10.00``, inside the opening) to Mar 19, so its window covers the
        whole span; the app's first entry is the ``40.00`` row matching Jan
        10's line, and Mar 19's ``-15.00`` has its row too.  Jan 2-9 are quiet
        on BOTH sides, so there is nothing a reconcile could record on them.
        Under the old boundary they lay before the first cash fact and were
        not compared, so this span could not reconcile until something -- a
        row or a back-dated balance -- was recorded on or before Jan 2;
        compared, they agree, and it does -- the completion test plan step
        X-bk-2 is held to, reachable.
        """
        with app.app_context():
            late = create_account_of_type(
                seed_user, db.session, "Checking", "Late Checking",
                anchor_balance=Decimal("500.00"),
            )
            _seed_import(
                db, late,
                lines=[(date(2025, 12, 31), "-10.00"),
                       (date(2026, 1, 10), "-40.00"),
                       (date(2026, 3, 19), "-15.00")],
            )
            _settle(
                seed_user, seed_periods[0], "40.00", date(2026, 1, 10),
                account=late,
            )
            _settle(
                seed_user, seed_periods[5], "15.00", date(2026, 3, 19),
                account=late,
            )
            db.session.commit()

            listing = _listed(seed_user, late)

            assert _shape(listing) == [
                (_AGREES, date(2026, 1, 2), date(2026, 3, 19)),
            ]
            assert listing.books.day_count == 77
            assert listing.books.imported == 77
            assert listing.books.compared == 77
            assert listing.books.reconciles is True
            # Dec 31 and Jan 1 are on or before the opening: set apart, the
            # line imported and never compared.
            before = listing.before_books
            assert (before.first_day, before.last_day) == (
                date(2025, 12, 31), date(2026, 1, 1),
            )
            assert before.compared == 0
            assert before.disagreeing == 0
            _assert_partition(listing)

    def test_a_line_ON_the_opening_day_is_set_apart_and_never_disagrees(
        self, app, seed_user, seed_periods, db,
    ):
        """Books open 2026-02-02 and the bank posts ``-25.00`` that day.

        An account created in one step: the opening and the origination
        assertion share Feb 2, which the old boundary took as the first cash
        fact, so this line was counted among the set-apart days as
        DISAGREEING although the ``$500.00`` opening already holds it.  It is
        imported and never compared now.  Feb 4's ``-40.00`` has its row, and
        the balance declared on Feb 5 is what the books then produce.
        """
        with app.app_context():
            account = _opened_on(seed_user, date(2026, 2, 2))
            _seed_import(
                db, account,
                lines=[(date(2026, 2, 2), "-25.00"),
                       (date(2026, 2, 4), "-40.00")],
            )
            _settle(
                seed_user, seed_periods[2], "40.00", date(2026, 2, 4),
                account=account,
            )
            _assert_balance(
                seed_user, db, "460.00", date(2026, 2, 5), account=account,
            )
            db.session.commit()

            listing = _listed(seed_user, account)

            before = listing.before_books
            assert (before.first_day, before.last_day) == (
                date(2026, 2, 2), date(2026, 2, 2),
            )
            assert before.imported == 1
            assert before.compared == 0
            assert before.disagreeing == 0
            # Feb 3 - Feb 5: Feb 4's pair agrees and the other two are quiet,
            # but Feb 5 lies past the statement's last line, so it was never
            # imported.
            assert _shape(listing) == [
                (_AGREES, date(2026, 2, 3), date(2026, 2, 4)),
                (_NO_STATEMENT, date(2026, 2, 5), date(2026, 2, 5)),
            ]
            _assert_partition(listing)


class TestDaysTheComparisonCannotTake:
    """Imported days that were never compared, and why each was not."""

    @pytest.mark.usefixtures("seed_periods")
    def test_days_past_the_TWO_YEAR_drawing_bound_are_cut_at_a_window(self, app, seed_user, db):
        """The seeded account: books from 2024-01-05, a 789-day span.

        One import's lines run 2024-02-01 to 2026-03-03, so the comparison
        draws only its last 731 days, from 2024-03-03.  The span's first 58
        days were never drawn, and the window's first day falls among them:
        2024-01-05..01-31 lie in no window (27 days), 2024-02-01..03-02 lie
        in one (31 days, 2024 being a leap year).  Neither stretch was
        compared, and they are two kinds because only one was imported.  The
        two 2026 lines have no row against them.

        **No day list is built for the undrawn days** -- they are cut only
        where a window begins or ends -- which is what keeps a mistyped
        opening year from costing a list of every day since.
        """
        with app.app_context():
            _assert_balance(seed_user, db, "1000.00", date(2026, 3, 3))
            _seed_import(
                db, seed_user["account"],
                lines=[(date(2024, 2, 1), "-5.00"),
                       (date(2026, 3, 2), "-50.00"),
                       (date(2026, 3, 3), "-25.00")],
            )
            db.session.commit()

            listing = _listed(seed_user)

            assert _shape(listing) == [
                (_NO_STATEMENT, date(2024, 1, 5), date(2024, 1, 31)),
                (_NOT_COMPARED, date(2024, 2, 1), date(2024, 3, 2)),
                (_AGREES, date(2024, 3, 3), date(2026, 3, 1)),
                (_DISAGREES, date(2026, 3, 2), date(2026, 3, 3)),
            ]
            assert [s.verdict.day_count for s in listing.stretches] == [
                27, 31, 729, 2,
            ]
            assert listing.books.day_count == 789
            assert listing.books.compared == 731
            assert listing.books.imported == 762
            assert listing.books.disagreeing == 2
            assert listing.before_books is None
            _assert_partition(listing)


    @pytest.mark.usefixtures("seed_periods")
    def test_an_old_window_ENDING_among_undrawn_days_is_cut_on_its_last_day(
        self, app, seed_user, db,
    ):
        """A firing control on the window's END edge -- found by review.

        The seeded account's books run from 2024-01-05.  An old statement's
        lines run 2024-01-10..01-20 and a new one's 2026-03-02..03-03, so the
        comparison draws only the last two years and the old window lies
        wholly among days it never drew.  Its LAST day, Jan 20, is inside it
        and Jan 21 is not: an end edge cut one day early or late reads one
        day of the wrong kind, and no other case here places a window's end
        among undrawn days.
        """
        with app.app_context():
            _assert_balance(seed_user, db, "1000.00", date(2026, 3, 3))
            _seed_import(
                db, seed_user["account"],
                lines=[(date(2024, 1, 10), "-5.00"),
                       (date(2024, 1, 20), "-6.00")],
                file_name="old.csv",
            )
            _seed_import(
                db, seed_user["account"],
                lines=[(date(2026, 3, 2), "-50.00"),
                       (date(2026, 3, 3), "-25.00")],
                file_name="new.csv",
            )
            db.session.commit()

            listing = _listed(seed_user)

            assert _shape(listing) == [
                (_NO_STATEMENT, date(2024, 1, 5), date(2024, 1, 9)),
                (_NOT_COMPARED, date(2024, 1, 10), date(2024, 1, 20)),
                (_NO_STATEMENT, date(2024, 1, 21), date(2026, 3, 1)),
                (_DISAGREES, date(2026, 3, 2), date(2026, 3, 3)),
            ]
            _assert_partition(listing)


class TestTheDaysAfterTheLatestBalance:
    """Imported days past the books span are graded, and kept out of it."""

    @pytest.mark.usefixtures("seed_periods")
    def test_an_EMPTY_span_lists_nothing_and_sets_the_statement_apart(self, app, seed_user, db):
        """A brand-new account: books and its only balance on one day.

        The books open 2026-03-01 and the statement's lines run Mar 2-4 with
        no row against them, so the span holds no day and every imported day
        comes after it -- graded, two of them disagreeing, and none counted in
        the span the reconcile is done against.
        """
        with app.app_context():
            account = _opened_on(seed_user, date(2026, 3, 1))
            _seed_import(
                db, account,
                lines=[(date(2026, 3, 2), "-50.00"),
                       (date(2026, 3, 4), "-25.00")],
            )
            db.session.commit()

            listing = _listed(seed_user, account)

            assert listing.stretches == ()
            assert listing.books.day_count == 0
            assert listing.books.reconciles is False
            assert listing.before_books is None
            after = listing.after_books
            assert (after.first_day, after.last_day) == (
                date(2026, 3, 2), date(2026, 3, 4),
            )
            assert after.day_count == 3
            assert after.imported == 3
            assert after.disagreeing == 2
            _assert_partition(listing)


class TestNoListWhereThereIsNoVerdict:
    """Each ``None`` the card answers, the list answers too."""

    def test_an_account_with_NO_statement_has_NO_list(
        self, app, seed_user, seed_periods, db,
    ):
        """Nobody imported a statement: an absence, not an empty list."""
        with app.app_context():
            _settle(seed_user, seed_periods[4], "150.00", date(2026, 3, 3))
            _assert_balance(seed_user, db, "1000.00", date(2026, 3, 3))
            db.session.commit()

            assert _listed(seed_user) is None

    @pytest.mark.usefixtures("seed_periods")
    def test_a_MODELLED_account_has_NO_list(self, app, seed_user, db):
        """Ruling R-FO: an HYSA's balance is not a check against cash."""
        with app.app_context():
            hysa = create_account_of_type(
                seed_user, db.session, "HYSA", "Savings",
                anchor_balance=Decimal("5000.00"),
            )
            db.session.commit()

            assert _listed(seed_user, hysa) is None

    @pytest.mark.usefixtures("seed_periods")
    def test_an_account_that_has_ASSERTED_NOTHING_has_NO_list(self, app, seed_user, db):
        """No declaration, so no span to list."""
        with app.app_context():
            orphan = account_never_asserted(
                seed_user, db.session, name="Never asserted",
                opening_equity=Decimal("75.00"),
            )
            db.session.commit()

            assert _listed(seed_user, orphan) is None
