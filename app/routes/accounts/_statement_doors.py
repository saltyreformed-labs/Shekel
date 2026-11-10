"""One shape for every statement write door: act, commit, or say what stopped it.

The doors the statement pages own -- import a file, delete an import, accept a
match, apply a whole reviewed pass,
release one -- share their whole failure story and differ only in the act and
in what to say when it worked.  Writing that story per door is what pylint's
cross-file ``duplicate-code`` reported when the second one landed, and the
report was right: three copies of a rollback-and-flash are three places for a
refusal to stop being rendered.

**TWO ANSWER SHAPES, and the split is the answer rather than the story.**
:func:`run_statement_door` sets a flash and redirects;
:func:`run_statement_fragment_door` re-renders the surface, because a per-item
receipt overflows the 4 KB a browser stores for the signed session cookie a
flash rides in.  The second arrived at plan step ``bank_import:X-gf-3b``, when
the hand-build match form got a write door of its own and ``duplicate-code``
reported the second copy of the fragment story -- the same report, one shape
along.

**The story, once:**

* the act runs and the request commits, so the unit of work is the request and
  a refusal leaves nothing behind -- which is what makes "nothing was changed",
  the phrase every refusal message here ends with, true rather than reassuring;
* a DOMAIN refusal is the user's own sentence, flashed as it was written --
  and, since plan step ``bank_import:X-f6b-2`` (ledger row **BI-499**),
  LOGGED at WARNING with its class through :func:`log_refusal` -- from
  these two arms, and from the one feed-panel press that commits nothing
  and so takes no helper -- so an operator reading the log after the fact
  has the trail a screen alone never left;
* a DATABASE error is not, so it goes to
  :func:`~app.routes._commit_helpers.handle_db_error`, which logs the detail
  and shows the user a sentence that does not name a table;
* success flashes what the door decided to say, AFTER the commit -- a business
  event or a message asserting that money moved must not appear when the
  transaction that would have moved it failed.

It lives in the accounts package rather than in ``_commit_helpers`` because the
refusal TYPE is a parameter here and that is a statement-door concern: the
import door refuses with ``StatementImportError`` and the match doors with
``ValidationError``, and generalising the helper any further would make it a
second spelling of ``try``.
"""

import logging
from dataclasses import dataclass
from typing import Callable

from flask import Response, flash, redirect, request
from flask_login import current_user
from sqlalchemy.exc import SQLAlchemyError

from app.extensions import db
from app.exceptions import ValidationError
from app.routes._commit_helpers import DbErrorContext, handle_db_error
from app.schemas.validation import form_payload
from app.utils.log_events import (
    BUSINESS,
    EVT_STATEMENT_BATCH_APPLIED,
    EVT_STATEMENT_DOOR_REFUSED,
    log_event,
)
from app.services.statement_match import (
    NEW_ENVELOPE,
    Consent,
    IncomeCreation,
    MatchSubmission,
    NewEnvelope,
    PlaceIn,
    PurchaseCreation,
    ReviewedBatch,
    ReviewScope,
    SkipRequest,
)


@dataclass(frozen=True)
class StatementDoorContext:
    """What one statement door needs in order to fail well.

    Attributes:
        logger: The calling module's logger, so a database error is logged
            under the module that owns the door rather than under this one.
        refusal: The exception class this door's service raises for a DESIGNED
            refusal -- a sentence written for the person who submitted the
            form.  Anything else propagates.
        log_message: The ``%``-style message for a database error.
        log_args: Its arguments.
        flash_message: What to tell the user when the database refused.  It
            ends with "Nothing was changed" for the reason the module docstring
            gives.
        target: Where to send the user afterwards, whatever happened.  ONE
            destination for all three outcomes, because a door that redirected
            somewhere else on failure would lose the flash it just set.
    """

    logger: logging.Logger
    refusal: type
    log_message: str
    log_args: tuple
    flash_message: str
    target: str


def run_statement_door(
    ctx: StatementDoorContext,
    act: Callable,
    on_success: Callable,
) -> Response:
    """Run *act*, commit, and turn every outcome into a redirect to the target.

    Args:
        ctx: What this door needs in order to fail well.
        act: The service call, taking no arguments and returning whatever the
            door wants to report.  It MUST NOT commit -- this function owns the
            unit of work, which is what makes a refusal leave nothing behind.
        on_success: ``result -> (message, category)``.  Called AFTER the commit,
            so anything it emits -- a flash, a business log event -- asserts
            something that has actually happened.

    Returns:
        A redirect to ``ctx.target``, with exactly one flash set.
    """
    try:
        result = act()
        db.session.commit()
    except ctx.refusal as exc:
        db.session.rollback()
        log_refusal(ctx.logger, exc)
        flash(str(exc), "danger")
        return redirect(ctx.target)
    except SQLAlchemyError:
        return handle_db_error(DbErrorContext(
            logger=ctx.logger,
            log_message=ctx.log_message,
            log_args=ctx.log_args,
            flash_message=ctx.flash_message,
            redirect=ctx.target,
        ))
    message, category = on_success(result)
    flash(message, category)
    return redirect(ctx.target)


def log_refusal(logger: logging.Logger, exc: Exception) -> None:
    """Log a designed refusal at WARNING with its class (ledger row **BI-499**).

    **The ONE place a statement door's refusal reaches the log.**  Until plan
    step ``bank_import:X-f6b-2`` a refusal was flashed and never logged, so
    an operator reading the app log after the fact -- or an unattended fetch,
    which that step builds -- had no trail of it.  Both door helpers call
    this from their refusal arm; the bank feed panel's listing press, which
    commits nothing and takes no helper, calls it from its own catch.

    **The CLASS, not the sentence** -- BI-499's letter, and the stricter
    choice: a refusal sentence is written for the OWNER's screen and can
    carry what the screen may show them (``StatementAccountMismatch`` names
    the source's account identifier, ``StatementLineConflict`` quotes bank
    line wording), which is data the app log has never held and this step
    does not decide to add.  A refusal that has structured facts worth a
    field -- Bridge's status and the failure's class on
    :class:`~app.exceptions.BridgeRefused` -- names them on its own
    ``log_details``, read here when present.  A database error is NOT this
    event: that path keeps its traceback under
    :func:`~app.routes._commit_helpers.handle_db_error`.

    Args:
        logger: The calling module's logger, so the line is filed under the
            module that owns the door.
        exc: The designed refusal.
    """
    log_event(
        logger, logging.WARNING, EVT_STATEMENT_DOOR_REFUSED, BUSINESS,
        "A statement door refused the request.",
        user_id=current_user.id,
        path=request.path,
        refusal_class=type(exc).__name__,
        **getattr(exc, "log_details", {}),
    )


def run_one_id_door(
    schema, field: str, ctx: StatementDoorContext,
    act_for: Callable, on_success: Callable,
) -> Response:
    """Grade a form naming ONE row id, then run *act_for* on it.

    Plan step ``bank_import:X-gj-4c-2`` extracted this, and the reason is the
    module docstring's own: *three copies of a rollback-and-flash are three
    places for a refusal to stop being rendered.*  Two doors here name exactly
    one act and nothing else -- releasing a match
    (:func:`~._statement_release.release_and_return`) and undoing a skip
    (:func:`~.statement_reconcile.unskip_from_reconcile`) -- and they were
    STRUCTURALLY identical for eight lines, five of them byte-identical:
    build the payload, validate, flash the refusal and redirect, load the id,
    hand it to :func:`run_statement_door`.  The three that differed named the
    schema and the field.

    **Pylint's cross-file ``duplicate-code`` did NOT report it**, and that is
    why this is written down rather than left to the gate.  The longest
    byte-identical run was FOUR lines (``return run_statement_door(`` through
    ``refusal=ValidationError,``), and the checker requires more than its
    ``min-similarity-lines`` to fire -- so at the default of 4 a four-line run
    is silent, and it takes 5 to report.  *An earlier version of this
    paragraph said "byte-identical for seven lines" and "three consecutive
    lines against a minimum of four"; both were measured false, and the second
    would have taught a reader that four identical lines ARE caught.*  A gate
    is a floor.  Named by adversarial review 2026-09-04.

    **It is the VALIDATE half only, and the two acts stay separate.**  What
    the doors share is the shape of grading a one-id form; what they do not
    share is the act, the receipt or the refusal story, which is why undoing a
    skip does not go through ``release_and_return``.  Folding those would be a
    helper for two things that only look alike.

    Args:
        schema: The Marshmallow schema naming the id field, one instance
            constructed at the caller's import like every sibling's.
        field: Which key to read off the loaded payload.
        ctx: What this door needs in order to fail well
            (:class:`StatementDoorContext`), whose ``target`` is where every
            outcome redirects -- including the schema refusal below, which is
            why the caller builds it before validating.
        act_for: ``row_id -> result``.  The service call, applied to the graded
            id -- THIS function wraps it in the zero-argument callable
            :func:`run_statement_door` takes, so *act_for* is not itself a
            factory.  It MUST NOT
            commit; that function owns the unit of work.  *An earlier version
            documented ``row_id -> (() -> result)``, which both live callers
            contradict: a third written to that contract would hand
            ``on_success`` a lambda object where the service's return value
            belongs, and build a receipt off a function.*
        on_success: ``result -> (message, category)``, called AFTER the commit.

    Returns:
        A redirect to ``ctx.target``, with exactly one flash set -- the
        schema's refusal, the door's, or the receipt.
    """
    payload = form_payload(request.form, schema)
    errors = schema.validate(payload)
    if errors:
        # **A schema refusal redirects to the SAME target as every other
        # outcome**, which is :class:`StatementDoorContext`'s own rule stated
        # one tier up: a door that redirected elsewhere on failure would lose
        # the flash it just set.
        flash(refusal_sentence(errors), "warning")
        return redirect(ctx.target)
    return run_statement_door(
        ctx, lambda: act_for(schema.load(payload)[field]), on_success,
    )


@dataclass(frozen=True)
class StatementFragmentDoorContext:  # pylint: disable=too-many-instance-attributes
    """What one FRAGMENT-shaped statement door needs in order to fail well.

    Pylint: ``too-many-instance-attributes`` (8/7) -- this class IS the
    parameter object ``CLAUDE.md``'s too-many-arguments rule points a public
    door at, so the count is the remedy rather than the defect: seven of the
    eight are what a fragment door supplies and the eighth (``refusal``) is
    the one fact that made a second kind of fragment door possible.
    Splitting them would put one door's configuration in two values every
    door must then keep in step.

    :class:`StatementDoorContext`'s twin, and a parameter object for the reason
    ``CLAUDE.md``'s own too-many-arguments rule gives: a PUBLIC function over
    the limit takes one, and only a private helper decomposes instead.

    **Built by :func:`fragment_door` for the two reconcile doors**, and that
    is what dissolved the ``duplicate-code`` this class was created in response
    to.  Constructing it inline left those two doors with nine byte-identical
    lines of configuration -- ``logger=``, the refusal lambda, the log-args
    tuple, the fresh-scope render -- because two doors that differ only in
    their payload and their surface CONFIGURE identically.  Extracting the
    helper made the invariant half invariant in one place; what is left at each
    call site is the two facts that genuinely differ.  The bank feed's claim
    door (plan step ``bank_import:X-f6b-2``) constructs it inline: its scope
    is not a review pass and its reread is Bridge's listing, so none of the
    factory's invariant half applies to it.

    Attributes:
        logger: The calling module's logger, so a database error is logged
            under the module that owns the door rather than under this one.
        render: ``(scope, *, outcome=None, error=None) -> response``.  This
            door's own surface.  **One callable for all three outcomes**, where
            each door used to carry a ``_refused`` beside the success render
            and state "answer with this door's body" twice: the designed 400
            and the 200 carrying the receipt are the same page with a different
            thing to say.
        scope: The REQUEST's own pass, which every refusal arm renders from.  A
            refused act wrote nothing, so it still describes the state that
            survives -- and re-deriving on the ``SQLAlchemyError`` path would
            run the very read whose failure is being handled, which escapes as
            an unhandled 500 that htmx will not swap.
        reread: ``() -> scope``.  A FRESH pass for the success arm, because the
            act has just settled rows and moved days, so the one it was applied
            against describes a state that no longer exists.
        log_message: The ``%``-style message for a database error.
        log_args: Its arguments.
        db_error_message: What to tell the owner when the database refused.  It
            names no table -- the traceback goes to the log -- and it ends the
            way every refusal in this package does, which is true because
            :func:`run_statement_fragment_door` owns the unit of work.

    **``refusal`` defaults to ``ValidationError``**, which every fragment
    door of the statement pages raises, and it became a field only when a
    second value existed: the bank feed's claim door (plan step
    ``bank_import:X-f6b-2``) answers with the panel and refuses through
    ``BankFeedError``.  Until then the class carried no such field, its
    docstring saying a field with one possible value is configurability
    nobody asked for -- which was true, and stopped being.
    """

    logger: logging.Logger
    render: Callable
    scope: object
    reread: Callable
    log_message: str
    log_args: tuple
    db_error_message: str
    refusal: type = ValidationError


def fragment_door(  # pylint: disable=too-many-arguments
    logger: logging.Logger,
    *,
    render: Callable,
    scope,
    account_id: int,
    act: str,
    db_error_message: str,
) -> StatementFragmentDoorContext:
    """Return the context for one fragment-shaped statement door.

    Pylint: ``too-many-arguments`` (6/5) -- these six are the door's own
    independent facts rather than a cohesive entity, and **the usual remedy is
    circular here**: this function IS the parameter-object factory that
    ``CLAUDE.md``'s rule points a public over-limit signature at, and wrapping
    its arguments in a second value would be a parameter object for a parameter
    object.  Five are what a door supplies (its logger, its surface, its pass,
    its account, what it was doing) and the sixth is the one sentence the owner
    reads.  Reducing the count by deriving ``db_error_message`` from ``act``
    was tried and rejected: ``act`` is developer wording that names the door in
    a log line, and the owner's sentence is not, so one string serving both
    audiences makes one of them worse.

    **The invariant configuration, stated once.**  Both doors that apply a
    statement money pass report a database failure the same way -- same logger
    call, same ``user_id``/``account_id`` arguments, same rollback-then-render
    -- and re-derive the same fresh scope on success.  Written at the call
    sites, that was nine identical lines apiece and pylint's cross-file
    ``duplicate-code`` reported it correctly: two doors differing only in their
    payload and their surface configure identically, so the configuration
    belongs here.

    Args:
        logger: The calling module's logger.
        render: ``(scope, *, outcome=None, error=None) -> response`` -- this
            door's own surface.
        scope: The request's own derived pass.
        account_id: The account, for the fresh read and the log arguments.
        act: What this door was doing, as an infinitive phrase, e.g.
            ``"apply a statement review"``.  It is the only per-door part of
            the failure log line, so the SENTENCE is written once here and
            cannot end up saying two different things about one shape.
        db_error_message: What to tell the owner when the database refused.

    Returns:
        The :class:`StatementFragmentDoorContext`.
    """
    return StatementFragmentDoorContext(
        logger=logger,
        render=render,
        scope=scope,
        # **Read AFTER the write, and only on the path that wrote** -- built
        # here as a thunk rather than a value so nothing derives a second pass
        # on the refusal arms, where the request's own is what must be shown.
        reread=lambda: ReviewScope.build(current_user.id, account_id),
        log_message=f"user_id=%d failed to {act} on account %d",
        log_args=(current_user.id, account_id),
        db_error_message=db_error_message,
    )


def run_statement_fragment_door(
    ctx: StatementFragmentDoorContext,
    act: Callable,
    on_applied: Callable,
):
    """Run *act*, commit, and answer with this door's own surface either way.

    :func:`run_statement_door`'s twin for the doors that answer with the SCREEN
    rather than with a redirect, added at plan step ``bank_import:X-gf-3b``
    when the hand-build form got a write door of its own and pylint's
    cross-file ``duplicate-code`` reported the second copy of the story -- the
    same report that produced its sibling, and right for the same reason: two
    copies of a rollback-and-answer are two places for a refusal to stop being
    rendered.

    **Why the two cannot be one.**  A redirect door sets a flash and sends the
    browser somewhere; these re-render the whole surface, because the ruled
    failure policy is that a refused item leaves nothing behind while the rest
    still land, each refusal quoted -- and flash messages ride in the signed
    session cookie, where the longest sentence a batch item can carry measures
    497 bytes against the 4 KB a browser will store.  Nine of those overflow
    it, and an overflowed cookie is dropped, which logs the owner out.  A
    single helper carrying both shapes would have to branch on which kind of
    door called it, which is the caller-sniffing this package refuses.

    **It owns the ANSWER as well as the failure**, unlike its sibling, and that
    is what makes the fresh-scope-on-success rule structural: a door cannot
    accidentally report a pass against the state that pass replaced, because it
    no longer chooses which scope to render from.

    The unit of work is the REQUEST: this function owns the commit, so a
    failure outside a designed refusal writes nothing at all -- which is what
    makes "nothing was changed" true rather than reassuring.

    Args:
        ctx: What this door needs in order to fail well
            (:func:`fragment_door` builds it).
        act: The service call, taking no arguments and returning whatever the
            door wants to report.  It MUST NOT commit.
        on_applied: ``result -> None``.  The audit event, called AFTER the
            commit so an event asserting money moved cannot sit in the log for
            a transaction that failed.  It does NOT render: the answer is this
            function's, from the re-read pass.

    Returns:
        This door's surface -- carrying the outcome at 200, or one refusal
        sentence as a designed 400.
    """
    try:
        result = act()
        db.session.commit()
    except ctx.refusal as exc:
        # **On the two reconcile doors nothing raises one from inside
        # ``act``, and the arm stands for the SURFACE rather than for a known
        # caller**: a designed refusal escaping an htmx POST is answered by
        # the app-wide handler with a page htmx will not swap (no marker
        # header), so the owner presses the button and sees nothing at all.
        # This arm is the only thing that can answer with the screen, and it
        # has a firing control -- ``test_a_refusal_raised_OUTSIDE_an_item
        # _still_answers_with_the_screen``.  The bank feed's claim door
        # (``ctx.refusal = BankFeedError``) raises from inside ``act`` on
        # every refusal it makes.
        db.session.rollback()
        log_refusal(ctx.logger, exc)
        return ctx.render(ctx.scope, error=str(exc))
    except SQLAlchemyError:
        db.session.rollback()
        ctx.logger.exception(ctx.log_message, *ctx.log_args)
        return ctx.render(ctx.scope, error=ctx.db_error_message)
    on_applied(result)
    return ctx.render(ctx.reread(), outcome=result)


def submitted_match(submitted) -> MatchSubmission:
    """Return one loaded match payload as the value the door applies.

    **One construction for every surface that submits a match**, which is the
    same rule the schema beside it keeps: a hand-built group, a ticked
    proposal and a Reconcile card's MATCH tab are the SAME act reaching the
    same door (``_accept.record_match``), so a second construction would be a
    second place for a member to be dropped on the way to it.  It moved here
    at plan step ``bank_import:X-gj-1b``, when the Reconcile page became the
    third caller.

    **Nothing here names an owner or an account.**  Whose match this is, is
    the scope's -- one statement, made where the route proved it -- so no
    member can be priced against one account and written against another.

    Args:
        submitted: What
            :class:`~app.schemas.validation.statements.StatementMatchSchema`
            loaded, or one item of what
            :class:`~app.schemas.validation.statements.StatementBatchSchema`
            loaded -- the two are the same shape, because the nested schema IS
            that schema.

    Returns:
        The :class:`~app.services.statement_match.MatchSubmission`.
    """
    return MatchSubmission(
        lines=frozenset(submitted["lines"]),
        rows=frozenset(submitted["rows"]),
        # The difference the owner agreed to AND the member it lands on, as
        # ONE value (plan step ``bank_import:X-gp``).  ``None`` where the body
        # stated no consent, which is what the schema's own ``load_default``
        # says and what the door refuses where a difference would be written.
        consent=submitted["consent"],
    )


def submitted_item_count(submitted) -> int:
    """Return how many ACTS one loaded pass carries.

    **Every kind, counted once** (ruling **bank_import:R-GW**).  A count that
    named three of the four would make the audit trail disagree with
    ``applied_count`` for any pass holding a skip, and it was written a kind
    short once already -- for the deposit, which is why the sentence names the
    failure rather than the arm.  Stated here because both surfaces that apply a pass log it, and
    pylint's cross-file ``duplicate-code`` reported the second copy the moment
    the Reconcile page became one.

    Args:
        submitted: What
            :class:`~app.schemas.validation.statements.StatementBatchSchema`
            loaded.

    Returns:
        The item count.
    """
    return (
        len(submitted["matches"])
        + len(submitted["creations"])
        + len(submitted["incomes"])
        + len(submitted["skips"])
    )


def log_pass_applied(
    logger: logging.Logger, message: str, *,
    account_id: int, item_count: int, outcome,
) -> None:
    """Log what an applied statement pass did, AFTER the commit.

    **ONE event for every door that applies a
    :class:`~app.services.statement_match.BatchOutcome`**, which is the rule
    :func:`outcome_counts` states and which this now makes structural rather
    than remembered: an audit trail whose FIELDS depend on which door wrote
    the row cannot be queried across the three, and the reviewed pass, the
    hand-built match and the Reconcile page are three ways of performing one
    act.

    Args:
        logger: The calling module's logger, so the row is attributed to the
            door the owner pressed.
        message: What that door was doing, in one sentence.
        account_id: The account.
        item_count: How many acts the pass carried
            (:func:`submitted_item_count`, or ``1`` for a door that applies
            exactly one).
        outcome: The :class:`~app.services.statement_match.BatchOutcome` the
            door applied.
    """
    log_event(
        logger, logging.INFO, EVT_STATEMENT_BATCH_APPLIED, BUSINESS,
        message,
        user_id=current_user.id,
        account_id=account_id,
        item_count=item_count,
        **outcome_counts(outcome),
    )


def submitted_batch(submitted) -> ReviewedBatch:
    """Return one loaded pass as the batch the service applies.

    **One construction for every surface that applies a whole pass**, which is
    the rule :func:`submitted_match` beside it keeps and which pylint's
    cross-file ``duplicate-code`` reported the moment the Reconcile page
    became the second such surface: two doors differing only in the FORM they
    were read from build the identical value, and a second copy is a second
    place for a kind of act to be dropped on the way to the door.  It has been
    caught once already at one tier up -- the audit event's own list of money
    effects was missing two entries from the day it was written.

    **Nothing here names an owner or an account.**  Whose pass this is, is the
    scope's -- one statement, made where the route proved it -- so no item can
    be priced against one account and written against another.

    **The consent is a LITERAL and never a wire value** (ruling
    **bank_import:R-GH**): a person read a screen and pressed Apply.  It is a
    fact about the DOOR rather than about the payload, and the only other
    consent belongs to an import filing under a standing rule.

    Args:
        submitted: What
            :class:`~app.schemas.validation.statements.StatementBatchSchema`
            loaded.

    Returns:
        The :class:`~app.services.statement_match.ReviewedBatch`.
    """
    return ReviewedBatch(
        consent=Consent.TICKED,
        matches=tuple(submitted_match(item) for item in submitted["matches"]),
        creations=tuple(
            _purchase_creation(item) for item in submitted["creations"]
        ),
        # **Every kind of act a pass can carry** (ruling
        # **bank_import:R-GW**).  A construction naming two of three kinds
        # would silently drop every deposit the owner ticked.
        incomes=tuple(
            IncomeCreation(line_id=item["line_id"])
            for item in submitted["incomes"]
        ),
        skips=tuple(
            SkipRequest(line_id=item["line_id"])
            for item in submitted["skips"]
        ),
    )


def _purchase_creation(item: dict) -> PurchaseCreation:
    """Return one ticked line's creation, its destination read into ONE arm.

    The schema's :class:`~app.schemas.validation.statements.PurchaseDestination`
    hands back one of three values -- an envelope's id, :data:`NEW_ENVELOPE`,
    or a :class:`~app.services.statement_match.PlaceIn` naming a one-off
    envelope's definition to place a row of (plan step ``balance:X-bi-7b``
    leaf 7b-3) -- and each is exactly one of
    :class:`~app.services.statement_match.PurchaseCreation`'s arms, so the
    value is built here once rather than by three conditionals per field.

    Args:
        item: One loaded creation from the batch schema.

    Returns:
        The creation.
    """
    destination = item["destination"]
    if destination == NEW_ENVELOPE:
        return PurchaseCreation(
            line_id=item["line_id"],
            new_envelope=NewEnvelope(
                name=item["envelope_name"], category_id=item["category_id"],
            ),
        )
    if isinstance(destination, PlaceIn):
        return PurchaseCreation(
            line_id=item["line_id"], template_id=destination.template_id,
        )
    return PurchaseCreation(line_id=item["line_id"], transaction_id=destination)


def outcome_counts(outcome) -> dict:
    """Return what one applied pass DID, as ``log_event`` keywords.

    Stated ONCE for both doors that apply a
    :class:`~app.services.statement_match.BatchOutcome` -- the reviewed pass
    and the hand-built match -- because they write the same event about the
    same value and a second copy of the list is a second place to forget a
    field.  **Two were forgotten once already**: ``repriced_count`` and
    ``residual_count`` were missing from the pass event from the day it was
    written, and they are the two that move MONEY rather than dates.  Named by
    adversarial financial review 2026-08-23.

    **Every count, including the ones a given door cannot produce.**  A
    hand-built match carries no creation, no income and no skip, so
    ``recorded_count``, ``refunded_count``, ``envelopes_created``,
    ``deposited_count``, ``skipped_count`` and ``already_skipped_count`` are
    structurally zero there
    -- and they are still emitted, because an audit trail whose FIELDS depend
    on which door wrote the row cannot be queried across the two.

    Args:
        outcome: The :class:`~app.services.statement_match.BatchOutcome` the
            door applied.

    Returns:
        The keyword arguments, ready to splat into ``log_event``.
    """
    return {
        "applied_count": outcome.applied_count,
        "refused_count": outcome.refused_count,
        "settled_count": outcome.settled_count,
        "corrected_count": outcome.corrected_count,
        "redated_count": outcome.redated_count,
        "repriced_count": outcome.repriced_count,
        "residual_count": outcome.residual_count,
        # A STRING, because the audit trail stores what was written rather than
        # a float of it -- the discipline every money field in this log keeps.
        "residual_total": str(outcome.residual_total),
        "recorded_count": outcome.recorded_count,
        # Added at plan step ``bank_import:X-gj-2b-3``, when
        # ``recorded_count`` split by direction -- for this function's own
        # stated reason: an audit trail whose fields depend on which door or
        # which release wrote the row cannot be queried across them, and two
        # counts have been forgotten from this list once already.
        "refunded_count": outcome.refunded_count,
        "envelopes_created": outcome.envelopes_created,
        "deposited_count": outcome.deposited_count,
        # Added at plan step ``bank_import:X-gj-4b`` with the fourth act
        # class, for this function's own stated reason.  **They are the two
        # counts here of acts that LANDED and moved no money** -- the scope
        # matters, because ``refused_count`` above reports no money either and
        # ``here`` is the whole dict.  Emitted anyway: a trail whose fields
        # depend on which door wrote the row cannot be queried across them,
        # and *how many lines this pass stopped asking about* is exactly the
        # question a skip answers.
        #
        # *This sentence has been miscounted twice.*  It said ONE count while
        # introducing two, a correction was appended BESIDE the false claim
        # rather than replacing it so both stood, and the number was right for
        # a set the words did not name.  A count claim whose scope is left to
        # the reader is how "one" survived three passes.
        "skipped_count": outcome.skipped_count,
        "already_skipped_count": outcome.already_skipped_count,
    }


def _messages(errors, path=()):
    """Yield every message in a Marshmallow error structure, WITH its path.

    **A LIST field's errors are keyed by INDEX, not flat**, so
    ``{"matches": {0: {"lines": {0: ["That is not a bank line this page could
    have shown you."]}}}}`` is the
    ordinary shape here rather than an exotic one -- and a flattener assuming
    ``{field: [str]}`` raises ``TypeError`` inside the handler that exists to
    render a refusal.  It is also why ``error_fragments.flatten_schema_errors``
    is not used: that helper's own contract is the flat shape, and one reviewed
    pass nests three levels deep.

    **The PATH travels with the message, and dropping it was a real cost at
    this volume.**  Marshmallow reports one entry per bad value, so a stale
    page with forty unparseable ids rendered ``Not a valid id.; Not a valid
    id.; ...`` forty times -- no item named, no remedy, and nothing the owner
    could act on.  Named by adversarial design review 2026-08-19.

    Args:
        errors: A Marshmallow error value -- a mapping, a list, or a message.
        path: The keys walked to reach it, innermost last.

    Yields:
        ``(path, message)`` for each leaf, in the order marshmallow reports
        them.
    """
    if isinstance(errors, dict):
        for key, value in errors.items():
            yield from _messages(value, path + (key,))
    elif isinstance(errors, (list, tuple)):
        for value in errors:
            yield from _messages(value, path)
    else:
        yield path, str(errors)


def refusal_sentence(errors) -> str:
    """Return one sentence naming what a schema refused, and where.

    **Stated once for every statement door**, because the import page had grown
    its own spelling twice over -- a byte-identical ``"; ".join(...)`` across
    the values, which loses which FIELD each message came from and repeats a
    message a nested schema raised per item.  This is the review screen's
    version, which its sibling delete door already used, moved to where all of
    them can reach it; pylint's cross-file ``duplicate-code`` named the copy,
    and the copy was the worse one.

    **De-duplicated on the MESSAGE and counted**, because forty copies of one
    sentence is not forty facts.  Each distinct message names the items it came
    from, so an owner is told which ticked thing to untick rather than that
    something, somewhere, is invalid.

    Args:
        errors: Marshmallow's ``validate()`` structure.

    Returns:
        The banner text.
    """
    by_message: "dict[str, list[str]]" = {}
    for path, message in _messages(errors):
        where = ".".join(str(part) for part in path)
        by_message.setdefault(message, [])
        if where and where not in by_message[message]:
            by_message[message].append(where)
    parts = []
    for message, places in by_message.items():
        parts.append(f"{message} ({', '.join(places)})" if places else message)
    return "; ".join(parts)
