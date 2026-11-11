"""Dump every loan figure on EVERY day, for a HEAD-vs-post diff.

The companion to :mod:`verify_balance_baseline`, and it exists because that one
is BLIND to a whole class of change by construction.  The baseline reads at the
seam's default ``as_of`` plus six fixed valuation dates; a change whose answer
differs only on the days BETWEEN two events -- a pay period opening and its
payment's cash leaving, an anchor's date and the next assertion -- lives in
windows of one to twelve days, and six samples miss them.  Plan step **X-an-a**
measured that: the baseline came back byte-identical over 9 accounts, 427 grid
cells and 5,978 daily points while a ``$1,910.95`` mortgage installment was
missing from the loan's amortization schedule for twelve days.

So this walks EVERY day of a span and, per day and per loan account, records what
the two producers answer:

* the balance seam (:func:`app.services.balance_at.positions`) at the read date
  AND at three horizons past it, so the FOLD (the past branch) and the PLAN (the
  forward branch) are both sampled -- the plan is fed by the resolver and would
  otherwise be measured only at today;
* the seam's forward plan (``loan_installments`` / ``loan_payoff_date``): its
  length, the first installment's date (the "next payment due" a page shows),
  the payoff date, and the interest still to come -- and beside it the
  resolver-derived schedule (the confirmed history plus the contract's
  forward): its length and its life-of-loan interest.

**It answers "did anything move", never "is the answer right"** -- the same
contract the baseline states.  A step's PROOF is its firing controls and its
hand-computed oracles; this is the regression check beside them.  What it adds is
the AXIS: run it before a change and after, on the same database, and every day
on which any loan figure moved is named.

**Usage** (from the repository root; the HEAD side needs a worktree, never a
``git checkout``, which would revert the very change under test)::

    git worktree add /tmp/head-wt dev
    export DATABASE_URL=postgresql://.../shekel_f3_final
    REPO_ROOT=/tmp/head-wt .venv/bin/python \\
        tests/manual/verify_loan_daily_figures.py before.json
    REPO_ROOT=$PWD .venv/bin/python \\
        tests/manual/verify_loan_daily_figures.py after.json
    diff before.json after.json

``REPO_ROOT`` is read from the environment rather than derived from
``__file__`` deliberately: ONE copy of this script must be able to drive both
trees, and a ``sys.path`` built from its own location silently measures the
working tree twice (hit on the first X-an-a run, which reported a byte-identical
diff because both sides imported the same ``app``).

The span defaults to the current year and is overridable, because the interesting
window is wherever the account's own events sit -- not a fixed calendar.

Outside pytest's collection (``pytest.ini`` sets ``python_files = test_*.py``),
like its ``verify_*`` siblings: it needs a populated database chosen by the
operator, not the seeded test template.
"""

import json
import os
import pathlib
import sys
from datetime import date, timedelta
from decimal import Decimal

# The repository root comes from the environment so the SAME file can be run
# against a HEAD worktree and against the working tree (see the module
# docstring).  It must precede the imports below, which is why they follow it.
sys.path.insert(0, os.environ.get("REPO_ROOT", os.getcwd()))

# Pylint: ``wrong-import-position`` -- these must follow the path bootstrap
# above; there is no import order that resolves ``app`` before it runs.
# pylint: disable=wrong-import-position
from app import create_app
from app.extensions import db
from app.models.account import Account
from app.models.loan_params import LoanParams
from app.services import (
    balance_at,
    loan_loaders,
    loan_payment_service,
    loan_resolver,
)

#: Horizons sampled from each read date.  The first is the read date itself (the
#: seam's FOLD branch); the rest cross into the PLAN branch, which the resolver
#: feeds and which a read-date-only sample never exercises.
_HORIZON_OFFSETS = (0, 90, 365, 1095)

_ZERO = Decimal("0.00")


def _figures(account: Account, day: date) -> dict:
    """Return every loan figure for *account* as read on *day*.

    Args:
        account: The loan account to value.
        day: The read date -- the ``BalanceContext``'s ``as_of``.

    Returns:
        A JSON-safe dict of the seam's balances (at *day* and each horizon past
        it), its plan figures and the resolver-derived schedule figures.
    """
    ctx = balance_at.BalanceContext.build(account.user_id, as_of=day)
    horizon = [day + timedelta(days=offset) for offset in _HORIZON_OFFSETS]
    sampled = balance_at.positions(account, ctx, horizon)
    # Built from the PUBLIC loaders rather than through the seam's own
    # ``resolved_loan``, which is re-exported nowhere and which W9910 protects
    # in every import spelling (plan step D-ctx / E1d-a).  This is the same
    # bundle any out-of-cluster consumer assembles, so measuring it measures
    # what a consumer can actually reach.
    params = loan_loaders.load_loan_params(account.id)
    context = loan_payment_service.load_loan_context(account.id)
    scenarios = loan_resolver.compute_payoff_scenarios(
        loan_inputs=loan_resolver.LoanInputs(
            params,
            loan_loaders.load_loan_anchor_facts(params),
            context.rate_changes,
        ),
        confirmed_view=balance_at.confirmed_view(account, ctx),
    )
    # The loan's PLAN is the seam's forward fold since plan step R7d-g-3
    # (ruling R-R88): the composer keeps the confirmed history and the
    # contract's forward, and what the loan is projected to pay -- the first
    # installment ahead, the payoff, the interest still to come -- is read off
    # ``loan_installments`` / ``loan_payoff_date``, the way the loan page does.
    installments = balance_at.loan_installments(account, ctx)
    payoff = balance_at.loan_payoff_date(account, ctx)
    # The resolver's schedule, composed as ``loan_resolver.resolve_loan``
    # composes it from the same scenarios (the confirmed history plus the
    # contract's forward).  It was read through ``balance_at.debt_schedule_rows``
    # until plan step recurrence:R16-c-2 deleted that accessor with its last
    # caller; the scenarios above are built from the same inputs, so the
    # figures it feeds are unchanged.
    schedule = list(scenarios.history_rows) + list(scenarios.original_forward)
    return {
        "seam": {d.isoformat(): str(sampled[d]) for d in horizon},
        "history_rows": len(scenarios.history_rows),
        "contract_len": len(scenarios.original_forward),
        "plan_first": (
            installments[0].due_date.isoformat() if installments else None
        ),
        "plan_len": len(installments),
        "payoff": payoff.isoformat() if payoff is not None else None,
        "plan_interest": str(sum(
            (installment.split.interest for installment in installments), _ZERO,
        )),
        "schedule_len": len(schedule),
        "total_interest": str(
            sum((row.interest for row in schedule), _ZERO),
        ),
    }


def main(out_path: str, start: date, end: date) -> None:
    """Write every loan's per-day figures over ``[start, end]`` to *out_path*.

    Args:
        out_path: Destination JSON file.
        start: First read date, inclusive.
        end: Last read date, inclusive.
    """
    app = create_app("development")
    with app.app_context():
        account_ids = [
            row.account_id
            for row in db.session.query(LoanParams).order_by(
                LoanParams.account_id,
            )
        ]
        blob: dict[str, dict] = {}
        for account_id in account_ids:
            account = db.session.get(Account, account_id)
            per_day: dict[str, dict] = {}
            day = start
            while day <= end:
                per_day[day.isoformat()] = _figures(account, day)
                day += timedelta(days=1)
            blob[f"{account_id}:{account.name}"] = per_day
        pathlib.Path(out_path).write_text(
            json.dumps(blob, indent=1, sort_keys=True), encoding="utf-8",
        )
        days = (end - start).days + 1
        print(f"wrote {out_path}: {len(blob)} loans x {days} days")


if __name__ == "__main__":
    _today = date.today()
    _start = (
        date.fromisoformat(sys.argv[2]) if len(sys.argv) > 2
        else date(_today.year, 1, 1)
    )
    _end = (
        date.fromisoformat(sys.argv[3]) if len(sys.argv) > 3
        else date(_today.year, 12, 31)
    )
    main(sys.argv[1], _start, _end)
