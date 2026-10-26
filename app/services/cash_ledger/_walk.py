"""The cash walk: ONE ordered traversal of an account's cash FACTS.

The single chronological event stream a cash account's balance derives from, and
the exact counterpart of :mod:`app.services.loan_ledger._walk`.  It yields two
lists -- one :class:`~._events.CashSourceFact` per settled row and one
:class:`~._events.CashAnchorFact` per balance assertion, each in its own walk
order -- and NOTHING else.

**The walk yields FACTS, and since plan step X-f3c-1 that is literally rather
than nearly true.**  Its output is a :class:`CashLedgerWalk`: the account's
data, re-keyed onto the civil day each event counts from and valued in
ledger-native sign.  Turning those facts into "what is the balance on date D" is
the FOLD -- collect each event's dated delta (:func:`dated_deltas`), prefix-sum,
sample -- and the prefix-sum lives in the balance seam, not here.  A consumer
holding a walk therefore cannot reach a balance from a public leaf name, which
is why the walk needs no call fence (plan step D-fold's ruling, restated for
cash).

**What an ASSERTION does to a running balance is not stated here, and that is
the step's point.**  This module replayed the assertions itself until plan step
X-f3c-1: it seeded at zero and, per assertion in business-date order, advanced
by everything that assertion cleared, recorded the correction, then RESET the
running balance to the asserted value.  That reset is a POLICY, and the policy
is not the same for every account -- ruling **R-FO** keeps it for the modelled
kinds (an IRA has no record of a price movement to discard, so the assertion is
the only fact and the reset IS mark-to-market) and plan step X-f3c deletes it
for the PLAIN ones (where the records are the fact and an assertion is a CHECK).
A walk that consulted the account's kind would break ruling **R-J**, and a walk
that hardcoded one kind's answer -- which is what it did -- silently imposed it
on the other.  So the replay moved to
:mod:`app.services.balance_at._assertions`, beside the running total it is a
policy ABOUT, and each fold applies the policy its own kind needs.

**Takes no as-of, and reads no clock.**  Its output is a function of the
account's data ALONE, which is what makes it re-derivable; deciding which facts
have HAPPENED as of a date belongs to a reader.  PLANNED (still-Projected) rows
are not in it at all: their effective date depends on the reader's as-of (ruling
R-G), so they are the seam fold's tier, exactly as the loan plan's PLANNED tier
lives in ``balance_at._plan`` and not in ``loan_ledger``.

**Two consumers, one walk** (ruling R-H).  The seam's read pass folds it into a
balance at a date; at plan step X-d the posting writer projects the same facts
into the balanced corrections it reconciles onto the general ledger, replacing
the postings-sourced
:func:`app.services.account_posting_service.walk_account_ledger`.
Today those are two independent statements of what happened to an account -- one
period-granular over transaction rows, one instant-granular over the postings it
is correcting -- and their disagreement IS the defect Phase X exists to close
(findings cash D1-D4).  One walk closes it by construction rather than by a test
holding two implementations in step.

**How that writer reaches the CORRECTIONS is a decision plan step X-f3c-1 left
it**, and it is named in :mod:`app.services.balance_at._assertions` rather than
left to be discovered: W9910 forbids ``account_posting_service`` importing that
private module in every spelling.  It is the same treatment the RESIDUE
question two paragraphs down gets, for the same reason.

**Three differences from the account POSTING walk, all deliberate, all settled at
X-d.**  (1) This walk reads SOURCE rows where that one reads back the postings;
that is the direction the whole arc turns on -- the posted ledger is a projection
of the facts, not a second opinion about them (plan Section 1, root cause 2).
(2) It does not refuse an amortizing account.  That refusal is a WRITE concern
(which correction family a loan's anchors book into), not a property of a fact
stream, and the cash-flow seam view deliberately consults no kind -- its balance
must reconcile with the transaction rows rendered beside it, whatever the
account.  What keeps a LOAN out of that view is a gate at the SOURCE, on every
resolver that feeds it: ``resolve_grid_account`` since ruling D4 / plan step A1,
and the analytics resolver (``resolve_analytics_cash_flow_set``;
``resolve_analytics_account`` until plan step CC-4-3) since plan step X-a1,
which closed the calendar door finding N-38 measured open (the Van Loan
rendered at ``$531.94`` against ``$15,663.59`` owed).  So this walk stays total
and kind-blind, the writer keeps
the guard it needs, and no screen reaches a cash-basis loan balance through
either.  (3) It sees no RESIDUE.  The posting walk reads a third source bucket --
entries whose ``transaction_id`` / ``transfer_id`` were SET-NULLed by a hard
delete -- precisely so its running total "stays equal to the live linked total
even if the reverse-before-delete discipline is ever violated".  A source-row
walk cannot see a posting whose source row is gone, by construction, so X-d must
decide whether that defence moves to the checked-projection assert (where a
residue row becomes a LOUD mismatch instead of a silently absorbed one) or is
ceded; it is named here so that decision is made rather than discovered.

Reads the account's rows; no writes, no commit.

Plan step **X-a** (shipped at ``929b3a72``).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from ._amounts import ReconciledThrough
from ._clearing import StatementCoverage, statement_coverage
from ._events import (
    CashAnchorFact,
    CashOpeningFact,
    CashSourceFact,
    account_opening_fact,
    cash_anchor_facts,
    settled_cash_facts,
)


@dataclass(frozen=True)
class CashLedgerWalk:
    """An account's full fact stream for one scenario: sources and assertions.

    The complete output of :func:`walk_cash_ledger`: every settled source fact
    AND every balance assertion the account carries, loaded in one pass so a
    consumer holding a walk holds one account's movements beside that same
    account's statements.

    Carries no as-of, because the walk takes none: it is the account's FACTS,
    whole, and a reader bounds them to a date.  It carries no running balance
    either -- what an assertion DOES to one is a policy of the fold
    (:mod:`app.services.balance_at._assertions`), not a property of the facts.

    Attributes:
        source_facts: One :class:`~._events.CashSourceFact` per settled
            balance-contributing row AND per posted purchase recorded against
            one (ruling **R-FM**, plan step X-f3b), ascending by
            ``(settled_on, entry_id)``.  **None is dated at or
            before :attr:`opening`'s day** -- an opening equity is that day's
            CLOSING balance (ruling **R-HG**, plan step X-f3c-2b), so such a
            row is already inside the level this walk's fold seeds at, and a
            deferrable constraint trigger makes it unstorable rather than
            leaving the fold to screen for it.
        anchor_facts: One :class:`~._events.CashAnchorFact` per assertion the
            account carries, ascending by ``(observed_on, created_at, id)`` --
            BUSINESS date first, so the FIRST is the account's OPENING and the
            LAST is the balance in force.  Both properties below rest on that
            order and neither re-derives it (finding N-133 / R1).
        opening: The account's governing
            :class:`~._events.CashOpeningFact` -- what its books opened with,
            and the level the fold's running total starts from (plan step
            **X-f3c-2a**, ruling **R-GX**).  A FACT like the other two, loaded
            in the same pass, which is what stops a consumer resolving it
            separately from the movements it is the level for.  It is not
            optional: :func:`~._events.account_opening_fact` raises for an
            account carrying no record, because a balance folded from a
            fabricated level is worse than no balance at all.
    """

    source_facts: list[CashSourceFact]
    anchor_facts: list[CashAnchorFact]
    opening: CashOpeningFact

    @property
    def reconciled_through(self) -> ReconciledThrough:
        """Return the coverage boundary the account's LAST assertion establishes.

        **The MODELLED side's boundary, and since plan step X-f3a-1 only
        that.**  The modelled accrual window opens on it (ruling R-L) and the
        modelled contribution feed asks it ``covers`` (ruling R-Z), because a
        payroll contribution and a modelled accrual are not lines anyone can
        tick: there the assertion legitimately outranks the model and the
        question really is "is this payday after the latest assertion".  Ruling
        R-FL says so explicitly and calls it not an exception.

        Every CASH consumer moved onto
        :class:`~app.services.cash_ledger.StatementCoverage` -- the recorded
        clearing fact -- because for a line the bank either did or did not show,
        comparing two of the app's own dates was a guess the bank's own record
        falsified.

        **It reads the LAST element and that is only correct because of where
        the ordering lives.**  ``cash_anchor_facts`` loads its rows
        ``(observed_on, created_at, id)`` ascending -- BUSINESS date first --
        so the last fact is the one ``resolve_anchor`` calls current.
        Re-deriving "the latest" with a ``max()`` here would be a second
        statement of that order, and the two agreed for free only while
        ``observed_on`` was derived from ``created_at``; plan step 2 made the
        column user-supplied and broke that, which is exactly how a
        ``$1,307.66`` true-up once posted to the ledger tagged as the account's
        OPENING.

        Returns:
            The account's :class:`~app.services.cash_ledger.ReconciledThrough`.
            Its ``observed_day`` is ``None`` for an account with no assertion
            history -- production-unreachable (migration ``cfb15e782f86`` plus
            ``account_service.create_account`` guarantee an opening row) -- so
            such an account reconciles nothing rather than raising, because a
            walk of no facts is honestly empty.  A consumer that cannot proceed
            without an assertion refuses on its own ground.
        """
        if not self.anchor_facts:
            return ReconciledThrough(None)
        return self.anchor_facts[-1].reconciled_through

    @property
    def coverage(self) -> StatementCoverage:
        """Return the clearing rule for the account this walk loaded.

        The CASH question -- *which statement showed this line* (ruling
        **R-FL**) -- read off the walk a caller is already holding, so the entry
        reservation pays no second query for it.  Its database twin for a caller
        that holds only an account id is
        :func:`app.services.cash_ledger.coverage_for`, and the two are provably
        equal: both build from the same ``(observed_on, created_at, id)``
        ordering, one from the rows in memory and one from the rows re-read.

        Returns:
            The account's
            :class:`~app.services.cash_ledger.StatementCoverage`.  It clears
            NOTHING for an account with no assertion history -- the same honest
            emptiness :attr:`reconciled_through` answers with a ``None`` day.
        """
        return statement_coverage(self.anchor_facts)


def walk_cash_ledger(account_id: int, scenario_id: int) -> CashLedgerWalk:
    """Return an account's settled movements and balance assertions, in order.

    ONE load of the two fact sets a cash balance is folded from, bound together
    so that a consumer holding a walk holds one account's movements beside that
    same account's statements.

    **It stopped computing a running balance at plan step X-f3c-1.**  It seeded
    at zero and, per assertion, advanced by everything that assertion cleared,
    recorded the correction, then reset the running total to the asserted value.
    Every word of that is the RESET policy, which ruling **R-FO** keeps for the
    modelled account kinds and plan step X-f3c deletes for the PLAIN ones -- so
    stating it in a kind-blind walk (ruling **R-J**) imposed one kind's answer
    on the other.  The replay lives in
    :func:`app.services.balance_at._assertions.assertion_corrections` now, next
    to the running total it is a policy about, and this function returns the
    facts that replay reads.

    **Every assertion is loaded, not just the latest**, and that half of the
    cutover this walk still carries.  The shipping projection read only the
    newest anchor (:func:`app.services.cash_ledger.resolve_anchor`) and carried
    it BACKWARD over the past, so a pre-anchor date read today's balance
    (finding B-18 / cash D3: measured on production 2026-07-25, the scalar
    answers ``$2,932.41`` for 2026-06-03 while the period map omits those 8
    periods entirely).  A fold over every assertion has no such state to invent.

    Reads only (no writes, no commit).

    Args:
        account_id: The account whose facts to load.
        scenario_id: The budget scenario whose settled rows to load.
            Assertions are per-ACCOUNT (``AccountAnchorHistory`` carries no
            scenario), so the same assertions accompany every scenario's own
            rows -- the same split
            :func:`app.services.account_posting_service.walk_account_ledger`
            documents.

    Returns:
        A :class:`CashLedgerWalk` (source facts + assertion facts, both
        chronological).  Both lists are EMPTY when the account carries no
        assertion history -- production-unreachable (migration ``cfb15e782f86``
        plus the account factory guarantee an opening row), and returned rather
        than raised because a walk of no facts is honestly empty; the caller that
        must distinguish "no account" asks the account row, never this emptiness.
    """
    # The OPENING is loaded first and unconditionally (plan step X-f3c-2a).
    # It is the level the fold starts from, so an account missing it has no
    # balance to report -- and unlike the assertion list, an empty answer is
    # not honest here: it would silently re-level every figure on the account
    # to zero.  :func:`~._events.account_opening_fact` raises instead.
    opening = account_opening_fact(account_id)
    anchors = cash_anchor_facts(account_id)
    if not anchors:
        return CashLedgerWalk([], [], opening)
    return CashLedgerWalk(
        settled_cash_facts(account_id, scenario_id), anchors, opening,
    )


def dated_deltas(walk: CashLedgerWalk) -> list[tuple[date, Decimal]]:
    """Return the walk's ``(day, delta)`` steps, ascending by date.

    The bridge from the walk (the account's facts) to the civil day each fact
    COUNTS FROM -- the cash twin of
    :func:`app.services.loan_ledger.dated_deltas`.  It RE-KEYS rather than
    re-values: each :class:`~._events.CashSourceFact` already carries its own
    day (:attr:`~._events.CashSourceFact.settled_on`) and its own signed
    :attr:`~._events.CashSourceFact.delta`, so this list is those two fields
    read off the record rather than a second statement of either.

    **It carries the SOURCE facts alone since plan step X-f3c-1.**  It also
    emitted one step per assertion -- the jump that assertion's RESET booked
    over the records -- which made every caller's step list depend on a policy
    this package no longer states (see :func:`walk_cash_ledger`).  A fold whose
    account kind treats an assertion as a reset asks
    :func:`app.services.balance_at._assertions.assertion_corrections` for those
    steps and merges them onto these; a fold whose kind treats an assertion as a
    CHECK (plan step X-f3c) merges nothing, and this list is the whole recorded
    tier.

    **These are the amounts the posting writer books onto the account's LINKED
    ledger, in the same sign -- NOT their negatives.**  The loan twin says
    "negated" and is right to, because a loan walk tracks OWED against a
    credit-normal liability ledger; cash does not.
    :func:`app.services.cash_ledger.movement_cash_leg` is debit-positive, so
    these steps ARE the linked ledger's movements in one convention, for assets
    and liabilities alike.  Verified against the writer:
    ``_posting_purchases._purchase_target`` books ``movement_cash_leg(txn,
    entry)`` -- this module's :attr:`~._events.CashSourceFact.delta` -- onto
    the linked ledger.
    The NEGATIVES are the counter legs (the category ledger, the anchor-equity
    account).  Getting this backwards is not a cosmetic error: plan step X-d
    wires the writer onto this walk, and a sign flip there still balances every
    entry -- so the trial balance closes and only the balance sheet is upside
    down.

    That equality is why the re-key lives on the LEAF: two consumers, one
    derivation.  A third statement of "which day does this event count from, and
    for how much" is precisely how the fold and the posted ledger drift apart
    (plan step E1a's finding, on the loan side).

    **This list carries no source dated at or before the account's opening, and
    since plan step X-f3c-2b that is a property of the DATA rather than a claim
    about it** (ruling **R-HG**).  An opening equity is the CLOSING balance for
    ``opened_on``, so such a source is already inside the level this fold seeds
    at and emitting it here would count the money twice -- finding **N-378**,
    twelve production rows over five accounts.  The state is now unstorable:
    :func:`app.services.cash_ledger.reject_movement_before_books_open` refuses
    it at the one settle-day writer, and a deferrable constraint trigger over
    both movement tables AND ``budget.account_openings`` refuses it from any
    client, in both directions.  **So there is no filter here, and its absence
    is the point**: a fold that screened its own inputs would be a second
    statement of a rule the database already holds, and the two would drift.
    The fold seeds at the stored opening (plan step **X-f3c-2a**), so a date
    before it reads that level flat rather than a partial sum from zero;
    finding **N-37** asked what a reader should answer there and was RULED
    (**R-I**, 2026-07-25) and archived.

    **The clearing question and this re-key are on ONE granularity, and that is
    ruling R-DH.**  They were not: the replay partitioned on the INSTANT while
    this list keyed the resulting deltas by civil DAY, so a day's total depended
    on sub-day click ordering even though the fold above reads only day
    boundaries.  This function's own prior text stated the split as a design --
    "which settles an assertion already covers turns on order within a day" --
    and that sentence is what cost production ``$4,001.42`` on 2026-07-31 (see
    :class:`app.services.cash_ledger.ReconciledThrough`).  Both halves key on
    the day now, so the collapse below is not merely safe, it is the same rule
    the clearing question applies.

    **Dated FACTS, not a balance-at-T** (the W9909 ruling): each pair says what
    ONE event contributed and when it counts, both readable off the public source
    facts.  Turning them into a balance is the prefix-sum, which is seam-private.

    Args:
        walk: The account's :class:`CashLedgerWalk` (:func:`walk_cash_ledger`).

    Returns:
        ``[(day, delta), ...]`` ascending by day.  Empty for a walk carrying no
        source facts.
    """
    steps = [(fact.settled_on, fact.delta) for fact in walk.source_facts]
    steps.sort(key=lambda step: step[0])
    return steps
