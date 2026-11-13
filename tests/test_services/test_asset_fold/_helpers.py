"""The helpers and constants every part of this package shares: the module-level helpers of
``tests/test_services/test_asset_fold.py``, moved verbatim by ``tools/split``.
"""

from datetime import date
from decimal import Decimal

from app.extensions import db
from app.models.paycheck_line import PaycheckLine
from app import ref_cache
from app.enums import CalcMethodEnum, PaycheckLineKindEnum
from app.services.balance_at import _asset_fold
from app.services.balance_at._asset_contributions import ContributionInputs
from app.services.investment_projection import AccountPayrollFeed
from app.services.pay_calendar import calendar_for
from app.services.balance_at._context import BalanceContext
from app.services.projection_inputs import load_payroll_feeds, load_investment_params_for_accounts
from tests._test_helpers import (
    make_investment_account,
    make_salary_profile,
    pricing_over,
    reassert_balance_on,
    restate_account_opening,
    settle_instant_on,
)


# An as-of far past every valuation date in this file, so the PLANNED tier
# (which clamps to ``as_of + 1``, ruling R-G) cannot reach any date asserted
# here.  The modelled tiers are graded on their own, exactly as the cash fold's
# oracle grades its tiers apart: a test that mixed them could pass with either
# one wrong.
_LATE_AS_OF = date(2026, 12, 31)


_ZERO = Decimal("0")


def _ctx(seed_user, as_of=_LATE_AS_OF):
    """Return a read-pass context for the seed user at *as_of*."""
    return BalanceContext.build(seed_user["user"].id, as_of=as_of)


def _inputs(params=None, feed=None):
    """Bundle a case's contribution feed the way the seam's callers do."""
    return ContributionInputs(
        investment_params=params,
        feed=AccountPayrollFeed.absent() if feed is None else feed,
    )


def _fold(account, ctx, dates, *, params=None, feed=None):
    """Fold *account* at each of *dates*, returning ``{date: Decimal}``."""
    return _asset_fold.fold_asset_balances(
        account, ctx, list(dates), _inputs(params, feed),
    )


def _view(account, ctx, periods, *, params=None, feed=None):
    """Return *account*'s modelled per-period columns."""
    return _asset_fold.asset_period_view(
        account, ctx, _inputs(params, feed),
    )


def _feed_for(seed_user, account, params=None):
    """Return the account's payroll feed, PRICED as the seam's loader prices it.

    ``ContributionInputs`` holds an
    :class:`~app.services.investment_projection.AccountPayrollFeed` since plan
    step **salary:R14-b** (ruling **R-SAL2**), and it is built by the real
    loader here rather than by hand: what a deduction takes from a paycheck is
    the paycheck ENGINE's answer, so a hand-built feed would grade this file's
    arithmetic instead of the engine's.  It replaced an ``adapt_deductions``
    call that flattened the ORM rows into a shape whose per-period figure this
    tier then re-derived, raise-blind (finding **D45**).

    ``params`` is passed through so the EMPLOYER half is priced too: the gross
    an employer percentage is taken of comes off the profile
    ``investment_params.salary_profile_id`` names (**R-SAL5**), so a case that
    wants employer money needs the link its own ``_salaried_deduction`` wrote.
    """
    user_id = seed_user["user"].id
    return load_payroll_feeds(
        pricing_over(calendar_for(user_id)), [account.id],
        {} if params is None else {account.id: params},
    )[account.id]


def _params_for(account):
    """Return the account's ``InvestmentParams`` through the shared loader."""
    return load_investment_params_for_accounts([account])[account.id]


def _401k(seed_user, period, balance, *, opened_on, books_open_on=None,
          **kwargs):
    """Build a 7%-return 401(k) whose OPENING assertion is on a CHOSEN day.

    ``make_investment_account`` now pins its own opening to the anchor period's
    first day (finding N-77, closed at plan step X-g2a), so this wrapper is no
    longer a compensator for a wall-clock stamp -- it exists because this file's
    whole subject is WHERE an accrual window opens, and several cases need that
    day to be somewhere other than the period's start (mid-period, or on a later
    period's payday).

    Args:
        seed_user: The ``seed_user`` fixture dict.
        period: The anchor period to build against.
        balance: The declared balance.
        opened_on: The civil day the opening ASSERTION is dated.
        books_open_on: The civil day the account's BOOKS open, when a case
            records money moving before the assertion.  ``None`` leaves them
            where ``reassert_balance_on`` puts them, one day before
            *opened_on* -- which is right until a case needs the span between
            the two (plan step X-f3c-2b, ruling **R-HG**: nothing may be dated
            on or before the books).
        **kwargs: Passed through to ``make_investment_account``.

    Returns:
        The created 401(k) account.
    """
    account = make_investment_account(
        seed_user, db.session, period, balance, **kwargs,
    )
    reassert_balance_on(
        db.session, account, settle_instant_on(opened_on),
    )
    if books_open_on is not None:
        restate_account_opening(db.session, account, books_open_on)
    db.session.commit()
    return account


def _salaried_deduction(seed_user, account, amount):
    """Attach an active flat-dollar deduction targeting *account*.

    Module-level rather than a method on the contribution class: three classes
    now build the same feed, and the modelled tier is only LIVE when one exists
    -- a fixture without it cannot tell a partition from a union (finding N-69,
    which is how the first version of the R-R pin was found vacuous).

    **It also records which job FUNDS the account** (plan step
    **salary:R14-b**, ruling **R-SAL5**), which is what
    ``salary:R14-a``'s backfill wrote for exactly this shape: an account with a
    deduction naming it is funded by that deduction's own profile.  Without
    the link the employer half models nothing at all (the developer's
    2026-09-04 ruling), so a case asserting an employer figure would assert
    ``$0.00`` and pass for the wrong reason.  The profile pays a made-up
    ``$78,500.00 / 26 = $3,019.23`` a paycheck, which is the gross those cases
    size a percentage off.
    """
    profile = make_salary_profile(
        seed_user, db.session, pay=Decimal("3019.23"),  # $78,500.00 a year / 26, made up
    )
    db.session.flush()
    deduction = PaycheckLine(
        salary_profile_id=profile.id,
        target_account_id=account.id,
        name="401k deferral",
        amount=Decimal(amount),
        calc_method_id=ref_cache.calc_method_id(CalcMethodEnum.FLAT),
        paycheck_line_kind_id=ref_cache.paycheck_line_kind_id(
            PaycheckLineKindEnum.PRE_TAX_DEDUCTION,
        ),
        is_active=True,
    )
    db.session.add(deduction)
    params = load_investment_params_for_accounts([account]).get(account.id)
    if params is not None:
        params.salary_profile_id = profile.id
    db.session.commit()
    return deduction
