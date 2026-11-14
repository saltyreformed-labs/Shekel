"""Pay-period generation and lifecycle validation schemas.

The bounds every field here applies are NAMED rather than repeated, and plan
step **X-ad-a** is why: registration became a fifth door onto the same two
rules, so a literal copied once more would have been five statements of one
bound.  Each pair is imported from whoever OWNS the rule rather than restated
here -- the batch pair from the module that says what one batch may be,
which the writer asks before its transaction does the work
(:data:`~app.services.pay_period_batch.PERIOD_BATCH_MIN` /
:data:`~app.services.pay_period_batch.PERIOD_BATCH_MAX`), and, since plan step
**balance:X-bh-2**, the pay-history window from :mod:`app.utils.dates` --
which is not a column's bound at all but how far this application's calendar
reaches, mirrored onto ``ck_pay_schedule_history_opens_range`` for the writers
that never see a schema.  A schema is a door, and a door does not get to
invent the rule it enforces.  The payday window is the shared one in
:mod:`app.schemas.validation._helpers` (``_PAYDAY_RANGE``, task plan#3577),
which every door that types a payday reads.

*TWO pairs here, where this paragraph counted three until plan step*
``pay_calendar:C17-d-3`` *moved the rhythm's -- the cadence pair, and the
day-of-month pair that step added -- to* :mod:`~app.schemas.validation._pay_rhythm`
*with the fields that read them, when this module met pylint's 1,000-line
cap.  The number is restated rather than left to decay, which is the same
discipline* :data:`app.utils.dates.CALENDAR_DATE_MIN`'s *own comment
follows.*

**The RHYTHM every door here states is declared once, in
:mod:`app.schemas.validation._pay_rhythm`** (plan step ``pay_calendar:C17-d-3``,
ruling **R-PC84**): the kind control and its arms' inputs, the convention,
the wire vocabulary, the cross-field rule and the mixins that put them on a
schema.  A door that establishes a rhythm takes
:class:`~app.schemas.validation._pay_rhythm.DefaultedRhythmFields`, a door
that corrects one takes
:class:`~app.schemas.validation._pay_rhythm.RequiredRhythmFields`, and each
reads the value back as ``rhythm`` off its loaded payload.
"""


from marshmallow import (
    fields,
    pre_load,
    validate,
)

from app.config import BaseConfig
from app.schemas.validation._helpers import (
    BaseSchema,
    RowId,
    _PAYDAY_RANGE,
    _normalize_empty_inputs,
)
from app.schemas.validation._pay_rhythm import (
    DefaultedRhythmFields,
    RequiredRhythmFields,
)
from app.services.pay_period_batch import PERIOD_BATCH_MAX, PERIOD_BATCH_MIN
from app.utils.dates import CALENDAR_DATE_MAX, CALENDAR_DATE_MIN


_PERIOD_BATCH_RANGE = validate.Range(
    min=PERIOD_BATCH_MIN, max=PERIOD_BATCH_MAX,
)
#: The window a stated pay-history opening may fall in, from
#: :mod:`app.utils.dates`, which OWNS it.  Not the column's own rule, unlike
#: the batch range above: it is how far this application's calendar reaches,
#: and ``ck_pay_schedule_history_opens_range`` mirrors the same pair onto the
#: table.  Reading the constant here rather than an alias of it is what keeps
#: one number from acquiring a fourth name.  The history opening's ALONE since
#: task plan#3577: a payday is not a history, and :func:`payday_field` reads
#: the window every payday door shares,
#: :data:`~app.schemas.validation._helpers._PAYDAY_RANGE`.
_HISTORY_OPENS_RANGE = validate.Range(
    min=CALENDAR_DATE_MIN, max=CALENDAR_DATE_MAX,
)


def num_periods_field(**kwargs) -> fields.Integer:
    """Return a how-many-periods field bounded by the batch policy.

    Args:
        **kwargs: Forwarded to :class:`marshmallow.fields.Integer`; see
            :func:`cadence_days_field` for why the rest of the declaration is
            the caller's.

    Returns:
        The field, carrying the shared range validator.
    """
    return fields.Integer(validate=_PERIOD_BATCH_RANGE, **kwargs)


def payday_field(**kwargs) -> fields.Date:
    """Return a which-day-were-you-paid field bounded by the app's calendar.

    **Plan step ``pay_calendar:C14-e-3``, and the bound is not cosmetic.**
    The four doors that state a payday -- registration's *last payday*,
    ``/pay-periods/generate``'s *start date*, and regenerate's and reset's
    *corrected first payday* -- each declared a bare
    :class:`marshmallow.fields.Date` while every other persisted date in this
    application is held to
    :data:`~app.utils.dates.CALENDAR_DATE_MIN` ..
    :data:`~app.utils.dates.CALENDAR_DATE_MAX`, ``history_opens_on`` included
    (:func:`history_opens_on_field`, one function below).

    That was survivable while a payday was only compared and stored.  It stops
    being survivable when a payday is DISPLACED: the shift reads
    :func:`~app.utils.business_days.federal_holidays`, which computes the
    FOLLOWING year's New Year spillover, so a stated payday in year 9999 leaves
    ``datetime``'s own domain and raises ``ValueError`` -- not
    ``ValidationError`` -- out of the service tier and into a 500 on a public
    form.  ``is_business_day``'s docstring says the bound is stated rather than
    guarded because "no in-app caller can reach that"; ``C14-e-3`` made a
    caller that can, and an adversarial review of that step found it.  The
    remedy is the DOOR, not a guard in the calendar: bounding the input is what
    makes that sentence true again rather than fencing a state the door should
    never have admitted.

    **The window is the one every door that types a payday reads**,
    :data:`~app.schemas.validation._helpers._PAYDAY_RANGE` (task plan#3577):
    the salary and pay-stub doors' paydays take it too, so no two doors can
    come to accept different paydays for one calendar.  Until that task it
    read :data:`_HISTORY_OPENS_RANGE`, a stated history's window: the same
    calendar's reach, but a different fact's bound.

    Args:
        **kwargs: Forwarded to :class:`marshmallow.fields.Date` -- the
            per-schema half of the declaration (``required``, and
            registration's own ``error_messages``).

    Returns:
        The field, carrying the one payday window.
    """
    return fields.Date(validate=_PAYDAY_RANGE, **kwargs)


def history_opens_on_field(**kwargs) -> fields.Date:
    """Return a when-did-these-paychecks-start field bounded by the column's CHECK.

    Plan step **balance:X-bh-2**.  Two doors ask the question -- registration
    and the pay-periods settings section -- so the bound is declared once here
    beside the cadence and batch fields, for the reason the module docstring
    gives.

    ``allow_none`` is fixed rather than forwarded, because a blank answer is
    the field's ORDINARY value and means something: the owner has not stated a
    history, so the engine counts only their recorded paydays (ruling
    **balance:R-IA** as amended 2026-08-31).  A door that wanted it required
    would be asking a different question.  Its callers pair
    it with :func:`~app.schemas.validation._helpers._normalize_empty_inputs`,
    which is what turns an untouched HTML date input's ``""`` into that
    ``None`` rather than into "not a valid date".

    Args:
        **kwargs: Forwarded to :class:`marshmallow.fields.Date` -- the
            per-schema half of the declaration; see :func:`cadence_days_field`.

    Returns:
        The field, carrying the shared range validator.
    """
    return fields.Date(
        validate=_HISTORY_OPENS_RANGE, allow_none=True, **kwargs
    )


class PayPeriodGenerateSchema(DefaultedRhythmFields, BaseSchema):
    """Validates POST data for generating pay periods.

    The rhythm's wire keys are :class:`DefaultedRhythmFields`' (plan step
    ``pay_calendar:C17-d-3``): this door establishes a rhythm, so an old
    client posting no kind and no day count is read as it always was.
    """

    payday_control = "start_date"

    start_date = payday_field(required=True)
    num_periods = num_periods_field(
        load_default=BaseConfig.DEFAULT_PAY_PERIOD_HORIZON,
    )


class PayPeriodExtendSchema(BaseSchema):
    """Validates POST data for extending the schedule forward.

    **One field, and the deleted one is finding P29's fix** (plan step C3-b).
    ``cadence_days`` was accepted here, optional, and forwarded into
    ``extend_pay_periods`` -- while the extend card renders NO control for it.
    So a direct POST generated paychecks at a spacing the app never recorded:
    ``budget.pay_schedule`` still said 14, and ``resolve_cadence``, the derived
    horizon and the next rolling top-up all continued at 14.  Extend CONTINUES
    an existing schedule, so the cadence is not a question this door asks; the
    field is gone rather than newly persisted, which is what finding **P30**
    asked for.  ``BaseSchema``'s ``unknown = EXCLUDE`` means an old client that
    still posts one is not refused -- the value is simply ignored, which is now
    what it means.

    **"Add earlier paychecks" validates with it too** (plan step
    ``pay_calendar:C18-b``, ruling **R-PC87**: "asking only how many
    paychecks to add before your first"), for the same reason in the other
    direction: that door states no date and no rhythm either, so this one
    field is its whole question.
    """

    num_periods = num_periods_field(required=True)


class PayPeriodTruncateSchema(BaseSchema):
    """Validates POST data for truncating the schedule tail.

    ``keep_through_period_id`` names the last pay period to KEEP; every period
    opening after it is deleted.  ``confirm_discard`` acknowledges the loss of
    hand-entered / changed rows the discard gate would otherwise block on.

    **It names the period by ``id``, and plan step C3-a is why** (finding
    **P13**).  This field was ``keep_through_index``, a plain
    ``fields.Integer`` carrying the ORDINAL ``budget.pay_periods.period_index``
    -- so a user-supplied position selected which periods a CASCADE destroyed,
    and it survived a round trip through the browser in the discard-confirm
    422's hidden payload.  That was safe only while nothing renumbered, which
    is true today and which plan steps C3-b and C6 change; identity is ``id``,
    so the wire key is ``id``.

    A :class:`~app.schemas.validation._helpers.RowId` rather than an
    ``Integer``: it names a ROW, and the strict spelling rules that go with
    that (no ``"007"``, no ``" 12 "``, no ``1.9``) travel with the type.  The
    service still resolves the id against the submitter's OWN periods, because
    a well-formed id is not an owned one.
    """

    keep_through_period_id = RowId(required=True)
    confirm_discard = fields.Boolean(load_default=False)


class PayPeriodRemoveEarlierSchema(BaseSchema):
    """Validates POST data for removing the paychecks before a chosen one.

    Plan step ``pay_calendar:C21``, ruling **R-PC111**: "Remove earlier
    paychecks" names the paycheck to START FROM, by ``id``, for
    :class:`PayPeriodTruncateSchema`'s reason (finding **P13**) -- the field
    selects which periods a CASCADE takes, so it names the row the owner
    picked rather than a count or a position.  No ``confirm_discard``: the
    door refuses a paycheck holding anything the owner entered rather than
    asking to discard it (ruling **R-PC109**).
    """

    start_from_period_id = RowId(required=True)


class PayPeriodRegenerateSchema(RequiredRhythmFields, BaseSchema):
    """Validates POST data for regenerating the future tail.

    Mirrors the generate fields plus ``confirm_discard``, with the rhythm's
    wire keys REQUIRED (:class:`RequiredRhythmFields`) because regenerate
    persists the new rhythm, and a door that would silently restate one
    half on a missing input must not.

    **``confirm_gap`` was a SECOND confirmation here from plan step
    ``pay_calendar:C14-f`` until ``C17-c-2a`` DELETED it** (ruling
    **R-PC67**, closing ledger row **P80**): a batch whose first payday skips
    a whole paycheck of the owner's plan is REFUSED by the writer now, so
    there is no hole left for an owner to confirm.  An old client that still
    posts the field is not refused; the value is ignored, which is now what
    it means (finding **P29**'s disposition, one field over).
    """

    payday_control = "new_start_date"

    new_start_date = payday_field(required=True)
    num_periods = num_periods_field(required=True)
    confirm_discard = fields.Boolean(load_default=False)


class PayPeriodResetSchema(RequiredRhythmFields, BaseSchema):
    """Validates POST data for a full schedule reset (first-time setup).

    Mirrors the regenerate fields plus a required ``confirm`` acknowledgement
    (the rhythm required as it is there, for the same reason).  Unlike
    regenerate there is no ``confirm_discard``: reset wipes the WHOLE
    schedule -- including past and anchor periods -- by design, so its
    safety is the service's zero-settled refusal plus this explicit
    confirmation (an unchecked box submits nothing, hence
    ``load_default=False``; the route refuses an unconfirmed reset).
    """

    payday_control = "new_start_date"

    new_start_date = payday_field(required=True)
    num_periods = num_periods_field(required=True)
    confirm = fields.Boolean(load_default=False)


class PayHistorySchema(BaseSchema):
    """Validates POST data for the pay-history opening.

    Plan step **balance:X-bh-2**.  One optional field, and it is its own form
    rather than a field on the rolling-window one beside it: that form
    configures a WRITE (how far ahead to keep generating) and this states a
    fact about the owner, so pressing Save on either must not restate the
    other's answer.

    ``load_default=None`` and ``allow_none`` together mean a submission with
    the box cleared -- or with the field absent -- stores ``NULL``, which is
    how an owner WITHDRAWS a statement and returns to being counted from the
    record.  Clearing it is a real user action rather than a missing input,
    which is exactly the distinction
    :func:`~app.schemas.validation._helpers._normalize_empty_inputs` draws.
    """

    history_opens_on = history_opens_on_field(load_default=None)

    @pre_load
    def normalize_inputs(self, data, **kwargs):
        """Map the cleared date input's ``""`` to an explicit ``None``."""
        return _normalize_empty_inputs(self, data)


class PayScheduleSchema(BaseSchema):
    """Validates POST data for the continuous-rolling-window settings.

    ``rolling_enabled`` toggles continuous top-up (an unchecked checkbox
    submits nothing, hence ``load_default=False``).  ``rolling_target_periods``
    is how many current-and-future periods to keep generated ahead -- the
    count INCLUDES the current period.  It takes the generate / extend batch
    bound, which is the same question asked of a target rather than of a
    submission, and it sits inside the column's ``> 0`` CHECK.
    Cadence is NOT set here: it is owned by generate / regenerate.
    """

    rolling_enabled = fields.Boolean(load_default=False)
    rolling_target_periods = num_periods_field(required=True)
