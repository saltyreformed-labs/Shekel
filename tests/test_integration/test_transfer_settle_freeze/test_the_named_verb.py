"""The named settle verb, and a transfer's offer is what its re-settle books."""

import logging
from datetime import timedelta
from decimal import Decimal

import pytest

from app import ref_cache
from app.enums import MovementFigureSourceEnum, StatusEnum
from app.exceptions import ValidationError
from app.services import status_seam, transaction_service, transfer_service
from app.utils.dates import display_today
from tests._test_helpers import (
    on_both_sides,
    typed,
    amount_basis_for,
    an_entered_day,
    transfer_side_figure,
    transfer_side_record,
)
from tests.test_integration.test_loan_transfer_live_amount._helpers import _side_leg
from app.services.row_valuation import settled_contribution
from app.services.transfer_legs import parent_of

from ._helpers import _STALE, _derived_loan_transfer, _plain_transfer, _sides, _side_settlement


class TestTheNamedVerbItself:
    """``settle_transfer``: the surface every settle-only door now calls.

    Its behaviour was reachable only THROUGH those doors until an adversarial
    review pointed out that a public verb with no direct case is a verb whose
    own contract is ungraded -- and then found a real defect in exactly the
    branch nothing exercised.
    """

    def test_it_returns_whether_a_HUMAN_s_figure_was_booked(
        self, app, db, seed_user, seed_periods,
    ):
        """The return value is what the reconcile writer counts (**N-231**).

        Three shapes in one case, because the answer is a three-way decision
        and grading one arm would leave the other two free to invert: nobody
        typed a figure, somebody typed the panel's own prefill back, and
        somebody typed a different one.  The record is read off each
        transfer's from-side record, not its expense twin (ruling R-BAL167
        class 1, plan step balance:X-bi-6-4d-2).
        """
        with app.app_context():
            nothing_typed = _plain_transfer(
                seed_user, seed_periods, destination="Savings",
            )
            echoed = _plain_transfer(
                seed_user, seed_periods, amount="120.00",
                destination="Vacation",
            )
            corrected = _plain_transfer(
                seed_user, seed_periods, amount="80.00",
                destination="Emergency",
            )
            db.session.commit()
            owner = seed_user["user"].id

            assert transfer_service.settle_transfer(
                nothing_typed.id, owner,
            ) is False
            assert transfer_service.settle_transfer(
                echoed.id, owner, submitted=typed(Decimal("120.00")),
            ) is False
            assert transfer_service.settle_transfer(
                corrected.id, owner, submitted=typed(Decimal("95.50")),
            ) is True

            db.session.commit()
            db.session.expire_all()
            # And the RECORD follows the same three-way decision: the two
            # uncorrected settles book what they resolved on the ``resolved``
            # source, the corrected one books the human's figure and says so.
            def _record(transfer):
                return _side_settlement(transfer.id, transfer.from_account_id)
            assert _record(nothing_typed).source is MovementFigureSourceEnum.RESOLVED
            assert _record(echoed) == status_seam.Settlement(
                Decimal("120.00"), MovementFigureSourceEnum.RESOLVED,
            )
            assert _record(corrected) == status_seam.Settlement(
                Decimal("95.50"), MovementFigureSourceEnum.TYPED,
            )

    def test_settling_an_ALREADY_settled_transfer_writes_nothing(
        self, app, db, seed_user, seed_periods,
    ):
        """A settle is idempotent, and the defect this grades was measured.

        ``enters_settled_band`` is False for ``done -> done``, so no settle
        runs -- and before plan step X-f2-c3's review the kwargs fell through
        to the ordinary field arms anyway: ``actual_amount`` was written
        VERBATIM past the echo rule, and ``settled_on`` re-dated both legs
        through ruling **R-ED**'s correction door, moving the posted
        ``entry_date`` (findings **N-146** / **N-178**).  The verb returned
        ``False`` throughout, so the reconcile writer's count under-reported a
        write it had just made.

        Shown to FIRE: deleting the ``settle_only`` arm in
        ``_apply_transfer_updates`` fails every assertion below.  Each side's
        record and day are read off the side's record, not its twin row
        (ruling R-BAL167 class 1, plan step balance:X-bi-6-4d-2).
        """
        with app.app_context():
            xfer = _plain_transfer(seed_user, seed_periods)
            db.session.commit()
            owner = seed_user["user"].id
            first_day = display_today() - timedelta(days=3)

            transfer_service.settle_transfer(
                xfer.id, owner,
                side_days=on_both_sides(
                    xfer.from_account_id, xfer.to_account_id,
                    an_entered_day(first_day),
                ),
            )
            db.session.commit()
            db.session.expire_all()
            assert transfer_side_record(
                db.session, xfer.id, xfer.from_account_id,
            ).settled_on == first_day

            # A stale tab replays the settle, carrying a figure and a later day.
            assert transfer_service.settle_transfer(
                xfer.id, owner,
                submitted=typed(Decimal("999.99")),
                side_days=on_both_sides(
                    xfer.from_account_id, xfer.to_account_id,
                    an_entered_day(display_today()),
                ),
            ) is False
            db.session.commit()

            db.session.expire_all()
            for account_id in _sides(xfer.id):
                # The echoed-past-the-rule write did not happen: the record
                # still says ``resolved`` at what the FIRST settle booked, not
                # ``typed`` at the replayed $999.99.
                assert _side_settlement(xfer.id, account_id).source is (
                    MovementFigureSourceEnum.RESOLVED
                )
                assert transfer_side_figure(
                    db.session, xfer.id, account_id,
                ) == Decimal("250.00")
                assert settled_contribution(
                    parent_of(_side_leg(xfer.id, account_id)),
                ) == Decimal("250.00")
                # ... and the day the money moved was not moved.
                assert transfer_side_record(
                    db.session, xfer.id, account_id,
                ).settled_on == first_day

    # ``test_a_derived_freeze_emits_its_own_event`` lived here until plan step
    # X-au-f, and it is DELETED with the event it graded (ruling **R-BAL12**).
    # ``EVT_TRANSFER_AMOUNT_FROZEN``'s predicate was
    # ``booked != rows.transfer.amount``; that column is EMPTY for a generated
    # transfer now, so the comparison fired on EVERY settle including a plain
    # savings transfer (finding **N-451**), and re-pointing it at the RESOLVED
    # figure makes it fire on NONE -- the two sides then share one producer and
    # the comparison is an identity.  An event that cannot fire is a fence the
    # design made unnecessary, so it went with its predicate, its ``log_events``
    # registration and this case rather than being kept as a green check that
    # measures nothing.  What it recorded is on the record instead: the
    # covering movement's ``figure_source_id`` says whether a booked figure
    # was the app's own resolution or a human's correction, for EVERY settle
    # rather than for the subset a predicate happened to select.

    def test_a_manual_payment_with_NO_extra_books_its_series_price(
        self, app, db, seed_user, seed_periods, caplog,
    ):
        """A manual payment with no extra books what its DEFINITION states.

        **Renamed at plan step X-au-f**, with the event it was named for
        (ruling **R-BAL12**): "reports no freeze" described a log assertion that
        no longer exists, and a name is the first thing the next reader trusts.
        What survives is the money half, which is the half that could ever fail.

        A MANUAL payment with no standing extra books exactly what its transfer
        states, so nothing was derived over the operator's head and there is
        nothing to record.  The deleted ``LoanPricing.live_cash`` answered
        ``None`` for this shape -- its candidate map admitted only derive-mode
        payments and manual ones carrying an extra -- so the old
        ``frozen is not None`` predicate emitted nothing here.

        **A first replacement asked the amount model for the RULE**
        (``AmountRule.LOAN_PAYMENT``), which is true of ANY transfer whose
        template carries a settings row, so the event began firing on settles
        where no derivation decided anything -- wider than what it replaced, and
        contradicting the sentence the code writes about itself.  An adversarial
        review of this step found it.  The predicate compares the BOOKED figure
        against the transfer's own, which reproduces the old extension exactly.
        """
        with app.app_context():
            xfer = _derived_loan_transfer(seed_user, seed_periods)
            xfer.template.settings.derive_from_loan = False
            xfer.template.settings.extra_principal = Decimal("0.00")
            db.session.commit()

            with caplog.at_level(logging.INFO):
                transfer_service.settle_transfer(xfer.id, seed_user["user"].id)
                db.session.commit()

            # It books what its DEFINITION states, so nothing was derived over
            # anybody's head.  The log assertion that stood beside this went
            # with ``EVT_TRANSFER_AMOUNT_FROZEN`` at plan step X-au-f (ruling
            # **R-BAL12**); this half is the one that could ever fail, and the
            # case is kept for it -- a manual payment with no extra must book
            # its series price and not the loan's contract.  Read off each
            # side's record, not its twin row (ruling R-BAL167 class 1, plan
            # step balance:X-bi-6-4d-2).
            for account_id in _sides(xfer.id):
                assert transfer_side_figure(
                    db.session, xfer.id, account_id,
                ) == _STALE

    def test_a_re_settle_HONOURS_a_retained_correction(
        self, app, db, seed_user, seed_periods, caplog,
    ):
        """A re-settle honours the figure a human retained.

        **Renamed at plan step X-au-f** with ``EVT_TRANSFER_AMOUNT_FROZEN``
        (ruling **R-BAL12**); the money assertion is what it always graded.

        Settle with a correction, revert -- which RELEASES the assertion and
        KEEPS what moved (plan step X-au-c3) -- then settle again with nobody
        typing anything.  The second settle HONOURS the retained ``$1,512.44``,
        so no derivation decided the figure.

        **The guard did not test ``held`` until this step**, so that second
        settle logged ``transfer_amount_frozen`` carrying the DERIVATION's
        ``$1,499.10`` as though it were the booked figure -- two numbers apart,
        in a record whose whole purpose is to say which was booked.  The fix
        shipped with no test until this case; the ``frozen_amount`` assertion in
        its sibling above cannot see it, because that path never retains.
        Each side's figure is read off its record, not its twin row (ruling
        R-BAL167 class 1, plan step balance:X-bi-6-4d-2).
        """
        with app.app_context():
            xfer = _derived_loan_transfer(seed_user, seed_periods)
            user_id = seed_user["user"].id
            corrected = Decimal("1512.44")

            transfer_service.settle_transfer(
                xfer.id, user_id, submitted=typed(corrected),
            )
            db.session.commit()
            transfer_service.update_transfer(
                xfer.id, user_id,
                status_id=ref_cache.status_id(StatusEnum.PROJECTED),
            )
            db.session.commit()

            with caplog.at_level(logging.INFO):
                transfer_service.settle_transfer(xfer.id, user_id)
                db.session.commit()

            db.session.expire_all()
            for account_id in _sides(xfer.id):
                assert transfer_side_figure(
                    db.session, xfer.id, account_id,
                ) == corrected

    def test_a_settle_carrying_a_CORRECTION_books_the_humans_figure(
        self, app, db, seed_user, seed_periods, caplog,
    ):
        """A human's correction beats the derivation and is what gets booked.

        **Renamed at plan step X-au-f** with the event (ruling **R-BAL12**).

        A human's correction beats the freeze, so the figure booked is theirs
        and no freeze happened.  Without this the event could be emitted on
        every settle of a loan payment and still pass its sibling above.  Each
        side's figure is read off its record, not its twin row (ruling
        R-BAL167 class 1, plan step balance:X-bi-6-4d-2).
        """
        with app.app_context():
            xfer = _derived_loan_transfer(seed_user, seed_periods)
            transfer_service.settle_transfer(
                xfer.id, seed_user["user"].id,
                submitted=typed(Decimal("1512.44")),
            )
            db.session.commit()

            db.session.expire_all()
            for account_id in _sides(xfer.id):
                assert transfer_side_figure(
                    db.session, xfer.id, account_id,
                ) == Decimal("1512.44")


class TestATransfersOfferIsWhatItsReSettleBOOKS:
    """The TRANSFER half of finding **C1**, which nothing graded.

    ``transaction_service.settle_amount`` answers a retained ``corrected``
    record before it prices anything, for a row and (since ruling plan#1983's
    settle fold deleted ``transfer_service.leg_settle_amount``) a transfer, so
    the reconcile panel's PREFILL equals what a tick BOOKS on either kind of
    row.  The transaction half is pinned by
    ``test_full_edit_settle_door.TestWhatAReSettleBooksIsWhatTheOfferSHOWED``;
    the transfer half was pinned by nothing.

    **Measured 2026-08-17 by an adversarial mutation pass**: deleting the
    ``honoured_correction`` arm from ``transfer_service._settle.settle_amount``
    left the whole 9,626-test suite GREEN.  ``settle()`` reads the retained
    correction independently, so the mutant separates the two -- the panel
    offers the row's derived figure and the tick books the retained one, which
    is exactly the defect the transaction side documents and refuses.
    """

    def test_the_offer_equals_the_booking_on_a_reverted_transfer(
        self, app, db, seed_user, seed_periods,
    ):
        """A reverted transfer offers the figure it will re-book, not its plan.

        Shown to FIRE (against ``settle_amount``, before plan step
        ``balance:X-bi-6-4d-2``): dropping its ``honoured_correction`` arm
        makes the offer ``$250.00`` while the settle still books ``$95.50``.

        The retained figure is the transfer's, and the offer is asked of the
        from-side LEG's parent (``settle_amount(parent_of(leg))``, ruling
        plan#1983; ``leg_settle_amount`` of the leg until that ruling's settle
        fold), where both were asked of the expense twin until plan step
        balance:X-bi-6-4d-2 (ruling R-BAL167 class 1).
        """
        with app.app_context():
            xfer = _plain_transfer(seed_user, seed_periods, amount="250.00")
            db.session.commit()
            owner = seed_user["user"].id
            xfer_id = xfer.id

            # Settle at a HUMAN's figure, then revert -- the round trip the
            # full-edit card instructs.
            assert transfer_service.settle_transfer(
                xfer_id, owner, submitted=typed(Decimal("95.50")),
            ) is True
            db.session.commit()
            transfer_service.update_transfer(
                xfer_id, owner,
                status_id=ref_cache.status_id(StatusEnum.PROJECTED),
            )
            db.session.commit()
            db.session.expire_all()

            checking = xfer.from_account_id
            # The record SURVIVES the revert and the assertion does not.
            assert _side_settlement(xfer_id, checking) == status_seam.Settlement(
                Decimal("95.50"), MovementFigureSourceEnum.TYPED,
            )
            assert transfer_side_record(
                db.session, xfer_id, checking,
            ).settled_on is None

            # THE CLAIM: what the panel would offer is what a tick will book.
            leg = _side_leg(xfer_id, checking)
            offered = transaction_service.settle_amount(
                parent_of(leg), amount_basis_for(leg),
            )
            assert offered == Decimal("95.50"), (
                f"the panel would offer {offered} for a transfer whose "
                "re-settle books its retained correction"
            )

            transfer_service.settle_transfer(xfer_id, owner)
            db.session.commit()
            db.session.expire_all()
            assert transfer_side_figure(db.session, xfer_id, checking) == offered

    def test_a_deleted_transfer_is_refused_by_the_rows_guard_in_its_words(
        self, app, db, seed_user, seed_periods,
    ):
        """A soft-deleted transfer's offer is refused by the one guard, naming no id.

        ``settle_amount`` refuses a deleted parent whatever its table
        (``transaction_service.reject_unsettleable``, ruling plan#1983's
        settle fold).  The transfer twin it replaced refused with "Transfer
        <id> is soft-deleted; a settle cannot resurrect a deleted transfer.",
        showing the owner an id (ruling R-CC98 forbids one); the seam's
        sentence names the transfer by its from-side's label, the words its
        Transfer arm refuses a deleted transfer's record with.  Shown to fire
        by restoring the twin's sentence: the first assertion fails.
        """
        with app.app_context():
            xfer = _plain_transfer(seed_user, seed_periods, amount="250.00")
            db.session.commit()
            leg = _side_leg(xfer.id, xfer.from_account_id)
            xfer.is_deleted = True
            db.session.flush()

            with pytest.raises(ValidationError) as exc:
                transaction_service.settle_amount(
                    parent_of(leg), amount_basis_for(leg),
                )

            message = str(exc.value)
            assert "was deleted: a payment cannot be recorded under it" in (
                message
            )
            assert f"Transfer {xfer.id}" not in message
