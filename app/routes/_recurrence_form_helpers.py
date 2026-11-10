"""
Shekel Budget App -- Recurrence-Form Route Helpers (F-24, F-26)

What a definition's recurrence rule IS after a form submit, for the
transaction-template (:mod:`app.routes.templates`) and transfer-template
(:mod:`app.routes.transfers`) CRUD routes -- and, since plan step
salary:R15-c, for the paycheck-deduction routes
(:mod:`app.routes.salary.items`), the third owner the arc admits, which
settle the two facts their form does not collect into the submission and then
run the same create write and update dispatcher.  What happens to the ROWS that rule
already generated is the sibling module
:mod:`app.routes._recurrence_conflict_chooser`, split out at plan step R2e-1
when this one reached the 1,000-line cap.  What an EDIT form's controls START
ON is :mod:`app.routes._recurrence_form_render`, split out at plan step R7b-4
when it reached that cap a second time -- the seam being SUBMISSION versus
STORED STATE: everything here reads a posted submission and writes, everything
there answers a read-only question about a definition already saved.

The helpers:

* :func:`author_recurrence_for_create` -- the create door's WRITE: the spec
  the submission authors
  (:meth:`~app.services.recurrence.RecurrenceSubmission.authored`) written
  onto the definition, which plan step R-F6 made a step of its own -- the
  rule is owned by its definition, so it cannot be written until the
  definition exists;
* :func:`resolve_recurrence_rule_for_update` -- dispatches the three
  update-form branches (re-point an existing rule, author + link a new
  one, or CLEAR the recurrence the user set to "Does not repeat") so
  each ``update_*`` route resolves its recurrence rule with a single
  call.  [F-24; the clear branch is plan step R2e-1]

**What a submission STATES is one typed value since plan step R7e** (finding
**D36**): the schema's ``@post_load`` consumes every recurrence key into a
:class:`~app.services.recurrence.RecurrenceSubmission`, which a route hands
here inside the context.  Three helpers left with the keys.
``recurrence_spec_for_create`` and ``recurrence_spec_from_form`` lifted the
closing bound out of the payload and popped every other recurrence key
before the caller's model constructor saw it -- a fence whose one miss, the
``starts_on`` pop on the "Does not repeat" branch, was a 500 -- and the spec
they built is :meth:`~app.services.recurrence.RecurrenceSubmission.authored`.
``update_recurrence_rule_from_form`` merged the submission onto the stored
rule with a presence read per field, and the merge is
:meth:`~app.services.recurrence.RecurrenceSubmission.applied_to`, read once by
the dispatcher for the refusal and the write together.

The dispatcher and its refusals share the
:class:`~app.routes._recurrence_form_refusals.RecurrenceFormContext` -- the
submission and the validation-error redirect target -- which is DEFINED in
the refusals module since plan step R7d-f (see there for why the leaf moved)
and imported here.

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
    RECURRENCE_NEEDS_A_START,
    RECURRENCE_STARTS_ON_KEY,
    end_bound_before_start_message,
)
from app.services.balance_at import BalanceContext
from app.services.pay_calendar import PayCalendar, calendar_for
from app.services.recurrence import (
    UNSET,
    EmptyAuthoredWindowError,
    RecurrenceOwner,
    RecurrenceSpec,
    author_rule,
    reauthor_rule,
)


def author_recurrence_for_create(
    spec: RecurrenceSpec | None,
    template: RecurrenceOwner,
    *,
    redirect: RedirectTarget,
    calendar: PayCalendar | None = None,
) -> RecurrenceRule | Response | None:
    """Write the create form's cadence onto the definition it belongs to.

    The WRITE half of the create door, and the half plan step R-F6 split from
    the read: a rule carries its owner's FK now, so it cannot be written until
    the definition exists.  Each create route reads the spec the submission
    authors (:meth:`~app.services.recurrence.RecurrenceSubmission.authored`)
    before its model constructor and calls this after it, with the same rows
    created between the two.  Until plan step R7e the read was a helper here
    too, because the recurrence keys had to be popped out of the payload
    before the constructor saw them; the schema consumes them now.

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
    *,
    ctx: RecurrenceFormContext,
    pass_ctx: BalanceContext,
) -> Response | None:
    """Re-point, rebuild, or CLEAR a template's recurrence rule for an update.

    Dispatches the three update-form branches shared by
    :func:`app.routes.templates.update_template` and
    :func:`app.routes.transfers.templates.update_transfer_template`:

    * a cadence named AND the template already owns a rule -> re-point
      that row in place through :func:`app.services.recurrence.reauthor_rule`
      (its primary key and its owning arc stay stable), writing the
      submission applied to it;
    * a cadence named, no existing rule -> author the spec the submission
      states (:meth:`~app.services.recurrence.RecurrenceSubmission.authored`)
      ONTO the template via :func:`app.services.recurrence.author_rule`,
      which is what links it;
    * the cadence CLEARED -> the user chose "Does not repeat", so any
      existing rule is cleared through :func:`_clear_recurrence_rule` --
      unless the template is a LOAN PAYMENT (see
      :data:`LOAN_PAYMENT_CANNOT_BE_ONE_TIME`) or its stored rule names a
      pattern this application no longer models (see
      :data:`UNREPAIRED_CADENCE_CANNOT_BE_CLEARED`), either of which is
      refused.

    **A cleared cadence and an unmentioned one are different requests**, and
    keeping them apart is what stops the third branch from breaking the
    partial-update contract.  The submission carries them as two states --
    ``None`` for the form's "Does not repeat",
    :data:`~app.services.recurrence.UNSET` for a form that said nothing about
    the cadence -- and only the
    first clears.  Without that distinction an amount-only PATCH, which
    submits no recurrence keys at all, would silently delete the template's
    cadence.  Until plan step R7e the distinction was this function's own
    ``"recurrence_unit" in data``, and the schema states it once now
    (:func:`~app.schemas.validation._recurrence_submission.consume_recurrence_submission`).

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
        ctx: The :class:`RecurrenceFormContext`: the submission every branch
            reads, and where a refusal sends the user.
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
          :func:`refuse_recurrence_update` makes, or the authoring branch's
          missing start, or the write door's empty window; the caller returns
          it directly.
    """
    submission = ctx.submission
    rule = template.recurrence_rule
    # **The spec the re-point branch WRITES, merged ONCE** (plan step R7e):
    # the inverted-window refusal grades it and the write below stores it, so
    # "the refusal grades what the save would write" holds by construction
    # rather than by two spellings of one merge agreeing.  Each spelled it
    # inline until this step -- field by field, each on its own presence read.
    # ``None`` where nothing is re-pointed: the template has no rule, or the
    # submission names no cadence.
    reauthored = (
        submission.applied_to(rule)
        if rule is not None and submission.named_cadence is not None
        else None
    )
    refusal = refuse_recurrence_update(
        template, ctx=ctx, pass_ctx=pass_ctx, reauthored=reauthored,
    )
    if refusal is not None:
        return refusal

    if reauthored is not None:
        # Re-points the rule in place.  Its one refusal is the write door's
        # (a stored pair that would invert once a paycheck-space start is
        # normalised -- the inverted-window door grades the authored pair,
        # this grades the stored one) and it is worded in the door's own
        # sentence, off the first occurrence the rule would actually have.
        try:
            reauthor_rule(rule, reauthored, pass_ctx.calendar())
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
    # ``schemas/validation/_recurrence.RECURRENCE_NEEDS_A_START``.  A transfer
    # into a LOAN never reaches the refusal: the route's destination settle
    # has stated the loan's own first occurrence in the submission by now
    # (``_loan_destination.settle_destination_for_update``, plan step
    # R7d-f-4), the same way the create door fills the locked control's gap.
    if submission.named_cadence is not None and submission.starts_on is UNSET:
        for message in RECURRENCE_NEEDS_A_START[RECURRENCE_STARTS_ON_KEY]:
            flash(message, "danger")
        return ctx.redirect.to_response()

    spec = submission.authored(template.user_id)
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
    elif submission.clears_the_recurrence:
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
    "resolve_recurrence_rule_for_update",
]
