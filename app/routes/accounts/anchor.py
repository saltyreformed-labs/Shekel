"""
Shekel Budget App -- Anchor Balance Edit Routes

The grid and Net Worth Cockpit anchor-balance true-up endpoints,
split out of the historical monolithic ``app/routes/accounts.py`` in
Commit 21 of the financial-calculation audit follow-up (F-1).  The
retired ``/accounts`` table's inline balance editor also lived here
until the Net Worth Cockpit replaced that table; the cockpit reuses
the grid editor below, so only that family remains.

``true_up`` routes the actual mutation, history-row append,
conditional entries reconcile, and commit through
:func:`app.services.anchor_service.apply_anchor_true_up`, so the duplicate rule
(ruling **R-EQ**: an assertion is refused only when it changes nothing) and the
concurrency contract live in exactly one place.  This module is
therefore deliberately thin: it owns the HTTP-shaped concerns (form
validation, HTMX-fragment rendering, HX-Trigger header composition)
and delegates the database mutation to the shared service.

**The C-17 / F-009 optimistic lock is no longer part of that contract**
(ruling R-EN, plan step X-f1c3c): a true-up UPDATEs no column on
``accounts``, so ``version_id`` cannot fire, and this module no longer
carries a pre-flush version check or a 409.  What serialises two
concurrent true-ups is the per-owner write lock
(:mod:`app.services.user_write_lock`), which the PATCH's transaction takes
before the route runs (:mod:`app.db_transaction`); *until plan step
``balance:X-bn`` the reconcile, the read-modify-write, took it itself.*

The editor opens from five surfaces -- the grid cell, the dashboard
balance card, the cockpit per-card cell, the investment / retirement
detail page's balance hero, and the cash detail page's balance hero --
each threaded through as a normalized ``revert`` token so Cancel /
Escape AND a save re-render the correct opener, through the one table
:data:`_SURFACES` (rulings R-CC74 / R-CC77), and a save draws from what the
write door reports rather than re-reading the ledger (ruling R-CC79).
"""

import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from flask import render_template, request, url_for
from flask.typing import ResponseReturnValue
from flask_login import current_user

from app.exceptions import ValidationError
from app.extensions import db
from app.models.account import Account
from app.routes.accounts._bp import accounts_bp
from app.routes.accounts._door_meaning import (
    LOAN_ANCHOR_REFUSAL,
    door_meaning_refusal,
)
from app.routes.accounts.detail import render_cash_balance_hero
from app.routes.accounts.reconcile import prompt_fragment
from app.routes.dashboard import render_balance_section
from app.routes.investment import render_balance_hero
from app.routes.savings import render_cockpit_balance
from app.services import (
    anchor_service,
    cash_ledger,
    liability_sign,
    pay_period_service,
)
from app.services.account_projection import (
    AccountProjectionKind,
    classify_account,
)
from app.services.anchor_service import (
    AnchorTrueUpOutcome,
    AnchorTrueUpReport,
)
from app.utils.account_validation import _anchor_schema
from app.utils.auth_helpers import get_or_404, require_owner
from app.utils.dates import display_today
from app.utils.error_fragments import designed_error, flatten_schema_errors

logger = logging.getLogger(__name__)


# ── Anchor Balance True-up (Grid) ─────────────────────────────────


def _cash_cell(account: Account, governing: cash_ledger.AnchorPoint) -> str:
    """Draw the GRID's anchor balance cell for a cash account -- the grid's draw.

    The ONE function behind :func:`anchor_display` (the grid's Cancel /
    Escape, for every kind but a loan) and a save opened from the grid,
    reached through :data:`_SURFACES` (ruling R-CC77).  The cell keeps the sign
    of the surface it sits on -- a card's grid shows the held balance its rows
    are summed in (ruling R-CC57) -- so it is NOT crossed; only the editor it
    opens speaks owed.

    **It reads nothing: the governing assertion is its input** (ruling
    **R-CC79**) -- a save's from its write door's report, Cancel's read by
    :func:`anchor_display`, which also holds the only loan check on the
    grid's Cancel path.

    Args:
        account: The owned, attached, non-amortizing :class:`Account`.
        governing: The assertion that governs the account today.

    Returns:
        The rendered display cell.
    """
    return render_template(
        "grid/_anchor_edit.html",
        account=account,
        anchor_balance=governing.balance,
        editing=False,
    )


def _dashboard_hero(
    _account: Account, _governing: cash_ledger.AnchorPoint,
) -> str:
    """Give the dashboard's draw the table's shape; neither input is read.

    :func:`app.routes.dashboard.render_balance_section` takes no account
    because the hero shows the account the DASHBOARD resolves -- the one whose
    editor it opened, and the one its Cancel redraws, so a save and a Cancel
    still answer alike if that resolution moved while the editor was open.
    The saved account it is handed goes unread for that reason.

    Returns:
        The rendered ``#balance-display`` fragment.
    """
    return render_balance_section()


# The other three screens' draws take the account (the cockpit's and the
# investment page's by its id, as their Cancel GETs do) and fold what they
# show from a read pass of their own, so the governing assertion goes unread.
def _cockpit_card(account: Account, _governing: cash_ledger.AnchorPoint) -> str | None:
    """Give :func:`app.routes.savings.render_cockpit_balance` the table's shape."""
    return render_cockpit_balance(account.id)


def _investment_hero(account: Account, _governing: cash_ledger.AnchorPoint) -> str | None:
    """Give :func:`app.routes.investment.render_balance_hero` the table's shape."""
    return render_balance_hero(account.id)


def _cash_hero(account: Account, _governing: cash_ledger.AnchorPoint) -> str | None:
    """Give :func:`app.routes.accounts.detail.render_cash_balance_hero` the table's shape."""
    return render_cash_balance_hero(account)


@dataclass(frozen=True)
class _Surface:
    """One screen the anchor editor opens from: where Cancel goes, and its draw.

    **ONE table, so "what this screen shows" has one home** (rulings R-CC74 /
    R-CC77, finding CC-365).  Cancel / Escape GETs :attr:`endpoint`, whose
    view calls the screen's draw; a save opened from the same screen calls
    that same draw through :attr:`draw`.  Until then every save answered with
    the grid's cell -- a card owing $1,200.00 saved from /savings showed
    ``-$1,200.00`` for a round trip, because the cockpit tile shows what a
    debt OWES (plan step credit_card:CC-5-5c) and the grid cell the held
    balance -- and the screen list lived twice, as an allowlist and an
    if-chain.

    Attributes:
        endpoint: The GET that re-renders this screen's cell on Cancel.
        takes_account_id: Whether *endpoint* names the account; the dashboard
            hero names none, because it shows the account the dashboard
            resolves.
        draw: The screen's draw: the saved account and the assertion that
            governs it after the write (the write door's report, ruling
            **R-CC79**) in, the rendered cell out -- or ``None`` when the
            screen no longer shows that account (archived, or no longer its
            kind), which a save, whose write has committed, answers with an
            empty cell.  The grid's reads nothing; the other four adapt a
            route module's draw that builds its own read pass as its GET does,
            so the save runs it after the write and builds none for it.
    """

    endpoint: str
    takes_account_id: bool
    draw: Callable[[Account, cash_ledger.AnchorPoint], str | None]


#: The five screens, keyed by the normalized ``revert`` token; ``None`` is the
#: grid, which sends none.  ``dashboard`` is the dashboard hero (restoring the
#: account name, caption and runway the grid cell lacks -- the audit's
#: cancel-path stranding fix); ``accounts`` the Net Worth Cockpit's per-card
#: cell, account-scoped because the cockpit is multi-card; ``investment`` the
#: investment / retirement detail hero, the model-from-anchor balance its
#: headline shows (Loop B P1 C4); ``cash`` the cash detail hero, the
#: current-period balance its headline shows (the S8 / D14 port).
_SURFACES: dict[str | None, _Surface] = {
    None: _Surface("accounts.anchor_display", True, _cash_cell),
    "dashboard": _Surface("dashboard.balance_section", False, _dashboard_hero),
    "accounts": _Surface("savings.cockpit_balance", True, _cockpit_card),
    "investment": _Surface("investment.balance_hero", True, _investment_hero),
    "cash": _Surface("accounts.cash_balance_hero", True, _cash_hero),
}


def _normalize_revert_context(raw_revert: str | None) -> str | None:
    """Allowlist-validate the raw ``revert`` token to a canonical value.

    The anchor editor is opened from more than one surface, and the opener
    names its surface via the ``revert`` query token.  The recognized values
    are :data:`_SURFACES`' keys; every other value (unset, unknown, an
    attacker's probe) collapses to ``None``, the grid.  Validating here means
    the token is checked against the table in exactly one place -- the Cancel
    / Escape target (:func:`_anchor_revert_url`), the save's answer
    (:func:`_true_up_success_response`) and the edit form's ``hx-patch``
    round-trip token all consume this normalized value -- so the token is
    never interpolated unvalidated into a URL or template.  A fourth consumer,
    the 409 conflict cell's retry opener, left with ruling R-EN (plan step
    X-f1c3c).

    Args:
        raw_revert: The ``revert`` query token as received, or ``None``.

    Returns:
        The canonical surface name when the token names a recognized
        surface; otherwise ``None`` (the grid default).
    """
    return raw_revert if raw_revert in _SURFACES else None


def _submission_is_the_coverage_boundary(
    boundary_day: date | None, submitted_day: date | None,
) -> bool:
    """Return True when this submission's assertion IS the account's boundary.

    **The reconcile prompt follows the COVERAGE BOUNDARY, not the click**
    (developer ruling 2026-08-04, plan step X-f1c4c).  Before this step every
    cash true-up stamped today, so "the day this submission asserts" and
    ``cash_ledger.reconciled_through`` -- ``MAX(observed_on)`` -- were the same
    value by construction and nothing had to say so.  A user-supplied day
    decouples them for the first time, and the prompt is keyed on the second.

    What that costs when nobody re-couples them, reproduced end to end: assert
    ``$2,500`` as of Jul 15 while the account's latest assertion is Aug 4, and
    the modal opens headed with the AUG 4 balance offering an Aug 2 purchase --
    which a Jul 15 statement cannot show.  Ticking it is a settlement the user
    has no evidence for, and it moves that debit out of its envelope's
    outstanding floor (``cash_ledger._amounts._entry_checking_impact``:
    ``max(500 - 0, 120) = 500`` becomes ``max(500 - 120, 0) = 380``), so the
    projected balance reads ``$120.00`` HIGH on money that never left the bank.

    **A ``None`` day is ALWAYS the boundary, and that is exact rather than a
    convenience.**  The service files ``display_today()`` for it, and every
    other assertion carries an ``observed_on`` at or before its own today
    (:func:`app.services.anchor_service.resolve_observation_day` refuses a
    future day), so today is ``>=`` every stored day and is therefore the new
    maximum.  For a supplied day *D* the new maximum is ``max(previous, D)``,
    so *D* is the boundary exactly when it equals it.  **The two branches are
    established differently and saying so matters**: the supplied-day branch
    COMPARES against the boundary, while the ``None`` branch is PROVED equal to
    it by the no-future-day rule and reads nothing.  An earlier version of this
    paragraph claimed both were read off one query, which is the over-stated
    shape this step corrected in three other docstrings.

    Asking here rather than sharpening the PANEL is the shape ruling R-EB and
    plan step X-f6 point at: the panel exists because nothing in the app records
    when money moved, and a bank import replaces the question rather than
    re-keying it.  Bounding the offer set by a historical statement day would
    manufacture hand-entered ``settled_on`` values for X-f6's matcher to
    arbitrate against the bank's own -- one question with two answers, which is
    what this arc exists to delete.

    Args:
        boundary_day: The account's coverage boundary AFTER the write --
            ``MAX(observed_on)`` over its owner-declared assertions, which is
            the day of the one that governs: the write door's
            ``governing_after``, read under the owner's lock (ruling R-CC79),
            where ``cash_ledger.reconciled_through`` asked the same MAX after
            the commit, outside it.  Passed in rather than queried so this is
            a pure comparison, gradable without a database.
        submitted_day: The civil day the FORM submitted, or ``None`` when its
            date box was blank.  The submitted value, deliberately, not a
            re-resolved one: re-reading the clock here would be a second
            reading that a midnight tick could disagree with.

    Returns:
        ``True`` when the assertion this request filed is the account's
        coverage boundary, so the reconcile question is about IT.
    """
    if submitted_day is None:
        return True
    return submitted_day == boundary_day


@dataclass(frozen=True)
class _AnchorSubmission:
    """One validated balance assertion, as the true-up form submitted it.

    Two values that are ONE fact -- "this account held $B on day D" -- so the
    gate hands them back together rather than as a widening tuple, and so the
    success response can acknowledge BOTH halves of what was recorded rather
    than being handed the day alone.  Frozen: a submission is a record of what
    arrived, not a working value.

    Attributes:
        balance: The validated :class:`Decimal` balance being asserted, HELD --
            the figure typed, crossed ONCE by
            :func:`app.services.liability_sign.held_balance` (plan step
            credit_card:CC-5-5b, rulings R-CC52 / R-CC57): a liability's box
            asks for the amount owed, so ``1,200.00`` typed on a card is
            ``-1,200.00`` here, and an asset's figure is here as typed.  Held
            because the write door stores held and the governing comparison
            below compares held; the acknowledgement crosses it back.
        observed_on: The civil day the form submitted, or ``None`` when its date
            box was left blank -- which the write door reads as the user's today
            (:func:`app.services.anchor_service.resolve_observation_day`).  It is
            NOT defaulted here: a route that invented the day would be a second
            answer to "when is an assertion dated", and both anchor write doors
            already share one.
    """

    balance: Decimal
    observed_on: date | None


def _submitted_or_resolved_day(
    submission: "_AnchorSubmission", anchor: "cash_ledger.AnchorPoint",
) -> date:
    """Return the civil day this submission asserted, blank date box included.

    The acknowledgement names a day, and a blank date box carries none: the
    write door resolves that to the user's today
    (:func:`app.services.anchor_service.resolve_observation_day`), and this
    route may not resolve it a SECOND time -- re-reading the clock here is a
    second answer to "when is an assertion dated" that a midnight tick can make
    disagree with the one that was stored.

    So the blank case reads the day back off the assertion that GOVERNS after
    the write, and that is exact rather than approximate.  A blank submission
    is dated today, today is at or after every stored day (the write door
    refuses a future one), so the row this request produced is the governing
    one and its day is the day asserted -- read under the owner's lock, so no
    concurrent save can have replaced it (ruling R-CC79).  It holds for ruling
    R-EQ's UNCHANGED outcome too: nothing was written because the governing
    assertion already carried that day and balance, so reading its day still
    answers with the day submitted.

    A SUPPLIED day is returned as given, because a back-dated assertion does
    not govern and the governing row's day would name a different one.

    Args:
        submission: What the form asserted.
        anchor: The assertion governing AFTER the write -- the write door's
            :attr:`~app.services.anchor_service.AnchorTrueUpReport.governing_after`.

    Returns:
        The civil day the acknowledgement should name.
    """
    if submission.observed_on is None:
        return anchor.observed_on
    return submission.observed_on


def _true_up_success_response(
    account: Account, revert_context: str | None,
    submission: _AnchorSubmission, report: AnchorTrueUpReport,
) -> tuple[str, int, dict[str, str]]:
    """Compose the anchor true-up success response.

    Shared by ``true_up``'s COMMITTED and UNCHANGED outcomes (both render the
    updated display cell and fire ``balanceChanged`` so other surfaces
    recompute).  Three fragments can ride along, and each is mounted where it
    can actually survive -- which is the whole of plan step X-f1e3:

    * **the display of the screen that opened the editor**, the response's
      primary target: that screen's own draw from :data:`_SURFACES`, the one
      its Cancel calls (rulings R-CC74 / R-CC77, finding CC-365) -- empty when
      that screen no longer shows the account (archived mid-edit), because
      the write has committed and a 404 would answer it as a failure;
    * **the reconcile prompt, the acknowledgement, or neither**, both
      out-of-band into a ``base.html`` mount that no refresh region owns, so
      both reach all five surfaces by construction;
    * **the ``#anchor-as-of`` caption, for the GRID alone**, because it is the
      only surface whose caption nothing else redraws (see
      ``grid/_anchor_as_of_oob.html`` for the per-surface measurement).

    **The prompt and the acknowledgement answer DIFFERENT questions, and
    welding them into one if/else is finding N-204** (plan step X-f2-b).  They
    were the two branches of :func:`_submission_is_the_coverage_boundary`,
    which is the right predicate for the PROMPT -- it is what makes the offered
    purchases the ones a statement for that day could actually settle -- and
    was never the right one for the acknowledgement.  Keying the second on the
    first's complement left a real write landing with NOTHING on screen:
    re-record the balance that already governs for a LATER day and a row is
    appended (the day differs), the coverage boundary moves, the cell
    re-renders to the same figure, and the prompt is ``""`` whenever nothing is
    outstanding.  Measured on production, that shape -- an equal balance
    re-asserted on a later day -- occurs once in the Checking account's 57
    assertions.

    So the acknowledgement now asks its own question: **did anything visible
    happen?**  It fires when the GOVERNING balance did not move and the prompt
    did not open.  The two stay mutually exclusive, but as a CONSEQUENCE of
    that second clause rather than as a construction -- and every state that
    acknowledged before still does, because a back-dated write cannot move the
    governing balance and so is a strict subset of the new condition.

    The comparison is on the BALANCE alone, deliberately, not on
    ``(balance, day)``.  Including the day would count the same-balance-later-day
    write as "something changed" because the ``as of`` caption moved -- and
    that is exactly the row N-204 is about.  A caption is also the wrong thing
    to rest on: it does not move at all for a back-dated write, so treating it
    as sufficient here and not there would be two rules for one question.

    **Both sides of that comparison are the write door's report, read under
    the owner's lock** (ruling **R-CC79**), and so is everything that decides
    between prompt and note: the coverage boundary is ``governing_after``'s
    day, and the prompt reconciles against ``governing_after`` itself.  Read
    here and in the route, outside the lock, two tabs saving at once could
    compare against a replaced figure and show the wrong one.

    **A BACK-DATED submission is acknowledged rather than rendered**, and the
    reason is that without it this response is indistinguishable from doing
    nothing.  The cell re-renders from the assertion that governs NOW -- the
    report's ``governing_after`` -- which a back-dated correction by definition
    does not change, so a user who recorded an older statement saw their
    editor collapse back to the same figure with no sign the write landed.
    That is the defect :func:`_anchor_editor_error` exists to prevent on the
    failure side, and it was still live on the success side.  It reached ONE of
    the five surfaces until plan step X-f1e3 gave it a mount of its own
    (finding **N-199**).

    Args:
        account: The post-commit account.
        revert_context: The normalized surface token, or ``None`` -- which is
            the grid, the one surface the "as of" snippet is emitted for.  It
            picks the draw that answers.
        submission: What the form asserted.  Supplies both figures the
            acknowledgement names.  **Required, with no default**: a defaulted
            submission here means "suppress the safety check", and with one
            caller a default that can only ever be wrong is a footgun rather
            than a convenience.
        report: The write door's report.  A ``None`` ``governing_before``
            (no assertion at all) counts as "the balance moved", which is what
            a first assertion does.  ``governing_after`` dates the "as of"
            snippet by the ASSERTION's ``observed_on``, never the row's
            ``updated_at`` (ruling R-EP).  ``outcome`` decides the
            acknowledgement's COPY, not whether it fires: an R-EQ submission
            matching what governs writes nothing yet reaches it, so "Balance
            recorded" there would be false.

    Returns:
        The ``(body, status, headers)`` tuple Flask returns, carrying the
        ``HX-Trigger: balanceChanged`` header.
    """
    before, after = report.governing_before, report.governing_after
    # The screen's own draw, run AFTER the write: each builds its read pass as
    # its GET does, and a pass built before the write would memoize the
    # pre-write fold.  The grid's reads none; it draws the assertion the door
    # reported.  ``None`` is a screen that no longer shows the account.
    cell = _SURFACES[revert_context].draw(account, after)
    html = "" if cell is None else cell
    # The one question worth asking after a balance reading -- which of these
    # purchases has your bank taken?  Empty when nothing is outstanding, so the
    # one-click habit is not taxed by a prompt with nothing in it.  The
    # boundary is the reported assertion's day: ``MAX(observed_on)`` over the
    # same owner-declared rows ``cash_ledger.reconciled_through`` would ask.
    feedback = (
        prompt_fragment(account, after)
        if _submission_is_the_coverage_boundary(
            after.observed_on, submission.observed_on,
        )
        else ""
    )
    if not feedback and before is not None and after.balance == before.balance:
        feedback = render_template(
            "accounts/_anchor_recorded_toast.html",
            account=account,
            # The figure the owner TYPED, crossed back from the held balance
            # the gate stored: a card's "$1,200.00 owed" (ruling R-CC57), an
            # asset's balance as typed.
            balance=liability_sign.shown_figure(
                account.account_type, submission.balance,
            ),
            asks_owed=liability_sign.asks_owed(account.account_type),
            # The day the SUBMISSION asserted, resolved: a blank date box means
            # the user's today, and the acknowledgement names the day the
            # balance is about rather than leaving it to be guessed from a
            # figure that did not move.
            observed_on=_submitted_or_resolved_day(submission, after),
            was_written=report.outcome is AnchorTrueUpOutcome.COMMITTED,
        )
    # ``None`` is the grid, and only the grid: every named surface re-fetches
    # its own region on the ``balanceChanged`` fired below and redraws its own
    # caption, while three of them carry no ``#anchor-as-of`` element at all
    # (an out-of-band swap there would orphan-target, htmx:oobErrorNoTarget).
    as_of = (
        ""
        if revert_context is not None
        else render_template(
            "grid/_anchor_as_of_oob.html", observed_on=after.observed_on,
        )
    )
    return html + as_of + feedback, 200, {"HX-Trigger": "balanceChanged"}


def _anchor_day_bounds() -> dict[str, date]:
    """Return the editor's date-input bounds, keyed for the template.

    The browser refuses what the seam would refuse rather than round-tripping a
    rejection, and both bounds come from the same two PRIMITIVES
    :func:`app.services.anchor_service.resolve_observation_day` refuses by --
    never from a template literal.  **Stated precisely, because a first version
    of this docstring claimed the bounds come from that function**: it exposes
    neither as a value, so the floor here is genuinely one shared implementation
    (``pay_period_service.earliest_recordable_day``) while the ceiling is a
    SECOND reading of the same clock.  A midnight tick between this render and
    the submission therefore lets the browser offer a day the service then
    refuses -- which is exactly why that refusal is rendered
    (:func:`_anchor_editor_error`) rather than assumed unreachable.
    ``display_today()`` rather than ``date.today()``: the
    process clock is pinned to the display zone in the deployed container but
    not in CI or a script, and an input must not offer a day the service then
    rejects (ruling R-DH (b)).

    The layering is deliberate, not redundant: an input bound is captured at
    RENDER time, and the floor moves when pay periods are generated or
    truncated, so a form left open across such a change can still submit a day
    the seam refuses.  That is why the refusal is also rendered
    (:func:`_anchor_editor_error`) rather than assumed unreachable.

    Returns:
        The ``observed_on_min`` / ``observed_on_max`` pair, as dates.
    """
    return {
        "observed_on_min": pay_period_service.earliest_recordable_day(
            current_user.id,
        ),
        "observed_on_max": display_today(),
    }


def _anchor_kind_refusal(account: Account) -> ResponseReturnValue:
    """Refuse a cash-anchor write on an AMORTIZING account, renderably.

    **The kind refusal answers a DISPLAY cell, not an editor** (plan step
    X-f1e3).  A loan's balance is ledger-derived and is asserted on the loan's
    own page (ruling D4 / step A1, finding B-15), so there is nothing here to
    resubmit -- re-rendering the editor would offer a Save button guaranteed to
    be refused again, which is the dead-end affordance this module's own
    ``anchor_form`` docstring says never to offer.
    :func:`_anchor_editor_error` is the right answer for the two INPUT-shaped
    rejections and the wrong one for this, so the two do not share a function.

    **It used to answer a raw string body**, which ``base.html`` leaves
    non-swapping, so the refusal rendered NOTHING and the form sat there.  That
    was justified on the claim that the arm is unreachable because
    ``anchor_form`` refuses to OPEN the editor for a loan -- and an account's
    kind is EDITABLE, so a form opened on a cash account can be submitted after
    that account has become a loan (finding **N-199**;
    ``test_a_cash_account_can_become_a_loan_under_an_open_editor`` walks the
    path).  The ordinary click that used to reach it is gone -- the shared
    partial now renders a loan's cell read-only -- so what remains is this
    race, and a raced write still deserves an answer its surface can render.

    Keeps the 422 rather than the sibling's 400: the payload is well formed and
    it is the ENTITY that cannot be processed.  A designed fragment swaps on
    any status, so naming the failure honestly costs a non-htmx client nothing.

    Args:
        account: The owned, attached amortizing :class:`Account`.

    Returns:
        The designed-fragment ``(body, 422, headers)`` triple.
    """
    return designed_error(_loan_cell(account, LOAN_ANCHOR_REFUSAL), 422)


def _loan_cell(account: Account, error: str | None = None) -> str:
    """Render an AMORTIZING account's read-only cell: a pointer, no figure.

    **It shows no balance, and that is ruling R-CC53** (plan step
    credit_card:CC-5-5b).  The cell printed the account's cash ASSERTION, which
    for a loan is a row typed at account creation that is not the loan's
    balance in size or, typed before this step, in sign: the production
    Mortgage's reads ``$178,103`` where the loan owes ``$176,719.77``.  A loan's
    balance is its own page's, so the cell points there instead -- and reads no
    assertion at all, which is what leaves nothing on this surface for such a
    row's sign to reach.

    Reached by :func:`anchor_display` (the grid's Cancel) on a direct request
    and by :func:`_anchor_kind_refusal` on the N-199 race; no ordinary click
    opens it, because every surface renders a loan's balance read-only.  A
    successful save never draws it: a save on a loan is refused before the
    write door, by :func:`_true_up_request_gates` through
    :func:`_anchor_kind_refusal` (ruling D4 / step A1), which is one of the
    two ways the N-199 race reaches it (the other is :func:`anchor_form`).

    Args:
        account: The owned, attached amortizing :class:`Account`.
        error: The refusal to show beside the pointer, or ``None``.

    Returns:
        The rendered cell.
    """
    return render_template(
        "grid/_anchor_edit.html", account=account, editing=False, error=error,
    )


def _anchor_editor_error(
    account: Account, revert_context: str | None, message: str,
) -> ResponseReturnValue:
    """Re-render the anchor editor in place, carrying *message*, as a 400.

    **The echo is NOT crossed, and that is deliberate** (plan step
    credit_card:CC-5-5b): the boxes held what the owner typed, which on a
    liability is already the amount OWED, so the redisplay shows it back as
    typed and keeps the box's "Amount owed" label (``asks_owed``).

    **The ONE rejection surface this door has** (plan step X-f1c4c).  Until that
    step its only rejection answered ``jsonify(errors=...)`` with no marker
    header -- and ``base.html``'s htmx config leaves 4xx non-swapping, so
    clearing the balance box and pressing Save produced a correct 400 that
    rendered NOTHING and left the form sitting there.  Adding a date box made a
    second rejection reachable by ordinary use (a day below the schedule, a form
    submitted after midnight), so the surface had to exist; converting the
    balance arm onto it too is what stops one form having a visible refusal and
    an invisible one.

    Echoes the SUBMITTED values rather than the stored ones: whichever field was
    wrong, the other is still what the user meant, and retyping it is not part of
    the fix.  Jinja escapes both into their attributes, and a value the browser
    cannot parse renders as an empty input -- the native affordance for "this
    needs re-entering".

    Args:
        account: The owned, attached :class:`Account` under edit.
        revert_context: The normalized surface token, or ``None``.  Threaded so
            Cancel / Escape from the error state still restore the surface that
            OPENED the editor rather than stranding a dashboard card on the grid
            cell.
        message: The user-facing reason, already flattened to one sentence.

    Returns:
        The designed-fragment ``(body, 400, headers)`` triple; the global
        ``htmx:beforeSwap`` listener in ``app.js`` swaps it despite the status.
    """
    return designed_error(
        render_template(
            "grid/_anchor_edit.html",
            account=account,
            anchor_balance=request.form.get("anchor_balance", ""),
            observed_on_value=request.form.get("observed_on", ""),
            editing=True,
            asks_owed=liability_sign.asks_owed(account.account_type),
            error=message,
            revert_url=_anchor_revert_url(account.id, revert_context),
            revert_context=revert_context,
            **_anchor_day_bounds(),
        ),
        400,
    )


def _true_up_request_gates(
    account: Account, revert_context: str | None,
) -> tuple[_AnchorSubmission | None, ResponseReturnValue | None]:
    """Run every pre-mutation gate for ``true_up`` in one place.

    The route grew a fifth early-return gate when the amortizing-kind
    refusal landed (ruling D4 / step A1), tripping Pylint's
    return-statement ceiling; consolidating the gates into a
    ``(values, failure)`` helper mirrors ``_validate_update_account``'s
    established shape.  Gate order: kind refusal first (a loan is
    rejected before its form is even validated -- the KIND of edit is
    wrong, not the payload), then schema validation.

    **Two gates left at plan step X-f1c3c and neither was weakened.**  The
    C-17 stale-form check went with ruling R-EN: an assertion history is
    APPEND-ONLY, so a second tab overwrites no ASSERTION and there is no
    conflict to REPORT -- two assertions are two facts and the later-observed
    one is current.  What that check was incidentally serialising, the posting
    reconcile, is serialised explicitly now
    (:mod:`app.services.user_write_lock`); a door-level gate was never the
    right home for it, since three other doors reach the same window.
    The "No current pay period found" 400 went with ruling R-EO:
    an assertion carries no pay period, so there is no period to resolve and
    nothing this door can fail to find.  That 400 was the true-up half of
    finding N-134's shape -- a balance the user typed, refused for want of a
    budgeting artifact that has nothing to do with what their bank holds.

    **BOTH arms answer a DESIGNED FRAGMENT.**  The schema arm converted at plan
    step X-f1c4c (it was ``jsonify(errors=...)``); the kind arm at X-f1e3, and
    the reason it had NOT converted was a measured-false claim this docstring
    used to make.  It said the editor is never OPENED for an amortizing account
    (``anchor_form`` refuses the same kind), so the arm answered a forged
    request and a designed fragment would be a rendering nobody could reach.
    **An account's kind is EDITABLE.**  ``_ACCOUNT_UPDATE_FIELDS`` includes
    ``account_type_id``, and ``_validate_account_type_change`` permits a
    boundary-crossing re-type while the account holds no ledger line -- which
    a ``$0.00`` opening leaves it holding, because a zero correction books no
    line (``ledger_lines.accounts_hold_lines``).  So: open the editor on such an
    account, re-type it to a mortgage in a second tab, press Save.  That is a
    real user in ordinary use, and ``base.html`` leaves 4xx non-swapping, so
    the raw body rendered NOTHING and the form simply sat there -- the exact
    defect X-f1c4c converted the other arm to prevent (finding **N-199**).
    It keeps the 422: the payload is well-formed and the ENTITY is what cannot
    be processed, and a designed fragment swaps on any status.

    It is the same open-then-change race ``_anchor_day_bounds`` already
    anticipates for the date floor, one field over: a form captured at render
    time can always be submitted after the state it was rendered against moved.

    Args:
        account: The owned, attached :class:`Account` under edit.
        revert_context: The normalized surface token, or ``None`` -- needed
            because a rejection RE-RENDERS the editor, and the re-rendered
            editor's Cancel must still return to the surface that opened it.

    Returns:
        ``(submission, failure)``.  On success ``failure`` is ``None`` and
        ``submission`` carries the validated balance and day; on rejection
        ``failure`` is the ready-to-return Flask response and ``submission``
        is ``None``.
    """
    if classify_account(account) is AccountProjectionKind.AMORTIZING:
        return None, _anchor_kind_refusal(account)

    errors = _anchor_schema.validate(request.form)
    if errors:
        return None, _anchor_editor_error(
            account, revert_context, flatten_schema_errors(errors),
        )

    data = _anchor_schema.load(request.form)
    # Ruling R-CC61: a form rendered under the other meaning is refused BEFORE
    # anything is staged, and re-opened as a fresh click opens it under the
    # account's meaning now -- NOT echoing the figure typed under the old one
    # (ruling R-CC62; see :func:`_fresh_editor`).
    stale = door_meaning_refusal(account, data["asks_owed"])
    if stale is not None:
        return None, designed_error(
            _fresh_editor(account, revert_context, stale), 400,
        )
    # The ONE crossing this door makes (plan step credit_card:CC-5-5b, rulings
    # R-CC52 / R-CC57): a liability's box asks for the amount OWED, on every
    # surface the editor opens from, and the write door stores the held sign.
    return _AnchorSubmission(
        balance=liability_sign.held_balance(
            account.account_type, Decimal(str(data["anchor_balance"])),
        ),
        observed_on=data.get("observed_on"),
    ), None


@accounts_bp.route("/accounts/<int:account_id>/true-up", methods=["PATCH"])
@require_owner
def true_up(account_id):
    """Update the anchor balance for an account (inline edit from grid).

    Records the true-up in anchor_history for audit trail, then
    triggers a balance recalculation via HX-Trigger.

    Refuses an AMORTIZING account with 422 (ruling D4 / step A1,
    finding B-15): a loan's balance is ledger-derived and is asserted
    through the loan page's own true-up, never as a cash anchor.

    **It carries no optimistic lock, and that is ruling R-EN** (plan step
    X-f1c3c).  The form used to submit ``version_id`` and a mismatch answered
    409 with the editor in conflict mode; the service used to translate a
    flush-time ``StaleDataError`` into the same response.  Both are gone,
    because a true-up no longer writes the ``accounts`` row that
    ``version_id`` guards -- it appends an assertion.  **No ASSERTION is
    overwritten by a second tab**: two assertions of different balances are
    two facts, the later-observed one is current, and neither is lost.  Two
    tabs submitting the SAME balance for the same day are still idempotent --
    the write door compares against the governing assertion under the owner's
    lock and writes nothing (ruling R-EQ), and the route reports success.  The
    LEDGER those assertions reconciled into was serialised by the same lock,
    which this PATCH's transaction took before the route ran, as every
    signed-in save's does (plan step ``balance:X-bn``), so the settle
    self-heal and the pay-period resync, both gone since plan#3076, held it too.
    *It was taken inside the reconcile until then: a lock at this door would
    have left those doors, and the direct anchor edit X-f1e deleted, unguarded.*
    """
    account = get_or_404(Account, account_id)
    if account is None:
        return "Account not found", 404

    # The opener (dashboard balance card, cockpit cell, detail hero) threads
    # its surface on the PATCH query so the success re-render matches the
    # surface that opened the editor.  Normalized against the allowlist so the
    # token is never interpolated unvalidated.
    revert_context = _normalize_revert_context(request.args.get("revert"))

    # Both pre-mutation gates (the D4/A1 amortizing-kind refusal and schema
    # validation) live in ``_true_up_request_gates``; a failure is returned
    # as-is.
    submission, failure = _true_up_request_gates(account, revert_context)
    if failure is not None:
        return failure

    # Canonical anchor true-up path: route the assertion append, the posting
    # re-base and the commit through the single authoritative helper
    # (``anchor_service.apply_anchor_true_up``) so ruling R-EQ's duplicate
    # rule cannot drift.  The route pre-gates
    # the amortizing kind, so the service's
    # ``AmortizingAccountAnchorError`` backstop is unreachable here (a
    # bypassing caller correctly surfaces it as a 500).  The
    # success-response composition (the updated cell, the optional OOB
    # "as-of" snippet, and the ``HX-Trigger: balanceChanged`` header)
    # lives in ``_true_up_success_response``.
    #
    # The route reads no governing assertion of its own (ruling R-CC79): the
    # door REPORTS today's before and after, both read inside the owner's lock.
    #
    # The DAY's bounds are the seam's, not this route's (ruling R-ER): a future
    # day and a day below the owner's schedule are refused by
    # ``anchor_service.resolve_observation_day``, shared with
    # ``account_service.create_account`` so an account's OPENING assertion and
    # every later one agree about which days are assertable.  (It was shared
    # with the account-edit door too, until plan step X-f1e deleted that door --
    # this is now the only place a balance is RE-asserted.)  Raised BEFORE
    # anything is staged, so nothing needs rolling back here: the open
    # transaction holds the owner's lock (taken before this route ran, plan
    # step ``balance:X-bn``) but has written nothing, and teardown ends it.  It
    # is a 400, not a 500, because the date box makes it ordinary user input.
    try:
        report = anchor_service.apply_anchor_true_up(
            account=account,
            new_balance=submission.balance,
            observed_on=submission.observed_on,
        )
    except ValidationError as exc:
        return _anchor_editor_error(account, revert_context, str(exc))

    # UNCHANGED and COMMITTED share the success response (the
    # updated cell + an OOB "as of" snippet + the HX-Trigger that
    # recomputes other grid cells), so they converge on one return.
    if report.outcome is AnchorTrueUpOutcome.UNCHANGED:
        # Ruling R-EQ idempotent success: the submission asserts the balance
        # that already stands, so nothing was written and the session was
        # rolled back.  Expire the account so the draws re-read it rather
        # than anything this request held.
        db.session.expire(account)
    else:
        db.session.refresh(account)
        # No log line here.  ``anchor_service.stage_anchor_true_up`` logs the
        # account, the balance AND the resolved day -- and it is the only layer
        # that knows the day, because a blank date box means "today" without
        # this one being told what that resolved to.  A route line naming the
        # same account and balance MINUS the day is a strict subset of the
        # writer's, and two INFO lines per true-up where one is contained in the
        # other is noise that reads as corroboration.

    return _true_up_success_response(
        account, revert_context, submission, report,
    )


def _anchor_revert_url(account_id, revert_context):
    """Resolve the URL the anchor editor reverts to on Cancel / Escape.

    The anchor editor (``grid/_anchor_edit.html``) is opened from more
    than one surface, and Cancel / Escape must restore whichever surface
    opened it -- not always the grid display cell.  The normalized surface
    token (from :func:`_normalize_revert_context`) picks the screen's row of
    :data:`_SURFACES`, whose endpoint re-renders the opener through the same
    draw a save answers with.  ``None`` is the grid's ``anchor_display``, so
    the grid path is byte-for-byte unchanged (it passes no ``revert``).

    Args:
        account_id: The account whose editor is being reverted.
        revert_context: The normalized surface token, or ``None``.

    Returns:
        The revert URL string.
    """
    surface = _SURFACES[revert_context]
    if surface.takes_account_id:
        return url_for(surface.endpoint, account_id=account_id)
    return url_for(surface.endpoint)


def _fresh_editor(
    account: Account, revert_context: str | None, error: str | None = None,
) -> str:
    """Render the anchor editor as a fresh click opens it, optionally refusing.

    The ONE rendering of an editor that has not been typed into: the standing
    balance in the door's words, today's date, the day bounds.  :func:`anchor_form`
    answers a click with it, and the stale-form refusal (ruling **R-CC61**)
    re-opens with it -- carrying the refusal beside the box, and deliberately
    NOT the figure that was typed (ruling **R-CC62**, plan step
    credit_card:CC-5-5b).  That figure was typed under the meaning the box no
    longer has, so echoing it under the new label left the refused save one
    Enter away: a $0.00 Checking account re-typed to a card, 2,500.00 typed and
    refused, re-opened as "Amount owed 2500.00" and stored a card owing
    $2,500.00 on the next Enter (measured by CC-5-5b's re-review).  Opened
    fresh, Enter re-asserts the standing figure, which changes nothing.

    Args:
        account: The owned, attached, non-amortizing :class:`Account`.
        revert_context: The normalized surface token, or ``None`` (the grid).
        error: The refusal to show beside the box, or ``None`` for a click.

    Returns:
        The rendered editor.
    """
    revert_url = _anchor_revert_url(account.id, revert_context)
    bounds = _anchor_day_bounds()
    return render_template(
        "grid/_anchor_edit.html",
        account=account,
        # The pre-fill speaks the door's language (plan step
        # credit_card:CC-5-5b, ruling R-CC57): a card holding -1,000.00 opens
        # on 1,000.00 owed, whichever surface opened it; an asset opens on its
        # balance.  The surface the editor replaces keeps its own sign.
        anchor_balance=liability_sign.shown_figure(
            account.account_type, cash_ledger.resolve_anchor(account).balance,
        ),
        editing=True,
        asks_owed=liability_sign.asks_owed(account.account_type),
        # The statement day defaults to TODAY, not to the governing assertion's
        # own day (rulings **R-EE** / **R-EI**, plan step X-f1c4c).  A true-up is
        # the user reading their bank NOW in the overwhelming case, and R-EE
        # keeps that one click plus Enter; prefilling the last assertion's day
        # would make the ordinary path silently RE-assert an old day, which is
        # the one thing this field exists to stop being a guess.  Back-dating is
        # then a deliberate edit of a box that already shows the right answer.
        observed_on_value=bounds["observed_on_max"].isoformat(),
        revert_url=revert_url,
        revert_context=revert_context,
        error=error,
        **bounds,
    )


@accounts_bp.route("/accounts/<int:account_id>/anchor-form", methods=["GET"])
@require_owner
def anchor_form(account_id):
    """HTMX partial: return the inline edit form for the anchor balance.

    Accepts an optional ``revert`` query parameter naming the surface
    that opened the editor (e.g. ``dashboard``), so Cancel and Escape
    restore that surface rather than always swapping in the grid display
    cell.  See :func:`_anchor_revert_url` for the mapping; an unset value
    keeps the grid's default revert target.

    The normalized token is also passed to the template as
    ``revert_context`` so the edit form's ``hx-patch`` carries the surface
    through the mutation round-trip, keeping the success re-render on the
    surface that opened the editor rather than stranding the dashboard card
    on the grid display cell.  It carried a second job -- letting a 409
    conflict response re-render the conflict cell with the same retry-reopen
    target -- until ruling R-EN deleted that response (plan step X-f1c3c).
    """
    account = get_or_404(Account, account_id)
    if account is None:
        return "Not found", 404

    # Ruling D4 / step A1: never OPEN the cash anchor editor for an
    # amortizing loan -- the PATCH would be refused (B-15), so offering
    # the form would be a dead-end affordance.
    #
    # **The affordance itself is gone since plan step X-f1e3**: the shared
    # partial renders a loan's balance read-only on every surface, the rule the
    # cockpit's loan cards already followed and the other four did not.  So
    # this arm no longer answers an ordinary click; what can still reach it is
    # a RACE -- the cell was rendered while the account was cash and clicked
    # after it became a loan (an account's kind is editable).  It answers a
    # designed fragment rather than a raw body for that case, because a raw
    # 4xx is left non-swapping by ``base.html`` and the click would otherwise
    # do nothing visible at all -- a dead click with no form to explain it,
    # which is finding N-199's defect in its worst form.
    if classify_account(account) is AccountProjectionKind.AMORTIZING:
        return _anchor_kind_refusal(account)

    return _fresh_editor(
        account, _normalize_revert_context(request.args.get("revert")),
    )


@accounts_bp.route("/accounts/<int:account_id>/anchor-display", methods=["GET"])
@require_owner
def anchor_display(account_id):
    """HTMX partial: return the anchor balance display (non-editing).

    The grid's Cancel / Escape target, answering with :func:`_cash_cell` --
    the draw a save opened from the grid answers with too (ruling R-CC77) --
    over the governing assertion it reads.  **It holds the only loan check on
    the grid's Cancel path** (ruling **R-CC79**): only Cancel can hand the
    grid's draw a loan, because a save on one is refused before the write
    (ruling D4 / step A1), and :func:`_loan_cell` reads no assertion (R-CC53).
    """
    account = get_or_404(Account, account_id)
    if account is None:
        return "Not found", 404
    if classify_account(account) is AccountProjectionKind.AMORTIZING:
        return _loan_cell(account)
    return _cash_cell(account, cash_ledger.resolve_anchor(account))
