"""The full-edit popover settles like every other door (plan step X-ap).

**Finding N-219: a transaction had THREE settle doors and one booked a
different figure.**  The grid's Mark Paid button and the reconcile panel's tick
both call ``transaction_service.settle_transaction``, which settles an
envelope-tracked row at the sum of the purchases recorded against it.  The
Status dropdown on the full-edit popover did not: it flipped the status through
the status SEAM -- the mechanics primitive -- and reconciled the ledger, never
consulting the entries, so the row booked ``COALESCE(actual, estimated)``: its
BUDGET.

Measured on the production clone this suite's figures are taken from: row 2231
*Gas*, budget ``$80.00``, one logged purchase of ``$48.98``.  Mark Paid booked
``$48.98`` and returned ``$31.02`` of unspent budget to the projection; the
dropdown booked ``$80.00`` and returned nothing.  ``$31.02`` of spend that never
happened, from two controls in the same card, and the double-entry ledger
followed whichever was used.

What these pin, in the order the request runs them:

  * the dropdown books the ENTRY SUM, and the ledger agrees (the money control);
  * the two doors agree, asserted by settling the same shape through each;
  * a row already INSIDE the settled band is not re-settled -- ``Paid ->
    Settled`` is an archive of a row whose amount is already a fact;
  * a settled status that contradicts the row's TYPE is refused rather than
    silently substituted, and the dropdown does not offer it;
  * the Actual box is rendered exactly when the settle would honour a figure
    typed into it (ruling **R-FF**), and a submitted one is refused otherwise;
  * the settle-DAY box beside it is rendered exactly when there is a day to
    state, which is what makes a settled row's day correctable in place.
"""
# pylint: disable=redefined-outer-name
# Rationale: ``redefined-outer-name`` is the canonical pytest fixture pattern;
# test bodies bind fixtures (``auth_client``, ``seed_user``, ...) by name.
from datetime import timedelta
from decimal import Decimal

from app import ref_cache
from app.enums import MovementFigureSourceEnum, StatusEnum
from app.extensions import db
from app.models.journal_entry import JournalEntry, Posting
from app.models.transaction import Transaction
from app.services import transaction_service
from app.services.settle_day import record_settle_day, recorded_settle_day
from app.services.state_machine import allowed_transitions
from app.utils.dates import display_today
from tests._test_helpers import (
    typed,
    family_journal_filter,
    add_entry,
    amount_basis_for,
    an_asserted_day,
    an_entered_day,
    create_envelope_txn,
    generate_row_of,
    net_posted_by_day,
)
from app.services import entry_service, status_seam, transaction_service
from app.services.cash_ledger import contribution_of, resolve_transaction_amount
from app.services.row_valuation import settled_contribution, settled_figure


def _plan_worth(txn):
    """What a REVERTED (Projected) row contributes -- its plan, resolved.

    Asked of :func:`~app.services.cash_ledger.contribution_of` because the row
    is out of the settled band: the two revert cases below assert that a row
    which has been reverted is worth its PLAN again, and since plan step X-bx
    ``row_valuation.settled_contribution`` refuses exactly that row rather than
    pricing its plan column by hand (finding **BAL-465**).  The figures those
    cases assert are unchanged, and so is the rule -- the STATUS decides which
    figure governs, not the columns.
    """
    return contribution_of(txn, amount_basis_for(txn))


def _gas_envelope(seed_user, period):
    """Build production row 2231's shape: an $80.00 budget, one $48.98 purchase.

    Returns the flushed, Projected, envelope-tracked transaction.
    """
    txn = create_envelope_txn(
        seed_user, db.session, period, "Gas", Decimal("80.00"),
    )
    add_entry(
        db.session, seed_user, txn, Decimal("48.98"), display_today(),
    )
    db.session.commit()
    return txn


def _full_edit_save(auth_client, txn, status_id, **overrides):
    """PATCH exactly what the full-edit popover submits on Save.

    The form posts the row's whole state, not a delta -- the estimated amount,
    the period, the notes, the due date and the status -- which is why the
    handler's field writes and its status work interact at all.  Keyword
    overrides replace or add a field.

    **A FINALISED row submits less, and modelling that is load-bearing.**  The
    template disables the money / period / due-date inputs on an immutable row
    (the #26 lock's UI half) and a disabled input is omitted from the POST, so
    a payload carrying them would be refused by ``_finalised_edit_response``
    before any of this suite's subject matter ran -- a helper that tested the
    lock instead of the settle.

    **A GENERATED row submits no due date at all, for the same reason one tier
    over** (plan step balance:X-au-e).  Its date is its DEFINITION's -- it is a
    member of ``DerivedRowFields`` and every regeneration rewrites it -- and
    since that step it is also what prices the row, so the popover renders it
    as TEXT rather than an input and a browser sends no ``due_date`` key.  A
    payload carrying one is refused by
    ``_gates._reject_generated_due_date_edit`` before this suite's subject
    matter runs, which is the same trap the finalised arm above exists to
    avoid: seven cases here failed on that refusal the moment the gate landed,
    all of them testing the settle and none of them testing the date.

    **The amount it posts is the one the popover RENDERS, which is the amount
    model's answer and not the raw column** (plan step balance:X-cf).  The
    template's box reads ``budgets[txn.id]`` --
    :func:`~app.services.cash_ledger.amounts_by_id`, the resolved figure --
    and a GENERATED row stores no figure of its own, so the column is ``None``
    there and a payload built from it posts the string ``"None"``, which the
    schema refuses with a 422 before the settle runs.  Every fixture this
    suite settles is generated now, so that is the browser this helper has to
    model; while the fixtures owned their figures the two spellings agreed
    and the wrong one went unnoticed.
    """
    payload = {
        "version_id": str(txn.version_id),
        "status_id": str(status_id),
        "notes": txn.notes or "",
    }
    if not txn.status.is_immutable:
        rendered = str(resolve_transaction_amount(txn, amount_basis_for(txn)))
        payload["estimated_amount"] = rendered
        # **The amount box's companion, carrying what the form RENDERED into
        # it** (ruling R-JR, plan step balance:X-au-h).  The popover posts both,
        # and equal values are what a browser sends when the user did not touch
        # the box -- which is the case every test using this helper models,
        # since each one is editing something else.
        #
        # Omitting it would not fail: a figure with no companion is treated as
        # AUTHORED, so every save through this helper would take ownership of
        # the row's amount and the suite would go on passing while modelling a
        # browser that does not exist.  That is the trap this docstring's other
        # two paragraphs already describe, on a third field.
        payload["estimated_amount_as_rendered"] = rendered
        payload["pay_period_id"] = str(txn.pay_period_id)
        if txn.template_id is None:
            payload["due_date"] = (
                txn.due_date.isoformat() if txn.due_date else ""
            )
    payload.update(overrides)
    return auth_client.patch(f"/transactions/{txn.id}", data=payload)


def _cash_leg(txn_id, account_id):
    """Return the net posted amount on the account's ledger for *txn_id*.

    Debit-positive, so a settled expense reads negative: this is what the
    double-entry ledger says the row cost, read back independently of the row.
    """
    # pylint: disable=import-outside-toplevel  -- collection-time safety, the
    # convention the shared helpers use.
    from tests._test_helpers import linked_ledger_account

    ledger_id = linked_ledger_account(db.session, account_id).id
    return (
        db.session.query(
            db.func.coalesce(db.func.sum(Posting.amount), Decimal("0"))
        )
        .join(JournalEntry, JournalEntry.id == Posting.journal_entry_id)
        .filter(
            family_journal_filter(txn_id),
            Posting.ledger_account_id == ledger_id,
        )
        .scalar()
    )


class TestTheDropdownBooksWhatTheRowCost:
    """The money control: production row 2231's $31.02."""

    def test_the_dropdown_settles_at_the_entry_sum(
        self, app, db, auth_client, seed_user, seed_periods_today,
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
        self, app, db, auth_client, seed_user, seed_periods_today,
    ):
        """Two controls, one card, one answer -- the whole point of the step.

        Two identically-shaped rows: one settled through the popover's Status
        dropdown, one through the grid's Mark Paid button.  Both must land on
        $48.98 with the same ledger effect.  Asserted as an EQUALITY between
        the two doors rather than against a constant, so a future change that
        moves both together still passes and a change that moves one fails.
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
            assert _cash_leg(
                dropdown_id, seed_user["account"].id,
            ) == _cash_leg(button_id, seed_user["account"].id)

    def test_an_entry_less_envelope_still_books_its_budget(
        self, app, db, auth_client, seed_user, seed_periods_today,
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
        self, app, db, auth_client, seed_user, seed_periods_today,
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
        self, app, db, auth_client, seed_user, seed_periods_today,
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
        self, app, db, auth_client, seed_user, seed_periods_today,
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
            # The settle's postings reverse with it: nothing is left booked.
            assert _cash_leg(txn_id, seed_user["account"].id) == Decimal("0.00")

    def test_a_BILLs_hand_typed_figure_SURVIVES_the_revert_round_trip(
        self, app, db, auth_client, seed_user, seed_periods_today,
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
        self, app, db, auth_client, seed_user, seed_periods_today,
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
        self, app, db, auth_client, seed_user, seed_periods_today,
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
        self, app, db, auth_client, seed_user, seed_periods_today,
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


class TestTheActualBoxExistsOnlyWhereTheSettleHonoursIt:
    """Ruling **R-FF** on the popover: correctable iff the MANUAL branch."""

    def test_an_envelope_with_purchases_renders_no_actual_input(
        self, app, db, auth_client, seed_user, seed_periods_today,
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
        self, app, db, auth_client, seed_user, seed_periods_today,
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
        self, app, db, auth_client, seed_user, seed_periods_today,
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
        self, app, db, auth_client, seed_user, seed_periods_today,
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
        self, app, db, auth_client, seed_user, seed_periods_today,
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
        self, app, db, auth_client, seed_user, seed_periods_today,
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
    (``transaction_service._settle._is_correction``), no input the user could
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
        self, app, db, seed_user, seed_periods_today,
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
        self, app, db, seed_user, seed_periods_today,
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


class TestAFigureArrivingALONEAtTheTransactionPATCH:
    """A PATCH carrying ONLY ``settled_amount`` must still record it.

    **The dispatch clause this grades had no firing control** (neutral review,
    2026-08-18).  ``routes/transactions/mutations`` enters the status arm when a
    status, a day OR a figure arrives; without the third term a PATCH carrying
    the figure alone fell to the ``elif _POSTING_RELEVANT_FIELDS & data.keys()``
    branch -- ``settled_amount`` is in that set -- and answered 200 having
    discarded the user's typed money value.

    Every other route test that submits a figure also submits a status, because
    the popover's Save posts the whole form; the case that reaches this clause
    is the box edited on its own, which is what the transfer door already grades
    and this one did not.
    """

    def test_a_settled_row_records_a_figure_submitted_with_no_status(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """Correct the Actual, touch nothing else, and the record moves.

        Hand arithmetic: a `$500.00` bill settles at its plan, so the cash
        account is `$500.00` down. The statement says `$245.32`, so the
        corrected record re-books that and the ledger nets to `$245.32` on the
        day the money moved -- not `$500.00`, and not `$745.32`.
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
            ).status_code == 200
            db.session.expire_all()
            settled = db.session.get(Transaction, txn_id)
            assert settled_figure(settled) == Decimal("500.00")
            day = settled.settled_on

            response = auth_client.patch(
                f"/transactions/{txn_id}",
                data={
                    "settled_amount": "245.32",
                    "version_id": str(settled.version_id),
                },
            )

            assert response.status_code == 200, response.get_data(as_text=True)
            db.session.expire_all()
            reloaded = db.session.get(Transaction, txn_id)
            assert settled_figure(reloaded) == Decimal("245.32"), (
                "a figure submitted alone was discarded and the save still "
                "answered 200"
            )
            assert status_seam.recorded_settlement(reloaded).source is (
                MovementFigureSourceEnum.TYPED
            )
            assert reloaded.status_id == ref_cache.status_id(StatusEnum.DONE)
            assert reloaded.settled_on == day, (
                "a figure correction moved the day the money moved"
            )
            assert net_posted_by_day(
                family_journal_filter(txn_id),
            ) == {day: Decimal("245.32")}

    def test_a_SECOND_correction_replaces_the_first(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """Correcting an already-CORRECTED record, which no test covered.

        Every echo test starts from a ``derived`` record, so nothing graded the
        comparison when the row already holds a human's figure -- the state a
        user is in the moment they re-open the popover after correcting once.
        The second figure must displace the first rather than be read as an echo
        of the PLAN, and the ledger must land on the second, not the sum.
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
            first = db.session.get(Transaction, txn_id)
            assert settled_figure(first) == Decimal("245.32")
            day = first.settled_on

            assert auth_client.patch(
                f"/transactions/{txn_id}",
                data={
                    "settled_amount": "251.08",
                    "version_id": str(first.version_id),
                },
            ).status_code == 200

            db.session.expire_all()
            second = db.session.get(Transaction, txn_id)
            assert status_seam.recorded_settlement(second) == status_seam.Settlement(
                Decimal("251.08"), MovementFigureSourceEnum.TYPED,
            )
            assert net_posted_by_day(
                family_journal_filter(txn_id),
            ) == {day: Decimal("251.08")}

    def test_re_posting_a_CORRECTED_figure_unchanged_writes_nothing(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """The echo rule, asked of a record that is already a correction.

        The firing control for the test above: the box is prefilled with what
        the row records, so re-saving must be a no-op even when what it records
        is itself a human's figure.  Graded on the version counter, because the
        record looks identical either way -- a write that changed nothing is
        still a write, and it is what a lost-update race is made of.  It is
        also the firing control for the test BELOW: the counter moves for a
        correction and not for an echo, and a mark that moved it for both
        would turn every second tab into a spurious 409.
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
            before = db.session.get(Transaction, txn_id)
            version_before = before.version_id

            assert auth_client.patch(
                f"/transactions/{txn_id}",
                data={
                    "settled_amount": "245.32",
                    "version_id": str(version_before),
                },
            ).status_code == 200

            db.session.expire_all()
            after = db.session.get(Transaction, txn_id)
            assert settled_figure(after) == Decimal("245.32")
            assert after.version_id == version_before, (
                "an echoed prefill wrote the row anyway"
            )

    def test_a_stale_tab_cannot_overwrite_a_figure_correction(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """Two tabs, one pin: a figure correction moves the row's counter.

        **The plain row's half of the transfer's two-tab lost update**
        (``transfer_service._update._bump_parent_version_if_a_leg_moved``),
        armed by plan step ``balance:X-bi-4b-2``.  A figure correction writes
        the covering movement and nothing of the row: through ``X-bi-4b-1``
        the seam wrote the figure onto the row's own ``settled_amount`` too,
        so the row was dirty and its counter moved for free; with those
        columns gone the row stayed clean, tab B's stale pin still matched,
        and B's prefilled ``$200.00`` overwrote A's ``$214.37`` as a 200.
        ``status_seam._covering._record_moved`` marks the row whenever its
        record nets a change, so the pin the popover holds is stale the
        moment another tab corrects the figure.
        """
        with app.app_context():
            txn = create_envelope_txn(
                seed_user, db.session, seed_periods_today[3],
                "Electricity", Decimal("200.00"),
            )
            txn.template.is_envelope = False
            db.session.commit()
            txn_id = txn.id
            assert auth_client.post(
                f"/transactions/{txn_id}/mark-done",
            ).status_code == 200
            db.session.expire_all()
            shared_pin = db.session.get(Transaction, txn_id).version_id

            first = auth_client.patch(
                f"/transactions/{txn_id}",
                data={"settled_amount": "214.37", "version_id": str(shared_pin)},
            )
            assert first.status_code == 200, first.get_data(as_text=True)
            db.session.expire_all()
            assert db.session.get(Transaction, txn_id).version_id > shared_pin, (
                "a figure correction left the row's counter behind"
            )

            second = auth_client.patch(
                f"/transactions/{txn_id}",
                data={"settled_amount": "200.00", "version_id": str(shared_pin)},
            )

            assert second.status_code == 409, (
                "a stale tab overwrote a figure correction and reported success"
            )
            db.session.expire_all()
            assert settled_figure(db.session.get(Transaction, txn_id)) == (
                Decimal("214.37")
            ), "the stale save landed anyway"


class TestAChangedFigureBesideARevert:
    """A retyped Actual submitted with Projected is REFUSED, not discarded.

    **The measured defect** (neutral review, 2026-08-18): the submission rule
    read the STATUS alone, so it could not tell an untouched prefill from a
    number the user had just typed and dropped both.  End to end, exactly as a
    user meets it: a bill settled at a hand-typed `$245.32`, re-read off the
    statement as `$214.37`, corrected and reverted in one Save -- HTTP 200, no
    message, and the row still recording `$245.32`.

    That is worse than the settle DAY's analogue ruling **R-EG** was written
    for.  A revert CLEARS the day, so a dropped day changes nothing; a revert
    RETAINS the figure, and ``Settlement.from_settle`` deliberately re-books a
    retained correction -- so the dropped figure became a silently WRONG
    booking on the next settle, promised on the grid by the re-book notice.

    The two halves of the rule are graded together here, because it is the
    DIFFERENCE between them that was missing: an echo still drops (the unlock
    path must keep working) and a change is refused.
    """

    @staticmethod
    def _settled_at(auth_client, seed_user, session, period, figure):
        """Return the id of a plain bill settled at a hand-typed *figure*."""
        txn = create_envelope_txn(
            seed_user, session, period, "Electricity", Decimal("500.00"),
        )
        txn.template.is_envelope = False
        session.commit()
        txn_id = txn.id
        assert auth_client.post(
            f"/transactions/{txn_id}/mark-done",
            data={"settled_amount": figure},
        ).status_code == 200
        session.expire_all()
        return txn_id

    def test_a_CHANGED_figure_beside_a_revert_is_refused(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """The user typed $214.37 and picked Projected. Neither is guessed at.

        Refused with the sentence that names both acts, and the row is left
        exactly as it was -- still Paid, still recording `$245.32` -- so the
        user can correct the figure and then revert, in that order.
        """
        with app.app_context():
            txn_id = self._settled_at(
                auth_client, seed_user, db.session,
                seed_periods_today[3], "245.32",
            )
            settled = db.session.get(Transaction, txn_id)

            response = auth_client.patch(
                f"/transactions/{txn_id}",
                data={
                    "status_id": str(ref_cache.status_id(StatusEnum.PROJECTED)),
                    "settled_amount": "214.37",
                    "version_id": str(settled.version_id),
                },
            )

            assert response.status_code == 400, (
                "a figure the user CHANGED was swallowed by the revert"
            )
            assert "has nothing to record" in response.get_data(as_text=True)
            db.session.expire_all()
            after = db.session.get(Transaction, txn_id)
            assert after.status_id == ref_cache.status_id(StatusEnum.DONE), (
                "a refused request reverted the row anyway"
            )
            assert settled_figure(after) == Decimal("245.32")

    def test_an_UNTOUCHED_box_beside_a_revert_still_unlocks(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """The firing control, and ruling R-EG's whole point.

        The popover PREFILLS the box with what the row records and submits every
        input it renders, so the ordinary revert carries `$245.32` back.  That
        is not an assertion that this much moved -- the user picked Projected --
        and refusing it would break the documented unlock path on every settled
        row.  Without this case the test above passes against a rule that
        refuses every figure.
        """
        with app.app_context():
            txn_id = self._settled_at(
                auth_client, seed_user, db.session,
                seed_periods_today[3], "245.32",
            )
            settled = db.session.get(Transaction, txn_id)

            response = auth_client.patch(
                f"/transactions/{txn_id}",
                data={
                    "status_id": str(ref_cache.status_id(StatusEnum.PROJECTED)),
                    "settled_amount": "245.32",
                    "version_id": str(settled.version_id),
                },
            )

            assert response.status_code == 200, response.get_data(as_text=True)
            db.session.expire_all()
            after = db.session.get(Transaction, txn_id)
            assert after.status_id == ref_cache.status_id(StatusEnum.PROJECTED)
            # The ASSERTION is withdrawn and WHAT MOVED is kept, so the
            # revert / edit / re-settle round trip stays lossless.
            assert after.settled_on is None
            assert status_seam.recorded_settlement(after).amount == Decimal("245.32")


class TestAReSubmittedDayDoesNotRestateItsBASIS:
    """Plan step **X-az**: the ECHO rule at the two STATUS doors.

    **The whole form posts on Save**, which is this suite's own premise, and the
    settle-day box is part of it -- so an untouched Save re-submits the day the
    row already carries.  Stamping that ``entered`` rewrites what the row knew
    about its own day: a reconcile-panel UPPER BOUND, or a day the bank stated,
    becomes the owner's own typing, with the day unchanged so nothing releases
    the clearing link and nothing tells anyone.

    The entry PATCH has its own controls (``test_entries.py``) and it is where
    the cost was measured -- **59 of 66 linked purchases, `$4,173.07`** on
    production.  These are the two doors that share the rule through
    ``status_seam.settle_day_for_status``: the transaction PATCH here, and the
    SHADOW branch below, which grades the transfer PATCH's copy of it.
    """

    @staticmethod
    def _settled_on_a_bound(auth_client, db, seed_user, period):
        """Return a Paid row whose day is an ``asserted`` BOUND, as the panel leaves it."""
        txn = _gas_envelope(seed_user, period)
        txn_id = txn.id
        assert auth_client.post(
            f"/transactions/{txn_id}/mark-done",
        ).status_code == 200
        db.session.expire_all()
        paid = db.session.get(Transaction, txn_id)
        record_settle_day(paid, an_asserted_day(paid.settled_on))
        db.session.commit()
        return db.session.get(Transaction, txn_id)

    def test_an_untouched_save_keeps_the_asserted_basis(
        self, app, db, auth_client, seed_user, seed_periods_today,
    ):
        """The defect, at the transaction PATCH.

        Drop the ``recorded`` argument at
        ``routes/transactions/mutations.py``'s ``settle_day_for_status`` call
        and this fails: every submission is then a fresh assertion, so the bound
        is reported as a day the owner typed.
        """
        with app.app_context():
            paid = self._settled_on_a_bound(
                auth_client, db, seed_user, seed_periods_today[3],
            )
            txn_id, day = paid.id, paid.settled_on

            resp = _full_edit_save(
                auth_client, paid, paid.status_id,
                settled_on=day.isoformat(),
            )

            assert resp.status_code == 200
            db.session.expire_all()
            saved = db.session.get(Transaction, txn_id)
            assert saved.settled_on == day
            assert recorded_settle_day(saved) == an_asserted_day(day)

    def test_a_day_the_owner_really_MOVED_is_their_own(
        self, app, db, auth_client, seed_user, seed_periods_today,
    ):
        """The firing half: a genuine correction still records ``entered``.

        Without it the rule is satisfied by one that never restates the basis,
        which would report a day the owner typed as one a balance bounded --
        the same laundering in the other direction.
        """
        with app.app_context():
            paid = self._settled_on_a_bound(
                auth_client, db, seed_user, seed_periods_today[3],
            )
            txn_id = paid.id
            corrected = paid.settled_on - timedelta(days=2)

            resp = _full_edit_save(
                auth_client, paid, paid.status_id,
                settled_on=corrected.isoformat(),
            )

            assert resp.status_code == 200
            db.session.expire_all()
            saved = db.session.get(Transaction, txn_id)
            assert recorded_settle_day(saved) == an_entered_day(corrected)
