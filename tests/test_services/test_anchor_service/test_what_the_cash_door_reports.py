"""What the cash door reports (rulings R-CC79 and R-CC85): the governing assertion either side of
its write, the stager's read of the day's own record, and a grid save's one read per fact.
"""

import inspect
from datetime import timedelta
from decimal import Decimal

import sqlalchemy as sa
from sqlalchemy import event

from app.extensions import db
from app.models.account import Account, AccountAnchorHistory
from app.services import anchor_service, pay_period_service
from app.services.anchor_service import AnchorTrueUpOutcome, apply_anchor_true_up
from app.services.cash_ledger import _facts as cash_ledger_facts
from app.services.user_write_lock import _USER_WRITE_LOCK_NAMESPACE
from app.utils.dates import display_today
from tests._test_helpers import current_pay_period
from app.services import cash_ledger

from ._helpers import _make_checking_account, _make_projected_expense_with_past_dated_entry


# ── The cash door's report (ruling R-CC79) ───────────────────────────


def _spy_governing_reads(monkeypatch, probe=None):
    """Record every read of the governing assertion: who asked, as of when.

    Wraps ``cash_ledger._facts._governing_row`` -- the ONE query behind
    ``resolve_anchor``, ``governing_anchor`` and ``governing_anchor_on`` -- so
    this counts READS of the governing assertion whichever reader spelled
    them, not SQL statements (a refresh or an eager load moves those without
    moving what is asked).

    Args:
        monkeypatch: The test's ``monkeypatch``; it restores the query.
        probe: An optional zero-argument callable run at each read, its answer
            recorded beside it -- how a case asks what held at that instant.

    Returns:
        The list each read appends ``(caller, horizon, probed)`` to: the
        function that called the reader, the horizon (``None`` is "what
        governs now", a day is ``governing_anchor_on``'s), and the probe's
        answer or ``None``.
    """
    real = cash_ledger_facts._governing_row
    reads = []

    def spy(account_id, on_or_before):
        """Note the caller and horizon, run the probe, then ask the real query."""
        caller = inspect.currentframe().f_back.f_back.f_code.co_name
        reads.append((caller, on_or_before, None if probe is None else probe()))
        return real(account_id, on_or_before)

    monkeypatch.setattr(cash_ledger_facts, "_governing_row", spy)
    return reads


def _statements_after_the_commit(fn):
    """Run *fn*; return its result and the SQL emitted after its first COMMIT.

    One timeline of the engine's statements and its commits, so "after the
    write door committed" is a position in it rather than an inference.

    Args:
        fn: A zero-argument callable to run under capture.

    Returns:
        ``(result, statements)`` -- *fn*'s return value and the text of every
        statement emitted after the first commit; the commit itself is
        asserted to have happened, so an empty list cannot mean "no commit".
    """
    commit = object()
    timeline = []

    def on_statement(_conn, _cursor, statement, *_args, **_kwargs):
        """Append the statement's text."""
        timeline.append(statement)

    def on_commit(_conn):
        """Append the commit marker."""
        timeline.append(commit)

    event.listen(db.engine, "before_cursor_execute", on_statement)
    event.listen(db.engine, "commit", on_commit)
    try:
        result = fn()
    finally:
        event.remove(db.engine, "before_cursor_execute", on_statement)
        event.remove(db.engine, "commit", on_commit)
    assert commit in timeline, "no commit was seen, so no window was graded"
    return result, timeline[timeline.index(commit) + 1:]


def _holds_owner_lock(user_id):
    """Return whether THIS session holds the owner's write lock right now.

    Asked of ``pg_locks`` rather than inferred from statement order: the
    two-key ``pg_advisory_xact_lock(namespace, user_id)`` form shows as
    ``classid`` / ``objid`` with ``objsubid = 2``.
    """
    return db.session.execute(
        sa.text(
            "SELECT count(*) FROM pg_locks WHERE locktype = 'advisory' "
            "AND pid = pg_backend_pid() AND granted AND objsubid = 2 "
            "AND classid::bigint = :namespace AND objid::bigint = :user_id"
        ),
        {"namespace": _USER_WRITE_LOCK_NAMESPACE, "user_id": user_id},
    ).scalar() > 0


class TestTheCashDoorReportsWhatGovernsEitherSide:
    """Ruling R-CC79: the door reports the governing assertion before and after.

    It returned the bare outcome until then, so the route asked the ledger
    itself what the door had decided -- once before the write, outside the
    owner's lock, and twice after it.  The report carries both records, read
    inside the lock; these grade its three shapes with hand-computed figures.
    Every day is derived backward from ``display_today()`` and checked against
    the schedule's floor, as ``test_the_day_an_assertion_is_for.py``'s
    ``TestBackDatedCashTrueUp`` does.
    """

    @staticmethod
    def _days_back(seed_user, *offsets):
        """Return ``today - offset`` for each offset, asserting each is assertable."""
        today = display_today()
        days = tuple(today - timedelta(days=offset) for offset in offsets)
        floor = pay_period_service.earliest_recordable_day(seed_user["user"].id)
        assert floor <= min(days), (
            f"the schedule starts {floor}; these cases need {min(days)}"
        )
        return days

    def test_a_save_for_today_reports_the_old_figure_and_the_new_one(
        self, app, db, seed_user, seed_periods_today,
    ):
        """$1,000.00 asserted 10 days ago; $1,200.00 saved for today.

        Hand-computed: ``governing_before`` is the $1,000.00 opening dated 10
        days ago, ``governing_after`` the $1,200.00 just recorded, dated today,
        and the outcome COMMITTED.  The after is the very record a reader
        resolves once the save has landed -- anchor id and recording instant
        included -- so what the grid draws from it is what every screen reads.
        """
        with app.app_context():
            (opened,) = self._days_back(seed_user, 10)
            account = _make_checking_account(
                seed_user, anchor_balance="1000.00", observed_on=opened,
            )
            db.session.commit()
            opening_id = cash_ledger.resolve_anchor(account).anchor_id

            report = apply_anchor_true_up(
                account=account, new_balance=Decimal("1200.00"),
            )

            assert report.outcome is AnchorTrueUpOutcome.COMMITTED
            before, after = report.governing_before, report.governing_after
            assert (before.anchor_id, before.balance, before.observed_on) == (
                opening_id, Decimal("1000.00"), opened,
            )
            assert (after.balance, after.observed_on) == (
                Decimal("1200.00"), display_today(),
            )
            assert after.anchor_id != opening_id
            db.session.expire_all()
            assert after == cash_ledger.resolve_anchor(
                db.session.get(Account, account.id),
            )

    def test_re_saving_what_governs_is_unchanged_and_reads_no_after(
        self, app, db, monkeypatch, seed_user, seed_periods_today,
    ):
        """Re-save the governing $1,000.00 for its own day: nothing is written.

        UNCHANGED (ruling R-EQ), and ``governing_after`` IS
        ``governing_before`` -- the same object, with exactly ONE read behind
        the whole call: the stager's read of the latest assertion, which is
        dated on the submitted day and so IS the record governing it (ruling
        R-CC85).  It was two until then, the door reading today's before
        itself.  A second read now would be the day's record re-asked for, or
        an after the door re-read after writing nothing.  And the session
        holds no owner's lock at the door's rollback, asked just before it
        runs: since plan step ``balance:X-bn`` no service takes the lock (the
        request's transaction does), so one re-added in the door or the stager
        turns this red.  Asked after the call, as it was until the developer's
        rule-5 answer (2026-09-29, "Move inside the step"), it could not: the
        rollback releases every lock its transaction took (measured
        2026-09-29 with one re-added at the door's top).  *Until plan step
        ``balance:X-bn`` this said the rollback is what releases the lock the
        stager took.*
        """
        with app.app_context():
            (opened,) = self._days_back(seed_user, 10)
            account = _make_checking_account(
                seed_user, anchor_balance="1000.00", observed_on=opened,
            )
            db.session.commit()
            rows = db.session.query(AccountAnchorHistory).filter_by(
                account_id=account.id,
            ).count()
            # Read BEFORE the call: its rollback expires the account.
            user_id = account.user_id
            reads = _spy_governing_reads(monkeypatch)
            held_at_rollback = []
            rollback = type(db.session).rollback

            def probed_rollback(session):
                """Ask whether the owner's key is held, then roll back."""
                held_at_rollback.append(_holds_owner_lock(user_id))
                return rollback(session)

            monkeypatch.setattr(type(db.session), "rollback", probed_rollback)

            report = apply_anchor_true_up(
                account=account, new_balance=Decimal("1000.00"),
                observed_on=opened,
            )

            assert report.outcome is AnchorTrueUpOutcome.UNCHANGED
            # ONE rollback, the door's, so the lock check below measured the
            # moment it names.
            assert len(held_at_rollback) == 1, (
                f"the UNCHANGED save rolled back {len(held_at_rollback)} times "
                f"through the session, where the door rolls back once; the "
                f"lock check below grades nothing unless it is exactly one"
            )
            assert held_at_rollback == [False], (
                "the owner's write lock was held at the UNCHANGED save's "
                "rollback, so a service took it again"
            )
            assert report.governing_after is report.governing_before
            assert (
                report.governing_before.balance,
                report.governing_before.observed_on,
            ) == (Decimal("1000.00"), opened)
            assert [(caller, horizon) for caller, horizon, _ in reads] == [
                ("stage_anchor_true_up", None),
            ]
            assert db.session.query(AccountAnchorHistory).filter_by(
                account_id=account.id,
            ).count() == rows

    def test_a_back_dated_save_writes_but_leaves_today_as_it_was(
        self, app, db, seed_user, seed_periods_today,
    ):
        """$1,000.00 at O, $900.00 at O+20; then $250.00 back-dated to O+10.

        Hand-computed, O being 30 days ago: the $250.00 row is appended for
        its own day, so the outcome is COMMITTED -- but an assertion for an
        earlier day does not govern today.  The $900.00 at O+20 governs on
        both sides, so ``governing_after`` equals ``governing_before``: two
        reads, one record.
        """
        with app.app_context():
            opening, back_dated, later = self._days_back(seed_user, 30, 20, 10)
            account = _make_checking_account(
                seed_user, anchor_balance="1000.00", observed_on=opening,
            )
            db.session.commit()
            apply_anchor_true_up(
                account=account, new_balance=Decimal("900.00"),
                observed_on=later,
            )

            report = apply_anchor_true_up(
                account=account, new_balance=Decimal("250.00"),
                observed_on=back_dated,
            )

            assert report.outcome is AnchorTrueUpOutcome.COMMITTED
            assert report.governing_after == report.governing_before
            assert (
                report.governing_before.balance,
                report.governing_before.observed_on,
            ) == (Decimal("900.00"), later)
            assert db.session.query(AccountAnchorHistory).filter_by(
                account_id=account.id, observed_on=back_dated,
            ).one().anchor_balance == Decimal("250.00")

    def test_every_read_the_door_makes_holds_the_owners_lock(
        self, app, auth_client, db, monkeypatch, seed_user, seed_periods_today,
    ):
        """The stager's read follows the lock; the after-read precedes the commit.

        Asked of PostgreSQL at the instant of each read, by a probe run inside
        the reader: does this session hold the owner's advisory key?  Since
        plan step ``balance:X-bn`` neither the door nor the stager takes it
        -- the signed-in request's command transaction takes it where it
        begins (:mod:`app.db_transaction`) -- so the save is driven through
        its route, ``PATCH /accounts/<id>/true-up``.  What this grades is that
        SOME acquisition on this session precedes both reads; the probe cannot
        tell who took it.  That it is the REQUEST's is graded elsewhere:
        ``tests/test_arch/test_the_owner_lock_has_one_home.py`` refuses a
        service that takes one, and
        ``tests/test_services/test_user_write_lock.py`` turns red when the
        request's is removed.  The stager's read of the latest assertion -- the
        report's before since ruling R-CC85 -- holding it proves the lock came
        first; the after-read holding it proves it came before the commit,
        which releases the key.  The probe answers ``False`` before
        the request and after it, so it can.  *Until that step this called
        :func:`apply_anchor_true_up` directly and graded the stager's own
        acquisition.*
        """
        with app.app_context():
            account_id = seed_user["account"].id
            user_id = seed_user["user"].id
            assert not _holds_owner_lock(user_id)
            reads = _spy_governing_reads(
                monkeypatch, probe=lambda: _holds_owner_lock(user_id),
            )

            response = auth_client.patch(
                f"/accounts/{account_id}/true-up",
                data={"anchor_balance": "1750.00"},
            )

            assert response.status_code == 200, response.status_code
            assert [
                read for read in reads
                if read[0] in ("stage_anchor_true_up", "apply_anchor_true_up")
            ] == [
                ("stage_anchor_true_up", None, True),
                ("apply_anchor_true_up", None, True),
            ]
            assert not _holds_owner_lock(user_id)


class TestTheStagerReadsTheDaysRecordOnlyBeforeTheLatest:
    """Ruling R-CC85, at the stager: the latest record once, the day's when earlier.

    ``stage_anchor_true_up`` reads the latest assertion and hands it back.
    That record IS the one governing any day on or after its own (the proof is
    in the stager's docstring), so only a day BEFORE it reads the day's record
    separately -- and then that separate read, not the latest, is what ruling
    R-EQ compares against.  Graded on the stager directly, so the read list is
    the stager's alone.
    """

    def test_a_day_before_the_latest_is_compared_against_its_own_record(
        self, app, db, monkeypatch, seed_user, seed_periods_today,
    ):
        """$1,000.00 at O and $900.00 at O+20; stage $1,000.00 for O+5, then O.

        Hand-computed, O being 30 days ago.  The latest assertion is the
        $900.00 at O+20, later than both submitted days, so each call reads
        the latest and then the day's own record -- two reads, both the
        stager's -- and hands the latest back either way.

        * **$1,000.00 for O+5 is STAGED.**  The record governing O+5 is the O
          row, (O, $1,000.00), and ruling R-EQ declines only a submission
          equal to it in day AND balance ("the day included", the module's
          words), so (O+5, $1,000.00) is a new assertion: it moves the day the
          fold resets at from O to O+5.  An equal balance is not enough.
        * **$1,000.00 for O is UNCHANGED.**  The record governing O is the O
          row itself, equal in both.  This is the arm the separate read
          exists for: compared against the latest, (O+20, $900.00), no
          back-dated re-save could ever be equal, and each would append a
          duplicate (the defect ``test_the_day_an_assertion_is_for.py``'s
          ``TestBackDatedCashTrueUp`` grades through the door).

        Each call is rolled back, so nothing is written and the second arm
        reads what the first did.  Before each rollback, inside the stager's
        transaction, the session holds no owner's lock: since plan step
        ``balance:X-bn`` no service takes it, so one re-added in the stager
        turns this red.  The check followed the last rollback until the
        developer's rule-5 answer (2026-09-29, "Move inside the step"), where
        it could not: the rollbacks release every lock their transaction took
        (measured 2026-09-29 with one re-added at the stager's top).  *Until
        plan step ``balance:X-bn`` this said each rollback releases the lock
        the stager took.*
        """
        with app.app_context():
            user_id = seed_user["user"].id
            opening = display_today() - timedelta(days=30)
            at_o_plus_5 = opening + timedelta(days=5)
            at_o_plus_20 = opening + timedelta(days=20)
            floor = pay_period_service.earliest_recordable_day(user_id)
            assert floor <= opening, (
                f"the schedule starts {floor}; this case needs {opening}"
            )
            account = _make_checking_account(
                seed_user, anchor_balance="1000.00", observed_on=opening,
            )
            db.session.commit()
            apply_anchor_true_up(
                account=account, new_balance=Decimal("900.00"),
                observed_on=at_o_plus_20,
            )
            latest = cash_ledger.resolve_anchor(account)
            assert (latest.balance, latest.observed_on) == (
                Decimal("900.00"), at_o_plus_20,
            )
            rows = db.session.query(AccountAnchorHistory).filter_by(
                account_id=account.id,
            ).count()
            reads = _spy_governing_reads(monkeypatch)

            def stage_1000_for(day):
                """Stage $1,000.00 for *day*; return the report and the row staged."""
                reads.clear()
                staging = anchor_service.stage_anchor_true_up(
                    account=account, new_balance=Decimal("1000.00"),
                    observed_on=anchor_service.resolve_observation_day(
                        user_id, day,
                    ),
                )
                staged_rows = [
                    (row.observed_on, row.anchor_balance)
                    for row in db.session.new
                    if isinstance(row, AccountAnchorHistory)
                ]
                assert not _holds_owner_lock(user_id), (
                    "the owner's write lock was held before the stage's "
                    "rollback, so the stager took it again"
                )
                db.session.rollback()
                return staging, staged_rows

            staging, staged_rows = stage_1000_for(at_o_plus_5)
            assert [(caller, horizon) for caller, horizon, _ in reads] == [
                ("stage_anchor_true_up", None),
                ("stage_anchor_true_up", at_o_plus_5),
            ]
            assert staging.staged is True
            assert staged_rows == [(at_o_plus_5, Decimal("1000.00"))]
            assert staging.latest == latest

            staging, staged_rows = stage_1000_for(opening)
            assert [(caller, horizon) for caller, horizon, _ in reads] == [
                ("stage_anchor_true_up", None),
                ("stage_anchor_true_up", opening),
            ]
            assert staging.staged is False, (
                "the re-save of the O statement was compared against the "
                "LATEST ($900.00 at O+20), which it can never equal"
            )
            assert staged_rows == []
            assert staging.latest == latest

            assert db.session.query(AccountAnchorHistory).filter_by(
                account_id=account.id,
            ).count() == rows


class TestAGridSaveReadsTheGoverningAssertionOncePerFact:
    """R-CC79's and R-CC85's count, through the route: one read per fact.

    Before ruling R-CC79 a back-dated grid save read the governing assertion
    four times -- the route before the write (outside the lock), the stager for
    the submitted day, the response after the commit, and the grid's own draw
    -- and a save for today five, the reconcile prompt asking once more.  That
    ruling had the door read today's before and after under its lock, and the
    draw, the prompt and the coverage boundary all take the after, which left
    three reads on both paths.  **Ruling R-CC85 took the third off a save for
    today**: the door's before and the stager's read of the submitted day were
    the same row whenever the day is on or after the latest assertion's, so the
    stager reads the latest once and hands it back.  A save for today reads
    TWO facts -- the latest (the before, and the record governing today) and
    the after -- and a back-dated save THREE, the day's own record being a
    different fact.  Counted as calls to the one query behind every reader
    (:func:`_spy_governing_reads`) and named by caller, so a read that returns
    is seen by WHO asks as well as by how many.  The boundary's own query
    (``cash_ledger.reconciled_through``) goes through no reader, so the last
    case here grades it by the SQL a request emits.
    """

    @staticmethod
    def _grid_save(auth_client, account_id, day):
        """PATCH the grid editor's save for *day*, as its form submits it."""
        return auth_client.patch(
            f"/accounts/{account_id}/true-up",
            data={"anchor_balance": "1234.56", "observed_on": day.isoformat()},
            headers={"HX-Request": "true"},
        )

    def test_a_back_dated_grid_save_reads_three_facts(
        self, app, auth_client, monkeypatch, seed_user, seed_periods_today,
    ):
        """The latest, the submitted day's record, today's after: three.

        A $1,100.00 assertion for today stands first, so a save back-dated
        five days is not the coverage boundary and the reconcile prompt is not
        asked (the case below is the one that asks it).  It is also what makes
        the save BACK-DATED in ruling R-CC85's sense: the latest assertion is
        later than the submitted day, so the stager reads that day's record for
        itself -- the second read, both by the stager.
        """
        with app.app_context():
            apply_anchor_true_up(
                account=db.session.get(Account, seed_user["account"].id),
                new_balance=Decimal("1100.00"),
            )
            day = display_today() - timedelta(days=5)
            reads = _spy_governing_reads(monkeypatch)

            resp = self._grid_save(auth_client, seed_user["account"].id, day)

            assert resp.status_code == 200, resp.data[:300]
            assert [(caller, horizon) for caller, horizon, _ in reads] == [
                ("stage_anchor_true_up", None),
                ("stage_anchor_true_up", day),
                ("apply_anchor_true_up", None),
            ]

    def test_a_grid_save_for_today_reads_two_facts(
        self, app, auth_client, monkeypatch, seed_user, seed_periods_today,
    ):
        """The ordinary save -- today, as the editor prefills -- reads TWICE.

        The stager's read of the latest assertion, which is dated on or before
        today and so IS the record governing the submitted day (ruling
        R-CC85), and the door's after.  It read three times until that ruling,
        the door asking for the latest itself one statement before the stager.

        A save for today IS the coverage boundary, so the reconcile prompt is
        asked, and an outstanding $50.00 purchase makes it render.  It
        reconciles against the report's after rather than asking the ledger
        (``reconcile.governing_statement``) once more, which it did until
        R-CC79 was finished.
        """
        with app.app_context():
            _make_projected_expense_with_past_dated_entry(
                seed_user, current_pay_period(seed_user["user"].id),
                amount="50.00",
            )
            day = display_today()
            reads = _spy_governing_reads(monkeypatch)

            resp = self._grid_save(auth_client, seed_user["account"].id, day)

            assert resp.status_code == 200, resp.data[:300]
            assert 'id="reconcileModal"' in resp.data.decode(), (
                "the prompt did not render, so this case graded no prompt"
            )
            assert [(caller, horizon) for caller, horizon, _ in reads] == [
                ("stage_anchor_true_up", None),
                ("apply_anchor_true_up", None),
            ]

    def test_nothing_after_the_commit_reads_an_assertion(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """The prompt-or-note decision reads the report, not the ledger.

        Everything that decides between the reconcile prompt and the
        acknowledgement -- the coverage boundary, the assertion the prompt
        reconciles against, and the governing balance either side -- is the
        write door's report, read under the owner's lock.  So once the door
        has committed, the request emits no statement against
        ``account_anchor_history`` at all, whichever query would spell it:
        ``reconciled_through``'s ``MAX`` goes through no reader, so the SQL
        timeline is what sees it.  The prompt renders here, so its build is
        inside the window graded.
        """
        with app.app_context():
            _make_projected_expense_with_past_dated_entry(
                seed_user, current_pay_period(seed_user["user"].id),
                amount="50.00",
            )

            resp, after_commit = _statements_after_the_commit(
                lambda: self._grid_save(
                    auth_client, seed_user["account"].id, display_today(),
                ),
            )

            assert resp.status_code == 200, resp.data[:300]
            assert 'id="reconcileModal"' in resp.data.decode()
            assert after_commit, "the request emitted nothing after its commit"
            assert not [
                text for text in after_commit
                if "account_anchor_history" in text
            ], "a statement after the commit read the assertion history"
