"""Tests for the efca4315bf81 ledger-account kind/loan schema migration.

Build-Order Step 4, Commit 2
(``docs/audits/balance_architecture/implementation_plan_posting_ledger_loan_payments.md``).
The migration turns the implicit NULL-pattern row taxonomy of
``budget.ledger_accounts`` into an explicit ``kind_id`` discriminator and adds
the per-loan link:

  * ``kind_id`` -- a NOT NULL FK (RESTRICT) into ``ref.ledger_account_kinds``,
    added in three steps (add nullable, backfill from each row's column shape,
    tighten to NOT NULL);
  * ``loan_account_id`` -- a NULLABLE FK (RESTRICT) into ``budget.accounts``;
  * ``uq_ledger_accounts_loan`` -- partial unique on
    ``(user_id, loan_account_id, kind_id)`` WHERE ``loan_account_id IS NOT
    NULL``;
  * ``ck_ledger_accounts_loan_shape`` -- a column-shape CHECK confining a
    per-loan row to ``account_id`` / ``category_id`` NULL and ``NOT
    is_fallback`` -- spelled ``NOT is_owner_bucket`` at HEAD since plan step
    ``balance:X-bi-6-3``'s migration ``c7d1e9a4b2f8`` renamed the flag
    (ruling R-BAL99); the stored CHECK followed the rename by itself.

The migration is already at HEAD when these tests run (the template builder
upgraded base->head), so the per-worker DB shows the post-migration schema.
These tests assert, without re-executing DDL in the worker:

  * the migration is correctly chained (revision / down_revision);
  * the two columns exist with the right type / nullability, the two FKs are
    ON DELETE RESTRICT, and the partial unique + the shape CHECK exist with the
    model's exact predicates -- the proof that ``upgrade`` ran and matches the
    model (the behavioural proof that each constraint *enforces* its rule lives
    in ``test_ledger_account.py``; this file proves the named objects exist so
    the downgrade's drop-by-name will find them and that there is no
    model-vs-migration drift);
  * the three-step backfill's shape -> kind mapping (the
    ``_KIND_FROM_SHAPE_CASE_SQL`` constant) assigns the correct kind to a row of
    every shape -- evaluated as a SELECT because at HEAD ``kind_id`` is NOT NULL
    so the backfill UPDATE's ``WHERE kind_id IS NULL`` guard can never match a
    real row;
  * the ``downgrade`` is not a bare pass -- it drops every object the
    ``upgrade`` adds.

A full executable upgrade -> downgrade round-trip belongs in the Alembic-driven
environment, not an in-test xdist worker (executing the downgrade here would
DROP columns the whole session's ORM depends on, breaking every other test in
the worker).  The executable round-trip was run during development against the
prod-clone dev DB -- ``flask db upgrade`` created the objects, a re-run of
``flask db migrate`` produced an empty diff (model == migration), and ``flask
db downgrade`` removed them cleanly, returning the DB to the Commit-1 head -- so
the source-level downgrade check below is the safe in-worker analogue (the same
split the cash-posting schema migration test uses).
"""
from __future__ import annotations

import pathlib

from sqlalchemy import text

from app import ref_cache
from app.enums import LedgerAccountClassEnum, LedgerAccountKindEnum
from app.extensions import db as _db
from app.models.ledger_account import LedgerAccount
from tests._stored_ledger import chart_row_or_plant
from tests._test_helpers import create_account_of_type, load_migration_module


_MIGRATIONS_DIR = (
    pathlib.Path(__file__).resolve().parents[2] / "migrations" / "versions"
)
_MIGRATION_FILENAME = (
    "efca4315bf81_add_ledger_accounts_kind_id_and_loan_.py"
)
_MIGRATION = load_migration_module(_MIGRATION_FILENAME)


def _kind_id(member):
    """Resolve a LedgerAccountKindEnum member to its integer PK."""
    return ref_cache.ledger_account_kind_id(member)


def _class_id(member):
    """Resolve a LedgerAccountClassEnum member to its integer PK."""
    return ref_cache.ledger_account_class_id(member)


class TestMigrationRevisionPair:
    """The migration chains off the Step-4 Commit-1 ref head."""

    def test_revision_pair(self):
        """revision / down_revision pin the migration into the chain."""
        assert _MIGRATION.revision == "efca4315bf81"
        assert _MIGRATION.down_revision == "f8e025a8be41"


class TestMigratedColumns:
    """The two new columns exist with the right type and nullability."""

    def test_kind_id_column_not_null(self, app, db):
        """``ledger_accounts.kind_id`` is a NOT NULL integer at HEAD."""
        with app.app_context():
            row = db.session.execute(text(
                "SELECT data_type, is_nullable "
                "FROM information_schema.columns "
                "WHERE table_schema = 'budget' "
                "  AND table_name = 'ledger_accounts' "
                "  AND column_name = 'kind_id'"
            )).fetchone()
            assert row is not None, "kind_id column missing at HEAD"
            assert row[0] == "integer"
            assert row[1] == "NO", "kind_id must be NOT NULL after the backfill"

    def test_loan_account_id_column_nullable(self, app, db):
        """``ledger_accounts.loan_account_id`` is a nullable integer at HEAD."""
        with app.app_context():
            row = db.session.execute(text(
                "SELECT data_type, is_nullable "
                "FROM information_schema.columns "
                "WHERE table_schema = 'budget' "
                "  AND table_name = 'ledger_accounts' "
                "  AND column_name = 'loan_account_id'"
            )).fetchone()
            assert row is not None, "loan_account_id column missing at HEAD"
            assert row[0] == "integer"
            assert row[1] == "YES"


class TestMigratedForeignKeys:
    """Both new FKs are ON DELETE RESTRICT.

    ``pg_constraint.confdeltype = 'r'`` is the RESTRICT delete action; any
    other value would break the seeded-kind invariant (kind_id) or let a loan
    account with per-loan ledger rows be deleted and orphan their postings
    (loan_account_id).
    """

    def _confdeltype(self, db, conname):
        """Return the FK delete action code for *conname*."""
        return db.session.execute(text(
            "SELECT confdeltype FROM pg_constraint WHERE conname = :n"
        ), {"n": conname}).scalar()

    def test_kind_fk_is_restrict(self, app, db):
        """``fk_ledger_accounts_kind_id`` is ON DELETE RESTRICT."""
        with app.app_context():
            deltype = self._confdeltype(db, "fk_ledger_accounts_kind_id")
            assert deltype == "r", (
                "fk_ledger_accounts_kind_id must be RESTRICT (confdeltype "
                f"'r'); found {deltype!r}"
            )

    def test_loan_account_fk_is_restrict(self, app, db):
        """``fk_ledger_accounts_loan_account_id`` is ON DELETE RESTRICT."""
        with app.app_context():
            deltype = self._confdeltype(
                db, "fk_ledger_accounts_loan_account_id",
            )
            assert deltype == "r", (
                "fk_ledger_accounts_loan_account_id must be RESTRICT "
                f"(confdeltype 'r'); found {deltype!r}"
            )


class TestMigratedLoanUniqueAndCheck:
    """The per-loan partial unique and the shape CHECK exist as declared."""

    def test_loan_unique_index(self, app, db):
        """``uq_ledger_accounts_loan`` is unique on the right key + predicate.

        The ``pg_indexes.indexdef`` carries the full CREATE INDEX text including
        the partial ``WHERE`` predicate, so a wrong key or predicate (which
        would change which rows the unique constrains) is caught here.
        """
        with app.app_context():
            ddl = db.session.execute(text(
                "SELECT indexdef FROM pg_indexes "
                "WHERE schemaname = 'budget' AND indexname = "
                "'uq_ledger_accounts_loan'"
            )).scalar()
            assert ddl is not None, "uq_ledger_accounts_loan missing"
            assert "UNIQUE INDEX" in ddl
            assert "user_id, loan_account_id, kind_id" in ddl
            assert "loan_account_id IS NOT NULL" in ddl

    def test_loan_shape_check(self, app, db):
        """``ck_ledger_accounts_loan_shape`` is present with the model predicate.

        A per-loan (``loan_account_id`` set) row must carry no account / category
        link and not be the fallback.  The CHECK deliberately does NOT also
        reference ``kind_id`` (a CHECK cannot subquery the kinds ref table and
        the project forbids hardcoding its IDs), so asserting ``kind_id`` is
        ABSENT from the predicate guards against a regression that re-introduces
        a hardcoded-ID coupling.
        """
        with app.app_context():
            ddl = db.session.execute(text(
                "SELECT pg_get_constraintdef(oid) FROM pg_constraint "
                "WHERE conname = 'ck_ledger_accounts_loan_shape'"
            )).scalar()
            assert ddl is not None, "loan-shape CHECK missing at HEAD"
            assert "loan_account_id IS NULL" in ddl
            assert "account_id IS NULL" in ddl
            assert "category_id IS NULL" in ddl
            # The flag's HEAD name (``is_fallback`` until ``c7d1e9a4b2f8``).
            assert "is_owner_bucket" in ddl
            assert "kind_id" not in ddl, (
                "the loan-shape CHECK must not reference kind_id -- a CHECK "
                "cannot subquery ref.ledger_account_kinds and the project "
                "forbids hardcoding its IDs (the sole writer guarantees the "
                "kind)"
            )


class TestBackfillShapeMapping:
    """The three-step backfill's shape -> kind CASE maps every shape correctly.

    At HEAD ``kind_id`` is NOT NULL, so the backfill UPDATE's ``WHERE kind_id IS
    NULL`` guard can never match a real row; the mapping logic is instead
    exercised by evaluating the migration's ``_KIND_FROM_SHAPE_CASE_SQL``
    constant as a SELECT over a row of each shape.  For every shape the test
    asserts BOTH that the CASE derives the expected kind AND that it agrees with
    the kind the sole writer already stamped (the backfill == go-forward
    invariant: a historical row backfilled from its shape lands on the same kind
    the live writer assigns).
    """

    def _derived_and_stored_kind(self, db, row_id):
        """Return (backfill-CASE-derived kind id, stored kind_id) for a row.

        The frozen CASE names the flag by its own-revision name,
        ``is_fallback``; the column is ``is_owner_bucket`` at HEAD since plan
        step ``balance:X-bi-6-3``'s migration ``c7d1e9a4b2f8`` (ruling
        R-BAL99), so the SELECT re-homes that one identifier -- the CASE's
        logic over the four shapes it was written for is what this evaluates,
        and the frozen text stays the single source of it.
        """
        frozen_flag = "WHEN is_fallback "
        assert frozen_flag in _MIGRATION._KIND_FROM_SHAPE_CASE_SQL, (
            "the frozen efca4315bf81 CASE changed; update the anchor here"
        )
        case_sql = _MIGRATION._KIND_FROM_SHAPE_CASE_SQL.replace(
            frozen_flag, "WHEN is_owner_bucket ",
        )
        row = db.session.execute(text(
            f"SELECT ({case_sql}) AS derived, "
            "kind_id AS stored "
            "FROM budget.ledger_accounts WHERE id = :id"
        ), {"id": row_id}).fetchone()
        return row[0], row[1]

    def test_linked_shape_maps_to_linked(self, app, db, seed_user):
        """An ``account_id``-bearing row derives (and was stamped) ``linked``."""
        with app.app_context():
            account = create_account_of_type(
                seed_user, _db.session, "Checking", "Kind Map Linked",
            )
            # Planted: no door pairs an account's linked row since plan task
            # plan#3133.
            linked_id = chart_row_or_plant(
                _db.session, account, "linked", "Asset",
            )
            derived, stored = self._derived_and_stored_kind(db, linked_id)
            assert derived == _kind_id(LedgerAccountKindEnum.LINKED)
            assert stored == derived

    def test_category_shape_maps_to_category(self, app, db, seed_user):
        """A ``category_id``-bearing row derives (and was stamped) ``category``."""
        with app.app_context():
            user_id = seed_user["user"].id
            groceries = seed_user["categories"]["Groceries"]
            row = LedgerAccount(
                user_id=user_id,
                class_id=_class_id(LedgerAccountClassEnum.EXPENSE),
                kind_id=_kind_id(LedgerAccountKindEnum.CATEGORY),
                account_id=None, category_id=groceries.id,
                name=groceries.display_name,
            )
            _db.session.add(row)
            _db.session.commit()
            derived, stored = self._derived_and_stored_kind(db, row.id)
            assert derived == _kind_id(LedgerAccountKindEnum.CATEGORY)
            assert stored == derived

    def test_fallback_shape_maps_to_fallback(self, app, db, seed_user):
        """A flagged NULL/NULL row derives (and was stamped) ``fallback``.

        The flag is ``is_owner_bucket`` at HEAD (``is_fallback`` at the
        migration's own revision; plan step ``balance:X-bi-6-3``).
        """
        with app.app_context():
            user_id = seed_user["user"].id
            row = LedgerAccount(
                user_id=user_id,
                class_id=_class_id(LedgerAccountClassEnum.EXPENSE),
                kind_id=_kind_id(LedgerAccountKindEnum.FALLBACK),
                account_id=None, category_id=None, is_owner_bucket=True,
                name="Uncategorized Expense",
            )
            _db.session.add(row)
            _db.session.commit()
            derived, stored = self._derived_and_stored_kind(db, row.id)
            assert derived == _kind_id(LedgerAccountKindEnum.FALLBACK)
            assert stored == derived

    def test_orphan_shape_maps_to_orphan(self, app, db, seed_user):
        """A NULL/NULL non-fallback row derives (and was stamped) ``orphan``.

        The CASE's ELSE branch -- the residual shape a deleted-category orphan
        leaves -- must map to ``orphan``, distinct from the flagged
        fallback that shares the NULL/NULL columns.
        """
        with app.app_context():
            user_id = seed_user["user"].id
            row = LedgerAccount(
                user_id=user_id,
                class_id=_class_id(LedgerAccountClassEnum.EXPENSE),
                kind_id=_kind_id(LedgerAccountKindEnum.ORPHAN),
                account_id=None, category_id=None, is_owner_bucket=False,
                name="Family: Groceries",
            )
            _db.session.add(row)
            _db.session.commit()
            derived, stored = self._derived_and_stored_kind(db, row.id)
            assert derived == _kind_id(LedgerAccountKindEnum.ORPHAN)
            assert stored == derived


class TestDowngradeReversible:
    """downgrade() is a real revert, not a bare pass.

    A source-level check (the executable round-trip is out of scope for the
    xdist worker -- see the module docstring) guards against a future edit
    silently dropping one of the reverts the upgrade's additions require, which
    would leave an orphaned column / index / constraint on a downgrade -- the
    bare-pass downgrade failure mode the coding standard forbids.
    """

    def test_downgrade_drops_every_added_object(self):
        """The downgrade source drops each object the upgrade adds.

        The value check is scoped to the text from ``def downgrade`` onward so
        an object is credited only if it is named in a drop, not merely echoed
        by the upgrade's earlier creates.
        """
        source = (_MIGRATIONS_DIR / _MIGRATION_FILENAME).read_text()
        downgrade_section = source[source.find("def downgrade"):]
        assert downgrade_section, "no downgrade() in the migration source"
        # Both columns dropped by name (table + column matched precisely).
        for column in ("loan_account_id", "kind_id"):
            assert (
                f"drop_column('ledger_accounts', '{column}'"
                in downgrade_section
            ), f"downgrade() never drops the ledger_accounts.{column} column"
        # The index dropped.
        assert "'uq_ledger_accounts_loan'" in downgrade_section, (
            "downgrade() never drops the uq_ledger_accounts_loan index"
        )
        # Both FKs and the CHECK dropped.
        for constraint in (
            "ck_ledger_accounts_loan_shape",
            "fk_ledger_accounts_loan_account_id",
            "fk_ledger_accounts_kind_id",
        ):
            assert f"'{constraint}'" in downgrade_section, (
                f"downgrade() never drops the {constraint} constraint"
            )
