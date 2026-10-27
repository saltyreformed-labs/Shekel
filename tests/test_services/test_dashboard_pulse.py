"""
Shekel Budget App -- Dashboard Pulse / Tracks Producer Tests (Loop B B-1)

Tests for ``dashboard_service._pulse`` -- the additive producers behind the
Terminal Road dashboard rebuild:

  * ``compute_pulse_section`` and its helpers: the as-of-today hero
    (balance / staleness / next-paycheck date), the 13-period projected
    end-balance chart + threshold, the full-horizon trough, the still-due
    totals on the locked B4 bases, and the current period's due-soon rows.
  * ``compute_tracks_section`` and ``_track_goal_datum``: the savings-goal
    metro tracks (trajectory passthrough) and the debt track (the debt
    summary, which since plan step X-u carries the principal-paid fraction
    that a second producer used to compute).

The module-level autouse fixture (``test_services/conftest.py``) freezes
``date.today()`` to 2026-03-20, which falls in ``seed_periods[5]`` (the
period 2026-03-13 .. 2026-03-26).  So with ``seed_periods``:

  * the current period is index 5,
  * the forward horizon is periods 5..9 (5 periods),
  * the next paycheck date is ``seed_periods[6].start_date`` (2026-03-27).

Every dollar assertion shows its arithmetic; Decimals are constructed
from strings per the testing standards.
"""

from datetime import date
from decimal import Decimal


from app import ref_cache
from app.enums import GoalModeEnum, IncomeUnitEnum, StatusEnum, TxnTypeEnum
from app.models.ref import AccountType
from app.models.savings_goal import SavingsGoal
from app.services import account_service, cash_ledger, dashboard_service
from app.services.dashboard_service import _bills, _pulse
from app.services.pay_calendar import PayCadence
from app.services import transfer_service
from app.services import balance_at, savings_dashboard_service
from app.services.balance_at import BalanceContext
from tests._test_helpers import (
    create_account_of_type,
    create_transfer,
    one_off_row_of,
    record_paydays_across_a_hole,
    rhythm_of,
    last_covered_day,
    account_never_asserted,
    add_anchor_history as _add_anchor_history,
    add_entry,
    create_envelope_txn,
    create_hysa_account,
    create_loan_account,
    create_savings_account,
    dashboard_section,
    default_settle_day,
    make_investment_account,
    make_salary_profile,
    period_window,
    reassert_balance_on,
    set_default_grid_account,
    settle_instant_on,
    settle_day_columns,
    cover_bare_settled_row,
)
from app.models.amount_ownership import AmountOwnership
from app.services.pay_rhythm import FixedDays


_CURRENT_IDX = 5  # seed_periods index that contains the frozen today.
_TODAY = date(2026, 3, 20)


# ── Helpers ──────────────────────────────────────────────────────────


def _add_expense(
    db_session, seed_user, period, name, amount,
    status_enum=StatusEnum.PROJECTED, due_date=None, is_deleted=False,
):
    """Create a non-tracked expense transaction for testing -- a ONE-OFF.

    Placed through the producer since plan step balance:X-bi-7c, so
    ``due_date=None`` means the paycheck's start (ruling R-BAL22), never an
    undated row: the undated shape is the LEGACY one, and a case about it
    builds :func:`_legacy_undated_expense` instead.  Returns the created
    Transaction (flushed).
    """
    status_id = ref_cache.status_id(status_enum)
    txn = one_off_row_of(
        period,
        name=name,
        amount=Decimal(str(amount)),
        user_id=period.user_id,
        account_id=seed_user["account"].id,
        scenario_id=seed_user["scenario"].id,
        transaction_type_id=ref_cache.txn_type_id(TxnTypeEnum.EXPENSE),
        due_date=due_date,
    )
    txn.status_id = status_id
    txn.is_deleted = is_deleted
    # The settle day laid on BARE, as ``add_txn`` lays it (X-f1); the
    # record is the covering movement written after the flush (X-bi-4b-2).
    settled_on = default_settle_day(period, status_id)
    for _column, _value in settle_day_columns(settled_on).items():
        setattr(txn, _column, _value)
    db_session.flush()
    if settled_on is not None:
        cover_bare_settled_row(db_session, txn, Decimal(str(amount)))
    return txn


def _undated_transfer_out(db_session, seed_user, period, to_account_name, amount):
    """Create an UNDATED transfer out of the checking account.

    The pulse's "anytime this period" shelf exists for items with no due date.
    Every one-off is dated on its paycheck's start since plan step
    balance:X-bi-7b (R-BAL22) and the cutover (balance:X-bi-7d-2) dated
    production's 26 undated one-offs the same way, so what still feeds the
    shelf is an item NO definition prices: a transfer's expense leg (the
    unpaid-expense loader draws the expense leg of a projected transfer out of
    the set, the Gate B4b ruling) made without a due date, which the transfer
    form leaves optional.  The leg is named ``Transfer to <to_account_name>``
    off the transfer's endpoints.

    It returned the transfer's expense shadow row, guarded undated, until plan
    step balance:X-bi-6-4d-2 made a transfer one row (ruling R-BAL258); the
    guard reads the transfer's own due date now, the column the leg projects,
    and no caller read the return (ruling R-BAL167 class 4).
    """
    savings = create_savings_account(
        seed_user, db_session, to_account_name, Decimal("500.00"),
    )
    xfer = create_transfer(
        seed_user, db_session, seed_user["account"], savings, period,
        amount=Decimal(str(amount)),
    )
    db_session.flush()
    assert xfer.due_date is None
    return xfer


def _add_tracked_expense(db_session, seed_user, period, name, estimated):
    """Create an entry-tracked (is_envelope) projected expense.

    Thin wrapper over the shared ``create_envelope_txn`` builder that
    accepts a string ``estimated`` for call-site brevity.
    """
    return create_envelope_txn(
        seed_user, db_session, period, name, Decimal(str(estimated)),
    )


def _add_entry(db_session, seed_user, txn, amount, purchased_on):
    """Attach one debit entry of ``amount`` to ``txn`` (string-amount wrapper)."""
    add_entry(db_session, seed_user, txn, Decimal(str(amount)), purchased_on)


# ── Hero: balance, captions, staleness, next paycheck ───────────────


class TestPulseHero:
    """The pulse hero block (as-of-today balance + captions + flags)."""

    def test_hero_carries_account_and_period(self, app, seed_user, seed_periods, db):
        """Hero carries the account name/id and the current period date range.

        The current period is seed_periods[5] (contains the frozen
        2026-03-20); its start_date is 2026-03-13 and end_date 2026-03-26.
        """
        with app.app_context():
            result = dashboard_service.compute_pulse_section(
                dashboard_section(seed_user["user"].id),
            )
            hero = result["hero"]
            assert hero["account_id"] == seed_user["account"].id
            assert hero["account_name"] == seed_user["account"].name
            assert hero["period_start_date"] == seed_periods[_CURRENT_IDX].start_date
            assert hero["period_start_date"] == date(2026, 3, 13)
            assert hero["period_end_date"] == last_covered_day(seed_periods[_CURRENT_IDX])
            assert hero["period_end_date"] == date(2026, 3, 26)

    def test_hero_balance_is_the_current_periods_end(
        self, app, seed_user, seed_periods, db,
    ):
        """Hero balance is the current period's END, which is what it says.

        ``_pulse.html`` labels ``#balance-display`` "End of this period", so the
        figure is the hero's own period map at the current period -- and thus IS
        the chart's first point rather than a second producer beside it.  It read
        the as-of-TODAY scalar until plan step X-c2b2 (finding N-60): that was
        legitimate only while the scalar was period-FLAT, and the fold made it
        date-precise, so the two now differ by the whole unpaid remainder of the
        current period.

        With the seed account anchored at $1,000.00 and no transactions the two
        coincide at exactly the anchor, $1,000.00 -- which is why this fixture
        could not have caught the divergence and the label is what settles it.
        """
        with app.app_context():
            result = dashboard_service.compute_pulse_section(
                dashboard_section(seed_user["user"].id),
            )
            assert result["hero"]["balance"] == Decimal("1000.00")

    def test_hero_next_paycheck_date_normal(self, app, seed_user, seed_periods, db):
        """next_paycheck_date is the first period starting after today.

        Today is 2026-03-20 (in period 5).  The first period whose
        start_date > today is period 6 (starts 2026-03-27).
        """
        with app.app_context():
            result = dashboard_service.compute_pulse_section(
                dashboard_section(seed_user["user"].id),
            )
            assert (
                result["hero"]["next_paycheck_date"]
                == seed_periods[_CURRENT_IDX + 1].start_date
            )
            assert result["hero"]["next_paycheck_date"] == date(2026, 3, 27)

    def test_next_paycheck_date_none_when_no_future_period(
        self, app, seed_user, db,
    ):
        """No period follows the current one -> next_paycheck_date is None.

        **Asked through the PRODUCER since plan step C2-f1**, where it used to
        call a private ``_next_paycheck_date`` helper that issued its own
        "first period starting after ``date.today()``" query.  That helper is
        deleted: the same render already resolved "the period after the
        current one" for the still-due panel, and the card shows both figures,
        so two queries could put two different paydays on one card.

        The state is built so today is inside the LAST period rather than past
        every period: six paydays from 2026-01-02 at cadence 14 puts the frozen
        2026-03-20 inside 2026-03-13 .. 2026-03-26, which is the last one.
        (``seed_user`` carries a 2024 bootstrap payday too, so the calendar
        holds seven; only the LAST one decides this.)  The
        old fixture (periods entirely in the past) could not reach the producer
        at all -- it answers ``None`` outright with no current period -- so
        this grades the rendered value where that one graded a helper.
        """
        with app.app_context():
            record_paydays_across_a_hole(
                user_id=seed_user["user"].id,
                first_payday=date(2026, 1, 2),
                num_periods=6,
                rhythm=rhythm_of(14),
            )
            db.session.commit()

            result = dashboard_service.compute_pulse_section(
                dashboard_section(seed_user["user"].id),
            )
            assert result is not None
            assert result["hero"]["next_paycheck_date"] is None
            # The still-due panel reads the SAME value, which is the point of
            # threading it: both are ``None`` together or neither is.
            assert result["still_due"]["next_period_start"] is None
            assert result["still_due"]["next_period_end"] is None

    def test_hero_staleness_fresh(self, app, seed_user, seed_periods, db):
        """A recently-updated anchor is NOT stale.

        Default staleness threshold is 14 days; an anchor updated 5 days
        ago is fresh (5 <= 14), so is_stale is False.
        """
        with app.app_context():
            _add_anchor_history(
                db.session, seed_user["account"],
                "1000.00", days_ago=5,
            )
            db.session.commit()

            result = dashboard_service.compute_pulse_section(
                dashboard_section(seed_user["user"].id),
            )
            assert result["hero"]["is_stale"] is False
            assert result["hero"]["last_updated_date"] is not None

    def test_hero_staleness_stale(self, app, seed_user, seed_periods, db):
        """An anchor older than the threshold IS stale.

        The seeded account asserts on the owner's bootstrap day, years
        before the 20-days-ago row appended here, so that row is the latest.
        20 > 14, so is_stale is True.

        *It used to clear the origination row first; the table is append-only
        since plan step X-f3c-2c and the row is dated years back rather than at
        NOW, so neither the act nor its reason survives.*
        """
        with app.app_context():
            account = seed_user["account"]
            _add_anchor_history(
                db.session, account, "1000.00", days_ago=20,
            )
            db.session.commit()

            result = dashboard_service.compute_pulse_section(
                dashboard_section(seed_user["user"].id),
            )
            assert result["hero"]["is_stale"] is True

    def test_hero_staleness_never_set(self, app, seed_user, seed_periods, db):
        """A never-set anchor IS stale, with a None last_updated_date.

        The Commit-3 invariant guarantees every account has an origination
        anchor row, so the truly-empty-history state hard-raises in the
        resolver and cannot reach the hero's balance call in production.
        The never-set staleness branch is therefore exercised one call below
        the hero: with the origination row deleted,
        ``cash_ledger.reconciled_through`` answers ``None`` and
        ``_anchor_is_stale`` treats that as stale (the user must set the
        balance).

        **It grades the PRODUCTION accessor since plan step X-f1c3a.**  It
        used to call ``_last_anchor_update_date``, a private wrapper with no
        production caller at all -- so the branch it pinned was reached by
        nothing, and the ``created_at``-keyed helper underneath it (ruling
        R-EP deleted that too) was never exercised on this path.
        """
        with app.app_context():
            # **Built rather than emptied** (plan step X-f3c-2c): an assertion
            # is append-only at the database tier, so an account that has
            # asserted nothing is one the assertion factory never touched.
            account = account_never_asserted(
                seed_user, db.session, name="Silent",
                opening_equity=Decimal("0.00"),
            )
            db.session.commit()

            last_observed = cash_ledger.reconciled_through(
                account.id,
            ).observed_day
            assert last_observed is None
            assert _pulse._anchor_is_stale(
                last_observed, seed_user["settings"], _TODAY,
            ) is True


# ── Chart: 13-point shape, degradation, threshold ──────────────────


#: The developer's own cadence, threaded into every ``_chart`` unit case.
#:
#: ``_chart`` took no cadence until plan step **R-F17** -- its window was the
#: hardcoded ``_CHART_HORIZON_PERIODS = 13``.  Passing the real value object
#: rather than a stand-in keeps these cases on the production derivation, and
#: at 14 days it answers the same 13 points they were written against.
_BIWEEKLY = PayCadence(FixedDays(14))


class TestPulseChart:
    """The projected end-balance chart series and threshold passthrough."""

    def _periods_and_balances(self, periods, per_period_balance="100.00"):
        """Build a forward DERIVED-period list and a flat end-balance map.

        The producer's forward set is ``balance_ctx.reported_periods()`` since
        pay-calendar plan step C2-f2e -- a window of
        :class:`~app.services.pay_calendar.DerivedPeriod`, not the ORM rows --
        so the helper it feeds is handed the same type production hands it,
        and the map is keyed by ``period_id`` as the seam's own maps are.
        """
        derived = list(period_window(periods))
        end_balances = {
            p.period_id: Decimal(per_period_balance) for p in derived
        }
        return derived, end_balances

    def test_the_chart_window_follows_the_owners_cadence(
        self, app, seed_user, db,
    ):
        """A WEEKLY owner gets 26 points, not 13 -- the same SIX MONTHS.

        Plan step **R-F17**, ledger row **F-17**.  The window was
        ``_CHART_HORIZON_PERIODS = 13`` while ``dashboard/_pulse.html``'s
        canvas announced "the next six months" to every screen reader beside
        it: at this cadence 13 periods is 91 days, so the chart plotted three
        months under a label saying six.  Both adversarial reviews of this step
        found it by re-grepping the ledger row's own predicate rather than the
        diff -- the row could not honestly close while it stood.

        26 weekly periods is 182 days, the same span the biweekly owner's 13
        cover, which is the whole point.
        """
        with app.app_context():
            periods = record_paydays_across_a_hole(
                user_id=seed_user["user"].id,
                first_payday=date(2026, 1, 2),
                num_periods=40,
                rhythm=rhythm_of(7),
            )
            db.session.commit()
            forward, balances = self._periods_and_balances(periods)

            chart = _pulse._chart(
                forward, balances, None, PayCadence(FixedDays(7)),
            )

            assert len(chart["points"]) == 26
            # 26 x 7 == 182 == 13 x 14: one span, two cadences.
            span = (
                chart["points"][-1]["end_date"] - periods[0].start_date
            ).days + 1
            assert span == 182

    def test_a_paycheck_longer_than_the_span_still_draws_one_point(
        self, app, seed_user, db,
    ):
        """A 300-day cadence reaches no paycheck inside six months: draw one.

        The same POLICY the mobile Plan tab keeps and the grid's range buttons
        do NOT: a chart on the dashboard must draw something, where a button
        may simply not be offered (ruling **R-R31**).
        """
        with app.app_context():
            periods = record_paydays_across_a_hole(
                user_id=seed_user["user"].id,
                first_payday=date(2026, 1, 2),
                num_periods=4,
                rhythm=rhythm_of(300),
            )
            db.session.commit()
            forward, balances = self._periods_and_balances(periods)

            assert PayCadence(FixedDays(300)).paychecks_within(6) == 0
            chart = _pulse._chart(
                forward, balances, None, PayCadence(FixedDays(300)),
            )

            assert len(chart["points"]) == 1

    def test_chart_caps_at_the_owners_paychecks_in_the_span(
        self, app, seed_user, db,
    ):
        """13 points at a BIWEEKLY cadence, even with more periods available.

        Generate 20 forward periods and assert the chart returns exactly 13 --
        which is how many paychecks fall inside
        :data:`~app.services.dashboard_service._pulse._CHART_HORIZON_MONTHS`
        for this owner, and exactly the ``_CHART_HORIZON_PERIODS = 13`` literal
        that stood here until plan step **R-F17**.  13 x 14 = 182 days, so the
        canvas's "next six months" aria-label is true for them.
        """
        with app.app_context():
            periods = record_paydays_across_a_hole(
                user_id=seed_user["user"].id,
                first_payday=date(2026, 1, 2),
                num_periods=20,
                rhythm=rhythm_of(14),
            )
            db.session.commit()
            forward, balances = self._periods_and_balances(periods)

            chart = _pulse._chart(forward, balances, None, _BIWEEKLY)
            assert len(chart["points"]) == 13
            # The points are the first 13 periods, in order.
            assert [pt["end_date"] for pt in chart["points"]] == [
                last_covered_day(p) for p in periods[:13]
            ]

    def test_chart_fewer_periods_degrades(self, app, seed_user, seed_periods, db):
        """With only 5 forward periods the chart returns 5 points, not 13.

        seed_periods[5..9] is 5 periods; the chart returns one point each
        (fewer-than-13 degradation), each carrying that period's end
        balance.
        """
        with app.app_context():
            forward = list(period_window(seed_periods[_CURRENT_IDX:]))
            assert len(forward) == 5
            balances = {p.period_id: Decimal("250.00") for p in forward}

            chart = _pulse._chart(forward, balances, None, _BIWEEKLY)
            assert len(chart["points"]) == 5
            assert all(
                pt["balance"] == Decimal("250.00") for pt in chart["points"]
            )

    def test_chart_threshold_passthrough(self, app, seed_user, seed_periods, db):
        """The user's low_balance_threshold flows through as a Decimal.

        seed_user's settings default low_balance_threshold to 500 (a
        whole-dollar integer column); the chart surfaces it as
        Decimal("500").
        """
        with app.app_context():
            forward = list(period_window(seed_periods[_CURRENT_IDX:]))
            balances = {p.period_id: Decimal("100.00") for p in forward}
            settings = seed_user["settings"]

            chart = _pulse._chart(
                forward, balances, settings, _BIWEEKLY,
)
            assert chart["low_balance_threshold"] == Decimal("500")

    def test_chart_threshold_tracks_configured_value(
        self, app, seed_user, seed_periods, db,
    ):
        """The chart threshold tracks the configured setting, not the default.

        Setting low_balance_threshold to 800 (a non-default whole-dollar
        integer) surfaces Decimal("800") on the chart -- the dashed line
        updates dynamically with the user's configured value.
        """
        with app.app_context():
            forward = list(period_window(seed_periods[_CURRENT_IDX:]))
            balances = {p.period_id: Decimal("100.00") for p in forward}
            settings = seed_user["settings"]
            settings.low_balance_threshold = 800
            db.session.commit()

            chart = _pulse._chart(
                forward, balances, settings, _BIWEEKLY,
)
            assert chart["low_balance_threshold"] == Decimal("800")

    def test_chart_threshold_none_without_settings(
        self, app, seed_user, seed_periods, db,
    ):
        """With no settings the threshold is None (no dashed line drawn)."""
        with app.app_context():
            forward = list(period_window(seed_periods[_CURRENT_IDX:]))
            balances = {p.period_id: Decimal("100.00") for p in forward}

            chart = _pulse._chart(forward, balances, None, _BIWEEKLY)
            assert chart["low_balance_threshold"] is None


# ── Trough: full-horizon minimum, beyond-chart dip, offset ──────────


class TestPulseTrough:
    """The lowest projected end balance over the full forward horizon."""

    def test_trough_all_positive(self, app, seed_user, seed_periods, db):
        """All-positive horizon -> the smallest positive end balance.

        Five forward periods (5..9) with balances 500, 400, 300, 350, 450.
        The minimum is 300 at period 7, offset 7 - 5 = 2.
        """
        with app.app_context():
            forward = list(period_window(seed_periods[_CURRENT_IDX:]))
            values = ["500.00", "400.00", "300.00", "350.00", "450.00"]
            balances = {p.period_id: Decimal(v) for p, v in zip(forward, values)}

            trough = _pulse._trough(
                forward, balances, forward[0],
            )
            assert trough["balance"] == Decimal("300.00")
            assert trough["end_date"] == last_covered_day(seed_periods[_CURRENT_IDX + 2])
            # offset = period_index(7) - current period_index(5) = 2.
            assert trough["offset"] == 2

    def test_trough_negative_beyond_chart_window(self, app, seed_user, db):
        """A negative dip BEYOND the 13-chart window is still found.

        Build a 15-period forward horizon, all balances positive EXCEPT
        the 15th (index 14 in the forward list), which is negative.  The
        chart caps at 13 points (all positive), so a chart-only scan would
        miss the dip; the trough scans the FULL horizon and catches it.
        The trough's offset is the dip period's index minus the current
        (forward[0]) period's index.
        """
        with app.app_context():
            periods = record_paydays_across_a_hole(
                user_id=seed_user["user"].id,
                first_payday=date(2026, 1, 2),
                num_periods=15,
                rhythm=rhythm_of(14),
            )
            db.session.commit()

            # Treat the whole generated run as the forward horizon and its
            # first period as the current (offset origin) -- this is a pure
            # _trough / _chart test, independent of today.
            forward = list(period_window(periods))
            current = forward[0]
            assert len(forward) == 15  # 15 forward periods; chart caps at 13.

            balances = {p.period_id: Decimal("500.00") for p in forward}
            dip_period = forward[14]  # the 15th forward period (beyond chart)
            balances[dip_period.period_id] = Decimal("-250.00")

            # The chart sees only the first 13 -- all positive.
            chart = _pulse._chart(forward, balances, None, _BIWEEKLY)
            assert len(chart["points"]) == 13
            assert all(pt["balance"] > Decimal("0") for pt in chart["points"])

            # The trough scans all 15 and finds the dip beyond the chart.
            trough = _pulse._trough(
                forward, balances, current,
            )
            assert trough["balance"] == Decimal("-250.00")
            assert trough["end_date"] == dip_period.end_date
            # offset = dip period_index - current period_index = 14.
            assert trough["offset"] == dip_period.period_index - current.period_index
            assert trough["offset"] == 14

    def test_trough_none_without_a_forward_period(
        self, app, seed_user, seed_periods, db,
    ):
        """An EMPTY forward window -> trough is None.

        **This case graded an empty end-balance MAP until C2-f2e's adversarial
        code review** (2026-08-18), and that state is now unconstructible:
        ``cash_balance_map`` is a TOTAL fold over ``reported_periods()`` and
        ``forward_periods`` is a slice of that same window, so every key is
        present by construction and the scan INDEXES rather than skipping. A
        test that builds a state production cannot reach grades the skip it
        depends on and nothing else -- which is why deleting the skip is what
        found it.

        What remains is the arm that CAN answer ``None``: no forward period at
        all. ``compute_pulse_section``'s own current-period guard refuses that
        owner before this helper is reached, so the arm is defensive -- and it
        is pinned rather than deleted because the helper is public to its
        module and its ``Returns`` block states it.
        """
        with app.app_context():
            current = list(period_window(seed_periods[_CURRENT_IDX:]))[0]
            trough = _pulse._trough([], {}, current)
            assert trough is None


# ── Peak: full-horizon maximum, beyond-chart rise, offset ───────────


class TestPulsePeak:
    """The highest projected end balance over the full forward horizon.

    The exact mirror of :class:`TestPulseTrough`: same full-horizon scan,
    same offset basis, same ``None`` degradation -- maximum instead of
    minimum.
    """

    def test_peak_all_positive(self, app, seed_user, seed_periods, db):
        """All-positive horizon -> the largest end balance at its offset.

        Five forward periods (5..9) with balances 300, 400, 500, 450, 350.
        The maximum is 500 at period 7, offset 7 - 5 = 2 -- a non-zero
        offset, proving the scan finds the true peak, not just the first
        point.
        """
        with app.app_context():
            forward = list(period_window(seed_periods[_CURRENT_IDX:]))
            values = ["300.00", "400.00", "500.00", "450.00", "350.00"]
            balances = {p.period_id: Decimal(v) for p, v in zip(forward, values)}

            peak = _pulse._peak(
                forward, balances, forward[0],
            )
            assert peak["balance"] == Decimal("500.00")
            assert peak["end_date"] == last_covered_day(seed_periods[_CURRENT_IDX + 2])
            # offset = period_index(7) - current period_index(5) = 2.
            assert peak["offset"] == 2

    def test_peak_equals_hero_when_first_period_is_max(
        self, app, seed_user, seed_periods, db,
    ):
        """When the current period is the maximum, the peak sits at offset 0.

        Five forward periods (5..9) with balances 500, 400, 300, 350, 450.
        The maximum is 500 at period 5 (the current period), offset
        5 - 5 = 0 -- the peak coincides with the hero / chart's first
        point, so the "highest point ahead" stat deep-links the current
        period.
        """
        with app.app_context():
            forward = list(period_window(seed_periods[_CURRENT_IDX:]))
            values = ["500.00", "400.00", "300.00", "350.00", "450.00"]
            balances = {p.period_id: Decimal(v) for p, v in zip(forward, values)}

            peak = _pulse._peak(
                forward, balances, forward[0],
            )
            assert peak["balance"] == Decimal("500.00")
            assert peak["end_date"] == last_covered_day(seed_periods[_CURRENT_IDX])
            # offset = period_index(5) - current period_index(5) = 0.
            assert peak["offset"] == 0

    def test_peak_beyond_chart_window(self, app, seed_user, db):
        """A peak BEYOND the 13-chart window is still found (full scan).

        Build a 15-period forward horizon, all balances equal EXCEPT the
        15th (index 14 in the forward list), which is the highest.  The
        chart caps at 13 points (all equal, none the peak), so a chart-only
        scan would miss the rise; the peak scans the FULL horizon and
        catches it.  The peak's offset is the rise period's index minus the
        current (forward[0]) period's index.
        """
        with app.app_context():
            periods = record_paydays_across_a_hole(
                user_id=seed_user["user"].id,
                first_payday=date(2026, 1, 2),
                num_periods=15,
                rhythm=rhythm_of(14),
            )
            db.session.commit()

            # Treat the whole generated run as the forward horizon and its
            # first period as the current (offset origin) -- this is a pure
            # _peak / _chart test, independent of today.
            forward = list(period_window(periods))
            current = forward[0]
            assert len(forward) == 15  # 15 forward periods; chart caps at 13.

            balances = {p.period_id: Decimal("500.00") for p in forward}
            rise_period = forward[14]  # the 15th forward period (beyond chart)
            balances[rise_period.period_id] = Decimal("1250.00")

            # The chart sees only the first 13 -- all 500.00, none the peak.
            chart = _pulse._chart(forward, balances, None, _BIWEEKLY)
            assert len(chart["points"]) == 13
            assert all(
                pt["balance"] == Decimal("500.00") for pt in chart["points"]
            )

            # The peak scans all 15 and finds the rise beyond the chart.
            peak = _pulse._peak(
                forward, balances, current,
            )
            assert peak["balance"] == Decimal("1250.00")
            assert peak["end_date"] == rise_period.end_date
            # offset = rise period_index - current period_index = 14.
            assert peak["offset"] == rise_period.period_index - current.period_index
            assert peak["offset"] == 14

    def test_peak_none_without_a_forward_period(
        self, app, seed_user, seed_periods, db,
    ):
        """An EMPTY forward window -> peak is None.

        **This case graded an empty end-balance MAP until C2-f2e's adversarial
        code review** (2026-08-18), and that state is now unconstructible:
        ``cash_balance_map`` is a TOTAL fold over ``reported_periods()`` and
        ``forward_periods`` is a slice of that same window, so every key is
        present by construction and the scan INDEXES rather than skipping. A
        test that builds a state production cannot reach grades the skip it
        depends on and nothing else -- which is why deleting the skip is what
        found it.

        What remains is the arm that CAN answer ``None``: no forward period at
        all. ``compute_pulse_section``'s own current-period guard refuses that
        owner before this helper is reached, so the arm is defensive -- and it
        is pinned rather than deleted because the helper is public to its
        module and its ``Returns`` block states it.
        """
        with app.app_context():
            current = list(period_window(seed_periods[_CURRENT_IDX:]))[0]
            peak = _pulse._peak([], {}, current)
            assert peak is None

    def test_peak_wired_through_compute_pulse_section(
        self, app, seed_user, seed_periods, db,
    ):
        """compute_pulse_section returns a populated peak from the real walk.

        A $1,200.00 projected income in the current period (5) lifts that
        period's projected end balance to 1,000.00 + 1,200.00 = 2,200.00,
        the highest point in the all-else-flat horizon (the later periods
        carry the same 2,200.00 balance forward, so the peak is the FIRST
        such period -- the current one, offset 0).  This proves the
        producer wires _peak through, not just the helper in
        isolation.
        """
        with app.app_context():
            current = seed_periods[_CURRENT_IDX]
            one_off_row_of(
                current,
                name="Paycheck",
                amount=Decimal("1200.00"),
                user_id=current.user_id,
                account_id=seed_user["account"].id,
                scenario_id=seed_user["scenario"].id,
                transaction_type_id=ref_cache.txn_type_id(TxnTypeEnum.INCOME),
            )
            db.session.commit()

            result = dashboard_service.compute_pulse_section(
                dashboard_section(seed_user["user"].id),
            )
            peak = result["peak"]
            # 1,000.00 anchor + 1,200.00 income = 2,200.00 end balance.
            assert peak["balance"] == Decimal("2200.00")
            assert peak["end_date"] == last_covered_day(current)
            # The current period is the first to reach the high; offset 0.
            assert peak["offset"] == 0


# ── Still due: locked B4 bases, current vs next separation ──────────


class TestPulseStillDue:
    """Still-due totals on the locked B4 bases (data-value pass)."""

    def test_untracked_row_contributes_effective_amount(
        self, app, seed_user, seed_periods, db,
    ):
        """An untracked projected expense contributes its effective amount.

        One $300.00 projected expense in the current period; still-due for
        the current period is $300.00, next period $0.00.
        """
        with app.app_context():
            _add_expense(
                db.session, seed_user, seed_periods[_CURRENT_IDX],
                "Rent", "300.00", due_date=date(2026, 3, 22),
            )
            db.session.commit()

            result = dashboard_service.compute_pulse_section(
                dashboard_section(seed_user["user"].id),
            )
            still = result["still_due"]
            assert still["current_period"] == Decimal("300.00")
            assert still["next_period"] == Decimal("0.00")

    def test_tracked_partial_entries_contributes_remaining(
        self, app, seed_user, seed_periods, db,
    ):
        """A tracked row with partial entries contributes remaining-after-entries.

        Envelope estimated $200.00, entries summing $80.00:
            remaining = 200.00 - 80.00 = 120.00 (floored at 0 -> 120.00).
        Still-due for the current period is $120.00.
        """
        with app.app_context():
            txn = _add_tracked_expense(
                db.session, seed_user, seed_periods[_CURRENT_IDX],
                "Groceries", "200.00",
            )
            _add_entry(db.session, seed_user, txn, "50.00", date(2026, 3, 18))
            _add_entry(db.session, seed_user, txn, "30.00", date(2026, 3, 19))
            db.session.commit()

            result = dashboard_service.compute_pulse_section(
                dashboard_section(seed_user["user"].id),
            )
            assert result["still_due"]["current_period"] == Decimal("120.00")

    def test_tracked_over_budget_floors_at_zero(
        self, app, seed_user, seed_periods, db,
    ):
        """An over-budget envelope contributes 0, never a negative.

        Envelope estimated $100.00, entries summing $130.00:
            remaining = 100.00 - 130.00 = -30.00 -> floored to 0.00.
        Still-due for the current period is $0.00 (not -$30.00).
        """
        with app.app_context():
            txn = _add_tracked_expense(
                db.session, seed_user, seed_periods[_CURRENT_IDX],
                "Groceries", "100.00",
            )
            _add_entry(db.session, seed_user, txn, "70.00", date(2026, 3, 18))
            _add_entry(db.session, seed_user, txn, "60.00", date(2026, 3, 19))
            db.session.commit()

            result = dashboard_service.compute_pulse_section(
                dashboard_section(seed_user["user"].id),
            )
            assert result["still_due"]["current_period"] == Decimal("0.00")

    def test_a_REFUNDED_envelope_contributes_MORE_than_its_budget(
        self, app, seed_user, seed_periods, db,
    ):
        """The floor is BELOW, and there is deliberately none above.

        Developer ruling 2026-09-01, ruling **bank_import:R-II**, plan step
        ``bank_import:X-gj-2b-3``.  Envelope `$100.00`, one `-$50.00` refund:

            remaining = 100.00 - (-50.00) = 150.00, not floored.

        Still-due reads `$150.00` against a `$100.00` plan, and that is the
        ruled NET basis: the plan is `$100.00` of net spending and `-$50.00` of
        it has happened, so `$150.00` may still be recorded.  Capping it at the
        budget was refused with these numbers, because the dashboard would then
        disagree with the reservation ``cash_ledger`` holds for the same row.

        Asserted BESIDE the over-budget case above, which is the one that
        makes the floor real: a producer with no floor at all would fail that
        one and a producer clamped to `[0, budget]` would fail this one.
        """
        with app.app_context():
            txn = _add_tracked_expense(
                db.session, seed_user, seed_periods[_CURRENT_IDX],
                "Groceries", "100.00",
            )
            _add_entry(
                db.session, seed_user, txn, "-50.00", date(2026, 3, 18),
            )
            db.session.commit()

            result = dashboard_service.compute_pulse_section(
                dashboard_section(seed_user["user"].id),
            )
            assert result["still_due"]["current_period"] == Decimal("150.00")

    def test_tracked_no_entries_contributes_full_estimate(
        self, app, seed_user, seed_periods, db,
    ):
        """A tracked row with no entries contributes its full estimate.

        Envelope estimated $150.00, no entries:
            remaining = 150.00 - 0 = 150.00.
        Still-due for the current period is $150.00.
        """
        with app.app_context():
            _add_tracked_expense(
                db.session, seed_user, seed_periods[_CURRENT_IDX],
                "Groceries", "150.00",
            )
            db.session.commit()

            result = dashboard_service.compute_pulse_section(
                dashboard_section(seed_user["user"].id),
            )
            assert result["still_due"]["current_period"] == Decimal("150.00")

    def test_transfer_out_shadow_included(
        self, app, seed_user, seed_periods, db,
    ):
        """A projected transfer out of checking IS counted in still-due (B4b).

        Build a REAL projected transfer (through the transfer service) from
        checking to savings for $400.00 in the current period.  Its from-side
        leg lands on checking as a projected outflow and contributes its
        $400.00 to still-due.
        """
        with app.app_context():
            savings_type = (
                db.session.query(AccountType).filter_by(name="Savings").one()
            )
            savings = account_service.create_account(
                account_service.AccountSpec(
                    user_id=seed_user["user"].id,
                    account_type_id=savings_type.id,
                    name="Sweep Target",
                    anchor_balance=Decimal("0.00"),
                ),
            )
            db.session.add(savings)
            db.session.flush()

            transfer_service.create_transfer(
                transfer_service.TransferSpec(
                    user_id=seed_user["user"].id,
                    from_account_id=seed_user["account"].id,
                    to_account_id=savings.id,
                    pay_period_id=seed_periods[_CURRENT_IDX].id,
                    scenario_id=seed_user["scenario"].id,
                    amount_ownership=AmountOwnership.own(Decimal("400.00")),
                    status_id=ref_cache.status_id(StatusEnum.PROJECTED),
                    category_id=None,
                    due_date=date(2026, 3, 24),
                ),
            )
            db.session.commit()

            result = dashboard_service.compute_pulse_section(
                dashboard_section(seed_user["user"].id),
            )
            # The transfer-out shadow is the only obligation: $400.00.
            assert result["still_due"]["current_period"] == Decimal("400.00")

    def test_current_and_next_period_separated(
        self, app, seed_user, seed_periods, db,
    ):
        """Still-due totals split correctly between current and next period.

        $300.00 in the current period (5) and $175.00 in the next period
        (6).  current_period total = 300.00, next_period total = 175.00.
        """
        with app.app_context():
            _add_expense(
                db.session, seed_user, seed_periods[_CURRENT_IDX],
                "This period bill", "300.00", due_date=date(2026, 3, 22),
            )
            _add_expense(
                db.session, seed_user, seed_periods[_CURRENT_IDX + 1],
                "Next period bill", "175.00", due_date=date(2026, 3, 30),
            )
            db.session.commit()

            result = dashboard_service.compute_pulse_section(
                dashboard_section(seed_user["user"].id),
            )
            still = result["still_due"]
            assert still["current_period"] == Decimal("300.00")
            assert still["next_period"] == Decimal("175.00")
            # The next period's date range accompanies its total so the
            # template can label "Next period (Mar 27 - Apr 9): $X".
            assert still["next_period_start"] == seed_periods[_CURRENT_IDX + 1].start_date
            assert still["next_period_start"] == date(2026, 3, 27)
            assert still["next_period_end"] == last_covered_day(seed_periods[_CURRENT_IDX + 1])
            assert still["next_period_end"] == date(2026, 4, 9)

    def test_still_due_next_period_dates_none_when_no_next(
        self, app, seed_user, db,
    ):
        """No next period -> next_period total 0.00 and date range None/None.

        Generate exactly 6 periods (indices 0..5) so the period containing
        the frozen 2026-03-20 (index 5) is the LAST one: there is no next
        period, so the still-due block reports next_period 0.00 and both
        next-period dates None (the template renders its generate-periods
        fallback line then).
        """
        with app.app_context():
            record_paydays_across_a_hole(
                user_id=seed_user["user"].id,
                first_payday=date(2026, 1, 2),
                num_periods=6,
                rhythm=rhythm_of(14),
            )
            db.session.commit()

            result = dashboard_service.compute_pulse_section(
                dashboard_section(seed_user["user"].id),
            )
            still = result["still_due"]
            assert still["next_period"] == Decimal("0.00")
            assert still["next_period_start"] is None
            assert still["next_period_end"] is None

    def test_settled_row_excluded_from_still_due(
        self, app, seed_user, seed_periods, db,
    ):
        """A settled (done) expense is not still due -- it is already paid.

        One $500.00 DONE expense in the current period contributes nothing
        (the still-due query is Projected-only).
        """
        with app.app_context():
            _add_expense(
                db.session, seed_user, seed_periods[_CURRENT_IDX],
                "Already paid", "500.00",
                status_enum=StatusEnum.DONE, due_date=date(2026, 3, 15),
            )
            db.session.commit()

            result = dashboard_service.compute_pulse_section(
                dashboard_section(seed_user["user"].id),
            )
            assert result["still_due"]["current_period"] == Decimal("0.00")


# ── Street: period day-span and today's offset, shared basis ────────


class TestTheBillsAreThePaychecksAcrossTheSet:
    """The upcoming bills read the owner's cash-flow set (plan step CC-4-3).

    Developer ruling ``credit_card:R-CC16``: a plan item's ``account_id`` is
    the account its money is expected to move through, so the phone bill
    that is always paid by card is a row ON the card -- and a dashboard
    that read one account's rows dropped it from what the paycheck still
    owes.  The bills are the set's now, through the one clause
    (:func:`~app.services.cash_flow_set.own_rows_clause` for the rows and
    :func:`~app.services.cash_flow_set.leg_accounts_shown` for a transfer's leg,
    since leaf ``balance:X-bi-6-1b``; it was ``paycheck_rows_clause``); the hero,
    the chart and the trough stay the BALANCE line's, the primary.  Every
    case below plants a card, because an owner with none is a set of one
    and cannot tell the clause from the filter it replaced.
    """

    @staticmethod
    def _card(seed_user, db_session):
        """Create an active Credit Card account for *seed_user*."""
        return create_account_of_type(
            seed_user, db_session, "Credit Card", "Rewards Card",
            anchor_balance=Decimal("-500.00"),
        )

    @staticmethod
    def _expense_on(seed_user, period, account, name, amount, due_date):
        """Place a projected one-off EXPENSE row on *account* in *period*."""
        return one_off_row_of(
            period, name=name, amount=Decimal(amount),
            user_id=seed_user["user"].id, account_id=account.id,
            scenario_id=seed_user["scenario"].id,
            transaction_type_id=ref_cache.txn_type_id(TxnTypeEnum.EXPENSE),
            due_date=due_date,
        )

    def test_a_bill_on_the_card_is_a_bill_the_paycheck_owes(
        self, app, seed_user, seed_periods, db,
    ):
        """A projected expense ON the card is due-soon and still-due.

        Rent $300.00 on checking due 03-22 and the phone bill $45.00 on the
        card due 03-25, both in the current period: still-due for the
        current period is 300.00 + 45.00 = $345.00, the due-soon list holds
        both rows in due-date order, and the hero is still checking's --
        the balance line is one account's whatever the rows are.
        """
        with app.app_context():
            card = self._card(seed_user, db.session)
            period = seed_periods[_CURRENT_IDX]
            self._expense_on(
                seed_user, period, seed_user["account"], "Rent", "300.00",
                date(2026, 3, 22),
            )
            self._expense_on(
                seed_user, period, card, "Phone", "45.00", date(2026, 3, 25),
            )
            db.session.commit()

            section = dashboard_section(seed_user["user"].id)
            assert section.cash_flow.member_ids == (
                seed_user["account"].id, card.id,
            )
            assert section.account.id == seed_user["account"].id

            result = dashboard_service.compute_pulse_section(section)
            assert result["still_due"]["current_period"] == Decimal("345.00")
            assert [b["name"] for b in result["due_soon"]] == ["Rent", "Phone"]
            assert result["hero"]["account_id"] == seed_user["account"].id

    def test_a_transfer_between_members_is_one_bill_from_the_balance_lines_side(
        self, app, seed_user, seed_periods, db,
    ):
        """Ruling R-CC23 on the bills: the far leg is not a bill.

        A $165.00 checking -> card payment is ONE obligation, checking's
        expense leg (the Gate B4b transfer-out rule); the card's leg is its
        income leg and the far leg, twice excluded.  A $40.00 card ->
        checking transfer is NO bill: checking's leg is income and the card's
        expense leg is the far leg -- the second view of an act the balance
        line's side already shows.  Still-due is $165.00 and the due-soon
        list holds exactly the one transfer bill.  Without the far-leg arm the
        card's $40.00 leg would be a bill and the total $205.00.

        The transfer doors refuse a transfer OUT of a card (plan step CC-10),
        so the $40.00 card -> checking transfer is the representable-but-
        refused shape and its state is PLANTED, the way the legacy-source
        tests plant theirs: created from a helper Savings account INTO
        checking (allowed), then its source re-pointed onto the card by
        assignment, past the door.  The re-point moved the transfer's expense
        shadow row too until plan step balance:X-bi-6-4d-2 made a transfer
        one row (ruling R-BAL258); the transfer is the whole plant now
        (ruling R-BAL167 class 1).  The savings account is not a member of
        the set.  The plant carries its own tell, the transfer's endpoints,
        because an UN-planted fixture (helper -> checking, never re-pointed)
        reads the same $165.00 with or without the far-leg arm and would
        grade nothing.
        """
        with app.app_context():
            card = self._card(seed_user, db.session)
            period = seed_periods[_CURRENT_IDX]
            create_transfer(
                seed_user, db.session, seed_user["account"], card, period,
                amount=Decimal("165.00"), due_date=date(2026, 3, 24),
            )
            helper = create_savings_account(
                seed_user, db.session, "Savings", Decimal("0.00"),
            )
            out_of_card = create_transfer(
                seed_user, db.session, helper, seed_user["account"], period,
                amount=Decimal("40.00"), due_date=date(2026, 3, 21),
            )
            db.session.flush()
            out_of_card.from_account = card
            db.session.commit()
            assert (out_of_card.from_account_id, out_of_card.to_account_id) == (
                card.id, seed_user["account"].id,
            )

            result = dashboard_service.compute_pulse_section(
                dashboard_section(seed_user["user"].id),
            )
            assert result["still_due"]["current_period"] == Decimal("165.00")
            bills = result["due_soon"]
            assert len(bills) == 1
            assert bills[0]["is_transfer"] is True
            assert bills[0]["amount"] == Decimal("165.00")

    def test_a_set_of_one_is_the_old_filter(
        self, app, seed_user, seed_periods, db,
    ):
        """With no card the bills are checking's alone, as before the step.

        The byte-identity claim in a unit test's shape: a $300.00 checking
        row and a $75.00 row on a SAVINGS account (an owned cash-flow
        account that is not a member) -- still-due reads $300.00 and the
        savings row is not a bill.
        """
        with app.app_context():
            savings = create_savings_account(
                seed_user, db.session, "Savings", Decimal("500.00"),
            )
            period = seed_periods[_CURRENT_IDX]
            self._expense_on(
                seed_user, period, seed_user["account"], "Rent", "300.00",
                date(2026, 3, 22),
            )
            self._expense_on(
                seed_user, period, savings, "Fee", "75.00", date(2026, 3, 23),
            )
            db.session.commit()

            section = dashboard_section(seed_user["user"].id)
            assert section.cash_flow.member_ids == (seed_user["account"].id,)

            result = dashboard_service.compute_pulse_section(section)
            assert result["still_due"]["current_period"] == Decimal("300.00")
            assert [b["name"] for b in result["due_soon"]] == ["Rent"]


class TestTheBillsAreLegsReadOffTheParent:
    """The bills draw a transfer as a LEG of its parent (leaf X-bi-6-1b, R-BAL86).

    ``_query_unpaid_expense_rows`` answers the set's OWN rows through
    ``own_rows_clause`` -- no shadow row -- beside the EXPENSE leg of every
    projected transfer the set touches, read off ``budget.transfers``; the
    still-due total and the due-soon list price both through one map keyed
    by ``cell_key``.  The money cases above (B4b, R-CC23) already grade what
    a transfer is WORTH here; these grade what it IS.
    """

    @staticmethod
    def _expense_on(seed_user, period, account, name, amount, due_date):
        """Place a projected one-off EXPENSE row on *account* in *period*."""
        return one_off_row_of(
            period, name=name, amount=Decimal(amount),
            user_id=seed_user["user"].id, account_id=account.id,
            scenario_id=seed_user["scenario"].id,
            transaction_type_id=ref_cache.txn_type_id(TxnTypeEnum.EXPENSE),
            due_date=due_date,
        )

    def test_the_loader_answers_own_rows_and_expense_legs_and_no_shadow(
        self, app, seed_user, seed_periods, db,
    ):
        with app.app_context():
            checking = seed_user["account"]
            period = seed_periods[_CURRENT_IDX]
            savings = create_savings_account(
                seed_user, db.session, "Sweep Target", Decimal("0.00"),
            )
            self._expense_on(
                seed_user, period, checking, "Rent", "300.00", date(2026, 3, 22),
            )
            transfer = create_transfer(
                seed_user, db.session, checking, savings, period,
                amount=Decimal("400.00"), due_date=date(2026, 3, 24),
            )
            db.session.commit()
            section = dashboard_section(seed_user["user"].id)

            unpaid = _bills._query_unpaid_expense_rows(  # pylint: disable=protected-access
                section.cash_flow, seed_user["scenario"].id, [period.id],
            )

            # "No shadow" is the database's since plan step
            # balance:X-bi-6-4d-2: a row naming a transfer is refused at the
            # write (ruling R-BAL258), so a per-row ``transfer_id is None``
            # check here passed with ``own_rows_clause``'s ``IS NULL`` term
            # deleted (measured 10-15) and is gone.
            assert [row.name for row in unpaid.rows] == ["Rent"]
            assert [
                (leg.transfer.id, leg.account_id, leg.is_expense)
                for leg in unpaid.legs
            ] == [(transfer.id, checking.id, True)]
            assert unpaid.items == [*unpaid.rows, *unpaid.legs]

    def test_a_leg_bill_is_keyed_by_the_pair_and_named_from_its_endpoints(
        self, app, seed_user, seed_periods, db,
    ):
        with app.app_context():
            checking = seed_user["account"]
            period = seed_periods[_CURRENT_IDX]
            savings = create_savings_account(
                seed_user, db.session, "Sweep Target", Decimal("0.00"),
            )
            transfer = create_transfer(
                seed_user, db.session, checking, savings, period,
                amount=Decimal("400.00"), due_date=date(2026, 3, 24),
            )
            db.session.commit()

            result = dashboard_service.compute_pulse_section(
                dashboard_section(seed_user["user"].id),
            )

            assert result["still_due"]["current_period"] == Decimal("400.00")
            [bill] = result["due_soon"]
            assert bill["id"] == (transfer.id, checking.id)
            assert bill["is_transfer"] is True
            assert bill["name"] == "Transfer to Sweep Target"
            assert bill["amount"] == Decimal("400.00")
            assert bill["amount_base"] is None
            assert bill["is_tracked"] is False
            assert bill["days_until_due"] == (date(2026, 3, 24) - _TODAY).days

    def test_a_soft_deleted_transfer_is_no_bill(
        self, app, seed_user, seed_periods, db,
    ):
        """The loader's ``Transfer.is_deleted`` term, graded on the one reader
        with no Python re-check behind it (the review of leaf X-bi-6-1b):
        a soft-deleted projected transfer draws no leg and moves no total."""
        with app.app_context():
            checking = seed_user["account"]
            period = seed_periods[_CURRENT_IDX]
            savings = create_savings_account(
                seed_user, db.session, "Sweep Target", Decimal("0.00"),
            )
            deleted = create_transfer(
                seed_user, db.session, checking, savings, period,
                amount=Decimal("400.00"), due_date=date(2026, 3, 24),
            )
            db.session.commit()
            transfer_service.delete_transfer(
                deleted.id, seed_user["user"].id, soft=True,
            )
            db.session.commit()
            section = dashboard_section(seed_user["user"].id)

            unpaid = _bills._query_unpaid_expense_rows(  # pylint: disable=protected-access
                section.cash_flow, seed_user["scenario"].id, [period.id],
            )
            assert unpaid.legs == []
            result = dashboard_service.compute_pulse_section(section)
            assert result["still_due"]["current_period"] == Decimal("0.00")
            assert result["due_soon"] == []

    def test_a_transfer_into_the_set_is_no_bill(
        self, app, seed_user, seed_periods, db,
    ):
        """The set shows a savings -> checking transfer from checking's side,
        where it is an INCOME leg -- money arriving, not a bill -- so the
        loader draws no leg for it and the totals ignore it, as the income
        shadow row's type did."""
        with app.app_context():
            checking = seed_user["account"]
            period = seed_periods[_CURRENT_IDX]
            savings = create_savings_account(
                seed_user, db.session, "Savings", Decimal("900.00"),
            )
            create_transfer(
                seed_user, db.session, savings, checking, period,
                amount=Decimal("250.00"), due_date=date(2026, 3, 24),
            )
            db.session.commit()
            section = dashboard_section(seed_user["user"].id)

            unpaid = _bills._query_unpaid_expense_rows(  # pylint: disable=protected-access
                section.cash_flow, seed_user["scenario"].id, [period.id],
            )
            assert unpaid.legs == [] and unpaid.rows == []
            result = dashboard_service.compute_pulse_section(section)
            assert result["still_due"]["current_period"] == Decimal("0.00")
            assert result["due_soon"] == []


class TestPulseStreet:
    """The street band's day-span and today's offset within the period.

    Basis (shared with the due-soon rows' ``day_offset``): the period
    start is day 0, so an event due on the start sits at 0, the period end
    sits at ``days_total``, and ``today_offset`` is days since the start.
    """

    def test_street_days_total_and_today_offset(
        self, app, seed_user, seed_periods, db,
    ):
        """days_total spans the period; today_offset is days since the start.

        Current period 5: start 2026-03-13, end 2026-03-26; frozen today
        2026-03-20.
            days_total   = (2026-03-26 - 2026-03-13).days = 13.
            today_offset = (2026-03-20 - 2026-03-13).days = 7.
        """
        with app.app_context():
            result = dashboard_service.compute_pulse_section(
                dashboard_section(seed_user["user"].id),
            )
            street = result["street"]
            assert street["days_total"] == 13
            assert street["today_offset"] == 7

    def test_street_shares_due_soon_basis(
        self, app, seed_user, seed_periods, db,
    ):
        """A due-soon row's day_offset shares the street's day-0 basis.

        A bill due on the period END date sits at day_offset == days_total
        (the period-end station's position), and a bill due on the period
        START sits at day_offset 0 -- proving the three numbers (start,
        today, end) ride one axis so the band's percentage math lines up.
        """
        with app.app_context():
            current = seed_periods[_CURRENT_IDX]
            _add_expense(
                db.session, seed_user, current,
                "Due on end", "10.00", due_date=last_covered_day(current),
            )
            _add_expense(
                db.session, seed_user, current,
                "Due on start", "20.00", due_date=current.start_date,
            )
            db.session.commit()

            result = dashboard_service.compute_pulse_section(
                dashboard_section(seed_user["user"].id),
            )
            street = result["street"]
            offsets = {
                b["name"]: b["day_offset"] for b in result["due_soon"]
            }
            # Period-start event sits at day 0.
            assert offsets["Due on start"] == 0
            # Period-end event sits at days_total -- the period-end station.
            assert offsets["Due on end"] == street["days_total"]


# ── Due soon: current-period rows with day offset + undated flag ────


class TestPulseDueSoon:
    """The current period's due-soon rows for the street / mobile list."""

    def test_due_soon_only_current_period(
        self, app, seed_user, seed_periods, db,
    ):
        """Only the current period's unpaid rows appear (next period excluded).

        A bill in period 5 (current) and a bill in period 6 (next): only
        the current-period bill is in due_soon.
        """
        with app.app_context():
            _add_expense(
                db.session, seed_user, seed_periods[_CURRENT_IDX],
                "Current bill", "100.00", due_date=date(2026, 3, 22),
            )
            _add_expense(
                db.session, seed_user, seed_periods[_CURRENT_IDX + 1],
                "Next bill", "200.00", due_date=date(2026, 3, 30),
            )
            db.session.commit()

            result = dashboard_service.compute_pulse_section(
                dashboard_section(seed_user["user"].id),
            )
            names = [b["name"] for b in result["due_soon"]]
            assert names == ["Current bill"]

    def test_due_soon_day_offset_and_undated(
        self, app, seed_user, seed_periods, db,
    ):
        """Dated rows carry day_offset from period start; undated flagged.

        Current period 5 starts 2026-03-13.  A bill due 2026-03-22 has
        day_offset = (03-22 - 03-13).days = 9.  An undated transfer-out
        leg carries day_offset None and undated True, and sorts AFTER
        dated rows.
        """
        with app.app_context():
            _add_expense(
                db.session, seed_user, seed_periods[_CURRENT_IDX],
                "Dated bill", "100.00", due_date=date(2026, 3, 22),
            )
            _undated_transfer_out(
                db.session, seed_user, seed_periods[_CURRENT_IDX],
                "Undated jar", "50.00",
            )
            db.session.commit()

            result = dashboard_service.compute_pulse_section(
                dashboard_section(seed_user["user"].id),
            )
            due_soon = result["due_soon"]
            # Dated first (chronological), undated last.
            assert [b["name"] for b in due_soon] == [
                "Dated bill", "Transfer to Undated jar",
            ]

            dated = due_soon[0]
            assert dated["undated"] is False
            # (2026-03-22 - 2026-03-13).days = 9.
            assert dated["day_offset"] == 9

            undated = due_soon[1]
            assert undated["undated"] is True
            assert undated["day_offset"] is None

    def test_due_soon_sorted_by_due_date(
        self, app, seed_user, seed_periods, db,
    ):
        """Dated due-soon rows sort by due_date ascending."""
        with app.app_context():
            _add_expense(
                db.session, seed_user, seed_periods[_CURRENT_IDX],
                "Late", "100.00", due_date=date(2026, 3, 25),
            )
            _add_expense(
                db.session, seed_user, seed_periods[_CURRENT_IDX],
                "Early", "100.00", due_date=date(2026, 3, 15),
            )
            db.session.commit()

            result = dashboard_service.compute_pulse_section(
                dashboard_section(seed_user["user"].id),
            )
            assert [b["name"] for b in result["due_soon"]] == ["Early", "Late"]


# ── Due-soon stations: per-day grouping for the street axis ─────────


class TestPulseDueSoonStations:
    """The dated due-soon rows grouped into one station per calendar day.

    Bills sharing a due_date share a day_offset and so must collapse into a
    single street station (one dot, a stacked label) instead of overlapping
    on one point.  The visible cap is 3: a station shows up to three bills,
    then reports the rest as ``extra_count`` for a "+N more" line.
    """

    def test_same_day_bills_collapse_into_one_station(
        self, app, seed_user, seed_periods, db,
    ):
        """Four bills on one due date -> one station, three shown, one extra.

        Current period 5 starts 2026-03-13.  Four bills due 2026-03-15 share
        day_offset = (03-15 - 03-13).days = 2.  With the cap at 3, the
        station shows the first three by name and folds the fourth into
        extra_count.
        """
        with app.app_context():
            for name in ("A bill", "B bill", "C bill", "D bill"):
                _add_expense(
                    db.session, seed_user, seed_periods[_CURRENT_IDX],
                    name, "10.00", due_date=date(2026, 3, 15),
                )
            db.session.commit()

            result = dashboard_service.compute_pulse_section(
                dashboard_section(seed_user["user"].id),
            )
            stations = result["due_soon_stations"]
            assert len(stations) == 1
            station = stations[0]
            assert station["day_offset"] == 2  # (03-15 - 03-13).days
            assert station["count"] == 4
            # Cap is 3: first three by name shown, fourth folded away.
            assert [b["name"] for b in station["visible_items"]] == [
                "A bill", "B bill", "C bill",
            ]
            assert station["extra_count"] == 1  # 4 - 3

    def test_distinct_days_form_separate_axis_ordered_stations(
        self, app, seed_user, seed_periods, db,
    ):
        """Bills on different due dates make one station each, in axis order.

        A bill due 2026-03-22 (day_offset 9) and one due 2026-03-15
        (day_offset 2) yield two stations ordered by day_offset [2, 9].
        """
        with app.app_context():
            _add_expense(
                db.session, seed_user, seed_periods[_CURRENT_IDX],
                "Later", "10.00", due_date=date(2026, 3, 22),
            )
            _add_expense(
                db.session, seed_user, seed_periods[_CURRENT_IDX],
                "Earlier", "10.00", due_date=date(2026, 3, 15),
            )
            db.session.commit()

            result = dashboard_service.compute_pulse_section(
                dashboard_section(seed_user["user"].id),
            )
            stations = result["due_soon_stations"]
            # day_offset 2 = (03-15 - 03-13); day_offset 9 = (03-22 - 03-13).
            assert [s["day_offset"] for s in stations] == [2, 9]
            assert [s["count"] for s in stations] == [1, 1]
            assert all(s["extra_count"] == 0 for s in stations)
            assert stations[0]["visible_items"][0]["name"] == "Earlier"
            assert stations[1]["visible_items"][0]["name"] == "Later"

    def test_single_bill_station_has_no_overflow(
        self, app, seed_user, seed_periods, db,
    ):
        """One dated bill -> a one-item station with extra_count 0."""
        with app.app_context():
            _add_expense(
                db.session, seed_user, seed_periods[_CURRENT_IDX],
                "Solo", "10.00", due_date=date(2026, 3, 18),
            )
            db.session.commit()

            result = dashboard_service.compute_pulse_section(
                dashboard_section(seed_user["user"].id),
            )
            stations = result["due_soon_stations"]
            assert len(stations) == 1
            assert stations[0]["count"] == 1
            assert len(stations[0]["visible_items"]) == 1
            assert stations[0]["extra_count"] == 0

    def test_station_carries_shared_days_until_due(
        self, app, seed_user, seed_periods, db,
    ):
        """The station's days_until_due is the bills' shared value.

        Two bills due 2026-03-22 with frozen today 2026-03-20 each have
        days_until_due = (03-22 - 03-20).days = 2; the station reports that
        single value (it drives the dot's overdue/soon/normal state).
        """
        with app.app_context():
            for name in ("First", "Second"):
                _add_expense(
                    db.session, seed_user, seed_periods[_CURRENT_IDX],
                    name, "10.00", due_date=date(2026, 3, 22),
                )
            db.session.commit()

            result = dashboard_service.compute_pulse_section(
                dashboard_section(seed_user["user"].id),
            )
            station = result["due_soon_stations"][0]
            assert station["days_until_due"] == 2  # (03-22 - 03-20).days
            assert all(
                b["days_until_due"] == 2 for b in station["visible_items"]
            )

    def test_undated_bills_excluded_from_stations(
        self, app, seed_user, seed_periods, db,
    ):
        """Undated rows stay off the axis but remain in due_soon (shelf).

        An undated transfer-out leg has no day_offset, so it forms no
        station, but it still appears in the flat due_soon list the "anytime
        this period" shelf renders.
        """
        with app.app_context():
            _add_expense(
                db.session, seed_user, seed_periods[_CURRENT_IDX],
                "Dated", "10.00", due_date=date(2026, 3, 18),
            )
            _undated_transfer_out(
                db.session, seed_user, seed_periods[_CURRENT_IDX],
                "Undated jar", "10.00",
            )
            db.session.commit()

            result = dashboard_service.compute_pulse_section(
                dashboard_section(seed_user["user"].id),
            )
            stations = result["due_soon_stations"]
            # Only the dated bill forms a station.
            assert len(stations) == 1
            assert stations[0]["visible_items"][0]["name"] == "Dated"
            # The undated leg is still on the flat list (shelf intact).
            undated = [b for b in result["due_soon"] if b["undated"]]
            assert [b["name"] for b in undated] == ["Transfer to Undated jar"]


# ── The hero == first chart point identity ──────────────────────────


class TestHeroChartIdentity:
    """The hero balance IS the chart's first point, and the revert agrees.

    Both read the current period's entry in the SAME folded period map since
    plan step X-c2b2, so their equality is structural rather than a
    coincidence of reservation semantics -- which is why the assertion that
    still earns its keep is the one against
    ``dashboard_service.compute_balance_section``: the anchor-editor's revert
    fragment renders the same ``#balance-display`` control from a SEPARATE
    producer call, and that is the pair that can still drift.

    (Before the cutover the hero was the as-of-TODAY scalar and the chart's
    first point the period-END balance.  They coincided only because that
    scalar was period-FLAT -- it summed the whole period's projected rows
    whatever their dates -- so the identity this class was written for was
    finding cash D2 holding the two together.  The template labels the figure
    "End of this period"; the fold made the scalar date-precise, so the hero
    moved to the date its own label promises.)
    """

    def test_hero_equals_first_chart_point_no_post_dated_entries(
        self, app, seed_user, seed_periods, db,
    ):
        """Hero balance == chart points[0].balance, and == the revert fragment.

        Seed account anchored $1,000.00.  Add a $300.00 projected expense
        and a $1,200.00 projected income in the current period, plus a
        tracked envelope with an entry dated BEFORE today.  The hero and the
        chart's first point are one map entry; the revert fragment reaches
        the same date through its own producer call, so all three must agree.
        """
        with app.app_context():
            current = seed_periods[_CURRENT_IDX]
            _add_expense(
                db.session, seed_user, current, "Rent", "300.00",
                due_date=date(2026, 3, 18),
            )
            one_off_row_of(
                current,
                name="Paycheck",
                amount=Decimal("1200.00"),
                user_id=current.user_id,
                account_id=seed_user["account"].id,
                scenario_id=seed_user["scenario"].id,
                transaction_type_id=ref_cache.txn_type_id(TxnTypeEnum.INCOME),
            )
            tracked = _add_tracked_expense(
                db.session, seed_user, current, "Groceries", "150.00",
            )
            _add_entry(
                db.session, seed_user, tracked, "40.00", date(2026, 3, 17),
            )
            db.session.commit()

            result = dashboard_service.compute_pulse_section(
                dashboard_section(seed_user["user"].id),
            )
            hero_balance = result["hero"]["balance"]
            first_point = result["chart"]["points"][0]["balance"]
            assert hero_balance == first_point
            # The anchor-editor's revert fragment renders this same control
            # from its own producer call -- the pair that can still drift.
            assert dashboard_service.compute_balance_section(
                dashboard_section(seed_user["user"].id),
            )["hero"]["balance"] == hero_balance
            # And the first chart point is the current period's end date.
            assert (
                result["chart"]["points"][0]["end_date"]
                == last_covered_day(current)
            )


# ── Cash-flow view for an any-kind grid account (seam reroute lock) ──


class TestPulseCashFlowViewForAnyKindGridAccount:
    """The pulse reads the CASH-FLOW view regardless of the grid account's kind.

    Regression lock for the Level-1 ``balance_at`` seam reroute.
    ``resolve_grid_account`` may return ANY kind -- a user can point the
    dashboard at an HYSA, or the fallback can land on a loan / investment --
    but the pulse is the spending-account runway, so the chart, the "lowest
    point ahead" trough, and the hero must show the PURE TRANSACTION running
    balance, never the kind-correct balance that accrues interest (HYSA),
    amortizes (loan), or compounds (investment).  The reroute briefly wired
    the chart to ``balance_map`` and the hero to the kind-correct
    ``balance_at`` scalar; every prior pulse test used a PLAIN checking
    account, where the cash-flow and kind-correct views coincide, so the
    divergence went uncaught.  These cases pin a non-cash grid account so a
    regression to the kind-correct entries fails loudly.
    """

    def test_hysa_grid_account_chart_and_trough_show_cash_not_interest(
        self, app, seed_user, seed_periods, db,
    ):
        """An HYSA grid account's chart + trough are cash, not accrued interest.

        Make a fresh HYSA (5% APY, daily) the user's default grid account,
        anchored $50,000.00 at ``seed_periods[0]`` (2026-01-02) with NO
        transactions, so the pure-cash running balance is a FLAT $50,000.00 at
        every forward period (anchor carried forward; no interest, no rows).
        The kind-correct map -- what the bug rendered -- compounds 5% APY
        across the ~2.5 months to the current period, so it reads STRICTLY
        ABOVE $50,000.00.  The chart and trough must show the flat cash value;
        had they kept the kind-correct map, the inflated "lowest point ahead"
        would hide a real future dip below zero.
        """
        with app.app_context():
            hysa = create_hysa_account(
                seed_user, db.session, seed_periods[0], Decimal("50000.00"),
            )
            set_default_grid_account(
                db.session, seed_user["user"].id, hysa.id,
            )

            scenario = seed_user["scenario"]
            bctx = BalanceContext.build(seed_user["user"].id)
            current = seed_periods[_CURRENT_IDX]
            cash = balance_at.cash_balance_map(hysa, bctx)
            accrued = balance_at.balance_map(hysa, bctx)

            # The cash truth is hand-computable: anchor carried flat, no rows,
            # no interest -> $50,000.00 at the current period.
            assert cash[current.id] == Decimal("50000.00")
            # Divergence is real (non-vacuous): 5% APY daily over the ~2.5
            # months from the period-0 anchor to the current period accrues
            # interest, so the kind-correct map exceeds the flat cash carry.
            assert accrued[current.id] > Decimal("50000.00")

            section = dashboard_service.compute_pulse_section(
                dashboard_section(seed_user["user"].id),
            )

            # Every charted point is the flat cash value, never the accrued
            # one (a revert to ``balance_map`` would make these == accrued).
            points_by_date = {
                p["end_date"]: p["balance"] for p in section["chart"]["points"]
            }
            for period in seed_periods[_CURRENT_IDX:]:
                assert points_by_date[last_covered_day(period)] == Decimal("50000.00")
                assert points_by_date[last_covered_day(period)] != accrued[period.id]

            # The "lowest point ahead" is the flat cash $50,000.00, not the
            # interest-inflated minimum the kind-correct map would have shown.
            assert section["trough"]["balance"] == Decimal("50000.00")

    def test_investment_grid_account_hero_shows_cash_not_modeled(
        self, app, seed_user, seed_periods, db,
    ):
        """An investment grid account's hero is the cash carry, not modeled growth.

        Make a 401(k) (7% assumed return) the user's default grid account,
        anchored $100,000.00 at ``seed_periods[0]`` with NO contributions, so
        the pure-cash running balance to today is a FLAT $100,000.00.  The
        kind-correct ``balance_at`` scalar -- what the bug's hero used --
        compounds that forward, reading STRICTLY ABOVE $100,000.00 at the
        current period.  The hero must show the cash carry (the runway view),
        matching the chart and the grid for the same account.
        """
        with app.app_context():
            inv = make_investment_account(
                seed_user, db.session, seed_periods[0], Decimal("100000.00"),
            )
            set_default_grid_account(
                db.session, seed_user["user"].id, inv.id,
            )

            scenario = seed_user["scenario"]
            bctx = BalanceContext.build(seed_user["user"].id)
            # The kind-correct scalar (the bug's hero) compounds the anchor
            # forward; assert the divergence is real before locking the fix.
            modeled = balance_at.balance_at(inv, bctx, _TODAY)
            assert modeled > Decimal("100000.00")

            section = dashboard_service.compute_pulse_section(
                dashboard_section(seed_user["user"].id),
            )
            # The hero is the cash carry: anchor $100,000.00 carried flat to
            # today with no contributions -> $100,000.00, not the modeled value.
            assert section["hero"]["balance"] == Decimal("100000.00")
            assert section["hero"]["balance"] != modeled


# ── compute_pulse_section degraded states ───────────────────────────


class TestPulseSectionDegraded:
    """The pulse producer's None contract for the degraded states."""

    def test_no_current_period_returns_none(self, app, seed_user):
        """No period contains today -> None.

        ``seed_user`` (no ``seed_periods``) has only the 2024 bootstrap period,
        so no SAVED period contains the pass's day, ``containing_index``
        answers ``None`` and the producer short-circuits.  That reader was
        ``pay_period_service.get_current_period`` until pay-calendar plan step
        C2-f2e; the state is the same one and the door is not.
        """
        with app.app_context():
            result = dashboard_service.compute_pulse_section(
                dashboard_section(seed_user["user"].id),
            )
            assert result is None

    def test_has_all_region_keys_when_populated(
        self, app, seed_user, seed_periods, db,
    ):
        """A populated pulse section carries exactly the eight region keys."""
        with app.app_context():
            result = dashboard_service.compute_pulse_section(
                dashboard_section(seed_user["user"].id),
            )
            assert set(result.keys()) == {
                "hero", "chart", "trough", "peak",
                "still_due", "street", "due_soon", "due_soon_stations",
            }


# ── One clock and one period per render (plan step C2-f2e) ─────────


class TestOneClockPerRender:
    """Every day-sensitive figure in the region reads the READ PASS's day.

    Three of them resolved ``date.today()`` for themselves before pay-calendar
    plan step **C2-f2e** -- the hero's staleness count, the street band's
    "Today" marker, and each bill's ``days_until_due`` -- so a render crossing
    midnight could count staleness against one day, place the marker on the
    next, and label a bill against either.  Ledger row **P55** names the class.

    These cases pin the day by PASSING one, which is also what makes them
    honest all year: they assert a figure MOVES with the pass's ``as_of``, so a
    producer that quietly re-read the wall clock would answer the frozen
    fixture day and fail.  A test that only asserted "the marker is where today
    is" would pass on both trees.

    **They could not be written against the merge base at all**, and that is
    the finding rather than a gap in the evidence: ``compute_pulse_section``
    took a ``user_id`` and built its own read pass, so no caller had a day to
    pin.  What CAN be graded on both sides is the period itself, and
    :class:`TestPeriodIsDerivedNotStored` below is that case -- run against
    ``5ab457b7`` it fails with the whole region ``None``.
    """

    def test_the_street_marker_follows_the_pass_day(
        self, app, seed_user, seed_periods, db,
    ):
        """The "Today" marker moves with the pass's ``as_of``, not the wall clock.

        Period 5 runs 2026-03-13 .. 2026-03-26.  A pass pinned to 2026-03-13
        puts the marker at offset 0 and a pass pinned to 2026-03-20 at offset
        7 -- ``(2026-03-20 - 2026-03-13).days``.  A producer reading its own
        ``date.today()`` would answer 7 for both, the suite's frozen day.
        """
        with app.app_context():
            opening = dashboard_service.compute_pulse_section(
                dashboard_section(seed_user["user"].id, as_of=date(2026, 3, 13)),
            )
            later = dashboard_service.compute_pulse_section(
                dashboard_section(seed_user["user"].id, as_of=_TODAY),
            )

            assert opening["street"]["today_offset"] == 0
            assert later["street"]["today_offset"] == 7
            # Both passes are inside ONE period, so the band itself did not move.
            assert opening["street"]["days_total"] == 13
            assert later["street"]["days_total"] == 13

    def test_days_until_due_counts_from_the_pass_day(
        self, app, seed_user, seed_periods, db,
    ):
        """A bill's ``days_until_due`` counts from the pass's day.

        A $50.00 bill due 2026-03-24 in the current period: 11 days out from a
        pass pinned to 2026-03-13, 4 days out from one pinned to 2026-03-20.
        """
        with app.app_context():
            _add_expense(
                db.session, seed_user, seed_periods[_CURRENT_IDX],
                "Water Bill", "50.00", due_date=date(2026, 3, 24),
            )
            db.session.commit()

            def _water(as_of):
                section = dashboard_service.compute_pulse_section(
                    dashboard_section(seed_user["user"].id, as_of=as_of),
                )
                return next(
                    bill for bill in section["due_soon"]
                    if bill["name"] == "Water Bill"
                )

            assert _water(date(2026, 3, 13))["days_until_due"] == 11
            assert _water(_TODAY)["days_until_due"] == 4

    def test_anchor_staleness_counts_from_the_pass_day(
        self, app, seed_user, seed_periods, db,
    ):
        """The hero's ``is_stale`` flag measures from the pass's day.

        The account is asserted on 2026-01-02 here.  The seeded one carries
        only its ORIGINATION assertion, dated on the bootstrap day before the
        calendar (plan step X-f3c-2c), so the day this case turns on is one
        the case states rather than one it inherits.  The default threshold is
        14 days.  A pass pinned to 2026-01-10 is 8 days out and
        NOT stale; the frozen 2026-03-20 is 77 days out and stale.  The
        threshold sits between the two, so the flag has to flip -- which a
        producer reading its own clock could not do.
        """
        with app.app_context():
            reassert_balance_on(
                db.session, seed_user["account"],
                settle_instant_on(date(2026, 1, 2)),
            )
            db.session.commit()
            observed = cash_ledger.reconciled_through(
                seed_user["account"].id,
            ).observed_day
            assert observed == date(2026, 1, 2)
            assert seed_user["settings"].anchor_staleness_days == 14

            fresh = dashboard_service.compute_pulse_section(
                dashboard_section(seed_user["user"].id, as_of=date(2026, 1, 10)),
            )
            stale = dashboard_service.compute_pulse_section(
                dashboard_section(seed_user["user"].id, as_of=_TODAY),
            )

            assert fresh["hero"]["is_stale"] is False
            assert stale["hero"]["is_stale"] is True


# **``TestPeriodIsDerivedNotStored`` was deleted whole at plan step
# ``pay_calendar:C4-c``, and it is worth saying what it did rather than
# leaving a gap.**  Its one case rewrote period 5's stored ``end_date`` to
# the day before the frozen today and asserted the hero region still
# rendered -- where the pre-cutover query matched
# ``start_date <= today <= end_date`` against that column, found no period
# covering today, and blanked the region to a "No pay period covers today"
# CTA for an owner mid-paycheck.  It was plan step C2-f2e's grade.
#
# C4-c dropped the column, so the disagreement it planted is not merely
# unreachable, it is SILENT: assigning to an attribute the model no longer
# maps sets a plain Python attribute, writes no UPDATE, and survives
# ``expire_all`` -- the *born dead* shape ``docs/plans/lessons.md`` names.
# The case and its own premise assertion both went on passing while
# measuring nothing, so it is deleted rather than left green.
#
# The class had documented that only ONE of the disagreement's two
# directions was covered, the other needing hand-edited or legacy rows
# (ledger row **P35**'s owner).  Neither direction exists now: there is one
# span, computed on every read.  The hero's period is graded on ordinary
# schedules by the classes above.


# ── Tracks: savings goal trajectory passthrough + debt fraction ─────


class TestTracksGoals:
    """Savings-goal metro tracks reshaped from compute_goal_progress."""

    def test_goal_track_passes_through_trajectory(
        self, app, seed_user, seed_periods, db,
    ):
        """A goal track carries name, percent, balance, target, and trajectory.

        A $2,500.00 savings account against a $10,000.00 fixed target:
            progress = 2500 / 10000 * 100 = 25.00%.
        The track exposes the trajectory keys (pace, projected completion
        date, required monthly) verbatim from calculate_trajectory.
        """
        with app.app_context():
            acct = create_savings_account(
                seed_user, db.session, "Goal Account",
                Decimal("2500.00"),
            )
            goal = SavingsGoal(
                user_id=seed_user["user"].id,
                account_id=acct.id,
                name="Emergency Fund",
                target_amount=Decimal("10000.00"),
                target_date=date(2027, 3, 1),
            )
            db.session.add(goal)
            db.session.commit()

            tracks = dashboard_service.compute_tracks_section(
                BalanceContext.build(seed_user["user"].id),
            )
            goal_tracks = [g for g in tracks["goals"] if g["account_id"] == acct.id]
            assert len(goal_tracks) == 1
            track = goal_tracks[0]
            assert track["name"] == "Emergency Fund"
            assert track["account_name"] == "Goal Account"
            assert track["current_balance"] == Decimal("2500.00")
            assert track["target_amount"] == Decimal("10000.00")
            # 2500 / 10000 * 100 = 25.00.
            assert track["progress_pct"] == Decimal("25.00")
            assert track["target_date"] == date(2027, 3, 1)
            # Trajectory keys present (the metro-track contract).
            assert "pace" in track
            assert "projected_completion_date" in track
            assert "required_monthly" in track
            assert "monthly_contribution" in track

    def test_goal_track_matches_savings_trajectory(
        self, app, seed_user, seed_periods, db,
    ):
        """The track's trajectory fields equal the /savings producer's verbatim.

        Proves the reshape passes calculate_trajectory's outputs through
        unchanged rather than recomputing them.
        """
        # pylint: disable=import-outside-toplevel
        from app.services import savings_dashboard_service

        with app.app_context():
            acct = create_savings_account(
                seed_user, db.session, "Trajectory Account",
                Decimal("3000.00"),
            )
            goal = SavingsGoal(
                user_id=seed_user["user"].id,
                account_id=acct.id,
                name="House",
                target_amount=Decimal("9000.00"),
                target_date=date(2027, 6, 1),
            )
            db.session.add(goal)
            db.session.commit()

            tracks = dashboard_service.compute_tracks_section(
                BalanceContext.build(seed_user["user"].id),
            )
            track = next(
                g for g in tracks["goals"] if g["account_id"] == acct.id
            )
            sav = next(
                gd for gd in savings_dashboard_service.compute_goal_progress(
                    BalanceContext.build(seed_user["user"].id),
                )
                if gd.goal.account_id == acct.id
            )
            assert track["pace"] == sav.trajectory.pace
            assert (
                track["projected_completion_date"]
                == sav.trajectory.projected_completion_date
            )
            assert track["required_monthly"] == sav.trajectory.required_monthly
            assert track["progress_pct"] == sav.progress_pct

    def test_no_goals_empty_list(self, app, seed_user, seed_periods, db):
        """No active goals -> goals is an empty list."""
        with app.app_context():
            tracks = dashboard_service.compute_tracks_section(
                BalanceContext.build(seed_user["user"].id),
            )
            assert tracks["goals"] == []


class TestTracksDebt:
    """The debt track: the /savings debt summary, passed through whole.

    It was a ``DebtTrack`` pairing that summary with a fraction from a SECOND
    narrow producer until plan step X-u (finding N-109); the fraction is a
    ``DebtSummary`` field now and this tier adds nothing to what the producer
    answered.
    """

    def test_no_debt_returns_none(self, app, seed_user, seed_periods, db):
        """No loan accounts -> debt is None (no track rendered)."""
        with app.app_context():
            tracks = dashboard_service.compute_tracks_section(
                BalanceContext.build(seed_user["user"].id),
            )
            assert tracks["debt"] is None

    def test_debt_track_is_the_summary_whole(
        self, app, seed_user, seed_periods, db,
    ):
        """A loan -> the very DebtSummary /savings renders, fraction included.

        A $1,000.00 auto loan originated 2026-01-01 at 5% for 24 months.
        By the frozen today (2026-03-20) confirmed payments have reduced
        the balance, so:
            principal_paid_fraction =
                (original - current) / original
              = (1000.00 - total_debt) / 1000.00
        which is the SAME current balance the debt summary's total_debt
        reports (same loan set), so the fraction reconciles with the
        summary to the cent.  The data is present (original_principal is
        NOT NULL), so the fraction is a real Decimal, not None.
        """
        with app.app_context():
            create_loan_account(seed_user, db.session, name="Pulse Loan")

            tracks = dashboard_service.compute_tracks_section(
                BalanceContext.build(seed_user["user"].id),
            )
            debt = tracks["debt"]
            assert debt is not None
            # The summary is PASSED THROUGH (plan steps X-s3, X-u), never
            # copied field by field and no longer wrapped: the track IS the
            # value ``/savings`` renders, so a field the summary grows arrives
            # here by construction rather than by someone remembering to copy
            # it.  Asserted as VALUE equality against the producer -- a frozen
            # dataclass compares on every field, so a tier that dropped or
            # altered one fails here, where a "these three keys are present"
            # check passed while the dict silently lost the outlook's third
            # state (finding N-104).
            #
            # Measured limit, stated rather than left implied: this fixture
            # carries no non-loan liability and no salary, so
            # ``debt_without_payoff_date``
            # and ``dti`` sit at their empty values on BOTH sides and a
            # substitution there would not be discriminating.  The firing
            # control therefore plants its defect in ``total_debt``, which the
            # fixture does populate.
            assert debt == savings_dashboard_service.compute_debt_summary(
                BalanceContext.build(seed_user["user"].id),
            )
            # The honest fraction is present and is a Decimal in [0, 1].
            fraction = debt.principal_paid_fraction
            assert isinstance(fraction, Decimal)
            assert Decimal("0") <= fraction <= Decimal("1")
            # Reconcile with the summary: fraction uses the SAME current
            # balance the summary's total_debt sums (one loan, original
            # $1,000.00), so:
            #   fraction == (1000.00 - total_debt) / 1000.00.
            expected = (
                (Decimal("1000.00") - debt.total_debt) / Decimal("1000.00")
            )
            assert fraction == expected

    def test_debt_fraction_zero_before_any_payment(
        self, app, seed_user, seed_periods, db,
    ):
        """A loan whose balance still equals its original -> fraction 0.

        Originate the loan TODAY (frozen 2026-03-20) so no scheduled
        payment has been confirmed yet; the resolver's current balance
        equals the original principal, giving a paid fraction of exactly
        0: (1000.00 - 1000.00) / 1000.00 = 0.
        """
        with app.app_context():
            # Originate TODAY (frozen 2026-03-20) so no scheduled payment
            # is yet confirmed: the resolver's current balance equals the
            # original principal.
            create_loan_account(
                seed_user, db.session, name="Fresh Loan",
                origination_date=_TODAY, payment_day=_TODAY.day,
            )

            tracks = dashboard_service.compute_tracks_section(
                BalanceContext.build(seed_user["user"].id),
            )
            debt = tracks["debt"]
            assert debt.total_debt == Decimal("1000.00")
            # No principal paid yet -> fraction is exactly 0.
            assert debt.principal_paid_fraction == Decimal("0")

    def test_one_render_projects_the_debt_accounts_once(
        self, app, seed_user, seed_periods, db, monkeypatch,
    ):
        """ONE tracks render runs the debt projection ONCE (finding N-109).

        The guard for the redundancy plan step X-u deleted, written against
        the pipeline rather than against the clock: two debt producers each ran
        ``_load_dashboard_core_data`` -> ``_load_account_params`` ->
        ``_compute_account_projections`` over the same loans, so a render paid
        for two full projections and three seam-batch builds (measured at 2 and
        3 on the developer's own data, and at 2 and 3 on the prod-shape clone).

        The fixture has a loan and NO savings goals, so
        ``compute_goal_progress`` returns early without projecting anything and
        every projection this counts is the debt one.  A wall-clock or
        SQL-count assertion would be a flaky proxy for the same fact; the call
        count is the fact.

        Shown FIRING: restoring the second producer's call makes this read 2.
        """
        # pylint: disable=import-outside-toplevel
        from app.services.savings_dashboard_service import _orchestrator

        calls = []
        original = _orchestrator._compute_account_projections

        def _counting(accounts, ctx):
            calls.append([acct.id for acct in accounts])
            return original(accounts, ctx)

        with app.app_context():
            loan = create_loan_account(seed_user, db.session, name="Once Loan")
            monkeypatch.setattr(
                _orchestrator, "_compute_account_projections", _counting,
            )

            tracks = dashboard_service.compute_tracks_section(
                BalanceContext.build(seed_user["user"].id),
            )

            # The render really did produce the debt track (an empty result
            # would satisfy a bare count of zero).
            assert tracks["debt"] is not None
            assert len(calls) == 1
            assert loan.id in calls[0]


# ── Tracks: income-relative goal pace passthrough ───────────────────


class TestTracksIncomeRelativeGoal:
    """An income-relative goal resolves a real target on the track."""

    def test_income_relative_goal_track_has_positive_target(
        self, app, seed_user, seed_periods, db,
    ):
        """An income-relative goal's resolved target is positive on the track.

        With a salary profile, the NULL stored target resolves to a real
        dollar figure (multiplier * net biweekly pay) -- the track must
        carry the resolved (positive) target, not 0, and a pace from the
        trajectory.
        """
        with app.app_context():
            make_salary_profile(seed_user, db.session, name="Track Salary")
            acct = create_savings_account(
                seed_user, db.session, "IR Goal Account",
                Decimal("4000.00"),
            )

            ir_id = ref_cache.goal_mode_id(GoalModeEnum.INCOME_RELATIVE)
            paychecks_id = ref_cache.income_unit_id(IncomeUnitEnum.PAYCHECKS)
            goal = SavingsGoal(
                user_id=seed_user["user"].id,
                account_id=acct.id,
                name="3 Paychecks",
                goal_mode_id=ir_id,
                income_unit_id=paychecks_id,
                income_multiplier=Decimal("3.00"),
                target_amount=None,
            )
            db.session.add(goal)
            db.session.commit()

            tracks = dashboard_service.compute_tracks_section(
                BalanceContext.build(seed_user["user"].id),
            )
            track = next(
                g for g in tracks["goals"] if g["account_id"] == acct.id
            )
            assert track["target_amount"] > Decimal("0.00")
            # The pace key is present (None when no contribution / target
            # date, a string otherwise) -- the track exposes it either way.
            assert "pace" in track
