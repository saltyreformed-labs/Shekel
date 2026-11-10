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
SAME loop as they are (``loan_posting_service._corrections``) and its entry links no row: not
the loan-side shadow (its key through ``balance:X-bi-6-1b``, a row plan step
``X-bi-6-4d-2`` deleted), not the shadow's covering movement (R-BAL100's key,
which a ``$0.00`` payment does not have -- the seam writes no movement for a
figure of nothing, and the walk still charges the installment), not the
transfer.  A key with no row has nothing to lose: no teardown door owes it a
reversal first, no detector hunts payments that "left the confirmed set" (a
key with no target reverses in the same pass), and no reader maps rows back
onto payments.  Two payments on one day in one period merge into one entry,
which is the sum the ledger holds; WHICH payment paid what is the walk's
answer (``loan_walk_reads``), never the ledger's.

**This module builds the TARGET and nothing else**, for the stored ledger's
writer and the worked-out read alike; it was ``loan_posting_service._payments``
until plan task plan#2526 moved it beside the read.  The reconcile -- what is
posted, the delta, the emission -- is ``loan_posting_service._corrections``' one loop over the
anchor and split targets together, on the shared primitives in
:mod:`app.services._posting_reconcile`.

**Flask-isolated**: plain data in, plain values out; never imports
``request`` / ``session``.  Names each leg by its chart KEY and touches no
stored chart row (plan task plan#2524): the reconcile loop resolves the keys,
minting a per-loan row on first use, at its emit step.
"""

from decimal import Decimal

from app import ref_cache
from app.enums import (
    LedgerAccountKindEnum,
    PostingKindEnum,
    PostingSourceEnum,
)
from app.models.account import Account
from app.services.loan_ledger import PaymentOutcome

from ._chart import linked_key, loan_key
from ._targets import ChartLegMap, CorrectionKey, merge_target_legs

_ZERO_MONEY = Decimal("0.00")

# The three per-loan correction components, each a tuple of (the per-loan ledger
# account KIND to resolve, the posting-leg KIND to tag the leg, the
# :class:`~app.services.loan_ledger.PaymentOutcome` attribute holding the leg's
# amount -- its flat read-through properties).  The loan-linked principal leg
# is handled separately -- it books onto the loan's existing Asset/Liability
# account mirror (the ``linked`` ledger), not a per-loan account -- and is
# summed from the legs this table emits (finding plan#2972), so this table is
# the one list of what a split moves off the loan: adding a component is a
# one-line change, and dropping one cannot unbalance the entry.
_LOAN_CORRECTION_COMPONENTS = (
    (LedgerAccountKindEnum.LOAN_INTEREST, PostingKindEnum.INTEREST, "interest"),
    (LedgerAccountKindEnum.LOAN_ESCROW, PostingKindEnum.ESCROW, "escrow"),
    (LedgerAccountKindEnum.LOAN_REFUND, PostingKindEnum.REFUND, "excess"),
)


def _loan_payment_target(outcome: PaymentOutcome, loan: Account) -> ChartLegMap:
    """Build the target ledger legs for one payment's real-split correction.

    Maps the split to ``{chart key: (signed amount, posting_kind_id)}``,
    dropping any zero component so no empty per-loan ledger account is minted and
    no zero leg is written:

    * the per-loan ``loan_interest`` Expense ledger gets ``+interest``;
    * the per-loan ``loan_escrow`` Expense ledger gets ``+escrow``;
    * the per-loan ``loan_refund`` Asset ledger gets ``+excess``;
    * the loan's LINKED ledger (the Asset/Liability mirror the cash movement
      dumped the whole cash onto) gets the negative of those legs' sum,
      ``-(interest + escrow + excess)``, tagged ``principal`` -- so the loan's
      NET across the cash leg and this correction is exactly ``principal``
      (plan Section 1).

    Each leg is named by its :class:`~app.services.ledger_lines.ChartKey` (the
    loan's own line, or one of its component lines) and only when its amount
    is non-zero, so the writer mints a per-loan row only for a component that
    books (:func:`app.services._posting_write.resolve_chart_targets`, plan task
    plan#2524; until that task this resolved and minted the rows as it went).
    An all-principal payment (``interest == escrow == excess == 0``) yields an
    EMPTY target: the loan already nets to principal from the cash leg, so no
    correction is owed.

    **The legs sum to zero by construction, because the loan's leg is summed
    from the component legs as built** (finding plan#2972).  It was a second
    spelling of them, ``-(outcome.interest + outcome.escrow +
    outcome.excess)`` beside the component table, so a component dropped from
    one and not the other left the entry unbalanced, and only the stored
    ledger's writer refused it (``_posting_write._emit_balanced_entry``, with
    the ``ck_account_postings_balanced`` trigger behind it), which the
    worked-out read never passes through.  Now a dropped component books
    a balanced but WRONG split, the loan keeping the part no leg carries off
    it, and that is a wrong loan balance: the loan's figure tests grade it
    (``test_payoff_overpayment`` in ``tests/test_services/
    test_loan_fold_oracle.py`` for the excess).

    Args:
        outcome: The payment's :class:`~app.services.loan_ledger.PaymentOutcome`
            -- a RECORDED payment's, whose ``event.source`` is the to-side
            :class:`~app.services.transfer_legs.TransferLeg` of its settled
            transfer (the walk this writer books from carries no projection:
            :func:`~app.services.loan_ledger.walk_loan_ledger`).
        loan: The loan the walk is of, with ``account_type`` loaded, which is
            the account every one of its payments' legs is on.  It was read
            off each payment's leg (``leg.account_id``) until plan task
            plan#2524, when the loan's own line's key came to need the
            account's type, which a leg does not carry.

    Returns:
        ``{chart key: (amount, posting_kind_id)}`` for the non-zero legs
        (empty when no correction is owed).
    """
    components: ChartLegMap = {}
    for ledger_kind, posting_kind, attr in _LOAN_CORRECTION_COMPONENTS:
        amount: Decimal = getattr(outcome, attr)
        if amount != 0:
            components[loan_key(ledger_kind, loan.id)] = (
                amount, ref_cache.posting_kind_id(posting_kind),
            )

    # The loan-linked leg backs the non-principal cash out of the loan: what
    # the component legs above carry, read off them rather than off the
    # outcome again, so the entry nets to zero whatever they hold.
    loan_leg = -sum(
        (amount for amount, _kind in components.values()), _ZERO_MONEY,
    )
    if loan_leg == 0:
        return components
    # The loan's own leg first, where it stood before finding plan#2972.
    return {
        linked_key(loan): (
            loan_leg, ref_cache.posting_kind_id(PostingKindEnum.PRINCIPAL),
        ),
        **components,
    }


def payment_split_targets(
    splits: list[PaymentOutcome], loan: Account,
) -> dict[CorrectionKey, ChartLegMap]:
    """Merge a loan's payment splits into per-(kind, period, day) targets.

    The split half of the loan's correction target (ruling **R-BAL102**): every
    settled outcome's legs (:func:`_loan_payment_target`) summed into the key
    ``(loan_payment source id, the payment's pay_period_id, the payment's
    visible day)`` through
    :func:`._targets.merge_target_legs` -- the same
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
        loan: The loan the walk is of, with ``account_type`` loaded
            (:func:`_loan_payment_target`).

    Returns:
        ``{(source_kind_id, pay_period_id, entry_date): {chart key: (amount,
        kind_id)}}``; empty when the loan has no settled payment.
    """
    source_id = ref_cache.posting_source_id(PostingSourceEnum.LOAN_PAYMENT)
    target: dict[CorrectionKey, ChartLegMap] = {}
    for outcome in splits:
        key = (source_id, outcome.source.pay_period_id, outcome.visible_on)
        bucket = target.setdefault(key, {})
        merge_target_legs(bucket, _loan_payment_target(outcome, loan))
    return target
