"""Account-anchor genesis walk: the DAY-granular correction producer.

The pure READ/COMPUTE half of the Build-Order Step 5 write side: one
chronological walk (:func:`walk_account_ledger`) replays a NON-loan account's
anchor assertions -- its :class:`~app.models.account.AccountAnchorHistory`
rows -- against the account's settled RECORDS, and produces the balanced
OPENING / TRUE-UP corrections that drive the ledger's running total to each
asserted balance at its assertion moment.  The reconcile / posting of those
corrections lives in the sibling module (:mod:`._anchors`); this module only
reads and computes (no writes, no commit).

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
and this module only frames it as the writer books it -- the account's opening
equity first, then one correction per assertion.  Deleted with the loaders:

* **the RESIDUE bucket** -- linked-ledger entries whose movement link was
  SET-NULLed by a hard delete, read so the walked total stayed equal to the live
  linked total "even if the reverse-before-delete discipline is ever violated".
  A walk over records cannot see a posting whose row is gone, by construction,
  so a residue that does not net to zero is now a difference between the stored
  ledger and the records -- which the parity run of plan step plan#2513 names
  -- rather than one the corrections silently absorb.  Measured on production
  2026-11-01: its four ``purchase`` residue entries (two reversal pairs) net
  ``$0.00`` per ledger account and per (paycheck, day), so no posted true-up
  moves.
* **the dayless-movement refusal** -- a nonzero posted net against a movement
  carrying no ``settled_on``, a leg posted for money never seen to move.  The
  records value such a movement at nothing (it is in flight, ruling
  **R-BAL77**), so no correction can be built on the stray leg; the leg itself
  is the source sync's to reverse, whose target for a dayless movement is
  empty.

The loan analogue is :mod:`app.services.loan_ledger`; loans
never walk here (their anchors are dated ``LoanAnchorEvent`` facts replayed
with amortization, a different mechanism), and :func:`walk_account_ledger`
refuses an amortizing account outright.
"""

from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from app.extensions import db
from app.models.account import Account
from app.services import balance_at
from app.services.account_projection import (
    AccountProjectionKind,
    classify_account,
)
from app.services.cash_ledger import walk_cash_ledger

_ZERO_MONEY = Decimal("0.00")


@dataclass(frozen=True)
class AccountAnchorCorrection:
    """One correction the posted ledger owes: the OPENING, or a true-up.

    The per-correction result of :func:`walk_account_ledger`.  The
    linked-ledger delta is ``target_balance - ledger_before`` (its counter leg
    the negative), so the two sum to zero and the linked ledger's total lands
    exactly on the target.  A correction whose ``ledger_before`` already equals
    its target books nothing (a fresh $0 account mints no entries and no equity
    row).

    **It carries no ``CashAnchorFact`` since plan step X-f3c-2a, and that is
    the point.**  Every field the reconcile needs is now stated ON the
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


def walk_account_ledger(
    account_id: int, scenario_id: int,
) -> list[AccountAnchorCorrection]:
    """Replay a non-loan account's anchors into its genesis corrections.

    The account's OPENING EQUITY first, then one correction per balance
    assertion in business-date order, each carrying the records' running
    total just before it -- the read fold's own replay
    (:func:`app.services.balance_at.assertion_corrections`) over the account's
    facts (:func:`app.services.cash_ledger.walk_cash_ledger`), so the posted
    corrections and the projection are one walk rather than two held in step
    (plan task plan#2525; the module docstring says what the replay over STORED
    postings it replaced had read).  The corrections' cumulative effect plus
    the cleared sources equals each asserted balance as of the day it is the
    closing balance for, and the account's ABSOLUTE ledger total equals the
    latest anchor plus the later source nets.

    **Which assertion absorbs which source is the clearing rule's**, asked once
    inside the replay of
    :class:`~app.services.cash_ledger.StatementCoverage` (ruling R-FL), opening
    and true-up alike (finding N-133 / F1).  It was asked twice -- here over
    the stored postings, and in the fold over the records -- and moving one
    without the other is what would have broken the equality plan step X-a
    established; there is no second asker left to move.

    Why this reproduces the go-forward chokepoint after the fact: at a
    true-up the fresh delta equals ``asserted - the records' total before it``,
    and the assertion history and the records are what both read, so a
    re-walk re-derives the same deltas.  Pre-anchor settles in ANY period are
    absorbed by the opening / true-up deltas (the DAY partition, not a period
    one); later settles ride on top; a source reverted or re-dated after a
    true-up leaves that true-up's records, and the next reconcile posts the
    difference (finding N-310: what a settle-day move owes the corrections is
    what the records now say, by construction).

    Reads only (no writes, no commit).  All-scenario anchors, per-scenario
    sources: anchor history is per-account, so the same facts walk in every
    scenario, against that scenario's own settled rows.

    Args:
        account_id: The non-loan account whose ledger to walk.
        scenario_id: The budget scenario whose settled rows to walk against.

    Returns:
        One :class:`AccountAnchorCorrection` for the opening, then one per
        anchor fact, chronological.  Empty when the account row is missing or
        carries no anchor history (fixture-only states; production accounts
        always have both).

    Raises:
        ValueError: If the account is an amortizing loan.  Loans book their
            anchor corrections through the loan posting package onto their
            per-loan ``equity_opening`` account; walking one here would
            double-book its balance across two correction families (and
            break the loan oracle's bare-``account_id`` ledger helpers).
        RuntimeError: If the account carries no ``AccountOpening`` row
            (:func:`app.services.cash_ledger.account_opening_fact`) -- a
            broken invariant, refused by the walk the read fold refuses it by.
    """
    account = db.session.get(Account, account_id)
    if account is None:
        return []
    if classify_account(account) is AccountProjectionKind.AMORTIZING:
        raise ValueError(
            f"cannot walk account anchors: account id={account_id} is an "
            f"amortizing loan (loans book their anchor corrections through "
            f"the loan posting package, never the account walk)"
        )
    walk = walk_cash_ledger(account_id, scenario_id)
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
