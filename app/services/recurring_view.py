"""
Shekel Budget App -- Unified Recurring-Definitions View Producer (Loop B, P1)

Single producer behind the unified ``/templates`` (Recurring) surface that
replaces the three parallel pages ``/templates`` + ``/transfers`` +
``/obligations``.  It shapes the user's active recurring definitions
(income, expense, and transfer templates) into one display model: a
summary band, three grouped sections with per-section subtotals, and per
row a defined amount, monthly + per-paycheck equivalents, an
engine-backed next date, and a share of its section's committed total.

Two units, one source of truth
------------------------------
Every monetary figure is produced in BOTH units so the page-wide
Monthly / Per-paycheck toggle can switch without recomputing money in the
template or in JS.  There is exactly one monthly source of truth --
``obligations_aggregator.monthly_or_none`` (E-24 / HIGH-05; the same
producer, one entry up, is behind the /savings emergency-fund baseline) -- and
the per-paycheck value is DERIVED from it by
:meth:`~app.services.pay_calendar.PayCadence.monthly_to_per_paycheck`.  The
toggle therefore only re-expresses the same committed figure in a different
unit; it never opens a second money path that could disagree with the first.

**The conversion is the OWNER's since plan step R7a-2a**, where this module
held it as a module-level ``MONTHS_PER_YEAR / PAY_PERIODS_PER_YEAR`` ratio --
a hardcoded 12/26, so the "per paycheck" column named a paycheck the owner
does not receive unless they are paid biweekly.  The cadence comes off the
:class:`~app.services.pay_calendar.PayCalendar` this module is already handed,
so the page resolves it once and no second load appears.  That change also
dropped a rounding step: the ratio was pre-computed and multiplied (``x *
(12/26)``, inexact twice), where the value's method divides once (``x * 12 /
ppy``).

Next dates are engine-backed, and the cadence phrase comes from the same read
-----------------------------------------------------------------------------
The next occurrence is the date the recurrence engine itself would assign
to the next generated instance: the composed read door
(``recurring_definition.read_definition``) walks the rule's cadence and places
each occurrence on a pay period -- the SAME composition the generation seam
makes since plan step R4b-2 -- and ``recurrence.compute_due_date``
gives the instance's due date, so a row's "next date" is the date the grid
cell it points at would carry.  **The one divergence that stood closed at
plan step R7d-c-2**: generation walked the rule's own bound
(``recurrence_engine/_plan.py``) while this surface read the composed one, so
a loan payment whose derived stop preceded its stored bound showed no next
date here while the grid could still hold generated rows up to the stored
one; since R7d-c-2 both read ``read_definition`` over one pass.  This
retires the ``/obligations`` approximation (``_next_occurrence``) the audit
flagged.

**The Recurrence column is produced here too** (plan step R7a).  It was eight
Jinja branches over the closed ``pattern_id`` set until then;
``recurrence.describe`` words it from the two-axis meaning instead, so the
column survives plan step R7c dropping the columns those branches read.  Each
rule is read ONCE per render THROUGH THE DOOR -- ``read_definition`` returns
the meaning and the placements together -- because the phrase, the next date
and the monthly equivalent are three questions about one reading, and resolving
twice would be a second resolution point in one request.  The monthly
equivalent joined the other two at plan step R7d-e: until then
``template_monthly_or_none`` -> ``has_ended`` resolved and walked a bounded
rule a second time, off the rule's own columns.

**The reading is the COMPOSED one since plan step R7d-d**: what the rule says
AND what its destination allows, held in one
:class:`~app.services.recurrence.Closing` on the resolved value.  A recurring
transfer that pays a loan stops when the debt does, and until that step the
only way that fact reached this surface was a CACHED copy of the loan's derived
payoff written into ``budget.recurrence_rules.end_date`` by ten call sites --
so the cadence sentence and the next date named whichever value a chokepoint
had most recently written (plan ledger row **D35**).

**A loan payment's row names the EARLIER of the loan's derived closing date
and the stop its owner stored**, active or archived, because since plan step
R7d-g the ``end_date`` column holds nothing the app wrote: the chokepoints'
cached payoff was NULLed on the standing payment of every loan and on every
archived transfer into one (ruling **R-R80**), and the writers are gone, so
every stored stop is the owner's word (ruling **R-R82**) and the door composes
it with the derived stop.  For a loan's own payment the column is ordinarily
NULL (the doors refuse a stated stop, rulings **R-R59**, **R-R60**, **R-R77**)
and the row names the payoff -- where the cache was EARLIER than the closing
date (ledger row D35's measured shape, ``2029-01-22`` stored against
``2029-02-22`` derived) the row names ``2029-02-22``; a standing payment that
kept a stop from before it became one names that stop.  Between R7d-d and
R7d-g the ACTIVE payment's row named the payoff through an arm that read the
column as the cache for that one definition (ruling **R-R56**), while the
ARCHIVED drawer still showed the cache as the owner's word; the arm is
deleted with the cache.  The phrase and the next date read ONE value, so they
cannot disagree with each other.

One limit was closed at plan step R7d-e: the MONTHLY EQUIVALENT.
``template_monthly_or_none`` -> ``has_ended`` read the rule's own columns, so
on D35's shape the row showed the derived stop and a next date beside a BLANK
monthly figure for the one installment the derived stop adds past the cached
date, and a RETIRED loan's row stated a monthly figure beside a stop line
saying the money had stopped; the monthly column reads the same door as the
other two since R7d-e, so the three cannot disagree.

**It takes the READ PASS** (:class:`~app.services.balance_at.BalanceContext`),
which carries the owner's whole schedule as a ``PayCalendar`` (plan step
R4b-1) and the ``as_of`` and scenario the destination's stop is folded in --
one pass, so the calendar a rule is resolved against and the pass its derived
stop is measured in cannot be two values that disagree.  Only a route builds
one (the 2026-08-16 ruling); this producer takes it.  A recurrence's first
occurrence is measured against the owner's schedule, so the value this surface
reads has to BE that schedule; taking ORM rows and rebuilding the calendar per
row would be a second producer of one answer, and taking a subset would date a
row from a schedule its owner does not have.

What appears vs what totals
---------------------------
The list is a management surface, so it shows EVERY active definition,
including the non-repeating (rule-less) definitions you still need to edit,
archive, or delete.  The summary band and the section subtotals, however,
sum only genuine recurring commitments -- ``monthly_or_none``
returns ``None`` for rule-less / expired / missing-or-zero-amount templates, so
those rows render with a blank equivalent and contribute nothing to any
total (matching the retired /obligations kernel exactly).

**A transfer into a card is listed and not totalled** (developer ruling
plan#3527, plan task plan#3530).  The card's bills are in the Expenses section,
and the card's payment pays those bills off, so adding both counted each bill
twice: a ``$100`` phone bill every paycheck on the card read Expenses
``$216.67`` and Transfers ``$216.67`` for a net of ``-$433.34``.  Such a row
keeps its Amount and its monthly figure, takes no share, says why it is left
out, and adds nothing to the Transfers subtotal or the band
(:func:`~app.services.account_projection.pays_a_card`).  A fixed amount sent
to the card is left out the same way, which is the ruling's stated cost.  A
payment the card works out has no monthly figure at all, because no one figure
of it repeats, and its Amount is its next row's (plan task plan#3700,
``obligations_aggregator``).

Boundary discipline (``CLAUDE.md`` Architecture): no Flask imports; inputs
are already-loaded ORM template lists (or any duck-typed equivalent, as the
tests build with ``types.SimpleNamespace``) plus the read pass; output is a
frozen dataclass tree of ``Decimal`` / ``date``.  All money math is
``Decimal``; the route/template only display.
"""

from dataclasses import dataclass
from datetime import date
from decimal import ROUND_HALF_UP, Decimal

from app.models.recurrence_rule import RecurrenceRule
from app.models.transfer_template import TransferTemplate
from app.services.account_projection import pays_a_card
from app.services.balance_at import BalanceContext
from app.services.cash_ledger import is_card_payment_definition
from app.services.obligations_aggregator import (
    RecurringTemplate,
    TodaysPrice,
    monthly_or_none,
    template_rule,
    todays_price,
)
from app.services.pay_calendar import DerivedPeriod, PayCadence
from app.services.recurrence import (
    RecurrenceDescription,
    RecurrenceResolutionError,
    ResolvedRecurrence,
    RuleReading,
    describe,
    placed_occurrences,
)
from app.services.recurrence import compute_due_date
from app.services.recurring_definition import (
    read_definition,
    resolved_definition,
)
from app.utils.money import round_money

_HUNDRED = Decimal("100")
_PERCENT_QUANTUM = Decimal("0.1")


@dataclass(frozen=True)
class UnitPair:
    """A monetary figure in both display units.

    ``monthly`` and ``per_paycheck`` are the same underlying commitment
    expressed two ways, each rounded to cents.  Both are ``None`` together
    for a definition that is not a recurring commitment (rule-less, expired,
    or missing/zero amount) -- the page renders a blank equivalent for such a
    row rather than a misleading zero.
    """

    monthly: Decimal | None
    per_paycheck: Decimal | None


@dataclass(frozen=True)
class RecurringRow:
    """One recurring-definition row in a section.

    Attributes:
        template: The ORM template (``TransactionTemplate`` or
            ``TransferTemplate``) this row renders.  Carried whole so the
            template layer can read its name, badges, category / accounts, and
            the edit / archive / delete action links -- none of which the
            producer recomputes.
        equivalent: The monthly + per-paycheck commitment (:class:`UnitPair`),
            both ``None`` for a non-recurring definition.
        recurrence: How the definition repeats, in display terms
            (:class:`~app.services.recurrence.RecurrenceDescription`), or
            ``None`` when it does not repeat -- which is the absence of a
            rule naming the definition, since plan step R2e-3.  Produced
            here rather than in the template (plan step R7a): the phrase is a
            function of what the recurrence MEANS against the owner's
            schedule, and a template holds neither.
        next_date: The engine-assigned due date of the next occurrence on
            or after ``as_of``, or ``None`` when the definition has no
            future recurring occurrence (no rule, or expired).
        share_pct: This row's monthly equivalent as a percentage (0-100) of
            its section's committed monthly total, for the share bar; ``None``
            when the row does not contribute (non-recurring, or
            ``pays_a_card``) or the section total is zero.
        amount: What one occurrence commits on the read pass's day, for the
            Amount column and its sort key
            (:func:`~app.services.obligations_aggregator.todays_price`, ruling
            plan#2693): a salary profile's definition shows TODAY's priced
            paycheck -- before the first saved payday, that first paycheck
            (**R-SAL79**) -- a loan payment amount rule 4's figure, its
            standing extra included, and every other definition its price
            series as of the day.  The template read ``default_amount``
            itself until plan step salary:X-av-2 (rulings **R-SAL71** and
            **R-SAL73**), and this field read it for every definition but a
            salary one until plan step X-bp: the NEWEST stated price, so a
            scheduled rise showed before it took effect.
        pays_a_card: True for a transfer definition whose money goes into a
            card (:func:`~app.services.account_projection.pays_a_card`): its
            Amount and monthly figure stand (a payment the card works out has
            none, plan task plan#3700), and it is in no subtotal, share or band
            figure, because the card's bills it pays are (ruling plan#3527).
            False for every other row.
    """

    template: RecurringTemplate
    equivalent: UnitPair
    recurrence: RecurrenceDescription | None
    next_date: date | None
    share_pct: Decimal | None
    amount: Decimal
    pays_a_card: bool

    @property
    def goes_with_its_card(self) -> bool:
        """Whether this row is a card's worked-out payment, archived only with its card.

        :func:`~app.services.cash_ledger.is_card_payment_definition` asked of
        the row's own definition: such a payment is archived and deleted only
        with its card (task plan#3757), so the page offers it no Archive.  A
        fixed payment into a card is a plain transfer, and ``False``.
        """
        return is_card_payment_definition(self.template)


@dataclass(frozen=True)
class ArchivedRow:
    """One archived recurring definition, for the collapsed Archived drawer.

    Archived definitions carry no monthly equivalent, no next date and no
    share -- they are inactive and excluded from every total -- so they are a
    different value from :class:`RecurringRow` rather than one with four fields
    permanently ``None``.  What they DO carry is how they repeated, which the
    drawer shows so an archived definition can be recognised before it is
    unarchived or permanently deleted.

    Attributes:
        template: The ORM template, carried whole for its name, kind and
            action links.
        recurrence: How it repeats, or ``None`` for a non-repeating
            definition.
        amount: What one occurrence would commit on the read pass's day, by
            the rule that prices its rows
            (:func:`~app.services.obligations_aggregator.todays_price`,
            ruling plan#2693) -- the same figure the active list's Amount
            column shows, so a definition reads one price on either side of
            an archive.  The drawer read the stored ``default_amount`` until
            plan step X-bp.
    """

    template: RecurringTemplate
    recurrence: RecurrenceDescription | None
    amount: Decimal

    @property
    def goes_with_its_card(self) -> bool:
        """Whether this row is a card's worked-out payment, deleted only with its card.

        :attr:`RecurringRow.goes_with_its_card`'s question, asked of an
        archived definition, so the drawer offers it no Delete (task
        plan#3757).
        """
        return is_card_payment_definition(self.template)


@dataclass(frozen=True)
class RecurringSection:
    """One kind-grouped section (income, expenses, or transfers).

    ``rows`` are ordered by monthly-equivalent cost descending (the locked
    default landing order), non-recurring rows last.  ``subtotal`` is the
    section's committed total in both units: the same full-precision
    filter+sum ``obligations_aggregator.committed_monthly`` makes, rounded
    once, over the rows that do not pay a card.  So it equals that aggregator
    for the income and expense sections by construction, and for the
    transfers section less any transfer into a card, which this leaves out
    (ruling plan#3527) and the aggregator keeps: its emergency-fund floor on
    ``/savings`` reads checking's definitions alone, the card's payment among
    them and none of the card's bills, so each counts a bill on the card once.
    """

    rows: tuple[RecurringRow, ...]
    subtotal: UnitPair


@dataclass(frozen=True)
class SummaryBand:
    """The obligations kernel: committed income vs outflow, no projection.

    Measured from the recurring definitions themselves (never a balance
    projection), so it stands in for the retired /obligations monthly lens.

    Attributes:
        income: Committed recurring income, both units.
        expenses: Committed recurring expenses, both units.
        transfers_out: Committed recurring transfers, both units, less any
            into a card (ruling plan#3527; :attr:`RecurringRow.pays_a_card`).
        net: ``income - expenses - transfers_out`` per unit, computed from
            the rounded section subtotals so the tile equals what the shown
            figures subtract to.
        expenses_pct_of_income: Expenses as a percentage (0-100) of income,
            or ``None`` when income is zero (no ratio to show).
    """

    income: UnitPair
    expenses: UnitPair
    transfers_out: UnitPair
    net: UnitPair
    expenses_pct_of_income: Decimal | None


@dataclass(frozen=True)
class RecurringView:
    """The full display model for the unified Recurring surface."""

    income: RecurringSection
    expenses: RecurringSection
    transfers: RecurringSection
    band: SummaryBand


def _unit_pair(
    monthly_full: Decimal | None, pay_cadence: PayCadence,
) -> UnitPair:
    """Round a full-precision monthly figure into both display units.

    ``monthly_full`` is the unquantized monthly equivalent from
    ``monthly_or_none`` (per-row) or a sum of such values (per
    section).  Rounding once here, at the display boundary, keeps
    intermediate sums at full precision so pennies cannot accumulate drift
    (the ``committed_monthly`` contract).  ``None`` in propagates to both
    fields so a non-recurring row shows a blank equivalent.

    Args:
        monthly_full: The unquantized monthly figure, or ``None`` for a
            definition that is not a recurring commitment.
        pay_cadence: How often the owner is paid, from the calendar this
            surface already holds.

    Returns:
        The :class:`UnitPair`, both fields rounded to cents, or both ``None``.
    """
    if monthly_full is None:
        return UnitPair(monthly=None, per_paycheck=None)
    return UnitPair(
        monthly=round_money(monthly_full),
        per_paycheck=round_money(
            pay_cadence.monthly_to_per_paycheck(monthly_full),
        ),
    )


def _share_pct(
    monthly_full: Decimal | None, section_total_full: Decimal,
) -> Decimal | None:
    """This row's share (0-100) of its section's committed monthly total.

    ``None`` when the row does not contribute (non-recurring) or the
    section has no committed total to take a share of (avoids a divide by
    zero and a meaningless bar).  Computed from the full-precision values
    so the share reflects the true proportion, then quantized for display.
    """
    if monthly_full is None or section_total_full == 0:
        return None
    return (monthly_full / section_total_full * _HUNDRED).quantize(
        _PERCENT_QUANTUM, rounding=ROUND_HALF_UP,
    )


def next_placement(
    rule: RecurrenceRule, reading: RuleReading, as_of: date,
) -> tuple[DerivedPeriod, date] | None:
    """Engine-backed NEXT placement on or after ``as_of``: its paycheck and due date.

    **Public since plan step R7d-g-3**: the loan dashboard's payment card
    prices each definition's next payment through the amount model
    (``cash_ledger.definition_cash``), which needs the paycheck the row would
    live in beside the date it would carry -- the same two facts this walk
    yields for the Recurring surface's "next" column, stated once.

    Reads the placements
    :func:`~app.services.recurring_definition.read_definition` already
    produced for this row -- the same walk that generates the grid instances --
    and ``compute_due_date`` gives the due date the generated instance would
    carry, over each placement's own occurrence (plan step R5-a: two
    occurrences seated in one long paycheck are two dates, not one).  Returns
    the first such placement whose due date is on or after
    ``as_of`` (the current period can match with a due date already past, so
    the search advances to the next matching period), or ``None`` when no
    matching period has a due date on or after ``as_of`` -- an expired rule
    whose remaining candidate periods are all in the past.  Otherwise this
    tracks the engine exactly: if the engine would still generate a future
    instance (e.g. an expired rule whose final period straddles ``as_of``),
    that instance is reported, matching the grid cell it points at.

    **``as_of`` is this surface's own display boundary, not the rule's** -- the
    rule's opening bound is its anchor, and putting a caller's window inside the
    producer is what defect D2 was.  So the bound is stated here and the
    FILTER is shared (:func:`~app.services.recurrence.placed_occurrences`),
    which is the same split the retired ``match_periods`` adapter fused: it
    both filtered and bounded, so a caller's window looked like a property of
    the recurrence.

    Args:
        rule: The definition's recurrence rule, for the due-date derivation.
        reading: What :func:`~app.services.recurring_definition.read_definition`
            already resolved and placed for this row.  Taken rather than
            re-walked so the page resolves each rule ONCE: the description and
            this date are two questions about one reading.
        as_of: The surface's display boundary -- the read pass's ``as_of``.

    Returns:
        ``(period, due)`` for the next occurrence on or after *as_of* -- the
        paycheck it is placed in and the due date its row carries -- or
        ``None`` when the reading places nothing there.
    """
    for placement in placed_occurrences(
        reading.placements, ending_on_or_after=as_of,
    ):
        due = compute_due_date(rule, placement.occurrence, placement.period)
        if due >= as_of:
            return placement.period, due
    return None


def _next_occurrence(
    rule: RecurrenceRule, reading: RuleReading, as_of: date,
) -> date | None:
    """The date half of :func:`next_placement`, for the Recurring surface's column."""
    placed = next_placement(rule, reading, as_of)
    return None if placed is None else placed[1]


@dataclass(frozen=True)
class _PreparedRow:
    """One template with everything the two row passes need, computed once.

    Rows are built in two passes -- the section's committed total has to exist
    before any row's share of it can -- and this is what the first pass hands
    the second.  It exists so the second pass re-derives nothing: resolving a
    rule twice per render is what plan step R7a's read door was restructured to
    prevent.

    Attributes:
        template: The ORM template.
        price: What one occurrence commits on the pass's day, read ONCE for
            both the row's Amount column and its monthly figure (plan step
            X-bp: the price is a derivation now, not a column).
        monthly_full: Its unquantized monthly equivalent, or ``None`` when it
            is not a recurring commitment.
        rule: Its recurrence rule, or ``None`` when the definition does not
            repeat.
        reading: That rule read through the composed door against the read
            pass, or ``None`` exactly when *rule* is -- the two are set and
            cleared together, so a row cannot be described from a reading of a
            rule it does not have.
        pays_a_card: Whether it is a transfer definition into a card, which
            no total counts (:attr:`RecurringRow.pays_a_card`).
    """

    template: RecurringTemplate
    price: TodaysPrice
    monthly_full: Decimal | None
    rule: RecurrenceRule | None
    reading: RuleReading | None
    pays_a_card: bool

    @property
    def resolved(self) -> ResolvedRecurrence | None:
        """The row's two-axis meaning, or ``None`` when it does not repeat."""
        return None if self.reading is None else self.reading.resolved

    def __post_init__(self) -> None:
        """Refuse a rule without its reading, or a reading without its rule.

        A check rather than a docstring guarantee, matching
        :class:`~app.services.recurrence.OccurrencePlacement`: the pair is one
        fact stated twice, so the second pass can test ONE of them and the
        two-condition guard that would otherwise be needed is a fence for a
        state the value should not permit.

        Raises:
            ValueError: When exactly one of *rule* and *reading* is set.
        """
        if (self.rule is None) != (self.reading is None):
            raise ValueError(
                f"prepared row for {self.template!r} carries rule="
                f"{self.rule!r} beside reading={self.reading!r}: a definition "
                f"has a rule and its reading together or neither, and a pair "
                f"that disagrees would describe a cadence the row does not "
                f"have."
            )


def described(
    rule: RecurrenceRule | None, resolved: ResolvedRecurrence | None,
) -> RecurrenceDescription | None:
    """Turn a resolved recurrence into a row's description, or ``None``.

    **The ONE place a management surface words a cadence.**  Both row kinds
    of the Recurring surface reach it: the active sections from the reading
    they already hold, the Archived drawer from a meaning-only resolve -- both
    through the composed door, so the drawer and the list beside it narrow a
    loan payment the same way.  Written once because the two differ in how
    they OBTAIN the resolved value and not at all in what they do with it --
    and a display contract expressed twice is one a later change updates
    once.  **Public since plan step R7d-g-3**: the loan dashboard's payment
    card words each recurring transfer into the loan through it too, so a
    definition reads the same on the loan page as on the Recurring surface
    (design principle 7), and the refusal below has one home.

    **``None`` out means "does not repeat" and nothing else**, which is why
    the RULE is the discriminator rather than the resolved value.  They are
    not the same question: the door also answers ``None`` for
    an owner with no pay periods, and mapping that onto the same ``None``
    would render a quarterly bill as "One-time" -- a repeating commitment
    reported as a one-off, on the surface whose job is to say how things
    repeat.  An empty schedule is a broken invariant rather than a state to
    word: registration bootstraps a period, ``truncate_pay_periods`` always
    keeps the period its ``keep_through_period_id`` names, and
    ``reset_pay_periods`` deletes and regenerates inside one transaction, so no
    committed state has none.  (That truncate clause read "keeps index 0 by its
    own schema bound" until plan step C3-a, which re-keyed the form onto
    ``budget.pay_periods.id`` -- the guarantee got STRONGER, resting on the
    named period being on the keep side rather than on a Marshmallow floor of
    zero happening to sit below every real ordinal.)  It
    is refused here for the same reason :func:`_build_section` refuses every
    other broken invariant -- loudly.

    Args:
        rule: The definition's recurrence rule, or ``None`` when it does not
            repeat.
        resolved: What that rule MEANS, or ``None`` when there was no rule to
            resolve or no schedule to resolve it against.

    Returns:
        The :class:`~app.services.recurrence.RecurrenceDescription`, or
        ``None`` for a definition that does not repeat.

    Raises:
        RecurrenceResolutionError: When *rule* is present and *resolved* is
            not, which is an owner with no pay periods.
    """
    if rule is None:
        return None
    if resolved is None:
        raise RecurrenceResolutionError(
            f"recurrence rule {rule.id!r} could not be resolved against its "
            f"owner's schedule, which holds no pay periods.  Registration "
            f"bootstraps one, so this is a broken invariant; describing it as "
            f"a non-repeating definition would report a recurring commitment "
            f"as a one-off."
        )
    return describe(resolved)


def _build_section(
    templates: list[RecurringTemplate], ctx: BalanceContext,
) -> RecurringSection:
    """Build one kind-grouped section: its rows and both-units subtotal.

    Every template becomes a row (management surface shows all active
    definitions); its monthly equivalent, when the aggregator returns one,
    also accumulates into the full-precision section total the subtotal and
    each row's share are measured against.  Rows are ordered by monthly cost
    descending with non-recurring rows last (the locked default), preserving
    the caller's incoming order among equals.  The subtotal rounds the
    full-precision total once.  A transfer definition into a card is a row
    with its monthly figure and adds nothing to that total (ruling plan#3527,
    :attr:`RecurringRow.pays_a_card`), so the subtotal equals
    ``committed_monthly`` for the section less such rows; :class:`RecurringSection`
    says why the aggregator keeps them.

    **Each rule is READ once** (plan step R7a).  A row's cadence phrase, its
    next date and its monthly equivalent are three questions about one reading,
    so the first pass takes
    :func:`~app.services.recurring_definition.read_definition` -- the single
    resolve-then-narrow-then-place composition -- derives the monthly figure
    from what it returned (plan step R7d-e; the aggregator's
    :func:`~app.services.obligations_aggregator.monthly_or_none` takes the
    reading rather than resolving the rule again), and the second pass derives
    the other two.  That door is where the destination's own stop joins the
    rule's (plan step R7d-d): a loan payment's row stops where the EARLIER of
    the loan's derived closing date and its owner's stored stop says -- for
    the loan's own payment ordinarily the payoff, its column being NULL since
    plan step R7d-g (see the module docstring) -- and since R7d-e its monthly
    figure leaves the section total on the same day, so a retired loan's row
    cannot state a commitment beside a stop line that says the money has
    stopped.

    **This is a fail-CLOSED read**, and plan step R4a is what changed it.  The
    retired matcher used to log a warning and answer ``[]`` for a rule it could
    not read; the resolution seam now raises
    :class:`~app.services.recurrence.RecurrenceResolutionError` (an unmodelled
    pattern, an interval below 1, a day or month outside its column's domain),
    so ONE such rule takes the whole Recurring surface to a 500 rather than
    rendering the other definitions beside a silently blank cell.  The
    overlapping-schedule refusal (``RecurrenceScheduleError``) that used to sit
    beside them is GONE, with the calendar that raised it: plan step C2-b2
    replaced it with :class:`~app.services.pay_calendar.PayCalendar`, which
    DERIVES each period's end from the next payday and so cannot be handed an
    overlapping schedule to refuse.  Every one of the refusals that remain is a
    state the CHECK constraints and the write door already refuse, and the
    project's disposition for a broken invariant is the loud one -- but the
    contract is stated rather than discovered.

    *A ``PayCalendarError`` arm stood in the contract below until plan step
    ``pay_calendar:C4-d`` (ruling **R-PC45**) and is DELETED rather than
    reworded.  It named an owner with no resolvable pay cadence, which
    :attr:`~app.services.pay_calendar.PayCalendar.cadence` refused on the read
    further down.  This function reads a calendar off a pass it did not build,
    and a calendar now carries a cadence or does not exist --
    ``pay_calendar.calendar_for`` refuses that owner at the door -- so the
    refusal is the CALLER's (``routes/templates/surface.list_templates`` builds
    the pass) and claiming it here would be claiming a raise this function can
    no longer make.*

    Raises:
        RecurrenceResolutionError: When a rule names a cadence this application
            cannot derive.
        AmountUnresolvable: When the rule that prices a definition's rows has
            no answer for the pass's day
            (:func:`~app.services.obligations_aggregator.todays_price`): an
            empty series on a definition that owns its price, a derive-mode
            payment whose loan will not resolve.  The same fail-closed
            disposition, for the same reason: a row the page cannot price is
            not rendered beside the others with a figure nobody stated.
        BaselineMissingError: When a definition pays into a configured loan
            and *ctx* has no baseline scenario (ruling **R-R30**): the loan's
            stop is a fold over its forward plan, and a producer that needs a
            scenario REFUSES to the single application-level handler rather
            than rendering a bound nothing derived.  Every definition with no
            loan behind it still renders for such an owner.
    """
    calendar = ctx.calendar()
    # ONE cadence for the section, off the schedule this surface already
    # holds: every row belongs to the calendar's owner, so asking per row
    # would resolve one fact many times.  Derived here rather than taken as a
    # parameter because :func:`build_archived_rows` calls this module's other
    # entry point and must NOT need one -- see
    # :class:`TestAnAbsentCadenceIsRefused`.  It costs one frozen 1-field value
    # per section and no query: the calendar is already in memory.
    pay_cadence = calendar.cadence
    prepared: list[_PreparedRow] = []
    section_total_full = Decimal("0")
    for template in templates:
        rule = template_rule(template)
        # The RULE decides whether there is a reading at all
        # (:class:`_PreparedRow` insists the two are set together); the
        # monthly figure is then derived from THAT reading, never from a
        # second resolution of the rule (plan step R7d-e).
        reading = None if rule is None else read_definition(template, ctx)
        # Priced ONCE (plan step X-bp): the Amount column and the monthly
        # figure are two questions about one price, and for a salary
        # definition it is the paycheck the monthly figure converts from
        # (rulings R-SAL71, R-SAL73), so the row cannot show one figure and
        # total another.
        price = todays_price(template, ctx)
        monthly_full = (
            None if reading is None
            else monthly_or_none(template, reading, price, ctx)
        )
        # Only a transfer definition names an account its money goes to; an
        # income or expense definition never pays a card.
        pays = isinstance(template, TransferTemplate) and pays_a_card(template)
        prepared.append(_PreparedRow(
            template=template,
            price=price,
            monthly_full=monthly_full,
            rule=rule,
            reading=reading,
            pays_a_card=pays,
        ))
        if monthly_full is not None and not pays:
            section_total_full += monthly_full

    rows = [
        RecurringRow(
            template=item.template,
            equivalent=_unit_pair(item.monthly_full, pay_cadence),
            recurrence=described(item.rule, item.resolved),
            next_date=(
                None if item.reading is None
                else _next_occurrence(item.rule, item.reading, ctx.as_of)
            ),
            share_pct=_share_pct(
                None if item.pays_a_card else item.monthly_full,
                section_total_full,
            ),
            amount=item.price.amount,
            pays_a_card=item.pays_a_card,
        )
        for item in prepared
    ]
    rows.sort(
        key=lambda row: (
            row.equivalent.monthly is not None,
            row.equivalent.monthly
            if row.equivalent.monthly is not None
            else Decimal("0"),
        ),
        reverse=True,
    )
    return RecurringSection(
        rows=tuple(rows),
        subtotal=_unit_pair(section_total_full, pay_cadence),
    )


def _build_band(
    income: UnitPair,
    expenses: UnitPair,
    transfers_out: UnitPair,
) -> SummaryBand:
    """Assemble the summary band from the three section subtotals.

    ``net`` subtracts expenses and transfers from income in each unit using
    the already-rounded subtotals, so the tile equals what the shown section
    totals subtract to.  Section subtotals are never ``None`` (an empty
    section rounds ``Decimal("0")``), so the arithmetic is always defined.
    ``expenses_pct_of_income`` is ``None`` when income is zero.
    """
    net = UnitPair(
        monthly=income.monthly - expenses.monthly - transfers_out.monthly,
        per_paycheck=(
            income.per_paycheck
            - expenses.per_paycheck
            - transfers_out.per_paycheck
        ),
    )
    if income.monthly is not None and income.monthly > 0:
        expenses_pct_of_income = (
            expenses.monthly / income.monthly * _HUNDRED
        ).quantize(_PERCENT_QUANTUM, rounding=ROUND_HALF_UP)
    else:
        expenses_pct_of_income = None
    return SummaryBand(
        income=income,
        expenses=expenses,
        transfers_out=transfers_out,
        net=net,
        expenses_pct_of_income=expenses_pct_of_income,
    )


def build_view(
    income_templates: list[RecurringTemplate],
    expense_templates: list[RecurringTemplate],
    transfer_templates: list[RecurringTemplate],
    ctx: BalanceContext,
) -> RecurringView:
    """Produce the unified Recurring surface's full display model.

    Args:
        income_templates: The user's active recurring income
            ``TransactionTemplate`` rows.
        expense_templates: The user's active recurring expense
            ``TransactionTemplate`` rows.
        transfer_templates: The user's active recurring ``TransferTemplate``
            rows.
        ctx: The read pass (:class:`~app.services.balance_at.BalanceContext`),
            built by the route.  Its ``calendar()`` is the owner's whole
            pay-period schedule the engine-backed next dates are measured
            against; its ``as_of`` is the surface's "now" for the expired-rule
            filter and the next-occurrence search; its scenario scopes the
            fold that decides where a loan payment stops.

    Returns:
        A :class:`RecurringView`: the summary band plus income, expenses,
        and transfers sections, each with cost-descending rows and a
        both-units subtotal.  Every figure is a ``Decimal`` rounded to
        cents; the caller only displays.

    *The ``PayCalendarError`` arm that stood beside the ones below is DELETED
    at plan step ``pay_calendar:C4-d``, for the reason :func:`_build_section`
    states: a calendar this function reads off the pass carries a cadence, so
    the owner it named is refused by whoever built the calendar.*

    Raises:
        RecurrenceResolutionError: When a rule names a cadence this application
            cannot derive -- the fail-closed read :func:`_build_section`
            documents.
        AmountUnresolvable: When a definition has no price on the pass's day;
            see :func:`_build_section`.
        BaselineMissingError: When a loan payment is read for an owner with no
            baseline scenario; see :func:`_build_section`.
    """
    income_section = _build_section(income_templates, ctx)
    expense_section = _build_section(expense_templates, ctx)
    transfer_section = _build_section(transfer_templates, ctx)
    band = _build_band(
        income_section.subtotal,
        expense_section.subtotal,
        transfer_section.subtotal,
    )
    return RecurringView(
        income=income_section,
        expenses=expense_section,
        transfers=transfer_section,
        band=band,
    )


def build_archived_rows(
    templates: list[RecurringTemplate], ctx: BalanceContext,
) -> tuple[ArchivedRow, ...]:
    """Shape the Archived drawer's rows for one template kind.

    A second entry point rather than a fourth section of :func:`build_view`,
    because the two answer different callers: the drawer renders only on the
    full page, while ``build_view`` is rebuilt on every Monthly /
    Per-paycheck toggle, and folding archived definitions into it would load
    and resolve them on a request that never shows them.

    It exists at all because the drawer's Recurrence column used to be raw ORM
    handed to a template that computed the phrase itself.  Since plan step R7a
    the phrase is a function of what a recurrence MEANS against the owner's
    schedule, which a template cannot ask -- so the drawer gets a producer like
    the active sections have.

    Only the description is resolved and the price read: an archived
    definition generates nothing, so its occurrences are never walked.  The
    price is :func:`~app.services.obligations_aggregator.todays_price`, the
    one the active list's Amount column reads (ruling plan#2693, plan step
    X-bp), so a definition shows one figure whichever side of an archive it
    is on; it converts nothing, so it reads no cadence.  It is the COMPOSED
    description
    (:func:`~app.services.recurring_definition.resolved_definition`, plan step
    R7d-d), through the same door the active list takes.  Until plan step
    R7d-g an archived loan payment's row here named the cache the
    chokepoints wrote while it was active -- the door read a column as the
    cache only for the account's ACTIVE payment (ruling **R-R56**) -- and a
    cache earlier than the derived stop bound the drawer row.  R7d-g NULLed
    the column on every archived transfer into a loan (ruling **R-R80**: the
    schema records who wrote a bound nowhere, and an archived transfer's
    cached stop was the app's), so the row names the derived stop.

    Args:
        templates: The user's archived templates of one kind, in the order the
            drawer shows them.
        ctx: The read pass, shared with :func:`build_view` by the route so the
            drawer and the active sections read one schedule and one scenario.

    Returns:
        One :class:`ArchivedRow` per template, in the given order.

    Raises:
        RecurrenceResolutionError: When a rule names a cadence this application
            cannot derive -- the same fail-closed disposition the active
            sections have carried since plan step R4a; see
            :func:`_build_section`.
        AmountUnresolvable: When the rule that prices a definition's rows has
            no answer for the pass's day
            (:func:`~app.services.obligations_aggregator.todays_price`).
        BaselineMissingError: When an archived loan payment is read for an
            owner with no baseline scenario; see :func:`_build_section`.
    """
    rows = []
    for template in templates:
        # The RULE stays the discriminator for "does not repeat"
        # (:func:`described`); the door answers ``None`` for that state too,
        # but also for an owner with no pay periods, which must raise.
        rule = template_rule(template)
        rows.append(ArchivedRow(
            template=template,
            recurrence=described(rule, resolved_definition(template, ctx)),
            amount=todays_price(template, ctx).amount,
        ))
    return tuple(rows)
