"""
Shekel Budget App -- Transaction Entry Routes

CRUD operations for individual purchase entries on entry-capable
transactions.  Returns HTMX fragments for inline management in
the transaction detail popover and the companion view.
"""

import logging
import re
from typing import Any, Literal, NamedTuple

from flask import Blueprint, render_template, request
from flask.typing import ResponseReturnValue
from flask_login import current_user
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm.exc import StaleDataError

from app.extensions import db
from app.models.transaction import Transaction
from app.models.transaction_entry import TransactionEntry
from app.routes._shown_lines import read_posted
from app.routes._typed_figure import typed_figure
from app.routes._render_helpers import (
    fragment_amounts,
    fragment_cash_flow,
    render_transaction_cell,
)
from app.schemas.validation import (
    EntryCreateSchema,
    EntryUpdateSchema,
    ShownLinesSchema,
)
from app.services import entry_credit_workflow, entry_service
from app.services.movement_account import purchase_choices
from app.services.match_press import NOTHING_SHOWN, OwnerOnly, Press, Shown, Silent
from app.services.pay_calendar import FiledRow, calendar_for
from app.services.settle_day import (
    recorded_settle_day,
    submitted_settle_day,
)
from app.exceptions import NotFoundError, PageOutOfDate, ValidationError
from app.utils.auth_helpers import (
    get_accessible_transaction,
    get_accessible_transaction_or_deleted,
)
from app.utils.dates import display_today
from app.utils.db_errors import is_unique_violation
from app.utils.digit_strings import integer_arg
from app.utils.error_fragments import (
    INVALID_REFERENCE_MSG,
    designed_error,
    flatten_schema_errors,
    refusal_for_a_gone_row,
)

logger = logging.getLogger(__name__)

entries_bp = Blueprint("entries", __name__)

# Marshmallow schema instances -- reused across requests.
_create_schema = EntryCreateSchema()
_update_schema = EntryUpdateSchema()
# The X's: a DELETE, so what its confirmation named rides the query string.
_shown_lines_schema = ShownLinesSchema()

# Name of the partial unique index that backstops the duplicate CC
# Payback bug closed in commit C-19.  ``entry_service.update_entry`` and
# ``delete_entry`` funnel through ``entry_credit_workflow.sync_entry_payback``
# (``create_entry`` did until plan task plan#2930), and the request's owner
# write lock (:mod:`app.db_transaction`, plan step ``balance:X-bn``, which
# deleted C-19's ``FOR NO KEY UPDATE`` on the parent row) serialises two
# of them; for a writer that holds no request, and so no owner lock, the
# partial index rejects the duplicate INSERT and the matching catch below
# converts the ``IntegrityError`` to idempotent success.  Mirrors the literal in
# the matching Alembic migration (b3d8f4a01c92) and the
# ``__table_args__`` declaration on
# ``app.models.transaction.Transaction``.
_CREDIT_PAYBACK_UNIQUE_INDEX = "uq_transactions_credit_payback_unique"

# Accepted shape of the ``host`` query param: a short lowercase id-prefix
# token.  Today the only non-empty value the templates emit is ``tp``
# (the inline mobile/companion card list); 16 chars is generous headroom
# for future prefixes while keeping free-form input out of DOM ids.
_HOST_TOKEN_RE = re.compile(r"[a-z0-9-]{1,16}")


def _request_host() -> str:
    """Read the validated ``host`` query param for this request.

    Every entries-CRUD control in ``grid/_transaction_entries.html``
    carries ``?host=<prefix>`` so the route's re-render reconstructs the
    same entry-list root id the request's ``hx-target`` named: the
    inline mobile/companion card list sends ``tp``, the desktop popover
    sends the empty default.  A value outside the short-token shape
    degrades to ``""`` (the popover surface) instead of echoing
    free-form input into a DOM id.

    Returns:
        The validated host prefix segment, possibly ``""``.
    """
    host = request.args.get("host", "")
    if host and _HOST_TOKEN_RE.fullmatch(host) is None:
        return ""
    return host


def _entry_list_host_id(txn_id: int, host: str) -> str:
    """Compose the entry-list root DOM id from a txn id and host prefix.

    Single Python-side source of truth that mirrors the Jinja default in
    ``grid/_transaction_entries.html`` (and the inline-card host set in
    ``_grid_row_macros.html``).  ``host=""`` yields the bare
    ``entry-list-<txn_id>`` (the full-edit popover's id); a non-empty
    host (e.g. ``"tp"``) yields ``entry-list-<host>-<txn_id>`` so the
    inline mobile-card list and the popover list never collide.  The
    route reconstructs the id this way so an entries-CRUD re-render
    lands on the same element the request's ``hx-target`` named.

    Args:
        txn_id: The parent transaction id.
        host: The host prefix segment (``""`` for the bare popover id).

    Returns:
        The DOM id string for the entry-list root element.
    """
    return "entry-list-" + (host + "-" if host else "") + str(txn_id)


class _Notice(NamedTuple):
    """The ONE banner a purchase list draws about the last submission.

    One typed value rather than a parameter per banner since plan step
    ``credit_card:CC-5-4a-5`` added the third, so no caller can ask for two.

    Attributes:
        name: The template variable it sets --
            ``conflict`` (the most recent edit was rejected by the
            optimistic-lock check, commit C-18; :func:`_stale_entry_response`),
            ``error`` (why the last submission was refused, the designed
            error-fragment path; :func:`_error_entry_response`), or
            ``page_refusal`` (:attr:`~app.exceptions.PageOutOfDate.facts`,
            drawn above the list as it is now; ruling **R-CC128**'s shape,
            :func:`_refused_entry_response`).
        value: ``True`` for a conflict; the sentence otherwise.
    """

    name: Literal["conflict", "error", "page_refusal"]
    value: "bool | str"


def _render_entry_list(
    txn: Transaction,
    editing_id: int | None = None,
    host: str = "",
    notice: "_Notice | None" = None,
) -> str:
    """Render the entry list partial for a transaction.

    Loads entries, computes remaining balance, and checks for
    out-of-period dates (OP-4 date awareness).

    Args:
        txn: The parent Transaction object.
        editing_id: Optional entry ID currently being edited.
            When set, the template shows an inline edit form
            for that entry instead of the display row.
        host: The validated ``host`` id-prefix for this request
            (:func:`_request_host`), threaded through so the re-rendered
            partial keeps the DOM id of the element HTMX is swapping
            (:func:`_entry_list_host_id`).  ``""`` for the bare list.
        notice: The ONE banner about the last submission (:class:`_Notice`),
            or ``None``.

    Returns:
        Rendered HTML string.
    """
    entries = entry_service.get_entries_for_transaction(
        txn.id, current_user.id,
    )
    # The WHOLE derived context, from the one producer that builds it
    # (:func:`app.services.entry_service.entry_list_view`), SPLATTED rather
    # than named key by key.  This route assembled its own until 2026-08-13,
    # and the grid macro assembled a different one that was missing the
    # posted-purchase indicator -- so every initial render showed already-posted
    # purchases as outstanding while the projection had released them.  A
    # caller that cannot name the keys cannot forget one.
    budgets = fragment_amounts(txn).budgets
    # The paycheck this row is FILED in, DERIVED (pay-calendar plan step
    # C4-a-3).  The producer read ``txn.pay_period`` for its span until then,
    # which is the stored ``end_date`` plan step C4-c dropped.
    #
    # The calendar belongs to the row's OWNER, spelled ``txn.user_id`` rather
    # than ``current_user.id``, because this fragment also serves the
    # COMPANION -- ``get_entries_for_transaction`` above validated the caller
    # against exactly that owner, so the two agree by construction and a
    # companion is not handed their own (empty) schedule.  It was
    # ``txn.pay_period.user_id`` until plan step ``pay_calendar:C13-b``: the
    # owner is a COLUMN on the row now, so asking the paycheck for it is a
    # second walk to one value.
    #
    # ``require_period`` and not ``period_by_id``: the id comes off a stored
    # row whose foreign key is NOT NULL and ``ON DELETE CASCADE``, so "this
    # owner's calendar does not hold it" is not a state a purchase list may
    # render past.
    #
    # **The READ ORDER: the row first, the paydays second, and no door that
    # deletes paydays can commit between them.**  Four routes reach here.
    # The GET (:func:`list_entries`) is a query, so one ``REPEATABLE READ``
    # snapshot holds both reads (``app.db_transaction``).  The three writes
    # draw the list in the command transaction their commit or rollback
    # opens, which takes the owner's write lock before its first read (plan
    # step ``balance:X-bn``); every door that deletes paydays, ``POST
    # /pay-periods/{reset,regenerate,truncate}`` among them, is a command too
    # and takes the same lock.  What is reachable is the gap BEFORE that
    # transaction: a door queued on the lock commits between the write's
    # commit or rollback and this redraw (finding **BAL-565**, ruled to ship
    # so, **R-BAL156**); plan step ``balance:X-dc`` makes each save one
    # transaction.
    #
    # *Until plan step ``balance:X-bn`` such a door could commit between the
    # row read and the payday read under ``READ COMMITTED`` (balance finding
    # **N-358**): the identity-mapped ``txn.pay_period`` still answered while
    # this calendar no longer held the id, and ``require_period`` raised.
    # This comment named that door rather than coping with it, for the reason
    # ``require_period``'s docstring gives.  A first draft had said the
    # calendar is read AFTER the row so no concurrent write can remove the
    # period it needs, which argued only about APPENDS (adversarial review,
    # 2026-08-31).*
    period = calendar_for(txn.user_id).require_period(
        FiledRow.for_row(txn),
    )
    # The accounts a new purchase may name (plan step credit_card:CC-5-2) and
    # the cards its CC tick offers (plan task plan#2930): the row's own plus
    # every account of the OWNER's that holds cash (ruling plan#3227), and the
    # owner's cards, by the row's owner for
    # the calendar's reason -- the purchase door gates against ``txn.user_id``
    # (ruling R-CC11), so a companion's refresh carries the owner's picker.
    # Through the fragment producer the mobile card and the cell share, so
    # one request's fragments resolve them by one rule.
    # What each purchase's X and CC un-tick would leave unexplained on the
    # statement screen, and whether the session's user may be shown it (plan
    # step ``credit_card:CC-5-4a-5``, rulings **R-CC80** / **R-CC132**), read
    # off the same row the list is: its purchases ARE ``entries``
    # (``get_entries_for_transaction`` returns ``txn.purchases``).
    view = entry_service.entry_list_view(
        entries, budgets[txn.id], period,
        purchase_choices(txn, fragment_cash_flow(txn.user_id).accounts),
        entry_service.purchase_controls([txn], current_user.id)[txn.id],
    )
    return render_template(
        "grid/_transaction_entries.html",
        txn=txn,
        # The add form's ``purchased_on`` default, and both date pickers'
        # ``max``.  It is the USER's today (``display_today``), not the
        # server's UTC one, because the service refuses a future purchase date
        # on that same clock (ruling R-M): with two clocks here, a UTC-running
        # process would stamp tomorrow's date for the evening hours the frames
        # disagree, and the app's own form would post a value its own guard
        # rejects.
        today=display_today().isoformat(),
        editing_id=editing_id,
        entry_list_host=host,
        entry_list_host_id=_entry_list_host_id(txn.id, host),
        **({notice.name: notice.value} if notice is not None else {}),
        **view,
    )


def _error_entry_response(
    txn: Transaction, message: str, host: str, status: int = 400,
) -> ResponseReturnValue:
    """Roll back and render the entry list as a designed error fragment.

    The rejected-mutation twin of :func:`_stale_entry_response` (the
    marker-header convention, closeout plan session 4): every
    entries-CRUD 400/422 a user can reach re-renders the requesting
    surface's entry list with current data plus a danger banner naming
    the rejection, and stamps the designed-fragment header so the body
    swaps instead of being silently dropped by the app-wide htmx
    config.  The rollback discards any changes the service staged
    before rejecting; SQLAlchemy re-loads the expired ``txn`` on
    attribute access, so the re-render reads committed state.

    Args:
        txn: The parent Transaction whose entry list is re-rendered.
        message: The user-facing rejection message for the banner.
        host: The validated host prefix from :func:`_request_host`, so
            the re-render reconstructs the requesting surface's id.
        status: The HTTP error status (400 domain rejection, 422
            validation failure, 404 for a submitted account that is not the
            row's owner's -- plan step ``credit_card:CC-5-2``).

    Returns:
        A designed-fragment Flask response tuple.
    """
    db.session.rollback()
    return designed_error(
        _render_entry_list(txn, host=host, notice=_Notice("error", message)),
        status,
    )


def _refused_entry_response(
    txn: Transaction, exc: ValidationError, host: str,
) -> ResponseReturnValue:
    """Roll back and answer a refused X or edit with the list as it is now.

    :func:`_error_entry_response` for every refusal but one: a press refused
    because its list named other bank lines than the press would free (ruling
    **R-CC127**) is drawn with :attr:`~app.exceptions.PageOutOfDate.facts`
    above the CURRENT list -- whose X and CC captions now name what is true
    -- so pressing again goes ahead, where the plain sentence's "Reload the
    page" would send the owner away from a list that is already current.
    **Except for a press no caption can name**: an edit re-pricing an old
    flagged line to a card total of exactly zero deletes a payback a match
    may name, the form named nothing, and the same refusal answers every try
    (finding **CC-381**, owned by plan step ``credit_card:CC-7``).

    Args:
        txn: The parent Transaction whose entry list is re-rendered.
        exc: What the service refused the press with.
        host: The validated host prefix from :func:`_request_host`.

    Returns:
        A designed-fragment Flask response tuple at 400.
    """
    if not isinstance(exc, PageOutOfDate):
        return _error_entry_response(txn, str(exc), host)
    db.session.rollback()
    return designed_error(
        _render_entry_list(
            txn, host=host, notice=_Notice("page_refusal", exc.facts),
        ),
        400,
    )


def _entry_shown(
    txn: Transaction, data: dict[str, Any], *, refusal: str,
) -> Shown | Silent:
    """Return what an X or an edit declares about the bank lines it frees.

    Plan step ``credit_card:CC-5-4a-5``.  The OWNER's list names what each
    press would free before it -- the X's confirmation, the edit form's CC
    caption (ruling **R-CC80**) -- and posts those lines back for the removal
    act to compare (ruling **R-CC127**); a request without the field named
    none.  A COMPANION's list names no bank
    line and never could (rulings **R-CC130** / **R-CC132**, developer
    2026-10-04, "Refuse, shown first"), so its press may free none, refused
    with *refusal* where it would.  Who pressed is the request's, so it is
    read here, as Mark Paid's door reads it
    (``routes.transactions._press._mark_paid_posted``); a companion's posted
    field is dropped, not read, for the reason that door gives.

    Args:
        txn: The purchase's row, on a request the door has already admitted.
        data: The schema-loaded payload; its ``shown_lines`` is taken out.
        refusal: The companion's sentence -- the X's names the PURCHASE
            (ruling **R-CC132**'s words); an edit's can free only the
            payback's line, so its is ``entry_credit_workflow.payback_refusal``.

    Returns:
        A :class:`~app.services.match_press.Shown` or
        :class:`~app.services.match_press.OwnerOnly`.
    """
    shown = read_posted(data, absent=NOTHING_SHOWN).shown
    if txn.user_id == current_user.id:
        return shown
    return OwnerOnly(refusal=refusal)


def _gone_entry_list_response(
    txn_id: int, host: str, message: str,
) -> ResponseReturnValue:
    """Render the purchase list of a row that is GONE: the red banner alone.

    Plan step ``credit_card:CC-5-4a-4``, ruling **R-CC103** (developer
    2026-09-23, "Banner only, both kinds"): *"For recurring and one-off rows
    alike, the list and its Add form are replaced by the red banner alone"*
    (``grid/_transaction_entries_gone.html``).  Keyed by the id in the URL,
    the only thing left of a one-off row its delete removed; the root keeps
    the list's id, so the request's outerHTML swap lands.  Designed and a
    404: the door answers the row "not found" (ruling **R-CC89**), and the
    body says why rather than being dropped.

    Args:
        txn_id: The row id the request named.
        host: The validated host prefix from :func:`_request_host`.
        message: The sentence -- the purchase refusal naming the row, or
            :data:`~app.utils.error_fragments.ROW_NO_LONGER_EXISTS_MSG`.

    Returns:
        A designed-fragment Flask response tuple at 404.
    """
    return designed_error(
        render_template(
            "grid/_transaction_entries_gone.html",
            entry_list_host_id=_entry_list_host_id(txn_id, host),
            error=message,
        ),
        404,
    )


def _entry_mutation_response(txn: Transaction, host: str) -> ResponseReturnValue:
    """Build the shared success response for an entries mutation.

    The refreshed entry list for the requesting surface plus, on the
    OWNER's desktop popover surface only, an out-of-band re-render of
    the parent transaction's grid cell.  An entry mutation changes the
    cell's "spent / budget" progress display, but the request's primary
    swap only replaces the entry list inside the popover -- without the
    OOB fragment the on-grid amount stays stale until an unrelated
    action re-renders the cell.

    Two gates on the fragment, both required:

    * ``host == ""`` -- the inline card surfaces (``"tp"``: mobile grid
      and companion page) have no ``#txn-cell-<id>`` element on the
      companion page, so an unconditional fragment would raise
      ``htmx:oobErrorNoTarget`` there.
    * the requester OWNS the transaction -- ``host`` is
      client-controlled, and these routes also admit companions (via
      :func:`get_accessible_transaction`).  The desktop grid cell is an
      owner-only surface whose markup includes ``txn.notes`` in
      aria-label/title; a companion stripping the ``host`` param must
      not receive it.

    The ``balanceChanged`` trigger drives the existing self-refreshes:
    the desktop tfoot balance row and subtotal tbodies, and (via the
    viewport-gated re-dispatch in ``mobile_grid.js``) the mobile This
    Period summary.

    Args:
        txn: The parent transaction, post-mutation and post-commit.
        host: The validated host prefix from :func:`_request_host`.

    Returns:
        Flask response tuple ``(html, 200, headers)``.
    """
    response = _render_entry_list(txn, host=host)
    is_owner = txn.user_id == current_user.id
    if host == "" and is_owner:
        response += render_transaction_cell(txn, wrap_div=True, wrap_oob=True)
    return response, 200, {"HX-Trigger": "balanceChanged"}


def _credit_payback_idempotent_response(
    exc: IntegrityError, txn_id: int, log_context: str, host: str,
) -> ResponseReturnValue:
    """Translate a credit-payback unique-index violation into a 200.

    Shared between :func:`create_entry`, :func:`update_entry`, and
    :func:`delete_entry`.  The update and delete routes funnel through
    ``entry_credit_workflow.sync_entry_payback`` (commit C-19); the create
    route has not since plan task plan#2930, so an ``IntegrityError`` there
    takes the other arm below, the invalid reference.  The
    request's owner write lock (plan step ``balance:X-bn``, which replaced
    C-19's lock on the parent row) prevents the duplicate-payback race in
    normal flow.  This helper is the backstop for a writer that holds no
    owner lock: the
    partial unique index ``uq_transactions_credit_payback_unique``
    rejects the duplicate INSERT, the calling route catches the
    resulting :class:`IntegrityError`, and this helper either returns
    the user the refreshed entry list at HTTP 200 (matching what a
    serialised request would have produced) or returns the standard
    400 response when the IntegrityError is for some other
    constraint we should not silently swallow.

    Args:
        exc: The caught :class:`IntegrityError`.
        txn_id: Parent transaction ID for re-fetching after rollback.
        log_context: Short human-readable string describing the route
            (e.g. ``"create_entry txn_id=42"``) for the structured
            log line emitted on the idempotent-success path.
        host: The validated host prefix from :func:`_request_host`,
            forwarded so the idempotent-success body matches the
            regular success response for the same surface.

    Returns:
        Flask response tuple suitable for direct return from the
        calling route.

    Side effects:
        Calls ``db.session.rollback()`` -- the caller has already
        seen the flush fail, so leaving pending changes in the
        session would block subsequent commits.
    """
    db.session.rollback()
    if not is_unique_violation(exc, _CREDIT_PAYBACK_UNIQUE_INDEX):
        rejected = get_accessible_transaction(txn_id)
        if rejected is None:
            return "Not found", 404
        return _error_entry_response(rejected, INVALID_REFERENCE_MSG, host)
    logger.info(
        "Duplicate CC payback prevented on %s (idempotent success)",
        log_context,
    )
    refreshed = get_accessible_transaction(txn_id)
    if refreshed is None:
        return "Not found", 404
    return _entry_mutation_response(refreshed, host)


def _stale_entry_response(
    txn: Transaction | None, host: str,
) -> ResponseReturnValue:
    """Roll back the session and render the entry list in conflict mode.

    Used by every entry-mutating PATCH/DELETE handler to convert a
    stale form or ``StaleDataError`` flush failure into a coherent
    UI response instead of a 500.  Re-fetches the entries so the
    user's view shows the winner's state -- never the loser's
    stale in-memory copies -- and tags the list with
    ``conflict=True`` so the template surfaces a warning banner.

    Args:
        txn: The parent Transaction whose entry list is being
            rendered.  The transaction itself is reloaded as well
            so any sibling-level changes (e.g. a concurrent paid
            event) are reflected.
        host: The validated host prefix from :func:`_request_host`,
            so the conflict re-render reconstructs the same
            entry-list root id the request's ``hx-target`` named.

    Returns:
        Flask response tuple ``(html, 409)``.
    """
    db.session.rollback()
    db.session.expire_all()
    fresh_txn = db.session.get(Transaction, txn.id) if txn is not None else None
    if fresh_txn is None:
        return "Not found", 404
    return _render_entry_list(
        fresh_txn, host=host, notice=_Notice("conflict", True),
    ), 409


def _accessible_txn_and_entry(
    txn_id: int, entry_id: int,
) -> tuple[Transaction, TransactionEntry] | None:
    """Resolve the parent transaction and its owned entry, or ``None``.

    The shared ownership preamble for the per-entry mutation routes
    (:func:`update_entry`, :func:`delete_entry`):
    the parent must be accessible to the requester
    (:func:`get_accessible_transaction` -- owner, or companion with
    visibility) AND the entry must belong to that parent.  The second
    check prevents parameter-confusion attacks where a visible
    transaction's URL is paired with another transaction's entry id.
    A single ``None`` covers every failure -- missing transaction,
    missing entry, or an entry on a different transaction -- so the
    caller's one guard clause returns the uniform 404 (the "404 for
    both not-found and not-yours" security response rule), and the
    security check has exactly one definition.

    Args:
        txn_id: Parent transaction id from the URL.
        entry_id: Entry id from the URL.

    Returns:
        ``(txn, entry)`` when both checks pass; ``None`` otherwise.
    """
    txn = get_accessible_transaction(txn_id)
    if txn is None:
        return None
    entry = db.session.get(TransactionEntry, entry_id)
    if entry is None or entry.transaction_id != txn.id:
        return None
    return txn, entry


@entries_bp.route("/transactions/<int:txn_id>/entries", methods=["GET"])
def list_entries(txn_id):
    """HTMX partial: return the entry list for a transaction.

    Accepts an optional ``editing`` query parameter (entry ID) to
    render an inline edit form for the specified entry.
    """
    txn = get_accessible_transaction(txn_id)
    if txn is None:
        return "Not found", 404
    editing_id = request.args.get("editing", type=integer_arg)
    return _render_entry_list(
        txn, editing_id=editing_id, host=_request_host(),
    )


@entries_bp.route("/transactions/<int:txn_id>/entries", methods=["POST"])
def create_entry(txn_id):
    """Create a new entry and return the updated entry list.

    Validates input via EntryCreateSchema, delegates to
    entry_service.create_entry (a ticked purchase goes on the owner's card,
    with no CC payback; a settled parent's purchases are closed, so no
    figure is re-derived), then commits atomically.
    Returns the refreshed entry list with a balanceChanged trigger
    (plus, on the desktop popover surface, the OOB grid-cell
    re-render -- see :func:`_entry_mutation_response`).

    **A row that is gone gets the banner alone** (plan step
    ``credit_card:CC-5-4a-4``, rulings **R-CC101**, **R-CC103**,
    **R-CC104**): a purchase on a row deleted in another tab answers
    "Groceries was deleted: a purchase cannot be recorded under it.  Reload
    the page." where the list stood -- "Groceries was archived: ..." where
    its recurring item is (ruling **R-CC107**) --
    :func:`_gone_entry_list_response`, before the door serves the row.  That
    answers a Delete that committed BEFORE the request.  Since plan step
    ``balance:X-bn`` (ruling **R-CC106**) the request's transaction takes its
    owner's write lock before the door reads the row, so a Delete either
    committed before that read or waits for THAT TRANSACTION to end -- which
    is not the request's end: the success path commits and then draws the
    list, and a refusal rolls back and then redraws it, each in a second
    transaction, and a queued Delete commits in between (finding BAL-565).
    A one-off row then answers a server error, and a recurring row's answer,
    a refusal or a success, draws the list and its Add form under the deleted
    row.  The answer for
    that moment (the race ruling **R-CC96** let a door see) was deleted with
    the row lock at that step and stays deleted (**R-BAL156**); plan step
    ``balance:X-dc`` makes each save one transaction.
    """
    host = _request_host()
    answer = get_accessible_transaction_or_deleted(txn_id)
    if not isinstance(answer, Transaction):
        return _gone_entry_list_response(
            txn_id, host,
            refusal_for_a_gone_row(
                answer, entry_service.deleted_row_purchase_refusal,
            ),
        )
    txn = answer

    errors = _create_schema.validate(request.form)
    if errors:
        return _error_entry_response(
            txn, flatten_schema_errors(errors), host, status=422,
        )

    data = _create_schema.load(request.form)
    # **The sign is COMPOSED, never typed** (developer ruling
    # **bank_import:R-IK**, 2026-09-01, plan
    # step ``bank_import:X-gj-2b-3``).  The form posts a MAGNITUDE and a
    # direction; ``purchase_amount`` is the one place the pair becomes a stored
    # figure, the signed value the column holds.
    #
    # **The figure and WHO WROTE it travel as one value** (plan step
    # X-bi-3e-1, ruling R-BAL61): this is the add-purchase form, so a PERSON
    # wrote it, and the route says so with the figure rather than leaving the
    # door to read it off a day.  The key is REPLACED, not overwritten, for
    # the reason ``_pair_the_posting_day`` gives for the day: the service
    # takes ``figure`` and would silently ignore an ``amount`` kwarg.
    data["figure"] = typed_figure(
        entry_service.purchase_amount(
            data.pop("amount"),
            records_a_refund=data.pop("direction") == entry_service.REFUND,
        ),
    )
    # **No press**: an add frees no bank line since plan task plan#2930.  It
    # freed one only by deleting a CC payback (a card refund bringing the card
    # total to zero), and a purchase the door writes carries no CC flag, so
    # its tick files it on the card and touches no payback.
    try:
        entry_service.create_entry(
            transaction_id=txn.id,
            user_id=current_user.id,
            details=entry_service.EntryDetails(**data),
        )
        db.session.commit()
    except IntegrityError as exc:
        # A refused reference is the shared helper's 400 arm; its payback
        # arm (commit C-19) cannot fire here since plan task plan#2930, the add
        # door syncing no payback.
        return _credit_payback_idempotent_response(
            exc, txn.id, f"create_entry txn_id={txn.id}", host,
        )
    except NotFoundError as exc:
        # A submitted ``account_id`` that is not the ROW's owner's -- or does
        # not exist -- is the door's 404 (plan step ``credit_card:CC-5-2``;
        # the security response rule: one answer for "not found" and "not
        # yours").  Rendered as the same designed fragment a domain refusal
        # is, so the surface reads the reason, at the status the rule names.
        # Through CC-5-1 no ``NotFoundError`` could reach this arm at all:
        # the row is resolved above, so every 404 the door could raise was
        # already answered, and the 400 this shared with ``ValidationError``
        # was never exercised.
        return _error_entry_response(txn, str(exc), host, status=404)
    except ValidationError as exc:
        return _error_entry_response(txn, str(exc), host)

    return _entry_mutation_response(txn, host)


def _execute_entry_update(
    entry_id: int, txn: Transaction, data: dict[str, Any], host: str,
    shown: Shown | Silent,
) -> ResponseReturnValue:
    """Run the entry update + commit, translating service outcomes to HTTP.

    ``StaleDataError`` at flush -> 409 conflict entry list; the C-19
    ``IntegrityError`` backstop -> the idempotent credit-payback response;
    ``NotFoundError`` / ``ValidationError`` -> 400, a list out of date drawn
    as it is now (:func:`_refused_entry_response`).  On success, the
    shared mutation response (refreshed entry list + OOB cell on the
    popover surface + ``balanceChanged``).  Extracted so
    ``update_entry`` keeps only its ownership guards + form validation;
    this owns the service-call/commit/error-translation tail (the
    ``transfers._execute_transfer_update`` precedent).  ``host`` is the
    validated surface prefix from :func:`_request_host`; ``shown`` is what
    the edit form named (:func:`_entry_shown`).
    """
    try:
        with Press(shown) as one_save:
            entry_service.update_entry(
                entry_id, current_user.id, press=one_save, **data,
            )
        db.session.commit()
    except StaleDataError:
        logger.info(
            "Stale-data conflict on update_entry id=%d", entry_id,
        )
        return _stale_entry_response(txn, host)
    except IntegrityError as exc:
        # Defensive backstop for commit C-19 -- see
        # ``_credit_payback_idempotent_response`` docstring.
        return _credit_payback_idempotent_response(
            exc, txn.id, f"update_entry id={entry_id}", host,
        )
    except NotFoundError as exc:
        return _error_entry_response(txn, str(exc), host, status=404)
    except ValidationError as exc:
        return _refused_entry_response(txn, exc, host)

    return _entry_mutation_response(txn, host)


@entries_bp.route(
    "/transactions/<int:txn_id>/entries/<int:entry_id>",
    methods=["PATCH"],
)
def update_entry(txn_id, entry_id):
    """Update an entry and return the updated entry list.

    Verifies the entry belongs to the specified transaction before
    calling the service, preventing parameter confusion attacks
    where a companion could modify entries on non-visible
    transactions by using a visible transaction's URL with a
    different entry ID.

    Optimistic locking (commit C-18 / F-010): the inline edit form
    ships ``version_id`` as a hidden input.  When the submitted
    value differs from the entry's current counter, the handler
    short-circuits with a 409 + entry list refreshed in conflict
    mode so the user sees the winner's values.  ``StaleDataError``
    raised at flush time is caught and produces the same response.
    """
    target = _accessible_txn_and_entry(txn_id, entry_id)
    if target is None:
        return "Not found", 404
    txn, entry = target
    host = _request_host()

    errors = _update_schema.validate(request.form)
    if errors:
        return _error_entry_response(
            txn, flatten_schema_errors(errors), host, status=422,
        )

    data = _update_schema.load(request.form)
    # What the form's CC caption named, taken out of the fields before they
    # reach the door (plan step ``credit_card:CC-5-4a-5``).
    shown = _entry_shown(
        txn, data, refusal=entry_credit_workflow.payback_refusal(txn),
    )

    # Stale-form check (commit C-18 / F-010).
    submitted_version = data.pop("version_id", None)
    if submitted_version is not None and submitted_version != entry.version_id:
        logger.info(
            "Stale-form conflict on update_entry id=%d "
            "(submitted=%d, current=%d)",
            entry_id, submitted_version, entry.version_id,
        )
        return _stale_entry_response(txn, host)

    refusal = _compose_the_amount(data)
    if refusal is not None:
        return _error_entry_response(txn, refusal, host, status=422)

    _pair_the_posting_day(data, entry)
    return _execute_entry_update(entry_id, txn, data, host, shown)


def _compose_the_amount(data: dict[str, Any]) -> "str | None":
    """Turn a submitted magnitude and direction into the stored figure.

    :func:`_pair_the_posting_day`'s sibling, and it exists for the same reason:
    the service takes ONE value where the form posts TWO controls, and the
    route is where a submission becomes what the door accepts.

    **The pair travels together -- both, or neither** (developer ruling
    2026-09-01, plan step ``bank_import:X-gj-2b-3``).  The edit form renders
    the amount box and the Charge/Refund control side by side and submits every
    control it holds, so a browser sends both or (on a settled parent, where
    neither is rendered) sends neither.  A submission carrying one without the
    other is hand-made or stale, and it cannot be composed: an amount with no
    direction has no sign, and guessing one either way writes money the owner
    did not state.  It is REFUSED rather than defaulted, which is the same
    choice ``_reject_incomplete_new_envelope`` makes about a new envelope
    stated by halves.

    ``direction`` is removed either way -- the service's ``_UPDATABLE_FIELDS``
    names the columns an entry has, and direction is not one of them: it is
    how a human says what the sign should be.

    Args:
        data: The loaded update payload, MUTATED in place.

    Returns:
        ``None`` when the payload is coherent, else the sentence to refuse it
        with.
    """
    has_amount = "amount" in data
    has_direction = "direction" in data
    if has_amount != has_direction:
        data.pop("direction", None)
        return (
            "An amount and whether it is a charge or a refund are one answer, "
            "and this submission carried only one of them. Reopen the purchase "
            "and save it again."
        )
    if has_amount:
        # A PERSON typed it -- this is the entry PATCH -- and the figure says
        # so (plan step X-bi-3e-1, ruling R-BAL61); ``figure`` is the key the
        # door reads, as ``settle_day`` is for the day.
        data["figure"] = typed_figure(
            entry_service.purchase_amount(
                data.pop("amount"),
                records_a_refund=data.pop("direction") == entry_service.REFUND,
            ),
        )
    return None


def _pair_the_posting_day(
    data: dict[str, Any], entry: TransactionEntry,
) -> None:
    """Replace a submitted ``settled_on`` with the PAIR the service takes.

    Plan step **X-az**.  ``entry_service.update_entry`` writes the posting day
    and the basis that says how that day is known as one
    :class:`app.services.settle_day.SettleDay`, so a bare date is not something
    it can accept: the two columns are welded by
    ``ck_transaction_entries_settle_day_basis_pairing`` and there is one writer
    for the pair.

    **It is ECHO-AWARE, and that is not a nicety -- it is what stops this door
    re-opening the defect the step closes.**  The purchase popover renders the
    Posted date input on EVERY entry, prefilled with the stored day and OUTSIDE
    the settled-parent guard (``_transaction_entries.html``), so on a closed
    envelope it is the only editable control there is: an owner who opens the
    row and saves re-submits the day it already carried.  Wrapping that
    unconditionally as ``entered`` rewrote the reconcile panel's ``asserted``
    upper BOUND as the owner's own typing -- with the day unchanged, so the
    clearing link survived and nothing signalled it -- and
    ``CandidateRow.expected_window`` then collapsed the purchase to a point at
    the assertion day, out of reach of its own bank line.  Measured on
    production 2026-08-22: **59 of 66 linked purchases, ``$4,173.07``**, one
    innocuous save each.  The rule itself is
    :func:`app.services.settle_day.submitted_settle_day`, shared with the two
    status doors so all three read a re-submitted day the same way.

    **A day that MOVED is ``entered``, and this is the only door that can say
    so.**  It is the owner's own record -- no bank statement showed it and no
    balance assertion bounds it.  The other two kinds are written elsewhere and
    neither can reach this handler: the statement matcher passes ``observed``
    through the same service door, and the reconcile panel's ``asserted`` days
    go through its own bulk ``UPDATE``.

    An explicit ``None`` is a real user act -- *"I ticked this as posted and the
    statement does not actually show it"* -- and stays a ``None``, which clears
    both columns.  **An EMPTY date input IS that ``None``**: the schema declares
    ``settled_on`` ``allow_none``, and ``_normalize_empty_inputs`` maps ``""`` to
    ``None`` for exactly such a field rather than dropping the key, which is what
    ``EntryUpdateSchema``'s own comment says the flag is for.  It reaches here as
    a present key carrying ``None``, and the echo rule is skipped for it because
    an emptied box asserts a CHANGE rather than re-stating a day.

    Args:
        data: The schema-loaded PATCH payload, mutated in place.
        entry: The purchase being PATCHed, for the pair it ALREADY records --
            which is what the echo rule compares against.
    """
    if "settled_on" not in data:
        return
    submitted = data.pop("settled_on")
    data["settle_day"] = (
        None if submitted is None
        else submitted_settle_day(submitted, recorded_settle_day(entry))
    )


@entries_bp.route(
    "/transactions/<int:txn_id>/entries/<int:entry_id>",
    methods=["DELETE"],
)
def delete_entry(txn_id, entry_id):
    """Delete an entry and return the updated entry list.

    Same parameter confusion guard as update_entry: verifies the
    entry belongs to the specified transaction.

    Optimistic locking: no form-side ``version_id`` is shipped with the
    delete button; the SQLAlchemy ``version_id_col`` lock catches
    concurrent races at flush time and the handler converts
    ``StaleDataError`` into a 409 + conflict entry list.

    **A refused removal is the list's banner** (finding **CC-376**, plan step
    ``credit_card:CC-5-4a-4``): the door's ``ValidationError`` had no arm
    here, so each was a 500 (measured by the step's lane probe on a settled
    envelope holding one purchase).  The arm answers every refusal the door
    raises, as the purchase list's other refusals are answered, where the
    delete button stood: a purchase under a settled row whose close records a
    fixed figure (``entry_service._refusals.removal_refusal``); a card
    purchase whose payback has already settled, or whose removal would leave
    the row's card refunds larger than its card purchases
    (``entry_credit_workflow.sync_entry_payback``) -- the first reached in an
    ordinary flow, paying the card and then removing a card purchase; and a
    row's own payment record, reached only by a crafted id
    (``_refusals._reject_settlement_record``).  Each names its row and prints
    its dollars (ruling **R-CC98**; review 6, M3).

    **The X names what it frees first, and sends it back** (plan step
    ``credit_card:CC-5-4a-5``, rulings **R-CC80** / **R-CC127**): the
    owner's confirmation names the bank lines the press would leave
    unexplained -- the purchase's own, and its payback's where the press
    deletes that -- and posts them as the DELETE's query string; a press
    whose list is out of date is refused and the list drawn as it is now
    (:func:`_refused_entry_response`).  A companion's X is withheld over a
    purchase whose own line it would free, and a companion's press that
    would free one anyway is refused (ruling **R-CC132**,
    :func:`_entry_shown`).
    """
    target = _accessible_txn_and_entry(txn_id, entry_id)
    if target is None:
        return "Not found", 404
    txn, entry = target
    host = _request_host()
    errors = _shown_lines_schema.validate(request.args)
    if errors:
        return _error_entry_response(
            txn, flatten_schema_errors(errors), host, status=422,
        )
    shown = _entry_shown(
        txn, _shown_lines_schema.load(request.args),
        refusal=(
            f"{entry.description} is matched to a line on the bank "
            "statement, so only the account owner can delete it."
        ),
    )
    return _execute_entry_delete(entry_id, txn, host, shown)


def _execute_entry_delete(
    entry_id: int, txn: Transaction, host: str, shown: Shown | Silent,
) -> ResponseReturnValue:
    """Run the entry delete + commit, translating service outcomes to HTTP.

    :func:`_execute_entry_update`'s sibling, split from :func:`delete_entry`
    at plan step ``credit_card:CC-5-4a-5`` -- whose posted-lines read gave
    that handler one return past pylint's ceiling -- on the same precedent:
    the handler keeps its ownership guard and form validation, this owns the
    service call, the commit and the error translation.  ``shown`` is what
    the X's confirmation named (:func:`_entry_shown`).
    """
    try:
        with Press(shown) as one_save:
            entry_service.delete_entry(entry_id, current_user.id, press=one_save)
        db.session.commit()
    except StaleDataError:
        logger.info(
            "Stale-data conflict on delete_entry id=%d", entry_id,
        )
        return _stale_entry_response(txn, host)
    except IntegrityError as exc:
        # Defensive backstop for commit C-19 -- ``delete_entry``
        # also calls ``sync_entry_payback``, so a writer that holds no
        # owner lock could race it the same way.  See
        # ``_credit_payback_idempotent_response`` docstring.
        return _credit_payback_idempotent_response(
            exc, txn.id, f"delete_entry id={entry_id}", host,
        )
    except NotFoundError as exc:
        db.session.rollback()
        return str(exc), 404
    except ValidationError as exc:
        return _refused_entry_response(txn, exc, host)

    return _entry_mutation_response(txn, host)
