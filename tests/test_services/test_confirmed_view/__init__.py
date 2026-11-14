"""The seam's walk-built CONFIRMED view: guards, shapes, and row economics.

Plan step **E1d-b** (``docs/audits/balance_architecture/README.md``).
:func:`app.services.balance_at.confirmed_view` is the loan resolver's confirmed
SEED -- the balance a loan's recorded events fold to, plus the confirmed schedule
rows the amortization table shows and the forward projection starts from.  It
replaced ``loan_payment_service.confirmed_loan_view``, which read the same view
out of the POSTED ledger, and this file replaced that reader's tests.

**Why the every-day equivalence oracle is gone, and what stands in its place.**
Step E1c built this view ADDITIVELY and parallel-ran it against the posting view
on every day of nine shapes; they agreed byte for byte, which is the proof the
cutover rested on.  Step E1d-b deleted the posting view, so that oracle lost its
counterparty -- and an oracle whose two sides are one implementation proves
nothing (plan Section 7.2).  Every shape is therefore re-anchored HERE on
values computed by hand from the loan's terms, with the arithmetic written into
each docstring: interest = round(balance x annual_rate / 12), principal = cash -
interest - escrow (capped at the balance; the excess is a lender Refund, not
principal), balance -= principal, and the row's P&I split against the governing
period's contractual payment under ``principal + interest == payment +
extra_payment``.  A reader can check every number without running the code.

**The fold is TOTAL, so what the partial posting reader refused, this
answers** -- pinned in ``test_the_guards.py``, not hidden: a what-if scenario with no postings
answers from the loan's anchors rather than falling back to the resolver's
money-blind replay.  (The reader's other refusal, a loan with no opening
posting, finding B-12, is no state at all for a walk that reads no posting.)

And one thing it is BLIND to: a raw transaction typed onto a loan account moves
the posted balance but not the walk (finding N-11).  That shape is forbidden at
every write source by step BG, which is why the fold is complete by
construction; the divergence is demonstrated in ``test_what_the_walk_reads.py``
(``TestRawLoanTransactionIsInvisibleToTheWalk``) so the
guard's value is visible, and the guard's own home is
``test_transaction_guards.py``.

Fixture: the shared ``SPLIT_LOAN`` -- $250,000 originated 2025-01-01 at 6%
(monthly rate 0.005), trued up to $100,000 on 2026-01-10, ``payment_day`` 1.  The
trueup balance differs from origination on purpose: a correct interest figure
proves the walk seeds from the trueup, not from origination.  Its contractual
P&I is round(250000 x 0.005 x 1.005^360 / (1.005^360 - 1)) = **1498.88**, the
threshold every row's ``extra_payment`` is measured against.
"""
