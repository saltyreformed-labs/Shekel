"""The helpers and constants every part of this package shares: the module-level helpers of
``tests/test_models/test_a_transfer_is_one_row.py``, moved verbatim by ``tools/split``.
"""

from __future__ import annotations

import contextlib
import io
import re

from sqlalchemy import text

from app.extensions import db as _db
from tests._test_helpers import (
    create_account_of_type,
    load_migration_module,
    run_migration_callable,
)


_MIGRATION = load_migration_module("82f7b7ed4332_a_transfer_is_one_row.py")


#: The revision below this one, whose downgrade runs next in a chain.
_BELOW = load_migration_module(
    "e616adf7fe22_a_transfer_side_s_payment_hangs_off_the_transfer.py"
)


#: Head's revision, which empties a side's record of the figure its transfer
#: holds.  A chain down to :data:`_BELOW` runs its downgrade first, which
#: copies each transfer's figure back onto its records (the shape every
#: revision below holds), and its upgrade last.
_ABOVE = load_migration_module("b7d2e4f19a63_a_side_record_holds_no_figure.py")


#: Every column of a twin row, read back as one tuple per twin.
_TWIN_ROWS_SQL = (
    "SELECT t.* FROM budget.transactions t "
    "WHERE t.transfer_id = ANY(:ids) ORDER BY t.id"
)


def _downgrade():
    """Run the shipped downgrade: every twin restored or built."""
    run_migration_callable(_MIGRATION.downgrade, _db.session)


def _upgrade():
    """Run the shipped upgrade: every twin kept and deleted."""
    run_migration_callable(_MIGRATION.upgrade, _db.session)


#: The downgrade's printed counts, every one of them, in the order it prints
#: them.
_DOWNGRADE_PRINT = re.compile(
    r"(?P<restored>\d+) kept twin\(s\) restored: (?P<rebuilt>\d+) in line "
    r"with a transfer written since; (?P<changed>\d+) of an unwritten "
    r"transfer differ from their kept row, saying what the transfer and its "
    r"side's record say now \((?P<unfit>\d+) of them because a kept value no "
    r"longer fits, ruling R-BAL261\); the rest exactly as kept\.  "
    r"(?P<built>\d+) built new for transfers holding none"
)


def _downgrade_counts():
    """Run the shipped downgrade and return its printed counts, by name.

    The print is read here, around the one call, so a case needs no capture
    fixture of its own.
    """
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        _downgrade()
    printed = _DOWNGRADE_PRINT.search(out.getvalue())
    assert printed is not None, "the downgrade printed no counts"
    return {name: int(value) for name, value in printed.groupdict().items()}


def _twin_rows(transfer_ids):
    """Return every twin of *transfer_ids*, every column, in id order."""
    return [
        dict(row) for row in _db.session.execute(
            text(_TWIN_ROWS_SQL), {"ids": list(transfer_ids)},
        ).mappings()
    ]


def _twin_of(transfer_id, *, income):
    """Return one side's twin of *transfer_id*, every column."""
    rows = [
        row for row in _twin_rows([transfer_id])
        if (row["account_id"] == _db.session.execute(text(
            "SELECT CASE WHEN :income THEN to_account_id "
            "ELSE from_account_id END FROM budget.transfers WHERE id = :t"
        ), {"income": income, "t": transfer_id}).scalar_one())
    ]
    assert len(rows) == 1, rows
    return rows[0]


def _transfer(transfer_id):
    """Return the transfer's columns."""
    return dict(_db.session.execute(text(
        "SELECT * FROM budget.transfers WHERE id = :t"
    ), {"t": transfer_id}).mappings().one())


def _status_id(name):
    """Return the ``ref.statuses`` id named *name*."""
    return _db.session.execute(text(
        "SELECT id FROM ref.statuses WHERE name = :n"
    ), {"n": name}).scalar_one()


def _parent_transfer_id():
    """Return the ``parent_transfer`` amount source's id."""
    return _db.session.execute(text(
        "SELECT id FROM ref.amount_sources WHERE name = 'parent_transfer'"
    )).scalar_one()


def _stale_income_twin(seed_user, xfer):
    """Give *xfer*'s to-side twin a category its transfer does not name.

    Finding BAL-575's shape: older income twins carried "Transfers: Incoming"
    in place of their transfer's own category.  Run at the downgraded state,
    where the twins exist.  Returns the stale category's id.
    """
    stale = seed_user["categories"]["Rent"].id
    _db.session.execute(text(
        "UPDATE budget.transactions SET category_id = :c WHERE id = :t"
    ), {"c": stale, "t": _twin_of(xfer.id, income=True)["id"]})
    _db.session.commit()
    return stale


def _accounts(seed_user):
    """Two savings accounts beside the seed checking account, committed."""
    savings = create_account_of_type(
        seed_user, _db.session, "Savings", "One Row Savings",
    )
    van = create_account_of_type(
        seed_user, _db.session, "Savings", "One Row Van Fund",
    )
    _db.session.commit()
    return savings, van


#: The fields the upgrade refuses a twin over, spelled HERE rather than read
#: off the migration, so a field the migration stops refusing fails its own
#: case: every field the app at ``e616adf7fe22`` still writes on both twins
#: with their transfer, save ``category_id``, which it writes there too but
#: which older income twins carry otherwise (finding BAL-575), so it is no copy
#: on production: it is neither refused for differing from its transfer's nor
#: re-read, the upgrade asking only that it be its owner's (ruling R-BAL261
#: answers the rest).
_COPIED = (
    "user_id", "account_id", "pay_period_id", "scenario_id", "due_date",
    "is_override", "is_deleted",
)


def _what_if_scenario(seed_user):
    """A second, non-baseline scenario of the seed user, by SQL; its id."""
    scenario = _db.session.execute(text(
        "INSERT INTO budget.scenarios (user_id, name, is_baseline) "
        "VALUES (:u, 'One Row What-if', FALSE) RETURNING id"
    ), {"u": seed_user["user"].id}).scalar_one()
    _db.session.commit()
    return scenario


def _new_category(user_id, group_name, item_name):
    """A category of *user_id* that no other row uses, by SQL; its id."""
    category = _db.session.execute(text(
        "INSERT INTO budget.categories (user_id, group_name, item_name) "
        "VALUES (:u, :g, :i) RETURNING id"
    ), {"u": user_id, "g": group_name, "i": item_name}).scalar_one()
    _db.session.commit()
    return category


def _second_owner(request):
    """The second user's ids: user, two accounts, a pay period and a scenario.

    A row's ``user_id`` cannot move alone: its accounts and its pay period are
    keyed to their owner with it (``fk_transactions_owner_account`` /
    ``fk_transactions_owner_period``, ``fk_transfers_owner_from_account`` /
    ``fk_transfers_owner_to_account``), so the owner drift moves them all.
    Read through ``request`` so only that case builds the second user.
    """
    second = request.getfixturevalue("seed_second_user")
    savings = create_account_of_type(
        second, _db.session, "Savings", "Second Owner Savings",
    )
    _db.session.commit()
    return {
        "user_id": second["user"].id,
        "checking": second["account"].id,
        "savings": savings.id,
        "pay_period_id": second["bootstrap_period"].id,
        "scenario_id": second["scenario"].id,
    }


def _set(table, row_id, values):
    """``UPDATE`` *values* onto one row of *table* by SQL, and commit."""
    _db.session.execute(text(
        f"UPDATE budget.{table} SET "
        + ", ".join(f"{column} = :{column}" for column in values)
        + " WHERE id = :row_id"
    ), {**values, "row_id": row_id})
    _db.session.commit()
