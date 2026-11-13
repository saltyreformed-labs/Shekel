"""Accrual: its window, the assertion that always wins, the daily grain, the cent carry."""

from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

import pytest

from app.models.interest_params import InterestParams
from app import ref_cache
from app.enums import CompoundingFrequencyEnum
from app.services.balance_at._context import BalanceContext
from tests._test_helpers import (
    account_never_asserted,
    append_balance_assertion,
    create_hysa_account,
    create_settled_cash_transaction,
    make_appreciating_account,
    reassert_balance_on,
    settle_instant_on,
)

from ._helpers import _ZERO, _ctx, _fold, _view, _params_for, _401k


def _instant(year, month, day, hour=12):
    """Return an aware-UTC instant, for pinning assertion / settle moments."""
    return datetime(year, month, day, hour, tzinfo=timezone.utc)


class TestTheAccrualWindow:
    """Rulings R-L / R-Y: ACCRUAL runs from the LATEST assertion's own day.

    Everything at or before that assertion is a bank FACT the user typed in, so
    modelling across those days adds money the assertion already contains.  The
    INTEREST half has shipped since plan step X-c2a; ruling R-Y is the
    generalisation, and it was a figure move: before it an INVESTMENT's growth
    started the period AFTER its anchor period (``period_index > anchor_idx``;
    ``anchor_idx`` is gone) and a Property's did too, so that period earned nothing.
    """

    def test_an_hysa_accrues_from_its_assertions_own_day(
        self, db, seed_user, seed_periods,
    ):
        """$10,000 at 5% APY, asserted on period 0's first day, accrues 14 days.

        Hand-computed.  Daily compounding at 5% is a rate of ``0.05 / 365 =
        0.000136986...`` a day, applied to the running balance and credited in
        whole cents off a full-precision total (ruling R-X)::

            01-02  10000.00 * r = 1.36986  cum 1.36986 -> credit 1.37
            01-03  10001.37 * r = 1.37005  cum 2.73991 -> credit 1.37
            ...    (1.37 a day, with 1.38 on 01-10 and 01-15 where the
                    full-precision total crosses the next half-cent)
            01-15  the cumulative accrual is 19.20

        so period 0 (2026-01-02 .. 2026-01-15) ends at $10,019.20.  The
        assertion's OWN day accrues, which is ruling R-L's sharpening -- the
        exact analogue of the day-count convention every period already uses.
        """
        account = create_hysa_account(
            seed_user, db.session, seed_periods[0], Decimal("10000.00"),
            apy=Decimal("0.05000"),
        )
        ctx = _ctx(seed_user)

        columns = _view(account, ctx, seed_periods[:1])
        assert columns[seed_periods[0].id].balance == Decimal("10019.20")
        assert columns[seed_periods[0].id].accrual == Decimal("19.20")
        assert columns[seed_periods[0].id].contribution == Decimal("0.00")
        # ...so the balance less its own accrual is the asserted $10,000.00.
        column = columns[seed_periods[0].id]
        assert column.balance - column.accrual == Decimal("10000.00")

    def test_a_mid_period_assertion_accrues_only_its_remaining_days(
        self, db, seed_user, seed_periods,
    ):
        """The same account asserted on 01-09 accrues 7 days, not 14.

        Hand-computed from the same daily table: seven credits of $1.37 sum to
        $9.59, so period 0 ends at $10,009.59.  This is the shape ruling R-L
        exists for -- before it, accrual opened at the anchor PERIOD's start,
        which can precede the assertion by up to 13 days and model interest
        across days the assertion already contains.
        """
        account = create_hysa_account(
            seed_user, db.session, seed_periods[0], Decimal("10000.00"),
            apy=Decimal("0.05000"),
        )
        reassert_balance_on(
            db.session, account, _instant(2026, 1, 9),
        )
        db.session.commit()
        ctx = _ctx(seed_user)

        columns = _view(account, ctx, seed_periods[:1])
        assert columns[seed_periods[0].id].balance == Decimal("10009.59")
        assert columns[seed_periods[0].id].accrual == Decimal("9.59")

    def test_an_investments_anchor_period_accrues(
        self, seed_user, seed_periods,
    ):
        """Ruling R-Y: a 401(k)'s anchor period earns its own days.

        Hand-computed.  $20,000 at a 7% assumed annual return is a daily rate of
        ``1.07 ** (1/365) - 1 = 0.000185383...``, so the running balance credits
        $3.71 a day (with $3.70 / $3.72 where the full-precision total crosses a
        half-cent) and the 14 days of period 0 accrue **$51.97**, ending at
        **$20,051.97**.

        The retired map read that period as a flat $20,000.00: it split its
        periods on ``period_index > anchor_idx``, so the anchor period was
        served by the flat cash base and growth started the period after.  On
        the real data that silently dropped $105.26 on the Roth IRA, $44.95 on
        the Traditional IRA and $76.59 on the Empower 401(k) -- and it recurred
        every time the user re-asserted, which they do every few weeks.
        """
        account = _401k(
            seed_user, seed_periods[0], Decimal("20000.00"),
            opened_on=date(2026, 1, 2),
        )
        ctx = _ctx(seed_user)

        columns = _view(
            account, ctx, seed_periods[:1], params=_params_for(account),
        )
        assert columns[seed_periods[0].id].balance == Decimal("20051.97")
        assert columns[seed_periods[0].id].accrual == Decimal("51.97")

    def test_a_property_accrues_from_its_assertions_own_day(
        self, db, seed_user, seed_periods,
    ):
        """Ruling R-Y again, on the third modelled kind.

        Hand-computed.  A $100,000 market value at 3% appreciation is a daily
        rate of ``1.03 ** (1/365) - 1 = 0.000080986...``, so the value credits
        $8.10 a day and 14 days accrue **$113.44** (the fourteenth credit is
        $8.11, where the full-precision total crosses the next half-cent),
        ending at **$100,113.44**.  Asserted mid-period on 01-09, only the
        remaining seven days accrue: ``7 * 8.10 =`` **$56.70**.
        """
        account = make_appreciating_account(
            seed_user, db.session, seed_periods[0], Decimal("100000.00"),
            Decimal("0.03000"),
        )
        reassert_balance_on(
            db.session, account, settle_instant_on(date(2026, 1, 2)),
        )
        db.session.commit()
        assert _view(account, _ctx(seed_user), seed_periods[:1])[
            seed_periods[0].id
        ].accrual == Decimal("113.44")

        reassert_balance_on(
            db.session, account, _instant(2026, 1, 9),
        )
        db.session.commit()
        # A FRESH read pass, because the assertion above is a WRITE and a pass
        # is a memo whose lifetime is the one read it was built for -- the rule
        # ``BalanceContext``'s own docstring states, and the shape every write
        # path in ``app/`` already takes.  Reusing the first pass here read the
        # pre-write fold from plan step X-i4 on, which is the memo working:
        # until then the cash fold was the one account-keyed derivation a pass
        # did NOT hold, so this was the only kind of test that could reuse one
        # across a commit and still see the new data.
        assert _view(account, _ctx(seed_user), seed_periods[:1])[
            seed_periods[0].id
        ].accrual == Decimal("56.70")

    def test_a_period_wholly_before_the_latest_assertion_accrues_nothing(
        self, db, seed_user, seed_periods,
    ):
        """A later assertion closes the window on every period before it.

        The HYSA opens 2026-01-02 at $10,000.00 and the user asserts
        $10,500.00 on 2026-02-13 (period 3's first day).  Periods 0-2 are then
        wholly at or before the LATEST assertion, so they accrue $0.00 and read
        the opening's own $10,000.00 -- the balance the user asserted, not a
        model of it.  Period 3 accrues its own 14 days.
        """
        account = create_hysa_account(
            seed_user, db.session, seed_periods[0], Decimal("10000.00"),
            apy=Decimal("0.05000"),
        )
        append_balance_assertion(
            db.session, account, "10500.00",
            _instant(2026, 2, 13),
        )
        db.session.commit()
        ctx = _ctx(seed_user)

        columns = _view(account, ctx, seed_periods[:4])
        for period in seed_periods[:3]:
            assert columns[period.id].accrual == Decimal("0.00")
            assert columns[period.id].balance == Decimal("10000.00")
        assert columns[seed_periods[3].id].accrual > _ZERO

    # ``test_the_window_reads_the_dated_source_of_truth_not_the_cache`` was
    # DELETED at plan step X-f1c3c.  It corrupted ``account.current_anchor_*``
    # and asserted the accrual window did not move -- proving the window reads
    # the dated assertion rather than the denormalized column.  Ruling R-EH
    # deleted that column, so a divergent cache is not expressible and the test
    # had no subject: what it defended against is now impossible rather than
    # merely detected.  The window's real source is still graded by the case
    # below (no assertion -> fail loud) and by every accrual test in this class,
    # each of which opens its window from an assertion it wrote.

    def test_a_modelled_account_with_no_assertion_fails_loud(
        self, db, seed_user, seed_periods,
    ):
        """No assertion means no honest window, so the fold refuses.

        The deliberate asymmetry with the CASH fold, which answers such an
        account from a zero seed (the totality rule): an accrual needs a DATE to
        open on.  It is the same refusal ``cash_ledger.resolve_anchor`` makes,
        and the same production-unreachable state -- migration ``cfb15e782f86``
        plus the account factory guarantee every account an opening row.
        """
        # **Built rather than emptied** (plan step X-f3c-2c): an assertion is
        # append-only at the database tier, so the state is reachable only by
        # an account the assertion factory never touched.  It carries its
        # ``InterestParams`` so it still classifies INTEREST, which is what
        # puts the modelled fold in front of it.
        account = account_never_asserted(
            seed_user, db.session, name="Silent HYSA", type_name="HYSA",
            opening_equity=Decimal("10000.00"),
        )
        db.session.add(InterestParams(
            account_id=account.id,
            apy=Decimal("0.05000"),
            compounding_frequency_id=ref_cache.compounding_frequency_id(
                CompoundingFrequencyEnum.DAILY,
            ),
        ))
        db.session.commit()

        with pytest.raises(RuntimeError, match="zero AccountAnchorHistory"):
            _view(account, _ctx(seed_user), seed_periods[:1])


class TestAnAssertionAlwaysWins:
    """Ruling R-S: every assertion is replayed, and there is no backward model.

    The defect this closes is measured: the three real modelled accounts carry
    15 ``AccountAnchorHistory`` rows and ``build_investment_balance_map`` reads
    only the LATEST, re-deriving every earlier period from a growth curve that
    ``_merge_balance_sources`` then prefers -- $6,315.57 of rendered net-worth
    history contradicting the user's own bank facts at ONE period (N-74), and a
    FUTURE contribution rewriting a PAST balance (N-75).
    """

    def test_every_assertion_is_replayed_not_just_the_latest(
        self, db, seed_user, seed_periods,
    ):
        """Four assertions, four periods, four asserted balances.

        The real Roth IRA's shape, compressed: an opening plus three later
        assertions, each filed on the first day of its own period.  Periods 0-2
        read the assertion in force THEN -- $22,909.02, $23,851.08, $24,605.20
        -- exactly, with no model laid over them, because an assertion is a
        RESET the replay applies where it happened and the days between two
        assertions are FACTS the later one already records.  Today
        ``_reverse_project_periods`` re-derives all three from a growth curve
        that ``_merge_balance_sources`` then prefers, which is the $6,315.57 of
        contradicted history N-74 measured.

        Period 3 holds the LATEST assertion, so ruling R-Y's window opens inside
        it: hand-computed, $25,959.47 at 7% credits $4.81 a day and the 14 days
        from 02-13 accrue **$67.46**, ending at **$26,026.93**.
        """
        account = _401k(
            seed_user, seed_periods[0], Decimal("22909.02"),
            opened_on=date(2026, 1, 2),
        )
        for index, balance in (
            (1, "23851.08"), (2, "24605.20"), (3, "25959.47"),
        ):
            period = seed_periods[index]
            append_balance_assertion(
                db.session, account, balance,
                settle_instant_on(period.start_date),
            )
        db.session.commit()
        ctx = _ctx(seed_user)

        columns = _view(
            account, ctx, seed_periods[:4], params=_params_for(account),
        )
        assert [columns[p.id].balance for p in seed_periods[:3]] == [
            Decimal("22909.02"), Decimal("23851.08"), Decimal("24605.20"),
        ]
        assert [columns[p.id].accrual for p in seed_periods[:3]] == [
            Decimal("0.00"), Decimal("0.00"), Decimal("0.00"),
        ]
        assert columns[seed_periods[3].id].accrual == Decimal("67.46")
        assert columns[seed_periods[3].id].balance == Decimal("26026.93")

    def test_before_the_first_assertion_it_is_the_cash_folds_answer(
        self, db, seed_user, seed_periods,
    ):
        """Ruling R-I's back-projection, inherited unchanged -- no un-growing.

        A $50,000 401(k) asserted 2026-02-13 with a $5,000.00 contribution
        already recorded on 2026-01-20.  Hand-computed: the records at or before
        the assertion sum to +$5,000.00.  The books were DECLARED at
        $50,000.00 (``create_account`` records the balance its owner typed), so
        the fold reads $50,000.00 before 01-20, $55,000.00 from it, and
        $50,000.00 from the assertion's own day, which resets it.  No ACCRUAL
        exists in that region at all -- it is before the LATEST assertion --
        which is ruling R-S: the reverse growth projection leaves the balance
        path rather than becoming a direction.

        **The contribution sits BETWEEN the books and the assertion, and that
        is what changed at plan step X-f3c-2b** (finding **N-378**).  It used
        to be dated before the books OPENED, which made the $55,000.00 a
        genuine double count -- money already inside the declared $50,000.00,
        counted a second time from its own day.  Ruling **R-HG** deletes that
        state rather than the reading: books opening 2026-01-19 hold a
        01-20 contribution honestly, and the assertion's reset on 02-13 is then
        a real ``-$5,000.00`` correction rather than the healing of an error.
        **Every figure below is unchanged by that move**, which is the evidence
        the state was repaired and the fold was not.

        *This read $45,000.00 / $50,000.00 until X-f3c-2a, when the seed
        stopped being the first assertion back-computed over its own records
        (ruling R-I) and became the stored ``budget.account_openings`` figure.
        The old answer hid the contradiction by choosing a level that made it
        cancel; nothing recorded $45,000.00 either.*
        """
        account = _401k(
            seed_user, seed_periods[3], Decimal("50000.00"),
            opened_on=date(2026, 2, 13),
            books_open_on=date(2026, 1, 19),
        )
        create_settled_cash_transaction(
            seed_user, seed_periods[1], Decimal("5000.00"),
            account=account, is_income=True,
            settled_on=date(2026, 1, 20), name="contribution",
        )
        db.session.commit()
        ctx = _ctx(seed_user)

        folded = _fold(
            account, ctx,
            [date(2026, 1, 19), date(2026, 1, 20), date(2026, 2, 12)],
            params=_params_for(account),
        )
        assert folded[date(2026, 1, 19)] == Decimal("50000.00")
        assert folded[date(2026, 1, 20)] == Decimal("55000.00")
        assert folded[date(2026, 2, 12)] == Decimal("55000.00")


class TestTheDailyGrain:
    """Ruling R-T: ACCRUAL is DAILY, so a modelled balance answers a DATE.

    Finding N-71, measured at period 30 on the prod-shape clone: the shipping
    scalar returns the IDENTICAL value on a period's first and last day while
    $328.50 of growth accrues inside it, because the three modelled kinds
    resolve a date to its period and read a period-keyed map.
    """

    def test_the_balance_moves_inside_a_period(
        self, db, seed_user, seed_periods,
    ):
        """The $10,000 HYSA reads a different number on each day of period 0.

        Hand-computed from the same daily table as
        :meth:`TestTheAccrualWindow.test_an_hysa_accrues_from_its_assertions_own_day`:
        $10,001.37 on 01-02, $10,009.59 on 01-08 (seven credits), $10,019.20 on
        01-15 (fourteen).  A period-granular producer answers $10,019.20 on all
        three.
        """
        account = create_hysa_account(
            seed_user, db.session, seed_periods[0], Decimal("10000.00"),
            apy=Decimal("0.05000"),
        )
        ctx = _ctx(seed_user)

        folded = _fold(account, ctx, [
            date(2026, 1, 1), date(2026, 1, 2),
            date(2026, 1, 8), date(2026, 1, 15),
        ])
        assert folded[date(2026, 1, 1)] == Decimal("10000.00")
        assert folded[date(2026, 1, 2)] == Decimal("10001.37")
        assert folded[date(2026, 1, 8)] == Decimal("10009.59")
        assert folded[date(2026, 1, 15)] == Decimal("10019.20")

    def test_a_dates_answer_does_not_depend_on_the_others_asked_for(
        self, db, seed_user, seed_periods,
    ):
        """One date alone answers what it answers inside a 140-date request.

        The property a coarser grain cannot have: a segment-per-compounding-
        interval design would need a partial-accrual read for a date INSIDE a
        segment, and the answer would then be a function of where the caller
        happened to put its boundaries.  A step for every day has no inside.
        """
        account = create_hysa_account(
            seed_user, db.session, seed_periods[0], Decimal("10000.00"),
            apy=Decimal("0.05000"),
        )
        ctx = _ctx(seed_user)
        horizon = [
            seed_periods[0].start_date + timedelta(days=offset)
            for offset in range(140)
        ]

        together = _fold(account, ctx, horizon)
        for day in (date(2026, 1, 8), date(2026, 3, 3), date(2026, 5, 21)):
            assert _fold(account, ctx, [day])[day] == together[day]


class TestTheCentCarry:
    """Ruling R-X: accrue at full precision, credit whole cents, carry the rest.

    Every emitted step is a whole cent -- the property the per-period identity
    needs -- while the cumulative accrual at any date is ``round(exact)``, so
    the daily grain introduces no rounding bias of its own.
    """

    def test_a_sub_half_cent_daily_accrual_accumulates_into_a_cent(
        self, db, seed_user, seed_periods,
    ):
        """A $30.00 HYSA at 5% APY earns money; per-day rounding earns nothing.

        Hand-computed.  ``30.00 * 0.05 / 365 = 0.004110`` a day -- BELOW half a
        cent, so a rule that rounded each day independently would credit $0.00
        every day forever and the account would never grow.  Under the carry the
        full-precision total crosses each half-cent in turn::

            01-02  cum 0.00411 -> round 0.00   balance 30.00
            01-03  cum 0.00822 -> round 0.01   balance 30.01
            01-04  cum 0.01233 -> round 0.01   balance 30.01
            01-05  cum 0.01644 -> round 0.02   balance 30.02
            ...
            01-15  cum 0.05757 -> round 0.06   balance 30.06

        so period 0 ends at $30.06 with $0.06 of accrual.
        """
        account = create_hysa_account(
            seed_user, db.session, seed_periods[0], Decimal("30.00"),
            apy=Decimal("0.05000"),
        )
        ctx = _ctx(seed_user)

        folded = _fold(account, ctx, [
            date(2026, 1, 2), date(2026, 1, 3),
            date(2026, 1, 4), date(2026, 1, 5), date(2026, 1, 15),
        ])
        assert folded[date(2026, 1, 2)] == Decimal("30.00")
        assert folded[date(2026, 1, 3)] == Decimal("30.01")
        assert folded[date(2026, 1, 4)] == Decimal("30.01")
        assert folded[date(2026, 1, 5)] == Decimal("30.02")
        assert folded[date(2026, 1, 15)] == Decimal("30.06")

    def test_the_cumulative_accrual_is_the_rounded_exact_total(
        self, db, seed_user, seed_periods,
    ):
        """14 days on $10,000 accrue $19.20, not 14 rounded credits of $1.37.

        Hand-computed both ways so the difference is the assertion.  The exact
        14-day total is $19.1988...  -> **$19.20**.  Rounding each day
        independently would credit $1.37 fourteen times over -- **$19.18** --
        and the gap widens with the horizon: on the real Fidelity Savings over
        840 days the two rules differ by $0.10, and on the Money Market by
        $0.09.
        """
        account = create_hysa_account(
            seed_user, db.session, seed_periods[0], Decimal("10000.00"),
            apy=Decimal("0.05000"),
        )
        ctx = _ctx(seed_user)

        accrued = _view(account, ctx, seed_periods[:1])[
            seed_periods[0].id
        ].accrual
        assert accrued == Decimal("19.20")
        assert accrued != Decimal("1.37") * 14

    def test_a_period_accrual_sums_to_the_balance_it_lifted(
        self, db, seed_user, seed_periods,
    ):
        """Across ten periods, accrual telescopes into the balance exactly.

        Every step being a whole cent is what makes this exact rather than
        within-a-cent: the reported accrual per period sums to the difference
        between the final balance and the balance the account was ASSERTED at,
        with no residue.  (This read the retired ``balance_without_accrual``
        field until plan step X-g2b; ruling R-AE took that field with the
        pre-growth-seed idea it belonged to, and the assertion is the honest
        right-hand side for an account holding no other rows.)
        """
        account = create_hysa_account(
            seed_user, db.session, seed_periods[0], Decimal("10000.00"),
            apy=Decimal("0.05000"),
        )
        ctx = _ctx(seed_user)

        columns = _view(account, ctx, seed_periods)
        last = columns[seed_periods[-1].id]
        assert sum(
            (column.accrual for column in columns.values()), Decimal("0.00"),
        ) == last.balance - Decimal("10000.00")

    def test_a_monthly_account_accrues_on_its_own_calendar_divisor(
        self, db, seed_user, seed_periods,
    ):
        """MONTHLY compounding, through the PRODUCER -- not just the engine.

        **Added at plan step X-g4b, closing a gap the deletion exposed rather
        than created.**  ``create_hysa_account`` hardcoded DAILY, so no test
        anywhere ran a MONTHLY or QUARTERLY account through a balance producer:
        the frequency was graded only against ``accrued_interest`` directly
        (``test_interest_projection.py``) and through a form round-trip
        (``test_hysa.py``).  A regression hardcoding DAILY in the replay's rate
        resolver (:func:`._asset_fold._modelled_return`) would have passed the
        whole suite -- and the developer's real Money Market compounds MONTHLY,
        which ``_InterestAccrual`` records in its own docstring.

        Hand-computed, and the divisor is the point.  MONTHLY is SIMPLE
        interest inside the month, so one day is
        ``balance x (apy / 12) / days_in_month`` -- not the DAILY rule's
        ``apy / 365`` compounded.  On $10,000.00 at 3.29% APY, both seeded
        periods lie wholly inside JANUARY (31 days), so each day accrues
        ``balance x 0.0329 / 12 / 31``.  Credited in whole cents off a
        full-precision running total (ruling R-X)::

            period 0 (2026-01-02 .. 01-15, 14 days): $12.39 -> $10,012.39
            period 1 (2026-01-16 .. 01-29, 14 days): $12.40 -> $10,024.79

        Under the DAILY rule the same window earns $12.63 in period 0, so the
        assertion discriminates the two divisors rather than merely the rate.
        """
        account = create_hysa_account(
            seed_user, db.session, seed_periods[0], Decimal("10000.00"),
            apy=Decimal("0.03290"),
            compounding=CompoundingFrequencyEnum.MONTHLY,
        )
        ctx = _ctx(seed_user)

        columns = _view(account, ctx, seed_periods[:2])
        assert columns[seed_periods[0].id].accrual == Decimal("12.39")
        assert columns[seed_periods[0].id].balance == Decimal("10012.39")
        assert columns[seed_periods[1].id].accrual == Decimal("12.40")
        assert columns[seed_periods[1].id].balance == Decimal("10024.79")

    def test_it_does_not_drift_over_a_production_scale_horizon(
        self, db, seed_user, seed_periods_52,
    ):
        """52 periods of compounding telescope EXACTLY and never lose a cent.

        The long-horizon no-drift oracle, ported at plan step **X-g4b** from
        ``test_interest_accrual.py``'s ``test_hysa_26_period_compounding_no_``
        ``drift``, which died with the per-PERIOD layer it graded.  The claim
        transfers directly and gets stronger with the grain: over a full 2-year
        projection the per-period accruals must sum to the balance change with
        NO residue, which is only possible if every one of the ~730 daily steps
        is a whole cent (ruling R-X's carry).

        **MONOTONICITY is the arm that discriminates, and the telescope beside
        it does not** -- measured, not reasoned.  Each period's accrual must
        STRICTLY EXCEED the one before it, which is what compounding means: a
        balance that only grows must earn more each period.  Three one-line
        production mutations were run against this test and all three fail on
        exactly that assertion, none on the telescope: crediting each day's
        accrual rounded INDEPENDENTLY (the defect ruling R-X exists to stop --
        it plateaus at $19.18), accruing on a STALE period-start base (also
        $19.18), and a uniformly halved rate ($9.61).

        **The telescope arm is a consistency check, NOT evidence, and saying so
        is the point.**  ``sum(accruals) == balance change`` holds under all
        three of those mutations, because ``_resolve_days`` builds the running
        balance by adding exactly the steps it records -- the identity is
        arithmetic, and :mod:`app.services.balance_at._asset_fold` says so
        itself ("it holds BY CONSTRUCTION rather than as an invariant a test
        polices").  It is kept because it costs nothing and would catch a
        future producer that stopped deriving the two from one step list; it is
        not what makes this test a control.

        **What monotonicity does NOT catch, stated so the boundary is known:**
        a rate wrong by a factor small enough that each period still out-earns
        the last.  That is pinned by
        :meth:`test_the_cumulative_accrual_is_the_rounded_exact_total` in this
        same class, whose $19.20 over 14 days is hand-computed against the
        APY -- so the two together pin the RATE and its long-horizon BEHAVIOUR
        separately, which is the split this package uses everywhere.

        No sampling: every one of the 52 columns participates in both arms.
        """
        account = create_hysa_account(
            seed_user, db.session, seed_periods_52[0], Decimal("10000.00"),
            apy=Decimal("0.05000"),
        )
        ctx = BalanceContext.build(
            seed_user["user"].id, as_of=date(2028, 12, 31),
        )

        columns = _view(account, ctx, seed_periods_52)
        assert len(columns) == 52

        accruals = [columns[period.id].accrual for period in seed_periods_52]
        last = columns[seed_periods_52[-1].id]
        assert sum(accruals, Decimal("0.00")) == (
            last.balance - Decimal("10000.00")
        )
        for index in range(1, len(accruals)):
            assert accruals[index] > accruals[index - 1], (
                f"period {index} accrued {accruals[index]} against "
                f"{accruals[index - 1]} the period before -- a compounding "
                "balance must earn strictly more each period"
            )
