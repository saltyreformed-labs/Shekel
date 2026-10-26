"""The investment dashboard page: who may see it and what it renders, the contribution-limit card
and the suggested contribution, the slider's default horizon, the chart's markers, and the
balance hero's click-to-edit true-up.
"""

from datetime import date, timedelta
from decimal import Decimal

from app import ref_cache
from app.enums import EmployerContributionTypeEnum
from app.models.investment_params import InvestmentParams
from app.services import balance_at
from app.services.balance_at import BalanceContext
from tests._test_helpers import current_pay_period, era_of
from app.services.investment_dashboard_service import _cards as investment_cards
from app.services.investment_dashboard_service import _chart as investment_chart
from app.services.pay_calendar import PayCalendar
from tests._test_helpers import make_investment_account

from ._helpers import (
    _cards_context,
    _create_investment_account,
    _create_investment_params,
    _create_other_investment,
)


class TestInvestmentDashboard:
    """Tests for the investment dashboard page."""

    def test_dashboard_no_params(self, auth_client, seed_user, db, seed_periods_today):
        """GET returns 200 even without investment params."""
        acct = _create_investment_account(seed_user, db.session)
        resp = auth_client.get(f"/accounts/{acct.id}/investment")
        assert resp.status_code == 200
        assert b"50,000.00" in resp.data

    def test_dashboard_with_params(self, auth_client, seed_user, db, seed_periods_today):
        """GET returns 200 with params and projection data."""
        acct = _create_investment_account(seed_user, db.session)
        # Anchor stays at the factory's current period (cache AND the
        # AccountAnchorHistory row in sync).  The headline reads the
        # model-from-anchor balance through the balance_at seam.  It used to
        # equal the $50,000 cash basis at anchor==current; since plan step
        # X-g2b the anchor period earns its own days (ruling R-Y), so the
        # headline is that basis PLUS the accrual -- read here from the seam
        # rather than pinned, because the arithmetic belongs to
        # tests/test_services/test_asset_fold.py's hand-computed oracles.
        _create_investment_params(db.session, acct.id)
        headline = balance_at.balance_map(
            acct, BalanceContext.build(seed_user["user"].id),
        )[current_pay_period(seed_user["user"].id).id]
        assert headline > Decimal("50000.00")
        resp = auth_client.get(f"/accounts/{acct.id}/investment")
        assert resp.status_code == 200
        assert f"{headline:,.2f}".encode() in resp.data

    def test_dashboard_idor(
        self, auth_client, second_user, db, seed_periods_today,
    ):
        """GET another user's investment dashboard returns 404 (security)
        and does not leak victim data."""
        other_acct = _create_other_investment(second_user, db.session)

        resp = auth_client.get(f"/accounts/{other_acct.id}/investment")
        assert resp.status_code == 404
        assert b"Other 401k" not in resp.data, (
            "IDOR response leaked victim's account name"
        )

    def test_dashboard_nonexistent(self, auth_client, seed_user, db, seed_periods_today):
        """Nonexistent account returns 404 (security: 404 for not-found and not-yours)."""
        resp = auth_client.get("/accounts/99999/investment")
        assert resp.status_code == 404

    def test_dashboard_brokerage(self, auth_client, seed_user, db, seed_periods_today):
        """Brokerage account (no contribution limit) works."""
        acct = _create_investment_account(
            seed_user, db.session, type_name="Brokerage",
            name="Brokerage", balance="25000.00",
        )
        _create_investment_params(
            db.session, acct.id,
            annual_contribution_limit=None,
            contribution_limit_year=None,
        )
        headline = balance_at.balance_map(
            acct, BalanceContext.build(seed_user["user"].id),
        )[current_pay_period(seed_user["user"].id).id]
        assert headline > Decimal("25000.00")  # ruling R-Y: the anchor accrues
        resp = auth_client.get(f"/accounts/{acct.id}/investment")
        assert resp.status_code == 200
        assert b"Brokerage" in resp.data
        assert f"{headline:,.2f}".encode() in resp.data
        # P2 rebuild: the summary tile became the hero's "modeled at x%
        # assumed return" caption (investment_audit.md, locked anatomy).
        assert b"assumed return" in resp.data

    def test_dashboard_employer_match_card(
        self, auth_client, seed_user, db, seed_periods_today,
    ):
        """Employer-contribution card renders the match formula (TPLA-01).

        The card body branches on ``employer_params.type_id``; before the
        fix it compared a non-existent ``type`` key, so the card header
        rendered while both branches stayed false -- a permanently blank
        body for every configured employer match.
        """
        acct = _create_investment_account(seed_user, db.session)
        _create_investment_params(
            db.session, acct.id,
            employer_contribution_type_id=ref_cache.employer_contribution_type_id(
                EmployerContributionTypeEnum.MATCH),
            employer_match_percentage=Decimal("0.50"),
            employer_match_cap_percentage=Decimal("0.06"),
        )
        resp = auth_client.get(f"/accounts/{acct.id}/investment")
        assert resp.status_code == 200
        # P2 rebuild: the employer card became the band chip's caption
        # ("50% match to 6.00%") plus the Parameters card's formula
        # sentence; both still branch on type_id (the TPLA-01 pin).
        assert b"50% match to 6.00%" in resp.data
        assert b"Employer matches" in resp.data
        # Stored fractions render through |to_percent: 0.50 -> 50%, 0.06 -> 6.00%.
        assert b"50%" in resp.data
        assert b"6.00%" in resp.data

    def test_dashboard_employer_flat_card(
        self, auth_client, seed_user, db, seed_periods_today,
    ):
        """Employer flat-percentage card renders the contribute formula (TPLA-01)."""
        acct = _create_investment_account(seed_user, db.session)
        _create_investment_params(
            db.session, acct.id,
            employer_contribution_type_id=ref_cache.employer_contribution_type_id(
                EmployerContributionTypeEnum.FLAT_PERCENTAGE),
            employer_flat_percentage=Decimal("0.03"),
        )
        resp = auth_client.get(f"/accounts/{acct.id}/investment")
        assert resp.status_code == 200
        # P2 rebuild: employer card -> chip caption ("3.00% of gross") +
        # Parameters formula sentence (same TPLA-01 type_id-branch pin).
        assert b"3.00% of gross" in resp.data
        assert b"Employer contributes" in resp.data
        # 0.03 -> 3.00% through |to_percent.
        assert b"3.00%" in resp.data

    def test_dashboard_ytd_card_shows_limit_year(
        self, auth_client, seed_user, db, seed_periods_today,
    ):
        """YTD contribution card title shows the configured limit year (TPLA-04).

        Before the fix the title computed ``date.today().year`` via an
        undefined ``date`` global and rendered a bare ' Contributions' with
        no year.
        """
        acct = _create_investment_account(seed_user, db.session)
        _create_investment_params(
            db.session, acct.id, contribution_limit_year=2026,
        )
        resp = auth_client.get(f"/accounts/{acct.id}/investment")
        assert resp.status_code == 200
        # P2 rebuild: the YTD card became the "<year> limit" band chip;
        # the TPLA-04 pin (configured year renders, no undefined `date`
        # global) carries over to the chip label.
        assert b"2026 limit" in resp.data

    def test_growth_since_anchor_chip_renders_for_past_anchor(
        self, auth_client, seed_user, db, seed_periods_today,
    ):
        """The measured growth-since-anchor chip renders for a past anchor.

        Anchored at the first period (well before the current one, index 3
        under seed_periods_today) via the proper anchor builder, so the
        growth window ``(anchor, current]`` is non-empty and the seam returns
        a real (growth, contributed) pair -- the band shows the "Growth since"
        chip with its "on <contributed> contributed" caption.  The exact
        figures and the reconciliation identity are pinned at the service
        level (TestInvestmentGrowthSinceAnchor); this pins the render.
        """
        inv = make_investment_account(
            seed_user, db.session, seed_periods_today[0], Decimal("10000.00"),
            name="Growth 401k",
        )
        resp = auth_client.get(f"/accounts/{inv.id}/investment")
        assert resp.status_code == 200
        html = resp.data.decode()
        assert "Growth since" in html
        assert "contributed" in html

    def test_growth_chip_shows_when_anchored_at_current(
        self, auth_client, seed_user, db, seed_periods_today,
    ):
        """An account anchored THIS period SHOWS its chip (ruling R-AC / R-Y).

        **This inverted at plan step X-g2b.**  The shipped producer split its
        periods on ``period_index > anchor_idx``, so an account anchored in the
        current period had no post-anchor window, the seam returned ``None`` and
        the band omitted the chip.  Ruling R-Y removes that premise: the
        assertion's own day accrues, so the account HAS earned something --
        measured $105.26 on the real Roth IRA at its anchor period -- and hiding
        the chip would deny a figure the balance beside it already contains.

        The chip is still hidden where it should be: no investment params, or no
        current period (the two arms ruling R-AC kept).
        """
        current = current_pay_period(seed_user["user"].id)
        inv = make_investment_account(
            seed_user, db.session, current, Decimal("10000.00"),
            name="Fresh 401k",
        )
        resp = auth_client.get(f"/accounts/{inv.id}/investment")
        assert resp.status_code == 200
        assert "Growth since" in resp.data.decode()


class TestContributionLimitZeroCap:
    """Pin the zero-vs-None annual-limit branches (quality-pass B7).

    Commit 24 / HIGH-06 / E-12 replaced Python truthiness on
    ``annual_contribution_limit`` with explicit ``is None`` checks so a
    stored ``Decimal("0")`` ("capped at zero this year") stays distinct
    from ``None`` ("no cap configured").  The cleanup left those three
    branches in ``_compute_limit_info`` and the zero-cap branch in
    ``_compute_suggested_contribution`` unpinned; these unit tests assert
    each on hand-reasoned values.

    ``_compute_limit_info`` reads only ``annual_contribution_limit``, so an
    in-memory params object keeps it pure.  ``_compute_suggested_contribution``
    takes the shared per-request feed since plan step C2-f2c and so needs a
    context; :func:`_cards_context` builds a real one over a real calendar,
    which is still pure (no DB, no engine).
    """

    def test_limit_info_zero_cap_with_ytd_is_fully_used(self):
        """Zero cap + positive YTD -> card renders at 100% used.

        ``limit`` is 0 (not ``None``), so the card is shown rather than
        hidden; any positive YTD is over a zero cap, so ``pct`` saturates
        at 100 (matching the growth engine's ``min(contribution, 0) = 0``
        semantics).  A truthiness regression would treat the zero cap as
        "no cap" and hide the card (return ``None``).
        """
        params = InvestmentParams(annual_contribution_limit=Decimal("0"))
        result = investment_cards._compute_limit_info(
            params, Decimal("100.00"),
        )
        # C1 (Loop B P1): the dict gained is_over / over_amount.  Zero cap +
        # $100 YTD is OVER by the full YTD: is_over = (100 > 0) = True;
        # over_amount = round_money(100.00 - 0) = 100.00.  The zero-cap pct
        # semantics (100 via the explicit elif) are unchanged.
        assert result == {
            "limit": Decimal("0"),
            "ytd": Decimal("100.00"),
            "pct": Decimal("100"),
            "is_over": True,
            "over_amount": Decimal("100.00"),
        }

    def test_limit_info_zero_cap_zero_ytd_is_zero_used(self):
        """Zero cap + zero YTD -> card renders at 0% used.

        Both cap and YTD are zero: nothing contributed against a zero
        cap, so ``pct`` is 0 (the ``elif ytd > 0`` branch is not taken).
        The card still renders (``limit`` is 0, not ``None``).
        """
        params = InvestmentParams(annual_contribution_limit=Decimal("0"))
        result = investment_cards._compute_limit_info(
            params, Decimal("0"),
        )
        # C1: zero cap + zero YTD is NOT over (0 > 0 is false), so over_amount
        # is None; the pct 0 semantics are unchanged.
        assert result == {
            "limit": Decimal("0"),
            "ytd": Decimal("0"),
            "pct": Decimal("0"),
            "is_over": False,
            "over_amount": None,
        }

    def test_limit_info_none_cap_hides_card(self):
        """No cap configured (``None``) -> hide the card (return ``None``).

        The contrast case to the zero cap above: ``None`` means
        "Brokerage-style, no IRS limit," which hides the card entirely.
        Keeping this distinct from the zero cap is the whole point of the
        ``is None`` fix.
        """
        params = InvestmentParams(annual_contribution_limit=None)
        result = investment_cards._compute_limit_info(
            params, Decimal("100.00"),
        )
        assert result is None

    def test_limit_info_positive_cap_rounds_half_up(self):
        """Positive cap -> pct rounds the YTD ratio HALF_UP via percent_complete.

        $4,980 / $5,000 = 99.6%.  deep-quality-hunt #78 routed the
        ``limit > 0`` branch through ``money.percent_complete`` (clamped
        [0, 100], ROUND_HALF_UP, Decimal), retiring the prior
        ``min(100, int(...))`` truncation that disagreed with the budget
        dashboard's "percent funded" surfaces.  So pct is ``Decimal("99.60")``
        (the template renders ``"{:.0f}".format(...)`` -> "100%"), NOT the
        old truncated ``99``.  Revert-proof: ``int(99.6) == 99`` fails this.
        """
        params = InvestmentParams(annual_contribution_limit=Decimal("5000"))
        result = investment_cards._compute_limit_info(
            params, Decimal("4980.00"),
        )
        # C1: $4,980 <= $5,000 cap, so is_over = (4980 > 5000) = False and
        # over_amount is None; the pct 99.60 rounding is unchanged.
        assert result == {
            "limit": Decimal("5000"),
            "ytd": Decimal("4980.00"),
            "pct": Decimal("99.60"),
            "is_over": False,
            "over_amount": None,
        }

    def test_limit_info_over_limit_states_the_overage(self):
        """C1: a positive cap with YTD over it flags is_over + the overage.

        The clamped pct saturates at 100 and cannot distinguish an excess
        contribution from a perfect max, so the goal-framed bar reads the
        overage from ``over_amount``.  $24,100 YTD against the $23,500 2026
        401(k) cap: is_over = (24100 > 23500) = True; over_amount =
        round_money(24100.00 - 23500.00) = 600.00; pct clamps to 100.00
        (percent_complete's high clamp).
        """
        params = InvestmentParams(
            annual_contribution_limit=Decimal("23500.00"),
        )
        result = investment_cards._compute_limit_info(
            params, Decimal("24100.00"),
        )
        assert result == {
            "limit": Decimal("23500.00"),
            "ytd": Decimal("24100.00"),
            "pct": Decimal("100.00"),
            "is_over": True,
            "over_amount": Decimal("600.00"),
        }

    def test_suggested_contribution_zero_cap_is_zero(self):
        """Zero cap -> $0.00 per-period suggestion, never a phantom default.

        Remaining limit = max(0 - ytd, 0) = 0, so the suggestion is
        ``(0 / max(periods, 1)).quantize(.01) = 0.00`` regardless of the
        period list.  Pins that a zero cap suggests nothing within the
        cap rather than the legacy $500 fallback truthiness once produced.
        """
        result = investment_cards._compute_suggested_contribution(
            _cards_context(
                limit=Decimal("0"), ytd=Decimal("0"),
                paydays=[date(2026, 1, 1)], current_index=None,
                as_of=date(2026, 1, 15),
            ),
        )
        assert result == Decimal("0.00")

    def test_suggested_contribution_none_cap_is_zero(self):
        """No cap configured (``None``) -> $0.00 suggestion (no IRS limit).

        The brokerage path returns ``Decimal("0")`` immediately: there is
        no annual limit to spread over the remaining periods.  Pins the
        contrast to a positive cap and guards against a reintroduced
        non-zero default for the no-cap case.
        """
        result = investment_cards._compute_suggested_contribution(
            _cards_context(
                limit=None, ytd=Decimal("0"),
                paydays=[date(2026, 1, 1)], current_index=None,
                as_of=date(2026, 1, 15),
            ),
        )
        assert result == Decimal("0")

    #: The four same-year paydays both boundary cases below spread over.
    _SPREAD_PAYDAYS = [
        date(2026, 1, 1), date(2026, 1, 15),
        date(2026, 1, 29), date(2026, 2, 12),
    ]

    def test_suggested_contribution_excludes_current_period_from_remaining(self):
        """remaining_periods is anchored on current_period, not on the clock.

        deep-quality-hunt #59: the YTD subtracted from the limit is summed
        over periods whose payday is ``<= current_period.start_date``
        (``investment_projection._inputs._ytd_contributions``), so the
        per-period suggestion must spread the remainder over the periods
        STRICTLY AFTER the current period -- else the current period is
        double-counted (in YTD *and* in the remaining spread) on the single
        calendar day a period begins (``today == period start``).  Four
        same-year periods (Jan 1/15/29, Feb 12), current = the Jan 15 period,
        with the pass's clock ON Jan 15 (the period-start edge that triggered
        the old bug): $7,000 limit - $3,000 YTD = $4,000 spread over the two
        strictly-after periods (Jan 29, Feb 12) = $2,000.00.  Revert-proof:
        the old ``start_date >= today`` window includes the Jan 15
        current period (3 periods) -> $1,333.33.

        **The clock is the read pass's, stated rather than frozen** (plan step
        C2-f2c).  This monkeypatched ``date.today`` in the ``_cards`` module
        until then, which pinned only that module -- the pass around it went on
        reading the real day.
        """
        result = investment_cards._compute_suggested_contribution(
            _cards_context(
                limit=Decimal("7000"), ytd=Decimal("3000"),
                paydays=self._SPREAD_PAYDAYS, current_index=1,
                as_of=date(2026, 1, 15),
            ),
        )
        # 7000 - 3000 = 4000; periods strictly after Jan 15 = {Jan 29,
        # Feb 12} = 2; 4000 / 2 = 2000.00 (NOT the old 4000 / 3 = 1333.33).
        assert result == Decimal("2000.00")

    def test_suggested_contribution_mid_period_today_is_behaviour_neutral(self):
        """Anchoring on current_period leaves the typical case unchanged.

        deep-quality-hunt #59: when the clock falls strictly inside the current
        period (the common case), no period starts in (current.start, today],
        so the current-period boundary (``> current.start``) and the old
        today boundary (``>= today``) select the SAME set -- the fix is
        behaviour-neutral here.  Same four periods, current = Jan 15, but the
        clock on Jan 22 (mid-period): both windows yield the two
        strictly-after periods, so the suggestion is $2,000.00, identical to
        what the old ``>= today`` rule produced.
        """
        result = investment_cards._compute_suggested_contribution(
            _cards_context(
                limit=Decimal("7000"), ytd=Decimal("3000"),
                paydays=self._SPREAD_PAYDAYS, current_index=1,
                as_of=date(2026, 1, 22),
            ),
        )
        # 4000 spread over {Jan 29, Feb 12} = 2 -> 2000.00 (same as old).
        assert result == Decimal("2000.00")

    def test_suggested_contribution_falls_back_to_the_PASSES_clock(self):
        """With no current period the boundary is ``balance_ctx.as_of``.

        The arm that read ``date.today()`` until plan step C2-f2c.  A pass
        valued at Jan 22 spreads over the paydays after it -- Jan 29 and
        Feb 12 -- so $4,000 / 2 = $2,000.00.

        **It cannot pass by the two clocks coinciding**, and the reason is the
        paydays rather than the year: every one of them is a January or
        February day of a year the suite has already passed, so a reverted
        process-clock read finds ZERO periods strictly after it and answers
        $4,000.00.  A draft of this docstring claimed the schedule's YEAR was
        not the suite's, which was false -- the conclusion held for a different
        reason, and a control whose stated reason is wrong is one nobody can
        re-check (adversarial review, 2026-08-15).
        """
        result = investment_cards._compute_suggested_contribution(
            _cards_context(
                limit=Decimal("7000"), ytd=Decimal("3000"),
                paydays=self._SPREAD_PAYDAYS, current_index=None,
                as_of=date(2026, 1, 22),
            ),
        )
        assert result == Decimal("2000.00")


class TestTheDefaultHorizonComesOffTheReadPass:
    """``_compute_default_horizon``: three arms, none of them reading a clock.

    Untested until plan step C2-f2c beyond the route's own smoke coverage,
    which is how the ``date.today()`` reads in it survived: the slider's
    default is an integer nobody asserted, and the route renders whatever it
    is.  All three arms are graded here against the pass's own ``as_of``.
    """

    def test_a_planned_retirement_date_wins_and_counts_from_the_pass(self):
        """Years from the pass's clock to the retirement YEAR."""
        assert investment_cards._compute_default_horizon(
            _cards_context(
                limit=None, ytd=Decimal("0"),
                paydays=[date(2026, 1, 1)], current_index=0,
                as_of=date(2026, 6, 15), retirement_date=date(2046, 3, 1),
            ),
        ) == 20

    def test_a_retirement_date_already_past_still_answers_one_year(self):
        """The floor, which keeps the slider from asking for zero periods."""
        assert investment_cards._compute_default_horizon(
            _cards_context(
                limit=None, ytd=Decimal("0"),
                paydays=[date(2026, 1, 1)], current_index=0,
                as_of=date(2026, 6, 15), retirement_date=date(2020, 3, 1),
            ),
        ) == 1

    def test_without_one_it_runs_a_year_past_the_LAST_SAVED_period(self):
        """The second arm: the LAST period's year, plus one.

        **Every term is separately graded, and an earlier draft of this case
        graded none of them** (adversarial review, 2026-08-15).  That draft put
        both paydays in 2026 against a 2026 clock, so the arm computed
        ``max(1, (2026 - 2026) + 1) = 1`` -- which is also what the ``max(1,
        ...)`` floor returns, what ``periods[0]`` would return, and what
        dropping the ``+ 1`` would return.  It could only distinguish this arm
        from the CONSTANT.

        Here the paydays straddle a year boundary: the FIRST period ends
        2028-06-14 and the LAST ends 2029-01-16, so ``[-1]`` and ``[0]`` differ
        (4 against 3), the ``+ 1`` differs (4 against 3), and the floor differs
        (4 against 1).  The last end is DERIVED -- the day the cadence projects
        past the final payday -- which is the whole point of reading the pass's
        calendar rather than a stored column.
        """
        assert investment_cards._compute_default_horizon(
            _cards_context(
                limit=None, ytd=Decimal("0"),
                paydays=[
                    date(2028, 6, 1), date(2028, 6, 15), date(2029, 1, 3),
                ],
                current_index=0, as_of=date(2026, 6, 15),
            ),
        ) == 4

    def test_an_owner_with_no_paydays_takes_the_constant(self):
        """The third arm, and the one an empty window must not crash on.

        A :class:`~app.services.pay_calendar.PeriodWindow` defines ``__len__``
        and no ``__bool__``, so Python's truthiness falls through to the
        length and ``if periods:`` is exactly the emptiness test -- which is
        what the code says.  A draft of this case justified an explicit
        ``len(...)`` by claiming otherwise (adversarial review, 2026-08-15).
        """
        assert investment_cards._compute_default_horizon(
            _cards_context(
                limit=None, ytd=Decimal("0"), paydays=[],
                current_index=None, as_of=date(2026, 6, 15),
            ),
        ) == investment_cards._FALLBACK_HORIZON_YEARS


class TestTheChartMarkersAskTheWindowWhereTheDateFALLS:
    """``_build_chart_markers``: ledger row **P48**, and its first tests.

    That row is the last live member of row **P6**'s census of "which pay
    period contains this date" implementations: this function walked the
    projection window period by period testing ``start_date <= d <= end_date``,
    which is
    :meth:`~app.services.pay_calendar.PeriodWindow.containing`'s own predicate.
    It had NO test coverage at all -- ``retirement_marker_index`` is an integer
    nothing asserted on -- which is how a duplicate predicate went unnoticed
    long enough to need a census to find.

    Every case here builds a REAL window off a REAL calendar, because the
    property under test is about a VIEW's ordinals and a fake list of periods
    could not express the distinction.
    """

    @staticmethod
    def _axis(first_index=0, count=6):
        """A real window over a real biweekly calendar opening 2026-01-02."""
        calendar = PayCalendar.from_paydays(
            [
                (index + 1, date(2026, 1, 2) + timedelta(days=14 * index))
                for index in range(10)
            ],
            (era_of(date(2026, 1, 2), 14),), user_id=1,
            history_opens_on=None,
        )
        return calendar.window(first_index=first_index, count=count)

    @staticmethod
    def _ctx(retirement_date):
        return _cards_context(
            limit=None, ytd=Decimal("0"), paydays=[date(2026, 1, 2)],
            current_index=0, as_of=date(2026, 1, 2),
            retirement_date=retirement_date,
        )

    def test_the_marker_is_the_history_length_plus_the_WINDOW_offset(self):
        """The chart plots history then one point per axis period.

        A window opening at calendar ordinal 3 covers 2026-02-13 onward; the
        date 2026-03-01 falls in its SECOND period (2026-02-27..2026-03-12), so
        the marker is ``history_len + 1``.
        """
        markers = investment_chart._build_chart_markers(
            self._ctx(date(2026, 3, 1)), 7, self._axis(first_index=3),
        )
        assert markers["retirement_marker_index"] == 8
        assert markers["today_boundary_index"] == 7
        assert markers["retirement_year"] == 2026

    def test_it_is_the_VIEWS_ordinal_not_the_CALENDARS(self):
        """The firing control, and the defect the harness was shown catching.

        The same date on the same window: its period's ``period_index`` is 4,
        the window's own offset for it is 1.  A reader taking the calendar
        ordinal would put the marker at ``7 + 4 = 11`` rather than ``7 + 1 =
        8`` -- measured on production at 241 against 252, an eleven-point slide
        that plants the retirement line in the wrong year with no error
        anywhere.
        """
        window = self._axis(first_index=3)
        assert window.containing(date(2026, 3, 1)).period_index == 4
        assert investment_chart._build_chart_markers(
            self._ctx(date(2026, 3, 1)), 7, window,
        )["retirement_marker_index"] == 8

    def test_a_date_BEFORE_the_window_marks_nothing(self):
        """Outside the axis is ``None``, not a clamp to its first point."""
        assert investment_chart._build_chart_markers(
            self._ctx(date(2026, 1, 5)), 7, self._axis(first_index=3),
        )["retirement_marker_index"] is None

    def test_a_date_PAST_the_window_marks_nothing(self):
        """The horizon side of the same rule -- the ordinary state.

        A retirement date decades out sits past any one-year axis, which is
        what the /investment slider's shortest position renders.
        """
        assert investment_chart._build_chart_markers(
            self._ctx(date(2046, 6, 30)), 7, self._axis(),
        )["retirement_marker_index"] is None

    def test_a_date_past_the_window_still_reports_its_YEAR(self):
        """The two answers are independent, and the caption survives.

        The verdict strip captions the retirement YEAR whether or not the
        chart can plot a line at it, so an off-axis date must not blank the
        caption as well as the marker.
        """
        markers = investment_chart._build_chart_markers(
            self._ctx(date(2046, 6, 30)), 7, self._axis(),
        )
        assert markers["retirement_year"] == 2046
        assert markers["retirement_marker_index"] is None

    def test_no_retirement_date_leaves_only_the_today_boundary(self):
        """The unset state -- production's own, until this step measured it.

        The clone the cutover was verified against carried no planned
        retirement date, so every marker probe on it came back ``None`` and the
        byte-identical diff said nothing about this function.  Setting one is
        what made the harness able to fail; this is the arm it then could not
        reach.
        """
        markers = investment_chart._build_chart_markers(
            self._ctx(None), 7, self._axis(),
        )
        assert markers == {
            "today_boundary_index": 7,
            "retirement_year": None,
            "retirement_marker_index": None,
        }

    def test_an_empty_axis_marks_nothing_and_raises_nothing(self):
        """A horizon the calendar does not reach yields no window at all."""
        markers = investment_chart._build_chart_markers(
            self._ctx(date(2026, 3, 1)), 0, self._axis(first_index=99),
        )
        assert markers["retirement_marker_index"] is None
        assert markers["today_boundary_index"] == 0


# ``test_true_up_from_investment_409_reopens_investment`` was DELETED at plan
# step X-f1c3c (ruling R-EN): it asserted that a 409 conflict raised from this
# hero reopened the editor back in the investment surface.  There is no 409 --
# an assertion history is append-only, so nothing a second tab does can be
# overwritten here.  The surviving revert-context cases in this class still
# grade that a SUCCESS re-renders into the surface that opened the editor,
# which is the routing this test shared.


class TestInvestmentBalanceHeroTrueUp:
    """Loop B P1 C4: the detail page's click-to-edit balance hero true-up.

    The hero reuses the shared anchor editor (accounts.anchor_form /
    accounts.true_up / anchor_service) via a new ``revert=investment``
    surface: ``investment.balance_hero`` is the Cancel / Escape revert
    target (rendering the model-from-anchor balance), and the PATCH records a
    statement balance as a dated anchor as-of today -- the same semantics as
    the cockpit card's click-to-edit editor.  It was a 409-conflict revert
    target too, until ruling R-EN deleted the 409 (plan step X-f1c3c).
    """

    def test_balance_hero_renders_editable_cell(
        self, auth_client, seed_user, db, seed_periods_today,
    ):
        """GET (HX) returns the model-from-anchor balance + the editor opener."""
        acct = _create_investment_account(seed_user, db.session)
        _create_investment_params(db.session, acct.id)
        resp = auth_client.get(
            f"/accounts/{acct.id}/investment/balance-hero",
            headers={"HX-Request": "true"},
        )
        assert resp.status_code == 200
        html = resp.data.decode()
        # The hero renders the model-from-anchor balance -- the SAME producer
        # the page's headline reads (finding N-81: they used to be two, and
        # cancelling the editor would have restored a figure the page was not
        # showing).  At anchor == current that is the $50,000 assertion plus
        # the anchor period's own accrual (ruling R-Y).
        headline = balance_at.balance_map(
            acct, BalanceContext.build(seed_user["user"].id),
        )[current_pay_period(seed_user["user"].id).id]
        assert headline > Decimal("50000.00")
        assert f"{headline:,.2f}" in html
        assert "investment-balance-hero" in html
        # Opens the shared anchor editor scoped to the investment surface, so
        # Cancel / Escape revert back to this hero, not the grid cell.
        assert "revert=investment" in html

    def test_balance_hero_redirects_without_htmx(
        self, auth_client, seed_user, db, seed_periods_today,
    ):
        """GET without HX-Request redirects to the dashboard page."""
        acct = _create_investment_account(seed_user, db.session)
        resp = auth_client.get(f"/accounts/{acct.id}/investment/balance-hero")
        assert resp.status_code == 302
        assert "/investment" in resp.headers.get("Location", "")

    def test_balance_hero_idor(
        self, auth_client, second_user, db, seed_periods_today,
    ):
        """GET another user's balance hero returns 404 and leaks nothing."""
        other_acct = _create_other_investment(second_user, db.session)
        resp = auth_client.get(
            f"/accounts/{other_acct.id}/investment/balance-hero",
            headers={"HX-Request": "true"},
        )
        assert resp.status_code == 404
        assert b"Other 401k" not in resp.data

    def test_balance_hero_nonexistent(
        self, auth_client, seed_user, db, seed_periods_today,
    ):
        """GET a nonexistent account's balance hero returns 404."""
        resp = auth_client.get(
            "/accounts/99999/investment/balance-hero",
            headers={"HX-Request": "true"},
        )
        assert resp.status_code == 404

    def test_true_up_from_investment_surface_persists_dated_anchor(
        self, auth_client, seed_user, db, seed_periods_today,
    ):
        """PATCH true-up (revert=investment) records a dated anchor + fires event.

        Reuses accounts.true_up -> anchor_service.apply_anchor_true_up: a
        new AccountAnchorHistory row is appended carrying the submitted
        balance (as-of today, anchored to the current period), and the
        response fires ``balanceChanged`` so the detail page re-renders its
        hero -- identical to the cockpit editor's success contract.
        """
        from app.models.account import AccountAnchorHistory  # pylint: disable=import-outside-toplevel

        acct = _create_investment_account(seed_user, db.session)
        _create_investment_params(db.session, acct.id)
        db.session.commit()

        resp = auth_client.patch(
            f"/accounts/{acct.id}/true-up?revert=investment",
            data={
                "anchor_balance": "51234.56",
                "version_id": acct.version_id,
            },
            headers={"HX-Request": "true"},
        )
        assert resp.status_code == 200
        assert resp.headers.get("HX-Trigger") == "balanceChanged"

        db.session.expire_all()
        history = (
            db.session.query(AccountAnchorHistory)
            .filter_by(
                account_id=acct.id, anchor_balance=Decimal("51234.56"),
            )
            .all()
        )
        assert len(history) == 1, (
            "true-up should append exactly one dated anchor history row"
        )
