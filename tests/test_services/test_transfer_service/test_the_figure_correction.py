"""The edit door's FIGURE correction on a settled transfer, the service tier of the Actual box:
recorded once on the transfer, beside both sides' dated records, refused untouched on an
unsettled transfer, an echo leaving the resolved source standing, and a settle owning a figure
that arrives with it.
"""

from decimal import Decimal

import pytest

from app import ref_cache
from app.enums import MovementFigureSourceEnum, StatusEnum
from app.extensions import db
from app.services import transfer_service
from app.exceptions import ValidationError
from tests._test_helpers import transfer_side_record, typed
from app.models.amount_ownership import AmountOwnership

from ._helpers import _reread


class TestTheFigureCorrectionDoorOnAPair:
    """``update_transfer``'s FIGURE correction -- the service tier of the Actual box.

    The transfer twin of ``transaction_service``'s door, and it exists because
    the two halves of one assertion had different rights: a transfer's settle
    DAY was correctable in place (ruling **R-ED**) while its FIGURE was refused
    outright, so restating what the bank moved meant reverting the transfer --
    and a revert RETAINS the recorded figure, so the re-settle re-booked the old
    number over the re-planned one (developer ruling, 2026-08-17).

    The route DROPS a figure that arrives with an unsettling status (ruling
    **R-EG**, graded at the route tier); what these grade is the SERVICE, where
    a caller stating both facts on purpose is refused instead.
    """

    @staticmethod
    def _settled_transfer(td, amount="250.00"):
        """Return a transfer created Projected and then settled."""
        xfer = transfer_service.create_transfer(
            transfer_service.TransferSpec(
                user_id=td["user"].id,
                from_account_id=td["account"].id,
                to_account_id=td["savings_account"].id,
                pay_period_id=td["periods"][0].id,
                scenario_id=td["scenario"].id,
                amount_ownership=AmountOwnership.own(Decimal(amount)),
                status_id=td["projected_status"].id,
                category_id=td["categories"]["Rent"].id,
            ),
        )
        db.session.flush()
        transfer_service.update_transfer(
            xfer.id, td["user"].id,
            status_id=ref_cache.status_id(StatusEnum.DONE),
        )
        db.session.flush()
        return xfer

    @staticmethod
    def _records(xfer):
        """Return the transfer's ``(from-side, to-side)`` payment records.

        What each side RECORDS since plan step ``balance:X-bi-6-4d-2``, read by
        independent SQL over the side links (ruling R-BAL167 class 4, plan
        step balance:X-bi-6-4d-2); its two shadow rows held it until then.
        Each holds its side's day; the figure and who wrote it are the
        TRANSFER's since that step's 4d-4 half (rulings **R-BAL365**,
        **R-BAL366**), where each record carried a copy until then.
        """
        return tuple(
            transfer_side_record(db.session, xfer.id, account_id)
            for account_id in (xfer.from_account_id, xfer.to_account_id)
        )

    def test_a_correction_records_once_on_a_paid_transfer_keeping_it_paid_and_both_sides_days(
        self, app, db, transfer_data,
    ):
        """The settlement record is ONE figure, on the transfer.

        A transfer's money moves on its two sides, and the figure it moved is
        ONE: the TRANSFER stores it and who wrote it, its one home since plan
        step ``balance:X-bi-6-4d-2``'s 4d-4 half (rulings **R-BAL365**,
        **R-BAL366**), where each side's record carried an equal copy until
        then.  Each side's day is its record's, which hangs off the transfer by
        a side link, read off the sides' records since plan step
        ``balance:X-bi-6-4d-2``, where the shadows held them until then
        (ruling R-BAL167 class 1, plan step balance:X-bi-6-4d-2).  The status
        is read off the row re-read from the database since plan step
        ``balance:X-bi-6-4d-2``'s checkpoint 2e-2b-1b-2, where it read the
        object in the session (ruling R-BAL290 part 1).  Named
        ``test_a_correction_records_on_both_legs_and_on_neither_parent``
        until plan step ``balance:X-bi-6-4d-2``'s 4d-4 half, when each side's
        record held the figure.
        """
        with app.app_context():
            td = transfer_data
            xfer = self._settled_transfer(td)
            day = self._records(xfer)[0].settled_on
            assert day is not None, "setup: the settle dated the pair"
            typed_id = ref_cache.movement_figure_source_id(
                MovementFigureSourceEnum.TYPED,
            )

            transfer_service.update_transfer(
                xfer.id, td["user"].id, figure=typed(Decimal("263.11")),
            )

            row = _reread(xfer.id)
            assert (row.settled_figure, row.settled_figure_source_id) == (
                Decimal("263.11"), typed_id,
            )
            for record in self._records(row):
                assert record.settled_on == day, (
                    "a figure correction moved the pair's day"
                )
            # The PARENT went through the seam too, and came out on its status,
            # Paid -- the one status the pair has, which each shadow mirrored
            # until plan step ``balance:X-bi-6-4d-2`` (Transfer Invariant 4).
            # It deliberately does NOT assert ``not hasattr(xfer,
            # "settled_amount")``: the seam gates its whole record block on
            # ``isinstance(row, Transaction)``, so handing the parent a record
            # is a silent no-op and only a SCHEMA change could fail such an
            # assertion -- the "``hasattr`` is not a test" shape plan step X-aa
            # already paid for (neutral review, 2026-08-18).
            assert row.status_id == ref_cache.status_id(StatusEnum.DONE), (
                "the correction moved the pair's status"
            )

    def test_a_figure_on_an_UNSETTLED_transfer_is_refused_untouched(
        self, app, db, transfer_data,
    ):
        """An amount states what MOVED, and a Projected pair's money has not.

        Refused BEFORE any field is written, which is why the gate sits at the
        top of ``_apply_transfer_updates`` beside the loan-move refusal rather
        than among the field arms: ``is_override`` and ``amount`` are staged two
        statements later, so a refusal further down would leave a partially
        edited transfer behind a raised exception.

        Shown to FIRE: the accompanying ``amount`` is asserted UNWRITTEN, so a
        gate moved below the field loop fails this test rather than passing it.
        Both writes are read off the row re-read from the database since plan
        step ``balance:X-bi-6-4d-2``'s checkpoint 2e-2b-1b-2, where they read
        the object in the session (ruling R-BAL290 part 1).

        It also asked each of the transfer's shadow rows for a recorded
        settlement until that checkpoint.  From that step's create door on no
        row can name a transfer (``ck_transactions_names_no_transfer``), so
        the loop ran over none (a vacuous read: such rows counted 0 before
        the update and after it, measured at that checkpoint), and it is
        dropped, not moved onto each side's record: with this gate deleted,
        the status seam refuses the figure itself, in other words, before
        either side records anything, and this case fails first at the
        refusal's words, the flag and the amount left written (measured
        there).
        """
        with app.app_context():
            td = transfer_data
            xfer = transfer_service.create_transfer(
                transfer_service.TransferSpec(
                    user_id=td["user"].id,
                    from_account_id=td["account"].id,
                    to_account_id=td["savings_account"].id,
                    pay_period_id=td["periods"][0].id,
                    scenario_id=td["scenario"].id,
                    amount_ownership=AmountOwnership.own(Decimal("250.00")),
                    status_id=td["projected_status"].id,
                    category_id=td["categories"]["Rent"].id,
                ),
            )
            db.session.flush()

            assert xfer.is_override is False, "fixture precondition"

            with pytest.raises(ValidationError) as exc:
                transfer_service.update_transfer(
                    xfer.id, td["user"].id,
                    is_override=True,
                    amount_ownership=AmountOwnership.own(Decimal("999.00")),
                    figure=typed(Decimal("50.00")),
                )

            assert "has nothing to record" in str(exc.value)
            # BOTH pre-settle writes are asserted untouched, and both are
            # needed: ``is_override`` is applied FIRST and ``amount`` second, so
            # a gate that slipped BETWEEN them would leave one written and pass
            # a test that checked only the other.
            row = _reread(xfer.id)
            assert row.is_override is False, (
                "the refusal ran AFTER the is_override write"
            )
            assert row.amount == Decimal("250.00"), (
                "the refusal ran AFTER the amount write"
            )

    def test_an_echo_leaves_the_resolved_source_standing(
        self, app, db, transfer_data,
    ):
        """Re-posting the prefilled figure must not manufacture a correction.

        The figure's source is the only stored signal that a human read a
        number off a statement, and this form submits every input it renders
        on every save.  It is the TRANSFER's, its one home since plan step
        ``balance:X-bi-6-4d-2``'s 4d-4 half (ruling **R-BAL366**), where each
        side's payment record, read off the transfer's side links since that
        step (ruling R-BAL167 class 1, plan step balance:X-bi-6-4d-2), carried
        a copy until then.
        """
        with app.app_context():
            td = transfer_data
            xfer = self._settled_transfer(td)
            resolved = ref_cache.movement_figure_source_id(
                MovementFigureSourceEnum.RESOLVED,
            )
            assert None not in self._records(xfer)
            assert xfer.settled_figure_source_id == resolved

            transfer_service.update_transfer(
                xfer.id, td["user"].id, figure=typed(Decimal("250.00")),
            )
            db.session.flush()

            assert None not in self._records(xfer)
            assert xfer.settled_figure_source_id == resolved

    def test_a_settle_still_owns_a_figure_arriving_with_it(
        self, app, db, transfer_data,
    ):
        """The correction door must not have stolen the SETTLE's figure.

        A figure riding a settling ``status_id`` is the settle's own
        (``_SETTLE_OWNED_FIELDS``), subject to its echo rule -- and the settle
        writes the status, the pair's day and the record as ONE act.  If the
        correction arm had taken it instead, the figure would be written twice
        and the freeze would be resolved after the status flip, where it always
        answers ``None``.  The record is each side's, read off the transfer's
        side links since plan step ``balance:X-bi-6-4d-2`` (ruling R-BAL167
        class 1, plan step balance:X-bi-6-4d-2), and holds the side's day;
        the figure and who wrote it are the TRANSFER's since that step's 4d-4
        half (rulings **R-BAL365**, **R-BAL366**), where each record carried
        a copy until then.
        """
        with app.app_context():
            td = transfer_data
            xfer = transfer_service.create_transfer(
                transfer_service.TransferSpec(
                    user_id=td["user"].id,
                    from_account_id=td["account"].id,
                    to_account_id=td["savings_account"].id,
                    pay_period_id=td["periods"][0].id,
                    scenario_id=td["scenario"].id,
                    amount_ownership=AmountOwnership.own(Decimal("250.00")),
                    status_id=td["projected_status"].id,
                    category_id=td["categories"]["Rent"].id,
                ),
            )
            db.session.flush()

            # Driven through the NAMED verb, because its return value is the
            # one observable that separates the two arms.  Asserting the end
            # state alone cannot: the echo rule makes a figure taken by the
            # CORRECTION arm instead converge on a byte-identical row (the
            # settle would record the plan, the correction would overwrite it),
            # so an end-state-only test passes either way.  ``settle_transfer``
            # reports whether the SETTLE booked a human's figure, and that is
            # False the moment the settle stops owning it (neutral review,
            # 2026-08-18).
            corrected = transfer_service.settle_transfer(
                xfer.id, td["user"].id, submitted=typed(Decimal("241.00")),
            )
            db.session.flush()

            assert corrected is True, (
                "the SETTLE did not book the figure -- the correction arm took "
                "it, so the freeze was resolved after the status flip"
            )
            typed_id = ref_cache.movement_figure_source_id(
                MovementFigureSourceEnum.TYPED,
            )
            assert (xfer.settled_figure, xfer.settled_figure_source_id) == (
                Decimal("241.00"), typed_id,
            ), "a figure that differs from the plan IS a correction"
            for record in self._records(xfer):
                assert record.settled_on is not None
