"""Balance-at seam tests: the cash-flow view, a settled row moving every cash answer, the interest
chip, the subtotals, and the contribution row.

Split from ``tests/test_services/test_balance_at.py`` by ``tools/split`` (ruling R-BAL301).
"""

from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

import pytest

from app import ref_cache
from app.enums import StatusEnum, TxnTypeEnum
from app.models.interest_params import InterestParams
from app.services import balance_at
from app.services.balance_at import _kernel as net_worth_kernel
from app.services.scenario_resolver import get_baseline_scenario
from app.services.balance_at import BalanceContext
from app.services.balance_at._cash_fold import assembled_fold
from app.services.balance_at._cash_periods import period_view_of
from app.services.cash_flow_set import CashFlowSet
from tests._test_helpers import (
    add_txn,
    all_periods,
    create_account_of_type,
    create_savings_account,
    create_settled_transfer,
    create_transfer,
    current_pay_period,
    derived_span,
    last_covered_day,
    make_investment_account,
    make_salary_profile,
    one_off_row_of,
)

from ._helpers import (
    _no_baseline,
    _make_hysa,
    _add_flat_deduction,
    _all_columns,
    _assert_grid_view_reconciles,
)


class TestCashFlowView:
    """``cash_balance_map`` / ``cash_balance_at`` -- the pure-cash view.

    The single-account cash-flow surfaces (the budget grid, obligations
    panel, calendar, and checking-detail page) read these instead of the
    kind-correct ``balance_map`` / ``balance_at``: they must show the
    account's pure transaction running-balance regardless of its kind, so
    the projected balance reconciles with the surface's own transaction
    rows.  These tests prove (1) the three entries are ONE fold read at three
    grains, so a map column, the scalar at that column's end date and the
    daily series' last day of it cannot disagree, and (2) they do NOT dispatch
    by kind: an INTEREST account's cash map omits the interest the
    kind-correct map accrues, which is the whole reason these entries exist
    (Level-1 Commit 8).
    """

    def test_cash_balance_map_is_the_scalar_at_every_period_end(
        self, app, db, seed_user, seed_periods_today,
    ):
        """Each column equals the scalar read at that column's end date.

        The map and the scalar were separate producers until plan step X-c2b2
        and had to be kept in step; they are one running total sampled at two
        grains now, so this holds by construction -- and stating it is what
        would catch a future reader that re-introduced a second walk.
        """
        with app.app_context():
            user_id = seed_user["user"].id
            scenario = get_baseline_scenario(user_id)
            bctx = BalanceContext.build(user_id)
            periods = all_periods(user_id)
            account = seed_user["account"]

            seam = balance_at.cash_balance_map(account, bctx)
            assert len(seam) == len(periods)  # the loop is not vacuous
            for period in periods:
                assert seam[period.id] == balance_at.cash_balance_at(
                    account, bctx, last_covered_day(period),
                ), f"map and scalar disagree at period {period.id}"

    def test_cash_map_omits_interest_unlike_kind_correct_map(
        self, app, db, seed_user, seed_periods_today,
    ):
        """For an HYSA, the cash map is the no-interest running balance.

        ``cash_balance_map`` must NOT accrue interest (it is the cash-flow
        view): its values stay flat at the $5,000 anchor (no transactions),
        strictly below the kind-correct ``balance_map``, which layers the
        modelled accrual on that same fold.  This is the divergence the cash
        entry exists for: a HYSA grid account whose balance row accrued
        interest would leave a change the rows on screen cannot explain.
        """
        with app.app_context():
            user_id = seed_user["user"].id
            scenario = get_baseline_scenario(user_id)
            bctx = BalanceContext.build(user_id)
            periods = all_periods(user_id)
            hysa = _make_hysa(db, seed_user, periods[0], Decimal("5000.00"))

            cash = balance_at.cash_balance_map(hysa, bctx)
            kind_correct = balance_at.balance_map(hysa, bctx)

            # No transactions + no interest -> flat at the anchor.
            assert set(cash.values()) == {Decimal("5000.00")}
            # The kind-correct view accrues interest strictly above it.
            assert kind_correct[periods[-1].id] > cash[periods[-1].id]

    def test_a_settled_post_anchor_row_is_counted_not_flagged(
        self, app, db, seed_user, seed_periods_today,
    ):
        """The stale-anchor banner's whole subject is now just counted.

        The grid used to render a warning when a settled row existed in a
        post-anchor period: those rows contributed nothing to the projection,
        so the balance might be wrong and only the user could fix it by
        re-anchoring (they did, 52 times in 119 days on the real account).
        The fold counts the row, so there is nothing left to warn about and
        plan step X-c2b2 deleted the flag, its banner and its detector.

        The seed account is anchored at ``periods[0]``; a RECEIVED income row
        of $500.00 in period 3 must RAISE the balance from period 3 on --
        $1,000 anchor + $500 = $1,500.00 -- while periods before it are
        unmoved.
        """
        with app.app_context():
            user_id = seed_user["user"].id
            bctx = BalanceContext.build(user_id)
            periods = all_periods(user_id)
            account = seed_user["account"]
            add_txn(
                db.session, seed_user, periods[3], "Deposit", "500.00",
                status_enum=StatusEnum.RECEIVED, is_income=True,
            )
            db.session.commit()

            seam = balance_at.cash_balance_map(account, bctx)
            assert seam[periods[2].id] == Decimal("1000.00")
            assert seam[periods[3].id] == Decimal("1500.00")
            assert seam[periods[-1].id] == Decimal("1500.00")

    def test_cash_balance_at_is_the_daily_series_on_that_day(
        self, app, db, seed_user, seed_periods_today,
    ):
        """The scalar equals the daily series' value for the same day.

        The third grain of the same fold.  These were two producers that
        measured $15.96 apart on the real Checking account the day before the
        cutover (finding cash D2); one is a sampling of the other now.
        """
        with app.app_context():
            user_id = seed_user["user"].id
            bctx = BalanceContext.build(user_id)
            periods = all_periods(user_id)
            account = seed_user["account"]
            as_of = periods[5].start_date

            series = balance_at.cash_daily_balance_series(
                account, bctx, as_of, as_of,
            )
            assert balance_at.cash_balance_at(
                account, bctx, as_of,
            ) == series[as_of]

    def test_cash_balance_at_is_no_interest_for_hysa(
        self, app, db, seed_user, seed_periods_today,
    ):
        """cash_balance_at is the no-interest scalar even for an HYSA.

        Mirrors the map case: the scalar cash view never layers interest and
        stays flat at the anchor for a transaction-free HYSA -- the calendar's
        month-end figure must be this cash-flow balance, not an
        interest-accrued one.
        """
        with app.app_context():
            user_id = seed_user["user"].id
            scenario = get_baseline_scenario(user_id)
            bctx = BalanceContext.build(user_id)
            periods = all_periods(user_id)
            hysa = _make_hysa(db, seed_user, periods[0], Decimal("5000.00"))
            as_of = last_covered_day(periods[-1])

            cash = balance_at.cash_balance_at(hysa, bctx, as_of)
            # No transactions, no interest -> flat at the anchor, strictly
            # below the kind-correct scalar which layers the accrual.
            assert cash == Decimal("5000.00")
            assert balance_at.balance_at(hysa, bctx, as_of) > cash

    def test_a_datetime_is_refused_where_a_civil_date_is_required(
        self, app, db, seed_user, seed_periods_today,
    ):
        """A ``datetime`` fails loud at the seam, not three layers down.

        ``datetime`` SUBCLASSES ``date``, so an ``isinstance`` guard accepts
        one -- it reads like a type check and is not one.  The fold's step
        boundaries are civil dates, so a ``datetime`` that got past would die
        inside ``bisect_right`` with a comparison error naming neither the
        entry nor the argument.  Both dated entries refuse it by exact type,
        and the message names the argument.
        """
        with app.app_context():
            user_id = seed_user["user"].id
            bctx = BalanceContext.build(user_id)
            account = seed_user["account"]
            instant = datetime(2026, 3, 20, 12, tzinfo=timezone.utc)

            with pytest.raises(TypeError, match="as_of"):
                balance_at.cash_balance_at(account, bctx, instant)
            with pytest.raises(TypeError, match="first_day"):
                balance_at.cash_daily_balance_series(
                    account, bctx, instant, date(2026, 3, 21),
                )
            with pytest.raises(TypeError, match="last_day"):
                balance_at.cash_daily_balance_series(
                    account, bctx, date(2026, 3, 20), instant,
                )
            # ...and a plain date is accepted, so the guard is not blanket.
            assert isinstance(
                balance_at.cash_balance_at(account, bctx, date(2026, 3, 20)),
                Decimal,
            )

    def test_cash_entries_raise_on_none_scenario(
        self, app, db, seed_user, seed_periods_today,
    ):
        """Both cash entries fail loud on a None scenario (C1 contract)."""
        with app.app_context():
            user_id = seed_user["user"].id
            periods = all_periods(user_id)
            account = seed_user["account"]
            as_of = periods[5].start_date

            with pytest.raises(ValueError):
                balance_at.cash_balance_map(account, _no_baseline(user_id))
            with pytest.raises(ValueError):
                balance_at.cash_balance_at(account, _no_baseline(user_id), as_of)


class TestASettledRowMovesEveryCashAnswerTogether:
    """The shape the cutover exists for, on every entry that must agree.

    A row SETTLED after the account's latest assertion is finding cash D1: the
    retired producers counted it nowhere, so the money left the bank and stayed
    on the screen.  Both tests below seed exactly that shape, because it is the
    only one on which the retired producers and the fold give different answers
    -- an account with no settled post-anchor activity cannot tell them apart,
    which is why the rest of this suite's fixtures could not.
    """

    def test_the_kind_correct_scalar_and_the_cash_scalar_are_one_call(
        self, app, db, seed_user, seed_periods_today,
    ):
        """For PLAIN, ``balance_at`` and ``cash_balance_at`` are the same fold.

        A plain account's kind-correct balance IS its cash-flow balance, so the
        two scalars must be one call (plan finding N-47): /savings reads the
        first and the dashboard reads the second, and a shape where they differ
        is one where those two pages contradict each other.

        Hand-computed: $1,000.00 anchor, one $250.00 expense settled after it,
        so both read $750.00.  On the retired date-precise producer the settled
        row contributed nothing and both would read $1,000.00 -- which is why
        this needs a settled row to have teeth at all.
        """
        # pylint: disable=import-outside-toplevel
        from tests._test_helpers import create_settled_cash_transaction

        with app.app_context():
            user_id = seed_user["user"].id
            bctx = BalanceContext.build(user_id)
            periods = all_periods(user_id)
            account = seed_user["account"]
            create_settled_cash_transaction(
                seed_user, periods[4], Decimal("250.00"),
                name="settled after the anchor",
                # The DAY is PASSED, not left to the status seam: this test
                # needs the settle on a specific day (one after the anchor's
                # period start), not on today.  It used to be passed for a
                # stronger reason -- the seam stamped the settle day from the
                # DATABASE clock, which ``freeze_today`` cannot patch -- and
                # that reason died with plan step X-f1 (ruling R-EC): the seam
                # stamps ``display_today()``, a Python read the freeze governs.
                settled_on=periods[4].start_date + timedelta(days=1),
            )
            db.session.commit()

            kind_correct = balance_at.balance_at(account, bctx, bctx.as_of)
            cash = balance_at.cash_balance_at(account, bctx, bctx.as_of)

            assert kind_correct == cash == Decimal("750.00")

    def test_the_grids_interest_row_is_not_the_settled_row_in_disguise(
        self, app, db, seed_user, seed_periods_today,
    ):
        """An INTEREST account's accrual stays interest-sized (finding N-49).

        The grid derives its "Interest" row from what the modelled accrual adds
        to the cash basis.  If the accrual's SEED lagged the cash map -- if it
        still walked forward from the ``current_anchor_balance`` cache while
        the cash map folded -- the row would absorb every settled row the cache
        never saw and label it earnings.  Measured on the real Money Market
        before the cutover: ``$2,007.01`` of "interest" in one column, which is
        the ``$2,000.00`` that had actually left the account.

        Hand-computed: a 5% HYSA anchored at $50,000.00 with a $2,000.00
        expense settled after that assertion.  The cash basis drops to
        $48,000.00, the displayed balance follows it, and the whole horizon's
        accrual stays a few hundred dollars of real interest -- never the
        $2,000.00 gap.
        """
        # pylint: disable=import-outside-toplevel
        from tests._test_helpers import create_settled_cash_transaction

        with app.app_context():
            user_id = seed_user["user"].id
            bctx = BalanceContext.build(user_id)
            periods = all_periods(user_id)
            hysa = _make_hysa(db, seed_user, periods[0], Decimal("50000.00"))
            create_settled_cash_transaction(
                seed_user, periods[4], Decimal("2000.00"),
                account=hysa, name="settled after the anchor",
                # Pinned for the reason the sibling test above documents.
                settled_on=periods[4].start_date + timedelta(days=1),
            )
            db.session.commit()

            view = balance_at.grid_balance_view(CashFlowSet.single(hysa), bctx)
            cash = balance_at.cash_balance_map(hysa, bctx)
            current = current_pay_period(user_id)

            # The settled row IS in the cash basis...
            assert cash[current.id] == Decimal("48000.00")
            # ...and in the displayed balance, which is that basis plus the
            # accrual -- never the basis the cache would have carried.
            column = view.columns[current.id]
            assert column.balance - cash[current.id] == sum(
                other.accrual for pid, other in view.columns.items()
                if pid in cash
                and list(cash).index(pid) <= list(cash).index(current.id)
            )
            # The accrual is interest, not the missing $2,000.00 -- and it is
            # pinned to the cent rather than to a band, because a band wide
            # enough to hold ten periods of compounding is also wide enough to
            # hold a day-count regression.  The first period is hand-derived:
            #   Q(50000 * ((1 + 0.05/365) ** 14 - 1)) = 95.98
            # (a 13-day window gives 89.11), and the ten-period total
            # compounds from there, stepping down when the $2,000.00 settles.
            # The first period is UNCHANGED by the daily grain, and that is
            # not a coincidence: compounding a daily rate over 14 days IS
            # ``(1 + 0.05/365) ** 14``, so the two rules are the same curve
            # re-grouped whenever no money moves inside the period.
            assert view.columns[periods[0].id].accrual == Decimal("95.98")
            total_accrual = sum(
                col.accrual for col in view.columns.values()
            )
            # $944.94 until plan step X-g2b, and the $0.28 it gained is DERIVED
            # rather than observed.  The per-period layer accrued a whole period
            # on that period's END balance, so period 4 earned all 14 of its
            # days on the post-settlement balance; the daily replay applies the
            # day's cash step first and then accrues on what is actually held,
            # so period 4's FIRST day still earns on the $2,000.00 that had not
            # left yet.  That is 2000 * 0.05 / 365 = $0.2740, plus $0.0032 of
            # its own compounding over the ~6 periods left in the horizon:
            # $0.28.  The two accrual BASES this arc found disagreeing -- the
            # interest path's period END and the growth path's period START --
            # collapse here into "the balance held on the day", which is what
            # leaves no boundary convention left to pin the wrong one.
            assert total_accrual == Decimal("945.22")
            _assert_grid_view_reconciles(view)


class TestTheInterestChipAndTheBalanceAreOneWalk:
    """The account-detail page's two seam calls report ONE projection.

    The cash detail page reads ``balance_map`` for an interest account's
    balances and ``interest_by_period_for_account`` for the "Interest, next
    12 mo" chip.  They are separate entries because interest EARNED is not a
    balance-at-T -- but they must be the same walk, or the page would show an
    accrual that does not explain its own balance change.  Since plan step
    X-c2b2 both go through ``_kernel._account_interest_projection``, so the
    property is structural; asserting it is what would catch a reader that
    gave one of them a second base.

    (This class replaces a parity check against
    ``calculate_balances_with_interest``, the composition X-c2b2 retired when
    the accrual's base became the cash fold.  Parity with a deleted producer
    cannot be asserted; the invariant it was standing in for can.)
    """

    def test_the_chip_explains_the_balance_change_exactly(
        self, app, db, seed_user, seed_periods_today,
    ):
        """balance[p] - balance[p-1] == cash[p] - cash[p-1] + interest[p].

        Seeds an HYSA (5% APY) anchored at ``periods[0]`` with a $1,000
        deposit at ``periods[6]`` so the running balance moves and interest
        accrues on it.  Every period's interest-accrued change must decompose
        into the cash change plus that period's chip figure, to the cent.

        The deposit is dated FORWARD of the read's as-of (``seed_periods_today``
        places today in period 4) so it lands in its own column: ruling R-G
        clamps a still-projected row whose date has passed up to ``as_of + 1``,
        which would put it in the current period and make the non-vacuity
        assertion below measure the wrong column.
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
            params = (
                db.session.query(InterestParams)
                .filter_by(account_id=hysa.id)
                .one()
            )

            accrued = balance_at.balance_map(hysa, bctx)
            cash = balance_at.cash_balance_map(hysa, bctx)
            interest = net_worth_kernel.interest_by_period_for_account(
                hysa, bctx,
            )

            assert len(periods) > 1  # the loop is not vacuous
            for previous, period in zip(periods, periods[1:]):
                assert (
                    accrued[period.id] - accrued[previous.id]
                    == (cash[period.id] - cash[previous.id])
                    + interest[period.id]
                ), f"period {period.id} does not decompose"

            # The projection is real, not flat: interest accrued and the
            # deposit raised the balance, so the identity is non-trivial.
            assert any(v > Decimal("0.00") for v in interest.values())
            assert cash[periods[6].id] - cash[periods[5].id] == Decimal(
                "1000.00",
            )


class TestTheSubtotalsAreThePaychecksAcrossTheSet:
    """Plan step credit_card:CC-4-1's PRODUCER-side control for the fifth term.

    Developer ruling ``credit_card:R-CC16``: the grid reads the owner's
    cash-flow set -- checking and its cards -- as ONE paycheck, so its
    subtotals sum every member's rows while the Projected End Balance stays one
    account's, and the identity gains ``elsewhere[p]``.  Ruling
    ``credit_card:R-CC23``: a transfer between two members counts ONCE, from
    the balance line's side.  ``TestTheRemainderOracleSeesEveryTerm`` grades
    the term on hand-built columns; this class puts a figure in it through
    :func:`~app.services.balance_at.grid_balance_view` itself.

    **The fixture is the ruling's own worked example**, on the paycheck
    ``seed_periods_today[6]``: the phone bill ``$45.00`` expected on the card,
    the grocery envelope ``$500.00`` on checking, and the ``$165.00`` card
    payment (checking -> card) in the same paycheck.  Hand-computed, with
    checking as the balance line::

        Total Income      0.00
        Total Expenses  710.00   (500 grocery + 45 phone + 165 payment leg)
        Net Cash Flow  -710.00
        On other accts   45.00   (the card's expense less its income;
                                  its payment leg is the FAR leg, excluded)
        checking moves -665.00 = -710.00 + 45.00

    and with the CARD as the balance line, the same paycheck seen from the
    card::

        Total Income    165.00   (the payment, now the near leg)
        Total Expenses  545.00   (500 grocery + 45 phone)
        Net Cash Flow  -380.00
        On other accts  500.00   (checking's expense less its income; ITS
                                  payment leg is now the far leg)
        the card moves  120.00 = -380.00 + 500.00

    Every row is placed PROJECTED in a paycheck that opens after the pass's
    ``as_of`` (asserted, not assumed: the fixture's periods 0-3 are PAST, and
    ruling R-G's clamp lands a past-period plan row on ``as_of + 1``, in
    today's column, which the identity absorbs through ``period_timing`` and
    a hand delta on the row's own column would not see), so each row lands on
    its own day and the column's balance delta is exactly the rows'.

    **What grades what.**  The identity oracle cannot grade the composition:
    ``net`` and ``elsewhere`` are built from the same other-member legs, so
    ``net + elsewhere`` is the balance account's own net ALGEBRAICALLY,
    whatever those legs hold -- the leaf's adversarial review named this.  The
    composition is graded two ways instead: the hand figures on the fixture
    column, and
    ``test_the_composition_is_each_members_own_cash_column_over_the_window``,
    which reads every member's OWN column off :func:`period_view_of` (the
    pre-CC-4-1 producer, an independent path) and checks every column of the
    window against it.
    """

    _PHONE = Decimal("45.00")
    _GROCERY = Decimal("500.00")
    _PAYMENT = Decimal("165.00")

    @staticmethod
    def _set(balance, other):
        """The two-member set with *balance* on the line."""
        return CashFlowSet(balance=balance, members=(balance, other))

    def _world(self, db, seed_user, periods):
        """Build the worked example; return ``(checking, card, paycheck)``."""
        user_id = seed_user["user"].id
        scenario_id = get_baseline_scenario(user_id).id
        checking = seed_user["account"]
        card = create_account_of_type(
            seed_user, db.session, "Credit Card", "Rewards Card",
            anchor_balance=Decimal("-500.00"),
        )
        paycheck = periods[6]
        assert paycheck.start_date > BalanceContext.build(user_id).as_of + timedelta(days=1)
        expense = ref_cache.txn_type_id(TxnTypeEnum.EXPENSE)
        one_off_row_of(
            paycheck, name="Phone", amount=self._PHONE, user_id=user_id,
            account_id=card.id, scenario_id=scenario_id,
            transaction_type_id=expense,
        )
        one_off_row_of(
            paycheck, name="Grocery", amount=self._GROCERY, user_id=user_id,
            account_id=checking.id, scenario_id=scenario_id,
            transaction_type_id=expense,
        )
        create_transfer(
            seed_user, checking, card, paycheck,
            amount=self._PAYMENT,
        )
        db.session.commit()
        return checking, card, paycheck

    @staticmethod
    def _delta(view, paycheck):
        """Return the paycheck column's balance change from its predecessor."""
        items = _all_columns(view)
        index = [pid for pid, _ in items].index(paycheck.id)
        return items[index][1].balance - items[index - 1][1].balance

    def test_checking_as_the_balance_line(
        self, app, db, seed_user, seed_periods_today,
    ):  # pylint: disable=unused-argument
        with app.app_context():
            checking, card, paycheck = self._world(db, seed_user, seed_periods_today)
            bctx = BalanceContext.build(seed_user["user"].id)

            view = balance_at.grid_balance_view(self._set(checking, card), bctx)

            column = view.columns[paycheck.id]
            assert column.income == Decimal("0.00")
            assert column.expense == Decimal("710.00")
            assert column.net == Decimal("-710.00")
            assert column.elsewhere == Decimal("45.00")
            assert self._delta(view, paycheck) == Decimal("-665.00")
            _assert_grid_view_reconciles(view)
            assert view.row_flags(
                [derived_span(paycheck)],
            ).elsewhere is True

    def test_the_card_as_the_balance_line(
        self, app, db, seed_user, seed_periods_today,
    ):  # pylint: disable=unused-argument
        with app.app_context():
            checking, card, paycheck = self._world(db, seed_user, seed_periods_today)
            bctx = BalanceContext.build(seed_user["user"].id)

            view = balance_at.grid_balance_view(self._set(card, checking), bctx)

            column = view.columns[paycheck.id]
            assert column.income == Decimal("165.00")
            assert column.expense == Decimal("545.00")
            assert column.net == Decimal("-380.00")
            assert column.elsewhere == Decimal("500.00")
            assert self._delta(view, paycheck) == Decimal("120.00")
            _assert_grid_view_reconciles(view)

    def test_a_settled_far_leg_is_excluded_too(
        self, app, db, seed_user, seed_periods_today,
    ):  # pylint: disable=unused-argument
        """The far leg of a PAID payment is a fact in the card's walk, keyed by its transfer.

        A settled leg is its covering movement's fact, which names its
        transfer (``CashSourceFact.transfer_id``, leaf ``X-bi-6-4a``; it was
        reached by the shadow's ``transaction_id`` until then), so the seam's
        exclusion is the same transfer id as the planned leg's.  Paid in the fixture paycheck: the
        card's balance delta is the same ``+120.00`` and its income the same
        ``165.00`` whether the payment is still projected or settled.
        """
        with app.app_context():
            checking, card, paycheck = self._world(db, seed_user, seed_periods_today)
            create_settled_transfer(
                seed_user, db.session, checking, card, seed_periods_today[5],
                amount=Decimal("80.00"),
            )
            db.session.commit()
            bctx = BalanceContext.build(seed_user["user"].id)

            from_checking = balance_at.grid_balance_view(self._set(checking, card), bctx)
            from_card = balance_at.grid_balance_view(self._set(card, checking), bctx)

            earlier = seed_periods_today[5].id
            # Seen from checking, the paid $80 is checking's own expense leg
            # in its paycheck and NOT the card's income; seen from the card,
            # the reverse.
            assert from_checking.columns[earlier].expense == Decimal("80.00")
            assert from_checking.columns[earlier].income == Decimal("0.00")
            assert from_checking.columns[earlier].elsewhere == Decimal("0.00")
            assert from_card.columns[earlier].income == Decimal("80.00")
            assert from_card.columns[earlier].expense == Decimal("0.00")
            assert from_card.columns[earlier].elsewhere == Decimal("0.00")
            _assert_grid_view_reconciles(from_checking)
            _assert_grid_view_reconciles(from_card)

    def test_a_transfer_with_one_endpoint_in_the_set_counts_from_that_endpoint(
        self, app, db, seed_user, seed_periods_today,
    ):  # pylint: disable=unused-argument
        """Savings -> card is the card's INCOME on the paycheck grid (R-CC23's kept case)."""
        with app.app_context():
            checking, card, paycheck = self._world(db, seed_user, seed_periods_today)
            savings = create_savings_account(
                seed_user, db.session, "Savings", Decimal("5000.00"),
            )
            create_transfer(
                seed_user, savings, card, paycheck,
                amount=Decimal("300.00"),
            )
            db.session.commit()
            bctx = BalanceContext.build(seed_user["user"].id)

            view = balance_at.grid_balance_view(self._set(checking, card), bctx)

            column = view.columns[paycheck.id]
            assert column.income == Decimal("300.00")
            assert column.expense == Decimal("710.00")
            # 45 phone - 300 from savings: the card's net budget is income.
            assert column.elsewhere == Decimal("-255.00")
            assert self._delta(view, paycheck) == Decimal("-665.00")
            _assert_grid_view_reconciles(view)

    def test_a_set_of_one_is_the_accounts_own_cash_column_unchanged(
        self, app, db, seed_user, seed_periods_today,
    ):  # pylint: disable=unused-argument
        """A set of one: checking's own columns, ``elsewhere`` zero, the row hidden.

        Compared column by column against the pre-CC-4-1 reading of the
        subtotals -- :func:`period_view_of` over checking's own fold, which
        is what the grid view read verbatim before the composition -- so the
        single-account view is shown unchanged rather than assumed.  The
        composition rounds each subtotal once over the composed legs where
        the cash column carried them verbatim; equal here in every column,
        which is the seam's cent-quantized premise measured rather than
        quoted.
        """
        with app.app_context():
            checking, card, paycheck = self._world(db, seed_user, seed_periods_today)
            del card
            bctx = BalanceContext.build(seed_user["user"].id)
            window = bctx.reported_periods()

            view = balance_at.grid_balance_view(CashFlowSet.single(checking), bctx)
            own = period_view_of(assembled_fold(checking, bctx), window)

            for period in window:
                composed = view.columns[period.period_id]
                cash = own.columns[period.period_id]
                assert (composed.income, composed.expense, composed.net) == (
                    cash.income, cash.expense, cash.net,
                ), period.period_id
                assert composed.elsewhere == Decimal("0.00")
            assert view.columns[paycheck.id].expense == Decimal("665.00")
            assert view.row_flags(window).elsewhere is False
            _assert_grid_view_reconciles(view)

    def test_the_composition_is_each_members_own_cash_column_over_the_window(
        self, app, db, seed_user, seed_periods_today,
    ):  # pylint: disable=unused-argument
        """Every column: the subtotals are the members' own columns summed, less the far leg.

        The independent path: each member's OWN :func:`period_view_of` column
        (the pre-CC-4-1 producer) gives what that member budgets per period.
        With the ``$165`` payment intra-set, the card's own column counts its
        income leg and the composed view must not, so on the fixture paycheck
        the composed income is the card's own income LESS ``$165`` and the
        term is the card's own ``expense - income`` PLUS ``$165``; on every
        other column the two agree exactly.  A composition that double-counted
        a member, folded the wrong one, or leaked a far leg into another
        period fails here where the identity oracle cannot see it.
        """
        with app.app_context():
            checking, card, paycheck = self._world(db, seed_user, seed_periods_today)
            bctx = BalanceContext.build(seed_user["user"].id)
            window = bctx.reported_periods()

            view = balance_at.grid_balance_view(self._set(checking, card), bctx)
            checking_own = period_view_of(assembled_fold(checking, bctx), window)
            card_own = period_view_of(assembled_fold(card, bctx), window)

            for period in window:
                composed = view.columns[period.period_id]
                mine = checking_own.columns[period.period_id]
                theirs = card_own.columns[period.period_id]
                far_income = (
                    self._PAYMENT if period.period_id == paycheck.id
                    else Decimal("0.00")
                )
                assert composed.income == mine.income + theirs.income - far_income, period.period_id
                assert composed.expense == mine.expense + theirs.expense, period.period_id
                assert composed.elsewhere == theirs.expense - theirs.income + far_income, period.period_id
                assert composed.net == composed.income - composed.expense, period.period_id
                assert composed.balance == mine.balance, period.period_id


class TestTheContributionRowOnARealFeed:
    """Plan step X-g3b's PRODUCER-side control for the fourth term.

    X-g3a seated ruling R-K's four-term identity and could only grade it on
    HAND-BUILT views (``TestTheRemainderOracleSeesEveryTerm``), because
    with the kind gate in place no producer could put a figure in
    ``contribution``: only an INTEREST account reached the replay, and only an
    INVESTMENT can have a payroll feed.  This is the first commit in which one
    can, so this is where the term is proven end to end -- from a real
    ``PaycheckLine`` through the replay to the column the grid renders.

    The figures are hand-computed off a $104,000 salary, which is $4,000.00 per
    period on the fixture's 26-period year -- chosen over the helper's default
    $75,000 precisely so the employer arithmetic below lands on whole cents and
    the pin is on the RULE rather than on a rounding path.
    """

    _EMPLOYEE = Decimal("200.00")
    _ANNUAL_SALARY = Decimal("104000.00")
    _GROSS = Decimal("4000.00")  # 104000 / 26

    def _401k_with_feed(self, db, seed_user, periods, **params):
        """Return a 401(k) anchored at ``periods[2]`` with a $200/period feed.

        The anchor matters: the opening assertion is stamped at the anchor
        period's first day, and a modelled CONTRIBUTION exists only STRICTLY
        after it (ruling R-Z), so ``periods[2]`` contributes nothing and every
        later period contributes.
        """
        account = make_investment_account(
            seed_user, db.session, periods[2], Decimal("10000.00"), **params,
        )
        profile = make_salary_profile(
            seed_user, db.session, pay=Decimal("4000.00"),  # $104,000.00 a year / 26
        )
        db.session.flush()
        _add_flat_deduction(db, profile, account, self._EMPLOYEE)
        db.session.commit()
        return account

    def test_the_employee_feed_lands_in_the_contribution_column(
        self, app, db, seed_user, seed_periods_today,
    ):
        """A $200/period deduction renders $200.00 in every period after the anchor.

        Hand-computed: a flat pre-tax $200.00 deduction contributes $200.00 per
        pay period, with no employer configured and no annual limit set, so the
        column IS the deduction.  The anchor period itself carries $0.00 --
        ruling R-Z's strict boundary, since a contribution on or before the
        asserted day is money the assertion already contains.
        """
        with app.app_context():
            user_id = seed_user["user"].id
            bctx = BalanceContext.build(user_id)
            periods = all_periods(user_id)
            account = self._401k_with_feed(db, seed_user, periods)

            view = balance_at.grid_balance_view(CashFlowSet.single(account), bctx)

            assert view.columns[periods[2].id].contribution == Decimal("0.00")
            assert view.columns[periods[3].id].contribution == self._EMPLOYEE
            assert view.columns[periods[4].id].contribution == self._EMPLOYEE
            # And the row therefore renders (ruling R-O's visibility rule).
            assert view.row_flags(bctx.reported_periods()).contribution is True
            _assert_grid_view_reconciles(view)

    def test_an_employer_match_is_in_the_same_column(
        self, app, db, seed_user, seed_periods_today,
    ):
        """A 100%-to-6% match doubles the column, and the arithmetic is pinned.

        Hand-computed on the $4,000.00 per-period gross:
          matchable = 4000.00 * 0.06 = 240.00
          matched   = min(employee 200.00, 240.00) * 1.00 = 200.00
          column    = employee 200.00 + employer 200.00 = 400.00

        **That gross is the PAYCHECK ENGINE's** since plan step
        **salary:R14-b** -- the gross it put on this payday's paycheck for the
        profile ``budget.investment_params.salary_profile_id`` names
        (**R-SAL5**).  There was a SECOND spelling until then:
        ``investment_projection._compute_deduction_per_period`` divided the
        profile's stored annual salary by the paycheck count, raise-blind,
        which is finding **D45**.  The two agree on this fixture ($104,000 /
        26, no raises), which is why the figure below is unchanged -- and on a
        raise-bearing profile they did not, by the whole raise.

        The employer half is what makes the row more than a restatement of the
        user's own deduction -- and on the developer's real Empower 401(k) it is
        a flat 5% that no employee amount is needed to trigger at all, which is
        the term ruling R-AH measured breaking the three-term identity on 53 of
        59 period pairs.
        """
        with app.app_context():
            user_id = seed_user["user"].id
            bctx = BalanceContext.build(user_id)
            periods = all_periods(user_id)
            account = self._401k_with_feed(
                db, seed_user, periods,
                employer_type="match",
                match_pct=Decimal("1.0000"),
                match_cap_pct=Decimal("0.0600"),
            )

            view = balance_at.grid_balance_view(CashFlowSet.single(account), bctx)

            assert view.columns[periods[3].id].contribution == Decimal("400.00")
            _assert_grid_view_reconciles(view)

    def test_the_three_term_identity_breaks_on_this_fixture(
        self, app, db, seed_user, seed_periods_today,
    ):
        """THE CONTROL: the fourth term is load-bearing on a real producer.

        ``_assert_grid_view_reconciles`` carries four terms, and X-g3a proved it
        can distinguish them on hand-built columns.  What no test could show
        until now is that a PRODUCER puts a figure in the fourth one -- so this
        recomputes the retired THREE-term form on this fixture's real columns
        and asserts it FAILS, by exactly the contribution.  Without this, the
        four-term oracle above could be passing because every producer-built
        column still carries ``$0.00`` there, which is the state X-g3a shipped
        in and could not escape.
        """
        with app.app_context():
            user_id = seed_user["user"].id
            bctx = BalanceContext.build(user_id)
            periods = all_periods(user_id)
            account = self._401k_with_feed(db, seed_user, periods)

            view = balance_at.grid_balance_view(CashFlowSet.single(account), bctx)
            previous = view.columns[periods[3].id]
            column = view.columns[periods[4].id]
            delta = column.balance - previous.balance
            without_contribution = (
                column.net + column.period_timing + column.book_vs_bank
                + column.accrual
            )

            # The retired form is short by the whole contribution, and the
            # full identity closes it exactly.
            assert column.contribution == self._EMPLOYEE
            assert delta != without_contribution
            assert delta - without_contribution == column.contribution
