"""The helpers and constants every part of this package shares: the module-level helpers of
``tests/test_routes/test_salary.py``, moved verbatim by ``tools/split``.
"""

from datetime import date
import re
from decimal import Decimal

from app.extensions import db
from app.models.salary_profile import SalaryProfile
from app.models.paycheck_line import PaycheckLine
from app.services import pay_period_write
from app.models.pay_period import PayPeriod
from app.models.transaction_template import TransactionTemplate
from app.models.user import User, UserSettings
from app.models.scenario import Scenario
from app.models.category import Category
from app.models.ref import AccountType, CalcMethod, PaycheckLineKind, FilingStatus, TransactionType
from app import ref_cache
from app.services.auth_service import hash_password

from app.services import account_service

from tests._test_helpers import (
    rhythm_of,
    make_line_cadence_rule,
    make_every_period_rule,
    open_owner_calendar,
    start_test_pay_list,
)


def _create_profile(seed_user):
    """Helper: create a salary profile with linked template and recurrence."""
    filing_status = db.session.query(FilingStatus).filter_by(name="single").one()
    income_type = db.session.query(TransactionType).filter_by(name="Income").one()

    # Find or create Salary category.
    cat = (
        db.session.query(Category)
        .filter_by(user_id=seed_user["user"].id, group_name="Income", item_name="Salary")
        .first()
    )
    if not cat:
        cat = Category(user_id=seed_user["user"].id, group_name="Income", item_name="Salary")
        db.session.add(cat)
        db.session.flush()

    template = TransactionTemplate(
        user_id=seed_user["user"].id,
        account_id=seed_user["account"].id,
        category_id=cat.id,
        transaction_type_id=income_type.id,
        name="Day Job",
        default_amount=Decimal("75000.00") / 26,
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


def _create_other_user_profile():
    """Create a second user with a salary profile.

    Returns:
        dict with keys: user, profile.
    """
    from datetime import date as _date  # pylint: disable=import-outside-toplevel

    other_user = User(
        email="other@shekel.local",
        password_hash=hash_password("otherpass"),
        display_name="Other User",
    )
    db.session.add(other_user)
    db.session.flush()

    settings = UserSettings(user_id=other_user.id)
    db.session.add(settings)

    # The owner's calendar, so the account factory can resolve an anchor
    # period without raising ValidationError.
    # Through the writer that owns the table (plan step pay_calendar:C4-b-1).
    _bootstrap = open_owner_calendar(other_user.id, _date(2024, 1, 5))[0]

    checking_type = db.session.query(AccountType).filter_by(name="Checking").one()
    account = account_service.create_account(
        account_service.AccountSpec(
            user_id=other_user.id,
            account_type_id=checking_type.id,
            name="Other Checking",
            anchor_balance=Decimal("500.00"),
        ),
    )

    scenario = Scenario(
        user_id=other_user.id,
        name="Baseline",
        is_baseline=True,
    )
    db.session.add(scenario)
    db.session.flush()

    filing_status = db.session.query(FilingStatus).filter_by(name="single").one()
    income_type = db.session.query(TransactionType).filter_by(name="Income").one()

    cat = Category(user_id=other_user.id, group_name="Income", item_name="Salary")
    db.session.add(cat)
    db.session.flush()

    template = TransactionTemplate(
        user_id=other_user.id,
        account_id=account.id,
        category_id=cat.id,
        transaction_type_id=income_type.id,
        name="Other Job",
        default_amount=Decimal("60000.00") / 26,
        is_active=True,
    )
    db.session.add(template)
    db.session.flush()
    # The definition first, then the cadence onto it (plan step R-F6).
    rule = make_every_period_rule(db.session, template)

    profile = SalaryProfile(
        user_id=other_user.id,
        scenario_id=scenario.id,
        template_id=template.id,
        filing_status_id=filing_status.id,
        name="Other Job",
        state_code="NC",
    )
    db.session.add(profile)
    start_test_pay_list(profile, Decimal("2307.69"))  # $60,000.00 a year / 26
    db.session.commit()

    return {"user": other_user, "profile": profile}


def _respace_paydays(db, user_id, cadence_days):
    """Re-generate the owner's schedule AT *cadence_days*, committed.

    Both the persisted cadence and the paydays move, through the one write
    door, so the fixture is a state the application can actually produce -- a
    schedule row saying 7 beside 14-day paydays is not.

    Args:
        db: The test session wrapper.
        user_id: The owner to respace.
        cadence_days: The new days between paydays.
    """
    pay_period_write.record_paydays(
        user_id=user_id,
        first_payday=date(2026, 1, 2),
        num_periods=10,
        rhythm=rhythm_of(cadence_days),
        retiring_ids={
            pid for (pid,) in db.session.query(PayPeriod.id)
            .filter_by(user_id=user_id)
        },
    )
    db.session.commit()


# ── The deduction form's cadence (plan step salary:R15-c) ─────────


def _cadence_payload(unit, placement, interval="1", ceiling=""):
    """The four cadence controls as the deduction form posts them.

    Every control the shared partial renders is posted, including an EMPTY
    ceiling: the box is enabled beside a paycheck unit and a browser submits
    an empty enabled control as ``""``, which the schema loads as a stated
    ``None``.  ``ceiling=None`` omits the key, which is what a DISABLED
    control (beside a monthly or yearly unit) produces.

    Args:
        unit: The ``RecurrenceUnitEnum`` member, or ``None`` for "Does not
            repeat" (posted as the empty option's ``""``).
        placement: The ``PeriodPlacementEnum`` member.
        interval: The interval box's text.
        ceiling: The ceiling box's text, or ``None`` to omit the key.

    Returns:
        dict: The submission.
    """
    payload = {
        "recurrence_unit": (
            "" if unit is None else str(ref_cache.recurrence_unit_id(unit))
        ),
        "interval_n": interval,
        "recurrence_placement": str(ref_cache.period_placement_id(placement)),
    }
    if ceiling is not None:
        payload["max_per_month"] = ceiling
    return payload


def _prefill_attributes(html, deduction_id):
    """The nine ``data-line-*`` cadence and span attributes on one row's edit button.

    The four cadence attributes since plan step salary:R15-c; the five span
    ones (the start, its nominal day, the bound's mode and its two values)
    since salary:R18-c.
    """
    match = re.search(
        r'<button[^>]*data-line-edit="%d"[^>]*>' % deduction_id, html, re.S,
    )
    assert match, f"no edit button for deduction {deduction_id}"
    button = match.group(0)
    found = {}
    for key in (
        "unit-id", "interval", "placement-id", "max-per-month",
        "starts-on", "repeats-on", "end-mode", "end-date", "max-occurrences",
    ):
        value = re.search(r'data-line-%s="([^"]*)"' % key, button)
        assert value, f"the edit button carries no data-line-{key}"
        found[key] = value.group(1)
    return found


def _authored_columns(rule):
    """Every column the write door authors, for a column-for-column comparison."""
    return {
        "unit_id": rule.unit_id,
        "placement_id": rule.placement_id,
        "shift_id": rule.shift_id,
        "interval_n": rule.interval_n,
        "starts_on": rule.starts_on,
        "nominal_day": rule.nominal_day,
        "max_per_month": rule.max_per_month,
        "end_date": rule.end_date,
        "max_occurrences": rule.max_occurrences,
    }


def _a_line(profile, name, per_year=26):
    """A flat pre-tax line on *profile*, with the migrated rule for a 24 / 12."""
    pre_tax = db.session.query(PaycheckLineKind).filter_by(name="pre_tax_deduction").one()
    flat_method = db.session.query(CalcMethod).filter_by(name="flat").one()
    deduction = PaycheckLine(
        salary_profile_id=profile.id,
        paycheck_line_kind_id=pre_tax.id,
        calc_method_id=flat_method.id,
        name=name,
        amount=Decimal("100.00"),
    )
    db.session.add(deduction)
    db.session.flush()
    if per_year != 26:
        make_line_cadence_rule(db.session, deduction, per_year)
    db.session.commit()
    return deduction


def _line_form(name, amount="100.00", **cadence):
    """Every non-cadence control the line form renders, plus *cadence*.

    Built to the FORM's control list rather than hand-picked: a browser posts
    every enabled control the template renders, empties included, and
    ``TestDeductionCadenceForm.test_the_payload_is_what_the_form_renders``
    pins this helper's keys to the rendered form in both directions.  The
    SPAN controls (plan step salary:R18-c) post what the rendered form posts
    beside a cadence: a BLANK start (the opening payday), the bound's select
    on its rendered ``never`` (it has no empty option), and the bound's two
    value boxes empty; a case that states a span passes it in *cadence*.
    """
    pre_tax = db.session.query(PaycheckLineKind).filter_by(name="pre_tax_deduction").one()
    flat_method = db.session.query(CalcMethod).filter_by(name="flat").one()
    return {
        "version_id": "",
        "name": name,
        "paycheck_line_kind_id": str(pre_tax.id),
        "target_account_id": "",
        "calc_method_id": str(flat_method.id),
        "amount": amount,
        "annual_cap": "",
        "inflation_rate": "",
        "inflation_effective_month": "",
        "starts_on": "",
        "repeats_on": "",
        "recurrence_end_mode": "never",
        "end_date": "",
        "max_occurrences": "",
        **cadence,
    }


class _TestDeductionCadenceFormMembers:
    """The deduction form authors a line's cadence through the shared controls.

    Plan step **salary:R15-c** (rulings **R-SAL31**, **R-SAL35**,
    **R-SAL36**, **R-SAL37**): the 26 / 24 / 12 select is the recurrence
    partial's four cadence controls, the route derives the first occurrence
    (R-SAL30) and canonicalises the every-paycheck spelling to no rule
    (R-SAL29), and every rule the form writes is compared column for column
    against the one the migration's shared builder writes for the same
    cadence -- so a form-authored line and a migrated line are one shape.
    The seeded schedule opens 2026-01-02 (a Friday, not a 1st), so the
    derived first occurrence differs per unit.
    """


# ── Helpers for Negative-Path Tests ───────────────────────────────


def _create_second_user_salary_profile(second_user_data):
    """Create a salary profile for the second_user fixture (IDOR testing).

    Mirrors _create_other_user_profile() but reuses the second_user
    fixture data instead of creating a new user.

    Args:
        second_user_data: Dict from the second_user conftest fixture.

    Returns:
        SalaryProfile: the created profile.
    """
    filing_status = db.session.query(FilingStatus).filter_by(name="single").one()
    income_type = db.session.query(TransactionType).filter_by(name="Income").one()

    cat = (
        db.session.query(Category)
        .filter_by(
            user_id=second_user_data["user"].id,
            group_name="Income",
            item_name="Salary",
        )
        .first()
    )
    if not cat:
        cat = Category(
            user_id=second_user_data["user"].id,
            group_name="Income",
            item_name="Salary",
        )
        db.session.add(cat)
        db.session.flush()

    template = TransactionTemplate(
        user_id=second_user_data["user"].id,
        account_id=second_user_data["account"].id,
        category_id=cat.id,
        transaction_type_id=income_type.id,
        name="Other Job",
        default_amount=Decimal("60000.00") / 26,
        is_active=True,
    )
    db.session.add(template)
    db.session.flush()
    # The definition first, then the cadence onto it (plan step R-F6).
    rule = make_every_period_rule(db.session, template)

    profile = SalaryProfile(
        user_id=second_user_data["user"].id,
        scenario_id=second_user_data["scenario"].id,
        template_id=template.id,
        filing_status_id=filing_status.id,
        name="Other Job",
        state_code="NC",
    )
    db.session.add(profile)
    start_test_pay_list(profile, Decimal("2307.69"))  # $60,000.00 a year / 26
    db.session.commit()
    return profile


# ── Cockpit (rebuilt /salary landing page) ────────────────────────


def _create_inactive_profile(seed_user, name="Old Job"):
    """Create an INACTIVE salary profile for the seed user.

    Distinct name so it does not collide with ``_create_profile``'s
    ``uq_salary_profiles_user_scenario_name`` on the same scenario.

    Returns:
        SalaryProfile: the created inactive profile.
    """
    income_type = db.session.query(TransactionType).filter_by(name="Income").one()
    filing_status = db.session.query(FilingStatus).filter_by(name="single").one()
    cat = (
        db.session.query(Category)
        .filter_by(user_id=seed_user["user"].id, group_name="Income", item_name="Salary")
        .first()
    )
    if not cat:
        cat = Category(user_id=seed_user["user"].id, group_name="Income", item_name="Salary")
        db.session.add(cat)
        db.session.flush()
    template = TransactionTemplate(
        user_id=seed_user["user"].id,
        account_id=seed_user["account"].id,
        category_id=cat.id,
        transaction_type_id=income_type.id,
        name=name,
        default_amount=Decimal("40000.00") / 26,
        is_active=False,
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
        name=name,
        state_code="NC",
        is_active=False,
    )
    db.session.add(profile)
    start_test_pay_list(profile, Decimal("1538.46"))  # $40,000.00 a year / 26
    db.session.commit()
    return profile


def _capture_render(app, auth_client, url, template_name):
    """Return the (response, context) captured for ``template_name`` at ``url``.

    Uses Flask's ``template_rendered`` signal to read the exact context the
    route handed ``render_template`` (the cockpit/projection templates are
    thin in P1; asserting on the context is the precise contract check).
    """
    # Pylint: import-outside-toplevel -- deferred import is the file-wide
    # test convention.
    from flask import template_rendered  # pylint: disable=import-outside-toplevel

    recorded = []

    def _record(sender, template, context, **extra):
        recorded.append((template, context))

    template_rendered.connect(_record, app)
    try:
        response = auth_client.get(url)
    finally:
        template_rendered.disconnect(_record, app)
    matches = [c for t, c in recorded if t.name == template_name]
    return response, (matches[0] if matches else None)
