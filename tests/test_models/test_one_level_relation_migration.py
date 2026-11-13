"""Migration ``d2e9f4a17c63`` -- one level relation (plan step ``balance:X-bj-1``).

Both directions are driven through the migration's own shipped callables
over a world holding what production holds -- an owner's assertions and a
placed statement -- plus the one shape production does not: a placement a
later import RELEASED.  Every assertion reads the DATABASE, and the
migration's claims about itself are the ones graded hardest: that the
backfill is a fast default that fires no trigger and writes no audit row,
that a standing placement round-trips onto the import row and back, and
that a released one writes back nothing (the state the old UPDATE-release
left) and is honestly gone after the next upgrade.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import text

from app import ref_cache
from app.enums import StatementBalanceEvidenceEnum, StatementSourceEnum
from app.extensions import db
from app.models.account import AccountAnchorHistory
from app.models.anchor_release import AnchorRelease
from app.models.statement_import import (
    BankStatementLine,
    StatementImport,
    StatementLineSighting,
)
from tests._test_helpers import (
    load_migration_module,
    run_migration_callable as _run,
)

_MIGRATION = load_migration_module("d2e9f4a17c63_one_level_relation.py")
#: The revision AFTER this one that touches the same tables (plan step
#: ``bank_import:X-f6b-1``: the sighting relation, and the import's window
#: under its declared name).  A round trip of ``d2e9f4a17c63`` at head has to
#: step it down first and up last, the way ``test_r18a_paycheck_lines_rename``
#: steps ``R18-b`` around ``R18-a``: a migration's downgrade runs on the
#: schema it left, and this one's ``create_check_constraint`` names
#: ``period_start``, which exists only below the later revision.
_LATER = load_migration_module(
    "af07125d00f1_a_line_is_held_by_its_sightings.py",
)
#: The revision after THAT one on the same tables (plan step
#: ``bank_import:X-f6b-1b``: the merchant key moves onto the sighting and
#: the line's column goes), stepped down before ``_LATER`` and up after it
#: for the same reason: ``af07125d00f1``'s upgrade backfills the sighting's
#: ``merchant`` word, which exists only below this revision.
_LATEST = load_migration_module(
    "3ef820b7dd52_the_sighting_names_the_merchant.py",
)
#: The revision that re-installs the append-only function with a FIFTH table
#: (plan step ``recurrence:R23``: a loan statement's withdrawal).  Stepped
#: down FIRST and up LAST for the same reason: this revision's downgrade drops
#: ``budget.refuse_append_only_change``, which that table's triggers still use
#: while it stands.
_R23 = load_migration_module(
    "cddb15ffba5f_a_mis_dated_loan_statement_is_withdrawn.py",
)
#: The revision that moves a source's id off the sighting into its own key
#: (plan step plan#2232).  Stepped down FIRST and up LAST, for ``_LATEST``'s
#: reason: ``_LATER``'s downgrade reads ``statement_line_sightings
#: .external_id``, which exists only below this revision.
_ID_KEY = load_migration_module("6534c1bfeeb1_a_bank_id_names_one_line.py")


def _sql(statement, **params):
    """Run one statement and return every row."""
    return db.session.execute(text(statement), params).all()


def _columns(table):
    """The column names of ``budget.<table>``, from the catalogue."""
    return {
        row[0] for row in _sql(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_schema = 'budget' AND table_name = :t", t=table,
        )
    }


def _trigger_count(pattern):
    """How many non-internal triggers match *pattern*."""
    return _sql(
        "SELECT count(*) FROM pg_trigger WHERE NOT tgisinternal "
        "AND tgname LIKE :p", p=pattern,
    )[0][0]


def _seed_placed_import(account, *, file_name, stated, day, period):
    """One import stating *stated*, placed on *day* -- through the head schema.

    It sights ONE line, because the later revision's downgrade refuses an
    import that sighted none (the schema it steps down to cannot hold a
    zero-line import), and a placed import in production always has lines.
    """
    statement = StatementImport(
        account_id=account.id,
        user_id=account.user_id,
        source_id=ref_cache.statement_source_id(
            StatementSourceEnum.SECU_CHECKING_CSV
        ),
        file_name=file_name,
        file_digest=file_name.ljust(64, "0")[:64],
        declared_start=period[0],
        declared_end=period[1],
        stated_balance=Decimal(stated),
        stated_balance_on=period[1],
    )
    db.session.add(statement)
    db.session.flush()
    line = BankStatementLine(
        account_id=account.id, posted_on=period[1], amount=Decimal("-1.00"),
        sequence_in_group=0,
    )
    db.session.add(line)
    db.session.flush()
    db.session.add(StatementLineSighting(
        account_id=account.id, line_id=line.id, import_id=statement.id,
        description=file_name,
    ))
    db.session.flush()
    level = AccountAnchorHistory(
        account_id=account.id,
        anchor_balance=Decimal(stated),
        observed_on=day,
        evidence_id=ref_cache.statement_balance_evidence_id(
            StatementBalanceEvidenceEnum.FILE_CHAIN,
        ),
        statement_import_id=statement.id,
    )
    db.session.add(level)
    db.session.flush()
    return statement, level


class TestTheRoundTrip:
    """Head -> down -> up, over a standing placement and a released one."""

    def test_down_writes_a_standing_placement_back_and_up_moves_it_in_again(
        self, db, seed_user,
    ):
        """The shipped downgrade and upgrade, graded on the rows they leave.

        Run in the ``db`` fixture's own app context rather than a nested one:
        a nested context scopes a SECOND session, and the fixture's session
        then sits idle-in-transaction holding ``ACCESS SHARE`` on the tables
        the seed touched while ``DROP TABLE`` waits for ``ACCESS EXCLUSIVE``
        until the cluster's ``lock_timeout`` -- the trap
        ``run_migration_callable`` names.
        """
        account = seed_user["account"]
        standing, _ = _seed_placed_import(
            account, file_name="standing.csv", stated="1085.00",
            day=date(2026, 3, 3),
            period=(date(2026, 3, 1), date(2026, 3, 3)),
        )
        released, released_level = _seed_placed_import(
            account, file_name="released.csv", stated="2000.00",
            day=date(2026, 4, 3),
            period=(date(2026, 4, 1), date(2026, 4, 3)),
        )
        db.session.add(AnchorRelease(
            account_id=account.id,
            anchor_id=released_level.id,
            released_by_import_id=standing.id,
            lines_changed_from=date(2026, 4, 2),
        ))
        db.session.commit()
        standing_id, released_id = standing.id, released.id
        owner_rows_before = _sql(
            "SELECT id, anchor_balance, observed_on FROM "
            "budget.account_anchor_history "
            "WHERE statement_import_id IS NULL ORDER BY id"
        )
        file_chain = ref_cache.statement_balance_evidence_id(
            StatementBalanceEvidenceEnum.FILE_CHAIN,
        )
        audit_updates_before = _sql(
            "SELECT count(*) FROM system.audit_log WHERE table_name = "
            "'account_anchor_history' AND operation = 'UPDATE'"
        )[0][0]

        _run(_ID_KEY.downgrade, db.session)
        _run(_R23.downgrade, db.session)
        _run(_LATEST.downgrade, db.session)
        _run(_LATER.downgrade, db.session)
        _run(_MIGRATION.downgrade, db.session)

        # The import row carries the placement again -- for the STANDING
        # level only; the released one writes back the NULL pair the old
        # release left.
        assert _sql(
            "SELECT balance_effective_on, balance_evidence_id "
            "FROM budget.statement_imports WHERE id = :i", i=standing_id,
        ) == [(date(2026, 3, 3), file_chain)]
        assert _sql(
            "SELECT balance_effective_on, balance_evidence_id "
            "FROM budget.statement_imports WHERE id = :i", i=released_id,
        ) == [(None, None)]
        # The bank rows are gone and the owner's are untouched.
        assert _columns("account_anchor_history").isdisjoint(
            {"evidence_id", "statement_import_id"},
        )
        assert _sql(
            "SELECT id, anchor_balance, observed_on FROM "
            "budget.account_anchor_history ORDER BY id"
        ) == owner_rows_before
        assert _sql("SELECT to_regclass('budget.anchor_releases')") == [
            (None,),
        ]
        # The three CHECKs are back, the refusal covers three tables, and
        # the within-file arms are gone.
        assert {
            row[0] for row in _sql(
                "SELECT conname FROM pg_constraint WHERE conrelid = "
                "'budget.statement_imports'::regclass AND contype = 'c'"
            )
        } >= {
            "ck_statement_imports_balance_evidence_paired",
            "ck_statement_imports_anchor_needs_a_claim",
            "ck_statement_imports_effective_day_within_file",
        }
        assert _trigger_count("ck\\_append\\_only%") == 9
        assert _trigger_count("ck\\_level\\_within\\_file") == 0
        assert _trigger_count("ck\\_file\\_span\\_holds\\_level") == 0

        _run(_MIGRATION.upgrade, db.session)
        _run(_LATER.upgrade, db.session)
        _run(_LATEST.upgrade, db.session)

        # The standing placement is a level again, keyed to its import
        # and its claim; the released one owns no level -- the release is
        # not representable below this revision, so the honest state is
        # "unplaced", which is what the old schema said of it.
        levels = _sql(
            "SELECT statement_import_id, anchor_balance, observed_on, "
            "evidence_id FROM budget.account_anchor_history "
            "WHERE statement_import_id IS NOT NULL"
        )
        assert levels == [
            (standing_id, Decimal("1085.00"), date(2026, 3, 3), file_chain),
        ]
        # The moved row is dated by the IMPORT ACT: its recording instant is
        # the import's own, and its recorded day that instant in the user's
        # timezone -- the derivation every backfill of ``recorded_on`` used.
        assert _sql(
            "SELECT h.created_at = si.created_at, "
            "       h.recorded_on = (si.created_at AT TIME ZONE "
            "                        'America/New_York')::date "
            "FROM budget.account_anchor_history h "
            "JOIN budget.statement_imports si ON si.id = h.statement_import_id"
        ) == [(True, True)]
        assert _sql("SELECT count(*) FROM budget.anchor_releases") == [
            (0,),
        ]
        assert _columns("statement_imports").isdisjoint(
            {"balance_effective_on", "balance_evidence_id"},
        )
        assert _trigger_count("ck\\_append\\_only%") == 12
        assert _trigger_count("ck\\_level\\_within\\_file") == 1
        assert _trigger_count("ck\\_file\\_span\\_holds\\_level") == 1
        assert _trigger_count("audit\\_anchor\\_releases") == 1

        # The owner's rows took the bottom rung by a FAST DEFAULT: the
        # value is there, no default remains, and no UPDATE was audited.
        uncorroborated = ref_cache.statement_balance_evidence_id(
            StatementBalanceEvidenceEnum.UNCORROBORATED,
        )
        assert _sql(
            "SELECT DISTINCT evidence_id FROM budget.account_anchor_history "
            "WHERE statement_import_id IS NULL"
        ) == [(uncorroborated,)]
        assert _sql(
            "SELECT column_default FROM information_schema.columns "
            "WHERE table_schema = 'budget' "
            "AND table_name = 'account_anchor_history' "
            "AND column_name = 'evidence_id'"
        ) == [(None,)]
        assert _sql(
            "SELECT count(*) FROM system.audit_log WHERE table_name = "
            "'account_anchor_history' AND operation = 'UPDATE'"
        )[0][0] == audit_updates_before

        _run(_R23.upgrade, db.session)
        _run(_ID_KEY.upgrade, db.session)

    def test_the_upgrade_refuses_a_database_without_the_evidence_ladder(
        self, db,
    ):
        """The one precondition, named rather than assumed.

        ``4c1f8b7e2a90`` seeds the ladder; a database without its
        ``uncorroborated`` row has no id to default the owner's rows to, and
        the revision says so instead of writing a wrong literal.
        """
        _run(_ID_KEY.downgrade, db.session)
        _run(_R23.downgrade, db.session)
        _run(_LATEST.downgrade, db.session)
        _run(_LATER.downgrade, db.session)
        _run(_MIGRATION.downgrade, db.session)
        db.session.execute(text(
            "DELETE FROM ref.statement_balance_evidence "
            "WHERE name = 'uncorroborated'"
        ))
        db.session.commit()

        with pytest.raises(RuntimeError, match="uncorroborated"):
            _run(_MIGRATION.upgrade, db.session)
        db.session.rollback()
