"""Balance-at seam tests: the cash balance map and its edges, the scalar balance, degrade and
scenario guards, and building the maps.

Split from ``tests/test_services/test_balance_at.py`` by ``tools/split`` (ruling R-BAL301).
"""

from datetime import date
from decimal import Decimal

import pytest

from app import ref_cache
from app.enums import TxnTypeEnum
from app.models.account import Account
from app.models.pay_period import PayPeriod
from app.models.ref import AccountType
from app.services import growth_engine, account_service, balance_at, cash_ledger, liability_sign
from app.services.balance_at import _kernel as net_worth_kernel
from app.services.pay_calendar import calendar_for
from app.services.balance_at._asset_contributions import ContributionInputs
from app.services.investment_projection import AccountPayrollFeed
from app.services.projection_inputs import load_payroll_feeds
from app.services.savings_dashboard_service._data import _load_account_params
from app.services.scenario_resolver import get_baseline_scenario
from app.utils.money import round_money
from app.services.balance_at import BalanceContext
from app.services.balance_at._resolution import resolved_loan
from tests._test_helpers import (
    add_txn,
    all_periods,
    last_covered_day,
    make_appreciating_account,
    make_investment_account,
    make_loan_payment_template,
    one_off_row_of,
    pricing_over,
)

from ._helpers import (
    _no_baseline,
    _make_hysa,
    _owed_today,
    _make_mortgage,
    _configured_annual_rate,
)


class TestBalanceMapCash:
    """``balance_map`` reproduces the kernel cash path (PLAIN / INTEREST)."""

    def test_plain_checking_equals_kernel(
        self, app, db, seed_user, seed_periods_today,
    ):
        """A PLAIN checking map equals the kernel called with the same inputs.

        The seam assembles no debt schedule, no investment params and no
        payroll feed for a checking account;
        the result must equal calling
        :func:`net_worth_kernel.build_account_balance_map` directly with
        exactly those inputs.  This proves the seam's internal assembly
        reproduces the existing inputs.
        """
        with app.app_context():
            user_id = seed_user["user"].id
            scenario = get_baseline_scenario(user_id)
            bctx = BalanceContext.build(user_id)
            periods = all_periods(user_id)
            account = seed_user["account"]
            seam = balance_at.balance_map(account, bctx)
            expected = net_worth_kernel.build_account_balance_map(
                account, bctx,
                # The EMPTY bundle since plan step salary:R14-b: an account
                # with no investment params gets no payroll feed at all.  It
                # carried a ``salary_gross_biweekly`` of the owner's current
                # paycheck until then -- one set-wide figure handed to an
                # account that could not consume one.
                ContributionInputs(),
            )

            assert seam is not None
            assert seam == expected
            # No transactions -> the flat $1,000 anchor at every period.
            assert seam[periods[0].id] == Decimal("1000.00")

    def test_interest_hysa_equals_kernel(
        self, app, db, seed_user, seed_periods_today,
    ):
        """An INTEREST (HYSA) map equals the kernel's interest path.

        The HYSA routes through the kernel's interest path -- the cash FOLD
        with :mod:`app.services.balance_at._interest`'s accrual layered on
        (plan step X-c2b2, which moved that layering out of the retired
        ``calculate_balances_with_interest`` and beside the accrual window it
        needs).  The seam must reproduce it, and the accrual means the closing
        balance sits above the flat anchor (proving the interest branch -- not
        the plain fall-through -- ran).
        """
        with app.app_context():
            user_id = seed_user["user"].id
            scenario = get_baseline_scenario(user_id)
            bctx = BalanceContext.build(user_id)
            periods = all_periods(user_id)
            hysa = _make_hysa(db, seed_user, periods[0], Decimal("5000.00"))
            seam = balance_at.balance_map(hysa, bctx)
            expected = net_worth_kernel.build_account_balance_map(
                hysa, bctx,
                ContributionInputs(),
            )

            assert seam is not None
            assert seam == expected
            # Interest accrues forward, so the last period exceeds the anchor.
            assert seam[periods[-1].id] > Decimal("5000.00")


class TestBuildMaps:
    """``build_maps`` reproduces the savings net-worth producer batch build."""

    def test_mixed_set_matches_net_worth_maps(
        self, app, db, seed_user, seed_periods_today,
    ):
        """For a mixed account set, build_maps dispatches every account correctly.

        Two oracles, because the kernel dispatch no longer answers loans (C3b3
        moved the AMORTIZING branch into the seam):

        * **Non-loan accounts** (cash / interest / investment / appreciation): the
          batch seam per-id map must equal the direct kernel
          ``build_account_balance_map`` under the orchestrator's manual assembly,
          which locks the batch's input assembly and its deduction-scoping rule
          (both scope to the InvestmentParams map's keys).  The oracle is the
          direct kernel call, NOT the savings cockpit's own read of this entry
          (which delegates to ``build_maps`` -- comparing would be tautological).
        * **Loan accounts**: the batch seam map must equal the SINGLE-account seam
          map, proving the batch loop routed the loan to its fold (the shared
          ``configured_loan`` gate admitted it rather than dropping it to the
          cash path).  The loan's
          VALUE correctness is ``TestBalanceMapLoan``, ``TestMultiLoanIsolation``,
          and the B2 fold oracle.
        """
        with app.app_context():
            user_id = seed_user["user"].id
            scenario = get_baseline_scenario(user_id)
            bctx = BalanceContext.build(user_id)
            periods = all_periods(user_id)

            _make_hysa(db, seed_user, periods[0], Decimal("5000.00"))
            _make_mortgage(
                db, seed_user, periods[0], Decimal("240000.00"),
                date(2024, 1, 1),
            )
            make_investment_account(seed_user, db.session, periods[2], Decimal("10000.00"))
            make_appreciating_account(
                seed_user, db.session, periods[2], Decimal("400000.00"),
                Decimal("0.03000"),
            )

            accounts = (
                db.session.query(Account)
                .filter_by(user_id=user_id, is_active=True)
                .order_by(Account.sort_order, Account.name)
                .all()
            )

            # Assemble the inputs the way the balance_at seam does, so the
            # per-account kernel dispatch below is fed the same inputs
            # build_maps feeds the kernel internally.
            params = _load_account_params(accounts)
            loan_ids = {
                a.id for a in accounts if a.id in params.loan_params_map
            }
            # The payroll FEED is no longer on _AccountParams (the seam
            # assembles it); source it from the same shared loader the seam
            # uses, with its investment-only scoping.  It was a deduction map
            # plus one engine gross until plan step salary:R14-b.
            inv_ids = list(params.investment_params_map.keys())
            feeds = load_payroll_feeds(
                pricing_over(calendar_for(user_id)), inv_ids,
                params.investment_params_map,
            )
            # Independent oracle for NON-loan accounts: the kernel dispatch the
            # savings net-worth producer ran inline pre-reroute, fed by the
            # orchestrator's manual assembly.  Loans are excluded -- the kernel
            # has no loan branch since C3b3, so it would degrade them to the cash
            # path; they are checked against the single-account seam below.
            expected_by_id = {}
            for account in accounts:
                if account.id in loan_ids:
                    continue
                balances = net_worth_kernel.build_account_balance_map(
                    account, bctx,
                    ContributionInputs(
                        investment_params=params.investment_params_map.get(
                            account.id,
                        ),
                        feed=feeds.get(
                            account.id, AccountPayrollFeed.absent(),
                        ),
                    ),
                )
                if balances is not None:
                    expected_by_id[account.id] = balances

            seam_maps = balance_at.build_maps(accounts, bctx)

            # All five seeded accounts have anchors, so none is omitted.
            assert len(seam_maps) == 5
            # Every NON-loan account: the batch seam == the manual kernel dispatch.
            for acct_id, expected_balances in expected_by_id.items():
                assert seam_maps[acct_id] == expected_balances
            # Every loan: the batch seam == the single-account seam (the batch
            # routed it to the fold, not the cash path).
            for loan_id in loan_ids:
                loan = next(a for a in accounts if a.id == loan_id)
                assert seam_maps[loan_id] == balance_at.balance_map(
                    loan, bctx,
                )

    def test_build_maps_is_total_over_the_accounts_it_is_handed(
        self, app, db, seed_user, seed_periods_today,
    ):
        """EVERY account handed to build_maps gets a map -- none is dropped.

        **This replaced an OMISSION test at plan step X-f1c3a.**  That test
        handed in a ``SimpleNamespace`` stand-in with
        ``current_anchor_period_id=None`` and asserted it was dropped -- a
        state the schema forbade (the column was ``NOT NULL`` with a CHECK
        beside it) and which ruling R-EH deleted the column for, so it graded a
        branch no real account could reach while teaching every consumer to
        carry a ``balances is None`` arm for it (finding N-73).

        Totality is what the consumers actually depend on: four producers
        INDEX this map rather than ``.get``-defaulting it, and a silently
        dropped account would surface as a ``KeyError`` in one of them rather
        than as a wrong figure -- which is the disposition ruling R-CA chose.
        Asserting it here is what makes that safe.
        """
        with app.app_context():
            user_id = seed_user["user"].id
            bctx = BalanceContext.build(user_id)
            periods = all_periods(user_id)
            accounts = account_service.list_active_accounts(user_id)
            assert accounts, "fixture must supply at least one account"

            seam_maps = balance_at.build_maps(accounts, bctx)

            assert set(seam_maps) == {acct.id for acct in accounts}
            for acct in accounts:
                assert set(seam_maps[acct.id]) == {p.id for p in periods}


class TestBalanceAt:
    """``balance_at`` dispatches to the correct date-granular producer."""

    def test_cash_equals_the_cash_flow_scalar(
        self, app, db, seed_user, seed_periods_today,
    ):
        """For a PLAIN account the kind-correct scalar IS the cash-flow scalar.

        Finding N-47's coupling, asserted: ``balance_at``'s PLAIN branch and
        ``cash_balance_at`` reach the same ``_cash_fold`` call, so /savings (the
        kind-correct scalar) and the dashboard (the cash-flow one) cannot answer
        one date two ways.  This was pinned against ``balance_as_of_date`` until
        plan step X-c2b3 deleted it; the property is unchanged and the reference
        is now the SEAM entry a screen actually reads, which is what makes the
        test fire if either branch is re-routed rather than only if the retired
        producer was.

        **The $250.00 row is what gives the equality teeth.**  Without a row the
        account holds only its $1,000.00 assertion, so ANY producer that returns
        the anchor satisfies both sides and the test passes for the wrong
        reason -- measured: replacing the PLAIN branch with a bare
        ``resolve_anchor`` read left it green.  With a row the two figures are
        the anchor MINUS a reservation the anchor read cannot know about, so the
        equality can only hold if both branches folded.
        """
        with app.app_context():
            user_id = seed_user["user"].id
            bctx = BalanceContext.build(user_id)
            periods = all_periods(user_id)
            account = seed_user["account"]
            as_of = periods[5].start_date  # inside a known period
            add_txn(
                db.session, seed_user, periods[4], "Rent", "250.00",
            )
            db.session.commit()

            seam = balance_at.balance_at(account, bctx, as_of)
            expected = balance_at.cash_balance_at(account, bctx, as_of)
            assert seam == expected
            # Non-vacuity: $1,000.00 anchor less the $250.00 still-projected
            # row, so neither side is the bare anchor.
            assert seam == Decimal("750.00")

    def test_interest_accrues_equals_period_map_not_cash(
        self, app, db, seed_user, seed_periods_today,
    ):
        """For an HYSA, balance_at accrues interest, NOT cash -- at the DATE.

        The kind-correct scalar must accrue where the cash-flow scalar does
        not.  Anchor a 5% APY HYSA at ``periods[2]`` with no transactions, then
        value it inside ``periods[6]``: strictly above the flat $5,000.00 cash
        carry ``cash_balance_at`` returns for the same date.  Asserting that
        divergence is what locks the scalar onto the accruing path: were
        INTEREST routed back to the cash producer, ``balance_at`` would equal
        the cash value and this test fails.

        **It meets the MAP at the map's own grain** (plan step X-g2b, finding
        N-71): the map is the fold sampled at each period's ``end_date``, and
        the scalar answers whatever date it is asked.  Reading it on the
        period's FIRST day used to return the period's END balance -- a whole
        period of accrual credited on day one.

        The cash reference was ``balance_as_of_date`` until plan step X-c2b3
        deleted it, and the $5,000.00 is unchanged by the swap for a reason
        worth stating: the HYSA holds ONE asserted balance and no transaction
        rows at all, so the fold has exactly the assertion to replay and the
        retired anchor-carry had exactly the same anchor to carry.  The two
        bases only diverge where money has settled or a second assertion
        exists, and this fixture has neither.
        """
        with app.app_context():
            user_id = seed_user["user"].id
            bctx = BalanceContext.build(user_id)
            periods = all_periods(user_id)
            hysa = _make_hysa(db, seed_user, periods[2], Decimal("5000.00"))
            as_of = periods[6].start_date  # independently known: in period 6

            seam = balance_at.balance_at(hysa, bctx, as_of)
            full_map = balance_at.balance_map(hysa, bctx)
            # Kind-correct scalar == kind-correct map at the period's END,
            # and STRICTLY BELOW it on the period's first day.
            assert balance_at.balance_at(
                hysa, bctx, last_covered_day(periods[6]),
            ) == full_map[periods[6].id]
            assert seam < full_map[periods[6].id]
            # And it ACCRUES: strictly above the flat no-interest cash carry
            # (anchor $5,000.00 with no rows) the cash-flow scalar returns for
            # the same date -- the Fork-B lock that the scalar is not on the
            # cash path for INTEREST.
            cash = balance_at.cash_balance_at(hysa, bctx, as_of)
            assert cash == Decimal("5000.00")
            assert seam > cash

    def test_loan_scalar_folds_the_forward_plan_not_the_contractual_schedule(
        self, app, db, seed_user, seed_periods_today,
    ):
        """The loan scalar's FUTURE value folds the PLAN, not the contractual schedule.

        A mortgage originated in the past with NO payment records carries a
        schedule of overdue unconfirmed installments, each crediting the
        contract's $1,516.96 P&I.  The retired forward walk
        (``balance_from_schedule_at_date`` over the resolver's schedule) credited
        every one of them (finding B-9).  The seam folds the loan's forward
        PLAN: what its recurring payment, a manual $1,000.00 below the
        $1,300.00 a month of interest, will pay, and nothing for the months
        before the owner's schedule opens, which are CHARGED all the same
        (ruling R-R71).  Pinned by the divergence -- the seam owes STRICTLY
        MORE than the contractual walk, and more than today -- so a regression
        back to the schedule walk would fire here.  Until plan step
        recurrence:R16-f the loan had no recurring payment and its plan billed
        no missed installment; such a loan is planned as its contract line
        now (rulings plan#3982, plan#3983), which credits each installment as
        the schedule walk does, so it cannot tell the two apart.

        The scalar reports what the loan HOLDS, negative when owed (ruling
        R-CC47, plan step credit_card:CC-5-5c), so what the seam owes is read
        through ``liability_sign.owed`` before it meets the owed schedule rows.
        """
        with app.app_context():
            user_id = seed_user["user"].id
            bctx = BalanceContext.build(user_id)
            periods = all_periods(user_id)
            mortgage, _params = _make_mortgage(
                db, seed_user, periods[0], Decimal("240000.00"),
                date(2024, 1, 1),
            )
            make_loan_payment_template(
                db.session, seed_user, mortgage, amount="1000.00",
                derive_from_loan=False,
            )
            db.session.commit()
            state = resolved_loan(mortgage, bctx).state
            as_of = last_covered_day(periods[7])  # future under seed_periods_today

            seam = balance_at.balance_at(mortgage, bctx, as_of)

            # Independent oracle: the retired forward walk credited EVERY
            # unconfirmed installment due by as_of, overdue ones included.
            forward_rows = sorted(
                (r for r in state.schedule if not r.is_confirmed),
                key=lambda r: r.payment_date,
            )
            due_by = [r for r in forward_rows if r.payment_date <= as_of]
            contractual_walk = (
                due_by[-1].remaining_balance if due_by
                else _owed_today(mortgage, bctx)
            )
            overdue = [r for r in forward_rows if r.payment_date <= bctx.as_of]
            assert overdue, "fixture must carry overdue unconfirmed installments"

            # The seam pays what the owner pays, below the interest, so it owes
            # MORE than the contractual walk -- and more than its seed.
            assert liability_sign.owed(seam) > contractual_walk
            assert liability_sign.owed(seam) > _owed_today(mortgage, bctx)

    def test_investment_is_date_precise_and_meets_the_map_at_period_ends(
        self, app, db, seed_user, seed_periods_today,
    ):
        """An investment answers the DATE, and the map is that fold at period ends.

        **The old contract was the defect** (finding N-71, closed at plan step
        X-g2b).  ``balance_at`` used to resolve the date to its pay period and
        return the map's value there, so it answered the IDENTICAL figure on a
        period's first day and its last -- measured at period 30 on the
        prod-shape clone, $328.50 of growth in that period landed entirely on
        day one.  The replay has a step for every day.

        So the two producers still meet, but at the map's OWN grain: the map is
        the fold sampled at each period's ``end_date``.  Asserting both halves
        is what makes this stronger than the identity it replaces -- the old one
        held while the scalar was period-flat, which is precisely the state
        being deleted.
        """
        with app.app_context():
            user_id = seed_user["user"].id
            bctx = BalanceContext.build(user_id)
            periods = all_periods(user_id)
            inv = make_investment_account(seed_user, db.session, periods[2], Decimal("10000.00"))

            full_map = balance_at.balance_map(inv, bctx)
            # The map IS the scalar at each period's end date.
            assert balance_at.balance_at(
                inv, bctx, last_covered_day(periods[6]),
            ) == full_map[periods[6].id]
            # And the scalar is DATE-precise: strictly less on the period's
            # first day than on its last, where it used to be equal.
            first_day = balance_at.balance_at(inv, bctx, periods[6].start_date)
            assert first_day < full_map[periods[6].id]
            # Neighbors differ -> an off-by-one in period selection would show.
            assert full_map[periods[5].id] != full_map[periods[7].id]
            # Read a post-anchor (grown) period, not the period-0 / anchor value.
            assert first_day > Decimal("10000.00")

    def test_property_is_date_precise_and_meets_the_map_at_period_ends(
        self, app, db, seed_user, seed_periods_today,
    ):
        """A Property answers the DATE too -- the same closure of finding N-71.

        The appreciating kind carried the identical period-flat wart, and it is
        the one whose figure is largest: a $400,000.00 market value at 3% moves
        about $32 a period, all of which used to land on the period's first day.
        """
        with app.app_context():
            user_id = seed_user["user"].id
            bctx = BalanceContext.build(user_id)
            periods = all_periods(user_id)
            prop = make_appreciating_account(
                seed_user, db.session, periods[2], Decimal("400000.00"),
                Decimal("0.03000"),
            )

            full_map = balance_at.balance_map(prop, bctx)
            assert balance_at.balance_at(
                prop, bctx, last_covered_day(periods[6]),
            ) == full_map[periods[6].id]
            first_day = balance_at.balance_at(prop, bctx, periods[6].start_date)
            assert first_day < full_map[periods[6].id]
            # Neighbors differ -> an off-by-one in period selection would show.
            assert full_map[periods[5].id] != full_map[periods[7].id]
            # Post-anchor appreciation above the anchor market value.
            assert first_day > Decimal("400000.00")


class TestScenarioGuard:
    """All three entry points fail loud on a None scenario (C1)."""

    def test_none_scenario_raises_value_error(
        self, app, db, seed_user, seed_periods_today,
    ):
        """balance_map / build_maps / balance_at each raise ValueError on None.

        ``get_baseline_scenario`` can return None (fresh user); the seam is
        the defensive contract that turns that into a clear failure rather
        than a deep AttributeError on ``scenario.id`` or a silent $0.
        """
        with app.app_context():
            user_id = seed_user["user"].id
            periods = all_periods(user_id)
            account = seed_user["account"]
            as_of = periods[5].start_date

            with pytest.raises(ValueError):
                balance_at.balance_map(account, _no_baseline(user_id))
            with pytest.raises(ValueError):
                balance_at.build_maps([account], _no_baseline(user_id))
            with pytest.raises(ValueError):
                balance_at.balance_at(account, _no_baseline(user_id), as_of)


class TestBalanceAtDegrade:
    """balance_at's documented fallbacks (no-schedule loan, before-horizon)."""

    def test_loan_without_schedule_degrades_to_cash_producer(
        self, app, db, seed_user, seed_periods_today,
    ):
        """An amortizing account with no LoanParams degrades to the cash fold.

        ``resolved_loan`` returns None (no LoanParams / anchor events), so
        ``balance_at`` falls back to the cash fold over the loan's own rows --
        the documented degrade, and the second of the two branches finding N-47
        moved onto the fold at plan step X-c2b2.  Referenced against the SEAM
        entry since plan step X-c2b3 deleted ``balance_as_of_date``.

        **The $300.00 row is what gives the equality teeth**, for the reason
        ``test_cash_equals_the_cash_flow_scalar`` records: an account holding
        only its opening assertion is answered identically by any producer that
        reads the anchor, so the degrade could be re-routed anywhere and this
        would stay green.
        """
        with app.app_context():
            user_id = seed_user["user"].id
            bctx = BalanceContext.build(user_id)
            periods = all_periods(user_id)
            mortgage_type = (
                db.session.query(AccountType).filter_by(name="Mortgage").one()
            )
            acct = account_service.create_account(account_service.AccountSpec(
                user_id=user_id, account_type_id=mortgage_type.id,
                name="Unconfigured Loan", anchor_balance=Decimal("5000.00"),
            ))
            db.session.add(acct)
            add_txn(
                db.session, seed_user, periods[4], "Payment", "300.00",
                account=acct,
            )
            db.session.commit()
            as_of = periods[5].start_date

            seam = balance_at.balance_at(acct, bctx, as_of)
            expected = balance_at.cash_balance_at(acct, bctx, as_of)
            assert seam == expected
            # Non-vacuity: $5,000.00 opening less the $300.00 still-projected
            # row, so neither side is the bare anchor.
            assert seam == Decimal("4700.00")

    def test_before_horizon_returns_anchor_balance(
        self, app, db, seed_user, seed_periods_today,
    ):
        """An as_of before the whole period horizon returns the canonical anchor.

        For an investment whose date precedes every period, no containing
        period exists, so balance_at returns the resolved anchor balance
        (rounded).  Note this is the KIND-CORRECT scalar's own fall-through,
        not the cash view's: the fold answers a pre-assertion date by
        back-projecting the first assertion over the records it already
        contains (ruling R-I), which is a different and deliberately separate
        rule.
        """
        with app.app_context():
            user_id = seed_user["user"].id
            scenario = get_baseline_scenario(user_id)
            bctx = BalanceContext.build(user_id)
            periods = all_periods(user_id)
            inv = make_investment_account(seed_user, db.session, periods[2], Decimal("10000.00"))

            seam = balance_at.balance_at(inv, bctx, date(2000, 1, 1))
            expected = round_money(
                cash_ledger.resolve_anchor(inv).balance,
            )
            assert seam == expected
            assert seam == Decimal("10000.00")  # the 401k's anchor balance


class TestOnlyALoanIsNotATransactionSum:
    """A typed row moves a MODELLED asset and never a loan (ruling R-W).

    **This class inverted at plan step X-g2b, for two of its three kinds.**  It
    used to assert that a row typed on an investment or a property moved
    nothing, and that was true of the shipped producer: those maps were a
    growth curve spliced over a cash base, so an ad-hoc row landed in the base
    and the splice preferred the curve.  ``_grid.py`` recorded the same
    objection as the reason the grid left those kinds on the cash-flow view.

    Under ONE replay a typed row IS an event in the same stream -- a modelled
    asset is its cash fold plus a rate -- so it moves the balance exactly as it
    moves an HYSA's, and ruling R-W is what makes that the intended answer
    rather than a regression.  A LOAN is the one kind where the old assertion
    survives, and for a reason that is about the loan rather than about the
    producer: its balance is an amortization schedule, not a transaction sum,
    which is why ruling D4 refuses such a row at every write door.
    """

    def test_a_typed_income_row_moves_the_modelled_kinds_but_never_a_loan(
        self, app, db, seed_user, seed_periods_today,
    ):
        """The map before and after adding an income row to each kind.

        (A loan REFUSES such a row at the write door since plan step BG /
        ruling R-E; this inserts one directly to prove the READ side ignores it
        too, which is what makes the write guard a belt rather than the only
        thing standing between a schedule and a transaction sum.)

        The modelled halves assert the row is counted ONCE and compounded --
        strictly more than the row's own $9,999.00 by the period it lands in
        and after, and unchanged before it -- so a double count or a dropped
        row both fail here, where "moved at all" would pass either.
        """
        with app.app_context():
            user_id = seed_user["user"].id
            scenario = get_baseline_scenario(user_id)
            bctx = BalanceContext.build(user_id)
            periods = all_periods(user_id)
            mortgage, _p = _make_mortgage(
                db, seed_user, periods[0], Decimal("240000.00"),
                date(2024, 1, 1),
            )
            inv = make_investment_account(
                seed_user, db.session, periods[2], Decimal("10000.00"),
            )
            prop = make_appreciating_account(
                seed_user, db.session, periods[2], Decimal("400000.00"),
                Decimal("0.03000"),
            )
            before = {
                acct.id: balance_at.balance_map(acct, bctx)
                for acct in (mortgage, inv, prop)
            }
            # The rows below are a WRITE, so the reads after them take a fresh
            # pass: a ``BalanceContext`` is a memo whose lifetime is the one
            # read it was built for, and since plan step X-i4 the cash fold the
            # modelled kinds ride on is one of the derivations it holds.
            for acct in (mortgage, inv, prop):
                one_off_row_of(
                    periods[5],
                    name="typed row",
                    amount=Decimal("9999.00"),
                    user_id=periods[5].user_id,
                    account_id=acct.id,
                    scenario_id=scenario.id,
                    transaction_type_id=ref_cache.txn_type_id(
                        TxnTypeEnum.INCOME,
                    ),
                )
            db.session.commit()

            after_ctx = BalanceContext.build(user_id)

            # The LOAN is unmoved: its balance is its schedule (ruling D4).
            assert balance_at.balance_map(
                mortgage, after_ctx,
            ) == before[mortgage.id], "the loan moved on a typed row"

            # The MODELLED kinds count it, once, from the period it lands in.
            for acct in (inv, prop):
                after = balance_at.balance_map(acct, after_ctx)
                # Untouched before the row's own period ...
                assert after[periods[4].id] == before[acct.id][periods[4].id], (
                    f"{acct.name} moved BEFORE the typed row's period"
                )
                # ... counted ONCE in it, and compounded after.  The bound is
                # derived, not guessed: the row is worth $9,999.00 and can earn
                # at most one period of the account's own configured return on
                # top of it (it lands inside the period, so at most that), which
                # is strictly less than counting it twice.  A dropped row fails
                # the lower bound and a double count fails the upper.
                one_period = Decimal("9999.00") * (
                    1 + growth_engine.span_return_rate(
                        _configured_annual_rate(acct),
                        periods[5].start_date, last_covered_day(periods[5]),
                    )
                )
                landed = after[periods[5].id] - before[acct.id][periods[5].id]
                assert Decimal("9999.00") <= landed <= one_period, (
                    f"{acct.name} counted the typed row as {landed}"
                )
                grown = after[periods[-1].id] - before[acct.id][periods[-1].id]
                assert grown > landed, f"{acct.name} did not compound the row"


class TestCashPreAnchorPeriodsAreAnswered:
    """The headline cash contract, REVERSED at plan step X-c2b2.

    Cash balances used to be materialized roll-forwards from the anchor, so a
    period before it had no balance at all -- absent, not zero.  That is
    finding cash D3 / B-18: on production the period map omitted eight past
    columns while the scalar fabricated today's balance for the same dates.
    The fold replays every assertion, so a pre-anchor period reads the balance
    in force then and EVERY requested period is present.
    """

    def test_an_interest_account_answers_its_pre_anchor_periods(
        self, app, db, seed_user, seed_periods_today,
    ):
        """An HYSA anchored mid-window carries a balance in every period.

        The account is anchored at period 2 with $5,000.00 and has no rows, so
        ruling R-I's back-projection holds that assertion flat backwards:
        periods 0 and 1 read $5,000.00 -- the balance it demonstrably held --
        rather than being absent.  The seam == the kernel.
        """
        with app.app_context():
            user_id = seed_user["user"].id
            scenario = get_baseline_scenario(user_id)
            bctx = BalanceContext.build(user_id)
            periods = all_periods(user_id)
            hysa = _make_hysa(db, seed_user, periods[2], Decimal("5000.00"))
            seam = balance_at.balance_map(hysa, bctx)
            expected = net_worth_kernel.build_account_balance_map(
                hysa, bctx,
                ContributionInputs(),
            )
            assert seam is not None
            assert seam[periods[0].id] == Decimal("5000.00")
            assert seam[periods[1].id] == Decimal("5000.00")
            assert periods[2].id in seam  # the anchor period is present
            assert len(seam) == len(periods)
            assert seam == expected


class TestBalanceMapEdgeCases:
    """Empty-set and direct no-anchor contracts."""

    def test_build_maps_empty_accounts_is_empty(
        self, app, db, seed_user, seed_periods_today,
    ):
        """build_maps over no accounts returns an empty dict (no query needed)."""
        with app.app_context():
            bctx = BalanceContext.build(seed_user["user"].id)
            assert balance_at.build_maps([], bctx) == {}

    def test_an_owner_with_no_periods_gets_an_empty_map_not_None(
        self, app, db, seed_user, seed_periods_today,
    ):
        """An anchored account whose owner has NO pay periods maps to nothing.

        **This graded a caller passing ``periods=[]`` until plan step C2-c**,
        and that state stopped being expressible with the argument: the domain
        is the owner's own calendar, read off the read pass, so "no periods
        requested" and "the owner has no periods" collapsed into the second.
        The contract the test exists for is unchanged and is asserted here
        against the state that survives -- an EMPTY map, never ``None``, which
        finding N-73 records consumers having had to branch on.
        """
        with app.app_context():
            user_id = seed_user["user"].id
            db.session.query(PayPeriod).filter_by(user_id=user_id).delete()
            db.session.commit()
            bctx = BalanceContext.build(user_id)

            result = balance_at.balance_map(seed_user["account"], bctx)

            assert result is not None
            assert len(result) == 0
