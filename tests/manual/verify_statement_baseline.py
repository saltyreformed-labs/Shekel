"""Dump every figure the two confirmed-ledger STATEMENTS render, for a diff.

The verification standard (``docs/plans/verification.md``) asks of every
harness: **can it SEE the code under test?**  Five exist because each is blind
where the next one looks, and none of them can see this one's subject.
``verify_balance_baseline`` reads the ``balance_at`` seam,
``verify_savings_producers`` and ``verify_anchor_surfaces`` read producers above
it, ``verify_projection_axis`` reads the forward axis, and
``verify_render_surfaces`` reads route status codes and body SIZES -- it cannot
see a figure at all.  The income statement and the balance sheet are read by
none of them, so a change that moves a statement line moves nothing any harness
prints.  Plan step **X-f3d** is exactly such a change: it re-points a balance
assertion's COUNTER leg, which no account balance depends on and both
statements do.

**What it captures, per user:**

* the income statement for EVERY pay period the user has, and for every
  calendar month and year the ledger actually attributes a source into --
  enumerated from the data rather than sampled, per the standard's rule 2;
* every section of each, line by line (label + amount, sorted), plus the
  section totals, net income, and -- where the running code has them -- the
  unrealized section and comprehensive income;
* the balance sheet as of the first and last attribution day, every pay
  period end, and today: sections line by line, and the whole two-part
  tie-out including its raw ledger net.

**It compiles and runs on BOTH sides of plan step X-f3d, and that is a
requirement rather than a nicety.**  A harness that a step's own change makes
uncompilable on HEAD reports every line moved and grades nothing.  So the
report fields that step ADDS are read through :func:`getattr` with a default,
and section lines are keyed by LABEL rather than by ``ledger_account_id`` --
a chart row minted by the new code has an id the old side never had, which
would diff as a moved line even where the money did not move.

**What a clean diff does and does not prove.**  Identical output means no
statement figure moved; it does not mean either figure is right.  X-f3d's own
gate is the reverse: its diff MUST show the counter legs move, and every moved
line is explained in its commit message.  The BALANCE side must stay
byte-identical, which is ``verify_balance_baseline``'s job, not this one's --
the two harnesses together are what say "the meaning moved and the money did
not".

**Usage** (from the repository root)::

    DATABASE_URL=postgresql://.../shekel_xf3d \\
        .venv/bin/python tests/manual/verify_statement_baseline.py before.json
    # ... deploy the change and run the anchor backfill ...
    DATABASE_URL=postgresql://.../shekel_xf3d \\
        .venv/bin/python tests/manual/verify_statement_baseline.py after.json
    diff before.json after.json

For a HEAD-vs-post comparison use ``git worktree add`` for the HEAD side --
never ``git checkout``, which reverts the working tree and discards the very
change under test.

This file is deliberately outside pytest's collection (``pytest.ini`` sets
``python_files = test_*.py``), like its ``verify_*`` siblings here: it needs a
populated database chosen by the operator, not the seeded test template.
"""

import json
import pathlib
import sys
from decimal import Decimal

# Python puts the SCRIPT's own directory on ``sys.path``, not the working
# directory, so ``app`` is not importable when this is run as
# ``.venv/bin/python tests/manual/verify_statement_baseline.py``.  Same
# bootstrap as ``verify_balance_baseline``, for the same reason: the figures
# here are service calls, and going through routes would capture what a
# template rendered rather than what the reader answered.
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2]))

# Pylint: ``wrong-import-position`` -- these must follow the path bootstrap
# above; there is no import order that resolves ``app`` before it runs.
# pylint: disable=wrong-import-position
from app import create_app
from app.extensions import db
from app.models.user import User
from app.services import ledger_report_service
from app.services.ledger_report_service import StatementWindow
from app.services.pay_calendar import calendar_for
from app.services.scenario_resolver import get_baseline_scenario
from app.utils.dates import display_today


def _money(value):
    """Return a JSON-stable string for a Decimal, or None.

    Formatted to two places so a ``Decimal("5.1")`` and a ``Decimal("5.10")``
    -- equal as money, distinct as objects -- cannot show up as a spurious
    diff.  Formatted rather than quantized, which is the form
    ``verify_balance_baseline`` uses and the one ``shekel-bare-money-quantize``
    exists to prefer: a bare ``quantize`` rounds ROUND_HALF_EVEN, and a
    harness that silently rounds is a harness that can hide a cent.
    """
    if value is None:
        return None
    return f"{Decimal(value):.2f}"


def _section(section):
    """Return one :class:`StatementSection` as a JSON-stable blob.

    Lines keyed by LABEL, not by ``ledger_account_id``: a chart row the change
    under test mints carries an id the other side never had, which would diff
    as a moved line even when the money is identical.  The label is what a
    reader actually sees, and it is what a section is already sorted by.
    """
    return {
        "lines": [
            {"label": line.label, "amount": _money(line.amount)}
            for line in section.lines
        ],
        "total": _money(section.total),
    }


def _income_statement(user_id, window):
    """Return one income statement as a JSON-stable blob, or its refusal.

    ``unrealized`` and ``comprehensive_income`` are read through ``getattr``
    so this runs unchanged on the side of plan step X-f3d that has neither:
    absent, they serialise as ``None`` and the field diffs exactly once, at
    the step that adds them, instead of making the file uncomparable.
    """
    report = ledger_report_service.compute_income_statement(user_id, calendar_for(user_id), window)
    unrealized = getattr(report, "unrealized", None)
    return {
        "window_label": report.window_label,
        "income": _section(report.income),
        "expense": _section(report.expense),
        "net_income": _money(report.net_income),
        "unrealized": _section(unrealized) if unrealized is not None else None,
        "comprehensive_income": _money(
            getattr(report, "comprehensive_income", None),
        ),
    }


def _balance_sheet(user_id, as_of):
    """Return one balance sheet as a JSON-stable blob, tie-out included."""
    report = ledger_report_service.compute_balance_sheet(user_id, as_of)
    return {
        "as_of": as_of.isoformat(),
        "assets": _section(report.assets),
        "liabilities": _section(report.liabilities),
        "equity": _section(report.equity),
        "tie_out": {
            "assets": _money(report.tie_out.assets),
            "liabilities_plus_equity": _money(
                report.tie_out.liabilities_plus_equity,
            ),
            "ledger_net": _money(report.tie_out.ledger_net),
            "in_balance": report.tie_out.in_balance,
        },
    }


def _ledger_days(user_id, scenario_id):
    """Return every civil day the ledger could attribute a source into, ascending.

    Read off the DATA rather than guessed from a calendar, so the month and
    year windows below are the ones that hold something and no populated
    window is missed -- the "never a sample" rule applied to window selection.
    Sampling months would have walked straight past the ones plan step X-f3d
    moves.

    Deliberately a raw two-table union rather than a call into the reader's own
    attribution core: this harness must stay a SECOND OPINION about what the
    statements say, and it must not reach into a reader's private module to
    decide what to ask.  It over-covers on purpose -- a settle day with no
    posted effect yields an empty window, which costs one statement and proves
    the window really is empty on both sides.
    """
    rows = db.session.execute(db.text("""
        SELECT DISTINCT je.entry_date AS day
        FROM budget.journal_entries je
        WHERE je.user_id = :user_id AND je.scenario_id = :scenario_id
        UNION
        SELECT DISTINCT t.settled_on
        FROM budget.transactions t
        WHERE t.scenario_id = :scenario_id AND t.settled_on IS NOT NULL
    """), {"user_id": user_id, "scenario_id": scenario_id}).all()
    return sorted(row[0] for row in rows)


def _user_blob(user_id):
    """Return every statement figure for one user, or ``None`` when unreadable.

    A user with no baseline scenario has no ledger to read; both statements
    refuse rather than answering zeros (the fabrication ruling R-CA deleted),
    so this records the refusal as ``None`` instead of catching it -- a user
    who GAINS a baseline between two runs should diff.
    """
    scenario = get_baseline_scenario(user_id)
    if scenario is None:
        return None

    days = _ledger_days(user_id, scenario.id)
    # The owner's calendar, not the ORM rows: a period's last covered day
    # is derived from the payday set since plan step ``pay_calendar:C4-c``
    # dropped the column, and ``saved()`` is where that answer lives.
    periods = calendar_for(user_id).saved()

    income_statements = {}
    for period in periods:
        income_statements[f"period/{period.period_id}"] = _income_statement(
            user_id, StatementWindow(window_type="pay_period",
                                     period_id=period.period_id),
        )
    for year, month in sorted({(day.year, day.month) for day in days}):
        income_statements[f"month/{year}-{month:02d}"] = _income_statement(
            user_id,
            StatementWindow(window_type="month", month=month, year=year),
        )
    for year in sorted({day.year for day in days}):
        income_statements[f"year/{year}"] = _income_statement(
            user_id, StatementWindow(window_type="year", year=year),
        )

    # As-of dates: both ends of the attributed span, every period end, and
    # today.  The ends matter because a cumulative fold is where an
    # off-by-one bound hides; the period ends are the dates the rest of the
    # app reasons in; today is what the screen shows.
    as_of_dates = sorted(
        ({days[0], days[-1]} if days else set())
        | {period.end_date for period in periods}
        | {display_today()}
    )
    return {
        "income_statements": income_statements,
        "balance_sheets": [
            _balance_sheet(user_id, as_of) for as_of in as_of_dates
        ],
    }


def main(out_path):
    """Write every user's statement figures to *out_path* as JSON."""
    app = create_app()
    with app.app_context():
        result = {}
        for user in db.session.query(User).order_by(User.id).all():
            result[f"user{user.id}"] = _user_blob(user.id)

        with open(out_path, "w", encoding="utf-8") as handle:
            json.dump(result, handle, indent=1, sort_keys=True)

    statements = sum(
        len(blob["income_statements"]) + len(blob["balance_sheets"])
        for blob in result.values() if blob is not None
    )
    print(f"wrote {out_path}: {len(result)} users, {statements} statements")


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print(__doc__)
        raise SystemExit(
            "usage: verify_statement_baseline.py <output.json>  "
            "(DATABASE_URL selects the database)"
        )
    main(sys.argv[1])
