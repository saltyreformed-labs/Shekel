"""Loan-payment correction posting: the per-payment split's TARGET legs.

The REAL principal / interest / escrow / refund split of a confirmed loan
payment, as the balanced CORRECTION the ledger layers on top of the payment's
cash movement (:mod:`app.services._posting_purchases`, plan step
``balance:X-bi-6-3``: the loan-side covering movement's own entry
``Loan +cash / Transit -cash``).  That entry dumps the ENTIRE cash onto the
loan, when only the PRINCIPAL portion pays the debt down.  Because a posted
entry is immutable, the ledger appends a second balanced entry that moves the
non-principal off the loan::

    Loan     -(interest + escrow + excess)   [principal]
    Interest +interest                        [interest -> Expense]
    Escrow   +escrow                          [escrow   -> Expense]
    Refund   +excess                          [refund   -> Asset]
             --------------------------------
             0

The loan's NET (the cash leg + this correction) is then exactly the real
principal paid.  The split (:mod:`app.services.loan_ledger`) is computed from
the ACTUAL cash (``principal = cash - interest - escrow``), so an extra or short
payment is captured honestly -- and a payment of ``$0.00`` (a missed
installment recorded as paid) carries the whole standing charge as an
underpayment, growing the debt by exactly the interest and escrow it did not
clear.

**A DERIVATION, keyed like one: ``(loan_payment kind, the payment's pay
period, its visible day)`` and NO row link** (ruling **R-BAL102**, which
re-ruled **R-BAL100**).  The split is re-computed from scratch by every walk,
exactly as the loan's opening and true-ups are, so it is reconciled in the
SAME loop as they are (:mod:`._corrections`) and its entry links no row: not
the loan-side shadow (its key through ``balance:X-bi-6-1b``, a row plan step
``X-bi-6-4d-2`` deleted), not the shadow's covering movement (R-BAL100's key,
which a ``$0.00`` payment does not have -- the seam writes no movement for a
figure of nothing, and the walk still charges the installment), not the
transfer.  A key with no row has nothing to lose: no teardown door owes it a
reversal first, no detector hunts payments that "left the confirmed set" (a
key with no target reverses in the same pass), and no reader maps rows back
onto payments.  Two payments on one day in one period merge into one entry,
which is the sum the ledger holds; WHICH payment paid what is the walk's
answer (:mod:`._display`), never the ledger's.

**This module builds the TARGET and nothing else.**  The reconcile -- what is
posted, the delta, the emission -- is :mod:`._corrections`' one loop over the
anchor and split targets together, on the shared primitives in
:mod:`app.services._posting_reconcile`.

**Flask-isolated**: plain data in, plain values out; never imports
``request`` / ``session``.  Reads the chart (minting a per-loan row on first
use) but writes no posting.
"""

from decimal import Decimal

from app import ref_cache
from app.enums import (
    LedgerAccountKindEnum,
    PostingKindEnum,
    PostingSourceEnum,
)
from app.services import ledger_account_service
from app.services.posting_service import _ledger_account_for
from app.services._posting_reconcile import (
    CorrectionKey,
    LegMap,
    merge_target_legs,
)

from app.services.loan_ledger import PaymentOutcome


# The three per-loan correction components, each a tuple of (the per-loan ledger
# account KIND to resolve, the posting-leg KIND to tag the leg, the
# :class:`~app.services.loan_ledger.PaymentOutcome` attribute holding the leg's
# amount -- its flat read-through properties).  The loan-linked principal leg
# is handled separately -- it books onto the loan's existing Asset/Liability
# account mirror (the ``linked`` ledger), not a per-loan account.  Driving the
# three components off one table keeps the target builder DRY and makes "add a
# component" a one-line change.
_LOAN_CORRECTION_COMPONENTS = (
    (LedgerAccountKindEnum.LOAN_INTEREST, PostingKindEnum.INTEREST, "interest"),
    (LedgerAccountKindEnum.LOAN_ESCROW, PostingKindEnum.ESCROW, "escrow"),
    (LedgerAccountKindEnum.LOAN_REFUND, PostingKindEnum.REFUND, "excess"),
)


def _loan_payment_target(outcome: PaymentOutcome) -> LegMap:
    """Build the target ledger legs for one payment's real-split correction.

    Maps the split to ``{ledger_account_id: (signed amount, posting_kind_id)}``,
    dropping any zero component so no empty per-loan ledger account is minted and
    no zero leg is written:

    * the loan's LINKED ledger (the Asset/Liability mirror the cash movement
      dumped the whole cash onto) gets ``-(interest + escrow + excess)`` tagged
      ``principal`` -- so the loan's NET across the cash leg and this
      correction is exactly ``principal`` (plan Section 1);
    * the per-loan ``loan_interest`` Expense ledger gets ``+interest``;
    * the per-loan ``loan_escrow`` Expense ledger gets ``+escrow``;
    * the per-loan ``loan_refund`` Asset ledger gets ``+excess``.

    The per-loan ledger accounts are lazily resolved (created on first use,
    reused after) via
    :func:`app.services.ledger_account_service.get_or_create_loan_ledger_account`,
    keyed only when their amount is non-zero.  The legs sum to zero by
    construction.  An all-principal payment (``interest == escrow == excess ==
    0``) yields an EMPTY target: the loan already nets to principal from the
    cash leg, so no correction is owed.

    Args:
        outcome: The payment's :class:`~app.services.loan_ledger.PaymentOutcome`
            -- a RECORDED payment's, whose ``event.source`` is the to-side
            :class:`~app.services.transfer_legs.TransferLeg` of its settled
            transfer (the walk this writer books from carries no projection:
            :func:`~app.services.loan_ledger.walk_loan_ledger`).

    Returns:
        ``{ledger_account_id: (amount, posting_kind_id)}`` for the non-zero
        legs (empty when no correction is owed).

    Raises:
        PostingError: If the loan account has no linked ledger account (a broken
            chart-of-accounts pairing).
    """
    leg = outcome.source
    # The TRANSFER's owner and the leg's account -- the loan -- read through the
    # leg (plan step balance:X-bi-6-4b).  It read the loan-side shadow's own
    # owner column from plan step ``pay_calendar:C13-b`` until then, which
    # stated the parent's owner since ``C13-a``; before C13-b it walked
    # ``shadow.pay_period.user_id``.
    owner_id = leg.user_id
    loan_account_id = leg.account_id
    target: LegMap = {}

    # The loan-linked leg backs the non-principal cash out of the loan; its
    # magnitude mirrors the interest + escrow + refund legs, so the four sum to
    # zero and the loan nets to the real principal.
    loan_leg = -(outcome.interest + outcome.escrow + outcome.excess)
    if loan_leg != 0:
        loan_linked = _ledger_account_for(loan_account_id)
        target[loan_linked.id] = (
            loan_leg, ref_cache.posting_kind_id(PostingKindEnum.PRINCIPAL),
        )
    for ledger_kind, posting_kind, attr in _LOAN_CORRECTION_COMPONENTS:
        amount: Decimal = getattr(outcome, attr)
        if amount != 0:
            ledger = ledger_account_service.get_or_create_loan_ledger_account(
                owner_id, loan_account_id, ledger_kind,
            )
            target[ledger.id] = (
                amount, ref_cache.posting_kind_id(posting_kind),
            )
    return target


def payment_split_targets(
    splits: list[PaymentOutcome],
) -> dict[CorrectionKey, LegMap]:
    """Merge a loan's payment splits into per-(kind, period, day) targets.

    The split half of the loan's correction target (ruling **R-BAL102**): every
    settled outcome's legs (:func:`_loan_payment_target`) summed into the key
    ``(loan_payment source id, the payment's pay_period_id, the payment's
    visible day)`` through
    :func:`app.services._posting_reconcile.merge_target_legs` -- the same
    merge the anchor half applies to two same-day anchors.  The PERIOD is the
    payment's own stored one -- its TRANSFER's, read through the leg since
    plan step balance:X-bi-6-4b (the owner's budgeting choice, ruling
    **pay_calendar:R-PC53**) -- which is what the anchors cannot have and so
    derive; the DAY is the fold's one clock for the payment
    (:attr:`~app.services.loan_ledger.PaymentOutcome.visible_on`, the settled
    day the cash movement's entry carries too), so the split and the cash it
    re-classifies land on one date.

    An all-principal outcome contributes an empty leg map, and that is not
    what reverses a stale correction: the reconcile walks the UNION of the
    target and posted keys, so a key the ledger holds a correction under and
    the walk no longer prices -- a reverted payment, a re-split that fell to
    all-principal, a payment moved to another period or day -- reverses
    whether or not it appears here.  Two payments on one day in one period
    sum here; a second payment inside one accrual period clears nothing fresh
    (plan step X-au-g-2c-3b-2) and contributes nothing.

    Args:
        splits: The loan's settled outcomes from
            :func:`~app.services.loan_ledger.walk_loan_ledger`
            (``settled_splits`` -- the WHOLE list: the walk bounds nothing).

    Returns:
        ``{(source_kind_id, pay_period_id, entry_date): {ledger_account_id:
        (amount, kind_id)}}``; empty when the loan has no settled payment.
    """
    source_id = ref_cache.posting_source_id(PostingSourceEnum.LOAN_PAYMENT)
    target: dict[CorrectionKey, LegMap] = {}
    for outcome in splits:
        key = (source_id, outcome.source.pay_period_id, outcome.visible_on)
        bucket = target.setdefault(key, {})
        merge_target_legs(bucket, _loan_payment_target(outcome))
    return target
