"""The income arm and a transfer's two sides: a paycheck covered in its own direction, both sides
of a transfer covered, its lifecycle, and a loan payment's loan-side record.
"""

from datetime import timedelta
from decimal import Decimal

from sqlalchemy import text

from app import ref_cache
from app.enums import (
    LedgerAccountClassEnum,
    MovementFigureSourceEnum,
    PostingKindEnum,
    SettledDayBasisEnum,
    StatusEnum,
)
from app.extensions import db
from app.models.journal_entry import JournalEntry, Posting
from app.models.ledger_account import LedgerAccount
from app.models.transaction_entry import TransactionEntry
from app.services import (
    balance_at,
    cash_ledger,
    posting_service,
    row_valuation,
    status_seam,
    transfer_service,
)
from app.services.cash_ledger import settled_cash_facts
from app.services.settle_day import SettleDay
from tests._test_helpers import (
    create_loan_account,
    create_savings_account,
    generate_row_of,
    ledger_net,
    linked_ledger_account,
    make_income_template,
    on_both_sides,
    posted_loan_balance_at,
    transit_ledger_account,
)

from ._helpers import (
    _source,
    _settle,
    _revert,
    _only_movement,
    _per_day,
    _latest_anchor,
    _legs_of,
    _settled_pair,
)


def _paycheck(seed_user, period, amount="2572.78"):
    """One engine-generated income row of a fresh definition."""
    template = make_income_template(
        db.session, seed_user, amount=amount, name="Paycheck",
    )
    return generate_row_of(template, period)


class TestAPaycheckIsCoveredInItsOwnDirection:
    """Plan step X-bi-3b: the INCOME arm, and the control that names it.

    A settled paycheck is covered exactly as a bill is, and every reader of
    its movement reads ``+figure``: the walk's fact (``is_income`` and the
    delta both the parent's), the posted ledger's cash net, the ledger legs'
    kind and class, and the family valuation.  The plan's own control is the
    ``+figure`` assertion below: with the seam's income half deleted and the
    direction still spelled ``-amount`` anywhere, the fold reads
    ``-2572.78`` for a ``$2,572.78`` paycheck -- wrong by twice the figure.
    """

    def test_a_paycheck_settles_with_a_movement_reading_PLUS_figure(
        self, app, seed_user, seed_periods,
    ):
        with app.app_context():
            txn = _paycheck(seed_user, seed_periods[0])
            _settle(txn)
            db.session.flush()
            assert txn.status.is_settled
            movement = _only_movement(txn)
            assert movement.amount == Decimal("2572.78")
            assert movement.figure_source_id == _source(
                MovementFigureSourceEnum.RESOLVED,
            )
            # The movement carries the money IN: the fact says income, and
            # says +figure; the row is worth that movement (R-BAL81).
            facts = [
                fact for fact in settled_cash_facts(txn.account_id, txn.scenario_id)
                if fact.entry_id == movement.id
            ]
            assert len(facts) == 1
            assert facts[0].is_income is True
            assert facts[0].delta == Decimal("2572.78")
            assert status_seam.covered_cash_leg(txn, txn.account_id) == Decimal("2572.78")

    def test_the_fold_reads_the_movement_and_nothing_without_it(
        self, app, seed_user, seed_periods,
    ):
        """The movement IS the fold for income exactly as for a bill (R-BAL80)."""
        with app.app_context():
            txn = _paycheck(seed_user, seed_periods[0])
            _settle(txn)
            db.session.flush()
            account_id, scenario_id = txn.account_id, txn.scenario_id
            with_movement = _per_day(settled_cash_facts(account_id, scenario_id))
            assert with_movement[txn.settled_on] == Decimal("2572.78")
            movement = _only_movement(txn)
            # Deleted, THEN out of the list: the list no longer deletes (R-CC64;
            # rule-5 re-expression, developer-confirmed 2026-09-23).
            db.session.delete(movement)
            txn.entries.remove(movement)
            db.session.flush()
            db.session.expire(txn)
            # Worth nothing without its movement (R-BAL81), as the fold reads.
            assert status_seam.covered_cash_leg(txn, txn.account_id) == Decimal("0")
            assert _per_day(settled_cash_facts(account_id, scenario_id)) == {}

    def test_the_posted_ledger_books_the_family_as_INCOME(
        self, app, seed_user, seed_periods,
    ):
        """The cash net rises by the figure; the movement's legs are income.

        The counter leg lands in an INCOME-class category account and both
        legs carry the ``income`` kind -- the parent's, read through the one
        mapping the transaction writer uses (``_posting_write.ledger_class_of``
        / ``posting_kind_of``).  Measured as a DELTA: the fixture's own
        opening correction already sits on the cash ledger.
        """
        with app.app_context():
            txn = _paycheck(seed_user, seed_periods[0])
            cash = linked_ledger_account(db.session, txn.account_id)
            before = ledger_net(db.session, cash.id, txn.scenario_id)
            _settle(txn)
            db.session.flush()
            after = ledger_net(db.session, cash.id, txn.scenario_id)
            assert after - before == Decimal("2572.78")
            movement = _only_movement(txn)
            legs = (
                db.session.query(Posting, LedgerAccount)
                .join(JournalEntry, JournalEntry.id == Posting.journal_entry_id)
                .join(LedgerAccount, LedgerAccount.id == Posting.ledger_account_id)
                .filter(JournalEntry.transaction_entry_id == movement.id)
                .all()
            )
            assert {leg.ledger_account_id: leg.amount for leg, _ in legs} == {
                cash.id: Decimal("2572.78"),
                next(
                    account.id for _, account in legs if account.id != cash.id
                ): Decimal("-2572.78"),
            }
            income_kind = ref_cache.posting_kind_id(PostingKindEnum.INCOME)
            assert {leg.posting_kind_id for leg, _ in legs} == {income_kind}
            counter = next(account for _, account in legs if account.id != cash.id)
            assert counter.class_id == ref_cache.ledger_account_class_id(
                LedgerAccountClassEnum.INCOME,
            )

    def test_a_revert_un_dates_it_and_the_ledger_moves_back(
        self, app, seed_user, seed_periods,
    ):
        with app.app_context():
            txn = _paycheck(seed_user, seed_periods[0])
            cash = linked_ledger_account(db.session, txn.account_id)
            before = ledger_net(db.session, cash.id, txn.scenario_id)
            _settle(txn)
            db.session.flush()
            movement_id = _only_movement(txn).id
            assert ledger_net(db.session, cash.id, txn.scenario_id) - before == Decimal("2572.78")
            _revert(txn)
            db.session.flush()
            survivor = _only_movement(txn)
            assert survivor.id == movement_id
            assert survivor.settled_on is None
            assert survivor.amount == Decimal("2572.78")
            assert ledger_net(db.session, cash.id, txn.scenario_id) == before


def _leg_again(leg):
    """Return *leg* read afresh: its transfer's same side, as :func:`_legs_of` reads it."""
    expense, income = _legs_of(leg.transfer)
    return income if leg.is_income else expense


def _side_records_of(leg):
    """The transfer's list of *leg*'s side records (``*_movements``), loaded by the read."""
    if leg.is_income:
        return leg.transfer.income_movements
    return leg.transfer.expense_movements


def _movement_entries(movement_ids):
    """Return the journal entries linked to any of *movement_ids*."""
    return (
        db.session.query(JournalEntry)
        .filter(JournalEntry.transaction_entry_id.in_(movement_ids))
        .all()
    )


class TestATransferIsCoveredOnBothLegs:
    """Plan step X-bi-3c: each leg of a settled transfer holds its movement.

    The transfer settle reaches the seam through ``transfer_service``
    (Invariant 4), so both legs are covered by the ONE writer a bill and a
    paycheck are (ruling **R-BAL41**); each movement moves in its own leg's
    direction.  Each leg is read as its TRANSFER and its side's record, which
    hangs off the transfer since plan step balance:X-bi-6-4d-2 (off the side's
    shadow until then; ruling R-BAL167 classes 1 and 4, :func:`_legs_of`).
    The posted ledger books each side's movement as an entry of its own
    against the owner's transfers-in-transit account, and nothing for the
    transfer whole (ruling **R-BAL45**, built at plan step
    ``balance:X-bi-6-3``; a transfer the ledger booked before that step
    keeps its legacy whole-transfer entries beside their reversal, netting
    to zero under ``journal_entries.transfer_id`` until plan step
    ``balance:X-bi-6-5``, and this class builds none); the walk reads each
    leg's movement and nothing of the leg (ruling **R-BAL80**), and each leg
    is worth its movement (ruling **R-BAL81**), so the fold reads nothing for
    a leg without one -- the control below.  Worked on ``$500.00`` Checking
    -> Savings.
    """

    def test_each_leg_holds_one_movement_in_its_own_direction(
        self, app, seed_user, seed_periods,
    ):
        with app.app_context():
            _, expense, income = _settled_pair(seed_user, seed_periods[0])
            for leg in (expense, income):
                assert leg.status.is_settled
                movement = _only_movement(leg)
                # What moved and who wrote it are the TRANSFER's, their one
                # home since plan step balance:X-bi-6-4d-2's 4d-4 half; the
                # side's record states neither.
                assert leg.transfer.settled_figure == Decimal("500.00")
                assert leg.transfer.settled_figure_source_id == _source(
                    MovementFigureSourceEnum.RESOLVED,
                )
                assert movement.description == leg.name
                # The leg's day IS its record's (plan step balance:X-bi-6-4d-2,
                # ruling R-BAL167 class 1): dated, its purchase day that day.
                assert movement.settled_on is not None
                assert movement.purchased_on == leg.settled_on
                assert movement.account_id == leg.account_id
                # The leg is worth what its movement moves (R-BAL81).
                assert status_seam.covered_cash_leg(leg, leg.account_id) == (
                    cash_ledger.movement_cash_leg(leg, movement)
                )
            facts = {
                leg.cell_key: [
                    fact for fact in settled_cash_facts(leg.account_id, leg.scenario_id)
                    if fact.entry_id == _only_movement(leg).id
                ]
                for leg in (expense, income)
            }
            assert [fact.delta for fact in facts[expense.cell_key]] == [Decimal("-500.00")]
            assert [fact.is_income for fact in facts[expense.cell_key]] == [False]
            assert [fact.delta for fact in facts[income.cell_key]] == [Decimal("500.00")]
            assert [fact.is_income for fact in facts[income.cell_key]] == [True]

    def test_the_fold_reads_each_legs_movement_and_nothing_without_it(
        self, app, seed_user, seed_periods,
    ):
        """The movement IS the fold on the from-account and the to-account (R-BAL80).

        The control's state -- a settled transfer missing a side's record --
        is refused at COMMIT since plan step balance:X-bi-6-4d-2 (the band
        rule), so it lives inside the flush alone, as it always did here.
        """
        with app.app_context():
            _, expense, income = _settled_pair(seed_user, seed_periods[0])
            expected = {expense: Decimal("-500.00"), income: Decimal("500.00")}
            for leg, figure in expected.items():
                account_id, scenario_id = leg.account_id, leg.scenario_id
                with_movement = _per_day(settled_cash_facts(account_id, scenario_id))
                assert with_movement[leg.settled_on] == figure
                movement = _only_movement(leg)
                side_records = _side_records_of(leg)
                # Deleted, THEN out of the list: the list no longer deletes (R-CC64;
                # rule-5 re-expression, developer-confirmed 2026-09-23).  The list
                # is the transfer's side list since plan step balance:X-bi-6-4d-2,
                # the shadow's ``entries`` until then (R-BAL167 class 4).
                db.session.delete(movement)
                side_records.remove(movement)
                db.session.flush()
                db.session.expire(leg.transfer)
                leg = _leg_again(leg)
                assert status_seam.covered_cash_leg(leg, leg.account_id) == Decimal("0")
                assert _per_day(settled_cash_facts(account_id, scenario_id)) == {}

    def test_the_ledger_books_each_movement_against_transit_and_nothing_whole(
        self, app, seed_user, seed_periods,
    ):
        """Ruling R-BAL45's ENDPOINT as the ledger shows it, graded by the oracle.

        Re-expressed from ``test_the_ledger_books_the_pair_whole_and_the_
        movements_nowhere`` at plan step ``balance:X-bi-6-3`` (ruling
        **R-BAL101**), which pinned the interval that step closes.  The cash
        nets move by exactly the figure on each account, each MOVEMENT links
        its own entry (two, against the owner's Transfers-in-transit
        account), NO entry links the transfer whole, the oracle's per-account
        identity holds on both, transit nets to zero, and the deploy resync --
        which walks every settled source -- finds nothing to post.  A
        movement posted beside a whole-pair entry would read ``$1,000.00``
        against the oracle's ``$500.00`` on that account.
        """
        with app.app_context():
            # Opened BEFORE the settle day, so the pair rides on top of the
            # opening instead of being absorbed by an assertion on the same
            # day (the lifecycle suite's stated precondition for this identity).
            savings = create_savings_account(
                seed_user, db.session, "Savings", Decimal("0.00"),
                observed_on=seed_periods[0].start_date,
            )
            accounts = (seed_user["account"], savings)
            scenario_id = seed_user["scenario"].id
            before = {
                account.id: posting_service.account_posting_total(account.id, scenario_id)
                for account in accounts
            }
            xfer, expense, income = _settled_pair(
                seed_user, seed_periods[0], to_account=savings,
            )
            movement_ids = [_only_movement(leg).id for leg in (expense, income)]
            after = {
                account.id: posting_service.account_posting_total(account.id, scenario_id)
                for account in accounts
            }
            assert after[accounts[0].id] - before[accounts[0].id] == Decimal("-500.00")
            assert after[savings.id] - before[savings.id] == Decimal("500.00")
            # ONE entry per MOVEMENT, and none linked by the transfer whole.
            pair_entries = (
                db.session.query(JournalEntry).filter_by(transfer_id=xfer.id).all()
            )
            assert pair_entries == []
            assert len(_movement_entries(movement_ids)) == 2
            transit = transit_ledger_account(db.session, seed_user["user"].id)
            assert db.session.query(
                db.func.coalesce(db.func.sum(Posting.amount), 0)
            ).filter(Posting.ledger_account_id == transit.id).scalar() == 0
            for account in accounts:
                opening = Decimal(str(cash_ledger.resolve_anchor(account).balance))
                assert posting_service.account_posting_total(account.id, scenario_id) == (
                    opening
                    + posting_service.settled_transfer_effect(account.id, scenario_id)
                    + posting_service.posted_purchase_effect(account.id, scenario_id)
                )
            db.session.commit()
            assert posting_service.resync_all_cash_postings() == (0, 0)
            assert len(_movement_entries(movement_ids)) == 2

    def test_a_revert_un_dates_both_movements_and_the_ledger_reverses_the_pair(
        self, app, seed_user, seed_periods,
    ):
        with app.app_context():
            savings = create_savings_account(
                seed_user, db.session, "Savings", Decimal("0.00"),
                observed_on=seed_periods[0].start_date,
            )
            account_ids = (seed_user["account"].id, savings.id)
            scenario_id = seed_user["scenario"].id
            before = {
                account_id: posting_service.account_posting_total(account_id, scenario_id)
                for account_id in account_ids
            }
            xfer, expense, income = _settled_pair(
                seed_user, seed_periods[0], to_account=savings,
            )
            movement_ids = [_only_movement(leg).id for leg in (expense, income)]
            assert posting_service.account_posting_total(savings.id, scenario_id) != before[savings.id]
            transfer_service.update_transfer(
                xfer.id, seed_user["user"].id,
                status_id=ref_cache.status_id(StatusEnum.PROJECTED),
            )
            db.session.flush()
            # The transfer says Projected and each side's record is kept
            # un-dated (R-BAL167 class 1, plan step balance:X-bi-6-4d-2); the
            # figure the records moved is kept on the TRANSFER, its one home
            # since that step's 4d-4 half.
            for leg, movement_id in zip(_legs_of(xfer), movement_ids):
                assert not leg.status.is_settled
                survivor = _only_movement(leg)
                assert survivor.id == movement_id
                assert survivor.settled_on is None
                assert survivor.reconciled_by_id is None
                assert leg.transfer.settled_figure == Decimal("500.00")
            assert {
                account_id: posting_service.account_posting_total(account_id, scenario_id)
                for account_id in account_ids
            } == before

    def test_the_reconcile_ticks_link_reaches_the_leg_and_its_movement_alone(
        self, app, seed_user, seed_periods,
    ):
        """``transfer_service.record_leg_clearing`` links the leg's record alone.

        The door delegates to the status seam since X-bi-3c; with the
        one-column write it had, the movement's fact walked unlinked (the gap
        3a closed for bills).  Per leg, still: the sibling on the other
        account and its record take nothing.  It linked the leg's shadow AND
        that shadow's movement (``record_clearing(shadow)``) until plan step
        balance:X-bi-6-4d-2, which made the side's record the one home of the
        link (ruling R-BAL167 class 1).
        """
        with app.app_context():
            _, expense, income = _settled_pair(seed_user, seed_periods[0])
            anchor = _latest_anchor(expense.account_id)
            transfer_service.record_leg_clearing(expense, anchor.id)
            db.session.flush()
            expense, income = _legs_of(expense.transfer)
            assert _only_movement(expense).reconciled_by_id == anchor.id
            assert _only_movement(income).reconciled_by_id is None

    def test_a_settle_day_correction_moves_both_movements(
        self, app, seed_user, seed_periods,
    ):
        with app.app_context():
            xfer, expense, _ = _settled_pair(seed_user, seed_periods[0])
            corrected = expense.settled_on - timedelta(days=3)
            transfer_service.update_transfer(
                xfer.id, seed_user["user"].id,
                side_days=on_both_sides(
                    xfer.from_account_id, xfer.to_account_id,
                    SettleDay(day=corrected, basis=SettledDayBasisEnum.ENTERED),
                ),
            )
            db.session.flush()
            # Each leg's day is its record's (R-BAL167 class 1, plan step
            # balance:X-bi-6-4d-2), where it was its shadow's.
            for leg in _legs_of(xfer):
                assert leg.settled_on == corrected
                movement = _only_movement(leg)
                assert movement.settled_on == corrected
                assert movement.purchased_on == corrected


class TestATransfersMovementsFollowItsLifecycle:
    """Soft delete, restore, hard delete and the endpoint move (R-BAL46)."""

    def test_a_soft_deleted_transfer_keeps_its_movements_worth_nothing_and_restore_revives_them(
        self, app, seed_user, seed_periods,
    ):
        """A soft delete takes both $500.00 movements off; a restore brings back a plan.

        It kept both movements worth nothing while hidden and revived them on
        the restore until plan step ``balance:X-bi-6-4d-2``, whose soft
        delete takes both sides' records off the books (ruling
        **credit_card:R-CC75**) and sets the Paid transfer back to Projected
        (ruling **R-BAL246**, "Back as a plan, now", developer 2026-10-09).
        So the restore returns a plan holding no movement, and neither
        account reads a settled fact either side of it.  The name is kept as
        history.
        """
        with app.app_context():
            xfer, expense, income = _settled_pair(seed_user, seed_periods[0])
            movement_ids = {leg.cell_key: _only_movement(leg).id for leg in (expense, income)}
            user_id = seed_user["user"].id
            transfer_service.delete_transfer(xfer.id, user_id, soft=True)
            db.session.flush()
            for leg in (expense, income):
                assert db.session.get(TransactionEntry, movement_ids[leg.cell_key]) is None
                assert settled_cash_facts(leg.account_id, leg.scenario_id) == []
            transfer_service.restore_transfer(xfer.id, user_id)
            db.session.flush()
            assert xfer.status_id == ref_cache.status_id(StatusEnum.PROJECTED)
            for leg in _legs_of(xfer):
                assert leg.record is None
                assert settled_cash_facts(leg.account_id, leg.scenario_id) == []

    def test_a_reverted_then_soft_deleted_then_restored_transfer_keeps_both_survivors(
        self, app, seed_user, seed_periods,
    ):
        """A reverted transfer's kept un-dated records leave with its delete.

        Each side is read as its transfer and its record since plan step
        balance:X-bi-6-4d-2, its shadow row until then (ruling R-BAL167
        classes 1 and 4).  A revert keeps each side's record un-dated (ruling
        R-BAL61), and the restore's identity pass kept both survivors (same
        ids, un-dated, same labels) until that step's soft delete took every
        record either side holds off the books (ruling
        **credit_card:R-CC75**).  The transfer was Projected already, so it
        stays Projected (ruling **R-BAL246** sets back only a settled one) and
        the restore returns a plan holding no record.  The name is kept as
        history.
        """
        with app.app_context():
            xfer, _, _ = _settled_pair(seed_user, seed_periods[0])
            user_id = seed_user["user"].id
            transfer_service.update_transfer(
                xfer.id, user_id,
                status_id=ref_cache.status_id(StatusEnum.PROJECTED),
            )
            db.session.flush()
            movement_ids = {leg.cell_key: _only_movement(leg).id for leg in _legs_of(xfer)}
            transfer_service.delete_transfer(xfer.id, user_id, soft=True)
            db.session.flush()
            for movement_id in movement_ids.values():
                assert db.session.get(TransactionEntry, movement_id) is None
            transfer_service.restore_transfer(xfer.id, user_id)
            db.session.flush()
            for leg in _legs_of(xfer):
                assert not leg.status.is_settled
                assert leg.record is None

    def test_a_hard_deleted_transfer_takes_its_movements_with_it(
        self, app, seed_user, seed_periods,
    ):
        with app.app_context():
            xfer, expense, income = _settled_pair(seed_user, seed_periods[0])
            movement_ids = [_only_movement(leg).id for leg in (expense, income)]
            db.session.commit()
            transfer_service.delete_transfer(xfer.id, seed_user["user"].id, soft=False)
            db.session.commit()
            for movement_id in movement_ids:
                assert db.session.get(TransactionEntry, movement_id) is None
            assert _movement_entries(movement_ids) == []

    def test_an_endpoint_move_carries_the_movements_to_the_new_account(
        self, app, seed_user, seed_periods,
    ):
        """Ruling R-BAL46: a leg's movement moves with its leg, by the applier.

        Since plan step ``credit_card:CC-5-1`` the applier's own assignment is
        the ONE writer of the move (``_endpoints._apply_endpoint_move``): the
        composite key whose ``ON UPDATE CASCADE`` used to move the row beneath
        it, ``fk_transaction_entries_parent_account``, is dropped (ruling
        R-BAL76), so a movement's account is its own and only the applier
        re-points a leg's.  Both the session and the database are read here:
        the in-session object before any expire, the row after, and the walks
        of the vacated and the new account.  Delete the assignment and the
        movement stays on the vacated account in all three.  The leg is read
        as its transfer's side and that side's record since plan step
        balance:X-bi-6-4d-2 (its shadow row until then; ruling R-BAL167
        class 4), read again after the move.
        """
        with app.app_context():
            savings = create_savings_account(
                seed_user, db.session, "Savings", Decimal("0.00"),
            )
            other = create_savings_account(
                seed_user, db.session, "Other Savings", Decimal("0.00"),
            )
            xfer, expense, income = _settled_pair(
                seed_user, seed_periods[0], to_account=savings,
            )
            movement = _only_movement(income)
            transfer_service.update_transfer(
                xfer.id, seed_user["user"].id, to_account_id=other.id,
            )
            db.session.flush()
            expense, income = _legs_of(xfer)
            assert income.account_id == other.id
            assert movement.account_id == other.id
            db.session.expire_all()
            assert db.session.get(TransactionEntry, movement.id).account_id == other.id
            assert _per_day(settled_cash_facts(other.id, income.scenario_id)) == {
                income.settled_on: Decimal("500.00"),
            }
            assert settled_cash_facts(savings.id, income.scenario_id) == []
            assert _only_movement(expense).account_id == seed_user["account"].id


class TestALoanPaymentReStatedAtZeroMovesTheLoansPositionAndNotItsPostedLedger:
    """The loan replay prices a payment by its RECORD: the transfer's settled figure.

    A payment's income leg sits on the LOAN account and carries a covering
    movement there; every loan reader values the leg by its record
    (``row_valuation.leg_settled_contribution``; from plan step
    ``balance:X-bi-4b-1`` the record was the leg's movement, ruling
    **R-BAL80**, which hangs off the transfer by its side link since
    ``balance:X-bi-6-4d-2``, not off the leg's shadow) -- and since that
    step's 4d-4 half what the loan side reads of the payment is the
    transfer's settled figure, which the movement does not hold.
    Through ``X-bi-4a`` the reader took the leg's own ``settled_amount`` and
    this class pinned the movement as INERT to it ("the seam's balance and
    the posted balance read identically with and without the movement");
    that was the interval ruling R-BAL40 accepted, and the pin is inverted
    with it: re-stating the payment as a close of nothing (a write around
    the doors: a ``$0.00`` ``figure`` through
    ``transfer_service.update_transfer`` records the close too, but reverses
    what the records posted) now reads it as nothing moved, and moves the
    loan's position with
    it.  The posted ledger does not move: the re-statement reaches no posting
    door, and the loan side's entry against transit (one of ruling R-BAL45's
    two) keeps its postings.
    Since plan step ``balance:X-bi-6-4d-2``'s 4d-4 half the figure
    the record states lives on the TRANSFER (rulings **R-BAL365**,
    **R-BAL366**), so the re-statement writes it there: ``0.00``, with both
    sides' records off, the one storable shape of a close of nothing.  Until
    then this took the records away and the reader inferred ``$0.00`` from
    the gap, the inference ruling **R-BAL368** deletes.

    What the case grades is that ``$0.00`` close, not that the reader reads
    the figure: the re-statement takes the records off and zeroes the figure
    together, so a reader answering ``0.00`` for a leg holding no record
    passes it too (the 4d-4b review measured that mutant passing).  The
    class was named
    ``TestALoanPaymentsLoanSideMovementIsItsRecord`` and the case
    ``test_the_loan_side_reads_the_transfers_figure_as_the_payment`` until
    that review.
    """

    def test_a_zero_close_is_worth_nothing_to_the_loan_and_moves_its_position_not_its_posted_ledger(
        self, app, seed_user, seed_periods,
    ):
        with app.app_context():
            loan = create_loan_account(seed_user, db.session)
            db.session.flush()
            xfer, _, income = _settled_pair(
                seed_user, seed_periods[0], to_account=loan, amount="1910.95",
            )
            db.session.commit()
            user_id, scenario_id = seed_user["user"].id, seed_user["scenario"].id
            as_of = income.settled_on
            movement = _only_movement(income)
            assert movement.account_id == loan.id

            def _read():
                ctx = balance_at.BalanceContext.build(user_id, as_of=as_of)
                return (
                    balance_at.positions(loan, ctx, [as_of])[as_of],
                    posted_loan_balance_at(loan.id, scenario_id, as_of),
                )

            assert row_valuation.leg_settled_contribution(income) == Decimal("1910.95")
            with_movement = _read()
            # Deleted, THEN out of the list: the list no longer deletes (R-CC64;
            # rule-5 re-expression, developer-confirmed 2026-09-23).  The list
            # is the transfer's side list since plan step balance:X-bi-6-4d-2.
            # That step's band rule refuses COMMITTING a settled transfer that
            # holds one side's record, so BOTH sides' records come off, and
            # the transfer's figure becomes 0.00 (ruling R-BAL365): the
            # $0.00 close the rule admits, the storable shape of a loan side
            # holding none (ruling R-BAL167 class 2).
            for leg in _legs_of(xfer):
                record = _only_movement(leg)
                side_records = _side_records_of(leg)
                db.session.delete(record)
                side_records.remove(record)
            db.session.flush()
            db.session.execute(
                text(
                    "UPDATE budget.transfers SET settled_figure = 0 "
                    "WHERE id = :t"
                ),
                {"t": xfer.id},
            )
            db.session.commit()
            db.session.expire_all()
            assert xfer.status.is_settled
            # The record is the transfer's figure: at 0.00 the leg records
            # $0.00 (R-BAL82's close of nothing), the loan's position moves,
            # and the posted ledger does not move: the loan side's entry
            # against transit, one of R-BAL45's two, keeps its postings.
            income = _leg_again(income)
            assert row_valuation.leg_settled_contribution(income) == Decimal("0")
            without_movement = _read()
            assert without_movement[0] != with_movement[0]
            assert without_movement[1] == with_movement[1]
