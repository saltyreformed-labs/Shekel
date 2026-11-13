"""The inputs helper is the engine's result, and each loader returns the rows it names."""

from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from app import ref_cache
from app.enums import EmployerContributionTypeEnum
from app.services.investment_projection import (
    AccountPayrollFeed,
    InvestmentInputs,
    PricedContribution,
    calculate_investment_inputs,
)
from app.services.projection_inputs import (
    build_investment_projection_inputs,
    load_active_deductions_for_account,
    load_active_deductions_for_accounts,
    load_investment_params_for_accounts,
)

from ._helpers import _FakeInvestmentParams, _seed_deductions_fixture


def _feed(paydays, *, employee=None, gross=None):
    """Build the :class:`AccountPayrollFeed` a caller hands the wrapper.

    **The input since plan step salary:R14-b** (ruling **R-SAL2**), in place
    of the ``_FakeDeduction`` this file built: the paycheck engine prices a
    deduction when it prices the paycheck, and :func:`load_payroll_feeds`
    hands this value a resolver over that pricer.  ``TestLoadPayrollFeeds``
    below grades the real resolvers against real rows; the cases here only
    need a feed that answers, so each resolver is a table over *paydays*
    keyed by the period's ``start_date`` -- the one thing a fake period here
    carries.  A ``None`` figure means no resolver at all, which is the state
    :attr:`AccountPayrollFeed.is_payroll_linked` / :attr:`funds_employer`
    read ``False`` for (plan step salary:S3-e-2).
    """
    def _table(figure):
        if figure is None:
            return None
        by_payday = {day: Decimal(str(figure)) for day in paydays}
        return lambda period: by_payday[period.start_date]

    return AccountPayrollFeed(employee=_table(employee), gross=_table(gross))


@dataclass
class _FakePeriod:
    """The one thing ``calculate_investment_inputs`` reads off a period.

    A ``start_date`` and nothing else since plan step C2-f2c, which is the
    whole surface that function has left: the period LIST it took to translate
    a contribution's ``pay_period_id`` into a payday is gone, because the
    loader dates each contribution at the boundary.  Both real period types --
    :class:`~app.models.pay_period.PayPeriod` and
    :class:`~app.services.pay_calendar.DerivedPeriod` -- satisfy exactly this.
    """

    start_date: date


class TestBuildInvestmentProjectionInputsEquivalence:
    """C18-1: lock the helper to the engine's exact result.

    One flat deduction + one settled contribution + employer flat
    percentage produces a fully-populated :class:`InvestmentInputs`.
    Both call paths (direct ``calculate_investment_inputs`` kwargs
    splat AND the new ``build_investment_projection_inputs`` wrapper)
    are exercised on the SAME input objects; their outputs must be
    byte-identical.

    Hand-computed expectations (gross = 100000 / 26 = 3846.15;
    per-period deduction = 500.00; contribution per period =
    400 / 2 = 200.00; total per period = 700.00; YTD over both
    contribution periods = 200 + 200 = 400.00; employer flat 5%
    of gross 3846.15 = 192.31 -- carried as flat_percentage in
    employer_params, not directly computed by this helper):
    """

    @staticmethod
    def _fixture_inputs():
        params = _FakeInvestmentParams(
            assumed_annual_return=Decimal("0.07"),
            annual_contribution_limit=Decimal("23500"),
            employer_contribution_type_id=ref_cache.employer_contribution_type_id(
                EmployerContributionTypeEnum.FLAT_PERCENTAGE,
            ),
            employer_flat_percentage=Decimal("0.05"),
        )
        periods = [
            _FakePeriod(start_date=date(2026, 1, 2)),
            _FakePeriod(start_date=date(2026, 1, 16)),
        ]
        feed = _feed(
            [period.start_date for period in periods],
            employee="500.00", gross="3846.15",
        )
        contributions = [
            PricedContribution(
                account_id=1, payday=periods[0].start_date,
                amount=Decimal("200"), is_confirmed=True,
            ),
            PricedContribution(
                account_id=1, payday=periods[1].start_date,
                amount=Decimal("200"), is_confirmed=True,
            ),
        ]
        return params, feed, contributions, periods

    def test_helper_matches_inline_kwargs_splat(self):
        """build_investment_projection_inputs == calculate_investment_inputs kwargs splat.

        The pre-Commit-18 call shape (direct kwargs splat) and the
        post-Commit-18 helper invocation must produce identical
        :class:`InvestmentInputs` for the same input objects.
        """
        params, feed, contributions, periods = self._fixture_inputs()

        # The pre-Commit-18 inline kwargs splat that lived in each
        # dashboard service.  Reproduced here exactly so a future
        # divergence between the wrapper and the engine surfaces.
        inline_result = calculate_investment_inputs(
            investment_params=params,
            feed=feed,
            all_contributions=contributions,
            current_period=periods[1],
        )

        helper_result = build_investment_projection_inputs(
            params, feed, contributions, periods[1],
        )

        assert isinstance(helper_result, InvestmentInputs)
        assert helper_result.periodic_contribution == inline_result.periodic_contribution
        assert helper_result.employer_params == inline_result.employer_params
        assert (
            helper_result.annual_contribution_limit
            == inline_result.annual_contribution_limit
        )
        assert helper_result.ytd_contributions == inline_result.ytd_contributions
        assert (
            helper_result.ytd_contributions_seed
            == inline_result.ytd_contributions_seed
        )

    def test_helper_returns_expected_decimal_values(self):
        """Hand-computed Decimal arithmetic locks the fixture's expected values.

        - periodic_contribution = 500.00 (the CURRENT payday's payroll figure,
          since plan step salary:R14-b) + (200+200)/2 (avg) = 700.00
        - ytd_contributions = 200 + 200 = 400 (both contributions in
          2026 up to current_period=periods[1])
        - annual_contribution_limit = 23500
        - employer_params.flat_percentage = Decimal("0.05")
        - the employer GROSS is no longer in the dict: it is a fact about a
          payday, so the feed answers it per period (salary:R14-b).
        """
        params, feed, contributions, periods = self._fixture_inputs()
        result = build_investment_projection_inputs(
            params, feed, contributions, periods[1],
        )
        assert result.periodic_contribution == Decimal("700.00")
        assert result.ytd_contributions == Decimal("400")
        assert result.annual_contribution_limit == Decimal("23500")
        assert result.employer_params is not None
        assert result.employer_params["flat_percentage"] == Decimal("0.05")
        assert "gross_biweekly" not in result.employer_params
        assert feed.gross_at(periods[1]) == Decimal("3846.15")


class TestLoadActiveDeductionsHelpers:
    """Query-shape tests for the new deduction loader helpers.

    Uses the live test DB with a small fixture seeded inside each
    test's ``app.app_context()`` so the inserts share the test's
    session: two investment accounts owned by one user, one active
    deduction per account, one inactive deduction (must be
    excluded), one deduction on an inactive salary profile (must be
    excluded), and one deduction owned by another user (must be
    excluded).
    """

    def test_single_account_loader_returns_only_active_owned_rows(
        self, app, db, seed_user, seed_second_user,
    ):
        """Single-account loader filters by user, active profile, active deduction."""
        with app.app_context():
            ctx = _seed_deductions_fixture(
                app, db, seed_user, seed_second_user,
            )
            result = load_active_deductions_for_account(
                ctx["user_id"], ctx["acct_a_id"],
            )
            amounts = sorted(d.amount for d in result)
            # Only the active deduction on the active profile.
            # 999 (inactive) and 888 (inactive profile) excluded.
            assert amounts == [Decimal("500")]

    def test_single_account_loader_rejects_other_user(
        self, app, db, seed_user, seed_second_user,
    ):
        """Single-account loader does not bleed across users."""
        with app.app_context():
            ctx = _seed_deductions_fixture(
                app, db, seed_user, seed_second_user,
            )
            result = load_active_deductions_for_account(
                ctx["user_id"], ctx["other_acct_id"],
            )
            assert result == []

    def test_batch_loader_groups_by_target_account_id(
        self, app, db, seed_user, seed_second_user,
    ):
        """Batch loader returns dict keyed by target_account_id."""
        with app.app_context():
            ctx = _seed_deductions_fixture(
                app, db, seed_user, seed_second_user,
            )
            result = load_active_deductions_for_accounts(
                ctx["user_id"], [ctx["acct_a_id"], ctx["acct_b_id"]],
            )
            assert set(result.keys()) == {ctx["acct_a_id"], ctx["acct_b_id"]}
            # Acct A: only the active 500 deduction (999 + 888 filtered).
            assert (
                [d.amount for d in result[ctx["acct_a_id"]]]
                == [Decimal("500")]
            )
            # Acct B: only the active 250 deduction.
            assert (
                [d.amount for d in result[ctx["acct_b_id"]]]
                == [Decimal("250")]
            )

    def test_batch_loader_empty_account_ids(
        self, app, db, seed_user, seed_second_user,
    ):
        """Batch loader returns {} for empty account_ids without an IN () query."""
        with app.app_context():
            ctx = _seed_deductions_fixture(
                app, db, seed_user, seed_second_user,
            )
            result = load_active_deductions_for_accounts(
                ctx["user_id"], [],
            )
            assert result == {}

    def test_batch_loader_omits_accounts_with_no_deductions(
        self, app, db, seed_user, seed_second_user,
    ):
        """Accounts without active deductions are absent from the dict."""
        from app.enums import AcctTypeEnum
        from app.models.account import Account
        with app.app_context():
            ctx = _seed_deductions_fixture(
                app, db, seed_user, seed_second_user,
            )
            retire_type_id = ref_cache.acct_type_id(AcctTypeEnum.K401)
            bare_acct = Account(
                user_id=ctx["user_id"], name="Bare",
                account_type_id=retire_type_id,
            )
            db.session.add(bare_acct)
            db.session.flush()
            result = load_active_deductions_for_accounts(
                ctx["user_id"],
                [ctx["acct_a_id"], bare_acct.id],
            )
            assert ctx["acct_a_id"] in result
            assert bare_acct.id not in result


class TestLoadInvestmentParamsForAccounts:
    """Query-shape tests for the investment-params batch loader.

    Level 1 balance-seam prep (DRY): this loader is the single home for
    the "which accounts get an :class:`InvestmentParams` row?" decision,
    scoped by the canonical classifier rather than by elimination.  Uses
    the live test DB with a mixed account set seeded inside the test's
    ``app.app_context()`` so the inserts share the test's session.
    """

    def test_returns_only_investment_accounts_with_a_params_row(
        self, app, db, seed_user,
    ):
        """Map holds exactly the INVESTMENT account that has a params row.

        Locks the classifier predicate
        (``classify_account(a) is AccountProjectionKind.INVESTMENT``):
        the loader must scope membership by the classifier, NOT by "does
        this account happen to have an InvestmentParams row?".  Mixed
        input set, all owned by ``seed_user``:

        - a 401(k) (classifier -> INVESTMENT) WITH an
          :class:`InvestmentParams` row -> present, keyed by account_id.
        - a Checking account (classifier -> PLAIN) that ALSO has an
          InvestmentParams row -> still excluded, because the classifier
          filters it out before the query runs.  If the predicate were
          dropped, the widened query would find this row and leak it
          into the result, failing the keys assertion below -- that is
          why the excluded account is deliberately given a row.
        - a second 401(k) (INVESTMENT) with NO params row -> absent,
          because the query finds no matching row for it.
        """
        from app.enums import AcctTypeEnum
        from app.models.account import Account
        from app.models.investment_params import InvestmentParams
        with app.app_context():
            user_id = seed_user["user"].id
            k401_type_id = ref_cache.acct_type_id(AcctTypeEnum.K401)
            checking_type_id = ref_cache.acct_type_id(AcctTypeEnum.CHECKING)

            inv_with_params = Account(
                user_id=user_id, name="401k With Params",
                account_type_id=k401_type_id,
            )
            checking = Account(
                user_id=user_id, name="Everyday Checking",
                account_type_id=checking_type_id,
            )
            inv_without_params = Account(
                user_id=user_id, name="401k No Params",
                account_type_id=k401_type_id,
            )
            db.session.add_all(
                [inv_with_params, checking, inv_without_params],
            )
            db.session.flush()

            none_type_id = ref_cache.employer_contribution_type_id(
                EmployerContributionTypeEnum.NONE,
            )
            # Seed a params row on BOTH the INVESTMENT account and the
            # classifier-excluded Checking account.  The checking row is
            # the predicate trip-wire: only the classifier filter keeps
            # it out of the result, so dropping that filter would widen
            # the query, leak the checking row in, and fail the keys
            # assertion below.
            db.session.add_all([
                InvestmentParams(
                    account_id=inv_with_params.id,
                    assumed_annual_return=Decimal("0.07000"),
                    employer_contribution_type_id=none_type_id,
                ),
                InvestmentParams(
                    account_id=checking.id,
                    assumed_annual_return=Decimal("0.07000"),
                    employer_contribution_type_id=none_type_id,
                ),
            ])
            db.session.flush()

            result = load_investment_params_for_accounts(
                [inv_with_params, checking, inv_without_params],
            )

            # Exactly the investment-with-params account, keyed by id;
            # the Checking account is filtered by the classifier (despite
            # having a row) and the params-less 401(k) has no row.
            assert set(result.keys()) == {inv_with_params.id}
            assert result[inv_with_params.id].account_id == inv_with_params.id
            assert (
                result[inv_with_params.id].assumed_annual_return
                == Decimal("0.07000")
            )

    def test_empty_accounts_returns_empty_dict(self, app):
        """Empty input returns {} without issuing an IN () query."""
        with app.app_context():
            assert load_investment_params_for_accounts([]) == {}
