"""A transfer written since the upgrade, and a kept value that no longer fits, each across the
downgrade.
"""

from __future__ import annotations

from decimal import Decimal

import pytest
from sqlalchemy import text

from app import ref_cache
from app.enums import MovementFigureSourceEnum
from app.extensions import db as _db
from app.models.amount_ownership import AmountOwnership
from app.models.settled_figure import SettledFigure
from app.services import transfer_service
from tests._test_helpers import create_account_of_type, create_transfer, restate_account_opening

from ._helpers import (
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
    _new_category,
    _set,
)


def _figures(transfer_id):
    """Each twin's ``(estimated_amount, amount_source_id)``, from-side first."""
    return [
        (_twin_of(transfer_id, income=income)["estimated_amount"],
         _twin_of(transfer_id, income=income)["amount_source_id"])
        for income in (False, True)
    ]


def _plant_zero_close(xfer, day):
    """Settle *xfer* at ``$0.00`` with *day* on both twins, by SQL.

    A ``$0.00`` close holds no record, so its day lived only on its twins below
    this revision.  The band rule admits a settled transfer with no record on
    either side, and no door below this revision states a close's day apart
    from the seam the twins no longer carry, so it is planted.  The transfer
    states the close's figure, ``0.00`` and who typed it, as the band's figure
    arm requires of a settled transfer.  Run at the downgraded state.
    """
    paid = _status_id("Paid")
    entered = _db.session.execute(text(
        "SELECT id FROM ref.settled_day_bases WHERE name = 'entered'"
    )).scalar_one()
    _db.session.execute(text(
        "UPDATE budget.transfers SET status_id = :p, settled_figure = 0.00, "
        "settled_figure_source_id = (SELECT id FROM "
        "ref.movement_figure_sources WHERE name = 'typed') WHERE id = :t"
    ), {"p": paid, "t": xfer.id})
    _db.session.execute(text(
        "UPDATE budget.transactions SET status_id = :p, "
        "settled_on = :d, settled_day_basis_id = :b WHERE transfer_id = :t"
    ), {"p": paid, "d": day, "b": entered, "t": xfer.id})
    _db.session.commit()
    return paid, entered


#: Each write after the upgrade that changes its transfer, by id.  The
#: full-edit form posts the category select on every save, a restore rewrote
#: both twins' categories, and a category changed and changed back leaves the
#: transfer's value as kept -- the three a comparison of values cannot see
#: (cp1 review M-1).  A notes-only save changes nothing a twin mirrors.
_WRITES = ("same_category_save", "restore", "a_to_b_to_a", "notes_only")


def _write(which, xfer, seed_user):
    """Make the write *which* names to *xfer* through the transfer service."""
    user_id = seed_user["user"].id
    if which == "same_category_save":
        transfer_service.update_transfer(
            xfer.id, user_id, category_id=_transfer(xfer.id)["category_id"],
            notes="a save",
        )
    elif which == "restore":
        transfer_service.delete_transfer(xfer.id, user_id, soft=True)
        _db.session.commit()
        transfer_service.restore_transfer(xfer.id, user_id)
    elif which == "a_to_b_to_a":
        kept = _transfer(xfer.id)["category_id"]
        transfer_service.update_transfer(
            xfer.id, user_id,
            category_id=seed_user["categories"]["Groceries"].id,
        )
        _db.session.commit()
        transfer_service.update_transfer(xfer.id, user_id, category_id=kept)
    else:
        transfer_service.update_transfer(xfer.id, user_id, notes="a note")
    _db.session.commit()


class TestATransferWrittenSince:
    """A twin of a transfer written since is the ONE build rule's, identity kept."""

    @pytest.mark.parametrize("which", _WRITES)
    def test_its_twins_are_built_from_it_and_keep_their_identity(
        self, app, db, seed_user, seed_periods, which,
    ):
        """Every written transfer's twins: the transfer's values, the kept id.

        The to-side's stale category becomes the transfer's (the code below
        wrote it onto both twins at each of these but the notes save); the
        figure is derived; the id, creation and update times and version
        counter are the kept ones.
        """
        with app.app_context():
            savings, _van = _accounts(seed_user)
            xfer = create_transfer(
                seed_user, db.session, seed_user["account"], savings,
                seed_periods[0], amount=Decimal("90.00"),
            )
            db.session.commit()
            _downgrade()
            stale = _stale_income_twin(seed_user, xfer)
            kept = {income: _twin_of(xfer.id, income=income)
                    for income in (False, True)}
            _upgrade()
            _write(which, xfer, seed_user)
            _downgrade()

            parent = _transfer(xfer.id)
            assert parent["category_id"] != stale
            for income in (False, True):
                now = _twin_of(xfer.id, income=income)
                assert now["category_id"] == parent["category_id"], which
                assert (now["estimated_amount"], now["amount_source_id"]) == (
                    None, _parent_transfer_id(),
                )
                assert now["is_deleted"] is False
                for column in ("id", "created_at", "updated_at", "version_id",
                               "transaction_type_id"):
                    assert now[column] == kept[income][column], column
            _upgrade()

    def test_a_save_that_changes_nothing_leaves_its_twins_as_kept(
        self, app, db, seed_user, seed_periods,
    ):
        """A save restating every value writes no row, so the twins are exact.

        The code below would have written the transfer's category onto the
        stale to-side twin at such a save; nothing in the database records a
        save that changed nothing on its transfer, and ruling R-BAL260 brings a
        twin in line with what CHANGED on its transfer, so it comes back as
        kept -- the one residue of the counter's test, and on a hidden row.
        """
        with app.app_context():
            savings, _van = _accounts(seed_user)
            xfer = create_transfer(
                seed_user, db.session, seed_user["account"], savings,
                seed_periods[0],
            )
            db.session.commit()
            _downgrade()
            _stale_income_twin(seed_user, xfer)
            kept = _twin_rows([xfer.id])
            version = _transfer(xfer.id)["version_id"]
            _upgrade()
            transfer_service.update_transfer(
                xfer.id, seed_user["user"].id,
                category_id=_transfer(xfer.id)["category_id"],
            )
            db.session.commit()
            assert _transfer(xfer.id)["version_id"] == version
            _downgrade()

            assert _twin_rows([xfer.id]) == kept
            _upgrade()

    def test_a_figure_typed_since_comes_back_derived(
        self, app, db, seed_user, seed_periods,
    ):
        """Kept legs owning 100.00, the parent re-typed to 400.00: both derive.

        The code below wrote a typed figure onto both legs, so kept legs can
        own the OLD figure (cp1 review H-2).  A derived leg prices its
        parent's 400.00 and cannot disagree with it; an owned leg kept at
        100.00 under a 400.00 parent breaks Transfer Invariant 3.
        """
        with app.app_context():
            savings, _van = _accounts(seed_user)
            typed = create_transfer(
                seed_user, db.session, seed_user["account"], savings,
                seed_periods[0], amount=Decimal("100.00"),
            )
            born = create_transfer(
                seed_user, db.session, seed_user["account"], savings,
                seed_periods[1], amount=Decimal("100.00"),
            )
            db.session.commit()
            _downgrade()
            # The state the update below leaves after a typed 100.00.
            db.session.execute(text(
                "UPDATE budget.transactions SET estimated_amount = 100.00, "
                "amount_source_id = NULL WHERE transfer_id = :t"
            ), {"t": typed.id})
            db.session.commit()
            _upgrade()
            for xfer in (typed, born):
                transfer_service.update_transfer(
                    xfer.id, seed_user["user"].id,
                    amount_ownership=AmountOwnership.own(Decimal("400.00")),
                )
                db.session.commit()
            _downgrade()

            derived = [(None, _parent_transfer_id())] * 2
            for xfer in (typed, born):
                assert _transfer(xfer.id)["amount"] == Decimal("400.00")
                assert _figures(xfer.id) == derived
            _upgrade()

    def test_an_unwritten_owned_figure_comes_back_exactly(
        self, app, db, seed_user, seed_periods,
    ):
        """Kept legs owning 100.00 under an unwritten parent: kept, as owned."""
        with app.app_context():
            savings, _van = _accounts(seed_user)
            xfer = create_transfer(
                seed_user, db.session, seed_user["account"], savings,
                seed_periods[0], amount=Decimal("100.00"),
            )
            db.session.commit()
            _downgrade()
            db.session.execute(text(
                "UPDATE budget.transactions SET estimated_amount = 100.00, "
                "amount_source_id = NULL WHERE transfer_id = :t"
            ), {"t": xfer.id})
            db.session.commit()
            _upgrade()
            _downgrade()

            assert _figures(xfer.id) == [(Decimal("100.00"), None)] * 2
            _upgrade()

    def test_an_override_flag_set_since_is_on_both_twins(
        self, app, db, seed_user, seed_periods,
    ):
        """A transfer flagged ``is_override`` after the upgrade: both twins say so.

        The update below this revision mirrored the flag onto both twins, so
        the build rule takes the transfer's flag rather than the create door's
        unflagged birth.
        """
        with app.app_context():
            savings, _van = _accounts(seed_user)
            xfer = create_transfer(
                seed_user, db.session, seed_user["account"], savings,
                seed_periods[0], amount=Decimal("20.00"),
            )
            db.session.commit()
            _downgrade()
            _upgrade()
            transfer_service.update_transfer(
                xfer.id, seed_user["user"].id, is_override=True,
            )
            db.session.commit()
            assert _transfer(xfer.id)["is_override"] is True
            _downgrade()

            assert [row["is_override"] for row in _twin_rows([xfer.id])] == [
                True, True,
            ]
            _upgrade()

    def test_a_soft_delete_since_comes_back_hidden(
        self, app, db, seed_user, seed_periods,
    ):
        """A transfer hidden after the upgrade gets hidden twins (cp1 review M-2)."""
        with app.app_context():
            savings, _van = _accounts(seed_user)
            xfer = create_transfer(
                seed_user, db.session, seed_user["account"], savings,
                seed_periods[0],
            )
            db.session.commit()
            _downgrade()
            _upgrade()
            transfer_service.delete_transfer(
                xfer.id, seed_user["user"].id, soft=True,
            )
            db.session.commit()
            _downgrade()

            assert [row["is_deleted"] for row in _twin_rows([xfer.id])] == [
                True, True,
            ]
            _upgrade()

    def test_a_zero_close_reverted_and_closed_again_has_no_day(
        self, app, db, seed_user, seed_periods,
    ):
        """A kept ``$0.00`` close re-closed since comes back dateless (cp1 review L-1).

        Its status is the kept one again, so a comparison of status alone
        would put back the kept day; the close made after the upgrade has no
        day anywhere (ruling R-BAL230), and the counter sees the writes.
        """
        with app.app_context():
            savings, _van = _accounts(seed_user)
            xfer = create_transfer(
                seed_user, db.session, seed_user["account"], savings,
                seed_periods[0],
            )
            db.session.commit()
            _downgrade()
            paid, _entered = _plant_zero_close(xfer, seed_periods[0].start_date)
            _upgrade()
            # The revert of a ``$0.00`` close clears its figure; the close
            # again states ``0.00`` and who typed it.
            for status, figure in (
                (_status_id("Projected"), None),
                (paid, SettledFigure(
                    Decimal("0.00"),
                    ref_cache.movement_figure_source_id(
                        MovementFigureSourceEnum.TYPED,
                    ),
                )),
            ):
                row = db.session.get(type(xfer), xfer.id)
                row.status_id = status
                row.settled_figure_pair = figure
                db.session.commit()
            # The kept day would no longer fit either; a written transfer's
            # twins take none of their kept values, so none counts as unfit.
            restate_account_opening(
                db.session, savings, seed_periods[0].start_date,
            )
            db.session.commit()

            assert _downgrade_counts() == {
                "restored": 2, "rebuilt": 2, "changed": 0, "unfit": 0,
                "built": 0,
            }
            for income in (False, True):
                twin = _twin_of(xfer.id, income=income)
                assert (twin["status_id"], twin["settled_on"]) == (paid, None)
                assert twin["settled_day_basis_id"] is None
            _upgrade()

    def test_a_linked_zero_close_passes_the_revision_below(
        self, app, db, seed_user, seed_periods,
    ):
        """A kept ``$0.00`` close linked to a statement: e616's census lets it by.

        Below ``e616adf7fe22`` such a close's day, basis and link lived on its
        twins alone, so that revision's census reads its twins for status
        alone and its downgrade leaves the link where the code below reads it
        (``X-bi-6-4d-2``'s cp5 review L-3).
        """
        with app.app_context():
            savings, _van = _accounts(seed_user)
            xfer = create_transfer(
                seed_user, db.session, seed_user["account"], savings,
                seed_periods[0],
            )
            db.session.commit()
            _downgrade()
            _plant_zero_close(xfer, seed_periods[0].start_date)
            for account in (seed_user["account"], savings):
                db.session.execute(text(
                    "UPDATE budget.transactions SET reconciled_by_id = ("
                    "SELECT min(id) FROM budget.account_anchor_history "
                    "WHERE account_id = :a) "
                    "WHERE transfer_id = :t AND account_id = :a"
                ), {"a": account.id, "t": xfer.id})
            db.session.commit()
            linked = _twin_rows([xfer.id])
            _upgrade()
            _downgrade()

            assert _twin_rows([xfer.id]) == linked
            _BELOW.refuse_drifted_twins(db.session.connection())
            db.session.execute(text(
                "UPDATE budget.transactions SET reconciled_by_id = NULL "
                "WHERE transfer_id = :t"
            ), {"t": xfer.id})
            db.session.commit()
            _upgrade()

    def test_an_endpoint_moved_away_and_back_is_renamed(
        self, app, db, seed_user, seed_periods,
    ):
        """A to B to A after a rename: the labels name the accounts now (L-2)."""
        with app.app_context():
            savings, van = _accounts(seed_user)
            xfer = create_transfer(
                seed_user, db.session, seed_user["account"], savings,
                seed_periods[0],
            )
            db.session.commit()
            _downgrade()
            _upgrade()
            savings.name = "One Row Renamed"
            db.session.commit()
            for account in (van, savings):
                transfer_service.update_transfer(
                    xfer.id, seed_user["user"].id, to_account_id=account.id,
                )
                db.session.commit()
            _downgrade()

            assert _twin_of(xfer.id, income=False)["name"] == (
                "Transfer to One Row Renamed"
            )
            assert _twin_of(xfer.id, income=True)["account_id"] == savings.id
            _upgrade()


class TestAKeptValueThatNoLongerFits:
    """A kept value that no longer fits comes back as the transfer says (R-BAL261)."""

    def test_a_category_the_screen_deleted(
        self, app, db, auth_client, seed_user, seed_periods,
    ):
        """The stale category, deleted on its screen: the transfer's comes back.

        Once the twins are gone nothing uses it, so the category screen
        deletes it outright (cp1 review H-1).  Every other kept column stays.
        """
        with app.app_context():
            savings, _van = _accounts(seed_user)
            xfer = create_transfer(
                seed_user, db.session, seed_user["account"], savings,
                seed_periods[0],
            )
            db.session.commit()
            _downgrade()
            incoming = _new_category(
                seed_user["user"].id, "Transfers", "Incoming",
            )
            db.session.execute(text(
                "UPDATE budget.transactions SET category_id = :c WHERE id = :t"
            ), {"c": incoming, "t": _twin_of(xfer.id, income=True)["id"]})
            db.session.commit()
            kept = {income: _twin_of(xfer.id, income=income)
                    for income in (False, True)}
            _upgrade()
            response = auth_client.post(f"/categories/{incoming}/delete")
            assert response.status_code == 302
            assert db.session.execute(text(
                "SELECT count(*) FROM budget.categories WHERE id = :c"
            ), {"c": incoming}).scalar_one() == 0

            assert _downgrade_counts() == {
                "restored": 2, "rebuilt": 0, "changed": 1, "unfit": 1,
                "built": 0,
            }
            income = _twin_of(xfer.id, income=True)
            assert income["category_id"] == _transfer(xfer.id)["category_id"]
            assert {k: v for k, v in income.items() if k != "category_id"} == {
                k: v for k, v in kept[True].items() if k != "category_id"
            }
            assert _twin_of(xfer.id, income=False) == kept[False]
            _upgrade()

    def test_a_day_the_books_now_open_after(
        self, app, db, seed_user, seed_periods,
    ):
        """A kept ``$0.00``-close day before a restated opening: no day, that side.

        The opening is restated past the kept day on the to-side's account only
        after the upgrade; that twin comes back settled with no day, basis or
        link, and the from-side keeps its day.
        """
        with app.app_context():
            savings, _van = _accounts(seed_user)
            xfer = create_transfer(
                seed_user, db.session, seed_user["account"], savings,
                seed_periods[0],
            )
            db.session.commit()
            _downgrade()
            day = seed_periods[0].start_date
            paid, entered = _plant_zero_close(xfer, day)
            _upgrade()
            restate_account_opening(db.session, savings, day)
            db.session.commit()

            assert _downgrade_counts() == {
                "restored": 2, "rebuilt": 0, "changed": 1, "unfit": 1,
                "built": 0,
            }
            moved = _twin_of(xfer.id, income=True)
            assert (moved["status_id"], moved["settled_on"]) == (paid, None)
            assert moved["settled_day_basis_id"] is None
            kept = _twin_of(xfer.id, income=False)
            assert (kept["settled_on"], kept["settled_day_basis_id"]) == (
                day, entered,
            )
            _upgrade()

    def test_a_statement_link_whose_statement_is_gone(
        self, app, db, seed_user, seed_periods,
    ):
        """A kept link to a statement since gone comes back as none; one still there stays.

        A statement level goes with its bank import, and nothing holds it once
        the twins are gone.  Planted in the copy, which has no keys: one side's
        kept link names a statement that no longer exists, the other's names
        its account's own.
        """
        with app.app_context():
            savings, _van = _accounts(seed_user)
            xfer = create_transfer(
                seed_user, db.session, seed_user["account"], savings,
                seed_periods[0],
            )
            db.session.commit()
            _downgrade()
            day = seed_periods[0].start_date
            _plant_zero_close(xfer, day)
            _upgrade()
            standing = db.session.execute(text(
                "SELECT min(id) FROM budget.account_anchor_history "
                "WHERE account_id = :a"
            ), {"a": seed_user["account"].id}).scalar_one()
            gone = db.session.execute(text(
                "SELECT max(id) + 1000 FROM budget.account_anchor_history"
            )).scalar_one()
            for income, link in ((False, standing), (True, gone)):
                db.session.execute(text(
                    "UPDATE system.transfer_twin_purge SET row_data = "
                    "jsonb_set(row_data, '{reconciled_by_id}', to_jsonb(:l)) "
                    "WHERE transfer_id = :t AND "
                    "(row_data->>'account_id')::integer = :a"
                ), {"l": link, "t": xfer.id,
                    "a": savings.id if income else seed_user["account"].id})
            db.session.commit()

            assert _downgrade_counts() == {
                "restored": 2, "rebuilt": 0, "changed": 1, "unfit": 1,
                "built": 0,
            }
            linked = _twin_of(xfer.id, income=False)
            assert (linked["settled_on"], linked["reconciled_by_id"]) == (
                day, standing,
            )
            unlinked = _twin_of(xfer.id, income=True)
            assert (unlinked["settled_on"], unlinked["reconciled_by_id"]) == (
                day, None,
            )
            db.session.execute(text(
                "UPDATE budget.transactions SET reconciled_by_id = NULL "
                "WHERE transfer_id = :t"
            ), {"t": xfer.id})
            db.session.commit()
            _upgrade()

    def test_a_category_that_is_not_its_owners(
        self, app, db, seed_user, seed_second_user, seed_periods,
    ):
        """A transfer moved to another user by a statement: its owner's category comes back.

        The owner is a mirrored field, re-read from the transfer, so the kept
        category, still the first user's, is not the twin's owner's now; no
        key on a row's category asks its owner, so the downgrade asks it
        (cp1d review L-4).  The upgrade refused a twin whose category was not
        its owner's, so here it can only mean the owner moved.  Reachable only
        by a raw statement: no door moves a transfer between users, and the
        owner keys make it move with its accounts, its period and its
        scenario.
        """
        with app.app_context():
            savings, _van = _accounts(seed_user)
            xfer = create_transfer(
                seed_user, db.session, seed_user["account"], savings,
                seed_periods[0],
            )
            db.session.commit()
            transfer_service.update_transfer(
                xfer.id, seed_user["user"].id,
                category_id=seed_user["categories"]["Rent"].id,
            )
            db.session.commit()
            _downgrade()
            _upgrade()
            second_savings = create_account_of_type(
                seed_second_user, db.session, "Savings", "Second Owner Savings",
            )
            db.session.commit()
            _set("transfers", xfer.id, {
                "user_id": seed_second_user["user"].id,
                "from_account_id": seed_second_user["account"].id,
                "to_account_id": second_savings.id,
                "pay_period_id": seed_second_user["bootstrap_period"].id,
                "scenario_id": seed_second_user["scenario"].id,
                "category_id": seed_second_user["categories"]["Rent"].id,
            })

            counts = _downgrade_counts()

            for income in (False, True):
                twin = _twin_of(xfer.id, income=income)
                assert twin["user_id"] == seed_second_user["user"].id
                assert twin["category_id"] == (
                    seed_second_user["categories"]["Rent"].id
                )
            assert counts == {
                "restored": 2, "rebuilt": 0, "changed": 2, "unfit": 2,
                "built": 0,
            }
            _upgrade()

    def test_a_transfer_moved_with_its_own_category_left_behind(
        self, app, db, seed_user, seed_second_user, seed_periods,
    ):
        """The owner moved, the transfer's category did not: changed, not unfit.

        A raw statement moves the transfer to the second user and leaves its
        own category the first user's.  The kept category is not the twin's
        owner's now, so it falls back to the transfer's, which is the very
        category kept: each twin differs from its kept row only by the moved
        owner and what its keys move with it, and the print counts it as
        changed and not as unfit, since no fallback changed a value (the cp1f
        review's R1, L-1).  The transfer itself is then under another user's
        category, so the next upgrade refuses its twins by name, each named
        with the transfer's category and that category's owner beside its
        own, and the transfer for its own category: the transfer is the row
        to repair, its twins with it.  The case below reaches the same
        refusals with the owner unmoved.
        """
        with app.app_context():
            savings = _accounts(seed_user)[0]
            xfer = create_transfer(
                seed_user, db.session, seed_user["account"], savings,
                seed_periods[0],
            )
            db.session.commit()
            rent = seed_user["categories"]["Rent"].id
            transfer_service.update_transfer(
                xfer.id, seed_user["user"].id, category_id=rent,
            )
            db.session.commit()
            _downgrade()
            kept = {income: _twin_of(xfer.id, income=income)
                    for income in (False, True)}
            _upgrade()
            second_savings = create_account_of_type(
                seed_second_user, db.session, "Savings", "Second Owner Savings",
            )
            db.session.commit()
            _set("transfers", xfer.id, {
                "user_id": seed_second_user["user"].id,
                "from_account_id": seed_second_user["account"].id,
                "to_account_id": second_savings.id,
                "pay_period_id": seed_second_user["bootstrap_period"].id,
                "scenario_id": seed_second_user["scenario"].id,
            })

            assert _downgrade_counts() == {
                "restored": 2, "rebuilt": 0, "changed": 2, "unfit": 0,
                "built": 0,
            }
            for income in (False, True):
                twin = _twin_of(xfer.id, income=income)
                assert twin["category_id"] == rent
                assert {
                    column for column in twin
                    if twin[column] != kept[income][column]
                } == {"user_id", "account_id", "pay_period_id", "scenario_id"}
            # The statement left the TRANSFER under another user's category,
            # and its twins say what it says, so the upgrade names all three.
            with pytest.raises(RuntimeError) as refused:
                _upgrade()
            db.session.rollback()
            for income in (False, True):
                assert (
                    f"({xfer.id}, {kept[income]['id']}, {rent}, "
                    f"{seed_user['user'].id}, {rent}, {seed_user['user'].id})"
                ) in str(refused.value)
            assert (
                f"({xfer.id}, {rent}, {seed_user['user'].id}, "
                f"{seed_second_user['user'].id})"
            ) in str(refused.value)
            _set("transfers", xfer.id, {"category_id": None})
            db.session.execute(text(
                "UPDATE budget.transactions SET category_id = NULL "
                "WHERE transfer_id = :t"
            ), {"t": xfer.id})
            db.session.commit()
            _upgrade()

    def test_the_transfers_own_category_given_to_another_user_since(
        self, app, db, seed_user, seed_second_user, seed_periods,
    ):
        """The create door's shape, its one category given away: back exactly, refused next.

        The transfer and both its twins under one category, as the create and
        update doors file them; after the upgrade a raw statement gives that
        category to the second user, the owner unmoved.  The kept category is
        not the twin's owner's now, so it falls back to the transfer's, which
        is the very category kept: both twins come back exactly as kept and
        the print counts none changed or unfit.  Each is then under another
        user's category, as its transfer is, so the next upgrade refuses both
        by name with the transfer's category and that category's owner beside
        each, and the transfer for its own category: the transfer is the row
        to repair, its twins with it (the cp1g review's L-1, its RVA; the
        cp2a review's L-1).
        Reachable only by a raw statement: no door writes a category's owner
        after its create.
        """
        with app.app_context():
            savings, _van = _accounts(seed_user)
            xfer = create_transfer(
                seed_user, db.session, seed_user["account"], savings,
                seed_periods[0],
            )
            db.session.commit()
            shared = _new_category(seed_user["user"].id, "Housing", "Shared")
            transfer_service.update_transfer(
                xfer.id, seed_user["user"].id, category_id=shared,
            )
            db.session.commit()
            _downgrade()
            kept = {income: _twin_of(xfer.id, income=income)
                    for income in (False, True)}
            assert {row["category_id"] for row in kept.values()} == {shared}
            _upgrade()
            _set("categories", shared, {
                "user_id": seed_second_user["user"].id,
            })

            assert _downgrade_counts() == {
                "restored": 2, "rebuilt": 0, "changed": 0, "unfit": 0,
                "built": 0,
            }
            for income in (False, True):
                assert _twin_of(xfer.id, income=income) == kept[income]
            with pytest.raises(RuntimeError) as refused:
                _upgrade()
            db.session.rollback()
            assert "2 twin(s) are filed under a category" in str(refused.value)
            for income in (False, True):
                assert (
                    f"({xfer.id}, {kept[income]['id']}, {shared}, "
                    f"{seed_second_user['user'].id}, {shared}, "
                    f"{seed_second_user['user'].id})"
                ) in str(refused.value)
            assert (
                f"({xfer.id}, {shared}, {seed_second_user['user'].id}, "
                f"{seed_user['user'].id})"
            ) in str(refused.value)
            _set("categories", shared, {"user_id": seed_user["user"].id})
            _upgrade()

    def test_a_kept_category_given_to_another_user_since(
        self, app, db, seed_user, seed_second_user, seed_periods,
    ):
        """A kept category whose OWN owner moved since: the transfer's comes back.

        The to-side twin is kept under its owner's "Incoming" (finding
        BAL-575's shape), its transfer under the owner's Rent.  After the
        upgrade a raw statement gives "Incoming" to the second user, the
        twin's owner unmoved.  Kept, the twin would come back under another
        user's category, a cross-user reference no row held at this revision;
        it is not its owner's now, so it comes back under its transfer's
        category, counted changed and unfit.  The transfer's category is its
        owner's here, so no twin is left under another user's category (the
        cp1f review's R2, L-2); where the transfer's category is the one given
        away, the case above, the fallback fits no better.  Reachable only by
        a raw statement: no door writes a category's owner after its create.
        """
        with app.app_context():
            savings, _van = _accounts(seed_user)
            xfer = create_transfer(
                seed_user, db.session, seed_user["account"], savings,
                seed_periods[0],
            )
            db.session.commit()
            rent = seed_user["categories"]["Rent"].id
            transfer_service.update_transfer(
                xfer.id, seed_user["user"].id, category_id=rent,
            )
            db.session.commit()
            _downgrade()
            incoming = _new_category(
                seed_user["user"].id, "Transfers", "Incoming",
            )
            _set("transactions", _twin_of(xfer.id, income=True)["id"], {
                "category_id": incoming,
            })
            kept = {income: _twin_of(xfer.id, income=income)
                    for income in (False, True)}
            _upgrade()
            _set("categories", incoming, {
                "user_id": seed_second_user["user"].id,
            })

            assert _downgrade_counts() == {
                "restored": 2, "rebuilt": 0, "changed": 1, "unfit": 1,
                "built": 0,
            }
            income = _twin_of(xfer.id, income=True)
            assert income["category_id"] == rent
            assert {k: v for k, v in income.items() if k != "category_id"} == {
                k: v for k, v in kept[True].items() if k != "category_id"
            }
            assert _twin_of(xfer.id, income=False) == kept[False]
            # Every twin's category is its owner's: with the transfer's own
            # category its owner's, the downgrade made no cross-user reference.
            assert db.session.execute(text(
                "SELECT count(*) FROM budget.transactions t "
                "JOIN budget.categories c ON c.id = t.category_id "
                "WHERE t.transfer_id = :t AND c.user_id <> t.user_id"
            ), {"t": xfer.id}).scalar_one() == 0
            _upgrade()
