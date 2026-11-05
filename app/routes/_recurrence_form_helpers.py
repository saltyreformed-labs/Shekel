"""
Shekel Budget App -- Recurrence-Form Route Helpers (F-24, F-26)

What a definition's recurrence rule IS after a form submit, for the
transaction-template (:mod:`app.routes.templates`) and transfer-template
(:mod:`app.routes.transfers`) CRUD routes -- and, since plan step
salary:R15-c, for the paycheck-deduction routes
(:mod:`app.routes.salary.items`), the third owner the arc admits, which
settle the two facts their form does not collect into the payload and then
run the same create preamble and update dispatcher.  What happens to the ROWS that rule
already generated is the sibling module
:mod:`app.routes._recurrence_conflict_chooser`, split out at plan step R2e-1
when this one reached the 1,000-line cap.  What an EDIT form's controls START
ON is :mod:`app.routes._recurrence_form_render`, split out at plan step R7b-4
when it reached that cap a second time -- the seam being SUBMISSION versus
STORED STATE: everything here reads a posted payload and writes, everything
there answers a read-only question about a definition already saved.

The helpers:

* :func:`recurrence_spec_for_create` -- the whole create-form
  preamble both kinds run: lift the closing bound out of the payload, build
  the context, read the spec.  Hoisted at plan step R7b-4, which is when it
  became hoistable;
* :func:`author_recurrence_for_create` -- its WRITE half, which plan step
  R-F6 separated from it: the rule is authored onto the definition, so it
  cannot be written until the definition exists and the two halves run either
  side of the caller's model constructor;
* :func:`recurrence_spec_from_form` -- consumes a Marshmallow-
  validated payload, pops the recurrence-related keys, and returns the
  :class:`~app.services.recurrence.RecurrenceSpec` it states or ``None`` when
  no cadence was selected.  It WROTE the rule until plan step R-F6, which
  moved the owning FK onto ``budget.recurrence_rules`` -- so a rule cannot be
  written before the definition that owns it exists, and reading the
  submission is now a separate step from writing it.  [F-24]
* :func:`update_recurrence_rule_from_form` -- sibling of the builder
  for the cadence-changed-on-an-existing-rule branch: re-points the
  template's current :class:`RecurrenceRule` in place (preserving its
  id and the owning FK), pops the recurrence keys, and returns
  ``None``; it cannot fail.  [F-24]
* :func:`resolve_recurrence_rule_for_update` -- dispatches the three
  update-form branches (re-point an existing rule, build + link a new
  one, or CLEAR the recurrence the user set to "Does not repeat") so
  each ``update_*`` route resolves its recurrence rule with a single
  call.  [F-24; the clear branch is plan step R2e-1]

The first three helpers share a verbatim pair of inputs -- the form's
closing bound and the validation-error redirect target -- bundled into the
frozen :class:`~app.routes._recurrence_form_refusals.RecurrenceFormContext`,
which is DEFINED in the refusals module since plan step R7d-f (see there for
why the leaf moved) and imported here.

**Everything about OPTIMISTIC LOCKING left at plan step R7c-b**, which is the
third time this module met the 1,000-line cap and the first time the cut was
available for free: ``handle_stale_form_conflict`` and the two
``STALE_*_MESSAGE`` templates moved to :mod:`app.routes._commit_helpers`,
where the guard's own commit-time siblings (``commit_or_handle_stale``,
``handle_stale_conflict``) had already gone for the same reason.  None of the
three was about recurrence -- the savings-goal form and the amount-version
actions imported them from here, across a boundary that had nothing to say
about them.

**Every REFUSAL left at the same step**, when the inverted-window door made a
fourth one and the module met the cap again:
:mod:`app.routes._recurrence_form_refusals` holds the three refusal messages,
:func:`~app.routes._recurrence_form_refusals.is_loan_payment`,
:func:`~app.routes._recurrence_form_refusals.refuse_inverted_window` and the
:func:`~app.routes._recurrence_form_refusals.refuse_recurrence_update`
dispatcher that asks them in order.  The seam is "may this submission be
applied at all" against "apply it", and it runs ONE way -- that module imports
nothing from this one.  Since plan step R7d-f the context both sides read is
defined THERE, so each refusal entry takes it whole, with the READ PASS beside
it: the update path judges a stored definition against the pass the route
built before any write (``pass_ctx``), and the calendar every re-author here
resolves against is taken from that pass rather than loaded by the helper.

Route-layer module rather than service because these helpers consume
Flask ``flash`` / ``redirect`` / ``url_for`` (the last two via
:class:`~app.routes._redirect_target.RedirectTarget`);
``CLAUDE.md::Architecture`` keeps services isolated from Flask globals.
The leading underscore marks the module as route-internal.
"""
from dataclasses import replace
from typing import Any

from flask import Response, flash

from app.extensions import db
from app.models.recurrence_rule import RecurrenceRule
from app.routes._redirect_target import RedirectTarget
from app.routes._recurrence_form_refusals import (
    RecurrenceFormContext,
    refuse_recurrence_update,
)
from app.schemas.validation import (
    RECURRENCE_END_BOUND_KEY,
    RECURRENCE_MAX_PER_MONTH_KEY,
    RECURRENCE_NEEDS_A_START,
    RECURRENCE_REPEATS_ON_KEY,
    RECURRENCE_STARTS_ON_KEY,
    end_bound_before_start_message,
)
from app.services.balance_at import BalanceContext
from app.services.pay_calendar import PayCalendar, calendar_for
from app.services.recurrence import (
    NEVER_ENDS,
    EmptyAuthoredWindowError,
    MonthReading,
    RecurrenceOwner,
    RecurrenceSpec,
    author_rule,
    reauthor_rule,
    recurrence_spec_with_cadence,
)

# Keys the recurrence-rule helper pops from the validated form payload
# regardless of whether a pattern was selected.  Listed here as
# module-level constants so the "drop every recurrence key" logic
# stays in one place.

_BASE_RECURRENCE_KEYS: tuple[str, ...] = (
    "recurrence_placement",
    "interval_n",
    RECURRENCE_REPEATS_ON_KEY,
    RECURRENCE_MAX_PER_MONTH_KEY,
)

# The closing bound's three controls (plan step R7b-3).
# ``recurrence_end_mode`` carries the COMPOSED bound after the schema's
# ``@post_load``, which also consumes the two value keys -- but ONLY when a
# mode was submitted.  A submission that named none (a loan payment's disabled
# control, an amount-only PATCH, a crafted POST, a page cached from before this
# deploy) leaves the two values in the payload, so they are dropped here.
#
# **Popped on every branch, from ``resolve_recurrence_rule_for_update``'s own
# entry**, and an adversarial review of this step is why: they were dropped in
# the builder alone, so the re-point branch carried ``end_date`` through to the
# caller's field loop and was saved only by that route's separate ``setattr``
# allowlist -- a second, unrelated guard standing in for a contract this
# module's own comment claimed to hold.
_END_BOUND_KEYS: tuple[str, ...] = (
    RECURRENCE_END_BOUND_KEY,
    "end_date",
    "max_occurrences",
)


def _pop_end_bound_keys(data: dict[str, Any]) -> None:
    """Drop the closing bound's three form keys from *data*.

    Args:
        data: The validated payload, mutated in place.
    """
    for key in _END_BOUND_KEYS:
        data.pop(key, None)

def recurrence_spec_for_create(
    data: dict[str, Any],
    *,
    user_id: int,
    redirect: RedirectTarget,
) -> RecurrenceSpec | None:
    """Run the whole create-form recurrence preamble, for either kind.

    The one call ``templates.create_template`` and
    ``transfers.create_transfer_template`` each make.  It reads the closing
    bound out of the payload -- the schema's ``@post_load`` composed it into
    ONE value under the mode key, and the route has to lift it before the
    builder pops the rest -- assembles the context, and reads the spec.

    **It returns what the form AUTHORS rather than a written row, since plan
    step R-F6**, and the split is what lets the definition come first.  A rule
    is owned by its definition now (``budget.recurrence_rules`` carries the
    FK), so it cannot be written before there is a definition to own it -- and
    the recurrence keys still have to leave ``data`` before the caller's model
    constructor sees them.  Reading the submission and writing the row were one
    step; they are two, in that order, with the definition created between
    them.  The route no longer links anything: :func:`author_rule` does.

    **It exists because plan step R7b-4 made hoisting possible, and the
    ``duplicate-code`` gate is what said so.**  The two routes ran a
    byte-identical preamble and carried a one-sided suppression whose stated
    reason was that it could NOT be hoisted: the transfers side reused
    ``start_period_id`` afterwards, for its one-time-transfer branch, so a
    wrapper popping it internally would have had to thread it back out.  That
    field is the transfer form's alone now -- its recurrence meaning became a
    date -- and it is read ABOVE this call there, which leaves the two
    preambles identical with nothing left to thread.  The suppression is
    deleted rather than moved.

    Args:
        data: Marshmallow-validated payload; mutated in place.  Every
            recurrence key is popped, including the closing bound's three.
        user_id: Owner of the resulting :class:`RecurrenceRule` row.
        redirect: Where a recoverable failure would send the user.  Carried
            into the context rather than read: neither this function nor
            :func:`recurrence_spec_from_form` has a failure left, and
            the field is on the context because
            :func:`resolve_recurrence_rule_for_update` does.

    Returns:
        The :class:`~app.services.recurrence.RecurrenceSpec` the form states,
        or ``None`` when the form said "Does not repeat".
    """
    return recurrence_spec_from_form(
        data,
        user_id=user_id,
        ctx=RecurrenceFormContext(
            # ABSENT when the form stated no bound -- a disabled control, or a
            # partial update -- which the helpers read as "leave the stored one
            # alone" (plan step R7b-3).  On a CREATE there is no stored bound,
            # so the builder reads it as "ends never".
            end_bound=data.pop(RECURRENCE_END_BOUND_KEY, None),
            redirect=redirect,
        ),
    )


def author_recurrence_for_create(
    spec: RecurrenceSpec | None,
    template: RecurrenceOwner,
    *,
    redirect: RedirectTarget,
    calendar: PayCalendar | None = None,
) -> RecurrenceRule | Response | None:
    """Write the create form's cadence onto the definition it belongs to.

    The WRITE half of :func:`recurrence_spec_for_create`, and the pair is what
    the create routes run in place of the one call they made before plan step
    R-F6.  A rule carries its owner's FK now, so it cannot be written until the
    definition exists -- reading the submission has to happen before the model
    constructor (the recurrence keys must leave the payload) and writing it has
    to happen after.  Both create routes therefore make the same two calls with
    the same rows created between them, and this is the second.

    Called AFTER the caller's flush, which matters on the transfer side:
    :func:`app.services.recurrence.author_rule` flushes, and a flush ahead of
    ``flush_template_or_namedup_redirect`` would surface a duplicate template
    name as an unhandled ``IntegrityError`` rather than that helper's redirect.

    Args:
        spec: What the form authored, or ``None`` when it said "Does not
            repeat".
        template: The just-created definition that owns the cadence -- the
            :data:`~app.services.recurrence.RecurrenceOwner` union the write
            door takes, stated rather than ``Any`` so the two agree on what may
            own a rule.  Mutated: its ``recurrence_rule`` is set.  Its
            ``user_id`` is what the owner's pay calendar is loaded for, so the
            rule and the schedule it resolves against cannot name different
            owners.
        redirect: Where the one refusal here sends the user (the create form).
        calendar: The owner's pay calendar when the caller already holds one
            -- the salary line door's read pass does (plan step salary:R18-c),
            and a second derivation of the same calendar in one request is
            the redundant producer call this project treats as a defect --
            else ``None`` and it is loaded for ``template.user_id`` here.

    Returns:
        The flushed :class:`RecurrenceRule`; ``None`` when *spec* is ``None``
        -- which the callers read as "this definition does not repeat" and
        use to skip generation; or a redirect :class:`Response` when the
        write door refused the pair it would store, with the pending create
        rolled back and the doors' own sentence flashed.

    **The refusal is the write door's, worded here** (plan step R7d-g).  The
    schema grades the stop against the AUTHORED start; the door stores the
    NORMALISED one, and for an every-paycheck cadence authored entirely before
    the owner's first payday the two part -- the stored pair would invert and
    ``ck_recurrence_rules_valid_window`` would answer the flush with a 500.
    The door refuses off the stored pair (:class:`~app.services.recurrence
    .EmptyAuthoredWindowError`) and this says so in the sentence the schema
    would have used, naming the first occurrence the rule would actually have.
    Rolled back because the definition is already flushed by now (the
    name-collision helper runs first), and a refusal that left it behind would
    leave a definition that generated nothing.
    """
    if spec is None:
        return None
    try:
        return author_rule(
            spec,
            calendar if calendar is not None else calendar_for(template.user_id),
            template,
        )
    except EmptyAuthoredWindowError as refused:
        db.session.rollback()
        flash(
            end_bound_before_start_message(refused.end_date, refused.starts_on),
            "danger",
        )
        return redirect.to_response()


def recurrence_spec_from_form(
    data: dict[str, Any],
    *,
    user_id: int,
    ctx: RecurrenceFormContext,
) -> RecurrenceSpec | None:
    """Read a :class:`RecurrenceSpec` out of a validated form payload.

    Pops every recurrence-related key from ``data`` so the caller's
    downstream ``TransactionTemplate`` / ``TransferTemplate``
    constructor does not receive stray kwargs, and states what the
    submission authors.  Writing it is
    :func:`app.services.recurrence.author_rule`'s -- the one door that
    resolves both of the table's cadence vocabularies together -- which the
    caller reaches once the owning definition exists (plan step R-F6).

    **It cannot fail on user input, since plan step R7b-4**, and the
    signature says so: it used to owner-check a submitted
    ``start_period_id`` and return a redirect :class:`Response` when the
    period was not this user's.  The recurrence no longer has a start
    PERIOD -- its opening bound is a date -- and the one caller that still
    submits one does so for its own non-recurring job, so that check moved
    to the route that owns the field
    (``transfers.create_transfer_template``).  A kind-agnostic helper
    checking a field only one kind submits was the coupling; moving it
    removed the coupling and the failure mode together.

    **It does NOT validate the cadence.**  That the submitted axes name values
    the application MODELS -- narrower than "name ``ref`` rows", and the
    difference is a 500 -- is a property of the SUBMISSION, so it belongs to
    the submission's validator:
    :class:`~app.schemas.validation._helpers.RecurrenceUnitField` and
    :class:`~app.schemas.validation._helpers.PeriodPlacementField` refuse it
    before any route code runs (plan step R2e-2 on the pattern field those two
    replaced, developer ruling 2026-08-07).  That the whole
    ``(interval, unit, placement)`` TRIPLE can be stored is a property of no
    single field, so it is
    :func:`~app.schemas.validation._helpers.validate_authorable_cadence`'s.
    The check used to live here AND in
    :func:`update_recurrence_rule_from_form` -- one rule written twice, which
    a third caller would have had neither copy of.

    Args:
        data: Marshmallow-validated payload; mutated in place.  The
            helper pops ``recurrence_unit``, ``recurrence_placement``,
            ``interval_n``, ``repeats_on``, ``max_per_month``,
            ``starts_on``, and the closing
            bound's three (:data:`_END_BOUND_KEYS`).
        user_id: Owner of the resulting :class:`RecurrenceRule` row.
        ctx: The :class:`RecurrenceFormContext` carrying the form's
            ``end_bound`` (written onto the rule, or ``NEVER_ENDS`` when the
            form stated none).  Its ``redirect`` is unused here now
            that the helper has no failure, and kept only because the three
            helpers share one context object.

    Returns:
        * :class:`~app.services.recurrence.RecurrenceSpec` -- what the
          submission authors, ready to write onto a definition.  Nothing is
          added to the session here and no owning FK is left for the caller to
          set: the rule's owner is an argument to
          :func:`~app.services.recurrence.author_rule`.
        * ``None`` -- no recurrence pattern was selected, so the template does
          not repeat; the helper still popped every recurrence key from
          ``data``.
    """
    unit = data.pop("recurrence_unit", None)

    if unit is None:
        # Does not repeat: drop every recurrence-related key so the caller's
        # model constructor does not receive stray kwargs.
        #
        # **``starts_on`` is popped HERE as well as below, and leaving it out
        # was a 500** -- caught by the browser drive
        # (``tests/manual/verify_recurrence_form.py``) with the whole pytest
        # suite green, which is the case that mandate exists for.  The box is
        # hidden with ``#recurrence-fields`` when the form says "does not
        # repeat", and a hidden input still SUBMITS: the payload carried
        # ``starts_on`` through this early return into
        # ``TransactionTemplate(**data)``, whose constructor has no such
        # keyword.  No test payload included the key on this branch, because
        # every one of them was written by hand; a browser posts every control
        # the page renders.
        for key in _BASE_RECURRENCE_KEYS:
            data.pop(key, None)
        _pop_end_bound_keys(data)
        data.pop(RECURRENCE_STARTS_ON_KEY, None)
        return None

    placement = data.pop("recurrence_placement")
    # NO default, exactly like the placement above: all three axes of the
    # cadence are required beside a chosen unit and
    # :func:`~app.schemas.validation._helpers.validate_authorable_cadence`
    # refuses a submission missing any of them.  It defaulted to ``1`` until
    # plan step R7c-c, which is the value a cleared interval box authored --
    # "Repeats: Months, every [blank]" became a MONTHLY rule rather than a
    # field error.
    interval_n = data.pop("interval_n")
    # Pop the closing bound's keys even though the value comes from
    # ``ctx.end_bound`` -- keeps the "all recurrence keys removed from data"
    # contract symmetric between the repeats and does-not-repeat branches, so
    # the caller's downstream model constructor never receives one as a stray
    # kwarg.
    _pop_end_bound_keys(data)
    # How the first occurrence is READ -- its day of the month, a clamped
    # month-end day, or its weekday (plan step recurrence:R8-c) -- as the ONE
    # value the schema composed from the "repeating on" control.  Absent is
    # the control's default, the same day of the month.
    reading = data.pop(RECURRENCE_REPEATS_ON_KEY, MonthReading())
    # The rule's FIRST OCCURRENCE (plan step R7c-b).  Popped unconditionally,
    # like every other recurrence key, so the caller's model constructor never
    # sees it.
    #
    # **Required by the SCHEMA whenever a cadence is chosen**
    # (``RecurrenceFormFieldsMixin.validate_recurrence_states_a_start``), so an
    # absent key here is a payload no form can produce.  It reaches the write
    # door as a stated ``None`` and ``RecurrenceSpec`` names the field in its
    # refusal, which is the honest disposition for a broken invariant: the
    # empty state this replaced meant "start with the schedule" and generated
    # five backdated rows into pay periods that had already closed.
    starts_on = data.pop(RECURRENCE_STARTS_ON_KEY, None)
    # The cadence's third value (plan step salary:R15-a).  Popped like the
    # interval beside it; ``None`` -- a cleared box, or a control the form
    # disabled beside a calendar-month unit -- authors no ceiling.
    max_per_month = data.pop(RECURRENCE_MAX_PER_MONTH_KEY, None)

    # The offset auto-derivation this branch used to run inline -- "for an
    # every-N-paychecks rule, phase it on the chosen start period" -- moved
    # into ``resolve``, which applies it on EVERY write rather than only on
    # create.  That is what closes defect D1: the update path had no such
    # derivation and wrote the schema default instead, re-phasing every
    # future occurrence on an amount-only edit.
    #
    # **No decode step, since plan step R7b-2**: the form POSTS the two axes,
    # so what the user authored reaches the write door unchanged.  The
    # translation that used to sit here read a submitted pattern id back into a
    # cadence, which made the form's vocabulary and the door's differ by one hop
    # for no reason once the picker could state the cadence itself -- and plan
    # step R7c-c deleted the encoding on the far side too, so the triple this
    # call states is now what the columns hold.
    #
    # ``offset_periods`` is not read from the payload at all -- the schemas no
    # longer declare it (defect D8) and the spec no longer carries it (plan
    # step R7b-4) -- so ``resolve`` derives the phase from the opening bound
    # this call states.
    return RecurrenceSpec(
        user_id=user_id,
        unit=unit,
        starts_on=starts_on,
        interval_n=interval_n,
        placement=placement,
        nominal_day=reading.nominal_day,
        weekday_reading=reading.weekday_reading,
        max_per_month=max_per_month,
        # A create form that stated no bound authors an UNBOUNDED rule:
        # there is no stored bound to leave alone, so absence and "never"
        # are the same request here and only here.
        end_bound=(
            NEVER_ENDS if ctx.end_bound is None else ctx.end_bound
        ),
    )


def update_recurrence_rule_from_form(
    rule: RecurrenceRule,
    data: dict[str, Any],
    *,
    ctx: RecurrenceFormContext,
    calendar: PayCalendar,
) -> None:
    """Re-point an existing :class:`RecurrenceRule` from a form payload.

    Sibling of :func:`recurrence_spec_from_form` for the
    pattern-changed-on-an-existing-rule branch of the ``update_*``
    routes.  When a template already owns a rule, the edit mutates
    that same row in place -- preserving its primary key and its owning
    arc -- rather than creating a
    new rule, then pops every recurrence key from ``data`` so the
    caller's downstream ``setattr`` loop never sees a stray kwarg.

    **It re-authors rather than assigns, and the difference is two
    closed defects.**  The pre-seam version wrote the payload onto the
    rule field by field, so what the form did not collect was written
    with the schema's DEFAULT rather than left alone -- that is defect
    **D1**: ``offset_periods`` went to 0 on an amount-only edit,
    re-phasing every future occurrence of an ``Every N Periods`` rule by
    one pay period.  Reading the rule's authored state back
    (:func:`app.services.recurrence.recurrence_spec`), replacing only the
    fields this form owns, and writing the whole value means the rule's
    start period still phases it and nothing the form does not collect is
    reset to a schema default.

    **The one field that still carried a schema default was ``interval_n``,
    and plan step R7c-c removed it.**  It is the same defect one field over:
    a cleared interval box drops the key, this helper defaulted to ``1``, and
    a quarterly bill re-cadenced to monthly on save.  It is refused at the
    submission now rather than merged here -- see the inline comment on the
    pop for why a fourth presence read would have been the wrong door.

    Args:
        rule: The existing :class:`RecurrenceRule` to mutate in place.
            The caller guarantees it is non-``None`` (the branch guard
            tests ``template.recurrence_rule``).
        data: Marshmallow-validated payload; mutated in place.  Pops
            ``recurrence_unit``, ``recurrence_placement``, ``interval_n``,
            ``repeats_on``, ``max_per_month`` and ``starts_on``.
        ctx: The :class:`RecurrenceFormContext` carrying the form's
            ``end_bound`` (which REPLACES the rule's when stated and leaves it
            alone when ``None``).  Its ``redirect`` is unused here and
            kept only because the three helpers share one context object.
        calendar: The OWNER's pay calendar the re-author resolves against.
            TAKEN since plan step R7d-f rather than loaded here: the update
            route builds one read pass for the refusals that precede this
            write, and its ``calendar()`` memo is that schedule already
            derived, so the pre-write side of an update derives it once.
            (Regeneration afterwards builds a fresh pass and derives its own,
            as it did before; this step removed no load from that side.)

    Returns:
        ``None``.  **It cannot fail on user input**, which is what plan step
        R2e-2 changed: the one failure it used to have -- an unmodelled cadence
        -- is refused by the schema's axis fields and by
        :func:`~app.schemas.validation._helpers.validate_authorable_cadence`
        before the route reads the payload, so there is no redirect left to
        return and the signature says so.  It still RAISES
        ``RecurrenceResolutionError`` for a triple no closed-set pattern
        stores, which after those two validators is a broken invariant rather
        than a submission.
    """
    unit = data.pop("recurrence_unit")
    placement = data.pop("recurrence_placement")

    # The form's every-recurrence-key pops happen unconditionally, so the
    # caller's downstream ``setattr`` loop never sees a stray kwarg whichever
    # cadence was chosen.
    #
    # **NO default, and the ``1`` that used to be here re-cadenced bills**
    # (plan step R7c-c).  Every other field this function merges reads PRESENCE
    # first, because a control the form DISABLES posts nothing and absence has
    # to mean "leave the stored one alone"; the interval had neither a presence
    # read nor a producer of absence, so a cleared box arrived as no key at all
    # and this line quietly stored ``1``.  Measured shape: a quarterly bill
    # edited with the interval box emptied generates 12 rows a year instead of
    # 4, across the whole projection, with nothing on screen saying so.
    #
    # It is NOT fixed by adding a fourth presence read.  The interval box is
    # enabled for every chosen cadence (``recurrence_form.js``), so beside a
    # named unit "absent" is not a request the form can make -- unlike
    # ``starts_on`` and the closing bound, whose locked loan-payment controls
    # make it one.  The refusal belongs to the submission, so it is
    # :func:`~app.schemas.validation._helpers.validate_authorable_cadence`'s,
    # beside the identical rule for the placement half, and this pop states the
    # guarantee rather than papering over its absence.
    submitted_interval = data.pop("interval_n")
    # The first occurrence and the day it means, on the SAME present-versus-
    # absent rule the closing bound runs on (plan step R7c-b).  PRESENCE is
    # read before the pop and it is not the same question as the value: a form
    # that rendered the control locked -- a loan payment's, whose start the app
    # derives -- omits the key entirely and must leave the stored date alone,
    # while a form that shows it always states one (the schema requires it
    # beside a chosen cadence).  Collapsing the two would make a loan edit
    # erase the origination bound that keeps its payments from generating
    # before the loan exists.  A transfer route's destination settle WRITES
    # the key for an edit that moves a repeating transfer onto a loan (plan
    # step R7d-f-4), so PRESENT here also means "the loan's own first
    # installment, derived", which this branch carries onto the rule exactly
    # as it would an owner's date.
    #
    # How the date is READ (``repeats_on``: a nominal day or, since plan step
    # recurrence:R8-c, a weekday reading) follows the SAME key, not its own:
    # the two are one statement of when the rule fires, and the control that
    # posts the second is rendered only beside the first.  Reading them apart
    # would let a save that moved the date keep a nominal day the new date's
    # month never clamped, or a "third Tuesday" on a date past the 28th --
    # which ``RecurrenceSpec`` refuses, so it would be a 500 rather than a
    # wrong answer, but a refusal reachable from an ordinary edit is a defect
    # either way.  Both halves of the reading are replaced together, so a
    # stored reading of one kind never survives beside a submitted one of the
    # other.
    states_a_start = RECURRENCE_STARTS_ON_KEY in data
    # The per-month ceiling on the same present-versus-absent rule (plan step
    # salary:R15-a): the control is rendered beside the cadence and DISABLED
    # beside a unit that cannot repeat within a month, so absence means the
    # form could not show it and the stored value rides through -- dropped
    # by ``recurrence_spec_with_cadence`` where the stated unit cannot hold
    # it -- while a present ``None`` is a cleared box and clears.
    states_a_ceiling = RECURRENCE_MAX_PER_MONTH_KEY in data
    submitted_starts_on = data.pop(RECURRENCE_STARTS_ON_KEY, None)
    submitted_reading = data.pop(RECURRENCE_REPEATS_ON_KEY, MonthReading())
    submitted_ceiling = data.pop(RECURRENCE_MAX_PER_MONTH_KEY, None)
    # The bound's keys too, on THIS branch as well: the route reads the mode
    # into ``ctx.end_bound`` before calling, and a submission that named none
    # still leaves its two value keys behind (see :data:`_END_BOUND_KEYS`).
    _pop_end_bound_keys(data)
    # **The phase is no longer read from the payload at all** (defect D8).  The
    # schemas stopped declaring ``offset_periods`` at plan step R7b-2, so the
    # rule's STORED phase rides through ``recurrence_spec_with_cadence``
    # untouched and there is no submitted value able to overwrite it.
    #
    # That finishes what plan step R2d started on defect D1.  Resolution
    # already IGNORED the submitted phase for any rule naming a start period,
    # deriving it from that period instead; the remaining exposure was a rule
    # with NO start period, for which the payload was the only statement of
    # phase -- and the field no form rendered an input for therefore submitted
    # the schema's default 0 on every edit.  Deleting the field is what makes
    # "an amount-only edit cannot re-phase a cadence" structural rather than
    # conditional on a column being set.

    # The rule's CURRENT authored state, with the form's fields replaced.
    # Anything the form does not collect rides through untouched, so this edit
    # cannot reset a field it never showed the user.  BOTH validity bounds are
    # collected now (plan step R7b-4), so neither rides unconditionally: each
    # replaces the stored value when the form stated it and leaves it alone
    # when the form did not.  See the two lines below.
    #
    # ``interval_n`` is written for EVERY unit from plan step R7c-c, and the
    # paragraph that stood here said the reverse.  While the closed pattern set
    # was the storage, the column held the authored count for ``Every N
    # Periods`` and ``1`` for every pattern whose interval was baked into its
    # NAME -- so a Quarterly rule's column read as monthly and the interval a
    # calendar cadence repeated on was recovered through ``pattern_id``.  That
    # is what R7c-c's migration re-points: the column now means "how many units
    # pass between occurrences" for all four units, and what this form posts
    # reaches it verbatim.
    #
    # Which is exactly why a MISSING interval may no longer be defaulted -- see
    # the pop above.  The old sentence "no value this form can submit is able to
    # say a Quarterly bill recurs monthly" was true only because the pattern
    # held the interval; once the column does, the default WAS such a value.
    # **Read with the SUBMITTED cadence, never the stored one**, and the
    # difference is the repair path this form advertises: an edit page may be
    # showing a rule whose stored unit or placement the application no longer
    # models (``edit_form_cadence`` renders the controls UNSET and
    # ``UNREADABLE_CADENCE_MESSAGE`` says to choose a cadence before saving),
    # and reading that rule's cadence on the way to REPLACING it raises.
    # Measured against ``origin/dev``: routing this through ``recurrence_spec``
    # turned the one action the surface tells the user to take into a 500.
    #
    # Since plan step R7b-2 the submitted cadence needs no decoding -- the form
    # states the axes -- so what arrives here is what the user chose.
    current = recurrence_spec_with_cadence(
        rule,
        interval_n=submitted_interval,
        unit=unit,
        placement=placement,
        max_per_month=(
            submitted_ceiling if states_a_ceiling else rule.max_per_month
        ),
    )
    reauthor_rule(
        rule,
        replace(
            current,
            starts_on=(
                submitted_starts_on if states_a_start else current.starts_on
            ),
            nominal_day=(
                submitted_reading.nominal_day if states_a_start
                else current.nominal_day
            ),
            weekday_reading=(
                submitted_reading.weekday_reading if states_a_start
                else current.weekday_reading
            ),
            # The form states the WHOLE closing bound, never half of one
            # (plan step R7b-3): the "Ends" control's three shapes are one
            # value, so a save replaces it outright and there is no merging a
            # posted date into whatever the rule already carried.
            #
            # A form that stated NOTHING leaves the stored bound untouched --
            # ``current`` already carries it.  That is what a loan payment's
            # disabled control produces, and what an amount-only PATCH
            # produces, and neither may be read as "ends never".  For the
            # loan's STANDING payment the stored bound rides through here too
            # -- the form locks the control and a stated bound is refused
            # before this runs -- and since plan step R7d-g it is its owner's
            # word or NULL (``NEVER_ENDS``), never a cache: this line writes
            # back exactly what the owner stored, on every unrelated edit.
            end_bound=(
                current.end_bound if ctx.end_bound is None else ctx.end_bound
            ),
        ),
        calendar,
    )





def _clear_recurrence_rule(template: Any) -> None:
    """DELETE the template's recurrence rule.

    What "this no longer recurs" means on the write side: the row stating the
    cadence ceases to exist.  Merely detaching would produce exactly the orphan
    finding **F-6** measured on the hard-delete path, from a second door.

    **This used to be five statements and a census, and plan step R-F6 made it
    one.**  While the FK pointed from the template at the rule, two templates
    could name one row, so a delete here had to count the OTHER references
    first (``_rule_is_exclusively_owned``, deleted with this rewrite) and log
    a warning when it found one -- a runtime probe standing in for a
    constraint the schema could not carry.  The FK is on the rule now, under a
    unique index per arm, so "this rule is this template's and no other's" is
    true by construction and there is nothing left to ask.  Dis-associating it
    is what deletes it: the relationship carries ``delete-orphan``.

    Flushed before returning so the DELETE lands in statement order here rather
    than wherever the unit of work's dependency sort would put it -- the same
    reason the previous shape flushed.  **Not** because a caller re-authors
    onto this template in the same request: an adversarial review of plan step
    R-F6 measured that it cannot, since the one caller
    (:func:`resolve_recurrence_rule_for_update`) reaches the clear only on the
    branch where no cadence was submitted.  What the flush buys is that a
    destructive statement is legible where it is written.

    Args:
        template: The ``TransactionTemplate``, ``TransferTemplate`` or (plan
            step salary:R15-c) ``PaycheckLine`` whose recurrence is being
            cleared.  Mutated in place; a no-op when it names no rule.
    """
    if not template.recurs:
        return
    template.recurrence_rule = None
    db.session.flush()



def resolve_recurrence_rule_for_update(
    template: Any,
    data: dict[str, Any],
    *,
    ctx: RecurrenceFormContext,
    pass_ctx: BalanceContext,
) -> Response | None:
    """Re-point, rebuild, or CLEAR a template's recurrence rule for an update.

    Dispatches the three update-form branches shared by
    :func:`app.routes.templates.update_template` and
    :func:`app.routes.transfers.templates.update_transfer_template`:

    * pattern present AND the template already owns a rule -> re-point
      that row in place via :func:`update_recurrence_rule_from_form`
      (its primary key and its owning arc stay stable);
    * pattern present, no existing rule -> read the submission with
      :func:`recurrence_spec_from_form` and author it ONTO the template via
      :func:`app.services.recurrence.author_rule`, which is what links it;
    * pattern SUBMITTED AS EMPTY -> the user chose "Does not repeat", so any
      existing rule is cleared through :func:`_clear_recurrence_rule` --
      unless the template is a LOAN PAYMENT (see
      :data:`LOAN_PAYMENT_CANNOT_BE_ONE_TIME`) or its stored rule names a
      pattern this application no longer models (see
      :data:`UNREPAIRED_CADENCE_CANNOT_BE_CLEARED`), either of which is
      refused.

    **A submitted-empty pattern and an absent one are different requests**, and
    keeping them apart is what stops the third branch from breaking the
    partial-update contract.  Both schemas declare ``recurrence_unit`` as
    ``allow_none``, so the form's "Does not repeat" option survives
    ``_normalize_empty_inputs`` as a present ``None`` while a field the caller
    never submitted stays absent -- and only the first clears.  Without that
    distinction an amount-only PATCH, which submits no recurrence keys at all,
    would silently delete the template's cadence.

    **The third branch is new, and its absence was a live defect.**  The
    transaction form has offered a "does not repeat" option since the recurring
    cluster shipped, and selecting it did nothing at all: the builder returned
    ``None``, this function assigned nothing, and the template kept both its
    rule and its cadence.  Worse than inert -- the caller then regenerated
    from the rule the user had just asked it to stop using.  Measured on a
    real edit of an every-paycheck template::

        rule_id before: 1   rows: 10
        rule_id after:  1   rows: 10
        (log) deleted_count=6  created_count=6

    **A LOAN PAYMENT is refused rather than cleared**, because clearing it
    produces a state the domain does not have: an amortizing loan still
    amortizes, so a payment that does not repeat leaves the loan with no
    cadence to project against.  It is not a cosmetic refusal -- measured, the
    clear silently re-dated a loan's payoff, because
    ``recurring_transfer_query.active_recurring_transfer_templates`` finds a
    loan's payments by whether a rule names them, and deleting that rule
    drops the definition -- its occurrences and the standing overpayment they
    carry -- out of the seam's forward plan while its settings row still
    asserts the extra (measured when a loan-level ``loan_standing_extra``
    read that row through the same query; the read went at plan step
    R7d-g-3, the mechanism stands)::

        definition in the loan's plan before: yes, extra 250.00
        definition in the loan's plan after:  no
        loan_payment_settings row still asserts: 250.00

    The template's own ``LoanPaymentSettings`` row would go on claiming an
    extra principal nothing reads.  The two real intents each have a door:
    change the cadence (pick another pattern) or stop paying (archive it).

    Whether the instances that rule already generated are swept is the
    CALLER's half of the same edit; see
    :func:`~app.routes._recurrence_conflict_chooser.regenerate_or_conflict_chooser`.

    The owning row's user scope comes from ``template.user_id`` -- the
    caller fetched the template through an owner-scoped ``get_or_404``,
    so this equals the pre-extraction ``current_user.id``.

    Args:
        template: The ``TransactionTemplate``, ``TransferTemplate`` or (plan
            step salary:R15-c) ``PaycheckLine`` being updated -- the
            :data:`~app.services.recurrence.RecurrenceOwner` union.
            Accessed for ``recurrence_rule`` (which a fresh rule is authored
            onto, and which is cleared when none was selected) and
            ``user_id``.  Mutated in place.
        data: Marshmallow-validated payload; the recurrence keys are
            popped by the delegated helper.  Read for whether
            ``recurrence_unit`` and ``starts_on`` are PRESENT before those
            pops consume them.
        ctx: The :class:`RecurrenceFormContext` forwarded unchanged to
            the delegated builder / updater (its ``end_bound`` and
            ``redirect`` target).
        pass_ctx: The read pass the route built BEFORE this write (plan step
            R7d-f).  The refusals judge the stored definition against it --
            the loan's standing payment is read off its loan-resolution memo,
            and only when a rule can turn on it -- and the calendar every
            branch below authors against is its ``calendar()``, so the
            pre-write side derives the owner's schedule once.  Regeneration
            afterwards builds a FRESH pass, as a writer must: a pass built
            here would memoise the loan as it stood before the re-author.

    Returns:
        * ``None`` -- the rule was resolved; the caller continues to
          the field-update loop.
        * :class:`Response` -- a Flask redirect for one of the refusals
          :func:`_refuse_recurrence_update` makes; the caller returns it
          directly.
    """
    # Read BEFORE the delegated helper pops the key.
    recurrence_submitted = "recurrence_unit" in data
    refusal = refuse_recurrence_update(
        template, data,
        ctx=ctx, pass_ctx=pass_ctx,
        recurrence_submitted=recurrence_submitted,
    )
    if refusal is not None:
        return refusal

    if data.get("recurrence_unit") is not None and template.recurrence_rule:
        # Re-points the rule in place.  Its one refusal is the write door's
        # (a stored pair that would invert once a paycheck-space start is
        # normalised -- the inverted-window door grades the authored pair,
        # this grades the stored one) and it is worded in the door's own
        # sentence, off the first occurrence the rule would actually have.
        try:
            update_recurrence_rule_from_form(
                template.recurrence_rule,
                data,
                ctx=ctx,
                calendar=pass_ctx.calendar(),
            )
        except EmptyAuthoredWindowError as refused:
            return _refuse_empty_window(refused, ctx)
        return None

    # **The one UPDATE branch that AUTHORS**, and therefore the one that needs
    # the first occurrence the update schemas stopped requiring.  Adding a
    # cadence to a template that had none builds a fresh rule below, and a rule
    # cannot be authored without a start (``budget.recurrence_rules.starts_on``
    # is NOT NULL) -- so an unstated one would reach the write door as a
    # ``RecurrenceResolutionError``, which is a broken-invariant signal and not
    # a field the user can fix.
    #
    # The schema cannot make this call: the rule is CONDITIONAL on the template
    # already having a rule, and a schema never sees the template.  Refusing
    # here with the schema's own message is what keeps the two layers saying
    # one thing -- see
    # ``schemas/validation/_helpers.RECURRENCE_NEEDS_A_START``.  A transfer
    # into a LOAN never reaches the refusal: the route's destination settle
    # has written the loan's own first occurrence into the payload by now
    # (``_loan_destination.settle_destination_for_update``, plan step
    # R7d-f-4), the same way the create door fills the locked control's gap.
    if (
        data.get("recurrence_unit") is not None
        and data.get(RECURRENCE_STARTS_ON_KEY) is None
    ):
        for message in RECURRENCE_NEEDS_A_START[RECURRENCE_STARTS_ON_KEY]:
            flash(message, "danger")
        return ctx.redirect.to_response()

    spec = recurrence_spec_from_form(
        data,
        user_id=template.user_id,
        ctx=ctx,
    )
    if spec is not None:
        # Authored ONTO the template (plan step R-F6), which is what links it:
        # the rule carries the owning FK now, so there is no ``recurrence_rule_id``
        # left for this branch to assign and no window in which a written rule
        # belongs to nothing.  The same write-door refusal as the re-point
        # branch above, worded the same way.
        try:
            author_rule(spec, pass_ctx.calendar(), template)
        except EmptyAuthoredWindowError as refused:
            return _refuse_empty_window(refused, ctx)
    elif recurrence_submitted:
        _clear_recurrence_rule(template)
    return None


def _refuse_empty_window(
    refused: EmptyAuthoredWindowError, ctx: RecurrenceFormContext,
) -> Response:
    """Word the write door's refusal of an inverted STORED pair, and redirect.

    The update door's twin of the create helper's ``except`` (plan step
    R7d-g): ``refuse_inverted_window`` grades the AUTHORED pair before any
    write, and this grades nothing -- the door already has -- but says it in
    the same sentence, off the two dates the door would have stored.  Nothing
    was written, so nothing is rolled back here; the route owns its
    transaction and commits nothing after a redirect.

    Args:
        refused: The write door's refusal, carrying the stored pair.
        ctx: The form context, for where the refusal sends the user.

    Returns:
        The redirect :class:`Response`, returned by the caller verbatim.
    """
    flash(
        end_bound_before_start_message(refused.end_date, refused.starts_on),
        "danger",
    )
    return ctx.redirect.to_response()


__all__ = [
    "author_recurrence_for_create",
    "recurrence_spec_for_create",
    "recurrence_spec_from_form",
    "update_recurrence_rule_from_form",
    "resolve_recurrence_rule_for_update",
]
