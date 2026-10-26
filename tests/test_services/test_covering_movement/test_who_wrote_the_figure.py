"""Who wrote a movement's figure: the source a correction takes, the evidence an identity re-submit
keeps, the purchase doors, the mark, the catalogue and the retained read.
"""

from datetime import date, timedelta
from decimal import Decimal

import pytest
import sqlalchemy.exc

from app import ref_cache
from app.enums import MovementFigureSourceEnum, SettledDayBasisEnum, TxnTypeEnum
from app.exceptions import ValidationError
from app.extensions import db
from app.models.transaction_entry import TransactionEntry
from app.services import (
    cash_ledger,
    entry_service,
    row_valuation,
    status_seam,
    transaction_service,
    transfer_service,
)
from app.services.entry_service import EntryDetails
from app.services.settle_day import SettleDay, recorded_settle_day
from tests._test_helpers import observed, one_off_row_of, typed

from ._helpers import (
    _source,
    _bill,
    _settle,
    _revert,
    _only_movement,
    _latest_anchor,
    _legs_of,
    _settled_pair,
)


class TestTheSourceFollowsWhoStatedTheFigure:
    """A corrected figure is labelled by WHO STATED it, never by the day.

    Ruling **R-BAL61** (plan step X-bi-3e-1): the seam used to infer the
    writer from the day's basis beside the figure -- an ``observed`` day made
    the figure the bank's -- and the premise was measured false on every row
    kind.  The writer states it now, as one value with the figure
    (:class:`~app.services.stated_figure.StatedFigure`, ruling **R-BAL69**).
    """

    def test_a_bank_repriced_bill_is_observed(
        self, app, seed_user, seed_periods,
    ):
        """The matcher's shape: the line's figure, stated as the bank's.

        The seam mapped ``corrected`` to ``typed`` regardless of the day, so
        a bill the matcher repriced from a bank line carried a figure stamped
        as a person's (adversarial review, 2026-09-16); then it inferred
        ``observed`` from the day.  The matcher STATES it now.
        """
        with app.app_context():
            txn = _bill(seed_user, seed_periods[0])
            _settle(
                txn, submitted=observed(Decimal("148.40")),
                settle_day=SettleDay(
                    day=seed_periods[0].start_date,
                    basis=SettledDayBasisEnum.OBSERVED,
                ),
            )
            db.session.flush()
            movement = _only_movement(txn)
            assert movement.amount == Decimal("148.40")
            assert movement.figure_source_id == _source(
                MovementFigureSourceEnum.OBSERVED,
            )

    def test_a_figure_typed_over_a_bank_observed_day_is_typed(
        self, app, seed_user, seed_periods,
    ):
        """BAL-508's class on a bill: the person's figure beside the bank's day.

        The reconcile-then-correct shape ruling R-BAL61 measured (5 settled
        rows on the 2026-09-18 restore): the matcher confirmed the DAY, and
        a person later typed the figure through the popover.  The old
        inference (``_source_of`` over the day's basis) labelled that figure
        the bank's; delete this leaf's change and this fails.
        """
        with app.app_context():
            txn = _bill(seed_user, seed_periods[0])
            _settle(
                txn,
                settle_day=SettleDay(
                    day=seed_periods[0].start_date,
                    basis=SettledDayBasisEnum.OBSERVED,
                ),
            )
            db.session.flush()
            assert _only_movement(txn).figure_source_id == _source(
                MovementFigureSourceEnum.RESOLVED,
            )
            transaction_service.apply_requested_status(
                txn, txn.status_id,
                stated=transaction_service.StatedRecord(figure=typed(Decimal("148.40"))),
            )
            db.session.flush()
            movement = _only_movement(txn)
            assert movement.amount == Decimal("148.40")
            assert movement.settled_day_basis_id == ref_cache.settled_day_basis_id(
                SettledDayBasisEnum.OBSERVED,
            ), "the bank's day still stands; only the figure's writer changed"
            assert movement.figure_source_id == _source(
                MovementFigureSourceEnum.TYPED,
            )

    def test_the_matchers_transaction_arm_reprice_is_observed(
        self, app, seed_user, seed_periods,
    ):
        """Through the ONE door the matcher's transaction arm reaches.

        ``statement_match._moving._apply_day`` re-prices a settled bill by an
        identity transition through ``apply_requested_status`` with the
        bank's figure stated ``observed``; the record it makes is the
        bank's on the movement and ``corrected`` on the row.
        """
        with app.app_context():
            txn = _bill(seed_user, seed_periods[0])
            _settle(txn)
            db.session.flush()
            transaction_service.apply_requested_status(
                txn, txn.status_id,
                settle_day=SettleDay(
                    day=seed_periods[0].start_date,
                    basis=SettledDayBasisEnum.OBSERVED,
                ),
                stated=transaction_service.StatedRecord(figure=observed(Decimal("148.40"))),
            )
            db.session.flush()
            movement = _only_movement(txn)
            assert movement.amount == Decimal("148.40")
            assert movement.figure_source_id == _source(
                MovementFigureSourceEnum.OBSERVED,
            )
            assert status_seam.recorded_settlement(txn).source is (
                MovementFigureSourceEnum.OBSERVED
            ), "the retained read takes the source off the movement"


class TestTheMirrorNeverLowersEvidence:
    """An identity re-submit leaves a bank-observed movement standing."""

    def test_a_bank_confirmation_on_the_same_day_raises_the_movement_too(
        self, app, seed_user, seed_periods,
    ):
        """Finding N-332's fix, carried down to the record.

        The panel ticked the bill on an asserted day; a bank line then
        confirms that very day and the row's basis rises to observed.  The
        mirror rose with it only where the DAY moved, so the two homes
        diverged on the reachable path (adversarial review, 2026-09-16).
        """
        with app.app_context():
            txn = _bill(seed_user, seed_periods[0])
            day = seed_periods[0].start_date
            _settle(txn, settle_day=SettleDay(day=day, basis=SettledDayBasisEnum.ASSERTED))
            db.session.flush()
            status_seam.record_clearing(txn, _latest_anchor(txn.account_id).id)
            db.session.flush()
            movement = _only_movement(txn)
            assert movement.settled_day_basis_id == ref_cache.settled_day_basis_id(
                SettledDayBasisEnum.ASSERTED,
            )
            link_before = movement.reconciled_by_id
            assert link_before is not None

            status_seam.apply_status_change(
                txn, txn.status_id,
                settle_day=SettleDay(day=day, basis=SettledDayBasisEnum.OBSERVED),
            )
            db.session.flush()

            same = _only_movement(txn)
            assert same.settled_on == day
            assert same.settled_day_basis_id == ref_cache.settled_day_basis_id(
                SettledDayBasisEnum.OBSERVED,
            )
            assert same.reconciled_by_id == link_before

    def test_an_identity_re_submit_keeps_the_movements_observed_day_and_link(
        self, app, seed_user, seed_periods,
    ):
        with app.app_context():
            txn = _bill(seed_user, seed_periods[0])
            _settle(txn)
            db.session.flush()
            movement = _only_movement(txn)
            # The matcher's write: the bank observed the same day, and a
            # statement showed the MOVEMENT (the row's leg is zero, so the row
            # is never offered).
            movement.settled_day_basis_id = ref_cache.settled_day_basis_id(
                SettledDayBasisEnum.OBSERVED,
            )
            movement.reconciled_by_id = _latest_anchor(txn.account_id).id
            db.session.flush()

            status_seam.apply_status_change(txn, txn.status_id)
            db.session.flush()

            same = _only_movement(txn)
            assert same.settled_on == txn.settled_on
            assert same.settled_day_basis_id == ref_cache.settled_day_basis_id(
                SettledDayBasisEnum.OBSERVED,
            )
            assert same.reconciled_by_id is not None

    def test_a_settle_day_correction_moves_the_movements_day_and_releases(
        self, app, seed_user, seed_periods,
    ):
        with app.app_context():
            txn = _bill(seed_user, seed_periods[0])
            _settle(txn)
            db.session.flush()
            movement = _only_movement(txn)
            movement.reconciled_by_id = _latest_anchor(txn.account_id).id
            db.session.flush()
            moved_to = txn.settled_on - timedelta(days=1)

            status_seam.apply_status_change(
                txn, txn.status_id,
                settle_day=SettleDay(day=moved_to, basis=SettledDayBasisEnum.ENTERED),
            )
            db.session.flush()

            same = _only_movement(txn)
            assert same.settled_on == moved_to
            assert same.purchased_on == moved_to
            assert same.reconciled_by_id is None


class TestThePurchaseDoorsStateTheSource:
    """Who wrote the figure, by the door that wrote it."""

    def test_a_hand_typed_purchase_is_typed(self, app, seed_user, seed_periods):
        with app.app_context():
            envelope = _bill(seed_user, seed_periods[0], "100.00", is_envelope=True)
            entry = entry_service.create_entry(
                envelope.id, seed_user["user"].id,
                EntryDetails(
                    figure=typed(Decimal("12.79")), description="Kroger",
                    purchased_on=seed_periods[0].start_date,
                ),
            )
            assert entry.figure_source_id == _source(MovementFigureSourceEnum.TYPED)

    def test_a_purchase_born_from_a_bank_line_is_observed(
        self, app, seed_user, seed_periods,
    ):
        """The born-purchase builder STATES the bank wrote it (R-BAL61)."""
        with app.app_context():
            envelope = _bill(seed_user, seed_periods[0], "100.00", is_envelope=True)
            entry = entry_service.create_entry(
                envelope.id, seed_user["user"].id,
                EntryDetails(
                    figure=observed(Decimal("12.79")), description="KROGER #123",
                    purchased_on=seed_periods[0].start_date,
                    settle_day=SettleDay(
                        day=seed_periods[0].start_date,
                        basis=SettledDayBasisEnum.OBSERVED,
                    ),
                ),
            )
            assert entry.figure_source_id == _source(MovementFigureSourceEnum.OBSERVED)

    def test_a_human_amount_edit_makes_a_bank_figure_typed(
        self, app, seed_user, seed_periods,
    ):
        with app.app_context():
            envelope = _bill(seed_user, seed_periods[0], "100.00", is_envelope=True)
            entry = entry_service.create_entry(
                envelope.id, seed_user["user"].id,
                EntryDetails(
                    figure=observed(Decimal("12.79")), description="KROGER #123",
                    purchased_on=seed_periods[0].start_date,
                    settle_day=SettleDay(
                        day=seed_periods[0].start_date,
                        basis=SettledDayBasisEnum.OBSERVED,
                    ),
                ),
            )
            db.session.flush()
            entry_service.update_entry(
                entry.id, seed_user["user"].id, figure=typed(Decimal("12.97")),
            )
            assert entry.figure_source_id == _source(MovementFigureSourceEnum.TYPED)

    def test_a_bank_confirmation_of_the_day_alone_leaves_a_typed_figure_typed(
        self, app, seed_user, seed_periods,
    ):
        """INVERTED at plan step X-bi-3e-1 (ruling R-BAL61).

        This door RAISED a typed figure to ``observed`` whenever a submission
        carried an ``observed`` day, figure written or not -- and every
        bank-observed day ever written onto a settled row on production was
        exactly this, a day-only confirmation.  A match that confirms the day
        writes no figure, so nothing about who wrote it changed.
        """
        with app.app_context():
            envelope = _bill(seed_user, seed_periods[0], "100.00", is_envelope=True)
            entry = entry_service.create_entry(
                envelope.id, seed_user["user"].id,
                EntryDetails(
                    figure=typed(Decimal("12.79")), description="Kroger",
                    purchased_on=seed_periods[0].start_date,
                ),
            )
            db.session.flush()
            entry_service.update_entry(
                entry.id, seed_user["user"].id,
                settle_day=SettleDay(
                    day=seed_periods[0].start_date,
                    basis=SettledDayBasisEnum.OBSERVED,
                ),
            )
            assert entry.settled_day_basis_id == ref_cache.settled_day_basis_id(
                SettledDayBasisEnum.OBSERVED,
            ), "the day IS confirmed; the figure's writer is not the question"
            assert entry.figure_source_id == _source(MovementFigureSourceEnum.TYPED)

    def test_a_figure_typed_over_a_standing_observed_day_is_typed(
        self, app, seed_user, seed_periods,
    ):
        """BAL-508's class on a purchase, through the PATCH's own shape.

        The entry PATCH re-submits the whole form, so a person re-pricing a
        bank-confirmed purchase sends the recorded ``observed`` day back
        unchanged beside the new figure (``submitted_settle_day``'s echo).
        Under ``figure_source_of`` that echo made the person's figure the
        bank's; the figure says ``typed`` itself now.
        """
        with app.app_context():
            envelope = _bill(seed_user, seed_periods[0], "100.00", is_envelope=True)
            entry = entry_service.create_entry(
                envelope.id, seed_user["user"].id,
                EntryDetails(
                    figure=observed(Decimal("12.79")), description="KROGER #123",
                    purchased_on=seed_periods[0].start_date,
                    settle_day=SettleDay(
                        day=seed_periods[0].start_date,
                        basis=SettledDayBasisEnum.OBSERVED,
                    ),
                ),
            )
            db.session.flush()
            entry_service.update_entry(
                entry.id, seed_user["user"].id,
                figure=typed(Decimal("13.00")),
                settle_day=recorded_settle_day(entry),
            )
            assert entry.amount == Decimal("13.00")
            assert entry.settled_day_basis_id == ref_cache.settled_day_basis_id(
                SettledDayBasisEnum.OBSERVED,
            )
            assert entry.figure_source_id == _source(MovementFigureSourceEnum.TYPED)

    def test_the_matchers_reprice_of_a_purchase_is_observed(
        self, app, seed_user, seed_periods,
    ):
        """The matcher's purchase arm: the bank's figure, stated as such."""
        with app.app_context():
            envelope = _bill(seed_user, seed_periods[0], "100.00", is_envelope=True)
            entry = entry_service.create_entry(
                envelope.id, seed_user["user"].id,
                EntryDetails(
                    figure=typed(Decimal("12.79")), description="Kroger",
                    purchased_on=seed_periods[0].start_date,
                ),
            )
            db.session.flush()
            entry_service.update_entry(
                entry.id, seed_user["user"].id,
                figure=observed(Decimal("12.97")),
                settle_day=SettleDay(
                    day=seed_periods[0].start_date,
                    basis=SettledDayBasisEnum.OBSERVED,
                ),
            )
            assert entry.amount == Decimal("12.97")
            assert entry.figure_source_id == _source(MovementFigureSourceEnum.OBSERVED)

    def test_a_day_only_edit_on_the_owners_word_leaves_the_source_alone(
        self, app, seed_user, seed_periods,
    ):
        with app.app_context():
            envelope = _bill(seed_user, seed_periods[0], "100.00", is_envelope=True)
            entry = entry_service.create_entry(
                envelope.id, seed_user["user"].id,
                EntryDetails(
                    figure=observed(Decimal("12.79")), description="KROGER #123",
                    purchased_on=seed_periods[0].start_date,
                    settle_day=SettleDay(
                        day=seed_periods[0].start_date,
                        basis=SettledDayBasisEnum.OBSERVED,
                    ),
                ),
            )
            db.session.flush()
            entry_service.update_entry(
                entry.id, seed_user["user"].id,
                settle_day=SettleDay(
                    day=seed_periods[0].start_date + timedelta(days=1),
                    basis=SettledDayBasisEnum.ENTERED,
                ),
            )
            assert entry.figure_source_id == _source(MovementFigureSourceEnum.OBSERVED)


class TestTheRecordIsMarkedAndTheSeamsAlone:
    """``covers_settlement``: found by the mark, held to one, closed to the doors."""

    def test_a_second_record_on_one_row_is_refused_by_the_index(
        self, app, seed_user, seed_periods,
    ):
        """The partial unique index, driven."""
        with app.app_context():
            txn = _bill(seed_user, seed_periods[0])
            _settle(txn)
            db.session.flush()
            movement = _only_movement(txn)
            assert movement.covers_settlement is True
            db.session.add(TransactionEntry(
                transaction_id=txn.id, account_id=txn.account_id, owner_id=txn.user_id,
                user_id=seed_user["user"].id, amount=Decimal("1.00"),
                description="second record", purchased_on=txn.settled_on,
                covers_settlement=True,
                figure_source_id=_source(MovementFigureSourceEnum.TYPED),
            ))
            with pytest.raises(sqlalchemy.exc.IntegrityError) as exc:
                db.session.flush()
            assert "uq_transaction_entries_one_settlement_record" in str(exc.value)
            db.session.rollback()

    def test_the_entry_doors_refuse_the_record(
        self, app, seed_user, seed_periods,
    ):
        with app.app_context():
            envelope = _bill(seed_user, seed_periods[0], "100.00", is_envelope=True)
            _settle(envelope)
            db.session.flush()
            record = _only_movement(envelope)
            with pytest.raises(ValidationError, match="payment record"):
                entry_service.update_entry(
                    record.id, seed_user["user"].id,
                    settle_day=SettleDay(
                        day=envelope.settled_on - timedelta(days=1),
                        basis=SettledDayBasisEnum.ENTERED,
                    ),
                )
            with pytest.raises(ValidationError, match="payment record"):
                entry_service.delete_entry(record.id, seed_user["user"].id)
            assert db.session.get(TransactionEntry, record.id) is not None

    def test_a_figure_typed_over_a_real_purchase_is_refused(
        self, app, seed_user, seed_periods,
    ):
        """The door path that once produced a stored figure beside a purchase.

        An envelope with a bank-born purchase is settled, its *Track
        individual purchases* is unticked on the settled row, and a figure is
        typed over it.  Through plan step ``X-bi-3e`` that wrote a
        ``corrected`` record beside the real purchase, and this case graded
        that the seam wrote its OWN movement rather than overwriting the
        purchase as its mirror.  Ruling **R-BAL78** (plan step
        ``balance:X-bi-4a``) makes the state UNREPRESENTABLE: the purchases
        ARE the figure, a stated figure over them would be counted beside
        them by a fold that reads movements alone, so the seam refuses the
        record and the row stands exactly as it was -- settled on its
        purchases, the purchase untouched, no second movement.
        """
        with app.app_context():
            # A bank-born envelope as the bank door mints one since X-bi-7b-3
            # (``statement_match._container._create_envelope`` on the
            # producer): a rule-less definition's placed row, whose flag is
            # the DEFINITION's (ruling R-BAL36) -- which is where the
            # popover's untick lands, below.
            envelope = one_off_row_of(
                seed_periods[0],
                name="Kayla's Spending Money",
                amount=Decimal("100.00"),
                user_id=seed_user["user"].id,
                account_id=seed_user["account"].id,
                scenario_id=seed_user["scenario"].id,
                transaction_type_id=ref_cache.txn_type_id(TxnTypeEnum.EXPENSE),
                category_id=seed_user["categories"]["Rent"].id,
                is_envelope=True,
            )
            db.session.flush()
            purchase = entry_service.create_entry(
                envelope.id, seed_user["user"].id,
                EntryDetails(
                    figure=typed(Decimal("57.96")), description="Walmart",
                    purchased_on=seed_periods[0].start_date,
                ),
            )
            db.session.flush()
            _settle(envelope)
            db.session.flush()
            assert envelope.covering_movements == []
            envelope.template.is_envelope = False
            db.session.flush()
            assert envelope.tracks_purchases is False
            # The DOOR refuses first: a row holding purchases takes its figure
            # from them whatever the flag says (``settles_from_entries``).
            with pytest.raises(ValidationError, match="takes its figure from the purchases"):
                transaction_service.apply_requested_status(
                    envelope, envelope.status_id,
                    stated=transaction_service.StatedRecord(figure=typed(Decimal("999.99"))),
                )
            # The SEAM refuses on its own, for a caller around the door.
            with pytest.raises(ValidationError, match="records its money as purchases"):
                status_seam.apply_status_change(
                    envelope, envelope.status_id,
                    settlement=status_seam.Settlement(
                        amount=Decimal("999.99"),
                        source=MovementFigureSourceEnum.TYPED,
                    ),
                )
            db.session.flush()

            assert status_seam.recorded_settlement(envelope) == status_seam.Settlement(
                None, None,
            )
            assert envelope.covering_movements == []
            kept = db.session.get(TransactionEntry, purchase.id)
            assert kept.amount == Decimal("57.96")
            assert kept.description == "Walmart"
            assert kept.covers_settlement is False


class TestTheCatalogueIsSeededAndResolvable:
    """Three members, each a row, none absent."""

    @pytest.mark.parametrize("member", list(MovementFigureSourceEnum))
    def test_each_member_resolves(self, app, member):
        with app.app_context():
            assert isinstance(_source(member), int)

    def test_a_movement_stating_no_source_is_refused_at_flush(
        self, app, seed_user, seed_periods,
    ):
        """NOT NULL with no default: the constraint, driven."""
        with app.app_context():
            envelope = _bill(seed_user, seed_periods[0], "100.00", is_envelope=True)
            db.session.add(TransactionEntry(
                transaction_id=envelope.id, account_id=envelope.account_id, owner_id=envelope.user_id,
                user_id=seed_user["user"].id, amount=Decimal("5.00"),
                description="bare", purchased_on=date(2026, 1, 5),
            ))
            with pytest.raises(sqlalchemy.exc.IntegrityError) as exc:
                db.session.flush()
            assert "figure_source_id" in str(exc.value)
            db.session.rollback()


class TestTheRetainedReadTakesTheSourceOffTheMovement:
    """``recorded_settlement`` reads the figure AND who wrote it off the
    covering movement, and a settled row with none records its entries.

    Plan step X-bi-3e-1 (ruling **R-BAL61**): the row stores no writer, so
    the retained record's source is the movement's.  Plan step
    ``balance:X-bi-4b-1`` (rulings **R-BAL80**, **R-BAL82**): the FIGURE is
    the movement's too, and a settled row holding no movement -- a ``$0.00``
    figure, whose movement ``ck_transaction_entries_positive_amount`` admits
    no row for -- is a close of nothing, read as a record stating no figure
    (``Settlement(None, None)``) exactly as an envelope's ``purchases``
    record is; reverted, it retains NOTHING.  Ruling **R-BAL70**'s cutover
    mapping (``derived`` -> ``resolved``, ``corrected`` -> ``typed`` off the
    row's basis column) answered that row through ``X-bi-4a`` and retired
    with the column read; the two ``$0.00`` cases below pinned it and pin
    R-BAL82 now.
    """

    def test_a_typed_correction_reads_typed_off_its_movement(
        self, app, seed_user, seed_periods,
    ):
        with app.app_context():
            txn = _bill(seed_user, seed_periods[0])
            _settle(txn, submitted=typed(Decimal("150.00")))
            db.session.flush()
            retained = status_seam.recorded_settlement(txn)
            assert retained.amount == Decimal("150.00")
            assert retained.source is MovementFigureSourceEnum.TYPED
            assert retained.stated

    def test_a_typed_zero_record_is_a_close_of_nothing_and_retains_nothing(
        self, app, seed_user, seed_periods,
    ):
        """A ``$0.00`` typed figure holds no movement: a close of nothing.

        ``ck_transaction_entries_positive_amount`` admits no movement of
        nothing (``TestAZeroSettlementWritesNoMovement``), so the row records
        its entries -- none -- and the typed figure's writer is stored nowhere
        (R-BAL70 accepted that; R-BAL82 follows it through).  Worked as the
        ruling was put: 'Water' budgeted `$148.32` here, closed at a typed
        `$0.00`, then reverted -- the panel used to OFFER and re-book
        `$0.00` (the retained ``corrected`` column), and offers the plan now
        unless the owner re-types `$0.00`.
        """
        with app.app_context():
            txn = _bill(seed_user, seed_periods[0])
            _settle(txn)
            db.session.flush()
            transaction_service.apply_requested_status(
                txn, txn.status_id,
                stated=transaction_service.StatedRecord(figure=typed(Decimal("0.00"))),
            )
            db.session.flush()
            assert txn.covering_movements == []
            assert row_valuation.settled_figure(txn) == Decimal("0")
            retained = status_seam.recorded_settlement(txn)
            assert retained.amount is None
            assert retained.source is None
            assert status_seam.honoured_correction(txn) is None

            _revert(txn)
            db.session.flush()
            assert status_seam.recorded_settlement(txn) is None
            assert transaction_service.retained_settle_amounts_by_id([txn]) == {
                txn.id: None,
            }
            basis = cash_ledger.derived_amount_basis(
                seed_user["user"].id, seed_user["scenario"].id,
            )
            assert transaction_service.settle_amount(txn, basis) == Decimal(
                "148.32"
            )

    def test_a_derived_zero_record_is_a_close_of_nothing(
        self, app, seed_user, seed_periods,
    ):
        with app.app_context():
            txn = _bill(seed_user, seed_periods[0], "0.00")
            _settle(txn)
            db.session.flush()
            assert txn.covering_movements == []
            assert row_valuation.settled_figure(txn) == Decimal("0")
            retained = status_seam.recorded_settlement(txn)
            assert retained == status_seam.Settlement(None, None)
            assert not retained.stated

    def test_a_purchases_record_reads_no_source(
        self, app, seed_user, seed_periods,
    ):
        with app.app_context():
            envelope = _bill(seed_user, seed_periods[0], "100.00", is_envelope=True)
            entry_service.create_entry(
                envelope.id, seed_user["user"].id,
                EntryDetails(
                    figure=typed(Decimal("12.79")), description="Kroger",
                    purchased_on=seed_periods[0].start_date,
                ),
            )
            _settle(envelope)
            db.session.flush()
            retained = status_seam.recorded_settlement(envelope)
            assert retained == status_seam.Settlement(None, None)
            assert not retained.stated

    def test_each_leg_of_a_corrected_transfer_holds_one_record_and_reads_typed_off_the_transfer(
        self, app, seed_user, seed_periods,
    ):
        """Each leg holds one record, and its retained read is the transfer's figure and source.

        Read through ``status_seam.recorded_leg_settlement``, the leg-shaped
        read the transfer settle takes its retained record by, since plan step
        balance:X-bi-6-4d-2 hung each side's record off the transfer; it was
        ``recorded_settlement`` off each shadow until then (ruling R-BAL167
        class 4).  The figure and who wrote it are stored once on the
        TRANSFER since that step's 4d-4 half; each side's record carried
        them until then and states neither now.  Named
        ``test_both_legs_of_a_corrected_transfer_read_typed_off_their_movements``
        until that half.
        """
        with app.app_context():
            xfer, _, _ = _settled_pair(seed_user, seed_periods[0])
            transfer_service.update_transfer(
                xfer.id, seed_user["user"].id, figure=typed(Decimal("480.00")),
            )
            db.session.flush()
            for leg in _legs_of(xfer):
                _only_movement(leg)
                assert leg.transfer.settled_figure == Decimal("480.00")
                assert leg.transfer.settled_figure_source_id == _source(
                    MovementFigureSourceEnum.TYPED,
                )
                retained = status_seam.recorded_leg_settlement(leg)
                assert retained.amount == Decimal("480.00")
                assert retained.source is MovementFigureSourceEnum.TYPED


class TestTheRetainedReadSurvivesARevert:
    """The 3e-1 interval pin, INVERTED at leaf X-bi-3e-2 (ruling R-BAL61).

    Through 3e-1 a revert deleted the covering movement and the retained
    record read by ruling R-BAL70's mapping alone, so a figure the BANK
    stated, reverted and re-settled, read ``typed``.  The movement now
    survives a revert un-dated, and the retained read takes the source off
    it: the bank's figure re-settles ``observed`` on the same row.
    """

    def test_a_reverted_bank_figure_re_settles_observed_off_the_surviving_movement(
        self, app, seed_user, seed_periods,
    ):
        with app.app_context():
            txn = _bill(seed_user, seed_periods[0])
            _settle(
                txn, submitted=observed(Decimal("148.40")),
                settle_day=SettleDay(
                    day=seed_periods[0].start_date,
                    basis=SettledDayBasisEnum.OBSERVED,
                ),
            )
            db.session.flush()
            first = _only_movement(txn)
            first_id = first.id
            assert first.figure_source_id == _source(
                MovementFigureSourceEnum.OBSERVED,
            )
            _revert(txn)
            db.session.flush()
            survivor = _only_movement(txn)
            assert survivor.id == first_id
            assert survivor.settled_on is None
            retained = status_seam.recorded_settlement(txn)
            assert retained.amount == Decimal("148.40")
            assert retained.source is MovementFigureSourceEnum.OBSERVED
            _settle(txn)
            db.session.flush()
            movement = _only_movement(txn)
            assert movement.id == first_id
            assert movement.amount == Decimal("148.40"), "the figure is honoured"
            assert movement.figure_source_id == _source(
                MovementFigureSourceEnum.OBSERVED,
            ), "who wrote a figure does not change because the row was reverted"
            assert movement.settled_on == txn.settled_on
