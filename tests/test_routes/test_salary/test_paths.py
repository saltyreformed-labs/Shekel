"""Salary route tests: negative paths, the net-biweekly mismatch fixes, and stub pricing on the
screens.

Split from ``tests/test_routes/test_salary.py`` by ``tools/split`` (ruling R-BAL301).
"""

import re
from decimal import Decimal

from app.extensions import db
from app.models.salary_profile import SalaryProfile
from app.models.salary_raise import SalaryRaise
from app.models.paycheck_line import PaycheckLine
from app.models.pay_period import PayPeriod
from app.models.transaction import Transaction
from app.models.ref import CalcMethod, PaycheckLineKind, FilingStatus, RaiseType
from app.enums import WithholdingKindEnum

from app.services.balance_at import BalanceContext
from app.services.generation_schedule import GenerationSchedule

from tests._test_helpers import add_test_pay_stub, all_periods, NO_TAX_LAW, state_and_fica_law

from ._helpers import _create_profile, _create_second_user_salary_profile, _capture_render


# ── Negative Paths ────────────────────────────────────────────────


class TestSalaryNegativePaths:
    """Negative-path tests: nonexistent IDs, IDOR on raises/deductions/views."""

    def test_edit_nonexistent_profile(self, app, auth_client, seed_user):
        """GET /salary/999999/edit for a nonexistent profile returns 404 (security)."""
        with app.app_context():
            resp = auth_client.get("/salary/999999/edit", follow_redirects=True)

            assert resp.status_code == 404

    def test_update_nonexistent_profile(self, app, auth_client, seed_user):
        """POST /salary/999999 for a nonexistent profile returns 404 (security)."""
        with app.app_context():
            filing_status = db.session.query(FilingStatus).filter_by(name="single").one()

            resp = auth_client.post("/salary/999999", data={
                "name": "Ghost",
                "filing_status_id": filing_status.id,
                "state_code": "NC",
            }, follow_redirects=True)

            assert resp.status_code == 404

    def test_delete_nonexistent_profile(self, app, auth_client, seed_user):
        """POST /salary/999999/delete for a nonexistent profile returns 404 (security)."""
        with app.app_context():
            resp = auth_client.post(
                "/salary/999999/delete", follow_redirects=True,
            )

            assert resp.status_code == 404

    def test_add_raise_to_other_users_profile_idor(
        self, app, auth_client, seed_user, second_user
    ):
        """POST /salary/<id>/raises for another user's profile is blocked."""
        with app.app_context():
            other_profile = _create_second_user_salary_profile(second_user)
            raise_type = db.session.query(RaiseType).filter_by(name="merit").one()

            resp = auth_client.post(
                f"/salary/{other_profile.id}/raises",
                data={
                    "raise_type_id": raise_type.id,
                    "effective_month": "7",
                    "effective_year": "2026",
                    "percentage": "3",
                },
                follow_redirects=True,
            )

            assert resp.status_code == 404

            # Verify no raise was created for the other user's profile.
            raise_count = db.session.query(SalaryRaise).filter_by(
                salary_profile_id=other_profile.id,
            ).count()
            assert raise_count == 0

    def test_add_deduction_to_other_users_profile_idor(
        self, app, auth_client, seed_user, second_user
    ):
        """POST /salary/<id>/lines for another user's profile is blocked."""
        with app.app_context():
            other_profile = _create_second_user_salary_profile(second_user)
            pre_tax = db.session.query(PaycheckLineKind).filter_by(name="pre_tax_deduction").one()
            flat_method = db.session.query(CalcMethod).filter_by(name="flat").one()

            resp = auth_client.post(
                f"/salary/{other_profile.id}/lines",
                data={
                    "name": "Sneaky 401k",
                    "paycheck_line_kind_id": pre_tax.id,
                    "calc_method_id": flat_method.id,
                    "amount": "200.00",
                },
                follow_redirects=True,
            )

            assert resp.status_code == 404

            # Verify no deduction was created for the other user's profile.
            ded_count = db.session.query(PaycheckLine).filter_by(
                salary_profile_id=other_profile.id,
            ).count()
            assert ded_count == 0

    def test_delete_raise_from_other_users_profile_idor(
        self, app, auth_client, seed_user, second_user
    ):
        """POST /salary/raises/<id>/delete for another user's raise is blocked."""
        with app.app_context():
            other_profile = _create_second_user_salary_profile(second_user)
            raise_type = db.session.query(RaiseType).filter_by(name="merit").one()

            salary_raise = SalaryRaise(
                salary_profile_id=other_profile.id,
                raise_type_id=raise_type.id,
                effective_month=7,
                effective_year=2026,
                percentage=Decimal("0.0300"),
            )
            db.session.add(salary_raise)
            db.session.commit()
            raise_id = salary_raise.id

            resp = auth_client.post(
                f"/salary/raises/{raise_id}/delete",
                follow_redirects=True,
            )

            assert resp.status_code == 404

            # Verify raise still exists in DB.
            db.session.expire_all()
            refreshed = db.session.get(SalaryRaise, raise_id)
            assert refreshed is not None

    def test_delete_deduction_from_other_users_profile_idor(
        self, app, auth_client, seed_user, second_user
    ):
        """POST /salary/lines/<id>/delete for another user's deduction is blocked."""
        with app.app_context():
            other_profile = _create_second_user_salary_profile(second_user)
            pre_tax = db.session.query(PaycheckLineKind).filter_by(name="pre_tax_deduction").one()
            flat_method = db.session.query(CalcMethod).filter_by(name="flat").one()

            deduction = PaycheckLine(
                salary_profile_id=other_profile.id,
                paycheck_line_kind_id=pre_tax.id,
                calc_method_id=flat_method.id,
                name="Other 401k",
                amount=Decimal("100.0000"),
            )
            db.session.add(deduction)
            db.session.commit()
            ded_id = deduction.id

            resp = auth_client.post(
                f"/salary/lines/{ded_id}/delete",
                follow_redirects=True,
            )

            assert resp.status_code == 404

            # Verify deduction still exists in DB.
            db.session.expire_all()
            refreshed = db.session.get(PaycheckLine, ded_id)
            assert refreshed is not None

    def test_view_other_users_breakdown_idor(
        self, app, auth_client, seed_user, seed_periods, second_user
    ):
        """GET /salary/<id>/breakdown for another user's profile is blocked."""
        with app.app_context():
            other_profile = _create_second_user_salary_profile(second_user)

            resp = auth_client.get(
                f"/salary/{other_profile.id}/breakdown",
                follow_redirects=True,
            )

            assert resp.status_code == 404
            # No sensitive data from the other user's profile should leak.
            assert b"Other Job" not in resp.data

    def test_view_other_users_projection_idor(
        self, app, auth_client, seed_user, seed_periods, second_user
    ):
        """GET /salary/<id>/projection for another user's profile is blocked."""
        with app.app_context():
            other_profile = _create_second_user_salary_profile(second_user)

            resp = auth_client.get(
                f"/salary/{other_profile.id}/projection",
                follow_redirects=True,
            )

            assert resp.status_code == 404


# ── Net Biweekly Mismatch Fixes (section 3.3) ────────────────────────


class TestNetBiweeklyMismatchFixes:
    """Tests for the root causes of net biweekly mismatch (section 3.3).

    Verifies that creating a salary profile sets template default_amount to
    NET, not GROSS, and that a future year prices on the latest year's law.
    The other two causes -- a state or FICA save that did not regenerate the
    salary rows -- went with those Settings saves at plan step salary:X-at-1,
    which left the tax law nothing in the app can edit.
    """

    def test_create_profile_sets_template_default_to_net(
        self, app, auth_client, seed_user, seed_periods, tax_law
    ):
        """Creating a salary profile sets template.default_amount to NET pay.

        Before the fix, default_amount was set to GROSS (annual / 26).
        After the fix, it should be NET (after taxes and deductions).
        Without tax configs, NET equals GROSS (no taxes to deduct), so
        we seed tax configs to produce a meaningful difference.
        """
        with app.app_context():
            user = seed_user["user"]
            tax_law(state_and_fica_law())
            db.session.commit()

            filing_status = db.session.query(FilingStatus).filter_by(
                name="single"
            ).one()

            auth_client.post("/salary", data={
                "name": "Net Test Job",
                "pay_amount": "2884.62",  # $75,000.00 a year / 26
                "pay_payday": "2026-01-02",  # the first period's payday
                "filing_status_id": filing_status.id,
                "state_code": "NC",
            }, follow_redirects=True)

            profile = (
                db.session.query(SalaryProfile)
                .filter_by(user_id=user.id, name="Net Test Job")
                .one()
            )
            template = profile.template

            gross_biweekly = Decimal("75000.00") / 26

            # With taxes configured, net pay is less than gross.
            # The template default_amount should reflect NET, not GROSS.
            assert template.default_amount < gross_biweekly, (
                f"template.default_amount ({template.default_amount}) should "
                f"be less than gross biweekly ({gross_biweekly}) when taxes "
                f"are configured"
            )

    def test_create_profile_without_tax_configs_uses_gross_as_fallback(
        self, app, auth_client, seed_user, seed_periods, tax_law
    ):
        """Without tax configs, default_amount equals GROSS (no taxes to subtract).

        This is correct behavior -- the installed law's federal rules and
        FICA price $0, and its North Carolina has no income tax, so state tax
        is $0.  NET == GROSS when there are no taxes.  (It installed a law
        with no year at all until plan step salary:X-at-3, whose engine
        refuses a state the law does not list.)
        """
        tax_law(NO_TAX_LAW)
        with app.app_context():
            filing_status = db.session.query(FilingStatus).filter_by(
                name="single"
            ).one()

            auth_client.post("/salary", data={
                "name": "No Tax Job",
                "pay_amount": "2307.69",  # $60,000.00 a year / 26
                "pay_payday": "2026-01-02",  # the first period's payday
                "filing_status_id": filing_status.id,
                "state_code": "NC",
            }, follow_redirects=True)

            profile = (
                db.session.query(SalaryProfile)
                .filter_by(
                    user_id=seed_user["user"].id,
                    name="No Tax Job",
                )
                .one()
            )
            template = profile.template

            gross_biweekly = Decimal("60000.00") / 26
            expected = gross_biweekly.quantize(Decimal("0.01"))

            # No taxes configured, so NET == GROSS.
            assert template.default_amount == expected, (
                f"Without tax configs, default_amount ({template.default_amount}) "
                f"should equal gross biweekly ({expected})"
            )

    def test_future_year_transactions_use_current_year_tax_configs(
        self, app, auth_client, seed_user, seed_periods_52, tax_law
    ):
        """Future-year salary rows fall back to current-year tax configs.

        When tax configs exist only for 2026, periods in 2027 must use the 2026
        configs instead of producing zero-tax paychecks.  This prevents the net
        biweekly mismatch where the salary page (which always uses current-year
        configs) shows a different amount than the grid.

        **Read through the AMOUNT MODEL since plan step balance:X-au-d**, where
        it read ``Transaction.estimated_amount``: a salary row stores no figure
        at all now, so the per-year resolution the case is about lives in
        ``income_service.SalaryPricing._breakdown_by_period`` and this asks the rule
        that consults it.  The claim and the figures are unchanged.
        """
        with app.app_context():
            user = seed_user["user"]
            profile = _create_profile(seed_user)
            tax_law(state_and_fica_law(tax_year=2026))
            db.session.commit()

            # Regenerate transactions via the recurrence engine so that
            # future-year periods get amounts from the calculator.
            from app.services import recurrence_engine, pay_period_service
            periods = all_periods(user.id)
            recurrence_engine.regenerate_for_template(
                profile.template, GenerationSchedule.for_period_ids(
                    BalanceContext.build(profile.template.user_id), {p.id for p in periods},
                ), seed_user["scenario"].id,
            )
            db.session.commit()

            # Find a 2026 transaction and a 2027 transaction.
            txn_2026 = (
                db.session.query(Transaction)
                # The relationship rather than the entity: since plan step
                # ``pay_calendar:C13-a`` two foreign keys link these tables,
                # so an entity join has no single onclause to infer.
                .join(Transaction.pay_period)
                .filter(
                    Transaction.template_id == profile.template_id,
                    Transaction.scenario_id == seed_user["scenario"].id,
                    db.extract("year", PayPeriod.start_date) == 2026,
                )
                .first()
            )
            txn_2027 = (
                db.session.query(Transaction)
                # The relationship rather than the entity: since plan step
                # ``pay_calendar:C13-a`` two foreign keys link these tables,
                # so an entity join has no single onclause to infer.
                .join(Transaction.pay_period)
                .filter(
                    Transaction.template_id == profile.template_id,
                    Transaction.scenario_id == seed_user["scenario"].id,
                    db.extract("year", PayPeriod.start_date) == 2027,
                )
                .first()
            )

            assert txn_2026 is not None, "Expected a 2026 salary transaction"
            assert txn_2027 is not None, "Expected a 2027 salary transaction"

            gross_biweekly = (Decimal("75000.00") / 26).quantize(Decimal("0.01"))

            # Pylint: ``import-outside-toplevel`` -- the amount model is not
            # this module's subject and is imported where it is used, as the
            # recurrence engine above it is.
            from app.services import cash_ledger  # pylint: disable=import-outside-toplevel
            priced = cash_ledger.amounts_by_key(
                [txn_2026, txn_2027],
                cash_ledger.derived_amount_basis(user.id, seed_user["scenario"].id),
            )
            net_2026 = priced[txn_2026.id]
            net_2027 = priced[txn_2027.id]

            # Both should be less than gross (taxes applied).
            assert net_2026 < gross_biweekly, (
                f"2026 row ({net_2026}) should be less than gross "
                f"({gross_biweekly}) -- taxes should be applied"
            )
            assert net_2027 < gross_biweekly, (
                f"2027 row ({net_2027}) should be less than gross "
                f"({gross_biweekly}) -- fallback taxes should be applied"
            )

            # Both years should produce the same net pay (same tax configs).
            assert net_2026 == net_2027, (
                f"2026 row ({net_2026}) and 2027 row ({net_2027}) should "
                "match because 2027 falls back to 2026 tax configs"
            )


class TestStubPricingOnTheScreens:
    """Each screen names the pay stub that priced a paycheck's taxes (R-SAL100).

    Plan step salary:S11-c-2c deleted the calibration door these screens
    described; ruling **R-SAL100** ("Name the pricing stub") has each one name
    the stub the ENGINE priced a paycheck from, or the tax formulas, and the
    old Re-calibrate / Remove buttons become one link to the Pay stubs list.
    The words are ruling **R-SAL121**'s, approved as an old -> new list, with
    the card foot and the strip amended by **R-SAL122** the same morning to
    say "used for pricing" (a switched-off stub dated earlier is not denied),
    and the strip reworded by **R-SAL126**: its "one with the same lines first"
    named the wrong stub in several cases (a stub carrying a tax-changing
    one-off, a taxed line shown at $0.00 or under another heading), so it now
    gives the gist and points at the card, which names the real one.  The
    stub's figures are made up; the stub sits on the fifth payday
    (``seed_periods[4]``), before today's paycheck (today is frozen inside
    ``seed_periods[5]``), so today's paycheck is priced from it and the
    first payday, before it, from the formulas.
    """

    @staticmethod
    def _seed_stub(profile, payday):
        """Record one switched-on stub of *profile* on *payday* (made-up taxes)."""
        add_test_pay_stub(
            profile, payday, "2884.62",
            taxes={
                WithholdingKindEnum.FEDERAL_INCOME: "250.00",
                WithholdingKindEnum.STATE_INCOME: "120.00",
                WithholdingKindEnum.SOCIAL_SECURITY: "178.85",
                WithholdingKindEnum.MEDICARE: "41.83",
            },
        )
        db.session.commit()

    @staticmethod
    def _text(response):
        """The response's HTML with every run of whitespace collapsed to one space."""
        return " ".join(response.data.decode().split())

    def test_without_a_stub_every_paycheck_names_the_tax_formulas(
        self, app, auth_client, seed_user, seed_periods,
    ):
        """No stub: the caption and the card say the formulas; the strip links the stubs."""
        with app.app_context():
            profile = _create_profile(seed_user)

            html = self._text(auth_client.get("/salary"))

            assert "taxes from the tax formulas" in html
            assert (
                "Taxes from the tax formulas: no pay stub used for pricing "
                "is dated on or before this payday"
            ) in html
            assert (
                "Each paycheck's taxes come from your latest pay stub used for "
                "pricing on or before its payday, preferring one that matches "
                "it. With no stub, the tax formulas. The 'Where this paycheck "
                "goes' card shows which stub each paycheck uses."
            ) in html
            assert "one with the same lines first" not in html
            assert "calibrat" not in html.lower()

    def test_the_strip_names_the_card_by_its_own_heading(
        self, app, auth_client, seed_user, seed_periods,
    ):
        """The card the strip names (R-SAL126) is the heading the same page renders.

        The title is spelled in two templates, the card's heading and the
        strip's sentence, so a renamed card would leave the strip pointing at
        nothing while every other assertion still passed; this reads both off
        one rendered page.
        """
        with app.app_context():
            _create_profile(seed_user)

            html = auth_client.get("/salary").data.decode()
            card = html[html.index('id="anatomy-composition"'):]
            heading = re.search(r"</i>\s*([^<]+?)\s*</h6>", card).group(1)
            strip = html[html.index('class="sal-stubstrip__text"'):]
            strip = " ".join(strip[:strip.index("</span>")].split())

            assert f"The '{heading}' card shows which stub each paycheck uses." in strip

    def test_the_strip_links_the_pay_stubs_list(
        self, app, auth_client, seed_user, seed_periods,
    ):
        """The Pay stubs button's URL routes to the profile page's stubs card.

        A link whose door moved would still render, so the URL is followed:
        it answers 200 and carries the anchor the button names.
        """
        with app.app_context():
            profile = _create_profile(seed_user)
            link = f"/salary/{profile.id}/edit#stubs-section"

            html = auth_client.get("/salary").data.decode()
            target = auth_client.get(link.split("#", maxsplit=1)[0])

            assert f'href="{link}"' in html
            assert target.status_code == 200
            assert 'id="stubs-section"' in target.data.decode()

    def test_a_stub_on_or_before_todays_payday_is_named(
        self, app, auth_client, seed_user, seed_periods,
    ):
        """Today's paycheck is priced from the stub, and both places say which."""
        with app.app_context():
            profile = _create_profile(seed_user)
            stub_day = seed_periods[4].start_date
            self._seed_stub(profile, stub_day)
            named = stub_day.strftime("%b %-d, %Y")

            response, ctx = _capture_render(
                app, auth_client, "/salary", "salary/cockpit.html",
            )
            html = self._text(response)

            assert ctx["composition"]["tax_stub_payday"] == stub_day
            assert f"taxes from your {named} pay stub" in html
            assert f"Taxes from your {named} pay stub" in html
            assert "taxes from the tax formulas" not in html

    def test_a_switched_off_stub_leaves_the_formulas_named(
        self, app, auth_client, seed_user, seed_periods,
    ):
        """A stub dated before today's payday but switched off prices nothing.

        Ruling R-SAL122 (amending R-SAL121): the card says no stub USED FOR
        PRICING is dated on or before the payday, which stays true with a
        switched-off stub sitting before it.
        """
        with app.app_context():
            profile = _create_profile(seed_user)
            self._seed_stub(profile, seed_periods[4].start_date)
            profile.pay_stubs[0].use_for_pricing = False
            db.session.commit()

            html = self._text(auth_client.get("/salary"))

            assert "taxes from the tax formulas" in html
            assert (
                "Taxes from the tax formulas: no pay stub used for pricing "
                "is dated on or before this payday"
            ) in html

    def test_the_card_names_each_paychecks_own_basis(
        self, app, auth_client, seed_user, seed_periods,
    ):
        """Stepping the card: before the stub the formulas, on and after it the stub."""
        with app.app_context():
            profile = _create_profile(seed_user)
            stub_day = seed_periods[4].start_date
            self._seed_stub(profile, stub_day)
            named = stub_day.strftime("%b %-d, %Y")

            before = self._text(auth_client.get(
                f"/salary/{profile.id}/anatomy/{seed_periods[0].id}"
            ))
            on_it = self._text(auth_client.get(
                f"/salary/{profile.id}/anatomy/{seed_periods[4].id}"
            ))

            assert (
                "Taxes from the tax formulas: no pay stub used for pricing "
                "is dated on or before this payday"
            ) in before
            assert f"Taxes from your {named} pay stub" in on_it

    def test_the_profile_page_has_no_calibration_card(
        self, app, auth_client, seed_user, seed_periods,
    ):
        """The calibration card is gone; the empty stubs card says stubs price taxes."""
        with app.app_context():
            profile = _create_profile(seed_user)

            html = self._text(auth_client.get(f"/salary/{profile.id}/edit"))

            assert "Pay Stub Calibration" not in html
            assert "calibrat" not in html.lower()
            assert (
                "No pay stubs yet. Enter one line by line: the app checks your "
                "paycheck lines against it and takes your paychecks' taxes "
                "from it."
            ) in html
