"""Account-anchor genesis walk: the DAY-granular correction producer.

One chronological framing (:func:`account_corrections`) replays a NON-loan
account's anchor assertions -- its
:class:`~app.models.account.AccountAnchorHistory` rows -- against the account's
settled RECORDS, as its :class:`~app.services.cash_ledger.CashLedgerWalk`
holds them, and produces the balanced OPENING / TRUE-UP corrections that drive
the ledger's running total to each asserted balance at its assertion moment.
What each correction books is the sibling rule's (:mod:`._account_anchors`),
which the worked-out read sums; this module only computes (no query, no
write).  It was ``account_posting_service._walk`` until plan task plan#2526
moved it beside that rule.  Its account door, ``walk_account_ledger``, which
loaded the walk itself and refused a loan, was deleted at plan task plan#3155
once the stored ledger's writer, its one caller, had gone: the read frames
each account's walk from the owner's one load, and dispatches a loan to its
loan walk first.

**A RECORDED clearing fact where there is one, and the civil DAY where there is
not** (ruling **R-FL**, plan step X-f3a-1).  A source names the
``account_anchor_history`` row whose statement showed it, and that assertion is
the one that absorbs it.  Where none is recorded, an anchor is the CLOSING
BALANCE for the civil day it was observed on, so it already reflects every
settled movement dated that day or earlier (``apply_anchor_true_up``: "the user
is declaring 'my real checking is now $X' -- every past-dated debit purchase is
already in that number").  Never by pay period -- a period-granular walk would
mis-state the ledger by every pre-true-up settle in the anchor period (the plan
review's CRITICAL-1) -- and never by instant, which decided the question by
which button the user pressed first and cost production ``$4,001.42`` on
2026-07-31 (ruling R-DH,
``docs/audits/balance_architecture/archive/anchor_settle_partition.md``).  The
DAY rule survives as the answer for an UNKNOWN source rather than as the rule,
because the developer's own bank exports measured it: only 33 of 110 matched
movements carry the day the bank posted them.

**One boundary for both anchor kinds**, opening and true-up alike (finding
N-133 / F1).  The OPENING was excepted for one day -- its boundary stepped back
a day so its own day's sources rode on top -- and the exception was reverted
once scored: it made the walk read ``$4,804.00`` for a day production's bank
showed ``$2,746.58``, and it forced the rule to be stated twice by hand, as a
date boundary here and as a sort position in the read fold.

**The replay is the read fold's, reached through the seam's public door, and
it reads the RECORDS** (plan task plan#2525, rule 14).  This walk summed its
own sources from the account's STORED postings -- grouped per movement and per
residue period off the linked ledger -- while the read fold replays the same
assertions over :func:`app.services.cash_ledger.walk_cash_ledger`
(:func:`app.services.balance_at.assertion_corrections`).  Two replays of one
running total, held equal by the clearing rule they shared and by the writer
having posted each movement's leg exactly as the walk values it: agreement by
maintenance, which is what rule 14 forbids.  So the running total is the fold's
and this module only frames it as the ledger books it -- the account's opening
equity first, then one correction per assertion.  Deleted with the loaders:

* **the RESIDUE bucket** -- linked-ledger entries whose movement link was
  SET-NULLed by a hard delete, read so the walked total stayed equal to the live
  linked total "even if the reverse-before-delete discipline is ever violated".
  A walk over records cannot see a posting whose row is gone, by construction,
  so a residue that does not net to zero is now a difference between the stored
  ledger and the records -- which the parity run of plan step plan#2513 names
  -- rather than one the corrections silently absorb (ruling **plan#2572**,
  developer 2026-11-01: no guard and no re-read takes its place; the account's
  own row carries it on top of the asserted balance).  Measured on production
  2026-11-01: its four ``purchase`` residue entries (two reversal pairs) net
  ``$0.00`` per ledger account and per (paycheck, day), so no posted true-up
  moves.
* **the dayless-movement refusal** -- a nonzero posted net against a movement
  carrying no ``settled_on``, a leg posted for money never seen to move.  The
  records value such a movement at nothing (it is in flight, ruling
  **R-BAL77**), so no correction can be built on the stray leg; the leg itself
  was the source sync's to reverse, whose target for a dayless movement was
  empty, until plan task plan#3133 deleted the sync with the store's writers.

The loan analogue is :mod:`app.services.loan_ledger`; loans
never walk here (their anchors are dated ``LoanAnchorEvent`` facts replayed
with amortization, a different mechanism), and the worked-out read hands
this framing no loan's walk (:func:`._worked_out._is_loan`).
"""

from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from app.services import balance_at
from app.services.cash_ledger import CashLedgerWalk

_ZERO_MONEY = Decimal("0.00")


@dataclass(frozen=True)
class AccountAnchorCorrection:
    """One correction the ledger books: the OPENING, or a true-up.

    The per-correction result of :func:`account_corrections`.  The
    linked-ledger delta is ``target_balance - ledger_before`` (its counter leg
    the negative), so the two sum to zero and the linked ledger's total lands
    exactly on the target.  A correction whose ``ledger_before`` already equals
    its target books nothing (a fresh $0 account books no line).

    **It carries no ``CashAnchorFact`` since plan step X-f3c-2a, and that is
    the point.**  Every field the correction rule needs is now stated ON the
    correction, because the FIRST correction is no longer an assertion at all:
    it is the account's stored OPENING EQUITY
    (``budget.account_openings``), which has an ``opened_on`` and an amount but
    no assertion behind it.  Reading ``correction.anchor.is_opening`` was how
    the posting path decided which correction opened the books, and that read
    was positional -- whichever assertion sorted first -- so a BACK-DATED
    assertion silently re-elected the opening AND re-dated its journal entry.
    The loan side has never had that problem for the same reason this no longer
    does: its origination is SYNTHESIZED from a stored fact
    (``loan_loaders.synthesize_origination_anchor``) rather than elected from
    the assertion series.

    Attributes:
        observed_on: The civil day this correction's journal entry is dated --
            the assertion's own ``observed_on``, or the opening's ``opened_on``.
        target_balance: What the linked ledger must read once this correction
            posts -- the asserted balance, or the opening equity.
        ledger_before: The records' running total JUST BEFORE this correction
            -- the read fold's
            :attr:`~app.services.balance_at.CashAnchorCorrection.balance_before`
            for an assertion, the opening equity plus every source this
            assertion or an earlier one cleared on top of the prior
            assertions' resets (plan task plan#2525).  ``Decimal("0.00")`` for
            the opening, which by definition precedes every posting.
        opens_the_books: ``True`` for the ONE correction that books the
            account's opening equity, ``False`` for every balance assertion.
            A stored fact read off ``budget.account_openings``, not a position
            in a list: it selects the ``account_opening`` journal source and
            the ``anchor_equity`` counter, where an assertion selects
            ``account_trueup`` and (for a modelled account, ruling R-FO)
            ``interest_income`` or ``unrealized_change``.
    """

    observed_on: date
    target_balance: Decimal
    ledger_before: Decimal
    opens_the_books: bool


def account_corrections(walk: CashLedgerWalk) -> list[AccountAnchorCorrection]:
    """Return a non-loan account's corrections, framed off its walk.

    The framing of the read fold's replay, over a walk the caller already
    holds: the opening equity first, then one correction per assertion
    carrying the records' running total just before it
    (:func:`app.services.balance_at.assertion_corrections`).  The
    corrections' cumulative effect plus the cleared sources equals each
    asserted balance as of the day it is the closing balance for, and which
    assertion absorbs which source is the clearing rule's, asked once inside
    that replay (:class:`~app.services.cash_ledger.StatementCoverage`, ruling
    R-FL).  An assertion is per-ACCOUNT and a source per-scenario, so the same
    assertions frame every scenario's own walk.  The worked-out read hands it
    each account's walk from the owner's one load
    (:func:`app.services.cash_ledger.owner_cash_records`, ruling
    **plan#2623**), so it walks no account a second time.  Pure: no query.

    Args:
        walk: A non-loan account's :class:`~app.services.cash_ledger.CashLedgerWalk`.
            Which kind of account walks here is the caller's dispatch: the
            worked-out read walks a loan through its loan walk
            (:func:`._worked_out._is_loan`), and this framing is kind-blind.

    Returns:
        The :class:`AccountAnchorCorrection` list, chronological; empty for a
        walk carrying no assertion.
    """
    if not walk.anchor_facts:
        return []
    # The OPENING EQUITY is the first correction and it is not an assertion
    # (plan step X-f3c-2a, ruling R-GX): what the account held before its
    # records begin, read from ``budget.account_openings``.  Booking it here --
    # rather than letting the earliest assertion's delta come out equal to it --
    # is what gives the quantity ONE definition, and the replay below seeds its
    # running total at the same stored figure.
    opening = walk.opening
    return [
        AccountAnchorCorrection(
            observed_on=opening.opened_on,
            target_balance=opening.opening_equity,
            ledger_before=_ZERO_MONEY,
            opens_the_books=True,
        ),
        *(
            AccountAnchorCorrection(
                observed_on=correction.observed_on,
                target_balance=correction.anchor.anchor_balance,
                ledger_before=correction.balance_before,
                opens_the_books=False,
            )
            for correction in balance_at.assertion_corrections(walk)
        ),
    ]
