"""
Shekel Budget App -- Net-Worth Kernel Tests (Loop B Phase 1)

Direct coverage for the shared :mod:`app.services.balance_at._kernel`
promoted out of the year-end summary package: the per-account balance-map
dispatch over the canonical entries-aware resolver.  These tests pin the
kernel's public contract independently of its consumers.

The asset-plus / liability-minus net-worth reduction is NOT covered here.
It lives with its consumer in ``savings_dashboard_service._net_worth``
(``_sum_composition_at_period`` / ``compute_net_worth_today``) and is
covered on the live path by ``test_savings_dashboard_service/test_net_worth.py``:
``test_assets_minus_liabilities`` and
``test_total_liabilities_is_positive_magnitude`` for the reduction, plus one
control per site that crosses a negatively-held liability to what it owes
(``liability_sign.owed``; both sites took ``abs`` until plan step
credit_card:CC-5-5c, ruling R-CC47) --

    hero            test_a_negative_balance_liability_still_adds_its_magnitude
    per-period band test_series_liability_band_holds_a_negative_balance_magnitude

Those two cannot tell ``owed()`` from ``abs()`` -- they agree on a liability
that owes.  The card holding a credit in
``tests/test_services/test_one_liability_sign.py`` is the control that parts
them at both sites.

The kernel's own
``sum_net_worth_at_period`` was deleted as dead code: it had no production
caller, and its unit tests graded it against hand-built dicts rather than
against anything a screen renders.
"""

from decimal import Decimal

from app.services.balance_at import _kernel as net_worth_kernel
from app.services.balance_at._asset_contributions import (
    ContributionInputs,
)
from app.services.scenario_resolver import get_baseline_scenario
from app.services.balance_at import BalanceContext
from tests._test_helpers import all_periods


class TestBuildAccountBalanceMap:
    """Tests for ``build_account_balance_map`` over the plain resolver path."""

    def test_plain_checking_map_seeds_anchor_balance(
        self, app, db, seed_user, seed_periods,
    ):
        """A plain checking account's dense map carries its flat anchor.

        The seed Checking account ($1,000) has no transactions, so every
        period in its dense map holds the flat 1,000.00 anchor balance
        (the canonical entries-aware resolver path).  Asserting the
        current period's entry pins the resolver dispatch.
        """
        with app.app_context():
            user_id = seed_user["user"].id
            scenario = get_baseline_scenario(user_id)
            bctx = BalanceContext.build(user_id)
            owner_periods = all_periods(user_id)
            account = seed_user["account"]

            balances = net_worth_kernel.build_account_balance_map(
                account, bctx,
                ContributionInputs(
                    investment_params=None,
                ),
            )

            assert balances is not None
            # No transactions -> flat anchor at every period.
            assert balances[owner_periods[0].id] == Decimal("1000.00")
            assert balances[owner_periods[-1].id] == Decimal("1000.00")

class TestInterestByPeriodForAccount:
    """Tests for ``interest_by_period_for_account`` (interest-earned accessor).

    The interest VALUE behavior (an account's per-period accrual matching
    the calculator) is locked end-to-end by the HYSA interest tests in the
    ``test_balance_at`` package (``test_cash_flow.py``'s
    ``TestTheInterestChipAndTheBalanceAreOneWalk``, ``test_loan_map.py``'s
    ``test_the_interest_chip_reads_the_same_window`` and ``test_grid.py``'s
    ``test_interest_balances_kind_correct_and_reconcile``) and the
    account-detail route tests in ``test_accounts/test_cash_detail.py`` -- the
    accessor's only consumer is that page's "Interest, next 12 mo" chip.  (It
    was the year-end savings-progress section until plan step F2 deleted that
    package.)

    **Its two no-anchor short-circuit tests were DELETED at plan step
    X-f1c3a, not re-pointed.**  Both handed in a ``SimpleNamespace`` stand-in
    with ``current_anchor_period_id=None`` -- a state the schema forbade with
    a ``NOT NULL`` column and a CHECK beside it, and which ruling R-EH deleted
    the column for.  They graded a branch no account could reach; keeping them
    would have meant keeping the branch (finding N-73).  What survives is the
    accessor's REAL contract, that it is the interest half of the same single
    walk the balance half comes from, pinned by the tests named above.
    """

    def test_it_is_the_interest_half_of_the_same_walk(self, app, db, seed_user):
        """The accessor's map IS ``interest_projection_for_account``'s second half.

        The whole reason the accessor exists is that a consumer wanting only
        the accrual must not pay a SECOND fold for it (finding N-47, ruling
        R-L: the balance a screen renders and the accrual figure beside it
        have to be one walk).  Asserting the delegation is what keeps that
        structural -- a re-implementation here would pass every VALUE test in
        the suite while re-introducing the second walk.
        """
        with app.app_context():
            user_id = seed_user["user"].id
            ctx = BalanceContext.build(user_id)
            account = seed_user["account"]

            _, from_projection = net_worth_kernel.interest_projection_for_account(
                account, ctx,
            )
            assert net_worth_kernel.interest_by_period_for_account(
                account, ctx,
            ) == from_projection
