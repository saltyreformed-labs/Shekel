"""The feed source, and a mapping declared under a feed dies with it

Plan step **bank_import:X-f6b-2** (the SimpleFIN feed), leaf (3c) the claim
door and the account mapping, under rulings **R-BI12** (developer,
2026-09-18: the access URL is per-owner ciphertext claimed in the app, and
the owner maps each Bridge account to a Shekel account through the existing
``account_external_identities`` row under a ``simplefin`` source) and
**R-BI26** (developer, 2026-09-20: a mapping DECLARED on the feed panel dies
with the feed; deleting imports never touches it).

Three things, and **no row's balance moves**: a ref row, a superkey, and a
nullable column with a composite cascade key.

1. **``ref.statement_sources`` gains ``simplefin``**, the feed source.  The
   dual seed every ref row here takes: this migration inline-seeds the row
   so a bare ``flask db upgrade`` lets ``ref_cache.init`` resolve
   ``StatementSourceEnum.SIMPLEFIN`` before the app-layer reseed runs, and
   ``app/ref_seeds.py`` carries the identical row for a database born by
   ``create_all``.  **The INSERT is ``ON CONFLICT (name) DO NOTHING``, and
   that clause is load-bearing** (the pattern every inline seed that adds a
   row to an EXISTING ref table carries, ``e4b8a71c0f36`` first): under
   ``FLASK_ENV=development`` the app factory runs the reseed BEFORE Alembic
   runs (``create_app`` -> ``_seed_ref_tables``), so on a dev database the
   row is already present and committed when this migration reaches it,
   and a bare INSERT would abort the boot on ``statement_sources_name_key``.
   Named by adversarial review 2026-09-20; the lane's rehearsal clone could
   not show it, because its reseed dies earlier on a column that clone
   lacks (``account_types.has_revolving_credit``).
   ``tests/test_models/test_posting_ref_seed_parity.py`` grades the name in
   all three places and
   ``test_reads.py::test_the_SEEDER_and_the_DATABASE_agree_about_that_label``
   grades the label.  The upload form does NOT offer it, and must never:
   ``available_sources`` is the intersection of these rows with the parser
   registry, no upload parser reads the feed, and the sync leaf owes that
   registry the distinction between a file source and a feed source before
   it registers the feed's reader anywhere (coordinator, 2026-09-20).

2. **``budget.bank_feeds`` gains ``uq_bank_feeds_id_user (id, user_id)``**,
   the superkey a composite foreign key needs as its target.  It constrains
   nothing on its own (``id`` is the primary key); PostgreSQL requires a
   UNIQUE over exactly the referenced columns.  ``uq_accounts_id_user``'s
   construction.

3. **``budget.account_external_identities`` gains ``feed_id``** (nullable)
   with ``fk_account_external_identities_feed_owner (feed_id, user_id) ->
   bank_feeds (id, user_id) ON DELETE CASCADE``.  A row LEARNED from a file
   -- every row that exists today -- carries NULL and is forgotten with the
   last import from its source, ruling **R-GB**'s rule unchanged.  A row
   DECLARED on the feed panel names the feed it was declared under, and the
   cascade is what makes "a declared mapping with no feed behind it"
   unrepresentable: the disconnect deletes the feed row and the database
   removes its mappings in the same statement, where a door rule would have
   left the orphan to whichever delete remembered.  The key is composite over
   ``user_id`` so the feed's owner IS the row's owner by construction (the
   argument ``fk_account_external_identities_owner`` makes for the account),
   and MATCH SIMPLE -- the default -- is what lets a learned row pass: a
   composite key is not checked while any of its columns is NULL.

**R-BI26 amends R-GB (2)**, in two halves: the CASCADE above is the
structural half (a declared mapping cannot outlive its feed), and
``_identity.forget_identity_if_last`` reading ``feed_id`` to leave a
declared row alone is a door rule, pinned by a test.  Without the door
rule, deleting the last feed import of an account would silently unmap the
account from the feed, and the nightly sync (a later leaf of this step)
would then skip it with no screen saying why.

**No backfill.**  Every existing ``account_external_identities`` row was
learned from a CSV (no feed has ever existed; ``budget.bank_feeds`` was born
empty at ``b447279d7a2e`` and no door writes it until the claim door lands),
so NULL is the true value for all of them and the column is added without a
default.  The migration prints both counts so the deploy log carries the
measurement; on the lane's ``shekel_xf6b3b`` clone (the 2026-09-18 restore)
it printed ``1 account_external_identities row(s) take feed_id NULL (learned
from a file); 0 bank_feeds row(s) exist`` (2026-09-20).

**Downgrade refuses while anything references the feed source, and is
lossy in one stated way otherwise.**  It cannot drop the ``simplefin`` ref
row while an identity or an import names it (``ON DELETE RESTRICT`` on both
keys would refuse the DELETE with a raw constraint error; refusing first, in
a sentence, is the chain's own shape, ``45f10b870c8b.refuse_lossy_rows``),
and deleting those rows here would destroy recorded bank lines, which ruling
**R-HN** forbids: a migration's downgrade deletes no data.  The repair is
the app's own:
disconnect the feed (its declared mappings cascade) and delete its imports,
then re-run.  What the downgrade then loses is the PROVENANCE column: any
surviving row with ``feed_id`` set becomes indistinguishable from a learned
one and is forgettable by R-GB's rule again.  The refusal has established
that no row under the feed source survives, and no door writes ``feed_id``
under any other source, so the loss is expressible and expected empty --
stated, not assumed, because nothing in the schema forbids the other case.

Revision ID: 6efa1fb46af8
Revises: b447279d7a2e
Create Date: 2026-09-20 11:34
"""
from alembic import op
import sqlalchemy as sa


# Revision identifiers, used by Alembic.
revision = '6efa1fb46af8'
down_revision = 'b447279d7a2e'
branch_labels = None
depends_on = None


#: The feed source's name, the enum's ``.value``.  The INSERT below spells
#: it as a quoted literal rather than through this constant, because
#: ``tests/test_models/test_posting_ref_seed_parity.py`` anchors on the
#: literal INSIDE the ``INSERT INTO ref.statement_sources`` string -- that is
#: how the scan tells a seed from prose naming one -- and an f-string hides
#: it from the scan (measured: the first draft failed the gate).
_SOURCE_NAME = "simplefin"

#: The dual seed's migration half: the row ``app/ref_seeds.py`` also carries,
#: with the same label.
_SEED_SOURCE_SQL = (
    "INSERT INTO ref.statement_sources (name, display_name) VALUES "
    "('simplefin', 'SimpleFIN Bridge -- daily feed') "
    "ON CONFLICT (name) DO NOTHING"
)

_DELETE_SOURCE_SQL = (
    f"DELETE FROM ref.statement_sources WHERE name = '{_SOURCE_NAME}'"
)

#: Rows that name the feed source, either table; the downgrade's refusal.
_IDENTITIES_UNDER_SOURCE_SQL = (
    "SELECT COUNT(*) FROM budget.account_external_identities i "
    "JOIN ref.statement_sources s ON s.id = i.source_id "
    f"WHERE s.name = '{_SOURCE_NAME}'"
)

_IMPORTS_UNDER_SOURCE_SQL = (
    "SELECT COUNT(*) FROM budget.statement_imports m "
    "JOIN ref.statement_sources s ON s.id = m.source_id "
    f"WHERE s.name = '{_SOURCE_NAME}'"
)

#: The measurement the upgrade prints: how many rows take NULL, and how
#: many feeds exist for a declared row to name.
_IDENTITY_COUNT_SQL = "SELECT COUNT(*) FROM budget.account_external_identities"
_FEED_COUNT_SQL = "SELECT COUNT(*) FROM budget.bank_feeds"


def _refuse_if_any(bind, sql: str, sentence: str) -> None:
    """Refuse with the count and *sentence* when *sql* counts anything.

    Args:
        bind: A SQLAlchemy connection.
        sql: A ``SELECT COUNT(*)`` statement.
        sentence: What the count means and how it is repaired; the count is
            prefixed and the diagnostic query appended.
    """
    count = bind.execute(sa.text(sql)).scalar()
    if count:
        raise RuntimeError(f"{count} {sentence}  Diagnose with: {sql}")


def refuse_referenced_source(bind) -> None:
    """Refuse the downgrade while any row names the feed source.

    **Module-level so a test can DRIVE each refusal** (the chain's own
    pattern, ``45f10b870c8b.refuse_lossy_rows``).

    Args:
        bind: A SQLAlchemy connection.

    Raises:
        RuntimeError: Naming the count, the repair and the diagnostic query.
    """
    _refuse_if_any(
        bind, _IDENTITIES_UNDER_SOURCE_SQL,
        "budget.account_external_identities row(s) name the simplefin "
        "source, so its ref row cannot be dropped.  Disconnect the bank feed "
        "in the app (its declared mappings go with it); then re-run.",
    )
    _refuse_if_any(
        bind, _IMPORTS_UNDER_SOURCE_SQL,
        "budget.statement_imports row(s) name the simplefin source, so its "
        "ref row cannot be dropped, and deleting them here would destroy "
        "recorded bank lines.  Delete each feed import from the statements "
        "page; then re-run.",
    )


def upgrade():
    """Seed the feed source, add the superkey, add the column and its key."""
    bind = op.get_bind()
    identities = bind.execute(sa.text(_IDENTITY_COUNT_SQL)).scalar()
    feeds = bind.execute(sa.text(_FEED_COUNT_SQL)).scalar()

    op.execute(_SEED_SOURCE_SQL)

    op.create_unique_constraint(
        "uq_bank_feeds_id_user", "bank_feeds", ["id", "user_id"],
        schema="budget",
    )

    op.add_column(
        "account_external_identities",
        sa.Column("feed_id", sa.Integer(), nullable=True),
        schema="budget",
    )
    op.create_foreign_key(
        "fk_account_external_identities_feed_owner",
        "account_external_identities", "bank_feeds",
        ["feed_id", "user_id"], ["id", "user_id"],
        source_schema="budget", referent_schema="budget",
        ondelete="CASCADE",
    )

    print(
        f"6efa1fb46af8: seeded ref.statement_sources.{_SOURCE_NAME}; "
        f"{identities} account_external_identities row(s) take feed_id NULL "
        f"(learned from a file); {feeds} bank_feeds row(s) exist."
    )


def downgrade():
    """Drop the key, the column, the superkey and the ref row, refusing first."""
    bind = op.get_bind()
    refuse_referenced_source(bind)

    op.drop_constraint(
        "fk_account_external_identities_feed_owner",
        "account_external_identities", schema="budget", type_="foreignkey",
    )
    op.drop_column("account_external_identities", "feed_id", schema="budget")
    op.drop_constraint(
        "uq_bank_feeds_id_user", "bank_feeds", schema="budget", type_="unique",
    )
    op.execute(_DELETE_SOURCE_SQL)
