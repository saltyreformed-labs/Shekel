"""
Shekel Budget App -- Which Marshmallow error a form flashes

One rule for "the schema refused this payload; what do we tell the user",
shared by the transaction-template and transfer-template CRUD routes, the
card APR and terms doors, and the salary profile's raise and payroll-line
doors.

It was one rule with two behaviours.  ``templates.py`` picked actionable
messages out of the errors dict; ``transfers/templates.py`` flashed a fixed
"Please correct the highlighted errors and try again." for everything -- so the
SAME schema refusal explained itself on one form and not on the other.  Plan
step R2e-2 made that asymmetry visible by moving the recurrence-pattern check
into the schema: the message the transfer routes had always flashed from the
route layer would otherwise have become the generic prompt.

**Why an allowlist rather than "flash the first error".**  Most field errors
are Marshmallow's own ("Not a valid integer."), and the widget that produced
them is already visible beside the input, so surfacing them adds noise without
adding information.  The listed keys are the ones whose message states a
BUSINESS rule the user cannot see from the form -- a cross-field validator, or
a value that is well-formed but names something this application does not
model.  Those are worth saying out loud.
"""
from typing import Any

from flask import Response, flash, request
from marshmallow import ValidationError
from marshmallow.exceptions import SCHEMA

from app.routes._redirect_target import RedirectTarget

#: Marshmallow error keys whose messages are flashed VERBATIM.
#:
#: * ``is_envelope`` -- ``validate_envelope_only_on_expense``, a cross-field
#:   rule ("Purchase tracking is only available for expense templates.").
#: * ``recurrence_unit`` -- ``RecurrenceUnitField``, which refuses a
#:   well-formed row id naming no cadence unit the application models, AND
#:   ``validate_authorable_cadence``, the cross-field rule that refuses a
#:   triple the closed pattern set cannot store ("That repeat schedule cannot
#:   be saved yet...").
#: * ``recurrence_placement`` -- ``PeriodPlacementField``, plus the same
#:   cross-field rule's refusal of a unit named with no placement.
#: * ``interval_n`` -- the same cross-field rule's refusal of a unit named with
#:   no INTERVAL ("Say how often this repeats..."), plan step R7c-c.  Unlike
#:   its two neighbours this one is NOT rare: the control is a free number box
#:   from that step, and clearing it is the ordinary way a user reaches the
#:   refusal.
#: * ``starts_on`` -- ``validate_recurrence_states_a_start``
#:   (:data:`~app.schemas.validation.RECURRENCE_NEEDS_A_START`), plan step
#:   R7c-b.  That constant exists because TWO layers raise it and they must say
#:   the same thing; only one of the two was audible.
#:   ``_recurrence_form_helpers.resolve_recurrence_rule_for_update`` flashes it
#:   DIRECTLY, so the update branch always said it -- while a CREATE, where the
#:   rule is a money decision (an unstated first occurrence backdated five rows
#:   of a $2,000.00 rent template into closed pay periods), raised it through
#:   the schema and reached the generic prompt.
#: * ``repeats_on`` -- ``validate_repeats_on_fits_the_start``, plan step
#:   R7c-b as ``nominal_day``, renamed at recurrence:R8-c when the control
#:   began offering weekday readings too.  Its message names every choice the
#:   chosen date allows, or says a schedule with no day of the month has none
#:   (ruling plan#2909: "Apr 15, 2026 cannot mean day 30. Starting on that
#:   date, this can repeat on the same day of the month or on the third
#:   Wednesday."), and no other layer says it, so the whole refusal was
#:   inaudible.
#:
#: **Those last three are the same defect a third, fourth and fifth time**, and
#: the R7c-c gate below is what stops a sixth: every field name
#: ``app.schemas.validation._recurrence`` raises a refusal against must be
#: listed here.  The arm that existed asked only the converse -- that no entry
#: names a field nothing declares -- so it caught a RENAME and was structurally
#: blind to an ADDITION, which is how each of these shipped.
#:
#: Field errors on these keys from the stock validators are rare in practice
#: (an HTML ``<select>`` submits only what it rendered) and remain acceptable
#: feedback when they do appear.
#:
#: **The two recurrence keys replaced ``recurrence_pattern`` at plan step
#: R7b-2, and an adversarial review is why they are here at all**: the step
#: split one field into two, authored three new refusal messages, and left the
#: allowlist naming a field no schema declares -- so every one of those
#: messages was dead copy, and a user whose cadence was refused got the generic
#: prompt after a redirect that highlights nothing.
#: * ``recurrence_end_mode`` / ``end_date`` / ``max_occurrences`` -- the three
#:   controls of the "Ends" bound (plan step R7b-3).  Each refusal names the
#:   CONTROL at fault rather than the one the user answered correctly: "you
#:   chose *ends on a date* and left the date blank" is attached to the date
#:   box, not to the mode select.
#:
#: **The bound's three arrived a review late, and the miss is this list's own
#: failure mode twice over.**  Plan step R7b-3 authored three sentences and
#: allowlisted none of them, so every one was dead copy and a user whose bound
#: was refused got the generic prompt after a redirect that highlights nothing
#: -- exactly what plan step R7b-2's adversarial review caught for
#: ``recurrence_pattern``.  Worse, the completeness gate could not see it: the
#: refusals live in a ``@post_load`` hook, and
#: ``tests/test_routes/test_form_errors.py``'s arm drove ``Schema.validate``,
#: which is ``_do_load(postprocess=False)`` and SKIPS post-load processors.
#: That arm loads now.
#:
#: ``tests/test_routes/test_form_errors.py`` pins the tuple against the schema
#: package BOTH ways, so neither a renamed field nor a new refusal can silence
#: its own message again.
#:
#: * ``max_per_month`` -- the per-month ceiling (plan step salary:R15-a),
#:   whose one authored refusal names a ceiling beside a monthly or yearly
#:   schedule; the gate below is what put it here in the same commit as the
#:   message, rather than a review late.
#: * ``target_account_id`` -- the payroll-line form's one side-specific
#:   refusal (plan step salary:R18-b): a target account on an EARNING kind,
#:   which funds nothing.  Listed in the same commit as the sentence, so it
#:   is heard rather than the generic prompt.
#:
#: **The raise form's three arrived with its doors' move onto this loader**
#: (task plan#3125): until then ``salary.add_raise`` and ``update_raise``
#: flashed the generic prompt for every refusal, so each sentence below was
#: dead copy, the leap-day one included although its docstring said "in
#: words".
#:
#: * ``raise_end_mode`` / ``terminal_year`` -- the raise's end-year answer
#:   (``validate_end_year``, through ``salary_raises.end_year_of``, ruling
#:   R-SAL22): unanswered on a recurring raise, a year missing where the
#:   answer asks one, an end before the start, or an end year on a one-time
#:   raise.  No other schema this loader serves declares either name.
#: * :data:`~marshmallow.exceptions.SCHEMA` -- marshmallow's WHOLE-FORM key
#:   (``"_schema"``), where a cross-field rule that belongs to no one control
#:   lands (ruling plan#3135): the raise's percentage-or-flat-amount rule, its
#:   leap-day rule (a raise that repeats every year cannot start on February
#:   29, ruling plan#3090: the Recur box and the date together, so ruling
#:   plan#3137 files it here), and the
#:   transfer template's same-account rule, which was silent too.  No
#:   stock validator writes there from a form post (a form is always a
#:   mapping), so every message under it is authored.  LAST, so a refusal
#:   that names its control is the one said when both are present.
ACTIONABLE_FLASH_FIELDS: tuple[str, ...] = (
    "is_envelope",
    "recurrence_unit",
    "recurrence_placement",
    "interval_n",
    "starts_on",
    "repeats_on",
    "max_per_month",
    "recurrence_end_mode",
    "end_date",
    "max_occurrences",
    "target_account_id",
    "raise_end_mode",
    "terminal_year",
    SCHEMA,
)

GENERIC_VALIDATION_FLASH: str = (
    "Please correct the highlighted errors and try again."
)
"""What a form says when no error carries a message worth repeating."""


def flash_message_for_errors(errors: dict[str, Any]) -> str:
    """Pick the user-facing flash message from a Marshmallow errors dict.

    Args:
        errors: A Marshmallow errors dict -- ``ValidationError
            .normalized_messages()``.

    Returns:
        The first :data:`ACTIONABLE_FLASH_FIELDS` message present, else
        :data:`GENERIC_VALIDATION_FLASH`.  Always non-empty.
    """
    for field in ACTIONABLE_FLASH_FIELDS:
        messages = errors.get(field)
        if isinstance(messages, list) and messages:
            return str(messages[0])
    return GENERIC_VALIDATION_FLASH


def load_form_or_redirect(
    schema: Any, redirect: RedirectTarget,
) -> dict[str, Any] | Response:
    """Load ``request.form`` through *schema*, flashing and redirecting on failure.

    The whole "refuse a bad payload, or hand back the good one" step of a CRUD
    route, in one place.  All four template-CRUD routes ran it inline; once
    plan step R2e-2 gave the two transfer routes the same message-picking as
    the two transaction routes, the four copies became textually identical and
    ``duplicate-code`` said so.  Extracting is the honest answer to that, not a
    one-sided disable: the step genuinely is one rule, and having one door
    means a future route cannot validate a payload and then flash something
    else.

    **It LOADS rather than validating-then-loading, since plan step R7b-3**,
    and the change fixes two things at once.

    Every caller ran ``schema.validate(request.form)`` here and then
    ``schema.load(request.form)`` on the next line, which is the whole
    pre-load, field-deserialization and cross-field pass run TWICE for one
    submission -- the redundant producer call this project treats as a DRY
    violation rather than a cost question.

    Worse, the two passes do not refuse the same things.  ``Schema.validate``
    is ``_do_load(postprocess=False)``: it SKIPS ``@post_load``, so a refusal
    raised there was invisible here and then escaped from the caller's
    ``load`` as an unhandled 500.  Measured on plan step R7b-3's own
    ``compose_end_bound``, which turns "you chose *ends on a date* and left the
    date blank" into a field error: it flashed nothing and 500'd.  One
    ``load`` in one ``try`` is what makes a refusal's placement in the schema
    stop mattering to the route.

    Args:
        schema: The Marshmallow schema to load ``request.form`` through.
        redirect: Where to send the user when the payload is refused --
            typically back to the form they submitted.

    Returns:
        The deserialized payload when it is valid, or a Flask redirect
        :class:`Response` when it is not.  The caller distinguishes them with
        ``isinstance(result, Response)``, the idiom
        ``recurrence_spec_from_form`` already uses for the same
        two-outcome shape.
    """
    try:
        return schema.load(request.form)
    except ValidationError as exc:
        flash(flash_message_for_errors(exc.normalized_messages()), "danger")
        return redirect.to_response()


__all__ = [
    "ACTIONABLE_FLASH_FIELDS",
    "GENERIC_VALIDATION_FLASH",
    "flash_message_for_errors",
    "load_form_or_redirect",
]
