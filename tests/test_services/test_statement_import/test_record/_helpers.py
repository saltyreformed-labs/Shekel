"""The helpers and constants every part of this package shares: the module-level helpers of
``tests/test_services/test_statement_import/test_record.py``, moved verbatim by ``tools/split``.
"""

from datetime import date
from decimal import Decimal

from sqlalchemy import text

from app.enums import StatementSourceEnum
from app.models.statement_import import StatementLineSighting
from app.services import account_service
from app.services.statement_import import record_statement
from app.models.ref import AccountType

from tests.test_services.test_statement_import import _csv_builder as build


_SOURCE = StatementSourceEnum.SECU_CHECKING_CSV


_ENTRIES = [
    (date(2026, 3, 2), "-25.00",
     "POINT OF SALE DEBIT L340 COFFEE (Big Cheese Clayton)"),
    (date(2026, 3, 3), "1500.00",
     "ACH DEPOSIT TOWN OF CLAYTON  PAYROLL (TOWN OF CLAYTON PAYROLL)"),
    (date(2026, 3, 4), "-40.81",
     "POINT OF SALE DEBIT L340 FOOD LION (Food Lion)"),
]


#: What SECU NAMES the merchant on each of those, in the same order -- the
#: parenthesised trailing token the adapter reads.  **Written out rather than
#: re-parsed here**: a fixture that re-ran the parse would move with it, so a
#: change to the adapter's rule would shift this list and its assertion
#: together and grade nothing.  The shared entries carry a token because every
#: one of the developer's 361 real lines does; a case that wants the ``None``
#: state says so with its own rows.
_MERCHANTS = ["Big Cheese Clayton", "TOWN OF CLAYTON PAYROLL", "Food Lion"]


def _file(entries=None, start="100.00", account_number=None):
    """Return a well-formed payload over *entries*."""
    kwargs = {}
    if account_number is not None:
        kwargs["account_number"] = account_number
    rows = build.chained(start, entries or _ENTRIES, **kwargs)
    return build.build(rows)


def _counts(db, account_id):
    """Return ``{import_id: (sighted, first)}`` -- the ONE derived count.

    What ``statement_imports.line_count`` / ``recorded_count`` stored until
    plan step ``bank_import:X-f6b-1``, read the way the imports table reads
    it.
    """
    return {
        import_id: (sighted, first)
        for import_id, sighted, first in db.session.execute(
            StatementLineSighting.counts_by_import(account_id),
        )
    }


def _audit_watermark(db):
    """Return the newest ``system.audit_log`` id, ``0`` while it is empty."""
    return db.session.execute(
        text("SELECT coalesce(max(id), 0) FROM system.audit_log"),
    ).scalar_one()


def _audited_writes_since(db, watermark):
    """Return ``{(table, operation): rows}`` the audit trigger logged after *watermark*.

    Args:
        db: The session fixture.
        watermark: An id :func:`_audit_watermark` returned.

    Returns:
        The count of each operation on each ``schema.table``, empty when
        nothing was written.
    """
    return {
        (f"{schema}.{table}", operation): count
        for schema, table, operation, count in db.session.execute(
            text(
                "SELECT table_schema, table_name, operation, count(*) "
                "FROM system.audit_log WHERE id > :watermark "
                "GROUP BY table_schema, table_name, operation"
            ),
            {"watermark": watermark},
        )
    }


def _record(seed_user, payload, file_name="statement.csv", account=None):
    """Run the door for the seeded user's checking account."""
    return record_statement(
        account_id=(account or seed_user["account"]).id,
        user_id=seed_user["user"].id,
        source=_SOURCE,
        file_name=file_name,
        payload=payload,
    )


def _second_account(db, seed_user):
    """Create a second cash account for the same owner."""
    checking_type = (
        db.session.query(AccountType).filter_by(name="Checking").one()
    )
    return account_service.create_account(
        account_service.AccountSpec(
            user_id=seed_user["user"].id,
            account_type_id=checking_type.id,
            name="Second Checking",
            anchor_balance=Decimal("0.00"),
            observed_on=date(2024, 1, 5),
        )
    )


#: Two same-day same-amount lines: the shape whose ordinal was the only term of
#: the identity key the bank never stated.  Measured across the developer's
#: 2026-07-19, 2026-08-16 and 2026-08-18 exports, 0 of 1,041 real lines shared a
#: ``(day, amount)`` with another -- so every case that reads this (they are
#: ``test_what_is_a_fact.py``'s) is PLANTED, and each is a firing control for
#: a refusal that used to fire on a file the bank had not restated at all.
_TWINS = [
    (date(2026, 3, 2), "-4.75",
     "POINT OF SALE DEBIT L340 STARBUCKS #123 (Starbucks)"),
    (date(2026, 3, 2), "-4.75",
     "POINT OF SALE DEBIT L340 DUNKIN #456 (Dunkin)"),
]


#: A line on a LATER day, so a refusal over the twins costs it too.  This is
#: what makes the cost of a false refusal visible: the whole file is refused,
#: not the disputed line.
_LATER = (date(2026, 3, 5), "-99.00",
          "POINT OF SALE DEBIT L340 ACE HARDWARE (Ace Hardware)")
