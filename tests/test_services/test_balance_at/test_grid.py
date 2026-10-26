"""Balance-at seam tests: the grid balance view, its row flags, and the remainder oracle.

Split from ``tests/test_services/test_balance_at.py`` by ``tools/split`` (ruling R-BAL301).
"""

from collections import OrderedDict
from datetime import date, timedelta
from decimal import Decimal

import pytest

from app import ref_cache
from app.enums import TxnTypeEnum
from app.models.interest_params import InterestParams
from app.services import balance_at, cash_ledger
from app.services.balance_at import _kernel as net_worth_kernel
from app.services.pay_calendar import DerivedPeriod
from app.services.scenario_resolver import get_baseline_scenario
from app.utils.money import round_money
from app.services.balance_at import BalanceContext
from app.services.cash_flow_set import CashFlowSet
from tests._test_helpers import (
    all_periods,
    make_appreciating_account,
    make_investment_account,
    one_off_row_of,
)

from ._helpers import (
    _no_baseline,
    _derived_income_row,
    _make_hysa,
    _make_mortgage,
    _all_columns,
    _assert_grid_view_reconciles,
    _column,
    _seed_grid_activity,
)


class TestGridBalanceView:
    """``grid_balance_view`` -- the kind-aware grid + obligations view."""

    def test_plain_is_cash_flow_with_no_accrual(
        self, app, db, seed_user, seed_periods_today,
    ):
        """A PLAIN account returns the cash-flow view verbatim, no accrual."""
        with app.app_context():
            user_id = seed_user["user"].id
            scenario = get_baseline_scenario(user_id)
            bctx = BalanceContext.build(user_id)
            account = seed_user["account"]

            view = balance_at.grid_balance_view(CashFlowSet.single(account), bctx)
            cash = balance_at.cash_balance_map(account, bctx)

            assert {
                pid: column.balance for pid, column in _all_columns(view)
            } == dict(cash)
            # No accrual row for a plain cash account.
            assert all(
                column.accrual == Decimal("0.00")
                and column.contribution == Decimal("0.00")
                for column in view.columns.values()
            )

    def test_loan_stays_cash_flow_no_accrual(
        self, app, db, seed_user, seed_periods_today,
    ):
        """A loan grid account stays on the cash-flow view (no accrual row).

        Loans cannot reconcile on a transaction grid (payments land as
        income while the balance is schedule-driven), so ``grid_balance_view``
        leaves them on the cash-flow view exactly like a PLAIN account -- no
        kind-correct walk, no accrual row.
        """
        with app.app_context():
            user_id = seed_user["user"].id
            scenario = get_baseline_scenario(user_id)
            bctx = BalanceContext.build(user_id)
            periods = all_periods(user_id)
            mortgage, _params = _make_mortgage(
                db, seed_user, periods[0], Decimal("240000.00"),
                date(2024, 1, 1),
            )

            view = balance_at.grid_balance_view(CashFlowSet.single(mortgage), bctx)
            cash = balance_at.cash_balance_map(mortgage, bctx)

            assert {
                pid: column.balance for pid, column in _all_columns(view)
            } == dict(cash)
            assert all(
                column.accrual == Decimal("0.00")
                and column.contribution == Decimal("0.00")
                for column in view.columns.values()
            )

    def test_interest_balances_kind_correct_and_reconcile(
        self, app, db, seed_user, seed_periods_today,
    ):
        """An HYSA shows the interest-accrued balance + a reconciling accrual.

        Seeds a 5% HYSA anchored at ``periods[0]`` with a $1,000 deposit at
        ``periods[3]`` so the cash flow is non-trivial (net != 0) AND interest
        accrues on top.  The displayed balances are the rounded kind-correct
        map; the per-period accrual reconciles the rows to the cent; and the
        telescoped total accrual equals the kernel's interest within a cent
        (proving it is real interest, not a residual absorbing a bug).
        """
        with app.app_context():
            user_id = seed_user["user"].id
            scenario = get_baseline_scenario(user_id)
            bctx = BalanceContext.build(user_id)
            periods = all_periods(user_id)
            hysa = _make_hysa(db, seed_user, periods[0], Decimal("8000.00"))
            one_off_row_of(
                periods[6],
                name="Deposit",
                amount=Decimal("1000.00"),
                user_id=periods[6].user_id,
                account_id=hysa.id,
                scenario_id=scenario.id,
                transaction_type_id=ref_cache.txn_type_id(TxnTypeEnum.INCOME),
            )
            db.session.commit()

            view = balance_at.grid_balance_view(CashFlowSet.single(hysa), bctx)
            kc = balance_at.balance_map(hysa, bctx)
            cash = balance_at.cash_balance_map(hysa, bctx)

            # Displayed balances are the rounded kind-correct (interest-accrued)
            # map, strictly above the no-interest cash balance by the horizon.
            projected = dict(_all_columns(view))
            assert set(projected) == set(cash)
            for pid, column in projected.items():
                assert column.balance == round_money(kc[pid])
            assert (
                projected[periods[-1].id].balance
                > cash[periods[-1].id]
            )

            # The rows reconcile to the cent off the view's own columns.
            _assert_grid_view_reconciles(view)

            # The accrual is real interest: the telescoped total equals the
            # final premium and matches the kernel's interest within a cent
            # (the two round on slightly different paths).
            params = (
                db.session.query(InterestParams)
                .filter_by(account_id=hysa.id).one()
            )
            kernel_interest = net_worth_kernel.interest_by_period_for_account(
                hysa, bctx,
            )
            total_accrual = sum(
                column.accrual for _pid, column in _all_columns(view)
            )
            assert total_accrual == (
                projected[periods[-1].id].balance
                - cash[periods[-1].id]
            )
            assert abs(total_accrual - sum(kernel_interest.values())) <= Decimal("0.02")
            assert total_accrual > Decimal("0.00")

    def test_investment_renders_the_modelled_balance_and_reconciles(
        self, app, db, seed_user, seed_periods_today,
    ):
        """An INVESTMENT grid account shows the MODELLED balance (ruling R-W).

        **This test asserted the opposite until plan step X-g3b**, on the
        reasoning that an investment's balance is projection-driven rather than
        a sum of its transactions, so "an ad-hoc grid row would not move a
        kind-correct balance".  Under one event replay that is no longer true --
        a typed grid row IS an event in the same stream, measured on the real
        Empower 401(k) at ``$1,003.84`` in its own column and ``$1,211.04`` at
        the horizon, the difference being the accrual ON the new money.  So the
        objection that justified the cash basis is gone, and what remained was
        one account answered two ways: ``$31,070.06`` here against ``$48,712.19``
        on ``/savings`` (finding N-76).  The developer's ruling R-W is the
        authority that the expected behaviour changed.

        The grid balance must now EQUAL the modelled map -- not merely exceed
        the cash basis -- because equality is what closes N-76, and it must
        still reconcile off the view's own rows.
        """
        with app.app_context():
            user_id = seed_user["user"].id
            bctx = BalanceContext.build(user_id)
            periods = all_periods(user_id)
            inv = make_investment_account(
                seed_user, db.session, periods[2], Decimal("10000.00"),
            )

            view = balance_at.grid_balance_view(CashFlowSet.single(inv), bctx)
            cash = balance_at.cash_balance_map(inv, bctx)
            modelled = balance_at.balance_map(inv, bctx)

            # THE unification: the grid and /savings are the same producer's
            # answer, in every column.
            assert {
                pid: column.balance for pid, column in _all_columns(view)
            } == dict(modelled)
            # Non-vacuous: the modelled answer is genuinely above the cash one,
            # so an unmoved grid would fail rather than agree by accident.
            assert view.columns[periods[-1].id].balance > cash[periods[-1].id]
            # And the growth is explained on screen rather than implied.
            assert any(
                column.accrual > Decimal("0.00")
                for column in view.columns.values()
            )
            _assert_grid_view_reconciles(view)

    def test_property_renders_the_modelled_balance_and_reconciles(
        self, app, db, seed_user, seed_periods_today,
    ):
        """An APPRECIATING grid account shows the MODELLED value (ruling R-W).

        The sibling inversion, for the kind whose accrual row reads
        "Appreciation".  It asserted the flat market value until plan step
        X-g3b; a house that appreciates has that appreciation on the grid now,
        with a row explaining it, and the figure equals what ``/savings`` and
        the net-worth trend already showed.
        """
        with app.app_context():
            user_id = seed_user["user"].id
            bctx = BalanceContext.build(user_id)
            periods = all_periods(user_id)
            prop = make_appreciating_account(
                seed_user, db.session, periods[2], Decimal("400000.00"),
                Decimal("0.03000"),
            )

            view = balance_at.grid_balance_view(CashFlowSet.single(prop), bctx)
            cash = balance_at.cash_balance_map(prop, bctx)
            modelled = balance_at.balance_map(prop, bctx)

            assert {
                pid: column.balance for pid, column in _all_columns(view)
            } == dict(modelled)
            assert view.columns[periods[-1].id].balance > cash[periods[-1].id]
            assert any(
                column.accrual > Decimal("0.00")
                for column in view.columns.values()
            )
            # A Property has no payroll feed, whatever else it models.
            assert all(
                column.contribution == Decimal("0.00")
                for column in view.columns.values()
            )
            _assert_grid_view_reconciles(view)

    def test_none_scenario_raises(
        self, app, db, seed_user, seed_periods_today,
    ):
        """grid_balance_view fails loud on a None scenario (seam contract)."""
        with app.app_context():
            user_id = seed_user["user"].id
            with pytest.raises(ValueError):
                balance_at.grid_balance_view(
                    CashFlowSet.single(seed_user["account"]), _no_baseline(user_id),
                )

    def test_an_interest_account_with_no_params_models_no_accrual(
        self, app, db, seed_user, seed_periods_today,
    ):
        """An INTEREST account with no params row shows cash, no accrual row.

        A HYSA whose ``InterestParams`` is missing models no rate, so inventing
        one would put interest on screen the account has never earned.  The
        view must show the folded cash balance with a ``0.00`` accrual in every
        column, never raise -- the degenerate arm of
        ``_interest.accrual_params``.  ``0.00`` and not ``None``: ruling
        R-AJ (c) made both modelled fields total, so "models no rate" and
        "earned nothing this window" report the same figure and ruling R-O's
        rule hides the row for both.

        (This replaces a test that forced ``balance_map`` to return ``None``
        and asserted the grid degraded to cash.  ``grid_balance_view`` does not
        call ``balance_map`` any more -- it folds once and layers -- so the
        branch that test drove no longer exists; the params-less account is the
        reachable state that still exercises the same "no accrual" outcome.)
        """
        with app.app_context():
            user_id = seed_user["user"].id
            bctx = BalanceContext.build(user_id)
            periods = all_periods(user_id)
            hysa = _make_hysa(db, seed_user, periods[0], Decimal("5000.00"))
            db.session.query(InterestParams).filter_by(
                account_id=hysa.id,
            ).delete()
            db.session.commit()
            db.session.refresh(hysa)

            cash = balance_at.cash_balance_map(hysa, bctx)
            view = balance_at.grid_balance_view(CashFlowSet.single(hysa), bctx)

            assert {
                pid: column.balance for pid, column in _all_columns(view)
            } == dict(cash)
            assert all(
                column.accrual == Decimal("0.00")
                and column.contribution == Decimal("0.00")
                for column in view.columns.values()
            )

    def test_the_accrual_is_pure_interest_on_one_income_basis(
        self, app, db, seed_user, seed_periods_today,
    ):
        """The accrual stays pure interest when income is DERIVED (M1).

        Regression guard for the income-basis trap ruling R-Q closes at the
        root.  The cash walk auto-built a LIVE income map while the
        kind-correct walk used the STORED amounts, so an interest account left
        on the defaults had its premium absorb the income recompute instead of
        being pure interest.  There is ONE walk now and it builds ONE map, so
        the divergence has no argument to arrive through -- and this pins the
        consequence.

        **The divergent figure is a real DATA state since plan step X-au-d**,
        where it was a monkeypatched read-time repair: the row DECLARES a
        definition whose price series states ``$1,500.00`` and stores nothing
        itself, so a walk reading the column would contribute ``None`` and one
        reading the definition contributes ``$1,500``.  The figures are
        unchanged, and the setup no longer patches a producer out of the app it
        is grading.

        The row sits in a FUTURE period (so ruling R-G lands it in its own
        column), and the accrual must telescope exactly to the premium over the
        cash basis with the income nowhere in it.
        """
        with app.app_context():
            user_id = seed_user["user"].id
            bctx = BalanceContext.build(user_id)
            periods = all_periods(user_id)
            hysa = _make_hysa(db, seed_user, periods[0], Decimal("5000.00"))
            income_txn = _derived_income_row(
                db, seed_user, hysa, periods[6], Decimal("1500.00"),
            )
            db.session.commit()
            assert income_txn.estimated_amount is None

            view = balance_at.grid_balance_view(CashFlowSet.single(hysa), bctx)
            cash = balance_at.cash_balance_map(hysa, bctx)
            last = periods[-1].id

            # The shape has teeth only if the live basis genuinely lands: the
            # cash column carries the $1,500, not the stored $1,000.
            assert cash[periods[6].id] - cash[periods[5].id] == Decimal(
                "1500.00",
            )

            # The accrual telescopes to the premium over that SAME basis, and
            # the first period's figure is hand-derived so the pin is on the
            # rate and the day count, not on a band a regression could sit in:
            #   Q(5000 * ((1 + 0.05/365) ** 14 - 1)) = 9.60
            # On the STORED basis the premium would carry the $500 income
            # delta as well, which is the divergence ruling R-Q deleted.
            projected = dict(_all_columns(view))
            assert view.columns[periods[0].id].accrual == Decimal("9.60")
            total_accrual = sum(
                column.accrual for _pid, column in _all_columns(view)
            )
            assert total_accrual == projected[last].balance - cash[last]
            assert total_accrual < Decimal("500.00"), (
                "the accrual absorbed the income delta instead of being "
                "interest"
            )


class TestGridRowFlags:
    """``GridBalanceView.row_flags`` -- ruling R-O's conditional-row rule.

    A conditional row renders for the whole visible window when at least ONE
    visible column carries a non-zero value.  Stated once here because the same
    rule governs three rows on four windows (the visible grid, the Plan tab, the
    mobile This Period card, and the two self-refresh partials), and a template
    deciding it per surface is how one form factor ends up rendering a balance
    its own figures cannot explain (ruling R-P).
    """

    @staticmethod
    def _view(columns):
        """Wrap *columns* in a view (the flags read nothing else)."""
        return balance_at.GridBalanceView(
            columns=OrderedDict(columns),
        )

    @staticmethod
    def _periods(*ids):
        """Return real :class:`DerivedPeriod` values for the given period ids.

        REAL periods rather than ``SimpleNamespace`` stand-ins since plan step
        C2-f2b, where ``row_flags`` stopped taking ORM rows: a stand-in cannot
        go stale against the type it imitates, it just keeps answering the
        attribute the producer stopped asking for.

        **A plain list, not a ``PeriodWindow``.**  A first cut of that step
        typed the parameter to the window and an adversarial design review
        refuted it: this rule reads ``period_id`` alone, so the window's two
        guarantees -- order and contiguity -- take no part in the answer, and
        the one that WOULD matter here (whose calendar these came from) is not
        one that type carries.  Typing to the container forced this builder to
        invent start and end dates for a rule that never reads them, which is
        the tell.  They are still real dates because the value type requires
        them; no assertion in this class depends on one.
        """
        opening = date(2026, 1, 2)
        return [
            DerivedPeriod(
                period_id=pid,
                period_index=index,
                start_date=opening + timedelta(days=14 * index),
                end_date=opening + timedelta(days=14 * index + 13),
                end_is_projected=False,
            )
            for index, pid in enumerate(ids)
        ]

    def test_all_zero_window_renders_no_row(self):
        """Every column zero -> all five rows hidden (the ordinary cash grid)."""
        view = self._view({1: _column(), 2: _column()})
        flags = view.row_flags(self._periods(1, 2))
        assert flags.elsewhere is False
        assert flags.period_timing is False
        assert flags.book_vs_bank is False
        assert flags.contribution is False
        assert flags.accrual is False

    def test_the_elsewhere_row_is_asked_its_own_question(self):
        """Plan step CC-4-1: "On other accounts" takes R-O's rule, per ROW.

        Asserted in both directions like the two remainders: a card bill in one
        column turns the row on for the window and no other row with it, and a
        timing figure alone leaves it off.  A negative figure -- income
        expected on a card, a refund -- is non-zero and renders.
        """
        card_bill = self._view({1: _column(), 2: _column(elsewhere="45.00")})
        flags = card_bill.row_flags(self._periods(1, 2))
        assert flags.elsewhere is True
        assert flags.period_timing is False
        assert flags.book_vs_bank is False

        timing_only = self._view({1: _column(period_timing="10.00"), 2: _column()})
        assert timing_only.row_flags(self._periods(1, 2)).elsewhere is False

        card_refund = self._view({1: _column(elsewhere="-12.50")})
        assert card_refund.row_flags(self._periods(1)).elsewhere is True

    def test_one_non_zero_column_renders_the_row_for_the_window(self):
        """A single non-zero column turns the row on for the whole window."""
        view = self._view({
            1: _column(),
            2: _column(period_timing="-788.68"),
            3: _column(),
        })
        assert view.row_flags(self._periods(1, 2, 3)).period_timing is True

    def test_the_rule_is_per_window_not_per_account(self):
        """A window that excludes the non-zero column hides the row.

        The flag is asked of the VISIBLE periods, so navigating away from the
        period that carries a remainder drops the row rather than leaving a
        permanently-zero line on the forward-looking windows -- the half of
        ruling R-O that rejected always-on.
        """
        view = self._view({1: _column(period_timing="12.00"), 2: _column()})
        assert view.row_flags(self._periods(2)).period_timing is False
        assert view.row_flags(self._periods(1, 2)).period_timing is True

    def test_a_negative_remainder_counts_as_non_zero(self):
        """The rule is non-zero, not positive -- timing swings both ways."""
        view = self._view({1: _column(period_timing="-0.01")})
        assert view.row_flags(self._periods(1)).period_timing is True

    def test_each_remainder_row_is_asked_the_question_separately(self):
        """R-DH (f): a window carrying only true-ups renders ONLY that row.

        The property the split exists for, and the one a shared flag could not
        express.  A window whose columns carry a ``book_vs_bank`` correction and
        no timing difference at all must render the "Book vs bank" row and NOT
        the "Period timing" row: a permanently-``$0.00`` timing line beside it
        reads as "measured, and the answer is zero" for a fact that was never in
        question, which is the opposite of what a diagnostic row is for.

        Asserted in BOTH directions, because a flag wired to the wrong field
        passes a one-directional check by luck.
        """
        true_ups_only = self._view({
            1: _column(), 2: _column(book_vs_bank="-160.05"),
        })
        flags = true_ups_only.row_flags(self._periods(1, 2))
        assert flags.book_vs_bank is True
        assert flags.period_timing is False

        timing_only = self._view({
            1: _column(period_timing="-427.22"), 2: _column(),
        })
        flags = timing_only.row_flags(self._periods(1, 2))
        assert flags.period_timing is True
        assert flags.book_vs_bank is False

    def test_two_remainders_that_cancel_still_render_both_rows(self):
        """Equal and opposite halves must not hide each other.

        The exact figures ruling R-DH (f) was measured on, negated against each
        other: ``+$427.22`` of timing beside ``-$427.22`` of book-vs-bank.  A
        flag computed on the SUM -- the shape the combined ``reconciliation``
        field made natural -- reads ``$0.00`` here and hides both rows, leaving
        the user a balance change with no visible explanation on a period where
        two real and opposite things happened.
        """
        view = self._view({
            1: _column(period_timing="427.22", book_vs_bank="-427.22"),
        })
        flags = view.row_flags(self._periods(1))
        assert flags.period_timing is True
        assert flags.book_vs_bank is True

    def test_an_all_zero_accrual_window_hides_the_accrual_row(self):
        """A window that earns nothing hides the row -- a labelled row of zeros.

        Both an account that models NO return and one whose visible window
        happens to earn nothing report ``0.00`` here, and ruling R-O removes the
        row in both cases.  The two used to be distinguishable -- ``None`` for
        the first, ``0.00`` for the second -- and ruling R-AJ (c) deleted that
        distinction: under one replay every column carries a ``Decimal``, so
        ``None`` is a state the producer cannot be in and a flag rule testing
        for it would be testing an unreachable arm.
        """
        view = self._view({1: _column(), 2: _column(accrual="0.00")})
        assert view.row_flags(self._periods(1, 2)).accrual is False

    def test_a_non_zero_accrual_renders_the_accrual_row(self):
        """One accruing column turns the modelled-return row on."""
        view = self._view({1: _column(accrual="0.00"), 2: _column(accrual="7.01")})
        assert view.row_flags(self._periods(1, 2)).accrual is True

    def test_a_negative_accrual_renders_the_accrual_row(self):
        """A market LOSS turns the row on -- the rule is non-zero, not positive.

        The two kinds ruling R-W adds to this row are bounded only ``> -1``
        (``investment_params`` / ``asset_appreciation_params``), so a
        depreciating asset or a down market accrues negative.  A rule that
        tested for a positive figure would hide exactly the column the user most
        needs to see (finding N-88's other half; the rendering of that sign is
        graded in ``tests/test_routes/test_grid/test_summary_rows.py``).
        """
        view = self._view({1: _column(accrual="-142.11")})
        assert view.row_flags(self._periods(1)).accrual is True

    def test_a_non_zero_contribution_renders_the_contribution_row(self):
        """One contributing column turns the Contributions row on.

        The third flag is its own predicate over its own field: the two modelled
        tiers answer different questions (what the market did, and what the user
        put in), and a single summed row can render POSITIVE on an account that
        LOST money -- measured on the real Empower 401(k) at a -10.5% return,
        ``-$7,366.83`` of market against ``+$9,624.27`` of payroll (ruling
        R-AH).  So the two rows appear and disappear independently.
        """
        view = self._view({
            1: _column(contribution="0.00"),
            2: _column(contribution="181.59"),
        })
        flags = view.row_flags(self._periods(1, 2))
        assert flags.contribution is True
        assert flags.accrual is False

    def test_an_accruing_window_that_contributes_nothing_hides_only_that_row(self):
        """An HYSA accrues and never contributes -- one row, not two.

        ``_asset_contributions.contribution_events`` returns ``[]`` for every
        kind but INVESTMENT, so an interest-bearing account's Contributions row
        can never render.  Pinned as a flag property rather than left to the
        producer, because it is the rule that keeps a permanently-zero row off
        the HYSA grid the developer actually uses.
        """
        view = self._view({1: _column(accrual="95.98"), 2: _column(accrual="96.30")})
        flags = view.row_flags(self._periods(1, 2))
        assert flags.accrual is True
        assert flags.contribution is False

    def test_a_period_outside_the_view_contributes_nothing(self):
        """A window period the projection never produced cannot flip a flag."""
        view = self._view({1: _column(period_timing="5.00")})
        assert view.row_flags(self._periods(99)).period_timing is False


class TestTheRemainderOracleSeesEveryTerm:
    """``_assert_grid_view_reconciles`` is the CUTOVER's oracle, so grade it.

    Plan step X-g3b moves every modelled account's grid balance, and this
    helper is what will police that the rows still explain it.  At X-g3a no
    PRODUCER can put a figure in ``contribution`` -- the kind gate admits only
    INTEREST accounts and their contribution feed is empty by construction --
    so an oracle written in the three-term form and one written in the four-term
    form are indistinguishable on every fixture in the suite.  That is exactly
    the state in which a term gets dropped and nothing notices.

    The helper takes a VIEW, not an account, so the control needs no producer
    and no fixture: a hand-built view whose balance delta contains a
    contribution reconciles with that term and cannot reconcile without it.
    Section 7.3 -- every guard gets a negative control that is shown to fire --
    and this is the guard that guards the money.

    **The identity is FIVE terms since plan step S1-c** (ruling R-DH (f) split
    the remainder in two).  The two remainder terms have no dedicated negative
    control here and do not need one: ``_two_columns`` gives them DISTINCT
    non-zero values (``10.00`` and ``-4.00``), so an oracle that dropped either
    one -- or read one twice -- fails ``test_a_contributing_column_reconciles``
    directly.  Stated rather than left implied, because "controlled by the
    fixture's shape" is only true while the shape stays asymmetric.
    """

    @staticmethod
    def _view(columns):
        """Wrap *columns* in a view (the oracle reads nothing else)."""
        return balance_at.GridBalanceView(
            columns=OrderedDict(columns),
        )

    # The figures are ruling R-AH's own: $181.59 is the employer's flat 5% of
    # $3,631.74 -- the term whose omission breaks the identity on 53 of 59 real
    # period pairs on the Empower 401(k) -- and $95.98 is the HYSA accrual this
    # file hand-derives elsewhere.
    _NET = "100.00"
    # Ruling credit_card:R-CC23's worked example: the $45 phone bill expected
    # on the card is budgeted in the paycheck's net and never leaves checking.
    _ELSEWHERE = "45.00"
    _PERIOD_TIMING = "10.00"
    _BOOK_VS_BANK = "-4.00"
    _CONTRIBUTION = "181.59"
    _ACCRUAL = "95.98"

    def _two_columns(self, *, contribution, elsewhere=_ELSEWHERE):
        """Return an opening column whose successor's delta is every term."""
        opening = _column(balance="1000.00")
        moved = _column(
            balance=str(
                Decimal("1000.00") + Decimal(self._NET) + Decimal(elsewhere)
                + Decimal(self._PERIOD_TIMING) + Decimal(self._BOOK_VS_BANK)
                + Decimal(contribution) + Decimal(self._ACCRUAL)
            ),
            net=self._NET,
            elsewhere=elsewhere,
            period_timing=self._PERIOD_TIMING,
            book_vs_bank=self._BOOK_VS_BANK,
            contribution=contribution,
            accrual=self._ACCRUAL,
        )
        return self._view({1: opening, 2: moved})

    def test_a_contributing_column_reconciles(self):
        """The four-term form holds when all four terms carry a figure."""
        _assert_grid_view_reconciles(
            self._two_columns(contribution=self._CONTRIBUTION),
        )

    def test_dropping_the_contribution_term_breaks_the_identity(self):
        """THE CONTROL: a delta short its contribution must not reconcile.

        Built by moving the contribution OUT of the column while leaving it in
        the balance delta, which is precisely what a three-term oracle would
        wave through -- and what X-g3b would ship if the term were dropped from
        the helper.  The failure message must name the term, so a real break
        points at the tier rather than at "the numbers disagree".
        """
        broken = self._two_columns(contribution=self._CONTRIBUTION)
        columns = OrderedDict(broken.columns)
        moved = columns[2]
        columns[2] = balance_at.GridColumn(
            balance=moved.balance,
            income=moved.income,
            expense=moved.expense,
            net=moved.net,
            elsewhere=moved.elsewhere,
            period_timing=moved.period_timing,
            book_vs_bank=moved.book_vs_bank,
            contribution=Decimal("0.00"),
            accrual=moved.accrual,
        )

        # The pattern matches the MESSAGE, not the repr.  ``match=`` searches
        # the whole rendered AssertionError, and pytest's rewriting appends the
        # GridColumn repr -- which contains the literal ``contribution=`` for
        # every column -- so a bare ``match="contribution"`` passes even when
        # the message never names the term.  ``+ contribution `` (a space, no
        # equals) appears only in the oracle's own sentence.
        with pytest.raises(AssertionError, match=r"\+ contribution "):
            _assert_grid_view_reconciles(self._view(columns))

    def test_the_accrual_term_is_load_bearing_too(self):
        """The same control for the tier the row already renders.

        Stated so the oracle is graded on BOTH modelled terms rather than only
        the new one: an oracle that had quietly lost its accrual term would
        also pass every INTEREST fixture whose window happens not to accrue.
        """
        view = self._two_columns(contribution=self._CONTRIBUTION)
        columns = OrderedDict(view.columns)
        moved = columns[2]
        columns[2] = balance_at.GridColumn(
            balance=moved.balance,
            income=moved.income,
            expense=moved.expense,
            net=moved.net,
            elsewhere=moved.elsewhere,
            period_timing=moved.period_timing,
            book_vs_bank=moved.book_vs_bank,
            contribution=moved.contribution,
            accrual=Decimal("0.00"),
        )

        with pytest.raises(AssertionError, match=r"\+ accrual "):
            _assert_grid_view_reconciles(self._view(columns))

    def test_dropping_the_elsewhere_term_breaks_the_identity(self):
        """The control for the term plan step CC-4-1 added (ruling R-CC16).

        The same shape as the contribution control: the ``$45.00`` the
        paycheck budgets on the card stays in the balance delta and leaves the
        column, which a five-term oracle would wave through.  The message must
        name the term.
        """
        broken = self._two_columns(contribution=self._CONTRIBUTION)
        columns = OrderedDict(broken.columns)
        moved = columns[2]
        columns[2] = balance_at.GridColumn(
            balance=moved.balance,
            income=moved.income,
            expense=moved.expense,
            net=moved.net,
            elsewhere=Decimal("0.00"),
            period_timing=moved.period_timing,
            book_vs_bank=moved.book_vs_bank,
            contribution=moved.contribution,
            accrual=moved.accrual,
        )

        with pytest.raises(AssertionError, match=r"\+ elsewhere "):
            _assert_grid_view_reconciles(self._view(columns))


class TestTheViewIsBuiltOnOneAmountModel:
    """Ruling R-Q: a cell and the balance row beside it price one row one way.

    **This class was ``TestTheViewOwnsTheLiveOverrideMap`` until plan step
    X-au-d, and the rename is the change.**  R-Q was answered by making the
    seam BUILD the live override map and hand it back, because before that the
    map was the caller's argument and the grid route built a second copy for
    its cells -- "provably identical" by an argument about which rows each side
    filters (finding **N-48**), which is the agreeing-by-coincidence shape this
    arc exists to end.

    There is no map to hand back now: a derived row stores no figure, so there
    is nothing for a recompute to be laid over and both the cells and the fold
    ask ``cash_ledger.amounts_by_id`` over the pass's own basis.  The field the
    old case asserted (``GridBalanceView.amount_overrides``) was carried to NO
    consumer at all -- an AST and grep census over ``app/``, ``app/templates``
    and ``app/static`` found zero reads, and the case's own docstring claimed
    "the grid reads ``view.amount_overrides.get(...)`` per row", which had been
    false since plan step X-au-c2b routed the grid through the amount model.
    So the field is deleted and this is what replaced the case: the property
    asserted end to end, on the two figures a reader actually sees.
    """

    def test_the_column_and_the_published_budget_agree_on_one_row(
        self, app, db, seed_user, seed_periods_today,
    ):
        """A derived row's income column and its published budget are one figure.

        The row DECLARES a definition stating ``$1,500.00`` and stores nothing,
        so neither side can answer by reading a column.

        **Both sides are asserted against the FIXTURE's figure rather than
        against each other**, and an adversarial review of plan step X-au-d is
        why the docstring says so: they reach one resolver through one chain,
        so "a producer that had drifted would differ here" names a shape the
        code no longer has.  What this grades is that the grid column and the
        published budget both reflect the DEFINITION -- a route computing its
        own figure, or a fold reading the row, fails on ``$1,500.00``.
        """
        with app.app_context():
            user_id = seed_user["user"].id
            bctx = BalanceContext.build(user_id)
            periods = all_periods(user_id)
            account = seed_user["account"]
            income_txn = _derived_income_row(
                db, seed_user, account, periods[1], Decimal("1500.00"),
            )
            db.session.commit()

            view = balance_at.grid_balance_view(CashFlowSet.single(account), bctx)
            budgets = cash_ledger.amounts_by_id([income_txn], bctx.amounts())

            assert income_txn.estimated_amount is None
            assert budgets[income_txn.id] == Decimal("1500.00")
            assert view.columns[periods[1].id].income == Decimal("1500.00")


class TestTheRemainderIsWhatTheRowsCannotExplain:
    """Ruling R-K's remainder, measured on producer-built columns.

    This class asserted the opposite until plan step X-c2b2: the remainder was
    a constant ``0.00``, and that was a true claim about the SHIPPING
    producers rather than a placeholder, because the balance row and the
    subtotal row both counted exactly the still-unpaid rows of one
    anchor-seeded walk.  Neither could see a settled row at all, so neither
    clock had anything to disagree about -- and every past column read ``$0.00``
    income and ``$0.00`` expenses while thousands of dollars moved through it
    (finding N-41).

    Now the subtotals count EVERY row attributed to the period and the balance
    counts money that MOVED, so the remainder carries what the two clocks
    disagree about.  What is asserted here is the pair of properties that makes
    it trustworthy: it is ``0.00`` exactly when nothing needs explaining, and
    it is the exact size of the disagreement when something does.

    **The remainder is TWO rows since plan step S1-c** (ruling R-DH (f)).
    Every case below is a TIMING one -- rows landing in a column other than
    the one they were budgeted to -- so each asserts its figure on
    ``period_timing`` and pins ``book_vs_bank`` at ``$0.00`` beside it.  That
    second assertion is the whole point of the split: these fixtures assert no
    balance at all beyond their opening, so a figure appearing on the
    book-vs-bank row would be the producer attributing a timing difference to
    the bank disagreeing with the app, which is a different diagnosis with
    different advice attached.
    """

    def test_a_period_where_everything_lands_in_its_own_column_has_no_remainder(
        self, app, db, seed_user, seed_periods_today,
    ):
        """Still-projected rows landing in their own columns explain themselves.

        A projected income and a projected expense, both in FUTURE periods so
        ruling R-G's clamp leaves them where they were budgeted: the budget
        clock and the cash clock agree on every column, so the remainder is
        ``0.00`` throughout and ruling R-O hides the row.
        """
        with app.app_context():
            user_id = seed_user["user"].id
            bctx = BalanceContext.build(user_id)
            periods = all_periods(user_id)
            account = seed_user["account"]
            _seed_grid_activity(db, seed_user, periods)

            view = balance_at.grid_balance_view(CashFlowSet.single(account), bctx)

            assert view.columns
            moved = [
                pid for pid, column in view.columns.items()
                if column.net != Decimal("0.00")
            ]
            assert moved, "the fixture must move money or this proves nothing"
            assert all(
                column.period_timing == Decimal("0.00")
                and column.book_vs_bank == Decimal("0.00")
                for column in view.columns.values()
            )
            flags = view.row_flags(bctx.reported_periods())
            assert flags.period_timing is False
            assert flags.book_vs_bank is False
            _assert_grid_view_reconciles(view)

    def test_a_row_that_settled_in_another_column_is_exactly_the_remainder(
        self, app, db, seed_user, seed_periods_today,
    ):
        """Budgeted here, paid there: both columns carry the timing difference.

        A ``$300.00`` expense budgeted to period 1 but settled inside period 3.
        Hand-computed: period 1 counts it on the BUDGET clock, so its net is
        ``-$300.00`` while no money moved there -- remainder ``+$300.00``.
        Period 3 counts nothing on the budget clock while ``-$300.00`` moved
        through it -- remainder ``-$300.00``.  The two net to zero across the
        window, which is why the row is called "Period timing" and not a
        correction.

        Nobody re-anchored, so ``book_vs_bank`` is ``$0.00`` in both columns
        and its row stays hidden (ruling R-DH (f) / R-O).  Under the combined
        remainder the two facts were indistinguishable, and this is the shape
        that tells them apart.
        """
        # pylint: disable=import-outside-toplevel
        from tests._test_helpers import create_settled_cash_transaction

        with app.app_context():
            user_id = seed_user["user"].id
            bctx = BalanceContext.build(user_id)
            periods = all_periods(user_id)
            account = seed_user["account"]
            create_settled_cash_transaction(
                seed_user, db.session, periods[1], Decimal("300.00"),
                settled_on=periods[3].start_date,
                name="paid two columns late",
            )
            db.session.commit()

            view = balance_at.grid_balance_view(CashFlowSet.single(account), bctx)

            assert view.columns[periods[1].id].net == Decimal("-300.00")
            assert view.columns[periods[1].id].period_timing == Decimal(
                "300.00",
            )
            assert view.columns[periods[3].id].net == Decimal("0.00")
            assert view.columns[periods[3].id].period_timing == Decimal(
                "-300.00",
            )
            assert all(
                column.book_vs_bank == Decimal("0.00")
                for column in view.columns.values()
            )
            flags = view.row_flags(bctx.reported_periods())
            assert flags.period_timing is True
            assert flags.book_vs_bank is False
            _assert_grid_view_reconciles(view)
