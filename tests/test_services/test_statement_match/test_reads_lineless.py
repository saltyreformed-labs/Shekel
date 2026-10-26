"""A match that lost its bank lines does not take the review screen down.

Plan step **bank_import:X-f6a-4**.  The state needs a code defect to reach --
``record_match`` refuses an empty side at the one writer,
``fk_statement_match_members_line_account`` refuses to remove a line a match
names, and migration ``e4a7c0f13b92`` deleted the acts that already held none
-- and what happens if it is reached anyway was measured by two adversarial
reviews 2026-08-20 rather than argued.

**Skipping it silently was the original defect** (invisible, and its rows stay
claimed).  **Raising takes the whole review surface down for that account**,
including the release control that would repair it, which is the rule
``_accepted_view._accepted_row`` already states in as many words for its own
degraded case.  So the reader degrades and the operator is told at ERROR.

**WHERE each half lives moved at plan step ``bank_import:X-gj-1c``**, finding
**N-389**, and the two halves are still both here.  The skip was a Python
guard inside the fold, which is the same invariant spelled a second time in a
language the COUNTS read could not share -- so a caption derived from the
table and cards derived from the fold disagreed by one for every such act.
:data:`~app.services.statement_match._acts.NAMES_A_BANK_LINE` states it once
in SQL: the loader narrows on it, so the fold cannot receive one and its guard
is deleted; and :func:`~app.services.statement_match._accepted_view
.accepted_counts` narrows and ALARMS on it, which is the read the Reconcile
page performs on every render whichever tab is open.  The equality that motion
bought is graded in ``test_reconcile.TestACaptionCountsOnlyWhatItsTabCanDraw``.
"""

from app.services.statement_match._accepted_view import (  # pylint: disable=protected-access
    accepted_counts,
    accepted_groups,
)

from app.services.statement_match._release import (  # pylint: disable=protected-access
    acts_of,
)

from ._builders import a_bank_line, a_transaction, an_import


def _planted_lineless(db, seed_user):
    """Stage an accepted match and then take ITS bank-line member away.

    Through raw SQL, because the schema exists to make this unreachable and the
    ORM would rightly refuse to help.

    **Scoped to the act it just made, and it was not** (plan step
    ``bank_import:X-gj-1c``).  The delete named every member row on the
    DATABASE carrying a line, which is harmless for a caller whose account
    holds exactly one act and wrong for any other: the first case to stage a
    REAL act beside the planted one -- which is the only way to grade that a
    caption equals what its tab draws -- had both of them stripped, and the
    numbers agreed for the wrong reason.  A helper that quietly widens its
    subject is the shape this package refuses in production code.

    Args:
        db: The session fixture.
        seed_user: The seeded user bundle.

    Returns:
        The transaction the act still claims.
    """
    from app.services import statement_match
    from ._builders import a_scope, a_submission

    statement = an_import(seed_user)
    line = a_bank_line(seed_user, statement, amount="-180.00")
    txn = a_transaction(seed_user, amount="180.00")
    scope = a_scope(seed_user)
    accepted = statement_match.accept_match(
        a_submission(scope, lines=[line], transactions=[txn]),
        scope,
    )
    db.session.flush()
    db.session.execute(
        db.text(
            "DELETE FROM budget.statement_match_members"
            " WHERE bank_statement_line_id IS NOT NULL"
            " AND match_id = :match_id"
        ),
        {"match_id": accepted.match_id},
    )
    db.session.flush()
    db.session.expire_all()
    return txn


class TestALinelessActDoesNotBreakTheScreen:
    """The reader degrades; it does not raise and it does not stay silent."""

    def test_the_reader_answers_rather_than_raising(
        self, app, db, seed_user,
    ):
        """FIRING CONTROL: a bare ``max()`` on the empty side used to raise.

        ``accepted_groups`` feeds ``review_set``, which renders the review GET
        and the response to every apply-POST -- so one such act took the whole
        reconcile surface down for the account, with the release button that
        would repair it rendered by the function that raised.
        """
        _planted_lineless(db, seed_user)

        assert accepted_groups(
            seed_user["user"].id, seed_user["account"].id,
        ) == []

    def test_it_is_LOGGED_at_error_by_the_read_that_COUNTS_them(
        self, app, db, seed_user, caplog,
    ):
        """Skipping silently was the original defect; this is what replaces it.

        The operator is told, because the act still holds its transactions out
        of every later match and nothing on screen can show it.

        **This case asked it of ``accepted_groups`` until plan step
        ``bank_import:X-gj-1c``**, and the alarm moved with the predicate: the
        fold no longer RECEIVES such an act, so a fold that logged would be a
        fold checking for something its own loader excluded.  The counts read
        is the one that still sees the whole set, in the same aggregate that
        narrows the caption, and it runs on every render of the page that shows
        both numbers.
        """
        import logging

        _planted_lineless(db, seed_user)

        with caplog.at_level(logging.ERROR):
            accepted_counts(seed_user["user"].id, seed_user["account"].id)

        raised = [
            record for record in caplog.records
            if record.__dict__.get("event") == "statement_match_lineless"
        ]

        assert raised, "the operator was told nothing"
        assert raised[0].__dict__.get("lineless_count") == 1
        # THE ID IS THE POINT.  Nothing on any screen can show such an act and
        # no release control exists for it, so the log is the only route to
        # the row -- and a first version of this alarm carried the count
        # alone, which tells an operator how many rows they cannot find.
        assert raised[0].__dict__.get("match_ids") == [
            db.session.execute(
                db.text(
                    "SELECT id FROM budget.statement_matches"
                    " WHERE account_id = :account_id"
                ),
                {"account_id": seed_user["account"].id},
            ).scalar_one(),
        ]

    def test_the_FOLD_no_longer_needs_a_guard_of_its_own(
        self, app, db, seed_user,
    ):
        """The loader's clause is what makes the fold's arm unnecessary.

        A bare ``max()`` over an act's empty bank side raised, and a Python
        skip beside it was the second spelling finding **N-389** is about.
        Neither is here now: what holds is that the act never reaches the fold,
        which this asks of the loader directly rather than of the fold's
        output alone.
        """
        _planted_lineless(db, seed_user)

        assert acts_of(
            seed_user["user"].id, seed_user["account"].id,
        ) == []
