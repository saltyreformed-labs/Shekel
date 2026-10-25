"""Report what an owner's payroll is modelled to put into each account.

Plan step **salary:R14-b** (shipped at ``e0f0c05f``),
findings **D45** and **N-532**.  The step re-points the modelled
contribution feed off its own arithmetic and onto the paycheck engine's
per-period breakdown (ruling **R-SAL2**), which **MOVES MONEY**: this is the
harness that says how much, on real data, through the app's own doors.

**It is a CROSS-TREE probe, and every line of it compiles on both sides.**  A
before/after harness that imports the code under test can only run on one of
the two trees, so this one imports none of it: it reads three PUBLIC seam
entries -- :func:`app.services.balance_at.balance_map`,
:func:`app.services.balance_at.investment_growth_since_anchor` and
:meth:`~app.services.balance_at.BalanceContext.reported_periods` -- whose
signatures are identical on ``origin/dev`` and on this branch.  Run it in a
worktree at the base commit, run it here, and diff the two reports.  Nothing
in it names ``AccountPayrollFeed``, ``adapt_deductions`` or
``get_current_gross_biweekly``, so neither run has to be adjusted for the
other.

**It is READ-ONLY.**  It opens one :class:`~app.services.balance_at
.BalanceContext` per owner and asks it questions; nothing is assigned to an
ORM attribute and nothing is committed.

**Point it at a THROWAWAY CLONE, never at the runtime database.**  The seam
issues no writes, but a clone is what makes the two runs comparable: the
report is a function of the owner's calendar, profiles and deductions, and a
figure measured against a database another session can move is not a
measurement.

**Usage** (from the repository root)::

    DATABASE_URL=postgresql://shekel_user:...@127.0.0.1:5432/shekel_r14b \\
        .venv/bin/python tests/manual/measure_payroll_contribution_feed.py \\
            --user 1 --json after.json

Then, in a worktree at the base commit, the same command with
``--json before.json``, and ``--compare before.json`` here to print the deltas.
"""

import argparse
import json
import logging
import sys
from decimal import Decimal

from app import create_app
from app.models.account import Account
from app.services import balance_at
from app.services.account_projection import (
    AccountProjectionKind,
    classify_account,
)
from app.services.balance_at import BalanceContext

logger = logging.getLogger(__name__)

ZERO = Decimal("0")


def _investment_accounts(user_id):
    """Return the owner's accounts that can model a payroll contribution.

    Membership is the canonical classifier's, never elimination: only an
    account :func:`~app.services.account_projection.classify_account` marks
    INVESTMENT has a contribution tier at all, so a Property carrying its own
    params is correctly out.

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


def _measure(user_id):
    """Return the per-account report for one owner.

    Three figures per account, each read at the LAST saved period so the
    report covers the whole schedule rather than a window a caller chose:

    * ``contributed`` -- the modelled contribution the replay has applied
      since the account's anchor, which is the figure this step moves.
    * ``growth`` -- the accrual beside it, reported because a contribution
      that lands earlier also earns longer, so a delta in the first shows up
      in the second and a report of one alone would understate the change.
    * ``balance`` -- what the grid shows for that period, which is the two
      tiers plus the cash fold and is what the owner actually reads.

    Args:
        user_id: The owner to measure.

    Returns:
        A JSON-safe dict: the owner, the window, and a list of per-account
        records with the three figures as strings (a float would launder the
        cents this exists to count).
    """
    ctx = BalanceContext.build(user_id)
    periods = ctx.reported_periods()
    if not periods:
        return {"user_id": user_id, "periods": 0, "accounts": []}

    last = periods[-1]
    accounts = []
    for account in _investment_accounts(user_id):
        balances = balance_at.balance_map(account, ctx)
        # ``None`` is the chip-hidden state (no period follows the anchor),
        # and it is reported as such rather than coerced to zero: a hidden
        # chip and a $0.00 chip are different answers, and a harness that
        # flattened them would show a move where there was none.
        growth = balance_at.investment_growth_since_anchor(account, ctx, last)
        accounts.append({
            "account_id": account.id,
            "name": account.name,
            "balance": str(balances[last.period_id]),
            "growth": None if growth is None else str(growth[0]),
            "contributed": None if growth is None else str(growth[1]),
        })
    return {
        "user_id": user_id,
        "periods": len(periods),
        "window": [periods[0].start_date.isoformat(),
                   last.start_date.isoformat()],
        "accounts": accounts,
    }


def _print_report(report):
    """Print one run's report as a table.

    Args:
        report: The dict :func:`_measure` returned.
    """
    print(f"user {report['user_id']}: {report['periods']} saved periods"
          + (f" {report['window'][0]}..{report['window'][1]}"
             if report["periods"] else ""))
    if not report["accounts"]:
        print("  no INVESTMENT account models a contribution")
        return
    print(f"  {'account':<34} {'contributed':>14} {'growth':>12}"
          f" {'balance':>16}")
    for row in report["accounts"]:
        print(f"  {row['name'][:34]:<34} {row['contributed'] or '--':>14}"
              f" {row['growth'] or '--':>12} {row['balance']:>16}")


def _print_comparison(before, after):
    """Print the per-account deltas between two runs.

    Args:
        before: The report from the base-commit tree.
        after: This tree's report.

    Returns:
        ``True`` when every account is accounted for in both runs, ``False``
        when the two sets differ -- which makes the deltas meaningless and is
        reported rather than silently partial.
    """
    before_by_id = {row["account_id"]: row for row in before["accounts"]}
    after_by_id = {row["account_id"]: row for row in after["accounts"]}
    if set(before_by_id) != set(after_by_id):
        print("REFUSED: the two runs cover different accounts "
              f"({sorted(before_by_id)} against {sorted(after_by_id)}); "
              "they are not measuring the same owner's data")
        return False

    print(f"\n  {'account':<30} {'contributed':>26} {'balance':>26}")
    total = ZERO
    for account_id, after_row in after_by_id.items():
        before_row = before_by_id[account_id]
        pairs = []
        for key in ("contributed", "balance"):
            if before_row[key] is None or after_row[key] is None:
                pairs.append(f"{'--':>26}")
                continue
            delta = Decimal(after_row[key]) - Decimal(before_row[key])
            if key == "contributed":
                total += delta
            pairs.append(
                f"{before_row[key]:>11} -> {after_row[key]:>11}"
                if delta else f"{'unchanged':>26}"
            )
        print(f"  {after_row['name'][:30]:<30} {pairs[0]} {pairs[1]}")
    print(f"\n  TOTAL modelled contribution moved: {total:+}")
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
