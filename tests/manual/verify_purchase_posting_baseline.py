"""X-f3b's oracle: what a cleared purchase becoming a cash posting MOVES.

The seventh manual harness, and it exists because the six before it cannot see
this step.  ``verify_balance_baseline.py`` samples the balance seam and would
call ruling **R-FM** byte-identical wherever an assertion happens to close over
the same day the purchase did; ``verify_statement_baseline.py`` reads the
posting ledger but grades the anchor-correction family.  What X-f3b moves is a
DAY: money the app booked when an envelope closed is booked when the bank took
it, so the difference lives in the narrow window between those two dates and a
coarser sample would report zero (the shape
``docs/plans/lessons.md`` records as a sampling baseline that cannot see a
window change).

So this samples EVERY DAY of a span, per cash account, plus every reported pay
period column, and prints the exact days that move and by how much.  It also
counted the stored ledger's entries by source kind and its trial balance,
because a balance that did not move was only half X-f3b's claim: the postings
had to exist and the trial balance still close.  That half went at plan task
plan#3080, the stored ledger's writers having gone (plan task plan#3133), and
an older snapshot's two ledger keys are no longer read by ``--diff``.

**Run it on BOTH sides, and the two sides differ by CHECKOUT, not by database.**
The BEFORE side must execute the OLD CODE against the OLD schema; running both
sides from this worktree reports ``byte-identical over 215 days`` and hides the
very figure the harness exists to show, which is
``lessons.md``'s "a harness must run on both sides" in its most literal form.
It imports nothing this step added, so it compiles under ``dev`` as well:

    # BEFORE -- the dev checkout, against its own clone at dev's migration head
    DATABASE_URL=postgresql://.../shekel_xf3b_base \\
        PYTHONPATH=/path/to/Shekel /path/to/.venv/bin/python \\
        tests/manual/verify_purchase_posting_baseline.py before.json

    # AFTER -- this worktree, against its own clone at this branch's head
    DATABASE_URL=postgresql://.../shekel_xf3b \\
        .venv/bin/python tests/manual/verify_purchase_posting_baseline.py after.json

then ``--diff before.json after.json`` to print what moved.  Each side needs its
OWN clone of the same production dump for the same reason: the two schemas differ
by one column, so a shared database would be migrated out from under whichever
reader ran first.

Read-only: it opens a session, folds, and never writes.
"""

from __future__ import annotations

import json
import sys
from datetime import date, timedelta
from decimal import Decimal

from app import create_app
from app.extensions import db
from app.models.account import Account
from app.services import balance_at
from app.services.balance_at import BalanceContext
from app.services.account_projection import (
    AccountProjectionKind,
    classify_account,
)

# The span the developer's own data lives in, widened a month either side of
# the first assertion and today so a movement at either edge is visible rather
# than clipped.
_FROM = date(2026, 3, 1)
_TO = date(2026, 10, 1)


def _cash_accounts():
    """Return every NON-loan account, which is what the cash fold answers for."""
    return [
        account for account in db.session.query(Account)
        .order_by(Account.id)
        .all()
        if classify_account(account) is not AccountProjectionKind.AMORTIZING
    ]


def _days():
    """Return every civil day of the sampled span, inclusive."""
    span = (_TO - _FROM).days
    return [_FROM + timedelta(days=offset) for offset in range(span + 1)]


def snapshot():
    """Return the whole measurement as JSON-safe plain data.

    Every account is read through the seam's own public scalar
    (``balance_at.cash_balance_at``) rather than through the fold beneath it,
    so what is graded is the figure a SCREEN gets.  One read pass per account
    per day is slow and is the point: a pass shared across days could not tell
    a per-day movement from a memoised one.
    """
    days = _days()
    accounts = {}
    for account in _cash_accounts():
        ctx = BalanceContext.build(account.user_id)
        accounts[str(account.id)] = {
            "name": account.name,
            "daily": {
                day.isoformat(): str(
                    balance_at.cash_balance_at(account, ctx, day)
                )
                for day in days
            },
        }
    return {"accounts": accounts}


def _diff(before_path, after_path):
    """Print every day whose balance moved, per account."""
    with open(before_path, encoding="utf-8") as handle:
        before = json.load(handle)
    with open(after_path, encoding="utf-8") as handle:
        after = json.load(handle)

    moved_total = 0
    for account_id, after_account in sorted(after["accounts"].items()):
        before_account = before["accounts"].get(account_id)
        if before_account is None:
            print(f"account {account_id}: absent from the BEFORE snapshot")
            continue
        moves = [
            (day, before_account["daily"][day], balance)
            for day, balance in sorted(after_account["daily"].items())
            if before_account["daily"].get(day) != balance
        ]
        moved_total += len(moves)
        label = f"{after_account['name']} (id {account_id})"
        if not moves:
            print(f"{label}: byte-identical over {len(after_account['daily'])} days")
            continue
        print(f"{label}: {len(moves)} of {len(after_account['daily'])} days move")
        for day, was, now in moves:
            delta = Decimal(now) - Decimal(was)
            print(f"    {day}  {was:>12}  ->  {now:>12}   ({delta:+})")

    print()
    print(f"days moved, all accounts: {moved_total}")


def main():
    """Write a snapshot, or diff two of them."""
    if len(sys.argv) == 4 and sys.argv[1] == "--diff":
        _diff(sys.argv[2], sys.argv[3])
        return
    if len(sys.argv) != 2:
        print(__doc__)
        sys.exit(2)
    app = create_app()
    with app.app_context():
        data = snapshot()
    with open(sys.argv[1], "w", encoding="utf-8") as handle:
        json.dump(data, handle, indent=1, sort_keys=True)
    print(f"wrote {sys.argv[1]}")


if __name__ == "__main__":
    main()
