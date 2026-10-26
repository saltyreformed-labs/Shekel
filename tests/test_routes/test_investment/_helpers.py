"""The helpers and constants every part of this package shares: the module-level helpers of
``tests/test_routes/test_investment.py``, moved verbatim by ``tools/split``.
"""

import json
import re
from datetime import date
from decimal import Decimal

from app import ref_cache
from app.enums import CalcMethodEnum, PaycheckLineKindEnum, EmployerContributionTypeEnum
from app.models.investment_params import InvestmentParams
from app.models.paycheck_line import PaycheckLine
from app.models.ref import AccountType, FilingStatus
from app.models.salary_profile import SalaryProfile
from app.models.transfer_template import TransferTemplate
from app.services import account_service
from app.services.investment_projection import AccountPayrollFeed, InvestmentInputs
from tests._test_helpers import (
    figure_source_columns,
    generate_row_of,
    make_expense_template,
    settle_day_columns,
    start_test_pay_list,
)
from app.services.investment_dashboard_service._context import _ProjectionContext
from tests._test_helpers import open_books_before_the_first_assertion, read_pass_over_paydays
from app.models.amount_ownership import AmountOwnership


def _cards_context(*, limit, ytd, paydays, current_index, as_of, cadence=14,
                   retirement_date=None):
    """Build a REAL ``_ProjectionContext`` for the card helpers' unit cases.

    The card helpers take the shared per-request feed since plan step C2-f2c,
    because the three things they read -- the owner's periods, the period
    covering the clock, and the clock itself -- must all come off ONE read
    pass, and three separate arguments are three ways to pair one pass's
    calendar with another's day.  So a case has to supply a context.

    **Every type here is the real one.**  The calendar is a real
    :class:`~app.services.pay_calendar.PayCalendar` derived from real paydays,
    the pass is a real :class:`~app.services.balance_at.BalanceContext` whose
    pay-calendar memo is filled at construction rather than loaded, and the
    feed is the real frozen dataclass -- so a field these helpers read cannot
    drift from the one the application fills.  Filling the memo is what keeps
    these cases pure: the arithmetic under test is a function of a payday set
    and a day, and a database would only supply those two values less
    legibly.

    **The seeding goes through ONE shared door**
    (:func:`tests._test_helpers.read_pass_over_paydays`, plan finding **P54**,
    ruled by the developer 2026-08-16).  This was the first site to name the
    pass's PRIVATE calendar memo, and plan step ``C2-f2d`` brings two more
    packages wanting the same fixture; N sites reaching into one private field
    is how a memo becomes a de-facto public seam.  That helper's docstring
    carries the two alternatives that were refused and why.

    The fields no card helper reads are filled with the degenerate value their
    type admits.  Nothing here asserts on them, and a helper that started
    reading one would fail loudly rather than silently agreeing with a fake.

    Args:
        limit: The account's ``annual_contribution_limit`` (or ``None``).
        ytd: Contributions already made this calendar year.
        paydays: The owner's paydays, ascending.
        current_index: Which payday's period covers the clock, or ``None``.
        as_of: The read pass's clock.
        cadence: Days between paydays.
        retirement_date: The owner's planned retirement date, or ``None``.

    Returns:
        The ``_ProjectionContext``.
    """
    balance_ctx = read_pass_over_paydays(paydays, cadence, as_of)
    saved = balance_ctx.calendar().saved()
    return _ProjectionContext(
        params=InvestmentParams(annual_contribution_limit=limit),
        current_balance=Decimal("0"),
        projection_start=as_of,
        projection_ytd=ytd,
        projection_seed=Decimal("0"),
        inputs=InvestmentInputs(
            periodic_contribution=Decimal("0"),
            employer_params=None,
            annual_contribution_limit=limit,
            ytd_contributions=ytd,
            ytd_contributions_seed=ytd,
        ),
        shadow_contributions=[],
        feed=AccountPayrollFeed.absent(),
        deductions=[],
        salary_profiles=[],
        balance_ctx=balance_ctx,
        anchor_as_of=None,
        planned_retirement_date=retirement_date,
        current_period=None if current_index is None else saved[current_index],
    )


def _create_investment_account(seed_user, db_session, type_name="401(k)",
                                name="My 401k", balance="50000.00"):
    """Helper to create an investment/retirement account.

    COMMITS rather than flushes (plan step balance:X-i3).  A request cannot see
    an uncommitted row -- it holds its own transaction -- so a fixture that
    only flushed and then issued one was asking the route to read a state no
    browser could produce.  The sibling ``_create_investment_params`` below has
    always committed; this one differed for no stated reason.
    """
    acct_type = db_session.query(AccountType).filter_by(name=type_name).one()
    account = account_service.create_account(
        account_service.AccountSpec(
            user_id=seed_user["user"].id,
            account_type_id=acct_type.id,
            name=name,
            anchor_balance=Decimal(balance),
        ),
    )
    db_session.add(account)
    # Its BOOKS open before anything this fixture dates (plan step X-f3c-2b,
    # ruling **R-HG**): ``create_account`` opens them on the day it asserts --
    # the owner's today -- and this suite settles on or before it.
    open_books_before_the_first_assertion(db_session, account)
    db_session.commit()
    return account


def _create_investment_params(db_session, account_id, **overrides):
    """Helper to create investment params for an account."""
    defaults = {
        "account_id": account_id,
        "assumed_annual_return": Decimal("0.07000"),
        "annual_contribution_limit": Decimal("23500.00"),
        "contribution_limit_year": 2026,
        "employer_contribution_type_id": ref_cache.employer_contribution_type_id(EmployerContributionTypeEnum.NONE),
    }
    defaults.update(overrides)
    params = InvestmentParams(**defaults)
    db_session.add(params)
    db_session.commit()
    return params


def _create_other_investment(second_user, db_session):
    """Create an investment account owned by the second user.

    Builds on the shared second_user fixture. Returns the Account
    (no InvestmentParams -- IDOR tests verify none get created).
    """
    acct_type = db_session.query(AccountType).filter_by(name="401(k)").one()
    account = account_service.create_account(
        account_service.AccountSpec(
            user_id=second_user["user"].id,
            account_type_id=acct_type.id,
            name="Other 401k",
            anchor_balance=Decimal("10000.00"),
        ),
    )
    db_session.add(account)
    # Its BOOKS open before anything this fixture dates (plan step X-f3c-2b,
    # ruling **R-HG**): ``create_account`` opens them on the day it asserts --
    # the owner's today -- and this suite settles on or before it.
    open_books_before_the_first_assertion(db_session, account)
    db_session.commit()
    return account


# ── Tests: Contribution-Aware Dashboard ───────────────────────


def _create_salary_profile(db_session, user_id, scenario_id, funds=None):
    """Create an active salary profile for the test user.

    Args:
        db_session: The session to add on.
        user_id: The owner.
        scenario_id: The scenario the profile belongs to.
        funds: An account id whose employer contribution this job FUNDS
            (**R-SAL5**), or ``None``.  Since plan step **salary:R14-b** an
            employer contribution is priced off the profile
            ``budget.investment_params.salary_profile_id`` names, and an
            account naming none models no employer money at all (developer,
            2026-09-04) -- so a case asserting an employer figure has to say
            which job pays it, exactly as the owner does at the form.
    """
    filing = db_session.query(FilingStatus).filter_by(name="single").one()
    profile = SalaryProfile(
        user_id=user_id,
        scenario_id=scenario_id,
        filing_status_id=filing.id,
        name="Day Job",
        state_code="NC",
        is_active=True,
    )
    db_session.add(profile)
    start_test_pay_list(profile, Decimal("3846.15"))  # $100,000.00 a year / 26
    db_session.flush()
    if funds is not None:
        db_session.query(InvestmentParams).filter_by(
            account_id=funds,
        ).one().salary_profile_id = profile.id
        db_session.flush()
    return profile


def _create_deduction(db_session, profile_id, account_id, amount="500.00"):
    """Create a flat-dollar deduction targeting the investment account."""
    flat_id = ref_cache.calc_method_id(CalcMethodEnum.FLAT)
    timing_id = ref_cache.paycheck_line_kind_id(PaycheckLineKindEnum.PRE_TAX_DEDUCTION)
    ded = PaycheckLine(
        salary_profile_id=profile_id,
        paycheck_line_kind_id=timing_id,
        calc_method_id=flat_id,
        name="401k Contribution",
        amount=Decimal(amount),
        target_account_id=account_id,
        is_active=True,
    )
    db_session.add(ded)
    params = (
        db_session.query(InvestmentParams)
        .filter_by(account_id=account_id).one_or_none()
    )
    if params is not None:
        params.salary_profile_id = profile_id
    db_session.flush()
    return ded


# ── Tests: Contribution Setup Prompt (5.2-3) ─────────────────


def _create_transfer_template(db_session, user_id, from_id, to_id,
                               is_active=True):
    """Create a recurring transfer template targeting an account."""
    from tests._test_helpers import make_every_period_rule

    # Authored through the write door (plan step R7c-b): the two-axis columns
    # are NOT NULL, so a rule naming only a pattern cannot be stored.
    tpl = TransferTemplate(
        user_id=user_id,
        from_account_id=from_id,
        to_account_id=to_id,
        name=f"Contribution {from_id}->{to_id}",
        default_amount=Decimal("200.00"),
        is_active=is_active,
    )
    db_session.add(tpl)
    db_session.flush()
    # The definition first, then the cadence onto it (plan step R-F6).
    rule = make_every_period_rule(db_session, tpl)
    return tpl


# ── Helpers: What-If Chart Data Extraction ──────────────────────


def _extract_data_attr(response_data, attr_name):
    """Extract a JSON data-* attribute value from the chart canvas element.

    Args:
        response_data: Response bytes from the test client.
        attr_name:     The data attribute name (e.g., 'whatif-balances').

    Returns:
        Parsed JSON value (list/dict), or None if not found.
    """
    html = response_data.decode()
    pattern = rf"data-{re.escape(attr_name)}='([^']*)'"
    match = re.search(pattern, html)
    if match:
        return json.loads(match.group(1))
    return None


# ── C8: investment dashboard / growth chart routed through producer ─
#
# Pre-Commit-8 the dashboard() and growth_chart() handlers each built
# their own per-account transaction query and called
# ``balance_calculator.calculate_balances`` directly with no
# ``selectinload(Transaction.entries)``.  The math-layer silent-degrade
# seam (closed in Commit 5) was the only safety net.  When an
# investment account had a Projected expense with cleared debit
# entries (an unusual but valid configuration; the contract is that
# the resolver applies the entries-aware reduction unconditionally
# regardless of account type), the route silently returned
# ``effective_amount``.  Commit 8 routes both handlers through
# ``balance_resolver.balances_for`` so the figure matches the grid and
# every other surface for the same inputs.


def _add_envelope_expense_with_cleared_entries_inv(
    db_session, *, seed_user, account, period, estimated, cleared_amounts,
):
    """Create a Projected envelope expense with cleared debit entries.

    Same shape as the helper used in the savings / accounts / year-end
    C8 tests; copied here so this file stays standalone.  The row is the
    engine's own, of a priced ``is_envelope=True`` definition on *account*
    (:func:`generate_row_of`, plan step balance:X-cf-4) -- the engine puts
    a row on its DEFINITION's account, so the account is stated there.
    These are the entries that produce the F-009 / CRIT-01 silent-degrade
    gap when the consuming query forgets to ``selectinload(entries)``.
    """
    from app.models.transaction_entry import TransactionEntry  # pylint: disable=import-outside-toplevel

    template = make_expense_template(
        db_session, seed_user, amount=estimated,
        name="Investment-side expense", category_key="Groceries",
        is_envelope=True, account=account,
    )
    txn = generate_row_of(template, period)

    for amt in cleared_amounts:
        db_session.add(TransactionEntry(
            **figure_source_columns(),
            transaction_id=txn.id, account_id=txn.account_id, owner_id=txn.user_id,
            user_id=seed_user["user"].id,
            amount=amt,
            description="Cleared purchase",
            purchased_on=date(2026, 5, 15),
            is_credit=False,
            **settle_day_columns(date(2026, 5, 15)),
        ))
    db_session.flush()
    return txn


def _make_projected_shadow_income(
    seed_user, to_account, period, amount, db_session,
):
    """Seed a PROJECTED transfer into the investment account in *period*.

    The shape the double count needed: a projected shadow income is counted
    in the balance the projection seeds from AND is the kind of row the
    growth-engine timeline re-applies.  It stays the fixture because it is
    still the discriminating shape -- under rulings R-AB / R-AF the seed's
    date and the window are disjoint, so the row is counted once, and this
    is where that would fail if either moved.
    """
    from app.enums import StatusEnum  # pylint: disable=import-outside-toplevel
    from app.services import transfer_service  # pylint: disable=import-outside-toplevel

    projected_id = ref_cache.status_id(StatusEnum.PROJECTED)
    cat = seed_user["categories"]["Groceries"]
    xfer = transfer_service.create_transfer(
        transfer_service.TransferSpec(
            user_id=seed_user["user"].id,
            from_account_id=seed_user["account"].id,
            to_account_id=to_account.id,
            pay_period_id=period.id,
            scenario_id=seed_user["scenario"].id,
            amount_ownership=AmountOwnership.own(amount),
            status_id=projected_id,
            category_id=cat.id,
            name="current-period contribution",
        ),
    )
    db_session.commit()
    return xfer
