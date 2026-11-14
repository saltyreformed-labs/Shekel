"""Balance-at-T seam -- a loan's ONE timeline: its recorded facts, then its plan.

Plan step **recurrence:R16-c-1** (rulings **R-R90** and **R-R72**).  A loan's
balance is a fold over its event stream, and until this step the seam folded
that stream TWICE: the settled walk from ``0.00`` over the recorded facts
(:func:`~app.services.loan_ledger.walk_loan_ledger`), and the forward plan from
a SEED -- the walk's balance at the read day -- over the projected rows and
estimated occurrences (``_plan_fold._split_plan``).  Two event lists, two seeds,
two output types, and a set-subtraction (``seed_slots``) keeping the two charge
calendars apart.  Each of those was one value with two homes.

This module builds the ONE stream: the recorded facts the pass has SEEN --
:meth:`~app.services.balance_at.BalanceContext.loan_walk` replays the facts
visible by the pass's ``as_of``, the spec's mark where recorded fact becomes
projection -- then the plan's payments as PROJECTIONS behind them
(:attr:`~app.services.loan_ledger.LoanEventStream.projections`), replayed once
by the leaf's :func:`~app.services.loan_ledger.replay_loan_stream` from the one
seed every loan has, its origination assertion.  The result is a
:class:`~app.services.loan_ledger.LoanLedgerWalk` holding the loan's WHOLE
timeline: :func:`~app.services.balance_at._fold.fold_from_walk` values any date
off it, past or future, by one prefix-sum over one dated-delta list; the payoff
is the first projected outcome whose balance reaches zero; the tax figure is
one sum over one list of outcomes; the loan page reads the same outcomes.

**What the merge changes, and what it does not.**  A projected payment is never
placed before a recorded fact (:func:`~app.services.loan_ledger.projection_boundary`),
so the fact prefix of this walk is the pass's facts walk exactly -- and, for a
pass whose ``as_of`` is on or after every recorded fact (every production
pass), :func:`~app.services.loan_ledger.walk_loan_ledger`'s output exactly, so
the posted ledger -- which replays the facts and nothing else -- agrees with
every screen on every settled payment by construction.  **ONE calendar charges
both** since plan step recurrence:R16-c-2 (ruling **R-R100**): every
contractual installment from origination, through the stream's last event
(:func:`~app.services.loan_ledger.with_contract_charges`).  The contract
terms are the SAME object in both -- the facts walk carries the calendar it
was charged on, the plan reads it off that walk rather than building its own
(:func:`~._plan.loan_plan`), and this module charges the timeline from it --
so the facts' charges are a prefix of the timeline's by construction (one
calendar, one pure function, a later last event), and the plan's own charge
list and the ``seed_slots`` partition that kept it apart from the facts' are
deleted.

**The CONTRACT line is the same facts with a third cash source** (plan step
recurrence:R16-f, ruling **R-R53**): every installment the contract still
bills after the record's last event, at its level P&I and escrow, the term's
last closing the loan (:func:`~app.services.loan_ledger.contract_projections`),
in place of the plan's payments (:func:`contract_line`).  It is composed HERE,
beside the plan's timeline, because the facts it extends are the pass's, and
the leaf builds a loan's calendar through ``loan_resolver.resolve_periods``,
so the resolver's composer, which the contract line replaces, could not reach
the replay without an import cycle.  The contract from ORIGINATION is the same
line over the opening alone, read on the origination day
(:func:`origination_contract_line`).

Boundary discipline (``CLAUDE.md``): no Flask symbol, no writes; all money is
:class:`~decimal.Decimal`.  Seam-PRIVATE -- W9910 refuses an import of it from
outside :mod:`app.services.balance_at`.
"""

from collections.abc import Sequence
from dataclasses import replace
from datetime import date
from decimal import Decimal

from app.models.account import Account
from app.services.loan_ledger import (
    LoanCashEvent,
    LoanEventStream,
    LoanLedgerWalk,
    contract_projections,
    replay_loan_stream,
    with_contract_charges,
)

from ._context import BalanceContext
from ._memoize import _memoize_once
from ._plan import LoanForwardPlan, PlannedPayment, memoized_plan

_ZERO_MONEY = Decimal("0.00")


def merged_stream(
    facts: LoanEventStream, plan: LoanForwardPlan,
) -> LoanEventStream:
    """Return *facts* with *plan*'s payments appended as the projection, charged once.

    The ONE composition of a loan's past and future, pure.  The plan's payments
    become :class:`~app.services.loan_ledger.LoanCashEvent` projections dated at
    their installment and visible on their effective day (``max(due, as_of +
    1d)``, ruling D1), in the forward fold's own ``(due_date, effective_date)``
    order, which the replay's stable sort preserves.  The whole stream is then
    charged by the leaf's one composer from the contract terms the plan carries
    (:func:`~app.services.loan_ledger.with_contract_charges`, ruling
    **R-R100**) -- the facts walk's own calendar, which :func:`~._plan.loan_plan`
    reads off it: every installment from origination through the stream's
    last event, of which the facts' own charges -- the same installments,
    from the same calendar object, through the last recorded fact -- are the
    prefix.  The replay walks the
    projections behind the recorded facts
    (:func:`~app.services.loan_ledger.projection_boundary`), so a month skipped
    before the loan's last recorded fact is a RECORDED month: that fact's
    payment clears it, as the posted ledger does (plan step
    recurrence:R16-c-2), and the overdue catch-up pays what stands after it.
    Nothing here re-dates an event: a charge's date is the installment's
    identity, and the loan page renders it.

    Args:
        facts: The loan's recorded stream as the pass sees it
            (:attr:`~app.services.loan_ledger.LoanLedgerWalk.stream` of the
            pass's memoized walk -- the facts visible by its ``as_of``, ruling
            R-R91).
        plan: The loan's :func:`~._plan.loan_plan` forward model.

    Returns:
        One :class:`~app.services.loan_ledger.LoanEventStream`: the facts'
        payments and resets, the plan's payments as projections, and the
        calendar's charges through the last event of all of them.  *facts*
        itself when the plan has no calendar (an account that is not a
        configured loan, whose recorded stream is empty too).
    """
    if plan.calendar is None:
        return facts
    return with_contract_charges(
        replace(facts, projections=projection_events(plan.payments)),
        plan.calendar,
    )


def projection_events(
    payments: Sequence[PlannedPayment],
) -> list[LoanCashEvent]:
    """Return *payments* as the stream's PROJECTIONS, in the forward fold's order.

    The one mapping from a plan's payment record to the replay's cash event:
    dated at its installment (``due_date``, contract time), visible on its
    effective day (``max(due, as_of + 1d)``, ruling D1), the record itself
    carried as the ``source``, ordered ``(due_date, effective_date)`` -- the
    order the replay's stable sort preserves within a date.  :func:`merged_stream`
    appends these to a loan's recorded facts; the hand-computed forward oracles
    (``tests/oracles/loan_forward_fold``) append them to a hand-stated stream,
    so the two cannot map a payment two ways.

    Args:
        payments: The plan's :class:`~._plan_records.PlannedPayment` records,
            in any order.

    Returns:
        One :class:`~app.services.loan_ledger.LoanCashEvent` per payment.
    """
    return [
        LoanCashEvent(
            on_date=payment.due_date,
            cash=payment.cash,
            source=payment,
            visible_on=payment.effective_date,
        )
        for payment in sorted(
            payments,
            key=lambda record: (record.due_date, record.effective_date),
        )
    ]


def contract_line(facts: LoanEventStream, as_of: date) -> LoanLedgerWalk:
    """Return the loan's CONTRACT line: its facts, then each installment it still owes as billed.

    What the loan is charged and pays if every installment the contract
    still bills is paid as the contract bills it: *facts* with the contract's
    remaining installments as its projections
    (:func:`~app.services.loan_ledger.contract_projections`), charged by the
    leaf's one composer from the calendar the facts were charged on
    (:func:`~app.services.loan_ledger.with_contract_charges`, ruling
    **R-R100**) and replayed once
    (:func:`~app.services.loan_ledger.replay_loan_stream`).  The fact prefix
    is the pass's facts walk exactly, as the plan's timeline's is
    (:func:`merged_stream`), so the contract line, the plan and the history
    agree on every recorded payment by construction, and the term's last
    installment closes the loan on whatever balance the walk reached.  An
    installment due before the read that nothing paid counts from the day
    after it and splits as its installment would (ruling plan#4029).

    Made-up figures (ruling plan#4029's): $3,000.00 at 5% over three months
    from Jan 15, nothing paid, read on Mar 20.  Feb 15's $1,008.34 and Mar
    15's both count from Mar 21, paying $12.50 and $8.35 of interest; Apr 15
    closes it for $1,008.35, $25.03 of interest in all.

    Args:
        facts: The loan's recorded stream as the pass sees it
            (:attr:`~app.services.loan_ledger.LoanLedgerWalk.stream` of the
            pass's memoized walk).
        as_of: The read pass's as-of.

    Returns:
        The contract line's :class:`~app.services.loan_ledger.LoanLedgerWalk`
        -- the facts' outcomes, then the contract's as projected outcomes.
        The facts replayed as they stand when they carry no calendar (an
        account that is not a configured loan, whose stream is empty), as
        :func:`merged_stream` answers it.
    """
    calendar = facts.calendar
    if calendar is None:
        return replay_loan_stream(facts)
    return replay_loan_stream(with_contract_charges(
        replace(
            facts, projections=contract_projections(facts, calendar, as_of),
        ),
        calendar,
    ))


def origination_contract_line(facts: LoanEventStream) -> LoanLedgerWalk:
    """Return the loan's contract FROM ORIGINATION: its opening, then every installment as billed.

    The contract the loan's terms state on its origination day, whatever its
    record says since: :func:`contract_line` over *facts*' opening assertion
    alone, read as of the origination, so every installment from the first
    counts on its own due date (none is due before the read) and the term's
    last closes the loan.  It is what the loan would owe on any date had every
    installment been paid as billed, for a reader that has no record to go on
    (a mid-life import's months before tracking).

    Args:
        facts: The loan's recorded stream; its opening assertion and its
            calendar are read, and nothing else.

    Returns:
        The from-origination contract's
        :class:`~app.services.loan_ledger.LoanLedgerWalk`.
    """
    calendar = facts.calendar
    if calendar is None:
        return replay_loan_stream(facts)
    return contract_line(
        replace(
            facts,
            payments=(),
            resets=[reset for reset in facts.resets if reset.is_opening],
            projections=(),
        ),
        calendar.origination_date,
    )


def loan_timeline(account: Account, ctx: BalanceContext) -> LoanLedgerWalk:
    """Return *account*'s whole timeline for this read pass, replayed at most once.

    The seam's ONE funnel for a loan's merged walk: the pass's memoized facts
    (:meth:`~app.services.balance_at.BalanceContext.loan_walk`) and its memoized
    plan (:func:`~._plan.memoized_plan`) composed by :func:`merged_stream` and
    replayed by :func:`~app.services.loan_ledger.replay_loan_stream`, stored on
    the pass so the balance, the payoff, the required extra, the projected
    interest and the loan page all read one replay.

    **The facts are replayed twice per pass, once alone and once here, and that
    is the dependency order rather than waste.**  The plan needs the loan's
    resolution, the resolution seeds the schedule composer from the confirmed
    view, and the confirmed view is built from the FACTS walk -- so the facts
    must be walked before the plan can exist.  The two replays share one stream
    prefix and one rule, and the fact outcomes of this walk equal the pass's
    facts walk's by construction
    (:func:`~app.services.loan_ledger.projection_boundary`); what this costs is
    one replay of the settled payments, not a second load.

    Args:
        account: The loan account.  Must belong to ``ctx.user_id`` (the caller
            owns the ownership check) and be a configured loan -- the plan
            funnel resolves it, and an unconfigured account's plan is empty, so
            its timeline is its facts walk (also empty).
        ctx: The read pass's :class:`~app.services.balance_at.BalanceContext`.

    Returns:
        The pass's memoized :class:`~app.services.loan_ledger.LoanLedgerWalk`
        holding the loan's facts and its projections.

    Raises:
        BaselineMissingError: When ``ctx.scenario`` is None (from the plan
            funnel, on every call; a raising derivation is never cached).
    """
    # Pylint: ``protected-access`` -- the loan twin of ``_cash_fold.cash_fold``'s
    # crossing, and the same design rather than a shortcut: this module owns
    # the timeline's derivation (it needs the plan, which sits above the
    # context) and the context owns per-pass storage.  The field is PRIVATE
    # because a merged walk carries balance-at-T -- ``dated_deltas`` over it,
    # prefix-summed, is ``positions()``'s own answer -- and Python has no
    # package-private, so the alternatives are a public cache no gate can see
    # or a disable at every reading call site.  One, here, named.
    cache = ctx._timelines  # pylint: disable=protected-access
    return _memoize_once(
        ctx, cache, account,
        lambda: replay_loan_stream(merged_stream(
            ctx.loan_walk(account).stream, memoized_plan(account, ctx),
        )),
    )


def what_if_timeline(
    account: Account, ctx: BalanceContext, extra_per_period: Decimal,
) -> LoanLedgerWalk:
    """Return *account*'s timeline replayed with a hypothetical per-period extra.

    The pay-off-sooner lever's preview and the target-date search: the SAME
    stream :func:`loan_timeline` replayed, with *extra_per_period* joining the
    cash at every charge on or after the projection boundary (the replay's own
    rule: money not yet paid never reprices a recorded month).  ``0.00`` is the memo
    itself, so a caller that passes the default replays nothing.

    Args:
        account: The loan account (as for :func:`loan_timeline`).
        ctx: The read pass's :class:`~app.services.balance_at.BalanceContext`.
        extra_per_period: The hypothetical extra per accrual period.

    Returns:
        A :class:`~app.services.loan_ledger.LoanLedgerWalk` -- the pass's memo
        for ``0.00``, a fresh replay of the same stream otherwise.
    """
    base = loan_timeline(account, ctx)
    if extra_per_period == _ZERO_MONEY:
        return base
    return replay_loan_stream(base.stream, extra_per_period=extra_per_period)
