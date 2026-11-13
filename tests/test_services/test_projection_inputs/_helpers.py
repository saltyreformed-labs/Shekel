"""The helpers and constants every part of this package shares: the module-level helpers of
``tests/test_services/test_projection_inputs.py``, moved verbatim by ``tools/split``.
"""

from dataclasses import dataclass
from decimal import Decimal

from app import ref_cache
from app.enums import CalcMethodEnum, PaycheckLineKindEnum
from app.models.paycheck_line import PaycheckLine
from app.models.salary_profile import SalaryProfile
from tests._test_helpers import start_test_pay_list


@dataclass
class _FakeInvestmentParams:
    assumed_annual_return: Decimal
    annual_contribution_limit: Decimal
    employer_contribution_type_id: int
    employer_flat_percentage: Decimal = Decimal("0")
    employer_match_percentage: Decimal = Decimal("0")
    employer_match_cap_percentage: Decimal = Decimal("0")


def _seed_deductions_fixture(app, db, seed_user, seed_second_user):
    """Seed two investment accounts + active and inactive deductions.

    Acct A has one active deduction (worth 500).  Acct B has one
    active deduction (worth 250).  Other user has an active
    deduction on their own account.  An additional inactive
    deduction on Acct A and a deduction on an inactive salary
    profile must NOT appear in the loader's results.

    Returns a dict of ids the caller's assertions consume.  Must run
    inside an active ``app.app_context()`` so the inserts share the
    test's session.
    """
    from app.enums import AcctTypeEnum
    from app.models.account import Account
    from app.models.ref import FilingStatus

    user_id = seed_user["user"].id
    other_user_id = seed_second_user["user"].id
    scenario_id = seed_user["scenario"].id
    other_scenario_id = seed_second_user["scenario"].id

    retire_type_id = ref_cache.acct_type_id(AcctTypeEnum.K401)
    flat_id = ref_cache.calc_method_id(CalcMethodEnum.FLAT)
    timing_id = ref_cache.paycheck_line_kind_id(
        PaycheckLineKindEnum.PRE_TAX_DEDUCTION,
    )
    filing_status_id = (
        db.session.query(FilingStatus).filter_by(name="single").one().id
    )

    acct_a = Account(
        user_id=user_id, name="Acct A", account_type_id=retire_type_id,
    )
    acct_b = Account(
        user_id=user_id, name="Acct B", account_type_id=retire_type_id,
    )
    other_acct = Account(
        user_id=other_user_id, name="Other Acct",
        account_type_id=retire_type_id,
    )
    db.session.add_all([acct_a, acct_b, other_acct])
    db.session.flush()

    active_profile = SalaryProfile(
        period_end_days_before_payday=6,
        user_id=user_id, scenario_id=scenario_id,
        name="Active",
        state_code="NC",
        filing_status_id=filing_status_id, is_active=True,
    )
    inactive_profile = SalaryProfile(
        period_end_days_before_payday=6,
        user_id=user_id, scenario_id=scenario_id,
        name="Inactive",
        state_code="NC",
        filing_status_id=filing_status_id, is_active=False,
    )
    other_profile = SalaryProfile(
        period_end_days_before_payday=6,
        user_id=other_user_id, scenario_id=other_scenario_id,
        name="Other",
        state_code="NC",
        filing_status_id=filing_status_id, is_active=True,
    )
    db.session.add_all([active_profile, inactive_profile, other_profile])
    start_test_pay_list(active_profile, Decimal("3846.15"))  # $100,000.00 a year / 26
    start_test_pay_list(inactive_profile, Decimal("3076.92"))  # $80,000.00 a year / 26
    start_test_pay_list(other_profile, Decimal("3846.15"))  # $100,000.00 a year / 26
    db.session.flush()

    active_a = PaycheckLine(
        salary_profile_id=active_profile.id, target_account_id=acct_a.id,
        name="A", amount=Decimal("500"), calc_method_id=flat_id,
        paycheck_line_kind_id=timing_id, is_active=True,
    )
    active_b = PaycheckLine(
        salary_profile_id=active_profile.id, target_account_id=acct_b.id,
        name="B", amount=Decimal("250"), calc_method_id=flat_id,
        paycheck_line_kind_id=timing_id, is_active=True,
    )
    inactive_dedn = PaycheckLine(
        salary_profile_id=active_profile.id, target_account_id=acct_a.id,
        name="A-inactive", amount=Decimal("999"), calc_method_id=flat_id,
        paycheck_line_kind_id=timing_id, is_active=False,
    )
    inactive_profile_dedn = PaycheckLine(
        salary_profile_id=inactive_profile.id,
        target_account_id=acct_a.id, name="A-inactive-profile",
        amount=Decimal("888"), calc_method_id=flat_id,
        paycheck_line_kind_id=timing_id, is_active=True,
    )
    other_user_dedn = PaycheckLine(
        salary_profile_id=other_profile.id,
        target_account_id=other_acct.id, name="Other",
        amount=Decimal("777"), calc_method_id=flat_id,
        paycheck_line_kind_id=timing_id, is_active=True,
    )
    db.session.add_all([
        active_a, active_b, inactive_dedn,
        inactive_profile_dedn, other_user_dedn,
    ])
    db.session.flush()
    return {
        "user_id": user_id,
        "other_user_id": other_user_id,
        "acct_a_id": acct_a.id,
        "acct_b_id": acct_b.id,
        "other_acct_id": other_acct.id,
    }
