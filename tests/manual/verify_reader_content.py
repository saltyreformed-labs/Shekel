"""Dump what the dashboard bills, the calendar and the Spending report SAY.

The regression harness for plan step **balance:X-bi-6-1b** (ruling
**R-BAL86**, the leaf after **R-BAL87**'s grid): the three display readers
that still drew a transfer from its SHADOW row in ``budget.transactions`` --
the dashboard's still-due totals and due-soon list, the analytics calendar's
day cells and month totals, and the Spending report's settled spend -- draw it
as a :class:`~app.services.transfer_legs.TransferLeg` read off the parent in
``budget.transfers`` instead.  None of the standing harnesses can see that
change: ``verify_balance_baseline`` walks the ``balance_at`` seam, which this
leaf does not touch; ``verify_render_surfaces`` reads status codes and body
sizes; ``verify_grid_cells`` reads the grid.  Running only those over this
leaf would report "nothing moved" for three surfaces it never read, which is
the free-pass shape ``docs/plans/verification.md`` standard 3 asks about.

It answers *did anything a person reads move*, never *is the answer right*.
The proof that a leg is priced and placed correctly is the suite's controls;
this is the exhaustive regression check beside them, at the SERVICE tier so
the diff is over the figures and labels themselves rather than over HTML.

**It captures CONTENT and deliberately not IDENTITY.**  A transfer's bill,
day entry or surprise is keyed by ``(transfer id, account id)`` after the leaf
where it carried a shadow row's id before, so the identity field of each
shape -- the bill dict's ``id``, ``DayEntry.transaction_id`` /
``DayEntry.item_key``, ``Surprise.transaction_id`` / ``Surprise.item_key`` --
is scrubbed at every depth before the JSON is written.  Every figure, label,
date, flag and count stays.  Two runs whose JSON is byte-identical showed the
same words and the same money on every one of these surfaces, whatever the
items were called.

**Where it is blind, stated so the diff is read correctly.**  The far-leg rule
(``credit_card:R-CC23``) is exercised only where the database holds a transfer
between two members of one cash-flow set; on the 2026-09-20 production restore
there is none, so that arm is the unit tests' alone.  The Spending report's
pay-period window is reachable from no route and is not run.

**Usage** (from the repository root, against the SAME database both times)::

    DATABASE_URL=postgresql://.../shekel_xbi6 \\
        .venv/bin/python tests/manual/verify_reader_content.py before.json
    # ... make the change ...
    DATABASE_URL=postgresql://.../shekel_xbi6 \\
        .venv/bin/python tests/manual/verify_reader_content.py after.json
    diff before.json after.json

It drives EVERY user in the database that has an account.  For each: the
dashboard pulse once (the surface takes no account override); the calendar's
month detail for every account as the balance line over every calendar month
the owner's pay periods touch, plus the year overview per account per year;
and the Spending report for every one of those months and years.
"""

import dataclasses
import json
import pathlib
import sys
from datetime import date, datetime, timezone
from decimal import Decimal

# Python puts the SCRIPT's own directory on ``sys.path``, not the working
# directory, so the repository root is added explicitly -- the same line every
# harness in this directory carries.
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2]))

from app import create_app  # noqa: E402  pylint: disable=wrong-import-position
from app.extensions import db  # noqa: E402  pylint: disable=wrong-import-position
from app.models.account import Account  # noqa: E402  pylint: disable=wrong-import-position
from app.models.pay_period import PayPeriod  # noqa: E402  pylint: disable=wrong-import-position
from app.models.user import User, UserSettings  # noqa: E402  pylint: disable=wrong-import-position
from app.services import (  # noqa: E402  pylint: disable=wrong-import-position
    calendar_service,
    dashboard_service,
    spending_report_service,
)
from app.services.balance_at import BalanceContext  # noqa: E402  pylint: disable=wrong-import-position
from app.utils.dates import to_display_date  # noqa: E402  pylint: disable=wrong-import-position

#: The identity field of each shape the leaf re-keys, dropped at every depth.
_IDENTITY_KEYS = frozenset({"id", "transaction_id", "item_key"})


def _plain(value):
    """Return *value* as JSON-ready plain data, identity fields scrubbed."""
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        return _plain(dataclasses.asdict(value))
    if isinstance(value, dict):
        return {
            str(key): _plain(item)
            for key, item in value.items()
            if key not in _IDENTITY_KEYS
        }
    if isinstance(value, (list, tuple)):
        return [_plain(item) for item in value]
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    return value


def _months_touched(user_id: int) -> list[tuple[int, int]]:
    """Return every ``(year, month)`` the owner's saved pay periods touch."""
    first = (
        db.session.query(db.func.min(PayPeriod.start_date))
        .filter_by(user_id=user_id).scalar()
    )
    last = (
        db.session.query(db.func.max(PayPeriod.start_date))
        .filter_by(user_id=user_id).scalar()
    )
    if first is None:
        return []
    months = []
    year, month = first.year, first.month
    while (year, month) <= (last.year, last.month):
        months.append((year, month))
        year, month = (year + 1, 1) if month == 12 else (year, month + 1)
    return months


def _snapshot_user(user_id: int, today: date) -> dict:
    """Compute every reader for one owner and return its plain content."""
    settings = db.session.query(UserSettings).filter_by(user_id=user_id).first()
    threshold = settings.large_transaction_threshold if settings else 500
    account_ids = [
        row.id for row in
        db.session.query(Account).filter_by(user_id=user_id)
        .order_by(Account.id).all()
    ]
    months = _months_touched(user_id)
    years = sorted({year for year, _ in months})

    out: dict = {"pulse": None, "calendar": {}, "spending": {}}

    section = dashboard_service.resolve_section(BalanceContext.build(user_id))
    pulse = dashboard_service.compute_pulse_section(section)
    if pulse is not None:
        # The hero, chart, trough, peak and street are the balance seam's
        # and the calendar's; the two bill surfaces are this leaf's.
        out["pulse"] = _plain({
            "still_due": pulse["still_due"],
            "due_soon": pulse["due_soon"],
            "due_soon_stations": pulse["due_soon_stations"],
        })

    for account_id in account_ids:
        # The calendar refuses an account outside the owner's cash-flow
        # accounts (a loan, an archived one) with the exception the route
        # turns into a 404; recorded as such, the way ``verify_grid_cells``
        # records a non-200, so a refusal that starts or stops is a diff.
        try:
            for year, month in months:
                key = f"account={account_id} {year}-{month:02d}"
                out["calendar"][key] = _plain(calendar_service.get_month_detail(
                    user_id, year, month, account_id, threshold,
                    today=today, user_settings=settings,
                ))
            for year in years:
                key = f"account={account_id} {year}"
                out["calendar"][key] = _plain(calendar_service.get_year_overview(
                    user_id, year, account_id, threshold, user_settings=settings,
                ))
        except calendar_service.CalendarAccountNotResolvableError:
            out["calendar"][f"account={account_id}"] = {"refused": True}

    for year, month in months:
        window = spending_report_service.SpendingWindow(
            window_type="month", month=month, year=year,
        )
        out["spending"][f"{year}-{month:02d}"] = _plain(
            spending_report_service.compute_spending_report(
                BalanceContext.build(user_id), window, user_settings=settings,
            ),
        )
    for year in years:
        window = spending_report_service.SpendingWindow(
            window_type="year", year=year,
        )
        out["spending"][str(year)] = _plain(
            spending_report_service.compute_spending_report(
                BalanceContext.build(user_id), window, user_settings=settings,
            ),
        )
    return out


def main(out_path):
    """Write the snapshot for every owner to *out_path*."""
    app = create_app()
    snapshot = {}
    with app.app_context():
        today = to_display_date(datetime.now(timezone.utc))
        user_ids = [
            row.id for row in db.session.query(User).order_by(User.id).all()
            if db.session.query(Account).filter_by(user_id=row.id).first()
            is not None
        ]
        for user_id in user_ids:
            snapshot[f"user={user_id}"] = _snapshot_user(user_id, today)
    with open(out_path, "w", encoding="utf-8") as handle:
        json.dump(snapshot, handle, indent=2, sort_keys=True)
    calendars = sum(len(v["calendar"]) for v in snapshot.values())
    spendings = sum(len(v["spending"]) for v in snapshot.values())
    bills = sum(
        len(v["pulse"]["due_soon"]) for v in snapshot.values()
        if v["pulse"] is not None
    )
    entries = sum(
        len(day)
        for v in snapshot.values()
        for view in v["calendar"].values()
        for day in (view.get("day_entries") or {}).values()
    )
    print(
        f"wrote {out_path}: {len(snapshot)} owners, {bills} due-soon bills, "
        f"{calendars} calendar views ({entries} month day entries), "
        f"{spendings} spending reports"
    )


if __name__ == "__main__":
    if len(sys.argv) != 2:
        raise SystemExit("usage: verify_reader_content.py OUT.json")
    main(sys.argv[1])
