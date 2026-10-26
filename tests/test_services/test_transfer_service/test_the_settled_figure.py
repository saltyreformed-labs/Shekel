"""
Tests for a transfer's settled figure: its ONE home, stated, kept and released.

Plan step ``balance:X-bi-6-4d-2``'s 4d-4 half (rulings **R-BAL365**,
**R-BAL366**).  A settled transfer stores what its money moved and who wrote
the figure on the TRANSFER (``settled_figure`` / ``settled_figure_source_id``),
written by the status seam's Transfer arm alone.  These cases grade the
figure's lifetime at the stored columns, through the service doors: stated by
a settle and a correction, ``0.00`` for a close of nothing, kept across a
revert that keeps the records (ruling **R-BAL61**), and released by a revert
that keeps none (R-BAL365: "Reverting a $0.00 close clears it, so a re-settle
re-prices") and by the delete that takes the records off.
"""

from decimal import Decimal

from sqlalchemy import text

from app import ref_cache
from app.enums import MovementFigureSourceEnum, StatusEnum
from app.extensions import db
from app.services import transfer_service
from tests._test_helpers import (
    create_savings_account,
    create_settled_transfer,
    transfer_side_record,
    typed,
)


def _stored(transfer_id):
    """Return a transfer's stored ``(settled_figure, source member)``, read off the columns."""
    figure, source_id = db.session.execute(
        text(
            "SELECT settled_figure, settled_figure_source_id "
            "FROM budget.transfers WHERE id = :t"
        ),
        {"t": transfer_id},
    ).one()
    members = {
        ref_cache.movement_figure_source_id(member): member
        for member in MovementFigureSourceEnum
    }
    return figure, (None if source_id is None else members[source_id])


def _settled(seed_user, seed_periods, amount="120.00"):
    """Settle a ``checking -> savings`` transfer planned at *amount*; return ``(id, savings id)``."""
    savings = create_savings_account(
        seed_user, db.session, "Savings", Decimal("0.00"),
    )
    xfer = create_settled_transfer(
        seed_user, db.session, seed_user["account"], savings, seed_periods[0],
        amount=Decimal(amount),
    )
    db.session.commit()
    return xfer.id, savings.id


def _move(transfer_id, user_id, member, **kwargs):
    """Move a transfer to *member* through the one transfer writer, and commit."""
    transfer_service.update_transfer(
        transfer_id, user_id, status_id=ref_cache.status_id(member), **kwargs,
    )
    db.session.commit()


class TestTheFigureIsStatedOnce:
    """A settle and a correction state the figure and who wrote it, on the transfer."""

    def test_a_settle_then_a_typed_correction(self, app, seed_user, seed_periods):
        """``$120.00`` resolved at the settle; ``$118.40`` typed after the correction."""
        with app.app_context():
            xfer_id, _ = _settled(seed_user, seed_periods)
            assert _stored(xfer_id) == (
                Decimal("120.00"), MovementFigureSourceEnum.RESOLVED,
            )
            transfer_service.update_transfer(
                xfer_id, seed_user["user"].id, figure=typed(Decimal("118.40")),
            )
            db.session.commit()
            assert _stored(xfer_id) == (
                Decimal("118.40"), MovementFigureSourceEnum.TYPED,
            )

    def test_a_close_of_nothing_stores_zero(self, app, seed_user, seed_periods):
        """A typed ``$0.00`` stores ``0.00`` and who typed it, with no record either side."""
        with app.app_context():
            xfer_id, savings_id = _settled(seed_user, seed_periods)
            transfer_service.update_transfer(
                xfer_id, seed_user["user"].id, figure=typed(Decimal("0")),
            )
            db.session.commit()
            assert _stored(xfer_id) == (
                Decimal("0.00"), MovementFigureSourceEnum.TYPED,
            )
            assert transfer_side_record(db.session, xfer_id, savings_id) is None


class TestARevert:
    """A revert keeps the figure its kept records moved, and clears a close of nothing's."""

    def test_a_revert_keeps_a_typed_figure_and_the_resettle_honours_it(
        self, app, seed_user, seed_periods,
    ):
        """Typed ``$118.40``, reverted: kept; re-settled: ``$118.40`` again (R-BAL61)."""
        with app.app_context():
            user_id = seed_user["user"].id
            xfer_id, _ = _settled(seed_user, seed_periods)
            transfer_service.update_transfer(
                xfer_id, user_id, figure=typed(Decimal("118.40")),
            )
            db.session.commit()
            _move(xfer_id, user_id, StatusEnum.PROJECTED)
            assert _stored(xfer_id) == (
                Decimal("118.40"), MovementFigureSourceEnum.TYPED,
            )
            _move(xfer_id, user_id, StatusEnum.DONE)
            assert _stored(xfer_id) == (
                Decimal("118.40"), MovementFigureSourceEnum.TYPED,
            )

    def test_a_reverted_close_of_nothing_is_cleared_and_reprices(
        self, app, seed_user, seed_periods,
    ):
        """A ``$0.00`` close reverted stores nothing; re-settled it books the plan's ``$120.00``.

        Ruling **R-BAL365**'s own sentence: "Reverting a $0.00 close clears
        it, so a re-settle re-prices at $500.00, as today" (here the plan is
        ``$120.00``).  A figure kept across this revert would be honoured by
        the re-settle as a TYPED ``$0.00`` -- a payment the owner withdrew
        re-booked at nothing.
        """
        with app.app_context():
            user_id = seed_user["user"].id
            xfer_id, _ = _settled(seed_user, seed_periods)
            transfer_service.update_transfer(
                xfer_id, user_id, figure=typed(Decimal("0")),
            )
            db.session.commit()
            _move(xfer_id, user_id, StatusEnum.PROJECTED)
            assert _stored(xfer_id) == (None, None)
            _move(xfer_id, user_id, StatusEnum.DONE)
            assert _stored(xfer_id) == (
                Decimal("120.00"), MovementFigureSourceEnum.RESOLVED,
            )


class TestTheDeleteReleasesTheFigure:
    """The soft delete takes the records off, and the figure goes with them."""

    def test_a_settled_transfer(self, app, seed_user, seed_periods):
        """Settled, soft-deleted: back to plan (R-BAL246) holding no figure."""
        with app.app_context():
            xfer_id, _ = _settled(seed_user, seed_periods)
            transfer_service.delete_transfer(
                xfer_id, seed_user["user"].id, soft=True,
            )
            db.session.commit()
            assert _stored(xfer_id) == (None, None)

    def test_a_reverted_transfer_keeping_its_records(
        self, app, seed_user, seed_periods,
    ):
        """Projected holding kept records, soft-deleted: the writer moves nothing, the delete releases."""
        with app.app_context():
            user_id = seed_user["user"].id
            xfer_id, savings_id = _settled(seed_user, seed_periods)
            _move(xfer_id, user_id, StatusEnum.PROJECTED)
            assert transfer_side_record(db.session, xfer_id, savings_id) is not None
            transfer_service.delete_transfer(xfer_id, user_id, soft=True)
            db.session.commit()
            assert _stored(xfer_id) == (None, None)
            assert transfer_side_record(db.session, xfer_id, savings_id) is None

    def test_a_close_of_nothing_keeps_its_zero(self, app, seed_user, seed_periods):
        """A ``$0.00`` close held nothing: it stays Paid at ``0.00`` (R-BAL246's exception)."""
        with app.app_context():
            user_id = seed_user["user"].id
            xfer_id, _ = _settled(seed_user, seed_periods)
            transfer_service.update_transfer(
                xfer_id, user_id, figure=typed(Decimal("0")),
            )
            db.session.commit()
            transfer_service.delete_transfer(xfer_id, user_id, soft=True)
            db.session.commit()
            assert _stored(xfer_id) == (
                Decimal("0.00"), MovementFigureSourceEnum.TYPED,
            )
