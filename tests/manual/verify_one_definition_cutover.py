"""Dump every figure the ONE-DEFINITION CUTOVER can move, for a before/after diff.

Plan step ``balance:X-bi-7d-2`` (migration ``596408fab6f1``) mints every bare
row a rule-less definition, dates the undated on their paycheck's start,
declares each TEMPLATE-priced, drops both flag columns and re-cuts
``ck_transactions_one_pricing_link`` to ``= 1``.  Its claim about itself is
EQUALITY: a bare row OWNED its figure and the same figure is now the ONE
version of its definition's series read on the row's own due date, so every
money reader answers what it answered.  This is the instrument, in the shape
of ``verify_level_relation.py``: run BEFORE the migration on a production
restore with the PRE-CUTOVER application (a checkout of dev at the base the
branch merged, this file copied in) and AFTER it with the branch, and diff
the two dumps.  **The before side must be the pre-cutover code, and the
flags are why**: the branch's model maps neither flag cell, so its
``tracks_purchases`` answers ``False`` for a bare row that stated ``True``
in its own cell -- a branch-code before-dump misread 4 envelopes and 3
companion-visible rows on the 2026-09-18 restore and reported them as the
cutover's doing.  The money halves (rule 1 prices a bare row off its column
under either code) agree on both; the flags do not.  The balance seam itself
is ``verify_balance_baseline.py``'s dump; run that alongside, both sides the
same way.

What it captures:

* **the bare rows** (``bare``): id, name, status, paycheck, due date,
  ``occurs_on``, the stored figure or the resolved one (``figure`` is what
  the app SAYS the row's amount is, through the one resolver, on either side
  of the cutover), the recorded settle figure and basis, both flags as the
  accessors answer them, and the link -- keyed by row id, so the after-dump's
  ``template_id`` is the one key expected to move, and ``due_date`` /
  ``occurs_on`` on exactly the rows the migration dated;
* **the grid** (``grid``), per account over EVERY saved paycheck: the row keys
  of both sections and each cell's matched row ids, exactly as the route
  builds them (``load_grid_items`` -> ``build_row_keys`` ->
  ``build_matched_by_row_period``), plus the ``amounts_by_id`` map every cell
  reads its figure from;
* **the companion pages** (``companion``): per companion user and paycheck,
  the row ids ``companion_service.get_visible_transactions`` shows;
* **timeliness** (``timeliness``): ``payment_timeliness_from_txns`` over every
  settled expense, and per row the ``days_paid_before_due`` -- the ONE
  surface the cutover is ruled to move (**R-BAL22**: a Paid row that was
  undated now reads its paycheck's start), printed per row;
* **statements** (``statements``): how many SQL statements the grid's read
  path issued for the LARGEST account, beside its row and definition counts
  -- finding **BAL-511**'s measurement, which must be FLAT in the one-off
  count if the pricing pass rides the route's eager loads;
* **the three tables** (``tables``): every row of ``transactions``,
  ``transaction_templates`` and ``template_amount_versions`` by id, so the
  downgrade's claim -- schema back, no row moved -- is a diff of this key
  between the post-upgrade dump and the post-downgrade one.

Usage::

    DATABASE_URL=postgresql://.../<a restore> PYTHONPATH=. \\
        python tests/manual/verify_one_definition_cutover.py before.json
    flask db upgrade
    ... python tests/manual/verify_one_definition_cutover.py after.json
    python tests/manual/verify_one_definition_cutover.py --compare before.json after.json

``--compare`` prints the expected differences with their figures (the bare
rows' links and dates, the timeliness days) and FAILS on any other one.
``--compare-downgrade AFTER_UP.json AFTER_DOWN.json`` grades the downgrade
(**R-BAL67**): the three tables identical but for the two re-added cells at
``false``, and the pre-cutover schema back under the prior names.
Reads only.  No writes, no commit, nothing staged.
"""

import json
import sys
from collections import Counter
from datetime import date
from decimal import Decimal

from sqlalchemy import event, text

from app import create_app
from app.extensions import db
from app.models.account import Account
from app.models.category import Category
from app.models.transaction import Transaction
from app.models.transaction_template import TransactionTemplate
from app.models.user import User
from app.routes.grid._items import load_grid_items
from app.services.cash_flow_set import CashFlowSet  # noqa: E402  pylint: disable=wrong-import-position
from app.services import balance_at, companion_service, grid_view_service
from app.services.cash_ledger import amounts_by_id, resolve_transaction_amount
from app.services.spending_analysis import payment_timeliness_from_txns
from app.services.row_valuation import settled_figure


def _plain(value):
    """Render a value as JSON-safe data."""
    if isinstance(value, Decimal):
        return f"{value:.2f}"
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, dict):
        return {str(_plain(k)): _plain(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_plain(v) for v in value]
    return value


def _is_bare_or_minted(row):
    """Whether *row* is a bare row (before), or any rule-less definition's row.

    Before the cutover a bare row names no link and is what the migration
    acts on.  After it the same row names a RULE-LESS definition -- as a
    grid-made one-off does on both sides (3 on the 2026-09-18 restore) -- so
    the key set is the same on both sides and, keyed by row id, the diff sees
    the bare rows as MOVED links and the pre-existing one-offs as unmoved.

    A transfer's shadow, which named its transfer, was excluded here too
    until plan step ``balance:X-bi-6-5`` dropped that column (plan step
    ``balance:X-bi-6-4d-2`` having deleted the shadows); a before-side run
    on a checkout that still holds shadows must exclude them again.
    """
    if row.credit_payback_for_id is not None:
        return False
    return row.template_id is None or not row.recurs


def _bare_rows(ctx):
    """Every bare (or minted) row of the owner, by id."""
    rows = (
        db.session.query(Transaction)
        .filter(Transaction.user_id == ctx.user_id)
        .order_by(Transaction.id)
        .all()
    )
    out = {}
    for row in rows:
        if not _is_bare_or_minted(row):
            continue
        out[str(row.id)] = {
            "name": row.name,
            "status": row.status.name,
            "pay_period_id": row.pay_period_id,
            "due_date": _plain(row.due_date),
            "occurs_on": _plain(row.occurs_on),
            "figure": _plain(resolve_transaction_amount(row, ctx.amounts())),
            "settled_figure": _plain(settled_figure(row)),
            "settled_on": _plain(row.settled_on),
            "tracks_purchases": row.tracks_purchases,
            "visible_to_companion": row.visible_to_companion,
            "is_override": row.is_override,
            "is_deleted": row.is_deleted,
            "template_id": row.template_id,
            "definition_name": None if row.template is None else row.template.name,
            "entries": len(row.entries),
        }
    return out


def _grid(account, ctx, periods, categories):
    """The grid's row keys, cells and figures for *account* over *periods*."""
    # The grid's window is rows AND transfer legs since balance:X-bi-6-1; this
    # harness measured definitions, which only rows carry.  The loader takes
    # the account's cash-flow SET (credit_card:CC-4-1), a set of one here.
    rows = load_grid_items(CashFlowSet.single(account), ctx, periods).rows
    income_keys = grid_view_service.build_row_keys(rows, categories, True)
    expense_keys = grid_view_service.build_row_keys(rows, categories, False)
    matched = grid_view_service.build_matched_by_row_period(
        income_keys, expense_keys, periods, rows,
    )
    budgets = amounts_by_id(rows, ctx.amounts())
    return {
        "row_count": len(rows),
        "definitions": len({r.template_id for r in rows if r.template_id is not None}),
        "income_rows": [[k.category_id, k.txn_name] for k in income_keys],
        "expense_rows": [[k.category_id, k.txn_name] for k in expense_keys],
        "cells": {
            f"{cat}/{name}/{period_id}": sorted(t.id for t in txns)
            for (cat, _tid, name, period_id), txns in matched.items()
            if txns
        },
        "budgets": {str(k): _plain(v) for k, v in budgets.items()},
    }


def _count_statements(fn):
    """Run *fn* and return ``(result, statement count)`` on the app's engine."""
    seen = []

    def _listen(_conn, _cursor, statement, *_args):
        seen.append(statement)

    event.listen(db.engine, "before_cursor_execute", _listen)
    try:
        result = fn()
    finally:
        event.remove(db.engine, "before_cursor_execute", _listen)
    return result, len(seen)


def _companion_pages(owner, periods):
    """Per companion of *owner* and paycheck, the row ids shown."""
    companions = (
        db.session.query(User)
        .filter(User.linked_owner_id == owner.id)
        .order_by(User.id)
        .all()
    )
    out = {}
    for companion in companions:
        pages = {}
        for period in periods:
            read = companion_service.get_visible_transactions(
                companion.id, period_id=period.period_id,
            )
            pages[str(period.period_id)] = sorted(t.id for t in read.transactions)
        out[str(companion.id)] = pages
    return out


def _timeliness(ctx):
    """The timeliness metric over every settled expense, and each row's days."""
    rows = (
        db.session.query(Transaction)
        .filter(
            Transaction.user_id == ctx.user_id,
            Transaction.scenario_id == ctx.scenario_id,
            Transaction.is_deleted.is_(False),
        )
        .order_by(Transaction.id)
        .all()
    )
    expenses = [r for r in rows if r.is_expense and r.settled_on is not None]
    return {
        "metric": _plain(payment_timeliness_from_txns(expenses)),
        "days_per_row": {
            str(r.id): r.days_paid_before_due for r in expenses
        },
    }


def _tables():
    """Every row of the three tables the migration writes, by id."""
    out = {}
    for table in ("transactions", "transaction_templates", "template_amount_versions"):
        rows = db.session.execute(
            text(f"SELECT * FROM budget.{table} ORDER BY id")
        ).mappings().all()
        out[table] = {
            str(r["id"]): {
                k: _plain(v) for k, v in r.items()
                if k not in ("created_at", "updated_at")
            }
            for r in rows
        }
    return out


def _schema():
    """The constraints and columns the migration changes."""
    constraints = db.session.execute(text(
        "SELECT conname, pg_get_constraintdef(oid) FROM pg_constraint "
        "WHERE conrelid = 'budget.transactions'::regclass "
        "AND conname IN ('ck_transactions_one_pricing_link', "
        "'fk_transactions_template_id', 'transactions_template_id_fkey', "
        "'fk_transactions_credit_payback_for') ORDER BY conname"
    )).all()
    columns = db.session.execute(text(
        "SELECT column_name FROM information_schema.columns "
        "WHERE table_schema = 'budget' AND table_name = 'transactions' "
        "AND column_name IN ('is_envelope', 'companion_visible') ORDER BY 1"
    )).scalars().all()
    return {"constraints": dict(constraints), "flag_columns": columns}


def dump(out_path):
    """Write the dump for every owner to *out_path*."""
    app = create_app()
    with app.app_context():
        out = {"schema": _schema(), "tables": _tables(), "users": {}}
        owners = (
            db.session.query(User)
            .filter(User.linked_owner_id.is_(None))
            .order_by(User.id)
            .all()
        )
        for owner in owners:
            ctx = balance_at.BalanceContext.build(owner.id)
            if ctx.scenario is None:
                continue
            periods = ctx.calendar().saved()
            categories = (
                db.session.query(Category)
                .filter_by(user_id=owner.id)
                .order_by(Category.group_name, Category.item_name)
                .all()
            )
            accounts = (
                db.session.query(Account)
                .filter(Account.user_id == owner.id)
                .order_by(Account.id)
                .all()
            )
            grids = {
                str(account.id): _grid(account, ctx, periods, categories)
                for account in accounts
            }
            statements = None
            if accounts:
                largest = max(accounts, key=lambda a: grids[str(a.id)]["row_count"])
                # A FRESH session state for the count: the dump above has
                # hydrated every row, so the identity map would answer the
                # loads this measures.
                db.session.expire_all()
                _, count = _count_statements(
                    lambda: _grid(largest, ctx, periods, categories),
                )
                statements = {
                    "account_id": largest.id,
                    "rows": grids[str(largest.id)]["row_count"],
                    "definitions": grids[str(largest.id)]["definitions"],
                    "statements": count,
                }
            out["users"][str(owner.id)] = {
                "bare": _bare_rows(ctx),
                "grid": grids,
                "companion": _companion_pages(owner, periods),
                "timeliness": _timeliness(ctx),
                "statements": statements,
                "definitions_total": db.session.query(TransactionTemplate)
                .filter_by(user_id=owner.id).count(),
            }
        with open(out_path, "w", encoding="utf-8") as handle:
            json.dump(out, handle, indent=1, sort_keys=True)
            handle.write("\n")
    for owner_id, blob in out["users"].items():
        print(
            f"user {owner_id}: {len(blob['bare'])} bare/minted rows, "
            f"{len(blob['grid'])} accounts, statements={blob['statements']}, "
            f"definitions={blob['definitions_total']}"
        )


def compare(before_path, after_path):
    """Diff two dumps; print the expected differences; fail on any other."""
    with open(before_path, encoding="utf-8") as handle:
        before = json.load(handle)
    with open(after_path, encoding="utf-8") as handle:
        after = json.load(handle)
    unexpected = []
    for owner_id, b_user in before["users"].items():
        a_user = after["users"][owner_id]
        # The bare rows: the link, the date and the occurrence are what
        # moves; the figure, the record, the flags and the entries may not.
        moved = Counter()
        for row_id, b_row in b_user["bare"].items():
            a_row = a_user["bare"][row_id]
            for key in ("figure", "settled_figure",
                        "settled_on", "tracks_purchases", "visible_to_companion",
                        "is_deleted", "entries", "name", "status", "pay_period_id"):
                if b_row[key] != a_row[key]:
                    unexpected.append(f"bare {row_id}.{key}: {b_row[key]} -> {a_row[key]}")
            if b_row["template_id"] is None:
                if a_row["template_id"] is None:
                    unexpected.append(f"bare {row_id}: still link-less after")
                else:
                    moved["linked"] += 1
                if b_row["due_date"] is None:
                    moved["dated"] += 1
                elif b_row["due_date"] != a_row["due_date"]:
                    unexpected.append(f"bare {row_id}.due_date moved: {b_row['due_date']} -> {a_row['due_date']}")
                if a_row["occurs_on"] != a_row["due_date"]:
                    unexpected.append(f"bare {row_id}: occurs_on {a_row['occurs_on']} != due {a_row['due_date']}")
                if a_row["is_override"]:
                    unexpected.append(f"bare {row_id}: is_override after")
            elif b_row != a_row:
                unexpected.append(f"one-off {row_id} moved: {b_row} -> {a_row}")
        print(f"user {owner_id}: {moved['linked']} bare rows linked, {moved['dated']} dated")
        # The grid -- row keys, cells and figures per account -- and the
        # companion pages: byte-identical.  The grid's ``definitions`` count
        # is the cutover's own expected move and is printed, not graded.
        for acct, b_grid in b_user["grid"].items():
            a_grid = a_user["grid"][acct]
            for key in ("row_count", "income_rows", "expense_rows", "cells", "budgets"):
                if b_grid[key] != a_grid[key]:
                    unexpected.append(f"user {owner_id}.grid[{acct}].{key} differs")
            if b_grid["definitions"] != a_grid["definitions"]:
                print(
                    f"  grid account {acct}: definitions "
                    f"{b_grid['definitions']} -> {a_grid['definitions']}"
                )
        if b_user["companion"] != a_user["companion"]:
            unexpected.append(f"user {owner_id}.companion differs")
        # Timeliness: the ruled difference, per row.
        b_days, a_days = b_user["timeliness"]["days_per_row"], a_user["timeliness"]["days_per_row"]
        changed = {
            row_id: (b_days[row_id], a_days.get(row_id))
            for row_id in b_days if b_days[row_id] != a_days.get(row_id)
        }
        for row_id, (was, now) in sorted(changed.items(), key=lambda kv: int(kv[0])):
            print(f"  timeliness row {row_id}: days_paid_before_due {was} -> {now}")
        print(f"  timeliness metric: {b_user['timeliness']['metric']} -> {a_user['timeliness']['metric']}")
        newly_dated_paid = {
            row_id for row_id, b_row in b_user["bare"].items()
            if b_row["due_date"] is None and b_row["settled_on"] is not None
            and row_id in b_days
        }
        # EXACTLY the newly-dated settled expenses, both directions: a row
        # the cutover did not date may not move, and one it did must.
        if set(changed) != newly_dated_paid:
            unexpected.append(
                f"user {owner_id}: timeliness moved on {sorted(set(changed))}, "
                f"expected exactly the newly-dated settled expenses "
                f"{sorted(newly_dated_paid)}"
            )
        # Statements: flat in the one-off count.
        print(f"  grid statements: {b_user['statements']} -> {a_user['statements']}")
        print(f"  definitions: {b_user['definitions_total']} -> {a_user['definitions_total']}")
    if unexpected:
        print("UNEXPECTED DIFFERENCES:")
        for line in unexpected:
            print("  " + line)
        sys.exit(1)
    print("OK: every difference is the ruled one.")


def compare_downgrade(after_up_path, after_down_path):
    """Grade the downgrade: schema back, no row moved (ruling R-BAL67)."""
    with open(after_up_path, encoding="utf-8") as handle:
        after_up = json.load(handle)
    with open(after_down_path, encoding="utf-8") as handle:
        after_down = json.load(handle)
    unexpected = []
    schema = after_down["schema"]
    if schema["flag_columns"] != ["companion_visible", "is_envelope"]:
        unexpected.append(f"flag columns after downgrade: {schema['flag_columns']}")
    constraints = schema["constraints"]
    if not constraints.get("ck_transactions_one_pricing_link", "").endswith("<= 1))"):
        unexpected.append(f"CHECK after downgrade: {constraints.get('ck_transactions_one_pricing_link')}")
    for name, tail in (
        ("transactions_template_id_fkey", "ON DELETE SET NULL"),
        ("fk_transactions_credit_payback_for", "ON DELETE SET NULL"),
    ):
        if not constraints.get(name, "").endswith(tail):
            unexpected.append(f"{name} after downgrade: {constraints.get(name)}")
    if "fk_transactions_template_id" in constraints:
        unexpected.append("fk_transactions_template_id survived the downgrade")
    for table, up_rows in after_up["tables"].items():
        down_rows = after_down["tables"][table]
        if set(up_rows) != set(down_rows):
            unexpected.append(f"{table}: row ids differ")
            continue
        for row_id, up_row in up_rows.items():
            down_row = dict(down_rows[row_id])
            if table == "transactions":
                # The two cells the downgrade re-adds, at ``false``; the
                # definition table's two columns of the same name are real
                # and are compared like any other.
                cells = (down_row.pop("is_envelope"), down_row.pop("companion_visible"))
                if cells != (False, False):
                    unexpected.append(f"transactions {row_id}: re-added cells read {cells}")
            if down_row != up_row:
                unexpected.append(f"{table} {row_id} moved: {up_row} -> {down_row}")
        print(f"{table}: {len(up_rows)} rows, unmoved")
    if unexpected:
        print("UNEXPECTED DIFFERENCES:")
        for line in unexpected:
            print("  " + line)
        sys.exit(1)
    print("OK: schema restored under the prior names, no row moved.")


if __name__ == "__main__":
    if len(sys.argv) == 2:
        dump(sys.argv[1])
    elif len(sys.argv) == 4 and sys.argv[1] == "--compare":
        compare(sys.argv[2], sys.argv[3])
    elif len(sys.argv) == 4 and sys.argv[1] == "--compare-downgrade":
        compare_downgrade(sys.argv[2], sys.argv[3])
    else:
        sys.exit(
            "usage: verify_one_definition_cutover.py OUT.json | "
            "--compare BEFORE.json AFTER.json | "
            "--compare-downgrade AFTER_UP.json AFTER_DOWN.json"
        )
