"""Migration ``3ef820b7dd52`` -- the sighting names the merchant (``bank_import:X-f6b-1b``).

Both directions are driven through the migration's own shipped callables over
a world holding what production holds -- one import per line, every worded
sighting's word already a merchant row -- plus the three shapes production
does not yet: a line two imports sighted under two WORDS, a second-source
word that never got a merchant row (the held-line gap the old record door
left), and a line whose stored key is STALE against its sightings (finding
**BI-504**).  Every assertion reads the DATABASE, and the migration's claims
about itself are the ones graded hardest: that the upgrade points every
worded sighting at its account's row for that word and mints the row where
none exists; that the downgrade writes the line's key back as the model
reads it -- the EARLIEST sighting naming one, by act order, never the lowest
sighting id -- and the word back from the row; and that the downgrade
SWEEPS the merchants no line's key and no rule names, which is the invariant
the old schema held.
"""

from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal

from sqlalchemy import text

from app import ref_cache
from app.enums import StatementSourceEnum
from app.extensions import db
from app.models.merchant_rule import MerchantRule
from app.models.statement_import import (
    BankStatementLine,
    StatementImport,
    StatementLineSighting,
)
from tests._test_helpers import (
    load_migration_module,
    run_migration_callable as _run,
)

_MIGRATION = load_migration_module(
    "3ef820b7dd52_the_sighting_names_the_merchant.py",
)


def _sql(statement, **params):
    """Run one statement and return every row (an UPDATE returns none)."""
    result = db.session.execute(text(statement), params)
    return result.all() if result.returns_rows else []


def _columns(table):
    """The column names of ``budget.<table>``, from the catalogue."""
    return {
        row[0] for row in _sql(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_schema = 'budget' AND table_name = :t", t=table,
        )
    }


def _constraint_count(name):
    """How many constraints carry *name*."""
    return _sql(
        "SELECT count(*) FROM pg_constraint WHERE conname = :n", n=name,
    )[0][0]


def _index_count(name):
    """How many indexes carry *name*."""
    return _sql(
        "SELECT count(*) FROM pg_indexes WHERE indexname = :n", n=name,
    )[0][0]


def _an_import(account, *, file_name, created_at):
    """One import over March, run at *created_at* -- through the head schema."""
    statement = StatementImport(
        account_id=account.id,
        user_id=account.user_id,
        source_id=ref_cache.statement_source_id(
            StatementSourceEnum.SECU_CHECKING_CSV
        ),
        file_name=file_name,
        file_digest=file_name.ljust(64, "0")[:64],
        declared_start=date(2026, 3, 1),
        declared_end=date(2026, 3, 31),
    )
    statement.created_at = created_at
    db.session.add(statement)
    db.session.flush()
    return statement


def _a_line(account, *, day, amount, ordinal=0):
    """One bank line, unsighted until a caller sights it."""
    line = BankStatementLine(
        account_id=account.id, posted_on=day, amount=Decimal(amount),
        sequence_in_group=ordinal,
    )
    db.session.add(line)
    db.session.flush()
    return line


def _a_merchant(account, name):
    """One merchant row, by SQL, returning its id."""
    return _sql(
        "INSERT INTO budget.merchants (account_id, name) "
        "VALUES (:a, :n) RETURNING id", a=account.id, n=name,
    )[0][0]


def _sight(statement, line, *, description, merchant_id=None):
    """*statement*'s sighting of *line* through the head schema."""
    sighting = StatementLineSighting(
        account_id=line.account_id, line_id=line.id, import_id=statement.id,
        description=description, merchant_id=merchant_id,
    )
    db.session.add(sighting)
    db.session.flush()
    return sighting


def _merchants():
    """Every merchant row as ``(id, name)``, ascending."""
    return _sql("SELECT id, name FROM budget.merchants ORDER BY id")


class TestTheRoundTrip:
    """Head -> down -> up, over a line two imports named two ways."""

    def test_down_keys_the_line_to_its_EARLIEST_naming_sighting_and_up_re_points_every_sighting(
        self, db, seed_user,
    ):
        """The shipped downgrade and upgrade, graded on the rows they leave.

        **The two-element case the order claim needs.**  The LATER import's
        sighting is written FIRST (the lower sighting id) and names
        ``Coffee Shop``; the EARLIER import's names ``COFFEE``.  The
        downgrade must key the line to ``COFFEE`` -- the earliest act, which
        is what the model reads -- and not to the lowest sighting id.  On the
        way back up, BOTH sightings name their own row again: the word is
        restored from the row and the row is found by the word.

        Run in the ``db`` fixture's own app context rather than a nested one,
        for the reason ``test_one_level_relation_migration`` gives.
        """
        account = seed_user["account"]
        earlier = _an_import(
            account, file_name="earlier.csv",
            created_at=datetime(2026, 4, 1, 12, 0, tzinfo=timezone.utc),
        )
        later = _an_import(
            account, file_name="later.csv",
            created_at=datetime(2026, 4, 20, 12, 0, tzinfo=timezone.utc),
        )
        coffee = _a_merchant(account, "COFFEE")
        coffee_shop = _a_merchant(account, "Coffee Shop")
        shared = _a_line(account, day=date(2026, 3, 20), amount="-4.50")
        later_sighting = _sight(
            later, shared, description="Coffee Shop", merchant_id=coffee_shop,
        )
        earlier_sighting = _sight(
            earlier, shared, description="POS (COFFEE)", merchant_id=coffee,
        )
        assert later_sighting.id < earlier_sighting.id
        unnamed = _a_line(account, day=date(2026, 3, 21), amount="-5.00")
        _sight(later, unnamed, description="NO MERCHANT HERE")
        db.session.commit()
        shared_id, unnamed_id = shared.id, unnamed.id

        _run(_MIGRATION.downgrade, db.session)

        # The line's key is the EARLIEST naming sighting's; the unnamed line
        # keys nothing; each sighting carries its row's word again.
        assert _sql(
            "SELECT id, merchant_id FROM budget.bank_statement_lines "
            "ORDER BY id",
        ) == [(shared_id, coffee), (unnamed_id, None)]
        assert _sql(
            "SELECT line_id, import_id, merchant "
            "FROM budget.statement_line_sightings ORDER BY line_id, import_id",
        ) == [
            (shared_id, earlier.id, "COFFEE"),
            (shared_id, later.id, "Coffee Shop"),
            (unnamed_id, later.id, None),
        ]
        assert "merchant_id" not in _columns("statement_line_sightings")
        assert _constraint_count("fk_bank_statement_lines_merchant_account") == 1
        assert _index_count("idx_bank_statement_lines_account_merchant") == 1
        assert _constraint_count(
            "fk_statement_line_sightings_merchant_account",
        ) == 0
        assert _index_count("idx_statement_line_sightings_account_merchant") == 0
        # The sweep: ``Coffee Shop`` is named by no line's key and no rule
        # below this revision, so it goes -- the old schema never held a
        # merchant nothing names.  ``COFFEE`` stays.
        assert _merchants() == [(coffee, "COFFEE")]

        _run(_MIGRATION.upgrade, db.session)

        # The earlier sighting finds its row by the word; the later one's
        # row is MINTED again (a new id) from the word the downgrade wrote
        # back.  The line's column is gone, and so is the word.
        assert _columns("bank_statement_lines") == {
            "id", "account_id", "posted_on", "amount", "sequence_in_group",
        }
        assert "merchant" not in _columns("statement_line_sightings")
        rows = _sql(
            "SELECT s.line_id, s.import_id, m.name "
            "FROM budget.statement_line_sightings AS s "
            "LEFT JOIN budget.merchants AS m ON m.id = s.merchant_id "
            "ORDER BY s.line_id, s.import_id",
        )
        assert rows == [
            (shared_id, earlier.id, "COFFEE"),
            (shared_id, later.id, "Coffee Shop"),
            (unnamed_id, later.id, None),
        ]
        assert _sql(
            "SELECT merchant_id FROM budget.statement_line_sightings "
            "WHERE line_id = :l AND import_id = :i", l=shared_id, i=earlier.id,
        ) == [(coffee,)]
        assert _constraint_count(
            "fk_statement_line_sightings_merchant_account",
        ) == 1
        assert _index_count("idx_statement_line_sightings_account_merchant") == 1
        assert _constraint_count("fk_bank_statement_lines_merchant_account") == 0
        assert _index_count("idx_bank_statement_lines_account_merchant") == 0
        # And the model reads what the migration left.
        db.session.expire_all()
        assert db.session.get(BankStatementLine, shared_id).merchant_name == (
            "COFFEE"
        )

    def test_the_downgrade_KEEPS_a_merchant_a_rule_is_about(
        self, db, seed_user,
    ):
        """The sweep's other arm: an answered merchant survives its lines.

        A merchant only a NON-earliest sighting named is un-keyed below this
        revision, but a standing rule is about it -- the property ruling
        **R-GS** keeps, and the reason the sweep tests both referrers.
        """
        account = seed_user["account"]
        earlier = _an_import(
            account, file_name="earlier.csv",
            created_at=datetime(2026, 4, 1, 12, 0, tzinfo=timezone.utc),
        )
        later = _an_import(
            account, file_name="later.csv",
            created_at=datetime(2026, 4, 20, 12, 0, tzinfo=timezone.utc),
        )
        coffee = _a_merchant(account, "COFFEE")
        coffee_shop = _a_merchant(account, "Coffee Shop")
        line = _a_line(account, day=date(2026, 3, 20), amount="-4.50")
        _sight(earlier, line, description="POS (COFFEE)", merchant_id=coffee)
        _sight(later, line, description="Coffee Shop", merchant_id=coffee_shop)
        db.session.add(MerchantRule(
            user_id=account.user_id, account_id=account.id,
            merchant_id=coffee_shop, never_a_purchase=True,
        ))
        db.session.commit()

        _run(_MIGRATION.downgrade, db.session)

        assert _merchants() == [(coffee, "COFFEE"), (coffee_shop, "Coffee Shop")]
        assert _sql(
            "SELECT merchant_id FROM budget.bank_statement_lines",
        ) == [(coffee,)]

        _run(_MIGRATION.upgrade, db.session)

        # The answered row was never deleted, so the later sighting finds
        # the SAME row by its word -- the rule still names it.
        assert _sql(
            "SELECT merchant_id FROM budget.statement_line_sightings "
            "WHERE import_id = :i", i=later.id,
        ) == [(coffee_shop,)]
        assert _sql(
            "SELECT merchant_id FROM budget.merchant_rules",
        ) == [(coffee_shop,)]


class TestTheUpgradeOverTheOldSchema:
    """The upgrade's own arms, run over the shape ``af07125d00f1`` left."""

    def _below(self, db, account):
        """Return ``(earlier, later, line)`` at the OLD schema, ready to seed by SQL.

        Stages through the head schema, downgrades, and hands back ids the
        caller then writes the old columns against directly.
        """
        earlier = _an_import(
            account, file_name="earlier.csv",
            created_at=datetime(2026, 4, 1, 12, 0, tzinfo=timezone.utc),
        )
        later = _an_import(
            account, file_name="later.csv",
            created_at=datetime(2026, 4, 20, 12, 0, tzinfo=timezone.utc),
        )
        line = _a_line(account, day=date(2026, 3, 20), amount="-4.50")
        _sight(earlier, line, description="POS (COFFEE)")
        _sight(later, line, description="Coffee Shop")
        db.session.commit()
        # The ids are read BEFORE the downgrade: a refresh of the line after
        # it would load the head model's projections against the old schema.
        ids = (earlier.id, later.id, line.id)
        _run(_MIGRATION.downgrade, db.session)
        return ids

    def test_a_word_with_NO_merchant_row_is_minted_one(self, db, seed_user):
        """The held-line gap, closed by the backfill rather than lost.

        Below this revision a re-import's different word for a line that
        already had a key was written as the sighting's WORD and never
        resolved to a row (``_merchant_words`` asked only about words
        filling a NULL key).  The upgrade mints the row, so the later
        sighting names a merchant rather than ``NULL`` -- a fact the file
        stated, kept.
        """
        account = seed_user["account"]
        earlier_id, later_id, line_id = self._below(db, account)
        coffee = _a_merchant(account, "COFFEE")
        _sql(
            "UPDATE budget.bank_statement_lines SET merchant_id = :m "
            "WHERE id = :l", m=coffee, l=line_id,
        )
        _sql(
            "UPDATE budget.statement_line_sightings SET merchant = 'COFFEE' "
            "WHERE line_id = :l AND import_id = :i", l=line_id, i=earlier_id,
        )
        _sql(
            "UPDATE budget.statement_line_sightings "
            "SET merchant = 'Coffee Shop' "
            "WHERE line_id = :l AND import_id = :i", l=line_id, i=later_id,
        )
        db.session.commit()
        assert _merchants() == [(coffee, "COFFEE")]

        _run(_MIGRATION.upgrade, db.session)

        assert [name for _id, name in _merchants()] == ["COFFEE", "Coffee Shop"]
        assert _sql(
            "SELECT s.import_id, m.name FROM budget.statement_line_sightings s "
            "JOIN budget.merchants m ON m.id = s.merchant_id "
            "ORDER BY s.import_id",
        ) == [(earlier_id, "COFFEE"), (later_id, "Coffee Shop")]
        db.session.expire_all()
        assert db.session.get(BankStatementLine, line_id).merchant_name == (
            "COFFEE"
        )

    def test_a_STALE_stored_key_is_replaced_by_the_read(self, db, seed_user):
        """Finding **BI-504**, the state this revision exists to make unrepresentable.

        Below this revision a line kept the key an import minted after that
        import was deleted -- here stood in for by a key no surviving
        sighting's word names.  The upgrade drops the column, and the read
        answers from the sightings: the earliest naming one.  The stale
        merchant row is SWEPT by the upgrade's own step 4: no sighting and no
        rule names it, so nothing at this revision could reach it and
        nothing would ever sweep it (``orphan_merchants_by_import`` attributes
        a merchant to the import whose sightings name it).  Named by
        adversarial review 2026-09-18: a first cut left it, and the docstring
        claimed the delete door would take it.
        """
        account = seed_user["account"]
        earlier_id, later_id, line_id = self._below(db, account)
        stale = _a_merchant(account, "GONE WITH ITS IMPORT")
        coffee = _a_merchant(account, "COFFEE")
        _sql(
            "UPDATE budget.bank_statement_lines SET merchant_id = :m "
            "WHERE id = :l", m=stale, l=line_id,
        )
        _sql(
            "UPDATE budget.statement_line_sightings SET merchant = 'COFFEE' "
            "WHERE line_id = :l AND import_id = :i", l=line_id, i=earlier_id,
        )
        db.session.commit()

        _run(_MIGRATION.upgrade, db.session)

        db.session.expire_all()
        read = db.session.get(BankStatementLine, line_id)
        assert read.merchant_id == coffee
        assert read.merchant_name == "COFFEE"
        assert [
            (sighting.import_id, sighting.merchant_id)
            for sighting in sorted(read.sightings, key=lambda s: s.import_id)
        ] == [(earlier_id, coffee), (later_id, None)]
        assert _merchants() == [(coffee, "COFFEE")]
        assert stale not in {merchant_id for merchant_id, _name in _merchants()}

    def test_the_upgrade_KEEPS_an_unsighted_merchant_a_rule_is_about(
        self, db, seed_user,
    ):
        """The upgrade's sweep has the same second arm as the downgrade's.

        A merchant no sighting names but a standing rule is about is an
        ANSWERED merchant that outlived its lines (ruling **R-GS**'s
        property), and the sweep must leave it -- with its rule.
        """
        account = seed_user["account"]
        earlier_id, _later_id, line_id = self._below(db, account)
        answered = _a_merchant(account, "ANSWERED, LINES GONE")
        coffee = _a_merchant(account, "COFFEE")
        _sql(
            "UPDATE budget.bank_statement_lines SET merchant_id = :m "
            "WHERE id = :l", m=coffee, l=line_id,
        )
        _sql(
            "UPDATE budget.statement_line_sightings SET merchant = 'COFFEE' "
            "WHERE line_id = :l AND import_id = :i", l=line_id, i=earlier_id,
        )
        db.session.add(MerchantRule(
            user_id=account.user_id, account_id=account.id,
            merchant_id=answered, never_a_purchase=True,
        ))
        db.session.commit()

        _run(_MIGRATION.upgrade, db.session)

        assert _merchants() == [
            (answered, "ANSWERED, LINES GONE"), (coffee, "COFFEE"),
        ]
        assert _sql("SELECT merchant_id FROM budget.merchant_rules") == [
            (answered,),
        ]
