"""
Shekel Budget App -- Account route package: an account's LIFECYCLE.

The three doors that move an account between its states -- archive, unarchive
and hard-delete -- split out of :mod:`app.routes.accounts.crud` (task
plan#3697, plan step ``credit_card:CC-6``) when that module stood 21 lines
under pylint's 1,000-line cap with the task's edits to two of them still to
land.  A pure move first, graded by AST (every definition identical, code and
docstrings, nothing new), then the task; the transfer routes made the same
split for the same reason at plan step R7d-g-2 (ruling **R-R84**,
:mod:`app.routes.transfers.lifecycle`).  Every URL and endpoint name is
preserved verbatim (the endpoint is the function's name on ``accounts_bp``),
so no ``url_for`` site or template changed.

What an account IS -- creating it, its edit form, its name and type -- stays
in ``crud.py``; what happens to it when it leaves the owner's active set, or
leaves the books for good, is here.

The optimistic-lock contract (commit C-17 / F-009) these doors share with
``crud.py``: the SQLAlchemy ``version_id_col`` ``WHERE version_id = ?`` at
flush time, converted from ``StaleDataError`` into a flash + redirect so the
owner retries against fresh row state.
"""

import logging
from dataclasses import dataclass

from flask import Response, abort, flash, redirect, url_for
from flask_login import current_user

from app.extensions import db
from app.models.account import Account
from app.models.asset_appreciation_params import AssetAppreciationParams
from app.models.escrow_line import EscrowLine
from app.models.interest_params import InterestParams
from app.models.investment_params import InvestmentParams
from app.models.loan_features import RateHistory
from app.models.loan_params import LoanParams
from app.models.savings_goal import SavingsGoal
from app.models.transaction import Transaction
from app.models.transaction_template import TransactionTemplate
from app.models.transfer import Transfer
from app.models.transfer_template import TransferTemplate
from app.routes._commit_helpers import (
    StaleConflictContext,
    commit_or_handle_stale,
    flush_or_handle_stale,
)
from app.routes._redirect_target import RedirectTarget
from app.routes._transfer_lifecycle import (
    archive_transfer_definition,
    remove_transfer_definition,
    unarchive_transfer_definition,
)
from app.routes.accounts._bp import accounts_bp
from app.services import (
    card_payoff,
    definition_delete,
    ledger_account_service,
    transfer_service,
)
from app.services.cash_ledger import is_card_payment_definition
from app.utils import archive_helpers
from app.utils.auth_helpers import fresh_login_required, get_or_404, require_owner

logger = logging.getLogger(__name__)


def _stale_context(log_label: str, account_id: int) -> StaleConflictContext:
    """Return the stale-race context every door here reports through.

    Args:
        log_label: The door's label for the conflict's log line.
        account_id: The account's id, for the log line.

    Returns:
        The :class:`StaleConflictContext`, redirecting to the accounts
        cockpit, where each of these doors is pressed.
    """
    return StaleConflictContext(
        logger=logger,
        log_label=log_label,
        log_id=account_id,
        flash_message=(
            "This account was changed by another action.  Please reload "
            "the page and try again."
        ),
        redirect=RedirectTarget("savings.dashboard"),
    )


def _own_payment(account: Account) -> TransferTemplate | None:
    """Return the card's one payment, archived or not, or ``None`` for any other account.

    Every card has one payment, made by the door that brings the card into
    being (task plan#2933), and its settings row names the card
    (:func:`app.services.card_payoff.card_payment_on`); only a card's can.

    Args:
        account: The owner-checked account.

    Returns:
        The payment's :class:`TransferTemplate`, or ``None``.
    """
    settings = card_payoff.card_payment_on(account.id)
    return None if settings is None else settings.template


def _other_transfer_definitions(account_id: int, payment: TransferTemplate | None):
    """Return the query of the transfer definitions naming the account, but its own payment.

    The refusal both the archive and the delete ask, from either side of the
    transfer: a card's one payment is not counted (tasks plan#3697 and
    plan#3754), because every card has one and it goes with the card.

    Args:
        account_id: The owner-checked account's id.
        payment: The card's one payment (:func:`_own_payment`), or ``None``.

    Returns:
        An unexecuted query of the owner's :class:`TransferTemplate` rows,
        active or archived, naming the account but *payment*.
    """
    query = db.session.query(TransferTemplate).filter(
        TransferTemplate.user_id == current_user.id,
        db.or_(
            TransferTemplate.from_account_id == account_id,
            TransferTemplate.to_account_id == account_id,
        ),
    )
    if payment is not None:
        query = query.filter(TransferTemplate.id != payment.id)
    return query


@dataclass(frozen=True)
class _NamedByTransfers:
    """The refusal of an archive or a delete while transfer definitions name the account.

    Its sentence names what to do about each kind of definition (task
    plan#3757).  Another card's worked-out payment that this account pays
    is archived and deleted only with its card, so the owner cannot clear it
    by hand as the sentence asks of the others: it is named, with the two
    ways out, changing the account it pays from or archiving (deleting)
    the card first.  A refusal naming no card's payment reads as it did
    before that task.

    Attributes:
        lead: The sentence that opens the refusal.
        others: What to do about the definitions that are no card's payment.
        card_payment: What to do about a card's payment, formatted with the
            payment's name and its card's.
    """

    lead: str
    others: str
    card_payment: str

    def sentence(self, blockers: list[TransferTemplate]) -> str:
        """Return the refusal for *blockers*, the definitions naming the account.

        Args:
            blockers: The owner's transfer definitions the refusal counted,
                at least one (:func:`_other_transfer_definitions`).

        Returns:
            The lead, then :attr:`others` when any blocker is no card's
            payment, then :attr:`card_payment` for each one that is.
        """
        payments = [t for t in blockers if is_card_payment_definition(t)]
        parts = [self.lead]
        if len(payments) < len(blockers):
            parts.append(self.others)
        parts.extend(
            self.card_payment.format(payment=t.name, card=t.to_account.name)
            for t in payments
        )
        return " ".join(parts)


#: The archive's refusal while an ACTIVE recurring transfer names the account.
_ARCHIVE_REFUSAL = _NamedByTransfers(
    lead=(
        "Cannot archive this account -- it is used by active recurring "
        "transfers."
    ),
    others="Archive those recurring transfers first.",
    card_payment=(
        "'{payment}' is archived only with '{card}': edit it to pay from "
        "another account, or archive '{card}' first."
    ),
)

#: The delete's refusal while any recurring transfer, archived ones included,
#: names the account (its key is ``RESTRICT``).
_DELETE_REFUSAL = _NamedByTransfers(
    lead="Cannot delete this account -- it is used by recurring transfers.",
    others="Delete those recurring transfers first.",
    card_payment=(
        "'{payment}' is deleted only with '{card}': edit it to pay from "
        "another account, or delete '{card}' first."
    ),
)


def _archive_with_its_payment(
    account: Account, payment: TransferTemplate | None, ctx: StaleConflictContext,
) -> str | Response:
    """Archive *account*, and a card's active payment with it, then commit.

    The one archive of an account, which its Archive performs and its
    delete's history arm performs in place of the delete
    (:func:`_archive_instead_of_delete`, task plan#3754).  The card's payment
    is archived through the transfers' own archive act
    (:func:`~app.routes._transfer_lifecycle.archive_transfer_definition`),
    which flushes the account's write with the payment's under the stale
    guard and commits both; a payment already archived is left as it is.

    Args:
        account: The owner-checked account to archive.
        payment: The card's one payment (:func:`_own_payment`), or ``None``.
        ctx: The door's stale-race context.

    Returns:
        The clause naming the payment archived with it ("" when none was),
        committed, or the refusal :class:`Response` the door returns
        verbatim, the session rolled back.
    """
    account.is_active = False
    if payment is None or not payment.is_active:
        conflict = commit_or_handle_stale(ctx)
        return "" if conflict is None else conflict
    archived = archive_transfer_definition(payment, ctx)
    if not isinstance(archived, tuple):
        return archived
    removed, kept = archived
    stays = kept.stays("row", named=True)
    return (
        f", and its payment '{payment.name}' with it: {removed} projected "
        "payment(s) removed" + (f"; {stays}" if stays else "")
    )


@accounts_bp.route("/accounts/<int:account_id>/archive", methods=["POST"])
@require_owner
def archive_account(account_id):
    """Archive an account (soft delete), and a card's one payment with it.

    **Refused while an active recurring transfer names the account**, from
    either side: archiving it would leave the transfer writing rows onto an
    account withdrawn from every projection.  **A card's own payment is not
    one of them** (task plan#3697): every card has one (task plan#2933), so
    the refusal would hold every card, and the payment is archived WITH the
    card instead, through the transfers' own archive act
    (:func:`~app.routes._transfer_lifecycle.archive_transfer_definition`), so
    its projected rows that hold nothing are retired as the Recurring page's
    Archive retires them, and a row holding a recorded payment stays.  Any
    other recurring transfer naming the card still refuses.  A payment
    already archived is left as it is.  **The account a card's payment comes
    from is refused with the payment named** (task plan#3757,
    :data:`_ARCHIVE_REFUSAL`): that payment is archived only with its card,
    so the refusal sends the owner to change the account it pays from, or
    to archive the card first.

    The Account model carries a ``version_id_col`` (commit C-17), so a
    concurrent mutation interleaving with this archive raises
    ``StaleDataError`` at flush time; the act flushes the account's and the
    payment's writes together under the stale guard, and the commit is
    guarded too, each converting the race into a flash + redirect so the
    user can retry against the fresh row state instead of seeing a 500.
    """
    account = get_or_404(Account, account_id)
    if account is None:
        abort(404)

    payment = _own_payment(account)
    blockers = _other_transfer_definitions(account_id, payment).filter(
        TransferTemplate.is_active.is_(True),
    ).all()
    if blockers:
        flash(_ARCHIVE_REFUSAL.sentence(blockers), "warning")
        return redirect(url_for("savings.dashboard"))

    with_payment = _archive_with_its_payment(
        account, payment, _stale_context("archive_account", account_id),
    )
    if not isinstance(with_payment, str):
        return with_payment
    logger.info("Archived account: %s (id=%d)", account.name, account.id)
    flash(f"Account '{account.name}' archived{with_payment}.", "info")
    return redirect(url_for("savings.dashboard"))


@accounts_bp.route("/accounts/<int:account_id>/unarchive", methods=["POST"])
@require_owner
def unarchive_account(account_id):
    """Unarchive an account, and a card's one payment with it.

    **A card's payment comes back with the card** (task plan#3697): every
    card has one payment (task plan#2933), so an active card has an active
    one.  It is restored through the transfers' own unarchive act
    (:func:`~app.routes._transfer_lifecycle.unarchive_transfer_definition`),
    so the rows its archive hid come back, less any the books now drop (the
    flash names them), and its rows are maintained from today.  That act
    refuses while the account the payment comes out of is archived, and then
    the card stays archived too, the flash naming the account to unarchive
    first.  Only an unarchive of an ARCHIVED account touches the payment: a
    stale tab's press on an active one changes nothing.

    The account's write is flushed under the stale guard BEFORE the act,
    whose pre-write read pass would otherwise autoflush it outside any guard.
    See :func:`archive_account` for the optimistic-lock contract.
    """
    account = get_or_404(Account, account_id)
    if account is None:
        abort(404)

    payment = _own_payment(account) if not account.is_active else None
    ctx = _stale_context("unarchive_account", account_id)
    account.is_active = True
    with_payment = "."
    if payment is None or payment.is_active:
        conflict = commit_or_handle_stale(ctx)
        if conflict is not None:
            return conflict
    else:
        stale = flush_or_handle_stale(ctx)
        if stale is not None:
            return stale
        # The act commits the account's write with the payment's.
        unarchived = unarchive_transfer_definition(payment, ctx)
        if not isinstance(unarchived, tuple):
            return unarchived
        restored, stays_deleted = unarchived
        with_payment = (
            f", and its payment '{payment.name}' with it: {restored} "
            "projected payment(s) restored."
            + (f" {stays_deleted}" if stays_deleted else "")
        )
    logger.info("Unarchived account: %s (id=%d)", account.name, account.id)
    flash(f"Account '{account.name}' unarchived{with_payment}", "success")
    return redirect(url_for("savings.dashboard"))


def _archive_instead_of_delete(account, account_id, payment, reason):
    """Archive an account with history worth preserving, instead of deleting.

    The shared outcome of the hard-delete history guard's two arms
    (:func:`_history_refusal`): an active account is archived by the
    account's one archive (:func:`_archive_with_its_payment`), so **a card's
    payment is archived with it** (task plan#3754), as the card's own Archive
    does; an account already archived is left as it is.  *reason* is
    flashed once the archive has committed, with the payment's clause: a
    refusal (the stale race) sends its own sentence, and this one beside it
    would report an archive that did not happen.  Returns the Flask response
    the caller returns directly.

    Args:
        account: The owned Account being archived.
        account_id: Its id (for the stale-conflict log label and redirect).
        payment: The card's one payment (:func:`_own_payment`), or ``None``.
        reason: The user-facing sentence saying why it is not deleted.

    Returns:
        A Flask redirect response, or the stale-conflict response when a
        concurrent update bumped the row's version.
    """
    with_payment = ""
    if account.is_active:
        with_payment = _archive_with_its_payment(
            account, payment,
            _stale_context("hard_delete_account archive-fallback", account_id),
        )
        if not isinstance(with_payment, str):
            return with_payment
    flash(f"{reason} It has been archived instead{with_payment}.", "warning")
    return redirect(url_for("savings.dashboard"))


def _history_refusal(account, definitions) -> "str | None":
    """Return why *account*'s history forbids a permanent delete, or ``None``.

    Guard 4 of :func:`hard_delete_account`, its two arms asked in order and
    each answering with the sentence the archive flash opens with:

    * **rows** -- any non-deleted transaction on the account or under one of
      its definitions, and any SETTLED row under one of its definitions
      whatever its ``is_deleted`` (the definition door's own refusal, ruling
      **R-JE**; an adversarial review of plan step ``balance:X-bi-7a``
      reproduced the disposal nulling such a row's link);
    * **movements on it** (plan step ``credit_card:CC-5-2``) -- money that
      moved through this account from rows that are NOT its own: a card
      holding swipes recorded in checking envelopes (ruling **R-CC15**).
      The arm above does not see them (the rows are checking's) and the
      door's cleanup disposes of none, so ``fk_transaction_entries_account_id``'s
      RESTRICT (ruling **R-CC32**) met the DELETE as a 500.  **A stated
      behaviour change**: such an account ARCHIVES, as every other kind of
      history does (``archive_helpers.account_holds_other_rows_movements``).
      *A card whose only movements sat under its own ghost rows still
      deleted, with them, until plan step ``credit_card:CC-5-4a-4``, whose
      arm here archived on a payment or purchase under a HIDDEN row or
      transfer the cleanup would remove.  That arm went at plan step
      ``balance:X-bi-6-4d-2``, because the database refuses a hidden row or
      transfer holding one (rulings **R-CC89**, **R-CC92**;
      :mod:`app.deleted_row_infrastructure`): a row the cleanup removes holds
      no movement.*

    Args:
        account: The owned :class:`~app.models.account.Account`.
        definitions: Its ``TransactionTemplate`` rows, already loaded by the
            door for guard 3.

    Returns:
        The user-facing reason, or ``None`` when neither arm finds history.
    """
    if archive_helpers.account_has_history(account.id) or any(
        archive_helpers.template_has_paid_history(definition.id)
        for definition in definitions
    ):
        return (
            f"'{account.name}' has transaction history and cannot be "
            "permanently deleted."
        )
    if archive_helpers.account_holds_other_rows_movements(account.id):
        return (
            f"'{account.name}' has purchases recorded on it from other "
            "accounts' rows and cannot be permanently deleted."
        )
    return None


@accounts_bp.route("/accounts/<int:account_id>/hard-delete", methods=["POST"])
@require_owner
@fresh_login_required()
def hard_delete_account(account_id):
    """Permanently delete an account if it has no blocking dependents.

    Guard chain (checked in order):
      1. Ownership -- account exists and belongs to current user.
      2. Transfer template guard -- any TransferTemplate (active or
         archived) referencing this account blocks deletion because the
         FK is ON DELETE RESTRICT.  **A card's own payment is not one of
         them** (task plan#3754): every card has one (task plan#2933), so
         the refusal held every card, a card added by mistake included; the
         payment goes with the card instead (step 0), or is archived with
         it where guard 4 archives.  Any other recurring transfer naming the
         card still refuses.  The account a card's payment comes from is
         refused with the payment named (task plan#3757,
         :data:`_DELETE_REFUSAL`): that payment is deleted only with its
         card.
      3. Recurring-definition guard -- any TransactionTemplate (active or
         archived) on this account that RECURS blocks deletion for the same
         FK reason.  A rule-less definition is a ONE-OFF's (plan step
         ``balance:X-bi-7a``, ruling **R-BAL23**: the refusal counts
         definitions with a rule) and is disposed of below, because the
         Recurring list is where the owner would go to "delete those
         recurring transactions first" and a one-off is not listed there.
      4. History check -- any non-deleted Transaction on this account, or
         under one of its definitions, triggers archive-instead-of-delete;
         so a rule-less definition still holding a live row anywhere
         archives the account exactly as an ad-hoc row does.  So does a
         SETTLED row under one of them whatever its ``is_deleted`` -- the
         definition door's own refusal (``template_has_paid_history``,
         ruling **R-JE**): the disposal below would otherwise leave that
         row TEMPLATE-priced with its link nulled, the state finding
         **N-440** names.  And so does a MOVEMENT on this account under a
         row that is neither on it nor under one of its definitions
         (``account_holds_other_rows_movements``, plan step
         ``credit_card:CC-5-2``): a card holding swipes recorded in
         checking envelopes is history the row arm cannot see, and the
         cleanup below cannot dispose of, so ``fk_transaction_entries_
         account_id``'s RESTRICT (ruling **R-CC32**) would refuse step 4
         as a 500.  A row or transfer the cleanup below removes holds no
         payment or purchase: a live one is history above, and the
         database refuses a hidden one holding one (rulings **R-CC89**,
         **R-CC92**).  **A card's own payment's transfers are its plan,
         not its history** (task plan#3754, ``account_has_history``):
         under it only a settled transfer, soft-deleted or not, or one
         holding a payment archives the card -- what the Recurring page's
         delete of the payment keeps -- and the archive then takes the
         payment with it, as the card's own Archive does.

    **Balance records alone do not archive** (ruling plan#2784).  An account's
    openings and true-ups, and a loan's terms and statements, are its alone:
    every correction books both halves on the account's own lines (pinned by
    ``tests/test_services/test_ledger_lines/test_the_target_rules.py``), so they
    go with it, and past statements lose those lines and stay in balance.
    Archive is what keeps an account's past.

    Permanent delete cleanup:
      After all guards pass, remaining RESTRICT-FK rows must be
      explicitly removed before the account row can be deleted:
        - A card's one payment, first, through the transfers' own delete
          act (``_transfer_lifecycle.remove_transfer_definition``, the
          Recurring page's delete without its commit): its unsettled
          transfers, the definition, and its settings row by cascade.  The
          definition keys onto the card, so its DELETE flushes before the
          card's, and the door commits both once.
        - Transfer rows (soft-deleted or ghost ad-hoc) referencing this
          account, deleted through transfer_service, which every write of
          a transfer goes through (CLAUDE.md, Architecture).
        - Transaction rows (soft-deleted ghosts) referencing this
          account.
        - The rule-less TransactionTemplates on this account (the only
          kind guard 3 lets through), each holding no live and no settled
          row (guard 4): a definition with no rule and no row defines
          nothing, and its RESTRICT key would refuse the account otherwise.
          Disposed of through ``definition_delete.permanently_delete_definition``,
          the SAME act the definition's own hard-delete performs, so the
          soft-deleted ghosts it still names (on this account or another)
          are deleted rather than unlinked -- none holding a movement, which
          the database refuses of a hidden row.  Their series rows cascade
          with them; the merchant rules that could name one cascade with the
          ACCOUNT already (``fk_merchant_rules_owner``).
        - A loan's per-loan chart rows, whose RESTRICT key would refuse the
          account otherwise.
      CASCADE-FK dependents (LoanParams, InterestParams,
      InvestmentParams, AccountAnchorHistory, AnchorRelease, SavingsGoal,
      LoanFeatures, and the account's own chart rows and every stored leg on
      them) are auto-deleted by PostgreSQL when the account row is removed.
      A stored entry with a leg elsewhere keeps that leg: the running app
      holds no DELETE on the journal, nothing reads a leg its movement has
      left, and X-ew's third leaf (plan#2516) deletes the store.
    """
    account = get_or_404(Account, account_id)
    if account is None:
        abort(404)

    # Guard 2: transfer templates with RESTRICT FK, but a card's own payment,
    # which goes with the card (step 0 below).
    payment = _own_payment(account)
    blockers = _other_transfer_definitions(account_id, payment).all()
    if blockers:
        flash(_DELETE_REFUSAL.sentence(blockers), "warning")
        return redirect(url_for("savings.dashboard"))

    # Guard 3: RECURRING transaction definitions with RESTRICT FK.  Loaded
    # and asked rather than filtered: ``recurs`` is a per-row derivation with
    # one spelling (``TransactionTemplate.recurs``), and a query keyed on it
    # refuses to build.  An account holds a handful of definitions.
    definitions = (
        db.session.query(TransactionTemplate)
        .filter_by(account_id=account_id, user_id=current_user.id)
        .all()
    )
    if any(definition.recurs for definition in definitions):
        flash(
            "Cannot delete this account -- it has recurring transactions. "
            "Delete those recurring transactions first.",
            "warning",
        )
        return redirect(url_for("savings.dashboard"))

    # Guard 4: transaction history, in its two arms (:func:`_history_refusal`).
    history_refusal = _history_refusal(account, definitions)
    if history_refusal is not None:
        return _archive_instead_of_delete(
            account, account_id, payment, history_refusal,
        )

    # All guards passed -- permanently delete.
    ctx = _stale_context("hard_delete_account", account_id)

    # Step 0: a card's one payment, through the transfers' own delete act
    # (task plan#3754).  Guard 4 has found no settled transfer under it and
    # none holding a payment (``account_has_history``), so what goes is its
    # plan: the act deletes its unsettled transfers and the definition, its
    # settings row cascading.  First, because the definition keys onto the
    # card (``RESTRICT``): its DELETE flushes here, before the card's at
    # step 4, and the act writes no commit, which the door makes once.
    with_payment = ""
    if payment is not None:
        with_payment = f", and its payment '{payment.name}' with it"
        refused = remove_transfer_definition(payment, ctx)
        if refused is not None:
            return refused
    # A loan's terms go first, before step 1.  Step 1's delete of a hidden
    # payment re-syncs the loan it left, and a loan the store is short of
    # would have the sync post on the very chart rows step 3 disposes of:
    # those legs go by the cascade, their entries stay empty, and the balanced
    # trigger their inserts queued refuses at COMMIT.  A loan with no terms
    # walks to nothing and the sync writes nothing
    # (``LoanLedgerWalk.is_empty``, the N1 guard), so this door never writes
    # on rows it deletes.
    db.session.query(LoanParams).filter_by(account_id=account_id).delete()

    # Step 1: delete remaining Transfer rows (soft-deleted or ghost
    # ad-hoc) through the transfer service, which every write of a transfer
    # goes through (CLAUDE.md, Architecture).
    remaining_transfers = (
        db.session.query(Transfer)
        .filter(db.or_(
            Transfer.from_account_id == account_id,
            Transfer.to_account_id == account_id,
        ))
        .all()
    )
    for xfer in remaining_transfers:
        transfer_service.delete_transfer(xfer.id, current_user.id, soft=False)

    # Step 2: delete remaining Transaction rows (soft-deleted ghosts
    # whose RESTRICT FK would block the account deletion).
    db.session.query(Transaction).filter(
        Transaction.account_id == account_id,
    ).delete(synchronize_session="fetch")

    # Step 2b: dispose of the rule-less definitions guard 3 let through.
    # Every one of them holds no live row and no settled row (guard 4
    # archived the account otherwise), so each defines nothing; the
    # soft-deleted, non-settled ghosts it still names -- on this account,
    # already gone at step 2, or on another -- are deleted, never unlinked,
    # because it is the SAME act the definition's own hard-delete performs.
    # None of them, and none of steps 1-2's rows or transfers, holds a
    # payment or purchase: guard 4 archived the account on a live one, and
    # the database refuses a hidden one holding one (rulings R-CC89,
    # R-CC92), so step 1's removal act and these deletes take no movement.
    # Only a state written past the database's triggers holds one, and the
    # two halves meet it differently: step 1 deletes a hidden transfer for
    # good through ``delete_transfer``, whose removal act takes its record
    # off the books first (they stay balanced), while step 2's delete of a
    # hidden row holding one meets ``fk_transaction_entries_transaction_id``.
    for definition in definitions:
        definition_delete.permanently_delete_definition(definition)

    # Step 3: explicitly delete CASCADE-FK dependents that lack ORM
    # relationships on Account.  Without explicit relationships,
    # SQLAlchemy's unit of work tries to SET NULL on their account_id
    # column before the DB-level CASCADE fires, violating NOT NULL.
    db.session.query(InterestParams).filter_by(account_id=account_id).delete()
    db.session.query(InvestmentParams).filter_by(account_id=account_id).delete()
    db.session.query(AssetAppreciationParams).filter_by(
        account_id=account_id,
    ).delete()
    # Escrow lines cascade their versions at the DB tier (ondelete=CASCADE on
    # ``line_id``); the bulk delete here clears the parent lines whose
    # ``Account.escrow_lines`` relationship would otherwise have the unit of work
    # attempt a NOT-NULL-violating SET NULL before the account delete.
    db.session.query(EscrowLine).filter_by(account_id=account_id).delete()
    db.session.query(RateHistory).filter_by(account_id=account_id).delete()
    db.session.query(SavingsGoal).filter_by(account_id=account_id).delete()
    ledger_account_service.dispose_loan_chart_rows(account_id)

    # Step 4: delete the account.  AccountAnchorHistory, AccountOpening and
    # LoanAnchorEvent are append-only (plan step X-f3c-2c), so the ORM must
    # NOT touch them: ``Account.anchor_history`` carries
    # ``passive_deletes="all"`` and no cascade, and PostgreSQL's own
    # ON DELETE CASCADE disposes of all three.  Their audit triggers conserve
    # every destroyed row in ``system.audit_log`` first.
    # The DELETE narrows by version_id thanks to the optimistic-lock
    # contract; a concurrent UPDATE that bumped the version since
    # this request loaded the row raises StaleDataError, which the
    # handler converts into a flash + redirect rather than a 500.
    account_name = account.name
    db.session.delete(account)
    conflict = commit_or_handle_stale(ctx)
    if conflict is not None:
        return conflict

    flash(f"Account '{account_name}' permanently deleted{with_payment}.", "info")
    return redirect(url_for("savings.dashboard"))
