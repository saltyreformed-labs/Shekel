"""A loan payment's legs read the loan through the declaration, an owner-typed figure shows before
it settles, and a mode flip rewrites no row.
"""

from decimal import Decimal

from app import ref_cache
from app.enums import StatusEnum
from app.services import loan_ledger, transfer_service
from app.services.cash_ledger import derived_amount_basis, leg_amounts_by_key
from app.services.transfer_legs import leg_of
from tests._test_helpers import transfer_amount, transfer_side_figure
from tests.test_integration.test_transfer_settle_freeze import _derived_loan_transfer
from app.models.amount_ownership import AmountOwnership
from app.services import template_amount_service

from ._helpers import _CONTRACT, _TYPED, _reread


#: 200,000 * 0.06 / 12, the first installment's interest.
_INTEREST = Decimal("1000.00")


_ESCROW = Decimal("300.00")


def _both_legs(xfer):
    """Return *xfer*'s two legs, from-side first, as the grid would draw them.

    :func:`~app.services.transfer_legs.leg_of` on each endpoint: the value a
    surface prices with :func:`~app.services.cash_ledger.leg_amounts_by_key`,
    which is how a leg is read since plan step ``balance:X-bi-6-1`` (ruling
    **R-BAL87**).  These cases priced the two shadow ROWS with
    ``amounts_by_id`` until plan step ``balance:X-bi-6-4d-2`` stopped the
    shadows being born (ruling **R-BAL167** class 1).
    """
    return [leg_of(xfer, xfer.from_account_id), leg_of(xfer, xfer.to_account_id)]


class TestALoanPaymentsLegsReadTheLoan:
    """Rule 4 through the declaration, with no read-time repair in front."""

    def test_both_legs_are_worth_the_contract_not_the_stored_figure(
        self, app, seed_user, seed_periods,
    ):
        """The definition's ``$1.00`` is not what either leg is worth.

        The fixture's definition states a deliberately stale ``$1.00``, so a leg
        worth ``$1.00`` is one the loan did not price.  It asked both legs
        because the cutover declared both shadow rows, and a version declaring
        only the checking side would have left the loan-side income leg
        answering the parent.  Since plan step balance:X-bi-6-4d-2 both legs
        resolve through ONE ``resolve_transfer_amount`` call on one transfer,
        so the two cannot differ: ``len(priced) == 2`` checks that the two
        legs' keys are distinct, not that two answers agree.

        **The parent stores NOTHING since plan step X-au-f**, and the assertion
        below is what replaces ``xfer.amount == Decimal("1.00")``: the stale
        figure the legs had to beat is not merely unread now, it is gone, and
        what the legs must beat is the SERIES the definition still states.  The
        discrimination is the same -- ``$1.00`` against the contract's
        ``$1,499.10`` -- and one stored copy shorter.

        **The legs are the transfer's since plan step balance:X-bi-6-4d-2**
        (ruling **R-BAL167** class 1): both are priced through
        ``leg_amounts_by_key``, where the two shadow rows were priced through
        ``amounts_by_id``, and the per-leg "stores nothing" check went with the
        rows -- the one row left is asserted storing nothing above it.
        """
        with app.app_context():
            xfer = _derived_loan_transfer(seed_user, seed_periods)
            basis = derived_amount_basis(
                seed_user["user"].id, seed_user["scenario"].id,
            )

            assert xfer.amount is None
            assert template_amount_service.amount_as_of(
                xfer.template, xfer.due_date,
            ) == Decimal("1.00")
            priced = leg_amounts_by_key(_both_legs(xfer), basis)
            assert len(priced) == 2
            assert set(priced.values()) == {_CONTRACT}

    def test_a_typed_figure_beats_the_contract_and_reaches_the_split(
        self, app, db, seed_user, seed_periods,
    ):
        """Ruling **R-IO**, end to end, on an UNDERPAYMENT.

        The owner types ``$1,325.00`` over a ``$1,499.10`` contractual
        installment and marks it Paid in one save -- the shape the transfers
        page sends, which auto-sets ``is_override`` when the amount box moves.

        Two things must hold and the second is what the ruling added.  The
        settle books what the owner typed, not the contract: booking
        ``$1,499.10`` would report a short payment as exactly on schedule and
        lose the ``$174.10``.  And that figure is the CASH the split allocates,
        so the loan's own books show the shortfall where it fell --
        interest ``$1,000.00`` (200,000 * 0.06 / 12) and escrow ``$300.00`` are
        taken in full, and principal absorbs the rest at
        ``1,325.00 - 1,000.00 - 300.00 = 25.00`` instead of the ``$199.10`` a
        full installment would have paid down.

        What each side booked is read off its record, where it was read off
        each twin row (ruling R-BAL167 class 1, plan step balance:X-bi-6-4d-2).
        """
        with app.app_context():
            xfer = _derived_loan_transfer(seed_user, seed_periods)
            loan_id = xfer.to_account_id
            scenario_id = seed_user["scenario"].id

            transfer_service.update_transfer(
                xfer.id, seed_user["user"].id,
                amount_ownership=AmountOwnership.own(_TYPED),

                is_override=True,
                status_id=ref_cache.status_id(StatusEnum.DONE),
            )
            db.session.commit()

            db.session.expire_all()
            for account_id in (xfer.from_account_id, loan_id):
                assert transfer_side_figure(
                    db.session, xfer.id, account_id,
                ) == _TYPED

            splits = loan_ledger.compute_loan_payment_splits(
                loan_id, scenario_id,
            )
            assert len(splits) == 1
            split = splits[0]
            assert split.interest == _INTEREST
            assert split.escrow == _ESCROW
            assert split.principal == _TYPED - _INTEREST - _ESCROW
            assert (
                split.interest + split.escrow + split.principal + split.excess
                == _TYPED
            )


class TestAnOwnerTypedFigureShowsBeforeItSettles:
    """R-IO on the PROJECTED row, not only through the settle."""

    def test_the_grid_shows_the_typed_figure_not_the_contract(
        self, app, db, seed_user, seed_periods,
    ):
        """What a screen shows for a hand-priced loan payment is the owner's figure.

        ``test_a_typed_figure_beats_the_contract_and_reaches_the_split`` grades
        ruling **R-IO** through the SETTLE, and an adversarial review pointed
        out that leaves the projected display ungraded: a regression that wrote
        both legs correctly but left ``amount_rule`` still classifying them
        ``LOAN_PAYMENT`` would book the right figure and show the wrong one for
        every day between the edit and the settle.

        What the grid publishes for a transfer's legs is asked here: it was
        ``amounts_by_id`` over the shadow rows (ruling **R-Q**), and since
        plan step ``balance:X-bi-6-1`` it is ``leg_amounts_by_key`` over the
        legs (``routes/grid/_items.py``); this case asked the first until plan
        step balance:X-bi-6-4d-2 stopped the shadows being born (ruling
        R-BAL167 class 1).
        """
        with app.app_context():
            xfer = _derived_loan_transfer(seed_user, seed_periods)
            basis = derived_amount_basis(
                seed_user["user"].id, seed_user["scenario"].id,
            )
            assert set(leg_amounts_by_key(_both_legs(xfer), basis).values()) == {
                _CONTRACT,
            }

            transfer_service.update_transfer(
                xfer.id, seed_user["user"].id,
                amount_ownership=AmountOwnership.own(_TYPED),  is_override=True,
            )
            db.session.commit()

            fresh = derived_amount_basis(
                seed_user["user"].id, seed_user["scenario"].id,
            )
            shown = leg_amounts_by_key(_both_legs(_reread(xfer.id)), fresh)
            assert len(shown) == 2
            assert set(shown.values()) == {_TYPED}


class TestAModeFlipRewritesNoRow:
    """Why ruling R-IN's wider scope needs no writer on the loan routes."""

    def test_track_payment_changes_what_the_legs_are_worth_without_touching_them(
        self, app, db, auth_client, seed_user, seed_periods,
    ):
        """Flipping a MANUAL payment to auto-track re-prices its existing rows.

        **This is the case that decided the scope** (ruling **R-IN**).  The loan
        dashboard's one-click "track the contract" creates or flips the
        ``loan_payment_settings`` row on a definition whose transfers are
        already generated.  Under the narrower design -- only loan-payment
        shadows declared -- those legs would still have been OWNING a stale
        figure at that moment, so the route would have needed a writer to
        declare them, and the button's own docstring promise ("no shadow
        regeneration is needed") would have become false.

        Every transfer shadow is derived from birth, so the flip writes no row
        at all: what changes is which RULE prices them, read live off the
        template (ruling **R-FK**).

        **The ``updated_at`` assertion below is NOT the measurement a draft of
        this docstring claimed it was**, and saying so beats leaving a stronger
        claim standing.  It named the mutation "a writer added to
        ``track_payment`` to declare the legs" -- but that writer is
        ``declare_derived``, which would set ``estimated_amount`` to the ``None``
        it already holds and the source to the id it already holds.  SQLAlchemy
        emits no UPDATE for a net-zero attribute history, so ``updated_at``'s
        ``onupdate`` never fires and the assertion passes.  It can only catch a
        writer that changes a VALUE, which is a weaker and different thing.

        What the case does hold: the legs' declaration is unchanged across the
        flip, and what they are WORTH moves from the parent's stale ``$1.00`` to
        the loan's ``$1,499.10`` -- so the route reprices without a writer, which
        is the scope argument (**R-IN**) itself.

        **Asked of the TRANSFER since plan step balance:X-bi-6-4d-2** (ruling
        **R-BAL167** class 1): no shadow row is born, both legs are read off
        the transfer (**R-BAL13**), so what they are worth is
        :func:`~tests._test_helpers.transfer_amount` and the row the flip must
        not write is the transfer -- its declaration (``TEMPLATE``) and its
        ``updated_at``, stamped where each shadow's were, with the caveat
        above unchanged.  The figures are the same two.
        """
        with app.app_context():
            xfer = _derived_loan_transfer(seed_user, seed_periods)
            loan_id = xfer.to_account_id
            xfer_id = xfer.id
            xfer.template.settings.derive_from_loan = False
            db.session.commit()

            before = _reread(xfer_id)
            assert transfer_amount(before) == Decimal("1.00")
            stamp = (before.amount_source_id, before.updated_at)

            resp = auth_client.post(
                f"/accounts/{loan_id}/loan/payments/{before.template.id}/track",
            )
            assert resp.status_code in (200, 302), resp.data

            after = _reread(xfer_id)
            assert transfer_amount(after) == _CONTRACT
            assert after.amount is None
            assert (after.amount_source_id, after.updated_at) == stamp
