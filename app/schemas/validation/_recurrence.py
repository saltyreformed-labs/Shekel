"""The recurrence controls the recurrence forms submit, and the rules on them.

Everything a form states about HOW OFTEN a definition repeats: the two cadence
axis fields, the cross-field rules over them, the closing bound's
three-controls-into-one-value composition, and the two mixins the create
schemas inherit -- :class:`RecurrenceCadenceFieldsMixin`, the cadence alone,
for the three forms that author one, and :class:`RecurrenceFormFieldsMixin`,
the cadence plus the calendar coordinates, for the two template forms.

Split out of :mod:`._helpers` at plan step R7c-b, when that module met the
1,000-line cap.  The seam is the one the module list already implied: `_helpers`
holds the primitives EVERY domain schema in this package needs -- the base
schema, the shared range validators, the percent-to-fraction hook, the
:class:`~app.schemas.validation._helpers.RowId` field and the
:class:`~app.schemas.validation._helpers._RefEnumField` base -- while this holds
one DOMAIN's shared form.  Its importers are the two template schemas and,
since plan step salary:R15-c, the paycheck-deduction schema
(:mod:`.salary`), whose form authors a cadence and nothing else about a rule.

**Two of the rules here are one half of a pair**, and each says so in its own
docstring, because the layer that holds the other half cannot be reached from a
schema: a schema never sees the template being edited, so it cannot know whether
an update AUTHORS a rule (``RECURRENCE_NEEDS_A_START``) or what the STORED half
of a validity window is (:func:`end_bound_before_start_message`).  Both name the
route helper that completes them.
"""


from datetime import date
from typing import TYPE_CHECKING

from marshmallow import (
    ValidationError,
    fields,
    post_load,
    validate,
    validates_schema,
)

from app.enums import RecurrenceUnitEnum
from app.schemas.validation._helpers import (
    _EFFECTIVE_DATE_RANGE,
    _RefEnumField,
)
from app.utils.digit_strings import MAX_INTEGER_COLUMN

if TYPE_CHECKING:
    from app.services.recurrence import MonthReading


#: The field error for a chosen cadence with no first occurrence beside it.
#:
#: Named here because TWO layers raise it and they must say the same thing: the
#: schema, for a CREATE (see
#: :meth:`RecurrenceFormFieldsMixin.validate_recurrence_states_a_start`), and
#: ``_recurrence_form_helpers.resolve_recurrence_rule_for_update`` for the one
#: UPDATE branch that also authors a rule -- adding a cadence to a template
#: that had none.  The schema cannot tell those apart, because it never sees
#: the template.
RECURRENCE_NEEDS_A_START: dict[str, list[str]] = {
    "starts_on": [
        "Choose the date this first happens.  It is what the recurrence "
        "repeats from, so nothing is generated before it.",
    ],
}

# THE COUNT FIELDS' CEILING -- the ``max=`` of ``interval_n`` and
# ``max_occurrences`` below.  This block documents those two declarations; the
# constant it names lives in ``app.utils.digit_strings``.
#
# :data:`~app.utils.digit_strings.MAX_INTEGER_COLUMN` -- the largest value a
# Postgres ``integer`` column holds, stated once there since plan step
# balance:X-dj -- is the ceiling BOTH of this form's count fields need:
# ``max_occurrences`` and ``interval_n`` are both ``integer``, so a larger
# submission dies at the DATABASE with ``NumericValueOutOfRange`` (SQLSTATE
# 22003) -- an unhandled 500 on a door an ordinary crafted POST reaches, which
# is the ``MarkDoneSchema`` defect the monetary bound records.  A schema-tier
# bound AT the column's domain is what keeps an unstorable value a designed
# 400.
#
# ``interval_n`` joined it at plan step R7c-c, and that step is what opened the
# hole on three of the four units: while the closed pattern set was the storage,
# ``is_authorable`` refused any MONTH or YEAR interval above 6, so only the
# PERIOD unit's free box could reach the flush with an unstorable count.
# Freeing the interval made every unit's box free, and ``is_authorable`` now
# asks only that the interval be POSITIVE -- the upper half of the domain is
# the column's type, and nothing was stating it.
#
# Each field states its own LOWER bound, or deliberately does not: see the two
# declarations for why ``interval_n`` carries ``min=1`` and ``max_occurrences``
# leaves that to the shape it composes into.

# The largest value a Postgres ``smallint`` column holds -- ``max_per_month``'s
# type (plan step salary:R15-a), and the same reasoning one column size down:
# a bound AT the column's domain keeps an unstorable ceiling a designed 400
# rather than a ``NumericValueOutOfRange`` 500.  A ``smallint`` because no
# calendar month holds more occurrences than that at any cadence
# ``budget.pay_schedule`` admits, and the type is the whole of the upper bound
# for the reason ``interval_n`` states: a ceiling above what a month can hold
# is vacuous, not wrong.
_MAX_SMALLINT_COLUMN = 32767


class RecurrenceUnitField(_RefEnumField):
    """A submitted ``ref.recurrence_units`` id, as its enum member.

    The first of the two axes a recurrence form authors since plan step R7b-2:
    what ``interval_n`` counts.  It replaced ``RecurrencePatternField``, which
    validated the closed pattern set the form used to post -- that set is now
    an ENCODING the write door chooses, never a thing a user picks.

    Whether the cadence this unit belongs to can be stored at all is a property
    of the ``(interval, unit, placement)`` triple rather than of any one field,
    so it is checked by :func:`validate_authorable_cadence` and not here.
    """

    _invalid_message = "Invalid repeat unit."

    def _member_for(self, row_id):
        """Return the :class:`~app.enums.RecurrenceUnitEnum` member, or ``None``.

        Args:
            row_id: A validated ``ref.recurrence_units`` id.

        Returns:
            The member, or ``None`` when unmodelled.
        """
        # Pylint: ``import-outside-toplevel`` -- deferred so this shared schema
        # helper, which every domain module imports, does not pull the
        # recurrence service package in at import time for the two schemas that
        # need it.
        from app.services.recurrence import (  # pylint: disable=import-outside-toplevel
            modelled_unit,
        )

        return modelled_unit(row_id)


class RepeatsOnField(fields.Field):
    """A submitted "repeating on" choice, as the reading it names.

    The control's options post the empty string (the same day of the month),
    a clamped month-end day or a weekday reading (plan step recurrence:R8-c),
    and :func:`~app.services.recurrence.month_reading_from_token` -- the ONE
    reader of those tokens, which the live preview asks too -- turns each
    into a :class:`~app.services.recurrence.MonthReading`.  A token no option
    posts is refused here, before any rule about the date is asked: it names
    no reading at all.
    """

    default_error_messages = {"invalid": "Invalid repeat day."}

    def _deserialize(self, value, attr, data, **kwargs):
        """Return the :class:`~app.services.recurrence.MonthReading` *value* names.

        Args:
            value: The submitted token.
            attr: Marshmallow's field-contract argument.
            data: Marshmallow's field-contract argument.
            **kwargs: Marshmallow's field-contract arguments.

        Returns:
            The reading.

        Raises:
            ValidationError: No option posts *value*.
        """
        # Pylint: ``import-outside-toplevel`` -- see
        # :meth:`RecurrenceUnitField._member_for`.
        from app.services.recurrence import (  # pylint: disable=import-outside-toplevel
            month_reading_from_token,
        )

        reading = (
            month_reading_from_token(value) if isinstance(value, str) else None
        )
        if reading is None:
            raise self.make_error("invalid")
        return reading


class PeriodPlacementField(_RefEnumField):
    """A submitted ``ref.period_placements`` id, as its enum member.

    The second axis a recurrence form authors: which pay period funds an
    occurrence.  See :class:`RecurrenceUnitField` for why the storable-set
    question is not asked here.
    """

    _invalid_message = "Invalid funding choice."

    def _member_for(self, row_id):
        """Return the :class:`~app.enums.PeriodPlacementEnum` member, or ``None``.

        Args:
            row_id: A validated ``ref.period_placements`` id.

        Returns:
            The member, or ``None`` when unmodelled.
        """
        # Pylint: ``import-outside-toplevel`` -- see
        # :meth:`RecurrenceUnitField._member_for`.
        from app.services.recurrence import (  # pylint: disable=import-outside-toplevel
            modelled_placement,
        )

        return modelled_placement(row_id)


def validate_authorable_cadence(data):
    """Refuse a submitted cadence the application cannot STORE.

    The cross-field half of the recurrence form's validation, shared by the
    transaction-template and transfer-template schemas so the rule is stated
    once.  Completeness is a property of the whole ``(interval, unit,
    placement)`` triple and of no single field, which is why all three are
    asked here.

    **What can still be REFUSED changed at plan step R7c-c.**  While the
    cadence was stored as a closed-set pattern id, the binding constraint was
    STORAGE: the set covered every N pay periods but only 1, 3 or 6 months, and
    paired the first-paycheck placement with a ONE-month interval only.  With
    ``interval_n`` and ``unit_id`` authored columns every reading can be stored,
    so what is left is whether the application can HONOUR the
    ``(unit, placement)`` PAIR.  Until plan step **R8-a** that meant "can a
    first occurrence be DERIVED", which refused two pairs by naming derivations
    ruling **R-R16** had already deleted; it is the live rule
    :func:`~app.services.recurrence.authorable_cadences` states now.  The
    ``WEEK`` unit was refused too until plan step **R8-b** dated a weekly row
    from its occurrence, so the only reading still refused is a pay-period
    cadence funded from a LATER paycheck, whose placement is inert.  **The
    interval is no longer able to make a cadence unauthorable**, which is what
    the refusal's own copy had to stop saying.

    **This is the door's copy of a rule the FORM already makes unreachable.**
    Plan step R7b-2 serves the picker's options from
    :func:`~app.services.recurrence.cadence_options`, derived from the same
    producer this asks, so no combination a user can assemble by clicking
    arrives here refused.  It is what a hand-assembled POST meets, and it is why
    the write door's
    :class:`~app.services.recurrence.RecurrenceResolutionError` stays a
    broken-invariant 500 rather than becoming a user-facing path.

    Skipped when NO unit is named: "does not repeat" is the absence of a
    cadence, and a partial update that omits the recurrence keys leaves the
    stored one alone.

    **A named unit with no placement is REFUSED rather than skipped**, and two
    independent adversarial reviews of plan step R7b-2 found the same 500 in
    the version that skipped it.  The two axes are halves of one value, not two
    optional refinements: both write doors pop the placement with no default,
    so half a cadence reached ``recurrence_spec_from_form`` as an
    unhandled ``KeyError``.  Its EMPTY spelling was worse -- the field is
    ``allow_none``, so :func:`_normalize_empty_inputs` keeps the key with a
    present ``None``, marshmallow never deserializes it, and the route built a
    spec with ``placement=None`` that RESOLVED (the ``PERIOD`` unit's anchor
    does not read the placement) and then died inside the write door's
    completeness refusal, which was ``encode_cadence`` then and is
    :func:`~app.services.recurrence.require_authorable_cadence` now.  That is
    precisely the refusal this function exists to turn into a field error,
    arriving as a 500 instead.

    It is defect **D13**'s shape one field over, and the transfer route's own
    docstring records that one: ``recurrence_pattern`` was ``allow_none`` while
    a comment claimed it required, so an omitted key 500'd there too.  Refusing
    the pair HERE rather than defaulting the placement in the route is what
    stops a rule being authored from a cadence the user only half stated -- a
    default would pick which paycheck pays a bill on the user's behalf.

    **A named unit with no INTERVAL is refused for the same reason, from plan
    step R7c-c, and until then it was DEFAULTED TO 1 in three places** -- here,
    and in each write door's ``pop``.  That default moved money: R7c-c replaced
    the months ``<select>`` (which cannot post an empty value) with one free
    ``<input type="number">``, so clearing the box on a quarterly bill dropped
    the key -- ``interval_n`` is not ``allow_none``, so
    :func:`_normalize_empty_inputs` removes it rather than keeping a stated
    ``None`` -- and the save silently re-cadenced the rule to every 1 month,
    generating 12 occurrences a year where 4 were owed, across the whole
    projection.  The shape was already reachable for the PERIOD unit's free box
    before R7c-c; freeing the interval widened it to every unit.

    **Absence is refused rather than read as "leave the stored one alone"**,
    which is the opposite of what ``starts_on`` and the closing bound do, and
    the difference is which states the CONTROL can produce.  Those two are
    disabled on a form whose value the app DERIVES (a loan payment's), so
    "not mine to state" is a real third state and the update door honours it.
    The interval box is disabled only while the definition does not repeat
    (``_recurrence_fields.html``; ``recurrence_form.js`` re-enables it for
    every chosen cadence), so beside a named unit there is no producer of an
    absent interval except a cleared box or a hand-assembled POST.  Two states,
    not three -- so this field is not one of plan ledger row **D36**'s, and
    refusing states its real arity instead of adding a fourth hand-written
    presence read at a route site, which is what D36 warns against.

    Args:
        data: The deserialized schema payload.

    Raises:
        ValidationError: A unit is named without a placement or without an
            interval, or the submitted triple has no closed-set pattern to be
            stored as.
    """
    # Pylint: ``import-outside-toplevel`` -- see
    # :meth:`RecurrenceUnitField._member_for`.
    from app.services.recurrence import (  # pylint: disable=import-outside-toplevel
        is_authorable,
    )

    unit = data.get("recurrence_unit")
    if unit is None:
        return
    placement = data.get("recurrence_placement")
    if placement is None:
        raise ValidationError(
            "Choose which paycheck funds each occurrence.",
            field_name="recurrence_placement",
        )
    if "interval_n" not in data:
        raise ValidationError(
            "Say how often this repeats.  Enter a number beside the unit, "
            "like 3 for every 3 months.",
            field_name="interval_n",
        )
    if not is_authorable(data["interval_n"], unit, placement):
        # **"a different repeat unit", not "a different interval", from plan
        # step R7c-c.**  The interval was half the answer while storage was the
        # binding constraint; it cannot make a cadence unauthorable now, so
        # telling the user to change it names a control that will not help.
        # The two that can are the two this copy names, and it spells them the
        # way their own field refusals do ("Invalid repeat unit.", "Invalid
        # funding choice.") so one vocabulary reaches the user.
        raise ValidationError(
            "That repeat schedule cannot be saved yet. Pick a different "
            "repeat unit or a different funding choice.",
            field_name="recurrence_unit",
        )


#: The key a form posts its closing-bound SHAPE under, and the key the
#: composed bound is handed to the route under.
#:
#: They are the same field: :func:`compose_end_bound` replaces the submitted
#: token with the value it names, the way
#: :class:`RecurrenceUnitField` replaces a submitted id with its enum member.
#: One key rather than two is what keeps "the form said nothing about the
#: bound" expressible -- an ABSENT key, which a loan payment's form and an
#: amount-only PATCH both produce, and which the update route reads as "leave
#: the stored bound alone".
#: The rule's FIRST OCCURRENCE, as the payload spells it, and how the "repeating
#: on" control reads it.
#:
#: Named here for the reason :data:`RECURRENCE_END_BOUND_KEY` is: the route
#: helpers read these keys' PRESENCE, not just their values, and presence is
#: how a form says "not mine to state" -- so the string has to be the same one
#: the field is declared under or a locked control's silence reads as a stated
#: ``None``.  Three route modules and the schema spelled it four times between
#: them before plan step R7c-b named it, which is three chances for a rename to
#: half-land.
#:
#: The two are one constant apart rather than one value because the payload has
#: two keys; they RIDE TOGETHER on the start's presence, which
#: ``update_recurrence_rule_from_form`` is where that rule is written.
#:
#: **``repeats_on`` replaced ``nominal_day`` at plan step recurrence:R8-c**
#: (developer ruling 2026-11-02, the rename approved with its tests): the
#: control offers the weekday readings beside the clamped month-end days, and
#: one ``<select>`` posts one key, so a nominal day beside a weekday reading
#: (``ck_recurrence_rules_one_month_reading``) is a pair no form can post.
#: The schema replaces the token with the
#: :class:`~app.services.recurrence.MonthReading` it names, under the same
#: key, as :func:`compose_end_bound` does for the bound.
RECURRENCE_STARTS_ON_KEY: str = "starts_on"
RECURRENCE_REPEATS_ON_KEY: str = "repeats_on"

#: The per-month ceiling's key (plan step salary:R15-a), named for the same
#: reason: the update door reads its PRESENCE.  The control is rendered only
#: beside a unit whose occurrences can repeat within a month and DISABLED
#: beside any other, so an absent key means "the form could not show it" and
#: leaves the stored value alone, while a present ``None`` is a cleared box.
RECURRENCE_MAX_PER_MONTH_KEY: str = "max_per_month"


RECURRENCE_END_BOUND_KEY: str = "recurrence_end_mode"


def compose_end_bound(data):
    """Replace the submitted bound token with the :class:`EndBound` it names.

    The closing bound crosses the wire as THREE controls -- a mode select and
    the two inputs its shapes need -- and is ONE value everywhere above this
    line.  Composing it here rather than in the route is the placement
    :class:`_RefEnumField` records for the axis fields: the schema is the
    boundary between the wire format and the logic value, so a second form
    door cannot forget the conversion.

    **The two payload inputs are consumed, not left beside the result.**  A
    route that could still read ``end_date`` would be able to write a bound the
    mode did not name, which is the two-independent-fields reading
    :class:`~app.services.recurrence.EndBound` exists to remove.

    **An ABSENT mode leaves the payload untouched**, and the distinction is
    load-bearing rather than tidy.  "The form did not mention the bound" is a
    real request -- a loan payment's form, whose bound is DERIVED from the loan
    and whose controls are therefore disabled, and any partial update -- and it
    must not read as "ends never", which would silently clear a stop the user
    set.  It is the same present-``None``-versus-absent distinction
    ``recurrence_unit`` already turns on.

    Args:
        data: The deserialized schema payload, mutated in place.

    Returns:
        The same payload, so a ``@post_load`` hook can hand it straight back.

    Raises:
        ValidationError: The mode names no shape, or the shape it names needs
            an input the submission left blank.  Raised against the CONTROL at
            fault -- the empty date box, not the mode select the user answered
            correctly -- which is what
            :class:`~app.services.recurrence.EndBoundInputError` carries the
            field for.
    """
    # Pylint: ``import-outside-toplevel`` -- see
    # :meth:`RecurrenceUnitField._member_for`.
    from app.services.recurrence import (  # pylint: disable=import-outside-toplevel
        EndBoundInputError,
        end_bound_from_token,
    )

    if RECURRENCE_END_BOUND_KEY not in data:
        return data
    token = data.pop(RECURRENCE_END_BOUND_KEY)
    end_date = data.pop("end_date", None)
    max_occurrences = data.pop("max_occurrences", None)
    try:
        data[RECURRENCE_END_BOUND_KEY] = end_bound_from_token(
            token, end_date=end_date, max_occurrences=max_occurrences,
        )
    except EndBoundInputError as exc:
        raise ValidationError(exc.message, field_name=exc.field) from exc
    return data


def end_bound_before_start_message(end_date: date, starts_on: date) -> str:
    """Return the refusal for a closing bound that precedes the first occurrence.

    **Named because TWO doors raise it and they must say the same thing**, the
    same reason :data:`RECURRENCE_NEEDS_A_START` is named: this schema, for a
    submission that states both values, and
    ``_recurrence_form_refusals.refuse_inverted_window`` for an UPDATE, where
    either value may be the STORED one and no schema can see the pair.

    ``ck_recurrence_rules_valid_window`` stands behind them since plan step
    R7d-g, as a backstop and not as the rule: a CHECK answers with an
    ``IntegrityError`` and these two doors answer with this sentence.  Plan
    step R7c-b held the CHECK back on a developer ruling while the columns
    carried user-authored windows AND derived loan-payment ones (an empty
    DERIVED window is a correct answer a CHECK cannot tell from a user's
    mistake); the derived window is stored nowhere now.

    Args:
        end_date: The stated closing date.
        starts_on: The rule's first occurrence.

    Returns:
        The refusal sentence, naming both dates.
    """
    return (
        f"This stops repeating on {end_date:%b %-d, %Y}, before it first "
        f"happens on {starts_on:%b %-d, %Y}.  A recurrence that ends before it "
        f"starts names no occurrence at all."
    )


def require_end_bound_after_start(data):
    """Reject a closing bound that falls before the rule's first occurrence.

    **The CREATE door's half of the rule**, where the submission states both
    values and the pair is a two-field comparison a schema can refuse without a
    calendar.  ``end_date`` is live and user-authored, so an unrefused
    violation would reach the flush -- the user could not stop a recurring bill
    and the projection would keep charging it.
    ``_recurrence_form_refusals.refuse_inverted_window`` is the UPDATE door's
    half, and it is not redundant: on an update either value may be the stored
    one, which this cannot see.

    **Read off the COMPOSED bound, not off the ``end_date`` control**, and the
    difference is a false refusal.  The three "Ends" controls are one value with
    three shapes (plan step R7b-3): a submission whose mode is "Never" carries
    no date whatever the date input holds, so comparing the raw key would refuse
    a stale value the rule will never store.  Asking the value keeps "which
    input does this shape read" stated once, in
    :class:`~app.services.recurrence.EndBound`.

    Runs only when the payload states BOTH.  A form that stated no bound leaves
    the stored one alone (a loan payment's disabled control, an amount-only
    PATCH), and a stored pair was checked when it was written.

    Args:
        data: The payload, AFTER :func:`compose_end_bound`.

    Returns:
        None.

    Raises:
        ValidationError: The stated end date precedes the first occurrence.
    """
    starts_on = data.get("starts_on")
    bound = data.get(RECURRENCE_END_BOUND_KEY)
    if starts_on is None or bound is None:
        return
    end_date = bound.columns().end_date
    if end_date is None or end_date >= starts_on:
        return
    raise ValidationError(
        end_bound_before_start_message(end_date, starts_on),
        field_name="end_date",
    )


def _require_offerable_reading(
    unit: RecurrenceUnitEnum, starts_on: date, reading: "MonthReading",
) -> None:
    """Refuse a "repeating on" choice *starts_on* does not allow.

    See :meth:`RecurrenceFormFieldsMixin.validate_repeats_on_fits_the_start`.

    Args:
        unit: The submitted cadence unit.
        starts_on: The submitted first occurrence.
        reading: The submitted :class:`~app.services.recurrence.MonthReading`.

    Raises:
        ValidationError: The date does not allow *reading*.
    """
    # Pylint: ``import-outside-toplevel`` -- see
    # :meth:`RecurrenceUnitField._member_for`.
    from app.services.recurrence import (  # pylint: disable=import-outside-toplevel
        month_reading_choices,
    )

    choices = month_reading_choices(unit, starts_on)
    if reading in choices:
        return
    day = reading.nominal_day
    if not choices:
        named = "a weekday of the month" if day is None else f"day {day}"
        raise ValidationError(
            f"A paycheck or weekly schedule has no day of the month, so it "
            f"cannot repeat on {named}. Choose a monthly or yearly schedule.",
            field_name=RECURRENCE_REPEATS_ON_KEY,
        )
    *earlier, last = choices.values()
    offered = f"this can repeat on {', on '.join(earlier)} or on {last}."
    raise ValidationError(
        f"Starting on {starts_on:%b %-d, %Y}, {offered}" if day is None else
        f"{starts_on:%b %-d, %Y} cannot mean day {day}. "
        f"Starting on that date, {offered}",
        field_name=RECURRENCE_REPEATS_ON_KEY,
    )


class RecurrenceCadenceFieldsMixin:
    """The CADENCE controls every recurrence form submits, declared once.

    The three cadence axes -- unit, interval, placement -- and the per-month
    ceiling, with the two cross-field rules that grade them as a set.  What a
    form says about HOW OFTEN a definition repeats, and nothing about WHEN it
    starts, stops, or which day a bill is due: those are the calendar
    coordinates :class:`RecurrenceFormFieldsMixin` adds below for the two
    template forms.

    **Split out of that mixin at plan step salary:R15-c** (ruling
    **R-SAL31**), when the third recurrence form arrived: a payroll deduction's
    cadence is a rule on the row (R15-b), authored on the deduction form
    through the same four controls -- and no others, because a payroll line's
    first occurrence is DERIVED from the owner's schedule (ruling **R-SAL30**,
    no "Starts on" box), it carries no closing bound and no due day.  The
    fuller mixin's own docstring promised that a third form would get the
    declaration rather than a copy; a third form needing FOUR of its nine
    fields is what made the two-level split the way to keep that promise
    without handing the deduction schema five fields its form never renders
    (a hidden ``starts_on`` a crafted POST could state, against a rule the
    door derives).

    Marshmallow collects fields and hooks across the whole MRO, so a plain
    mixin beside :class:`~app.schemas.validation._helpers.BaseSchema` is all
    this needs; it deliberately does NOT subclass ``Schema``, which would
    make it a schema in its own right and invite it to be loaded.
    """

    # The two AUTHORED cadence axes since plan step R7b-2.  Each value is the
    # integer primary key of a ref row (recurrence_units, period_placements),
    # submitted as a string via HTML form data and deserialized to the ENUM
    # member it names.  The typed fields rather than bare ``RowId``s: they also
    # refuse an id no enum member names -- what the application MODELS is
    # narrower than what a table HOLDS, and the gap is a 500 (plan step R2e-2).
    #
    # ``RowId`` underneath rather than ``Integer`` because these ARE row ids
    # despite their names (plan step X-ae): an adversarial review found
    # ``Integer`` reading '١', ' 2 ', '+3', '007' and '1_0' as ids, and the
    # completeness gate could not see it while that gate matched on a ``_id``
    # SUFFIX.
    #
    # They replaced ``recurrence_pattern``: the closed pattern set is now the
    # STORAGE encoding the write door chooses, not a name a user picks.
    #
    # ``allow_none`` so the form's "Does not repeat" option survives the
    # pre_load hook as an explicit ``None`` rather than a dropped key (plan step
    # R2e-1).  The two are different requests and the update route acts on them
    # differently -- a present ``None`` CLEARS the recurrence, an absent key
    # leaves it alone -- so collapsing them would make an amount-only PATCH
    # silently delete a template's cadence.
    #
    # ``offset_periods`` is GONE (defect D8).  It was a vestigial field no
    # template ever rendered an input for, so every submission carried the
    # schema default -- which the update path then wrote over the rule's real
    # phase.  ``resolve`` derives the phase from the rule's opening bound.
    #
    # ``interval_n`` is the cadence's THIRD axis and it is bounded at BOTH ends
    # here.  ``min=1`` mirrors ``ck_recurrence_rules_positive_interval``;
    # ``max`` is the column's own type, which nothing stated until plan step
    # R7c-c freed the interval for every unit -- see
    # :data:`~app.utils.digit_strings.MAX_INTEGER_COLUMN`.  It is NOT
    # ``required``, for the reason
    # ``starts_on`` is not: a submission naming no cadence authors no rule and
    # a partial update that omits every recurrence key must stay one.  What
    # makes it required WHEN A CADENCE IS CHOSEN is
    # :func:`validate_authorable_cadence`, the only layer that sees the triple.
    recurrence_unit = RecurrenceUnitField(allow_none=True)
    recurrence_placement = PeriodPlacementField(allow_none=True)
    interval_n = fields.Integer(
        validate=validate.Range(min=1, max=MAX_INTEGER_COLUMN),
    )

    # The most occurrences any one calendar month admits -- a month's first N
    # paydays for a paycheck cadence -- and ``None``, which is every ordinary
    # rule, for no ceiling (plan step
    # salary:R15-a, ruling **R-SAL29**).  "Every paycheck, at most 2 a month"
    # is a payroll benefit taken on a month's first two paychecks, which the
    # two axes could not say (ledger row **D59**).  The form renders the
    # control ONLY beside a unit whose occurrences can repeat within a month
    # (paychecks and weeks); a calendar-month
    # cadence fires at most once a month by construction, and
    # :meth:`validate_ceiling_fits_the_unit` refuses the pair a hand-assembled
    # POST could state.
    #
    # ``min=1`` mirrors ``ck_recurrence_rules_positive_max_per_month`` -- a
    # zero would be a rule that never fires spelled as a cadence -- and ``max``
    # is the column's own type; see :data:`_MAX_SMALLINT_COLUMN`.
    # ``allow_none`` so a cleared box reaches the update door as a stated
    # clear rather than a dropped key.
    max_per_month = fields.Integer(
        allow_none=True,
        validate=validate.Range(min=1, max=_MAX_SMALLINT_COLUMN),
    )

    @validates_schema
    def validate_cadence_is_storable(self, data, **kwargs):
        """Reject a submitted cadence the closed pattern set cannot store.

        See :func:`validate_authorable_cadence` for the reasoning: the
        storable set is a property of the whole ``(interval, unit,
        placement)`` triple, and the form already makes an unstorable one
        unofferable, so this is what a hand-assembled POST meets.

        The update schemas inherit it; a partial update that omits the
        recurrence keys returns early there for the same reason the envelope
        rule does.

        Args:
            data: The deserialized payload.
            **kwargs: Marshmallow's hook contract.

        Raises:
            ValidationError: The triple has no closed-set pattern to be stored
                as.
        """
        validate_authorable_cadence(data)

    @validates_schema
    def validate_ceiling_fits_the_unit(self, data, **kwargs):
        """Reject a per-month ceiling on a unit that cannot repeat within a month.

        **The submission's half of the ``(unit, max_per_month)`` pair** that
        :class:`~app.services.recurrence.RecurrenceSpec` refuses at
        construction (plan step salary:R15-a): a cadence measured in whole
        months fires at most once a month by construction, so a ceiling
        beside it is a value the walk applies and never reads -- and the pair
        reaching the write door would be a ``RecurrenceResolutionError``
        turned into a flash rather than a field error naming the control.
        Asked through :func:`~app.services.recurrence.can_repeat_within_month`,
        the same predicate the picker offers the control on, so the form, this
        door and the write door cannot disagree about which units admit one.

        Skipped unless the submission states both: a form that names no
        cadence authors no rule, and a submission with no ceiling states no
        pair to refuse.

        Args:
            data: The deserialized payload.
            **kwargs: Marshmallow's hook contract.

        Raises:
            ValidationError: The pair contradicts itself.
        """
        # Pylint: ``import-outside-toplevel`` -- see
        # :meth:`RecurrenceUnitField._member_for`.
        from app.services.recurrence import (  # pylint: disable=import-outside-toplevel
            can_repeat_within_month,
        )

        unit = data.get("recurrence_unit")
        max_per_month = data.get("max_per_month")
        if unit is None or max_per_month is None:
            return
        if can_repeat_within_month(unit):
            return
        raise ValidationError(
            "A monthly or yearly schedule already happens at most once a "
            "month, so a per-month limit has nothing to limit. Clear it, or "
            "choose a paycheck schedule.",
            field_name="max_per_month",
        )


class RecurrenceFormFieldsMixin(RecurrenceCadenceFieldsMixin):
    """The recurrence controls both template forms submit, declared once.

    A form authors a recurrence the same way whichever kind of definition it
    belongs to -- the cadence's two axes, the calendar coordinates, the opening
    bound and the closing one -- so the two template schemas
    (``TemplateCreateSchema``, ``TransferTemplateCreateSchema``) carried SEVEN
    identical field declarations and an identical cross-field cadence rule.
    Plan step R7b-3 would have made it nine and added an identical
    ``@post_load`` to each, which is when ``duplicate-code`` said so and the
    copy stopped being worth keeping.  They differed in exactly ONE field,
    ``due_day_of_month``, which only a transaction template carried, until
    plan step recurrence:R5-a dropped it with its column (ruling **R-R96**).

    **Declared here rather than copied because a copy is what a THIRD form
    would have neither of**, which is the same reasoning
    :class:`_RefEnumField` records for putting the modelled-value check in the
    field type: a rule that travels with the declaration cannot be forgotten.
    Plan step R7b-3 is what made the duplication worth removing -- it added the
    closing bound's three controls and its hook to both, and ``duplicate-code``
    caught the pair.  **The third form came at plan step salary:R15-c and
    took FOUR of the nine**, so the cadence's own declarations live on
    :class:`RecurrenceCadenceFieldsMixin`, which this extends with the
    calendar coordinates a template's form collects and a payroll line's
    does not.

    Marshmallow collects fields and hooks across the whole MRO, so a plain
    mixin beside :class:`BaseSchema` is all this needs; it deliberately does
    NOT subclass ``Schema``, which would make it a schema in its own right and
    invite it to be loaded.
    """

    # The rule's FIRST OCCURRENCE (plan step R7c-b, ruling **R-R16**).  It
    # replaced THREE fields: ``day_of_month`` (the cycle's day),
    # ``month_of_year`` (its residue class) and ``start_date`` (the opening
    # bound) -- because the first occurrence carries all three, being the
    # earliest thing the cadence produces and the member of the cycle its
    # position defines.
    #
    # **The key is ``starts_on`` and not the ``start_date`` it replaced, and
    # renaming it is the point.**  The two mean different things -- a bound
    # against an occurrence -- so a page cached from before this deploy posts a
    # key nothing reads and the submission fails the required-together rule
    # below rather than silently authoring a rule whose start means something
    # the user did not say.
    #
    # NOT ``required``: a submission that names no cadence
    # (``recurrence_unit`` empty, the form's "Does not repeat") authors no rule
    # at all, and a partial update that omits every recurrence key must stay a
    # partial update.  What makes it required WHEN A CADENCE IS CHOSEN is
    # :meth:`validate_recurrence_states_a_start` below, which is the only
    # layer that can see both fields.
    #
    # Bounded for the reason ``effective_from`` is bounded on both template
    # schemas: an ``<input type="date">`` accepts a four- or five-digit-year
    # typo, and here the consequence is worse than a bad version date -- a
    # first occurrence past the horizon generates NOTHING, silently.
    starts_on = fields.Date(validate=_EFFECTIVE_DATE_RANGE)

    # How ``starts_on`` is READ (plan step recurrence:R8-c, rulings plan#2668
    # and plan#2670): by its day of the month, as the day it MEANS when its
    # own month was too short to hold it (ruling R-R3; 29-31, the domain
    # ``ck_recurrence_rules_nominal_day`` bounds), or by its weekday -- "the
    # third Tuesday", "the last Friday".  ONE field for what was
    # ``nominal_day`` alone, because the form renders one control and a
    # rule's first date is read one way.  ABSENT is the same day of the
    # month: the control's default option posts the empty string, which
    # :func:`_normalize_empty_inputs` drops.  Which choices a date allows is
    # a rule over the pair, and :meth:`validate_repeats_on_fits_the_start`
    # refuses one it does not.
    repeats_on = RepeatsOnField()

    # The CLOSING BOUND, as three controls that compose into ONE value (plan
    # step R7b-3).  ``recurrence_end_mode`` names which of the bound's three
    # shapes the user chose and :meth:`build_end_bound` replaces it with the
    # :class:`~app.services.recurrence.EndBound` that shape builds, consuming
    # the two inputs beside it -- the same wire-format-to-logic-value
    # conversion :class:`RecurrenceUnitField` performs one field up.
    #
    # ``max_occurrences`` carries only an UPPER bound here, and the asymmetry
    # is the point.  "At least one occurrence" is the SHAPE's invariant
    # (``EndsAfterOccurrences.__post_init__``), held where no path can miss it
    # and refused with a message that names the control; repeating it as a
    # ``min=`` would put one rule in two places and hand the user marshmallow's
    # generic wording instead.  What the shape has no opinion about is how
    # large a count the COLUMN can hold, so that bound is here -- see
    # :data:`~app.utils.digit_strings.MAX_INTEGER_COLUMN`.
    #
    # No ``allow_none`` on the mode, deliberately.  An empty select value is
    # dropped by :func:`_normalize_empty_inputs`, so it arrives ABSENT -- and
    # an absent mode is what "this form said nothing about the bound" means (a
    # loan payment, whose bound is derived; an amount-only PATCH).  Keeping it
    # as a present ``None`` would make those requests indistinguishable from
    # "ends never", which silently clears a stop the user set.
    #
    # ``end_date`` stays ``allow_none`` because clearing a date input has to
    # reach the schema as a stated empty rather than a dropped key.
    recurrence_end_mode = fields.String()
    end_date = fields.Date(allow_none=True)
    max_occurrences = fields.Integer(
        validate=validate.Range(max=MAX_INTEGER_COLUMN),
    )

    #: Whether a chosen cadence on THIS schema must come with a first
    #: occurrence.
    #:
    #: ``True`` on the create schemas, where a submission always authors a new
    #: rule; ``False`` on the update schemas, where it usually re-points an
    #: existing one and where an omitted key means "leave the stored value
    #: alone".  See :meth:`validate_recurrence_states_a_start`.
    recurrence_start_is_required: bool = True

    @validates_schema
    def validate_repeats_on_fits_the_start(self, data, **kwargs):
        """Reject a "repeating on" choice the submitted first occurrence does not allow.

        **The submission's half of the pair** that
        :class:`~app.services.recurrence.RecurrenceSpec` refuses at
        construction, for each of the choice's two kinds -- so the refusal is
        a field error naming the control rather than a broken invariant the
        write door turns into a 500.

        * **A nominal day** -- the two-field half of
          ``ck_recurrence_rules_nominal_day``: the day must EXCEED the date's
          own, and the date's own day must be what clamping it into that
          month produces.  ``starts_on=2026-04-15&repeats_on=30`` is the
          shape: 30 is in the domain, April 15 was never clamped.
        * **A weekday reading** (plan step recurrence:R8-c) -- the date must
          be one the reading can read (no fifth from the start, ruling
          plan#2670; the last such weekday from the end), on a unit measured
          in months.

        Both are asked through
        :func:`~app.services.recurrence.month_reading_choices`, built on the
        producers the control is enabled from and the write door refuses
        against, and answered with every choice the date DOES allow, in the
        control's own words (ruling plan#2909).

        Skipped unless the submission states all three.  A form that names no
        cadence authors no rule; one that omits ``starts_on`` leaves the stored
        date alone, and the update door then ignores a submitted choice with
        it (both ride on ONE presence key, because they are one statement of
        when the rule fires).

        Args:
            data: The deserialized payload.
            **kwargs: Marshmallow's hook contract.

        Raises:
            ValidationError: The pair contradicts itself.
        """
        unit = data.get("recurrence_unit")
        starts_on = data.get("starts_on")
        reading = data.get(RECURRENCE_REPEATS_ON_KEY)
        if unit is None or starts_on is None or reading is None:
            return
        _require_offerable_reading(unit, starts_on, reading)

    @validates_schema
    def validate_recurrence_states_a_start(self, data, **kwargs):
        """Reject a chosen cadence with no first occurrence beside it.

        ``budget.recurrence_rules.starts_on`` is ``NOT NULL`` from plan step
        R7c-b, so a rule cannot be authored without one -- and the value is not
        merely a required field, it is the whole of what the rule says about
        when it begins.  Without this the omission would reach the write door
        as a ``RecurrenceResolutionError``, which routes turn into a flash
        rather than a field error.

        **A money decision, not a typing one.**  An empty opening bound used to
        mean "start with the schedule", and the create routes generate with no
        lower window bound -- so a ``$2,000.00`` rent template created today
        wrote five backdated rows into pay periods that had already closed
        (measured at plan step R7b-4).  A required first occurrence makes that
        state unreachable rather than defended against.

        **It applies to a CREATE and not to an UPDATE** (developer ruling
        2026-08-15), which is the split :attr:`recurrence_start_is_required`
        carries.  The harm above is a create's: an update edits a rule that
        already HAS a start, so an unstated one leaves that value alone and
        backdates nothing.  Requiring it on both is what made a loan payment's
        locked control unsubmittable -- the app derives that bound, so its form
        may not state one, and the only ways to satisfy a required field there
        were to post the derived value back (making "not mine to state"
        indistinguishable from a deliberate choice) or to lock the user out of
        renaming their own loan payment.

        An update that adds a cadence to a template which had none DOES author
        a rule, and the schema cannot see that -- it has no template.  The
        route can, and
        ``_recurrence_form_helpers.resolve_recurrence_rule_for_update`` refuses
        it there with the same message.

        Both fields are read from the payload rather than from the schema's
        ``required``, because the rule is CONDITIONAL: a submission naming no
        cadence authors no rule, and a partial update that omits every
        recurrence key must stay a partial update.

        Args:
            data: The deserialized payload.
            **kwargs: Marshmallow's hook contract.

        Raises:
            ValidationError: A cadence was chosen and no ``starts_on`` given.
        """
        if not self.recurrence_start_is_required:
            return
        if data.get("recurrence_unit") is None:
            return
        if data.get("starts_on") is not None:
            return
        raise ValidationError(RECURRENCE_NEEDS_A_START)

    @post_load
    def build_end_bound(self, data, **kwargs):
        """Replace the submitted bound token with the value it names.

        See :func:`compose_end_bound`, which carries the reasoning, and
        :func:`require_end_bound_after_start` for the window rule checked once
        the bound is a value rather than three controls.

        Args:
            data: The deserialized payload.
            **kwargs: Marshmallow's hook contract.

        Returns:
            The payload, with the three bound controls collapsed into one
            ``recurrence_end_mode`` entry holding an
            :class:`~app.services.recurrence.EndBound`.

        Raises:
            ValidationError: See :func:`require_end_bound_after_start`.
        """
        composed = compose_end_bound(data)
        require_end_bound_after_start(composed)
        return composed
