"""Architecture test: what KIND of transaction a request runs in.

Plan step **balance:X-i3**, finding **N-353**.  :mod:`app.db_transaction` makes
one rule structural: a GET or HEAD runs at ``REPEATABLE READ, READ ONLY`` -- one
snapshot of the database for every statement it issues, and no write possible
inside it -- and every other method runs at PostgreSQL's default ``READ
COMMITTED``, writable, exactly as before.

Why the read half matters
-------------------------

Under ``READ COMMITTED`` *each statement gets its own snapshot*.  A render
assembles one screen from dozens of statements, so it could read the owner's
pay calendar in one and the rows filed against it in another, and the two could
disagree -- which is exactly what ``/grid``'s rolling top-up produces for a
concurrent render: it appends a pay period, repopulates it from every active
template, and commits mid-render.

Why the WRITE half matters at least as much
-------------------------------------------

**A command may not be given one snapshot, and this file is where that is
pinned.**  The writes the owner's advisory lock serialises
(:mod:`app.services.user_write_lock`, taken where each command transaction
begins) read and then write: the anchor doors read the assertion that governs
and append only when the submission differs (ruling **R-EQ**), and the
pay-period mutations count the owner's periods and then append or delete
against that count.  The waiter's read is REQUIRED to see the winner's
just-committed rows, and only ``READ COMMITTED`` shows them.  Under one
snapshot the waiter reads its own pre-lock picture and acts on a stale one.
The case measured was the posting-ledger reconciles' (read what is posted,
write the difference): the divergence ruling **R-EN** measured at
``$4,000.00`` asserted against ``$1,000.00`` settled, with the trial balance
still ``$0.00`` because the anchor-equity leg mirrors the error, so nothing
failed loudly.  No door runs those reconciles since plan step plan#2914.

So "make everything REPEATABLE READ, it is strictly safer" is a plausible,
wrong change, and :func:`test_a_command_stays_read_committed` is the control
that refuses it.

Every test here is BEHAVIOURAL
------------------------------

Nothing below asserts that a particular function was called or that a module
carries a particular line.  Each asks the database what mode it is actually in,
or drives a real request and asks what that request could see.  The snapshot
test carries its own planted defect (:func:`test_the_snapshot_control_fires`),
because a test that would pass with the guarantee removed proves nothing.
"""

from datetime import date

import psycopg
import pytest
from flask import g, request_started
from sqlalchemy import event, text
from sqlalchemy.engine import Engine
from sqlalchemy.exc import InternalError
from sqlalchemy.orm import Session

from app import db_transaction
from app.extensions import db
from app.models.pay_period import PayPeriod
from app.models.user import UserSettings
from tests._test_helpers import shift_id_of

#: The statement that tells the audit triggers who is acting.  Matched as a
#: FRAGMENT of the SQL rather than by patching
#: :func:`app.audit_infrastructure.bind_audit_actor`, so what the tests below
#: count is what PostgreSQL was actually asked to do.
_ACTOR_BIND_SQL = "set_config('app.current_user_id'"


def _record_len(calendar, sink):
    """Append *calendar*'s payday count to *sink* and return it unchanged."""
    sink.append(len(calendar.periods))
    return calendar


@pytest.fixture()
def observed_modes(app):
    """Record the isolation mode of every transaction opened while active.

    Registered AFTER :mod:`app.db_transaction`'s own ``after_begin`` listener,
    which is installed at module import -- SQLAlchemy fires listeners in
    registration order, so by the time this one runs the ``SET TRANSACTION`` has
    already been issued and what it reads is the mode as bound.

    Yields:
        The list of ``(isolation, read_only)`` pairs, appended to as the test
        runs.
    """
    seen = []

    def _record(session, transaction, connection):  # noqa: ARG001
        seen.append(
            connection.exec_driver_sql(
                "select current_setting('transaction_isolation'), "
                "current_setting('transaction_read_only')"
            ).one()
        )

    event.listen(Session, "after_begin", _record)
    try:
        yield seen
    finally:
        event.remove(Session, "after_begin", _record)


@pytest.fixture()
def observed_statements(app):
    """Record every statement the engine executes while active.

    On the ``Engine`` class rather than on this app's engine, for the reason
    :func:`observed_modes` registers on the ``Session`` class: the listener
    then sees what the process actually issued, whichever session issued it.

    **It cannot see a ``BEGIN``, a ``COMMIT`` or a ``ROLLBACK``**, which is
    worth stating because a reader will otherwise take a count here for the
    round-trip count: psycopg issues those through the connection rather than
    through a cursor, so they never reach ``before_cursor_execute``.  Every
    test below counts a named statement, never a total.

    Yields:
        The list of normalised SQL strings, appended to as the test runs.
    """
    seen = []

    def _record(conn, cursor, statement, params, context, executemany):  # noqa: ARG001
        seen.append(" ".join(statement.split()))

    event.listen(Engine, "before_cursor_execute", _record)
    try:
        yield seen
    finally:
        event.remove(Engine, "before_cursor_execute", _record)


class TestTheModeIsBoundByTheDispatch:
    """A dispatched request's method decides its transaction, and nothing else."""

    def test_a_query_runs_read_only_at_repeatable_read(
        self, auth_client, seed_user, observed_modes,
    ):
        """A GET's transactions are one snapshot each, and cannot write."""
        assert auth_client.get("/settings").status_code == 200

        during = [pair for pair in observed_modes if pair[1] == "on"]
        assert during, "no read-only transaction was opened during the GET"
        assert all(
            isolation == "repeatable read" for isolation, _ in during
        ), observed_modes

    def test_a_command_stays_read_committed(
        self, auth_client, seed_user, observed_modes,
    ):
        """A POST keeps the default, and this is the money-critical half.

        ``user_write_lock``'s lock-then-read is only correct at ``READ
        COMMITTED``: the waiting transaction must see the winner's committed
        rows.  If a future change binds one snapshot here, a waiter acts on
        the picture it had before the lock.  See this module's docstring for
        what that was measured at.
        """
        observed_modes.clear()
        auth_client.post("/settings", data={"grid_default_periods": "7"})

        assert observed_modes, "the POST opened no transaction"
        assert all(
            pair == ("read committed", "off") for pair in observed_modes
        ), observed_modes

    def test_a_caller_with_no_dispatched_request_is_untouched(
        self, app, db, seed_user, observed_modes,
    ):
        """A CLI script, the deploy and Alembic keep what they had.

        ``pytest-flask`` pushes a synthetic ``test_request_context`` around
        every test in this suite -- method ``GET``, never dispatched -- so this
        also pins that a request CONTEXT is not a request: were the mode
        derived from ``request.method`` rather than recorded by the dispatch,
        this test body itself would be read-only and could not write.
        """
        db.session.rollback()
        db.session.query(PayPeriod).filter_by(user_id=seed_user["user"].id).count()

        assert observed_modes == [("read committed", "off")]


class TestAQueryIsOneSnapshot:
    """The property finding N-353 records, and the defect that proves it fires."""

    @staticmethod
    def _append_a_payday_from_another_connection(app, user_id, start_date):
        """Commit one pay period on a SEPARATE connection, as a rival would.

        A second connection is the whole point: the row must be committed by
        someone else while the request is in flight, which is what the rolling
        top-up on a concurrent ``/grid`` render does.

        Args:
            app: The Flask application, for its database URI.
            user_id: The owner to append the payday for.
            start_date: The payday.
        """
        rival = psycopg.connect(app.config["SQLALCHEMY_DATABASE_URI"])
        try:
            rival.autocommit = True
            with rival.cursor() as cur:
                cur.execute(
                    "insert into budget.pay_periods (user_id, start_date) "
                    "values (%s, %s)",
                    (user_id, start_date),
                )
        finally:
            rival.close()

    @pytest.fixture()
    def calendars_the_render_saw(self, app, seed_user, monkeypatch):
        """Interleave a rival's committed payday, and record what the pass saw.

        The rival commits from ``before_cursor_execute`` on the FIRST statement
        that touches ``budget.pay_periods`` -- before that statement runs, so a
        ``READ COMMITTED`` reader sees the new row in that very statement and a
        snapshot reader cannot.  No statement counting, so nothing here breaks
        when a route's query plan changes.

        Yields:
            ``(counts, armed)`` -- the payday counts each ``calendar_for`` call
            returned, and the one-shot arming flag, which a test asserts is
            SPENT so a render that never read ``budget.pay_periods`` cannot
            pass vacuously.
        """
        counts = []
        armed = [True]

        # Patched where the read pass BINDS it, not where it is defined: the
        # seam imported the name at module load, so patching the package would
        # leave the pass calling the original.
        import app.services.balance_at._context as seam  # pylint: disable=import-outside-toplevel

        real_calendar_for = seam.calendar_for

        def _recording_calendar_for(user_id):
            calendar = real_calendar_for(user_id)
            counts.append(len(calendar.periods))
            return calendar

        monkeypatch.setattr(seam, "calendar_for", _recording_calendar_for)

        def _interleave(conn, cursor, statement, parameters, context, executemany):  # noqa: ARG001
            if not armed[0] or "pay_periods" not in statement:
                return
            armed[0] = False
            self._append_a_payday_from_another_connection(
                app, seed_user["user"].id, date(2099, 1, 1),
            )

        event.listen(db.engine, "before_cursor_execute", _interleave)
        try:
            yield counts, armed
        finally:
            event.remove(db.engine, "before_cursor_execute", _interleave)

    def test_a_render_cannot_see_a_payday_committed_under_it(
        self, auth_client, seed_user, seed_periods, calendars_the_render_saw,
    ):
        """N-353's exact shape, made unconstructible.

        The rival's payday commits while the render is running.  Every calendar
        the pass derives is taken from ONE snapshot, so none of them holds it --
        which is what keeps a row from being valued against a calendar older
        than it is.
        """
        calendars, armed = calendars_the_render_saw
        before = len(seed_periods)

        assert auth_client.get("/grid").status_code == 200

        assert calendars, "the render derived no calendar"
        assert not armed[0], (
            "the rival never committed its payday -- no statement naming "
            "budget.pay_periods ran, so this test proves nothing about "
            "snapshots"
        )
        assert all(seen == before for seen in calendars), (
            f"the render saw {calendars} paydays against {before} at its "
            f"snapshot; a concurrent append reached it"
        )

    def test_the_snapshot_control_fires(
        self, auth_client, seed_user, seed_periods, calendars_the_render_saw,
        monkeypatch,
    ):
        """The SAME render, with the guarantee removed, DOES see the append.

        The planted defect is the smallest one that removes THIS rule and
        nothing else: the request is still classified as a query, so
        ``write_transaction`` still works and ``/grid`` still renders -- only
        the isolation the query binds is put back to the default every render
        had before this step.  A defect that also broke the classification
        would prove the test notices *something*, not that it notices this.

        Without this control the test above would pass just as happily against
        a boundary that had quietly stopped binding anything.
        """
        monkeypatch.setattr(
            db_transaction,
            "_QUERY_TRANSACTION_SQL",
            "SET TRANSACTION ISOLATION LEVEL READ COMMITTED",
        )
        calendars, armed = calendars_the_render_saw
        before = len(seed_periods)

        assert auth_client.get("/grid").status_code == 200

        assert calendars, "the render derived no calendar"
        assert not armed[0], "the rival never committed its payday"
        assert any(seen > before for seen in calendars), (
            "READ COMMITTED should have shown the rival's payday to at least "
            f"one calendar; saw {calendars} against {before}"
        )


class TestAQueryMayNotWrite:
    """``READ ONLY`` is what makes the rule the database's rather than a habit."""

    def test_a_write_inside_a_render_is_refused_by_postgresql(
        self, app, auth_client, seed_user,
    ):
        """Not a checker, not a review note: an error from the server.

        Driven through a real dispatched GET so the refusal is the one a
        render would actually meet.  ``/health`` is used because it is the
        cheapest GET in the application and its own body is irrelevant here --
        the write is attempted from a listener, standing in for any producer
        that decided to repair something mid-render.
        """
        attempted = []

        def _write_during_the_render(session, transaction, connection):  # noqa: ARG001
            if not db_transaction._is_query_request():  # pylint: disable=protected-access
                return
            # Inside a SAVEPOINT: PostgreSQL aborts a transaction on ANY error,
            # so an unguarded refusal here would poison the request rather than
            # let it finish, and the test would be measuring the poisoning.
            connection.exec_driver_sql("SAVEPOINT the_render_tried_to_write")
            try:
                connection.exec_driver_sql(
                    "insert into budget.pay_periods "
                    "(user_id, start_date) values (1, '2099-06-01')"
                )
                attempted.append("allowed")
            except InternalError as exc:
                attempted.append(type(exc.orig).__name__)
            connection.exec_driver_sql(
                "ROLLBACK TO SAVEPOINT the_render_tried_to_write"
            )

        event.listen(Session, "after_begin", _write_during_the_render)
        try:
            auth_client.get("/health")
        finally:
            event.remove(Session, "after_begin", _write_during_the_render)

        assert attempted, "no query transaction was opened during the GET"
        assert set(attempted) == {"ReadOnlySqlTransaction"}, attempted


class TestWriteTransaction:
    """The one door a render has when it must write, and its two refusals."""

    def test_the_rolling_top_up_still_lands_and_the_render_sees_it(
        self, app, auth_client, seed_user, seed_periods, db, monkeypatch,
    ):
        """``/grid``'s top-up commits, and the pass snapshots AFTER it.

        The behavioural proof that ``write_transaction`` does what the route
        needs: a schedule short of its rolling target is extended by the render
        that finds it, from INSIDE a read-only request, and the periods are
        still there once that request has gone.
        """
        from app.models.pay_schedule import PaySchedule  # pylint: disable=import-outside-toplevel

        target = len(seed_periods) + 4
        schedule = db.session.query(PaySchedule).filter_by(
            user_id=seed_user["user"].id,
        ).one_or_none()
        if schedule is None:
            schedule = PaySchedule(user_id=seed_user["user"].id)
            db.session.add(schedule)
        schedule.rolling_enabled = True
        schedule.rolling_target_periods = target
        db.session.commit()

        seen = []
        import app.services.balance_at._context as seam  # pylint: disable=import-outside-toplevel

        real_calendar_for = seam.calendar_for
        monkeypatch.setattr(seam, "calendar_for", lambda uid: _record_len(
            real_calendar_for(uid), seen,
        ))

        assert auth_client.get("/grid").status_code == 200

        db.session.rollback()
        assert db.session.query(PayPeriod).filter_by(
            user_id=seed_user["user"].id,
        ).count() >= target, "the top-up did not commit inside the render"
        # And the RENDER saw them, which is the half the block exists for: the
        # pass snapshots AFTER the command commits, not before it.
        assert seen and min(seen) >= target, (
            f"the read pass derived {seen} paydays after a top-up that left "
            f"at least {target} committed; its snapshot predates the write"
        )

    def test_a_grid_render_is_query_command_query(
        self, app, auth_client, seed_user, seed_periods, db, observed_modes,
    ):
        """The mode SEQUENCE of the one route that declares a write.

        It is the counterexample to
        :meth:`TestTheDecisionPrecedesTheRequestSFirstStatement.test_every_transaction_of_a_query_is_one_snapshot`,
        and having both is what keeps that one from reading as "every
        transaction in the application is read-only".  ``/grid`` opens its
        snapshot, leaves it for a writable transaction that commits the
        rolling top-up, and returns to a NEW snapshot -- which is what lets
        the render see the periods the block just created.

        Asserted as a SUBSEQUENCE rather than as the whole list, because how
        many transactions the render itself opens is a property of the render
        and not of this rule; what this pins is that the writable one is
        surrounded by read-only ones.
        """
        from app.models.pay_schedule import PaySchedule  # pylint: disable=import-outside-toplevel

        schedule = db.session.query(PaySchedule).filter_by(
            user_id=seed_user["user"].id,
        ).one_or_none()
        if schedule is None:
            schedule = PaySchedule(user_id=seed_user["user"].id)
            db.session.add(schedule)
        schedule.rolling_enabled = True
        schedule.rolling_target_periods = len(seed_periods) + 4
        db.session.commit()
        observed_modes.clear()

        assert auth_client.get("/grid").status_code == 200

        writable = [
            index for index, pair in enumerate(observed_modes)
            if pair == ("read committed", "off")
        ]
        assert len(writable) == 1, (
            f"expected exactly one writable transaction -- the top-up's -- in "
            f"a /grid render, saw {len(writable)}: {observed_modes}"
        )
        at = writable[0]
        assert at > 0 and observed_modes[at - 1] == ("repeatable read", "on"), (
            f"the write_transaction block did not leave a snapshot behind it: "
            f"{observed_modes}"
        )
        assert observed_modes[at + 1:] and all(
            pair == ("repeatable read", "on") for pair in observed_modes[at + 1:]
        ), (
            f"the render did not return to a snapshot after the block "
            f"committed: {observed_modes}"
        )

    def test_a_write_it_commits_still_names_the_ACTOR_in_the_audit_log(
        self, app, auth_client, seed_user, seed_periods, db,
    ):
        """The regression this block introduced, and the reason it is here.

        The audit triggers attribute a row through a TRANSACTION-scoped
        setting (:func:`app.audit_infrastructure.bind_audit_actor`).  A request
        used to run in ONE transaction, so one before-request hook could bind
        it once; this step gave a render its own snapshot and a render's
        declared write a command transaction of its own, and the first draft
        bound neither -- so every pay period the rolling top-up appended landed
        in ``system.audit_log`` with a NULL user, silently, because the
        trigger reads an unset setting as "no authenticated user".

        Found by adversarial review, twice, independently.
        """
        from app.models.pay_schedule import PaySchedule  # pylint: disable=import-outside-toplevel

        target = len(seed_periods) + 4
        schedule = db.session.query(PaySchedule).filter_by(
            user_id=seed_user["user"].id,
        ).one_or_none()
        if schedule is None:
            schedule = PaySchedule(user_id=seed_user["user"].id)
            db.session.add(schedule)
        schedule.rolling_enabled = True
        schedule.rolling_target_periods = target
        db.session.commit()
        db.session.execute(text("DELETE FROM system.audit_log"))
        db.session.commit()

        assert auth_client.get("/grid").status_code == 200

        db.session.rollback()
        actors = db.session.execute(text(
            "select distinct user_id from system.audit_log "
            "where table_name = 'pay_periods'"
        )).scalars().all()
        assert actors, "the top-up wrote no audited pay-period rows"
        assert actors == [seed_user["user"].id], (
            f"the rolling top-up's rows were attributed to {actors}; a NULL "
            f"here is the audit trail losing the acting user on every write a "
            f"render commits"
        )

    def test_it_refuses_a_caller_with_no_dispatched_request(self, app):
        """Outside a request there is no snapshot to leave and return to."""
        with app.app_context():
            with pytest.raises(RuntimeError, match="no dispatched request"):
                with db_transaction.write_transaction():
                    pass

    def test_it_refuses_a_COMMAND_transaction(self, app):
        """The second refusal: a mutation route is writable end to end.

        Covers both spellings of the same state -- a POST, and a block already
        open on a query request -- because the mode is what distinguishes them
        and by then it is the same value.
        """
        with app.test_request_context("/settings", method="POST"):
            setattr(g, db_transaction._MODE_KEY, db_transaction._COMMAND)  # pylint: disable=protected-access
            with pytest.raises(RuntimeError, match="COMMAND transaction"):
                with db_transaction.write_transaction():
                    pass

    def test_a_body_that_raises_leaves_nothing_behind(
        self, app, auth_client, seed_user, db,
    ):
        """The block rolls back on the way out, and hands the mode back.

        Both halves matter: a render whose declared write fails must commit
        none of it, and the render that continues must still be a query -- if
        the mode were left on COMMAND the rest of the page would run writable
        and un-snapshotted.
        """
        with app.test_request_context("/grid"):
            setattr(g, db_transaction._MODE_KEY, db_transaction._QUERY)  # pylint: disable=protected-access
            with pytest.raises(ZeroDivisionError):
                with db_transaction.write_transaction():
                    db.session.add(PayPeriod(
                        user_id=seed_user["user"].id,
                        start_date=date(2099, 9, 1),
                    ))
                    db.session.flush()
                    raise ZeroDivisionError("the render's write failed")

            assert db_transaction._is_query_request()  # pylint: disable=protected-access

        db.session.rollback()
        assert db.session.query(PayPeriod).filter_by(
            user_id=seed_user["user"].id, start_date=date(2099, 9, 1),
        ).one_or_none() is None


class TestARequestArrivingOnAWrittenTransaction:
    """The precondition, and why it is a precondition rather than a fence."""

    def test_it_refuses_rather_than_discarding_the_writes(
        self, app, auth_client, seed_user, db,
    ):
        """A query cannot be given a snapshot over someone's uncommitted work.

        Unreachable in production -- a request's session is its own -- so what
        it names is a fixture that staged rows and then issued a request
        expecting to see them, which no request can do.  Refusing beats
        discarding: the writes are not this boundary's to throw away.
        """
        db.session.add(PayPeriod(
            user_id=seed_user["user"].id,
            start_date=date(2099, 3, 1),
        ))
        db.session.flush()

        with pytest.raises(db_transaction.UncommittedWriteAtRequestStart):
            auth_client.get("/settings")

        # And it left them alone.  The refusal says the writes are not this
        # boundary's to discard, so the teardown must not discard them one hook
        # later -- which is why the mode is recorded only AFTER the refusal.
        assert db.session.query(PayPeriod).filter_by(
            user_id=seed_user["user"].id, start_date=date(2099, 3, 1),
        ).one_or_none() is not None

    def test_an_unwritten_inherited_transaction_is_simply_ended(
        self, app, auth_client, seed_user, db, observed_modes,
    ):
        """Reading before a request costs nothing; only WRITING is refused.

        The distinction is the whole reason the boundary asks PostgreSQL for
        an assigned transaction id rather than asking whether a transaction is
        open: a read-only transaction holds no work, so ending it loses none.

        **It asserts the MODE, not just a 200.**  An adversarial review caught
        the first version doing the latter: delete the boundary's rollback and
        the request runs inside the inherited transaction with no snapshot and
        no read-only guarantee, and a 200 is still a 200.
        """
        db.session.query(PayPeriod).filter_by(
            user_id=seed_user["user"].id,
        ).count()
        observed_modes.clear()

        assert auth_client.get("/settings").status_code == 200

        assert ("repeatable read", "on") in observed_modes, (
            f"the request did not open a transaction of its own; it inherited "
            f"the one this test body opened. Modes seen: {observed_modes}"
        )


class TestTheDecisionPrecedesTheRequestSFirstStatement:
    """Where the mode is decided, and why anywhere later is not enough.

    The decision has to happen before the request's FIRST statement, and a
    ``before_request`` hook cannot promise that: Flask calls them in
    registration order and ``setup_logging``'s ``_attach_request_id`` is
    registered first -- it reads ``current_user``, which loads the user row.
    While the decision lived in a hook, that load ran at ``READ COMMITTED`` in
    a transaction the boundary then threw away, so a render's real snapshot
    was its SECOND transaction and everything the first one read was outside
    it.  The decision is made on ``request_started`` now, which Flask sends
    before ``preprocess_request``.
    """

    def test_every_transaction_of_a_query_is_one_snapshot(
        self, auth_client, seed_user, db, observed_modes,
    ):
        """Not "some transaction was read-only" -- EVERY one of them was.

        This is the assertion that separates deciding early from deciding in a
        hook, and the reason the sibling above is written the weaker way is
        that it is asking a different question (did the request leave the
        inherited transaction).  Here a single ``("read committed", "off")``
        means some part of this render read outside its own snapshot, which is
        finding **N-353** with a smaller blast radius rather than a different
        defect.

        ``/settings`` deliberately: it holds no :func:`write_transaction`
        block, so every transaction it opens is a query's.  The route that
        does hold one is pinned by
        :meth:`TestWriteTransaction.test_a_grid_render_is_query_command_query`.

        **The rollback below is the setup, not tidying**, and without it this
        test passes against the defect it exists to catch.  It puts the
        session in the state a production request starts in -- no transaction
        open, the user row not loaded -- so that resolving ``current_user``
        is what opens the request's first transaction.  Leave a fixture's
        transaction open instead and the user row is already there, no
        statement is issued for it, and the first transaction anything
        observes is the one opened after the boundary had run either way.
        """
        db.session.rollback()
        observed_modes.clear()

        assert auth_client.get("/settings").status_code == 200

        assert observed_modes, "the GET opened no transaction at all"
        assert all(
            pair == ("repeatable read", "on") for pair in observed_modes
        ), (
            f"a transaction of this render ran at READ COMMITTED, so what it "
            f"read is outside the snapshot the rest of the page is computed "
            f"against. Modes seen, in order: {observed_modes}"
        )

    def test_the_boundary_is_the_only_request_started_receiver(self, app):
        """The ordering claim holds against HOOKS; this is what holds it here.

        ``request_started`` precedes every before-request hook by Flask's own
        dispatch code, which is the guarantee the module rests on.  It says
        nothing about other RECEIVERS of the same signal: blinker's ``send``
        iterates a ``set`` and documents receiver order as undefined.  With one
        receiver there is no order to be undefined -- so the property is a
        COUNT, and this is the arm that keeps it one rather than a census
        somebody re-takes by hand.

        A second receiver that reads the database would put a statement back in
        front of the decision, which is the defect this whole module was
        corrected for, and nothing else in the suite would notice.
        """
        # Pylint: ``protected-access`` -- blinker exposes no public accessor
        # for a signal's receivers, and asserting the COUNT is the property.
        # pylint: disable=protected-access
        receivers = list(request_started.receivers_for(app))

        assert receivers == [db_transaction._open_this_request_s_own_transaction], (
            f"``request_started`` has receivers other than the transaction "
            f"boundary: {receivers}. Blinker does not order them, so whichever "
            f"runs first decides whether the request's mode is set before its "
            f"first statement -- see app/db_transaction.py"
        )

    def test_a_query_binds_no_audit_actor(
        self, auth_client, seed_user, db, observed_statements,
    ):
        """A transaction that cannot write is not told who is acting.

        ``READ ONLY`` refuses every write to an audited table, so no trigger
        in a query's transaction can fire and nothing can read the setting.
        Binding one there is a round trip per render transaction buying a
        value with no reader -- measured at THREE per authenticated GET before
        this: one from the listener on the transaction the user load opened,
        one bound explicitly at that load, and one on the render's own.

        The firing control is
        :meth:`TestACommandNamesItsActor.test_a_command_binds_the_actor`,
        which counts the same statement on a POST and finds it.

        The rollback is the same setup its sibling above explains: it puts the
        session in a production request's starting state, so the transaction
        the user load opens is one of the transactions counted here.
        """
        db.session.rollback()
        observed_statements.clear()

        assert auth_client.get("/settings").status_code == 200

        binds = [sql for sql in observed_statements if _ACTOR_BIND_SQL in sql]
        assert not binds, (
            f"{len(binds)} audit-actor bind(s) were issued during a GET, whose "
            f"transactions are READ ONLY and can fire no audit trigger: {binds}"
        )


class TestACommandNamesItsActor:
    """The half a query does not need, and the audit trail that depends on it."""

    def test_a_command_binds_the_actor(
        self, auth_client, seed_user, observed_statements,
    ):
        """The control that makes the query-side assertion mean something.

        Without it, deleting the bind everywhere would leave both tests green
        and the audit trail anonymous.
        """
        observed_statements.clear()

        auth_client.post("/settings", data={"grid_default_periods": "7"})

        binds = [sql for sql in observed_statements if _ACTOR_BIND_SQL in sql]
        assert binds, (
            "a POST bound no audit actor, so every row it writes lands in "
            "system.audit_log with a NULL user -- silently, because the "
            "trigger reads an unset setting as 'no authenticated user'"
        )

    def test_binding_the_actor_opens_no_transaction_of_its_own(
        self, app, db, seed_user, observed_statements,
    ):
        """It tells the transaction already open, and starts none to tell.

        The distinction is not pedantic.  ``Session.connection()`` BEGINS a
        transaction when none is open, so binding unconditionally makes a
        LOGGING hook start one -- which the listener then binds off ``g``,
        giving that transaction the actor twice and putting the first statement
        of a request's transaction somewhere no reader of the route would look
        for it.  Reachable whenever ``current_user`` resolves without a
        statement, which is wherever the row is loaded and unexpired.

        **Asserted on the transaction rather than on a statement count**,
        because counting binds against transactions cannot work here: a request
        under the test client can INHERIT a transaction whose ``after_begin``
        fired before the count began, so two binds on two transactions and two
        binds on one are indistinguishable to that instrument.  The first
        version of this test was that instrument and reported the defect it was
        written to refute.
        """
        # Read the id BEFORE the rollback below and pass a plain int: the
        # rollback EXPIRES every instance, so `seed_user["user"].id` inside the
        # measured window would refresh the row and open the very transaction
        # this test is asking about -- which is how the first draft of it
        # failed, blaming the code for its own setup.
        owner_id = seed_user["user"].id

        with app.test_request_context("/settings", method="POST"):
            db.session.rollback()
            session = db.session()
            assert not session.in_transaction(), "the setup left one open"
            observed_statements.clear()

            db_transaction.bind_request_actor(owner_id, owner_id)

            assert not session.in_transaction(), (
                "binding the audit actor opened a transaction that nothing "
                "asked for, and the listener will bind it a second time"
            )
            assert not observed_statements, (
                f"binding the actor with no transaction open issued "
                f"{observed_statements}; there was nothing to tell"
            )
            # Through the CONSTANT, not the string: the whole reason
            # ``_ACTOR_KEY`` exists is that this key had two spellings in two
            # modules, and a test that hard-codes the literal is a third.
            # pylint: disable=protected-access
            recorded = getattr(g, db_transaction._ACTOR_KEY, None)
            assert recorded == owner_id, (
                "the actor was not recorded, so no LATER transaction of this "
                "request would be told who is acting"
            )

    def test_an_audited_write_a_command_makes_carries_the_user(
        self, auth_client, seed_user, db,
    ):
        """The behavioural half: the ROW names the owner, not the statement.

        ``auth.user_settings`` is audited, and ``POST /settings`` updates it,
        so this reads the trail the trigger actually wrote rather than
        inferring it from a bind having been issued.
        """
        db.session.execute(text("DELETE FROM system.audit_log"))
        db.session.commit()

        auth_client.post("/settings", data={"grid_default_periods": "9"})

        db.session.rollback()
        actors = db.session.execute(text(
            "select distinct user_id from system.audit_log "
            "where table_name = 'user_settings'"
        )).scalars().all()
        assert actors == [seed_user["user"].id], (
            f"the settings write was attributed to {actors}; a NULL here is "
            f"the audit trail losing the acting user on an ordinary mutation"
        )

    def test_the_actor_does_not_outlive_the_request(
        self, auth_client, seed_user, db,
    ):
        """A write the TEST BODY makes after a request is not that user's.

        ``flask.g`` dies with the request in production, and the ``SET LOCAL``
        this replaced died with the request's transaction -- so neither ever
        attributed a later write to the request's user.  Under the test client
        the app context is shared across a whole test, so an actor left on
        ``g`` would follow the request out and sign every row the test body
        went on to write.  The teardown retires it for the same reason it
        retires the mode.

        What the trail should say for such a row is ``NULL``: nobody acting
        through the application wrote it, which is exactly what
        ``TestAuditTriggerMetadata.test_user_id_is_null_without_middleware``
        pins for a test that issues no request at all.
        """
        auth_client.post("/settings", data={"grid_default_periods": "7"})
        db.session.rollback()
        db.session.execute(text("DELETE FROM system.audit_log"))
        db.session.commit()

        settings = db.session.query(UserSettings).filter_by(
            user_id=seed_user["user"].id,
        ).one()
        settings.grid_default_periods = 11
        db.session.commit()

        actors = db.session.execute(text(
            "select distinct user_id from system.audit_log "
            "where table_name = 'user_settings'"
        )).scalars().all()
        assert actors == [None], (
            f"a row this test body wrote AFTER the request was attributed to "
            f"{actors}; the request's actor outlived the request"
        )
