"""
Shekel Budget App -- migration ``b7d2e4f19a63``: a transfer side's record holds no figure.

Plan step ``balance:X-bi-6-4d-2``'s 4d-4 half (rulings **R-BAL365**,
**R-BAL366**).  The revision empties every side record's ``amount`` and
``figure_source_id`` -- the transfer's ``settled_figure`` is the one home --
makes a side record stating either unstorable and every other movement
stating both, and installs the band's figure arm; it refuses while a stored
transfer breaks that arm or its records state a figure it does not store.
The migration cases downgrade a head database through the revision (the
records carry the figure again), stage a state, and drive the upgrade or the
downgrade, graded on what the database holds after.
"""

from decimal import Decimal

import pytest
from sqlalchemy import exc, text

from app import ref_cache
from app.enums import MovementFigureSourceEnum, StatusEnum
from app.extensions import db
from app.services import transfer_service
from app.side_band_infrastructure import BAND_ARM, apply_side_band_infrastructure
from tests._test_helpers import (
    add_entry,
    create_envelope_txn,
    create_savings_account,
    create_settled_transfer,
    load_migration_module,
    run_migration_callable,
    typed,
)

_MIGRATION = load_migration_module("b7d2e4f19a63_a_side_record_holds_no_figure.py")


def _records(transfer_id):
    """Return a transfer's side records as ``[(amount, figure_source_id)]``, expense side first."""
    return [
        tuple(row) for row in db.session.execute(
            text(
                "SELECT amount, figure_source_id FROM budget.transaction_entries "
                "WHERE expense_transfer_id = :t OR income_transfer_id = :t "
                "ORDER BY expense_transfer_id IS NULL"
            ),
            {"t": transfer_id},
        )
    ]


def _not_null_columns():
    """Return which of the entries' ``amount`` / ``figure_source_id`` are NOT NULL."""
    return {
        name for (name,) in db.session.execute(text(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_schema = 'budget' AND table_name = 'transaction_entries' "
            "AND column_name IN ('amount', 'figure_source_id') "
            "AND is_nullable = 'NO'"
        ))
    }


def _settled(seed_user, seed_periods, amount):
    """Settle a ``checking -> savings`` transfer at *amount*; return its id."""
    savings = create_savings_account(
        seed_user, db.session, f"Savings {amount}", Decimal("0.00"),
    )
    xfer = create_settled_transfer(
        seed_user, db.session, seed_user["account"], savings, seed_periods[0],
        amount=Decimal(amount),
    )
    db.session.commit()
    return xfer.id


def _revert(transfer_id, user_id):
    """Revert a transfer to Projected through the one transfer writer."""
    transfer_service.update_transfer(
        transfer_id, user_id, status_id=ref_cache.status_id(StatusEnum.PROJECTED),
    )
    db.session.commit()


def _resolved():
    """The ``resolved`` figure source's id: a settle priced from the plan."""
    return ref_cache.movement_figure_source_id(MovementFigureSourceEnum.RESOLVED)


class TestTheUpgradeEmptiesEachSideRecord:
    """The upgrade takes the figure off every side record; the transfer keeps it."""

    def test_settled_and_kept_records_are_emptied(self, app, seed_user, seed_periods):
        """A settled pair's and a reverted pair's records carry the figure down, and none up.

        A transfer settled at ``$120.00`` and one settled at ``$75.00`` then
        reverted (its records kept un-dated, ruling **R-BAL61**): the
        downgrade copies each transfer's figure back onto both its records,
        and the upgrade empties them while each transfer's stored figure
        stands.
        """
        with app.app_context():
            settled = _settled(seed_user, seed_periods, "120.00")
            kept = _settled(seed_user, seed_periods, "75.00")
            _revert(kept, seed_user["user"].id)
            assert _records(settled) == [(None, None), (None, None)]

            run_migration_callable(_MIGRATION.downgrade, db.session)
            assert _not_null_columns() == {"amount", "figure_source_id"}
            assert _records(settled) == [(Decimal("120.00"), _resolved())] * 2
            assert _records(kept) == [(Decimal("75.00"), _resolved())] * 2

            run_migration_callable(_MIGRATION.upgrade, db.session)
            assert not _not_null_columns()
            assert _records(settled) == [(None, None), (None, None)]
            assert _records(kept) == [(None, None), (None, None)]
            figures = dict(db.session.execute(text(
                "SELECT id, settled_figure FROM budget.transfers "
                "WHERE id IN (:a, :b)"
            ), {"a": settled, "b": kept}).all())
            assert figures == {settled: Decimal("120.00"), kept: Decimal("75.00")}

    def test_a_close_of_nothing_round_trips_with_no_record(
        self, app, seed_user, seed_periods,
    ):
        """A stored ``0.00`` close holds no record: nothing is copied down, nothing emptied up."""
        with app.app_context():
            xfer_id = _settled(seed_user, seed_periods, "60.00")
            transfer_service.update_transfer(
                xfer_id, seed_user["user"].id, figure=typed(Decimal("0")),
            )
            db.session.commit()
            run_migration_callable(_MIGRATION.downgrade, db.session)
            assert not _records(xfer_id)
            run_migration_callable(_MIGRATION.upgrade, db.session)
            assert not _records(xfer_id)


class TestTheUpgradeRefuses:
    """The upgrade writes nothing while a stored transfer breaks the rule it installs."""

    @staticmethod
    def _refused(match):
        """Drive the upgrade and assert it refuses with *match*, then roll back."""
        with pytest.raises(RuntimeError, match=match):
            run_migration_callable(_MIGRATION.upgrade, db.session)
        db.session.rollback()
        assert _not_null_columns() == {"amount", "figure_source_id"}

    def test_a_record_stating_a_figure_its_transfer_does_not_store(
        self, app, seed_user, seed_periods,
    ):
        """Emptying a record whose figure is not its transfer's would discard it: refused."""
        with app.app_context():
            xfer_id = _settled(seed_user, seed_periods, "60.00")
            run_migration_callable(_MIGRATION.downgrade, db.session)
            db.session.execute(
                text(
                    "UPDATE budget.transaction_entries SET amount = 61.00 "
                    "WHERE income_transfer_id = :t"
                ),
                {"t": xfer_id},
            )
            db.session.commit()
            self._refused(r"a record stating a figure its transfer does not store")

    @pytest.mark.parametrize(
        ("status", "figure", "words"),
        [
            (StatusEnum.DONE, None, "settled storing no figure"),
            (StatusEnum.PROJECTED, "0.00", r"not settled storing 0\.00"),
            (StatusEnum.PROJECTED, "500.00", "a figure its records do not match"),
        ],
        ids=["settled-no-figure", "unsettled-zero", "a-figure-no-record"],
    )
    def test_a_state_the_figure_arm_refuses(
        self, app, seed_user, seed_periods, status, figure, words,
    ):
        """Each figure-arm state on a transfer holding no record, planted below the arm: refused."""
        with app.app_context():
            xfer_id = _settled(seed_user, seed_periods, "60.00")
            transfer_service.update_transfer(
                xfer_id, seed_user["user"].id, figure=typed(Decimal("0")),
            )
            db.session.commit()
            run_migration_callable(_MIGRATION.downgrade, db.session)
            db.session.execute(
                text(
                    "UPDATE budget.transfers SET status_id = :status, "
                    "settled_figure = :figure, settled_figure_source_id = :source "
                    "WHERE id = :t"
                ),
                {
                    "status": ref_cache.status_id(status),
                    "figure": figure,
                    "source": None if figure is None else _resolved(),
                    "t": xfer_id,
                },
            )
            db.session.commit()
            self._refused(words)


class TestTheDowngradeRefuses:
    """The downgrade refuses to set NOT NULL back over a record it could not fill."""

    def test_a_record_whose_transfer_stores_no_figure(
        self, app, seed_user, seed_periods,
    ):
        """Kept records under a transfer storing no figure: planted with the figure arm off.

        The arm refuses the state at commit, so the case lifts it (the band
        alone, as before the revision) to plant it; the downgrade then names
        both records.
        """
        with app.app_context():
            xfer_id = _settled(seed_user, seed_periods, "60.00")
            _revert(xfer_id, seed_user["user"].id)
            apply_side_band_infrastructure(
                lambda sql: db.session.execute(text(sql)), arms=(BAND_ARM,),
            )
            db.session.execute(
                text(
                    "UPDATE budget.transfers SET settled_figure = NULL, "
                    "settled_figure_source_id = NULL WHERE id = :t"
                ),
                {"t": xfer_id},
            )
            db.session.commit()
            with pytest.raises(
                RuntimeError, match=r"2 movement\(s\) would stay without a figure",
            ):
                run_migration_callable(_MIGRATION.downgrade, db.session)
            db.session.rollback()
            assert not _not_null_columns()


class TestEachMovementStatesItsFigureOrNone:
    """R-BAL366's two CHECKs: a side record states no figure, every other movement one."""

    @pytest.mark.parametrize("income", [False, True], ids=["from-side", "to-side"])
    @pytest.mark.parametrize(
        ("column", "constraint"),
        [
            ("amount = 60.00", "ck_transaction_entries_side_record_states_no_figure"),
            ("figure_source_id = :source",
             "ck_transaction_entries_side_record_names_no_figure_source"),
        ],
        ids=["amount", "source"],
    )
    def test_a_side_record_stating_a_figure_is_refused(
        self, app, seed_user, seed_periods, column, constraint, income,
    ):
        """A settled transfer's record handed the figure back: the copy is unstorable.

        Each side's record is handed it ALONE, because each CHECK reads both
        link columns and each side must refuse on its own.  The case handed
        the figure to the from-side's record alone until plan step
        ``balance:X-bi-6-4d-2``'s 4d-4b review, where the amount CHECK mutated
        to admit an amount on any to-side record left this module green
        (measured there).
        """
        link = "income_transfer_id" if income else "expense_transfer_id"
        with app.app_context():
            xfer_id = _settled(seed_user, seed_periods, "60.00")
            with pytest.raises(exc.IntegrityError, match=constraint):
                db.session.execute(
                    text(
                        f"UPDATE budget.transaction_entries SET {column} "
                        f"WHERE {link} = :t"
                    ),
                    {"t": xfer_id, "source": _resolved()},
                )
            db.session.rollback()

    @pytest.mark.parametrize(
        ("column", "constraint"),
        [
            ("amount", "ck_transaction_entries_side_record_states_no_figure"),
            ("figure_source_id",
             "ck_transaction_entries_side_record_names_no_figure_source"),
        ],
        ids=["amount", "source"],
    )
    def test_a_purchase_stating_no_figure_is_refused(
        self, app, seed_user, seed_periods, column, constraint,
    ):
        """CONTROL half: a movement under a row keeps both, so NOT NULL's work still holds."""
        with app.app_context():
            txn = create_envelope_txn(
                seed_user, db.session, seed_periods[0], "Groceries", Decimal("100.00"),
            )
            add_entry(
                db.session, seed_user, txn, Decimal("12.34"),
                seed_periods[0].start_date,
            )
            db.session.commit()
            with pytest.raises(exc.IntegrityError, match=constraint):
                db.session.execute(
                    text(
                        f"UPDATE budget.transaction_entries SET {column} = NULL "
                        "WHERE transaction_id = :t"
                    ),
                    {"t": txn.id},
                )
            db.session.rollback()
