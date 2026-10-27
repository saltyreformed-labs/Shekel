"""
Shekel Budget App -- migration ``1712c506ee99``: a ledger entry links only its movement.

Plan step ``balance:X-bi-6-5``'s second leaf, card plan#1845 (rulings
**R-BAL102**, plan#1843, plan#1867).  The revision refuses while an entry of
another kind carries ``journal_entries.transfer_id`` or ``transaction_id``, or
while the ``transfer`` / ``transaction`` entries of one (scenario, ledger
account, pay period, entry date, source kind, transfer link, transaction link,
movement link) net to anything but zero; then it deletes every entry of those
two kinds with
its legs, drops both columns with their keys and indexes, and deletes both
``ref.posting_sources`` rows.  Its downgrade restores the schema and both rows,
and no entry.

The cases plant the legacy shapes on the test's own database below the
revision (its downgrade), drive the upgrade, and grade what the catalog, the
ledger and the audit log hold after each.  The plants are raw SQL: the model
has no link column and the enum no legacy member at head.
"""

import contextlib
import io
from datetime import timedelta
from decimal import Decimal

import pytest
from sqlalchemy import text

from app.extensions import db as _db
from tests._test_helpers import (
    add_txn,
    create_savings_account,
    create_settled_transfer,
    ledger_accounts_for_account,
    load_migration_module,
    run_migration_callable,
)

_MIGRATION = load_migration_module(
    "1712c506ee99_a_ledger_entry_links_only_its_movement.py",
)

#: The two source kinds the revision deletes, as the catalog names them.
_LEGACY = ("transaction", "transfer")

#: What a database below the revision holds, as rendered on the dev database at
#: ``b3f7c2a91d4e`` (2026-10-27, read-only): neither link's objects changed
#: between their creation (``db239773c2fd``, ``bdde62675c9b``) and this
#: revision, so a database at ``c956c1d83108`` renders them the same.
_BELOW = {
    "columns": [
        ("transaction_id", "integer", "YES"),
        ("transfer_id", "integer", "YES"),
    ],
    "constraints": {
        "fk_journal_entries_transaction_id": (
            "FOREIGN KEY (transaction_id) REFERENCES budget.transactions(id) "
            "ON DELETE SET NULL"
        ),
        "fk_journal_entries_transfer_id": (
            "FOREIGN KEY (transfer_id) REFERENCES budget.transfers(id) "
            "ON DELETE SET NULL"
        ),
    },
    "indexes": {
        "idx_journal_entries_transaction": (
            "CREATE INDEX idx_journal_entries_transaction ON "
            "budget.journal_entries USING btree (transaction_id) "
            "WHERE (transaction_id IS NOT NULL)"
        ),
        "idx_journal_entries_transfer": (
            "CREATE INDEX idx_journal_entries_transfer ON "
            "budget.journal_entries USING btree (transfer_id) "
            "WHERE (transfer_id IS NOT NULL)"
        ),
    },
    "legacy_sources": list(_LEGACY),
}

#: What head holds: none of them.
_HEAD = {"columns": [], "constraints": {}, "indexes": {}, "legacy_sources": []}


def _schema():
    """Return what the revision changes, read from the catalog, as one value.

    Both columns with their type and nullability, every constraint on
    ``budget.journal_entries`` naming either or keyed into
    ``budget.transfers`` / ``budget.transactions`` by any name (so a
    ``create_all`` database's unnamed key is seen too), every index over
    either, and which of the two legacy ``ref.posting_sources`` names exist.
    Only those two names are read: a later step that adds a source kind must
    not move this value.
    """
    columns = [tuple(row) for row in _db.session.execute(text(
        "SELECT column_name, data_type, is_nullable "
        "FROM information_schema.columns "
        "WHERE table_schema = 'budget' AND table_name = 'journal_entries' "
        "AND column_name IN ('transfer_id', 'transaction_id') "
        "ORDER BY column_name"
    )).all()]
    constraints = dict(_db.session.execute(text(
        "SELECT conname, pg_get_constraintdef(oid) FROM pg_constraint "
        "WHERE conrelid = 'budget.journal_entries'::regclass "
        "AND (pg_get_constraintdef(oid) ~ '\\m(transfer_id|transaction_id)\\M' "
        "     OR confrelid IN ('budget.transfers'::regclass, "
        "                      'budget.transactions'::regclass))"
    )).all())
    indexes = dict(_db.session.execute(text(
        "SELECT indexname, indexdef FROM pg_indexes "
        "WHERE schemaname = 'budget' AND tablename = 'journal_entries' "
        "AND indexdef ~ '\\m(transfer_id|transaction_id)\\M'"
    )).all())
    sources = _db.session.execute(text(
        "SELECT name FROM ref.posting_sources "
        "WHERE name IN ('transfer', 'transaction') ORDER BY name"
    )).scalars().all()
    return {
        "columns": columns,
        "constraints": constraints,
        "indexes": indexes,
        "legacy_sources": sources,
    }


def _world(seed_user, seed_periods):
    """Build what the plants link and post into, then step down below the revision.

    A settled transfer Checking -> Savings (its two movement entries are the
    ``transfer_movement`` entries a delete must leave alone), a second
    transfer and a plain row for the links to name, and three accounts'
    linked ledgers.  Plain ids, read before the downgrade.
    """
    savings = create_savings_account(
        seed_user, _db.session, "Savings", Decimal("0.00"),
    )
    reserve = create_savings_account(
        seed_user, _db.session, "Reserve", Decimal("0.00"),
    )
    _db.session.commit()
    xfer = create_settled_transfer(
        seed_user, _db.session, seed_user["account"], savings,
        seed_periods[0], amount=Decimal("100.00"),
    )
    other_xfer = create_settled_transfer(
        seed_user, _db.session, seed_user["account"], reserve,
        seed_periods[0], amount=Decimal("100.00"),
    )
    txn = add_txn(
        _db.session, seed_user, seed_periods[0], "Groceries", "40.00",
    )
    _db.session.commit()
    world = {
        "user": seed_user["user"].id,
        "scenario": seed_user["scenario"].id,
        "periods": (seed_periods[0].id, seed_periods[1].id),
        "day": seed_periods[0].start_date,
        "transfers": (xfer.id, other_xfer.id),
        "transaction": txn.id,
        "ledgers": tuple(
            ledger_accounts_for_account(_db.session, account.id)[0].id
            for account in (seed_user["account"], savings, reserve)
        ),
    }
    run_migration_callable(_MIGRATION.downgrade, _db.session)
    return world


def _plant(world, source, legs, **key):
    """INSERT one *source*-kind entry with *legs* ``{ledger id: amount}``; return its id.

    *key* overrides the entry's ``period`` / ``day`` (the world's first
    period and day otherwise) and sets ``transfer_id`` / ``transaction_id`` /
    ``transaction_entry_id``.  The legs carry the ``transfer`` posting kind,
    which head keeps.  Not committed: the deferred balanced trigger reads each
    entry at the caller's commit.
    """
    entry_id = _db.session.execute(text(
        "INSERT INTO budget.journal_entries (user_id, scenario_id, "
        "pay_period_id, entry_date, source_kind_id, transfer_id, "
        "transaction_id, transaction_entry_id, description) "
        "SELECT :u, :s, :p, :d, id, :xfer, :txn, :mov, 'legacy plant' "
        "FROM ref.posting_sources WHERE name = :source RETURNING id"
    ), {
        "u": world["user"], "s": world["scenario"],
        "p": key.get("period", world["periods"][0]),
        "d": key.get("day", world["day"]),
        "xfer": key.get("transfer_id"), "txn": key.get("transaction_id"),
        "mov": key.get("transaction_entry_id"),
        "source": source,
    }).scalar_one()
    for ledger_id, amount in legs.items():
        _db.session.execute(text(
            "INSERT INTO budget.account_postings (journal_entry_id, "
            "ledger_account_id, amount, posting_kind_id) "
            "SELECT :e, :l, :a, id FROM ref.posting_kinds WHERE name = 'transfer'"
        ), {"e": entry_id, "l": ledger_id, "a": amount})
    return entry_id


def _legs(world, amount):
    """Return a leg map moving *amount* out of Checking into Savings."""
    checking, savings, _reserve = world["ledgers"]
    return {checking: -amount, savings: amount}


def _reversed(legs):
    """Return *legs* with every amount's sign flipped: the reversal's legs."""
    return {ledger_id: -amount for ledger_id, amount in legs.items()}


def _entry_ids():
    """Return every journal entry id, ascending."""
    return _db.session.execute(text(
        "SELECT id FROM budget.journal_entries ORDER BY id"
    )).scalars().all()


def _legs_of(entry_ids):
    """Return ``(entry id, ledger id, amount)`` of every leg of *entry_ids*, sorted."""
    return [tuple(row) for row in _db.session.execute(text(
        "SELECT journal_entry_id, ledger_account_id, amount "
        "FROM budget.account_postings WHERE journal_entry_id = ANY(:ids) "
        "ORDER BY journal_entry_id, ledger_account_id"
    ), {"ids": list(entry_ids)}).all()]


def _audited_deletes(table):
    """Return how many DELETE rows the audit log holds for ``budget.<table>``."""
    return _db.session.execute(text(
        "SELECT count(*) FROM system.audit_log WHERE table_schema = 'budget' "
        "AND table_name = :t AND operation = 'DELETE'"
    ), {"t": table}).scalar_one()


def _upgrade_printing():
    """Run the upgrade and return what it printed."""
    printed = io.StringIO()
    with contextlib.redirect_stdout(printed):
        run_migration_callable(_MIGRATION.upgrade, _db.session)
    return printed.getvalue()


class TestTheRevision:
    """Where the revision sits in the chain."""

    def test_revision_and_down_revision(self):
        """revision / down_revision pin the migration onto leaf 1's head."""
        assert _MIGRATION.revision == "1712c506ee99"
        assert _MIGRATION.down_revision == "c956c1d83108"


class TestTheSchemaEachWay:
    """Head holds neither link; the downgrade restores each piece; the upgrade drops it."""

    def test_head_holds_neither_link(self, app, db):  # pylint: disable=unused-argument
        """The test template is built at head: no column, key, index or legacy source.

        Pylint: ``unused-argument`` -- ``db`` is requested for the test's own
        database the catalog is read from.
        """
        with app.app_context():
            assert _schema() == _HEAD

    def test_the_downgrade_restores_each_piece_and_the_upgrade_drops_it(
        self, app, db,  # pylint: disable=unused-argument
    ):
        """Down restores both links by name and definition and both sources; up is head again.

        The keys come back as ``fk_journal_entries_transfer_id`` /
        ``fk_journal_entries_transaction_id`` with ``ON DELETE SET NULL``, the
        names the revisions below drop, and the reference rows by name.  An
        upgrade over no legacy entry deletes none and says so.

        Pylint: ``unused-argument`` -- ``db`` is requested for the test's own
        database the migration runs on.
        """
        with app.app_context():
            run_migration_callable(_MIGRATION.downgrade, _db.session)
            assert _schema() == _BELOW
            printed = _upgrade_printing()
            assert _schema() == _HEAD
            assert (
                "0 legacy journal entry(ies) deleted with their 0 leg(s)"
                in printed
            ), printed
            assert (
                "2 posting source(s) ['transfer', 'transaction'] deleted"
                in printed
            ), printed


class TestTheUpgradeDeletesTheReversedPairs:
    """Every ``transfer`` / ``transaction`` entry goes with its legs; nothing else moves."""

    def test_both_kinds_go_with_their_legs_and_every_other_entry_stays(
        self, app, seed_user, seed_periods,
    ):
        """Two reversed pairs, one of each kind: four entries and eight legs deleted, and audited.

        Each pair nets to zero on every key (same day, period and link), the
        shape the deploy resync left (ruling **R-BAL102**).  The settled
        transfer's two movement entries and every opening are another kind's
        and keep their ids and legs; the audit trigger records one DELETE per
        entry and per leg (the legs go by ``ON DELETE CASCADE``).
        """
        with app.app_context():
            world = _world(seed_user, seed_periods)
            others = _entry_ids()
            others_legs = _legs_of(others)
            assert others_legs, "the world must hold entries a delete could reach"
            transfer = _legs(world, Decimal("100.00"))
            _checking, _savings, reserve = world["ledgers"]
            row = {world["ledgers"][0]: Decimal("-40.00"), reserve: Decimal("40.00")}
            planted = [
                _plant(world, "transfer", transfer,
                       transfer_id=world["transfers"][0]),
                _plant(world, "transfer", _reversed(transfer),
                       transfer_id=world["transfers"][0]),
                _plant(world, "transaction", row,
                       transaction_id=world["transaction"]),
                _plant(world, "transaction", _reversed(row),
                       transaction_id=world["transaction"]),
            ]
            _db.session.commit()
            assert len(_legs_of(planted)) == 8
            entries_audited = _audited_deletes("journal_entries")
            legs_audited = _audited_deletes("account_postings")

            printed = _upgrade_printing()

            assert _entry_ids() == others
            assert _legs_of(others) == others_legs
            assert _legs_of(planted) == []
            assert (
                "4 legacy journal entry(ies) deleted with their 8 leg(s)"
                in printed
            ), printed
            assert _audited_deletes("journal_entries") - entries_audited == 4
            assert _audited_deletes("account_postings") - legs_audited == 8
            assert _schema() == _HEAD

    def test_the_downgrade_restores_no_entry(self, app, seed_user, seed_periods):
        """Down after the delete: both columns back and empty, no legacy entry back (ruling plan#1867)."""
        with app.app_context():
            world = _world(seed_user, seed_periods)
            transfer = _legs(world, Decimal("100.00"))
            _plant(world, "transfer", transfer, transfer_id=world["transfers"][0])
            _plant(world, "transfer", _reversed(transfer),
                   transfer_id=world["transfers"][0])
            _db.session.commit()
            run_migration_callable(_MIGRATION.upgrade, _db.session)

            run_migration_callable(_MIGRATION.downgrade, _db.session)

            assert _schema() == _BELOW
            assert _db.session.execute(text(
                "SELECT count(*) FROM budget.journal_entries je "
                "JOIN ref.posting_sources ps ON ps.id = je.source_kind_id "
                "WHERE ps.name IN ('transfer', 'transaction') "
                "OR je.transfer_id IS NOT NULL OR je.transaction_id IS NOT NULL"
            )).scalar_one() == 0


class TestTheUpgradeRefuses:
    """The upgrade writes nothing while the delete would lose a fact a reader reads."""

    @staticmethod
    def _refused(planted, *matches):
        """Drive the upgrade, assert each of *matches* in its refusal, then grade nothing moved."""
        with pytest.raises(RuntimeError) as raised:
            run_migration_callable(_MIGRATION.upgrade, _db.session)
        _db.session.rollback()
        for match in matches:
            assert match in str(raised.value), str(raised.value)
        assert _schema() == _BELOW
        assert set(planted) <= set(_entry_ids())
        return str(raised.value)

    @pytest.mark.parametrize("link", ["transfer_id", "transaction_id"])
    def test_an_entry_of_another_kind_carrying_a_link(
        self, app, seed_user, seed_periods, link,
    ):
        """A ``purchase`` entry naming a transfer or a row is named by id: no writer makes one."""
        with app.app_context():
            world = _world(seed_user, seed_periods)
            target = (
                world["transfers"][0] if link == "transfer_id"
                else world["transaction"]
            )
            entry_id = _plant(
                world, "purchase", _legs(world, Decimal("5.00")),
                **{link: target},
            )
            _db.session.commit()
            refusal = self._refused(
                [entry_id],
                "1 entry(ies) of a kind other than ['transfer', 'transaction'] "
                f"carry transfer_id or transaction_id, first ids [{entry_id}]",
            )
            assert "group(s)" not in refusal

    def test_an_entry_never_reversed(self, app, seed_user, seed_periods):
        """One ``transfer`` entry alone nets nonzero on both accounts: two groups, each named."""
        with app.app_context():
            world = _world(seed_user, seed_periods)
            legs = _legs(world, Decimal("100.00"))
            entry_id = _plant(
                world, "transfer", legs, transfer_id=world["transfers"][0],
            )
            _db.session.commit()
            first_ledger = min(legs)
            first_group = (
                world["scenario"], first_ledger, world["periods"][0],
                world["day"], "transfer", world["transfers"][0], None, None,
                legs[first_ledger], 1,
            )
            refusal = self._refused(
                [entry_id],
                "2 group(s) of ['transfer', 'transaction'] entries net nonzero",
                repr(first_group),
            )
            assert "entry(ies) of a kind other than" not in refusal

    @pytest.mark.parametrize("apart", ["day", "period", "link"])
    def test_a_pair_netting_zero_overall_but_not_on_one_key(
        self, app, seed_user, seed_periods, apart,
    ):
        """An original and its reversal a DAY, a PERIOD or a LINK apart: four groups refused.

        Summed over the whole ledger the pair is zero, but each half sits in a
        group of its own -- the loan sync's checked projection reads per date,
        the account walk per period, the deleted readers per link -- so the
        delete would move a figure one of them shows.
        """
        with app.app_context():
            world = _world(seed_user, seed_periods)
            legs = _legs(world, Decimal("100.00"))
            key = {
                "day": {"day": world["day"] + timedelta(days=1)},
                "period": {"period": world["periods"][1]},
                "link": {"transfer_id": world["transfers"][1]},
            }[apart]
            planted = [
                _plant(world, "transfer", legs,
                       transfer_id=world["transfers"][0]),
                _plant(world, "transfer", _reversed(legs),
                       **{"transfer_id": world["transfers"][0], **key}),
            ]
            _db.session.commit()
            self._refused(
                planted,
                "4 group(s) of ['transfer', 'transaction'] entries net nonzero",
            )

    def test_a_pair_apart_only_by_its_movement_link(
        self, app, seed_user, seed_periods,
    ):
        """An original carrying a movement link, its reversal not: four groups refused.

        The account walk's and the ledger report's movement readers read any
        entry carrying ``transaction_entry_id`` under that movement, at the
        movement's day, whatever its kind, while the reversal, carrying no
        movement link, is read apart from it (by the base's row loader at the
        row's day, as its period's residue since).  Wherever those days
        differ the delete would move a figure, so the refusal keys on the
        movement link itself rather than on whether they differ here.  The
        first group named is the original's, on the lowest ledger: NULL sorts
        last.
        """
        with app.app_context():
            world = _world(seed_user, seed_periods)
            movement = _db.session.execute(text(
                "SELECT min(transaction_entry_id) FROM budget.journal_entries"
            )).scalar_one()
            assert movement is not None, (
                "the world's settled transfer must post a movement entry"
            )
            legs = _legs(world, Decimal("40.00"))
            planted = [
                _plant(world, "transaction", legs,
                       transaction_id=world["transaction"],
                       transaction_entry_id=movement),
                _plant(world, "transaction", _reversed(legs),
                       transaction_id=world["transaction"]),
            ]
            _db.session.commit()
            first_ledger = min(legs)
            first_group = (
                world["scenario"], first_ledger, world["periods"][0],
                world["day"], "transaction", None, world["transaction"],
                movement, legs[first_ledger], 1,
            )
            self._refused(
                planted,
                "4 group(s) of ['transfer', 'transaction'] entries net nonzero",
                repr(first_group),
            )

    def test_both_refusals_are_named_at_once_by_the_module_level_check(
        self, app, seed_user, seed_periods,
    ):
        """One of each shape: the check itself names both, so one repair pass sees everything.

        Driven through :func:`refuse_what_the_delete_would_move` directly, the
        seam the module exposes for a test, and then through the upgrade.
        """
        with app.app_context():
            world = _world(seed_user, seed_periods)
            linked = _plant(
                world, "purchase", _legs(world, Decimal("5.00")),
                transaction_id=world["transaction"],
            )
            unreversed = _plant(
                world, "transaction", _legs(world, Decimal("40.00")),
                transaction_id=world["transaction"],
            )
            _db.session.commit()
            with pytest.raises(RuntimeError) as raised:
                _MIGRATION.refuse_what_the_delete_would_move(
                    _db.session.connection(),
                )
            _db.session.rollback()
            for match in (
                f"first ids [{linked}]",
                "2 group(s) of ['transfer', 'transaction'] entries net nonzero",
            ):
                assert match in str(raised.value), str(raised.value)
            self._refused(
                [linked, unreversed], f"first ids [{linked}]", "2 group(s)",
            )
