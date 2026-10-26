"""A row's covering movement through its settle and revert: the write, the balance it is, the
un-date and re-date, the seam's own guarantee, the row's counter and the zero settlement.
"""

from datetime import timedelta
from decimal import Decimal

from app import ref_cache
from app.enums import MovementFigureSourceEnum, SettledDayBasisEnum, StatusEnum
from app.extensions import db
from app.models.transaction_entry import TransactionEntry
from app.services import entry_service, row_valuation, status_seam, transaction_service
from app.services.cash_ledger import settled_cash_facts
from app.services.cash_ledger._amounts import _entry_aware_amount
from app.services.entry_service import EntryDetails
from app.services.settle_day import SettleDay
from app.services.transaction_service._row_rules import settles_from_entries
from app.services.transaction_service._settle import settle_from_entries
from tests._test_helpers import (
    create_savings_account,
    ledger_net,
    linked_ledger_account,
    planted_basis,
    state_template_price,
    typed,
)

from ._helpers import _source, _bill, _settle, _revert, _only_movement, _per_day, _latest_anchor


class TestASettleWritesTheMovementItRecords:
    """The write, column by column."""

    def test_a_ticked_bill_holds_one_resolved_movement(
        self, app, seed_user, seed_periods,
    ):
        with app.app_context():
            txn = _bill(seed_user, seed_periods[0])
            _settle(txn)
            db.session.flush()

            movement = _only_movement(txn)
            assert movement.amount == Decimal("148.32")
            assert movement.figure_source_id == _source(
                MovementFigureSourceEnum.RESOLVED,
            )
            assert movement.description == txn.name
            assert movement.purchased_on == txn.settled_on
            assert movement.settled_on == txn.settled_on
            assert movement.settled_day_basis_id == txn.settled_day_basis_id
            assert movement.reconciled_by_id is None
            assert movement.account_id == txn.account_id
            assert movement.user_id == txn.user_id
            assert movement.is_credit is False
            # The movement IS the row's record (plan step X-bi-4b-2).
            assert status_seam.recorded_settlement(txn) == status_seam.Settlement(
                Decimal("148.32"), MovementFigureSourceEnum.RESOLVED,
            )

    def test_a_typed_correction_is_a_typed_movement(
        self, app, seed_user, seed_periods,
    ):
        with app.app_context():
            txn = _bill(seed_user, seed_periods[0])
            _settle(txn, submitted=typed(Decimal("150.00")))
            db.session.flush()

            movement = _only_movement(txn)
            assert movement.amount == Decimal("150.00")
            assert movement.figure_source_id == _source(
                MovementFigureSourceEnum.TYPED,
            )

    def test_a_callers_settle_day_is_the_movements_day(
        self, app, seed_user, seed_periods,
    ):
        """The reconcile panel's shape: an asserted day, stated by the caller."""
        with app.app_context():
            txn = _bill(seed_user, seed_periods[0])
            day = SettleDay(
                day=seed_periods[0].start_date,
                basis=SettledDayBasisEnum.ASSERTED,
            )
            _settle(txn, settle_day=day)
            db.session.flush()

            movement = _only_movement(txn)
            assert movement.settled_on == seed_periods[0].start_date
            assert movement.purchased_on == seed_periods[0].start_date
            assert movement.settled_day_basis_id == ref_cache.settled_day_basis_id(
                SettledDayBasisEnum.ASSERTED,
            )


class TestTheMovementIsTheBalance:
    """The fold reads the movement and nothing of the row (ruling R-BAL80).

    Through plan step ``X-bi-3e`` this class graded ruling R-FM's identity --
    the fold's per-day sums were IDENTICAL with and without the movement,
    because the row's own leg booked whatever the movement did not.  Since
    ``balance:X-bi-4a`` the row's leg is not a fact, and under ruling
    **R-BAL81** it is not a price either: the row is WORTH what its covering
    movement moves (``status_seam.covered_cash_leg``, the matcher's one
    valuation of a settled row).  Delete the movement around the seam and
    the fold reads NOTHING for the row and the matcher prices it at
    nothing -- the same answer from both readers, stated by the movement.
    The inverse of the identity, and the control that would fail if a
    row-leg fact came back.  (Through X-bi-4a's first cut the control
    asserted the deleted ``settled_cash_leg`` still priced the whole bill
    without its movement -- the row-leg reader R-BAL81 deleted.)
    """

    def test_the_fold_reads_the_movement_and_nothing_without_it(
        self, app, seed_user, seed_periods,
    ):
        with app.app_context():
            txn = _bill(seed_user, seed_periods[0])
            _settle(txn)
            db.session.flush()
            account_id, scenario_id = txn.account_id, txn.scenario_id

            # The row is worth its covering movement (R-BAL81) and the fold
            # reads that movement.
            assert status_seam.covered_cash_leg(txn, txn.account_id) == Decimal("-148.32")
            with_movement = _per_day(settled_cash_facts(account_id, scenario_id))
            assert with_movement[txn.settled_on] == Decimal("-148.32")

            # The CONTROL: delete the movement around the seam.  The row is
            # worth nothing to the matcher and the fold reads none of it.
            movement = _only_movement(txn)
            # Deleted, THEN out of the list: the list no longer deletes (R-CC64;
            # rule-5 re-expression, developer-confirmed 2026-09-23).
            db.session.delete(movement)
            txn.entries.remove(movement)
            db.session.flush()
            db.session.expire(txn)
            assert status_seam.covered_cash_leg(txn, txn.account_id) == Decimal("0")
            without = _per_day(settled_cash_facts(account_id, scenario_id))
            assert txn.settled_on not in without
            assert without == {}

    def test_the_posted_ledgers_cash_net_moves_by_the_figure(
        self, app, seed_user, seed_periods,
    ):
        """The verb's ledger reconcile posts the family in one pass.

        Measured as a DELTA: the fixture's own opening correction already
        sits on the same cash ledger.
        """
        with app.app_context():
            txn = _bill(seed_user, seed_periods[0])
            cash = linked_ledger_account(db.session, txn.account_id)
            before = ledger_net(db.session, cash.id, txn.scenario_id)
            _settle(txn)
            db.session.flush()
            after = ledger_net(db.session, cash.id, txn.scenario_id)
            assert after - before == Decimal("-148.32")


class TestARevertUnDatesAndAReSettleReDates:
    """The movement mirrors the row's record: un-dated with the band, re-dated from it.

    Plan step **X-bi-3e-2**, ruling **R-BAL61**: a revert releases the row's
    assertion and KEEPS what moved, and the movement -- the record's mirror
    and the only home of who wrote the figure -- follows the record rather
    than the band.  Every revert here goes through the door production uses
    (``transaction_service.apply_requested_status``), which runs the family
    reconcile after the seam; the seam's own half is graded alone in
    :class:`TestTheBareSeamUnDatesAndKeeps`.
    """

    def test_a_revert_un_dates_a_resolved_movement_and_the_door_reverses_its_legs(
        self, app, seed_user, seed_periods,
    ):
        with app.app_context():
            txn = _bill(seed_user, seed_periods[0])
            cash = linked_ledger_account(db.session, txn.account_id)
            before = ledger_net(db.session, cash.id, txn.scenario_id)
            _settle(txn)
            db.session.flush()
            movement = _only_movement(txn)
            movement_id = movement.id
            assert movement.reconciled_by_id is None
            assert ledger_net(db.session, cash.id, txn.scenario_id) - before == Decimal("-148.32")

            _revert(txn)
            db.session.flush()

            survivor = _only_movement(txn)
            assert survivor.id == movement_id
            assert db.session.get(TransactionEntry, movement_id) is survivor
            assert survivor.settled_on is None
            assert survivor.settled_day_basis_id is None
            assert survivor.reconciled_by_id is None
            assert survivor.amount == Decimal("148.32")
            assert survivor.figure_source_id == _source(MovementFigureSourceEnum.RESOLVED)
            assert txn.purchases == []
            # The door's family reconcile reversed the un-dated movement's
            # legs (it posts nothing without a day); the parent's zero leg had
            # nothing to reverse.  Net: back to before.
            assert ledger_net(db.session, cash.id, txn.scenario_id) == before

    def test_a_revert_un_dates_a_typed_movement_and_both_homes_retain_the_figure(
        self, app, seed_user, seed_periods,
    ):
        """The movement stays; what moved stays where X-au-c3 keeps it, on both."""
        with app.app_context():
            txn = _bill(seed_user, seed_periods[0])
            _settle(txn, submitted=typed(Decimal("150.00")))
            db.session.flush()
            movement_id = _only_movement(txn).id

            _revert(txn)
            db.session.flush()

            survivor = _only_movement(txn)
            assert survivor.id == movement_id
            assert survivor.settled_on is None
            assert survivor.amount == Decimal("150.00")
            assert survivor.figure_source_id == _source(MovementFigureSourceEnum.TYPED)
            # The retained read takes the figure and the source off the survivor.
            retained = status_seam.recorded_settlement(txn)
            assert retained.amount == Decimal("150.00")
            assert retained.source is MovementFigureSourceEnum.TYPED

    def test_a_revert_releases_the_movements_clearing_link_with_the_rows(
        self, app, seed_user, seed_periods,
    ):
        """A link says a statement showed money that moved; un-dated, it cannot stand."""
        with app.app_context():
            txn = _bill(seed_user, seed_periods[0])
            _settle(txn)
            db.session.flush()
            anchor = _latest_anchor(txn.account_id)
            status_seam.record_clearing(txn, anchor.id)
            db.session.flush()
            assert _only_movement(txn).reconciled_by_id == anchor.id

            _revert(txn)
            db.session.flush()

            assert txn.reconciled_by_id is None
            assert _only_movement(txn).reconciled_by_id is None

    def test_a_reverted_bill_is_worth_its_estimate_as_before(
        self, app, seed_user, seed_periods,
    ):
        """The projection reads the plan again: the survivor is no purchase, no split."""
        with app.app_context():
            txn = _bill(seed_user, seed_periods[0])
            _settle(txn, submitted=typed(Decimal("150.00")))
            db.session.flush()
            _revert(txn)
            db.session.flush()
            db.session.expire(txn)
            assert len(txn.entries) == 1, "the survivor is on the row"
            assert _entry_aware_amount(txn, planted_basis(txn)) == Decimal("148.32")

    def test_a_re_settle_re_dates_the_same_typed_movement_from_the_retained_record(
        self, app, seed_user, seed_periods,
    ):
        """Revert, re-settle with nothing typed: the honoured figure, on the same row."""
        with app.app_context():
            txn = _bill(seed_user, seed_periods[0])
            _settle(txn, submitted=typed(Decimal("150.00")))
            db.session.flush()
            first_id = _only_movement(txn).id
            _revert(txn)
            db.session.flush()

            booked_a_human_figure = _settle(txn)
            db.session.flush()

            movement = _only_movement(txn)
            assert movement.id == first_id
            assert movement.amount == Decimal("150.00")
            assert movement.figure_source_id == _source(MovementFigureSourceEnum.TYPED)
            assert movement.settled_on == txn.settled_on
            assert movement.settled_on is not None
            assert movement.purchased_on == txn.settled_on
            assert movement.settled_day_basis_id == txn.settled_day_basis_id
            assert booked_a_human_figure is False

    def test_a_re_settle_keeps_the_kept_movement_where_the_money_moved(
        self, app, seed_user, seed_periods,
    ):
        """Revert, move the row's account, re-settle: the record stays where it was.

        Plan step ``credit_card:CC-5-3``, ruling **R-CC42** (developer
        2026-09-21), which AMENDS ruling **R-CC36**'s re-point clause -- and
        this test, which pinned that clause as
        ``test_a_re_settle_re_points_the_kept_movement_onto_the_rows_account``
        under CC-5-2, is RE-EXPRESSED under CLAUDE.md rule 5 with the
        developer's confirmation, his option text as picked: *"Retained across
        a revert (Recommended)": "From scratch: where the money moved is part
        of the record and survives a revert exactly as a stated figure does.
        A re-settle that names no account keeps the kept payment's account;
        naming one re-points it; every door reads the record: the popover's
        'Paid from' shows the kept account selected, and the one-click
        checkmark and mobile Mark Paid pass nothing, so the seam keeps it.
        One default, spelled once in the seam. AMENDS R-CC36's re-point
        clause: a Projected row whose definition moved keeps its kept record
        where the money moved, and only a named tender moves it (that test is
        re-expressed to say so)."*

        The maintain pass no longer retains a row holding a kept payment
        record when its definition's account moves (R-CC36's clause that
        STANDS), so the row can sit on Second Checking while its un-dated
        record still names Checking.  Under R-CC42 the same columns cannot
        tell that row from a bill the owner charged to the card and reverted
        to edit, and the one-click re-settle of the latter must not move its
        money back onto checking -- so a re-settle naming no tender keeps the
        SAME movement (its id survives, as ``X-bi-3e-2`` pinned) on Checking,
        and the fold reads `-148.32` there and nothing on Second Checking;
        a re-settle NAMING Second Checking is what moves it.  The row's
        account is moved directly here: the maintain pass is the door that
        moves it in production, and its own suite pins that it now does.
        """
        with app.app_context():
            txn = _bill(seed_user, seed_periods[0])
            _settle(txn)
            db.session.flush()
            first_id = _only_movement(txn).id
            old_account_id, scenario_id = txn.account_id, txn.scenario_id
            _revert(txn)
            db.session.flush()
            kept = _only_movement(txn)
            assert kept.settled_on is None
            assert kept.account_id == old_account_id

            moved_to = create_savings_account(
                seed_user, db.session, "Second Checking", Decimal("0.00"),
            )
            db.session.flush()
            txn.account_id = moved_to.id
            db.session.flush()

            _settle(txn)
            db.session.flush()

            movement = _only_movement(txn)
            assert movement.id == first_id
            assert movement.account_id == old_account_id
            assert movement.settled_on == txn.settled_on
            on_the_new = _per_day(settled_cash_facts(moved_to.id, scenario_id))
            on_the_old = _per_day(settled_cash_facts(old_account_id, scenario_id))
            assert on_the_old[txn.settled_on] == Decimal("-148.32")
            assert txn.settled_on not in on_the_new

            # Only a NAMED tender moves it: revert and re-settle naming the
            # row's new account, and the same movement books there.
            _revert(txn)
            db.session.flush()
            transaction_service.settle_transaction(
                txn, tender_account_id=moved_to.id,
            )
            db.session.flush()
            movement = _only_movement(txn)
            assert movement.id == first_id
            assert movement.account_id == moved_to.id
            on_the_new = _per_day(settled_cash_facts(moved_to.id, scenario_id))
            on_the_old = _per_day(settled_cash_facts(old_account_id, scenario_id))
            assert on_the_new[txn.settled_on] == Decimal("-148.32")
            assert txn.settled_on not in on_the_old

    def test_a_re_settle_after_a_resolved_revert_re_prices_the_same_movement(
        self, app, seed_user, seed_periods,
    ):
        """The plan moved meanwhile: the survivor takes the new price, keeps its id."""
        with app.app_context():
            txn = _bill(seed_user, seed_periods[0])
            _settle(txn)
            db.session.flush()
            first_id = _only_movement(txn).id
            _revert(txn)
            db.session.flush()
            # The definition is re-priced; a derived row is worth its plan.
            state_template_price(txn.template, Decimal("160.00"))
            db.session.flush()

            _settle(txn)
            db.session.flush()

            movement = _only_movement(txn)
            assert movement.id == first_id
            assert movement.amount == Decimal("160.00")
            assert row_valuation.settled_figure(txn) == Decimal("160.00")
            assert movement.figure_source_id == _source(
                MovementFigureSourceEnum.RESOLVED,
            )
            assert movement.settled_on == txn.settled_on

    def test_a_re_settle_on_a_later_day_moves_the_survivors_purchase_day(
        self, app, seed_user, seed_periods,
    ):
        """A covering movement's purchase day IS its settle day (R-BAL39), re-dated too."""
        with app.app_context():
            txn = _bill(seed_user, seed_periods[0])
            first_day = seed_periods[0].start_date
            _settle(txn, settle_day=SettleDay(
                day=first_day, basis=SettledDayBasisEnum.ENTERED,
            ))
            db.session.flush()
            assert _only_movement(txn).purchased_on == first_day
            _revert(txn)
            db.session.flush()
            assert _only_movement(txn).purchased_on == first_day, (
                "un-dated, the survivor keeps the day of the close it recorded"
            )

            later = first_day + timedelta(days=3)
            _settle(txn, settle_day=SettleDay(
                day=later, basis=SettledDayBasisEnum.ENTERED,
            ))
            db.session.flush()

            movement = _only_movement(txn)
            assert movement.purchased_on == later
            assert movement.settled_on == later

    def test_an_envelope_closed_empty_then_given_purchases_sums_the_purchases(
        self, app, seed_user, seed_periods,
    ):
        """The stale-close control: the survivor is no purchase, and the purchases record withdraws it."""
        with app.app_context():
            envelope = _bill(seed_user, seed_periods[0], "100.00", is_envelope=True)
            _settle(envelope)
            db.session.flush()
            close = _only_movement(envelope)
            assert close.amount == Decimal("100.00")
            assert close.figure_source_id == _source(
                MovementFigureSourceEnum.RESOLVED,
            )

            _revert(envelope)
            db.session.flush()
            assert [entry.id for entry in envelope.entries] == [close.id]
            assert envelope.purchases == []
            assert settles_from_entries(envelope) is False

            entry_service.create_entry(
                envelope.id, seed_user["user"].id,
                EntryDetails(
                    figure=typed(Decimal("60.00")), description="Kroger",
                    purchased_on=seed_periods[0].start_date,
                ),
            )
            db.session.flush()
            assert [entry.amount for entry in envelope.purchases] == [Decimal("60.00")]
            assert settles_from_entries(envelope) is True
            _settle(envelope)
            db.session.flush()

            assert status_seam.recorded_settlement(envelope) == status_seam.Settlement(
                None, None,
            )
            assert sum(e.amount for e in envelope.entries) == Decimal("60.00")
            assert envelope.covering_movements == []
            assert db.session.get(TransactionEntry, close.id) is None

    def test_an_envelope_with_purchases_holds_no_covering_movement(
        self, app, seed_user, seed_periods,
    ):
        with app.app_context():
            envelope = _bill(seed_user, seed_periods[0], "100.00", is_envelope=True)
            entry_service.create_entry(
                envelope.id, seed_user["user"].id,
                EntryDetails(
                    figure=typed(Decimal("60.00")), description="Kroger",
                    purchased_on=seed_periods[0].start_date,
                ),
            )
            db.session.flush()
            settle_from_entries(envelope)
            db.session.flush()
            assert len(envelope.entries) == 1
            assert envelope.covering_movements == []


class TestTheBareSeamUnDatesAndKeeps:
    """What the SEAM alone guarantees on a revert, apart from any door.

    ``status_seam.apply_status_change`` un-dates the movement and keeps it;
    the ledger is the door's (``_covering``'s module docstring), so this
    class asserts nothing about postings -- ``test_integrity_check``'s DC-10
    case is where the state a caller of the bare seam would leave is graded.
    """

    def test_the_seam_alone_un_dates_the_movement_and_keeps_it(
        self, app, seed_user, seed_periods,
    ):
        with app.app_context():
            txn = _bill(seed_user, seed_periods[0])
            _settle(txn, submitted=typed(Decimal("150.00")))
            db.session.flush()
            movement_id = _only_movement(txn).id

            status_seam.apply_status_change(
                txn, ref_cache.status_id(StatusEnum.PROJECTED),
            )
            db.session.flush()

            survivor = _only_movement(txn)
            assert survivor.id == movement_id
            assert survivor.settled_on is None
            assert survivor.settled_day_basis_id is None
            assert survivor.reconciled_by_id is None
            assert survivor.amount == Decimal("150.00")
            assert survivor.figure_source_id == _source(MovementFigureSourceEnum.TYPED)


class TestARecordThatMovesMarksItsRow:
    """``_record_moved``: the row's counter follows its record, and only a change.

    Plan step ``balance:X-bi-4b-2``.  The settled figure lives on the
    covering movement alone, so a figure correction writes this table and
    nothing of the row; the row's optimistic-lock counter is what a stale
    popover is caught by (``version_id``), and through ``X-bi-4b-1`` the
    seam's write of the row's own figure columns moved it for free.  The
    covering writer now marks the ROW modified whenever its record nets a
    change -- and not otherwise, because a mark that moved for an identical
    re-record would turn every second tab into a spurious 409.  Graded at
    the seam, on the session's own dirty state, with the SECOND identical
    write as its own case: a producer right the first time can be wrong on
    the repeat.  The route-tier controls are the two
    ``test_a_stale_tab_cannot_overwrite_a_figure_correction`` cases
    (``test_full_edit_settle_door``, ``test_transfers.test_actual_box``).
    """

    @staticmethod
    def _re_record(txn, amount):
        """Hand the seam an identity re-settle stating *amount* as typed."""
        status_seam.apply_status_change(
            txn, txn.status_id,
            settlement=status_seam.Settlement(
                Decimal(amount), MovementFigureSourceEnum.TYPED,
            ),
        )

    def test_a_re_record_that_changes_the_figure_marks_the_row(
        self, app, seed_user, seed_periods,
    ):
        with app.app_context():
            txn = _bill(seed_user, seed_periods[0])
            _settle(txn)
            db.session.flush()
            version = txn.version_id
            assert not db.session.is_modified(txn)

            self._re_record(txn, "150.00")

            assert db.session.is_modified(txn), (
                "the record moved and the row stayed clean"
            )
            db.session.flush()
            assert txn.version_id == version + 1
            assert row_valuation.settled_figure(txn) == Decimal("150.00")

    def test_an_identical_second_re_record_marks_nothing(
        self, app, seed_user, seed_periods,
    ):
        """The second time: the same figure and source again nets no change."""
        with app.app_context():
            txn = _bill(seed_user, seed_periods[0])
            _settle(txn)
            db.session.flush()
            self._re_record(txn, "150.00")
            db.session.flush()
            version = txn.version_id
            assert not db.session.is_modified(txn)

            self._re_record(txn, "150.00")

            assert not db.session.is_modified(txn), (
                "an identical re-record marked the row"
            )
            db.session.flush()
            assert txn.version_id == version

    def test_a_withdrawal_marks_the_row(self, app, seed_user, seed_periods):
        """A typed ``$0.00`` over a covered bill deletes the movement: a change."""
        with app.app_context():
            txn = _bill(seed_user, seed_periods[0])
            _settle(txn)
            db.session.flush()
            version = txn.version_id
            assert not db.session.is_modified(txn)

            self._re_record(txn, "0.00")

            assert db.session.is_modified(txn)
            db.session.flush()
            assert txn.version_id == version + 1
            assert txn.covering_movements == []


class TestAZeroSettlementWritesNoMovement:
    """Zero movements is a legal count; a movement of nothing is not one."""

    def test_a_bill_budgeted_at_zero_settles_with_no_movement(
        self, app, seed_user, seed_periods,
    ):
        """The reachable case: Mark Paid on a `$0.00` bill.

        ``ck_transaction_entries_positive_amount`` is ``<> 0``, so a mirror
        written at zero was an IntegrityError where yesterday it was a 200
        (adversarial review, 2026-09-16).  R-BAL40's cutover arm, applied to
        the go-forward writer.
        """
        with app.app_context():
            txn = _bill(seed_user, seed_periods[0], "0.00")
            _settle(txn)
            db.session.flush()
            assert txn.status.is_settled
            assert row_valuation.settled_figure(txn) == Decimal("0.00")
            assert txn.covering_movements == []

    def test_a_typed_zero_over_a_covered_bill_withdraws_the_movement(
        self, app, seed_user, seed_periods,
    ):
        with app.app_context():
            txn = _bill(seed_user, seed_periods[0])
            _settle(txn)
            db.session.flush()
            movement_id = _only_movement(txn).id
            status_seam.apply_status_change(
                txn, txn.status_id,
                settlement=status_seam.Settlement.from_settle(
                    Decimal("148.32"), typed(Decimal("0.00")),
                    status_seam.recorded_settlement(txn),
                ),
            )
            db.session.flush()
            assert txn.covering_movements == []
            assert db.session.get(TransactionEntry, movement_id) is None
