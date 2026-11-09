"""Balance-at-T seam -- a loan's balance at many dates: fold past, projection future.

Plan step **C3a/C3b** (``docs/audits/balance_architecture/README.md``).
:func:`positions` is the ONE total loan balance-at-T producer the seam's AMORTIZING
dispatch reads: the event FOLD
(:func:`app.services.balance_at._fold.fold_from_walk`) over the read pass's
memoized TIMELINE -- the loan's recorded facts and its forward plan in ONE walk
since plan step **recurrence:R16-c-1** (:mod:`._loan_stream`).  The seam's SCALAR
(:func:`app.services.balance_at.balance_at`) and its LIABILITY band
(:func:`app.services.balance_at.liability_owed_at_dates`) read it as of step C3b1;
the per-period map (:func:`app.services.balance_at._inputs._account_balance_map`'s
CONFIGURED-LOAN branch) reads :func:`positions_period_map` as of step C3b3, so every
loan balance surface -- point, band, and map -- now answers from this one
producer.

**The past reads the FOLD, not the postings -- that is the cutover's heart.**
Before this the seam's past balance was a sum of POSTINGS (the genesis reader
``confirmed_loan_balance_at``, since DELETED at plan step E1e).  Here it is the
fold over the loan's SOURCE events, which step B2 proves equal to the postings
on every day (``tests/test_services/test_loan_fold_oracle.py``): the postings
become a checked projection of the fold (asserted at write time from plan step
E1a until plan task plan#3093), not the answer to "what do I owe".  A loan
whose POSTING ledger is missing (a cache miss) still folds correctly from its
source facts, so the read is no longer an outage when the cache is cold -- it
is a repairable inconsistency (B-8).

**The future is the same fold, over the plan's outcomes behind the facts
(step C6b, merged at recurrence:R16-c-1).**  The loan's
:func:`~app.services.balance_at._plan.loan_plan` -- its projected payment RECORDS
at their LIVE cash, then what its definitions would generate beyond the record
horizon -- is appended to the recorded stream as projections and replayed once
from the origination, rather than from a seed a separate fold resolved.  An
overdue installment with NO settled record does not pay the loan down (finding
B-9, killed here), and a projected payment folds its LIVE cash, so the loan
balance and the checking side move together.  The timeline is memoized on the
read pass (:func:`~._loan_stream.loan_timeline`) so one replay serves every
date and every producer that reads it.

**Why here, and not in the ``loan_ledger`` leaf.**  Section 3's end-state has the
loan ledger answering a date on its own, but the plan half composes the
resolver, the escrow lines, the projected shadows and their live cash
(:func:`~app.services.balance_at._plan.loan_plan`) -- all above the pure leaf.
Composing them is a SEAM responsibility, not a leaf one; the leaf stays pure.
(An earlier note here said this producer "can move to ``loan_ledger``" once the
seed went fold-native -- step D2a made it fold-native,
and the note was WRONG: the D0b ruling is that a balance producer moves deeper
INTO the seam, never out to a public leaf, where it would need the very fence
Phase D deletes.)

**Proven equal before it was wired.**  C3a shipped this ADDITIVE, with an oracle
that parallel-ran it against the scalar it replaced on EVERY day past and future,
so C3b's cutover moved no money by proof, not hope (plan Section 7.2).  That oracle
retired with the cutover (the scalar IS this now); the ongoing every-day guarantee
is B2's fold-vs-reader oracle (``test_loan_fold_oracle.py``) plus the seam's own
tests.

Boundary discipline (``CLAUDE.md``): no Flask symbol, no writes; all money is
:class:`~decimal.Decimal`.
"""

from collections import OrderedDict
from datetime import date
from decimal import Decimal

from app.models.account import Account

from app.services.loan_ledger import PaymentOutcome

from ._context import BalanceContext
from ._memoize import _memoize_once, require_scenario
from ._fold import fold_from_walk
from ._loan_stream import loan_timeline, what_if_timeline
from ._plan_fold import is_retired, required_extra, timeline_payoff_date
from ._resolution import resolved_loan

ZERO_MONEY = Decimal("0.00")


def window_sample_date(start_date: date, end_date: date, as_of: date) -> date:
    """Return the date that values a ``[start, end]`` window as of *as_of*.

    The begun/future sampling rule shared by every :func:`positions` caller that
    values a WINDOW rather than a point -- the per-period map
    (:func:`positions_period_map`) and the property equity chart's per-month debt
    line (:func:`app.services.balance_at.secured_loan_series`).  A window that has
    BEGUN (its start on or before *as_of*) is valued at ``min(end_date, as_of)``:
    its end for a fully-past window, *as_of* for the CURRENT one -- so the current
    window reads today's FOLD, not a projected end (the clamp C3b2 proved
    load-bearing).  A FUTURE window (start after *as_of*) is valued at ``end_date``,
    the projected balance at its close.  ONE home, so the map and the chart cannot
    drift on the boundary that clamp rests on.

    Args:
        start_date: The window's start (its ``period.start_date`` / month first).
        end_date: The window's end (its ``period.end_date`` / month end).
        as_of: The read pass's as-of.

    Returns:
        The date to sample :func:`positions` at for this window.
    """
    if start_date <= as_of:
        return min(end_date, as_of)
    return end_date


def _owed_from(account: Account, ctx: BalanceContext, caller: str) -> date:
    """Return the loan's origination, refusing unless *account* is a configured loan.

    The entry guard every producer in this module shares, so none restates the
    "is this a configured loan" refusal, and the one date the retired
    predicate needs beside the walk (:func:`._plan_fold.is_retired`).  It
    replaced ``_forward_schedule`` / ``_forward_seed`` at plan step
    recurrence:R16-c-1, which handed back the resolver bundle's
    ``projection_seed`` -- a balance the timeline no longer starts from --
    beside the same refusal.

    Args:
        account: The amortizing loan account.
        ctx: The read pass's :class:`~app.services.balance_at.BalanceContext`.
        caller: The public function's name, for the fail-loud message.

    Returns:
        The loan's ``origination_date``.

    Raises:
        BaselineMissingError: When ``scenario`` is None (a ``ValueError``
            subclass, ruling R-BW).
        ValueError: When *account* is not a configured loan.
    """
    require_scenario(ctx)
    resolved = resolved_loan(account, ctx)
    if resolved is None:
        raise ValueError(
            f"{caller}() requires a configured loan; account {account.id} "
            f"({account.name!r}) has no LoanParams. The seam's AMORTIZING "
            f"dispatch degrades a non-loan account to the cash producer "
            f"(the cash fold) before reaching here."
        )
    return resolved.params.origination_date


def _owed_from_gated(
    sampled: dict[date, Decimal], owed_from: date,
) -> dict[date, Decimal]:
    """Return *sampled* with every date before *owed_from* answering ``0.00``.

    The one statement of "a loan owes nothing before it exists", applied to the
    timeline's fold by :func:`positions` and :func:`loan_what_if_owed_at_dates`
    -- on every date, past or future (see :func:`positions` for the past half's
    change).  The fold itself is total and clock-free -- it answers a date before the
    opening assertion from whatever is visible by then, which is the honest fold
    of the events -- but a payment settled ahead of the loan's closing (ruling
    R-C permits one whose installment falls after origination) is visible before
    the opening is, and the balance a screen shows for a loan not yet taken out
    is ``0.00``, never that payment's principal as a credit.

    Args:
        sampled: :func:`~._fold.fold_from_walk`'s answer per date.
        owed_from: The loan's ``origination_date``.

    Returns:
        The same map, gated.
    """
    return {
        on_date: (ZERO_MONEY if on_date < owed_from else balance)
        for on_date, balance in sampled.items()
    }


def positions(
    account: Account, ctx: BalanceContext, dates: list[date],
) -> dict[date, Decimal]:
    """Return *account*'s loan balance at each of *dates* -- ONE fold over ONE timeline.

    The total loan balance-at-T producer (see the module docstring), applied to
    the whole date list so N dates cost one replay, not N.  Since plan step
    **recurrence:R16-c-1** there is no past/future dispatch here: the read
    pass's memoized TIMELINE (:func:`~._loan_stream.loan_timeline`) holds the
    loan's recorded facts and its forward plan in one walk, and
    :func:`~._fold.fold_from_walk` prefix-sums its dated deltas -- each fact from
    its settled day, each projection from its effective day (``max(due, as_of +
    1d)``, ruling D1) -- and reads every requested date off the one running
    total.  **A date before the origination owes ``0.00``**
    (:func:`_owed_from_gated`): a loan owes nothing before it exists, and the
    gate is a RULE rather than the empty prefix, because a payment can settle
    before the loan opens -- an installment paid ahead of closing is a write
    ruling R-C allows, and ruling R-EL bounds its settle day by the pay
    schedule, not the origination -- and its principal is then visible before
    the opening assertion is.  The retired forward fold applied this gate
    (``_sample_from_steps``) to the dates it answered; **the retired past
    branch applied none**, so on an ORIGINATED loan a date before the
    origination with such a payment visible read that payment's principal as a
    negative balance (the posted ledger, which books the entry at its settled
    day, still does).  The one fold gates every date, which is the rule's own
    statement and the one place the merge answers a date differently from
    the two folds it replaced: disclosed at recurrence:R16-c-1, unreachable on
    production (no live loan has a payment settled before its origination), and
    the door that admits such a settle day is the structural fix.

    **What this replaced was two folds meeting at the read day**: the settled
    fold from ``0.00`` for a date at or before ``ctx.as_of``, and the plan fold
    from that fold's balance (``projection_seed``) for a date after it, with a
    set-subtraction keeping their charge calendars apart.  The two are one list
    now, and the past half of it is byte-for-byte the posted ledger's own walk
    (:func:`~app.services.loan_ledger.projection_boundary`).

    **Loan-only, and fails loud otherwise.**  A non-configured account (no
    :class:`~app.models.loan_params.LoanParams`) is the seam dispatch's
    cash-degrade case, resolved before this is reached, so an account the pass
    cannot resolve is a caller error here rather than a silent wrong answer.

    Args:
        account: The amortizing loan account (the caller owns the ownership
            check).
        ctx: The read pass's :class:`~app.services.balance_at.BalanceContext`
            -- its scenario scopes the walk and the plan; its ``as_of`` is the
            plan's clamp floor and the projections' past/future boundary.
        dates: The calendar dates to value the loan at, in any order.  Duplicates
            collapse.

    Returns:
        ``{date: Decimal balance owed}`` -- one cent-quantized balance per distinct
        requested date.  ``{}`` for an empty *dates*.

    Raises:
        BaselineMissingError: When ``scenario`` is None.  A ``ValueError``
            subclass; ONE application-level handler answers it (plan step
            X-v2, ruling R-BW), so no caller pre-checks.
        ValueError: When *account* is not a configured loan
            (the seam degrades a non-loan to the cash producer before reaching
            here).
    """
    return _owed_from_gated(
        fold_from_walk(loan_timeline(account, ctx), dates),
        _owed_from(account, ctx, "positions"),
    )


def positions_period_map(
    account: Account, ctx: BalanceContext,
) -> "OrderedDict[int, Decimal]":
    """Return a loan's per-period balance map, built from :func:`positions`.

    The per-period form of :func:`positions`, read by the seam's loan map
    dispatch (:func:`app.services.balance_at._inputs._account_balance_map`) as of step
    C3b3.  It reproduces the genesis per-period read switch the kernel's retired
    ``_build_amortizing_balance_map`` ran -- ledger past, projection future -- but
    from the ONE total loan producer instead of the sum-of-postings map plus a
    splice, so the scalar, the map, and the liability band all answer a loan from
    :func:`positions` and cannot disagree.

    **The sampling rule reproduces the retired splice's boundary.**  That splice
    (the deleted ``splice_confirmed_and_projected_loan_balances``) keyed on
    ``period.start_date <= ctx.as_of``: a BEGUN period read the confirmed ledger at
    its END, a FUTURE period the forward projection at its end.  This samples
    :func:`positions` at the date that reproduces each side:

    * **A BEGUN period (``period.start_date <= ctx.as_of``): valued at**
      ``min(period.end_date, ctx.as_of)``.  For a period that ENDED by the NOW the
      clamp is a no-op (``period.end_date``), and :func:`positions` folds the ledger
      there -- equal to the kernel's period-END-keyed confirmed balance (step B2
      proves the fold equals the sum-of-postings reader on every day).  For the
      CURRENT period (begun, but ending after the NOW) the clamp is ``ctx.as_of``,
      and that clamp is load-bearing: sampling at ``period.end_date`` would hand the
      current period to the forward PROJECTION (its end is after the NOW), moving it
      by any payment scheduled between the NOW and period end -- where the confirmed
      ledger holds today's balance flat to period end.  The clamp keeps the current
      period on the fold, matching the confirmed map.  (Under the one clock no
      confirmed posting is dated after today, so for the production read pass -- whose
      ``ctx.as_of`` is always today -- the confirmed map's period-END value for the
      current period IS its balance-at-today; the clamp reproduces it exactly.)
    * **A FUTURE period (``period.start_date > ctx.as_of``): valued at**
      ``period.end_date``.  :func:`positions` answers it from the forward PLAN fold
      (step C6b) at that date, so a future period reflects the loan's projected
      payment records and contractual synthesis rather than the resolver's
      contractual schedule walk the retired kernel forward map ran period-END-keyed.

    A not-yet-originated loan folds nothing: :func:`positions` routes every date
    (begun periods clamp to a date before ``owed_from``) to the projection's
    origination gate, which returns ``0.00`` before the loan exists -- exactly the
    kernel map's true-zero confirmed side for such a loan.

    **Equal to the retired ``_build_amortizing_balance_map`` for the production
    read pass, with one behaviour change adopted at the cutover.**  For a
    configured loan with an intact posting ledger this returns the same map the
    kernel produced (proven every-period by the C3b2 oracle, which retired with the
    cutover since the map it graded against is now this producer).  The one place
    it differs is a BROKEN loan (originated, no OPENING posting): this folds the
    loan's SOURCE facts and answers, where the kernel map RAISED for it -- the same
    E1 repairable-cache decision the scalar already took at step C3b1 (a cold
    posting cache is a repairable inconsistency, not a read-time outage).

    Args:
        account: The amortizing loan account (the caller owns the ownership check).
            Loan-only, and fails loud otherwise: a non-configured account (no
            :class:`~app.models.loan_params.LoanParams`) is the seam dispatch's
            cash-degrade case, resolved before this is reached, so
            :func:`positions` raises for it.
        ctx: The read pass's :class:`~app.services.balance_at.BalanceContext`
            -- its ``as_of`` is the resolver's NOW and the begun/future boundary
            (the SAME ``ctx.as_of`` the kernel map splices on), and whose
            ``reported_periods()`` is the map's domain since plan step C2-c --
            each period's bounds DERIVED from the owner's paydays rather than
            read off the two stored columns plan step C4-c dropped.

    Returns:
        ``OrderedDict`` period_id -> cent-quantized ``Decimal`` balance, in
        payday order.  ``OrderedDict()`` for an owner with no pay periods.

    Raises:
        BaselineMissingError: When ``scenario`` is None.  A ``ValueError``
            subclass; ONE application-level handler answers it (plan step
            X-v2, ruling R-BW), so no caller pre-checks.
        ValueError: When *account* is not a configured loan
            (:func:`positions`' own contract).
        PayCalendarError: The owner's paydays cannot define a calendar, which
            since plan step C2-c is reachable from every per-period seam entry
            rather than only from the recurrence pages -- see
            :meth:`~app.services.balance_at.BalanceContext.calendar`, where the
            reporting domain is derived, for the one state that produces it and
            the step that removes it.
    """
    # Fail loud at the entry on a missing baseline, as every public seam entry
    # does -- positions() guards too, but guarding here keeps the contract's
    # failure at the surface the consumer called rather than deep inside the
    # composed producer (the same defensive double-guard balance_at() runs).
    require_scenario(ctx)
    # The date to value each period at, reproducing the splice's begun/future
    # boundary (see the docstring).  positions() collapses duplicate dates, so a
    # boundary period landing on ctx.as_of costs nothing extra.
    window = ctx.reported_periods()
    sample_on: dict[int, date] = {
        period.period_id: window_sample_date(
            period.start_date, period.end_date, ctx.as_of,
        )
        for period in window
    }
    valued = positions(account, ctx, list(sample_on.values()))
    return OrderedDict(
        (period.period_id, valued[sample_on[period.period_id]])
        for period in window
    )


def loan_payoff_date(
    account: Account, ctx: BalanceContext, extra_monthly: Decimal = ZERO_MONEY,
) -> date | None:
    """Return *account*'s DERIVED payoff date -- the date its balance folds to zero.

    The payoff-date sibling of :func:`positions`: it composes the SAME confirmed
    timeline :func:`positions` folds
    (:func:`~._loan_stream.loan_timeline`) and reads the first projected
    outcome whose balance reaches zero
    (:func:`~app.services.balance_at._plan_fold.timeline_payoff_date`), so the
    payoff is the date :func:`positions` shows the balance reaching ``0.00`` -- the
    chip, the equity chart, and the payoff cannot disagree about WHETHER the loan
    clears.  (They can differ on the DATE only in one rare edge: an overdue-but-
    projected installment that itself clears the loan folds at its past DUE date
    here but its future EFFECTIVE date in :func:`positions`; see
    :func:`~app.services.balance_at._plan_fold.timeline_payoff_date`.)  DERIVED, never
    stored: it replaces the persisted-from-a-blind-walk copies
    (``LoanState.payoff_date``, ``RecurrenceRule.end_date``) the arc retires (plan
    step C8).

    It is a FOLD-TO-ZERO, not ``plan[-1].date``: the plan runs PAST the contractual
    payoff (the ESTIMATED tail's extension), so a loan paying extra reaches zero at
    an EARLIER installment, and this returns that earlier date.  **The baseline is
    unmoved for a HEALTHY or OVERPAYING loan** -- the fold reaches zero at or before
    the contractual last installment, the same date the engine's contractual
    projection (``project_forward``) reports.  An UNDERPAYING loan gets a
    slightly-LATER date -- the fold keeps paying the level payment past the
    contractual date until it clears (the extension) -- where ``project_forward``
    forces the contractual date via ``is_last_month`` (a phantom final payment); a
    drift so severe it never clears within the extension folds to ``None``.  The
    drift is what C7's payment-drift warning surfaces.

    ``None`` for a loan already retired (owing nothing at ``ctx.as_of`` -- no
    forward crossing), negative amortization, or an underpayment too severe to clear within
    the post-contractual extension.  The caller reads
    :attr:`~app.services.balance_at.LoanFigures.is_retired` to tell the paid-off
    state (badge it) from the not-yet-cleared ones (recurrence stays indefinite).

    Args:
        account: The amortizing loan account (the caller owns the ownership
            check).
        ctx: The read pass's :class:`~app.services.balance_at.BalanceContext`
            -- its scenario scopes the resolution and plan, and its ``as_of`` is
            the projection's now (the ``max(due, as_of + 1d)`` clamp floor).
        extra_monthly: A HYPOTHETICAL extra per accrual period on top of the
            plan, the pay-off-sooner lever's what-if (plan step R7d-g-3).
            ``0.00`` -- the default, and what every real read passes -- dates
            the plan as it stands; :func:`memoized_payoff` caches only that.

    Returns:
        The DUE date the loan's balance first folds to ``<= 0``, or ``None`` when
        the loan is already retired or never pays off.

    Raises:
        BaselineMissingError: When ``scenario`` is None.  A ``ValueError``
            subclass; ONE application-level handler answers it (plan step
            X-v2, ruling R-BW), so no caller pre-checks.
        ValueError: When *account* is not a configured loan
            (the seam degrades a non-loan to the cash producer before reaching
            here).
    """
    return timeline_payoff_date(
        what_if_timeline(account, ctx, extra_monthly),
        _owed_from(account, ctx, "loan_payoff_date"),
        ctx.as_of,
    )


def memoized_payoff(account: Account, ctx: BalanceContext) -> date | None:
    """Return *account*'s DERIVED payoff for this read pass, deriving it once.

    The seam's ONE funnel for the payoff (the :func:`loan_payoff_date` analog of
    :func:`~app.services.balance_at._plan.memoized_plan`): it fills the read pass's
    per-loan payoff cache (:attr:`~app.services.balance_at.BalanceContext.payoffs`)
    from :func:`loan_payoff_date` through the shared store-once primitive
    (``_memoize._memoize_once``), so the fold-to-zero runs at most once per account
    per pass.  A single ``/savings`` render asks for the payoff twice on one loan
    (the debt tile's :func:`~app.services.balance_at.loan_figures`, and the
    home-equity card's configured-loan test), and the property page asks again per
    secured loan -- one derivation now serves them all.

    **The context receives no deriver (plan step D-ctx-b).**  Like the plan funnel,
    this FILLS a public pass-through cache rather than injecting the derivation into
    a context method: :func:`loan_payoff_date` lives ABOVE the context, which cannot
    import it back without inverting the dependency arrow (finding N-25).

    Args:
        account: The loan account to derive the payoff for.  Must belong to
            ``ctx.user_id`` (the caller owns the ownership check), and must be a
            CONFIGURED loan -- :func:`loan_payoff_date` fails loud otherwise.
        ctx: The read pass's :class:`~app.services.balance_at.BalanceContext`.

    Returns:
        The memoized payoff date, or ``None`` when the loan is already retired or
        never pays off within its plan (the caller reads
        :attr:`~app.services.balance_at.LoanFigures.is_retired` to tell them apart).

    Raises:
        BaselineMissingError: When ``ctx.scenario`` is None.  A ``ValueError``
            subclass; ONE application-level handler answers it (plan step
            X-v2, ruling R-BW), so no caller pre-checks.
        ValueError: When *account* is not a
            configured loan -- on EVERY call (a raising derivation is never cached).
    """
    return _memoize_once(
        ctx, ctx.payoffs, account, lambda: loan_payoff_date(account, ctx),
    )


def loan_installments(
    account: Account, ctx: BalanceContext, extra_monthly: Decimal = ZERO_MONEY,
) -> list[PaymentOutcome]:
    """Return *account*'s forward trajectory, one split per planned payment.

    **The loan page's ONE forward walk since plan step R7d-g-3** (ruling
    **R-R88**): the band chart's projected line, the pay-off-sooner lever's
    preview and savings, this month's allocation bar and the amortization
    table all read the seam's timeline through this -- the SAME memoized
    timeline :func:`loan_payoff_date` and :func:`positions` read, its
    PROJECTED outcomes (:attr:`~app.services.loan_ledger.LoanLedgerWalk
    .projected_splits`), so no loan surface can disagree with the balance
    hero or the payoff chip beside it.  Until that step they read
    ``loan_resolver.compute_payoff_scenarios``' committed slice, a second
    forward walk that priced the months no generated row covered from the
    contract plus ONE picked definition's extra (plan ledger row **D49**).

    Args:
        account: The amortizing loan account (the caller owns the ownership
            check).
        ctx: The read pass's :class:`~app.services.balance_at.BalanceContext`
            -- its scenario scopes the resolution and plan, and its ``as_of``
            is the projection's now.
        extra_monthly: A HYPOTHETICAL extra per accrual period on top of the
            plan, for the lever's what-if
            (:func:`~._loan_stream.what_if_timeline`).  ``0.00`` -- the
            default -- is the plan as it stands.

    Returns:
        The timeline's projected :class:`~app.services.loan_ledger.PaymentOutcome`
        list in walk order, running to the plan's post-contractual extension
        (rows after the payoff carry a zero balance and pay nothing down).  EMPTY for a
        loan already retired (owing nothing at ``ctx.as_of``): its plan's
        payments would fold to refunds against nothing owed, and no surface
        lists a payment a paid-off loan will not make -- the same guard
        :func:`~._plan_fold.timeline_payoff_date` answers ``None`` under.

    Raises:
        BaselineMissingError: When ``scenario`` is None (ruling R-BW).
        ValueError: When *account* is not a configured loan.
    """
    owed_from = _owed_from(account, ctx, "loan_installments")
    walk = what_if_timeline(account, ctx, extra_monthly)
    if is_retired(walk, owed_from, ctx.as_of):
        return []
    return walk.projected_splits


def loan_what_if_owed_at_dates(
    account: Account, ctx: BalanceContext, dates: list[date],
    extra_monthly: Decimal,
) -> dict[date, Decimal]:
    """Return *account*'s FORWARD balance per date under a what-if extra.

    The pay-off-sooner lever's preview line (plan step R7d-g-3): the same
    fold :func:`positions` runs (:func:`~._fold.fold_from_walk` over the
    pass's timeline), replayed with *extra_monthly* added once per accrual
    period behind the projection boundary
    (:func:`~._loan_stream.what_if_timeline`).  With ``0.00`` it is
    :func:`positions`' own answer on those dates, which is what makes the
    preview and the projected line one walk apart by exactly the extra and
    nothing else.

    Args:
        account: The amortizing loan account (the caller owns the ownership
            check).
        ctx: The read pass's :class:`~app.services.balance_at.BalanceContext`.
        dates: FORWARD dates -- every one after ``ctx.as_of``, since a past
            balance is the ledger's and no what-if changes it.
        extra_monthly: The hypothetical extra per accrual period.

    Returns:
        ``{date: balance owed}`` -- one cent-quantized ``Decimal`` per date.

    Raises:
        ValueError: When a date is not after ``ctx.as_of``, when ``scenario``
            is None, or when *account* is not a configured loan.
    """
    past = [on_date for on_date in dates if on_date <= ctx.as_of]
    if past:
        raise ValueError(
            f"loan_what_if_owed_at_dates() takes FORWARD dates only; "
            f"{past[0]} is not after the pass's as-of {ctx.as_of}. A past "
            f"balance is the ledger's -- ask positions()."
        )
    return _owed_from_gated(
        fold_from_walk(what_if_timeline(account, ctx, extra_monthly), dates),
        _owed_from(account, ctx, "loan_what_if_owed_at_dates"),
    )


def loan_required_extra(
    account: Account, ctx: BalanceContext, target_date: date,
) -> Decimal | None:
    """Return the extra PER MONTH *account* needs to be clear by *target_date*.

    The target-date calculator's answer (plan step C8f), composed from the SAME
    memoized timeline :func:`loan_payoff_date` reads -- so "when does my plan
    pay this off" and "what would it take to finish by X" are two questions
    asked of ONE model.

    It replaced ``loan_resolver.target_date_outlook``, which binary-searched the
    resolver's contractual schedule walk.  That walk amortizes an installment per
    month whether or not a payment stands behind it (finding B-9), so for a
    delinquent or drifted loan it retired the debt earlier than the fold and could
    answer "no extra needed" for a target the loan does not reach -- on the same
    page as a payoff chip that folds and says otherwise.

    Args:
        account: The amortizing loan account (the caller owns the ownership
            check).
        ctx: The read pass's :class:`~app.services.balance_at.BalanceContext`.
        target_date: The date the user wants the loan retired by.

    Returns:
        ``Decimal("0.00")`` when the current plan already clears the loan by
        *target_date*, the searched per-MONTH extra when one exists (per ACCRUAL
        PERIOD since plan step R16-a, which is the ``/mo`` the panel renders), or
        ``None``
        when the target is unreachable -- no planned payment lands by then (a past
        target, or one before the next installment), or the search exhausted its
        bound (see :func:`~app.services.balance_at._plan_fold.required_extra`).

    Raises:
        ValueError: When ``scenario`` is None, or when *account* is not a
            configured loan (the seam degrades a non-loan to the cash producer
            before reaching here).
    """
    return required_extra(
        loan_timeline(account, ctx),
        _owed_from(account, ctx, "loan_required_extra"),
        ctx.as_of, target_date,
        lambda extra: what_if_timeline(account, ctx, extra),
    )
