"""A matched bank line bounds the books too."""

from datetime import timedelta
from decimal import Decimal

import pytest
import sqlalchemy as sa

from app.enums import AccountOpeningSourceEnum

from app.exceptions import ValidationError
from app.extensions import db as _db
from app.models.account_opening import AccountOpening
from app import ref_cache
from app.services.cash_ledger import (
    earliest_matched_line_day,
    earliest_recorded_movement_day,
    reject_books_open_on_or_after_matched_lines,
)
from tests._test_helpers import refused_by_database_rule

from ._helpers import _an_account_with_no_movement, _match_a_group


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

        Raw SQL on purpose, exactly as ``test_the_database_trigger.py``'s
        :class:`TestTheDatabaseSeesWhatTheOrmCannot` argues: an ORM call
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
