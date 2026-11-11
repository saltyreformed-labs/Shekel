"""What a recurrence form's validated payload BECOMES: one typed submission.

The wire keys the recurrence controls post under, and
:func:`consume_recurrence_submission`, the ONE reading of them into a
:class:`~app.services.recurrence.RecurrenceSubmission` (plan step R7e, finding
**D36**), which
:meth:`~app.schemas.validation._recurrence.RecurrenceFormFieldsMixin.build_recurrence_submission`
hands every route under :data:`RECURRENCE_SUBMISSION_KEY`.

Split out of :mod:`._recurrence` at plan step R7e, when the consumer took that
module past the 1,000-line cap.  The seam is "what the payload says" against
"which payloads are refused": :mod:`._recurrence` declares the fields and
every rule that REFUSES a submission, and imports its keys from here; this
module refuses nothing, which is also what keeps
``tests/test_routes/test_form_errors.py``'s census of that module's refusals
whole.
"""
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from app.services.recurrence import RecurrenceSubmission


#: The ONE key a route reads off a recurrence form's loaded payload (plan step
#: R7e): the schema's ``@post_load``
#: (:meth:`~._recurrence.RecurrenceFormFieldsMixin.build_recurrence_submission`)
#: consumes every recurrence control into one
#: :class:`~app.services.recurrence.RecurrenceSubmission` and hands it over
#: under this key, so no recurrence control's own key reaches a route at all.
#:
#: **That is what closes finding D36.**  Until this step each door told a
#: cleared field from an unmentioned one by testing the payload for the
#: field's KEY, site by site -- the start, the reading, the ceiling, the bound
#: and the unit, in six places -- and a site that forgot erased what it should
#: have kept.  A value with no key to test cannot be read that way, and the
#: wire meaning of each field is stated once, in
#: :func:`consume_recurrence_submission`.
RECURRENCE_SUBMISSION_KEY: str = "recurrence"

#: The rule's FIRST OCCURRENCE, as the payload spells it, and how the "repeating
#: on" control reads it.  The per-month ceiling's (plan step salary:R15-a) and
#: the closing bound's, below, are named for the same reason.
#:
#: **Named because three things must spell one string**: the field
#: declaration and the cross-field rules that read it (:mod:`._recurrence`),
#: and :func:`consume_recurrence_submission`, which reads the key's PRESENCE --
#: and
#: presence is how a form says "not mine to state", so a consumer spelling a
#: different string would read a locked control's silence as a stated value.
#: Until plan step R7e the route helpers read these keys too; since then the
#: only readers outside the schema are the two route doors that word
#: :data:`~._recurrence.RECURRENCE_NEEDS_A_START`, which is keyed by the
#: start: the update dispatcher's authoring branch
#: (``_recurrence_form_helpers.resolve_recurrence_rule_for_update``) and the
#: transfer create door (``_loan_destination.settle_first_occurrence``).
#:
#: **``repeats_on`` replaced ``nominal_day`` at plan step recurrence:R8-c**
#: (developer ruling 2026-11-02, the rename approved with its tests): the
#: control offers the weekday readings beside the clamped month-end days, and
#: one ``<select>`` posts one key, so a nominal day beside a weekday reading
#: (``ck_recurrence_rules_one_month_reading``) is a pair no form can post.
#: The schema replaces the token with the
#: :class:`~app.services.recurrence.MonthReading` it names, under the same
#: key, as :func:`~app.schemas.validation._recurrence.compose_end_bound` does for the bound.
RECURRENCE_STARTS_ON_KEY: str = "starts_on"
RECURRENCE_REPEATS_ON_KEY: str = "repeats_on"
RECURRENCE_MAX_PER_MONTH_KEY: str = "max_per_month"

#: The key a form posts its closing-bound SHAPE under, and the key the
#: composed bound sits under until :func:`consume_recurrence_submission` takes
#: it.
#:
#: They are the same field: :func:`~._recurrence.compose_end_bound` replaces
#: the submitted token with the value it names, the way
#: :class:`~._recurrence.RecurrenceUnitField` replaces a submitted id with its
#: enum member.
#: One key rather than two is what keeps "the form said nothing about the
#: bound" expressible -- an ABSENT key, which a loan payment's form and an
#: amount-only PATCH both produce, and which the submission carries as
#: "leave the stored bound alone".
RECURRENCE_END_BOUND_KEY: str = "recurrence_end_mode"


def consume_recurrence_submission(data: dict[str, Any]) -> "RecurrenceSubmission":
    """Consume every recurrence control in *data* into the ONE value it states.

    **Each field's wire meaning is stated here and nowhere else** (plan step
    R7e, finding **D36**).  A recurrence field has three meanings -- stated,
    cleared, not mentioned -- and HTML form data has two states, so the third
    is carried by whether the KEY is present, which
    :func:`~app.schemas.validation._helpers._normalize_empty_inputs` decides
    by whether the field is ``allow_none``.  Read once, here:

    * ``recurrence_unit`` -- ABSENT is a form that did not mention the
      cadence (an amount-only PATCH, a page that posts no cadence controls):
      :data:`~app.services.recurrence.UNSET`, which leaves the stored rule
      alone.  A present ``None`` is "Does not repeat", which clears it.  A
      unit names a :class:`~app.services.recurrence.SubmittedCadence` with
      the interval and the placement
      :func:`~app.schemas.validation._recurrence.validate_authorable_cadence` has
      required beside it -- so they are popped with NO default, and a payload
      missing one is a broken invariant rather than a submission -- and the
      per-month ceiling, which keeps all three states: absent (its control is
      disabled beside a unit that cannot repeat within a month), a present
      ``None`` (a cleared box), or a count.  Beside an absent or empty unit
      the axis keys are consumed and applied nowhere: a submission that names
      no cadence authors none.
    * ``starts_on`` -- absent is UNSET, and there is no cleared state: the
      column is ``NOT NULL`` and the field is not ``allow_none``, so an
      emptied box is dropped before the schema sees it and reads exactly as a
      locked control does.
    * ``repeats_on`` -- absent is UNSET.  The control's default option, "the
      same day of the month", posts the empty string, which is dropped.
    * the closing bound -- an absent mode is UNSET; a present one is the
      :class:`~app.services.recurrence.EndBound`
      :func:`~app.schemas.validation._recurrence.compose_end_bound` composed.
      ``end_date`` and ``max_occurrences`` are consumed either way: beside no
      mode they are inputs no shape named.

    **Consumed, not copied**: every key above leaves *data*, so nothing a
    route hands its model constructor or its field loop is a recurrence key.
    That property was a pop-every-key fence in the route helpers until this
    step, and a key it missed (``starts_on``, on the "Does not repeat"
    branch) was a 500 the browser harness caught with the suite green.

    Args:
        data: The payload AFTER
            :func:`~app.schemas.validation._recurrence.compose_end_bound`, mutated in
            place.

    Returns:
        The :class:`~app.services.recurrence.RecurrenceSubmission`.
    """
    # Pylint: ``import-outside-toplevel`` -- see
    # :meth:`~app.schemas.validation._recurrence.RecurrenceUnitField._member_for`.
    from app.services.recurrence import (  # pylint: disable=import-outside-toplevel
        UNSET,
        RecurrenceSubmission,
        SubmittedCadence,
    )

    unit = data.pop("recurrence_unit", UNSET)
    if unit is UNSET or unit is None:
        cadence = unit
        for key in ("interval_n", "recurrence_placement", RECURRENCE_MAX_PER_MONTH_KEY):
            data.pop(key, None)
    else:
        cadence = SubmittedCadence(
            unit=unit,
            interval_n=data.pop("interval_n"),
            placement=data.pop("recurrence_placement"),
            max_per_month=data.pop(RECURRENCE_MAX_PER_MONTH_KEY, UNSET),
        )
    data.pop("end_date", None)
    data.pop("max_occurrences", None)
    return RecurrenceSubmission(
        cadence=cadence,
        starts_on=data.pop(RECURRENCE_STARTS_ON_KEY, UNSET),
        reading=data.pop(RECURRENCE_REPEATS_ON_KEY, UNSET),
        end_bound=data.pop(RECURRENCE_END_BOUND_KEY, UNSET),
    )


__all__ = [
    "RECURRENCE_END_BOUND_KEY",
    "RECURRENCE_MAX_PER_MONTH_KEY",
    "RECURRENCE_REPEATS_ON_KEY",
    "RECURRENCE_STARTS_ON_KEY",
    "RECURRENCE_SUBMISSION_KEY",
    "consume_recurrence_submission",
]
