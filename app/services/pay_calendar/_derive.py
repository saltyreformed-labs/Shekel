"""The derivation: an owner's paydays in, their pay periods out (plan step C1).

``budget.pay_periods`` stores three values per row and only ``start_date`` is a
fact -- the day money arrived.  ``end_date`` is ``lead(start_date) - 1`` and
``period_index`` is ``row_number() - 1``, both stored beside the fact they
derive from with nothing reconciling them, which is why five separate runtime
fences exist to police one functional dependency
(``docs/plans/implementation_plan_pay_calendar.md`` section 1).  This module is
that dependency, written once::

    period_index = row_number() over (order by start_date) - 1
    end_date     = next_payday - 1

where ``next_payday`` is the following row's ``start_date``, or -- for the
last period, which has none -- the era grid's next planned payday
(:func:`~._eras.payday_after`).  **Plan step C14-c made those ONE rule.**  The last
end read ``start_date + cadence_days - 1``, which agrees with the first only
while every payday sits exactly one cadence from its neighbour; since
``C14-e-3`` displaces one onto a business day the two part, and the day between falls in
NO period or in two (**R-PC54**).

**Its one caller is** :meth:`~._calendar.PayCalendar.__post_init__`, since plan
step C2-a; at C1 it had none at all, deliberately, because the value had to be
proven equal to what was stored -- over a clone of production and over irregular
schedules the live data cannot supply -- before anything read it, wrote it, or
dropped the columns.  **Plan step C4-c dropped them, so that comparison has no
second side and the harness that took it is gone**; the proof it was is in
migration ``b7a41e2c9d63``'s docstring, measured on production itself.  What
holds this function to its values now is
``tests/oracles/pay_calendar_derivation.py``'s hand-computed sweep, driven by
the ``test_the_irregular_shapes_and_re_derivation`` part of the
``tests/test_services/test_pay_calendar_derivation/`` package.

**Pure, and that is load-bearing.**  No session, no Flask, no clock: the
derivation is a function of two values, so the sweep drives it over a
catalogue of schedule shapes without a database at all.  That is what let the
same code be driven over a production clone while a stored column still
existed to diff it against.
**Every application import this module takes is a PURE LEAF** -- a module
reaching no session, no request and no ORM -- and that is the rule rather than
the names it happens to admit today (``app.services.pay_rhythm`` and
``app.utils.dates``, beside the package's own :mod:`._eras`).  So the module
still loads with no app stack behind it.
*It was written as a LIST -- "the only application imports are
``app.exceptions`` and ``app.utils.dates``" -- and a list decays: an
adversarial review corrected it on 2026-08-14 when it counted one import and
there were two, ``C14-e-1`` made it stale again by adding
``app.services.pay_rhythm``, ``C14-e-3`` added ``app.utils.business_days``
for the displacement, and plan step ``C17-b-2`` moved the displacement, the
refusal and the cadence bound down to :mod:`._eras` (ruling **R-PC73**), which
took ``app.exceptions`` and ``app.utils.business_days`` with them.  Stated as
a property, the next addition is GRADED rather than counted.*

**That import WEAKENED the no-clock property, and the honest form says which
property survives.**  Before plan step C2-f this module could not reach a clock
through its import graph at all; ``app.utils.dates`` carries
:func:`~app.utils.dates.display_today`, so the guarantee is now "nothing here
CALLS a clock" rather than "no clock is reachable".  It was taken so the period
LABEL is one rule rather than one per type that answers "which paycheck" (see
:attr:`DerivedPeriod.label`), and ``app.utils.dates`` is the only leaf this
package may take on those terms.  *There is one such type now* -- plan step
``pay_calendar:C4-a-5`` deleted the ORM row's accessor -- *and the import
stays for the reason* :func:`~app.utils.dates.pay_period_label`'s *own
docstring gives: the narrow register and the wide one are one decision (ledger
row P47) and live together, and the wide one has a caller outside this
package.*

**Why the last end is a different KIND of value, and says so.**  Every other
end is dictated by a fact -- the next RECORDED payday.  The last one has no
successor row, so the payday it stops before is PROJECTED: the owner's era
grid's next payday after the one the last record stands for
(:func:`~._eras.payday_after`; ruling 2026-08-08: "a projection stated as one").  The
RULE over the two is identical since plan step ``C14-c``; what differs is
whether the payday it reads is a record or a projection.
:attr:`DerivedPeriod.end_is_projected`
is that statement, and it cannot be recomputed by a consumer holding one period
out of its calendar.  It is not cosmetic: a projected end moves when the era it
is read from moves -- a batch that mints one, or retires one -- where an end
dictated by a recorded payday does not, and the flag is the only thing on the
value that says which kind a consumer holds.  *Until plan step ``C4-c`` a
stored ``end_date`` hid that, and plan finding P12 -- a
``/pay-periods/generate`` post naming an existing payday created zero rows and
still rewrote the stored cadence -- was the door that moved it.*
"""

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date, datetime, timedelta

from app.services.pay_rhythm import Era
from app.utils.dates import pay_period_label, pay_period_range_label

from ._eras import (
    PayCalendarError,
    first_payday_of,
    last_step_of,
    payday_after,
    projected_payday,
    validate_cadence,
)
from ._grid import cadence_steps_to, nominal_payday


@dataclass(frozen=True)
class DerivedPeriod:
    """One pay period, derived rather than stored.

    The shape of the two columns plan step C4-c dropped, plus the one thing the
    columns could not say.  Ordered ``start_date`` ascending inside a
    :func:`derive_periods` result, with ``period_index`` matching that order by
    construction -- the disagreement between index order and date order that
    ``uq_pay_periods_user_index`` and three runtime fences existed to catch is
    not expressible here.

    Attributes:
        period_id: The ``budget.pay_periods.id`` the payday was read from, or
            ``None`` when the period is not MATERIALISED -- a projection past
            the owner's horizon, or a candidate the writer has not saved yet.
            The one field here that is carried rather than derived, and it is
            carried because identity is what separates the two questions plan
            step C2 has to keep apart: "which paycheck does this row live in"
            needs a row a foreign key can point at, while "which span does this
            day fall in" is answerable by a projection.  A value that could not
            say which it was would answer the write question wrongly and
            silently.
        period_index: The owner's 0-based ordinal for this period, which is its
            position in payday order.
        start_date: The payday that opens the period.  The only fact in the
            row; everything else here is derived from it and its neighbours.
        end_date: The last day the period covers -- the day before the NEXT
            payday, which for the last period is the projected one
            (:func:`~._eras.projected_payday`) rather than a recorded neighbour.  ONE
            rule since ``C14-c``; it read ``start_date + cadence_days - 1``
            there, the same day only while no payday can move.
        end_is_projected: Whether the payday :attr:`end_date` was taken from is
            a PROJECTION rather than a recorded one.  ``True`` for the LAST
            period of a
            non-empty calendar and ``False`` for every other -- so exactly one
            per calendar, and none at all for the empty one.  A consumer
            holding a single period cannot work this out, which is why it rides
            on the value: a projected end moves when the stored cadence moves,
            and a fact-derived end does not.
    """

    period_id: "int | None"
    period_index: int
    start_date: date
    end_date: date
    end_is_projected: bool

    @property
    def is_projected(self) -> bool:
        """Whether this period lies PAST the owner's saved schedule.

        **The period answers the question itself, and nothing passes the
        boundary down** (ruling **R-SAL18**, plan step **salary:S3-e-2**).
        The fact is already carried: :attr:`period_id` is ``None`` exactly
        for a period :func:`~._views.axis_window` built from
        :func:`~._views.projected_paychecks`, which opens at ``horizon + 1``
        and stamps no row id because there is no row.  The consumer that
        must know -- :func:`~app.services.investment_projection
        .build_contribution_timeline`, which adds an account's average
        recurring transfer only where the schedule has not reached -- used
        to be HANDED that boundary as a ``saved_through`` date read off a
        calendar that had to be the one the axis came from, a pairing an AST
        census fenced.  Asking the period deletes the parameter, the wiring
        and the census.  **A NAMED accessor and not a raw ``period_id``
        read**, because pay-calendar plan step C2-f2c deliberately stopped
        that consumer knowing how a period spells its primary key.

        Returns:
            ``True`` for a projection past the saved schedule, ``False`` for
            a saved (materialised) period.
        """
        return self.period_id is None

    def covers(self, day: date) -> bool:
        """Return whether *day* falls inside this period's span.

        **The single CONTAINMENT rule for one period**, ruled at **R-PC31**
        and landing at plan step C4-a-3, which retired the sites that
        open-coded ``start_date <= day <= end_date``.  Both bounds are
        INCLUSIVE -- a period covers its payday and it covers the day before
        the next one -- and writing that twice is how a chained comparison
        comes to be spelled once with ``<`` on one end.

        **WHO asks it is a PREDICATE and not a list**, which is this package's
        own hard-won convention rather than a style choice: ``grep -rn
        "\\.covers(" app/`` with the definition struck out.  Three at C4-a-3 --
        the purchase-date warning
        (``entry_service._sums.entry_list_view``), the recurrence engine's
        base-month scan (``recurrence.compute_due_date``, deleted at plan
        step recurrence:R5-a) and
        this package's own :func:`~._searches.containing_index` -- and a
        FOURTH is already ranked: ``balance:X-x1`` names this method in its
        own sentence, so a closed list here would go stale the day that step
        ships.  :meth:`~._calendar.PayCalendar.period_by_id`'s docstring is an
        essay on that exact failure, having claimed zero, five, seven and
        eleven callers in turn; writing the grep instead of the names is what
        that essay concludes.  *An adversarial review of this step found the
        closed list, 2026-08-31.*

        **A different ``covers`` exists and is NOT this one**:
        ``cash_ledger._amounts.ReconciledThrough.covers`` asks whether a
        statement's coverage reaches an event day -- one-sided, and TOTAL over
        ``None`` by design.  This one is two-sided and raises on ``None``,
        because a period always has both bounds.

        **It is the predicate the SEARCHES already run**, said once rather
        than a second implementation beside them.
        :func:`~._searches.containing_index` bisects to the last period
        opening on or before *day* and then asks this; that lower bound is
        already established there, so the first comparison below is redundant
        AT THAT ONE CALL SITE and is paid anyway, because a containment rule
        with a branch missing from one caller is the shape ledger row **P6**
        counted six of.

        **What it does NOT ask is whether the end is a FACT or a
        PROJECTION.**  The last period of a calendar carries
        :attr:`end_is_projected`, and its span moves when the owner's stored
        cadence moves; a caller that must distinguish "covered by a paycheck
        that has been banked" from "covered by one the cadence implies" reads
        that flag, exactly as it would to interpret :attr:`end_date` itself.
        No caller today does: a purchase is in or out of its own paycheck's
        span whichever way that span was derived.

        Args:
            day: The calendar day to place.

        Returns:
            ``True`` when ``start_date <= day <= end_date``.
        """
        return self.start_date <= day <= self.end_date

    def attribution_day(self, preferred: "date | None") -> date:
        """Return the day an item filed in THIS period is budgeted to.

        The single BUDGET-attribution rule, shared by the calendar's day-cell
        grouping (``calendar_service._get_display_day``), the balance-at seam's
        PLANNED tier (``balance_at._cash_fold._cash_plan``) and the reconcile
        panel's offer bound (``reconcile_service._rows.attributed_on``), so no
        two of them can come to disagree about which day an item is BUDGETED
        to.  An item lands on *preferred* -- its ``due_date`` -- falling back
        to this period's :attr:`start_date` when it has none; the result is
        then clamped into ``[start_date, end_date]``.

        **It is a METHOD ON THE PERIOD, and that is plan step C4-a-2's whole
        subject.**  It was ``utils.dates.attribution_date(preferred,
        period_start, period_end)``, three positional arguments a caller had to
        pair correctly, and pairing them wrongly is not a crash -- it is a row
        rendered on the wrong day.  Two of its three callers already held a
        :class:`DerivedPeriod` and split it back into two dates to make the
        call; the third read ``txn.pay_period`` and clamped a projected row
        against the STORED ``end_date`` while everything around it sampled the
        DERIVED one.  Asking the period itself makes the pairing structural:
        there is no second span to supply.

        **Clamping is load-bearing for the daily balance**: every one of a
        period's contributing items must fall on or before :attr:`end_date`, so
        the running balance summed through that day equals the period-end
        balance the grid shows (the calendar/grid reconciliation invariant).  A
        ``due_date`` outside the item's own period is possible -- the
        recurrence engine can date an item just outside its period's range,
        which is why the calendar query carries a due-date-in-range OR
        no-due-date path -- so such a stray date is pulled to the nearest
        boundary rather than escaping onto a neighbouring period's day and
        breaking that period's sum.

        **What this does NOT guarantee, and both halves of the paragraph it
        replaces were false** (ledger row **N-97**).  The deleted function's
        docstring named the seam's caller as
        ``balance_resolver.daily_cash_balance_series``, a producer plan step
        X-c2b3 had DELETED a month earlier, so the citation resolved to
        nothing.  And it guaranteed that a flow's calendar cell and the balance
        line's step for it land on the SAME day: that stopped holding at plan
        step X-c2b2, when the balance line became the cash fold, which steps a
        SETTLED row on the day its money moved and a projected one on
        ``max(attribution, as_of + 1)`` (rulings **R-DH (b)** and **R-G**).
        Neither is this day, so a chip and its own step can sit days apart:
        ``|settled_on - due_date|`` over the real Checking account's settled
        rows is **median 2, p75 7, max 25 across 126 rows, re-measured
        2026-08-28**.  It moved here CARRYING NO DATE, quoted as "median 2, p75
        6, max 25" over 130 rows, and a measurement quoted as a REASON decays
        invisibly because nobody re-checks a premise -- so the leaf that
        relocated it re-took it rather than copying it forward.  That
        divergence is finding **N-58**, it is an open fork rather than a
        settled rule, and ``calendar_service._get_display_day`` states it at
        the site.  What survives is the budget attribution itself, which is
        what every reader asks this for.

        Args:
            preferred: The item's preferred landing date (its ``due_date``), or
                ``None`` to fall back to :attr:`start_date`.

        Returns:
            The attributed calendar day, guaranteed within
            ``[start_date, end_date]``.
        """
        if preferred is None or preferred < self.start_date:
            return self.start_date
        if preferred > self.end_date:
            return self.end_date
        return preferred

    @property
    def label(self) -> str:
        """Return this period's human label (``"02/21 - 03/06"``).

        **The ONLY accessor that answers "which paycheck" in words**, since
        plan step ``pay_calendar:C4-a-5``.  It was one of two: the ORM row
        carried ``PayPeriod.label``, reaching the same
        :func:`app.utils.dates.pay_period_label` -- so the FORMAT agreed while
        the DATES did not, because the row fed it the STORED ``end_date`` and
        this value the derived one.  Under the P12 / P28 shape that stored end
        goes stale, and the two rendered one paycheck two ways: C4-a-2 saw it
        across screens, and C4-a-5 measured it inside ONE card, where the
        grid's full-edit popover named the row's period off the row while the
        period ``<select>`` two sections below it named the same period off
        this property.  That step moved both readers here and deleted the
        accessor, so the stored column is no longer reachable for a label at
        all.  The COLUMN itself goes at ``C4-c``.

        Returns:
            The label, carrying the year on both halves only when this period
            straddles one.
        """
        return pay_period_label(self.start_date, self.end_date)

    @property
    def range_label(self) -> str:
        """Return this period's WIDE label (``"Feb 21 - Mar 06, 2026"``).

        :attr:`label`'s sibling register, for a surface with room for month
        names -- today the Income Statement's window ``<select>``, whose
        ``<option>`` sits on screen beside the report heading the same rule
        produces.  That heading reaches
        :func:`~app.utils.dates.pay_period_range_label` through
        ``spending_analysis.window_label``
        (``ledger_report_service.compute_income_statement``), which is one call
        away rather than the direct one this sentence claimed until plan step
        ``pay_calendar:C4-a-5`` re-checked it; either way the ``<option>`` and
        the heading beside it come from one rule, which is the property that
        matters.

        It is a PROPERTY rather than a Jinja global for :attr:`label`'s reason:
        the format belongs to ``app.utils.dates``, and a template that called a
        two-argument formatter would be the place a fourth spelling of this
        register next appeared (ledger row **P47**).  Plan step C2-f3a.

        Returns:
            The label.  The four-digit year rides on the END date alone, and
            on BOTH halves where the period straddles one -- ledger row
            **P67**, ruled 2026-08-25.  *This line said "the END date's" flat
            until ``C4-a-5``, which rewrote the paragraph above it and left the
            residue; :func:`~app.utils.dates.pay_period_range_label` has stated
            the ruled rule since P67 landed.*
        """
        return pay_period_range_label(self.start_date, self.end_date)


def derive_periods(
    paydays: "Iterable[tuple[int | None, date]]", eras: "tuple[Era, ...]",
) -> tuple[DerivedPeriod, ...]:
    """Derive an owner's whole pay calendar from their paydays and eras.

    **Takes the owner's COMPLETE payday set, never a window.**  A period's end
    is its successor's payday, so the LAST payday in whatever list arrives here
    falls to the cadence projection -- which means a partial list makes one
    period report a different end depending on which window asked (plan finding
    P14).  **The sibling shape was measured at ``$150,000.00``**: folding a
    ``$100,000.00`` loan true-up against the owner's whole calendar, versus
    against a window that excluded the true-up's own period, moved the balance
    by that much on the days between -- because with the containing period
    absent the old locator missed and its fallback fired.  That measurement
    lived on ``loan_ledger/_visible.owner_pay_periods``' docstring until plan
    step C2-d deleted the function, and it is restated here rather than lost,
    because it is the evidence for this paragraph.  This function cannot detect
    partiality -- a slice of paydays is indistinguishable from a short schedule
    -- so the guarantee has to be structural at the caller.  Plan step C2 makes
    it so: the calendar is built once from the complete set and a window becomes
    a VIEW over it that keeps the real ends.

    **A derived end is not stable against a later write, and that is the one
    way it differs from the column it replaces** (found by adversarial review
    of this step, 2026-08-08).  A stored ``end_date`` cannot move when a
    neighbouring row is written; a derived one can.  Concretely, with paydays
    ``[01-02, 01-16]`` at cadence 14 the second period ends 01-29 (projected);
    append the payday 01-28 -- LATER than every existing one, so a forward-only
    write by the plan's own definition -- and that end moves back to 01-27.
    Only ends inside the last period's PROJECTED span can move this way; an end
    dictated by a following payday is fixed for as long as that payday is.
    A row already dated into the vacated days is not left outside its period --
    :meth:`DerivedPeriod.attribution_day` clamps it -- so the damage is a row
    silently RENDERED on a different day, which is plan finding P10's shape
    reached through a door P10 does not cover.  ``_reject_overlapping_batch``
    blocked that write by comparing against the stored end; plan step **C3-b**
    replaced it with ``pay_period_batch.reject_backward_payday``, and **that
    floor is what closes this example** -- corrected 2026-08-11, because this
    paragraph named C3-b's coverage rule until the floor's own correction made
    the citation wrong and the rule was then deleted.  The floor is the era
    grid's next payday after the one the latest record stands for
    (:func:`~._eras.payday_after`, the same call that closes the last period here), so
    on ``[01-02, 01-16]`` under an era from 01-02 at cadence 14 the earliest
    acceptable new payday is 01-30 and the 01-28 above is refused outright.

    **What the floor buys is exactly that and no more, and a second adversarial
    review caught a first draft of this paragraph claiming more**: no write can
    place a payday INSIDE an existing paycheck's derived span.  A stored end can
    still be pulled BACK, two ways, and both are exercised by C3-b's own tests
    -- ``retire_paydays`` drops the newly-last survivor from its
    successor-dictated end to the cadence projection, and ``record_paydays``
    shortens the previously-last period whenever the stored cadence is shorter
    than the one the schedule was generated at (row **P28**'s legacy shape).
    The derivation states the property and does not police it: it is a function
    of a payday set, and which sets a user may write is the writer's question.

    Order and duplication are handled HERE rather than trusted, because the
    result's whole value is that index order and date order cannot disagree:
    the input is sorted (so a caller's query order cannot change the answer) and
    a repeated payday is refused (it would place two periods on one opening day
    and give the earlier of them an ``end_date`` before its own
    ``start_date``).

    Args:
        paydays: The owner's complete set of paydays as
            ``(period_id, payday)`` pairs, in any order.  ``period_id`` is the
            ``budget.pay_periods.id`` the payday was read from, or ``None`` for
            a period that is not materialised; it takes no part in the
            derivation and rides through onto
            :attr:`DerivedPeriod.period_id`.  Empty is a legal input and yields
            an empty calendar -- a user who has never generated a schedule, and
            the companion role, which by design holds no paydays of its own.
        eras: The owner's pay eras, ``effective_from`` ascending and
            NON-EMPTY (:class:`~app.services.pay_rhythm.Era`, one per
            ``budget.pay_eras`` row): each carries the day its rhythm took
            effect, which is the grid's PHASE, and the rhythm itself.  Read
            only for the LAST period's end -- every other end is dictated by
            the next recorded payday -- and through :func:`~._eras.payday_after`, so
            the end lands on the grid of the era covering the last payday
            rather than one cadence past the day the bank happened to pay
            (plan step ``pay_calendar:C17-b-2``; ledger rows **N-495**,
            **N-492**).  Validated EAGERLY, before the paydays are looked at
            (:func:`validate_eras`): a bad sequence is a bad caller whether or
            not this owner has paydays yet, and refusing it only when the data
            reaches the projection branch would hide it until the day a user
            records their first payday.
            **It was a single :class:`~app.services.pay_rhythm.Rhythm` until
            ``C17-b-2``** -- the schedule row's one cadence and convention,
            then the LATEST era's at ``C17-a`` -- and a bare ``cadence_days``
            until ``C14-e-1``, which threaded the pair ahead of ``C14-e-3``
            making :func:`~._eras.projected_payday` the nominal day displaced under
            the owner's convention.  The sequence travels as one value for the
            reason the pair did: passed apart, one owner's cadence could be
            paired with another's convention or phase.
            **It is not optional, since plan step C4-d** (ruling **R-PC45**).
            The cadence was ``int | None``, ``None`` being legal beside an
            empty payday set and REFUSED beside a non-empty one -- a pairing
            this function policed at runtime for every caller, in twenty
            lines, because the type would not.  The absence it stood for was
            an owner with no ``budget.pay_schedule`` row, and that owner now
            has no CALENDAR: :func:`~._loader.calendar_for` refuses them
            rather than building an empty one with no rhythm, so nothing
            constructs the pair and there is nothing here to refuse.  An owner
            with an era and zero paydays is unaffected and still ordinary --
            they have a real rhythm and an empty calendar, which is what
            ``pay_period_admin.reset_pay_periods`` passes through.

    Returns:
        The owner's periods, ``start_date`` ascending, ``period_index`` running
        0..n-1 in that order.  Empty for an empty payday set.

    Raises:
        PayCalendarError: Anything :func:`validate_eras` refuses -- no era, an
            era whose cadence is not an ``int`` (``None`` included) or falls
            outside 1..365, eras out of order, or an era the seam rule leaves
            no payday; a ``period_id`` is neither
            an ``int`` nor ``None``; a payday is not a ``datetime.date``, or
            is a ``datetime.datetime`` (which is a ``date`` subclass and would
            silently give every derived end a time component); or a payday
            appears twice.
    """
    validate_eras(eras)
    # Sorted on the PAYDAY alone.  Sorting the pairs would break on a ``None``
    # id the moment two paydays tied -- and they cannot tie, which is checked
    # next, so keying the sort on the id would only hide that check.
    ordered = sorted(_validated(paydays), key=lambda pair: pair[1])
    for (_earlier_id, earlier), (_later_id, later) in zip(ordered, ordered[1:]):
        if earlier == later:
            raise PayCalendarError(
                f"payday {earlier.isoformat()} appears twice in the same "
                f"calendar.  A pay period is identified one-to-one by the "
                f"payday that opens it (uq_pay_periods_user_start enforces "
                f"that on the table), so two periods cannot share an opening "
                f"day; the first of them would be derived an end_date one day "
                f"before its own start_date."
            )

    if not ordered:
        # No last period, so no projected end, so the eras are unread.  They
        # were still validated above, deliberately: a bad sequence is a bad
        # caller whether or not this owner has paydays yet.
        return ()

    last_position = len(ordered) - 1
    derived = []
    for position, (period_id, payday) in enumerate(ordered):
        # ONE end rule, since plan step C14-c: a period runs to the day
        # before the NEXT payday.  All that differs for the last period is
        # where that payday comes FROM -- the projection rather than the
        # record -- which is what ``end_is_projected`` says.  The projection
        # is the ERA grid's next payday after the one this record stands for
        # (``C17-b-2``), never one cadence past the recorded day itself.
        is_last = position == last_position
        next_payday = (
            payday_after(eras, payday)
            if is_last
            else ordered[position + 1][1]
        )
        derived.append(
            DerivedPeriod(
                period_id=period_id,
                period_index=position,
                start_date=payday,
                end_date=next_payday - timedelta(days=1),
                end_is_projected=is_last,
            )
        )
    return tuple(derived)


def validate_eras(eras: "tuple[Era, ...]") -> None:
    """Refuse an era sequence no calendar can be derived from.

    Plan step ``pay_calendar:C17-b-2``.  Held to :func:`~._eras.validate_cadence`'s
    standard, and for the same reason: every reader below walks the sequence
    without re-checking it, so a bad one refused here is refused once rather
    than answered wrongly four ways.  Five things are refused, and each is a
    state the storage or the write door already keeps a STORED sequence out
    of -- so reaching this means a caller assembled the tuple by hand and got
    it wrong, and failing loud is the only safe disposition.

    * **No era at all.**  An owner with a ``budget.pay_schedule`` row and no
      era has stated no rhythm, and :func:`~._loader.calendar_for` refuses
      them (ruling **R-PC45**'s principle, one relation over) -- so no absence
      travels here for a reader to remember to test.
    * **A cadence outside the column's bound**, per era, through
      :func:`~._eras.validate_cadence`.
    * **A phase OFF its own grid**, per era (plan step ``C17-d-2``).  An
      era's ``effective_from`` is its first nominal payday, so its grid
      passes through it: ``nominal_payday(effective_from, cadence, 0)`` is
      the day itself.  A fixed-days grid passes through any anchor and
      cannot fail this; a day-of-month grid passes through its stated day
      only, so a ``Monthly(15)`` era phased on the 10th, or a
      ``SemiMonthly`` one phased on neither day, would project a step-zero
      payday that is not its own anchor and every reader would read the
      seam a day off.  The storage cannot hold that state (the anchor's own
      day IS the meant day unless ``nominal_day`` records the clamp, and
      ``ck_pay_eras_nominal_day`` ties that to a month too short to carry
      it) and ``pay_era_write.reject_phase_off_grid`` refuses it at the
      write door, so it reaches here from a hand-built sequence only.
    * **Eras out of order.**  ``effective_from`` must strictly ascend;
      ``uq_pay_eras_user_effective_from`` and the loader's ``ORDER BY`` hold
      that for a stored sequence.
    * **An era that pays NOTHING** (ruling **R-PC75**, plan step
      ``pay_calendar:C17-c-2b``).  A later era's first payday REPLACES the
      earlier era's last planned payday at or before it
      (:func:`~._eras.last_step_of`), so a first payday that falls before
      the earlier era's SECOND planned payday leaves that era no payday of
      its own -- its last step is below ``0`` -- and every reader here
      partitions the calendar on the eras' paydays.  This subsumes the
      check it replaced, that each era's FIRST PAYDAY (its
      ``effective_from`` displaced under its own convention,
      :func:`first_payday_of`) strictly ascends: first paydays that coincide
      or cross are the same state one cadence earlier.  The write door
      keeps a stored sequence out of it because its floor bounds a MINTING
      batch at the plan's next payday after the kept record, which is at or
      past the covering era's second payday whenever the record holds that
      era's first (ruling 2026-09-11, after an adversarial review of
      ``C17-b-2`` drove a legal sequence past a floor that saw only the
      batch's NEW paydays).  A record BELOW the earliest era's first step
      would void that argument: its floor is that era's first payday, and a
      rebuild from there leaves the era paying nothing past it.
      ``C18-b``'s door is the one that records below, and it keeps the
      argument whole by moving the earliest era's phase down with the
      paydays it adds (ruling **R-PC105**, :func:`~._eras.earlier_paydays`).
      ``C21``'s door, which retires the first paydays, cannot void it -- it
      leaves the record ABOVE the phase, the migrated shape below -- and it
      moves the phase up to the payday left first anyway (ruling
      **R-PC110**, :func:`~._eras.opening_rephase`), refusing a removal that
      would leave the earliest era none of its paydays.  So the record's
      first payday stands for that era's first step for every owner a door
      has written; the one era the ``C17-a`` migration
      backfilled may be phased up to a cadence below it
      (``models/pay_era.py``), which leaves that record at or above the
      era's first step, so the argument holds there too.

    Args:
        eras: The candidate sequence.

    Raises:
        PayCalendarError: The tuple is empty; an era's cadence is refused by
            :func:`~._eras.validate_cadence`; an era's ``effective_from`` is
            off its own cadence's grid; two consecutive eras are out of
            order by ``effective_from``; or a non-latest era would pay no
            payday under the seam rule.
    """
    if not eras:
        raise PayCalendarError(
            "a pay calendar needs at least one era: an owner holding a "
            "budget.pay_schedule row and no budget.pay_eras row has stated "
            "no rhythm, so nothing says how often they are paid or where "
            "their grid lies.  pay_calendar._loader.calendar_for refuses "
            "them; reaching here means a caller built the sequence by hand."
        )
    for era in eras:
        validate_cadence(era.rhythm.cadence)
        cadence = era.rhythm.cadence
        if nominal_payday(era.effective_from, cadence, 0) != era.effective_from:
            before = cadence_steps_to(era.effective_from, cadence, era.effective_from)
            raise PayCalendarError(
                f"pay era {era.effective_from.isoformat()} is phased on a "
                f"day its own grid does not pass through: paid "
                f"{cadence.phrase}, the grid days either side of it are "
                f"{nominal_payday(era.effective_from, cadence, before).isoformat()} "
                f"and "
                f"{nominal_payday(era.effective_from, cadence, before + 1).isoformat()}."
                f"  An era's effective_from is its first nominal payday, so "
                f"it must lie on the stated day of the month; "
                f"pay_era_write.reject_phase_off_grid refuses this at the "
                f"write door and the storage cannot hold it, so this sequence "
                f"was assembled by hand."
            )
    for earlier, later in zip(eras, eras[1:]):
        if later.effective_from <= earlier.effective_from:
            raise PayCalendarError(
                f"pay eras must take effect in strictly ascending order, got "
                f"{earlier.effective_from.isoformat()} followed by "
                f"{later.effective_from.isoformat()}.  "
                f"uq_pay_eras_user_effective_from and the loader's ORDER BY "
                f"hold that for a stored sequence, so this one was assembled "
                f"by hand."
            )
    for index, era in enumerate(eras[:-1]):
        if last_step_of(eras, index) < 0:
            following = eras[index + 1]
            raise PayCalendarError(
                f"pay era {following.effective_from.isoformat()}'s first "
                f"payday ({first_payday_of(following).isoformat()}) falls "
                f"before the previous era's second planned payday "
                f"({projected_payday(era.effective_from, era.rhythm, 1).isoformat()}"
                f", from {era.effective_from.isoformat()}, paid "
                f"{era.rhythm.cadence.phrase}), so that era would "
                f"pay nothing: a later era's first payday replaces the "
                f"earlier era's last planned payday at or before it (ruling "
                f"R-PC75).  pay_period_batch's floor keeps a minted era's "
                f"first payday at or past the plan's next payday after the "
                f"kept record, so this sequence was assembled by hand."
            )


def _validated(
    paydays: "Iterable[tuple[int | None, date]]",
) -> "list[tuple[int | None, date]]":
    """Return *paydays* as a list, refusing any pair it cannot derive from.

    ``datetime`` is a subclass of ``date``, so a bare ``isinstance`` check would
    accept one -- and every derived end would silently carry a time component,
    comparing unequal to the ``DATE`` column it is meant to reproduce and
    placing a day's money by an accident of the clock.  The stored column is
    ``DATE`` and the app's civil day is ``app.utils.dates.display_today()``, so
    a ``datetime`` reaching here is a caller that skipped the display-timezone
    conversion; refusing it names that at the boundary instead of at the diff.

    The id is checked too, even though nothing here computes with it: it rides
    onto :attr:`DerivedPeriod.period_id`, whose whole purpose is to be the
    thing a foreign key points at, so a value of the wrong type would surface
    as a failed lookup somewhere far from the caller that supplied it.

    Args:
        paydays: The candidate ``(period_id, payday)`` pairs, in any order.

    Returns:
        The same pairs as a list, unsorted.

    Raises:
        PayCalendarError: A payday is not a ``datetime.date`` or is a
            ``datetime.datetime``, or an id is neither an ``int`` nor ``None``.
    """
    checked = []
    for period_id, payday in paydays:
        if not isinstance(payday, date) or isinstance(payday, datetime):
            raise PayCalendarError(
                f"a payday must be a datetime.date, got "
                f"{type(payday).__name__} {payday!r}.  budget.pay_periods."
                f"start_date is a DATE column and the app's civil day is "
                f"app.utils.dates.display_today(); a datetime here would give "
                f"every derived end a time component and place a day's money "
                f"by the process timezone."
            )
        if period_id is not None and (
            not isinstance(period_id, int) or isinstance(period_id, bool)
        ):
            raise PayCalendarError(
                f"a period_id must be an int or None, got "
                f"{type(period_id).__name__} {period_id!r} beside payday "
                f"{payday.isoformat()}.  It is what a foreign key points at, "
                f"and None is how a period that is not materialised says so."
            )
        checked.append((period_id, payday))
    return checked
