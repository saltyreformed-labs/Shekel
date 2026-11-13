"""The full-edit dropdown's settle: it books the entry sum (production row 2231's $31.02), flushes
its field writes inside the exception net, takes back on revert what the settle derived, and
refuses a settled status that contradicts the row's type.
"""

from decimal import Decimal

from app import ref_cache
from app.enums import MovementFigureSourceEnum, StatusEnum
from app.extensions import db
from app.models.transaction import Transaction
from app.services import entry_service, status_seam, transaction_service
from app.services.cash_ledger import resolve_transaction_amount
from app.services.row_valuation import settled_contribution, settled_figure
from app.services.state_machine import allowed_transitions
from app.utils.dates import display_today
from tests._test_helpers import (
    amount_basis_for,
    an_entered_day,
    create_envelope_txn,
    generate_row_of,
)

from ._helpers import _plan_worth, _gas_envelope, _full_edit_save, _cash_leg


class TestTheDropdownBooksWhatTheRowCost:
    """The money control: production row 2231's $31.02."""

    def test_the_dropdown_settles_at_the_entry_sum(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """Picking Paid in the popover records $48.98, not the $80.00 budget.

        Shown to FIRE: routing this door back to ``status_seam`` books
        ``$80.00`` -- a dated covering movement -- and leaves the record
        stating the budget.

        Arithmetic: one purchase of $48.98 against an $80.00 envelope, so the
        record is 48.98 and the ledger holds NOTHING at the close (ruling
        **R-BAL77**, plan step ``balance:X-bi-4a``: an un-dated purchase is
        in flight, and the close books nothing of its own); dating the
        purchase books its own -48.98.  The $31.02 difference is budget that
        was never spent and must not be booked either way.
        """
        with app.app_context():
            txn = _gas_envelope(seed_user, seed_periods_today[3])
            txn_id = txn.id

            resp = _full_edit_save(
                auth_client, txn, ref_cache.status_id(StatusEnum.DONE),
            )

            assert resp.status_code == 200
            db.session.expire_all()
            reloaded = db.session.get(Transaction, txn_id)
            assert reloaded.status_id == ref_cache.status_id(StatusEnum.DONE)
            # A ``purchases`` record stores NO figure -- the row's own entries
            # state it (plan step X-au-c3) -- so the accessor is what answers,
            # and the row's PLAN is untouched beside it: still its definition's
            # $80.00, resolved the way every reader resolves it (the row is
            # generated, so the raw column is ``None`` and says nothing).
            assert settled_figure(reloaded) == Decimal("48.98")
            assert resolve_transaction_amount(
                reloaded, amount_basis_for(reloaded),
            ) == Decimal("80.00")
            assert settled_contribution(reloaded) == Decimal("48.98")
            assert reloaded.settled_on == display_today()
            # The ledger books nothing at the close -- neither the budget nor
            # the in-flight purchase -- and the purchase's own leg once dated.
            assert _cash_leg(txn_id, seed_user["account"].id) == Decimal("0")
            [purchase] = reloaded.purchases
            entry_service.update_entry(
                purchase.id, seed_user["user"].id,
                settle_day=an_entered_day(display_today()),
            )
            db.session.commit()
            assert _cash_leg(txn_id, seed_user["account"].id) == Decimal(
                "-48.98",
            )

    def test_the_dropdown_and_mark_paid_book_the_same_figure(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """Two controls, one card, one answer -- the whole point of the step.

        Two identically-shaped rows: one settled through the popover's Status
        dropdown, one through the grid's Mark Paid button.  Both must land on
        $48.98 with the same ledger effect.  Asserted as an EQUALITY between
        the two doors, so a change that moves one fails, and the ledger half
        states its figure too, because two doors booking nothing would agree.
        """
        with app.app_context():
            via_dropdown = _gas_envelope(seed_user, seed_periods_today[3])
            via_button = _gas_envelope(seed_user, seed_periods_today[4])
            dropdown_id, button_id = via_dropdown.id, via_button.id

            assert _full_edit_save(
                auth_client, via_dropdown,
                ref_cache.status_id(StatusEnum.DONE),
            ).status_code == 200
            assert auth_client.post(
                f"/transactions/{button_id}/mark-done",
            ).status_code == 200

            db.session.expire_all()
            dropdown_row = db.session.get(Transaction, dropdown_id)
            button_row = db.session.get(Transaction, button_id)
            assert settled_contribution(dropdown_row) == settled_contribution(button_row)
            assert settled_figure(dropdown_row) == settled_figure(button_row)
            assert dropdown_row.status_id == button_row.status_id
            # The close books nothing of its own (ruling R-BAL77), so two $0.00
            # legs would agree whatever either door did: the ledger half compares
            # what each row books once its purchase is dated.
            for row in (dropdown_row, button_row):
                [purchase] = row.purchases
                entry_service.update_entry(
                    purchase.id, seed_user["user"].id,
                    settle_day=an_entered_day(display_today()),
                )
            db.session.commit()
            booked = _cash_leg(dropdown_id, seed_user["account"].id)
            assert booked == _cash_leg(button_id, seed_user["account"].id)
            assert booked == Decimal("-48.98")

    def test_an_entry_less_envelope_still_books_its_budget(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """Ruling **R-FJ** at the door: no purchases means the budget is spent.

        The half of finding N-230 that belongs to a DOOR.  An envelope with no
        logged purchases settles at its own $100.00 estimate here, because
        pressing Paid says the budget is finished and nothing has relocated the
        unspent money.  ``carry_forward_service`` -- which HAS relocated it,
        into the next period's row -- settles the same shape at $0.00 through
        ``settle_from_entries``, and that difference is the ruling.
        """
        with app.app_context():
            txn = create_envelope_txn(
                seed_user, db.session, seed_periods_today[3],
                "Kayla's Spending Money", Decimal("100.00"),
            )
            db.session.commit()
            txn_id = txn.id

            assert _full_edit_save(
                auth_client, txn, ref_cache.status_id(StatusEnum.DONE),
            ).status_code == 200

            db.session.expire_all()
            reloaded = db.session.get(Transaction, txn_id)
            assert settled_contribution(reloaded) == Decimal("100.00")
            assert _cash_leg(txn_id, seed_user["account"].id) == Decimal(
                "-100.00",
            )

    def test_re_saving_a_paid_row_does_not_re_settle_it(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """Staying inside the band is not a settle.

        The control for ``enters_settled_band``'s second half.  A row already
        settled has an amount that is a FACT and an immutable status, so
        routing this move to the settle verb would ask the envelope branch to
        re-price a row it refuses by precondition.  The save must leave the
        status alone and, above all, the settle DAY -- which must not jump to
        the day the popover was re-saved.

        It was ``Paid -> Settled``, the ARCHIVE, until plan step
        **balance:X-am** deleted that status.  That was a real status MOVE
        inside the band; what remains is the identity re-submit, which is what
        this popover actually produces on every Save because it posts the whole
        row rather than a delta.  Both reach the same rule, and the day is the
        assertion either one could have destroyed.
        """
        with app.app_context():
            txn = _gas_envelope(seed_user, seed_periods_today[3])
            txn_id = txn.id
            assert auth_client.post(
                f"/transactions/{txn_id}/mark-done",
            ).status_code == 200

            db.session.expire_all()
            paid = db.session.get(Transaction, txn_id)
            settled_day = paid.settled_on
            paid_id = paid.status_id

            resp = _full_edit_save(
                auth_client, paid, paid_id,
                settled_on=settled_day.isoformat(),
            )

            assert resp.status_code == 200
            db.session.expire_all()
            resaved = db.session.get(Transaction, txn_id)
            assert resaved.status_id == paid_id
            assert settled_figure(resaved) == Decimal("48.98")
            assert resaved.settled_on == settled_day


class TestTheFieldWritesFlushInsideTheExceptionNet:
    """The derived-amount guard reads a LAZY relationship, so it FLUSHES.

    ``settles_from_entries`` reads ``Transaction.purchases`` -- the ``entries``
    one-to-many, a default ``lazy="select"`` relationship -- for every row
    since plan step ``balance:X-bi-4a`` (ruling **R-BAL78** dropped the
    ``tracks_purchases`` half, which read ``self.template.is_envelope``
    through the ``template`` many-to-one).  Reading it emits a SELECT, and a
    SELECT autoflushes the ``setattr`` loop's staged mutations as the
    version-pinned UPDATE.  That made the request's FIRST flush happen above
    the handler's own exception net and, worse, before ``is_override`` was
    written.

    Found by adversarial review after the step had shipped, and the comment it
    contradicted is the tell: the handler claimed its three excepts "cover the
    WHOLE tail", which was true of the tail while the first flush had moved
    above it.
    """

    def test_a_period_move_carrying_an_actual_does_not_500(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """Moving a generated row into an already-populated period still works.

        Both generation indexes are unique only ``WHERE is_override = FALSE``
        -- so ``is_override = True`` is exactly what makes this move legal into
        a period the recurrence engine has already filled, which is every future
        period.  Written before the flush, the row leaves the index predicate
        and the move commits; written after it, the flush lands while the row is
        still inside the predicate and PostgreSQL rejects it as an
        ``IntegrityError`` raised above the net -- an unhandled 500 on an
        ordinary edit.

        The submitted figure is what reaches the lazy load at all: the guard
        short-circuits on ``data.get("settled_amount") is not None``, and the
        popover prefills that box from the row's RECORD, so any row that has
        settled submits one on every Save.  A crafted or stale form reaches it
        on an unsettled row too, which is the request this case makes.

        **The save now SETTLES the row as it moves it** (plan step X-au-c3),
        and that is what keeps the ordering exercised rather than a rewrite of
        what the case is about.  A figure records a settle, so an unsettled row
        can neither carry one nor be handed one -- the seam refuses a settlement
        offered beside a Projected status with a 400, which would end the
        request before the move.  A full-edit save that moves the period AND
        marks the row paid is one ordinary user action, it is the only shape
        that legitimately carries a figure past the guard, and the destination
        period is not locked because the LOCK reads the row's current status,
        which is still Projected when the save arrives.

        **The two rows are UNDATED -- ``occurs_on IS NULL`` -- and that is the
        whole of what makes this case a case.**  Plan step R17 keyed the
        generation index on the occurrence a row answers, which a move never
        changes, so the engine's own DATED rows cannot collide however late the
        flag is written; only a row in ``idx_transactions_template_scenario_
        undated`` -- keyed on its PAYCHECK -- still can, and that is the
        pre-R17 shape no producer writes any more and production still holds
        (six rows, per finding REC-516).  Both rows are the engine's, then put
        into that shape the way ``test_recurrence_engine.test_generation``
        models the same legacy rows (plan step balance:X-cf); when this suite last built them
        by hand it got the shape by accident, and a fixture generated the
        modern way silently stopped grading the ordering -- the review of that
        conversion found it, and this sentence is why the rows are undated on
        purpose.

        **And the row stays an ENVELOPE, with no purchases against it.**  The
        flush the ordering is about is the guard's ``entries`` read -- a
        one-to-many the ORM autoflushes before querying.  Through plan step
        ``balance:X-bi-3e`` only an envelope reached it: ``tracks_purchases``
        was asked first, and its ``template`` read is a by-key many-to-one
        the ORM serves from the identity map with no flush at all, so this
        case's switching the template to non-envelope meant the guard never
        flushed and the flag could be written anywhere without a collision
        (measured 2026-09-11: it passed with the flag moved below the guard
        AND with the flag deleted outright, in that shape).  Since
        ``balance:X-bi-4a`` every row's guard reads ``entries`` (ruling
        **R-BAL78**), and the envelope shape is kept because it is the one
        the case was written about.  An envelope with no purchases still
        settles on the MANUAL branch (nothing is recorded against it, so
        ``settles_from_entries`` is false), so the typed figure is honoured
        exactly as before.

        Shown to FIRE: moving ``is_override`` back below the guard raises
        ``IntegrityError`` out of the handler (re-measured 2026-09-11 on the
        undated envelope pair: red with the flag below the guard, green with
        it above).
        """
        with app.app_context():
            source = create_envelope_txn(
                seed_user, db.session, seed_periods_today[3],
                "Electricity", Decimal("300.00"),
            )
            # The destination period already holds this template's generated
            # row, non-override -- the state every future period is in.
            occupant = generate_row_of(source.template, seed_periods_today[4])
            # The legacy shape the ordering hazard needs, on both rows.
            source.occurs_on = None
            occupant.occurs_on = None
            db.session.commit()
            source_id = source.id

            resp = _full_edit_save(
                auth_client, source,
                ref_cache.status_id(StatusEnum.DONE),
                pay_period_id=str(seed_periods_today[4].id),
                settled_amount="45.00",
            )

            assert resp.status_code == 200, resp.data[:300]
            db.session.expire_all()
            moved = db.session.get(Transaction, source_id)
            assert moved.pay_period_id == seed_periods_today[4].id
            assert moved.is_override is True
            assert settled_figure(moved) == Decimal("45.00")


class TestARevertTakesBackWhatTheSettleDerived:
    """A settle writes an envelope's actual; leaving the band takes it back.

    The seam already clears ``settled_on`` on the way out of the settled band.
    ``actual_amount`` on an envelope is the same kind of value -- ``sum(entries)``
    at the moment of the settle, written by ``settle_from_entries`` in one
    statement with the status and the day -- and was being left behind.

    **Production row 2281 *Groceries* is that state today**: Projected, carrying
    ``actual_amount = 533.08`` against a `$500.00` budget from a settle that was
    later reverted, so it projects at its SPEND rather than its budget.  Plan
    step X-ap made that figure UNREACHABLE as well as wrong -- the popover stops
    rendering an Actual box for a row whose amount the settle derives -- so the
    release is what keeps the removal of that box honest.
    """

    def test_reverting_an_envelope_releases_its_derived_actual(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """Settle, then revert: the row is back to projecting its budget.

        $80.00 budget, one $48.98 purchase.  The settle books $48.98; the
        revert releases it, so the row projects at $80.00 again -- which is what
        Projected means for an envelope that has not finished being spent.

        Shown to FIRE: without the release the reverted row still reads $48.98.
        """
        with app.app_context():
            txn = _gas_envelope(seed_user, seed_periods_today[3])
            txn_id = txn.id
            assert auth_client.post(
                f"/transactions/{txn_id}/mark-done",
            ).status_code == 200

            db.session.expire_all()
            paid = db.session.get(Transaction, txn_id)
            assert settled_figure(paid) == Decimal("48.98")

            resp = _full_edit_save(
                auth_client, paid, ref_cache.status_id(StatusEnum.PROJECTED),
            )

            assert resp.status_code == 200
            db.session.expire_all()
            reverted = db.session.get(Transaction, txn_id)
            assert status_seam.recorded_settlement(reverted) is None
            assert reverted.settled_on is None
            assert _plan_worth(reverted) == Decimal("80.00")
            # The settle's money leaves with it: nothing is left booked.
            assert _cash_leg(txn_id, seed_user["account"].id) == Decimal("0.00")

    def test_a_BILLs_hand_typed_figure_SURVIVES_the_revert_round_trip(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """A human's figure outlives the settle that recorded it (ruling R-FB).

        **The whole round trip, because the round trip is the point.**  The
        full-edit popover TELLS the user to set Status to Projected in order to
        edit the amounts, so reverting has to be lossless or the app's own
        instruction is a trap.

        A $500.00 bill corrected to $245.32: the revert withdraws the ASSERTION
        (the day, the clearing link) and the row projects at its $500.00 plan
        again, because the STATUS decides which figure governs.  What it does
        NOT do is un-know what the bank took -- the $245.32 and its ``corrected``
        basis stay on the row, and settling again books them rather than
        re-deriving $500.00 over them
        (``status_seam.Settlement.from_settle``).

        **A draft of plan step X-au-c3 released all three columns together**,
        under ``ck_transactions_settlement_recorded``, which paired the settle
        day with the figure's provenance.  That welded two facts with different
        lifetimes into one -- finding **N-241**'s shape rebuilt one level up, in
        the step meant to remove it -- and cost the user the $245.32 for
        following the instruction on screen.  The constraint is deleted
        (developer, 2026-08-17).
        """
        with app.app_context():
            txn = create_envelope_txn(
                seed_user, db.session, seed_periods_today[3],
                "Electricity", Decimal("500.00"),
            )
            txn.template.is_envelope = False
            db.session.commit()
            txn_id = txn.id

            assert auth_client.post(
                f"/transactions/{txn_id}/mark-done",
                data={"settled_amount": "245.32"},
            ).status_code == 200
            db.session.expire_all()
            assert settled_figure(
                db.session.get(Transaction, txn_id),
            ) == Decimal("245.32")

            paid = db.session.get(Transaction, txn_id)
            assert _full_edit_save(
                auth_client, paid, ref_cache.status_id(StatusEnum.PROJECTED),
            ).status_code == 200

            db.session.expire_all()
            reverted = db.session.get(Transaction, txn_id)
            # The ASSERTION is withdrawn ...
            assert reverted.settled_on is None
            assert reverted.reconciled_by_id is None
            # ... WHAT MOVED is kept, still flagged as the human's figure ...
            assert status_seam.recorded_settlement(reverted) == status_seam.Settlement(
                Decimal("245.32"), MovementFigureSourceEnum.TYPED,
            )
            # ... and the row is nonetheless worth its PLAN again, because the
            # STATUS decides which figure governs, not the record.
            assert settled_figure(reverted) is None
            assert _plan_worth(reverted) == Decimal("500.00")

            # THE RETURN LEG: settling again books the human's figure rather
            # than re-deriving the plan over it. Without this the retention
            # would only delay the loss by one step.
            assert auth_client.post(
                f"/transactions/{txn_id}/mark-done",
            ).status_code == 200
            db.session.expire_all()
            resettled = db.session.get(Transaction, txn_id)
            assert status_seam.recorded_settlement(resettled) == status_seam.Settlement(
                Decimal("245.32"), MovementFigureSourceEnum.TYPED,
            )


class TestASettledStatusMustMatchTheRowsType:
    """Income settles as Received, an expense as Paid -- never the other one."""

    def test_an_expense_asked_to_settle_as_received_is_refused(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """The state machine admits it; the type rule does not.

        ``state_machine``'s transaction map allows Projected -> Received
        because it grades the STATUS and never sees ``transaction_type_id``.
        Without this refusal the verb would substitute Paid silently and the
        row would land in a status the user did not pick.

        Shown to FIRE: deleting the refusal returns 200 with the row Paid.
        """
        with app.app_context():
            txn = _gas_envelope(seed_user, seed_periods_today[3])
            txn_id = txn.id

            resp = _full_edit_save(
                auth_client, txn, ref_cache.status_id(StatusEnum.RECEIVED),
            )

            assert resp.status_code == 400
            assert b"settles as Paid" in resp.data
            db.session.expire_all()
            reloaded = db.session.get(Transaction, txn_id)
            assert reloaded.status_id == ref_cache.status_id(
                StatusEnum.PROJECTED,
            )
            assert status_seam.recorded_settlement(reloaded) is None

    def test_the_narrowing_removes_EXACTLY_the_type_mismatch(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """The offer set is the state machine's answer minus ONE status.

        The narrowing exists to drop the settled status this row's TYPE does
        not take -- ``Received`` on an expense -- and nothing else.  Asserting
        the WHOLE difference rather than one membership is what catches an
        over-broad subtraction, which is the defect this case was written for.

        **The defect it was written for was shipped and caught by review**: a
        first draft subtracted the whole settled BAND, which also removed
        ``Settled`` -- the ARCHIVE -- from every row's dropdown, silently
        retiring the only control that offered it while ``state_machine`` still
        called ``Paid -> Settled`` legal.  Plan step **balance:X-am** has since
        deleted that status, so the specimen is gone; the over-broad
        subtraction is not, because the band still has two members and dropping
        both would leave a Projected expense unable to be marked Paid at all.

        **Asked of a PROJECTED row, and that is where the narrowing bites.**
        From Paid the state machine offers only ``{Paid, Projected}``, so the
        subtraction is empty there and the case would grade nothing -- the
        state machine is keyed on the STATUS and admits BOTH settled statuses
        only from Projected, which is the exact blindness this narrowing
        exists to cover.
        """
        with app.app_context():
            txn = _gas_envelope(seed_user, seed_periods_today[3])
            db.session.expire_all()
            projected = db.session.get(Transaction, txn.id)
            assert projected.is_expense

            offerable = transaction_service.offerable_status_ids(projected)
            legal = allowed_transitions(projected)
            names = {ref_cache.status_id(m): m.value for m in StatusEnum}
            shown = (
                f"status={names[projected.status_id]} "
                f"legal={sorted(names[i] for i in legal)} "
                f"offered={sorted(names[i] for i in offerable)}"
            )

            assert legal == {ref_cache.status_id(m) for m in StatusEnum}, shown
            assert legal - offerable == {
                ref_cache.status_id(StatusEnum.RECEIVED),
            }, shown

    def test_the_dropdown_does_not_offer_the_mismatched_status(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """The pre-hint disables what the refusal would reject.

        Grid audit D2's rule ("options the state machine would reject render
        disabled, so an illegal transition cannot be picked instead of failing
        as a 400 after Save"), completed: the state machine's own answer is
        type-blind, so Received was offered on every expense row.
        """
        with app.app_context():
            txn = _gas_envelope(seed_user, seed_periods_today[3])
            offerable = transaction_service.offerable_status_ids(txn)

            assert ref_cache.status_id(StatusEnum.DONE) in offerable
            assert ref_cache.status_id(StatusEnum.RECEIVED) not in offerable
            assert ref_cache.status_id(StatusEnum.CANCELLED) in offerable
