"""The helpers and constants every part of this package shares: the module-level helpers of
``tests/test_routes/test_retirement.py``, moved verbatim by ``tools/split``.
"""

import re
from datetime import date
from decimal import Decimal

from app import ref_cache
from app.enums import EmployerContributionTypeEnum
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
from app.services import account_service
from app.models.ref import AccountType, FilingStatus, TransactionType
from app.utils.dates import display_today
from tests._test_helpers import make_every_period_rule, make_recurring_raise, start_test_pay_list


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
        is_active=True,
    )
    db_session.add(template)
    db_session.flush()
    # The definition first, then the cadence onto it (plan step R-F6).
    rule = make_every_period_rule(db_session, template)

    profile = SalaryProfile(
        period_end_days_before_payday=6,
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


class _TestTheRailProbesARaisesEndYearMembers:
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
            profile.id, db.session, effective_date=date(effective, 1, 1),
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


class _TestTheRailSavesARaisesEndYearMembers:
    """The rail-save helpers ``test_the_rails_lost_race.py`` reads as well."""

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
            profile.id, db.session, effective_date=date(effective, effective_month, 1),
            terminal_year=terminal_year,
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
