"""The forward continuation: the projected period covering any day past the record.

**Plan step ``pay_calendar:C17-b-1``, a pure move** (ruling **R-PC69**,
developer 2026-09-11).  :func:`project_period_after` and its refusal
:func:`covering_projection` lived in :mod:`._derive` beside the rule they
continue -- a period runs from its payday to the day before the next -- and
that module stood at 998 of pylint's 1,000 lines (ledger row **PC-498**) with
the money-moving leaf ``C17-b-2`` about to edit both.  The remedy is a SPLIT
and not a trim, on the terms that row records: the derivation of the RECORD
stays in ``_derive``, and the projection PAST it lives here.  Nothing in
either function changed in the move; the docstrings carry their history.
**Plan step ``C17-b-2`` then made the projection PIECEWISE** (rulings
**R-PC66**, **R-PC72**): it anchors on the phase of the era covering the day
it is asked about rather than on the last recorded payday, and an era's
projected paydays stop where the next era's begin -- at the seam
:func:`~._eras.last_step_of` draws, which since ruling **R-PC75** (plan step
``C17-c-2b``) is the one the door that minted the later era drew.

Placed between :mod:`._derive` and :mod:`._searches` in the package's one-way
chain: it imports the derivation's values and producer and nothing above it,
and its two callers -- :func:`~._views.projected_paychecks` and
:meth:`~._calendar.PayCalendar.span_containing` -- sit above it.  Pure, on
the package's standing terms: no session, no Flask, no clock.
"""

from collections.abc import Iterable
from datetime import date, timedelta

from app.services.pay_rhythm import Era

from ._derive import DerivedPeriod
from ._eras import (
    PayCalendarError,
    era_index_at,
    following_planned,
    horizon_step,
    last_step_of,
    projected_payday,
)
from ._grid import cadence_steps_to


def covering_projection(
    candidates: "Iterable[DerivedPeriod]", day: date,
) -> DerivedPeriod:
    """Return the candidate projection covering *day*, refusing when none does.

    **A function of its own so :func:`project_period_after` states the RULE and
    this states the REFUSAL**, which is fifteen lines of it.  *An earlier form
    claimed the split was what let the probe be graded before the shift
    shipped; an adversarial review of ``C14-c`` refuted that from the suite --
    what grades the probe is a case that STATES a displacing convention and
    drives the REAL* :func:`project_period_after`, *reaching this through its
    own caller.  It substituted the producer until ``C14-e-3`` shipped one.*

    Consumed LAZILY, so the arithmetic estimate costs the one candidate it
    always did -- a property of the ORDER its caller offers them in, not of
    this function, which is order-free for correctness.  The estimate is the
    answer on EVERY call today and stays the answer on all but a fraction of a
    percent once the convention is live: over the 26,427 days the projection
    must answer for past the production owner's real horizon -- last recorded
    payday 2028-08-10, cadence 14, out to
    :data:`~app.utils.dates.CALENDAR_DATE_MAX` -- it names the wrong period on
    **64 of them, 0.24%**, under ``prior`` and again under ``next`` (measured
    2026-09-05).  The horizon and cadence are the owner's real ones; the
    CONVENTION is not -- every row is seeded ``none`` and nobody has answered
    (**R-PC56**) -- so the two conventions bracket the figure.

    **The containment test is the PERIOD's own** (:meth:`DerivedPeriod.covers`,
    **R-PC31**) rather than a comparison written here, the discipline
    :func:`~._searches.containing_index` keeps: one rule spelled two ways is how
    ledger row **P6** came to have six copies that disagreed.  While the
    collision floor holds the candidates TILE, so at most one matches and their
    order cannot change the answer; below it two can overlap, the state the
    refusal exists for.

    **The refusal is a REPORTED hole, and ruling R-PC59 is where it is
    reported.**  Nothing covers *day* only when two nominal paydays were
    displaced onto one day or past each other, which needs a cadence no longer
    than the longest run of consecutive closed days.
    ``pay_schedule_service.reject_shift_on_short_cadence`` refuses that pair at
    the column's one write door -- but a write-time refusal cannot see a row a
    LATER holiday-set change made illegal, and nothing reconciles that table
    (ledger row **N-493**).  Raising names the schedule; the alternative is
    answering with a period that does not contain the day it was asked about.

    Args:
        candidates: The projections to choose between: the estimate and the
            neighbours that fall inside the era's own window, one to three
            above the collision floor.  Below it -- **N-493**'s reported
            hole, a pairing no door admits -- the window can empty, and the
            refusal below says so rather than listing nothing.
        day: The calendar day to place.

    Returns:
        The candidate whose span covers *day*.

    Raises:
        PayCalendarError: No candidate covers *day*.
    """
    tried = []
    for candidate in candidates:
        if candidate.covers(day):
            return candidate
        tried.append(candidate)
    spans = ", ".join(
        f"[{c.start_date.isoformat()}..{c.end_date.isoformat()}]"
        for c in tried
    ) or "none inside the era's window"
    raise PayCalendarError(
        f"no projected pay period covers {day.isoformat()}: the candidates "
        f"were {spans}.  A projection is the nominal rhythm displaced onto a "
        f"business day, and the candidates either side of the arithmetic "
        f"estimate cover every day the rhythm can reach while no payday moves "
        f"by a whole cadence.  Reaching here means two nominal paydays "
        f"displaced onto one day or past each other, which needs a cadence no "
        f"longer than the longest run of consecutive closed days -- "
        f"pay_schedule_service.reject_shift_on_short_cadence refuses that pair "
        f"at the write door, and ruling R-PC59 records that a write-time "
        f"refusal cannot see a stored row a later holiday-set change made "
        f"illegal."
    )


def project_period_after(
    periods: "tuple[DerivedPeriod, ...]", eras: "tuple[Era, ...]", day: date,
) -> DerivedPeriod:
    """Return the projected period covering *day*, past the last saved payday.

    **The forward continuation of :func:`~._derive.derive_periods`' rule** --
    beside which it lived until ``C17-b-1`` moved it here -- and it IS that
    rule: a projected period runs from its own payday to the day before the
    next, exactly as a saved one does, and both paydays come from
    :func:`~._eras.projected_payday`.  Two consumers ask --
    :meth:`~._calendar.PayCalendar.span_containing`, which must answer for any
    day, and :func:`~._views.axis_window`, which walks the projection to a
    horizon -- and a second implementation of "where does the next paycheck
    land" is the class ledger row **P6** counted seven of.

    **It anchors on the ERA covering *day*, not on the last recorded payday**
    (plan step ``C17-b-2``, rulings **R-PC66** and **R-PC72**; ledger row
    **N-495** closed).  The era's ``effective_from`` is a day its grid passes
    through by definition; the last recorded payday is what the BANK did,
    which **R-PC47** says may fall off the cadence, and anchoring on it
    projected an owner whose last payday payroll moved a rhythm off by that
    displacement -- worked: a last recorded 2030-11-27, the nominal 11-28
    Thanksgiving under ``prior``, projected a next payday of 12-11 where the
    grid says 12-12, one paycheck and every boundary after it a day early.
    And the projection is PIECEWISE: an era's paydays run from its first
    (:func:`~._eras.first_payday_of`) to its last planned one under the
    seam rule (:func:`~._eras.last_step_of`: the next era's first payday
    replaces the one at or before it, ruling **R-PC75**), so a day past the
    record but before a later era's first payday is projected on the era
    that covers it, and that era's last projected period closes on the
    seam.  The owner's eras are read in CASH days there
    (:func:`~._eras.era_index_at`, :func:`~._eras.last_step_of`), because
    where two conventions meet a nominal seam can pay one day twice.

    Projection is ARITHMETIC rather than a walk: the period covering *day* is
    about the ``n``-th of its era, ``n`` being the whole cadences from the
    era's phase (:func:`~._grid.cadence_steps_to`, since ``balance:X-bh-2``),
    so cost does not grow with how far ahead a caller asks.  That property
    was priced when :func:`~._views.projected_paychecks` stepped to its answer
    instead of jumping -- **32 ms against 0.1 ms** for one render,
    ``balance:X-bh-1``, measured 2026-08-30 -- and a walk here would
    reintroduce it one layer down.  The ORDINAL is arithmetic too: a
    period's ``period_index`` continues the saved sequence by the number of
    projected paydays between the horizon and it, counted across the eras
    between (:func:`_ordinal`).

    **"About" is plan step C14-c's word, and the PROBE is why the jump survives
    a payday that moves** (**R-PC57**: the containment probe tolerates a moved
    boundary).  The division is exact only while every payday sits on the
    arithmetic grid; since ``C14-e-3`` displaces one, the count can name the
    period next door, so the estimate is checked against its NEIGHBOURS and
    whichever candidate covers *day* wins (:func:`covering_projection`).

    **Why ONE neighbour either side is enough, and it is a theorem rather than
    a margin.**  A displacement is bounded by the longest run of consecutive
    closed days, and
    :func:`~app.utils.business_days.shortest_collision_free_cadence` is that
    run PLUS ONE -- the floor
    ``pay_schedule_service.reject_shift_on_short_cadence`` holds a displacing
    convention to (**R-PC59**).  So no payday moves a whole cadence, which puts
    the true index within one of the estimate; a candidate two out would need a
    displacement of a full cadence or more.  **Its second premise -- that the
    count and the candidates are measured from the SAME anchor -- is what
    ``C17-b-2`` restored** (ledger row **N-495**): both read the era's phase
    now, where anchoring only the candidates on it while the estimate counted
    from the record would have added the displacement to the window.  Swept
    in ``tests/test_services/test_pay_calendar_derivation/test_the_boundary_arithmetic.py``
    over both conventions, four anchors, every cadence from the floor to a year, and
    steps either side of the anchor -- driving THIS function, not a second
    copy of its arithmetic -- and, for the seam, in
    ``tests/test_services/test_pay_calendar_eras.py`` against a brute-force
    walk over randomised era sequences, asked at every period's last day
    too, and an enumeration of every shape whose estimate overshoots the
    seam (the clamp below).

    **A neighbour outside the era's window is not offered, and the estimate
    is CLAMPED to the era's last step first.**  Below its first step a
    non-earliest era has no payday -- the previous era's last period reaches
    to the seam -- and above :func:`~._eras.last_step_of` the next era pays;
    the candidates that remain are one to three, and one of them covers
    *day* because the era's first payday is at or below it and its last
    period closes the day before the next era's first.  The clamp is ruling
    **R-PC75**'s (plan step ``C17-c-2b``): the era's last paycheck runs from
    its last planned payday ``L`` to the day before the next era's first
    ``F``, and ``F < payday(L + 2) < nominal(L + 3)``, so the arithmetic
    estimate for a day late in it can name a step TWO past the era's last
    and never three -- an old era under
    ``next`` whose nominal payday falls on a Saturday, and a new era under
    ``none`` opening the Sunday after it, leaves that Saturday with no
    candidate inside the window at all.  Clamping the estimate puts the last
    step itself in the window, and it covers every such day: the day is
    below the seam and above that step's own payday.

    **The precondition below is NOT structural, and a first cut of this step
    filtered on the belief that it was.**  Candidates at step ``0`` and below
    were dropped, reasoning that *day* falls past the last period's end so
    nothing earlier could win -- and the suite refused it:
    :meth:`~._calendar.PayCalendar.span_containing` reaches here for a day
    INSIDE an unsaved interior candidate, whose materialisation filter leaves
    the total answer here.  *day* is then below the record, the ordinal below
    is negative, and the rhythm is read backwards -- which is ledger row
    **N-496**, carried: the answer is the era's grid where the record is
    authoritative, and a ``period_index`` that does not match the saved
    sequence.

    Every projected period reports ``end_is_projected`` ``True`` -- the end
    comes from the projection rather than a recorded payday -- and carries
    ``period_id = None``, so a caller needing a foreign key target cannot
    mistake one for a saved row.

    Args:
        periods: The owner's SAVED periods, ``start_date`` ascending and
            non-empty.  Only the last one is read, for the ordinal.
        eras: The owner's eras, ``effective_from`` ascending and validated
            (:class:`~app.services.pay_rhythm.Era`; :func:`~._derive.validate_eras`).
        day: The calendar day to place.  NORMALLY past the last saved period's
            ``end_date``, which is what both callers test for -- but that is
            not a guarantee, and this entry said it was until an adversarial
            review of ``C14-c`` read it against the paragraph above.  Believing
            the old wording is how the ``steps >= 1`` filter this step already
            had to remove gets re-added.

    Returns:
        The projected :class:`DerivedPeriod`, carrying ``period_id = None`` and
        a ``period_index`` continuing the saved sequence.

    Raises:
        PayCalendarError: No candidate covers *day* -- the state
            :func:`covering_projection` states in full.  No schedule the write
            door admits can reach it TODAY; ledger row **N-493** is the
            reported hole.
    """
    last = periods[-1]
    index = era_index_at(eras, day)
    era = eras[index]
    top = last_step_of(eras, index)
    estimate = cadence_steps_to(era.effective_from, era.rhythm.cadence, day)
    if top is not None:
        estimate = min(estimate, top)
    horizon = horizon_step(eras, last.start_date)
    return covering_projection(
        (
            _candidate(
                eras, index, steps,
                last.period_index + 1
                + _ordinal(eras, index, steps) - _ordinal(eras, *horizon),
            )
            # The estimate FIRST: it is the answer whenever no payday between
            # the horizon and *day* was displaced across a boundary, and the
            # generator is consumed lazily, so the common call builds one
            # candidate as it always did.
            for steps in (estimate, estimate - 1, estimate + 1)
            if (index == 0 or steps >= 0) and (top is None or steps <= top)
        ),
        day,
    )


def _candidate(
    eras: "tuple[Era, ...]", index: int, steps: int, period_index: int,
) -> DerivedPeriod:
    """Return era *index*'s projected period at grid step *steps*.

    The end is the day before the planned payday that FOLLOWS this one
    (:func:`~._eras.following_planned`): the era's next step, or at the
    era's last step the following era's first payday, which is the seam the
    piecewise projection closes on.

    Args:
        eras: The owner's eras, validated.
        index: The era the period belongs to.
        steps: The grid step from that era's phase.
        period_index: The ordinal the period carries, continuing the saved
            sequence.

    Returns:
        The projected :class:`~._derive.DerivedPeriod`.
    """
    era = eras[index]
    next_index, next_steps = following_planned(eras, index, steps)
    following = eras[next_index]
    return DerivedPeriod(
        period_id=None,
        period_index=period_index,
        start_date=projected_payday(era.effective_from, era.rhythm, steps),
        end_date=projected_payday(
            following.effective_from, following.rhythm, next_steps,
        ) - timedelta(days=1),
        end_is_projected=True,
    )


def _ordinal(eras: "tuple[Era, ...]", index: int, steps: int) -> int:
    """Return the position of era *index*'s step *steps* in the piecewise grid.

    Counted from the EARLIEST era's phase: every era before *index*
    contributes the paydays it pays (steps ``0`` through
    :func:`~._eras.last_step_of`), and the step itself is added.  Two
    positions differ by the number of paydays between them whichever eras
    they fall in, which is what lets a projected ``period_index`` continue
    the saved sequence across a seam by one subtraction.  The earliest era's
    steps may be negative -- it runs backward below the record -- and the
    arithmetic reads the same there.

    Args:
        eras: The owner's eras, validated.
        index: The era.
        steps: The grid step within it.

    Returns:
        The position, an integer without further meaning.
    """
    return sum(last_step_of(eras, j) + 1 for j in range(index)) + steps
