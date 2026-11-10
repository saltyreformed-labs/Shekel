"""X-b / X-c2b2: the three cash seam entries ARE the fold, on every day.

Plan steps X-b and X-c2b2 (``docs/audits/balance_architecture/README.md``).
Runs ``balance_at._cash_fold``'s own reading against the three seam entries
-- ``cash_balance_map``, ``cash_balance_at`` and ``cash_daily_balance_series`` --
on **EVERY DAY** of each shape's domain.

**This file changed sides at the cutover, and the fixtures did not.**  At plan
step X-b the three entries were separate producers carrying the live findings
this phase exists to close, so equality was NOT the pass condition: on a shape
that triggered a finding the fold had to DIFFER, in hand-computed dollars, and
:class:`TestEveryFindingIsClosedAtTheSeam` asserted the size of each gap.  Plan
step X-c2b2 pointed all three at the fold, so those same shapes and those same
figures now assert the CLOSED state -- the seam answering what only the fold used
to.  Each test names the figure the retired producer returned beside the one the
seam returns now, which is the moved-figure record in executable form: revert
the cutover and the old number comes back, and the test says so.

* :class:`TestACleanShapeAgreesOnEveryDay` -- on a shape that triggers no
  finding, all three entries and the fold agree on every day of the domain.
  Since the cutover this is structural rather than a coincidence, and it is
  still worth pinning: it is what proves a scalar, a period map and a daily
  series are ONE running total read at three grains rather than three producers
  that happen to line up.
* :class:`TestEveryFindingIsClosedAtTheSeam` -- on a shape that triggers a
  finding, the seam answers the fold's figure, on named days and to the cent.

**Sampling is forbidden.**  A 14-day sample once scored perfect while wrong by
$178,103.41 on 22% of days (plan Section 7.2), so each comparison walks every
day of its range and guards the loop so a vacuous domain cannot pass.
"""

from datetime import date, timedelta
from decimal import Decimal

from app.services import balance_at
from app.services.balance_at import BalanceContext
from app.services.balance_at._cash_fold import assembled_fold, balances_at
from tests._test_helpers import (
    add_txn,
    append_balance_assertion,
    create_settled_cash_transaction,
    last_covered_day,
)
from tests.test_services.test_cash_fold import _instant


def _days(first_day, last_day):
    """Return every calendar day in ``[first_day, last_day]``, ascending."""
    span = (last_day - first_day).days
    return [first_day + timedelta(days=offset) for offset in range(span + 1)]


def _context(seed_user, as_of):
    """Return the read pass's :class:`BalanceContext` pinned at *as_of*."""
    return BalanceContext.build(seed_user["user"].id, as_of=as_of)


def _folded(seed_user, account, as_of, days):
    """Fold *account* at every day in *days*.

    **On its OWN read pass, deliberately.**  :func:`_context` builds a fresh
    :class:`~app.services.balance_at.BalanceContext` per call, so the fold on
    the left of every equality in this file and the seam entry on the right are
    two INDEPENDENT passes -- which is what keeps the comparisons meaningful.
    Sharing one pass would make both sides read the same memoized
    :class:`~app.services.balance_at._cash_fold.AssembledCashFold` after plan
    step X-i4, and every equality here would become a tautology that could not
    fail. A first draft of this docstring claimed the sharing as a feature;
    an adversarial review caught that it was both false and the wrong thing to
    want.
    """
    return balances_at(
        assembled_fold(account, _context(seed_user, as_of)), list(days),
    )


def _clean_shape(db_session, seed_user, seed_periods):
    """Build a shape that triggers NONE of the cash findings.

    One opening assertion inside the first period and no later true-up (so no
    assertion history to replay -- cash D3), no settled rows at all (so nothing
    can be attributed after the assertion -- cash D1), and every planned row due
    well after the read's as-of (so ruling R-G's clamp is a no-op).  On this
    shape the fold and the three producers are all answering the same question
    about the same facts, and must agree.

    Returns:
        The ``as_of`` the caller reads at.
    """
    add_txn(
        db_session, seed_user, seed_periods[3], "paycheck", "1500.00",
        is_income=True, due_date=date(2026, 2, 20),
    )
    add_txn(
        db_session, seed_user, seed_periods[4], "rent", "1200.00",
        due_date=date(2026, 3, 5),
    )
    add_txn(
        db_session, seed_user, seed_periods[5], "undated bill", "75.00",
    )
    db_session.commit()
    return date(2026, 1, 2)


class TestACleanShapeAgreesOnEveryDay:
    """With no finding triggered, the fold is the producers' own answer.

    This is the half that proves the fold is not simply a DIFFERENT producer.
    Every divergence asserted in the next class would be worthless if the fold
    also disagreed with the shipping answer where the shipping answer is right.
    """

    def test_it_matches_the_daily_series_on_every_day(
        self, db, seed_user, seed_periods,
    ):  # pylint: disable=unused-argument
        """Every day from the anchor period's start to the horizon's end.

        The daily series is the only one of the three that is daily, so this is
        the comparison with no shape mismatch to allow for.
        """
        account = seed_user["account"]
        as_of = _clean_shape(db.session, seed_user, seed_periods)
        first_day, last_day = seed_periods[0].start_date, last_covered_day(seed_periods[9])
        days = _days(first_day, last_day)
        assert len(days) == 140  # the loop is not vacuous

        series = balance_at.cash_daily_balance_series(
            account, _context(seed_user, as_of), first_day, last_day,
        )
        # Non-vacuity: two producers that both answered one flat number every
        # day would satisfy the equality below while proving nothing.  The
        # shape's three planned rows must actually move the line.
        assert len(set(series.values())) == 4, "the shape does not move"

        folded = _folded(seed_user, account, as_of, days)
        assert {day: folded[day] for day in days} == dict(series)

    def test_it_matches_the_scalar_at_every_period_end(
        self, db, seed_user, seed_periods,
    ):  # pylint: disable=unused-argument
        """Every period end in the horizon, not a sample of them.

        A period END is where the period-flat scalar and a daily ramp are
        defined to coincide; inside a period they cannot (finding cash D2, whose
        magnitude the next class measures).
        """
        account = seed_user["account"]
        as_of = _clean_shape(db.session, seed_user, seed_periods)
        ctx = _context(seed_user, as_of)
        ends = [last_covered_day(period) for period in seed_periods]
        assert len(ends) == 10  # the loop is not vacuous

        folded = _folded(seed_user, account, as_of, ends)
        assert len(set(folded.values())) == 4, "the shape does not move"
        for end in ends:
            assert folded[end] == balance_at.cash_balance_at(
                account, ctx, end,
            ), f"diverged at period end {end}"

    def test_it_matches_the_period_map_everywhere_the_map_answers(
        self, db, seed_user, seed_periods,
    ):  # pylint: disable=unused-argument
        """Every period, since the map is TOTAL over the periods it is given.

        The map is keyed by period.  It used to omit pre-anchor periods
        entirely; since plan step X-c2b2 every requested period is present, and
        the anchor here is the first period anyway, so it answers for all ten.
        """
        account = seed_user["account"]
        as_of = _clean_shape(db.session, seed_user, seed_periods)
        ctx = _context(seed_user, as_of)

        mapped = balance_at.cash_balance_map(account, ctx)
        assert len(mapped) == 10  # the loop is not vacuous
        assert len(set(mapped.values())) == 4, "the shape does not move"

        folded = _folded(
            seed_user, account, as_of,
            [last_covered_day(period) for period in seed_periods],
        )
        for period in seed_periods:
            assert folded[last_covered_day(period)] == mapped[period.id], (
                f"diverged at period {period.id} "
                f"({last_covered_day(period)})"
            )


class TestEveryFindingIsClosedAtTheSeam:
    """Each shape that used to break a producer now reads the fold, to the cent.

    Every fixture and every hand-computed figure here is the one plan step X-b
    measured the DIVERGENCE with.  What moved is the assertion: the seam now
    answers the fold's number instead of the defective one, and each docstring
    records both so the cutover's effect is legible from the test alone.
    """

    def test_cash_d1_the_settled_row_the_producers_count_nowhere(
        self, db, seed_user, seed_periods,
    ):  # pylint: disable=unused-argument
        """A settle attributed after the assertion is counted (cash D1 closed).

        The developer's reported bug.  Assertion $5,644.27 on 2026-03-01; a
        $2,000.00 expense settles 2026-04-01, a month later.

        Hand-computed: every entry reads $3,644.27 from 04-01.  Before the
        cutover all three answered $5,644.27 -- the anchor, unmoved, because a
        settled row contributed zero to the projection and the anchor predated
        it -- so the $2,000.00 the account had actually spent was counted by NO
        producer.  On production that class was $53,880.81 gross across 130 rows
        in 45 assertion gaps.
        """
        account = seed_user["account"]
        as_of = date(2026, 4, 5)
        append_balance_assertion(
            db.session, account, Decimal("5644.27"),
            _instant(2026, 3, 1),
        )
        create_settled_cash_transaction(
            seed_user, seed_periods[6], Decimal("2000.00"),
            settled_on=date(2026, 4, 1), name="the transfer out",
        )
        db.session.commit()

        ctx = _context(seed_user, as_of)
        days = _days(date(2026, 4, 1), date(2026, 4, 9))
        assert len(days) == 9  # the loop is not vacuous
        folded = _folded(seed_user, account, as_of, days)
        series = balance_at.cash_daily_balance_series(
            account, ctx, days[0], days[-1],
        )
        for day in days:
            # $5,644.27 - $2,000.00: the settled row, counted from the day its
            # money moved.  The retired producers all answered $5,644.27 here.
            assert folded[day] == Decimal("3644.27")
            assert series[day] == Decimal("3644.27")
            assert balance_at.cash_balance_at(
                account, ctx, day,
            ) == Decimal("3644.27")

        mapped = balance_at.cash_balance_map(account, ctx)
        assert mapped[seed_periods[6].id] == Decimal("3644.27")
        assert folded[last_covered_day(seed_periods[6])] == Decimal("3644.27")

    def test_cash_d2_the_scalar_steps_on_the_day_the_money_moves(
        self, db, seed_user, seed_periods,
    ):  # pylint: disable=unused-argument
        """The scalar is date-precise, not period-flat (cash D2 closed).

        A $1,200.00 bill due 2026-04-08, inside period 6 (2026-03-27 .. 04-09).

        Hand-computed: the scalar holds $1,000.00 until 04-07 and steps to
        -$200.00 on 04-08, exactly as the daily series does.  Before the cutover
        it answered -$200.00 for EVERY day of the period including 03-27 --
        twelve days before the money moves -- while the daily series ramped, so
        two shipping producers gave one question two answers ($15.96 apart on
        the real Checking account, $246.36 at the worst day of its current
        period).  They are one fold read at two grains now.
        """
        account = seed_user["account"]
        as_of = date(2026, 3, 27)
        add_txn(
            db.session, seed_user, seed_periods[6], "big bill", "1200.00",
            due_date=date(2026, 4, 8),
        )
        db.session.commit()

        ctx = _context(seed_user, as_of)
        days = _days(seed_periods[6].start_date, last_covered_day(seed_periods[6]))
        assert len(days) == 14  # the loop is not vacuous
        folded = _folded(seed_user, account, as_of, days)
        series = balance_at.cash_daily_balance_series(
            account, ctx, days[0], days[-1],
        )
        for day in days:
            expected = (
                Decimal("1000.00") if day < date(2026, 4, 8)
                else Decimal("-200.00")
            )
            assert folded[day] == expected, f"fold wrong on {day}"
            assert series[day] == expected, f"daily series wrong on {day}"
            # ...and the scalar steps with them rather than answering the
            # period-END value on all fourteen days.
            assert balance_at.cash_balance_at(
                account, ctx, day,
            ) == expected, f"scalar wrong on {day}"

    def test_cash_d3_a_past_date_reads_the_assertion_in_force_then(
        self, db, seed_user, seed_periods,
    ):  # pylint: disable=unused-argument
        """A past date is replayed, not fabricated or omitted (cash D3 closed).

        Two assertions: $1,000.00 opening (2026-01-01) and $5,644.27
        (2026-03-01).  Read across January and February, dates the account
        demonstrably held $1,000.00 at.

        Hand-computed: every entry answers $1,000.00 -- the assertion in force
        then.  Before the cutover the scalar answered $5,644.27, a balance the
        account did not hold until a month later (on production it fabricated
        $2,932.41 for 2026-06-03), and the period map omitted the pre-anchor
        periods entirely -- two producers, two different wrong answers.  Every
        assertion is replayed now, so a past column carries a real balance.
        """
        account = seed_user["account"]
        as_of = date(2026, 4, 5)
        append_balance_assertion(
            db.session, account, Decimal("5644.27"),
            _instant(2026, 3, 1),
        )
        db.session.commit()

        ctx = _context(seed_user, as_of)
        days = _days(date(2026, 1, 2), date(2026, 2, 28))
        assert len(days) == 58  # the loop is not vacuous
        folded = _folded(seed_user, account, as_of, days)
        for day in days:
            assert folded[day] == Decimal("1000.00"), f"fold wrong on {day}"
            # The retired scalar answered $5,644.27 on every one of these days.
            assert balance_at.cash_balance_at(
                account, ctx, day,
            ) == Decimal("1000.00"), f"scalar wrong on {day}"

        # The retired map omitted these four periods entirely; every requested
        # period is present now, carrying the balance in force then.
        mapped = balance_at.cash_balance_map(account, ctx)
        for period in seed_periods[:4]:
            assert mapped[period.id] == Decimal("1000.00")
            assert folded[last_covered_day(period)] == Decimal("1000.00")

    def test_ruling_r_g_the_overdue_bill_the_producers_keep_in_the_past(
        self, db, seed_user, seed_periods,
    ):  # pylint: disable=unused-argument
        """An overdue projected bill lands tomorrow, not on its stale due date.

        A $50.00 bill due 2026-03-29, read at ``as_of = 2026-04-02``.

        Hand-computed: the clamp lands it on 04-03, so 04-02 reads $1,000.00 and
        04-03 reads $950.00 -- on the fold AND on the daily series, which used
        to land it on its stale 03-29 due date, five days early.  Both reach
        $950.00 by the period end, so the divergence was confined to the window
        between the stale due date and ``as_of + 1``; what makes the ruling
        load-bearing is not that window but what happens at the NEXT re-anchor,
        which under the rejected alternative would absorb the bill and delete it
        from the projection entirely (one re-anchor every 2.3 days on the real
        Checking account).
        """
        account = seed_user["account"]
        as_of = date(2026, 4, 2)
        add_txn(
            db.session, seed_user, seed_periods[6], "overdue bill", "50.00",
            due_date=date(2026, 3, 29),
        )
        db.session.commit()

        ctx = _context(seed_user, as_of)
        days = _days(date(2026, 3, 27), date(2026, 4, 9))
        assert len(days) == 14  # the loop is not vacuous
        folded = _folded(seed_user, account, as_of, days)
        series = balance_at.cash_daily_balance_series(
            account, ctx, days[0], days[-1],
        )
        for day in days:
            assert folded[day] == (
                Decimal("1000.00") if day < date(2026, 4, 3)
                else Decimal("950.00")
            ), f"fold wrong on {day}"
            assert series[day] == (
                Decimal("1000.00") if day < date(2026, 4, 3)
                else Decimal("950.00")
            ), f"daily series wrong on {day}"
        assert folded[last_covered_day(seed_periods[6])] == series[
            last_covered_day(seed_periods[6])
        ]
