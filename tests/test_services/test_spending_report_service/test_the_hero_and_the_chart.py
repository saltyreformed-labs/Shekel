"""The hero band and the chart: the vs-prior and vs-average comparisons, the twelve windows derived
off the pay calendar, and the trailing series both read.
"""

from contextlib import contextmanager
from datetime import date, timedelta
from decimal import Decimal

from sqlalchemy import event

from app.models.account import Account
from app.models.pay_period import PayPeriod
from app.services import spending_analysis
from app.services.cash_flow_set import AttributionSpan, CashFlowSet, Paychecks
from app.services.pay_calendar import PayCalendar
from app.services.spending_report_service import (
    Comparison,
    SpendingWindow,
    compute_spending_report,
)
from app.services.spending_report_service._hero import _TRAILING_WINDOW_COUNT
from app.services.spending_report_service._types import _ScopeIds
from app.services.spending_report_service._window import (
    _CHART_WINDOW_COUNT,
    _series_windows,
    _shift_month,
)
from tests._test_helpers import (
    eras_of,
    last_covered_day,
    pay_periods_hydrated,
    record_paydays_across_a_hole,
    rhythm_of,
)

from ._helpers import _txn, _pp_window


@contextmanager
def _pay_period_selects(engine):
    """Capture every statement this block SELECTs FROM ``budget.pay_periods``.

    The engine rather than the session, because what is being counted is
    round trips to the database: a ``db.session.get`` served out of the
    identity map issues none, which is exactly how the retired ordinal
    walk's own row load stayed invisible while it ran eleven queries beside
    it (plan step C2-f3d).

    **It matches the FROM clause and so a JOIN is invisible to it**, which is
    deliberate and was measured at plan step C2-f3e: a query JOINing the table
    is captured as ZERO statements even while it hydrates ten ``PayPeriod``
    entities.  That is the right scope for the question here -- the assertions
    below count reads of the table as a SUBJECT, and the settled-spend
    loader's calendar span (``AttributionSpan``) KEEPS its join for the
    COALESCE attribution filter -- but it means this probe cannot grade a
    join-filtered read, and :func:`~tests._test_helpers.pay_periods_hydrated`
    beside it is what sees those.

    Args:
        engine: The SQLAlchemy engine to listen on, normally ``db.engine``.

    Yields:
        The list of flattened SQL statements, appended to as the block runs.
    """
    captured = []

    def _record(_conn, _cursor, statement, _params, _context, _executemany):
        flattened = " ".join(statement.split())
        if "FROM budget.pay_periods" in flattened:
            captured.append(flattened)

    event.listen(engine, "before_cursor_execute", _record)
    try:
        yield captured
    finally:
        event.remove(engine, "before_cursor_execute", _record)


# ── Hero band ────────────────────────────────────────────────────────


class TestHero:
    """Hero comparisons: vs-prior, vs-average, None-safety."""

    def test_vs_prior_pct(self, app, seed_user, seed_periods, db):
        """vs-prior compares the immediately preceding same-type window.

        period[0] spends 1000, period[1] spends 1500.  Window = period[1]:
        delta 500, pct 500/1000*100 = 50.00.
        """
        with app.app_context():
            _txn(db, seed_user, seed_periods[0], "A", "Rent", "1000.00")
            _txn(db, seed_user, seed_periods[1], "B", "Rent", "1500.00")
            db.session.commit()

            report = compute_spending_report(
                seed_user["user"].id, _pp_window(seed_periods[1]),
                user_settings=None,
            )
            assert report.hero.spent_total == Decimal("1500.00")
            assert report.hero.vs_prior.baseline == Decimal("1000.00")
            assert report.hero.vs_prior.delta == Decimal("500.00")
            assert report.hero.vs_prior.pct == Decimal("50.00")

    def test_vs_prior_none_when_no_prior_period(self, app, seed_user, seed_periods, db):
        """The earliest period has no prior window -> a None comparison."""
        with app.app_context():
            _txn(db, seed_user, seed_periods[0], "A", "Rent", "1000.00")
            db.session.commit()

            report = compute_spending_report(
                seed_user["user"].id, _pp_window(seed_periods[0]),
                user_settings=None,
            )
            assert report.hero.vs_prior.baseline is None
            assert report.hero.vs_prior.delta is None
            assert report.hero.vs_prior.pct is None

    def test_vs_prior_empty_prior_is_pct_none(self, app, seed_user, seed_periods, db):
        """An empty (zero-spend) prior window gives a real delta but None pct.

        period[0] has no spend; period[1] spends 800.  Prior exists (a lean
        period) so baseline 0, delta 800, but pct is None (no percent of 0).
        """
        with app.app_context():
            _txn(db, seed_user, seed_periods[1], "B", "Rent", "800.00")
            db.session.commit()

            report = compute_spending_report(
                seed_user["user"].id, _pp_window(seed_periods[1]),
                user_settings=None,
            )
            assert report.hero.vs_prior.baseline == Decimal("0")
            assert report.hero.vs_prior.delta == Decimal("800.00")
            assert report.hero.vs_prior.pct is None

    def test_vs_average_over_six_windows(self, app, seed_user, seed_periods, db):
        """vs-average uses the trailing six same-type windows.

        period[0..5] spend 100,200,300,400,500,600; window = period[6] spends
        1000.  Trailing average = 2100/6 = 350.00; delta 650; pct
        650/350*100 = 185.71.
        """
        with app.app_context():
            for idx, amt in enumerate([100, 200, 300, 400, 500, 600]):
                _txn(db, seed_user, seed_periods[idx], f"P{idx}", "Rent",
                     f"{amt}.00")
            _txn(db, seed_user, seed_periods[6], "W", "Rent", "1000.00")
            db.session.commit()

            report = compute_spending_report(
                seed_user["user"].id, _pp_window(seed_periods[6]),
                user_settings=None,
            )
            assert report.hero.vs_average.baseline == Decimal("350.00")
            assert report.hero.vs_average.delta == Decimal("650.00")
            assert report.hero.vs_average.pct == Decimal("185.71")

    def test_vs_average_none_when_no_prior(self, app, seed_user, seed_periods, db):
        """No trailing windows -> a None average comparison."""
        with app.app_context():
            _txn(db, seed_user, seed_periods[0], "A", "Rent", "500.00")
            db.session.commit()

            report = compute_spending_report(
                seed_user["user"].id, _pp_window(seed_periods[0]),
                user_settings=None,
            )
            assert report.hero.vs_average.baseline is None
            assert report.hero.vs_average.pct is None

    def test_payment_timing_scoped_to_window(self, app, seed_user, seed_periods, db):
        """Payment timing reuses the year-end rule over the window's rows.

        Two settled bills in the window, both paid on their due date (0 days):
        on-time 2, late 0, avg 0.00.
        """
        with app.app_context():
            due = seed_periods[0].start_date
            # The settle day IS the due date, so days_paid_before_due == 0 (on
            # time).  It was a noon-UTC instant until plan step X-f1, chosen so
            # the display-timezone conversion landed back on the same civil day;
            # the column stores that day directly now, so the arithmetic is
            # exact rather than zone-dependent.
            paid = due
            _txn(db, seed_user, seed_periods[0], "R", "Rent", "1200.00",
                 actual="1200.00", due_date=due, settled_on=paid)
            _txn(db, seed_user, seed_periods[0], "G", "Groceries", "400.00",
                 actual="400.00", due_date=due, settled_on=paid)
            db.session.commit()

            report = compute_spending_report(
                seed_user["user"].id, _pp_window(seed_periods[0]),
                user_settings=None,
            )
            timing = report.hero.payment_timing
            assert timing["total_bills_paid"] == 2
            assert timing["paid_on_time"] == 2
            assert timing["paid_late"] == 0
            assert timing["avg_days_before_due"] == Decimal("0.00")


# ── The chart's twelve windows (plan step C2-f3d) ────────────────────


def _calendar_scope(paydays, cadence_days=14, user_id=1):
    """Return a ``_ScopeIds`` whose calendar is *paydays*, with no database.

    ``_series_windows`` reads the scope's CALENDAR and nothing else, so the
    three window arms are exercised over hand-written paydays rather than
    through a fixture -- which is what lets the cases below name an exact
    expected id list per slot.  The cash-flow set and the scenario id are
    never read on this path: the set is one unsaved account row, the
    placeholder ``account_id=1`` was until plan step CC-4-3.
    """
    return _ScopeIds(
        cash_flow=CashFlowSet.single(Account(id=1)),
        scenario_id=1,
        calendar=PayCalendar.from_paydays(paydays, eras_of(paydays, cadence_days), user_id, history_opens_on=None),
    )


class TestSeriesWindows:
    """The twelve chart windows, derived in one pass off the calendar.

    ``_shift_month`` is exercised here rather than in a class of its own: it
    is the month arm's one primitive and has no other caller since plan step
    C2-f3d deleted ``_shift_window``.
    """

    def test_shift_month_january_rolls_to_prior_december(self):
        """One month before January 2026 is December 2025."""
        assert _shift_month(2026, 1, 1) == (2025, 12)
        # Two months before March 2026 is January 2026.
        assert _shift_month(2026, 3, 2) == (2026, 1)
        # Thirteen months before June 2026 is May 2025.
        assert _shift_month(2026, 6, 13) == (2025, 5)

    def test_year_series_is_twelve_descending_years(self):
        """A year window's series is 2015..2026, the chosen year last."""
        scope = _calendar_scope([(1, date(2026, 1, 2))])
        chosen = SpendingWindow(window_type="year", year=2026)

        windows = _series_windows(scope, chosen)

        assert len(windows) == _CHART_WINDOW_COUNT
        assert [w.year for w in windows] == list(range(2015, 2027))
        assert {w.window_type for w in windows} == {"year"}
        assert windows[-1] == chosen

    def test_month_series_rolls_the_year(self):
        """March 2026 trails back to April 2025, twelve slots inclusive."""
        scope = _calendar_scope([(1, date(2026, 1, 2))])
        chosen = SpendingWindow(window_type="month", month=3, year=2026)

        windows = _series_windows(scope, chosen)

        assert [(w.year, w.month) for w in windows] == [
            (2025, 4), (2025, 5), (2025, 6), (2025, 7), (2025, 8),
            (2025, 9), (2025, 10), (2025, 11), (2025, 12),
            (2026, 1), (2026, 2), (2026, 3),
        ]
        assert windows[-1] == chosen

    def test_pay_period_series_is_the_twelve_preceding_paychecks(self):
        """With 20 paydays, viewing #14 gives ids 3..14 and no blank slot.

        Paydays are ids 1..20 on a 14-day cadence from 2026-01-02, so the
        calendar's derived ordinals are 0..19 in that same order.  Viewing
        the period with ordinal 13 (id 14) fills every slot: ordinals
        2..13, which are ids 3..14.
        """
        paydays = [
            (n + 1, date(2026, 1, 2) + timedelta(days=14 * n))
            for n in range(20)
        ]
        chosen = SpendingWindow(window_type="pay_period", period_id=14)

        windows = _series_windows(_calendar_scope(paydays), chosen)

        assert len(windows) == _CHART_WINDOW_COUNT
        assert [w.period_id for w in windows] == list(range(3, 15))
        assert windows[-1] == chosen

    def test_pay_period_series_pads_before_the_first_payday(self):
        """A schedule shorter than the chart leaves the LEADING slots blank.

        Four paydays, viewing the third (ordinal 2, id 3): the two earlier
        paychecks fill slots 9 and 10, the chosen one slot 11, and the nine
        slots below the owner's first payday are ``None`` rather than
        shortening the series -- which is what keeps ``series[-2]`` the
        prior window for every owner.
        """
        paydays = [
            (n + 1, date(2026, 1, 2) + timedelta(days=14 * n))
            for n in range(4)
        ]
        chosen = SpendingWindow(window_type="pay_period", period_id=3)

        windows = _series_windows(_calendar_scope(paydays), chosen)

        assert [w.period_id if w else None for w in windows] == (
            [None] * 9 + [1, 2, 3]
        )

    def test_pay_period_series_of_the_first_paycheck_has_no_prior(self):
        """The earliest paycheck fills one slot; the other eleven are blank."""
        paydays = [
            (n + 1, date(2026, 1, 2) + timedelta(days=14 * n))
            for n in range(4)
        ]
        chosen = SpendingWindow(window_type="pay_period", period_id=1)

        windows = _series_windows(_calendar_scope(paydays), chosen)

        assert windows[:-1] == [None] * (_CHART_WINDOW_COUNT - 1)
        assert windows[-1] == chosen

    def test_an_unmatched_period_id_leaves_every_earlier_slot_blank(self):
        """An id naming none of this calendar's periods resolves no predecessor.

        The ``None`` branch, stated at the unit level over a hand-written
        calendar; the case that MATTERS -- an id belonging to another owner
        rather than to nobody -- takes the same branch and is asserted
        end to end by
        ``TestTheChartReadsTheDerivedOrdinal.test_another_owners_period_id_buys_no_bar_of_this_owners_money``,
        which is where the money is.
        """
        paydays = [
            (n + 1, date(2026, 1, 2) + timedelta(days=14 * n))
            for n in range(4)
        ]
        chosen = SpendingWindow(window_type="pay_period", period_id=9999)

        windows = _series_windows(_calendar_scope(paydays), chosen)

        assert windows == [None] * (_CHART_WINDOW_COUNT - 1) + [chosen]

    def test_the_series_never_reaches_past_the_chosen_window(self):
        """No slot names a paycheck LATER than the chosen one.

        The chart is retrospective: ``viewed_index`` is the last bar, so a
        later period appearing anywhere in the list would draw future
        spending beside the window the user asked for.
        """
        paydays = [
            (n + 1, date(2026, 1, 2) + timedelta(days=14 * n))
            for n in range(20)
        ]
        scope = _calendar_scope(paydays)

        for period_id in range(1, 21):
            windows = _series_windows(
                scope,
                SpendingWindow(window_type="pay_period", period_id=period_id),
            )
            assert [w.period_id for w in windows if w is not None] == [
                earlier for earlier in range(1, period_id + 1)
            ][-_CHART_WINDOW_COUNT:]


class TestTheChartAndTheHeroCountTheSameWindows:
    """The two window counts are held in step by a test, not by a comment.

    Both modules state the relation and both say it is unenforced.  It was:
    mutation testing of plan step C2-f3d set ``_TRAILING_WINDOW_COUNT`` to 12
    and the whole 10,236-test suite stayed green, while the vs-average chip
    silently began averaging over a window the chart does not draw -- a wrong
    figure given quietly, which is this project's worst failure shape.  The
    precedent is ``test_pay_schedule.TestTheCadenceBoundHasOneValue``, which
    holds the two cadence bounds equal the same way.
    """

    def test_the_average_reads_fewer_windows_than_the_chart_draws(self):
        """vs-average must derive from bars the chart actually draws.

        ``_build_hero`` averages ``series[-(N + 1):-1]``, so the baseline
        stays inside the drawn series only while ``_TRAILING_WINDOW_COUNT``
        is strictly below ``_CHART_WINDOW_COUNT``.
        """
        assert 1 <= _TRAILING_WINDOW_COUNT < _CHART_WINDOW_COUNT

    def test_the_chart_has_room_for_a_chosen_window_and_a_prior(self):
        """``[-1]`` and ``[-2]`` are named positions, so the count is >= 2.

        ``_series_windows``, ``_build_series``, ``_build_hero`` and the
        chart's ``compare_index`` all index the last two slots directly.
        """
        assert _CHART_WINDOW_COUNT >= 2


class TestTheChartReadsTheDerivedOrdinal:
    """The chart's paychecks come from payday order.

    **Two firing controls for plan step C2-f3d went at plan step
    ``pay_calendar:C4-c``, and neither was replaced.**  The retired walk
    selected each bar with ``WHERE period_index = <chosen> - <steps>``, so a
    stored ordinal out of payday order put the wrong paycheck in a bar
    silently, and a HOLE in the stored ordinal moved a MONEY figure -- the
    vs-average baseline read ``$1,300.00`` against a true ``$1,250.00``.  Both
    cases built their state by writing ``budget.pay_periods.period_index``
    directly, and C4-c dropped that column: the ordinal is a row's position in
    payday order, so it cannot be scrambled and it cannot gap.

    What survives here is the ownership case, which is about the chosen
    period's OWNER rather than about its ordinal.
    """

    def test_another_owners_period_id_buys_no_bar_of_this_owners_money(
        self, app, seed_user, second_user, seed_periods, db,
    ):
        """A FOREIGN period id resolves nothing, chips included.

        The walk read the chosen id with an unscoped ``db.session.get``, so
        another owner's period supplied an ordinal and THIS owner's paychecks
        were listed beneath it: five bars of real money under a window whose
        own total is ``None``, with both hero chips priced off them
        (measured on the merge base: vs-prior ``$500.00``, vs-average
        ``$300.00``, each at ``-100%``).  The calendar holds one owner's
        periods, so the id now matches none and every slot is blank.

        Unreachable from ``/analytics/spending``, which exposes only month
        and year -- this pins the door rather than closing a live leak, and
        it is the ownership case ``9999`` cannot make because an id belonging
        to NOBODY takes the same branch as one belonging to someone else.
        """
        with app.app_context():
            record_paydays_across_a_hole(
                user_id=second_user["user"].id,
                first_payday=date(2026, 1, 2),
                num_periods=10,
                rhythm=rhythm_of(14),
            )
            db.session.flush()
            foreign = (
                db.session.query(PayPeriod)
                .filter_by(user_id=second_user["user"].id)
                .order_by(PayPeriod.start_date)
                .all()
            )
            for idx in range(5):
                _txn(db, seed_user, seed_periods[idx], f"P{idx}", "Rent",
                     f"{(idx + 1) * 100}.00")
            db.session.commit()

            report = compute_spending_report(
                seed_user["user"].id,
                SpendingWindow(window_type="pay_period",
                               period_id=foreign[5].id),
                               user_settings=None,
            )

            assert [point.window for point in report.series[:-1]] == (
                [None] * (_CHART_WINDOW_COUNT - 1)
            )
            assert report.series[-1].window.period_id == foreign[5].id
            assert report.series[-1].total is None
            assert report.hero.spent_total == Decimal("0")
            assert report.hero.vs_prior.baseline is None
            assert report.hero.vs_average.baseline is None
            assert report.breakdown == [] and report.changes == []

    def test_the_report_reads_pay_periods_exactly_once(
        self, app, seed_user, seed_periods, db,
    ):
        """A report reads one ``budget.pay_periods`` row set and hydrates none.

        **Over ROWS.**  Since leaf ``balance:X-bi-6-1b`` a window's transfers
        load beside its rows, and a transfer's pricing chain carries its
        period (``transfer_pricing_load_options``: rule 4's derive arm reads
        it), so a fixture seeding a settled transfer would read one more
        statement and hydrate its period; this fixture seeds none and the
        claim is the row path's.

        **Measured on the merge base at TWELVE statements for this fixture and
        TWENTY-THREE on a clone of production**, and the difference is the
        point: the retired walk's eleven ``db.session.get`` calls issued no
        statement whenever the chosen window's own rows had already hydrated
        that period, and eleven statements when it had no rows to do so (the
        identity map holds weak references).  Either way the eleven
        ``period_index`` queries beside them always ran.

        **The two assertions catch different things and neither is redundant**
        (mutation testing of this step measured the gap).  A statement count
        alone is order-dependent and JOIN-blind: nine per-bar
        ``db.session.get`` calls placed AFTER the row loads emit no statement,
        and an eager load renders as ``LEFT OUTER JOIN budget.pay_periods``
        inside the transactions query, which the capture predicate cannot see.
        The identity map sees both -- a report that resolves a paycheck any
        way at all leaves the ORM entity behind, and this path is meant to
        leave none, because the calendar is loaded as a column tuple.

        ONE statement is the count for an owner with a ``budget.pay_schedule``
        row, which every owner a live door creates has had since plan step
        X-ad-a.  A legacy owner without one (plan finding **P8**) reads TWO,
        because ``pay_schedule_service.resolve_cadence`` infers the cadence
        from a second ``budget.pay_periods`` query -- one that C4-c deleted with
        the column it orders by.  ``seed_user`` has the row.
        """
        with app.app_context():
            for idx in range(10):
                _txn(db, seed_user, seed_periods[idx], f"P{idx}", "Rent",
                     "100.00")
            db.session.commit()
            with _pay_period_selects(db.engine) as selects, \
                    pay_periods_hydrated() as hydrated:
                compute_spending_report(
                    seed_user["user"].id, _pp_window(seed_periods[9]),
                    user_settings=None,
                )

            assert len(selects) == 1, "\n".join(selects)
            # The one surviving read may not be an ORDINAL search: C4-c dropped
            # that column, and ``_loader.calendar_for`` selects id + start_date
            # precisely so it already runs against the schema C4 leaves.
            assert "period_index" not in selects[0]
            assert hydrated == [], (
                f"the report hydrated {len(hydrated)} PayPeriod row(s); "
                f"it resolves every paycheck from the calendar it already "
                f"holds, which is loaded as a column tuple"
            )

    def test_no_settled_expense_row_query_loads_a_pay_period(
        self, app, seed_user, seed_periods, db,
    ):
        """Neither window's ROW query hydrates ``Transaction.pay_period``.

        The legs half (leaf ``balance:X-bi-6-1b``) loads the parent's period
        by design; no transfer is seeded here, so this grades the row query
        alone (the body says so where it counts).  The other half of the
        guard above, and it needs its own case because
        the statement counter cannot see this one: an eager load rides INSIDE
        the transactions query as a ``JOIN budget.pay_periods``, so re-adding
        one emits no statement of its own and moves no count.  What it does
        move is the identity map, which is what this asserts.

        Nothing on this read path asks a row which paycheck it sits in, so
        both queries were carrying a per-row ``PayPeriod`` for nobody.  The
        span query keeps its JOIN -- the COALESCE attribution filter runs on
        it -- and drops only the hydration.
        """
        with app.app_context():
            _txn(db, seed_user, seed_periods[0], "A", "Rent", "100.00")
            _txn(db, seed_user, seed_periods[1], "B", "Rent", "200.00")
            db.session.commit()

            # The set of one account is the old single-account filter, byte
            # for byte (plan step CC-4-3); the assertion is about the loader.
            cash_flow = CashFlowSet.single(seed_user["account"])
            with pay_periods_hydrated() as hydrated:
                by_period = spending_analysis.query_settled_expenses(
                    cash_flow, seed_user["scenario"].id,
                    Paychecks((seed_periods[0].id, seed_periods[1].id)),
                )
                by_span = spending_analysis.query_settled_expenses(
                    cash_flow, seed_user["scenario"].id,
                    AttributionSpan(
                        seed_periods[0].start_date,
                        last_covered_day(seed_periods[1]),
                    ),
                )

            # Both loads must actually return rows, or "nothing was
            # hydrated" is true of a query that loaded nothing at all.  Each
            # answers its rows then its legs since leaf balance:X-bi-6-1b, as
            # one list since plan step plan#2130; no transfer is seeded, so
            # it holds rows alone and the claim below is about the ROW query
            # (a leg's parent carries its period by design: rule 4's derive
            # arm reads it).
            assert len(by_period) == 2 and len(by_span) == 2
            assert hydrated == [], (
                "a settled-expense query hydrated a PayPeriod.  This asserts "
                "the QUERY's loader, not that nothing downstream reads the "
                "relationship: since the surprises list began pricing through "
                "cash_ledger (2026-09-05), rule 4's loan arm reads "
                "txn.pay_period, so the old 'no consumer reads it' wording was "
                "made false by that change and is corrected here rather than "
                "left standing"
            )


# ── Trailing series (the chart / hero one-source identity) ──────────


class TestSeries:
    """The trailing same-type window series the chart and hero share."""

    def test_series_shape_and_pay_period_totals(self, app, seed_user,
                                                seed_periods, db):
        """Twelve points, viewed last; each point totals its own window.

        period[0..2] spend 100/200/300; window = period[2].  The series is
        _CHART_WINDOW_COUNT (12) points ending at the viewed window: the
        last three total 300/200/100 walking back, and every earlier slot
        is an all-None point (no period exists before period[0]).
        """
        with app.app_context():
            for idx, amt in enumerate(["100.00", "200.00", "300.00"]):
                _txn(db, seed_user, seed_periods[idx], f"T{idx}", "Rent", amt)
            db.session.commit()

            report = compute_spending_report(
                seed_user["user"].id, _pp_window(seed_periods[2]),
                user_settings=None,
            )
            series = report.series
            assert len(series) == _CHART_WINDOW_COUNT
            assert series[-1].window.period_id == seed_periods[2].id
            assert series[-1].total == Decimal("300.00")
            assert series[-2].total == Decimal("200.00")
            assert series[-3].total == Decimal("100.00")
            # Slots before the user's first period are all-None points.
            assert all(
                p.window is None and p.total is None for p in series[:-3]
            )

    def test_series_month_zero_vs_none(self, app, seed_user, seed_periods, db):
        """A tracked zero-spend month is 0; a pre-history month is None.

        seed_periods span 2026-01-02 .. 2026-05-21.  Viewing March 2026:
        the series is Apr 2025 .. Mar 2026.  Jan spends 1200, Feb nothing
        (tracked, so Decimal 0), Mar spends 300; Apr-Dec 2025 overlap no
        period, so their totals are None (excluded from averages; drawn as
        baseline ticks).
        """
        with app.app_context():
            _txn(db, seed_user, seed_periods[0], "JanRent", "Rent", "1200.00",
                 due_date=date(2026, 1, 10))
            _txn(db, seed_user, seed_periods[5], "MarBill", "Rent", "300.00",
                 due_date=date(2026, 3, 15))
            db.session.commit()

            report = compute_spending_report(
                seed_user["user"].id,
                SpendingWindow(window_type="month", month=3, year=2026),
                user_settings=None,
            )
            series = report.series
            # Apr 2025 .. Dec 2025: before the user's first period.
            assert [p.total for p in series[:9]] == [None] * 9
            assert (series[9].window.year, series[9].window.month) == (2026, 1)
            assert series[9].total == Decimal("1200.00")
            assert series[10].total == Decimal("0")   # Feb: tracked, no spend
            assert series[11].total == Decimal("300.00")  # March (viewed)

    def test_year_window_series_end_to_end(self, app, seed_user,
                                           seed_periods, db):
        """A YEAR window produces a full twelve-point series through the report.

        The arm had no end-to-end case: the only ``window_type="year"`` outside
        ``tests/manual/`` was the unit test on ``_series_windows``.  It is
        unrenderable by the S-P1 deferral (the route exposes month only) and
        computable, which is exactly the pair that goes untested by accident.

        ``seed_periods`` spans 2026-01-02..2026-05-21 with 1200 spent in
        January, so 2026 totals 1200.00, 2025 and earlier overlap no pay
        period and are ``None``.
        """
        with app.app_context():
            _txn(db, seed_user, seed_periods[0], "JanRent", "Rent", "1200.00",
                 due_date=date(2026, 1, 10))
            db.session.commit()

            report = compute_spending_report(
                seed_user["user"].id,
                SpendingWindow(window_type="year", year=2026),
                user_settings=None,
            )

            assert [p.window.year for p in report.series] == list(
                range(2015, 2027)
            )
            assert report.series[-1].total == Decimal("1200.00")
            assert [p.total for p in report.series[:-1]] == [None] * 11
            assert report.hero.spent_total == Decimal("1200.00")
            assert report.hero.vs_prior.baseline is None
            assert report.hero.vs_average.baseline is None

    def test_hero_baselines_read_the_series(self, app, seed_user,
                                            seed_periods, db):
        """The hero's baselines derive from the series (one data source).

        Same data as above, viewing March: vs-prior baseline is the
        series' Feb point (0 -> delta 300.00, pct None); vs-average
        averages the existing points among Sep 2025..Feb 2026 -- Jan 1200
        and Feb 0 (the None 2025 months are skipped) -> (1200 + 0) / 2 =
        600.00; delta 300 - 600 = -300.00; pct -300/600*100 = -50.00.
        """
        with app.app_context():
            _txn(db, seed_user, seed_periods[0], "JanRent", "Rent", "1200.00",
                 due_date=date(2026, 1, 10))
            _txn(db, seed_user, seed_periods[5], "MarBill", "Rent", "300.00",
                 due_date=date(2026, 3, 15))
            db.session.commit()

            report = compute_spending_report(
                seed_user["user"].id,
                SpendingWindow(window_type="month", month=3, year=2026),
                user_settings=None,
            )
            assert report.hero.vs_prior.baseline == Decimal("0")
            assert report.hero.vs_prior.delta == Decimal("300.00")
            assert report.hero.vs_prior.pct is None
            assert report.hero.vs_average.baseline == Decimal("600.00")
            assert report.hero.vs_average.delta == Decimal("-300.00")
            assert report.hero.vs_average.pct == Decimal("-50.00")


# ── Comparison value object ──────────────────────────────────────────


class TestComparison:
    """The hero comparison value object's None-safety."""

    def test_of_none_baseline(self):
        """A None baseline yields an all-None comparison."""
        cmp = Comparison.of(Decimal("100.00"), None)
        assert cmp.baseline is None
        assert cmp.delta is None
        assert cmp.pct is None

    def test_of_zero_baseline(self):
        """A zero baseline yields a real delta but a None percent."""
        cmp = Comparison.of(Decimal("100.00"), Decimal("0"))
        assert cmp.baseline == Decimal("0")
        assert cmp.delta == Decimal("100.00")
        assert cmp.pct is None

    def test_of_NEGATIVE_baseline_reports_no_percent(self):
        """A refund-dominated prior window has no percent CHANGE to state.

        Ruling **bank_import:R-II**, plan step ``bank_import:X-gj-2b``.  A
        window's spend could not be negative while ``_spent_total`` took an
        ``abs()`` per row; a merchant credit files as a NEGATIVE purchase now,
        so a window whose refunds exceeded its purchases comes to a negative
        total and can be a baseline.

        **The ratio against one reports the wrong DIRECTION.**  Spend rising
        from ``-50.00`` to ``100.00`` is a rise of ``150.00``, and
        ``150 / -50`` is ``-300%`` -- a fall of three hundred percent printed
        over a rise, on the hero chip the owner reads.  Asserts the figures
        SURVIVE beside the missing percent, because suppressing the delta too
        would hide a real movement.
        """
        cmp = Comparison.of(Decimal("100.00"), Decimal("-50.00"))

        assert cmp.baseline == Decimal("-50.00")
        assert cmp.delta == Decimal("150.00")
        assert cmp.pct is None

    def test_of_POSITIVE_baseline_still_reports_its_percent(self):
        """The control, without which the case above grades a chip that never
        computes a percent at all."""
        cmp = Comparison.of(Decimal("150.00"), Decimal("100.00"))

        assert cmp.delta == Decimal("50.00")
        assert cmp.pct == Decimal("50.00")
