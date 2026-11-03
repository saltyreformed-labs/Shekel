"""A definition's price is its series alone: both default_amount columns go

Plan step X-bp (plan#1045), rulings R-IY and R-BAL16; closes findings N-446
(plan#1471) and N-450 (plan#1494).  ``budget.transaction_templates`` and
``budget.transfer_templates`` each stored ``default_amount``, a copy of the
newest price the definition's dated series states
(``budget.template_amount_versions``), or, for the two kinds that state no
price, a snapshot of a computation: a salary profile's definition (the
paycheck as of the last salary save) and a loan payment that follows its loan
(the payment as of its creation or its last Track).  Every reader was
re-pointed by this step's earlier tasks: the series prices a definition that
states its price, and the paycheck calculator or amount rule 4 answers live
for the two that do not.  The upgrade drops both columns and their CHECKs;
the series keeps both floors (``ck_template_amount_versions_nonneg_amount``,
``ck_template_amount_versions_transfer_positive_amount``).

**The downgrade is ruling plan#2695** (developer, 2026-11-02).  A production
rollback restores the dump the deploy takes first (``deploy/shekel-deploy.sh``),
so this runs in tests and by hand.  Each column comes back nullable, is
restored, then is made NOT NULL with its CHECK:

* a definition that states its price gets back its series' NEWEST version,
  which is exactly what the column held (``template_amount_service
  ._resync_scalar`` kept it there);
* a salary profile's definition (an ACTIVE profile names it) gets back its
  latest non-deleted Received row's recorded figure, the sum of that row's
  entries, ``$0.00`` for none (a settled row's entries are its record, and
  none is a close of nothing: ruling R-BAL82) -- the paycheck the bank or the
  owner recorded, which the old code read only on the paycheck's edit page
  and as the price an archive opened at, and which its next salary save
  rewrote;
* a loan payment that follows its loan, and a salary definition no Received
  row records, are REFUSED: no stored fact gives their copy back, and $0.00
  was rejected (the old code skips a $0 definition in the Recurring totals and
  the emergency fund, and a transfer cannot be $0).  The refusal changes
  nothing, names each definition, and gives the one line that states its copy
  in a by-hand run of these statements, before the NOT NULL.  The same
  refusal catches a definition that owns its amount with an empty series,
  which no write door produces.

Review: developer-ruled 2026-11-02 -- the step's spec (both columns go,
rulings R-IY / R-BAL16) and its design loop, answered "Proceed with the three
recommendations" (plan#1045's comment of that day; the downgrade is plan#2695).

Self-contained dependency policy: this migration imports nothing from ``app``.

Revision ID: 4d4eb9cd3850
Revises: 0703995c8536
Create Date: 2026-11-02
"""
from alembic import op
import sqlalchemy as sa


# Revision identifiers, used by Alembic.
revision = '4d4eb9cd3850'
# The head of ``dev`` this card was cut from, measured by
# alembic.script.ScriptDirectory.  Another lane landing a revision first makes
# two heads, which git merges cleanly and ``flask db upgrade`` refuses:
# re-point this at the measured head as the last edit before the pull request.
down_revision = '0703995c8536'
branch_labels = None
depends_on = None


#: The column this revision drops from both tables.
_COLUMN = 'default_amount'

#: Each table, the CHECK its column carried and that CHECK's SQL, stated once
#: for the upgrade, the downgrade and the refusal.
_TABLES = (
    (
        'transaction_templates', 'ck_transaction_templates_nonneg_amount',
        'default_amount >= 0',
    ),
    (
        'transfer_templates', 'ck_transfer_templates_positive_amount',
        'default_amount > 0',
    ),
)

#: A transaction template an ACTIVE salary profile names: the paycheck
#: calculator prices it, so its series is dormant
#: (``template_amount_service.is_salary_linked_template``).
_SALARY_LINKED = (
    "EXISTS (SELECT 1 FROM salary.salary_profiles sp "
    "WHERE sp.template_id = tt.id AND sp.is_active)"
)

#: A transfer template whose loan-payment settings follow the loan: amount
#: rule 4 prices it, so its series is dormant
#: (``template_amount_service.owns_its_amount``).
_FOLLOWS_ITS_LOAN = (
    "EXISTS (SELECT 1 FROM budget.loan_payment_settings lps "
    "WHERE lps.transfer_template_id = tt.id AND lps.derive_from_loan)"
)


def _newest_version_sql(table: str, owner_column: str, excluded: str) -> str:
    """The restore of every *table* definition that states its price.

    Its series' newest version: the latest ``effective_date``, ``id`` breaking
    a tie the partial unique indexes make unreachable.

    Args:
        table: ``transaction_templates`` or ``transfer_templates``.
        owner_column: The series' key naming that table.
        excluded: The predicate (over ``tt``) of the kind whose series is
            dormant, which this restore skips.

    Returns:
        The UPDATE statement.
    """
    return (
        f"UPDATE budget.{table} tt SET {_COLUMN} = v.amount "
        f"FROM (SELECT DISTINCT ON ({owner_column}) {owner_column}, amount "
        f"      FROM budget.template_amount_versions "
        f"      WHERE {owner_column} IS NOT NULL "
        f"      ORDER BY {owner_column}, effective_date DESC, id DESC) v "
        f"WHERE v.{owner_column} = tt.id AND NOT {excluded}"
    )


#: The restore of a salary profile's definition: its latest non-deleted
#: Received row's recorded figure, the sum of that row's entries.  A settled
#: row with no entry records ``$0.00`` (ruling R-BAL82), so ``SUM``'s ``NULL``
#: over no rows is that figure, not a missing one; a definition no Received
#: row names keeps ``NULL``, which the refusal names.
_LATEST_RECEIVED_SQL = (
    "UPDATE budget.transaction_templates tt SET default_amount = r.recorded "
    "FROM (SELECT DISTINCT ON (t.template_id) t.template_id, "
    "             (SELECT COALESCE(SUM(e.amount), 0) "
    "                FROM budget.transaction_entries e "
    "               WHERE e.transaction_id = t.id) AS recorded "
    "        FROM budget.transactions t "
    "        JOIN ref.statuses s ON s.id = t.status_id "
    "       WHERE s.name = 'Received' AND NOT t.is_deleted "
    "         AND t.template_id IS NOT NULL "
    "       ORDER BY t.template_id, t.due_date DESC, t.id DESC) r "
    f"WHERE r.template_id = tt.id AND {_SALARY_LINKED}"
)


def _unrestored_sql(table: str, check_sql: str, reason: str) -> str:
    """The *table* definitions whose restored copy its CHECK would refuse.

    ``IS NOT TRUE`` over the CHECK's own SQL, so a copy left ``NULL`` and one
    the CHECK refuses are one test, stated by the constraint it guards.

    Args:
        table: ``transaction_templates`` or ``transfer_templates``.
        check_sql: That table's CHECK on the column.
        reason: A CASE over ``tt`` naming why such a definition has no copy.

    Returns:
        A SELECT of ``(table, id, name, reason)``.
    """
    return (
        f"SELECT '{table}' AS tbl, tt.id, tt.name, {reason} AS reason "
        f"FROM budget.{table} tt WHERE ({check_sql}) IS NOT TRUE"
    )


#: Why a definition the restores left without a copy has none.
_TRANSACTION_REASON = (
    f"CASE WHEN {_SALARY_LINKED} THEN "
    "'a salary profile''s paycheck that no Received row records' "
    "ELSE 'a definition with no stated price' END"
)
_TRANSFER_REASON = (
    f"CASE WHEN {_FOLLOWS_ITS_LOAN} THEN "
    "'a loan payment that follows its loan' "
    "ELSE 'a definition with no stated price' END"
)


def restore_copies(bind) -> None:
    """Put each definition's copy back on its re-added, nullable column.

    Ruling plan#2695's two restores: the newest version of a definition that
    states its price, and a salary profile's latest Received figure.  A
    definition neither covers keeps ``NULL``, which :func:`unrestored`
    names.

    Args:
        bind: The migration's connection.
    """
    bind.execute(sa.text(_newest_version_sql(
        'transaction_templates', 'transaction_template_id', _SALARY_LINKED,
    )))
    bind.execute(sa.text(_LATEST_RECEIVED_SQL))
    bind.execute(sa.text(_newest_version_sql(
        'transfer_templates', 'transfer_template_id', _FOLLOWS_ITS_LOAN,
    )))


def unrestored(bind) -> list:
    """Return the definitions :func:`restore_copies` left without a valid copy.

    Args:
        bind: The migration's connection.

    Returns:
        ``(table, id, name, reason)`` rows, ordered by table then id.
    """
    (transactions, transaction_check), (transfers, transfer_check) = (
        (table, check_sql) for table, _name, check_sql in _TABLES
    )
    return list(bind.execute(sa.text(
        _unrestored_sql(transactions, transaction_check, _TRANSACTION_REASON)
        + " UNION ALL "
        + _unrestored_sql(transfers, transfer_check, _TRANSFER_REASON)
        + " ORDER BY tbl, id"
    )))


def readd_columns() -> None:
    """Re-add both columns, nullable and empty: the downgrade's first step.

    Module-level so a test of an OLDER revision that reads the column can put
    it back without :func:`restore_copies`: ``a9d3c15e7f42``'s backfill mines
    each series against the column, and the series is the restore's input.
    """
    for table, _check, _check_sql in _TABLES:
        op.add_column(
            table, sa.Column(_COLUMN, sa.Numeric(12, 2), nullable=True),
            schema='budget',
        )


def upgrade():
    """Drop both copies and their CHECKs; the series keeps both floors."""
    for table, check, _check_sql in _TABLES:
        op.drop_constraint(check, table, schema='budget', type_='check')
        op.drop_column(table, _COLUMN, schema='budget')


def downgrade():
    """Restore both copies (ruling plan#2695), or refuse and change nothing.

    Raises:
        RuntimeError: A definition no stored fact gives a copy back; the
            transaction rolls back, so the columns are not re-added.
    """
    bind = op.get_bind()
    readd_columns()
    restore_copies(bind)
    missing = unrestored(bind)
    if missing:
        lines = "\n".join(
            f"  {table} {row_id} ({name!r}), {reason}:\n"
            f"    UPDATE budget.{table} SET {_COLUMN} = <figure> "
            f"WHERE id = {row_id};"
            for table, row_id, name, reason in missing
        )
        diagnostics = "\n".join(
            f"  SELECT id, name FROM budget.{table} "
            f"WHERE ({check_sql}) IS NOT TRUE;"
            for table, _check, check_sql in _TABLES
        )
        raise RuntimeError(
            f"X-bp downgrade refused: {len(missing)} definition(s) have no "
            f"copy any stored fact gives back, and NOTHING has been changed.\n"
            f"{lines}\n"
            "To undo by hand, run this migration's downgrade statements in one "
            "transaction and, before the SET NOT NULL, state each copy with "
            "its line above: the payment as its loan last priced it, or the "
            "paycheck's net pay.  After the restores, these name what is "
            f"left:\n{diagnostics}"
        )
    for table, check, check_sql in _TABLES:
        op.alter_column(
            table, _COLUMN, existing_type=sa.Numeric(12, 2), nullable=False,
            schema='budget',
        )
        op.create_check_constraint(check, table, check_sql, schema='budget')
