"""Balance-at seam tests: liability owed at dates, and the scalar and the map agreeing.

Split from ``tests/test_services/test_balance_at.py`` by ``tools/split`` (ruling R-BAL301).
"""

from datetime import date, timedelta
from decimal import Decimal

import pytest

from app import ref_cache
from app.enums import TxnTypeEnum
from app.services import balance_at, liability_sign
from app.services.scenario_resolver import get_baseline_scenario
from app.services.balance_at import BalanceContext
from app.services.balance_at._resolution import resolved_loan
from tests._test_helpers import (
    all_periods,
    create_account_of_type,
    insert_trueup_event,
    last_covered_day,
    make_loan_payment_template,
    one_off_row_of,
)

from ._helpers import _no_baseline, _owed_today, _make_mortgage, _paid_then_trued_loan


class TestLiabilityOwedAtDates:
    """``liability_owed_at_dates``: the seam's FORWARD multi-date liability view.

    The seam entry that closed the W9906 fence hole
    (``docs/audits/balance_architecture/followup_fence_loan_owed_at_dates.md``):
    the horizon liability band used to reach past the seam into
    ``net_worth_kernel.loan_owed_at_dates`` and hold half the boundary rule (the
    non-amortizing flat carry) itself.  Since plan step credit_card:CC-1 (ruling
    R-CC14) the band is kind-blind -- every liability's future is the
    kind-correct read, a loan's schedule or any other liability's cash fold --
    and the flat hold survives only for the no-baseline case.  These tests pin
    both arms of that read, the one-read-per-liability shape, the today-point
    source, the sign convention, and the forward-only domain.
    """

    def test_amortizing_loan_amortizes_across_future_dates(
        self, app, db, seed_user, seed_periods_today,
    ):
        """A mortgage's owed balance strictly declines across future sample dates.

        A mortgage originated a year ago whose $200,000 balance is stated
        TODAY, at setup -- the mid-life-import shape, recorded the way the
        setup door records it (plan step R20: a ``tracking_start``).  The
        assertion is what makes it amortize: since plan step R16-b-2 (ruling
        R-R71) the plan charges every contractual installment after the
        loan's LATEST assertion, so with only its origination anchor the year
        of unrecorded installments would stand as arrears and the balance
        would GROW (the shape
        :meth:`test_forward_owed_credits_only_future_installments_not_overdue_ones`
        pins).
        """
        with app.app_context():
            user_id = seed_user["user"].id
            scenario = get_baseline_scenario(user_id)
            bctx = BalanceContext.build(user_id)
            periods = all_periods(user_id)
            acct, _params = _make_mortgage(
                db, seed_user, periods[0], Decimal("200000.00"),
                date.today() - timedelta(days=365), tracked_from=date.today(),
            )
            today = date.today()
            samples = [
                today,
                date(today.year + 1, 12, 31),
                date(today.year + 2, 12, 31),
            ]

            # The caller hands the HELD balance (ruling R-CC47, plan step
            # credit_card:CC-5-5c): -200,000.00 is what a loan owing $200,000
            # holds, and the band returns what it owes.
            owed = balance_at.liability_owed_at_dates(
                [acct], bctx, samples,
                {acct.id: Decimal("-200000.00")},
            )

            series = owed[acct.id]
            assert len(series) == len(samples)
            # Today is what the caller's confirmed balance owes; each later
            # year end has had another year of scheduled principal applied, so
            # the owed balance falls monotonically.
            assert series[0] == Decimal("200000.00")
            assert series[1] < series[0]
            assert series[2] < series[1]

    def test_today_point_is_the_callers_confirmed_balance(
        self, app, db, seed_user, seed_periods_today,
    ):
        """The today point comes from *current_balances*, never a schedule walk.

        Load-bearing: the caller's current balance is the ledger-confirmed figure
        the net-worth hero renders, so a band built on this reconciles with the
        hero at index 0 by construction.  A schedule walk at ``today`` would
        instead report the balance net of any OVERDUE unconfirmed payment (the
        due-basis rows stay in the forward walk), UNDERSTATING the debt.

        Pinned by passing a current balance the schedule could not possibly
        produce: what it owes must come back at index 0.  The balance is passed
        HELD (ruling R-CC47, plan step credit_card:CC-5-5c) -- ``-sentinel``, a
        loan owing the sentinel -- and the band returns what it owes, the
        sentinel itself.
        """
        with app.app_context():
            user_id = seed_user["user"].id
            scenario = get_baseline_scenario(user_id)
            bctx = BalanceContext.build(user_id)
            periods = all_periods(user_id)
            acct, _params = _make_mortgage(
                db, seed_user, periods[0], Decimal("200000.00"),
                date.today() - timedelta(days=365),
            )
            sentinel = Decimal("123456.78")

            owed = balance_at.liability_owed_at_dates(
                [acct], bctx, [date.today()], {acct.id: -sentinel},
            )

            assert owed[acct.id] == [sentinel]

    def test_a_card_with_no_rows_reads_its_opening_flat(
        self, app, db, seed_user, seed_periods_today,
    ):
        """A revolving Credit Card with nothing planned reads its fold: the opening, flat.

        The CONTROL for the case below.  Since plan step credit_card:CC-1
        (ruling R-CC14) the band reads every non-loan liability's cash fold at
        each future date rather than holding it flat; a card with no rows folds
        to its asserted ``-500.00`` on every day, so the figures here are the
        ones the old flat hold gave -- which is exactly why this case alone
        cannot tell the two rules apart, and the sibling with planned rows can.
        Also pins the sign convention: a card owing $500 HOLDS ``-500.00``, and
        the seam returns what it OWES, ``liability_sign.owed`` of the held
        balance (ruling R-CC47, plan step credit_card:CC-5-5c) -- the same
        ``owed()`` the net-worth hero sums into its liability total.  It took
        ``abs`` until then, which agrees on a card that owes, so this case
        cannot tell the two apart; the card holding a credit in
        ``tests/test_services/test_one_liability_sign.py`` can.
        """
        with app.app_context():
            user_id = seed_user["user"].id
            bctx = BalanceContext.build(user_id)
            card = create_account_of_type(
                seed_user, db.session, "Credit Card", "Rewards Card",
                anchor_balance=Decimal("-500.00"),
            )
            db.session.commit()
            today = bctx.as_of
            samples = [today, date(today.year + 1, 12, 31),
                       date(today.year + 5, 12, 31)]

            owed = balance_at.liability_owed_at_dates(
                [card], bctx, samples, {card.id: Decimal("-500.00")},
            )

            # owed(-500.00) = 500.00 at every sample: today from the caller, the
            # future from a fold that holds no row to move it.
            assert owed[card.id] == [Decimal("500.00")] * 3

    def test_a_cards_planned_rows_move_it_across_the_horizon(
        self, app, db, seed_user, seed_periods_today,
    ):
        """A card's projected purchase and payment move its owed balance by date.

        THE rule plan step credit_card:CC-1 ships (ruling ``credit_card:R-CC14``,
        design ``credit_card_from_scratch.md`` 3.1): a non-loan liability's
        forward model is its cash fold -- opening plus recorded movements plus
        the still-projected plan -- read at each future sample date, where the
        seam used to hold it flat "because it has no forward model".  This test
        REPLACES ``test_non_amortizing_liability_holds_flat``, which pinned that
        flat hold; the developer ruled the behaviour changed (R-CC14,
        2026-09-18), the one exception CLAUDE.md rule 5 allows.

        The card is asserted at ``-500.00``.  A ``$120.00`` purchase is planned
        in period 6 and a ``$200.00`` payment (an income row on the card) in
        period 8; the samples are today, the START of period 7 (after the
        purchase, before the payment) and the year end after next (after
        both).  Hand-computed, what the card owes is ``liability_sign.owed``
        of the held fold -- minus it (ruling R-CC47, plan step
        credit_card:CC-5-5c; it read ``abs(fold)`` until then, which agrees on
        a card that owes and parts on one holding a credit)::

            today                 caller's -500.00              ->  500.00
            period 7 start        -500.00 - 120.00 = -620.00    ->  620.00
            Dec 31 next year      -620.00 + 200.00 = -420.00    ->  420.00

        The second future point differs from the first, so a band that read
        ONE forward figure and repeated it, or that still held flat, fails
        here.  Every future point also equals the kind-correct scalar's own
        answer at that date: the band and ``balance_at`` are readings of ONE
        producer (``_kind_correct.balance_at_dates``), so the horizon cannot
        drift from the account page.
        """
        with app.app_context():
            user_id = seed_user["user"].id
            scenario = get_baseline_scenario(user_id)
            bctx = BalanceContext.build(user_id)
            periods = seed_periods_today
            card = create_account_of_type(
                seed_user, db.session, "Credit Card", "Rewards Card",
                anchor_balance=Decimal("-500.00"),
            )
            one_off_row_of(
                periods[6],
                name="Groceries on the card",
                amount=Decimal("120.00"),
                user_id=user_id,
                account_id=card.id,
                scenario_id=scenario.id,
                transaction_type_id=ref_cache.txn_type_id(TxnTypeEnum.EXPENSE),
            )
            one_off_row_of(
                periods[8],
                name="Card payment",
                amount=Decimal("200.00"),
                user_id=user_id,
                account_id=card.id,
                scenario_id=scenario.id,
                transaction_type_id=ref_cache.txn_type_id(TxnTypeEnum.INCOME),
            )
            db.session.commit()
            today = bctx.as_of
            between = periods[7].start_date
            assert periods[6].start_date < between <= periods[8].start_date - timedelta(days=1)
            assert between > today
            year_end = date(today.year + 1, 12, 31)
            samples = [today, between, year_end]

            owed = balance_at.liability_owed_at_dates(
                [card], bctx, samples, {card.id: Decimal("-500.00")},
            )

            assert owed[card.id] == [
                Decimal("500.00"), Decimal("620.00"), Decimal("420.00"),
            ]
            # The same producer answers the scalar, so the band's future
            # points ARE what the account page's figures for those days owe --
            # the one flip of the held scalar, not its magnitude.
            for sample, owed_on in zip(samples[1:], owed[card.id][1:]):
                assert owed_on == liability_sign.owed(
                    balance_at.balance_at(card, bctx, sample),
                )

    def test_no_baseline_holds_a_card_with_planned_rows_flat(
        self, app, db, seed_user, seed_periods_today,
    ):
        """With no baseline scenario a card holds flat even with rows planned.

        The no-baseline hold is a gate OLDER than plan step credit_card:CC-1
        and outside it: without a scenario there is no plan to fold, so the
        band answers the caller's current magnitude at every date rather than
        raising into ruling R-BW's handler.  Pinned on a card carrying planned
        rows so that the case is not vacuous -- under a baseline those rows
        move the band (the sibling above), and here they must not.
        """
        with app.app_context():
            user_id = seed_user["user"].id
            scenario = get_baseline_scenario(user_id)
            periods = seed_periods_today
            card = create_account_of_type(
                seed_user, db.session, "Credit Card", "Rewards Card",
                anchor_balance=Decimal("-500.00"),
            )
            one_off_row_of(
                periods[6],
                name="Groceries on the card",
                amount=Decimal("120.00"),
                user_id=user_id,
                account_id=card.id,
                scenario_id=scenario.id,
                transaction_type_id=ref_cache.txn_type_id(TxnTypeEnum.EXPENSE),
            )
            db.session.commit()
            no_baseline = _no_baseline(user_id)
            today = no_baseline.as_of
            samples = [today, periods[7].start_date, date(today.year + 1, 12, 31)]

            owed = balance_at.liability_owed_at_dates(
                [card], no_baseline, samples, {card.id: Decimal("-500.00")},
            )

            assert owed[card.id] == [Decimal("500.00")] * 3

    def test_each_liability_is_folded_once_per_pass(
        self, app, db, seed_user, seed_periods_today, monkeypatch,
    ):
        """The band reads each liability's producer ONCE over the whole future axis.

        Plan step credit_card:CC-1's condition on its own shape: the band hands
        the kind-correct producer the LIST of future dates, so a loan costs one
        ``positions`` walk and a card one ``fold_asset_balances`` assembly, not
        one per sample date.  Counted on the two producers the dispatch reaches,
        with the dates each was handed, because a per-date loop would return the
        identical figures and only the count can see it.
        """
        with app.app_context():
            user_id = seed_user["user"].id
            bctx = BalanceContext.build(user_id)
            periods = all_periods(user_id)
            mortgage, params = _make_mortgage(
                db, seed_user, periods[0], Decimal("200000.00"),
                date.today() - timedelta(days=365),
            )
            insert_trueup_event(
                params, Decimal("200000.00"), anchor_date=date.today(),
            )
            card = create_account_of_type(
                seed_user, db.session, "Credit Card", "Rewards Card",
                anchor_balance=Decimal("-500.00"),
            )
            db.session.commit()
            today = bctx.as_of
            future = [
                date(today.year + 1, 12, 31),
                date(today.year + 2, 12, 31),
                date(today.year + 3, 12, 31),
            ]
            samples = [today, *future]

            # pylint: disable=import-outside-toplevel -- the private seam
            # modules are reached only to count the producers' calls.
            from app.services.balance_at import _asset_fold, _kind_correct

            positions_calls: list[list[date]] = []
            fold_calls: list[list[date]] = []
            real_positions = _kind_correct.positions
            real_fold = _asset_fold.fold_asset_balances

            def counting_positions(account, ctx, dates):
                positions_calls.append(list(dates))
                return real_positions(account, ctx, dates)

            def counting_fold(account, ctx, dates, inputs):
                fold_calls.append(list(dates))
                return real_fold(account, ctx, dates, inputs)

            monkeypatch.setattr(_kind_correct, "positions", counting_positions)
            monkeypatch.setattr(
                _asset_fold, "fold_asset_balances", counting_fold,
            )

            # Both current balances are HELD (R-CC47): the loan owing $200,000
            # holds -200,000.00, the card owing $500 holds -500.00.
            owed = balance_at.liability_owed_at_dates(
                [mortgage, card], bctx, samples,
                {mortgage.id: Decimal("-200000.00"), card.id: Decimal("-500.00")},
            )

            assert positions_calls == [future], positions_calls
            assert fold_calls == [future], fold_calls
            assert len(owed[mortgage.id]) == len(owed[card.id]) == 4

    def test_no_baseline_scenario_holds_every_liability_flat(
        self, app, db, seed_user, seed_periods_today,
    ):
        """``scenario=None`` is the degenerate case of the SAME rule, not an error.

        No baseline means no loan is resolvable and no plan can be folded, so
        every liability -- including an amortizing mortgage -- holds flat.  This
        is the one public seam entry that does NOT raise on a None scenario: it
        has a correct answer, and raising would force each caller to re-derive
        the flat hold (the very duplication the seam exists to prevent).
        """
        with app.app_context():
            user_id = seed_user["user"].id
            periods = all_periods(user_id)
            acct, _params = _make_mortgage(
                db, seed_user, periods[0], Decimal("200000.00"),
                date.today() - timedelta(days=365),
            )
            today = date.today()
            samples = [today, date(today.year + 1, 12, 31)]

            # Passed HELD (R-CC47): a loan owing $200,000 holds -200,000.00.
            owed = balance_at.liability_owed_at_dates(
                [acct], _no_baseline(user_id), samples, {acct.id: Decimal("-200000.00")},
            )

            assert owed[acct.id] == [Decimal("200000.00")] * 2

    def test_liability_missing_from_current_balances_is_zero(
        self, app, db, seed_user, seed_periods_today,
    ):
        """An account absent from *current_balances* reads 0, not a KeyError."""
        with app.app_context():
            user_id = seed_user["user"].id
            scenario = get_baseline_scenario(user_id)
            bctx = BalanceContext.build(user_id)
            card = create_account_of_type(
                seed_user, db.session, "Credit Card", "Rewards Card",
                anchor_balance=Decimal("-500.00"),
            )
            db.session.commit()

            owed = balance_at.liability_owed_at_dates(
                [card], bctx, [date.today()], {},
            )

            assert owed[card.id] == [Decimal("0")]

    def test_past_sample_date_raises(
        self, app, db, seed_user, seed_periods_today,
    ):
        """A past date is a LEDGER read, not a projection -- the seam refuses it.

        The forward-only domain is a runtime invariant, not a docstring note.  A
        consumer that wants a loan's past balance must ask ``balance_at``, which
        routes an amortizing account's past to the genesis ledger (the only
        complete record -- it books the true-ups that have no schedule row).
        """
        with app.app_context():
            user_id = seed_user["user"].id
            scenario = get_baseline_scenario(user_id)
            bctx = BalanceContext.build(user_id)
            periods = all_periods(user_id)
            acct, _params = _make_mortgage(
                db, seed_user, periods[0], Decimal("200000.00"),
                date.today() - timedelta(days=365),
            )
            yesterday = date.today() - timedelta(days=1)

            # Passed HELD (R-CC47), like every caller's current balance: a
            # loan owing $200,000 holds -200,000.00.
            with pytest.raises(ValueError, match="FORWARD"):
                balance_at.liability_owed_at_dates(
                    [acct], bctx, [yesterday, date.today()],
                    {acct.id: Decimal("-200000.00")},
                )

    def test_projection_is_joined_by_date_not_by_position(
        self, app, db, seed_user, seed_periods_today,
    ):
        """Sample dates are joined BY DATE, so their ORDER cannot mis-value the band.

        The producer's returned list must never be consumed positionally.  If it
        were, de-duplicating or sorting the sample dates inside
        ``loan_owed_at_dates`` -- a natural optimization, since the schedule walk
        is the expensive part -- would silently shift every point of the
        liability band with no crash and no failing test.

        Pinned by passing the samples OUT of chronological order: the +2y point
        must still owe LESS than the +1y point, whatever order they arrive in.
        """
        with app.app_context():
            user_id = seed_user["user"].id
            scenario = get_baseline_scenario(user_id)
            bctx = BalanceContext.build(user_id)
            periods = all_periods(user_id)
            acct, _params = _make_mortgage(
                db, seed_user, periods[0], Decimal("200000.00"),
                date.today() - timedelta(days=365),
            )
            today = date.today()
            plus_one = date(today.year + 1, 12, 31)
            plus_two = date(today.year + 2, 12, 31)

            # Deliberately unsorted: today, +2y, +1y.  The current balance is
            # passed HELD (R-CC47): a loan owing $200,000 holds -200,000.00.
            owed = balance_at.liability_owed_at_dates(
                [acct], bctx, [today, plus_two, plus_one],
                {acct.id: Decimal("-200000.00")},
            )

            series = owed[acct.id]
            assert series[0] == Decimal("200000.00")
            # series[1] is the +2y sample and series[2] the +1y sample, so the
            # LATER date must owe strictly less -- the opposite of what a
            # positional join against a sorted producer list would return.
            assert series[1] < series[2], (
                "the +2y sample owes more than the +1y sample: the forward "
                "projection was joined by POSITION, not by date"
            )

    def test_mixed_liability_set_in_one_call(
        self, app, db, seed_user, seed_periods_today,
    ):
        """A loan and a card in ONE call: both arms of the read coexist in one result.

        The batch shape the sole caller actually passes.  The amortizing account
        must amortize while the card -- which carries no planned row here --
        folds to its asserted figure at every date, in the same result dict.
        The mortgage's $200,000 is stated today, at setup, so that it
        amortizes (see :meth:`test_amortizing_loan_amortizes_across_future_dates`).
        """
        with app.app_context():
            user_id = seed_user["user"].id
            scenario = get_baseline_scenario(user_id)
            bctx = BalanceContext.build(user_id)
            periods = all_periods(user_id)
            acct, _params = _make_mortgage(
                db, seed_user, periods[0], Decimal("200000.00"),
                date.today() - timedelta(days=365), tracked_from=date.today(),
            )
            card = create_account_of_type(
                seed_user, db.session, "Credit Card", "Rewards Card",
                anchor_balance=Decimal("-500.00"),
            )
            db.session.commit()
            today = date.today()
            samples = [today, date(today.year + 1, 12, 31)]

            # Both current balances are HELD (R-CC47): the loan owing $200,000
            # holds -200,000.00, the card owing $500 holds -500.00.
            owed = balance_at.liability_owed_at_dates(
                [acct, card], bctx, samples,
                {acct.id: Decimal("-200000.00"), card.id: Decimal("-500.00")},
            )

            assert set(owed) == {acct.id, card.id}
            # The loan amortizes; the card's fold holds no row, so it reads
            # its asserted 500.00 at every date.
            assert owed[acct.id][1] < owed[acct.id][0]
            assert owed[card.id] == [Decimal("500.00"), Decimal("500.00")]

    def test_forward_owed_credits_only_future_installments_not_overdue_ones(
        self, app, db, seed_user, seed_periods_today,
    ):
        """The band's forward point folds the PLAN, not the contractual schedule (B-9).

        A mortgage originated a year ago with NO payment records carries a year of
        overdue unconfirmed installments.  The retired forward walk credited every
        one of them, reporting the loan paid down for installments nobody made
        (finding B-9); the band now folds the forward PLAN -- here a manual
        $1,000.00 recurring payment, below the $1,083.33 a month of interest --
        so its forward point owes STRICTLY MORE than the contractual walk.  Until
        plan step recurrence:R16-f the loan had no recurring payment and its plan
        billed no missed installment; such a loan is planned as its contract line
        now (rulings plan#3982, plan#3983), crediting each installment as the
        walk does, so it cannot tell the two apart.

        **And the skipped year is CHARGED** (plan step R16-b-2, ruling R-R71,
        finding D53): the plan charges every contractual installment after the
        loan's latest assertion -- here its origination -- whether or not a
        payment lands in it, so the twelve unpaid months' interest stands when
        the first plan payment arrives, that payment clears interest before
        principal, and the shortfall capitalizes.  The forward point therefore
        owes MORE than today's balance, not less (the months before the
        owner's schedule opens are charged and paid by nothing, and the
        payment is below the interest after them).  Until R16-b-2 this asserted the opposite ("still
        amortizes below today's balance"), which was B-9's "an overdue slot
        with no record holds flat" -- a rule about what an unpaid installment
        PAYS that had been read as a rule about what an unpaid month CHARGES.

        The caller hands the band the balance the loan HOLDS (ruling R-CC47,
        plan step credit_card:CC-5-5c): ``-200,000.00``, a loan owing
        $200,000.  Handed ``+200,000.00`` -- the owed sign this case passed
        until then -- the band reads a $200,000 CREDIT at today, ``-200,000.00``
        owed, and the "owes more than today" assertion below holds for any
        forward figure above that.  The today point is pinned first so the
        comparison cannot go vacuous that way again.
        """
        with app.app_context():
            user_id = seed_user["user"].id
            bctx = BalanceContext.build(user_id)
            acct, _params = _make_mortgage(
                db, seed_user,
                all_periods(user_id)[0],
                Decimal("200000.00"), date.today() - timedelta(days=365),
            )
            make_loan_payment_template(
                db.session, seed_user, acct, amount="1000.00",
                derive_from_loan=False,
            )
            db.session.commit()
            today = date.today()
            far_out = date(today.year + 1, 12, 31)

            # Passed HELD (R-CC47): a loan owing $200,000 holds -200,000.00.
            owed = balance_at.liability_owed_at_dates(
                [acct], bctx, [today, far_out],
                {acct.id: Decimal("-200000.00")},
            )
            # Non-vacuity: today's point is the $200,000 the loan owes, so the
            # comparison with the forward point below measures arrears and not
            # the sign of the caller's figure.
            assert owed[acct.id][0] == Decimal("200000.00")

            state = resolved_loan(acct, bctx).state
            forward_rows = sorted(
                (row for row in state.schedule if not row.is_confirmed),
                key=lambda row: row.payment_date,
            )
            # The fixture really is in the hazardous state: a year of overdue
            # unconfirmed installments the retired walk would have credited.
            overdue = [r for r in forward_rows if r.payment_date <= today]
            assert len(overdue) > 6, "expected a run of overdue unconfirmed rows"
            due_by = [r for r in forward_rows if r.payment_date <= far_out]
            contractual_walk = (
                due_by[-1].remaining_balance if due_by
                else _owed_today(acct, bctx)
            )

            # The band pays what the owner pays, below the interest, so its
            # forward point owes strictly more than the contractual walk.
            assert owed[acct.id][1] > contractual_walk
            # And it CHARGES every one of the skipped months (R-R71), so the
            # arrears capitalize at the first plan payment and the forward point
            # owes more than today's balance.
            assert owed[acct.id][1] > owed[acct.id][0]

    def test_today_point_ignores_overdue_rows_that_would_understate_the_debt(
        self, app, db, seed_user, seed_periods_today,
    ):
        """An OVERDUE unpaid payment must not shrink the today point.

        This fixture is the real hazard, not a contrived one: the mortgage was
        originated a year ago with NO payments recorded, so its schedule carries
        a dozen UNCONFIRMED rows already past due.  The retired forward walk
        deliberately kept overdue rows in its walk (the project's due-basis
        treatment), so a schedule walk AT TODAY reports the balance net of
        payments that were never made -- thousands of dollars less than the loan
        actually owes.

        The seam must not do that.  Its today point is the caller's
        ledger-confirmed balance, full stop.  Pinned by asserting the two
        differ: the contractual schedule walk (recomputed inline as an independent
        oracle) understates, and the seam does not follow it.
        """
        with app.app_context():
            user_id = seed_user["user"].id
            bctx = BalanceContext.build(user_id)
            periods = all_periods(user_id)
            acct, _params = _make_mortgage(
                db, seed_user, periods[0], Decimal("200000.00"),
                date.today() - timedelta(days=365),
            )
            today = date.today()
            confirmed = Decimal("200000.00")

            state = resolved_loan(acct, bctx).state
            forward_rows = sorted(
                (row for row in state.schedule if not row.is_confirmed),
                key=lambda row: row.payment_date,
            )
            overdue = [row for row in forward_rows if row.payment_date <= today]
            # The fixture really is in the hazardous state.
            assert overdue, "expected overdue unconfirmed rows in this fixture"
            # The contractual walk (last unconfirmed row on-or-before today),
            # recomputed inline -- the understating value the seam must NOT follow.
            walk_at_today = overdue[-1].remaining_balance
            assert walk_at_today < confirmed, (
                "a schedule walk at today should understate the debt here"
            )

            # ``confirmed`` is what the loan OWES; the caller hands the band the
            # balance it HOLDS (R-CC47), which is its negation.
            owed = balance_at.liability_owed_at_dates(
                [acct], bctx, [today], {acct.id: -confirmed},
            )

            # The seam reports what is OWED, not what the schedule wishes was paid.
            assert owed[acct.id] == [confirmed]
            assert owed[acct.id][0] != walk_at_today


class TestScalarAndMapAgree:
    """The scalar and the per-period map agree on every day of seven loan shapes.

    Defect 2a was a $3,455.79 divergence between ``balance_at`` and ``balance_map``
    for the same loan on the same day -- two producers the code's own docstrings
    called siblings, with nothing comparing them.  Since the cutover BOTH read the
    one loan producer :func:`~app.services.balance_at.positions` (the scalar at
    step C3b1, the per-period map at C3b3), so a divergence from two DIFFERENT
    readers is now structurally impossible -- which is the cutover's whole point.

    **What this now pins, stated plainly so the next reader does not over-trust
    it.**  Both sides call ``positions``, so this is no longer a two-reader
    comparison; it is a SAMPLING-consistency check.  It pins that the map's
    per-period sampler (:func:`~app.services.balance_at.positions_period_map`)
    asks ``positions`` at the SAME date the scalar is asked at for each period --
    ``min(period.end, as_of)`` for a begun period (the current-period clamp) and
    ``period.end`` for a future one.  A regression in that sampling -- most
    dangerously dropping the current-period clamp, which would hand the current
    period to the forward projection -- would surface here across all seven shapes,
    including the origination-inside-the-current-period keying trap (shape 6).  The
    balance VALUES themselves are pinned elsewhere: B2's fold-vs-reader oracle and
    the C6a hand-computed fold oracles (``test_loan_plan_forward_oracle.py`` and
    ``test_loan_plan_assembly.py``) -- plan Section 7.2: never two producers that
    share code proving each other.

    "Every loan shape the app can produce" is what this docstring used to claim.
    It is a fixture matrix, not a proof of exhaustiveness, and the shapes are
    enumerated rather than generated.

    **The probe date follows each period's OWN keying, and that is not a detail.**
    The map is period-END keyed since step C2 (a posting counts from its own
    ``entry_date``, which can fall mid period, so a payment settled during a period
    must count in it): a BEGUN period's confirmed value is the ledger at its END,
    and a FUTURE period's is the projection at its END.  The scalar reader,
    though, RAISES / PROJECTS for a future date, so the current period (begun, but
    its end is future) is probed at TODAY -- where the confirmed map, carrying every
    posting flat, equals the confirmed scalar.  Probing the current period at its
    END instead would compare the confirmed map to ``balance_at(period.end_date)``,
    which projects FORWARD (reducing by an overdue-but-unpaid installment -- FU-7)
    and is a different, smaller number.  Hence ``min(period.end_date, today)`` for a
    begun period, ``period.end_date`` for a future one.
    """

    # pylint: disable=too-many-arguments
    def _assert_agrees(self, acct, bctx, periods, shape):
        """Every period: map[p] == balance_at at the day that period is keyed to."""
        bmap = balance_at.balance_map(acct, bctx)
        for period in periods:
            begun = period.start_date <= bctx.as_of
            # Period-END keyed (C2), clamped to today for the current period,
            # whose end is future (the scalar would project there, not confirm).
            probe = (
                min(last_covered_day(period), bctx.as_of) if begun else last_covered_day(period)
            )
            scalar = balance_at.balance_at(acct, bctx, probe)
            assert bmap[period.id] == scalar, (
                f"{shape}: period {period.start_date}.."
                f"{last_covered_day(period)} "
                f"({'begun' if begun else 'future'}) -- "
                f"map={bmap[period.id]} scalar@{probe}={scalar}"
            )

    def test_every_enumerated_loan_shape(self, app, db, seed_user, seed_periods):
        """Seven shapes, including the two this arc was built to handle.

        The frozen 2026-03-20 clock (this package's autouse fixture) puts six of
        ``seed_periods`` in the past and four ahead, so both splice branches run
        for every shape.
        """
        # pylint: disable=import-outside-toplevel
        from app.enums import AcctTypeEnum
        from tests._test_helpers import (
            create_loan_account, insert_tracking_start_event,
            insert_trueup_event, loan_params_for,
        )

        with app.app_context():
            periods = seed_periods
            bctx_user = seed_user["user"].id

            # 1. Never paid, originated long ago -- the shape defect 2a lived in.
            never_paid = create_loan_account(
                seed_user, db.session, name="Never Paid",
                principal=Decimal("240000.00"), rate=Decimal("0.06000"),
                term=360, origination_date=date(2024, 9, 1), payment_day=1,
                account_type=AcctTypeEnum.MORTGAGE,
            )
            # 2. Trued up (the operator asserted a balance).
            trued_up = create_loan_account(
                seed_user, db.session, name="Trued Up",
                principal=Decimal("100000.00"), rate=Decimal("0.05000"),
                term=120, origination_date=date(2025, 2, 1), payment_day=1,
                account_type=AcctTypeEnum.AUTO_LOAN,
            )
            insert_trueup_event(
                loan_params_for(db.session, trued_up.id),
                Decimal("88000.00"), anchor_date=date(2026, 2, 10),
            )
            # 3. Mid-life import (the ledger opens at a tracking start).
            mid_life = create_loan_account(
                seed_user, db.session, name="Mid Life",
                principal=Decimal("300000.00"), rate=Decimal("0.04500"),
                term=360, origination_date=date(2019, 6, 1), payment_day=1,
                account_type=AcctTypeEnum.MORTGAGE,
            )
            insert_tracking_start_event(
                loan_params_for(db.session, mid_life.id),
                Decimal("240000.00"), date(2026, 1, 20),
            )
            # 4. Paid off (a $0.00 true-up -- the operator's explicit action).
            paid_off = create_loan_account(
                seed_user, db.session, name="Paid Off",
                principal=Decimal("15000.00"), rate=Decimal("0.07000"),
                term=48, origination_date=date(2025, 1, 1), payment_day=1,
                account_type=AcctTypeEnum.AUTO_LOAN,
            )
            insert_trueup_event(
                loan_params_for(db.session, paid_off.id),
                Decimal("0.00"), anchor_date=date(2026, 3, 1),
            )
            # 5. Not yet originated (C2's shape).
            upcoming = create_loan_account(
                seed_user, db.session, name="Upcoming",
                principal=Decimal("200000.00"), rate=Decimal("0.05000"),
                term=360, origination_date=date(2026, 4, 15), payment_day=1,
                account_type=AcctTypeEnum.MORTGAGE,
            )
            # 6. Originating INSIDE the current pay period -- the keying trap.
            closing_now = create_loan_account(
                seed_user, db.session, name="Closing Now",
                principal=Decimal("180000.00"), rate=Decimal("0.05500"),
                term=360, origination_date=date(2026, 3, 25), payment_day=1,
                account_type=AcctTypeEnum.MORTGAGE,
            )
            db.session.commit()
            # 7. PAID, then trued up -- the shape the forward producers exist
            # for, and the one this matrix lacked (plan Section 7.4).  Its
            # CONFIRMED rows are what make the begun half a real two-reader
            # check rather than a walk over an all-unconfirmed schedule.
            paid_then_trued = _paid_then_trued_loan(
                seed_user, db.session, periods,
            )

            bctx = BalanceContext.build(bctx_user)
            for acct, shape in [
                (never_paid, "never-paid"),
                (trued_up, "trued-up"),
                (mid_life, "mid-life import"),
                (paid_off, "paid-off"),
                (upcoming, "not-yet-originated"),
                (closing_now, "originating inside the current period"),
                (paid_then_trued, "paid, then trued up"),
            ]:
                self._assert_agrees(acct, bctx, periods, shape)
