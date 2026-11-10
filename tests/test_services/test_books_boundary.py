"""A movement cannot predate the books it is in (plan step X-f3c-2b, **R-HG**).

**The rule, in one sentence.** An account's opening equity is the balance at the
CLOSE of ``budget.account_openings.opened_on`` -- the same rule
``account_anchor_history.observed_on`` states for an assertion (ruling R-DH (a))
-- so no cash movement may be dated ON OR BEFORE that day.

**Three tiers grade it here, and each covers what the one below cannot.**

* The PREDICATE (:func:`app.services.cash_ledger.reject_movement_before_books_open`),
  where the boundary itself is pinned from both sides so a ``<`` written where
  the code has ``<=`` fails.
* The ORM chokepoint (:func:`app.services.settle_day.record_settle_day`) and the
  doors above it, which is what makes the refusal a sentence a date box can
  render rather than a database exception at COMMIT.
* The DATABASE, which is the only tier that sees the writers the other two
  cannot: a bulk ``query.update()`` with no ORM instance, an opening restated
  FORWARD past a movement that already exists, and anything a future writer
  does that nobody enumerated.

**Why the third tier is graded here and not taken on trust.** The suite's own
history is the argument: ``reconcile_service.record_settled_days`` writes
``settled_on`` by bulk ``UPDATE`` precisely because it has no instance to hand
``record_settle_day``, and the ``@validates`` guard one column over documents
its own blindness to exactly that shape. A guard whose scope nobody measured is
a guard whose scope is wrong.
"""

from datetime import date, timedelta
from decimal import Decimal

import pytest
import sqlalchemy as sa

from app.enums import (
    AccountOpeningSourceEnum, SettledDayBasisEnum, StatusEnum, TxnTypeEnum,
)
from sqlalchemy.exc import OperationalError

from app.exceptions import ValidationError
from app.extensions import db as _db
from app.models.account_opening import AccountOpening
from app.models.transaction import Transaction
from app.models.transaction_entry import TransactionEntry
from app import ref_cache
from app.services import status_seam
from app.services.cash_ledger import (
    AnchorPoint,
    account_opening_fact,
    cash_anchor_facts,
    earliest_matched_line_day,
    earliest_recorded_movement_day,
    reject_books_open_on_or_after_matched_lines,
    reject_movement_before_books_open,
)
from app.services.pay_calendar import calendar_for
from app.services.reconcile_service import Statement, record_settled_days
from app.services.settle_day import SettleDay, record_settle_day
from tests._test_helpers import (
    account_never_asserted,
    append_only_guard_lifted,
    create_account_of_type,
    create_settled_cash_transaction,
    figure_source_columns,
    match_two_lines,
    one_off_row_of,
    refused_by_database_rule,
    restate_account_opening,
    settle_day_columns,
)

_ONE_DAY = timedelta(days=1)


def _books_open_on(account):
    """Return the civil day *account*'s books open, through the app's loader."""
    return account_opening_fact(account.id).opened_on


def _entered(day):
    """Return the ``entered``-basis :class:`SettleDay` for *day*."""
    return SettleDay(day=day, basis=SettledDayBasisEnum.ENTERED)


#: Removing an opening row is not an ORM act -- ``AccountOpening._block_delete``
#: refuses one -- so the cases that need it go around the ORM, which is the same
#: reason the arm being graded lives in the database rather than in a service.
_REMOVE_ONE_OPENING = "DELETE FROM budget.account_openings WHERE id = :i"

#: Counts an account's opening rows, for the cascade case below: the point
#: there is that SEVERAL are disposed of together, so the number is read
#: rather than assumed.
_COUNT_OPENINGS = (
    "SELECT count(*) FROM budget.account_openings WHERE account_id = :a"
)

#: The SQL half of "which restatement governs", asked directly.  Going through
#: the function rather than re-writing its ``ORDER BY`` here is the point: a
#: hand-written query in the test would agree with the Python loader while the
#: function the CONSTRAINT calls disagreed with both.
_OPENED_ON_IN_SQL = "SELECT budget.account_books_opened_on(:a)"

#: Two restatements in one statement, with ``created_at`` supplied so the
#: RECORDING order is the test's to choose rather than the wall clock's.
_RECORD_TWO_RESTATEMENTS = """
    INSERT INTO budget.account_openings
           (account_id, opened_on, opening_equity, source_id, created_at)
    VALUES (:a, :first, 1000.00, :s, now() + :first_offset),
           (:a, :second, 1000.00, :s, now() + :second_offset)
"""


def _record_two_restatements(account, first, second, *, same_instant):
    """Append two opening restatements to *account*, oldest recorded first.

    Args:
        account: The :class:`~app.models.account.Account` to restate.
        first: The civil day the first-RECORDED restatement names.
        second: The civil day the second-RECORDED one names.
        same_instant: When true both rows share one ``created_at``, so only
            ``id`` can break the tie; when false the second is recorded a
            second later.
    """
    _db.session.execute(sa.text(_RECORD_TWO_RESTATEMENTS), {
        "a": account.id,
        "s": account_opening_fact(account.id).source_id,
        "first": first,
        "second": second,
        "first_offset": timedelta(seconds=1),
        "second_offset": timedelta(seconds=1 if same_instant else 2),
    })
    _db.session.commit()


class TestTheBoundaryItself:
    """The predicate, pinned from BOTH sides of the day it turns on.

    Asserted at the boundary rather than at an arbitrary depth so the comparison
    direction is what the tests measure: a ``<`` written where the code has
    ``<=`` moves exactly the opening day across the line and fails
    :meth:`test_the_opening_day_itself_is_refused` while every other case here
    still passes.
    """

    def test_a_day_before_the_books_open_is_refused(
        self, app, db, seed_user, seed_periods,
    ):  # pylint: disable=unused-argument
        """The day before the opening raises, naming both dates."""
        with app.app_context():
            account = create_account_of_type(
                seed_user, db.session, "Checking", "Bounded",
                anchor_balance=Decimal("1000.00"),
            )
            opened_on = _books_open_on(account)
            with pytest.raises(ValidationError) as exc:
                reject_movement_before_books_open(
                    account.id, opened_on - _ONE_DAY,
                )
            message = str(exc.value)
            assert (opened_on - _ONE_DAY).isoformat() in message
            assert opened_on.isoformat() in message

    def test_the_opening_day_itself_is_refused(
        self, app, db, seed_user, seed_periods,
    ):  # pylint: disable=unused-argument
        """**The `<=` half of the rule, and the whole of R-HG.**

        The opening equity is the CLOSING balance for its own day, so money that
        moved that day is inside it.  Paired with the case below so neither can
        pass while the comparison is off by one in either direction.
        """
        with app.app_context():
            account = create_account_of_type(
                seed_user, db.session, "Checking", "Bounded",
                anchor_balance=Decimal("1000.00"),
            )
            opened_on = _books_open_on(account)
            with pytest.raises(ValidationError, match=r"books open on"):
                reject_movement_before_books_open(account.id, opened_on)

    def test_the_day_after_the_books_open_is_accepted(
        self, app, db, seed_user, seed_periods,
    ):  # pylint: disable=unused-argument
        """One day past the opening is the first recordable day."""
        with app.app_context():
            account = create_account_of_type(
                seed_user, db.session, "Checking", "Bounded",
                anchor_balance=Decimal("1000.00"),
            )
            reject_movement_before_books_open(
                account.id, _books_open_on(account) + _ONE_DAY,
            )

    def test_an_account_with_no_opening_record_RAISES_rather_than_permitting(
        self, app, db, seed_user, seed_periods,
    ):  # pylint: disable=unused-argument
        """A broken invariant fails loud instead of reading as "anything goes".

        The alternative -- treating a missing record as an unbounded account --
        is the fabrication ``account_opening_fact`` exists to refuse on the read
        side, arriving through the write side instead.
        """
        with app.app_context():
            # **Built rather than emptied** (plan step X-f3c-2c).
            # ``budget.account_openings`` is append-only at the database tier,
            # so nothing may remove an account's opening record; the only route
            # to an account that has none is one the factory never touched --
            # which is what ``account_opening_fact``'s own message tells a
            # reader to go looking for.
            account = account_never_asserted(
                seed_user, db.session, name="Bounded",
            )
            db.session.flush()
            with pytest.raises(RuntimeError, match="zero"):
                reject_movement_before_books_open(account.id, date(2030, 1, 1))


class TestTheOneOrmWriter:
    """``record_settle_day`` asks it, so every ORM door inherits the refusal.

    ``status_seam.apply_status_change``, both ``entry_service`` doors and the
    statement matcher all reach ``settled_on`` through this one function, which
    is why the rule is asked there rather than at each of them.
    """

    def test_a_transaction_cannot_be_settled_on_the_opening_day(
        self, app, db, seed_user, seed_periods,
    ):  # pylint: disable=unused-argument
        """And the row is left UNTOUCHED, both columns."""
        with app.app_context():
            account = seed_user["account"]
            opened_on = _books_open_on(account)
            row = create_settled_cash_transaction(
                seed_user, seed_periods[0], Decimal("25.00"),
                settled_on=opened_on + _ONE_DAY, name="dated-probe",
            )
            before = (row.settled_on, row.settled_day_basis_id)
            with pytest.raises(ValidationError, match=r"books open on"):
                record_settle_day(row, _entered(opened_on))
            assert (row.settled_on, row.settled_day_basis_id) == before

    def test_a_purchase_cannot_be_settled_on_the_opening_day(
        self, app, db, seed_user, seed_periods,
    ):  # pylint: disable=unused-argument
        """The SECOND table carries the same rule, from the same call.

        ``transaction_entries`` is the other :class:`SettleDatedMixin` table and
        the fold reads a posted purchase as a movement exactly as it reads a
        settled transaction (ruling **R-FM**).  A rule stated for one table and
        enforced on one table is a rule the second table does not have.
        """
        with app.app_context():
            account = seed_user["account"]
            parent = create_settled_cash_transaction(
                seed_user, seed_periods[0], Decimal("25.00"),
                settled_on=_books_open_on(account) + _ONE_DAY, name="parent",
            )
            entry = TransactionEntry(
                **figure_source_columns(),
                transaction_id=parent.id,
                account_id=account.id,
                owner_id=parent.user_id,
                user_id=seed_user["user"].id,
                amount=Decimal("10.00"),
                description="probe",
                purchased_on=_books_open_on(account) - _ONE_DAY,
                is_credit=False,
            )
            with pytest.raises(ValidationError, match=r"books open on"):
                record_settle_day(entry, _entered(_books_open_on(account)))
            assert entry.settled_on is None

    def test_CLEARING_a_settle_day_is_never_bounded(
        self, app, db, seed_user, seed_periods,
    ):  # pylint: disable=unused-argument
        """A revert withdraws an assertion and states nothing about when.

        The same carve-out ``settle_day_for_status`` makes for the schedule
        floor (ruling **R-EG**'s unlock path): bounding the clear arm would
        strand a legacy row whose stored day precedes its own books, which is
        the one row that most needs to be revertible.
        """
        with app.app_context():
            account = seed_user["account"]
            row = create_settled_cash_transaction(
                seed_user, seed_periods[0], Decimal("25.00"),
                settled_on=_books_open_on(account) + _ONE_DAY, name="revert-probe",
            )
            record_settle_day(row, None)
            assert row.settled_on is None
            assert row.settled_day_basis_id is None

    def test_the_seam_refuses_it_for_every_caller(
        self, app, db, seed_user, seed_periods,
    ):  # pylint: disable=unused-argument
        """``apply_status_change`` inherits it, so a programmatic caller does too.

        The bound is a SEAM invariant and not a door policy, which is the
        difference from the schedule floor (ruling **R-EL**): a pre-schedule
        settle is absorbed by an assertion and is correct, where a pre-opening
        one is counted twice and never is.
        """
        with app.app_context():
            account = seed_user["account"]
            row = one_off_row_of(
                seed_periods[0],
                name="seam-probe",
                amount=Decimal("25.00"),
                user_id=seed_periods[0].user_id,
                account_id=account.id,
                scenario_id=seed_user["scenario"].id,
                transaction_type_id=ref_cache.txn_type_id(TxnTypeEnum.EXPENSE),
                category_id=seed_user["categories"]["Rent"].id,
            )
            db.session.flush()
            done = ref_cache.status_id(StatusEnum.DONE)
            with pytest.raises(ValidationError, match=r"books open on"):
                status_seam.apply_status_change(
                    row, done,
                    settle_day=_entered(_books_open_on(account)),
                    settlement=status_seam.Settlement.from_settle(
                        Decimal("10.00"), None,
                    ),
                )


class TestTheBulkWriterAsksForItself:
    """``reconcile_service.record_settled_days`` has no instance to be guarded.

    It stamps a statement's day onto every ticked purchase with one
    ``query.update()``, by design (its own comment: "there is no ORM instance
    to hand that function"), so the refusal
    :func:`app.services.settle_day.record_settle_day` inherits cannot reach it.
    It asks the same predicate itself, and this is the control that says so.

    **Reachable rather than theoretical.**  An assertion's ``observed_on`` is
    bounded below only by ``pay_period_service.earliest_recordable_day``, which
    on the developer's own data is 2026-03-26 -- the very day Checking's books
    open after this step's migration -- so a statement recorded for that day
    would stamp every ticked purchase inside the opening equity.
    """

    def test_a_statement_dated_inside_the_opening_stamps_NOTHING(
        self, app, db, seed_user, seed_periods,
    ):  # pylint: disable=unused-argument
        """The whole act is refused, and no entry is written.

        Refused per ACT rather than per entry, because the day being stamped is
        the STATEMENT's: either every tick lands inside the opening equity or
        none does.  Asserted on the entry's columns after the refusal, so a
        version that raised AFTER its ``UPDATE`` would fail here.
        """
        with app.app_context():
            account = seed_user["account"]
            opened_on = _books_open_on(account)
            parent = create_settled_cash_transaction(
                seed_user, seed_periods[0], Decimal("40.00"),
                settled_on=opened_on + timedelta(days=5), name="envelope",
            )
            entry = TransactionEntry(
                **figure_source_columns(),
                transaction_id=parent.id,
                account_id=account.id,
                owner_id=parent.user_id,
                user_id=seed_user["user"].id,
                amount=Decimal("10.00"),
                description="ticked purchase",
                purchased_on=opened_on - _ONE_DAY,
                is_credit=False,
            )
            db.session.add(entry)
            db.session.flush()

            governing = cash_anchor_facts(account.id)[0]
            statement = Statement(
                calendar_for(seed_user["user"].id),
                account.id,
                AnchorPoint(
                    anchor_id=governing.anchor_id,
                    balance=governing.anchor_balance,
                    observed_on=opened_on,
                    created_at=governing.asserted_at,
                ),
            )
            with pytest.raises(ValidationError, match=r"books open on"):
                record_settled_days(statement, {entry.id})
            db.session.refresh(entry)
            assert entry.settled_on is None
            assert entry.reconciled_by_id is None


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


class TestTheGoverningRowIsWhatIsGraded:
    """The openings side grades the SURVIVING books, not the row written.

    ``budget.assert_books_open_before_books_movements`` dispatches and
    ``budget.assert_account_books_hold_its_movements`` decides, and the
    predicate takes an ACCOUNT rather than a row.  That is what lets one
    statement of the rule cover an INSERT, a raw ``UPDATE`` and a raw
    ``DELETE`` -- and the delete is the case a row-oriented check cannot see at
    all, because the row that breaks the invariant is the one that SURVIVED.
    """

    def test_deleting_the_governing_restatement_cannot_strand_a_movement(
        self, app, db, seed_user, seed_periods,
    ):  # pylint: disable=unused-argument
        """Removing a restatement PROMOTES an older one, which may not reach.

        Hand-built so the promotion is the whole of what changes: the books are
        restated BACKWARD (legal, and the repair the refusal's own message
        recommends), a movement is then recorded in the span that restatement
        opened up, and the restatement is removed by raw SQL -- which the ORM
        refuses (``AccountOpening._block_delete``) and which is exactly why the
        arm lives in the database.  The older row's day is on or after the
        movement, so the account's books no longer hold what it records.
        """
        with app.app_context():
            account = seed_user["account"]
            original = _books_open_on(account)
            restate_account_opening(
                db.session, account, original - timedelta(days=10),
            )
            db.session.commit()
            restatement_id = account_opening_fact(account.id).opening_id

            create_settled_cash_transaction(
                seed_user, seed_periods[0], Decimal("25.00"),
                settled_on=original - timedelta(days=5),
                name="inside-the-older-books",
            )
            db.session.commit()

            # **The append-only refusal is lifted for this statement, and
            # only for it** (plan step X-f3c-2c).  It stands in front of
            # the arm under test here and would answer first, which would
            # leave that arm graded by nothing -- see
            # ``append_only_guard_lifted`` for why the answer is to reach
            # past it rather than to delete a measured control.
            with append_only_guard_lifted(
                db.session, "budget.account_openings",
            ):
                db.session.execute(
                    sa.text(_REMOVE_ONE_OPENING), {"i": restatement_id},
                )
                with refused_by_database_rule("cannot open its books"):
                    db.session.commit()
            db.session.rollback()

    def test_removing_one_that_does_NOT_govern_is_allowed(
        self, app, db, seed_user, seed_periods,
    ):  # pylint: disable=unused-argument
        """The negative half, so the arm above is not simply "no deletes".

        A superseded row decides nothing, so removing it changes no answer and
        the constraint has nothing to say.  Without this case the arm above
        would pass just as well against a trigger that refused every delete,
        which is a different and wrong rule.
        """
        with app.app_context():
            account = seed_user["account"]
            superseded_id = account_opening_fact(account.id).opening_id
            restate_account_opening(
                db.session, account, _books_open_on(account) - _ONE_DAY,
            )
            db.session.commit()
            governing = account_opening_fact(account.id)
            assert governing.opening_id != superseded_id

            # **The append-only refusal is lifted for this statement, and
            # only for it** (plan step X-f3c-2c).  It stands in front of
            # the arm under test here and would answer first, which would
            # leave that arm graded by nothing -- see
            # ``append_only_guard_lifted`` for why the answer is to reach
            # past it rather than to delete a measured control.
            with append_only_guard_lifted(
                db.session, "budget.account_openings",
            ):
                db.session.execute(
                    sa.text(_REMOVE_ONE_OPENING), {"i": superseded_id},
                )
                db.session.commit()
            assert account_opening_fact(account.id).opening_id == (
                governing.opening_id
            )


    def test_disposing_of_the_ACCOUNT_cascades_its_books_away(
        self, app, db, seed_user, seed_periods,
    ):  # pylint: disable=unused-argument
        """The DELETE arm cannot make an account undeletable.

        The case a new DELETE arm on a constraint trigger owes: a CASCADE
        removes several opening rows at once, each firing this trigger, and if
        the predicate graded the SURVIVING books against movements that were
        also being disposed of it would abort an ordinary disposal at COMMIT.

        **It cannot, and the reason is an FK asymmetry that this case PINS
        rather than assumes.**  ``budget.account_openings.account_id`` is ON
        DELETE CASCADE and ``budget.transactions.account_id`` is ON DELETE
        RESTRICT, so the two halves are asserted in the order they constrain:
        while a movement is on file the delete is refused by the FOREIGN KEY
        (an ``IntegrityError``, never this trigger's message -- if the books
        constraint were what refused it, that would be the bug this case is
        for); once the movement is gone the account and its several openings
        go together.  A first draft asserted only the second half against an
        account that had never recorded anything, which could not fail.

        The movement is a ONE-OFF since plan step ``balance:X-bi-7c`` -- a
        rule-less definition plus its placed row -- and the definition
        references the account too (``transaction_templates.account_id``,
        RESTRICT), so "with the movement gone" disposes of the definition
        with its last row, as the transaction delete verb does (ruling
        **R-BAL27**).
        """
        with app.app_context():
            account = create_account_of_type(
                seed_user, db.session, "Savings", "Disposable",
                anchor_balance=Decimal("500.00"),
            )
            restate_account_opening(
                db.session, account, _books_open_on(account) - timedelta(days=5),
            )
            movement = create_settled_cash_transaction(
                seed_user, seed_periods[0], Decimal("25.00"),
                settled_on=_books_open_on(account) + _ONE_DAY,
                account=account, name="on-the-books",
            )
            db.session.commit()
            account_id, movement_id = account.id, movement.id
            definition_id = movement.template_id
            assert db.session.execute(
                sa.text(_COUNT_OPENINGS), {"a": account_id},
            ).scalar() >= 3, "the cascade must take several rows, not one"

            # While the movement is on file, the FOREIGN KEY refuses -- and it
            # is the FK rather than the books trigger, which is the half that
            # makes the arm above safe.
            with pytest.raises(sa.exc.IntegrityError) as refusal:
                db.session.execute(
                    sa.text("DELETE FROM budget.accounts WHERE id = :i"),
                    {"i": account_id},
                )
                db.session.commit()
            assert "cannot open its books" not in str(refusal.value)
            db.session.rollback()

            # With it gone -- the row and the definition that was its -- the
            # account and its openings go together.
            # Its payments first, in the same raw SQL: a row holding one is no longer
            # deleted with it (R-CC54; rule-5 re-expression, developer-confirmed
            # 2026-09-23).
            db.session.execute(
                sa.text(
                    "DELETE FROM budget.transaction_entries "
                    "WHERE transaction_id = :i"
                ),
                {"i": movement_id},
            )
            db.session.execute(
                sa.text("DELETE FROM budget.transactions WHERE id = :i"),
                {"i": movement_id},
            )
            db.session.execute(
                sa.text("DELETE FROM budget.transaction_templates WHERE id = :i"),
                {"i": definition_id},
            )
            db.session.execute(
                sa.text("DELETE FROM budget.accounts WHERE id = :i"),
                {"i": account_id},
            )
            db.session.commit()
            assert db.session.execute(
                sa.text(_COUNT_OPENINGS), {"a": account_id},
            ).scalar() == 0


    def test_moving_an_opening_BETWEEN_accounts_grades_the_one_it_left(
        self, app, db, seed_user, seed_periods,
    ):  # pylint: disable=unused-argument
        """Both accounts an ``UPDATE`` touches are graded, not just the new one.

        The arm this pins is the whole reason
        ``budget.assert_account_books_hold_its_movements`` takes an ACCOUNT id
        instead of reading ``NEW``: one raw ``UPDATE`` can change which books
        govern TWO accounts, and the account it left is the one a
        ``NEW``-reading predicate never looks at.  The donor is left with an
        older restatement its own movements no longer fit, so a trigger that
        graded only ``NEW.account_id`` would commit this happily.

        No door does this -- the table is append-only and nothing reassigns an
        opening -- which is exactly why it belongs at the database tier, whose
        stated job is the writer nobody enumerated.
        """
        with app.app_context():
            donor = seed_user["account"]
            recipient = create_account_of_type(
                seed_user, db.session, "Savings", "Recipient",
                anchor_balance=Decimal("500.00"),
            )
            original = _books_open_on(donor)
            # The donor's books move BACK, a movement lands in the span that
            # opened up, and only the restatement keeps the two legal.
            restate_account_opening(
                db.session, donor, original - timedelta(days=10),
            )
            db.session.commit()
            moved_id = account_opening_fact(donor.id).opening_id
            create_settled_cash_transaction(
                seed_user, seed_periods[0], Decimal("25.00"),
                settled_on=original - timedelta(days=5),
                name="held-by-the-restatement",
            )
            db.session.commit()

            # Hand that restatement to the OTHER account.  The recipient is
            # unaffected -- it records nothing that early -- so any refusal
            # must come from grading the account the row LEFT.
            # **The append-only refusal is lifted for this statement, and
            # only for it** (plan step X-f3c-2c).  It stands in front of
            # the arm under test here and would answer first, which would
            # leave that arm graded by nothing -- see
            # ``append_only_guard_lifted`` for why the answer is to reach
            # past it rather than to delete a measured control.
            with append_only_guard_lifted(
                db.session, "budget.account_openings",
            ):
                db.session.execute(
                    sa.text(
                        "UPDATE budget.account_openings SET account_id = :b "
                        "WHERE id = :i"
                    ),
                    {"b": recipient.id, "i": moved_id},
                )
                with refused_by_database_rule("cannot open its books") as refusal:
                    db.session.commit()
            assert f"account {donor.id} " in str(refusal.value), (
                "the refusal must name the DONOR, which is the account a "
                "NEW-reading predicate would never have graded"
            )
            db.session.rollback()

    def test_the_PURCHASE_table_carries_the_constraint_too(
        self, app, db, seed_user, seed_periods,
    ):  # pylint: disable=unused-argument
        """The second movement table is attached, graded at the DATABASE tier.

        ``apply_opening_infrastructure`` attaches the movement trigger to
        ``budget.transaction_entries`` as well as ``budget.transactions``, and
        every other case for the entries table goes through the Python
        refusal -- which would still pass if the attachment were dropped from
        ``_MOVEMENT_TABLES`` or silently failed to re-pin.  Purchases are
        where the bulk ``query.update()`` writer lives, so the tier that
        covers it is the one that has to be shown attached.

        Raw SQL on purpose: an ORM write would grade the service rule instead.
        """
        with app.app_context():
            account = seed_user["account"]
            opened_on = _books_open_on(account)
            parent = create_settled_cash_transaction(
                seed_user, seed_periods[0], Decimal("40.00"),
                settled_on=opened_on + timedelta(days=5), name="envelope",
            )
            entry = TransactionEntry(
                **figure_source_columns(),
                transaction_id=parent.id,
                account_id=account.id,
                owner_id=parent.user_id,
                user_id=seed_user["user"].id,
                amount=Decimal("10.00"),
                description="purchase",
                # BEFORE the books, which is legal: a purchase DATE says
                # when the card was swiped and only ``settled_on`` says when
                # cash moved.  It also has to precede the settle day the
                # UPDATE below writes, or
                # ``ck_transaction_entries_settled_not_before_purchase``
                # refuses first and this case would grade that instead.
                purchased_on=opened_on - _ONE_DAY,
                is_credit=False,
                **settle_day_columns(opened_on + timedelta(days=5)),
            )
            db.session.add(entry)
            db.session.commit()

            db.session.execute(
                sa.text(
                    "UPDATE budget.transaction_entries SET settled_on = :d "
                    "WHERE id = :i"
                ),
                {"d": opened_on, "i": entry.id},
            )
            with refused_by_database_rule("books open"):
                db.session.commit()
            db.session.rollback()

class TestTheTwoGoverningLookupsElectTheSameRow:
    """SQL and Python must not disagree about which restatement is in force.

    ``budget.account_books_opened_on`` decides what the constraint ENFORCES and
    :func:`app.services.cash_ledger.account_opening_fact` decides what the fold
    SEEDS AT.  They are two implementations of one rule (ruling **R-HE**: the
    latest RECORDING instant governs, ``id`` breaking a same-instant tie), and
    nothing but this class holds them to the same answer -- a disagreement
    would let the app render a balance from a level the database is
    simultaneously refusing to let it record against.
    """

    def test_they_agree_when_the_recording_order_contradicts_the_days(
        self, app, db, seed_user, seed_periods,
    ):  # pylint: disable=unused-argument
        """Restatements recorded in the REVERSE order of the days they name.

        A lookup ordered by ``opened_on`` -- which is what the positional read
        plan step X-f3c-2a deleted did -- elects the 1-day-back row here, and
        one ordered by the recording instant elects the 90-days-back row.  The
        days are chosen so those are different rows, so the case fails rather
        than passing vacuously if either side drifts.
        """
        with app.app_context():
            account = seed_user["account"]
            base = _books_open_on(account)
            _record_two_restatements(
                account,
                base - timedelta(days=1),
                base - timedelta(days=90),
                same_instant=False,
            )

            in_sql = db.session.execute(
                sa.text(_OPENED_ON_IN_SQL), {"a": account.id},
            ).scalar()
            assert in_sql == base - timedelta(days=90)
            assert account_opening_fact(account.id).opened_on == in_sql

    def test_they_break_a_SAME_INSTANT_tie_the_same_way(
        self, app, db, seed_user, seed_periods,
    ):  # pylint: disable=unused-argument
        """Two rows sharing ``created_at`` to the microsecond, ``id`` deciding.

        Reachable rather than contrived: ``created_at`` carries a ``now()``
        default and PostgreSQL's ``now()`` is the TRANSACTION timestamp, so two
        restatements written in one transaction share it exactly.  Both sides
        break the tie on ``id`` DESC; without that arm each lookup would take
        whichever row its own plan happened to hand it first.
        """
        with app.app_context():
            account = seed_user["account"]
            base = _books_open_on(account)
            _record_two_restatements(
                account,
                base - timedelta(days=30),
                base - timedelta(days=60),
                same_instant=True,
            )

            rows = db.session.query(AccountOpening).filter_by(
                account_id=account.id,
            ).order_by(AccountOpening.id).all()
            assert rows[-1].opened_on == base - timedelta(days=60)

            in_sql = db.session.execute(
                sa.text(_OPENED_ON_IN_SQL), {"a": account.id},
            ).scalar()
            assert in_sql == rows[-1].opened_on
            assert account_opening_fact(account.id).opened_on == in_sql


def _an_account_with_no_movement(seed_user, name, opened_on):
    """Return an account whose books open on *opened_on* and record nothing.

    Every case in the class below needs one: the matched-line arm is a window
    INSIDE the movement bound, so an account carrying a movement would have
    the bound beside it answer first and the cases would pass against an arm
    that does nothing.

    Args:
        seed_user: The seeded user bundle.
        name: The account name, unique per owner.
        opened_on: The civil day its books open.

    Returns:
        The committed :class:`~app.models.account.Account`.
    """
    account = account_never_asserted(seed_user, _db.session, name=name)
    _db.session.flush()
    _db.session.add(AccountOpening(
        account_id=account.id,
        opened_on=opened_on,
        opening_equity=Decimal("10.00"),
        source_id=ref_cache.account_opening_source_id(
            AccountOpeningSourceEnum.USER_DECLARED,
        ),
    ))
    _db.session.commit()
    return account


def _match_a_group(account, owner_id, early, late):
    """Match two bank lines on *account*, posted *early* and *late*.

    **The SQL lives in ``tests/_test_helpers.py``, once.**  It was copied
    byte-identically into three test modules until an adversarial
    test-quality review counted them -- and the query must agree with
    :data:`app.opening_infrastructure.MATCHED_LINE_DAYS_SQL`'s row set, so
    three copies were three places for that to drift silently.

    Args:
        account: The :class:`~app.models.account.Account` to match on.
        owner_id: Its owner.
        early: The earlier line's posting day.
        late: The later one's.
    """
    match_two_lines(_db.session, account, owner_id, early, late)


class TestAMatchedLineBoundsTheBooksToo:
    """The fourth arm (plan step balance:X-f3c-2b-2b, ruling **R-IH**).

    **The window this closes is INSIDE the movement bound rather than beside
    it**, which is why every case here uses an account that records no settled
    movement at all: with one, the movements predicate answers first and these
    cases would pass against an arm that does nothing.
    ``statement_match._accept.record_match`` settles every member on the
    LATEST of the match's bank days, so a group's earliest line posts strictly
    before the row explaining it settles, and every day in between is one the
    movement bound calls legal.
    """

    def test_the_reader_returns_the_EARLIEST_matched_day(
        self, app, db, seed_user, seed_periods,
    ):  # pylint: disable=unused-argument
        """Two lines, one match: the bound is the earlier of them.

        The LATER day is the one a movement would carry, so a reader that
        returned it would agree with the movement bound and close nothing.
        The ``None`` assertion before the match is what makes the second one
        mean something.
        """
        with app.app_context():
            opened = seed_periods[0].start_date
            account = _an_account_with_no_movement(
                seed_user, "Matched Reader", opened,
            )
            assert earliest_matched_line_day(account.id) is None

            early = opened + timedelta(days=10)
            _match_a_group(
                account, seed_user["user"].id,
                early, early + timedelta(days=10),
            )

            assert earliest_matched_line_day(account.id) == early

    def test_the_door_refuses_a_restatement_INTO_the_window(
        self, app, db, seed_user, seed_periods,
    ):  # pylint: disable=unused-argument
        """FIRING CONTROL, and the whole reason this arm exists.

        The account records NO movement, so
        ``reject_books_open_on_or_after_movements`` returns without counting
        anything -- and the day chosen sits BETWEEN the group's two lines,
        which is exactly where a real match leaves the gap.
        """
        with app.app_context():
            opened = seed_periods[0].start_date
            account = _an_account_with_no_movement(
                seed_user, "Matched Window", opened,
            )
            early = opened + timedelta(days=10)
            _match_a_group(
                account, seed_user["user"].id,
                early, early + timedelta(days=10),
            )
            assert earliest_recorded_movement_day(account.id) is None, (
                "this case isolates the MATCHED-LINE arm; a movement would "
                "make the bound beside it answer first"
            )

            with pytest.raises(ValidationError, match="matched a bank line"):
                reject_books_open_on_or_after_matched_lines(
                    account.id, early + timedelta(days=5),
                )

    def test_a_restatement_BELOW_the_window_is_accepted(
        self, app, db, seed_user, seed_periods,
    ):  # pylint: disable=unused-argument
        """The refusal is a BOUND and not a blanket.

        Without this the case above passes against a door that refuses every
        restatement on any account that has ever matched a line.
        """
        with app.app_context():
            opened = seed_periods[0].start_date
            account = _an_account_with_no_movement(
                seed_user, "Matched Below", opened,
            )
            early = opened + timedelta(days=10)
            _match_a_group(
                account, seed_user["user"].id,
                early, early + timedelta(days=10),
            )

            reject_books_open_on_or_after_matched_lines(
                account.id, early - timedelta(days=1),
            )

    def test_the_boundary_is_INCLUSIVE_on_the_lines_own_day(
        self, app, db, seed_user, seed_periods,
    ):  # pylint: disable=unused-argument
        """A ``<`` written for a ``<=`` passes every case a day either side.

        The opening equity is the CLOSING balance for its own day (R-HG), so
        books opening ON the day a matched line posted already hold that
        line's money.
        """
        with app.app_context():
            opened = seed_periods[0].start_date
            account = _an_account_with_no_movement(
                seed_user, "Matched Boundary", opened,
            )
            early = opened + timedelta(days=10)
            _match_a_group(
                account, seed_user["user"].id,
                early, early + timedelta(days=10),
            )

            with pytest.raises(ValidationError, match="matched a bank line"):
                reject_books_open_on_or_after_matched_lines(account.id, early)

    def test_the_DATABASE_refuses_a_member_the_books_cannot_hold(
        self, app, db, seed_user, seed_periods,
    ):  # pylint: disable=unused-argument
        """The structural half, on the client with no door.

        Raw SQL on purpose, exactly as the class above it argues: an ORM call
        would prove only that the service refusal fires.  A match member is
        written by ``statement_match._accept._record`` today, and the whole
        point of the constraint is the writer nobody enumerated.
        """
        with app.app_context():
            opened = seed_periods[0].start_date
            account = _an_account_with_no_movement(
                seed_user, "Matched Trigger", opened,
            )

            # **The matched-line predicate's OWN wording.**  Both database
            # arms raise "...the day that account's books open (%)...", so
            # matching that phrase could not tell which one refused -- the
            # discrimination the case four down deliberately buys.  Only
            # ``assert_matched_line_holds_books`` names a bank statement line.
            with refused_by_database_rule("bank statement line"):
                _match_a_group(
                    account, seed_user["user"].id, opened,
                    opened + timedelta(days=10),
                )
            db.session.rollback()

    def test_the_DATABASE_refuses_the_RESTATEMENT_past_a_matched_line(
        self, app, db, seed_user, seed_periods,
    ):  # pylint: disable=unused-argument
        """The other direction, and the one no write door can see.

        Moving the books FORWARD is how an account acquires a pre-opening
        matched line without anybody writing a member row -- the same argument
        the movement arm's own forward case makes, over the second row set.
        """
        with app.app_context():
            opened = seed_periods[0].start_date
            account = _an_account_with_no_movement(
                seed_user, "Matched Forward", opened,
            )
            early = opened + timedelta(days=10)
            _match_a_group(
                account, seed_user["user"].id,
                early, early + timedelta(days=10),
            )

            db.session.add(AccountOpening(
                account_id=account.id,
                opened_on=early + timedelta(days=5),
                opening_equity=Decimal("10.00"),
                source_id=ref_cache.account_opening_source_id(
                    AccountOpeningSourceEnum.USER_DECLARED,
                ),
            ))
            # The DATABASE's own wording, not the service's: the two tiers
            # address different readers and a case that matched a phrase they
            # share could not tell which one refused -- which is exactly what
            # a planted defect showed one door over on 2026-08-31.
            with refused_by_database_rule("bank line you have matched"):
                db.session.commit()
            db.session.rollback()

    def test_the_DATABASE_refuses_MOVING_a_matched_lines_day_BACK(
        self, app, db, seed_user, seed_periods,
    ):  # pylint: disable=unused-argument
        """THE EVASION, and the control the repair that closed it owes.

        The members trigger grades a day that lives on ANOTHER table, so a
        rule attached there alone let ``UPDATE budget.bank_statement_lines SET
        posted_on = ...`` on an already-matched line commit cleanly into the
        forbidden state -- against a module docstring claiming no client could
        store it.  Found by adversarial design review 2026-08-31 and closed by
        a SECOND attachment, ``ck_line_day_after_books_open``.

        Raw SQL for the reason the whole class uses it: nothing in the app
        updates ``posted_on`` at all, so the writer this grades is the one
        nobody enumerated -- which is the entire point of the database tier.
        """
        with app.app_context():
            opened = seed_periods[0].start_date
            account = _an_account_with_no_movement(
                seed_user, "Matched Day Moves", opened,
            )
            early = opened + timedelta(days=10)
            _match_a_group(
                account, seed_user["user"].id,
                early, early + timedelta(days=10),
            )

            _db.session.execute(sa.text(
                "UPDATE budget.bank_statement_lines SET posted_on = :d "
                "WHERE account_id = :a AND posted_on = :was"
            ), {"d": opened, "a": account.id, "was": early})
            # The matched-line predicate's own wording, for the reason the
            # member case above states: "books open" is shared by both arms.
            with refused_by_database_rule("bank statement line"):
                _db.session.commit()
            _db.session.rollback()

    def test_a_matched_lines_day_may_still_move_ABOVE_the_books(
        self, app, db, seed_user, seed_periods,
    ):  # pylint: disable=unused-argument
        """The carve-out's first direction: the bound is a bound.

        Without this the case above passes against a trigger that refuses
        every re-date, which is a rule nobody asked for and which would make
        an ordinary import correction impossible.
        """
        with app.app_context():
            opened = seed_periods[0].start_date
            account = _an_account_with_no_movement(
                seed_user, "Matched Day Legal", opened,
            )
            early = opened + timedelta(days=10)
            _match_a_group(
                account, seed_user["user"].id,
                early, early + timedelta(days=10),
            )
            moved_to = early + timedelta(days=1)

            _db.session.execute(sa.text(
                "UPDATE budget.bank_statement_lines SET posted_on = :d "
                "WHERE account_id = :a AND posted_on = :was"
            ), {"d": moved_to, "a": account.id, "was": early})
            _db.session.commit()

            assert earliest_matched_line_day(account.id) == moved_to

    def test_an_UNMATCHED_lines_day_may_move_ANYWHERE(
        self, app, db, seed_user, seed_periods,
    ):  # pylint: disable=unused-argument
        """The carve-out's SECOND direction, and it is the one that matters.

        An imported line the owner has not explained is EVIDENCE, not a
        record: nothing of theirs claims it, so its day bounds nothing and the
        books may open right over it.  A trigger that graded every line rather
        than every MATCHED line would refuse an ordinary re-import correction
        on a line no row of theirs names -- the too-strong mutation the
        refusal case above cannot see, which is why ``lessons.md`` asks for a
        mutation per direction.
        """
        with app.app_context():
            opened = seed_periods[0].start_date
            account = _an_account_with_no_movement(
                seed_user, "Unmatched Day Free", opened,
            )
            statement = _db.session.execute(sa.text(
                "INSERT INTO budget.statement_imports "
                "(account_id, user_id, source_id, file_name, file_digest, "
                " declared_start, declared_end) "
                "SELECT :a, :u, "
                " (SELECT id FROM ref.statement_sources ORDER BY id LIMIT 1), "
                " 'unmatched-probe.csv', :digest, :d, :d "
                "RETURNING id"
            ), {
                "a": account.id, "u": seed_user["user"].id,
                "digest": f"unmatched-{account.id}",
                "d": opened + timedelta(days=10),
            }).scalar()
            line = _db.session.execute(sa.text(
                "INSERT INTO budget.bank_statement_lines "
                "(account_id, posted_on, amount, sequence_in_group) "
                "VALUES (:a, :d, -1.00, 0) RETURNING id"
            ), {"a": account.id, "d": opened + timedelta(days=10)}).scalar()
            # The line is held by its import's SIGHTING (plan step
            # ``bank_import:X-f6b-1``); the wording lives there.
            _db.session.execute(sa.text(
                "INSERT INTO budget.statement_line_sightings "
                "(account_id, line_id, import_id, description) "
                "VALUES (:a, :l, :i, 'UNMATCHED')"
            ), {"a": account.id, "l": line, "i": statement})
            _db.session.commit()

            _db.session.execute(sa.text(
                "UPDATE budget.bank_statement_lines SET posted_on = :d "
                "WHERE id = :l"
            ), {"d": opened - timedelta(days=5), "l": line})
            _db.session.commit()

            # It moved, and it bounds nothing: no match names it.
            assert earliest_matched_line_day(account.id) is None
