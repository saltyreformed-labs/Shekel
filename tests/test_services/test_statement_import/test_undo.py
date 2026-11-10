"""The UNDO door: a recorded import stops being permanent.

Plan step **bank_import:X-f6a-4**, finding **N-302**.  Recording what a bank
said is append-only everywhere else, which left every refusal TERMINAL: a
restated line, or a first import that named the wrong Shekel account, ended
that account's ability to import at all, while the refusal's own message
promised a repair nothing in ``app/`` could perform.

**Four properties are what this door is FOR, and all four are graded here:**

* it removes the import and the lines IT first recorded, and nothing else --
  and it SAYS how many of the owner's own SKIP decisions went with those lines
  (plan step ``bank_import:X-gj-4a``), because a destructive act that removes
  answers without saying how many is the shape every other figure on this
  receipt exists to refuse;
* it RELEASES every match naming one of those lines rather than letting the
  database shred the membership -- and the settle days those matches wrote
  STAY, because they are the app's own record;
* it forgets the source-account pairing exactly when the last import from that
  source goes, which is what makes a wrongly-recorded pairing repairable;
* after it runs, the account can import again -- the round trip that closes
  N-302, asserted end to end rather than inferred from the parts.

The refusals are FIRING CONTROLS: an unknown id and another owner's import are
states no ordinary use produces, so each is planted and its refusal asserted.
"""

from datetime import date, datetime, timezone

import pytest

from app import ref_cache
from app.enums import StatementSourceEnum, StatusEnum
from app.exceptions import ValidationError
from app.models.statement_import import (
    AccountExternalIdentity,
    BankStatementLine,
    StatementImport,
    StatementLineSighting,
)
from app.models.merchant import Merchant
from app.models.merchant_rule import MerchantRule
from app.models.statement_match import StatementMatch, StatementMatchMember
from app.services import statement_match
from app.services.statement_import import (
    StatementLineConflict,
    delete_import,
    record_statement,
)
from app.services.statement_import._balance import bank_levels
from app.services.statement_match import matched_subjects

from tests.test_services.test_statement_match._builders import (
    a_bank_line,
    a_scope,
    a_sighting,
    a_submission,
    a_transaction,
    an_import,
)

from . import _csv_builder as build
from .test_record import _second_account

_SOURCE = StatementSourceEnum.SECU_CHECKING_CSV

_ENTRIES = [
    (date(2026, 3, 2), "-25.00",
     "POINT OF SALE DEBIT L340 COFFEE (Big Cheese Clayton)"),
    (date(2026, 3, 4), "-40.81",
     "POINT OF SALE DEBIT L340 FOOD LION (Food Lion)"),
]


def _file(entries=None, start="100.00"):
    """Return a well-formed payload over *entries*."""
    return build.build(build.chained(start, entries or _ENTRIES))


def _import(seed_user, payload=None, file_name="statement.csv"):
    """Record a real statement through the real door."""
    return record_statement(
        account_id=seed_user["account"].id,
        user_id=seed_user["user"].id,
        source=_SOURCE,
        file_name=file_name,
        payload=payload if payload is not None else _file(),
    )


def _undo(seed_user, import_id):
    """Run the undo door for the seeded user's checking account."""
    return delete_import(
        import_id, seed_user["user"].id, seed_user["account"].id,
    )


def _rows(db, table, column, value):
    """Return how many rows the DATABASE holds, bypassing the identity map.

    ``session.get`` answers from the identity map, so an object this test
    staged can never read back as ``None`` however the database was changed --
    and ``StatementImport.lines`` is ``passive_deletes=True``, so a DB-tier
    cascade never expires those instances either.  An assertion written that
    way passes whether the row went or not; found by adversarial test-quality
    review 2026-08-20, which deleted EVERY line on the account and watched 13
    of these 15 cases pass.

    Args:
        db: The session fixture.
        table: The schema-qualified table name.
        column: The column to filter on.
        value: Its value.

    Returns:
        The row count.
    """
    return db.session.execute(
        db.text(f"SELECT count(*) FROM {table} WHERE {column} = :value"),
        {"value": value},
    ).scalar()


def _match(seed_user, line, txn):
    """Accept a real match between one line and one row, through the door.

    ONE scope builds the submission and applies it, which is the two-moment
    flow the screen has: the reviewed state a tick carries is read off the pass
    that rendered it (plan step ``bank_import:X-f6d-3``).
    """
    scope = a_scope(seed_user)
    return statement_match.accept_match(
        a_submission(scope, lines=[line], transactions=[txn]),
        scope,
    )


class TestItRemovesWhatThatImportRecorded:
    """The import, its lines, and nothing beyond them."""

    def test_it_removes_the_import_and_its_lines(self, app, db, seed_user):
        """The act and the observations it wrote go together."""
        outcome = _import(seed_user)

        removal = _undo(seed_user, outcome.import_id)

        assert removal.lines_removed == 2
        assert db.session.query(StatementImport).count() == 0
        assert _rows(db, "budget.bank_statement_lines", "account_id",
                     seed_user["account"].id) == 0

    def test_a_line_ANOTHER_import_also_sighted_stays(
        self, app, db, seed_user,
    ):
        """A line lives while any sighting does (ruling **R-BI10**).

        MONEY-ADJACENT: re-importing an overlapping span records a SIGHTING
        of every line and no duplicate line.  Deleting the second must take
        no line -- if it took the lines it merely re-saw, an owner tidying up
        a redundant import would silently destroy the record the first import
        holds -- and it takes only the second's sightings.
        """
        first = _import(seed_user)
        second = _import(seed_user, file_name="again.csv")
        assert second.recorded_count == 0

        removal = _undo(seed_user, second.import_id)

        assert removal.lines_removed == 0
        assert db.session.query(BankStatementLine).count() == 2
        assert db.session.query(StatementImport).one().id == first.import_id
        assert db.session.query(StatementLineSighting).count() == 2

    def test_deleting_the_FIRST_import_keeps_the_lines_a_re_import_sighted(
        self, app, db, seed_user,
    ):
        """The other direction, which the old ownership got wrong.

        *Until plan step ``bank_import:X-f6b-1`` the first import owned the
        lines, so deleting it took every line the re-import had also shown
        and left the re-import's span claiming days with no lines under it.*
        Now the re-import vouches for them: the first import goes, its
        sightings go, the lines and the re-import's sightings stay, and the
        receipt says no line was removed.
        """
        first = _import(seed_user)
        second = _import(seed_user, file_name="again.csv")

        removal = _undo(seed_user, first.import_id)

        assert removal.lines_removed == 0
        assert db.session.query(BankStatementLine).count() == 2
        assert db.session.query(StatementImport).one().id == second.import_id
        assert {
            sighting.import_id
            for sighting in db.session.query(StatementLineSighting).all()
        } == {second.import_id}

    def test_it_reports_the_span_and_the_file_it_removed(
        self, app, db, seed_user,
    ):
        """Every figure is counted as the act runs, because afterwards it is gone."""
        outcome = _import(seed_user, file_name="ytd.csv")

        removal = _undo(seed_user, outcome.import_id)

        assert removal.file_name == "ytd.csv"
        assert removal.declared_start == date(2026, 3, 2)
        assert removal.declared_end == date(2026, 3, 4)
        assert removal.matches_released == 0


class TestItSaysHowManyOfTheOwnersSKIPSItDestroyed:
    """Plan step ``bank_import:X-gj-4a``, ruling **bank_import:R-JG**.

    A skip claims nothing but its own line, so
    ``fk_statement_line_skips_line_account`` takes it away with the line rather
    than refusing this delete -- which is right, and is exactly why the receipt
    has to COUNT it.  What the number names is questions the owner has already
    answered being asked again the next time the same span is imported.

    **Counted BEFORE the cascade, which is what these grade**: a figure read
    afterwards would be zero however many rows went, and the test would pass
    against a receipt that under-reports every removal.
    """

    def test_a_delete_reports_the_skips_it_took(self, app, db, seed_user):
        """Two skipped lines go with their import, and the receipt says two."""
        outcome = _import(seed_user)
        lines = db.session.query(BankStatementLine).order_by(
            BankStatementLine.id,
        ).all()
        for line in lines:
            statement_match.skip_line(
                line.id, seed_user["user"].id, seed_user["account"].id,
            )
        db.session.flush()

        removal = _undo(seed_user, outcome.import_id)

        assert removal.skips_forgotten == 2
        assert _rows(db, "budget.statement_line_skips", "account_id",
                     seed_user["account"].id) == 0

    def test_a_delete_that_took_no_skip_reports_zero(
        self, app, db, seed_user,
    ):
        """The ordinary case, so the count above is about the skips.

        Separated deliberately: a receipt hard-coded to the number of lines
        would pass the case above and fail here.
        """
        outcome = _import(seed_user)

        removal = _undo(seed_user, outcome.import_id)

        assert removal.skips_forgotten == 0

    def test_it_counts_only_THIS_imports_skips(self, app, db, seed_user):
        """A skip on a line another import owns is not this delete's to report.

        MONEY-ADJACENT in the same way ``test_a_line_ANOTHER_import_recorded
        _stays`` is: an owner tidying up one import must not be told they lost
        answers that are still standing.
        """
        first = _import(seed_user)
        kept = db.session.query(BankStatementLine).order_by(
            BankStatementLine.id,
        ).first()
        statement_match.skip_line(
            kept.id, seed_user["user"].id, seed_user["account"].id,
        )
        second = _import(seed_user, file_name="again.csv")
        assert second.recorded_count == 0

        removal = _undo(seed_user, second.import_id)

        assert removal.skips_forgotten == 0
        assert _rows(db, "budget.statement_line_skips", "account_id",
                     seed_user["account"].id) == 1
        assert _undo(seed_user, first.import_id).skips_forgotten == 1


def _placements(account_id):
    """Return ``{import_id: (level, release)}`` off the level relation.

    The page's own read, keyed the way the page keys it, so a test asks the
    relation what stands rather than a column that no longer exists (plan
    step ``balance:X-bj-1``).
    """
    return {
        level.statement_import_id: (level, release)
        for level, release in bank_levels(account_id)
    }


class TestItReleasesTheANCHORSItsLinesSupported:
    """A balance anchor rests on lines, and this door takes lines away.

    **Reproduced through these very doors by adversarial review, 2026-08-23**:
    a later overlapping import kept its span and its anchor while the earlier
    import's lines were deleted beneath it, so the coverage test reported
    "covered" over a `$150.00` hole and the walk returned a confident wrong
    opening.

    **Since plan step ``balance:X-bj-1`` a release is an APPENDED row**
    (``budget.anchor_releases``) naming the level and the import whose lines
    changed, and the deleted import's OWN level cascades with it rather than
    being withdrawn.  The delete door names the doomed import as the cause
    BEFORE deleting it, so the cause reaches the audit log; on the row the
    key's ``SET NULL`` then reads it as "the import responsible no longer
    exists".
    """

    def test_deleting_an_import_releases_an_anchor_over_its_span(
        self, app, db, seed_user,
    ):
        """The anchor goes with the evidence it rested on."""
        outcome = _import(seed_user)
        assert outcome.import_id in _placements(seed_user["account"].id)

        second = _import(
            seed_user,
            payload=build.build(build.chained(
                "1534.19",
                [(date(2026, 3, 5), "-10.00", "POINT OF SALE DEBIT L340 X")],
            )),
            file_name="later.csv",
        )
        later_level, later_release = _placements(
            seed_user["account"].id,
        )[second.import_id]
        assert later_release is None

        removal = _undo(seed_user, outcome.import_id)

        placements = _placements(seed_user["account"].id)
        assert removal.anchors_released == 1
        # The deleted import's own level went with it (cascade); the later
        # import's level stands in the relation, withdrawn by a release that
        # kept the day and lost its cause to the delete.
        assert outcome.import_id not in placements
        level, release = placements[second.import_id]
        assert level.id == later_level.id
        assert level.observed_on == date(2026, 3, 5)
        assert release is not None
        assert release.released_by_import_id is None
        assert release.lines_changed_from == date(2026, 3, 2)

    def test_a_delete_that_removes_NO_LINES_releases_nothing(
        self, app, db, seed_user,
    ):
        """An idempotent re-import owns no lines, so undoing it changes none.

        **Measured on the developer's own database**: undoing a re-import of
        his 2026-08-16 export removed 0 of that file's 361 lines and yet took a
        good anchor with it, because the release was keyed on the import's
        declared SPAN rather than on the days whose lines actually went.
        """
        first = _import(seed_user)
        again = _import(seed_user, file_name="again.csv")
        early_level, _ = _placements(seed_user["account"].id)[first.import_id]
        assert early_level.observed_on == date(2026, 3, 4)

        removal = _undo(seed_user, again.import_id)

        assert removal.lines_removed == 0
        assert removal.anchors_released == 0
        level, release = _placements(seed_user["account"].id)[first.import_id]
        assert level.observed_on == date(2026, 3, 4)
        assert release is None

    def test_it_LEAVES_an_anchor_that_predates_the_deleted_span(
        self, app, db, seed_user,
    ):
        """A delete of later days says nothing about an earlier anchor."""
        first = _import(seed_user)
        second = _import(
            seed_user,
            payload=build.build(build.chained(
                "1534.19",
                [(date(2026, 3, 5), "-10.00", "POINT OF SALE DEBIT L340 X")],
            )),
            file_name="later.csv",
        )

        removal = _undo(seed_user, second.import_id)

        assert removal.anchors_released == 0
        level, release = _placements(seed_user["account"].id)[first.import_id]
        assert level.observed_on == date(2026, 3, 4)
        assert release is None

    def test_the_cause_reaches_the_audit_log_before_the_delete_nulls_it(
        self, app, db, seed_user,
    ):
        """The release row is INSERTed naming the doomed import, then SET NULL.

        Two audit rows for one release: the INSERT carrying the cause and the
        UPDATE the key's action wrote.  A door that released after deleting
        would write one row carrying NULL, and the fact of WHICH import's
        delete withdrew the level would exist nowhere.  Committed, because
        the audit rows are written by triggers and the row under test is
        read back by SQL.
        """
        outcome = _import(seed_user)
        second = _import(
            seed_user,
            payload=build.build(build.chained(
                "1534.19",
                [(date(2026, 3, 5), "-10.00", "POINT OF SALE DEBIT L340 X")],
            )),
            file_name="later.csv",
        )
        db.session.commit()

        _undo(seed_user, outcome.import_id)
        db.session.commit()

        _level, release = _placements(
            seed_user["account"].id,
        )[second.import_id]
        rows = db.session.execute(db.text(
            "SELECT operation, "
            "       new_data ->> 'released_by_import_id' AS cause "
            "FROM system.audit_log "
            "WHERE table_name = 'anchor_releases' AND row_id = :row_id "
            "ORDER BY id"
        ), {"row_id": release.id}).all()
        assert [(op, cause) for op, cause in rows] == [
            ("INSERT", str(outcome.import_id)),
            ("UPDATE", None),
        ]


class TestItReleasesRatherThanOrphans:
    """A match may not lose its bank lines, and the days it wrote stay."""

    def test_it_releases_a_match_naming_one_of_its_lines(
        self, app, db, seed_user,
    ):
        """The act is DELETED, not left standing with an empty bank side.

        A match with no line asserts nothing about a bank, so the review screen
        cannot render it and no release button ever exists for it -- while
        ``matched_subjects`` reads the member rows directly and goes on
        reporting its rows as claimed.  Measured on a production clone
        2026-08-20, deleting an import by hand left exactly that: an act with 0
        line members and 1 transaction member.
        """
        statement = an_import(seed_user)
        line = a_bank_line(seed_user, statement, amount="-180.00")
        txn = a_transaction(seed_user, amount="180.00")
        _match(seed_user, line, txn)
        assert db.session.query(StatementMatch).count() == 1

        removal = _undo(seed_user, statement.id)

        assert removal.matches_released == 1
        assert db.session.query(StatementMatch).count() == 0
        assert db.session.query(StatementMatchMember).count() == 0

    def test_the_settle_day_the_match_wrote_is_UNCHANGED(
        self, app, db, seed_user,
    ):
        """MONEY: the delete removes what the BANK said, not what the app knows.

        A settle day is the app's own record of when money moved, and the bank
        is still the best evidence it has -- so reverting a correction in order
        to tidy a relation would throw away the fact and keep the bookkeeping.
        This is ``release_match``'s own rule, asked of the door that calls it.
        """
        statement = an_import(seed_user)
        posted = seed_user["bootstrap_period"].start_date
        line = a_bank_line(
            seed_user, statement, amount="-180.00", posted_on=posted,
        )
        txn = a_transaction(seed_user, amount="180.00")
        _match(seed_user, line, txn)
        db.session.refresh(txn)
        assert txn.settled_on == posted

        _undo(seed_user, statement.id)

        db.session.refresh(txn)
        assert txn.settled_on == posted
        assert txn.status_id == ref_cache.status_id(StatusEnum.DONE)

    def test_the_released_rows_are_MATCHABLE_again(self, app, db, seed_user):
        """What comes back is the QUESTION, which is the point of a release.

        FIRING CONTROL for the defect this door exists not to create: if the
        act were left standing without its lines, this row would report as
        already-matched forever and no door could free it.
        """
        statement = an_import(seed_user)
        line = a_bank_line(seed_user, statement, amount="-180.00")
        txn = a_transaction(seed_user, amount="180.00")
        _match(seed_user, line, txn)
        assert txn.id in matched_subjects(seed_user["account"].id).transactions

        _undo(seed_user, statement.id)

        claimed = matched_subjects(seed_user["account"].id)
        assert txn.id not in claimed.transactions
        assert claimed.lines == frozenset()

    def test_a_match_spanning_TWO_imports_is_released_whole(
        self, app, db, seed_user,
    ):
        """The other import's line becomes unexplained again, and stays recorded.

        A group is one movement; releasing half of it is not a smaller group,
        it is a broken one.  So the other import's line survives the delete --
        it belongs to an import nobody removed -- and is simply unmatched.
        """
        first = an_import(seed_user)
        second = an_import(seed_user)
        posted = seed_user["bootstrap_period"].start_date
        kept = a_bank_line(
            seed_user, first, amount="-80.00", posted_on=posted,
            description="ONE",
        )
        going = a_bank_line(
            seed_user, second, amount="-100.00", posted_on=posted,
            description="TWO",
        )
        txn = a_transaction(seed_user, amount="180.00")
        scope = a_scope(seed_user)
        statement_match.accept_match(
            a_submission(
                scope, lines=[kept, going], transactions=[txn],
            ),
            scope,
        )

        removal = _undo(seed_user, second.id)

        assert removal.matches_released == 1
        assert db.session.query(StatementMatch).count() == 0
        assert _rows(db, "budget.bank_statement_lines", "id", kept.id) == 1
        assert matched_subjects(seed_user["account"].id).lines == frozenset()


class TestThePairingOutlivesNoImportThatTaughtIt:
    """Which bank account this is, is a fact learned from an import."""

    def test_the_last_import_takes_the_pairing_with_it(
        self, app, db, seed_user,
    ):
        """What makes a WRONGLY-recorded pairing repairable (N-302's second shape).

        Recorded on a first import and then used to refuse every later file
        naming a different account -- correct, and until this step
        unrepairable: an owner who chose the wrong Shekel account once could
        never import that account's statements again.
        """
        outcome = _import(seed_user)
        assert db.session.query(AccountExternalIdentity).count() == 1

        removal = _undo(seed_user, outcome.import_id)

        assert removal.identity_forgotten is True
        assert db.session.query(AccountExternalIdentity).count() == 0

    def test_a_surviving_import_from_that_source_KEEPS_the_pairing(
        self, app, db, seed_user,
    ):
        """FIRING CONTROL: forgetting on any delete would be the wrong rule.

        The pairing protects the recorded lines from another account's
        statement.  While an import from that source survives there are lines
        to protect, so the guard must stay armed.
        """
        first = _import(seed_user)
        second = _import(seed_user, file_name="again.csv")

        removal = _undo(seed_user, second.import_id)

        assert removal.identity_forgotten is False
        assert db.session.query(AccountExternalIdentity).count() == 1
        assert first.import_id == db.session.query(StatementImport).one().id


class TestTheMerchantsOutliveTheirLinesONLYWhileTheyAreANSWERED:
    """The sweep, and the exception that is the whole reason it is narrow.

    Plan step ``bank_import:X-gd-1``, on an adversarial security review of
    2026-08-25.  A merchant is deliberately NOT removed with the lines that
    named it, because a stated answer has to stay readable and restatable
    afterwards -- that property is what retired ``statable_merchants``' second
    derivation.  **It is a property of ANSWERED merchants**, and applied to all
    of them it left ``budget.merchants`` with no ceiling at all: upload a file
    naming any number of unseen merchants, delete the import, keep them,
    permanently, once per upload.

    Both directions are here because either alone is satisfiable by a wrong
    rule -- sweeping nothing passes the second, sweeping everything passes the
    first.
    """

    def test_a_merchant_NOTHING_still_names_goes_with_the_import(
        self, app, db, seed_user,
    ):
        """The ceiling.  Sweep nothing and this fails."""
        outcome = _import(seed_user)
        assert db.session.query(Merchant).count() == 2

        removal = _undo(seed_user, outcome.import_id)

        assert removal.merchants_forgotten == 2
        assert db.session.query(Merchant).count() == 0

    def test_an_ANSWERED_merchant_STAYS_when_its_lines_go(
        self, app, db, seed_user,
    ):
        """THE firing control for the sweep's ``notin_`` over stated answers.

        Drop that clause and an owner who deletes an import loses every answer
        they had stated about its merchants -- silently, through a door whose
        receipt would not even say so, because the rule row goes with the
        merchant on ``fk_merchant_rules_merchant_account``.
        """
        outcome = _import(seed_user)
        answered = db.session.query(Merchant).filter(
            Merchant.name == "Food Lion",
        ).one()
        db.session.add(MerchantRule(
            user_id=seed_user["user"].id,
            account_id=seed_user["account"].id,
            merchant_id=answered.id,
            never_a_purchase=True,
        ))
        db.session.flush()

        removal = _undo(seed_user, outcome.import_id)

        assert removal.merchants_forgotten == 1
        assert [row.name for row in db.session.query(Merchant).all()] == [
            "Food Lion",
        ]
        assert db.session.query(MerchantRule).count() == 1

    def test_a_merchant_ANOTHER_SURVIVING_import_names_STAYS(
        self, app, db, seed_user,
    ):
        """The other half of *nothing still names it*, on the sighting side.

        Two imports over the same span record one set of lines -- the second
        adds none -- so deleting the second must sweep nothing at all: every
        merchant is still named by a sighting the first import wrote.
        """
        first = _import(seed_user)
        second = _import(seed_user, file_name="again.csv")

        removal = _undo(seed_user, second.import_id)

        assert removal.merchants_forgotten == 0
        assert db.session.query(Merchant).count() == 2
        assert first.import_id == db.session.query(StatementImport).one().id

    def test_a_merchant_only_the_DELETED_imports_sighting_named_goes_and_the_line_reads_the_survivor(
        self, app, db, seed_user,
    ):
        """Finding **BI-504** at the door: the line's merchant follows its sightings.

        One line, two imports, two WORDS (the cross-source shape the feed
        brings; staged through the builders because one CSV source refuses
        its own restatement).  The FIRST import named ``COFFEE`` and is
        deleted; the line survives on the second's sighting, its merchant
        READS ``Coffee Shop`` with nothing repaired, and ``COFFEE`` -- named
        by no surviving sighting and no rule -- is swept and counted.  Until
        plan step ``bank_import:X-f6b-1b`` the line kept ``COFFEE``'s key,
        the sweep could not take a merchant a line still keyed, and the
        stale key survived its import.
        """
        first = an_import(
            seed_user, created_at=datetime(2026, 4, 1, 12, tzinfo=timezone.utc),
        )
        second = an_import(
            seed_user, created_at=datetime(2026, 4, 2, 12, tzinfo=timezone.utc),
        )
        line = a_bank_line(seed_user, first, amount="-4.50", merchant="COFFEE")
        a_sighting(seed_user, second, line, merchant="Coffee Shop")
        db.session.flush()
        db.session.expire_all()
        assert db.session.get(BankStatementLine, line.id).merchant_name == "COFFEE"
        line_id = line.id

        removal = _undo(seed_user, first.id)

        assert removal.lines_removed == 0
        assert removal.merchants_forgotten == 1
        assert [row.name for row in db.session.query(Merchant).all()] == [
            "Coffee Shop",
        ]
        db.session.expire_all()
        read = db.session.get(BankStatementLine, line_id)
        assert read.merchant_name == "Coffee Shop"
        assert [sighting.import_id for sighting in read.sightings] == [second.id]

    def test_it_sweeps_THIS_account_alone(
        self, app, db, seed_user, seed_second_user,
    ):
        """Another account's merchant no line names is not this delete's to take.

        *Until plan step ``bank_import:X-gr`` this docstring said dropping
        ``Merchant.account_id == account_id`` from the sweep would take every
        unanswered merchant in the database.*  The act deletes the list
        :func:`~app.services.statement_import._reads.orphan_merchants_by_import`
        names since that step -- merchants this import's lines alone name --
        so a merchant NO line names is in it on no account, and this case
        fires on no scope clause: the cross-account twin of the case below.
        The read's own scope is graded where a foreign line DOES name a
        merchant, ``test_reads.test_orphan_merchants_reads_only_ITS_OWN_account``.
        """
        theirs = Merchant(
            account_id=seed_second_user["account"].id, name="Theirs Alone",
        )
        db.session.add(theirs)
        db.session.flush()
        outcome = _import(seed_user)

        removal = _undo(seed_user, outcome.import_id)

        assert removal.merchants_forgotten == 2
        assert [row.name for row in db.session.query(Merchant).all()] == [
            "Theirs Alone",
        ]

    def test_a_merchant_NO_line_named_before_the_delete_is_not_its_work(
        self, app, db, seed_user,
    ):
        """The act deletes the list the confirmation previewed, not a sweep.

        Plan step ``bank_import:X-gr``, finding **BI-490**.  The act swept
        *whatever is orphaned now* after the rows were gone until that step,
        which would have reported a merchant this delete never touched as its
        own work and counted differently from the confirmation that previewed
        it.  No door produces a merchant no line names
        (:func:`~app.services.statement_import._merchants.resolve_merchants`
        writes every row it creates onto a line in the same pass; measured 0
        of 67 on the developer's database 2026-09-12), so it is PLANTED, and
        the receipt is graded against the read rather than the sweep.
        """
        planted = Merchant(
            account_id=seed_user["account"].id, name="Nobody Named Me",
        )
        db.session.add(planted)
        db.session.flush()
        outcome = _import(seed_user)
        assert db.session.query(Merchant).count() == 3

        removal = _undo(seed_user, outcome.import_id)

        assert removal.merchants_forgotten == 2
        assert [row.name for row in db.session.query(Merchant).all()] == [
            "Nobody Named Me",
        ]


class TestItRefusesWhatIsNotThisOwnersImport:
    """The set-operation form of "404 for both not-found and not-yours"."""

    def test_an_unknown_import_id_is_refused(self, app, db, seed_user):
        """A stale page naming an import someone else already deleted."""
        with pytest.raises(ValidationError) as caught:
            _undo(seed_user, 999999)

        assert "no longer there" in str(caught.value)

    def test_ANOTHER_accounts_import_is_refused(self, app, db, seed_user):
        """Scoping is by account as well as by owner.

        The route proves ownership of the ACCOUNT; this door is what stops an
        id from one of the owner's own accounts reaching another's statements.
        """
        outcome = _import(seed_user)
        other = _second_account(db, seed_user)

        with pytest.raises(ValidationError):
            delete_import(
                outcome.import_id, seed_user["user"].id, other.id,
            )

        assert db.session.query(StatementImport).count() == 1
        assert db.session.query(BankStatementLine).count() == 2

    def test_ANOTHER_OWNERS_import_is_refused(
        self, app, db, seed_user, seed_second_user,
    ):
        """FIRING CONTROL for the OWNER half of the scope.

        The account filter already implies the owner today -- ``@require_owner``
        blocks a companion at the route -- so this arm is defence in depth, and
        a class named for grading the scope must actually grade both halves of
        it.  Adversarial review measured that removing the owner filter left
        192 tests green; this is the case that turns red.
        """
        outcome = _import(seed_user)
        with pytest.raises(ValidationError):
            delete_import(
                outcome.import_id,
                seed_second_user["user"].id,
                seed_user["account"].id,
            )

        assert db.session.query(StatementImport).count() == 1
        assert _rows(db, "budget.bank_statement_lines", "account_id",
                     seed_user["account"].id) == 2

    def test_a_refusal_writes_nothing(self, app, db, seed_user):
        """It refuses BEFORE it stages anything, not by rolling back."""
        _import(seed_user)
        before = db.session.query(BankStatementLine).count()

        with pytest.raises(ValidationError):
            _undo(seed_user, 999999)

        assert db.session.query(BankStatementLine).count() == before
        assert db.session.query(AccountExternalIdentity).count() == 1


class TestTheAccountCanImportAgain:
    """The round trip N-302 asks for, asserted end to end."""

    def test_a_deleted_span_can_be_re_imported_in_full(
        self, app, db, seed_user,
    ):
        """Delete then re-import restores exactly what was there.

        This is the whole point: the repair is not "the rows are gone", it is
        "the account works again".  A door that removed the lines but left
        anything behind -- a pairing that now refuses the same file, a match
        holding its rows, an ordinal the identity key collides on -- would pass
        every test above and fail this one.
        """
        first = _import(seed_user)
        _undo(seed_user, first.import_id)

        second = _import(seed_user, file_name="redo.csv")

        assert second.recorded_count == first.recorded_count == 2
        assert db.session.query(BankStatementLine).count() == 2
        assert db.session.query(AccountExternalIdentity).count() == 1

    def test_a_RESTATED_line_is_repaired_by_deleting_its_import(
        self, app, db, seed_user,
    ):
        """The dead end N-302 names, walked from end to end.

        The bank restates a line; the import refuses, correctly and
        permanently, because a recorded observation may not be overwritten.
        Before this step nothing in ``app/`` could clear the recorded line, so
        that account could never import again.
        """
        first = _import(seed_user)
        restated = list(_ENTRIES)
        restated[0] = (date(2026, 3, 2), "-25.00", "SOMETHING ELSE ENTIRELY")

        with pytest.raises(StatementLineConflict):
            _import(seed_user, _file(restated), file_name="restated.csv")

        _undo(seed_user, first.import_id)
        repaired = _import(
            seed_user, _file(restated), file_name="restated.csv",
        )

        assert repaired.recorded_count == 2
        assert db.session.query(StatementLineSighting).filter_by(
            description="SOMETHING ELSE ENTIRELY",
        ).count() == 1


class TestTheDeletesAreAudited:
    """`system.audit_log` is what makes a destructive door forensically safe."""

    def test_every_removed_line_leaves_an_audit_row(
        self, app, db, seed_user,
    ):
        """The trigger fires on a CASCADED delete, not only a direct one.

        The lines go with the import at the database tier, so the audit trail
        depends on PostgreSQL firing row triggers on cascade -- which it does,
        and which is asserted rather than assumed because the whole forensic
        story of this door rests on it.
        """
        outcome = _import(seed_user)
        db.session.flush()

        _undo(seed_user, outcome.import_id)
        db.session.flush()

        deleted = db.session.execute(db.text(
            "SELECT count(*) FROM system.audit_log "
            "WHERE table_name = 'bank_statement_lines' AND operation = 'DELETE'"
        )).scalar()
        assert deleted == 2
