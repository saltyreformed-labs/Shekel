"""
Shekel Budget App -- YTD Tax Checkpoint Model Tests (T-P2)

Storage-tier guarantees for ``salary.ytd_tax_checkpoints``: the
non-negativity and ``component <= gross`` CHECK constraints, the
``(salary_profile_id, as_of_date)`` unique constraint, the CASCADE FK to
``salary.salary_profiles``, and the AUDITED_TABLES registration that keeps
the audit trigger attached.  These are the raw-SQL-bypass backstop for the
schema-tier validation in ``YtdTaxCheckpointSchema``.
"""

from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy.exc import IntegrityError

from app.audit_infrastructure import AUDITED_TABLES
from app.extensions import db as _db
from app.models.ref import FilingStatus
from app.models.salary_profile import SalaryProfile
from app.models.ytd_tax_checkpoint import YtdTaxCheckpoint
from tests._test_helpers import start_test_pay_list


def _make_profile(seed_user, name="Checkpoint Model Profile"):
    """Build and flush an active single/NC SalaryProfile for the seeded user."""
    filing_status = (
        _db.session.query(FilingStatus).filter_by(name="single").one()
    )
    profile = SalaryProfile(
        period_end_days_before_payday=6,
        user_id=seed_user["user"].id,
        scenario_id=seed_user["scenario"].id,
        name=name,
        filing_status_id=filing_status.id,
        state_code="NC",
        is_active=True,
    )
    _db.session.add(profile)
    _db.session.flush()
    start_test_pay_list(profile, Decimal("4615.38"))  # $120,000.00 a year / 26
    return profile


def _valid_kwargs(profile, **overrides):
    """Return a valid YtdTaxCheckpoint kwargs dict, overridable per test.

    Figures chosen so every withholding line is well under gross: gross
    50,000; federal 5,000; state 2,000; SS 3,100; Medicare 725.
    """
    kwargs = {
        "salary_profile_id": profile.id,
        "as_of_date": date(2026, 6, 30),
        "ytd_gross": Decimal("50000.00"),
        "ytd_federal": Decimal("5000.00"),
        "ytd_state": Decimal("2000.00"),
        "ytd_social_security": Decimal("3100.00"),
        "ytd_medicare": Decimal("725.00"),
    }
    kwargs.update(overrides)
    return kwargs


class TestValidCheckpoint:
    """A well-formed checkpoint persists and reads back its figures."""

    def test_insert_round_trip(self, app, db, seed_user):
        """A valid checkpoint commits and reloads with exact Decimals."""
        profile = _make_profile(seed_user)
        cp = YtdTaxCheckpoint(**_valid_kwargs(profile))
        db.session.add(cp)
        db.session.commit()

        reloaded = db.session.get(YtdTaxCheckpoint, cp.id)
        assert reloaded.ytd_gross == Decimal("50000.00")
        assert reloaded.ytd_federal == Decimal("5000.00")
        assert reloaded.ytd_medicare == Decimal("725.00")
        assert reloaded.as_of_date == date(2026, 6, 30)
        # created_at / updated_at populated by TimestampMixin defaults.
        assert reloaded.created_at is not None
        assert reloaded.updated_at is not None

    def test_zero_gross_allowed(self, app, db, seed_user):
        """ytd_gross == 0 is admitted (>= 0, not > 0) with zero components."""
        profile = _make_profile(seed_user)
        cp = YtdTaxCheckpoint(**_valid_kwargs(
            profile,
            ytd_gross=Decimal("0.00"),
            ytd_federal=Decimal("0.00"),
            ytd_state=Decimal("0.00"),
            ytd_social_security=Decimal("0.00"),
            ytd_medicare=Decimal("0.00"),
        ))
        db.session.add(cp)
        db.session.commit()
        assert db.session.get(YtdTaxCheckpoint, cp.id).ytd_gross == Decimal("0.00")


class TestNonNegativeChecks:
    """Each ck_ytd_tax_checkpoints_nonneg_* rejects a negative figure."""

    @pytest.mark.parametrize("field", [
        "ytd_gross",
        "ytd_federal",
        "ytd_state",
        "ytd_social_security",
        "ytd_medicare",
    ])
    def test_negative_rejected(self, app, db, seed_user, field):
        """A negative value on any of the five money columns is rejected."""
        profile = _make_profile(seed_user)
        cp = YtdTaxCheckpoint(**_valid_kwargs(profile, **{field: Decimal("-1.00")}))
        db.session.add(cp)
        with pytest.raises(IntegrityError):
            db.session.commit()
        db.session.rollback()


class TestComponentLeGrossChecks:
    """Each *_le_gross CHECK rejects a withholding line larger than gross."""

    @pytest.mark.parametrize("field", [
        "ytd_federal",
        "ytd_state",
        "ytd_social_security",
        "ytd_medicare",
    ])
    def test_component_exceeding_gross_rejected(self, app, db, seed_user, field):
        """A withholding line above ytd_gross violates the plausibility CHECK."""
        profile = _make_profile(seed_user)
        # gross 1,000; every component starts at 100, then the one under
        # test is bumped to 1,000.01 (> gross) via the overrides dict.
        overrides = {
            "ytd_gross": Decimal("1000.00"),
            "ytd_federal": Decimal("100.00"),
            "ytd_state": Decimal("100.00"),
            "ytd_social_security": Decimal("100.00"),
            "ytd_medicare": Decimal("100.00"),
        }
        overrides[field] = Decimal("1000.01")
        cp = YtdTaxCheckpoint(**_valid_kwargs(profile, **overrides))
        db.session.add(cp)
        with pytest.raises(IntegrityError):
            db.session.commit()
        db.session.rollback()

    def test_component_equal_to_gross_allowed(self, app, db, seed_user):
        """A withholding line exactly equal to gross is admitted (<=, not <)."""
        profile = _make_profile(seed_user)
        cp = YtdTaxCheckpoint(**_valid_kwargs(
            profile,
            ytd_gross=Decimal("1000.00"),
            ytd_federal=Decimal("1000.00"),
            ytd_state=Decimal("0.00"),
            ytd_social_security=Decimal("0.00"),
            ytd_medicare=Decimal("0.00"),
        ))
        db.session.add(cp)
        db.session.commit()
        assert db.session.get(YtdTaxCheckpoint, cp.id).ytd_federal == Decimal("1000.00")


class TestUniqueConstraint:
    """uq_ytd_tax_checkpoints_profile_date forbids two rows on one date."""

    def test_duplicate_profile_date_rejected(self, app, db, seed_user):
        """A second checkpoint for the same (profile, as_of_date) is rejected."""
        profile = _make_profile(seed_user)
        db.session.add(YtdTaxCheckpoint(**_valid_kwargs(profile)))
        db.session.flush()
        db.session.add(YtdTaxCheckpoint(**_valid_kwargs(
            profile, ytd_gross=Decimal("60000.00"),
        )))
        with pytest.raises(IntegrityError):
            db.session.commit()
        db.session.rollback()

    def test_same_date_different_profile_allowed(self, app, db, seed_user):
        """Two profiles may each hold a checkpoint on the same date."""
        profile_a = _make_profile(seed_user, name="Profile A")
        profile_b = _make_profile(seed_user, name="Profile B")
        db.session.add(YtdTaxCheckpoint(**_valid_kwargs(profile_a)))
        db.session.add(YtdTaxCheckpoint(**_valid_kwargs(profile_b)))
        db.session.commit()
        rows = (
            db.session.query(YtdTaxCheckpoint)
            .filter(YtdTaxCheckpoint.as_of_date == date(2026, 6, 30))
            .all()
        )
        assert len(rows) == 2

    def test_different_date_same_profile_allowed(self, app, db, seed_user):
        """History-keeping: one profile may hold many dated checkpoints."""
        profile = _make_profile(seed_user)
        db.session.add(YtdTaxCheckpoint(**_valid_kwargs(
            profile, as_of_date=date(2026, 3, 31),
        )))
        db.session.add(YtdTaxCheckpoint(**_valid_kwargs(
            profile, as_of_date=date(2026, 6, 30),
        )))
        db.session.commit()
        count = (
            db.session.query(YtdTaxCheckpoint)
            .filter(YtdTaxCheckpoint.salary_profile_id == profile.id)
            .count()
        )
        assert count == 2


class TestCascadeDelete:
    """Deleting a profile cascades to its checkpoints (ondelete=CASCADE)."""

    def test_profile_delete_cascades(self, app, db, seed_user):
        """A checkpoint is removed when its owning profile is deleted."""
        profile = _make_profile(seed_user)
        cp = YtdTaxCheckpoint(**_valid_kwargs(profile))
        db.session.add(cp)
        db.session.commit()
        cp_id = cp.id

        db.session.delete(profile)
        db.session.commit()
        assert db.session.get(YtdTaxCheckpoint, cp_id) is None


class TestAuditRegistration:
    """The table is audited so its trigger is attached and counted."""

    def test_table_registered(self):
        """Static check: the salary table is in AUDITED_TABLES."""
        assert ("salary", "ytd_tax_checkpoints") in AUDITED_TABLES

    def test_audit_trigger_attached_in_db(self, db):
        """Live check: the named audit trigger exists on the table."""
        count = _db.session.execute(_db.text(
            "SELECT count(*) FROM pg_trigger t "
            "JOIN pg_class c ON c.oid = t.tgrelid "
            "JOIN pg_namespace n ON n.oid = c.relnamespace "
            "WHERE n.nspname = 'salary' "
            "  AND c.relname = 'ytd_tax_checkpoints' "
            "  AND t.tgname = 'audit_ytd_tax_checkpoints'"
        )).scalar()
        assert count == 1
