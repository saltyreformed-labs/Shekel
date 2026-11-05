"""Migration ``0a4d2c3e89f8`` -- a paycheck is base pay plus a list of lines.

Plan step **salary:R18-a** (ruling **R-SAL38**; ledger row **D59**): the
RENAME of ``salary.paycheck_deductions`` to ``salary.paycheck_lines``, of
``ref.deduction_timings`` to ``ref.paycheck_line_kinds`` (its two rows to
``pre_tax_deduction`` / ``post_tax_deduction``), of the kind column and of
the owning arm's column on ``budget.recurrence_rules`` -- with every
artifact the tables carry renamed beside them.  Every assertion reads the
CATALOGUE for the objects, both directions are driven through the
migration's own shipped callables, and the migration's own claims about
itself are graded hardest: that it moves every artifact (the catalogue is
compared whole, not by the names it lists), that a row survives the round
trip unchanged, that the audit trigger fires on the renamed table under
its new name and only its new name, and that the downgrade REFUSES a kind
name the old column cannot hold.

**Leaf R18-b (``6c15d2a97b78``) sits above this revision** and seeds the two
earning kinds, whose names the pre-rename column cannot hold; so the round
trip here runs R18-b's own ``downgrade()`` first and its ``upgrade()`` last
(Alembic's newest-first order, the stacked shape of
:func:`~tests._test_helpers.rewind_pay_schedule_rhythm`), and the refusal
case needs no seeded row of its own: R18-b's rows ARE the refusal.
"""
from __future__ import annotations

from decimal import Decimal

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DataError

from app.audit_infrastructure import EXPECTED_TRIGGER_COUNT
from app.extensions import db
from app.models.paycheck_line import PaycheckLine
from app.models.ref import CalcMethod, PaycheckLineKind
from app.utils.db_errors import sqlstate_of
from tests._test_helpers import (
    load_migration_module,
    make_salary_profile,
    run_migration_callable as _run,
)

#: This revision, loaded so its own callables (and its own rename tables)
#: are what this file drives and reads.
_M_R18A = load_migration_module("0a4d2c3e89f8_a_paycheck_is_a_list_of_lines.py")
#: The revision above it, whose two earning-kind rows must go before this
#: one's downgrade can narrow the name column, and come back after.
_M_R18B = load_migration_module("6c15d2a97b78_a_paycheck_has_earning_lines.py")
#: The revision that split the pre-tax kind (plan step salary:S11-e), whose
#: row must go first on the way down, its name being too long for the column
#: this revision's downgrade narrows too.
_M_S11E = load_migration_module("47beea2b628a_pre_tax_splits_in_two.py")

#: The kinds head carries (rulings R-SAL38 and plan#2639), in the enum's
#: waterfall order.
_HEAD_KINDS = {
    "taxable_earning", "pre_fica_deduction", "pre_tax_deduction", "post_tax_deduction",
    "after_tax_earning",
}

_LINES = ("salary", "paycheck_lines")
_OLD_LINES = ("salary", "paycheck_deductions")
_KINDS = ("ref", "paycheck_line_kinds")
_OLD_KINDS = ("ref", "deduction_timings")
_RULES = ("budget", "recurrence_rules")

#: Constraints a LATER revision added to a renamed table, by name.  The
#: exact-set assertion admits these and nothing else, so a constraint R18-a's
#: rename tables forgot still fails, and so does any other stranger.
#: ``uq_paycheck_lines_id_profile`` is plan step salary:S11-a's superkey
#: (``5641f7729b68``), the target of a transcribed stub's line key; the
#: developer confirmed this test admits it, 2026-09-23.
_ADDED_SINCE = {
    _LINES: {"uq_paycheck_lines_id_profile"},
}


def _columns(session, schema, table) -> set[str]:
    """Return a table's column names, read from the catalogue."""
    return {
        row[0] for row in session.execute(text(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_schema = :schema AND table_name = :table"
        ), {"schema": schema, "table": table})
    }


def _constraints(session, schema, table) -> dict[str, str]:
    """Return a table's constraints as ``{name: definition}``, every kind."""
    return dict(session.execute(text(
        "SELECT conname, pg_get_constraintdef(c.oid) FROM pg_constraint c "
        "  JOIN pg_class t ON t.oid = c.conrelid "
        "  JOIN pg_namespace n ON n.oid = t.relnamespace "
        " WHERE n.nspname = :schema AND t.relname = :table"
    ), {"schema": schema, "table": table}).all())


def _indexes(session, schema, table) -> dict[str, str]:
    """Return a table's indexes as ``{name: definition}``."""
    return dict(session.execute(text(
        "SELECT indexname, indexdef FROM pg_indexes "
        "WHERE schemaname = :schema AND tablename = :table"
    ), {"schema": schema, "table": table}).all())


def _triggers(session, schema, table) -> set[str]:
    """Return the non-internal trigger names on a table."""
    return {
        row[0] for row in session.execute(text(
            "SELECT tgname FROM pg_trigger "
            " WHERE NOT tgisinternal AND tgrelid = (:table)::regclass"
        ), {"table": f"{schema}.{table}"})
    }


def _sequences(session, schema) -> set[str]:
    """Return every sequence name in a schema."""
    return {
        row[0] for row in session.execute(text(
            "SELECT sequence_name FROM information_schema.sequences "
            " WHERE sequence_schema = :schema"
        ), {"schema": schema})
    }


def _table_exists(session, schema, table) -> bool:
    """Whether ``schema.table`` is in the catalogue."""
    return bool(session.execute(text(
        "SELECT 1 FROM information_schema.tables "
        " WHERE table_schema = :schema AND table_name = :table"
    ), {"schema": schema, "table": table}).scalar())


def _kind_names(session, schema, table) -> set[str]:
    """The ref table's row names."""
    return {
        row[0] for row in session.execute(text(f"SELECT name FROM {schema}.{table}"))
    }


def _snapshot(session) -> dict:
    """Everything this revision touches, keyed for a whole-catalogue equality."""
    return {
        "lines_columns": _columns(session, *_LINES),
        "lines_constraints": _constraints(session, *_LINES),
        "lines_indexes": _indexes(session, *_LINES),
        "lines_triggers": _triggers(session, *_LINES),
        "kinds_columns": _columns(session, *_KINDS),
        "kinds_constraints": _constraints(session, *_KINDS),
        "kinds_indexes": _indexes(session, *_KINDS),
        "kinds_names": _kind_names(session, *_KINDS),
        "rules_columns": _columns(session, *_RULES),
        "rules_constraints": _constraints(session, *_RULES),
        "rules_indexes": _indexes(session, *_RULES),
        "salary_sequences": _sequences(session, "salary"),
        "ref_sequences": _sequences(session, "ref"),
    }


def _seed_line(seed_user, name="Dental", amount="40.00"):
    """One flat pre-tax line on a fresh profile; returns its id."""
    profile = make_salary_profile(seed_user, db.session)
    db.session.flush()
    kind = db.session.query(PaycheckLineKind).filter_by(name="pre_tax_deduction").one()
    method = db.session.query(CalcMethod).filter_by(name="flat").one()
    line = PaycheckLine(
        salary_profile=profile, paycheck_line_kind_id=kind.id,
        calc_method_id=method.id, name=name, amount=Decimal(amount),
    )
    db.session.add(line)
    db.session.commit()
    return line.id


@pytest.mark.xdist_group("recurrence_rules_ddl")
class TestHeadCarriesTheNewNamesOnly:
    """The template is at head: every artifact under its new name, none under the old."""

    def test_the_tables_and_columns(self, app, db):
        """The two tables exist under the new names alone; both columns are renamed."""
        with app.app_context():
            assert _table_exists(db.session, *_LINES)
            assert not _table_exists(db.session, *_OLD_LINES)
            assert _table_exists(db.session, *_KINDS)
            assert not _table_exists(db.session, *_OLD_KINDS)
            lines = _columns(db.session, *_LINES)
            assert "paycheck_line_kind_id" in lines
            assert "deduction_timing_id" not in lines
            rules = _columns(db.session, *_RULES)
            assert "paycheck_line_id" in rules
            assert "paycheck_deduction_id" not in rules

    def test_the_constraint_set_is_exactly_the_migrations_new_names(self, app, db):
        """The renamed tables' constraint sets EQUAL the migration's ``new`` sets.

        Set equality rather than membership, so a constraint the migration's
        rename tables FORGOT -- one the catalogue carries under its old name
        with no pair to rename it -- fails here instead of surviving: a
        membership check over the migration's own tables is circular, and
        the whole-catalogue round trip below is blind to a pair that was
        never renamed in either direction (an adversarial review of this
        leaf).  The arm's FK is one constraint among the rules table's many,
        so that table is checked by membership plus the residue sweep.  A
        constraint a later revision added is admitted BY NAME
        (:data:`_ADDED_SINCE`), never by loosening the equality.
        """
        with app.app_context():
            for table, pairs in (
                (_LINES, _M_R18A._LINE_CONSTRAINTS),  # pylint: disable=protected-access
                (_KINDS, _M_R18A._KIND_CONSTRAINTS),  # pylint: disable=protected-access
            ):
                assert set(_constraints(db.session, *table)) == {
                    new for _old, new in pairs
                } | _ADDED_SINCE.get(table, set()), table[1]
            rules = _constraints(db.session, *_RULES)
            for old, new in _M_R18A._ARM_CONSTRAINTS:  # pylint: disable=protected-access
                assert new in rules and old not in rules, (old, new)
            # The arc CHECK followed its column without being touched.
            assert "paycheck_line_id" in rules["ck_recurrence_rules_one_owner"]
            assert "paycheck_deduction_id" not in rules["ck_recurrence_rules_one_owner"]

    def test_no_artifact_on_the_three_tables_carries_an_old_name(self, app, db):
        """No constraint, index, sequence or trigger name still spells the old objects."""
        with app.app_context():
            names = set()
            for table in (_LINES, _KINDS, _RULES):
                names |= set(_constraints(db.session, *table))
                names |= set(_indexes(db.session, *table))
                names |= _triggers(db.session, *table)
            names |= _sequences(db.session, "salary") | _sequences(db.session, "ref")
            stale = {
                name for name in names
                if "paycheck_deduction" in name
                or "deduction_timing" in name
                or "deductions_profile" in name
            }
            assert not stale, stale

    def test_the_indexes_sequences_trigger_and_rows(self, app, db):
        """The child-FK index, the arm's unique index, both sequences, the trigger, the names."""
        with app.app_context():
            assert "idx_paycheck_lines_profile" in _indexes(db.session, *_LINES)
            assert "idx_deductions_profile" not in _indexes(db.session, *_LINES)
            rule_indexes = _indexes(db.session, *_RULES)
            assert "uq_recurrence_rules_paycheck_line_id" in rule_indexes
            assert "uq_recurrence_rules_paycheck_deduction_id" not in rule_indexes
            assert "paycheck_lines_id_seq" in _sequences(db.session, "salary")
            assert "paycheck_deductions_id_seq" not in _sequences(db.session, "salary")
            assert "paycheck_line_kinds_id_seq" in _sequences(db.session, "ref")
            assert "deduction_timings_id_seq" not in _sequences(db.session, "ref")
            assert _triggers(db.session, *_LINES) == {"audit_paycheck_lines"}
            assert _kind_names(db.session, *_KINDS) == _HEAD_KINDS

    def test_the_audit_trigger_fires_once_under_its_new_name(self, app, db, seed_user):
        """A write to the renamed table logs ONE row naming ``paycheck_lines``.

        The ``hysa_params`` lesson (``44893a9dbcc3``): a trigger left behind
        under the old name beside the new one logs every write twice.  The
        trigger census is the entrypoint's own count.
        """
        with app.app_context():
            line_id = _seed_line(seed_user)
            rows = db.session.execute(text(
                "SELECT table_name FROM system.audit_log "
                " WHERE row_id = :id AND operation = 'INSERT' "
                "   AND table_name IN ('paycheck_lines', 'paycheck_deductions')"
            ), {"id": line_id}).all()
            assert [row[0] for row in rows] == ["paycheck_lines"]
            assert db.session.execute(text(
                "SELECT count(*) FROM pg_trigger WHERE tgname LIKE 'audit_%'"
            )).scalar() == EXPECTED_TRIGGER_COUNT


@pytest.mark.xdist_group("recurrence_rules_ddl")
class TestTheRoundTrip:
    """Down restores every old name; up restores the whole catalogue; the row rides through."""

    def test_down_then_up_restores_the_whole_catalogue(self, app, db, seed_user):
        """Whole-catalogue equality after down-then-up, with a line's data intact.

        Graded against the CATALOGUE rather than the migration's own rename
        tables, so an artifact the tables forgot -- a NOT NULL, a sequence,
        the trigger -- fails here rather than surviving under a stale name.
        """
        with app.app_context():
            line_id = _seed_line(seed_user, "Vision", "12.06")
            before = _snapshot(db.session)

            _run(_M_S11E.downgrade, db.session)
            _run(_M_R18B.downgrade, db.session)
            assert _kind_names(db.session, *_KINDS) == {
                "pre_tax_deduction", "post_tax_deduction",
            }
            _run(_M_R18A.downgrade, db.session)

            assert _table_exists(db.session, *_OLD_LINES)
            assert not _table_exists(db.session, *_LINES)
            assert _table_exists(db.session, *_OLD_KINDS)
            assert not _table_exists(db.session, *_KINDS)
            old_lines = _constraints(db.session, *_OLD_LINES)
            for old, new in _M_R18A._LINE_CONSTRAINTS:  # pylint: disable=protected-access
                assert old in old_lines and new not in old_lines, (old, new)
            old_kinds = _constraints(db.session, *_OLD_KINDS)
            for old, new in _M_R18A._KIND_CONSTRAINTS:  # pylint: disable=protected-access
                assert old in old_kinds and new not in old_kinds, (old, new)
            rules = _constraints(db.session, *_RULES)
            for old, new in _M_R18A._ARM_CONSTRAINTS:  # pylint: disable=protected-access
                assert old in rules and new not in rules, (old, new)
            assert "paycheck_deduction_id" in _columns(db.session, *_RULES)
            assert "deduction_timing_id" in _columns(db.session, *_OLD_LINES)
            assert "idx_deductions_profile" in _indexes(db.session, *_OLD_LINES)
            assert "uq_recurrence_rules_paycheck_deduction_id" in _indexes(
                db.session, *_RULES,
            )
            assert "paycheck_deductions_id_seq" in _sequences(db.session, "salary")
            assert "deduction_timings_id_seq" in _sequences(db.session, "ref")
            assert _triggers(db.session, *_OLD_LINES) == {"audit_paycheck_deductions"}
            assert _kind_names(db.session, *_OLD_KINDS) == {"pre_tax", "post_tax"}
            # The row rode the rename down, under the old column name.
            assert db.session.execute(text(
                "SELECT amount FROM salary.paycheck_deductions WHERE id = :id"
            ), {"id": line_id}).scalar() == Decimal("12.0600")

            _run(_M_R18A.upgrade, db.session)
            _run(_M_R18B.upgrade, db.session)
            _run(_M_S11E.upgrade, db.session)

            assert _snapshot(db.session) == before
            db.session.expire_all()
            line = db.session.get(PaycheckLine, line_id)
            assert (line.name, line.amount) == ("Vision", Decimal("12.0600"))
            assert line.paycheck_line_kind.name == "pre_tax_deduction"

    def test_the_downgrade_refuses_a_kind_name_the_old_column_cannot_hold(
        self, app, db,
    ):
        """A kind row longer than ten characters -- R18-b's -- stops the downgrade.

        The migration's own claim about itself: the narrowing cast refuses
        rather than truncating, so a tree that seeded the earning kinds
        cannot be downgraded past them silently -- and head IS such a tree,
        so this downgrade is run with R18-b's rows in place.  The refusal is
        PostgreSQL's ``StringDataRightTruncation``, surfaced as a
        :class:`~sqlalchemy.exc.DataError`.
        """
        with app.app_context():
            assert _kind_names(db.session, *_KINDS) == _HEAD_KINDS

            with pytest.raises(DataError) as excinfo:
                _run(_M_R18A.downgrade, db.session)
            db.session.rollback()
            # 22001 is ``string_data_right_truncation``: the VARCHAR(10) cast
            # and nothing else in that downgrade can raise it.
            assert sqlstate_of(excinfo.value) == "22001", excinfo.value
            assert "character varying(10)" in str(excinfo.value.orig)

            # Refused means untouched: the DDL ran in one transaction and
            # rolled back with the cast that failed.
            assert _table_exists(db.session, *_LINES)
            assert not _table_exists(db.session, *_OLD_LINES)
            assert _kind_names(db.session, *_KINDS) == _HEAD_KINDS
