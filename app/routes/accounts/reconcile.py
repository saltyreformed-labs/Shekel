"""
Shekel Budget App -- The reconcile panel's routes

The "which of these has your bank actually taken?" panel: its shared
context builder, the two DOM-id helpers its two mounts share, and its
GET / POST endpoints.

**The POST is ``record_reconciliation`` and the partial is
``_reconcile_panel.html``; both were named ``..._purchases`` until plan step
X-f2-c2.**  Ruling **R-EW** widens the offer set to everything a statement can
settle -- purchases nested under their envelope, the envelope's own close,
bills, transfer shadows -- and only the first of those four is a purchase, so
the old names claimed a scope the door no longer has.  Renamed in its own
zero-money commit ahead of the leaf that widens it, per ruling **R-EY**: a
rename inside a money-moving diff is a rename nobody reviews.

**Why it is not in :mod:`app.routes.accounts.anchor`.**  It was, and the
subject boundary was always there to cut along: ``anchor`` owns the WRITE
DOOR for a balance assertion -- form validation, the mutation, the
rendered refusals -- while everything here is about what is still
OUTSTANDING against an assertion that already landed.  ``anchor`` calls
:func:`prompt_fragment` after a successful true-up and that is the whole
of the coupling.

**Stated precisely, because a first version of this paragraph overstated
it**: ``anchor.py`` stood at 916 of pylint's 1000-line ceiling before plan
step X-f2-a, so the pressure was not pre-existing -- that step's own
difference-preview family is what reached the ceiling.  The split is
therefore the RIGHT cut made at the moment something forced a cut, not a
remedy for an already-binding gate.  Findings **N-152**, **N-156** and
**N-201** record the same ceiling on three SERVICE modules and rule the
same answer (a split, never a fourth round of shaving prose off a measured
claim); this is that answer applied a tier up.

**Two names left this module PUBLIC on the way out, deliberately.**
``app.routes.accounts.detail`` imported ``_panel_id`` and
``reconcile_context`` across module boundaries while the first was
underscore-private -- finding **N-33**'s shape, a private name that four
route modules reach for, so the name lies about its visibility.  A helper
with consumers in two modules is part of this module's interface;
:func:`panel_id` says so rather than being fenced by a convention nobody
can enforce.

Services boundary: this module owns the HTTP-shaped concerns (ownership
checks, form parsing, fragment rendering, ``HX-Trigger`` composition) and
delegates every read and write to :mod:`app.services.reconcile_service` and
:mod:`app.services.cash_ledger`.
"""

from decimal import Decimal

from flask import render_template, request
from flask_login import current_user
from sqlalchemy.orm.exc import StaleDataError

from app.exceptions import PageOutOfDate, ValidationError
from app.extensions import db
from app.models.account import Account
from app.routes.accounts._bp import accounts_bp
from app.routes.accounts._cash_page import (
    cash_detail_wrong_type,
    load_cash_account_or_404,
)
from app.schemas.validation import ShownLinesSchema
from app.schemas.validation.transactions import MarkDoneSchema
from app.services import cash_ledger, reconcile_service
from app.services.pay_calendar import PayCalendar, calendar_for
from app.utils.auth_helpers import require_owner
from app.utils.digit_strings import parse_row_id, parse_row_ids
from app.utils.error_fragments import flatten_schema_errors


def panel_id(account_id: int) -> str:
    """Return the reconcile panel's DOM id for one account.

    Stated once so the POST's re-render lands on whichever copy of the panel
    submitted it: the modal prompt roots at a fixed id, the detail page's
    section at a per-account one, and both post to the same route.

    PUBLIC because it has consumers in two modules -- this one and
    ``app.routes.accounts.detail``, which renders the detail page's copy of the
    panel.  It was ``_panel_id`` and was imported across that boundary anyway,
    which is finding **N-33**'s shape: a name whose underscore claims a
    visibility its call sites do not respect.

    Args:
        account_id: The account whose panel id to compose.

    Returns:
        The DOM id string.
    """
    return f"reconcile-panel-{account_id}"


def _submitted_corrections(
    form, tick_form: "reconcile_service.TickForm",
) -> dict[int, Decimal]:
    """Return ``{tick id: amount}`` for one tick form's amount boxes.

    Ruling **R-FB** gives a bill's tick a prefilled, editable figure, so the
    form carries one box per correctable offer, named by its own field rather
    than by position -- paired arrays would depend on the browser submitting
    two lists in the same order, which is a property of the document rather
    than of the form.  **Its prefix is the service's**
    (:class:`~app.services.reconcile_service.TickForm`, ruling **R-BAL145**):
    a row's box is ``settled_amount-<row id>`` and a transfer's
    ``transfer_amount-<transfer id>``, and this parses each with the member the
    offer rendered it from, so the name is spelled once.

    **Each value is validated by ``MarkDoneSchema`` -- the SAME schema the
    grid's Mark Paid loads** -- because it is the same question feeding the same
    parameter of the same verb: what actual amount did a human type for this
    settle.  A second field declaration here would be a second answer to "what
    is a valid money input", on a money path, which is exactly what this arc
    removes; and it brings the two-place Decimal, the ``>= 0`` range mirroring
    ``status_seam.Settlement``'s own refusal of a negative figure (the
    ``ck_transactions_settled_amount`` CHECK's through plan step
    ``balance:X-bi-4b-1``), and the empty-string-to-``None`` normalisation for
    free.

    An empty box loads as ``None`` and is DROPPED rather than recorded: a user
    who clears the figure is not asserting `$0.00`, they are declining to
    correct, and the row then settles at what it would have settled at anyway.

    Args:
        form: The submitted ``request.form``.
        tick_form: Which form's boxes to read.

    Returns:
        The corrections, keyed by the id the box names (a row id, or a
        transfer id).  Empty when no box was submitted.  A key whose offer is
        not correctable, or was not ticked, is ignored by the service -- this
        function does not know which those are and must not guess.

    Raises:
        ValidationError: On a value that is not a non-negative two-place
            decimal.  Validated BEFORE loading, the way
            ``anchor._anchor_submission`` does, so the failure arrives as this
            app's own exception carrying a flattened sentence rather than as a
            raw Marshmallow one -- which has no handler and would be a 500 on a
            door an ordinary crafted POST can reach.
    """
    corrections: dict[int, Decimal] = {}
    schema = MarkDoneSchema()
    prefix = tick_form.amount_prefix
    for field, raw in form.items():
        if not field.startswith(prefix):
            continue
        row_id = parse_row_id(field[len(prefix):])
        if row_id is None:
            continue
        errors = schema.validate({"settled_amount": raw})
        if errors:
            raise ValidationError(flatten_schema_errors(errors))
        amount = schema.load({"settled_amount": raw}).get("settled_amount")
        if amount is not None:
            corrections[row_id] = amount
    return corrections


def _submitted_shown_lines(form) -> "dict[int, frozenset[int]]":
    """Return ``{row id: bank line ids}`` -- what each row's caption NAMED.

    Plan step ``credit_card:CC-5-4a-5``, rulings **R-CC76** / **R-CC127**: a
    row whose tick would take a matched payment out of its matches prints a
    caption naming the bank lines it frees, and posts those ids back under
    its own hidden field (:attr:`~app.services.reconcile_service.TickForm.shown_prefix`
    plus the row's id).  PER ROW, because what the save named is the lines
    under the rows TICKED, and the save's one press compares their union with
    what the whole save frees (``reconcile_service.record_reconciliation``,
    ruling **R-CC135**).  Each value is read by the schema every other caption-bearing
    door loads (``ShownLinesSchema``, over ``ShownIds``), so a posted id means
    here what it means there; an empty value is a caption that named nothing.

    Args:
        form: The submitted ``request.form``.

    Returns:
        The named lines, keyed by row id; a row that posted no field is absent,
        which the writer reads as having named nothing.

    Raises:
        ValidationError: On a value that is not a comma-joined list of ids,
            flattened to this app's own exception as
            :func:`_submitted_corrections` does.
    """
    shown: dict[int, frozenset[int]] = {}
    schema = ShownLinesSchema()
    prefix = reconcile_service.TickForm.ROW.shown_prefix
    for field, raw in form.items():
        if not field.startswith(prefix):
            continue
        row_id = parse_row_id(field[len(prefix):])
        if row_id is None:
            continue
        shown[row_id] = _posted_ids(schema, raw)
    return shown


def _posted_ids(schema: ShownLinesSchema, raw: str) -> "frozenset[int]":
    """Return the ids one posted comma-joined value names; empty for an empty value.

    The one reading of a caption's posted ids for both of this panel's fields
    (:func:`_submitted_shown_lines`, :func:`_submitted_shared_lines`), through
    the schema every caption-bearing door loads.

    Raises:
        ValidationError: On a value that is not a comma-joined list of ids,
            flattened to this app's own exception.
    """
    errors = schema.validate({"shown_lines": raw})
    if errors:
        raise ValidationError(flatten_schema_errors(errors))
    return schema.load({"shown_lines": raw}).get("shown_lines") or frozenset()


#: Why a shared match's posted value is refused when it is not two non-empty
#: id lists joined by the separator -- a value no page posts (a shared match
#: always frees a line and always names two rows or more).
_MALFORMED_SHARED = "A shared match's warning came back without its lines and rows."


def _submitted_shared_lines(form) -> "tuple[reconcile_service.SharedShown, ...]":
    """Return what each shared match's warning NAMED: its lines, and the rows they need.

    Plan step ``credit_card:CC-5-4a-5`` (leaf 5c-2c-1), ruling **R-CC135**: a
    match naming the payments of several rows is warned under each of them,
    saying which rows must all close, and each warning posts
    ``"<line ids>;<row ids>"`` under
    :attr:`~app.services.reconcile_service.SharedMatch.FIELD`.  The save counts
    the lines as named only when every one of the rows is ticked
    (``reconcile_service.record_reconciliation``).  A match warned under two
    rows posts the same value twice and is read once.  Each half is read by
    the schema every caption-bearing door loads (``ShownLinesSchema``), so a
    posted id means here what it means there.

    Args:
        form: The submitted ``request.form``.

    Returns:
        One :class:`~app.services.reconcile_service.SharedShown` per distinct
        value posted; empty when none was.

    Raises:
        ValidationError: On a value that is not two NON-EMPTY comma-joined
            lists of ids joined by
            :attr:`~app.services.reconcile_service.SharedMatch.SEPARATOR` --
            an empty row half would name the lines whatever was ticked.
    """
    schema = ShownLinesSchema()
    shown = []
    for raw in dict.fromkeys(form.getlist(reconcile_service.SharedMatch.FIELD)):
        lines, separator, rows = raw.partition(
            reconcile_service.SharedMatch.SEPARATOR,
        )
        line_ids, row_ids = _posted_ids(schema, lines), _posted_ids(schema, rows)
        if not separator or not line_ids or not row_ids:
            raise ValidationError(_MALFORMED_SHARED)
        shown.append(reconcile_service.SharedShown(
            line_ids=line_ids, row_ids=row_ids,
        ))
    return tuple(shown)


#: What the panel says after it is drawn again from CURRENT state, so the
#: owner ticks again -- the remedy both of its out-of-date refusals share.
_TICK_AGAIN = "Here it is again -- tick what your statement shows."

#: What a concurrent edit reads as.  A statement is walked slowly and the grid
#: is open on another device as often as not, so this is ordinary rather than
#: exotic: the list re-renders from the CURRENT state and the user ticks again.
_STALE_MESSAGE = (
    "Something on this list changed while you were reconciling, so nothing "
    f"was recorded.  {_TICK_AGAIN}"
)

#: What a PARTLY-landed submission reads as.  Both arms drop an out-of-scope id
#: silently by design -- the set-operation form of "404 for not-found and
#: not-yours" -- and for the purchase arm that hides a column stamp, but here it
#: hides a status change, an amount and a ledger posting.  "Some of what you
#: ticked was already settled" is a different sentence from "saved", so it is
#: said.
_PARTIAL_MESSAGE = (
    "Some of what you ticked had already been settled elsewhere, so it was "
    "left alone.  The list below is what is still outstanding."
)


def _refusal(
    account: Account, statement: "reconcile_service.Statement",
    message: str,
):
    """Re-render the panel carrying *message*, as a designed 400.

    **The panel's ONE rejection surface**, and it exists because plan step
    X-f2-c2 gives this door two reachable refusals it did not have while it
    only stamped a column: a submitted amount that is not money, and the settle
    verb's own ``ValidationError`` for a transition a STALE panel can still
    ask for (a row someone cancelled on another device while the statement was
    being walked).  Neither has an application-wide handler, so without this
    both are 500s on an ordinary user action.

    It carries ``Shekel-Designed-Fragment: 1`` for the reason
    ``anchor._anchor_editor_error`` does: htmx leaves a 4xx non-swapping, so a
    refusal without the header renders NOTHING and the button reads as broken
    -- worse than the error it is reporting, and the exact failure
    ``prompt_fragment``'s own docstring records the kind gate having caused.

    **The caller has already rolled back.**  The re-render reads the offer set,
    so a session still holding half a reconciliation would render a panel
    describing a state that is about to be discarded.

    Args:
        account: The owned, attached account.
        statement: The already-resolved
            :class:`~app.services.reconcile_service.Statement` (finding
            **N-222**), carrying the calendar the refused write was measured
            against -- a second derivation across a rollback would be a second
            answer to one question.
        message: The user-facing reason, one sentence.

    Returns:
        The designed-fragment ``(body, 400, headers)`` triple.
    """
    return (
        render_template(
            "accounts/_reconcile_panel.html",
            error=message,
            **reconcile_context(
                account, panel=panel_id(account.id), statement=statement,
            ),
        ),
        400,
        {"Shekel-Designed-Fragment": "1"},
    )


def governing_statement(account: Account) -> "cash_ledger.AnchorPoint | None":
    """Return the ASSERTION this account is being reconciled against.

    **Resolved ONCE per request and threaded** (finding **N-222**).  This module
    used to compute it in three places inside one POST -- the writer's bound,
    the no-assertion early return, and the success re-render -- three walks of
    one column for one account on one day.  X-f2-c2 would have made it four, so
    the fix is the discipline ``BalanceContext`` applies one tier down: resolve
    at the top of the handler, pass it in.

    **It returned the raw DAY until plan step X-f3a-1** (ruling **R-FL**), and a
    day is not enough any more: a tick now records WHICH statement showed the
    money, and production carries three days on which Checking holds more than
    one assertion, so ``MAX(observed_on)`` cannot name one.  The whole record is
    also what the offer set's bound and the rendered caption were already
    reading a second query for.

    PUBLIC for the reason :func:`panel_id` is: ``app.routes.accounts.detail``
    renders the detail page's copy of the panel and needs the same assertion for
    the same context, so an underscore here would be finding **N-33**'s shape.

    Args:
        account: The account to resolve.

    Returns:
        The governing :class:`~app.services.cash_ledger.AnchorPoint`, or
        ``None`` for an account carrying no assertion at all.
    """
    # The NON-RAISING resolver, because "this account has never had a balance
    # declared" is a legitimate empty state for this panel where it is a broken
    # invariant for a reader.  Nothing here asks whether a movement is inside a
    # balance -- that question has one implementation
    # (``StatementCoverage.clearing_anchor_id``) and none of these is a second
    # one.
    return cash_ledger.governing_anchor(account.id)


def reconcile_statement(
    account: Account, calendar: PayCalendar,
) -> "reconcile_service.Statement | None":
    """Return the STATEMENT this account is being reconciled against.

    The route tier's construction of
    :class:`~app.services.reconcile_service.Statement` -- the value both halves
    of that package take (pay-calendar plan step C4-a-2) -- for every caller
    that holds no assertion; :func:`prompt_fragment` holds the one its true-up
    reported and builds its own from it (ruling R-CC79).  It pairs the
    governing assertion with the requester's pay calendar, which is what makes
    every row the panel offers datable and is the OWNERSHIP scope every arm
    narrows by.

    **The calendar comes from the caller and is not resolved here**, for
    :func:`governing_statement`'s reason: the cash detail page already holds one
    on its read pass (``ctx.calendar()``), and a second derivation inside one
    render is two answers to "what are this owner's paydays".  This package
    holds no read pass of its own, so the HTTP tier is where that read belongs
    (``pay_calendar:C11``).

    An account carrying NO assertion answers ``None`` -- there is nothing for an
    offer to be inside of -- and its caller renders the panel's empty state.
    On that path the caller has already paid for a calendar it does not use;
    the state is fixture-only in production (``cash_ledger.governing_anchor``),
    and resolving the calendar lazily would mean this value could not be built
    in one expression.

    Args:
        account: The account to reconcile.  Caller owns the ownership check.
        calendar: The requester's pay calendar.

    Returns:
        The :class:`~app.services.reconcile_service.Statement`, or ``None`` for
        an account whose owner has never declared a balance for it.
    """
    anchor = governing_statement(account)
    if anchor is None:
        return None
    return reconcile_service.Statement(calendar, account.id, anchor)


def reconcile_context(
    account: Account, panel: str,
    statement: "reconcile_service.Statement | None",
) -> dict:
    """Assemble the reconcile panel's context for one account.

    The ONE builder both surfaces read: the post-true-up prompt
    (:func:`prompt_fragment`) and the permanent section on the cash
    account's detail page.  Two builders would be two answers to "what is still
    outstanding on this account", which is the shape plan step S1-c exists to
    remove.

    Args:
        account: The account to reconcile.  Caller owns the ownership check.
        panel: The DOM id the inner partial roots at, so a POST's re-render
            swaps in place.  Named ``panel`` rather than ``panel_id`` because
            :func:`panel_id` is now a module-level function and a parameter
            shadowing it would make the two indistinguishable at a glance.
        statement: What is being reconciled, from :func:`reconcile_statement`
            or, for the prompt, from the assertion the true-up reported --
            resolved by the caller.  **Taken rather than resolved here**
            (finding **N-222**): the POST needs the SAME value for its writers,
            which stamp its assertion's id and date every settled row against
            its calendar, and a builder that re-derived it would be the second
            of two answers inside one request -- with a write in between them,
            which is when two answers become a wrong one.  ``None`` for an
            account carrying no assertion at all.

    Returns:
        The template context.  ``outstanding`` is an
        :class:`~app.services.reconcile_service.OutstandingSet`, and it reports
        itself empty (the partial says so) for an account with nothing to
        reconcile or no assertion at all.
    """
    # An account with no assertion has nothing for an offer to be INSIDE of,
    # so the empty set is built here rather than passing a sentinel value.  The
    # producer is never asked about a ``None`` statement, which is what keeps
    # its own fields non-optional.
    outstanding = (
        reconcile_service.OutstandingSet.empty()
        if statement is None
        else reconcile_service.outstanding_set(statement)
    )
    return {
        "account": account,
        "outstanding": outstanding,
        "reconciled_through": (
            None if statement is None else statement.observed_on
        ),
        # The SAME record, not a second resolution: ``resolve_anchor`` and
        # ``governing_anchor`` share ``_governing_row``, so re-asking here would
        # be one request putting one question twice and trusting the answers to
        # agree -- finding **N-222**'s shape, which this builder already carries
        # for the day.
        "anchor_balance": (
            None if statement is None else statement.anchor.balance
        ),
        "panel_id": panel,
    }


def prompt_fragment(
    account: Account, governing: "cash_ledger.AnchorPoint",
) -> str:
    """Return the out-of-band reconcile prompt, or ``""`` when there is none.

    Appended to a successful true-up's body so the modal lands in
    ``#modal-mount`` (``base.html``) whichever of the five surfaces opened the
    editor.  It is empty whenever the account has nothing outstanding.

    **It reconciles against the assertion its caller HOLDS, and reads none**
    (ruling **R-CC79**).  The true-up's write door reports the assertion that
    governs after its write, read under the owner's lock; this asked the ledger
    the same question again after the commit, outside the lock -- a second
    answer a concurrent save could make disagree with the first.  So it builds
    its :class:`~app.services.reconcile_service.Statement` from that record
    rather than through :func:`reconcile_statement`, which stays the one
    construction for the callers that hold no assertion.  A just-written
    account always has one, so there is no empty state to render here.

    **That used to be the steady state and since plan step X-f2-c2 it is not.**
    The sentence here read "the one-click true-up habit is not taxed by a
    prompt with nothing in it", and the widening falsified it: replayed over
    all 53 Checking assertion days on production, 46 would have carried at
    least one offer, because an envelope's close is offerable for the whole of
    its own period and nothing but closing it clears it.  The prompt is now the
    norm rather than the exception, which is a cost ruling **R-EE** did not
    price.  Finding **N-227** owns the bound that causes it.

    PUBLIC for the same reason :func:`panel_id` is: its one caller
    (``app.routes.accounts.anchor._true_up_success_response``) is in another
    module, so an underscore here would be finding **N-33**'s shape rather than
    a boundary.

    **It is the panel's THIRD door and it takes the same kind gate** (finding
    **N-216**), which a first version of that fix missed because the finding
    named the two ROUTES.  The anchor editor opens on every kind except an
    amortizing one, so a Property or 401(k) true-up reached here -- and once
    the two routes 404'd those kinds, the modal still rendered with its
    checkboxes while its submit button POSTed to a door that refused.  htmx
    swaps only 2xx, so the button did nothing, silently and forever: strictly
    worse than before the gate, and the exact failure
    ``app/error_handlers.py`` already refuses to ship for a mutating fragment.
    Gating here rather than widening the two routes is what makes
    ``_reconcile_modal.html``'s own promise -- *"dismissing it loses nothing:
    the same list is a permanent section on the account's detail page"* --
    TRUE, because that page 404s these kinds too.

    **The consequence is stated rather than discovered**: an entry recorded
    against a transaction on one of those accounts has no reconcile surface at
    all and its reservation releases only through ``entry_service.update_entry``.
    Measured on production 2026-08-10: all 82 entries, and all 59 outstanding
    ones, are on Checking, so the set this closes is empty today.

    Args:
        account: The just-asserted account.
        governing: The assertion that governs it after the write -- the write
            door's ``AnchorTrueUpReport.governing_after``.

    Returns:
        The rendered fragment, or ``""`` -- for an account with nothing to
        tick, and for one this panel does not serve.
    """
    if cash_detail_wrong_type(account):
        return ""
    # The assertion arrives from the caller; the CALENDAR does not.  This
    # fragment is appended to a true-up's response, whose write door holds no
    # pay calendar and whose other draws open read passes of their own, so
    # nothing on that path holds one to thread: this is where the one read of
    # it happens.
    context = reconcile_context(
        account, panel="reconcile-panel-modal",
        statement=reconcile_service.Statement(
            calendar_for(current_user.id), account.id, governing,
        ),
    )
    if context["outstanding"].is_empty:
        return ""
    return render_template("accounts/_reconcile_modal.html", **context)


@accounts_bp.route(
    "/accounts/<int:account_id>/reconcile", methods=["GET"],
)
@require_owner
def reconcile_panel(account_id):
    """HTMX partial: re-render the account's outstanding-purchase list.

    The ``balanceChanged`` refresh target on the cash detail page's section.
    A true-up moves the day the list is computed against, so a list left
    un-refreshed would offer purchases that are no longer outstanding -- the
    same reason the band above it re-fetches on the same event.

    **The kind gate is finding N-216's fix.**  This route guarded on ownership
    alone while the page it belongs to and its other three fragments all went
    through :func:`~app.routes.accounts._cash_page.load_cash_account_or_404`,
    so ``GET /accounts/<loan id>/reconcile`` rendered a cash-reconciliation
    panel for an amortizing account -- captioned "every purchase recorded on
    this account has been matched to your bank", which is not a sentence about
    a loan.  It answered with an EMPTY list only because a loan's
    ``account_anchor_history`` carries just its origination row, so the offer
    set's date bound admitted nothing: a property of the data, not of the
    route, and one plan step X-f2-c2 removes when the set widens to
    transactions.
    """
    account = load_cash_account_or_404(account_id)
    return render_template(
        "accounts/_reconcile_panel.html",
        **reconcile_context(
            account, panel=panel_id(account.id),
            statement=reconcile_statement(
                account, calendar_for(current_user.id),
            ),
        ),
    )


@accounts_bp.route(
    "/accounts/<int:account_id>/reconcile", methods=["POST"],
)
@require_owner
def record_reconciliation(account_id):
    """Record that the ticked rows had reached the bank.

    The reconcile step's write door (ruling R-DH (d) / the R-M re-ruling).
    Everything ticked is recorded as having moved by the civil day the
    account's latest balance was asserted for: a purchase's ``settled_on``
    becomes that day, and a transaction settles through the same service verb
    the grid's Mark Paid calls, stamped with it (ruling **R-FA**).  After it the
    projection stops holding those budgets back -- on a date the USER supplied
    rather than one the engine guessed.  A ticked transfer posts its own id
    under its own field (ruling **R-BAL145**, leaf ``balance:X-bi-6-4c-2``),
    both field names read off
    :class:`~app.services.reconcile_service.TickForm`.

    **The ORDER the two arms run in is NOT this module's** -- it is
    ``reconcile_service.record_reconciliation``'s, because it is a rule about
    the arms rather than about HTTP.  It lived here as two statements until an
    adversarial review pointed out that nothing could fail if a later edit
    swapped them.  What made it load-bearing was that the purchase arm's scope
    required a PROJECTED parent, which the transaction arm's writer destroyed;
    plan step X-au-c3 widened that scope, so the order is now a CONVENTION and
    that function states which it is in its own words rather than here in
    weaker ones.

    **It is its own request, and that is deliberate.**  Folding it into
    ``apply_anchor_true_up`` would put it inside the transaction that function
    ROLLS BACK when a submission changes nothing (ruling R-EQ) while reporting
    idempotent success -- so a re-assert of the governing balance would silently
    discard every reconciliation the user had just made while the UI said it
    saved.  A separate transaction cannot be swallowed by another one's
    rollback.  The mechanism was an ``IntegrityError`` handler around a unique
    index until plan step X-f1c4b; the hazard is the same and so is the ruling.

    A submitted value that does not name a row is dropped by
    :func:`~app.utils.digit_strings.parse_row_ids` before the service is
    reached, and every id that survives is re-scoped inside the arm that owns
    it, so a forged id matches nothing rather than raising -- the
    set-operation form of the project's "404 for both not-found and
    not-yours" rule.  What the arms actually changed comes back, so a
    submission that landed on nothing is SAID rather than rendered as success.

    **Both writers run inside ONE database transaction**, committed once here:
    a statement is one act, so four purchases and their envelope's close mean
    all five or none.

    Returns the refreshed panel plus ``HX-Trigger: balanceChanged`` so every
    surface showing a projection recomputes.  A ``ValidationError`` from the
    settle verb -- an illegal transition a stale panel can still submit -- is
    the designed 400 the app's error handlers already render; nothing is caught
    here, because a half-written reconciliation must not commit.

    **The kind gate is finding N-216's fix**, on the WRITE half; see
    :func:`reconcile_panel` for the measurement.  The two doors take the same
    gate because a gate one member of a family can be written without is a gate
    the next member will be written without too -- which is how this pair came
    to be the exception in the first place.
    """
    account = load_cash_account_or_404(account_id)
    # ONE statement for this request, resolved here and threaded through the
    # write and every render below it -- the discipline ``governing_statement``
    # states for the assertion (finding **N-222**), now carrying the pay
    # calendar too.  The writers date the rows they settle against it and the
    # re-render dates the rows it offers against it, so a second derivation
    # across the commit would be two answers to one question with a write
    # between them.  Nothing on this path creates or retires a payday, so the
    # one answer stays true for the whole request.
    statement = reconcile_statement(account, calendar_for(current_user.id))
    if statement is None:
        # No balance has ever been asserted for this account, so there is
        # nothing for an offer to be inside of.  Unreachable through the UI
        # (the panel renders no form in that state) and answered rather than
        # raised, because it is a legitimate empty state.
        return render_template(
            "accounts/_reconcile_panel.html",
            **reconcile_context(
                account, panel=panel_id(account.id), statement=None,
            ),
        )

    rows = reconcile_service.TickForm.ROW
    legs = reconcile_service.TickForm.LEG
    entry_ids = parse_row_ids(request.form.getlist("entry_ids"))
    transaction_ids = parse_row_ids(request.form.getlist(rows.ids_field))
    transfer_ids = parse_row_ids(request.form.getlist(legs.ids_field))
    try:
        recorded = reconcile_service.record_reconciliation(
            reconcile_service.ReconcileSubmission(
                statement=statement,
                entry_ids=entry_ids,
                transaction_ids=transaction_ids,
                corrections=_submitted_corrections(request.form, rows),
                transfer_ids=transfer_ids,
                transfer_corrections=_submitted_corrections(
                    request.form, legs,
                ),
                named=reconcile_service.NamedLines(
                    under_rows=_submitted_shown_lines(request.form),
                    shared=_submitted_shared_lines(request.form),
                ),
            ),
        )
        db.session.commit()
    except PageOutOfDate as exc:
        # A save that frees other bank lines than the panel named for the
        # rows ticked (ruling R-CC127: "on the reconcile panel, one
        # out-of-date row means nothing on it saves"; ruling R-CC135: compared
        # once, for the whole save, so a ticked row whose caption named a line
        # its save does not free refuses it too, ledger row BAL-597, and a
        # shared match's lines count only when all its rows are ticked,
        # ``NamedLines.for_ticks``).  The panel is drawn again from
        # current state below, so the refusal's facts are followed by the
        # panel's own remedy rather than the reload a plain surface needs --
        # the shape a popover's redraw has (ruling R-CC128).
        db.session.rollback()
        return _refusal(account, statement, f"{exc.facts} {_TICK_AGAIN}")
    except ValidationError as exc:
        db.session.rollback()
        return _refusal(account, statement, str(exc))
    except StaleDataError:
        db.session.rollback()
        return _refusal(account, statement, _STALE_MESSAGE)

    # A tick that did not all land is REPORTED rather than swallowed.  The
    # ordinary way to reach it is a second device settling the same rows while
    # a statement is being walked, and the two cases read differently: nothing
    # landed at all, or some of it did.  Answered as a 200 either way -- the
    # request itself succeeded and the refreshed list is the useful part; only
    # the silent reassurance would have been false.
    asked = len(entry_ids) + len(transaction_ids) + len(transfer_ids)
    notice = None
    if asked and not recorded:
        notice = _STALE_MESSAGE
    elif recorded < asked:
        notice = _PARTIAL_MESSAGE

    # The SAME assertion, not a second resolution (finding N-222).  Neither
    # writer touches ``account_anchor_history``, so re-reading it here would be
    # one request asking one question twice and trusting that the answers agree
    # -- and one of those answers is now the id every ticked row just recorded.
    return (
        render_template(
            "accounts/_reconcile_panel.html",
            error=notice,
            **reconcile_context(
                account, panel=panel_id(account.id), statement=statement,
            ),
        ),
        200,
        {"HX-Trigger": "balanceChanged"},
    )
