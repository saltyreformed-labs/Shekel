"""
Shekel Budget App -- Obligations Aggregator Service (E-24, HIGH-05)

Single canonical producer for "monthly equivalent of recurring template"
and "sum of monthly equivalents across a set of recurring templates."

Before this module, four near-identical loops aggregated monthly
equivalents (three in ``app/routes/obligations.py:summary``, one in
``savings_goal_service.compute_committed_monthly``). Only the three
``/obligations`` loops skipped a template whose recurrence ``end_date``
was in the past. ``compute_committed_monthly`` did not, so an expired
recurring expense or transfer kept inflating the emergency-fund
baseline and every per-goal contribution floor on ``/savings`` forever
while ``/obligations`` correctly excluded it -- the same obligation
showing as two different numbers on two pages (HIGH-05 / D6-05).

The shared filter applied here, in one place, by every consumer:

  1. Skip if the template has no recurrence rule (one-off charge or
     orphaned reference -- no defined cadence to monthly-equivalent).
  2. Skip if anything that stops the definition has already stopped it
     (``recurrence.has_ended``) -- an expired recurring template is no
     longer a future obligation. This is the filter the audit found
     missing from ``compute_committed_monthly``.  **It reads the COMPOSED
     closing since plan step R7d-e**: the bound the owner authored AND the
     stop the destination derives, through the one door
     (``recurring_definition.read_definition``).  Until then it read the
     rule's own two columns, and for a loan payment those hold the
     chokepoints' CACHE of the payoff (ruling **R-R56**) -- so a retired
     loan's payment stayed in both totals until some chokepoint happened to
     rewrite the column, and left on the day it did rather than on the day
     the loan was finished -- and where the cache was EARLIER than the
     payoff (ledger row **D35**'s shape) a LIVE payment left them early.
     **MOVES MONEY** on both surfaces in both directions; nothing moves only
     where the cached column agrees with the derived stop on whether the
     payment has ended on the day asked, which is what both live loans do
     on the dev database today.
  3. Skip if what one occurrence commits is ``0`` -- nothing to contribute.
     That is the definition's price on the read pass's day, by the rule that
     prices its rows (:func:`todays_price`, ruling plan#2693, plan step
     **X-bp**): a salary profile's definition, its priced paycheck's net
     (ruling **R-SAL71**, plan step salary:X-av-2); a loan payment, amount
     rule 4 in either mode, its standing extra included; any other
     definition, rule 3, its price series as of that day.  Until X-bp every
     definition but a salary one read the stored ``default_amount`` here,
     which is the NEWEST stated price -- so a rise scheduled for December
     counted in both totals from the day it was entered, and a payment that
     tracks its loan counted whatever the contract was when it last pressed
     Track.  **There is no ``None`` arm any more**: a definition states a
     price or the rule refuses it, exactly as its rows would be refused.

**There is no fourth rule, and its removal is plan step R7a-2b's.**  The filter
used to end "skip if the conversion returns ``None`` -- a pattern this
application does not model has no cadence to normalize", which made this the
only surface in the app that answered a broken rule with silence: ``resolve``
RAISES for the same state, so the Recurring surface already 500'd on such a
rule through ``read_rule`` while THIS filter quietly left the same obligation
out of the emergency-fund baseline -- one row, counted on one page and not the
other.  ``recurrence.cadence_of`` raises now (ruled 2026-08-11), so the skip
has no subject.

**How often the owner is paid is an INPUT, not a constant** (plan step
R7a-2a).  The paycheck-space patterns' monthly equivalent used to be computed
against ``app.utils.money.PAY_PERIODS_PER_YEAR``, a hardcoded ``Decimal("26")``
-- so this "single canonical producer" produced a figure that was simply wrong
for an owner not paid biweekly.  Both entry points now take the owner's
schedule, resolved ONCE per request by the caller and threaded, never looked up
per row.  The month denominator (``MONTHS_PER_YEAR``) stays a constant, because
12 is a property of the calendar rather than of an owner -- and since plan step
salary:R15-a the division itself lives on
:meth:`~app.services.recurrence.Cadence.monthly_equivalent`, where a rule's
per-month ceiling gives it a second arm this module must not restate.

**They take the whole :class:`~app.services.pay_calendar.PayCalendar` rather
than its cadence, since plan step R7b-3**, and the extra is what step 2 of the
filter needs: a recurrence may now stop after a COUNT of occurrences, and when
that count is spent depends on when the owner's paychecks fall.  Reading only
the cadence, this module could answer "has it expired" for a DATE bound and had
no answer at all for a count -- so a spent count would have gone on inflating
``/obligations`` and the ``/savings`` emergency-fund baseline forever, which is
the HIGH-05 defect this module exists to have fixed, reappearing on the other
bound.  Two savings call sites trade ``cadence_for``'s one query for
``calendar_for``'s two to pay for it.

**And the conversion itself is ONE expression, on the recurrence value**
(plan step R7a-2b, moved at plan step salary:R15-a).  It lived in
``savings_goal_service.amount_to_monthly`` as a seven-branch switch over
``pattern_id`` -- in a savings module, though no savings code called it and its
inputs are a recurrence and a pay cadence -- so the branches were seven
spellings of one formula, each of which had to be written again for every
cadence plan step R8 adds.  R7a-2b collapsed them to the one division spelled
here; R15-a gave that division a second arm (a rule whose per-month ceiling
binds costs ``amount x ceiling``, exactly) and moved both onto
``recurrence.Cadence.monthly_equivalent``, so how often a cadence FIRES and
what that costs a month are the recurrence package's to answer and this
module reads the figure.

**Both entry points take the READ PASS** (plan step R7d-e), the
:class:`~app.services.balance_at.BalanceContext` the route built, because
step 2 of the filter is a question about a definition's DESTINATION as well as
its rule and only the pass can fold a loan's balance.  A pass carries the
owner's schedule and the ``as_of`` the pass is read at, so the two cannot be
handed in disagreeing -- which is the same reason every producer beside this
one takes it.  Only a route builds one (developer ruling 2026-08-16); this
module TAKES it.

Every function here is a read: ORM template instances (or any object exposing
the attributes the pricing rules read -- ``recurrence_rule``, ``id``,
``settings`` and ``amount_versions``; duck-typing supports the
``types.SimpleNamespace`` mock templates used in tests) and the pass in,
Decimal results out.  No Flask imports.
"""

from dataclasses import dataclass
from decimal import Decimal
from typing import TYPE_CHECKING, Iterable, Union

from app.models.transaction_template import TransactionTemplate
from app.models.transfer_template import TransferTemplate
from app.services.balance_at import BalanceContext, next_card_payment_price
from app.services.cash_ledger import (
    DefinitionRow,
    definition_cash,
    is_card_payment_definition,
    is_loan_payment_definition,
    stated_price,
)
from app.services.recurrence import RuleReading, cadence_of, has_ended
from app.services.recurring_definition import read_definition
from app.utils.money import round_money

if TYPE_CHECKING:  # pragma: no cover -- annotations only
    from app.services.pay_calendar import PayCadence
    from app.services.paycheck_calculator import PaycheckBreakdown

# Either ORM template class exposes ``recurrence_rule`` and the attributes
# its price is read through -- the aggregator reads them via attribute
# access. Test fixtures that build ``types.SimpleNamespace`` mock
# templates rely on the same duck-typed contract; the static type
# hint names the two production classes for IDE / pylint
# consumption.
RecurringTemplate = Union[TransactionTemplate, TransferTemplate]


def template_rule(template: RecurringTemplate):
    """Return *template*'s recurrence rule, or ``None`` when it does not repeat.

    The one accessor for "does this definition repeat", beside the
    ``RecurringTemplate`` type both readers duck-type over.  ``getattr`` rather
    than attribute access because the test fixtures build templates as
    ``types.SimpleNamespace``; "does not repeat" is the ABSENCE of a
    ``budget.recurrence_rules`` row naming the definition, on both ORM kinds
    since plan step R2e-3 (which read as ``recurrence_rule_id IS NULL`` until
    plan step R-F6 moved the owning FK onto the rule).

    Lives here rather than in ``recurring_view`` because this module owns the
    shared filter's step 1 and was already performing the same read inline --
    two spellings of one question in modules that import each other.

    Args:
        template: A ``TransactionTemplate`` or ``TransferTemplate`` (or any
            object exposing ``recurrence_rule``).

    Returns:
        The ``RecurrenceRule``, or ``None``.
    """
    return getattr(template, "recurrence_rule", None)


@dataclass(frozen=True)
class TodaysPrice:
    """What one occurrence of a definition commits on the read pass's day.

    The value :func:`todays_price` answers (ruling plan#2693, plan step
    **X-bp**).  The Recurring page's Amount column and the Archived drawer
    read :attr:`amount`; a monthly equivalent converts it at
    :meth:`pay_cadence`.  A value rather than a bare ``Decimal`` because the
    Recurring page asks two questions of one price -- the row's Amount and
    its monthly figure -- and pricing twice would run rule 4's derivation or
    walk the price series twice for one answer.

    Attributes:
        amount: The price.  Always a ``Decimal``: a definition states one or
            the rule that prices its rows refuses (:func:`todays_price`).
        paycheck_cadence: The rhythm the paycheck engine priced *amount* at,
            for a salary profile's definition
            (:attr:`~app.services.paycheck_calculator.PeriodInfo.cadence`,
            ruling **R-SAL71**); ``None`` for every other definition, which
            converts at the owner's latest rhythm (:meth:`pay_cadence`).
    """

    amount: Decimal
    paycheck_cadence: "PayCadence | None"

    @property
    def commits_nothing(self) -> bool:
        """Filter step 3: a zero price contributes nothing to a monthly total."""
        return self.amount == 0

    def pay_cadence(self, ctx: BalanceContext) -> "PayCadence":
        """The rhythm a monthly equivalent of this price converts at.

        A salary definition's paycheck converts at the rhythm the engine
        priced it at (ruling **R-SAL71**); every other definition at the
        owner's latest, the forward-looking count a recurring bill is paid at.
        Read here, over the pass, rather than filled in when the price is
        read, because only a MONTHLY figure needs it: the Archived drawer
        reads the price and converts nothing, and must not read a cadence
        (``test_recurring_view.TestAnAbsentCadenceIsRefused``).

        Args:
            ctx: The read pass the price was read in.

        Returns:
            The :class:`~app.services.pay_calendar.PayCadence`.
        """
        if self.paycheck_cadence is not None:
            return self.paycheck_cadence
        return ctx.calendar().cadence


def todays_price(
    template: RecurringTemplate, ctx: BalanceContext,
) -> TodaysPrice:
    """Return what one occurrence of *template* commits on the read pass's day.

    **The ONE producer of a definition's figure on the Recurring and savings
    pages** (ruling plan#2693, plan step **X-bp**): the Recurring page's
    Amount and monthly columns, the Archived drawer's Amount, the
    emergency-fund floor and each goal's monthly contribution all read it.
    It prices the definition on ``ctx.as_of`` by THE RULE THAT PRICES ITS
    ROWS, so a definition's figure and its rows cannot part:

    * a salary profile's definition: today's paycheck
      (:func:`_todays_paycheck`, rulings **R-SAL71** and **R-SAL79**);
    * a loan payment: amount rule 4 in either mode, through
      :func:`~app.services.cash_ledger.definition_cash` -- the contract for a
      payment that tracks its loan, the stated series otherwise, and the
      standing extra in both, because that is what leaves checking;
    * a card's worked-out payment: what its next row asks of checking
      (:func:`~app.services.balance_at.next_card_payment_price`), because the
      rule prices a row off the card's balance and a row imagined on the day
      would read ``$0.00`` from each due date until the next close;
    * any other definition: rule 3, its price series as of the day.

    **Not the newest stated price**, which is what the stored
    ``default_amount`` held and what every one of these figures but a salary
    definition's read until X-bp.  A $15.49 subscription with $17.99 entered
    from Dec 1 counted $17.99 a month on both pages from the day the rise was
    entered, while the grid rightly kept $15.49 until December; read here it
    is $15.49 until Dec 1 and $17.99 from then.  A payment that tracks its
    loan follows the contract, where the column held the contract as it was
    when the payment was created or last tracked.

    **A loan payment is priced on the installment today pays**: the
    occurrence is dated on the pass's day, so rule 4 prices the latest
    installment due on or before it (ruling **R-R104**).  ``period_start``
    is :class:`~app.services.cash_ledger.DefinitionRow`'s fallback for a row
    with no due date and is never read when one is given, so the pass's day
    stands there too.  The loan page's payment strip prices the NEXT payment,
    a different question on a different date (``routes/loan/dashboard``).

    Args:
        template: The definition, of either kind.  **Must belong to
            ``ctx.user_id``**: the caller owns the ownership check.
        ctx: The read pass: its day; its pricer and calendar for a salary
            definition; its amount basis for a loan payment.

    Returns:
        The :class:`TodaysPrice`.

    Raises:
        AmountUnresolvable: The rule that prices the definition's rows has no
            answer -- an empty series on a definition that owns its price, a
            derive-mode payment whose loan will not resolve, a salary
            definition no paycheck prices -- exactly where its rows would be
            refused.  There is no stored copy to fall back to, and no reader
            ever should have.
        BaselineMissingError: A loan payment priced for an owner with no
            baseline scenario (ruling **R-R30**), or a card's worked-out payment,
            which needs one for the reason a loan payment does; every other
            definition is priced without one.
        UnsupportedStateError: A salary profile in a state the tax law does
            not list, from the pricer (plan step salary:X-at-3).
    """
    paycheck = _todays_paycheck(template, ctx)
    if paycheck is not None:
        return TodaysPrice(
            amount=paycheck.earnings.net_pay,
            paycheck_cadence=paycheck.period.cadence,
        )
    return TodaysPrice(
        amount=_definition_price(template, ctx), paycheck_cadence=None,
    )


def _definition_price(
    template: RecurringTemplate, ctx: BalanceContext,
) -> Decimal:
    """The card's rule, rule 4 or rule 3, for a definition no paycheck prices.

    The two arms :func:`~app.services.cash_ledger.definition_cash` decides
    between for a row, decided by the same predicate
    (:func:`~app.services.cash_ledger.is_loan_payment_definition`).  The
    predicate is asked HERE only to hand a basis to the one arm that needs
    it: rule 4 reads the loan through the pass's amount basis, building that
    refuses an owner with no baseline scenario, and ruling **R-R30** allows
    the refusal only for a definition that pays into a loan.  Rule 3 needs no
    basis (:func:`~app.services.cash_ledger.stated_price`), so every other
    definition is still priced for such an owner.

    Args:
        template: The definition, of either kind.
        ctx: The read pass.

    Returns:
        The price on ``ctx.as_of``.

    Raises:
        AmountUnresolvable: See :func:`todays_price`.
        BaselineMissingError: See :func:`todays_price`.
    """
    if is_card_payment_definition(template):
        return next_card_payment_price(template, ctx)
    subject = (
        f"{type(template).__name__} {template.id}'s price on "
        f"{ctx.as_of.isoformat()}"
    )
    if is_loan_payment_definition(template):
        return definition_cash(
            DefinitionRow(
                template=template,
                due_date=ctx.as_of,
                period_start=ctx.as_of,
                to_account_id=template.to_account_id,
            ),
            ctx.amounts(),
            subject=subject,
        )
    return stated_price(template, ctx.as_of, subject=subject)


def _todays_paycheck(
    template: RecurringTemplate, ctx: BalanceContext,
) -> "PaycheckBreakdown | None":
    """TODAY's paycheck when *template* is an active salary profile's, else ``None``.

    Ruling **R-SAL71** (plan step salary:X-av-2): a salary definition's
    ``default_amount`` was a STORED copy of a paycheck the engine priced when
    the salary was last saved (until plan step X-bp dropped it), which
    recorded neither which payday it was nor the rhythm that payday was paid
    at -- so a reader converting it to a
    month has to guess the count, and guessed the LATEST era's.  That was
    right only while the engine divided every payday by that count too.  The
    definition's figure is read from the pricer instead: the pass's
    :class:`~app.services.income_service.SalaryPricing`, which knows which
    profile drives the template in this scenario, prices the paycheck of the
    span containing the pass's day.

    ``span_containing`` rather than the saved ``period_containing``: it IS
    the savings page's current period wherever the saved schedule covers the
    day, and past the horizon it keeps answering with the projected paycheck,
    so the row there is priced at that day's paycheck.  **Before the first
    SAVED payday** (the calendar's ``opening_bound``) **it prices that first
    saved paycheck** (ruling **R-SAL79**): there is no paycheck today to
    price, and the copy was exactly the figure the ruling retired -- one a
    salary EDIT made in that window left at GROSS pay when the ruling was
    made, because the edit door wrote the gross and ``salary_regeneration``
    re-stated the net only once a saved period contained the day (the gross
    write went at plan step salary:X-av-3a, the re-statement at X-bp).  For
    an owner who has stated
    ``history_opens_on`` the backward rhythm may hold earlier paydays, but
    those are counts and never periods, so the first saved paycheck is the
    next one priceable.  The savings page shows no current pay in that
    window; this row shows that paycheck.

    ``None`` -- the definition is priced by its own rule instead
    (:func:`_definition_price`) -- for every definition no active profile
    drives in the pass's scenario, which is every definition but a salary
    one, and for a transfer or a duck-typed test template (the module
    docstring's contract).  Two further branches answer ``None`` for a
    salary definition, and neither is a state the application produces: a
    pass with NO baseline scenario (``BaselineMissingError``'s docstring:
    every owner is registered with one and nothing deletes it), where no
    profile is in scope; and a calendar holding NO saved payday.  **Both
    REFUSE one tier down since plan step X-bp**, where they showed the
    stored copy until then: rule 3 refuses a definition whose amount is
    derived (``cash_ledger.stated_price``), which is the refusal its rows
    would raise, and there is no copy left to show.  For every OTHER
    transaction template the guards carry weight, because they are asked
    before the pricer says the template is no salary one: they are what lets
    :func:`committed_monthly` answer the ``/savings`` floor for such an
    owner, as ``recurrence.has_ended`` intends, and lets a page of rule-less
    rows render.  **The Archived drawer reads this too since plan step
    X-bp** (:func:`todays_price`), so a salary template archived while its
    profile stays active shows today's paycheck there rather than the stored
    copy, which was the one place the page still showed it.  The state
    itself is still a defect: two doors allow it -- the archive door, and
    the hard-delete door's archive fallback for a template with settled
    history or a standing merchant rule -- and ruling **R-SAL81** makes the
    door the defect to fix rather than the drawer: finding **SAL-579**,
    owned by plan step salary:S13, which refuses the archive or archives the
    profile with it so the state cannot arise.

    Args:
        template: The recurring definition.
        ctx: The read pass: its pricer, its scenario, its calendar and its day.

    Returns:
        The :class:`~app.services.paycheck_calculator.PaycheckBreakdown`, or
        ``None``.
    """
    if not isinstance(template, TransactionTemplate):
        return None
    basis = ctx.amounts_or_none()
    if basis is None:
        return None
    calendar = ctx.calendar()
    today = calendar.span_containing(ctx.as_of)
    if today is None:
        # Before the first payday (R-SAL79), or a calendar with no payday.
        if not calendar.periods:
            return None
        today = calendar.periods[0]
    return basis.salary.paycheck_on(template.id, today)


def monthly_or_none(
    template: RecurringTemplate,
    reading: RuleReading,
    price: TodaysPrice,
    ctx: BalanceContext,
) -> Decimal | None:
    """Return the monthly equivalent of a definition already READ and PRICED, or ``None``.

    The layer under :func:`template_monthly_or_none` for a caller that has
    already read the definition through the composed door and holds the
    reading: the Recurring surface reads every definition once for its stop
    line and its next date, and its monthly column is a third question about
    that same reading rather than a second resolution of the rule
    (``CLAUDE.md`` rule 14, ONE WALK -- and the second resolution this deletes
    is the one ``test_a_loan_payment_is_RESOLVED_exactly_once_per_build`` had
    to scope itself around).  **It takes the PRICE for the same reason since
    plan step X-bp**: that surface shows the price in the row's Amount column
    too, and the price is now a derivation (:func:`todays_price`) rather than
    a column read, so the caller prices once and hands it here.

    Applies filter steps 2 and 3 (expired, zero price).  Step 1 -- no rule,
    which is how a definition says it does not repeat -- is the caller's: a
    definition with no rule has no *reading* to hand in.  The returned
    Decimal is NOT quantized -- callers that aggregate first then round
    (``committed_monthly``) need full precision; callers that display a
    per-row value round at the display boundary with ``round_money``.

    **The conversion is one expression**, and the same one for every cadence:
    an amount times how often it happens in a year, over twelve.  Plan step
    R7a-2b replaced a seven-branch switch with it, so ``(2, MONTH)`` and
    ``(1, WEEK)`` -- cadences the closed set could not name, authorable since
    plan steps R7c-c and R8-b -- total correctly rather than falling to a
    ``None`` the caller drops.

    Args:
        template: A ``TransactionTemplate`` or ``TransferTemplate`` ORM
            instance (or any object exposing ``recurrence_rule``) that
            repeats.  The recurrence rule is read via attribute access;
            loading is the caller's responsibility
            (``joinedload(.recurrence_rule)`` in the production routes).
        reading: *template* read through
            :func:`app.services.recurring_definition.read_definition` against
            *ctx* -- what its rule means, narrowed by what its destination
            allows, and every occurrence that names.  The expired filter reads
            the composed closing off it and judges it against the walk it
            already holds.
        price: *template* priced on *ctx*'s day (:func:`todays_price`).
        ctx: The read pass.  Its ``as_of`` is the day the expired filter asks
            about; its ``calendar()`` supplies the cadence the conversion
            needs -- except for a salary profile's definition, converted at
            the rhythm its paycheck was priced at (ruling **R-SAL71**,
            :meth:`TodaysPrice.pay_cadence`).  The horizon the filter needs
            rides on *reading* itself since plan step R7d-f-2 (plan ledger
            row **N-514**), so the schedule a definition was walked against
            and the one its stop is judged against are one value rather than
            two arguments that agree.

    Returns:
        The full-precision Decimal monthly equivalent, or ``None`` if the
        template is filtered out by either rule.  ``None`` means "do not
        include this template in any monthly-equivalent total."

    Raises:
        RecurrenceResolutionError: The rule names a cadence this application
            does not model, so it has no derivable cadence.  A REFUSAL rather
            than a skip since plan step R7a-2b: a rule the app cannot read is a
            broken invariant, and dropping it silently understated every total
            this module feeds while the Recurring surface 500'd on the same
            row.
    """
    if price.commits_nothing:
        return None

    rule = template_rule(template)
    if has_ended(rule, reading, on=ctx.as_of):
        return None

    # ONE producer, on the value (plan step salary:R15-a): a monthly
    # equivalent is an amount times how often it happens in a year, over
    # twelve, divided ONCE by an exact integer -- or, for a rule whose
    # per-month ceiling binds, the amount times the ceiling exactly.  The
    # division was spelled inline here until that step gave it a second arm;
    # ``Cadence.monthly_equivalent`` carries both and the 31,072-cent
    # measurement of why neither may round twice.  The pay cadence is the one
    # the amount is paid at (ruling R-SAL71): today's paycheck's own rhythm for
    # a salary definition, the owner's latest for every other.
    return cadence_of(rule).monthly_equivalent(
        price.amount, price.pay_cadence(ctx),
    )


def template_monthly_or_none(
    template: RecurringTemplate, ctx: BalanceContext,
) -> Decimal | None:
    """Return the monthly equivalent of one recurring template, or ``None``.

    :func:`monthly_or_none` for a caller that holds the template and the pass
    but has neither read nor priced the definition: this prices it
    (:func:`todays_price`), reads it through the composed door
    (:func:`app.services.recurring_definition.read_definition`) and hands
    both down.  The ``/savings`` emergency-fund floor and the per-goal
    contribution floors take this entry; the Recurring surface, which has
    already read and priced every definition once, takes the one below it.

    The two skips that need no walk are asked FIRST, so a definition that
    cannot contribute costs no door read: no rule (filter step 1), then a
    zero price (step 3).  **The zero skip comes after the PRICE since plan
    step X-bp**, where it read the stored column before anything else: the
    price is a derivation on the pass's day now, so it is read here once and
    the same value is handed down.

    Args:
        template: A ``TransactionTemplate`` or ``TransferTemplate`` ORM
            instance, or any object exposing ``recurrence_rule``, ``id``,
            ``settings``, ``amount_versions`` and ``to_account_id`` -- the
            door's own duck-typed contract (:data:`~app.services.recurrence.
            RecurrenceOwner`) and the pricing rules'.  **Must belong to
            ``ctx.user_id``**: the caller owns the ownership check, as every
            seam entry this reaches states.
        ctx: The read pass.  See :func:`monthly_or_none`.

    Returns:
        See :func:`monthly_or_none`.

    Raises:
        AmountUnresolvable: See :func:`todays_price`.
        RecurrenceResolutionError: See :func:`monthly_or_none`; also raised by
            the door for a rule paired with another owner's pass.
        RecurrenceGenerationError: The resolved value names something the
            occurrence engine cannot walk -- a business-day shift, until plan
            step R8-d gives it a walk.
        BaselineMissingError: The definition pays into a configured loan and
            *ctx* has no baseline scenario (ruling **R-R30**), from the seam's
            own ``require_scenario``: the loan's stop is a fold over its plan,
            and a producer that needs a scenario REFUSES rather than answering
            a bound nothing derived.  A definition with no loan behind it
            still resolves for such an owner.
    """
    if template_rule(template) is None:
        return None
    price = todays_price(template, ctx)
    if price.commits_nothing:
        return None
    return monthly_or_none(
        template, read_definition(template, ctx), price, ctx,
    )


def committed_monthly(
    templates: Iterable[RecurringTemplate], ctx: BalanceContext,
) -> Decimal:
    """Sum monthly equivalents across a set of recurring templates.

    Routes every template through :func:`template_monthly_or_none`, which
    applies the shared filter (no rule, expired, zero price on the pass's
    day).
    Templates returning ``None`` contribute zero to the total; only non-None
    Decimals are summed.  The final result is rounded once at the boundary with
    ``round_money`` (ROUND_HALF_UP via ``app.utils.money``) -- intermediate
    sums stay at full Decimal precision so penny-level drift cannot accumulate.

    This is the single canonical aggregator behind the ``/savings``
    emergency-fund baseline + per-goal contribution-floor figures, and the
    Recurring surface's totals sum the same filter per row (the retired
    ``/obligations`` page's kernel, ``recurring_view._build_section``, which
    holds each row's figure for its share).  Per E-24 / HIGH-05, every
    consumer must call this function or that per-row filter rather than
    inline its own.  **The two part at a transfer into a card** (ruling
    plan#3527, plan task plan#3530): the Recurring surface leaves one out of
    its totals, the card's bills being in them, and this counts one, its
    emergency-fund caller reading checking's definitions alone -- the card's
    payment and none of the card's bills -- so each counts a bill on the
    card once.

    Args:
        templates: Iterable of ORM template instances (``TransactionTemplate``,
            ``TransferTemplate``, or any duck-typed equivalent).  Callers are
            responsible for scoping the query (user_id, is_active, account_id,
            etc.); this function applies only the cross-cutting recurrence
            filter, not the data-ownership filter.  **Every one must belong to
            ``ctx.user_id``.**
        ctx: The read pass, one value for the whole set: these templates belong
            to one owner, so summing figures resolved against two schedules or
            judged on two days would be adding different units.

    Returns:
        The total monthly-equivalent Decimal, rounded to cents with
        ``ROUND_HALF_UP``.  Returns ``Decimal("0.00")`` if every input template
        is filtered out or the iterable is empty.

    Raises:
        RecurrenceResolutionError: A template's rule names a cadence this
            application does not model, so the total cannot be completed.  The
            whole sum is refused rather than shrunk by one row -- see
            :func:`monthly_or_none`, and note that this function's three
            callers (the Recurring surface, the emergency-fund floor and the
            per-goal contribution floors) each publish a figure a missing row
            would silently understate.
        AmountUnresolvable: A template's rule has no price for the pass's
            day (:func:`todays_price`) -- refused whole for the same reason.
        RecurrenceGenerationError: See :func:`template_monthly_or_none`.
        BaselineMissingError: See :func:`template_monthly_or_none`.
    """
    total = Decimal("0")
    for template in templates:
        monthly = template_monthly_or_none(template, ctx)
        if monthly is not None:
            total += monthly
    return round_money(total)
