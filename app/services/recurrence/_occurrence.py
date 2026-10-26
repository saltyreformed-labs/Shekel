"""
Shekel Budget App -- The forward occurrence engine (plan step R3)

Generation, stated forward.  :func:`occurrences` walks a rule's own cadence
from its first occurrence; :func:`place` carries one occurrence DATE onto the
pay period the row lives in; :func:`occurrence_placements` composes the two.

**This is what generation is.**  Plan step R3 built it parallel and unread;
plan step R4a made ``recurrence_engine.match_periods`` a thin adapter over it,
so every pay period the application generates a row into is selected here; and
plan step R4b-2 deleted that adapter, so every reader now takes the
``(occurrence, period)`` pairs themselves through
:func:`~app.services.recurrence.rule_occurrences`.

Why forward, and what that fixed
--------------------------------

Selection used to be a REVERSE mapping: it scanned every candidate pay
period and asked whether that period contained the rule's target day, through
five near-identical ``_match_*`` helpers.  Each of them inspected only the
months of a period's two ENDPOINTS, so a period spanning more than two months
could not match the interior ones -- and ``cadence_days`` is user-selectable
1..365 (``schemas/validation/pay_periods.py``), so that is reachable
configuration.  Measured on the R1 baseline's own 90-day schedule: a monthly
day-1 rule over three years matched 13 periods instead of 36 (and returned one
period TWICE, which the paycheck-keyed generation index of the day could not
store), and a quarterly rule matched 1
of its 12 occurrences.  That was plan defect **D3**.

Generating forward and then placing removes the defect structurally rather
than by widening the scan: every occurrence the cadence names is emitted, at
every cadence, and a date the schedule cannot host is an explicit "no period"
rather than an occurrence that was never looked for.

Why the schedule is threaded in (finding **D9**)
------------------------------------------------

The plan's stated signature was ``occurrences(rule, window)``.  It cannot
serve the ``PERIOD`` unit: those occurrences are PAYCHECKS, and which dates
are paydays is a property of the owner's schedule, not of the rule.  So the
calendar is an argument, and it is what makes the function total over
:class:`~app.enums.RecurrenceUnitEnum` rather than over three of its four
members.

What an occurrence IS, per unit
-------------------------------

* ``MONTH`` / ``YEAR`` -- the day ``starts_on`` names, in every
  *interval_n*-th month (or every *interval_n*-th YEAR, which is the same walk
  with a step of twelve), month-end clamped per month.  The day clamped from is
  :attr:`ResolvedRecurrence.nominal_day` when ``starts_on``'s own month was too
  short to hold it, and ``starts_on.day`` otherwise (ruling R-R3) -- which is
  what keeps a day-31 rule on the last day of every month instead of decaying
  to the 30th forever.  Both are read through
  :attr:`ResolvedRecurrence.day_of_month`, the one place that join is written.
* ``WEEK`` -- ``starts_on`` plus multiples of ``7 * interval_n`` days.  **This
  walk is correct and unreachable**, and plan step R8-a re-stated why: the unit
  is kept out of the offer set by
  :func:`~app.services.recurrence._offer.has_row_date_coordinate`, because
  a weekly occurrence is neither a payday nor a day of the month and
  ``recurrence.compute_due_date`` dates a row from its occurrence only for
  a cadence naming a day of the month (plan step R5-a).  Plan step **R8-b**
  dates a weekly row from its occurrence too, and the unit becomes
  authorable by deleting that predicate.  It is implemented here rather than
  refused because a partial function over an enum is the defect this redesign
  exists to remove, and because a walk that silently ignored the unit would be
  a wrong answer rather than an error.
* ``PERIOD`` -- **the qualifying paycheck's own payday.**  A paycheck qualifies
  when it has not already ENDED before ``starts_on`` -- which is what the
  reverse matcher selected (``p.end_date >= effective_from``) -- and it is
  deliberate that a mid-period date bills in that period rather than the next:
  a loan whose first installment falls mid-period pays from that paycheck
  (plan step C9a).  **Since plan step R7c-b there is nothing to reconcile
  here**: ``resolve`` NORMALISES an authored date onto that paycheck's own
  payday before this walk ever sees it, so ``starts_on`` is this sequence's
  first element by construction.  Plan ledger row **D6** recorded the
  asymmetry that produced -- one field meaning an occurrence for three units
  and a BOUND for the fourth -- and it is what ruling R-R16 removed.

**A consequence worth stating rather than discovering: placement is INERT
under the ``PERIOD`` unit.**  Every occurrence it emits is a paycheck's own
``start_date``, and both placements carry such a date back to that same
paycheck -- ``CONTAINING_DATE`` because the period contains its own opening
day, and ``PERIOD_STARTING_ON_OR_AFTER`` because no earlier period starts
later.  Past the SAVED horizon both answer ``None`` instead, and they still
agree: since plan step R16-b-1 the walk names PROJECTED paydays too, and a
projected payday is not a row either search can return.  That is the ordinary
"the schedule has not got there yet", which is what ``period is None`` means
everywhere else here.  The
plan's section 3 says the opposite ("a mid-period bound places differently
under the two placements"); that claim reads the anchor as the emitted
occurrence, which the paragraph above is exactly the decision not to do.
Emitting the payday is what reproduced the reverse matcher's date as well as
its period: ``compute_due_date`` returns ``period.start_date`` for a rule
naming no day of the month, so a mid-period bound emitted verbatim would have
moved the first row's date.

The per-month CEILING (plan step salary:R15-a, ruling R-SAL29)
--------------------------------------------------------------

A rule may admit at most N occurrences in any calendar month
(:attr:`~app.services.recurrence.ResolvedRecurrence.max_per_month`), and
:func:`_ceilinged` is the one place that drops the rest -- between the unit's
walk and the closing bound, for every unit alike.  For the ``PERIOD`` unit the
N are the month's first N paydays on the owner's calendar; for every other
unit they are the rule's own first N occurrences of the month.  It is how
"every paycheck, at most 2 a month" says a payroll benefit taken on a month's
first two paychecks, the cadence ledger row **D59** measured the two axes
unable to state; that function's docstring carries what the alternatives on
those axes were measured to get wrong, and why the paycheck unit counts the
calendar rather than itself.

Bounds are OCCURRENCE bounds (ruling R-R6)
------------------------------------------

The closing (:class:`~app.services.recurrence.Closing`) is applied to
the occurrence, not to the period it lands in.  It holds the bound the owner
AUTHORED and, since plan step R7d-d, any stop DERIVED from the definition's
destination -- a recurring transfer into a loan stops when the debt does -- and
both are applied here because both bound an occurrence.  The reverse matcher bounded
PERIODS -- the end date was tested against a period's START -- so it generated
rows dated outside the window the user stated: measured on the R1 baseline, a
monthly-15th rule ending 2025-06-05 generated a row due 2025-06-15.  That was
plan defect **D5**, and it died here: an occurrence the bound does not admit is
simply never emitted.

A COUNT bound counts OCCURRENCES the cadence names, including any the schedule
does not reach and never places.  "Stop after twelve" is a property of the
rule, not of how many rows the schedule happened to host.

The window, and the ONE answer ``period=None`` now gives (finding **D7**)
-------------------------------------------------------------------------

**It used to be two answers, and plan step C2-b2 collapsed them to one by
making the second unconstructible.**  A pay period's last covered day is now
DERIVED -- it is the day before the next payday
(:func:`app.services.pay_calendar.derive_periods`) -- so consecutive paydays
define adjacent intervals and the schedule TILES
``[opening_bound(), horizon()]`` with no hole and no overlap.  A day inside the
covered span therefore always has a period, and the SCHEDULE GAP that ledger
row D7 described stopped being a state a READER can see.  What was
policing it went with it: the ``SCHEDULE_GAP`` outcome, ``GenerationPlan.gaps``
and ``_recurrence_common.report_schedule_gaps`` (plan steps **C2-b2** /
**C5a**, which is recurrence **R-F10**).

So ``period is None`` means exactly one thing: the SAVED schedule does not
reach this occurrence.  That is ordinary rather than alarming, and on a
perfectly healthy schedule it happens constantly -- under
``PERIOD_STARTING_ON_OR_AFTER`` an occurrence dated after the LAST PAYDAY has
no paycheck to defer onto even though
:meth:`~app.services.pay_calendar.PayCalendar.period_containing` finds the day
covered, which is roughly one biweekly schedule in three (measured: any
schedule whose last period straddles a month boundary).  The next schedule
extend places it.

**Where the DERIVED calendar and the STORED columns disagree, this engine now
believes the derivation** -- and that MOVES MONEY.  Stated here rather than
discovered, because it is the whole risk surface plan step C2-b2 opened
(adversarial review, 2026-08-11, which measured all three shapes).

**All three are now UNCONSTRUCTIBLE, and the paragraph is kept as the record of
what the cutover crossed rather than as a live warning** (plan step
``pay_calendar:C4-c``).  Each was a STORED column disagreeing with the payday
set, and that step dropped both columns; there is one span and one ordinal,
computed on every read, so nothing is left to disagree.  *Until then this
paragraph said the states were unreachable because ``pay_period_write``
re-materialised the derivation on every write and "the owner's next payday
write REPAIRS them" -- true at the time and false since C4-c, which deleted
that machinery along with its subject.  The writer repairs nothing now because
there is nothing to repair.*  The three, as they were:

* **A HOLE was absorbed** (plan ledger row **P27**).  A stored ``end_date``
  short of the next payday left days uncovered; the derivation runs the
  preceding paycheck to the day before that payday, so an occurrence there
  seats against a real period id and generates a row where it used to be
  logged and skipped.
* **The stored CADENCE moved the horizon** (row **P28**).  The last period's
  derived end is the day before the PROJECTED next payday, one cadence on
  (``pay_calendar._derive.projected_payday``, spelled ``payday +
  cadence_days - 1`` until plan step ``pay_calendar:C14-c`` made the two ends
  one rule), so a stored cadence that no longer matched the stored end moved
  the generation window -- SHORTER losing the occurrences past the new horizon,
  LONGER seating rows in a paycheck whose stored span ended before their date.
  **This one has a survivor**: the cadence is still an input to the last
  period's PROJECTED end, so changing it still moves the horizon.  What is gone
  is the DISAGREEMENT -- there is no stored end for it to come apart from.
* **A stored ORDINAL was re-derived** (row **P26**).  ``period_index`` is a
  period's position in payday order, so a stored ordinal that was not
  ``0..n-1`` re-phased every ``Every N Periods`` rule -- including one naming a
  start period, which the plan's first statement of P26 did not cover.

Two consequences ride on the first two.  Where the change puts a SECOND
occurrence of one template into one paycheck, both rows are now GENERATED and
stored -- plan step **R17** re-keyed the unique index onto the occurrence, and
the refusal a 30-day-or-longer cadence used to earn went with it.  Where it
does not repeat, the row was generated with a date ``compute_due_date`` read
off the paycheck's two ENDPOINT months rather than off the occurrence this
module found, so it could be dated in the wrong month entirely -- plan ledger
row **D18**, closed by recurrence plan step **R5-a**, which dates a row from
the occurrence it answers.  Both are measured and pinned by ``TestALegacyScheduleHole``
in ``test_recurrence_engine.test_legacy_schedule_hole``.  Of the three shapes only
the HOLE ever had a detector -- ``scripts/integrity_check.py`` **BA-07**, a
query over the stored column -- and it died with that column at plan step
C4-c, by which point it had no subject either.

:func:`occurrence_placements` generates through the schedule's HORIZON by
default, because that is the last day a placement can succeed at all.  A
caller that passes a later ``through`` gets those occurrences back explicitly,
each carrying ``period=None``.

Several occurrences in ONE paycheck, and why they are not collapsed
-------------------------------------------------------------------

**The condition is "a calendar month the schedule puts no payday in", which
begins at a 30-day cadence** -- not "a cadence longer than a month", which an
earlier draft of this docstring said and which is too narrow by a factor of
three.  It reaches both placements, differently:

* under ``CONTAINING_DATE`` a monthly bill at a 90-day cadence simply occurs
  three times inside one paycheck's span;
* under ``PERIOD_STARTING_ON_OR_AFTER`` -- today's ``Monthly First`` -- three
  months' occurrences DEFER onto the one paycheck that follows them.

Both are real obligations.  A monthly bill is owed monthly whatever the pay
cadence, and the reverse matcher walked PAYCHECKS instead of months, so it
emitted one row per paycheck and silently dropped the rest: 12 rows for 36
months of rent, measured on the R1 baseline's own 90-day schedule.  That was
defect D3, and for ``Monthly First`` it went unmeasured until plan step R3
added the missing oracle shapes.

``budget.transactions`` HOLDS them since plan step **R17**:
``idx_transactions_template_scenario_occurrence`` is UNIQUE over
``(template, scenario, occurs_on)``.  The old index was keyed on the wrong
column -- it is a generation-idempotency guard, and a generated row's identity
is its OCCURRENCE, not its paycheck -- which is what made a repeat unstorable
and what made a MOVED row vacate its own occurrence (ledger row **D57**).

**The rows stay separate; only the grid sums them** (developer ruling,
2026-08-07).  Summing at generation would fit today's index, and that is
exactly why it is tempting -- it buys the fit by destroying which month a
payment covers, by freezing an amount that may change mid-group, and by
putting one row in front of several events.  So the pairs are returned as
generated: this module answers "what does the cadence name", and presentation
is the display layer's.

**A bill funded IN ADVANCE is already expressible, and the claim that it was
not was measured false at plan step R8-a.**  This paragraph read "a third
placement -- the LAST paycheck on or before the occurrence -- is what a bill
funded in advance needs, and the axis does not have it: today's two values both
fund on or after" (plan ledger row **D20**).  Both halves are wrong.
``CONTAINING_DATE`` funds from the paycheck whose span COVERS the occurrence,
so its payday is on or before that date by construction -- measured over five
pay cadences, 0 of 305 seated occurrences funded after theirs.  And the remedy
it named is not a third rule: ``PayCalendar.period_starting_on_or_before``
disagreed with ``period_containing`` on 0 of 8,460 days of a tiling calendar,
because derived periods TILE, and past the horizon it answers the LAST saved
paycheck -- which would seat every future occurrence of every rule in one.

What the axis genuinely cannot say is a LEAD: fund from a paycheck EARLIER than
the one containing the occurrence, so rent due 1 August is paid from the 17
July paycheck rather than the 31 July one.  D20 closed on the measurement and
that capability carries its own ledger row.

Pure: no Flask, no ORM, no clock, no database.
"""
from collections.abc import Iterator
from datetime import date, timedelta

from app.enums import (
    BusinessDayShiftEnum,
    RecurrenceUnitEnum,
)
from app.exceptions import ShekelError
from app.services.pay_calendar import (
    PayCalendar,
    paychecks_from,
    paydays_in_month_through,
)
from app.services.recurrence._months import (
    month_ordinal,
    months_per_step,
    walk_months,
)
from app.services.recurrence._resolution import ResolvedRecurrence

#: Days in a week, for the ``WEEK`` unit's stride.
_DAYS_PER_WEEK = 7


class RecurrenceGenerationError(ShekelError):
    """A resolved recurrence names something this engine cannot generate.

    A broken invariant rather than bad user input, and deliberately NOT
    :class:`~app.services.recurrence.RecurrenceResolutionError`: nothing
    failed to resolve.  The value is well-formed and its cadence is
    understood; what it asks for is a walk this step does not implement (a
    business-day shift, whose first author is plan step R8) or one that cannot
    terminate (a non-positive interval, which
    ``ck_recurrence_rules_positive_interval`` and the write door both refuse,
    so only a hand-built value can carry it).

    Raised rather than ignored because both alternatives are worse than an
    error: silently dropping the shift would date a bill on a day the user
    said it does not fall on, and a zero interval would spin forever emitting
    the same date.
    """


def _week_walk(anchor: date, interval_n: int) -> Iterator[date]:
    """Yield *anchor*, then every ``7 * interval_n`` days.

    Unbounded by design; see :func:`_month_ordinal_walk`.

    Args:
        anchor: The first occurrence.
        interval_n: Weeks between occurrences.  Must be positive.

    Yields:
        Occurrence dates, ascending, until the consumer stops pulling.  Walked
        past ``date.max`` it raises ``OverflowError`` rather than looping.
    """
    stride = timedelta(days=_DAYS_PER_WEEK * interval_n)
    day = anchor
    while True:
        yield day
        day += stride


def _period_walk(
    resolved: ResolvedRecurrence, calendar: PayCalendar,
) -> Iterator[date]:
    """Yield the payday of every paycheck this rule fires in.

    The ``PERIOD`` unit's occurrences, and the reason
    :func:`occurrences` takes a calendar at all (finding D9).  A paycheck
    qualifies when it has not ENDED before ``starts_on`` and when its index is
    in the rule's phase.  Both tests are verbatim what the reverse matcher
    applied (``p.end_date >= effective_from`` and
    ``(p.period_index - offset) % n == 0``), which is what made plan step
    R4a's cutover a no-op for every pay-period-space rule.

    **The phase and the first occurrence are ONE fact, so the divergence this
    note used to record is gone** (plan step R7b-4).  It said: deriving the
    phase from the anchor agrees whenever the anchor names a qualifying period,
    but ``_phased_period_anchor`` fell back to the raw bound when the schedule
    reached NO period in phase, and the derived form would then take the first
    remaining period, in phase or not.  Both halves were symptoms of one cause:
    the phase was stored INDEPENDENTLY of the bound.  ``resolve`` reads the
    phase off ``starts_on`` now (``_derive_offset_periods``), the paycheck that
    date falls in is in phase by construction, and the advancing anchor is
    deleted.  ``resolved`` still carries ``offset_periods`` because this walk is
    the ONE reader of it; plan step R7c-c dropped the COLUMN it used to be
    written to, so what rides here is a derivation and nothing stores it.

    **``starts_on`` is this walk's own first yield, since plan step R7c-b**, and
    that is structural rather than checked: ``_resolution._first_occurrence``
    normalises an authored date onto the payday of the first paycheck not
    ending before it, which is this loop's admission test read directly.
    Nothing has to agree with anything.

    **It walks the owner's paychecks, saved AND projected, since plan step
    R16-b-1 -- and until then it TRUNCATED in silence.**  It iterated
    ``calendar.periods``, the SAVED set, so a caller asking for occurrences
    past the schedule's horizon got fewer dates than it asked for and no
    signal that it had: measured on a production clone (2026-08-27), an
    every-paycheck rule asked through ``2036-01-01`` answered 62 dates ending
    ``2028-07-27`` against the 255 that owner is actually paid in the window,
    the last ``2035-12-20`` -- 193 paydays dropped in silence.
    A truncated walk and a completed one are indistinguishable from the
    occurrences alone, which is the shape ledger row **P23** refuses one
    concept over -- an axis that covers part of its range reads exactly like
    one that covers all of it.

    The horizon is a MATERIALISATION boundary, not a fact about the cadence:
    an owner goes on being paid after the last payday anyone has saved, so
    this sequence goes on naming paydays.
    :func:`~app.services.pay_calendar.paychecks_from` is where
    that continuation lives, beside the derivation it continues, and it bounds
    the sequence at :data:`~app.utils.dates.CALENDAR_DATE_MAX` exactly as
    :func:`~._months.walk_months` does -- so this walk stops where the MONTH
    walk stops, and :func:`_bounded` is once again the ONLY place a bound is
    applied.  (:func:`_week_walk`, the third sibling, is bounded by ``date``
    itself rather than by that constant; the unit is unreachable until plan
    step R8-b opens it, and bounding it is that step's.)

    Args:
        resolved: The recurrence's two-axis meaning.
        calendar: The owner's pay-period schedule.

    Yields:
        Qualifying paychecks' ``start_date`` values, ascending -- saved where
        the schedule reaches, projected at the owner's cadence beyond it.
    """
    for period in paychecks_from(calendar, resolved.starts_on):
        phase = period.period_index - resolved.offset_periods
        if phase % resolved.interval_n != 0:
            continue
        yield period.start_date


def _unbounded(
    resolved: ResolvedRecurrence, calendar: PayCalendar,
) -> Iterator[date]:
    """Return the rule's raw occurrence sequence, before any bound.

    Total over :class:`~app.enums.RecurrenceUnitEnum`: every member has a
    walk, and an unrecognised value raises rather than returning an empty
    sequence that would read as "this rule never fires".

    Args:
        resolved: The recurrence's two-axis meaning.
        calendar: The owner's pay-period schedule.

    Returns:
        An ascending iterator of occurrence dates, unbounded for every unit in
        the sense that nothing about the SCHEDULE stops one -- plan step
        R16-b-1 removed the ``PERIOD`` unit's exception, which was a SILENT
        truncation at the saved horizon rather than a bound anything had
        chosen.  Where each walk finally runs out still differs: ``PERIOD`` and
        the month-spanning units stop at
        :data:`~app.utils.dates.CALENDAR_DATE_MAX`, ``WEEK`` at ``date``'s own
        maximum.

    Raises:
        RecurrenceGenerationError: When *resolved* names a unit this engine
            does not walk.
    """
    unit = resolved.unit
    if unit is RecurrenceUnitEnum.PERIOD:
        return _period_walk(resolved, calendar)
    if unit is RecurrenceUnitEnum.WEEK:
        return _week_walk(resolved.starts_on, resolved.interval_n)
    # The day the rule MEANS, read through the ONE accessor that joins
    # ``starts_on``'s day to ``nominal_day`` (plan step R7a).  It was open-coded here
    # until the display describer needed the same join: two copies of "which of
    # these two fields holds the day" is how a rule comes to fire on the 31st
    # and read as the 30th.  It answers ``None`` for a unit that does not fire
    # on a day of the month, which after the two returns above means a unit
    # this engine has no walk for -- so ONE refusal covers both, and neither
    # ``walk_months`` nor ``clamped_day`` is ever handed a ``None`` to fail on
    # three frames down with a bare ``TypeError``.
    month_day = resolved.day_of_month
    if month_day is None:
        raise RecurrenceGenerationError(
            f"recurrence unit {unit!r} has no occurrence walk: it is neither "
            f"PERIOD nor WEEK and it names no day of the month.  Every member "
            f"of RecurrenceUnitEnum must have a walk -- returning nothing "
            f"would read as a rule that never fires -- and walking a "
            f"day-of-month cadence without a day would fire it on a "
            f"fabricated one."
        )
    # Seeded at ``starts_on``'s own month, so the first date this yields IS the
    # authored first occurrence -- by construction rather than by two
    # implementations agreeing.  **Until plan step R7c-b there WAS a second
    # implementation**: ``_resolution._calendar_anchor`` walked these same month
    # ordinals to derive the anchor on every read, and the two agreed because
    # they shared ``_months``.  The date is authored now, so the derivation is
    # deleted and there is nothing left to share it with.  **The STRIDE** was a
    # second copy of its own: this function computed ``interval_n *
    # MONTHS_PER_YEAR`` for a YEAR cadence while the anchor took one off the
    # pattern table, so ``months_per_step`` states it once.  A YEAR cadence is
    # that walk with a twelve-month stride; there is no separate year
    # arithmetic, so leap-day clamping is the month clamp and cannot diverge
    # from it.
    #
    # ``months_per_step`` is partial over the enum and is NOT guarded here,
    # because reaching this line already proves membership: ``day_of_month``
    # answers non-``None`` only for ``_frequency.has_day_of_month_coordinate``,
    # which is a membership test against ``_months.MONTH_SPANNING_UNITS``
    # itself, which is that function's own key set.  (It read
    # ``_resolution._DAY_OF_MONTH_UNITS`` until plan step R8-a, an alias of the
    # same tuple that moved to ``_frequency`` with the predicate.)  A guard
    # whose only reachability condition is "two hand-written sets
    # disagree" is the fence this project removes rather than tests, and an
    # adversarial review of plan step R7b-1 named it: the sets are one now, so
    # the state is unconstructible rather than merely unreached.
    start = month_ordinal(resolved.starts_on)
    return walk_months(
        start, month_day, months_per_step(unit, resolved.interval_n),
    )


def _ceilinged(
    raw: Iterator[date], resolved: ResolvedRecurrence, calendar: PayCalendar,
) -> Iterator[date]:
    """Yield from *raw*, dropping every occurrence past the Nth in its month.

    **The per-month ceiling, applied in ONE place for every unit** (plan step
    salary:R15-a, ruling **R-SAL29** as amended 2026-09-13).  What "the Nth in
    its month" counts is the sequence the UNIT is measured in:

    * for the ``PERIOD`` unit an occurrence is a payday, and it is admitted
      when it is among the month's first N paydays ON THE OWNER'S CALENDAR
      (:func:`~app.services.pay_calendar.paydays_in_month_through`, the same
      producer the paycheck engine's month-ordinal rule reads) -- so "every
      paycheck, at most 2 a month" is a month's first two paychecks whatever
      paycheck the rule started on, which is what an employer taking a
      benefit premium on the first two paychecks of each month does;
    * for every other unit it is the rule's OWN occurrences, counted per
      calendar month from the first, because a weekly date is not a payday
      and the calendar has no ordinal to answer for it.

    **The first reading is the developer's amendment**, and an adversarial
    review of this step is why: the ceiling first counted the rule's own
    occurrences for every unit, which agrees with the calendar from a rule's
    second month on and disagrees in its FIRST month whenever the rule starts
    on a month's second or later payday -- a ``$100.00`` allowance started
    2026-07-16 would have been paid on 2026-07-30 too, July's third payday,
    which is ledger row **D59**'s defect for one month.  The cost the
    amendment accepts: "every 2 paychecks, at most 1 a month" admits an
    in-phase paycheck only when it is also its month's first, so a month
    whose first payday is out of phase gets none.  No consumer composes the
    two.

    A unit that cannot put two occurrences in a month
    (:func:`~app.services.recurrence.can_repeat_within_month`) never reaches
    either drop: the value is refused at construction rather than ignored
    here.

    **Before :func:`_bounded`, and the order is the semantics.**  A count
    bound counts occurrences the cadence NAMES (ruling R-R6), and an
    occurrence the ceiling drops is one the rule does not name -- so "at most
    2 a month, for 12 occurrences" runs six months, not five and a third.
    Composing them the other way would spend the count on paychecks the rule
    skips.

    Why a ceiling rather than a new unit or placement: the cadence a payroll
    benefit is taken on -- a month's first two paychecks, the third skipped --
    has no spelling on the two axes (ledger row **D59**), and the candidates
    that stayed on them were measured wrong: a semi-monthly unit placed on
    the first paycheck on or after the 1st and the 16th agrees with the
    developer's 2026 paydays and then skips the WRONG paycheck in a month
    whose paydays fall on the 1st, 15th and 29th (his July 2027), and a
    set-valued placement would make one occurrence two rows.

    Args:
        raw: The rule's unbounded occurrence sequence, ascending.
        resolved: The recurrence's two-axis meaning, read for the unit and
            the ceiling.  ``None`` passes every occurrence through untouched.
        calendar: The owner's pay-period schedule, which the ``PERIOD`` unit's
            month tally is counted over.

    Yields:
        The admitted occurrence dates, ascending.
    """
    ceiling = resolved.max_per_month
    if ceiling is None:
        yield from raw
        return
    if resolved.unit is RecurrenceUnitEnum.PERIOD:
        for occurrence in raw:
            if len(paydays_in_month_through(calendar, occurrence)) <= ceiling:
                yield occurrence
        return
    month: tuple[int, int] | None = None
    admitted = 0
    for occurrence in raw:
        key = (occurrence.year, occurrence.month)
        if key != month:
            month = key
            admitted = 0
        if admitted >= ceiling:
            continue
        admitted += 1
        yield occurrence


def _bounded(
    raw: Iterator[date], resolved: ResolvedRecurrence, through: date,
) -> Iterator[date]:
    """Yield from *raw* until the first occurrence past any stopping bound.

    The ONE place a bound is applied, so the three walks cannot come to
    disagree about what a closing bound means.  Every walk is ascending, so the
    first occurrence the bound refuses is also the last one to check.

    **Two branches until plan step R7b-3, and now none.**  It tested
    ``end_date`` and ``max_occurrences`` separately and tested BOTH even though
    ``ck_recurrence_rules_single_end_bound`` refuses the pair, "because a value
    built in memory is not the table".  The bound is one value with three
    shapes now (:class:`~app.services.recurrence.EndBound`), so there is no
    pair to be defensive about and no shape this loop can fail to handle: a
    bound plan step R8 adds arrives with its own
    :meth:`~app.services.recurrence.EndBound.admits`, and this function does
    not change for it.

    **It asks the whole CLOSING since plan step R7d-d, and gained no
    parameter doing it.**  A definition can be stopped by something it did not
    author -- a recurring transfer into a loan stops when the debt does -- and
    that stop is now carried by the value this function already reads
    (:class:`~app.services.recurrence.Closing`) rather than threaded past it.
    The alternative considered and refused was an optional ``narrowed_by=``
    argument here: it would have left every walking caller one forgotten
    keyword away from a plausible answer that admits occurrences the loan does
    not, with nothing to raise on.  A value cannot be forgotten.

    Args:
        raw: The rule's unbounded occurrence sequence, ascending.
        resolved: The recurrence's two-axis meaning, carrying the closing.
        through: The last day the caller asked about.

    Yields:
        The bounded occurrence dates, ascending.
    """
    emitted = 0
    for occurrence in raw:
        if occurrence > through:
            return
        if not resolved.closing.admits(
            emitted=emitted, occurrence=occurrence,
        ):
            return
        emitted += 1
        yield occurrence


def _require_generable(resolved: ResolvedRecurrence) -> None:
    """Refuse a resolved value this engine cannot honour, before any walking.

    The refusals every entry point makes FIRST, in one place so the
    composition cannot answer where :func:`occurrences` would raise -- which
    it did until a neutral review measured it: an empty schedule short-circuits
    to ``()`` before any occurrence is generated, so a business-day shift or a
    zero interval was silently accepted there and refused everywhere else.

    **A THIRD refusal left at plan step R7c-b, and it left by becoming
    UNCONSTRUCTIBLE rather than by being dropped.**  It checked that
    ``nominal_day`` clamped to the day ``starts_on`` carries -- a pair that says
    one thing twice, which the schema could not fully express and so a guard
    stood in for.  That step completes ``ck_recurrence_rules_nominal_day`` with
    the clamp equality and moves the in-memory half into
    :meth:`~._resolution.ResolvedRecurrence.__post_init__`, so the value cannot
    exist and there is no state for a walk-time fence to catch.  A guard whose
    only reachability condition is "somebody built the pair by hand" is the
    fence this project removes rather than tests.

    Args:
        resolved: The recurrence's two-axis meaning.

    Raises:
        RecurrenceGenerationError: When *resolved* asks for a business-day
            shift (plan step R8 is its first author), or when its interval is
            not positive.
    """
    if resolved.shift is not BusinessDayShiftEnum.NONE:
        raise RecurrenceGenerationError(
            f"business-day shift {resolved.shift!r} is not implemented: plan "
            f"step R8 adds the weekend/holiday adjustment, and it needs a "
            f"holiday source this step does not have.  Generating the "
            f"unshifted dates instead would silently date every occurrence "
            f"on a day the rule says it does not fall on."
        )
    if resolved.interval_n < 1:
        raise RecurrenceGenerationError(
            f"recurrence interval_n must be positive, got "
            f"{resolved.interval_n}.  A non-positive interval emits the same "
            f"date forever, so this is refused rather than allowed to spin; "
            f"ck_recurrence_rules_positive_interval and "
            f"app.services.recurrence.resolve both refuse it upstream, so "
            f"only a hand-built value reaches here."
        )


def occurrences(
    resolved: ResolvedRecurrence,
    calendar: PayCalendar,
    *,
    through: date,
) -> Iterator[date]:
    """Return the dates this recurrence fires on, ascending, through *through*.

    The forward half of the redesign's generation model.  Walks the rule's own
    cadence from :attr:`ResolvedRecurrence.starts_on`, applying the value's
    per-month ceiling (:func:`_ceilinged`, plan step salary:R15-a), then its
    whole :attr:`~ResolvedRecurrence.closing` -- the bound its owner authored
    AND any stop derived from its destination -- and the caller's window.  See
    the module docstring for what an occurrence IS under each unit.

    **"Nothing before ``starts_on``" holds for EVERY unit, since plan step
    R7c-b.**  It held for the calendar units only while the ``PERIOD`` unit
    anchored on a BOUND (ruling R-R8) whose paycheck's payday could be earlier
    -- one field with two meanings, plan ledger row **D6**.  ``resolve``
    normalises that date onto the paycheck's own payday now, so ruling R-R6's
    "occurrence-bounded" holds on both sides for all four units and the
    asymmetry is gone rather than documented.

    **There is no LOWER window argument, and each CALLER that has one applies
    it.**  The production paths that state a lower bound narrow generation to a
    window the RULE does not state: both unarchive paths, both regenerate
    paths, ``period_population``'s batch boundary, the preview's display
    choice, and ``recurring_view``'s ``as_of``.  The paths that state NOTHING
    pass ``None`` since plan step R4b-1 -- the template create path,
    ``generate_transfers_for_all_periods`` and ``can_generate_in_period`` --
    because the two defaults ``resolve_generation_plan`` used to apply were
    already inside the anchor.  That is a per-call display / regeneration
    boundary rather than a property of the recurrence, and conflating the two
    is how defect D2 happened -- so it stays out of here.  Plan step R4a's
    adapter applied it for its callers; plan step R4b-2 deleted the adapter,
    and each caller now filters on the placed period's ``end_date``, which is
    the same predicate the reverse matcher applied to its candidate list.

    Refuses its impossible-to-honour inputs EAGERLY rather than on the first
    ``next()``: a caller that builds the iterator and passes it on would
    otherwise see the failure surface somewhere else entirely.

    Args:
        resolved: The recurrence's two-axis meaning, from
            :func:`app.services.recurrence.resolve`.
        calendar: The owner's pay-period schedule.  Required for the
            ``PERIOD`` unit, whose occurrences are paydays (finding D9), and
            unread by the other three.
        through: The last day to generate through.  Required, and there is
            deliberately no default: an unbounded walk over a calendar unit
            does not terminate.

    Returns:
        An ascending iterator of occurrence dates.  Empty when *through*
        precedes the anchor, when the value's closing admits none of
        them, or -- for the ``PERIOD`` unit -- when the owner has no payday at
        all.  **It is COMPLETE through *through* since plan step R16-b-1**, up to
        :data:`~app.utils.dates.CALENDAR_DATE_MAX`, past which this application
        names no date: the ``PERIOD`` walk used to stop at the SAVED schedule's
        horizon and say nothing, so a caller asking past it was answered short
        (62 dates against 255, measured on a production clone 2026-08-27).  The
        schedule having "not got there yet" is a fact about PLACEMENT
        (:func:`place` answering ``None``), never about whether the cadence
        fires.

    Raises:
        RecurrenceGenerationError: See :func:`_require_generable`, plus a unit
            with no walk (:func:`_unbounded`).
    """
    _require_generable(resolved)
    return _bounded(
        _ceilinged(_unbounded(resolved, calendar), resolved, calendar),
        resolved, through,
    )


__all__ = [
    "RecurrenceGenerationError",
    "occurrences",
]
# The placement half -- ``OccurrencePlacement``, ``place`` and the two
# compositions -- lives in ``_placement`` since plan step salary:R15-a (a pure
# move; see that module's docstring).
