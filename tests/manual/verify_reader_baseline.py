"""Dump every figure plan step X-au-c2 ROUTES, for a HEAD-vs-post diff.

The byte-identity gate for the reader leaf, and it exists because the arc's
existing regression harness cannot see this change.
:mod:`tests.manual.verify_balance_baseline` walks the
:mod:`app.services.balance_at` seam exhaustively -- but X-au-c2 re-routes
readers that are almost all OUTSIDE the seam: the analytics calendar, the
dashboard pulse, the spending report, the savings emergency-fund metric, the
investment and retirement projections, the loan payment history, and the
transfer settle door.  Running only the balance baseline over this leaf would
report "nothing moved" while saying nothing about the twelve modules it
actually changed, which is the free-pass shape
``docs/plans/verification.md`` standard 3 asks about.

It answers *did anything move*, never *is the answer right*.  The proof that
the new expressions are correct is the suite's hand-computed figures and the
per-rule controls; this is the exhaustive regression check beside them.

**Why byte-identity is the right gate here.** Plan step X-au-c1 backfilled no
declaration, so every row carries ``amount_source_id IS NULL`` and the amount
resolver answers the stored ``estimated_amount`` through ONE arm.  Every reader
this step re-routes therefore has to produce exactly the figure it produced
before -- not approximately, not modulo rounding.  A single moved cent is a
defect, and the per-kind cutovers (X-au-d..X-au-i) are what deliberately move
figures later.

**Usage** (from the repository root, against a production CLONE)::

    DATABASE_URL=postgresql://.../shekel_xauc2 \\
        .venv/bin/python tests/manual/verify_reader_baseline.py before.json
    # ... make the change ...
    DATABASE_URL=postgresql://.../shekel_xauc2 \\
        .venv/bin/python tests/manual/verify_reader_baseline.py after.json
    diff before.json after.json

For the HEAD side use ``git worktree add`` -- never ``git checkout``, which
reverts the working tree and discards the change under test
(``docs/plans/lessons.md``).

**RUN BOTH SIDES ON THE SAME CIVIL DAY.**  Several of these producers are
clock-dependent -- the retirement and investment projections seed from the
current period and accrue forward from today -- so a BEFORE captured yesterday
and an AFTER captured today differ by the calendar, not by the change.  Measured
during plan step X-au-c2a: a run that straddled midnight reported 2,277 moved
lines and a `current_balance` of `$2,422.94` against `$2,290.36`, all of it the
date.  Re-running the HEAD side the same day made the two IDENTICAL.  If a diff
surprises you, re-capture the HEAD side before reading a single figure -- the
harness cannot tell the two causes apart and does not try to.

Every figure is stringified through :func:`_money` so a ``Decimal`` diff is a
TEXT diff: ``Decimal("1.10")`` and ``Decimal("1.1")`` are equal numerically and
must not be reported as a move, while ``1.10`` -> ``1.11`` must be.
"""

import json
import pathlib
import sys
import traceback
from datetime import date
from decimal import Decimal

from app import create_app
from app.extensions import db
from app.models.account import Account
from app.models.loan_params import LoanParams
from app.models.transfer import Transfer
from app.models.user import User
from app.services import (
    calendar_service,
    dashboard_service,
    loan_loaders,
    loan_payment_service,
    loan_posting_service,
    retirement_projection,
    savings_dashboard_service,
    spending_report_service,
    transfer_service,
)
from app.services.cash_ledger import derived_amount_basis
from app.services.balance_at import BalanceContext
from app.services.investment_dashboard_service import compute_dashboard_data
from app.services.transfer_legs import grid_transfer_leg
from app.utils.balance_predicates import is_projected_clause
from tests._test_helpers import all_periods

# Six fixed valuation dates, the same discipline
# ``verify_balance_baseline`` applies: a producer read at one date is blind
# between dates, and two of these sit outside any seeded horizon.
_DATES = [
    date(2025, 6, 30), date(2026, 1, 15), date(2026, 3, 31),
    date(2026, 8, 12), date(2027, 2, 28), date(2028, 12, 31),
]


def _money(value):
    """Stringify a Decimal so the diff is textual and exact.

    ``None`` passes through as ``None`` so an absent figure is distinguishable
    from a zero one -- which is the whole distinction the amount model turns
    on.
    """
    if value is None:
        return None
    if isinstance(value, Decimal):
        return f"{value:.6f}"
    return str(value)


def _plain(value, depth=0):
    """Serialise ANY producer result to comparable plain data.

    Generic rather than field-by-field, and an early draft of this file is why:
    it named the fields it expected off ``savings_dashboard_service`` and the
    two dashboards with ``getattr(obj, "...", None)``, and all three producers
    return a plain DICT -- so every one of those reads answered ``None`` on
    BOTH sides and the diff reported "identical" over three surfaces it had not
    captured at all.  That is the hole ``docs/plans/lessons.md`` names: a
    baseline hides its own holes, so ask which axis no case varies on.  Walking
    the structure removes the guess.

    ORM instances collapse to ``ClassName#id``: their identity is stable across
    the two runs and their attribute graph is unbounded.
    """
    if value is None or isinstance(value, (bool, int, str)):
        return value
    if isinstance(value, Decimal):
        return _money(value)
    if isinstance(value, float):
        return f"FLOAT:{value!r}"
    if isinstance(value, (date,)):
        return value.isoformat()
    if depth > 8:
        return "DEPTH"
    if isinstance(value, dict):
        return {str(k): _plain(v, depth + 1) for k, v in sorted(
            value.items(), key=lambda kv: str(kv[0]),
        )}
    if isinstance(value, (list, tuple, set)):
        return [_plain(v, depth + 1) for v in value]
    if hasattr(value, "_sa_instance_state"):
        return f"{type(value).__name__}#{getattr(value, 'id', None)}"
    if hasattr(value, "__dict__"):
        return {
            "__type__": type(value).__name__,
            **{
                k: _plain(v, depth + 1)
                for k, v in sorted(vars(value).items())
                if not k.startswith("_")
            },
        }
    return str(value)


def _guard(label, thunk):
    """Run *thunk*, recording a RAISE rather than aborting the dump.

    A producer that raises on one account must not hide the other thirty
    figures, and the raise itself is a fact worth diffing: this leaf's whole
    risk is a reader that starts refusing where it used to answer, so
    "raised here, answered there" has to show up as a move rather than as a
    crashed run.
    """
    try:
        return thunk()
    except Exception as exc:  # pylint: disable=broad-exception-caught
        return {
            "RAISED": type(exc).__name__,
            "message": str(exc)[:400],
            "where": label,
            "tb": traceback.format_exc(limit=3)[-400:],
        }


def _calendar(user_id, account_id):
    """Every day cell and month total the analytics calendar renders.

    ``_build_day_entry`` is one of the routed sites, and its figure reaches
    both the day cell and the month headline, so both are captured.
    """
    out = {}
    for year in (2025, 2026, 2027):
        overview = _guard(
            f"year_overview:{year}",
            lambda y=year: calendar_service.get_year_overview(
                user_id, y, account_id,
                user_settings=None,
            ),
        )
        if isinstance(overview, dict):
            out[str(year)] = overview
            continue
        out[str(year)] = {
            "annual_income": _money(overview.annual_income),
            "annual_expenses": _money(overview.annual_expenses),
            "months": [
                {
                    "month": ms.month,
                    "total_income": _money(ms.total_income),
                    "total_expenses": _money(ms.total_expenses),
                    "day_totals": {
                        str(day): [_money(inc), _money(exp)]
                        for day, (inc, exp) in sorted(ms.day_totals.items())
                    },
                    "day_entries": {
                        str(day): [
                            [e.item_key, _money(e.amount), e.is_large]
                            for e in entries
                        ]
                        for day, entries in sorted(ms.day_entries.items())
                    },
                }
                for ms in overview.months
            ],
        }
    return out


def _pulse(user_id):
    """The dashboard pulse's due-soon amounts and still-due totals.

    Both read the ONE contributions map this step introduces, so a divergence
    between them would show here as well as a move in either.
    """
    section = _guard("pulse", lambda: dashboard_service.compute_pulse_section(
        dashboard_service.resolve_section(BalanceContext.build(user_id)),
    ))
    if section is None or "RAISED" in section:
        return section
    return {
        "still_due": {
            k: _money(v) if isinstance(v, Decimal) else str(v)
            for k, v in section["still_due"].items()
        },
        "due_soon": [
            {
                "id": bill["id"],
                "amount": _money(bill["amount"]),
                "amount_base": bill["amount_base"],
                "entry_remaining": _money(bill["entry_remaining"]),
                "entry_over_budget": bill["entry_over_budget"],
            }
            for bill in section["due_soon"]
        ],
    }


def _spending(user_id, account_id):
    """The spending report's hero, category totals and surprises."""
    _ = account_id
    out = {}
    for year, month in ((2026, 6), (2026, 7), (2026, 8)):
        report = _guard(
            f"spending:{year}-{month}",
            lambda y=year, m=month: spending_report_service
            .compute_spending_report(
                user_id,
                spending_report_service.SpendingWindow(
                    window_type="month", period_id=None, month=m, year=y,
                ),
                user_settings=None,
            ),
        )
        out[f"{year}-{month:02d}"] = (
            report if report is None
            or (isinstance(report, dict) and "RAISED" in report)
            else {
                "spent_total": _money(report.hero.spent_total),
                "breakdown": [
                    [row.group_name, _money(row.amount), _money(row.delta),
                     [[i.item_name, _money(i.amount)] for i in row.items]]
                    for row in report.breakdown
                ],
                "changes": [
                    [c.category_id, c.group_name, c.item_name,
                     _money(c.current), _money(c.prior), _money(c.delta)]
                    for c in report.changes
                ],
                "series": [_money(pt.total) for pt in report.series],
                "surprises_net": _money(report.surprises.net),
                "surprises": [
                    [s.item_key, _money(s.estimated),
                     _money(s.actual), _money(s.delta)]
                    for s in report.surprises.rows
                ],
            }
        )
    return out


def _savings(user_id):
    """The savings dashboard's emergency-fund metrics (the settled-spend feed)."""
    return _plain(_guard(
        "savings",
        lambda: savings_dashboard_service.compute_dashboard_data(
            BalanceContext.build(user_id),
        ),
    ))


def _investment(user_id, accounts):
    """Every investment account's projection inputs and contribution timeline."""
    out = {}
    for account in accounts:
        out[str(account.id)] = _plain(_guard(
            f"investment:{account.id}",
            lambda a=account: compute_dashboard_data(user_id, a),
        ))
    return out


def _retirement(user_id):
    """The retirement projection's per-account results (the cross-account batch).

    Only the PROJECTIONS are dumped, not the axis or the clock beside them:
    pay-calendar plan step C2-e made those published facts, and both are new
    rather than moved.  The axis has its own harness
    (:mod:`tests.manual.verify_projection_axis`); this one keeps grading the
    figures it was written for.

    The three entries are composed here rather than called through one
    convenience wrapper because plan step C2-f2d-2 deleted that wrapper: it had
    no ``app/`` caller once the retirement picture became the one producer, and
    a public entry whose only callers are harnesses is dead production surface.

    **This probe was DEAD from plan step C2-f2d-3 until C2-f2e's adversarial
    code review found it** (2026-08-18). That step dropped
    ``build_projection_context``'s two PERIOD parameters -- both are derived
    from the pass now -- and did not update this call, which kept passing six
    positional arguments to a four-argument function. Every run since recorded
    a ``TypeError`` in the dump where the retirement figures belong, and a diff
    of two such runs reads as "nothing moved". `pylint tests/manual/` sees this
    class (``E1121``) and nothing runs it; ledger row **P66** carries that.
    *The gate then caught the same class a second time, at plan step
    salary:S3-e-2, which dropped the ``employer_salary_basis`` parameter:
    this call was passing four arguments to a three-argument function
    before the commit, and the pre-commit run of that gate is what said so.*
    """
    def _project():
        ctx = retirement_projection.build_projection_context(
            BalanceContext.build(user_id), None, None,
        )
        return retirement_projection.project_accounts_with_batch(
            ctx,
            retirement_projection.load_projection_batch(ctx),
            retirement_projection.resolve_projection_axis(ctx),
        )

    return _plain(_guard("retirement", _project))


def _loans(user_id, scenario_id, accounts):
    """Loan payment history and the confirmed split table, per loan account."""
    out = {}
    for account in accounts:
        params = _guard(
            f"loan_params:{account.id}",
            lambda a=account: [
                [str(p.dates.due_date), _money(p.amount), p.dates.is_confirmed]
                for p in loan_payment_service.load_loan_context(
                    a.id, derived_amount_basis(user_id, scenario_id),
                    db.session.query(LoanParams)
                    .filter(LoanParams.account_id == a.id).one(),
                ).payments
            ],
        )
        history = _guard(
            f"loan_history:{account.id}",
            lambda a=account: loan_posting_service
            .confirmed_loan_payment_history(a.id, scenario_id, date(2026, 8, 12)),
        )
        out[str(account.id)] = {
            "loan_context_payments": params,
            "confirmed_history": (
                history if isinstance(history, dict)
                else None if history is None
                else [
                    # ``due_date`` before ruling R-R108, ``installment`` after
                    # it: read whichever the tree carries.
                    [str(getattr(row, 'installment', None) or getattr(row, 'due_date')),
                     _money(row.cash), _money(row.principal),
                     _money(row.interest), _money(row.escrow)]
                    for row in history
                ]
            ),
        }
    _ = user_id
    return out


def _transfer_settle(scenario_id):
    """What a settle would BOOK on both legs of every projected transfer.

    ``transfer_service.leg_settle_amount`` is a pure read and the reconcile
    panel calls it per offered leg, so both legs of every live projected
    transfer are asked, keyed ``<transfer id>:<account id>`` (the leg's
    ``cell_key``) -- which is also the widest possible exercise of the one-row
    basis plan step X-au-c2 gave it.

    **It asked ``transfer_service.settle_amount(shadow, basis)`` per projected
    shadow until plan step balance:X-bi-6-4d-2 deleted that function** (the
    twins stopped carrying a transfer's status there).  At dev ``22262643c``,
    the base that leaf is graded against, ``leg_settle_amount(leg, basis)``
    returns ``settle_amount(_shadow_of(leg), basis)``, ``_shadow_of`` being
    the live shadow on the leg's side (``transfer_service/_settle.py``:565,
    :510 there), so on that base this one call IS the deleted path and on the
    leaf it is the re-bodied one: the arm compares the two, it does not ask
    one producer twice.
    """
    transfers = (
        db.session.query(Transfer)
        .filter(
            Transfer.is_deleted.is_(False),
            is_projected_clause(Transfer),
        )
        .order_by(Transfer.id)
        .all()
    )
    _ = scenario_id
    out = {}
    for transfer in transfers:
        for account_id in (transfer.from_account_id, transfer.to_account_id):
            key = f"{transfer.id}:{account_id}"
            out[key] = _guard(
                f"leg_settle_amount:{key}",
                lambda t=transfer, a=account_id: _leg_settle_amount(t, a),
            )
    return out


def _leg_settle_amount(transfer, account_id):
    """What settling *transfer* would book on its leg on *account_id*."""
    leg = grid_transfer_leg(transfer, account_id)
    return _money(transfer_service.leg_settle_amount(
        leg, derived_amount_basis(leg.user_id, leg.scenario_id),
    ))


def _dump_user(user_id):
    """Every routed figure for one user."""
    ctx = _guard("balance_ctx", lambda: BalanceContext.build(user_id))
    if isinstance(ctx, dict):
        return {"balance_ctx": ctx}
    # A user with no baseline scenario is a real state on the clone, and the
    # seam REFUSES rather than answering for them (ruling R-BW).  Recorded as
    # a refusal so the diff still covers the user: what must not move is
    # whether they are refused, not only what they are told.
    scenario_id = _guard("scenario_id", lambda: ctx.scenario_id)
    if isinstance(scenario_id, dict):
        return {"scenario_id": scenario_id}
    accounts = (
        db.session.query(Account)
        .filter(Account.user_id == user_id)
        .order_by(Account.id)
        .all()
    )
    periods = all_periods(user_id)
    return {
        "period_count": len(periods),
        "calendar": {
            str(a.id): _calendar(user_id, a.id) for a in accounts
        },
        "pulse": _pulse(user_id),
        "spending": {
            str(a.id): _spending(user_id, a.id) for a in accounts
        },
        "savings": _savings(user_id),
        "investment": _investment(user_id, accounts),
        "retirement": _retirement(user_id),
        "loans": _loans(user_id, scenario_id, accounts),
        "transfer_settle": _transfer_settle(scenario_id),
        # A LOAN's feed, dumped for loan accounts only since plan step
        # recurrence:R16-c-2 (ruling R-R107), when it began taking the loan's
        # params.  Before that it dumped EVERY account with a hard-coded due
        # day of 1 -- wrong for a loan due the 22nd -- so a baseline taken
        # before that step differs in its account set.
        "payment_history": {
            str(a.id): _guard(
                f"payment_history:{a.id}",
                lambda acc=a, params=params: [
                    [str(p.dates.period_start), str(p.dates.due_date),
                     _money(p.amount), p.dates.is_confirmed]
                    for p in loan_payment_service.get_payment_history(
                        acc.id, derived_amount_basis(user_id, scenario_id),
                        params,
                    )
                ],
            )
            for a in accounts
            if (params := loan_loaders.load_loan_params(a.id)) is not None
        },
    }


def main():
    """Dump every user's routed-reader figures to the given path."""
    out_path = pathlib.Path(sys.argv[1])
    app = create_app()
    with app.app_context():
        blob = {}
        for user in db.session.query(User).order_by(User.id).all():
            blob[str(user.id)] = _dump_user(user.id)
    out_path.write_text(
        json.dumps(blob, indent=2, sort_keys=True, default=str),
        encoding="utf-8",
    )
    print(f"wrote {out_path} ({len(blob)} users)")


if __name__ == "__main__":
    main()
