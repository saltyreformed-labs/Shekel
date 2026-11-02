"""The loan's ONE correction reconcile: anchors and payment splits, one loop.

A loan's ledger is THREE kinds of balanced correction, all projected from ONE
deterministic running-balance walk
(:func:`app.services.loan_ledger.walk_loan_ledger`) and reconciled here in ONE
pass (plan step ``balance:X-bi-6-3``, ruling **R-BAL102**):

* the once-per-loan OPENING and every user balance TRUE-UP
  (:func:`app.services.ledger_lines.anchor_correction_targets`);
* every confirmed payment's real principal / interest / escrow / refund SPLIT
  (:func:`app.services.ledger_lines.payment_split_targets`).

Each is a DERIVATION -- the walk re-computes it from scratch every sync -- so
each is keyed the way a derivation is keyed, ``(source kind, pay period, entry
date)`` with no row link (:data:`app.services.ledger_lines.CorrectionKey`),
and the three share one "already posted" read
(:func:`app.services._posting_reconcile.posted_correction_legs`, scoped to the
loan's own chart rows) and one delta loop
(:func:`app.services._posting_reconcile.emit_correction_deltas`).  A key
present only in the target posts, only in the ledger reverses, in both adjusts
by the difference; that one shape is every lifecycle a correction has --
a payment settled, reverted, re-dated, moved to another period, re-priced,
deleted or re-pointed at another account; a true-up whose ``owed_before`` a
pre-true-up payment moved; a period boundary that grew under an anchor.

**The split was reconciled by a loop of its own, keyed by the loan-side income
shadow's ``transaction_id``, until this step -- and that key was every fence
the loan package carried.**  A row key must be defended at every door that can
lose the row: the delete door and the endpoint-move arm reversed the split
FIRST (``transfer_service._loan_posting._reverse_loan_payment_before_it_
leaves``, deleted), a detector hunted payments that had "left the confirmed
set" (``_stale_loan_payment_shadows``, deleted), and the payment-history
table read the legs back per shadow to cross-check the writer (``_reader``,
deleted; the table reads the walk).  The key R-BAL100 first chose in its
place, the loan-side covering movement, was measured to not exist for a
``$0.00`` payment the walk still charges.  A key with no row has nothing to
lose, so none of that machinery has a job.

Flushes but never commits -- the caller owns the transaction boundary.
Flask-isolated: plain data in, no ``request`` / ``session``.
"""

from app import ref_cache
from app.enums import PostingSourceEnum
from app.extensions import db
from app.models.account import Account
from app.services._posting_reconcile import (
    emit_correction_deltas,
    filing_calendar_for,
    posted_correction_legs,
)
from app.services.ledger_lines import loan_correction_targets
from app.services.loan_ledger import LoanLedgerWalk


def reconcile_loan_corrections(
    loan_account_id: int, scenario_id: int, walk: LoanLedgerWalk,
) -> None:
    """Reconcile a loan's opening, true-ups AND payment splits to ONE walk.

    Builds the per-``(source kind, pay period, date)`` targets of both halves
    from the walk the caller already holds -- the anchor corrections
    (:func:`app.services.ledger_lines.anchor_correction_targets`) and the settled payment
    splits (:func:`app.services.ledger_lines.payment_split_targets`) -- reads back what is
    posted under the three correction kinds on the loan's OWN chart rows (its
    linked row and its per-loan interest / escrow / refund / opening-equity
    rows: :func:`app.services._posting_reconcile.posted_correction_legs`), and
    emits ONE balanced delta per key that differs
    (:func:`app.services._posting_reconcile.emit_correction_deltas`).  One
    read and one loop for all three kinds, because interest accrues on the
    running balance the anchors reset: a pre-true-up payment change re-splits
    every later payment AND moves the true-up's ``owed_before``, and the two
    must reconcile TOGETHER off the same walk or one goes stale.

    Idempotent and self-healing: a re-run at the same state writes nothing.
    Touches ONLY the loan's own ledgers -- never the cash side (the payment's
    cash leg is the movement's own entry, immutable and correct), so a loan
    sync can never move a cash balance.  An unresolvable loan (no
    :class:`~app.models.loan_params.LoanParams`: an empty walk) or an owner
    that cannot be resolved is a no-op.  Flushes but does not commit.

    Args:
        loan_account_id: The loan whose corrections to reconcile.
        scenario_id: The budget scenario to reconcile within.
        walk: The loan's :class:`~app.services.loan_ledger.LoanLedgerWalk`
            (:func:`~app.services.loan_ledger.walk_loan_ledger`) -- its
            ``anchor_corrections`` and ``settled_splits``, the WHOLE lists:
            the walk bounds nothing and reads no clock, so the ledger it
            projects is the same whenever this runs.

    Raises:
        PayCalendarError: The owner has no MATERIALISED pay period, so an
            anchor correction's ``NOT NULL`` ``pay_period_id`` has nothing to
            point at -- refused by
            :meth:`~app.services.pay_calendar.PayCalendar.filing_period`, the
            ONE place that question is asked (developer ruling 2026-08-10).  A
            payment split carries its payment's own stored period and asks no
            calendar.
        PostingError: A loan account with no linked ledger account (a broken
            chart-of-accounts pairing), when a correction books on it, from
            the emit step that resolves the targets' chart keys (plan task
            plan#2524; from either target builder until then).
    """
    if not walk.anchor_corrections and not walk.payment_splits:
        return
    # The SAME door the account twin takes (plan step C2-d), so the period
    # this writer FILES an anchor under and the period that one files an
    # assertion under cannot come from two different rules OR two different
    # loads.  It hands back the OWNER with the calendar, which is what keeps
    # a loan from being resolved against another user's schedule.
    resolved = filing_calendar_for(loan_account_id)
    if resolved is None:
        return
    owner_id, calendar = resolved
    # The loan itself, once, for the key of its own line, whose class is its
    # type's (plan task plan#2524).  Present: the owner just read is its row's.
    # Usually an identity-map hit, since a payment door's transfer holds its
    # accounts joined.  Every leg of both halves is this loan's, and every key
    # resolves to a row of the owner's -- the transfer's owner too, which
    # ``fk_transfers_owner_to_account`` makes the loan's.
    loan = db.session.get(Account, loan_account_id)

    emit_correction_deltas(
        owner_id,
        scenario_id,
        target=loan_correction_targets(walk, loan, calendar),
        posted=posted_correction_legs(
            loan_account_id,
            scenario_id,
            [
                ref_cache.posting_source_id(PostingSourceEnum.LOAN_OPENING),
                ref_cache.posting_source_id(PostingSourceEnum.LOAN_TRUEUP),
                ref_cache.posting_source_id(PostingSourceEnum.LOAN_PAYMENT),
            ],
        ),
    )
