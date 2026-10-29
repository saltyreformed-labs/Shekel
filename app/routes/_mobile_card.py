"""
Shekel Budget App -- The phone card a card-targeted request is answered with.

The mobile / companion card's action bar posts ``render=mobile_card`` (with
its tab prefix and its edit flag) to a row's door or to a transfer's, and
either door answers with that ONE card re-rendered in place.  This module is
the shared home of both halves of that answer, beside
``app/routes/_render_helpers.py`` and on its package-level convention:
:class:`RenderTarget`, which surface a request asked to be answered on, and
:func:`render_mobile_card`, the one card renderer for a plan row and a
transfer side alike (ruling **plan#1983**, "price the parent, draw the
side").  Until that ruling the transaction routes held the first as a
private ``_RenderTarget`` and the second as ``_render_mobile_card``, and the
transfer routes re-spelled each (``_LegRequest`` re-read two of the three
fields off the form; ``_render_helpers.render_transfer_leg_card`` repeated
the categories query, the row key, the error card and the amounts).
"""

from dataclasses import dataclass

from flask import render_template, request
from flask_login import current_user

from app.extensions import db
from app.models.category import Category
from app.routes._render_helpers import (
    fragment_amounts,
    fragment_cash_flow,
    render_transaction_cell,
)
from app.services import grid_view_service
from app.services.entry_service import (
    build_entry_lists_dict,
    build_entry_sums_dict,
)
from app.services.pay_calendar import FiledRow, calendar_for
from app.services.transfer_legs import PlanItem
from app.utils.dates import display_today


@dataclass(frozen=True)
class RenderTarget:
    """The response surface a request renders into.

    Bundles the three render-routing fields the mobile / companion card
    action bar posts (``render=mobile_card`` plus the per-tab
    ``card_prefix`` and the ``can_edit`` flag) so a door and its helpers
    thread one value instead of three parallel arguments.  The desktop grid
    and full-edit popover omit these, so the default (empty
    ``render_mode``) resolves to the cell.  The transaction doors read it
    for a row; the transfer doors hold it inside their ``_LegRequest`` for
    a side, because the card's one form posts the same three fields for
    both shapes (``grid/_mobile_card_actions.html``).
    """

    render_mode: str
    card_prefix: str
    can_edit: bool

    @classmethod
    def from_form(cls) -> "RenderTarget":
        """Read the surface this request asked to be answered on off its form.

        The mobile / companion card action bar posts ``render=mobile_card``
        plus the per-tab ``card_prefix`` and the ``can_edit`` flag so the
        response is a single re-rendered card (in-place swap, no reload); the
        desktop grid and full-edit popover omit these, so the response
        defaults to the cell.  Read off ``request.form`` directly -- these are
        render-routing fields, not part of any money schema -- and before
        any validation, so a refusal renders the right surface too.

        Returns:
            The surface, read once for the request.
        """
        return cls(
            request.form.get("render", ""),
            request.form.get("card_prefix", ""),
            request.form.get("can_edit") == "1",
        )


def render_mobile_card(
    item: PlanItem, *, card_prefix: str, can_edit: bool,
    error: str | None = None,
) -> str:
    """Render a single mobile card -- a row's or a transfer side's -- for an HTMX swap.

    The mobile / companion Mark Paid form targets the card wrapper
    (``hx-target="#card-<prefix-><id>"``, ``hx-swap="outerHTML"``), so
    this returns exactly that one card re-rendered in its post-action
    state -- the settled badge shows and the Mark Paid button drops --
    without the full-page reload the desktop ``gridRefresh`` path uses.

    Reuses the canonical producers so the swapped-in card is identical
    to the page-load card: :func:`grid_view_service.build_row_keys` for
    the row label, :func:`build_entry_sums_dict` for the progress
    aggregate, and :func:`build_entry_lists_dict` for the inline
    envelope entries.  Ownership scoping uses ``item.user_id`` (the data
    owner; a side's is its transfer's) so the companion path resolves the
    linked owner's categories, not the companion's own (empty) set.  It read
    that value off the paycheck (``txn.pay_period.user_id``) until plan step
    ``pay_calendar:C13-b``; the walk hydrated a whole ``budget.pay_periods``
    row to learn one integer the transaction carries.

    **One renderer for both shapes** (ruling **plan#1983**): every input is
    one call over the item, keyed by its ``cell_key`` and valued by its
    parent (:func:`~app.routes._render_helpers.fragment_amounts`), so a
    side's card is drawn by the code a row's is.  A side tracks no purchases
    and holds none (:attr:`~app.services.transfer_legs.TransferLeg.purchases`),
    so its sums and its purchase list are empty by the same predicates a
    bill's are.  A side's card renders on its OWNER's grid alone -- a
    transfer names no definition, so no companion sees one
    (:attr:`~app.models.transaction.Transaction.visible_to_companion`'s rule,
    ruling **R-BAL73**) -- and its form posts ``can_edit=1`` as the owner's
    row card does, so *can_edit* reads ``True`` for it as it did when the
    transfer twin hard-coded it.

    **It derives the owner's pay calendar for an item that TRACKS
    PURCHASES, since pay-calendar plan step C4-a-3**, which is one indexed
    read of ``budget.pay_periods`` and one of ``budget.pay_schedule`` more
    than this fragment used to cost.  The entry list's out-of-period warning
    needs the paycheck's SPAN, and the span is a derivation over the owner's
    paydays rather than the ``end_date`` column plan step **C4-c** dropped
    -- so a single-row surface that holds no window has to derive rather
    than read.  Deriving is the only honest option left: a targeted "what is
    the next payday after this one" query here would be a second
    implementation of the rule this arc exists to state once.

    **And that owner's cash-flow set, since plan step ``credit_card:CC-5-2``**,
    for the envelope's add-purchase account picker -- the owner's set with no
    override, the purchase door's own predicate -- from the SAME walk that
    resolves the chip's balance line (:func:`fragment_cash_flow`), so the
    card pays one resolve for its two questions.

    Args:
        item: The row or transfer side just acted on, with a row's
            ``entries`` and ``template`` accessible.  A transfer door hands
            the side :func:`~app.services.transfer_legs.leg_of` builds after
            the act; a card reads no side's record (ruling **plan#2136**).
        card_prefix: The per-tab id namespace the requesting card used
            (``"tp"`` for This Period and the companion view; ``""`` for
            prefix-less direct renders).  Drives the wrapper id so the
            outerHTML swap resolves.
        can_edit: ``True`` for the owner card, ``False`` for companion.
        error: Optional rejection message.  When set, the card renders
            a danger banner naming why the mutation was refused (the
            designed error-fragment path; see
            ``routes/transactions/_helpers._error_transaction_response`` and
            ``routes/transfers/_helpers._error_transfer_response``).

    Returns:
        Rendered HTML string for one ``grid/_mobile_card_single.html``, the
        banner-only ``grid/_mobile_card_error.html``, or the item's desktop
        cell (see the comment at the row-key guard).

    Raises:
        AmountUnresolvable: From the resolver, for a parent whose rule cannot
            answer (:func:`fragment_amounts`).
    """
    owner_id = item.user_id
    categories = (
        db.session.query(Category)
        .filter_by(user_id=owner_id)
        .order_by(Category.group_name, Category.item_name)
        .all()
    )
    row_keys = grid_view_service.build_row_keys(
        [item], categories, is_income_section=item.is_income,
    )
    # A just-settled item is neither cancelled nor deleted, so the SUCCESS
    # path yields a row key -- except in finding BAL-565's race: the answer
    # is drawn in a transaction of its own after Mark Paid's commit, a
    # Delete queued behind it can hide a recurring row first, and
    # ``build_row_keys`` skips a deleted row, so the phone card can receive
    # the desktop cell below (plan step ``balance:X-dc`` owns it).  The
    # guard degrades to that cell rather than raising IndexError.  The
    # ERROR path can genuinely land here: the card lists filter cancelled
    # items out, so a stale card's rejected action (e.g. Mark Paid after
    # another device cancelled) has no card to re-render -- swap in a
    # banner-only wrapper that keeps the requesting card's id and says
    # why the action was refused.
    #
    # **Both shapes take this rule** (ruling plan#1983, decided by the lane
    # under R-BAL207 and reported in its pull request).  The transfer twin
    # drew the banner-only wrapper with NO message for a side yielding no
    # row key on any path, so a stale card of a cancelled transfer's side
    # (a refused PATCH's 409, which names no error) swapped in an empty
    # banner where a row's card swaps in its desktop cell.  The cell is the
    # rule written once; the race moves no money.
    if not row_keys:
        if error is not None:
            return render_template(
                "grid/_mobile_card_error.html",
                card_id=grid_view_service.card_dom_id(item, card_prefix),
                error=error,
            )
        return render_transaction_cell(item)
    amounts = fragment_amounts(item)
    budgets = amounts.budgets
    # The owner's cash-flow set, resolved ONCE for the two things this card
    # draws from it (plan step credit_card:CC-5-2): the balance line its
    # account chip is decided against, and the add-purchase picker's
    # accounts -- the OWNER's set with no override, because the purchase
    # door admits the row's own account plus the owner's members whatever
    # page the card is on.  Two questions, one walk.
    cash_flow = fragment_cash_flow(owner_id)
    # The purchase list, drawn for an item that TRACKS PURCHASES and for no
    # other: the builder's own filter (``build_entry_lists_dict`` emits an
    # entry only for such an item) and this guard read the one predicate.
    # The calendar below was gated so first (adversarial review, 2026-08-31:
    # a plain bill draws no purchase list, so deriving its owner's whole
    # calendar was two queries for a value nothing reads).  The build is
    # gated with it rather than left to that filter because the builder asks
    # every item it is handed for its purchase controls BEFORE it filters,
    # which reads a row's id -- a side has none -- and costs a bill's card a
    # payback query whose answer the filter then dropped (ruling plan#1983's
    # route fold, which made this one card for both shapes).
    #
    # The paycheck the row is FILED in, DERIVED (pay-calendar plan step
    # C4-a-3): the entry-list builder judged its out-of-period warning against
    # ``txn.pay_period.end_date`` until then, a stored column plan step C4-c
    # drops.  The owner is the one resolved above -- the DATA owner, so the
    # companion path reads the linked owner's schedule -- and ``require_period``
    # rather than ``period_by_id`` because the id comes off a stored row with a
    # NOT NULL, ``ON DELETE CASCADE`` foreign key, so a ``None`` here would be
    # an inconsistent picture rather than an answer.
    #
    # **The READ ORDER, stated here because ``require_period`` requires every
    # caller to state its own**: the ROW is read first (the refresh after Mark
    # Paid's commit, or the re-fetch after its rollback), the paydays second,
    # and both in ONE transaction holding the owner's write lock.  Every
    # caller is Mark Paid's answer, drawn in the command transaction its
    # commit or rollback opens, which takes that lock before its first read
    # (plan step ``balance:X-bn``, ``app.db_transaction``); a door that
    # DELETES paydays -- reset, regenerate and truncate among them -- is a
    # command too and takes the same lock, so it cannot land between the two
    # reads.  What is reachable is the gap
    # BEFORE this transaction: a door queued on the lock commits between Mark
    # Paid's commit or rollback and this read (finding **BAL-565**, ruled to
    # ship so, **R-BAL156**); plan step ``balance:X-dc`` makes each save one
    # transaction.  *Until plan step ``balance:X-bn`` this said such a door
    # could land between the two reads under ``READ COMMITTED`` (balance
    # finding **N-358**), a render-after-commit path with no snapshot.*
    entry_lists = {}
    if item.tracks_purchases:
        period = calendar_for(owner_id).require_period(FiledRow.for_row(item))
        entry_lists = build_entry_lists_dict(
            [item], budgets, {period.period_id: period}, cash_flow.purchases,
            # The SESSION's user decides whether the card's purchase list may
            # name the owner's bank lines, never ``can_edit`` below, which the
            # browser posts back (plan step ``credit_card:CC-5-4a-5``).
            current_user.id,
        )
    return render_template(
        "grid/_mobile_card_single.html",
        rk=row_keys[0],
        txn=item,
        budgets=budgets,
        settled=amounts.settled,
        retained=amounts.retained,
        entry_sums=build_entry_sums_dict([item], budgets),
        entry_lists=entry_lists,
        can_edit=can_edit,
        id_prefix=card_prefix,
        # The BALANCE LINE the card's account chip is decided against (plan
        # step credit_card:CC-4-2): the balance line of the page this card
        # lands on, or ``None`` for a companion -- by the row's OWNER, not by
        # ``can_edit``, which is a render-routing field the form posts.  The
        # one resolution the desktop cell and the transfer cell share.
        account=cash_flow.balance_line,
        # The USER's civil day, never the process's.  This reaches
        # ``grid/_transaction_entries.html``'s add-purchase form as the
        # ``purchased_on`` default and both pickers' ``max``, and
        # ``entry_service._reject_future_purchase_date`` judges that field
        # against ``display_today()`` (ruling R-M).  With ``date.today()`` here
        # the two clocks disagree on any process not pinned to
        # ``America/New_York`` -- CI runs ``TZ=Pacific/Kiritimati`` precisely to
        # catch this -- and the app's own form would default to a date its own
        # server rejects.  Prod pins the zone, so this was latent rather than
        # live; it is the same two-clock shape as finding N-133 / R2.
        today=display_today(),
        error=error,
    )
