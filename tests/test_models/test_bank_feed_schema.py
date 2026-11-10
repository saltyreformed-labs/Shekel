"""The feed table refuses what no writer should be trusted to avoid.

Plan step ``bank_import:X-f6b-2``, ruling **bank_import:R-BI12**.  Migration
``b447279d7a2e`` lands ``budget.bank_feeds``: the owner's claimed SimpleFIN
access URL, as ciphertext under the field-encryption key.

**Every constraint test here is a FIRING CONTROL.**  The claim door (a later
commit of the same step) refuses each of these states before it writes, so
nothing in ordinary use reaches the database tier -- and a test that merely
asserted a constraint EXISTS would pass against one admitting everything.
Each writes the state through the ORM, flushes, and asserts the refusal BY
NAME, which is the only tier that can see a future writer bypassing the door.

The shapes under test, and what each would cost if writable:

* **two feeds for one owner** -- two access URLs, so "the owner's feed" has
  two answers and the nightly door syncs one owner twice;
* **a feed holding no URL** -- a row that is not a feed, which the disconnect
  door would have to distinguish from one that is;
* **a feed surviving its owner** -- the CASCADE, asserted in the other
  direction: the state is unreachable because the database removes the row.

It also pins the audit registration and what the audit copy CARRIES: the
trigger writes the whole row, ciphertext included, as it does for
``auth.mfa_configs`` -- the exposure class ruling R-BI12 accepts -- so a
disconnect leaves the ciphertext readable under the key in ``system.audit_log``
until retention removes it.  That is stated by a test rather than assumed,
because the disconnect sentence ("revoke at Bridge") rests on it.
"""

import pytest
import sqlalchemy.exc

from app.audit_infrastructure import AUDITED_TABLES
from app.extensions import db
from app.models.bank_feed import BankFeed
from app.models.user import User
from app.services.auth_service import hash_password
from app.utils.field_encryption import encrypt_secret
from tests._test_helpers import assert_no_schema_drift, load_migration_module

_MIGRATION = load_migration_module(
    "b447279d7a2e_a_bank_feed_is_the_owners_claimed_access_url.py",
)

# The shape of the credential the column holds: the userinfo IS the secret.
_AN_ACCESS_URL = "https://alice:s3cr3t@beta-bridge.simplefin.org/simplefin"


def _a_feed(seed_user, **overrides):
    """Return an unsaved feed for the seeded owner, with fields replaceable.

    Args:
        seed_user: The seeded user bundle.
        **overrides: Column values to replace.

    Returns:
        The unsaved :class:`~app.models.bank_feed.BankFeed`.
    """
    fields = {
        "user_id": seed_user["user"].id,
        "access_url_encrypted": encrypt_secret(_AN_ACCESS_URL),
    }
    fields.update(overrides)
    return BankFeed(**fields)


class TestOneOwnerHoldsOneFeed:
    """``uq_bank_feeds_user``: the owner's feed has at most one answer."""

    def test_an_owner_may_hold_a_feed(self, app, db, seed_user):
        """The plain case, so the refusal below is not a refusal of everything."""
        feed = _a_feed(seed_user)
        db.session.add(feed)
        db.session.flush()

        assert feed.id is not None
        assert feed.claimed_at is not None

    def test_a_second_feed_for_the_same_owner_is_refused(
        self, app, db, seed_user,
    ):
        """FIRING CONTROL: the unique key, by name.

        A double-submitted claim, or a second claim while the first stands,
        cannot store two access URLs for one owner.
        """
        db.session.add(_a_feed(seed_user))
        db.session.flush()
        db.session.add(_a_feed(seed_user))

        with pytest.raises(sqlalchemy.exc.IntegrityError) as excinfo:
            db.session.flush()
        assert "uq_bank_feeds_user" in str(excinfo.value)


class TestAFeedHoldsAnAccessUrl:
    """``access_url_encrypted`` is NOT NULL: a row holding no URL is not a feed."""

    def test_a_feed_with_no_url_is_refused(self, app, db, seed_user):
        """FIRING CONTROL: disconnecting is deleting the row, never clearing
        the column, and the database says so."""
        db.session.add(_a_feed(seed_user, access_url_encrypted=None))

        with pytest.raises(sqlalchemy.exc.IntegrityError) as excinfo:
            db.session.flush()
        assert "access_url_encrypted" in str(excinfo.value)

    def test_the_ciphertext_round_trips_as_bytes(self, app, db, seed_user):
        """What is stored is what was written: the Fernet token, byte for byte.

        Read back through a fresh query rather than the identity map, so the
        ``BYTEA`` column's round trip is what is graded.
        """
        feed = _a_feed(seed_user)
        db.session.add(feed)
        db.session.flush()
        written = feed.access_url_encrypted
        db.session.expire(feed)

        stored = db.session.get(BankFeed, feed.id).access_url_encrypted

        assert isinstance(stored, bytes)
        assert stored == written


class TestAFeedCannotOutliveItsOwner:
    """``fk_bank_feeds_user_id`` is ``ON DELETE CASCADE``."""

    def test_deleting_the_owner_takes_the_feed(self, app, db):
        """Asserted in the other direction from the refusals above: the state
        is unreachable because the database removes the row.

        A user minted here rather than the seeded one, which holds rows
        under ``ON DELETE RESTRICT`` keys (its transactions) that would
        refuse the delete before this key is reached.
        """
        owner = User(
            email="feed-owner@example.test",
            password_hash=hash_password("a-passphrase-for-this-test"),
            display_name="Feed Owner",
        )
        db.session.add(owner)
        db.session.flush()
        feed = BankFeed(
            user_id=owner.id, access_url_encrypted=encrypt_secret(_AN_ACCESS_URL),
        )
        db.session.add(feed)
        db.session.flush()
        feed_id = feed.id
        db.session.expunge(feed)

        db.session.execute(
            db.delete(User).where(User.id == owner.id),
        )

        assert db.session.get(BankFeed, feed_id) is None


class TestTheFeedIsAudited:
    """A feed is destroyed by an ordinary DELETE (disconnect), so the trail
    is the record -- and the trail carries the ciphertext."""

    def test_the_table_is_in_the_audited_list(self, app, db):
        """``app.audit_infrastructure.AUDITED_TABLES`` names it, as it must
        name every ``budget`` table."""
        assert ("budget", "bank_feeds") in AUDITED_TABLES

    def test_the_migration_and_the_audit_list_agree(self, app, db):
        """Two lists that must agree are two lists that can drift."""
        audited = {
            name for schema, name in AUDITED_TABLES if schema == "budget"
        }
        assert _MIGRATION._AUDITED_NEW_TABLE in audited

    def test_the_table_carries_its_trigger(self, app, db):
        """Under the name the entrypoint health check enumerates."""
        found = db.session.execute(
            db.text(
                "SELECT 1 FROM pg_trigger g "
                "JOIN pg_class t ON t.oid = g.tgrelid "
                "JOIN pg_namespace n ON n.oid = t.relnamespace "
                "WHERE n.nspname = 'budget' AND t.relname = :table "
                "AND g.tgname = :trigger"
            ),
            {
                "table": _MIGRATION._AUDITED_NEW_TABLE,
                "trigger": f"audit_{_MIGRATION._AUDITED_NEW_TABLE}",
            },
        ).scalar()

        assert found == 1

    def test_deleting_a_feed_writes_the_whole_row_ciphertext_included(
        self, app, db, seed_user,
    ):
        """The audit copy carries ``access_url_encrypted``: the same exposure
        class as the live column (ruling R-BI12: dump + key), stated here so
        the disconnect wording that rests on it -- revoke at Bridge -- is
        graded rather than assumed.  ``to_jsonb`` renders ``BYTEA`` as its
        hex text, so the copy is the ciphertext, never the plaintext.
        """
        feed = _a_feed(seed_user)
        db.session.add(feed)
        db.session.flush()
        feed_id = feed.id
        ciphertext = feed.access_url_encrypted

        db.session.delete(feed)
        db.session.flush()

        old = db.session.execute(
            db.text(
                "SELECT old_data FROM system.audit_log "
                "WHERE table_name = 'bank_feeds' "
                "AND operation = 'DELETE' AND row_id = :row_id"
            ),
            {"row_id": feed_id},
        ).scalar()
        assert old is not None
        assert old["user_id"] == seed_user["user"].id
        assert old["access_url_encrypted"] == "\\x" + ciphertext.hex()
        assert _AN_ACCESS_URL not in str(old)


class TestTheColumnsAreWhatTheRulingSaid:
    """The absences are decisions, so they are pinned like the presences.

    No ``created_at`` beside ``claimed_at`` (one fact, one home: the claim
    is the row's birth); no ``updated_at`` (a feed is never edited); no
    ``last_synced_at`` (the newest ``simplefin`` import is that fact).
    """

    def test_it_carries_exactly_the_four_columns_ruled(self, app, db):
        """Named rather than counted, so a rename is as visible as an add."""
        assert {
            column.name for column in BankFeed.__table__.columns
        } == {"id", "user_id", "access_url_encrypted", "claimed_at"}

    def test_every_column_is_NOT_NULL_in_the_DATABASE(self, app, db):
        """Asked of ``information_schema`` rather than of the model: a
        migration relaxing a column, or a drift between ``db.create_all()``
        and the chain, is exactly what a model-only assertion cannot see.

        The whole ``column -> is_nullable`` mapping is asserted, not the
        empty set of nullable ones: a misspelled table name would answer
        an empty set too (named by adversarial review 2026-09-19), and the
        mapping also pins the DATABASE's column set beside the model's.
        """
        nullability = dict(db.session.execute(
            db.text(
                "SELECT column_name, is_nullable FROM information_schema.columns "
                "WHERE table_schema = 'budget' AND table_name = 'bank_feeds'"
            ),
        ).all())

        assert nullability == {
            "id": "NO",
            "user_id": "NO",
            "access_url_encrypted": "NO",
            "claimed_at": "NO",
        }

    def test_autogenerate_sees_no_drift_on_the_table(self, app):
        """The migrated table and the model agree on every column, type,
        default and key.

        Alembic's own comparison over the test database (migrated from
        scratch through the chain, never ``create_all``'d), scoped to this
        one table with server defaults compared.  The rehearsal clone this
        migration was driven on carries older tables' drifts, so this is
        the check that the CHAIN produces the model.
        """
        assert_no_schema_drift(app, [("budget", "bank_feeds")])

    def test_the_repr_carries_no_ciphertext(self, app, db, seed_user):
        """A repr reaches log lines and tracebacks."""
        feed = _a_feed(seed_user)
        db.session.add(feed)
        db.session.flush()

        text = repr(feed)

        assert text == f"<BankFeed id={feed.id} user={seed_user['user'].id}>"
        assert feed.access_url_encrypted.decode("ascii") not in text
