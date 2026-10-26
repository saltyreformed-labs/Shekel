"""
Shekel Budget App -- Transfer Service loan posting wiring

The genesis-ledger glue for :mod:`app.services.transfer_service`: the helpers
that re-reconcile a loan's full posting ledger -- the confirmed-payment split
corrections AND the opening / true-up anchor corrections
(:mod:`app.services.loan_posting_service`) -- whenever a transfer mutation
settles, reverts, edits, restores, or deletes a loan payment.

Extracted from ``transfer_service`` so that module stays under the 1000-line
module limit as the loan-posting wiring lands -- the same split that moved the
ownership loaders into ``_ownership``.  These helpers are a cohesive,
transfer-service-private cluster (single responsibility: keep the loan's genesis
ledger in step with a transfer mutation), routing every call through
:mod:`app.services.loan_posting_service` so ``transfer_service`` itself carries
no loan-posting knowledge.  Flask-isolated like the parent service: plain data
in, ORM objects or plain values out, no ``request`` / ``session``.

A loan payment is a Transfer whose ``to_account`` is an amortizing loan
(:func:`_pays_a_loan`, the ONE spelling).  Its split correction links no row
(ruling **R-BAL102**, plan step ``balance:X-bi-6-3``): it is keyed by the
payment's period and visible day on the loan's own chart rows, so a delete or
an endpoint move owes it NO reversal first -- the loan sync the door runs
AFTER finds the vacated key with no target and reverses it.  Every loan
correction -- payment splits and anchor corrections alike -- touches only the
loan's own ledgers, never Checking, so it is invisible to the cash path.
"""

from datetime import date

from app.exceptions import ValidationError
from app.extensions import db
from app.models.account import Account
from app.models.transfer import Transfer
from app.services import (
    loan_loaders,
    loan_posting_service,
)
from app.services.pay_calendar import DerivedPeriod
from app.services.account_projection import (
    AccountProjectionKind,
    classify_account,
)


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
    both call :func:`._validation._reject_unmodeled_source`, the ONE set of
    source refusals, which asks this and its card sibling
    :func:`._validation._reject_transfer_out_of_revolving` together.

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
_POSTING_RELEVANT_INSTALLMENT_FIELDS = frozenset(
    {"pay_period_id", "due_date", "to_account_id"}
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

    Only the three kwargs in :data:`_POSTING_RELEVANT_INSTALLMENT_FIELDS` can
    put a payment on the wrong side of an origination date, so an edit touching
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
    if not _POSTING_RELEVANT_INSTALLMENT_FIELDS & updates.keys():
        return
    _reject_payment_before_origination(
        to_account, period_after, updates.get("due_date", xfer.due_date),
    )


def _is_loan(account: Account) -> bool:
    """Return whether *account* is an amortizing loan.

    The ONE spelling of the classification this package asks at five doors:
    the settle / revert / edit / restore sync and the delete door through
    :func:`_pays_a_loan`, the vacated-destination re-sync
    (:func:`_resync_vacated_loan`), the source refusal
    (:func:`_reject_transfer_out_of_loan`) and the pre-origination refusal
    (:func:`_reject_payment_before_origination`).

    Args:
        account: The account, its ``account_type`` loaded.

    Returns:
        ``True`` for an amortizing loan.
    """
    return classify_account(account) is AccountProjectionKind.AMORTIZING


def _pays_a_loan(xfer: Transfer) -> bool:
    """Return whether *xfer* is a loan payment: its destination amortizes.

    Asked by the settle / revert / edit / restore sync
    (:func:`_sync_loan_postings_if_loan`) and the delete door
    (``_delete.delete_transfer``, which captures the answer BEFORE the row
    goes so it can re-sync the loan the payment left).  A loan reached as a
    transfer's SOURCE is not one: a loan's payment set is the transfers INTO
    it (:func:`app.services.loan_loaders.income_legs`), and a transfer
    OUT of a loan is refused at the source anyway
    (:func:`_reject_transfer_out_of_loan`).

    Args:
        xfer: The transfer, its ``to_account`` (with ``account_type``) loaded.

    Returns:
        ``True`` when the destination is an amortizing loan.
    """
    return _is_loan(xfer.to_account)


def _sync_loan_postings_if_loan(xfer: Transfer) -> None:
    """Re-sync a loan's full genesis ledger after a settle / revert / edit / restore.

    When *xfer* pays down an amortizing loan, reconcile that loan's FULL genesis
    ledger (:func:`app.services.loan_posting_service.sync_loan_postings`) to the
    transfer's now-current settled state, in the transfer's own scenario -- BOTH
    the per-payment principal / interest / escrow split corrections
    AND the opening / true-up anchor corrections.  The anchor half matters
    because a change to a payment that came due BEFORE a true-up moves that
    true-up's ``owed_before`` (the running balance it corrects from), so the
    true-up self-heals in the same reconcile; and every payment's split rides
    the same running balance, so this is a whole-loan reconcile, not a
    per-payment one.  A no-op for a non-loan transfer (the common case), so the
    settle / revert / restore chokepoints call it unconditionally after the
    Step-2 cash reconcile.

    Every correction touches only the loan's own ledgers (never Checking) --
    the payment splits and the anchor corrections alike link no row and are
    keyed on the loan's own chart rows (ruling **R-BAL102**) -- so the whole
    reconcile is structurally invisible to the cash path and cannot move a
    cash balance (plan Section 5 / 7).

    Args:
        xfer: The transfer just mutated.  Its ``to_account`` (with
            ``account_type``) drives the amortizing-loan classification and its
            ``scenario_id`` scopes the reconcile.
    """
    if _pays_a_loan(xfer):
        loan_posting_service.sync_loan_postings(
            xfer.to_account_id, xfer.scenario_id,
        )


def _resync_loan_after_payment_left(
    loan_account_id: int, scenario_id: int,
) -> None:
    """Re-sync a loan's downstream ledger after one payment LEAVES it.

    Run AFTER the payment is no longer the loan's: re-reconciles the loan's
    full genesis ledger
    (:func:`app.services.loan_posting_service.sync_loan_postings`) --
    re-splitting the LATER confirmed payments whose running balance the
    departure changed AND re-deriving any true-up whose ``owed_before`` it moved
    (a pre-true-up payment leaving).  Takes the loan / scenario ids explicitly
    because the caller has captured them before the payment moved (a
    hard-deleted ``xfer`` can no longer be read at all).

    **A payment leaves a loan in two ways, and it was named for only one of
    them until plan step R10-b.**  It is DELETED (the transfer row goes), or
    its transfer's DESTINATION is re-pointed at another account, which is what
    :func:`app.services.transfer_service.update_transfer`'s endpoint arm can now
    do.  The re-reconcile the loan needs is identical in both cases -- it reads
    the loan's remaining payment set rather than the departing row -- so the two
    callers share one body rather than the second growing a near-copy of it.

    **And this AFTER-resync is the whole of what the departure owes the
    ledger** (ruling **R-BAL102**, plan step ``balance:X-bi-6-3``).  Through
    that step each caller ALSO reversed the departing payment's split
    correction FIRST, while its income shadow still existed and still sat on
    the loan, because the split was keyed by that shadow's ``transaction_id``
    and the loan-side reconcile found a loan's payments through the account
    the shadow sat on -- a hard delete SET-NULLed the link and an endpoint
    move hid the row, either stranding the correction (the R10-b ``-$4.17``
    that measured it).  The split links no row now and is keyed on the LOAN's
    own chart rows by the payment's period and day, so the departed payment's
    key is simply a posted key with no target when this walk runs, and the
    one reconcile reverses it.

    Args:
        loan_account_id: The loan whose downstream ledger to re-reconcile.
        scenario_id: The departing payment's scenario.
    """
    loan_posting_service.sync_loan_postings(loan_account_id, scenario_id)


def _resync_vacated_loan(account_id: int, scenario_id: int) -> None:
    """Re-reconcile *account_id* when a transfer's endpoint move just left it.

    The endpoint-move half of :func:`_resync_loan_after_payment_left` (plan step
    R10-b), and the only thing it adds is the classification: a vacated endpoint
    is an ordinary account far more often than it is a loan, and the caller --
    :func:`app.services.transfer_service._update._reconcile_postings_after_update`
    -- holds an account ID rather than a row, so asking "was that a loan" here
    keeps this package's loan knowledge in this module.

    **Only the vacated DESTINATION is offered to it, and an adversarial review
    of plan step R10-b is why the source is not.**  A loan reached as a
    transfer's SOURCE carries that transfer's EXPENSE leg, and a loan's
    payment set is :func:`app.services.loan_loaders.income_legs` --
    transfers INTO the loan only -- so such a transfer was never one of the
    loan's payments and losing it re-derives nothing.  A first version offered both
    endpoints and justified it by a legacy loan-source row "holding a payment it
    no longer has", which is not what that row holds; removing the call left the
    legacy case green while both destination cases failed.

    Args:
        account_id: The account the transfer just moved OFF.
        scenario_id: The transfer's scenario.
    """
    account = db.session.get(Account, account_id)
    if account is None or not _is_loan(account):
        return
    _resync_loan_after_payment_left(account_id, scenario_id)
