"""The one status door: the status and correction it applies, and the delete door's owner check."""

from datetime import timedelta

from app.utils.dates import display_today
from decimal import Decimal

import pytest

from app import ref_cache
from app.enums import MovementFigureSourceEnum, StatusEnum, TxnTypeEnum
from app.exceptions import NotFoundError, ValidationError
from app.extensions import db
from app.models.transaction import Transaction
from app.services import status_seam, transaction_service
from app.services.row_valuation import settled_figure
from tests._ledger_reads import (
    account_net,
    family_movement_ids,
    net_posted_by_day,
    owner_worked_out_lines,
)
from tests._records_oracle import posted_purchase_effect
from tests._test_helpers import an_entered_day, generate_row_of, typed
from app.models.amount_ownership import AmountOwnership

from ._helpers import _make_template


class TestApplyRequestedStatusTheDoorVerb:
    """``apply_requested_status`` -- the route layer's ONE status entry point.

    **It exists because a ROUTE was deciding what a status change means.**  The
    transaction PATCH handler and the cancel handler each called
    ``status_seam.apply_status_change`` -- the MECHANICS primitive, which
    assigns the column and posts nothing -- so the PATCH door could flip a row
    into the settled band without ever asking what the row was worth (finding
    **N-219**).  These grade the verb's status change, and the ledger it
    leaves.
    """

    @staticmethod
    def _plain_row(seed_user, period):
        """Return a Projected, NON-envelope $100.00 row: the verb's manual branch.

        The engine's row of a $100.00 definition (plan step balance:X-cf-3b);
        the figure is stated on the definition, which is what prices the row.
        """
        template = _make_template(
            seed_user, amount="100.00", is_envelope=False,
        )
        return generate_row_of(template, period)

    def test_a_status_change_books_the_rows_effect(
        self, app, seed_user, seed_periods,
    ):
        """A settled status books the row's effect, in the same call.

        A $100.00 expense settles through the verb and its movement books
        $100.00 out of the cash account on the settle day.
        """
        with app.app_context():
            txn = self._plain_row(seed_user, seed_periods[0])

            transaction_service.apply_requested_status(
                txn, ref_cache.status_id(StatusEnum.DONE),
            )

            assert txn.status_id == ref_cache.status_id(StatusEnum.DONE)
            assert txn.settled_on == display_today()
            # The row's FAMILY (plan step X-bi-3a): the $100.00 is booked under
            # the covering movement the seam wrote, on today, its settle day.
            assert net_posted_by_day(family_movement_ids(txn)) == {
                display_today(): Decimal("100.00"),
            }
            # effective_amount == estimated_amount == 100.00, nothing credited,
            # so the cash account's movement effect is a 100.00 outflow.
            assert posted_purchase_effect(
                seed_user["account"].id, seed_user["scenario"].id,
            ) == Decimal("-100.00")

    @staticmethod
    def _ledger_nets(user_id):
        """Return the owner's worked-out ledger, net per scenario, chart account and day.

        The ledger is worked out from the records, so a write of any kind that
        books money -- a line for this row, its movement, or anything else --
        moves a net here, and the same answer before and after a call is the
        whole of "it posted nothing".  It read the stored journal's entry ids
        until plan step plan#2914 took the store's grading off the suite.
        """
        nets = {}
        for scenario_id, line in owner_worked_out_lines(db.session, user_id):
            cell = (scenario_id, line.key, line.entry_date)
            nets[cell] = nets.get(cell, Decimal("0.00")) + line.amount
        return {cell: net for cell, net in nets.items() if net != 0}

    @staticmethod
    def _assert_the_ledger_is_not_empty(seed_user):
        """Assert the snapshot holds the seeded Checking's opening, so it is no empty one.

        Hand arithmetic: the seeded Checking opens at $1,000.00 and the
        Projected row books nothing, so Checking nets 1,000.00 + 0.00 =
        1,000.00.
        """
        assert account_net(
            db.session, seed_user["account"].id, seed_user["scenario"].id,
        ) == Decimal("1000.00")

    def test_a_cancel_leaves_the_ledger_flat(
        self, app, db, seed_user, seed_periods,
    ):
        """Cancelling a Projected row posts nothing.

        The non-settled arm.  A Projected row has never posted, so Cancelled
        leaves the ledger as it was -- the property the cancel handler used to
        spell out for itself and now inherits.  The settle day is cleared by
        the seam, because a cancelled row's money never moved.
        """
        with app.app_context():
            txn = self._plain_row(seed_user, seed_periods[0])
            ledger_before = self._ledger_nets(seed_user["user"].id)
            self._assert_the_ledger_is_not_empty(seed_user)

            transaction_service.apply_requested_status(
                txn, ref_cache.status_id(StatusEnum.CANCELLED),
            )

            assert txn.status_id == ref_cache.status_id(StatusEnum.CANCELLED)
            assert txn.settled_on is None
            assert self._ledger_nets(seed_user["user"].id) == ledger_before

    def test_an_illegal_transition_raises_and_posts_nothing(
        self, app, db, seed_user, seed_periods,
    ):
        """A refused transition leaves both the row and the ledger untouched.

        Cancelled -> Paid is not in the transaction workflow: a cancelled row
        must be reprojected first, so the audit trail records both moves.  The
        verb must refuse BEFORE it records anything -- a covering movement
        written for a status the state machine rejected would book an effect
        the row is not entitled to.
        """
        with app.app_context():
            txn = self._plain_row(seed_user, seed_periods[0])
            ledger_before = self._ledger_nets(seed_user["user"].id)
            self._assert_the_ledger_is_not_empty(seed_user)
            transaction_service.apply_requested_status(
                txn, ref_cache.status_id(StatusEnum.CANCELLED),
            )

            with pytest.raises(ValidationError) as exc:
                transaction_service.apply_requested_status(
                    txn, ref_cache.status_id(StatusEnum.DONE),
                )

            assert "transition" in str(exc.value)
            assert txn.status_id == ref_cache.status_id(StatusEnum.CANCELLED)
            assert self._ledger_nets(seed_user["user"].id) == ledger_before


class TestTheDoorAppliesTheStatusANDTheCorrection:
    """A status change and a figure correction are INDEPENDENT facts.

    **The door treated them as alternatives, and that dropped status changes on
    the floor** -- measured on 2026-08-18, not reasoned.  ``apply_requested_status``
    recorded a submitted figure and RETURNED, so a row moving Paid -> Settled
    while carrying a corrected Actual recorded the figure and stayed Paid,
    answering 200.  Both controls are on the same popover -- the Status dropdown
    sits beside the Actual box -- so a user correcting a figure on the way to
    filing the row away got half of what they asked for, silently.

    **The sharpest specimen is GONE and this says so rather than leaving the
    reader to notice.**  ``Paid -> Settled`` was a real status MOVE that a
    correction could ride, and plan step **balance:X-am** deleted the terminal
    ``Settled`` archive.  A settled row's only remaining status move is the
    REVERT, and a revert carrying a CHANGED figure is deliberately refused
    (below) rather than composed -- so the "move plus correction" case has no
    legal instance left and its test went with the status.

    What still grades the composition is the IDENTITY move below: the door is
    handed a status AND a figure in one call and must apply both, which is the
    property whose absence dropped status changes on the floor.  What is no
    longer graded is the same composition across a status BOUNDARY, and that is
    a real reduction -- recorded here, not absorbed.  The first status move
    added to a settled row's map owes this class a case.
    """

    #: A settle day that is NOT the user's today.  A settle stamps
    #: ``display_today()``, so on a today-dated row "the correction preserved
    #: the day" and "the seam re-stamped it" read the same and the assertion
    #: grades nothing -- finding **N-146**'s shape, which this arc has already
    #: paid for once.
    _SETTLED_DAY_OFFSET = timedelta(days=3)

    @classmethod
    def _settled_row(cls, seed_user, period):
        """Return a $100.00 row settled at its plan, on a PAST day, through the real verb.

        The engine's row of a $100.00 definition (plan step balance:X-cf-3b).
        """
        template = _make_template(
            seed_user, amount="100.00", is_envelope=False,
        )
        txn = generate_row_of(template, period)
        transaction_service.apply_requested_status(
            txn, ref_cache.status_id(StatusEnum.DONE),
            settle_day=an_entered_day(display_today() - cls._SETTLED_DAY_OFFSET),
        )
        return txn

    def test_a_correction_with_a_status_IN_HAND_does_BOTH(
        self, app, seed_user, seed_periods,
    ):
        """The identity case: the figure lands, the status stays, the ledger moves.

        The composition, and since plan step **balance:X-am** the only legal
        instance of it: the door is handed a status AND a figure in ONE call
        and must apply both.  It used to record the figure and RETURN, so the
        status half was dropped -- measured 2026-08-18 on ``Paid -> Settled``,
        the move that no longer exists.

        Hand arithmetic: the row settles at its $100.00 plan, so the cash
        account is $100.00 down.  The correction restates the figure as
        $87.10, so the settled effect becomes -$87.10 -- the figure restated,
        not a second full booking.

        The settle day is preserved, because a figure correction moves no day
        and ruling **R-FL** releases the clearing link on the DAY alone.
        """
        with app.app_context():
            txn = self._settled_row(seed_user, seed_periods[0])
            assert settled_figure(txn) == Decimal("100.00")
            day = txn.settled_on

            transaction_service.apply_requested_status(
                txn, txn.status_id,
                stated=transaction_service.StatedRecord(figure=typed(Decimal("87.10"))),
            )

            assert txn.status_id == ref_cache.status_id(StatusEnum.DONE)
            assert settled_figure(txn) == Decimal("87.10")
            assert status_seam.recorded_settlement(txn).source is (
                MovementFigureSourceEnum.TYPED
            )
            assert txn.settled_on == day, (
                "a figure correction moved the settle day"
            )
            # The worked-out ledger: what the row's records book, per day.
            assert net_posted_by_day(
                family_movement_ids(txn),
            ) == {day: Decimal("87.10")}

    def test_a_REVERT_carrying_a_figure_is_refused_and_changes_nothing(
        self, app, db, seed_user, seed_periods,
    ):
        """Two contradictory facts in one call is a programming error, not a save.

        A revert says the money did not move; a figure says this much did.  The
        door used to record the figure, post the ledger difference, and never
        revert -- booking money for a row it had just been told had not moved.
        It refuses now, BEFORE the seam runs, so the row is untouched.

        A FORM reaches this whenever the user CHANGED the box, which is the
        point: an untouched prefill is still dropped on the way out of the band
        (ruling **R-EG**, so the unlock path keeps working), and only a figure
        that differs from the record is refused.  ``figure_for_status`` draws
        that line, and until it did both were discarded.
        """
        with app.app_context():
            txn = self._settled_row(seed_user, seed_periods[0])
            # The LEDGER before, from the worked-out lines rather than the row --
            # see the archive test for why the row-tier reader is not an oracle.
            # The row's FAMILY, its movements' entries: it read the row's own
            # ``transaction_id`` link until card plan#1845 deleted it, which
            # had found nothing since plan step ``balance:X-bi-4a``.
            ledger_before = net_posted_by_day(family_movement_ids(txn))
            assert ledger_before == {txn.settled_on: Decimal("100.00")}

            with pytest.raises(ValidationError) as exc:
                transaction_service.apply_requested_status(
                    txn, ref_cache.status_id(StatusEnum.PROJECTED),
                    stated=transaction_service.StatedRecord(figure=typed(Decimal("123.45"))),
                )

            assert "has nothing to record" in str(exc.value)
            assert txn.status_id == ref_cache.status_id(StatusEnum.DONE)
            assert settled_figure(txn) == Decimal("100.00")
            assert net_posted_by_day(family_movement_ids(txn)) == ledger_before, (
                "a refused request posted to the ledger anyway"
            )

    def test_an_ECHO_leaves_the_derived_basis_standing(
        self, app, db, seed_user, seed_periods,
    ):
        """Re-posting the prefilled figure must not manufacture a correction.

        The echo rule's firing control at this tier: the movement's source is
        the only stored signal that a human read a number off a statement,
        and an untouched Save posts the box's contents back on every edit.
        """
        with app.app_context():
            txn = self._settled_row(seed_user, seed_periods[0])
            resolved = MovementFigureSourceEnum.RESOLVED
            assert status_seam.recorded_settlement(txn).source is resolved

            transaction_service.apply_requested_status(
                txn, txn.status_id,
                stated=transaction_service.StatedRecord(figure=typed(Decimal("100.00"))),
            )

            assert status_seam.recorded_settlement(txn).source is resolved


class TestTheDeleteDoorReconcilesItsOwnerArgument:
    """``delete_transaction`` checks the owner it is handed against the row.

    Finding **N-373**, landed at plan step **pay_calendar:C13-b**.  This door
    took an ``owner_id`` for its events and reconciled it against the row
    NOWHERE, while its sibling ``entry_service.delete_entry`` re-validated --
    so of the two doors ``statement_match._release._remove`` calls, one asked
    and one trusted.  Both live callers prove ownership first, so the finding
    measured ``$0.00`` and no reachable defect; what it names is a PERMISSION
    a third caller would inherit.

    It is ONE comparison because ``budget.transactions`` carries its owner as
    a COLUMN since plan step ``C13-a``.  Before that the predicate was a join
    walk through the paycheck, which is why the finding was sequenced behind
    the key rather than fixed when it was raised.

    The refusal is a ``NotFoundError`` naming only the row's id -- the same
    shape and the same silence as the sibling door, per the security response
    rule that "not yours" and "not found" read alike.
    """

    def test_a_foreign_owner_is_refused(
        self, app, seed_user, seed_periods, second_user,
    ):
        """A caller naming another user as the owner gets the 404 shape."""
        with app.app_context():
            template = _make_template(seed_user)
            txn = generate_row_of(template, seed_periods[0])
            db.session.flush()

            with pytest.raises(NotFoundError) as exc:
                transaction_service.delete_transaction(
                    txn, second_user["user"].id,
                )
            assert str(txn.id) in str(exc.value)
            assert not txn.is_deleted

    def test_the_owner_is_refused_BEFORE_the_deletion_rules(
        self, app, seed_user, seed_periods, second_user,
    ):
        """The ownership arm runs ahead of ``deletion_refusal``.

        Ordering is the half a status code cannot show: a foreign caller must
        be told NOTHING about the row, so the ownership refusal has to come
        before the rules that name WHY a row may not be deleted.  The row here
        is one ``deletion_refusal`` rejects by name -- a CC payback -- so the
        two arms give different exceptions and the order is observable: put
        ownership second and this call comes back as a ``ValidationError``
        whose sentence tells a stranger the row is a payback.

        The OWNER's own call is made too, and it must reach that
        ``ValidationError``: without it this case would also pass on a door
        that refused everything.
        """
        with app.app_context():
            template = _make_template(seed_user)
            source = generate_row_of(template, seed_periods[0])
            # Built directly: a payback carries no template, because
            # ``ck_transactions_one_pricing_link`` allows a row exactly one
            # pricing parent.
            payback = Transaction(
                user_id=seed_user["user"].id,
                pay_period_id=seed_periods[1].id,
                scenario_id=seed_user["scenario"].id,
                account_id=seed_user["account"].id,
                status_id=ref_cache.status_id(StatusEnum.PROJECTED),
                name="Tracked Expense (payback)",
                category_id=seed_user["categories"]["Groceries"].id,
                transaction_type_id=ref_cache.txn_type_id(TxnTypeEnum.EXPENSE),
                amount_ownership=AmountOwnership.own(Decimal("500.00")),
                credit_payback_for_id=source.id,
            )
            db.session.add(payback)
            db.session.flush()

            with pytest.raises(NotFoundError) as exc:
                transaction_service.delete_transaction(
                    payback, second_user["user"].id,
                )
            assert "payback" not in str(exc.value).lower()

            with pytest.raises(ValidationError):
                transaction_service.delete_transaction(
                    payback, seed_user["user"].id,
                )
