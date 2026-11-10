"""Tests for the b82538084d24 ledger-accounts migration backfill.

Build-Order Step 2, Commit 2
(``docs/audits/balance_architecture/implementation_plan_posting_ledger_transfers.md``).
The migration creates ``budget.ledger_accounts`` then backfills one linked
ledger account for every existing real account, deriving the ledger class
from the account-type category.

The migration is already at HEAD when these tests run (the template
builder upgraded it base->head against an EMPTY ``budget.accounts`` table,
so the in-chain backfill was a no-op).  Each test therefore sets up a new
account, removes the ledger account the go-forward sync hook auto-created,
then invokes the migration's idempotent backfill SQL directly to assert the
deterministic mapping from account-type category to ledger class -- exactly
the pattern the ``loan_anchor_events`` backfill test
(``tests/test_models/test_loan_anchor_backfill.py``) uses.

**Post-Step-4 adaptation (``kind_id``).**  Step 4, Commit 2
(``efca4315bf81``) added a NOT NULL ``ledger_accounts.kind_id`` discriminator,
so the frozen b82 INSERT -- which predates that column and omits it -- can no
longer run standalone at HEAD.  In production the chain ran the b82 INSERT at
its own revision (before ``kind_id`` existed) and the Step-4 migration then
backfilled ``kind_id = 'linked'`` for every ``account_id``-bearing row; at
HEAD those two are fused because ``kind_id`` is already NOT NULL.
:func:`_head_valid_backfill_sql` reproduces exactly that fusion -- it reuses
the frozen (immutable, shipped) b82 mapping SQL as the single source of the
category -> class derivation and injects only the linked-kind value the Step-4
backfill would assign.  The category-mapping assertions below are unchanged;
they still execute the b82 derivation, now against the post-Step-4 schema.

A full executable upgrade -> downgrade -> upgrade round-trip belongs in the
Alembic-driven environment, not an in-test xdist worker (a ``DROP TABLE``
needs an ACCESS EXCLUSIVE lock that conflicts with the session-scoped
``ref_cache`` refresh's ACCESS SHARE locks).  The downgrade is checked at
source level here; the executable round-trip was run manually against the
prod-clone dev DB during development (downgrade dropped the table and its
trigger; re-upgrade regenerated all 10 backfilled rows, 8 Asset / 2
Liability).
"""
from __future__ import annotations

import importlib.util
import pathlib
from decimal import Decimal

from app import ref_cache
from app.enums import LedgerAccountClassEnum
from app.extensions import db as _db
from tests._stored_ledger import ledger_accounts_for_account
from tests._test_helpers import create_account_of_type


# ---------------------------------------------------------------------------
# Migration module loader -- importlib pattern from the loan-anchor /
# posting-ledger-ref migration tests (migrations/versions has no __init__).
# ---------------------------------------------------------------------------


_MIGRATIONS_DIR = (
    pathlib.Path(__file__).resolve().parents[2] / "migrations" / "versions"
)

_MIGRATION_FILENAME = (
    "b82538084d24_create_budget_ledger_accounts_chart_of_.py"
)


def _load_migration(filename):
    """Load an Alembic migration module by path via importlib."""
    path = _MIGRATIONS_DIR / filename
    spec = importlib.util.spec_from_file_location(path.stem, str(path))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_MIGRATION = _load_migration(_MIGRATION_FILENAME)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _drop_ledger_account_for(account_id):
    """Remove the ledger account the sync hook auto-created, via raw SQL.

    Leaves the account itself unpaired so the migration's backfill (which
    skips already-paired accounts) has work to do.
    """
    _db.session.execute(_db.text(
        "DELETE FROM budget.ledger_accounts WHERE account_id = :a"
    ), {"a": account_id})
    _db.session.commit()


# The Step-4 ``ledger_accounts.kind_id`` subquery the b82 linked-row backfill
# would be paired with at HEAD (see the module docstring): every account_id
# bearing row is the ``linked`` kind.
_LINKED_KIND_SUBQUERY = (
    "(SELECT id FROM ref.ledger_account_kinds WHERE name = 'linked')"
)


def _head_valid_backfill_sql():
    """Return the frozen b82 linked-row backfill adapted for NOT NULL kind_id.

    Injects the ``kind_id`` column and the linked-kind subquery into the frozen,
    immutable ``_BACKFILL_LEDGER_ACCOUNTS_SQL`` so the historical INSERT runs
    against the post-Step-4 schema (where ``kind_id`` is NOT NULL).  The two
    string replacements are anchored on the exact shipped text of the constant,
    which never changes (it is locked migration history), reproducing the
    cumulative production effect (b82 insert + Step-4 kind backfill) the module
    docstring describes -- not a hand-rewritten copy of the mapping.  Asserts the
    transform fired (mirroring the shared 7d63 ``_inject_pass_a_kind`` helper) so
    a future change to the shipped constant fails loudly here rather than
    silently emitting kind-less SQL that trips the NOT NULL at insert.
    """
    injected = (
        _MIGRATION._BACKFILL_LEDGER_ACCOUNTS_SQL
        .replace(
            "(user_id, class_id, account_id) ",
            "(user_id, class_id, account_id, kind_id) ",
        )
        .replace(
            "SELECT a.user_id, lc.id, a.id ",
            f"SELECT a.user_id, lc.id, a.id, {_LINKED_KIND_SUBQUERY} ",
        )
    )
    assert "kind_id" in injected and _LINKED_KIND_SUBQUERY in injected, (
        "kind_id injection did not fire -- the frozen b82 backfill SQL changed; "
        "update the anchors in test_ledger_account_backfill.py"
    )
    return injected


def _run_backfill():
    """Execute the (HEAD-adapted) b82 linked-row backfill SQL."""
    _db.session.execute(_db.text(_head_valid_backfill_sql()))
    _db.session.commit()


# ---------------------------------------------------------------------------
# Backfill: one linked row per pre-existing account, correct class
# ---------------------------------------------------------------------------


class TestBackfill:
    """The backfill pairs every unpaired account with the right class."""

    def test_backfill_pairs_asset_account(self, app, db, seed_user):
        """An Asset-category account backfills to one Asset ledger account.

        Arithmetic: a Checking account is Asset-category; the backfill's
        CASE maps every non-Liability category to the Asset class, so the
        recreated row carries the Asset class ID, the account's
        ``account_id``, and a NULL ``name``.
        """
        with app.app_context():
            # $0 anchor: no Step-5 opening entry, so the raw ledger-row drop
            # below leaves no posting-less journal entry behind (the deferred
            # balanced trigger raises on one at COMMIT) and no equity twin.
            account = create_account_of_type(
                seed_user, _db.session, "Checking", "Backfill Asset",
                anchor_balance=Decimal("0.00"),
            )
            _drop_ledger_account_for(account.id)
            assert ledger_accounts_for_account(_db.session, account.id) == []

            _run_backfill()

            rows = ledger_accounts_for_account(_db.session, account.id)
            assert len(rows) == 1
            assert rows[0].class_id == ref_cache.ledger_account_class_id(
                LedgerAccountClassEnum.ASSET,
            )
            assert rows[0].name is None

    def test_backfill_pairs_liability_account(self, app, db, seed_user):
        """A Liability-category account backfills to the Liability class.

        Arithmetic: a Mortgage is Liability-category; the backfill's CASE
        maps it to the Liability class, distinguishing it from every other
        category (which maps to Asset).
        """
        with app.app_context():
            account = create_account_of_type(
                seed_user, _db.session, "Mortgage", "Backfill Loan",
                anchor_balance=Decimal("0.00"),
            )
            _drop_ledger_account_for(account.id)

            _run_backfill()

            rows = ledger_accounts_for_account(_db.session, account.id)
            assert len(rows) == 1
            assert rows[0].class_id == ref_cache.ledger_account_class_id(
                LedgerAccountClassEnum.LIABILITY,
            )

    def test_backfill_is_idempotent(self, app, db, seed_user):
        """Re-running the backfill does not duplicate a ledger account.

        The ``WHERE NOT EXISTS`` guard keys off the natural ``account_id``;
        two runs in a row must leave exactly one linked row.
        """
        with app.app_context():
            # $0 anchor -- same rationale as test_backfill_pairs_asset_account.
            account = create_account_of_type(
                seed_user, _db.session, "HYSA", "Idem Backfill",
                anchor_balance=Decimal("0.00"),
            )
            _drop_ledger_account_for(account.id)

            _run_backfill()
            _run_backfill()

            assert len(ledger_accounts_for_account(_db.session, account.id)) == 1

    def test_backfill_covers_every_unpaired_account(
        self, app, db, seed_user,
    ):
        """After the backfill, every unpaired account has exactly one row.

        Two accounts of different categories are unpaired, then backfilled
        together; each ends with exactly one correctly-classed row.  The
        seed_user Checking (still paired) is skipped by the NOT EXISTS
        guard, proving the backfill is additive, not a wipe-and-replace.
        """
        with app.app_context():
            # $0 anchors -- same rationale as test_backfill_pairs_asset_account.
            asset_acct = create_account_of_type(
                seed_user, _db.session, "Savings", "Cover Asset",
                anchor_balance=Decimal("0.00"),
            )
            liab_acct = create_account_of_type(
                seed_user, _db.session, "Auto Loan", "Cover Liability",
                anchor_balance=Decimal("0.00"),
            )
            _drop_ledger_account_for(asset_acct.id)
            _drop_ledger_account_for(liab_acct.id)

            _run_backfill()

            asset_rows = ledger_accounts_for_account(_db.session, asset_acct.id)
            liab_rows = ledger_accounts_for_account(_db.session, liab_acct.id)
            assert len(asset_rows) == 1
            assert len(liab_rows) == 1
            assert asset_rows[0].class_id == ref_cache.ledger_account_class_id(
                LedgerAccountClassEnum.ASSET,
            )
            assert liab_rows[0].class_id == ref_cache.ledger_account_class_id(
                LedgerAccountClassEnum.LIABILITY,
            )
            # The seed_user Checking keeps its never-dropped rows (the linked
            # pairing plus the anchor-equity twin its $1000 opening minted).
            assert len(ledger_accounts_for_account(
                _db.session, seed_user["account"].id,
            )) == 2


# ---------------------------------------------------------------------------
# Migration revision pair + downgrade smoke check
# ---------------------------------------------------------------------------


class TestMigrationRevisionPair:
    """The migration chains off the Commit-1 head."""

    def test_revision_pair(self):
        """revision / down_revision pin the migration into the chain."""
        assert _MIGRATION.revision == "b82538084d24"
        assert _MIGRATION.down_revision == "f5037400dc5e"


class TestDowngradeReversible:
    """downgrade() is a real revert, not a bare pass.

    A source-level check (the executable round-trip is out of scope for the
    xdist worker -- see the module docstring) guards against a future edit
    silently re-routing the downgrade past one of the artefacts the upgrade
    materialises, which would leave a half-reverted schema.
    """

    def test_downgrade_drops_table_and_indexes(self):
        """The downgrade source drops the table and both indexes."""
        source = (_MIGRATIONS_DIR / _MIGRATION_FILENAME).read_text()
        assert "drop_index" in source
        assert "uq_ledger_accounts_account" in source
        assert "idx_ledger_accounts_user" in source
        assert (
            'drop_table("ledger_accounts"' in source
            or "drop_table('ledger_accounts'" in source
        ), "downgrade() never drops budget.ledger_accounts"
