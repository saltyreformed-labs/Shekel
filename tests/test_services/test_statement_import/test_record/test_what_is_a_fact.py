"""The account mapping is a fact, a recorded line is not restated, and a group reconciles."""

from datetime import date
from decimal import Decimal

import pytest

from app.models.statement_import import AccountExternalIdentity, BankStatementLine, StatementImport
from app.services.statement_import import StatementAccountMismatch, StatementLineConflict

from tests.test_services.test_statement_import import _csv_builder as build

from ._helpers import _ENTRIES, _file, _record, _second_account, _TWINS, _LATER


class TestTheAccountMappingIsAFactNotAGuess:
    """Ruling R-FP: the first import records it, every import after checks it."""

    def test_the_first_import_records_what_the_source_calls_the_account(
        self, app, db, seed_user,
    ):
        """The mapping exists because the user stated it, not because the app
        inferred it."""
        _record(seed_user, _file())

        identity = db.session.query(AccountExternalIdentity).one()
        assert identity.account_id == seed_user["account"].id
        assert identity.external_account_id == build.ACCOUNT_IDENTITY

    def test_a_second_import_of_the_same_account_records_no_second_mapping(
        self, app, db, seed_user,
    ):
        """One account has ONE identity per source."""
        _record(seed_user, _file())
        _record(seed_user, _file(), file_name="again.csv")

        assert db.session.query(AccountExternalIdentity).count() == 1

    def test_a_file_for_a_DIFFERENT_account_is_refused(
        self, app, db, seed_user,
    ):
        """Importing the card's export into checking, the case R-FP names."""
        _record(seed_user, _file())

        with pytest.raises(StatementAccountMismatch) as caught:
            _record(
                seed_user, _file(account_number="******9999"),
                file_name="wrong.csv",
            )

        assert caught.value.submitted == "Checking ******9999"
        assert caught.value.recorded == build.ACCOUNT_IDENTITY

    def test_the_identity_carries_the_account_NAME_not_just_the_mask(
        self, app, db, seed_user,
    ):
        """The mask alone cannot tell two of one owner's accounts apart.

        SECU masks the number to its last four digits, so two accounts whose
        numbers end alike mask identically -- and comparing only the mask would
        ACCEPT a file imported against the wrong account, which is the failure
        direction this mapping exists to prevent.
        """
        _record(seed_user, _file())

        with pytest.raises(StatementAccountMismatch):
            _record(
                seed_user,
                build.build(build.chained(
                    "100.00", _ENTRIES, account_name="Savings",
                )),
                file_name="savings.csv",
            )

    def test_a_file_another_account_already_claims_is_refused(
        self, app, db, seed_user,
    ):
        """The arm a one-directional check would miss.

        Comparing only against THIS account's recorded identity would let a
        statement already imported under account A be imported again under
        account B as a "first import" -- the same bank statement recorded
        twice, under two accounts.
        """
        _record(seed_user, _file())
        other = _second_account(db, seed_user)
        db.session.flush()

        with pytest.raises(StatementAccountMismatch):
            _record(seed_user, _file(), account=other)

    def test_a_refused_import_stages_nothing(self, app, db, seed_user):
        """"Nothing was imported" is true BEFORE the rollback, not because of it.

        Asserted without rolling back deliberately: the door validates
        everything it can refuse before it stages a single row, so the session
        is already clean when the refusal is raised.  A test that rolled back
        first would pass equally against a door that wrote and then relied on
        the route remembering to undo it.
        """
        _record(seed_user, _file())
        before = db.session.query(BankStatementLine).count()

        with pytest.raises(StatementAccountMismatch):
            _record(
                seed_user, _file(account_number="******9999"),
                file_name="wrong.csv",
            )

        assert db.session.query(BankStatementLine).count() == before
        assert db.session.query(StatementImport).count() == 1


class TestARecordedLineMayNotBeQuietlyRestated:
    """An observation rewritten is what ruling R-FL exists to prevent."""

    def test_a_changed_description_at_a_known_identity_is_refused(
        self, app, db, seed_user,
    ):
        """Same day, same amount, same ordinal -- different text."""
        _record(seed_user, _file())

        changed = list(_ENTRIES)
        changed[0] = (date(2026, 3, 2), "-25.00", "SOMETHING ELSE ENTIRELY")

        with pytest.raises(StatementLineConflict) as caught:
            _record(seed_user, _file(changed), file_name="restated.csv")

        assert caught.value.posted_on == date(2026, 3, 2)
        assert caught.value.submitted == "SOMETHING ELSE ENTIRELY"

    def test_a_changed_running_balance_is_NOT_a_conflict(
        self, app, db, seed_user,
    ):
        """A running balance is not a fact about a line, so it cannot restate one.

        It is a prefix sum over the bank's LISTING ORDER, and SECU lists a
        day's card debits sorted by ascending magnitude rather than by arrival
        -- so a swipe that finalizes onto an already-listed day is INSERTED,
        and every later line on that day legitimately gets a different balance
        while both files verify their own chain.  Comparing it per line refused
        an honest, more-complete re-export of the user's own year-to-date
        statement and left that account unable to import ever again.
        """
        _record(seed_user, _file())

        second = _record(
            seed_user, _file(start="9999.00"), file_name="later.csv",
        )

        assert second.recorded_count == 0

    def test_an_unchanged_re_import_is_NOT_a_conflict(
        self, app, db, seed_user,
    ):
        """The refusal must not fire on the ordinary case.

        A guard that refuses the correct write is worse than none, and the
        correct write here -- re-importing an unchanged year-to-date export --
        is what the user does every single time.
        """
        _record(seed_user, _file())

        second = _record(seed_user, _file(), file_name="again.csv")

        assert second.recorded_count == 0


class TestAGroupIsReconciledAsASet:
    """Plan step X-f6a-4: the ordinal is a surrogate, not a comparison term."""

    def test_the_same_two_lines_in_the_OTHER_order_record_nothing(
        self, app, db, seed_user,
    ):
        """FIRING CONTROL: the bank re-orders one day's two equal debits.

        Measured against the positional code 2026-08-20, this refused the whole
        file and told the owner the bank had restated a line it had not.  Both
        lines are present in both files; only the ordinal this app assigned
        moved.
        """
        _record(seed_user, _file(_TWINS))

        second = _record(
            seed_user, _file(list(reversed(_TWINS))), file_name="swapped.csv",
        )

        assert second.recorded_count == 0
        assert db.session.query(BankStatementLine).count() == 2

    def test_a_reorder_does_not_cost_the_file_its_GENUINELY_new_lines(
        self, app, db, seed_user,
    ):
        """MONEY: what a false refusal actually costs is the rest of the export.

        The re-ordered pair is two lines; the file also carries a line the app
        has never seen.  Refusing the file loses that line -- and every other
        line after it -- until someone repairs the account by hand.
        """
        _record(seed_user, _file(_TWINS))

        second = _record(
            seed_user,
            _file(list(reversed(_TWINS)) + [_LATER]),
            file_name="swapped_plus_new.csv",
        )

        assert second.recorded_count == 1
        assert db.session.query(BankStatementLine).filter_by(
            posted_on=date(2026, 3, 5),
        ).one().amount == Decimal("-99.00")

    def test_a_NEW_line_the_bank_lists_FIRST_is_recorded_as_the_new_one(
        self, app, db, seed_user,
    ):
        """FIRING CONTROL: a swipe finalizes onto an already-recorded day.

        SECU INSERTS such a line into that day's block rather than appending it
        -- the behaviour that made ``_refuse_restatement`` stop comparing
        running balances -- so the file lists the NEW line FIRST and the
        recorded one after it.  Under the positional rule that compared the new
        line against the recorded one's wording and refused the whole file.
        Exactly one line is new, and the recorded one keeps its own address.
        """
        _record(seed_user, _file([_TWINS[0]]))

        second = _record(
            seed_user,
            _file([_TWINS[1], _TWINS[0]]),
            file_name="inserted.csv",
        )

        assert second.recorded_count == 1
        rows = {
            row.description: row.sequence_in_group
            for row in db.session.query(BankStatementLine).all()
        }
        assert rows == {_TWINS[0][2]: 0, _TWINS[1][2]: 1}

    def test_a_second_IDENTICAL_charge_is_recorded_rather_than_dropped(
        self, app, db, seed_user,
    ):
        """MONEY: the same coffee twice at the same shop is two movements.

        This is what the ordinal exists for, asked of the set-wise
        reconciliation: pairing by wording must be by COUNT, or the second
        charge is read as a duplicate of the first and never recorded.
        """
        same = (date(2026, 3, 2), "-4.75", "POINT OF SALE DEBIT L340 COFFEE")
        _record(seed_user, _file([same]))

        second = _record(seed_user, _file([same, same]), file_name="twice.csv")

        assert second.recorded_count == 1
        assert db.session.query(BankStatementLine).count() == 2
        assert sorted(
            row.sequence_in_group
            for row in db.session.query(BankStatementLine).all()
        ) == [0, 1]

    def test_a_RESTATED_line_in_a_shared_group_is_still_refused(
        self, app, db, seed_user,
    ):
        """The refusal the policy is FOR survives the reconciliation change.

        One of the two lines keeps its wording and pairs; the other's wording
        is gone from the file and an unaccounted-for line stands in its place.
        That is the bank re-wording an observation, and ruling R-FL refuses it.
        """
        _record(seed_user, _file(_TWINS))

        restated = [
            _TWINS[0],
            (date(2026, 3, 2), "-4.75", "SOMETHING ELSE ENTIRELY"),
        ]

        with pytest.raises(StatementLineConflict) as caught:
            _record(seed_user, _file(restated), file_name="restated.csv")

        assert caught.value.recorded == _TWINS[1][2]
        assert caught.value.submitted == "SOMETHING ELSE ENTIRELY"
        assert db.session.query(BankStatementLine).count() == 2

    def test_a_SHORTER_export_over_a_shared_group_refuses_nothing(
        self, app, db, seed_user,
    ):
        """A file covering less than the app holds contradicts nothing.

        The app holds both twins and the file states one.  Under the positional
        rule the surviving line landed at ordinal 0 and was compared against
        the OTHER twin's wording, which refused.  It is now what it is: a
        shorter export, recording nothing and refusing nothing.
        """
        _record(seed_user, _file(_TWINS))

        second = _record(
            seed_user, _file([_TWINS[1]]), file_name="shorter.csv",
        )

        assert second.recorded_count == 0
        assert db.session.query(BankStatementLine).count() == 2
