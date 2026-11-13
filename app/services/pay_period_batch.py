"""
Shekel Budget App -- Pay Period Batch

**What a batch of paydays IS, and what refuses one** -- split out of
:mod:`app.services.pay_period_write` at plan step ``pay_calendar:C17-c-1``
(developer ruling **R-PC74**, 2026-09-12, closing ledger row **PC-507**).  A
batch has a size this app will materialise in one call
(:data:`PERIOD_BATCH_MIN` .. :data:`PERIOD_BATCH_MAX`,
:func:`reject_out_of_range_batch_size`), a first payday that must be a civil
day (:func:`reject_undatable_payday`), the days it lands on
(:func:`requested_paydays`), a floor it may not start under
(:func:`reject_backward_payday`) and a ceiling it may not start at
(:func:`reject_skipped_paycheck`, since plan step ``pay_calendar:C17-c-2a``).
None of that touches a row: the writer asks every one of these before it
issues a statement, which is what lets it promise that a refused batch leaves
nothing behind.

**Why a module of its own.**  The writer stood at 998 of pylint's 1,000 lines
after ``C17-a`` and ``C17-b-2``, and ``C17-c-2`` -- the leaf that rewrites its
doors -- could not land in a file at the ceiling.  **PC-507 named the remedy a
SPLIT and not a trim**, on the terms ledger row **PC-498** records (answering a
ceiling by deleting argument is how a module loses the reasoning that keeps it
correct), and named the PLACEMENT the developer's rather than the next
session's to assume, as **R-PC60** placed ``_grid`` and **R-PC69** placed
``_projection``.  He placed it here: the whole batch shape, leaving the writer
its two doors and its statements.  What stays there is what
``TestThereIsOneWriter`` pins to that file -- the one ``PayPeriod(...)``
construction and the one ``DELETE`` in ``app/`` -- and this module imports no
model and issues no statement, so that census reads exactly as it did.

**Three of these names are PUBLIC and were private before the move**, for the
reason :mod:`app.services.pay_period_gates` gives: a function another module
calls is part of this module's surface, and keeping the underscore would have
made every call site read as a privacy breach.  Nothing here computes
differently than it did inside the writer -- a move and a rename, ``$0.00``.

**The dependency runs ONE WAY**: the writer imports this module, and this
module imports nothing from the writer, ``pay_period_admin`` or the routes.
Its imports are the derivation (:mod:`app.services.pay_calendar`, whose
producers the batch's days and its floor are answered by), the rhythm value,
the form error it raises, the calendar's window
(:data:`~app.utils.dates.CALENDAR_DATE_MIN`) and, since plan step
``pay_calendar:C18-b``, the one history predicate
(``pay_schedule_service.record_below_history``, ruling **R-PC104**).

The floor and its mirror, and why a third refusal was DELETED
=============================================================

Plan ruling **R-PC1** stated ONE rule -- "the last paycheck must hold no row
dated on or after the new payday".  Tracing it against ``shekel-prod-db`` found
it wrong in both directions, so the developer ruled it into two (2026-08-10): a
structural floor and a financial coverage rule.  What survives is the floor,
and since plan step ``pay_calendar:C17-c-2a`` its mirror stands beside it.

:func:`reject_backward_payday` -- **structural, and HALF TEMPORARY.**  A
STATED batch may not start inside a paycheck the owner already has -- plan
step **C6**'s mid-schedule insert, which **C6 removes** -- nor before their
first one, which splits nothing and which "Add earlier paychecks" records
instead, stating nothing (ruling **R-PC87**, plan step ``pay_calendar:C18-b``;
that half stays).

:func:`reject_skipped_paycheck` -- **structural, and PERMANENT** (rulings
**R-PC67** and **R-PC76**).  A batch's first new payday may not fall at or
past the SECOND payday the owner's plan projects after their record, because
then a whole planned paycheck is missing between the two and the derived
calendar -- which cannot express a hole -- files it into the paycheck before.
Both read the plan through
:func:`~app.services.pay_calendar.planned_paydays_after` -- the floor its
first day over the STORED eras (the calendar as it derives today), the
ceiling its second over the eras the batch leaves STANDING (the calendar it
leaves behind); where a batch retires no era they are one window over one
sequence.

**The coverage rule was DELETED (developer ruling 2026-08-11), and the argument
is recorded because this module could re-derive it.**  It refused any write
that moved a day from COVERED to UNCOVERED underneath a SETTLED row filed in a
surviving period, and it was approved on the claim that stranding such a day
reproduces ``balance:N-128`` -- the two halves of the cash period view
disagreeing.  **That claim was false, and it was the whole of the case for the
rule.**  ``_cash_periods._assemble_figures`` values each column at that
period's OWN ``end_date`` and computes ``period_timing`` as ``moved - net``, so
a settle day past the last reported end is absent from BOTH sides of ruling
R-K's identity and cancels.  The money reports as a timing remainder -- the row
ruling R-DH split out to carry precisely this -- and the balance is right
either way: on that end date the bank had genuinely not taken it.  Pinned by
``test_cash_period_view/test_the_identity.py``'s
``test_a_settle_day_past_the_window_keeps_every_column_exact``, and driven on a
production CLONE: retiring 58 of 61 periods strands three real rows totalling
``$177.47`` and every surviving column reconciles to the cent.

**Two things that are NOT evidence for it, stated because the first draft of
this paragraph offered both.**  Production has never been in the refused state
-- **0** settled rows fall outside its schedule's coverage -- so production is
silent on this rule rather than supporting its removal.  What production shows
is the DESIGN it rests on: 21 of 160 settled rows settle outside their OWN
paycheck (measured 2026-08-11), carried by the remainder with nothing refusing.
And the refusal's message offered THREE remedies, not one -- re-date the row,
move it, or choose a schedule that still covers the day.  The first two falsify
when money moved; the third is declining the edit.  **The measured COST is what
decided it**: 5 of the owner's 61 truncation points refused, one over three
rows that cleared the bank ONE day late.

**"Outside the reported window" is not a windowing nicety on the load-bearing
surface**: ``routes/grid/page.py`` passes the owner's COMPLETE period set, so there
the phrase means "outside every paycheck they have".  The identity holds all
the same -- it is a property of where each column is valued, not of how the
window was chosen -- but the reassurance must not be read as "only a partial
view sees this".

Flask-isolated: takes and returns plain data, never imports ``request`` /
``session``.  Raises :class:`~app.exceptions.ValidationError`, the form error
each refusal actually is, so the route that took the input renders it.
"""

from datetime import date, datetime

from app.exceptions import ValidationError
from app.services import pay_calendar, pay_rhythm, pay_schedule_service
from app.utils.dates import CALENDAR_DATE_MIN

#: Inclusive bounds on how many pay periods ONE call may create.
#:
#: A generation policy rather than a column constraint, which is why it lives
#: beside the writer rather than on a model: 260 is ten years of fortnightly
#: paydays (five of weekly), past any horizon the app renders and well inside
#: what one transaction can generate and populate.  The floor is 1 because a
#: batch that creates nothing is a caller mistake, not a no-op -- at
#: registration it used to surface several statements later as
#: ``create_account`` complaining that the owner had no pay periods.
#:
#: Read by :func:`reject_out_of_range_batch_size` and by the Marshmallow fields
#: in ``app.schemas.validation.pay_periods``, which import them from here: the
#: bound belongs with the batch it bounds, and a schema that stated its own
#: copy is how four form fields came to hold four literals.
PERIOD_BATCH_MIN = 1
PERIOD_BATCH_MAX = 260


def reject_out_of_range_batch_size(num_periods: int) -> None:
    """Refuse a batch size outside what one call may create.

    **The batch's OWN precondition, stated once and asked by the writer before
    anything is written** (plan step X-ad-a).  It was held by each caller's
    Marshmallow field until registration became a fifth caller, and a bound
    held by remembering is a bound the next door does without: ``num_periods``
    was refused by no service at all, so a non-form caller could ask for zero
    periods (which then failed several statements later, in ``create_account``,
    under a message about accounts) or for a hundred thousand (383 years of
    fortnights in one transaction).

    **It used to carry a cadence FLOOR beside this, and plan step C4-c deleted
    it with its subject.**  That floor refused a cadence of 1 because a STORED
    ``end_date`` cannot express a one-day period -- ``ck_pay_periods_date_order``
    required ``start_date < end_date`` and the writer stored
    ``start_date + (cadence_days - 1)`` -- so the INSERT died as an unhandled
    ``CheckViolation`` 500 on both the settings form and the registration form.
    Nothing is stored now, two paydays a day apart simply define a one-day
    period, and pay-calendar findings **P9** and **P33** close with the column.

    The CADENCE bound that remains is ``budget.pay_schedule``'s CHECK, asked by
    that column's own writer,
    :func:`~app.services.pay_schedule_service.reject_out_of_range_cadence`.
    Two bounds, two owners, because they answer different questions -- what may
    be STORED as a schedule, and how much of one the writer will materialise
    in a single call.

    Args:
        num_periods: How many periods the batch would create.

    Raises:
        ValidationError: *num_periods* is outside
            :data:`PERIOD_BATCH_MIN` .. :data:`PERIOD_BATCH_MAX`.  The message
            names the offending value and both bounds.
    """
    if not PERIOD_BATCH_MIN <= num_periods <= PERIOD_BATCH_MAX:
        raise ValidationError(
            f"Number of pay periods must be between {PERIOD_BATCH_MIN} and "
            f"{PERIOD_BATCH_MAX}; got {num_periods}."
        )


def reject_undatable_payday(payday: date) -> None:
    """Refuse a payday that is not a plain ``datetime.date``.

    ``datetime`` is a subclass of ``date``, so the bare ``isinstance`` check
    this replaced accepted one -- and every derived end would then carry a time
    component, comparing unequal to the ``DATE`` column it is stored in and
    placing a day's money by an accident of the process clock.
    ``pay_calendar._validated`` refuses the same value, but it raises
    ``PayCalendarError``, which no route catches; refusing here makes it the
    form error it actually is.

    Args:
        payday: The candidate first payday.

    Raises:
        ValidationError: *payday* is not a ``date``, or is a ``datetime``.
    """
    if not isinstance(payday, date) or isinstance(payday, datetime):
        raise ValidationError(
            f"first_payday must be a date object, got "
            f"{type(payday).__name__}.  budget.pay_periods.start_date is a "
            f"DATE column and the app's civil day is display_today(); a "
            f"datetime here would place a day's money by the process timezone."
        )


def requested_paydays(
    first_payday: date, num_periods: int, rhythm: pay_rhythm.Rhythm,
) -> "list[date]":
    """Return the paydays a batch asks for, whether or not they already exist.

    **It asks the PRODUCER rather than restating its arithmetic** (plan step
    C14-d).  ``first_payday + timedelta(days=cadence_days * step)`` was the
    fourth spelling of the payday rhythm, in the module whose OTHER spelling
    that step deleted, and an adversarial review of ``C14-d`` found it missing
    from the census that step corrected.

    **It RECORDS THE DISPLACED DAY since plan step ``C14-e-3``, which is
    ledger row PC-497 fault 1** -- and this is where the ``$0.00`` stops.  It
    ran the batch on the GRID and recorded what it spaced, so under a
    displacing convention it wrote NOMINAL paydays where the projection showed
    CASH ones.  The two disagreed at the door next to it: ``derive_periods``
    closes the last saved paycheck on the DISPLACED day, so
    :func:`reject_backward_payday`'s floor is a cash day, and an extend
    offering the nominal one was refused -- on a READ path, since
    ``top_up_rolling_window`` reaches this door from ``/grid`` and
    ``/dashboard`` and nothing registers a handler for ``ValidationError``.
    The batch still runs on the GRID: every element is
    ``projected_payday(first_payday, rhythm, step)``, which is the grid day
    ``step`` cadences on DISPLACED, never the previous element displaced and
    stepped from.  That is what keeps the progression from compounding, and it
    is why this takes the whole :class:`~app.services.pay_rhythm.Rhythm` rather
    than the cadence alone.

    **``first_payday`` is READ as a point on the nominal grid, and since plan
    step ``C17-c-2a`` every form door ASKS for one.**  The continue path
    (extend, the rolling top-up) states no first payday at all since plan
    step ``C17-c-2b``: ``pay_period_write.continue_paydays`` records the
    plan's own next paydays, so this function is asked only of a STATED
    batch.  What no door could establish is that the day the OWNER typed is
    on payroll's grid.  *An adversarial
    review of ``C14-e-3`` struck a sentence resting that on the typed day
    being a business day and so its own displacement: being a fixed point of
    the displacement does not make a day a grid point.*  Worked: an owner
    really paid 2025-12-31, because payroll moved the 2026-01-01 nominal day
    back, typed 2025-12-31 -- which is what the sign-up form asked for -- and
    the batch recorded 2025-12-31, 2026-01-14, 2026-01-28 against a truth of
    2026-01-15 and 2026-01-29, every element after the first a day early,
    permanently.  That was ledger row **pay_calendar:PC-504**, CLOSED at
    ``C17-c-2a``: an era carries the anchor the owner states, and the four
    doors that take a payday -- sign-up, first-time generate, regenerate,
    reset -- ask for the SCHEDULED day in one shared sentence
    (``jinja_globals.PAYDAY_NOMINAL_HELP``) beside the convention control
    that says how payroll moves it.

    Args:
        first_payday: The batch's first NOMINAL payday.
        num_periods: How many paydays the batch covers.
        rhythm: The owner's cadence and payday convention
            (:class:`~app.services.pay_rhythm.Rhythm`).  The pair rather than
            the cadence, because the days recorded are displaced under the
            convention.

    Returns:
        *num_periods* days, ascending: the grid days ``rhythm.cadence``
        apart from *first_payday*, each displaced onto a business day.  Under
        :attr:`~app.enums.BusinessDayShiftEnum.NONE` that is the grid itself.
    """
    return [
        pay_calendar.projected_payday(first_payday, rhythm, step)
        for step in range(num_periods)
    ]


def reject_backward_payday(
    surviving_paydays: "set[date]",
    new_paydays: "list[date]",
    stored_eras: "tuple[pay_rhythm.Era, ...] | None",
) -> None:
    """Refuse a STATED batch whose earliest payday falls below the floor.

    **The forward-only rule, keyed on the PAYDAY** (ruling **R-PC1** as split
    2026-08-10).  It replaces ``pay_period_service._reject_overlapping_batch``,
    which bounded a batch on ``max(end_date)`` -- a derived column plan step
    C4-c dropped, and one that made the guard do a second job nothing credited
    it with.

    **It refuses TWO things, and until plan step ``pay_calendar:C18-b`` it
    described one** (ledger row **PC-499**; ruling **R-PC87**: "The date
    check stays for forms that state a start; its description is
    corrected").  The floor is the day the owner's NEXT paycheck opens --
    the day after their latest one ends -- so everything below it is
    refused, and that span holds two states with different reasons and,
    since ruling **R-PC103**, a message each:

    * **A payday INSIDE a paycheck the owner already has** -- from their
      first payday up to the floor -- splits it: **keeping plan step C6
      closed.**  Under the derivation a gap and an overlap are not
      expressible -- consecutive paydays define adjacent intervals -- so a
      split is the one harm left.  C6 owns it, behind two questions ledger
      row **P10** records as unruled: what happens to a row
      ``DerivedPeriod.attribution_day`` would now clamp into the wrong half,
      and whether the split-off payday is repopulated (a monthly billed
      twice) or left empty (income understated for the whole horizon).
      **When C6 answers them, this half is what it deletes.**  The message
      names the paycheck by its span as
      :func:`~app.services.pay_calendar.derive_periods` draws it over the
      kept paydays -- the day before the next kept payday, or the floor's
      eve for the latest -- rather than restating either end here.
    * **A payday BEFORE the first paycheck** splits nothing, and the message
      that said it would was PC-499.  It stays refused for the doors that
      reach it -- they STATE a start, and ``regenerate``, the only one with a
      kept record, rebuilds a tail: a stated batch opening below the record
      would run its own grid forward through the kept paychecks.  The
      paydays before the first are recorded by "Add earlier paychecks"
      (``pay_period_write.prepend_paydays``), which states no date or rhythm
      and so can neither split a paycheck nor leave a gap; the message
      points there.  That half is not C6's and outlives it.

    **The floor is WHERE THE LAST PAYCHECK ENDS, and since plan step C14-d it
    asks the derivation rather than restating it.**  It is
    ``payday_after(stored_eras, latest_payday)`` -- the same call
    :func:`~app.services.pay_calendar.derive_periods` makes to close the last
    saved period, whose ``end_date`` is that day minus one.  So the first day
    NOT inside a paycheck the owner already has is the floor by construction,
    and the two cannot come apart.  Since plan step ``C17-b-2`` that day is
    the next payday of the ERA grid after the one the latest record stands
    for, anchored on the era's phase rather than stepped from the record.

    *That was a maintained agreement until C14-d, and the docstring said so:
    "on any schedule this app can write those two spellings select the same
    set, because the last period's end IS ``payday + cadence - 1``".  A rule
    that two places must always agree is rule 14's tell -- one value with two
    homes -- and the remedy is to delete a home rather than keep them in step.
    The home deleted here is this function's own ``latest_payday +
    cadence_days``, one of the five spellings*
    :func:`~app.services.pay_calendar.projected_payday` *censuses -- and this
    module held TWO of them:* :func:`requested_paydays` *routed to the grid
    producer in the same step, and to the PROJECTION at ``C14-e-3``, so two of
    the five went and the census now names one.*

    **``C14-d`` moved ``$0.00`` and this fence now MOVES MONEY, which is
    ``C14-e-3``.**  That step's own ``$0.00`` was structural rather than a fact
    about stored data -- an adversarial review corrected a first draft resting
    it on every row holding ``none``, which any owner can falsify in one POST
    through the four doors ``C14-b`` shipped -- because
    :func:`~app.services.pay_calendar.projected_payday` took no convention and
    nothing in the pay-calendar package read one.  It reads one now, so the
    floor below is a DISPLACED day for any owner who has answered the question.

    **What the change buys is measured, not asserted** (probe over production's
    own schedule, 1,951 paydays from 2026-03-26 at cadence 14 out to
    ``CALENDAR_DATE_MAX``, 2026-09-05).  Both spellings refuse the same **0**
    paydays with the convention at ``none``.  Under ``prior`` the open-coded
    floor refuses **58** of the owner's own future paydays -- the R-PC47 case
    exactly: a payday nominally
    2026-01-01 is really paid 2025-12-31, and ``latest + cadence`` puts the
    floor on the nominal day and refuses the real one -- and the producer call
    refuses **0**.

    **The floor reads the STORED eras, an obligation written down by an
    adversarial review of ``C14-d``, MOVED by ``C14-e-1`` without being
    gradable, and GRADED at ``C14-e-3``.**  The floor reads the stored eras
    and not the batch's own
    :attr:`Rhythm.shift <app.services.pay_rhythm.Rhythm.shift>`.  The
    argument's rhythm is what the operation LEAVES BEHIND, and a batch that
    changes the convention would otherwise compute its floor under the new one
    while :func:`~app.services.pay_calendar.derive_periods` still closes the
    existing calendar under the old -- the disagreement between fence and
    boundary ``C14-d`` exists to end, reintroduced through the argument list.

    **Nothing could grade that until ``C14-e-3``, which is why it stayed an
    obligation for two steps.**  While
    :func:`~app.services.pay_calendar.projected_payday` returned the nominal
    grid day the stored convention and the batch's own selected the SAME floor,
    so no test could tell this function from one reading the wrong half -- and
    an obligation marked discharged with nothing grading it is worse than one
    left open, because the next reader stops looking.  With the displacement
    live the two part company, and
    ``TestTheFloorReadsTheSTOREDConventionAndNotTheBatchS`` lands both
    directions: stored ``prior`` with an incoming ``next`` must ACCEPT a payday
    on 2030-11-27 (reading the batch's half refuses a day the owner was really
    paid), and stored ``next`` with an incoming ``prior`` must REFUSE it
    (reading the batch's half splits a paycheck they already hold).

    **Under ``next`` it still refused those 58 until ``C17-b-2``**, ledger
    row **N-495**: the ANCHOR was the last RECORDED payday, so a payday
    payroll moved forward carried its whole projection forward with it, and
    the floor inherited exactly the error the derived end had -- the point of
    asking the producer being that the fence cannot be wrong in a way the
    calendar is not.  The anchor is the era's phase now, at both.

    **Why it is not two days, and an adversarial review of C3-b is why.**  That
    step's first cut bounded at ``latest_payday +
    MIN_MATERIALISABLE_CADENCE_DAYS``, on the reasoning that the only insert
    worth refusing is one before an existing payday.  That is wrong by the
    length of a paycheck, and P10's BOTH damage arms are then reachable through
    a door P10 says is closed.  Measured on the two-period fortnightly
    schedule: recording 2026-01-23 shrank the 2026-01-16 paycheck from 01-29 to
    01-22 and moved a row due 01-25 from rendering on 01-25 to rendering on
    01-22, while ``/pay-periods/generate`` left the split-off half EMPTY and
    ``regenerate`` repopulated it beside the row the shrunk half kept -- one
    monthly billed twice in what had been one paycheck.

    Args:
        surviving_paydays: The paydays the owner keeps once this operation's
            retirements are applied, empty for a first-time schedule.
        new_paydays: The paydays this batch would create -- already filtered of
            any that exist, so a re-run naming existing days is bounded on what
            it would actually add -- with the minted era's first payday in
            front when the batch mints one, whether or not the record holds
            that day: the seam it opens is bounded like the paydays it adds.
        stored_eras: The owner's STORED eras
            (:class:`~app.services.pay_rhythm.Era`), which set how far the
            last paycheck reaches.  **Stored rather than the batch's own**,
            which is the whole of the paragraph above: the question is how far
            the existing calendar already reaches.  ``None`` only beside an
            empty payday set -- an owner with no era (every batch that records
            a payday mints one when none covers it) -- where there is no floor
            to apply.  The early return below is what makes that safe, and it
            has to be, because the producer takes the eras.

    Raises:
        ValidationError: The earliest new payday falls before the floor --
            before the owner's first paycheck, or inside one of theirs.
    """
    if not surviving_paydays or not new_paydays:
        return
    latest_payday = max(surviving_paydays)
    floor = pay_calendar.payday_after(stored_eras, latest_payday)
    earliest_new = min(new_paydays)
    if earliest_new >= floor:
        return
    # Two refusals, one message each (ruling R-PC103): a day before the first
    # paycheck splits nothing and has its own door; a day at or after it lands
    # inside a paycheck, named by its span as the calendar derives it.
    first_payday = min(surviving_paydays)
    if earliest_new < first_payday:
        raise ValidationError(
            f"{earliest_new.isoformat()} is before your first paycheck "
            f"({first_payday.isoformat()}). To add paychecks before it, use "
            f"Add earlier paychecks in Settings > Pay Periods."
        )
    paycheck = pay_calendar.containing_period(
        pay_calendar.derive_periods(
            ((None, payday) for payday in surviving_paydays), stored_eras,
        ),
        earliest_new,
    )
    era = stored_eras[pay_calendar.era_index_at(stored_eras, floor)]
    raise ValidationError(
        f"{earliest_new.isoformat()} falls inside a paycheck you already have "
        f"({paycheck.start_date.isoformat()} to "
        f"{paycheck.end_date.isoformat()}), and splitting a paycheck isn't "
        f"supported yet. Choose {floor.isoformat()} or later, when the next "
        f"paycheck opens after your latest ({latest_payday.isoformat()}, paid "
        f"{era.rhythm.cadence.phrase})."
    )


def reject_skipped_paycheck(
    surviving_paydays: "set[date]",
    new_paydays: "list[date]",
    standing_eras: "tuple[pay_rhythm.Era, ...]",
) -> None:
    """Refuse a batch whose earliest new payday skips a whole planned paycheck.

    **A hole in the schedule is UNREPRESENTABLE, not confirmed** (ruling
    **R-PC67**, 2026-09-11; plan step ``pay_calendar:C17-c-2a``, closing
    ledger row **P80**).  The floor's mirror: :func:`reject_backward_payday`
    refuses a payday landing INSIDE a paycheck the owner already has, this
    refuses one that leaves a whole paycheck of the plan MISSING.  The plan's
    next payday after the record (the floor) may be moved -- an owner
    correcting a phase or a cadence going forward states where their next
    paycheck really lands, anywhere from that day up to the one after it --
    but the paycheck after that is not theirs to skip: the derived calendar
    holds no hole, so a batch opening at or past the plan's SECOND projected
    payday files every day between into the last paycheck the owner keeps.
    Worked on P80's own example -- paydays 2026-01-02 and 01-16 at cadence
    14, a rebuild opening 07-31 -- the 13 planned paydays between carry
    ``$45,838.00`` at ruling **R-PC47**'s ``$3,526.00`` gross, and under the
    confirmation this replaced the owner could accept a 196-day paycheck with
    all of it absent at HTTP 200.  Refused, they continue the old rhythm
    (that income appears in the forecast) or state what actually happened
    at the day it happened.

    **It binds at EVERY batch with a survivor, whichever door sent it**
    (ruling **R-PC76**, 2026-09-12).  ``regenerate`` is the door that states
    a start beside a kept record; registration, first-time generate and reset
    have no survivor; and the continue path (extend, the rolling top-up)
    continues the plan.  Ledger row P80 exists because this class of
    constraint was once written per door, so it lives here, asked by the one
    writer, and a future door that keeps a prefix and states a start inherits
    it without its author remembering.  The test tree pays for that
    totality: a fixture that needs a hole (a 2026 block beside a 2024 opening
    payday) builds it through a helper that writes the rows directly, the
    state the tree's own predicate reserves for "a row no application door
    can write".

    **The continue path cannot reach it, since plan step ``C17-c-2b``.**
    Between ``C17-c-2a`` and that step one continue-path state did, and it
    was stated rather than absorbed (``C17-c-2a``'s adversarial review):
    ``pay_period_admin.extend_pay_periods`` restated the LATEST era from the
    horizon (ledger row **PC-509**), so for an owner truncated below a later
    era whose cadence was LONGER than the kept one's -- a weekly era from
    01-02, a 30-day era at 01-30, the record cut to 01-16 -- the top-up
    offered 01-30 at 30 days, skipping the weekly 01-23, and met this
    refusal from ``/grid`` with no handler.  ``C17-c-2b`` made the door
    record the plan's own paydays (01-23, then 01-30) through
    ``pay_period_write.continue_paydays``, a prefix of the sequence this
    function reads, so it is neither asked of that batch nor able to fire on
    it; the two steps deployed together.

    **It replaced ``pay_period_gates.reject_unconfirmed_gap``,
    ``PayPeriodGapRequired``, ``confirm_gap`` and the settings banner**,
    which asked the owner to CONFIRM the hole (plan step ``C14-f``, ruling
    **R-PC64**) and were deleted whole: a confirmed gap was still a gap, and
    the reference that gate graded from -- where the RETIRED tail used to
    open -- was the record rather than the plan, so a batch retiring nothing
    was not graded at all.  The ceiling here reads the plan
    (:func:`~app.services.pay_calendar.planned_paydays_after`, the sequence
    the floor's day is the first of).  Five weeks of unpaid leave is not a
    state this refuses the owner from recording -- it is an ``unpaid`` era
    KIND, which R-PC67 named the honest model of unemployment and deferred
    until asked for.

    **It reads the eras the batch leaves STANDING, not the stored ones**, and
    this step's adversarial review is why.  A rebuild retires every era whose
    first payday falls past the last kept payday, and a retired era takes its
    plan with it: fortnightly from 01-02 with a 30-day era minted at 01-30,
    then a fortnightly rebuild from 02-20 retiring that era, is a 35-day
    01-16 paycheck with two fortnightly paydays missing -- accepted against
    the stored plan (whose second payday was the 30-day era's 03-01), and the
    same batch from 02-06 was refused against it where the kept rhythm's own
    window ``[01-30, 02-13)`` admits it.  The floor keeps the stored eras,
    because its question is where the paycheck the owner HAS ends, which is
    the calendar as it derives before the batch.

    Args:
        surviving_paydays: The paydays the owner keeps once this operation's
            retirements are applied, empty for a first-time schedule.
        new_paydays: The paydays this batch would create, already filtered of
            any that exist.  Its earliest is what may skip: a batch whose
            first requested day the record already holds continues the plan
            from there, and every day it adds is on that plan.
        standing_eras: The eras the batch leaves STANDING -- those with a
            surviving payday, and the earliest whenever any payday survives
            (:func:`~app.services.pay_era_write.eras_describing`) -- whose
            plan is the calendar the batch leaves behind.  Empty only beside
            an empty payday set, by that function's contract.

    Raises:
        ValidationError: The earliest new payday falls at or after the
            plan's second projected payday past the latest surviving one.
    """
    if not surviving_paydays or not new_paydays:
        return
    latest_payday = max(surviving_paydays)
    planned = pay_calendar.planned_paydays_after(standing_eras, latest_payday)
    next_planned, ceiling = next(planned), next(planned)
    earliest_new = min(new_paydays)
    if earliest_new >= ceiling:
        raise ValidationError(
            f"A new payday, or the first payday of a new pay rhythm, must "
            f"fall before {ceiling.isoformat()} -- the second paycheck your "
            f"current rhythm projects after your latest recorded payday "
            f"({latest_payday.isoformat()}; the next one is "
            f"{next_planned.isoformat()}); got {earliest_new.isoformat()}.  "
            f"A later date leaves at least one whole paycheck missing, which "
            f"this app cannot represent: every day between would be filed "
            f"into the paycheck before it.  Choose a date before "
            f"{ceiling.isoformat()}, or extend your current rhythm to the "
            f"paycheck you mean and rebuild from there."
        )


def reject_payday_before_calendar(earliest: date) -> None:
    """Refuse an EARLIER batch that reaches below the application's calendar.

    **Asked of "Add earlier paychecks" alone** (plan step
    ``pay_calendar:C18-b``), because it is the one door that COMPUTES a day
    below the record rather than taking one from a form: every STATED payday
    is held to :data:`~app.utils.dates.CALENDAR_DATE_MIN` ..
    :data:`~app.utils.dates.CALENDAR_DATE_MAX` by the one payday window,
    ``app.schemas.validation._helpers._PAYDAY_RANGE`` (which
    ``pay_periods.payday_field`` carries onto the pay-period doors), and so
    is a stated history (``ck_pay_schedule_history_opens_range``), while
    ``budget.pay_periods.start_date`` carries no CHECK of its own.  A long
    cadence times a large count reaches past it -- 27 paychecks 365 days
    apart below a record opening 2026-01-02 reach 1999-01-09 -- so the door
    asks here, before anything is written.  Only the lower bound: the batch
    lies below a record that is itself inside the window.

    Args:
        earliest: The earliest payday the batch would record.

    Raises:
        ValidationError: *earliest* falls before
            :data:`~app.utils.dates.CALENDAR_DATE_MIN`.  The message names
            both days.
    """
    if earliest < CALENDAR_DATE_MIN:
        raise ValidationError(
            f"{earliest.isoformat()} is before "
            f"{CALENDAR_DATE_MIN.isoformat()}, the earliest day Shekel's "
            f"calendar holds. Add fewer."
        )


def reject_payday_before_history(
    earliest: date, history_opens_on: "date | None",
) -> None:
    """Refuse an EARLIER batch that reaches below the owner's stated history.

    **Ruling pay_calendar:R-PC104** (plan step ``pay_calendar:C18-b``): when
    a payday "Add earlier paychecks" would record falls before the day the
    owner saved as when their paychecks started, nothing is added.  The test
    is :func:`~app.services.pay_schedule_service.record_below_history`, the
    one predicate the history setter's refusal asks too -- so the two doors
    can never disagree about which records a stated history admits.  The
    message is this door's, as the ruling worded it; an unstated history
    (``None``) admits every batch.

    Args:
        earliest: The earliest payday the batch would record.
        history_opens_on: The owner's stated ``history_opens_on``, or
            ``None``.

    Raises:
        ValidationError: *earliest* falls before *history_opens_on*.  The
            message names the earliest offending payday and the saved day.
    """
    if pay_schedule_service.record_below_history(earliest, history_opens_on):
        raise ValidationError(
            f"{earliest.isoformat()} is before "
            f"{history_opens_on.isoformat()}, the day you saved as when your "
            f"paychecks started. Change that date first, or add fewer."
        )
