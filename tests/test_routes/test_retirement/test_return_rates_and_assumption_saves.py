"""The return rate's display, the rail's raise rows and the per-field saves."""

import re
from datetime import date
from decimal import Decimal

from app import ref_cache
from app.enums import EmployerContributionTypeEnum
from app.models.user import UserSettings
from app.models.investment_params import InvestmentParams
from app.services import account_service
from app.services.balance_at import BalanceContext
from app.models.ref import AccountType
from app.utils.dates import display_today

from ._helpers import (
    _RAISE_ROW_MARKUP,
    _create_salary_profile,
    _create_pension,
    _create_retirement_account,
)


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
            effective_date=date(starts, 1, 1),
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
            effective_date=date(date.today().year + 1, 1, 1),
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
