"""Salary route tests: deductions, and earning lines through the door.

Split from ``tests/test_routes/test_salary.py`` by ``tools/split`` (ruling R-BAL301).
"""

from datetime import date
import re
from decimal import Decimal

from app.extensions import db
from app.models.salary_profile import SalaryProfile
from app.models.paycheck_line import PaycheckLine
from app.models.recurrence_rule import RecurrenceRule
from app.models.ref import CalcMethod, PaycheckLineKind
from app import ref_cache
from app.enums import PaycheckLineKindEnum, PeriodPlacementEnum, RecurrenceUnitEnum

import pytest
from app.services.balance_at import BalanceContext

from tests._test_helpers import make_line_cadence_rule

from ._helpers import _create_profile, _create_other_user_profile, _cadence_payload, _line_form


# ── Deductions ─────────────────────────────────────────────────────


class TestDeductions:
    """Tests for deduction add/delete endpoints."""

    def test_add_deduction(self, app, auth_client, seed_user, seed_periods):
        """POST /salary/<id>/lines adds a deduction."""
        with app.app_context():
            profile = _create_profile(seed_user)
            pre_tax = db.session.query(PaycheckLineKind).filter_by(name="pre_tax_deduction").one()
            flat_method = db.session.query(CalcMethod).filter_by(name="flat").one()

            response = auth_client.post(
                f"/salary/{profile.id}/lines",
                data={
                    "name": "401k",
                    "paycheck_line_kind_id": pre_tax.id,
                    "calc_method_id": flat_method.id,
                    "amount": "200.00",
                },
                follow_redirects=True,
            )

            assert response.status_code == 200
            assert b"401k" in response.data

    def test_delete_deduction(self, app, auth_client, seed_user, seed_periods):
        """POST /salary/lines/<id>/delete removes a deduction."""
        with app.app_context():
            profile = _create_profile(seed_user)
            pre_tax = db.session.query(PaycheckLineKind).filter_by(name="pre_tax_deduction").one()
            flat_method = db.session.query(CalcMethod).filter_by(name="flat").one()

            deduction = PaycheckLine(
                salary_profile_id=profile.id,
                paycheck_line_kind_id=pre_tax.id,
                calc_method_id=flat_method.id,
                name="Health Insurance",
                amount=Decimal("150.00"),
            )
            db.session.add(deduction)
            db.session.commit()

            response = auth_client.post(
                f"/salary/lines/{deduction.id}/delete",
                follow_redirects=True,
            )

            assert response.status_code == 200
            assert b"Payroll line removed." in response.data

    def test_delete_deduction_takes_its_cadence_rule_with_it(
        self, app, auth_client, seed_user, seed_periods,
    ):
        """A deleted line's rule goes with it: the arc's third arm cascades like the other two.

        Plan step salary:R15-b.  The rule's FK is on the rule and the
        database cascades it (``ON DELETE CASCADE``), so no door that deletes
        a deduction can leave an owner-less rule -- finding F-6's shape -- and
        the ORM relationship's ``delete-orphan`` says the same in the session.
        """
        with app.app_context():
            profile = _create_profile(seed_user)
            pre_tax = db.session.query(PaycheckLineKind).filter_by(name="pre_tax_deduction").one()
            flat_method = db.session.query(CalcMethod).filter_by(name="flat").one()
            deduction = PaycheckLine(
                salary_profile_id=profile.id,
                paycheck_line_kind_id=pre_tax.id,
                calc_method_id=flat_method.id,
                name="Health Insurance",
                amount=Decimal("150.00"),
            )
            db.session.add(deduction)
            db.session.flush()
            rule = make_line_cadence_rule(db.session, deduction, 24)
            db.session.commit()
            rule_id, ded_id = rule.id, deduction.id
            assert db.session.get(RecurrenceRule, rule_id).paycheck_line_id == ded_id

            response = auth_client.post(
                f"/salary/lines/{ded_id}/delete", follow_redirects=True,
            )

            assert response.status_code == 200
            db.session.expire_all()
            assert db.session.get(PaycheckLine, ded_id) is None
            assert db.session.get(RecurrenceRule, rule_id) is None, (
                "the deduction's rule survived its owner"
            )

    def test_a_raw_delete_of_the_line_cascades_at_the_database(
        self, app, seed_user, seed_periods,
    ):
        """The cascade is the DATABASE's, so a delete from any door disposes of the rule."""
        # pylint: disable=import-outside-toplevel
        from sqlalchemy import text

        with app.app_context():
            profile = _create_profile(seed_user)
            pre_tax = db.session.query(PaycheckLineKind).filter_by(name="pre_tax_deduction").one()
            flat_method = db.session.query(CalcMethod).filter_by(name="flat").one()
            deduction = PaycheckLine(
                salary_profile_id=profile.id,
                paycheck_line_kind_id=pre_tax.id,
                calc_method_id=flat_method.id,
                name="Transit",
                amount=Decimal("50.00"),
            )
            db.session.add(deduction)
            db.session.flush()
            rule = make_line_cadence_rule(db.session, deduction, 12)
            db.session.commit()
            rule_id, ded_id = rule.id, deduction.id

            db.session.execute(
                text("DELETE FROM salary.paycheck_lines WHERE id = :id"), {"id": ded_id},
            )
            db.session.commit()
            db.session.expire_all()
            assert db.session.get(RecurrenceRule, rule_id) is None

    def test_add_deduction_validation_error(self, app, auth_client, seed_user, seed_periods):
        """POST /salary/<id>/lines with missing fields shows a validation error."""
        with app.app_context():
            profile = _create_profile(seed_user)

            response = auth_client.post(
                f"/salary/{profile.id}/lines",
                data={"name": ""},
                follow_redirects=True,
            )

            assert response.status_code == 200
            assert b"Please correct the highlighted errors" in response.data

    def test_delete_other_users_deduction_redirects(
        self, app, auth_client, seed_user
    ):
        """POST /salary/lines/<id>/delete for another user's deduction returns 404 (security)."""
        with app.app_context():
            other = _create_other_user_profile()
            pre_tax = db.session.query(PaycheckLineKind).filter_by(name="pre_tax_deduction").one()
            flat_method = db.session.query(CalcMethod).filter_by(name="flat").one()

            deduction = PaycheckLine(
                salary_profile_id=other["profile"].id,
                paycheck_line_kind_id=pre_tax.id,
                calc_method_id=flat_method.id,
                name="Other 401k",
                amount=Decimal("100.00"),
            )
            db.session.add(deduction)
            db.session.commit()

            response = auth_client.post(
                f"/salary/lines/{deduction.id}/delete",
                follow_redirects=True,
            )

            assert response.status_code == 404

    def test_add_deduction_htmx_returns_partial(
        self, app, auth_client, seed_user, seed_periods
    ):
        """POST /salary/<id>/lines with HX-Request returns a partial."""
        with app.app_context():
            profile = _create_profile(seed_user)
            post_tax = db.session.query(PaycheckLineKind).filter_by(name="post_tax_deduction").one()
            flat_method = db.session.query(CalcMethod).filter_by(name="flat").one()

            response = auth_client.post(
                f"/salary/{profile.id}/lines",
                data={
                    "name": "Roth IRA",
                    "paycheck_line_kind_id": post_tax.id,
                    "calc_method_id": flat_method.id,
                    "amount": "300.00",
                },
                headers={"HX-Request": "true"},
            )

            assert response.status_code == 200
            assert b"Roth IRA" in response.data
            assert b"300" in response.data

    def test_update_deduction(self, app, auth_client, seed_user, seed_periods):
        """POST /salary/lines/<id>/edit updates an existing deduction, cadence included.

        The edit form posts the whole record, the cadence controls among it
        since plan step salary:R15-c: a line taken every paycheck edited to
        "Paychecks, every 1, at most 2 a month" gains the rule the migration
        writes for a 24, authored onto the line by the same save that renames
        it.
        """
        with app.app_context():
            profile = _create_profile(seed_user)
            pre_tax = db.session.query(PaycheckLineKind).filter_by(name="pre_tax_deduction").one()
            flat_method = db.session.query(CalcMethod).filter_by(name="flat").one()

            deduction = PaycheckLine(
                salary_profile_id=profile.id,
                paycheck_line_kind_id=pre_tax.id,
                calc_method_id=flat_method.id,
                name="401k",
                amount=Decimal("200.00"),
            )
            db.session.add(deduction)
            db.session.commit()
            assert deduction.recurrence_rule is None

            response = auth_client.post(
                f"/salary/lines/{deduction.id}/edit",
                data={
                    "name": "401k Updated",
                    "paycheck_line_kind_id": pre_tax.id,
                    "calc_method_id": flat_method.id,
                    "amount": "350.00",
                    **_cadence_payload(
                        RecurrenceUnitEnum.PERIOD,
                        PeriodPlacementEnum.CONTAINING_DATE,
                        ceiling="2",
                    ),
                },
                follow_redirects=True,
            )

            assert response.status_code == 200
            assert b"Payroll line &#39;401k Updated&#39; updated." in response.data

            db.session.refresh(deduction)
            assert deduction.name == "401k Updated"
            assert deduction.amount == Decimal("350.00")
            assert deduction.recurrence_rule is not None, "the edit authored no rule"
            assert deduction.recurrence_rule.max_per_month == 2
            assert deduction.recurrence_rule.starts_on == date(2026, 1, 2)

    def test_update_deduction_clears_target_account_and_cap(
        self, app, auth_client, seed_user, seed_periods
    ):
        """Empty submits clear the nullable target-account link and cap.

        The edit form's "-- None --" option posts ``target_account_id=""``
        and an emptied cap input posts ``annual_cap=""``; both are
        allow_none, so the schema pre_load now loads them as explicit
        None (it used to DROP the keys, making them unclearable) and
        the route's setattr loop nulls the columns.  A cleared cap
        means uncapped: the paycheck calculator's clamp is
        ``is not None``-guarded, so the deduction reverts to its raw
        amount.
        """
        with app.app_context():
            profile = _create_profile(seed_user)
            pre_tax = db.session.query(PaycheckLineKind).filter_by(name="pre_tax_deduction").one()
            flat_method = db.session.query(CalcMethod).filter_by(name="flat").one()

            deduction = PaycheckLine(
                salary_profile_id=profile.id,
                paycheck_line_kind_id=pre_tax.id,
                calc_method_id=flat_method.id,
                name="401k Linked",
                amount=Decimal("200.00"),
                annual_cap=Decimal("23500.00"),
                target_account_id=seed_user["account"].id,
            )
            db.session.add(deduction)
            db.session.commit()

            response = auth_client.post(
                f"/salary/lines/{deduction.id}/edit",
                data={
                    "name": "401k Linked",
                    "paycheck_line_kind_id": pre_tax.id,
                    "calc_method_id": flat_method.id,
                    "amount": "200.00",
                    "target_account_id": "",
                    "annual_cap": "",
                },
                follow_redirects=True,
            )

            assert response.status_code == 200
            db.session.refresh(deduction)
            assert deduction.target_account_id is None
            assert deduction.annual_cap is None

    def test_add_deduction_refuses_another_users_target_account(
        self, app, auth_client, seed_user, seed_second_user, seed_periods
    ):
        """A forged ``target_account_id`` is refused at the create door.

        Ledger row **N-534**.  ``target_account_id`` is what makes a deduction
        a contribution FEED into an investment account, and the schema checked
        only that it was a positive integer while the database FK checked only
        that the account existed -- so this payload was accepted and the
        deduction pointed at a stranger's account.
        """
        with app.app_context():
            profile = _create_profile(seed_user)
            pre_tax = db.session.query(PaycheckLineKind).filter_by(name="pre_tax_deduction").one()
            flat_method = db.session.query(CalcMethod).filter_by(name="flat").one()
            victim_account_id = seed_second_user["account"].id

            response = auth_client.post(
                f"/salary/{profile.id}/lines",
                data={
                    "name": "Forged Feed",
                    "paycheck_line_kind_id": pre_tax.id,
                    "calc_method_id": flat_method.id,
                    "amount": "200.00",
                    "target_account_id": str(victim_account_id),
                },
            )

            assert response.status_code == 404
            assert db.session.query(PaycheckLine).filter_by(
                name="Forged Feed",
            ).one_or_none() is None

    def test_add_deduction_still_accepts_the_users_own_target_account(
        self, app, auth_client, seed_user, seed_periods
    ):
        """The ownership guard refuses a stranger's account and NOTHING else.

        Paired with the refusal above deliberately.  A 404 from the ownership
        gate and a 404 from a broken URL map are indistinguishable, so a
        refusal test alone would still pass if the guard rejected every
        account -- or if the route stopped existing.  This asserts the door is
        open for the legitimate payload the form actually posts.
        """
        with app.app_context():
            profile = _create_profile(seed_user)
            pre_tax = db.session.query(PaycheckLineKind).filter_by(name="pre_tax_deduction").one()
            flat_method = db.session.query(CalcMethod).filter_by(name="flat").one()

            response = auth_client.post(
                f"/salary/{profile.id}/lines",
                data={
                    "name": "Own Feed",
                    "paycheck_line_kind_id": pre_tax.id,
                    "calc_method_id": flat_method.id,
                    "amount": "200.00",
                    "target_account_id": str(seed_user["account"].id),
                },
                follow_redirects=True,
            )

            assert response.status_code == 200
            saved = db.session.query(PaycheckLine).filter_by(
                name="Own Feed",
            ).one()
            assert saved.target_account_id == seed_user["account"].id

    def test_update_deduction_refuses_repointing_to_another_users_account(
        self, app, auth_client, seed_user, seed_second_user, seed_periods
    ):
        """The EDIT door refuses a forged re-point too, and keeps the old link.

        The create and update doors load through different schemas
        (``_line_schema`` / ``_line_update_schema``) and the update
        one writes through the ``_LINE_UPDATE_FIELDS`` allowlist, so
        guarding only the create door would leave the same forged FK reachable
        one route over -- which is how this class of defect survives a fix.
        """
        with app.app_context():
            profile = _create_profile(seed_user)
            pre_tax = db.session.query(PaycheckLineKind).filter_by(name="pre_tax_deduction").one()
            flat_method = db.session.query(CalcMethod).filter_by(name="flat").one()
            own_account_id = seed_user["account"].id

            deduction = PaycheckLine(
                salary_profile_id=profile.id,
                paycheck_line_kind_id=pre_tax.id,
                calc_method_id=flat_method.id,
                name="Retirement Feed",
                amount=Decimal("200.00"),
                target_account_id=own_account_id,
            )
            db.session.add(deduction)
            db.session.commit()

            response = auth_client.post(
                f"/salary/lines/{deduction.id}/edit",
                data={
                    "name": "Retirement Feed",
                    "paycheck_line_kind_id": pre_tax.id,
                    "calc_method_id": flat_method.id,
                    "amount": "200.00",
                    "target_account_id": str(seed_second_user["account"].id),
                },
            )

            assert response.status_code == 404
            db.session.refresh(deduction)
            assert deduction.target_account_id == own_account_id

    def test_update_deduction_percentage_conversion(
        self, app, auth_client, seed_user, seed_periods
    ):
        """Editing a percentage deduction correctly converts input to decimal."""
        with app.app_context():
            profile = _create_profile(seed_user)
            pre_tax = db.session.query(PaycheckLineKind).filter_by(name="pre_tax_deduction").one()
            pct_method = db.session.query(CalcMethod).filter_by(name="percentage").one()

            deduction = PaycheckLine(
                salary_profile_id=profile.id,
                paycheck_line_kind_id=pre_tax.id,
                calc_method_id=pct_method.id,
                name="401k Pct",
                amount=Decimal("0.06"),
            )
            db.session.add(deduction)
            db.session.commit()

            response = auth_client.post(
                f"/salary/lines/{deduction.id}/edit",
                data={
                    "name": "401k Pct",
                    "paycheck_line_kind_id": pre_tax.id,
                    "calc_method_id": pct_method.id,
                    "amount": "8",
                },
                follow_redirects=True,
            )

            assert response.status_code == 200

            db.session.refresh(deduction)
            assert deduction.amount == Decimal("0.08"), (
                f"Expected 0.08 (8% converted to decimal), got {deduction.amount}"
            )

    def test_update_deduction_other_user_blocked(
        self, app, auth_client, seed_user, seed_periods
    ):
        """POST /salary/lines/<id>/edit on another user's deduction is rejected."""
        with app.app_context():
            other = _create_other_user_profile()
            pre_tax = db.session.query(PaycheckLineKind).filter_by(name="pre_tax_deduction").one()
            flat_method = db.session.query(CalcMethod).filter_by(name="flat").one()

            other_ded = PaycheckLine(
                salary_profile_id=other["profile"].id,
                paycheck_line_kind_id=pre_tax.id,
                calc_method_id=flat_method.id,
                name="Other 401k",
                amount=Decimal("100.00"),
            )
            db.session.add(other_ded)
            db.session.commit()
            orig_amount = other_ded.amount

            response = auth_client.post(
                f"/salary/lines/{other_ded.id}/edit",
                data={
                    "name": "Hacked",
                    "paycheck_line_kind_id": pre_tax.id,
                    "calc_method_id": flat_method.id,
                    "amount": "9999.00",
                },
            )

            # Security: 404 for both not-found and not-yours.
            assert response.status_code == 404

            db.session.expire_all()
            after = db.session.get(PaycheckLine, other_ded.id)
            assert after.amount == orig_amount, (
                "IDOR attack modified another user's deduction!"
            )
            assert after.name == "Other 401k", (
                "IDOR attack modified another user's deduction name!"
            )

    def test_add_percentage_deduction_converts_input(
        self, app, auth_client, seed_user, seed_periods
    ):
        """Percentage input (6) is converted to decimal (0.06) for storage."""
        with app.app_context():
            profile = _create_profile(seed_user)
            pre_tax = db.session.query(PaycheckLineKind).filter_by(name="pre_tax_deduction").one()
            pct_method = db.session.query(CalcMethod).filter_by(name="percentage").one()

            auth_client.post(
                f"/salary/{profile.id}/lines",
                data={
                    "name": "401k Match",
                    "paycheck_line_kind_id": pre_tax.id,
                    "calc_method_id": pct_method.id,
                    "amount": "6",
                },
                follow_redirects=True,
            )

            ded = (
                db.session.query(PaycheckLine)
                .filter_by(salary_profile_id=profile.id, name="401k Match")
                .one()
            )
            assert ded.amount == Decimal("0.06")

    def test_add_deduction_zero_inflation_rate_stored(
        self, app, auth_client, seed_user, seed_periods
    ):
        """A submitted 0% inflation rate stores as exact zero, not dropped.

        Zero is a value, not a missing field: the route's percent-to-
        fraction conversion must run for ``0`` (``0 / 100 == 0``) and
        persist ``Decimal("0")`` rather than skipping it on falsiness.
        """
        with app.app_context():
            profile = _create_profile(seed_user)
            pre_tax = db.session.query(PaycheckLineKind).filter_by(name="pre_tax_deduction").one()
            flat_method = db.session.query(CalcMethod).filter_by(name="flat").one()

            auth_client.post(
                f"/salary/{profile.id}/lines",
                data={
                    "name": "HSA",
                    "paycheck_line_kind_id": pre_tax.id,
                    "calc_method_id": flat_method.id,
                    "amount": "100.00",
                    "inflation_enabled": "on",
                    "inflation_rate": "0",
                },
                follow_redirects=True,
            )

            ded = (
                db.session.query(PaycheckLine)
                .filter_by(salary_profile_id=profile.id, name="HSA")
                .one()
            )
            assert ded.inflation_rate == Decimal("0")


class TestEarningLinesThroughTheDoor:
    """The one line door authors an EARNING kind (plan step salary:R18-b, ruling R-SAL38).

    The engine's arithmetic for the two earning kinds is graded in
    ``tests/test_services/test_paycheck_calculator/test_the_gross.py``
    (``TestEarningLines``); what is graded HERE
    is the door: that the section offers the four kinds under the
    vocabulary's labels, that a taxable earning added through the form
    reaches the regeneration and raises the paycheck template's amount (the
    one stored figure, ``balance:X-au-e``), and that the one rule which
    differs by side -- a target account -- is refused for an earning and
    nothing is written.
    """

    def test_the_section_offers_the_four_kinds_under_their_labels(
        self, app, auth_client, seed_user, seed_periods,
    ):
        """The kind select renders every kind in waterfall order, labelled by the vocabulary."""
        with app.app_context():
            profile = _create_profile(seed_user)
            html = auth_client.get(f"/salary/{profile.id}/edit").data.decode()
            select = re.search(
                r'<select name="paycheck_line_kind_id".*?</select>', html, re.S,
            ).group(0)
            options = re.findall(r'<option value="(\d+)">([^<]+)</option>', select)
            assert [label for _id, label in options] == [
                "Taxable earning", "Pre-tax deduction",
                "Post-tax deduction", "After-tax earning",
            ]
            assert [int(kind_id) for kind_id, _label in options] == [
                ref_cache.paycheck_line_kind_id(member) for member in PaycheckLineKindEnum
            ]
            assert "Payroll lines" in html and "Deductions</h6>" not in html

    def test_a_taxable_earning_added_by_the_form_raises_the_regenerated_paycheck(
        self, app, auth_client, seed_user, seed_periods,
    ):
        """A $45 taxable line: the section labels it, the priced gross carries it, the template's amount rises.

        The developer's own line (R-SAL38's facts): ``$45.00`` gross, taxable,
        on the first paycheck of every month.  Today is frozen at 2026-03-20
        in this module and the paycheck of Mar 13 is March's first payday,
        so the current paycheck carries it: gross rises by exactly ``$45.00``
        and the net by less (it is taxed), and the regeneration's stored net
        is the priced one.
        """
        with app.app_context():
            profile = _create_profile(seed_user)
            ctx = BalanceContext.build(seed_user["user"].id)
            current = ctx.calendar().period_containing(date(2026, 3, 20))
            before = ctx.paychecks().for_profile(profile).at(current)
            taxable_kind = ref_cache.paycheck_line_kind_id(PaycheckLineKindEnum.TAXABLE_EARNING)

            payload = _line_form(
                "Phone Allowance", amount="45.00",
                **_cadence_payload(
                    RecurrenceUnitEnum.MONTH, PeriodPlacementEnum.PERIOD_STARTING_ON_OR_AFTER,
                ),
            )
            payload["paycheck_line_kind_id"] = str(taxable_kind)
            response = auth_client.post(
                f"/salary/{profile.id}/lines", data=payload, follow_redirects=True,
            )
            assert response.status_code == 200
            html = response.data.decode()
            assert "Phone Allowance" in html
            assert "Taxable earning" in html

            db.session.expire_all()
            profile = db.session.get(SalaryProfile, profile.id)
            saved = db.session.query(PaycheckLine).filter_by(name="Phone Allowance").one()
            assert saved.paycheck_line_kind_id == taxable_kind
            ctx = BalanceContext.build(seed_user["user"].id)
            priced = ctx.paychecks().for_profile(profile).at(current)
            assert priced.earnings.base_biweekly == before.earnings.gross_biweekly
            assert priced.earnings.gross_biweekly == before.earnings.gross_biweekly + Decimal("45.00")
            assert [(l.name, l.amount) for l in priced.earnings.taxable] == [
                ("Phone Allowance", Decimal("45.00")),
            ]
            # The deposit rises by the line less what it is taxed (this module's
            # seeded profile carries no tax configs, so here the two are equal;
            # the taxed figures are the engine suite's to grade).
            assert priced.earnings.net_pay - before.earnings.net_pay == (
                Decimal("45.00") - (priced.taxes.total - before.taxes.total)
            )
            assert profile.template.default_amount == priced.earnings.net_pay, (
                "the regeneration stated a net that did not carry the earning line"
            )

    def test_a_target_account_on_an_earning_is_refused_and_nothing_is_written(
        self, app, auth_client, seed_user, seed_periods,
    ):
        """An earning funds no account: the door refuses the pair and writes no line.

        The contribution feed reads every active line's ``target_account_id``
        as a payroll contribution into that account; an earning with a target
        would feed it money nobody contributes, so the schema refuses the
        pair (IDs for logic: the side is read through the kind vocabulary).
        The same payload with a deduction kind is the positive control.
        """
        with app.app_context():
            profile = _create_profile(seed_user)
            after_tax_kind = ref_cache.paycheck_line_kind_id(PaycheckLineKindEnum.AFTER_TAX_EARNING)
            payload = _line_form("Stipend", amount="50.00")
            payload["paycheck_line_kind_id"] = str(after_tax_kind)
            payload["target_account_id"] = str(seed_user["account"].id)

            response = auth_client.post(
                f"/salary/{profile.id}/lines", data=payload, follow_redirects=True,
            )
            assert response.status_code == 200
            assert b"Only a deduction can fund an account" in response.data
            assert db.session.query(PaycheckLine).filter_by(name="Stipend").count() == 0

            # Positive control: the identical payload as a post-tax deduction is written.
            payload["paycheck_line_kind_id"] = str(
                ref_cache.paycheck_line_kind_id(PaycheckLineKindEnum.POST_TAX_DEDUCTION),
            )
            response = auth_client.post(
                f"/salary/{profile.id}/lines", data=payload, follow_redirects=True,
            )
            assert response.status_code == 200
            saved = db.session.query(PaycheckLine).filter_by(name="Stipend").one()
            assert saved.target_account_id == seed_user["account"].id


    @pytest.mark.parametrize(
        "earning_kind",
        [PaycheckLineKindEnum.TAXABLE_EARNING, PaycheckLineKindEnum.AFTER_TAX_EARNING],
        ids=lambda member: member.value,
    )
    def test_flipping_a_funded_deduction_to_an_earning_is_refused_at_the_update_door(
        self, app, auth_client, seed_user, seed_periods, earning_kind,
    ):
        """A stored deduction WITH a target cannot become an earning that keeps it -- posted or omitted.

        The update route writes only the keys the payload carries (an absent
        key leaves the stored value alone), so the crafted shape -- the kind
        flipped, the target key OMITTED -- passes the schema's posted-pair
        rule and would have left the stored target on an earning; the route
        judges the EFFECTIVE pair, posted kind beside the target the row will
        carry, with the same sentence (an adversarial review of plan step
        salary:R18-b).  Both shapes are driven and both leave the row exactly
        as it was.  The positive control: the same edit with the target
        CLEARED is accepted and the row becomes an earning with no target.
        """
        with app.app_context():
            profile = _create_profile(seed_user)
            pre_tax = ref_cache.paycheck_line_kind_id(PaycheckLineKindEnum.PRE_TAX_DEDUCTION)
            flat_method = db.session.query(CalcMethod).filter_by(name="flat").one()
            line = PaycheckLine(
                salary_profile_id=profile.id, paycheck_line_kind_id=pre_tax,
                calc_method_id=flat_method.id, name="Retirement Feed",
                amount=Decimal("200.00"), target_account_id=seed_user["account"].id,
            )
            db.session.add(line)
            db.session.commit()
            line_id, version = line.id, line.version_id
            payload = _line_form("Retirement Feed", amount="200.00")
            payload["version_id"] = str(version)
            payload["paycheck_line_kind_id"] = str(ref_cache.paycheck_line_kind_id(earning_kind))

            # (1) the target posted with the flipped kind: the cross-field rule.
            posted = {**payload, "target_account_id": str(seed_user["account"].id)}
            response = auth_client.post(
                f"/salary/lines/{line_id}/edit", data=posted, follow_redirects=True,
            )
            assert response.status_code == 200
            assert b"Only a deduction can fund an account" in response.data

            # (2) the target key OMITTED with the flipped kind: the route's
            # effective-pair rule, worded identically.
            omitted = {k: v for k, v in payload.items() if k != "target_account_id"}
            response = auth_client.post(
                f"/salary/lines/{line_id}/edit", data=omitted, follow_redirects=True,
            )
            assert response.status_code == 200
            assert b"Only a deduction can fund an account" in response.data

            db.session.expire_all()
            stored = db.session.get(PaycheckLine, line_id)
            assert stored.paycheck_line_kind_id == pre_tax
            assert stored.target_account_id == seed_user["account"].id
            assert stored.version_id == version

            # Positive control: the target cleared, the flip is accepted.
            cleared = {**payload, "target_account_id": ""}
            response = auth_client.post(
                f"/salary/lines/{line_id}/edit", data=cleared, follow_redirects=True,
            )
            assert response.status_code == 200
            db.session.expire_all()
            stored = db.session.get(PaycheckLine, line_id)
            assert stored.paycheck_line_kind_id == ref_cache.paycheck_line_kind_id(earning_kind)
            assert stored.target_account_id is None
