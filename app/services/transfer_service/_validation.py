"""
Shekel Budget App -- Transfer Service validate-and-load guards

The input-validation and entity-loading guards for
:mod:`app.services.transfer_service`: validate a submitted amount, refuse a
transfer OUT of a source no engine models one from (the composed loan-or-card
set both doors call), refuse a loan payment whose installment falls at or
before its loan's origination (ruling **R-C**, at the create and at an edit
that moves it), load the owned/active
:class:`~app.models.transfer.Transfer` a mutation writes, and refuse a
restore onto an archived account.
Each is a precondition check the mutation entry points run before they touch
any row, raising the project's domain exceptions
(:class:`~app.exceptions.ValidationError` for a bad amount, a refused loan
payment or a restore onto an archived account,
:class:`~app.exceptions.NotFoundError` for a missing or
not-yours transfer -- with an identical message for both the "missing" and
the "not yours" case, the project security-response rule -- no existence
oracle).

Extracted from ``transfer_service`` so that module stays under the 1000-line
module limit as the Build-Order Step 2-4 posting-ledger wiring lands -- the
same split that moved the ownership loaders into ``_ownership``.
:func:`assert_restorable` joined them at plan step X-aj1 (ruling **R-DR**),
bringing ``restore_transfer``'s preconditions to the module whose single
responsibility they already were.  **The loan refusals joined them at plan
task plan#3075**, from ``_loan_posting``, the loan's genesis-ledger glue they
had shared a module with since the split: that glue went with the stored
ledger's transfer doors, and what it left were preconditions a mutation asks
before it touches a row, which is this module's responsibility.

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
from datetime import date
from decimal import Decimal, InvalidOperation

from app.extensions import db
from app.models.account import Account
from app.models.transfer import Transfer
from app.exceptions import NotFoundError, ValidationError
from app.services import loan_loaders
from app.services.account_projection import (
    AccountProjectionKind,
    classify_account,
    is_revolving,
)
from app.services.pay_calendar import DerivedPeriod
from app.utils.log_events import (
    BUSINESS,
    EVT_TRANSFER_RESTORE_REFUSED_ARCHIVED_ACCOUNT,
    log_event,
)

logger = logging.getLogger(__name__)


# The ``update_transfer`` kwargs that can move a loan payment across its loan's
# origination, and therefore into the state ruling R-C refuses (plan step C9b).
# ``due_date`` names the installment directly; ``pay_period_id`` supplies the
# fallback basis for a payment carrying no due date
# (``loan_loaders.installment_for``); and ``to_account_id`` moves WHICH LOAN --
# and so which origination date -- the installment is graded against, which is
# plan step R10-b's addition.  A payment sitting comfortably after loan A's
# origination can sit before loan B's without its own installment moving at all,
# so an endpoint move re-asks the question even when neither date field is in
# the payload.  Nothing else moves it -- an amount or status edit leaves both
# the installment and the loan where they are -- so an edit touching none of the
# three is never re-checked, which is what keeps a pre-existing
# pre-origination row (legacy data the C9a purge deliberately left, e.g. a
# settled one) editable in every other respect.
#
# **It was ``_POSTING_RELEVANT_INSTALLMENT_FIELDS`` until plan task
# plan#3075**, which deleted the posting reconcile that followed an update and
# measured what the set still answers: the trigger of
# :func:`_reject_installment_move_before_loan`, and of ``._update``'s one
# resolution of the paycheck an edit leaves the transfer in, which that refusal
# and a placed transfer's re-placing both read.  Both are about the installment
# and the paycheck, neither about a posting, so the set stays and its name lost
# the word that was no longer true.
_INSTALLMENT_RELEVANT_FIELDS = frozenset(
    {"pay_period_id", "due_date", "to_account_id"}
)


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


def _is_loan(account: Account) -> bool:
    """Return whether *account* is an amortizing loan.

    The ONE spelling of the classification this package asks, at its two loan
    refusals: the source refusal (:func:`_reject_transfer_out_of_loan`) and
    the pre-origination refusal (:func:`_reject_payment_before_origination`).
    The stored ledger's transfer doors asked it too -- the settle, revert,
    edit and restore sync and the delete's re-sync, through a
    ``_pays_a_loan`` beside it, and the vacated-destination re-sync -- until
    plan task plan#3075 deleted them.

    Args:
        account: The account, its ``account_type`` loaded.

    Returns:
        ``True`` for an amortizing loan.
    """
    return classify_account(account) is AccountProjectionKind.AMORTIZING


def _reject_transfer_out_of_loan(from_account: Account) -> None:
    """Reject a transfer whose SOURCE is an amortizing loan.

    A transfer OUT of a loan (the loan as ``from_account``) is not a modeled
    operation.  The amortization engine only projects payments INTO a loan, and
    the loan's posting ledger assumes every transfer leg on a loan is a
    payment IN: the
    per-payment interest / escrow split, the genesis reader's income-only
    history walk, and the reconciliation oracle's superseding invariant
    (``linked == settled_income_cash - per_loan_corrections``) all rest on it.
    A disbursement would instead post a raw cash movement onto the loan's linked
    ledger with no split correction and misproject every forward balance, so it
    is forbidden at BOTH doors that can put a transfer on a source account --
    :func:`app.services.transfer_service.create_transfer`, and, since plan step
    R10-b, :func:`._endpoints._resolve_endpoints`, which is where an update
    MOVES one -- rather than silently corrupting the loan's balance.  Refusing
    at only the first would have left the second a way straight past it.
    Since plan step ``credit_card:CC-10`` neither door names this function:
    both call :func:`_reject_unmodeled_source`, the ONE set of source
    refusals, which asks this and its card sibling
    :func:`_reject_transfer_out_of_revolving` together.

    Args:
        from_account: The transfer's source account (already ownership-checked).

    Raises:
        ValidationError: When *from_account* is an amortizing loan.
    """
    if _is_loan(from_account):
        raise ValidationError(
            f"Cannot transfer money out of a loan: source account "
            f"'{from_account.name}' is an amortizing loan."
        )


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
    out of a loan is refused (:func:`_reject_transfer_out_of_loan`).

    **Asked at BOTH doors that can put a transfer on a source account**, which
    is the loan refusal's discipline: :func:`app.services.transfer_service.create_transfer`,
    and :func:`._endpoints._resolve_endpoints`, where an update MOVES a
    source.  Refusing at only the first would leave the second a way straight
    past it -- and neither door names this function.  Both call
    :func:`_reject_unmodeled_source`, the ONE set of source refusals, so a
    door cannot hold the loan's refusal and miss the card's.

    **It lives beside the loan refusal, in this module**, whose
    responsibility is exactly this: a precondition a mutation asks before it
    touches a row.  It was written here rather than beside the loan refusal
    because that one lived in ``_loan_posting``, the loan's genesis-ledger
    glue, and a card has none -- it classifies PLAIN and rides the cash fold;
    plan task plan#3075 deleted the glue and brought the loan refusals here.
    It reads :func:`app.services.account_projection.is_revolving`, the ONE
    predicate every card feature gates on (plan step CC-1, ruling
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
            (:func:`_reject_transfer_out_of_loan`) or a credit card
            (:func:`_reject_transfer_out_of_revolving`).
    """
    _reject_transfer_out_of_loan(from_account)
    _reject_transfer_out_of_revolving(from_account)


def _reject_payment_before_origination(
    to_account: Account, period: DerivedPeriod, due_date: date | None,
) -> None:
    """Reject a loan payment whose installment precedes the loan's origination.

    Ruling R-C (plan step C9b).  A loan cannot receive a payment before it
    exists, and the reason is not tidiness -- such a payment is ERASED, silently:

    * the fold orders events by installment and applies each anchor as a RESET,
      with a payment sorting BEFORE an anchor on a shared date, so a payment due
      at or before origination splits against a running balance of ZERO.
      The ONE allocation's closed-loan arm
      (``app.utils.money.apply_payment_cash``) routes the entire cash to
      ``excess`` -- measured $0.00 interest, $0.00 principal, $1,200.00 to a
      Refund Receivable -- and the origination anchor then resets the balance to
      the full principal over the top of it.
    * meanwhile the cash side debits the funding account in full.

    So the money leaves checking, the loan is untouched, and the app models the
    lender as owing it back.  The developer ruled this REJECTED at the write
    boundary rather than modeled as a prepayment (which is a feature) or left to
    fail loud on read (which would 500 a page for data the user was allowed to
    enter).

    **The boundary is ``<=``, not ``<``, and it is stated ONCE**
    (:func:`~app.services.loan_loaders.precedes_origination`, since plan step
    R16-b-2, which asks it of every occurrence the forward plan estimates so
    the plan never prices a row this guard would refuse).  A payment due
    exactly ON the origination date is subsumed by that anchor's reset -- the
    same strict ``anchor_date < due_date`` post-anchor rule
    (:func:`~app.services.loan_ledger.replay_loan_events`) -- so it
    is erased identically.  Swept and measured: due 02-01, 02-28 and 03-01
    against a 03-01 origination all book $0.00 principal; 03-02 pays down
    $366.67.

    The installment is derived through the SHARED
    :func:`~app.services.loan_loaders.installment_for`, so the guard refuses
    exactly the payments the fold would erase -- including one carrying NO
    ``due_date``, whose installment comes from its pay-period start (an ad-hoc
    transfer into a loan, which is how the shape was originally found).

    A payment due AFTER origination but before the first contractual installment
    is deliberately ALLOWED: an early extra payment is legitimate and the fold
    splits it correctly against the opening balance.  This guard is about the
    loan's EXISTENCE; the recurrence ``start_date`` bound (C9a) is what keeps
    generated installments on the contract.

    A no-op for a non-loan destination and for an amortizing account with no
    :class:`~app.models.loan_params.LoanParams` yet (nothing to compare against
    -- ``classify_account`` reads the account TYPE only, so a Mortgage-typed
    account can be unconfigured).

    **It TAKES the period rather than its id** (plan step
    ``pay_calendar:C13-b``).  This was ``pay_period_id`` plus a
    ``db.session.get(PayPeriod, ...)`` of its own -- a SECOND read, in the same
    request, of the row the caller's :func:`~._ownership._get_owned_period` had
    just resolved -- and a ``None`` arm underneath it that had to explain why
    it stayed silent about a row the caller would refuse anyway.  Taking the
    value deletes the read, the arm and the explanation, and makes the ordering
    the caller's comment used to assert ("deliberately AFTER
    ``_get_owned_period``") a data dependency the signature enforces.

    Args:
        to_account: The transfer's destination account (already
            ownership-checked).
        period: The paycheck the payment lands in, as the owner's calendar
            derives it -- it supplies the installment fallback when *due_date*
            is ``None``.
        due_date: The payment's due date, or ``None``.

    Raises:
        ValidationError: When *to_account* is a configured loan and the
            payment's installment falls at or before its origination.
    """
    if not _is_loan(to_account):
        return
    params = loan_loaders.load_loan_params(to_account.id)
    if params is None:
        return
    installment = loan_loaders.installment_for(
        due_date, period.start_date, params.payment_day,
    )
    if not loan_loaders.precedes_origination(params, installment):
        return
    raise ValidationError(
        f"Cannot pay '{to_account.name}' before it originates: this payment's "
        f"installment ({installment.isoformat()}) falls on or before the loan's "
        f"origination date ({params.origination_date.isoformat()}).  Move the "
        f"payment to a later pay period, or correct the loan's origination date."
    )


def _reject_installment_move_before_loan(
    xfer: Transfer,
    updates: dict[str, object],
    to_account: Account,
    period_after: DerivedPeriod | None,
) -> None:
    """Refuse an ``update_transfer`` edit that drags a loan payment behind its loan.

    The edit-path half of ruling R-C (the create-path half is the
    :func:`_reject_payment_before_origination` call in
    :func:`create_transfer`).  Create is not the only door: the transfers PATCH
    route forwards ``due_date`` and ``pay_period_id`` straight through, so a
    payment created legitimately after origination could be moved behind it and
    then settled -- landing in exactly the erased state the create guard refuses
    (see that guard for what "erased" costs).

    Runs BEFORE any field is applied, so a rejected edit leaves the transfer and
    its sides' records untouched -- the same discipline
    :func:`app.services.transfer_service._status.apply_status_to_all_three` follows
    for an illegal status transition.

    Only the three kwargs in :data:`_INSTALLMENT_RELEVANT_FIELDS` can put a
    payment on the wrong side of an origination date, so an edit touching
    none of them is never re-checked.  That is deliberate: it keeps a
    PRE-EXISTING pre-origination row -- legacy data the C9a purge intentionally
    leaves behind, such as a settled payment whose cash really moved -- editable
    in every other respect instead of frozen.

    **The paycheck the edit leaves the transfer in arrives RESOLVED** (plan
    step ``balance:X-ci-1``, ruling **R-BAL96**), and this guard no longer
    resolves one.  It called ``_ownership._get_owned_period`` itself, and so
    did ``._update._reject_unowned_references`` two gates later -- a second
    walk of the owner's calendar for one edit, measured at plan step
    ``pay_calendar:C13-b`` (2026-09-03, ``PATCH /transfers/instance/<id>``
    moving a transfer between periods: 21 statements before that step, 23
    after, two payday reads either side) and left in place then because two
    statements were not enough to restructure a pre-write block for.  The
    re-placing rule this leaf adds needs the same paycheck a third time, and
    three walks for one value is the shape the developer's DRY rule names
    (one producer per request, threaded), so ``._update`` resolves it ONCE --
    which is also where the submitted ``pay_period_id`` is ownership-checked
    now, ahead of this guard, for the reason this guard used to do it: it
    reads that period's ``start_date`` as the installment fallback, and an
    unowned row answering it would return a 400 carrying a date derived from
    it where the security-response rule requires an indistinguishable 404.
    **The destination account is the caller's already-owned value for the
    same reason** (plan step R10-b): the guard names that account in its
    refusal message, so resolving an unowned id here would answer a
    cross-user probe with that account's NAME.

    Args:
        xfer: The transfer being updated (supplies the current due date for
            an edit that does not move it).
        updates: The :func:`update_transfer` kwargs about to be applied --
            read, not written; *xfer* still holds its pre-edit values.  A
            placed transfer's re-placed ``due_date`` is already in it
            (``._placed.re_place_and_grade_the_day`` runs first), so a one-time payment
            into a loan is graded on the installment the move leaves it with.
        to_account: The destination this edit LEAVES the transfer with, already
            ownership-checked by the caller -- ``xfer.to_account`` when the edit
            moves no endpoint.
        period_after: The paycheck this edit leaves the transfer in, resolved
            off the owner's calendar by the caller whenever *updates* names
            one of the three fields above; ``None`` otherwise, and not read.

    Raises:
        ValidationError: If the resulting installment falls at or before the
            destination loan's origination.
    """
    if not _INSTALLMENT_RELEVANT_FIELDS & updates.keys():
        return
    _reject_payment_before_origination(
        to_account, period_after, updates.get("due_date", xfer.due_date),
    )


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
