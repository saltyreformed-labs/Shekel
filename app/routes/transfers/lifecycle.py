"""
Shekel Budget App -- Transfer route package: a template's LIFECYCLE.

The three doors that move a transfer template between its states -- archive,
unarchive and hard-delete -- split out of the sibling module
:mod:`app.routes.transfers.templates` at plan step R7d-g-2 (developer ruling
**R-R84**, 2026-09-13), where they had lived since the package split, when
that module stood at pylint's 1,000-line cap with this leaf's edits to every
one of them still to land.  A pure move first (graded by AST: every definition
identical, code and docstrings, nothing new), then the step.  Every URL and
endpoint name is preserved verbatim, so no ``url_for`` site or template
changed.

The three are one concern: which of the template's ``budget.transfers`` rows
still stand for it.  Archiving soft-deletes its projected rows that hold
nothing (through the transfer service), unarchiving restores them and fills
forward, and hard-delete either destroys the unsettled rows with the
template or -- where settled history stands, or a leg holds a payment (plan
step ``credit_card:CC-5-4a-4``) -- falls back to the archive
(:func:`_archive`, the one spelling both archive arms share).  What the
template IS (its CRUD routes, its form payload, its recurrence rule) stays in
``templates.py``, and what happens to a NON-repeating template's single row on
an edit is :mod:`app.routes.transfers._instances`.

**Every door here can change WHICH transfer is a loan's standing payment**
(plan step R7d-g-2, ruling **R-R85**), and the standing payment's ``starts_on``
is a stored derived value (ruling **R-R29**: the loan's first contractual
installment, the cadence anchor a month-unit rule cannot fire without).
Archiving or deleting the standing payment PROMOTES the next-oldest active
transfer into the loan; unarchiving makes the restored transfer standing
again -- or newly, when it is older than the live payment, since the seam
names the oldest active one.  So each door, once its write is flushed, calls
the ONE entry helper
(:func:`~app.routes._standing_payment.sync_loan_payment_start_or_refuse`),
which asks the seam who is standing NOW and brings that definition's start
onto the contract.  A stop the promoted or restored definition's owner
authored earlier is HONOURED (ruling **R-R82**); the one pair the window
CHECK refuses -- that start moved past that stop -- refuses the door whole,
naming the transfer.

**The version-pinned write lands at the first flush, under the guard.**  Each
door dirties the template, and the transfer service's soft-deletes and
restores flush; the standing-payment lookup autoflushes too.  Until this step
the archive and unarchive doors guarded only their commit, so a concurrent
edit surfaced as an unhandled ``StaleDataError`` whenever the template had a
row to soft-delete.  :func:`~app.routes._commit_helpers.flush_or_handle_stale`
now lands the pinned statement first, where the race can be reported.
"""

import logging

from flask import abort, flash, redirect, url_for
from flask_login import current_user

from app.extensions import db
from app.models.ref import Status
from app.models.transfer import Transfer
from app.models.transfer_template import TransferTemplate
from app.routes._commit_helpers import (
    STALE_ACTION_MESSAGE,
    StaleConflictContext,
    commit_or_handle_stale,
    flush_or_handle_stale,
)
from app.routes._recurrence_conflict_chooser import PreEditTemplateState
from app.routes._redirect_target import RedirectTarget
from app.routes._standing_payment import (
    regenerate_or_refuse,
    sync_loan_payment_start_or_refuse,
)
from app.routes.transfers._bp import transfers_bp
from app.services import definition_unarchive, transfer_service
from app.services.balance_at import BalanceContext
from app.utils import archive_helpers
from app.utils.auth_helpers import get_or_404, require_owner
from app.utils.balance_predicates import is_projected_clause
from app.utils.dates import display_today

logger = logging.getLogger(__name__)

#: Where every door here sends the user, refused or done.
_LIST = RedirectTarget("transfers.list_transfer_templates")


def _stale_context(log_label, template_id):
    """Return the stale-race context every door here reports through."""
    return StaleConflictContext(
        logger=logger,
        log_label=log_label,
        log_id=template_id,
        flash_message=STALE_ACTION_MESSAGE.format(noun="recurring transfer"),
        redirect=_LIST,
    )


def _archive(template, ctx):
    """Archive *template*: stop generation, retire its projected rows, promote the next.

    The one spelling of the archive both :func:`archive_transfer_template`
    and :func:`hard_delete_transfer_template`'s history arm run (it was
    written out twice until plan step R7d-g-2).  Three steps, in the one
    order they can run in:

    1. ``is_active = False``, FLUSHED under the stale guard: the
       version-pinned ``UPDATE`` lands here, before the service's soft-deletes
       flush and before anything reads the active set.
    2. Every projected, non-deleted transfer of the template that HOLDS
       NOTHING is soft-deleted THROUGH the transfer service, which every
       write of a transfer goes through (``CLAUDE.md``, Architecture).  Its
       two shadow transactions went with it (a three-level cascade: template
       archival -> transfer soft-delete -> shadow soft-delete) until plan
       step ``balance:X-bi-6-4d-2`` deleted the shadows.  **A transfer
       either of whose legs holds a payment is kept** (plan step
       ``credit_card:CC-5-4a-4``, rulings **R-CC63**, **R-CC65**): a
       Projected leg holds one only as a reverted settle's kept payment, which
       may still be matched to the bank's line, and a row holding a movement
       is history the archive leaves as it is
       (``archive_helpers.transfer_holds_nothing``).  Routed through the
       centralized ``is_projected_clause`` (D6-09 / MED-02) parameterised on
       ``Transfer`` so the rule "what does a Projected filter look like in
       SQL" is shared with the Transaction filter sites.
    3. The loan this template pays into, if it is one, has its standing
       payment re-derived (ruling **R-R85**): with this template out of the
       active set the next-oldest transfer into the loan is PROMOTED and its
       start becomes the contract's -- honouring a stop its owner authored
       (ruling **R-R82**), or refusing the archive whole where the derived
       start would pass that stop.

    Nothing commits here; the caller owns the commit and its own flash.

    Args:
        template: The owner-checked :class:`TransferTemplate` to archive.
        ctx: The stale-race context the caller reports through.

    Returns:
        ``(retired, kept)`` -- the number of projected transfers retired and
        the :class:`~app.utils.archive_helpers.HeldMovements` of the
        projected ones kept -- or a refusal :class:`Response` (the stale race,
        or the promotion's refusal) the caller returns verbatim, the session
        rolled back.
    """
    template.is_active = False
    stale = flush_or_handle_stale(ctx)
    if stale is not None:
        return stale

    projected = (
        Transfer.transfer_template_id == template.id,
        is_projected_clause(Transfer),
        Transfer.is_deleted.is_(False),
    )
    kept = archive_helpers.transfers_holding_movements(*projected)
    transfers_to_delete = (
        db.session.query(Transfer)
        .filter(*projected, archive_helpers.transfer_holds_nothing())
        .all()
    )
    for xfer in transfers_to_delete:
        transfer_service.delete_transfer(xfer.id, current_user.id, soft=True)

    refused = sync_loan_payment_start_or_refuse(
        template.to_account_id, redirect=_LIST,
    )
    if refused is not None:
        return refused
    return len(transfers_to_delete), kept


@transfers_bp.route("/transfers/<int:template_id>/archive", methods=["POST"])
@require_owner
def archive_transfer_template(template_id):
    """Archive a transfer template (stops future generation, keeps history).

    Soft-deletes the template's projected transfers that hold nothing,
    via the transfer service (:func:`_archive`).  Where the template was a
    loan's standing payment, the next-oldest transfer into that loan is
    promoted and its start re-derived (:func:`_archive`, ruling **R-R85**).

    Optimistic locking (commit C-18 / F-010): the template's
    ``version_id`` is enforced by SQLAlchemy on the
    ``is_active = False`` flush; a concurrent edit raises
    ``StaleDataError`` which the handler converts into a flash +
    redirect so the user retries against fresh state.
    """
    template = get_or_404(TransferTemplate, template_id)
    if template is None:
        abort(404)

    ctx = _stale_context("archive_transfer_template", template_id)
    archived = _archive(template, ctx)
    if not isinstance(archived, tuple):
        return archived
    removed, kept = archived

    conflict = commit_or_handle_stale(ctx)
    if conflict is not None:
        return conflict

    stays = kept.stays("transfer", named=True)
    flash(
        f"Recurring transfer '{template.name}' archived. "
        f"{removed} projected transfer(s) removed"
        + (f"; {stays}." if stays else "."),
        "info",
    )
    return redirect(url_for("transfers.list_transfer_templates"))


@transfers_bp.route("/transfers/<int:template_id>/unarchive", methods=["POST"])
@require_owner
def unarchive_transfer_template(template_id):
    """Unarchive a transfer template.

    Restores the transfers the archive hid, less any the books now drop
    (rulings **R-PC93**, **R-PC95** and **R-PC96**,
    :class:`~app.services.definition_unarchive.UnarchiveScope`): a transfer
    its owner deleted by hand is soft-deleted exactly as the archive hides
    one, and one the books have passed would come back inside the opening
    balance, so it stays deleted and the flash names it.  **An unarchive of a
    template that is not archived restores nothing**: its soft-deleted
    transfers are its owner's own deletions, and a stale tab's button is the
    only way to post here for one.  Then --
    BEFORE anything generates from it -- re-derives the standing payment of
    the loan it pays into (ruling **R-R85**): the restored definition is that
    payment once more, or newly if it is older than the live one, and its
    start is the contract's from this moment.  A stop its owner authored
    earlier is honoured (ruling **R-R82**); a start that would pass it
    refuses the unarchive whole.  This is where plan ledger row **REC-522**'s
    start half closes: until plan step R7d-g-2 the restored rule regenerated
    from whatever start it stored, with no sync.

    **The rows are then brought in line by the MAINTAIN pass, not a plain
    fill-forward** (developer 2026-09-13, after this leaf's adversarial
    review).  Restored rows sit on the dates the rule named when it was
    archived; where the sync has just moved the rule -- a promoted second
    transfer restored as the standing payment -- a fill-forward wrote the
    contract's dates BESIDE them, two debits a month.  The maintain pass
    (``_standing_payment.regenerate_or_refuse``, ruling **R-R19**'s) keeps
    the rows the rule still names, creates the missing ones and retires the
    rest unless they carry the owner's records, from the owner's civil day
    forward -- for a rule that did not move, exactly the fill-forward it
    replaces.

    Optimistic locking: see :func:`archive_transfer_template`.
    """
    template = get_or_404(TransferTemplate, template_id)
    if template is None:
        abort(404)
    if template.is_active:
        flash(
            f"Recurring transfer '{template.name}' is not archived, so "
            "nothing was restored.",
            "info",
        )
        return redirect(url_for("transfers.list_transfer_templates"))

    # What comes back and what stays deleted: the ONE scope the books
    # refusals count too (``definition_unarchive``), read on a PRE-WRITE pass
    # before anything is restored.  The maintain pass below builds its own.
    unarchive = definition_unarchive.unarchive_scope_on(
        template, BalanceContext.build(current_user.id),
    )
    stays_deleted = definition_unarchive.stays_deleted_notice(unarchive)

    ctx = _stale_context("unarchive_transfer_template", template_id)
    template.is_active = True
    stale = flush_or_handle_stale(ctx)
    if stale is not None:
        return stale

    transfers_to_restore = (
        db.session.query(Transfer).filter(*unarchive.restores()).all()
    )

    # Restore transfers via the service so all mutations
    # flow through the single enforcement point (design doc section 4.1).
    for xfer in transfers_to_restore:
        transfer_service.restore_transfer(xfer.id, current_user.id)

    restored_count = len(transfers_to_restore)

    # ``rows_follow=False``: the pass just below brings this definition's
    # rows along whether or not the sync moved it.
    refused = sync_loan_payment_start_or_refuse(
        template.to_account_id, redirect=_LIST, rows_follow=False,
    )
    if refused is not None:
        return refused

    if template.recurs:
        refused = regenerate_or_refuse(
            template,
            PreEditTemplateState(
                amount=template.default_amount, had_recurrence_rule=True,
            ),
            display_today(),
            redirect=_LIST,
        )
        if refused is not None:
            return refused

    conflict = commit_or_handle_stale(ctx)
    if conflict is not None:
        return conflict
    flash(
        f"Recurring transfer '{template.name}' unarchived. "
        f"{restored_count} projected transfer(s) restored."
        + (f" {stays_deleted}" if stays_deleted else ""),
        "success",
    )
    return redirect(url_for("transfers.list_transfer_templates"))


@transfers_bp.route("/transfers/<int:template_id>/hard-delete", methods=["POST"])
@require_owner
def hard_delete_transfer_template(template_id):
    """Permanently delete a transfer template if it has no payment history.

    Every transfer deletion is routed through
    ``transfer_service.delete_transfer()``, which every write of a transfer
    goes through (``CLAUDE.md``, Architecture).  Until plan step
    ``balance:X-bi-6-4d-2`` deleted the transfer's two shadow transactions,
    this docstring listed five transfer invariants the deletion kept; all but
    the one above were about the shadows.

    Two-path logic:
      - History exists (Paid transfers, or a transfer whose leg holds a
        payment -- plan step ``credit_card:CC-5-4a-4``, ruling
        **R-CC65**): permanent deletion is blocked.  Template is archived
        instead (if not already) and the user is warned, the sentence naming
        which (:func:`_archive_instead_of_delete`).
      - No history: linked transfers are hard-deleted through the
        transfer service, then the template itself is permanently
        removed (:func:`_destroy`).

    Both paths can take a loan's standing payment out of the active set, so
    both re-derive that loan's standing payment afterwards (ruling
    **R-R85**): the history arm through :func:`_archive`, the no-history arm
    once the template's ``DELETE`` has flushed.  Decomposed into the two arms
    at plan step R7d-g-2, when that second sync made a seventh return.

    Defense in depth (F-14): the bulk delete is constrained to non-
    settled transfers via the semantic ``Status.is_settled`` boolean,
    mirroring the ``templates.py::hard_delete_template`` shape added
    after CRIT-05.  Even if the guard predicate above regresses, is
    bypassed, or races a concurrent mark-done that lands between the
    guard check and the loop, settled transfers (Paid, Received) cannot
    be physically destroyed by this route.  Survivors retain their
    ``transfer_template_id``; the column's FK is ``ON DELETE SET NULL`` so they become detached
    settled history when the parent template is removed.
    """
    template = get_or_404(TransferTemplate, template_id)
    if template is None:
        abort(404)

    if archive_helpers.transfer_template_has_paid_history(template.id):
        return _archive_instead_of_delete(
            template, template_id,
            f"'{template.name}' has payment history and cannot be permanently "
            "deleted. It has been archived instead.",
        )
    # **A transfer whose leg holds a payment is history too** (plan step
    # ``credit_card:CC-5-4a-4``, rulings **R-CC65**, **R-CC66**): a reverted
    # settle KEEPS its payment on each leg (ruling **R-BAL61**), perhaps
    # still matched to the bank's line, and :func:`_destroy` took it off the
    # books through the transfer service's removal act -- withdrawing the
    # match and freeing the line -- as a side effect of deleting the
    # template.
    held = archive_helpers.transfer_template_holding_movements(template.id)
    if held:
        stays = held.stays("transfer", named=False)
        return _archive_instead_of_delete(
            template, template_id,
            f"'{template.name}' holds {held.noun} and cannot be permanently "
            "deleted. It has been archived instead"
            + (f"; {stays}." if stays else "."),
        )
    return _destroy(template, template_id)


def _archive_instead_of_delete(template, template_id, refusal):
    """The history arm of :func:`hard_delete_transfer_template`: archive, warn.

    A template with settled history, or a transfer holding a payment, is
    never destroyed; it is archived in place of the deletion the owner asked
    for -- if it was still active -- and the flash says why, once the
    archive has committed.  The archive is :func:`_archive`'s, promotion
    included, so a transfer holding a payment stays live.

    Args:
        template: The owner-checked :class:`TransferTemplate`.
        template_id: Its id, for the stale-race log.
        refusal: The sentence naming the reason, flashed on success.

    Returns:
        The redirect to the template list, or a refusal :class:`Response`.
    """
    if template.is_active:
        ctx = _stale_context(
            "hard_delete_transfer_template archive-fallback", template_id,
        )
        archived = _archive(template, ctx)
        if not isinstance(archived, tuple):
            return archived
        conflict = commit_or_handle_stale(ctx)
        if conflict is not None:
            return conflict
    # Flashed AFTER the archive, and only once it committed: a refusal above
    # (the promotion's, or the stale race) sends its own sentence, and this
    # one beside it would report an archive that did not happen.
    flash(refusal, "warning")
    return redirect(url_for("transfers.list_transfer_templates"))


def _destroy(template, template_id):
    """The no-history arm of :func:`hard_delete_transfer_template`: delete it.

    Permanently deletes the linked transfers through the transfer service,
    which every write of a transfer goes through (``CLAUDE.md``,
    Architecture); the delete CASCADE-deleted their shadow transactions too
    until plan step ``balance:X-bi-6-4d-2`` deleted the shadows.  None of them
    holds a payment: the caller archived on that (plan step
    ``credit_card:CC-5-4a-4``), so the service's removal act takes nothing.
    ``transfer_service.delete_transfer`` flushes but does not commit, so all
    deletions are atomic within a single DB transaction.

    Defense in depth (F-14 / commit C-21 mirror): the bulk delete is
    additionally constrained to ``Status.is_settled = False`` rows via the
    semantic ``Status.is_settled`` boolean -- the same shape
    ``templates.py::hard_delete_template`` applies after CRIT-05.  Even if
    ``transfer_template_has_paid_history`` regresses, is bypassed, or races
    a concurrent mark-done that lands between the guard check and the loop
    below, settled transfers (Paid, Received) cannot be physically
    destroyed by this route.  Survivors retain their
    ``transfer_template_id``; the column's FK is ``ON DELETE SET NULL`` (see
    ``app/models/transfer.py``) so they become detached settled history when
    the parent template is removed below.

    Args:
        template: The owner-checked :class:`TransferTemplate`, with no
            settled history.
        template_id: Its id, for the stale-race log.

    Returns:
        The redirect to the template list, or a refusal :class:`Response`.
    """
    template_name = template.name
    # Read before the DELETE flushes: the promotion below is asked of the
    # loan this template paid into, once it is out of the active set.
    to_account_id = template.to_account_id
    settled_status_ids = db.session.query(Status.id).filter(
        Status.is_settled.is_(True)
    ).scalar_subquery()
    deletable_transfers = (
        db.session.query(Transfer)
        .filter(
            Transfer.transfer_template_id == template.id,
            Transfer.status_id.notin_(settled_status_ids),
        )
        .all()
    )
    for xfer in deletable_transfers:
        transfer_service.delete_transfer(xfer.id, current_user.id, soft=False)

    ctx = _stale_context("hard_delete_transfer_template", template_id)
    db.session.delete(template)
    # The version-pinned DELETE lands here, under the guard, so the
    # promotion below reads an active set this template has left.
    stale = flush_or_handle_stale(ctx)
    if stale is not None:
        return stale
    refused = sync_loan_payment_start_or_refuse(to_account_id, redirect=_LIST)
    if refused is not None:
        return refused
    conflict = commit_or_handle_stale(ctx)
    if conflict is not None:
        return conflict

    flash(f"Recurring transfer '{template_name}' permanently deleted.", "info")
    return redirect(url_for("transfers.list_transfer_templates"))
