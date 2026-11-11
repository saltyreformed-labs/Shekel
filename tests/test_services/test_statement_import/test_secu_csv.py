"""The SECU CSV adapter reads what the bank wrote, and refuses what it did not.

Plan step **bank_import:X-f6a-1**, ruling **R-FP**.  The adapter is the only
place in the importer that knows a format, so every quirk of the real export is
graded here rather than downstream.

**Most of these are FIRING CONTROLS** (``docs/plans/verification.md`` standard
4): each writes the file shape the adapter is supposed to refuse and asserts the
refusal.  The one that matters most is the TOTALS row -- SECU appends a summary
line whose Credit and Debit cells hold the file's whole-year sums, and an adapter
that did not recognise it would import a fabricated ``+$43,597.96`` /
``-$43,213.56`` movement dated nowhere.  That is not a hypothetical: it is the
last row of the developer's own export, and the first draft of this adapter's
measurement harness read it as a transaction.
"""

from datetime import date
from decimal import Decimal

import pytest

from app.services.statement_import import StatementParseError, parse_statement
from app.enums import StatementSourceEnum

from . import _csv_builder as build

_SOURCE = StatementSourceEnum.SECU_CHECKING_CSV

#: Three days of ordinary activity, chronological.
_ENTRIES = [
    (date(2026, 3, 2), "-25.00", "POINT OF SALE DEBIT L340 COFFEE"),
    (date(2026, 3, 3), "1500.00", "ACH DEPOSIT TOWN OF CLAYTON  PAYROLL"),
    (date(2026, 3, 4), "-40.81", "POINT OF SALE DEBIT L340 FOOD LION"),
]


def _payload(**kwargs):
    """Return a well-formed three-line file, with overrides."""
    rows = build.chained("100.00", _ENTRIES)
    return build.build(rows, **kwargs), rows


class TestItReadsAWellFormedExport:
    """The ordinary case, in the shape SECU actually writes."""

    def test_it_returns_every_line_and_the_account(self):
        """Three data rows in, three lines out, plus the masked account."""
        payload, _ = _payload()

        parsed = parse_statement(_SOURCE, payload)

        assert parsed.external_account_id == build.ACCOUNT_IDENTITY
        assert len(parsed.lines) == 3

    def test_it_returns_lines_in_chronological_order(self):
        """The file is newest-first; every consumer needs oldest-first.

        The reversal is load-bearing rather than cosmetic: the running-balance
        chain is a prefix sum, so a file left in its own order fails on every
        pair, and the identity ordinal is positional.
        """
        payload, _ = _payload()

        lines = parse_statement(_SOURCE, payload).lines

        assert [line.posted_on for line in lines] == [
            date(2026, 3, 2), date(2026, 3, 3), date(2026, 3, 4),
        ]

    def test_a_debit_is_negative_and_a_credit_is_positive(self):
        """The sign convention is the bank's own, with no inversion applied.

        SECU pre-signs the Debit column, so the adapter takes whichever of the
        two cells is filled.  Getting this backwards would invert every
        movement while leaving the file parseable, which is why it is asserted
        on both directions rather than on a total.
        """
        payload, _ = _payload()

        lines = parse_statement(_SOURCE, payload).lines

        assert lines[0].amount == Decimal("-25.00")
        assert lines[1].amount == Decimal("1500.00")
        assert lines[2].amount == Decimal("-40.81")

    def test_amounts_are_decimal_never_float(self):
        """Money crosses the boundary as Decimal, per the coding standard."""
        payload, _ = _payload()

        lines = parse_statement(_SOURCE, payload).lines

        assert all(isinstance(line.amount, Decimal) for line in lines)
        assert all(
            isinstance(line.running_balance, Decimal) for line in lines
        )

    def test_it_carries_the_running_balance_through(self):
        """The self-check's input survives the parse."""
        payload, _ = _payload()

        lines = parse_statement(_SOURCE, payload).lines

        assert [line.running_balance for line in lines] == [
            Decimal("75.00"), Decimal("1575.00"), Decimal("1534.19"),
        ]

    def test_it_carries_the_banks_category_as_provenance(self):
        """The bank's own category is kept, and it is not a Shekel category."""
        rows = [build.row(
            date(2026, 3, 2), "-25.00", "POINT OF SALE DEBIT L340 COFFEE",
            category="Food/Coffee Shops", running="75.00",
        )]
        payload = build.build(rows)

        lines = parse_statement(_SOURCE, payload).lines

        assert lines[0].source_category == "Food/Coffee Shops"

    def test_a_line_with_no_category_carries_none_not_empty_string(self):
        """Absence is ``None``, so a reader cannot mistake "" for a category."""
        rows = [build.row(
            date(2026, 3, 2), "-25.00", "SOMETHING", running="75.00",
        )]

        lines = parse_statement(_SOURCE, build.build(rows)).lines

        assert lines[0].source_category is None

    def test_a_line_stating_no_transaction_day_records_NONE(self):
        """The NULL is the source saying so, not the adapter not knowing.

        It held a COPY of ``posted_on`` until plan step X-f6a-3a, which is what
        made it useless: no reader could tell an observed swipe day from a
        restatement of the clearing day.  A match writes this day onto a
        matched purchase's ``purchased_on`` (ruling **R-FW**), so a copy would
        record every card purchase as made on the day it cleared.
        """
        payload, _ = _payload()

        lines = parse_statement(_SOURCE, payload).lines

        assert all(line.transaction_on is None for line in lines)

    def test_this_source_carries_no_external_id(self):
        """The CSV has no FITID, and the shape says so rather than faking one."""
        payload, _ = _payload()

        lines = parse_statement(_SOURCE, payload).lines

        assert all(line.external_id is None for line in lines)

    def test_a_memo_is_appended_to_the_description(self):
        """A memo is rare but must not be silently dropped."""
        rows = [build.row(
            date(2026, 3, 2), "-25.00", "CHECK 1234", memo="birthday",
            running="75.00",
        )]

        lines = parse_statement(_SOURCE, build.build(rows)).lines

        assert lines[0].description == "CHECK 1234 | birthday"

    def test_a_long_description_is_truncated_to_the_column(self):
        """200 characters is the column; a longer bank string must still land."""
        rows = [build.row(
            date(2026, 3, 2), "-25.00", "X" * 400, running="75.00",
        )]

        lines = parse_statement(_SOURCE, build.build(rows)).lines

        assert len(lines[0].description) == 200

    def test_a_file_without_the_running_balance_column_still_parses(self):
        """The column is an export OPTION, so its absence is legal.

        What it costs is the self-check, not the import -- which is why the
        page asks for it rather than the parser requiring it.
        """
        rows = [
            build.row(date(2026, 3, 2), "-25.00", "COFFEE")[:10],
            build.row(date(2026, 3, 1), "-10.00", "TEA")[:10],
        ]
        payload = build.build(
            rows, totals=build.totals_row(rows)[:10],
            header=build.HEADER[:10],
        )

        lines = parse_statement(_SOURCE, payload).lines

        assert [line.running_balance for line in lines] == [None, None]


class TestItRefusesTheSummaryRowAsATransaction:
    """The TOTALS row is not a line, and reading it as one fabricates money."""

    def test_the_summary_is_not_imported_as_a_line(self):
        """Three data rows plus a summary must yield three lines, not four."""
        payload, _ = _payload()

        lines = parse_statement(_SOURCE, payload).lines

        assert len(lines) == 3

    def test_a_summary_row_CARRYING_A_DATE_is_still_not_a_line(self):
        """The control that actually grades the guard, and it had to be found.

        The first version of this test fed a summary with an EMPTY date, which
        the blank-date skip already dropped -- so an adversarial review deleted
        ``_is_totals_row`` entirely and the test still passed.  A summary that
        carries a date is the only shape that reaches the predicate, and it is
        what "keyed on the marker, not on position" has to mean.  If it were
        read as a transaction it would import a fabricated ``+$43,597.96`` /
        ``-$43,213.56`` movement that every later stage would treat as a bank
        fact.
        """
        rows = build.chained("100.00", _ENTRIES)
        summary = build.totals_row(rows)
        summary[0] = "03/05/2026"

        lines = parse_statement(
            _SOURCE, build.build(rows, totals=summary),
        ).lines

        assert len(lines) == 3
        assert all(line.amount != Decimal("1500.00") for line in lines[:1])
        assert not any(
            line.posted_on == date(2026, 3, 5) for line in lines
        )

    def test_a_data_row_filling_both_money_columns_is_refused(self):
        """Both columns filled is the SUMMARY's shape, so a data row in that
        state means the summary was not recognised -- and continuing would
        import whichever cell the adapter happened to prefer."""
        bad = build.row(date(2026, 3, 2), "-25.00", "ODD", running="75.00")
        bad[8] = "10.00"
        payload = build.build([bad])

        with pytest.raises(StatementParseError, match="BOTH"):
            parse_statement(_SOURCE, payload)

    def test_a_data_row_filling_neither_money_column_is_refused(self):
        """A line stating no amount is not a movement, and is not invented."""
        bad = build.row(date(2026, 3, 2), "-25.00", "ODD", running="75.00")
        bad[9] = ""
        payload = build.build([bad])

        with pytest.raises(StatementParseError, match="NEITHER"):
            parse_statement(_SOURCE, payload)


class TestItGradesTheParseAgainstTheFilesOwnSummary:
    """Three equalities, each catching what the others cannot."""

    def test_a_wrong_item_count_is_refused(self):
        """Catches a dropped or duplicated row."""
        rows = build.chained("100.00", _ENTRIES)
        payload = build.build(
            rows, totals=build.totals_row(rows, item_count=99),
        )

        with pytest.raises(StatementParseError, match="99 items"):
            parse_statement(_SOURCE, payload)

    def test_a_wrong_credit_total_is_refused(self):
        """Catches money IN read with the wrong sign or dropped."""
        rows = build.chained("100.00", _ENTRIES)
        payload = build.build(
            rows, totals=build.totals_row(rows, credit="9999.00"),
        )

        with pytest.raises(StatementParseError, match="came in"):
            parse_statement(_SOURCE, payload)

    def test_a_wrong_debit_total_is_refused(self):
        """Catches money OUT read with the wrong sign or dropped."""
        rows = build.chained("100.00", _ENTRIES)
        payload = build.build(
            rows, totals=build.totals_row(rows, debit="-9999.00"),
        )

        with pytest.raises(StatementParseError, match="went out"):
            parse_statement(_SOURCE, payload)

    def test_a_file_with_no_summary_is_REFUSED(self):
        """The summary is REQUIRED, and a truncated download is why.

        It is the only cross-check that survives when the running-balance
        option is not ticked -- which is the default export -- and it is the
        LAST ROW of the file, so an interrupted download loses exactly the row
        that would have caught the lines it also lost.  All six of the
        developer's real exports carry it.
        """
        rows = build.chained("100.00", _ENTRIES)

        with pytest.raises(StatementParseError, match="no 'Totals:' summary"):
            parse_statement(_SOURCE, build.build(rows, with_totals=False))

    def test_a_TRUNCATED_file_is_refused_by_the_missing_summary(self):
        """The shape the requirement exists for, end to end.

        A download cut short loses its trailing rows AND its summary; without
        the requirement it parses cleanly and reports success over a statement
        that is missing movements.
        """
        rows = build.chained("100.00", _ENTRIES)
        truncated = build.build(rows, with_totals=False)
        truncated = truncated[:truncated.rindex(b"\n", 0, len(truncated) - 1)]

        with pytest.raises(StatementParseError):
            parse_statement(_SOURCE, truncated)

    def test_a_summary_whose_count_is_not_a_number_is_refused(self):
        """A summary that cannot be read is not silently treated as absent."""
        rows = build.chained("100.00", _ENTRIES)
        summary = build.totals_row(rows)
        summary[7] = "lots of items"

        with pytest.raises(StatementParseError, match="not a count"):
            parse_statement(_SOURCE, build.build(rows, totals=summary))


class TestItRefusesWhatIsNotAFiniteAmount:
    """``Decimal("NaN")`` does not raise on its own, and that is the hole."""

    def test_NaN_is_refused(self):
        """The measured critical defect: NaN passed every arm and committed.

        ``Decimal("NaN")`` constructs without raising AND survives
        ``round_money``'s quantize, so before the finiteness check a file
        stating NaN was accepted into a money column.  It then compares equal
        to nothing (invisible to every matcher), makes ``SUM()`` over the
        account NaN, and raises ``InvalidOperation`` inside the display macro's
        ``value < 0`` -- so the page 500s on every later load, permanently,
        with no in-app way to remove the row.
        """
        bad = build.row(date(2026, 3, 2), "-25.00", "ODD", running="75.00")
        bad[8], bad[9] = "NaN", ""

        with pytest.raises(StatementParseError, match="not a real number"):
            parse_statement(_SOURCE, build.build([bad]))

    def test_infinity_is_refused(self):
        """Its siblings raise on construction; asserted so both arms are held."""
        bad = build.row(date(2026, 3, 2), "-25.00", "ODD", running="75.00")
        bad[9] = "-Infinity"

        with pytest.raises(StatementParseError):
            parse_statement(_SOURCE, build.build([bad]))

    def test_a_sub_cent_amount_rounds_HALF_UP(self):
        """The whole reason ``_money`` calls ``round_money``, finally graded.

        SECU's OFX writes six decimal places (``-165.220000``), so a sub-cent
        figure is a real possibility -- and Python's default is banker's
        rounding, which would send ``-165.225`` to ``-165.22``.  The app's rule
        is ROUND_HALF_UP.
        """
        rows = [build.row(date(2026, 3, 2), "-1.00", "X", running="99.00")]
        rows[0][9] = "-165.225"
        summary = build.totals_row(rows, credit="0.00", debit="-165.23")

        lines = parse_statement(
            _SOURCE, build.build(rows, totals=summary),
        ).lines

        assert lines[0].amount == Decimal("-165.23")


class TestItBindsColumnsByName:
    """SECU's two balance options share an index and mean different things."""

    def test_a_DAILY_balance_export_is_refused_by_name(self):
        """Measured: index 10 is ``Running Balance`` in one real export and
        ``Daily Balance`` in another, where the latter is written only on each
        day's last line.  Read positionally, one is silently taken for the
        other -- and on a span whose days each hold one transaction it would
        pass every check while meaning something else.
        """
        header = list(build.HEADER)
        header[10] = "Daily Balance"
        rows = build.chained("100.00", _ENTRIES)

        with pytest.raises(StatementParseError, match="Daily Balance"):
            parse_statement(_SOURCE, build.build(rows, header=header))

    def test_a_missing_required_column_names_itself(self):
        """A header short of a column this adapter reads is not this export."""
        header = [name for name in build.HEADER if name != "Category"]
        rows = [r[:6] + r[7:] for r in build.chained("100.00", _ENTRIES)]

        with pytest.raises(StatementParseError, match="Category"):
            parse_statement(_SOURCE, build.build(rows, header=header))

    def test_columns_in_a_DIFFERENT_ORDER_still_read_correctly(self):
        """Binding by name is what makes this true rather than lucky."""
        order = [1, 0] + list(range(2, 11))
        header = [build.HEADER[i] for i in order]
        rows = [[r[i] for i in order]
                for r in build.chained("100.00", _ENTRIES)]
        summary = [build.totals_row(build.chained("100.00", _ENTRIES))[i]
                   for i in order]

        parsed = parse_statement(
            _SOURCE, build.build(rows, totals=summary, header=header),
        )

        assert parsed.external_account_id == build.ACCOUNT_IDENTITY
        assert [line.amount for line in parsed.lines] == [
            Decimal("-25.00"), Decimal("1500.00"), Decimal("-40.81"),
        ]


class TestItRefusesAFileItCannotTrust:
    """Everything that is not a SECU transaction export."""

    def test_a_file_with_no_header_row_is_refused(self):
        """A different bank's export, or the wrong file entirely."""
        payload = b"one,two,three\r\n1,2,3\r\n"

        with pytest.raises(StatementParseError, match="no transaction header"):
            parse_statement(_SOURCE, payload)

    def test_a_file_with_no_transactions_is_refused(self):
        """Headers alone are not a statement."""
        payload = build.build([], with_totals=False)

        with pytest.raises(StatementParseError, match="no transactions"):
            parse_statement(_SOURCE, payload)

    def test_a_row_whose_date_is_not_a_date_is_refused(self):
        """Refused rather than skipped: a skipped row is a missing movement."""
        bad = build.row(date(2026, 3, 2), "-25.00", "ODD", running="75.00")
        bad[0] = "the second of March"

        with pytest.raises(StatementParseError, match="not a date"):
            parse_statement(_SOURCE, build.build([bad]))

    def test_a_row_whose_amount_is_not_a_number_is_refused(self):
        """Same reason: a movement that cannot be read is not one to drop."""
        bad = build.row(date(2026, 3, 2), "-25.00", "ODD", running="75.00")
        bad[9] = "twenty five dollars"

        with pytest.raises(StatementParseError, match="not an amount"):
            parse_statement(_SOURCE, build.build([bad]))

    def test_a_file_mixing_two_accounts_is_refused(self):
        """An import records ONE account; a mixed file would split silently."""
        rows = [
            build.row(date(2026, 3, 2), "-25.00", "A", running="75.00"),
            build.row(
                date(2026, 3, 1), "-10.00", "B", running="100.00",
                account_number="******9999", account_name="Savings",
            ),
        ]

        with pytest.raises(StatementParseError, match="mixes 2 accounts"):
            parse_statement(_SOURCE, build.build(rows))

    def test_a_file_out_of_DATE_ORDER_is_refused(self):
        """Chronological order is a precondition of three separate things.

        The chain is a prefix sum, the identity ordinal is positional, and the
        import's own span is derived from the days -- and until this check
        existed the order was stated in three docstrings and enforced nowhere,
        resting entirely on the file being newest-first.  A user who opens the
        CSV in a spreadsheet, sorts it and re-saves is the ordinary way that
        stops being true, and the 10-column export has no chain to catch it.
        """
        rows = build.chained("100.00", _ENTRIES, with_running=False)
        rows[0], rows[2] = rows[2], rows[0]

        with pytest.raises(StatementParseError, match="not in date order"):
            parse_statement(_SOURCE, build.build(rows))

    def test_a_blank_account_number_is_refused(self):
        """An empty string is not an identity, and it would be permanent.

        Recorded once, it claims the empty string for this account forever and
        every later import of a real file is refused as a mismatch -- with no
        door in the app to correct it.
        """
        rows = build.chained("100.00", _ENTRIES, account_number="")

        with pytest.raises(StatementParseError, match="which account"):
            parse_statement(_SOURCE, build.build(rows))

    def test_a_NUL_byte_is_refused_as_a_sentence_not_a_500(self):
        """It survives decode and csv, then fails deep inside the driver.

        psycopg2 raised a ``ValueError``, which is not a ``SQLAlchemyError``,
        so it escaped the route's handlers as a 500; psycopg 3 (plan step
        balance:X-dj) raises a ``DataError`` those handlers turn into a
        generic failure.  Neither is a message the uploader can act on.
        """
        payload, _ = _payload()

        with pytest.raises(StatementParseError, match="binary data"):
            parse_statement(_SOURCE, payload.replace(b"COFFEE", b"COF\x00EE"))

    def test_a_file_that_is_not_text_is_refused(self):
        """A PDF or an image, uploaded by mistake."""
        with pytest.raises(StatementParseError, match="not text"):
            parse_statement(_SOURCE, b"\xff\xfe\x00\x80\x81\x8f")

    def test_an_unsupported_source_is_refused(self):
        """The registry answers rather than defaulting to some parser.

        A member with a ref row but no adapter is the state a later leaf
        creates when it seeds a source ahead of writing its reader, and it must
        refuse by NAME rather than read the file with the wrong parser.
        """
        class _Unwired:  # pylint: disable=too-few-public-methods
            """A source member the registry has no parser for."""

            value = "not_a_real_adapter"

        with pytest.raises(StatementParseError, match="no importer"):
            parse_statement(_Unwired(), b"")


class TestItRecordsTheDayTheBankSTATED:
    """``DATE MM-DD`` inside a card line's description, parsed or refused.

    Plan step **bank_import:X-f6a-3a**, ruling **R-FW**.  SECU concatenates a
    fixed transaction-day field into the description of every card line, and it
    is the ONLY place either of this bank's formats carries the day a swipe
    happened: the OFX's structured ``DTUSER`` equals ``DTPOSTED`` on 359 of its
    361 lines and is one day LATER on the other two.  So the choice is not
    between a parsed day and a stated one; it is between the stated day and
    nothing.

    **Why it matters that this is right**: an accepted match writes this day
    onto a matched purchase's ``purchased_on``.  Reading the token wrongly
    would re-date a real purchase, and reading the POSTING day instead would
    record every card purchase as having been made on the day it cleared.

    Measured over the developer's own 2026-08-16 export: 182 of 361 lines carry
    exactly one token, every one derivable, gaps of 0-4 days, 2 genuine year
    rollovers, and 0 lines carrying two tokens.
    """

    @staticmethod
    def _one(day, description):
        """Return the single parsed line of a file holding just this row."""
        rows = build.chained("100.00", [(day, "-25.00", description)])
        lines = parse_statement(_SOURCE, build.build(rows)).lines
        return lines[0]

    def test_it_reads_the_stated_day(self):
        """The ordinary card line: posted a day after the swipe."""
        line = self._one(
            date(2026, 8, 14),
            "POINT OF SALE DEBIT L340 DATE 08-13 Amazon.com*5H2RA5V",
        )

        assert line.transaction_on == date(2026, 8, 13)

    def test_it_reads_a_day_stated_as_the_posting_day(self):
        """Equal is a real answer, not a reason to record NONE.

        25 of the developer's 182 stated days equal their posting day.  The
        NULL means *the source states none*, so a stated day that happens to
        agree must still be recorded as stated.
        """
        line = self._one(
            date(2026, 8, 14),
            "POINT OF SALE DEBIT L340 DATE 08-14 Amazon.com*5H2RA5V",
        )

        assert line.transaction_on == date(2026, 8, 14)

    def test_it_rolls_the_year_back_over_new_year(self):
        """The token states no year, and January lines state December days.

        Both of the developer's real rollovers are this line: posted
        2026-01-02, stating ``DATE 12-31``.  Reading the year off the posting
        day alone would date the purchase eleven months into the FUTURE, which
        ``entry_service`` would then refuse as a purchase that has not happened.
        """
        line = self._one(
            date(2026, 1, 2),
            "POINT OF SALE DEBIT L340 DATE 12-31 BJS.COM #5490",
        )

        assert line.transaction_on == date(2025, 12, 31)

    def test_a_line_stating_nothing_records_NONE(self):
        """Most non-card lines state no transaction day at all."""
        line = self._one(
            date(2026, 8, 14),
            "ACH DEBIT CAPITAL ONE      MOBILE PMT 026226009042739",
        )

        assert line.transaction_on is None

    def test_TWO_stated_days_record_NONE(self):
        """Which one is "the" transaction day would be a guess, so neither is.

        The stored description is a ``Description | Memo`` join, so a memo can
        carry its own date.  0 of the developer's 361 lines do, which is
        exactly why the rule has to be TOTAL rather than correct because
        today's data is tidy.
        """
        line = self._one(
            date(2026, 8, 14),
            "POINT OF SALE DEBIT L340 DATE 08-13 KOBO DATE 08-11",
        )

        assert line.transaction_on is None

    def test_a_day_too_far_back_to_be_unambiguous_records_NONE(self):
        """The token carries no year, so its day is only readable near the post.

        A stated day that lands neither in the posted month nor the one before
        it cannot be resolved to a year without guessing -- and reading it
        anyway would drag a matched purchase months backwards into a closed pay
        period.  This is a statement about the TOKEN's ambiguity, not a
        tolerance on money.
        """
        line = self._one(
            date(2026, 8, 14),
            "POINT OF SALE DEBIT L340 DATE 02-11 SOMETHING STALE",
        )

        assert line.transaction_on is None

    def test_the_stated_day_is_never_after_the_posting_day(self):
        """A future-looking token resolves backwards, never forwards.

        ``DATE 08-20`` on a line posted 08-14 is last year's 08-20 by the
        year rule -- and that is more than a month back, so it records NONE
        rather than a purchase made in the future.
        """
        line = self._one(
            date(2026, 8, 14),
            "POINT OF SALE DEBIT L340 DATE 08-20 SOMETHING AHEAD",
        )

        assert line.transaction_on is None

    def test_a_date_in_the_MEMO_is_not_read_as_the_bank_stating_one(self):
        """A memo is the USER's free text; only the bank's cell states a day.

        The description column is where SECU puts its transaction-day field.
        A parse over the stored ``Description | Memo`` text would read a memo's
        own date as the bank's word -- and the two-token refusal above, written
        for exactly that hazard, only catches its two-token form.  Found by
        adversarial review 2026-08-18.
        """
        rows = [
            build.row(date(2026, 8, 14), "-25.00",
                      "POINT OF SALE DEBIT L340 KOBO",
                      memo="DATE 08-13", running="75.00"),
        ]
        lines = parse_statement(_SOURCE, build.build(rows)).lines

        assert lines[0].description.endswith("| DATE 08-13")
        assert lines[0].transaction_on is None

    def test_a_second_token_past_the_200_char_cut_still_refuses(self):
        """The two-token guard must not depend on a string length.

        The stored description is truncated to 200 characters; a guard that ran
        over the truncated text could see one token where the bank wrote two,
        turning a refused line into an accepted one.  Parsing the CELL removes
        the truncation from the guard's reach.
        """
        long_desc = (
            "POINT OF SALE DEBIT L340 DATE 08-13 " + "X" * 190 + " DATE 08-11"
        )
        rows = [build.row(date(2026, 8, 14), "-25.00", long_desc,
                          running="75.00")]
        lines = parse_statement(_SOURCE, build.build(rows)).lines

        assert len(lines[0].description) == 200
        assert lines[0].transaction_on is None

    def test_a_token_TWO_months_back_is_refused(self):
        """The month window's first REFUSED value, not a comfortable margin.

        A refusal test six months out passes whatever the bound is; this one
        fails the moment ``months_back <= 1`` is loosened by even one.  Found
        by adversarial test-quality review 2026-08-18.
        """
        line = self._one(
            date(2026, 8, 14),
            "POINT OF SALE DEBIT L340 DATE 06-13 SOMETHING OLD",
        )

        assert line.transaction_on is None

    def test_a_token_ONE_month_back_is_accepted(self):
        """The last ACCEPTED value, so the bound is pinned from both sides."""
        line = self._one(
            date(2026, 8, 14),
            "POINT OF SALE DEBIT L340 DATE 07-16 SOMETHING RECENT",
        )

        assert line.transaction_on == date(2026, 7, 16)

    def test_february_29_in_a_year_that_has_none_is_refused(self):
        """The leap-day arm, which the year loop's own comment names.

        ``date(2026, 2, 29)`` raises, and the loop must move to the next
        candidate year rather than propagating it.  2025 has no 02-29 either,
        so the honest answer is ``None``.
        """
        line = self._one(
            date(2026, 3, 2),
            "POINT OF SALE DEBIT L340 DATE 02-29 LEAP",
        )

        assert line.transaction_on is None

    def test_february_29_in_a_year_that_HAS_one_is_read(self):
        """The control: the arm skips an impossible year, not a real day."""
        line = self._one(
            date(2028, 3, 1),
            "POINT OF SALE DEBIT L340 DATE 02-29 LEAP",
        )

        assert line.transaction_on == date(2028, 2, 29)

    def test_each_line_anchors_its_year_on_ITS_OWN_posting_day(self):
        """One file, two lines, two different years -- read per row.

        Every other test here parses a one-row file, so nothing graded that the
        year is anchored per LINE rather than once per import.  A file spanning
        a new year is the ordinary case for a year-to-date export, and it is
        where a per-file anchor would silently mis-date January.
        """
        rows = build.chained("100.00", [
            (date(2026, 1, 2), "-25.00",
             "POINT OF SALE DEBIT L340 DATE 12-31 BJS.COM"),
            (date(2026, 8, 14), "-25.00",
             "POINT OF SALE DEBIT L340 DATE 08-13 AMAZON"),
        ])
        lines = parse_statement(_SOURCE, build.build(rows)).lines

        assert [line.transaction_on for line in lines] == [
            date(2025, 12, 31), date(2026, 8, 13),
        ]


class TestItRecordsTheMerchantTheBankNAMES:
    """The parenthesised trailing token of the description CELL, or ``None``.

    Plan step **bank_import:X-f6a-3d**.  SECU appends its own normalized
    merchant at the end of every description cell -- ``BJS FUEL #9151
    25GARNER     NC (BJ's Fuel)`` -- on **361 of 361** of the developer's
    recorded lines, resolving to 59 distinct merchants, the longest 28
    characters.

    **Why it matters that this is right, and why it is a COLUMN.**  This string
    is the key a merchant destination policy is stated against, so a wrong
    value files a payment in the wrong envelope on every later statement.  It
    was a reader over the stored description
    (``statement_match._offers.merchant_of``) while its only consumer was a
    form's name box; a reader has to be TOTAL, and SECU's own OFX truncates 326
    of those 361 descriptions to the same 32 characters -- so a total reader
    would key one policy on that string and fire it on every merchant behind
    it.  ``None`` here keys nothing.
    """

    @staticmethod
    def _one(description, memo=""):
        """Return the single parsed line of a file holding just this row."""
        rows = [build.row(
            date(2026, 8, 14), "-25.00", description, memo=memo,
            running="75.00",
        )]
        lines = parse_statement(_SOURCE, build.build(rows)).lines
        return lines[0]

    def test_it_reads_the_merchant(self):
        """The ordinary card line, which is 361 of the developer's 361."""
        line = self._one(
            "POINT OF SALE DEBIT L340 BJS FUEL #9151 25GARNER     NC "
            "(BJ's Fuel)"
        )

        assert line.merchant == "BJ's Fuel"

    def test_a_line_naming_no_merchant_records_NONE(self):
        """The NULL is the source saying so, and it keys no policy.

        Not a fallback to the description: that fallback is what made the old
        reader unusable as a key, because a source with no merchant field gives
        every line the same truncated string.
        """
        line = self._one("ACH DEBIT GEICO            PREM COLL 026002003831385")

        assert line.merchant is None

    def test_the_merchant_is_read_from_the_CELL_not_the_stored_text(self):
        """A memo's own parentheses are the USER's, not the bank's.

        The stored description is a ``Description | Memo`` join, so a memo
        ending "(anything)" would become the merchant -- and now the key a rule
        matches on.  Reading the CELL makes that unreachable rather than
        guarded against, which is the bound
        ``_stated_transaction_day`` learned to make structural on 2026-08-18.
        """
        line = self._one("CHECK 1234", memo="dinner (Patinos Super Bar)")

        assert line.description == "CHECK 1234 | dinner (Patinos Super Bar)"
        assert line.merchant is None

    def test_a_parenthesis_inside_the_banks_own_wording_is_not_the_merchant(
        self,
    ):
        """Only the TRAILING token is a candidate, so mid-string text is not.

        ``KOBO (US) INC`` is a real line of the developer's, and its ``(US)``
        is part of what the bank called the movement.  The pattern is anchored
        at the end of the cell, so the merchant here is the token SECU
        appended and not the one inside its own prose.
        """
        line = self._one(
            "POINT OF SALE DEBIT L340 DATE 01-03 KOBO (US) INC     "
            "180-036-8539 (Kobo Us Inc)"
        )

        assert line.merchant == "Kobo Us Inc"

    def test_a_trailing_parenthesis_that_is_the_WHOLE_description_is_read(self):
        """``SECU FOUNDATION (Secu Foundation)`` is 8 of the developer's lines.

        A non-card line whose entire description is the bank's own words plus
        the token.  It names a merchant like any other.
        """
        line = self._one("SECU FOUNDATION (Secu Foundation)")

        assert line.merchant == "Secu Foundation"

    def test_a_BLANK_token_records_NONE_rather_than_a_blank_key(self):
        """An all-whitespace token is not a name, and here that is a KEY.

        A blank merchant would be a policy the owner could neither read on the
        screen nor restate.  ``ck_merchants_name_not_blank``
        says the same thing in the database, so the adapter and the table
        cannot drift.
        """
        line = self._one("POINT OF SALE DEBIT L340 SOMETHING (   )")

        assert line.merchant is None

    def test_a_token_longer_than_the_column_records_NONE(self):
        """The pattern's own bound is the column's, so no write can truncate.

        A truncated merchant would be a DIFFERENT key from the one the bank
        named, silently -- so a token too long to store is read as no merchant
        at all rather than as a shortened one.
        """
        line = self._one(
            "POINT OF SALE DEBIT L340 SOMETHING (" + "X" * 101 + ")"
        )

        assert line.merchant is None

    def test_the_merchant_is_read_from_the_UNTRUNCATED_cell(self):
        """The stored description is cut to 200 characters; the cell is not.

        A merchant read after the cut would be missing on exactly the longest
        bank strings, so the token has to be read from the cell -- the same
        reason ``_stated_transaction_day`` is.
        """
        line = self._one(
            "POINT OF SALE DEBIT L340 " + "X" * 190 + " (Long Merchant)"
        )

        assert len(line.description) == 200
        assert line.merchant == "Long Merchant"


class TestTheBalanceTheFileClaimsAboutItself:
    """The ``Balance as of`` preamble: read, refused, or honestly absent.

    **The only cross-check available to a file carrying no running balance**,
    which is every export the developer's bank has produced since 2026-07.  It
    is RECORDED and never gated on: this module's own docstring measures that
    the figure can LAG its own lines -- the 2026-08-16 export stated
    ``$4,747.63``, which was 08-13's closing balance, while listing two 08-14
    lines worth ``-$1,006.72`` -- so a file whose header disagrees with its
    contents is an ordinary file.

    What IS refused is a header the adapter cannot read, because the
    alternative is a cross-check that quietly stops running.  Every case below
    reached the app's 500 page or was silently dropped before 2026-08-22; found
    by adversarial robustness review.
    """

    def test_the_claim_and_its_day_are_both_read(self):
        """The ordinary case, from the builder's own preamble."""
        rows = [build.row(date(2026, 8, 16), "-25.00", "POS COFFEE")]

        parsed = parse_statement(
            _SOURCE,
            build.build(rows, balance_as_of="08/16/2026",
                        stated_balance="2501.31"),
        )

        assert parsed.stated_balance == Decimal("2501.31")
        assert parsed.stated_balance_on == date(2026, 8, 16)

    def test_a_file_stating_no_balance_states_NEITHER_half(self):
        """An absent header is not an error; it is the absence of a claim.

        Both halves go together or neither does -- a figure with no day
        asserts nothing about an account, and the database says so too
        (``ck_statement_imports_stated_balance_paired``).
        """
        rows = [build.row(date(2026, 8, 16), "-25.00", "POS COFFEE")]
        payload = build.build(rows)
        without = b"\n".join(
            line for line in payload.split(b"\n")
            if not line.startswith(b"Balance as of")
        )

        parsed = parse_statement(_SOURCE, without)

        assert parsed.stated_balance is None
        assert parsed.stated_balance_on is None
        assert len(parsed.lines) == 1

    @pytest.mark.parametrize("day", ["13/45/2026", "02/30/2026", "00/00/0000"])
    def test_a_day_that_is_not_a_date_REFUSES_the_file(self, day):
        """``strptime`` raises a bare ValueError the door does not catch.

        So each of these reached the 500 handler rather than the importer's own
        message, on a file the owner could not tell was malformed.
        """
        rows = [build.row(date(2026, 8, 16), "-25.00", "POS COFFEE")]

        with pytest.raises(StatementParseError, match="not a date"):
            parse_statement(_SOURCE, build.build(rows, balance_as_of=day))

    def test_a_SINGLE_DIGIT_day_is_read_rather_than_dropped(self):
        """``strptime`` accepts ``8/2/2026`` everywhere else in this adapter.

        A strict two-digit pattern made a stated header indistinguishable from
        an absent one -- the silent drop this class exists to refuse.
        """
        rows = [build.row(date(2026, 8, 16), "-25.00", "POS COFFEE")]

        parsed = parse_statement(
            _SOURCE, build.build(rows, balance_as_of="8/2/2026"),
        )

        assert parsed.stated_balance_on == date(2026, 8, 2)

    def test_a_figure_with_a_THOUSANDS_SEPARATOR_is_read(self):
        """``$2,501.31`` is what a bank writes; it refused the whole file."""
        rows = [build.row(date(2026, 8, 16), "-25.00", "POS COFFEE")]

        parsed = parse_statement(
            _SOURCE, build.build(rows, stated_balance="$2,501.31"),
        )

        assert parsed.stated_balance == Decimal("2501.31")

    def test_a_figure_too_large_to_store_REFUSES_the_file(self):
        """``round_money``'s quantize raised outside every handler here.

        ``1E+30`` is finite, so the NaN guard passes it through to a
        ``quantize`` that raises ``InvalidOperation`` -- a 500 rather than a
        refusal, reachable from every money cell this adapter reads.
        """
        rows = [build.row(date(2026, 8, 16), "-25.00", "POS COFFEE")]

        with pytest.raises(StatementParseError, match="too large"):
            parse_statement(
                _SOURCE, build.build(rows, stated_balance="1E+30"),
            )

    def test_a_file_stating_its_balance_TWICE_is_refused(self):
        """Taking the first would be a coin toss about money.

        The module's own measurement is that one of these can lag the file, so
        "the first" is not a safe default.  The neighbouring rule refuses a
        file naming two accounts for the same reason.
        """
        rows = [build.row(date(2026, 8, 16), "-25.00", "POS COFFEE")]
        payload = build.build(rows)
        doubled = payload.replace(
            b"Balance as of 08/16/2026,1000.00",
            b"Balance as of 08/13/2026,4747.63\nBalance as of 08/16/2026,1000.00",
        )

        with pytest.raises(StatementParseError, match="states its balance"):
            parse_statement(_SOURCE, doubled)

    def test_a_balance_line_BELOW_the_header_is_a_transaction_and_not_a_claim(
        self,
    ):
        """The search is bounded ABOVE the header on purpose.

        Below it every row is a transaction, so a description reading
        ``Balance as of ...`` is the owner's own text and must not be read as
        the account's balance.
        """
        rows = [build.row(date(2026, 8, 16), "-25.00",
                          "Balance as of 01/01/2000")]

        parsed = parse_statement(
            _SOURCE, build.build(rows, balance_as_of="08/16/2026",
                                 stated_balance="1000.00"),
        )

        assert parsed.stated_balance_on == date(2026, 8, 16)
