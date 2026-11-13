"""The popover's Actual box (ruling R-FF): rendered exactly where the settle honours a typed
figure, and what a re-settle books is what the offer showed.
"""

from decimal import Decimal

from app import ref_cache
from app.enums import MovementFigureSourceEnum, StatusEnum
from app.extensions import db
from app.models.transaction import Transaction
from app.services import status_seam, transaction_service
from app.services.cash_ledger import resolve_transaction_amount
from app.services.row_valuation import settled_figure
from tests._test_helpers import amount_basis_for, create_envelope_txn, typed

from ._helpers import _gas_envelope, _full_edit_save


class TestTheActualBoxExistsOnlyWhereTheSettleHonoursIt:
    """Ruling **R-FF** on the popover: correctable iff the MANUAL branch."""

    def test_an_envelope_with_purchases_renders_no_actual_input(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """The box the settle would ignore is not drawn.

        The reconcile panel already obeys this rule (``is_correctable``); this
        is the second door that offers an amount box.  A box whose figure the
        verb discards takes the user's typed number and drops it, silently, on
        a screen whose whole job is entering the true one.
        """
        with app.app_context():
            txn = _gas_envelope(seed_user, seed_periods_today[3])

            resp = auth_client.get(f"/transactions/{txn.id}/full-edit")

            assert resp.status_code == 200
            assert b'name="settled_amount"' not in resp.data
            assert b"Actual comes from the purchases below" in resp.data

    def test_the_box_appears_ONLY_once_there_is_a_figure_to_correct(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """Before the settle there is nothing to state; after it, there is.

        **This asserted the box was absent in BOTH states until the developer's
        2026-08-17 ruling**, and the reasoning behind that was backwards.  A
        draft of plan step X-au-c3 drew the box gated on ``locked`` -- and since
        every ``is_settled`` status is also ``is_immutable`` (``ref_seeds``:
        Paid and Received) it rendered ``disabled`` on 100% of the rows it
        appeared on, so the box was deleted as unreachable.  But being disabled
        WAS the defect: the estimate and the actual are two different facts, a
        lock protects BUDGET DECISIONS from being rewritten, and what the bank
        took is an OBSERVED FACT -- the same argument the "Money moved on" date
        beside it has always made for not being disabled.

        Both states are asserted because a box that renders where nothing has
        moved and a box missing where something has fail the same user in
        opposite directions.
        """
        with app.app_context():
            txn = create_envelope_txn(
                seed_user, db.session, seed_periods_today[3],
                "Kayla's Spending Money", Decimal("100.00"),
            )
            db.session.commit()
            txn_id = txn.id

            # Projected: no money has moved, so there is nothing to state.
            resp = auth_client.get(f"/transactions/{txn_id}/full-edit")
            assert resp.status_code == 200
            assert b'name="settled_amount"' not in resp.data

            assert auth_client.post(
                f"/transactions/{txn_id}/mark-done",
            ).status_code == 200
            db.session.expire_all()

            # Settled through the MANUAL branch: the figure exists, so the box
            # does -- and it is NOT disabled, which is the whole point.  A
            # disabled input is not submitted, so a box that rendered locked
            # would be the dead control this ruling removed.
            resp = auth_client.get(f"/transactions/{txn_id}/full-edit")
            assert resp.status_code == 200
            assert b'name="settled_amount"' in resp.data
            body = resp.data.decode()
            box = body[body.index('name="settled_amount"'):]
            box = box[:box.index(">")]
            assert "disabled" not in box, (
                "the Actual box rendered disabled, which is the defect that "
                "got the box deleted the first time"
            )

    def test_the_settle_DAY_box_appears_only_once_the_money_has_moved(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """"Money moved on" renders exactly when there is a day to state.

        **The twin of the Actual box's gate above, and it went unpinned while
        that one was asserted in both directions.**  The template states the
        rule -- ``{% if txn.status and txn.status.is_settled %}`` -- and the
        schema's ``settled_on`` is deliberately not ``allow_none``, so a
        Projected row has no day and no box to state one in.

        **What rests on it is a procedure rather than a screen.**  Plan step
        ``balance:X-f3c-2b-2c``'s runbook tells an operator to record a
        dividend in THREE saves -- create, settle, then reopen the settled card
        and correct the day -- and the third save exists only because this box
        is absent on the second.  A template change that rendered it earlier
        would make those instructions wrong while every other test stayed
        green, which is a document going stale against code nothing compares it
        to.

        Asserted in both directions for the reason the Actual box is: a box
        that renders where nothing has moved and a box missing where something
        has fail the same user in opposite directions.
        """
        with app.app_context():
            txn = create_envelope_txn(
                seed_user, db.session, seed_periods_today[3],
                "Kayla's Spending Money", Decimal("100.00"),
            )
            db.session.commit()
            txn_id = txn.id

            # Projected: the money has not moved, so there is no day to state.
            resp = auth_client.get(f"/transactions/{txn_id}/full-edit")
            assert resp.status_code == 200
            assert b'name="settled_on"' not in resp.data

            assert auth_client.post(
                f"/transactions/{txn_id}/mark-done",
            ).status_code == 200
            db.session.expire_all()

            # Settled: the day exists, so the box does -- and it is NOT
            # disabled.  The #26 lock protects budget decisions; the day the
            # bank moved money is an observed fact and is corrected in place.
            resp = auth_client.get(f"/transactions/{txn_id}/full-edit")
            assert resp.status_code == 200
            assert b'name="settled_on"' in resp.data
            body = resp.data.decode()
            box = body[body.index('name="settled_on"'):]
            box = box[:box.index(">")]
            assert "disabled" not in box, (
                "the settle-day box rendered disabled, so the day could not be "
                "corrected in place and the runbook's third save would fail"
            )

    def test_correcting_the_actual_records_it_without_reverting(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """The ruling's whole point: no revert needed to fix what moved.

        A bill settled at its ``$500.00`` plan, then corrected to ``$245.32``
        through the popover alone.  The row stays Paid throughout -- the user
        never touches the Status dropdown -- and the record says a HUMAN
        supplied the figure.

        **The estimate is NOT touched by this**, which is the half the developer
        named: the two boxes are two facts, and editing one may not move the
        other.
        """
        with app.app_context():
            txn = create_envelope_txn(
                seed_user, db.session, seed_periods_today[3],
                "Electricity", Decimal("500.00"),
            )
            # A plain BILL, not an envelope: an envelope's actual is the sum of
            # its purchases and is not correctable (ruling R-FF).
            txn.template.is_envelope = False
            db.session.commit()
            txn_id = txn.id
            assert auth_client.post(
                f"/transactions/{txn_id}/mark-done",
            ).status_code == 200
            db.session.commit()
            db.session.expire_all()

            paid_status = db.session.get(Transaction, txn_id).status_id
            resp = auth_client.patch(
                f"/transactions/{txn_id}",
                data={
                    "version_id": db.session.get(Transaction, txn_id).version_id,
                    "status_id": str(paid_status),
                    "settled_amount": "245.32",
                },
            )
            assert resp.status_code == 200

            db.session.expire_all()
            reloaded = db.session.get(Transaction, txn_id)
            assert settled_figure(reloaded) == Decimal("245.32")
            assert status_seam.recorded_settlement(reloaded).stated
            # Still Paid: correcting an observation is not a status change.
            assert reloaded.status_id == paid_status
            assert reloaded.settled_on is not None
            # And the PLAN is untouched -- two boxes, two facts.  Resolved,
            # because the row is generated and stores no figure of its own.
            assert resolve_transaction_amount(
                reloaded, amount_basis_for(reloaded),
            ) == Decimal("500.00")
            # WHO WROTE it: the popover is a person's door, and the route
            # states ``typed`` with the figure (plan step X-bi-3e-1, ruling
            # R-BAL61) -- graded on the covering movement, the source's home.
            (movement,) = reloaded.covering_movements
            assert movement.amount == Decimal("245.32")
            assert movement.figure_source_id == ref_cache.movement_figure_source_id(
                MovementFigureSourceEnum.TYPED,
            )

    def test_a_submitted_actual_on_a_derived_row_is_refused(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """The stale-form and crafted-request backstop, and it books nothing.

        A form rendered before a purchase was added still carries the input.
        Submitting $60.00 against a row whose purchases sum to $48.98 must not
        be written and then silently overwritten by the settle.
        """
        with app.app_context():
            txn = _gas_envelope(seed_user, seed_periods_today[3])
            txn_id = txn.id

            resp = _full_edit_save(
                auth_client, txn, ref_cache.status_id(StatusEnum.DONE),
                settled_amount="60.00",
            )

            assert resp.status_code == 400
            assert b"comes from the purchases" in resp.data
            db.session.expire_all()
            reloaded = db.session.get(Transaction, txn_id)
            assert reloaded.status_id == ref_cache.status_id(
                StatusEnum.PROJECTED,
            )
            assert status_seam.recorded_settlement(reloaded) is None

    def test_an_empty_actual_box_is_not_a_figure(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """An empty input states no amount, so it is not refused.

        ``actual_amount`` is ``allow_none``, so an empty box loads as an
        explicit ``None`` rather than being dropped.  Refusing that would break
        every save from a legacy form on an envelope row.
        """
        with app.app_context():
            txn = _gas_envelope(seed_user, seed_periods_today[3])
            txn_id = txn.id

            resp = _full_edit_save(
                auth_client, txn, ref_cache.status_id(StatusEnum.DONE),
                settled_amount="",
            )

            assert resp.status_code == 200
            db.session.expire_all()
            assert settled_figure(
                db.session.get(Transaction, txn_id),
            ) == Decimal("48.98")


class TestWhatAReSettleBooksIsWhatTheOfferSHOWED:
    """The panel's offer and the verb's booking are ONE expression.

    **Both cases here are regressions a first draft of the retention rule
    shipped, and an adversarial review found them** (2026-08-17).
    ``Settlement.from_settle`` honoured a retained ``corrected`` record while
    ``settle_amount`` -- what the reconcile panel PREFILLS from -- went on
    pricing the row's PLAN.  Measured end to end: a ``$500.00`` bill corrected
    to ``$245.32`` and then reverted was OFFERED at ``$500.00`` and BOOKED at
    ``$245.32``.

    That broke two things at once, and the second is worse than the drift.  The
    figure a tick booked was one the screen had never shown; and because a
    submitted figure counts as a correction only when it DIFFERS from the offer
    (``transaction_service.is_correction``), no input the user could
    give meant "book the plan".  ``status_seam.honoured_correction`` answers
    both sides now, so the offer equals the booking and any other number is a
    genuine correction.
    """

    def _reverted_bill(self, seed_user, period):
        """A $500.00 bill corrected to $245.32, then reverted to Projected."""
        txn = create_envelope_txn(
            seed_user, db.session, period, "Electricity", Decimal("500.00"),
        )
        txn.template.is_envelope = False
        db.session.commit()
        transaction_service.settle_transaction(
            txn, submitted=typed(Decimal("245.32")),
        )
        db.session.commit()
        status_seam.apply_status_change(
            txn, ref_cache.status_id(StatusEnum.PROJECTED),
        )
        db.session.commit()
        return txn

    def test_an_untouched_tick_books_the_figure_the_panel_offered(
        self, app, seed_user, seed_periods_today,
    ):
        """The drift control: offer and booking must be the same number.

        The reconcile panel offers a PROJECTED row, prefills its box from
        ``settle_amount``, and an untouched tick posts exactly that figure back.
        Asserting the two are equal is the claim ``settle_amount``'s docstring
        makes -- *"there is no shape in which the displayed figure and the
        booked one can drift"* -- which was false for a reverted row.
        """
        with app.app_context():
            txn = self._reverted_bill(seed_user, seed_periods_today[3])
            txn_id = txn.id

            offered = transaction_service.settle_amount(txn, amount_basis_for(txn))
            transaction_service.settle_transaction(txn, submitted=typed(offered))
            db.session.commit()

            booked = settled_figure(db.session.get(Transaction, txn_id))
            assert offered == booked
            # And the offer is the RETAINED figure, not the plan: what the bank
            # took survives the revert and is what a re-settle books.
            assert booked == Decimal("245.32")

    def test_typing_the_plan_DISPLACES_the_retained_correction(
        self, app, seed_user, seed_periods_today,
    ):
        """The user can still say "no, it really was the $500.00".

        The half that makes the retained figure a default rather than a trap.
        While the offer was the plan, submitting the plan was an ECHO -- so the
        one number the user most plausibly wants to book was the one number they
        could not express, and they would have had to type $499.99 and correct
        it afterwards.
        """
        with app.app_context():
            txn = self._reverted_bill(seed_user, seed_periods_today[3])
            txn_id = txn.id

            transaction_service.settle_transaction(
                txn, submitted=typed(Decimal("500.00")),
            )
            db.session.commit()

            reloaded = db.session.get(Transaction, txn_id)
            assert status_seam.recorded_settlement(reloaded) == status_seam.Settlement(
                Decimal("500.00"), MovementFigureSourceEnum.TYPED,
            )
