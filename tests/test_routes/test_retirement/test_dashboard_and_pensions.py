"""The retirement dashboard, its pension profiles, its settings and its projections."""

from datetime import date
from decimal import Decimal

from app.models.pension_profile import PensionProfile
from app.models.user import UserSettings
from app.models.paycheck_line import PaycheckLine
from app.services.balance_at import BalanceContext
from app.models.ref import CalcMethod, PaycheckLineKind

from ._helpers import _create_salary_profile, _create_pension, _create_retirement_account


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
            effective_date=date(date.today().year + 1, 1, 1),
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
