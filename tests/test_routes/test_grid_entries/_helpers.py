"""The helpers and constants every part of this package shares: the module-level helpers of
``tests/test_routes/test_grid_entries.py``, moved verbatim by ``tools/split``.
"""

from datetime import date
from decimal import Decimal

from app.extensions import db
from app.models.transaction_entry import TransactionEntry
from app.models.ref import TransactionType
from app.routes._render_helpers import fragment_amounts
from app.services.account_resolver import resolve_owner_cash_flow_set
from app.services.entry_service import build_entry_lists_dict, build_entry_sums_dict
from app.services.pay_calendar import FiledRow, calendar_for

from tests._test_helpers import (
    figure_source_columns,
    generate_row_of,
    make_expense_template,
    one_off_row_of,
)


def _sums(rows):
    """``build_entry_sums_dict`` with the budget map its caller must supply.

    The builder takes ``{transaction_id: resolved amount}`` since plan step
    X-au-c2b -- it read ``txn.estimated_amount``, the COLUMN a derived row does
    not carry -- so every route that renders a cell resolves its rows once and
    hands the map down.  These tests resolve through the app's own single-set
    door (:func:`~app.routes._render_helpers.fragment_amounts`) rather than
    passing a literal, so the figures they assert are the ones the app would
    show.

    Args:
        rows: The rows to aggregate.

    Returns:
        The ``{txn_id: sums}`` mapping.
    """
    budgets = {}
    for row in rows:
        budgets.update(fragment_amounts(row).budgets)
    return build_entry_sums_dict(rows, budgets)


def _lists(rows):
    """``build_entry_lists_dict`` with the same budget map (see :func:`_sums`).

    **The paycheck SPANS come from the owner's own pay calendar** since
    pay-calendar plan step C4-a-3, resolved through ``require_period`` rather
    than assembled from a literal here, so a span read off the stored
    ``end_date`` column does not have to move again when plan step C4-c dropped
    it.

    **It is the same construction TWO of the four call sites use, not four**,
    and saying which is the correction an adversarial review of this step
    made: ``_render_entry_list`` and ``render_mobile_card`` resolve a period
    exactly this way, so for those the map here IS the app's.  ``/grid``
    builds a third construction (``{p.period_id: p for p in ctx.all_periods}``,
    inside ``grid.page._build_entry_maps``) and the companion page a fourth
    (``{view.period.period_id: view.period}``); this helper exercises neither.
    They answer equal ``DerivedPeriod`` values today -- checked -- so it
    cannot HIDE a defect in them, but it does not measure them either.  The
    route-level control for ``/grid``'s own map is
    :meth:`TestGridPageEntrySums.test_grid_page_shows_progress`.

    Args:
        rows: The rows to build entry-list contexts for.

    Returns:
        The ``{txn_id: entry_list_view}`` mapping.
    """
    budgets = {}
    periods = {}
    for row in rows:
        budgets.update(fragment_amounts(row).budgets)
        period = calendar_for(row.pay_period.user_id).require_period(
            FiledRow.for_row(row),
        )
        periods[period.period_id] = period
    # The OWNER's cash-flow set with no override, as every route passes it
    # (plan step credit_card:CC-5-2): every row here is one owner's, and an
    # empty list has no owner (the builder never reads the set for it).
    return build_entry_lists_dict(
        rows, budgets, periods,
        resolve_owner_cash_flow_set(rows[0].user_id) if rows else None,
        # The owner is looking, as on the grid (plan step CC-5-4a-5).
        rows[0].user_id if rows else None,
    )


def _create_tracked_txn(seed_user, seed_periods_today, period_index=0,
                         estimated=Decimal("500.00")):
    """Create a tracked expense transaction backed by a tracking-enabled template.

    The definition is priced at *estimated* and its row is the engine's
    (:func:`generate_row_of`, plan step balance:X-cf-4): DERIVED, so the
    figure a case expects to see rendered is what the row RESOLVES to.

    Returns:
        tuple of (Transaction, TransactionTemplate).
    """
    template = make_expense_template(
        db.session, seed_user, amount=estimated, name="Groceries",
        category_key="Groceries", is_envelope=True,
    )
    txn = generate_row_of(template, seed_periods_today[period_index])
    return txn, template


def _create_plain_txn(seed_user, seed_periods_today, period_index=0,
                       estimated=Decimal("200.00"), name="Test Expense"):
    """Create a non-tracked one-off expense (its definition's ``is_envelope`` False)."""
    expense_type = db.session.query(TransactionType).filter_by(name="Expense").one()

    txn = one_off_row_of(
        seed_periods_today[period_index],
        name=name,
        amount=estimated,
        user_id=seed_periods_today[period_index].user_id,
        account_id=seed_user["account"].id,
        scenario_id=seed_user["scenario"].id,
        transaction_type_id=expense_type.id,
        category_id=seed_user["categories"]["Groceries"].id,
    )
    db.session.flush()
    return txn


def _add_entry(txn, seed_user, amount, is_credit=False,
               description="Purchase", purchased_on=None):
    """Add a purchase entry to a transaction.

    Args:
        txn: The parent transaction.
        seed_user: The seed_user fixture dict.
        amount: The purchase amount.
        is_credit: Whether the purchase was made on a credit card.
        description: The store name / note.
        purchased_on: The day the purchase was made.  **Pass one wherever the
            test asserts anything about the OUT-OF-PERIOD warning**, and pass
            it relative to the row's own payday rather than as a literal: the
            default below is a fixed 2026-04-12 while ``seed_periods_today``
            builds its periods around the CLOCK, so whether that date falls
            inside the row's paycheck is a property of the day the suite runs
            (the weekly clock sweep moves it, ``docs/test-suite-clocks.md``).
            A test that leaves it defaulted may assert the sums but must not
            assert the warning.

    Returns:
        The flushed :class:`TransactionEntry`.
    """
    entry = TransactionEntry(
        **figure_source_columns(),
        transaction_id=txn.id, account_id=txn.account_id, owner_id=txn.user_id,
        user_id=seed_user["user"].id,
        amount=amount,
        description=description,
        purchased_on=purchased_on or date(2026, 4, 12),
        is_credit=is_credit,
    )
    db.session.add(entry)
    db.session.flush()
    return entry
