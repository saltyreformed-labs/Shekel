"""The dashboard tracks: the savings-goal metro tracks, the debt track passed through whole, and an
income-relative goal's resolved target.
"""

from datetime import date
from decimal import Decimal

from app import ref_cache
from app.enums import GoalModeEnum, IncomeUnitEnum
from app.models.savings_goal import SavingsGoal
from app.services import dashboard_service
from app.services import savings_dashboard_service
from app.services.balance_at import BalanceContext
from tests._test_helpers import create_loan_account, create_savings_account, make_salary_profile

from ._helpers import _TODAY


# ── Tracks: savings goal trajectory passthrough + debt fraction ─────


class TestTracksGoals:
    """Savings-goal metro tracks reshaped from compute_goal_progress."""

    def test_goal_track_passes_through_trajectory(
        self, app, seed_user, seed_periods, db,
    ):
        """A goal track carries name, percent, balance, target, and trajectory.

        A $2,500.00 savings account against a $10,000.00 fixed target:
            progress = 2500 / 10000 * 100 = 25.00%.
        The track exposes the trajectory keys (pace, projected completion
        date, required monthly) verbatim from calculate_trajectory.
        """
        with app.app_context():
            acct = create_savings_account(
                seed_user, db.session, "Goal Account",
                Decimal("2500.00"),
            )
            goal = SavingsGoal(
                user_id=seed_user["user"].id,
                account_id=acct.id,
                name="Emergency Fund",
                target_amount=Decimal("10000.00"),
                target_date=date(2027, 3, 1),
            )
            db.session.add(goal)
            db.session.commit()

            tracks = dashboard_service.compute_tracks_section(
                BalanceContext.build(seed_user["user"].id),
            )
            goal_tracks = [g for g in tracks["goals"] if g["account_id"] == acct.id]
            assert len(goal_tracks) == 1
            track = goal_tracks[0]
            assert track["name"] == "Emergency Fund"
            assert track["account_name"] == "Goal Account"
            assert track["current_balance"] == Decimal("2500.00")
            assert track["target_amount"] == Decimal("10000.00")
            # 2500 / 10000 * 100 = 25.00.
            assert track["progress_pct"] == Decimal("25.00")
            assert track["target_date"] == date(2027, 3, 1)
            # Trajectory keys present (the metro-track contract).
            assert "pace" in track
            assert "projected_completion_date" in track
            assert "required_monthly" in track
            assert "monthly_contribution" in track

    def test_goal_track_matches_savings_trajectory(
        self, app, seed_user, seed_periods, db,
    ):
        """The track's trajectory fields equal the /savings producer's verbatim.

        Proves the reshape passes calculate_trajectory's outputs through
        unchanged rather than recomputing them.
        """
        # pylint: disable=import-outside-toplevel
        from app.services import savings_dashboard_service

        with app.app_context():
            acct = create_savings_account(
                seed_user, db.session, "Trajectory Account",
                Decimal("3000.00"),
            )
            goal = SavingsGoal(
                user_id=seed_user["user"].id,
                account_id=acct.id,
                name="House",
                target_amount=Decimal("9000.00"),
                target_date=date(2027, 6, 1),
            )
            db.session.add(goal)
            db.session.commit()

            tracks = dashboard_service.compute_tracks_section(
                BalanceContext.build(seed_user["user"].id),
            )
            track = next(
                g for g in tracks["goals"] if g["account_id"] == acct.id
            )
            sav = next(
                gd for gd in savings_dashboard_service.compute_goal_progress(
                    BalanceContext.build(seed_user["user"].id),
                )
                if gd.goal.account_id == acct.id
            )
            assert track["pace"] == sav.trajectory.pace
            assert (
                track["projected_completion_date"]
                == sav.trajectory.projected_completion_date
            )
            assert track["required_monthly"] == sav.trajectory.required_monthly
            assert track["progress_pct"] == sav.progress_pct

    def test_no_goals_empty_list(self, app, seed_user, seed_periods, db):
        """No active goals -> goals is an empty list."""
        with app.app_context():
            tracks = dashboard_service.compute_tracks_section(
                BalanceContext.build(seed_user["user"].id),
            )
            assert tracks["goals"] == []


class TestTracksDebt:
    """The debt track: the /savings debt summary, passed through whole.

    It was a ``DebtTrack`` pairing that summary with a fraction from a SECOND
    narrow producer until plan step X-u (finding N-109); the fraction is a
    ``DebtSummary`` field now and this tier adds nothing to what the producer
    answered.
    """

    def test_no_debt_returns_none(self, app, seed_user, seed_periods, db):
        """No loan accounts -> debt is None (no track rendered)."""
        with app.app_context():
            tracks = dashboard_service.compute_tracks_section(
                BalanceContext.build(seed_user["user"].id),
            )
            assert tracks["debt"] is None

    def test_debt_track_is_the_summary_whole(
        self, app, seed_user, seed_periods, db,
    ):
        """A loan -> the very DebtSummary /savings renders, fraction included.

        A $1,000.00 auto loan originated 2026-01-01 at 5% for 24 months.
        By the frozen today (2026-03-20) confirmed payments have reduced
        the balance, so:
            principal_paid_fraction =
                (original - current) / original
              = (1000.00 - total_debt) / 1000.00
        which is the SAME current balance the debt summary's total_debt
        reports (same loan set), so the fraction reconciles with the
        summary to the cent.  The data is present (original_principal is
        NOT NULL), so the fraction is a real Decimal, not None.
        """
        with app.app_context():
            create_loan_account(seed_user, db.session, name="Pulse Loan")

            tracks = dashboard_service.compute_tracks_section(
                BalanceContext.build(seed_user["user"].id),
            )
            debt = tracks["debt"]
            assert debt is not None
            # The summary is PASSED THROUGH (plan steps X-s3, X-u), never
            # copied field by field and no longer wrapped: the track IS the
            # value ``/savings`` renders, so a field the summary grows arrives
            # here by construction rather than by someone remembering to copy
            # it.  Asserted as VALUE equality against the producer -- a frozen
            # dataclass compares on every field, so a tier that dropped or
            # altered one fails here, where a "these three keys are present"
            # check passed while the dict silently lost the outlook's third
            # state (finding N-104).
            #
            # Measured limit, stated rather than left implied: this fixture
            # carries no non-loan liability and no salary, so
            # ``debt_without_payoff_date``
            # and ``dti`` sit at their empty values on BOTH sides and a
            # substitution there would not be discriminating.  The firing
            # control therefore plants its defect in ``total_debt``, which the
            # fixture does populate.
            assert debt == savings_dashboard_service.compute_debt_summary(
                BalanceContext.build(seed_user["user"].id),
            )
            # The honest fraction is present and is a Decimal in [0, 1].
            fraction = debt.principal_paid_fraction
            assert isinstance(fraction, Decimal)
            assert Decimal("0") <= fraction <= Decimal("1")
            # Reconcile with the summary: fraction uses the SAME current
            # balance the summary's total_debt sums (one loan, original
            # $1,000.00), so:
            #   fraction == (1000.00 - total_debt) / 1000.00.
            expected = (
                (Decimal("1000.00") - debt.total_debt) / Decimal("1000.00")
            )
            assert fraction == expected

    def test_debt_fraction_zero_before_any_payment(
        self, app, seed_user, seed_periods, db,
    ):
        """A loan whose balance still equals its original -> fraction 0.

        Originate the loan TODAY (frozen 2026-03-20) so no scheduled
        payment has been confirmed yet; the resolver's current balance
        equals the original principal, giving a paid fraction of exactly
        0: (1000.00 - 1000.00) / 1000.00 = 0.
        """
        with app.app_context():
            # Originate TODAY (frozen 2026-03-20) so no scheduled payment
            # is yet confirmed: the resolver's current balance equals the
            # original principal.
            create_loan_account(
                seed_user, db.session, name="Fresh Loan",
                origination_date=_TODAY, payment_day=_TODAY.day,
            )

            tracks = dashboard_service.compute_tracks_section(
                BalanceContext.build(seed_user["user"].id),
            )
            debt = tracks["debt"]
            assert debt.total_debt == Decimal("1000.00")
            # No principal paid yet -> fraction is exactly 0.
            assert debt.principal_paid_fraction == Decimal("0")

    def test_one_render_projects_the_debt_accounts_once(
        self, app, seed_user, seed_periods, db, monkeypatch,
    ):
        """ONE tracks render runs the debt projection ONCE (finding N-109).

        The guard for the redundancy plan step X-u deleted, written against
        the pipeline rather than against the clock: two debt producers each ran
        ``_load_dashboard_core_data`` -> ``_load_account_params`` ->
        ``_compute_account_projections`` over the same loans, so a render paid
        for two full projections and three seam-batch builds (measured at 2 and
        3 on the developer's own data, and at 2 and 3 on the prod-shape clone).

        The fixture has a loan and NO savings goals, so
        ``compute_goal_progress`` returns early without projecting anything and
        every projection this counts is the debt one.  A wall-clock or
        SQL-count assertion would be a flaky proxy for the same fact; the call
        count is the fact.

        Shown FIRING: restoring the second producer's call makes this read 2.
        """
        # pylint: disable=import-outside-toplevel
        from app.services.savings_dashboard_service import _orchestrator

        calls = []
        original = _orchestrator._compute_account_projections

        def _counting(accounts, ctx):
            calls.append([acct.id for acct in accounts])
            return original(accounts, ctx)

        with app.app_context():
            loan = create_loan_account(seed_user, db.session, name="Once Loan")
            monkeypatch.setattr(
                _orchestrator, "_compute_account_projections", _counting,
            )

            tracks = dashboard_service.compute_tracks_section(
                BalanceContext.build(seed_user["user"].id),
            )

            # The render really did produce the debt track (an empty result
            # would satisfy a bare count of zero).
            assert tracks["debt"] is not None
            assert len(calls) == 1
            assert loan.id in calls[0]


# ── Tracks: income-relative goal pace passthrough ───────────────────


class TestTracksIncomeRelativeGoal:
    """An income-relative goal resolves a real target on the track."""

    def test_income_relative_goal_track_has_positive_target(
        self, app, seed_user, seed_periods, db,
    ):
        """An income-relative goal's resolved target is positive on the track.

        With a salary profile, the NULL stored target resolves to a real
        dollar figure (multiplier * net biweekly pay) -- the track must
        carry the resolved (positive) target, not 0, and a pace from the
        trajectory.
        """
        with app.app_context():
            make_salary_profile(seed_user, db.session, name="Track Salary")
            acct = create_savings_account(
                seed_user, db.session, "IR Goal Account",
                Decimal("4000.00"),
            )

            ir_id = ref_cache.goal_mode_id(GoalModeEnum.INCOME_RELATIVE)
            paychecks_id = ref_cache.income_unit_id(IncomeUnitEnum.PAYCHECKS)
            goal = SavingsGoal(
                user_id=seed_user["user"].id,
                account_id=acct.id,
                name="3 Paychecks",
                goal_mode_id=ir_id,
                income_unit_id=paychecks_id,
                income_multiplier=Decimal("3.00"),
                target_amount=None,
            )
            db.session.add(goal)
            db.session.commit()

            tracks = dashboard_service.compute_tracks_section(
                BalanceContext.build(seed_user["user"].id),
            )
            track = next(
                g for g in tracks["goals"] if g["account_id"] == acct.id
            )
            assert track["target_amount"] > Decimal("0.00")
            # The pace key is present (None when no contribution / target
            # date, a string otherwise) -- the track exposes it either way.
            assert "pace" in track
