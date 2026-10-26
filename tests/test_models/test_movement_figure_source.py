"""A movement's figure names its source: the column and the backfill's arms.

Plan step **balance:X-bi-3a**, ruling **R-BAL39**.  ``transaction_entries.
figure_source_id`` says WHO WROTE a movement's figure -- the settle pricing it
from the plan (``resolved``), a person (``typed``) or the bank's own line
(``observed``) -- NOT NULL with no default, because both writers of a movement
state it and a stored guess is the shape ruling R-IY deletes.

**The backfill's arms are DRIVEN, not described** (the pattern
``test_settle_day_basis`` set for the day's catalogue, after a review showed a
test over a docstring grades a docstring).  The migration classifies by the
purchase's OWN day basis and never by its parent: a purchase the bank observed
carries the bank's figure, every other one a person's.  Each case below stages
rows in the pre-backfill state -- dated or not, with NO source -- runs
``classify_figure_sources`` against the test connection, and reads the
classification back by NAME.  What refuses a movement with no source is
dropped for the duration of each case -- the NOT NULL until revision
``b7d2e4f19a63``, and since it the CHECK that a movement off a transfer's
side names its source (a side's record names none) -- because the state a
backfill classifies is exactly the intermediate the migration itself creates
before it adds the NOT NULL; DDL is transactional in PostgreSQL, so the
rollback each case ends with restores it.
"""

from datetime import timedelta
from decimal import Decimal

import sqlalchemy

from app.enums import SettledDayBasisEnum
from app.extensions import db
from app.models.transaction_entry import TransactionEntry
from tests._test_helpers import (
    generate_row_of,
    load_migration_module,
    make_expense_template,
    settle_day_columns,
)

_MIGRATION = load_migration_module(
    "b5c7e9a1d2f4_a_movements_figure_names_its_source.py"
)


def _envelope(seed_user, period):
    """One engine-generated envelope row, flushed."""
    template = make_expense_template(
        db.session, seed_user, amount="100.00",
        name="Groceries", category_key="Rent", is_envelope=True,
    )
    return generate_row_of(template, period)


class TestTheBackfillArmsAreExactOverTheirOwnPredicates:
    """Two arms, one predicate each, read back by name."""

    @staticmethod
    def _unrequire(db_session):
        """Drop the CHECK requiring a purchase's source, so the pre-backfill state is expressible.

        The column holds no NOT NULL since ``b7d2e4f19a63`` (a side's record
        names no source); this CHECK is what refuses a purchase naming none.
        """
        db_session.session.execute(sqlalchemy.text(
            "ALTER TABLE budget.transaction_entries DROP CONSTRAINT "
            "ck_transaction_entries_side_record_names_no_figure_source"
        ))

    @staticmethod
    def _source_names(db_session):
        """Return ``{entry id: source name}`` for every purchase."""
        rows = db_session.session.execute(sqlalchemy.text(
            "SELECT e.id, s.name FROM budget.transaction_entries e "
            "LEFT JOIN ref.movement_figure_sources s "
            "  ON s.id = e.figure_source_id"
        )).all()
        return {row[0]: row[1] for row in rows}

    @staticmethod
    def _bare_purchase(envelope, seed_user, settled_on, basis):
        """Stage one purchase with the given day pair and NO source."""
        entry = TransactionEntry(
            transaction_id=envelope.id, account_id=envelope.account_id, owner_id=envelope.user_id,
            user_id=seed_user["user"].id, amount=Decimal("12.79"),
            description="Kroger", purchased_on=envelope.pay_period.start_date,
            **settle_day_columns(settled_on, basis),
            figure_source_id=None,
        )
        db.session.add(entry)
        db.session.flush()
        return entry

    def test_a_purchase_with_a_bank_observed_day_is_OBSERVED(
        self, app, db, seed_user, seed_periods,
    ):
        with app.app_context():
            self._unrequire(db)
            envelope = _envelope(seed_user, seed_periods[0])
            entry = self._bare_purchase(
                envelope, seed_user, envelope.pay_period.start_date,
                SettledDayBasisEnum.OBSERVED,
            )
            _MIGRATION.classify_figure_sources(db.session.connection())
            assert self._source_names(db)[entry.id] == "observed"
            db.session.rollback()

    def test_an_asserted_day_is_TYPED(self, app, db, seed_user, seed_periods):
        """The reconcile panel's bound is not the bank stating a figure."""
        with app.app_context():
            self._unrequire(db)
            envelope = _envelope(seed_user, seed_periods[0])
            entry = self._bare_purchase(
                envelope, seed_user,
                envelope.pay_period.start_date + timedelta(days=2),
                SettledDayBasisEnum.ASSERTED,
            )
            _MIGRATION.classify_figure_sources(db.session.connection())
            assert self._source_names(db)[entry.id] == "typed"
            db.session.rollback()

    def test_an_entered_day_is_TYPED(self, app, db, seed_user, seed_periods):
        with app.app_context():
            self._unrequire(db)
            envelope = _envelope(seed_user, seed_periods[0])
            entry = self._bare_purchase(
                envelope, seed_user, envelope.pay_period.start_date,
                SettledDayBasisEnum.ENTERED,
            )
            _MIGRATION.classify_figure_sources(db.session.connection())
            assert self._source_names(db)[entry.id] == "typed"
            db.session.rollback()

    def test_an_undated_purchase_is_TYPED(self, app, db, seed_user, seed_periods):
        with app.app_context():
            self._unrequire(db)
            envelope = _envelope(seed_user, seed_periods[0])
            entry = self._bare_purchase(envelope, seed_user, None, None)
            _MIGRATION.classify_figure_sources(db.session.connection())
            assert self._source_names(db)[entry.id] == "typed"
            db.session.rollback()

    def test_the_parents_basis_is_never_consulted(
        self, app, db, seed_user, seed_periods,
    ):
        """The firing control for R-BAL39's 'never from the parent'.

        A purchase under an envelope whose own record says ``purchases``
        classifies by ITS day, not by the envelope's basis: an observed day
        is observed whatever the parent records.
        """
        with app.app_context():
            self._unrequire(db)
            envelope = _envelope(seed_user, seed_periods[0])
            entry = self._bare_purchase(
                envelope, seed_user, envelope.pay_period.start_date,
                SettledDayBasisEnum.OBSERVED,
            )
            # The parent records its purchases and nothing of its own, as a
            # real envelope with posted purchases does: no covering movement.
            db.session.flush()
            assert envelope.covering_movements == []
            _MIGRATION.classify_figure_sources(db.session.connection())
            assert self._source_names(db)[entry.id] == "observed"
            db.session.rollback()

    def test_no_purchase_is_left_unclassified(
        self, app, db, seed_user, seed_periods,
    ):
        """The NOT NULL proof, on every shape at once."""
        with app.app_context():
            self._unrequire(db)
            envelope = _envelope(seed_user, seed_periods[0])
            start = envelope.pay_period.start_date
            for settled_on, basis in (
                (start, SettledDayBasisEnum.OBSERVED),
                (start, SettledDayBasisEnum.ASSERTED),
                (start, SettledDayBasisEnum.ENTERED),
                (None, None),
            ):
                self._bare_purchase(envelope, seed_user, settled_on, basis)
            _MIGRATION.classify_figure_sources(db.session.connection())
            assert None not in self._source_names(db).values()
            # And the refusal the NOT NULL step rests on has nothing to say.
            _MIGRATION._refuse_unclassified(db.session.connection())  # pylint: disable=protected-access -- driving the migration's own backstop
            db.session.rollback()
