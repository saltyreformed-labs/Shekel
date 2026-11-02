"""
Tests for retirement planning routes.
"""

import logging
import re
from pathlib import Path
from datetime import date
from decimal import Decimal

from app import ref_cache
from app.enums import EmployerContributionTypeEnum, RaiseTypeEnum
from app.extensions import db
from app.models.account import Account
from app.models.category import Category
from app.models.pension_profile import PensionProfile
from app.models.recurrence_rule import RecurrenceRule
from app.models.salary_profile import SalaryProfile
from app.models.salary_raise import SalaryRaise
from app.utils.error_fragments import DESIGNED_FRAGMENT_HEADER, RETARGET_HEADER
from app.models.transaction_template import TransactionTemplate
from app.models.user import UserSettings
from app.models.investment_params import InvestmentParams
from app.models.paycheck_line import PaycheckLine
from app.services import account_service
from app.services.balance_at import BalanceContext
from app.models.ref import (
    AccountType, CalcMethod, PaycheckLineKind, FilingStatus,
    TransactionType,
)
from app.utils.dates import display_today
from tests._test_helpers import (
    freeze_today, make_every_period_rule, make_recurring_raise, start_test_pay_list,
)
from tests.conftest import log_in_seed_user


#: One recurring-raise row of the assumptions rail: ``(raise id, inner markup)``.
#: A ``<form>`` since plan step salary:S3-f-3 gave each row a Save; matched on
#: its two data attributes rather than on attribute order.
_RAISE_ROW_MARKUP = (
    r'<form[^>]*data-assumption="raise_terminal_year"[^>]*'
    r'data-raise-id="(\d+)"[^>]*>(.*?)</form>'
)


def _create_salary_profile(seed_user, db_session, name="Main Job"):
    """Helper to create a salary profile with all required relations.

    *name* names both the profile and its template; a second profile for the
    same owner needs its own (``uq_salary_profiles_user_scenario_name``).
    """
    filing_status = db_session.query(FilingStatus).filter_by(name="single").one()
    income_type = db_session.query(TransactionType).filter_by(name="Income").one()

    cat = db_session.query(Category).filter_by(
        user_id=seed_user["user"].id, item_name="Salary"
    ).first()
    if not cat:
        cat = Category(
            user_id=seed_user["user"].id,
            group_name="Income", item_name="Salary",
        )
        db_session.add(cat)
        db_session.flush()

    template = TransactionTemplate(
        user_id=seed_user["user"].id,
        account_id=seed_user["account"].id,
        category_id=cat.id,
        transaction_type_id=income_type.id,
        name=name,
        default_amount=Decimal("80000.00") / 26,
        is_active=True,
    )
    db_session.add(template)
    db_session.flush()
    # The definition first, then the cadence onto it (plan step R-F6).
    rule = make_every_period_rule(db_session, template)

    profile = SalaryProfile(
        user_id=seed_user["user"].id,
        scenario_id=seed_user["scenario"].id,
        template_id=template.id,
        filing_status_id=filing_status.id,
        name=name,
    )
    db_session.add(profile)
    start_test_pay_list(profile, Decimal("3076.92"))  # $80,000.00 a year / 26
    db_session.flush()
    return profile


def _create_pension(seed_user, db_session, salary_profile=None, name="State Pension"):
    """Helper to create a pension profile."""
    pension = PensionProfile(
        user_id=seed_user["user"].id,
        salary_profile_id=salary_profile.id if salary_profile else None,
        name=name,
        benefit_multiplier=Decimal("0.01850"),
        consecutive_high_years=4,
        hire_date=date(2018, 7, 1),
        planned_retirement_date=date(2048, 7, 1),
    )
    db_session.add(pension)
    db_session.commit()
    return pension




def _create_retirement_account(seed_user, db_session, type_name="401(k)"):
    """Helper to create a retirement account with investment params.

    COMMITS rather than flushes (plan step balance:X-i3): most callers go on to
    issue a request, and a request holds a transaction of its own in which an
    uncommitted account and its params do not exist.
    """
    acct_type = db_session.query(AccountType).filter_by(name=type_name).one()
    account = account_service.create_account(
        account_service.AccountSpec(
            user_id=seed_user["user"].id,
            account_type_id=acct_type.id,
            name=f"Test {type_name}",
            anchor_balance=Decimal("10000.00"),
        ),
    )
    db_session.add(account)
    db_session.flush()

    params = InvestmentParams(
        account_id=account.id,
        assumed_annual_return=Decimal("0.07000"),
        annual_contribution_limit=Decimal("23500.00"),
        employer_contribution_type_id=ref_cache.employer_contribution_type_id(
            EmployerContributionTypeEnum.MATCH,
        ),
        employer_match_percentage=Decimal("1.0000"),
        employer_match_cap_percentage=Decimal("0.0600"),
    )
    db_session.add(params)
    db_session.commit()
    return account, params


def _seed_overfunded(seed_user, db_session):
    """Salary + settings retirement date + a $5M 401(k), no pension.

    Deterministically ALREADY FUNDED: $5,000,000 at 7% projects to well
    over $30M by +240 months against an ~$1.4M requirement (net biweekly
    ~ $2,200 * 26 / 0.04), so both levers report already_funded at their
    baselines.  Shared by the acceptance-fix-3 override-rendering tests.
    """
    from app.utils.dates import add_months

    _create_salary_profile(seed_user, db_session)
    settings = (
        db_session.query(UserSettings)
        .filter_by(user_id=seed_user["user"].id)
        .one()
    )
    settings.planned_retirement_date = add_months(date.today(), 240)
    acct_type = db_session.query(AccountType).filter_by(name="401(k)").one()
    account = account_service.create_account(
        account_service.AccountSpec(
            user_id=seed_user["user"].id,
            account_type_id=acct_type.id,
            name="Big 401k",
            anchor_balance=Decimal("5000000.00"),
        ),
    )
    db_session.flush()
    db_session.add(InvestmentParams(
        account_id=account.id,
        assumed_annual_return=Decimal("0.07000"),
        employer_contribution_type_id=ref_cache.employer_contribution_type_id(
            EmployerContributionTypeEnum.NONE,
        ),
    ))
    db_session.commit()


class TestRetirementDashboard:
    """Tests for the retirement dashboard page."""

    def test_dashboard_empty(self, auth_client, seed_user, db, seed_periods_today):
        """GET returns 200 with the direction-D empty states, no pension footer.

        Realigned for the locked direction-D rebuild (P3b): with nothing
        configured there is no retirement date, so the readiness / levers /
        income cards each render the "set a planned retirement date" empty
        state (the old always-rendered gap table -- audit Surface 7's dead
        else branch -- is retired), and no per-pension derivation line
        exists.
        """
        resp = auth_client.get("/retirement")
        assert resp.status_code == 200
        html = resp.data.decode()
        assert "Retirement readiness" in html
        assert "Set a planned retirement date" in html
        # The retired old-page surfaces must not resurface.
        assert "Retirement Income Gap Analysis" not in html
        assert "retire-pension-line" not in html

    def test_dashboard_with_pension(self, auth_client, seed_user, db, seed_periods_today):
        """The per-pension derivation footer renders the real derivation.

        Realigned for direction D (P3b): the 'Pension Benefit Details'
        card retired into the one-line-per-pension footer (audit D6).
        Hand arithmetic for the seeded pension (hire 2018-07-01, planned
        2048-07-01, multiplier 1.85%):
          service days = 30 years incl. 8 leap days (2020..2048)
                       = 30 * 365 + 8 = 10,958
          years_of_service = (10958 / 365.25).quantize(0.01) = 30.00
        so the footer line reads "State Pension: 30.00 yrs x" and the
        multiplier renders as "1.85%".
        """
        profile = _create_salary_profile(seed_user, db.session)
        _create_pension(seed_user, db.session, salary_profile=profile)
        resp = auth_client.get("/retirement")
        assert resp.status_code == 200
        html = resp.data.decode()
        assert "retire-pension-line" in html
        assert "State Pension: 30.00 yrs x" in html
        assert "1.85%" in html
        assert "Pension Benefit Details" not in html

    def test_past_planned_date_renders_honest_lever_state(
        self, auth_client, seed_user, db, seed_periods_today,
    ):
        """A stored past date cannot make the lever claim "already funded".

        M1: the settings schema now rejects NEW past dates, but stored
        data ages (and a pension-sourced date bypasses that schema), so
        the page must stay honest: with a shortfall and zero remaining
        paychecks the contribution lever renders the past_horizon line,
        never the funded one, and the stepper input survives the None
        solved amount.
        """
        from datetime import timedelta

        _create_salary_profile(seed_user, db.session)
        _create_retirement_account(seed_user, db.session)
        settings = db.session.query(UserSettings).filter_by(
            user_id=seed_user["user"].id,
        ).one()
        settings.planned_retirement_date = date.today() - timedelta(days=30)
        db.session.commit()

        resp = auth_client.get("/retirement")
        assert resp.status_code == 200
        html = resp.data.decode()
        assert "Your planned retirement date has passed" in html
        assert "Already fully funded" not in html

    def test_assumptions_rail_echoes_non_default_stored_values(
        self, auth_client, seed_user, db, seed_periods_today,
    ):
        """The full page renders the STORED assumption values, not fallbacks.

        P4 live-verify regression: the P3c-slimmed dashboard context
        dropped ``settings``, so the rail's inputs silently rendered
        their empty/fallback states.  A NON-DEFAULT stored value closes
        that hole, because a fallback cannot produce one:
          SWR 0.0350 -> to_percent 3.50 -> "%.2f" -> value="3.50"
        The unset tax rate must also surface its not-set flag -- an
        Undefined ``settings`` suppressed it too.

        *It pinned ``merit_raise_horizon_years 7 -> value="7"`` as a second
        probe until plan step salary:S3-c deleted that setting.  The rail's
        other context-dropping hole is now the recurring-raise rows, which
        read ``salary_profiles`` rather than ``settings`` and are covered
        by ``TestTheRailStatesEachRecurringRaisesEndYear``.*
        """
        settings = db.session.query(UserSettings).filter_by(
            user_id=seed_user["user"].id,
        ).one()
        settings.safe_withdrawal_rate = Decimal("0.0350")
        db.session.commit()

        resp = auth_client.get("/retirement")
        assert resp.status_code == 200
        html = resp.data.decode()
        assert 'value="3.50"' in html
        assert 'data-assumption-flag="tax-missing"' in html

    def test_pension_owned_date_row_is_editable_with_provenance(
        self, auth_client, seed_user, db, seed_periods_today,
    ):
        """The pension-owned date row is editable and names its target.

        Acceptance r2 item 2 (developer ruling, superseding round 1's
        read-only row): the date row stays editable -- Save writes
        through to the owning pension -- with the input echoing the
        RESOLVED (pension) date and the caption naming the pension it
        updates, linked to its edit page.
        """
        profile = _create_salary_profile(seed_user, db.session)
        pension = _create_pension(seed_user, db.session, salary_profile=profile)
        resp = auth_client.get("/retirement")
        assert resp.status_code == 200
        html = resp.data.decode()
        assert 'id="assump-retirement-date"' in html
        # The input echoes the RESOLVED (pension) date.
        assert 'value="2048-07-01"' in html  # _create_pension's date
        assert 'data-assumption-source="pension"' in html
        assert "updates" in html
        assert "State Pension" in html
        assert f"/retirement/pension/{pension.id}/edit" in html

    def test_settings_owned_date_row_keeps_the_editable_input(
        self, auth_client, seed_user, db, seed_periods_today,
    ):
        """Without a pension date the editable input + Save remain."""
        _create_salary_profile(seed_user, db.session)
        settings = db.session.query(UserSettings).filter_by(
            user_id=seed_user["user"].id,
        ).one()
        settings.planned_retirement_date = date(2046, 1, 1)
        db.session.commit()

        resp = auth_client.get("/retirement")
        assert resp.status_code == 200
        html = resp.data.decode()
        assert 'id="assump-retirement-date"' in html
        assert 'value="2046-01-01"' in html
        assert 'data-assumption-source="pension"' not in html

    def test_dashboard_no_stale_settings_migration_message(
        self, auth_client, seed_user, db, seed_periods_today
    ):
        """Dashboard must not contain the old 'settings have moved' notice."""
        resp = auth_client.get("/retirement")
        assert resp.status_code == 200
        assert b"have moved to" not in resp.data

    def test_dashboard_requires_auth(self, client, db):
        """Unauthenticated → redirect to login."""
        resp = client.get("/retirement")
        assert resp.status_code == 302
        assert "/login" in resp.headers["Location"]


class TestPensionCRUD:
    """Tests for pension profile CRUD operations."""

    def test_pension_list(self, auth_client, seed_user, db, seed_periods_today):
        """GET pension list returns 200 with pension form."""
        resp = auth_client.get("/retirement/pension")
        assert resp.status_code == 200
        assert b"Pension Profiles" in resp.data
        assert b'name="benefit_multiplier"' in resp.data

    def test_create_pension(self, auth_client, seed_user, db, seed_periods_today):
        """POST creates a new pension profile."""
        profile = _create_salary_profile(seed_user, db.session)
        resp = auth_client.post("/retirement/pension", data={
            "name": "LGERS",
            "salary_profile_id": str(profile.id),
            "benefit_multiplier": "1.85",
            "consecutive_high_years": "4",
            "hire_date": "2018-07-01",
            "planned_retirement_date": "2048-07-01",
        })
        assert resp.status_code == 302
        pension = db.session.query(PensionProfile).filter_by(
            user_id=seed_user["user"].id
        ).first()
        assert pension is not None
        assert pension.name == "LGERS"
        assert pension.benefit_multiplier == Decimal("0.01850")

    def test_create_pension_percent_normalized_by_schema(
        self, auth_client, seed_user, db, seed_periods_today,
    ):
        """C12-2 (F-17 / Commit 12): pension-create schema's @pre_load
        converts ``benefit_multiplier`` percent input to its fraction
        equivalent.  Arithmetic: 2.5 / 100 = 0.025 (stored at the
        column's ``Numeric(7, 5)`` precision as ``0.02500``).
        """
        profile = _create_salary_profile(seed_user, db.session)
        resp = auth_client.post("/retirement/pension", data={
            "name": "C12-2 Pension",
            "salary_profile_id": str(profile.id),
            "benefit_multiplier": "2.5",
            "consecutive_high_years": "4",
            "hire_date": "2018-07-01",
            "planned_retirement_date": "2048-07-01",
        })
        assert resp.status_code == 302
        pension = db.session.query(PensionProfile).filter_by(
            user_id=seed_user["user"].id, name="C12-2 Pension",
        ).one()
        # Hand-computed: 2.5 / 100 = 0.025.
        assert pension.benefit_multiplier == Decimal("0.02500")

    def test_update_pension_percent_normalized_by_schema(
        self, auth_client, seed_user, db, seed_periods_today,
    ):
        """F-17 / Commit 12: pension-update schema's @pre_load converts
        ``benefit_multiplier`` percent input to its fraction equivalent
        on the update path too.  Arithmetic: 3.5 / 100 = 0.035.
        """
        profile = _create_salary_profile(seed_user, db.session)
        pension = _create_pension(seed_user, db.session, salary_profile=profile)
        resp = auth_client.post(f"/retirement/pension/{pension.id}", data={
            "name": pension.name,
            "salary_profile_id": str(profile.id),
            "benefit_multiplier": "3.5",
            "consecutive_high_years": "4",
            "hire_date": "2018-07-01",
            "planned_retirement_date": "2048-07-01",
        })
        assert resp.status_code == 302
        db.session.refresh(pension)
        # Hand-computed: 3.5 / 100 = 0.035.
        assert pension.benefit_multiplier == Decimal("0.03500")

    def test_edit_pension_form(self, auth_client, seed_user, db, seed_periods_today):
        """GET edit form returns 200 with pre-populated pension data."""
        pension = _create_pension(seed_user, db.session)
        resp = auth_client.get(f"/retirement/pension/{pension.id}/edit")
        assert resp.status_code == 200
        assert b"Edit Pension" in resp.data
        assert b"State Pension" in resp.data
        assert b'name="benefit_multiplier"' in resp.data
        assert b'name="hire_date"' in resp.data

    def test_update_pension(self, auth_client, seed_user, db, seed_periods_today):
        """POST update modifies pension fields."""
        profile = _create_salary_profile(seed_user, db.session)
        pension = _create_pension(seed_user, db.session, salary_profile=profile)
        resp = auth_client.post(f"/retirement/pension/{pension.id}", data={
            "name": "Updated Pension",
            "salary_profile_id": str(profile.id),
            "benefit_multiplier": "2.00",
            "consecutive_high_years": "3",
            "hire_date": "2018-07-01",
            "planned_retirement_date": "2050-01-01",
        })
        assert resp.status_code == 302
        db.session.refresh(pension)
        assert pension.name == "Updated Pension"
        assert pension.benefit_multiplier == Decimal("0.02000")

    def test_update_pension_clears_salary_link(
        self, auth_client, seed_user, db, seed_periods_today,
    ):
        """The form's "-- None --" option (salary_profile_id="") unlinks.

        The deep-hunt follow-up defect: the update schema's pre_load
        used to DROP the empty string, so the setattr loop never saw
        the key and the link could not be cleared from the UI.  The
        empty submit on the allow_none FK now loads as an explicit
        None and the route persists the unlink.
        """
        profile = _create_salary_profile(seed_user, db.session)
        pension = _create_pension(seed_user, db.session, salary_profile=profile)
        assert pension.salary_profile_id == profile.id

        resp = auth_client.post(f"/retirement/pension/{pension.id}", data={
            "salary_profile_id": "",
        })
        assert resp.status_code == 302
        db.session.refresh(pension)
        assert pension.salary_profile_id is None

    def test_create_pension_rejects_cross_user_salary_profile(
        self, auth_client, seed_user, seed_second_user, db, seed_periods_today,
    ):
        """IDOR: a foreign salary_profile_id is rejected 404 and not persisted.

        The pension form lists only the user's own active salary profiles,
        so a salary_profile_id owned by another user is a forged FK that
        must not link the victim's salary/raise history into this user's
        pension projection (security rule: 404 for both not-found and
        not-yours).
        """
        foreign = _create_salary_profile(seed_second_user, db.session)
        resp = auth_client.post("/retirement/pension", data={
            "name": "IDOR Pension",
            "salary_profile_id": str(foreign.id),
            "benefit_multiplier": "1.85",
            "consecutive_high_years": "4",
            "hire_date": "2018-07-01",
            "planned_retirement_date": "2048-07-01",
        })
        assert resp.status_code == 404
        # The forged FK must not have created a pension for the attacker.
        assert db.session.query(PensionProfile).filter_by(
            user_id=seed_user["user"].id
        ).first() is None

    def test_update_pension_rejects_cross_user_salary_profile(
        self, auth_client, seed_user, seed_second_user, db, seed_periods_today,
    ):
        """IDOR: re-linking a pension to a foreign salary_profile_id is rejected.

        The update path must reject a salary_profile_id owned by another
        user with 404 and leave the existing (owned) link untouched.
        """
        own = _create_salary_profile(seed_user, db.session)
        pension = _create_pension(seed_user, db.session, salary_profile=own)
        foreign = _create_salary_profile(seed_second_user, db.session)
        resp = auth_client.post(f"/retirement/pension/{pension.id}", data={
            "name": pension.name,
            "salary_profile_id": str(foreign.id),
            "benefit_multiplier": "1.85",
            "consecutive_high_years": "4",
            "hire_date": "2018-07-01",
            "planned_retirement_date": "2048-07-01",
        })
        assert resp.status_code == 404
        db.session.refresh(pension)
        # The existing owned link is unchanged.
        assert pension.salary_profile_id == own.id

    def test_delete_pension(self, auth_client, seed_user, db, seed_periods_today):
        """POST delete deactivates pension."""
        pension = _create_pension(seed_user, db.session)
        resp = auth_client.post(f"/retirement/pension/{pension.id}/delete")
        assert resp.status_code == 302
        db.session.refresh(pension)
        assert pension.is_active is False

    def test_create_pension_double_submit(
        self, auth_client, seed_user, db, seed_periods_today,
    ):
        """F-105 / C-22: same-name double-submit creates exactly one pension.

        The composite unique ``uq_pension_profiles_user_name`` rejects
        the second INSERT.  The route catches the IntegrityError and
        returns idempotent success: the user lands on the retirement
        dashboard with the pension they intended to create.
        """
        profile = _create_salary_profile(seed_user, db.session)
        data = {
            "name": "DuplicateName",
            "salary_profile_id": str(profile.id),
            "benefit_multiplier": "1.85",
            "consecutive_high_years": "4",
            "hire_date": "2018-07-01",
            "planned_retirement_date": "2048-07-01",
        }
        r1 = auth_client.post("/retirement/pension", data=data)
        assert r1.status_code == 302

        r2 = auth_client.post("/retirement/pension", data=data)
        assert r2.status_code == 302

        db.session.expire_all()
        rows = (
            db.session.query(PensionProfile)
            .filter_by(user_id=seed_user["user"].id, name="DuplicateName")
            .all()
        )
        assert len(rows) == 1, (
            f"Expected 1 pension after double-submit, found {len(rows)}; "
            f"F-105 dedupe failed."
        )

    def test_create_pension_different_names_allowed(
        self, auth_client, seed_user, db, seed_periods_today,
    ):
        """F-105 / C-22: distinct names create distinct pension rows."""
        profile = _create_salary_profile(seed_user, db.session)
        base = {
            "salary_profile_id": str(profile.id),
            "benefit_multiplier": "1.85",
            "consecutive_high_years": "4",
            "hire_date": "2018-07-01",
            "planned_retirement_date": "2048-07-01",
        }
        r1 = auth_client.post(
            "/retirement/pension", data={**base, "name": "Plan A"},
        )
        r2 = auth_client.post(
            "/retirement/pension", data={**base, "name": "Plan B"},
        )
        assert r1.status_code == 302
        assert r2.status_code == 302

        db.session.expire_all()
        count = (
            db.session.query(PensionProfile)
            .filter_by(user_id=seed_user["user"].id)
            .count()
        )
        assert count == 2

    def test_update_pension_collision_returns_422(
        self, auth_client, seed_user, db, seed_periods_today,
    ):
        """F-105 / C-22: renaming pension to an existing name returns 422.

        Constraint enforcement on the update path: the user cannot
        bypass the unique by editing an existing pension to match
        another's name.
        """
        profile = _create_salary_profile(seed_user, db.session)
        first = _create_pension(
            seed_user, db.session,
            salary_profile=profile, name="First Pension",
        )
        second = _create_pension(
            seed_user, db.session,
            salary_profile=profile, name="Second Pension",
        )

        resp = auth_client.post(f"/retirement/pension/{second.id}", data={
            "name": "First Pension",  # Collision target.
            "salary_profile_id": str(profile.id),
            "benefit_multiplier": "1.85",
            "consecutive_high_years": "4",
            "hire_date": "2018-07-01",
            "planned_retirement_date": "2048-07-01",
        })
        assert resp.status_code == 422
        assert b"already have a pension profile with this name" in resp.data

        db.session.expire_all()
        db.session.refresh(second)
        assert second.name == "Second Pension", (
            "Failed update should not have mutated the row."
        )

    def test_edit_pension_idor(
        self, auth_client, second_user, db, seed_periods_today,
    ):
        """GET another user's pension edit form is rejected
        and does not leak victim data."""
        pension = PensionProfile(
            user_id=second_user["user"].id,
            name="Other Pension",
            benefit_multiplier=Decimal("0.01850"),
            consecutive_high_years=4,
            hire_date=date(2020, 1, 1),
        )
        db.session.add(pension)
        db.session.commit()

        resp = auth_client.get(f"/retirement/pension/{pension.id}/edit")
        assert resp.status_code == 404
        assert b"Other Pension" not in resp.data, (
            "IDOR response leaked victim's pension name"
        )

    def test_delete_pension_idor(
        self, auth_client, second_user, db, seed_periods_today,
    ):
        """POST delete on another user's pension is rejected
        and leaves all fields unchanged."""
        pension = PensionProfile(
            user_id=second_user["user"].id,
            name="Other Pension",
            benefit_multiplier=Decimal("0.01850"),
            consecutive_high_years=4,
            hire_date=date(2020, 1, 1),
        )
        db.session.add(pension)
        db.session.commit()

        # Snapshot mutable fields.
        orig_name = pension.name
        orig_multiplier = pension.benefit_multiplier
        orig_active = pension.is_active

        resp = auth_client.post(f"/retirement/pension/{pension.id}/delete")
        assert resp.status_code == 404

        db.session.expire_all()
        after = db.session.get(PensionProfile, pension.id)
        assert after.is_active == orig_active, (
            "IDOR attack deactivated victim's pension!"
        )
        assert after.name == orig_name, (
            "IDOR attack modified victim's pension name!"
        )
        assert after.benefit_multiplier == orig_multiplier, (
            "IDOR attack modified victim's benefit_multiplier!"
        )


class TestRetirementSettings:
    """Tests for retirement settings update."""

    def test_update_settings(self, auth_client, seed_user, db, seed_periods_today):
        """POST updates retirement settings."""
        resp = auth_client.post("/retirement/settings", data={
            "safe_withdrawal_rate": "4",
            "planned_retirement_date": "2055-01-01",
            "estimated_retirement_tax_rate": "20",
        })
        assert resp.status_code == 302
        settings = db.session.query(UserSettings).filter_by(
            user_id=seed_user["user"].id
        ).first()
        assert settings.safe_withdrawal_rate == Decimal("0.0400")
        assert settings.planned_retirement_date == date(2055, 1, 1)
        assert settings.estimated_retirement_tax_rate == Decimal("0.2000")

    def test_update_settings_clears_planned_retirement_date(
        self, auth_client, seed_user, db, seed_periods_today,
    ):
        """An emptied date input clears the stored planned retirement date.

        Same nullable-field clear rule as the pension salary unlink:
        the empty submit on the allow_none date loads as an explicit
        None instead of being dropped, so the settings setattr loop
        nulls the column.
        """
        settings = db.session.query(UserSettings).filter_by(
            user_id=seed_user["user"].id
        ).first()
        settings.planned_retirement_date = date(2055, 1, 1)
        db.session.commit()

        resp = auth_client.post("/retirement/settings", data={
            "planned_retirement_date": "",
        })
        assert resp.status_code == 302
        db.session.refresh(settings)
        assert settings.planned_retirement_date is None

    def test_update_settings_percent_normalized_by_schema(
        self, auth_client, seed_user, db, seed_periods_today,
    ):
        """C12-3 (F-17 / Commit 12): retirement-settings schema's
        @pre_load converts both percent fields to fractions before the
        route persists.  Arithmetic: 4 / 100 = 0.04 (stored as
        ``0.0400``); 22 / 100 = 0.22 (stored as ``0.2200``).
        """
        resp = auth_client.post("/retirement/settings", data={
            "safe_withdrawal_rate": "4",
            "estimated_retirement_tax_rate": "22",
        })
        assert resp.status_code == 302
        settings = db.session.query(UserSettings).filter_by(
            user_id=seed_user["user"].id,
        ).one()
        # Hand-computed: 4 / 100 = 0.04.
        assert settings.safe_withdrawal_rate == Decimal("0.0400")
        # Hand-computed: 22 / 100 = 0.22.
        assert settings.estimated_retirement_tax_rate == Decimal("0.2200")

    def test_update_settings_partial(
        self, auth_client, seed_user, db, seed_periods_today,
    ):
        """POST with only SWR persists the value and leaves
        other retirement fields unchanged."""
        # Snapshot pre-request values.
        settings_before = db.session.query(UserSettings).filter_by(
            user_id=seed_user["user"].id
        ).one()
        orig_retirement_date = settings_before.planned_retirement_date
        orig_tax_rate = settings_before.estimated_retirement_tax_rate

        resp = auth_client.post("/retirement/settings", data={
            "safe_withdrawal_rate": "3.5",
        })
        assert resp.status_code == 302

        # Verify SWR was persisted.
        db.session.expire_all()
        settings_after = db.session.query(UserSettings).filter_by(
            user_id=seed_user["user"].id
        ).one()
        assert settings_after.safe_withdrawal_rate == Decimal("0.0350"), (
            "SWR was not persisted correctly"
        )
        assert settings_after.planned_retirement_date == orig_retirement_date, (
            "Partial update modified planned_retirement_date!"
        )
        assert settings_after.estimated_retirement_tax_rate == orig_tax_rate, (
            "Partial update modified estimated_retirement_tax_rate!"
        )


class TestRetirementProjections:
    """Tests that retirement dashboard projects with full contribution inputs."""

    def test_dashboard_projects_with_contributions(
        self, auth_client, seed_user, db, seed_periods_today
    ):
        """Dashboard projection includes employee contributions and employer match."""
        profile = _create_salary_profile(seed_user, db.session)
        account, params = _create_retirement_account(seed_user, db.session)

        # Set retirement date 20 years out.
        settings = db.session.query(UserSettings).filter_by(
            user_id=seed_user["user"].id
        ).first()
        settings.planned_retirement_date = date(2046, 1, 1)

        # Create a paycheck deduction targeting the retirement account.
        pre_tax = db.session.query(PaycheckLineKind).filter_by(name="pre_tax_deduction").one()
        flat_method = db.session.query(CalcMethod).filter_by(name="flat").one()
        deduction = PaycheckLine(
            salary_profile_id=profile.id,
            target_account_id=account.id,
            name="401k Contribution",
            amount=Decimal("500.00"),
            paycheck_line_kind_id=pre_tax.id,
            calc_method_id=flat_method.id,
        )
        db.session.add(deduction)
        db.session.commit()

        resp = auth_client.get("/retirement")
        assert resp.status_code == 200
        html = resp.data.decode()
        # With $500/period + 7% return + employer match over 20 years,
        # projected balance should be significantly more than $10,000.
        # Extract projected balances from the retirement accounts table.
        # The table has current balance then projected (fw-bold) on the same row.
        import re
        # Match the projected balance cells (fw-bold) that follow the current
        # balance cell and the annual return rate cell.  The current-balance
        # cell is matched by SHAPE rather than by the $10,000.00 literal it
        # used to carry: since plan step X-g2b an account anchored in the
        # current period earns that period's own days (ruling R-Y), so the
        # rendered opening balance is $10,000 plus a few dollars of accrual.
        projected_values = re.findall(
            r'\$10,0[0-9][0-9]\.\d{2}</td>\s*<td[^>]*>.*?</td>'
            r'\s*<td class="text-end font-mono fw-bold">\$([0-9,]+\.\d{2})',
            html, re.DOTALL,
        )
        assert projected_values, "Expected projected balance for retirement account"
        projected = float(projected_values[0].replace(",", ""))
        assert projected > 50000, (
            f"Projected balance {projected} should be >> $10,000 with contributions"
        )

    def test_dashboard_projects_without_retirement_date(
        self, auth_client, seed_user, db, seed_periods_today
    ):
        """Without planned retirement date, dashboard still renders correctly.

        The page should render the Retirement Planning heading and content
        even when no retirement date is configured.
        """
        _create_retirement_account(seed_user, db.session)

        # No planned_retirement_date set.
        settings = db.session.query(UserSettings).filter_by(
            user_id=seed_user["user"].id
        ).first()
        settings.planned_retirement_date = None
        db.session.commit()

        resp = auth_client.get("/retirement")
        assert resp.status_code == 200
        assert b"Retirement Planning" in resp.data

    def test_dashboard_pension_tax_shown(
        self, auth_client, seed_user, db, seed_periods_today
    ):
        """After-tax pension line shown when tax rate is set."""
        profile = _create_salary_profile(seed_user, db.session)
        _create_pension(seed_user, db.session, salary_profile=profile)

        settings = db.session.query(UserSettings).filter_by(
            user_id=seed_user["user"].id
        ).first()
        settings.planned_retirement_date = date(2046, 1, 1)
        settings.estimated_retirement_tax_rate = Decimal("0.2000")
        db.session.commit()

        resp = auth_client.get("/retirement")
        assert resp.status_code == 200
        html = resp.data.decode()
        # Realigned for direction D (P3b): the old "After-Tax Monthly
        # Pension" row retired; the stored 20% rate now nets the pension
        # footer line ("net of 20.0% est. tax" -- 0.2000|to_percent ->
        # 20.0 via "%.1f") and the income card's row is labeled net.  The
        # F1 not-set flag must NOT render once a rate is stored.
        assert "20.0% est. tax" in html
        assert "Pension (net)" in html
        assert "(not set" not in html

    def test_dashboard_projects_multiple_accounts(
        self, auth_client, seed_user, db, seed_periods_today
    ):
        """Multiple retirement accounts all project correctly."""
        _create_retirement_account(seed_user, db.session, "401(k)")
        _create_retirement_account(seed_user, db.session, "Roth IRA")

        settings = db.session.query(UserSettings).filter_by(
            user_id=seed_user["user"].id
        ).first()
        settings.planned_retirement_date = date(2046, 1, 1)
        db.session.commit()

        resp = auth_client.get("/retirement")
        assert resp.status_code == 200
        assert b"Test 401(k)" in resp.data
        assert b"Test Roth IRA" in resp.data

    def test_dashboard_uses_projected_salary_for_gap(
        self, auth_client, seed_user, db, seed_periods_today
    ):
        """Gap analysis uses projected pre-retirement income, not current."""
        from app.models.salary_raise import SalaryRaise
        from app.models.ref import RaiseType

        profile = _create_salary_profile(seed_user, db.session)

        # Add a recurring 3% annual raise.
        merit = db.session.query(RaiseType).filter_by(name="merit").one()
        raise_obj = SalaryRaise(
            salary_profile_id=profile.id,
            raise_type_id=merit.id,
            percentage=Decimal("0.0300"),
            effective_month=1,
            effective_year=date.today().year + 1,
            is_recurring=True,
        )
        db.session.add(raise_obj)

        settings = db.session.query(UserSettings).filter_by(
            user_id=seed_user["user"].id
        ).first()
        settings.planned_retirement_date = date(2046, 1, 1)
        db.session.commit()

        resp = auth_client.get("/retirement")
        assert resp.status_code == 200
        html = resp.data.decode()
        # Realigned for direction D (P3b): the old table row label is
        # retired; the income card's "Income to replace" figure must equal
        # the raise-aware readiness producer's net monthly target exactly
        # (page == service, a strictly stronger pin than the old
        # label-presence check).  The money macro renders "$1,234.56".
        from app.services import retirement_plan, retirement_readiness

        inputs = retirement_plan.load_retirement_inputs(
            BalanceContext.build(seed_user["user"].id),
        )
        readiness = retirement_readiness.readiness_from_picture(
            retirement_plan.picture_at(inputs, inputs.stored_plan),
        )
        target = readiness["income_target_net_monthly"]
        assert target > 0
        assert "Income to replace" in html
        assert f"${target:,.2f}" in html


class TestRetirementNegativePaths:
    """Negative-path and boundary tests for retirement routes."""

    def test_create_pension_missing_name(
        self, auth_client, seed_user, db, seed_periods_today,
    ):
        """Pension POST without name is rejected by schema (required field)."""
        resp = auth_client.post("/retirement/pension", data={
            "benefit_multiplier": "2.0",
            "consecutive_high_years": "4",
            "hire_date": "2020-01-01",
        })
        assert resp.status_code == 422
        assert b"is-invalid" in resp.data

        count = db.session.query(PensionProfile).filter_by(
            user_id=seed_user["user"].id,
        ).count()
        assert count == 0

    def test_create_pension_missing_hire_date(
        self, auth_client, seed_user, db, seed_periods_today,
    ):
        """Pension POST without hire_date is rejected (required field)."""
        resp = auth_client.post("/retirement/pension", data={
            "name": "State Pension",
            "benefit_multiplier": "2.0",
            "consecutive_high_years": "4",
        })
        assert resp.status_code == 422
        assert b"is-invalid" in resp.data

        count = db.session.query(PensionProfile).filter_by(
            user_id=seed_user["user"].id,
        ).count()
        assert count == 0

    def test_create_pension_negative_multiplier(
        self, auth_client, seed_user, db, seed_periods_today,
    ):
        """Pension POST with negative benefit_multiplier is rejected by Range(min=0, min_inclusive=False)."""
        resp = auth_client.post("/retirement/pension", data={
            "name": "Bad Pension",
            "benefit_multiplier": "-0.01",
            "consecutive_high_years": "4",
            "hire_date": "2020-01-01",
        })
        assert resp.status_code == 422
        assert b"is-invalid" in resp.data

        count = db.session.query(PensionProfile).filter_by(
            user_id=seed_user["user"].id,
        ).count()
        assert count == 0

    def test_create_pension_retirement_before_hire_rejected(
        self, auth_client, seed_user, db, seed_periods_today,
    ):
        """Planned retirement date before hire date is rejected."""
        resp = auth_client.post("/retirement/pension", data={
            "name": "Bad Dates",
            "benefit_multiplier": "1.85",
            "consecutive_high_years": "4",
            "hire_date": "2020-01-01",
            "planned_retirement_date": "2019-01-01",
        })
        assert resp.status_code == 422
        assert b"is-invalid" in resp.data

        count = db.session.query(PensionProfile).filter_by(
            user_id=seed_user["user"].id,
        ).count()
        assert count == 0

    def test_create_pension_retirement_in_past_rejected(
        self, auth_client, seed_user, db, seed_periods_today,
    ):
        """Planned retirement date in the past is rejected."""
        resp = auth_client.post("/retirement/pension", data={
            "name": "Past Retirement",
            "benefit_multiplier": "1.85",
            "consecutive_high_years": "4",
            "hire_date": "2010-01-01",
            "planned_retirement_date": "2020-01-01",
        })
        assert resp.status_code == 422
        assert b"is-invalid" in resp.data

        count = db.session.query(PensionProfile).filter_by(
            user_id=seed_user["user"].id,
        ).count()
        assert count == 0

    def test_create_pension_earliest_before_hire_rejected(
        self, auth_client, seed_user, db, seed_periods_today,
    ):
        """Earliest retirement date before hire date is rejected."""
        resp = auth_client.post("/retirement/pension", data={
            "name": "Bad Earliest",
            "benefit_multiplier": "1.85",
            "consecutive_high_years": "4",
            "hire_date": "2020-01-01",
            "earliest_retirement_date": "2019-06-01",
        })
        assert resp.status_code == 422
        assert b"is-invalid" in resp.data

        count = db.session.query(PensionProfile).filter_by(
            user_id=seed_user["user"].id,
        ).count()
        assert count == 0

    def test_create_pension_planned_before_earliest_rejected(
        self, auth_client, seed_user, db, seed_periods_today,
    ):
        """Planned retirement date before earliest retirement date is rejected."""
        resp = auth_client.post("/retirement/pension", data={
            "name": "Backwards Dates",
            "benefit_multiplier": "1.85",
            "consecutive_high_years": "4",
            "hire_date": "2010-01-01",
            "earliest_retirement_date": "2050-01-01",
            "planned_retirement_date": "2045-01-01",
        })
        assert resp.status_code == 422
        assert b"is-invalid" in resp.data

        count = db.session.query(PensionProfile).filter_by(
            user_id=seed_user["user"].id,
        ).count()
        assert count == 0

    def test_create_pension_valid_dates_accepted(
        self, auth_client, seed_user, db, seed_periods_today,
    ):
        """Valid date ordering is accepted and pension is created."""
        resp = auth_client.post("/retirement/pension", data={
            "name": "Good Dates",
            "benefit_multiplier": "1.85",
            "consecutive_high_years": "4",
            "hire_date": "2015-01-01",
            "earliest_retirement_date": "2045-01-01",
            "planned_retirement_date": "2050-01-01",
        })
        assert resp.status_code == 302

        pension = db.session.query(PensionProfile).filter_by(
            user_id=seed_user["user"].id, name="Good Dates",
        ).first()
        assert pension is not None
        assert pension.hire_date == date(2015, 1, 1)
        assert pension.earliest_retirement_date == date(2045, 1, 1)
        assert pension.planned_retirement_date == date(2050, 1, 1)

    def test_update_pension_retirement_before_hire_rejected(
        self, auth_client, seed_user, db, seed_periods_today,
    ):
        """Updating planned_retirement_date to before existing hire_date is rejected."""
        pension = _create_pension(seed_user, db.session)
        # pension.hire_date is 2018-07-01
        resp = auth_client.post(f"/retirement/pension/{pension.id}", data={
            "name": pension.name,
            "benefit_multiplier": "1.85",
            "consecutive_high_years": "4",
            "hire_date": "2018-07-01",
            "planned_retirement_date": "2017-01-01",
        })
        assert resp.status_code == 422
        assert b"Must be after hire date" in resp.data
        assert b"is-invalid" in resp.data

        # Pension should be unchanged.
        db.session.expire_all()
        after = db.session.get(PensionProfile, pension.id)
        assert after.planned_retirement_date == date(2048, 7, 1)

    def test_edit_nonexistent_pension(
        self, auth_client, seed_user, db, seed_periods_today,
    ):
        """GET edit for nonexistent pension returns 404 (security)."""
        resp = auth_client.get("/retirement/pension/999999/edit")
        assert resp.status_code == 404

    def test_update_nonexistent_pension(
        self, auth_client, seed_user, db, seed_periods_today,
    ):
        """POST update for nonexistent pension returns 404 (security)."""
        resp = auth_client.post("/retirement/pension/999999", data={
            "name": "Ghost",
            "benefit_multiplier": "2.0",
            "hire_date": "2020-01-01",
        })
        assert resp.status_code == 404

    def test_delete_nonexistent_pension(
        self, auth_client, seed_user, db, seed_periods_today,
    ):
        """POST delete for nonexistent pension returns 404 (security)."""
        resp = auth_client.post("/retirement/pension/999999/delete")
        assert resp.status_code == 404

    def test_edit_pension_idor_no_data_leaked(
        self, auth_client, second_user, db, seed_periods_today,
    ):
        """IDOR GET to edit pension does not leak victim's pension data."""
        pension = PensionProfile(
            user_id=second_user["user"].id,
            name="Secret Pension",
            benefit_multiplier=Decimal("0.02500"),
            consecutive_high_years=4,
            hire_date=date(2020, 1, 1),
        )
        db.session.add(pension)
        db.session.commit()

        resp = auth_client.get(f"/retirement/pension/{pension.id}/edit")
        assert resp.status_code == 404
        assert b"Secret Pension" not in resp.data
        assert b"0.02500" not in resp.data

    def test_update_settings_invalid_swr(
        self, auth_client, seed_user, db, seed_periods_today,
    ):
        """Non-numeric SWR is handled gracefully; original value preserved."""
        settings = db.session.query(UserSettings).filter_by(
            user_id=seed_user["user"].id,
        ).one()
        orig_swr = settings.safe_withdrawal_rate

        # The route's try/except passes on conversion failure, leaving "abc"
        # as-is. The schema then rejects it as not a valid Decimal.
        resp = auth_client.post("/retirement/settings", data={
            "safe_withdrawal_rate": "abc",
        })
        assert resp.status_code == 422
        # Designed fragment: the assumptions rail re-rendered with field
        # errors must carry the marker header so the global app.js
        # listener swaps it (replaced the retirement_controls.js shim).
        assert resp.headers.get(DESIGNED_FRAGMENT_HEADER) == "1"

        db.session.expire_all()
        after = db.session.query(UserSettings).filter_by(
            user_id=seed_user["user"].id,
        ).one()
        assert after.safe_withdrawal_rate == orig_swr

    def test_update_settings_negative_swr(
        self, auth_client, seed_user, db, seed_periods_today,
    ):
        """Negative SWR as percentage: -5 converts to -0.05, rejected by Range(min=0)."""
        settings = db.session.query(UserSettings).filter_by(
            user_id=seed_user["user"].id,
        ).one()
        orig_swr = settings.safe_withdrawal_rate

        resp = auth_client.post("/retirement/settings", data={
            "safe_withdrawal_rate": "-5",
        })
        assert resp.status_code == 422

        db.session.expire_all()
        after = db.session.query(UserSettings).filter_by(
            user_id=seed_user["user"].id,
        ).one()
        assert after.safe_withdrawal_rate == orig_swr

    def test_update_settings_zero_swr(
        self, auth_client, seed_user, db, seed_periods_today,
    ):
        """SWR of 0% converts to 0.00, which is valid per Range(min=0)."""
        # 0 / 100 = 0.00, which passes Range(min=0, max=1).
        # The gap calculator would need to handle division by zero separately.
        resp = auth_client.post("/retirement/settings", data={
            "safe_withdrawal_rate": "0",
        })
        assert resp.status_code == 302

        db.session.expire_all()
        settings = db.session.query(UserSettings).filter_by(
            user_id=seed_user["user"].id,
        ).one()
        # 0 / 100 = 0.0000
        assert settings.safe_withdrawal_rate == Decimal("0.0000")

    def test_update_settings_invalid_tax_rate(
        self, auth_client, seed_user, db, seed_periods_today,
    ):
        """Non-numeric tax rate is handled; original value preserved."""
        settings = db.session.query(UserSettings).filter_by(
            user_id=seed_user["user"].id,
        ).one()
        orig_tax = settings.estimated_retirement_tax_rate

        resp = auth_client.post("/retirement/settings", data={
            "estimated_retirement_tax_rate": "abc",
        })
        assert resp.status_code == 422

        db.session.expire_all()
        after = db.session.query(UserSettings).filter_by(
            user_id=seed_user["user"].id,
        ).one()
        assert after.estimated_retirement_tax_rate == orig_tax

    def test_update_settings_tax_rate_over_100(
        self, auth_client, seed_user, db, seed_periods_today,
    ):
        """Tax rate 150% converts to 1.50, rejected by Range(max=1)."""
        settings = db.session.query(UserSettings).filter_by(
            user_id=seed_user["user"].id,
        ).one()
        orig_tax = settings.estimated_retirement_tax_rate

        resp = auth_client.post("/retirement/settings", data={
            "estimated_retirement_tax_rate": "150",
        })
        assert resp.status_code == 422

        db.session.expire_all()
        after = db.session.query(UserSettings).filter_by(
            user_id=seed_user["user"].id,
        ).one()
        assert after.estimated_retirement_tax_rate == orig_tax

    def test_update_settings_invalid_date(
        self, auth_client, seed_user, db, seed_periods_today,
    ):
        """Non-date retirement date is rejected; original date preserved."""
        settings = db.session.query(UserSettings).filter_by(
            user_id=seed_user["user"].id,
        ).one()
        orig_date = settings.planned_retirement_date

        resp = auth_client.post("/retirement/settings", data={
            "planned_retirement_date": "not-a-date",
        })
        assert resp.status_code == 422

        db.session.expire_all()
        after = db.session.query(UserSettings).filter_by(
            user_id=seed_user["user"].id,
        ).one()
        assert after.planned_retirement_date == orig_date

    def test_create_pension_login_required(self, client, db):
        """Unauthenticated POST to create pension redirects to login."""
        resp = client.post("/retirement/pension", data={
            "name": "Sneaky",
            "benefit_multiplier": "2.0",
            "hire_date": "2020-01-01",
        })
        assert resp.status_code == 302
        assert "/login" in resp.headers["Location"]

    def test_update_settings_login_required(self, client, db):
        """Unauthenticated POST to update settings redirects to login."""
        resp = client.post("/retirement/settings", data={
            "safe_withdrawal_rate": "4",
        })
        assert resp.status_code == 302
        assert "/login" in resp.headers["Location"]


class TestRetirementValidationUX:
    """Tests for render-on-error validation UX with field highlights and data preservation."""

    def test_pension_validation_error_preserves_all_fields(
        self, auth_client, seed_user, db, seed_periods_today,
    ):
        """All submitted field values are preserved in the re-rendered form on error."""
        profile = _create_salary_profile(seed_user, db.session)
        pension = _create_pension(seed_user, db.session, salary_profile=profile)
        resp = auth_client.post(f"/retirement/pension/{pension.id}", data={
            "name": "My Updated Name",
            "salary_profile_id": str(profile.id),
            "benefit_multiplier": "2.500",
            "consecutive_high_years": "3",
            "hire_date": "2018-07-01",
            "earliest_retirement_date": "2040-01-01",
            "planned_retirement_date": "2017-01-01",  # Before hire -> error
        })
        assert resp.status_code == 422
        html = resp.data.decode()
        # Every submitted value must appear in the re-rendered form.
        assert 'value="My Updated Name"' in html
        assert 'value="2.500"' in html
        assert 'value="3"' in html
        assert 'value="2018-07-01"' in html
        assert 'value="2040-01-01"' in html
        assert 'value="2017-01-01"' in html

    def test_pension_validation_error_highlights_invalid_field(
        self, auth_client, seed_user, db, seed_periods_today,
    ):
        """Invalid field gets is-invalid class and invalid-feedback message."""
        pension = _create_pension(seed_user, db.session)
        resp = auth_client.post(f"/retirement/pension/{pension.id}", data={
            "name": pension.name,
            "benefit_multiplier": "1.850",
            "consecutive_high_years": "4",
            "hire_date": "2018-07-01",
            "planned_retirement_date": "2017-01-01",
        })
        assert resp.status_code == 422
        html = resp.data.decode()
        assert "is-invalid" in html
        assert "invalid-feedback" in html
        assert "Must be after hire date" in html

    def test_pension_validation_error_does_not_highlight_valid_fields(
        self, auth_client, seed_user, db, seed_periods_today,
    ):
        """Only the invalid field gets is-invalid; valid fields do not."""
        pension = _create_pension(seed_user, db.session)
        resp = auth_client.post(f"/retirement/pension/{pension.id}", data={
            "name": pension.name,
            "benefit_multiplier": "1.850",
            "consecutive_high_years": "4",
            "hire_date": "2018-07-01",
            "planned_retirement_date": "2017-01-01",
        })
        assert resp.status_code == 422
        html = resp.data.decode()
        assert "Must be after hire date" in html
        # Only planned_retirement_date should have is-invalid.
        assert html.count("is-invalid") == 1

    def test_pension_validation_multiple_errors(
        self, auth_client, seed_user, db, seed_periods_today,
    ):
        """Multiple date fields with errors are all highlighted."""
        pension = _create_pension(seed_user, db.session)
        # Both earliest and planned are before hire date (2018-07-01).
        resp = auth_client.post(f"/retirement/pension/{pension.id}", data={
            "name": pension.name,
            "benefit_multiplier": "1.850",
            "consecutive_high_years": "4",
            "hire_date": "2018-07-01",
            "earliest_retirement_date": "2017-06-01",
            "planned_retirement_date": "2017-01-01",
        })
        assert resp.status_code == 422
        html = resp.data.decode()
        # Both fields should have errors.
        assert html.count("is-invalid") >= 2
        assert html.count("invalid-feedback") >= 2

    def test_pension_valid_submission_still_works(
        self, auth_client, seed_user, db, seed_periods_today,
    ):
        """Valid pension update still redirects and persists changes (regression)."""
        profile = _create_salary_profile(seed_user, db.session)
        pension = _create_pension(seed_user, db.session, salary_profile=profile)
        resp = auth_client.post(f"/retirement/pension/{pension.id}", data={
            "name": "Regression Test",
            "salary_profile_id": str(profile.id),
            "benefit_multiplier": "2.000",
            "consecutive_high_years": "3",
            "hire_date": "2018-07-01",
            "planned_retirement_date": "2050-01-01",
        })
        assert resp.status_code == 302
        db.session.refresh(pension)
        assert pension.name == "Regression Test"
        assert pension.benefit_multiplier == Decimal("0.02000")

    def test_pension_validation_returns_422_not_redirect(
        self, auth_client, seed_user, db, seed_periods_today,
    ):
        """Validation failure returns 422 HTML, not a 302 redirect."""
        pension = _create_pension(seed_user, db.session)
        resp = auth_client.post(f"/retirement/pension/{pension.id}", data={
            "name": pension.name,
            "benefit_multiplier": "1.850",
            "hire_date": "2018-07-01",
            "planned_retirement_date": "2017-01-01",
        })
        assert resp.status_code == 422
        assert resp.content_type.startswith("text/html")
        assert "Location" not in resp.headers

    def test_pension_form_data_not_present_on_get(
        self, auth_client, seed_user, db, seed_periods_today,
    ):
        """GET renders the form from the pension model with no error indicators."""
        pension = _create_pension(seed_user, db.session)
        resp = auth_client.get(f"/retirement/pension/{pension.id}/edit")
        assert resp.status_code == 200
        html = resp.data.decode()
        assert "is-invalid" not in html
        assert "invalid-feedback" not in html
        assert "State Pension" in html

    def test_settings_validation_error_preserves_form_data(
        self, auth_client, seed_user, db, seed_periods_today,
    ):
        """Settings validation error re-renders with submitted values preserved."""
        resp = auth_client.post("/retirement/settings", data={
            "safe_withdrawal_rate": "-5",
            "planned_retirement_date": "2055-01-01",
            "estimated_retirement_tax_rate": "20",
        })
        assert resp.status_code == 422
        html = resp.data.decode()
        # The submitted SWR should be in the form (original user input).
        assert 'value="-5"' in html
        assert "is-invalid" in html

    def test_settings_validation_error_highlights_field(
        self, auth_client, seed_user, db, seed_periods_today,
    ):
        """Settings error highlights the invalid field with error message."""
        resp = auth_client.post("/retirement/settings", data={
            "estimated_retirement_tax_rate": "150",
        })
        assert resp.status_code == 422
        html = resp.data.decode()
        assert "is-invalid" in html
        assert "invalid-feedback" in html

    def test_settings_valid_submission_still_works(
        self, auth_client, seed_user, db, seed_periods_today,
    ):
        """Valid settings POST still redirects and persists (regression)."""
        resp = auth_client.post("/retirement/settings", data={
            "safe_withdrawal_rate": "3.5",
            "planned_retirement_date": "2055-01-01",
            "estimated_retirement_tax_rate": "22",
        })
        assert resp.status_code == 302
        db.session.expire_all()
        settings = db.session.query(UserSettings).filter_by(
            user_id=seed_user["user"].id,
        ).one()
        assert settings.safe_withdrawal_rate == Decimal("0.0350")
        assert settings.planned_retirement_date == date(2055, 1, 1)

    def test_pension_error_then_success(
        self, auth_client, seed_user, db, seed_periods_today,
    ):
        """A failed submission does not poison subsequent successful submissions."""
        pension = _create_pension(seed_user, db.session)
        # First: invalid submission.
        resp1 = auth_client.post(f"/retirement/pension/{pension.id}", data={
            "name": "Attempt 1",
            "benefit_multiplier": "1.850",
            "consecutive_high_years": "4",
            "hire_date": "2018-07-01",
            "planned_retirement_date": "2017-01-01",
        })
        assert resp1.status_code == 422

        # Second: corrected submission.
        resp2 = auth_client.post(f"/retirement/pension/{pension.id}", data={
            "name": "Attempt 2",
            "benefit_multiplier": "1.850",
            "consecutive_high_years": "4",
            "hire_date": "2018-07-01",
            "planned_retirement_date": "2050-01-01",
        })
        assert resp2.status_code == 302
        db.session.refresh(pension)
        assert pension.name == "Attempt 2"
        assert pension.planned_retirement_date == date(2050, 1, 1)

    def test_pension_select_fields_preserve_selection(
        self, auth_client, seed_user, db, seed_periods_today,
    ):
        """Select dropdown preserves the selected salary profile on error re-render."""
        import re

        profile = _create_salary_profile(seed_user, db.session)
        pension = _create_pension(seed_user, db.session)
        resp = auth_client.post(f"/retirement/pension/{pension.id}", data={
            "name": "Select Test",
            "salary_profile_id": str(profile.id),
            "benefit_multiplier": "1.850",
            "consecutive_high_years": "4",
            "hire_date": "2018-07-01",
            "planned_retirement_date": "2017-01-01",
        })
        assert resp.status_code == 422
        html = resp.data.decode()
        # The salary profile option should be selected.
        pattern = rf'value="{profile.id}"\s+selected'
        assert re.search(pattern, html), (
            f"Salary profile {profile.id} option not selected in re-rendered form"
        )

    def test_pension_empty_date_handled(
        self, auth_client, seed_user, db, seed_periods_today,
    ):
        """Empty planned_retirement_date does not crash -- treated as omitted."""
        pension = _create_pension(seed_user, db.session)
        resp = auth_client.post(f"/retirement/pension/{pension.id}", data={
            "name": "No Date",
            "benefit_multiplier": "1.850",
            "consecutive_high_years": "4",
            "hire_date": "2018-07-01",
            "planned_retirement_date": "",
        })
        # Empty optional date is stripped by pre_load and omitted from update.
        assert resp.status_code == 302


class TestReturnRateClarity:
    """Tests for return rate slider tooltip and per-account rate display."""

    def test_return_row_shows_blended_value_and_caption(
        self, auth_client, seed_user, db, seed_periods_today,
    ):
        """The assumed-return row shows the blended rate and its provenance.

        Realigned (P3b): the slider tooltip retired; the assumptions
        rail's what-if-only return row renders the balance-weighted
        blended rate with its provenance caption.  With a single account
        at 7% the weighted average IS 7% -> "%.2f" renders "7.00%".
        """
        profile = _create_salary_profile(seed_user, db.session)
        _create_pension(seed_user, db.session, salary_profile=profile)
        _create_retirement_account(seed_user, db.session)
        resp = auth_client.get("/retirement")
        assert resp.status_code == 200
        html = resp.data.decode()
        assert "7.00%" in html
        assert "per-account rates own this" in html

    def test_per_account_rate_displayed_on_dashboard(
        self, auth_client, seed_user, db, seed_periods_today,
    ):
        """Each retirement account row shows its configured annual return rate."""
        account, params = _create_retirement_account(seed_user, db.session)
        settings = db.session.query(UserSettings).filter_by(
            user_id=seed_user["user"].id
        ).first()
        settings.planned_retirement_date = date(2046, 1, 1)
        db.session.commit()

        resp = auth_client.get("/retirement")
        assert resp.status_code == 200
        html = resp.data.decode()
        # Realigned for direction D (P3b): the column header is now
        # "Return"; params.assumed_annual_return 0.07000|to_percent -> 7.0
        # via "%.1f" still renders in the row.
        assert "7.0%" in html
        assert ">Return</th>" in html

    def test_per_account_rate_accuracy_multiple_accounts(
        self, auth_client, seed_user, db, seed_periods_today,
    ):
        """Multiple accounts with different rates each show their own rate."""
        acct1, params1 = _create_retirement_account(
            seed_user, db.session, type_name="401(k)",
        )
        # Create a second account with a different rate.
        acct_type = db.session.query(AccountType).filter_by(name="Roth IRA").one()
        acct2 = account_service.create_account(
            account_service.AccountSpec(
                user_id=seed_user["user"].id,
                account_type_id=acct_type.id,
                name="Test Roth IRA",
                anchor_balance=Decimal("5000.00"),
            ),
        )
        db.session.add(acct2)
        db.session.flush()
        params2 = InvestmentParams(
            account_id=acct2.id,
            assumed_annual_return=Decimal("0.09500"),
            annual_contribution_limit=Decimal("7000.00"),
            employer_contribution_type_id=ref_cache.employer_contribution_type_id(
                EmployerContributionTypeEnum.NONE,
            ),
        )
        db.session.add(params2)

        settings = db.session.query(UserSettings).filter_by(
            user_id=seed_user["user"].id
        ).first()
        settings.planned_retirement_date = date(2046, 1, 1)
        db.session.commit()

        resp = auth_client.get("/retirement")
        assert resp.status_code == 200
        html = resp.data.decode()
        # 401(k) at 7.0%, Roth IRA at 9.5% -- each in its own column cell.
        assert ">7.0%<" in html
        assert ">9.5%<" in html

    def test_initial_dashboard_renders_no_oob(
        self, auth_client, seed_user, db, seed_periods_today,
    ):
        """The full page render carries no out-of-band swap attributes.

        Realigned (P3b): the gap card's OOB accounts container retired;
        OOB siblings now ride ONLY standalone readiness fragment
        responses (the ``embedded`` flag suppresses them in the page
        include), so a full page load must contain zero hx-swap-oob
        attributes while still rendering the stable income panel shell.
        """
        _create_retirement_account(seed_user, db.session)
        settings = db.session.query(UserSettings).filter_by(
            user_id=seed_user["user"].id
        ).first()
        settings.planned_retirement_date = date(2046, 1, 1)
        db.session.commit()

        resp = auth_client.get("/retirement")
        assert resp.status_code == 200
        html = resp.data.decode()
        assert "hx-swap-oob" not in html
        assert 'id="income-panel"' in html

    def test_whatif_controls_present(self, auth_client, seed_user, db, seed_periods_today):
        """The direction-D what-if inputs replace the retired sliders.

        Realigned (P3b): the two sensitivity sliders retired into the
        assumptions rail's what-if inputs (audit ruling 7) -- the SWR row
        input, the what-if-only return input, and the lever steppers.
        """
        _create_retirement_account(seed_user, db.session)
        resp = auth_client.get("/retirement")
        assert resp.status_code == 200
        html = resp.data.decode()
        assert 'id="assump-swr"' in html
        assert 'id="assump-return-rate"' in html
        assert 'id="return_slider"' not in html
        assert 'id="swr_slider"' not in html

    def test_gap_analysis_user_created_pretax_type(
        self, app, auth_client, seed_user, db, seed_periods_today,
    ):
        """A user-created Retirement type with is_pretax=True is flagged
        as traditional (pre-tax) in the gap analysis projections."""
        from app import ref_cache
        from app.enums import AcctCategoryEnum
        from app.services.retirement_plan import (
            load_retirement_inputs,
            picture_at,
        )

        with app.app_context():
            _create_salary_profile(seed_user, db.session)
            settings = db.session.query(UserSettings).filter_by(
                user_id=seed_user["user"].id,
            ).one()
            settings.planned_retirement_date = date(2060, 1, 1)
            db.session.commit()

            custom_type = AccountType(
                name="Test403b",
                category_id=ref_cache.acct_category_id(AcctCategoryEnum.RETIREMENT),
                has_parameters=True,
                is_pretax=True,
                icon_class="bi-graph-up-arrow",
            )
            db.session.add(custom_type)
            db.session.flush()

            acct = account_service.create_account(
                account_service.AccountSpec(
                    user_id=seed_user["user"].id,
                    account_type_id=custom_type.id,
                    name="My 403(b)",
                    anchor_balance=Decimal("50000"),
                ),
            )
            db.session.add(acct)
            db.session.flush()
            db.session.add(InvestmentParams(
                account_id=acct.id,
                employer_contribution_type_id=ref_cache.employer_contribution_type_id(
                    EmployerContributionTypeEnum.NONE,
                ),
            ))
            db.session.commit()

            inputs = load_retirement_inputs(
                BalanceContext.build(seed_user["user"].id),
            )
            picture = picture_at(inputs, inputs.stored_plan)
            proj = next(
                p for p in picture.projections if p["account"].id == acct.id
            )
            assert proj["is_traditional"] is True

    def test_gap_analysis_posttax_type(
        self, app, auth_client, seed_user, db, seed_periods_today,
    ):
        """A Retirement type with is_pretax=False is NOT flagged as traditional."""
        from app import ref_cache
        from app.enums import AcctCategoryEnum
        from app.services.retirement_plan import (
            load_retirement_inputs,
            picture_at,
        )

        with app.app_context():
            _create_salary_profile(seed_user, db.session)
            settings = db.session.query(UserSettings).filter_by(
                user_id=seed_user["user"].id,
            ).one()
            settings.planned_retirement_date = date(2060, 1, 1)
            db.session.commit()

            custom_type = AccountType(
                name="TestRothSolo",
                category_id=ref_cache.acct_category_id(AcctCategoryEnum.RETIREMENT),
                has_parameters=True,
                is_pretax=False,
                icon_class="bi-graph-up-arrow",
            )
            db.session.add(custom_type)
            db.session.flush()

            acct = account_service.create_account(
                account_service.AccountSpec(
                    user_id=seed_user["user"].id,
                    account_type_id=custom_type.id,
                    name="My Roth Solo 401(k)",
                    anchor_balance=Decimal("25000"),
                ),
            )
            db.session.add(acct)
            db.session.flush()
            db.session.add(InvestmentParams(
                account_id=acct.id,
                employer_contribution_type_id=ref_cache.employer_contribution_type_id(
                    EmployerContributionTypeEnum.NONE,
                ),
            ))
            db.session.commit()

            inputs = load_retirement_inputs(
                BalanceContext.build(seed_user["user"].id),
            )
            picture = picture_at(inputs, inputs.stored_plan)
            proj = next(
                p for p in picture.projections if p["account"].id == acct.id
            )
            assert proj["is_traditional"] is False

    def test_gap_analysis_no_retirement_accounts(
        self, app, auth_client, seed_user, db,
    ):
        """Gap analysis returns empty projections when no retirement accounts exist."""
        from app.services.retirement_plan import (
            load_retirement_inputs,
            picture_at,
        )

        with app.app_context():
            inputs = load_retirement_inputs(
                BalanceContext.build(seed_user["user"].id),
            )
            picture = picture_at(inputs, inputs.stored_plan)
            assert picture.projections == []


def _seed_underfunded(seed_user, db_session):
    """Salary + settings retirement date + capped 401(k), no pension.

    Deterministically underfunded on both levers: the ~$80k salary
    drives a required target well over $1M (net biweekly ~ $2,200 ->
    required = net * 26 / 0.04 ~ $1.4M) while the $10k account at 7%
    with zero contributions projects to only ~$40k by +240 months --
    the contribution lever solves (and its solution, roughly
    shortfall / AF ~ $1.3M / ~1100 ~ $1,200+/period, exceeds the
    23500 / 26 = $903.85 per-period limit headroom), and retiring
    later alone cannot close the gap even at +180 months
    ($10k * 1.07^35 < $110k).  Shared by the lever-fragment (P2c) and
    readiness-fragment (P3a) route tests.
    """
    from app.utils.dates import add_months

    _create_salary_profile(seed_user, db_session)
    settings = (
        db_session.query(UserSettings)
        .filter_by(user_id=seed_user["user"].id)
        .one()
    )
    settings.planned_retirement_date = add_months(date.today(), 240)
    _create_retirement_account(seed_user, db_session)
    db_session.commit()


class TestTheRailStatesEachRecurringRaisesEndYear:
    """The rail row that replaced "Merit raises continue (years)".

    Plan step **salary:S3-c** (ruling **R-SAL11**) deleted the global setting
    that row saved, because the horizon a recurring raise decays over is a
    fact on the RAISE.  The rail states each recurring raise's end year
    instead, with a link to the salary page that owns the edit -- so
    ``/retirement`` says what it is projecting from and has no second home
    for the belief to disagree from.  **Since plan step salary:S3-f-2b the
    row IS the salary form's end-year pair** (ruling **R-SAL13**): a mode
    select pre-selected from the row and a year input pre-filled from it,
    both what-if inputs, so "the rail states the stored end year" is graded
    on the SELECTED option and the input's VALUE -- the read-only cell that
    spelled "ends after 2033" is gone, and the strings that graded it went
    with it.  Probing one is :class:`TestTheRailProbesARaisesEndYear`'s.

    **Both render sites are covered and they load differently.**  The
    dashboard's include gets the profiles off the render's own
    ``RetirementInputs``; ``retirement.update_settings`` re-renders the same
    fragment after a save and loads them itself.  A context dropped at either
    site renders the empty state silently -- which is exactly the P4
    live-verify defect that made ``settings`` worth pinning, one fragment
    earlier.
    """

    @staticmethod
    def _seed_raise(seed_user, db, *, ends_after=None, effective_year=None):
        """Give the owner one recurring 2.5% January raise.

        *ends_after* is a number of years PAST the effective year, not a
        literal: a fixture pinned to 2033 starts failing in 2033 against
        ``ck_salary_raises_terminal_year_not_before_effective``, which is a
        test that expires rather than one that measures.
        """
        # Pylint: ``import-outside-toplevel`` (2) -- both models are imported
        # locally, exactly as the neighbouring raise-seeding case in this
        # module does, so the module's import block is left unchanged.
        from app.models.ref import RaiseType  # pylint: disable=import-outside-toplevel
        from app.models.salary_raise import SalaryRaise  # pylint: disable=import-outside-toplevel

        profile = _create_salary_profile(seed_user, db.session)
        merit = db.session.query(RaiseType).filter_by(name="merit").one()
        starts = effective_year or (display_today().year + 1)
        db.session.add(SalaryRaise(
            salary_profile_id=profile.id,
            raise_type_id=merit.id,
            percentage=Decimal("0.0250"),
            effective_month=1,
            effective_year=starts,
            is_recurring=True,
            terminal_year=None if ends_after is None else starts + ends_after,
        ))
        db.session.commit()
        return starts + (ends_after or 0)

    @staticmethod
    def _raise_row(html):
        """The ONE recurring-raise row's markup, or fail if there is not one.

        The row is a ``<form>`` since plan step salary:S3-f-3 gave it a Save.
        """
        rows = re.findall(_RAISE_ROW_MARKUP, html, re.S)
        assert len(rows) == 1, f"expected one raise row, found {len(rows)}"
        return rows[0]

    def _assert_row_states(self, html, *, mode, year):
        """The row's mode select has *mode* selected and its year input *year*.

        Graded as the template emits them -- ``<option value=".." selected>``
        and ``value=".."`` on the year input, both NAMED by the raise id --
        because these are the controls a readiness refresh submits
        (``feedback_a_route_test_must_post_what_the_template_emits``).
        """
        raise_id, row = self._raise_row(html)
        assert f'name="raise_end_mode_{raise_id}"' in row
        assert f'<option value="{mode}" selected>' in row, (
            f"the mode select does not pre-select {mode!r}: {row}"
        )
        assert row.count(" selected>") == 1, "two options selected at once"
        assert (
            f'name="raise_end_year_{raise_id}" placeholder="year"\n'
            in row
        )
        assert f'value="{year}"\n' in row, (
            f"the year input is not pre-filled with {year!r}: {row}"
        )
        return raise_id

    def test_the_dashboard_states_a_stored_end_year(
        self, auth_client, seed_user, db, seed_periods_today,
    ):
        """A raise with an end year pre-selects "ends after" and fills the year."""
        ends = self._seed_raise(seed_user, db, ends_after=6)

        html = auth_client.get("/retirement").data.decode()

        self._assert_row_states(html, mode="year", year=ends)

    def test_the_dashboard_states_a_raise_with_no_end_year(
        self, auth_client, seed_user, db, seed_periods_today,
    ):
        """``NULL`` is an ANSWER and the rail says so, rather than blank.

        "no end year" and an empty cell are different claims: the first says
        the owner believes the raise continues, the second reads as a fact
        nobody has supplied.  The projection compounds it forever either way,
        so the rail must not make that look unstated: the mode select
        pre-selects *no end year* and the year box is empty, which is the pair
        the salary form itself would show for the row.
        """
        self._seed_raise(seed_user, db, ends_after=None)

        html = auth_client.get("/retirement").data.decode()

        self._assert_row_states(html, mode="none", year="")

    def test_an_owner_with_no_recurring_raises_reads_none_recorded(
        self, auth_client, seed_user, db, seed_periods_today,
    ):
        """The empty state is STATED, not a silently missing row.

        The rail lost a row it always had; rendering nothing at all would read
        as the feature having disappeared rather than as there being nothing
        to say.
        """
        _create_salary_profile(seed_user, db.session)
        db.session.commit()

        html = auth_client.get("/retirement").data.decode()

        assert "none recorded" in html

    def test_a_one_time_raise_contributes_no_row(
        self, auth_client, seed_user, db, seed_periods_today,
    ):
        """Only recurring raises carry the question at all.

        ``ck_salary_raises_terminal_year_only_on_a_recurring_raise`` forbids a
        one-time raise an end year, so a row for one would state a question
        that cannot be answered.
        """
        # Pylint: ``import-outside-toplevel`` (2) -- both models are imported
        # locally, exactly as the neighbouring raise-seeding case in this
        # module does, so the module's import block is left unchanged.
        from app.models.ref import RaiseType  # pylint: disable=import-outside-toplevel
        from app.models.salary_raise import SalaryRaise  # pylint: disable=import-outside-toplevel

        profile = _create_salary_profile(seed_user, db.session)
        merit = db.session.query(RaiseType).filter_by(name="merit").one()
        db.session.add(SalaryRaise(
            salary_profile_id=profile.id,
            raise_type_id=merit.id,
            percentage=Decimal("0.0250"),
            effective_month=1,
            effective_year=date.today().year + 1,
            is_recurring=False,
        ))
        db.session.commit()

        html = auth_client.get("/retirement").data.decode()

        assert "none recorded" in html

    def test_the_rail_keeps_the_rows_after_a_save(
        self, auth_client, seed_user, db, seed_periods_today,
    ):
        """The SECOND render site, which loads the profiles for itself.

        ``update_settings`` re-renders this fragment from its own queries
        rather than from a render's ``RetirementInputs``, so a missing load
        there shows up only after a save -- the rail would state the raise
        before you touched it and "none recorded" afterwards, on the same
        page, with no error anywhere.
        """
        ends = self._seed_raise(seed_user, db, ends_after=6)

        resp = auth_client.post(
            "/retirement/settings",
            data={"safe_withdrawal_rate": "3.5"},
            headers={"HX-Request": "true"},
        )

        assert resp.status_code == 200
        html = resp.data.decode()
        assert 'data-assumption="raise_terminal_year"' in html, (
            "the rail's re-render after a save lost the recurring-raise "
            "rows, so update_settings is not loading the salary profiles the "
            "dashboard's include supplies"
        )
        self._assert_row_states(html, mode="year", year=ends)


class TestAssumptionSaves:
    """P3a per-field assumption saves through retirement.update_settings."""

    def test_a_merit_horizon_post_is_dropped_not_stored(
        self, auth_client, seed_user, db, seed_periods_today,
    ):
        """The deleted setting has no save path left (plan step salary:S3-c).

        ``merit_raise_horizon_years`` was a saveable rail field until ruling
        **R-SAL11** moved the belief onto each raise row.  Both its schema
        field and its ``_SETTINGS_FIELDS`` allowlist entry are gone, so
        ``BaseSchema``'s ``unknown = EXCLUDE`` drops a stale post -- from a
        bookmark, a replayed form, or a cached page -- rather than 422-ing
        it.  **The assertion that matters is that nothing is WRITTEN**: an
        allowlist entry surviving the column would be a 500 on the setattr,
        and a schema field surviving it would be a silent write to a column
        that no longer exists.
        """
        resp = auth_client.post("/retirement/settings", data={
            "merit_raise_horizon_years": "10",
        })
        assert resp.status_code == 302
        settings = db.session.query(UserSettings).filter_by(
            user_id=seed_user["user"].id,
        ).one()
        assert not hasattr(settings, "merit_raise_horizon_years"), (
            "auth.user_settings still carries merit_raise_horizon_years; "
            "plan step salary:S3-c deletes it, and while it exists the "
            "retirement page has a second home for a belief that lives on "
            "the raise row"
        )

    def test_htmx_save_returns_assumptions_fragment(
        self, auth_client, seed_user, db, seed_periods_today,
    ):
        """An HTMX save responds with the refreshed assumptions panel.

        Stored fraction 0.0350 renders back through to_percent as
        3.50 -> "%.2f" (the P3b rail renders SWR at two decimals) ->
        value="3.50" in the SWR row's input.
        """
        resp = auth_client.post(
            "/retirement/settings",
            data={"safe_withdrawal_rate": "3.5"},
            headers={"HX-Request": "true"},
        )
        assert resp.status_code == 200
        html = resp.data.decode()
        assert 'id="assumptions-panel"' in html
        assert 'value="3.50"' in html
        settings = db.session.query(UserSettings).filter_by(
            user_id=seed_user["user"].id,
        ).one()
        # 3.5 / 100 = 0.035 (schema percent-to-fraction pre_load).
        assert settings.safe_withdrawal_rate == Decimal("0.0350")

    def test_422_renders_assumptions_fragment_with_echo(
        self, auth_client, seed_user, db, seed_periods_today,
    ):
        """A validation failure is the assumptions fragment at 422.

        -5% converts to -0.05 and fails Range(min=0); the fragment echoes
        the submitted raw value and marks the field invalid -- the same
        form-error semantics the retired settings-page re-render carried.
        """
        resp = auth_client.post("/retirement/settings", data={
            "safe_withdrawal_rate": "-5",
        })
        assert resp.status_code == 422
        html = resp.data.decode()
        assert 'id="assumptions-panel"' in html
        assert 'value="-5"' in html
        assert "is-invalid" in html
        assert "invalid-feedback" in html

    def test_non_htmx_success_redirects_to_retirement(
        self, auth_client, seed_user, db, seed_periods_today,
    ):
        """The non-HTMX fallback lands on /retirement, not /settings.

        Gate A ruling 6: Settings > Retirement retired, so the redirect
        target is the retirement page that now owns these fields.
        """
        resp = auth_client.post("/retirement/settings", data={
            "safe_withdrawal_rate": "4",
        })
        assert resp.status_code == 302
        assert "/retirement" in resp.headers["Location"]
        assert "/settings" not in resp.headers["Location"]

    def test_assumed_return_has_no_save_path(
        self, auth_client, seed_user, db, seed_periods_today,
    ):
        """Return-rate-shaped fields are dropped, never persisted.

        There is deliberately no schema field for an assumed annual
        return (its save semantics are an open developer question), so
        BaseSchema's unknown=EXCLUDE drops these keys and the POST is a
        no-op success: every stored assumption is byte-identical after.
        """
        before = db.session.query(UserSettings).filter_by(
            user_id=seed_user["user"].id,
        ).one()
        snapshot = (
            before.safe_withdrawal_rate,
            before.planned_retirement_date,
            before.estimated_retirement_tax_rate,
        )
        resp = auth_client.post("/retirement/settings", data={
            "assumed_annual_return": "9",
            "return_rate": "9",
        })
        assert resp.status_code == 302
        db.session.expire_all()
        after = db.session.query(UserSettings).filter_by(
            user_id=seed_user["user"].id,
        ).one()
        assert (
            after.safe_withdrawal_rate,
            after.planned_retirement_date,
            after.estimated_retirement_tax_rate,
        ) == snapshot


    def test_fragment_renders_pension_owned_date_row(
        self, auth_client, seed_user, db, seed_periods_today,
    ):
        """The per-field save fragment renders the write-through date row.

        Acceptance r2 item 2: after any rail save, the re-rendered
        fragment keeps the editable date input (echoing the resolved
        pension date) with the "updates <pension>" caption (the
        update_settings path resolves provenance itself -- it does not
        ride the dashboard context).
        """
        profile = _create_salary_profile(seed_user, db.session)
        _create_pension(seed_user, db.session, salary_profile=profile)
        resp = auth_client.post(
            "/retirement/settings",
            data={"safe_withdrawal_rate": "3.5"},
            headers={"HX-Request": "true"},
        )
        assert resp.status_code == 200
        html = resp.data.decode()
        assert 'data-assumption-source="pension"' in html
        assert 'id="assump-retirement-date"' in html
        assert 'value="2048-07-01"' in html

    def test_pension_owned_date_save_writes_through_to_the_pension(
        self, auth_client, seed_user, db, seed_periods_today,
    ):
        """Saving the date row updates the OWNING pension, not settings.

        Acceptance r2 item 2 (developer ruling): the pension owns the
        resolved date, so Save persists to
        PensionProfile.planned_retirement_date via the ORM (the audited
        pension_profiles trigger must capture the UPDATE), leaves the
        settings column untouched, and the reloaded page projects from
        the new resolved date (recomputed countdown header).
        """
        from app.models.pension_profile import PensionProfile

        profile = _create_salary_profile(seed_user, db.session)
        pension = _create_pension(seed_user, db.session, salary_profile=profile)
        settings = db.session.query(UserSettings).filter_by(
            user_id=seed_user["user"].id,
        ).one()
        settings.planned_retirement_date = date(2040, 1, 1)
        db.session.commit()

        resp = auth_client.post(
            "/retirement/settings",
            data={"planned_retirement_date": "2050-06-01"},
            headers={"HX-Request": "true"},
        )
        assert resp.status_code == 200
        db.session.expire_all()
        # The OWNING pension carries the new date...
        stored = db.session.get(PensionProfile, pension.id)
        assert stored.planned_retirement_date == date(2050, 6, 1)
        # ...and the settings column is untouched.
        after_settings = db.session.query(UserSettings).filter_by(
            user_id=seed_user["user"].id,
        ).one()
        assert after_settings.planned_retirement_date == date(2040, 1, 1)

        # The audited-table trigger captured the ORM update.
        audit_rows = db.session.execute(db.text(
            "SELECT 1 FROM system.audit_log "
            "WHERE table_name = 'pension_profiles' AND operation = 'UPDATE'"
        )).fetchall()
        assert audit_rows, "pension_profiles UPDATE missing from audit_log"

        # The page recomputes from the new resolved date.
        page = auth_client.get("/retirement").data.decode()
        assert "Planned date 2050-06-01" in page
        assert 'value="2050-06-01"' in page

    def test_pension_owned_date_save_enforces_after_hire_rule(
        self, auth_client, seed_user, db, seed_periods_today,
    ):
        """The write-through enforces the pension form's after-hire rule.

        A pension hired in the FUTURE (2049-01-01) makes the rule
        genuinely reachable for a future candidate date: 2048-12-01 is
        in the future (passing the schema's M1 check) but on/before the
        hire date, so the shared pension validator rejects it at 422
        and nothing persists.
        """
        from app.models.pension_profile import PensionProfile

        profile = _create_salary_profile(seed_user, db.session)
        pension = PensionProfile(
            user_id=seed_user["user"].id,
            salary_profile_id=profile.id,
            name="Future Hire Pension",
            benefit_multiplier=Decimal("0.01850"),
            consecutive_high_years=4,
            hire_date=date(2049, 1, 1),
            planned_retirement_date=date(2060, 1, 1),
        )
        db.session.add(pension)
        db.session.commit()

        resp = auth_client.post(
            "/retirement/settings",
            data={"planned_retirement_date": "2048-12-01"},
            headers={"HX-Request": "true"},
        )
        assert resp.status_code == 422
        html = resp.data.decode()
        assert "Must be after hire date." in html
        assert "is-invalid" in html
        db.session.expire_all()
        stored = db.session.get(PensionProfile, pension.id)
        assert stored.planned_retirement_date == date(2060, 1, 1)

    def test_pension_owned_date_save_enforces_earliest_date_rule(
        self, auth_client, seed_user, db, seed_periods_today,
    ):
        """The write-through enforces the earliest-retirement-date rule.

        earliest 2045-01-01: a candidate 2040-01-01 is future and after
        the 2018 hire, but precedes the earliest date -- the shared
        pension validator rejects it at 422 and nothing persists.
        """
        from app.models.pension_profile import PensionProfile

        profile = _create_salary_profile(seed_user, db.session)
        pension = PensionProfile(
            user_id=seed_user["user"].id,
            salary_profile_id=profile.id,
            name="Earliest Bound Pension",
            benefit_multiplier=Decimal("0.01850"),
            consecutive_high_years=4,
            hire_date=date(2018, 7, 1),
            earliest_retirement_date=date(2045, 1, 1),
            planned_retirement_date=date(2048, 7, 1),
        )
        db.session.add(pension)
        db.session.commit()

        resp = auth_client.post(
            "/retirement/settings",
            data={"planned_retirement_date": "2040-01-01"},
            headers={"HX-Request": "true"},
        )
        assert resp.status_code == 422
        html = resp.data.decode()
        assert "Must be on or after earliest retirement date." in html
        db.session.expire_all()
        stored = db.session.get(PensionProfile, pension.id)
        assert stored.planned_retirement_date == date(2048, 7, 1)

    def test_saved_zero_tax_rate_echoes_and_clears_the_flag(
        self, auth_client, seed_user, db, seed_periods_today,
    ):
        """Saving 0% persists Decimal("0.0000") and the panel shows it set.

        L1: 0 / 100 = 0.0000 through the percent pre_load; the refreshed
        panel echoes value="0.0" (0.0000 -> to_percent 0.0 -> "%.1f") and
        the "Not set" flag -- keyed on ``is none`` -- must NOT render.
        """
        resp = auth_client.post(
            "/retirement/settings",
            data={"estimated_retirement_tax_rate": "0"},
            headers={"HX-Request": "true"},
        )
        assert resp.status_code == 200
        html = resp.data.decode()
        assert 'value="0.0"' in html
        assert 'data-assumption-flag="tax-missing"' not in html
        settings = db.session.query(UserSettings).filter_by(
            user_id=seed_user["user"].id,
        ).one()
        # 0 / 100 = 0.0000 (schema percent-to-fraction pre_load).
        assert settings.estimated_retirement_tax_rate == Decimal("0.0000")

    def test_past_retirement_date_is_422(
        self, auth_client, seed_user, db, seed_periods_today,
    ):
        """A past planned retirement date is rejected with a field error.

        M1: mirrors the pension schemas' must-be-future rule.  The
        assumptions fragment renders the error; nothing persists.
        """
        resp = auth_client.post("/retirement/settings", data={
            "planned_retirement_date": "2020-01-01",
        })
        assert resp.status_code == 422
        html = resp.data.decode()
        assert "must be in the future" in html
        assert "is-invalid" in html
        settings = db.session.query(UserSettings).filter_by(
            user_id=seed_user["user"].id,
        ).one()
        assert settings.planned_retirement_date is None

    def test_today_retirement_date_is_422(
        self, auth_client, seed_user, db, seed_periods_today,
    ):
        """Today's date is rejected too (<= today, per the pension rule)."""
        resp = auth_client.post("/retirement/settings", data={
            "planned_retirement_date": date.today().isoformat(),
        })
        assert resp.status_code == 422
        assert "must be in the future" in resp.data.decode()


class TestReadinessFragment:
    """Tests for the /retirement/readiness HTMX what-if fragment (P3a)."""

    def test_redirects_without_htmx(
        self, auth_client, seed_user, db, seed_periods_today,
    ):
        """GET /retirement/readiness without HX-Request redirects."""
        resp = auth_client.get("/retirement/readiness")
        assert resp.status_code == 302
        assert "/retirement" in resp.headers["Location"]

    def test_requires_auth(self, client, db):
        """Unauthenticated -> redirect to login."""
        resp = client.get(
            "/retirement/readiness", headers={"HX-Request": "true"},
        )
        assert resp.status_code == 302
        assert "/login" in resp.headers["Location"]

    def test_baseline_fragment_has_verdict_and_no_deltas(
        self, auth_client, seed_user, db, seed_periods_today,
    ):
        """No overrides: the verdict renders with both OOB siblings.

        Realigned (P3b): the countdown moved from the fragment to the page
        header, and a STANDALONE fragment response carries the income panel as
        an hx-swap-oob sibling so the meter always tracks the verdict.  No
        delta line renders without overrides.

        **The LEVER card is the second sibling since plan step C2-f2d-4**
        (ledger row **P59**), and this assertion inverted with the developer's
        ruling.  It used to read ``'data-lever=' not in html`` -- the levers
        rode along only when a stepper had moved, because they ignored the
        what-if sliders and so had no new answer to give.  They are solved
        against the displayed assumptions now, which means every refresh has a
        new answer and a card left un-refreshed would state a different plan
        from the verdict beside it.
        """
        _seed_underfunded(seed_user, db.session)
        resp = auth_client.get(
            "/retirement/readiness", headers={"HX-Request": "true"},
        )
        assert resp.status_code == 200
        html = resp.data.decode()
        assert 'data-readiness="verdict"' in html
        assert "% funded" in html
        assert 'id="income-panel"' in html
        assert 'hx-swap-oob="innerHTML"' in html
        assert 'data-readiness="deltas"' not in html
        # GRADE THE ONE TOKEN THE FEATURE HANGS ON, as ONE string.  Asserting
        # the id and `hx-swap-oob` separately passes with the attribute
        # deleted -- `#income-panel` satisfies the second half -- and the whole
        # 9,572-test suite was MEASURED green with it removed (adversarial
        # design review, 2026-08-16), which is `lessons.md`'s N-199 on the very
        # same attribute name.  Without it the lever card never updates and
        # C2-f2d-4 does nothing at all in a browser.
        assert '<div id="levers-region" hx-swap-oob="innerHTML">' in html
        assert 'data-lever="contribution"' in html

    def test_the_levers_are_solved_at_the_what_if_the_hero_shows(
        self, app, auth_client, seed_user, db, seed_periods_today,
    ):
        """One response, ONE set of assumptions -- plan step C2-f2d-4, row P59.

        The failure this replaces was visible on screen and captioned nowhere.
        The hero honoured the SWR / return / merit sliders while the lever card
        beside it always solved against the STORED settings, so a page showing
        "65.3% funded" under a 3.5% withdrawal rate told the owner to
        contribute the amount that closes the gap at 4%.  Both figures were
        correct; they were answers to different questions, presented as one
        plan.

        A tighter withdrawal rate makes the plan need MORE, so the solved
        contribution must RISE.  Asserted as a directional inequality against
        the same page's own stored-settings answer rather than against a
        literal, so the case cannot rot into a restatement of the seeded
        scenario's arithmetic.

        The retire-later lever is asserted through its OUTCOME LINES rather
        than through a solved month count, because this fixture is
        deliberately ``not_within_cap`` on that lever (see
        :func:`_seed_underfunded`) and so has no solved delay to compare.  The
        lines render in every state and carry that state's own figures, so a
        lever card that ignored the what-if would return them byte-identical.
        """
        _seed_underfunded(seed_user, db.session)

        def fragment(query):
            """The lever card's solved contribution and its outcome lines."""
            resp = auth_client.get(
                f"/retirement/readiness{query}", headers={"HX-Request": "true"},
            )
            assert resp.status_code == 200
            html = resp.data.decode()
            amount = re.search(
                r"\+\$([\d,]+\.\d\d)<span class=\"retire-lever-unit\"> /paycheck",
                html,
            )
            assert amount, "the lever card did not render its solved default"
            lines = re.search(
                r'<div id="lever-outcomes".*?</div>', html, re.S,
            )
            assert lines, "the lever card did not render its outcome lines"
            return Decimal(amount.group(1).replace(",", "")), lines.group(0)

        stored_amount, stored_lines = fragment("")
        tighter_amount, tighter_lines = fragment("?swr=2")
        assert tighter_amount > stored_amount, (
            f"a 2% withdrawal rate left the solved contribution at "
            f"{tighter_amount} against {stored_amount} at the stored 4% -- the "
            "lever card is not solving against the displayed what-if"
        )
        assert tighter_lines != stored_lines, (
            "both lever outcome lines came back unchanged under a what-if "
            "that moved the verdict, so the card is stating a different plan "
            "from the hero above it"
        )

    def test_a_moved_stepper_keeps_the_owners_value_under_a_what_if(
        self, app, auth_client, seed_user, db, seed_periods_today,
    ):
        """A what-if re-solves the DEFAULT and keeps the entered amount.

        The trap in refreshing the lever card on every slider move: the card
        holds the owner's own stepper entries, so an out-of-band swap that
        re-rendered it from the solved defaults would silently discard what
        they typed.  It does not, because the refresh request carries
        ``.lever-param`` and the producer echoes the submitted amount back --
        so the headline shows the newly solved default while the stepper shows
        the entry, which is the pair a reader needs to compare.
        """
        _seed_underfunded(seed_user, db.session)
        resp = auth_client.get(
            "/retirement/readiness?swr=2&contribution=250.00",
            headers={"HX-Request": "true"},
        )
        assert resp.status_code == 200
        html = resp.data.decode()
        assert 'id="lever-contribution"' in html
        assert 'value="250.00"' in html, (
            "the what-if refresh discarded the owner's stepper entry"
        )
        # And the OUTCOME line states the entered amount, not the solved one.
        assert "+$250.00/paycheck" in html

    def test_swr_override_renders_deltas(
        self, auth_client, seed_user, db, seed_periods_today,
    ):
        """A what-if SWR renders the baseline-vs-override delta line.

        swr=2 (vs the stored 4%) doubles the required target (required =
        gap * 12 / swr), so the funded ratio drops and the delta block
        must render with the "Was ...% funded" baseline and the
        points/dollars changes.
        """
        _seed_underfunded(seed_user, db.session)
        resp = auth_client.get(
            "/retirement/readiness?swr=2", headers={"HX-Request": "true"},
        )
        assert resp.status_code == 200
        html = resp.data.decode()
        assert 'data-readiness="deltas"' in html
        assert "Was " in html
        assert "points" in html

    def test_lever_override_includes_lever_lines(
        self, auth_client, seed_user, db, seed_periods_today,
    ):
        """months=24 additionally renders the lever outcome lines."""
        _seed_underfunded(seed_user, db.session)
        resp = auth_client.get(
            "/retirement/readiness?months=24",
            headers={"HX-Request": "true"},
        )
        assert resp.status_code == 200
        html = resp.data.decode()
        assert 'data-lever="retire-later"' in html
        assert "+24 months" in html
        # L5b (locked anatomy): the outcome line carries the shortfall
        # dollars -- deterministic sign for _seed_underfunded, whose
        # funded ratio stays far below 1 at +24 months.
        assert "shortfall" in html

    def test_contribution_override_renders_full_lever_content(
        self, auth_client, seed_user, db, seed_periods_today,
    ):
        """A contribution stepper value renders both lever lines in full.

        Consolidated from the retired /retirement/levers fragment tests
        (M2): the readiness fragment is now the ONE rendering path for
        _lever_outcomes.html.  contribution=100.00 renders the displayed
        amount via the money macro WITHOUT the over-headroom flag ($100 is
        under the 23500 / 26 = $903.85 per-period limit headroom -- the
        flag applies to the DISPLAYED amount, the documented P2 contract);
        with no months override the retire-later line reports the
        not-within-180-months degenerate state (a $10k account at 7%
        cannot reach a ~$1.4M target).
        """
        _seed_underfunded(seed_user, db.session)
        resp = auth_client.get(
            "/retirement/readiness?contribution=100.00",
            headers={"HX-Request": "true"},
        )
        assert resp.status_code == 200
        html = resp.data.decode()
        assert "+$100.00/paycheck" in html
        assert 'data-lever-flag="headroom"' not in html
        assert "cannot close the gap within 180 months" in html

    def test_solved_contribution_line_renders_headroom_flag(
        self, auth_client, seed_user, db, seed_periods_today,
    ):
        """With no contribution override the SOLVED amount is flagged.

        Consolidated from the retired /retirement/levers fragment tests
        (M2): a months-only request computes the levers with the
        contribution stepper at its solved default (~$1,200+/period for
        _seed_underfunded's ~$1.3M shortfall over an AF of ~1,100), which
        exceeds the 23500 / 26 = $903.85 per-period limit headroom -- the
        honest over-headroom flag renders with the headroom figure, and
        the solved line reports 100.0% funded.
        """
        _seed_underfunded(seed_user, db.session)
        resp = auth_client.get(
            "/retirement/readiness?months=24",
            headers={"HX-Request": "true"},
        )
        assert resp.status_code == 200
        html = resp.data.decode()
        assert "100.0% funded" in html
        assert 'data-lever-flag="headroom"' in html
        assert "$903.85" in html

    def test_funded_baseline_with_months_override_renders_its_outcome(
        self, auth_client, seed_user, db, seed_periods_today,
    ):
        """A typed months override renders ITS outcome when already funded.

        Acceptance fix 3: pre-fix the already_funded branch swallowed the
        override and re-rendered "no delay needed".  With the $5M
        overfunded scenario and months=24, the line must show the
        override's own picture ("+24 months (retiring <date>) reaches
        X% funded (surplus)") -- funded stays >= 100%, so the surplus
        wording is deterministic -- and the baseline copy must be gone.
        """
        _seed_overfunded(seed_user, db.session)
        resp = auth_client.get(
            "/retirement/readiness?months=24",
            headers={"HX-Request": "true"},
        )
        assert resp.status_code == 200
        html = resp.data.decode()
        assert "+24 months" in html
        assert "surplus" in html
        assert "no delay needed" not in html

    def test_funded_baseline_with_contribution_override_renders_its_outcome(
        self, auth_client, seed_user, db, seed_periods_today,
    ):
        """A typed contribution override renders ITS outcome when funded.

        The contribution twin of acceptance fix 3: contribution=100.00
        on the overfunded baseline renders "+$100.00/paycheck ... reaches
        X% funded (surplus)" instead of the swallowed "no extra
        contribution needed" copy.
        """
        _seed_overfunded(seed_user, db.session)
        resp = auth_client.get(
            "/retirement/readiness?contribution=100.00",
            headers={"HX-Request": "true"},
        )
        assert resp.status_code == 200
        html = resp.data.decode()
        assert "+$100.00/paycheck" in html
        assert "no extra contribution needed" not in html

    def test_garbage_params_are_422(
        self, auth_client, seed_user, db, seed_periods_today,
    ):
        """Out-of-bounds what-if params are the assumptions rail at 422.

        Every refusal of this GET is the rail, re-rendered with each message
        on its control and RETARGETED at the rail's region, since plan step
        salary:S3-f-4 (ruling **R-SAL33**; it was a JSON no panel swapped
        in).  The SWR box renders its refusal; the lever steppers are
        controls outside the rail, so theirs render nowhere yet -- the
        response is still the rail, and the rail is clean.
        """
        for query, refused_control in (
            ("swr=-5", "safe_withdrawal_rate"),     # negative percent
            ("months=181", None),                   # above the +180 solver cap
            ("months=abc", None),                   # non-numeric offset
            ("contribution=-1", None),              # negative money
            ("contribution=100001", None),          # above the 100000 bound
            ("contribution=abc", None),             # non-numeric money
        ):
            resp = auth_client.get(
                f"/retirement/readiness?{query}",
                headers={"HX-Request": "true"},
            )
            assert resp.status_code == 422, (
                f"{query}: expected 422, got {resp.status_code}"
            )
            assert resp.headers.get(DESIGNED_FRAGMENT_HEADER) == "1", query
            assert resp.headers.get(RETARGET_HEADER) == "#assumptions-region", query
            html = resp.data.decode()
            assert 'id="assumptions-panel"' in html, query
            if refused_control is None:
                assert "is-invalid" not in html, query
                continue
            control = re.search(
                r'<input[^>]*name="%s"[^>]*>' % refused_control, html,
            ).group(0)
            assert "is-invalid" in control, query


class TestDashboardReadinessContext:
    """P3a: the dashboard passes the direction-D baselines to the template."""

    def test_dashboard_context_carries_readiness_and_levers(
        self, app, auth_client, seed_user, db, seed_periods_today,
    ):
        """The render context gains readiness + levers; the page still 200s.

        The current dashboard.html ignores both keys (zero template edits
        in P3a), so the assertion captures the render context via the
        template_rendered signal instead of scraping HTML.
        """
        from flask import template_rendered

        captured = []

        def record(sender, template, context, **extra):
            if template.name == "retirement/dashboard.html":
                captured.append(context)

        template_rendered.connect(record, app)
        try:
            resp = auth_client.get("/retirement")
            assert resp.status_code == 200
        finally:
            template_rendered.disconnect(record, app)

        assert captured, "retirement/dashboard.html was not rendered"
        context = captured[-1]
        assert "readiness" in context
        assert "levers" in context
        # Shape spot-checks: the producers' signature keys are present.
        assert "funded_ratio" in context["readiness"]
        assert "no_horizon" in context["levers"]


class TestTheRailProbesARaisesEndYear:
    """The rail's end-year pair is a WHAT-IF the readiness GET carries (S3-f-2b).

    Plan step **salary:S3-f-2b** (rulings **R-SAL20**, **R-SAL21**, **R-SAL13**).
    Each recurring-raise row renders the salary form's pair -- a mode select
    and a year input, both ``whatif-param js-whatif-input`` -- so the existing
    debounce fires the readiness GET with them and the plan point resolves
    them against the rows.  The cases post WHAT THE TEMPLATE EMITS
    (``feedback_a_route_test_must_post_what_the_template_emits``): the first
    reads the rendered controls back off the dashboard and submits exactly
    those, which must be the stored plan; the rest vary one control.
    """

    @staticmethod
    def _seed_forever_raise(seed_user, db):
        """An underfunded owner with one recurring 5% January raise, no end year.

        Effective NEXT year so the probe can end it after its first year --
        a smaller salary path, a smaller target, a higher funded ratio.
        """
        _seed_underfunded(seed_user, db.session)
        profile = (
            db.session.query(SalaryProfile)
            .filter_by(user_id=seed_user["user"].id).one()
        )
        effective = display_today().year + 1
        row = make_recurring_raise(
            profile.id, db.session, effective_year=effective,
        )
        db.session.commit()
        return row.id, effective

    @staticmethod
    def _rail_controls(html):
        """Every ``whatif-param`` control the rail's raise rows render: name -> value."""
        rows = re.findall(_RAISE_ROW_MARKUP, html, re.S)
        assert rows, "the dashboard rendered no recurring-raise row"
        controls = {}
        for _raise_id, row in rows:
            select = re.search(
                r'<select[^>]*name="(raise_end_mode_\d+)"[^>]*>(.*?)</select>',
                row, re.S,
            )
            assert select and "whatif-param js-whatif-input" in select.group(0)
            selected = re.search(r'<option value="(\w+)" selected>', select.group(2))
            controls[select.group(1)] = selected.group(1) if selected else ""
            year = re.search(
                r'<input[^>]*name="(raise_end_year_\d+)"[^>]*value="([^"]*)"',
                row, re.S,
            )
            assert year and "whatif-param js-whatif-input" in year.group(0)
            controls[year.group(1)] = year.group(2)
        return controls

    def _fragment(self, auth_client, params):
        """GET the readiness fragment with *params*; return ``(status, html)``."""
        resp = auth_client.get(
            "/retirement/readiness", query_string=params,
            headers={"HX-Request": "true"},
        )
        return resp.status_code, resp.data.decode()

    def _refused(self, auth_client, params):
        """GET with *params* expecting the rail at 422; return its html.

        The refusal's shape since plan step salary:S3-f-4 (ruling
        **R-SAL33**): the designed-fragment marker, htmx's retarget at the
        rail's region (this request targets the readiness CARD), and the
        rail itself as the body.
        """
        resp = auth_client.get(
            "/retirement/readiness", query_string=params,
            headers={"HX-Request": "true"},
        )
        assert resp.status_code == 422
        assert resp.headers.get(DESIGNED_FRAGMENT_HEADER) == "1"
        assert resp.headers.get(RETARGET_HEADER) == "#assumptions-region"
        html = resp.data.decode()
        assert 'id="assumptions-panel"' in html
        return html

    @staticmethod
    def _rows(html):
        """Every rendered raise row, keyed by its id as a string."""
        return dict(re.findall(_RAISE_ROW_MARKUP, html, re.S))

    def test_what_the_rail_emits_is_the_stored_plan(
        self, auth_client, seed_user, db, seed_periods_today,
    ):
        """The pre-filled controls, submitted verbatim, resolve to no what-if.

        Every rail row submits on every refresh, so a point that treated the
        pre-filled pair as an override would derive one plan twice and state a
        zero delta (row P57's shape).  The controls are read back off the
        rendered page rather than hand-written, so this grades the pair the
        browser actually sends.
        """
        raise_id, _ = self._seed_forever_raise(seed_user, db)
        html = auth_client.get("/retirement").data.decode()
        controls = self._rail_controls(html)
        assert controls == {
            f"raise_end_mode_{raise_id}": "none",
            f"raise_end_year_{raise_id}": "",
        }

        status, fragment = self._fragment(auth_client, controls)
        assert status == 200
        assert 'data-readiness="verdict"' in fragment
        assert 'data-readiness="deltas"' not in fragment, (
            "the rail's own pre-filled pair was read as a what-if"
        )

    def test_a_probed_end_year_moves_the_verdict_and_the_levers(
        self, auth_client, seed_user, db, seed_periods_today,
    ):
        """Ending the raise after its first year is a what-if with a delta.

        A shorter raise is a smaller final salary, so the income target and
        the required savings FALL and the funded ratio RISES: the delta line
        renders with a positive points change, and the lever card -- solved
        against the displayed what-if since plan step C2-f2d-4 -- comes back
        different from the stored plan's.
        """
        raise_id, effective = self._seed_forever_raise(seed_user, db)
        _, stored = self._fragment(auth_client, {})
        status, probed = self._fragment(auth_client, {
            f"raise_end_mode_{raise_id}": "year",
            f"raise_end_year_{raise_id}": str(effective),
        })
        assert status == 200
        assert 'data-readiness="deltas"' in probed
        points = re.search(r"Funded change: (-?[\d.]+) points", probed)
        assert points, "the delta line did not state the funded change"
        assert Decimal(points.group(1)) > 0, (
            "ending the raise early did not raise the funded ratio, so the "
            "probe did not reach the income target"
        )
        stored_lines = re.search(r'<div id="lever-outcomes".*?</div>', stored, re.S)
        probed_lines = re.search(r'<div id="lever-outcomes".*?</div>', probed, re.S)
        assert stored_lines and probed_lines
        assert stored_lines.group(0) != probed_lines.group(0), (
            "the lever card was solved at the stored raise set beside a "
            "verdict stated at the probed one"
        )

    def test_a_probe_on_a_raise_that_does_not_exist_is_refused(
        self, auth_client, seed_user, db, seed_periods_today,
    ):
        """A stale bookmark or a URL edit is a designed 422, not a silent no-op.

        The refusal is keyed to the stale id's mode control, which no rendered
        row carries: the rail comes back as the rows ARE -- the stale row
        gone, the live row clean -- and the sentence itself renders nowhere.
        A rail-level notice for a refusal that names no row is a known
        opening, not built here.
        """
        raise_id, _ = self._seed_forever_raise(seed_user, db)
        stale = raise_id + 999
        html = self._refused(auth_client, {
            f"raise_end_mode_{stale}": "none",
            f"raise_end_year_{stale}": "",
        })
        rows = self._rows(html)
        assert set(rows) == {str(raise_id)}
        assert "is-invalid" not in rows[str(raise_id)]
        assert "Not one of your recurring raises" not in html

    def test_a_probe_on_another_owners_raise_is_refused_and_discloses_nothing(
        self, auth_client, seed_user, seed_second_user, db, seed_periods_today,
    ):
        """Another owner's raise id is refused EXACTLY as a dead id is.

        The IDOR axis the dead-id case above cannot measure (an adversarial
        review of this step).  The probe is resolved against THIS owner's rows
        -- never by querying the submitted id -- so a foreign id has no row to
        be graded against, and the refusal is the constant not-found sentence.
        **The probe names a year the rule would ACCEPT for the foreign row**
        (a second adversarial review, at plan step S3-f-4, when the answer
        became the rail): a resolver that looked the row up by id would find
        it, accept the year and answer 200 with the card, so the 422 is the
        assertion that carries the control.  A year BEFORE the foreign
        effective year -- the case as first written -- is refused by such a
        resolver too, with "takes effect in <year>" keyed to a control no row
        renders, and every assertion on the rail passed with the IDOR in
        place; the JSON body this case read until S3-f-4 was what caught it.
        """
        self._seed_forever_raise(seed_user, db)
        foreign_profile = _create_salary_profile(seed_second_user, db.session)
        foreign_effective = display_today().year + 7
        foreign = make_recurring_raise(
            foreign_profile.id, db.session, effective_year=foreign_effective,
        )
        db.session.commit()

        html = self._refused(auth_client, {
            f"raise_end_mode_{foreign.id}": "year",
            f"raise_end_year_{foreign.id}": str(foreign_effective + 1),
        })
        assert str(foreign.id) not in self._rows(html), (
            "the rail rendered a row for another owner's raise"
        )
        # No row renders the foreign id and the refusal is keyed to its
        # control, so no message renders at all; a message here would be a
        # fact about the row.  (Checked on the messages rather than on the
        # whole body: the csrf tokens the rows carry are random text.)
        assert "invalid-feedback" not in html

    def test_a_year_before_the_raises_effective_year_is_refused(
        self, auth_client, seed_user, db, seed_periods_today,
    ):
        """The ONE end-year rule, applied against the ROW, answers the rail too.

        The salary form refuses this payload through ``RaiseCreateSchema``; the
        rail refuses it through ``plan_with`` with the same sentence, because
        both call ``salary_raises.end_year_of``.
        """
        raise_id, effective = self._seed_forever_raise(seed_user, db)
        html = self._refused(auth_client, {
            f"raise_end_mode_{raise_id}": "year",
            f"raise_end_year_{raise_id}": str(effective - 1),
        })
        row = self._rows(html)[str(raise_id)]
        assert (
            f"A raise cannot end before it starts: it takes effect in {effective}."
            in row
        )
        year_input = re.search(
            r'<input[^>]*name="raise_end_year_%d"[^>]*>' % raise_id, row, re.S,
        ).group(0)
        assert "is-invalid" in year_input
        assert f'value="{effective - 1}"' in year_input, "the refused year was not echoed"
        assert '<option value="year" selected>' in row, "the submitted mode was not echoed"

    def test_a_mode_outside_the_vocabulary_is_refused_by_the_schema(
        self, auth_client, seed_user, db, seed_periods_today,
    ):
        """The field-tier half of the rule stays the schema's, as on the form.

        marshmallow's nested report is re-keyed onto the row's mode control,
        exactly as the Save re-keys it -- the readiness GET no longer emits
        that nested shape (ledger row SAL-548's second 422 shape).
        """
        raise_id, _ = self._seed_forever_raise(seed_user, db)
        html = self._refused(auth_client, {
            f"raise_end_mode_{raise_id}": "forever",
        })
        row = self._rows(html)[str(raise_id)]
        select = re.search(r"<select[^>]*>", row).group(0)
        assert "is-invalid" in select
        assert "Must be one of: year, none." in row
        assert "raise_probes" not in html

    def test_a_refused_probe_echoes_the_swr_what_if_and_every_other_row(
        self, auth_client, seed_user, db, seed_periods_today,
    ):
        """The rail comes back stating what is ON SCREEN, not the stored plan.

        Every rail what-if rides on every refresh, so the refusal echoes the
        query string: the other raise row's pair, and the SWR what-if -- which
        arrives under the GET's name ``swr`` and must land in the SWR box AND
        its mirror, or the next refresh would carry the stored rate under a
        box stating 3.5.  The date row, which no refresh carries, states its
        stored value.
        """
        raise_id, effective = self._seed_forever_raise(seed_user, db)
        profile = (
            db.session.query(SalaryProfile)
            .filter_by(user_id=seed_user["user"].id).one()
        )
        other = make_recurring_raise(
            profile.id, db.session, effective_year=effective + 1,
            terminal_year=effective + 4,
        )
        settings = db.session.query(UserSettings).filter_by(
            user_id=seed_user["user"].id,
        ).one()
        settings.safe_withdrawal_rate = Decimal("0.0400")
        planned = settings.planned_retirement_date
        db.session.commit()
        other_id = other.id

        # The other row's probe DIFFERS from its stored year, so a rail that
        # stated the stored value instead of the echo would fail here (an
        # adversarial review: probing the stored year measured nothing).
        html = self._refused(auth_client, {
            "swr": "3.5",
            f"raise_end_mode_{raise_id}": "year",
            f"raise_end_year_{raise_id}": str(effective - 1),
            f"raise_end_mode_{other_id}": "year",
            f"raise_end_year_{other_id}": str(effective + 3),
        })
        rows = self._rows(html)
        assert "is-invalid" in rows[str(raise_id)]
        other_row = rows[str(other_id)]
        assert "is-invalid" not in other_row
        assert '<option value="year" selected>' in other_row
        assert f'value="{effective + 3}"' in other_row, "the other row's what-if was not echoed"
        assert f'value="{effective + 4}"' not in other_row
        swr_box = re.search(r'<input[^>]*name="safe_withdrawal_rate"[^>]*>', html).group(0)
        assert 'value="3.5"' in swr_box, "the SWR what-if was reset to the stored rate"
        assert "is-invalid" not in swr_box
        mirror = re.search(r'<input[^>]*id="swr-whatif-mirror"[^>]*>', html).group(0)
        assert 'value="3.5"' in mirror, "the mirror lost the what-if the box states"
        date_input = re.search(r'<input[^>]*name="planned_retirement_date"[^>]*>', html).group(0)
        assert f'value="{planned.isoformat()}"' in date_input

    def test_no_swr_what_if_leaves_the_box_on_its_stored_rate(
        self, auth_client, seed_user, db, seed_periods_today,
    ):
        """An untouched mirror submits ``swr=`` -- no what-if -- and echoes nothing.

        The mirror starts empty and every refresh carries it, so the empty
        string is the ordinary case; a rail that echoed it would blank the
        SWR box on every refused probe.
        """
        raise_id, effective = self._seed_forever_raise(seed_user, db)
        settings = db.session.query(UserSettings).filter_by(
            user_id=seed_user["user"].id,
        ).one()
        settings.safe_withdrawal_rate = Decimal("0.0350")
        db.session.commit()

        html = self._refused(auth_client, {
            "swr": "",
            f"raise_end_mode_{raise_id}": "year",
            f"raise_end_year_{raise_id}": str(effective - 1),
        })
        swr_box = re.search(r'<input[^>]*name="safe_withdrawal_rate"[^>]*>', html).group(0)
        assert 'value="3.50"' in swr_box
        mirror = re.search(r'<input[^>]*id="swr-whatif-mirror"[^>]*>', html).group(0)
        assert 'value=""' in mirror

    def test_an_swr_what_if_outside_its_range_is_refused_on_its_box(
        self, auth_client, seed_user, db, seed_periods_today,
    ):
        """The SWR box's refusal renders on the box, under the GET's own name.

        The box is one control with two names -- ``safe_withdrawal_rate`` on
        its Save, ``swr`` on the what-if -- and the browser's ``max`` fences
        only a form submit, not the htmx refresh, so 150 reaches the schema.
        Until plan step salary:S3-f-4 the answer was a JSON the page never
        rendered.
        """
        raise_id, _ = self._seed_forever_raise(seed_user, db)
        html = self._refused(auth_client, {
            "swr": "150",
            f"raise_end_mode_{raise_id}": "none",
            f"raise_end_year_{raise_id}": "",
        })
        swr_box = re.search(r'<input[^>]*name="safe_withdrawal_rate"[^>]*>', html).group(0)
        assert "is-invalid" in swr_box
        assert 'value="150"' in swr_box
        feedback = re.search(
            r'name="safe_withdrawal_rate".*?<div class="invalid-feedback">(.*?)</div>',
            html, re.S,
        )
        assert feedback and "Must be" in feedback.group(1)
        assert "is-invalid" not in self._rows(html)[str(raise_id)]

    def test_a_lever_refusal_beside_a_bad_year_is_the_rail_with_the_row_unasked(
        self, auth_client, seed_user, db, seed_periods_today,
    ):
        """One answer for every refusal, and the tiers are asked in order.

        A stepper outside its range is the SCHEMA's refusal, which answers
        before the rows are consulted -- the ONE end-year rule is asked only
        of a payload the schema accepted, as on the Save -- so the year row
        comes back unmarked, its pair echoed.  The steppers are controls
        outside the rail, so the refusal itself renders nowhere yet; the
        answer is still the rail, not a JSON.
        """
        raise_id, effective = self._seed_forever_raise(seed_user, db)
        html = self._refused(auth_client, {
            "months": "181",
            f"raise_end_mode_{raise_id}": "year",
            f"raise_end_year_{raise_id}": str(effective - 1),
        })
        row = self._rows(html)[str(raise_id)]
        assert "is-invalid" not in row
        assert f'value="{effective - 1}"' in row
        assert "invalid-feedback" not in html

    def test_the_refusal_is_the_saves_own_body(
        self, auth_client, seed_user, db, seed_periods_today,
    ):
        """ONE 422 shape: the what-if's refusal IS the Save's, retargeted.

        The same pair refused by the same rule through both doors renders the
        same rail, byte for byte once the per-render csrf tokens are set
        aside; the Save's answer is built for its form's target and the GET's
        carries the retarget that points it there.
        """
        raise_id, effective = self._seed_forever_raise(seed_user, db)
        pair = {
            f"raise_end_mode_{raise_id}": "year",
            f"raise_end_year_{raise_id}": str(effective - 1),
        }
        probed = auth_client.get(
            "/retirement/readiness", query_string=pair,
            headers={"HX-Request": "true"},
        )
        saved = auth_client.post(
            "/retirement/settings", data=pair, headers={"HX-Request": "true"},
        )
        assert probed.status_code == saved.status_code == 422
        assert probed.headers.get(DESIGNED_FRAGMENT_HEADER) == "1"
        assert saved.headers.get(DESIGNED_FRAGMENT_HEADER) == "1"
        assert probed.headers.get(RETARGET_HEADER) == "#assumptions-region"
        assert RETARGET_HEADER not in saved.headers

        def without_tokens(html):
            return re.sub(r'name="csrf_token" value="[^"]*"', 'name="csrf_token"', html)

        assert without_tokens(probed.data.decode()) == without_tokens(saved.data.decode())
        assert f"it takes effect in {effective}." in probed.data.decode()


class TestTheRailRegionIsSpelledOnce:
    """The rail's region and the refresh trigger are named in four files.

    ``dashboard.html`` renders ``#assumptions-region`` and the
    ``#readiness-refresh`` trigger; every rail Save form targets the region;
    ``retirement_controls.js`` watches the region for the post-save reload
    and aborts the trigger's request on a keystroke; and the readiness GET
    retargets its refusal at the region (plan step salary:S3-f-4, ruling
    **R-SAL33**).  No pytest renders htmx, so a rename in one file passes
    every route test and breaks the page: this is the gate an adversarial
    review of that step asked for, in the shape of
    ``test_header_name_matches_js_listener``.
    """

    @staticmethod
    def _source(relative):
        return (Path(__file__).resolve().parents[2] / relative).read_text()

    def test_the_rail_region_selector_matches_every_spelling(self):
        """The route's selector is the dashboard's id, the forms' target, the JS's guard."""
        # pylint: disable=import-outside-toplevel
        from app.routes.retirement import _RAIL_REGION

        assert _RAIL_REGION == "#assumptions-region"
        dashboard = self._source("app/templates/retirement/dashboard.html")
        assert 'id="assumptions-region"' in dashboard
        rail = self._source("app/templates/retirement/_assumptions.html")
        targets = re.findall(r'hx-target="([^"]+)"', rail)
        assert targets and set(targets) == {_RAIL_REGION}, targets
        controls = self._source("app/static/js/retirement_controls.js")
        assert 'event.target.id !== "assumptions-region"' in controls

    def test_the_refresh_trigger_id_matches_the_abort_handle(self):
        """The JS aborts the request of the element the dashboard renders."""
        dashboard = self._source("app/templates/retirement/dashboard.html")
        controls = self._source("app/static/js/retirement_controls.js")
        assert 'id="readiness-refresh"' in dashboard
        assert 'hx-sync="this:replace"' in dashboard
        assert 'document.getElementById("readiness-refresh")' in controls


class TestTheRailSavesARaisesEndYear:
    """The rail's end-year pair has a Save that writes the ROW (S3-f-3).

    Plan step **salary:S3-f-3** (rulings **R-SAL22** and **R-SAL24**).  Each
    recurring-raise row is a form posting the SAME two controls its what-if
    sends to ``retirement.update_settings``, which resolves them against the
    rows the rail lists, applies the ONE end-year rule, writes
    ``salary.salary_raises.terminal_year`` and runs the regeneration every
    salary write runs.  The cases post WHAT THE TEMPLATE EMITS
    (``feedback_a_route_test_must_post_what_the_template_emits``): the first
    reads the rendered row back and submits it verbatim; the rest vary one
    control.
    """

    @staticmethod
    def _seed_saveable_raise(
        seed_user, db, periods, *, terminal_year=None, effective_month=1,
    ):
        """An owner with a template-backed profile and one recurring 5% raise.

        Effective the year BEFORE the current payday's (R-SAL21's fixture),
        in *effective_month*.  **It is priced only where it lands AFTER the
        profile's one pay entry** (plan step salary:X-av-3a, ruling R-SAL59:
        a raise landing on or before the entry is already in it), and the
        entry sits on the first of *periods*.  So believed forever it has
        applied twice by the current payday, and believed through its first
        year only once -- the case in which a Save that ends it after its
        first year moves TODAY's paycheck -- only where the caller's calendar
        opens before its first landing; see
        :meth:`test_an_end_year_is_written_and_the_paychecks_regenerated`.
        Every other caller grades the row's write, which the pricing does
        not touch.

        Returns:
            ``(raise id, effective year, profile id)``.
        """
        current_payday = max(
            period.start_date for period in periods
            if period.start_date <= display_today()
        )
        _seed_underfunded(seed_user, db.session)
        profile = (
            db.session.query(SalaryProfile)
            .filter_by(user_id=seed_user["user"].id).one()
        )
        effective = current_payday.year - 1
        row = make_recurring_raise(
            profile.id, db.session, effective_year=effective,
            effective_month=effective_month, terminal_year=terminal_year,
        )
        db.session.commit()
        return row.id, effective, profile.id

    @staticmethod
    def _save(auth_client, raise_id, mode, year, *, htmx=True):
        """POST one row's pair as its form emits it; return the response."""
        return auth_client.post(
            "/retirement/settings",
            data={
                f"raise_end_mode_{raise_id}": mode,
                f"raise_end_year_{raise_id}": year,
            },
            headers={"HX-Request": "true"} if htmx else {},
        )

    @staticmethod
    def _stored(db, raise_id):
        """The row's ``(terminal_year, version_id)`` as the database holds it."""
        db.session.expire_all()
        row = db.session.get(SalaryRaise, raise_id)
        return row.terminal_year, row.version_id

    @staticmethod
    def _row(html, raise_id):
        """The rendered row for *raise_id*, or fail."""
        rows = dict(re.findall(_RAISE_ROW_MARKUP, html, re.S))
        assert str(raise_id) in rows, f"no rendered row for raise {raise_id}"
        return rows[str(raise_id)]

    def test_what_the_rail_emits_saved_back_writes_nothing(
        self, auth_client, seed_user, db, seed_periods_today,
    ):
        """The pre-filled pair, submitted verbatim, is a no-op on the row.

        A rail row carries its stored answer, so a Save nobody changed must
        leave the row where it was: the same ``terminal_year`` and -- the
        claim the year alone cannot grade -- the same optimistic-lock counter,
        so no UPDATE was issued against the raise.
        """
        raise_id, _, _ = self._seed_saveable_raise(seed_user, db, seed_periods_today)
        before = self._stored(db, raise_id)
        html = auth_client.get("/retirement").data.decode()
        controls = TestTheRailProbesARaisesEndYear._rail_controls(html)
        assert controls == {
            f"raise_end_mode_{raise_id}": "none",
            f"raise_end_year_{raise_id}": "",
        }

        resp = auth_client.post(
            "/retirement/settings", data=controls,
            headers={"HX-Request": "true"},
        )

        assert resp.status_code == 200
        assert 'id="assumptions-panel"' in resp.data.decode()
        assert self._stored(db, raise_id) == before

    def test_an_end_year_is_written_and_the_paychecks_regenerated(
        self, auth_client, seed_user, db, seed_periods_52, monkeypatch,
    ):
        """Ending the raise after its first year writes the column AND re-prices.

        The write is the row's ``terminal_year``.  The re-price is TODAY's
        paycheck as a fresh pass prices it after each Save, the figure the
        Recurring and savings pages show
        (``obligations_aggregator.todays_price``): the grid derives each
        paycheck row from the same rows this door wrote, so the two cannot
        disagree.  Ending the raise a year earlier is the smaller figure
        (``$3,230.77`` against ``$3,392.31`` gross), which the second Save
        shows.  Its observable was the template's ``default_amount``, which
        the regeneration re-stated at today's net, until plan step X-bp
        deleted the re-statement; the regeneration's call is graded by
        :meth:`test_two_rows_posted_together_both_write` and
        :meth:`test_the_saved_raises_own_profile_is_regenerated_and_no_other`.

        **Built on fixed days, so it holds in any month** (plan step
        salary:X-av-3a).  The one pay entry sits on the first payday,
        2026-01-02, at $3,076.92 ($80,000.00 / 26), and a raise is priced
        only where it lands AFTER the entry (ruling R-SAL59), so both
        landings must follow the entry and precede the payday priced.  No
        calendar built from the wall clock holds that: ``seed_periods_today``
        spans 20 weeks, and a yearly raise lands twice in no fewer than 12
        months.  So the calendar is ``seed_periods_52`` (paydays 2026-01-02
        through 2027-12-17), today is 2027-03-20 (the 2027-03-12 payday's
        period, so the effective year is 2026), and the raise moves to
        February -- the first month after the entry's payday -- landing
        2026-02-01 and 2027-02-01.  Each step rounds to the cent (R-SAL60):
        believed forever, 3,076.92 x 1.05 = 3,230.766 -> 3,230.77, then
        x 1.05 = 3,392.3085 -> 3,392.31; ended after 2026, 3,230.77.
        """
        # Pin both clocks to one civil day: the calendar below is fixed, so
        # "today" must be its 2027-03-12 payday's period, the four UTC hours
        # after a New York midnight included.
        freeze_today(monkeypatch, date(2027, 3, 20))
        # Sign in again on the moved date: ``auth_client`` signed in on the
        # real clock, and the idle timeout measures against the clock this
        # freeze moves, so on any real date more than the idle window away
        # the old session is expired here, as a real one would be.
        log_in_seed_user(auth_client)
        raise_id, effective, profile_id = self._seed_saveable_raise(
            seed_user, db, seed_periods_52, effective_month=2,
        )

        def _todays_paycheck():
            """Today's paycheck, priced by a fresh pass over what was saved."""
            db.session.expire_all()
            ctx = BalanceContext.build(seed_user["user"].id)
            return ctx.paychecks().for_profile(
                db.session.get(SalaryProfile, profile_id),
            ).at(ctx.calendar().period_containing(display_today()))

        forever = self._save(auth_client, raise_id, "none", "")
        assert forever.status_code == 200
        priced_forever = _todays_paycheck()
        assert priced_forever.earnings.gross_biweekly == Decimal("3392.31")

        ended = self._save(auth_client, raise_id, "year", str(effective))
        assert ended.status_code == 200
        assert self._stored(db, raise_id)[0] == effective
        priced_ended = _todays_paycheck()
        assert priced_ended.earnings.gross_biweekly == Decimal("3230.77")
        assert priced_ended.earnings.net_pay < priced_forever.earnings.net_pay, (
            "a raise believed one year less did not lower today's paycheck"
        )

        html = ended.data.decode()
        row = self._row(html, raise_id)
        assert '<option value="year" selected>' in row
        assert f'value="{effective}"' in row

    def test_no_end_year_with_a_stale_year_in_the_box_writes_null(
        self, auth_client, seed_user, db, seed_periods_today,
    ):
        """The mode is authoritative (R-SAL13): "no end year" clears the column.

        A browser fills the year box from the stored value, so choosing "no
        end year" on a raise that ends in three years submits that year
        beside the mode -- the pair the salary form's own test posts for the
        same reason.
        """
        current_payday = max(
            period.start_date for period in seed_periods_today
            if period.start_date <= display_today()
        )
        ends = current_payday.year - 1 + 3
        raise_id, _, _ = self._seed_saveable_raise(
            seed_user, db, seed_periods_today, terminal_year=ends,
        )
        assert self._stored(db, raise_id)[0] == ends

        resp = self._save(auth_client, raise_id, "none", str(ends))

        assert resp.status_code == 200
        assert self._stored(db, raise_id)[0] is None
        row = self._row(resp.data.decode(), raise_id)
        assert '<option value="none" selected>' in row
        year_input = re.search(
            r'<input[^>]*name="raise_end_year_%d"[^>]*>' % raise_id, row, re.S,
        ).group(0)
        assert 'value=""' in year_input, "the cleared row still shows the old year"

    def test_a_year_before_the_effective_year_is_refused_on_the_row(
        self, auth_client, seed_user, db, seed_periods_today,
    ):
        """The ONE end-year rule refuses, and the refusal renders on the row.

        The same sentence the salary form and the probe give, because all
        three call ``salary_raises.end_year_of``; here it is a designed 422
        fragment with the year control marked invalid and the submitted pair
        echoed on THIS row, and nothing written.
        """
        raise_id, effective, _ = self._seed_saveable_raise(seed_user, db, seed_periods_today)

        resp = self._save(auth_client, raise_id, "year", str(effective - 1))

        assert resp.status_code == 422
        assert resp.headers.get(DESIGNED_FRAGMENT_HEADER) == "1"
        html = resp.data.decode()
        assert 'id="assumptions-panel"' in html
        row = self._row(html, raise_id)
        assert (
            f"A raise cannot end before it starts: it takes effect in {effective}."
            in row
        )
        year_input = re.search(
            r'<input[^>]*name="raise_end_year_%d"[^>]*>' % raise_id, row, re.S,
        ).group(0)
        assert "is-invalid" in year_input
        assert f'value="{effective - 1}"' in year_input, "the refused year was not echoed"
        assert '<option value="year" selected>' in row, "the submitted mode was not echoed"
        assert self._stored(db, raise_id)[0] is None

    def test_a_mode_outside_the_vocabulary_is_refused_on_the_row(
        self, auth_client, seed_user, db, seed_periods_today,
    ):
        """The field-tier half of the rule is the schema's, keyed to the control.

        The schema reports the pair's refusal in marshmallow's nested shape;
        the route re-keys it onto ``raise_end_mode_<id>`` so the fragment
        renders it where the rail renders every other field error.
        """
        raise_id, _, _ = self._seed_saveable_raise(seed_user, db, seed_periods_today)

        resp = self._save(auth_client, raise_id, "forever", "")

        assert resp.status_code == 422
        assert resp.headers.get(DESIGNED_FRAGMENT_HEADER) == "1"
        row = self._row(resp.data.decode(), raise_id)
        select = re.search(r"<select[^>]*>", row).group(0)
        assert "is-invalid" in select
        assert "Must be one of: year, none." in row
        assert self._stored(db, raise_id)[0] is None

    def test_a_refusal_on_one_row_leaves_every_other_row_stating_its_value(
        self, auth_client, seed_user, db, seed_periods_today,
    ):
        """The echo is per row: a 422 for one raise must not blank another.

        Two recurring raises, one Save refused; the untouched row still
        pre-selects its stored mode and fills its stored year.
        """
        raise_id, effective, profile_id = self._seed_saveable_raise(
            seed_user, db, seed_periods_today,
        )
        other = make_recurring_raise(
            profile_id, db.session, effective_year=effective + 1,
            terminal_year=effective + 4,
        )
        db.session.commit()
        other_id = other.id

        settings = db.session.query(UserSettings).filter_by(
            user_id=seed_user["user"].id,
        ).one()
        settings.safe_withdrawal_rate = Decimal("0.0350")
        planned = settings.planned_retirement_date
        db.session.commit()

        resp = self._save(auth_client, raise_id, "year", str(effective - 1))

        assert resp.status_code == 422
        html = resp.data.decode()
        other_row = self._row(html, other_id)
        assert '<option value="year" selected>' in other_row
        assert f'value="{effective + 4}"' in other_row
        assert "is-invalid" not in other_row
        # The older rows too: a rail 422 used to render them EMPTY (they echoed
        # ``fd.get(name, '')`` whatever form was refused), and an emptied date
        # row then Saved cleared the pension's planned date.
        swr = re.search(r'<input[^>]*name="safe_withdrawal_rate"[^>]*>', html).group(0)
        assert 'value="3.50"' in swr, "the SWR row lost its stored value on another row's 422"
        date_input = re.search(r'<input[^>]*name="planned_retirement_date"[^>]*>', html).group(0)
        assert f'value="{planned.isoformat()}"' in date_input, (
            "the date row lost its stored value on another row's 422"
        )

    def test_another_owners_raise_id_is_404_and_writes_nothing(
        self, auth_client, seed_user, seed_second_user, db, seed_periods_today, caplog,
    ):
        """Not yours is not found, as everywhere; the refusal leaves a trail.

        The pair is resolved against THIS owner's rail rows -- never by
        querying the submitted id -- so a foreign id has no row to be graded
        against and is the constant 404; ``log_refused_lookup`` records it at
        the one severity the ambiguity supports.
        """
        self._seed_saveable_raise(seed_user, db, seed_periods_today)
        foreign_profile = _create_salary_profile(seed_second_user, db.session)
        foreign = make_recurring_raise(
            foreign_profile.id, db.session, effective_year=display_today().year + 2,
        )
        db.session.commit()
        foreign_id = foreign.id

        with caplog.at_level(logging.INFO, logger="app.utils.auth_helpers"):
            resp = self._save(auth_client, foreign_id, "year", "2099")

        assert resp.status_code == 404
        assert self._stored(db, foreign_id)[0] is None
        assert any(
            "Owner-scoped lookup found no such record" in record.getMessage()
            for record in caplog.records
        ), "the refused id left no audit trail"

    def test_a_dead_id_and_a_one_time_raise_are_404(
        self, auth_client, seed_user, db, seed_periods_today,
    ):
        """Only a row the rail LISTS can be saved: recurring, on an active profile.

        A one-time raise of the owner's own is not a rail row (it has no end
        year to answer, and the column forbids it one), so a pair naming it
        is refused exactly as a dead id is.
        """
        raise_id, effective, profile_id = self._seed_saveable_raise(
            seed_user, db, seed_periods_today,
        )
        one_time = SalaryRaise(
            salary_profile_id=profile_id,
            raise_type_id=ref_cache.raise_type_id(RaiseTypeEnum.MERIT),
            effective_month=6, effective_year=effective + 1,
            percentage=Decimal("0.0200"), is_recurring=False,
        )
        db.session.add(one_time)
        db.session.commit()
        one_time_id = one_time.id

        assert self._save(auth_client, raise_id + 999, "none", "").status_code == 404
        assert self._save(auth_client, one_time_id, "year", str(effective + 1)).status_code == 404
        assert self._stored(db, one_time_id)[0] is None

    def test_an_archived_profiles_raise_is_not_a_rail_row(
        self, auth_client, seed_user, db, seed_periods_today,
    ):
        """The rail lists ACTIVE profiles' raises; an archived one's is a 404."""
        raise_id, effective, profile_id = self._seed_saveable_raise(
            seed_user, db, seed_periods_today,
        )
        db.session.get(SalaryProfile, profile_id).is_active = False
        db.session.commit()

        resp = self._save(auth_client, raise_id, "year", str(effective + 2))

        assert resp.status_code == 404
        assert self._stored(db, raise_id)[0] is None

    def test_an_id_that_is_not_an_id_is_refused_by_the_schema(
        self, auth_client, seed_user, db, seed_periods_today,
    ):
        """``raise_end_mode_abc`` is a refused KEY, keyed to a control, never a 500.

        ``RowId`` refuses the key; marshmallow reports it under ``{"key":
        [...]}`` and the route re-keys that onto the mode control of the id
        as submitted, so the arm has a home even though no row renders it.
        """
        raise_id, _, _ = self._seed_saveable_raise(seed_user, db, seed_periods_today)

        resp = auth_client.post(
            "/retirement/settings",
            data={"raise_end_mode_abc": "none", "raise_end_year_abc": ""},
            headers={"HX-Request": "true"},
        )

        assert resp.status_code == 422
        assert resp.headers.get(DESIGNED_FRAGMENT_HEADER) == "1"
        assert 'id="assumptions-panel"' in resp.data.decode()
        assert self._stored(db, raise_id)[0] is None

    def test_a_concurrent_edit_is_a_409_conflict_on_the_row(
        self, auth_client, seed_user, db, seed_periods_today, monkeypatch,
    ):
        """A stale race rolls back and re-renders the rail fresh at 409.

        The regeneration flushes under the optimistic lock (the raise, its
        template, its rows), so a concurrent edit surfaces there as
        ``StaleDataError``; the route answers the designed-conflict shape
        every fragment route uses -- the fragment, the message on the row,
        409 so htmx swaps it and the post-save reload does not fire -- and
        the write is rolled back.
        """
        # Pylint: import-outside-toplevel -- the route module and the
        # exception are needed only to simulate the race, as the salary
        # package's own stale-race case does.
        from app.routes import retirement as retirement_mod  # pylint: disable=import-outside-toplevel
        from sqlalchemy.orm.exc import StaleDataError  # pylint: disable=import-outside-toplevel

        raise_id, effective, _ = self._seed_saveable_raise(seed_user, db, seed_periods_today)

        def _raise_stale(_ctx, _profile):
            raise StaleDataError("simulated concurrent edit")

        monkeypatch.setattr(
            retirement_mod.salary_regeneration,
            "regenerate_salary_transactions", _raise_stale,
        )

        resp = self._save(auth_client, raise_id, "year", str(effective + 2))

        assert resp.status_code == 409
        html = resp.data.decode()
        assert 'id="assumptions-panel"' in html
        row = self._row(html, raise_id)
        assert "This raise was changed by another action while you were editing." in row
        assert "is-invalid" in re.search(r"<select[^>]*>", row).group(0)
        assert self._stored(db, raise_id)[0] is None, "the write survived the rollback"
        assert '<option value="none" selected>' in row, (
            "the conflict re-render did not state the FRESH row"
        )

    def test_a_database_failure_is_a_designed_500_on_the_row(
        self, auth_client, seed_user, db, seed_periods_today, monkeypatch, caplog,
    ):
        """The rail's twin of the salary package's ``handle_db_error`` arm.

        The service re-raises any non-stale ``SQLAlchemyError`` so the
        route's own handler reports it; an unhandled 500 here is a click
        that did nothing and said nothing, so the route rolls back and
        answers the fragment at 500 with the message on the row, the
        designed-fragment header set so htmx swaps it.
        """
        # Pylint: import-outside-toplevel -- the route module and the error
        # class are needed only to simulate the failure.
        from app.routes import retirement as retirement_mod  # pylint: disable=import-outside-toplevel
        from sqlalchemy.exc import DataError  # pylint: disable=import-outside-toplevel

        raise_id, effective, _ = self._seed_saveable_raise(seed_user, db, seed_periods_today)

        def _fail(_ctx, _profile):
            raise DataError("stmt", None, Exception("simulated NUMERIC overflow"))

        monkeypatch.setattr(
            retirement_mod.salary_regeneration, "regenerate_salary_transactions", _fail,
        )

        with caplog.at_level(logging.ERROR, logger="app.routes.retirement"):
            resp = self._save(auth_client, raise_id, "year", str(effective + 2))

        assert resp.status_code == 500
        assert resp.headers.get(DESIGNED_FRAGMENT_HEADER) == "1"
        html = resp.data.decode()
        assert 'id="assumptions-panel"' in html
        row = self._row(html, raise_id)
        assert "Failed to save this end year." in row
        assert self._stored(db, raise_id)[0] is None, "the write survived the rollback"
        assert any(
            "failed to save raise end years" in record.getMessage()
            and str(raise_id) in record.getMessage()
            for record in caplog.records
        )

    def test_the_retained_rows_are_told_to_the_owner(
        self, auth_client, seed_user, db, seed_periods_today, monkeypatch,
    ):
        """What the regeneration declined to touch is flashed, not dropped.

        The service RETURNS the retained ids (plan step R10-a, ruling
        **R-SAL24**); the route flashes them, and the reload the rail's
        success swap triggers is where the owner reads it.
        """
        # Pylint: import-outside-toplevel -- the route module is needed only
        # to stub the regeneration's answer.
        from app.routes import retirement as retirement_mod  # pylint: disable=import-outside-toplevel

        raise_id, effective, _ = self._seed_saveable_raise(seed_user, db, seed_periods_today)
        monkeypatch.setattr(
            retirement_mod.salary_regeneration,
            "regenerate_salary_transactions", lambda _ctx, _profile: [7],
        )

        resp = self._save(auth_client, raise_id, "year", str(effective + 2))

        assert resp.status_code == 200
        assert self._stored(db, raise_id)[0] == effective + 2
        with auth_client.session_transaction() as sess:
            flashes = sess.get("_flashes", [])
        assert any("1 upcoming instance kept the value" in text for _, text in flashes)

    def test_the_saved_raises_own_profile_is_regenerated_and_no_other(
        self, auth_client, seed_user, db, seed_periods_today, monkeypatch,
    ):
        """Two profiles, one Save: the raise's OWN profile is regenerated.

        Every other case seeds one profile, so a regeneration that reached
        for ``salary_profiles[0]`` instead of the row's profile would pass
        them all while redrawing the wrong template's rows for an owner with
        two (an adversarial review of this step named the mutation).  The
        raise sits on the SECOND profile, and the regeneration is called for
        it alone.  Its observable was each template's ``default_amount``,
        which the regeneration re-stated, until plan step X-bp deleted the
        re-statement, so the call is counted, as
        :meth:`test_two_rows_posted_together_both_write` counts it.
        """
        # Pylint: import-outside-toplevel -- the route module is needed only
        # to count the regeneration's calls.
        from app.routes import retirement as retirement_mod  # pylint: disable=import-outside-toplevel

        # The first profile, with no raise, is what _seed_underfunded builds.
        _, effective, first_id = self._seed_saveable_raise(seed_user, db, seed_periods_today)
        first_raise = db.session.query(SalaryRaise).filter_by(
            salary_profile_id=first_id,
        ).one()
        db.session.delete(first_raise)
        second = _create_salary_profile(seed_user, db.session, name="Second Job")
        second_id = second.id
        row = make_recurring_raise(second_id, db.session, effective_year=effective)
        db.session.commit()
        raise_id = row.id
        real = retirement_mod.salary_regeneration.regenerate_salary_transactions
        calls = []

        def _counted(ctx, profile):
            calls.append(profile.id)
            return real(ctx, profile)

        monkeypatch.setattr(
            retirement_mod.salary_regeneration, "regenerate_salary_transactions", _counted,
        )

        resp = self._save(auth_client, raise_id, "year", str(effective))

        assert resp.status_code == 200
        assert self._stored(db, raise_id)[0] == effective
        assert first_id != second_id
        assert calls == [second_id], (
            "the regeneration ran for a profile other than the raise's own"
        )

    def test_a_non_htmx_save_writes_and_redirects_to_retirement(
        self, auth_client, seed_user, db, seed_periods_today,
    ):
        """The plain-form fallback writes the same column and lands on the page."""
        raise_id, effective, _ = self._seed_saveable_raise(seed_user, db, seed_periods_today)

        resp = self._save(auth_client, raise_id, "year", str(effective + 1), htmx=False)

        assert resp.status_code == 302
        assert "/retirement" in resp.headers["Location"]
        assert self._stored(db, raise_id)[0] == effective + 1

    def test_two_rows_posted_together_both_write(
        self, auth_client, seed_user, db, seed_periods_today, monkeypatch,
    ):
        """Every pair the POST carries is resolved and written.

        No rendered form posts two rows, but the arm is a loop over the pairs
        the schema gathered rather than a special case for one, so the general
        shape is what is graded: both rows written, and ONE regeneration for
        the one profile both raises sit on.
        """
        # Pylint: import-outside-toplevel -- the route module is needed only
        # to count the regeneration's calls.
        from app.routes import retirement as retirement_mod  # pylint: disable=import-outside-toplevel

        raise_id, effective, profile_id = self._seed_saveable_raise(
            seed_user, db, seed_periods_today,
        )
        other = make_recurring_raise(
            profile_id, db.session, effective_year=effective + 1,
        )
        db.session.commit()
        other_id = other.id
        real = retirement_mod.salary_regeneration.regenerate_salary_transactions
        calls = []

        def _counted(ctx, profile):
            calls.append(profile.id)
            return real(ctx, profile)

        monkeypatch.setattr(
            retirement_mod.salary_regeneration, "regenerate_salary_transactions", _counted,
        )

        resp = auth_client.post(
            "/retirement/settings",
            data={
                f"raise_end_mode_{raise_id}": "year",
                f"raise_end_year_{raise_id}": str(effective + 5),
                f"raise_end_mode_{other_id}": "year",
                f"raise_end_year_{other_id}": str(effective + 6),
            },
            headers={"HX-Request": "true"},
        )

        assert resp.status_code == 200
        assert self._stored(db, raise_id)[0] == effective + 5
        assert self._stored(db, other_id)[0] == effective + 6
        assert calls == [profile_id]
