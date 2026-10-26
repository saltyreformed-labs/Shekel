"""Report what the three forward-projecting surfaces say past the saved calendar.

Plan step **salary:S3-e-2** (shipped at ``a6af5b3c``;
rulings **R-SAL15**, **R-SAL18**).  The step deletes
``AccountPayrollFeed``'s hold -- the figure the feed INVENTED for a payday
past the owner's saved schedule -- and prices such a payday through the
paycheck engine on demand, which **MOVES MONEY** on every surface whose axis
runs past the horizon: ``/investment``'s growth chart, ``/savings``'s Horizon
band and ``/retirement``'s projected balances.  This is the harness that says
how much, on real data, through the app's own doors.

**It is a CROSS-TREE probe, and every line of it compiles on both sides.**  A
before/after harness that imports the code under test can only run on one of
the two trees, so this one imports none of it: it reads four PUBLIC entries
whose signatures are identical on ``origin/dev`` and on this branch --
:func:`app.services.investment_dashboard_service.compute_growth_chart_data`,
:func:`app.services.savings_dashboard_service.compute_dashboard_data`,
:func:`app.services.retirement_plan.load_retirement_inputs` /
:func:`~app.services.retirement_plan.picture_at`, and
:func:`app.services.balance_at.balance_map` as the CONTROL that must NOT
move (the balance seam reports over the saved window only, where the feed's
answer is the engine's on both trees).  Nothing in it names
``AccountPayrollFeed``, ``salary_basis``, ``build_employer_salary_basis`` or
``saved_through``, so neither run has to be adjusted for the other.

**It is READ-ONLY.**  It opens read passes and asks them questions; nothing is
assigned to an ORM attribute and nothing is committed.

**Point it at a THROWAWAY CLONE, never at the runtime database**, and ARM the
employee path first.  On the developer's own data no deduction carries a
``target_account_id``, so the employee half of the feed prices ``$0.00`` on
every payday and a before/after run over that data grades the employer half
alone; a second clone with one deduction wired onto an investment account is
what makes both halves live.  The report says which surfaces it measured and
over how many periods, so a figure that could not have moved is visible as
such rather than read as "unchanged".

**Usage** (from the repository root)::

    DATABASE_URL=postgresql://shekel_user:...@127.0.0.1:5432/shekel_s3e2 \\
        .venv/bin/python tests/manual/measure_payroll_feed_on_demand.py \\
            --user 1 --json after.json

Then, in a worktree at the base commit, the same command with
``--json before.json``, and ``--compare before.json`` here to print the deltas.
"""

import argparse
import json
import logging
import sys
from dataclasses import replace
from decimal import Decimal

from app import create_app
from app.models.account import Account
from app.services import balance_at, retirement_plan
from app.services.account_projection import (
    AccountProjectionKind,
    classify_account,
)
from app.services.balance_at import BalanceContext
from app.services.investment_dashboard_service import (
    compute_growth_chart_data,
)
from app.services.savings_dashboard_service import compute_dashboard_data

logger = logging.getLogger(__name__)

ZERO = Decimal("0")

#: The ``/investment`` chart horizons measured, in years.  One year sits
#: inside the saved calendar on the developer's data (63 paydays reach
#: 2028-08), ten runs well past it, forty is the slider's maximum.
CHART_HORIZONS = (1, 10, 40)

#: The ``/retirement`` plan points measured: the stored plan, and the two
#: retire-later probes the lever card asks (``retirement_levers``'s +180 is
#: its ``_MAX_DELAY_MONTHS``).
PLAN_OFFSETS = (0, 60, 180)


def _investment_accounts(user_id):
    """Return the owner's accounts that can model a payroll contribution.

    Membership is the canonical classifier's, never elimination: only an
    account :func:`~app.services.account_projection.classify_account` marks
    INVESTMENT has a contribution tier at all.

    Args:
        user_id: The owner.

    Returns:
        The account rows, id ascending.
    """
    from app.extensions import db
    return [
        account
        for account in (
            db.session.query(Account)
            .filter(Account.user_id == user_id, Account.is_active.is_(True))
            .order_by(Account.id)
            .all()
        )
        if classify_account(account) is AccountProjectionKind.INVESTMENT
    ]


def _money(value):
    """Return *value* as the string the report stores, or ``None``."""
    return None if value is None else str(Decimal(str(value)))


def _measure_investment(user_id, accounts, figures, notes):
    """Read each account's growth chart at every horizon in CHART_HORIZONS."""
    for account in accounts:
        for years in CHART_HORIZONS:
            chart = compute_growth_chart_data(user_id, account, years, None)
            key = f"investment/{account.id}/{years}y"
            figures[f"{key}/projection_end"] = _money(chart["projection_end"])
            figures[f"{key}/contributions_end"] = (
                chart["chart_contributions"][-1]
                if chart["chart_contributions"] else None
            )
            notes[key] = f"{len(chart['chart_balances'])} periods"


def _measure_savings(user_id, figures, notes):
    """Read the /savings Horizon band's end-of-domain composition."""
    data = compute_dashboard_data(BalanceContext.build(user_id))
    horizon = data["net_worth"].horizon
    if horizon is None:
        notes["savings/horizon"] = "no horizon (no projectable account)"
        return
    for band, series in horizon["composition"].items():
        figures[f"savings/horizon/{band}_end"] = _money(series[-1])
    figures["savings/horizon/net_end"] = _money(horizon["net"][-1])
    notes["savings/horizon"] = (
        f"{len(horizon['dates'])} annual samples to {horizon['dates'][-1]}"
    )


def _measure_retirement(user_id, figures, notes):
    """Read /retirement's picture at the stored plan and two probe points."""
    inputs = retirement_plan.load_retirement_inputs(
        BalanceContext.build(user_id),
    )
    for months in PLAN_OFFSETS:
        point = replace(inputs.stored_plan, month_offset=months)
        picture = retirement_plan.picture_at(inputs, point)
        key = f"retirement/+{months}m"
        notes[key] = (
            f"{len(picture.axis)} periods to {picture.retirement_date}"
        )
        for projection in picture.projections:
            account = projection["account"]
            figures[f"{key}/{account.id}/projected_balance"] = _money(
                projection["projected_balance"],
            )
            figures[f"{key}/{account.id}/employer_per_period"] = _money(
                projection["employer_per_period"],
            )
            figures[f"{key}/{account.id}/employee_per_period"] = _money(
                projection["employee_per_period"],
            )
        figures[f"{key}/net/projected_total_savings"] = _money(
            picture.net.projected_total_savings,
        )
        figures[f"{key}/net/required_retirement_savings"] = _money(
            picture.net.required_retirement_savings,
        )


def _measure_control(user_id, accounts, figures, notes):
    """Read the balance seam at the saved window's last period: the CONTROL."""
    ctx = BalanceContext.build(user_id)
    periods = ctx.reported_periods()
    if not periods:
        notes["control/balance_map"] = "no saved periods"
        return
    last = periods[-1]
    notes["control/balance_map"] = (
        f"{len(periods)} saved periods, last opens {last.start_date}"
    )
    for account in accounts:
        by_period = balance_at.balance_map(account, ctx)
        figures[f"control/{account.id}/balance_at_last_saved"] = _money(
            by_period[last.period_id],
        )


def _basis(user_id):
    """Return what a comparison must hold FIXED: the clock and the database.

    Every door below opens a read pass on ``date.today()``, and the axes it
    projects over open at that day's period -- so a before run and an after
    run on different days are not the same measurement, and neither are two
    runs against two databases.  Recorded here so :func:`_print_comparison`
    can REFUSE such a pair instead of printing deltas that mix the mechanism
    under test with a payday boundary crossed between the runs (an
    adversarial review of plan step salary:S3-e-2 asked for this).
    """
    from app.extensions import db
    return {
        "as_of": BalanceContext.build(user_id).as_of.isoformat(),
        "database": db.engine.url.database,
    }


def _measure(user_id):
    """Build the report for *user_id*.

    Args:
        user_id: The owner to measure.

    Returns:
        ``{"user_id", "basis": {"as_of", "database"},
        "figures": {name: str | None}, "notes": {name: str}}``.
    """
    accounts = _investment_accounts(user_id)
    figures: "dict[str, str | None]" = {}
    notes: "dict[str, str]" = {}
    notes["accounts"] = ", ".join(
        f"{account.id}={account.name}" for account in accounts
    ) or "none"
    _measure_investment(user_id, accounts, figures, notes)
    _measure_savings(user_id, figures, notes)
    _measure_retirement(user_id, figures, notes)
    _measure_control(user_id, accounts, figures, notes)
    return {
        "user_id": user_id, "basis": _basis(user_id),
        "figures": figures, "notes": notes,
    }


def _print_report(report):
    """Print one run's figures and notes."""
    print(f"user {report['user_id']}  as_of {report['basis']['as_of']}  "
          f"database {report['basis']['database']}")
    for name, note in report["notes"].items():
        print(f"  [{name}] {note}")
    print()
    for name, value in report["figures"].items():
        print(f"  {name:<52} {value if value is not None else '--':>16}")


def _print_comparison(before, after):
    """Print every figure that moved between two runs, and the count that did not.

    Args:
        before: The report from the base-commit tree.
        after: This tree's report.

    Returns:
        ``True`` when both runs share a basis and report the same figure
        set, ``False`` when they do not -- which makes the deltas meaningless
        and is reported rather than silently partial.
    """
    if before["basis"] != after["basis"]:
        print("REFUSED: the two runs are not the same measurement "
              f"(before {before['basis']}, after {after['basis']}); a "
              "different day moves every axis and a different database "
              "moves every input")
        return False
    if set(before["figures"]) != set(after["figures"]):
        only_before = sorted(set(before["figures"]) - set(after["figures"]))
        only_after = sorted(set(after["figures"]) - set(before["figures"]))
        print("REFUSED: the two runs report different figure sets "
              f"(only before: {only_before}; only after: {only_after})")
        return False
    unchanged = 0
    print(f"\n  {'figure':<52} {'before':>16} {'after':>16} {'delta':>16}")
    for name, after_value in after["figures"].items():
        before_value = before["figures"][name]
        if before_value == after_value:
            unchanged += 1
            continue
        delta = (
            Decimal(after_value) - Decimal(before_value)
            if before_value is not None and after_value is not None
            else "--"
        )
        print(f"  {name:<52} {before_value or '--':>16} "
              f"{after_value or '--':>16} {delta:>+16}")
    print(f"\n  {unchanged} of {len(after['figures'])} figures unchanged")
    return True


def main(argv=None):
    """Run the measurement, and optionally the comparison.

    Args:
        argv: Command-line arguments, or ``None`` for ``sys.argv``.

    Returns:
        The process exit code: ``0`` on a clean run, ``1`` when a comparison
        was asked for and refused.
    """
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--user", type=int, required=True,
                        help="the owner to measure")
    parser.add_argument("--json", help="write this run's report here")
    parser.add_argument("--compare",
                        help="a report from the base-commit tree to diff against")
    args = parser.parse_args(argv)

    app = create_app()
    with app.app_context():
        report = _measure(args.user)

    _print_report(report)
    if args.json:
        with open(args.json, "w", encoding="utf-8") as handle:
            json.dump(report, handle, indent=2)
        print(f"\nwrote {args.json}")

    if args.compare:
        with open(args.compare, encoding="utf-8") as handle:
            before = json.load(handle)
        if not _print_comparison(before, report):
            return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
