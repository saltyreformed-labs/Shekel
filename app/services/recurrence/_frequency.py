"""What a recurrence's cadence IS, with no schedule to measure it against.

Plan step **R7a-2b**.  :func:`app.services.recurrence.resolve` answers what a
recurrence MEANS against one owner's pay calendar, and most of that answer does
not need the calendar at all: the interval, the unit and the placement are what
the rule itself states, and only the anchor -- where the first occurrence lands
-- needs a schedule.  This module is that schedule-free half, split out so a
consumer holding no calendar can still ask how often something repeats.

**The split is what makes the monthly equivalent possible.**
``obligations_aggregator`` turns a per-occurrence amount into a monthly one for
every recurring template on ``/savings`` and the Recurring surface, and it has
no :class:`~app.services.pay_calendar.PayCalendar`, and neither do its
savings-dashboard callers.  (The Recurring surface DOES hold one and reads
``calendar.cadence`` off it -- an adversarial review corrected an earlier
"nor do their callers" here, which was checkable and wrong.)
Before this step it therefore could not use the two-axis vocabulary at all and
read ``pattern_id`` through a seven-branch switch, which is why a cadence plan
step R8 authors -- ``(2, MONTH)`` -- would have read "Every 2 months" in the
Recurrence cell beside a BLANK monthly figure.  One expression over
``(interval_n, unit)`` answers for every cadence, including the ones nothing
authors yet.

**The dependency runs ONE way** -- ``_resolution`` imports this module and this
module imports nothing of it -- which is what keeps the split a boundary rather
than a pair of files that need each other.

**The SEAM between two vocabularies LEFT at plan step R7c-c, and it left
because there is only one vocabulary now.**  ``budget.recurrence_rules`` stated
its cadence with a closed set of eight pattern names until then, so this module
carried the translation: ``PATTERN_DERIVATIONS`` and its computed inverse,
``encode_cadence`` (authored -> stored) and ``decode_pattern`` (stored ->
authored), plus ``stored_interval`` and ``cadence_of`` reading through them.
``interval_n`` and ``unit_id`` are authored columns from that step, so a
caller's ``(interval_n, unit, placement)`` IS what the table holds and there is
nothing to translate.  All seven are deleted; :class:`Cadence` -- the pair
itself -- :meth:`Cadence.occurrences_per_year` and the family router survive,
being facts about the two axes rather than about how they were stored.

**The ANCHOR FAMILY router LEFT at plan step R8-a, and it left because the
derivations it selected between were deleted two steps earlier.**  It arrived
here at R7b-2 as ``anchor_family`` plus three ``FAMILY_*`` constants, naming
which of three reconstructions of a rule's first occurrence a
``(unit, placement)`` pair used.  Ruling **R-R16** made that date AUTHORED:
R7c-b deleted all three derivations, and from that step
``_resolution._first_occurrence`` branches on ONE thing -- whether the unit's
occurrences are paydays.  What survived was a three-valued router selecting
between nothing, still gating the offer set, and still refusing two cadences by
naming derivations that no longer existed.  Measured before it went, over all
eight ``(unit, placement)`` pairs: the router agreed exactly with
``has_day_of_month_coordinate(unit) and placement is CONTAINING_DATE`` on every
pair it answered for, and disagreed only on the three it refused.

**The OFFER SET is derived from what a rule can DO, not from a name it can
have** (:func:`~._offer.authorable_cadences` -- the offer set and the
predicate it rests on live in :mod:`._offer` since plan step salary:R15-a, a
pure move made when the cadence's third value took this module past pylint's
1000-line ceiling; the argument stays here, the code implements it there), and
that is plan step R7b-2's property restated on live constraints.  R7b-2 served
the form's options from the storage ENCODER, so a cadence the closed pattern
set could not name was unofferable
rather than fenced; R7c-c dropped that encoder, and R7b-2's successor gate was
this router.  TWO rules replaced it, each derived from a fact stated once
elsewhere and each naming the live thing it rested on:

* the cadence's occurrences must be DATABLE onto a generated row
  (``has_row_date_coordinate``) -- the ``WEEK`` unit's were not, until plan
  step **R8-b** dated a weekly row from its occurrence
  (``_row_day.date_row``) and deleted the rule;
* the placement must be able to CHANGE the answer
  (:func:`emits_period_starts`) -- under the ``PERIOD`` unit it cannot.  This
  is the one rule left.

The interval is not a third: :func:`require_positive_interval` predates the
router and was never gated by it, and every positive interval is authorable on
every offered pair.

**The per-month CEILING joined the vocabulary at plan step salary:R15-a**
(ruling **R-SAL29**), as a third value on :class:`Cadence` rather than a new
unit or placement: "every paycheck, at most 2 a month" is how a payroll benefit
taken on a month's first two paychecks is stated, and neither axis could say it
-- ledger row **D59** measured the developer's Health Insurance Allowance held
as an every-paycheck rule that an ``end_date`` happened to stop before the
first three-payday month.  Like the interval it is not part of an OFFER: every
positive ceiling is authorable wherever a unit can put two occurrences in a
month (:func:`can_repeat_within_month`), and refused where it cannot.

No Flask, no ORM, no clock, and no database read: a unit's day-of-month flag
comes from the reference cache, loaded once per process (ruling plan#2748).
"""
from dataclasses import dataclass
from decimal import Decimal

from app import ref_cache
from app.enums import (
    PeriodPlacementEnum,
    RecurrenceUnitEnum,
)
from app.exceptions import ShekelError
from app.services.pay_calendar import PayCadence
from app.services.pay_rhythm import FixedDays
from app.utils.money import MONTHS_PER_YEAR
from app.services.recurrence._months import months_per_step
from app.services.recurrence._vocabulary import (
    modelled_placement,
    modelled_unit,
)

#: A seven-day pay cadence, used ONLY for its yearly count.
#:
#: The WEEK unit fires ``round(365.2425 / 7) = 52`` times a year -- the SAME
#: derivation :class:`~app.services.pay_calendar.PayCadence` applies to an
#: owner's own cadence, applied to a fixed seven days.  Deriving it rather than
#: writing ``52`` keeps ONE rule for "how often does something every N days
#: happen in a year"; plan step R8-b is the WEEK unit's first writer and
#: inherits that rule without a second one being invented for it.
_WEEKLY = PayCadence(FixedDays(7))

#: How many times a year each CALENDAR unit fires, before its interval divides
#: the count.
#:
#: The twelve comes from :data:`app.utils.money.MONTHS_PER_YEAR`, the MONEY
#: constant, and not from ``recurrence._months``' integer twelve: this count is
#: a factor in a monthly equivalent, and an adversarial review of plan step
#: R7a-2b noted that one expression was reading its twelve from two modules.
#: ``_months``' is for month-ORDINAL arithmetic and stays where it is.
#:
#: ``PERIOD`` is absent DELIBERATELY, and the absence is not a lookup miss:
#: it is the one unit whose count is a property of the OWNER rather than of the
#: calendar, so :meth:`Cadence.occurrences_per_year` answers it from the pay
#: cadence BEFORE consulting this table.  An entry for it would have to be a
#: placeholder, which is how a per-owner fact becomes a constant again -- the
#: defect plan step R7a-2a removed.
_CALENDAR_UNITS_PER_YEAR: dict[RecurrenceUnitEnum, Decimal] = {
    RecurrenceUnitEnum.WEEK: _WEEKLY.periods_per_year,
    RecurrenceUnitEnum.MONTH: MONTHS_PER_YEAR,
    RecurrenceUnitEnum.YEAR: Decimal(1),
}

class RecurrenceFrequencyError(ShekelError):
    """A cadence names a unit with no yearly count.

    A broken invariant, not user input: every member of
    :class:`~app.enums.RecurrenceUnitEnum` must convert to a number of
    occurrences a year, because every monthly-equivalent figure in the
    application is that number over twelve.  A member added to the enum
    without one would otherwise contribute a silently wrong figure to the
    emergency-fund baseline and to every per-goal contribution floor.

    Raised rather than defaulted for the reason the whole redesign exists: a
    partial function over an enum is the defect being removed, and a plausible
    wrong number on a financial surface is worse than an error.  A
    :class:`~app.exceptions.ShekelError` like the other refusals this package
    makes, so a handler written against that hierarchy catches it.
    """


@dataclass(frozen=True)
class Cadence:
    """How often a recurrence fires: an interval and the unit it counts.

    The two-axis reading with the schedule-dependent half removed.  It is what
    :class:`~app.services.recurrence.ResolvedRecurrence` carries as its first
    two fields -- read back through
    :attr:`~app.services.recurrence.ResolvedRecurrence.cadence`, so the two are
    one value rather than two copies -- and what
    :func:`~app.services.recurrence.cadence_of` answers for a caller with no
    calendar to resolve against.

    **It carries the per-month CEILING since plan step salary:R15-a** (ruling
    **R-SAL29**), because the ceiling is part of how often a rule fires: "every
    paycheck, at most 2 a month" fires 24 times a year on a biweekly owner, not
    26, and a monthly equivalent that read the pair alone would price the
    developer's eleven such payroll lines at ``26/12`` of their real cost.
    The ceiling is meaningful only for a unit that can put more than one
    occurrence in a calendar month (:func:`can_repeat_within_month`); a
    calendar-month cadence fires at most once a month by construction, so the
    field is ``None`` there and :func:`~app.services.recurrence.RecurrenceSpec`
    refuses anything else.

    Attributes:
        interval_n: How many *unit*\\ s pass between occurrences.  Always the
            two-axis reading: 3 for Quarterly, 6 for Semi-Annual, the authored
            count for ``Every N Periods``, 1 elsewhere.
        unit: The cadence unit *interval_n* counts.
        max_per_month: The most occurrences the rule admits in any one calendar
            month, or ``None`` for no ceiling -- which is every rule authored
            before plan step salary:R15-a and every calendar-month cadence.
            For the paycheck unit the N are the month's first N paydays on the
            OWNER'S CALENDAR (ruling R-SAL29 as amended 2026-09-13), so "every
            paycheck, at most 2 a month" is a month's first two paychecks
            whatever paycheck the rule started on; for the week unit they are
            the rule's own first N of the month, a weekly date being no
            payday.  ``_occurrence._ceilinged`` carries the argument.
    """

    interval_n: int
    unit: RecurrenceUnitEnum
    max_per_month: int | None = None

    def units_per_year(self, pay_cadence: PayCadence) -> Decimal:
        """Return how many of this cadence's UNIT fit in a year, EXACTLY.

        The count before :attr:`interval_n` divides it -- 26 paychecks, 52
        weeks, 12 months, 1 year -- and always a whole number, which is what
        makes it the right thing to hand a money conversion.

        **The interval is deliberately NOT applied here, and that is an
        accuracy decision measured rather than assumed.** The obvious shape is
        to return :meth:`occurrences_per_year` and let the caller multiply, but
        that quotient is inexact for any interval that does not divide its
        unit's year -- ``52 / 12`` is ``4.333...`` -- so multiplying money by
        it rounds twice.  Measured: over 52,000,000 (cadence, interval, amount)
        combinations the two orders disagreed on **31,072 displayed cents**, and
        the lossy one was WRONG where they differed -- ``$0.18`` every 12 weekly
        paychecks is exactly ``$0.065`` a month, which it computes as
        ``0.06499...`` and rounds down to ``$0.06``.

        Handing back the exact count lets a caller divide ONCE, by the exact
        integer ``interval_n * 12``.  That form reproduces every one of the
        seven hand-written branches plan step R7a-2b replaced **at the
        displayed cent**, over 13,500,000 comparisons -- and it is provably so
        rather than merely swept: for ``a = A/100`` the exact value is
        ``A*U / (1200n)``, which is either exactly a half-cent (and then
        exactly representable, so both forms compute it exactly) or at least
        ``1/(1200n)`` away from one, while both forms' error is ~1e-27
        relative.  There is no amount at which they can round differently.

        **They are NOT equal unquantized, and an adversarial review corrected
        this docstring for claiming they were**: against the ``Every N
        Periods`` branch the last digits differ in about 21% of cases
        (``a=0.28, ppy=11, n=3`` gives ``...558`` against ``...556``), because
        that branch rounded twice and this divides once.  Where they differ
        this one is the correctly-rounded value.  The difference is ~1e-27 and
        both entry points round at their own boundary, so no published figure
        moves; the honest claim is "same cent", not "same digits".

        Args:
            pay_cadence: How often the owner is paid
                (:class:`~app.services.pay_calendar.PayCadence`).  Read only by
                the ``PERIOD`` unit; a calendar-space cadence needs no
                schedule, which is what lets a caller holding neither still ask
                about a monthly bill.

        Returns:
            The whole number of units in a year, as a ``Decimal``.

        Raises:
            RecurrenceFrequencyError: *unit* has no yearly count -- a member
                added to :class:`~app.enums.RecurrenceUnitEnum` without one.
        """
        if self.unit is RecurrenceUnitEnum.PERIOD:
            return pay_cadence.periods_per_year
        per_year = _CALENDAR_UNITS_PER_YEAR.get(self.unit)
        if per_year is None:
            raise RecurrenceFrequencyError(
                f"recurrence unit {self.unit!r} has no yearly count.  Every "
                f"member of RecurrenceUnitEnum must have one: a monthly "
                f"equivalent is that count over twelve, so a unit without one "
                f"would contribute a silently wrong figure to the "
                f"emergency-fund baseline and to every per-goal contribution "
                f"floor."
            )
        return per_year

    def occurrences_per_year(self, pay_cadence: PayCadence) -> Decimal:
        """Return how many times a year this cadence actually fires.

        :meth:`units_per_year` divided by the interval, and the one fact
        "how often is this" questions read.  Its live caller is
        ``calendar_service``'s infrequent-transaction badge, which asks
        whether a definition fires less often than monthly -- a COMPARISON,
        where the inexactness :meth:`units_per_year` documents cannot change
        an answer: the boundary cases divide evenly (26/2, 12/1) and a
        fractional one is nowhere near 12.

        **A money conversion must NOT use this**; see :meth:`units_per_year`
        for the 31,072 cents that says why.

        **The per-month ceiling caps it** (plan step salary:R15-a): a rule
        that admits at most N occurrences a month fires at most ``12 N`` times
        a year, so the answer is that where the ceiling binds
        (:meth:`ceiling_binds`, the ONE comparison) and the pair's own rate
        otherwise.  An annualised RATE like the pair's, not a count of any
        particular year: it is exact whenever every calendar month holds at
        least N of the occurrences the ceiling counts -- true for the payroll strides
        (7 and 14 days) and for every stride of eleven days or more, measured
        over 2026 by an adversarial review of this step -- and a shorter
        stride ceilinged near a month's capacity over-counts by one where a
        28- or 30-day month falls short (stride 3 at N = 10 fires 119 times,
        not 120).  The same class of rounding :meth:`units_per_year` already
        accepts for a 27-payday year.

        Args:
            pay_cadence: How often the owner is paid.

        Returns:
            The rate as an unquantized ``Decimal`` -- ``26`` for every
            paycheck on a biweekly schedule, ``24`` for every paycheck at most
            twice a month, ``4`` for quarterly, ``1`` for annual, ``26 / 3``
            for every third paycheck.

        Raises:
            RecurrenceFrequencyError: See :meth:`units_per_year`.
        """
        if self.ceiling_binds(pay_cadence):
            return self.max_per_month * MONTHS_PER_YEAR
        return self.units_per_year(pay_cadence) / self.interval_n

    def ceiling_binds(self, pay_cadence: PayCadence) -> bool:
        """Return whether the per-month ceiling admits FEWER occurrences than the pair.

        The one comparison behind :meth:`occurrences_per_year`'s cap and
        :meth:`monthly_equivalent`'s exact arm, spelled over integers so it
        cannot round: the pair fires ``units / interval_n`` times a year and
        the ceiling admits ``12 * max_per_month``, and the second is smaller
        exactly when ``12 * max_per_month * interval_n < units``.

        Args:
            pay_cadence: How often the owner is paid.

        Returns:
            ``True`` when the ceiling is the binding rate.  Always ``False``
            with no ceiling.

        Raises:
            RecurrenceFrequencyError: See :meth:`units_per_year`.
        """
        if self.max_per_month is None:
            return False
        return (
            self.max_per_month * MONTHS_PER_YEAR * self.interval_n
            < self.units_per_year(pay_cadence)
        )

    def monthly_equivalent(
        self, amount: Decimal, pay_cadence: PayCadence,
    ) -> Decimal:
        """Return what *amount* per occurrence comes to in a month.

        **The ONE monthly-equivalent producer since plan step salary:R15-a**,
        and it moved here from ``obligations_aggregator`` because the ceiling
        gave it a second arm: a rule whose ceiling binds costs exactly
        ``amount * max_per_month`` a month -- an integer multiply, exact -- and
        every other rule costs ``amount * units / (interval_n * 12)``, ONE
        division by an exact integer.  That second form is deliberately not
        ``amount * occurrences_per_year / 12`` and deliberately not
        ``per_paycheck_to_monthly(amount / n)``: both round twice, and
        :meth:`units_per_year` carries the 31,072-cent measurement of what
        that costs.  Keeping the two arms on the value is what stops a caller
        from applying the uncapped formula to a ceilinged rule -- which would
        price the developer's eleven "at most 2 a month" payroll lines at
        ``26/12`` of their real monthly cost.

        Args:
            amount: What one occurrence is worth.
            pay_cadence: How often the owner is paid.

        Returns:
            The unquantized monthly figure; the caller rounds at its own
            boundary, as every consumer of this module's counts already does.

        Raises:
            RecurrenceFrequencyError: See :meth:`units_per_year`.
        """
        if self.ceiling_binds(pay_cadence):
            return amount * self.max_per_month
        return (
            amount * self.units_per_year(pay_cadence)
            / (self.interval_n * MONTHS_PER_YEAR)
        )


@dataclass(frozen=True)
class CadenceReading:
    """What one STORED rule says on both authored axes.

    The whole of what a rule's cadence columns say, held as one value so a
    caller cannot take the cadence and forget the placement -- two rules with
    the identical ``(1, MONTH)`` cadence differ only in it, and reading one
    without the other is how a bill that funds from the month's first paycheck
    comes to be treated as one that funds from the paycheck containing its own
    date.

    **It was ``PatternReading`` until plan step R7c-c**, when it stopped being a
    reading OF a pattern: it was what ``decode_pattern`` recovered from
    ``pattern_id`` plus the ``interval_n`` column, and it is now what
    ``unit_id`` / ``placement_id`` / ``interval_n`` say directly.  The value's
    shape did not move and neither did any consumer's use of it; the name did,
    because a name that says "pattern" would be the closed set's last surviving
    claim on a vocabulary it no longer holds.

    Attributes:
        cadence: How often the rule fires.
        placement: Which pay period an occurrence is funded from.
    """

    cadence: "Cadence"
    placement: PeriodPlacementEnum


class RecurrenceResolutionError(ShekelError):
    """A recurrence could not be resolved into a complete row.

    A broken invariant rather than bad user input, which is why it is not a
    ``ValidationError`` a route flashes: every user has had at least one pay
    period since registration bootstraps one (``registration_service.register_user``),
    and both cadence axes are ``NOT NULL`` columns under ``RESTRICT`` foreign
    keys onto ``ref`` tables the enums mirror.  Raised loudly so a recurrence
    can never be READ with a fabricated cadence, and -- because
    ``app.services.recurrence.author_rule`` resolves before it writes -- so a
    rule that cannot be resolved is never persisted in the first place.

    The second sentence named ``pattern_id`` until plan step **R9**, four
    steps after R7c-c dropped that column: what a broken cadence looks like
    now is a ``unit_id`` or ``placement_id`` naming a ``ref`` row the enums do
    not model.
    """




def has_day_of_month_coordinate(unit: RecurrenceUnitEnum) -> bool:
    """Return whether a cadence measured in *unit* fires on a day of the month.

    **The one statement of the question three producers and one reader ask**,
    and making it one is plan step R7c-b's fix for a wrong-money defect that
    step itself introduced.  Every consumer used to reach for whichever of the
    two nearby predicates was in scope:

    * :func:`~app.services.recurrence.offerable_nominal_days` and
      :attr:`~app.services.recurrence.ResolvedRecurrence.day_of_month` ask
      THIS question -- the unit's;
    * :func:`fires_on_day_of_month` asks a DIFFERENT one -- whether the day a
      generated ROW is dated from comes off that coordinate -- and the two
      differ for exactly one cadence, ``Monthly First``.

    They differ because a MONTH-unit rule funded from a month's FIRST paycheck
    still fires on a day of the month; only the row it generates is dated from
    the paycheck.  The occurrence walk reads
    :attr:`~app.services.recurrence.ResolvedRecurrence.day_of_month` for it
    like any other MONTH rule, so its nominal day is meaningful -- and asking
    the other question where this one belongs is what let a "last day of every
    month" rent lose its ``nominal_day`` and move to the 30th forever
    (``recurrence_form.js``), and a month-end loan payment bill a day early for
    the life of the loan (``loan_recurrence_sync.loan_cadence_start``).

    **It lived in ``_resolution`` until plan step R8-a**, and it moved because
    :func:`fires_on_day_of_month` is stated over it directly from that step and
    this module may not import its own consumer.  The move is also where it
    belongs on this package's own division: which coordinate a cadence has is a
    fact about the cadence and needs no schedule, which is this module's whole
    charter.  ``_resolution`` imports it and the public name is unchanged.

    **Read off the unit's reference row** (``ref_cache
    .recurrence_unit_has_day_of_month``) since plan step plan#2302, ruling
    plan#2748.  It was a membership test against ``_months``' code tuple of the
    month-spanning units, itself the key set of that module's month-span table,
    which an adversarial review of plan step R7b-1 required over a second
    literal ``(MONTH, YEAR)``.  The row's ``has_day_of_month`` is GENERATED by
    the database from the same ``months_per_unit`` the month stride walks, so
    the two still cannot disagree -- and a rule's CHECKs read that same column,
    which no code tuple could give them.  Firing on a day of the month is not a
    second fact about a unit: a cadence measured in months has a day-of-month
    coordinate and one measured in paychecks or weeks does not.

    Args:
        unit: The cadence unit.

    Returns:
        ``True`` for the units measured in whole months, which are the only
        ones whose occurrences can be month-end clamped.
    """
    return ref_cache.recurrence_unit_has_day_of_month(unit)


def can_repeat_within_month(unit: RecurrenceUnitEnum) -> bool:
    """Return whether a cadence measured in *unit* can fire twice in one month.

    **The one statement of which units a per-month ceiling can bind** (plan
    step salary:R15-a, ruling **R-SAL29**), asked by four consumers that must
    agree: :class:`~app.services.recurrence.RecurrenceSpec` refuses a ceiling
    on any other unit, the shared recurrence schema refuses the same
    submission with a message, the picker offers the ceiling control only
    where this answers ``True`` -- the ``emits_period_starts`` shape, a
    control the engine would ignore being a control the form does not show --
    and the read door that takes a STATED cadence
    (``_reading.recurrence_spec_with_cadence``) DROPS a stored ceiling where
    the stated unit cannot hold it, which is the arm the edit form's repair
    path meets, stating a calendar-month cadence over a stored paycheck one.
    A stored row cannot meet it on its own unit:
    ``ck_recurrence_rules_max_per_month_unit`` refuses that pair (plan step
    plan#2302).

    Derived from :func:`has_day_of_month_coordinate` rather than from a second
    list of units, which an adversarial review of plan step R7b-1 required of
    that predicate's own source: a cadence measured in whole months fires at
    most once a month by construction, so exactly the units WITHOUT a
    day-of-month coordinate -- paychecks and weeks -- can put a second
    occurrence in a month.  A ceiling on a monthly cadence would be a value
    the walk can never read, which is the closed-set defect this package
    removes rather than stores.

    Args:
        unit: The cadence unit.

    Returns:
        ``True`` for the paycheck and week units.
    """
    return not has_day_of_month_coordinate(unit)


def unit_member(unit_id: int) -> RecurrenceUnitEnum:
    """Return the cadence unit a STORED ``unit_id`` names, or RAISE.

    The read door takes a rule's unit off ``budget.recurrence_rules.unit_id``
    from plan step R7c-b, and the column is ``NOT NULL`` with an
    ``ondelete="RESTRICT"`` FK to ``ref.recurrence_units`` -- so an id no enum
    member names is a ``ref`` row this application does not model, which is a
    broken invariant rather than user input.  It RAISES because a rule read
    with a fabricated cadence is worse than a refused read.

    It was ``pattern_member``'s twin until plan step R7c-c, which dropped
    ``pattern_id`` and deleted that function; this one and
    :func:`placement_member` are what is left of the pair's job.

    Built on :func:`~app.services.recurrence.modelled_unit`, which owns the
    lookup, so the SUBMISSION door (which answers ``None`` and flashes) and this
    one can never disagree about which units the application models.

    Args:
        unit_id: A stored ``ref.recurrence_units`` id.

    Returns:
        The matching :class:`~app.enums.RecurrenceUnitEnum` member.

    Raises:
        RecurrenceResolutionError: When no member names *unit_id*.
    """
    member = modelled_unit(unit_id)
    if member is not None:
        return member
    raise RecurrenceResolutionError(
        f"recurrence unit id {unit_id} matches no RecurrenceUnitEnum member.  "
        f"budget.recurrence_rules.unit_id is NOT NULL with a RESTRICT foreign "
        f"key, so a stored id this application does not model means the ref "
        f"seed and the enum have diverged; deriving a cadence from it would "
        f"generate rows on a rhythm the rule never named."
    )


def placement_member(placement_id: int) -> PeriodPlacementEnum:
    """Return the placement a STORED ``placement_id`` names, or RAISE.

    :func:`unit_member`'s twin on the second authored axis; see it for why a
    stored id is raised on rather than answered.

    Args:
        placement_id: A stored ``ref.period_placements`` id.

    Returns:
        The matching :class:`~app.enums.PeriodPlacementEnum` member.

    Raises:
        RecurrenceResolutionError: When no member names *placement_id*.
    """
    member = modelled_placement(placement_id)
    if member is not None:
        return member
    raise RecurrenceResolutionError(
        f"recurrence placement id {placement_id} matches no "
        f"PeriodPlacementEnum member.  budget.recurrence_rules.placement_id is "
        f"NOT NULL with a RESTRICT foreign key, so a stored id this "
        f"application does not model means the ref seed and the enum have "
        f"diverged; it decides WHICH PAYCHECK PAYS a bill, so defaulting it "
        f"would move real money."
    )


def require_positive_interval(interval_n: int, where: str) -> None:
    """Refuse a cadence interval that is not positive.

    Package-internal, and named rather than inlined into its one caller
    (:func:`require_authorable_cadence`) because a SECOND function depends on
    it having run: :func:`canonical_cadence`'s floor division answers ``0`` for
    a non-positive interval, and it says so in its own ``Args``.  A rule two
    functions rely on is one neither may state for itself.

    **The check is on the AUTHORED value, not on a pattern's own**, and the
    difference is a live defect an adversarial review measured.  Every calendar
    pattern's interval is a hard-coded 1, 3 or 6, which can never be
    non-positive -- so checking that looked at nothing, while the write door
    wrote the authored value into a ``NOT NULL`` column carrying
    ``CHECK (interval_n > 0)``.  An authored 0 therefore reached the flush as an
    unhandled ``IntegrityError``.

    Args:
        interval_n: The AUTHORED interval.
        where: What to name in the refusal -- the pattern id a stored rule
            carries, or the ``(unit, placement)`` an authoring request states.
            The caller composes it because only the caller knows which
            vocabulary the offending value arrived in.

    Raises:
        RecurrenceResolutionError: When *interval_n* is not positive.  Mirrors
            ``ck_recurrence_rules_positive_interval``, refused here so the
            caller sees the offending value rather than an ``IntegrityError``
            at flush, and so the phase modulo cannot divide by zero.
    """
    if interval_n < 1:
        raise RecurrenceResolutionError(
            f"recurrence interval_n must be positive, got {interval_n} for "
            f"{where}.  It is written to a NOT NULL column with "
            f"CHECK (interval_n > 0), so letting it through would raise an "
            f"unhandled IntegrityError at the flush instead of here."
        )


def canonical_cadence(
    interval_n: int,
    unit: RecurrenceUnitEnum,
    max_per_month: int | None = None,
) -> Cadence:
    """Return the ONE spelling of the cadence *interval_n* *unit* names.

    **Ruling R-R17, applied at the write door** (plan step R7c-c).  Freeing the
    interval makes ``(12, MONTH)`` authorable, and it is the same rhythm as
    ``(1, YEAR)``: the occurrence walk strides twelve months either way
    (:func:`~app.services.recurrence._months.months_per_step`), both have the
    same day-of-month coordinate, and both fire once a year.  Two spellings of
    one rhythm
    is the second vocabulary this arc removed from the table, arriving back
    through the form -- the Recurring surface would word the same annual bill
    "Every 12 months" on one row and "Yearly" on another, and the obligations
    filter would group them apart.

    **The PLACEMENT guard LEFT at plan step R8-a, because what it guarded
    against stopped existing.**  It caught one case:
    ``(12, MONTH, PERIOD_STARTING_ON_OR_AFTER)`` was authorable while its YEAR
    spelling was not, so rewriting it would have turned a storable cadence into
    a refusal at the door about to store it.  That asymmetry was the
    anchor-family router refusing a year-scale deferred cadence on a derivation
    R7c-b had already deleted; R8-a admits the pair, and MONTH and YEAR are now
    authorable on exactly the same placements BY DERIVATION rather than by
    coincidence -- both have a day-of-month coordinate, neither emits period
    starts, so :func:`authorable_cadences`' two rules answer identically for
    them at every placement.  Re-checking it here would be a fence over a state
    the offer set cannot produce; ``test_recurrence_frequency`` proves the
    property over every placement instead.

    The stride is read through ``months_per_step`` rather than compared against
    a literal twelve, so this function states no month arithmetic of its own --
    the same rule ``_occurrence`` follows for the walk it seeds.

    Args:
        interval_n: The authored interval.  Assumed positive; the write door
            has already run :func:`require_positive_interval`, and this
            function's floor division would otherwise answer ``0``.
        unit: The authored cadence unit.
        max_per_month: The authored per-month ceiling, carried through
            unchanged (plan step salary:R15-a).  The one rewrite this function
            makes is months-to-years, and a ceiling cannot sit on either of
            those units (:func:`can_repeat_within_month`), so there is nothing
            for the rewrite to carry: the spec has already refused the pair.

    Returns:
        The canonical :class:`Cadence` -- the caller's own pair for every
        cadence but a whole number of years authored in months.
    """
    stated = Cadence(
        interval_n=interval_n, unit=unit, max_per_month=max_per_month,
    )
    if unit is not RecurrenceUnitEnum.MONTH:
        return stated
    months_per_year = months_per_step(RecurrenceUnitEnum.YEAR, 1)
    if months_per_step(unit, interval_n) % months_per_year != 0:
        return stated
    return Cadence(
        interval_n=interval_n // months_per_year,
        unit=RecurrenceUnitEnum.YEAR,
        max_per_month=max_per_month,
    )


__all__ = [
    "Cadence",
    "CadenceReading",
    "RecurrenceFrequencyError",
    "RecurrenceResolutionError",
    "can_repeat_within_month",
    "canonical_cadence",
    "has_day_of_month_coordinate",
]
# The offer set and the predicates it is derived from live in ``_offer`` since
# plan step salary:R15-a (a pure move; see that module's docstring).
# ``require_positive_interval`` is deliberately ABSENT here: it states a rule
# this package applies to itself, and a consumer outside the package asking it
# would be a second reader to find.
