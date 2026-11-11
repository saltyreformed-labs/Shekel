"""The constraint trigger on the writers no ORM guard reaches, and its WHEN clause."""

from datetime import timedelta
from decimal import Decimal

import pytest
import sqlalchemy as sa

from app.enums import TxnTypeEnum
from sqlalchemy.exc import OperationalError

from app.models.account_opening import AccountOpening
from app.models.transaction import Transaction
from app.models.transaction_entry import TransactionEntry
from app import ref_cache
from app.services.cash_ledger import account_opening_fact
from tests._test_helpers import (
    create_settled_cash_transaction,
    one_off_row_of,
    refused_by_database_rule,
    restate_account_opening,
)

from ._helpers import _ONE_DAY, _books_open_on


class TestTheDatabaseSeesWhatTheOrmCannot:
    """The constraint trigger, graded on the writers no ORM guard reaches.

    Each case here goes through raw SQL or a bulk ``UPDATE`` on purpose: an ORM
    call would prove only that the service refusal fires, which the class above
    already grades.
    """

    def test_a_bulk_update_onto_the_opening_day_aborts_at_COMMIT(
        self, app, db, seed_user, seed_periods,
    ):  # pylint: disable=unused-argument
        """``query.update()`` bypasses every Python guard and is still refused.

        This is the shape ``reconcile_service.record_settled_days`` writes in,
        and the shape the ``settled_on`` ORM validator documents itself blind
        to.
        """
        with app.app_context():
            account = seed_user["account"]
            row = create_settled_cash_transaction(
                seed_user, seed_periods[0], Decimal("25.00"),
                settled_on=_books_open_on(account) + _ONE_DAY,
                name="bulk-probe",
            )
            db.session.commit()
            db.session.query(Transaction).filter_by(id=row.id).update(
                {Transaction.settled_on: _books_open_on(account)},
                synchronize_session=False,
            )
            with refused_by_database_rule("books open"):
                db.session.commit()
            db.session.rollback()

    def test_an_opening_cannot_be_restated_FORWARD_past_a_movement(
        self, app, db, seed_user, seed_periods,
    ):  # pylint: disable=unused-argument
        """The OTHER direction, which no write-door refusal can see.

        Moving the books forward is how an account acquires a pre-opening
        movement without anybody writing one, so the invariant needs both arms
        or it holds only against the writer that happens to be guarded.
        """
        with app.app_context():
            account = seed_user["account"]
            moved_on = _books_open_on(account) + _ONE_DAY
            create_settled_cash_transaction(
                seed_user, seed_periods[0], Decimal("25.00"),
                settled_on=moved_on, name="in-the-way",
            )
            db.session.commit()
            db.session.add(AccountOpening(
                account_id=account.id,
                opened_on=moved_on,
                opening_equity=Decimal("1000.00"),
                source_id=account_opening_fact(account.id).source_id,
            ))
            with refused_by_database_rule("cannot open its books"):
                db.session.commit()
            db.session.rollback()

    def test_an_opening_restated_BACKWARD_is_accepted(
        self, app, db, seed_user, seed_periods,
    ):  # pylint: disable=unused-argument
        """Which is the repair the refusal's own message tells an owner to make."""
        with app.app_context():
            account = seed_user["account"]
            earlier = _books_open_on(account) - timedelta(days=30)
            restate_account_opening(db.session, account, earlier)
            db.session.commit()
            assert _books_open_on(account) == earlier

    def test_the_check_is_DEFERRED_so_one_transaction_may_do_both(
        self, app, db, seed_user, seed_periods,
    ):  # pylint: disable=unused-argument
        """Re-date the movements and move the books forward in ONE commit.

        **This is why the trigger is ``DEFERRABLE INITIALLY DEFERRED`` and not a
        statement-level check.**  The account-10 repair (**N-379**) does exactly
        this: the opening moves forward to the bank's own day while the rows in
        its way move out of it, and an immediate check would refuse the pair by
        statement order alone.

        **THE ORDER OF THE TWO STATEMENTS IS THE WHOLE MEASUREMENT.**  Writing
        the movement first and the opening second is a case a NON-deferrable
        trigger also accepts -- each statement is individually legal at the
        moment it runs -- so it grades nothing.  This writes the OPENING first,
        which is illegal at that instant and legal at COMMIT, and is the only
        order the deferral decides.  Measured by stripping
        ``DEFERRABLE INITIALLY DEFERRED``: the old order still committed, this
        one is refused (adversarial review, 2026-08-28).
        """
        with app.app_context():
            account = seed_user["account"]
            opened_on = _books_open_on(account)
            row = create_settled_cash_transaction(
                seed_user, seed_periods[0], Decimal("25.00"),
                settled_on=opened_on + _ONE_DAY, name="moves-with-the-books",
            )
            db.session.commit()

            later = opened_on + timedelta(days=10)
            # The books move FORWARD past the movement that is still standing.
            db.session.add(AccountOpening(
                account_id=account.id,
                opened_on=later,
                opening_equity=Decimal("1000.00"),
                source_id=account_opening_fact(account.id).source_id,
            ))
            db.session.flush()
            # ... and only now does the movement leave the opening it is inside
            # -- the row AND its covering movement, which the seam dated with
            # it (plan step X-bi-3a) and which the same trigger guards on its
            # own table.
            db.session.query(Transaction).filter_by(id=row.id).update(
                {Transaction.settled_on: later + _ONE_DAY},
                synchronize_session=False,
            )
            db.session.query(TransactionEntry).filter_by(
                transaction_id=row.id,
            ).update(
                {
                    TransactionEntry.settled_on: later + _ONE_DAY,
                    TransactionEntry.purchased_on: later + _ONE_DAY,
                },
                synchronize_session=False,
            )
            db.session.commit()
            assert _books_open_on(account) == later

    def test_a_SUPERSEDED_restatement_is_history_and_not_a_violation(
        self, app, db, seed_user, seed_periods,
    ):  # pylint: disable=unused-argument
        """Only the GOVERNING row constrains anything.

        The table is append-only precisely so what the opening USED to be
        survives; a superseded row that the live data now contradicts is a
        record, not a defect.  Both rows land in ONE transaction, so the check
        is made against the one that wins.
        """
        with app.app_context():
            account = seed_user["account"]
            opened_on = _books_open_on(account)
            create_settled_cash_transaction(
                seed_user, seed_periods[0], Decimal("25.00"),
                settled_on=opened_on + _ONE_DAY, name="after-the-books",
            )
            db.session.commit()

            source_id = account_opening_fact(account.id).source_id
            db.session.execute(sa.text("""
                INSERT INTO budget.account_openings
                       (account_id, opened_on, opening_equity, source_id,
                        created_at)
                VALUES (:a, :bad, 1000.00, :s, now()),
                       (:a, :good, 1000.00, :s, now() + interval '1 second')
            """), {
                "a": account.id,
                "bad": opened_on + timedelta(days=90),
                "good": opened_on,
                "s": source_id,
            })
            db.session.commit()
            assert _books_open_on(account) == opened_on


class TestAnUnsettledRowDoesNotRESERVETheTable:
    """The WHEN clause, graded by the DDL a pending event would forbid.

    **PostgreSQL refuses ``ALTER TABLE`` on a table carrying pending trigger
    events**, and a deferred constraint trigger queues its event at STATEMENT
    time whatever the function would later decide.  So the guard "a row with no
    settle day states nothing" has to live in the trigger's WHEN clause, not in
    an early ``RETURN`` inside its body -- there it runs at COMMIT, one phase
    after the queueing it needed to prevent.

    **The only thing that caught this was CI**, twice: ``recurrence:R17``'s
    index re-key on the merge, then two audit-trigger benchmarks.  Both live in
    ``tests/test_performance``, which ``pytest.ini`` excludes from the ordinary
    run, so a green local suite said nothing about either.  These two cases put
    the interaction where every run sees it.
    """

    #: The DDL the audit-trigger benchmarks perform between samples, and the
    #: statement PostgreSQL refuses while an event is pending.
    _DDL = "ALTER TABLE budget.transactions ENABLE TRIGGER audit_transactions"

    def test_a_PROJECTED_write_leaves_DDL_on_the_table_legal(
        self, app, db, seed_user, seed_periods,
    ):  # pylint: disable=unused-argument
        """An unsettled row queues no event, so the table stays alterable."""
        with app.app_context():
            account = seed_user["account"]
            one_off_row_of(
                seed_periods[0],
                name="projected-probe",
                amount=Decimal("25.00"),
                user_id=seed_periods[0].user_id,
                account_id=account.id,
                scenario_id=seed_user["scenario"].id,
                transaction_type_id=ref_cache.txn_type_id(
                    TxnTypeEnum.EXPENSE,
                ),
            )
            db.session.flush()
            # No exception: the WHEN clause kept the row out of the queue.
            db.session.execute(sa.text(self._DDL))
            db.session.rollback()

    def test_a_SETTLED_write_still_reserves_it(
        self, app, db, seed_user, seed_periods,
    ):  # pylint: disable=unused-argument
        """The control: the case above passes because of WHEN, not because the
        DDL is always legal.

        A row that DOES carry a settle day is a row the rule must check, so its
        event is queued and the same statement is refused.  Without this arm the
        case above would pass on a database where the trigger had been dropped
        altogether.
        """
        with app.app_context():
            account = seed_user["account"]
            create_settled_cash_transaction(
                seed_user, seed_periods[0], Decimal("25.00"),
                settled_on=_books_open_on(account) + _ONE_DAY,
                name="settled-probe",
            )
            db.session.flush()
            with pytest.raises(OperationalError, match="pending trigger events"):
                db.session.execute(sa.text(self._DDL))
            db.session.rollback()
