"""
Shekel Budget App -- Pay Period Routes

Generates the biweekly schedule and manages its lifecycle: extend the
schedule forward, add paychecks before the first or remove the ones before a
chosen paycheck, truncate the tail, and regenerate a wrong future tail.
All management actions are full-page POST + redirect (or a 422 re-render
of the settings dashboard when a discard needs confirming); they live on
the settings "pay-periods" section.
"""

import logging

from flask import Blueprint, flash, redirect, render_template, request, url_for
from flask_login import current_user

from app.utils.auth_helpers import require_owner

from app.extensions import db
from app.exceptions import (
    PayPeriodDiscardRequired,
    PayPeriodLocked,
    PayPeriodRemovalRefused,
    PayPeriodResetBlocked,
    PayPeriodUnresolved,
    ValidationError,
)
from app.routes._period_population import populate_new_periods
from app.routes.settings import render_settings_dashboard
from app.schemas.validation import (
    PayHistorySchema,
    PayPeriodExtendSchema,
    PayPeriodGenerateSchema,
    PayPeriodRegenerateSchema,
    PayPeriodRemoveEarlierSchema,
    PayPeriodResetSchema,
    PayPeriodTruncateSchema,
    PayScheduleSchema,
    rhythm_to_wire,
)
from app.services import (
    pay_period_admin,
    pay_period_write,
    pay_schedule_service,
)
from app.services.pay_calendar import calendar_at_schedule

logger = logging.getLogger(__name__)

pay_periods_bp = Blueprint("pay_periods", __name__)

_generate_schema = PayPeriodGenerateSchema()
_extend_schema = PayPeriodExtendSchema()
_truncate_schema = PayPeriodTruncateSchema()
_remove_earlier_schema = PayPeriodRemoveEarlierSchema()
_regenerate_schema = PayPeriodRegenerateSchema()
_reset_schema = PayPeriodResetSchema()
_schedule_schema = PayScheduleSchema()
_history_schema = PayHistorySchema()


def _pay_periods_redirect():
    """Redirect back to the settings pay-periods section."""
    return redirect(url_for("settings.show", section="pay-periods"))


def _summarize_errors(errors):
    """Flatten a Marshmallow error dict into one flash-able sentence."""
    parts = [
        f"{field}: {'; '.join(str(m) for m in messages)}"
        for field, messages in errors.items()
    ]
    return "Please correct the form: " + " | ".join(parts)


def _holds_paydays(user_id: int) -> bool:
    """Return whether this owner has a rhythm to CONTINUE rather than state.

    Plan step **pay_calendar:C14-f**, ruling **R-PC63**.  The discriminator
    between the two jobs ``POST /pay-periods/generate`` used to do at once:
    ESTABLISH a rhythm, or CONTINUE one.

    **It asks the PAYDAYS and not the schedule row, because that is the
    question its name asks** -- not because the difference is reachable today.
    ``get_schedule`` returning ``None`` implies no paydays
    (``fk_pay_periods_schedule``, plan step C4-b-2); the converse does not
    hold in the SCHEMA.  An owner holding the row and no ERA (plan step
    ``pay_calendar:C17-a``) holds no paydays either -- every batch that
    records one mints an era when none covers it -- and answers ``False``
    here without a calendar being built.

    *A first draft justified this by naming ``truncate_pay_periods`` and
    ``reset_pay_periods`` as producers of "a row with no paydays", and this
    step's adversarial review measured BOTH false*: truncate's own docstring
    records that the named period is always KEPT, "so THIS DOOR can never
    empty a schedule", and reset retires and re-records inside one
    ``record_paydays`` call.  **No door in ``app/`` produces that state.**  The
    predicate is written this way because "does this owner hold paydays" is
    the thing the dispatch actually turns on, and asking a proxy for it would
    be the substitution rule 14 exists to refuse -- not because a caller was
    censused and found.

    **A DIVERGENCE worth naming rather than leaving implicit**: the same
    question has three homes -- the template's ``pp_periods``, this, and
    the continue door's own empty-record refusal
    (``pay_period_write.continue_paydays``; ``extend_pay_periods``' ``not
    saved`` until plan step ``C17-c-2b``).  *Until plan step
    ``C17-a`` the extend door asked a second half too, ``nominal_anchor is
    None``, which this did not; that column is gone and the two doors ask one
    question again.*  The rule-14 remedy is still for the service to expose
    its own predicate so there is ONE producer, which is a change to that
    module's surface and not this step's.

    Args:
        user_id: The owning user.

    Returns:
        ``True`` when the owner holds at least one recorded payday, so the
        generate door CONTINUES their rhythm; ``False`` when it establishes
        one.
    """
    # ``schedule_row`` and not ``schedule``: this module registers a view
    # function of that name at ``POST /pay-periods/schedule`` (pylint W0621).
    schedule_row = pay_schedule_service.get_schedule(user_id)
    if schedule_row is None:
        return False
    facts = pay_schedule_service.ScheduleFacts.of(schedule_row)
    if facts is None:
        return False
    return bool(calendar_at_schedule(user_id, facts).saved())


def _append_periods(num_periods):
    """Continue the owner's existing rhythm by *num_periods* paychecks.

    **The ONE continue path, shared by both doors that reach it** (plan step
    **pay_calendar:C14-f**, ruling **R-PC63**).  ``POST /pay-periods/extend``
    is its obvious caller; ``POST /pay-periods/generate`` became the other
    when that door stopped asking an owner who already holds a rhythm to
    restate it.  Sharing the BODY rather than repeating it is what makes
    "the days come from the producer Extend already uses" structural: there
    is one call to :func:`~app.services.pay_period_admin.extend_pay_periods`,
    so the two doors cannot drift into two spellings of appending a paycheck.

    Args:
        num_periods: How many paychecks to append.

    Returns:
        A redirect to the settings pay-periods section, flashing either the
        count appended or the refusal.
    """
    return _record_periods(
        pay_period_admin.extend_pay_periods, num_periods,
        "Added {count} pay periods.",
    )


def _record_periods(door, num_periods, added):
    """Record *num_periods* paychecks through *door*, populate them, commit.

    **The one body behind every settings-page door that ADDS paychecks
    without stating a rhythm** -- the continue path (:func:`_append_periods`) and "Add earlier
    paychecks" (plan step ``pay_calendar:C18-b``) -- so the order below and
    the rollback that guards it are written once rather than per door.

    RECORD, then POPULATE, and the order is the whole of ruling **R-R38**:
    the read pass the recurrence resolves in is opened by
    :func:`~app.routes._period_population.populate_new_periods` AFTER the
    paydays exist, because a pass resolved before them holds a calendar that
    does not contain them and a loan whose payoff has not moved yet.  The
    door leaves the periods EMPTY; dropping the second call ships paydays
    with no rent, no paycheck and no recurring transfer in them.

    Args:
        door: The ``pay_period_admin`` door, called as ``door(user_id,
            num_periods)`` and returning the new periods, flushed and empty.
        num_periods: How many paychecks to add.
        added: The success message, with ``{count}`` for how many were.

    Returns:
        A redirect to the settings pay-periods section, flashing either the
        count added or the refusal.
    """
    try:
        new_periods = door(current_user.id, num_periods)
        populate_new_periods(current_user.id, new_periods)
    except ValidationError as exc:
        # Rolled back before the redirect: this request's command transaction
        # holds the owner's write lock, which it took before this view read
        # anything of the owner's (plan step ``balance:X-bn``,
        # :mod:`app.db_transaction`; no door takes it), and whichever of the
        # two calls above ran before the refusal may have flushed -- the
        # door's own refusals run before its first durable statement, the
        # repopulation's do not. The page this redirects to reads the owner's
        # schedule back, so it reads committed state either way.
        db.session.rollback()
        flash(str(exc), "danger")
        return _pay_periods_redirect()

    db.session.commit()
    flash(added.format(count=len(new_periods)), "success")
    return _pay_periods_redirect()


@pay_periods_bp.route("/pay-periods/generate", methods=["GET"])
@require_owner
def generate_form():
    """Redirect to settings dashboard pay periods section."""
    return redirect(url_for("settings.show", section="pay-periods"))


@pay_periods_bp.route("/pay-periods/generate", methods=["POST"])
@require_owner
def generate():
    """Generate pay periods from the submitted form data."""
    errors = _generate_schema.validate(request.form)
    if errors:
        return render_template("pay_periods/generate.html", errors=errors), 422

    data = _generate_schema.load(request.form)

    # **THE DOOR ASKS ONE JOB'S QUESTIONS, NOT TWO** (plan step
    # ``pay_calendar:C14-f``, ruling **R-PC63**).  An owner who already holds a
    # rhythm is asked only HOW MANY MORE paychecks, and the days come from
    # the owner's own plan (``pay_period_write.continue_paydays``, since
    # plan step ``C17-c-2b``) by way of the shared continue path -- so
    # ``start_date`` and ``rhythm`` are not consulted for them.
    #
    # **P80's write is unrepresentable THROUGH THIS DOOR** (this step) **and
    # through every other since plan step ``pay_calendar:C17-c-2a``.**  This
    # door accepted any payday at or after the owner's floor, so three posts
    # naming unrelated days wrote ``[2026-01-02, 2026-01-16, 2026-07-31]`` at
    # cadence 14 and derived a 196-day paycheck -- six months of rows filing
    # into one grid column, with ``scripts/integrity_check.py`` reporting
    # green.  That is shut here.
    #
    # *A first draft of this comment claimed P80 became UNWRITABLE, and this
    # step's own adversarial review measured that FALSE.*  ``regenerate``
    # rendered a "Corrected first payday" with no ceiling -- only
    # ``pay_period_batch.reject_backward_payday``'s FLOOR -- so the same
    # irregular set stayed writable in three form fields: MEASURED through
    # the real route on a clean owner, paydays
    # ``[2026-01-02 .. 2026-03-13, 2026-07-31, ...]``, a **140-day gap**, HTTP
    # 200.  The root remedy was a CEILING where the floor already is, closing
    # every door at once rather than one at a time, and whether an owner may
    # record a GAP at all was an era question (**C17**): ruling **R-PC67** said
    # a hole is UNREPRESENTABLE, and ``C17-c-2a`` built the ceiling
    # (``pay_period_batch.reject_skipped_paycheck``, asked of every batch by
    # the one writer).  **P80 is CLOSED there.**
    #
    # The step was originally specified as a CHECK for that state
    # (**R-PC55**); a check is a reconciler for a duplication, and rule 14 says
    # delete a home instead.  **R-PC55's supersession rides with R-PC63**, on
    # its ruling card.
    #
    # **The submitted fields are IGNORED rather than refused, and that is this
    # arc's own precedent rather than a shortcut.**  Plan step C3-b deleted
    # ``cadence_days`` from the extend door at finding **P29**, and
    # :class:`~app.schemas.validation.pay_periods.PayPeriodExtendSchema` states
    # the disposition: an old client that still posts one "is not refused --
    # the value is simply ignored, which is now what it means".  Same arc, same
    # weld, opposite door.  **The analogy is not exact and the gap is stated
    # rather than glossed**: P29 DELETED the field, while ``start_date`` here is
    # still ``required=True``, so a client posting only ``num_periods`` is
    # refused 422 for a field this branch discards.  Completing it means
    # dispatching BEFORE schema validation, which is a shape change this step
    # did not take.
    #
    # The card stops offering the three fields to such an owner
    # (``settings/_pay_periods.html``) -- but the UI is not the control and a
    # 422 re-render still hands them the full card, so this line is.
    if _holds_paydays(current_user.id):
        return _append_periods(data["num_periods"])

    try:
        # One call, because recording the paydays and capturing the cadence
        # extend / the rolling top-up continue from is ONE operation -- the
        # pair was two lines here and would have been two more in
        # ``registration_service.register_user`` at plan step X-ad-a.  Plan step C3-b
        # folded the pair INTO the writer as the cadence rule, so
        # ``establish_schedule`` had nothing left to compose.
        periods = pay_period_write.record_paydays(
            user_id=current_user.id,
            first_payday=data["start_date"],
            num_periods=data["num_periods"],
            # The schema built the value from the kind control and its
            # arm's inputs (plan step pay_calendar:C17-d-3, ruling R-PC84);
            # no route spells a cadence's constructor.
            rhythm=data["rhythm"],
        )
        # POPULATE, like every other door that creates a pay period (ruling
        # **R-R38**).
        #
        # **This branch READS as first-time-only and now IS, which is plan step
        # C14-f's doing rather than something that was always true.**  Until
        # ``R-PC63`` the dispatch above did not exist, so ``record_paydays``'
        # forward-only rule let this door accept any payday AFTER the owner's
        # last and it behaved as an extend that skipped every template --
        # measured through this route at 3 appended periods holding 0 template
        # rows, with "Generate pay periods" one click away in the main nav on
        # every screen.  That was ledger row **D58**, closed by adding this
        # call.  The call STAYS: D58's remedy was to populate, and C14-f moved
        # the owner it was protecting onto the continue path, which populates
        # too.  Deleting it here would re-open D58 for the only owner still
        # reaching this line.
        #
        # A genuinely NEW owner is unaffected twice over: no template can
        # exist yet, and this route is reachable only once they have an
        # account, so the pass finds nothing to generate.
        populate_new_periods(current_user.id, periods)
    except ValidationError as exc:
        # Forward-only rule (ruling R-PC1, plan step C3-b): a payday that would
        # land BETWEEN two existing ones is rejected.  Surfaced on the
        # start_date field, mirroring the schema 422 -- and that attribution
        # is PROVABLE rather than assumed.  ``record_paydays`` refuses for
        # SEVEN reasons (its own ``Raises:`` block is the census) -- an
        # undatable payday, a batch size out of range, a cadence out of
        # range, a first payday off the rhythm's grid (plan step
        # ``pay_calendar:C17-d-2``), a convention the cadence cannot carry,
        # the forward-only floor, and the skipped-paycheck ceiling (plan step
        # ``C17-c-2a``) -- and the first five cannot reach this line:
        # ``fields.Date`` guarantees a plain ``date``, and
        # ``PayPeriodGenerateSchema`` bounds the batch size and the cadence to
        # exactly the ranges the writer and the column accept AND asks the
        # grid question and the cadence-convention pair through
        # ``validate_derivable_rhythm`` (the grid question since plan step
        # ``C17-d-3``), so the schema's own 422 answers them first.  The
        # ceiling cannot either, for the reason the floor cannot (next
        # paragraph): with no surviving payday it returns before judging.
        # *An adversarial review of C17-d-3 found this count at SIX with the
        # ceiling missing -- the same class as the C4-c correction below.*
        #
        # **Since ``C14-f`` the FLOOR cannot reach this line either, and the
        # enumeration above inverted rather than shrank** (found by this step's
        # adversarial review).  The dispatch means this branch runs only for an
        # owner holding ZERO paydays, so ``record_paydays`` computes an empty
        # ``surviving_paydays`` and ``reject_backward_payday`` has nothing to
        # bound against.  What can still raise here is ``populate_new_periods``
        # below -- whose message this then renders under the date box, which is
        # exactly the misattribution the paragraph above warns about.  Narrowing
        # that is a real fix and is NOT this step's: it needs its own case
        # forcing the repopulation to raise, and no test covers this branch's
        # 422 today.  Reported rather than silently left.
        #
        # *The FOURTH reason arrived at plan step ``pay_calendar:C14-b``, and
        # this comment's own warning is what caught it*: that step's first
        # draft refused the pair at the write door alone, so a two-day cadence
        # chosen with an early-pay convention rendered "Days between paydays
        # must be at least 4..." beneath "First Payday".  The schema-level
        # cross-field rule is what put the message back on the control the
        # owner would have to change.
        #
        # *Both halves of that sentence were false until plan step
        # ``pay_calendar:C4-c`` corrected them* (adversarial review,
        # 2026-09-01).  It said THREE reasons, which was right only while the
        # cadence bound was asked inside ``_apply`` rather than at the door;
        # and it said ``_CADENCE_DAYS_RANGE`` "takes the TIGHTER of the two
        # floors, which is the writer's", which C4-c deleted -- there is one
        # floor now, ``ck_pay_schedule_cadence_range``'s, and the schema reads
        # it directly.  The conclusion held throughout; the proof of it did
        # not.
        #
        # The rollback is what makes the 422 clean.  ``record_paydays`` runs
        # every refusal BEFORE its first durable statement, so nothing is
        # staged when IT is the raiser -- but the populate above it can raise
        # after flushing, and the response below re-renders a form, which
        # should not sit on a unit of work whose emptiness depends on reading
        # a writer.
        db.session.rollback()
        return render_template(
            "pay_periods/generate.html",
            errors={"start_date": [str(exc)]},
        ), 422
    db.session.commit()

    flash(f"Generated {len(periods)} pay periods.", "success")
    return redirect(url_for("grid.index"))


@pay_periods_bp.route("/pay-periods/extend", methods=["POST"])
@require_owner
def extend():
    """Append pay periods to the end of the schedule."""
    errors = _extend_schema.validate(request.form)
    if errors:
        flash(_summarize_errors(errors), "danger")
        return _pay_periods_redirect()

    data = _extend_schema.load(request.form)
    # The body moved to ``_append_periods`` at plan step ``pay_calendar:C14-f``
    # so this door and the generate door share ONE continue path rather than
    # two spellings of it.  Nothing about this door's behaviour changed.
    return _append_periods(data["num_periods"])


@pay_periods_bp.route("/pay-periods/earlier", methods=["POST"])
@require_owner
def add_earlier():
    """Add paychecks before the first one ("Add earlier paychecks").

    Plan step ``pay_calendar:C18-b`` (ruling **R-PC87**): Extend's twin at
    the schedule's other end, asking only how many -- the same one field, so
    the same schema -- and recording the paydays the owner's earliest rhythm
    projects just before their first
    (:func:`~app.services.pay_period_admin.add_earlier_pay_periods`).  Its
    refusals -- a payday before the owner's stated history (ruling
    **R-PC104**) or before the application's calendar -- come back as a
    flash, like every sibling action on this settings section.
    """
    errors = _extend_schema.validate(request.form)
    if errors:
        flash(_summarize_errors(errors), "danger")
        return _pay_periods_redirect()

    data = _extend_schema.load(request.form)
    return _record_periods(
        pay_period_admin.add_earlier_pay_periods, data["num_periods"],
        "Added {count} earlier paycheck(s).",
    )


@pay_periods_bp.route("/pay-periods/remove-earlier", methods=["POST"])
@require_owner
def remove_earlier():
    """Remove the paychecks before a chosen one ("Remove earlier paychecks").

    Plan step ``pay_calendar:C21`` (rulings **R-PC108** to **R-PC111**): the
    undo of :func:`add_earlier`, shaped as :func:`truncate` is at the other
    end -- the form posts the paycheck to START FROM by id, and every
    paycheck before it goes
    (:func:`~app.services.pay_period_admin.remove_earlier_pay_periods`).
    Nothing is populated, since nothing is recorded.

    **Every refusal is a flash, and nothing is left staged**: an
    id that is not the owner's (``PayPeriodUnresolved``, one message for "no
    such" and "not yours", as at truncate), a paycheck holding money or money
    dated inside the removed ones (**R-PC109**), a removal taking every
    payday of the earliest pay rhythm (**R-PC110**), and one that would
    change a posted total the ledger's re-syncs do not rebuild (**R-PC114**)
    -- the last three ``PayPeriodRemovalRefused``: R-PC109's before the door
    writes, R-PC110's and R-PC114's after it rolls back its own savepoint.
    The catch is that class and not
    the generic ``ValidationError``, because both ledger re-syncs run below
    it: a refusal of theirs is a defect to surface, not advice to flash.
    The rollback here is for the page this redirects to, which reads the
    owner's schedule back.  There is no discard-confirm panel: the ruling
    refused one.
    """
    errors = _remove_earlier_schema.validate(request.form)
    if errors:
        flash(_summarize_errors(errors), "danger")
        return _pay_periods_redirect()

    data = _remove_earlier_schema.load(request.form)
    try:
        removed = pay_period_admin.remove_earlier_pay_periods(
            current_user.id, data["start_from_period_id"],
        )
    except (PayPeriodUnresolved, PayPeriodRemovalRefused) as exc:
        db.session.rollback()
        flash(str(exc), "danger")
        return _pay_periods_redirect()

    db.session.commit()
    flash(f"Removed {removed} earlier paycheck(s).", "success")
    return _pay_periods_redirect()


@pay_periods_bp.route("/pay-periods/truncate", methods=["POST"])
@require_owner
def truncate():
    """Delete the schedule tail beyond the chosen period."""
    errors = _truncate_schema.validate(request.form)
    if errors:
        flash(_summarize_errors(errors), "danger")
        return _pay_periods_redirect()

    data = _truncate_schema.load(request.form)
    try:
        deleted = pay_period_admin.truncate_pay_periods(
            current_user.id, data["keep_through_period_id"],
            confirm_discard=data["confirm_discard"],
        )
    except (PayPeriodLocked, PayPeriodUnresolved) as exc:
        # ``PayPeriodUnresolved`` is the service refusing an id that names no
        # pay period of this user's (plan step C3-a): a forged one, another
        # owner's, or a STALE one -- the confirm panel below re-submits the id
        # the user reviewed, and a concurrent truncate can delete that period
        # between the two posts.  **Its own class rather than the generic
        # ``ValidationError``**, which an adversarial review of this step
        # asked for: a catch on the base would flash "choose the period again"
        # for any future business-rule refusal raised anywhere below this
        # call, reporting a real defect as advice about a dropdown.
        #
        # It flashes rather than 404ing because every sibling action on this
        # settings form does, and the security property that matters is
        # intact: "not yours" and "does not exist" carry the same message, so
        # this door is not an existence oracle.  Which case it was is recorded
        # in the ACCESS log instead (``_log_unresolved_period``).
        #
        # **``ValidationError`` was the THIRD member here until plan step
        # ``pay_calendar:C4-c``, and removing it is that step's own argument
        # applied to this door.**  It joined at C3-b because the writer
        # re-projected the surviving last period from the stored cadence, and
        # ``budget.pay_schedule.cadence_days`` accepts 1 while a stored
        # ``end_date`` could not express a one-day period -- so a legacy owner
        # met an unhandled 500 here.  C4-c dropped that column: a delete now
        # removes rows and computes nothing, ``retire_paydays`` reaches
        # ``_apply`` with ``recording=[]`` so no era is minted or judged
        # (``mint_era`` since plan step C17-a), and the whole path below this
        # line raises no ``ValidationError`` at all.
        #
        # Leaving it would be the exact defect the paragraph above rejects for
        # ``PayPeriodUnresolved``: a business-rule refusal added anywhere under
        # ``_gate_deletable_tail`` in future would be flashed as advice about a
        # dropdown and silently rolled back, instead of surfacing.  Found by an
        # adversarial review of C4-c, 2026-09-01.
        #
        # BOTH refuse BEFORE the ``DELETE``, so nothing durable is staged; the
        # rollback is for the page this redirects to, which reads the owner's
        # schedule back and should read committed state.
        db.session.rollback()
        flash(str(exc), "danger")
        return _pay_periods_redirect()
    except PayPeriodDiscardRequired as exc:
        db.session.rollback()
        return render_settings_dashboard("pay-periods", extra={"pp_confirm": {
            "op": "truncate",
            "count": exc.count,
            "params": {
                "keep_through_period_id": data["keep_through_period_id"],
            },
        }}, status=422)

    db.session.commit()
    flash(f"Removed {deleted} pay period(s).", "success")
    return _pay_periods_redirect()


@pay_periods_bp.route("/pay-periods/regenerate", methods=["POST"])
@require_owner
def regenerate():
    """Rebuild the not-yet-started future tail from a corrected start."""
    errors = _regenerate_schema.validate(request.form)
    if errors:
        flash(_summarize_errors(errors), "danger")
        return _pay_periods_redirect()

    data = _regenerate_schema.load(request.form)
    try:
        new_periods = pay_period_admin.regenerate_pay_periods(
            current_user.id, data["new_start_date"], data["num_periods"],
            data["rhythm"],
            confirm_discard=data["confirm_discard"],
        )
        # The rebuilt tail comes back EMPTY; this fills it.  See the extend
        # route for why the pass may only be opened here (ruling R-R38).
        populate_new_periods(current_user.id, new_periods)
    except (PayPeriodLocked, ValidationError) as exc:
        # Rolled back for the reason the generate route states.  *Until plan
        # step C3-b composed the truncate and the rebuild into ONE writer call
        # this was load-bearing -- the tail was deleted before the new start
        # was validated, and its docstring promised a rollback no route made
        # (an adversarial review of C3-b found the gap).*  Every refusal in
        # ``record_paydays`` now runs before its first statement, so what the
        # rollback covers is the repopulation below it, which can raise after
        # flushing, and the page this redirects to reading committed state.
        #
        # **A first payday that skips a whole paycheck lands HERE, as a
        # refusal** (plan step ``pay_calendar:C17-c-2a``, ruling **R-PC67**).
        # Until then it was a SECOND confirmation -- ``PayPeriodGapRequired``,
        # a banner and a ``confirm_gap`` field -- and a confirmed gap was
        # still a gap (ledger row **P80**).  The writer's ceiling refuses it
        # with the day the owner must stay before, and the flash carries it.
        db.session.rollback()
        flash(str(exc), "danger")
        return _pay_periods_redirect()
    except PayPeriodDiscardRequired as exc:
        # The discard gate raises BEFORE the delete, so nothing is staged --
        # but this response re-renders the settings dashboard, which reads the
        # owner's periods.  Rolling back first means it reads committed state
        # rather than a session the service may have flushed into.
        db.session.rollback()
        return render_settings_dashboard("pay-periods", extra={"pp_confirm": {
            "op": "regenerate",
            "count": exc.count,
            "params": {
                "new_start_date": data["new_start_date"].isoformat(),
                "num_periods": data["num_periods"],
                # Back to the WIRE spelling, because the confirm banner
                # re-POSTs each of these as a hidden input and this door
                # requires the kind and the convention: the schema hands out
                # a ``Rhythm`` value, and a value rendered into ``value=""``
                # is not one this form can submit back.  ``rhythm_to_wire``
                # is the schema's own inverse -- the kind's token, the
                # chosen arm's inputs and the convention's id -- so the
                # round trip cannot mis-state a month kind.  Omitting any of
                # it would refuse every discard-confirm rather than only
                # mis-stating one.
                **rhythm_to_wire(data["rhythm"]),
            },
        }}, status=422)

    db.session.commit()
    flash(f"Rebuilt the schedule: {len(new_periods)} new period(s).", "success")
    return _pay_periods_redirect()


@pay_periods_bp.route("/pay-periods/reset", methods=["POST"])
@require_owner
def reset():
    """Wipe and rebuild the entire schedule (first-time-setup correction).

    Refuses unless the user explicitly confirmed and -- enforced by the
    service -- has no settled transactions.  The whole rebuild runs in one
    transaction this route commits; a service-side failure (the settled
    refusal, or an invalid start/cadence after the wipe) rolls back so
    nothing partial ships.
    """
    errors = _reset_schema.validate(request.form)
    if errors:
        flash(_summarize_errors(errors), "danger")
        return _pay_periods_redirect()

    data = _reset_schema.load(request.form)
    if not data["confirm"]:
        flash(
            "Confirm the reset to rebuild your entire schedule.", "danger",
        )
        return _pay_periods_redirect()

    try:
        new_periods = pay_period_admin.reset_pay_periods(
            current_user.id, data["new_start_date"], data["num_periods"],
            data["rhythm"],
        )
        # LAST, after the wipe, the rebuild and both posting re-syncs -- see
        # ``reset_pay_periods`` for why the re-syncs cannot see what this
        # writes and why generating AFTER them is the order R7d-c-2 needs, and
        # the extend route for why the pass opens here.
        populate_new_periods(current_user.id, new_periods)
    except (PayPeriodResetBlocked, ValidationError) as exc:
        # ``reset_pay_periods`` wipes every period before it records the new
        # schedule, so a refusal raised after that leaves the wipe, the
        # rebuild and both posting re-syncs staged in the session -- as does
        # one raised by the repopulation below it.  The settled-transaction
        # refusal happens before any of it; the rollback is for the rest.
        db.session.rollback()
        flash(str(exc), "danger")
        return _pay_periods_redirect()

    db.session.commit()
    flash(f"Reset your schedule: {len(new_periods)} new period(s).", "success")
    return _pay_periods_redirect()


@pay_periods_bp.route("/pay-periods/history", methods=["POST"])
@require_owner
def history():
    """Save how far back the owner's paychecks reach.

    Plan step **balance:X-bh-2** (ruling **balance:R-IA**).  The second door
    onto ``budget.pay_schedule.history_opens_on``, and the only one an existing
    owner has: registration asks the question once, and every owner who signed
    up before the column existed holds ``NULL`` with no sign-up form left to
    revisit.

    A cleared field stores ``NULL``, which is a real answer rather than a
    no-op -- "I have been paid this way longer than the app needs to know" --
    so this route submits whatever the schema loaded rather than skipping an
    absent value.  The service refuses a day outside the app's calendar window
    or after the owner's first recorded payday, and both come back as a flash
    rather than a 500.
    """
    errors = _history_schema.validate(request.form)
    if errors:
        flash(_summarize_errors(errors), "danger")
        return _pay_periods_redirect()

    data = _history_schema.load(request.form)
    try:
        pay_schedule_service.set_history_opening(
            current_user.id, data["history_opens_on"],
        )
    except ValidationError as exc:
        # Nothing is staged before the refusal -- the setter validates ahead of
        # its one assignment -- so this needs no rollback, unlike the
        # structural doors above it.
        flash(str(exc), "danger")
        return _pay_periods_redirect()

    db.session.commit()
    flash("Saved when your paychecks started.", "success")
    return _pay_periods_redirect()


@pay_periods_bp.route("/pay-periods/schedule", methods=["POST"])
@require_owner
def schedule():
    """Save the continuous-rolling-window configuration."""
    errors = _schedule_schema.validate(request.form)
    if errors:
        flash(_summarize_errors(errors), "danger")
        return _pay_periods_redirect()

    data = _schedule_schema.load(request.form)
    try:
        pay_schedule_service.set_rolling(
            current_user.id,
            enabled=data["rolling_enabled"],
            target_periods=data["rolling_target_periods"],
        )
    except ValidationError as exc:
        flash(str(exc), "danger")
        return _pay_periods_redirect()

    db.session.commit()
    flash("Rolling-window settings saved.", "success")
    return _pay_periods_redirect()
