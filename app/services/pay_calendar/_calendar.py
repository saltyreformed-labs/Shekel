"""The pay calendar as ONE value, answering every "which paycheck" question.

Plan step **C2-a** (shipped at ``3cb3082f``), the
first leaf of the step three arcs share -- it is also ``balance:X-l``
and ``recurrence:R-F12``.

**The first consumer arrived at plan step C2-d**, and until then nothing in
``app/`` called this at all -- that was C2-a's leaf boundary.  C1 built the
derivation and proved it byte-identical against production's 61 paydays before
anything read it; C2-a built the value and proved it the same way.  The cutover
leaves then move one consumer package each.  **C2-d took the first**: both
anchor-correction posting writers file every ledger entry's ``pay_period_id``
through :meth:`PayCalendar.filing_period`.  **C2-b2 took the second**: the
recurrence engine resolves, walks and places against this value, and the
``PeriodCalendar`` it used -- which COPIED each period's stored end and ordinal
-- was deleted with the fences it carried.  **C2-c took the third**: the balance
seam's thirteen per-period entries stopped TAKING a period list and now read
:meth:`PayCalendar.saved` off their read pass, which deleted
``_cash_periods._PeriodSpans`` -- a fourth index over the STORED spans.
**TWO readers of the stored columns survived INSIDE that seam and an earlier
draft of this paragraph claimed none did** (adversarial review, 2026-08-13):
``balance_at/_cash_fold._cash_plan`` clamps a projected row against
``txn.pay_period``'s span -- it still does, and it is named in the pay-calendar
plan's section 3 as ``C4``'s -- and ``balance_at/_asset_contributions`` walked
``pay_period_service.get_all_periods``, whose ORDER is the stored ordinal and
whose order its year-to-date accumulation depends on.  **The second went at
``C2-f2a``** (ledger row **P37**): the contribution tier takes the read pass's
own calendar, so no module under ``balance_at`` IMPORTS ``pay_period_service``
and the ordering rule is the derivation rather than a sort at that door.
``C2-e`` (the projection axis) has since shipped; ``C2-f3a`` deleted
``pay_period_service.get_current_period``; ``C2-f3b`` took the four destructive
schedule doors and the settings period list; and ``C2-f3c`` deleted
``get_all_periods`` with its last caller, the recurrence generation seam.  That
module now holds ``earliest_recordable_day`` alone.

Why the value exists at all: an AST census on 2026-08-10 found **SIX**
implementations of "which pay period contains this date" in ``app/`` -- ledger
row **P6**, which had claimed three until then -- and an adversarial review of
this step found a **SEVENTH** the same day (``savings_dashboard_service``'s
``_period_id_at``), which the census structurally could not see because it keyed
on the containment PREDICATE.  They disagree at exactly the edges that matter.
Two bisect and answer ``None`` outside the schedule; one scans linearly and
falls back past the end of it; one scans SYNTHETIC periods; two were SQL, and
one of those (``get_current_period``) had no ``ORDER BY`` at all (row **P19**).
**All seven are gone**, the last at ``C2-f3a``.
Seven answers to one question is the defect; the number of CONTAINMENT
questions is three, and they are named here so a caller has to choose:

===================================== ==================================
question                              method
===================================== ==================================
which SAVED paycheck covers this day  :meth:`PayCalendar.period_containing`
                                      -- ``None`` only BEFORE the first
                                      payday or PAST the horizon; these
                                      periods tile, so a hole is not a
                                      third case (ledger row **P25**)
which span covers this day, saved or  :meth:`PayCalendar.span_containing`
projected                             -- TOTAL from the first payday on
which SAVED paycheck does a record    :meth:`PayCalendar.filing_period`
FILE under                            -- never ``None``; it CLAMPS
                                      (ruling **R-PC53**)
===================================== ==================================

**Beside them sit FOUR ORDERING searches, which are a 2x2 rather than a list**,
and stating it as one is what keeps a caller from reaching for the wrong half:
the axis is BEFORE or AFTER a day, and the bound is INCLUSIVE or STRICT.

========== ============================================ ====================
axis       inclusive (the day itself qualifies)         strict (it does not)
========== ============================================ ====================
backwards  :meth:`period_starting_on_or_before`         :meth:`period_starting_before`
forwards   :meth:`period_starting_on_or_after`          :meth:`period_starting_after`
========== ============================================ ====================

**The strict pair arrived at plan step C2-f1** with the deletion of
``pay_period_service.get_next_period`` and ``companion_service.get_previous_period``,
which asked "the next / previous paycheck" as ``period_index +/- 1`` queries.
Reaching for an INCLUSIVE search to mean "the next one" answers with the SAME
period, and two of the callers write the answer into
``transactions.pay_period_id`` -- so the wrong pick files a credit-card payback
in the paycheck it is paying back.  The 2x2 is above the table for that reason,
and ``test_pay_calendar_value.TestPeriodStartingAfter`` pins all four corners.

**The filing rule was ruled a second QUESTION, not a compensator** (developer,
2026-08-10).  ``loan_ledger`` answers it today with a three-branch chain across
two functions, one of which is a public package export whose only caller sits
twelve lines below it.  Measured over 1,800 (shape, day) pairs of STORED-style
periods -- contiguous, gapped, two-hole, single-period, one-day and long-tail
-- that chain is exactly *"the latest period starting on or before the day,
else the earliest"*, which is
:meth:`PayCalendar.period_starting_on_or_before` clamped onto the saved set.
The gapped shapes live in that probe and not in this value's tests, because a
hole is unconstructible here: the periods tile.  So the rule is DERIVED from a
search rather than being a fourth scan, and the search itself is the missing
mirror of :meth:`PayCalendar.period_starting_on_or_after`, which the recurrence
arc's calendar already carried.

**Why the filing rule cannot simply be deleted: plan step ``C7`` RULED that
``journal_entries.pay_period_id`` STAYS** (ruling **R-PC53**, which holds the
measurements and the argument; nothing is restated here).  The short form is
that the column has TWO halves and ledger row **P18**'s premise -- that it
merely materialises ``entry_date`` -- fits neither as stated: a source-linked
entry COPIES its source row's own period, and an anchor correction DERIVES its
period through this method (ruling **balance:R-EA**).  A DROP would have
deleted this method, so it stays.

**A WINDOW is a VIEW, and the type is what makes that structural** (ledger row
**P14**).  A period's end is its successor's payday, so deriving a calendar from
a SLICE gives that slice's last row a cadence-projected end instead of a
fact-dictated one -- the same period reporting two different ends depending on
which window asked.  The sibling shape is measured at ``$150,000.00``
(:func:`~._derive.derive_periods`, which holds it now: it was on
``loan_ledger/_visible.owner_pay_periods``, deleted at plan step C2-d).  Here a
slice is a :class:`~._window.PeriodWindow`, which is NOT a
:class:`PayCalendar` and which carries the ends the whole calendar computed.
What that type guarantees is that no CONSTRUCTOR accepts a window -- not that a
window cannot be taken apart into paydays, which it can, and which an earlier
draft of this paragraph claimed otherwise (ledger row **P24**).

**This module holds the CALENDAR and nothing else**, which is what the
1,000-line ceiling has twice measured.  At plan step **C2-c** the shared
SEARCHES moved to :mod:`._searches` and the view type to :mod:`._window`; at
plan step **C2-f3b** the five VIEW PRODUCERS moved to :mod:`._views` and the
forward projection to :func:`~._projection.project_period_after` (ledger row
**P64**; the 1002 that row records was a transient inside C2-f3a's build,
resolved before that commit, and an earlier draft of this sentence read it as a
committed state).  **No line count is quoted here, and that is the correction
plan step R16-b-1 made**: this paragraph claimed "999 of the 1,000 permitted --
ONE line of headroom" and the file had been 890 for some time, so a stale number
was arguing for a constraint that was not binding.  ``pylint`` measures it on
every commit; a copy in prose does not.  The dependency runs one way through
all five -- ``_derive`` -> ``_searches`` -> ``_window`` -> ``_views`` -> this --
so a search, a view producer, a view and the calendar itself cannot answer one
question differently.

Boundary discipline (``CLAUDE.md``): no Flask symbol, no database session, no
clock.  Every answer is a pure function of the paydays and the eras the
caller supplies.
"""

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from datetime import date, timedelta
from types import MappingProxyType

from app.services.pay_rhythm import Era

from ._cadence import PayCadence
from ._derive import DerivedPeriod, derive_periods
from ._eras import PayCalendarError
from ._projection import project_period_after
from ._searches import (
    FiledRow,
    containing_period,
    earliest_started_period,
    final_covered_day,
    latest_started_period,
    materialised_periods,
    opening_payday,
    period_by_id,
    require_period,
    saved_by_id,
)
from ._views import (
    axis_window,
    current_and_future_window,
    index_window,
    overlapping_window,
    projection_axis_window,
    saved_window,
)
from ._window import PeriodWindow


@dataclass(frozen=True)
class PayCalendar:
    """One owner's whole pay calendar, derived from their COMPLETE payday set.

    **The calendar stores the PAYDAYS and DERIVES the periods, which is the
    same normalization the whole arc is about, applied to the value itself.**
    :attr:`periods` is not an ``__init__`` field: it is computed once at
    construction and the value is frozen, so the fact and its derivation cannot
    drift -- there is no writer to reconcile.  That also makes the TILING
    invariant structural rather than checked.  Consecutive paydays define
    adjacent intervals, so ``[opening_bound(), horizon()]`` is covered with no
    hole and no overlap BY CONSTRUCTION, and the recurrence arc's
    ``PeriodCalendar.__post_init__`` -- which raises on an overlapping or
    reversed schedule -- has nothing left to refuse.  A fence deleted by making
    its subject unconstructible, not by trusting a caller.

    **The completeness of the payday set is the one precondition this value
    cannot check**, and saying so is the honest form of ledger row P14: a slice
    of paydays is indistinguishable from a short schedule.  What IS structural
    is that a subset is a :class:`PeriodWindow`, which is not a calendar and
    cannot be derived from, so the obligation lands in exactly one place --
    :meth:`from_paydays` -- and no consumer can rebuild a calendar out of a
    window it was handed.

    Attributes:
        user_id: The owner whose paydays these are.  Carried rather than
            inferred because a calendar is legal with no periods at all (a
            companion holds none by design, and production has one such user),
            so there is no row to read it off.  It is what lets a consumer
            refuse to resolve one owner's rule against another owner's
            schedule -- a pairing that produces a plausible wrong answer rather
            than an error.
        paydays: The owner's COMPLETE payday set as ``(period_id, payday)``
            pairs.  The only fact here; everything else is derived from it and
            :attr:`eras`.
        eras: The owner's pay eras, ``effective_from`` ascending and
            non-empty, from ``budget.pay_eras``
            (:class:`~app.services.pay_rhythm.Era`): one row per *how I have
            been paid since*, each carrying the day its rhythm took effect --
            the grid's PHASE -- with the cadence and the payday convention.
            Read for the last saved period's end, for every projected period
            past it and for the rhythm below the record, each reader asking
            the era covering its own day and anchoring on that era's phase
            (plan step ``C17-b-2``, rulings **R-PC66** and **R-PC72**);
            validated by :func:`~._derive.derive_periods`.
            **It was one :class:`~app.services.pay_rhythm.Rhythm` until
            ``C17-b-2``** -- the schedule row's pair, then the LATEST era's at
            ``C17-a`` -- and a bare ``cadence_days`` until ``C14-e-1``, the
            step that gave the derivation a reader for the convention: from
            ``C14-e-3`` a projected payday is the nominal grid day displaced
            under it, so the pair is one owner's one rhythm and a calendar
            that carried half of it could be paired with another owner's
            other half.  The sequence travels whole for the same reason.
            **It is not optional, since plan step C4-d** (ruling
            **R-PC45**).  The cadence was
            ``int | None`` with ``None`` legal ONLY beside an empty
            :attr:`paydays`, a pairing enforced at construction and restated as
            a precondition by every producer it travelled to.  A calendar now
            HAS a rhythm: the owner it stood for -- no ``budget.pay_schedule``
            row, which since plan step C4-b-2 is also no pay periods
            (``fk_pay_periods_schedule``) -- has no calendar at all, because
            :func:`~._loader.calendar_for` refuses them.  **An EMPTY calendar is
            still ordinary and carries a real era**: an owner holding a
            schedule row and zero paydays, which
            ``pay_period_admin.reset_pay_periods`` passes through.
        history_opens_on: How far back this owner's PAYCHECKS reach, from
            ``budget.pay_schedule``, or ``None`` for NOT STATED -- in which
            case the backward rhythm answers nothing and only the RECORD is
            counted (ruling **balance:R-IA**, amended 2026-08-31).  Carried
            HERE for the reason :attr:`eras` is: the paydays and the
            bound on them are one owner's one rhythm, and a bound arriving
            separately can be paired with another owner's schedule.
            :mod:`._rhythm` is its only reader, and the column's comment
            carries the rule and why the null is an absence rather than a
            claim.
        periods: The owner's SAVED periods, ``start_date`` ascending.  DERIVED
            at construction, never passed in, and excluded from equality so two
            calendars compare on their facts.
        _saved: :meth:`saved`'s memo, filled on first use and excluded from
            equality for the same reason.  A one-element list because a frozen
            dataclass cannot rebind a field.
        _by_id: :meth:`saved_by_id`'s memo, on the same slot shape and for the
            same reason.
    """

    user_id: int
    paydays: "tuple[tuple[int | None, date], ...]"
    eras: "tuple[Era, ...]"
    history_opens_on: "date | None"
    periods: "tuple[DerivedPeriod, ...]" = field(
        init=False, repr=False, compare=False,
    )
    _saved: "list[PeriodWindow]" = field(
        init=False, default_factory=list, repr=False, compare=False,
    )
    _by_id: "list[Mapping[int, DerivedPeriod]]" = field(
        init=False, default_factory=list, repr=False, compare=False,
    )

    def __post_init__(self) -> None:
        """Derive the periods from the paydays, and CANONICALISE the paydays.

        ``object.__setattr__`` because the dataclass is frozen: both values are
        written exactly here, at construction, and nothing can rewrite either
        afterwards -- which is what makes :attr:`periods` a cache with a
        structural reconciler rather than the stored-derived-value defect this
        arc exists to remove.

        **:attr:`paydays` is rewritten FROM the derived periods, and that is
        not circular -- it is the fix for an ordering leak.**  A caller may
        hand over paydays in any order, so without this two calendars built
        from the same SET in different orders hold different tuples and compare
        unequal while every answer they give is identical: one value with two
        representations, which is the shape this whole arc removes.  Reading
        the canonical form back off the derivation means the ordering rule is
        stated once, in :func:`~._derive.derive_periods`, instead of twice.

        Raises:
            PayCalendarError: Anything :func:`~._derive.derive_periods` refuses
                -- a bad era sequence, a payday that is not a plain ``date``,
                an id that is neither ``int`` nor ``None``, or a repeated
                payday.
        """
        periods = derive_periods(self.paydays, self.eras)
        object.__setattr__(self, "periods", periods)
        object.__setattr__(
            self,
            "paydays",
            tuple((period.period_id, period.start_date) for period in periods),
        )

    @classmethod
    def from_paydays(
        cls,
        paydays: "Iterable[tuple[int | None, date]]",
        eras: "tuple[Era, ...]",
        user_id: int,
        history_opens_on: "date | None",
    ) -> "PayCalendar":
        """Build a calendar from an owner's COMPLETE payday set.

        Args:
            paydays: Every payday the owner has, as ``(period_id, payday)``
                pairs in any order.  ``period_id`` is the
                ``budget.pay_periods.id`` the payday was read from; ``None``
                marks a period no foreign key can point at.  **The whole set,
                never a window** -- see the class docstring.
            eras: The owner's pay eras, ``effective_from`` ascending, from
                ``budget.pay_eras`` (:class:`~app.services.pay_rhythm.Era`).
                **Required and non-empty since plan step C4-d**'s principle:
                an owner with no era has no calendar rather than a
                rhythm-less one, so there is no absence for this parameter
                to carry.
            user_id: The owner these paydays belong to.
            history_opens_on: How far back the owner's paychecks reach, or
                ``None`` -- see the class docstring.  **Required rather than
                defaulted**: ``None`` is a legitimate stored value AND what a
                forgetful caller would get, so a default would let a calendar
                claim an unbounded rhythm the owner never stated -- a wrong
                figure rather than an error.

        Returns:
            The frozen calendar.

        Raises:
            PayCalendarError: Anything :func:`~._derive.derive_periods` refuses.
        """
        return cls(
            user_id=user_id,
            paydays=tuple(paydays),
            eras=eras,
            history_opens_on=history_opens_on,
        )

    # ---- how often this owner is paid --------------------------------

    @property
    def cadence(self) -> PayCadence:
        """Return how often this owner is paid, as a value of its own.

        **So that a caller already holding a calendar never builds a second
        answer** (plan step R7a-2a).  :class:`~._cadence.PayCadence` is the one
        producer of "how many paychecks in a year" and of the unit conversions
        that rest on it; this is the door for the consumers that have a whole
        calendar in hand -- the Recurring surface, the recurrence write paths --
        while :func:`~._loader.cadence_for` serves the ones that need the
        cadence and nothing else.  Both answer from the LATEST era's stored
        cadence -- this one through :attr:`eras` -- so there is one
        fact and one derivation however it is reached.  The latest era's,
        because the question is how often the owner IS paid: a year holds the
        paychecks of the rhythm that continues past the record.

        **TOTAL since plan step C4-d** (ruling **R-PC45**), and the refusal it
        lost is the point of that step rather than a relaxation.  It raised
        ``PayCalendarError`` when the cadence was ``None``, which was
        reachable for exactly one owner: no ``budget.pay_schedule`` row, hence
        (``fk_pay_periods_schedule``) no paydays.  That owner has no CALENDAR
        now -- :func:`~._loader.calendar_for` refuses them where it used to
        answer an empty one -- so this property is never reached without a
        cadence and every ``cadence_days is not None`` guard written to keep it
        off a page has been deleted rather than left standing over a state
        nothing can produce.  **The refusal did not disappear; it moved UP**, to
        the one door that knows whether the owner has a schedule at all, where
        the reason it always gave still holds: every monthly equivalent in the
        application is a function of this number, and a silently assumed
        biweekly rhythm would render a weekly-paid owner every figure at half
        its true value.

        Returns:
            The owner's :class:`~._cadence.PayCadence`.
        """
        return PayCadence(self.eras[-1].rhythm.cadence)

    # ---- the schedule's own bounds -----------------------------------

    def opening_bound(self) -> "date | None":
        """Return the owner's first payday, or ``None`` for an empty calendar.

        The day below which :meth:`span_containing` stops answering.  Nothing
        is projected backwards past it: before an owner's first payday there is
        no paycheck, and inventing one would attribute money to a paycheck that
        never happened.  :meth:`filing_period` is the one rule that must answer
        there anyway, and it CLAMPS rather than projecting.

        Returns:
            The earliest ``start_date``, or ``None`` when the calendar is empty.
        """
        return opening_payday(self.periods)

    def horizon(self) -> "date | None":
        """Return the last day the SAVED schedule covers, or ``None`` when empty.

        Saved, deliberately, and the reason is NARROWER than the one an earlier
        draft gave (ledger row **P25**).  That draft said the recurrence engine
        needs this bound to tell a schedule HOLE from "the schedule has not
        reached there yet"; on a DERIVED calendar the periods tile, so there is
        no hole to tell apart and that reason has expired.  What has not: a
        horizon that moved with the projection would let generation seat a row
        in a period with no ``id`` for ``transactions.pay_period_id`` to point
        at.  The bound is about the foreign key, not about holes.

        Returns:
            The last saved period's ``end_date``, or ``None`` when empty.
        """
        return final_covered_day(self.periods)

    # ---- the three questions -----------------------------------------

    def period_containing(self, day: date) -> "DerivedPeriod | None":
        """Return the SAVED period whose span covers *day*, else ``None``.

        ``None`` is a real answer and not an error, and there are exactly TWO
        ways to get it here: *day* precedes the first payday, or it lies past
        the horizon.  **A hole is NOT one of them** -- these periods are derived
        from the paydays and therefore TILE, which is ledger row **P25** and
        which an earlier draft of this docstring got wrong by carrying the
        stored model's third case forward.  Answering ``None`` outside the
        covered span rather than clamping to the nearest period is the point:
        seating a bill in a paycheck whose span does not contain it silently
        misplaces real money.

        **SAVED is ENFORCED rather than described, since plan step C2-f2b** --
        the last of the five searches here to rest on "no calendar holding an
        UNSAVED candidate reaches it".  That was true, and this package has
        twice ruled true is not structural: :meth:`filing_period` and
        :meth:`period_starting_after` were each corrected after a review fed
        them a candidate and got ``period_id=None`` back for a ``NOT NULL``
        column.  Both consumers here write that column -- the recurrence engine
        PLACES a row on this answer, ``companion_service`` SCOPES its query by
        it -- and ``== None`` is ``IS NULL``, which returns no rows silently.

        Args:
            day: The calendar day to place.

        Returns:
            The containing :class:`~._derive.DerivedPeriod`, whose ``period_id``
            is never ``None``, or ``None`` when no saved period covers *day*.
        """
        return containing_period(materialised_periods(self.periods), day)

    def span_containing(self, day: date) -> "DerivedPeriod | None":
        """Return the span covering *day*, projecting past the horizon.

        **The TOTAL answer, and the reason this step exists** (``balance:X-l``,
        "the pay calendar answers any date").  The readers this replaced
        returned the SAVED rows and nothing else, so past the last payday every
        consumer improvised and the improvisations disagreed: the modelled
        replay's accrual tier keeps running while its contribution tier stops
        (``+$2,501.92`` and ``+$5,427.07`` at six months out, ledger rows
        **N-82** / **P7**), and ``growth_engine`` invents its own axis at a
        hardcoded 14-day cadence (row **P20**).

        A projected period carries ``period_id = None`` -- the marker C1 built
        for exactly this -- so a caller that needs a foreign key target cannot
        mistake one for a saved row.  Its ``period_index`` continues the saved
        sequence, and ``end_is_projected`` is ``True``.

        Answers ``None`` BEFORE the opening bound rather than projecting
        backwards: the ruling of 2026-08-10 is forward projection, because a
        day before the owner's first payday genuinely has no paycheck, and the
        one caller that needs an answer there is the FILING rule, which clamps
        (:meth:`filing_period`).

        Args:
            day: The calendar day to place.

        Returns:
            The covering :class:`~._derive.DerivedPeriod` -- MATERIALISED when
            the schedule reaches *day* (:meth:`period_containing` answers that
            half and states its own filter), projected when it does not -- or
            ``None`` when the calendar is empty or *day* precedes
            :meth:`opening_bound`.
        """
        opening = self.opening_bound()
        if opening is None or day < opening:
            return None
        saved = self.period_containing(day)
        # No third branch, and its absence is load-bearing.  A first cut had
        # one for "inside the saved span but in a hole"; the periods TILE
        # ``[opening_bound(), horizon()]`` by construction, so that branch was
        # unreachable -- dead code carrying a claim about a state this value
        # cannot hold.  Past the horizon there is no saved period, and the
        # projection answers.
        if saved is not None:
            return saved
        return project_period_after(self.periods, self.eras, day)

    def filing_period(self, day: date) -> DerivedPeriod:
        """Return the SAVED period a record dated *day* files under.

        **A different question from containment, ruled so 2026-08-10**, because
        it may never answer ``None``: every per-period reader in this app must
        be able to place every recorded entry, so this rule is TOTAL and CLAMPS
        rather than answering nothing.  ``journal_entries.pay_period_id`` being
        ``NOT NULL`` EXPRESSES that requirement rather than causing it.  The
        causation was stated the other way round here until plan step ``C7``,
        which is how ledger row **P18** came to read a reader requirement as a
        storage artifact (ruling **R-PC53**).

        The rule is ONE clamp: **the latest period starting on or before *day*,
        else the earliest**.  It replaced the three-branch chain
        ``loan_ledger.find_period_containing_date`` composed with
        ``resolve_anchor_pay_period`` -- containment, else the latest period
        ENDING before the day, else the earliest -- which plan step **C2-d**
        DELETED once both anchor-posting writers took this method.

        **The equivalence to that chain has a PRECONDITION, and the first
        statement of it here named only half.**  The two rules agree on every
        schedule that is non-overlapping AND whose ``period_index`` order
        matches its ``start_date`` order.  Drop the second half and they part
        company on 800 of 872 probed days: the old chain reduced by INDEX (its
        fallbacks took ``max(period_index)`` and its last resort ``periods[0]``)
        while this reduces by DATE.  Both halves were held by
        ``pay_period_service`` -- the batch guard ``_reject_overlapping_batch``
        and the tail-append at ``max_index + 1`` -- until plan step **C3-b**
        moved the writer to ``pay_period_write``, where neither survives as
        stated: an ordinal is no longer ASSIGNED at all, it is read off the
        derivation, so it cannot disagree with date order.  Where the two DO
        differ this one is
        right: it searches a calendar whose index is DERIVED from date order, so
        no stored ordinal can disagree with its own dates.

        The proofs, each saying what it covers:
        ``tests/test_services/test_pay_calendar_value.py`` grades this method
        against a transcription of the chain over every shape a
        :class:`PayCalendar` can HOLD, and separately over stored-style period
        lists a calendar cannot express -- gapped, two-hole, overlapping, and
        the index-order counterexample -- because derived periods TILE and so
        cannot produce a hole to test with.  *A second proof,
        ``tests/manual/verify_filing_cutover.py``, was named here until plan
        step ``C7``; plan step ``C4-c`` had DELETED it, so the pointer named
        nothing.*

        **It never reads a period's END, and that is why C2-d could ship ahead
        of the two steps that close the calendar's write doors.**  The other
        four C2 cutovers wait on ``balance:X-ad`` and ``C3`` because a DERIVED
        calendar absorbs a hole instead of reporting it (ledger row **P27**);
        this one bisects on ``start_date`` through
        :func:`latest_started_period` and so cannot tell a derived end from a
        stored one.  A hole changes which period a day is INSIDE; it does not
        change which period most recently OPENED.  *The METHOD is end-free; the
        composition a caller reaches it through is not, and saying only the
        first is how the exemption gets over-read.*
        *A paragraph stood here about ``resolve_cadence`` INFERRING a cadence
        from the last period's stored length for an owner with no
        ``budget.pay_schedule`` row -- plan finding **P8** -- and about a value
        outside 1..365 therefore refusing the calendar rather than mis-filing a
        record.  Plan step C4-b-2 deleted that inference and
        ``fk_pay_periods_schedule`` deleted the owner it served, so the cadence
        is now the stored column and nothing else.  The exemption this method
        claims never depended on it: the cadence moves only the last period's
        derived end, which this never reads.*

        **Always a MATERIALISED period**, and that is enforced rather than
        assumed.  A first cut searched all of :attr:`periods` and claimed the
        answer was saved because a projection is not in that tuple -- true of a
        projection and FALSE of an unsaved payday, which
        :func:`~._derive.derive_periods` accepts by design (``period_id`` is
        documented as ``None`` for "a candidate the writer has not saved yet",
        and plan step C3 makes the writer build exactly such a calendar).  An
        adversarial review of this step reproduced it: two lines of input, and
        the method returned a period whose id was ``None`` straight into a
        ``NOT NULL`` column.  It now searches the materialised periods only.

        Args:
            day: The date the record asserts or was posted at.

        Returns:
            The materialised :class:`~._derive.DerivedPeriod` the record files
            under.  Its ``period_id`` is never ``None``.

        Raises:
            PayCalendarError: The calendar holds no MATERIALISED period -- it
                is empty, or every payday in it is an unsaved candidate.  A
                loud refusal rather than ``None``: there is no period for the
                record to file under and no safe value to invent.
        """
        saved = materialised_periods(self.periods)
        if not saved:
            raise PayCalendarError(
                f"user {self.user_id} has no materialised pay period, so a "
                f"record dated {day.isoformat()} has no paycheck to file "
                f"under.  journal_entries.pay_period_id is NOT NULL and this "
                f"calendar holds {len(self.periods)} payday(s), none of them "
                f"saved, so there is no id to point at -- a broken invariant "
                f"rather than an input this can clamp."
            )
        located = latest_started_period(saved, day)
        return located if located is not None else saved[0]

    # ---- the two ordering searches -----------------------------------

    def period_starting_on_or_after(self, day: date) -> "DerivedPeriod | None":
        """Return the first SAVED period opening on or after *day*, else ``None``.

        "The NEXT paycheck."  One half of the recurrence placement axis
        (:class:`~app.enums.PeriodPlacementEnum`
        ``PERIOD_STARTING_ON_OR_AFTER``): a ``Monthly First`` rule puts its row
        in the first paycheck on or after the 1st of the month.

        Args:
            day: The calendar day to place.

        Returns:
            The first period whose ``start_date`` is on or after *day*, or
            ``None`` when the schedule reaches no such period -- a day past the
            horizon, where the answer is "not yet" rather than "never".
        """
        return earliest_started_period(self.periods, day)

    def period_starting_on_or_before(self, day: date) -> "DerivedPeriod | None":
        """Return the last SAVED period opening on or before *day*, else ``None``.

        "The MOST RECENT paycheck."  The exact mirror of
        :meth:`period_starting_on_or_after`, and the half that was missing --
        three modules had open-coded it, one of them as a scan with two
        fallbacks (ledger row **P6**).  :meth:`filing_period` is this search
        plus one clamp.

        Distinct from :meth:`period_containing` only in a HOLE and past the
        horizon, which is exactly where the six implementations P6 counts
        disagreed: containment answers ``None`` there, this answers the period
        that opened last.

        Args:
            day: The calendar day to place.

        Returns:
            The last period whose ``start_date`` is on or before *day*, or
            ``None`` when *day* precedes every payday.
        """
        return latest_started_period(self.periods, day)

    def period_starting_after(self, day: date) -> "DerivedPeriod | None":
        """Return the first MATERIALISED period opening STRICTLY after *day*.

        "The NEXT paycheck after this one", asked by passing that one's own
        payday.  The strict sibling of :meth:`period_starting_on_or_after`,
        and it exists as a method for the reason that search does: plan step
        **C2-f** retired ``pay_period_service.get_next_period`` -- a query on
        ``period_index + 1`` -- and "add a day, then search from there" is an
        off-by-one each of its callers would otherwise carry a copy of.  A copy
        that got it wrong would answer with the SAME paycheck, which for the
        two credit-payback writers means a payback landing in the period it is
        paying back.  **FOUR ``app/`` call sites, not the five the retired
        reader had**: the companion view's two collapsed into one
        ``_period_neighbours``, which asks this and its mirror together.

        Equal to the ordinal query it replaces by construction: this calendar's
        ``period_index`` IS payday order, so "the next index" and "the next
        payday" cannot name different periods -- which is the disagreement
        ``uq_pay_periods_user_index`` and three runtime fences existed to
        police on the stored columns, all five deleted with their subject at
        plan step C4-c.

        **MATERIALISED, and that filter is what makes the answer safe to write
        into a foreign key** -- the same enforcement :meth:`filing_period`
        carries, taken here for the same reason and put in before shipping by
        an adversarial review of C2-f1.  Two of the four callers write this
        period's id into ``transactions.pay_period_id``, which is ``NOT NULL``,
        and :attr:`~._derive.DerivedPeriod.period_id` is nullable in general.
        A projection is NOT the risk (this search never projects); the risk is
        the OTHER way a period is unmaterialised -- an unsaved candidate, which
        :func:`~._derive.derive_periods` accepts by design and which plan step
        C3's writer builds.  A first cut searched all of :attr:`periods` and
        argued in a docstring that no such calendar reaches these callers;
        that argument was TRUE and is not a property, which is the distinction
        :meth:`filing_period` was corrected on one step earlier.  Skipping a
        candidate is also the right answer on its own terms: a caller asking
        for the next paycheck wants one a record can point at.

        Args:
            day: The payday to search forward from, itself excluded.

        Returns:
            The next materialised period -- its ``period_id`` is never
            ``None`` -- or ``None`` when *day* falls in or after the last saved
            period, which is "the schedule has not reached there yet" and which
            every caller answers rather than projecting past.
        """
        return earliest_started_period(
            materialised_periods(self.periods), day + timedelta(days=1),
        )

    def period_starting_before(self, day: date) -> "DerivedPeriod | None":
        """Return the last MATERIALISED period opening STRICTLY before *day*.

        "The PREVIOUS paycheck", the exact mirror of
        :meth:`period_starting_after` and the half ``companion_service`` held
        as its own ``period_index - 1`` query until plan step **C2-f**.  Both
        halves of the companion view's period navigation ask one value now, so
        stepping back and then forward cannot land somewhere else -- a property
        two independent ordinal queries never had, and one
        ``test_pay_calendar_value`` asserts across the whole schedule.

        MATERIALISED for the reason :meth:`period_starting_after` gives, and
        symmetrically rather than because this side writes a foreign key: its
        one caller builds a URL from the id, and a link to ``/companion/period/None``
        is the same defect one register down.

        Args:
            day: The payday to search backward from, itself excluded.

        Returns:
            The previous materialised period -- its ``period_id`` is never
            ``None`` -- or ``None`` when *day* is at or before the owner's
            first payday.
        """
        return latest_started_period(
            materialised_periods(self.periods), day - timedelta(days=1),
        )

    # ---- identity, and the month a paycheck opens --------------------

    def period_by_id(self, period_id: "int | None") -> "DerivedPeriod | None":
        """Return the SAVED period carrying *period_id*, else ``None``.

        The one question here that is not about a DATE, and the TOTAL twin of
        :meth:`require_period` below: this one answers ``None`` because its
        callers hold an id a user typed, a URL carried or a nullable column
        holds, and each renders a 404 or an empty state for it.
        :func:`~._searches.require_period` states which of the twins a call
        site owes.

        **No count of its callers is written down, and that is this method's
        own lesson.**  The sentence here claimed ZERO, FIVE, SEVEN, ELEVEN and
        THIRTEEN in turn, once without any step touching it at all -- so what
        survives is the PREDICATE, which cannot go stale: call sites of
        this method or of :func:`~._searches.period_by_id` that live in
        ``app/`` OUTSIDE this package, which is
        ``grep -rn "period_by_id(" app/`` with the definitions, the re-exports
        and the docstring mentions struck out.  It does NOT match
        :meth:`require_period`'s callers, and a predicate that silently stopped
        covering a sibling is how the number went stale twice.

        Never answers a projected period, which has no id.

        Args:
            period_id: A ``budget.pay_periods.id``, or ``None``.

        Returns:
            The matching :class:`~._derive.DerivedPeriod`, or ``None`` -- see
            :func:`~._searches.period_by_id` for the two distinct ways that
            happens.
        """
        return period_by_id(self.periods, period_id)

    def require_period(self, filed: FiledRow) -> DerivedPeriod:
        """Return the SAVED period *filed* names, REFUSING one this calendar lacks.

        :meth:`period_by_id`'s RAISING twin, for a caller holding a row that is
        already FILED rather than an id someone supplied.  A stored
        ``budget.transactions.pay_period_id`` is NOT NULL under an
        ``ON DELETE CASCADE`` key -- as is ``budget.transfers``' -- so a
        ``None`` here is not "not found" -- it is a contradiction, and
        answering it would hand a money surface a decision it has no basis to
        make.  :func:`~._searches.require_period` holds the rule: which of the
        twins a call site owes, the two states that reach the refusal, and the
        three quieter answers that were weighed and refused.

        **It takes ONE value where it took two loose integers** (plan step
        ``pay_calendar:C4-a-5``).  :class:`~._searches.FiledRow` states why:
        the pair could be crossed, a crossed pair returns the WRONG period
        rather than raising, and one of this method's callers dates the daily
        balance line with the answer.

        Args:
            filed: The stored row and the period it names
                (:class:`~._searches.FiledRow`), normally built with
                :meth:`~._searches.FiledRow.for_row` -- never a submitted or
                nullable id, which is :meth:`period_by_id`'s question.

        Returns:
            The :class:`~._derive.DerivedPeriod` carrying ``filed.period_id``.

        Raises:
            RuntimeError: This calendar does not hold that period.
        """
        return require_period(self.periods, filed, self.user_id)

    def saved_by_id(self) -> "Mapping[int, DerivedPeriod]":
        """Return every MATERIALISED period of this calendar, keyed by its id.

        :meth:`period_by_id` in BULK, for a caller holding a whole ROW SET
        rather than one id -- and whose KEYS are that caller's query SCOPE, so
        a row it cannot place is unconstructible rather than guarded against.
        :func:`~._searches.saved_by_id` holds the rule, the measurement that
        decides an index against that scan, and what each caller owes.

        **Memoized on the same slot shape** :meth:`saved` **uses, and the
        reason is this method's own rule applied to itself**: one review pass
        asks it TWICE -- ``statement_match`` derives its candidate scope and
        its destination scope from it -- and the two apply doors build two
        scopes apiece, so a request asks four times.  A first cut declined the
        memo on a stated "callers run it once per pass", which was measured
        false by an adversarial design review the same day (2026-08-31).  Two
        asks in one request is this project's DRY violation rather than a cost,
        and it is the rule ``_candidates.candidates_for`` quotes three lines
        above its own call.

        **The memo is returned IMMUTABLE, which the window memo does not have
        to be.**  A :class:`~._window.PeriodWindow` is frozen; a ``dict`` is
        not, so a shared one lets a producer narrow the scope another producer
        in the same pass is holding -- and that scope decides whose budget rows
        a screen may offer money into.  A read-only proxy makes that
        unconstructible rather than conventional, and it costs a caller
        nothing: it is a scope for ``IN``, a key set for ``frozenset`` and an
        index for a lookup, all of which a proxy answers.

        Returns:
            A read-only ``{period_id: DerivedPeriod}``; empty for a calendar
            with no saved period, which admits nothing and is that owner's
            correct scope.
        """
        if not self._by_id:
            self._by_id.append(MappingProxyType(saved_by_id(self.periods)))
        return self._by_id[0]

    # ---- views, never calendars --------------------------------------
    #
    # Each delegates to :mod:`._views`, which holds the RULE and the argument
    # for it; what stays here is the contract a caller needs at the call site.
    # An adversarial review of plan step C2-f3b measured the first cut of this
    # section restating those arguments in full -- 103 lines of one claim
    # written twice, with nothing reconciling the two, which is the defect this
    # arc removes from data and would have reintroduced in prose.

    def saved(self) -> PeriodWindow:
        """Return every MATERIALISED period of this calendar as one window.

        The balance seam's whole reporting domain, read once per read pass
        through :meth:`~app.services.balance_at.BalanceContext.reported_periods`
        -- :func:`~._views.saved_window` states what the MATERIALISED filter is
        load-bearing for and why a projection is not here.

        **Memoized on the calendar rather than on its caller**, because this is
        where the derivation lives: the balance seam asks for it once per
        ACCOUNT (``build_maps`` over nine accounts ran the filter, the sort and
        the contiguity scan nine times for one answer), and a memo on the read
        pass would have been a memo of a memo.  The slot is a one-element list
        because the dataclass is frozen; it is excluded from equality, so two
        calendars still compare on their facts.  A RAISING build is not cached,
        so the refusal fires on every call rather than once.

        Returns:
            The :class:`~._window.PeriodWindow` over every saved period,
            ``start_date`` ascending.  Empty for a calendar with no saved period.

        Raises:
            PayCalendarError: The saved periods do not cover an unbroken span
                (:func:`~._views.saved_window`).
        """
        if not self._saved:
            self._saved.append(saved_window(self.periods))
        return self._saved[0]

    def window(self, first_index: int, count: int) -> PeriodWindow:
        """Return *count* SAVED periods from ordinal *first_index* onward.

        The grid's six-period window and every other index-keyed slice
        (:func:`~._views.index_window`).

        Args:
            first_index: The first ``period_index`` to include.
            count: How many periods to take; a non-positive count yields an
                empty window.

        Returns:
            The :class:`~._window.PeriodWindow`, shorter than *count* when the
            calendar ends first and empty when *first_index* is past the end.
        """
        return index_window(self.periods, first_index, count)

    def current_and_future(self, day: date) -> PeriodWindow:
        """Return the periods that have not ENDED before *day*.

        "How many paychecks are left", counting the one *day* falls in
        (:func:`~._views.current_and_future_window`).  The rolling top-up's
        target is compared against this; plan step **C4** moved the question
        onto this value from a ``PayPeriod.end_date >= as_of`` count in SQL,
        which was the last query in ``pay_period_admin`` naming a column plan
        step C4-c dropped (finding **P70**).

        Args:
            day: The first day the window covers, inclusive.

        Returns:
            The :class:`~._window.PeriodWindow` of periods ending on or after
            *day*.  **Empty rather than refused** when every period has already
            ended -- unlike :meth:`overlapping`, which is the same question with
            its bounds written out and which treats ``[day, horizon()]``
            crossed as a caller defect.
        """
        return current_and_future_window(self.periods, day)

    def overlapping(self, first_day: date, last_day: date) -> PeriodWindow:
        """Return every SAVED period overlapping ``[first_day, last_day]``.

        Both bounds inclusive -- the calendar-month and calendar-year slices the
        reporting surfaces ask for (:func:`~._views.overlapping_window`).

        Args:
            first_day: Inclusive lower bound of the range.
            last_day: Inclusive upper bound of the range.

        Returns:
            The overlapping periods as a :class:`~._window.PeriodWindow`, empty
            when none overlaps.

        Raises:
            PayCalendarError: *last_day* precedes *first_day* -- a caller with
                its bounds crossed rather than an empty range.
        """
        return overlapping_window(self.periods, first_day, last_day)

    def axis(self, first_day: date, last_day: date) -> PeriodWindow:
        """Return the spans covering ``[first_day, last_day]``, projecting past the horizon.

        The STRICT producer: it covers the range it is given or it refuses, and
        never covers part of one (ledger row **P23**).  Every projecting surface
        in ``app/`` calls :meth:`projection_axis` instead, which clamps;
        :func:`~._views.axis_window` holds the rule and the argument for both.

        Args:
            first_day: Inclusive lower bound.  Must be at or after
                :meth:`opening_bound` unless the calendar is empty.
            last_day: Inclusive upper bound; may lie past :meth:`horizon`.

        Returns:
            The covering periods as a :class:`~._window.PeriodWindow`, saved
            where the schedule reaches and projected beyond it.

        Raises:
            PayCalendarError: *last_day* precedes *first_day*, or *first_day*
                precedes :meth:`opening_bound`.
        """
        return axis_window(
            self.periods, self.eras, self.user_id, first_day, last_day,
        )

    def projection_axis(self, first_day: date, last_day: date) -> PeriodWindow:
        """Return the paychecks a FORWARD projection over ``[first_day, last_day]`` runs on.

        :meth:`axis` with ONE clamp -- *first_day* is raised to
        :meth:`opening_bound` -- and the TOTAL companion every projecting
        surface actually calls, exactly as :meth:`filing_period` sits beside
        :meth:`period_starting_on_or_before`
        (:func:`~._views.projection_axis_window`).

        Args:
            first_day: The day the projection window opens -- the day AFTER the
                balance it seeds from is valued.
            last_day: The last day the projection reaches.

        Returns:
            The :class:`~._window.PeriodWindow` covering the (possibly raised)
            range.  Empty for a calendar with no payday, and when the raised
            range would end before it starts.

        Raises:
            PayCalendarError: *last_day* precedes *first_day* as the caller
                supplied them.  A CROSSED range is refused where a CLAMPED-empty
                one is answered, and telling them apart is the point.
        """
        return projection_axis_window(
            self.periods, self.eras, self.user_id, first_day, last_day,
        )
