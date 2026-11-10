"""Which day is a statement's stated balance for, and how firmly is it held?

Plan step **bank_import:X-f6e-1**, ruling **R-GF**.  A bank writes its balance
as of the EXPORT INSTANT and labels it with the export's own day, so the header
day is not the day the figure is for -- and the file's own lines are what solve
it.

**Every case below is a shape MEASURED on the developer's real exports**, and
each one is a different answer, which is why the arms are separable at all:

* 2026-08-22 -- header ``2459.60`` as of 08-22, last line 08-21, solves at
  08-21.  The ordinary export.
* 2026-08-16 -- header ``4747.63`` as of 08-16 over a file listing two 08-14
  lines worth ``-1006.72``; the figure is 08-13's closing and solves there.
  The LAG.
* 2026-01-02..2026-03-31 pulled 2026-08-23 -- header ``2459.60`` as of 08-23,
  145 days past the last line and ``$255.41`` from the ``$2,715.01`` its own
  139 lines imply.  Solves nowhere, and refusing it would reject an honest
  export.  The RANGE.

**The refusal arm is a FIRING CONTROL** (``docs/plans/verification.md``
standard 4): nothing in ordinary use reaches it, because the only files that do
are ones whose own running-balance chain contradicts their own header, so a
test asserting only that good files pass would pass equally against a solver
that never refused.
"""

from datetime import date
from decimal import Decimal

import pytest

from app import ref_cache
from app.enums import StatementBalanceEvidenceEnum, StatementSourceEnum
from app.models.account import AccountAnchorHistory
from app.models.statement_import import (
    BankStatementLine,
    StatementImport,
    StatementLineSighting,
)
from app.services.statement_import import (
    KnownOpening,
    StatementBalanceUnexplained,
    ParsedStatement,
    StatementLine,
    recorded_opening_before,
    release_anchors_from,
    resolve_anchor,
    solve_effective_day,
    weaker_of,
)
# The package's partition of the release into a fetch and a predicate has no
# importer outside the package, so exporting the pair from
# ``statement_import.__init__`` would be the public surface ``CLAUDE.md``
# rule 13 forbids.  Reaching into it from the module's own tests is the
# allowance ``test_merchant_schema`` takes for ``_merchants``.
from app.services.statement_import._anchor import resting_on
from app.services.statement_import._balance import (
    bank_levels,
    standing_bank_levels,
)

_FILE_CHAIN = StatementBalanceEvidenceEnum.FILE_CHAIN
_CORROBORATED = StatementBalanceEvidenceEnum.CORROBORATED
_UNCORROBORATED = StatementBalanceEvidenceEnum.UNCORROBORATED


def _parsed(lines, stated, stated_on, *, window=None):
    """Return the file *lines* came from, declaring *window* or its extremes.

    The shape :func:`solve_effective_day` and :func:`resolve_anchor` take
    since plan step ``bank_import:X-f6b-1``: the claim and the declared
    window ride with the lines.  A CSV declares its first..last line day, so
    that is the default; a case about a QUIET day inside a declared window
    states one wider than its lines.
    """
    days = [line.posted_on for line in lines]
    start, end = window or (min(days), max(days))
    return ParsedStatement(
        external_account_id="X",
        lines=lines,
        declared_start=start,
        declared_end=end,
        stated_balance=stated,
        stated_balance_on=stated_on,
    )


def _line(day, amount, running=None):
    """Return one line, optionally carrying a running balance."""
    return StatementLine(
        posted_on=day,
        transaction_on=None,
        amount=Decimal(amount),
        description="X",
        merchant=None,
        source_category=None,
        external_id=None,
        running_balance=None if running is None else Decimal(running),
    )


def _chain(opening, moves, first_day=date(2026, 3, 1)):
    """Return lines whose running balances FOLLOW from *opening*, one per day."""
    balance = Decimal(opening)
    built = []
    for offset, amount in enumerate(moves):
        balance += Decimal(amount)
        built.append(
            _line(date.fromordinal(first_day.toordinal() + offset),
                  amount, balance)
        )
    return built


def _plain(moves, first_day=date(2026, 3, 1)):
    """Return lines with NO running balance -- every modern SECU export."""
    return [
        _line(date.fromordinal(first_day.toordinal() + offset), amount)
        for offset, amount in enumerate(moves)
    ]


def _known(amount, evidence=_CORROBORATED):
    """Return a :class:`KnownOpening` for a solve to run against."""
    return KnownOpening(amount=Decimal(amount), evidence=evidence)


def _seed_import(db, account, *, stated=None, effective_on=None,
                 evidence=None, lines=(), period=None, file_name="seed.csv"):
    """Record one import and its lines directly, anchored or not.

    Built through the models rather than through ``record_statement``, and
    deliberately: these exercise the WALK and the RELEASE over what is stored,
    so constructing the stored state directly keeps their tests from also being
    tests of the door that writes it.  A placed figure is a LEVEL row naming
    the import (plan step ``balance:X-bj-1``), written here as the door
    writes it.

    Args:
        db: The session fixture.
        account: The account to record against.
        stated: The header figure, or ``None`` for a file stating none.
        effective_on: The day it is placed at, or ``None`` for unanchored.
        evidence: The evidence member, required with *effective_on*.
        lines: ``(day, amount)`` pairs, chronological.
        period: The declared window ``(start, end)``, defaulting to the
            lines' own extremes -- what a CSV declares.
        file_name: Provenance, and the digest's seed.

    Returns:
        The staged :class:`~app.models.statement_import.StatementImport`.
        Its level, when it placed one, is :func:`_placement`'s answer.
    """
    days = [day for day, _ in lines]
    start, end = period or (min(days), max(days))
    row = StatementImport(
        account_id=account.id,
        user_id=account.user_id,
        source_id=ref_cache.statement_source_id(
            StatementSourceEnum.SECU_CHECKING_CSV
        ),
        file_name=file_name,
        file_digest=file_name.ljust(64, "0")[:64],
        declared_start=start,
        declared_end=end,
        stated_balance=None if stated is None else Decimal(stated),
        stated_balance_on=None if stated is None else (effective_on or end),
    )
    db.session.add(row)
    db.session.flush()
    if effective_on is not None:
        db.session.add(AccountAnchorHistory(
            account_id=account.id,
            anchor_balance=Decimal(stated),
            observed_on=effective_on,
            evidence_id=ref_cache.statement_balance_evidence_id(evidence),
            statement_import_id=row.id,
        ))
        db.session.flush()
    for ordinal, (day, amount) in enumerate(lines):
        line = BankStatementLine(
            account_id=account.id,
            posted_on=day,
            amount=Decimal(amount),
            sequence_in_group=ordinal,
        )
        db.session.add(line)
        db.session.flush()
        db.session.add(StatementLineSighting(
            account_id=account.id, line_id=line.id, import_id=row.id,
            description="X",
        ))
    db.session.flush()
    return row


def _placement(db, statement_import):
    """Return ``(level, release)`` for one import, or ``None`` when unplaced.

    The page's own read (:func:`~app.services.statement_import._balance
    .bank_levels`), narrowed to one import, so a test asks the relation what
    stands rather than a column that no longer exists.
    """
    db.session.flush()
    for level, release in bank_levels(statement_import.account_id):
        if level.statement_import_id == statement_import.id:
            return level, release
    return None


class TestTheSolveFindsTheDayTheFigureIsFor:
    """``stated - sum(lines up to d) == opening``, over the file's own days."""

    def test_a_figure_matching_the_last_day_solves_there(self):
        """The ordinary export: the header is the closing after everything.

        Arithmetic: opening 1000.00, then +100.00, -40.00, +25.00, so the
        cumulative figures are 1100.00 / 1060.00 / 1085.00 and only the last
        matches.
        """
        lines = _plain(["100.00", "-40.00", "25.00"])

        assert solve_effective_day(
            _parsed(lines, Decimal("1085.00"), date(2026, 3, 9)),
            Decimal("1000.00")) == date(2026, 3, 3,
        )

    def test_a_figure_LAGGING_its_own_file_solves_at_the_EARLIER_day(self):
        """The 2026-08-16 shape, in miniature.

        The header states a balance the file's own tail has already moved past
        -- 1060.00 is the 03-02 cumulative, not the 03-03 one.  Nothing is
        wrong with the file; the bank computed the figure before it ledgered
        the last day.
        """
        lines = _plain(["100.00", "-40.00", "25.00"])

        assert solve_effective_day(
            _parsed(lines, Decimal("1060.00"), date(2026, 3, 9)),
            Decimal("1000.00")) == date(2026, 3, 2,
        )

    def test_a_figure_equal_to_the_opening_solves_BEFORE_the_first_line(self):
        """The day before the first line is a candidate in its own right."""
        lines = _plain(["100.00", "-40.00"])

        assert solve_effective_day(
            _parsed(lines, Decimal("1000.00"), date(2026, 3, 9)),
            Decimal("1000.00")) == date(2026, 2, 28,
        )

    def test_a_figure_no_day_reaches_solves_NOWHERE(self):
        """The range-export shape: the answer is None, not a nearest day."""
        lines = _plain(["100.00", "-40.00", "25.00"])

        assert solve_effective_day(
            _parsed(lines, Decimal("2459.60"), date(2026, 8, 23)),
            Decimal("1000.00"),
        ) is None

    def test_two_solving_days_take_the_LATER_one(self):
        """Harmless, and PROVEN so rather than assumed.

        Two satisfying days differ by lines summing to exactly zero, so every
        balance the app later derives is identical either way; taking the later
        is a choice about which day to NAME.  Here 03-02 and 03-03 both satisfy
        it because -40.00 and +40.00 cancel between them.
        """
        lines = _plain(["100.00", "-40.00", "40.00"])

        assert solve_effective_day(
            _parsed(lines, Decimal("1100.00"), date(2026, 3, 9)),
            Decimal("1000.00")) == date(2026, 3, 3,
        )

    def test_no_candidate_may_be_AFTER_the_day_the_header_names(self):
        """A bank cannot state a balance for a day it has not reached.

        **The bound is what stops a 500**: without it the solve returned 03-03
        under a header dated 03-02, and
        ``ck_statement_imports_effective_day_within_file`` refused the row at
        flush -- so a file the app could describe exactly became "Something
        went wrong saving this statement".  Found by two independent
        adversarial reviews, 2026-08-23.

        Here 1085.00 solves at 03-03 and at no earlier day, so bounding at
        03-02 must yield nothing rather than the later day.
        """
        lines = _plain(["100.00", "-40.00", "25.00"])

        assert solve_effective_day(
            _parsed(lines, Decimal("1085.00"), date(2026, 3, 2)),
            Decimal("1000.00"),
        ) is None

    def test_the_bound_also_covers_the_day_BEFORE_the_first_line(self):
        """The extra candidate is bounded too, or it escapes the same check."""
        lines = _plain(["100.00"])

        assert solve_effective_day(
            _parsed(lines, Decimal("1000.00"), date(2026, 2, 27)),
            Decimal("1000.00"),
        ) is None

    def test_a_QUIET_day_after_the_solving_line_day_is_NOT_named(self):
        """The line day, never the quiet day after it (ruling **R-BAL74**).

        Lines 03-01 +100, 03-02 -40, 03-04 +25 with 03-03 quiet; 1060.00 is
        03-02's closing and 03-03's too, because nothing moved.  The bank's
        walk cannot tell the two apart; the CASH walk can, because it
        absorbs an app row settled on 03-03 into a level dated 03-03 and not
        into one dated 03-02.  The build's first cut named 03-03 -- every
        window day was a candidate and the latest won -- and its docstring
        said it could not happen.  THE FIRING CONTROL for the candidate set.
        """
        lines = [
            _line(date(2026, 3, 1), "100.00"),
            _line(date(2026, 3, 2), "-40.00"),
            _line(date(2026, 3, 4), "25.00"),
        ]

        assert solve_effective_day(
            _parsed(lines, Decimal("1060.00"), date(2026, 3, 9)),
            Decimal("1000.00"),
        ) == date(2026, 3, 2)

    def test_a_window_WIDER_than_its_lines_lands_on_the_STATED_day(self):
        """The sync case R-BAL71 was ruled for, with lines in the window (R-BAL74).

        A window 03-01..03-10 holding one line on 03-02; the header states
        1100.00 (the closing after it) as of 03-08.  The figure is the
        balance at the end of 03-08 too, and 03-08 is the day the bank
        stated it, so 03-08 is named -- not 03-02, and not 03-10, which the
        bank had not reached.
        """
        lines = [_line(date(2026, 3, 2), "100.00")]

        assert solve_effective_day(
            _parsed(
                lines, Decimal("1100.00"), date(2026, 3, 8),
                window=(date(2026, 3, 1), date(2026, 3, 10)),
            ),
            Decimal("1000.00"),
        ) == date(2026, 3, 8)

    def test_a_window_with_NO_line_lands_on_the_stated_day_or_nowhere(self):
        """A quiet sync: the figure IS the opening, or the file says nothing."""
        placed = resolve_anchor(
            ParsedStatement(
                external_account_id="X", lines=[],
                declared_start=date(2026, 3, 1), declared_end=date(2026, 3, 7),
                stated_balance=Decimal("1000.00"),
                stated_balance_on=date(2026, 3, 7),
            ),
            _known("1000.00", _CORROBORATED),
        )
        unplaced = resolve_anchor(
            ParsedStatement(
                external_account_id="X", lines=[],
                declared_start=date(2026, 3, 1), declared_end=date(2026, 3, 7),
                stated_balance=Decimal("1001.00"),
                stated_balance_on=date(2026, 3, 7),
            ),
            _known("1000.00", _CORROBORATED),
        )

        assert placed.effective_on == date(2026, 3, 7)
        assert placed.evidence is _CORROBORATED
        assert placed.day_is_solved
        assert unplaced.effective_on is None
        assert not unplaced.is_anchored


class TestTheEvidenceIsTheWeakestLinkInTheChain:
    """A solved day is only as good as the opening it was solved against."""

    def test_a_running_balance_PROVES_it_from_the_file_itself(self):
        """``file_chain``: nothing outside the file is consulted."""
        lines = _chain("1000.00", ["100.00", "-40.00"])

        balance = resolve_anchor(
            _parsed(lines, Decimal("1060.00"), date(2026, 3, 5)),
            None,
        )

        assert balance.evidence is _FILE_CHAIN
        assert balance.effective_on == date(2026, 3, 2)
        assert balance.is_anchored

    def test_a_recorded_opening_CORROBORATES_it(self):
        """Two statements agreeing, when the one behind it is itself proved."""
        lines = _plain(["100.00", "-40.00"])

        balance = resolve_anchor(
            _parsed(lines, Decimal("1060.00"), date(2026, 3, 5)),
            _known("1000.00", _CORROBORATED),
        )

        assert balance.evidence is _CORROBORATED
        assert balance.effective_on == date(2026, 3, 2)

    def test_solving_against_an_UNCORROBORATED_opening_stays_uncorroborated(
        self,
    ):
        """The whole point of the ladder, and the defect it removes.

        **Reproduced in two clicks by adversarial review, 2026-08-23**:
        re-uploading the identical file made the app walk back to its own
        assumption, find that the file agreed with it, and record the result as
        corroborated -- the assumption checking itself.  The DAY here is
        genuinely solved; the opening behind it was not, so the answer is not.
        """
        lines = _plain(["100.00", "-40.00"])

        balance = resolve_anchor(
            _parsed(lines, Decimal("1060.00"), date(2026, 3, 5)),
            _known("1000.00", _UNCORROBORATED),
        )

        assert balance.effective_on == date(2026, 3, 2)
        assert balance.evidence is _UNCORROBORATED

    def test_with_NOTHING_to_check_against_it_is_UNCORROBORATED(self):
        """A FIRST import, and the receipt says so."""
        lines = _plain(["100.00", "-40.00"])

        balance = resolve_anchor(
            _parsed(lines, Decimal("9999.99"), date(2026, 3, 5)),
            None,
        )

        assert balance.evidence is _UNCORROBORATED
        assert balance.effective_on == date(2026, 3, 2)

    def test_the_assumed_day_is_BOUNDED_by_the_day_the_header_names(self):
        """The other arm the CHECK constraint refused, and it had no guard.

        ``effective_on`` was ``lines[-1].posted_on`` with no comparison to the
        header's day at all, so a file listing a line past its own header --
        a bank showing a future-dated item -- raised ``IntegrityError`` inside
        the door's flush and reached the owner as a 500.  Found by two
        independent adversarial reviews, 2026-08-23.
        """
        lines = _plain(["100.00", "-40.00", "25.00"])

        balance = resolve_anchor(
            _parsed(lines, Decimal("2000.00"), date(2026, 3, 1)),
            None,
        )

        assert balance.effective_on == date(2026, 3, 1)
        assert balance.effective_on <= balance.stated_on

    def test_the_CHAIN_wins_where_both_openings_are_known(self):
        """It needs nothing outside the file, so it is the one used.

        The recorded opening here would solve at a DIFFERENT day, so the
        precedence is observable rather than a tie-break over one answer.
        """
        lines = _chain("1000.00", ["100.00", "-40.00"])

        balance = resolve_anchor(
            _parsed(lines, Decimal("1060.00"), date(2026, 3, 5)),
            _known("1100.00"),
        )

        assert balance.evidence is _FILE_CHAIN
        assert balance.effective_on == date(2026, 3, 2)

    def test_a_file_stating_no_balance_determines_nothing(self):
        """No claim, so no anchor and no refusal."""
        assert resolve_anchor(
            _parsed(_plain(["100.00"]), None, None),
            _known("1000.00"),
        ) is None

    def test_a_figure_with_no_DAY_determines_nothing_either(self):
        """Total over its inputs rather than resting on the adapter's pairing.

        The adapter reads the two as one fact, so this is unreachable through
        the door -- and a function whose whole job is to be total may not rest
        a guard on another module's invariant.  Without it the arms would
        ``TypeError`` on the bound comparison.  Found by adversarial review
        2026-08-23.
        """
        assert resolve_anchor(
            _parsed(_plain(["100.00"]), Decimal("500.00"), None),
            _known("1000.00"),
        ) is None


class TestTheWeakestLinkRule:
    """One comparison, stated once, over the enum's own ladder."""

    def test_it_returns_the_weaker_of_two_levels(self):
        """Both orders, so an argument swap cannot pass."""
        assert weaker_of(_FILE_CHAIN, _UNCORROBORATED) is _UNCORROBORATED
        assert weaker_of(_UNCORROBORATED, _FILE_CHAIN) is _UNCORROBORATED
        assert weaker_of(_FILE_CHAIN, _CORROBORATED) is _CORROBORATED
        assert weaker_of(_CORROBORATED, _FILE_CHAIN) is _CORROBORATED

    def test_equal_levels_return_that_level(self):
        """The identity case, which the minimum must not disturb."""
        assert weaker_of(_CORROBORATED, _CORROBORATED) is _CORROBORATED

    def test_the_ladder_is_strictly_ordered(self):
        """Proved by the file beats corroborated beats nothing.

        Asserted here rather than trusted because the bank walk picks each
        run's anchor by this order (``_balance._strongest_then_latest``), and
        an early draft read the order off the ref table's row ids instead --
        which was measured BACKWARDS, since the seed writes ``file_chain``
        first.
        """
        assert _UNCORROBORATED.strength < _CORROBORATED.strength
        assert _CORROBORATED.strength < _FILE_CHAIN.strength


class TestAFileMayStateABalanceItsOwnLinesCannotReach:
    """The RANGE export, which a refusal would have rejected."""

    def test_a_header_the_lines_cannot_reach_records_the_claim_and_no_anchor(
        self,
    ):
        """The claim is real; the placement is undeterminable, and says so."""
        lines = _plain(["100.00", "-40.00"])

        balance = resolve_anchor(
            _parsed(lines, Decimal("2459.60"), date(2026, 8, 23)),
            _known("1000.00"),
        )

        assert balance.stated == Decimal("2459.60")
        assert balance.stated_on == date(2026, 8, 23)
        assert balance.effective_on is None
        assert balance.evidence is None
        assert not balance.is_anchored

    def test_a_mismatch_against_RECORDED_history_is_never_the_files_fault(
        self,
    ):
        """No refusal on this arm, whatever the header's day.

        **An earlier draft refused here** when the header sat on or before the
        file's last line, and an adversarial review reproduced it rejecting an
        honest export and telling the owner to delete an import -- blaming the
        file for the app's own stale anchor.  Only the file's own CHAIN can
        convict the file.
        """
        lines = _plain(["100.00", "-40.00"])

        balance = resolve_anchor(
            _parsed(lines, Decimal("9999.99"), date(2026, 3, 1)),
            _known("1000.00"),
        )

        assert not balance.is_anchored


class TestAFileThatCONTRADICTSItselfIsRefused:
    """The firing control, and the ONLY refusal: the chain against the header.

    A per-line running balance states what the account held on every day the
    file covers, so a header figure the chain reaches on no such day is the
    file disagreeing with itself -- which needs no evidence from outside it and
    is the one disagreement a re-export can be expected to fix.
    """

    def test_a_chain_that_reaches_the_header_on_no_day_is_refused(self):
        """The whole arm."""
        lines = _chain("1000.00", ["100.00", "-40.00"])

        with pytest.raises(StatementBalanceUnexplained) as raised:
            resolve_anchor(
                _parsed(lines, Decimal("9999.99"), date(2026, 3, 9)),
                None,
            )

        assert raised.value.stated == Decimal("9999.99")
        assert raised.value.implied == Decimal("1060.00")

    def test_the_refusal_carries_BOTH_figures(self):
        """The pair is what says the file disagrees with ITSELF."""
        lines = _chain("1000.00", ["100.00"])

        with pytest.raises(StatementBalanceUnexplained) as raised:
            resolve_anchor(
                _parsed(lines, Decimal("500.00"), date(2026, 3, 5)),
                None,
            )

        assert raised.value.stated == Decimal("500.00")
        assert raised.value.implied == Decimal("1100.00")
        assert "500.00" in str(raised.value)
        assert "1100.00" in str(raised.value)

    def test_a_chained_file_whose_header_merely_LAGS_is_NOT_refused(self):
        """The complement, or the arm above passes for the wrong reason.

        A header the chain DOES reach, just not on the last day, is an
        ordinary file -- the 2026-08-16 shape.
        """
        lines = _chain("1000.00", ["100.00", "-40.00"])

        balance = resolve_anchor(
            _parsed(lines, Decimal("1100.00"), date(2026, 3, 9)),
            None,
        )

        assert balance.effective_on == date(2026, 3, 1)
        assert balance.evidence is _FILE_CHAIN


class TestTheRecordedHistoryWalk:
    """What this account's OWN recorded statements say it held before a day."""

    def test_it_answers_NOTHING_for_an_account_with_no_anchored_import(
        self, app, db, seed_user,
    ):
        """A first import has nothing to check against, and says so."""
        assert recorded_opening_before(
            seed_user["account"].id, date(2026, 3, 1),
        ) is None

    def test_an_UNANCHORED_import_is_never_selected_as_the_anchor(
        self, app, db, seed_user,
    ):
        """The filter that stops a date-range export becoming the anchor.

        Without it the anchor selection returned a row whose placed day was
        ``None`` and the coverage test raised ``TypeError`` comparing a date
        against it -- a 500.  Found by adversarial review 2026-08-23.  (The
        selection moved into ``_balance`` at plan step
        ``bank_import:X-f6e-2``, became per run at ``balance:X-bj-1b``, and
        since ``balance:X-bj-1`` an unplaced import simply owns no level row;
        this reaches it through the public reader that consumes it.)
        """
        _seed_import(
            db, seed_user["account"], stated="2459.60", effective_on=None,
            evidence=None, lines=[(date(2026, 3, 1), "100.00")],
        )

        assert recorded_opening_before(
            seed_user["account"].id, date(2026, 3, 5),
        ) is None

    def test_it_walks_FORWARD_from_an_anchor_over_the_recorded_lines(
        self, app, db, seed_user,
    ):
        """Everything through the anchor's day is already inside its figure.

        Anchor: 1085.00 at 03-03 over +100.00, -40.00, +25.00.  Asked for the
        balance before 03-04, the two sums cancel and the answer is the anchor
        itself.
        """
        _seed_import(
            db, seed_user["account"], stated="1085.00",
            effective_on=date(2026, 3, 3), evidence=_FILE_CHAIN,
            lines=[(date(2026, 3, 1), "100.00"),
                   (date(2026, 3, 2), "-40.00"),
                   (date(2026, 3, 3), "25.00")],
        )

        known = recorded_opening_before(
            seed_user["account"].id, date(2026, 3, 4),
        )

        assert known.amount == Decimal("1085.00")

    def test_it_walks_BACKWARD_with_no_branch_deciding_the_sign(
        self, app, db, seed_user,
    ):
        """A day BEFORE the anchor subtracts, out of the same subtraction.

        Before 03-02: 1085.00 less the 03-02 (-40.00) and 03-03 (+25.00) lines
        is 1100.00.  Before the first line at all: less +100.00 too, so
        1000.00.
        """
        _seed_import(
            db, seed_user["account"], stated="1085.00",
            effective_on=date(2026, 3, 3), evidence=_FILE_CHAIN,
            lines=[(date(2026, 3, 1), "100.00"),
                   (date(2026, 3, 2), "-40.00"),
                   (date(2026, 3, 3), "25.00")],
        )
        account_id = seed_user["account"].id

        assert recorded_opening_before(
            account_id, date(2026, 3, 2),
        ).amount == Decimal("1100.00")
        assert recorded_opening_before(
            account_id, date(2026, 3, 1),
        ).amount == Decimal("1000.00")

    def test_the_evidence_is_CAPPED_at_corroborated(self, app, db, seed_user):
        """Agreeing with a proved anchor is corroboration, not proof.

        The file being imported states no chain of its own, so it cannot
        inherit ``file_chain`` however strong the anchor behind it is.
        """
        _seed_import(
            db, seed_user["account"], stated="1100.00",
            effective_on=date(2026, 3, 1), evidence=_FILE_CHAIN,
            lines=[(date(2026, 3, 1), "100.00")],
        )

        known = recorded_opening_before(
            seed_user["account"].id, date(2026, 3, 2),
        )

        assert known.evidence is _CORROBORATED

    def test_the_evidence_is_no_STRONGER_than_the_anchor_behind_it(
        self, app, db, seed_user,
    ):
        """The weakest link, at the database tier rather than in a unit."""
        _seed_import(
            db, seed_user["account"], stated="1100.00",
            effective_on=date(2026, 3, 1), evidence=_UNCORROBORATED,
            lines=[(date(2026, 3, 1), "100.00")],
        )

        known = recorded_opening_before(
            seed_user["account"].id, date(2026, 3, 2),
        )

        assert known.evidence is _UNCORROBORATED

    def test_the_STRONGEST_anchor_is_chosen_not_the_most_recent(
        self, app, db, seed_user,
    ):
        """Recency is only the tie-break, and the comment saying otherwise was
        refuted.

        Two anchors on one account: a proved one at 03-01 and a later
        uncorroborated one at 03-02.  ``created_at`` is the IMPORT ACT's time,
        so ordering by it would take the weaker.  Walking from the proved
        anchor gives 1100.00 and corroboration; from the weaker one it would
        give 1150.00 and no corroboration.
        """
        account = seed_user["account"]
        _seed_import(
            db, account, stated="1100.00", effective_on=date(2026, 3, 1),
            evidence=_FILE_CHAIN, lines=[(date(2026, 3, 1), "100.00")],
            file_name="proved.csv",
        )
        _seed_import(
            db, account, stated="1150.00", effective_on=date(2026, 3, 2),
            evidence=_UNCORROBORATED, lines=[(date(2026, 3, 2), "50.00")],
            file_name="assumed.csv",
        )

        known = recorded_opening_before(account.id, date(2026, 3, 2))

        assert known.amount == Decimal("1100.00")
        assert known.evidence is _CORROBORATED

    def test_a_GAP_in_coverage_answers_NOTHING_rather_than_a_wrong_figure(
        self, app, db, seed_user,
    ):
        """ONE uncovered day is enough, which is the boundary that had no test.

        The anchor's import spans 03-01..03-03; 03-04 is covered by nothing, so
        a walk to 03-05 crosses a day whose lines nobody has imported.
        Answering ``None`` sends the caller to ``uncorroborated``, which the
        receipt SAYS.  A pre-existing test asked this three MONTHS past the
        boundary and so could not see an off-by-one; mutating the comparison
        to ``>=`` left the whole suite green.  Found by adversarial review
        2026-08-23.
        """
        _seed_import(
            db, seed_user["account"], stated="1100.00",
            effective_on=date(2026, 3, 3), evidence=_FILE_CHAIN,
            lines=[(date(2026, 3, 1), "100.00"),
                   (date(2026, 3, 3), "0.01")],
            period=(date(2026, 3, 1), date(2026, 3, 3)),
        )

        assert recorded_opening_before(
            seed_user["account"].id, date(2026, 3, 5),
        ) is None

    def test_the_day_IMMEDIATELY_after_the_span_is_still_covered(
        self, app, db, seed_user,
    ):
        """The other side of that boundary, or the arm above passes vacuously.

        Asked for the balance before 03-04, the days strictly between the
        anchor (03-03) and it are EMPTY, so nothing can be missing.
        """
        _seed_import(
            db, seed_user["account"], stated="1100.00",
            effective_on=date(2026, 3, 3), evidence=_FILE_CHAIN,
            lines=[(date(2026, 3, 1), "100.00"),
                   (date(2026, 3, 3), "0.01")],
            period=(date(2026, 3, 1), date(2026, 3, 3)),
        )

        assert recorded_opening_before(
            seed_user["account"].id, date(2026, 3, 4),
        ) is not None

    def test_it_sums_only_ITS_OWN_accounts_lines(
        self, app, db, seed_user, seed_second_user,
    ):
        """A real second account holding its own anchor and its own lines.

        **The previous version of this test could not fail.**  It asked for a
        NON-EXISTENT account id, so the anchor query returned ``None`` and
        execution never reached either sum -- deleting the account filter from
        both left the whole suite green while the walk summed every account's
        lines in the database into one owner's balance.  Found by adversarial
        review 2026-08-23.
        """
        _seed_import(
            db, seed_user["account"], stated="1100.00",
            effective_on=date(2026, 3, 1), evidence=_FILE_CHAIN,
            lines=[(date(2026, 3, 1), "100.00")],
        )
        _seed_import(
            db, seed_second_user["account"], stated="9000.00",
            effective_on=date(2026, 3, 1), evidence=_FILE_CHAIN,
            lines=[(date(2026, 3, 1), "8000.00")],
            file_name="other.csv",
        )

        known = recorded_opening_before(
            seed_user["account"].id, date(2026, 3, 2),
        )

        # 1100.00, not 1100.00 plus the other account's 8000.00 line.
        assert known.amount == Decimal("1100.00")


    def test_a_file_continuing_an_ASSUMED_run_opens_from_THAT_runs_anchor(
        self, app, db, seed_user,
    ):
        """Ruling **R-BAL66**: the anchor that priced the day is the cap.

        January holds a proved level; March, across an unimported February,
        holds an assumed 500.00 at 03-20.  A file starting 03-21 opens at
        500.00 -- walked from March's own anchor -- and is held
        ``uncorroborated``, never stronger than that anchor.  Before plan
        step ``balance:X-bj-1b`` the account-wide walk from the proved
        January level could not reach 03-20, so this file got no opening and
        a GUESSED day; it gets a SOLVED one now.
        """
        account = seed_user["account"]
        _seed_import(
            db, account, stated="1000.00", effective_on=date(2026, 1, 31),
            evidence=_FILE_CHAIN, lines=[(date(2026, 1, 31), "10.00")],
            file_name="january.csv",
        )
        _seed_import(
            db, account, stated="500.00", effective_on=date(2026, 3, 20),
            evidence=_UNCORROBORATED, file_name="march.csv",
            lines=[(date(2026, 3, 20), "-5.00")],
        )

        known = recorded_opening_before(account.id, date(2026, 3, 21))

        assert known.amount == Decimal("500.00")
        assert known.evidence is _UNCORROBORATED

    def test_a_file_starting_inside_a_GAP_still_opens_on_nothing(
        self, app, db, seed_user,
    ):
        """Two anchored runs either side of a gap reach nothing inside it."""
        account = seed_user["account"]
        _seed_import(
            db, account, stated="1000.00", effective_on=date(2026, 1, 31),
            evidence=_FILE_CHAIN, lines=[(date(2026, 1, 31), "10.00")],
            file_name="january.csv",
        )
        _seed_import(
            db, account, stated="500.00", effective_on=date(2026, 3, 20),
            evidence=_UNCORROBORATED, file_name="march.csv",
            lines=[(date(2026, 3, 20), "-5.00")],
        )

        assert recorded_opening_before(account.id, date(2026, 3, 10)) is None

    def test_a_DISAGREEING_checkpoint_between_anchor_and_opening_changes_nothing(
        self, app, db, seed_user,
    ):
        """Ruling **R-BAL66**: reported on the page, and a gate on nothing.

        Anchor: proved 1000.00 at 03-05 over +100.00 (03-01), -40.00
        (03-03), +25.00 (03-05).  A statement that assumed 1075.00 for 03-03
        sits between the anchor and the opening day and is off by -100.00.
        The balance before 03-02 is still the anchor's walk, 1015.00, and
        still ``corroborated``: the checkpoint is the weaker figure by rank,
        and the comparison is an instrument, never a gate (**R-GF**).
        """
        account = seed_user["account"]
        _seed_import(
            db, account, stated="1000.00", effective_on=date(2026, 3, 5),
            evidence=_FILE_CHAIN, file_name="ytd.csv",
            lines=[(date(2026, 3, 1), "100.00"),
                   (date(2026, 3, 3), "-40.00"),
                   (date(2026, 3, 5), "25.00")],
        )
        _seed_import(
            db, account, stated="1075.00", effective_on=date(2026, 3, 3),
            evidence=_UNCORROBORATED, file_name="guessed.csv", lines=[],
            period=(date(2026, 3, 1), date(2026, 3, 3)),
        )

        known = recorded_opening_before(account.id, date(2026, 3, 2))

        assert known.amount == Decimal("1015.00")
        assert known.evidence is _CORROBORATED


class TestTheDoorsThatChangeLinesReleaseTheAnchorsTheyUndercut:
    """An anchor is a conclusion drawn from lines at or before its own day.

    Two defects were reproduced as SILENTLY WRONG OPENINGS by independent
    adversarial reviews on 2026-08-23 -- a later export inserting a line into a
    day an earlier anchor had priced, and a delete removing the lines an anchor
    rested on -- and one rule closes both: the evidence moved, so the
    conclusion goes.

    **A release is an APPENDED row since plan step ``balance:X-bj-1``**: the
    level relation is append-only at the database tier, so the withdrawal is
    a ``budget.anchor_releases`` row naming the level and the import whose
    lines changed, and a level STANDS when no release names it.  Every case
    here therefore seeds the CAUSE -- the import that recorded the changing
    line -- because both doors have one and the door's own id is what it
    passes.
    """

    def test_it_releases_an_anchor_at_or_after_the_changed_day(
        self, app, db, seed_user,
    ):
        """A line recorded inside an anchor's window was not in its solve."""
        row = _seed_import(
            db, seed_user["account"], stated="1085.00",
            effective_on=date(2026, 3, 3), evidence=_FILE_CHAIN,
            lines=[(date(2026, 3, 1), "100.00")],
            period=(date(2026, 3, 1), date(2026, 3, 3)),
        )
        cause = _seed_import(
            db, seed_user["account"],
            lines=[(date(2026, 3, 2), "-15.00")], file_name="later.csv",
        )

        assert release_anchors_from(
            seed_user["account"].id, date(2026, 3, 2), cause.id,
        ) == 1
        level, release = _placement(db, row)
        # The level is untouched -- append-only -- and the release names
        # the cause and the day.
        assert level.observed_on == date(2026, 3, 3)
        assert release is not None
        assert release.anchor_id == level.id
        assert release.released_by_import_id == cause.id
        assert release.lines_changed_from == date(2026, 3, 2)
        assert standing_bank_levels(seed_user["account"].id) == []

    def test_it_LEAVES_an_anchor_that_predates_the_change(
        self, app, db, seed_user,
    ):
        """A line after an anchor's day says nothing about days before it."""
        row = _seed_import(
            db, seed_user["account"], stated="1100.00",
            effective_on=date(2026, 3, 1), evidence=_FILE_CHAIN,
            lines=[(date(2026, 3, 1), "100.00")],
            period=(date(2026, 3, 1), date(2026, 3, 3)),
        )
        cause = _seed_import(
            db, seed_user["account"],
            lines=[(date(2026, 3, 2), "-15.00")], file_name="later.csv",
        )

        assert release_anchors_from(
            seed_user["account"].id, date(2026, 3, 2), cause.id,
        ) == 0
        level, release = _placement(db, row)
        assert level.observed_on == date(2026, 3, 1)
        assert release is None

    def test_the_recording_import_is_EXCLUDED_from_its_own_release(
        self, app, db, seed_user,
    ):
        """It solved against its own COMPLETE line list, so nothing undercuts it."""
        row = _seed_import(
            db, seed_user["account"], stated="1085.00",
            effective_on=date(2026, 3, 3), evidence=_FILE_CHAIN,
            lines=[(date(2026, 3, 1), "100.00")],
            period=(date(2026, 3, 1), date(2026, 3, 3)),
        )

        assert release_anchors_from(
            seed_user["account"].id, date(2026, 3, 2), row.id,
        ) == 0
        level, release = _placement(db, row)
        assert level.observed_on == date(2026, 3, 3)
        assert release is None

    def test_it_is_scoped_to_ITS_OWN_account(
        self, app, db, seed_user, seed_second_user,
    ):
        """Another owner's anchors are not this owner's to release."""
        other = _seed_import(
            db, seed_second_user["account"], stated="9000.00",
            effective_on=date(2026, 3, 3), evidence=_FILE_CHAIN,
            lines=[(date(2026, 3, 1), "8000.00")],
            period=(date(2026, 3, 1), date(2026, 3, 3)),
            file_name="other.csv",
        )
        cause = _seed_import(
            db, seed_user["account"],
            lines=[(date(2026, 3, 1), "-15.00")], file_name="mine.csv",
        )

        assert release_anchors_from(
            seed_user["account"].id, date(2026, 3, 1), cause.id,
        ) == 0
        level, release = _placement(db, other)
        assert level.observed_on == date(2026, 3, 3)
        assert release is None

    def test_a_released_level_is_released_ONCE(self, app, db, seed_user):
        """A withdrawn level rests on nothing: a second change releases nothing.

        ``resting_on`` reads STANDING levels, so the second door finds none,
        and ``uq_anchor_releases_anchor`` would refuse a second row anyway.
        """
        row = _seed_import(
            db, seed_user["account"], stated="1085.00",
            effective_on=date(2026, 3, 3), evidence=_FILE_CHAIN,
            lines=[(date(2026, 3, 1), "100.00")],
            period=(date(2026, 3, 1), date(2026, 3, 3)),
        )
        first = _seed_import(
            db, seed_user["account"],
            lines=[(date(2026, 3, 2), "-15.00")], file_name="first.csv",
        )
        second = _seed_import(
            db, seed_user["account"],
            lines=[(date(2026, 3, 2), "-20.00")], file_name="second.csv",
        )
        assert release_anchors_from(
            seed_user["account"].id, date(2026, 3, 2), first.id,
        ) == 1

        assert release_anchors_from(
            seed_user["account"].id, date(2026, 3, 2), second.id,
        ) == 0
        _level, release = _placement(db, row)
        assert release.released_by_import_id == first.id

    def test_deleting_the_CAUSE_keeps_the_release_and_nulls_the_cause(
        self, app, db, seed_user,
    ):
        """The one UPDATE the append-only refusal admits: the key's SET NULL.

        The level stays withdrawn -- nothing re-instates it -- and the release
        keeps the day whose lines changed; only the import it named is gone.
        Committed, because the refusal's DELETE arm is a deferred constraint
        trigger and the audit row is the record that survives.
        """
        row = _seed_import(
            db, seed_user["account"], stated="1085.00",
            effective_on=date(2026, 3, 3), evidence=_FILE_CHAIN,
            lines=[(date(2026, 3, 1), "100.00")],
            period=(date(2026, 3, 1), date(2026, 3, 3)),
        )
        cause = _seed_import(
            db, seed_user["account"],
            lines=[(date(2026, 3, 2), "-15.00")], file_name="later.csv",
        )
        release_anchors_from(
            seed_user["account"].id, date(2026, 3, 2), cause.id,
        )
        db.session.commit()

        db.session.delete(cause)
        db.session.commit()

        _level, release = _placement(db, row)
        assert release is not None
        assert release.released_by_import_id is None
        assert release.lines_changed_from == date(2026, 3, 2)
        assert standing_bank_levels(seed_user["account"].id) == []


class TestWhichPlacementsRestOnAChangedDay:
    """``resting_on`` over ``standing_bank_levels``: THE predicate, stated once.

    Plan step ``bank_import:X-gr``, finding **BI-490**.  The release door acts
    on this list and the delete confirmation counts it, so what the pair
    answers is graded on its own rather than only through the release -- the
    cases above change a day strictly BEFORE a placement, and none of them
    graded the boundary.
    """

    def test_a_placement_ON_the_changed_day_itself_rests_on_it(
        self, app, db, seed_user,
    ):
        """The boundary is inclusive: a placement at *d* rests on *d*'s lines.

        A placement is a conclusion drawn from the lines at or before its own
        day, so a line recorded ON that day was not in its solve.  Written
        ``>`` instead of ``>=`` this reads empty on the first assertion.
        """
        row = _seed_import(
            db, seed_user["account"], stated="1085.00",
            effective_on=date(2026, 3, 3), evidence=_FILE_CHAIN,
            lines=[(date(2026, 3, 1), "100.00")],
            period=(date(2026, 3, 1), date(2026, 3, 3)),
        )
        standing = standing_bank_levels(seed_user["account"].id)

        assert [
            one.statement_import_id
            for one in resting_on(standing, date(2026, 3, 3))
        ] == [row.id]
        assert resting_on(standing, date(2026, 3, 4)) == []

    def test_standing_bank_levels_holds_the_PLACED_ones_only(
        self, app, db, seed_user,
    ):
        """A claim with no placement owns no level and is not fetched.

        And the OWNER's own levels are not bank levels: an assertion typed
        through the true-up door sits in the same relation since plan step
        ``balance:X-bj-1`` and names no import, so it can neither anchor the
        bank walk nor be released by a line change.
        """
        placed = _seed_import(
            db, seed_user["account"], stated="1085.00",
            effective_on=date(2026, 3, 3), evidence=_FILE_CHAIN,
            lines=[(date(2026, 3, 1), "100.00")],
            period=(date(2026, 3, 1), date(2026, 3, 3)),
        )
        _seed_import(
            db, seed_user["account"], stated="2459.60",
            lines=[(date(2026, 3, 5), "10.00")],
            period=(date(2026, 3, 5), date(2026, 3, 5)),
            file_name="unplaced.csv",
        )
        db.session.add(AccountAnchorHistory(
            account_id=seed_user["account"].id,
            anchor_balance=Decimal("1085.00"),
            observed_on=date(2026, 3, 3),
        ))
        db.session.flush()

        assert [
            one.statement_import_id
            for one in standing_bank_levels(seed_user["account"].id)
        ] == [placed.id]

    def test_the_exclusion_leaves_the_named_import_and_no_other(
        self, app, db, seed_user,
    ):
        """Two placements rest on the day; naming one leaves exactly the other."""
        first = _seed_import(
            db, seed_user["account"], stated="1085.00",
            effective_on=date(2026, 3, 3), evidence=_FILE_CHAIN,
            lines=[(date(2026, 3, 1), "100.00")],
            period=(date(2026, 3, 1), date(2026, 3, 3)),
        )
        second = _seed_import(
            db, seed_user["account"], stated="1095.00",
            effective_on=date(2026, 3, 5), evidence=_FILE_CHAIN,
            lines=[(date(2026, 3, 5), "10.00")],
            period=(date(2026, 3, 5), date(2026, 3, 5)),
            file_name="second.csv",
        )
        standing = standing_bank_levels(seed_user["account"].id)

        def imports_of(levels):
            return [one.statement_import_id for one in levels]

        assert imports_of(
            resting_on(standing, date(2026, 3, 1), first.id),
        ) == [second.id]
        assert imports_of(
            resting_on(standing, date(2026, 3, 1), second.id),
        ) == [first.id]
        assert imports_of(
            resting_on(standing, date(2026, 3, 1)),
        ) == [first.id, second.id]
