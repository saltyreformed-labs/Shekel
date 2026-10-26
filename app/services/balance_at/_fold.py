"""Balance-at-T seam -- the loan fold: a WALK of facts sampled into a balance.

Plan step **D-fold** (``docs/audits/balance_architecture/README.md``).  "A loan's
balance is a fold over its event stream" (Section 3): this module owns the FOLD --
the balance half -- while the ``loan_ledger`` leaf owns the WALK it samples (the
facts).  The split is the step's whole point: *a fold is a balance; a walk is a
fact.*

The leaf's :func:`app.services.loan_ledger.walk_loan_ledger` replays a loan's
anchors and settled payments into per-payment splits and per-anchor corrections in
CONTRACT-time order -- FACTS, which both the posting writer and the seam need.
Turning those facts into "what is owed on date D" is what this module does, and it
does it the way the whole arc turns on (Section 3):

* re-key each event by the date it became VISIBLE
  (:func:`app.services.loan_ledger.dated_deltas` -- on the LEAF since plan step
  E1a, because the posting writer's checked-projection assert consumes the same
  re-key and two copies of the one clock is how the fold and the ledger would
  drift), then
* prefix-sum the paydowns and read each requested date off the running total
  (:func:`sample_cumulative`) -- ``0.00`` for a date before any event, the honest
  fold of an empty prefix.

**Why the fold is in the seam and the walk is not.**  Because it lives here, a
consumer that legitimately holds a :class:`~app.services.loan_ledger.LoanLedgerWalk`
(the writer, the read pass) cannot reach a balance from a public leaf name -- the
only code that turns a walk into money is seam-private.  That is what lets the walk
shed its fence entry (a walk one call from a balance had to be fenced; a walk that is
not, does not).  It WAS the balance-side twin of a forward fold
(``_plan_fold.fold_forward``, seam-private since step C6a) that started from a
seed this fold had sampled; since plan step recurrence:R16-c-1 there is no
second fold -- the loan's timeline carries its projections behind its facts
(:mod:`._loan_stream`) and :func:`fold_from_walk` samples the whole of it, so
the past and the future are one prefix-sum and cannot drift.

**TOTAL and clock-free.**  :func:`fold_loan_balances` refuses no date and no
account: a date before any event, or an account with no
:class:`~app.models.loan_params.LoanParams`, folds to ``0.00``; a future date
answers (holding the last recorded balance flat) rather than raising.  That totality
is the property that deletes the posting readers' partiality -- there is nothing to
compose it with, so no seed / flag / splice / fallback can disagree with it.

Boundary discipline (``CLAUDE.md``): no Flask symbol, no writes; all money is
:class:`~decimal.Decimal`.
"""

from bisect import bisect_right
from datetime import date
from decimal import Decimal

from app.services.loan_ledger import (
    LoanLedgerWalk,
    dated_deltas,
    walk_loan_ledger,
)
from app.utils.money import round_money

_ZERO_MONEY = Decimal("0.00")


def _collapsed_prefix(
    start: Decimal, steps: list[tuple[date, Decimal]],
) -> tuple[list[date], list[Decimal]]:
    """Prefix-sum *steps* from *start*, collapsing steps that SHARE a date.

    The running-balance series both date readers in this module share: the
    sampler (:func:`sample_cumulative`) bisects it, and the backward
    zero-crossing (:func:`last_closed_on`) scans it.  It exists as one function
    because the COLLAPSE is the load-bearing part -- a date carrying several
    events has ONE balance, its combined prefix, so a payoff and a balance
    true-up landing on the same visible date read as that date's net and never
    as two states within it.  Written twice, the sampled balance and the
    crossing date could disagree about whether a loan was closed on such a day.

    Args:
        start: The balance before any step.
        steps: ``(date, delta)`` pairs, ASCENDING by date (the caller sorts).

    Returns:
        ``(boundaries, cumulative_at_boundary)`` -- one entry per DISTINCT date
        in *steps*, ascending, each carrying the running total through the end
        of that date.  Both lists are empty for empty *steps*.  The values are
        NOT quantized; each reader rounds at the point it compares or returns.
    """
    boundaries: list[date] = []
    cumulative_at_boundary: list[Decimal] = []
    running = start
    for on_date, delta in steps:
        running += delta
        if boundaries and boundaries[-1] == on_date:
            cumulative_at_boundary[-1] = running
            continue
        boundaries.append(on_date)
        cumulative_at_boundary.append(running)
    return boundaries, cumulative_at_boundary


def sample_cumulative(
    start: Decimal,
    steps: list[tuple[date, Decimal]],
    dates: list[date],
) -> dict[date, Decimal]:
    """Prefix-sum date-keyed *steps* from *start* and read each of *dates* off it.

    The shared date-sampling core of every running-balance fold: given a base
    value and a list of ``(date, delta)`` steps ALREADY ascending by date, it
    accumulates the running total (collapsing steps that share a date to their
    combined prefix) and answers each requested date with the cumulative at the
    latest step on or before it -- one bisect per date, not a re-sum.  A date
    before every step reads *start* (the empty prefix).

    The loan fold (:func:`fold_from_walk`, seeded at ``0.00`` over a walk's dated
    principal deltas -- the recorded facts alone, or the pass's whole timeline
    with its projections behind them since plan step recurrence:R16-c-1) and
    the cash fold (:mod:`._cash_fold`) sample through here, so no two running
    balances can drift on how they are read at a date.

    Args:
        start: The balance before any step -- ``0.00`` for the from-zero event
            fold, the seed balance for the forward projection.
        steps: ``(date, delta)`` pairs, ASCENDING by date (the caller sorts).
        dates: The dates to value, in any order.  Duplicates collapse.

    Returns:
        ``{date: cent-quantized cumulative}`` -- one per distinct requested date.
    """
    boundaries, cumulative_at_boundary = _collapsed_prefix(start, steps)

    sampled: dict[date, Decimal] = {}
    for on_date in dates:
        # The count of boundaries at or before this date; the last one's prefix is
        # the answer (start when none precede -- the empty prefix).
        count = bisect_right(boundaries, on_date)
        sampled[on_date] = round_money(
            cumulative_at_boundary[count - 1] if count > 0 else start
        )
    return sampled


def fold_from_walk(
    walk: LoanLedgerWalk, dates: list[date],
) -> dict[date, Decimal]:
    """Sample an ALREADY-COMPUTED walk at each of *dates* -- the fold's core.

    The date-sampling half of :func:`fold_loan_balances`, taking the walk as a
    parameter rather than computing it, so a read pass that folds one loan at
    several date lists (the seam's scalar, per-period map, and liability band all
    read :func:`app.services.balance_at.positions`) can walk it ONCE and sample the
    memoized walk here each time -- the redundant-derivation the read pass's
    context exists to kill (:meth:`~app.services.balance_at.BalanceContext.loan_walk`).

    Re-keys each event by its visible-on date (the leaf's
    :func:`app.services.loan_ledger.dated_deltas` -- the ONE statement of the
    clock, shared with the posting writer's checked-projection assert),
    prefix-sums the deltas, and reads each requested date off the cumulative --
    ``0.00`` for a date before any event (the empty prefix's honest fold).
    Identical output to :func:`fold_loan_balances` for the same walk, so
    threading a memoized walk through here moves no balance.

    Args:
        walk: The loan's :class:`~app.services.loan_ledger.LoanLedgerWalk`
            (:func:`app.services.loan_ledger.walk_loan_ledger`).
        dates: The dates to value the loan at, in any order.  Duplicates collapse.

    Returns:
        ``{date: balance owed}`` -- one cent-quantized ``Decimal`` per requested
        date.  ``{}`` for an empty *dates*.
    """
    # The fold's balance seeds at ZERO and the empty prefix (before any event)
    # is 0.00 -- the honest fold of no facts.
    return sample_cumulative(_ZERO_MONEY, dated_deltas(walk), dates)


def last_closed_on(walk: LoanLedgerWalk, as_of: date) -> date | None:
    """Return the date *walk*'s loan LAST became closed at or before *as_of*.

    The BACKWARD zero-crossing, and the half
    :func:`~app.services.balance_at._plan_fold.timeline_payoff_date` structurally
    cannot answer: that one reads the PROJECTED outcomes forward of the read,
    so a loan already at zero has no crossing left ahead of it and returns
    ``None`` --
    which does not mean *never*, it means *not my half* (plan step
    ``recurrence:R7d-h``).  This reads the same rule the other way, over the
    RECORDED events rather than the forward plan, so the two together answer a
    loan's closing date across its whole timeline.

    It scans the SAME collapsed running balance :func:`sample_cumulative` bisects
    (:func:`_collapsed_prefix`) and rounds each date's balance the way
    :func:`fold_from_walk` rounds a sampled one, so the date this returns is a
    date the fold itself reports ``<= 0.00`` on -- the crossing and the balance
    beside it cannot disagree.

    **A loan that closed, was trued back UP, and closed again has TWO crossings,
    and this returns the LATER** (developer, 2026-09-03).  The alternative reads
    a loan's whole reopened span as already finished: with a payoff on
    2026-09-01, a ``$1,200.00`` true-up on 2026-10-15 and a ``$1,200.00`` payment
    clearing it on 2026-11-20, taking the FIRST crossing stops the loan's
    recurrence on 2026-09-01, so the payment that actually settled in November
    has no projected row behind it and generation silently under-produces across
    the span.  The later crossing is also what "the day it last became closed"
    means literally.

    **The zero before a loan exists is not a closed loan.**  A date before any
    recorded fact folds to ``0.00`` -- the empty prefix's honest answer
    (:func:`fold_from_walk`) -- but the loan had not been borrowed yet, so the
    scan treats the state before the first event as OPEN.  Two consequences: a
    loan with no fact by *as_of* answers ``None``, and one whose FIRST visible
    balance is already ``<= 0`` closes on that first date rather than answering
    "never".  The second is reachable through a ``$0.00`` true-up dated ON the
    origination date -- ``anchor_service`` admits ``anchor_date >=
    origination_date``, and the two facts then collapse onto one boundary.  It
    is NOT reachable through a zero OPENING anchor, which is synthesized from
    ``original_principal`` and forbidden by
    ``ck_loan_params_orig_principal`` (``original_principal > 0``).

    Args:
        walk: The loan's :class:`~app.services.loan_ledger.LoanLedgerWalk`
            (:func:`app.services.loan_ledger.walk_loan_ledger`) -- the read
            pass's memoized one where the caller holds a context.
        as_of: The read pass's as-of.  Events visible AFTER it are not read, so
            a loan closed only in the future is not closed here.

    Returns:
        The date the loan last went to a ``<= 0.00`` balance and stayed there
        through *as_of*, or ``None`` when it is not closed at *as_of* (it still
        owes, or it has no recorded fact yet).
    """
    boundaries, cumulative_at_boundary = _collapsed_prefix(
        _ZERO_MONEY, dated_deltas(walk),
    )
    # Only what is VISIBLE by as_of: the count of distinct dates at or before it.
    count = bisect_right(boundaries, as_of)
    if count == 0:
        return None
    became_closed_on: date | None = None
    # The loan does not exist before its first recorded fact, so the state going
    # in is OPEN rather than closed (see the docstring).
    was_open = True
    for index in range(count):
        is_closed = round_money(cumulative_at_boundary[index]) <= _ZERO_MONEY
        if is_closed and was_open:
            became_closed_on = boundaries[index]
        was_open = not is_closed
    return None if was_open else became_closed_on


def fold_loan_balances(
    loan_account_id: int,
    scenario_id: int,
    dates: list[date],
) -> dict[date, Decimal]:
    """Return the loan's folded balance at each of *dates* -- from SOURCE events.

    The fold's read side, and the reference the optimized readers are graded
    against (plan step B2).  ONE walk of the loan's facts
    (:func:`app.services.loan_ledger.walk_loan_ledger`), sampled at every requested
    date (:func:`fold_from_walk`) -- so N dates cost one walk, not N.  A read pass
    that would fold the same loan more than once memoizes the walk on its context
    and calls :func:`fold_from_walk` directly; this entry is the standalone form for
    a caller (B2's oracle) that holds no context.

    **It reads the loan's SOURCE rows and never the postings table.**  That is the
    whole point: the posted ledger is a PROJECTION of this fold, so an answer
    derived independently of it is what can prove the projection faithful.  Where
    the two disagree, the ledger is a stale cache -- a detectable, repairable
    inconsistency -- rather than the outage a missing posting is today.

    **TOTAL over every DATE and every ACCOUNT.**  There is no date it refuses and
    no account it refuses: asked about a date before any event -- or about an
    account with no :class:`~app.models.loan_params.LoanParams` at all -- it
    returns ``0.00``, the correct fold of an empty prefix; asked about a future
    date it answers rather than raising.  This is the property the whole arc turns
    on (plan Section 3): the posting readers' partiality (``None`` for an unopened
    ledger, a RAISE for a future date) is what forces every caller to compose them
    with a projection, a seed, a flag or a fallback, and every composition is a new
    producer that can disagree with the others.  A total function has nothing to
    compose with.  A caller that must distinguish "owed nothing" from "no loan"
    asks the FACT (``origination_date``), never this function's zero.

    That totality is genuine since step C2: an event's visible-on date is its own
    date (an anchor) or its settled date (a payment), neither of which needs the
    owner's calendar, so the fold no longer has a "loan has facts but its owner has
    no pay periods" state to raise on.  It answers from the loan's facts alone.

    **ACTUAL events only.**  It folds what is RECORDED -- the loan's anchors and
    its settled payments -- so it answers the past.  PLANNED payments (the future)
    are the seam's timeline (:func:`~._loan_stream.loan_timeline`, the same
    walk with the plan's projections behind the facts); asked about a future
    date this holds the last recorded balance flat, which is honest for what it
    knows but is NOT the projection the seam shows.  Grade it on the past.

    **N-11 (a raw transaction typed onto a loan) is closed by construction (BG,
    ruling R-E), not a live gap.**  A raw settled transaction typed directly onto a
    loan account would post onto the loan's linked ledger where the sum-of-postings
    readers count it, while the fold -- whose payment set is transfer legs
    only (:func:`~app.services.loan_loaders.settled_income_legs`) -- would not
    see it, so the two would disagree by that transaction's amount ($300.00
    measured).  Ruling R-E FORBIDS that write at every source (the two
    transaction-create routes, the recurrence-template form, and the salary-profile
    picker each refuse an amortizing account -- BG, ``dba91dc0``), so no such row
    can enter the fold's domain; B2 both demonstrates the divergence on a forced
    row and asserts the sources refuse it.  Do NOT read a disagreement in that
    shape as a stale cache to be repaired.

    Reads only -- no writes, no commit.  This standalone entry (walk + sample in
    one call) is B2's parallel-run oracle subject; it DELEGATES to
    :func:`fold_from_walk`, which is the code the seam's AMORTIZING dispatch now
    runs in production (over the read pass's memoized walk, step C3b) -- so the
    oracle grades the exact sampling the user's balance is answered by, not a copy
    of it.

    Args:
        loan_account_id: The loan account to fold.
        scenario_id: The budget scenario the payments live in.
        dates: The dates to value the loan at, in any order.  Duplicates collapse.

    Returns:
        ``{date: balance owed}`` -- one cent-quantized ``Decimal`` per requested
        date.  ``{}`` for an empty *dates*.
    """
    return fold_from_walk(
        walk_loan_ledger(loan_account_id, scenario_id), dates,
    )
