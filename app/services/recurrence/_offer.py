"""The OFFER SET: which cadences a rule may be authored on, and why.

**The offer-set half of** :mod:`._frequency`, **split out at plan step
salary:R15-a** when the per-month ceiling took that module past pylint's
1000-line ceiling (its 895 lines were 90% of it before the cadence's third
value arrived, so the room the C12-a package split bought the paycheck engine
was never here).  A leaf per concern is the answer this project has given the
same measurement four times -- ``pay_calendar``, ``cash_ledger``,
``transaction_service``, ``paycheck_calculator`` -- and it is a PURE MOVE:
every definition below is byte-for-byte what :mod:`._frequency` held, graded
by AST in the same commit.  The argument for the offer set being DERIVED from
what a rule can DO rather than from a name it can have stays in
:mod:`._frequency`'s own docstring, which this leaf implements.

What lives here: the predicates the offer set is derived from
(:func:`emits_period_starts`, :func:`follows_card_due_dates`,
:func:`fixed_interval`), the day-of-month question asked of a PAIR
(:func:`fires_on_day_of_month`), the offer itself (:class:`AuthorableCadence`,
:func:`authorable_cadences`), the set a definition with no card behind it is
offered (:func:`cadences_without_a_card`) and the two doors that ask it of a
submission (:func:`is_authorable`, :func:`require_authorable_cadence`). A
second predicate, ``has_row_date_coordinate``, and its raising twin
``require_row_date_coordinate`` withheld the ``WEEK`` unit from plan step R8-a
until plan step **R8-b** dated a weekly row from its occurrence
(``_row_day.date_row``) and deleted both.

**The dependency runs ONE way**: this module imports :mod:`._frequency` -- the
unit facts, the interval floor and the error class -- and that module imports
nothing of this one, which is what keeps the split a boundary.

No Flask, no ORM, no clock, and no database read: what a unit's reference row
states comes from the reference cache, loaded once per process (ruling
plan#2748).
"""
from dataclasses import dataclass

from app.enums import (
    PeriodPlacementEnum,
    RecurrenceUnitEnum,
)
from app.services.recurrence._frequency import (
    RecurrenceResolutionError,
    has_day_of_month_coordinate,
    require_positive_interval,
)


def fires_on_day_of_month(
    unit: RecurrenceUnitEnum, placement: PeriodPlacementEnum,
) -> bool:
    """Return whether a generated row is dated from a DAY of the month.

    **The one predicate the three-valued anchor-family router collapsed to at
    plan step R8-a**, and the collapse was measured rather than argued: over
    all eight ``(unit, placement)`` pairs the router's
    ``family == FAMILY_CALENDAR`` projection agreed with the expression below
    on every pair it answered for, and disagreed only on the three it refused
    -- which are the pairs that step re-decided.

    It is NOT "is this unit measured in months", and an adversarial review of
    plan step R7b-2 is why the distinction is stated here.  ``Monthly First``
    is a MONTH-unit cadence whose rows are dated from the PAYCHECK they defer
    onto, so ``scheduling_day_of_month`` answers ``None`` for it (the form hid
    a Due Day input for it until that input left at plan step R5-a) -- while
    its occurrences still land on
    days of the month, which is :func:`has_day_of_month_coordinate`'s question.
    Reaching for the wrong one of the two moved money twice; see that function.

    Args:
        unit: The cadence unit.
        placement: Which pay period funds an occurrence.

    Returns:
        ``True`` when the cadence has a day-of-month coordinate AND the row is
        funded by the paycheck containing the occurrence: the readings whose
        rows are dated ON a day of the month.  A weekly row funded by its
        containing paycheck is dated on its occurrence too (plan step R8-b,
        ``_row_day.date_row``) and names no day of the month, so it answers
        ``False`` here.
    """
    return (
        has_day_of_month_coordinate(unit)
        and placement is PeriodPlacementEnum.CONTAINING_DATE
    )


@dataclass(frozen=True)
class AuthorableCadence:
    """One ``(unit, placement)`` pair a rule may be authored on.

    :func:`authorable_cadences` returns these.

    **It lost its ``interval_n`` field at plan step R7c-c**, and the loss is the
    step: the interval was ``None`` for the one pattern that took it from a
    column and a fixed 1 / 3 / 6 for the rest, so a "storable reading" had to
    carry it.  Every positive interval is storable now, for every unit, so the
    interval is no longer part of what an offer NAMES -- which is also why the
    form's month ``<select>`` becomes a free number box in the same step.

    **The consequence for plan ledger row D32**: a placement was a property of
    the ``(unit, interval)`` PAIR while ``MONTHLY_FIRST`` had no quarterly twin,
    so raising a monthly rule's interval silently rewrote its funding choice.
    With the closed set gone the MONTH unit offers both placements at every
    interval, and the pair dependency is gone with the fusion that created it.

    Attributes:
        unit: The cadence unit.
        placement: Which pay period an occurrence is funded from.
    """

    unit: RecurrenceUnitEnum
    placement: PeriodPlacementEnum


def emits_period_starts(unit: RecurrenceUnitEnum) -> bool:
    """Return whether *unit*'s occurrences ARE pay-period start dates.

    **The one place "the placement is inert" is stated**, and it is what keeps
    :func:`authorable_cadences` from offering a control that changes nothing.
    ``_occurrence._period_walk`` yields a qualifying paycheck's own
    ``start_date``, and both members of
    :class:`~app.enums.PeriodPlacementEnum` carry a period start back to that
    same period -- the one that CONTAINS it, and the first one STARTING on or
    after it, are the same period when the date is a period's own start.  So a
    pay-period cadence has one funding answer however the placement reads, and
    a form offering the choice would be asking the user to decide something the
    engine ignores.

    Every other unit emits a calendar DATE, which can fall strictly inside a
    period, where the two placements genuinely differ.

    Stated as a predicate over the unit rather than derived from the
    day-of-month one, because the two are different questions: this one says
    whether the placement can move a row, and
    :func:`has_day_of_month_coordinate` says whether the cadence names a day.
    They answer differently for three of the five units and agree only on
    ``WEEK`` and ``DUE_DATE``, where BOTH are ``False``: a weekly occurrence
    or a card's due date is a calendar date that can fall strictly inside a
    paycheck, and names no day of the month (a due date's day is the card's).
    Pinned by driving ``place`` over a whole schedule under both placements
    (``test_recurrence_occurrence``), which is a proof over the schedule rather
    than the argument above.

    Args:
        unit: The cadence unit.

    Returns:
        ``True`` for the ``PERIOD`` unit and ``False`` for every other.
    """
    return unit is RecurrenceUnitEnum.PERIOD


def follows_card_due_dates(unit: RecurrenceUnitEnum) -> bool:
    """Return whether *unit*'s occurrences are a credit card's due dates.

    **The one statement of which unit a card's terms date** (plan step
    credit_card:CC-6, ruling plan#2671), asked for its two consequences:

    * **its dates are the card's**, so only a transfer into a card may repeat
      by it, and the walk is handed the card's
      :class:`~app.services.card_statement.DueDateSchedule` by the read pass
      rather than reading a day off the rule;
    * **each occurrence is a DEADLINE**, so the cadence admits one reading of
      the two axes: funded by the paycheck covering the due date (the first
      paycheck on or after it pays late) and every 1 (every other statement
      leaves one unpaid).  :func:`authorable_cadences` and
      :func:`is_authorable` take both from here.

    Args:
        unit: The cadence unit.

    Returns:
        ``True`` for the due-date unit and ``False`` for every other.
    """
    return unit is RecurrenceUnitEnum.DUE_DATE


def fixed_interval(unit: RecurrenceUnitEnum) -> int | None:
    """Return the one interval *unit* may be authored on, or ``None`` for any.

    **The one statement of it** (plan step credit_card:CC-6), read by
    :func:`is_authorable`, which refuses any other, and by the picker's wire,
    which is how the form fixes its interval box without the script knowing
    which unit takes one or what the number is.

    Args:
        unit: The cadence unit.

    Returns:
        ``1`` for a card's due dates (every other statement would leave one
        unpaid, :func:`follows_card_due_dates`); ``None`` for every other
        unit, each of which takes any positive interval.
    """
    return 1 if follows_card_due_dates(unit) else None


def authorable_cadences() -> tuple[AuthorableCadence, ...]:
    """Return every ``(unit, placement)`` pair a rule may be authored on.

    **The producer the form serves its options from**, which is what makes a
    cadence the application cannot honour unofferable rather than fenced behind
    a refusal.  Plan step R7b-2 gave the picker that property by deriving its
    options from the storage ENCODER; R7c-c dropped the encoder and left the
    anchor-family router holding the gate; plan step **R8-a** replaced the
    router, which by then selected between derivations ruling **R-R16** had
    deleted, with two live constraints.

    **One rule is left, derived from a fact stated once elsewhere rather than
    from a list of units kept here**: the placement must be able to CHANGE the
    answer (:func:`emits_period_starts`).  A pay-period cadence's occurrences
    are paydays and both placements carry a payday back to its own paycheck,
    so offering the choice would render a control the engine ignores.

    **What R8-b WIDENED, and it is a whole unit**: weeks, at both placements.
    The other R8-a rule required a cadence's occurrences to be DATABLE onto a
    generated row (``has_row_date_coordinate``), and ``WEEK`` was the one unit
    whose occurrences were neither paydays nor days of the month, so
    ``recurrence.compute_due_date`` would have dated every weekly row on its
    funding payday.  Plan step R8-b dates a row from the cadence's unit and
    placement instead (``_row_day.date_row``): a weekly row funded by the
    paycheck containing it is due on its occurrence, and one funded from a
    later paycheck on that paycheck's payday, exactly as a monthly row is.
    Nothing was left for the rule to withhold, so it was deleted.

    **What R8-a WIDENED, and it is one reading**: a year-scale cadence funded
    from the first paycheck on or after its date.  The router refused it
    because ``_resolution._first_of_month_anchor`` -- deleted at R7c-b -- would
    have anchored it on "the 1st of the first qualifying month", firing it in
    whichever month the owner's schedule happened to open in.  With
    ``starts_on`` authored there is no such derivation and no such month: the
    rule fires on its own date every ``interval_n`` years and defers onto the
    next paycheck, exactly as its MONTH twin already did.  Measured on a
    2026-08-16 production clone: 0 of 46 live rules read differently, and a
    yearly cadence cannot put two occurrences in one paycheck at ANY cadence in
    ``pay_schedule.cadence_days``' whole 1-365 domain.  That mattered while
    the generation index was keyed on the paycheck and a repeat was REFUSED;
    plan step **R17** re-keyed it onto the occurrence, so a repeat now stores
    and the observation is a statement about the cadence rather than about
    exposure.

    **What plan step credit_card:CC-6 WIDENED, and it is one reading**: a
    card's statement due dates, funded by the paycheck covering each
    (:func:`follows_card_due_dates`).  Its deferred placement is withheld for
    a reason of its own rather than :func:`emits_period_starts`': the
    placement does change the answer, to a payment after the due date.

    The INTERVAL is not part of an offer.  Every positive interval is authorable
    for every unit here but the due dates, which take 1 alone
    (:func:`is_authorable`); that is what the form's free number box renders and
    what ``ck_recurrence_rules_positive_interval`` is the whole of the domain
    for.

    Returns:
        One entry per authorable pair, in
        :class:`~app.enums.RecurrenceUnitEnum` declaration order (paycheck,
        week, month, year, due date: the order the picker renders) and within
        a unit in :class:`~app.enums.PeriodPlacementEnum` declaration order.
    """
    offered = []
    for unit in RecurrenceUnitEnum:
        for placement in PeriodPlacementEnum:
            offered.append(AuthorableCadence(unit=unit, placement=placement))
            # The ``break`` offers a unit with an INERT placement its FIRST
            # member, and which member that is depends on
            # :class:`~app.enums.PeriodPlacementEnum`'s declaration order.
            # That dependency is load-bearing rather than incidental: the one
            # offered must be ``CONTAINING_DATE``, because that is what
            # ``RecurrenceSpec.placement`` defaults to and what an edit form
            # preselects for a stored pay-period rule.  Asserted rather than
            # left to the ordering by ``test_recurrence_frequency
            # .test_the_offer_set_is_exactly_these_seven_readings``, which names
            # the pair.
            if emits_period_starts(unit) or follows_card_due_dates(unit):
                break
    return tuple(offered)


def cadences_without_a_card() -> tuple[AuthorableCadence, ...]:
    """Return the offer set for a definition with no card behind it.

    :func:`authorable_cadences` less the card's due dates
    (:func:`follows_card_due_dates`), in that set's order: what a form whose
    definition cannot pay a card offers, the ruling plan#2591 shape of a form
    passing the set its own door refuses against.  Narrowed rather than
    restated, so a placement the package stops offering leaves this set too.

    Returns:
        The offered ``(unit, placement)`` pairs.
    """
    return tuple(
        cadence for cadence in authorable_cadences()
        if not follows_card_due_dates(cadence.unit)
    )


def is_authorable(
    interval_n: int,
    unit: RecurrenceUnitEnum,
    placement: PeriodPlacementEnum,
) -> bool:
    """Return whether this reading can be AUTHORED, without raising.

    The write door's question asked by a validator rather than by the door: the
    door raises because reaching it with an unauthorable cadence is a broken
    invariant, while a SUBMISSION carrying one is bad input to refuse with a
    field error.  Built on :func:`authorable_cadences`, so the validator and
    the form's own offer set cannot disagree about the set.

    Args:
        interval_n: The authored interval.
        unit: The cadence unit.
        placement: Which pay period an occurrence is funded from.

    Returns:
        ``True`` when a rule may be authored on this reading: a positive
        interval, 1 alone for the card's due dates (every other statement
        would leave one unpaid, :func:`follows_card_due_dates`), on a pair
        the offer set holds.
    """
    if interval_n < 1:
        return False
    if fixed_interval(unit) not in (None, interval_n):
        return False
    return AuthorableCadence(unit=unit, placement=placement) in (
        authorable_cadences()
    )


def require_authorable_cadence(
    interval_n: int,
    unit: RecurrenceUnitEnum,
    placement: PeriodPlacementEnum,
    where: str,
) -> None:
    """Refuse a cadence this application cannot author.

    **The write door's completeness refusal, and plan step R7c-c is why it has
    a name of its own.**  ``encode_cadence`` used to be it: a cadence the
    closed pattern set could not NAME had nowhere to be written, so the door
    raised and every caller above it inherited the refusal -- including the
    recurrence PREVIEW, which builds a transient rule through the same door and
    whose whole contract is to show what saving would produce.  Deleting the
    encoder took that refusal with it, and the preview began listing dates for
    a cadence the schema then refused -- measured, five of them for the ``WEEK``
    unit.

    So the refusal is restated where it belongs, over the set the FORM offers
    rather than over the set a storage encoding could name.  It is
    :func:`is_authorable`'s raising twin and reads the same producer, which is
    what keeps the three answers one: the picker offers it, the schema accepts
    it, the door writes it.

    Args:
        interval_n: The authored interval.
        unit: The cadence unit.
        placement: Which pay period funds an occurrence.
        where: What to name in the refusal, composed by the caller because only
            the caller knows which value is being built.

    Raises:
        RecurrenceResolutionError: When *interval_n* is not positive, or when
            the pair is one this application cannot author.
    """
    require_positive_interval(interval_n, where)
    if is_authorable(interval_n, unit, placement):
        return
    if follows_card_due_dates(unit):
        raise RecurrenceResolutionError(
            f"a recurrence of every {interval_n} {unit!r} funded "
            f"{placement!r} is not one this application can author, for "
            f"{where}.  Each card due date is a deadline: it is paid every "
            f"statement (every 1) from the paycheck covering it, and any "
            f"other reading pays a statement late or not at all.  The offer "
            f"set is ``authorable_cadences``; nothing the picker renders "
            f"reaches here."
        )
    raise RecurrenceResolutionError(
        f"a recurrence of every {interval_n} {unit!r} funded {placement!r} is "
        f"not one this application can author, for {where}.  The placement "
        f"is inert for the unit -- a pay-period cadence's occurrences are "
        f"paydays, and both placements carry a payday back to its own "
        f"paycheck -- so offering it would store a choice the edit form "
        f"cannot preselect.  The offer set is ``authorable_cadences``; "
        f"nothing the picker renders reaches here."
    )


__all__ = [
    "AuthorableCadence",
    "authorable_cadences",
    "cadences_without_a_card",
    "emits_period_starts",
    "fires_on_day_of_month",
    "fixed_interval",
    "follows_card_due_dates",
    "is_authorable",
    "require_authorable_cadence",
]
