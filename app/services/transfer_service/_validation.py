"""
Shekel Budget App -- Transfer Service validate-and-load guards

The input-validation and entity-loading guards for
:mod:`app.services.transfer_service`: validate a submitted amount, refuse a
transfer OUT of a source no engine models one from (the composed loan-or-card
set both doors call), load the owned/active
:class:`~app.models.transfer.Transfer` a mutation writes, and refuse a
restore onto an archived account.
Each is a precondition check the mutation entry points run before they touch
any row, raising the project's domain exceptions
(:class:`~app.exceptions.ValidationError` for a bad amount or a restore onto
an archived account, :class:`~app.exceptions.NotFoundError` for a missing or
not-yours transfer -- with an identical message for both the "missing" and
the "not yours" case, the project security-response rule -- no existence
oracle).

Extracted from ``transfer_service`` so that module stays under the 1000-line
module limit as the Build-Order Step 2-4 posting-ledger wiring lands -- the
same split that moved the ownership loaders into ``_ownership`` and
the loan-posting glue into ``_loan_posting``.  :func:`assert_restorable`
joined them at plan step X-aj1 (ruling **R-DR**), bringing ``restore_transfer``'s
preconditions to the module whose single responsibility they already were.

These helpers are a cohesive, transfer-service-private cluster (single
responsibility: validate inputs and load the transfer a mutation operates on).
They write no ``status_id`` and construct no ``Transaction`` -- so they stay
clear of the W9907 status fence, whose one transfer entry is the ``_create``
leaf's constructor write -- and they compute no balance.
Flask-isolated like the parent service: plain data in, ORM objects out, no
``request`` / ``session`` imports.

**Each act takes the** :class:`~app.models.transfer.Transfer` **itself since
finding plan#2043**, which deleted the ``TransferRows`` wrapper that stood
here.  It came in at plan step X-f2-c3 as Transfer Invariant 1 made a TYPE --
a parent and exactly two shadow transactions, whose statuses and periods were
kept equal -- because threading the three through the settle's signature put
it over pylint's argument ceiling, and a call site that could pair one
transfer with another's shadows could break the invariant by typo.  Plan step
``balance:X-bi-6-4d-2`` deleted the twin rows (rulings **R-BAL166**,
**R-BAL258**) and its leaf 3b the ``shadows`` attribute with the loader that
queried them (``load_transfer_rows``) and the arms of ``_update``,
``_amount`` and ``_endpoints`` that wrote the mirrors over them.  Its last
field beside the transfer, ``expense_leg``, went with its last reader at
plan#2019 (ruling **plan#1983**), which left a wrapper of one field: no
ceiling to stay under and no pair to keep together.
"""

import logging
from decimal import Decimal, InvalidOperation

from app.extensions import db
from app.models.account import Account
from app.models.transfer import Transfer
from app.exceptions import NotFoundError, ValidationError
from app.services.account_projection import is_revolving
from app.services.transfer_service._loan_posting import (
    _reject_transfer_out_of_loan,
)
from app.utils.log_events import (
    BUSINESS,
    EVT_TRANSFER_RESTORE_REFUSED_ARCHIVED_ACCOUNT,
    log_event,
)

logger = logging.getLogger(__name__)


def _validate_positive_amount(amount):
    """Ensure *amount* is a positive Decimal.

    Args:
        amount: The transfer amount (Decimal, int, float, or string).

    Returns:
        The validated amount as a Decimal.

    Raises:
        ValidationError: If amount is zero, negative, or not numeric.
    """
    try:
        amount = Decimal(str(amount))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise ValidationError(
            f"Invalid amount: {amount!r}.  Must be a positive number."
        ) from exc
    if amount <= 0:
        raise ValidationError(
            "Transfer amount must be positive."
        )
    return amount


def _reject_transfer_out_of_revolving(from_account: Account) -> None:
    """Reject a transfer whose SOURCE is a revolving credit line (a credit card).

    A transfer OUT of a card is a cash advance (into a cash account) or a
    balance transfer (into another card), and the card refuses both rather
    than modelling them (design ``docs/design/credit_card_from_scratch.md``
    3.8, plan step ``credit_card:CC-10``).  The card's whole model rests on
    money leaving it ONLY as a purchase -- a movement on the card (3.2) -- and
    a transfer touching it only as its PAYMENT, into it (3.5): the statement's
    grace test is graded on the credits INTO the card
    (:func:`app.services.card_statement.grace_kept`), and its finance charge
    prices one APR per date (``budget.rate_history`` through
    :func:`app.services.card_apr.apr_in_effect`) over the balance path (3.6).
    A cash advance carries its own APR, a fee and no grace, which one APR row
    per date and one grace rule cannot express, so admitting it would price
    every later statement of that card wrong -- the same reason a disbursement
    out of a loan is refused (:func:`._loan_posting._reject_transfer_out_of_loan`).

    **Asked at BOTH doors that can put a transfer on a source account**, which
    is the loan refusal's discipline: :func:`app.services.transfer_service.create_transfer`,
    and :func:`._endpoints._resolve_endpoints`, where an update MOVES a
    source.  Refusing at only the first would leave the second a way straight
    past it -- and neither door names this function.  Both call
    :func:`_reject_unmodeled_source`, the ONE set of source refusals, so a
    door cannot hold the loan's refusal and miss the card's.

    **It lives here, not beside the loan refusal in** :mod:`._loan_posting`,
    because that module's responsibility is the loan's genesis-ledger glue and
    a card has none -- it classifies PLAIN and rides the cash fold -- while
    this module's is exactly this: a precondition a mutation asks before it
    touches a row.  It reads :func:`app.services.account_projection.is_revolving`,
    the ONE predicate every card feature gates on (plan step CC-1, ruling
    ``credit_card:R-CC14``).

    What stays ALLOWED, and is pinned beside the refusals: a transfer INTO the
    card (its payment), a move of a transfer's DESTINATION onto a card, and
    direct income on the card at the transaction-create doors (a refund, a
    redemption), which refuse an amortizing loan only
    (:func:`app.routes.transactions.create._reject_transaction_on_loan`).

    Args:
        from_account: The transfer's source account (already ownership-checked,
            with its ``account_type`` loaded).

    Raises:
        ValidationError: When *from_account* is a credit card.
    """
    if is_revolving(from_account):
        raise ValidationError(
            f"Cannot transfer money out of a credit card: source account "
            f"'{from_account.name}' is a credit card.  A card is paid by a "
            f"transfer INTO it; money leaves it only as a purchase on the card."
        )


def _reject_unmodeled_source(from_account: Account) -> None:
    """Refuse a SOURCE no engine models a transfer out of: a loan or a card.

    **The ONE set of source refusals, and the only name either door calls.**
    A transfer's source is refused at two doors --
    :func:`app.services.transfer_service.create_transfer` and
    :func:`._endpoints._resolve_endpoints`, where an update moves a source --
    and the loan refusal's own docstring records why refusing at one door
    leaves the other a way straight past it.  Two refusals spelled at two
    doors is the same defect one level up: a third refusal added at one door
    and forgotten at the other, with nothing but a test per door to notice.
    Composing the set here gives it ONE home: a refusal joins this body and
    reaches both doors (plan step ``credit_card:CC-10``, CLAUDE.md rule 14).
    It does not make a one-door refusal unrepresentable -- a call added
    directly at either door still is one -- which is why neither door names
    an arm of this set, and why the arms have no other caller.

    The order is immaterial: ``ck_account_types_revolving_is_plain`` makes a
    type that is both amortizing and revolving unrepresentable, so at most one
    arm can fire for any real row.

    Args:
        from_account: The transfer's source account (already ownership-checked,
            with its ``account_type`` loaded).

    Raises:
        ValidationError: When *from_account* is an amortizing loan
            (:func:`._loan_posting._reject_transfer_out_of_loan`) or a credit
            card (:func:`_reject_transfer_out_of_revolving`).
    """
    _reject_transfer_out_of_loan(from_account)
    _reject_transfer_out_of_revolving(from_account)


def _get_transfer_or_raise(transfer_id, user_id, allow_deleted=False):
    """Load a Transfer and verify ownership and active status.

    Args:
        transfer_id:   The primary key.
        user_id:       The expected owner.
        allow_deleted: If False (default), soft-deleted transfers are
                       treated as non-existent and raise NotFoundError.
                       Set to True for operations that legitimately need
                       to act on deleted transfers (e.g. delete_transfer
                       for idempotent soft-delete, restore_transfer).

    Returns:
        The Transfer object.

    Raises:
        NotFoundError: If the transfer does not exist, belongs to
            another user, or is soft-deleted (when allow_deleted is
            False).  The message is identical in all cases (security
            response rule -- do not reveal existence to wrong user).
    """
    xfer = db.session.get(Transfer, transfer_id)
    if xfer is None or xfer.user_id != user_id:
        raise NotFoundError(f"Transfer {transfer_id} not found.")
    # Soft-deleted transfers are invisible to normal operations: a deleted
    # transfer takes no money (ruling R-CC89).
    if not allow_deleted and xfer.is_deleted:
        raise NotFoundError(f"Transfer {transfer_id} not found.")
    return xfer


def assert_restorable(xfer, user_id):
    """Refuse a restore whose preconditions do not hold, before anything moves.

    The check ``restore_transfer`` runs before it un-deletes a thing.
    Extracted here at plan step X-aj1 (ruling **R-DO**) because it is a
    precondition check on the rows a mutation operates on, which is this
    module's single responsibility, and because gathering the checks made the
    caller's own defect visible: it used to set ``is_deleted = False`` FIRST
    and then hand-restore the flag on each failing branch -- a rollback written
    out three times, with three chances for the next branch to forget it.
    Validating before mutating makes that class of miss structurally
    impossible.

    **Archived endpoints (F-164).**  The account FK is RESTRICT, so the rows
    cannot be hard-deleted while the transfer references them; the only way an
    endpoint goes away semantically is ``is_active = False``.  Restoring onto
    one would resurrect entries against an account the user has withdrawn from
    active projections, producing balance drift they have no UI affordance to
    investigate.

    Three more checks went with the twin rows they graded.  An **unrepairable
    status drift** (ruling **R-DO**: a twin whose status could not legally reach
    the parent's) went at plan step ``balance:X-bi-6-4d-2``, when a twin's
    status stopped being kept or read; the **twin count** (exactly two) and the
    **type pairing** (one expense, one income) went at ``balance:X-bi-6-4d-2``,
    which deleted the twins and made the database refuse a new one (ruling
    **R-BAL258**).

    Args:
        xfer: The soft-deleted :class:`~app.models.transfer.Transfer` being
            restored.  NOT mutated here.
        user_id: The owner, for the archived-endpoint refusal's structured log.

    Raises:
        ValidationError: When either endpoint is archived, with a message
            naming what a human has to fix.
    """
    transfer_id = xfer.id
    from_account = db.session.get(Account, xfer.from_account_id)
    to_account = db.session.get(Account, xfer.to_account_id)
    from_active = bool(from_account is not None and from_account.is_active)
    to_active = bool(to_account is not None and to_account.is_active)
    if not (from_active and to_active):
        log_event(
            logger, logging.WARNING,
            EVT_TRANSFER_RESTORE_REFUSED_ARCHIVED_ACCOUNT, BUSINESS,
            "Refused to restore transfer with archived account",
            user_id=user_id,
            transfer_id=transfer_id,
            from_account_id=xfer.from_account_id,
            to_account_id=xfer.to_account_id,
            from_account_active=from_active,
            to_account_active=to_active,
        )
        raise ValidationError(
            "Cannot restore transfer: source or destination account "
            "is archived.  Reactivate the account before restoring."
        )
