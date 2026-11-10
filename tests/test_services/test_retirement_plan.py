"""
Shekel Budget App -- Retirement Plan Producer Tests (C2-f2d-2)

The step made "the retirement picture at a candidate plan" ONE producer where
there were two, and the two claims that carries are graded here:

* **One derivation per plan.**  Asking for the same
  :class:`~app.services.retirement_plan.PlanPoint` twice returns the SAME
  object, so the readiness hero and the lever card's baseline cannot state
  different figures for one plan.  Asserted as IDENTITY, because equality is
  what the old two-derivation shape already satisfied right up until the day
  it did not.
* **One blended return.**  The rate the assumptions rail displays, the rate the
  readiness chart reverses under, and the rate the contribution lever divides
  the shortfall by are one value.  Its arithmetic is pinned HERE rather than in
  ``tests/manual/verify_retirement_render.py``, because on the developer's own
  data every projecting account carries the SAME assumed return -- so the
  weighting, the zero arms and the two-decimal round-trip are all invisible to
  that harness (measured 2026-08-16: removing the quantization moved zero
  lines).
"""

from dataclasses import replace
from datetime import date
from decimal import Decimal

import pytest

from app.models.pension_profile import PensionProfile
from app.models.ref import AccountType, FilingStatus
from app.models.salary_profile import SalaryProfile
from app.models.user import UserSettings
from app.services import pension_calculator, retirement_levers, retirement_readiness
from app.services.balance_at import BalanceContext
from app.services.payroll_basis import PayrollBasis
from app.services import retirement_plan
from app.services.retirement_plan import (
    _stored_blend_percent,
    load_retirement_inputs,
    picture_at,
)
from app.utils.dates import add_months, display_today
from tests._test_helpers import freeze_today, start_test_pay_list


class _FakeAccount:
    """An account the blended-return fold can key on: an id and nothing else."""

    def __init__(self, account_id):
        self.id = account_id


class _FakeParams:
    """An ``InvestmentParams`` stand-in carrying only the stored return."""

    def __init__(self, assumed_annual_return):
        self.assumed_annual_return = assumed_annual_return


def _projection(account_id, balance):
    """A per-account projection dict carrying only the blend's two inputs."""
    return {
        "account": _FakeAccount(account_id),
        "current_balance": Decimal(balance),
    }


# ── The blended return, on the axes the production data cannot vary ──


class TestStoredBlendPercent:
    """The balance-weighted average of each account's STORED return.

    Every figure below is hand-computed and stated in the docstring, and every
    case varies an axis the developer's own data holds constant.
    """

    def test_weights_by_balance(self):
        """Two accounts blend by balance, not by count.

        $100,000 at 4% and $300,000 at 8%: the count average would be 6.00%,
        the balance-weighted one is
        (100000*0.04 + 300000*0.08) / 400000 = 28000 / 400000 = 0.07 -> 7.00%.
        The two differ, which is what makes this case a control on the
        weighting rather than on the loop.
        """
        assert _stored_blend_percent(
            [_projection(1, "100000.00"), _projection(2, "300000.00")],
            {1: _FakeParams(Decimal("0.04000")),
             2: _FakeParams(Decimal("0.08000"))},
        ) == Decimal("7.00")

    def test_a_zero_rate_account_still_carries_its_weight(self):
        """A 0% sleeve dilutes the blend; it is not skipped (E-12).

        $100,000 at 0.00% and $100,000 at 7.00% blend to
        (0 + 7000) / 200000 = 0.035 -> 3.50%.  A truthiness check on the rate
        drops the first account entirely and reports 7.00% -- twice the true
        blend, and the exact defect CRIT-04 recorded.
        """
        assert _stored_blend_percent(
            [_projection(1, "100000.00"), _projection(2, "100000.00")],
            {1: _FakeParams(Decimal("0.00000")),
             2: _FakeParams(Decimal("0.07000"))},
        ) == Decimal("3.50")

    def test_a_zero_balance_account_contributes_no_weight(self):
        """A $0 account is real and weighs nothing; it does not skew the blend.

        $0 at 20% beside $50,000 at 6% blends to
        (0 + 3000) / 50000 = 0.06 -> 6.00%: the empty account's rate cannot
        pull the average, and its presence cannot make the result undefined.
        """
        assert _stored_blend_percent(
            [_projection(1, "0.00"), _projection(2, "50000.00")],
            {1: _FakeParams(Decimal("0.20000")),
             2: _FakeParams(Decimal("0.06000"))},
        ) == Decimal("6.00")

    def test_an_account_with_no_params_row_is_skipped(self):
        """No stored rate means no known rate: skipped from BOTH sums.

        $100,000 with no params row beside $100,000 at 6% blends to
        6000 / 100000 = 0.06 -> 6.00%, not 3.00%: an unknown rate must not be
        read as a zero one, because that would report a balanced portfolio as
        half its true growth assumption.  A params row with a NULL rate takes
        the same arm, which the second mapping below states.
        """
        blend_inputs = [_projection(1, "100000.00"), _projection(2, "100000.00")]
        assert _stored_blend_percent(
            blend_inputs, {2: _FakeParams(Decimal("0.06000"))},
        ) == Decimal("6.00")
        assert _stored_blend_percent(
            blend_inputs,
            {1: _FakeParams(None), 2: _FakeParams(Decimal("0.06000"))},
        ) == Decimal("6.00")

    def test_no_weight_at_all_falls_back_to_seven_percent(self):
        """Nothing to blend -> the documented 7.00% default, not a division.

        Both arms of "no weight": no accounts at all, and accounts whose
        balances sum to zero.  The second is the one that would raise on a
        naive implementation.
        """
        assert _stored_blend_percent([], {}) == Decimal("7.00")
        assert _stored_blend_percent(
            [_projection(1, "0.00")], {1: _FakeParams(Decimal("0.09000"))},
        ) == Decimal("7.00")

    def test_the_blend_is_quantized_to_the_two_decimals_shown(self):
        """The rate the solver divides by is the rate the rail displays.

        $100,000 at 5% and $200,000 at 10% blend to
        (5000 + 20000) / 300000 = 0.08333... -> 8.333...% , which quantizes to
        8.33%.  Two decimals is not cosmetic here: the assumptions rail renders
        ``"%.2f"``, and the contribution lever divides the shortfall by an
        annuity factor built from this rate, so an unquantized blend would
        solve "contribute $X per period" at a return the page never stated.
        """
        assert _stored_blend_percent(
            [_projection(1, "100000.00"), _projection(2, "200000.00")],
            {1: _FakeParams(Decimal("0.05000")),
             2: _FakeParams(Decimal("0.10000"))},
        ) == Decimal("8.33")


# ── Seeded scenario: one picture per plan ────────────────────────


def _seed_plan(db, seed_user, *, balance, annual_return, months_out=240):
    """Seed a salary profile, a pension, a retirement date and a 401(k).

    Mirrors ``tests/test_services/test_retirement_levers._seed_scenario`` --
    deliberately, because these tests assert relationships BETWEEN the lever
    producer and the readiness producer and must run on the shape both were
    written against.

    Args:
        db: The test database handle.
        seed_user: The ``seed_user`` fixture dict.
        balance: The 401(k)'s opening anchor balance (Decimal).
        annual_return: Its stored assumed annual return (Decimal).
        months_out: Months from today to the planned retirement date.

    Returns:
        The created 401(k) :class:`~app.models.account.Account`.
    """
    # pylint: disable=import-outside-toplevel
    from app import ref_cache
    from app.enums import EmployerContributionTypeEnum
    from app.models.investment_params import InvestmentParams
    from app.services import account_service

    user = seed_user["user"]
    filing = db.session.query(FilingStatus).first()
    retirement_date = add_months(date.today(), months_out)

    profile = SalaryProfile(
        period_end_days_before_payday=6,
        user_id=user.id,
        scenario_id=seed_user["scenario"].id,
        filing_status_id=filing.id,
        name="Day Job",
        state_code="NC",
        is_active=True,
    )
    db.session.add(profile)
    start_test_pay_list(profile, Decimal("3076.92"))  # $80,000.00 a year / 26
    db.session.flush()

    settings = (
        db.session.query(UserSettings).filter_by(user_id=user.id).one()
    )
    settings.planned_retirement_date = retirement_date
    db.session.add(PensionProfile(
        user_id=user.id,
        salary_profile_id=profile.id,
        name="State Pension",
        benefit_multiplier=Decimal("0.01500"),
        consecutive_high_years=4,
        hire_date=date(2010, 1, 1),
        planned_retirement_date=retirement_date,
        is_active=True,
    ))

    inv_type = db.session.query(AccountType).filter_by(name="401(k)").one()
    acct = account_service.create_account(
        account_service.AccountSpec(
            user_id=user.id,
            account_type_id=inv_type.id,
            name="401k",
            anchor_balance=balance,
        ),
    )
    db.session.flush()
    db.session.add(InvestmentParams(
        account_id=acct.id,
        assumed_annual_return=annual_return,
        employer_contribution_type_id=ref_cache.employer_contribution_type_id(
            EmployerContributionTypeEnum.NONE
        ),
    ))
    db.session.commit()
    return acct


class TestOnePicturePerPlan:
    """One plan point, one derivation -- asserted as identity, not equality.

    **Equality is what the shape this step replaced already satisfied.**  Before
    it, the readiness verdict and the lever card's month-0 probe were two
    independent computations that agreed on the developer's data (funded ratio
    ``0.7463``, required ``$1,120,707.00``, projected ``$836,398.65``) and would
    have gone on agreeing until one of them was edited.  A test asserting they
    are EQUAL passes in both worlds and so grades nothing about the change; a
    test asserting they are the same OBJECT passes only in this one.
    """

    def test_the_same_point_is_derived_once(
        self, app, db, seed_user, seed_periods_today,
    ):
        """Two asks for one plan point return the identical object."""
        with app.app_context():
            _seed_plan(
                db, seed_user,
                balance=Decimal("100000.00"),
                annual_return=Decimal("0.10500"),
            )
            inputs = load_retirement_inputs(
                BalanceContext.build(seed_user["user"].id),
            )
            stored = inputs.stored_plan
            assert picture_at(inputs, stored) is picture_at(inputs, stored)
            delayed = replace(stored, month_offset=24)
            assert picture_at(inputs, delayed) is picture_at(inputs, delayed)

    def test_different_points_are_different_pictures(
        self, app, db, seed_user, seed_periods_today,
    ):
        """The memo distinguishes every field of the point.

        The failure this guards is the one the C2-f2d-1 seed memo already paid
        for: a key that omits a term of its own value hands one caller another
        caller's answer.  Each point below differs from the stored plan in
        exactly ONE field, so a key that drops that field returns the stored
        plan's picture and the assertion fails on the field that was dropped.
        """
        with app.app_context():
            _seed_plan(
                db, seed_user,
                balance=Decimal("100000.00"),
                annual_return=Decimal("0.10500"),
            )
            inputs = load_retirement_inputs(
                BalanceContext.build(seed_user["user"].id),
            )
            stored_picture = picture_at(inputs, inputs.stored_plan)
            for varied in (
                replace(inputs.stored_plan, month_offset=24),
                inputs.plan_with(swr_override=Decimal("0.0300")),
                inputs.plan_with(return_rate_override=Decimal("0.02000")),
            ):
                assert picture_at(inputs, varied) is not stored_picture

    def test_the_lever_baseline_is_the_readiness_picture(
        self, app, db, seed_user, seed_periods_today,
    ):
        """The page's two cards read ONE derivation of the stored plan.

        The route derives the picture, shapes the readiness verdict from it and
        hands the same loaded inputs to the lever solver; the solver's month-0
        probe must therefore be a memo HIT rather than a second derivation.
        Asserted by identity through the memo, which is the only way to tell a
        shared answer from two equal ones.
        """
        with app.app_context():
            _seed_plan(
                db, seed_user,
                balance=Decimal("100000.00"),
                annual_return=Decimal("0.10500"),
            )
            inputs = load_retirement_inputs(
                BalanceContext.build(seed_user["user"].id),
            )
            page_picture = picture_at(inputs, inputs.stored_plan)
            retirement_readiness.readiness_from_picture(page_picture)
            # COUNT the derivations rather than inspect the memo afterwards.
            # Reading `inputs.picture_memo[stored] is page_picture` proves
            # nothing: the line above wrote that entry and `picture_at` never
            # overwrites a key, so the assertion holds even if the solver
            # derived its own picture and threw it away (adversarial code
            # review, 2026-08-16).
            derived = []
            real = retirement_plan._derive_picture
            retirement_plan._derive_picture = (
                lambda i, pt: derived.append(pt) or real(i, pt)
            )
            try:
                retirement_levers.compute_lever_data(inputs)
            finally:
                retirement_plan._derive_picture = real
            assert inputs.stored_plan not in derived, (
                "the lever solver derived the stored plan again instead of "
                "taking the picture the page had already computed"
            )

    def test_a_uniform_return_override_is_the_blend(
        self, app, db, seed_user, seed_periods_today,
    ):
        """A what-if return replaces the blend rather than being averaged in.

        Every account's weight carries the override, so the balance-weighted
        average of the overridden rates IS the override -- and the chart's
        needed path, the accounts table and the contribution lever's annuity
        factor must all run in that one frame.  Read off the picture, which is
        where the two arms meet.
        """
        with app.app_context():
            _seed_plan(
                db, seed_user,
                balance=Decimal("100000.00"),
                annual_return=Decimal("0.10500"),
            )
            inputs = load_retirement_inputs(
                BalanceContext.build(seed_user["user"].id),
            )
            assert picture_at(
                inputs, inputs.stored_plan,
            ).blended_return == Decimal("0.105")
            override = Decimal("0.02000")
            assert picture_at(
                inputs, inputs.plan_with(return_rate_override=override),
            ).blended_return == override


class TestTheBatchIsHorizonIndependent:
    """ONE batch serves every candidate plan -- pinned, not asserted in prose.

    :class:`~app.services.retirement_plan.RetirementInputs` loads the
    projection batch once and every point re-projects it.  That is safe only
    because
    :func:`~app.services.retirement_projection.load_projection_batch` reads
    none of the context fields a point replaces -- the horizon, the return
    override, the employer salary basis.  Nothing structural enforces it: the
    day a horizon-aware prefetch is added to that loader, every plan in a
    render silently gets the STORED plan's batch and the call-counting
    architecture gate stays green, because the call count would not change.

    So the property is measured: a batch built at one horizon must project a
    second horizon to the same figures as a batch built at that second horizon.
    """

    def test_a_batch_built_at_one_horizon_projects_another_identically(
        self, app, db, seed_user, seed_periods_today,
    ):
        """Same figures whichever horizon the batch was loaded at."""
        # pylint: disable=import-outside-toplevel
        from app.services import retirement_projection

        with app.app_context():
            _seed_plan(
                db, seed_user,
                balance=Decimal("100000.00"),
                annual_return=Decimal("0.10500"),
            )
            inputs = load_retirement_inputs(
                BalanceContext.build(seed_user["user"].id),
            )
            far = replace(inputs.stored_plan, month_offset=120)
            far_ctx = replace(
                inputs.base_ctx,
                planned_retirement_date=add_months(inputs.base_date, 120),
            )
            axis = retirement_projection.resolve_projection_axis(far_ctx)

            # The batch the render SHARES, loaded at the stored horizon ...
            shared = retirement_projection.project_accounts_with_batch(
                far_ctx, inputs.batch, axis,
            )
            # ... against one loaded at the far horizon itself.
            own = retirement_projection.project_accounts_with_batch(
                far_ctx,
                retirement_projection.load_projection_batch(far_ctx),
                axis,
            )
            assert [p["projected_balance"] for p in shared] == [
                p["projected_balance"] for p in own
            ], (
                "load_projection_batch has become horizon-dependent, so the "
                "one batch a render shares is the STORED plan's and every "
                "other plan is projected from the wrong inputs"
            )
            assert [p["current_balance"] for p in shared] == [
                p["current_balance"] for p in own
            ]
            # And the picture at that far point agrees with the direct walk,
            # which is the path production actually takes.
            assert [
                p["projected_balance"] for p in picture_at(inputs, far).projections
            ] == [p["projected_balance"] for p in own]


# ── salary:S3-f-2b: the point BELIEVES a raise set, and a probe moves it ──


def _seed_believed_plan(db, seed_user, *, effective_year, effective_month=1):
    """The :func:`_seed_plan` scenario plus ONE forever raise reaching every read.

    A recurring 5% raise from *effective_year* (January unless
    *effective_month* says otherwise) with no end year, on the
    profile that the pension projects from, that the income target scales
    from, that the current paycheck is priced from, AND that funds the 401(k)'s
    5%-of-gross employer contribution -- so the four salary-path reads plan
    step salary:S3-f-2b threads the believed set into all read the same raise,
    and a probe that ends it early has to move every one of them.

    Args:
        db: The test database handle.
        seed_user: The ``seed_user`` fixture dict.
        effective_year: The year the raise first applies.
        effective_month: The month it applies in each year (default
            January).

    Returns:
        ``(profile, raise_row, account)``.
    """
    # pylint: disable=import-outside-toplevel
    from app import ref_cache
    from app.enums import EmployerContributionTypeEnum
    from app.models.investment_params import InvestmentParams
    from tests._test_helpers import make_recurring_raise

    account = _seed_plan(
        db, seed_user,
        balance=Decimal("100000.00"),
        annual_return=Decimal("0.10500"),
    )
    profile = (
        db.session.query(SalaryProfile)
        .filter_by(user_id=seed_user["user"].id)
        .one()
    )
    raise_row = make_recurring_raise(
        profile.id, db.session, effective_date=date(effective_year, effective_month, 1),
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
    db.session.refresh(profile)
    return profile, raise_row, account


class TestThePointBelievesARaiseSet:
    """``PlanPoint.raise_end_years`` is RESOLVED, CANONICAL and reaches every read.

    Plan step **salary:S3-f-2b** (rulings **R-SAL20**, **R-SAL21**, **R-SAL13**).
    The point carries one resolved end year per recurring raise on every active
    profile; a probe equal to the stored year IS the stored plan (the memo-key
    discipline ``swr`` already obeys, because every pre-filled rail row submits
    its stored value on every refresh); and the set the point believes is what
    the pension, the income target, the current paycheck and the payroll feeds
    all price from -- one belief per picture, which is the two-beliefs-on-one-
    verdict shape R-SAL20 rejected.
    """

    def test_the_stored_plan_carries_every_recurring_raises_stored_year(
        self, app, db, seed_user, seed_periods_today,
    ):
        """One ``(raise_id, terminal_year)`` per recurring raise; one-time excluded.

        A one-time raise carries no end year at all
        (``ck_salary_raises_terminal_year_only_on_a_recurring_raise``), so it
        is not on the point -- and ``terms_for`` still answers it, with its own
        stored ``None``, because the believed set is total over the profile's
        rows.
        """
        # pylint: disable=import-outside-toplevel
        from app import ref_cache
        from app.enums import RaiseTypeEnum
        from app.models.salary_raise import SalaryRaise
        from app.services.salary_raises import terms_of

        with app.app_context():
            year = display_today().year + 1
            profile, raise_row, _ = _seed_believed_plan(
                db, seed_user, effective_year=year,
            )
            one_time = SalaryRaise(
                salary_profile_id=profile.id,
                raise_type_id=ref_cache.raise_type_id(RaiseTypeEnum.COLA),
                effective_date=date(year, 6, 1),
                flat_amount=Decimal("1500.00"), is_recurring=False,
            )
            db.session.add(one_time)
            db.session.commit()
            db.session.refresh(profile)
            inputs = load_retirement_inputs(
                BalanceContext.build(seed_user["user"].id),
            )

            stored = inputs.stored_plan
            assert stored.raise_end_years == ((raise_row.id, None),)
            # At the stored point the believed set IS the rows' terms, by
            # value -- which is what makes the pricer's memo answer the pricer
            # the feed loader already built rather than a second one.
            assert stored.terms_for(profile) == terms_of(profile.raises)
            assert len(stored.terms_for(profile)) == 2

    def test_a_probe_equal_to_the_stored_year_is_the_stored_plan(
        self, app, db, seed_user, seed_periods_today,
    ):
        """Canonical: the pre-filled rail row's own answer resolves to no what-if.

        The stored raise has NO end year, so the row submits ``("none",
        None)`` -- and, because the mode is authoritative (R-SAL13), ``("none",
        <any year>)`` is the same answer.  Both must be the stored point, and
        the picture at either must be the SAME object as the stored picture,
        or the what-if panel would derive one plan twice and report a delta
        of zero (row P57's shape).
        """
        with app.app_context():
            year = display_today().year + 1
            _, raise_row, _ = _seed_believed_plan(
                db, seed_user, effective_year=year,
            )
            inputs = load_retirement_inputs(
                BalanceContext.build(seed_user["user"].id),
            )
            stored = inputs.stored_plan

            unchanged = inputs.plan_with(
                raise_probes={raise_row.id: ("none", None)},
            )
            stale_year_box = inputs.plan_with(
                raise_probes={raise_row.id: ("none", 2031)},
            )
            assert unchanged == stored
            assert stale_year_box == stored
            assert picture_at(inputs, unchanged) is picture_at(inputs, stored)

            probed = inputs.plan_with(
                raise_probes={raise_row.id: ("year", year)},
            )
            assert probed != stored
            assert probed.raise_end_years == ((raise_row.id, year),)
            assert picture_at(inputs, probed) is not picture_at(inputs, stored)
            # And the retire-later lever's replace carries the belief through.
            assert replace(probed, month_offset=12).raise_end_years == (
                probed.raise_end_years
            )

    def test_a_probe_moves_the_pension_the_target_and_the_funded_account(
        self, app, db, seed_user, seed_periods_today,
    ):
        """Ending the raise after its first year lowers every figure it feeds.

        The salary path is exact and hand-checkable.  $3,076.92 a paycheck
        ($80,000.00 / 26) with a 5% January raise from year N+1 (N is the
        pass's year), evaluated each December 1, each raise step rounded to
        the cent (ruling R-SAL60) and the year's figure the pay times 26
        (ruling R-SAL59):

          stored (no end year):   N+1: 3,076.92 x 1.05 = 3,230.766 -> 3,230.77
                                       x 26 = 84,000.02
                                  N+2: 3,230.77 x 1.05 = 3,392.3085 -> 3,392.31
                                       x 26 = 88,200.06
          probed (ends after N+1): N+1 = 84,000.02, N+2 = 84,000.02 (it stops)

        **The path is read off the walk the pension projects it with** --
        :func:`~app.services.pension_calculator.project_profile_salaries` over
        each point's believed set -- since plan step salary:X-av-3a deleted
        the summary's copy of it; that the pension itself read the probed set
        is the ``monthly_income`` inequality below.

        Every figure downstream of that path is then LOWER under the probe:
        the pension's monthly benefit (its high-4 average is over smaller
        salaries), the income target, and the 401(k)'s projected balance (its
        5%-of-gross employer contribution is priced off a smaller gross from
        N+2 on).  The REQUIRED savings fall too, but that is a fact about
        these parameters rather than a law: the requirement is the gap
        between target and pension scaled by the SWR, and the target falls
        by roughly the take-home rate of the salary delta while the pension
        falls by the benefit multiplier times years of service times the
        high-4 delta -- here about 0.75 against about 0.55 of a comparable
        delta, with the gap staying well above zero, so the target's drop
        wins.  Asserted as inequalities against the same render's stored
        picture, so the case cannot rot into a restatement of the tax
        engine's arithmetic.
        """
        with app.app_context():
            year = display_today().year + 1
            profile, raise_row, account = _seed_believed_plan(
                db, seed_user, effective_year=year,
            )
            inputs = load_retirement_inputs(
                BalanceContext.build(seed_user["user"].id),
            )
            probed_point = inputs.plan_with(
                raise_probes={raise_row.id: ("year", year)},
            )
            stored = picture_at(inputs, inputs.stored_plan)
            probed = picture_at(inputs, probed_point)

            def salary_path(point):
                return dict(pension_calculator.project_profile_salaries(
                    PayrollBasis(
                        profile, inputs.balance_ctx.calendar(),
                        point.terms_for(profile),
                    ),
                    year, year + 1,
                ))

            stored_path = salary_path(inputs.stored_plan)
            probed_path = salary_path(probed_point)
            assert stored_path[year] == Decimal("84000.02")
            assert stored_path[year + 1] == Decimal("88200.06")
            assert probed_path[year] == Decimal("84000.02")
            assert probed_path[year + 1] == Decimal("84000.02"), (
                "the probed end year did not reach the salary path the "
                "pension walks"
            )

            assert probed.pension.monthly_income < stored.pension.monthly_income
            assert (
                probed.net.pre_retirement_net_monthly
                < stored.net.pre_retirement_net_monthly
            ), "the probed end year did not reach the income target"
            assert (
                probed.net.required_retirement_savings
                < stored.net.required_retirement_savings
            )
            by_account = {
                p["account"].id: p["projected_balance"] for p in probed.projections
            }
            stored_by_account = {
                p["account"].id: p["projected_balance"] for p in stored.projections
            }
            assert by_account[account.id] < stored_by_account[account.id], (
                "the probed end year did not reach the payroll feed: the "
                "employer contribution is still priced off the stored raises"
            )

    def test_a_probe_before_this_year_moves_the_current_paycheck(
        self, app, db, monkeypatch, seed_user, seed_periods_52,
    ):
        """R-SAL21's case: an end year before this year re-prices TODAY's paycheck.

        A 5% February raise effective the year BEFORE the current payday's,
        believed forever, has applied twice by that payday; believed only
        through its first year it applied once.  From $3,076.92 a paycheck
        ($80,000.00 / 26), each step rounded to the cent (ruling R-SAL60),
        the current paycheck's gross, biweekly, is then exact:

          stored:  3,076.92 x 1.05 = 3,230.766 -> 3,230.77
                   x 1.05          = 3,392.3085 -> 3,392.31
          probed:  3,230.77

        **The owner's pay record reaches back a year** (52 paydays from
        2026-01-02, today pinned to 2027-03-20) since plan step
        salary:X-av-3a.  The pay entry is recorded on the first payday and
        holds every raise landing on or before it (ruling R-SAL59), and a
        pay-list door refuses a payday below the record -- so on the
        ten-payday window this case ran on, both applications of a raise
        from the year before landed inside the entry and nothing was left to
        probe.  February rather than January for the same reason: a January
        2026 application lands on 2026-01-01, the day before the entry.

        The years are taken off the CURRENT PAYDAY rather than off today: in
        early January the period containing today can open in December, and
        a raise pinned to today's year would then have applied once on both
        sides (a fixture must hold on every day of the calendar).  Priced
        through the pass's pricer under the point's set -- the same door the
        payroll feed prices from -- so the income target's take-home rate and
        the feed cannot believe two different raise sets.
        """
        # pylint: disable=import-outside-toplevel
        from app.services.retirement_dashboard_service import (
            compute_current_paycheck,
        )

        freeze_today(monkeypatch, date(2027, 3, 20))
        with app.app_context():
            current_payday = max(
                period.start_date for period in seed_periods_52
                if period.start_date <= display_today()
            )
            last_year = current_payday.year - 1
            _, raise_row, _ = _seed_believed_plan(
                db, seed_user, effective_year=last_year, effective_month=2,
            )
            inputs = load_retirement_inputs(
                BalanceContext.build(seed_user["user"].id),
            )
            probed = inputs.plan_with(
                raise_probes={raise_row.id: ("year", last_year)},
            )

            def gross_at(point):
                return compute_current_paycheck(
                    inputs.balance_ctx, inputs.gap.salary_profiles,
                    point.terms_for,
                ).earnings.gross_biweekly

            assert gross_at(inputs.stored_plan) == Decimal("3392.31")
            assert gross_at(probed) == Decimal("3230.77")
            assert (
                picture_at(inputs, probed).net.pre_retirement_net_monthly
                < picture_at(inputs, inputs.stored_plan)
                .net.pre_retirement_net_monthly
            )

    def test_a_probe_is_refused_against_the_rows(
        self, app, db, seed_user, seed_periods_today,
    ):
        """Unknown ids and rule-breaking years are refused, ALL of them, by name.

        The ONE end-year rule (``salary_raises.end_year_of``) runs against the
        ROW's effective year -- the same rule the salary form applies to its
        payload -- so the rail cannot accept a year the form would refuse.  An
        id that is not one of this owner's recurring raises is a stale
        bookmark or a URL edit and is refused rather than resolved into an
        unchanged plan; every failing probe is reported, the way a schema
        reports every field.
        """
        with app.app_context():
            year = display_today().year + 1
            _, raise_row, _ = _seed_believed_plan(
                db, seed_user, effective_year=year,
            )
            inputs = load_retirement_inputs(
                BalanceContext.build(seed_user["user"].id),
            )
            stale = raise_row.id + 999

            # Each refusal names WHICH HALF of the pair it is about (plan step
            # salary:S3-f-4, ruling R-SAL33), so the route can render it on
            # that half's control as the Save does; a raise the owner has no
            # row for is a refusal of the pair as a whole and names none.
            with pytest.raises(retirement_plan.RaiseProbeError) as unknown:
                inputs.plan_with(raise_probes={stale: ("none", None)})
            assert unknown.value.errors == {
                stale: (None, "Not one of your recurring raises; reload the page."),
            }

            with pytest.raises(retirement_plan.RaiseProbeError) as early:
                inputs.plan_with(
                    raise_probes={raise_row.id: ("year", year - 1)},
                )
            assert early.value.errors == {
                raise_row.id: (
                    "year",
                    f"A raise cannot end before it starts: it takes effect "
                    f"in {year}.",
                ),
            }
            assert str(early.value) == (
                f"raise {raise_row.id}: A raise cannot end before it starts: "
                f"it takes effect in {year}."
            )

            with pytest.raises(retirement_plan.RaiseProbeError) as unanswered:
                inputs.plan_with(raise_probes={raise_row.id: ("year", None)})
            assert unanswered.value.errors == {
                raise_row.id: (
                    "year",
                    "Enter the last year this raise is believed to happen.",
                ),
            }

            # Both failing probes are reported together, and a valid probe
            # beside them does not rescue the request.
            with pytest.raises(retirement_plan.RaiseProbeError) as both:
                inputs.plan_with(raise_probes={
                    raise_row.id: ("year", year - 1),
                    stale: ("year", year),
                })
            assert set(both.value.errors) == {raise_row.id, stale}

    def test_another_owners_raise_is_refused_as_a_raise_this_owner_lacks(
        self, app, db, seed_user, seed_second_user, seed_periods_today,
    ):
        """The membership is THIS owner's rows; a foreign id is graded against nothing.

        The IDOR axis at the service door (an adversarial review of plan step
        salary:S3-f-4, where the route's case lost its JSON oracle): the probe
        names a year the foreign row would ACCEPT, so a resolver that looked
        the row up by id would resolve it into a believed set and raise
        nothing -- the refusal, with the constant not-found sentence and no
        half named, is the assertion.
        """
        # pylint: disable=import-outside-toplevel
        from tests._test_helpers import make_recurring_raise

        with app.app_context():
            year = display_today().year + 1
            _seed_believed_plan(db, seed_user, effective_year=year)
            other = seed_second_user["user"]
            foreign_profile = SalaryProfile(
                period_end_days_before_payday=6,
                user_id=other.id,
                scenario_id=seed_second_user["scenario"].id,
                filing_status_id=db.session.query(FilingStatus).first().id,
                name="Second Job",
                state_code="NC",
                is_active=True,
            )
            db.session.add(foreign_profile)
            # $65,000.00 a year / 26
            start_test_pay_list(foreign_profile, Decimal("2500.00"))
            db.session.flush()
            foreign = make_recurring_raise(
                foreign_profile.id, db.session, effective_date=date(year + 2, 1, 1),
            )
            db.session.commit()
            inputs = load_retirement_inputs(
                BalanceContext.build(seed_user["user"].id),
            )

            with pytest.raises(retirement_plan.RaiseProbeError) as refused:
                inputs.plan_with(raise_probes={foreign.id: ("year", year + 3)})
            assert refused.value.errors == {
                foreign.id: (None, "Not one of your recurring raises; reload the page."),
            }

    def test_a_probed_set_is_the_one_legitimate_second_pricer(
        self, app, db, seed_user, seed_periods_today,
    ):
        """The stored point re-prices nothing; a probed set costs one pricer.

        ``price_payroll_feeds`` rebuilds the feeds per point over the pass's
        pricers.  At the STORED set that is a memo hit -- zero statements,
        zero ``ProfilePaychecks`` -- because the point's ``terms_for`` equals
        the rows' terms by value and ``for_profile`` keys on the canonical set.
        A PROBED set is a pricer of its own: one construction, and no
        statement at all (measured here rather than quoted).  The construction
        issued three tax-series SELECTs until plan step salary:X-at-1 moved the
        tax law into the code, so a probe now costs no query.
        """
        # pylint: disable=import-outside-toplevel
        from sqlalchemy import event

        from app.extensions import db as _db
        from tests._test_helpers import counting_calls

        with app.app_context():
            year = display_today().year + 1
            _, raise_row, _ = _seed_believed_plan(
                db, seed_user, effective_year=year,
            )
            inputs = load_retirement_inputs(
                BalanceContext.build(seed_user["user"].id),
            )
            probed = inputs.plan_with(
                raise_probes={raise_row.id: ("year", year)},
            )
            statements = []

            def _count(*args):  # pylint: disable=unused-argument
                statements.append(args[2])

            door = ("app.services.income_service", "ProfilePaychecks")
            event.listen(_db.engine, "before_cursor_execute", _count)
            try:
                with counting_calls(door) as stored_counts:
                    retirement_plan._believed_batch(  # pylint: disable=protected-access
                        inputs, inputs.stored_plan,
                    )
                stored_statements = len(statements)
                with counting_calls(door) as probed_counts:
                    retirement_plan._believed_batch(  # pylint: disable=protected-access
                        inputs, probed,
                    )
            finally:
                event.remove(_db.engine, "before_cursor_execute", _count)

            assert stored_counts["ProfilePaychecks"] == 0
            assert stored_statements == 0, (
                "pricing the stored set issued a query; the point's terms "
                "should have hit the pricer the batch loader built"
            )
            assert probed_counts["ProfilePaychecks"] == 1
            assert len(statements) - stored_statements == 0, (
                f"a probed set issued {len(statements) - stored_statements} "
                "statements; a new pricer reads the tax law from the code and "
                "should issue none"
            )
