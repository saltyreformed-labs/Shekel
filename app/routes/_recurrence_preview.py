"""
Shekel Budget App -- Recurrence occurrence preview

The read-only HTMX fragment that shows the next five pay periods a recurrence
would land in, and the helpers that build it.  The endpoint is deliberately
kind-agnostic -- both the transaction-template form and the transfer-template
form point their live preview at it (``_recurrence_fields.html``) -- so none of
this belongs inside the transaction-template CRUD module it used to live in.
``templates.preview_recurrence`` is now the route decorator and one call.

The preview reads request args and never writes: it RESOLVES the submitted
recurrence through the same producer a save resolves through
(:func:`app.services.recurrence.resolve`, reached through the composed door
:func:`app.services.recurring_definition.resolved_submission`), so what the
user is shown is what saving would produce.  Before plan step R2c-1 it built
the rule by hand and derived the ``Every N Periods`` phase inline, which is
exactly the kind of second copy of a derivation the seam exists to remove.

**It reads the DESTINATION too, since plan step R7d-f-2** (plan ledger row
**REC-515**).  A transfer into a loan stops when the loan does, and that stop
is derived rather than stored, so a preview that walked the submitted rule
alone listed occurrences past the loan's payoff whenever fewer than five
remained -- on the one surface whose contract is the sentence above.  The
transfer form's ``to_account_id`` rides on the request, is resolved through
the ownership gate (404 for a missing id and a foreign one alike, the house
rule), and the walk is narrowed by the same door every other reader of a loan
payment's schedule takes.  The transaction form has no destination control,
sends none, and is narrowed by nothing -- which is the door's own answer for
a definition that pays into no account.

**It reads where the definition's BOOKS open too, since plan step
``pay_calendar:C18-a``** (rulings **R-PC85**, **R-PC86**).  A save bounds a
definition's occurrences by the books of every account it moves money in, so
the transaction form's ``account_id`` and the transfer form's
``from_account_id`` ride on the request beside ``to_account_id``, each
through the same ownership gate, and the walk drops an occurrence whose row
would land on or before the latest of their openings -- the same composition
(``BalanceContext.resolved_for``) the save's own walk takes.

**It stopped building a transient ROW at plan step R-F6.**  It used to author
the submission onto an unsaved ``RecurrenceRule`` and hand that to
``rule_occurrences``, which read it straight back into a spec -- a submission
turned into a fake row and back to answer a question about a schedule.  That
round-trip also stopped being expressible: a rule belongs to exactly one
definition (``ck_recurrence_rules_one_owner``) and the preview has none, since
the template it previews may not exist yet.

Route-layer module rather than service because these read ``request`` and
``current_user``; the leading underscore marks it route-internal.
"""
import logging
from datetime import date

from flask import abort, request
from flask_login import current_user
from markupsafe import Markup

from app.enums import PeriodPlacementEnum, RecurrenceUnitEnum
from app.models.account import Account
from app.services.balance_at import BalanceContext
from app.services.pay_calendar import DerivedPeriod
from app.services.recurrence import (
    NEVER_ENDS,
    EndBoundInputError,
    MonthReading,
    RecurrenceResolutionError,
    RecurrenceSpec,
    end_bound_from_token,
    modelled_placement,
    modelled_unit,
    month_reading_from_token,
    occurrence_placements,
    placed_periods,
)
from app.services.recurring_definition import (
    UnsavedDefinition,
    resolved_submission,
)
from app.utils.auth_helpers import get_or_404
from app.utils.digit_strings import integer_arg

logger = logging.getLogger(__name__)

#: How many upcoming occurrences the fragment lists.
PREVIEW_OCCURRENCE_LIMIT = 5


def _submitted_iso_date(field: str) -> date | None:
    """Return an ISO date query argument, or ``None`` when it is unusable.

    ``date.fromisoformat`` raises ``ValueError`` on anything that is not an
    ISO date, and this endpoint reads its values straight from
    ``request.args`` -- so ``?end_date=garbage`` was an unhandled 500 for any
    signed-in user.  Found by a neutral review of plan step R4a, which had
    closed the OTHER four unvalidated arguments through
    :class:`~app.services.recurrence.RecurrenceResolutionError` and then
    claimed the whole class; a value parsed BEFORE the seam cannot be refused
    by it.

    An unparseable bound is treated as ABSENT rather than refused, and the
    asymmetry with the day / month / interval refusals is deliberate: those
    are values the rule would be SAVED with, so a wrong one must not be
    previewed as though it were fine.  A bound that is not a date is not a
    bound either form can submit -- ``<input type="date">`` and the
    Marshmallow schema both produce ISO or nothing -- so it is a hand-crafted
    query string, and the honest preview of "no parseable bound" is the
    unbounded rule.

    **Both bounds share this one parser since plan step R7b-4**, which gave
    the OPENING bound a control: two copies of "parse it or drop it" is the
    shape that leaves one of them missing a fix the other got.  An
    out-of-RANGE opening bound is a different question and is NOT answered
    here -- ``resolve`` refuses it for every caller, and this endpoint's
    existing handler reports it (see :func:`recurrence_preview_fragment`).

    Args:
        field: The query-argument name to read.

    Returns:
        The parsed date, or ``None`` when absent or unparseable.
    """
    submitted = request.args.get(field)
    if not submitted:
        return None
    try:
        return date.fromisoformat(submitted)
    except ValueError:
        logger.info(
            "Recurrence preview ignored an unparseable %s for user %s",
            field, current_user.id,
        )
        return None


def build_preview_spec(
    unit: RecurrenceUnitEnum,
    placement: PeriodPlacementEnum,
    starts_on: date,
    reading: MonthReading,
) -> RecurrenceSpec:
    """Build the authored recurrence the preview request describes.

    **It states the recurrence rather than building a ROW, since plan step
    R-F6**, and deleting that round-trip is the point.  The route used to hand
    these arguments to ``build_transient_rule``, which wrote them onto an
    unsaved :class:`~app.models.recurrence_rule.RecurrenceRule` that
    ``rule_occurrences`` then read straight back into a spec -- so a submission
    became a fake row and a spec again to answer a question about a schedule.
    The occurrence walk takes the RESOLVED recurrence, which
    :func:`~app.services.recurrence.resolve` produces from the spec directly.

    That round-trip also stopped being expressible: a recurrence rule belongs
    to exactly one definition now (``ck_recurrence_rules_one_owner``), and the
    preview has no definition -- it is showing what saving WOULD produce, for a
    template that may not exist yet.

    Takes the AXES the form authors rather than a pattern id (plan step
    R7b-2): the preview reads the same two controls the save posts, so neither
    can read one submission as a different cadence than the other.  Before that
    it took the closed-set id and decoded it here, which was one translation
    the form no longer needs -- and the ``ref`` row it fetched before plan step
    R2e-2 was written onto the transient rule's ``pattern`` relationship for a
    reader that does not exist.

    **The three counts -- ``interval_n``, ``max_per_month``,
    ``max_occurrences`` -- are held to one range here and to no rule.**  Each
    is read through :func:`~app.utils.digit_strings.integer_arg`, so a value
    outside a PostgreSQL ``integer``'s range is read exactly as an unparseable
    one already was, as the argument's default (``interval_n`` 1, the other
    two absent), and the preview lists what THAT rule would generate while
    the save refuses the submission (the form schema bounds each of the three
    within its column's range).  Every bound the RULE has -- a
    non-positive interval, a nominal day the first occurrence does not leave
    open, a weekday reading it cannot be read by, a per-month ceiling beside a
    unit that cannot hold it -- is refused by ``RecurrenceSpec`` or the
    authoring seam, which is the caller's ``RecurrenceResolutionError``
    handler's job; see :func:`recurrence_preview_fragment` for why those are
    stated once, on the column and its mirror in ``resolve``, rather than a
    third time on this endpoint.

    Args:
        unit: The submitted cadence unit, already checked as modelled by the
            caller.
        placement: The submitted placement, likewise.
        starts_on: The submitted first occurrence, already parsed by the
            caller.
        reading: How the "repeating on" control reads it, already parsed by
            the caller through the schema's own token reader.

    Returns:
        The :class:`~app.services.recurrence.RecurrenceSpec` the request
        describes.  UNRESOLVED -- the caller resolves it against the owner's
        schedule, which is where every refusal this endpoint reports comes
        from.
    """
    return RecurrenceSpec(
        user_id=current_user.id,
        unit=unit,
        # The rule's FIRST OCCURRENCE (plan step R7c-b).  It replaced an
        # owner-checked ``start_period_id`` at plan step R7b-4, and the
        # ownership probe went with it rather than being relocated: a DATE
        # names nothing of anyone else's, so the disclosure this endpoint
        # was guarded against (audit finding H3 -- another user's
        # pay-period structure) is not expressible in the argument any
        # more.
        starts_on=starts_on,
        interval_n=request.args.get("interval_n", type=integer_arg, default=1),
        placement=placement,
        # How the first occurrence is READ (plan step recurrence:R8-c): a
        # clamped month-end day or a weekday reading, from the one token the
        # "repeating on" control posts.  A choice the date does not allow is
        # refused by ``RecurrenceSpec`` itself, which is the caller's
        # ``RecurrenceResolutionError`` handler's job.
        nominal_day=reading.nominal_day,
        weekday_reading=reading.weekday_reading,
        # The per-month ceiling (plan step salary:R15-a), read the way the
        # nominal day is: refused by ``RecurrenceSpec`` and ``resolve``
        # beside a unit that cannot hold it or below one, so the preview
        # lists the same dates the save would generate -- "at most 2 a
        # month" previews the third paycheck skipped.
        max_per_month=request.args.get("max_per_month", type=integer_arg),
        # Composed through the SUBMISSION door, not the storage one (plan
        # step R7b-3).  These are query args -- a submission -- so a
        # mistake in them is user input, and
        # ``end_bound_from_columns`` would have reported it as a row
        # written around ``ck_recurrence_rules_single_end_bound``: a log
        # line asserting corrupted data that does not exist.  The form
        # posts the same three controls the save does, so the preview
        # reads one submission the way the save reads it.
        end_bound=end_bound_from_token(
            request.args.get(
                "recurrence_end_mode", default=NEVER_ENDS.token,
            ),
            end_date=_submitted_iso_date("end_date"),
            max_occurrences=request.args.get(
                "max_occurrences", type=integer_arg,
            ),
        ),
    )


def render_preview_html(
    preview_periods: list[DerivedPeriod],
) -> Markup:
    """Render the occurrence-preview HTML fragment for *preview_periods*.

    Args:
        preview_periods: The matched
            :class:`~app.services.pay_calendar.DerivedPeriod` values to list --
            the calendar's own view of a pay period, which is what
            :func:`~app.services.recurrence.occurrence_placements` answers
            in.  Only the two dates are rendered.

    Returns:
        The fragment markup.
    """
    items = "".join(
        f"<li>{p.start_date.strftime('%b %d, %Y')} - {p.end_date.strftime('%b %d, %Y')}</li>"
        for p in preview_periods
    )
    html = (
        f"<small class='text-muted'>Next {len(preview_periods)} occurrences:</small>"
        f"<ul class='list-unstyled mb-0 ms-2'><small>{items}</small></ul>"
    )
    return Markup(html)


#: The line shown when the request does not describe a previewable rule.
#:
#: Named because THREE states share it and users read it: no cadence chosen
#: ("does not repeat"), no first occurrence stated, and a cadence the authoring
#: seam refuses.  From the form's side all three mean the same thing -- there is
#: nothing to preview for what you typed -- and giving them one wording is what
#: stops the fragment from developing three near-identical strings.
NOTHING_TO_PREVIEW = "No preview for this cadence"


def _muted(text: str) -> str:
    """Return *text* as the fragment's muted one-liner.

    The markup was written out at each of six return points, which is the shape
    that lets one of them drift a class.

    Args:
        text: The sentence to show.

    Returns:
        The ``<small>`` markup.
    """
    return f"<small class='text-muted'>{text}</small>"


def _submitted_preview(
    starts_on: date | None,
) -> tuple[RecurrenceUnitEnum, PeriodPlacementEnum, date, MonthReading] | str:
    """Return the inputs a preview needs, or the line to show instead.

    Reading the request is a different job from walking the rule, and splitting
    them is what keeps :func:`recurrence_preview_fragment` inside the return
    count -- the guard plan step R7c-b added for an unstated first occurrence
    is what pushed it over.

    **Both submitted AXES are checked against what the application MODELS**
    (plan step R2e-2's rule, on the two fields plan step R7b-2 replaced its one
    with).  They used to be checked against the ``ref`` table, and the two are
    not the same set: a row no enum member names passes a table lookup and then
    raises inside the authoring seam, so the preview would 500 on the same
    input the picker refuses to offer.

    An ABSENT unit is the form's "does not repeat" option, which has no
    occurrences to preview.  An absent PLACEMENT is not the same state: a
    placement is the cadence's second half rather than an optional refinement,
    so it takes the unmodelled answer (via the ``or 0`` below, which no ``ref``
    row can carry) rather than being defaulted into a schedule the save would
    not produce.

    **The schedule is not read here since plan step R7d-f-2.**  This used to
    load the owner's calendar and refuse an empty one before the rule was
    looked at; the door the preview now resolves through
    (:func:`~app.services.recurring_definition.resolved_submission`) answers
    ``None`` for exactly that state, so the caller reads the one producer's
    answer rather than a second spelling of "has this owner any pay periods".
    ONE calendar still -- the read pass's, the same one the SAVE resolves
    against (plan step R4b-1's point, kept).

    Args:
        starts_on: The parsed ``starts_on`` argument, or ``None`` when it was
            absent or unparseable.  Passed in rather than read here so the
            caller states which argument it means.

    **The "repeating on" token is read here too** (plan step
    recurrence:R8-c), through
    :func:`~app.services.recurrence.month_reading_from_token` -- the reader
    the form schema asks, so the preview and the save read one submission
    one way.  A token no option posts is a hand-crafted query, answered with
    the line a choice the date does not allow gets -- there is nothing to
    preview for what was typed; an ABSENT one is the control's default, the
    same day of the month.

    Returns:
        ``(unit, placement, starts_on, reading)`` when the request describes a
        previewable rule, or the muted markup to render instead.
    """
    unit_id = request.args.get("recurrence_unit", type=integer_arg)
    if not unit_id:
        return _muted(NOTHING_TO_PREVIEW)
    unit = modelled_unit(unit_id)
    placement = modelled_placement(
        request.args.get("recurrence_placement", type=integer_arg) or 0,
    )
    if unit is None or placement is None:
        return _muted("Unknown cadence")
    reading = month_reading_from_token(request.args.get("repeats_on", ""))
    if reading is None:
        return _muted(NOTHING_TO_PREVIEW)
    # Since plan step R7c-b there is nothing to preview without a first
    # occurrence: a rule cannot be authored without stating when it first
    # happens, so an absent or unparseable value is a request the save could
    # not honour either.
    if starts_on is None:
        return _muted(NOTHING_TO_PREVIEW)
    return unit, placement, starts_on, reading


def _submitted_definition() -> UnsavedDefinition:
    """Return the accounts the request names, owner-checked, or abort 404.

    The transfer form's ``to_account_id`` control (plan step R7d-f-2, plan
    ledger row **REC-515**): the fact about a definition beyond its rule that
    decides where its occurrences STOP, because a transfer into a loan stops
    when the loan does.  **And, since plan step ``pay_calendar:C18-a`` (ruling
    R-PC85), the accounts that decide where they START**: the transaction
    form's ``account_id`` and the transfer form's ``from_account_id`` beside
    its ``to_account_id`` -- every account the definition moves money in,
    whose books the walk may not write below.  A save bounds by them, so a
    preview that did not would list a date saving would not generate.  The
    transaction form's envelope box rides for the same reason (ruling
    **R-PC89**: an envelope's row is compared with the books on its
    paycheck's last day); it is a flag, not an id, so no gate applies, and
    an absent or unreadable value is the unticked box.

    **An untrusted id becomes a row through the ownership gate and nowhere
    else.**  A missing account and another owner's account are both answered
    ``404`` (the house rule, and :func:`~app.utils.auth_helpers.get_or_404`
    logs the cross-user probe); neither can come from the form, whose
    ``<select>`` offers only the owner's own accounts, so both are hand-crafted
    queries and the strict answer costs no real user anything.  An
    unparseable value is ABSENT rather than refused, on the ground
    :func:`_submitted_iso_date` states for the two dates: the control cannot
    produce one, and the honest preview of "no readable destination" is the
    un-narrowed rule.  An absent id is the transaction form's normal request,
    and the door's own answer for a definition that pays into no account.

    Returns:
        The :class:`~app.services.recurring_definition.UnsavedDefinition`
        the door composes the derived stop and the books floor from.
    """
    return UnsavedDefinition(
        to_account_id=_submitted_account_id("to_account_id"),
        account_id=_submitted_account_id("account_id"),
        from_account_id=_submitted_account_id("from_account_id"),
        is_envelope=request.args.get("is_envelope") == "1",
    )


def _submitted_account_id(field: str) -> int | None:
    """Return the account id *field* names, owner-checked, or abort 404.

    ONE gate for the three account controls :func:`_submitted_definition`
    reads, so a new one cannot arrive un-checked.  Absent or unparseable is
    ``None`` (see :func:`_submitted_definition` for why); a present id is
    resolved through :func:`~app.utils.auth_helpers.get_or_404`, and a missing
    or foreign account is ``404``.

    Args:
        field: The query-argument name to read.

    Returns:
        The owner's account id, or ``None`` when the request names none.
    """
    account_id = request.args.get(field, type=integer_arg)
    if account_id is None:
        return None
    account = get_or_404(Account, account_id)
    if account is None:
        abort(404)
    return account.id


def recurrence_preview_fragment() -> str:
    """Return the preview fragment for the recurrence the request describes.

    The whole body of ``templates.preview_recurrence``, beside the
    helpers it composes rather than in the transaction-template CRUD module
    that merely routes to it.

    **Both submitted AXES are checked against what the application MODELS**
    (plan step R2e-2's rule, on the two fields plan step R7b-2 replaced its one
    with).  They used to be checked against the ``ref`` table, and the two are
    not the same set: a row no enum member names passes a table lookup and then
    raises inside the resolver :func:`build_preview_spec`'s output goes through --
    so the preview would 500 on the same input the picker refuses to offer.

    An ABSENT unit is the form's "does not repeat" option, which has no
    occurrences to preview, and it keeps its own message because users read it.
    An absent PLACEMENT is not the same state: a placement is the cadence's
    second half rather than an optional refinement, so it takes the unmodelled
    answer (via the ``or 0`` below, which no ``ref`` row can carry) rather than
    being defaulted into a schedule the save would not produce.

    **Every OTHER query arg is checked against no rule here, and plan step R4a
    made that a 200 instead of a 500.**  ``interval_n`` / ``starts_on`` /
    ``repeats_on`` / ``max_per_month`` / ``recurrence_end_mode`` /
    ``end_date`` / ``max_occurrences`` are read straight from
    ``request.args``; the two form schemas bound them, this endpoint holds the
    integer ones only to a PostgreSQL ``integer``'s range (since plan step
    balance:X-dj -- see :func:`build_preview_spec`), and it is reachable by
    anyone signed in.
    Measured at R4a on the arguments of the day: ``?interval_n=0`` raised out
    of the authoring seam, ``?month_of_year=13`` raised ``ValueError`` from
    ``monthrange(year, 13)`` inside the matcher R4a deleted,
    ``?day_of_month=-5`` raised ``ValueError`` from ``date(y, m, -5)``, and
    ``?day_of_month=32`` / ``?month_of_year=99`` answered 200 with a silently
    clamped or modulo-wrapped date.  Catching the ONE exception the resolution
    seam raises answers all five, and keeps each bound stated once -- on the
    column and its mirror in :func:`app.services.recurrence.resolve` -- rather
    than a third time here.  The day and month args are gone with the columns
    they named (plan step R7c-b); ``nominal_day`` took their place and took
    the same disposition, refused by ``RecurrenceSpec`` when the date leaves no
    such day open -- and ``repeats_on`` took ITS place at plan step
    recurrence:R8-c, its token read by :func:`_submitted_preview` and its
    pair with the date refused the same way.

    **``starts_on`` is the one that showed the rule needs maintaining rather
    than merely restating.**  R7c-b deleted the anchor walk that
    ``_MAX_START_DATE_YEAR`` was derived from, so the bound went with it -- but
    the failure it prevented had a second route: past the saved horizon the
    calendar PROJECTS a paycheck by adding ``cadence_days`` to a start, and
    ``?starts_on=9999-12-31`` overflowed that addition with an
    ``OverflowError`` this handler does not catch.
    ``_resolution._require_authored_start_window`` restores the bound at the
    seam, on the window the whole application already agrees on, so the muted
    line below covers it again.

    The two DATE bounds are the exception, and it took a second review to
    see the first of them: they are parsed BEFORE the seam and so cannot be
    refused by it.
    :func:`_submitted_iso_date` handles it -- BOTH closing-bound dates, through
    one parser since plan step R7b-4 -- and the docstring there says why an
    unparseable bound is dropped rather than refused.  **The accounts are
    the other exception, and they are REFUSED**: each id names a row, so it
    goes through the ownership gate (:func:`_submitted_definition`) before the
    door sees it.

    Returns:
        The fragment markup, or a muted one-line explanation when there is
        nothing to preview.

    Raises:
        BaselineMissingError: The destination is a configured loan and the
            owner has no baseline scenario (ruling **R-R30**), from the seam's
            own guard on the way to the derived stop.  The application-level
            handler answers it -- and what the browser then shows is stated
            rather than implied: ``recurrence_form.js`` fetches this fragment
            with a plain ``fetch()`` and no ``HX-Request`` header, so
            ``_recovery_response`` answers the FULL recovery page at ``200``
            and the script swaps that page into the preview ``<div>``.  The
            state is the broken invariant finding **F-10** names (registration
            bootstraps a baseline and nothing deletes one), the owner's edit
            form already rendered the recovery page one request earlier for
            the loan's own payment, and a fragment-aware branch in the handler
            is that handler's change, not this route's.  Every definition
            with no loan behind it previews for such an owner.
    """
    requested = _submitted_preview(_submitted_iso_date("starts_on"))
    if isinstance(requested, str):
        return requested
    unit, placement, starts_on, reading = requested
    definition = _submitted_definition()
    # The READ PASS, built here because this is the route (the 2026-08-16
    # ruling): the door resolves the rule against its calendar and folds the
    # destination loan in its scenario, so the schedule the preview walks and
    # the stop it is narrowed by are one pass's and cannot disagree.
    ctx = BalanceContext.build(current_user.id)

    # ``effective_from`` is a DISPLAY choice -- "show me the next five from
    # here" -- and so the route's, not the rule's.  It follows the submitted
    # first occurrence, so the list opens where the user says the rule does
    # rather than at today's paycheck; showing five occurrences the user
    # cannot see the start of is a preview that answers a question nobody
    # asked.
    effective_from = starts_on

    try:
        resolved = resolved_submission(
            build_preview_spec(unit, placement, starts_on, reading),
            definition, ctx,
        )
        if resolved is None:
            return _muted("No pay periods generated yet")
        # ``effective_from`` is this ROUTE's display choice, made above --
        # "show me the next five from here" -- never the rule's opening bound,
        # which is its anchor.  The retired ``match_periods`` adapter applied
        # the bound for its callers, which is how a caller's window came to
        # look like a property of the recurrence (defect D2); the PROJECTION is
        # still shared, so this surface and the generation seam cannot come to
        # disagree about which periods a rule fires in.  The walk reads the
        # COMPOSED closing off the resolved value, so a loan's derived stop
        # narrows it without this surface gaining a parameter.
        matching = placed_periods(
            occurrence_placements(resolved, ctx.calendar()),
            ending_on_or_after=effective_from,
        )
    except (RecurrenceResolutionError, EndBoundInputError) as exc:
        # The submitted arguments do not name a recurrence this application can
        # resolve OR can store.  The user gets a muted line either way, because
        # from the form's side both mean "there is nothing to preview for what
        # you typed" -- but the LOG carries the refusal's own message, which
        # names the field, the value and the constraint it broke.  Dropping it
        # would waste the one thing the door goes to length to produce.
        #
        # **The unstorable half arrives here because ``_author`` ENCODES before
        # it resolves**, which an adversarial review of plan step R7b-2 made
        # the order: a cadence with no closed-set pattern is refused before the
        # month walk touches it, so ``(10000, YEAR)`` is this muted line rather
        # than a ``ValueError`` out of ``date()`` that no handler here catches.
        logger.info(
            "Recurrence preview refused unresolvable arguments for user %s: %s",
            current_user.id, exc,
        )
        return _muted(NOTHING_TO_PREVIEW)

    preview_periods = matching[:PREVIEW_OCCURRENCE_LIMIT]
    if not preview_periods:
        return _muted("No matching periods found")
    return render_preview_html(preview_periods)


__all__ = [
    "NOTHING_TO_PREVIEW",
    "PREVIEW_OCCURRENCE_LIMIT",
    "build_preview_spec",
    "recurrence_preview_fragment",
    "render_preview_html",
]
