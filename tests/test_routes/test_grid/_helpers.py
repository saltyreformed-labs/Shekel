"""The helpers and constants every part of this package shares: the module-level helpers of
``tests/test_routes/test_grid.py``, moved verbatim by ``tools/split``.
"""

from datetime import date
from pathlib import Path
from decimal import Decimal

from app.extensions import db
from app.models.ref import TransactionType
from app.services import balance_at
from app.services.balance_at import BalanceContext
from app.services.pay_calendar import DerivedPeriod
from app.services.generation_schedule import GenerationSchedule

from tests._test_helpers import one_off_row_of


#: Where the grid route's source lives, for the three STATIC guards below.
#: It was the single file ``app/routes/grid.py`` until plan step C2-f2b split
#: it into a package (991 of pylint's 1000-line ceiling).
_GRID_ROUTE_PACKAGE = Path("app/routes/grid")


def _grid_route_source() -> str:
    """Return every module of the grid route package, concatenated.

    The three static guards in this file read the route's own source to
    refuse a shape rather than a behaviour -- an inline subtotal loop, a
    producer swap, a direct read of a canonical producer's source column.
    They each read ``app/routes/grid.py`` by name until plan step C2-f2b
    made that file a package.

    **It globs the DIRECTORY rather than naming the modules**, and that is
    what keeps the guards from weakening as the package grows: a named list
    would let the next module added to it carry the very line these refuse,
    and every guard would still pass.  Sorted so a failure message's line
    ordering is stable across filesystems.
    """
    return "\n".join(
        module.read_text(encoding="utf-8")
        for module in sorted(_GRID_ROUTE_PACKAGE.glob("*.py"))
    )


def _generate_first_row(template, seed_user, periods):
    """Generate *template*'s rows and return the first, committed.

    The tests below need a row a DEFINITION produced rather than one built by
    hand: only a generated row carries the ``amount_source_id`` declaration
    plan step balance:X-au-e's refusal is keyed on, so a hand-built row would
    grade the gate against a shape the app never writes.
    """
    from app.services import recurrence_engine  # pylint: disable=import-outside-toplevel

    created = recurrence_engine.generate_for_template(
        template,
        GenerationSchedule.for_period_ids(
            BalanceContext.build(template.user_id), {p.id for p in periods},
        ),
        seed_user["scenario"].id,
    )
    db.session.commit()
    return created[0] if created else None


class _TestTransactionCRUDMembers:
    """Tests for transaction create, update, delete, and status changes."""

    def _create_test_txn(self, seed_user, seed_periods_today, *, name="Test Expense"):
        """Helper: place and return a projected one-off called *name*."""
        expense_type = db.session.query(TransactionType).filter_by(name="Expense").one()

        txn = one_off_row_of(
            seed_periods_today[0],
            name=name,
            amount=Decimal("123.45"),
            user_id=seed_periods_today[0].user_id,
            account_id=seed_user["account"].id,
            scenario_id=seed_user["scenario"].id,
            transaction_type_id=expense_type.id,
            category_id=seed_user["categories"]["Groceries"].id,
        )
        db.session.commit()
        return txn


def _summary_periods():
    """Two periods carrying what the summary templates read.

    REAL :class:`~app.services.pay_calendar.DerivedPeriod` values rather than
    ``SimpleNamespace`` stand-ins since plan step C2-f2b, which is the type the
    grid's templates now receive.  A stand-in cannot go stale against the type
    it imitates -- it keeps answering the attribute the template stopped asking
    for -- and these render tests are the only proof several of these rows have.
    """
    return [
        DerivedPeriod(
            period_id=101, period_index=10,
            start_date=date(2026, 6, 1), end_date=date(2026, 6, 14),
            end_is_projected=False,
        ),
        DerivedPeriod(
            period_id=102, period_index=11,
            start_date=date(2026, 6, 15), end_date=date(2026, 6, 28),
            end_is_projected=False,
        ),
    ]


def _summary_columns(
    periods, *, elsewhere="0.00", period_timing="0.00", book_vs_bank="0.00",
    contribution="0.00", accrual="0.00",
):
    """Return one hand-built GridColumn per period, all carrying the same figures.

    The three conditional rows are graded on hand-built columns because a
    producer cannot reach every arm of the render at every step: plan step
    X-c2b1 could not put a figure in "Timing & true-ups" at all, and X-g3a
    cannot put one in "Contributions" for ANY fixture -- the grid's kind gate
    admits only INTEREST accounts and ``contribution_events`` returns ``[]``
    for every kind but INVESTMENT.  A row whose template nobody ever executed
    would arrive at the cutover unproven, and the cutover is the commit where
    money moves.
    """
    return {
        period.period_id: balance_at.GridColumn(
            balance=Decimal("3000.00"),
            income=Decimal("2400.00"),
            expense=Decimal("1450.00"),
            net=Decimal("950.00"),
            elsewhere=Decimal(elsewhere),
            period_timing=Decimal(period_timing),
            book_vs_bank=Decimal(book_vs_bank),
            contribution=Decimal(contribution),
            accrual=Decimal(accrual),
        )
        for period in periods
    }


def _row_flags(
    *, elsewhere=False, period_timing=False, book_vs_bank=False,
    contribution=False, accrual=False,
):
    """Return GridRowFlags with every arm explicit (no defaulted visibility).

    The single ``reconciliation`` arm became two at plan step S1-c (ruling
    R-DH (f)) and they are separate PARAMETERS rather than one value fanned
    out, because R-O's visibility question is now asked of each row
    independently -- and the render tests below have to be able to turn one on
    with the other off.
    """
    return balance_at.GridRowFlags(
        elsewhere=elsewhere,
        period_timing=period_timing,
        book_vs_bank=book_vs_bank,
        contribution=contribution,
        accrual=accrual,
    )


def _render_grid_footer(app, periods, columns, flags, accrual_label="Interest"):
    """Render the desktop ``<tfoot>`` partial against a hand-built context."""
    template = app.jinja_env.get_template("grid/_balance_row.html")
    with app.test_request_context("/"):
        return template.render(
            periods=periods,
            columns=columns,
            row_flags=flags,
            accrual_label=accrual_label,
            account=None,
            num_periods=len(periods),
            start_offset=0,
            low_balance_threshold=500,
        )


def _render_mobile_card(app, period, columns, flags, accrual_label="Interest"):
    """Render the mobile "This Period" summary against a hand-built context."""
    template = app.jinja_env.get_template("grid/_mobile_tp_summary.html")
    with app.test_request_context("/"):
        return template.render(
            period=period,
            columns=columns,
            period_row_flags=flags,
            accrual_label=accrual_label,
            account=None,
            oob=False,
        )


def _render_plan_recap(app, period, columns, flags, accrual_label="Interest"):
    """Render the mobile Plan recap against a hand-built context."""
    template = app.jinja_env.get_template("grid/_mobile_plan.html")
    with app.test_request_context("/"):
        return template.render(
            plan_periods=[period],
            plan_income_row_keys=[],
            plan_expense_row_keys=[],
            plan_matched_by_row_period={},
            plan_columns=columns,
            plan_row_flags=flags,
            accrual_label=accrual_label,
            low_balance_threshold=500,
        )
