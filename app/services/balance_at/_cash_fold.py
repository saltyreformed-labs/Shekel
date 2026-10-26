"""Balance-at-T seam -- the CASH fold: a walk of facts sampled into a balance.

Plan steps **X-b** and **X-c1** (``docs/audits/balance_architecture/README.md``).
"A cash account is an event stream" (Section 3), and this module is the half that
turns that stream into money: the :mod:`app.services.cash_ledger` leaf owns the
WALK (the facts), this owns the FOLD (the balance).  The same split plan step
D-fold made on the loan side, for the same reason -- *a fold is a balance; a walk
is a fact* -- and the same placement: because the prefix-sum lives HERE, a
consumer legitimately holding a
:class:`~app.services.cash_ledger.CashLedgerWalk` (the posting writer at plan
step X-d) cannot reach a balance from a public leaf name.

**Three tiers, ONE :func:`~app.services.balance_at._fold.sample_cumulative`, no
branch.**  Every date is answered off a single running total
(:func:`_running_steps`) assembled from:

* the **SEED** -- the account's stored OPENING EQUITY: what it held before its
  records begin, read from ``budget.account_openings`` rather than backed out of
  its first assertion (plan step **X-f3c-2a**, ruling **R-GX**, which retires
  ruling R-I's back-projection).  See :func:`_actual_steps`.
* the **ACTUAL** steps -- X-a's walk re-keyed by the day each event became
  visible (:func:`app.services.cash_ledger.dated_deltas`, the ONE statement of
  that clock, shared with the posting writer so the fold and the posted ledger
  cannot drift).
* the **PLANNED** steps -- the still-Projected rows, each landing at
  ``max(its attribution date, as_of + 1 day)`` (ruling R-G: "a plan cannot have
  already happened").  The cash twin of the loan timeline's projections
  (:mod:`app.services.balance_at._loan_stream`).  See :func:`_cash_plan`
  and :func:`_planned_day_nets`.

**Three readers of that ONE row set** (plan steps X-c1 / X-c2b2, ruling R-K).
:func:`balances_at` samples the running total at a list of dates -- the
balance a scalar or a daily series asks for.  :func:`period_balances`
samples it at each pay period's end -- the per-period map.  And
:mod:`._cash_periods` samples the same period ends AND groups the very same rows
a second way, by the period each was BUDGETED to, so the grid's balance row and
its subtotal rows reconcile by construction with named remainders for what
neither clock alone can explain.  The assembly is shared rather than duplicated:
one walk, one plan load, one valuation, whichever reader is asking.

**And they READ an assembly they cannot make, which is plan step X-i4.**  Every
reader here took ``(account, basis, as_of[, window])`` and assembled for itself,
so an account and the pass's own derivations were independent arguments that
agreed only because each call site named one ``ctx`` three times -- finding
**N-354**.  Now :func:`assembled_fold` is the ONE door: it memoizes the fold on
the pass and refuses an account the pass does not own
(:func:`~._memoize._memoize_once`), and every reader below takes the assembled
record and carries no account and no clock at all.  There is nothing left to
pair wrongly.

**The third reader lives in its own module since plan step S1-c**, when ruling
R-DH (f) split its remainder in two and this one passed the 1,000-line ceiling.
Assembling a running total and regrouping it into columns are two jobs sharing
exactly one input (:class:`AssembledCashFold`), and the dependency runs one way:
:mod:`._cash_periods` imports this and this imports nothing of it.

Keeping it to one sample is not economy, it is the correctness property: a fold
assembled from a past producer spliced to a future producer needs a rule for the
join, and every such rule in this codebase's history is a place the two sides
disagreed.  There is no join here -- the planned steps are simply later steps on
the same running total.

**What this fixes, and it is measured.**  The shipping projection starts at the
LATEST assertion and sums only the still-Projected rows forward, so:

* a settled row attributed AFTER that assertion is counted by NO producer
  (finding cash D1 -- on production 2026-07-25, ``$2,000.00`` of a Money Market
  transfer and ``$108.15`` on Checking, invisible at that instant, and
  ``$53,880.81`` gross across 130 rows in 45 assertion gaps historically).  Here
  it is an ordinary ACTUAL step that rides on top of the assertion it followed.
* a date before the latest assertion reads TODAY's balance (the scalar) or
  nothing at all (the period map) -- finding B-18 / cash D3.  Here every
  assertion is replayed, so a past date reads the balance in force THEN.
* an overdue-but-still-Projected bill is absorbed by the next re-anchor and
  silently deleted from the projection (on real data, one re-anchor every 2.3
  days).  Here R-G clamps it forward instead.

**TOTAL over every date and every account.**  No date is refused and no account
is: an account with no assertion history folds from a zero seed over whatever
facts it has, a date before every event reads the seed, and a future date
answers.  That totality is what deletes a partial producer's need to be composed
with a seed, a flag or a fallback -- and every such composition is a new producer
that can disagree with the others (plan Section 3).  A caller that must
distinguish "holds nothing" from "no account" asks the account row, never this
function's number.

**Kind-blind by design (ruling R-J).**  It consults no
:class:`~app.services.account_projection.AccountProjectionKind`: this is the
cash-flow view, whose balance must reconcile with the transaction rows rendered
beside it whatever the account.  What keeps a LOAN out of that view is a gate at
the SOURCE -- ``resolve_grid_account`` (plan step A1) and ``resolve_analytics_cash_flow_set``
(X-a1; ``resolve_analytics_account`` until CC-4-3) -- not a refusal in here, which would
reintroduce exactly the partiality above.

**WIRED since plan step X-c2b2.**  Every cash figure the app renders is one of
these three readings: the seam's three cash-flow entries
(:mod:`app.services.balance_at._cash_flow`), the kernel's PLAIN fall-through and
INTEREST seed (:mod:`app.services.balance_at._kernel`), the kind-correct
scalar's PLAIN and degraded-AMORTIZING branches
(:mod:`app.services.balance_at._kind_correct`), and the grid's whole column set
(:mod:`app.services.balance_at._grid`).  That is the cutover: the settled drop,
the scalar/daily fork and the pre-anchor fabrication close together, because one
total fold subsumes all three.

Boundary discipline (``CLAUDE.md``): no Flask symbol, no writes; all money is
:class:`~decimal.Decimal`.
"""

from collections import OrderedDict, defaultdict
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal

from app.models.account import Account
from app.models.transaction import Transaction
from app.services.cash_ledger import (
    InFlightMovement,
    AmountBasis,
    CashLedgerWalk,
    dated_deltas,
    planned_cash_rows,
    sum_projected,
    walk_cash_ledger,
)
from app.services.pay_calendar import FiledRow, PayCalendar, PeriodWindow
from app.services.transfer_legs import TransferLeg

from ._assertions import CashAnchorCorrection, assertion_corrections
from ._context import BalanceContext
from ._memoize import _memoize_once
from ._fold import sample_cumulative

_PlanItem = Transaction | TransferLeg | InFlightMovement  # one plan item

_ZERO_MONEY = Decimal("0.00")
# Ruling R-G's clamp floor: the earliest day a plan can still happen is the day
# AFTER the reader's as-of.  Named (and spelled the same way) as the loan twin's
# ``_plan._ONE_DAY``, because it is the same rule -- ruling D1 for loans, R-G for
# cash -- and the two are meant to read as one.
_ONE_DAY = timedelta(days=1)


@dataclass(frozen=True)
class AssembledCashFold:  # pylint: disable=too-many-instance-attributes
    """One account's whole running total, plus the facts it was built from.

    Pylint: ``too-many-instance-attributes`` (9/7) -- suppressed because the
    count is the POINT rather than an accident.  Three of the nine are the
    inputs this record was built FROM (:attr:`account_id`, :attr:`scenario_id`,
    :attr:`calendar`), carried so a reader holding both this and one of them
    cannot pair them wrongly; the other six (:attr:`seed`, :attr:`steps`,
    :attr:`walk`, :attr:`corrections`, :attr:`plan`, :attr:`day_nets`) are one
    derivation and its groupings.  Collapsing the three into a nested value
    would hide exactly
    what each is here to make visible, and dropping one is what findings
    **N-354** and this step's own review re-opened.  Same disposition as
    :class:`~._context.BalanceContext` (10/7) one module over, for the same
    reason.

    *This said* ``(8/7)`` *and "the other five" until plan step
    balance:X-f3c-3, which is one short of the nine fields below:*
    :attr:`calendar` *arrived at pay-calendar plan step C4-a-1 and the prose
    was never recounted.*  **And the pairing argument above holds for only two
    of the three today**: :attr:`account_id` is read by
    :meth:`require_account` and :attr:`calendar` by
    :func:`~._asset_fold.resolve`, while :attr:`scenario_id` has NO reader at
    all -- a grep over ``app/``, ``tests/``, ``tests/manual/`` and ``scripts/``
    on 2026-09-01 finds it written by :func:`_assemble` and read nowhere, so
    the mis-pairing it is said to prevent is one no caller can perform.  That
    is finding **N-433**, owned by plan step **X-f4**, which is where this
    package's deletions land; it is recorded rather than removed here because
    deleting a field from a shipped value is not X-f3c-3's subject.  What IS
    its subject is that the rationale said something its own code does not.

    The output of :func:`~._cash_fold.assembled_fold`, and the reason the three readers below are
    readings of ONE valued row set rather than three producers a test keeps in
    step (ruling R-K).  It carries the sampling inputs (:attr:`seed` /
    :attr:`steps`) AND the groupings the period view regroups the same rows by
    (:attr:`walk` / :attr:`plan` / :attr:`day_nets`), so no reader re-loads or
    re-values anything a sibling reader already has.

    **A fourth reader lives one module over** (plan step X-g1): the modelled
    asset fold (:mod:`app.services.balance_at._asset_fold`) takes this whole
    record and resolves two MORE event kinds onto the same running total --
    CONTRIBUTION and ACCRUAL.  That is why :func:`~._cash_fold.assembled_fold` and this record are
    seam-visible rather than module-private: a modelled asset IS its cash fold
    plus a modelled return (plan Section 3.2), so the two must share ONE
    assembly rather than the modelled side re-deriving a cash basis.  It reads
    :attr:`walk` for the latest ASSERTION, which is where its accrual window
    opens (ruling R-L, generalised at ruling R-Y).

    Attributes:
        account_id: The account these rows belong to, added by plan step
            **X-i4** for the case ruling R-GV did not reach: a reader taking
            BOTH this record and an ``Account`` -- which
            :func:`app.services.balance_at._asset_fold.resolve` does, folding
            the account's own modelled rule, its latest assertion and its
            contribution feed onto these steps -- could be handed the two for
            different accounts.  The pass refuses a foreign account where it
            MEMOIZES (``_memoize._memoize_once``); that reader memoizes nothing,
            so the pairing is bound HERE, on the value, rather than by a rule
            its caller has to keep.  An adversarial review of X-i4's first build
            measured the gap it closes: an account of one owner resolved onto a
            fold assembled for another, seeding the first's HYSA rate with the
            second's `$2,000.00` opening.
        seed: The balance before every step -- the account's stored OPENING
            EQUITY (plan step X-f3c-2a, ruling R-GX).
        steps: The dated deltas, ASCENDING by date -- the ACTUAL tier and the
            PLANNED tier merged into one list.
        walk: The account's :class:`~app.services.cash_ledger.CashLedgerWalk`
            (its settled source facts and its balance assertions).
        corrections: One
            :class:`~._assertions.CashAnchorCorrection` per assertion, replayed
            ONCE here (:func:`~._assertions.assertion_corrections`) rather than
            by each of the FIVE readers that need them -- the step list below,
            :func:`day_facts`' assertion split, :mod:`._cash_periods`'
            per-period component, and both of :mod:`._cash_flow`'s
            (``records_balance_at`` and ``cash_anchor_history``).  Redundant
            derivation
            is where a divergence hides, which is the lesson
            :class:`~._context.BalanceContext` was built on; and since plan step
            **X-f3c-1** the replay is a POLICY rather than a property of the
            facts (see :mod:`._assertions`), so a second caller of it would be a
            second place the policy could differ.
        plan: The account's :class:`_CashPlan` (its still-Projected rows, the
            day each lands on, and the live override map).
        day_nets: The PLANNED tier's per-day nets.
        calendar: The owner's :class:`~app.services.pay_calendar.PayCalendar`
            these steps were CLAMPED by, added by pay-calendar plan step
            **C4-a-1** for the reason :attr:`account_id` and
            :attr:`scenario_id` are here.  That step made the calendar a THIRD
            determinant of this record -- it decides the day every planned row
            lands on (:func:`_cash_plan`) -- and a determinant a reader can
            supply a second, different answer for is the shape those two fields
            exist to close.  Carrying it means the modelled fold one module
            over reads the calendar this record was actually built with rather
            than taking one beside it: :func:`~._asset_fold.resolve` DROPPED
            its own ``calendar`` parameter here, which removes the mis-pairing
            rather than adding a check that catches it.
    """

    account_id: int
    scenario_id: int
    seed: Decimal
    steps: "list[tuple[date, Decimal]]"
    walk: CashLedgerWalk
    corrections: "list[CashAnchorCorrection]"
    plan: _CashPlan
    day_nets: "dict[date, Decimal]"
    calendar: PayCalendar

    def require_account(self, account: Account) -> None:
        """Refuse an *account* these steps were not assembled for.

        **The pairing check for a reader that takes BOTH this record and an
        ``Account``** -- which :func:`~._asset_fold.resolve` does, folding the
        account's own modelled rule, its latest assertion and its contribution
        feed onto these steps.  The pass refuses a foreign account where it
        MEMOIZES (:func:`~._memoize._memoize_once`); such a reader memoizes
        nothing, so the rule lives on the VALUE instead and every reader that
        needs it asks the same one rather than writing its own.

        Args:
            account: The account the caller is about to fold onto these steps.

        Raises:
            ValueError: When it is not the account this record is for.
        """
        if self.account_id != account.id:
            raise ValueError(
                f"account {account.id} cannot be folded onto a cash fold "
                f"assembled for account {self.account_id}: the running total, "
                f"the walk and the plan are all that account's. Take the fold "
                f"from the pass this account came from (assembled_fold)"
            )


def assembled_fold(
    account: Account, ctx: BalanceContext,
) -> AssembledCashFold:
    """Return *account*'s assembled cash fold for this pass, assembling it once.

    **The seam's ONE door to a cash fold** (plan step **X-i4**), and the cash
    counterpart of :meth:`~._context.BalanceContext.loan_walk`: one walk of the
    account's facts, one load of its plan and one valuation, however many
    readings ask.  The four readings below take the record this returns, so
    none of them carries an account or a clock and there is nothing left for a
    caller to pair wrongly.

    **What it replaced.**  :func:`_assemble` and four siblings each took
    ``(account, basis, as_of[, window])``, and every call site spelled
    ``ctx.amounts(), ctx.as_of`` by hand -- values that agreed only because one
    ``ctx`` happened to be named two or three times, with nothing checking the
    account against the ``user_id`` that pass pins (finding **N-354**).  The
    refusal is :func:`~._memoize._memoize_once`'s rather than this function's,
    because creating per-account state on a pass is the thing that must be
    bound and that is the only way to create it.

    **The memo, measured** on the dev database over the six cash entries a
    single-account screen asks: **42 ``walk_cash_ledger`` calls across 8
    accounts before, 8 after** -- and
    :func:`~app.services.balance_at.records_balance_at` walked the same account
    TWICE inside one call, once for its assertion lookup and once through its
    :func:`~app.services.balance_at.cash_balance_at` fall-through.  Redundant
    derivation is where a divergence hides, which is the lesson
    :class:`~._context.BalanceContext` was built on.

    **What a caller must obey**, and it is that object's standing contract
    rather than a new one: a pass is a memo whose lifetime is the ONE read it
    was built for, so a path that writes and then reads again builds a FRESH
    pass.  Nothing in ``app/`` reuses one across a write; four TESTS did and
    were corrected, because the cash fold was the last account-keyed derivation
    a pass did not hold and so the only one that could hide the mistake.

    **The door is HERE and the cache is PRIVATE, which is not an accident of
    layering.**  An :class:`AssembledCashFold` carries ``seed`` and ``steps`` --
    a running total, so five lines of prefix sum over it reproduce
    :func:`~app.services.balance_at.cash_balance_at`'s answer exactly.  X-i4's
    first build put the door on :class:`~._context.BalanceContext` as a public
    method with a public cache beside it, and two adversarial reviews measured
    the same consequence: a consumer importing nothing private, holding only the
    re-exported context, could read a balance the W9910 fence exists to make
    unreachable.  W9910 sees IMPORTS and ``protected-access`` sees underscores,
    so neither saw it -- and W9909, which DOES scope public methods on that
    class, refused the public form outright with "non-producer or move it into a
    private seam module".  This module IS that private seam module.

    Args:
        account: The account to value.  Must belong to ``ctx.user_id``, which is
            REFUSED rather than trusted.  Its ``id`` scopes the walk and the
            plan; its KIND is not consulted (ruling R-J).  Must be attached to
            ``db.session``.
        ctx: The read pass.  Its
            :meth:`~._context.BalanceContext.amounts` carries the scenario whose
            rows are folded and the derivations they are priced through, and its
            ``as_of`` is ruling R-G's clamp floor.

    Returns:
        The pass's memoized :class:`AssembledCashFold` for this account.

    Raises:
        ForeignAccountError: When *account* belongs to another owner.
        BaselineMissingError: When this pass has no baseline scenario -- a row's
            amount rule resolves against one.
        PayCalendarError: When the owner's paydays cannot define a calendar.
            **New at pay-calendar plan step C4-a-1, and disclosed rather than
            absorbed**: the PLANNED tier now clamps each row against the span
            its paycheck DERIVES
            (:meth:`~app.services.pay_calendar.PayCalendar.require_period`), so a cash fold
            rests on the same derivation every per-period entry beside it
            already did.
            **The route that made this reachable from a PAGE is closed** (plan
            step C4-b-2, ledger rows **P8** / **P35**).  It needed a cadence
            outside 1..365, which ``resolve_cadence``'s fallback could infer
            for an owner with no ``budget.pay_schedule`` row; that owner is
            unstorable under ``fk_pay_periods_schedule`` and the fallback is
            deleted, so the only cadence there is comes from a column bounded
            to the same 1..365 the derivation enforces.  Declared still,
            because ``derive_periods`` refuses payday SETS it cannot derive
            from and this pass does not enumerate its callers' inputs.
        RuntimeError: When a planned row names a pay period this pass's
            calendar does not hold -- see
            :meth:`~app.services.pay_calendar.PayCalendar.require_period`.
    """
    # Pylint: ``protected-access`` -- one of TWO crossings of this boundary in
    # the package (the other: ``_loan_stream.loan_timeline``, its loan twin
    # since recurrence:R16-c-1), and the design rather than a shortcut: this
    # module owns the cash-fold derivation and the context owns per-pass
    # storage (plan step D-ctx-b's rule for the three PUBLIC caches beside it).
    # THIS one is private because it holds a balance-at-T (see above), and
    # Python has no package-private -- so the alternatives are a public cache
    # no gate can see or eight disables at the reading call sites.
    cache = ctx._cash_folds  # pylint: disable=protected-access
    return _memoize_once(
        ctx, cache, account,
        lambda: _assemble(
            account, ctx.amounts(), ctx.as_of, ctx.calendar(),
        ),
    )


def _assemble(
    account: Account, basis: AmountBasis, as_of: date, calendar: PayCalendar,
) -> AssembledCashFold:
    """Walk the account's facts and load its plan -- ONCE, for every reader.

    Private since plan step X-i4: :func:`assembled_fold` above is its only
    caller, so the ``(account, basis, as_of, calendar)`` tuple is constructed in
    exactly one place, out of one pass, and no other seam module can spell it.

    Args:
        account: The account to value.  Its ``id`` scopes the walk and the plan;
            its KIND is not consulted (ruling R-J).  Must be attached to
            ``db.session``.
        basis: The read pass's
            :class:`~app.services.cash_ledger.AmountBasis`
            (:meth:`~app.services.balance_at.BalanceContext.amounts`).  It
            carries the SCENARIO whose rows are folded -- assertions are
            per-ACCOUNT and replay in every scenario; only the transaction rows
            are scenario-scoped -- so the scenario and the pricing it resolves
            under are one argument rather than two a caller could disagree
            about (plan step X-au-c2b).
        as_of: The reader's NOW, and since plan step X-c2c1 deleted the
            reservation's entry window it does exactly ONE job: it is the floor
            a still-Projected row's effective date is clamped up to (ruling
            R-G).  It decides WHEN a row lands, never what it is worth.
        calendar: The OWNER's pay calendar
            (:meth:`~._context.BalanceContext.calendar`), which
            :func:`_cash_plan` clamps each planned row against.  **A calendar
            rather than the pass, and REQUIRED** (pay-calendar plan step
            C4-a-1): it is the shape ruling **R-PC19** ("How the CONTRIBUTION
            tier learns its periods") already gave
            :func:`~._asset_fold.resolve` one module over, and a calendar
            carries neither a scenario nor a clock, so passing one alongside
            *basis* and *as_of* reintroduces nothing they could disagree
            about.

    Returns:
        The :class:`AssembledCashFold`.

    Raises:
        RuntimeError: A planned row names a pay period *calendar* does not
            hold (:meth:`~app.services.pay_calendar.PayCalendar.require_period`).
    """
    walk = walk_cash_ledger(account.id, basis.scenario_id)
    # The RESET policy, applied ONCE for every reader below (plan step
    # X-f3c-1).  It is what the walk itself used to do; it moved out because
    # what an assertion does to a running total differs by account kind
    # (ruling R-FO) and the walk may not consult one (ruling R-J).
    corrections = assertion_corrections(walk)
    # The plan load is INDEPENDENT of the walk since plan step X-f3b (ruling
    # **R-FM**): it took the walk so the entry reservation could ask which of an
    # envelope's purchases a declared balance already contained, and a purchase
    # carrying a posting day is now a movement in the walk itself, so the
    # reservation reads the purchase and the clearing rule is only ever asked
    # where the money is replayed.
    plan = _cash_plan(account, basis, as_of, calendar)
    day_nets = _planned_day_nets(plan)
    seed, steps = _running_steps(walk, corrections, day_nets)
    return AssembledCashFold(
        account_id=account.id,
        scenario_id=basis.scenario_id,
        seed=seed, steps=steps, walk=walk, corrections=corrections,
        plan=plan, day_nets=day_nets, calendar=calendar,
    )


def balances_at(
    folded: AssembledCashFold, dates: list[date],
) -> dict[date, Decimal]:
    """Return the folded cash balance at each of *dates*.

    The cash counterpart of
    :func:`app.services.balance_at._fold.fold_loan_balances`, and the reading
    the seam's cash-flow SCALAR and DAILY SERIES take (plan step X-c2b2).  ONE
    walk of the account's facts plus ONE load of its plan, sampled at every
    requested date -- so N dates cost one pass, not N, which is what lets the
    daily series be a sampling of the period map's own running total rather than
    a second producer that drifts from it (finding cash D2: ``$15.96`` apart on
    the real Checking account the day before the cutover, ``$246.36`` at the
    worst day of the current period).

    **It TAKES the assembled fold since plan step X-i4** and therefore carries
    no account and no clock: it was ``(account, basis, as_of, dates)``, three of
    whose four arguments a caller had to keep in agreement by hand.  The
    reader's NOW is inside *folded* -- ruling R-G's clamp was applied when the
    plan was keyed onto landing days -- so the only date left here is a
    VALUATION date, which may be long before that now (a historical read) or
    long after (a projection).  Two dates that are not the same date used to be
    a contract this docstring stated; it is now a property of the signature.

    Args:
        folded: The account's :class:`AssembledCashFold`
            (:func:`~._cash_fold.assembled_fold`).
        dates: The dates to value the account at, in any order.  Duplicates
            collapse.

    Returns:
        ``{date: balance}`` -- one cent-quantized ``Decimal`` per distinct
        requested date.  ``{}`` for an empty *dates*.
    """
    return sample_cumulative(folded.seed, folded.steps, dates)


@dataclass(frozen=True)
class CashDayFacts:
    """One day's folded balance, and the THREE things that moved it.

    **The decomposition lives here, beside :func:`_running_steps`, because it
    is that assembly read back.**  A consumer deriving it -- "whatever the
    balance moved by, less the rows I can see, must be a true-up" -- would be
    correct today and silently WRONG the day a fourth tier joins the running
    total, labelling it as something it is not.  Stating the split where the
    steps are built means a new tier is a change to this class rather than a
    mislabelled number on a report.

    Attributes:
        balance: The account's cash-flow balance at the END of the day, cent
            quantized -- the same figure :func:`balances_at` samples,
            from the same running total.
        recorded: What the account's OWN settled rows moved that day
            (:attr:`~app.services.cash_ledger.CashSourceFact.delta`, summed).
            The only one of the three that is money the app believes actually
            changed hands on that day.
        asserted: What BALANCE ASSERTIONS moved that day -- the jump each reset
            booked, **every assertion included, the first one too** (plan step
            X-f3c-2a).  The opening used to contribute nothing by construction,
            because the fold swallowed its correction into the seed and
            cancelled it on its own day (ruling R-I).  The seed is a stored fact
            now, so the first assertion books what it actually is: the
            difference between what the owner declared and what the books held,
            which is ``0.00`` wherever the two agree and a real movement
            wherever a BACK-DATED assertion disagrees with the recorded
            opening.
        planned: What still-Projected rows contribute that day, each landing at
            ``max(its attribution date, as_of + 1)`` (ruling R-G).  Zero for
            every day at or before *as_of*, which is what lets a reader
            comparing PAST days against an outside record ignore it.
    """

    balance: Decimal
    recorded: Decimal
    asserted: Decimal
    planned: Decimal


@dataclass(frozen=True)
class CashDaySeries:
    """A day-grain reading of one account's fold, and the day its books OPENED.

    Attributes:
        facts: One :class:`CashDayFacts` per requested day.
        opened_on: The day the account's books opened -- the governing
            :attr:`~app.services.cash_ledger.CashOpeningFact.opened_on` off
            the walk this fold was built from.  Never ``None``: the walk cannot
            be loaded without an opening.

    **``opened_on`` travels with the facts because the walk already holds it.**
    A reader comparing the app against an outside record has to tell "we
    disagree here" from "the books do not know this day", and the books'
    boundary is read two ways (:mod:`app.utils.books_boundary`, ruling
    **R-HG**): a balance from ``opened_on`` on, a movement only after it.  The
    facts stay TOTAL -- the flat opening level included -- because what a
    day's figures MEAN beside an outside record is that reader's question
    (:mod:`app.services.bank_agreement`, plan step ``balance:X-f3c-2b-2c``).
    *Until that step this carried the account's first CASH FACT instead*
    (finding **BAL-616**), which counted the opening day's lines as
    disagreements wherever an assertion fell on it and compared a quiet books
    day only once something (a row or a back-dated balance) was recorded on
    or before it.
    """

    facts: "dict[date, CashDayFacts]"
    opened_on: date


def _day_sums(
    pairs: "list[tuple[date, Decimal]]",
) -> "dict[date, Decimal]":
    """Return ``{day: total}`` for ``(day, amount)`` pairs.

    Args:
        pairs: The dated amounts to reduce.

    Returns:
        One entry per DISTINCT day present, summed.
    """
    sums: "dict[date, Decimal]" = {}
    for day, amount in pairs:
        sums[day] = sums.get(day, _ZERO_MONEY) + amount
    return sums


def day_facts(
    folded: AssembledCashFold, days: list[date],
) -> "CashDaySeries":
    """Return each day's folded balance beside the three tiers that moved it.

    The fourth reading of the ONE assembled row set (plan step
    ``bank_import:X-f6e-2``), and a reading rather than a second producer: the
    balance it reports is :func:`sample_cumulative` over the very
    ``(seed, steps)`` :func:`balances_at` samples, so the two cannot
    disagree about a day's balance.  What it adds is the SPLIT of that day's
    movement, which no other reader needs and a consumer must not re-derive
    (see :class:`CashDayFacts`).

    Args:
        folded: The account's :class:`AssembledCashFold`
            (:func:`~._cash_fold.assembled_fold`).
        days: The days to answer, in any order.  Duplicates collapse.

    Returns:
        The :class:`CashDaySeries` -- one :class:`CashDayFacts` per distinct
        requested day, and the day the account's books opened.

    **The three components sum to the day's change in the running total**, and
    that is arithmetic rather than a claim: ``_running_steps`` assembles exactly
    ``dated_deltas`` (the source facts) plus this fold's assertion corrections
    plus the planned nets, so a day's steps ARE these three sums.  The opening
    equity is in the SEED, not in any day's steps, so it moves no day (plan step
    X-f3c-2a; before it a compensator had to cancel the opening correction on
    its own day to achieve the same thing).  ``balance`` is quantized where the
    components are exact, so a
    reader wanting the identity to the cent must compare the components rather
    than differencing two rounded balances.
    """
    balances = sample_cumulative(folded.seed, folded.steps, days)
    recorded = _day_sums(
        [(fact.settled_on, fact.delta) for fact in folded.walk.source_facts]
    )
    # EVERY assertion's correction, the first included (plan step X-f3c-2a).
    # The opening's used to be excluded because the fold swallowed it into its
    # seed and cancelled it on its own day; with the seed a stored fact the
    # first assertion is an ordinary correction -- ``0.00`` where the books
    # opened at the level the records imply, and a real movement where a
    # BACK-DATED assertion disagrees with the recorded opening, which is
    # precisely the day a reader needs to see it.
    asserted = _day_sums(
        [
            (correction.observed_on, correction.delta)
            for correction in folded.corrections
        ]
    )
    return CashDaySeries(
        facts={
            day: CashDayFacts(
                balance=balance,
                recorded=recorded.get(day, _ZERO_MONEY),
                asserted=asserted.get(day, _ZERO_MONEY),
                planned=folded.day_nets.get(day, _ZERO_MONEY),
            )
            for day, balance in balances.items()
        },
        opened_on=folded.walk.opening.opened_on,
    )


def period_balances(
    folded: AssembledCashFold, window: PeriodWindow,
) -> "OrderedDict[int, Decimal]":
    """Sample an assembled fold at each period's ``end_date``, keyed by period id.

    The per-period map (plan step X-c2b2): the seam's ``cash_balance_map``, the
    kernel's PLAIN fall-through, and the INTEREST accrual's SEED all read it, so
    a period column, the scalar sampled at that column's end date, and the daily
    series' last day of it are one running total read at three grains.
    :func:`~._cash_periods.period_view_of` reads it too, for the balance row it
    reconciles its budget-clock subtotals against.

    **TOTAL over the periods it is given**, which is the cutover's whole point:
    the shipping producer carried the anchor forward and OMITTED every
    pre-anchor period, so a past column rendered blank while the account plainly
    held money (finding cash D3 / B-18 -- on the real Checking account, eight
    blank columns).  Every assertion is replayed here, so a past period reads the
    balance in force THEN.

    **One function since plan step X-i4**, where it was two: a private
    ``_period_balances`` taking the assembled fold, and a public
    ``cash_period_balances`` taking ``(account, basis, as_of, window)`` that
    assembled one first.  The second was the mis-pairable spelling -- and every
    caller of it already held the pass the fold now comes out of.

    **The end it samples at is DERIVED, since plan step C2-c.**  It was
    ``PayPeriod.end_date``, a stored copy of ``lead(start_date) - 1`` with
    nothing reconciling it to the paydays it comes from, so a schedule whose
    stored ends had drifted valued a column on a day the calendar does not
    agree is that column's last (``docs/plans/implementation_plan_pay_calendar.md``
    section 1).  Measured on both production-shaped databases the day this
    shipped: 0 of 62 and 0 of 61 stored ends differ from the derivation, so
    the class of defect was latent rather than live, and plan step C4-c
    removed the column that could hold it.

    Args:
        folded: The account's :class:`AssembledCashFold`
            (:func:`~._cash_fold.assembled_fold`).
        window: The pay periods to value, as a slice of the owner's ONE derived
            calendar
            (:meth:`~app.services.balance_at.BalanceContext.reported_periods`).
            It need not start at the account's anchor.

    Returns:
        ``OrderedDict`` period id -> cent-quantized ``Decimal``, in payday
        order.  EVERY period of *window* is present.
    """
    sampled = sample_cumulative(
        folded.seed, folded.steps, [period.end_date for period in window],
    )
    return OrderedDict(
        (period.period_id, sampled[period.end_date]) for period in window
    )


def _actual_steps(
    walk: CashLedgerWalk, corrections: "list[CashAnchorCorrection]",
) -> "tuple[Decimal, list[tuple[date, Decimal]]]":
    """Return the ``(seed, steps)`` the RECORDED facts contribute.

    The seed is the account's stored OPENING EQUITY and the steps are the leaf's
    :func:`app.services.cash_ledger.dated_deltas` plus the assertion RESETS this
    fold has chosen to apply; nothing is re-keyed, re-valued or compensated.
    That restraint is deliberate: the same re-key is what the posting writer
    consumes at plan step X-d, and a second statement of "which day does this
    event count from, and for how much" is precisely how the fold and the posted
    ledger drift apart (the shape plan step E1a found on the loan side).

    **The assertion steps are this fold's CHOICE since plan step X-f3c-1**, and
    naming that is the point: ``dated_deltas`` carried them until then, so every
    consumer of the leaf inherited the RESET whether its account kind wanted one
    or not (ruling R-FO says the modelled kinds do and plan step X-f3c says the
    PLAIN ones do not).  They are merged HERE, from
    :func:`~._assertions.assertion_corrections`, which is the one place the
    cutover has to delete them from.

    **The seed is a RECORDED FACT, and ruling R-I's back-projection is GONE**
    (plan step **X-f3c-2a**, ruling **R-GX**).  This function used to compute
    the seed: the replay started at zero, so the first assertion's correction
    came out as the whole of what the account held before its records began, and
    the fold moved that correction into the seed and cancelled it with an
    equal-and-opposite step on the opening's own day.  The arithmetic was right
    and the QUANTITY was inferred -- re-derived on every read from whichever
    assertion sorted first, over scenario-scoped rows, with nowhere to correct
    it.  ``budget.account_openings`` records it, so the seed is a read and there
    is no compensator to keep in step with anything.

    **What that deletes, concretely.**  The ``corrections[0]`` special case, the
    appended ``-opening.delta`` step, the ``if not corrections`` branch (an
    account with no assertion now folds from its real opening rather than from
    zero), and the pin
    ``TestTheOpeningMovesIntoTheSeed.test_at_and_after_the_opening_it_equals_the_zero_seeded_walk``
    existed to hold -- the cancellation it asserted has no terms left.  The
    balance at every date is unchanged on production, and that is arithmetic
    rather than a hope: with ``E`` the stored opening, ``P`` the records the
    first assertion cleared and ``A`` what it asserted, the migration seeds
    ``E = A - P``, so a date BEFORE the opening reads ``E + <records through
    it>`` exactly as the old seed did, and a date at or after it reads
    ``E + P + (A - E - P) = A`` -- the same reset, reached without a
    compensator, and independent of ``E`` because the assertion overwrites it.

    Args:
        walk: The account's :class:`~app.services.cash_ledger.CashLedgerWalk`,
            whose :attr:`~app.services.cash_ledger.CashLedgerWalk.opening`
            carries the level this seeds from.
        corrections: Its assertion corrections
            (:func:`~._assertions.assertion_corrections`), replayed once by
            :func:`_assemble` and shared with every other reader of this fold.

    Returns:
        ``(seed, steps)`` -- the account's opening equity, and the dated deltas
        with every assertion's correction merged in.  ``steps`` is a fresh list
        the caller owns and may extend, and it is NOT sorted: the caller sorts
        once after merging in the planned tier (which
        :func:`~app.services.balance_at._fold.sample_cumulative` requires).
        Within-day order is immaterial -- ``sample_cumulative`` reads a day's
        boundary AFTER every step on it, so only the day's SUM is observable.
    """
    steps = dated_deltas(walk)
    steps.extend(
        (correction.observed_on, correction.delta)
        for correction in corrections
    )
    return walk.opening.opening_equity, steps


def _running_steps(
    walk: CashLedgerWalk,
    corrections: "list[CashAnchorCorrection]",
    day_nets: "dict[date, Decimal]",
) -> "tuple[Decimal, list[tuple[date, Decimal]]]":
    """Return the ``(seed, steps)`` a whole cash account folds from.

    The ONE assembly, and the reason both readers in this module are readings of
    ONE valued row set rather than two producers a test keeps in step (ruling
    R-K): :func:`balances_at` samples these steps for a balance, and
    :func:`~._cash_periods.period_view_of` samples them for its balance row while grouping the
    SAME facts on the budget clock beside it.

    Args:
        walk: The account's :class:`~app.services.cash_ledger.CashLedgerWalk`.
        corrections: Its assertion corrections
            (:func:`~._assertions.assertion_corrections`).
        day_nets: The PLANNED tier's per-day nets
            (:func:`_planned_day_nets`), merged in as later steps on the same
            running total -- never spliced on as a second producer's series.

    Returns:
        ``(seed, steps)`` with *steps* ASCENDING by date, which is what
        :func:`~app.services.balance_at._fold.sample_cumulative` requires.
    """
    seed, steps = _actual_steps(walk, corrections)
    steps.extend(day_nets.items())
    steps.sort(key=lambda step: step[0])
    return seed, steps


@dataclass(frozen=True)
class _CashPlan:
    """The account's still-Projected rows, loaded and dated once.

    The PLANNED tier's inputs, shared by the two readers in this module so a
    plan is loaded (and its live override map built) ONCE per call whichever
    reader is asking.  Splitting the LOAD from the reduction is what lets the
    period view group the same rows on the budget clock without a second query
    -- redundant derivation being where a divergence hides (the lesson the read
    pass's context was built on).

    Attributes:
        rows: What :func:`~app.services.cash_ledger.planned_cash_rows` loads,
            unwindowed: every still-Projected contributing row of the
            account's OWN, one
            :class:`~app.services.transfer_legs.TransferLeg` per
            still-projected transfer whose leg here is not yet a dated
            movement (rulings **R-BAL13**, **R-BAL79**), and one
            :class:`~app.services.cash_ledger.InFlightMovement` per un-dated
            purchase on the account (ruling **R-BAL77**).
        by_day: The same items keyed by the day each LANDS on (ruling R-G's
            clamp; a movement in flight's floor is the day it happened) --
            the cash clock.  Empty when the account has no plan.
        basis: The READ PASS's
            :class:`~app.services.cash_ledger.AmountBasis` -- what every row is
            priced through -- carried here and threaded into every reduction so
            no reduction reaches for one of its own.  It was BUILT here, over
            this plan's rows, until plan step X-au-c2b: a basis is pinned to an
            owner and a scenario now rather than to a row set, so the pass hands
            one down instead and a second reader of the same request stops
            paying for a second paycheck-engine run (findings **N-268**,
            **N-269**).  It was a
            ``ProjectedBasis`` carrying the account's clearing rule beside this
            until plan step X-f3b: the entry reservation was what asked, and
            ruling **R-FM** made a purchase's posted-ness a fact about the
            PURCHASE, so the reservation reads the row in front of it and the
            clearing rule stayed with the WALK, which is the only thing that
            needs it.
    """

    rows: "list[_PlanItem]"
    by_day: "dict[date, list[_PlanItem]]"
    basis: AmountBasis


def _cash_plan(
    account: Account, basis: AmountBasis, as_of: date, calendar: PayCalendar,
) -> _CashPlan:
    """Load the account's plan and key each row onto the day it lands on.

    The PLANNED tier's load, and the reason it lives in this READER rather than
    in the clock-free leaf: a plan's effective date is a function of *as_of*
    (ruling R-G), exactly as the loan plan's is (plan step C6a).

    **Where a planned row lands.**  Its nominal day is the shared
    :meth:`~app.services.pay_calendar.DerivedPeriod.attribution_day` -- its
    ``due_date``, falling back to
    its pay period's ``start_date``, clamped into that period's span -- the SAME
    rule the calendar groups its day cells by, so a flow's cell and the balance
    line's step for it cannot land on different days.  That day is then clamped
    UP to ``as_of + 1``: a plan cannot have already happened, so an overdue bill
    moves forward rather than being absorbed by the next assertion's reset.
    Rejected at the ruling: landing it on its nominal date, which on real data
    (one re-anchor every 2.3 days on Checking) silently deletes nearly every
    unpaid past-due bill within days of its being entered.

    **A movement IN FLIGHT lands on ``max(the day it happened, as_of + 1)``**
    (ruling **R-BAL77**): money already spent and not yet seen to leave, with
    no due date and no period to clamp into, held from tomorrow and never
    from before it was made.  Its BUDGET column stays its parent's.

    **A transfer LEG lands where its PARENT is filed** (plan step **X-bi-6a**).
    The plan holds one :class:`~app.services.transfer_legs.TransferLeg`
    per still-projected transfer the account is on, and the row the calendar
    is asked to place is the parent ``Transfer`` -- its ``pay_period_id`` and
    ``due_date`` are the leg's, and
    :meth:`~app.services.pay_calendar.FiledRow.for_row` already takes a
    transfer.  Under Transfer Invariant 3 that was the day the shadow row this
    leg replaced landed on; where a pair had drifted, the parent's day won,
    which is ruling **R-JA**'s direction.

    **The SPAN it clamps against is DERIVED, since pay-calendar plan step
    C4-a-1**, which is why this function takes a calendar -- see
    :meth:`~app.services.pay_calendar.PayCalendar.require_period` -- the ONE
    statement of that rule for every caller placing a stored row.  It read
    ``txn.pay_period`` until then, so this module
    clamped a projected row against a STORED end while :func:`period_balances`
    sampled the very same period at its derived one.

    **This is the EXPOSED order for balance finding N-358, and saying which
    order a caller has is the whole of its exposure.**  The calendar is derived
    before these rows are loaded, so under ``READ COMMITTED`` a payday appended
    between the two leaves rows filed in a period the calendar never saw -- and
    the rolling top-up appends exactly that way, mid-render, on ``/grid`` and
    ``/dashboard``.  What holds today is a MEASUREMENT rather than a
    guarantee: recording the request behind every :func:`assembled_fold` across
    the whole suite on 2026-08-27 gave **2,206 dispatched-request assemblies
    over 22 distinct endpoints, every one of them a GET**, and a GET holds one
    snapshot between :func:`~app.db_transaction.write_transaction` blocks.  Two
    limits on that, both real.  It is a census of what RAN, so it cannot say
    what does not run.  And it counted assemblies REACHED THROUGH A REQUEST --
    the suite calls this door directly too, and those are not in the figure --
    so the supporting fact for "no other caller" is a different one:
    ``grep -rl balance_at scripts/`` is empty, so no CLI door reaches the seam
    at all.  ``balance:X-i5`` is what turns the measurement into a guarantee.

    **The load is separate from the reduction, and that is plan step X-c1's
    doing.**  The rows are kept, not just their per-day totals, because the
    period view reduces the SAME rows a second way -- by the budget column they
    were attributed to -- and re-loading them for that would be two answers to
    "what is in this account's plan" a day apart from each other.

    **It no longer takes the WALK, and plan step X-f3b is why.**  It did, for
    one reason: a projected envelope's reservation had to know which of its
    purchases a declared balance already contained, and that answer
    (:attr:`~app.services.cash_ledger.CashLedgerWalk.coverage`) was read off the
    facts :func:`_assemble` had already loaded rather than by resolving the
    account's assertions a second time.  Ruling **R-FM** dissolved the question:
    a purchase carrying a recorded posting day is a cash movement of its OWN in
    the walk, so the reservation asks the purchase and the clearing rule stays
    where it is asked -- in the walk, about that movement.  One less argument,
    and no shape in which the plan could be valued against a different account's
    assertions than the walk replayed.

    Args:
        account: The account whose plan to load.
        basis: The read pass's amount basis, carrying the scenario the rows live
            in and the derivations they are priced through.
        as_of: The reader's NOW -- the floor ruling R-G clamps a landing day up
            to.
        calendar: The OWNER's pay calendar, which each row's span is read
            from (:meth:`~app.services.pay_calendar.PayCalendar.require_period`).

    Returns:
        The account's :class:`_CashPlan`; its ``rows`` and ``by_day`` are empty
        for an account with no plan, and it carries the pass's basis either way
        so the record is self-describing.

    Raises:
        RuntimeError: A row names a pay period *calendar* does not hold
            (:meth:`~app.services.pay_calendar.PayCalendar.require_period`).
    """
    rows = planned_cash_rows(account.id, basis.scenario_id)
    if not rows:
        return _CashPlan(rows=[], by_day={}, basis=basis)

    not_before = as_of + _ONE_DAY
    by_day: "dict[date, list[_PlanItem]]" = defaultdict(list)
    for item in rows:
        if isinstance(item, InFlightMovement):
            # No due date and no attribution clamp: money already spent lands
            # no earlier than the day it happened (ruling **R-BAL77**).
            by_day[max(item.purchased_on, not_before)].append(item)
            continue
        filed = item.transfer if isinstance(item, TransferLeg) else item
        period = calendar.require_period(FiledRow.for_row(filed))
        nominal = period.attribution_day(item.due_date)
        by_day[max(nominal, not_before)].append(item)
    return _CashPlan(rows=rows, by_day=dict(by_day), basis=basis)


def _planned_day_nets(plan: _CashPlan) -> "dict[date, Decimal]":
    """Return ``{day: net}`` for the plan -- what each landing day is WORTH.

    The PLANNED tier's reduction, split from its load (:func:`_cash_plan`) so the
    two readers in this module share one valuation.

    **What a planned row is WORTH is not re-implemented here.**  Each day's group
    is reduced by the shared
    :func:`~app.services.cash_ledger.sum_projected` -- the same engine the
    shipping period walk and the grid's subtotal row both call, carrying the
    entries-aware reservation for an envelope expense and the live override for a
    salary paycheck or a derived loan debit.  Reducing per GROUP rather than per
    ROW is what keeps that one rule intact: ``sum_projected`` is additive over
    disjoint groups, so the days of a period sum to the period's net exactly --
    which is also why :func:`_budget_legs` may reduce the same rows grouped by
    pay period and get an answer that reconciles with this one to the cent.

    **The reservation reads no clock, and that is the whole of finding N-39's
    resolution.**  This reduction once passed *as_of* as ``sum_projected``'s
    entry-date window, so an entry dated after the reader's now could not reduce
    the reservation early -- a deliberate pick between the two answers the three
    shipping producers gave (the scalar windowed, the grid and the daily ramp did
    not).  Ruling R-M closed the fork at the SOURCE rather than picking a winner:
    plan step X-c0 refuses ``entry_date > display_today()`` at both write doors,
    after which the window could drop nothing, and plan step X-c2c1 deleted it.
    So a planned row's WORTH is now a function of the row alone and *as_of*
    survives in this module for exactly one job -- ruling R-G's clamp, which
    :func:`_cash_plan` applied when it keyed these groups by day.

    Args:
        plan: The account's :class:`_CashPlan`, already keyed onto the day each
            row lands on (ruling R-G's clamp, applied at load).

    Returns:
        ``{day: net}`` -- one entry per day carrying at least one planned row,
        valued as signed income-minus-expense.  ``{}`` for an account with no
        plan.
    """
    nets: "dict[date, Decimal]" = {}
    for day, txns in plan.by_day.items():
        income, expense = sum_projected(txns, plan.basis)
        nets[day] = income - expense
    return nets
