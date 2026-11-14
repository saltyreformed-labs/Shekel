"""The contribution tier: by source and payday, its annual limit, decided by the kind."""

from datetime import date, timedelta
from decimal import Decimal

from app.services.balance_at import _asset_contributions, _cash_fold
from app.services.investment_projection import AccountPayrollFeed
from app.services.cash_ledger import ReconciledThrough
from app.services.pay_calendar import DerivedPeriod, PeriodWindow
from tests._test_helpers import (
    create_settled_transfer,
    make_appreciating_account,
    make_salary_profile,
)

from ._helpers import (
    _ctx,
    _inputs,
    _fold,
    _view,
    _feed_for,
    _params_for,
    _401k,
    _salaried_deduction,
)


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

        Hand-computed on a made-up $3,019.23 paycheck.  A flat-percentage
        employer does not read the employee amount, so with zero deductions the
        account still receives ``round(3019.23 * 0.05) = round(150.9615) =
        $150.96`` every payday.  The production 401(k) has this shape, and its
        employer money ($9,624.27 over the horizon on the owner's real pay, as
        17ead4c59 recorded it on 2026-07-26) is why the contribution tier is
        not dead code even though ruling R-R measured both EMPLOYEE feeds empty.

        The opening is asserted on period 0's first day, so period 0's own
        payday (01-02) is NOT strictly after it and contributes nothing (ruling
        R-Z); period 1's payday (01-16) is, and contributes $150.96.

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
            seed_user, db.session, pay=Decimal("3019.23"),  # $78,500.00 a year / 26, made up
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
        assert columns[seed_periods[1].id].contribution == Decimal("150.96")

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

        A 50% match capped at 6% of a made-up $3,019.23 gross, with a $100.00
        deduction and a $300.00 recorded transfer in the same period.
        Hand-computed: the matchable salary is ``round(3019.23 * 0.06) =
        round(181.1538) = $181.15``, the resolved employee total is
        ``100.00 + 300.00 = $400.00``, the matched amount is
        ``min(400.00, 181.15) = $181.15`` and the employer contributes
        ``round(181.15 * 0.50) = round(90.575) = $90.58`` (half up).  The
        modelled CONTRIBUTION event is therefore ``100.00 + 90.58 = $190.58``
        -- the deduction and the match, never the recorded transfer, which the
        cash tier already carries.

        With the recorded half ignored the match would size off $100.00 alone
        and pay ``round(100.00 * 0.50) = $50.00``, so the two answers differ.
        **The deduction must sit under the cap for that to hold** (ruling
        plan#3553): at $200.00 it alone exceeds $181.15, both readings pay
        $90.58, and the case stops catching the defect it names.
        """
        account = _401k(
            seed_user, seed_periods[0], Decimal("20000.00"),
            opened_on=date(2026, 1, 2),
            employer_type="match", match_pct=Decimal("0.5000"),
            match_cap_pct=Decimal("0.0600"),
        )
        _salaried_deduction(seed_user, account, "100.00")
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
        assert columns[seed_periods[1].id].contribution == Decimal("190.58")

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
        cash = _cash_fold.assembled_fold(account, ctx)
        assert _asset_contributions._plan_for(
            account, cash, _inputs(params, _flat_feed("0")),
            periods[-1].start_date,
        ) is not None
        assert _asset_contributions._plan_for(
            account, cash, _inputs(params), periods[-1].start_date,
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
            transferred_by_payday={},
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
            transferred_by_payday={},
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
            transferred_by_payday={periods[0].start_date: Decimal("900.00")},
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
                transferred_by_payday={},
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
