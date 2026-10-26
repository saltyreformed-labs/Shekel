"""The one-row migration's downgrade, the census below it that still refuses, and the revision
below taking what this downgrade writes.
"""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal

import pytest
from sqlalchemy import text

from app.extensions import db as _db
from app.services import transfer_service
from tests._test_helpers import (
    create_account_of_type,
    create_settled_transfer,
    create_transfer,
    run_migration_callable,
)

from ._helpers import (
    _ABOVE,
    _BELOW,
    _downgrade,
    _upgrade,
    _downgrade_counts,
    _twin_rows,
    _twin_of,
    _transfer,
    _status_id,
    _parent_transfer_id,
    _stale_income_twin,
    _accounts,
    _COPIED,
    _what_if_scenario,
    _second_owner,
    _set,
)


def _side_record(transfer_id, *, income):
    """Return the side's payment record's day, basis and link, or ``None``."""
    link = "income_transfer_id" if income else "expense_transfer_id"
    row = _db.session.execute(text(
        "SELECT settled_on, settled_day_basis_id, reconciled_by_id "
        f"FROM budget.transaction_entries WHERE {link} = :t"
    ), {"t": transfer_id}).mappings().one_or_none()
    return dict(row) if row is not None else None


#: Every field a twin mirrors from its transfer, which the downgrade re-reads:
#: the copied ones and the status, which the app at ``e616adf7fe22`` writes on
#: the transfer alone.  Spelled here for the same reason, so a field the
#: migration stops re-reading fails its own case.
_MIRRORED = _COPIED + ("status_id",)


def _transfer_drift(field, request, seed_user, seed_periods, van):
    """The values a statement around the version counter writes on the transfer.

    Each changes *field* as a twin mirrors it; the owner drift moves the
    transfer whole to the second user (both endpoints, its pay period and its
    scenario), as its keys require.
    """
    if field == "user_id":
        second = _second_owner(request)
        return {
            "user_id": second["user_id"],
            "from_account_id": second["checking"],
            "to_account_id": second["savings"],
            "pay_period_id": second["pay_period_id"],
            "scenario_id": second["scenario_id"],
        }
    column, value = {
        "account_id": lambda: ("to_account_id", van.id),
        "pay_period_id": lambda: ("pay_period_id", seed_periods[1].id),
        "scenario_id": lambda: ("scenario_id", _what_if_scenario(seed_user)),
        "status_id": lambda: ("status_id", _status_id("Cancelled")),
        "due_date": lambda: (
            "due_date", seed_periods[0].start_date + timedelta(days=3),
        ),
        "is_override": lambda: ("is_override", True),
        "is_deleted": lambda: ("is_deleted", True),
    }[field]()
    return {column: value}


#: A ``$0.00`` close of transfer ``:t`` at status ``:p``, by SQL: the close
#: states its figure on the transfer, ``0.00`` and who typed it, as the band's
#: figure arm requires of a settled transfer.
_ZERO_CLOSE_SQL = (
    "UPDATE budget.transfers SET status_id = :p, settled_figure = 0.00, "
    "settled_figure_source_id = (SELECT id FROM ref.movement_figure_sources "
    "WHERE name = 'typed') WHERE id = :t"
)


def _side_value(parent, field, *, income):
    """What *parent* says its *income* (or expense) side's twin holds in *field*."""
    if field == "account_id":
        return parent["to_account_id" if income else "from_account_id"]
    return parent[field]


class TestTheDowngrade:
    """The downgrade restores the kept copy exactly, and re-syncs what changed."""

    def test_a_round_trip_restores_every_twin_column_for_column(
        self, app, db, seed_user, seed_periods,
    ):
        """Upgrade then downgrade: every twin's every column, id included.

        The twins are made to carry what a rebuild could NOT reproduce first
        -- a raised version counter, an old update time, and on one income
        twin a category its transfer does not name (finding BAL-575's shape)
        -- so the equality grades the copy, not a rebuild that happens to
        agree.
        """
        with app.app_context():
            savings, van = _accounts(seed_user)
            planned = create_transfer(
                seed_user, db.session, seed_user["account"], savings,
                seed_periods[0], amount=Decimal("100.00"),
            )
            paid = create_settled_transfer(
                seed_user, db.session, savings, van, seed_periods[0],
                amount=Decimal("80.00"),
            )
            hidden = create_transfer(
                seed_user, db.session, seed_user["account"], van,
                seed_periods[1], amount=Decimal("40.00"),
            )
            db.session.commit()
            transfer_service.delete_transfer(
                hidden.id, seed_user["user"].id, soft=True,
            )
            db.session.commit()
            ids = [planned.id, paid.id, hidden.id]

            _downgrade()
            db.session.execute(text(
                "UPDATE budget.transactions SET version_id = version_id + 2, "
                "updated_at = TIMESTAMPTZ '2026-01-02 03:04:05.678901+00' "
                "WHERE transfer_id = ANY(:ids)"
            ), {"ids": ids})
            db.session.execute(text(
                "UPDATE budget.transactions SET category_id = :c WHERE id = :t"
            ), {
                "c": seed_user["categories"]["Rent"].id,
                "t": _twin_of(planned.id, income=True)["id"],
            })
            db.session.commit()
            before = _twin_rows(ids)
            assert len(before) == 6

            _upgrade()
            counts = _downgrade_counts()

            assert _twin_rows(ids) == before
            # Every twin exactly as kept, and printed so.
            assert counts == {
                "restored": 6, "rebuilt": 0, "changed": 0, "unfit": 0,
                "built": 0,
            }
            _upgrade()

    def test_what_changed_since_is_brought_in_line(
        self, app, db, seed_user, seed_periods,
    ):
        """A transfer WRITTEN since gets twins built from it, keeping their identity.

        Its version counter moved, so each twin takes the one build rule
        (``_BUILT_SQL``): ``account_id`` from its side's endpoint, the
        transfer's ``pay_period_id``, ``status_id``, ``due_date``,
        ``is_override``, ``user_id``, ``scenario_id``, ``is_deleted`` and
        ``category_id`` on BOTH sides (the kept to-side's stale category goes),
        ``name`` from the endpoints' current names, the day, basis and link
        from each side's record, and the rule's constants (a derived figure, no
        template, notes or payback).  Only the four identity columns (``id``,
        ``created_at``, ``updated_at``, ``version_id``) are the kept copy's.
        """
        with app.app_context():
            savings, van = _accounts(seed_user)
            xfer = create_transfer(
                seed_user, db.session, seed_user["account"], savings,
                seed_periods[0], amount=Decimal("120.00"),
            )
            db.session.commit()
            _downgrade()
            db.session.execute(text(
                "UPDATE budget.transactions SET category_id = :c WHERE id = :t"
            ), {
                "c": seed_user["categories"]["Rent"].id,
                "t": _twin_of(xfer.id, income=True)["id"],
            })
            db.session.commit()
            kept_expense = _twin_of(xfer.id, income=False)
            kept_income = _twin_of(xfer.id, income=True)
            _upgrade()

            transfer_service.update_transfer(
                xfer.id, seed_user["user"].id,
                pay_period_id=seed_periods[1].id,
                to_account_id=van.id,
                category_id=seed_user["categories"]["Groceries"].id,
                due_date=seed_periods[1].start_date,
            )
            db.session.commit()
            transfer_service.update_transfer(
                xfer.id, seed_user["user"].id,
                status_id=_db.session.execute(text(
                    "SELECT id FROM ref.statuses WHERE name = 'Paid'"
                )).scalar_one(),
            )
            db.session.commit()
            _downgrade()

            parent = _transfer(xfer.id)
            resynced = (
                "account_id", "pay_period_id", "status_id", "due_date",
                "is_override", "user_id", "scenario_id", "is_deleted",
                "category_id", "settled_on", "settled_day_basis_id",
                "reconciled_by_id",
            )
            for kept, income in ((kept_expense, False), (kept_income, True)):
                now = _twin_of(xfer.id, income=income)
                record = _side_record(xfer.id, income=income)
                assert record is not None and record["settled_on"] is not None
                assert now["account_id"] == (
                    parent["to_account_id"] if income
                    else parent["from_account_id"]
                )
                for column in (
                    "pay_period_id", "status_id", "due_date", "is_override",
                    "user_id", "scenario_id", "is_deleted", "category_id",
                ):
                    assert now[column] == parent[column], column
                for column in record:
                    assert now[column] == record[column], column
                # The label is built from the endpoints' current names.
                assert now["name"] == (
                    f"Transfer from {seed_user['account'].name}" if income
                    else "Transfer to One Row Van Fund"
                )
                # The rule's constants: a derived figure and nothing else.
                assert now["estimated_amount"] is None
                assert now["amount_source_id"] == _parent_transfer_id()
                for column in (
                    "template_id", "credit_payback_for_id", "notes", "occurs_on",
                ):
                    assert now[column] is None, column
                assert now["transfer_id"] == xfer.id
                # Only the identity is the kept copy's.
                for column in ("id", "created_at", "updated_at", "version_id"):
                    assert now[column] == kept[column], column
                assert set(now) - set(resynced) == {
                    "name", "estimated_amount", "amount_source_id",
                    "template_id", "credit_payback_for_id", "notes",
                    "occurs_on", "transfer_id", "transaction_type_id", "id",
                    "created_at", "updated_at", "version_id",
                }, "a column this case does not grade"
                assert now["transaction_type_id"] == kept["transaction_type_id"]
            assert kept_income["account_id"] == savings.id
            assert kept_expense["name"] == "Transfer to One Row Savings"
            _upgrade()

    def test_a_transfer_not_written_since_keeps_its_stale_income_category(
        self, app, db, seed_user, seed_periods,
    ):
        """A to-side twin keeps a kept category while its transfer is unwritten.

        "Written since" is the transfer's own version counter, so a write to
        ANOTHER transfer leaves this one's twins exactly as kept: the stale
        income category (finding BAL-575's shape) comes back as it was.
        """
        with app.app_context():
            savings, _van = _accounts(seed_user)
            xfer = create_transfer(
                seed_user, db.session, seed_user["account"], savings,
                seed_periods[0],
            )
            other = create_transfer(
                seed_user, db.session, seed_user["account"], savings,
                seed_periods[1],
            )
            db.session.commit()
            _downgrade()
            stale = _stale_income_twin(seed_user, xfer)
            kept = _twin_rows([xfer.id])
            _upgrade()
            transfer_service.update_transfer(
                other.id, seed_user["user"].id, notes="another transfer",
            )
            db.session.commit()
            _downgrade()

            assert _twin_rows([xfer.id]) == kept
            assert _twin_of(xfer.id, income=True)["category_id"] == stale
            _upgrade()

    def test_a_transfer_created_since_gets_two_new_twins(
        self, app, db, seed_user, seed_periods,
    ):
        """No kept twin: two are built as the create door below built them."""
        with app.app_context():
            savings, _van = _accounts(seed_user)
            xfer = create_transfer(
                seed_user, db.session, seed_user["account"], savings,
                seed_periods[0], amount=Decimal("75.00"),
            )
            db.session.commit()
            parent = _transfer(xfer.id)

            _downgrade()

            expense = _twin_of(xfer.id, income=False)
            income = _twin_of(xfer.id, income=True)
            assert expense["name"] == "Transfer to One Row Savings"
            assert income["name"] == f"Transfer from {seed_user['account'].name}"
            # One id drawn per twin, from-side first: a row expansion that
            # re-ran the draw per column would skip the ids between.
            assert income["id"] == expense["id"] + 1
            for twin in (expense, income):
                assert twin["version_id"] == 1
                assert twin["created_at"] == parent["created_at"]
                # Updated at the downgrade, a later transaction than the
                # transfer's creation.
                assert twin["updated_at"] > parent["created_at"]
                assert twin["estimated_amount"] is None
                assert twin["template_id"] is None
                assert twin["occurs_on"] is None
                assert twin["settled_on"] is None
                for column in (
                    "pay_period_id", "scenario_id", "status_id", "category_id",
                    "due_date", "is_override", "is_deleted", "user_id",
                ):
                    assert twin[column] == parent[column], column
            assert db.session.execute(text(
                "SELECT count(*) FROM information_schema.tables "
                "WHERE table_schema = 'system' "
                "AND table_name = 'transfer_twin_purge'"
            )).scalar_one() == 0
            _upgrade()

    def test_a_zero_close_keeps_its_day_and_a_later_one_has_none(
        self, app, db, seed_user, seed_periods,
    ):
        """A ``$0.00`` close kept unchanged keeps its day; one made after has none.

        A ``$0.00`` close holds no record, so its day lived only on its twins
        below this revision (ruling **R-BAL230** gives none at it).  The kept
        close comes back with its kept day and basis; a transfer closed at
        ``$0.00`` after the upgrade comes back settled with no day, the
        dateless pair the code below calls legacy (finding N-181).  Planted by
        SQL: the band rule admits a settled transfer with no record on either
        side, and no door below this revision states a close's day apart from
        the seam the twins no longer carry.
        """
        with app.app_context():
            savings, van = _accounts(seed_user)
            early = create_transfer(
                seed_user, db.session, seed_user["account"], savings,
                seed_periods[0],
            )
            late = create_transfer(
                seed_user, db.session, seed_user["account"], van,
                seed_periods[0],
            )
            db.session.commit()
            paid = _db.session.execute(text(
                "SELECT id FROM ref.statuses WHERE name = 'Paid'"
            )).scalar_one()
            entered = _db.session.execute(text(
                "SELECT id FROM ref.settled_day_bases WHERE name = 'entered'"
            )).scalar_one()
            _downgrade()
            day = seed_periods[0].start_date
            db.session.execute(text(_ZERO_CLOSE_SQL), {"p": paid, "t": early.id})
            db.session.execute(text(
                "UPDATE budget.transactions SET status_id = :p, "
                "settled_on = :d, settled_day_basis_id = :b "
                "WHERE transfer_id = :t"
            ), {"p": paid, "d": day, "b": entered, "t": early.id})
            db.session.commit()
            _upgrade()
            db.session.execute(text(_ZERO_CLOSE_SQL), {"p": paid, "t": late.id})
            db.session.commit()
            _downgrade()

            for income in (False, True):
                kept = _twin_of(early.id, income=income)
                assert (kept["status_id"], kept["settled_on"]) == (paid, day)
                assert kept["settled_day_basis_id"] == entered
                later = _twin_of(late.id, income=income)
                assert later["status_id"] == paid
                assert later["settled_on"] is None
                assert later["settled_day_basis_id"] is None
            _upgrade()

    def test_a_twin_whose_transfer_was_deleted_since_is_not_restored(
        self, app, db, seed_user, seed_periods,
    ):
        """A hard delete after the upgrade leaves nothing to restore."""
        with app.app_context():
            savings, _van = _accounts(seed_user)
            gone = create_transfer(
                seed_user, db.session, seed_user["account"], savings,
                seed_periods[0],
            )
            kept = create_transfer(
                seed_user, db.session, seed_user["account"], savings,
                seed_periods[1],
            )
            db.session.commit()
            _downgrade()
            _upgrade()
            gone_id = gone.id
            transfer_service.delete_transfer(gone_id, seed_user["user"].id)
            db.session.commit()
            _downgrade()

            assert _twin_rows([gone_id]) == []
            assert len(_twin_rows([kept.id])) == 2
            _upgrade()

    @pytest.mark.parametrize("field", _MIRRORED)
    def test_a_mirrored_field_written_around_the_counter_is_read_live(
        self, app, request, seed_user, seed_periods, field,
    ):
        """A transfer changed by a statement that left its counter: the twin follows.

        The version counter is raised by the ORM alone, so a raw statement or
        a later revision can change a transfer it reads as unwritten.  Each
        mirrored field is re-read from the transfer, so the twin says what its
        transfer says now; every other column is the kept row's, and the
        print counts each twin that differs from its kept row.
        """
        with app.app_context():
            savings, van = _accounts(seed_user)
            xfer = create_transfer(
                seed_user, _db.session, seed_user["account"], savings,
                seed_periods[0],
            )
            _db.session.commit()
            _downgrade()
            kept = {income: _twin_of(xfer.id, income=income)
                    for income in (False, True)}
            _upgrade()
            version = _transfer(xfer.id)["version_id"]
            _set("transfers", xfer.id, _transfer_drift(
                field, request, seed_user, seed_periods, van,
            ))
            assert _transfer(xfer.id)["version_id"] == version

            counts = _downgrade_counts()

            parent = _transfer(xfer.id)
            moved = set()
            for income in (False, True):
                now = _twin_of(xfer.id, income=income)
                for column in _MIRRORED:
                    assert now[column] == _side_value(
                        parent, column, income=income,
                    ), column
                differ = {k for k in now if now[k] != kept[income][k]}
                assert differ <= set(_MIRRORED), differ
                assert field in differ or (field, income) == (
                    "account_id", False,
                ), differ
                if differ:
                    moved.add(income)
            # Only the to-side mirrors the to-account; every other plant
            # moves both twins.
            assert counts == {
                "restored": 2, "rebuilt": 0,
                "changed": 1 if field == "account_id" else 2,
                "unfit": 0, "built": 0,
            }
            assert len(moved) == counts["changed"]
            _upgrade()

    def test_a_database_with_no_copy_gets_two_twins_per_transfer(
        self, app, db, seed_user, seed_periods, capsys,
    ):
        """A database built at head (``create_all``, then a stamp) has no copy.

        ``scripts/init_database.py`` never runs the upgrade, so the copy table
        is missing; the downgrade reads it as empty and finishes (ruling
        **R-BAL261**: the undo always finishes), building two twins for every
        transfer.  Simulated by dropping the copy at head.
        """
        with app.app_context():
            savings, _van = _accounts(seed_user)
            xfer = create_transfer(
                seed_user, db.session, seed_user["account"], savings,
                seed_periods[0], amount=Decimal("35.00"),
            )
            db.session.commit()
            db.session.execute(text("DROP TABLE system.transfer_twin_purge"))
            db.session.commit()
            _downgrade()

            assert [
                (row["account_id"], row["amount_source_id"])
                for row in _twin_rows([xfer.id])
            ] == [
                (seed_user["account"].id, _parent_transfer_id()),
                (savings.id, _parent_transfer_id()),
            ]
            out = capsys.readouterr().out
            assert "0 kept twin(s) restored" in out
            assert (
                "(no copy table was found: none restored, every twin built new)"
            ) in out
            assert db.session.execute(text(
                "SELECT to_regclass('system.transfer_twin_purge') IS NULL"
            )).scalar_one() is True
            _upgrade()

    @pytest.mark.parametrize("column", (
        "settled_on", "settled_day_basis_id", "reconciled_by_id",
    ))
    def test_a_twin_off_its_sides_record_comes_back_saying_the_records(
        self, app, db, seed_user, seed_periods, column,
    ):
        """A to-side twin whose *column* is not its record's: the record's comes back.

        Not a copied field, so this revision's upgrade lets it through
        (``e616adf7fe22``'s upgrade refuses it on production's path, where no
        app writes between the two), and the downgrade re-reads the side's
        record whatever the kept twin held: the twin comes back saying what
        its record says and the print counts it (cp1d review L-1).  The state
        the app at ``e616adf7fe22`` leaves after a day correction, which writes
        the record alone.  The from-side twin comes back exactly as kept.
        """
        with app.app_context():
            savings, _van = _accounts(seed_user)
            paid = create_settled_transfer(
                seed_user, db.session, seed_user["account"], savings,
                seed_periods[0], amount=Decimal("50.00"),
            )
            db.session.commit()
            _downgrade()
            twin = _twin_of(paid.id, income=True)
            record = _side_record(paid.id, income=True)
            off = {
                "settled_on": lambda: record["settled_on"] + timedelta(days=1),
                "settled_day_basis_id": lambda: db.session.execute(text(
                    "SELECT min(id) FROM ref.settled_day_bases WHERE id <> :b"
                ), {"b": record["settled_day_basis_id"]}).scalar_one(),
                "reconciled_by_id": lambda: db.session.execute(text(
                    "SELECT min(id) FROM budget.account_anchor_history "
                    "WHERE account_id = :a"
                ), {"a": savings.id}).scalar_one(),
            }[column]()
            assert off is not None and off != record[column]
            _set("transactions", twin["id"], {column: off})
            kept_from_side = _twin_of(paid.id, income=False)
            _upgrade()

            counts = _downgrade_counts()

            assert _twin_of(paid.id, income=True) == {
                **twin, column: record[column],
            }
            assert _twin_of(paid.id, income=False) == kept_from_side
            assert counts == {
                "restored": 2, "rebuilt": 0, "changed": 1, "unfit": 0,
                "built": 0,
            }
            _upgrade()

    def test_built_twins_take_ids_in_transfer_then_side_order(
        self, app, db, seed_user, seed_periods,
    ):
        """Three transfers, the first stored last: the built ids follow the transfers.

        ``_BUILD_NEW_SQL`` draws each id in transfer-then-side order, which
        one transfer cannot tell from the order a scan meets the rows in.  A
        no-op rewrite moves the first transfer's row to the end of the table,
        asserted by its physical position, so a draw in scan order would hand
        it the last ids (cp1d review X3).
        """
        with app.app_context():
            savings, _van = _accounts(seed_user)
            ids = []
            for period in seed_periods[:3]:
                ids.append(create_transfer(
                    seed_user, db.session, seed_user["account"], savings,
                    period,
                ).id)
                db.session.commit()
            db.session.execute(text(
                "UPDATE budget.transfers SET name = name WHERE id = :t"
            ), {"t": ids[0]})
            db.session.commit()
            stored = [row[0] for row in db.session.execute(text(
                "SELECT id FROM budget.transfers WHERE id = ANY(:ids) "
                "ORDER BY ctid"
            ), {"ids": ids})]
            assert stored == [ids[1], ids[2], ids[0]], stored

            _downgrade()

            rows = sorted(_twin_rows(ids), key=lambda row: row["id"])
            first = rows[0]["id"]
            assert [row["id"] for row in rows] == list(range(first, first + 6))
            assert [
                (row["transfer_id"], row["account_id"] == savings.id)
                for row in rows
            ] == [(xfer, income) for xfer in sorted(ids)
                  for income in (False, True)]
            _upgrade()


class TestTheCensusBelowStillRefuses:
    """``e616adf7fe22``'s drift census, after this downgrade, still REFUSES.

    This revision's step re-keyed that census (a ``$0.00`` close's twins are
    read for status alone, keyed on the SIDE holding no record); the chain
    test below grades what it passes, these grade what it must still refuse.
    """

    def test_a_basis_off_its_sides_record(
        self, app, db, seed_user, seed_periods,
    ):
        """A settled twin whose day basis is not its side's record's is refused."""
        with app.app_context():
            savings, _van = _accounts(seed_user)
            paid = create_settled_transfer(
                seed_user, db.session, seed_user["account"], savings,
                seed_periods[0], amount=Decimal("50.00"),
            )
            db.session.commit()
            _downgrade()
            _BELOW.refuse_drifted_twins(db.session.connection())
            twin = _twin_of(paid.id, income=True)
            other = db.session.execute(text(
                "SELECT min(id) FROM ref.settled_day_bases WHERE id <> :b"
            ), {"b": twin["settled_day_basis_id"]}).scalar_one()
            db.session.execute(text(
                "UPDATE budget.transactions SET settled_day_basis_id = :b "
                "WHERE id = :t"
            ), {"b": other, "t": twin["id"]})
            db.session.commit()

            # The tuple, not the fact's name: the message embeds the census
            # SQL, which spells every fact's name.
            with pytest.raises(RuntimeError) as refused:
                _BELOW.refuse_drifted_twins(db.session.connection())
            assert f"({paid.id}, {twin['id']}, ['day basis'])" in str(
                refused.value
            )

            db.session.rollback()
            db.session.execute(text(
                "UPDATE budget.transactions SET settled_day_basis_id = :b "
                "WHERE id = :t"
            ), {"b": twin["settled_day_basis_id"], "t": twin["id"]})
            db.session.commit()
            _upgrade()

    def test_a_deleted_twin_whose_side_holds_a_record(
        self, app, db, seed_user, seed_periods,
    ):
        """A deleted twin is read against its side's record, and refused.

        Keyed on the SIDE holding a record (L-3 of ``X-bi-6-4d-2``'s cp5
        review), a deleted twin of a settled side is graded like a live one:
        its day is none of its side's record's.
        """
        with app.app_context():
            savings, _van = _accounts(seed_user)
            paid = create_settled_transfer(
                seed_user, db.session, seed_user["account"], savings,
                seed_periods[0], amount=Decimal("50.00"),
            )
            db.session.commit()
            _downgrade()
            twin_id = _twin_of(paid.id, income=True)["id"]
            db.session.execute(text(
                "UPDATE budget.transactions SET is_deleted = TRUE WHERE id = :t"
            ), {"t": twin_id})
            db.session.commit()

            # Read against no record, so its day and basis both differ; its
            # status is its transfer's.
            with pytest.raises(RuntimeError) as refused:
                _BELOW.refuse_drifted_twins(db.session.connection())
            assert (
                f"({paid.id}, {twin_id}, ['settle day', 'day basis'])"
            ) in str(refused.value)

            db.session.rollback()
            db.session.execute(text(
                "UPDATE budget.transactions SET is_deleted = FALSE WHERE id = :t"
            ), {"t": twin_id})
            db.session.commit()
            _upgrade()


def test_the_revision_below_takes_what_this_downgrade_writes(
    app, seed_user, seed_periods,
):
    """b7d2 down, 82f7 down, e616 down, e616 up, 82f7 up, b7d2 up, over every post-upgrade shape.

    ``e616adf7fe22``'s downgrade, which runs next in a chain, requires every
    twin to say its transfer's status and its side's record's day, basis and
    link (cp1 review M-2).  Six shapes made after the upgrade: an endpoint
    move with records, a soft delete of a Paid transfer, a revert, a hard
    delete, a ``$0.00`` close and a transfer created and then marked Paid;
    every record is re-attached to its twin on the way down.  Deliberately
    NOT inside a nested application context: the fixture's idle session
    would hold a lock that
    ``e616adf7fe22``'s ``ALTER TABLE budget.transfers`` waits on.
    """
    del app
    savings = create_account_of_type(
        seed_user, _db.session, "Savings", "Chain Savings",
    )
    van = create_account_of_type(seed_user, _db.session, "Savings", "Chain Van")
    _db.session.commit()
    user_id = seed_user["user"].id
    moved = create_settled_transfer(
        seed_user, _db.session, seed_user["account"], savings,
        seed_periods[0], amount=Decimal("80.00"),
    )
    hidden = create_settled_transfer(
        seed_user, _db.session, seed_user["account"], savings,
        seed_periods[1], amount=Decimal("30.00"),
    )
    reverted = create_settled_transfer(
        seed_user, _db.session, seed_user["account"], van,
        seed_periods[1], amount=Decimal("45.00"),
    )
    gone = create_transfer(
        seed_user, _db.session, seed_user["account"], savings, seed_periods[2],
    )
    zero = create_transfer(
        seed_user, _db.session, seed_user["account"], van, seed_periods[2],
    )
    _db.session.commit()
    ids = {"moved": moved.id, "hidden": hidden.id, "reverted": reverted.id,
           "gone": gone.id, "zero": zero.id}
    _downgrade()
    _upgrade()
    transfer_service.update_transfer(ids["moved"], user_id, to_account_id=van.id)
    _db.session.commit()
    transfer_service.delete_transfer(ids["hidden"], user_id, soft=True)
    _db.session.commit()
    transfer_service.update_transfer(
        ids["reverted"], user_id, status_id=_status_id("Projected"),
    )
    _db.session.commit()
    transfer_service.delete_transfer(ids["gone"], user_id)
    _db.session.commit()
    _db.session.execute(
        text(_ZERO_CLOSE_SQL), {"p": _status_id("Paid"), "t": ids["zero"]},
    )
    _db.session.commit()
    born = create_settled_transfer(
        seed_user, _db.session, savings, van, seed_periods[3],
        amount=Decimal("12.00"),
    )
    _db.session.commit()
    ids["born"] = born.id
    _db.session.expunge_all()
    on_sides = dict(_db.session.execute(text(
        "SELECT coalesce(expense_transfer_id, income_transfer_id), count(*) "
        "FROM budget.transaction_entries "
        "WHERE expense_transfer_id IS NOT NULL "
        "OR income_transfer_id IS NOT NULL GROUP BY 1"
    )).all())

    run_migration_callable(_ABOVE.downgrade, _db.session)
    _downgrade()
    run_migration_callable(_BELOW.downgrade, _db.session)
    records = dict(_db.session.execute(text(
        "SELECT t.transfer_id, count(e.id) FROM budget.transaction_entries e "
        "JOIN budget.transactions t ON t.id = e.transaction_id "
        "WHERE t.transfer_id IS NOT NULL GROUP BY 1"
    )).all())
    run_migration_callable(_BELOW.upgrade, _db.session)
    _upgrade()
    run_migration_callable(_ABOVE.upgrade, _db.session)

    # Every record a side held is back on its side's twin, the moved and the
    # born-since transfers' both sides among them.
    assert records == on_sides
    assert records[ids["moved"]] == records[ids["born"]] == 2


#: Each status act the app makes at ``e616adf7fe22``, which writes the transfer
#: and its sides' records and never a twin: ``(act, whether the transfer is
#: settled before the act, the status the act states)``.
_STATUS_ACTS = (
    ("settle", False, "Paid"),
    ("revert", True, "Projected"),
    ("cancel", False, "Cancelled"),
)


@pytest.mark.parametrize(
    "born_settled, status", [act[1:] for act in _STATUS_ACTS],
    ids=[act[0] for act in _STATUS_ACTS],
)
def test_a_database_the_app_wrote_below_takes_that_revisions_way_down(
    app, seed_user, seed_periods, born_settled, status,
):
    """A status act at ``e616adf7fe22``: up through this revision, then down through both.

    Head's revision (:data:`_ABOVE`) goes down before this one's second
    downgrade and up after its last upgrade, as the chain does.

    From ``e616adf7fe22`` the app writes a settle, revert or cancel on the
    transfer and its sides' records and never on a twin, so afterwards the
    twins' status is not their transfer's, by design (cp1d review M-1).  This
    revision's upgrade does not refuse that (the status is not a copied
    field), and its downgrade re-reads each twin's status from the transfer
    and its day, basis and link from its side's record, which is what
    ``e616adf7fe22``'s downgrade census requires: the route that revision's
    own refusal prescribes, back to head.  Not inside a nested application
    context, for the chain test's reason.
    """
    del app
    savings = create_account_of_type(
        seed_user, _db.session, "Savings", "Status Act Savings",
    )
    _db.session.commit()
    make = create_settled_transfer if born_settled else create_transfer
    xfer_id = make(
        seed_user, _db.session, seed_user["account"], savings,
        seed_periods[0], amount=Decimal("50.00"),
    ).id
    _db.session.commit()
    _downgrade()
    born = {row["status_id"] for row in _twin_rows([xfer_id])}
    transfer_service.update_transfer(
        xfer_id, seed_user["user"].id, status_id=_status_id(status),
    )
    _db.session.commit()
    # The premise: the act wrote the transfer and never a twin.
    assert _transfer(xfer_id)["status_id"] == _status_id(status)
    assert {row["status_id"] for row in _twin_rows([xfer_id])} == born
    assert born != {_status_id(status)}
    records = {
        income: _side_record(xfer_id, income=income) for income in (False, True)
    }
    none = dict.fromkeys(
        ("settled_on", "settled_day_basis_id", "reconciled_by_id"),
    )

    _upgrade()
    run_migration_callable(_ABOVE.downgrade, _db.session)
    counts = _downgrade_counts()

    assert counts == {
        "restored": 2, "rebuilt": 0, "changed": 2, "unfit": 0, "built": 0,
    }
    for income in (False, True):
        twin = _twin_of(xfer_id, income=income)
        assert twin["status_id"] == _status_id(status)
        for column, value in (records[income] or none).items():
            assert twin[column] == value, column
    run_migration_callable(_BELOW.downgrade, _db.session)
    run_migration_callable(_BELOW.upgrade, _db.session)
    _upgrade()
    run_migration_callable(_ABOVE.upgrade, _db.session)

    assert _twin_rows([xfer_id]) == []
    assert {
        income: _side_record(xfer_id, income=income) for income in (False, True)
    } == records
