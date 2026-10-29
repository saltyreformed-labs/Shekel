"""Balance-at-T seam -- the GRID kind-aware cash-flow view.

The budget grid is a single-account cash-flow surface (it reads the cash FOLD,
:mod:`._cash_fold`), but it is NOT always pointed at a cash account
(``resolve_grid_account`` falls back to any active account).  For a grid account
that MODELS a return -- an INTEREST-bearing HYSA / Money Market / CD / HSA, an
INVESTMENT, or an APPRECIATING asset -- the pure transaction running-balance
understates the real balance, because it ignores the return the net-worth
surfaces already credit.  This view gives such an account the modelled balance
AND the per-period modelled figures that explain the part of the balance change
the transactions do not -- so the grid's balance row still reconciles with the
rows above it.

**ONE per-period column, from ONE producer pass** (plan steps X-c2b1 / X-c2b2,
ruling R-K).  Every figure the grid renders for one pay period -- the projected
end balance, the income and expense subtotals, the two remainders ("Period
timing" and "Book vs bank"), the modelled contribution and the modelled
accrual -- is one
:class:`GridColumn`, and all but the last two come from a single
:func:`~app.services.balance_at._cash_periods.period_view_of`: one walk, one
plan load, one valuation, grouped on the two clocks the identity binds.

    balance[p] - balance[p-1]
        == net[p] + elsewhere[p] + period_timing[p] + book_vs_bank[p]
           + contribution[p] + accrual[p]

The grid used to compute those figures in three independent producer passes a
test then had to keep in step (finding N-48), against a subtotal that counted
only still-UNPAID rows while the balance counted the anchor plus the same rows
-- an identity that held only because neither side could see a settled row at
all, and that broke on 8 of 59 real period pairs (worst ``$2,505.17``) the
moment the balance became a fold (finding N-41).  Reading one row set grouped
two ways makes the identity a property of the object the template reads.

**The subtotals are the PAYCHECK's, across the owner's cash-flow set, and the
balance is ONE account's -- so the identity carries ``elsewhere[p]``** (developer
ruling ``credit_card:R-CC16``, plan step CC-4-1).  A plan item's ``account_id``
is the account its money is expected to move through, the phone bill that is
always paid by card being a row ON the card; the grid reads checking and its
cards as one set (:class:`~app.services.cash_flow_set.CashFlowSet`), so
``income`` / ``expense`` / ``net`` sum every member's rows -- the balance
account's through :func:`._cash_periods.period_view_of` and each other member's
through :func:`._cash_periods.paycheck_legs`, each off ITS OWN assembled fold
-- while ``balance`` stays the balance account's.  What the paycheck budgets on
the other members does not move that balance, and ``elsewhere[p]`` names it:
the other members' expense less their income, the sign that makes the identity
read forward.  Rendered "On other accounts", it is the figure the ``Credit``
cheat showed the owner as the Credit rows' total.  It is computed FROM the
other members' legs, never as a residual of the balance change, for the reason
:func:`._cash_periods._assemble_figures` gives for its two remainders.  A
transfer between two members counts ONCE, from the balance line's side
(ruling ``credit_card:R-CC23``; :mod:`app.services.cash_flow_set`).  On the
ruling's example -- phone ``$45`` on the card, grocery ``$500`` on checking, a
``$165`` checking -> card payment in the same paycheck -- Total Expenses reads
``$710``, Net ``-$710``, On other accounts ``+$45``, and checking's balance
moves ``-$665``.  A one-member set (no cards) carries ``elsewhere = 0.00`` in
every column and the R-O rule hides the row, so the RENDER of such an owner's
grid is byte-for-byte what it was before this step (measured on a production
clone, 2026-09-18); the record itself gained the field, and its three
subtotals are now rounded once each over the composed legs rather than read
off the cash column verbatim -- equal under the seam's standing premise that
every leg is cent-quantized.

**The identity carries FOUR terms, not three** (ruling R-W as corrected at
X-g2b, measured at ruling R-AH).  A modelled asset has TWO modelled tiers, and
on the real Empower 401(k) the CONTRIBUTION is the larger of them
(``$9,624.27`` against ``$8,152.58`` over the horizon): the three-term form
breaks on 53 of 59 period pairs, worst ``$181.59`` a column, and the four-term
form on none.  So the seam carries both tiers apart -- they answer different
questions, what the market did and what the user put in
(:func:`._asset_fold.asset_growth_at`) -- and the grid renders them as two rows
rather than one sum, which could otherwise report a gain on an account that lost
money.

**THERE IS NO KIND GATE, and its deletion is what plan step X-g3b was**
(ruling R-W, closing finding N-76).  Every account -- PLAIN, INTEREST,
INVESTMENT, APPRECIATING, and a loan the resolver cannot configure -- reaches
:func:`._asset_fold.resolve`, which is resolved over the very
:class:`~app.services.balance_at._cash_fold.AssembledCashFold` this view regroups
into its cash columns (ONE walk, one plan load, one valuation).  The replay
decides what it decides: an account whose parameters model no return has no
ACCRUAL tier and one whose payroll does not fund it has no CONTRIBUTION tier, so
its columns ARE its cash fold.  A gate here would be a second statement of that
decision -- the shape plan Section 8 rules a defect -- and it was one: it read
``accrual_params(account) is None`` and so admitted exactly the INTEREST kind,
leaving an INVESTMENT or an APPRECIATING asset on the kind-blind cash basis
here while ``/savings`` answered it modelled.  Measured before the cutover, at
the last projected period on BOTH databases: the Empower 401(k) rendered
``$31,070.06`` here against ``$48,846.91`` there, with nothing on screen
explaining the gap.  (Ruling R-W recorded ``$48,712.19`` for the same figure on
2026-07-26; the ``$134.72`` between them is NOT explained here, because it was
not measured -- one day of this account's accrual is nearer ``$10``.  The
figure above is the one this cutover moved, re-measured on the day it shipped.)
The grid's balance now equals ``balance_map``'s on **900 of 900** (account,
period) pairs across both databases, which is the unification stated as a
property rather than as an aspiration.

**The cost this moved, stated per RENDER rather than per call.**  A modelled
grid account's column set now costs its contribution load -- an
investment-params query, a deductions query and a raise-aware gross fetch, the
same load ``/savings`` already pays for the same account -- measured
best-of-five on both databases at ``2.7 -> 14.8 ms`` for an INVESTMENT and
``2.7 -> 3.7 ms`` for an APPRECIATING asset, with PLAIN and INTEREST inside
run-to-run noise.  It is paid by EVERY grid render entry, including
``subtotal_rows``, which reads only ``income`` / ``expense`` / ``net`` off the
result; and the gross fetch loads the pay-period calendar a THIRD time in a
render that has already loaded it twice (findings **N-89** and **N-92**, both
recorded and both waiting on one context memo -- finding **N-93** records what
this step added to them).

**The replay's one fail-loud is now reachable from this surface too.**  A
modelled account carrying ZERO ``AccountAnchorHistory`` rows has no honest day
to open an accrual window on, so :func:`._asset_fold._latest_assertion_day`
raises rather than inventing one -- and deleting the gate widens that from
``/savings`` to ``/grid`` for the INVESTMENT and APPRECIATING kinds (INTEREST
already reached it).  The state is unreachable in production
(``account_service.create_account`` and migration ``cfb15e782f86`` guarantee
every account an opening row) and it is deliberate where it can happen, so no
guard is added here; it is stated because the blast radius moved to the landing
page.

**A loan is the one kind this view answers on the cash basis, and it is the
replay that decides so.**  Its amortization schedule drives its real balance
(principal paydown) while its grid "transactions" are payment transfers recorded
as income -- opposite sign, different magnitude -- so no accrual row reconciles
them.  Ruling D4 refuses a loan at the RESOLVER (``resolve_grid_account`` and
``resolve_analytics_cash_flow_set`` both skip amortizing accounts), so this is a
degenerate safety rather than a supported view; the replay reaches it anyway and
returns the cash fold, because ``_modelled_return`` models nothing for the
AMORTIZING kind.  An AMORTIZING account with no ``LoanParams`` lands in the same
place and belongs there.
"""

from collections import OrderedDict
from collections.abc import Iterable
from dataclasses import dataclass
from decimal import Decimal

from app.services.cash_flow_set import CashFlowSet, far_legs_of
from app.services.pay_calendar import DerivedPeriod, PeriodWindow
from app.utils.money import round_money

from ._context import BalanceContext
from . import _asset_fold, _cash_fold, _cash_periods
from ._inputs import _contribution_inputs_for_account, _require_scenario

_ZERO_MONEY = Decimal("0.00")


@dataclass(frozen=True)
class GridColumn:  # pylint: disable=too-many-instance-attributes
    """Every figure the grid renders for ONE pay period.

    The per-period unit of :class:`GridBalanceView`, and ruling R-K's row set
    expressed as one record: the same valued rows grouped on the budget clock
    (:attr:`income` / :attr:`expense` / :attr:`net`), what the paycheck budgets
    on the set's OTHER accounts (:attr:`elsewhere`), what the cash clock adds
    on top of that (:attr:`period_timing`), what the user's own balance
    readings booked (:attr:`book_vs_bank`), the two modelled tiers
    (:attr:`contribution` and :attr:`accrual`), and the balance all six roll
    forward to (:attr:`balance`).

    Pylint: ``too-many-instance-attributes`` (9/7) -- suppressed because this
    is the flat per-period bundle the grid's footer renders row by row
    (``columns[period.period_id].<figure>``, one row per attribute); every
    field is a line on screen and the identity below names all of them, so
    nesting a sub-bundle would add an access level no template reads as a unit
    while splitting one visible row set across two objects.  It reached 8 at
    plan step S1-c, when ruling R-DH (f) split the single "Timing & true-ups"
    remainder into the two figures a user can actually act on, and 9 at plan
    step CC-4-1, when the subtotals became the paycheck's across the owner's
    cash-flow set and the balance stayed one account's.

    Attributes:
        balance: The projected end balance the surface displays, cent-quantized
            -- the modelled balance for an account that models a return, the
            folded cash balance for every other kind.  Never ``None``: the fold
            is TOTAL, so every requested period has one.  (It was optional while
            the projection carried the anchor forward and omitted every
            pre-anchor period; on the real Checking account eight columns
            rendered ``--`` for periods it plainly held money in.)
        income: The period's income subtotal, cent-quantized -- every row
            ATTRIBUTED to the period, settled at its confirmed cash leg and
            still-projected at its live or entries-aware amount (ruling R-K).
        expense: The same, for expense rows, so the column reads ``income``
            minus ``expense``.  A magnitude in the ordinary case and not a
            bound -- see
            :attr:`~app.services.balance_at._cash_periods.CashPeriodFigures.expense`,
            which this reads unchanged, for the two shapes that invert one.
        net: ``round_money(income - expense)`` -- rounded ONCE at the boundary
            rather than as the difference of two separately-rounded legs,
            because it is the figure the balance roll-forward has to reconcile
            with.  **The three subtotals are the PAYCHECK's** (ruling
            ``credit_card:R-CC16``): every member of the owner's cash-flow set
            contributes its rows, the balance account's through
            :func:`._cash_periods.period_view_of` and each card's through
            :func:`._cash_periods.paycheck_legs`.
        elsewhere: What the paycheck budgets on the set's OTHER accounts --
            their expense less their income, cent-quantized -- rendered "On
            other accounts".  The term that reconciles a paycheck-wide
            ``net`` with a one-account ``balance``: ``net + elsewhere`` is the
            balance account's own budget-clock net.  ``0.00`` in every column
            for an owner with no card, and for a balance line outside the set.
            Computed from the other members' legs, never as a residual.
        period_timing: Ruling R-K's remainder from the ROWS, rendered as
            "Period timing": money budgeted to this period that moved in
            another (or has not moved yet), and money that moved here but is
            budgeted elsewhere.
        book_vs_bank: Ruling R-K's remainder from the ASSERTIONS, rendered as
            "Book vs bank": what each balance true-up inside the period booked
            -- the gap between what the app had recorded and what the bank
            actually showed.
        contribution: The period's modelled CONTRIBUTION -- what the account's
            payroll puts in, employee plus employer (the read-only
            "Contributions" row).  ``0.00`` for every kind but INVESTMENT, whose
            feed is the only one that exists
            (:func:`._asset_contributions.contribution_events`).
        accrual: The period's modelled RETURN -- interest, market growth or
            appreciation (the read-only accrual row, whose LABEL the route
            resolves per kind, ruling R-AI).  ``0.00`` for an account that
            models none.

    **Neither modelled field is optional, and that is ruling R-AJ (c).**  Under
    one replay every requested period carries a ``Decimal`` for both, so
    ``Decimal | None`` is a state the producer cannot be in -- and a template
    guarding against it is a guard against an impossible shape, which reads as
    coverage and is not.  ``interest: Decimal | None`` is what this field was
    until plan step X-g3a; it was optional because the accrual arrived as a map
    that covered only the periods an INTEREST account's layering pass produced.
    """

    # The four subtotal figures are the cash view's verbatim, so this record
    # and :class:`~app.services.balance_at._cash_periods.CashPeriodFigures` share
    # five field declarations.  Composing instead (``GridColumn.cash``) was
    # REJECTED: it would put TWO balances on the one object the templates read
    # -- the kind-blind cash balance beside the displayed modelled one
    # -- which is precisely the "two producers on one screen" shape this arc
    # exists to end, and a template reaching the wrong one would render a
    # silently wrong figure.  Inheriting was rejected for the same reason one
    # level up: a subclass whose ``balance`` means something the parent's does
    # not is a substitution defect, and the two carry DIFFERENT identities
    # (``net + the two remainders`` there, ``+ contribution + accrual``
    # here).
    # There is no shared BEHAVIOUR to extract -- only names -- and the two
    # contracts are free to diverge (this one is what the grid renders; that one
    # is what the fold produces).
    # Pylint: ``duplicate-code`` -- incidental field-name overlap with
    # ``_cash_periods.CashPeriodFigures``; one-sided disable so the producer's own
    # declaration stays un-disabled.
    # pylint: disable=duplicate-code
    balance: Decimal
    income: Decimal
    expense: Decimal
    net: Decimal
    period_timing: Decimal
    book_vs_bank: Decimal
    # pylint: enable=duplicate-code
    elsewhere: Decimal
    contribution: Decimal
    accrual: Decimal


@dataclass(frozen=True)
class GridRowFlags:
    """Which CONDITIONAL rows a given visible window renders.

    Ruling R-O's visibility rule, stated ONCE for all three rows it governs: a
    conditional row is present for the whole visible window when at least one
    visible column carries a non-zero value, and shows its own figure (``$0.00``
    included) in every column of that window.  Rejected at the ruling: always-on
    (a permanently-zero row on the forward-looking windows, which are the ones
    most used) and past/current-only (an all-zero PAST window then reads as "not
    measured" rather than "nothing to explain").

    The rule lives here rather than in each template because it is the same
    rule for three rows on four different windows (the visible grid, the Plan
    tab, the mobile This Period card, and the two self-refresh partials), and a
    template that decided it per surface is how one form factor ends up
    rendering a balance its own figures cannot explain (ruling R-P).

    **It is a METHOD on the view rather than a free function in the grid's
    presentation service, and that is the deliberate half.**  The rule is a
    predicate over the view's OWN columns, so a free
    ``row_flags(columns, periods)`` would take as an argument the one thing a
    caller can get wrong -- hand it a different account's columns, or the
    window it is not about, and it answers confidently and wrongly.  That is
    the shape plan Section 8 rules a defect rather than a contract (the fold
    once TOOK the period list its visibility rule needed, and a caller passing
    a window moved a balance by ``$150,000.00``).  Asked of the view, it cannot
    be asked about anything else.  It carries no money and decides no figure --
    only whether a row the seam already computed appears -- so it is not a
    balance producer wearing a presentation hat.

    The fields are declared in the order the rows RENDER, which is the order
    the replay resolves them in (ruling R-AH): a contribution lands on its pay
    period's ``start_date`` and the day's accrual is then taken on the balance
    that day ENDS holding, so the money is contributed and then earns.

    **Each of the two remainder rows carries its OWN flag** (ruling R-DH (f)).
    They were one row and one flag until plan step S1-c.  Sharing a flag would
    have been cheaper and is wrong for the same reason the split itself is: a
    period that carries only true-ups would render a permanently-``$0.00``
    timing row beside them, which reads as "measured and zero" for a fact that
    was never in question.  R-O's rule is per ROW, so it is asked per row.

    **The "On other accounts" row takes the same rule** (plan step CC-4-1): it
    is a reconciliation row like the two remainders, present for the window
    when any visible column budgets something on another member of the
    owner's cash-flow set, and absent -- not a permanently-``$0.00`` line --
    for the owner with no card, which is why the grid of such an owner is
    byte-for-byte what it was before the term existed.

    Attributes:
        elsewhere: Whether the "On other accounts" row renders.
        period_timing: Whether ruling R-O's "Period timing" row renders.
        book_vs_bank: Whether the "Book vs bank" row renders.
        contribution: Whether the "Contributions" row renders.
        accrual: Whether the modelled-return row renders (labelled "Interest" /
            "Growth" / "Appreciation" by the route, ruling R-AI).
    """

    elsewhere: bool
    period_timing: bool
    book_vs_bank: bool
    contribution: bool
    accrual: bool


@dataclass(frozen=True)
class GridBalanceView:
    """Kind-aware cash-flow-surface projection for the budget grid.

    The single view the budget grid reads, regardless of the grid account's
    kind: its balances are the MODELLED ones for every kind, beside the
    per-period tiers that keep the grid's rows reconciling with them.  An
    account that models nothing -- a plain checking account, an unconfigured
    HYSA, a loan -- resolves no tier, so its balances ARE its cash fold; that
    is the replay's answer rather than a separate branch, and it is why the
    grid and ``/savings`` agree on 900 of 900 (account, period) pairs.

    It carried the opposite contract until plan step X-g3b -- "for every kind
    EXCEPT interest-bearing its balances are identical to
    :func:`~app.services.balance_at.cash_balance_map`" -- and that entry is now
    exactly the one a reader must NOT reach for a modelled account, because the
    two answer it differently by design (ruling R-W).

    Attributes:
        columns: ``OrderedDict`` period_id -> :class:`GridColumn`, in payday
            order.  EVERY period of the pass's reported window is present, with
            a real balance beside its real subtotals -- which is why this is one
            map rather than a balance map that omitted periods and a subtotal
            map that did not.
    **The ``amount_overrides`` field is GONE (plan step X-au-d)**, with the
    producer behind it: see :class:`._cash_periods.CashPeriodView` for the
    census that found nothing had read it since plan step X-au-c2b routed the
    grid through the amount model's own map.  Ruling **R-Q** -- a cell and the
    balance row beside it price one row one way -- is unchanged and is now
    structural: both read ``cash_ledger.amounts_by_key`` over the pass's basis,
    and a derived row has no second figure to disagree with.
    """

    columns: "OrderedDict[int, GridColumn]"

    def row_flags(self, periods: "Iterable[DerivedPeriod]") -> GridRowFlags:
        """Return which conditional rows *periods* renders (ruling R-O).

        The one place a caller still names periods, and deliberately: this
        decides which ROWS a given VISIBLE window renders, so the window is the
        question rather than an input to the projection.  The columns
        themselves are the pass's own (plan step C2-c).

        **It takes DERIVED periods rather than the ORM rows it took until plan
        step C2-f2b**, which is what removed the last ``PayPeriod`` name from
        this package.  It takes an ITERABLE of them rather than the
        :class:`~app.services.pay_calendar.PeriodWindow` its callers happen to
        hold, and an adversarial design review of that step is why: this rule
        reads ``period_id`` and nothing else, so a window's two guarantees --
        order and contiguity -- take no part in the answer, while the one
        guarantee that WOULD matter here is the one that type does not carry.
        A ``PeriodWindow`` has no ``user_id`` (``PayCalendar`` does, and its
        docstring says why), so it cannot refuse another owner's periods
        against these columns, which is the only way this method can be asked a
        wrong question.  Demanding the stronger type bought a contiguity check
        the rule ignores, put a raising constructor on a display path, and left
        :func:`~app.services.grid_view_service.build_matched_by_row_period` --
        handed the SAME value one line away in the route -- typed to the
        element while this was typed to the container.  One value, one type.

        Args:
            periods: The visible pay periods, in any order.  Only their
                ``period_id`` is read.  Periods absent from :attr:`columns`
                contribute nothing -- the Plan tab's window reaches past the
                grid's, and a projected period carries no ``period_id`` a
                column could be keyed under.

        Returns:
            The window's :class:`GridRowFlags`.
        """
        columns = [
            self.columns[period.period_id] for period in periods
            if period.period_id in self.columns
        ]
        # ``!= ZERO`` on every arm, with no ``None`` member beside it: both
        # modelled fields are total ``Decimal``s since plan step X-g3a (ruling
        # R-AJ (c)).  The comparison itself IS ruling R-O's visibility rule and
        # stays load-bearing -- only the impossible-shape half went.
        return GridRowFlags(
            elsewhere=any(
                column.elsewhere != _ZERO_MONEY for column in columns
            ),
            period_timing=any(
                column.period_timing != _ZERO_MONEY for column in columns
            ),
            book_vs_bank=any(
                column.book_vs_bank != _ZERO_MONEY for column in columns
            ),
            contribution=any(
                column.contribution != _ZERO_MONEY for column in columns
            ),
            accrual=any(
                column.accrual != _ZERO_MONEY for column in columns
            ),
        )


def _elsewhere_legs(
    cash_flow: CashFlowSet,
    ctx: BalanceContext,
    window: PeriodWindow,
) -> "dict[int, tuple[Decimal, Decimal]]":
    """Return ``{period_id: (income, expense)}`` budgeted on the set's other members.

    One assembled fold per other member -- its own walk, plan and valuation,
    the ONE producer of a budget leg (rule 14) -- reduced by
    :func:`._cash_periods.paycheck_legs`, less the far legs of the set's
    intra-set transfers (ruling ``credit_card:R-CC23``), which
    :func:`~app.services.cash_flow_set.far_legs_of` answers ONCE for every
    member here.  Summed across members, SIGNED and UNROUNDED, in
    :func:`._cash_periods._budget_legs`'s contract; the caller rounds once.

    Args:
        cash_flow: The set; its :attr:`~app.services.cash_flow_set.CashFlowSet.others`
            are the members summed.
        ctx: The read pass.
        window: The reported periods.

    Returns:
        The summed legs, total over *window* (zeros for no other member).
    """
    income = {period.period_id: _ZERO_MONEY for period in window}
    expense = {period.period_id: _ZERO_MONEY for period in window}
    far = far_legs_of(cash_flow)
    for member in cash_flow.others:
        legs = _cash_periods.paycheck_legs(
            _cash_fold.assembled_fold(member, ctx), window, far,
        )
        for period_id, (member_income, member_expense) in legs.items():
            income[period_id] += member_income
            expense[period_id] += member_expense
    return {
        period_id: (income[period_id], expense[period_id])
        for period_id in income
    }


def _assemble_columns(
    window: PeriodWindow,
    figures: "OrderedDict[int, _cash_periods.CashPeriodFigures]",
    modelled: "OrderedDict[int, _asset_fold.AssetPeriodFigures]",
    elsewhere: "dict[int, tuple[Decimal, Decimal]]",
) -> "OrderedDict[int, GridColumn]":
    """Combine each period's cash, modelled and elsewhere figures into one :class:`GridColumn`.

    **The subtotals are composed here and rounded once** (plan step CC-4-1):
    the balance account's figures arrive cent-quantized from
    :func:`._cash_periods._assemble_figures`, and the other members' legs are
    sums of cent-quantized legs, so every ``round_money`` below is a no-op on
    real data and ``round(a) + round(b) == round(a + b)`` holds -- the same
    argument that function makes for its two remainders, and what keeps the
    identity exact on the DISPLAYED figures: ``net + elsewhere`` equals the
    balance account's own ``cash.net`` to the cent, so
    ``balance[p] - balance[p-1] == net + elsewhere + period_timing +
    book_vs_bank + contribution + accrual`` is a property of this record.

    Args:
        window: The pay periods to report.
        figures: The period view's
            :class:`._cash_periods.CashPeriodFigures` per period (the balance
            account's budget-clock subtotals and ruling R-K's two remainders).
            Total over *window*.
        modelled: The :class:`._asset_fold.AssetPeriodFigures` per period -- the
            DISPLAYED balance and the two modelled tiers.  Total over *window*,
            so a missing key is a defect rather than a blank column; it is
            indexed, not ``.get``.
        elsewhere: The other members' ``(income, expense)`` per period
            (:func:`_elsewhere_legs`), signed and unrounded.  Total over
            *window*; indexed for the same reason.

    Returns:
        ``OrderedDict`` period id -> :class:`GridColumn`, one per requested
        period.
    """
    columns: "OrderedDict[int, GridColumn]" = OrderedDict()
    for period in window:
        cash = figures[period.period_id]
        tiers = modelled[period.period_id]
        other_income, other_expense = elsewhere[period.period_id]
        income = round_money(cash.income + other_income)
        expense = round_money(cash.expense + other_expense)
        columns[period.period_id] = GridColumn(
            balance=tiers.balance,
            income=income,
            expense=expense,
            net=round_money(income - expense),
            period_timing=cash.period_timing,
            book_vs_bank=cash.book_vs_bank,
            elsewhere=round_money(other_expense - other_income),
            contribution=tiers.contribution,
            accrual=tiers.accrual,
        )
    return columns


def grid_balance_view(
    cash_flow: CashFlowSet, ctx: BalanceContext,
) -> GridBalanceView:
    """Return the kind-aware cash-flow-surface view for a cash-flow set.

    The single entry the budget grid reads to project its column set.  ONE
    :func:`~app.services.balance_at._cash_fold.assembled_fold` of the set's
    BALANCE account supplies its balance and its own subtotals:
    :func:`._cash_periods.period_view_of` regroups it into the income and
    expense subtotals and ruling R-K's remainder, and
    :func:`._asset_fold.resolve` resolves the modelled tiers over the SAME
    record for the balance, the accrual and the contribution.  Each OTHER
    member of the set (ruling ``credit_card:R-CC16``; plan step CC-4-1) is
    assembled ONCE too and contributes its rows to the subtotals through
    :func:`._cash_periods.paycheck_legs`, its intra-set far legs excluded
    (ruling ``credit_card:R-CC23``).  So

        balance[p] - balance[p-1]
            == net[p] + elsewhere[p] + period_timing[p] + book_vs_bank[p]
               + contribution[p] + accrual[p]

    is a property of the construction rather than an invariant a test polices
    across three independent producer passes (finding N-48).

    **It takes the SET as one value** (the read pass is the other): the route
    hands over what the resolver built, and a single-account reader -- the
    cross-page equality tests, the anchor surfaces, the balance baseline
    harness -- says so with :meth:`~app.services.cash_flow_set.CashFlowSet.single`,
    a legitimate set of one under which ``elsewhere`` is ``0.00`` in every
    column and the subtotals are that account's own.  It took ``(account,
    ctx)`` until plan step CC-4-1; a first cut kept that signature and grew an
    ``others=()`` tail, so the route destructured the set it held and this
    rebuilt it -- one value, two spellings across a boundary, which the
    leaf's adversarial review named.

    **The sharing is the point, and it is what plan step X-g2a built**
    (Section 4's constraint under ruling R-AA).  Reaching the replay through its
    own entry (:func:`._asset_fold.asset_period_view`) would have walked the
    account, loaded its plan and valued its rows a SECOND time -- undoing plan
    step X-c1's "one walk, one plan load, one valuation, whichever reader is
    asking" for the sake of one extra tier.

    **EVERY kind reaches the replay** (ruling R-W, plan step X-g3b) and it is the
    replay that decides what each one models: an ACCRUAL tier only for an account
    whose own parameters carry a rate, a CONTRIBUTION tier only for an INVESTMENT
    whose payroll funds it (:func:`._asset_contributions.contribution_events`
    returns ``[]`` for every other kind -- an HYSA's payroll does not fund it).
    A PLAIN account resolves neither, so its columns ARE its cash fold: the same
    statement this module's old kind gate made by branching, now made by the
    producer.

    **The account's REAL contribution feed is loaded here, and it is the whole
    INVESTMENT half** (ruling R-AJ (a)).  :func:`._asset_fold._modelled_return`
    reads the CALLER's ``investment_params`` on the INVESTMENT arm, while the
    INTEREST and APPRECIATING arms read the account's own params row --
    so passing ``ContributionInputs.absent()`` here, as this entry did until the
    cutover, would model NO return at all for the whole kind, not merely no
    contribution.  It loads through :func:`._inputs._contribution_inputs_for_account`,
    the same entry the scalar and the growth chip call, so the app keeps ONE
    definition of what an account's payroll puts in.  Cost, measured best-of-five
    with a fresh context per run on both databases: an INVESTMENT grid account
    ``2.7 -> 14.8 ms`` (the deductions query plus the raise-aware gross fetch,
    the same load ``/savings`` already pays for the same account), an
    APPRECIATING one ``2.7 -> 3.7 ms``, and the real Checking within run-to-run
    noise at ``~100 ms`` (804 rows; the walk dominates).

    **The accrual is a producer's answer, not a residual** (plan step X-c2b2,
    finding N-52).  It used to be the period-to-period delta of the PREMIUM
    between two independently computed balance maps, which meant any
    disagreement between those maps rendered as interest EARNED: measured on the
    real Money Market, folding the cash map while the accrual still seeded off
    the retired ``current_anchor_balance`` cache would have shown ``$2,007.01`` of
    interest in the current column -- the ``$2,000.00`` of settled money the
    cache never saw, relabelled (finding N-49).  Both halves now come off ONE
    resolved step list, so the row is the accrual map itself.

    **ONE income basis, and nobody chooses it** (ruling R-Q).  The live override
    map -- recomputed salary income and derived loan debits -- is built inside
    the fold over the account's own plan and returned on the result, so the
    grid's CELLS render from the same map its balance row folded.  It used to be
    an argument threaded through two walks whose ``None``-handling differed by
    kind, which is how one account could be valued on two income bases.

    Args:
        cash_flow: The owner's :class:`~app.services.cash_flow_set.CashFlowSet`.
            Its balance account is projected (any kind: the replay reads its
            parameters to decide which modelled tiers it has, and no branch
            here consults its kind); its other members' rows join the
            subtotals and their balances do not.
        ctx: The read pass's :class:`~app.services.balance_at.BalanceContext`.
            **Its ``reported_periods()`` is the column set** since plan step
            C2-c -- the owner's whole saved calendar, in payday order, each
            period valued off its OWN derived span rather than re-based on the
            window's left edge.  The route used to pass that set in, having
            read it out of the table with its two derived columns attached.

    Returns:
        A :class:`GridBalanceView`.

    Raises:
        BaselineMissingError: When ``scenario`` is None.  A ``ValueError``
            subclass; ONE application-level handler answers it (plan step
            X-v2, ruling R-BW), so no caller pre-checks.
        PayCalendarError: The owner's paydays cannot define a calendar, which
            since plan step C2-c is reachable from every per-period seam entry
            rather than only from the recurrence pages -- see
            :meth:`~app.services.balance_at.BalanceContext.calendar`, where the
            reporting domain is derived, for the one state that produces it and
            the step that removes it.
    """
    _require_scenario(ctx)
    window = ctx.reported_periods()
    if not window:
        # A user with no pay periods has no columns to render and no rows to
        # price, so the override map describes nothing.  Early-out rather than
        # asking the replay below for a horizon it cannot derive from an empty
        # window -- the same guard :func:`._asset_fold.asset_period_view` and
        # :func:`._asset_fold.period_columns` already carry.
        return empty_grid_view()
    account = cash_flow.balance
    folded = _cash_fold.assembled_fold(account, ctx)
    view = _cash_periods.period_view_of(folded, window)
    modelled = _asset_fold.period_columns(
        # No calendar is passed: the contribution tier reads the one ``folded``
        # was CLAMPED by (pay-calendar plan step C4-a-1).  It was ``ctx.calendar()``
        # here, which was the pass's own and therefore right -- but only because
        # this call site named one ``ctx`` twice, which is the pairing plan step
        # X-i4 removed for the account and X-au-c2b for the scenario.  Plan step
        # C2-f2a's ruling that the tier takes a CALENDAR and never a WINDOW is
        # unchanged; ``window`` above is that same calendar's ``saved()`` view.
        _asset_fold.resolve(
            account, folded,
            max(period.end_date for period in window),
            _contribution_inputs_for_account(account, ctx),
        ),
        window,
    )
    return GridBalanceView(
        columns=_assemble_columns(
            window, view.columns, modelled,
            _elsewhere_legs(cash_flow, ctx, window),
        ),
    )


def empty_grid_view() -> GridBalanceView:
    """Return the view for a user with no accounts at all.

    The grid renders for a user whose account set is empty (nothing to point
    ``resolve_grid_account`` at), and its templates then have no column to read.
    Returning the empty view from the SEAM rather than constructing one at each
    of the grid's four render entries keeps the no-account shape a property of
    this module -- a route that assembled its own would be a second definition
    of what an absent projection looks like, and the three self-refresh
    endpoints each had one.

    Returns:
        A :class:`GridBalanceView` with no columns.
    """
    return GridBalanceView(columns=OrderedDict())
