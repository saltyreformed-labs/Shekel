"""The plan half: which still-Projected transfers yield a leg, how the cash fold, the one reduction
and the contribution feeds read the transfer, and a transfer settled with no record counted by
neither half of the cash fold.

The part's docstring named the two status drifts it pinned, drift A counted once and drift B by
neither, until plan step ``balance:X-bi-6-4d-2`` deleted drift A's four cases (ruling
**R-BAL313**); :class:`TestATransferClosedAtZeroIsCountedByNeitherHalf` says what is left.
"""

from datetime import timedelta
from decimal import Decimal

from app import ref_cache
from app.enums import MovementFigureSourceEnum, StatusEnum
from app.models.settled_figure import SettledFigure
from app.models.transfer import Transfer
from app.services.balance_at._asset_contributions import _recorded_contributions
from app.services.balance_at._cash_fold import assembled_fold
from app.services.cash_ledger import planned_cash_rows, resolve_amount, sum_projected
from app.services.recorded_contributions import load_transfer_contributions_for_account
from app.services.transfer_legs import (
    TransferLeg,
    leg_of,
    parent_of,
    planned_transfer_legs,
)
from app.services.transfer_service import delete_transfer
from tests._test_helpers import (
    basis_for,
    capture_sql_statements,
    create_settled_transfer,
    create_transfer,
    make_investment_account,
    read_pass,
)

from ._helpers import (
    _AMOUNT,
    _AS_OF,
    _savings,
    _cancelled_transfer,
    _balance_on,
)


class TestTransferLegs:
    """The loader: which parents, on which side, and none that has settled."""

    def test_each_account_gets_its_own_side(
        self, app, db, seed_user, seed_periods,
    ):  # pylint: disable=unused-argument
        with app.app_context():
            checking = seed_user["account"]
            savings = _savings(seed_user)
            transfer = create_transfer(
                seed_user, db.session, checking, savings, seed_periods[2],
                amount=_AMOUNT,
            )
            db.session.commit()
            scenario_id = seed_user["scenario"].id

            on_checking = planned_transfer_legs(
                checking.id, scenario_id, options=(),
            )
            on_savings = planned_transfer_legs(
                savings.id, scenario_id, options=(),
            )

            assert [(leg.transfer.id, leg.is_income) for leg in on_checking] == [
                (transfer.id, False),
            ]
            assert [(leg.transfer.id, leg.is_income) for leg in on_savings] == [
                (transfer.id, True),
            ]

    def test_a_settled_a_cancelled_and_a_deleted_parent_yield_no_leg(
        self, app, db, seed_user, seed_periods,
    ):  # pylint: disable=unused-argument
        """The plan half admits live still-Projected parents and nothing else.

        A settled transfer is the RECORD half's (its sides' records); a
        Cancelled one is not money the account owes; a deleted one is gone.
        Each is built through the door so the state is one the app produces.
        """
        with app.app_context():
            checking = seed_user["account"]
            savings = _savings(seed_user)
            create_settled_transfer(
                seed_user, db.session, checking, savings, seed_periods[1],
                amount=_AMOUNT,
            )
            _cancelled_transfer(seed_user, checking, savings, seed_periods[2])
            deleted = create_transfer(
                seed_user, db.session, checking, savings, seed_periods[3],
                amount=_AMOUNT,
            )
            live = create_transfer(
                seed_user, db.session, checking, savings, seed_periods[4],
                amount=_AMOUNT,
            )
            db.session.commit()
            # Through the transfer service's delete, the door that hides a
            # transfer; it hid the parent and both shadows together until
            # plan step balance:X-bi-6-4d-2 made a transfer one row (ruling
            # R-BAL258).
            delete_transfer(deleted.id, seed_user["user"].id, soft=True)
            db.session.commit()

            legs = planned_transfer_legs(
                checking.id, seed_user["scenario"].id, options=(),
            )

            assert [leg.transfer.id for leg in legs] == [live.id]


class TestTheCashFoldReadsTheParent:
    """The general case: every account's plan derives its transfer legs."""

    def test_the_plan_holds_the_transfers_leg_once(
        self, app, db, seed_user, seed_periods,
    ):  # pylint: disable=unused-argument
        """``planned_cash_rows`` carries the transfer's leg once, on its from-side.

        The leg half derives the leg from the transfer.  The case also read
        that no ROW of the plan named a transfer (a shadow beside its leg
        would have counted the transfer twice) until plan step
        ``balance:X-bi-6-4d-2``'s vacuous-residue checkpoint: a transfer is
        one row since that step (ruling **R-BAL258**), and the database
        refused a row naming one (``ck_transactions_names_no_transfer``,
        pinned by ``tests/test_models/test_a_transfer_is_one_row/test_the_upgrade.py``'s
        ``TestTheUpgrade::test_the_database_refuses_a_twin``) until plan step
        balance:X-bi-6-5 dropped the column, so that read
        walked a list holding no such row (measured empty).  Renamed from
        ``test_the_plan_holds_a_leg_and_no_shadow_row``.
        """
        with app.app_context():
            checking = seed_user["account"]
            savings = _savings(seed_user)
            transfer = create_transfer(
                seed_user, db.session, checking, savings, seed_periods[2],
                amount=_AMOUNT,
            )
            db.session.commit()

            plan = planned_cash_rows(checking.id, seed_user["scenario"].id)

            legs = [item for item in plan if isinstance(item, TransferLeg)]
            assert [(leg.transfer.id, leg.is_income) for leg in legs] == [
                (transfer.id, False),
            ]

    def test_both_sides_fold_the_parent_to_the_cent(
        self, app, db, seed_user, seed_periods,
    ):  # pylint: disable=unused-argument
        """Worked: $250.00 checking -> savings lands -$250.00 and +$250.00."""
        with app.app_context():
            checking = seed_user["account"]
            savings = _savings(seed_user)
            scenario = seed_user["scenario"]
            period = seed_periods[2]
            before = period.start_date - timedelta(days=1)
            checking_before = _balance_on(checking, scenario, before)
            savings_before = _balance_on(savings, scenario, before)
            create_transfer(
                seed_user, db.session, checking, savings, period,
                amount=_AMOUNT,
            )
            db.session.commit()

            landed = period.start_date
            assert (
                _balance_on(checking, scenario, landed) - checking_before
                == -_AMOUNT
            )
            assert (
                _balance_on(savings, scenario, landed) - savings_before
                == _AMOUNT
            )

    def test_a_leg_lands_on_its_transfers_due_date(
        self, app, db, seed_user, seed_periods,
    ):  # pylint: disable=unused-argument
        """The landing day is the TRANSFER's due date, on both sides.

        The $250.00 checking -> savings transfer is created due on the 3rd
        day of its period, and each of its legs lands there: on each account
        the day before reads the pre-transfer balance and the 3rd reads the
        transfer (checking $250.00 lower, savings $250.00 higher).  The
        savings side's two reads joined at plan step
        ``balance:X-bi-6-4d-2``: until then the case read checking alone, so
        a fold that landed only the to-side's leg on its period's day instead
        of its due date failed none of the tests that passed at that step
        (measured over the whole suite).  Renamed from
        ``test_a_shadow_dated_around_the_service_lands_on_the_parents_day``:
        it was a firing control that moved the checking-side shadow's due
        date to the 8th around the service, the day the fold dated until
        plan step ``balance:X-bi-6a``.  A transfer came with two shadow rows
        until plan step ``balance:X-bi-6-4d-2`` made it one row (ruling
        **R-BAL258**), so there is no shadow to move, and the case keeps its
        transfer's day alone.
        """
        with app.app_context():
            checking = seed_user["account"]
            savings = _savings(seed_user)
            scenario = seed_user["scenario"]
            period = seed_periods[2]
            parents_day = period.start_date + timedelta(days=2)
            opening = _balance_on(checking, scenario, parents_day)
            savings_opening = _balance_on(savings, scenario, parents_day)
            transfer = create_transfer(
                seed_user, db.session, checking, savings, period,
                amount=_AMOUNT, due_date=parents_day,
            )
            db.session.commit()
            db.session.expire_all()
            assert db.session.get(Transfer, transfer.id).due_date == parents_day

            day_before = parents_day - timedelta(days=1)
            assert _balance_on(checking, scenario, day_before) == opening
            assert _balance_on(checking, scenario, parents_day) == (
                opening - _AMOUNT
            )
            assert _balance_on(savings, scenario, day_before) == savings_opening
            assert _balance_on(savings, scenario, parents_day) == (
                savings_opening + _AMOUNT
            )

    def test_the_fold_reads_the_plan_relation_once(
        self, app, db, seed_user, seed_periods,
    ):  # pylint: disable=unused-argument
        """ONE statement against ``budget.transfers`` for a whole account's plan.

        The leg loader is a set load, not a per-row walk: three projected
        transfers cost one read of the plan relation.
        """
        with app.app_context():
            checking = seed_user["account"]
            savings = _savings(seed_user)
            for period in seed_periods[2:5]:
                create_transfer(
                    seed_user, db.session, checking, savings, period,
                    amount=_AMOUNT,
                )
            db.session.commit()
            db.session.expire_all()
            ctx = read_pass(checking, seed_user["scenario"], _AS_OF)

            folded, statements = capture_sql_statements(
                lambda: assembled_fold(checking, ctx),
            )

            assert len([
                item for item in folded.plan.rows
                if isinstance(item, TransferLeg)
            ]) == 3, "the plan came back short -- a count over nothing"
            reads = [
                statement for statement, _ in statements
                if "FROM budget.transfers" in statement
            ]
            assert len(reads) == 1, reads


class TestSumProjectedOverLegs:
    """The ONE reduction values a leg on the side its account is on."""

    def test_an_income_and_an_expense_leg_reduce_on_their_own_sides(
        self, app, db, seed_user, seed_periods,
    ):  # pylint: disable=unused-argument
        with app.app_context():
            checking = seed_user["account"]
            savings = _savings(seed_user)
            transfer = create_transfer(
                seed_user, db.session, checking, savings, seed_periods[2],
                amount=_AMOUNT,
            )
            db.session.commit()
            basis = basis_for(checking, seed_user["scenario"])

            income, expense = sum_projected(
                [leg_of(transfer, savings.id), leg_of(transfer, checking.id)],
                basis,
            )

            assert (income, expense) == (_AMOUNT, _AMOUNT)
            assert resolve_amount(
                parent_of(leg_of(transfer, savings.id)), basis,
            ) == _AMOUNT

    def test_a_leg_of_a_parent_that_is_not_projected_is_skipped(
        self, app, db, seed_user, seed_periods,
    ):  # pylint: disable=unused-argument
        """The reduction re-checks the PARENT's status, as it does a row's.

        The loader admits only still-Projected parents; the reduction does not
        trust it, exactly as it re-applies ``is_projected`` to every row it is
        handed, so the two cannot disagree about which legs are in the plan.
        """
        with app.app_context():
            checking = seed_user["account"]
            savings = _savings(seed_user)
            settled = create_settled_transfer(
                seed_user, db.session, checking, savings, seed_periods[1],
                amount=_AMOUNT,
            )
            db.session.commit()

            assert sum_projected(
                [leg_of(settled, checking.id)],
                basis_for(checking, seed_user["scenario"]),
            ) == (Decimal("0.00"), Decimal("0.00"))


class TestTheContributionFeedsReadTheParent:
    """Sites 3 and 4: the investment feeds' projected half is the parent's."""

    def test_a_projected_contribution_is_the_parents_amount_unconfirmed(
        self, app, db, seed_user, seed_periods,
    ):  # pylint: disable=unused-argument
        with app.app_context():
            period = seed_periods[2]
            account = make_investment_account(
                seed_user, db.session, period, Decimal("1000.00"), name="401k",
            )
            create_transfer(
                seed_user, db.session, seed_user["account"], account, period,
                amount=_AMOUNT,
            )
            db.session.commit()
            basis = basis_for(account, seed_user["scenario"])

            feed = load_transfer_contributions_for_account(
                basis, account.id, [period.id],
            )
            recorded = _recorded_contributions(basis, account.id)

            assert [
                (r.account_id, r.payday, r.amount, r.is_confirmed)
                for r in feed.records
            ] == [(account.id, period.start_date, _AMOUNT, False)]
            assert feed.linked_account_ids == frozenset({account.id})
            assert recorded == {period.id: _AMOUNT}

    def test_a_cancelled_contribution_links_the_account_and_counts_nothing(
        self, app, db, seed_user, seed_periods,
    ):  # pylint: disable=unused-argument
        """The link set is read off the parents in EVERY status."""
        with app.app_context():
            period = seed_periods[2]
            account = make_investment_account(
                seed_user, db.session, period, Decimal("1000.00"), name="401k",
            )
            _cancelled_transfer(seed_user, seed_user["account"], account, period)
            db.session.commit()
            basis = basis_for(account, seed_user["scenario"])

            feed = load_transfer_contributions_for_account(
                basis, account.id, [period.id],
            )

            assert feed.records == []
            assert feed.linked_account_ids == frozenset({account.id})
            assert _recorded_contributions(basis, account.id) == {}

    def test_a_settled_contribution_is_its_record_confirmed(
        self, app, db, seed_user, seed_periods,
    ):  # pylint: disable=unused-argument
        """The RECORD half is the to-side's record, confirmed, at what it recorded.

        $240.00 where the plan says $250.00.  The record half was the
        to-side's shadow row when this case was written.
        """
        with app.app_context():
            period = seed_periods[2]
            account = make_investment_account(
                seed_user, db.session, period, Decimal("1000.00"), name="401k",
            )
            create_settled_transfer(
                seed_user, db.session, seed_user["account"], account, period,
                amount=_AMOUNT, settled_amount=Decimal("240.00"),
            )
            db.session.commit()
            basis = basis_for(account, seed_user["scenario"])

            feed = load_transfer_contributions_for_account(
                basis, account.id, [period.id],
            )

            assert [(r.amount, r.is_confirmed) for r in feed.records] == [
                (Decimal("240.00"), True),
            ]
            assert _recorded_contributions(basis, account.id) == {
                period.id: Decimal("240.00"),
            }


class TestATransferClosedAtZeroIsCountedByNeitherHalf:
    """A transfer settled with no record on either side is counted by neither half.

    The class pinned the two status drifts between a transfer and its
    shadows (Transfer Invariant 3) from plan step balance:X-bi-6a.  Drift A,
    a shadow settled under a still-Projected parent, was read by both halves
    (``-$500.00`` on a ``$250.00`` transfer, ledger row **BAL-500**) until
    plan step ``balance:X-bi-4a`` let a side's DATED movement, not a status,
    decide which relation its leg is in (ruling **R-BAL79**).  Its four
    cases were deleted at plan step ``balance:X-bi-6-4d-2`` (ruling
    **R-BAL313**): a transfer came with two shadow rows until that step made
    it one row (ruling **R-BAL258**), so no shadow's status can drift.  Drift
    B, the parent settled alone, is the one case left, and it is no drift
    any more: a settled transfer whose sides hold no record is the shape the
    status-band rule admits for a transfer closed at zero
    (``side_band_infrastructure``).  The
    loan walk counts the same state once, as a ``$0.00`` payment (ruling
    **R-BAL140**), in ``test_loan_settled_legs/test_the_two_halves.py``.
    """

    def test_a_settled_transfer_with_no_side_record_is_counted_by_neither(
        self, app, db, seed_user, seed_periods,
    ):  # pylint: disable=unused-argument
        """Settled with no record on either side, the $250.00 transfer moves nothing.

        The parent's status is written Done directly, beside the figure a
        ``$0.00`` close stores, ``0.00`` and typed (ruling **R-BAL365**):
        since plan step ``balance:X-bi-6-4d-2``'s 4d-4b-2 leaf the band
        trigger's figure arm refuses a settled transfer storing no figure.
        The fold's plan holds
        no leg of it on either account (the parent is not Projected), and the
        fold folds no settled fact (neither side holds a record), so each
        account reads on the transfer's day what it read before the transfer
        existed: checking does not fall $250.00 and savings does not rise
        $250.00.  The plan read, by the fold's own loader on both accounts,
        joined at plan step ``balance:X-bi-6-4d-2``'s vacuous-residue
        checkpoint: until then the case read it only through the balances,
        which the valuation's own re-check of the status holds, so a loader
        that admitted the closed transfer's leg on either side failed none of
        this case's reads (measured on each side).  The savings
        read joined at plan step ``balance:X-bi-6-4d-2``: until then the case
        read checking alone, and a cash fold that valued only the to-side's
        leg of such a transfer, at the transfer's amount, changed no balance
        any passing test read (measured over the whole suite at that step).
        The loan walk reads the same state's to-side by its own producer, in
        ``test_loan_settled_legs/``.  Until that step its
        shadows stayed Projected under the settled parent, a status drift no
        door wrote; a transfer is one row since (ruling **R-BAL258**), and
        the state is the status-band rule's zero close.
        """
        with app.app_context():
            checking = seed_user["account"]
            savings = _savings(seed_user)
            scenario = seed_user["scenario"]
            period = seed_periods[2]
            day = period.start_date
            checking_before = _balance_on(checking, scenario, day)
            savings_before = _balance_on(savings, scenario, day)
            transfer = create_transfer(
                seed_user, db.session, checking, savings, period,
                amount=_AMOUNT,
            )
            db.session.commit()
            parent = db.session.get(Transfer, transfer.id)
            parent.status_id = ref_cache.status_id(StatusEnum.DONE)
            parent.settled_figure_pair = SettledFigure(
                Decimal("0.00"),
                ref_cache.movement_figure_source_id(
                    MovementFigureSourceEnum.TYPED,
                ),
            )
            db.session.commit()
            db.session.expire_all()

            for account in (checking, savings):
                assert not [
                    item for item in planned_cash_rows(account.id, scenario.id)
                    if isinstance(item, TransferLeg)
                    and item.transfer.id == transfer.id
                ], f"the plan holds a leg of the closed transfer on account {account.id}"
            assert _balance_on(checking, scenario, day) == checking_before
            assert _balance_on(savings, scenario, day) == savings_before
