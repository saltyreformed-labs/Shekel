"""Tests for the 858f569b2c17 "the old calibration table goes" migration.

Plan step **salary:S11-d** (card plan#1155; rulings **R-SAL42** fork 9,
**R-SAL124**, **R-SAL127**).  The revision refuses while a calibration the
database still records has no pay stub on its date, prints each one's four
taxes beside its stub's, and drops ``salary.calibration_overrides``; the same
change deletes its model, its ``calibration`` backref and its
``AUDITED_TABLES`` row.

**Both directions are EXECUTED here.**  The ``db`` fixture re-clones this
worker's own database for every test, so the DDL reaches no other worker and
survives no other case: each refusal case runs the downgrade (the table back,
empty), seeds the state it grades, then runs the real upgrade.  The rows a
change log holds are written the way production's were, by an audit trigger
attached to the recreated table for the case; nothing writes
``system.audit_log`` directly.

Every refusal and printout case's job holds one stub, on 2026-03-27, with
taxes $150.00 / $100.00 / $180.00 / $42.00 (the ``world`` fixture).
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import inspect, text

from app import ref_cache
from app.audit_infrastructure import AUDITED_TABLES
from app.enums import WithholdingKindEnum
from app.models.pay_stub import PayStub, PayStubWithholding
from app.models.salary_profile import SalaryProfile
from tests._test_helpers import load_migration_module, make_salary_profile

_MIGRATION = load_migration_module("858f569b2c17_the_old_calibration_table_goes.py")

#: The payday of the one stub every refusal case's job holds.
PAYDAY = date(2026, 3, 27)

#: A date the job has no stub on.
_NO_STUB_DAY = date(2026, 3, 13)

#: The stub's four taxes, which :func:`_insert_calibration` copies by default.
_STUB_TAXES = {
    WithholdingKindEnum.FEDERAL_INCOME: Decimal("150.00"),
    WithholdingKindEnum.STATE_INCOME: Decimal("100.00"),
    WithholdingKindEnum.SOCIAL_SECURITY: Decimal("180.00"),
    WithholdingKindEnum.MEDICARE: Decimal("42.00"),
}


@pytest.fixture(name="world")
def _world(seed_user, db):
    """A job and its one pay stub on 2026-03-27 with the four taxes, written past the door.

    The door's checks are not this file's subject: the migration reads only a
    stub's job, payday and withholdings, so the stub carries nothing else.
    """
    profile = make_salary_profile(seed_user, db.session, name="Day Job")
    stub = PayStub(salary_profile_id=profile.id, payday=PAYDAY, base_pay=Decimal("2884.62"))
    db.session.add(stub)
    db.session.flush()
    for member, amount in _STUB_TAXES.items():
        db.session.add(PayStubWithholding(
            pay_stub_id=stub.id, withholding_kind_id=ref_cache.withholding_kind_id(member),
            amount=amount,
        ))
    db.session.commit()
    return {"profile": profile, "stub": stub}


def _run(session, direction: str) -> None:
    """Execute the revision's own ``upgrade`` or ``downgrade`` on *session*'s connection.

    Installs Alembic's operation proxy over the test's connection, so the
    migration's module-level ``op`` calls run inside the per-test transaction.

    Args:
        session: The test session.
        direction: ``"upgrade"`` or ``"downgrade"``.
    """
    context = MigrationContext.configure(session.connection())
    with Operations.context(context):
        getattr(_MIGRATION, direction)()


def _table_exists(session) -> bool:
    """Return whether ``salary.calibration_overrides`` exists."""
    return session.execute(text(
        "SELECT to_regclass('salary.calibration_overrides') IS NOT NULL"
    )).scalar_one()


def _attach_the_audit_trigger(session) -> None:
    """Attach production's audit trigger to the recreated table, for this case only.

    The downgrade recreates the table untriggered (the table is no longer in
    ``AUDITED_TABLES``); production's table carried this trigger until the
    drop, and its rows are what the refusal reads from the change log.
    """
    session.execute(text(
        "CREATE TRIGGER audit_calibration_overrides "
        "AFTER INSERT OR UPDATE OR DELETE ON salary.calibration_overrides "
        "FOR EACH ROW EXECUTE FUNCTION system.audit_trigger_func()"
    ))


def _insert_calibration(session, profile_id: int, day: date, **taxes) -> int:
    """Insert one calibration and return its id.

    Args:
        session: The test session.
        profile_id: Its salary profile.
        day: Its ``pay_stub_date``.
        **taxes: ``actual_*`` columns to replace the job's stub's taxes
            (:data:`_STUB_TAXES`), which are the defaults.

    Returns:
        The new row's id.
    """
    figures = {
        "actual_gross_pay": Decimal("2999.62"),
        "actual_federal_tax": _STUB_TAXES[WithholdingKindEnum.FEDERAL_INCOME],
        "actual_state_tax": _STUB_TAXES[WithholdingKindEnum.STATE_INCOME],
        "actual_social_security": _STUB_TAXES[WithholdingKindEnum.SOCIAL_SECURITY],
        "actual_medicare": _STUB_TAXES[WithholdingKindEnum.MEDICARE],
    }
    figures.update(taxes)
    return session.execute(text(
        "INSERT INTO salary.calibration_overrides "
        "(salary_profile_id, actual_gross_pay, actual_federal_tax, actual_state_tax, "
        " actual_social_security, actual_medicare, effective_federal_rate, "
        " effective_state_rate, effective_ss_rate, effective_medicare_rate, pay_stub_date) "
        "VALUES (:profile, :actual_gross_pay, :actual_federal_tax, :actual_state_tax, "
        " :actual_social_security, :actual_medicare, 0.05, 0.03, 0.06, 0.014, :day) "
        "RETURNING id"
    ), {"profile": profile_id, "day": day, **figures}).scalar_one()


class TestChaining:
    """The revision sits where it claims in the Alembic chain."""

    def test_revision_and_down_revision(self):
        """revision / down_revision pin the migration into the chain."""
        assert _MIGRATION.revision == "858f569b2c17"
        assert _MIGRATION.down_revision == "69634349bdce"

    def test_it_is_reviewed(self):
        """A destructive migration carries the ``Review:`` line, naming R-SAL124.

        ``.claude/rules/database.md``: a migration that drops needs developer
        approval recorded in the module docstring.
        """
        assert "Review:" in _MIGRATION.__doc__
        assert "R-SAL124" in _MIGRATION.__doc__


class TestTheTableIsGoneAtHead:
    """At head the table, its trigger, its audited entry and its backref are gone."""

    def test_the_table_does_not_exist(self, db):
        """The upgrade's whole point, against the live catalogue."""
        assert _table_exists(db.session) is False

    def test_no_audit_trigger_is_named_for_it(self, db):
        """No trigger anywhere carries the dropped table's audit trigger name."""
        found = db.session.execute(text(
            "SELECT count(*) FROM pg_trigger "
            " WHERE tgname = 'audit_calibration_overrides'"
        )).scalar_one()
        assert found == 0

    def test_the_audited_list_does_not_name_it(self):
        """``AUDITED_TABLES`` lost the row, so the trigger census expects one fewer."""
        assert ("salary", "calibration_overrides") not in AUDITED_TABLES

    def test_a_salary_profile_has_no_calibration_relationship(self):
        """The ``calibration`` backref went with the model (and its joined load)."""
        assert "calibration" not in inspect(SalaryProfile).relationships


class TestTheDowngradeRecreatesItEmpty:
    """The downgrade brings the table back EMPTY, in the shape production held (R-SAL124)."""

    def test_the_columns_are_production_s(self, db):
        """Sixteen columns, in production's order, types, nullability and defaults.

        Measured on production's catalogue at ``1712c506ee99`` (the table's
        last change was ``c5d20b701a4e``), 2026-11-02.
        """
        _run(db.session, "downgrade")
        columns = db.session.execute(text(
            "SELECT column_name, data_type, numeric_precision, numeric_scale, "
            "       is_nullable, column_default "
            "  FROM information_schema.columns "
            " WHERE table_schema = 'salary' AND table_name = 'calibration_overrides' "
            " ORDER BY ordinal_position"
        )).all()
        money, rate = ("numeric", 10, 2, "NO", None), ("numeric", 12, 10, "NO", None)
        assert [tuple(row) for row in columns] == [
            ("id", "integer", 32, 0, "NO",
             "nextval('salary.calibration_overrides_id_seq'::regclass)"),
            ("salary_profile_id", "integer", 32, 0, "NO", None),
            ("actual_gross_pay", *money),
            ("actual_federal_tax", *money),
            ("actual_state_tax", *money),
            ("actual_social_security", *money),
            ("actual_medicare", *money),
            ("effective_federal_rate", *rate),
            ("effective_state_rate", *rate),
            ("effective_ss_rate", *rate),
            ("effective_medicare_rate", *rate),
            ("pay_stub_date", "date", None, None, "NO", None),
            ("is_active", "boolean", None, None, "NO", "true"),
            ("notes", "text", None, None, "YES", None),
            ("created_at", "timestamp with time zone", None, None, "NO", "now()"),
            ("updated_at", "timestamp with time zone", None, None, "NO", "now()"),
        ]

    def test_the_constraints_are_production_s(self, db):
        """Every key and CHECK, by production's names and definitions (measured 2026-11-02)."""
        _run(db.session, "downgrade")
        constraints = db.session.execute(text(
            "SELECT conname, pg_get_constraintdef(oid) FROM pg_constraint "
            " WHERE conrelid = 'salary.calibration_overrides'::regclass "
            "   AND contype <> 'n' ORDER BY conname"
        )).all()
        assert [tuple(row) for row in constraints] == [
            ("calibration_overrides_pkey", "PRIMARY KEY (id)"),
            ("calibration_overrides_salary_profile_id_fkey",
             "FOREIGN KEY (salary_profile_id) REFERENCES salary.salary_profiles(id) "
             "ON DELETE CASCADE"),
            ("ck_calibration_overrides_nonneg_federal",
             "CHECK ((actual_federal_tax >= (0)::numeric))"),
            ("ck_calibration_overrides_nonneg_medicare",
             "CHECK ((actual_medicare >= (0)::numeric))"),
            ("ck_calibration_overrides_nonneg_ss",
             "CHECK ((actual_social_security >= (0)::numeric))"),
            ("ck_calibration_overrides_nonneg_state",
             "CHECK ((actual_state_tax >= (0)::numeric))"),
            ("ck_calibration_overrides_positive_gross",
             "CHECK ((actual_gross_pay > (0)::numeric))"),
            ("ck_calibration_overrides_valid_federal_rate",
             "CHECK (((effective_federal_rate >= (0)::numeric) AND "
             "(effective_federal_rate <= (1)::numeric)))"),
            ("ck_calibration_overrides_valid_medicare_rate",
             "CHECK (((effective_medicare_rate >= (0)::numeric) AND "
             "(effective_medicare_rate <= (1)::numeric)))"),
            ("ck_calibration_overrides_valid_ss_rate",
             "CHECK (((effective_ss_rate >= (0)::numeric) AND "
             "(effective_ss_rate <= (1)::numeric)))"),
            ("ck_calibration_overrides_valid_state_rate",
             "CHECK (((effective_state_rate >= (0)::numeric) AND "
             "(effective_state_rate <= (1)::numeric)))"),
            ("uq_calibration_overrides_profile", "UNIQUE (salary_profile_id)"),
        ]

    def test_it_comes_back_empty_and_untriggered(self, db):
        """No row is restored (R-SAL124) and no audit trigger is attached."""
        _run(db.session, "downgrade")
        rows = db.session.execute(text(
            "SELECT count(*) FROM salary.calibration_overrides"
        )).scalar_one()
        triggers = db.session.execute(text(
            "SELECT count(*) FROM pg_trigger "
            " WHERE tgrelid = 'salary.calibration_overrides'::regclass "
            "   AND NOT tgisinternal"
        )).scalar_one()
        assert (rows, triggers) == (0, 0)

    def test_the_upgrade_runs_again_after_it(self, db, capsys):
        """Down then up: an empty table and an empty log pass, and the table goes."""
        _run(db.session, "downgrade")
        _run(db.session, "upgrade")
        gone = not _table_exists(db.session)
        printed = capsys.readouterr().out
        assert gone
        assert "S11-d: 0 old calibration(s) found" in printed
        assert "S11-d: salary.calibration_overrides dropped with its audit trigger." in printed


class TestTheRefusal:
    """Fork 9 as R-SAL124 approved it: no drop while a held calibration lacks its stub.

    Each case records the worked stub (2026-03-27), recreates the table by the
    downgrade, seeds the calibration state it grades and runs the real upgrade.
    A refusal writes nothing: the table and its rows are still there.
    """

    @staticmethod
    def _refusal(session) -> str:
        """Run the upgrade, which must refuse; return its message after checking nothing moved."""
        before = session.execute(text(
            "SELECT count(*) FROM salary.calibration_overrides"
        )).scalar_one()
        with pytest.raises(RuntimeError) as refused:
            _run(session, "upgrade")
        assert _table_exists(session)
        assert session.execute(text(
            "SELECT count(*) FROM salary.calibration_overrides"
        )).scalar_one() == before
        return str(refused.value)

    def test_a_live_calibration_without_its_stub_refuses(self, db, world):
        """A live row dated 03-13, where no stub is: refused, naming the date and the job."""
        _run(db.session, "downgrade")
        _insert_calibration(db.session, world["profile"].id, _NO_STUB_DAY)
        message = self._refusal(db.session)
        profile = world["profile"]
        assert (
            f"2026-03-13 for job '{profile.name}' (salary profile {profile.id})"
        ) in message
        assert message.startswith("S11-d refuses: an old calibration has no pay stub")
        assert message.endswith("Nothing was written: the revision rolls back whole.")

    def test_a_live_calibration_with_its_stub_passes(self, db, world):
        """A live row dated on the stub's payday: the upgrade runs and the table goes."""
        _run(db.session, "downgrade")
        _insert_calibration(db.session, world["profile"].id, PAYDAY)
        _run(db.session, "upgrade")
        assert not _table_exists(db.session)

    def test_a_calibration_only_a_logged_delete_holds_refuses(self, db, world):
        """Inserted before the log began, deleted after: its DELETE's ``old_data`` alone counts.

        Production's 2026-03-26 calibration has this shape: the change log
        began after the row was written, so the only record of it is the
        DELETE that replaced it.
        """
        _run(db.session, "downgrade")
        row_id = _insert_calibration(db.session, world["profile"].id, _NO_STUB_DAY)
        _attach_the_audit_trigger(db.session)
        db.session.execute(text(
            "DELETE FROM salary.calibration_overrides WHERE id = :id"
        ), {"id": row_id})
        assert "2026-03-13 for job" in self._refusal(db.session)

    def test_a_state_logged_twice_is_named_once(self, db, world):
        """Inserted and deleted under the trigger: the two images are one state.

        The date is named ONCE however many images of the table carry it.
        """
        _run(db.session, "downgrade")
        _attach_the_audit_trigger(db.session)
        row_id = _insert_calibration(db.session, world["profile"].id, _NO_STUB_DAY)
        db.session.execute(text(
            "DELETE FROM salary.calibration_overrides WHERE id = :id"
        ), {"id": row_id})
        message = self._refusal(db.session)
        assert message.count("2026-03-13 for job") == 1

    def test_the_date_an_update_moved_away_from_still_counts(self, db, world):
        """A row re-dated from 03-13 to the stub's payday: its UPDATE's ``old_data`` refuses.

        Written before the log began, so the UPDATE's old image is the only
        record of 03-13; the live row has its stub.
        """
        _run(db.session, "downgrade")
        row_id = _insert_calibration(db.session, world["profile"].id, _NO_STUB_DAY)
        _attach_the_audit_trigger(db.session)
        db.session.execute(text(
            "UPDATE salary.calibration_overrides SET pay_stub_date = :day WHERE id = :id"
        ), {"day": PAYDAY, "id": row_id})
        message = self._refusal(db.session)
        assert "2026-03-13 for job" in message
        assert "2026-03-27" not in message

    def test_a_state_only_a_logged_insert_holds_still_counts(self, db, world):
        """Logged when inserted, re-dated with no trigger: the INSERT's ``new_data`` counts.

        A trigger can be absent for a time (a replay gap, or a trigger dropped
        by hand); the state it recorded before that is still a record.
        """
        _run(db.session, "downgrade")
        _attach_the_audit_trigger(db.session)
        row_id = _insert_calibration(db.session, world["profile"].id, _NO_STUB_DAY)
        db.session.execute(text(
            "DROP TRIGGER audit_calibration_overrides ON salary.calibration_overrides"
        ))
        db.session.execute(text(
            "UPDATE salary.calibration_overrides SET pay_stub_date = :day WHERE id = :id"
        ), {"day": PAYDAY, "id": row_id})
        assert "2026-03-13 for job" in self._refusal(db.session)

    def test_a_stub_of_another_job_on_that_date_does_not_count(
        self, db, world, seed_second_user,
    ):
        """A second owner's job has a calibration on 03-27, where only the first job has a stub."""
        assert world["stub"].payday == PAYDAY
        other = make_salary_profile(seed_second_user, db.session, name="Night Job")
        db.session.commit()
        _run(db.session, "downgrade")
        _insert_calibration(db.session, other.id, PAYDAY)
        message = self._refusal(db.session)
        assert f"2026-03-27 for job '{other.name}' (salary profile {other.id})" in message

    def test_a_stub_switched_off_for_pricing_still_counts(self, db, world):
        """The switch says whether a stub prices, not whether it is transcribed."""
        stub = world["stub"]
        db.session.execute(text(
            "UPDATE salary.pay_stubs SET use_for_pricing = false WHERE id = :id"
        ), {"id": stub.id})
        _run(db.session, "downgrade")
        _insert_calibration(db.session, world["profile"].id, PAYDAY)
        _run(db.session, "upgrade")
        assert not _table_exists(db.session)

    def test_a_job_that_no_longer_exists_is_worded_so(self):
        """A calibration's job deleted since cannot take a stub, and the words say so."""
        assert _MIGRATION.job_words(7, {}) == "salary profile 7, which no longer exists"
        assert _MIGRATION.job_words(7, {7: "Day job"}) == "job 'Day job' (salary profile 7)"


class TestThePrintout:
    """R-SAL127: each of the four taxes that differs from the stub's, "then" and "on the stub"."""

    def test_differing_taxes_are_named_and_agreeing_ones_are_not(self, db, world, capsys):
        """State $91.00 then against the stub's $100.00, and Medicare $40.00 against $42.00."""
        _run(db.session, "downgrade")
        row_id = _insert_calibration(
            db.session, world["profile"].id, PAYDAY,
            actual_state_tax=Decimal("91.00"), actual_medicare=Decimal("40.00"),
        )
        _run(db.session, "upgrade")
        profile = world["profile"]
        assert (
            f"S11-d: the old calibration of 2026-03-27 (job '{profile.name}' "
            f"(salary profile {profile.id}), calibration {row_id}) and its pay stub "
            "differ: State income tax $91.00 then, $100.00 on the stub; "
            "Medicare $40.00 then, $42.00 on the stub."
        ) in capsys.readouterr().out.splitlines()

    def test_four_agreeing_taxes_say_so(self, db, world, capsys):
        """The worked stub's own four taxes: one line saying they match."""
        _run(db.session, "downgrade")
        row_id = _insert_calibration(db.session, world["profile"].id, PAYDAY)
        _run(db.session, "upgrade")
        printed = capsys.readouterr().out
        assert f"calibration {row_id}) matches its pay stub's four taxes." in printed
        assert "S11-d: 1 old calibration(s) found" in printed

    def test_a_tax_the_stub_does_not_record_reads_as_none(self, db, world, capsys):
        """A stub missing its Medicare row prints "none on the stub" beside the old figure."""
        stub = world["stub"]
        db.session.execute(text(
            "DELETE FROM salary.pay_stub_withholdings w USING ref.withholding_kinds k "
            " WHERE w.pay_stub_id = :id AND k.id = w.withholding_kind_id "
            "   AND k.name = 'medicare'"
        ), {"id": stub.id})
        _run(db.session, "downgrade")
        _insert_calibration(db.session, world["profile"].id, PAYDAY)
        _run(db.session, "upgrade")
        assert "differ: Medicare $42.00 then, none on the stub." in capsys.readouterr().out

    def test_one_state_held_twice_prints_once(self, db, world, capsys):
        """A live row's INSERT in the change log is the same state as the row: one line."""
        _run(db.session, "downgrade")
        _attach_the_audit_trigger(db.session)
        _insert_calibration(db.session, world["profile"].id, PAYDAY)
        _run(db.session, "upgrade")
        printed = capsys.readouterr().out
        assert printed.count("matches its pay stub's four taxes.") == 1
        assert "S11-d: 1 old calibration(s) found" in printed
