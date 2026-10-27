"""Migration ``e616adf7fe22``: a transfer side's payment hangs off the transfer.

Plan step ``balance:X-bi-6-4d-2``, rulings **R-BAL88** (two side links),
**R-BAL107** (a transfer's accounts are its owner's) and **R-BAL168** (the side
keys carry a record on an endpoint move).  The revision files every payment
record under a twin row under its TRANSFER by a side link, moving no figure,
day, account or link; it REFUSES, writing nothing, while any stored row could
not take its link; and its downgrade puts each record back under its side's
twin, refusing while a side has none or while a twin no longer says what its
transfer and its side's record say (the cp2b-1 review's L-4).

Driven against the test database, which is built at head, through the shipped
``upgrade`` / ``downgrade``.  **At head no transfer has a twin**: plan step
``balance:X-bi-6-4d-2``'s revision, the one above (:data:`_STEP_ABOVE`),
deletes them and refuses a new one.  So every case that reads or plants a
twin, or downgrades this revision, first runs the step above's downgrade
(:func:`_twins_built`), as the release's own way down does: it builds two
twins for every transfer by its one build rule, each saying its transfer's
status and its side's record's day, basis and link.  This revision's
downgrade then files every record under its side's twin, which is the
pre-step shape its upgrade meets.  Between the two the ORM's models name
columns the table does not have, so that window is raw SQL only.

Every such case first steps down through ``c956c1d83108``
(:data:`_COLUMN_DROP`, by the ``below_the_transfer_column_drop`` fixture on
its class), which puts back the transfer column a twin names.  The model has
had no such column since, so a twin is found and copied by raw SQL that spells
it (:data:`_TWIN_COLUMNS`).  A case whose downgrade of this revision re-files
a record under its twin, or that plants a record as the revisions below hold
it, then runs ``b7d2e4f19a63``'s downgrade (:data:`_SIDE_RECORD_FIGURE`): above
it a side's record holds no figure, its transfer does, and that downgrade
copies the transfer's figure back onto its records, the shape every revision
below holds.  Every case whose last act is an upgrade runs one for each
revision it or its fixture downgraded, :data:`_COLUMN_DROP`'s last, and so
ends at head; a case that ends in a refusal ends where the refusal left it, on
a database the ``db`` fixture clones for that case alone.

**A case whose premise is a settle or a ``$0.00`` close the app made AT this
revision builds the twins first and then drives that act through the app's
own status door**: from this revision that door writes the transfer and its
sides' records and never a twin, so the twins keep the status and the day
they were built with, which is what this revision's downgrade refuses.  Only
that act stands in this way: the head app's create door writes no twin where
the one at this revision wrote two, so a case needing another act made at
this revision would have to plant it.
"""

from __future__ import annotations

from decimal import Decimal

import pytest
from sqlalchemy import text

from app import ref_cache
from app.enums import StatusEnum
from app.extensions import db as _db
from app.models.transaction import Transaction
from app.models.transaction_entry import TransactionEntry
from app.services import transfer_service
from tests._test_helpers import (
    create_account_of_type,
    create_settled_transfer,
    create_transfer,
    load_migration_module,
    run_migration_callable,
    typed,
)

_MIGRATION = load_migration_module(
    "e616adf7fe22_a_transfer_side_s_payment_hangs_off_the_transfer.py",
)

#: Plan step ``balance:X-bi-6-4d-2``'s revision, the one above, whose downgrade
#: builds the twins this revision's downgrade re-attaches records under.
_STEP_ABOVE = load_migration_module("82f7b7ed4332_a_transfer_is_one_row.py")

#: Plan step ``balance:X-bi-6-4d-2``'s last revision, which empties every side
#: record's figure and source onto its transfer's.  Its downgrade, run where the
#: module docstring says, after :data:`_COLUMN_DROP`'s, copies each transfer's
#: figure back onto its records, the shape every revision below it holds; its
#: upgrade runs on the way back, before :data:`_COLUMN_DROP`'s.
_SIDE_RECORD_FIGURE = load_migration_module(
    "b7d2e4f19a63_a_side_record_holds_no_figure.py",
)

#: Plan step ``balance:X-bi-6-5``'s revision, which drops the transfer column a
#: twin names.  Its downgrade is the case's fixture (the module docstring); its
#: upgrade runs LAST on the way back.
_COLUMN_DROP = load_migration_module(
    "c956c1d83108_a_transaction_names_no_transfer.py",
)

#: A twin row's columns less its key and its soft-delete flag, so a raw
#: statement can copy one the way a writer nobody enumerated would.  The
#: model's columns and ``transfer_id``, which every twin holds and the model
#: lost at :data:`_COLUMN_DROP`: without it a copy would name no transfer.
_TWIN_COLUMNS = ", ".join([
    *(column.name for column in Transaction.__table__.columns
      if column.name not in ("id", "is_deleted")),
    "transfer_id",
])

#: A record's columns in the PRE-STEP table: less its key, its row and the two
#: side links this revision adds.
_RECORD_COLUMNS = ", ".join(
    column.name for column in TransactionEntry.__table__.columns
    if column.name not in (
        "id", "transaction_id", "expense_transfer_id", "income_transfer_id",
    )
)

#: What a record IS, less what it is filed under: the fields the revision must
#: not move.
_RECORD_FIELDS = (
    "id, amount, settled_on, settled_day_basis_id, account_id, owner_id, "
    "reconciled_by_id, purchased_on, description, figure_source_id, "
    "covers_settlement, version_id"
)


def _record_ids(transfer_ids):
    """Return the ids of the transfers' records, at head, under either parent."""
    return [row[0] for row in _db.session.execute(text(
        "SELECT e.id FROM budget.transaction_entries e "
        "WHERE coalesce(e.expense_transfer_id, e.income_transfer_id, "
        "  (SELECT t.transfer_id FROM budget.transactions t "
        "    WHERE t.id = e.transaction_id)) = ANY(:ids) "
        "ORDER BY e.id"
    ), {"ids": list(transfer_ids)})]


def _records(entry_ids):
    """Return each record's fields, sorted by id -- readable on either schema."""
    return _db.session.execute(text(
        f"SELECT {_RECORD_FIELDS} FROM budget.transaction_entries "
        "WHERE id = ANY(:ids) ORDER BY id"
    ), {"ids": list(entry_ids)}).all()


def _parents(entry_ids):
    """Return ``{record id: (row id, from-side link, to-side link)}``, at head."""
    return {
        row[0]: tuple(row[1:])
        for row in _db.session.execute(text(
            "SELECT e.id, e.transaction_id, e.expense_transfer_id, "
            "e.income_transfer_id FROM budget.transaction_entries e "
            "WHERE e.id = ANY(:ids)"
        ), {"ids": list(entry_ids)})
    }


def _has_side_links():
    """Return whether ``transaction_entries`` carries the side-link columns."""
    return _db.session.execute(text(
        "SELECT count(*) FROM information_schema.columns "
        "WHERE table_schema = 'budget' AND table_name = 'transaction_entries' "
        "AND column_name IN ('expense_transfer_id', 'income_transfer_id')"
    )).scalar_one() == 2


#: The triggers this revision's two rules attach and the revision below holds
#: none of: the deleted-row rule's transfer arm and the band rule's four.
#: Spelled here, not read off the modules.  A refused upgrade reads none of
#: them, which a renamed attachment would read too, so each case asserts all
#: five exist once this revision's last upgrade in it has run, before the
#: step above's: that is where a rename fails (the cp1f review's L-3).
_RULE_TRIGGERS = (
    "ck_hidden_transfer_holds_nothing",
    "ck_side_record_band_on_insert", "ck_side_record_band_on_update",
    "ck_side_record_band_on_delete", "ck_side_record_band_on_status",
)


def _rule_triggers():
    """Return which of :data:`_RULE_TRIGGERS` exist now, sorted."""
    return sorted(row[0] for row in _db.session.execute(text(
        "SELECT tgname FROM pg_trigger WHERE tgname = ANY(:names)"
    ), {"names": list(_RULE_TRIGGERS)}))


def _pair(seed_user, build):
    """Two transfers on two accounts, each made by *build*, committed; returns ``(a, b)``."""
    savings = create_account_of_type(
        seed_user, _db.session, "Savings", "Migration Savings",
    )
    van = create_account_of_type(
        seed_user, _db.session, "Savings", "Migration Van Fund",
    )
    _db.session.commit()
    first = build(
        seed_user, _db.session, seed_user["account"], savings,
        seed_user["bootstrap_period"], amount=Decimal("500.00"),
    )
    second = build(
        seed_user, _db.session, savings, van,
        seed_user["bootstrap_period"], amount=Decimal("120.00"),
    )
    _db.session.commit()
    return first, second


def _settled_pair(seed_user):
    """Two Paid transfers on two accounts, committed; returns ``(a, b)``."""
    return _pair(seed_user, create_settled_transfer)


def _settle_at_this_revision(seed_user, xfer, **figure):
    """Mark *xfer* Paid through the app's own door, after its twins are built.

    The door writes the transfer and its sides' records and never a twin, so
    the twins keep the status and the day they were built with (Projected
    and none, for a transfer built Projected): what the app from this
    revision on leaves.  *figure* passes a typed figure through
    (``figure=typed(...)``), as :func:`create_settled_transfer` does.
    Committed.
    """
    transfer_service.update_transfer(
        xfer.id, seed_user["user"].id,
        status_id=ref_cache.status_id(StatusEnum.DONE), **figure,
    )
    _db.session.commit()


def _twins_built():
    """Run the step above's downgrade: two twins for every transfer, at this revision.

    Each built by that revision's one build rule: its transfer's status, and
    its side's record's day, basis and statement link (none where the side
    holds no record).  Committed, as every migration call is.
    """
    run_migration_callable(_STEP_ABOVE.downgrade, _db.session)


def _below_this_revision(seed_user):
    """A settled pair at the revision below this one, every record under its twin.

    No twin exists above the step above, so (the case's fixture having put
    back the transfer column) ``b7d2e4f19a63``'s downgrade copies each
    transfer's figure back onto its records (:data:`_SIDE_RECORD_FIGURE`), the
    step above's downgrade builds the twins (:func:`_twins_built`), and this
    revision's downgrade then files each record back under its side's twin:
    the shape this revision's upgrade meets on production.  Committed;
    returns the pair.
    """
    first, second = _settled_pair(seed_user)
    run_migration_callable(_SIDE_RECORD_FIGURE.downgrade, _db.session)
    _twins_built()
    run_migration_callable(_MIGRATION.downgrade, _db.session)
    assert not _has_side_links()
    return first, second


def _up_to_head():
    """Run this revision's upgrade, the step above's, then the two above it: head again."""
    run_migration_callable(_MIGRATION.upgrade, _db.session)
    assert _has_side_links()
    run_migration_callable(_STEP_ABOVE.upgrade, _db.session)
    run_migration_callable(_SIDE_RECORD_FIGURE.upgrade, _db.session)
    run_migration_callable(_COLUMN_DROP.upgrade, _db.session)


class TestTheRevision:
    """Where the revision sits in the chain."""

    def test_revision_and_down_revision(self):
        """revision / down_revision pin the migration into the chain."""
        assert _MIGRATION.revision == "e616adf7fe22"
        assert _MIGRATION.down_revision == "1f431fec6547"


#: Every constraint the revision adds, by table.
_NEW_CONSTRAINTS = {
    "transfers": (
        "uq_transfers_id_from_account", "uq_transfers_id_to_account",
        "fk_transfers_owner_from_account", "fk_transfers_owner_to_account",
    ),
    "transaction_entries": (
        "ck_transaction_entries_one_parent",
        "ck_transaction_entries_side_link_is_a_record",
        "fk_transaction_entries_expense_side",
        "fk_transaction_entries_income_side",
    ),
}

#: Every index the revision adds.
_NEW_INDEXES = (
    "uq_transaction_entries_one_expense_side_record",
    "uq_transaction_entries_one_income_side_record",
)

#: The scratch schema the models are built into for the comparison.
_MODEL_SCHEMA = "parity_budget"


def _catalog(schema):
    """Return what PostgreSQL holds in *schema* for the revision's objects.

    Each definition is PostgreSQL's own spelling (``pg_get_constraintdef``,
    ``pg_indexes.indexdef``), with *schema* written as ``S`` so the two builds
    compare equal when they agree.

    Returns:
        ``(constraints, indexes, columns)``: ``{(table, name):
        definition}``, ``{name: definition}`` and ``{column: (is_nullable,
        data_type)}`` for ``transaction_entries``' three parent columns.
    """
    names = [name for group in _NEW_CONSTRAINTS.values() for name in group]
    constraints = {
        (table, name): definition.replace(f"{schema}.", "S.")
        for table, name, definition in _db.session.execute(text(
            "SELECT c.relname, con.conname, pg_get_constraintdef(con.oid) "
            "FROM pg_constraint con JOIN pg_class c ON c.oid = con.conrelid "
            "JOIN pg_namespace n ON n.oid = c.relnamespace "
            "WHERE n.nspname = :schema AND con.conname = ANY(:names)"
        ), {"schema": schema, "names": names})
    }
    indexes = {
        name: definition.replace(f"{schema}.", "S.")
        for name, definition in _db.session.execute(text(
            "SELECT indexname, indexdef FROM pg_indexes "
            "WHERE schemaname = :schema AND indexname = ANY(:names)"
        ), {"schema": schema, "names": list(_NEW_INDEXES)})
    }
    columns = {
        column: (is_nullable, data_type)
        for column, is_nullable, data_type in _db.session.execute(text(
            "SELECT column_name, is_nullable, data_type "
            "FROM information_schema.columns "
            "WHERE table_schema = :schema "
            "AND table_name = 'transaction_entries' "
            "AND column_name IN ('transaction_id', 'expense_transfer_id', "
            "'income_transfer_id')"
        ), {"schema": schema})
    }
    return constraints, indexes, columns


class TestTheTables:
    """The migrated tables and the models agree on everything the revision adds.

    ``scripts/init_database.py`` builds a fresh database from the MODELS and
    stamps head, while the suite's template replays the chain; a key, check or
    index declared in one and not the other would split the two.  Alembic's
    ``compare_metadata`` reads neither a CHECK nor a partial index's predicate
    (measured: deleting the side-link CHECK from the model, or inverting an
    index's predicate, left it reporting no drift), so the models are built
    into a scratch schema and PostgreSQL's own definitions are compared.
    """

    def test_the_models_build_what_the_revision_built(self, app):
        """Every new constraint and index, and the three parent columns' nullability and type."""
        del app
        budget_tables = [
            table for table in _db.metadata.sorted_tables
            if table.schema == "budget"
        ]
        _db.session.execute(text(f"CREATE SCHEMA {_MODEL_SCHEMA}"))
        _db.metadata.create_all(
            bind=_db.session.connection().execution_options(
                schema_translate_map={"budget": _MODEL_SCHEMA},
            ),
            tables=budget_tables,
        )

        migrated = _catalog("budget")
        modelled = _catalog(_MODEL_SCHEMA)
        _db.session.rollback()

        assert len(migrated[0]) == 8 and len(migrated[1]) == 2
        assert migrated[2] == {
            "transaction_id": ("YES", "integer"),
            "expense_transfer_id": ("YES", "integer"),
            "income_transfer_id": ("YES", "integer"),
        }
        assert modelled == migrated


@pytest.mark.usefixtures("below_the_transfer_column_drop")
class TestTheRoundTrip:
    """Down files every record under its twin, up under its transfer; nothing else moves."""

    def test_down_up_down_moves_only_what_a_record_is_filed_under(
        self, app, seed_user,
    ):
        """Four records, two per transfer: same fields; twin, side link, the SAME twin.

        The second downgrade is the one that re-attaches side-linked records
        (the first meets them under twins whenever the code under test writes
        them there), so each must land back on the very twin it left.
        """
        del app
        ids = tuple(xfer.id for xfer in _settled_pair(seed_user))
        run_migration_callable(_SIDE_RECORD_FIGURE.downgrade, _db.session)
        entry_ids = _record_ids(ids)
        before = _records(entry_ids)
        assert len(before) == 4

        _twins_built()
        run_migration_callable(_MIGRATION.downgrade, _db.session)
        assert not _has_side_links()
        assert _records(entry_ids) == before
        twins = _db.session.execute(text(
            "SELECT e.id, t.id, (t.transaction_type_id = (SELECT id FROM "
            "ref.transaction_types WHERE name = 'Income')) "
            "FROM budget.transaction_entries e "
            "JOIN budget.transactions t ON t.id = e.transaction_id "
            "WHERE t.transfer_id = ANY(:ids) ORDER BY e.id"
        ), {"ids": list(ids)}).all()
        assert [entry_id for entry_id, _twin, _income in twins] == entry_ids

        run_migration_callable(_MIGRATION.upgrade, _db.session)
        assert _has_side_links()
        assert _records(entry_ids) == before
        parents = _parents(entry_ids)
        for entry_id, _twin, is_income in twins:
            row_id, expense_link, income_link = parents[entry_id]
            assert row_id is None
            assert (income_link is not None) is is_income
            assert (expense_link is not None) is (not is_income)
            assert (expense_link or income_link) in ids
        # CONTROL for the downgrade's refusal below: every side has its twin.
        _MIGRATION.refuse_twinless_sides(_db.session.connection())

        run_migration_callable(_MIGRATION.downgrade, _db.session)
        assert not _has_side_links()
        assert _records(entry_ids) == before
        assert _db.session.execute(text(
            "SELECT id, transaction_id FROM budget.transaction_entries "
            "WHERE id = ANY(:ids) ORDER BY id"
        ), {"ids": entry_ids}).all() == [
            (entry_id, twin) for entry_id, twin, _income in twins
        ]
        _up_to_head()

    def test_the_downgrade_withdraws_the_transfer_arm_and_the_upgrade_restores_it(
        self, app,
    ):
        """The trigger set follows the revision: two attachments below it, three at it."""
        del app
        def attachments():
            return sorted(row[0] for row in _db.session.execute(text(
                "SELECT tgname FROM pg_trigger WHERE tgname IN ("
                "'ck_movement_row_not_deleted', 'ck_hidden_row_holds_nothing', "
                "'ck_hidden_transfer_holds_nothing')"
            )))

        _twins_built()
        run_migration_callable(_MIGRATION.downgrade, _db.session)
        assert attachments() == [
            "ck_hidden_row_holds_nothing", "ck_movement_row_not_deleted",
        ]
        run_migration_callable(_MIGRATION.upgrade, _db.session)
        assert attachments() == [
            "ck_hidden_row_holds_nothing", "ck_hidden_transfer_holds_nothing",
            "ck_movement_row_not_deleted",
        ]
        run_migration_callable(_STEP_ABOVE.upgrade, _db.session)
        run_migration_callable(_COLUMN_DROP.upgrade, _db.session)


def _downgraded_with(seed_user, plant_sql, params_of):
    """Downgrade, then stage *plant_sql* over the first transfer's from-side record.

    Returns the transfer pair; the plant is staged, not committed, and the
    caller rolls it back before upgrading.
    """
    first, second = _below_this_revision(seed_user)
    (entry_id, shadow_id) = _db.session.execute(text(
        "SELECT e.id, t.id FROM budget.transaction_entries e "
        "JOIN budget.transactions t ON t.id = e.transaction_id "
        "WHERE t.transfer_id = :t AND t.account_id = :a"
    ), {"t": first.id, "a": first.from_account_id}).one()
    _db.session.execute(
        text(plant_sql),
        params_of(entry_id=entry_id, shadow_id=shadow_id, transfer=first),
    )
    return first, second


def _back_to_head():
    """Discard a staged plant and run both upgrades."""
    _db.session.rollback()
    _up_to_head()


@pytest.mark.usefixtures("below_the_transfer_column_drop")
class TestTheUpgradeRefuses:
    """Every stored row that could not take its link is named; nothing is written."""

    @pytest.mark.parametrize("plant_sql, reason", [
        ("UPDATE budget.transaction_entries SET covers_settlement = FALSE "
         "WHERE id = :entry_id", "not a settlement record"),
        ("UPDATE budget.transactions SET is_deleted = TRUE "
         "WHERE id = :shadow_id", "under a deleted twin"),
        ("UPDATE budget.transfers SET is_deleted = TRUE "
         "WHERE id = :transfer_id", "under a deleted transfer"),
    ])
    def test_a_record_that_cannot_take_its_link(
        self, app, seed_user, plant_sql, reason,
    ):
        """Each arm of the unlinkable census, named with its reason."""
        del app
        _downgraded_with(seed_user, plant_sql, lambda **kw: {
            "entry_id": kw["entry_id"], "shadow_id": kw["shadow_id"],
            "transfer_id": kw["transfer"].id,
        })
        with pytest.raises(RuntimeError, match=reason):
            _MIGRATION.refuse_unlinkable_rows(_db.session.connection())
        _back_to_head()

    def test_a_record_off_its_sides_account(self, app, seed_user):
        """The from-side record moved onto the to-account: the side key would refuse it."""
        del app
        _downgraded_with(
            seed_user,
            "UPDATE budget.transaction_entries SET account_id = :to "
            "WHERE id = :entry_id",
            lambda **kw: {
                "entry_id": kw["entry_id"],
                "to": kw["transfer"].to_account_id,
            },
        )
        with pytest.raises(RuntimeError, match="not on its side's account"):
            _MIGRATION.refuse_unlinkable_rows(_db.session.connection())
        _back_to_head()

    def test_a_transfer_naming_another_users_account(
        self, app, seed_user, second_user,
    ):
        """R-BAL107's census: the owner key would refuse the transfer."""
        del app
        first, _second = _downgraded_with(
            seed_user,
            "UPDATE budget.transfers SET to_account_id = :foreign "
            "WHERE id = :transfer_id",
            lambda **kw: {
                "transfer_id": kw["transfer"].id,
                "foreign": second_user["account"].id,
            },
        )
        with pytest.raises(RuntimeError, match=rf"ids \[{first.id}\]"):
            _MIGRATION.refuse_unlinkable_rows(_db.session.connection())
        _back_to_head()

    def test_two_records_on_one_side(self, app, seed_user):
        """A second from-side twin holding a covering copy: one side, two records.

        Planted the one order the row arm allows: the original twin is hidden
        first (a twin is the row arm's carve-out), a live copy of it takes its
        place, and a copy of the record arrives under the live copy.
        """
        del app
        first, _second = _downgraded_with(
            seed_user,
            "UPDATE budget.transactions SET is_deleted = TRUE "
            "WHERE id = :shadow_id",
            lambda **kw: {"shadow_id": kw["shadow_id"]},
        )
        (entry_id, shadow_id) = _db.session.execute(text(
            "SELECT e.id, t.id FROM budget.transaction_entries e "
            "JOIN budget.transactions t ON t.id = e.transaction_id "
            "WHERE t.transfer_id = :t AND t.account_id = :a"
        ), {"t": first.id, "a": first.from_account_id}).one()
        twin_id = _db.session.execute(text(
            f"INSERT INTO budget.transactions ({_TWIN_COLUMNS}, is_deleted) "
            f"SELECT {_TWIN_COLUMNS}, FALSE FROM budget.transactions "
            "WHERE id = :s RETURNING id"
        ), {"s": shadow_id}).scalar_one()
        _db.session.execute(text(
            "INSERT INTO budget.transaction_entries "
            f"(transaction_id, {_RECORD_COLUMNS}) "
            f"SELECT :twin, {_RECORD_COLUMNS} "
            "FROM budget.transaction_entries WHERE id = :e"
        ), {"twin": twin_id, "e": entry_id})
        with pytest.raises(
            RuntimeError,
            match=rf"more than one movement \(transfer id, is the to-side, "
                  rf"count: \[\({first.id}, False, 2\)\]",
        ):
            _MIGRATION.refuse_unlinkable_rows(_db.session.connection())
        _back_to_head()

    def test_the_upgrade_writes_nothing_before_it_refuses(self, app, seed_user):
        """The refusal runs first: a committed bad row leaves no new column behind."""
        del app
        _downgraded_with(
            seed_user,
            "UPDATE budget.transaction_entries SET covers_settlement = FALSE "
            "WHERE id = :entry_id",
            lambda **kw: {"entry_id": kw["entry_id"]},
        )
        _db.session.commit()
        with pytest.raises(RuntimeError, match="not a settlement record"):
            run_migration_callable(_MIGRATION.upgrade, _db.session)
        _db.session.rollback()
        assert not _has_side_links()
        _db.session.execute(text(
            "UPDATE budget.transaction_entries SET covers_settlement = TRUE "
            "WHERE transaction_id IN (SELECT id FROM budget.transactions "
            "WHERE transfer_id IS NOT NULL)"
        ))
        _db.session.commit()
        _up_to_head()


#: One fact moved on one twin of a settled pair at the revision below, as
#: ``(the fact the census names, the statement that moves it)``: its status
#: off its transfer's, and its day, basis or statement link off its record's.
_PRE_STEP_DRIFTS = (
    ("status", "UPDATE budget.transactions SET status_id = (SELECT id FROM "
     "ref.statuses WHERE name = 'Projected') WHERE id = :twin"),
    ("settle day", "UPDATE budget.transactions "
     "SET settled_on = settled_on + 1 WHERE id = :twin"),
    ("day basis", "UPDATE budget.transactions t SET settled_day_basis_id = "
     "(SELECT min(b.id) FROM ref.settled_day_bases b "
     "WHERE b.id <> t.settled_day_basis_id) WHERE id = :twin"),
    ("statement link", "UPDATE budget.transactions t SET reconciled_by_id = "
     "(SELECT min(h.id) FROM budget.account_anchor_history h "
     "WHERE h.account_id = t.account_id) WHERE id = :twin"),
)


@pytest.mark.usefixtures("below_the_transfer_column_drop")
class TestTheUpgradeRefusesADriftedTwin:
    """A twin that is not what its transfer and record say refuses the UPGRADE.

    Below this revision the app writes a twin's status, day, basis and
    statement link with its transfer and its record, so a twin that disagrees
    is a drift no door wrote; from this revision the app writes those facts
    on the transfer and the record alone, after which the drift could no
    longer be told from its own.  So the upgrade asks the downgrade's own
    census once the side links exist (the cp1d review's M-1, decided by the
    coordinator 2026-10-11), and the refusal rolls the revision back.
    """

    @pytest.mark.parametrize(
        "income", (False, True), ids=("from_side", "to_side"),
    )
    @pytest.mark.parametrize(
        "fact, drift_sql", _PRE_STEP_DRIFTS,
        ids=[fact.replace(" ", "_") for fact, _sql in _PRE_STEP_DRIFTS],
    )
    def test_a_twin_off_its_transfer_or_record_is_named(
        self, app, seed_user, fact, drift_sql, income,
    ):
        """One fact moved on one side's twin: named with that fact alone, before either rule.

        Asked inside the refused transaction, before it is rolled back: the
        census ran once the side links existed and before either rule was
        installed, the placement the module docstring states.  The revision
        then rolls back whole, which PostgreSQL's transactional DDL does and
        no assertion here could fail on (the cp1e review's L-3).
        """
        del app
        first, _second = _below_this_revision(seed_user)
        account = first.to_account_id if income else first.from_account_id
        twin = _db.session.execute(text(
            "SELECT id, status_id, settled_on, settled_day_basis_id, "
            "reconciled_by_id FROM budget.transactions "
            "WHERE transfer_id = :t AND account_id = :a"
        ), {"t": first.id, "a": account}).mappings().one()
        _db.session.execute(text(drift_sql), {"twin": twin["id"]})
        _db.session.commit()

        with pytest.raises(
            RuntimeError, match=r"X-bi-6-4d-2 refuses: 1 twin row\(s\) do not",
        ) as refused:
            run_migration_callable(_MIGRATION.upgrade, _db.session)
        assert _has_side_links()
        assert _rule_triggers() == []
        _db.session.rollback()

        assert f"({first.id}, {twin['id']}, ['{fact}'])" in str(refused.value)
        _db.session.execute(text(
            "UPDATE budget.transactions SET status_id = :status_id, "
            "settled_on = :settled_on, "
            "settled_day_basis_id = :settled_day_basis_id, "
            "reconciled_by_id = :reconciled_by_id WHERE id = :id"
        ), dict(twin))
        _db.session.commit()
        run_migration_callable(_MIGRATION.upgrade, _db.session)
        assert _has_side_links()
        assert _rule_triggers() == sorted(_RULE_TRIGGERS)
        run_migration_callable(_STEP_ABOVE.upgrade, _db.session)
        run_migration_callable(_SIDE_RECORD_FIGURE.upgrade, _db.session)
        run_migration_callable(_COLUMN_DROP.upgrade, _db.session)


@pytest.mark.usefixtures("below_the_transfer_column_drop")
class TestTheDowngradeRefuses:
    """A record whose side has no LIVE twin cannot go back under one."""

    def test_a_side_whose_twin_was_hidden_refuses_the_downgrade_by_name(
        self, app, seed_user,
    ):
        """A twin hidden around the service: named, and nothing is written.

        A deleted twin cannot take the record back -- the row arm the
        downgrade restores refuses a movement arriving under a deleted row --
        so the census names it before any statement runs.
        """
        del app
        first, _second = _below_this_revision(seed_user)
        run_migration_callable(_MIGRATION.upgrade, _db.session)
        _db.session.execute(text(
            "UPDATE budget.transactions SET is_deleted = TRUE "
            "WHERE transfer_id = :t AND account_id = :a"
        ), {"t": first.id, "a": first.from_account_id})
        _db.session.commit()
        with pytest.raises(RuntimeError, match=rf"no live twin.*, {first.id}\)"):
            run_migration_callable(_MIGRATION.downgrade, _db.session)
        _db.session.rollback()
        assert _has_side_links()

    def test_a_side_with_no_twin(self, app, seed_user):
        """A from-side record whose transfer's twins are gone is named; nothing is written.

        The step above's downgrade, the only way down to this revision,
        builds every transfer's two twins (:func:`_twins_built`), so the case
        deletes them after it: at head there are none to delete.
        """
        del app
        savings = create_account_of_type(
            seed_user, _db.session, "Savings", "Twinless Savings",
        )
        _db.session.commit()
        xfer = create_transfer(
            seed_user, _db.session, seed_user["account"], savings,
            seed_user["bootstrap_period"], amount=Decimal("75.00"),
        )
        _db.session.commit()
        run_migration_callable(_SIDE_RECORD_FIGURE.downgrade, _db.session)
        _twins_built()
        assert _db.session.execute(text(
            "DELETE FROM budget.transactions WHERE transfer_id = :t"
        ), {"t": xfer.id}).rowcount == 2
        _db.session.execute(text(
            "INSERT INTO budget.transaction_entries (expense_transfer_id, "
            "account_id, owner_id, user_id, amount, description, "
            "covers_settlement, figure_source_id) "
            "SELECT :t, :a, :u, :u, 75.00, 'Twinless', TRUE, id "
            "FROM ref.movement_figure_sources WHERE name = 'resolved'"
        ), {"t": xfer.id, "a": xfer.from_account_id, "u": xfer.user_id})
        with pytest.raises(RuntimeError, match=rf"\(\d+, {xfer.id}\)"):
            _MIGRATION.refuse_twinless_sides(_db.session.connection())
        _db.session.rollback()


def _from_side_twin(xfer):
    """Return *xfer*'s live from-side twin's id."""
    return _db.session.execute(text(
        "SELECT id FROM budget.transactions "
        "WHERE transfer_id = :t AND account_id = :a AND NOT is_deleted"
    ), {"t": xfer.id, "a": xfer.from_account_id}).scalar_one()


@pytest.mark.usefixtures("below_the_transfer_column_drop")
class TestTheDowngradeRefusesADriftedTwin:
    """A twin that no longer says what its transfer and record say: named, nothing written.

    The cp2b-1 review's L-4: this revision's app writes a side's status, day,
    basis and statement link on the transfer and the side's record alone, so
    the code below it would read a twin it outlived as a drifted pair.
    """

    def test_a_pair_settled_at_this_revision_refuses_the_downgrade_by_name(
        self, app, seed_user,
    ):
        """Two transfers marked Paid by the app: their twins still say Projected.

        Created Projected, their twins built, and then marked Paid through the
        app's own door (:func:`_settle_at_this_revision`).
        """
        del app
        first, second = _pair(seed_user, create_transfer)
        _twins_built()
        for xfer in (first, second):
            _settle_at_this_revision(seed_user, xfer)

        with pytest.raises(RuntimeError, match="no longer say") as refused:
            run_migration_callable(_MIGRATION.downgrade, _db.session)
        _db.session.rollback()

        # Every fact the door left on the twin, not only its status: twins the
        # door had dated would be named for their status alone.
        message = str(refused.value)
        for xfer in (first, second):
            assert (
                f"({xfer.id}, {_from_side_twin(xfer)}, "
                "['status', 'settle day', 'day basis'])" in message
            )
        assert "4 twin row(s)" in message
        assert _has_side_links()

    def test_every_shape_a_pre_step_door_wrote_passes_and_downgrades(
        self, app, seed_user,
    ):
        """CONTROL, planted as the doors below this revision wrote it, not as the census reads.

        Two Paid pairs whose twins mirror their records (the design's M4),
        and a ``$0.00`` close live and soft-deleted: the seam below this
        revision dated both twins of a close on the day it was stated, with no
        record beside them, and its soft delete kept them (the cp2c review's
        M-1, which a control left as the step above's downgrade builds it --
        the census's complement, every twin its transfer's status and its
        side's record's day or none -- could never fail on).  The census
        passes, the whole downgrade runs, and the closes' twins keep their
        day.
        """
        del app
        first, _second = _settled_pair(seed_user)
        live = create_transfer(
            seed_user, _db.session, seed_user["account"], first.to_account,
            seed_user["bootstrap_period"], amount=Decimal("75.00"),
        )
        hidden = create_transfer(
            seed_user, _db.session, seed_user["account"], first.to_account,
            seed_user["bootstrap_period"], amount=Decimal("80.00"),
        )
        _db.session.commit()
        run_migration_callable(_SIDE_RECORD_FIGURE.downgrade, _db.session)
        _twins_built()
        closed_on = _db.session.execute(text(
            "SELECT settled_on FROM budget.transaction_entries "
            "WHERE expense_transfer_id = :t"
        ), {"t": first.id}).scalar_one()
        zero_closes = [live.id, hidden.id]
        # A close of nothing stores ``0.00`` and its typed source on the
        # transfer: head's columns, which no downgrade here drops, and which
        # head's upgrade grades on the way back.
        _db.session.execute(text(
            "UPDATE budget.transfers SET status_id = (SELECT id FROM "
            "ref.statuses WHERE name = 'Paid'), settled_figure = 0.00, "
            "settled_figure_source_id = (SELECT id FROM "
            "ref.movement_figure_sources WHERE name = 'typed') "
            "WHERE id = ANY(:ids)"
        ), {"ids": zero_closes})
        _db.session.execute(text(
            "UPDATE budget.transactions SET status_id = (SELECT id FROM "
            "ref.statuses WHERE name = 'Paid'), settled_on = :d, "
            "settled_day_basis_id = (SELECT id FROM ref.settled_day_bases "
            "WHERE name = 'borrowed') WHERE transfer_id = ANY(:ids)"
        ), {"d": closed_on, "ids": zero_closes})
        _db.session.execute(text(
            "UPDATE budget.transfers SET is_deleted = TRUE WHERE id = :t"
        ), {"t": hidden.id})
        _db.session.execute(text(
            "UPDATE budget.transactions SET is_deleted = TRUE "
            "WHERE transfer_id = :t"
        ), {"t": hidden.id})

        _MIGRATION.refuse_drifted_twins(_db.session.connection())
        run_migration_callable(_MIGRATION.downgrade, _db.session)

        assert not _has_side_links()
        assert _db.session.execute(text(
            "SELECT DISTINCT settled_on FROM budget.transactions "
            "WHERE transfer_id = ANY(:ids)"
        ), {"ids": zero_closes}).scalars().all() == [closed_on]
        _up_to_head()

    def test_a_zero_close_made_at_this_revision_refuses(self, app, seed_user):
        """The carve-out's boundary: a close made HERE leaves its twins Projected.

        Created Projected, its twins built, and then closed at ``$0.00``
        through the app's own door (:func:`_settle_at_this_revision`), which
        writes no record on either side.
        """
        del app
        savings = create_account_of_type(
            seed_user, _db.session, "Savings", "Zero Close Savings",
        )
        _db.session.commit()
        xfer = create_transfer(
            seed_user, _db.session, seed_user["account"], savings,
            seed_user["bootstrap_period"], amount=Decimal("90.00"),
        )
        _db.session.commit()
        _twins_built()
        _settle_at_this_revision(
            seed_user, xfer, figure=typed(Decimal("0.00")),
        )

        with pytest.raises(RuntimeError, match="2 twin row") as refused:
            _MIGRATION.refuse_drifted_twins(_db.session.connection())
        _db.session.rollback()

        assert f"({xfer.id}, {_from_side_twin(xfer)}, ['status'])" in str(
            refused.value,
        )

    def test_a_dated_twin_under_an_unsettled_transfer_is_refused(
        self, app, seed_user,
    ):
        """A recordless twin is read for its day while its transfer is not settled."""
        del app
        savings = create_account_of_type(
            seed_user, _db.session, "Savings", "Unsettled Savings",
        )
        _db.session.commit()
        xfer = create_transfer(
            seed_user, _db.session, seed_user["account"], savings,
            seed_user["bootstrap_period"], amount=Decimal("60.00"),
        )
        _db.session.commit()
        _twins_built()
        twin = _from_side_twin(xfer)
        _db.session.execute(text(
            "UPDATE budget.transactions SET settled_on = :d, "
            "settled_day_basis_id = (SELECT id FROM ref.settled_day_bases "
            "WHERE name = 'entered') WHERE id = :twin"
        ), {"d": seed_user["bootstrap_period"].start_date, "twin": twin})

        with pytest.raises(RuntimeError, match="1 twin row") as refused:
            _MIGRATION.refuse_drifted_twins(_db.session.connection())
        _db.session.rollback()

        assert (
            f"({xfer.id}, {twin}, ['settle day', 'day basis'])"
            in str(refused.value)
        )

    @pytest.mark.parametrize("drift_sql, fact", [
        ("UPDATE budget.transactions SET status_id = (SELECT id FROM "
         "ref.statuses WHERE name = 'Projected') WHERE id = :twin", "status"),
        ("UPDATE budget.transactions SET settled_on = settled_on - 1 "
         "WHERE id = :twin", "settle day"),
        ("UPDATE budget.transactions t SET settled_day_basis_id = (SELECT "
         "min(b.id) FROM ref.settled_day_bases b "
         "WHERE b.id <> t.settled_day_basis_id) WHERE id = :twin", "day basis"),
        ("UPDATE budget.transactions t SET reconciled_by_id = (SELECT "
         "max(h.id) FROM budget.account_anchor_history h "
         "WHERE h.account_id = t.account_id) WHERE id = :twin",
         "statement link"),
    ])
    def test_each_fact_is_read(self, app, seed_user, drift_sql, fact):
        """One fact moved on one in-step twin: that twin is named with that fact alone."""
        del app
        first, _second = _settled_pair(seed_user)
        _twins_built()
        twin = _from_side_twin(first)
        _db.session.execute(text(drift_sql), {"twin": twin})

        with pytest.raises(RuntimeError, match="1 twin row") as refused:
            _MIGRATION.refuse_drifted_twins(_db.session.connection())
        _db.session.rollback()

        assert f"({first.id}, {twin}, ['{fact}'])" in str(refused.value)

    def test_a_deleted_twin_is_read_against_no_record(self, app, seed_user):
        """A hidden copy of a LINKED twin: its record goes back under the LIVE twin, not it.

        So the copy is read against no record, for its day, basis and link
        alike.  The ``$0.00``-close carve-out, which reads a twin for its
        status alone, asks whether the twin's SIDE holds a record, live twin
        or not (the census's docstring), and this side holds one: the copy's
        day, basis and link, each its live twin's, each differ from none.
        """
        del app
        first, _second = _settled_pair(seed_user)
        _db.session.execute(text(
            "UPDATE budget.transaction_entries e SET reconciled_by_id = ("
            "SELECT max(h.id) FROM budget.account_anchor_history h "
            "WHERE h.account_id = e.account_id) "
            "WHERE e.expense_transfer_id = :t"
        ), {"t": first.id})
        _twins_built()
        copy = _db.session.execute(text(
            f"INSERT INTO budget.transactions (is_deleted, {_TWIN_COLUMNS}) "
            f"SELECT TRUE, {_TWIN_COLUMNS} FROM budget.transactions "
            "WHERE id = :twin RETURNING id"
        ), {"twin": _from_side_twin(first)}).scalar_one()

        with pytest.raises(RuntimeError, match="1 twin row") as refused:
            _MIGRATION.refuse_drifted_twins(_db.session.connection())
        _db.session.rollback()

        assert (
            f"({first.id}, {copy}, ['settle day', 'day basis', "
            "'statement link'])" in str(refused.value)
        )
