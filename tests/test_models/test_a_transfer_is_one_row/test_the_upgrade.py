"""The one-row migration's upgrade: what it copies, mirrors and refuses."""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from app.extensions import db as _db
from app.services import transfer_service
from tests._test_helpers import (
    create_account_of_type,
    create_settled_cash_transaction,
    create_settled_transfer,
    create_transfer,
    run_migration_callable,
)

from ._helpers import (
    _MIGRATION,
    _BELOW,
    _downgrade,
    _upgrade,
    _twin_rows,
    _twin_of,
    _transfer,
    _accounts,
    _COPIED,
    _what_if_scenario,
    _new_category,
    _second_owner,
    _set,
)


def _assert_nothing_written(transfer_ids, twins_before):
    """A refused upgrade left the twins, and made no copy and no CHECK.

    Called INSIDE the refused transaction, before the test rolls it back: the
    upgrade runs in the test session's own transaction
    (:func:`run_migration_callable`), so a write made before the refusal is
    visible here and gone once rolled back (cp1d review L-2).
    """
    assert _twin_rows(transfer_ids) == twins_before
    assert _db.session.execute(text(
        "SELECT to_regclass('system.transfer_twin_purge') IS NULL"
    )).scalar_one() is True
    assert _db.session.execute(text(
        "SELECT count(*) FROM pg_constraint "
        "WHERE conname = 'ck_transactions_names_no_transfer'"
    )).scalar_one() == 0


def _twin_drift(field, request, seed_user, seed_periods, van):
    """The values that put a twin's copied *field* off its transfer.

    Returns ``(column values, the fields the refusal then names)``.  The owner
    drift names three fields, since its account and pay period move with it.
    """
    if field == "user_id":
        second = _second_owner(request)
        return {
            "user_id": second["user_id"],
            "account_id": second["checking"],
            "pay_period_id": second["pay_period_id"],
        }, ["user_id", "account_id", "pay_period_id"]
    value = {
        "account_id": lambda: van.id,
        "pay_period_id": lambda: seed_periods[1].id,
        "scenario_id": lambda: _what_if_scenario(seed_user),
        "due_date": lambda: seed_periods[0].start_date + timedelta(days=3),
        "is_override": lambda: True,
        "is_deleted": lambda: True,
    }[field]()
    return {field: value}, [field]


#: The category refusal's repair advice, spelled HERE rather than read off the
#: migration, so a refusal that stops saying which row to repair fails its
#: case (the cp2b-0 review's L-1).  It runs to the message's own clause break,
#: ``"; "``, so no word the message adds after it can turn an arm around (the
#: cp2b-1 review's L-2).
_REPAIR_ADVICE = (
    "where the transfer names a category whose owner is not the transfer's, "
    "the transfer is refused for its own category as well and is a row to "
    "repair, its twins with it; otherwise the twin alone is; "
)


def _cross_user_references():
    """Every transfer and transaction filed under a category not its owner's.

    Returned as ``(table, row id)`` pairs, so an empty list says no row of
    either table names another user's category.
    """
    return [tuple(row) for row in _db.session.execute(text(
        "SELECT 'transfers', x.id FROM budget.transfers x "
        "JOIN budget.categories c ON c.id = x.category_id "
        "WHERE c.user_id <> x.user_id "
        "UNION ALL "
        "SELECT 'transactions', t.id FROM budget.transactions t "
        "JOIN budget.categories c ON c.id = t.category_id "
        "WHERE c.user_id <> t.user_id ORDER BY 1, 2"
    ))]


class TestTheUpgrade:
    """The upgrade keeps each twin whole, deletes it, and refuses a new one."""

    def test_it_keeps_every_twin_whole_and_deletes_it(
        self, app, db, seed_user, seed_periods,
    ):
        """Each twin goes into the copy with its side's record flag, then away."""
        with app.app_context():
            savings, _van = _accounts(seed_user)
            planned = create_transfer(
                seed_user, db.session, seed_user["account"], savings,
                seed_periods[0], amount=Decimal("100.00"),
            )
            paid = create_settled_transfer(
                seed_user, db.session, seed_user["account"], savings,
                seed_periods[0], amount=Decimal("250.00"),
            )
            db.session.commit()
            ids = [planned.id, paid.id]

            _downgrade()
            before = _twin_rows(ids)
            assert len(before) == 4
            _upgrade()

            assert _twin_rows(ids) == []
            kept = db.session.execute(text(
                "SELECT row_id, transfer_id, side_held_record, "
                "transfer_version_id, "
                "(jsonb_populate_record(NULL::budget.transactions, row_data)).* "
                "FROM system.transfer_twin_purge WHERE transfer_id = ANY(:ids) "
                "ORDER BY row_id"
            ), {"ids": ids}).mappings().all()
            assert [row["row_id"] for row in kept] == [
                row["id"] for row in before
            ]
            # The copy is the row, column for column.
            for row, twin in zip(kept, before):
                assert {k: row[k] for k in twin} == twin
            # Only the settled transfer's sides held a record.
            assert {
                (row["transfer_id"], row["side_held_record"]) for row in kept
            } == {(planned.id, False), (paid.id, True)}
            # Each keeps its transfer's version counter, the downgrade's test
            # of "written since".
            assert {
                row["transfer_id"]: row["transfer_version_id"] for row in kept
            } == {i: _transfer(i)["version_id"] for i in ids}

    def test_the_database_refuses_a_twin(self, app, db, seed_user, seed_periods):
        """A row naming a transfer is refused at the write (ruling R-BAL258)."""
        with app.app_context():
            savings, _van = _accounts(seed_user)
            xfer = create_transfer(
                seed_user, db.session, seed_user["account"], savings,
                seed_periods[0],
            )
            db.session.commit()
            _downgrade()
            twin = _twin_rows([xfer.id])[0]
            _upgrade()

            columns = [name for name in twin if name != "id"]
            with pytest.raises(
                IntegrityError, match="ck_transactions_names_no_transfer",
            ):
                db.session.execute(text(
                    f"INSERT INTO budget.transactions ({', '.join(columns)}) "
                    f"VALUES ({', '.join(':' + name for name in columns)})"
                ), {name: twin[name] for name in columns})
                db.session.commit()
            db.session.rollback()

    def test_it_refuses_a_twin_another_row_names(
        self, app, db, seed_user, seed_periods,
    ):
        """A card payback naming a twin refuses the upgrade, writing nothing."""
        with app.app_context():
            savings, _van = _accounts(seed_user)
            xfer = create_transfer(
                seed_user, db.session, seed_user["account"], savings,
                seed_periods[0],
            )
            db.session.commit()
            _downgrade()
            twin = _twin_of(xfer.id, income=False)
            payback = dict(twin)
            payback.pop("id")
            payback.update(transfer_id=None, credit_payback_for_id=twin["id"])
            columns = list(payback)
            db.session.execute(text(
                f"INSERT INTO budget.transactions ({', '.join(columns)}) "
                f"VALUES ({', '.join(':' + name for name in columns)})"
            ), payback)
            db.session.commit()
            before = _twin_rows([xfer.id])

            with pytest.raises(RuntimeError) as refused:
                _upgrade()
            _assert_nothing_written([xfer.id], before)
            db.session.rollback()
            assert (
                f"({twin['id']}, {xfer.id}, "
                "'budget.transactions.credit_payback_for_id')"
            ) in str(refused.value)

            db.session.execute(text(
                "DELETE FROM budget.transactions "
                "WHERE credit_payback_for_id = :t"
            ), {"t": twin["id"]})
            db.session.commit()
            _upgrade()

    def test_it_refuses_a_transfer_whose_twins_are_not_a_pair(
        self, app, db, seed_user, seed_periods,
    ):
        """A transfer missing its income twin refuses the upgrade by name."""
        with app.app_context():
            savings, _van = _accounts(seed_user)
            xfer = create_transfer(
                seed_user, db.session, seed_user["account"], savings,
                seed_periods[0],
            )
            db.session.commit()
            _downgrade()
            income = _twin_of(xfer.id, income=True)
            db.session.execute(text(
                "DELETE FROM budget.transactions WHERE id = :t"
            ), {"t": income["id"]})
            db.session.commit()
            before = _twin_rows([xfer.id])

            with pytest.raises(RuntimeError) as refused:
                _upgrade()
            _assert_nothing_written([xfer.id], before)
            db.session.rollback()
            assert f"({xfer.id}, 1, 0)" in str(refused.value)

            # Put a twin back so the transfer is a pair, then return to head.
            income.pop("id")
            columns = list(income)
            db.session.execute(text(
                f"INSERT INTO budget.transactions ({', '.join(columns)}) "
                f"VALUES ({', '.join(':' + name for name in columns)})"
            ), income)
            db.session.commit()
            _upgrade()

    def test_it_refuses_a_transfer_holding_two_expense_twins(
        self, app, db, seed_user, seed_periods,
    ):
        """A transfer holding a second (deleted) expense twin refuses the upgrade.

        The EXPENSE arm of the pair test; the case above drives the income
        arm.  Let through, both expense twins would come back with
        ``is_deleted`` re-read from the live transfer and the downgrade would
        die on ``uq_transactions_transfer_type_active``.
        """
        with app.app_context():
            savings, _van = _accounts(seed_user)
            xfer = create_transfer(
                seed_user, db.session, seed_user["account"], savings,
                seed_periods[0],
            )
            db.session.commit()
            _downgrade()
            second = _twin_of(xfer.id, income=False)
            second.pop("id")
            second["is_deleted"] = True
            columns = list(second)
            extra = db.session.execute(text(
                f"INSERT INTO budget.transactions ({', '.join(columns)}) "
                f"VALUES ({', '.join(':' + name for name in columns)}) "
                "RETURNING id"
            ), second).scalar_one()
            db.session.commit()
            before = _twin_rows([xfer.id])

            with pytest.raises(RuntimeError) as refused:
                _upgrade()
            _assert_nothing_written([xfer.id], before)
            db.session.rollback()
            assert f"({xfer.id}, 2, 1)" in str(refused.value)

            db.session.execute(text(
                "DELETE FROM budget.transactions WHERE id = :e"
            ), {"e": extra})
            db.session.commit()
            _upgrade()

    def test_it_refuses_a_twin_a_movement_names(
        self, app, db, seed_user, seed_periods,
    ):
        """A movement naming a twin refuses the upgrade by its key's name.

        The second arm graded by a plant, on a table other than the twin's
        own: every arm is the same ``EXISTS`` over a key the catalog names, so
        this and the card payback above grade the arm's shape, and the catalog
        test below grades which keys it is asked over.
        """
        with app.app_context():
            savings, _van = _accounts(seed_user)
            paid = create_settled_transfer(
                seed_user, db.session, seed_user["account"], savings,
                seed_periods[0], amount=Decimal("60.00"),
            )
            db.session.commit()
            _downgrade()
            twin = _twin_of(paid.id, income=False)
            movement = dict(db.session.execute(text(
                "SELECT * FROM budget.transaction_entries "
                "WHERE expense_transfer_id = :t"
            ), {"t": paid.id}).mappings().one())
            movement.pop("id")
            movement.update(expense_transfer_id=None, transaction_id=twin["id"])
            # A side's record states no figure; its transfer holds it, and a
            # movement under a twin states one.
            movement.update(db.session.execute(text(
                "SELECT settled_figure AS amount, "
                "settled_figure_source_id AS figure_source_id "
                "FROM budget.transfers WHERE id = :t"
            ), {"t": paid.id}).mappings().one())
            columns = list(movement)
            planted = db.session.execute(text(
                f"INSERT INTO budget.transaction_entries ({', '.join(columns)}) "
                f"VALUES ({', '.join(':' + name for name in columns)}) "
                "RETURNING id"
            ), movement).scalar_one()
            db.session.commit()
            before = _twin_rows([paid.id])

            with pytest.raises(RuntimeError) as refused:
                _upgrade()
            _assert_nothing_written([paid.id], before)
            db.session.rollback()
            assert (
                f"({twin['id']}, {paid.id}, "
                "'budget.transaction_entries.transaction_id')"
            ) in str(refused.value)
            # One movement is one reference, though its column carries two
            # keys (the single-column one and the composite owner key).
            assert str(refused.value).count(f"({twin['id']}, {paid.id}, ") == 1
            assert "1 twin reference(s)" in str(refused.value)

            db.session.execute(text(
                "DELETE FROM budget.transaction_entries WHERE id = :e"
            ), {"e": planted})
            db.session.commit()
            _upgrade()

    def test_the_catalog_names_every_key_into_transactions(self, app, db):
        """The refusal is asked over every foreign key into a transaction.

        A catalog read that found nothing would leave the refusal asking
        nothing and passing, so the keys this revision's schema holds are
        named: each would null, cascade or refuse at a twin's delete.
        """
        with app.app_context():
            found = set(_MIGRATION.foreign_keys_into_transactions(
                db.session.connection(),
            ))
            assert found >= {
                ("budget.journal_entries", "transaction_id"),
                ("budget.statement_match_creations", "transaction_id"),
                ("budget.transaction_entries", "transaction_id"),
                ("budget.transaction_entries", "credit_payback_id"),
                ("budget.transactions", "credit_payback_for_id"),
            }, found

    def test_a_transfer_holding_no_twin_passes_and_gets_two(
        self, app, db, seed_user, seed_periods,
    ):
        """A twinless transfer is already one row: no refusal, two on the way down.

        The create door at this revision builds no twin, so a transfer it
        writes to a database still at ``e616adf7fe22`` holds none.  The upgrade
        has nothing of it to keep; the downgrade builds its two.
        """
        with app.app_context():
            savings, _van = _accounts(seed_user)
            _downgrade()
            xfer = create_transfer(
                seed_user, db.session, seed_user["account"], savings,
                seed_periods[0],
            )
            db.session.commit()
            assert _twin_rows([xfer.id]) == []

            _MIGRATION.refuse_twins_that_cannot_go(db.session.connection())
            _upgrade()
            _downgrade()

            assert len(_twin_rows([xfer.id])) == 2
            _upgrade()

    @pytest.mark.parametrize(
        "drift",
        [(field, income) for field in _COPIED for income in (False, True)],
        ids=[f"{field}-{side}" for field in _COPIED
             for side in ("from_side", "to_side")],
    )
    def test_it_refuses_a_twin_that_is_no_copy_of_its_transfer(
        self, app, request, seed_user, seed_periods, drift,
    ):
        """A twin whose copied field is not its transfer's refuses the upgrade.

        Named as ``(transfer, twin, [fields])``, writing nothing: the twin is
        no copy of its transfer, so its delete would erase the disagreement
        unseen.  Each field is planted alone (the owner drift with the account
        and pay period its keys move with it), on each side's twin in turn
        (cp1d review L-3), and the refusal must name exactly the fields that
        differ.  *drift* is ``(the field, whether the to-side's twin)``.
        """
        field, income = drift
        with app.app_context():
            savings, van = _accounts(seed_user)
            xfer = create_transfer(
                seed_user, _db.session, seed_user["account"], savings,
                seed_periods[0],
            )
            _db.session.commit()
            _downgrade()
            twin = _twin_of(xfer.id, income=income)
            values, named = _twin_drift(
                field, request, seed_user, seed_periods, van,
            )
            assert all(twin[column] != value for column, value in values.items())
            _set("transactions", twin["id"], values)
            before = _twin_rows([xfer.id])

            with pytest.raises(RuntimeError) as refused:
                _upgrade()
            _assert_nothing_written([xfer.id], before)
            _db.session.rollback()
            assert "1 twin(s) are not a copy of their transfer" in str(
                refused.value
            )
            assert f"({xfer.id}, {twin['id']}, {named!r})" in str(refused.value)

            _set("transactions", twin["id"], {
                column: twin[column] for column in values
            })
            _upgrade()

    @pytest.mark.parametrize(
        "shape",
        [(income, deleted) for deleted in (False, True)
         for income in (False, True)],
        ids=[f"{side}-{state}" for state in ("live", "soft_deleted")
             for side in ("from_side", "to_side")],
    )
    def test_it_refuses_a_twin_filed_under_another_users_category(
        self, app, seed_user, seed_second_user, seed_periods, shape,
    ):
        """A twin under another user's category refuses the upgrade by name.

        No door writes one (both transfer doors refuse another user's
        category, ``_get_owned_category`` and ``_reject_unowned_references``,
        and a category's owner is written only at its create), so it is
        refused, writing nothing, rather than kept and carried back by the
        downgrade.  The transfer is moved under the same category, so the twin
        AGREES with its transfer and is refused for its owner alone, named
        with its transfer's category and that category's owner beside its
        own; that owner is not the transfer's, so the transfer is refused for
        its own category as well: the transfer is a row to repair, that twin
        with it, and the upgrade passes only once both are.  The other twin
        keeps a category its transfer no longer names, which is its owner's
        and passes (the cp1e review's Q1, decided C by the coordinator
        2026-10-11).  Raw statements, on each side's twin in turn, of a live
        transfer and of one the delete door soft-deleted: a hidden transfer's
        twins are kept and deleted like any other, so a refusal that asked
        live twins alone would carry one into the copy (the cp1g review's
        L-2).  *shape* is ``(whether the to-side's twin, whether
        soft-deleted)``.
        """
        income, deleted = shape
        with app.app_context():
            savings = _accounts(seed_user)[0]
            xfer = create_transfer(
                seed_user, _db.session, seed_user["account"], savings,
                seed_periods[0],
            )
            _db.session.commit()
            rent = seed_user["categories"]["Rent"].id
            transfer_service.update_transfer(
                xfer.id, seed_user["user"].id, category_id=rent,
            )
            _db.session.commit()
            if deleted:
                transfer_service.delete_transfer(
                    xfer.id, seed_user["user"].id, soft=True,
                )
                _db.session.commit()
            _downgrade()
            twin = _twin_of(xfer.id, income=income)
            assert twin["category_id"] == rent
            assert twin["is_deleted"] is deleted
            foreign = seed_second_user["categories"]["Rent"].id
            _set("transfers", xfer.id, {"category_id": foreign})
            _set("transactions", twin["id"], {"category_id": foreign})
            before = _twin_rows([xfer.id])

            with pytest.raises(RuntimeError) as refused:
                _upgrade()
            _assert_nothing_written([xfer.id], before)
            _db.session.rollback()
            assert (
                "1 twin(s) are filed under a category that is not their "
                "owner's"
            ) in str(refused.value)
            assert (
                f"({xfer.id}, {twin['id']}, {foreign}, "
                f"{seed_second_user['user'].id}, {foreign}, "
                f"{seed_second_user['user'].id})"
            ) in str(refused.value)
            assert (
                f"({xfer.id}, {foreign}, {seed_second_user['user'].id}, "
                f"{seed_user['user'].id})"
            ) in str(refused.value)

            # The twin alone repaired: the transfer's own refusal stands.
            _set("transactions", twin["id"], {"category_id": rent})
            with pytest.raises(RuntimeError) as refused:
                _upgrade()
            _db.session.rollback()
            assert "twin(s) are filed under" not in str(refused.value)
            assert (
                "1 transfer(s) are filed under a category that is not their "
                "owner's"
            ) in str(refused.value)
            _set("transfers", xfer.id, {"category_id": rent})
            _upgrade()

    def test_the_release_path_refuses_a_twin_filed_under_another_users_category(
        self, app, seed_user, seed_second_user, seed_periods,
    ):
        """Below ``e616adf7fe22``, a twin under another user's category: the release refuses.

        The release runs both upgrades with no app write between;
        ``e616adf7fe22``'s census asks no category, so this revision's refusal
        is the one that names it (the cp1e review's Q2, decided C by the
        coordinator 2026-10-11).  The transfer names no category, so the
        refusal names the twin as the row to repair.  Not inside a nested
        application context, for the chain test's reason.
        """
        del app
        savings = create_account_of_type(
            seed_user, _db.session, "Savings", "Kept Category Savings",
        )
        _db.session.commit()
        xfer_id = create_transfer(
            seed_user, _db.session, seed_user["account"], savings,
            seed_periods[0],
        ).id
        _db.session.commit()
        _downgrade()
        run_migration_callable(_BELOW.downgrade, _db.session)
        twin = _twin_of(xfer_id, income=True)
        foreign = seed_second_user["categories"]["Rent"].id
        _set("transactions", twin["id"], {"category_id": foreign})
        run_migration_callable(_BELOW.upgrade, _db.session)
        before = _twin_rows([xfer_id])

        with pytest.raises(RuntimeError) as refused:
            _upgrade()
        _assert_nothing_written([xfer_id], before)
        _db.session.rollback()
        assert _transfer(xfer_id)["category_id"] is None
        assert (
            f"({xfer_id}, {twin['id']}, {foreign}, "
            f"{seed_second_user['user'].id}, None, None)"
        ) in str(refused.value)
        assert "transfer(s) are filed under" not in str(refused.value)

        _set("transactions", twin["id"], {"category_id": twin["category_id"]})
        _upgrade()

    @pytest.mark.parametrize(
        "income", (False, True), ids=("from_side", "to_side"),
    )
    def test_a_twin_moved_to_another_owner_is_the_row_to_repair(
        self, app, request, seed_user, seed_periods, income,
    ):
        """The category ids agree and the transfer is clean: the twin is the row.

        The transfer and both its twins under the owner's Rent, as the create
        and update doors file them; a raw statement then moves ONE twin's
        owner (with the account and pay period its keys move with it).  Its
        category is now not its owner's, while its transfer's category is
        still the transfer's owner's, so the refusal names the transfer's
        category's owner as the transfer's own and does not refuse the
        transfer: the twin alone is the row to repair (the cp2a review's L-1,
        its RV-A1a).  The copied-field refusal names the same twin.
        """
        with app.app_context():
            savings, van = _accounts(seed_user)
            xfer = create_transfer(
                seed_user, _db.session, seed_user["account"], savings,
                seed_periods[0],
            )
            _db.session.commit()
            rent = seed_user["categories"]["Rent"].id
            transfer_service.update_transfer(
                xfer.id, seed_user["user"].id, category_id=rent,
            )
            _db.session.commit()
            _downgrade()
            twin = _twin_of(xfer.id, income=income)
            assert twin["category_id"] == rent
            values, named = _twin_drift(
                "user_id", request, seed_user, seed_periods, van,
            )
            _set("transactions", twin["id"], values)
            before = _twin_rows([xfer.id])

            with pytest.raises(RuntimeError) as refused:
                _upgrade()
            _assert_nothing_written([xfer.id], before)
            _db.session.rollback()
            assert f"({xfer.id}, {twin['id']}, {named!r})" in str(refused.value)
            assert (
                f"({xfer.id}, {twin['id']}, {rent}, {seed_user['user'].id}, "
                f"{rent}, {seed_user['user'].id})"
            ) in str(refused.value)
            assert "transfer(s) are filed under" not in str(refused.value)

            _set("transactions", twin["id"], {
                column: twin[column] for column in values
            })
            _upgrade()

    def test_a_twin_only_repair_leaves_a_foreign_transfer_refused(
        self, app, seed_user, seed_second_user, seed_periods,
    ):
        """The ids differ and the transfer's category is foreign too: both are named.

        The transfer under the second user's Rent, its income twin under the
        second user's Groceries, by raw statements.  The twin is refused with
        its transfer's category and THAT category's owner beside its own, and
        the transfer is refused for its own category.  Repairing the twin
        alone, the upgrade still refuses the transfer: the downgrade would
        build its twins back under that category (the cp2a review's L-1, its
        RV-A1b, which passed a twin-only repair before the transfer's own
        refusal, option D, decided by the coordinator 2026-10-11).
        """
        with app.app_context():
            savings, _van = _accounts(seed_user)
            xfer = create_transfer(
                seed_user, _db.session, seed_user["account"], savings,
                seed_periods[0],
            )
            _db.session.commit()
            _downgrade()
            twin = _twin_of(xfer.id, income=True)
            rent = seed_second_user["categories"]["Rent"].id
            groceries = seed_second_user["categories"]["Groceries"].id
            second = seed_second_user["user"].id
            owner = seed_user["user"].id
            _set("transfers", xfer.id, {"category_id": rent})
            _set("transactions", twin["id"], {"category_id": groceries})
            before = _twin_rows([xfer.id])

            with pytest.raises(RuntimeError) as refused:
                _upgrade()
            _assert_nothing_written([xfer.id], before)
            _db.session.rollback()
            assert (
                f"({xfer.id}, {twin['id']}, {groceries}, {second}, {rent}, "
                f"{second})"
            ) in str(refused.value)
            assert f"({xfer.id}, {rent}, {second}, {owner})" in str(
                refused.value
            )

            _set("transactions", twin["id"], {
                "category_id": seed_user["categories"]["Groceries"].id,
            })
            before = _twin_rows([xfer.id])
            with pytest.raises(RuntimeError) as refused:
                _upgrade()
            _assert_nothing_written([xfer.id], before)
            _db.session.rollback()
            assert "twin(s) are filed under" not in str(refused.value)
            assert f"({xfer.id}, {rent}, {second}, {owner})" in str(
                refused.value
            )

            _set("transfers", xfer.id, {"category_id": None})
            _upgrade()

    @pytest.mark.parametrize(
        "given_away", (False, True),
        ids=("another_users_category", "its_own_category_given_away"),
    )
    def test_a_foreign_twin_of_a_clean_transfer_is_the_row_to_repair(
        self, app, seed_user, seed_second_user, seed_periods, given_away,
    ):
        """The ids differ and the transfer's category is its owner's: the twin alone is the row.

        The transfer under its owner's Rent, as the update door files it; ONE
        twin, by raw statements, under a category that is not its owner's
        and is not its transfer's either: the second user's Groceries on the
        to-side (the ids differ as on finding BAL-575's older income twins,
        here with a foreign category), or on the from-side a category of the
        owner's own that is then given to the second user.  The twin is refused with its
        transfer's category and THAT category's owner, the transfer's own,
        beside its own; the transfer is not refused, and the advice, read
        whole here, sends the operator to the twin alone.  Following it, the
        upgrade passes and no transfer or row is left under another user's
        category, through a round trip after (the cp2b-0 review's L-1, its
        RV-S3 and RV-S6).  *given_away* is whether the twin's category is the
        owner's own, given away since.
        """
        with app.app_context():
            savings = _accounts(seed_user)[0]
            owner = seed_user["user"].id
            second = seed_second_user["user"].id
            rent = seed_user["categories"]["Rent"].id
            xfer = create_transfer(
                seed_user, _db.session, seed_user["account"], savings,
                seed_periods[0],
            )
            _db.session.commit()
            transfer_service.update_transfer(xfer.id, owner, category_id=rent)
            _db.session.commit()
            _downgrade()
            twin = _twin_of(xfer.id, income=not given_away)
            if given_away:
                category = _new_category(owner, "One Row", "Given Away")
                _set("transactions", twin["id"], {"category_id": category})
                _set("categories", category, {"user_id": second})
            else:
                category = seed_second_user["categories"]["Groceries"].id
                _set("transactions", twin["id"], {"category_id": category})
            before = _twin_rows([xfer.id])

            with pytest.raises(RuntimeError) as refused:
                _upgrade()
            _assert_nothing_written([xfer.id], before)
            _db.session.rollback()
            assert (
                f"({xfer.id}, {twin['id']}, {category}, {second}, {rent}, "
                f"{owner})"
            ) in str(refused.value)
            assert "transfer(s) are filed under" not in str(refused.value)
            assert _REPAIR_ADVICE in str(refused.value)

            # The advice followed: the twin alone repaired.
            _set("transactions", twin["id"], {"category_id": rent})
            _upgrade()
            assert _cross_user_references() == []
            _downgrade()
            assert _cross_user_references() == []
            _upgrade()

    @pytest.mark.parametrize(
        "state", ("live", "soft_deleted", "twinless"),
    )
    def test_it_refuses_a_transfer_filed_under_another_users_category(
        self, app, seed_user, seed_second_user, seed_periods, state,
    ):
        """A transfer under another user's category refuses the upgrade by name.

        Option D (the cp2a review's L-1, decided by the coordinator
        2026-10-11): no door writes one, and the downgrade's one build rule
        files every twin it builds under its transfer's category, so such a
        transfer's twins would come back under another user's category, which
        the next upgrade refuses.  Here the transfer ALONE is foreign (its
        twins keep their owner's category, so no twin is refused), named as
        ``(transfer, category, that category's owner, the transfer's
        owner)``, writing nothing; once repaired, the upgrade passes.  A live
        transfer, one the delete door soft-deleted (the build rule builds its
        twins too) and one holding no twin (the create door at head, run at
        the downgraded state; the downgrade builds its two).
        """
        with app.app_context():
            savings, _van = _accounts(seed_user)
            owner = seed_user["user"].id
            rent = seed_user["categories"]["Rent"].id
            if state == "twinless":
                _downgrade()
            xfer = create_transfer(
                seed_user, _db.session, seed_user["account"], savings,
                seed_periods[0],
            )
            _db.session.commit()
            transfer_service.update_transfer(xfer.id, owner, category_id=rent)
            _db.session.commit()
            if state == "soft_deleted":
                transfer_service.delete_transfer(xfer.id, owner, soft=True)
                _db.session.commit()
            if state != "twinless":
                _downgrade()
            assert len(_twin_rows([xfer.id])) == (
                0 if state == "twinless" else 2
            )
            foreign = seed_second_user["categories"]["Rent"].id
            second = seed_second_user["user"].id
            _set("transfers", xfer.id, {"category_id": foreign})
            before = _twin_rows([xfer.id])

            with pytest.raises(RuntimeError) as refused:
                _upgrade()
            _assert_nothing_written([xfer.id], before)
            _db.session.rollback()
            assert (
                "1 transfer(s) are filed under a category that is not their "
                "owner's"
            ) in str(refused.value)
            assert f"({xfer.id}, {foreign}, {second}, {owner})" in str(
                refused.value
            )
            assert "twin(s)" not in str(refused.value)

            _set("transfers", xfer.id, {"category_id": rent})
            _upgrade()

    def test_an_ordinary_settled_row_beside_the_twins_is_not_refused(
        self, app, db, seed_user, seed_periods,
    ):
        """The reference census names TWINS: a settled ordinary row's movement passes.

        A movement names every settled ordinary transaction, so a census over
        every referenced row would refuse every database holding one.
        """
        with app.app_context():
            savings, _van = _accounts(seed_user)
            xfer = create_transfer(
                seed_user, db.session, seed_user["account"], savings,
                seed_periods[0],
            )
            create_settled_cash_transaction(
                seed_user, db.session, seed_periods[0], Decimal("12.00"),
            )
            db.session.commit()
            assert db.session.execute(text(
                "SELECT count(*) FROM budget.transaction_entries "
                "WHERE transaction_id IS NOT NULL"
            )).scalar_one() >= 1
            _downgrade()
            _upgrade()

            assert _twin_rows([xfer.id]) == []

    def test_an_ordinary_row_under_another_users_category_is_not_refused(
        self, app, db, seed_user, seed_second_user, seed_periods,
    ):
        """The category refusal names TWINS: an ordinary row is not its to ask.

        Its join to the transfer is what finds the twins, so a row naming no
        transfer is never asked, whatever category it carries; this revision
        deletes no such row.  Planted by a raw statement, as every cross-user
        category is.
        """
        with app.app_context():
            savings, _van = _accounts(seed_user)
            xfer = create_transfer(
                seed_user, db.session, seed_user["account"], savings,
                seed_periods[0],
            )
            ordinary = create_settled_cash_transaction(
                seed_user, db.session, seed_periods[0], Decimal("12.00"),
            )
            db.session.commit()
            ordinary_id = ordinary.id
            _downgrade()
            _set("transactions", ordinary_id, {
                "category_id": seed_second_user["categories"]["Rent"].id,
            })

            _upgrade()

            assert _twin_rows([xfer.id]) == []
            assert db.session.execute(text(
                "SELECT category_id FROM budget.transactions WHERE id = :t"
            ), {"t": ordinary_id}).scalar_one() == (
                seed_second_user["categories"]["Rent"].id
            )
