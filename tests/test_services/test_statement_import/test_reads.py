"""What the statements page reads, and the scoping nothing was watching.

Plan step **bank_import:X-f6a-1**.  ``_reads.py`` had NO test at all until an
adversarial review said so: four public functions, three of them carrying an
``account_id`` filter, and the only coverage was four static-string assertions
in the route test.

**The scoping tests are the point.**  Deleting the ``account_id`` filter from
any of the three readers left every other test in the branch green -- while the
page rendered another account's bank descriptions, days, amounts and running
balances.  That is a disclosure of exactly the material this leaf exists to
record, and the route's 404 tests cannot see it: they prove you cannot ADDRESS
another account's page, not that your own page shows only your own lines.

``recorded_span``'s ``net_amount`` is the other hole they name: it is a money
figure rendered to the user and it was asserted nowhere, so summing absolute
values instead of signed ones would have gone unnoticed.
"""

from datetime import date, datetime, timezone
from decimal import Decimal

import pytest
from sqlalchemy import text

from app.enums import (
    StatementBalanceEvidenceEnum,
    StatementSourceEnum,
)
from app.models.merchant import Merchant
from app.models.merchant_rule import MerchantRule
from app.models.ref import AccountType, StatementSource
from app.models.statement_import import BankStatementLine
from app.ref_seeds import _REF_TABLE_SEEDS
from app.services import account_service
from app.services.statement_import import (
    available_sources,
    import_history,
    recent_lines,
    record_statement,
    recorded_span,
    supported_sources,
)
# The two reads the delete confirmation shares with the act have no importer
# outside the package, so exporting them from ``statement_import.__init__``
# would be the public surface ``CLAUDE.md`` rule 13 forbids.  Reaching into
# the module from its own tests is the allowance ``test_merchant_schema``
# takes for ``_merchants``.
from app.services.statement_import._reads import (
    OwnedLines,
    lines_by_import,
    matches_by_import,
    orphan_merchants_by_import,
)

from tests.test_services.test_statement_match._builders import (
    a_bank_line,
    a_merchant,
    a_sighting,
    an_import,
)

from . import _csv_builder as build

_SOURCE = StatementSourceEnum.SECU_CHECKING_CSV

_ENTRIES = [
    (date(2026, 3, 2), "-25.00", "POINT OF SALE DEBIT L340 COFFEE"),
    (date(2026, 3, 3), "1500.00", "ACH DEPOSIT TOWN OF CLAYTON  PAYROLL"),
    (date(2026, 3, 4), "-40.81", "POINT OF SALE DEBIT L340 FOOD LION"),
]

#: The same shape with SECU's parenthesised merchant token on each line, for
#: the cases about which merchants a delete would orphan.  ``_ENTRIES`` above
#: deliberately names none, and the reads-are-scoped cases rely on that.
_NAMED = [
    (date(2026, 3, 2), "-25.00",
     "POINT OF SALE DEBIT L340 COFFEE (Big Cheese Clayton)"),
    (date(2026, 3, 4), "-40.81",
     "POINT OF SALE DEBIT L340 FOOD LION (Food Lion)"),
]


def _record(seed_user, account, entries, number=build.ACCOUNT_NUMBER,
            name=build.ACCOUNT_NAME, file_name="statement.csv"):
    """Record *entries* against *account*."""
    payload = build.build(build.chained(
        "100.00", entries, account_number=number, account_name=name,
    ))
    return record_statement(
        account_id=account.id,
        user_id=seed_user["user"].id,
        source=_SOURCE,
        file_name=file_name,
        payload=payload,
    )


@pytest.fixture()
def two_accounts(db, seed_user):
    """Return the seeded account plus a SECOND one owned by the same user."""
    checking_type = (
        db.session.query(AccountType).filter_by(name="Checking").one()
    )
    other = account_service.create_account(
        account_service.AccountSpec(
            user_id=seed_user["user"].id,
            account_type_id=checking_type.id,
            name="Second Checking",
            anchor_balance=Decimal("0.00"),
            observed_on=seed_user["bootstrap_period"].start_date,
        )
    )
    db.session.flush()
    return seed_user["account"], other


class TestEveryReaderIsScopedToItsOwnAccount:
    """One owner, two accounts: a reader must not blend them."""

    def test_recorded_span_counts_only_its_own_lines(
        self, app, db, seed_user, two_accounts,
    ):
        """Three lines here, one there -- and the span says three."""
        mine, theirs = two_accounts
        _record(seed_user, mine, _ENTRIES)
        _record(
            seed_user, theirs,
            [(date(2026, 3, 9), "-99.00", "ELSEWHERE")],
            number="******9999", name="Second Checking",
            file_name="other.csv",
        )

        assert recorded_span(mine.id).line_count == 3
        assert recorded_span(theirs.id).line_count == 1

    def test_recorded_span_dates_only_its_own_lines(
        self, app, db, seed_user, two_accounts,
    ):
        """The other account's later day must not widen this account's span."""
        mine, theirs = two_accounts
        _record(seed_user, mine, _ENTRIES)
        _record(
            seed_user, theirs,
            [(date(2026, 9, 9), "-99.00", "MUCH LATER")],
            number="******9999", name="Second Checking",
            file_name="other.csv",
        )

        span = recorded_span(mine.id)
        assert span.first_day == date(2026, 3, 2)
        assert span.last_day == date(2026, 3, 4)

    def test_recent_lines_returns_only_its_own(
        self, app, db, seed_user, two_accounts,
    ):
        """The disclosure test: another account's descriptions must not show."""
        mine, theirs = two_accounts
        _record(seed_user, mine, _ENTRIES)
        _record(
            seed_user, theirs,
            [(date(2026, 3, 9), "-99.00", "SOMEBODY ELSES MERCHANT")],
            number="******9999", name="Second Checking",
            file_name="other.csv",
        )

        descriptions = [line.description for line in recent_lines(mine.id)]

        assert len(descriptions) == 3
        assert "SOMEBODY ELSES MERCHANT" not in descriptions

    def test_import_history_returns_only_its_own(
        self, app, db, seed_user, two_accounts,
    ):
        """The same, for the acts rather than the lines."""
        mine, theirs = two_accounts
        _record(seed_user, mine, _ENTRIES, file_name="mine.csv")
        _record(
            seed_user, theirs,
            [(date(2026, 3, 9), "-99.00", "ELSEWHERE")],
            number="******9999", name="Second Checking",
            file_name="theirs.csv",
        )

        assert [row.file_name for row in import_history(seed_user["user"].id, mine.id)] == [
            "mine.csv",
        ]


class TestTheTwoReadsTheDeleteConfirmationSharesWithTheAct:
    """Plan step ``bank_import:X-gr``, finding **BI-490**.

    The receipt reports the placements a delete released and the merchants it
    forgot; the confirmation previews both, and the developer ruled it does so
    from the ONE read the act counts with.  These grade the reads on their own:
    what each keys, what each leaves absent, and that each sees ITS OWN
    account alone -- the scoping this file exists for.
    """

    def test_lines_by_import_keys_each_imports_COUNT_and_EARLIEST_day(
        self, app, db, seed_user,
    ):
        """Three facts per import: it is keyed, counted and dated from its lines.

        **A line two imports sighted is neither's to lose** (plan step
        ``bank_import:X-f6b-1``, ruling **R-BI10**): the first import and its
        re-import both vouch for the three March lines, so deleting either
        removes nothing, and BOTH are absent rather than zero -- which is the
        reading the act gives an absence: nothing removed, so nothing
        released.  *Until that step the first import owned the three and the
        re-import owned none.*  The later import alone holds its one line.
        """
        account = seed_user["account"]
        first = _record(seed_user, account, _ENTRIES)
        again = _record(seed_user, account, _ENTRIES, file_name="again.csv")
        later = _record(
            seed_user, account,
            [(date(2026, 3, 9), "-99.00", "LATER")], file_name="later.csv",
        )

        owned = lines_by_import(account.id)

        assert owned == {
            later.import_id: OwnedLines(count=1, earliest=date(2026, 3, 9)),
        }
        assert first.import_id not in owned
        assert again.import_id not in owned

    def test_lines_by_import_reads_only_ITS_OWN_account(
        self, app, db, seed_user, two_accounts,
    ):
        """Another account's import is not in this account's map."""
        mine, theirs = two_accounts
        _record(seed_user, mine, _ENTRIES, file_name="mine.csv")
        elsewhere = _record(
            seed_user, theirs,
            [(date(2026, 3, 9), "-99.00", "ELSEWHERE")],
            number="******9999", name="Second Checking",
            file_name="theirs.csv",
        )

        assert elsewhere.import_id not in lines_by_import(mine.id)

    def test_orphan_merchants_attribute_a_merchant_to_its_SOLE_namer(
        self, app, db, seed_user,
    ):
        """A merchant two imports name is nobody's to orphan.

        Deleting either import leaves the other's line naming it, so it is
        attributed to neither; the one only the first import names is the
        first import's.
        """
        account = seed_user["account"]
        first = _record(seed_user, account, _NAMED)
        second = _record(
            seed_user, account,
            [(date(2026, 3, 9), "-12.00",
              "POINT OF SALE DEBIT L340 FOOD LION (Food Lion)")],
            file_name="second.csv",
        )
        big_cheese = db.session.query(Merchant).filter(
            Merchant.name == "Big Cheese Clayton",
        ).one()

        orphans = orphan_merchants_by_import(account.id)

        assert orphans == {first.import_id: [big_cheese.id]}
        assert second.import_id not in orphans

    def test_a_merchant_only_ONE_imports_sighting_names_is_that_imports_even_where_the_line_survives(
        self, app, db, seed_user,
    ):
        """The read is over the SIGHTINGS, not the lines' derived merchant.

        One line two imports showed under two words (ruling **R-BI16**):
        deleting the first takes its sighting, the line survives on the
        second's, and ``COFFEE`` is then named by nothing -- so it is the
        first import's to orphan, although the LINE is not the first
        import's to remove.  A read grouped by the line's derived merchant
        would attribute ``COFFEE`` to neither (two imports sight the line)
        and the delete door would leave a merchant no sighting names.
        """
        account = seed_user["account"]
        first = an_import(
            seed_user, created_at=datetime(2026, 4, 1, 12, tzinfo=timezone.utc),
        )
        second = an_import(
            seed_user, created_at=datetime(2026, 4, 2, 12, tzinfo=timezone.utc),
        )
        line = a_bank_line(seed_user, first, amount="-4.50", merchant="COFFEE")
        a_sighting(seed_user, second, line, merchant="Coffee Shop")
        db.session.flush()
        coffee = a_merchant(seed_user, "COFFEE")
        coffee_shop = a_merchant(seed_user, "Coffee Shop")

        orphans = orphan_merchants_by_import(account.id)

        assert orphans == {first.id: [coffee.id], second.id: [coffee_shop.id]}
        assert lines_by_import(account.id) == {}

    def test_an_ANSWERED_merchant_is_attributed_to_NO_import(
        self, app, db, seed_user,
    ):
        """A stated rule keeps its merchant, so the delete would not forget it."""
        account = seed_user["account"]
        first = _record(seed_user, account, _NAMED)
        food_lion = db.session.query(Merchant).filter(
            Merchant.name == "Food Lion",
        ).one()
        db.session.add(MerchantRule(
            user_id=seed_user["user"].id,
            account_id=account.id,
            merchant_id=food_lion.id,
            never_a_purchase=True,
        ))
        db.session.flush()
        big_cheese = db.session.query(Merchant).filter(
            Merchant.name == "Big Cheese Clayton",
        ).one()

        assert orphan_merchants_by_import(account.id) == {
            first.import_id: [big_cheese.id],
        }

    def test_orphan_merchants_reads_only_ITS_OWN_account(
        self, app, db, seed_user, two_accounts,
    ):
        """Another account's import, naming its own merchant, is not in this map."""
        mine, theirs = two_accounts
        _record(seed_user, mine, _ENTRIES, file_name="mine.csv")
        elsewhere = _record(
            seed_user, theirs,
            [(date(2026, 3, 9), "-99.00", "ELSEWHERE (Their Grocer)")],
            number="******9999", name="Second Checking",
            file_name="theirs.csv",
        )
        assert db.session.query(Merchant).filter(
            Merchant.name == "Their Grocer",
        ).count() == 1

        assert elsewhere.import_id not in orphan_merchants_by_import(mine.id)

    def test_import_history_previews_BOTH_figures_from_those_reads(
        self, app, db, seed_user,
    ):
        """The page's row carries what deleting it would release and forget.

        Two imports: the first names two merchants and is placed at 03-04;
        the second adds a line on 03-09 and is placed there.  Deleting the
        FIRST releases the second's placement (03-09 is at or after 03-02, the
        earliest day the first's lines post on) and forgets both merchants;
        deleting the SECOND releases nothing (03-04 is before 03-09) and
        forgets nothing.  Both directions are asserted because either alone
        is satisfied by a constant.
        """
        account = seed_user["account"]
        first = _record(seed_user, account, _NAMED)
        second = _record(
            seed_user, account,
            [(date(2026, 3, 9), "-12.00", "POINT OF SALE DEBIT L340 X")],
            file_name="second.csv",
        )

        by_id = {
            row.import_id: row
            for row in import_history(seed_user["user"].id, account.id)
        }

        assert by_id[first.import_id].removes.anchors == 1
        assert by_id[first.import_id].removes.merchants == 2
        assert by_id[second.import_id].removes.anchors == 0
        assert by_id[second.import_id].removes.merchants == 0


class TestTheImportRowCarriesWhatTheBankSaid:
    """The RECORD, where the receipt is transient.

    ``ImportedBalance``'s own docstring argues this matters because an anchor
    the import only assumed has to stay readable after the flash is gone -- and
    the field had no test at all, so forcing ``_imported_balance`` to return
    ``None`` left 233 tests passing.  Found by adversarial review 2026-08-23.
    """

    def test_it_carries_the_claim_the_day_and_the_evidence(
        self, app, db, seed_user, two_accounts,
    ):
        """All four facts, because the schema holds them as one.

        The fixture chains from `$100.00`, so its header states the closing its
        own lines imply and the file proves itself.
        """
        mine, _ = two_accounts
        _record(seed_user, mine, _ENTRIES)

        balance = import_history(seed_user["user"].id, mine.id)[0].balance

        assert balance is not None
        assert balance.stated == Decimal("1534.19")
        assert balance.effective_on == date(2026, 3, 4)
        assert balance.evidence is StatementBalanceEvidenceEnum.FILE_CHAIN
        assert balance.is_anchored

    def test_a_file_stating_NO_balance_carries_none(
        self, app, db, seed_user, two_accounts,
    ):
        """The absence is a value the page branches on, so it is asserted."""
        mine, _ = two_accounts
        payload = build.build(build.chained("100.00", _ENTRIES))
        without = b"\n".join(
            line for line in payload.split(b"\n")
            if not line.startswith(b"Balance as of")
        )
        record_statement(
            account_id=mine.id, user_id=seed_user["user"].id,
            source=StatementSourceEnum.SECU_CHECKING_CSV,
            file_name="nobalance.csv", payload=without,
        )

        assert import_history(seed_user["user"].id, mine.id)[0].balance is None


class TestTheSpanIsArithmeticallyTrue:
    """``net_amount`` is a money figure the page renders."""

    def test_the_net_is_the_SIGNED_sum(self, app, db, seed_user):
        """Signed, not absolute: -25.00 + 1500.00 - 40.81 = 1434.19.

        Summing magnitudes instead would give 1565.81 and read as a windfall.
        """
        _record(seed_user, seed_user["account"], _ENTRIES)

        assert recorded_span(seed_user["account"].id).net_amount == Decimal(
            "1434.19"
        )

    def test_the_net_is_decimal(self, app, db, seed_user):
        """It crosses the SQL boundary as Decimal, never float."""
        _record(seed_user, seed_user["account"], _ENTRIES)

        assert isinstance(
            recorded_span(seed_user["account"].id).net_amount, Decimal
        )

    def test_an_account_with_nothing_recorded_answers_rather_than_raising(
        self, app, db, seed_user,
    ):
        """A zero-count span with ``None`` days, not an absence to branch on."""
        span = recorded_span(seed_user["account"].id)

        assert span.line_count == 0
        assert span.first_day is None
        assert span.last_day is None
        assert span.net_amount == Decimal("0")


class TestTheLineListIsOrderedAndBounded:
    """What the page shows, and how much of it."""

    def test_lines_come_back_newest_POSTED_day_first(
        self, app, db, seed_user,
    ):
        """Ordered by the bank's own day, not by when they were recorded.

        An import backfilling an older span must not push newer lines down the
        page -- the question the section answers is "what does my bank say
        happened lately".
        """
        _record(seed_user, seed_user["account"], _ENTRIES)

        assert [line.posted_on for line in
                recent_lines(seed_user["account"].id)] == [
            date(2026, 3, 4), date(2026, 3, 3), date(2026, 3, 2),
        ]

    def test_the_limit_bounds_the_list(self, app, db, seed_user):
        """A page section, not an unbounded dump."""
        _record(seed_user, seed_user["account"], _ENTRIES)

        assert len(recent_lines(seed_user["account"].id, limit=2)) == 2

    def test_imports_come_back_newest_first(self, app, db, seed_user):
        """Most recent act at the top."""
        _record(seed_user, seed_user["account"], _ENTRIES, file_name="one.csv")
        _record(seed_user, seed_user["account"], _ENTRIES, file_name="two.csv")

        assert [row.file_name for row in
                import_history(seed_user["user"].id, seed_user["account"].id)][0] == "two.csv"

    def test_the_counts_and_the_window_are_DERIVED_per_import(
        self, app, db, seed_user,
    ):
        """What a file held, what it added, and the window it declared.

        Plan step ``bank_import:X-f6b-1``, ruling **R-IY**: the two counts
        were columns and are the sightings now.  A first import of three
        lines held three and added three; a re-import held three and added
        none; a later file of one held one and added one.  The window is the
        CSV's own line extremes.
        """
        account = seed_user["account"]
        _record(seed_user, account, _ENTRIES, file_name="first.csv")
        _record(seed_user, account, _ENTRIES, file_name="again.csv")
        _record(
            seed_user, account,
            [(date(2026, 3, 9), "-99.00", "LATER")], file_name="later.csv",
        )

        rows = import_history(seed_user["user"].id, account.id)

        assert [
            (row.file_name, row.line_count, row.recorded_count,
             row.declared_start, row.declared_end)
            for row in rows
        ] == [
            ("later.csv", 1, 1, date(2026, 3, 9), date(2026, 3, 9)),
            ("again.csv", 3, 0, date(2026, 3, 2), date(2026, 3, 4)),
            ("first.csv", 3, 3, date(2026, 3, 2), date(2026, 3, 4)),
        ]

    def test_a_match_on_a_line_TWO_imports_hold_is_neither_imports_to_release(
        self, app, db, seed_user,
    ):
        """The delete preview and the act read one derivation (**R-BI10**).

        A re-import re-sighted the matched line, so deleting either import
        leaves the line -- and the match -- standing; the confirmation must
        preview no release for either.  *Until plan step ``bank_import:
        X-f6b-1`` the first import owned the line and its match was released
        by that import's deletion even though the re-import had shown it.*
        """
        account = seed_user["account"]
        first = _record(seed_user, account, _ENTRIES, file_name="first.csv")
        again = _record(seed_user, account, _ENTRIES, file_name="again.csv")
        line = db.session.query(BankStatementLine).order_by(
            BankStatementLine.posted_on,
        ).first()
        db.session.execute(text(
            "WITH act AS ("
            "  INSERT INTO budget.statement_matches "
            "  (account_id, user_id, applied_by_rule) "
            "  VALUES (:a, :u, false) RETURNING id) "
            "INSERT INTO budget.statement_match_members "
            "  (match_id, account_id, bank_statement_line_id) "
            "SELECT id, :a, :l FROM act"
        ), {"a": account.id, "u": seed_user["user"].id, "l": line.id})
        db.session.flush()

        assert matches_by_import(account.id) == {}
        assert {
            row.import_id: row.matches_affected
            for row in import_history(seed_user["user"].id, account.id)
        } == {first.import_id: 0, again.import_id: 0}


class TestTheOfferedSourcesAreTheUSABLEOnes:
    """The form's options are the intersection of the ref table and the
    adapter registry."""

    def test_every_offered_source_has_a_label_and_a_parser(
        self, app, db,
    ):
        """A source with no label could not be rendered; one with no parser
        would fail deeper in with a worse message.

        **The expected set changed at plan step ``bank_import:X-f6b-2``
        (developer, 2026-09-20).**  It read "every enum member", which held
        only while every member had a parser; the feed source ``simplefin``
        is seeded ahead of its parser (the claim door needs the row, the sync
        leaf writes the reader), which is the very case the intersection
        exists for, so the set this pins is now the parser-backed members --
        what the docstring above always said it graded.  That ``simplefin``
        is seeded AND left out is pinned beside the schema in
        ``tests/test_models/test_declared_mapping_schema.py``.
        """
        offered = available_sources()

        assert offered
        assert all(option.label for option in offered)
        assert {option.value for option in offered} == {
            member.value for member in supported_sources()
        }

    def test_the_label_comes_from_the_ref_table(self, app, db):
        """IDs for logic, strings for display -- and the string lives in ref.

        **The expected string changed at plan step ``bank_import:X-gc``.**  It
        read "SECU checking -- CSV with running balance", which named the
        format by a column SECU no longer exports: all four of the developer's
        exports on disk 2026-08-25 carry no balance column at all, and the help
        text rendered directly beneath this control has said the column is
        optional since plan step ``bank_import:X-f6e-1``.  Migration
        ``a1f4c7e0b839`` re-labels the row; ``app.ref_seeds`` carries the same
        value for a fresh bootstrap.  This test still grades what it always
        graded -- that the label is READ FROM ``ref`` rather than written into
        the reader -- and the value it pins is now the true one.
        """
        assert available_sources()[0].label == "SECU checking -- CSV export"

    def test_the_SEEDER_and_the_DATABASE_agree_about_that_label(
        self, app, db,
    ):
        """Leg 3 of the dual seed, for the one ref row that carries a LABEL.

        A ref value lives in three places -- the enum, the introducing
        migration's inline seed, and ``app.ref_seeds`` -- and
        ``tests/test_models/test_posting_ref_seed_parity.py`` grades all three
        for NAMES.  It cannot grade this one: ``display_name`` is not a name,
        and ``_seed_other_ref_tables`` INSERTS missing rows while leaving
        present ones alone, so a label changed in ``ref_seeds.py`` and nowhere
        else changes what a FRESH bootstrap says and nothing about the
        databases that already exist.  The two would then disagree silently and
        for ever.

        Plan step ``bank_import:X-gc`` created exactly that opportunity by
        re-labelling the row in a migration and in the seed file together; this
        is what makes the pair verifiable rather than a convention.  The test
        database is migration-built and then reseeded, so the stored value is
        the MIGRATION's -- which is precisely why comparing it against the
        SEED file catches the half that would otherwise be graded nowhere.
        """
        seeded = {
            entry["name"]: entry["display_name"]
            for entry in dict(_REF_TABLE_SEEDS)["StatementSource"]
        }
        stored = {
            row.name: row.display_name
            for row in db.session.query(StatementSource).all()
        }

        assert seeded == stored
