"""Balance-at-T seam -- a loan's CONFIRMED view, folded from the walk.

Plan steps **E1c** (built) and **E1d-b** (cut over)
(``docs/audits/balance_architecture/README.md``).  A loan's confirmed state --
the balance it owes today and the CONFIRMED schedule rows the amortization table
and the forward projection seed from -- used to have one producer,
``loan_payment_service.confirmed_loan_view``, which read it out of the POSTED
ledger (a cache of journal entries).  This module builds the SAME
:class:`~app.services.loan_resolver.ConfirmedLedgerView` from the event WALK
instead -- the balance from the fold (:func:`._fold.fold_from_walk`) and the
history rows re-derived from the walk's per-payment splits and per-anchor
corrections -- so the confirmed view no longer depends on the posting cache
being warm.  Step E1d-b made it the production seed and DELETED the posting
view; step E1e deleted the two posting balance readers behind it.

**Why this completes the read switch (plan Section 3).**  The seam's balance
SCALAR, per-period MAP, and LIABILITY band already fold the walk (steps C3b1/C3b3);
the confirmed VIEW that seeds the RESOLVER was the last surface still reading the
partial posting readers.  Building it from the walk is what lets those readers
come off the production path: ``confirmed_loan_history_rows`` was DELETED with
the view that composed it, and ``confirmed_loan_balance_at`` /
``confirmed_loan_balance_map`` -- left with no caller in ``app/`` at all -- were
DELETED at plan step E1e, their oracle window moving to the test suite.  It also
lets a loan with a cold posting cache
seed its schedule from source facts rather than fall back to the money-blind
anchor replay (finding B-12).

**Byte-equal to the reader it replaced, proven before the cutover.**  The row
builder reproduced the posting reader's ``confirmed_loan_history_rows``
exactly: each row's ``principal`` / ``interest`` equal the posted linked /
interest nets (the checked-projection invariant plan step E1a asserts at write
time), and its ``remaining_balance`` is the SAME contract-order running sum over
the SAME visible event set the reader walked.  Step E1c's oracle parallel-ran
the two on EVERY DAY of nine shapes and they agreed byte for byte; step E1d-b
retired that oracle with its counterparty and re-anchored every shape on
HAND-COMPUTED values instead (``tests/test_services/test_confirmed_view.py``),
so the rows are pinned by arithmetic rather than by a second implementation.

**Two DELIBERATE divergences from the reader, both documented not hidden.**  The
fold is TOTAL, so it answers where the partial reader returned ``None`` -- a
BROKEN loan (originated, no opening posting: a cold cache or a what-if never
posted into) folds from SOURCE facts (finding B-12, the same repairable-cache
decision the scalar and map took at steps C3b1 / C3b3) -- and it is blind to a
raw transaction typed onto a loan account (finding N-11, forbidden at source by
step BG) where the posting reader was not.  Both are pinned in the test file.

Boundary discipline (``CLAUDE.md``): no Flask symbol, no writes; all money is
:class:`~decimal.Decimal`.
"""

from datetime import date
from decimal import Decimal

from app.models.account import Account
from app.services.installment_calendar import installment_paid_by
from app.services.loan_ledger import LoanLedgerWalk, latest_recorded_on
from app.services.loan_resolver import ConfirmedLedgerView
from app.services.amortization_engine import AmortizationRow
from app.services.rate_period_engine import (
    ConfirmedRowInputs,
    confirmed_amortization_row,
)
from app.utils.money import round_money

from ._context import BalanceContext
from ._fold import fold_from_walk

_ZERO_MONEY = Decimal("0.00")

# Contract-order tie tags: a PAYMENT sorts before an ANCHOR on a shared date, so a
# payment due exactly on an anchor's date is subsumed by that anchor's reset -- the
# SAME tie-break the walk applies (``loan_ledger.replay_loan_events``, which
# orders charge -> payment -> reset within a date).  Charges are absent here: a
# confirmed row reads the split its payment already carries, never re-accrues.
_TAG_PAYMENT = 0
_TAG_ANCHOR = 1


def _origination_date(walk: LoanLedgerWalk) -> date | None:
    """Return the loan's origination date off *walk*, or None if it has no facts.

    The walk carries the loan's own opening as a fact: a configured loan ALWAYS
    has exactly one ``is_opening`` anchor, synthesized from the immutable
    :class:`~app.models.loan_params.LoanParams`
    (:func:`~app.services.loan_loaders.synthesize_origination_anchor`), whose
    ``anchor_date`` IS ``origination_date``; an account with no ``LoanParams``
    walks to an EMPTY :class:`~app.services.loan_ledger.LoanLedgerWalk` (the
    leaf's own no-params contract).

    Reading it HERE rather than re-loading the params is what makes the two
    unmismatchable: the date this view numbers its rows from, and the date its
    not-yet-originated guard tests, are the same fact the walk itself replayed --
    one load, one source (plan step E1d-b).

    Args:
        walk: The loan's :class:`~app.services.loan_ledger.LoanLedgerWalk`.

    Returns:
        The origination date, or ``None`` when *walk* carries no opening (the
        account is not a configured loan).
    """
    for correction in walk.anchor_corrections:
        if correction.anchor.is_opening:
            return correction.anchor.anchor_date
    return None


def _history_rows_from_walk(
    walk: LoanLedgerWalk, origination_date: date,
) -> list[AmortizationRow]:
    """Return a loan's CONFIRMED schedule rows, folded from the pass's walk.

    One :class:`~app.services.amortization_engine.AmortizationRow` per settled
    payment the pass has seen (the walk is bounded to the facts visible by its
    ``as_of`` at :meth:`~app.services.balance_at.BalanceContext.loan_walk`),
    chronological, each carrying that payment's ACTUAL economics off the
    walk's split:

    * ``principal`` / ``interest`` -- the split's real principal and accrued
      interest, which the posting writer books verbatim onto the ledger, so they
      equal the posted nets (plan step E1a's checked-projection invariant).
    * ``remaining_balance`` -- the genesis running balance owed AFTER this payment,
      accumulated in CONTRACT order over the walk's events, every one of which
      is visible by the pass's as-of: an
      anchor moves the balance by ``owed_before - anchor_balance`` (the exact linked
      net its posted correction carries), a payment by ``+principal``, and the row
      reads ``-(cumulative)``.  It is NOT the walk's full-timeline balance-after and
      NOT the fold sampled at the payment's visible date: a payment settled in the
      future (not yet visible) but due EARLIER must not reduce a visible row's
      balance.
    * ``payment`` / ``extra_payment`` -- the actual P&I split against the governing
      period's contractual ``period_pi`` under the schedule-row invariant
      ``principal + interest == payment + extra_payment``.
    * ``month`` / ``payment_date`` -- numbered and dated at the installment the
      payment PAYS: the installment its own due date (the split's ``due_date``,
      the walk's ordering key) falls in
      (:func:`~app.services.installment_calendar.installment_paid_by`, ruling
      **R-R109**), so a late-settled payment is dated at the installment it
      paid, never the next, and a payment due Mar 10 on a loan due the 22nd is
      dated and numbered at Feb 22 -- where the schedule prices its escrow and
      the band chart plots it.  The loan's due day is read off the calendar the
      walk was charged on (``walk.stream.calendar``, ruling **R-R100**), its
      origination from *origination_date*, so the row is numbered and placed
      from one origination.
    * ``interest_rate`` -- the split's governing period ``annual_rate`` (the SAME
      period its ``interest`` accrued at, carried on the split -- plan step E1c).

    Event ORDER mirrors the write walk exactly: payments by DUE date, anchors by
    their own date, a payment BEFORE a same-date anchor, ties within a type keeping
    the walk's ``(pay_period.start_date, id)`` order via a stable sort.  A row
    is NAMED by its installment but ORDERED by its due date, because the
    running balance must accumulate in the walk's order; the installment never
    falls as the due date rises, so the rows' dates stay ascending.

    Args:
        walk: The loan's :class:`~app.services.loan_ledger.LoanLedgerWalk` (the read
            pass's memoized walk, :meth:`~app.services.balance_at.BalanceContext.loan_walk`).
        origination_date: The loan's origination (:func:`_origination_date`), which
            numbers the rows and places each on its installment.

    Returns:
        The chronological confirmed :class:`~app.services.amortization_engine.AmortizationRow`
        list (possibly empty for a configured loan with no confirmed payment yet).
    """
    # Every recorded event, tagged for the contract-order sort.  The pass's
    # walk holds only the facts VISIBLE by its as_of since plan step
    # recurrence:R16-c-1 (a payment from its settled date, an anchor from its
    # own date -- the ONE clock, bounded once at
    # ``BalanceContext.loan_walk``), so the per-event ``<= as_of`` tests this
    # comprehension carried are stated there and not again here.
    events: list[tuple[date, int, object]] = [
        (outcome.due_date, _TAG_PAYMENT, outcome)
        for outcome in walk.settled_splits
    ] + [
        (correction.anchor.anchor_date, _TAG_ANCHOR, correction)
        for correction in walk.anchor_corrections
    ]
    events.sort(key=lambda event: (event[0], event[1]))

    # ``linked_sum`` is the cumulative net on the loan's linked ledger; owed is its
    # negation.  It accumulates in the SAME sign and order the posted corrections
    # do, so the row balances and the posted ledger cannot drift.
    linked_sum = _ZERO_MONEY
    rows: list[AmortizationRow] = []
    payment_day = walk.stream.calendar.payment_day
    for _event_date, tag, item in events:
        if tag == _TAG_ANCHOR:
            # The anchor's posted linked net -- the jump its reset booked, in
            # linked-ledger sign (``owed_before - anchor_balance``).  Drives owed
            # from ``owed_before`` to ``anchor_balance`` additively.
            linked_sum += (
                item.owed_before - item.anchor.anchor_balance
            )
            continue
        principal = round_money(item.principal)
        interest = round_money(item.interest)
        linked_sum += principal
        rows.append(confirmed_amortization_row(ConfirmedRowInputs(
            origination_date=origination_date,
            installment=installment_paid_by(
                origination_date, payment_day, item.due_date,
            ),
            principal=principal,
            interest=interest,
            period=item.period,
            # Debit-positive ledger: owed is the negated cumulative linked net,
            # ``0 - sum`` so a zero cumulative reads 0.00, never -0.00.
            remaining_balance=round_money(_ZERO_MONEY - linked_sum),
        )))
    return rows


def confirmed_view(
    account: Account, ctx: BalanceContext,
) -> "ConfirmedLedgerView | None":
    """Return *account*'s genesis-ledger confirmed view, folded from the walk, or None.

    The loan resolver's confirmed SEED (plan step E1d-b): the confirmed balance
    owed as of ``ctx.as_of`` -- the FOLD of the loan's recorded events -- bundled
    with the ledger-derived confirmed schedule rows and the date of the last
    event it folded (:func:`~app.services.loan_ledger.latest_recorded_on`, where
    the contract's remaining installments start after, ruling plan#3382), or
    ``None`` to seed the resolver from the loan's latest assertion instead.  The
    seam's whole-loan read
    (:func:`~app.services.balance_at._resolution.resolve_loan_bundle`) threads it
    into every resolution, and the loan-detail route's what-if composers read it
    for the same seed, so the loan card, the amortization table's confirmed rows,
    the band chart, and the payoff / refinance calculators cannot disagree about
    what has actually been paid.

    Derived on demand, NOT memoized, and that is deliberate: the expensive part
    -- the walk -- IS memoized on the pass
    (:meth:`~app.services.balance_at.BalanceContext.loan_walk`), so a second call
    in one pass costs no query at all, only a prefix-sum and a row re-accumulation
    over facts already in memory.  Caching the result would put a balance-bearing
    value on the publicly re-exported context, which is the one thing its plan /
    payoff caches are careful not to do.

    Returns ``None`` on three guards, and the resolver then seeds the contract's
    remaining installments from the loan's latest assertion, with no history:

    * ``ctx.scenario_id_or_none`` is ``None`` (no baseline scenario to scope the
      walk to -- one of the seam's two degenerate-case readers of the nullable,
      ruling R-BX);
    * *account* is not a configured loan (its walk carries no opening fact, so it
      has no :class:`~app.models.loan_params.LoanParams`);
    * ``ctx.as_of`` PRECEDES the loan's ``origination_date`` -- nothing has happened
      to it yet, and the fold's honest ``0.00`` for an empty prefix must not seed the
      forward projection (outage B-1: it collapsed a 360-row schedule to zero rows
      and held $200,000 flat forever).  The same rule is stated in full on
      :func:`app.services.balance_at.positions`'s not-yet-originated branch.

    **A pass dated after today is answered like any other** (plan step
    recurrence:R16-e, ruling plan#3382).  It returned ``None`` until then, on
    the ground that "the resolver projects it": the resolver's anchor replay
    stepped the payments settled by such a date along the schedule.  That replay
    is deleted, and the walk is already bounded to the facts visible by the
    pass's ``as_of`` (:meth:`~app.services.balance_at.BalanceContext.loan_walk`),
    so the view of a later date is the record as it will stand then; the guard
    was also the view's one read of the process clock.

    **One case the retired posting view returned ``None`` for, this does NOT: a
    BROKEN loan** (originated, no OPENING posting -- a cold cache or a what-if
    never posted into).  The fold answers from the loan's SOURCE facts, so the
    real confirmed view survives a cache the posting reader could not read
    (finding B-12) -- the same repairable-cache decision the scalar and map took
    at steps C3b1 / C3b3.

    Args:
        account: The loan account whose confirmed view to build.  The caller owns
            the ownership check (the loaders trust it).
        ctx: The read pass's :class:`~app.services.balance_at.BalanceContext` --
            its ``scenario`` scopes the walk, and its ``as_of`` is the evaluation
            date (the resolver's NOW).

    Returns:
        The :class:`~app.services.loan_resolver.ConfirmedLedgerView`, or ``None``
        for the resolver to seed from the loan's latest assertion.
    """
    if ctx.scenario_id_or_none is None:
        return None
    walk = ctx.loan_walk(account)
    origination_date = _origination_date(walk)
    if origination_date is None or ctx.as_of < origination_date:
        return None
    return ConfirmedLedgerView(
        balance=fold_from_walk(walk, [ctx.as_of])[ctx.as_of],
        history_rows=_history_rows_from_walk(walk, origination_date),
        # Never None here: the walk holds the loan's opening (the guard just
        # above found it), and an assertion is a recorded fact.
        last_recorded_on=latest_recorded_on(walk.stream),
    )
