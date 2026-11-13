"""
Shekel Budget App -- A Recurring Transfer's ARCHIVE and UNARCHIVE, One Act Each

The acts that take a transfer definition out of the owner's active set, bring
it back, or delete it for good, each ONE function every door performing it
calls (task plan#3697, plan step ``credit_card:CC-6``):

* :func:`archive_transfer_definition` -- the transfer archive door, the
  transfer hard-delete's history arm, and an account's archive, which takes a
  card's one payment with it.
* :func:`unarchive_transfer_definition` -- the transfer unarchive door, and an
  account's unarchive, which brings the card's payment back.
* :func:`destroy_transfer_definition` -- the transfer hard-delete's
  no-history arm, moved here beside the other two because the three share
  their ending: the loan's standing payment re-derived, then the commit.
  Its writes are :func:`remove_transfer_definition`, which an account's
  delete calls for a card's one payment (task plan#3754).

They lived in :mod:`app.routes.transfers.lifecycle` (the archive as its
private ``_archive`` since plan step R7d-g-2, the unarchive written out in
its door) until a card's own doors needed them: an account door that copied
either body would inherit its writes and none of its refusals.  Route-layer
module (leading underscore = route-internal) rather than a service, because
both acts answer a refusal as a flash and a redirect, as
:mod:`app.routes._standing_payment`, which they call, does; ``CLAUDE.md``
(Architecture) keeps services isolated from Flask.  Nothing here writes a
transfer row directly: every soft-delete and restore goes through
``transfer_service``.

**Each act takes the door's stale-race context and refuses through it**, its
redirect included, so a refusal returns the owner to the page they pressed.
**Each act ends with the door's commit**, under that same guard: every door
performing one commits straight after it, the door's own writes made before
the call riding in the same transaction, so the commit is part of the act
rather than six copies of it.  **The one door that writes AFTER an act is an
account's delete**, which deletes the card the payment's template keys onto:
the template's ``DELETE`` must flush before the card's, so that door calls
:func:`remove_transfer_definition`, the delete's writes, and commits once
its own are made.  The door owns its flash, made once the act has committed.
"""

from flask import Response, flash
from flask_login import current_user

from app.extensions import db
from app.models.account import Account
from app.models.ref import Status
from app.models.transfer import Transfer
from app.models.transfer_template import TransferTemplate
from app.routes._commit_helpers import (
    StaleConflictContext,
    commit_or_handle_stale,
    flush_or_handle_stale,
)
from app.routes._recurrence_conflict_chooser import PreEditTemplateState
from app.routes._standing_payment import (
    regenerate_or_refuse,
    sync_loan_payment_start_or_refuse,
)
from app.services import (
    definition_unarchive,
    template_amount_service,
    transfer_service,
)
from app.services.balance_at import BalanceContext
from app.utils import archive_helpers
from app.utils.balance_predicates import is_projected_clause
from app.utils.dates import display_today

#: The refusal of an unarchive while an account the transfer moves money
#: between is archived (task plan#3697).  Formatted with the transfer's name,
#: its two accounts, the archived one(s), and the verb that agrees with them.
#: Until then the transfer door answered a 500 when a row was to come back
#: (``transfer_service.restore_transfer`` refuses one onto an archived
#: account, finding F-164, and nothing caught it) and, when none was, wrote
#: fresh rows onto the archived account, the state an account's own archive
#: refuses to leave behind.
UNARCHIVE_NEEDS_ITS_ACCOUNTS = (
    "Nothing was unarchived: '{transfer}' moves money from '{source}' to "
    "'{destination}', and {archived} {verb} archived. Unarchive {archived} "
    "first."
)


def archive_transfer_definition(
    template: TransferTemplate, ctx: StaleConflictContext,
) -> tuple[int, archive_helpers.HeldMovements] | Response:
    """Archive *template*: stop generation, retire its projected rows, promote the next.

    The one spelling of a transfer definition's archive (plan step R7d-g-2
    first made it one for the transfer doors' two arms; task plan#3697 for an
    account's archive too).  Three steps, in the one order they can run in:

    1. ``is_active = False``, FLUSHED under the stale guard: the
       version-pinned ``UPDATE`` lands here, with any write the door made
       before calling (an account's own ``is_active``), before the service's
       soft-deletes flush and before anything reads the active set.
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
       start would pass that stop.  For any other destination, a card
       included, the producer reads nothing and writes nothing.

    Then the commit, under the same guard.

    Args:
        template: The owner-checked :class:`TransferTemplate` to archive,
            active.
        ctx: The door's stale-race context; a refusal redirects to its
            ``redirect``.

    Returns:
        ``(retired, kept)`` -- the number of projected transfers retired and
        the :class:`~app.utils.archive_helpers.HeldMovements` of the
        projected ones kept, committed -- or a refusal :class:`Response` (the
        stale race, or the promotion's refusal) the caller returns verbatim,
        the session rolled back.
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
        template.to_account_id, redirect=ctx.redirect,
    )
    if refused is not None:
        return refused
    conflict = commit_or_handle_stale(ctx)
    if conflict is not None:
        return conflict
    return len(transfers_to_delete), kept


def _archived_endpoint_refusal(
    template: TransferTemplate, ctx: StaleConflictContext,
) -> Response | None:
    """Refuse an unarchive while either account *template* names is archived.

    Asked before anything is written, as the account's own archive asks
    before it leaves an active transfer on an archived account.  The two
    accounts are read through the session, so an account its door has just
    set active (an account's unarchive bringing its card's payment back)
    reads active.

    Args:
        template: The archived :class:`TransferTemplate` to restore.
        ctx: The door's stale-race context, for its redirect.

    Returns:
        ``None`` when both accounts are active; otherwise the refusal
        redirect, the session rolled back so the door's pending write goes
        too, with :data:`UNARCHIVE_NEEDS_ITS_ACCOUNTS` flashed.
    """
    source = db.session.get(Account, template.from_account_id)
    destination = db.session.get(Account, template.to_account_id)
    archived = [
        account.name for account in (source, destination)
        if not account.is_active
    ]
    if not archived:
        return None
    sentence = UNARCHIVE_NEEDS_ITS_ACCOUNTS.format(
        transfer=template.name,
        source=source.name,
        destination=destination.name,
        archived=" and ".join(f"'{name}'" for name in archived),
        verb="is" if len(archived) == 1 else "are",
    )
    db.session.rollback()
    flash(sentence, "warning")
    return ctx.redirect.to_response()


def unarchive_transfer_definition(
    template: TransferTemplate, ctx: StaleConflictContext,
) -> tuple[int, str | None] | Response:
    """Unarchive *template*: restore the rows the archive hid, then maintain its rows.

    The one spelling of a transfer definition's unarchive (task plan#3697:
    the transfer door's body until then, now the account unarchive's too).

    **Refused first while either account is archived**
    (:func:`_archived_endpoint_refusal`).  Then it restores the transfers the
    archive hid, less any the books now drop (rulings **R-PC93**,
    **R-PC95** and **R-PC96**,
    :class:`~app.services.definition_unarchive.UnarchiveScope`): a transfer
    its owner deleted by hand is soft-deleted exactly as the archive hides
    one, and one the books have passed would come back inside the opening
    balance, so it stays deleted and the returned notice names it.  Then --
    BEFORE anything generates from it -- it re-derives the standing payment
    of the loan it pays into (ruling **R-R85**): the restored definition is
    that payment once more, or newly if it is older than the live one, and
    its start is the contract's from this moment.  A stop its owner authored
    earlier is honoured (ruling **R-R82**); a start that would pass it
    refuses the unarchive whole.  This is where plan ledger row **REC-522**'s
    start half closes: until plan step R7d-g-2 the restored rule regenerated
    from whatever start it stored, with no sync.

    **The rows are then brought in line by the MAINTAIN pass, not a plain
    fill-forward** (developer 2026-09-13, after plan step R7d-g-2's
    adversarial review).  Restored rows sit on the dates the rule named when
    it was archived; where the sync has just moved the rule -- a promoted
    second transfer restored as the standing payment -- a fill-forward wrote
    the contract's dates BESIDE them, two debits a month.  The maintain pass
    (``_standing_payment.regenerate_or_refuse``, ruling **R-R19**'s) keeps
    the rows the rule still names, creates the missing ones and retires the
    rest unless they carry the owner's records, from the owner's civil day
    forward -- for a rule that did not move, exactly the fill-forward it
    replaces.  Then the commit, under the same guard.

    Args:
        template: The owner-checked :class:`TransferTemplate` to unarchive,
            archived (the caller has asked; an active one's soft-deleted
            transfers are its owner's own deletions).
        ctx: The door's stale-race context; a refusal redirects to its
            ``redirect``.

    Returns:
        ``(restored, stays_deleted)`` -- the number of transfers restored and
        :func:`~app.services.definition_unarchive.stays_deleted_notice`'s
        sentence or ``None``, committed -- or a refusal :class:`Response` the
        caller returns verbatim, the session rolled back.
    """
    refused = _archived_endpoint_refusal(template, ctx)
    if refused is not None:
        return refused

    # What comes back and what stays deleted: the ONE scope the books
    # refusals count too (``definition_unarchive``), read on a PRE-WRITE pass
    # before anything is restored.  The maintain pass below builds its own.
    unarchive = definition_unarchive.unarchive_scope_on(
        template, BalanceContext.build(current_user.id),
    )
    stays_deleted = definition_unarchive.stays_deleted_notice(unarchive)

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

    # ``rows_follow=False``: the pass just below brings this definition's
    # rows along whether or not the sync moved it.
    refused = sync_loan_payment_start_or_refuse(
        template.to_account_id, redirect=ctx.redirect, rows_follow=False,
    )
    if refused is not None:
        return refused

    if template.recurs:
        refused = regenerate_or_refuse(
            template,
            PreEditTemplateState(
                series=template_amount_service.series_image(template),
                had_recurrence_rule=True,
                # A transfer names two accounts; no account question is asked.
                account_id=None,
            ),
            display_today(),
            redirect=ctx.redirect,
        )
        if refused is not None:
            return refused
    conflict = commit_or_handle_stale(ctx)
    if conflict is not None:
        return conflict
    return len(transfers_to_restore), stays_deleted


def remove_transfer_definition(
    template: TransferTemplate, ctx: StaleConflictContext,
) -> Response | None:
    """Permanently delete *template* and its unsettled transfers, flushed; no commit.

    The writes of :func:`destroy_transfer_definition`, which the transfer
    hard-delete's no-history arm performs and then commits, and which an
    account's delete performs for a card's one payment BEFORE its own writes
    (task plan#3754): the card's ``DELETE`` must flush after the template's,
    whose ``to_account_id`` keys onto it (``RESTRICT``), so the account door
    commits once its own writes are made.

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
    destroyed by this act.  Survivors retain their
    ``transfer_template_id``; the column's FK is ``ON DELETE SET NULL`` (see
    ``app/models/transfer.py``) so they become detached settled history when
    the parent template is removed below.

    Args:
        template: The owner-checked :class:`TransferTemplate`, with no
            settled history and no transfer holding a payment.
        ctx: The door's stale-race context; a refusal redirects to its
            ``redirect``.

    Returns:
        ``None`` once flushed, or a refusal :class:`Response` (the stale
        race, or the promotion's refusal) the caller returns verbatim, the
        session rolled back.
    """
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

    db.session.delete(template)
    # The version-pinned DELETE lands here, under the guard, so the
    # promotion below reads an active set this template has left.
    stale = flush_or_handle_stale(ctx)
    if stale is not None:
        return stale
    return sync_loan_payment_start_or_refuse(
        to_account_id, redirect=ctx.redirect,
    )


def destroy_transfer_definition(
    template: TransferTemplate, ctx: StaleConflictContext,
) -> Response | None:
    """Permanently delete *template* and its unsettled transfers, then commit.

    The transfer hard-delete's no-history arm, moved here from
    ``routes/transfers/lifecycle._destroy`` beside the archive and the
    unarchive (task plan#3697): the three acts of a transfer definition's
    lifecycle, each ending with the loan's standing payment re-derived and
    the door's commit.  The writes are :func:`remove_transfer_definition`'s.

    Args:
        template: The owner-checked :class:`TransferTemplate`, with no
            settled history and no transfer holding a payment.
        ctx: The door's stale-race context; a refusal redirects to its
            ``redirect``.

    Returns:
        ``None`` once committed, or a refusal :class:`Response` (the stale
        race, or the promotion's refusal) the caller returns verbatim, the
        session rolled back.
    """
    refused = remove_transfer_definition(template, ctx)
    return refused if refused is not None else commit_or_handle_stale(ctx)


__all__ = [
    "UNARCHIVE_NEEDS_ITS_ACCOUNTS",
    "archive_transfer_definition",
    "destroy_transfer_definition",
    "remove_transfer_definition",
    "unarchive_transfer_definition",
]
