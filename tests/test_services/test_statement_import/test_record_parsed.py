"""The recording walk behind the upload door: reading is the upload's, recording is everyone's.

Ruling **bank_import:R-BI30** (plan step ``bank_import:X-f6b-2``, sync leaf
(ii)): :func:`record_statement` is the upload door, ``parse_statement`` then
:func:`record_parsed`, and :func:`record_parsed` is the ONE recording walk,
taking what a source STATED (a :class:`ParsedStatement`) and the act's
:class:`Provenance`.  The bank feed's reader reaches
the walk with what it read off Bridge's answer, so no source that is not a
file needs a place in the file parsers' table.

Two things are graded here:

* **the two doors record the same import**, column by column, so the split
  moved the walk and changed nothing it writes;
* **the walk records a statement with NO line** (ruling **R-BAL71**: coverage
  is the declared window, so a feed sync over a quiet window covers what it
  asked for and may state a balance), which no upload can reach because the
  CSV adapter refuses an empty file -- as a first import, after a recorded
  window, after a coverage gap (ruling **R-BI35**), and with no claim;

In a file of its own rather than ``test_record.py``, which stands past the
1,000-line ceiling and is edited by other cards too.
"""

import hashlib
from datetime import date
from decimal import Decimal

from app import ref_cache
from app.enums import StatementBalanceEvidenceEnum, StatementSourceEnum
from app.models.merchant import Merchant
from app.models.ref import AccountType
from app.models.statement_import import (
    AccountExternalIdentity,
    BankStatementLine,
    StatementImport,
    StatementLineSighting,
)
from app.services import account_service
from app.services.statement_import import (
    ParsedStatement,
    Provenance,
    bank_levels,
    covered_runs,
    parse_statement,
    record_parsed,
    record_statement,
)

from . import _csv_builder as build

_CSV = StatementSourceEnum.SECU_CHECKING_CSV
_FEED = StatementSourceEnum.SIMPLEFIN

#: Three lines from 100.00: -25.00, +1,500.00, -40.81, so the file's own chain
#: closes at 100.00 - 25.00 + 1,500.00 - 40.81 = 1,534.19 on 03-04, which is
#: the figure its header states by default.
_ENTRIES = [
    (date(2026, 3, 2), "-25.00",
     "POINT OF SALE DEBIT L340 COFFEE (Big Cheese Clayton)"),
    (date(2026, 3, 3), "1500.00",
     "ACH DEPOSIT TOWN OF CLAYTON  PAYROLL (TOWN OF CLAYTON PAYROLL)"),
    (date(2026, 3, 4), "-40.81",
     "POINT OF SALE DEBIT L340 FOOD LION (Food Lion)"),
]


def _file(account_number=build.ACCOUNT_NUMBER):
    """Return the three-line export, its running balances chained from 100.00."""
    return build.build(
        build.chained("100.00", _ENTRIES, account_number=account_number),
    )


def _upload(seed_user, payload, account=None):
    """Record *payload* through the upload door into the seeded checking account."""
    return record_statement(
        account_id=(account or seed_user["account"]).id,
        user_id=seed_user["user"].id,
        source=_CSV,
        file_name="statement.csv",
        payload=payload,
    )


def _provenance(source, file_name, body):
    """Return the act's provenance, its digest the SHA-256 of *body*."""
    return Provenance(
        source=source,
        file_name=file_name,
        file_digest=hashlib.sha256(body).hexdigest(),
    )


def _quiet(external_account_id, start, end, stated=None):
    """Return a statement with NO line over ``[start, end]``.

    Its claim, when *stated* is given, is as of the window's last day: what
    the feed's present window states on a quiet run.
    """
    return ParsedStatement(
        external_account_id=external_account_id,
        lines=[],
        declared_start=start,
        declared_end=end,
        stated_balance=None if stated is None else Decimal(stated),
        stated_balance_on=None if stated is None else end,
    )


def _second_account(seed_user):
    """Create a second checking account for the same owner."""
    return account_service.create_account(
        account_service.AccountSpec(
            user_id=seed_user["user"].id,
            account_type_id=(
                AccountType.query.filter_by(name="Checking").one().id
            ),
            name="Second Checking",
            anchor_balance=Decimal("0.00"),
            observed_on=date(2024, 1, 5),
        ),
    )


class TestTheTwoDoorsRecordTheSameImport:
    """The upload door is the reading plus the walk, and nothing else."""

    def test_every_row_the_walk_writes_agrees_column_by_column(
        self, app, db, seed_user,
    ):
        """The upload door on the seeded account, the walk on a second one.

        The same three lines under a second account NUMBER, because one owner
        may not pair two accounts to one external identity.  The import rows
        agree on every column but the account and the digest; the line rows,
        the sightings (merchants by NAME, since merchants are per account),
        the placed level and the coverage agree in full.
        """
        payload = _file()
        second = _second_account(seed_user)
        second_payload = _file(account_number="******9999")

        via_upload = _upload(seed_user, payload)
        via_walk = record_parsed(
            second.id,
            seed_user["user"].id,
            _provenance(_CSV, "statement.csv", second_payload),
            parse_statement(_CSV, second_payload),
        )

        rows = {row.account_id: row for row in StatementImport.query.all()}
        first, other = rows[seed_user["account"].id], rows[second.id]
        for column in (
            "user_id", "source_id", "file_name", "declared_start",
            "declared_end", "stated_balance", "stated_balance_on",
        ):
            assert getattr(first, column) == getattr(other, column), column
        assert first.file_digest == hashlib.sha256(payload).hexdigest()
        assert other.file_digest == hashlib.sha256(second_payload).hexdigest()
        assert (via_upload.line_count, via_upload.recorded_count) == (3, 3)
        assert (via_walk.line_count, via_walk.recorded_count) == (3, 3)
        assert via_upload.balance == via_walk.balance
        assert covered_runs(second.id) == covered_runs(seed_user["account"].id)

        def lines_of(account_id):
            return [
                (row.posted_on, row.amount, row.sequence_in_group)
                for row in BankStatementLine.query
                .filter_by(account_id=account_id)
                .order_by(BankStatementLine.posted_on, BankStatementLine.id)
            ]

        def sightings_of(account_id):
            return [
                (row.description, db.session.get(Merchant, row.merchant_id).name,
                 row.transaction_on, row.running_balance, row.source_category)
                for row in StatementLineSighting.query
                .filter_by(account_id=account_id)
                .order_by(StatementLineSighting.line_id)
            ]

        def level_of(account_id):
            [(level, release)] = bank_levels(account_id)
            return (
                level.anchor_balance, level.observed_on, level.evidence_id,
                release,
            )

        assert lines_of(second.id) == lines_of(seed_user["account"].id)
        assert sightings_of(second.id) == sightings_of(seed_user["account"].id)
        assert level_of(second.id) == level_of(seed_user["account"].id)
        # The level itself, worked: the file's chain closes at 1,534.19 on
        # 03-04 and its header states that figure, so it is placed there.
        assert level_of(second.id)[:2] == (Decimal("1534.19"), date(2026, 3, 4))


class TestTheWalkRecordsAStatementWithNoLine:
    """A quiet window is recorded: the act, its coverage and its claim."""

    def test_as_a_first_import_it_places_its_claim_assumed(
        self, app, db, seed_user,
    ):
        """Nothing recorded constrains the claim, so the first-import arm
        places 100.00 on the window's last day, 03-08, uncorroborated and
        ASSUMED.  The receipt reads ``(0, 0)`` off the one aggregate, and the
        identity the walk learned is recorded as any first import's is."""
        body = b'{"accounts": []}'
        outcome = record_parsed(
            seed_user["account"].id,
            seed_user["user"].id,
            _provenance(_FEED, "SimpleFIN 2026-03-02..2026-03-08", body),
            _quiet("ACT-quiet", date(2026, 3, 2), date(2026, 3, 8), "100.00"),
        )

        assert (outcome.line_count, outcome.recorded_count) == (0, 0)
        assert outcome.already_known == 0
        assert (outcome.declared_start, outcome.declared_end) == (
            date(2026, 3, 2), date(2026, 3, 8),
        )
        assert outcome.anchors_released == 0
        row = StatementImport.query.one()
        assert row.file_name == "SimpleFIN 2026-03-02..2026-03-08"
        assert row.file_digest == hashlib.sha256(body).hexdigest()
        assert row.source_id == ref_cache.statement_source_id(_FEED)
        assert (row.declared_start, row.declared_end) == (
            date(2026, 3, 2), date(2026, 3, 8),
        )
        assert (row.stated_balance, row.stated_balance_on) == (
            Decimal("100.00"), date(2026, 3, 8),
        )
        assert BankStatementLine.query.count() == 0
        assert StatementLineSighting.query.count() == 0
        assert covered_runs(seed_user["account"].id) == [
            (date(2026, 3, 2), date(2026, 3, 8)),
        ]
        [(level, release)] = bank_levels(seed_user["account"].id)
        assert level.statement_import_id == row.id
        assert (level.anchor_balance, level.observed_on) == (
            Decimal("100.00"), date(2026, 3, 8),
        )
        assert ref_cache.statement_balance_evidence_member(
            level.evidence_id,
        ) is StatementBalanceEvidenceEnum.UNCORROBORATED
        assert release is None
        assert outcome.balance.day_is_solved is False
        identity = AccountExternalIdentity.query.one()
        assert identity.external_account_id == "ACT-quiet"
        assert identity.feed_id is None

    def test_after_a_recorded_window_it_solves_its_claim(
        self, app, db, seed_user,
    ):
        """The upload places 1,534.19 at 03-04 from its own chain; a quiet
        window 03-05..03-11 states 1,534.19 as of 03-11.  Its opening (03-04)
        is priced at 1,534.19 and no line moves it, so the claim solves on
        the latest candidate, 03-11, held CORROBORATED: the recorded opening
        is capped there, two statements agreeing.  The coverage is one run."""
        _upload(seed_user, _file())

        outcome = record_parsed(
            seed_user["account"].id,
            seed_user["user"].id,
            _provenance(_CSV, "quiet.csv", b"quiet"),
            _quiet(
                build.ACCOUNT_IDENTITY, date(2026, 3, 5), date(2026, 3, 11),
                "1534.19",
            ),
        )

        assert outcome.balance.effective_on == date(2026, 3, 11)
        assert outcome.balance.day_is_solved is True
        assert outcome.balance.opening_known is True
        assert outcome.balance.evidence is (
            StatementBalanceEvidenceEnum.CORROBORATED
        )
        assert covered_runs(seed_user["account"].id) == [
            (date(2026, 3, 2), date(2026, 3, 11)),
        ]
        assert len(bank_levels(seed_user["account"].id)) == 2

    def test_after_a_coverage_GAP_it_records_its_claim_unsolved(
        self, app, db, seed_user,
    ):
        """Ruling **bank_import:R-BI35**, through the walk, on the feed's
        ordinary shape.  A level stands (1,534.19 at 03-04); the quiet window
        opens on 03-10 with 03-05..03-09 imported by nobody, so the day
        before it is a gap.  The claim is recorded verbatim, unsolved and
        placing no level, and the coverage holds both runs apart."""
        _upload(seed_user, _file())

        outcome = record_parsed(
            seed_user["account"].id,
            seed_user["user"].id,
            _provenance(_CSV, "after-a-gap.csv", b"gap"),
            _quiet(
                build.ACCOUNT_IDENTITY, date(2026, 3, 10), date(2026, 3, 16),
                "1534.19",
            ),
        )

        assert outcome.balance.stated == Decimal("1534.19")
        assert not outcome.balance.is_anchored
        assert outcome.balance.evidence is None
        assert outcome.balance.day_is_solved is False
        assert outcome.balance.opening_known is False
        [(level, _release)] = bank_levels(seed_user["account"].id)
        assert level.observed_on == date(2026, 3, 4)
        assert covered_runs(seed_user["account"].id) == [
            (date(2026, 3, 2), date(2026, 3, 4)),
            (date(2026, 3, 10), date(2026, 3, 16)),
        ]

    def test_with_no_claim_it_covers_its_window_and_places_nothing(
        self, app, db, seed_user,
    ):
        """A back-walk window's run: no line and no balance -- the act and
        its coverage, and no level."""
        outcome = record_parsed(
            seed_user["account"].id,
            seed_user["user"].id,
            _provenance(_FEED, "SimpleFIN 2026-03-02..2026-03-08", b"{}"),
            _quiet("ACT-quiet", date(2026, 3, 2), date(2026, 3, 8)),
        )

        assert outcome.balance is None
        assert (outcome.line_count, outcome.recorded_count) == (0, 0)
        assert StatementImport.query.count() == 1
        assert bank_levels(seed_user["account"].id) == []
        assert covered_runs(seed_user["account"].id) == [
            (date(2026, 3, 2), date(2026, 3, 8)),
        ]
