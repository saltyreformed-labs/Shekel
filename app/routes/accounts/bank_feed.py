"""
Shekel Budget App -- The bank feed panel: connect, list, map, disconnect

The four doors of the feed panel every cash account's statements page carries
(plan step ``bank_import:X-f6b-2``, leaf (3c); rulings **R-BI12**, **R-BI26**,
**R-BI27**).  The feed is the OWNER's -- one ``budget.bank_feeds`` row, one
set of declared mappings -- so these routes take no ``account_id`` in their
path: the panel is the same panel on every statements page, and the account
whose page it was pressed on rides in the form only to say where to answer.

**Two answer shapes, and which door takes which.**  The claim and the
"Map accounts" press answer with the PANEL (an HTMX swap), because ruling
R-BI27 has the claim fetch Bridge's account list and render the mapping form
in its own response -- a redirect could carry that list only through the
session cookie, which the ruling rejected.  The mapping submission and the
disconnect answer with a flash and a redirect to the page they were pressed
on, the shape every other statement write door takes.

**The claim commits BEFORE the listing is asked for** (R-BI27), and the
order is the whole point: Bridge consumes a setup token on the claim, so the
access URL it answers with must be stored before anything that can fail is
attempted.  A listing that fails after the commit leaves the feed connected
with no mappings, and the panel says so and offers the press that retries.

Services boundary: this module owns the HTTP-shaped concerns -- ownership,
form parsing, the panel render, flashes and redirects -- and delegates every
read and write to :mod:`app.services.bank_feed`.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from flask import abort, flash, redirect, render_template, request, url_for
from flask_login import current_user

from app.exceptions import BankFeedError
from app.routes.accounts._bp import accounts_bp
from app.routes.accounts._cash_page import load_cash_account_or_404
from app.routes.accounts._statement_doors import (
    StatementDoorContext,
    StatementFragmentDoorContext,
    log_refusal,
    refusal_sentence,
    run_statement_door,
    run_statement_fragment_door,
)
from app.schemas.validation import form_payload
from app.schemas.validation.bank_feed import (
    FeedClaimSchema,
    FeedMappingSchema,
    FeedPageSchema,
)
from app.services.bank_feed import (
    BridgeListing,
    claim_feed,
    disconnect_feed,
    feed_state,
    list_bridge_accounts,
    map_accounts,
    mappable_accounts,
)
from app.utils.auth_helpers import require_owner
from app.utils.error_fragments import designed_error
from app.utils.log_events import (
    BUSINESS,
    EVT_BANK_FEED_ACCOUNTS_MAPPED,
    EVT_BANK_FEED_CLAIMED,
    EVT_BANK_FEED_DISCONNECTED,
    log_event,
)

_logger = logging.getLogger(__name__)

#: The panel's template: ONE for every state, included by the statements
#: page and answered by the two fragment doors.
PANEL_TEMPLATE = "accounts/_bank_feed_panel.html"

_claim_schema = FeedClaimSchema()
_mapping_schema = FeedMappingSchema()
_page_schema = FeedPageSchema()


@dataclass(frozen=True)
class _PanelView:
    """What a press learned from Bridge, for the panel to render.

    The fragment door's SCOPE for the feed panel: the pre-act view carries
    nothing (Bridge was not asked), the post-act view carries Bridge's list
    or why it could not be fetched.  A value rather than a rendered
    response, so :func:`run_statement_fragment_door`'s contract holds as
    written -- ``reread`` derives, ``render`` renders.

    Attributes:
        listing: Bridge's list when a press fetched one, which opens the
            mapping form.
        listing_error: Why a press could not fetch one, when it could not.
    """

    listing: BridgeListing | None = None
    listing_error: str | None = None


def _page_account():
    """Load the statements page the panel was pressed on, or 404.

    The feed is the owner's, so the account decides nothing about the act;
    it decides where to answer, and it is gated the way the page itself is
    (:func:`load_cash_account_or_404`: not the caller's, or not a cash
    kind, is a 404) so a form naming another owner's account answers
    nothing about it.  A form naming no account at all is a 404 too: no
    page of ours posts one.

    Returns:
        The :class:`~app.models.account.Account`.
    """
    if _page_schema.validate(request.form):
        abort(404)
    return load_cash_account_or_404(
        _page_schema.load(request.form)["account_id"],
    )


def _panel(account, view: _PanelView, *, error=None):
    """Render the feed panel for *account*'s statements page.

    ONE render for every state the panel has -- no feed, a feed, a feed
    with Bridge's list open, any of those with a sentence to say -- so the
    page's include and the four doors' answers cannot drift apart.

    Args:
        account: The page's account.
        view: What the press learned from Bridge.
        error: A refusal sentence for the act just pressed.

    Returns:
        The rendered panel at 200, or the designed-fragment 400 when
        *error* is set.
    """
    body = render_template(
        PANEL_TEMPLATE,
        account=account,
        feed=feed_state(current_user.id),
        listing=view.listing,
        listing_error=view.listing_error,
        panel_error=error,
        mappable=mappable_accounts(current_user.id) if view.listing else [],
    )
    return body if error is None else designed_error(body, 400)


def _listing_view() -> _PanelView:
    """Ask Bridge for the owner's accounts, live, and say how it went.

    Ruling **R-BI27**: fetched on this press and never stored.  A listing
    that fails is a refusal the owner reads on the panel and the log records
    with its class (**BI-499**) -- logged HERE because this read commits
    nothing and so takes no door helper -- and the panel then offers the
    press again.

    Returns:
        The :class:`_PanelView`.
    """
    try:
        return _PanelView(listing=list_bridge_accounts(current_user.id))
    except BankFeedError as exc:
        log_refusal(_logger, exc)
        return _PanelView(listing_error=str(exc))


@accounts_bp.route("/accounts/feed/claim", methods=["POST"])
@require_owner
def claim_bank_feed():
    """Claim a pasted setup token, then open the mapping form.

    **The claim COMMITS before the listing is asked for** (ruling R-BI27):
    the token is consumed at Bridge by the claim, so the access URL is
    stored -- the unit of work closed -- before the request that can fail
    without costing anything is made.  :func:`run_statement_fragment_door`
    owns the commit; the listing is the ``reread`` it renders the success
    from, and a listing that fails with a designed refusal -- Bridge
    unreachable, an error status, a shape not read, a stored URL the key
    cannot decrypt -- renders the connected panel with the reason and the
    press that retries, never a rollback of the claim.

    Returns:
        The feed panel: the mapping form on success; the paste form with a
        refusal sentence, as a designed 400, when the claim was refused.
    """
    account = _page_account()
    errors = _claim_schema.validate(request.form)
    if errors:
        return _panel(account, _PanelView(), error=refusal_sentence(errors))
    setup_token = _claim_schema.load(request.form)["setup_token"]

    def _report(feed):
        log_event(
            _logger, logging.INFO, EVT_BANK_FEED_CLAIMED, BUSINESS,
            "Claimed a SimpleFIN setup token; the access URL is stored.",
            user_id=current_user.id, feed_id=feed.id,
        )

    return run_statement_fragment_door(
        StatementFragmentDoorContext(
            logger=_logger,
            render=lambda view, *, outcome=None, error=None: (
                _panel(account, view, error=error)
            ),
            scope=_PanelView(),
            reread=_listing_view,
            log_message="user_id=%d failed to claim a bank feed",
            log_args=(current_user.id,),
            db_error_message=(
                "Something went wrong saving the feed.  Nothing was "
                "connected."
            ),
            refusal=BankFeedError,
        ),
        lambda: claim_feed(current_user.id, setup_token),
        _report,
    )


@accounts_bp.route("/accounts/feed/accounts", methods=["POST"])
@require_owner
def list_bank_feed_accounts():
    """Ask Bridge for the owner's accounts and open the mapping form.

    The "Map accounts" press: a READ of Bridge, live (ruling R-BI27), so it
    commits nothing and takes no door helper.  A POST rather than a GET
    because it spends one of Bridge's daily requests and must not be
    prefetched, cached or re-run by a page refresh.

    Returns:
        The feed panel with Bridge's list open, or with why it is not.
    """
    return _panel(_page_account(), _listing_view())


def _feed_door(*, act: str, flash_message: str, target: str) -> StatementDoorContext:
    """Return the context for one redirect-shaped feed door.

    The mapping door and the disconnect door configure identically but for
    what they were doing and what to say when the database refused -- the
    shape :func:`~app.routes.accounts._statement_doors.fragment_door` was
    extracted for on the reconcile page, and for the same reason: two doors
    that differ only in their act and their sentence are two copies of one
    configuration otherwise.

    Args:
        act: What the door was doing, as an infinitive phrase for the
            failure log line.
        flash_message: What to tell the owner when the database refused.
        target: The statements page to answer on.

    Returns:
        The :class:`StatementDoorContext`.
    """
    return StatementDoorContext(
        logger=_logger,
        refusal=BankFeedError,
        log_message=f"user_id=%d failed to {act}",
        log_args=(current_user.id,),
        flash_message=flash_message,
        target=target,
    )


def _mapping_flash(outcome) -> tuple:
    """Return what the receipt says the mapping submission did.

    Every figure renders, each clause only when non-zero, and a submission
    that changed nothing says so rather than "Saved".

    Args:
        outcome: The :class:`~app.services.bank_feed.MappingOutcome`.

    Returns:
        ``(message, category)``.
    """
    parts = []
    if outcome.declared:
        parts.append(f"mapped {outcome.declared} Bridge account(s)")
    if outcome.moved:
        parts.append(f"moved {outcome.moved} to a different account")
    if outcome.cleared:
        parts.append(f"unmapped {outcome.cleared}")
    if not parts:
        return (
            f"Nothing changed: the mapping already said this for all "
            f"{outcome.unchanged} Bridge account(s).",
            "info",
        )
    return f"Saved the mapping: {', '.join(parts)}.", "success"


@accounts_bp.route("/accounts/feed/map", methods=["POST"])
@require_owner
def map_bank_feed_accounts():
    """Save which account here each of Bridge's accounts is.

    Returns:
        A redirect to the statements page the form was on, with a flash
        saying what changed.
    """
    account = _page_account()
    target = url_for("accounts.statements", account_id=account.id)
    payload = form_payload(request.form, _mapping_schema)
    errors = _mapping_schema.validate(payload)
    if errors:
        flash(refusal_sentence(errors), "warning")
        return redirect(target)
    loaded = _mapping_schema.load(payload)
    choices = dict(zip(loaded["external_id"], loaded["target_account_id"]))

    def _report(outcome):
        log_event(
            _logger, logging.INFO, EVT_BANK_FEED_ACCOUNTS_MAPPED, BUSINESS,
            "The owner declared which account each Bridge account is.",
            user_id=current_user.id,
            declared=outcome.declared, moved=outcome.moved,
            cleared=outcome.cleared, unchanged=outcome.unchanged,
        )
        return _mapping_flash(outcome)

    return run_statement_door(
        _feed_door(
            act="save the bank feed mapping",
            flash_message=(
                "Something went wrong saving the mapping.  Nothing was "
                "changed."
            ),
            target=target,
        ),
        lambda: map_accounts(current_user.id, choices),
        _report,
    )


@accounts_bp.route("/accounts/feed/disconnect", methods=["POST"])
@require_owner
def disconnect_bank_feed():
    """Delete the owner's feed; its declared mappings go with it.

    Returns:
        A redirect to the statements page the form was on, with a flash
        saying what was removed and what the owner still owes Bridge.
    """
    account = _page_account()
    target = url_for("accounts.statements", account_id=account.id)

    def _report(outcome):
        log_event(
            _logger, logging.INFO, EVT_BANK_FEED_DISCONNECTED, BUSINESS,
            "The owner disconnected the bank feed; the access URL is gone.",
            user_id=current_user.id,
            mappings_removed=outcome.mappings_removed,
        )
        removed = (
            f"  {outcome.mappings_removed} account mapping(s) declared under "
            f"it went with it."
            if outcome.mappings_removed else ""
        )
        return (
            f"Disconnected: the stored access URL is deleted.{removed}  What "
            f"the feed recorded stays.  Revoke the token at Bridge as well; "
            f"the app cannot do that for you.",
            "success",
        )

    return run_statement_door(
        _feed_door(
            act="disconnect the bank feed",
            flash_message=(
                "Something went wrong disconnecting the feed.  Nothing was "
                "changed."
            ),
            target=target,
        ),
        lambda: disconnect_feed(current_user.id),
        _report,
    )
