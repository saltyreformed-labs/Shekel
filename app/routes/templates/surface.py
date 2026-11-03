"""
Shekel Budget App -- Recurring route package: the unified Recurring surface.

The ``/templates`` page itself: every active recurring definition a user has --
income, expense AND transfer templates -- in one list, plus the page-wide
Monthly / Per-paycheck unit toggle and the collapsed Archived drawer.  It reads
BOTH template kinds and shapes them through one producer
(:mod:`app.services.recurring_view`), which is why it is not part of the
recurring-TRANSACTION CRUD module beside it.

The loaders here are the surface's own inputs: the producer takes plain data
(its docstring states that contract), so the queries that feed it live at the
route boundary rather than inside it.
"""

import logging

from flask import redirect, render_template, request, url_for
from flask_login import current_user

from app import ref_cache
from app.enums import TxnTypeEnum
from app.extensions import db
from app.models.transaction_template import TransactionTemplate
from app.models.transfer_template import TransferTemplate
from app.models.user import UserSettings
from app.routes.templates._bp import templates_bp
from app.services import recurring_view
from app.services.balance_at import BalanceContext
from app.services.cash_ledger import (
    template_pricing_load_options,
    transfer_template_pricing_load_options,
)
from app.utils.auth_helpers import require_owner

logger = logging.getLogger(__name__)


# Form values the unit-preference toggle submits, mapped to the stored
# boolean.  Any other value is ignored (the toggle only ever sends one of
# these two), so a hand-crafted request cannot force an unexpected state.
_UNIT_MONTHLY = "monthly"
_UNIT_PER_PAYCHECK = "per_paycheck"

def _load_active_transaction_templates(user_id):
    """Load the user's active RECURRING income and expense templates, partitioned.

    One query for the definitions, split by transaction type so the producer
    receives the income and expense sections separately.  ``sort_order,
    name`` fixes the tie-break order the producer then re-sorts by monthly
    cost.

    **What the producer reads of each definition comes with it, so the
    page's statements do not grow per definition.**  The account, the
    category, the rule and the type are ``lazy="joined"`` on the model and
    ride on that one query.  The price history and the salary link are
    ``lazy="select"``, and since plan step X-bp the producer prices every
    definition on the read pass's day from them
    (``obligations_aggregator.todays_price``, ruling plan#2693), so they are
    selected in with the definitions -- a ``SELECT ... IN`` per relationship
    for each 500 definitions (SQLAlchemy's batch), not one per definition --
    by :func:`~app.utils.amount_relationships.template_pricing_load_options`
    (plan#2733: without it the page ran two statements per definition).
    Every loader on this page takes the same options for its kind.

    **Definitions WITH a rule only, since plan step ``balance:X-bi-7b``**
    (ruling **R-BAL23**: *the Recurring list shows definitions with a rule*).
    A rule-less transaction definition is a ONE-OFF's -- the grid popover is
    its whole lifecycle -- and listing it here would give every one-off a
    second door and, once bank import mints one per envelope, fill this page
    with lines that repeat nothing.  Asked of each loaded definition through
    the one accessor (``recurs``) rather than restated in the ``WHERE``: the
    rule rides on the template's joined load, so the filter costs no query.
    The Archived drawer is NOT filtered (:func:`_load_archived_rows`): an
    archived one-off's row is soft-deleted, so that drawer's Unarchive is the
    only door left to it.
    """
    income_id = ref_cache.txn_type_id(TxnTypeEnum.INCOME)
    expense_id = ref_cache.txn_type_id(TxnTypeEnum.EXPENSE)
    templates = [
        template for template in (
            db.session.query(TransactionTemplate)
            .options(*template_pricing_load_options())
            .filter(
                TransactionTemplate.user_id == user_id,
                TransactionTemplate.is_active.is_(True),
            )
            .order_by(TransactionTemplate.sort_order, TransactionTemplate.name)
            .all()
        )
        if template.recurs
    ]
    income = [t for t in templates if t.transaction_type_id == income_id]
    expense = [t for t in templates if t.transaction_type_id == expense_id]
    return income, expense


def _load_active_transfer_templates(user_id):
    """Load the user's active transfer templates, ordered for display.

    With what pricing each reads, for the reason
    :func:`_load_active_transaction_templates` gives.
    """
    return (
        db.session.query(TransferTemplate)
        .options(*transfer_template_pricing_load_options())
        .filter(
            TransferTemplate.user_id == user_id,
            TransferTemplate.is_active.is_(True),
        )
        .order_by(TransferTemplate.sort_order, TransferTemplate.name)
        .all()
    )


def _load_archived_rows(user_id, ctx):
    """Shape the Archived drawer's rows for both template kinds.

    Returns ``(archived_transactions, archived_transfers)`` as
    ``recurring_view.ArchivedRow`` tuples; the unified page renders both under
    one collapsed Archived section with Unarchive actions.  Archived rows carry
    no monthly equivalent, no next date and no share -- they are inactive and
    excluded from every total -- but they DO carry how the definition repeated,
    which since plan step R7a is a producer's answer rather than a phrase a
    template assembles from the rule's columns.

    Args:
        user_id: The owner.
        ctx: The read pass, built once by the caller and shared with the
            active-section producer -- the cadence phrase is measured against
            its calendar and a loan payment's stop is folded in its scenario,
            and building a second pass for the drawer would be a second
            resolution point in one request.
    """
    # Each drawer row shows today's price (``todays_price``), so each query
    # loads what pricing reads, as the active sections' do.
    archived_transactions = (
        db.session.query(TransactionTemplate)
        .options(*template_pricing_load_options())
        .filter(
            TransactionTemplate.user_id == user_id,
            TransactionTemplate.is_active.is_(False),
        )
        .order_by(TransactionTemplate.sort_order, TransactionTemplate.name)
        .all()
    )
    archived_transfers = (
        db.session.query(TransferTemplate)
        .options(*transfer_template_pricing_load_options())
        .filter(
            TransferTemplate.user_id == user_id,
            TransferTemplate.is_active.is_(False),
        )
        .order_by(TransferTemplate.sort_order, TransferTemplate.name)
        .all()
    )
    return (
        recurring_view.build_archived_rows(archived_transactions, ctx),
        recurring_view.build_archived_rows(archived_transfers, ctx),
    )


def _load_recurring_view(user_id, ctx):
    """Build the unified Recurring display model for a user.

    Shared by the full-page ``list_templates`` render and the
    ``set_unit_preference`` HTMX toggle, so both paths produce identical
    figures from one code path.  The toggle only re-picks which unit the
    template displays; it does not open a second money path.

    Args:
        user_id: The owner.
        ctx: The read pass, taken as an argument rather than built here so the
            full-page render shares ONE with the Archived drawer.  The route
            builds it (plan step R7d-d; a producer below the route takes the
            pass and never builds one), and its default ``as_of`` is the
            ``date.today()`` this surface has always measured "now" at.
    """
    income_templates, expense_templates = _load_active_transaction_templates(
        user_id,
    )
    transfer_templates = _load_active_transfer_templates(user_id)
    return recurring_view.build_view(
        income_templates=income_templates,
        expense_templates=expense_templates,
        transfer_templates=transfer_templates,
        ctx=ctx,
    )


@templates_bp.route("/templates")
@require_owner
def list_templates():
    """Render the unified Recurring surface.

    One page for every recurring definition -- income, expense, and
    transfer templates -- replacing the retired ``/transfers`` list and
    ``/obligations`` page.  ``recurring_view.build_view`` produces the
    summary band (the /obligations monthly kernel), the three grouped
    sections with per-section subtotals, and per row the monthly +
    per-paycheck equivalents, engine-backed next date, and share of section
    committed total.  ``show_per_paycheck`` seeds which unit the page-wide
    toggle shows first, read from the user's stored preference.
    """
    user_id = current_user.id
    # ONE read pass for the whole page: the active sections measure every
    # cadence and next date against its schedule and fold every loan payment's
    # stop in its scenario, and so does the Archived drawer.
    ctx = BalanceContext.build(user_id)
    view = _load_recurring_view(user_id, ctx)
    archived_transactions, archived_transfers = _load_archived_rows(
        user_id, ctx,
    )

    settings = current_user.settings
    show_per_paycheck = bool(settings and settings.recurring_show_per_paycheck)

    return render_template(
        "templates/list.html",
        view=view,
        archived_transactions=archived_transactions,
        archived_transfers=archived_transfers,
        show_per_paycheck=show_per_paycheck,
    )


@templates_bp.route("/templates/unit-preference", methods=["POST"])
@require_owner
def set_unit_preference():
    """Persist the Recurring surface's Monthly / Per-paycheck unit choice.

    The page-wide unit toggle POSTs ``unit=monthly`` or
    ``unit=per_paycheck``; the choice is stored on the user's settings so
    it survives across devices and sessions (the producer renders both
    units regardless -- this only sets which one shows first).  Any other
    ``unit`` value is ignored.

    Response shape depends on the caller.  An HTMX toggle (``HX-Request``
    header) gets the re-rendered ``_recurring_body`` fragment in the
    EFFECTIVE unit, swapped live into ``#recurring-body`` -- money is
    formatted once, server-side, so the toggle never recomputes a figure in
    JS.  A plain (no-JS) POST redirects back to the list, which then
    re-renders in the stored unit.

    An unrecognized ``unit`` leaves the stored preference untouched; the
    response still matches the caller (a fragment in the unchanged unit for
    HTMX, a redirect otherwise), so an HTMX request never receives a
    redirect it would follow and swap the whole page into the body.
    """
    unit = request.form.get("unit")
    if unit in (_UNIT_MONTHLY, _UNIT_PER_PAYCHECK):
        settings = current_user.settings
        if settings is None:
            settings = UserSettings(user_id=current_user.id)
            db.session.add(settings)
        settings.recurring_show_per_paycheck = unit == _UNIT_PER_PAYCHECK
        db.session.commit()

    if request.headers.get("HX-Request"):
        settings = current_user.settings
        show_per_paycheck = bool(settings and settings.recurring_show_per_paycheck)
        # The toggle re-renders the body only; the Archived drawer is not part
        # of the swapped fragment, so this path needs no rows for it.
        view = _load_recurring_view(
            current_user.id, BalanceContext.build(current_user.id),
        )
        return render_template(
            "templates/_recurring_body.html",
            view=view,
            show_per_paycheck=show_per_paycheck,
        )
    return redirect(url_for("templates.list_templates"))
