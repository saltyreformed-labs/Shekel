"""What one render prices: one paycheck pricer per salary profile."""

from datetime import date
from decimal import Decimal

from app import ref_cache
from app.enums import EmployerContributionTypeEnum, GoalModeEnum, IncomeUnitEnum, TxnTypeEnum
from app.models.investment_params import InvestmentParams
from app.models.salary_profile import SalaryProfile
from app.models.savings_goal import SavingsGoal
from app.models.transaction_template import TransactionTemplate
from app.utils.dates import display_today
from tests._test_helpers import (
    counting_calls,
    generate_row_of,
    make_every_period_rule,
    make_recurring_raise,
)

from ._helpers import _CALENDAR_DOOR, _seed_projecting_account


#: The PAYCHECK-PRICER door, as ``(module path, attribute)``.  One
#: :class:`~app.services.income_service.ProfilePaychecks` per salary profile
#: per render is the budget (plan steps **salary:R14-b**, **salary:S3-d**);
#: see :class:`TestOnePaycheckProjectionPerProfilePerRender` for why this door
#: and not ``calculate_paycheck``.
#:
#: **It was ``project_profile`` until plan step salary:S3-d deleted that
#: function.**  Counting a CONSTRUCTION rather than a projection is the same
#: question asked of the new shape: a pricer answers paydays on demand and
#: memoizes them, so building a second one for a profile a render already has
#: a pricer for is exactly the missed-memo defect this class was written
#: after.  What it can no longer see -- because the shape removed it -- is a
#: single producer re-projecting a window it had already projected.
_PROJECTION_DOOR = ("app.services.income_service", "ProfilePaychecks")


#: The amount model's salary-row door, as ``(module path, attribute)``: the
#: ONE producer of a salary row's figure (``salary_net_for``, plan step
#: X-au-d).  Counted by the both-paths case in
#: :class:`TestOnePaycheckProjectionPerProfilePerRender` to prove the render
#: it measures actually priced a row through the amount model, without which
#: "one pricer" is true of a render that never took the second path.
_SALARY_ROW_DOOR = ("app.services.income_service", "salary_net_for")


class TestOnePaycheckProjectionPerProfilePerRender:
    """Every render below runs the paycheck ENGINE once per salary profile.

    **The third count of one question, and the one the other two were blind
    to** (plan step **salary:R14-b**).  ``TestOneReadPassPerRender`` proves a
    render opens one pass; ``TestOneCalendarDerivationPerRender`` proves every
    producer below it reads that pass's CALENDAR.  Neither can see a producer
    that holds the pass, reads its calendar, and then runs the paycheck engine
    over the owner's whole saved window again -- which is exactly what
    ``salary:R14-b`` introduced and what an adversarial review of that step
    measured: ``/savings`` ran ``calculate_paycheck`` **61** times on a
    3-account, 10-period fixture where the pre-step tree ran it once per
    account, because ``_contribution_inputs_for_account`` is the batch loader
    over a one-element set and each call re-projected the whole window.

    :class:`~app.services.income_service.ProfilePaychecks` is the door counted
    rather than ``calculate_paycheck`` for two reasons: it is the ONE spelling
    of a profile's projection (ledger row **N-443**, closed at
    ``salary:R14-a``), and the inner call's count is a property of the owner's
    schedule LENGTH, so counting it would make the assertion a function of the
    fixture's payday count rather than of the code under test.

    The budget is ONE per active salary profile: a profile's paychecks are a
    function of the profile, the calendar and the RAISE SET they are priced
    under -- the third since plan step salary:S3-f-1 -- and every render here
    prices the stored set alone, so a second pricer for the same profile is a
    memo that was missed.
    :meth:`~app.services.balance_at.BalanceContext.paychecks` is that memo.
    **The one legitimate second construction is a ``/retirement/readiness``
    request carrying a per-raise probe at a NON-stored year** (plan step
    salary:S3-f-2b): the probed set is a different function, so it is a
    pricer of its own, and :meth:`test_a_probed_raise_set_is_exactly_one_more_pricer`
    pins it at exactly two -- the stored set's and the probed set's -- while
    the same request carrying the STORED year stays at one.

    **Plan step salary:S3-d moved the door and NARROWED what this class has
    left to catch, and the narrowing is worth stating.**  It counted
    ``project_profile`` calls against a ``payroll_breakdowns`` dict passed as
    an OPTIONAL argument -- ``breakdowns=None`` meant "no memo", and two of
    that argument's four callers passed it inside the step that added it,
    which is what this class was written after.  Nothing is passed a dict now;
    a consumer is handed the pricer, so that particular hole is closed by the
    shape rather than by this assertion.

    **What it still catches is a SECOND pricer, and since plan step
    salary:C12 none exists on purpose.**  Until C12,
    :class:`~app.services.income_service.SalaryPricing` derived its own,
    because ``cash_ledger.amount_basis`` was built from an owner and a
    scenario alone and had nothing to hand it (ledger row **P63**); the basis
    is built OVER the pass's pricer now, so a render that prices a salary ROW
    through the amount model and reads a payroll FEED holds one pricer for
    both.  :meth:`test_savings_prices_a_salary_row_and_a_feed_through_one_pricer`
    is the case that reads both paths and is the one C12 turned from 2 to 1;
    the three renders above it read one path each and were 1 before.  This
    class stays: it is what keeps a second source from returning.
    """

    @staticmethod
    def _fund_the_account(db, seed_user, account):
        """Name the owner's profile as the account's funding job.

        Without the link (**R-SAL5**) the loader resolves no profile, projects
        nothing, and the count reads ZERO on the fixed tree and the broken one
        alike -- the arm-that-cannot-fail shape this file's own fixture
        docstring warns about one function up.  ``_seed_projecting_account``
        already created the profile; this is what makes it FUND something.
        """
        profile = (
            db.session.query(SalaryProfile)
            .filter_by(user_id=seed_user["user"].id, is_active=True)
            .first()
        )
        params = (
            db.session.query(InvestmentParams)
            .filter_by(account_id=account.id).one()
        )
        params.employer_contribution_type_id = (
            ref_cache.employer_contribution_type_id(
                EmployerContributionTypeEnum.FLAT_PERCENTAGE,
            )
        )
        params.employer_flat_percentage = Decimal("0.0500")
        params.salary_profile_id = profile.id
        db.session.commit()

    def test_savings_projects_each_profile_once(
        self, app, db, auth_client, seed_user, seed_periods_today,
    ):
        """GET /savings builds one paycheck pricer for the owner's profile."""
        with app.app_context():
            account = _seed_projecting_account(
                db, seed_user, seed_periods_today,
            )
            self._fund_the_account(db, seed_user, account)

        with counting_calls(_PROJECTION_DOOR) as counts:
            resp = auth_client.get("/savings")

        assert resp.status_code == 200
        assert counts["ProfilePaychecks"] == 1, (
            f"/savings built the owner's paycheck pricer "
            f"{counts['ProfilePaychecks']} times; every producer below the "
            "route's one read pass must read that pass's pricer"
        )

    def test_retirement_projects_each_profile_once(
        self, app, db, auth_client, seed_user, seed_periods_today,
    ):
        """GET /retirement builds one paycheck pricer for the profile.

        The third of the memo's call sites (``retirement_projection
        .load_projection_batch``), which an adversarial review noted this
        class did not cover even though the site is one of the three.
        """
        with app.app_context():
            account = _seed_projecting_account(
                db, seed_user, seed_periods_today,
            )
            self._fund_the_account(db, seed_user, account)

        with counting_calls(_PROJECTION_DOOR) as counts:
            resp = auth_client.get("/retirement")

        assert resp.status_code == 200
        assert counts["ProfilePaychecks"] == 1, (
            f"/retirement built the owner's paycheck pricer "
            f"{counts['ProfilePaychecks']} times; the page holds one read pass "
            "and its batch load and its seam reads must share that pass's "
            "pricer"
        )

    @staticmethod
    def _seed_a_salary_row(db, seed_user, seed_periods_today):
        """Give the owner's profile a definition and ONE generated paycheck row.

        What makes ``/grid`` price a salary row through the AMOUNT MODEL
        (``ctx.amounts()`` -> ``SalaryPricing.net_for``), which is the second
        of the two paths ledger row **P63** was about.  The row is the
        engine's own (:func:`~tests._test_helpers.generate_row_of`), derived
        and undated by hand, so the amount model dispatches it to the salary
        rule rather than to a stated figure.
        """
        profile = (
            db.session.query(SalaryProfile)
            .filter_by(user_id=seed_user["user"].id, is_active=True)
            .one()
        )
        template = TransactionTemplate(
            user_id=seed_user["user"].id,
            account_id=seed_user["account"].id,
            category_id=next(iter(seed_user["categories"].values())).id,
            transaction_type_id=ref_cache.txn_type_id(TxnTypeEnum.INCOME),
            name="Paycheck",
        )
        db.session.add(template)
        db.session.flush()
        make_every_period_rule(db.session, template)
        profile.template_id = template.id
        db.session.flush()
        row = generate_row_of(template, seed_periods_today[0])
        db.session.commit()
        return row

    def test_savings_prices_a_salary_row_and_a_feed_through_one_pricer(
        self, app, db, auth_client, seed_user, seed_periods_today,
    ):
        """GET /savings builds ONE pricer when it reads BOTH paycheck paths.

        **The case plan step salary:C12 turned from 2 to 1** (ledger row
        **P63**), and the one the three cases above are blind to: each of
        them reads ONE path.  Here the owner's profile FUNDS an investment
        account, so the seam's payroll feed prices the profile through
        ``ctx.paychecks()``, AND the profile's definition has a generated row
        in the reported window, so the cash fold under the same render
        prices that row through the amount model (``ctx.amounts()``).  Until
        C12 the amount model built a pricer of its own over a calendar of its
        own (``cash_ledger.amount_basis`` took two ids and had nothing to
        hand it), so this count read 2 and the calendar count 2 -- measured
        2026-09-12 with ``ctx.amounts()`` mutated back to the pass-less
        constructor as the negative control.  The basis is built over the
        pass's pricer now, so both paths hit one memo.

        ``/savings`` and not ``/grid``, and the reason is recorded because
        the first draft chose the grid: the grid's cash view prices the
        salary row and never asks the feed for a paycheck, so it built ONE
        pricer on the broken tree too and the control could not fire.  The
        amount-model assertion is not decoration either: an owner whose
        profile has no generated row reads 1 on the broken tree, because the
        amount model never prices a paycheck and never builds anything -- so
        the case pins that ``salary_net_for`` ran, which is the path this
        count depends on.  (A first draft asserted the row's id appeared in
        the page body; ``/savings`` renders no transaction id, and a small
        integer is a substring of half a page, so that graded nothing -- an
        adversarial review of this step measured it.)
        """
        with app.app_context():
            account = _seed_projecting_account(
                db, seed_user, seed_periods_today,
            )
            self._fund_the_account(db, seed_user, account)
            self._seed_a_salary_row(db, seed_user, seed_periods_today)

        with counting_calls(_PROJECTION_DOOR) as counts, \
                counting_calls(_CALENDAR_DOOR) as calendars, \
                counting_calls(_SALARY_ROW_DOOR) as rows:
            resp = auth_client.get("/savings")

        assert resp.status_code == 200
        assert rows["salary_net_for"] >= 1, (
            "the amount model priced no salary row on this render, so the "
            "count below cannot see the second path this case exists for"
        )
        assert counts["ProfilePaychecks"] == 1, (
            f"/savings built the owner's paycheck pricer "
            f"{counts['ProfilePaychecks']} times while pricing a salary row "
            "AND a payroll feed; the amount model must read the pass's pricer "
            "(plan step salary:C12), not derive a second one"
        )
        assert calendars["derive_periods"] == 1, (
            f"/savings derived the pay calendar {calendars['derive_periods']} "
            "times; the amount model's pricer is the pass's, over the pass's "
            "one calendar"
        )

    def test_savings_prices_its_current_paycheck_through_the_pricer(
        self, app, db, auth_client, seed_user, seed_periods_today,
    ):
        """GET /savings builds the pricer even when the profile funds nothing.

        The ``/savings`` twin of the ``/retirement`` case below, for plan step
        salary:C12-b: with nothing funded the feed loader wires no profile and
        builds nothing, so the ONE pricer this render builds is the one the
        page's current pay is priced through.  On the tree before C12-b --
        a direct ``calculate_paycheck`` in ``_metrics`` -- this count read 0.
        The page prices the current pay on every full render, goal or not
        (the build has no early return ahead of it), so the goal below does
        not gate the count; it is what makes the page PUBLISH a figure off
        that paycheck, so a wrong one is visible on the surface and not only
        in this counter.
        """
        with app.app_context():
            _seed_projecting_account(db, seed_user, seed_periods_today)
            db.session.add(SavingsGoal(
                user_id=seed_user["user"].id,
                account_id=seed_user["account"].id,
                name="One paycheck",
                goal_mode_id=ref_cache.goal_mode_id(
                    GoalModeEnum.INCOME_RELATIVE,
                ),
                income_unit_id=ref_cache.income_unit_id(
                    IncomeUnitEnum.PAYCHECKS,
                ),
                income_multiplier=Decimal("1.00"),
                is_active=True,
            ))
            db.session.commit()

        with counting_calls(_PROJECTION_DOOR) as counts:
            resp = auth_client.get("/savings")

        assert resp.status_code == 200
        assert counts["ProfilePaychecks"] == 1, (
            f"/savings built the owner's paycheck pricer "
            f"{counts['ProfilePaychecks']} times for a profile that funds no "
            "account; the page's current pay must be priced through the "
            "pass's pricer (plan step salary:C12-b), which is the one "
            "construction this render has left"
        )

    def test_retirement_prices_its_current_paycheck_through_the_pricer(
        self, app, db, auth_client, seed_user, seed_periods_today,
    ):
        """GET /retirement builds the pricer even when the profile funds nothing.

        **The case above cannot see plan step salary:S3-f-2a, and this one
        exists because it cannot.**  There the profile FUNDS the account, so
        the feed loader builds its pricer and the count reads 1 whether the
        verdict's current paycheck comes off that pricer or off a direct
        engine call beside it -- the direct call (``_compute_current_pay``,
        the door that priced without the calibration) constructed no
        ``ProfilePaychecks`` at all.  Here nothing funds the account, so the
        feed loader wires no profile and builds nothing: the ONE pricer this
        render builds is the one the current paycheck is priced through.  On
        the tree before S3-f-2a this count read 0.

        It is still within the budget the class states -- one per active
        profile -- and it is the direction the case above is blind to: a
        regression re-pricing the current paycheck outside the pass reads 0
        here and 1 there.
        """
        with app.app_context():
            _seed_projecting_account(db, seed_user, seed_periods_today)

        with counting_calls(_PROJECTION_DOOR) as counts:
            resp = auth_client.get("/retirement")

        assert resp.status_code == 200
        assert counts["ProfilePaychecks"] == 1, (
            f"/retirement built the owner's paycheck pricer "
            f"{counts['ProfilePaychecks']} times for a profile that funds no "
            "account; the verdict's current paycheck must be priced through "
            "the pass's pricer (plan step salary:S3-f-2a), which is the one "
            "construction this render has left"
        )

    @staticmethod
    def _seed_forever_raise(db, seed_user):
        """One recurring 5% January raise from next year, believed forever."""
        profile = (
            db.session.query(SalaryProfile)
            .filter_by(user_id=seed_user["user"].id, is_active=True)
            .first()
        )
        effective = display_today().year + 1
        row = make_recurring_raise(
            profile.id, db.session, effective_date=date(effective, 1, 1),
        )
        db.session.commit()
        return row.id, effective

    def test_a_probed_raise_set_is_exactly_one_more_pricer(
        self, app, db, auth_client, seed_user, seed_periods_today,
    ):
        """A readiness refresh at a probed end year builds TWO pricers, not N.

        The rail's pair at the STORED answer (``none``, the row's own) is the
        stored plan and builds one pricer -- the probe cannot be told from a
        plain refresh.  At a NON-stored year the request derives the stored
        picture (the what-if panel's baseline) AND the probed one, and the
        probed set is a legitimate second pricer: the current paycheck, the
        payroll feed and every retire-later probe at that set must all read
        that ONE pricer, so the count is exactly two.  Three or more is a
        reader that built its own instead of asking the pass's memo.
        """
        with app.app_context():
            account = _seed_projecting_account(
                db, seed_user, seed_periods_today,
            )
            self._fund_the_account(db, seed_user, account)
            raise_id, effective = self._seed_forever_raise(db, seed_user)

        with counting_calls(_PROJECTION_DOOR) as stored_counts:
            resp = auth_client.get(
                "/retirement/readiness",
                query_string={
                    f"raise_end_mode_{raise_id}": "none",
                    f"raise_end_year_{raise_id}": "",
                },
                headers={"HX-Request": "true"},
            )
        assert resp.status_code == 200
        assert stored_counts["ProfilePaychecks"] == 1, (
            "the rail's pre-filled pair built a second pricer; a probe equal "
            "to the stored year must resolve to the stored plan"
        )

        with counting_calls(_PROJECTION_DOOR) as probed_counts:
            resp = auth_client.get(
                "/retirement/readiness",
                query_string={
                    f"raise_end_mode_{raise_id}": "year",
                    f"raise_end_year_{raise_id}": str(effective),
                },
                headers={"HX-Request": "true"},
            )
        assert resp.status_code == 200
        assert probed_counts["ProfilePaychecks"] == 2, (
            f"a probed readiness refresh built {probed_counts['ProfilePaychecks']} "
            "pricers; the stored set and the probed set are the only two"
        )

    def test_investment_projects_each_profile_once(
        self, app, db, auth_client, seed_user, seed_periods_today,
    ):
        """GET the investment dashboard builds one pricer for the profile.

        The account carries an employer contribution and NAMES the job that
        funds it, so the feed actually prices a gross -- without the link the
        loader resolves no profile at all and the count would read 0 on the
        fixed tree and the broken one alike.
        """
        with app.app_context():
            account = _seed_projecting_account(
                db, seed_user, seed_periods_today,
            )
            self._fund_the_account(db, seed_user, account)
            account_id = account.id

        with counting_calls(_PROJECTION_DOOR) as counts:
            resp = auth_client.get(f"/accounts/{account_id}/investment")

        assert resp.status_code == 200
        assert "$" in resp.get_data(as_text=True), (
            "the dashboard did not render figures, so this count was taken "
            "over a producer that returned early"
        )
        assert counts["ProfilePaychecks"] == 1, (
            f"/investment built the owner's paycheck pricer "
            f"{counts['ProfilePaychecks']} times; the page holds one read pass "
            "and both its seam reads and its own feed load must share that "
            "pass's pricer"
        )
