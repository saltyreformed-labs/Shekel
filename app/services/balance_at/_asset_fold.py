"""Balance-at-T seam -- the MODELLED fold: a cash fold plus a modelled return.

Plan step **X-g1** (``docs/audits/balance_architecture/README.md`` Section 3.2).
"A modelled asset is an event stream", and the stream is the CASH one with two
more event kinds on it::

    AssetEvent = (effective_date, kind, payload)

    kind = ASSERTION    balance := asserted_value      (AccountAnchorHistory, every OWNER row)
         | ACTUAL       balance += movement_cash_leg   (dated movements)
         | PLANNED      balance += reservation         (still-projected, clamped, R-G)
         | CONTRIBUTION balance += modelled_rate       (payroll deductions + employer)
         | ACCRUAL      balance += balance * rate      (modelled return, DAILY, R-T)

The first three are exactly :mod:`app.services.balance_at._cash_fold`'s three
tiers, taken whole through :func:`~._cash_fold.assembled_fold`
rather than re-derived -- which is the structural claim this module exists to
make: an INTEREST account, an INVESTMENT and a Property are not three questions,
they are the cash fold plus a rate.  The retired interest layer said exactly
that in its own words ("that module models an INVESTMENT's growth and an
APPRECIATING asset's appreciation on top of their cash bases, and this one models
an INTEREST account's accrual on top of its folded cash balance") years before
there was a shape that could express it; plan step X-g4b deleted it, and this
module is the sentence made structural.

**ACCRUAL is the only MULTIPLICATIVE kind, and that is the whole difference.**
Its delta is a function of the running balance at its own instant, so resolving
it must be sequential (:func:`_resolve`).  Between two events the balance is
constant, so ONE pass over the merged, date-ordered event list resolves every
accrual on the horizon into ordinary dated deltas -- after which
:func:`~app.services.balance_at._fold.sample_cumulative`, the sampler this
module shares with the LOAN fold and the cash fold, is unchanged.  Generalising
that sampler to be balance-dependent would have put this step's blast radius on
the loan side for no gain.

**What it REPLACED, and why the replacement was not a preference** (wired at
plan step X-g2b, and the producers it replaced were deleted at X-g4b).  A
modelled account's map used to be three producers merged by a preference
order -- a forward growth projection, the anchor-forward cash base, and a
REVERSE growth projection.  Measured on the prod-shape clone, that merge
rendered **$6,315.57** of net-worth history contradicting the user's own
recorded balance assertions: the three modelled accounts carry 15 of them and
the map read only the LATEST, re-deriving every earlier period from a model
(findings N-43 / N-74).  A fold has no join, so it has no join rule to get
wrong: every ASSERTION is replayed as a reset, which is what makes the earlier
periods read the numbers the user typed in.

**Four rules decide where the modelled tiers start and what they are worth.**

* **Ruling R-L, generalised at ruling R-Y.**  ACCRUAL exists only on days at or
  after the LATEST balance assertion, and the assertion's OWN day accrues.
  Everything at or before it is a bank fact the user typed in, and modelling
  across those days adds money the assertion already contains.  The retired
  interest layer had done this since plan step X-c2a -- before it, accrual began
  at the anchor PERIOD's start, up to 13 days early, worth $6.77 over 14 days on
  the real Fidelity Savings where the honest window earns $1.45 over 3 -- and
  ruling R-Y extended it to INVESTMENT and APPRECIATING, which skipped the anchor
  PERIOD entirely and so silently dropped up to a full period of return
  (measured: Roth +$105.26, Trad IRA +$44.95, Empower +$76.59, Property +$170.11
  at the anchor period).
* **The DAY-COUNT class stops being representable, and it is recorded here
  because the module that recorded it is gone.**  The retired per-period layer
  had to convert a pay period's INCLUSIVE ``end_date`` into the exclusive right
  boundary its interest formula wanted, and getting that wrong counted 13 days of
  a 14-day period -- understating a HYSA's yield by ~1 day in 14 (~7%), the
  interest-path twin of the growth engine's own day-count defect.  A date-keyed
  daily ACCRUAL has no period boundary to convert, so there is no convention left
  to misstate.
* **Ruling R-S.**  There is no backward direction.  Before the FIRST assertion
  the balance is the account's stored OPENING EQUITY plus its records by then --
  the cash fold's answer, inherited free -- and the reverse growth projection
  leaves the balance path entirely.
* **Ruling R-T.**  ACCRUAL is DAILY.  A step exists for every day, so a sampled
  date never falls inside an unresolved span and the answer never depends on
  which OTHER dates were asked for.
* **Ruling R-X.**  A day's accrual is computed at FULL precision and credited in
  whole cents, carrying the sub-cent remainder
  (:func:`_resolve`).  Every emitted step is therefore an exact cent -- so the
  grid identity stays exact by construction -- while the cumulative accrual at
  every date equals ``round(exact)``.  Rounding each day independently instead
  would make a small balance accrue nothing at all, forever: 0.45 cents a day on
  a $50 HYSA at 3.29% APY rounds to zero every day, and a $20 holding at 10.5%
  would grow $3.65 a year against a true $2.00.

**Ruling R-R partitions a contribution by SOURCE**, which is what makes the two
feeds disjoint by construction rather than by a de-dup rule: a recorded transfer
HAS a transaction row, so it is already an ACTUAL / PLANNED event; a payroll
deduction never has one, so it is a modelled CONTRIBUTION event.  That tier is
:mod:`app.services.balance_at._asset_contributions`, split off here at plan step
X-g2a on plan step D1c's cohesion line; this module asks it for dated events and
states no contribution rule of its own.

**Assembly and resolution are separate entries** (plan step X-g2a).
:func:`resolve` takes an ALREADY-assembled
:class:`~app.services.balance_at._cash_fold.AssembledCashFold`, so a reader that
needs the cash period columns AND the modelled tiers off one account -- the
budget grid, from plan step X-g2b -- pays for ONE walk, ONE plan load and ONE
valuation.  :func:`fold_asset_balances` and :func:`asset_period_view` are the
convenience entries that assemble first, for the readers that want only the
modelled answer.

**TOTAL over every date and every account, like the folds it extends.**  An
account that models no return (an INTEREST account whose params row is absent,
an INVESTMENT with no ``InvestmentParams``) IS its cash fold; a date before every
event reads the seed; a future date answers.  The one place it fails loud is a
modelled account with NO assertion history at all, which has no honest window to
open an accrual on -- the same deliberate asymmetry
:func:`~app.services.cash_ledger.resolve_anchor` already enforces for the
interest path.

Boundary discipline (``CLAUDE.md``): no Flask symbol, no writes; all money is
:class:`~decimal.Decimal`.
"""

from collections import OrderedDict
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal

from app.models.account import Account
from app.services.cash_ledger import ReconciledThrough
from app.services.pay_calendar import PeriodWindow
from app.utils.money import round_money

from . import _asset_contributions, _cash_fold
from ._asset_contributions import ContributionInputs
from ._asset_return import AccrualWindow, modelled_return
from ._context import BalanceContext
from ._fold import sample_cumulative

_ZERO = Decimal("0")
_ZERO_MONEY = Decimal("0.00")
_ONE_DAY = timedelta(days=1)


@dataclass(frozen=True)
class AssetPeriodFigures:
    """One pay period's modelled column: the balance and what moved it.

    The per-period output of :func:`asset_period_view`.  For every period and
    every modelled kind, in terms of :attr:`balance`::

        balance(p.end) - balance(p.start - 1 day)
            == <the cash period view's net + reconciliation>
               + accrual + contribution

    (the boundary form, so the FIRST period is covered too -- it has no
    predecessor to subtract), and it holds BY CONSTRUCTION rather than as an
    invariant a test polices: all four terms are readings of ONE resolved step
    list, and every step is a whole cent (ruling R-X).  The grid renders the
    last two as TWO rows since plan step X-g3a -- ruling R-W wrote one "Growth"
    row and ruling R-AH split it, because a single summed row can report a GAIN
    on an account that lost money (measured at a -10.5% return: -$7,366.83 of
    market beside +$9,624.27 of payroll renders +$2,257.44).

    Attributes:
        balance: The modelled end-of-period balance.
        accrual: The modelled return credited inside this period's span.
        contribution: The modelled contribution credited inside it.

    A fourth field carried the same balance with the ACCRUAL events filtered
    out -- the pre-growth seed ruling R-U had a forward chart compound FROM.
    **Ruling R-AE retired that idea** (plan step X-g2b): with the seed read at a
    DATE strictly before the projection window, the window and the seed's past
    are disjoint, so there is no overlap for a pre-growth basis to correct --
    and filtering the modelled return instead DROPS every cent the account
    earned since its last assertion (up to $292.11 on the real Empower 401(k),
    finding N-80).  A chart seeds from the ordinary balance at a date now, so
    neither that field nor its scalar twin ``asset_seed_at`` has a reader, and
    both went with the ruling rather than surviving as an attractive nuisance.
    """

    balance: Decimal
    accrual: Decimal
    contribution: Decimal


@dataclass(frozen=True)
class ModelledFold:
    """One account's resolved modelled step list, plus what each tier contributed.

    The output of :func:`resolve`.  :attr:`steps` is what
    :func:`~app.services.balance_at._fold.sample_cumulative` reads; the two maps
    beside it are the same deltas kept apart so a reader can report WHY a
    balance moved without re-deriving it -- which is what
    :func:`asset_growth_at` totals.

    Attributes:
        seed: The balance before every step (the cash fold's stored seed).
        steps: The resolved dated deltas, ASCENDING by date -- the cash tiers,
            the contributions and the accruals merged into one running total.
        accrual_by_day: day -> the cent-quantized accrual credited on it.  Days
            crediting nothing are absent.
        contribution_by_day: day -> the modelled contribution landing on it.
    """

    seed: Decimal
    steps: "list[tuple[date, Decimal]]"
    accrual_by_day: "dict[date, Decimal]"
    contribution_by_day: "dict[date, Decimal]"


def _latest_assertion_boundary(
    account: Account, walk: "_cash_fold.CashLedgerWalk",
) -> ReconciledThrough:
    """Return the coverage boundary *account*'s LATEST assertion establishes.

    The boundary ruling R-L's window opens on and ruling R-Z's contribution
    feed asks its coverage question of, read off the WALK the fold was already
    built from rather than through a second
    :func:`~app.services.cash_ledger.resolve_anchor` query.  The two are the same
    row by construction -- the walk's facts are loaded ``(observed_on,
    created_at, id)`` ascending and the resolver takes that exact key
    descending -- which is what makes ruling R-L "one line of the event
    builder" rather than a rule each modelled layer restates.  The shared key
    became BUSINESS-date-first at plan step 2, when ``observed_on`` stopped
    being derived from ``created_at`` and the two orders could differ.

    **It fails loud for an account with no assertion history, and that asymmetry
    is deliberate.**  The cash fold answers such an account from a zero seed
    (the totality rule the whole arc turns on); a modelled layer cannot, because
    an accrual needs a DATE to open its window on and there is no honest window
    without an assertion.  It is the same refusal
    :func:`~app.services.cash_ledger.resolve_anchor` makes, and the same
    unreachable state: migration ``cfb15e782f86`` and
    ``account_service.create_account`` guarantee every account an opening row.

    Args:
        account: The account, named in the failure.
        walk: Its :class:`~app.services.cash_ledger.CashLedgerWalk`.

    Returns:
        The account's :class:`~app.services.cash_ledger.ReconciledThrough`.
        Its ``observed_day`` is never ``None`` here -- the refusal above is
        what guarantees that -- so the accrual window can take it as a raw
        civil day while the contribution feed asks it ``covers``.

    Raises:
        RuntimeError: When the account carries no assertion at all.
    """
    if not walk.anchor_facts:
        raise RuntimeError(
            f"_asset_fold: account id={account.id} models a return but has "
            "zero AccountAnchorHistory rows, so there is no assertion to open "
            "its accrual window on.  Migration cfb15e782f86 plus "
            "account_service.create_account make this state unreachable; "
            "investigate any code path that constructed the Account row "
            "without routing through the canonical factory."
        )
    return walk.reconciled_through


def _resolve(
    cash: _cash_fold.AssembledCashFold,
    contributions: "list[tuple[date, Decimal]]",
    window: "AccrualWindow | None",
) -> ModelledFold:
    """Replay the merged event stream ONCE, resolving each day's accrual in order.

    The sequential pass ACCRUAL forces and the reason it is the only kind that
    needs one: its delta is a function of the running balance at its own
    instant, so it cannot be known before the events before it are applied.
    Everything else -- the cash tiers, the contributions -- is already a dated
    delta, so the pass merges them by day, walks the days in order, and turns
    each day's accrual into an ordinary dated delta.  After it,
    :func:`~app.services.balance_at._fold.sample_cumulative` reads the result
    exactly as it reads the cash and loan folds.

    **A day accrues on the balance it ENDS holding, and that dissolved a fork
    rather than picking a side.**  The day's cash and contribution steps are
    applied first, then the accrual is computed on the result and credited on
    the same day.  The two producers this replaced disagreed about that base --
    the interest layer accrued on the pay period's END balance, the growth
    engine on its START -- so a deposit made mid-period earned a FULL period of
    interest under one rule and none under the other, and no test could pin the
    right one because each was self-consistent.  At the daily grain both
    collapse into "the balance actually held on the day", and there is no
    boundary left for a convention to be wrong about.

    **Ruling R-X's cent carry, in three lines.**  The accrual is accumulated at
    full precision in ``exact``; what is CREDITED is ``round_money(exact)``, and
    each day's step is the change in that.  So every step is a whole cent (the
    property the identity in :class:`AssetPeriodFigures` needs), the cumulative
    accrual at any date equals ``round(exact)`` (no per-day rounding bias), and a
    sub-half-cent daily accrual accumulates into a cent instead of vanishing.

    Args:
        cash: The account's assembled cash fold -- its seed and its three
            tiers' dated deltas.
        contributions: The dated CONTRIBUTION events.
        window: The account's :class:`AccrualWindow`, or ``None`` when it
            models no return (the result is then the cash fold, unchanged).

    Returns:
        The :class:`ModelledFold`.
    """
    by_day, contribution_by_day = _merged_day_deltas(cash, contributions)
    days = set(by_day)
    if window is not None:
        days.update(window.days())
    steps, accrual_by_day = _resolve_days(cash.seed, by_day, days, window)
    return ModelledFold(
        seed=cash.seed,
        steps=steps,
        accrual_by_day=accrual_by_day,
        contribution_by_day=contribution_by_day,
    )


def _merged_day_deltas(
    cash: _cash_fold.AssembledCashFold,
    contributions: "list[tuple[date, Decimal]]",
) -> "tuple[dict[date, Decimal], dict[date, Decimal]]":
    """Collapse the additive tiers onto their days, keeping the contributions apart.

    Args:
        cash: The account's assembled cash fold.
        contributions: The dated CONTRIBUTION events.

    Returns:
        ``(all_by_day, contribution_by_day)`` -- every additive delta summed per
        day, and the CONTRIBUTION half of the same sum kept separately so a
        reader can report what the modelled tier put in without re-deriving it.
    """
    by_day: dict[date, Decimal] = {}
    for on_date, delta in cash.steps:
        by_day[on_date] = by_day.get(on_date, _ZERO_MONEY) + delta
    contribution_by_day: dict[date, Decimal] = {}
    for on_date, amount in contributions:
        by_day[on_date] = by_day.get(on_date, _ZERO_MONEY) + amount
        contribution_by_day[on_date] = (
            contribution_by_day.get(on_date, _ZERO_MONEY) + amount
        )
    return by_day, contribution_by_day


def _resolve_days(
    seed: Decimal,
    by_day: "dict[date, Decimal]",
    days: "set[date]",
    window: "AccrualWindow | None",
) -> "tuple[list[tuple[date, Decimal]], dict[date, Decimal]]":
    """Walk the days in order, turning each one's accrual into a dated delta.

    The sequential half of :func:`_resolve` -- see its docstring for the
    end-of-day base and ruling R-X's cent carry, both of which live in this
    loop.

    Args:
        seed: The balance before every step.
        by_day: Every additive delta summed per day.
        days: Every day to walk -- the additive days plus the accruing ones.
        window: The account's :class:`AccrualWindow`, or ``None``.

    Returns:
        ``(steps, accrual_by_day)`` -- the resolved dated deltas ASCENDING by
        date, and the cent-quantized accrual credited on each day that credited
        one.
    """
    running = seed
    exact = _ZERO
    credited = _ZERO_MONEY
    steps: "list[tuple[date, Decimal]]" = []
    accrual_by_day: dict[date, Decimal] = {}
    for on_date in sorted(days):
        delta = by_day.get(on_date, _ZERO_MONEY)
        running += delta
        if window is not None and window.accrues_on(on_date):
            exact += window.rule.one_day(on_date, running)
            step = round_money(exact) - credited
            if step != _ZERO_MONEY:
                credited += step
                running += step
                delta += step
                accrual_by_day[on_date] = step
        steps.append((on_date, delta))
    return steps, accrual_by_day


def resolve(
    account: Account,
    cash: _cash_fold.AssembledCashFold,
    horizon_end: date,
    inputs: ContributionInputs,
) -> ModelledFold:
    """Resolve *account*'s modelled tiers onto an ALREADY-assembled cash fold.

    The replay's real entry, and the reason a modelled asset needs no cash basis
    of its own: it takes the pass's whole assembled record -- the seed, the
    three tiers' dated deltas, and the walk it reads the latest assertion off --
    and adds the two modelled kinds to it.

    Taking the assembled fold rather than assembling one is what lets a reader
    that ALSO needs the cash period columns share the walk (plan step X-g2a):
    :func:`~app.services.balance_at._cash_periods.period_view_of` regroups the very
    same record.  The two convenience entries below assemble first, for the
    readers that want only the modelled answer.

    It takes NO context, and that is deliberate: everything it would have read
    off one -- the scenario the contribution feed is scoped by, the ``as_of``
    ruling R-G clamps to -- is already inside *cash*.  A signature that took both
    could be handed a fold assembled at one scenario and a context carrying
    another, and the modelled tier would then be loaded against a row set the
    cash tiers underneath it never saw.  Reading it off the fold removes the
    disagreement rather than documenting it.

    **The pay CALENDAR is the exception, and it now comes off the FOLD** (plan
    step **C2-f2a**, ledger row **P37**; re-sourced at pay-calendar plan step
    **C4-a-1**).  The contribution tier below used to load the owner's schedule
    itself, the last ``pay_period_service`` read in this package; C2-f2a made
    it a required argument here instead, on the ground that a
    :class:`~app.services.pay_calendar.PayCalendar` carries no scenario and no
    clock so it cannot reproduce the disagreement above.  **The calendar and
    not a WINDOW** for the reason that ruling gives: the tier's precondition is
    the owner's WHOLE schedule -- the annual limit is a calendar-year
    accumulation, so a slice restarts the year-to-date total mid-year and
    uncaps it -- and a ``PeriodWindow`` cannot say whether ``saved()``,
    ``window()`` or ``axis()`` produced it.

    C4-a-1 changed only WHERE it comes from, and closer to that ruling rather
    than further: it made the calendar a determinant of *cash* too -- the day
    every planned row in the fold lands on -- so a SECOND one taken here as its
    own argument was the shape the paragraph above deletes for the scenario.
    The argument is GONE and the value is read off the record clamped by it, so
    the mis-pairing is unrepresentable rather than refused.  Since plan step
    **C9** (superseding ruling R-AG) the tier walks its projection axis through
    *horizon_end*, past the saved schedule as far as the accrual runs (**P7**).

    Args:
        account: The account to value.
        cash: The account's
            :class:`~app.services.balance_at._cash_fold.AssembledCashFold`,
            which carries the scenario it was scoped by AND the pay calendar it
            was clamped by -- the contribution tier's axis is read from the
            second (``cash.calendar``).
        horizon_end: The furthest date this read will be sampled at.  Sampling
            beyond it would read a balance that had stopped accruing, so every
            caller derives it from its OWN request rather than from a horizon
            constant.
        inputs: The account's
            :class:`~app.services.balance_at._asset_contributions.ContributionInputs`
            -- its ``absent()`` constructor for a reader that cannot have a
            contribution feed.

    Returns:
        The resolved :class:`ModelledFold`.

    Raises:
        ValueError: When *cash* was assembled for a DIFFERENT account (X-i4).
    """
    cash.require_account(account)
    accrual = modelled_return(account, inputs.investment_params)
    if accrual is None:
        return _resolve(cash, [], None)

    # ONE resolution of "the account's latest assertion", read two ways on
    # purpose: the contribution feed asks it the COVERAGE question (ruling
    # R-DH's rule, one implementation), and the accrual window takes its raw
    # day because tiling a calendar is a different question with its own
    # inclusive boundary (ruling R-Z).  They were one bare date until the
    # one-partition step, which is how the contribution feed came to hold a
    # second statement of the coverage rule.
    reconciled_through = _latest_assertion_boundary(account, cash.walk)
    window = AccrualWindow(
        rule=accrual,
        start=reconciled_through.observed_day,
        end=horizon_end,
    )
    return _resolve(
        cash,
        _asset_contributions.contribution_events(
            account, cash.plan.basis, inputs, reconciled_through,
            # Every paycheck from the FIRST payday through the read's end,
            # projected past the saved horizon as the accrual runs (ruling
            # plan#3930): ``projection_axis`` raises ``date.min`` to the first
            # payday, the opening the calendar-year limit needs.
            cash.calendar.projection_axis(date.min, horizon_end),
        ),
        window,
    )


def _assemble(
    account: Account,
    ctx: BalanceContext,
    horizon_end: date,
    inputs: ContributionInputs,
) -> ModelledFold:
    """Assemble the cash fold and resolve the modelled tiers onto it -- ONCE.

    The single assembly the three convenience entries below share, so a scalar,
    a period map and a growth decomposition of the same account are readings of
    ONE resolved step list rather than three producers a test keeps in step.

    **It unpacks NOTHING off the context any more**: the scenario that scopes
    the fold and the ``as_of`` ruling R-G clamps to are read inside
    :func:`~._cash_fold.assembled_fold`, and the owner's pay CALENDAR -- the
    contribution tier's axis, ledger row **P37** -- rode here as a third
    argument until pay-calendar plan step **C4-a-1** moved it onto the
    assembled fold itself.  It is still derived EAGERLY, now inside that fold,
    before :func:`resolve` can early-out on an account modelling no return:
    free on a pass that has already asked, two queries on one that has not.

    Args:
        account: The account to value.
        ctx: The read pass's :class:`~app.services.balance_at.BalanceContext`
            (its scenario scopes the fold and the contribution feed, its
            ``as_of`` is ruling R-G's clamp floor, and its calendar reaches the
            contribution tier's axis through the fold).
        horizon_end: The furthest date this read will be sampled at.
        inputs: The account's
            :class:`~app.services.balance_at._asset_contributions.ContributionInputs`.

    Returns:
        The resolved :class:`ModelledFold`.

    Raises:
        PayCalendarError: The owner's paydays cannot define a calendar -- see
            :meth:`~app.services.balance_at.BalanceContext.calendar`.  **The
            contribution tier could not raise this before plan step C2-f2a**,
            which read the schedule through a query that cannot fail, so where
            it can now surface was CENSUSED; that census is in the step's
            commit message.  Of the ten ``app/`` callers of the two public doors
            above, nine already reach the calendar in the same request or take
            the ``positions`` arm; the ONE that does not is
            ``investment_dashboard_service._orchestrator.compute_balance_hero_cell``,
            which is ``C2-f2c``'s module.  The STATE needed an owner with
            paydays, no ``budget.pay_schedule`` row and a cadence inferred
            outside 1..365 -- **UNSTORABLE since plan step C4-b-2**, which
            pointed the column at that table and deleted the inference (**P8** / **P35**).
    """
    return resolve(
        account,
        _cash_fold.assembled_fold(account, ctx),
        horizon_end,
        inputs,
    )


def fold_asset_balances(
    account: Account,
    ctx: BalanceContext,
    dates: list[date],
    inputs: ContributionInputs,
) -> dict[date, Decimal]:
    """Return *account*'s modelled balance at each of *dates*.

    The modelled counterpart of
    :func:`app.services.balance_at._cash_fold.balances_at`, and the
    producer that makes a modelled kind answer a DATE rather than a period.
    Until plan step X-g2b wired it, ``_kind_correct.balance_at`` resolved a date
    to its pay period and read a period-keyed map for these three kinds, so a
    whole period's growth landed on the period's FIRST day -- measured at period
    30 on the prod-shape clone, the scalar returned the identical value on that
    period's first and last day while $328.50 of growth accrued inside it
    (finding N-71).  A daily step list has no such state to be in, and since
    that step this is what the scalar reads.

    Args:
        account: The account to value.
        ctx: The read pass's :class:`~app.services.balance_at.BalanceContext`.
        dates: The dates to value the account at, in any order.  Duplicates
            collapse.
        inputs: The account's
            :class:`~app.services.balance_at._asset_contributions.ContributionInputs`.

    Returns:
        ``{date: balance}`` -- one cent-quantized ``Decimal`` per distinct
        requested date.  ``{}`` for an empty *dates*.

    Raises:
        PayCalendarError: The owner's paydays cannot define a calendar, which
            this reaches through :func:`_assemble` since plan step C2-f2a --
            see that function's own ``Raises`` for the census.
    """
    if not dates:
        return {}
    folded = _assemble(account, ctx, max(dates), inputs)
    return sample_cumulative(folded.seed, folded.steps, dates)


def asset_period_view(
    account: Account,
    ctx: BalanceContext,
    inputs: ContributionInputs,
) -> "OrderedDict[int, AssetPeriodFigures]":
    """Return *account*'s modelled column for each of the pass's pay periods.

    The per-period map and its accrual and contribution components, sampled off
    ONE resolved step list.  Each period
    is valued over its OWN span -- ``(p.start_date - 1 day, p.end_date]`` -- so
    the first period is covered without a predecessor to subtract from and a
    window is a window rather than a re-based projection.

    Every component is read through the shared
    :func:`~app.services.balance_at._fold.sample_cumulative`, never as a
    residual: a residual would make the identity in
    :class:`AssetPeriodFigures` arithmetically true and therefore untestable,
    and would silently absorb whatever the resolution got wrong.

    Args:
        account: The account to value.
        ctx: The read pass's :class:`~app.services.balance_at.BalanceContext`,
            which is also where the reported periods come from since plan step
            C2-c (:meth:`~app.services.balance_at.BalanceContext.reported_periods`).
        inputs: The account's
            :class:`~app.services.balance_at._asset_contributions.ContributionInputs`.

    Returns:
        ``OrderedDict`` period id -> :class:`AssetPeriodFigures`, in payday
        order.  EVERY reported period is present.  Empty for an owner with no
        pay periods.
    """
    window = ctx.reported_periods()
    if not window:
        return OrderedDict()
    return period_columns(
        _assemble(
            account, ctx, max(period.end_date for period in window), inputs,
        ),
        window,
    )


def period_columns(
    folded: ModelledFold, window: PeriodWindow,
) -> "OrderedDict[int, AssetPeriodFigures]":
    """Read a window's columns off an ALREADY-resolved modelled fold.

    :func:`asset_period_view`'s body, split from its assembly for the same
    reason :func:`~app.services.balance_at._cash_periods.period_view_of` was (plan
    step X-g2a): the grid resolves the modelled tiers over the very
    :class:`~app.services.balance_at._cash_fold.AssembledCashFold` it regroups
    into cash columns, so both of its row sets come off ONE walk.

    Args:
        folded: The account's :class:`ModelledFold` (:func:`resolve`), resolved
            to a horizon at or past every period's ``end_date``.
        window: The pay periods to value, as a slice of the owner's ONE derived
            calendar
            (:meth:`~app.services.balance_at.BalanceContext.reported_periods`).

    Returns:
        ``OrderedDict`` period id -> :class:`AssetPeriodFigures`, in payday
        order.  EVERY period of *window* is present.  Empty for an empty
        *window*.
    """
    if not window:
        return OrderedDict()
    ends = [period.end_date for period in window]
    boundaries = ends + [period.start_date - _ONE_DAY for period in window]
    return _assemble_columns(
        window,
        sample_cumulative(folded.seed, folded.steps, ends),
        sample_cumulative(
            _ZERO_MONEY, sorted(folded.accrual_by_day.items()), boundaries,
        ),
        sample_cumulative(
            _ZERO_MONEY, sorted(folded.contribution_by_day.items()), boundaries,
        ),
    )


def asset_growth_at(
    account: Account,
    ctx: BalanceContext,
    as_of: date,
    inputs: ContributionInputs,
) -> "tuple[Decimal, Decimal]":
    """Return *account*'s ``(accrual, contribution)`` since its latest assertion.

    The growth-vs-contributed decomposition the investment detail page's chip
    renders, read off the replay's own two modelled tiers rather than
    re-projected.

    **"Since the latest assertion" needs no window arithmetic**, which is what
    makes this ONE sample rather than a difference of two: ACCRUAL exists only
    from the latest assertion's own day forward (rulings R-L / R-Y) and a
    CONTRIBUTION only strictly after it (ruling R-Z), so the cumulative total at
    *as_of* IS the total since the anchor.  A window subtraction would state the
    same boundary a second time, and a second statement of a boundary is where
    this arc's defects live.

    The two are reported apart rather than summed because they answer different
    questions -- what the market did, and what the user put in -- and together
    they explain the whole modelled part of the balance change: for any period
    the account holds no recorded rows in,
    ``balance(as_of) - asserted == accrual + contribution`` exactly, every term
    being a whole cent (ruling R-X).

    Args:
        account: The account to decompose.
        ctx: The read pass's :class:`~app.services.balance_at.BalanceContext`.
        as_of: The date to report through, inclusive.
        inputs: The account's
            :class:`~app.services.balance_at._asset_contributions.ContributionInputs`.

    Returns:
        ``(accrual, contribution)`` -- cumulative cent-quantized ``Decimal``
        totals through *as_of*.  ``(0.00, 0.00)`` for an account that models
        neither, which is a real answer and not a missing one.

    Raises:
        PayCalendarError: The owner's paydays cannot define a calendar, which
            this reaches through :func:`_assemble` since plan step C2-f2a --
            see that function's own ``Raises`` for the census.
    """
    folded = _assemble(account, ctx, as_of, inputs)
    return (
        _cumulative(folded.accrual_by_day, as_of),
        _cumulative(folded.contribution_by_day, as_of),
    )


def _cumulative(by_day: "dict[date, Decimal]", as_of: date) -> Decimal:
    """Return the total of a tier's dated deltas on or before *as_of*.

    Read through the shared
    :func:`~app.services.balance_at._fold.sample_cumulative` rather than by
    summing a filtered dict, so a tier total and the balance it moved are the
    same prefix of the same series.

    Args:
        by_day: One tier's ``{day: delta}`` map from a :class:`ModelledFold`.
        as_of: The date to total through, inclusive.

    Returns:
        The cent-quantized cumulative ``Decimal``; ``0.00`` for an empty tier.
    """
    return sample_cumulative(
        _ZERO_MONEY, sorted(by_day.items()), [as_of],
    )[as_of]


def _assemble_columns(
    window: PeriodWindow,
    balances: dict[date, Decimal],
    accrued: dict[date, Decimal],
    contributed: dict[date, Decimal],
) -> "OrderedDict[int, AssetPeriodFigures]":
    """Read each period's column off the three sampled cumulative series.

    Args:
        window: The pay periods to report.
        balances: The modelled running total sampled at each period's
            ``end_date``.
        accrued: The cumulative ACCRUAL sampled at each period's ``end_date``
            AND at the day before its ``start_date``.
        contributed: The cumulative CONTRIBUTION sampled at the same two
            boundaries.

    Returns:
        ``OrderedDict`` period id -> :class:`AssetPeriodFigures`.
    """
    columns: "OrderedDict[int, AssetPeriodFigures]" = OrderedDict()
    for period in window:
        opening = period.start_date - _ONE_DAY
        columns[period.period_id] = AssetPeriodFigures(
            balance=balances[period.end_date],
            accrual=accrued[period.end_date] - accrued[opening],
            contribution=(
                contributed[period.end_date] - contributed[opening]
            ),
        )
    return columns
