"""
Shekel Budget App -- the ONE plan-item loader: a screen states its scope once (plan step plan#2130)

:func:`~app.services.cash_flow_set.load_plan_items` turns each part of a
:class:`~app.services.cash_flow_set.PlanScope` -- the cash-flow set, the
scenario, the window, live, the status band and expenses only -- into a clause
for BOTH parent tables, and answers one list: the bill rows by id, then the
transfer sides in transfer order.  Each case below seeds a row AND a transfer
for the part it grades, so a part spelled for one table only fails here.

The far-leg rule (which side of a transfer the set draws) is
:func:`~app.services.cash_flow_set.leg_accounts_shown`'s, graded in
``test_transfer_legs/test_the_grids_legs.py``; the screens' own loads are
graded in their screens' suites.
"""

from datetime import timedelta
from decimal import Decimal

import pytest
from sqlalchemy import text

from app import ref_cache
from app.enums import StatusEnum, TxnTypeEnum
from app.extensions import db
from app.models.amount_ownership import AmountOwnership
from app.models.scenario import Scenario
from app.services import transfer_service
from app.services.cash_flow_set import (
    AttributionSpan,
    CashFlowSet,
    Paychecks,
    PlanScope,
    StatusBand,
    load_plan_items,
)
from app.services.transfer_legs import cell_key
from tests._test_helpers import (
    capture_sql_statements,
    create_savings_account,
    create_settled_cash_transaction,
    create_settled_transfer,
    create_transfer,
    last_covered_day,
    one_off_row_of,
)


def _row(seed_user, period, name, *, account=None, scenario=None,
         is_income=False, status=StatusEnum.PROJECTED, due_date=None):
    """Place a one-off row of *seed_user*'s in *period*, at *status*, flushed."""
    account = seed_user["account"] if account is None else account
    scenario = seed_user["scenario"] if scenario is None else scenario
    row = one_off_row_of(
        period, name=name, amount=Decimal("10.00"),
        user_id=seed_user["user"].id, account_id=account.id,
        scenario_id=scenario.id,
        transaction_type_id=ref_cache.txn_type_id(
            TxnTypeEnum.INCOME if is_income else TxnTypeEnum.EXPENSE,
        ),
        due_date=due_date,
    )
    row.status_id = ref_cache.status_id(status)
    db.session.flush()
    return row


def _cancelled_transfer(seed_user, from_account, to_account, period, amount):
    """Create a CANCELLED transfer through the creation door, flushed."""
    return transfer_service.create_transfer(transfer_service.TransferSpec(
        user_id=seed_user["user"].id,
        from_account_id=from_account.id,
        to_account_id=to_account.id,
        pay_period_id=period.id,
        scenario_id=seed_user["scenario"].id,
        amount_ownership=AmountOwnership.own(amount),
        status_id=ref_cache.status_id(StatusEnum.CANCELLED),
        category_id=None,
    ))


def _what_if(seed_user):
    """A second, non-baseline scenario of the seeded owner."""
    scenario = Scenario(
        user_id=seed_user["user"].id, name="What-if", is_baseline=False,
    )
    db.session.add(scenario)
    db.session.flush()
    return scenario


def _scope(seed_user, window, band, *, expenses_only=False, cash_flow=None):
    """The seeded owner's checking set behind *window* and *band*."""
    return PlanScope(
        cash_flow=(
            CashFlowSet.single(seed_user["account"])
            if cash_flow is None else cash_flow
        ),
        scenario_id=seed_user["scenario"].id,
        window=window,
        band=band,
        expenses_only=expenses_only,
    )


def _keys(items):
    """The items' identities, in the order the loader answered them."""
    return [cell_key(item) for item in items]


class TestEveryScopePartOnBothTables:
    """One state holding, for each part, a row and a transfer inside it and outside it."""

    @pytest.fixture()
    def state(self, app, db, seed_user, seed_periods):
        """Seed checking's set with an item of each kind in and out of every part.

        Inside (period 0, baseline, live, on checking): a projected expense,
        a Paid expense, a cancelled expense, a credit expense and a projected
        income row; a projected transfer out to savings, a projected one in
        from savings, a Paid one out and a cancelled one out.  Outside, one of
        each table per part: period 2, the what-if scenario, soft-deleted, on
        savings (not a member).  Amounts differ so no two ad-hoc transfers
        meet ``uq_transfers_adhoc_dedupe``.
        """
        with app.app_context():
            checking = seed_user["account"]
            savings = create_savings_account(
                seed_user, db.session, "Savings", Decimal("0.00"),
            )
            other = create_savings_account(
                seed_user, db.session, "Other", Decimal("0.00"),
            )
            what_if = _what_if(seed_user)
            here, later = seed_periods[0], seed_periods[2]

            rows = {
                "projected": _row(seed_user, here, "Projected"),
                "paid": create_settled_cash_transaction(
                    seed_user, db.session, here, Decimal("20.00"), name="Paid",
                ),
                "cancelled": _row(
                    seed_user, here, "Cancelled", status=StatusEnum.CANCELLED,
                ),
                "credit": _row(seed_user, here, "Credit", status=StatusEnum.CREDIT),
                "income": _row(seed_user, here, "Income", is_income=True),
            }
            deleted_row = _row(seed_user, here, "Deleted")
            deleted_row.is_deleted = True
            _row(seed_user, later, "Later")
            _row(seed_user, here, "What-if", scenario=what_if)
            _row(seed_user, here, "On savings", account=savings)

            transfers = {
                "out": create_transfer(
                    seed_user, db.session, checking, savings, here,
                    amount=Decimal("101.00"),
                ),
                "in": create_transfer(
                    seed_user, db.session, savings, checking, here,
                    amount=Decimal("102.00"),
                ),
                "paid": create_settled_transfer(
                    seed_user, db.session, checking, savings, here,
                    amount=Decimal("103.00"),
                ),
                "cancelled": _cancelled_transfer(
                    seed_user, checking, savings, here, Decimal("104.00"),
                ),
            }
            deleted = create_transfer(
                seed_user, db.session, checking, savings, here,
                amount=Decimal("105.00"),
            )
            create_transfer(
                seed_user, db.session, checking, savings, later,
                amount=Decimal("106.00"),
            )
            create_transfer(
                seed_user, db.session, checking, savings, here,
                amount=Decimal("107.00"), scenario=what_if,
            )
            create_transfer(
                seed_user, db.session, savings, other, here,
                amount=Decimal("108.00"),
            )
            db.session.commit()
            transfer_service.delete_transfer(
                deleted.id, seed_user["user"].id, soft=True,
            )
            db.session.commit()
            yield {
                "rows": {name: row.id for name, row in rows.items()},
                "sides": {
                    name: (transfer.id, checking.id)
                    for name, transfer in transfers.items()
                },
                "window": Paychecks((here.id,)),
            }

    @pytest.mark.parametrize(
        ("band", "expenses_only", "row_names", "side_names"),
        [
            (
                StatusBand.EVERY, False,
                ["projected", "paid", "cancelled", "credit", "income"],
                ["out", "in", "paid", "cancelled"],
            ),
            (
                StatusBand.BALANCE_CONTRIBUTING, False,
                ["projected", "paid", "income"], ["out", "in", "paid"],
            ),
            (StatusBand.PROJECTED, False, ["projected", "income"], ["out", "in"]),
            (StatusBand.PROJECTED, True, ["projected"], ["out"]),
            (StatusBand.SETTLED, True, ["paid"], ["paid"]),
        ],
    )
    def test_the_band_and_expenses_select_alike_on_rows_and_sides(
        self, app, seed_user, state, band, expenses_only, row_names, side_names,
    ):
        """Each band admits the same statuses of a row and of a transfer.

        Every case drops the outside items alike -- period 2, the what-if
        scenario, the soft-deleted, savings' own row and the savings -> other
        transfer that touches no member -- so each expected list is the
        inside items the band (and expenses only) keeps: the rows by id, then
        the sides by transfer id, each on checking.  Expenses only drops the
        income row and the transfer INTO checking, whose side on checking is
        money arriving.
        """
        with app.app_context():
            items = load_plan_items(
                _scope(seed_user, state["window"], band,
                       expenses_only=expenses_only),
                (),
            )

            expected_rows = sorted(state["rows"][name] for name in row_names)
            expected_sides = sorted(state["sides"][name] for name in side_names)
            assert _keys(items) == [*expected_rows, *expected_sides]


class TestTheAttributionSpan:
    """An item belongs to a span by ``COALESCE(due_date, its period's start)``."""

    def test_a_row_and_a_transfer_are_the_span_their_day_falls_in(
        self, app, db, seed_user, seed_periods,
    ):
        """Filed in period 0 but due inside period 2's span: in period 2's span.

        Filed in period 2 but due on period 0's start: in neither period 2's
        span nor period 0's membership answer for the span.  A transfer with
        no due date falls back to its period's start (period 2's), so it is
        in period 2's span; a row cannot be built undated, so the fallback is
        graded on the transfer.
        """
        with app.app_context():
            checking = seed_user["account"]
            savings = create_savings_account(
                seed_user, db.session, "Savings", Decimal("0.00"),
            )
            early, late = seed_periods[0], seed_periods[2]
            inside_day = late.start_date + timedelta(days=3)
            early_row = _row(seed_user, early, "Due late", due_date=inside_day)
            _row(seed_user, late, "Due early", due_date=early.start_date)
            early_transfer = create_transfer(
                seed_user, db.session, checking, savings, early,
                amount=Decimal("201.00"), due_date=inside_day,
            )
            create_transfer(
                seed_user, db.session, checking, savings, late,
                amount=Decimal("202.00"), due_date=early.start_date,
            )
            undated = create_transfer(
                seed_user, db.session, checking, savings, late,
                amount=Decimal("203.00"),
            )
            db.session.commit()
            assert undated.due_date is None

            items = load_plan_items(
                _scope(
                    seed_user,
                    AttributionSpan(late.start_date, last_covered_day(late)),
                    StatusBand.EVERY,
                ),
                (),
            )

            assert _keys(items) == [
                early_row.id,
                (early_transfer.id, checking.id),
                (undated.id, checking.id),
            ]

    def test_a_transfer_filed_under_another_owners_period_is_in_no_span(
        self, app, db, seed_user, seed_periods, seed_second_periods,
    ):
        """The joined period must be the transfer's own owner's.

        A row's period is held to its owner by ``fk_transactions_owner_period``
        and a transfer's by no key, so this case moves an undated transfer
        onto the SECOND owner's period 2 (the same dates as the first
        owner's) in SQL, the one writer that can.  Its fallback day would be
        that period's start, inside the span, so only the owner term keeps it
        out.
        """
        with app.app_context():
            checking = seed_user["account"]
            savings = create_savings_account(
                seed_user, db.session, "Savings", Decimal("0.00"),
            )
            late = seed_periods[2]
            strayed = create_transfer(
                seed_user, db.session, checking, savings, late,
                amount=Decimal("204.00"),
            )
            db.session.commit()
            db.session.execute(
                text(
                    "UPDATE budget.transfers SET pay_period_id = :period "
                    "WHERE id = :transfer"
                ),
                {"period": seed_second_periods[2].id, "transfer": strayed.id},
            )
            db.session.commit()

            items = load_plan_items(
                _scope(
                    seed_user,
                    AttributionSpan(late.start_date, last_covered_day(late)),
                    StatusBand.EVERY,
                ),
                (),
            )

            assert seed_second_periods[2].start_date == late.start_date
            assert _keys(items) == []


class TestTheListsOrder:
    """Rows by id, then sides by transfer id: finding plan#2186."""

    def test_rows_load_in_id_order_whatever_the_heap_holds(
        self, app, db, seed_user, seed_periods,
    ):
        """Two rows answer in id order after the first is moved to the next paycheck.

        The move rewrites an INDEXED column, so the first row's new version
        lands after the second's in the heap and in the period index alike:
        a scan with no ORDER BY, sequential or by index, returns the second
        first -- the planner-chosen order a grid cell drew before this step
        (finding plan#2186).
        """
        with app.app_context():
            first = _row(seed_user, seed_periods[0], "First")
            second = _row(seed_user, seed_periods[0], "Second")
            db.session.commit()
            first.pay_period_id = seed_periods[1].id
            db.session.commit()
            window = Paychecks((seed_periods[0].id, seed_periods[1].id))

            items = load_plan_items(
                _scope(seed_user, window, StatusBand.EVERY), (),
            )

            assert _keys(items) == [first.id, second.id]

    def test_sides_load_in_transfer_id_order_whatever_the_heap_holds(
        self, app, db, seed_user, seed_periods,
    ):
        """Two transfers' sides answer in transfer id order after the first is moved.

        The rows case's technique, for the transfer table: the move to the
        next paycheck goes through the transfer door and rewrites
        ``pay_period_id``, an INDEXED column (``idx_transfers_period_scenario``),
        so the first transfer's new version lands after the second's and a
        scan with no ORDER BY returns the second first.  Every other fixture
        creates its transfers in id order, which is why the sides' ORDER BY
        stood ungraded until the adversarial review of plan#2130 deleted it
        and 869 tests stayed green.
        """
        with app.app_context():
            checking = seed_user["account"]
            savings = create_savings_account(
                seed_user, db.session, "Savings", Decimal("0.00"),
            )
            first = create_transfer(
                seed_user, db.session, checking, savings, seed_periods[0],
                amount=Decimal("301.00"),
            )
            second = create_transfer(
                seed_user, db.session, checking, savings, seed_periods[0],
                amount=Decimal("302.00"),
            )
            db.session.commit()
            transfer_service.update_transfer(
                first.id, seed_user["user"].id, pay_period_id=seed_periods[1].id,
            )
            db.session.commit()
            window = Paychecks((seed_periods[0].id, seed_periods[1].id))

            items = load_plan_items(
                _scope(seed_user, window, StatusBand.EVERY), (),
            )

            assert _keys(items) == [
                (first.id, checking.id), (second.id, checking.id),
            ]


class TestWhatTheLoaderIssues:
    """Statements: none for an empty window, and the records' one only when asked."""

    def test_an_empty_paycheck_window_issues_nothing(self, app, seed_user):
        """``Paychecks(())`` answers ``[]`` before any statement."""
        with app.app_context():
            scope = _scope(seed_user, Paychecks(()), StatusBand.EVERY)
            items, statements = capture_sql_statements(
                lambda: load_plan_items(scope, ()),
            )
            assert items == []
            assert statements == []

    def test_side_records_load_every_sides_record_in_one_more_statement(
        self, app, db, seed_user, seed_periods,
    ):
        """With ``side_records`` a settled side's record reads no statement.

        Without it the record is read lazily, one statement at the read; with
        it that statement moves into the load (one more than the plain load)
        and the read issues none.  The identity map is emptied before each
        load, so neither inherits the other's loaded lists.
        """
        with app.app_context():
            checking = seed_user["account"]
            savings = create_savings_account(
                seed_user, db.session, "Savings", Decimal("0.00"),
            )
            period = seed_periods[0]
            settled = create_settled_transfer(
                seed_user, db.session, checking, savings, period,
                amount=Decimal("250.00"),
            )
            db.session.commit()
            settled_id = settled.id
            scope = _scope(seed_user, Paychecks((period.id,)), StatusBand.SETTLED)

            db.session.expunge_all()
            [plain], plain_load = capture_sql_statements(
                lambda: load_plan_items(scope, ()),
            )
            _record, plain_read = capture_sql_statements(lambda: plain.record)
            db.session.expunge_all()
            [loaded], loaded_load = capture_sql_statements(
                lambda: load_plan_items(scope, (), side_records=True),
            )
            record, loaded_read = capture_sql_statements(lambda: loaded.record)

            assert len(plain_read) == 1
            assert (len(loaded_load), len(loaded_read)) == (len(plain_load) + 1, 0)
            assert record is not None and record.expense_transfer_id == settled_id
