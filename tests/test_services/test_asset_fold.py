"""X-g1: the MODELLED fold, graded on a hand-computed oracle.

Plan step X-g1 (``docs/audits/balance_architecture/README.md``).  Grades
``app.services.balance_at._asset_fold`` -- the replay plan step X-g2 will point
the three modelled kinds at.  The fold is ADDITIVE here: no production surface
reads it yet, so nothing in this file can move a shipped balance.

**Every expected figure below is HAND-COMPUTED and written out in the test that
asserts it.**  None is taken from a shipping producer, and that is not a style
preference: the shipping producers are WRONG about exactly the cases this file
exists for -- the retired three-source merge rendered $6,315.57 of
net-worth history that contradicts the user's own recorded assertions (findings
N-43 / N-74), and the three modelled kinds answer a DATE with a PERIOD (N-71).
Grading the replay against them would prove the defect rather than the fix
(plan Section 7.2, finding N-7).  The every-period parallel run against those
producers is a separate file (``test_asset_fold_parallel.py``), where DIVERGENCE
is the expected result and each one is classified.

Six rulings are graded here, each with a control a wrong implementation fails:

* **R-L / R-Y** -- ACCRUAL exists only from the LATEST assertion's own day
  forward, for ALL modelled kinds (:class:`TestTheAccrualWindow`).  R-Y is the
  half that is new: an INVESTMENT's or a Property's anchor PERIOD accrues today
  from nothing at all.
* **R-S** -- an ASSERTION always wins and there is no backward model
  (:class:`TestAnAssertionAlwaysWins`).
* **R-T** -- the grain is DAILY, so a balance moves inside a period and a date's
  answer never depends on which other dates were asked for
  (:class:`TestTheDailyGrain`).
* **R-X** -- the cent carry: full-precision accrual, whole-cent crediting
  (:class:`TestTheCentCarry`).  Its sharpest case is the one per-day rounding
  gets permanently wrong -- a sub-half-cent daily accrual.
* **R-R** -- a contribution is partitioned by SOURCE, so a recorded transfer is
  never modelled a second time (:class:`TestTheContributionTier`).
* **R-Z** -- a modelled contribution lands on its payday and stops STRICTLY
  before the latest assertion (:class:`TestTheContributionTier`).
"""

from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

import pytest

from app.extensions import db
from app.models.interest_params import InterestParams
from app.models.paycheck_line import PaycheckLine
from app import ref_cache
from app.enums import (
    CalcMethodEnum,
    CompoundingFrequencyEnum,
    PaycheckLineKindEnum,
)
from app.services.balance_at import (
    _asset_contributions,
    _asset_fold,
    _cash_fold,
    _cash_periods,
)
from app.services.balance_at._asset_contributions import ContributionInputs
from app.services.investment_projection import AccountPayrollFeed
from app.services.pay_calendar import calendar_for
from app.services.balance_at._context import BalanceContext
from app.services.cash_ledger import ReconciledThrough
from app.services.pay_calendar import DerivedPeriod, PeriodWindow
from app.services.projection_inputs import (
    load_payroll_feeds,
    load_investment_params_for_accounts,
)
from tests._test_helpers import (
    account_never_asserted,
    append_balance_assertion,
    create_hysa_account,
    create_savings_account,
    create_settled_cash_transaction,
    create_settled_transfer,
    derived_span,
    last_covered_day,
    make_appreciating_account,
    make_investment_account,
    make_salary_profile,
    period_window,
    pricing_over,
    reassert_balance_on,
    restate_account_opening,
    settle_instant_on,
)

# An as-of far past every valuation date in this file, so the PLANNED tier
# (which clamps to ``as_of + 1``, ruling R-G) cannot reach any date asserted
# here.  The modelled tiers are graded on their own, exactly as the cash fold's
# oracle grades its tiers apart: a test that mixed them could pass with either
# one wrong.
_LATE_AS_OF = date(2026, 12, 31)

_ZERO = Decimal("0")


def _instant(year, month, day, hour=12):
    """Return an aware-UTC instant, for pinning assertion / settle moments."""
    return datetime(year, month, day, hour, tzinfo=timezone.utc)


def _ctx(seed_user, as_of=_LATE_AS_OF):
    """Return a read-pass context for the seed user at *as_of*."""
    return BalanceContext.build(seed_user["user"].id, as_of=as_of)


def _inputs(params=None, feed=None):
    """Bundle a case's contribution feed the way the seam's callers do."""
    return ContributionInputs(
        investment_params=params,
        feed=AccountPayrollFeed.absent() if feed is None else feed,
    )


def _fold(account, ctx, dates, *, params=None, feed=None):
    """Fold *account* at each of *dates*, returning ``{date: Decimal}``."""
    return _asset_fold.fold_asset_balances(
        account, ctx, list(dates), _inputs(params, feed),
    )


def _view(account, ctx, periods, *, params=None, feed=None):
    """Return *account*'s modelled per-period columns."""
    return _asset_fold.asset_period_view(
        account, ctx, _inputs(params, feed),
    )


def _growth(account, ctx, as_of, *, params=None, feed=None):
    """Return *account*'s ``(accrual, contribution)`` through *as_of*."""
    return _asset_fold.asset_growth_at(
        account, ctx, as_of, _inputs(params, feed),
    )


def _feed_for(seed_user, account, params=None):
    """Return the account's payroll feed, PRICED as the seam's loader prices it.

    ``ContributionInputs`` holds an
    :class:`~app.services.investment_projection.AccountPayrollFeed` since plan
    step **salary:R14-b** (ruling **R-SAL2**), and it is built by the real
    loader here rather than by hand: what a deduction takes from a paycheck is
    the paycheck ENGINE's answer, so a hand-built feed would grade this file's
    arithmetic instead of the engine's.  It replaced an ``adapt_deductions``
    call that flattened the ORM rows into a shape whose per-period figure this
    tier then re-derived, raise-blind (finding **D45**).

    ``params`` is passed through so the EMPLOYER half is priced too: the gross
    an employer percentage is taken of comes off the profile
    ``investment_params.salary_profile_id`` names (**R-SAL5**), so a case that
    wants employer money needs the link its own ``_salaried_deduction`` wrote.
    """
    user_id = seed_user["user"].id
    return load_payroll_feeds(
        pricing_over(calendar_for(user_id)), [account.id],
        {} if params is None else {account.id: params},
    )[account.id]


def _flat_feed(amount, gross=None):
    """A feed paying *amount* on EVERY period it is asked about.

    For the cases that drive :func:`_asset_contributions._dated_events`
    directly over periods no owner has -- the calendar-year limit rules, which
    need a schedule spanning New Year.  The engine cannot price a paycheck for
    a calendar nobody holds, and these cases are about the LIMIT walk rather
    than about what a paycheck pays, so the feed is stated rather than priced.
    Flat over whatever period the fold asks, which is what a stated feed over
    a map WAS before plan step salary:S3-e-2 (a flat map held its own figure
    past its keys); a resolver says so directly.
    """
    return AccountPayrollFeed(
        employee=lambda period: Decimal(amount),
        gross=None if gross is None else (lambda period: Decimal(gross)),
    )


def _params_for(account):
    """Return the account's ``InvestmentParams`` through the shared loader."""
    return load_investment_params_for_accounts([account])[account.id]


def _401k(seed_user, period, balance, *, opened_on, books_open_on=None,
          **kwargs):
    """Build a 7%-return 401(k) whose OPENING assertion is on a CHOSEN day.

    ``make_investment_account`` now pins its own opening to the anchor period's
    first day (finding N-77, closed at plan step X-g2a), so this wrapper is no
    longer a compensator for a wall-clock stamp -- it exists because this file's
    whole subject is WHERE an accrual window opens, and several cases need that
    day to be somewhere other than the period's start (mid-period, or on a later
    period's payday).

    Args:
        seed_user: The ``seed_user`` fixture dict.
        period: The anchor period to build against.
        balance: The declared balance.
        opened_on: The civil day the opening ASSERTION is dated.
        books_open_on: The civil day the account's BOOKS open, when a case
            records money moving before the assertion.  ``None`` leaves them
            where ``reassert_balance_on`` puts them, one day before
            *opened_on* -- which is right until a case needs the span between
            the two (plan step X-f3c-2b, ruling **R-HG**: nothing may be dated
            on or before the books).
        **kwargs: Passed through to ``make_investment_account``.

    Returns:
        The created 401(k) account.
    """
    account = make_investment_account(
        seed_user, db.session, period, balance, **kwargs,
    )
    reassert_balance_on(
        db.session, account, settle_instant_on(opened_on),
    )
    if books_open_on is not None:
        restate_account_opening(db.session, account, books_open_on)
    db.session.commit()
    return account


def _salaried_deduction(seed_user, account, amount):
    """Attach an active flat-dollar deduction targeting *account*.

    Module-level rather than a method on the contribution class: three classes
    now build the same feed, and the modelled tier is only LIVE when one exists
    -- a fixture without it cannot tell a partition from a union (finding N-69,
    which is how the first version of the R-R pin was found vacuous).

    **It also records which job FUNDS the account** (plan step
    **salary:R14-b**, ruling **R-SAL5**), which is what
    ``salary:R14-a``'s backfill wrote for exactly this shape: an account with a
    deduction naming it is funded by that deduction's own profile.  Without
    the link the employer half models nothing at all (the developer's
    2026-09-04 ruling), so a case asserting an employer figure would assert
    ``$0.00`` and pass for the wrong reason.  The profile pays
    ``$94,425.24 / 26 = $3,631.74`` a paycheck, which is the gross those cases
    size a percentage off.
    """
    profile = make_salary_profile(
        seed_user, db.session, pay=Decimal("3631.74"),  # $94,425.24 a year / 26
    )
    db.session.flush()
    deduction = PaycheckLine(
        salary_profile_id=profile.id,
        target_account_id=account.id,
        name="401k deferral",
        amount=Decimal(amount),
        calc_method_id=ref_cache.calc_method_id(CalcMethodEnum.FLAT),
        paycheck_line_kind_id=ref_cache.paycheck_line_kind_id(
            PaycheckLineKindEnum.PRE_TAX_DEDUCTION,
        ),
        is_active=True,
    )
    db.session.add(deduction)
    params = load_investment_params_for_accounts([account]).get(account.id)
    if params is not None:
        params.salary_profile_id = profile.id
    db.session.commit()
    return deduction


class TestTheAccrualWindow:
    """Rulings R-L / R-Y: ACCRUAL runs from the LATEST assertion's own day.

    Everything at or before that assertion is a bank FACT the user typed in, so
    modelling across those days adds money the assertion already contains.  The
    INTEREST half has shipped since plan step X-c2a; ruling R-Y is the
    generalisation, and it is a figure move: today an INVESTMENT's growth starts
    the period AFTER its anchor period (``period_index > anchor_idx``) and a
    Property's does too, so the anchor period earns nothing at all.
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
            seed_user, db.session, seed_periods[1], Decimal("5000.00"),
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
        separately, which is the split this file uses everywhere.

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


class TestTheContributionTier:
    """Rulings R-R / R-Z: a contribution is partitioned by SOURCE, dated at payday.

    A recorded transfer HAS a transaction row, so it is already an ACTUAL /
    PLANNED event in the cash tiers underneath; a payroll deduction never has
    one, so it is a modelled CONTRIBUTION event.  The two feeds are therefore
    disjoint by construction and there is no de-dup rule to get wrong -- which
    matters, because the two row sets provably overlap: measured on six
    rolled-back $500.00 transfers, a naive union added $3,000.00 over six
    periods.
    """

    def test_a_flat_percentage_employer_contributes_with_no_employee_feed(
        self, db, seed_user, seed_periods,
    ):
        """The real Empower shape: 5% of gross, and no deduction at all.

        Hand-computed.  A flat-percentage employer does not read the employee
        amount, so with zero deductions the account still receives
        ``round(3631.74 * 0.05) = $181.59`` every payday.  On the production
        401(k) that is $9,624.27 over the horizon, which is why the contribution
        tier is not dead code even though ruling R-R measured both EMPLOYEE
        feeds empty.

        The opening is asserted on period 0's first day, so period 0's own
        payday (01-02) is NOT strictly after it and contributes nothing (ruling
        R-Z); period 1's payday (01-16) is, and contributes $181.59.

        **The account NAMES the job that funds it** (ruling **R-SAL5**, plan
        step **salary:R14-b**), which is what makes this shape modellable at
        all: no deduction names the account, so before that column there was
        no link to any profile and the gross fell to an owner-level
        ``.first()``.  Without the link the ruling of 2026-09-04 models no
        employer money, and this case would assert ``$0.00`` twice and pass
        for the wrong reason -- which is why the link is written here rather
        than the gross being stated.
        """
        account = _401k(
            seed_user, seed_periods[0], Decimal("20000.00"),
            opened_on=date(2026, 1, 2),
            employer_type="flat_percentage",
        )
        params = _params_for(account)
        params.employer_flat_percentage = Decimal("0.0500")
        profile = make_salary_profile(
            seed_user, db.session, pay=Decimal("3631.74"),  # $94,425.24 a year / 26
        )
        db.session.flush()
        params.salary_profile_id = profile.id
        db.session.commit()
        ctx = _ctx(seed_user)

        columns = _view(
            account, ctx, seed_periods[:2], params=params,
            feed=_feed_for(seed_user, account, params),
        )
        assert columns[seed_periods[0].id].contribution == Decimal("0.00")
        assert columns[seed_periods[1].id].contribution == Decimal("181.59")

    def test_a_deduction_lands_on_the_payday_and_earns_its_own_period(
        self, seed_user, seed_periods,
    ):
        """$500 a period, credited on the pay period's start_date -- the payday.

        ``PayPeriod`` says so in its own docstring ("start_date (payday)"), and
        it is already the date every ``ContributionRecord`` carries.  So the
        money is in the account from the payday and accrues for the whole
        period, where the growth engine adds a period's contribution AFTER its
        growth and it earns nothing in its own period.

        Hand-computed on the $20,000 401(k) at 7%, and asserted at the DAY
        rather than at the period, because a period-level assertion cannot tell
        a payday from any other day inside the same column: the balance is
        **$20,051.97** on 2026-01-15 (14 days of growth, no contribution yet)
        and **$20,555.78** on 2026-01-16 -- the $500.00 landing plus that day's
        own $3.81 of growth on the raised base.  Dated at the period's END
        instead, 01-16 would read $20,055.78.
        """
        account = _401k(
            seed_user, seed_periods[0], Decimal("20000.00"),
            opened_on=date(2026, 1, 2),
        )
        _salaried_deduction(seed_user, account, "500.00")
        ctx = _ctx(seed_user)

        params = _params_for(account)
        feed = _feed_for(seed_user, account, params)
        columns = _view(
            account, ctx, seed_periods[:2], params=params,
            feed=feed,
        )
        assert columns[seed_periods[1].id].contribution == Decimal("500.00")

        folded = _fold(
            account, ctx, [date(2026, 1, 15), date(2026, 1, 16)],
            params=params, feed=feed,
        )
        assert folded[date(2026, 1, 15)] == Decimal("20051.97")
        assert folded[date(2026, 1, 16)] == Decimal("20555.78")

    def test_a_payday_at_or_before_the_assertion_contributes_nothing(
        self, seed_user, seed_periods,
    ):
        """Ruling R-Z: the contribution boundary is STRICT.

        The account is asserted on 2026-01-16, which is period 1's own payday.
        That paycheck's deduction is money the asserted balance already
        contains, so it is not modelled again; period 2's payday (01-30) is
        strictly after and is.

        The ACCRUAL boundary beside it is inclusive for a reason that does not
        transfer: a day count has to tile the calendar with no gap, while a
        contribution is a discrete event that either is or is not inside the
        assertion.
        """
        account = _401k(
            seed_user, seed_periods[0], Decimal("20000.00"),
            opened_on=date(2026, 1, 16),
        )
        _salaried_deduction(seed_user, account, "500.00")
        ctx = _ctx(seed_user)

        params = _params_for(account)
        feed = _feed_for(seed_user, account, params)
        columns = _view(
            account, ctx, seed_periods[:3], params=_params_for(account),
            feed=feed,
        )
        assert columns[seed_periods[1].id].contribution == Decimal("0.00")
        assert columns[seed_periods[2].id].contribution == Decimal("500.00")

    def test_a_recorded_transfer_is_counted_once_not_twice(
        self, db, seed_user, seed_periods,
    ):
        """Ruling R-R's partition: the row is cash, never a modelled contribution.

        The account carries a $100.00 payroll deduction, so the modelled
        contribution tier is LIVE -- **without one the tier is absent entirely
        and this test could not tell a partition from a union**, which is
        finding N-69's shape and is how the firing control found the first
        version of this test vacuous.

        Hand-computed: period 1's modelled contribution is the DEDUCTION alone,
        $100.00, and the settled $500.00 transfer on 2026-01-20 moves the
        balance exactly once through the cash tier -- the step from 01-19 to
        01-20 is $500.00 plus that day's own accrual on the higher base.  A
        naive union would report $600.00 of contribution; measured on six
        rolled-back $500.00 transfers against the real Roth IRA, it would have
        added $3,000.00 over six periods.
        """
        account = _401k(
            seed_user, seed_periods[0], Decimal("20000.00"),
            opened_on=date(2026, 1, 2),
        )
        _salaried_deduction(seed_user, account, "100.00")
        create_settled_transfer(
            seed_user, db.session, seed_user["account"], account,
            seed_periods[1], amount=Decimal("500.00"),
            settled_on=date(2026, 1, 20),
        )
        db.session.commit()
        ctx = _ctx(seed_user)
        params = _params_for(account)
        feed = _feed_for(seed_user, account, params)

        folded = _fold(
            account, ctx, [date(2026, 1, 19), date(2026, 1, 20)],
            params=params, feed=feed,
        )
        step = folded[date(2026, 1, 20)] - folded[date(2026, 1, 19)]
        assert Decimal("500.00") < step < Decimal("505.00")
        assert _view(
            account, ctx, seed_periods[:2],
            params=params, feed=feed,
        )[seed_periods[1].id].contribution == Decimal("100.00")

    def test_the_employer_match_sizes_off_the_resolved_employee_total(
        self, db, seed_user, seed_periods,
    ):
        """Ruling R-R consequence (a): the match reads BOTH feeds' total.

        A 50% match capped at 6% of a $3,631.74 gross, with a $200.00 deduction
        and a $300.00 recorded transfer in the same period.  Hand-computed: the
        matchable salary is ``round(3631.74 * 0.06) = $217.90``, the resolved
        employee total is ``200.00 + 300.00 = $500.00``, the matched amount is
        ``min(500.00, 217.90) = $217.90`` and the employer contributes
        ``round(217.90 * 0.50) = $108.95``.  The modelled CONTRIBUTION event is
        therefore ``200.00 + 108.95 = $308.95`` -- the deduction and the match,
        never the recorded transfer, which the cash tier already carries.

        With the recorded half ignored the match would size off $200.00 alone
        and pay ``round(200.00 * 0.50) = $100.00``, so the two answers differ.
        """
        account = _401k(
            seed_user, seed_periods[0], Decimal("20000.00"),
            opened_on=date(2026, 1, 2),
            employer_type="match", match_pct=Decimal("0.5000"),
            match_cap_pct=Decimal("0.0600"),
        )
        _salaried_deduction(seed_user, account, "200.00")
        create_settled_transfer(
            seed_user, db.session, seed_user["account"], account,
            seed_periods[1], amount=Decimal("300.00"),
            settled_on=date(2026, 1, 20),
        )
        db.session.commit()
        ctx = _ctx(seed_user)

        params = _params_for(account)
        feed = _feed_for(seed_user, account, params)
        columns = _view(
            account, ctx, seed_periods[:2], params=_params_for(account),
            feed=feed,
        )
        assert columns[seed_periods[1].id].contribution == Decimal("308.95")

    # pylint: disable-next=protected-access
    def test_a_LINKED_feed_pricing_zero_folds_exactly_as_NO_feed_does(
        self, db, seed_user, seed_periods,
    ):
        """The state plan step salary:S3-e-1's gate change moved, measured.

        ``_plan_for`` asked a PRICE question -- *did any priced payday pay
        this account* -- and now asks the PRESENCE one ``is_payroll_linked``,
        because *priced* stops naming a fixed window at plan step
        **salary:S3-e-2** (the field's own docstring carries the argument).
        The two disagree on exactly one state: a deduction WIRED to this
        account that prices ``$0.00`` on every payday, with no employer
        contribution to size.  That state built no plan before and builds an
        empty one now, so this case is the claim that the fold does not move.

        It is not an argument that nothing changed -- something did, a
        recorded-contribution query now runs for such an account.  It is the
        claim that no FIGURE changed, and the reason is that
        :func:`~app.services.balance_at._asset_contributions
        .contribution_events` emits an event only for a non-zero amount.

        **It PASSES on both sides of that gate, by design, and saying so is
        the point** (measured 2026-09-06 against the reverted predicate).  It
        is a regression guard on the equality, not a control that tells the
        two gates apart -- their only other difference is one query, which is
        not worth a brittle count.  What makes the equality a MEASUREMENT
        rather than the shape of the helper is the non-vacuity arm below: a
        paying feed moves the same columns.
        """
        account = _401k(
            seed_user, seed_periods[0], Decimal("20000.00"),
            opened_on=date(2026, 1, 2),
        )
        ctx = _ctx(seed_user)
        params = _params_for(account)
        periods = seed_periods[:3]

        linked_but_zero = _view(
            account, ctx, periods, params=params,
            feed=_flat_feed("0"),
        )
        no_feed_at_all = _view(account, ctx, periods, params=params)
        assert linked_but_zero == no_feed_at_all

        # Non-vacuity: the harness CAN tell two feeds apart, so the equality
        # above is a measurement rather than the shape of the helper.  Note
        # ``_view`` DISCARDS its ``periods`` argument -- the seam walks
        # ``cash.calendar.saved()``, the whole schedule -- so the three-period
        # feeds above cover a prefix of it and the rest reads the hold.  That
        # is harmless in both directions here (an all-zero map holds
        # ``$0.00``, an all-$500 map holds ``$500``) and is stated because a
        # reader would otherwise assume the fold is scoped to those three.
        paying = _view(
            account, ctx, periods, params=params,
            feed=_flat_feed("500.00"),
        )
        assert paying != no_feed_at_all

        # **And the PREDICATE itself, which the equality above cannot grade.**
        # An adversarial review of this step measured that the equality passes
        # on BOTH sides of the gate change -- it is a regression guard, so
        # reverting `_plan_for` to the PRICE question leaves it green. These
        # two lines are what fails on that revert: a linked all-zero feed now
        # builds a plan where it used to build none, and an unlinked one still
        # builds none.
        assert _asset_contributions._plan_for(
            account, ctx.amounts(),
            _inputs(params, _flat_feed("0")),
        ) is not None
        assert _asset_contributions._plan_for(
            account, ctx.amounts(), _inputs(params),
        ) is None


class TestTheContributionWalksLimit:  # pylint: disable=protected-access
    """The annual limit is a calendar-year recurrence over BOTH feeds.

    Pylint: ``protected-access`` -- this class drives ``_contribution_events``
    and ``_ContributionPlan`` directly, which is the point (see below): the walk
    is a pure function and the calendar-year rule needs periods the seeded
    fixture calendar does not contain.


    Driven directly against :func:`_asset_contributions._dated_events` over
    periods no owner has, because the rule that needs grading -- the reset at a
    calendar-year boundary -- needs periods spanning New Year, and the seeded
    fixture calendar covers five months.  The walk is pure, so nothing is
    faked: these are real
    :class:`~app.services.pay_calendar.DerivedPeriod` values -- the same values
    a :class:`~app.services.pay_calendar.PeriodWindow` off
    :meth:`~app.services.balance_at.BalanceContext.reported_periods` holds.

    **They were ``growth_engine.generate_projection_periods`` output until plan
    step C2-e and unsaved ``PayPeriod`` ROWS until C2-f2a.**  The row was what
    the feed took while it keyed its recorded contributions on ``period.id``;
    it now takes the read pass's own
    :class:`~app.services.pay_calendar.PeriodWindow` and keys on
    ``period.period_id``, so these are derived periods (ledger row **P37**).
    """

    @staticmethod
    def _periods(start, count):
        """Return *count* consecutive 14-day :class:`DerivedPeriod` values.

        ``period_id`` is 1-based so it is never ``0`` -- a falsy id would let a
        ``.get(period.period_id)`` defect pass unnoticed on the first period.
        ``end_is_projected`` is ``True`` on the last one alone, which is what a
        real calendar carries (exactly one per non-empty calendar); nothing in
        the walk reads it, and stating it wrongly would make these values
        unrepresentative of the window the door is actually handed.
        """
        return [
            DerivedPeriod(
                period_id=index + 1,
                period_index=index,
                start_date=start + timedelta(days=14 * index),
                end_date=start + timedelta(days=14 * index + 13),
                end_is_projected=(index == count - 1),
            )
            for index in range(count)
        ]

    def test_the_modelled_amount_is_capped_at_the_remaining_limit(self):
        """A $500 deduction against a $1,200 limit pays 500, 500, 200, 0.

        Hand-computed: the cap is ``max(limit - ytd, 0)`` applied per period,
        which is the growth engine's own ``cap_contribution_at_limit``.
        """
        periods = self._periods(date(2026, 1, 2), 4)
        plan = _asset_contributions._ContributionPlan(
            feed=_flat_feed("500.00"),
            employer_params=None,
            annual_limit=Decimal("1200.00"),
            recorded_by_period={},
        )
        events = _asset_contributions._dated_events(
            plan, periods, ReconciledThrough(date(2026, 1, 1)),
        )
        assert [amount for _day, amount in events] == [
            Decimal("500.00"), Decimal("500.00"), Decimal("200.00"),
        ]

    def test_the_walk_is_ORDER_SENSITIVE_which_is_what_the_window_settles(
        self,
    ):
        """The firing control for the ORDER guarantee at the door.

        The same four periods and the same plan, walked NEWEST-FIRST: the
        annual limit is consumed by whichever periods the walk reaches first,
        so the ``$200.00`` partial lands on the EARLIEST payday instead of the
        third one and two periods swap what they are credited.  The walk has
        to be order-sensitive -- a calendar-year limit is an accumulation --
        which is precisely why the order may not be inherited from a loader
        that sorts by the stored ``period_index``.

        **What answers that changed at plan step C2-f2a and this control did
        not.**  :func:`contribution_events` used to SORT its loaded rows; it
        now takes a :class:`~app.services.pay_calendar.PeriodWindow`, which
        sorts at construction and is frozen, so the guarantee is a property of
        the value rather than a step at one door.  This test drives the
        private walk with a plain reversed LIST because that is the only way
        the sensitivity is still expressible -- a window cannot HOLD an
        out-of-order sequence -- and a guarantee whose violation cannot be
        demonstrated is a guarantee nobody can tell is load-bearing.

        Hand-computed: chronological pays 500 / 500 / 200 / 0 (the test
        above); reversed pays the same three amounts against the LAST three
        paydays, so period 3 gets 500, period 2 gets 500 and period 1 gets
        200.
        """
        periods = self._periods(date(2026, 1, 2), 4)
        plan = _asset_contributions._ContributionPlan(
            feed=_flat_feed("500.00"),
            employer_params=None,
            annual_limit=Decimal("1200.00"),
            recorded_by_period={},
        )
        chronological = _asset_contributions._dated_events(
            plan, periods, ReconciledThrough(date(2026, 1, 1)),
        )
        reversed_walk = _asset_contributions._dated_events(
            plan, list(reversed(periods)), ReconciledThrough(date(2026, 1, 1)),
        )

        assert dict(chronological) == {
            periods[0].start_date: Decimal("500.00"),
            periods[1].start_date: Decimal("500.00"),
            periods[2].start_date: Decimal("200.00"),
        }
        assert dict(reversed_walk) == {
            periods[3].start_date: Decimal("500.00"),
            periods[2].start_date: Decimal("500.00"),
            periods[1].start_date: Decimal("200.00"),
        }
        assert dict(chronological) != dict(reversed_walk)

        # And the half that makes the sensitivity above harmless at the DOOR:
        # the type ``resolve`` derives cannot HOLD that reversed order -- it
        # sorts at construction -- so the wrong answer measured here is not
        # reachable through ``contribution_events``.
        assert list(PeriodWindow(periods=tuple(reversed(periods)))) == periods

    # **``test_the_door_walks_the_PAYDAY_whatever_the_stored_ordinal_says``
    # was deleted at plan step ``pay_calendar:C4-c``, and it is worth saying
    # what it did rather than leaving a gap.**  It REVERSED
    # ``budget.pay_periods.period_index`` underneath a live schedule with a
    # direct UPDATE -- a state ``uq_pay_periods_user_index`` permitted --
    # re-read the contribution feed through a fresh read pass, and asserted
    # the events were unchanged, with two controls: that a read ordered by
    # the stored column really did hand the rows back newest-first, and
    # that the derived window was in payday order regardless.
    #
    # The column is dropped, so the scramble is not constructible and
    # neither control has a subject: an ordinal IS the position in payday
    # order now.  What the case was protecting -- that the walk's answer
    # depends on the order it is given -- is measured by
    # ``test_the_walk_is_ORDER_SENSITIVE_which_is_what_the_window_settles``
    # above, which needs no corrupt column to show it.

    def test_a_recorded_contribution_consumes_the_same_limit(self):
        """$900 recorded in period 0 leaves $300 of that year's $1,200 limit.

        Hand-computed.  A recorded contribution is a FACT, so it is never capped
        or dropped -- it consumes the year's room, and the MODELLED amount is
        then capped against what is left: ``min(500.00, 1200.00 - 900.00) =
        $300.00`` in period 0, after which the limit is exhausted and periods 1
        and 2 contribute nothing.  Without the recorded row the same plan pays
        500 / 500 / 200 (the test above), so the recorded feed is load-bearing
        here rather than incidental.
        """
        periods = self._periods(date(2026, 1, 2), 3)
        plan = _asset_contributions._ContributionPlan(
            feed=_flat_feed("500.00"),
            employer_params=None,
            annual_limit=Decimal("1200.00"),
            recorded_by_period={periods[0].period_id: Decimal("900.00")},
        )
        events = _asset_contributions._dated_events(
            plan, periods, ReconciledThrough(date(2026, 1, 1)),
        )
        assert [amount for _day, amount in events] == [Decimal("300.00")]

    def test_the_limit_resets_at_the_calendar_year_boundary(self):
        """A limit exhausted in December pays again in January.

        Hand-computed over four biweekly periods from 2026-12-04: the first two
        paydays fall in 2026 and exhaust a $600 limit ($500 then $100); the
        2027 paydays start a fresh $600 and pay $500 again.
        """
        periods = self._periods(date(2026, 12, 4), 4)
        events = _asset_contributions._dated_events(
            _asset_contributions._ContributionPlan(
                feed=_flat_feed("500.00"),
                employer_params=None,
                annual_limit=Decimal("600.00"),
                recorded_by_period={},
            ),
            periods,
            ReconciledThrough(date(2026, 12, 3)),
        )
        assert [(day.isoformat(), amount) for day, amount in events] == [
            ("2026-12-04", Decimal("500.00")),
            ("2026-12-18", Decimal("100.00")),
            ("2027-01-01", Decimal("500.00")),
            ("2027-01-15", Decimal("100.00")),
        ]


class TestAnAccountThatModelsNothingIsItsCashFold:
    """No parameters, no modelled tier -- and the fold stays TOTAL.

    An INTEREST-kinded account with no ``InterestParams`` is an HYSA the user
    has not configured; an INVESTMENT with no ``InvestmentParams`` is the state
    the kernel's dispatcher already falls through on; a Property with no
    appreciation row is one whose rate is unset.  Inventing a rate for any of
    them would put growth on a screen the account has never earned.
    """

    def test_a_plain_savings_account_reads_its_cash_fold(
        self, db, seed_user, seed_periods,
    ):
        """A PLAIN account has no modelled tier at all, and folds as cash."""
        account = create_savings_account(
            seed_user, db.session, "Plain", Decimal("4000.00"),
        )
        db.session.commit()
        ctx = _ctx(seed_user)

        assert [
            column.balance
            for column in _view(account, ctx, seed_periods).values()
        ] == list(
            _cash_fold.period_balances(
                _cash_fold.assembled_fold(account, ctx),
                period_window(seed_periods),
            ).values()
        )

    def test_an_investment_without_params_reads_its_cash_fold(
        self, seed_user, seed_periods,
    ):
        """The dispatcher's own no-params state: $20,000 flat, no growth."""
        account = _401k(
            seed_user, seed_periods[0], Decimal("20000.00"),
            opened_on=date(2026, 1, 2),
        )
        ctx = _ctx(seed_user)

        columns = _view(account, ctx, seed_periods[:2], params=None)
        assert columns[seed_periods[0].id].balance == Decimal("20000.00")
        assert columns[seed_periods[1].id].accrual == Decimal("0.00")


class TestThePerPeriodIdentity:
    """``balance delta == net + period_timing + book_vs_bank + accrual + contribution``.

    Ruling R-K's identity, extended by ruling R-W to the modelled kinds -- what
    plan step X-g3 renders as the grid's "Growth" row.  It holds BY
    CONSTRUCTION: all five terms are readings of ONE resolved step list, and
    every step is a whole cent.  Every component is computed INDEPENDENTLY here,
    never as a residual proving itself (plan Section 7.2).

    **The cash remainder is TWO terms since plan step S1-c** (ruling R-DH (f)):
    ``period_timing`` (money budgeted to one column that moved in another) and
    ``book_vs_bank`` (what the user's own balance readings booked).  They are
    summed here rather than through a combined accessor, because no such
    accessor survives -- leaving one would invite a surface to render the sum
    again, which is the figure the ruling exists to delete.
    """

    def test_it_holds_over_every_period_of_a_mixed_shape(
        self, db, seed_user, seed_periods,
    ):
        """Settled money, a still-projected row, an accrual and a contribution.

        The shape that can break it: a settled transfer whose cash clock differs
        from its budget column, a projected expense the reader clamps forward, a
        modelled contribution on a payday, and a daily accrual over all of it.
        """
        account = _401k(
            seed_user, seed_periods[0], Decimal("20000.00"),
            opened_on=date(2026, 1, 2),
            employer_type="flat_percentage",
        )
        params = _params_for(account)
        params.employer_flat_percentage = Decimal("0.0500")
        # The job that funds the employer contribution (R-SAL5): without it
        # the 2026-09-04 ruling models no employer money and this shape would
        # be missing the contribution it is here to mix in.
        profile = make_salary_profile(
            seed_user, db.session, pay=Decimal("3631.74"),  # $94,425.24 a year / 26
        )
        db.session.flush()
        params.salary_profile_id = profile.id
        create_settled_transfer(
            seed_user, db.session, seed_user["account"], account,
            seed_periods[2], amount=Decimal("750.00"),
            settled_on=date(2026, 1, 20),
        )
        db.session.commit()
        ctx = _ctx(seed_user, as_of=date(2026, 3, 1))

        feed = _feed_for(seed_user, account, params)
        columns = _view(account, ctx, seed_periods, params=params, feed=feed)
        cash = _cash_periods.period_view_of(
            _cash_fold.assembled_fold(account, ctx),
            period_window(seed_periods),
        )
        openings = _fold(
            account, ctx,
            [period.start_date - timedelta(days=1) for period in seed_periods],
            params=params, feed=feed,
        )
        for period in seed_periods:
            column, cash_column = columns[period.id], cash.columns[period.id]
            assert (
                column.balance - openings[period.start_date - timedelta(days=1)]
            ) == (
                cash_column.net
                + cash_column.period_timing + cash_column.book_vs_bank
                + column.accrual + column.contribution
            ), f"identity broke on period {derived_span(period).period_index}"


# ``TestTheSeedFiltersTheModelledReturn`` stood here until plan step X-g2b.
# It graded ``asset_seed_at`` -- ruling R-U's ACCRUAL-filtered read -- and both
# went with ruling **R-AE**, which found that filter to be a SECOND correction
# for an overlap ruling R-AB's date had already removed: applied together they
# start a chart's projection line BELOW its own history line by every cent
# earned since the account's last assertion (up to $292.11 on the real Empower
# 401(k), finding N-80).  What replaced the rule is pinned where the seed is
# now read -- ``tests/test_routes/test_investment/test_the_projection.py``'s
# ``TestTheProjectionContinuesTheHistory`` (the seed IS the history line's last
# point), ``TestTheProjectionAppliesEachContributionOnce`` (applied once),
# and ``test_retirement_dashboard_service.py``'s three-way seed discrimination.
class TestTheGrowthDecomposition:
    """``asset_growth_at``: what the market did and what the user put in.

    The investment detail page's growth chip, read off the replay's own two
    modelled tiers instead of being re-projected by the growth engine.  "Since
    the latest assertion" needs no window arithmetic and that is the point: an
    ACCRUAL exists only from the assertion's own day (rulings R-L / R-Y) and a
    CONTRIBUTION only strictly after it (ruling R-Z), so the cumulative total at
    a date IS the total since the anchor.
    """

    def test_the_two_tiers_and_the_assertion_explain_the_whole_balance(
        self, seed_user, seed_periods,
    ):
        """$20,000 at 7% with a $500.00 deduction: (55.78, 500.00) on 2026-01-16.

        Hand-computed, and it closes on itself: the 14 days of period 0 accrue
        **$51.97** (this file's own figure), the 01-16 payday lands **$500.00**,
        and that day's growth on the raised $20,551.97 base is **$3.81** -- so
        the cumulative accrual is $55.78 and ``20000.00 + 55.78 + 500.00 ==
        20555.78``, the balance the fold independently reports for that day.
        The chip's two numbers and the balance beside it therefore reconcile by
        construction rather than by a test keeping two producers in step.
        """
        account = _401k(
            seed_user, seed_periods[0], Decimal("20000.00"),
            opened_on=date(2026, 1, 2),
        )
        _salaried_deduction(seed_user, account, "500.00")
        ctx = _ctx(seed_user)
        params = _params_for(account)
        feed = _feed_for(seed_user, account, params)

        accrual, contribution = _growth(
            account, ctx, date(2026, 1, 16),
            params=params, feed=feed,
        )
        assert accrual == Decimal("55.78")
        assert contribution == Decimal("500.00")
        assert Decimal("20000.00") + accrual + contribution == _fold(
            account, ctx, [date(2026, 1, 16)],
            params=params, feed=feed,
        )[date(2026, 1, 16)]

    def test_the_anchor_periods_own_days_are_reported_not_hidden(
        self, seed_user, seed_periods,
    ):
        """Ruling R-Y: an account anchored THIS period has already grown.

        The shipping decomposition returns ``None`` -- and the page hides the
        chip -- when no period follows the anchor, because its forward
        projection starts the period AFTER.  The replay accrues from the
        assertion's own day, so there is a real figure to report on day one:
        hand-computed, $20,000 at 7% credits **$3.71** on 2026-01-02 alone.
        Hiding it would deny a number the balance beside it already contains.
        """
        account = _401k(
            seed_user, seed_periods[0], Decimal("20000.00"),
            opened_on=date(2026, 1, 2),
        )
        ctx = _ctx(seed_user)

        assert _growth(
            account, ctx, date(2026, 1, 2), params=_params_for(account),
        ) == (Decimal("3.71"), Decimal("0.00"))

    def test_an_account_that_models_nothing_reports_zeros_not_none(
        self, seed_user, seed_periods,
    ):
        """A plain savings account decomposes to ``(0.00, 0.00)``.

        A real answer rather than a missing one, which is the totality rule the
        whole arc turns on: the caller decides whether a zero chip is worth
        rendering, and no consumer has to compose this producer with a fallback.
        """
        ctx = _ctx(seed_user)

        assert _growth(
            seed_user["account"], ctx, last_covered_day(seed_periods[-1]),
        ) == (Decimal("0.00"), Decimal("0.00"))


class TestTheContributionTierIsDecidedByTheKind:
    """A payroll feed belongs to an INVESTMENT, whatever the caller handed in.

    ``ContributionInputs`` is loaded per account by the seam, so
    in production a Property never carries ``InvestmentParams``.  The tier gates
    on the account's own KIND anyway, because an argument a caller can get wrong
    is a defect rather than a contract (plan Section 8) -- and a wrong bundle
    here would model payroll contributions into a house, which is a figure no
    reviewer would recognise as wrong on the screen.
    """

    def test_a_property_handed_an_investments_feed_contributes_nothing(
        self, db, seed_user, seed_periods,
    ):
        """The Property appreciates and receives no contribution at all.

        Hand-computed: $100,000 at 3% asserted on 2026-01-02 credits $8.10 a day
        (with an $8.11 where the full-precision total crosses a half-cent), so
        period 0's 14 days accrue **$113.44** -- the same figure the parallel run
        pins -- and the contribution column stays **$0.00** even though the call
        supplies the 401(k)'s own params and a live $500.00 deduction.

        **Period 1 is what gives this teeth, and the firing control is why it is
        here.**  Ruling R-Z's boundary is STRICT, so the ANCHOR period skips a
        payday on its own start day whatever the kind is -- a first version of
        this test read period 0 alone, and deleting the kind guard left it GREEN
        (finding N-69's shape).  Period 1's payday (2026-01-16) is strictly after
        the assertion, so without the guard the house would receive $500.00
        there.
        """
        house = make_appreciating_account(
            seed_user, db.session, seed_periods[0], Decimal("100000.00"),
            Decimal("0.03000"),
        )
        investment = _401k(
            seed_user, seed_periods[0], Decimal("20000.00"),
            opened_on=date(2026, 1, 2), name="401k-for-params",
        )
        _salaried_deduction(seed_user, investment, "500.00")
        ctx = _ctx(seed_user)

        columns = _view(
            house, ctx, seed_periods[:2],
            params=_params_for(investment),
            feed=_feed_for(seed_user, investment),
        )
        assert columns[seed_periods[0].id].accrual == Decimal("113.44")
        assert columns[seed_periods[0].id].contribution == Decimal("0.00")
        assert columns[seed_periods[1].id].contribution == Decimal("0.00")
