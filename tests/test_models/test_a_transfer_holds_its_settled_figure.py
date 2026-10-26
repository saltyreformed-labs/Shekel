"""
Shekel Budget App -- migration ``993eae6ade80``: a transfer holds its settled figure.

Plan step ``balance:X-bi-6-4d-2``'s 4d-4 half (rulings **R-BAL365**,
**R-BAL366**).  The revision gives a settled transfer's figure and who wrote it
ONE home on ``budget.transfers``, copied off its side records, and refuses
while the records do not state one figure.  Each case downgrades a head
database through head's revision ``b7d2e4f19a63`` (:data:`_SIDE_RECORD_FIGURE`,
which copies each transfer's figure back onto its records) and then through
this one (the columns go; the records' copy is the only one again), stages a
state, and drives the upgrade or the downgrade, graded on what the database
holds after.  This revision's downgrade never runs from head: there a side's
record states no figure, and the band's figure arm reads the columns it drops.
"""

from decimal import Decimal

import pytest
from sqlalchemy import text

from app import ref_cache
from app.enums import MovementFigureSourceEnum, StatusEnum
from app.extensions import db
from app.models.amount_ownership import AmountOwnership
from app.services import transfer_service
from tests._test_helpers import (
    create_savings_account,
    create_settled_transfer,
    load_migration_module,
    run_migration_callable,
    typed,
)

_MIGRATION = load_migration_module(
    "993eae6ade80_a_transfer_holds_its_settled_figure.py",
)

#: The revision above, head: a side's record holds no figure.  Its downgrade
#: runs before this revision's; where a case returns to head, its upgrade runs
#: after this revision's.
_SIDE_RECORD_FIGURE = load_migration_module(
    "b7d2e4f19a63_a_side_record_holds_no_figure.py",
)


def _figure_columns():
    """Return the names of ``budget.transfers``' settled figure columns that exist."""
    return {
        name for (name,) in db.session.execute(text(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_schema = 'budget' AND table_name = 'transfers' "
            "AND column_name LIKE 'settled_figure%'"
        ))
    }


def _stored(transfer_id):
    """Return a transfer's stored ``(settled_figure, settled_figure_source_id)``."""
    return tuple(db.session.execute(
        text(
            "SELECT settled_figure, settled_figure_source_id "
            "FROM budget.transfers WHERE id = :t"
        ),
        {"t": transfer_id},
    ).one())


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


def _set_status(transfer_id, user_id, member):
    """Move a transfer to *member* through the one transfer writer."""
    transfer_service.update_transfer(
        transfer_id, user_id, status_id=ref_cache.status_id(member),
    )
    db.session.commit()


class TestTheUpgradeCopiesEachFigure:
    """The upgrade stores each transfer's figure off its records, and only theirs."""

    def test_settled_kept_and_never_settled(self, app, seed_user, seed_periods):
        """A settled pair's figure, a reverted pair's kept one, and none for a plan.

        A settled transfer's two DATED records state ``$120.00``, resolved; a
        transfer settled at ``$75.00`` then reverted keeps both records
        UN-dated (ruling **R-BAL61**) and so its figure; a transfer never
        settled holds no record and stores none.  The downgrade drops both
        columns first, so what the upgrade stores is its own copy.
        """
        with app.app_context():
            user_id = seed_user["user"].id
            settled = _settled(seed_user, seed_periods, "120.00")
            kept = _settled(seed_user, seed_periods, "75.00")
            _set_status(kept, user_id, StatusEnum.PROJECTED)
            planned = transfer_service.create_transfer(transfer_service.TransferSpec(
                user_id=user_id,
                from_account_id=seed_user["account"].id,
                to_account_id=create_savings_account(
                    seed_user, db.session, "Plan savings", Decimal("0.00"),
                ).id,
                pay_period_id=seed_periods[0].id,
                scenario_id=seed_user["scenario"].id,
                amount_ownership=AmountOwnership.own(Decimal("40.00")),
                status_id=ref_cache.status_id(StatusEnum.PROJECTED),
                category_id=None,
                name="Never settled",
            )).id
            db.session.commit()

            run_migration_callable(_SIDE_RECORD_FIGURE.downgrade, db.session)
            run_migration_callable(_MIGRATION.downgrade, db.session)
            assert not _figure_columns()
            run_migration_callable(_MIGRATION.upgrade, db.session)

            resolved = ref_cache.movement_figure_source_id(
                MovementFigureSourceEnum.RESOLVED,
            )
            assert _figure_columns() == {
                "settled_figure", "settled_figure_source_id",
            }
            assert _stored(settled) == (Decimal("120.00"), resolved)
            assert _stored(kept) == (Decimal("75.00"), resolved)
            assert _stored(planned) == (None, None)
            run_migration_callable(_SIDE_RECORD_FIGURE.upgrade, db.session)


class TestTheUpgradeRefuses:
    """The upgrade writes nothing while a transfer's records do not state one figure."""

    @staticmethod
    def _refused(match):
        """Drive the upgrade and assert it refuses with *match*, then roll back."""
        with pytest.raises(RuntimeError, match=match):
            run_migration_callable(_MIGRATION.upgrade, db.session)
        db.session.rollback()
        assert not _figure_columns()

    def test_a_close_of_nothing_names_its_repair(self, app, seed_user, seed_periods):
        """A ``$0.00`` close from before the revision stores no source: refused.

        Built through the app (a typed ``$0.00`` correction takes both
        sides' records off), then downgraded: a settled transfer holding no
        record, the one class a door wrote, whose source cannot be read back.
        """
        with app.app_context():
            xfer_id = _settled(seed_user, seed_periods, "60.00")
            transfer_service.update_transfer(
                xfer_id, seed_user["user"].id, figure=typed(Decimal("0")),
            )
            db.session.commit()
            run_migration_callable(_SIDE_RECORD_FIGURE.downgrade, db.session)
            run_migration_callable(_MIGRATION.downgrade, db.session)
            self._refused(
                r"(?s)settled holding no record.*revert each to Projected in the "
                r"app BEFORE upgrading",
            )

    def test_two_records_stating_different_figures(
        self, app, seed_user, seed_periods,
    ):
        """Two records whose figures differ state no ONE figure: refused."""
        with app.app_context():
            xfer_id = _settled(seed_user, seed_periods, "60.00")
            run_migration_callable(_SIDE_RECORD_FIGURE.downgrade, db.session)
            run_migration_callable(_MIGRATION.downgrade, db.session)
            db.session.execute(
                text(
                    "UPDATE budget.transaction_entries SET amount = 61.00 "
                    "WHERE income_transfer_id = :t"
                ),
                {"t": xfer_id},
            )
            db.session.commit()
            self._refused(r"two records stating different figures")

    def test_a_record_on_one_side_only(self, app, seed_user, seed_periods):
        """A reverted transfer keeping one side's record states half a payment: refused."""
        with app.app_context():
            xfer_id = _settled(seed_user, seed_periods, "60.00")
            _set_status(xfer_id, seed_user["user"].id, StatusEnum.PROJECTED)
            run_migration_callable(_SIDE_RECORD_FIGURE.downgrade, db.session)
            run_migration_callable(_MIGRATION.downgrade, db.session)
            db.session.execute(
                text(
                    "DELETE FROM budget.transaction_entries "
                    "WHERE income_transfer_id = :t"
                ),
                {"t": xfer_id},
            )
            db.session.commit()
            self._refused(r"a record on one side only")


class TestTheDowngrade:
    """The downgrade drops the columns, and refuses to drop a figure no record states."""

    def test_a_close_of_nothing_downgrades_to_its_old_shape(
        self, app, seed_user, seed_periods,
    ):
        """A stored ``0.00`` close holds no record: it downgrades to the close of nothing."""
        with app.app_context():
            xfer_id = _settled(seed_user, seed_periods, "60.00")
            transfer_service.update_transfer(
                xfer_id, seed_user["user"].id, figure=typed(Decimal("0")),
            )
            db.session.commit()
            assert _stored(xfer_id)[0] == Decimal("0.00")
            run_migration_callable(_SIDE_RECORD_FIGURE.downgrade, db.session)
            run_migration_callable(_MIGRATION.downgrade, db.session)
            assert not _figure_columns()

    def test_a_figure_its_records_do_not_state_is_refused(
        self, app, seed_user, seed_periods,
    ):
        """Dropping a figure the records contradict would lose what the app read: refused."""
        with app.app_context():
            xfer_id = _settled(seed_user, seed_periods, "60.00")
            # Tampered BELOW head, once the records state the figure again:
            # at head the downgrade above would copy the tamper onto them.
            run_migration_callable(_SIDE_RECORD_FIGURE.downgrade, db.session)
            db.session.execute(
                text(
                    "UPDATE budget.transfers SET settled_figure = 999.00 "
                    "WHERE id = :t"
                ),
                {"t": xfer_id},
            )
            db.session.commit()
            with pytest.raises(RuntimeError, match=rf"\[{xfer_id}\]"):
                run_migration_callable(_MIGRATION.downgrade, db.session)
            db.session.rollback()
            assert _figure_columns() == {
                "settled_figure", "settled_figure_source_id",
            }
