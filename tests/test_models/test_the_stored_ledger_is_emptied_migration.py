"""Migration ``d913d743c78c``: the stored ledger's tables are emptied.

Plan step balance:X-ew's second leaf (plan#2914, task plan#3078), ruling
plan#3068.  The upgrade DELETEs every row of ``budget.account_postings``,
``budget.journal_entries`` and ``budget.ledger_accounts``, legs first, so each
table's audit trigger records each row.  The downgrade restores one linked
chart row per account, the one kind an earlier release's deploy hooks cannot
mint; the migration's docstring gives the measurement that says so.

The revision's own ``upgrade()`` and ``downgrade()`` run against the test
database, built at head, one clone per test.  Every row is planted through the
models or by SQL, never through a writer, so these tests grade the migration
whatever the app still writes.  That the migration is refused under
``shekel_app`` and runs as the owner was rehearsed on a production copy; the
commit that adds it records the run.
"""
from decimal import Decimal

import pytest
from sqlalchemy import bindparam, text
from sqlalchemy.exc import IntegrityError

from app import ref_cache
from app.enums import LedgerAccountClassEnum, LedgerAccountKindEnum
from app.models.account import Account
from app.models.ref import AccountType
from app.services.ledger_lines import linked_ledger_class
from tests._test_helpers import (
    create_account_of_type,
    load_migration_module,
    make_balanced_entry,
    run_migration_callable,
)

_MIGRATION = load_migration_module(
    "d913d743c78c_the_stored_ledger_s_tables_are_emptied.py",
)

#: The store's three tables, by the name ``system.audit_log`` records.
_TABLES = ("account_postings", "journal_entries", "ledger_accounts")


def _ids(session, table: str) -> set[int]:
    """Return every row id *table* holds."""
    return set(session.execute(
        text(f"SELECT id FROM budget.{table}"),
    ).scalars())


def _deleted_ids_since(session, table: str, mark: int) -> list[int]:
    """Return the row ids ``system.audit_log`` records DELETED from *table* after *mark*.

    A list, not a set, so a row recorded twice would show as two.
    """
    return sorted(session.execute(text(
        "SELECT row_id FROM system.audit_log "
        "WHERE id > :mark AND table_schema = 'budget' "
        "AND table_name = :table AND operation = 'DELETE'"
    ), {"mark": mark, "table": table}).scalars())


def _audit_mark(session) -> int:
    """Return the newest ``system.audit_log`` id, so later rows can be told apart."""
    return session.execute(
        text("SELECT COALESCE(MAX(id), 0) FROM system.audit_log"),
    ).scalar_one()


def _chart_row(session, user_id: int, kind, ledger_class, **columns) -> int:
    """Insert one chart row of *kind* and *ledger_class* by SQL; return its id.

    By SQL rather than the model, as the migration itself reads and writes the
    table, so nothing of the store's model layer stands between the plant and
    the statement under test.
    """
    values = {
        "user_id": user_id,
        "class_id": ref_cache.ledger_account_class_id(ledger_class),
        "kind_id": ref_cache.ledger_account_kind_id(kind),
        **columns,
    }
    return session.execute(text(
        f"INSERT INTO budget.ledger_accounts ({', '.join(values)}) "
        f"VALUES ({', '.join(f':{name}' for name in values)}) RETURNING id"
    ), values).scalar_one()


def _chart_rows_by_account(session) -> dict[int, tuple]:
    """Return every chart row as ``{account_id: (id, user_id, kind_id, class_id, rest)}``.

    ``rest`` is ``(name, category_id, loan_account_id, is_owner_bucket)``.  A
    second row on one account would collapse here, so callers also count rows.
    """
    return {
        row.account_id: (
            row.id, row.user_id, row.kind_id, row.class_id,
            (row.name, row.category_id, row.loan_account_id, row.is_owner_bucket),
        )
        for row in session.execute(text(
            "SELECT id, user_id, kind_id, class_id, account_id, name, "
            "category_id, loan_account_id, is_owner_bucket "
            "FROM budget.ledger_accounts"
        ))
    }


def _expected_linked_rows(session) -> dict[int, tuple[int, int, int]]:
    """Return ``{account_id: (user_id, kind_id, class_id)}`` an account's own row carries.

    The class by the app's own rule (``ledger_lines.linked_ledger_class``) over
    the account's type's category, the oracle the migration's frozen SQL is
    graded against.
    """
    linked = ref_cache.ledger_account_kind_id(LedgerAccountKindEnum.LINKED)
    return {
        account.id: (
            account.user_id, linked,
            ref_cache.ledger_account_class_id(linked_ledger_class(
                session.get(AccountType, account.account_type_id).category_id,
            )),
        )
        for account in session.query(Account)
    }


class TestTheRevision:
    """Where the revision sits and what it owes the rules."""

    def test_revision_and_down_revision(self):
        """revision / down_revision pin the migration into the chain."""
        assert _MIGRATION.revision == "d913d743c78c"
        assert _MIGRATION.down_revision == "897a6663d221"

    def test_it_is_reviewed_under_its_ruling(self):
        """A destructive migration carries the ``Review:`` line, naming plan#3068."""
        review = [
            line for line in _MIGRATION.__doc__.splitlines()
            if line.startswith("Review:")
        ]
        assert len(review) == 1
        assert "plan#3068" in review[0]

    def test_the_legs_go_first_and_the_chart_rows_last(self):
        """A leg names its entry and its chart row, so it is deleted before either."""
        assert _MIGRATION.EMPTIED_TABLES == _TABLES


class TestNothingElseHoldsAKeyOntoTheStore:
    """The DELETEs can cascade into, and be refused by, these three tables alone."""

    def test_every_key_onto_the_three_tables_is_one_of_theirs(self, app, db):
        """Each foreign key referencing a store table is held by a store table.

        Read from the catalog, so a key added onto the store from any other
        table fails here: the upgrade would then delete through it, or be
        refused by it, and its docstring says neither can happen.
        """
        with app.app_context():
            holders = db.session.execute(text(
                "SELECT c.conname, held.relname AS holder, "
                "       held_ns.nspname AS holder_schema, "
                "       target.relname AS target "
                "  FROM pg_constraint c "
                "  JOIN pg_class held ON held.oid = c.conrelid "
                "  JOIN pg_namespace held_ns ON held_ns.oid = held.relnamespace "
                "  JOIN pg_class target ON target.oid = c.confrelid "
                "  JOIN pg_namespace target_ns ON target_ns.oid = target.relnamespace "
                " WHERE c.contype = 'f' AND target_ns.nspname = 'budget' "
                "   AND target.relname IN :tables"
            ).bindparams(
                bindparam("tables", expanding=True),
            ), {"tables": list(_TABLES)}).all()

            assert holders, "the catalog read found no key at all, so it measured nothing"
            outside = [
                row for row in holders
                if (row.holder_schema, row.holder) not in
                {("budget", table) for table in _TABLES}
            ]
            assert outside == []
            # The store's own keys onto itself: a leg onto its entry and its row.
            assert {(row.holder, row.target) for row in holders} == {
                ("account_postings", "journal_entries"),
                ("account_postings", "ledger_accounts"),
            }


class TestTheUpgrade:
    """Every row of the three tables goes, and the audit log records each one."""

    def test_it_empties_all_three_and_the_audit_log_keeps_each_row(
        self, app, db, seed_user,
    ):
        """Planted entries, legs and chart rows of four kinds are each deleted once.

        Arithmetic: two entries of two legs each, so 2 entries and 4 legs,
        plus whatever the fixtures' own writes left; every id present before
        the upgrade is recorded DELETED exactly once after it.
        """
        with app.app_context():
            user_id = seed_user["user"].id
            mortgage = create_account_of_type(
                seed_user, db.session, "Mortgage", "Emptied Mortgage",
            )
            transit = _chart_row(
                db.session, user_id, LedgerAccountKindEnum.TRANSIT,
                LedgerAccountClassEnum.ASSET,
                is_owner_bucket=True, name="Transfers in transit",
            )
            interest = _chart_row(
                db.session, user_id, LedgerAccountKindEnum.LOAN_INTEREST,
                LedgerAccountClassEnum.EXPENSE,
                loan_account_id=mortgage.id, name="Emptied Mortgage -- Interest",
            )
            fallback = _chart_row(
                db.session, user_id, LedgerAccountKindEnum.FALLBACK,
                LedgerAccountClassEnum.EXPENSE,
                is_owner_bucket=True, name="Uncategorized Expense",
            )
            db.session.commit()
            make_balanced_entry(
                db.session, seed_user, from_ledger_id=transit,
                to_ledger_id=fallback, amount=Decimal("42.17"),
            )
            make_balanced_entry(
                db.session, seed_user, from_ledger_id=transit,
                to_ledger_id=interest, amount=Decimal("83.33"),
            )
            before = {table: _ids(db.session, table) for table in _TABLES}
            assert len(before["journal_entries"]) >= 2
            assert len(before["account_postings"]) >= 4
            assert {transit, interest, fallback} <= before["ledger_accounts"]
            mark = _audit_mark(db.session)

            run_migration_callable(_MIGRATION.upgrade, db.session)
            db.session.commit()

            for table in _TABLES:
                assert _ids(db.session, table) == set(), table
                assert _deleted_ids_since(db.session, table, mark) == sorted(
                    before[table],
                ), table

    def test_a_loan_its_chart_rows_held_can_be_deleted_after_it(
        self, app, db, seed_user,
    ):
        """The ruling's case: a loan's chart row refuses the loan's delete, then is gone.

        ``fk_ledger_accounts_loan_account_id`` is RESTRICT, so before the
        upgrade the database refuses to delete the account the loan's
        interest row names; after it, nothing names the account and the
        same statement deletes it.
        """
        with app.app_context():
            mortgage = create_account_of_type(
                seed_user, db.session, "Mortgage", "Held Mortgage",
            )
            mortgage_id = mortgage.id
            _chart_row(
                db.session, seed_user["user"].id,
                LedgerAccountKindEnum.LOAN_INTEREST,
                LedgerAccountClassEnum.EXPENSE,
                loan_account_id=mortgage_id, name="Held Mortgage -- Interest",
            )
            db.session.commit()
            delete = text("DELETE FROM budget.accounts WHERE id = :id")

            with pytest.raises(
                IntegrityError, match="fk_ledger_accounts_loan_account_id",
            ):
                with db.session.begin_nested():
                    db.session.execute(delete, {"id": mortgage_id})

            run_migration_callable(_MIGRATION.upgrade, db.session)
            assert db.session.execute(delete, {"id": mortgage_id}).rowcount == 1
            db.session.commit()
            assert db.session.get(Account, mortgage_id) is None


#: An account type of each category, and the class its own row carries, by hand:
#: only borrowed money is a liability on the books.
_TYPES_AND_CLASSES = (
    ("Checking", LedgerAccountClassEnum.ASSET),         # Asset category
    ("Credit Card", LedgerAccountClassEnum.LIABILITY),  # Liability category
    ("Mortgage", LedgerAccountClassEnum.LIABILITY),     # Liability category
    ("401(k)", LedgerAccountClassEnum.ASSET),           # Retirement category
    ("Brokerage", LedgerAccountClassEnum.ASSET),        # Investment category
)


class TestTheDowngrade:
    """One linked chart row per account comes back, and nothing else."""

    def test_each_account_gets_its_own_row_and_one_already_paired_keeps_its(
        self, app, db, seed_user, seed_second_user,
    ):
        """Every account of both owners is paired once, classed by its type's category.

        The second owner's checking account is paired before the downgrade, as
        a tree still pairing at account create would leave it: it keeps that
        row, id and all, and gains no second.  Every other account gets a row
        carrying its owner, the ``linked`` kind, the class its type's category
        names (stated by hand, and equal to the app's own rule,
        ``ledger_lines.linked_ledger_class``), no name, no category, no loan
        and no owner-bucket flag.  No entry, leg or other kind comes back.
        """
        with app.app_context():
            accounts = {
                type_name: create_account_of_type(
                    seed_user, db.session, type_name, f"Paired {type_name}",
                ).id
                for type_name, _ in _TYPES_AND_CLASSES
            }
            run_migration_callable(_MIGRATION.upgrade, db.session)
            second_checking = seed_second_user["account"]
            kept = _chart_row(
                db.session, second_checking.user_id,
                LedgerAccountKindEnum.LINKED, LedgerAccountClassEnum.ASSET,
                account_id=second_checking.id,
            )
            db.session.commit()

            run_migration_callable(_MIGRATION.downgrade, db.session)
            db.session.commit()

            rows = _chart_rows_by_account(db.session)
            expected = _expected_linked_rows(db.session)
            assert len(_ids(db.session, "ledger_accounts")) == len(expected)
            assert {
                account_id: row[1:4] for account_id, row in rows.items()
            } == expected
            assert {row[4] for row in rows.values()} == {(None, None, None, False)}
            for type_name, ledger_class in _TYPES_AND_CLASSES:
                assert rows[accounts[type_name]][3] == (
                    ref_cache.ledger_account_class_id(ledger_class)
                ), type_name
            assert rows[second_checking.id][0] == kept
            for table in ("account_postings", "journal_entries"):
                assert _ids(db.session, table) == set(), table

    def test_up_down_up_leaves_the_store_empty_again(self, app, db, seed_user):
        """The upgrade after a downgrade deletes the restored rows too.

        The round trip a re-deploy of this revision takes: what the downgrade
        put back is a chart row like any other, and goes.
        """
        with app.app_context():
            run_migration_callable(_MIGRATION.upgrade, db.session)
            run_migration_callable(_MIGRATION.downgrade, db.session)
            restored = _ids(db.session, "ledger_accounts")
            assert len(restored) == db.session.query(Account).count()
            assert seed_user["account"].id in _chart_rows_by_account(db.session)

            run_migration_callable(_MIGRATION.upgrade, db.session)
            db.session.commit()

            for table in _TABLES:
                assert _ids(db.session, table) == set(), table
