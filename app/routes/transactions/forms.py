"""
Shekel Budget App -- Transaction route package: read-only HTMX partials.

The GET routes that return display / edit / create form fragments for the
grid: the display cell, the quick-edit and full-edit popovers, and the
quick-create / full-create / empty-cell placeholders.  None of these
mutate state.
"""

from typing import NamedTuple

from flask import render_template, request
from flask_login import current_user

from app import ref_cache
from app.enums import TxnTypeEnum
from app.extensions import db
from app.models.ref import Status
from app.models.category import Category
from app.models.account import Account
from app.services import (
    category_service,
    credit_workflow,
    definition_delete,
    match_withdrawal,
    pay_period_service,
    status_seam,
    transaction_service,
)
from app.services.account_resolver import (
    resolve_cash_flow_set,
    resolve_owner_cash_flow_set,
)
from app.services.card_today import date_left_by_a_move
from app.services.cash_flow_set import CashFlowSet, purchase_accounts
from app.services.pay_calendar import FiledRow, calendar_for
from app.services.scenario_resolver import get_baseline_scenario
from app.utils.auth_helpers import require_owner
from app.utils.balance_predicates import is_credit
from app.utils.dates import display_today
from app.utils.digit_strings import integer_arg
from app.routes._period_options import period_move_options
from app.routes._refused_press import RedrawnCard
from app.routes._render_helpers import (
    fragment_amounts,
    render_transaction_cell,
    settles_at_nothing,
)
from app.routes.transactions._bp import transactions_bp
from app.routes.transactions._helpers import (
    _get_owned_transaction,
    _resolve_owned_fks,
    _resolve_owned_period,
)


@transactions_bp.route("/transactions/<int:txn_id>/cell", methods=["GET"])
@require_owner
def get_cell(txn_id):
    """HTMX partial: return the display-mode cell content for a transaction."""
    txn = _get_owned_transaction(txn_id)
    if txn is None:
        return "Not found", 404
    return render_transaction_cell(txn)


@transactions_bp.route("/transactions/<int:txn_id>/quick-edit", methods=["GET"])
@require_owner
def get_quick_edit(txn_id):
    """HTMX partial: return the minimal inline amount input."""
    txn = _get_owned_transaction(txn_id)
    if txn is None:
        return "Not found", 404
    return render_template(
        "grid/_transaction_quick_edit.html",
        txn=txn,
        # What the row is worth NOW, which is what the field must be primed
        # with (plan step X-au-c2b).  It read ``txn.estimated_amount``, the
        # COLUMN: on a derived row that is empty, so the field would open
        # BLANK and a save would book whatever the user typed over a figure
        # they never saw.  Priming it with the resolved amount also makes the
        # save honest -- typing the same number back is a no-op, where
        # accepting a blank would not be.
        #
        # A MAP the template indexes, not the scalar this first published: an
        # adversarial review measured that a missing scalar renders
        # ``value=""`` in SILENCE while a missing map raises, and these two
        # forms are the surfaces where an empty figure is POSTED BACK.  A
        # fallback that ships a blank into a save is the shape this whole step
        # exists to delete, so it may not survive on the edit doors.
        budgets=fragment_amounts(txn).budgets,
        # **The SECOND render site of an Estimated box**, and it needs the same
        # withdrawal the full-edit popover got (plan step X-au-j).  An
        # adversarial review found this one ungated: nothing in
        # ``app/templates`` or ``app/static/js`` links here any more
        # (``grid_edit.js`` keeps tier-1 inline editing only for the
        # empty-cell quick-create), but the route is live behind the login
        # gate and ``@require_owner``, so an owner reaching it by URL
        # was offered a control the PATCH door now always rejects. The census
        # in ``repays_card_spend``'s docstring said "two surfaces"; it was
        # three.
        budget_correctable=not transaction_service.repays_card_spend(txn),
    )


@transactions_bp.route("/transactions/<int:txn_id>/full-edit", methods=["GET"])
@require_owner
def get_full_edit(txn_id):
    """HTMX partial: return the full edit popover form.

    A transfer's popover is the TRANSFER's door (``transfers.get_full_edit``)
    since leaf ``X-bi-6-1``: the grid draws a transfer as a leg read off its
    parent and the leg's cell asks that door with its ``leg_account_id``.
    This door answered the transfer form for a SHADOW row until then; a
    shadow is no row this blueprint admits now
    (:func:`~app.routes.transactions._helpers._get_owned_transaction`).
    """
    txn = _get_owned_transaction(txn_id)
    if txn is None:
        return "Not found", 404
    return render_full_edit(txn, page_refusal=None)


def render_full_edit(txn, *, page_refusal):
    """Render the full-edit popover for *txn* from its current state.

    **ONE render for the popover's GET and for its REDRAW** (plan step
    ``credit_card:CC-5-4a-5``, ruling **R-CC128**, developer 2026-10-04,
    "Redraw all"): a press the popover sends that is refused as out of date
    -- by the removal act over the bank lines (ruling **R-CC127**), or by the
    row delete over its dialog's purchases (ruling **R-CC131**) -- answers
    with this same card, drawn from what is true now, with the refusal above
    it, so every box and every caption on the redrawn card, and what it posts
    back, come from one moment.  Two
    renders would be two answers to what the card shows.

    Args:
        txn: The row, owner-established by the caller
            (:func:`~app.routes.transactions._helpers._get_owned_transaction`).
        page_refusal: :attr:`~app.exceptions.PageOutOfDate.facts` for a
            redraw, else ``None``.

    Returns:
        The rendered ``grid/_transaction_full_edit.html``.
    """
    statuses = db.session.query(Status).all()
    # Pay periods power the in-popover period-move selector.  Only the
    # current and future periods are offered -- moving an expense into an
    # already-closed period is not a supported workflow -- but the row's
    # own period is always included so a transaction that currently sits
    # in a past period stays selected (and is not silently re-pointed at
    # the first current period on save).  Periods are per-user; the PATCH
    # handler re-checks ownership of the submitted id (F-029).
    # ONE derivation for this render, threaded -- the shape of ledger row
    # **P68**, closed by C2-f3c (two derivations of one owner's calendar in
    # one render, nothing holding the two equal; **P69** is its open
    # sibling): the
    # ``<select>`` below is built from it and so is the card's context line,
    # which names the row's OWN paycheck.  Asking twice would be two answers to
    # one question with nothing holding them equal.
    #
    # **The row is loaded BEFORE the calendar is derived**, and each
    # ``require_period`` caller owes its own statement of that order.  That
    # method documents TWO states that reach its refusal and BOTH are closed
    # here, which a first draft of this comment did not say -- it argued only
    # the first and read as a proof (adversarial review, 2026-08-31).
    #
    #   * **A picture from more than one moment** (finding **N-358**).  This
    #     door is a GET, so ``db_transaction`` binds it to ``REPEATABLE READ,
    #     READ ONLY`` -- both reads see one snapshot.  It is not a fragment
    #     that splits its own transaction: no ``write_transaction`` block runs
    #     on this path, which is what makes the GET argument sound where it is
    #     unsound on ``/grid``.
    #   * **A row filed in ANOTHER owner's pay period -- and the leg that
    #     closes it MOVED, exactly as this comment predicted it would.**
    #     ``_get_owned_transaction`` scoped on ``txn.pay_period.user_id``, so
    #     the calendar built from ``current_user.id`` below and the owner of
    #     the period the row names were the same BY CONSTRUCTION.  That door
    #     reads ``txn.user_id`` since plan step ``pay_calendar:C13-b`` and no
    #     longer proves anything about the row's PERIOD's owner -- and it does
    #     not have to: ``fk_transactions_owner_period`` (plan step ``C13-a``)
    #     makes a row whose paycheck belongs to someone else UNSTORABLE, so
    #     the equality this leg used to establish by asking is now a fact the
    #     schema will not let be false.  The leg is replaced, not lost.
    calendar = calendar_for(current_user.id)
    periods = period_move_options(calendar, txn.pay_period_id)
    amounts = fragment_amounts(txn)
    # A PLACED row's item is edited HERE (ruling **R-BAL23**; the controls
    # **R-BAL36**): its category select offers the owner's ACTIVE categories
    # -- the definition's own edit form's list (``list_active_categories``:
    # an archived category is not a selectable target), plus the row's own
    # category when THAT is archived, so an untouched save posts the category
    # the row has rather than the first option a browser selects when the
    # rendered one is missing.  A first draft copied the transfer branch's
    # raw query above and offered archived categories; found by adversarial
    # review.  Loaded only for such a row; every other row's category is its
    # definition's (edited on the template form) or, until the family's
    # cutover, a legacy row's own, which the popover has never offered.
    categories = []
    if txn.is_placed:
        categories = category_service.list_active_categories(current_user.id)
        if txn.category is not None and txn.category not in categories:
            categories.append(txn.category)
    # The two ``is_last_row_of_its_definition`` readers below share ONE read:
    # the refusal's merchant-rule arm and the dialog's pair sentence want the
    # same answer, and the delete verb threads it the same way.
    last_row_of_definition = definition_delete.is_last_row_of_its_definition(txn)
    # The OWNER's set (this door is owner-only, so ``txn.user_id`` is the
    # requester), read once for its two readers: the "Paid from" picker and
    # the cards a Credit press could mean (plan task plan#3056).
    owner_set = resolve_owner_cash_flow_set(txn.user_id)
    tender_accounts, tender_account_id = _tender_picker(txn, owner_set)
    credit_cards = transaction_service.credit_cards(txn, owner_set)
    return render_template(
        "grid/_transaction_full_edit.html",
        card_id=full_edit_dom_id(txn.id),
        # A date a move left on a card opens its box (ruling plan#2991).
        date_left_by_a_move=date_left_by_a_move(txn, calendar),
        page_refusal=page_refusal,
        # **What taking the row's payment out of its matches would WITHDRAW**
        # -- read once, for the card's four captions: a different "Paid
        # from" pick re-points the payment (plan step
        # ``credit_card:CC-5-4a-1``, ruling **R-CC46**), and a ``$0.00``
        # Actual, a ``$0.00`` estimate saved as Paid (ruling **R-CC129**) or
        # Paid on a row whose purchases replace its payment takes it off the
        # books (plan step ``credit_card:CC-5-4a-3``, rulings **R-CC54** /
        # **R-CC56**).  Every accepted match names the row's payment, and
        # none of the four keeps it named, so all four free the same lines
        # -- said BEFORE the press, the disclosure a delete makes through its
        # dialog, read through the same twin
        # (``match_withdrawal.pending_for_movements``) the doors' own write
        # uses.  ``None`` for a row holding no payment; a withdrawal freeing
        # nothing renders nothing.
        payment_withdraws=_payment_withdrawal(txn),
        # **What leaving Credit would WITHDRAW** -- Undo CC, or Status set
        # back to Projected, deletes the row's CC payback (plan step
        # ``credit_card:CC-5-4a-5``, ruling **R-CC80**: *"all three
        # card-payback buttons name the bank line before you press"*), read
        # through the doors' own twin over the same payback.  ``None`` for a
        # row that is not Credit, whose card offers neither press.
        payback_withdraws=(
            credit_workflow.pending_for_credit_revert(txn)
            if is_credit(txn) else None
        ),
        # **Whether Undo CC takes the row's payment OFF THE CARD** (plan task
        # plan#3055): a row put on the card from another account, whose
        # revert moves its payment home and out of every match naming it on
        # the card -- the lines ``payment_withdraws`` names, so the card's
        # Undo CC caption reads them.  The seam asks the same predicate as
        # it reverts.  ``False`` for a bill that lives on the card.
        takes_off_the_card=status_seam.revert_moves_payment_home(txn),
        # **Whether Paid / Received would record $0.00** (plan step
        # ``credit_card:CC-5-4a-5``): such a settle takes a kept payment off
        # the books exactly as a typed $0.00 does, so Paid's caption names
        # what it withdraws (R-CC56) -- read off the two maps this card
        # already draws, by the contract they state.  The card asks it only
        # of a Projected row whose figure is not its purchases'.
        paid_records_nothing=settles_at_nothing(
            amounts.budgets[txn.id], amounts.retained[txn.id],
        ),
        txn=txn,
        categories=categories,
        # The row's OWN paycheck, as the DERIVED value (plan step C4-a-5).  The
        # card printed ``txn.pay_period.label`` -- the ORM row's accessor, which
        # formats the STORED ``end_date`` -- while the ``<select>`` beside it
        # printed the derived one, so on a period whose stored end has gone
        # stale (findings **P12** / **P28**) one paycheck was labelled two ways
        # inside one card.  ``PayPeriod.label`` is deleted with this step, so
        # the wrong source is unreachable rather than merely unused.
        period=calendar.require_period(FiledRow.for_row(txn)),
        # **What the Credit press shows** (plan task plan#3056): the day it
        # puts the row on the card, in its "Money moved on" box for the owner
        # to correct (``credit_day``, reading this render's calendar); the
        # cards it could mean, for the card it names, the picker it offers
        # with two or more, or the ask to add one with none
        # (``credit_cards``, the producer the press itself asks); and whether
        # it moves a payment the row keeps onto the card
        # (``credit_moves_payment``), so its caption names the bank lines
        # ``payment_withdraws`` says that frees (ruling **R-CC127**).
        credit_on=transaction_service.credit_day(txn, calendar),
        credit_cards=credit_cards,
        credit_moves_payment=transaction_service.credit_moves_payment(
            txn, credit_cards,
        ),
        # See ``get_quick_edit`` for why the Estimated field is primed with the
        # RESOLVED amount rather than the column, and why it is a MAP.
        #
        # All three travel because :class:`RenderAmounts` is one value and a
        # surface may not publish two of it, and this card reads every one:
        # ``budgets`` primes the Estimated field, ``settled`` primes the Actual
        # box, and ``retained`` draws the re-book notice.  A draft of plan step
        # X-au-c3 drew that Actual box gated on ``locked`` and then deleted it
        # as unreachable -- every ``is_settled`` status is also ``is_immutable``,
        # so it rendered ``disabled`` on 100% of the rows it appeared on.  Being
        # disabled WAS the defect: a lock protects a budget decision, and what
        # the bank took is an observation.
        budgets=amounts.budgets,
        settled=amounts.settled,
        retained=amounts.retained,
        statuses=statuses,
        periods=periods,
        # The settle-day correction's bounds (rulings R-EJ / R-EL) -- see the
        # transfer branch above for why the clock is the display one and why the
        # floor comes from the seam's own function.
        today=display_today(),
        settle_day_min=pay_period_service.earliest_recordable_day(
            current_user.id,
        ),
        # Pre-hint (grid audit D2): the status dropdown disables transitions
        # the row cannot take.  ``offerable_status_ids`` is the state machine's
        # answer narrowed by the row's TYPE -- the map admits both Paid and
        # Received from Projected because it grades the status and never sees
        # ``transaction_type_id``, and exactly one of them is what an income or
        # an expense row settles as (plan step X-ap).
        allowed_status_ids=transaction_service.offerable_status_ids(txn),
        # Ruling **R-FF**, the same sentence the reconcile panel obeys: an
        # amount is correctable exactly when the settle verb takes its MANUAL
        # branch.  An envelope carrying purchases settles at ``sum(entries)``,
        # so an Actual box beside it would take a figure the settle discards.
        amount_correctable=not transaction_service.settles_from_entries(txn),
        # **A CC payback's ESTIMATE is not its own to state either**, which is
        # the same sentence one box over: a payback is worth the card spend of
        # the row it repays, so you change it by changing what went on the
        # card.  The box rendered and took a figure until now, and the next
        # entry mutation on the source silently overwrote it -- finding
        # **N-252**, ``$58.40`` live on the developer's own payback 2590.
        # ``transaction_service.repays_card_spend``'s docstring carries the
        # whole rule, including why a ROW-backed payback needs it for the
        # opposite reason (nothing overwrites it, so the lie sticks).
        budget_correctable=not transaction_service.repays_card_spend(txn),
        # WHICH repair the withdrawn box should name.  The two payback kinds
        # are corrected by different acts and only one of them involves
        # purchases -- ``transaction_service.repays_tracked_purchases``'s
        # docstring carries the fork.  Resolved here rather than in the
        # template, which displays a decision and never takes one.
        budget_from_purchases=transaction_service.repays_tracked_purchases(txn),
        # **Why this row may NOT be deleted, or ``None``** (plan step
        # ``bank_import:X-gb``).  The card renders the delete control exactly
        # when this is ``None`` and prints the sentence when it is not, and
        # ``delete_transaction`` re-asks the same function as the
        # crafted-request backstop -- the layering every guard in
        # ``_gates`` uses, with the rule in the service and the screen
        # displaying its answer.
        delete_refusal=transaction_service.deletion_refusal(
            txn, last_row_of_definition=last_row_of_definition,
        ),
        # **What deleting it would take back besides the row itself**
        # (:class:`~app.services.transaction_service.RowDeletion`): the CC
        # payback rows that go down with it, and the bank lines whose matches
        # it would empty.  The dialog NAMES them rather than counting them,
        # because the control destroys records and *"1 bank line"* over a
        # `$793.23` ACH payment is the *"Nothing moved."* sentence this arc has
        # already shipped once.
        #
        # It is the SAME function the door's own verb calls over the SAME row
        # set -- a draft read the row alone while the press also tore down its
        # payback chain, and an adversarial review measured a `$200.00` card
        # payment silently un-explained over a dialog naming no line at all.
        delete_preview=transaction_service.preview_deletion(
            txn, last_row_of_definition=last_row_of_definition,
        ),
        # **The "Paid from" picker** (plan step ``credit_card:CC-5-3``): the
        # accounts a payment under this row may have moved through, in
        # picker order -- the row's own first, then the OWNER's cash-flow set
        # (ruling **R-CC39**; the SAME tuple the add-purchase form renders
        # and both doors gate on) -- and which one to preselect: what the row
        # RECORDS as its tender (the kept payment's account, else the row's
        # own; ``status_seam.tender_account_id_of``, the seam's one default),
        # so an untouched Save posts an echo the door drops and a reverted
        # bill paid from another account shows that account (ruling
        # **R-CC42**; one put on the card is home again, plan#3104).  The card
        # renders it only when there is a choice (ruling **R-CC34**).
        # Resolved for the OWNER (``txn.user_id``; this door is owner-only,
        # so the two coincide) with no override, as every picker is.
        tender_accounts=tender_accounts,
        tender_account_id=tender_account_id,
    )


def full_edit_dom_id(txn_id):
    """Return the ``id`` of a row's full-edit card -- what a redraw replaces.

    Spelled once for the card's root (``card_id``) and the redraw that
    targets it (:func:`redraw_full_edit`, ruling **R-CC128**).

    Args:
        txn_id: The row's id.

    Returns:
        The DOM id.
    """
    return f"txn-full-edit-{txn_id}"


def redraw_full_edit(txn_id, facts):
    """Re-read a row after a refused press and draw its card again (ruling **R-CC128**).

    The transaction popover's half of
    :func:`app.routes._refused_press.answer_refused_press`, which has rolled
    the press back before calling this, so the row is read as it stands.

    Args:
        txn_id: The row the press named.
        facts: :attr:`~app.exceptions.PageOutOfDate.facts`, drawn above the
            card.

    Returns:
        The :class:`~app.routes._refused_press.RedrawnCard`, or ``None`` when
        the row is no longer the requester's to draw -- the ownership door's
        own answer (:func:`_get_owned_transaction`).
    """
    txn = _get_owned_transaction(txn_id)
    if txn is None:
        return None
    return RedrawnCard(
        full_edit_dom_id(txn_id), render_full_edit(txn, page_refusal=facts),
    )


def _payment_withdrawal(txn):
    """Return what taking *txn*'s payment out of its matches would withdraw, or ``None``.

    The read twin of the seam's two writes on the payment -- the re-point
    (``status_seam._covering._re_point`` calls
    ``match_withdrawal.withdraw_for_moved_movement``) and the removal a
    ``$0.00`` or ``purchases`` record makes (``_covering._withdraw`` calls
    ``movement_removal.remove_movements``) -- so each caption on the card and
    the act under it come from one derivation, the shape the delete dialog
    has over ``preview_deletion``.  One read serves all three, because an act
    names the MOVEMENT and none of the three keeps it named.

    Args:
        txn: The row the popover is drawn for, with ``entries`` loaded.

    Returns:
        A :class:`~app.services.match_withdrawal.MatchWithdrawal`, or
        ``None`` when the row holds no payment.
    """
    movement = status_seam.covering_movement_of(txn)
    if movement is None:
        return None
    return match_withdrawal.pending_for_movements([movement])


def _tender_picker(txn, owner_set):
    """Return ``(accounts, selected id)`` for the popover's "Paid from" picker.

    The accounts a payment under *txn* may NAME
    (:func:`~app.services.cash_flow_set.purchase_accounts` over the OWNER's
    set, the tuple both doors gate on) **plus the account the row RECORDS
    when that is no longer among them** -- a card the owner has since
    archived, or a checking account that was the primary when the bill was
    paid.  The same shape this route gives an archived category one block
    up, and for the same reason: a ``<select>`` whose preselected value is
    missing submits its FIRST option, so without this arm an untouched Save
    on a bill paid from a since-archived card would post the row's own
    account, read as a CORRECTION (not the echo the door drops), re-point
    the payment onto checking and release both clearing links -- and the
    documented unlock path (Status = Projected) would be refused for a
    tender nobody changed.  Found by the neutral review of this leaf.  The
    recorded account never reaches the gate: an echo of it is dropped
    before the gate is asked (``status_seam.tender_for_status``).

    Args:
        txn: The row the popover is drawn for, with ``entries`` loaded.
        owner_set: The row OWNER's cash-flow set with no override, read once
            by :func:`render_full_edit` for this picker and the Credit press.

    Returns:
        The tuple to render, in picker order, and the id to preselect.
    """
    accounts = purchase_accounts(owner_set, txn)
    recorded_id = status_seam.tender_account_id_of(txn)
    if recorded_id not in {account.id for account in accounts}:
        accounts = (*accounts, db.session.get(Account, recorded_id))
    return accounts, recorded_id


# ---- the empty-cell family ------------------------------------------


# ---- the empty-cell family ------------------------------------------
#
# The three fragments a cell with no transaction in it can show -- the dash,
# the quick-create input, the full-create popover -- are all addressed by the
# same coordinate, so they resolve it through one function.


class _GridCell(NamedTuple):
    """The grid coordinate one of the three empty-cell fragments renders for.

    Produced by :func:`_resolve_grid_cell`, the query-string prefix that
    :func:`get_quick_create`, :func:`get_full_create` and
    :func:`get_empty_cell` share: all three read the same four ids and
    ownership-check the same three of them, and until plan step **C2-f3e**
    each wrote that out for itself.

    Attributes:
        category: The row's :class:`~app.models.category.Category`, proved to
            belong to the requester.
        period_id: The ``budget.pay_periods.id`` of the column's paycheck, as
            the owner's own calendar answered it -- an id this owner holds by
            construction rather than one a comparison let through.  It is what
            the two create forms POST back as ``pay_period_id``, so it is the
            paycheck any row created from this cell is funded by.
        account: The viewed :class:`~app.models.account.Account`, proved to
            belong to the requester.
        transaction_type_id: Income or expense.  A reference-table id and so
            not ownership-checked; that it is also unvalidated is one of the
            ``request.args.get(..., type=integer_arg)`` sites plan step
            ``balance:X-ah`` owns.
    """

    category: Category
    period_id: int
    account: Account
    transaction_type_id: int


def _resolve_grid_cell():
    """Resolve and ownership-check the grid cell this request's query names.

    Plan step **C2-f3e**, closing ledger row **P51**.

    **The period is answered by the OWNER'S CALENDAR, and that is what makes
    the ownership check structural.**  It was a fourth
    :func:`_resolve_owned_fks` spec -- fetch ``budget.pay_periods`` by primary
    key, then compare ``row.user_id`` against the requester.  A calendar holds
    ONE owner's whole schedule and nothing else, so an id that is not in it is
    not this owner's, and a single lookup answers "no such period" and "not
    yours" with the identical 404 the security response rule asks for.  There
    is no comparison left for a later edit to drop.  This is the shape plan
    step C2-f2b put on ``grid.partials.mobile_this_period_summary`` and plan
    step C2-f3c put on ``_resolve_carry_forward_context`` one module over.

    **It did NOT take the last ORM ``PayPeriod`` out of this blueprint, and a
    first draft of this paragraph claimed it did.**  Measured with
    ``tests._test_helpers.pay_periods_hydrated``: after C2-f3e
    ``/transactions/<id>/cell``, ``/quick-edit`` and ``/full-edit`` each still
    hydrated exactly one, because :func:`._helpers._get_owned_transaction`
    walked ``txn.pay_period.user_id`` -- one of the ELEVEN such comparisons
    ledger row **P75** counted, beside EIGHT more that fetched the row by
    primary key.  **Plan step ``pay_calendar:C13-b`` retired all nineteen**,
    and what it did to THESE three is documented where the hydration now
    happens: :func:`app.routes._render_helpers.render_transaction_cell`, which
    reads ``pay_period.start_date`` for the due caption and used to get it
    free off the ownership walk.  The count did not fall; the load MOVED, and
    that docstring carries the re-measurement.

    **What it costs, stated rather than glossed**, because the honest
    comparison is not free: a primary-key ``session.get`` becomes
    :func:`~app.services.pay_calendar.calendar_for`'s two queries and a
    derivation over the owner's whole payday set.  That is once per fragment
    request, where the page that offers the fragment already derives one per
    render, and the two cheap probes are ordered FIRST so a request naming a
    foreign category or account is refused before any of it runs.  No row
    count is quoted here on purpose: the owner's payday set GROWS as the
    rolling top-up extends the schedule, and eleven docstrings in this
    repository already state that moving number, ten of them at a value it has
    since passed.

    Returns:
        ``(cell, None)`` on success, or ``(None, (message, 404))`` on the
        first ownership failure -- a Flask response tuple the caller returns
        directly to HTMX.  ``period_id`` is carried out of the DERIVED period
        rather than off the query string: the two are the same integer, and
        taking the confirmed one means no unchecked value can reach a form
        even if a later edit moves the guard.

    Raises:
        PayCalendarError: The owner's paydays cannot define a calendar (see
            :func:`~app.services.pay_calendar.calendar_for`).  Uncaught, as it
            is at every other route that derives one: the grid page these
            fragments are swapped into derives its own calendar to render at
            all, so an owner who can reach this door has one that derives.
    """
    category_id = request.args.get("category_id", type=integer_arg)
    period_id = request.args.get("period_id", type=integer_arg)
    account_id = request.args.get("account_id", type=integer_arg)
    transaction_type_id = request.args.get(
        "transaction_type_id", type=integer_arg,
        default=ref_cache.txn_type_id(TxnTypeEnum.EXPENSE),
    )

    # Ownership: prevent IDOR -- return the identical 404 for "does not exist"
    # and "belongs to another user" so an attacker cannot distinguish the two
    # cases and enumerate another user's category names.  See audit finding H1.
    objs, err = _resolve_owned_fks([
        (Category, category_id, "Not found"),
        (Account, account_id, "Not found"),
    ])
    if err is not None:
        return None, err

    # The lookup and its forensic half are ``_resolve_owned_period``'s since
    # plan step ``pay_calendar:C13-b``, which gave this shape a name so the
    # three WRITE doors could take it too.  They were silent -- they ran
    # through ``_resolve_owned_fks`` -- and now share this one's
    # ``log_refused_lookup`` call, which is that helper's own stated rule: an
    # owner-scoped lookup cannot tell "no such row" from "not yours", which is
    # the stronger security property and exactly why it must not also mean no
    # trail.  The body stays this blueprint's uniform ``"Not found"`` so a
    # fragment's 404 still says nothing about WHICH of its four ids was wrong.
    period, err = _resolve_owned_period(period_id, "Not found")
    if err is not None:
        return None, err

    return _GridCell(
        category=objs[Category],
        period_id=period.period_id,
        account=objs[Account],
        transaction_type_id=transaction_type_id,
    ), None


@transactions_bp.route("/transactions/new/quick", methods=["GET"])
@require_owner
def get_quick_create():
    """HTMX partial: return a quick-create input for an empty cell.

    Query params: category_id, period_id, account_id, transaction_type_id.
    """
    cell, err = _resolve_grid_cell()
    if err is not None:
        return err

    # Look up the baseline scenario for hidden fields.  Not part of the cell
    # coordinate: :func:`get_empty_cell` renders a dash for an owner who has
    # none, and answering it 400 would be a new refusal rather than a shared
    # one.
    scenario = get_baseline_scenario(current_user.id)
    if not scenario:
        return "No baseline scenario", 400

    return render_template(
        "grid/_transaction_quick_create.html",
        category=cell.category,
        # The ID, not the row -- the whole ``PayPeriod`` was being carried for
        # this one integer (plan step C2-f3e, ledger row **P51**), which left
        # the three create partials stating their period contract two ways.
        period_id=cell.period_id,
        account_id=cell.account.id,
        scenario_id=scenario.id,
        transaction_type_id=cell.transaction_type_id,
    )


@transactions_bp.route("/transactions/new/full", methods=["GET"])
@require_owner
def get_full_create():
    """HTMX partial: return the full create popover form.

    Query params: category_id, period_id, account_id, transaction_type_id.

    **The form offers an ACCOUNT PICKER over the owner's cash-flow set**
    (plan step ``credit_card:CC-4-2``, ruling **R-CC16**): the cell's
    ``account_id`` is the balance line of the grid that opened the popover,
    and the set it HEADS --
    :func:`~app.services.account_resolver.resolve_cash_flow_set` with that
    id as the override, the resolution ``/grid?account_id=`` makes -- is
    what the picker lists, that account selected.  A member heads the whole
    set (checking and its cards); an owned cash-flow account outside it
    (savings) heads a set of one.

    **An id the grid resolver would NOT put on the line -- a loan, an
    archived account, or any id for an owner with no grid-eligible account
    -- is the cell's own set of one, never the primary's set.**  This is a
    WRITE form: it carries the account it was opened with and the create
    door decides, exactly as it decides the quick-create's hidden input.
    The grid PAGE falls through to the primary for the same crafted URL,
    and a first draft copied that; the review named the difference (two
    create doors answering one id two ways) and the developer ruled
    2026-09-18: carry the id, let the door refuse -- a write never
    re-targets silently.  **What that door refuses today is narrower than
    the ruling's premise said**: a foreign account is 404 and an amortizing
    loan is 422 (``create._reject_transaction_on_loan``), but an ARCHIVED
    account is ADMITTED -- neither create door reads ``is_active`` -- so a
    crafted id for one lands a plan row on an account no grid surface loads.
    Pre-existing at both doors (the quick-create carried the same id
    before this step), found by CC-4-2's re-review, reported to the
    developer and the coordinator as a candidate ledger row rather than
    closed here.  Only a crafted URL reaches any of this: the empty cell
    names the page's balance line, which the resolver admitted.

    ONE member renders the hidden input the form always carried, so the
    pre-card popover is byte-identical.  The quick-create keeps its hidden
    default: it is the one-keystroke path.
    """
    cell, err = _resolve_grid_cell()
    if err is not None:
        return err

    scenario = get_baseline_scenario(current_user.id)
    if not scenario:
        return "No baseline scenario", 400

    cash_flow = resolve_cash_flow_set(
        current_user.id, current_user.settings, cell.account.id,
    )
    if cash_flow is None or cash_flow.balance.id != cell.account.id:
        cash_flow = CashFlowSet.single(cell.account)
    # No ``statuses``: the create form has no status control -- a new
    # transaction is born Projected (the create route assigns it), so there is
    # nothing for the user to pick.  Status changes happen later through the
    # mark-done / cancel / credit / full-edit actions on the saved row.
    return render_template(
        "grid/_transaction_full_create.html",
        category=cell.category,
        # The ID, not the row -- see :func:`get_quick_create`.
        period_id=cell.period_id,
        # The cell's own account in every case (the docstring's rule); the
        # set's balance is that account by construction above.
        account_id=cell.account.id,
        accounts=cash_flow.members,
        scenario_id=scenario.id,
        transaction_type_id=cell.transaction_type_id,
    )


@transactions_bp.route("/transactions/empty-cell", methods=["GET"])
@require_owner
def get_empty_cell():
    """HTMX partial: return the empty cell placeholder.

    Used by Escape key to revert a quick-create form back to the dash.
    Query params: category_id, period_id, account_id, transaction_type_id.
    """
    cell, err = _resolve_grid_cell()
    if err is not None:
        return err

    return render_template(
        "grid/_transaction_empty_cell.html",
        category=cell.category,
        # IDS, not rows.  The partial builds one URL and reads nothing else off
        # either; its other render entry -- the desktop grid macro -- has only
        # a ``DerivedPeriod`` to give it since plan step C2-f2b.  The ACCOUNT
        # moved the same way here: both callers hold an ORM row, so it was not
        # the two-types defect **P51** records, but it was the same whole row
        # carried for one integer, and leaving it made the family's contract
        # false in its own header comment.
        period_id=cell.period_id,
        account_id=cell.account.id,
        txn_type_id=cell.transaction_type_id,
    )
