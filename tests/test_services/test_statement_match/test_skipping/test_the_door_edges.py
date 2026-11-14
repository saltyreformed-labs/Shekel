"""The doors' log lines, an empty submission, and a bounded line that cannot be skipped."""

from datetime import timedelta

from app.models.statement_line_skip import StatementLineSkip
from app.services.statement_match import awaiting_review_count, unskip_line
from app.services.statement_match._undisposed import skipped_among

from tests.test_services.test_statement_match._builders import (
    a_bank_line,
    an_import,
    an_unexplained_outflow,
)

from ._helpers import _owner, _skip


class TestTheDoorsAreLogged:
    """A decision about money the bank moved leaves a structured event."""

    def test_recording_one_emits_its_event(self, app, db, seed_user, caplog):
        """``statement_line_skipped``, carrying the line and the act."""
        line = an_unexplained_outflow(seed_user)
        db.session.flush()

        with caplog.at_level("INFO"):
            recorded = _skip(seed_user, line)

        emitted = [
            record for record in caplog.records
            if getattr(record, "event", None) == "statement_line_skipped"
        ]
        assert len(emitted) == 1
        assert emitted[0].line_id == line.id
        assert emitted[0].skip_id == recorded.skip_id

    def test_a_repeat_emits_NOTHING(self, app, db, seed_user, caplog):
        """It wrote nothing, so it says nothing.

        An event asserting a skip was recorded, emitted for a call that
        recorded none, would make "how many lines did they skip" unanswerable
        from the log -- which is the whole reason these events exist.
        """
        line = an_unexplained_outflow(seed_user)
        db.session.flush()
        _skip(seed_user, line)

        with caplog.at_level("INFO"):
            caplog.clear()
            _skip(seed_user, line)

        assert [
            record for record in caplog.records
            if getattr(record, "event", None) == "statement_line_skipped"
        ] == []

    def test_undoing_one_emits_its_own_event(
        self, app, db, seed_user, caplog,
    ):
        """``statement_line_unskipped``, its own event and not a direction."""
        line = an_unexplained_outflow(seed_user)
        db.session.flush()
        recorded = _skip(seed_user, line)
        owner_id, account_id = _owner(seed_user)

        with caplog.at_level("INFO"):
            caplog.clear()
            unskip_line(recorded.skip_id, owner_id, account_id)

        emitted = [
            record for record in caplog.records
            if getattr(record, "event", None) == "statement_line_unskipped"
        ]
        assert len(emitted) == 1
        assert emitted[0].line_id == line.id


class TestSkippedAmongAnswersAnEmptySubmission:
    """The early return, which no door exercises and every door reaches.

    :func:`~app.services.statement_match._undisposed.skipped_among`
    short-circuits an empty ``line_ids`` rather than issuing ``IN ()``.  Every
    caller today passes at least one id, so the branch is unreached by the
    cases above -- and an untested branch on a refusal path is one that could
    return the wrong thing and stay green.
    """

    def test_an_empty_submission_answers_an_empty_set(
        self, app, db, seed_user,
    ):
        """Empty in, empty out, with no query issued and no refusal raised."""
        line = an_unexplained_outflow(seed_user)
        db.session.flush()
        _skip(seed_user, line)

        assert skipped_among(frozenset(), seed_user["account"].id) == frozenset()

    def test_it_answers_only_the_ids_it_was_ASKED_about(
        self, app, db, seed_user,
    ):
        """The narrowing, which the empty case cannot see.

        Two lines, one skipped: asking about the OTHER must come back empty,
        and asking about both must name only the skipped one.  A reader that
        ignored ``line_ids`` and returned the account's whole skip set would
        pass the empty case and fail here.
        """
        skipped_line = an_unexplained_outflow(seed_user)
        # A DIFFERENT amount, because a line's identity is
        # ``(account, posted_on, amount, sequence_in_group)`` and both builders
        # post on the bootstrap day at ordinal 0.
        other = an_unexplained_outflow(
            seed_user, merchant="Walmart", amount="-18.06",
        )
        db.session.flush()
        _skip(seed_user, skipped_line)
        account_id = seed_user["account"].id

        assert skipped_among(frozenset({other.id}), account_id) == frozenset()
        assert skipped_among(
            frozenset({skipped_line.id, other.id}), account_id,
        ) == frozenset({skipped_line.id})


class TestABoundedLineCannotBeSkipped:
    """The two DAY bounds hold ahead of this door, and stay the pass's job.

    A line before the pay calendar opens, or one the account's opening equity
    already accounts for, reaches no card -- so nothing offers a skip for it.
    The door does NOT re-state those bounds, and that is the right split: they
    are facts about what the pass may act on, and re-spelling them here would
    be a second place for them to be wrong.  What IS asserted is the
    consequence: skipping such a line changes no count, because it was in none.
    """

    def test_skipping_a_pre_calendar_line_changes_no_count(
        self, app, db, seed_user,
    ):
        """It was never counted, so it cannot stop being counted.

        The assertion is the pair: the badge reads 0 before AND after, so a
        door that had somehow made the line visible would fail here rather
        than in whichever screen later rendered it.
        """
        opens = seed_user["bootstrap_period"].start_date
        line = a_bank_line(
            seed_user, an_import(seed_user),
            posted_on=opens - timedelta(days=1),
        )
        db.session.flush()
        account_id = seed_user["account"].id
        assert awaiting_review_count(
            seed_user["user"].id, account_id, opens,
        ) == 0

        _skip(seed_user, line)

        assert awaiting_review_count(
            seed_user["user"].id, account_id, opens,
        ) == 0
        assert db.session.query(StatementLineSkip).count() == 1
