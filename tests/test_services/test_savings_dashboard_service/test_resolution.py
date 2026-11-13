"""Savings dashboard service tests: canonical producer routing, one resolution per loan per read
pass, a type-drifted loan row, and no-baseline answers.

Split from ``tests/test_services/test_savings_dashboard_service.py`` by ``tools/split`` (ruling
R-BAL301).
"""

from decimal import Decimal

import pytest

from app.exceptions import BaselineMissingError

from app import ref_cache
from app.services.cash_flow_set import CashFlowSet
from app.enums import CompoundingFrequencyEnum
from app.models.ref import AccountType
from app.models.scenario import Scenario
from app.services import balance_at, savings_dashboard_service
from app.services import account_service
from app.services.balance_at import BalanceContext

from tests._test_helpers import (
    current_pay_period,
    make_projected_envelope_expense,
    open_books_before_the_first_assertion,
)

from ._helpers import (
    _create_small_loan,
    _override_anchor,
    _PURCHASE_DAY,
    _add_entry,
    _add_mortgage_account,
    _add_property_account,
)


class TestCanonicalProducerRouting:
    """C6: /savings balances routed through balance_resolver.balances_for.

    The single-source-of-truth ``balances_for`` owns the transaction
    query (entries eager-loaded) and the anchor resolution
    (AccountAnchorHistory dated SoT), so the per-tile current balance
    cannot disagree with the grid for any input.  These tests pin the
    contract.  Test IDs match remediation_plan.md Commit 6 (C6-1
    through C6-3).
    """

    def test_savings_equals_grid_symptom1(
        self, app, db, seed_user, seed_periods_today,
    ):
        """C6-1: /savings checking tile == grid current-period balance.

        Reproduction of symptom #1 (audit 05_symptoms.md):

          - Real checking anchor 614.29 on the current pay period.
          - One Projected envelope expense ``estimated_amount = 500.00``
            on the same period (so ``sum_projected`` applies).
          - Three CLEARED debit entries 20.00 + 15.71 + 10.00 = 45.71.
            No credit entries, no uncleared debits.

        Hand arithmetic (F-009 worked example):

          cleared_debit   = 20.00 + 15.71 + 10.00 = 45.71
          uncleared_debit = 0
          sum_credit      = 0
          checking_impact = max(500.00 - 45.71 - 0, 0) = 454.29
          anchor_period_balance = 614.29 + 0 - 454.29 = 160.00

        Both the grid and the savings dashboard MUST return
        Decimal("160.00") -- one seam entry, ``balance_at.cash_balance_map``,
        read twice.  Pre-Commit-6, /savings returned Decimal("114.29") via the
        silent-degrade seam.

        The $160.00 survives the basis change at plan step X-c2b2 for a reason
        this fixture makes plain: the account holds ONE asserted balance and the
        only row is still PROJECTED, so the fold has the same assertion to
        replay and the same entries-aware reservation to hold back.  The two
        bases diverge only where money has SETTLED, and nothing here has.
        """
        with app.app_context():
            # Current period == anchor period: seed_periods_today
            # places today in period 4 of a 10-period biweekly window.
            current_period = current_pay_period(
                seed_user["user"].id
            )
            assert current_period is not None
            _override_anchor(
                db.session,
                seed_user["account"],
                current_period,
                Decimal("614.29"),
            )

            txn = make_projected_envelope_expense(
                db.session,
                seed_user=seed_user,
                pay_period=current_period,
                estimated=Decimal("500.00"),
            )
            for amt in (Decimal("20.00"), Decimal("15.71"), Decimal("10.00")):
                _add_entry(
                    db.session,
                    txn=txn,
                    user_id=seed_user["user"].id,
                    amount=amt,
                    is_settled=True,
                    is_credit=False,
                )
            db.session.commit()

            # Grid value: ``balance_at.grid_balance_view`` is the seam entry the
            # grid's balance row reads, so replaying it here is "what does the
            # grid show" without a route round-trip.  It has been repointed
            # TWICE for the same reason -- a guard that had stopped guarding
            # (the N-63 shape): from ``_cash_engine.balances_for`` at plan step
            # X-c2b2, and from ``cash_balance_map`` at X-g3b, where the grid
            # stopped reading the cash view for a modelled account (ruling
            # R-W).  Corrected here rather than left as prose, because the whole
            # point of the test is that the two surfaces read ONE producer --
            # and this account is PLAIN, where they agree by construction.
            grid_current_balance = balance_at.grid_balance_view(
                CashFlowSet.single(seed_user["account"]),
                BalanceContext.build(seed_user["user"].id),
            ).columns[current_period.id].balance

            # F-009 / CRIT-01: 614.29 - max(500 - 45.71 - 0, 0)
            #                = 614.29 - 454.29 = 160.00.
            # Pre-Commit-6 /savings reported 114.29 (entries silently
            # unloaded; effective_amount returned 500.00 unchanged).
            assert grid_current_balance == Decimal("160.00")

            # Savings dashboard tile: routed through balances_for by
            # Commit 6.  Must equal the grid value exactly.
            result = savings_dashboard_service.compute_dashboard_data(
                BalanceContext.build(seed_user["user"].id),
            )
            checking_ad = next(
                ad for ad in result["account_data"]
                if ad.account.id == seed_user["account"].id
            )
            assert checking_ad.current_balance == Decimal("160.00")
            assert checking_ad.current_balance == grid_current_balance

    def test_savings_hysa_entry_aware(
        self, app, db, seed_user, seed_periods_today,
    ):
        """C6-2: HYSA accounts with cleared entries get the entry-aware reduction.

        HYSA still routes through ``calculate_balances_with_interest``
        in Commit 6 (the canonical producer does not yet carry an
        interest variant; MED-01 / Commit 28 collapses the dispatcher).
        However the Commit-5 seam softening at the math layer makes
        ``_entry_aware_amount`` lazy-load entries via the SQLAlchemy
        descriptor instead of silently degrading to ``effective_amount``,
        so the value is correct regardless.

        Setup mirrors symptom #1 on an HYSA:
          - HYSA anchor 614.29 on the current period.
          - One Projected envelope expense est=500.00 on the same period.
          - Three cleared debit entries summing to 45.71.

        Hand arithmetic (identical formula; interest for one period at
        the default 4.5%% APY rounds to a few cents and is verified
        loosely):

          base_balance = 614.29 - max(500 - 45.71 - 0, 0) = 160.00
          + small positive interest accrual (HYSA is not zero-rate).
        """
        from app.models.interest_params import InterestParams  # pylint: disable=import-outside-toplevel

        with app.app_context():
            hysa_type = (
                db.session.query(AccountType).filter_by(name="HYSA").one()
            )
            current_period = current_pay_period(
                seed_user["user"].id
            )
            assert current_period is not None

            hysa = account_service.create_account(
                account_service.AccountSpec(
                    user_id=seed_user["user"].id,
                    account_type_id=hysa_type.id,
                    name="HYSA Entry Test",
                    anchor_balance=Decimal("614.29"),
                ),
            )
            db.session.add(hysa)
            db.session.flush()
            # **Its books open before the entries below** (plan step X-f3c-2b,
            # ruling **R-HG**).  ``create_account`` opens them on the
            # assertion's own day, and ``_add_entry`` settles earlier than
            # that -- money already inside the $614.29 declared.  Moves no
            # figure: the assertion the arithmetic below is computed from does
            # not move.
            open_books_before_the_first_assertion(
                db.session, hysa, also_before=_PURCHASE_DAY,
            )
            # HIGH-06 / Commit 24: ``apy`` NOT NULL, no server_default.
            db.session.add(InterestParams(
                account_id=hysa.id, apy=Decimal("0.04500"),
                compounding_frequency_id=ref_cache.compounding_frequency_id(
                    CompoundingFrequencyEnum.DAILY,
                ),
            ))
            db.session.commit()

            txn = make_projected_envelope_expense(
                db.session,
                seed_user=seed_user,
                pay_period=current_period,
                estimated=Decimal("500.00"),
                account=hysa,
                name="HYSA Groceries",
            )
            for amt in (Decimal("20.00"), Decimal("15.71"), Decimal("10.00")):
                _add_entry(
                    db.session,
                    txn=txn,
                    user_id=seed_user["user"].id,
                    amount=amt,
                    is_settled=True,
                    is_credit=False,
                )
            db.session.commit()

            result = savings_dashboard_service.compute_dashboard_data(
                BalanceContext.build(seed_user["user"].id),
            )
            hysa_ad = next(
                ad for ad in result["account_data"]
                if ad.account.id == hysa.id
            )

            # base = 614.29 - max(500 - 45.71 - 0, 0)
            #     = 614.29 - 454.29 = 160.00 (entry-aware reduction)
            # plus a small positive interest accrual at 4.5%% APY for
            # the anchor period.  Pre-Commit-5 the entries were
            # silently unloaded and the base would have been
            # 614.29 - 500.00 = 114.29.  We require strictly greater
            # than 114.29 + interest noise (a 100x margin from the
            # 45.71 gap) to lock the entry-aware semantics:
            assert hysa_ad.current_balance > Decimal("159.00")
            assert hysa_ad.current_balance < Decimal("161.00")

    def test_savings_no_entries_unchanged(
        self, app, db, seed_user, seed_periods_today,
    ):
        """C6-3: with no entries, the current balance equals effective_amount.

        Assert-unchanged: the regression-safety guarantee that
        accounts with no envelope entries see byte-identical balances
        pre- and post-Commit-6.  Verified directly: with an anchor of
        614.29 and a single Projected $500 expense on the current
        period and NO entries, the entry-aware formula collapses to
        ``max(500.00 - 0 - 0, 0) = 500.00``, so the current balance
        is 614.29 - 500.00 = 114.29.  This is the SAME number /savings
        would have shown pre-Commit-6 (where entries were silently
        unloaded and ``effective_amount`` returned the same 500.00).

        Hand arithmetic:

          cleared_debit = 0; uncleared_debit = 0; sum_credit = 0
          checking_impact = max(500.00 - 0 - 0, 0) = 500.00
          anchor_period_balance = 614.29 + 0 - 500.00 = 114.29
        """
        with app.app_context():
            current_period = current_pay_period(
                seed_user["user"].id
            )
            assert current_period is not None
            _override_anchor(
                db.session,
                seed_user["account"],
                current_period,
                Decimal("614.29"),
            )

            make_projected_envelope_expense(
                db.session,
                seed_user=seed_user,
                pay_period=current_period,
                estimated=Decimal("500.00"),
            )
            db.session.commit()

            result = savings_dashboard_service.compute_dashboard_data(
                BalanceContext.build(seed_user["user"].id),
            )
            checking_ad = next(
                ad for ad in result["account_data"]
                if ad.account.id == seed_user["account"].id
            )
            # 614.29 - max(500 - 0 - 0, 0) = 614.29 - 500.00 = 114.29.
            # Identical to the pre-Commit-6 value for this no-entries
            # case; the formula reduces to ``effective_amount`` when
            # the entry buckets are all zero.
            assert checking_ad.current_balance == Decimal("114.29")


class TestOneResolutionPerLoanPerReadPass:
    """Every producer resolves each loan EXACTLY ONCE per read pass.

    The DRY property made into a deterministic gate rather than a hope.  Before
    the read-pass :class:`~app.services.balance_at.BalanceContext`, ONE
    ``compute_dashboard_data`` ran the loan resolver ELEVEN times for two loans:
    the balance maps, the trend window's honest-history gate, the liability band,
    the loan tile, the property-equity card, and an "ever paid off" probe each
    resolved independently.

    That was filed as waste, and the waste was the least of it: because there was
    no single resolution to compare against, nothing revealed that one of the
    eleven answered from a producer that could not read the genesis ledger
    (``followup_redundant_loan_resolution.md``).  Redundant derivation is where a
    divergence hides, so "resolved once" is pinned HERE, at the count -- a new
    consumer that re-resolves a loan behind the seam's back fails this test
    rather than silently agreeing until the day it does not.

    The spy sits on ``resolve_loan_bundle``: the single db-facing load the memo
    wraps, so it counts every resolution anywhere in the pass regardless of which
    module asked.
    """

    def _count_resolutions(self, monkeypatch):
        """Spy on the db-facing loan resolver; return the per-account call list."""
        # Pylint: import-outside-toplevel -- the file-wide deferred-import
        # convention for test-local symbols.
        from app.services.balance_at import (  # pylint: disable=import-outside-toplevel
            _resolution as resolution_module,
        )

        calls = []
        real = resolution_module.resolve_loan_bundle

        def _spy(account, ctx):
            calls.append(account.id)
            return real(account, ctx)

        monkeypatch.setattr(resolution_module, "resolve_loan_bundle", _spy)
        return calls

    def test_dashboard_data_resolves_each_loan_once(
        self, app, db, seed_user, seed_periods_today, monkeypatch,
    ):
        """/savings resolves each of two loans exactly once (was 11 runs for 2)."""
        with app.app_context():
            first = _create_small_loan(seed_user, db.session, name="Loan A")
            second = _create_small_loan(seed_user, db.session, name="Loan B")
            db.session.commit()
            ids = {first.id, second.id}

            calls = self._count_resolutions(monkeypatch)
            savings_dashboard_service.compute_dashboard_data(
                BalanceContext.build(seed_user["user"].id),
            )

            assert set(calls) == ids
            for loan_id in ids:
                assert calls.count(loan_id) == 1, (
                    f"loan {loan_id} was resolved {calls.count(loan_id)} times "
                    "in one /savings render; it must be resolved exactly once"
                )

    def test_tracks_section_resolves_each_loan_once(
        self, app, db, seed_user, seed_periods_today, monkeypatch,
    ):
        """The dashboard's tracks section shares ONE pass across its producers.

        ``compute_tracks_section`` runs the goal and debt-summary producers back
        to back -- three producers until plan step X-u deleted the
        principal-progress one (finding N-109).  Each used to start its own read
        pass, so a two-loan user paid for six resolutions; they now share one
        context.

        The CONTEXT is shared; the LOADS behind it are not, which is finding
        N-115 and is what this test does NOT assert -- it counts loan
        resolutions, and a duplicate ``_load_dashboard_core_data`` resolves no
        loan.  ``TestTracksDebt::test_one_render_projects_the_debt_accounts_once``
        is the one that counts pipelines.
        """
        with app.app_context():
            loan = _create_small_loan(seed_user, db.session, name="Tracks Loan")
            db.session.commit()
            loan_id = loan.id

            calls = self._count_resolutions(monkeypatch)
            # Pylint: import-outside-toplevel -- deferred, matching the producer's
            # own lazy import of the savings package.
            from app.services import (  # pylint: disable=import-outside-toplevel
                dashboard_service,
            )
            dashboard_service.compute_tracks_section(
                BalanceContext.build(seed_user["user"].id),
            )

            assert calls.count(loan_id) == 1


class TestTypeDriftedLoanParamsRow:
    """An orphan ``LoanParams`` on a non-amortizing type is not treated as a loan.

    The reachable drift state: the account edit form allows changing
    ``account_type_id`` (``accounts/crud.py``), nothing deletes the params row on
    a type change, so a Mortgage re-typed to Credit Card KEEPS its ``LoanParams``.
    ``followup_horizon_loan_predicate_split.md`` proposed that the Horizon's
    domain / milestones (which read ``ad.loan.params``) would then disagree
    with its liability band (which asks the canonical classifier), drawing a
    payoff flag on a debt line that never retires.

    They do NOT disagree, and this test is the evidence the follow-up said did not
    exist.  ``_data._load_loan_params_and_escrow`` builds ``loan_params_map`` from
    the accounts whose TYPE carries ``has_amortization`` -- the SAME flag
    ``classify_account`` branches on -- so a drifted account never enters the map,
    never gets ``loan_params`` in its projection dict, and is therefore skipped by
    the domain and the milestones exactly as the band skips it.  One flag, one
    answer, three consumers.
    """

    def test_drifted_account_is_a_plain_liability_everywhere(
        self, app, db, seed_user, seed_periods_today,
    ):
        """A re-typed loan carries no loan fields and raises no payoff milestone."""
        with app.app_context():
            acct = _create_small_loan(seed_user, db.session, name="Drifted")
            assert acct.loan_params is not None

            # Re-type it to a NON-amortizing liability, exactly as the edit route
            # allows; the LoanParams row deliberately survives.
            card_type = (
                db.session.query(AccountType)
                .filter_by(name="Credit Card").one()
            )
            assert card_type.has_amortization is False
            acct.account_type_id = card_type.id
            db.session.commit()
            acct_id = acct.id

            data = savings_dashboard_service.compute_dashboard_data(
                BalanceContext.build(seed_user["user"].id),
            )
            entry = next(
                ad for ad in data["account_data"] if ad.account.id == acct_id
            )

            # No loan fields: the tile shows no payment / rate / payoff, so the
            # Horizon's domain and its milestone flags cannot pick it up.
            # ONE field now answers both of the questions the dict spelled
            # as two absent keys (``loan_params`` / ``loan_figures``): the
            # projection either has a loan detail or it does not.
            assert entry.loan is None

            # And it raises no "paid off" milestone on the Horizon.
            horizon = data["net_worth"].horizon
            assert horizon is not None
            assert not [
                m for m in horizon["milestones"] if "Drifted" in m["label"]
            ]


class TestNoBaselineIsAnsweredOnceForEveryKind:
    """A user with no baseline scenario gets ONE answer, not five blank tiles.

    Plan step X-v2 (rulings R-BW / R-BZ / R-CA), REVERSING plan step X-t2's
    ruling that this region should degrade in place.  The developer confirmed
    the expected behaviour changed (CLAUDE.md rule 5); this docstring is where
    that reversal is findable from the tests.

    **What X-t2 shipped and why it was wrong.**  It gave the region one
    no-baseline door returning the "today figures over an empty series".  Those
    today figures were ``compute_net_worth_today`` reducing
    ``current_balance or ZERO`` over balances that were ALL ``None`` -- so a
    user whose every balance the app cannot answer was told their net worth,
    total assets and total liabilities were exactly ``$0.00`` (finding N-113).
    Seven reducers in this package made that same substitution, and four other
    surfaces answered the same state four other ways, two of them by
    fabricating a figure and three of them with a 500.

    **What replaces it.**  The seam raises
    :class:`~app.exceptions.BaselineMissingError` and ONE application-level
    handler answers: the setup-recovery card for a page, ``204`` for an HTMX
    fragment.  ``AccountProjection.current_balance`` stopped being nullable in
    the same commit, so the seven fabrications are gone with the state that
    produced them.

    Unreachable in production -- ``registration_service.register_user`` writes a
    baseline for every owner, nothing deletes or un-baselines one, and no path
    promotes a companion to owner -- which is precisely why it needs tests:
    nothing else would ever execute it.  The end-to-end arms live in
    ``tests/test_routes/test_no_baseline_policy/test_the_sweep.py``, which
    sweeps every route in ``url_map``.
    """

    def test_every_kind_raises_the_same_named_exception(
        self, app, db, seed_user, seed_periods_today,
    ):
        """No baseline: the projection raises, whatever kinds the user holds.

        The discriminating fixture is a loan BESIDE the seeded cash account.
        Before X-t2 the two arms disagreed -- the non-loan map builder returned
        ``{}`` while the loan arm four lines later reached ``require_scenario``
        and raised -- so ONE account kind 500'd a page the other four rendered.
        Both arms now reach the same named exception, which is what "answered
        once" means here; a fixture with only one kind could not tell.
        """
        with app.app_context():
            _create_small_loan(seed_user, db.session, name="No Baseline")
            scenario = db.session.get(Scenario, seed_user["scenario"].id)
            scenario.is_baseline = False
            db.session.commit()

            with pytest.raises(BaselineMissingError):
                savings_dashboard_service.compute_dashboard_data(
                    BalanceContext.build(seed_user["user"].id),
                )

    def test_the_hero_cannot_report_a_fabricated_zero(
        self, app, db, seed_user, seed_periods_today,
    ):
        """No baseline: no figure is produced at all, fabricated or otherwise.

        The regression this pins is finding N-113 as a POSITIVE claim rather
        than an absence: the page used to reach
        :func:`.._net_worth.compute_net_worth_today` with every
        ``current_balance`` ``None`` and report ``$0.00`` net worth, ``$0.00``
        assets and ``$0.00`` liabilities.  The assertion is that the producer
        never returns, so there is no dict to inspect -- if a future change
        re-introduces a degraded return, this fails whatever figures it carries.

        The account set is deliberately NON-trivial (a loan and a cash account
        with real balances), so a ``$0.00`` result would be visibly wrong
        rather than coincidentally right.
        """
        with app.app_context():
            _create_small_loan(seed_user, db.session, name="No Baseline")
            scenario = db.session.get(Scenario, seed_user["scenario"].id)
            scenario.is_baseline = False
            db.session.commit()

            with pytest.raises(BaselineMissingError) as excinfo:
                savings_dashboard_service.compute_dashboard_data(
                    BalanceContext.build(seed_user["user"].id),
                )
            # The message names the repair, which is what makes the handler's
            # card actionable rather than decorative.
            assert "create-baseline" in str(excinfo.value)

    def test_the_page_answers_with_the_repair(
        self, app, auth_client, db, seed_user, seed_periods_today,
    ):
        """/savings renders the setup-recovery card for a baseline-less owner.

        The end-to-end arm: a producer that raises is only correct if the
        application answers the raise, and this is the route where X-t2's
        version returned 200 with a page full of fabricated zeros.

        **The fixture carries a PROPERTY SECURING A MORTGAGE, and that is the
        whole discriminator** (kept from plan step X-t5).  X-t2 shipped its
        version of this test with a loan-only fixture and a docstring claiming
        the page renders -- while a third seam door,
        ``compute_property_equity`` -> ``home_equity_service.resolve_home_equity``
        -> ``balance_at.loan_figures``, raised for exactly this user.  Both of
        X-t's adversarial reviews found it by walking the call graph; the
        control that should have caught it could not fire, because its fixture
        had no Property.  The real link is ``collateral_account_id``:
        ``secured_by_account_id`` is NOT a field, and SQLAlchemy accepts that
        assignment in silence, which is how the first Property fixture was born
        dead.
        """
        with app.app_context():
            _create_small_loan(seed_user, db.session, name="No Baseline")
            mortgage = _add_mortgage_account(
                seed_user, Decimal("240000.00"),
            )
            prop = _add_property_account(
                seed_user, Decimal("400000.00"),
            )
            mortgage.collateral_account_id = prop.id
            scenario = db.session.get(Scenario, seed_user["scenario"].id)
            scenario.is_baseline = False
            db.session.commit()

            resp = auth_client.get("/savings")

            assert resp.status_code == 200
            body = resp.data.decode()
            assert "Setup Incomplete" in body
            assert "/create-baseline" in body
            # And none of the page it replaced: no fabricated hero, no chart.
            assert "Net worth" not in body
            assert 'id="net-worth-chart-canvas"' not in body
