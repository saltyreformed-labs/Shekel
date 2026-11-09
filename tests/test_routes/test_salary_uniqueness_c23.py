"""
Shekel Budget App -- C-23 Salary Raise / Deduction Uniqueness Tests

Verifies the composite unique constraints introduced in commit C-23
of the 2026-04-15 security remediation plan:

  - ``uq_salary_raises_profile_type_date`` on
    ``salary.salary_raises (salary_profile_id, raise_type_id,
    effective_date)`` (F-051), keyed on the year and month with
    ``NULLS NOT DISTINCT`` semantics until migrations ``c9f1a7b3d2e8``
    (DH-#57) and ``6740ce13e174`` (plan#3048, the start became a date).
  - ``uq_paycheck_lines_profile_name`` on
    ``salary.paycheck_lines (salary_profile_id, name)`` (F-052).

Coverage:
  1. Database-level enforcement (raw INSERT raises IntegrityError on
     the named constraint).
  2. Route-level idempotency (double-submit on add returns one row
     and a friendly flash, not a 500).
  3. Route-level update collision (renaming or re-keying to an
     existing row redirects with a warning, leaves DB unchanged).
  4. Permitted variations (different year, different month,
     different type, different name -- distinct rows allowed).
"""

from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy.exc import IntegrityError

from app.extensions import db
from app.models.category import Category
from app.models.paycheck_line import PaycheckLine
from app.models.recurrence_rule import RecurrenceRule
from app.models.ref import (
    CalcMethod, PaycheckLineKind, FilingStatus,
    RaiseType, TransactionType,
)
from app.models.salary_profile import SalaryProfile
from app.models.salary_raise import SalaryRaise
from app.models.transaction_template import TransactionTemplate
from app.utils.db_errors import is_unique_violation

from tests._test_helpers import (
    freeze_today, make_every_period_rule, start_test_pay_list,
)


# ── Fixtures and helpers ────────────────────────────────────────


SALARY_RAISES_UNIQUE = "uq_salary_raises_profile_type_date"
PAYCHECK_LINES_UNIQUE = "uq_paycheck_lines_profile_name"


@pytest.fixture(autouse=True)
def _freeze_today_inside_seed_range(monkeypatch):
    """Freeze today inside the seeded period range.

    Mirrors the fixture in ``test_salary/conftest.py``.  Required because
    these tests exercise routes that call ``_regenerate_salary_transactions``,
    which in turn asks which paycheck contains today; that lookup
    needs to find a seeded period at "today" or it
    skips regeneration silently and the tests cannot tell whether
    the constraint or the regeneration path failed.
    """
    freeze_today(monkeypatch, date(2026, 3, 20))


def _create_profile(seed_user):
    """Create a salary profile with linked template and recurrence."""
    filing_status = db.session.query(FilingStatus).filter_by(name="single").one()
    income_type = db.session.query(TransactionType).filter_by(name="Income").one()

    cat = (
        db.session.query(Category)
        .filter_by(user_id=seed_user["user"].id, group_name="Income", item_name="Salary")
        .first()
    )
    if cat is None:
        cat = Category(
            user_id=seed_user["user"].id,
            group_name="Income",
            item_name="Salary",
        )
        db.session.add(cat)
        db.session.flush()

    template = TransactionTemplate(
        user_id=seed_user["user"].id,
        account_id=seed_user["account"].id,
        category_id=cat.id,
        transaction_type_id=income_type.id,
        name="Day Job",
        is_active=True,
    )
    db.session.add(template)
    db.session.flush()
    # The definition first, then the cadence onto it (plan step R-F6).
    rule = make_every_period_rule(db.session, template)

    profile = SalaryProfile(
        user_id=seed_user["user"].id,
        scenario_id=seed_user["scenario"].id,
        template_id=template.id,
        filing_status_id=filing_status.id,
        name="Day Job",
        state_code="NC",
    )
    db.session.add(profile)
    start_test_pay_list(profile, Decimal("2884.62"))  # $75,000.00 a year / 26
    db.session.commit()
    return profile


def _make_raise(profile, raise_type, *, starts, percentage="0.03",
                is_recurring=False, flat_amount=None):
    """Insert a SalaryRaise directly (bypasses the route layer).

    Used to seed pre-existing rows for collision tests at the model
    layer.  Returns the persisted object so callers can read its id.
    """
    kwargs = dict(
        salary_profile_id=profile.id,
        raise_type_id=raise_type.id,
        effective_date=starts,
        is_recurring=is_recurring,
    )
    if flat_amount is not None:
        kwargs["flat_amount"] = Decimal(flat_amount)
    else:
        kwargs["percentage"] = Decimal(percentage)
    obj = SalaryRaise(**kwargs)
    db.session.add(obj)
    db.session.commit()
    return obj


def _make_deduction(profile, name="401k", amount="200.00",
                    timing_name="pre_tax_deduction", method_name="flat"):
    """Insert a PaycheckLine directly (bypasses the route layer)."""
    timing = db.session.query(PaycheckLineKind).filter_by(name=timing_name).one()
    method = db.session.query(CalcMethod).filter_by(name=method_name).one()
    obj = PaycheckLine(
        salary_profile_id=profile.id,
        paycheck_line_kind_id=timing.id,
        calc_method_id=method.id,
        name=name,
        amount=Decimal(amount),
    )
    db.session.add(obj)
    db.session.commit()
    return obj


# ── Database-level enforcement ──────────────────────────────────


class TestSalaryRaiseUniqueConstraint:
    """Direct INSERT collisions surface IntegrityError on the C-23 constraint."""

    def test_duplicate_non_recurring_raise_rejected(self, app, seed_user, seed_periods):
        """Two non-recurring raises sharing (profile, type, start date) collide."""
        with app.app_context():
            profile = _create_profile(seed_user)
            merit = db.session.query(RaiseType).filter_by(name="merit").one()
            _make_raise(profile, merit, starts=date(2026, 7, 1))

            duplicate = SalaryRaise(
                salary_profile_id=profile.id,
                raise_type_id=merit.id,
                effective_date=date(2026, 7, 1),
                percentage=Decimal("0.05"),
                is_recurring=False,
            )
            db.session.add(duplicate)
            with pytest.raises(IntegrityError) as excinfo:
                db.session.commit()
            db.session.rollback()
            assert is_unique_violation(
                excinfo.value, SALARY_RAISES_UNIQUE,
            ), (
                "Expected IntegrityError on the C-23 constraint; got "
                f"constraint={getattr(getattr(excinfo.value.orig, 'diag', None), 'constraint_name', None)!r}"
            )

    def test_distinct_date_or_type_allowed(
        self, app, seed_user, seed_periods,
    ):
        """A different year, month, day or type creates distinct raises.

        The day is new since migration ``6740ce13e174`` keyed the guard on the
        start date: two raises of one type on two days of one month are two
        stated facts, where the month key refused the second.
        """
        with app.app_context():
            profile = _create_profile(seed_user)
            merit = db.session.query(RaiseType).filter_by(name="merit").one()
            cola = db.session.query(RaiseType).filter_by(name="cola").one()
            _make_raise(profile, merit, starts=date(2026, 7, 1))
            # Different year.
            _make_raise(profile, merit, starts=date(2027, 7, 1))
            # Different month.
            _make_raise(profile, merit, starts=date(2026, 8, 1))
            # Different day of the same month.
            _make_raise(profile, merit, starts=date(2026, 7, 15))
            # Different type.
            _make_raise(profile, cola, starts=date(2026, 7, 1),
                        flat_amount="500.00")
            count = (
                db.session.query(SalaryRaise)
                .filter_by(salary_profile_id=profile.id)
                .count()
            )
            assert count == 5

    def test_same_shape_on_different_profile_allowed(
        self, app, seed_user, seed_periods,
    ):
        """The constraint scopes to a single salary profile, not user-wide."""
        with app.app_context():
            profile_a = _create_profile(seed_user)
            # Build a second profile under the same user; the
            # ``_create_profile`` helper insists on a unique
            # template, so we duplicate the bare minimum here.
            filing = (
                db.session.query(FilingStatus).filter_by(name="single").one()
            )
            profile_b = SalaryProfile(
                user_id=seed_user["user"].id,
                scenario_id=seed_user["scenario"].id,
                template_id=None,
                filing_status_id=filing.id,
                name="Side Gig",
                state_code="NC",
            )
            db.session.add(profile_b)
            start_test_pay_list(profile_b, Decimal("769.23"))  # $20,000.00 a year / 26
            db.session.commit()

            merit = db.session.query(RaiseType).filter_by(name="merit").one()
            _make_raise(profile_a, merit, starts=date(2026, 7, 1))
            _make_raise(profile_b, merit, starts=date(2026, 7, 1))
            count = (
                db.session.query(SalaryRaise)
                .filter(SalaryRaise.salary_profile_id.in_(
                    [profile_a.id, profile_b.id],
                ))
                .count()
            )
            assert count == 2


class TestPaycheckLineUniqueConstraint:
    """Direct INSERT collisions surface IntegrityError on the C-23 constraint."""

    def test_duplicate_name_on_same_profile_rejected(
        self, app, seed_user, seed_periods,
    ):
        """Two deductions sharing (profile, name) collide."""
        with app.app_context():
            profile = _create_profile(seed_user)
            _make_deduction(profile, name="401k", amount="200.00")

            timing = (
                db.session.query(PaycheckLineKind).filter_by(name="pre_tax_deduction").one()
            )
            method = db.session.query(CalcMethod).filter_by(name="flat").one()
            duplicate = PaycheckLine(
                salary_profile_id=profile.id,
                paycheck_line_kind_id=timing.id,
                calc_method_id=method.id,
                name="401k",
                amount=Decimal("250.00"),
            )
            db.session.add(duplicate)
            with pytest.raises(IntegrityError) as excinfo:
                db.session.commit()
            db.session.rollback()
            assert is_unique_violation(
                excinfo.value, PAYCHECK_LINES_UNIQUE,
            )

    def test_duplicate_blocked_even_when_first_is_inactive(
        self, app, seed_user, seed_periods,
    ):
        """is_active toggle does not exempt rows from the unique key.

        The audit cited this as a deliberate tradeoff: a deactivated
        deduction must be reactivated rather than re-created, so
        the unique scope is the full table on the salary profile,
        not "active rows only."
        """
        with app.app_context():
            profile = _create_profile(seed_user)
            existing = _make_deduction(profile, name="Health Insurance")
            existing.is_active = False
            db.session.commit()

            timing = (
                db.session.query(PaycheckLineKind).filter_by(name="pre_tax_deduction").one()
            )
            method = db.session.query(CalcMethod).filter_by(name="flat").one()
            duplicate = PaycheckLine(
                salary_profile_id=profile.id,
                paycheck_line_kind_id=timing.id,
                calc_method_id=method.id,
                name="Health Insurance",
                amount=Decimal("199.00"),
            )
            db.session.add(duplicate)
            with pytest.raises(IntegrityError) as excinfo:
                db.session.commit()
            db.session.rollback()
            assert is_unique_violation(
                excinfo.value, PAYCHECK_LINES_UNIQUE,
            )

    def test_distinct_name_or_profile_allowed(
        self, app, seed_user, seed_periods,
    ):
        """Different name or profile creates distinct deductions."""
        with app.app_context():
            profile = _create_profile(seed_user)
            _make_deduction(profile, name="401k", amount="200.00")
            _make_deduction(profile, name="Roth IRA", amount="150.00")
            count = (
                db.session.query(PaycheckLine)
                .filter_by(salary_profile_id=profile.id)
                .count()
            )
            assert count == 2


# ── Route-level enforcement ─────────────────────────────────────


class TestAddRaiseRoute:
    """POST /salary/<id>/raises double-submit handling."""

    def test_double_submit_creates_one_raise(
        self, app, auth_client, seed_user, seed_periods,
    ):
        """Duplicate POST returns idempotent success and one row."""
        with app.app_context():
            profile = _create_profile(seed_user)
            merit = db.session.query(RaiseType).filter_by(name="merit").one()
            data = {
                "raise_type_id": merit.id,
                "effective_date": "2026-07-01",
                "percentage": "3",
            }
            r1 = auth_client.post(
                f"/salary/{profile.id}/raises", data=data,
                follow_redirects=True,
            )
            assert r1.status_code == 200
            assert b"Raise added." in r1.data

            r2 = auth_client.post(
                f"/salary/{profile.id}/raises", data=data,
                follow_redirects=True,
            )
            assert r2.status_code == 200
            assert b"already exists" in r2.data
            # The "Raise added." flash from the second submit is
            # absent: we hit the IntegrityError branch.
            assert r2.data.count(b"Raise added.") == 0

            db.session.expire_all()
            count = (
                db.session.query(SalaryRaise)
                .filter_by(salary_profile_id=profile.id)
                .count()
            )
            assert count == 1

    def test_distinct_year_creates_two_rows(
        self, app, auth_client, seed_user, seed_periods,
    ):
        """A different start year is a different raise -- both succeed."""
        with app.app_context():
            profile = _create_profile(seed_user)
            merit = db.session.query(RaiseType).filter_by(name="merit").one()
            base = {
                "raise_type_id": merit.id,
                "percentage": "3",
            }
            r1 = auth_client.post(
                f"/salary/{profile.id}/raises",
                data={**base, "effective_date": "2026-07-01"},
                follow_redirects=True,
            )
            r2 = auth_client.post(
                f"/salary/{profile.id}/raises",
                data={**base, "effective_date": "2027-07-01"},
                follow_redirects=True,
            )
            assert r1.status_code == 200
            assert r2.status_code == 200
            assert b"Raise added." in r1.data
            assert b"Raise added." in r2.data
            db.session.expire_all()
            count = (
                db.session.query(SalaryRaise)
                .filter_by(salary_profile_id=profile.id)
                .count()
            )
            assert count == 2


class TestUpdateRaiseRoute:
    """POST /salary/raises/<id>/edit collision handling."""

    def test_rename_to_existing_key_returns_warning(
        self, app, auth_client, seed_user, seed_periods,
    ):
        """Editing a raise to collide with a sibling redirects with a warning."""
        with app.app_context():
            profile = _create_profile(seed_user)
            merit = db.session.query(RaiseType).filter_by(name="merit").one()
            existing = _make_raise(profile, merit, starts=date(2026, 7, 1))
            target = _make_raise(profile, merit, starts=date(2027, 8, 1))
            target_id = target.id
            target_version = target.version_id

            # Try to edit ``target`` to overlap ``existing``'s key.
            resp = auth_client.post(
                f"/salary/raises/{target_id}/edit",
                data={
                    "raise_type_id": str(merit.id),
                    "effective_date": "2026-07-01",
                    "percentage": "3",
                    "version_id": str(target_version),
                },
                follow_redirects=True,
            )
            assert resp.status_code == 200
            assert b"already covers" in resp.data

            # Target row's effective date must be unchanged.
            db.session.expire_all()
            refreshed = db.session.get(SalaryRaise, target_id)
            assert refreshed.effective_date == date(2027, 8, 1)
            # And ``existing`` must still exist (not overwritten).
            assert (
                db.session.query(SalaryRaise)
                .filter_by(id=existing.id)
                .one()
                .effective_date == date(2026, 7, 1)
            )


class TestAddDeductionRoute:
    """POST /salary/<id>/lines double-submit handling."""

    def test_double_submit_creates_one_deduction(
        self, app, auth_client, seed_user, seed_periods,
    ):
        """Duplicate POST returns idempotent success and one row."""
        with app.app_context():
            profile = _create_profile(seed_user)
            timing = (
                db.session.query(PaycheckLineKind).filter_by(name="pre_tax_deduction").one()
            )
            method = db.session.query(CalcMethod).filter_by(name="flat").one()
            data = {
                "name": "401k",
                "paycheck_line_kind_id": timing.id,
                "calc_method_id": method.id,
                "amount": "250.00",
            }
            r1 = auth_client.post(
                f"/salary/{profile.id}/lines", data=data,
                follow_redirects=True,
            )
            assert r1.status_code == 200
            assert b"401k" in r1.data
            assert b"added" in r1.data

            r2 = auth_client.post(
                f"/salary/{profile.id}/lines", data=data,
                follow_redirects=True,
            )
            assert r2.status_code == 200
            assert b"already exists" in r2.data

            db.session.expire_all()
            count = (
                db.session.query(PaycheckLine)
                .filter_by(salary_profile_id=profile.id, name="401k")
                .count()
            )
            assert count == 1

    def test_distinct_name_creates_two_rows(
        self, app, auth_client, seed_user, seed_periods,
    ):
        """Different name is a different deduction -- both succeed."""
        with app.app_context():
            profile = _create_profile(seed_user)
            timing = (
                db.session.query(PaycheckLineKind).filter_by(name="pre_tax_deduction").one()
            )
            method = db.session.query(CalcMethod).filter_by(name="flat").one()
            base = {
                "paycheck_line_kind_id": timing.id,
                "calc_method_id": method.id,
                "amount": "200.00",
            }
            r1 = auth_client.post(
                f"/salary/{profile.id}/lines",
                data={**base, "name": "401k"},
                follow_redirects=True,
            )
            r2 = auth_client.post(
                f"/salary/{profile.id}/lines",
                data={**base, "name": "HSA"},
                follow_redirects=True,
            )
            assert r1.status_code == 200
            assert r2.status_code == 200
            db.session.expire_all()
            count = (
                db.session.query(PaycheckLine)
                .filter_by(salary_profile_id=profile.id)
                .count()
            )
            assert count == 2


class TestUpdateDeductionRoute:
    """POST /salary/lines/<id>/edit collision handling."""

    def test_rename_to_existing_name_returns_warning(
        self, app, auth_client, seed_user, seed_periods,
    ):
        """Editing a deduction's name to collide with a sibling redirects with a warning."""
        with app.app_context():
            profile = _create_profile(seed_user)
            timing = (
                db.session.query(PaycheckLineKind).filter_by(name="pre_tax_deduction").one()
            )
            method = db.session.query(CalcMethod).filter_by(name="flat").one()
            existing = _make_deduction(profile, name="401k")
            target = _make_deduction(profile, name="HSA")
            target_id = target.id
            target_version = target.version_id

            resp = auth_client.post(
                f"/salary/lines/{target_id}/edit",
                data={
                    "name": "401k",
                    "paycheck_line_kind_id": str(timing.id),
                    "calc_method_id": str(method.id),
                    "amount": "200.00",
                    "version_id": str(target_version),
                },
                follow_redirects=True,
            )
            assert resp.status_code == 200
            assert b"already uses that" in resp.data

            db.session.expire_all()
            refreshed = db.session.get(PaycheckLine, target_id)
            assert refreshed.name == "HSA"
            # ``existing`` is untouched.
            assert (
                db.session.query(PaycheckLine)
                .filter_by(id=existing.id)
                .one()
                .name == "401k"
            )
