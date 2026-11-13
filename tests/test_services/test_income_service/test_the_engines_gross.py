"""The per-period gross is the engine's, and every consumer reads that one figure."""

from datetime import date
from decimal import Decimal

from app.extensions import db
from app.services.pay_calendar import calendar_for
from app.services.projection_inputs import load_payroll_feeds
from app.services import balance_at
from app.services.balance_at import BalanceContext
from tests._test_helpers import make_investment_account, pricing_over

from ._helpers import (
    _RAISE_APPLIED_GROSS,
    _NO_RAISE_GROSS,
    _AS_OF_AFTER_RAISE,
    _AS_OF_BEFORE_RAISE,
    _create_profile,
    _add_one_time_raise,
)


class TestThePerPeriodGrossIsTheENGINES:
    """What replaced ``income_service.get_current_gross_biweekly``.

    That helper was the canonical raise-aware gross producer (C17 / F-20 /
    MED-06 / F-032) and plan step **salary:R14-b** DELETED it, because its
    shape was the defect: one figure, read at ONE moment, standing in for a
    series every raise moves.  Its consumers read
    :class:`~app.services.investment_projection.AccountPayrollFeed` now, priced
    per payday by :func:`app.services.projection_inputs.load_payroll_feeds`.

    **The property C17 was written to protect is graded here, and it is
    STRONGER than it was.**  The old cases asked one question at one clock and
    got one answer; these read the same feed at two paydays and see the raise
    land between them, which is what the helper structurally could not show.
    Two of the six old cases have no successor and say so below.
    """

    @staticmethod
    def _feed_for(user_id, profile, account_id):
        """Price *account_id*'s feed off *profile* as the funding job."""
        from app.models.investment_params import InvestmentParams
        params = db.session.query(InvestmentParams).filter_by(
            account_id=account_id,
        ).one()
        params.salary_profile_id = profile.id
        db.session.flush()
        return load_payroll_feeds(
            pricing_over(calendar_for(user_id)), [account_id],
            {account_id: params},
        )[account_id]

    def test_the_raise_lands_ON_ITS_DAY_and_not_before(
        self, app, db, seed_user, seed_periods,
    ):
        """C17-1 and the effective-month case, in ONE feed.

        Hand arithmetic: ``104000 / 26 = 4000.00`` before the March 2026
        raise and ``104000 * 1.03 / 26 = 4120.00`` from it, which the
        paycheck of 03-27 pays.  The paycheck of 03-13 pays 02-22..03-07,
        five weekdays each side of Sunday 03-01 (ruling plan#3089): 2000.00
        + 2060.00 = 4060.00.  This case was named for the raise landing on
        its payday, and read 03-13 at 4120.00, until task plan#3100.
        Pre-Commit-17
        the off-engine sites returned $4,000.00 forever because the raise was
        silently dropped; the helper C17 replaced them with fixed that at ONE
        clock, and the feed fixes it at every payday -- the January and the
        March paycheck are both in this one value, which is the whole of
        finding **D45**'s remedy.
        """
        with app.app_context():
            user_id = seed_user["user"].id
            profile = _create_profile(user_id, seed_user["scenario"].id)
            _add_one_time_raise(profile)
            account = make_investment_account(
                seed_user, db.session, seed_periods[0], Decimal("10000.00"),
            )
            db.session.commit()

            feed = self._feed_for(user_id, profile, account.id)
            calendar = calendar_for(user_id)
            before = calendar.period_containing(_AS_OF_BEFORE_RAISE)
            after = calendar.period_containing(_AS_OF_AFTER_RAISE)
            straddle = calendar.period_containing(date(2026, 3, 15))

            assert feed.gross_at(before) == _NO_RAISE_GROSS
            assert feed.gross_at(after) == _RAISE_APPLIED_GROSS
            assert straddle.start_date == date(2026, 3, 13)
            assert feed.gross_at(straddle) == Decimal("4060.00")

    def test_no_raise_yields_the_byte_identical_pre_fix_value(
        self, app, db, seed_user, seed_periods,
    ):
        """C17-2: no raises -> the engine value equals the pre-fix value.

        With zero raises the post-raise annual salary equals the base, so the
        engine's ``104000 / 26`` matches the pre-fix ``104000 / 26 = 4000.00``
        exactly.  Locks the "no regression for non-raised users" property that
        every C17 site was measured against.
        """
        with app.app_context():
            user_id = seed_user["user"].id
            profile = _create_profile(user_id, seed_user["scenario"].id)
            account = make_investment_account(
                seed_user, db.session, seed_periods[0], Decimal("10000.00"),
            )
            db.session.commit()

            feed = self._feed_for(user_id, profile, account.id)
            calendar = calendar_for(user_id)
            after = calendar.period_containing(_AS_OF_AFTER_RAISE)
            assert feed.gross_at(after) == _NO_RAISE_GROSS

    def test_no_funding_job_REFUSES_rather_than_answering_zero(
        self, app, db, seed_user, seed_periods,
    ):
        """C17-3 INVERTED, and the inversion is the ruling.

        The deleted helper answered ``Decimal("0")`` for an owner with no
        active profile -- and, worse, for one whose calendar simply did not
        cover the clock, which silently deleted a whole contribution plan at
        onboarding and after a horizon lapse.  That zero is why
        ``recurrence:R-F16`` had to REVERT a fix.

        The feed refuses instead: no funding job means no gross, so no
        employer money is modelled and the surface says which (developer,
        2026-09-04).  ``None`` rather than ``$0.00`` because a zero is a basis
        a percentage can be taken of.
        """
        with app.app_context():
            user_id = seed_user["user"].id
            account = make_investment_account(
                seed_user, db.session, seed_periods[0], Decimal("10000.00"),
            )
            db.session.commit()

            from app.models.investment_params import InvestmentParams
            params = db.session.query(InvestmentParams).filter_by(
                account_id=account.id,
            ).one()
            feed = load_payroll_feeds(
                pricing_over(calendar_for(user_id)),
                [account.id],
                {account.id: params},
            )[account.id]

            assert feed.funds_employer is False
            assert feed.gross_at(calendar_for(user_id).saved()[0]) is None


    # **``test_scenario_id_filter_scopes_lookup`` has no successor, and that
    # is the point rather than a gap.**  It pinned the deleted helper's
    # ``scenario_id`` keyword, which existed because the helper SEARCHED for a
    # profile -- an unordered ``.first()`` across the owner's active ones,
    # optionally narrowed to a scenario.  Nothing searches now: the employee
    # half is priced inside the deduction's OWN profile and the employer half
    # off the profile ``budget.investment_params.salary_profile_id`` NAMES
    # (**R-SAL5**).  A filter that narrows a search cannot be tested when
    # there is no search, and the 39% swing that filter was mitigating is not
    # a state the model can reach.  ``TestLoadPayrollFeeds
    # .test_ANOTHER_OWNERS_profile_prices_nothing`` grades what DID survive:
    # that the read is owner-scoped.


class TestConsumerIntegration:
    """C17-4: every downstream consumer reads the same engine value."""

    def test_the_seam_and_the_engine_agree_on_a_raised_paycheck(
        self, app, db, seed_user, seed_periods_today,
    ):
        """C17-4: the seam's feed IS the engine's own breakdown.

        The original case called four consumers' private helpers and asserted
        they agreed on one scalar.  Three of the four read that scalar through
        ``income_service.get_current_gross_biweekly``, which plan step
        **salary:R14-b** deleted, so what is left to grade is the one
        agreement that can still fail: the gross the balance seam hands its
        contribution tier is the gross the paycheck engine put on that
        paycheck -- read back through
        :class:`income_service.ProfilePaychecks`, the ONE spelling of a
        profile's projection, rather than re-derived here.

        Hand arithmetic: ``4000.00 * 1.03 = 4120.00`` (the pay entry,
        $104,000 / 26, raised once).  The raise is effective February 2026:
        the pay entry is recorded on the window's first payday, 2026-01-19
        (today pinned to 2026-03-20), and holds every raise dated by the day
        its paycheck opens, 12-31 (ruling plan#3091), so a raise dated before
        then would be inside it; February lands after it, and before the
        days the current paycheck pays for.
        """
        with app.app_context():
            user_id = seed_user["user"].id
            profile = _create_profile(user_id, seed_user["scenario"].id)
            _add_one_time_raise(
                profile, effective_month=2, effective_year=2026,
            )
            inv = make_investment_account(
                seed_user, db.session, seed_periods_today[0],
                Decimal("10000.00"),
            )
            db.session.commit()

            from app.models.investment_params import InvestmentParams
            params = db.session.query(InvestmentParams).filter_by(
                account_id=inv.id,
            ).one()
            params.salary_profile_id = profile.id
            db.session.commit()

            bctx = BalanceContext.build(user_id)
            seam_feed = balance_at._contribution_inputs_for_account(
                inv, bctx,
            ).feed
            calendar = calendar_for(user_id)
            # Keyed on the BREAKDOWN's OWN payday rather than zipped
            # against the calendar: the loader under test keys the same way,
            # and an equality whose two sides share one SPELLING can agree
            # while both are wrong.  The hand figure below is what keeps this
            # honest even so.
            engine = {
                breakdown.period.payday: breakdown.earnings.gross_biweekly
                for breakdown in pricing_over(
                    calendar,
                ).for_profile(profile).over(calendar.saved())
            }
            current = calendar.period_containing(bctx.as_of)

            assert engine[current.start_date] == _RAISE_APPLIED_GROSS
            assert seam_feed.gross_at(current) == engine[current.start_date]

            # The scoping control: a non-investment account in the same user's
            # set gets NO feed, so the assertion above pins the
            # investment-only pricing rather than a value every account
            # carries.
            checking_inputs = balance_at._contribution_inputs_for_account(
                seed_user["account"], BalanceContext.build(user_id),
            )
            assert checking_inputs.investment_params is None
            assert checking_inputs.feed.funds_employer is False
