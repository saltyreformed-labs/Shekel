"""
Shekel Budget App -- Transaction route package: mutation handlers.

Every state-changing transaction route on a single row: the PATCH inline
edit save, the DELETE soft/hard delete, and the status workflow
(mark-done, mark/unmark credit, cancel).  A transfer's LEG is not a row
these doors admit (leaf ``balance:X-bi-6-1``, ruling **R-BAL87**): the grid
draws it off the parent and its cell calls the transfer's own routes, so the
three shadow branches this package carried (``_shadow_mutations``, which
re-expressed a request on a shadow row as a transfer update) are gone, and a
request naming a shadow row is a 404 at the ownership helper.

The edit and status concerns share this one module deliberately: their
paths are near-identical parallel code (the apply +
posting reconcile + commit body, the ``StaleDataError`` / ``IntegrityError``
tails, the ``RenderTarget`` response handling).  Splitting those across
modules would re-surface the intra-file duplication the monolith hid (R0801
is cross-file only); co-locating them keeps that intentional parallel code in
one file.
"""

import logging

from flask import request
from flask_login import current_user
from marshmallow import ValidationError as MarshmallowValidationError
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm.exc import StaleDataError

from app import ref_cache
from app.enums import StatusEnum
from app.extensions import db
from app.models.ref import Status
from app.services import (
    credit_workflow,
    posting_service,
    state_machine,
    status_seam,
    transaction_service,
)
from app.services.match_press import NOTHING_SHOWN, Press
from app.services.settle_day import recorded_settle_day
from app.exceptions import NotFoundError, ValidationError
from app.utils.auth_helpers import require_owner
from app.utils.balance_predicates import is_credit
from app.routes.transactions._bp import transactions_bp
from app.routes.transactions._field_updates import (
    _apply_field_updates,
    _re_files_the_row,
)
from app.routes.transactions._gates import (
    _reject_tracking_on_income,
    _reject_generated_due_date_edit,
    _reject_typed_payback_figure,
    _resolve_status_change,
    _stale_form_conflict,
)
from app.routes.transactions._press import _mark_paid_posted, _refused
from app.routes._authored_figure import figure_was_authored
from app.routes._typed_figure import typed_figure
from app.utils.rendered_figure import as_rendered_field
from app.routes._render_helpers import render_transaction_cell
from app.routes._shown_lines import read_posted
from app.routes.transactions._helpers import (
    _credit_payback_idempotent_response,
    _deleted_row_change_refusal,
    _door_naming_a_gone_row,
    _error_transaction_response,
    _finalised_edit_response,
    _get_owned_transaction,
    _INVALID_REFERENCE_MSG,
    _mark_done_schema,
    _mark_done_success_response,
    _delete_dialog_schema,
    _stale_transaction_response,
    _unmark_credit_schema,
    _update_schema_for,
    _verify_owned_fks_in_update,
)
from app.utils.error_fragments import flatten_schema_errors

logger = logging.getLogger(__name__)


# The PATCH fields whose change can alter a transaction's posted double-entry
# ledger effect, so a change to any triggers a posting reconcile (Build-Order
# Step 3, the transaction analog of ``transfer_service._POSTING_RELEVANT_FIELDS``).
# ``status_id`` flips the settled/unsettled target; ``estimated_amount`` is the
# row's PLAN and ``settled_amount`` what it RECORDED, and the status decides
# which one is the magnitude posted (``row_valuation.settled_figure``, plan step
# X-au-c3 -- it was ``COALESCE(actual, estimated)``); ``category_id``
# moves which counter (category) ledger account the expense/income leg books
# into.
#
# ``settled_on`` IS here, and its inclusion is ruling **R-ED**'s second half:
# since plan step E1a a settle's civil day IS the ``entry_date`` its postings
# are filed under, so moving the day moves the day every posting counts from.
# ``sync_transaction_postings`` reconciles per ``(period, entry_date)``, so the
# entry at the OLD day reverses at that day and the effect re-posts at the new
# one (finding **N-13**), and its tail re-derives the account's anchor
# corrections that the moved delta staled.  Without it the rendered balance
# would move while the posted ledger kept the stale date -- the books silently
# out of step with the screen, on the one surface this arc exists to keep in
# step.
#
#
# ``pay_period_id`` IS here since plan step X-f3b (ruling **R-FM**), and the
# reason it was not is the reason it must be: the omission was justified by "a
# settled row's period cannot move anyway (the finalised-edit lock blocks
# editing it unless the same PATCH reverts to Projected)", which was sound
# while only a SETTLED row held postings.  A PROJECTED envelope now holds one
# per purchase whose bank posting day is recorded, and a posting carries the
# BUDGET column its source row is attributed to -- so moving such a row without
# a reconcile leaves its purchases' legs filed under the period it left, which
# is the R2 attribution rule broken on the one row type that can now reach it.
# The reconcile keys by ``(period, entry date)``, so the move reverses the old
# key and posts the new one in one balanced pair, exactly as a revert-and-move
# already does for a settled row.
#
# The other PATCH fields (``notes`` / ``name`` / ``due_date`` /
# ``is_envelope`` / the visibility flags) move none of
# those, so a metadata-only edit raises no reconcile.  The reconcile is
# idempotent, so listing a field that did not move the effect is a harmless
# no-op; this set is the cheap pre-filter that avoids a ledger round-trip on a
# pure metadata edit.
#
# **A PLACED row's ``category_id`` never reaches this filter** (plan step
# ``balance:X-bi-7b``): it is the DEFINITION's, so ``_apply_field_updates``
# takes it out of ``data`` and lands it there, and the propagation that
# brings the row into line reconciles the row's postings itself.  What that
# spares is narrow -- the card posts ``status_id`` on every save, so the
# status arm reconciles the row again anyway, idempotently -- but the filter
# should not name a key this door no longer writes to the row.
_POSTING_RELEVANT_FIELDS = frozenset({
    "status_id", "estimated_amount", "settled_amount", "category_id",
    "settled_on", "pay_period_id",
})

def _apply_regular_update(txn, txn_id, data, *, target_period, posted):
    """Apply a PATCH update to a transaction (the inline edit save).

    Runs the three pre-mutation gates, writes the submitted fields
    (:func:`_apply_field_updates`) and applies the requested status through
    ``transaction_service.apply_requested_status`` -- the status FIRST where
    it lifts a finalised row's lock, the fields first otherwise, and the
    ledger reconciled after the fields either way (*unlock, edit, lock*,
    ruling **R-BAL58**; the comment at ``unlocks`` below) -- deletes the auto-generated
    payback when the change reverts a Credit row (mirroring ``unmark_credit``
    via the shared ``credit_workflow.delete_payback_on_credit_revert``), and
    commits under the optimistic lock.  A ``pay_period_id`` change relocates the
    row across the grid, so it triggers a full ``gridRefresh`` instead of the
    in-place ``balanceChanged`` swap -- and so does a placed row's rename or
    re-category since plan step ``balance:X-bi-7b``, which moves it to
    another grid row (ruling **R-BAL34**).

    **This handler does not know what a status change means, and that is plan
    step X-ap.**  It used to call the status SEAM -- the mechanics primitive --
    so picking Paid in the popover's Status dropdown flipped an envelope-tracked
    row into the settled band without ever consulting its purchases: a `$25`
    purchase against a `$400` estimate booked `$400` here and `$25` through the
    grid's own Mark Paid button, from two controls in the same card (finding
    **N-219**).

    Args:
        txn: The Transaction being edited.
        txn_id: The transaction's id, used for stale-conflict logging.
        data: The schema-loaded PATCH payload (``version_id`` already
            popped by the caller).
        target_period: The submitted paycheck as the owner's calendar
            derived it, or ``None`` when the payload names none --
            :func:`_verify_owned_fks_in_update`'s answer, threaded so a
            one-off's re-placing (ruling **R-BAL33**) reads the paycheck's
            start off the derivation the FK probe already made.
        posted: What the card said about the bank lines this save frees
            (``routes._shown_lines.read_posted``), for the status verb's
            removal act and for answering its refusal (ruling **R-CC128**).

    Returns:
        A Flask response tuple: the updated cell + ``gridRefresh`` (on a
        period move or a re-filed row) or ``balanceChanged`` on success, a 409 conflict
        cell on a concurrent commit, or a 400 on a rejected status
        change, a locked-field edit of a finalised row (#26), the income
        purchase-tracking guard, an amount the settle would discard, or a bad
        FK.
    """
    # The three pre-mutation gates share ONE error exit, the shape
    # ``transfers.update_transfer`` already uses, so each refusal this arc adds
    # does not push the handler past pylint's too-many-returns threshold.
    # ``or`` preserves the precedence the guards depend on: an illegal status
    # change reports its own message BEFORE the finalised-field lock speaks, and
    # each returns ``None`` when it passes.
    #
    # Gate 1, ``_resolve_status_change``: the state-machine transition check,
    # run before any column is mutated so a rejection leaves the row untouched.
    # Gate 2, the finalised-row edit lock (#26): a Paid/Received/
    # Credit/Cancelled row's money/period/category/due-date fields cannot be
    # rewritten unless this same request reverts it to Projected.
    # Gate 3, purchase tracking is expense-only.
    # The refusal this request answers with: the gates' here, the fields' below.
    refusal = (
        _resolve_status_change(txn, data)
        or _finalised_edit_response(txn, data)
        or _reject_tracking_on_income(txn, data)
        # A payback's figure is not its own to state (N-252); see the gate.
        or _reject_typed_payback_figure(txn, data)
        # Gate 5: a generated row's due date is its definition's, and since
        # plan step X-au-e it is also what prices the row (balance:X-au-e).
        or _reject_generated_due_date_edit(txn, data)
    )
    if refusal is not None:
        return refusal

    # Detect a period move before the setattr loop mutates the row.  A
    # move relocates the row to a different period in the grid, which an
    # in-place cell swap (hx-target="#txn-cell-<id>") cannot express --
    # the cell would re-render in its old position.  When the period
    # actually changes the response triggers a full grid refresh (see
    # the ``HX-Trigger`` selection at the end of the handler), matching
    # the ``gridRefresh`` pattern carry-forward uses for cross-period
    # moves.
    period_changed = (
        "pay_period_id" in data and data["pay_period_id"] != txn.pay_period_id
    )

    # Did a HUMAN type the figure in the Estimated box (ruling **R-JR**, plan
    # step X-au-h)?  It sits beside the period's question because they are the
    # two acts that make a row the owner's -- but only ``period_changed`` MUST
    # precede the setattr loop; this one reads the payload alone.
    #
    # **The door used to ask whether the field was PRESENT**, true of every
    # save that renders an amount box -- so a notes-only edit took ownership of
    # a figure nobody chose and the row stopped tracking its definition.  That
    # is finding **N-248**.
    #
    # The companion is popped because it is not a column, and
    # ``_apply_field_updates`` ``setattr``s every key it does not recognise.
    amount_authored = figure_was_authored(data, "estimated_amount")
    data.pop(as_rendered_field("estimated_amount"), None)

    # Does this edit move the row to ANOTHER GRID ROW -- a placed row's name
    # or category, which are its definition's (plan step balance:X-bi-7b,
    # ruling R-BAL34)?  Asked BEFORE the field write for the same reason
    # ``period_changed`` is: the write lands the new values on the
    # definition, after which the question cannot be asked.
    re_filed = _re_files_the_row(txn, data)

    # Detect a Credit reversion before the setattr loop rewrites
    # status_id.  A Credit row leaving Credit status (the state machine
    # only admits Credit -> Projected besides identity) must delete its
    # auto-generated payback exactly like unmark_credit -- otherwise the
    # PATCH path orphans the payback and inflates the next period's
    # projected expenses.  An identity re-submit (Credit -> Credit)
    # keeps the payback.
    reverts_credit = (
        "status_id" in data
        and is_credit(txn)
        and data["status_id"] != ref_cache.status_id(StatusEnum.CREDIT)
    )

    # Apply the status and the settle day through the ONE status verb, in ONE
    # call, because they are ONE fact: a row is settled if and only if it
    # carries the day its money moved, and the seam underneath writes both in
    # the same statement.  The status is the SUBMITTED one when the PATCH
    # carried one, else the row's own -- a day-only edit is an identity
    # transition, exactly as the transfer side's day correction is
    # (:func:`transfer_service._status.apply_status_to_all_three`).
    # ``settle_day_for_status`` drops a day submitted alongside a revert out of
    # the settled band (ruling **R-EG**), so the documented unlock path (set
    # Status to Projected to edit the amounts) is not broken by the form
    # re-submitting the day the row already carried.
    #
    # **This route does NOT decide what applying a status MEANS** --
    # ``transaction_service.apply_requested_status`` does, and it reconciles the
    # ledger as part of the act.  Calling the seam here instead is finding
    # **N-219**: the seam is the MECHANICS primitive, so a door that reaches for
    # it settles a row without ever asking what the row is worth.
    #
    # ``_resolve_status_change`` already pre-verified a SUBMITTED transition for
    # error precedence, and the identity transition a day-only edit performs is
    # always legal (``state_machine``'s module docstring) -- so the TRANSITION
    # arm never raises here for a recognised status.  The DAY arm can: ruling
    # **R-EJ** refuses a settle day that has not happened yet, and that is
    # ordinary user input from the correction box.  A day-only PATCH also
    # reaches the seam with NO prior ``verify_transition`` (that gate returns
    # early when no status was submitted), so a corrupt ``status_id`` surfaces
    # here too.  ``_error_transaction_response`` rolls the session back, which
    # discards the ``setattr`` loop's already-staged mutations above.
    #
    # ``settle_day_for_status`` itself can refuse too (ruling **R-EL** bounds a
    # forwarded day below at the schedule's start), so it shares the same
    # ``except``: both are ordinary input from the correction box, and both must
    # render as a designed 400 with the staged ``setattr`` mutations rolled back.
    new_status_id = data.get("status_id", txn.status_id)

    # **UNLOCK, EDIT, LOCK** (plan step ``balance:X-bi-7c``, ruling **R-BAL58**,
    # developer 2026-09-16).  A finalised row's item fields are locked
    # (``state_machine.finalised_edit_rejection``; the popover disables them)
    # and a placed row's definition propagation rewrites Projected rows only.
    # A request that LIFTS the lock -- reverts the row to a mutable status
    # (``state_machine.lock_lifts``, the gate's own clause for the "revert and
    # correct" edit) -- applies the status transition FIRST, so the edits land
    # on an unlocked row; one that settles it edits first, so the settle reads
    # the final fields (the untick-and-settle save needs the definition's flag
    # before the lock).  Found by moving the suite's one-off fixtures onto the
    # producer: a revert + re-category in one PATCH landed the category on the
    # DEFINITION and the propagation skipped the still-settled row, so the
    # definition said Rent, the row still said Groceries, and the next settle
    # posted `$50.00` to Groceries -- every one-off's shape after the cutover.
    #
    # **The ledger is reconciled LAST in BOTH orders.**  The status verb
    # reconciles at the transition (``apply_requested_status``, so no door can
    # forget it); in the unlock order that reconcile reads the PRE-edit row,
    # and an edit that moves a posting-relevant field afterwards -- the period
    # or the category of an envelope whose purchases are posted -- re-files
    # nothing by itself: the purchase legs stayed under the old paycheck
    # (found by 7c-1's adversarial review, measured on a settled envelope
    # reverted and moved in one PATCH).  So the unlock order owes the row the
    # same end-of-handler reconcile the no-status path has always made, which
    # is the discipline ``transaction_service._door`` states: reconciled after
    # the caller's own field writes, reading the final amount and category.
    unlocks = "status_id" in data and state_machine.lock_lifts(
        db.session.get(Status, txn.status_id),
        db.session.get(Status, data["status_id"]),
    )

    # **The three excepts cover the WHOLE tail, and they were split by the order
    # the phases were written in rather than by a decision** -- the same
    # unification :func:`_mark_done_regular` records, forced here by the same
    # cause: the status verb now reconciles the ledger, so one call raises both
    # the designed ``ValidationError`` (a 400) and the concurrency
    # ``StaleDataError`` (a 409) and cannot be split across two nets.  Nothing
    # moves the other way: ``PostingError`` is a SIBLING of ``ValidationError``
    # under ``ShekelError`` rather than a subclass, so a broken ledger invariant
    # still fails loud instead of rendering as a designed refusal, and
    # ``credit_workflow.delete_payback_on_credit_revert`` raises neither (its
    # three ``ValidationError`` siblings are in ``mark_as_credit`` /
    # ``unmark_credit``, which this path does not call).
    try:
        # ONE press for the save (R-CC135); a field refusal abandons it.
        with Press(posted.shown) as one_save:
            if unlocks:
                _apply_status_or_postings(txn, data, new_status_id, one_save)
            # Write the submitted fields, flag a template row as overridden, and
            # refuse an amount the settle would discard -- three acts whose ORDER is
            # load-bearing and is documented at the helper.  Extracted so this
            # handler keeps one exit per concern rather than one per rule.
            #
            # **INSIDE the net, because it FLUSHES.**  Its derived-amount guard asks
            # ``settles_from_entries``, which lazy-loads ``template`` (or
            # ``entries``) and so autoflushes the ``setattr`` loop's staged
            # mutations as the version-pinned UPDATE.  Left above the ``try`` that
            # was the request's FIRST flush sitting outside its own exception net: a
            # concurrent commit surfaced as a 500 instead of the designed 409, and a
            # period move whose ``is_override`` had not yet been written tripped
            # the generation index (then keyed on the paycheck, now
            # ``idx_transactions_template_scenario_undated`` for an undated row)
            # as an uncaught ``IntegrityError``.  Found by adversarial review; the comment below
            # claimed the three excepts covered the whole tail, and they covered the
            # tail while the first flush had moved above it.
            refusal = _apply_field_updates(
                txn, data,
                amount_authored=amount_authored, period_changed=period_changed,
                target_period=target_period,
            )
            if refusal is not None:
                one_save.abandon()
                return refusal
            if not unlocks:
                _apply_status_or_postings(txn, data, new_status_id, one_save)
            elif _POSTING_RELEVANT_FIELDS & data.keys():
                posting_service.sync_transaction_postings(txn)
            if reverts_credit:
                # Inside the StaleDataError net deliberately: the payback
                # lookup autoflushes the already-dirtied row (the
                # version-pinned UPDATE), so a concurrent commit surfaces
                # here as StaleDataError and must yield the 409 conflict
                # cell, not a 500.  The helper does not commit -- the
                # deletion joins this request's commit so the status flip
                # and the payback removal land atomically, under what the card
                # named for the payback (ruling **R-CC80**).
                credit_workflow.delete_payback_on_credit_revert(
                    txn, current_user.id, press=one_save,
                )
        db.session.commit()
    except (NotFoundError, ValidationError) as exc:
        # A "Paid from" account that is not the ROW's owner's (plan step
        # ``credit_card:CC-5-3``) is the security response rule's 404, as the
        # designed fragment -- see ``_mark_done_regular``'s arm; a domain
        # refusal is the 400 it always was, and a card out of date is
        # redrawn (ruling **R-CC128**).  One arm, because this handler is at
        # pylint's return ceiling.
        return _refused(txn_id, exc, posted)
    except StaleDataError:
        logger.info(
            "Stale-data conflict on update_transaction id=%d", txn_id,
        )
        return _stale_transaction_response(txn_id)
    except IntegrityError:
        return _error_transaction_response(txn_id, _INVALID_REFERENCE_MSG)
    logger.info("user_id=%d updated transaction %d", current_user.id, txn_id)

    # A period move needs a full grid refresh so the row appears under
    # its new period, and so does a placed row renamed or re-categorised
    # (it moves to another grid row); an in-place edit only needs the
    # balance rows recomputed.  ``gridRefresh`` reloads the page (app.js);
    # the returned cell still swaps first, which is harmless before reload.
    response = render_transaction_cell(txn)
    return response, 200, {
        "HX-Trigger": (
            "gridRefresh" if period_changed or re_filed else "balanceChanged"
        ),
    }


def _apply_status_or_postings(txn, data, new_status_id, press: Press):
    """Apply the status the payload asks for, else reconcile the edited row.

    The status half of :func:`_apply_regular_update`, in one place so the
    UNLOCK order there can run it before the field writes and the ordinary
    order after them.  A payload naming a status, a settle day or a settled
    figure goes through ``transaction_service.apply_requested_status`` (the
    route layer's one status entry point, plan step X-ap), which reconciles
    the ledger itself; one naming none of them but a posting-relevant field
    owes the row its own reconcile.  Every comment below is the arm's own,
    moved with it.

    Args:
        txn: The Transaction being edited.
        data: The schema-loaded PATCH payload.
        new_status_id: The status the payload asks for, or the row's own.
        press: The save's open press, for the verb's removal act (R-CC135).
    """
    # ``recorded`` is what makes the reading ECHO-AWARE (plan step X-az):
    # this form prefills the settle-day box, so an untouched Save re-submits
    # the day the row already carries -- and without the stored pair the
    # rule would restamp that day's BASIS as the owner's own typing, which
    # is finding **N-332**'s own laundering arriving through the edit door.
    settle_day = status_seam.settle_day_for_status(
        current_user.id, new_status_id, data.get("settled_on"),
        recorded_settle_day(txn),
    )
    # **A submitted FIGURE is a third reason to enter the status arm**, and
    # without it the door never saw one (found by two independent
    # adversarial reviews, 2026-08-17).  ``apply_requested_status`` decides
    # what a figure MEANS -- a correction on a row already settled, a
    # dropped echo on the way out of the band, a refusal for a figure the
    # user CHANGED beside a revert -- but this dispatch reached it only when
    # a STATUS or a DAY arrived too, so a PATCH carrying ``settled_amount``
    # alone answered 200 having discarded it.  ``new_status_id`` defaults to
    # the row's CURRENT status (above), so such a request is an identity
    # move.  **The route no longer grades the figure itself** (2026-08-18):
    # it called ``settled_amount_for_status``, which read the STATUS alone
    # and so could not tell an untouched prefill from a number the user had
    # just retyped.  That rule needs the row, and the door has it.
    # **A figure out of the Actual box is a PERSON's statement**, and the
    # route says so with the figure (plan step X-bi-3e-1, ruling R-BAL61):
    # the door takes the amount and who wrote it as one value, and this is
    # the full-edit popover, never the bank.
    submitted_figure = typed_figure(data.get("settled_amount"))
    # **A submitted TENDER is a fourth reason** (plan step
    # ``credit_card:CC-5-3``), for the reason the figure is a third: the
    # popover posts its "Paid from" picker on every Save, and the door -- not
    # the route -- decides what it MEANS (an echo dropped, a re-point on a
    # settled row, a refusal beside a revert), which needs the row.
    tender_account_id = data.get("tender_account_id")
    if (
        "status_id" in data
        or settle_day is not None
        or submitted_figure is not None
        or tender_account_id is not None
    ):
        transaction_service.apply_requested_status(
            txn, new_status_id, settle_day=settle_day,
            stated=transaction_service.StatedRecord(
                figure=submitted_figure,
                tender_account_id=tender_account_id,
            ),
            press=press,
        )
    elif _POSTING_RELEVANT_FIELDS & data.keys():
        # Posting ledger reconcile (Build-Order Step 3) for the edit that
        # moves a posted effect WITHOUT touching the status: a re-category,
        # a corrected amount.  The status arm above owns the reconcile for
        # every other case, which is what keeps this request to exactly ONE
        # ledger round-trip -- ``status_id`` and ``settled_on`` are both in
        # ``_POSTING_RELEVANT_FIELDS``, so an ungated call here would be a
        # second reconcile of the same row on every status change.  (The
        # UNLOCK order is the one request that makes two, and the caller
        # says why.)  Placed
        # LAST -- NOT at the field write -- so it reads the FINAL amount and
        # category, the exact discipline
        # ``transfer_service.update_transfer`` documents (the 2.8b HIGH: a
        # settle-and-recategorize PATCH applies category_id after status_id,
        # so posting at the flip would book the stale category).  The
        # reconcile reads the OLD category's posted legs back from the
        # ledger by transaction_id, so a revert-and-recategorize reverses
        # the old category cleanly whichever order the door ran it in --
        # before the field writes since the UNLOCK order (plan step
        # ``balance:X-bi-7c``), after them until then, when
        # ``txn.category_id`` already pointed at the new one (the 2.8
        # CRITICAL).  Gated so a notes-only
        # edit posts nothing.  Inside the StaleDataError net for the same
        # reason as the caller's payback delete: the reconcile's flush
        # autoflushes the version-pinned row, so a concurrent commit
        # surfaces here as a 409, not a 500.
        posting_service.sync_transaction_postings(txn)


@transactions_bp.route("/transactions/<int:txn_id>", methods=["PATCH"])
@require_owner
@_door_naming_a_gone_row(_deleted_row_change_refusal)
def update_transaction(txn, _target):
    """Update a transaction's fields (inline edit save).

    Returns the updated cell fragment.  Sends an HX-Trigger header
    to refresh the balance row.

    Optimistic locking (commit C-18 / F-010) operates in two layers:

      1. Stale-form check: the cell ships ``version_id`` as a hidden
         input set to ``Transaction.version_id`` at render time.
         When the submitted value differs from the row's current
         counter, the handler short-circuits with a 409 + conflict
         cell partial and records nothing.  This catches the
         sequential Tab-1/Tab-2 race documented in C-17.

      2. SQLAlchemy ``version_id_col``: any concurrent flush that
         races past the stale-form check is still narrowed by
         ``WHERE version_id = ?`` at the database tier; the loser
         raises ``StaleDataError`` which the handler converts into
         the same 409 + conflict cell.  The two layers together
         close every interleaving the optimistic-lock contract is
         meant to cover.

    Route-boundary FK ownership (commit C-29 / F-029 of the
    2026-04-15 security remediation plan): when the schema accepts
    a user-scoped FK -- ``pay_period_id`` or ``category_id`` -- the
    submitted id is verified against ``current_user.id`` here,
    before any state-changing work runs.  Without this probe an
    authenticated owner could submit another user's
    ``pay_period_id`` or ``category_id`` and the unfiltered
    ``setattr`` loop would silently re-parent the transaction into
    the victim's namespace (the FK row exists, the FK constraint
    passes, and PostgreSQL never raises ``IntegrityError``).
    ``status_id`` is a reference table FK (not user-scoped) and so
    does not need an ownership check.  The probe runs before the
    stale-form check so the security response wins where one request
    triggers both -- matching the layered defense
    ``transfers.update_transfer`` received in commit C-27.  (It ran
    before the transfer-shadow branch as well, until leaf
    ``balance:X-bi-6-1`` deleted that branch.)

    **The door is** :func:`_door_naming_a_gone_row` (plan step
    ``credit_card:CC-5-4a-4``): a Save on a row deleted in another tab, or
    while this one waited, answers the red "Deleted" cell saying "Hotel was
    deleted: this change cannot be saved.  Reload the page." (rulings
    **R-CC101**, **R-CC102**, **R-CC104**, **R-CC105**) -- "Archived" and
    "was archived" for a row whose recurring item is archived (rulings
    **R-CC107**, **R-CC108**).  **It LOGS where the door it replaced was
    silent** (review 6, L7): ``_get_owned_transaction`` refused another
    user's id and a missing id without a record, where this door, through
    ``auth_helpers.get_accessible_transaction_or_deleted``, emits
    ``access_denied_cross_user`` at WARNING for the first and
    ``resource_not_found`` at INFO for the second -- the F-144 contract the
    other ownership doors keep.
    """
    # Parse and validate input.  WHICH schema is the row's shape's to say
    # (plan step balance:X-bi-7b): the flags are declared only for a row
    # whose item is editable here, so a recurring row's crafted flag is
    # dropped before any code could land it on the dead cell (BAL-484).
    schema = _update_schema_for(txn)
    errors = schema.validate(request.form)
    if errors:
        # Designed fragment (marker-header convention): the cell
        # re-rendered with the flattened field errors in its hint,
        # so a rejected card Save is visible instead of silently
        # dropped by the app-wide htmx config.
        return _error_transaction_response(
            txn.id, flatten_schema_errors(errors), status=422,
        )

    data = schema.load(request.form)
    # Taken out here: it names no column, and the field loop below
    # ``setattr``s every key it does not recognise.
    posted = read_posted(data, absent=NOTHING_SHOWN)

    # Route-boundary FK ownership (commit C-29 / F-029).  Reject
    # cross-user ``pay_period_id`` / ``category_id`` before the
    # stale-form check so the
    # security response (404) takes precedence over the UX
    # response (409 conflict cell) when the same request triggers
    # both.  See :func:`_verify_owned_fks_in_update` for the
    # threat-model details.  The target paycheck it derived rides on to
    # the field write, which re-places a moved one-off on its start.
    target_period, fk_error = _verify_owned_fks_in_update(data)
    if fk_error is not None:
        return fk_error

    # Stale-form check.  Performed before any mutation so audit-log
    # triggers record only successful edits.  Conditional on the
    # form having submitted a version (clients that omit it fall
    # through to the SQLAlchemy-tier check at flush time).  ONE pin per
    # home the card edits -- the row's, and a placed row's definition's --
    # decided by :func:`_stale_form_conflict`.
    conflict = _stale_form_conflict(txn, data)
    if conflict is not None:
        return conflict

    return _apply_regular_update(
        txn, txn.id, data, target_period=target_period, posted=posted,
    )


@transactions_bp.route("/transactions/<int:txn_id>", methods=["DELETE"])
@require_owner
def delete_transaction(txn_id):
    """Remove a transaction from the books, soft or hard.

    **The HTTP shape only.**  What a delete MEANS -- which matches it
    withdraws, which payback it takes down, which postings it reverses and
    whether the row leaves the table at all -- is
    :func:`transaction_service.delete_transaction`, the verb this route and
    ``statement_match``'s undo now share (plan step ``bank_import:X-gb``).
    The sequence was spelled here and again there, and each of its four steps
    has an ORDER that is load-bearing.

    **This door has a SURFACE since X-gb** -- the delete control on the
    full-edit action card -- and finding **N-344** is that it did not: the
    route existed with full teardown logic and a census of every template and
    script found zero callers, so the 46 envelope shells one YTD statement pass
    minted could not be removed from the app at all.

    Responds with ``gridRefresh`` rather than ``balanceChanged`` because the
    row LEAVES the grid, which an in-place cell swap cannot express -- the same
    reason ``cancel_transaction`` and ``unmark_credit`` keep it.  The body is
    empty: there is no cell left to render.

    Refusals (``deletion_refusal``) come back as the designed error fragment
    the card's other controls use, so a crafted request or a stale card is told
    why rather than swapping a bare string.  A dialog out of date -- it named
    other bank lines (ruling **R-CC127**) or other purchases (ruling
    **R-CC131**) than the row holds now -- redraws the popover instead (ruling
    **R-CC128**).

    Optimistic locking (commit C-18 / F-010): the soft-delete UPDATE and the
    hard-delete DELETE are both version-pinned by SQLAlchemy.  A concurrent
    commit that bumps the row's version raises ``StaleDataError``, which the
    handler converts to a 409 + conflict cell so the user can retry against
    fresh state -- or to a 404 when that commit deleted the row, soft or hard
    (``_stale_transaction_response`` re-fetches through the ownership door,
    which answers a deleted row "not found").
    """
    txn = _get_owned_transaction(txn_id)
    if txn is None:
        return "Not found", 404
    # What the card's dialog named, which htmx sends as a DELETE's query
    # string: the bank lines (ruling **R-CC127**) and the purchases (ruling
    # **R-CC131**).  A request without them named none.
    errors = _delete_dialog_schema.validate(request.values)
    if errors:
        return _error_transaction_response(
            txn_id, flatten_schema_errors(errors), status=422,
        )
    dialog = _delete_dialog_schema.load(request.values)
    purchases_named = dialog.pop("shown_purchases", None) or frozenset()
    posted = read_posted(dialog, absent=NOTHING_SHOWN)

    try:
        with Press(posted.shown) as one_save:
            outcome = transaction_service.delete_transaction(
                txn, current_user.id, press=one_save,
                purchases_named=purchases_named,
            )
        db.session.commit()
    except ValidationError as exc:
        return _refused(txn_id, exc, posted)
    except StaleDataError:
        logger.info(
            "Stale-data conflict on delete_transaction id=%d", txn_id,
        )
        return _stale_transaction_response(txn_id)
    logger.info(
        "user_id=%d deleted transaction %d (soft=%s, matches_withdrawn=%d)",
        current_user.id, txn_id, outcome.soft, outcome.withdrawn.matches,
    )
    return "", 200, {"HX-Trigger": "gridRefresh"}


def _mark_done_regular(txn, submitted, tender_account_id, posted, target):
    """Settle a transaction.

    **The rule this used to hold is now a SERVICE verb** --
    :func:`transaction_service.settle_transaction`, ruling **R-FA** -- because
    plan step X-f2-c2 gives the reconcile panel's tick the same settle, and
    two doors restating one money rule is this arc's own root cause 1.  What
    is left here is the HTTP shape: which surface renders, and which of the
    three designed responses a failure takes.

    **The three excepts now cover the WHOLE settle, and what that changes was
    MEASURED rather than argued.**  This handler used to run the
    amount-and-status phase under an ``except ValidationError`` and the
    reconcile-and-commit phase under ``except StaleDataError / IntegrityError``
    -- a split that came from the order the phases were written in, not from a
    decision, and one no caller of a single verb could be expected to
    reproduce.  It is unified here because an argument a caller can get wrong
    is a defect rather than a contract, and X-f2-c2's reconcile writer calls
    the same verb in a loop.

    **It fixes no live defect, and the first draft of this paragraph claimed it
    did.**  Reconstructing the pre-change route from git and running the new
    control against it shows the same 409 both ways.  The reason is not visible
    by reading the phases: the FIRST flush of a settle is
    ``txn.status.is_settled``, the argument to the posting reconcile, because
    ``apply_status_change`` both dirties the row and expires ``status``, so
    reading it back refreshes and autoflushes.  That expression is inside the
    net under either topology, and everything before it reads columns or
    already-loaded relationships.  Recorded at
    ``TestTransactionStaleFormPrevention.test_mark_done_catches_a_stale_settle_as_409``.

    Nothing moves the other way either: the reconcile raises ``PostingError``,
    a SIBLING of ``ValidationError`` under ``ShekelError`` rather than a
    subclass, so a broken ledger invariant still fails loud instead of
    rendering as a designed refusal -- and no module in the reconcile's import
    closure raises ``ValidationError`` at all (measured across
    ``posting_service``, ``posting_reads``, ``_posting_write``,
    ``ledger_account_service`` and ``user_write_lock``).

    Args:
        txn: The Transaction being settled.
        submitted: The figure a human typed for what moved.  ``None`` does NOT
            mean "leave the record alone": it means nobody typed one, and the
            settle then RECORDS what it resolved on the ``derived`` basis.  The
            verb ignores it for an envelope-tracked row with entries, whose
            entries ARE the record of what it cost.
        tender_account_id: The account the form named as the one the money
            moved through (the popover's "Paid from" picker, plan step
            ``credit_card:CC-5-3``), or ``None`` when the surface carried
            none -- the cell's checkmark, the mobile card -- and the verb's
            seam books on its default (ruling **R-CC42**).
        posted: What the surface said about the bank lines the settle frees:
            the popover's captions, or the one-click's silence (ruling
            **R-CC56**).
        target: The :class:`~app.routes._mobile_card.RenderTarget` describing the response
            surface (mobile card vs desktop cell).

    Returns:
        A Flask response tuple: the success surface on commit, a 409
        conflict surface on a concurrent commit, a 404 fragment for a tender
        that is not the row owner's, a 400 on a bad FK or a rejected
        transition, or the popover redrawn when it was out of date (ruling
        **R-CC128**).
    """
    # Read before the settle: a refusal's rollback expires the row.
    txn_id = txn.id
    try:
        with Press(posted.shown) as one_save:
            transaction_service.settle_transaction(
                txn, submitted=submitted, tender_account_id=tender_account_id,
                press=one_save,
            )
        db.session.commit()
    except (NotFoundError, ValidationError) as exc:
        # ONE arm, answered by the package's one refusal decision (``_refused``,
        # which is where "not found" becomes the 404): a submitted tender that
        # is not the ROW's owner's -- or does not exist -- is the verb's 404
        # (plan step ``credit_card:CC-5-3``; the security response rule: one
        # answer for "not found" and "not yours"), as the designed fragment the
        # purchase door's tender took at CC-5-2.  A domain refusal -- the
        # envelope branch's preconditions, the illegal transition a stale
        # surface can still reach (a Mark Paid tap on a card another device
        # just cancelled; grid audit D2, F-047 / F-161), a companion's press
        # that would free a line (ruling **R-CC130**) -- is the 400 fragment
        # showing current state plus the reason; a popover out of date is
        # redrawn (ruling **R-CC128**).
        return _refused(txn_id, exc, posted, target)
    except StaleDataError:
        logger.info(
            "Stale-data conflict on mark_done id=%d", txn_id,
        )
        return _stale_transaction_response(txn_id, target)
    except IntegrityError:
        return _error_transaction_response(
            txn_id, _INVALID_REFERENCE_MSG, target,
        )
    # The status the row LANDED in, read off the row rather than from a value
    # the caller computed: the verb owns the income/expense pick now
    # (``transaction_service.settled_status_id``), and logging a requested id
    # beside a row that took a different one is how a log stops being
    # evidence.
    logger.info(
        "user_id=%d marked transaction %d status_id=%d",
        current_user.id, txn_id, txn.status_id,
    )

    return _mark_done_success_response(txn, target)


@transactions_bp.route("/transactions/<int:txn_id>/mark-done", methods=["POST"])
@_door_naming_a_gone_row(status_seam.deleted_row_payment_refusal)
def mark_done(txn, target):
    """Set a transaction's status to 'done' (expenses) or 'received' (income).

    Automatically picks the correct status based on transaction type.
    For entry-capable transactions with entries, the settle records the
    ``purchases`` basis and the entries state the figure.  For all others, it
    accepts an optional ``settled_amount`` from the form -- parsed via
    :class:`MarkDoneSchema` so a malformed numeric value returns a
    clean 422 with the Marshmallow per-field message instead of the
    legacy ``"Invalid actual amount"`` translation, and a negative
    value is rejected at the schema tier (commit C-27 / F-042 /
    F-162 of the 2026-04-15 security remediation plan).  422 (not 400)
    is the validation-error status the entry routes and
    ``coding-standards.md`` mandate (DH-#81).

    Optimistic locking (commit C-18 / F-010): the button-click path
    has no form-side ``version_id`` to compare, so the optimistic
    lock relies on SQLAlchemy's ``version_id_col`` race detection
    at flush time.  ``StaleDataError`` is converted to a 409 +
    conflict cell so the user retries against fresh state instead
    of seeing a 500.

    **The door is** :func:`_door_naming_a_gone_row` (plan step
    ``credit_card:CC-5-4a-4``), which also reads the rendering surface
    (``target``) off the form: a Mark Paid on a row deleted in another tab
    answers the cell's red
    "Deleted" or the card's banner saying "Hotel was deleted: a payment
    cannot be recorded under it.  Reload the page." (rulings **R-CC101**,
    **R-CC102**, **R-CC104**) -- "Archived" and "was archived" for a row
    whose recurring item is archived (rulings **R-CC107**, **R-CC108**).
    """
    # Validate the optional ``settled_amount`` form field once.
    # ``MarkDoneSchema`` strips empty
    # strings via its pre_load hook so the missing-field UX (a
    # button click with no body) yields it absent from the loaded
    # dict -- which means "nobody typed a figure", and the settle
    # then records what it resolved rather than leaving the row
    # unrecorded (plan step X-au-c3).
    try:
        mark_done_data = _mark_done_schema.load(request.form)
    except MarshmallowValidationError as exc:
        # Designed fragment: the requesting surface (cell or mobile
        # card) re-rendered with the per-field message in its hint /
        # banner instead of a JSON body the client silently drops.
        return _error_transaction_response(
            txn.id, flatten_schema_errors(exc.messages), target, status=422,
        )
    # The Mark Paid form's optional figure is a PERSON's (plan step
    # X-bi-3e-1, ruling R-BAL61), stated once here for both branches below.
    submitted = typed_figure(mark_done_data.get("settled_amount"))

    # The income/expense status pick is NOT made here.  It used to be, and
    # ``transaction_service.settle_from_entries`` re-derived the same id from
    # the same predicate with a comment saying it "mirrors" this line -- two
    # spellings of one rule that agreed by reading.  It is now
    # ``transaction_service.settled_status_id``, inside the verb.

    return _mark_done_regular(
        txn, submitted,
        # WHICH ACCOUNT the money moved through, when the surface said (the
        # popover's "Paid from" picker, plan step ``credit_card:CC-5-3``);
        # gated by the verb against the ROW's owner, never ``current_user``
        # (ruling **R-CC11**: a companion settles the owner's row).
        mark_done_data.get("tender_account_id"),
        # What the popover's captions named; the owner's one-click posts
        # nothing and withdraws silently (ruling **R-CC56**), and a
        # companion's may free no line (ruling **R-CC130**).
        _mark_paid_posted(txn, mark_done_data),
        target,
    )


@transactions_bp.route("/transactions/<int:txn_id>/mark-credit", methods=["POST"])
@require_owner
def mark_credit(txn_id):
    """Mark a transaction as 'credit' and auto-generate a payback expense.

    Optimistic locking (commit C-18 / F-010):
    ``StaleDataError`` -> 409 conflict cell.

    TOCTOU duplicate-payback prevention (commit C-19 / F-008): the
    request's owner write lock (:mod:`app.db_transaction`, plan step
    ``balance:X-bn``) serialises two Mark Credits; the partial unique
    index ``uq_transactions_credit_payback_unique`` backstops a writer
    that holds no request, and the IntegrityError catch below converts
    the violation into idempotent success.  (C-19's row lock, a
    ``FOR NO KEY UPDATE`` on the source row, was deleted at that step.)
    """
    txn = _get_owned_transaction(txn_id)
    if txn is None:
        return "Not found", 404

    try:
        credit_workflow.mark_as_credit(txn_id, current_user.id)
        db.session.commit()
    except StaleDataError:
        logger.info(
            "Stale-data conflict on mark_credit id=%d", txn_id,
        )
        return _stale_transaction_response(txn_id)
    except IntegrityError as exc:
        # Defensive backstop for commit C-19 -- see
        # ``_credit_payback_idempotent_response`` docstring.
        return _credit_payback_idempotent_response(exc, txn_id)
    except (NotFoundError, ValidationError) as exc:
        return _error_transaction_response(txn_id, str(exc))
    response = render_transaction_cell(txn)
    return response, 200, {"HX-Trigger": "gridRefresh"}


@transactions_bp.route("/transactions/<int:txn_id>/unmark-credit", methods=["DELETE"])
@require_owner
def unmark_credit(txn_id):
    """Revert credit status and delete the auto-generated payback.

    Optimistic locking: see :func:`mark_credit`.  The button posts the bank
    lines its caption named (plan step ``credit_card:CC-5-4a-5``, rulings
    **R-CC80** / **R-CC127**), and a card out of date is redrawn (**R-CC128**).
    """
    txn = _get_owned_transaction(txn_id)
    if txn is None:
        return "Not found", 404
    errors = _unmark_credit_schema.validate(request.args)
    if errors:
        return _error_transaction_response(
            txn_id, flatten_schema_errors(errors), status=422,
        )
    posted = read_posted(
        _unmark_credit_schema.load(request.args), absent=NOTHING_SHOWN,
    )

    try:
        with Press(posted.shown) as one_save:
            credit_workflow.unmark_credit(
                txn_id, current_user.id, press=one_save,
            )
        db.session.commit()
    except StaleDataError:
        logger.info(
            "Stale-data conflict on unmark_credit id=%d", txn_id,
        )
        return _stale_transaction_response(txn_id)
    except NotFoundError as exc:
        return str(exc), 404
    except ValidationError as exc:
        # Raised when the bespoke source-state guard or the
        # state-machine verification in
        # ``credit_workflow.unmark_credit`` rejects the request --
        # e.g. attempting to unmark a Paid row.  The fragment names
        # the offending status so the user understands why; a card out of
        # date is redrawn instead (ruling **R-CC128**).
        return _refused(txn_id, exc, posted)
    response = render_transaction_cell(txn)
    return response, 200, {"HX-Trigger": "gridRefresh"}


@transactions_bp.route("/transactions/<int:txn_id>/cancel", methods=["POST"])
@require_owner
def cancel_transaction(txn_id):
    """Set a transaction's status to 'cancelled'.

    Optimistic locking: see :func:`mark_credit`.
    """
    txn = _get_owned_transaction(txn_id)
    if txn is None:
        return "Not found", 404

    cancelled_id = ref_cache.status_id(StatusEnum.CANCELLED)

    # Route the cancel through the ONE status verb (state-machine check +
    # status_id + settled_on + the ledger reconcile).  Cancelled is reachable
    # only from Projected (or the Cancelled identity edge for idempotent
    # re-submits); a direct done -> cancelled or settled -> cancelled would
    # erase the paid/archived audit trail and raises ValidationError -> 400.
    # Cancelled is non-settled, so the seam leaves settled_on clear.  Audit
    # reference: F-047 / F-161 follow-up to commit C-21.
    #
    # The reconcile inside the verb is what this handler used to run itself:
    # reconcile to the new status's settled sense as the final step, mirroring
    # the transfer pattern (reconcile on every status change).  Cancelled is
    # non-settled and is reachable only from Projected, so it is an idempotent
    # no-op today (a Projected source has no postings to reverse), but it keeps
    # the "every status change reconciles last" invariant complete and
    # self-heals if the state machine ever admits cancelling a settled row.
    # Inside the StaleDataError net: the reconcile's flush autoflushes the
    # version-pinned row, so a concurrent commit surfaces here as a 409, not a
    # 500.
    try:
        transaction_service.apply_requested_status(txn, cancelled_id)
        db.session.commit()
    except ValidationError as exc:
        return _error_transaction_response(txn_id, str(exc))
    except StaleDataError:
        logger.info(
            "Stale-data conflict on cancel_transaction id=%d", txn_id,
        )
        return _stale_transaction_response(txn_id)
    logger.info("user_id=%d cancelled transaction %d", current_user.id, txn_id)

    response = render_transaction_cell(txn)
    return response, 200, {"HX-Trigger": "gridRefresh"}
