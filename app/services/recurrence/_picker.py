"""What the recurrence form OFFERS, and what each option is called (R7b-2).

The picker's vocabulary, replacing the closed-set pattern list that
``_vocabulary`` held until plan step R7b-2.  A form no longer chooses a NAME
for a cadence ("Quarterly"); it authors the two axes directly -- how often
(``interval_n`` and ``unit``) and which pay period funds an occurrence
(``placement``) -- and this module is the one producer of which of those
readings may be offered and how each reads to a human.

**The offer set is DERIVED from the producer that would REFUSE the cadence**,
through :func:`~app.services.recurrence._offer.authorable_cadences`, and
that is the whole point.  While the picker iterated ``RecurrencePatternEnum``
and the write door read a different table, "nothing offers an uncreatable
cadence" held only because the two sets happened to coincide -- a coincidence no
gate protected.  Serving the options from the refusing producer makes that
refusal UNREACHABLE through the form rather than fenced behind it (developer
ruling 2026-08-12).

**What that producer IS moved at plan step R7c-c**, and the offer set widened
with it.  The binding constraint used to be storage: ``budget.recurrence_rules``
named its cadence with a closed pattern set, so ``(2, MONTH)`` walked correctly
and had nowhere to be written.  With ``unit_id`` and ``interval_n`` authored
columns every reading can be stored, and what is left is whether the
application can HONOUR it -- which since plan step R8-a is
:func:`~app.services.recurrence._offer.authorable_cadences`' derived rules,
replacing the ``anchor_family`` router that gated it on first-occurrence
derivations ruling **R-R16** had already deleted.  R8-a stated two; plan step
R8-b deleted the one that withheld the ``WEEK`` unit, by dating a weekly row
from its occurrence.  Three consequences here:

* **the interval is a free number box for every unit.**  ``fixed_intervals``
  and ``free_unit_ids`` are deleted with the ``<select>`` they fed, and
  :class:`CadenceWire` no longer carries an ``interval_n`` at all -- an offer
  names a ``(unit, placement)`` pair and every positive interval is authorable
  on it;
* **a placement stopped being a property of the ``(unit, interval)`` PAIR.**
  ``MONTHLY_FIRST`` had no quarterly twin, so "MONTH allows either placement"
  was true at interval 1 and false at 3 and 6; that dependency was the closed
  set's, and it is gone.  The MONTH unit offers both placements at every
  interval, which is plan ledger row **D32**'s measured defect ceasing to
  exist rather than being warned about;
* **the "Funded from" row is always RENDERED** (developer ruling 2026-08-16,
  the rest of D32).  ONE cadence still admits one placement -- the ``PERIOD``
  unit, where it is inert
  (:func:`~app.services.recurrence._offer.emits_period_starts`) -- and
  hiding the row for it is what let a funding rule change with nothing on
  screen saying so.  It was TWO until plan step **R8-a**, which admitted the
  ``YEAR`` unit's deferring reading; the row's two help sentences are unchanged
  and only the set each applies to moved.

**A flat list of pairs, not a nested tree**, and the shape is a DRY decision
rather than a convenience.  The form needs two linked answers -- which units,
and which placements for a unit -- and emitting them as a nested structure would
state the grouping rule a second time, in a producer that could drift from the
set it groups.  A consumer filters the pairs instead, which cannot mean anything
the offer set does not.

Pure: no Flask, no ORM, no clock.  The ``ref`` ids come from
:mod:`app.ref_cache`, the project's IDs-for-logic seam.
"""
import json
from collections.abc import Callable, Iterable
from dataclasses import asdict, dataclass
from datetime import date

from app import ref_cache
from app.enums import PeriodPlacementEnum, RecurrenceUnitEnum
from app.services.recurrence._bounds import (
    END_BOUND_KINDS,
    EndsAfterOccurrences,
    EndsOnDate,
    NeverEnds,
)
from app.services.recurrence._describe import weekday_words
from app.services.recurrence._frequency import (
    CadenceReading,
    can_repeat_within_month,
    has_day_of_month_coordinate,
)
from app.services.recurrence._month_coordinate import (
    WEEKDAY_READING_WINDOWS,
    WeekdayReading,
    offerable_weekday_readings,
    week_of_month,
    weekday_reading_of,
    weekday_shape,
)
from app.services.recurrence._offer import AuthorableCadence
from app.utils.dates import DAYS_PER_WEEK

#: What each cadence UNIT is called on the form, singular and plural.
#:
#: Two forms rather than one because the control reads "Repeat every N <unit>"
#: and the count drives the noun: "every 1 months" is what a single label
#: produces.  Both are carried on the option so the template and its script
#: DISPLAY and never compute -- the rule :class:`CadenceOption` states.
#:
#: Keyed by enum member, so a unit added to
#: :class:`~app.enums.RecurrenceUnitEnum` without copy raises ``KeyError`` at
#: the first render rather than shipping a blank option.  ``WEEK`` joined at
#: plan step R8-b, the leaf that made the unit authorable by dating a weekly
#: row from its occurrence; until then the offer set withheld it and this map
#: was never asked for it.
_UNIT_LABELS: dict[RecurrenceUnitEnum, tuple[str, str]] = {
    RecurrenceUnitEnum.PERIOD: ("paycheck", "paychecks"),
    RecurrenceUnitEnum.WEEK: ("week", "weeks"),
    RecurrenceUnitEnum.MONTH: ("month", "months"),
    RecurrenceUnitEnum.YEAR: ("year", "years"),
}

#: What each PLACEMENT is called on the form.
#:
#: Worded generically over the occurrence DATE rather than per unit, because the
#: placement is the same rule whatever the cadence counts: the old picker's
#: "Monthly (first paycheck of month)" fused a unit, an interval, an anchor day
#: and this choice into one name, which is why "every other month, funded from
#: the first paycheck" had nowhere to live.
_PLACEMENT_LABELS: dict[PeriodPlacementEnum, str] = {
    PeriodPlacementEnum.CONTAINING_DATE: "The paycheck that covers the date",
    PeriodPlacementEnum.PERIOD_STARTING_ON_OR_AFTER: (
        "The first paycheck starting on or after the date"
    ),
}


@dataclass(frozen=True)
class CadenceWire:
    """One storable cadence, as the SCRIPT reads it.

    **Plan ledger row D31**, closed at plan step R7c-b.  The offer set has two
    consumers that read disjoint halves of it: ``recurrence_form.js`` filters
    on ids and facts, the templates render labels.  One flat value served both
    and was serialised whole into :attr:`PickerModel.options_json`, so every
    render shipped three labels to a browser that discards them -- and the
    ``too-many-instance-attributes`` disable :class:`CadenceOption` carried was
    spent on holding the union together.  Splitting along the consumer line is
    what an adversarial review of plan step R7b-2 measured as available; it
    waited for this step because it changes the wire shape the script parses
    and that needs a browser pass.

    Attributes:
        unit_id: The ``ref.recurrence_units`` id the form posts.
        placement_id: The ``ref.period_placements`` id the form posts.
        has_day_of_month_coordinate: Whether occurrences land on a day of the
            month at all, which is what decides whether the "repeating on"
            control has anything to ask.  **Not the same fact as
            :func:`~app.services.recurrence._offer.fires_on_day_of_month`'s**
            (whether a ROW is dated from that day, keyed on the ``(unit,
            placement)`` pair), and shipping only that one was a wrong-money
            defect plan step R7c-b introduced: this one is ``True`` for
            ``Monthly First`` where that one is ``False``, so
            the script cleared and disabled a control the SERVER had rendered
            enabled -- and the update door reads ``nominal_day`` off the same
            presence key as ``starts_on``, so changing only "Funded from" on a
            "last day of every month" rent wrote ``nominal_day = NULL`` and
            moved every later occurrence to the 30th forever.  Answered by
            :func:`~app.services.recurrence.has_day_of_month_coordinate`, the
            same function ``offerable_nominal_days`` is built on, so the
            control the browser shows and the set the server offers cannot
            disagree.
        can_repeat_within_month: Whether the unit can put two occurrences in
            one calendar month, which is what decides whether the "at most N a
            month" control has anything to ask (plan step salary:R15-a).
            Answered by
            :func:`~app.services.recurrence.can_repeat_within_month`, the same
            predicate :class:`~app.services.recurrence.RecurrenceSpec` refuses
            a ceiling against, so the control the browser shows and the pair
            the door admits cannot disagree -- the ``emits_period_starts``
            shape: a control the engine would ignore is one the form does
            not render.
    """

    unit_id: int
    placement_id: int
    has_day_of_month_coordinate: bool
    can_repeat_within_month: bool


@dataclass(frozen=True)
class CadenceOption:
    """One storable cadence, identified and worded for the form.

    A plain value so the form templates DISPLAY and never compute: every id
    goes in an ``<option value>`` and every label between the tags, with no
    lookup, no fallback expression and no ``name`` string in the template at
    all.  The ids it renders live on :attr:`wire`, which is also exactly what
    the script is sent -- so the two consumers read ONE statement of the offer
    set rather than two copies that agree.

    **``months_per_unit`` left at plan step R7c-b**, with the Month control it
    fed.  It said how many calendar months one unit spans, which decided
    whether a cycle SKIPS months and therefore whether a "Month" select
    narrowed anything; ruling R-R16 put the cycle's month on ``starts_on``, so
    there is no such control and no consumer for the fact.

    **``schedules_on_day_of_month`` left at plan step recurrence:R5-a** the
    same way, with the Due Day of Month control it decided (ruling R-R96,
    developer 2026-09-23): whether a ROW is dated from a day of the month, a
    fact nothing on the form reads once that control is gone.

    Attributes:
        wire: The ids and facts both the template and the script read.
        unit_label_one: What the unit is called after a ``1``.
        unit_label_many: What it is called after any other count.
        placement_label: The human copy for the placement option.
    """

    wire: CadenceWire
    unit_label_one: str
    unit_label_many: str
    placement_label: str

    @property
    def unit_id(self) -> int:
        """Return the ``ref.recurrence_units`` id this option posts."""
        return self.wire.unit_id

    @property
    def placement_id(self) -> int:
        """Return the ``ref.period_placements`` id this option posts."""
        return self.wire.placement_id


def cadence_options(
    cadences: tuple[AuthorableCadence, ...],
) -> tuple[CadenceOption, ...]:
    """Return every cadence a form may offer, worded.

    The single producer of the recurrence picker's options, for both template
    kinds and both the create and edit forms, and for the salary page's
    payroll lines.

    **It takes the offer set rather than reading one** (ruling plan#2591):
    the template forms offer
    :func:`~app.services.recurrence.authorable_cadences`, and the salary page
    offers that set narrowed to the units a payroll line may repeat by
    (``payroll_line_cadence.line_cadences``), because a line's rule picks
    WHICH paychecks take it, so a weekly amount would be taken once a paycheck
    however many weeks the paycheck holds.  Each caller passes
    the set its own door refuses against, so the property above still holds
    per form: nothing a form renders is refused by its door.

    Args:
        cadences: The ``(unit, placement)`` pairs this form offers, in the
            order the form renders them.

    Returns:
        One :class:`CadenceOption` per offered pair, in *cadences*' order.
        Every entry RECURS: "does not repeat" is the form's own empty option,
        not a cadence (plan step R2e-3).

    Raises:
        RuntimeError: If ``ref_cache`` has not been initialized.
        KeyError: If an offered unit or placement has no copy in
            :data:`_UNIT_LABELS` or :data:`_PLACEMENT_LABELS`.
    """
    options = []
    for cadence in cadences:
        label_one, label_many = _UNIT_LABELS[cadence.unit]
        options.append(
            CadenceOption(
                wire=CadenceWire(
                    unit_id=ref_cache.recurrence_unit_id(cadence.unit),
                    placement_id=ref_cache.period_placement_id(
                        cadence.placement,
                    ),
                    has_day_of_month_coordinate=(
                        has_day_of_month_coordinate(cadence.unit)
                    ),
                    can_repeat_within_month=(
                        can_repeat_within_month(cadence.unit)
                    ),
                ),
                unit_label_one=label_one,
                unit_label_many=label_many,
                placement_label=_PLACEMENT_LABELS[cadence.placement],
            )
        )
    return tuple(options)


def _first_by(
    options: Iterable["CadenceOption"],
    key: Callable[["CadenceOption"], object],
) -> tuple["CadenceOption", ...]:
    """Return the first option for each distinct *key*, in offer order.

    The ONE deduplication rule the two cadence ``<select>``\\ s share, differing
    only in what they are keyed on.  Written once here rather than twice in
    Jinja: the template's contract is to DISPLAY (see :class:`CadenceOption`), a
    projection is a computation, and a control that took the un-projected list
    shipped a visible defect -- the interval ``<select>`` plan step R7c-c
    deleted rendered "1 month" twice, with ``selected`` on BOTH, because
    ``MONTHLY`` and ``MONTHLY_FIRST`` were two offers over one ``(1, MONTH)``
    interval.  The same shape is why the UNIT select needs this: the MONTH unit
    still has two offers and must render one entry.

    Args:
        options: The offer set.
        key: What makes two options the SAME entry on one control.

    Returns:
        tuple[CadenceOption, ...]: One option per distinct key, in the order
        the keys first appear.
    """
    seen = set()
    chosen = []
    for option in options:
        marker = key(option)
        if marker in seen:
            continue
        seen.add(marker)
        chosen.append(option)
    return tuple(chosen)


#: What each closing-bound SHAPE is called on the form, and which control it
#: needs a value from, keyed by the token its ``<option>`` posts.
#:
#: Keyed by token rather than by class so this table and
#: :data:`~app.services.recurrence._bounds.END_BOUND_KINDS` state one thing
#: each -- the shapes are the bounds module's, the copy is this module's -- and
#: a shape added there without copy here raises ``KeyError`` at the first
#: render rather than shipping a blank option.  Exactly the contract
#: :data:`_UNIT_LABELS` holds for the cadence units.
#:
#: The second entry is a DOM element id, which is more than copy, and it is
#: here rather than in the template for the reason every other projection on
#: :class:`PickerModel` is: "which input does this shape need" is a fact about
#: the shape, and a template that decided it would be a second statement of the
#: shape set -- keyed on a token STRING, which is the comparison this project
#: rules out everywhere else.  ``None`` for the shape that needs no value.
_END_BOUND_COPY: dict[str, tuple[str, str | None]] = {
    NeverEnds.token: ("Never", None),
    EndsOnDate.token: ("On a date", "field-end-date"),
    EndsAfterOccurrences.token: (
        "After a number of times", "field-max-occurrences",
    ),
}


#: What each NOMINAL DAY is called on the form.
#:
#: The days a month can fail to hold, and no others: every month holds its
#: first 28, so 29-31 is the whole domain -- the same one
#: ``ck_recurrence_rules_nominal_day`` bounds the column to and
#: :func:`~app.services.recurrence.offerable_nominal_days` selects from.
#:
#: **31 is worded as the LAST DAY rather than as a number**, and that is what
#: the value means rather than a friendlier way of saying it: the occurrence
#: walk clamps the day into each month
#: (:func:`~app.services.recurrence._months.clamped_day`), so a day-31 rule
#: fires on the 31st in January and the 30th in April.  "The 31st" would be a
#: label the cadence contradicts eight months a year.
_NOMINAL_DAY_LABELS: dict[int, str] = {
    29: "the 29th",
    30: "the 30th",
    31: "the last day of the month",
}


@dataclass(frozen=True)
class NominalDayOption:
    """One day a clamped first occurrence could have MEANT, worded.

    Half of the offer set behind the form's "repeating on" control; the
    weekday readings are the other (:class:`WeekdayReadingOption`).  An
    option is ENABLED only where the chosen date leaves the question open --
    see :func:`~app.services.recurrence.offerable_nominal_days` for when that
    is.  A plain value so the template DISPLAYS: the token goes in the
    ``<option value>`` and the label between the tags.

    Attributes:
        day: The nominal day, 29-31.
        label: The human copy.
    """

    day: int
    label: str

    @property
    def token(self) -> str:
        """Return what the option posts: the day's digits, ``"31"``.

        :attr:`MonthReading.token`'s spelling, so the option and a stored
        rule's preselection name one string.
        """
        return MonthReading(nominal_day=self.day).token


def nominal_day_options() -> tuple[NominalDayOption, ...]:
    """Return every nominal day the form may offer, worded, ascending.

    The WHOLE domain rather than the subset a particular date leaves open:
    which of them apply is a property of the chosen date, so the control
    renders them all and enables the ones
    :func:`~app.services.recurrence.offerable_nominal_days` names.  That is
    what lets the script re-enable them as the user edits the date without
    holding any copy of its own.

    Returns:
        One :class:`NominalDayOption` per day, ascending.
    """
    return tuple(
        NominalDayOption(day=day, label=label)
        for day, label in sorted(_NOMINAL_DAY_LABELS.items())
    )


@dataclass(frozen=True)
class WeekdayReadingOption:
    """One weekday reading the "repeating on" control offers, worded for every date.

    The weekday half of the control the nominal days share (plan step
    recurrence:R8-c, rulings plan#2668 and plan#2670): ONE ``<select>``
    offers the same day of the month, the clamped month-end days, and these,
    because a rule's first date is read exactly one of those ways and a form
    that posted two keys could state two.

    **Worded per date by the SERVER, and the script picks the words.**
    "The third Tuesday" depends on the date the user is still typing, so the
    option carries every phrase it can show, keyed by where a date sits, and
    the window that says which dates it reads.  ``recurrence_form.js`` counts
    the typed date's week from the window's end and shows the phrase at that
    key; it states no copy and no rule (the template's rule, every other
    string on this form included).

    Attributes:
        token: What the option posts, the reading's stored value.
        counts_from_end: The window's end
            (:class:`~app.services.recurrence._month_coordinate.WeekdayReadingWindow`).
        last_week: The latest week, counted from that end, this reading reads.
        labels_json: Every phrase the option can show, as a JSON object keyed
            ``"<week>-<weekday>"`` -- the date's week counted from that end
            and its :meth:`datetime.date.weekday` index (0 = Monday) --
            ``{"3-1": "the third Tuesday", ...}``.
    """

    token: str
    counts_from_end: bool
    last_week: int
    labels_json: str


def _weekday_label(reading: WeekdayReading, week: int, weekday: int) -> str:
    """Return what the control calls *reading* for a date at *week* on *weekday*.

    The shape's own words (:func:`~._describe.weekday_words`, the describer's
    too) behind an article, so "repeating on the third Tuesday" and
    "Monthly (third Tuesday)" cannot name two days.

    Args:
        reading: The weekday reading.
        week: The date's week counted from *reading*'s end.
        weekday: The date's weekday index, 0 = Monday.

    Returns:
        The label, ``"the third Tuesday"``.
    """
    return f"the {weekday_words(weekday_shape(reading, week, weekday))}"


def weekday_reading_options() -> tuple[WeekdayReadingOption, ...]:
    """Return every weekday reading the form may offer, worded for every date.

    Derived from
    :data:`~app.services.recurrence._month_coordinate.WEEKDAY_READING_WINDOWS`
    -- the table :func:`~app.services.recurrence.offerable_weekday_readings`
    reads -- so a reading is offerable and worded together or neither, and
    the script is handed the same window the write door refuses against.

    Returns:
        One option per reading, from-start first.
    """
    return tuple(
        WeekdayReadingOption(
            token=MonthReading(weekday_reading=reading).token,
            counts_from_end=window.counts_from_end,
            last_week=window.last_week,
            labels_json=json.dumps({
                f"{week}-{weekday}": _weekday_label(reading, week, weekday)
                for week in range(1, window.last_week + 1)
                for weekday in range(DAYS_PER_WEEK)
            }),
        )
        for reading, window in WEEKDAY_READING_WINDOWS.items()
    )


def weekday_reading_choices(
    unit: RecurrenceUnitEnum, starts_on: date,
) -> dict[WeekdayReading, str]:
    """Return the weekday readings *starts_on* allows, each worded for that date.

    The server's half of what the script does as the user types: which
    weekday options a rendered form starts ENABLED, and what each says.  The
    set is :func:`~app.services.recurrence.offerable_weekday_readings`'; the
    words are :func:`_weekday_label`'s, the same ones
    :func:`weekday_reading_options` hands the script.

    Args:
        unit: The cadence unit.
        starts_on: The rule's first occurrence.

    Returns:
        ``{reading: label}`` for each reading the date allows -- ``{}`` for a
        cadence with no month coordinate.
    """
    return {
        reading: _weekday_label(
            reading,
            week_of_month(
                starts_on,
                counts_from_end=WEEKDAY_READING_WINDOWS[reading].counts_from_end,
            ),
            starts_on.weekday(),
        )
        for reading in offerable_weekday_readings(unit, starts_on)
    }


@dataclass(frozen=True)
class MonthReading:
    """How a submitted "repeating on" choice reads the first date.

    What one posted token means: at most one of the two fields is set,
    because the control posts one choice -- which is what keeps a nominal
    day beside a weekday reading (``ck_recurrence_rules_one_month_reading``)
    a state no form can post.  Both ``None`` is the same day of the month.

    Attributes:
        nominal_day: The clamped month-end day the choice names, or ``None``.
        weekday_reading: The weekday reading it names, or ``None``.
    """

    nominal_day: int | None = None
    weekday_reading: WeekdayReading | None = None

    def __post_init__(self) -> None:
        """Refuse a reading that reads the date two ways.

        Raises:
            ValueError: When both fields are set.
        """
        if self.nominal_day is None or self.weekday_reading is None:
            return
        raise ValueError(
            f"a month reading names nominal day {self.nominal_day} AND weekday "
            f"reading {self.weekday_reading.value!r}: one date is read one way "
            f"(the exclusive arc, ruling R-R25), so the pair disagrees with "
            f"itself."
        )

    @property
    def token(self) -> str:
        """Return what the "repeating on" option for this reading posts.

        The ONE spelling of the control's wire values, which every option's
        ``token`` and :func:`month_reading_from_token` read: the empty string
        for the same day of the month, the day's digits, or the weekday
        reading's stored value.
        """
        if self.weekday_reading is not None:
            return self.weekday_reading.value
        if self.nominal_day is not None:
            return str(self.nominal_day)
        return ""


def stored_month_reading(
    nominal_day: int | None, weekday_reading: str | None,
) -> MonthReading:
    """Return how a STORED rule reads its first date, from its two columns.

    What an edit form preselects, read off the columns the write door wrote
    (``budget.recurrence_rules.nominal_day`` and ``weekday_reading``, under
    ``ck_recurrence_rules_one_month_reading``).

    Args:
        nominal_day: The rule's ``nominal_day``.
        weekday_reading: The rule's ``weekday_reading`` column value.

    Returns:
        The :class:`MonthReading`.
    """
    return MonthReading(
        nominal_day=nominal_day,
        weekday_reading=weekday_reading_of(weekday_reading),
    )


def month_reading_from_token(token: str) -> MonthReading | None:
    """Return what a "repeating on" token posts, or ``None`` for a token no option posts.

    **The ONE reader of the control's wire value**, asked by the form schema
    and the live preview, so the two read one submission one way -- the
    shape :func:`~app.services.recurrence.end_bound_from_token` gives the
    "Ends" control.  It accepts exactly the tokens the options post: the
    empty string (the same day of the month), a nominal day's
    :attr:`NominalDayOption.token` and a weekday reading's
    :attr:`WeekdayReadingOption.token`.  A plain integer parse would also
    read ``"031"`` or ``" 31"``, spellings no option posts.

    Whether the date ALLOWS the choice is not asked here: that is a rule
    over the pair, which the schema and ``RecurrenceSpec`` refuse.

    Args:
        token: The submitted value.

    Returns:
        The :class:`MonthReading`, or ``None`` when no option posts *token*.
    """
    offered = (
        MonthReading(),
        *(MonthReading(nominal_day=option.day) for option in nominal_day_options()),
        *(MonthReading(weekday_reading=reading) for reading in WeekdayReading),
    )
    return next(
        (reading for reading in offered if reading.token == token), None,
    )


@dataclass(frozen=True)
class EndBoundOption:
    """One shape a closing bound can take, worded for the form's mode select.

    The bound half of what the recurrence form offers, and the same shape as
    :class:`CadenceOption`: a token for the ``<option value>`` and a label
    between the tags, so the template DISPLAYS and computes nothing.

    Its token is not a ``ref`` id, and that is the difference from every other
    control on this form.  A closing bound's shape is not a stored value --
    which column is non-NULL decides it, ruling R-R13's "absence is the
    discriminator" applied to the bound -- so there is no ``ref`` table to
    carry an id, and inventing one would be the second representation that
    ruling refuses.

    Attributes:
        token: What the ``<option>`` posts, and what
            :func:`~app.services.recurrence.end_bound_from_token` dispatches
            on.
        label: The human copy for the option.
        needs_field_id: The id of the control this shape needs a value from,
            or ``None`` for the shape that needs none.  The script shows that
            one and disables the others, so exactly the input the chosen shape
            reads is the input that submits -- the same idiom the nominal-day
            control uses, and what keeps a stale value from a shape
            the user moved off from reaching the door.
    """

    token: str
    label: str
    needs_field_id: str | None


def end_bound_options() -> tuple[EndBoundOption, ...]:
    """Return every closing-bound shape the form may offer, worded.

    Derived from :data:`~app.services.recurrence._bounds.END_BOUND_KINDS` --
    the same tuple :func:`~app.services.recurrence.end_bound_from_token`
    dispatches over -- so a shape is offerable and submittable together or
    neither.  That is the property plan step R7b-2 gave the cadence controls by
    serving them from the encoder's own table, applied to the bound.

    Returns:
        One :class:`EndBoundOption` per shape, in the order the form offers
        them: never first, because it is the default and the form's first
        entry.

    Raises:
        KeyError: A shape has no copy in :data:`_END_BOUND_COPY`.
    """
    options = []
    for kind in END_BOUND_KINDS:
        label, needs_field_id = _END_BOUND_COPY[kind.token]
        options.append(EndBoundOption(
            token=kind.token, label=label, needs_field_id=needs_field_id,
        ))
    return tuple(options)


@dataclass(frozen=True)
class PickerModel:
    """Everything the recurrence form needs to render its three controls.

    Every projection of ONE :func:`cadence_options` call, bundled because a
    route that took them separately would resolve the same producer twice in
    one request -- the redundancy this project treats as a DRY violation rather
    than a cost question, since two calls are two chances to disagree.

    **Two projections LEFT at plan step R7c-c**, with the control they fed:
    ``fixed_intervals`` (one option per ``(unit, interval)`` whose interval the
    closed set fixed) and ``free_unit_ids`` (the units that took any positive
    interval, which was exactly one).  Every unit takes any positive interval
    now, so the interval ``<select>`` and the free/fixed toggle are both gone
    and the number box is the only interval control.  The value is back under
    the attribute count that needed a suppression, so the suppression went too.

    Attributes:
        options: The whole offer set.  Read by :attr:`options_json`'s own
            construction and by nothing in the templates -- each control
            renders from its projection below -- so it is the value the others
            are derived FROM rather than one a consumer reaches for.
        options_json: The set's WIRE half as JSON, for the script that LINKS
            the controls: the chosen unit decides which placements may show and
            what the day-of-month controls do.  It carries
            :class:`CadenceWire` rather than the whole option since plan step
            R7c-b, because the labels beside it were shipped to a browser that
            discards them (plan ledger row **D31**).
        nominal_days: Every day a clamped first occurrence could have MEANT,
            worded.  The "repeating on" control renders them all and enables
            the ones the chosen date leaves open, so the script re-enables them
            as the user edits that date without holding any copy of the wording.
        weekday_readings: The weekday readings the same control offers, each
            with its window and every phrase it can show (plan step
            recurrence:R8-c), for the same reason.
        units: One option per UNIT, for the unit ``<select>``.  The MONTH unit
            has two offered readings and must render one entry, or the user is
            asked to choose between two things called "months".
        placements: One option per PLACEMENT, for the funding ``<select>``.  The
            script enables the ones the chosen unit admits and explains the row
            rather than hiding it when that is one (plan ledger row **D32**).
        end_bounds: Every shape the "Ends" control offers, worded.  It rides
            on this bundle rather than being resolved separately for the same
            reason the cadence projections do: a route that took them apart
            would resolve two producers for one render.
    """

    options: tuple["CadenceOption", ...]
    options_json: str
    nominal_days: tuple["NominalDayOption", ...]
    weekday_readings: tuple["WeekdayReadingOption", ...]
    end_bounds: tuple["EndBoundOption", ...]
    units: tuple["CadenceOption", ...]
    placements: tuple["CadenceOption", ...]


def picker_model(cadences: tuple[AuthorableCadence, ...]) -> PickerModel:
    """Return the form's options, their projections and their JSON, from one call.

    **Serialized and projected here rather than in each form's route or
    template** so the forms cannot disagree about either shape, and so the set
    the browser filters is the same one the form's door validates against on
    the way back in: :func:`~app.services.recurrence.is_authorable` for the
    template forms, and the line schema's ``picks_paychecks`` refusal beside
    it for the salary page.

    Args:
        cadences: The ``(unit, placement)`` pairs this form offers -- see
            :func:`cadence_options`.

    Returns:
        The :class:`PickerModel`.

    Raises:
        RuntimeError: If ``ref_cache`` has not been initialized.
        KeyError: See :func:`cadence_options`.
    """
    options = cadence_options(cadences)
    return PickerModel(
        options=options,
        options_json=json.dumps(
            [asdict(option.wire) for option in options],
        ),
        nominal_days=nominal_day_options(),
        weekday_readings=weekday_reading_options(),
        end_bounds=end_bound_options(),
        units=_first_by(options, lambda option: option.unit_id),
        placements=_first_by(options, lambda option: option.placement_id),
    )


@dataclass(frozen=True)
class SelectedCadence:
    """Which option each of the three cadence controls starts on.

    An EDIT form's counterpart to :func:`cadence_options`: the options say what
    may be chosen, this says what IS chosen.  Ids rather than enum members for
    the same reason :class:`CadenceOption` carries them -- the template
    compares them to ``<option value>``\\ s and computes nothing.

    Attributes:
        unit_id: The ``ref.recurrence_units`` id to preselect.
        interval_n: The interval to prefill.
        placement_id: The ``ref.period_placements`` id to preselect.
        max_per_month: The per-month ceiling to prefill, or ``None`` for an
            empty box (plan step salary:R15-a) -- the cadence's third value,
            read off the same :class:`~app.services.recurrence.CadenceReading`
            as the other two so the form cannot preselect a cadence and
            forget its ceiling.
        ceiling_applies: Whether the ceiling control starts ENABLED -- the
            preselected unit can put two occurrences in a month
            (:func:`~app.services.recurrence.can_repeat_within_month`).  A
            fact about the unit rather than the value: a paycheck rule with
            no ceiling still renders an empty, enabled box, and a monthly
            rule renders it disabled so the control posts nothing.  Served
            here so the server renders the state the script would set,
            which is what makes the form correct before any script runs.
    """

    unit_id: int
    interval_n: int
    placement_id: int
    max_per_month: int | None
    ceiling_applies: bool


def selected_cadence(reading: CadenceReading) -> SelectedCadence:
    """Return the controls' starting state for a STORED rule's cadence.

    Projects a rule's cadence onto the ids the form posts, so the edit form's
    preselection cannot disagree with what the rule actually means.

    **It takes the READING rather than the stored columns, and plan step R7c-b
    is why.**  It used to take ``(pattern_id, interval_n)`` and decode them
    here -- which made the edit form the ONE reader still deriving a rule's
    unit and placement from the closed pattern set, while
    :func:`~app.services.recurrence.recurrence_spec` had moved to the authored
    ``unit_id`` / ``placement_id`` columns.  Two readers of one cadence, in a
    step whose whole claim is that every reader moved across.  The reading
    arrives from :func:`~app.services.recurrence.stored_cadence`, which is
    where "what the row's cadence columns say" is stated once -- and since plan
    step R7c-c that is a straight read of three columns rather than a decode.

    **A ``<select>`` whose selected value is absent from its options does not
    fail -- it silently becomes a different value**, which is why this is
    derived rather than assembled per form.  Measured on the transaction edit
    form before plan step R2e-2: no option carried ``selected``, so the browser
    fell back to the first in document order, which is the empty "Does not
    repeat" entry -- and submitting THAT deletes the rule and sweeps its future
    rows.  The caller asks :func:`~app.services.recurrence.stored_cadence`
    FIRST and renders the controls unset when the answer is ``None``, so this
    function is never asked about a cadence it cannot project.

    Args:
        reading: What the stored rule says on both authored axes, from
            :func:`~app.services.recurrence.stored_cadence`.

    Returns:
        The :class:`SelectedCadence`.

    Raises:
        RuntimeError: If ``ref_cache`` has not been initialized.
    """
    return SelectedCadence(
        unit_id=ref_cache.recurrence_unit_id(reading.cadence.unit),
        interval_n=reading.cadence.interval_n,
        placement_id=ref_cache.period_placement_id(reading.placement),
        max_per_month=reading.cadence.max_per_month,
        ceiling_applies=can_repeat_within_month(reading.cadence.unit),
    )


__all__ = [
    "CadenceOption",
    "CadenceWire",
    "EndBoundOption",
    "MonthReading",
    "NominalDayOption",
    "PickerModel",
    "SelectedCadence",
    "WeekdayReadingOption",
    "cadence_options",
    "end_bound_options",
    "month_reading_from_token",
    "nominal_day_options",
    "picker_model",
    "selected_cadence",
    "stored_month_reading",
    "weekday_reading_choices",
    "weekday_reading_options",
]
