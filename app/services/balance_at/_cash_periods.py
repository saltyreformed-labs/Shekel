"""Balance-at-T seam -- the CASH fold regrouped into per-period COLUMNS.

Ruling R-K's half of :mod:`._cash_fold`: the same assembled fold, read a second
way.  The fold answers "what is the balance on date D" off one running total;
this module answers "what does one pay-period COLUMN say" by grouping the very
same valued rows on the BUDGET clock beside it, so the grid's balance row and
its subtotal rows reconcile by construction rather than by a test holding two
producers in step.

**Why it is its own module (plan step S1-c).**  It lived inside ``_cash_fold``
until ruling R-DH (f) split the remainder in two, at which point that module
passed the 1,000-line ceiling.  Growing past a gate is a signal, not a nuisance:
assembling a running total and regrouping it into columns are two jobs, they
share exactly one input (the :class:`~._cash_fold.AssembledCashFold`), and the
dependency runs one way -- this module imports the fold and the fold imports
nothing here.  Splitting on that seam is what the ceiling was measuring.

The identity every column satisfies, in terms of
:class:`CashPeriodFigures`::

    balance(p.end) - balance(p.start - 1 day)
        == net + period_timing + book_vs_bank

(the boundary form, so the FIRST period is covered too -- it has no predecessor
column to subtract).  A modelled account adds two more terms above this layer;
see :mod:`._grid`.

Boundary discipline (``CLAUDE.md``): no Flask symbol, no writes; all money is
:class:`~decimal.Decimal`.
"""

from collections import OrderedDict, defaultdict
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from app.models.transaction import Transaction
from app.services.cash_flow_set import FarLegs
from app.services.cash_ledger import (
    CashLedgerWalk,
    sum_projected,
)
from app.services.transfer_legs import TransferLeg
from app.services.pay_calendar import PeriodWindow
from app.utils.money import round_money

from ._assertions import CashAnchorCorrection
from ._cash_fold import (
    AssembledCashFold,
    _CashPlan,
    period_balances,
)

_ZERO_MONEY = Decimal("0.00")


@dataclass(frozen=True)
class CashPeriodFigures:
    """One pay period's cash column: the balance, the subtotals, the remainders.

    The per-period output of :func:`period_view_of`, and the grid rows ruling
    R-K makes ONE row set grouped two ways.  For every period and every account
    kind, in terms of :attr:`balance` below::

        balance(p.end) - balance(p.start - 1 day)
            == net + period_timing + book_vs_bank

    (the boundary form, so the FIRST period is covered too -- it has no
    predecessor column to subtract).  That identity is a property of the
    construction, not a coincidence: :attr:`net` sums the rows attributed to the
    period on the BUDGET clock, :attr:`period_timing` is what the same rows
    contribute on the CASH clock MINUS that budget sum, :attr:`book_vs_bank` is
    what the user's own balance assertions booked inside the period, and the
    balances are the fold of those very steps.  Verified on the prod-shape
    clone 2026-07-25 over 360 (account, period) pairs -- 6 non-loan accounts x
    60 periods -- with zero breaks.

    An INTEREST account's modelled accrual is NOT in :attr:`balance` (this is
    the kind-blind CASH-FLOW view), so a grid layering it back on adds R-K's
    remaining terms and reads ``balance[p] - balance[p-1] == net[p] +
    period_timing[p] + book_vs_bank[p] + contribution[p] + accrual[p]``.

    **The remainder is TWO figures, and that is ruling R-DH (f).**  It was one
    -- rendered "Timing & true-ups" -- and on the developer's own data it read
    ``-$4,588.69`` in a single column, of which ``-$4,161.47`` was a plug the
    engine had booked against its own double count.  Even correct it summed two
    unrelated facts: money landing in a different column from the one it was
    budgeted to, and the gap between what the app had recorded and what the
    bank actually held.  They have different causes and different fixes, so a
    user cannot act on their sum.  Split, each is a diagnostic:
    :attr:`period_timing` should read ``$0.00`` whenever every bill's money
    moves inside the period it was budgeted to, so a persistently non-zero
    value means a bill is budgeted to the wrong period or dates are being
    recorded late; :attr:`book_vs_bank` is untracked spend and should be small.

    Attributes:
        balance: The fold's balance at the period's ``end_date``, cent-quantized
            -- assertions replayed, settled cash counted from the day it moved,
            and the still-projected plan clamped forward (ruling R-G).
        income: Every row ATTRIBUTED to this period whose type is income --
            settled at its confirmed cash leg, still-projected at its live or
            entries-aware amount.  A magnitude, cent-quantized.
        expense: The same, for expense rows, so the column reads ``income``
            minus ``expense``.  **A magnitude in the ordinary case and NOT a
            bound**: an expense whose cash leg inverts contributes NEGATIVELY
            here, which :func:`_budget_legs` derives and
            ``test_a_row_counts_on_its_TYPE_row_even_when_its_cash_leg_inverts``
            pins.  A settled envelope whose refunds exceeded its purchases is a
            second way in since ruling **bank_import:R-II**; the word
            *(positive)* stood here until plan step ``bank_import:X-gj-2b-3``
            and contradicted that function 250 lines below it.
        net: ``round_money(income - expense)`` -- rounded ONCE at the boundary
            rather than as the difference of two separately-rounded legs,
            because it is the figure the balance roll-forward has to reconcile
            with.
        period_timing: What the two subtotal rows cannot explain about the
            balance change from the ROWS alone: money BUDGETED here that moved
            in another period (or has not moved yet), and money that MOVED here
            but is budgeted elsewhere.  ``$0.00`` for a period where every row
            settled in its own column -- which is every FUTURE period, so the
            row is conditional on screen.
        book_vs_bank: What the user's balance ASSERTIONS booked inside the
            period -- the difference between what the app had recorded and what
            the bank actually showed, each time they looked.  **EVERY
            assertion, the account's FIRST included** (see
            :func:`_assertion_sums`).  ``$0.00`` for a period with no true-up,
            which is every future period.

            *This said "Excludes the OPENING assertion" until plan step
            X-f3c-3, and the exclusion had been gone since X-f3c-2a: the fold
            stopped swallowing the first correction into its own seed when the
            opening equity became a stored fact, and ``_assertion_sums``
            stopped taking ``corrections[1:]`` in the same commit.  The
            function this sentence CITED said so; the sentence citing it did
            not, so one docstring contradicted the other about which
            assertions a money figure counts.*
    """

    balance: Decimal
    income: Decimal
    expense: Decimal
    net: Decimal
    period_timing: Decimal
    book_vs_bank: Decimal


@dataclass(frozen=True)
class CashPeriodView:
    """A whole account's cash columns, and the income basis they were valued on.

    The output of :func:`period_view_of`.  The override map rides on the
    result rather than being the caller's ARGUMENT (ruling R-Q): it is what the
    projection was actually computed with, so a consumer that renders the
    individual rows beside the columns -- the budget grid -- prices each row off
    the same map its balance row folded, BY CONSTRUCTION.  Passing it in was the
    only way for the two to disagree, and an argument a caller can get wrong is a
    defect rather than a contract (plan Section 8).

    Attributes:
        columns: ``OrderedDict`` period id -> :class:`CashPeriodFigures`, in the
            order the reported window holds them, which is payday order.
            Every period of the window is present.
    **It carried an ``amount_overrides`` map until plan step X-au-d, and
    NOTHING EVER READ IT.**  The field was added so a grid cell and the balance
    row beside it would fold one map (ruling **R-Q**); the grid was routed
    through ``cash_ledger.display_amounts_by_id`` instead at plan step
    X-au-c2b, and this copy was carried through :class:`._grid.GridBalanceView`
    to no consumer -- an AST and grep census over ``app/``, ``app/templates``
    and ``app/static`` found zero reads.  A test asserting its contents said
    "the grid reads ``view.amount_overrides.get(...)`` per row", which had been
    false since that step.  The cutovers then emptied the map itself: a derived
    row stores no figure, so there is nothing for an override to supersede.
    Both the field and the producer behind it go here.
    """

    columns: "OrderedDict[int, CashPeriodFigures]"


def period_view_of(
    folded: AssembledCashFold, window: PeriodWindow,
) -> CashPeriodView:
    """Return the account's cash column for each period of *window* -- ruling R-K.

    ONE valued row set, grouped on TWO clocks, plus the assertion steps.  The
    same walk and the same plan :func:`~._cash_fold.balances_at` folds
    are grouped here a second way -- by the pay period each row was BUDGETED to
    -- so the grid's balance row and its subtotal rows stop being two producers
    that a test has to keep in step and become two readings of one set.

    **It takes an ALREADY-assembled fold**, split from its assembly at plan step
    X-g2a so a reader that needs the cash columns AND something else off the
    same account pays for ONE walk, ONE plan load and ONE valuation rather than
    two.  The grid is that reader: from plan step X-g2b it renders the modelled
    balance -- :func:`app.services.balance_at._asset_fold.resolve` over this very
    :class:`~._cash_fold.AssembledCashFold` -- beside the budget-clock subtotals
    this returns, and calling two entry points would have assembled the account
    twice.  Taking the assembled record rather than the account is what makes
    that sharing STRUCTURAL: the columns and whatever the caller resolves beside
    them are readings of one valued row set by construction, not two producers
    that a test keeps in step.

    **The convenience twin that assembled first is GONE (plan step X-i4).**  A
    ``cash_period_view(account, basis, as_of, window)`` stood beside this and
    had no caller in ``app/`` at all -- the grid, its only production reader,
    already came through here.  It took the account and the pass's own
    derivations as independent arguments (finding **N-354**), which is the
    shape X-i4 removes, and keeping a production function alive for test callers
    alone is what ``CLAUDE.md`` rule 13 forbids.  Its callers now spell
    ``period_view_of(assembled_fold(account, ctx), window)``, where the pass
    binds the account it values.

    **Why the subtotals had to change basis, measured.**  Today's subtotal counts
    only rows that are still UNPAID (``cash_ledger.sum_projected`` filters through
    ``is_projected``), while a balance folded from the facts counts money that
    MOVED.  On the real Checking account that identity breaks on 8 of 59 period
    pairs -- worst ``$2,505.17`` -- and every past column reads ``$0.00`` income
    and ``$0.00`` expenses while thousands of dollars moved through it
    (finding N-41).  So :attr:`~CashPeriodFigures.income` /
    :attr:`~CashPeriodFigures.expense` count EVERY row attributed to the period,
    which is budget-vs-actual and fixes the ``$0.00`` past columns as a side
    effect.  Verified on the prod-shape clone 2026-07-25: the eight past columns
    go from ``$0.00`` to real figures, the current column from ``-$140.63`` to
    ``$3,153.22``, and every FUTURE column is unchanged to the cent (nothing has
    settled there, so the two bases coincide).

    **What the remainders hold, and why they are TWO rows** (ruling R-DH (f)).
    Three things the subtotal rows structurally cannot say, falling into two
    causes.  :attr:`~CashPeriodFigures.period_timing` carries the two that are
    about WHEN money moved: a row that settled outside its own pay period (19 of
    the real account's 130 settled rows; nets to ``$0.00`` across history and
    swings to ``-$2,007.46`` inside one period), and a still-projected row
    ruling R-G clamped forward out of its column.
    :attr:`~CashPeriodFigures.book_vs_bank` carries EVERY balance ASSERTION
    (the first one included since plan step X-f3c-2a, where the opening used to
    be held back because the fold had swallowed its correction into the seed)
    (``-$2,906.31`` net over the 51 that follow the opening on the real
    account, measured 2026-08-01), which are about what the app did not know.
    Rejected at the ruling: leaving the subtotals
    unpaid-only, which turns the remainder into a garbage bucket holding all
    real past activity; shipping no remainder at all, which leaves a visible
    contradiction on the screen; and summing the two, which is what shipped
    until 2026-08-01 and produced a figure with no action attached to it.

    **The OPENING EQUITY is in neither, and since plan step X-f3c-2a the reason
    is structural rather than an exclusion.**  What an account held before its
    records begin is the fold's SEED -- a stored
    ``budget.account_openings`` fact, not a correction -- so it is part of the
    level every period is measured from and steps no period's balance.  The
    first ASSERTION does appear here like any other, because it is now an
    ordinary correction: ``0.00`` where the owner's declaration agrees with the
    books, and a real movement where it does not.

    Kind-blind, exactly as the fold is (ruling R-J): this is the CASH-FLOW view,
    whose balance has to reconcile with the transaction rows rendered beside it.
    An INTEREST account's accrual is layered on ABOVE this by the grid view,
    which is the ``+ accrual[p]`` term of R-K's identity.

    Args:
        folded: The account's :class:`~._cash_fold.AssembledCashFold`
            (:func:`~._cash_fold.assembled_fold`), which carries the account's
            walk, its plan and the scenario and pricing basis both were loaded
            under.  The account's kind is not consulted.
        window: The pay periods to report, as a slice of the owner's ONE
            derived calendar
            (:meth:`~app.services.balance_at.BalanceContext.reported_periods`).
            It need not start at the account's anchor -- each period's figures
            are read off its OWN span, so a window is a window rather than a
            re-based projection -- but it is contiguous and ordered by
            construction, which plan step **C2-c** made a property of the type
            rather than a sentence in this docstring (ledger rows **P14**,
            **P24**, **P32**).

    Returns:
        The :class:`CashPeriodView`: one :class:`CashPeriodFigures` per period
        of *window* (a period with no rows and no assertions reports zeros
        against its folded balance).
    """
    return CashPeriodView(
        columns=_assemble_figures(
            window,
            period_balances(folded, window),
            _budget_legs(folded.walk, folded.plan, window, FarLegs.none()),
            _cash_sums(folded.walk, folded.day_nets, window),
            _assertion_sums(folded.corrections, window),
        ),
    )


def paycheck_legs(
    folded: AssembledCashFold, window: PeriodWindow, far: FarLegs,
) -> "dict[int, tuple[Decimal, Decimal]]":
    """Return a NON-balance member's budget legs -- the grid's composed subtotal.

    The other half of ruling ``credit_card:R-CC16``'s composed subtotal (plan
    step CC-4-1): the budget grid reads the owner's cash-flow set (checking
    and its cards) as ONE paycheck, so its Total Income / Total Expenses / Net
    Cash Flow rows sum every member's rows, while the Projected End Balance
    stays ONE account's.  :func:`period_view_of` answers the balance account;
    this answers each other member, off ITS OWN assembled fold -- the same
    walk, plan and valuation :func:`_budget_legs` reads for the balance
    account, so a card's ``$45`` phone bill is priced by the one reduction
    every reader prices it by (rule 14: one walk per account, one producer
    of a budget leg).

    **It takes the far legs and drops them** (ruling ``credit_card:R-CC23``):
    a transfer between two members shows on the paycheck grid ONCE, from the
    balance line's side, so a card's leg of a checking -> card payment is
    neither a leg the grid draws nor a figure this sums.  The exclusion is
    the same ONE rule the readers' legs apply
    (:func:`app.services.cash_flow_set.leg_accounts_shown`; its row spelling
    ``far_leg_clause`` went at leaf ``balance:X-bi-6-1b``), answered once by
    :func:`app.services.cash_flow_set.far_legs_of` and threaded here, so the
    cells and the subtotal cannot disagree about which items are the
    paycheck's.

    Args:
        folded: The member's :class:`~._cash_fold.AssembledCashFold`.
        window: The reported periods.
        far: The set's :class:`~app.services.cash_flow_set.FarLegs`.

    Returns:
        ``{period_id: (income, expense)}`` -- SIGNED, UNROUNDED, total over the
        window, in the same contract as :func:`_budget_legs`; the grid view
        sums them across members and rounds once at its boundary.
    """
    return _budget_legs(folded.walk, folded.plan, window, far)


def _column_for(window: PeriodWindow, day: date) -> "int | None":
    """Return the id of the reported period whose span contains *day*.

    **The CASH clock's grouping key**, and since plan step C2-c it is one call
    into the pay calendar rather than a fourth index over the stored spans.
    The class it replaced (``_PeriodSpans``) bisected the ``end_date`` COLUMN,
    which is a stored derivative of the paydays with nothing reconciling it
    (``docs/plans/implementation_plan_pay_calendar.md`` section 1); a hole
    between two stored spans therefore dropped a day's money into no column at
    all, silently, which is ``balance:N-128``'s shape reached through a reader.
    Derived periods TILE, so the hole is not a state this can be in.

    A day is answered by the period whose span CONTAINS it and by nothing
    otherwise -- no nearest-period fallback, deliberately.  The FILING rule
    (:meth:`app.services.pay_calendar.PayCalendar.filing_period`) does clamp,
    to the latest period that OPENED on or before the target, which is right
    for the question it answers (every per-period reader must be able to place
    every ledger entry, which ``journal_entries.pay_period_id``'s ``NOT NULL``
    EXPRESSES rather than causes -- ruling **pay_calendar:R-PC53**) and wrong
    for this one: the
    identity these columns satisfy reads a period's balance change as the steps
    inside its OWN span, so a step outside every reported span belongs to no
    column and must not be pulled into the nearest one.  The pay-calendar arc
    names them as two distinct QUESTIONS on one value for exactly this reason.

    Scoped to the WINDOW rather than to the whole calendar, and that is the
    same distinction one level down: what is being grouped is the reported
    column set, so a day above or below it has no column here even where the
    owner's calendar has one for it.

    Args:
        window: The reported periods.
        day: The calendar day to place.

    Returns:
        The containing period's ``budget.pay_periods.id``, or ``None`` when
        *day* falls before the first reported period or after the last one's
        end.
    """
    period = window.containing(day)
    return period.period_id if period is not None else None


def _zeroed(window: PeriodWindow) -> "dict[int, Decimal]":
    """Return a ``{period_id: 0.00}`` accumulator over every reported period.

    Args:
        window: The reported periods.

    Returns:
        One zero per reported period, so a component's dict is TOTAL over the
        window and a period with nothing in it reads ``0.00`` rather than being
        missing.
    """
    return {period.period_id: _ZERO_MONEY for period in window}


def _budget_legs(
    walk: CashLedgerWalk,
    plan: _CashPlan,
    window: PeriodWindow,
    far: FarLegs,
) -> "dict[int, tuple[Decimal, Decimal]]":
    """Return ``{period_id: (income, expense)}`` on the BUDGET clock.

    Every row ATTRIBUTED to a reported period, whatever day its money moved:
    settled rows at the confirmed cash leg the walk already valued them at, and
    still-projected rows -- the account's own, and one derived leg per
    still-projected transfer it is on (plan step **X-bi-6a**) -- at the shared
    ``sum_projected`` reduction -- the same
    engine :func:`~._cash_fold._planned_day_nets` reduces the same items through
    on the other clock, through the same
    :class:`~app.services.cash_ledger.AmountBasis`, which is why the two
    groupings reconcile to the cent.

    **Less the far legs** (ruling ``credit_card:R-CC23``, plan step CC-4-1):
    *far* names the transfer legs this account holds as the NON-balance member
    of a cash-flow set, which the paycheck grid shows from the balance line's
    side and not from here.  Both halves are excluded by their TRANSFER's id:
    a settled one is a fact naming it
    (:attr:`~app.services.cash_ledger.CashSourceFact.transfer_id`, leaf
    ``X-bi-6-4a``; its shadow's ``transaction_id`` until then), a
    still-projected one a :class:`~app.services.transfer_legs.TransferLeg`
    carrying it.  The balance account itself passes
    :meth:`~app.services.cash_flow_set.FarLegs.none` -- every leg it holds is
    the near side by definition -- so :func:`period_view_of`'s columns are
    unchanged by the parameter.

    **A partially-spent envelope therefore counts on BOTH sides of the split,
    and their sum is what the period costs** (ruling **R-FM**, plan step
    X-f3b).  Its posted purchases are settled facts here, at the parent's own
    budget column; the reservation
    (:func:`~app.services.cash_ledger._amounts._entry_checking_impact`) holds
    the rest.  Before X-f3b a purchase was no fact at all, so an envelope
    budgeted ``$100.00`` with ``$45.85`` already taken by the bank contributed
    only its ``$54.15`` reservation to this column -- the spend was inside the
    owner's asserted balance and nowhere in these subtotals.

    The income / expense split follows the transaction TYPE
    (:attr:`~app.services.cash_ledger.CashSourceFact.is_income`), never the sign
    of the row's value, and the difference is observable: a settled expense whose
    ``actual_amount`` was corrected BELOW its credit-card entries has a POSITIVE
    cash leg (it nets money back into checking) and still belongs on the expense
    row, as a negative expense.  An expense that came back is not income.  The
    net and the balance agree either way, which is exactly why the shape is
    pinned -- it is the only one that can tell the two rules apart, and without
    it this classification would be an untested claim
    (``test_cash_period_view/test_the_subtotals.py``:
    ``test_a_row_counts_on_its_TYPE_row_even_when_its_cash_leg_inverts``).

    Args:
        walk: The account's walk -- its settled facts carry both clocks.
        plan: The account's :class:`~._cash_fold._CashPlan`.
        window: The reported periods.
        far: The far legs to leave out, by their transfers.

    Returns:
        ``{period_id: (income, expense)}`` -- SIGNED, UNROUNDED (the caller
        rounds once at the boundary), and total over the window.  Each is a
        magnitude in the ordinary case; the paragraph above states the two
        shapes that invert one.
    """
    income = _zeroed(window)
    expense = _zeroed(window)
    for fact in walk.source_facts:
        if fact.pay_period_id not in income:
            continue
        if fact.transfer_id in far.transfer_ids:
            continue
        if fact.is_income:
            income[fact.pay_period_id] += fact.delta
        else:
            # A settled expense's leg is NEGATIVE (money left), so negating it
            # puts the row on screen the right way up.  **Total over both
            # directions**: a leg that inverted -- a correction below the card
            # entries, or an envelope its refunds carried below zero (ruling
            # **bank_import:R-II**) -- comes out as a NEGATIVE expense, which
            # is the classification this function pins by TYPE.
            expense[fact.pay_period_id] -= fact.delta
    # A transfer LEG files under its PARENT's period (plan step X-bi-6a):
    # ``TransferLeg.pay_period_id`` reads the parent's column, so the
    # one attribute this grouping asks is answered by rows and legs alike.
    by_period: "dict[int, list[Transaction | TransferLeg]]" = (
        defaultdict(list)
    )
    for txn in plan.rows:
        if txn.pay_period_id not in income:
            continue
        if (
            isinstance(txn, TransferLeg)
            and txn.transfer.id in far.transfer_ids
        ):
            continue
        by_period[txn.pay_period_id].append(txn)
    for period_id, txns in by_period.items():
        projected_income, projected_expense = sum_projected(txns, plan.basis)
        income[period_id] += projected_income
        expense[period_id] += projected_expense
    return {
        period_id: (income[period_id], expense[period_id])
        for period_id in income
    }


def _cash_sums(
    walk: CashLedgerWalk,
    day_nets: "dict[date, Decimal]",
    window: PeriodWindow,
) -> "dict[int, Decimal]":
    """Return ``{period_id: net}`` on the CASH clock -- what MOVED in the period.

    The same rows :func:`_budget_legs` groups by budget column, grouped instead
    by the day each one's money moves: a settled row on its ``settled_on``
    (a STORED civil day since plan step X-f1, read once onto the fact; it was
    ``paid_at``'s display-timezone day under ruling R-DH), a planned row on
    the day ruling R-G lands it.
    Assertions are NOT here -- they are :func:`_assertion_sums`, because an
    assertion is not a row.

    Args:
        walk: The account's walk.
        day_nets: The plan's per-day nets
            (:func:`~._cash_fold._planned_day_nets`).
        window: The reported periods.

    Returns:
        ``{period_id: net}`` -- signed, UNROUNDED, total over the window.
    """
    moved = _zeroed(window)
    for fact in walk.source_facts:
        period_id = _column_for(window, fact.settled_on)
        if period_id is not None:
            moved[period_id] += fact.delta
    for day, net in day_nets.items():
        period_id = _column_for(window, day)
        if period_id is not None:
            moved[period_id] += net
    return moved


def _assertion_sums(
    corrections: "list[CashAnchorCorrection]", window: PeriodWindow,
) -> "dict[int, Decimal]":
    """Return ``{period_id: correction}`` -- what the user's assertions booked.

    EVERY assertion correction, on the civil day it was asserted (plan step
    **X-f3c-2a**).

    **The opening used to be excluded and the exclusion is GONE with the thing
    that required it.**  While the fold computed its own seed, the first
    assertion's correction WAS that seed -- back-projected over the records it
    already contained and cancelled on its own day (ruling R-I) -- so counting
    it here would have put a jump in a column the balance never took, and this
    function took ``corrections[1:]`` as the exact complement of the fold's
    ``[0]``.  The seed is a stored fact now
    (:attr:`~app.services.cash_ledger.CashLedgerWalk.opening`), so no correction
    is swallowed, the two functions partition nothing between them, and the
    first assertion books what it is: ``0.00`` where the owner's declaration
    agrees with the books, and a real movement where a BACK-DATED assertion
    disagrees with the recorded opening -- which belongs in this remainder
    exactly as any other disagreement does.

    All the corrections are replayed ONCE per fold
    (:func:`~._assertions.assertion_corrections`) and carried on
    :class:`~._cash_fold.AssembledCashFold`, which is plan step X-f3c-1's doing:
    this reader and the fold's step list read one object rather than two replays
    that could have applied different assertion policies.

    Args:
        corrections: The account's assertion corrections, chronological
            (:attr:`~._cash_fold.AssembledCashFold.corrections`).  All of them
            are counted; there is no longer a first one to hold back.
        window: The reported periods.

    Returns:
        ``{period_id: correction}`` -- signed, UNROUNDED, total over the window.
    """
    asserted = _zeroed(window)
    for correction in corrections:
        period_id = _column_for(window, correction.observed_on)
        if period_id is not None:
            asserted[period_id] += correction.delta
    return asserted


def _assemble_figures(
    window: PeriodWindow,
    balances: "OrderedDict[int, Decimal]",
    legs: "dict[int, tuple[Decimal, Decimal]]",
    moved: "dict[int, Decimal]",
    asserted: "dict[int, Decimal]",
) -> "OrderedDict[int, CashPeriodFigures]":
    """Combine the three groupings into one :class:`CashPeriodFigures` per period.

    The remainders are computed HERE and directly -- ``what moved in the column``
    minus ``what was budgeted to it``, and the assertions -- rather than as the
    leftover of the fold's balance change.  That distinction is the step's whole
    verification standard: a remainder defined as ``balance_delta - net`` makes
    the identity arithmetically true and therefore untestable, and it would
    silently ABSORB a row the budget grouping got wrong.  Computed from the row
    set, the identity is a claim the fold can falsify (Section 7.2).

    **The two remainders are rounded separately, and their sum is still exact**
    (ruling R-DH (f)).  Every leg reaching this point is already cent-quantized
    (stored ``Numeric(12,2)`` columns, and both live-override seams return
    ``round_money``), so each ``round_money`` is a no-op on real data and
    ``round(a) + round(b) == round(a + b)`` holds rather than being assumed --
    which is what lets the identity hold on the DISPLAYED figures, not merely on
    the raw ones, now that the row a user reads is two rows.

    Args:
        window: The pay periods to report.
        balances: The fold sampled at every period ``end_date``, keyed by
            period id (:func:`~._cash_fold.period_balances`).
        legs: The budget-clock ``(income, expense)`` per period.
        moved: The cash-clock net per period.
        asserted: The assertion corrections per period.

    Returns:
        ``OrderedDict`` period id -> :class:`CashPeriodFigures`.
    """
    figures: "OrderedDict[int, CashPeriodFigures]" = OrderedDict()
    for period in window:
        income, expense = legs[period.period_id]
        net = income - expense
        figures[period.period_id] = CashPeriodFigures(
            balance=balances[period.period_id],
            income=round_money(income),
            expense=round_money(expense),
            net=round_money(net),
            period_timing=round_money(moved[period.period_id] - net),
            book_vs_bank=round_money(asserted[period.period_id]),
        )
    return figures
