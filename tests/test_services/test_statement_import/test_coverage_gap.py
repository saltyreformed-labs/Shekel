"""A claim whose opening lies in a COVERAGE GAP is recorded unsolved, never assumed.

Ruling **bank_import:R-BI35** (plan step ``bank_import:X-f6b-2``): the import
door's ASSUMED arm -- a day guessed for a claim with nothing to check it
against -- fires for a TRUE first import only, meaning the account's record
prices no day at all.  When the record prices other days and does not reach
the day before a file's window (a span nobody imported lies between them), the
claim is recorded UNSOLVED and places no level.  Until the ruling the assumed
arm fired for both, so a CSV imported after a gap placed its header as a level
on a guessed day with nothing to expose a wrong figure.

The cases run the arm three ways: pure (:func:`resolve_anchor` over a stated
opening), through the one opening read that tells the three states apart
(:func:`window_opening`, one fold), and through the upload door end to end.
"""

from datetime import date
from decimal import Decimal

from app.enums import StatementBalanceEvidenceEnum, StatementSourceEnum
from app.services.statement_import import (
    BankBalances,
    BankAnchor,
    CoverageGap,
    KnownOpening,
    ParsedStatement,
    RecordedRun,
    StatementLine,
    bank_levels,
    record_statement,
    recorded_opening_before,
    resolve_anchor,
    window_opening,
)

from . import _csv_builder as build

_SOURCE = StatementSourceEnum.SECU_CHECKING_CSV
_UNCORROBORATED = StatementBalanceEvidenceEnum.UNCORROBORATED
_CORROBORATED = StatementBalanceEvidenceEnum.CORROBORATED


def _claiming(stated, stated_on, lines, start, end):
    """Return a statement with no running balance stating *stated* as of *stated_on*.

    No chain, so the file-chain arm never answers and the opening the caller
    hands :func:`resolve_anchor` decides.
    """
    return ParsedStatement(
        external_account_id="acct",
        lines=[
            StatementLine(
                posted_on=day, transaction_on=None, amount=Decimal(amount),
                description=f"LINE {index}",
            )
            for index, (day, amount) in enumerate(lines)
        ],
        declared_start=start,
        declared_end=end,
        stated_balance=Decimal(stated),
        stated_balance_on=stated_on,
    )


def _upload(seed_user, entries, stated, as_of, file_name):
    """Record a SECU export with no running-balance column through the upload door.

    The 10-column export the developer actually downloads, so the file states
    no chain of its own and its claim is resolved against the record.
    """
    return record_statement(
        account_id=seed_user["account"].id,
        user_id=seed_user["user"].id,
        source=_SOURCE,
        file_name=file_name,
        payload=build.build(
            build.chained("100.00", entries, with_running=False),
            stated_balance=stated, balance_as_of=as_of,
        ),
    )


#: The first file: 03-02 and 03-03, closing 100.00 - 25.00 + 1,500.00 =
#: 1,575.00, stated as of 03-03.  A first import, so its day is assumed.
_MARCH_2_TO_3 = [
    (date(2026, 3, 2), "-25.00", "POINT OF SALE DEBIT L340 COFFEE (Big Cheese)"),
    (date(2026, 3, 3), "1500.00", "ACH DEPOSIT PAYROLL (TOWN OF CLAYTON)"),
]


class TestTheArmIsPure:
    """:func:`resolve_anchor`'s answer for each kind of opening."""

    def test_a_GAP_records_the_claim_unsolved_and_says_no_opening_was_known(self):
        """The claim is kept verbatim and places no day: both level fields
        ``None`` together, the day not solved, and ``opening_known`` False --
        what the receipt reads to name the gap."""
        parsed = _claiming(
            "1571.00", date(2026, 3, 10), [(date(2026, 3, 10), "-4.00")],
            date(2026, 3, 10), date(2026, 3, 10),
        )

        balance = resolve_anchor(parsed, CoverageGap())

        assert balance.stated == Decimal("1571.00")
        assert balance.stated_on == date(2026, 3, 10)
        assert not balance.is_anchored
        assert balance.effective_on is None
        assert balance.evidence is None
        assert balance.day_is_solved is False
        assert balance.opening_known is False

    def test_NOTHING_priced_is_a_first_import_and_still_assumes_its_day(self):
        """``None`` is the record pricing no day: the day is ASSUMED, the
        window's end bounded by the header's day (03-10), uncorroborated --
        and it too had no opening, which is why it says so."""
        parsed = _claiming(
            "1571.00", date(2026, 3, 10), [(date(2026, 3, 10), "-4.00")],
            date(2026, 3, 10), date(2026, 3, 10),
        )

        balance = resolve_anchor(parsed, None)

        assert balance.effective_on == date(2026, 3, 10)
        assert balance.evidence is _UNCORROBORATED
        assert balance.day_is_solved is False
        assert balance.opening_known is False

    def test_a_PRICED_opening_is_known_whether_or_not_it_solves(self):
        """Opening 1,575.00: 1,571.00 - (-4.00) = 1,575.00 solves on 03-10;
        an opening of 1,000.00 solves on no candidate day.  Both had an
        opening to check against, so neither is the gap's sentence."""
        parsed = _claiming(
            "1571.00", date(2026, 3, 10), [(date(2026, 3, 10), "-4.00")],
            date(2026, 3, 10), date(2026, 3, 10),
        )

        solved = resolve_anchor(
            parsed, KnownOpening(Decimal("1575.00"), _CORROBORATED),
        )
        unsolved = resolve_anchor(
            parsed, KnownOpening(Decimal("1000.00"), _CORROBORATED),
        )

        assert solved.effective_on == date(2026, 3, 10)
        assert solved.opening_known is True
        assert unsolved.effective_on is None
        assert unsolved.opening_known is True


class TestTheOpeningReadTellsTheThreeStatesApart:
    """:func:`window_opening`: priced, a gap, or nothing priced -- one fold."""

    def test_an_account_with_no_import_is_priced_nowhere(
        self, app, db, seed_user,
    ):
        """Nothing recorded: ``None``, the first-import state."""
        assert window_opening(seed_user["account"].id, date(2026, 3, 2)) is None

    def test_a_day_the_run_reaches_is_priced_and_a_day_past_a_gap_is_a_gap(
        self, app, db, seed_user,
    ):
        """The first file's level is 1,575.00 at 03-03.  Before 03-04 the
        crossing is empty, so the opening is the level itself; before 03-10,
        03-04..03-09 were imported by nobody, so the record that prices
        03-03 does not reach 03-09.  The narrowing read reads the gap as
        ``None``, as it always did."""
        _upload(seed_user, _MARCH_2_TO_3, "1575.00", "03/03/2026", "march.csv")
        account_id = seed_user["account"].id

        priced = window_opening(account_id, date(2026, 3, 4))
        gap = window_opening(account_id, date(2026, 3, 10))

        assert priced == KnownOpening(Decimal("1575.00"), _UNCORROBORATED)
        assert isinstance(gap, CoverageGap)
        assert recorded_opening_before(account_id, date(2026, 3, 10)) is None

    def test_whether_any_day_is_priced_is_the_runs_answer(self):
        """An unanchored run prices nothing, so a fold of one prices no day;
        an anchored run prices at least its anchor's day.  Read off the runs,
        so a standing level that no run reaches cannot count."""
        unanchored = RecordedRun(date(2026, 3, 2), date(2026, 3, 3), None, ())
        anchored = RecordedRun(
            date(2026, 3, 2), date(2026, 3, 3),
            BankAnchor(
                day=date(2026, 3, 3), balance=Decimal("1575.00"),
                evidence=_UNCORROBORATED, file_name="march.csv",
            ),
            (),
        )

        assert not BankBalances(runs=[], balances={}).prices_any_day
        assert not BankBalances(runs=[unanchored], balances={}).prices_any_day
        assert BankBalances(
            runs=[unanchored, anchored], balances={},
        ).prices_any_day


class TestThroughTheUploadDoor:
    """The arm end to end: what is recorded, and what level stands after."""

    def test_a_file_after_a_GAP_records_its_claim_and_places_no_level(
        self, app, db, seed_user,
    ):
        """The first file places 1,575.00 at 03-03 (assumed: a first
        import).  The second opens on 03-10 with 03-04..03-09 imported by
        nobody and states 1,571.00 as of 03-10.  Before the ruling it was
        placed at 03-10 on a guess; now the claim is recorded on its row and
        the first file's level stands alone."""
        first = _upload(
            seed_user, _MARCH_2_TO_3, "1575.00", "03/03/2026", "march.csv",
        )
        after_gap = _upload(
            seed_user, [(date(2026, 3, 10), "-4.00", "POS DEBIT (Food Lion)")],
            "1571.00", "03/10/2026", "after-a-gap.csv",
        )

        assert first.balance.effective_on == date(2026, 3, 3)
        assert first.balance.evidence is _UNCORROBORATED
        assert after_gap.balance.stated == Decimal("1571.00")
        assert not after_gap.balance.is_anchored
        assert after_gap.balance.opening_known is False
        [(level, release)] = bank_levels(seed_user["account"].id)
        assert level.statement_import_id == first.import_id
        assert release is None

    def test_a_file_CONTIGUOUS_with_the_record_solves_against_it(
        self, app, db, seed_user,
    ):
        """The control: the second file opens on 03-04, the day after the
        first's level.  1,571.00 - (-4.00) = 1,575.00 is the opening, so the
        day solves on 03-04, held no stronger than the uncorroborated level
        behind it, and two levels stand."""
        _upload(seed_user, _MARCH_2_TO_3, "1575.00", "03/03/2026", "march.csv")
        next_day = _upload(
            seed_user, [(date(2026, 3, 4), "-4.00", "POS DEBIT (Food Lion)")],
            "1571.00", "03/04/2026", "march-4.csv",
        )

        assert next_day.balance.effective_on == date(2026, 3, 4)
        assert next_day.balance.day_is_solved is True
        assert next_day.balance.evidence is _UNCORROBORATED
        assert next_day.balance.opening_known is True
        assert len(bank_levels(seed_user["account"].id)) == 2

    def test_once_every_level_is_released_the_next_import_re_establishes_one(
        self, app, db, seed_user,
    ):
        """A released level prices nothing, so it must not keep the account
        out of the first-import arm, or no statement could place a level
        again.  The first file's level (03-03) is released by a second file
        that freshly records a line on 03-01, before it; that second file's
        own opening (02-28) is a gap, so it places nothing.  The account now
        holds no standing level, and a third file after a gap is resolved as
        a first import: assumed, placed at 03-20."""
        _upload(seed_user, _MARCH_2_TO_3, "1575.00", "03/03/2026", "march.csv")
        earlier = _upload(
            seed_user,
            [(date(2026, 3, 1), "10.00", "DEPOSIT (Branch)"),
             _MARCH_2_TO_3[0]],
            "85.00", "03/02/2026", "march-1.csv",
        )
        later = _upload(
            seed_user, [(date(2026, 3, 20), "-4.00", "POS DEBIT (Food Lion)")],
            "1581.00", "03/20/2026", "march-20.csv",
        )

        assert earlier.anchors_released == 1
        assert not earlier.balance.is_anchored
        assert earlier.balance.opening_known is False
        assert later.balance.effective_on == date(2026, 3, 20)
        assert later.balance.evidence is _UNCORROBORATED
        assert later.balance.day_is_solved is False
        standing = [
            level for level, release in bank_levels(seed_user["account"].id)
            if release is None
        ]
        assert [level.statement_import_id for level in standing] == [
            later.import_id,
        ]
