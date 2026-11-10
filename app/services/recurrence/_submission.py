"""What one recurrence form submission STATES, field by field (plan step R7e).

A recurrence field has three meanings and an HTML form has two wire states for
it.  A field the form STATED carries a value; a field it CLEARED carries the
empty state its type admits (``None`` for "Does not repeat" and for a cleared
per-month ceiling); a field it did not MENTION -- a control the form rendered
disabled, an amount-only PATCH, a crafted POST -- carries :data:`UNSET`.  The
recurrence form schema composes one :class:`RecurrenceSubmission` from its
payload, and every door that reads a recurrence submission reads this value.

**It replaces a presence read at each route site** (finding **D36**): until
this step each door asked ``KEY in data`` for itself -- the update writer for
the start and the ceiling, the dispatcher for the unit, the loan refusal for
the start and the reading, the inverted-window refusal for the start, the loan
destination's settles and the payroll line's settle for the unit, the start,
the ceiling and the bound -- so "cleared" and "not mentioned" were told apart
by whichever site remembered to ask, and a site that forgot erased what it
should have kept (an amount-only PATCH once erased a stored due day this way).
A value with no key to test cannot be read that way.

**The rule "the submitted value where the form stated one, the stored one
where it did not" is stated ONCE, in :meth:`RecurrenceSubmission.applied_to`**,
which the update writer and the inverted-window refusal both read: until this
step each spelled it inline, and two spellings of one rule that agree today are
still two (``CLAUDE.md`` rule 14).
"""
from dataclasses import dataclass, replace
from datetime import date
from enum import Enum
from typing import TypeVar

from app.enums import PeriodPlacementEnum, RecurrenceUnitEnum
from app.models.recurrence_rule import RecurrenceRule
from app.services.recurrence._bounds import NEVER_ENDS, EndBound
from app.services.recurrence._picker import MonthReading
from app.services.recurrence._reading import recurrence_spec_with_cadence
from app.services.recurrence._resolution import RecurrenceSpec


class Unset(Enum):
    """The state of a recurrence field the submission did not mention.

    A one-member enum rather than a bare ``object()`` so a type hint can name
    it (``date | Unset``) and a reader sees the state spelled the same way
    everywhere.
    """

    UNSET = "unset"


#: The one value of :class:`Unset`: "the form said nothing about this field",
#: which a door reads as "leave the stored value alone".
UNSET = Unset.UNSET

_T = TypeVar("_T")


def _over(stated: "_T | Unset", stored: _T) -> _T:
    """Return *stated*, or *stored* where the submission did not mention it.

    The one spelling of the per-field rule every method below applies.

    Args:
        stated: What the submission says about one field.
        stored: What the stored rule (or the create default) holds for it.

    Returns:
        The value the field takes once the submission is applied.
    """
    return stored if stated is UNSET else stated


@dataclass(frozen=True)
class SubmittedCadence:
    """How often the submission says the definition repeats, as ONE value.

    The three axes travel together because the schema refuses half of them --
    a unit with no placement or no interval is a field error
    (``validate_authorable_cadence``) -- so a value holding two of the three is
    a state no submission can reach, and the type does not offer one.

    **The per-month ceiling is nested here**, as the one axis that keeps a
    third state: its control is DISABLED beside a unit that cannot repeat
    within a month, so an absent ceiling means "the form could not show it" and
    leaves the stored one alone, while a cleared box clears it.  A ceiling
    stated beside no cadence is applied nowhere (a submission that names no
    cadence authors nothing), so it has no field outside this one.

    Attributes:
        unit: The cadence unit.
        interval_n: How many units pass between occurrences.
        placement: Which pay period funds an occurrence.
        max_per_month: The per-month ceiling stated (an ``int``), cleared
            (``None``), or not mentioned (:data:`UNSET`).
    """

    unit: RecurrenceUnitEnum
    interval_n: int
    placement: PeriodPlacementEnum
    max_per_month: int | None | Unset = UNSET

    def ceiling_over(self, stored: int | None) -> int | None:
        """Return the ceiling the definition carries once this is applied.

        Args:
            stored: The stored rule's ceiling, or ``None`` where there is no
                stored rule.

        Returns:
            The stated ceiling (``None`` for a cleared box), or *stored* where
            the form did not mention it.
        """
        return _over(self.max_per_month, stored)


@dataclass(frozen=True)
class RecurrenceSubmission:
    """What one recurrence form submission states, each field in one of its states.

    Attributes:
        cadence: The :class:`SubmittedCadence` the form names; ``None`` for
            "Does not repeat", which CLEARS the recurrence; or :data:`UNSET`
            when the form did not mention the cadence, which leaves the stored
            rule alone and its other fields unapplied.
        starts_on: The rule's FIRST OCCURRENCE as stated, or :data:`UNSET`.
            There is no cleared state: the column is ``NOT NULL``, and an empty
            "Starts on" box reaches the schema as no key at all, so on an
            update it leaves the stored date alone exactly as a locked control
            does (and a create refuses it).
        reading: How the start is read (the "repeating on" control), or
            :data:`UNSET`.  **It rides on** ``starts_on``: the two are one
            statement of when the rule fires and the control is rendered only
            beside the date, so a reading is applied only with a stated start,
            and a start stated without one is read by its own day of the month.
        end_bound: When the recurrence stops, as the ONE value the "Ends"
            controls composed, or :data:`UNSET`.  ``NEVER_ENDS`` is a stated
            bound, not the absence of one.
    """

    cadence: SubmittedCadence | None | Unset = UNSET
    starts_on: date | Unset = UNSET
    reading: MonthReading | Unset = UNSET
    end_bound: EndBound | Unset = UNSET

    @property
    def mentions_the_cadence(self) -> bool:
        """Whether the form said anything about the cadence at all.

        ``False`` is a partial update -- an amount-only PATCH, a page that
        posts no cadence controls -- and leaves the recurrence alone.
        """
        return self.cadence is not UNSET

    @property
    def clears_the_recurrence(self) -> bool:
        """Whether the form said "Does not repeat"."""
        return self.cadence is None

    @property
    def named_cadence(self) -> SubmittedCadence | None:
        """The cadence the form names, or ``None`` when it names none.

        ``None`` covers both "Does not repeat" and "not mentioned"; the two
        predicates above tell them apart where a door must.
        """
        return self.cadence if isinstance(self.cadence, SubmittedCadence) else None

    @property
    def states_a_bound(self) -> bool:
        """Whether the form stated either validity bound.

        The opening bound is the date AND how it is read: a "repeating on"
        choice states the start as surely as the date does (plan step
        recurrence:R8-c), so a reading posted with no date counts here even
        though :meth:`applied_to` applies none.  This is the set a definition
        whose bounds the app derives (a loan's standing payment) may not state.
        """
        return (
            self.starts_on is not UNSET
            or self.reading is not UNSET
            or self.end_bound is not UNSET
        )

    def starts_on_over(self, stored: date | None) -> date | None:
        """Return the first occurrence the definition has once this is applied.

        Args:
            stored: The stored rule's ``starts_on``, or ``None`` where there is
                no stored rule.

        Returns:
            The stated start, or *stored* where the form did not mention it.
        """
        return _over(self.starts_on, stored)

    def end_bound_over(self, stored: EndBound | None) -> EndBound | None:
        """Return the closing bound the definition has once this is applied.

        Args:
            stored: The stored rule's bound, or ``None`` where there is no
                stored rule.

        Returns:
            The stated bound, or *stored* where the form did not mention it.
        """
        return _over(self.end_bound, stored)

    def applied_to(self, rule: RecurrenceRule) -> RecurrenceSpec:
        """Return the spec *rule* authors once this submission is applied to it.

        The update door's whole merge, uniform over the fields: each one the
        form STATED replaces the stored value, and each one it did not mention
        rides through.  Nothing the form does not collect is reset to a
        default, which is what closed defect **D1** (an amount-only edit
        re-phasing a cadence).

        **Read with the STATED cadence, never the stored one**
        (:func:`~app.services.recurrence.recurrence_spec_with_cadence`): an
        edit page may be showing a rule whose stored unit or placement the
        application no longer models, the form tells the user to choose a
        cadence before saving, and reading that rule's cadence on the way to
        replacing it raises -- the one repair the surface advertises would be a
        500.  The same read DROPS a stored nominal day, weekday reading or
        ceiling the stated unit cannot hold.

        Args:
            rule: The stored rule the submission edits.

        Returns:
            The :class:`~app.services.recurrence.RecurrenceSpec` to re-author.

        Raises:
            ValueError: The submission names no cadence.  Each caller asks
                only beside a named one (a submission that clears or does not
                mention the cadence re-authors nothing), so this is a caller's
                broken invariant, never a submission.
        """
        cadence = self.named_cadence
        if cadence is None:
            raise ValueError(
                f"a recurrence submission whose cadence is {self.cadence!r} "
                f"cannot be applied to rule {rule.id}: only a submission that "
                f"names a cadence re-authors one."
            )
        current = recurrence_spec_with_cadence(
            rule,
            interval_n=cadence.interval_n,
            unit=cadence.unit,
            placement=cadence.placement,
            max_per_month=cadence.ceiling_over(rule.max_per_month),
        )
        if isinstance(self.starts_on, date):
            # The date and its reading are replaced TOGETHER, so a stored
            # reading of one kind never survives beside a stated date it may
            # not fit -- which ``RecurrenceSpec`` refuses, a 500 from an
            # ordinary edit.
            reading = self._reading_of_the_start()
            current = replace(
                current,
                starts_on=self.starts_on,
                nominal_day=reading.nominal_day,
                weekday_reading=reading.weekday_reading,
            )
        return replace(current, end_bound=self.end_bound_over(current.end_bound))

    def authored(self, user_id: int) -> RecurrenceSpec | None:
        """Return the spec a CREATE authors from this submission.

        A create has no stored rule, so a field the form did not mention takes
        the create default: read by its own day of the month, no per-month
        ceiling, and no end (the only place "not mentioned" and "never" are
        the same request, because there is no stored bound to leave alone).

        **An unmentioned start reaches the spec as ``None``**, and
        :class:`~app.services.recurrence.RecurrenceSpec` refuses it naming the
        field.  Every create door guarantees one -- the template schema
        requires it beside a cadence, and the transfer and payroll-line doors
        derive or require it before this runs -- so this is a broken invariant
        rather than a submission, and the spec's own refusal is its honest
        disposition.

        Args:
            user_id: The owner of the rule the spec authors.

        Returns:
            The spec, or ``None`` when the submission names no cadence: the
            definition does not repeat.
        """
        cadence = self.named_cadence
        if cadence is None:
            return None
        reading = self._reading_of_the_start()
        return RecurrenceSpec(
            user_id=user_id,
            unit=cadence.unit,
            starts_on=self.starts_on_over(None),
            interval_n=cadence.interval_n,
            placement=cadence.placement,
            nominal_day=reading.nominal_day,
            weekday_reading=reading.weekday_reading,
            max_per_month=cadence.ceiling_over(None),
            end_bound=self.end_bound_over(NEVER_ENDS),
        )

    def _reading_of_the_start(self) -> MonthReading:
        """Return how a stated start is read: the stated reading, or its own day.

        Returns:
            The :class:`~app.services.recurrence.MonthReading`.
        """
        return _over(self.reading, MonthReading())


__all__ = [
    "UNSET",
    "RecurrenceSubmission",
    "SubmittedCadence",
    "Unset",
]
