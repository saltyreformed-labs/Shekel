"""A mapping declared under a feed dies with it; one learned from a file does not.

Plan step ``bank_import:X-f6b-2``, leaf (3c), rulings **R-BI12** (the
mapping is the existing ``account_external_identities`` row under a
``simplefin`` source) and **R-BI26** (a declared mapping's life is the
feed's).  Migration ``6efa1fb46af8`` lands the three parts: the ``simplefin``
ref row, the superkey ``uq_bank_feeds_id_user``, and ``feed_id`` with the
composite cascade key ``fk_account_external_identities_feed_owner``.

**Every constraint case is a FIRING CONTROL** (the standard
``test_bank_feed_schema.py`` set for the sibling table): it writes the state
the key exists to refuse and asserts the refusal BY NAME, because a test that
only asserts a key EXISTS passes against one that admits everything, and a
misspelled key name satisfies an existence check just as well.

The shapes under test, and what each would cost if writable:

* **a mapping declared under ANOTHER owner's feed** -- one owner's Bridge
  account mapped under a feed they do not hold, which the composite key over
  ``user_id`` makes unrepresentable rather than checked;
* **a mapping naming a feed that does not exist** -- a declaration with no
  permission behind it;
* **a declared mapping surviving its feed** -- the CASCADE, asserted in the
  other direction; and its twin, a LEARNED row surviving a feed delete it has
  nothing to do with;
* **R-GB's forgetting reaching a declared row** -- the amendment R-BI26
  makes, pinned with the learned row as its boundary control.

And the ref row's two sides: it resolves through ``ref_cache``, and the
upload form does NOT offer it, because a source seeded ahead of its parser
must not be offerable (``available_sources`` is an intersection).
"""

from datetime import date

import marshmallow
import pytest
import sqlalchemy.exc

from app import ref_cache
from app.enums import StatementSourceEnum
from app.extensions import db
from app.models.bank_feed import BankFeed
from app.models.statement_import import (
    AccountExternalIdentity,
    StatementImport,
)
from app.models.user import User
from app.schemas.validation.statements import StatementUploadSchema
from app.services.auth_service import hash_password
from app.services.statement_import import available_sources
from app.services.statement_import._identity import (
    forget_identity_if_last,
    record_identity,
)
from app.utils.field_encryption import encrypt_secret
from tests._test_helpers import assert_no_schema_drift, load_migration_module

_MIGRATION = load_migration_module(
    "6efa1fb46af8_the_feed_source_and_the_declared_mapping.py",
)

# Bridge's account id shape, measured 2026-09-18 (``ACT-`` + uuid, 40 chars).
_A_BRIDGE_ACCOUNT = "ACT-125e0df6-8f88-4a46-888a-37e0342ed307"
_AN_ACCESS_URL = "https://alice:s3cr3t@beta-bridge.simplefin.org/simplefin"


def _feed_source_id() -> int:
    """The ``simplefin`` ref row's id, resolved the way the app resolves it."""
    return ref_cache.statement_source_id(StatementSourceEnum.SIMPLEFIN)


def _a_feed_for(user_id: int) -> BankFeed:
    """Stage and flush a feed for *user_id*; return it with its id set."""
    feed = BankFeed(
        user_id=user_id, access_url_encrypted=encrypt_secret(_AN_ACCESS_URL),
    )
    db.session.add(feed)
    db.session.flush()
    return feed


def _another_owner() -> User:
    """Mint a second owner, so a cross-owner feed exists to name."""
    owner = User(
        email="other-feed-owner@example.test",
        password_hash=hash_password("a-passphrase-for-this-test"),
        display_name="Other Owner",
    )
    db.session.add(owner)
    db.session.flush()
    return owner


class TestTheFeedSourceRow:
    """``ref.statement_sources`` carries ``simplefin``, and the form does not."""

    def test_the_enum_member_resolves_to_a_row(self, app, db):
        """The dual seed's migration half: a migration-built database (the
        test template, never ``create_all``'d) resolves the member."""
        assert isinstance(_feed_source_id(), int)

    def test_the_upload_form_neither_offers_nor_accepts_it(self, app, db):
        """FIRING CONTROL for ``available_sources``' intersection, and THE
        ALARM for the sync leaf: the row exists, no upload parser reads it,
        so the form must leave it out AND the upload schema must refuse it
        submitted by hand.  Both read ``_adapters._PARSERS``; the day a feed
        reader is registered there, this fails, and the right repair is the
        registry's file-source / feed-source distinction, never removing
        this test.  Asserted against the row's presence in the same test, so
        it cannot pass merely because the seed is missing.
        """
        names = {
            name for (name,) in db.session.execute(
                db.text("SELECT name FROM ref.statement_sources"),
            )
        }
        assert StatementSourceEnum.SIMPLEFIN.value in names

        offered = {option.value for option in available_sources()}

        assert StatementSourceEnum.SECU_CHECKING_CSV.value in offered
        assert StatementSourceEnum.SIMPLEFIN.value not in offered
        with pytest.raises(marshmallow.ValidationError):
            StatementUploadSchema().load(
                {"source": StatementSourceEnum.SIMPLEFIN.value},
            )


class TestTheColumnAndItsKey:
    """What the migration built, asked of the catalog by name and shape."""

    def test_feed_id_is_nullable(self, app, db):
        """A learned row carries NULL, so the column must admit it."""
        nullable = db.session.execute(
            db.text(
                "SELECT is_nullable FROM information_schema.columns "
                "WHERE table_schema = 'budget' "
                "AND table_name = 'account_external_identities' "
                "AND column_name = 'feed_id'"
            ),
        ).scalar()

        assert nullable == "YES"

    def test_the_key_cascades_over_exactly_feed_and_owner(self, app, db):
        """The MAPPING, not the existence: which column references which,
        as ordered PAIRS (a swapped ``(user_id, feed_id) -> (id, user_id)``
        would satisfy a set comparison and hold nothing), and what the
        delete rule is.  ``confdeltype = 'c'`` is CASCADE."""
        rows = db.session.execute(
            db.text(
                "SELECT c.confdeltype, r.relname AS target, "
                "  a.attname AS referencing, b.attname AS referenced "
                "FROM pg_constraint c "
                "JOIN pg_class r ON r.oid = c.confrelid "
                "JOIN unnest(c.conkey, c.confkey) WITH ORDINALITY "
                "  AS k(con, conf, n) ON TRUE "
                "JOIN pg_attribute a ON a.attrelid = c.conrelid "
                "  AND a.attnum = k.con "
                "JOIN pg_attribute b ON b.attrelid = c.confrelid "
                "  AND b.attnum = k.conf "
                "WHERE c.conname = 'fk_account_external_identities_feed_owner' "
                "ORDER BY k.n"
            ),
        ).all()

        assert [(row.referencing, row.referenced) for row in rows] == [
            ("feed_id", "id"), ("user_id", "user_id"),
        ]
        assert {row.confdeltype for row in rows} == {"c"}
        assert {row.target for row in rows} == {"bank_feeds"}

    def test_autogenerate_sees_no_drift_on_the_two_tables(self, app):
        """The migrated tables and the models agree on every column, type,
        default and key -- Alembic's own comparison over the test database,
        scoped to the two tables this migration touched."""
        assert_no_schema_drift(app, [
            ("budget", "account_external_identities"),
            ("budget", "bank_feeds"),
        ])


class TestADeclaredMappingNamesItsOwnFeed:
    """``fk_account_external_identities_feed_owner``, in both refusing shapes."""

    def test_a_declared_mapping_under_the_owners_feed_is_written(
        self, app, db, seed_user,
    ):
        """The plain case, so the refusals below are not refusals of everything."""
        feed = _a_feed_for(seed_user["user"].id)

        identity = record_identity(
            seed_user["account"].id, seed_user["user"].id, _feed_source_id(),
            _A_BRIDGE_ACCOUNT, feed_id=feed.id,
        )
        db.session.flush()

        assert identity.id is not None
        assert identity.feed_id == feed.id

    def test_a_mapping_under_ANOTHER_owners_feed_is_refused(
        self, app, db, seed_user,
    ):
        """FIRING CONTROL: the composite key over ``user_id``.  The row's
        owner and the feed's owner disagree, and the key -- not a door --
        refuses it."""
        other = _another_owner()
        others_feed = _a_feed_for(other.id)
        db.session.add(AccountExternalIdentity(
            account_id=seed_user["account"].id,
            user_id=seed_user["user"].id,
            source_id=_feed_source_id(),
            external_account_id=_A_BRIDGE_ACCOUNT,
            feed_id=others_feed.id,
        ))

        with pytest.raises(sqlalchemy.exc.IntegrityError) as excinfo:
            db.session.flush()
        assert "fk_account_external_identities_feed_owner" in str(excinfo.value)

    def test_a_mapping_naming_no_feed_at_all_is_refused(
        self, app, db, seed_user,
    ):
        """FIRING CONTROL: a declaration with no permission behind it."""
        db.session.add(AccountExternalIdentity(
            account_id=seed_user["account"].id,
            user_id=seed_user["user"].id,
            source_id=_feed_source_id(),
            external_account_id=_A_BRIDGE_ACCOUNT,
            feed_id=987654,
        ))

        with pytest.raises(sqlalchemy.exc.IntegrityError) as excinfo:
            db.session.flush()
        assert "fk_account_external_identities_feed_owner" in str(excinfo.value)

    def test_a_learned_row_carries_no_feed_and_passes(
        self, app, db, seed_user,
    ):
        """MATCH SIMPLE: a composite key is not checked while a column is
        NULL, which is what lets every file-learned row exist beside the key.
        The default ``feed_id`` is what ``record_statement``'s call writes."""
        identity = record_identity(
            seed_user["account"].id, seed_user["user"].id,
            ref_cache.statement_source_id(StatementSourceEnum.SECU_CHECKING_CSV),
            "******3820",
        )
        db.session.flush()

        assert identity.feed_id is None


class TestADeclaredMappingDiesWithItsFeed:
    """The CASCADE, in both directions: declared rows go, learned rows stay."""

    def test_deleting_the_feed_takes_its_declared_mapping(
        self, app, db, seed_user,
    ):
        """Asserted in the other direction from the refusals: the state is
        unreachable because the database removes the row."""
        feed = _a_feed_for(seed_user["user"].id)
        declared = record_identity(
            seed_user["account"].id, seed_user["user"].id, _feed_source_id(),
            _A_BRIDGE_ACCOUNT, feed_id=feed.id,
        )
        db.session.flush()
        declared_id = declared.id
        db.session.expunge(declared)

        db.session.execute(db.delete(BankFeed).where(BankFeed.id == feed.id))

        assert db.session.get(AccountExternalIdentity, declared_id) is None

    def test_deleting_the_feed_leaves_a_LEARNED_row_alone(
        self, app, db, seed_user,
    ):
        """Boundary control: the cascade follows ``feed_id``, and a learned
        row names none, so an owner's CSV pairing survives their disconnect."""
        feed = _a_feed_for(seed_user["user"].id)
        learned = record_identity(
            seed_user["account"].id, seed_user["user"].id,
            ref_cache.statement_source_id(StatementSourceEnum.SECU_CHECKING_CSV),
            "******3820",
        )
        db.session.flush()
        learned_id = learned.id
        db.session.expunge(learned)

        db.session.execute(db.delete(BankFeed).where(BankFeed.id == feed.id))

        assert db.session.get(AccountExternalIdentity, learned_id) is not None


class TestForgettingReachesOnlyLearnedRows:
    """R-GB's forgetting, amended by R-BI26 to the rows an import taught."""

    def test_a_declared_mapping_with_no_import_is_NOT_forgotten(
        self, app, db, seed_user,
    ):
        """The amendment.  No import survives -- none ever existed -- and the
        row stays, because no import taught it.  Without the arm, deleting the
        last feed import would silently unmap the account."""
        feed = _a_feed_for(seed_user["user"].id)
        record_identity(
            seed_user["account"].id, seed_user["user"].id, _feed_source_id(),
            _A_BRIDGE_ACCOUNT, feed_id=feed.id,
        )
        db.session.flush()

        forgotten = forget_identity_if_last(
            seed_user["account"].id, _feed_source_id(),
        )

        assert forgotten is False
        assert db.session.query(AccountExternalIdentity).filter_by(
            feed_id=feed.id,
        ).count() == 1

    def test_a_learned_row_with_no_import_IS_forgotten(
        self, app, db, seed_user,
    ):
        """FIRING CONTROL for the amendment's boundary: the same call on a
        learned row still forgets it, so the arm reads ``feed_id`` and not
        "never forget"."""
        source_id = ref_cache.statement_source_id(
            StatementSourceEnum.SECU_CHECKING_CSV,
        )
        record_identity(
            seed_user["account"].id, seed_user["user"].id, source_id,
            "******3820",
        )
        db.session.flush()

        forgotten = forget_identity_if_last(seed_user["account"].id, source_id)

        assert forgotten is True
        assert db.session.query(AccountExternalIdentity).count() == 0


class TestTheDowngradeRefusesWhileTheSourceIsNamed:
    """``refuse_referenced_source``, driven per arm, and passing on a bare tree."""

    def test_it_passes_when_nothing_names_the_source(self, app, db):
        """The plain case: a database with no feed rows downgrades."""
        _MIGRATION.refuse_referenced_source(db.session.connection())

    def test_a_declared_mapping_refuses_it(self, app, db, seed_user):
        """FIRING CONTROL, first arm: the identity table."""
        feed = _a_feed_for(seed_user["user"].id)
        record_identity(
            seed_user["account"].id, seed_user["user"].id, _feed_source_id(),
            _A_BRIDGE_ACCOUNT, feed_id=feed.id,
        )
        db.session.flush()

        with pytest.raises(RuntimeError) as excinfo:
            _MIGRATION.refuse_referenced_source(db.session.connection())
        assert "1 budget.account_external_identities row(s)" in str(excinfo.value)
        assert "Disconnect the bank feed" in str(excinfo.value)

    def test_a_feed_import_refuses_it(self, app, db, seed_user):
        """FIRING CONTROL, second arm: the imports table, reached with no
        identity row so the first arm cannot be what fired."""
        db.session.add(StatementImport(
            account_id=seed_user["account"].id,
            user_id=seed_user["user"].id,
            source_id=_feed_source_id(),
            file_name="SimpleFIN 2026-09-13..2026-09-20",
            file_digest="0" * 64,
            declared_start=date(2026, 9, 13),
            declared_end=date(2026, 9, 20),
        ))
        db.session.flush()

        with pytest.raises(RuntimeError) as excinfo:
            _MIGRATION.refuse_referenced_source(db.session.connection())
        assert "1 budget.statement_imports row(s)" in str(excinfo.value)
        assert "Delete each feed import" in str(excinfo.value)
