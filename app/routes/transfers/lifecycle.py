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
step ``credit_card:CC-5-4a-4``) -- falls back to the archive.  The archive
and the unarchive are each ONE act, in :mod:`app.routes._transfer_lifecycle`
since task plan#3697, because an account's archive and unarchive perform them
too, for a card's one payment; the archive's two arms here have shared it
since plan step R7d-g-2.  What the
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

from app.models.transfer_template import TransferTemplate
from app.routes._commit_helpers import (
    STALE_ACTION_MESSAGE,
    StaleConflictContext,
)
from app.routes._redirect_target import RedirectTarget
from app.routes._transfer_lifecycle import (
    archive_transfer_definition,
    destroy_transfer_definition,
    unarchive_transfer_definition,
)
from app.routes.transfers._bp import transfers_bp
from app.utils import archive_helpers
from app.utils.auth_helpers import get_or_404, require_owner

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


@transfers_bp.route("/transfers/<int:template_id>/archive", methods=["POST"])
@require_owner
def archive_transfer_template(template_id):
    """Archive a transfer template (stops future generation, keeps history).

    Soft-deletes the template's projected transfers that hold nothing,
    via the transfer service
    (:func:`~app.routes._transfer_lifecycle.archive_transfer_definition`).  Where the template
    was a loan's standing payment, the next-oldest transfer into that loan is
    promoted and its start re-derived (the same act, ruling **R-R85**).

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
    archived = archive_transfer_definition(template, ctx)
    if not isinstance(archived, tuple):
        return archived
    removed, kept = archived

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

    The act is :func:`~app.routes._transfer_lifecycle.unarchive_transfer_definition`,
    the one an account's unarchive calls for its card's payment too (task
    plan#3697): refused while either account is archived, then the transfers
    the archive hid restored, less any the books now drop (the flash names
    them), the loan's standing payment re-derived, and the rows maintained.
    **An unarchive of a template that is not archived restores nothing**: its
    soft-deleted transfers are its owner's own deletions, and a stale tab's
    button is the only way to post here for one.

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

    ctx = _stale_context("unarchive_transfer_template", template_id)
    unarchived = unarchive_transfer_definition(template, ctx)
    if not isinstance(unarchived, tuple):
        return unarchived
    restored_count, stays_deleted = unarchived
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
    **R-R85**): the history arm through the archive act, the no-history arm
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
    archive has committed.  The archive is
    :func:`~app.routes._transfer_lifecycle.archive_transfer_definition`, promotion
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
        archived = archive_transfer_definition(template, ctx)
        if not isinstance(archived, tuple):
            return archived
    # Flashed AFTER the archive, and only once it committed: a refusal above
    # (the promotion's, or the stale race) sends its own sentence, and this
    # one beside it would report an archive that did not happen.
    flash(refusal, "warning")
    return redirect(url_for("transfers.list_transfer_templates"))


def _destroy(template, template_id):
    """The no-history arm of :func:`hard_delete_transfer_template`: delete it.

    The act is
    :func:`~app.routes._transfer_lifecycle.destroy_transfer_definition`: its
    unsettled transfers deleted through the transfer service, the template
    deleted, the loan's standing payment re-derived, committed.  This arm
    flashes once it has.

    Args:
        template: The owner-checked :class:`TransferTemplate`, with no
            settled history.
        template_id: Its id, for the stale-race log.

    Returns:
        The redirect to the template list, or a refusal :class:`Response`.
    """
    template_name = template.name
    refused = destroy_transfer_definition(
        template, _stale_context("hard_delete_transfer_template", template_id),
    )
    if refused is not None:
        return refused
    flash(f"Recurring transfer '{template_name}' permanently deleted.", "info")
    return redirect(url_for("transfers.list_transfer_templates"))
