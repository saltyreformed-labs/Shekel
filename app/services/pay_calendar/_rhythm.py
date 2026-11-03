"""How a pay calendar is COUNTED: how many paydays a month or a year holds.

The package's FOURTH shape.  :mod:`._searches` answers "which period is X",
:mod:`._views` answers "which SLICE of this schedule does a surface report
over", :mod:`._walks` answers "keep giving me paychecks from here".  This one
answers **"how many times was this owner paid between these two days"** -- a
COUNT of paydays rather than a period, a window or a stream, and the two
questions below are the only two shapes of it anything asks.  Beside them,
since plan step salary:X-av-2, is the count per YEAR the rhythm in force on a
day pays (:func:`cadence_on`) -- the era's count, where
:attr:`~._calendar.PayCalendar.cadence` answers the latest era's.

**Why it is a count and not a window, which is the whole point of the module.**
Its consumers do not want a pay period.  A 24-per-year deduction skips the
THIRD payday of a month and a 12-per-year one is taken on the FIRST, so the
paycheck engine wants a position; the analytics year overview flags a month
holding three paydays, so it wants a size.  Both are answered by counting
paydays, and a payday is a ``date``.  **Returning dates rather than
:class:`~._derive.DerivedPeriod` values is a structural guard rather than a
convenience**: a projected payday is not a period a foreign key can name, and a
value that never becomes one cannot be filed into, cannot be handed to
:meth:`~._calendar.PayCalendar.require_period`, and cannot be mistaken for a
saved row by a consumer that assumes ``period_id is None`` means "in the
future".

*That guard is NOT on its own the answer to ``pay_calendar:R-PC14``, and an
adversarial review of plan step balance:X-bh-1 corrected a draft of this
paragraph that said it was.*  R-PC14 rejects projecting backwards because it
"would attribute money to paychecks that never happened", and plan step
**balance:X-bh-2** does exactly that -- a year-to-date gross IS money attributed
to those paydays, whatever type carries them.  What answers R-PC14 is ruling
**balance:R-IA**: the app stops GUESSING that the earlier paychecks happened and
stores the fact, ``budget.pay_schedule.history_opens_on``, so the rhythm runs
back to a bound the owner stated rather than to one the app inferred.  The
date-return is the narrower guarantee it sounds like -- nothing fabricated can
be FILED into -- which is what keeps `filing_period` and the balance axis
untouched while the rhythm widens.

**One primitive, two questions.**  :func:`paydays_in_month_through` and
:func:`paydays_in_year_before` differ only in the span they ask for, so the
span search is written once (:func:`_paydays_between`) and neither caller
states a bound the other could state differently.  That is the same argument
:mod:`._searches` makes for its bisects: six copies of "which period contains
this day" already disagreed at the edges that matter (ledger row **P6**).

**It sits LAST in the package's chain**, beside :mod:`._walks` and for the same
reason that module gives: it takes a whole :class:`~._calendar.PayCalendar`
because it needs both the payday set and the cadence, and a caller holding the
value object should not have to open it to ask a question of it -- nor be able
to pair one owner's paydays with another owner's cadence.  ``_derive`` ->
``_searches`` -> ``_window`` -> ``_views`` -> ``_calendar`` -> this.  **It is
not a method on that class**, and that is a measured constraint rather than
taste: ``_calendar.py`` stands within a handful of lines of pylint's
1,000-line ceiling and at 19 of its 20 permitted public methods, and ledger row
**P77** records that the next step touching it breaks both -- which is exactly
what happened at ``recurrence:R16-b-1``, resolved by putting that step's one
method here in the package instead.  *No line count is quoted, deliberately:
the 995 stated here was true for one commit.  Plan step balance:X-bh-2 added a
field to the class and deleted the unreached ``earliest_start_in_month``
method (ledger row N-396), which loosened the METHOD budget by one slot and
left the file one line CLOSER to the ceiling, not further -- an adversarial
review of that step corrected a draft of this sentence that claimed the
deletion paid for the addition.  pylint measures the number on every commit; a
copy in prose only decays, and this one decayed within its own step.*

**The rhythm runs in BOTH directions, and the two ends are NOT symmetric.**
Forward past the schedule's horizon it runs to
:data:`~app.utils.dates.CALENDAR_DATE_MAX` (ruling ``pay_calendar:R-PC9``) --
a property of the APPLICATION, and genuinely unbounded, because the balance
axis projects years out and :func:`~._walks.paychecks_from` walks to the end of
the app's calendar.  Backward below the opening payday it runs to
``budget.pay_schedule.history_opens_on`` (ruling ``balance:R-IA``, plan step
**balance:X-bh-2**) -- a property of the OWNER, which no derivation can supply,
and which is asked for rather than assumed.  **A ``NULL`` there means NOT
STATED and the backward half answers nothing**, which is the ruling's
2026-08-31 amendment: an owner nobody has asked has made no claim, and the
first form of the rule let the absence of a question stand in for one.

The backward end does not need to be unbounded and is not: the only two readers
of this rhythm ask over one calendar month or one calendar year, so it never
reaches more than about twelve months below the record.  The mirror with
``CALENDAR_DATE_MAX`` was aesthetic, and an adversarial review of this step
priced what taking it literally cost -- a ``$200,000`` salary whose record
opens mid-2026 withholding ``$1,437.91`` less Social Security tax, because a
year of paychecks nobody had claimed retired the wage base early.

Until this step the backward half did not exist at all, so a month or a year
the record opens inside was counted from the first RECORDED payday: measured on
the production owner, whose schedule opens 2026-03-26, the 2026 wage total for
2026-05-21 read ``$14,103.84`` from four paydays against the ``$31,733.64`` of
the nine he was really paid (ledger row **N-390**).  He states his opening and
the rhythm answers it; an owner who states nothing keeps that reading.

**The backward bound is a FLOOR and never an anchor**, which is what keeps the
two halves one rhythm rather than two.  The days below the record are stepped
back from the EARLIEST ERA's phase at that era's cadence (**R-PC66**: the
earliest era alone runs backward below the record; plan step ``C17-b-2``,
which moved the anchor off the first RECORDED payday -- ledger row
**PC-502**) and dropped once they fall under the floor; the stored day is not
itself treated as a payday.  Anchoring on it instead would put a short gap at
the seam whenever the day the owner remembers does not land on the grid --
which it need not, and for the production owner does not.  A short gap
inflates that month's payday count by one, which is exactly the ordinal the
deduction cadence reads.

*What the floor GIVES UP, said because an adversarial review of plan step
balance:X-bh-2 found only the other half written down.*  An anchor would place
the owner's real first paycheck on its real day, and a floor cannot: the
earliest day this returns is a phase-derived one within a cadence of the
stated day, never the stated day itself.  Where the real first paycheck was
shifted ACROSS a year boundary that is a wage-base year attributed wrongly --
ledger row **N-398**'s subject, which is therefore partly a consequence of
this choice rather than an independent limitation.  The seam cost was judged
the larger because it lands on every month of the projection while the
attribution cost lands on one day of one year.

**The floor bounds the PROJECTION and never the RECORD.**  A saved payday
below a stated ``history_opens_on`` is still counted, because a row the owner
entered is a fact and the bound is a statement about the unrecorded past.  The
pairing is reachable -- ``/pay-periods/reset`` rebuilds a schedule from an
EARLIER first payday without touching ``budget.pay_schedule`` -- and it
degrades to "no backward rhythm" rather than to a wrong count.

Pure: no session, no clock, no Flask.  Every answer is a function of the
paydays and the cadence the calendar carries.
"""

from datetime import date, timedelta
from itertools import takewhile

from ._cadence import PayCadence
from ._calendar import PayCalendar
from ._eras import grid_below_record, matched_planned, projected_payday
from ._grid import cadence_steps_to
from ._searches import paydays_between
from ._views import projected_paychecks


def paydays_in_month_through(
    calendar: PayCalendar, day: date,
) -> "tuple[date, ...]":
    """Return the paydays of *day*'s calendar month falling on or before it.

    **The single "where does this payday sit in its month" rule**, and the two
    consumers that need it want different halves of one answer.  For a day that
    IS a payday the length is that payday's 1-based position in its month, so
    the paycheck engine reads ``>= 3`` for the third paycheck a 24-per-year
    deduction skips and ``== 1`` for the first a 12-per-year one is taken on --
    two predicates that were two independent scans of a caller-supplied period
    list until plan step **balance:X-bh-1**.  Asked at a month's LAST day the
    length is how many paydays that month holds, which is what the analytics
    year overview flags as a three-paycheck month.

    One rule rather than two is load-bearing rather than tidy: the engine and
    the year overview disagreed about the same month before this step, because
    each counted over whatever period set its own caller happened to hold.

    **A consequence of the backward half worth stating, because it looks like
    a loss and is not** (plan step balance:X-bh-2).  In the month the record
    OPENS, an earlier rhythm payday can now take ordinal 1 -- so a 12-per-year
    deduction, which is taken only at ordinal 1, moves onto a paycheck the app
    holds no row for and is charged in that month to NOTHING.  That is the
    honest answer rather than a gap: the owner really paid it on the earlier
    paycheck, and charging it to the recorded one put a deduction on a paycheck
    that did not have it.  It happens once per owner, in one month, and only
    for a deduction taken at ordinal 1.  **The direction is UP** -- that
    paycheck's net rises by the deduction -- which is the optimistic direction
    and so the one to say out loud.

    Args:
        calendar: The owner's schedule.
        day: Any calendar day.  Its month is the span, and it is the inclusive
            upper bound within that month.  It need not be a payday -- the
            month-size question asks at a month end -- but when it is one, it
            is counted.

    Returns:
        The paydays, ascending, as plain ``date`` values.  Empty when the month
        holds none at or before *day*, which is a real answer: a cadence longer
        than a month leaves months empty, and a stated ``history_opens_on``
        stops the rhythm somewhere.
    """
    return _paydays_between(calendar, _month_opens(day), day)


def saved_paydays_in_month_through(
    calendar: PayCalendar, day: date,
) -> "tuple[date, ...]":
    """Return the RECORDED paydays of *day*'s month falling on or before it.

    :func:`paydays_in_month_through`'s bounded twin, over the same month span
    and the same search -- what differs is the SET, and each name says which
    one it means.  This one counts only paydays the app HOLDS; that one
    continues the owner's rhythm past the schedule's horizon.

    **Two sets rather than two rules, and the distinction is the developer's
    ruling balance:R-IB.**  The paycheck engine needs the total answer: a
    paycheck it cannot place would take ordinal 0, and 0 silently drops a
    12-per-year deduction and takes a 24-per-year one on a third paycheck.
    The analytics month card needs the bounded one, because everything else on
    that card -- its income, its expenses, its net, its month-end balance -- is
    folded from SAVED periods, so a projected payday would appear beside a
    ``$0.00`` net and an unmoved balance.  Measured 2026-08-30 on the
    developer's own data before the bound went in: 29 month cards, 2028-08
    through 2030-12, read "3 paychecks, income ``$0.00``, balance
    ``$9,539.92``".

    **The bound comes OFF when the cash tier projects** -- ledger row
    **N-394**, which is what pairs the two halves of that card rather than
    silencing one of them.  Until then this is where the card says how far its
    own evidence reaches.

    Args:
        calendar: The owner's schedule.
        day: Any calendar day; its month is the span and it is the inclusive
            upper bound within that month.

    Returns:
        The recorded paydays, ascending.  Empty for a month the saved schedule
        opens no period in, which is a real answer.
    """
    return paydays_between(calendar.periods, _month_opens(day), day)


def _month_opens(day: date) -> date:
    """Return the first day of *day*'s calendar month.

    One line, and it is a function because both month questions above take
    this bound: written twice it would be two places for "which month is this"
    to drift, which is the shape ledger row **P6** counts.
    """
    return date(day.year, day.month, 1)


def paydays_in_year_before(
    calendar: PayCalendar, day: date,
) -> "tuple[date, ...]":
    """Return the paydays of *day*'s calendar year falling strictly before it.

    **The single year-to-date rule.**  The paycheck engine's two cumulatives
    both walk it -- the Social Security and Medicare wages that drive the
    Social Security wage-base cap and the Medicare surtax threshold (the
    gross until plan step salary:S11-e) and a deduction's own calendar-year
    total against its ``annual_cap`` -- and
    they walked two separate copies of it over a caller-supplied period list
    until plan step **balance:X-bh-1**.

    The calendar YEAR is the span because that is the window both caps are
    defined over: the tax law states a wage base per tax year
    (:class:`app.tax_law.FicaRules`), and
    :func:`~app.utils.deduction_cap.cap_period_amount` clamps a calendar year's
    total.  STRICTLY before, because a cumulative is what has already been paid
    when this paycheck is priced.

    Args:
        calendar: The owner's schedule.
        day: The payday whose year-to-date is wanted.  Excluded from the
            answer.

    Returns:
        The paydays, ascending, as plain ``date`` values.  Empty for the year's
        first payday, which is the correct cumulative of zero rather than an
        error -- and empty for 1 January whatever the schedule holds, because
        the span it names ends the day before it opens.
    """
    return _paydays_between(
        calendar, date(day.year, 1, 1), day - timedelta(days=1),
    )


def _paydays_between(
    calendar: PayCalendar, first_day: date, last_day: date,
) -> "tuple[date, ...]":
    """Return every payday in ``[first_day, last_day]``: back, saved, forward.

    The one span search both public questions above are asked through.  Stepped
    backward below the record's opening payday, read off the schedule where it
    reaches, and projected at the owner's cadence beyond it -- three segments of
    ONE rhythm, in that order, each ascending and none overlapping the next.

    **It COMPOSES producers and states only where the span stops.**  The saved
    half is :func:`~._searches.paydays_between`, which owns the bisect pair and
    the empty-span disposition; the forward continuation is
    :func:`~._views.projected_paychecks`, the ONE loop that steps it (ledger row
    **P6**), told where to START so its cost is the size of the answer rather
    than the distance from the horizon -- an adversarial review of plan step
    balance:X-bh-1 measured **442 ms** on one year-overview render before that
    argument existed, against **0.6 ms** after (2026-08-30).  The backward
    continuation is :func:`_backdated_paydays` below.

    **The two continuations do not share a producer, and that asymmetry is
    deliberate.**  The forward one must yield :class:`~._derive.DerivedPeriod`
    values because :func:`~._views.axis_window` and :mod:`._walks` consume the
    same sequence, so a second forward walk here would be the duplicate row P6
    counts.  Nothing else asks for the backward one, and it must NOT produce
    periods at all -- a fabricated period is a thing a consumer could try to
    file into, which is exactly what ruling ``pay_calendar:R-PC14`` refuses.
    What the two DO share is the arithmetic: both step the progression through
    :func:`~._grid.cadence_steps_to`, so there is one statement of where a
    paycheck lands at a cadence and two directions of reading it.  *That
    function moved out of* :mod:`._derive` *at plan step ``C14-d``, which split
    the NOMINAL grid from the projection that displaces it; the arithmetic is
    unchanged and this module reads the grid, which is what it always read.*

    **It reads every payday the calendar HOLDS, saved or not**, where the
    producers it replaced read :meth:`~._calendar.PayCalendar.saved`.  For a
    calendar built by :func:`~._loader.calendar_for` the two sets are equal --
    it reads saved rows and nothing else -- so nothing moves today.  The
    difference is deliberate rather than incidental: an unsaved candidate
    payday is still a day the owner is paid on, which is what a COUNT asks,
    and :meth:`~._calendar.PayCalendar.saved` additionally REFUSES a gapped
    schedule, so routing a count through it would make "how many paydays does
    this month hold" raise for a state that has an answer.

    Args:
        calendar: The owner's schedule.
        first_day: Inclusive lower bound.  May lie below the schedule's opening
            payday, which is what the backward rhythm is for.
        last_day: Inclusive upper bound.  May lie past the schedule's horizon,
            which is what the forward projection is for.

    Returns:
        The paydays, ascending, as plain ``date`` values.
    """
    if last_day < first_day or not calendar.periods:
        return ()
    periods = calendar.periods
    backdated = ()
    if first_day < periods[0].start_date:
        backdated = _backdated_paydays(calendar, first_day, last_day)
    saved = paydays_between(periods, first_day, last_day)
    projected = ()
    if last_day > periods[-1].end_date:
        # ``takewhile`` rather than a hand-rolled loop so the stop condition is
        # the whole of what this adds.  The filter keeps the paydays that OPEN
        # in the span rather than the periods that overlap it -- this counts
        # paychecks, not coverage -- and it is what absorbs the one period
        # ``from_day`` may yield from before the span, which that argument
        # documents as deliberate.
        projected = tuple(
            period.start_date
            for period in takewhile(
                lambda paycheck: paycheck.start_date <= last_day,
                projected_paychecks(periods, calendar.eras, first_day),
            )
            if period.start_date >= first_day
        )
    return backdated + saved + projected


def _backdated_paydays(
    calendar: PayCalendar, first_day: date, last_day: date,
) -> "tuple[date, ...]":
    """Return the rhythm's paydays in the span that fall BELOW the record.

    Plan step **balance:X-bh-2**, ruling **balance:R-IA** as amended
    2026-08-31.  The mirror of :func:`~._views.projected_paychecks` for the
    other end of the schedule: the owner's paydays continue at their cadence
    below the first RECORDED one just as they do above the last, and until this
    existed the engine counted nothing there -- so a month or a calendar year
    the record opens inside was measured from the record's boundary rather than
    from the owner's.

    **An UNSTATED history answers ``()``, which is the amendment and the whole
    disposition of this function.**  ``history_opens_on`` is ``NULL`` for every
    owner nobody has asked, and NULL means exactly that -- not "this owner has
    always been paid this way".  Two facts with opposite epistemics cannot
    share one encoding: R-IA's first form let NULL mean the claim, so an owner
    who had never seen the question was counted as having made it.  That is
    the guessing the ruling set out to stop, relocated into the default.

    **The direction is why the unstated answer is the RECORD and not the
    calendar's floor.**  Under-counting a year-to-date reaches the FICA wage
    base late and exhausts an ``annual_cap`` late, both of which UNDERSTATE
    net; under-counting a month ordinal makes a 24-per-year deduction more
    likely to be taken, which understates net again.  Over-counting inverts
    all three.  An application that budgets should guess poor, so where it has
    not been told it counts only what it holds -- which is also, exactly, the
    behaviour before this step, so stating nothing changes nothing.

    **Two bounds decide the rest, and they are different kinds of thing.**  The
    ceiling is the record: strictly below ``periods[0].start_date``, because at
    and above it the saved half is authoritative and a day counted twice would
    inflate every ordinal and every year-to-date.  The floor is the OWNER's
    stated day, inclusive -- a day ON the rhythm is kept, which is the natural
    answer to the question both forms ask.

    **The grid is the EARLIEST ERA's, anchored on its phase** (plan step
    ``C17-b-2``, ruling **R-PC66**; ledger row **PC-502** closed).  Until then
    it was stepped from the first RECORDED payday, which ``C14-e-3``'s writer
    records DISPLACED -- so an owner whose record opens on a closed day had
    every backdated payday off by that displacement, in the half that feeds
    the FICA wage base and every ``annual_cap``: **22** of the 684 rhythm days
    below production's record displace under either convention.  The era and
    the top step come from :func:`~._eras.grid_below_record` since plan step
    ``pay_calendar:C18-b``, the one statement of the grid below the record
    that the "Add earlier paychecks" door records from too, so the door
    cannot write a payday this count does not stand on.  *It asked for the
    era by the span's upper day (:func:`~._eras.era_index_at`) until then,
    which answers the earliest era for every day below a record that opens
    in that era's span -- every record a door writes.*

    **A floor at or after the opening payday yields nothing, and that is a real
    answer rather than a degenerate one.**  It is what an owner whose first
    payday has not happened yet states.  It is also reachable AFTER the fact:
    ``/pay-periods/reset`` rebuilds a schedule from an EARLIER first payday and
    leaves the stored floor untouched, so a bound accepted when it was written
    can end up above the record.  The producer stays total for that, and the
    saved half is unaffected -- the floor bounds the projection, never the rows.

    **``CALENDAR_DATE_MIN`` is no longer a MEANING here, only a bound.**  It is
    where ``ck_pay_schedule_history_opens_range`` stops, so a stated floor
    cannot precede the application's own calendar; nothing reads it as "as far
    back as possible" any more.  An owner who has been paid this way for years
    says so by entering an early date, and because the only two readers of this
    rhythm ask over one calendar month or one calendar year, any day at or
    before 1 January of their earliest priced year is equivalent.

    ARITHMETIC rather than a walk from the anchor, for
    :func:`~._projection.project_period_after`'s reason: the highest rhythm day in
    the span is one division away, so the loop below runs once per payday
    RETURNED rather than once per payday between the span and the record.  At
    the one-day cadence ``budget.pay_schedule`` admits, a January question
    asked of a 2029 record is 31 steps here and would be ~1,100 from the anchor.

    **It DISPLACES since plan step ``C14-e-3``, and that half is what closes
    ledger row N-398.**  It open-coded the rhythm twice --
    ``opening + timedelta(days=steps * cadence)`` and ``day -= timedelta(days=
    cadence)`` down the loop -- and ``C14-c`` REFUSED routing them to
    :func:`~._derive.projected_payday` as unruled, because that would have made
    the backward rhythm displace in a step that moved no money.  It is ruled
    now: ``C14-e-3`` displaces *every projected and backdated payday*, so both
    spellings are gone and this reads the same producer the forward half does.
    **The forward half alone does not close N-398**: that row's own worked
    example is the rhythm day 2026-01-01 that payroll really paid 2025-12-31,
    which crosses a TAX YEAR -- so the wage base and every ``annual_cap`` turn
    over between the nominal day and the real one, and only a backward walk
    that displaces puts the paycheck in the year it was paid in.

    **The GRID is walked and the DISPLACED day is FILTERED, so the walk runs
    one cadence past the top of the span and not past the bottom.**  The
    asymmetry is a theorem rather than a margin, and it rests on a
    displacement being strictly shorter than a cadence
    (:func:`~app.utils.business_days.shortest_collision_free_cadence` is the
    longest closed run PLUS ONE, and
    ``pay_schedule_service.reject_shift_on_short_cadence`` holds a displacing
    convention above it).  Above the span: the first grid day past *upper*
    can be paid at or below it -- ``prior`` does exactly that to the
    2026-01-01 payroll paid 2025-12-31 -- while the NEXT one up is more than a
    cadence past *upper* and cannot reach back that far, so ONE extra step is
    both necessary and enough.  Below the span: :func:`~._grid.cadence_steps_to`
    already answers the last grid day at or before *lower*, and every day below
    THAT is a full cadence lower, so its payday is under *lower* however
    ``next`` pushes it -- an extra step there is unreachable, and a first cut
    of this function took one anyway.  Both halves are checked against a
    brute-force reference over 4,000 randomised spans -- both conventions,
    cadences from the collision floor to 40, spans to 800 days below the record
    (``test_pay_calendar_rhythm.py``).  The result stays ASCENDING because
    :func:`~app.utils.business_days.shift_to_business_day` is monotone and the
    same floor forbids the collision that would flatten two grid days onto one.

    **The two halves are partitioned by GRID INDEX and not by DAY, and an
    adversarial review of ``C14-e-3`` is why.**  The saved half owns the step
    the recorded opening payday STANDS FOR (:func:`~._eras.matched_step` --
    step ``0`` wherever the opening is the era's own first payday, which is
    every owner a door has written) so this one stops at the step below it,
    and the ``min`` below is that bound rather than a guard.  A first
    cut bounded only by the DAY ``opening - 1`` and argued that step ``0``
    could never reach it, because ``opening`` is a day money moved and so is
    its own displacement.  **That is false of every payday recorded BEFORE the
    convention was chosen**, which is two POSTs of ``/pay-periods/generate``
    away -- a door ``app/routes/pay_periods.py`` says out loud "reads as
    first-time-only and is not".  Measured on a schedule opening 2026-11-26,
    Thanksgiving, under ``prior``: November answered
    ``(11-12, 11-25, 11-26)``, three paydays for an owner who holds two, the
    2026 year-to-date read **25** against a true 24, and the phantom was the
    DISPLACEMENT of the recorded opening standing beside the opening.  Its
    direction is the one this module's own docstring says to avoid: an extra
    paycheck reaches the FICA wage base and every ``annual_cap`` early, which
    understates tax and OVERSTATES net.

    Below the opening's step nothing can reach the record either: a grid day
    a whole cadence under the opening cannot be pushed up to it, by the
    sub-cadence bound above.  So the index bound and the day filter together
    select exactly the days the saved half does not hold.

    Args:
        calendar: The owner's schedule.  Non-empty, and *first_day* is already
            known to fall below its opening payday -- both are the caller's,
            which is the only caller.
        first_day: Inclusive lower bound of the span being counted.
        last_day: Inclusive upper bound of the span being counted.  Clamped
            here to below the opening payday; the saved half answers above it.

    Returns:
        The paydays, ascending, as plain ``date`` values.  **Empty when the
        owner has stated no history**, which is the commonest answer and the
        first thing this checks; empty too when the floor, the span and the
        record leave no room between them.
    """
    floor_day = calendar.history_opens_on
    if floor_day is None:
        return ()
    opening = calendar.periods[0].start_date
    lower = max(first_day, floor_day)
    upper = min(last_day, opening - timedelta(days=1))
    if upper < lower:
        return ()
    era, top_step = grid_below_record(calendar.eras, opening)
    anchor, rhythm = era.effective_from, era.rhythm
    cadence = rhythm.cadence
    # The GRID indices whose PAYDAY can fall in ``[lower, upper]``, counted
    # from the era's phase.  ONE extra step above and none below: a
    # displacement is shorter than a cadence, so only the first grid day past
    # ``upper`` can be paid back inside the span, and no day below the last
    # one at or before ``lower`` can be pushed up into it.  The docstring
    # above carries the theorem.
    first_step = cadence_steps_to(anchor, cadence, lower)
    # Never the opening's own step: that index IS the recorded opening payday,
    # which the SAVED half answers (``top_step`` is the one below it).  Under
    # ``prior`` its displacement falls below ``upper`` and would stand beside
    # it as a phantom paycheck.
    last_step = min(cadence_steps_to(anchor, cadence, upper) + 1, top_step)
    return tuple(
        payday
        for payday in (
            projected_payday(anchor, rhythm, steps)
            for steps in range(first_step, last_step + 1)
        )
        if lower <= payday <= upper
    )


def cadence_on(calendar: PayCalendar, payday: date) -> PayCadence:
    """Return the cadence the owner was paid at on *payday*: its era's.

    **The per-payday answer :attr:`~._calendar.PayCalendar.cadence` does not
    give** (plan step salary:X-av-2, ruling **R-SAL66**; ledger row
    **SAL-569**).  That property answers how often the owner IS paid -- the
    LATEST era's rhythm, the one that continues past the record -- which is
    the right answer to every question about the future.  A paycheck paid
    under an earlier era was paid at THAT era's rhythm, so the paycheck
    engine's divisor and the count Pub 15-T annualises it by are that era's
    count.  Read off the latest era instead, a monthly-era paycheck under a
    later biweekly era was priced at 12/26 of its pay.

    **The era is the one whose PLANNED payday the day stands for**
    (:func:`~._eras.matched_planned`), the rule the calendar places a
    recorded payday by, and it is read in CASH days.  It starts from
    :func:`~._eras.era_index_at` -- an era pays from its first payday, its
    ``effective_from`` displaced under its own convention, so an era taking
    effect on a closed day, whose first paycheck is paid BEFORE its
    ``effective_from``, owns that paycheck -- and differs from it only for a
    record paid early just before a seam, which stands for the NEXT era's
    first payday and is that era's paycheck (the calendar closes it at that
    era's rhythm).  A projected or backdated payday IS a planned payday, so
    for every day the calendar yields rather than records the two readers
    agree.  :func:`~app.services.pay_rhythm.era_covering` asks the question
    in NOMINAL days, the writer's coordinate, and would hand the displaced
    first paycheck to the era before it.  *The first draft of this function
    read ``era_index_at`` alone and said it was the rule the calendar
    derives its periods by; an adversarial review of X-av-2 found the
    early-paid record it misplaces.*

    A free function here rather than a method beside ``cadence``, for ledger
    row **P77**'s reason (this module's docstring states it).

    Args:
        calendar: The owner's schedule.
        payday: The day a paycheck arrives -- saved, projected past the
            record, or stepped back below it (where the earliest era covers
            it, by the same rule).

    Returns:
        The covering era's :class:`~._cadence.PayCadence`.
    """
    index, _step = matched_planned(calendar.eras, payday)
    return PayCadence(calendar.eras[index].rhythm.cadence)


__all__ = [
    "cadence_on",
    "paydays_in_month_through",
    "paydays_in_year_before",
    "saved_paydays_in_month_through",
]
