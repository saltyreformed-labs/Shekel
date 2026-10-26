"""Savings dashboard service tests: debt-to-income, raise-aware, and the current pay as the read
pass prices it.

Split from ``tests/test_services/test_savings_dashboard_service.py`` by ``tools/split`` (ruling
R-BAL301).
"""

from decimal import ROUND_HALF_UP, Decimal

import pytest

from app import ref_cache
from app.enums import GoalModeEnum, IncomeUnitEnum, WithholdingKindEnum
from app.models.pay_stub import PayStub
from app.models.ref import FilingStatus
from app.models.salary_profile import SalaryProfile
from app.models.savings_goal import SavingsGoal
from app.services import savings_dashboard_service
from app.services.balance_at import BalanceContext

from tests._test_helpers import (
    add_test_pay_stub,
    current_pay_period,
    make_salary_profile,
    fica_only_law,
    start_test_pay_list,
)
from app.models.amount_ownership import AmountOwnership

from ._helpers import _create_small_loan


class TestDTI:
    """Tests for debt-to-income ratio computation.

    DTI = total_monthly_payments / gross_monthly * 100.

    **Gross monthly is the owner's paycheck at the OWNER's cadence** since plan
    step R7a-2a -- ``gross_biweekly * PayCadence.periods_per_year / 12``, where
    this docstring used to say "``* 26 / 12`` (biweekly, not semi-monthly)".
    ``budget.pay_schedule.cadence_days`` is user-selectable 1..365, so the
    parenthetical was describing a constraint the schema does not have.  The
    conversion happens in ``_debt_summary_with_dti``; ``_dti_metrics`` takes
    the monthly figure and performs none.
    """

    def test_dti_no_salary(
        self, app, db, seed_user, seed_periods,
    ):
        """C-5.12-6: Loans exist but no salary profile yields no DTI block.

        The whole block is absent, not three fields set to ``None`` (plan step
        X-s3): "no income source" is ONE state and is now spelled once.
        """
        with app.app_context():
            _create_small_loan(seed_user, db.session)

            result = savings_dashboard_service.compute_dashboard_data(
                BalanceContext.build(seed_user["user"].id),
            )
            ds = result["debt_summary"]
            assert ds is not None
            assert ds.dti is None

    def test_dti_with_salary(
        self, app, db, seed_user, seed_periods,
    ):
        """C-5.12-5: Known DTI from specific monthly debt and gross pay.

        Gross biweekly = annual_salary / 26.
        gross_monthly = gross_biweekly * 26 / 12 = annual_salary / 12.
        $78,000 / 12 = $6,500.
        A $1,000 loan at 5% for 24 months: monthly P&I ~ $43.87.
        DTI = 43.87 / 6500 * 100 = ~0.7%.
        """
        with app.app_context():
            filing = db.session.query(FilingStatus).first()
            profile = SalaryProfile(
                user_id=seed_user["user"].id,
                scenario_id=seed_user["scenario"].id,
                filing_status_id=filing.id,
                name="DTI Salary",
                state_code="NC",
            )
            db.session.add(profile)
            start_test_pay_list(profile, Decimal("3000.00"))  # $78,000.00 a year / 26
            _create_small_loan(seed_user, db.session)
            db.session.commit()

            result = savings_dashboard_service.compute_dashboard_data(
                BalanceContext.build(seed_user["user"].id),
            )
            ds = result["debt_summary"]
            assert ds.dti is not None
            assert isinstance(ds.dti.ratio, Decimal)
            assert ds.dti.label == "healthy"
            # The $6,500 denominator, pinned through the ratio: $78,000 / 26 =
            # $3,000 biweekly -> $3,000 * 26 / 12 = $6,500 monthly.  The gross
            # is not stored on the value object (plan step X-s3 -- an input
            # already spent, with no app/ reader), so the assertion divides by
            # it instead of reading it back.
            assert ds.dti.ratio == (
                ds.total_monthly_payments / Decimal("6500.00")
                * Decimal("100")
            ).quantize(Decimal("0.1"), rounding=ROUND_HALF_UP)

    def test_dti_zero_debt(
        self, app, db, seed_user, seed_periods,
    ):
        """C-5.12-13: Salary exists, all loans paid off -> DTI = 0.0%."""
        from app import ref_cache as rc
        from app.enums import StatusEnum
        from app.services.transfer_service import (
            TransferSpec, create_transfer, settle_transfer,
        )

        with app.app_context():
            filing = db.session.query(FilingStatus).first()
            profile = SalaryProfile(
                user_id=seed_user["user"].id,
                scenario_id=seed_user["scenario"].id,
                filing_status_id=filing.id,
                name="DTI Salary",
                state_code="NC",
            )
            db.session.add(profile)
            start_test_pay_list(profile, Decimal("3000.00"))  # $78,000.00 a year / 26
            acct = _create_small_loan(seed_user, db.session)
            payment = create_transfer(
                TransferSpec(
                    user_id=seed_user["user"].id,
                    from_account_id=seed_user["account"].id,
                    to_account_id=acct.id,
                    pay_period_id=seed_periods[7].id,
                    scenario_id=seed_user["scenario"].id,
                    amount_ownership=AmountOwnership.own(Decimal("1100.00")),
                    status_id=rc.status_id(StatusEnum.PROJECTED),
                    category_id=seed_user["categories"]["Rent"].id,
                ),
            )
            # Created Projected, then settled: the create makes no
            # settled transfer (ruling R-BAL379).
            settle_transfer(payment.id, seed_user["user"].id)
            db.session.commit()

            # All debt paid off -> recorded as a balance true-up to $0.
            from tests._test_helpers import insert_trueup_event  # pylint: disable=import-outside-toplevel
            insert_trueup_event(acct.loan_params, Decimal("0.00"))
            db.session.commit()

            result = savings_dashboard_service.compute_dashboard_data(
                BalanceContext.build(seed_user["user"].id),
            )
            ds = result["debt_summary"]
            assert ds.dti is not None
            assert ds.dti.ratio == Decimal("0.0")
            assert ds.dti.label == "healthy"

    def test_the_dti_denominator_is_a_monthly_figure_the_caller_converted(
        self, app,
    ):
        """``_dti_metrics`` takes MONTHLY income and performs no conversion.

        Plan step R7a-2a.  This function used to take the per-PAYCHECK gross
        and convert it against a hardcoded ``26 / 12``; a DTI is a ratio of two
        monthly figures, so the conversion was never its job, and owning it
        made the function need the owner's pay schedule to answer a question
        that is ``None`` whenever no salary is configured.

        The assertion is that the SAME gross figure produces the same ratio
        whatever the owner's cadence -- because the cadence has already been
        applied by the caller.  Hand-computed: $3,500 of PITI against $8,666.67
        of monthly income is 40.4%, which is the "moderate" band.  A conversion
        smuggled back in here would move one of these two and not the other.
        """
        from app.services.savings_dashboard_service._metrics import (
            _dti_metrics,
        )
        # $4,000 a paycheck at 26/year -> $8,666.67 a month; at 52/year ->
        # $17,333.33.  BOTH are handed in already converted, so this function
        # sees only the monthly figure and cannot tell them apart.
        moderate = _dti_metrics(Decimal("3500.00"), Decimal("8666.67"))
        healthy = _dti_metrics(Decimal("3500.00"), Decimal("17333.33"))

        assert moderate is not None and healthy is not None
        assert moderate.ratio == Decimal("40.4")
        assert moderate.label == "moderate"
        assert healthy.ratio == Decimal("20.2")
        assert healthy.label == "healthy"

    def test_the_dti_block_is_absent_without_income_and_reads_no_schedule(
        self, app,
    ):
        """A zero gross answers ``None`` before anything is converted.

        The control for the 500 an adversarial review of plan step R7a-2a
        found: an owner with a mortgage and no salary profile reaches the debt
        summary, and this branch is why the page must not have resolved their
        pay schedule to get here.  ``_dti_metrics`` takes no cadence at all
        now, so the property is structural -- this pins the branch that makes
        it safe for the CALLER to skip the resolution.
        """
        from app.services.savings_dashboard_service._metrics import (
            _dti_metrics,
        )
        assert _dti_metrics(Decimal("3500.00"), Decimal("0.00")) is None

    def test_dti_thresholds(self, app):
        """C-5.12-7 / C-5.12-14 / C-5.12-15: DTI threshold boundary values.

        35.9% -> healthy (< 36)
        36.0% -> moderate (not < 36)
        43.0% -> moderate (not > 43)
        43.1% -> high (> 43)
        """
        from app.services.savings_dashboard_service._metrics import _get_dti_label
        assert _get_dti_label(Decimal("35.9")) == "healthy"
        assert _get_dti_label(Decimal("36.0")) == "moderate"
        assert _get_dti_label(Decimal("43.0")) == "moderate"
        assert _get_dti_label(Decimal("43.1")) == "high"

    def test_dti_over_100(self, app):
        """C-5.12-16: DTI > 100% is valid and labeled 'high'."""
        from app.services.savings_dashboard_service._metrics import _get_dti_label
        assert _get_dti_label(Decimal("124.5")) == "high"

    def test_gross_monthly_uses_26_not_24(self, app):
        """C-5.12-20: Gross monthly = biweekly * 26 / 12, not * 24 / 12.

        Biweekly $3,000:
            Correct: 3000 * 26 / 12 = $6,500.00
            Wrong:   3000 * 24 / 12 = $6,000.00

        Hand-calculation:
            26 biweekly periods per year / 12 months = 2.16667
            3000 * 2.16667 = 6500.00
        """
        gross_biweekly = Decimal("3000.00")
        gross_monthly = (
            gross_biweekly * Decimal("26") / Decimal("12")
        ).quantize(Decimal("0.01"))
        assert gross_monthly == Decimal("6500.00")


class TestDTIRaiseAware:
    """C26 / MED-06 / F-032: DTI gross monthly income is sourced from
    the canonical raise-aware paycheck engine, not the off-engine
    ``annual_salary / pay_periods`` recompute.

    Pre-Commit-26 the savings dashboard read
    ``params["salary_gross_biweekly"]`` (computed in
    ``_load_account_params`` as raw ``annual_salary / pay_periods``,
    with no ``apply_raises`` invocation) and converted to monthly via
    the 26/12 factor.  For any user with an applicable
    :class:`SalaryRaise` the displayed DTI denominator drifted from the
    paycheck engine: the audit's worked example carried a $104,000
    salary + recurring 3% raise where the engine produces $8,926.67
    monthly gross and the off-engine path produced $8,666.67, yielding
    a 27.7% DTI vs the correct 26.9% (`03_consistency.md` F-032 worked
    example).  Commit 26 routes both DTI gross and the savings-goal
    net biweekly pay through ``calculate_paycheck`` for the current
    period, making the engine the single source of truth.
    """

    def test_dti_with_applicable_raise(
        self, app, db, seed_user, seed_periods,
    ):
        """C26-1: With an applicable raise the DTI denominator is the
        post-raise engine gross.

        Salary $4,000.00 a paycheck ($104,000.00 / 26, from the first
        payday, 2026-01-02) + a one-time 3% raise effective month 2 of the
        current period's year.  The raise lands on 2026-02-01, after the pay
        entry (an entry holds every raise landing on or before its payday,
        ruling R-SAL59, so a January raise would be inside it) and before the
        current period's payday, so the engine's per-period gross reflects
        the post-raise pay; the period-to-monthly factor (26/12) is the
        structural biweekly-pay-schedule normalization and is preserved.

        Hand-computed engine output (MED-06 / F-032):
            gross_biweekly     = 4000.00 * 1.03 = 4120.00
            gross_monthly      = 4120.00 * 26 / 12 = 8926.6666...
                                                   -> $8,926.67 ROUND_HALF_UP

        Pre-Commit-26 (off-engine, no raise applied) would have produced:
            biweekly = 104000.00 / 26 = $4,000.00
            monthly  = 4000.00 * 26 / 12 = $8,666.67
        The $260.00/mo gap is the F-032 drift the fix closes.

        DTI ratio uses the engine-derived ``total_monthly_payments``
        (verified by sibling debt-summary tests) over the new
        denominator, quantized to one decimal place.  **The loan is
        $10,000.00, not the helper's $1,000.00**, because a one-decimal
        ratio cannot see the $260.00 over the small loan's $43.87 payment
        (both denominators give 0.5%).  At $10,000.00, 5%, 24 months the
        payment is $438.71: 438.71 / 8,926.67 = 4.91% -> 4.9, where the
        dropped raise's 438.71 / 8,666.67 = 5.06% -> 5.1.
        """
        from app.models.salary_raise import SalaryRaise  # pylint: disable=import-outside-toplevel
        from app.models.ref import RaiseType  # pylint: disable=import-outside-toplevel

        with app.app_context():
            filing = db.session.query(FilingStatus).first()
            profile = SalaryProfile(
                user_id=seed_user["user"].id,
                scenario_id=seed_user["scenario"].id,
                filing_status_id=filing.id,
                name="DTI Raise Salary",
                state_code="NC",
            )
            db.session.add(profile)
            start_test_pay_list(profile, Decimal("4000.00"))  # $104,000.00 a year / 26
            db.session.flush()

            current = current_pay_period(
                seed_user["user"].id
            )
            assert current is not None, (
                "seed_periods must cover today so the engine has a "
                "current period to compute against"
            )

            merit = (
                db.session.query(RaiseType).filter_by(name="merit").one()
            )
            db.session.add(SalaryRaise(
                salary_profile_id=profile.id,
                raise_type_id=merit.id,
                percentage=Decimal("0.0300"),
                effective_month=2,
                effective_year=current.start_date.year,
                is_recurring=False,
            ))
            _create_small_loan(
                seed_user, db.session, principal=Decimal("10000.00"),
            )
            db.session.commit()

            result = savings_dashboard_service.compute_dashboard_data(
                BalanceContext.build(seed_user["user"].id),
            )
            ds = result["debt_summary"]

            # MED-06 / F-032: engine-derived gross_monthly is $8,926.67.
            # Off-engine pre-fix value was $8,666.67 (raise dropped); see the
            # class docstring for the arithmetic.  The denominator is pinned by
            # the ratio identity below rather than read off the value object,
            # which no longer stores it (plan step X-s3) -- and the identity is
            # the stronger pin, since the off-engine $8,666.67 would fail it.
            # total_monthly_payments is the engine-derived monthly P&I
            # from _create_small_loan ($10,000 @ 5% for 24mo); we
            # consume it as an input here so the test pins behaviour
            # without re-deriving the amortization engine's output.
            expected_dti = (
                ds.total_monthly_payments / Decimal("8926.67")
                * Decimal("100")
            ).quantize(Decimal("0.1"), rounding=ROUND_HALF_UP)
            assert ds.dti.ratio == expected_dti

    def test_dti_no_raise_unchanged(
        self, app, db, seed_user, seed_periods,
    ):
        """C26-2: Without any raise, DTI gross matches the historical
        value (no regression for the raise-free majority).

        Engine output for a flat $78,000 salary, no raises:
            annual_salary  = $78,000.00
            gross_biweekly = 78000.00 / 26 = 3000.0000 -> $3,000.00
            gross_monthly  = 3000.00 * 26 / 12 = $6,500.00

        This is byte-identical to the pre-Commit-26 off-engine path for
        the no-raise case (the only F-032 divergence axis is the raise
        omission and the A-01 banker's-default rounding -- neither
        bites on this salary), so the fix is provably a no-op for the
        majority case where no scheduled raise applies.
        """

        with app.app_context():
            filing = db.session.query(FilingStatus).first()
            profile = SalaryProfile(
                user_id=seed_user["user"].id,
                scenario_id=seed_user["scenario"].id,
                filing_status_id=filing.id,
                name="DTI No-Raise Salary",
                state_code="NC",
            )
            db.session.add(profile)
            start_test_pay_list(profile, Decimal("3000.00"))  # $78,000.00 a year / 26
            _create_small_loan(seed_user, db.session)
            db.session.commit()

            result = savings_dashboard_service.compute_dashboard_data(
                BalanceContext.build(seed_user["user"].id),
            )
            ds = result["debt_summary"]
            # DTI ratio matches the pre-fix calculation (no regression), and
            # dividing by $6,500 is what pins the denominator now that the
            # value object does not store it (plan step X-s3).
            expected_dti = (
                ds.total_monthly_payments / Decimal("6500.00")
                * Decimal("100")
            ).quantize(Decimal("0.1"), rounding=ROUND_HALF_UP)
            assert ds.dti.ratio == expected_dti

    def test_dti_uses_paycheck_producer_no_flat_factor(self):
        """C26-3: Verification gate that the DTI block does not reach
        the off-engine ``salary_gross_biweekly`` raw recompute.

        Checks two regression guards on
        ``app/services/savings_dashboard_service.py``:

        1. The shared DTI applier ``_debt_summary_with_dti`` (the single
           home of the debt/DTI rule behind both
           ``compute_dashboard_data`` and the narrow #82
           ``compute_debt_summary``) reads
           ``current_pay.gross_biweekly`` (the engine-derived value
           introduced by Commit 26 as
           ``current_breakdown.earnings.gross_biweekly``; plan step
           salary:C12-b summed it over the owner's profiles into
           :class:`~app.services.savings_dashboard_service._metrics
           .CurrentPay`, whose ``gross_biweekly`` is the engine's gross and
           nothing else), and NONE of the three
           functions subscripts ``params`` with the
           ``"salary_gross_biweekly"`` key (the off-engine value still
           used by the investment-projection path -- F-20 follow-up).
        2. No bare ``Decimal("26") / Decimal("12")`` literal remains
           anywhere in the file.  The paycheck count is
           ``PayCadence.periods_per_year``, DERIVED per owner from
           ``budget.pay_schedule.cadence_days`` since plan step R7a-2a --
           it was ``app/utils/money.py``'s ``PAY_PERIODS_PER_YEAR``
           constant until then (E-24 / HIGH-05 / Commit 23), and a
           re-inlined 26 would be that constant back under another name.

        Guard 1 is implemented as an AST scan rather than a substring
        check so docstrings or comments that mention the off-engine
        key for historical / explanatory reasons do not trip the
        assertion -- only an actual subscript expression that READS
        the value does.
        """
        import ast  # pylint: disable=import-outside-toplevel
        import inspect  # pylint: disable=import-outside-toplevel
        import pathlib  # pylint: disable=import-outside-toplevel
        from app.services import savings_dashboard_service as svc  # pylint: disable=import-outside-toplevel
        # ``compute_dashboard_data`` lives in the package's
        # ``_orchestrator`` sub-module after the Phase 2 split; ``svc``
        # re-exports it, but the source-inspection guards must target the
        # sub-module (the package ``__init__`` holds only the re-export,
        # not the function body).
        from app.services.savings_dashboard_service import (  # pylint: disable=import-outside-toplevel
            _orchestrator,
        )

        # Guard 1a: positive lock -- the engine breakdown attribute is
        # read in the shared DTI applier (the #82 refactor moved the
        # expression out of compute_dashboard_data into the single
        # helper both entry points route through).
        source = inspect.getsource(_orchestrator._debt_summary_with_dti)
        assert "current_pay.gross_biweekly" in source, (
            "DTI block must read gross_biweekly from the paycheck "
            "engine's current pay (MED-06 / F-032; salary:C12-b)."
        )

        # Guard 1b: negative lock -- neither entry point nor the shared
        # DTI applier may read the off-engine ``salary_gross_biweekly``,
        # by either the legacy dict subscript
        # ``params["salary_gross_biweekly"]`` or the dataclass attribute
        # ``params.salary_gross_biweekly``.  That field was REMOVED from
        # ``_AccountParams`` in the balance-seam cleanup (the seam assembles
        # the gross itself, so carrying it here was dead state), so a
        # regression would have to re-add the field AND read it here; this
        # AST guard catches that second step.  ``compute_debt_summary`` is
        # scanned too so the narrow #82 path cannot regress independently.
        dti_fn_names = {
            "compute_dashboard_data",
            "compute_debt_summary",
            "_debt_summary_with_dti",
        }
        tree = ast.parse(inspect.getsource(_orchestrator))
        target_fns = [
            node for node in ast.walk(tree)
            if isinstance(node, ast.FunctionDef) and node.name in dti_fn_names
        ]
        assert len(target_fns) == len(dti_fn_names), (
            "expected DTI functions not all found in module source"
        )
        for target_fn in target_fns:
            for node in ast.walk(target_fn):
                reads_off_engine = (
                    isinstance(node, ast.Subscript)
                    and isinstance(node.value, ast.Name)
                    and node.value.id == "params"
                    and isinstance(node.slice, ast.Constant)
                    and node.slice.value == "salary_gross_biweekly"
                ) or (
                    isinstance(node, ast.Attribute)
                    and isinstance(node.value, ast.Name)
                    and node.value.id == "params"
                    and node.attr == "salary_gross_biweekly"
                )
                if reads_off_engine:
                    raise AssertionError(
                        f"{target_fn.name} must not read the off-engine "
                        "salary_gross_biweekly value for DTI "
                        "(MED-06 / F-032)."
                    )

        # Guard 2: package-wide -- no bare 26/12 literal in any
        # sub-module.  Reads every .py file in the package directory so
        # the check stays module-wide after the Phase 2 split.
        pkg_dir = pathlib.Path(inspect.getfile(_orchestrator)).parent
        file_source = "\n".join(
            p.read_text(encoding="utf-8")
            for p in sorted(pkg_dir.glob("*.py"))
        )
        assert 'Decimal("26") / Decimal("12")' not in file_source, (
            "the paycheck-to-monthly conversion must go through "
            "PayCadence, which derives the owner's paycheck count from "
            "their cadence -- an inlined 26 is the retired "
            "PAY_PERIODS_PER_YEAR constant under another name "
            "(E-24 / HIGH-05, plan step R7a-2a)."
        )

    def test_dti_label_band_correct_with_raise(
        self, app, db, seed_user, seed_periods,
    ):
        """C26-4: For a borderline DTI fixture, the band reflects the
        engine-derived gross, not the off-engine recompute.

        Hand-construction (annotated below) yields a case where the
        pre-Commit-26 path would have labelled the DTI 'moderate' and
        the post-Commit-26 path labels it 'healthy' against the
        documented bands:

            < 36%  -> healthy
            36-43% -> moderate
            > 43%  -> high

        Salary $1,923.08 a paycheck ($50,000 / 26, from the first payday,
        2026-01-02) + a one-time 3% raise effective month 2 of the current
        year -- landing 2026-02-01, after the pay entry, so it applies once
        in the current period (a January raise would be inside the entry,
        ruling R-SAL59):
            gross_biweekly     = 1923.08 * 1.03 = 1980.7724 -> $1,980.77
            gross_monthly      = 1980.77 * 26 / 12 = 4291.6683...
                                                   -> $4,291.67 ROUND_HALF_UP
            36% band floor (engine)  = 4291.67 * 0.36 = $1,545.00

        Pre-Commit-26 off-engine would have been:
            biweekly = 50000 / 26 = $1,923.08
            monthly  = 1923.08 * 26 / 12 = $4,166.67
            36% band floor (off-engine) = 4166.67 * 0.36 = $1,500.00

        For ``total_monthly_payments`` between $1,500.00 and $1,545.00
        the band flips: off-engine labels 'moderate', engine labels
        'healthy'.  This test asserts the band corresponds to the
        engine-derived ratio.  ``_create_small_loan`` produces a P&I
        well below $1,500 so we exercise the deep-healthy case here;
        the band assertion is the structural lock -- the ratio is
        bounded below 36% by the engine denominator, so a regression
        that reverts to the off-engine $4,166.67 denominator would
        still label 'healthy' for THIS fixture (the band crossing only
        bites at larger debt loads), but C26-1 above pins the
        denominator exactly so the band-flip regression cannot hide.
        """
        from app.models.salary_raise import SalaryRaise  # pylint: disable=import-outside-toplevel
        from app.models.ref import RaiseType  # pylint: disable=import-outside-toplevel

        with app.app_context():
            filing = db.session.query(FilingStatus).first()
            profile = SalaryProfile(
                user_id=seed_user["user"].id,
                scenario_id=seed_user["scenario"].id,
                filing_status_id=filing.id,
                name="DTI Band Raise Salary",
                state_code="NC",
            )
            db.session.add(profile)
            start_test_pay_list(profile, Decimal("1923.08"))  # $50,000.00 a year / 26
            db.session.flush()

            current = current_pay_period(
                seed_user["user"].id
            )
            assert current is not None

            merit = (
                db.session.query(RaiseType).filter_by(name="merit").one()
            )
            db.session.add(SalaryRaise(
                salary_profile_id=profile.id,
                raise_type_id=merit.id,
                percentage=Decimal("0.0300"),
                effective_month=2,
                effective_year=current.start_date.year,
                is_recurring=False,
            ))
            _create_small_loan(seed_user, db.session)
            db.session.commit()

            result = savings_dashboard_service.compute_dashboard_data(
                BalanceContext.build(seed_user["user"].id),
            )
            ds = result["debt_summary"]
            # Engine-derived gross_monthly is $4,291.67 (see class + test
            # docstring); pinned through the ratio, since plan step X-s3
            # stopped storing the denominator on the value object.
            assert ds.dti.ratio == (
                ds.total_monthly_payments / Decimal("4291.67")
                * Decimal("100")
            ).quantize(Decimal("0.1"), rounding=ROUND_HALF_UP)
            # Small loan P&I is well under 36% of $4,291.67, so the
            # band is 'healthy' under the engine denominator.
            assert ds.dti.label == "healthy"
            # And the ratio is strictly less than 36 (boundary check).
            assert ds.dti.ratio < Decimal("36.0")


# ── salary:C12-b: /savings' current pay is the pass's pricer's, stub-priced, summed ──


class TestTheCurrentPayIsThePassPricersStubPricedAndSummed:
    """``/savings``' current pay is the pass's pricer's, STUB-PRICED, SUMMED.

    Plan step **salary:C12-b**, rulings **R-SAL25** and **R-SAL26**, ledger
    row **P62**'s last site.  ``_metrics._get_current_paycheck_breakdown``
    priced ONE profile -- an unordered ``.first()`` -- through a direct engine
    call with NO calibration, while every other surface priced the same payday
    through :meth:`~app.services.balance_at.BalanceContext.paychecks` with the
    profile's calibration: measured ``$31.29`` apart on the developer's
    2026-09-10 paycheck, ``$203.38`` on his Emergency Fund target.  Every case
    here goes through the page's own narrow producers and asserts a
    HAND-COMPUTED figure at the surface the page publishes, so neither side of
    the equality is the producer under test.

    The owner is ``TestTheCurrentPaycheckIsThePassPricers``'s
    (``test_retirement_dashboard_service``): a raise-free ``$52,000.00``
    profile on a 14-day cadence, no deductions, and a made-up law with FICA
    for 2026 and no federal or state rules (``fica_only_law``), so every line
    is arithmetic::

        gross per paycheck   52,000.00 / 26            = 2,000.00
        Social Security      2,000.00 x 6.20%          =   124.00
        Medicare             2,000.00 x 1.45%          =    29.00

    With no pay stub the formulas withhold no federal or state (no rules for
    either), so net is ``2,000.00 - 153.00 = 1,847.00``.  With a switched-on
    STUB of the paycheck's pay and (no) lines printing federal ``200.00``,
    state ``100.00``, SS ``124.00`` and Medicare ``29.00``, each tax is the
    stub's: the formulas price the stub's paycheck and the current one alike
    (same gross, both far below the wage base), so their difference is
    ``$0.00``, and net is ``2,000.00 - 453.00 = 1,547.00``.  An
    income-relative goal of THREE PAYCHECKS is ``3 x net``, so the page
    publishes ``$4,641.00`` stub-priced against ``$5,541.00`` not; the priced
    case failed on the tree before plan step C12-b (``5541.00`` where
    ``4641.00`` is asserted).

    **RE-BASED by rulings R-SAL42 and R-SAL100** (plan step salary:S11-c-2c):
    until that step the priced side was an ACTIVE calibration at the same
    four dollars as effective rates (10% / 5% / 6.2% / 1.45%), which the step
    deleted with the rates path; the subject -- the page publishes the
    pricer's priced net, summed -- and every figure are unchanged.

    A SECOND profile of ``$26,000.00`` -- gross ``1,000.00``, SS ``62.00``,
    Medicare ``14.50``, net ``923.50`` -- makes the sum case: one paycheck of
    salary is ``1,847.00 + 923.50 = 2,770.50``, and the DTI denominator is a
    month of the summed gross, ``3,000.00 x 26 / 12 = 6,500.00``.  **Fired on
    the tree before this step, the sum case read ``923.50``**: the old door's
    unordered ``.first()`` returned the SECOND profile, which is
    ``recurrence:R-F16``'s 39% swing observed live rather than cited.  A
    deterministic first-by-id door (the remedy R-SAL5 rejected) would have
    read ``1,847.00`` and a denominator of ``4,333.33``; the DTI case asserts
    that figure as its negative control.
    """

    @pytest.fixture(autouse=True)
    def _fica_and_nothing_else(self, tax_law):
        """Install the law the owner above is priced on: 2026 FICA, nothing else."""
        tax_law(fica_only_law())

    @staticmethod
    def _seed_owner(db, seed_user, *, stubbed, second_profile=False,
                    multiplier=Decimal("3.00")):
        """The owner above, with the pay stub and the second profile as asked.

        The stub is dated on the pay list's first payday, the calendar's first
        saved one, so it is on or before the current paycheck whatever day
        the suite runs.
        """
        profile = make_salary_profile(
            seed_user, db.session, pay=Decimal("2000.00"),  # $52,000.00 a year / 26
        )
        db.session.flush()
        if stubbed:
            add_test_pay_stub(
                profile, profile.pay_entries[0].payday, "2000.00",
                taxes={
                    WithholdingKindEnum.FEDERAL_INCOME: "200.00",
                    WithholdingKindEnum.STATE_INCOME: "100.00",
                    WithholdingKindEnum.SOCIAL_SECURITY: "124.00",
                    WithholdingKindEnum.MEDICARE: "29.00",
                },
            )
        if second_profile:
            make_salary_profile(
                seed_user, db.session, name="Second Job",
                pay=Decimal("1000.00"),  # $26,000.00 a year / 26
            )
        db.session.add(SavingsGoal(
            user_id=seed_user["user"].id,
            account_id=seed_user["account"].id,
            name="Paychecks of salary",
            goal_mode_id=ref_cache.goal_mode_id(GoalModeEnum.INCOME_RELATIVE),
            income_unit_id=ref_cache.income_unit_id(IncomeUnitEnum.PAYCHECKS),
            income_multiplier=multiplier,
            is_active=True,
        ))
        db.session.commit()

    @staticmethod
    def _goal_target(seed_user):
        """The one goal's resolved target, through the page's narrow producer."""
        goals = savings_dashboard_service.compute_goal_progress(
            BalanceContext.build(seed_user["user"].id),
        )
        assert len(goals) == 1
        return goals[0].resolved_target

    def test_the_stub_priced_net_is_what_the_goal_is_stated_in(
        self, app, db, seed_user, seed_periods_today,
    ):
        """A pay stub reaches the goal: three paychecks of $1,547.00."""
        with app.app_context():
            self._seed_owner(db, seed_user, stubbed=True)
            assert self._goal_target(seed_user) == Decimal("4641.00")

    def test_without_a_stub_the_formulas_net_is_what_it_is_stated_in(
        self, app, db, seed_user, seed_periods_today,
    ):
        """The same owner with no pay stub: three paychecks of $1,847.00.

        The pair is the control: one stub toggled, one figure moved, by the
        stub's ``$300.00`` of federal and state and nothing else.
        """
        with app.app_context():
            self._seed_owner(db, seed_user, stubbed=False)
            assert self._goal_target(seed_user) == Decimal("5541.00")

    def test_two_active_profiles_are_summed_into_one_paycheck(
        self, app, db, seed_user, seed_periods_today,
    ):
        """Two jobs, one paycheck of salary: ``1,847.00 + 923.50``."""
        with app.app_context():
            self._seed_owner(
                db, seed_user, stubbed=False, second_profile=True,
                multiplier=Decimal("1.00"),
            )
            assert self._goal_target(seed_user) == Decimal("2770.50")

    def test_the_dti_denominator_is_a_month_of_the_summed_gross(
        self, app, db, seed_user, seed_periods_today,
    ):
        """DTI reads the summed GROSS, so it is stub-blind and job-total.

        The denominator is ``3,000.00 x 26 / 12 = 6,500.00`` a month with both
        profiles; under the first-by-id door it was ``4,333.33``.  The
        numerator is the debt summary's own PITI total (not under test), so
        the ratio is asserted as that total over ``6,500.00``, the way this
        file's existing DTI cases pin the denominator; and it is asserted
        EQUAL with the first profile's pay stub switched on and off, which is
        the other half of the ruling: a stub prices the withholding lines and
        the gross is upstream of them.  (A calibration row toggled until
        plan step salary:S11-c-2c, rulings R-SAL42 / R-SAL100; a stub cannot
        be deleted, so its switch is the toggle.)
        """
        def _summary():
            return savings_dashboard_service.compute_debt_summary(
                BalanceContext.build(seed_user["user"].id),
            )

        with app.app_context():
            self._seed_owner(
                db, seed_user, stubbed=True, second_profile=True,
            )
            _create_small_loan(seed_user, db.session)
            db.session.commit()
            priced = _summary()
            assert priced is not None and priced.dti is not None
            assert priced.dti.ratio == (
                priced.total_monthly_payments / Decimal("6500.00")
                * Decimal("100")
            ).quantize(Decimal("0.1"), rounding=ROUND_HALF_UP)
            # The first-by-id denominator, 4,333.33, would read differently
            # for any positive payment; assert the two are distinguishable so
            # the equality above graded the denominator and not a zero.
            assert priced.total_monthly_payments > Decimal("0.00")
            assert priced.dti.ratio != (
                priced.total_monthly_payments / Decimal("4333.33")
                * Decimal("100")
            ).quantize(Decimal("0.1"), rounding=ROUND_HALF_UP)

            # The toggle is real: the goal (three paychecks of salary, both
            # jobs) moves by the stub's $300.00 a paycheck, 3 x (1,547.00 +
            # 923.50) on and 3 x (1,847.00 + 923.50) off -- so the equal DTI
            # below is the gross being upstream of the taxes, not a toggle
            # that did nothing.
            assert self._goal_target(seed_user) == Decimal("7411.50")

            # Off: same owner, the stub switched off.
            db.session.query(PayStub).update({PayStub.use_for_pricing: False})
            db.session.commit()
            assert self._goal_target(seed_user) == Decimal("8311.50")
            assert _summary().dti.ratio == priced.dti.ratio
