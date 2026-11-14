"""The payroll feeds: load_payroll_feeds over real rows, and the wiring the feeds build on."""

from dataclasses import replace
from datetime import date, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import event

from app import ref_cache
from app.enums import (
    CalcMethodEnum,
    PaycheckLineKindEnum,
    EmployerContributionTypeEnum,
    RaiseTypeEnum,
)
from app.extensions import db
from app.models.paycheck_line import PaycheckLine
from app.models.salary_profile import SalaryProfile
from app.models.salary_raise import SalaryRaise
from app.services.investment_projection import AccountPayrollFeed
from app.services.pay_calendar import calendar_for
from app.services.salary_raises import RaiseTerms, terms_of
from app.services.projection_inputs import (
    build_payroll_feeds,
    load_payroll_feeds,
    load_payroll_wiring,
    price_payroll_feeds,
)
from tests._test_helpers import make_line_cadence_rule, pricing_over, start_test_pay_list

from ._helpers import _seed_deductions_fixture


def _flat_id():
    return ref_cache.calc_method_id(CalcMethodEnum.FLAT)


class TestLoadPayrollFeeds:
    """The producer plan step **salary:R14-b** puts in place of the feed's own
    arithmetic (ruling **R-SAL2**), graded against REAL rows.

    Two questions, and the point of the step is that neither is answered here:
    what a deduction takes from a paycheck, and what gross funds an employer
    contribution, are both established by
    :class:`~app.services.income_service.ProfilePaychecks` when it prices the
    paycheck.  What this loader owns is the FOLD -- which lines belong to which
    account, and which profile may answer at all -- so that is what these cases
    grade.

    They reuse :func:`_seed_deductions_fixture`, whose five deductions were
    built for the loader beneath this one: an active $500 flat on Acct A, an
    active $250 on Acct B, an INACTIVE deduction worth $999 on Acct A, one
    worth $888 on an ARCHIVED profile, and another owner's worth $777.  Three
    of the five must contribute nothing, and each does so for its own reason.
    """

    @staticmethod
    def _params_for(account_id, salary_profile_id=None):
        """Attach an :class:`InvestmentParams` row and return the map entry."""
        from app.models.investment_params import InvestmentParams
        params = InvestmentParams(
            account_id=account_id,
            assumed_annual_return=Decimal("0.07"),
            annual_contribution_limit=Decimal("23500"),
            employer_contribution_type_id=(
                ref_cache.employer_contribution_type_id(
                    EmployerContributionTypeEnum.FLAT_PERCENTAGE,
                )
            ),
            employer_flat_percentage=Decimal("0.05"),
            salary_profile_id=salary_profile_id,
        )
        db.session.add(params)
        db.session.flush()
        return params

    def test_the_fold_keys_each_line_by_its_target_account(
        self, app, db, seed_user, seed_second_user, seed_periods,
    ):
        """Each account receives the deduction lines that NAME it, and no others.

        Acct A's $500 and Acct B's $250 come off the engine's own
        ``DeductionLine.target_account_id``, which is the hook ruling R-SAL2
        says this consumption finishes rather than adds: the column was
        populated so the two surfaces would agree and nothing ever read it.
        """
        with app.app_context():
            ids = _seed_deductions_fixture(app, db, seed_user, seed_second_user)
            db.session.commit()
            calendar = calendar_for(ids["user_id"])
            pricing = pricing_over(calendar)
            feeds = load_payroll_feeds(
                pricing,
                [ids["acct_a_id"], ids["acct_b_id"]], {},
            )
            assert set(feeds) == {ids["acct_a_id"], ids["acct_b_id"]}
            for period in calendar.saved():
                assert feeds[ids["acct_a_id"]].employee_at(period) == Decimal("500")
                assert feeds[ids["acct_b_id"]].employee_at(period) == Decimal("250")

    def test_two_deductions_on_ONE_profile_are_summed_ONCE(
        self, app, db, seed_user, seed_second_user, seed_periods,
    ):
        """An account funded twice by one job reads the SUM, not the double.

        The fold reads each funding PROFILE's breakdown, and one breakdown
        already holds every line that profile takes -- so an account named by
        two of one profile's deductions must read that profile once.  Reading
        it per DEDUCTION instead counts every line as many times as the
        account has deductions, which is $1,500 here against a true $750, and
        no other case in this class can tell the two apart: every one of them
        has a single active line per account.

        Acct A already carries the fixture's active $500; this adds a $250
        second line on the same profile, so the account's payday is $750.
        """
        with app.app_context():
            ids = _seed_deductions_fixture(app, db, seed_user, seed_second_user)
            profile = (
                db.session.query(SalaryProfile)
                .filter_by(user_id=ids["user_id"], name="Active")
                .one()
            )
            db.session.add(PaycheckLine(
                salary_profile_id=profile.id,
                target_account_id=ids["acct_a_id"],
                name="A-second", amount=Decimal("250"),
                calc_method_id=_flat_id(),
                paycheck_line_kind_id=ref_cache.paycheck_line_kind_id(
                    PaycheckLineKindEnum.PRE_TAX_DEDUCTION,
                ),
                is_active=True,
            ))
            db.session.commit()

            calendar = calendar_for(ids["user_id"])
            pricing = pricing_over(calendar)
            feed = load_payroll_feeds(
                pricing, [ids["acct_a_id"]], {},
            )[ids["acct_a_id"]]
            assert feed.employee_at(calendar.saved()[0]) == Decimal("750")

    @staticmethod
    def _projected(calendar, years_out):
        """Return a projected period about *years_out* past the horizon.

        Off :meth:`~app.services.pay_calendar.PayCalendar.projection_axis`,
        the producer every projecting surface uses, so the period is the
        shape production hands the feed and not one assembled by hand.
        """
        far = calendar.horizon() + timedelta(days=365 * years_out)
        axis = calendar.projection_axis(calendar.opening_bound(), far)
        return next(p for p in axis if p.start_date > far - timedelta(days=30))

    def test_a_period_PAST_the_horizon_is_priced_not_held(
        self, app, db, seed_user, seed_second_user, seed_periods,
    ):
        """Both resolvers answer a projected period off the engine.

        **The capability plan step salary:S3-e-2 exists for** (ruling
        **R-SAL15**): the feed used to be two dictionaries over the saved
        window and HELD an invented figure past it.  A period ten years past
        the horizon carries ``period_id = None`` and says so through
        :attr:`~app.services.pay_calendar.DerivedPeriod.is_projected`; the
        feed prices it through the same pricer as a saved one, so a flat
        deduction and a raise-free profile read the same figures there as
        on the first saved payday -- and both are hand-computed, so the case
        still grades something if feed and pricer were to move together.
        """
        with app.app_context():
            ids = _seed_deductions_fixture(app, db, seed_user, seed_second_user)
            profile = (
                db.session.query(SalaryProfile)
                .filter_by(user_id=ids["user_id"], name="Active")
                .one()
            )
            params = self._params_for(ids["acct_a_id"], profile.id)
            db.session.commit()
            calendar = calendar_for(ids["user_id"])
            feed = load_payroll_feeds(
                pricing_over(calendar), [ids["acct_a_id"]],
                {ids["acct_a_id"]: params},
            )[ids["acct_a_id"]]
            projected = self._projected(calendar, 10)
            assert projected.is_projected is True
            assert projected.start_date > calendar.horizon()
            assert feed.employee_at(projected) == Decimal("500")
            assert feed.gross_at(projected) == Decimal("3846.15")

    def test_a_cadence_skip_PAST_the_horizon_prices_zero_not_the_previous_payday(
        self, app, db, seed_user, seed_second_user, seed_periods,
    ):
        """A 24-per-year line is ``$0.00`` on a projected month's third payday.

        The rule the deleted dictionaries had to be TOTAL for -- a skipped
        payday is an explicit zero, never a gap the hold would read the
        previous payday's figure over -- and the hold's own docstring got the
        cadence wrong about it (**N-544**).  On a resolver the engine answers
        it directly, and this grades that on a PROJECTED period: the third
        payday of a month found by counting the axis's own paydays per month,
        an oracle independent of the engine's ``is_third_paycheck`` flag.  11
        of the developer's 12 live deductions carry this cadence.
        """
        with app.app_context():
            ids = _seed_deductions_fixture(app, db, seed_user, seed_second_user)
            profile = (
                db.session.query(SalaryProfile)
                .filter_by(user_id=ids["user_id"], name="Active")
                .one()
            )
            twice_monthly = PaycheckLine(
                salary_profile_id=profile.id,
                target_account_id=ids["acct_b_id"],
                name="B-twice-monthly", amount=Decimal("100"),
                calc_method_id=_flat_id(),
                paycheck_line_kind_id=ref_cache.paycheck_line_kind_id(
                    PaycheckLineKindEnum.PRE_TAX_DEDUCTION,
                ),
                is_active=True,
            )
            db.session.add(twice_monthly)
            db.session.flush()
            # The 24-per-year shape (plan step salary:R15-b): every paycheck,
            # at most 2 a month.
            make_line_cadence_rule(db.session, twice_monthly, 24)
            db.session.commit()
            calendar = calendar_for(ids["user_id"])
            feed = load_payroll_feeds(
                pricing_over(calendar), [ids["acct_b_id"]], {},
            )[ids["acct_b_id"]]
            far = calendar.horizon() + timedelta(days=365 * 2)
            axis = calendar.projection_axis(calendar.opening_bound(), far)
            by_month: "dict[tuple[int, int], list]" = {}
            for period in axis:
                if period.is_projected:
                    by_month.setdefault(
                        (period.start_date.year, period.start_date.month), [],
                    ).append(period)
            three = next(
                paydays for paydays in by_month.values() if len(paydays) == 3
            )
            # The fixture's own $250 line pays every payday; the 24-per-year
            # line skips the third, so the sum drops by exactly its amount.
            assert feed.employee_at(three[0]) == Decimal("350")
            assert feed.employee_at(three[1]) == Decimal("350")
            assert feed.employee_at(three[2]) == Decimal("250")

    def test_a_resolver_fired_past_the_loader_issues_NO_query(
        self, app, db, seed_user, seed_second_user, seed_periods,
    ):
        """Walking a feed over projected periods touches the database not at all.

        The loader hands :mod:`app.services.investment_projection` callables
        that run inside its *no database access* contract, and ruling
        **R-SAL15** accepted that design on the claim that no lazy ORM load
        fires mid-walk.  This is the measurement behind the claim: every
        relationship the engine reads is loaded before the walk, so a hundred
        projected periods -- pricing, not memo hits -- issue zero statements.
        Counted at the cursor rather than asserted from the relationship
        definitions, because a ``lazy="joined"`` that is later loosened would
        leave those definitions looking exactly as they do today.

        **The profile carries a RECURRING RAISE whose month the walk crosses
        every year**, because an adversarial review of this step found the
        fixture blind to the one relationship the engine read only on a
        raise's own month: ``get_raise_event`` read ``raise_obj.raise_type``
        (``lazy="joined"`` on ``SalaryRaise``), and a raise-free profile
        never reached that line.  *Since plan step salary:S3-f-1 the engine
        prices ``RaiseTerms`` values and the type's name is resolved from the
        FK through the ref cache at ``for_profile`` -- inside the loader,
        before the count opens -- so no relationship is read on the walk at
        all; the fixture is kept so this case still walks a raise month and
        would see a read that returned there.*  Asserted non-vacuously below:
        at least one walked period prices a paycheck labelled with the raise.
        """
        with app.app_context():
            ids = _seed_deductions_fixture(app, db, seed_user, seed_second_user)
            profile = (
                db.session.query(SalaryProfile)
                .filter_by(user_id=ids["user_id"], name="Active")
                .one()
            )
            params = self._params_for(ids["acct_a_id"], profile.id)
            db.session.add(SalaryRaise(
                salary_profile_id=profile.id,
                raise_type_id=ref_cache.raise_type_id(RaiseTypeEnum.MERIT),
                effective_date=date(2026, 7, 1),
                percentage=Decimal("0.0300"), is_recurring=True,
                terminal_year=None,
            ))
            # And a line WITH a cadence rule (plan step salary:R15-b): the
            # engine reads it through the rule's owner and the owner's
            # profile, so the walk must be query-free on THAT path too -- an
            # adversarial review found the fixture blind to it.
            ruled = PaycheckLine(
                salary_profile_id=profile.id,
                target_account_id=ids["acct_a_id"],
                name="A-twice-monthly", amount=Decimal("40"),
                calc_method_id=_flat_id(),
                paycheck_line_kind_id=ref_cache.paycheck_line_kind_id(
                    PaycheckLineKindEnum.PRE_TAX_DEDUCTION,
                ),
                is_active=True,
            )
            db.session.add(ruled)
            db.session.flush()
            make_line_cadence_rule(db.session, ruled, 24)
            db.session.commit()
            calendar = calendar_for(ids["user_id"])
            pricing = pricing_over(calendar)
            feed = load_payroll_feeds(
                pricing, [ids["acct_a_id"]], {ids["acct_a_id"]: params},
            )[ids["acct_a_id"]]
            far = calendar.horizon() + timedelta(days=365 * 4)
            axis = calendar.projection_axis(calendar.opening_bound(), far)
            projected = [p for p in axis if p.is_projected]
            assert len(projected) >= 100

            statements = []

            def _count(*args):  # pylint: disable=unused-argument
                statements.append(args[2])

            event.listen(db.engine, "before_cursor_execute", _count)
            try:
                for period in projected:
                    feed.employee_at(period)
                    feed.gross_at(period)
            finally:
                event.remove(db.engine, "before_cursor_execute", _count)
            assert statements == []
            # Non-vacuity for the raise path: the pricer the feed read priced
            # at least one walked paycheck with the raise's own label, the
            # first each July's application reaches, so the banner's path
            # (``PayrollBasis.banner_on``) was walked.
            assert any(
                pricing.for_profile(profile).at(period).period.banner.events
                for period in projected
            )
            # Non-vacuity for the cadence path: the ruled line was priced on
            # the walk and skipped on at least one projected third paycheck,
            # so its rule was resolved and walked inside the count.
            taken = [
                any(line.name == "A-twice-monthly"
                    for line in pricing.for_profile(profile).at(period).deductions.pre_tax)
                for period in projected
            ]
            assert any(taken) and not all(taken)

    def test_an_inactive_or_ARCHIVED_line_contributes_nothing(
        self, app, db, seed_user, seed_second_user, seed_periods,
    ):
        """Acct A reads $500, not $500 + $999 + $888.

        Three deductions name Acct A: the active one, an INACTIVE one worth
        $999, and one worth $888 on a profile the owner has ARCHIVED.  The
        second is refused by the deduction loader's own filter and the third
        by :func:`_load_funding_profiles` -- which matters because archiving a
        profile does NOT deactivate its deductions
        (``routes/salary/profiles.delete_profile`` sets ``is_active`` on the
        profile and its template and touches the deductions not at all), so
        the deduction's own flag cannot answer this.  That was a measured
        defect in ``salary:R14-a``'s backfill before its review caught it.
        """
        with app.app_context():
            ids = _seed_deductions_fixture(app, db, seed_user, seed_second_user)
            db.session.commit()
            calendar = calendar_for(ids["user_id"])
            pricing = pricing_over(calendar)
            feed = load_payroll_feeds(
                pricing, [ids["acct_a_id"]], {},
            )[ids["acct_a_id"]]
            assert feed.employee_at(calendar.saved()[0]) == Decimal("500")

    def test_a_gross_is_priced_only_where_a_funding_profile_is_NAMED(
        self, app, db, seed_user, seed_second_user, seed_periods,
    ):
        """``salary_profile_id`` set -> a gross per payday; unset -> none at all.

        Ruling **R-SAL5** and the developer's 2026-09-04 ruling together: the
        employer contribution's basis is the NAMED profile's own paycheck, and
        an account that names none models no employer money rather than
        borrowing whichever profile a reader resolved.  The gross asserted is
        the engine's own for that payday, read back through
        :class:`~app.services.income_service.ProfilePaychecks` -- so this
        grades the FOLD and cannot pass by re-deriving the arithmetic it is
        checking.
        """
        with app.app_context():
            ids = _seed_deductions_fixture(app, db, seed_user, seed_second_user)
            profile = (
                db.session.query(SalaryProfile)
                .filter_by(user_id=ids["user_id"], name="Active")
                .one()
            )
            named = self._params_for(ids["acct_a_id"], profile.id)
            unnamed = self._params_for(ids["acct_b_id"], None)
            db.session.commit()

            calendar = calendar_for(ids["user_id"])
            pricing = pricing_over(calendar)
            feeds = load_payroll_feeds(
                pricing,
                [ids["acct_a_id"], ids["acct_b_id"]],
                {ids["acct_a_id"]: named, ids["acct_b_id"]: unnamed},
            )
            first = calendar.saved()[0]
            # Keyed on the BREAKDOWN's OWN payday, never paired by
            # position: an equality whose two sides share one producer
            # measures nothing, and a ``zip`` here would have shared the exact
            # expression the loader used.  This read the payday out of a
            # ``{period_id: start_date}`` table until plan step salary:S3-d
            # put the payday on the paycheck.  A SECOND pricer, deliberately:
            # the feed's resolver reads the pass's pricer, and reading the
            # same memo back would share the producer under test.
            expected = {
                breakdown.period.payday: breakdown.earnings.gross_biweekly
                for breakdown in pricing_over(calendar).for_profile(
                    profile,
                ).over(calendar.saved())
            }[first.start_date]
            # And the figure itself, hand-computed, so the case still grades
            # something if BOTH sides were to move together: $100,000 over 26
            # paychecks is $3,846.15, and this fixture's profile carries no
            # raise, so every payday in the window prices the same.
            assert expected == Decimal("3846.15")

            assert feeds[ids["acct_a_id"]].funds_employer is True
            assert feeds[ids["acct_a_id"]].gross_at(first) == expected
            assert feeds[ids["acct_b_id"]].funds_employer is False
            assert feeds[ids["acct_b_id"]].gross_at(first) is None

    def test_the_employer_basis_is_base_pay_not_the_gross_a_taxable_earning_joins(
        self, app, db, seed_user, seed_second_user, seed_periods,
    ):
        """Beside a $45 taxable earning the funding profile's basis stays $3,846.15, not $3,891.15.

        Ruling **R-SAL38** (3), plan step salary:R18-b: a percentage figure
        in payroll is a percentage of BASE PAY, and the employer-match
        resolver reads that base.  Until R18-b ``gross_biweekly`` WAS the
        base, so no case here could tell the two apart; this one seeds a
        taxable earning line on the funding profile so they differ by exactly
        the line, and asserts the feed reads the base.
        """
        with app.app_context():
            ids = _seed_deductions_fixture(app, db, seed_user, seed_second_user)
            profile = (
                db.session.query(SalaryProfile)
                .filter_by(user_id=ids["user_id"], name="Active")
                .one()
            )
            db.session.add(PaycheckLine(
                salary_profile_id=profile.id, name="Phone Allowance",
                amount=Decimal("45"),
                calc_method_id=ref_cache.calc_method_id(CalcMethodEnum.FLAT),
                paycheck_line_kind_id=ref_cache.paycheck_line_kind_id(
                    PaycheckLineKindEnum.TAXABLE_EARNING,
                ),
                is_active=True,
            ))
            named = self._params_for(ids["acct_a_id"], profile.id)
            db.session.commit()

            calendar = calendar_for(ids["user_id"])
            feeds = load_payroll_feeds(
                pricing_over(calendar), [ids["acct_a_id"]], {ids["acct_a_id"]: named},
            )
            first = calendar.saved()[0]
            priced = pricing_over(calendar).for_profile(profile).at(first)
            # The line is on the paycheck: gross carries it, base does not.
            assert priced.earnings.base_biweekly == Decimal("3846.15")
            assert priced.earnings.gross_biweekly == Decimal("3891.15")
            assert feeds[ids["acct_a_id"]].gross_at(first) == Decimal("3846.15")

    def test_an_ARCHIVED_funding_profile_models_no_employer_money(
        self, app, db, seed_user, seed_second_user, seed_periods,
    ):
        """A job the owner has left does not fund an employer contribution.

        ``ondelete="RESTRICT"`` keeps the row, and a profile is archived
        rather than deleted here, so the column can outlive the job it names.
        The read refuses it, which is the same answer a ``NULL`` gets.
        """
        with app.app_context():
            ids = _seed_deductions_fixture(app, db, seed_user, seed_second_user)
            archived = (
                db.session.query(SalaryProfile)
                .filter_by(user_id=ids["user_id"], name="Inactive")
                .one()
            )
            params = self._params_for(ids["acct_a_id"], archived.id)
            db.session.commit()

            calendar = calendar_for(ids["user_id"])
            pricing = pricing_over(calendar)
            feed = load_payroll_feeds(
                pricing, [ids["acct_a_id"]],
                {ids["acct_a_id"]: params},
            )[ids["acct_a_id"]]
            assert feed.funds_employer is False

    def test_ANOTHER_OWNERS_profile_prices_nothing(
        self, app, db, seed_user, seed_second_user, seed_periods,
    ):
        """The read scopes by owner, not only the write door.

        ``salary:R14-a`` closed the forged FK at the deduction door and its
        migration had to defend against the same shape in SQL -- measured
        writing a STRANGER's ``salary_profile_id`` onto this owner's row
        before its review caught it.  A read that trusts the column's
        ownership is a read that can be made to price a stranger's salary, so
        it is scoped here too.  The row is written directly, bypassing the
        route guard, precisely because the point is what the READ does with a
        value the door would have refused.
        """
        with app.app_context():
            ids = _seed_deductions_fixture(app, db, seed_user, seed_second_user)
            stranger = (
                db.session.query(SalaryProfile)
                .filter_by(user_id=ids["other_user_id"], name="Other")
                .one()
            )
            params = self._params_for(ids["acct_a_id"], stranger.id)
            db.session.commit()

            calendar = calendar_for(ids["user_id"])
            pricing = pricing_over(calendar)
            feed = load_payroll_feeds(
                pricing, [ids["acct_a_id"]],
                {ids["acct_a_id"]: params},
            )[ids["acct_a_id"]]
            assert feed.funds_employer is False

    def test_the_result_is_total_over_the_accounts_asked_for(
        self, app, db, seed_user, seed_second_user, seed_periods,
    ):
        """An account no payroll funds maps to the EMPTY feed, not to nothing.

        A caller indexes rather than defaulting, so a missing key is a defect
        instead of a silently unfunded account -- the contract
        ``ContributionInputs.absent`` keeps one tier up.
        """
        with app.app_context():
            ids = _seed_deductions_fixture(app, db, seed_user, seed_second_user)
            db.session.commit()
            calendar = calendar_for(ids["user_id"])
            pricing = pricing_over(calendar)
            feeds = load_payroll_feeds(
                pricing,
                [ids["acct_a_id"], ids["other_acct_id"]], {},
            )
            assert set(feeds) == {ids["acct_a_id"], ids["other_acct_id"]}
            unfunded = feeds[ids["other_acct_id"]]
            assert unfunded == AccountPayrollFeed.absent()
            assert unfunded.is_payroll_linked is False
            assert unfunded.funds_employer is False

    def test_no_accounts_issues_no_query(self, app, db, seed_user):
        """An empty account list answers the empty map without a query.

        MEASURED, not inferred from the empty answer (plan step salary:S3-f-2b
        deleted the door's own early return, so the claim now rests on the
        wiring loader's guards): the statements issued between the pricer's
        construction and the answer are counted, and there are none.
        """
        with app.app_context():
            pricing = pricing_over(calendar_for(seed_user["user"].id))
            statements = []

            def _count(*args):  # pylint: disable=unused-argument
                statements.append(args[2])

            event.listen(db.engine, "before_cursor_execute", _count)
            try:
                feeds = load_payroll_feeds(pricing, [], {})
            finally:
                event.remove(db.engine, "before_cursor_execute", _count)
            assert feeds == {}
            assert statements == [], (
                "an empty account list issued a query: " + "; ".join(statements)
            )


# ── salary:S3-f-1: the feed is WIRING loaded once, priced per raise set ──


class TestTheFeedIsBuiltOverTheWiring:
    """``load_payroll_feeds`` is two halves, and the pure half re-prices.

    Plan step **salary:S3-f-1** (ruling **R-SAL20**).  The loader issued its
    queries and built its resolvers in one body, so pricing the same accounts
    under another raise set -- the ``/retirement`` rail's per-raise probe,
    plan step salary:S3-f -- meant either re-issuing every query per probe or
    reaching into the pass's memo for paychecks priced under the rows.
    :func:`load_payroll_wiring` is the queries; :func:`build_payroll_feeds`
    is the resolvers over whatever pricers it is handed.
    """

    @staticmethod
    def _wired(app, db, seed_user, seed_second_user):
        """Acct A funded by the Active profile, which carries a forever raise.

        A recurring 5% July raise from 2026 with no end year, so a 2028 payday
        prices ``$100,000 x 1.05^3 / 26 = $4,452.40`` under the rows and
        ``$100,000 x 1.05 / 26 = $4,038.46`` under terms believed through
        2026.
        """
        ids = _seed_deductions_fixture(app, db, seed_user, seed_second_user)
        profile = (
            db.session.query(SalaryProfile)
            .filter_by(user_id=ids["user_id"], name="Active")
            .one()
        )
        params = TestLoadPayrollFeeds._params_for(ids["acct_a_id"], profile.id)
        row = SalaryRaise(
            salary_profile_id=profile.id,
            raise_type_id=ref_cache.raise_type_id(RaiseTypeEnum.MERIT),
            effective_date=date(2026, 7, 1),
            percentage=Decimal("0.0500"), is_recurring=True,
            terminal_year=None,
        )
        db.session.add(row)
        db.session.commit()
        db.session.refresh(profile)
        terms = (replace(RaiseTerms.of(row), terminal_year=2026),)
        return ids, profile, {ids["acct_a_id"]: params}, terms

    @staticmethod
    def _payday_in(calendar, year):
        """A projected payday in *year* on or after September."""
        axis = calendar.axis(calendar.opening_bound(), date(year, 12, 31))
        return next(p for p in axis if p.start_date.year == year
                    and p.start_date.month >= 9)

    def test_the_composed_door_is_the_two_halves(
        self, app, db, seed_user, seed_second_user, seed_periods,
    ):
        """What the callers still call answers what the halves answer."""
        with app.app_context():
            ids, profile, params_by_account, _ = self._wired(
                app, db, seed_user, seed_second_user,
            )
            calendar = calendar_for(ids["user_id"])
            pricing = pricing_over(calendar)
            account_ids = [ids["acct_a_id"], ids["acct_b_id"]]

            composed = load_payroll_feeds(
                pricing, account_ids, params_by_account,
            )
            wiring = load_payroll_wiring(
                ids["user_id"], account_ids, params_by_account,
            )
            halves = build_payroll_feeds(wiring, {
                pid: pricing.for_profile(p) for pid, p in wiring.profiles.items()
            })

            assert wiring.account_ids == tuple(account_ids)
            assert set(wiring.profiles) == {profile.id}
            assert set(composed) == set(halves) == set(account_ids)
            first = calendar.saved()[0]
            for account_id in account_ids:
                assert (
                    composed[account_id].employee_at(first)
                    == halves[account_id].employee_at(first)
                )
                assert (
                    composed[account_id].gross_at(first)
                    == halves[account_id].gross_at(first)
                )
            # And a figure of its own, so the equality above is not two
            # sides of one producer: Acct A's $500 flat deduction, and its
            # employer half off a $100,000 salary in 2026, before the raise.
            assert halves[ids["acct_a_id"]].employee_at(first) == Decimal("500")
            assert halves[ids["acct_a_id"]].gross_at(first) == Decimal("3846.15")

    def test_one_wiring_is_priced_under_two_raise_sets_with_no_query(
        self, app, db, seed_user, seed_second_user, seed_periods,
    ):
        """The purpose of the split: rebuild the resolvers, re-issue nothing.

        The wiring is loaded once; two builds over it -- the pass's pricer
        under the rows and a pricer under terms believed through 2026 --
        issue zero statements between them, and the two feeds answer the
        September 2028 employer gross as ``$4,452.40`` and ``$4,038.46``.
        """
        with app.app_context():
            ids, profile, params_by_account, terms = self._wired(
                app, db, seed_user, seed_second_user,
            )
            calendar = calendar_for(ids["user_id"])
            pricing = pricing_over(calendar)
            wiring = load_payroll_wiring(
                ids["user_id"], [ids["acct_a_id"]], params_by_account,
            )
            # Both pricers are built BEFORE the count opens (their construction
            # loaded a tax series until plan step salary:X-at-1): the claim is
            # about the builds.
            stored_pricers = {profile.id: pricing.for_profile(profile)}
            believed_pricers = {
                profile.id: pricing.for_profile(profile, terms),
            }
            statements = []

            def _count(*args):  # pylint: disable=unused-argument
                statements.append(args[2])

            event.listen(db.engine, "before_cursor_execute", _count)
            try:
                stored = build_payroll_feeds(wiring, stored_pricers)
                believed = build_payroll_feeds(wiring, believed_pricers)
            finally:
                event.remove(db.engine, "before_cursor_execute", _count)
            assert statements == [], (
                "build_payroll_feeds issued a query; the queries belong to "
                "load_payroll_wiring alone"
            )

            payday = self._payday_in(calendar, 2028)
            acct = ids["acct_a_id"]
            assert stored[acct].gross_at(payday) == Decimal("4452.40")
            assert believed[acct].gross_at(payday) == Decimal("4038.46"), (
                "a feed built over pricers keyed on the terms answered the "
                "rows' gross"
            )

    def test_price_payroll_feeds_prices_each_profile_under_ITS_OWN_terms(
        self, app, db, seed_user, seed_second_user, seed_periods,
    ):
        """The one pricer-map spelling dispatches ``terms_for`` per profile.

        Plan step **salary:S3-f-2b**: :func:`price_payroll_feeds` is what the
        composed door, the batch loader and the picture at each plan point all
        build their pricer maps through, so a wiring with TWO funding profiles
        and a belief that names a raise on only ONE of them must price Acct A
        (the Active profile, its raise believed through 2026) at the believed
        ``$4,038.46`` and Acct B (a second profile, no raises, $52,000) at its
        own ``$52,000 / 26 = $2,000.00`` -- and hand the second profile the
        SAME pricer the rows made, because its believed set equals its rows.
        A dispatch that priced every profile under the first profile's terms,
        or built a fresh pricer for an unchanged set, fails one of the two.
        """
        with app.app_context():
            ids, active, params_by_account, terms = self._wired(
                app, db, seed_user, seed_second_user,
            )
            second = SalaryProfile(
                period_end_days_before_payday=6,
                user_id=ids["user_id"], scenario_id=seed_user["scenario"].id,
                name="Second",
                state_code="NC", is_active=True,
                filing_status_id=active.filing_status_id,
            )
            db.session.add(second)
            start_test_pay_list(second, Decimal("2000.00"))  # $52,000.00 a year / 26
            db.session.flush()
            params_by_account[ids["acct_b_id"]] = (
                TestLoadPayrollFeeds._params_for(ids["acct_b_id"], second.id)
            )
            db.session.commit()
            calendar = calendar_for(ids["user_id"])
            pricing = pricing_over(calendar)
            wiring = load_payroll_wiring(
                ids["user_id"], [ids["acct_a_id"], ids["acct_b_id"]],
                params_by_account,
            )
            assert set(wiring.profiles) == {active.id, second.id}

            def terms_for(profile):
                """Believe the Active raise through 2026; every other row as is."""
                return terms if profile.id == active.id else terms_of(profile.raises)

            feeds = price_payroll_feeds(wiring, pricing, terms_for)

            payday = self._payday_in(calendar, 2028)
            assert feeds[ids["acct_a_id"]].gross_at(payday) == Decimal("4038.46")
            assert feeds[ids["acct_b_id"]].gross_at(payday) == Decimal("2000.00")
            # The unchanged profile's pricer is the rows' own object; the
            # believed one is a different pricer, keyed on its terms.
            assert (
                pricing.for_profile(second, terms_for(second))
                is pricing.for_profile(second)
            )
            assert (
                pricing.for_profile(active, terms_for(active))
                is not pricing.for_profile(active)
            )

    def test_a_pricer_map_missing_a_wired_profile_is_REFUSED(
        self, app, db, seed_user, seed_second_user, seed_periods,
    ):
        """A public door's precondition, refused once by name.

        Without it the two resolvers answer the same mistake two ways: the
        employer half reads ``pricers.get`` and reports the account as funding
        no employer money -- the answer reserved for an absent, archived or
        foreign profile, which the wiring has already filtered out -- while
        the employee half ``KeyError``s on the first period asked.
        """
        with app.app_context():
            ids, profile, params_by_account, _ = self._wired(
                app, db, seed_user, seed_second_user,
            )
            wiring = load_payroll_wiring(
                ids["user_id"], [ids["acct_a_id"]], params_by_account,
            )
            assert profile.id in wiring.profiles

            with pytest.raises(ValueError, match=f"{profile.id}"):
                build_payroll_feeds(wiring, {})
