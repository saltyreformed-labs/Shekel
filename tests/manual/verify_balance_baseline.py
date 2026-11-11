"""Dump every balance figure the seam renders, for a HEAD-vs-post diff.

The balance arc's verification standard (plan Section 7.2,
``docs/audits/balance_architecture/README.md``) says the baseline must not move
unless a step's design says it moves, and that oracles are **exhaustive and
never sampled** -- "a 14-day sample once scored perfect while wrong by
$178,103.41 on 22% of days".  This script is the exhaustive half: it walks
every account in a database and writes one JSON blob of every figure the
:mod:`app.services.balance_at` seam can answer about it.  Run it before a
change and after, diff the two files, and every moved cent is visible.

It answers "did anything move", never "is the answer right".  A step's PROOF is
its firing controls and its hand-computed oracles; this is the REGRESSION check
beside them.  Two figures identical here can both be wrong -- see finding N-69,
where two tests agreed over an account holding no rows.

**What it captures, per account:**

* the kind-correct scalar at today, at six fixed valuation dates, and the
  kind-correct period map (all five account kinds, so a loan regression shows up
  in a cash commit).  **The dated scalars are plan step X-g2b-0's addition and
  they exist because the other two are BLIND between them**: the map answers
  period ENDS and the today scalar answers one day, so a mid-period date-precise
  read (finding N-71) and the pre-horizon back-projection (N-74) -- the two
  regions plan step X-g2b moves furthest -- fell straight through the gap.  Two
  of the six dates are deliberately outside the seeded horizon, which is where
  a period-keyed producer and a total fold differ most;
* for every non-loan account: the whole ``GridBalanceView`` -- balance, income,
  expense, net, ruling R-K's remainder (as its pre-S1-c total AND ruling
  R-DH (f)'s two halves, see :func:`_grid_columns`), and BOTH modelled tiers
  (contribution and accrual, plan step X-g3a) -- plus the live override map the
  projection was computed with (ruling R-Q).  The two tiers are captured at
  X-g3a, which moves no figure, precisely so plan step X-g3b's cutover can be
  DIFFED rather than argued: the instrument before the measurement, the
  X-g2b-0 precedent;
* the cash-flow scalar at today and at the same six fixed valuation dates, and the day-by-day series over the entire period horizon (the
  no-sampling requirement);
* for the modelled kinds, ``investment_growth_since_anchor`` -- the seam entry
  plan step X-g changes, captured so that step's cutover can be diffed rather
  than argued.  Its sibling ``investment_seed_map`` was captured here too until
  plan step X-g2b deleted that entry (ruling R-AE).

**Usage** (from the repository root)::

    DATABASE_URL=postgresql://.../shekel_f3_final \\
        .venv/bin/python tests/manual/verify_balance_baseline.py before.json
    # ... make the change ...
    DATABASE_URL=postgresql://.../shekel_f3_final \\
        .venv/bin/python tests/manual/verify_balance_baseline.py after.json
    diff before.json after.json

For a HEAD-vs-post comparison use ``git worktree add`` for the HEAD side --
never ``git checkout``, which reverts the working tree and discards the very
change under test.

**One database is now enough, and that is a LOSS rather than a simplification.**
This said to run it against BOTH -- ``shekel`` the dev runtime clone and
``shekel_f3_final`` the prod-shape one -- because they carried DIFFERENT account
sets, and plan step X-c2c1's run found figures that existed in one and not the
other.  That is no longer true: measured 2026-08-07, both hold the identical
nine accounts and differ by one transaction row, because ``shekel`` has since
been re-cloned from production and ``shekel_f3_final`` was refreshed from it.
Running both now costs a second pass and buys almost nothing.

So a second dataset is worth REBUILDING before a step that could be
data-shape-sensitive, rather than assumed to exist: give it an account the
other lacks, or a kind the other has no instance of.  X-c2c1's find is the
argument for doing so -- a figure that exists in only one dataset is invisible
to a harness that sees one shape twice.

**Its own limitation, stated so it is not mistaken for more.**  Every figure
here is read at the seam's default ``as_of`` (the reader's today).  A step that
changes behaviour only for a pinned historical ``as_of`` moves nothing in this
output, and X-c2c1 was exactly such a step: its real-data run was byte-identical
by construction, and the firing control -- not this script -- was its proof.

This file is deliberately outside pytest's collection (``pytest.ini`` sets
``python_files = test_*.py``), like its ``verify_*`` siblings here: it needs a
populated database chosen by the operator, not the seeded test template.
"""

import json
import pathlib
import sys
from datetime import date
from decimal import Decimal

# Python puts the SCRIPT's own directory on ``sys.path``, not the working
# directory, so ``app`` is not importable when this is run as
# ``.venv/bin/python tests/manual/verify_balance_baseline.py``.  Its siblings
# here need no bootstrap because they drive a RUNNING server over HTTP; this is
# the first one that loads the app in-process, which it must -- the figures it
# dumps are seam calls, and going through routes would capture what a template
# rendered rather than what the producer answered.
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2]))

# Pylint: ``wrong-import-position`` -- these must follow the path bootstrap
# above; there is no import order that resolves ``app`` before it runs.
# pylint: disable=wrong-import-position
from app import create_app
from app.extensions import db
from app.models.account import Account
from app.models.user import User
from app.services import balance_at
from app.services.account_projection import (
    AccountProjectionKind,
    classify_account,
)

from tests._test_helpers import (
    current_pay_period,
    derived_span,
)


# Fixed valuation dates spanning the horizon, read by BOTH the cash-flow scalar
# and the kind-correct one: two in the past (one of them pre-anchor for the real
# accounts, which is where the cutover's pre-anchor fabrication used to live),
# today's neighbourhood, and three forward.
#
# Two of the six sit deliberately OUTSIDE the seeded horizon -- 2026-01-15
# before the first pay period and 2029-01-01 after the last -- because that is
# where a period-keyed producer and a total fold differ most, and neither the
# period map nor the today scalar can see it.  A producer that resolves a date
# to its period answers the FIRST period's balance before the horizon and the
# LAST period's after it; a fold answers the date.  Plan step X-g2b moves both
# ends (findings N-74 and N-82), so both are pinned here.
_SCALAR_DATES = (
    date(2026, 1, 15),
    date(2026, 4, 30),
    date(2026, 6, 3),
    date(2026, 12, 31),
    date(2027, 6, 30),
    date(2029, 1, 1),
)


def _money(value):
    """Return a JSON-stable string for a Decimal, or None.

    Formatted to two places so a ``Decimal("5.1")`` and a ``Decimal("5.10")``
    -- equal as money, distinct as objects -- cannot show up as a spurious
    diff.
    """
    if value is None:
        return None
    return f"{Decimal(value):.2f}"


def _grid_columns(account, ctx):
    """Return the whole grid view for *account*, as JSON-ready primitives.

    **The remainder is captured as THREE keys since plan step S1-c** (ruling
    R-DH (f)).  ``GridColumn.reconciliation`` split into ``period_timing`` and
    ``book_vs_bank``, and no combined accessor survives on the dataclass --
    leaving one would invite a surface to render the sum again, which is the
    figure the ruling exists to delete.  This script is not a surface: it is the
    instrument the verification standard diffs a change against, and a baseline
    captured BEFORE the split carries the single ``reconciliation`` key.  So the
    sum is composed HERE, once, under that key, purely so a pre-split baseline
    stays diffable -- and the two halves are captured beside it so the split
    itself is visible in the same diff rather than argued.

    **The ``reconciliation`` key is a dated compatibility shim: delete it once
    a post-S1-c baseline has been re-cut**, which is the last moment any
    comparison needs it.  A shim with no stated end is how the deleted name
    survives its own deletion.
    """
    view = balance_at.grid_balance_view(account, ctx)
    return {
        "columns": {
            str(period_id): {
                "balance": _money(col.balance),
                "income": _money(col.income),
                "expense": _money(col.expense),
                "net": _money(col.net),
                # The PRE-SPLIT total, for diffing against a baseline captured
                # before S1-c.  No production surface may read this sum.
                "reconciliation": _money(
                    col.period_timing + col.book_vs_bank,
                ),
                "period_timing": _money(col.period_timing),
                "book_vs_bank": _money(col.book_vs_bank),
                "contribution": _money(col.contribution),
                "accrual": _money(col.accrual),
            }
            for period_id, col in view.columns.items()
        },
    }


def _cash_figures(account, ctx, periods):
    """Return the cash-flow scalars and the every-day series for *account*."""
    figures = {
        "cash_scalar_today": _money(
            balance_at.cash_balance_at(account, ctx, date.today()),
        ),
        "cash_scalar_dates": {
            day.isoformat(): _money(
                balance_at.cash_balance_at(account, ctx, day),
            )
            for day in _SCALAR_DATES
        },
    }
    if periods:
        series = balance_at.cash_daily_balance_series(
            account, ctx, periods[0].start_date, periods[-1].end_date,
        )
        figures["daily_series"] = [
            [day.isoformat(), _money(balance)]
            for day, balance in series.items()
        ]
    return figures


def _modelled_figures(account, ctx):
    """Return the growth decomposition the detail page's chip renders, or ``{}``.

    ``investment_seed_map`` used to be captured beside it -- the pre-growth SEED
    a forward chart compounded FROM -- and plan step X-g2b DELETED that entry
    (ruling R-AE): a seed is the account's balance on a date, which
    ``scalar_dates`` above already answers, so there is nothing left for a
    separate accessor to say.  Its keys therefore disappear from this blob at
    that step, which is the deletion showing up rather than a figure moving.
    """
    if classify_account(account) not in (
        AccountProjectionKind.INVESTMENT,
        AccountProjectionKind.APPRECIATING,
    ):
        return {}
    # The DERIVED period, because that is the parameter's type since plan step
    # ``pay_calendar:C4-c``: the ORM row carries no span any more.
    growth = balance_at.investment_growth_since_anchor(
        account, ctx, derived_span(current_pay_period(ctx.user_id)),
    )
    return {
        # A ``(growth, contributed)`` pair, or None when the account models
        # no growth -- not a scalar, which is why it is unpacked rather than
        # passed straight to :func:`_money`.
        "investment_growth_since_anchor": [
            _money(part) for part in growth
        ] if growth is not None else None,
    }


def _account_blob(account, ctx, periods):
    """Return every figure the seam can answer about one account."""
    kind = classify_account(account)
    blob = {
        "name": account.name,
        "kind": kind.name,
        "scalar_today": _money(
            balance_at.balance_at(account, ctx, date.today()),
        ),
        # The kind-correct scalar at each fixed date (plan step X-g2b-0).  It is
        # asked of EVERY kind, loans included: a loan's is ``positions()``, so
        # these dates are also the standing loan regression gate read at a past
        # and a future date rather than only at today.
        "scalar_dates": {
            day.isoformat(): _money(balance_at.balance_at(account, ctx, day))
            for day in _SCALAR_DATES
        },
        "kind_correct_map": {
            str(period_id): _money(balance)
            for period_id, balance in balance_at.balance_map(
                account, ctx,
            ).items()
        },
    }
    # A loan is refused by every cash-flow surface (ruling R-J), so asking the
    # cash view about one is not a baseline -- it is the defect plan step X-a1
    # closed.  Its kind-correct figures above ARE the loan regression gate.
    if kind is not AccountProjectionKind.AMORTIZING:
        blob.update(_grid_columns(account, ctx))
        blob.update(_cash_figures(account, ctx, periods))
        blob.update(_modelled_figures(account, ctx))
    return blob


def main(out_path):
    """Write the baseline blob for the configured database to *out_path*."""
    app = create_app()
    with app.app_context():
        result = {}
        users = db.session.query(User).order_by(User.id).all()
        for user in users:
            ctx = balance_at.BalanceContext.build(user.id)
            if ctx.scenario is None:
                continue
            # The pass's own calendar rather than a second read of the
            # table: ``_cash_figures`` wants a span, and a period's end
            # is derived from the paydays since plan step
            # ``pay_calendar:C4-c`` dropped the column.
            periods = ctx.calendar().saved()
            accounts = (
                db.session.query(Account)
                .filter(Account.user_id == user.id)
                .order_by(Account.id)
                .all()
            )
            for account in accounts:
                key = f"user{user.id}/acct{account.id}"
                result[key] = _account_blob(account, ctx, periods)

        with open(out_path, "w", encoding="utf-8") as handle:
            json.dump(result, handle, indent=1, sort_keys=True)

    cells = sum(len(b.get("columns", {})) for b in result.values())
    days = sum(len(b.get("daily_series", [])) for b in result.values())
    print(
        f"wrote {out_path}: {len(result)} accounts, {cells} grid cells, "
        f"{days} daily points"
    )


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print(__doc__)
        raise SystemExit(
            "usage: verify_balance_baseline.py <output.json>  "
            "(DATABASE_URL selects the database)"
        )
    main(sys.argv[1])
