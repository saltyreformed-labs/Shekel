"""The 52-period drift oracle (plan step X-g4a, ruling R-AT): every tier across 52 periods, graded
against an independent running total built from the same named parameters.
"""

from datetime import date, timedelta
from decimal import Decimal

from app.enums import StatusEnum
from app.services.balance_at._cash_fold import assembled_fold, period_balances
from tests._test_helpers import (
    add_txn,
    create_settled_cash_transaction,
    last_covered_day,
    override_anchor,
    period_window,
    read_pass,
)

from ._helpers import _instant


# -- The 52-period drift oracle (plan step X-g4a, ruling R-AT) --------------
#
# Every parameter of the shape is named HERE, at module scope, so the BUILDER
# and the ORACLE below read the same numbers without either deriving them from
# the other -- and so a reader can see the whole fixture without reading either.

# The ``seed_periods_52`` fixture's own opening assertion: ``build_seed_user``
# creates the seeded Checking at this balance, asserted on
# ``SEED_USER_BOOTSTRAP_START``.  *This said the assertion was "stamped at
# midnight UTC on period 0's start" because ``_drop_seed_user_bootstrap``
# re-pointed the factory row and re-stamped it.  Both halves are gone: plan
# step ``balance:X-f3c-2c`` deleted the re-point when the table became
# append-only, and ``pay_calendar:C4-b-1`` deleted the function.  The FIGURE is
# unchanged, and it is the figure this constant is.*
_DRIFT_OPENING = Decimal("1000.00")


_DRIFT_INCOME = Decimal("2500.00")


# Non-round, so a cent dropped or double-counted anywhere in 52 periods shows up
# in the total instead of cancelling.
_DRIFT_EXPENSE = Decimal("1175.53")


# Period 15's settled expense carries an ACTUAL over its estimate, so the
# settled row's movement carrying the RECORDED figure is graded rather than
# assumed: a walk reading the ESTIMATE understates every column from 15 on by
# $24.47.
_DRIFT_ACTUAL_EXPENSE = Decimal("1200.00")


_DRIFT_ACTUAL_INDEX = 15


# Three of these sum to $99.99, NOT $100.00.
_DRIFT_THIRD = Decimal("33.33")


_DRIFT_THIRDS_PER_PERIOD = 3


_DRIFT_THIRD_EVERY = 3


_DRIFT_INCOME_DAY = 2


_DRIFT_EXPENSE_DAY = 5


_DRIFT_THIRD_DAY = 7


# 1..16, straddling the re-assertion at 13.
_DRIFT_SETTLED_PERIODS = range(1, 17)


# 20..51.
_DRIFT_PLANNED_PERIODS = range(20, 52)


# Period 19's end.  Period 20 starts the very next day, so every ordinary
# planned row's own attribution date is strictly after ``as_of + 1`` and ruling
# R-G's clamp is a no-op for it -- the ONE row it must bite is the overdue one
# below, which is what makes the clamp gradeable here rather than inert.
_DRIFT_AS_OF = date(2026, 10, 8)


# The RESET, 09:00 UTC on period 13's first day.
_DRIFT_REASSERTION = Decimal("5412.83")


_DRIFT_REASSERTION_AT = _instant(2026, 7, 3, 9, 0)


_DRIFT_REASSERTION_INDEX = 13


# Period 13's two settled rows sit on the assertion's OWN CIVIL DAY, and under
# ruling R-DH (a) an assertion is the CLOSING balance for its day -- so BOTH are
# inside it and the reset absorbs both.  They were two instants an hour either
# side of the assertion's when ruling R-B partitioned by instant; R-DH replaced
# that rule and the figures below were recomputed for it then, so collapsing the
# pair to the one day they always shared moves nothing.  Kept as a named
# constant rather than inlined because "the settles that share the assertion's
# day" is the property period 13 is here to grade.
_DRIFT_STRADDLE_DAY = date(2026, 7, 3)


# Rows worth exactly nothing, in four shapes.  See the class docstring for what
# they are and are NOT: three independent layers zero them, so no single-point
# defect leaks one, and they are defence-in-depth rather than controls.
_DRIFT_EXCLUDED_ONLY_INDEX = 17


_DRIFT_CANCELLED = Decimal("600.00")


_DRIFT_CREDIT = Decimal("350.00")


_DRIFT_EXCLUDED_MIXED_INDEX = 29


_DRIFT_MIXED_CANCELLED = Decimal("500.00")


_DRIFT_MIXED_CREDIT = Decimal("450.00")


_DRIFT_ZERO_INDEX = 25


_DRIFT_DELETED_INDEX = 45


_DRIFT_DELETED = Decimal("999.00")


# Ruling R-G: a still-Projected row whose own date has PASSED is clamped
# forward to ``as_of + 1``, never absorbed.  It sits in period 16, which ALREADY
# holds two settled rows -- the ACTUAL and PLANNED tiers in ONE column, which is
# what every user's current period looks like -- and lands on 2026-10-09,
# period 20's first day.
_DRIFT_OVERDUE_INDEX = 16


_DRIFT_OVERDUE = Decimal("412.19")


_DRIFT_OVERDUE_DUE = date(2026, 8, 20)


_DRIFT_OVERDUE_LANDS_INDEX = 20


# ``DerivedPeriod.attribution_day`` clamps a due date outside its own period
# to the nearer boundary, and BOTH directions are graded.  Period 40's row is
# due 30 days EARLY (2027-06-16, inside period 37); period 44's is 20 days LATE
# (2027-10-13, inside period 46).  Unclamped, each would land on a different
# column than the one it is budgeted to.
_DRIFT_STRAY_INDEX = 40


_DRIFT_STRAY = Decimal("77.11")


_DRIFT_STRAY_EARLY_DAYS = 30


_DRIFT_LATE_STRAY_INDEX = 44


_DRIFT_LATE_STRAY = Decimal("88.23")


_DRIFT_STRAY_LATE_DAYS = 20


def _drift_settle_days(period, index):
    """Return ``(income_day, expense_day)`` for a settled period.

    Args:
        period: The :class:`~app.models.pay_period.PayPeriod` being filled.
        index: Its ``period_index``.

    Returns:
        The two civil settle days.  Period 13's both fall on the re-assertion's
        own day; every other period's sit on ordinary days inside their own
        period.  They were noon-UTC INSTANTS until plan step X-f1 (ruling
        R-EC) -- noon UTC is the same civil day in the display timezone, which
        is the day the readers derived from them, so every figure below is
        unchanged.
    """
    if index == _DRIFT_REASSERTION_INDEX:
        return _DRIFT_STRADDLE_DAY, _DRIFT_STRADDLE_DAY
    return (
        period.start_date + timedelta(days=_DRIFT_INCOME_DAY),
        period.start_date + timedelta(days=_DRIFT_EXPENSE_DAY),
    )


def _add_drift_zero_worth_rows(db_session, seed_user, periods):
    """Add the four row shapes the fold must value at exactly ZERO.

    A Cancelled and a Credit row in a period holding nothing else (the
    original's S5), the same pair inside a period that also holds contributing
    rows (S3 / S4), a zero-amount projected row (S8), and a soft-deleted row.
    None appears in :func:`_drift_oracle`.  What they are NOT is stated in the
    class docstring: three layers zero them independently, so they are
    defence-in-depth rather than firing controls.

    Args:
        db_session: The test ``db.session``.
        seed_user: The ``seed_user`` fixture dict.
        periods: The 52 pay periods.
    """
    for index, cancelled, credit in (
        (_DRIFT_EXCLUDED_ONLY_INDEX, _DRIFT_CANCELLED, _DRIFT_CREDIT),
        (
            _DRIFT_EXCLUDED_MIXED_INDEX,
            _DRIFT_MIXED_CANCELLED,
            _DRIFT_MIXED_CREDIT,
        ),
    ):
        add_txn(
            db_session, seed_user, periods[index], f"cancelled p{index}",
            cancelled, status_enum=StatusEnum.CANCELLED,
        )
        add_txn(
            db_session, seed_user, periods[index], f"credit p{index}", credit,
            status_enum=StatusEnum.CREDIT,
        )
    add_txn(
        db_session, seed_user, periods[_DRIFT_ZERO_INDEX],
        f"zero p{_DRIFT_ZERO_INDEX}", Decimal("0.00"),
    )
    add_txn(
        db_session, seed_user, periods[_DRIFT_DELETED_INDEX],
        f"deleted p{_DRIFT_DELETED_INDEX}", _DRIFT_DELETED, is_deleted=True,
    )


def _add_drift_stray_dated_rows(db_session, seed_user, periods):
    """Add the two rows whose due dates fall OUTSIDE their own pay period.

    One 30 days early and one 20 days late, so ``attribution_day``'s two
    clamp arms are each graded: unclamped, the early row lands on period 37 and
    the late one on period 46, neither of which is the column it is budgeted to.

    Args:
        db_session: The test ``db.session``.
        seed_user: The ``seed_user`` fixture dict.
        periods: The 52 pay periods.
    """
    early = periods[_DRIFT_STRAY_INDEX]
    add_txn(
        db_session, seed_user, early, "stray early due date", _DRIFT_STRAY,
        due_date=early.start_date - timedelta(days=_DRIFT_STRAY_EARLY_DAYS),
    )
    late = periods[_DRIFT_LATE_STRAY_INDEX]
    add_txn(
        db_session, seed_user, late, "stray late due date", _DRIFT_LATE_STRAY,
        due_date=last_covered_day(late) + timedelta(days=_DRIFT_STRAY_LATE_DAYS),
    )


def _build_drift_shape(db_session, seed_user, periods):
    """Create the 52-period mixed shape on the seed user's Checking account.

    Args:
        db_session: The test ``db.session``.
        seed_user: The ``seed_user`` fixture dict.
        periods: The 52 pay periods from ``seed_periods_52``.
    """
    for index in _DRIFT_SETTLED_PERIODS:
        period = periods[index]
        income_day, expense_day = _drift_settle_days(period, index)
        create_settled_cash_transaction(
            seed_user, period, _DRIFT_INCOME, is_income=True,
            name=f"paycheck p{index}", settled_on=income_day,
        )
        create_settled_cash_transaction(
            seed_user, period, _DRIFT_EXPENSE,
            name=f"rent p{index}", settled_on=expense_day,
            settled_amount=(
                _DRIFT_ACTUAL_EXPENSE if index == _DRIFT_ACTUAL_INDEX else None
            ),
        )

    override_anchor(
        db_session, seed_user["account"], periods[_DRIFT_REASSERTION_INDEX],
        _DRIFT_REASSERTION,
        at=_DRIFT_REASSERTION_AT,
    )

    add_txn(
        db_session, seed_user, periods[_DRIFT_OVERDUE_INDEX], "overdue bill",
        _DRIFT_OVERDUE, due_date=_DRIFT_OVERDUE_DUE,
    )

    for index in _DRIFT_PLANNED_PERIODS:
        period = periods[index]
        add_txn(
            db_session, seed_user, period, f"paycheck p{index}", _DRIFT_INCOME,
            is_income=True,
            due_date=period.start_date + timedelta(days=_DRIFT_INCOME_DAY),
        )
        add_txn(
            db_session, seed_user, period, f"rent p{index}", _DRIFT_EXPENSE,
            due_date=period.start_date + timedelta(days=_DRIFT_EXPENSE_DAY),
        )
        if index % _DRIFT_THIRD_EVERY == 0:
            for slot in range(_DRIFT_THIRDS_PER_PERIOD):
                add_txn(
                    db_session, seed_user, period, f"third {slot} p{index}",
                    _DRIFT_THIRD,
                    due_date=period.start_date
                    + timedelta(days=_DRIFT_THIRD_DAY),
                )

    _add_drift_stray_dated_rows(db_session, seed_user, periods)
    _add_drift_zero_worth_rows(db_session, seed_user, periods)
    db_session.commit()


def _drift_oracle(periods):
    """Return ``{period_id: balance}`` from an INDEPENDENT running total.

    Iterates the 52 periods by INDEX and applies each tier's per-period effect
    as the rulings say it should land.  It imports nothing and calls nothing in
    ``balance_at`` or ``cash_ledger``, and it does not re-derive any of the
    producer's dating arithmetic -- it states each ruling's OUTCOME as a
    constant, which is the stronger of the two oracle forms here: the producer
    computes a landing DAY from ``attribution_day`` and ``max(nominal, as_of +
    1)`` and prefix-sums it, while this names the landing PERIOD outright
    (``_DRIFT_OVERDUE_LANDS_INDEX``, ``_DRIFT_STRAY_INDEX``), so a broken clamp
    is caught rather than mirrored.  Likewise it ASSIGNS on the re-assertion
    where the producer books ``anchor_balance - balance_before``.

    Args:
        periods: The 52 pay periods, ordered by ``period_index``.

    Returns:
        ``dict`` mapping period id to the expected end balance.
    """
    running = _DRIFT_OPENING
    expected = {}
    for index, period in enumerate(periods):
        if index == _DRIFT_REASSERTION_INDEX:
            # The reset discards everything before it AND everything else on
            # its own civil day -- the income recorded earlier and the expense
            # recorded later alike -- because an assertion is that day's
            # CLOSING balance (ruling R-DH (a), which superseded R-B's instant
            # partition on 2026-07-31).  So the column IS the asserted figure.
            running = _DRIFT_REASSERTION
        elif index in _DRIFT_SETTLED_PERIODS:
            expense = (
                _DRIFT_ACTUAL_EXPENSE if index == _DRIFT_ACTUAL_INDEX
                else _DRIFT_EXPENSE
            )
            running += _DRIFT_INCOME - expense
        elif index in _DRIFT_PLANNED_PERIODS:
            running += _DRIFT_INCOME - _DRIFT_EXPENSE
            if index % _DRIFT_THIRD_EVERY == 0:
                running -= _DRIFT_THIRD * _DRIFT_THIRDS_PER_PERIOD
        if index == _DRIFT_OVERDUE_LANDS_INDEX:
            running -= _DRIFT_OVERDUE
        if index == _DRIFT_STRAY_INDEX:
            running -= _DRIFT_STRAY
        if index == _DRIFT_LATE_STRAY_INDEX:
            running -= _DRIFT_LATE_STRAY
        expected[period.id] = running
    return expected


def _drift_period_map(seed_user, periods):
    """Return the fold's period-end balance map for the drift shape."""
    account = seed_user["account"]
    ctx = read_pass(account, seed_user["scenario"], _DRIFT_AS_OF)
    return period_balances(
        assembled_fold(account, ctx), period_window(periods),
    )


class TestTheDriftOracleWalksFiftyTwoPeriods:
    """52 periods, every tier, against an independent running total.

    The long-horizon cumulative-accuracy oracle, ported at plan step **X-g4a**
    (ruling R-AT) from ``test_balance_calculator.py``'s
    ``test_52_period_penny_accuracy``, which dies with ``_calculator`` at
    X-g4b.  The original walked 52 periods of still-projected rows carried
    forward from an anchor -- exactly ONE of the tiers this fold has -- so a
    faithful port would have been a drift oracle for a third of the producer it
    now grades.  The shape:

    * the **OPENING** assertion ($1,000.00 at 2026-01-02 00:00 UTC);
    * a **SETTLED** past, periods 1-16, every row stamped at a pinned instant,
      one of them (period 15) carrying an ACTUAL over its estimate;
    * a mid-horizon **RE-ASSERTION** on 2026-07-03 -- the RESET the original
      could not express at all -- whose own period's two rows sit either side of
      it on its own civil day, so ruling **R-DH (a)**'s CLOSING-BALANCE
      partition decides period 13's figure and an instant-keyed partition
      cannot reproduce it;
    * period 16 holding the SETTLED and PLANNED tiers at once -- two settled
      rows plus an overdue bill due 2026-08-20 that ruling **R-G** must clamp
      forward onto period 20.  That coexistence is what every user's CURRENT
      period looks like (plan Section 7.4);
    * periods 17-19 holding nothing that reaches the balance, one of them
      holding ONLY a Cancelled and a Credit row, so a column with nothing in it
      is proved to carry the running total forward;
    * a still-**PROJECTED** future, periods 20-51, including two rows whose due
      dates fall outside their own period -- one 30 days early, one 20 days
      late -- so both of ``attribution_day``'s clamp arms are graded;
    * rows worth exactly nothing in four shapes: Cancelled, Credit,
      zero-amount and soft-deleted.

    **What the original covered and this does not, measured rather than
    asserted.** Its done / received exclusions (S2 / S6 / S7) are SUPERSEDED
    rather than dropped -- the fold counts a settled row as an ACTUAL from the
    day the money moved, which is finding cash D1 and the whole reason
    ``_calculator`` is being deleted.  Its non-round ANCHOR is not reproduced:
    the fixture's opening is $1,000.00 and cannot be changed without writing a
    cache-versus-history divergence production cannot reach, so the
    truncation-exposing role is carried by the non-round re-assertion
    ($5,412.83) and by every per-period amount.  Ruling **R-I** is NOT graded
    here and no shape could grade it at this grain -- no period end precedes the
    opening -- so it stays with :class:`TestTheOpeningMovesIntoTheSeed` above.

    **The oracle is a test-local running total and never the fold reading
    itself** (plan Section 7.2).  Sampling is forbidden: every one of the 52
    columns is asserted, never a sample of them.  **Ruling R-AT's "cumulative
    cross-check" is the seven hand-written figures** in the second test -- human
    arithmetic is a second instrument over the same facts, where the original's
    flat re-summation would only have re-checked this file's own loop.

    **Firing controls, run at X-g4a** (plan Section 7.3; each a one-line
    production mutation, reverted).  EIGHT fail this class, seven of them on
    BOTH tests: the assertion no longer resetting the walked total
    (``cash_ledger._walk``); the planned tier never merging into the running
    steps; the closing-balance partition re-keyed onto the INSTANT
    (``cash_ledger.ReconciledThrough.covers``) -- the control that
    ran the other way until ruling R-DH (a), and still fires, because period 13
    is built to separate the two rules; ruling R-G's clamp
    deleted; its floor off by one (``not_before = as_of``); the map sampling
    each period's START; and ``settled_cash_leg`` valuing a settled row at its
    ESTIMATE rather than its ACTUAL.  The eighth, ``attribution_day``'s clamp
    deleted, fails the 52-column test in EITHER direction while the hand-figure
    test survives it -- the stray rows move to periods neither test names
    individually and net out again by the horizon, which is precisely why the
    52-column walk is not redundant with the eight named figures.

    **Three mutation CLASSES do NOT fail it, and the reason is structural --
    stated so the boundary is known instead of discovered.**  (1) Ignoring
    ``due_date`` entirely lands every ordinary planned row on its own period's
    start, invisible at period-END grain; the day a row lands on is graded at
    day grain by ``test_the_planned_tier.py``'s :class:`TestThePlannedTier`.
    (2) Shifting every
    assertion's ``visible_on`` by a day moves neither assertion out of its own
    period; that is ``test_an_assertion_covers_what_preceded_it.py``'s
    :class:`TestEveryAssertionIsReplayed`'s subject.  (3) The
    four zero-worth rows cannot be leaked by any SINGLE-point defect: the SQL
    clause pair (``balance_contributing_clause`` / ``is_projected_clause``), the
    Python predicate ``sum_projected`` re-applies, and
    ``Transaction.effective_amount``'s own zero-guards each zero them
    independently.  Only the soft-deleted row fires, and only under a
    simultaneous two-point break.  They are retained as defence-in-depth
    verification, NOT as controls, and this paragraph is what stops a later
    reader crediting them as coverage.

    ``tests/test_services`` freezes ``date.today()`` to 2026-03-20 while this
    shape stamps instants from 2026-01-18 to 2026-08-19 and reads at
    2026-10-08, so the fixture is production-shaped only when read as "today is
    2026-10-08".  Nothing under test consults that clock: the walk takes none,
    the read's ``as_of`` is explicit, and neither live-override seam has a
    candidate row here (no salary-linked template, no loan-payment transfer).
    """

    def test_every_period_end_matches_the_independent_running_total(
        self, db, seed_user, seed_periods_52,
    ):
        """All 52 columns, each against the oracle's own figure.

        The cumulative property: a one-cent error anywhere in the walk survives
        to every later column, so each column is asserted individually rather
        than only the total.
        """
        _build_drift_shape(db.session, seed_user, seed_periods_52)
        expected = _drift_oracle(seed_periods_52)

        actual = _drift_period_map(seed_user, seed_periods_52)

        assert len(seed_periods_52) == 52
        assert len(actual) == 52
        for index, period in enumerate(seed_periods_52):
            assert actual[period.id] == expected[period.id], (
                f"period {index} (id={period.id}, ending "
                f"{last_covered_day(period)}): expected "
                f"{expected[period.id]}, got "
                f"{actual[period.id]}, diff "
                f"{actual[period.id] - expected[period.id]}"
            )

    def test_the_named_columns_are_the_hand_computed_figures(
        self, db, seed_user, seed_periods_52,
    ):
        """Eight columns computed by hand, one per structural feature.

        The oracle above is a loop; these are arithmetic written out, so an
        error shared by the builder and the oracle still fails here -- which is
        ruling R-AT's cumulative cross-check.  Net per ordinary period is
        ``2,500.00 - 1,175.53 = 1,324.47``.

          * **period 12** (ends 2026-07-02, the last column before the reset):
            ``1,000.00 + 12 x 1,324.47 = $16,893.64``.
          * **period 13** (ends 2026-07-16, the reset's own column): the
            assertion REPLACES that total and BOTH of its own day's rows go with
            it -- the income recorded earlier and the expense recorded later --
            so the column IS the asserted ``$5,412.83`` (ruling R-DH (a)).  A
            fold that ignored the assertion would read
            ``16,893.64 + 1,324.47 = $18,218.11``; one partitioning on the
            INSTANT keeps the later expense and reads
            ``5,412.83 - 1,175.53 = $4,237.30``, which is what this figure was
            until 2026-07-31.
          * **period 15** (ends 2026-08-13): its settled expense is worth its
            ACTUAL $1,200.00, not its $1,175.53 estimate, so this period nets
            ``2,500.00 - 1,200.00 = 1,300.00`` --
            ``5,412.83 + 1,324.47 + 1,300.00 = $8,037.30``.
          * **period 16** (ends 2026-08-27): settled and planned in ONE column.
            Its two settled rows net ``+1,324.47``; its overdue bill is clamped
            OUT of it by ruling R-G -- ``8,037.30 + 1,324.47 = $9,361.77``.
          * **period 17** (ends 2026-09-10): holds ONLY a $600.00 Cancelled and
            a $350.00 Credit row, so it carries ``$9,361.77`` unchanged.
          * **period 19** (ends 2026-10-08, the read's own as-of): still
            ``$9,361.77``.
          * **period 20** (ends 2026-10-22): the clamp lands the overdue bill
            here, ``9,361.77 + 1,324.47 - 412.19 = $10,274.05``.
          * **period 51** (ends 2027-12-30, the horizon): periods 21-51 add
            ``31 x 1,324.47 = 41,058.57``, the 11 of them divisible by three
            each hold back ``3 x 33.33 = 99.99``, and the two stray-dated rows
            hold back ``77.11`` (period 40) and ``88.23`` (period 44) --
            ``10,274.05 + 41,058.57 - 1,099.89 - 77.11 - 88.23 = $50,067.39``.
        """
        _build_drift_shape(db.session, seed_user, seed_periods_52)

        actual = _drift_period_map(seed_user, seed_periods_52)

        assert actual[seed_periods_52[12].id] == Decimal("16893.64")
        assert actual[seed_periods_52[13].id] == Decimal("5412.83")
        assert actual[seed_periods_52[15].id] == Decimal("8037.30")
        assert actual[seed_periods_52[16].id] == Decimal("9361.77")
        assert actual[seed_periods_52[17].id] == Decimal("9361.77")
        assert actual[seed_periods_52[19].id] == Decimal("9361.77")
        assert actual[seed_periods_52[20].id] == Decimal("10274.05")
        assert actual[seed_periods_52[51].id] == Decimal("50067.39")
