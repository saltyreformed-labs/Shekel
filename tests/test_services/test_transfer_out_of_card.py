"""
Shekel Budget App -- a transfer OUT of a credit card is refused at both doors

Plan step ``credit_card:CC-10``, design ``docs/design/credit_card_from_scratch.md``
3.8: no cash advances, no balance transfers.
``_validation._reject_transfer_out_of_revolving`` is the loan refusal's
sibling inside ``_validation._reject_unmodeled_source``, the ONE set of source
refusals both doors call -- ``create_transfer`` and the endpoint move in
``_endpoints._resolve_endpoints`` -- and nowhere else.  The allow pins are the
controls: the SAME requests with the card as the DESTINATION succeed, so the
refusal is attributable to the card being the SOURCE and to nothing else.
"""

from decimal import Decimal

import pytest

from app import ref_cache
from app.enums import StatusEnum
from app.exceptions import ValidationError
from app.extensions import db
from app.models.amount_ownership import AmountOwnership
from app.models.transfer import Transfer
from app.services import transfer_service
from tests._test_helpers import create_account_of_type


def _card(td, name="Visa"):
    """Build a Credit Card account owned by the seeded user, books open."""
    return create_account_of_type(
        td, db.session, "Credit Card", name,
        anchor_balance=Decimal("-500.00"),
    )


def _spec(td, *, from_account_id, to_account_id):
    """The one create spec these tests vary only by its endpoints."""
    return transfer_service.TransferSpec(
        user_id=td["user"].id,
        from_account_id=from_account_id,
        to_account_id=to_account_id,
        pay_period_id=td["periods"][0].id,
        scenario_id=td["scenario"].id,
        amount_ownership=AmountOwnership.own(Decimal("100.00")),
        status_id=ref_cache.status_id(StatusEnum.PROJECTED),
        category_id=td["categories"]["Rent"].id,
    )


def _reread(xfer_id):
    """Commit, expire every loaded object, and return the transfer's row re-read.

    What a door WROTE is what the database holds after the commit, not what
    the session's in-memory object says, so a case reads the row this returns
    (ruling R-BAL290 part 1), as ``test_transfer_service/_helpers.py``'s helper of the
    same name does.  The commit writes whatever a REFUSED door staged, so a
    refusal case reads what the session would have written.  The cases read
    the transfer's two legs, by side, until plan step
    ``balance:X-bi-6-4d-2``'s checkpoint 2e-2b-1b-3; the legs were two shadow
    rows until that step (ruling R-BAL167 class 4), and each is derived from
    the transfer now: a side's account is its endpoint column
    (``test_transfer_legs/test_what_a_side_shows.py``'s ``TestLegOf``) and its label is composed
    from the endpoints' current names (that file's
    ``TestALegsDisplayProjections``).
    """
    db.session.commit()
    db.session.expire_all()
    return db.session.get(Transfer, xfer_id)


class TestTheCreateDoor:
    """``create_transfer``: the card may be the destination, never the source."""

    def test_a_transfer_out_of_a_card_is_refused_and_writes_nothing(
        self, app, db, seed_full_user_data,
    ):
        """A card as ``from_account`` raises ValidationError before any write.

        A transfer OUT of a card is a cash advance or a balance transfer,
        which the card refuses rather than models (design 3.8).  The guard
        fires with the loan's, ahead of the first ``db.session.add``, so no
        transfer exists afterwards.

        It also counted the card's ``Transaction`` rows (none) until plan
        step ``balance:X-bi-6-4d-2``'s checkpoint 2e-2b-2b.  No transfer door
        writes a ``Transaction`` row since that step's create door, so the
        count read none whether the guard fired or not (a vacuous read, the
        mutant M-NOCARDGUARD of the review of its checkpoint 2e-1), and a
        guard asked after the create's first write fails the transfer count
        above first (measured at checkpoint 2e-2b-2b); it is dropped.
        """
        with app.app_context():
            td = seed_full_user_data
            card = _card(td)
            db.session.commit()
            transfers_before = db.session.query(Transfer).count()

            with pytest.raises(ValidationError, match="out of a credit card"):
                transfer_service.create_transfer(_spec(
                    td, from_account_id=card.id, to_account_id=td["account"].id,
                ))

            assert db.session.query(Transfer).count() == transfers_before

    def test_a_transfer_into_a_card_is_its_payment_and_is_allowed(
        self, app, db, seed_full_user_data,
    ):
        """The control: the same spec with the endpoints swapped succeeds.

        A transfer INTO the card is how the card is paid (design 3.5), so it
        must pass the guard that refuses the reverse.  The transfer exists
        from the spec's source to the card, which attributes the refusal above
        to the card being the SOURCE and to nothing else about a card.  Read
        off the row re-read from the database since the checkpoint
        :func:`_reread` names, where it read the transfer's destination, its
        legs' accounts and the income leg's label, so its source was asked
        only of the expense leg.
        """
        with app.app_context():
            td = seed_full_user_data
            card = _card(td)
            db.session.commit()

            xfer = transfer_service.create_transfer(_spec(
                td, from_account_id=td["account"].id, to_account_id=card.id,
            ))

            row = _reread(xfer.id)
            assert (row.from_account_id, row.to_account_id) == (
                td["account"].id, card.id,
            )


class TestTheEndpointMoveDoor:
    """``update_transfer``'s endpoint move: the same rule, asked on a SOURCE move."""

    def test_a_move_making_a_card_the_source_is_refused_before_any_write(
        self, app, db, seed_full_user_data,
    ):
        """Re-pointing a transfer's source onto a card raises, rows untouched.

        The create door refuses a card source; the edit path can move a
        source (plan step R10-b), so it inherits the rule -- otherwise the one
        guard would have a second door straight past it.  The refusal runs in
        ``_resolve_endpoints``, which writes nothing, so the transfer still
        names its pre-edit accounts when it raises: read off the row re-read
        after a commit, which writes whatever the refused update staged
        (:func:`_reread`).  A move assigns the endpoint RELATIONSHIPS
        (``_apply_endpoint_move``), whose columns sync only at a flush, so
        until the checkpoint :func:`_reread` names the case flushed, read the
        session's source column and relationship and both legs' accounts, and
        rolled the edit back; its destination was asked only of the income
        leg.
        """
        with app.app_context():
            td = seed_full_user_data
            card = _card(td)
            xfer = transfer_service.create_transfer(_spec(
                td,
                from_account_id=td["account"].id,
                to_account_id=td["savings_account"].id,
            ))
            db.session.commit()

            with pytest.raises(ValidationError, match="out of a credit card"):
                transfer_service.update_transfer(
                    xfer.id, td["user"].id, from_account_id=card.id,
                )

            row = _reread(xfer.id)
            assert (row.from_account_id, row.to_account_id) == (
                td["account"].id, td["savings_account"].id,
            )

    def test_a_move_of_the_destination_onto_a_card_is_allowed(
        self, app, db, seed_full_user_data,
    ):
        """The control: the same edit naming the card as DESTINATION lands.

        Moving where a transfer arrives onto a card turns it into that card's
        payment, which is allowed: the destination is the card and the source
        is where it was, read off the row re-read from the database since the
        checkpoint :func:`_reread` names, where it also read the legs'
        accounts and the expense leg's label; the legs were the two shadow
        rows, whose names the move re-wrote, until plan step
        ``balance:X-bi-6-4d-2``.
        """
        with app.app_context():
            td = seed_full_user_data
            card = _card(td)
            xfer = transfer_service.create_transfer(_spec(
                td,
                from_account_id=td["account"].id,
                to_account_id=td["savings_account"].id,
            ))
            db.session.commit()

            transfer_service.update_transfer(
                xfer.id, td["user"].id, to_account_id=card.id,
            )

            row = _reread(xfer.id)
            assert (row.from_account_id, row.to_account_id) == (
                td["account"].id, card.id,
            )

    def test_a_legacy_card_source_row_can_still_move_its_destination(
        self, app, db, seed_full_user_data,
    ):
        """The refusal is asked about a source MOVE, not about every edit.

        The loan's twin (``test_a_legacy_loan_SOURCE_row_can_still_move_its
        _destination``) pins the R10-b narrowing for the loan arm; this pins
        it for the card arm, so lifting the composed call out of the
        ``vacated_source`` gate is caught whichever arm a test reaches for.
        The legacy state is PLANTED, because no door produces it: a transfer
        whose source is already a card must move its DESTINATION without
        being re-graded for a shape the edit does not touch.  It is planted on
        the transfer alone since plan step ``balance:X-bi-6-4d-2``, where it was
        planted on the transfer AND its expense shadow: a leg is derived from
        its transfer, so there is no second row to plant it on (ruling
        R-BAL167 class 1).  The move is read off the row re-read from the
        database since the checkpoint :func:`_reread` names, where it read
        the session's columns and the income leg's account.
        """
        with app.app_context():
            td = seed_full_user_data
            card = _card(td)
            xfer = transfer_service.create_transfer(_spec(
                td,
                from_account_id=td["account"].id,
                to_account_id=td["savings_account"].id,
            ))
            db.session.flush()
            # The pre-refusal shape: money leaving a card.
            xfer.from_account = card
            db.session.commit()
            elsewhere = create_account_of_type(
                td, db.session, "Savings", "New Destination",
                anchor_balance=Decimal("0.00"),
            )
            db.session.flush()

            transfer_service.update_transfer(
                xfer.id, td["user"].id, to_account_id=elsewhere.id,
            )

            row = _reread(xfer.id)
            assert row.from_account_id == card.id, (
                "the source it did not touch stayed where it was"
            )
            assert row.to_account_id == elsewhere.id
