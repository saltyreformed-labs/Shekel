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
  3. Skip if what one occurrence commits is ``None`` or ``0`` -- nothing to
     contribute.  That is the stored ``default_amount``, except for a salary
     profile's definition, whose occurrence is its priced paycheck's net
     (ruling **R-SAL71**, plan step salary:X-av-2).

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
the same ``recurrence_rule`` / ``default_amount`` attributes -- duck-typing
supports the ``types.SimpleNamespace`` mock templates used in tests) and the
pass in, Decimal results out.  No Flask imports.
"""

from decimal import Decimal
from typing import TYPE_CHECKING, Iterable, Union

from app.models.transaction_template import TransactionTemplate
from app.models.transfer_template import TransferTemplate
from app.services.balance_at import BalanceContext
from app.services.recurrence import RuleReading, cadence_of, has_ended
from app.services.recurring_definition import read_definition
from app.utils.money import round_money

if TYPE_CHECKING:  # pragma: no cover -- annotations only
    from app.services.pay_calendar import PayCadence
    from app.services.paycheck_calculator import PaycheckBreakdown

# Either ORM template class exposes ``recurrence_rule`` and
# ``default_amount`` -- the aggregator reads them via attribute
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


def _committed_amount(template: RecurringTemplate) -> Decimal | None:
    """Return what *template* commits per occurrence, or ``None``.

    Filter steps 1 and 3 -- the two that need neither a schedule nor a walk: a
    definition that does not repeat commits nothing, and so does one with a
    missing or zero amount.  Held apart from step 2 so both entry points can
    ask them BEFORE the door is read: step 2 resolves the definition against
    the owner's schedule, folds its destination and walks its occurrences,
    which is real work to reach the same ``None`` a zero amount reaches for
    free.  The three are independent skips, so their order is ours to choose,
    and this order was plan step R7b-3's for the same reason.

    Args:
        template: A ``TransactionTemplate`` or ``TransferTemplate`` (or any
            object exposing ``recurrence_rule`` and ``default_amount``).

    Returns:
        The committed amount as a ``Decimal``, or ``None`` when the definition
        does not repeat or states no positive amount.
    """
    if template_rule(template) is None:
        return None
    amount = template.default_amount
    if amount is None:
        return None
    amount = Decimal(str(amount))
    if amount == 0:
        return None
    return amount


def _todays_paycheck(
    template: RecurringTemplate, ctx: BalanceContext,
) -> "PaycheckBreakdown | None":
    """TODAY's paycheck when *template* is an active salary profile's, else ``None``.

    Ruling **R-SAL71** (plan step salary:X-av-2): a salary definition's
    ``default_amount`` is a STORED copy of a paycheck the engine priced when
    the salary was last saved, which records neither which payday it was nor
    the rhythm that payday was paid at -- so a reader converting it to a
    month has to guess the count, and guessed the LATEST era's.  That was
    right only while the engine divided every payday by that count too.  The
    definition's figure is read from the pricer instead: the pass's
    :class:`~app.services.income_service.SalaryPricing`, which knows which
    profile drives the template in this scenario, prices the paycheck of the
    span containing the pass's day.

    ``span_containing`` rather than the saved ``period_containing``: it IS
    the savings page's current period wherever the saved schedule covers the
    day, and past the horizon it keeps answering with the projected paycheck,
    so the row never falls back to the copy there.  **Before the first
    SAVED payday** (the calendar's ``opening_bound``) **it prices that first
    saved paycheck** (ruling **R-SAL79**): there is no paycheck today to
    price, and the copy is exactly the figure the ruling retires -- one a
    salary EDIT made in that window leaves at GROSS pay, because the edit
    door writes the gross and ``salary_regeneration`` restates the net only
    once a saved period contains the day.  For an owner who has stated
    ``history_opens_on`` the backward rhythm may hold earlier paydays, but
    those are counts and never periods, so the first saved paycheck is the
    next one priceable.  The savings page shows no current pay in that
    window; this row shows that paycheck.

    ``None`` -- the definition keeps its stored amount -- for every
    definition no active profile drives in the pass's scenario, which is
    every definition but a salary one, and for a transfer or a duck-typed
    test template (the module docstring's contract).  Two further branches
    answer ``None`` for a salary definition, and neither is a state the
    application produces: a pass with NO baseline scenario
    (``BaselineMissingError``'s docstring: every owner is registered with
    one and nothing deletes it), where no profile is in scope; and a
    calendar holding NO saved payday, where for a salary definition the
    guard only keeps ``periods[0]``'s ``IndexError`` from pre-empting the
    Recurring page's own refusal of that owner, which comes AFTER this read:
    :func:`~app.services.recurring_view.described` refuses the section's
    first repeating row, whichever row that is.  For every OTHER transaction
    template the same guard carries weight, because it is asked before the
    pricer says the template is no salary one: it is what lets
    :func:`committed_monthly` answer the ``/savings`` floor for such an
    owner, as ``recurrence.has_ended`` intends, and lets a page of rule-less
    rows render.  **The one place the page
    still shows a salary definition's stored copy** is the Archived drawer,
    for a salary template archived while its profile stays active: two doors
    allow that state -- the archive door, and the hard-delete door's archive
    fallback for a template with settled history or a standing merchant
    rule -- and ruling **R-SAL81**
    makes the door the defect to fix rather than the drawer: finding
    **SAL-579**, owned by plan step salary:S13, which refuses the archive or
    archives the profile with it so the state cannot arise.

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


def _commitment(
    template: RecurringTemplate, ctx: BalanceContext,
) -> "tuple[Decimal, PayCadence] | None":
    """What one occurrence of *template* commits and the rhythm it converts at.

    The pair a monthly equivalent is computed from, answered ONCE for both
    kinds of definition (ruling **R-SAL71**): a salary profile's definition
    commits today's priced paycheck at the rhythm the engine priced it at
    (:attr:`~app.services.paycheck_calculator.PeriodInfo.cadence`); every
    other definition its stored amount at the owner's latest rhythm, which
    is the forward-looking count a recurring bill is paid at.  ``None`` when
    the definition does not repeat or commits nothing -- a salary one by its
    priced net, so the stored copy decides nothing about it in any state the
    application produces (:func:`_todays_paycheck`).

    Args:
        template: The recurring definition.
        ctx: The read pass.

    Returns:
        ``(amount, pay cadence)``, or ``None``.
    """
    if template_rule(template) is None:
        return None
    paycheck = _todays_paycheck(template, ctx)
    if paycheck is not None:
        net = paycheck.earnings.net_pay
        return None if net == 0 else (net, paycheck.period.cadence)
    amount = _committed_amount(template)
    return None if amount is None else (amount, ctx.calendar().cadence)


def occurrence_amount(
    template: RecurringTemplate, ctx: BalanceContext,
) -> Decimal | None:
    """Return what one occurrence of *template* commits, as the read pass prices it.

    The Recurring surface's Amount column (ruling **R-SAL73**): a salary
    profile's definition shows TODAY's priced paycheck, or before the first
    saved payday that first paycheck (**R-SAL79**) -- not the stored copy,
    which goes stale when a raise date passes until the salary is next
    saved (:func:`_todays_paycheck` names the one place the page still shows
    it: the Archived drawer) -- and every other definition its stored
    amount.  The same
    :func:`_todays_paycheck` its monthly figure is converted from, so the row
    cannot show one paycheck and total another; the pricer memoizes the
    paycheck by payday, so asking twice prices once.

    Args:
        template: The recurring definition.
        ctx: The read pass.

    Returns:
        The amount, or ``None`` when the definition states none.
    """
    paycheck = _todays_paycheck(template, ctx)
    if paycheck is not None:
        return paycheck.earnings.net_pay
    amount = getattr(template, "default_amount", None)
    return None if amount is None else Decimal(str(amount))


def monthly_or_none(
    template: RecurringTemplate, reading: RuleReading, ctx: BalanceContext,
) -> Decimal | None:
    """Return the monthly equivalent of a definition already READ, or ``None``.

    The layer under :func:`template_monthly_or_none` for a caller that has
    already read the definition through the composed door and holds the
    reading: the Recurring surface reads every definition once for its stop
    line and its next date, and its monthly column is a third question about
    that same reading rather than a second resolution of the rule
    (``CLAUDE.md`` rule 14, ONE WALK -- and the second resolution this deletes
    is the one ``test_a_loan_payment_is_RESOLVED_exactly_once_per_build`` had
    to scope itself around).

    Applies the shared filter (no rule -- which is how a definition says it
    does not repeat -- expired, missing/zero amount).  The returned Decimal is
    NOT quantized -- callers that aggregate first then round
    (``committed_monthly``) need full precision; callers that display a per-row
    value round at the display boundary with ``round_money``.

    **The conversion is one expression**, and the same one for every cadence:
    an amount times how often it happens in a year, over twelve.  Plan step
    R7a-2b replaced a seven-branch switch with it, so ``(2, MONTH)`` and
    ``(1, WEEK)`` -- cadences the closed set could not name, authorable since
    plan steps R7c-c and R8-b -- total correctly rather than falling to a
    ``None`` the caller drops.

    Args:
        template: A ``TransactionTemplate`` or ``TransferTemplate`` ORM
            instance (or any object exposing ``recurrence_rule`` and
            ``default_amount``).  The recurrence rule is read via attribute
            access; loading is the caller's responsibility
            (``joinedload(.recurrence_rule)`` in the production routes).
        reading: *template* read through
            :func:`app.services.recurring_definition.read_definition` against
            *ctx* -- what its rule means, narrowed by what its destination
            allows, and every occurrence that names.  The expired filter reads
            the composed closing off it and judges it against the walk it
            already holds.
        ctx: The read pass.  Its ``as_of`` is the day the expired filter asks
            about; its ``calendar()`` supplies the cadence the conversion
            needs -- except for a salary profile's definition, converted at
            the rhythm its paycheck was priced at (ruling **R-SAL71**).  The
            horizon the filter needs rides on *reading* itself
            since plan step R7d-f-2 (plan ledger row **N-514**), so the
            schedule a definition was walked against and the one its stop is
            judged against are one value rather than two arguments that agree.

    Returns:
        The full-precision Decimal monthly equivalent, or ``None`` if the
        template is filtered out by any of the shared-filter rules.  ``None``
        means "do not include this template in any monthly-equivalent total."

    Raises:
        RecurrenceResolutionError: The rule names a cadence this application
            does not model, so it has no derivable cadence.  A REFUSAL rather
            than a skip since plan step R7a-2b: a rule the app cannot read is a
            broken invariant, and dropping it silently understated every total
            this module feeds while the Recurring surface 500'd on the same
            row.
    """
    commitment = _commitment(template, ctx)
    if commitment is None:
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
    amount, pay_cadence = commitment
    return cadence_of(rule).monthly_equivalent(amount, pay_cadence)


def template_monthly_or_none(
    template: RecurringTemplate, ctx: BalanceContext,
) -> Decimal | None:
    """Return the monthly equivalent of one recurring template, or ``None``.

    :func:`monthly_or_none` for a caller that holds the template and the pass
    but has not read the definition: this reads it through the composed door
    (:func:`app.services.recurring_definition.read_definition`) and hands the
    reading down.  The ``/savings`` emergency-fund floor and the per-goal
    contribution floors take this entry; the Recurring surface, which has
    already read every definition once, takes the one below it.

    The two schedule-free skips are asked FIRST, here as well as there, so a
    definition that cannot contribute costs no door read.

    Args:
        template: A ``TransactionTemplate`` or ``TransferTemplate`` ORM
            instance, or any object exposing ``recurrence_rule``,
            ``default_amount`` and ``to_account_id`` -- the door's own
            duck-typed contract (:data:`~app.services.recurrence.
            RecurrenceOwner`).  **Must belong to ``ctx.user_id``**: the caller
            owns the ownership check, as every seam entry this reaches states.
        ctx: The read pass.  See :func:`monthly_or_none`.

    Returns:
        See :func:`monthly_or_none`.

    Raises:
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
    # The pre-door skip asks the STORED amount, schedule-free.  This function's
    # one production caller is ``committed_monthly``, whose two callers (the
    # emergency-fund floor: expense and transfer definitions; the goal
    # floors: transfer definitions) pass no income definition, so no salary
    # definition's stored copy decides anything here (ruling R-SAL71); a
    # nonzero one would be re-decided from the priced paycheck below anyway.
    if _committed_amount(template) is None:
        return None
    return monthly_or_none(template, read_definition(template, ctx), ctx)


def committed_monthly(
    templates: Iterable[RecurringTemplate], ctx: BalanceContext,
) -> Decimal:
    """Sum monthly equivalents across a set of recurring templates.

    Routes every template through :func:`template_monthly_or_none`, which
    applies the shared filter (no rule, expired, missing/zero amount).
    Templates returning ``None`` contribute zero to the total; only non-None
    Decimals are summed.  The final result is rounded once at the boundary with
    ``round_money`` (ROUND_HALF_UP via ``app.utils.money``) -- intermediate
    sums stay at full Decimal precision so penny-level drift cannot accumulate.

    This is the single canonical aggregator behind both the Recurring
    surface's totals (the retired ``/obligations`` page's kernel) and the
    ``/savings`` emergency-fund baseline + per-goal contribution-floor figures.
    Per E-24 / HIGH-05, every consumer must call this function rather than
    inline its own filter+sum loop.

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
        RecurrenceGenerationError: See :func:`template_monthly_or_none`.
        BaselineMissingError: See :func:`template_monthly_or_none`.
    """
    total = Decimal("0")
    for template in templates:
        monthly = template_monthly_or_none(template, ctx)
        if monthly is not None:
            total += monthly
    return round_money(total)
