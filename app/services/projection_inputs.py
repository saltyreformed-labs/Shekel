"""
Shekel Budget App -- Shared Investment-Projection Inputs (F-22 / Commit 18).

Single home for the deduction-loader query and the
:func:`calculate_investment_inputs` kwargs splat that were duplicated
across the investment / retirement / savings / year-end consumers
pre-Commit-18.  The duplicates triggered pylint R0801 (similar-lines)
and -- more importantly -- meant the engine-input contract was defined
in four places at once, any one of which could drift independently.

Boundary discipline (``CLAUDE.md``: "services are isolated from Flask"):
this module imports no Flask symbol.  All inputs are plain data (user
id, account id, ORM model instances already loaded by the caller); the
return values are ORM lists, plain dicts, and the existing
:class:`~app.services.investment_projection.InvestmentInputs` DTO.

The deductions-loader and the projection-inputs wrapper live here
rather than in :mod:`app.services.investment_projection` because that
module's module-level docstring promises "no database access" -- the
contract Commit 28 / S6-01 set up so pure-data tests can construct
FakeDeduction / FakeContribution objects without a DB.  Placing the
DB-touching helpers in a sibling module preserves that boundary.

**That boundary is why :func:`load_payroll_feeds` is here** (plan step
**salary:R14-b**).  Pricing an account's payroll feed means running the
paycheck engine, which means resolving a profile and its tax configs, which
means a session -- so the PRICING happens at this loader and the pure module
receives a finished
:class:`~app.services.investment_projection.AccountPayrollFeed`.  It is the
same split ``PricedContribution`` and ``ShadowContributions`` already sit on,
applied to the third and last input that was still arriving raw.  **The
contribution feed itself lives one module over since plan step
balance:X-bi-6a** (:mod:`app.services.recorded_contributions`): that step's
edit carried this module past the 1000-line ceiling, and a feed with a
valuation rule of its own moved out rather than this module shaving.
"""

import logging
from collections.abc import Callable
from dataclasses import dataclass
from decimal import Decimal

from sqlalchemy.orm import joinedload, subqueryload

from app.extensions import db
from app.models.account import Account
from app.models.investment_params import InvestmentParams
from app.models.paycheck_line import PaycheckLine
from app.models.salary_profile import SalaryProfile
from app.services.account_projection import (
    AccountProjectionKind,
    classify_account,
)
from app.services.income_service import PaycheckPricing, ProfilePaychecks
from app.services.investment_projection import (
    AccountPayrollFeed,
    InvestmentInputs,
    calculate_investment_inputs,
)
from app.services.pay_calendar import DerivedPeriod
from app.utils.money import ZERO

logger = logging.getLogger(__name__)


def load_active_salary_profiles(
    user_id: int, scenario_id: int,
) -> list[SalaryProfile]:
    """Return a user's active salary profiles for a scenario, primary first.

    The single home for the "active profiles in this scenario, raises and
    deductions eager-loaded" query the year-end summary
    (:mod:`app.services.year_end_summary_service._data`) and the analytics
    Taxes report (:mod:`app.services.tax_report_service`) both drive -- an
    R0801 duplicate otherwise, the same consolidation rationale as the
    deduction loaders below.  Ordered by ``(sort_order, name)`` so
    ``result[0]`` is the PRIMARY profile (the salary cockpit's
    default-profile rule the Taxes report relies on); the year-end summary,
    an order-independent sum across the profiles, is unaffected by the
    ordering.

    Args:
        user_id: The owning user.
        scenario_id: The scenario to scope the profiles to.

    Returns:
        The active :class:`~app.models.salary_profile.SalaryProfile` list,
        ordered ``(sort_order, name)`` with ``raises``, ``lines``,
        ``pay_entries`` and ``pay_stubs`` eager-loaded (the stubs price the
        taxes since plan step salary:S11-c-2c; each stub's own lines load
        with it).
    """
    return (
        db.session.query(SalaryProfile)
        .options(
            subqueryload(SalaryProfile.raises),
            subqueryload(SalaryProfile.lines),
            subqueryload(SalaryProfile.pay_entries),
            subqueryload(SalaryProfile.pay_stubs),
        )
        .filter(
            SalaryProfile.user_id == user_id,
            SalaryProfile.scenario_id == scenario_id,
            SalaryProfile.is_active.is_(True),
        )
        .order_by(SalaryProfile.sort_order, SalaryProfile.name)
        .all()
    )


def load_active_accounts_with_types(user_id: int) -> list[Account]:
    """Return a user's active accounts with their ``account_type`` joined.

    The single home for the "active accounts, account_type eager-loaded"
    query the year-end summary and the analytics Taxes report (its Schedule
    A debt-account selection) both drive; joining the type avoids an N+1
    when the callers read ``account.account_type`` to classify each row
    (amortizing / interest-bearing / savings).

    Args:
        user_id: The owning user.

    Returns:
        The active :class:`~app.models.account.Account` list with
        ``account_type`` joined.
    """
    return (
        db.session.query(Account)
        .options(joinedload(Account.account_type))
        .filter(
            Account.user_id == user_id,
            Account.is_active.is_(True),
        )
        .all()
    )


def load_active_deductions_for_account(
    user_id: int, account_id: int,
) -> list[PaycheckLine]:
    """Return active paycheck deductions targeting a single account.

    The single-account variant of :func:`load_active_deductions_for_accounts`
    used by the investment-detail dashboard, which renders one account at a
    time -- for the contribution PROMPT it shows, which asks whether any
    deduction funds this account at all.  The dollars come off
    :func:`load_payroll_feeds` since plan step **salary:R14-b**; these rows
    are read for their existence, not for their amounts.

    Args:
        user_id: ID of the authenticated user (scopes via
            ``SalaryProfile.user_id``).
        account_id: ID of the investment / retirement account the
            deductions target.

    Returns:
        A list of :class:`PaycheckLine` rows (possibly empty).
    """
    return _active_deductions_query(user_id, [account_id]).all()


def load_active_deductions_for_accounts(
    user_id: int, account_ids: list[int],
) -> dict[int, list[PaycheckLine]]:
    """Return active paycheck deductions keyed by target account id.

    Batch variant used by the savings / retirement / year-end services
    when they classify many accounts in one pass and need O(1) lookup
    by account id inside a per-account loop.  Pre-Commit-18 the three
    consumers each issued the same query with their own local
    ``account_ids`` list; centralising it removes the R0801 duplicate
    and makes the active-deduction filter shape a single point of
    truth.

    Args:
        user_id: ID of the authenticated user (scopes via
            ``SalaryProfile.user_id``).
        account_ids: List of target account ids.  Empty list returns
            an empty dict without issuing a query, so callers do not
            need to guard ``IN ()`` against PostgreSQL.

    Returns:
        Dict mapping ``target_account_id`` -> list of
        :class:`PaycheckLine`.  Accounts with no deductions are
        absent from the dict; callers should use ``dict.get(id, [])``.
    """
    if not account_ids:
        return {}
    grouped: dict[int, list[PaycheckLine]] = {}
    for ded in _active_deductions_query(user_id, account_ids).all():
        grouped.setdefault(ded.target_account_id, []).append(ded)
    return grouped


def _active_deductions_query(user_id: int, account_ids: list[int]):
    """Build the canonical active-deductions query.

    Owns the filter shape duplicated three times pre-Commit-18:
    ``SalaryProfile.user_id == user_id``,
    ``SalaryProfile.is_active.is_(True)``,
    ``PaycheckLine.target_account_id.in_(...)``, and
    ``PaycheckLine.is_active.is_(True)``.  ``.in_(...)`` works
    for both single-id and multi-id call sites, so both public
    loaders route through this builder.

    Args:
        user_id: ID of the authenticated user.
        account_ids: Non-empty list of target account ids.

    Returns:
        A SQLAlchemy ``Query`` object; the caller decides ``.all()``
        vs ``.scalar()`` etc.
    """
    return (
        db.session.query(PaycheckLine)
        .join(SalaryProfile)
        .filter(
            SalaryProfile.user_id == user_id,
            SalaryProfile.is_active.is_(True),
            PaycheckLine.target_account_id.in_(account_ids),
            PaycheckLine.is_active.is_(True),
        )
    )


def load_investment_params_for_accounts(
    accounts: list[Account],
) -> dict[int, InvestmentParams]:
    """Return :class:`InvestmentParams` keyed by id for INVESTMENT accounts.

    The single home for the investment-params batch load: the savings
    dashboard's :func:`_load_account_params` built this map inline pre-seam,
    and the forthcoming ``balance_at`` seam (Level 1 of the
    balance-architecture work) shares this loader so the "which accounts
    get an InvestmentParams row?" decision lives in exactly one place
    instead of being re-derived per surface.

    Membership is decided by the canonical classifier
    (:func:`app.services.account_projection.classify_account`), never by
    elimination: only accounts the classifier marks
    :data:`~app.services.account_projection.AccountProjectionKind.INVESTMENT`
    are loaded.  This deliberately excludes a parameterised physical
    asset -- a Property classifies as
    :data:`~app.services.account_projection.AccountProjectionKind.APPRECIATING`
    and carries its own params, so it must not be pulled in here.  An
    account whose ``account_type`` is unloaded / ``None`` classifies as
    PLAIN and is skipped.

    Args:
        accounts: Account model instances to scope to, each with its
            ``account_type`` relationship available for the classifier
            (the consumer is expected to have loaded it; the classifier
            issues no queries).  An empty list -- or a list containing
            no INVESTMENT accounts -- returns an empty dict without
            issuing an ``IN ()`` query against PostgreSQL.

    Returns:
        Dict mapping ``account_id`` -> :class:`InvestmentParams`.
        INVESTMENT accounts that have no params row are absent from the
        dict; callers should use ``dict.get(id)``.
    """
    inv_account_ids = [
        a.id for a in accounts
        if classify_account(a) is AccountProjectionKind.INVESTMENT
    ]
    if not inv_account_ids:
        return {}
    params_map: dict[int, InvestmentParams] = {}
    for ip in (
        db.session.query(InvestmentParams)
        .filter(InvestmentParams.account_id.in_(inv_account_ids))
        .all()
    ):
        params_map[ip.account_id] = ip
    return params_map


@dataclass(frozen=True)
class PayrollWiring:
    """WHICH payroll funds WHICH account, loaded once and priced by nobody.

    The point-independent half of a payroll feed (plan step salary:S3-f-1,
    ruling **R-SAL20**): the active deductions targeting each account and the
    profiles they and the accounts' params name.  It is every query
    :func:`load_payroll_feeds` issues, held apart from the resolvers built
    over it so that a consumer pricing the SAME accounts under ANOTHER raise
    set -- the ``/retirement`` picture at each plan point, since plan step
    **salary:S3-f-2b** -- rebuilds the resolvers through
    :func:`price_payroll_feeds` and re-issues nothing.  A frozen value
    carrying dicts: it is not a memo key and is never hashed.

    Attributes:
        account_ids: The accounts a feed is built for, in the order asked.
        deductions_by_account: ``{account_id: [PaycheckLine]}`` -- each
            account's active deductions, from
            :func:`load_active_deductions_for_accounts`; an account with none
            is absent.
        profiles: ``{profile_id: SalaryProfile}`` -- every profile that funds
            any of these accounts from either side, active and this owner's,
            from :func:`_load_funding_profiles`.
        params_by_account: ``{account_id: InvestmentParams}``, read for the
            ``salary_profile_id`` that funds each employer contribution.
    """

    account_ids: "tuple[int, ...]"
    deductions_by_account: "dict[int, list[PaycheckLine]]"
    profiles: "dict[int, SalaryProfile]"
    params_by_account: "dict[int, InvestmentParams]"


def load_payroll_feeds(
    paychecks: "PaycheckPricing",
    account_ids: "list[int]",
    params_by_account: "dict[int, InvestmentParams]",
) -> "dict[int, AccountPayrollFeed]":
    """Build each account's payroll feed over the PAYCHECK ENGINE's pricer.

    **Two halves since plan step salary:S3-f-1**, composed by
    :func:`load_payroll` for the callers that price the stored plan:
    :func:`load_payroll_wiring` issues every query and
    :func:`price_payroll_feeds` builds the resolvers over the pass's pricers,
    one per funding profile under its rows.  This door returns the feeds
    alone; a consumer that will price the same accounts under another raise
    set later takes the wiring too through :func:`load_payroll` and calls
    :func:`price_payroll_feeds` with the set it believes -- which is what the
    ``/retirement`` batch loader and picture do (salary:S3-f-2b).

    **The producer plan step salary:R14-b puts in place of the feed's own
    arithmetic** (ruling **R-SAL2**).  What a payroll deduction takes from a
    paycheck, and what gross an employer contribution is a percentage of, are
    both facts the paycheck engine establishes when it prices the paycheck.
    This takes :class:`~app.services.income_service.PaycheckPricing` -- the
    ONE spelling of a profile's projection since ``salary:R14-a`` -- and hands
    each account two RESOLVERS closed over the pricers of the profiles that
    fund it, each folding the
    :class:`~app.services.paycheck_calculator.PricedLine`\\ s of the
    paycheck priced for the period it is asked about by the
    ``target_account_id`` they already carry.

    **It prices NOTHING here, since plan step salary:S3-e-2** (ruling
    **R-SAL15**).  It priced the owner's whole saved window up front and
    handed the feed two dictionaries, which is why the feed had to INVENT a
    figure past the last saved payday; a resolver asks the pricer for the
    period it is handed, whichever one that is, and the pricer's memo makes
    the second ask free.  What this function still does up front is build
    the per-profile pricers (:meth:`~app.services.income_service
    .PaycheckPricing.for_profile`), because THAT is where the pass's calendar
    is derived on its first use (the raises it may read are eager-loaded by
    :func:`_load_funding_profiles`, and the tax series loaded there too, three
    queries, until plan step salary:X-at-1 moved the law into the code) -- so
    the loader keeps every query, and a resolver fired inside
    :mod:`app.services.investment_projection`'s *no database access*
    contract issues none.

    It answers R-SAL2's three questions at their source rather than
    re-deriving any of them:

    * **WHOSE salary** -- each deduction is priced inside its OWN profile's
      paycheck, because that is the profile whose deductions the engine walked.
      A two-job owner's two profiles are two projections, and no reader picks
      between them.
    * **WHICH gross** -- the paycheck's own, raises applied as of its payday.
    * **WHICH clock** -- the period's, never ``date.today()``.  Nothing here
      reads a clock; the CALENDAR is the domain.

    **The profiles are NAMED, never searched**, which is what retires
    ``income_service.get_current_gross_biweekly`` rather than re-pointing it.
    That helper resolved a profile with an unordered ``.first()`` across the
    owner's active profiles -- a measured **39%** swing on a two-job owner,
    flipping between renders -- and answered ``$0.00`` whenever no period
    covered today, which silently deleted the whole contribution plan at
    onboarding and after a horizon lapse.  Here the employee half's profile is
    the one the DEDUCTION belongs to and the employer half's is the one
    ``budget.investment_params.salary_profile_id`` names (**R-SAL5**), so
    there is no search to be non-deterministic about and no clock to answer
    zero against.

    **An unknown funding profile models NO employer money** (developer,
    2026-09-04): the account gets no gross resolver, which is what
    :attr:`~app.services.investment_projection.AccountPayrollFeed.funds_employer`
    reports and what the surfaces render as *the funding job is not set*.
    Unknown covers three states and they are one answer: the column is
    ``NULL``, it names a profile the owner has ARCHIVED (an employer
    contribution from a job they have left is not money they receive), or it
    names a profile that is not theirs -- the forged FK ``salary:R14-a``
    closed at the write door and this scopes against a second time, because a
    read that trusts a column's ownership is a read that can be made to price
    a stranger's salary.

    Args:
        paychecks: The read pass's
            :class:`~app.services.income_service.PaycheckPricing`, which
            carries the owner's calendar as well as their prices.

            **It replaced a calendar beside an OPTIONAL memo at plan step
            salary:S3-d**, and both halves of that pair were holes.  Running
            the engine is the expensive half of a feed -- each paycheck
            replays the year's prior paydays for its FICA and annual-cap
            cumulatives -- and the balance seam asks for a feed once per
            ACCOUNT, so without a memo the engine re-ran the same profile once
            per account per entry.  ``breakdowns=None`` meant "no memo", and
            two of that argument's four callers passed it inside the step that
            added it; there is nothing to omit now.  Taking the calendar off
            the same value is the other half: a calendar handed separately is
            one owner's paydays pairable with another owner's prices.

            **The OWNER comes off it too**, for the same reason and by the
            same argument one input further: this took a ``user_id`` beside
            the calendar, and every caller filled it with the owner that
            calendar already names.  Two homes for one fact on the very
            function whose case against a separate calendar is that pairing
            them is a hazard -- so the ``user_id`` every query below is
            scoped by is read off the pricer (``paychecks.user_id``, pinned
            beside its calendar source and refused at the first derivation if
            the two disagree, plan step salary:C12), which cannot disagree
            with the paydays and costs no derivation to ask.  *The calendar's
            PAYDAYS are not read here any more* (plan step salary:S3-e-2):
            the owner is the only thing this function takes off it.
        account_ids: The accounts to price a feed for.  An empty list returns
            an empty map without issuing a query.
        params_by_account: ``{account_id: InvestmentParams}`` from
            :func:`load_investment_params_for_accounts`, read for the
            ``salary_profile_id`` that funds each employer contribution.

    Returns:
        ``{account_id: AccountPayrollFeed}``, TOTAL over *account_ids* -- an
        account no payroll funds maps to
        :meth:`~app.services.investment_projection.AccountPayrollFeed.absent`'s
        value rather than being absent, so a caller indexes rather than
        defaulting and a missing key is a defect instead of a silently
        unfunded account.
    """
    return load_payroll(paychecks, account_ids, params_by_account)[1]


def load_payroll(
    paychecks: "PaycheckPricing",
    account_ids: "list[int]",
    params_by_account: "dict[int, InvestmentParams]",
) -> "tuple[PayrollWiring, dict[int, AccountPayrollFeed]]":
    """Load which payroll funds *account_ids* and price it under the rows.

    The ONE body behind :func:`load_payroll_feeds` (plan step salary:S3-f-2b):
    it returns the wiring beside the feeds so a caller that will re-price the
    same accounts under another raise set -- the ``/retirement`` batch loader,
    for the picture at each plan point -- keeps the wiring without composing
    the two halves a second time beside this door.  A second composition
    would have to name the OWNER for itself, and the argument for this door
    is that the owner comes off the pricer's own calendar so the rows it
    scopes and the paychecks that price them cannot name two owners.

    Args:
        paychecks: The read pass's
            :class:`~app.services.income_service.PaycheckPricing`; its
            calendar names the owner.
        account_ids: The accounts to wire and price.  An empty list wires
            nothing and issues no query.
        params_by_account: ``{account_id: InvestmentParams}`` from
            :func:`load_investment_params_for_accounts`.

    Returns:
        ``(wiring, feeds)`` -- the :class:`PayrollWiring` and the feeds
        :func:`load_payroll_feeds` documents, TOTAL over *account_ids*.
    """
    wiring = load_payroll_wiring(
        paychecks.user_id, account_ids, params_by_account,
    )
    return wiring, price_payroll_feeds(wiring, paychecks)


def load_payroll_wiring(
    user_id: int,
    account_ids: "list[int]",
    params_by_account: "dict[int, InvestmentParams]",
) -> PayrollWiring:
    """Load which payroll funds which of *account_ids*, issuing every query.

    The query half of :func:`load_payroll_feeds`, which documents what the
    two halves answer together.  Every profile that funds any of these
    accounts is loaded from BOTH sides -- the profile each active deduction
    belongs to, and the one each account's params name -- NAMED rather than
    searched, and scoped to *user_id* so a column naming a stranger's
    profile resolves to nothing.

    Args:
        user_id: The owner.  Off the pass's calendar at the composed door, so
            the rows this scopes and the paychecks that price them cannot
            name two owners.
        account_ids: The accounts to wire.  An empty list wires nothing and
            issues no query, the same as the composed door.
        params_by_account: ``{account_id: InvestmentParams}`` from
            :func:`load_investment_params_for_accounts`.

    Returns:
        The :class:`PayrollWiring`.
    """
    deductions_by_account = load_active_deductions_for_accounts(
        user_id, account_ids,
    )
    wanted = {
        ded.salary_profile_id
        for rows in deductions_by_account.values() for ded in rows
    }
    wanted.update(
        params.salary_profile_id
        for account_id in account_ids
        if (params := params_by_account.get(account_id)) is not None
        and params.salary_profile_id is not None
    )
    return PayrollWiring(
        account_ids=tuple(account_ids),
        deductions_by_account=deductions_by_account,
        profiles=_load_funding_profiles(user_id, wanted),
        params_by_account=params_by_account,
    )


def price_payroll_feeds(
    wiring: PayrollWiring,
    paychecks: "PaycheckPricing",
    terms_for=None,
) -> "dict[int, AccountPayrollFeed]":
    """Build *wiring*'s feeds over the PASS's pricers, one per funding profile.

    **The one spelling of "price this wiring through the pass"** (plan step
    salary:S3-f-2b).  Three callers build the same pricer map -- the composed
    door :func:`load_payroll_feeds`, the ``/retirement`` batch loader for the
    stored plan, and the picture at each plan point under the set it
    believes -- and three comprehensions over ``wiring.profiles`` would be
    three chances to hand :func:`build_payroll_feeds` a map built off the
    wrong set.

    The PRICERS are built here rather than inside a resolver:
    :meth:`~app.services.income_service.PaycheckPricing.for_profile` derives
    the pass's calendar on its first use -- the raises it reads when no set is
    given are eager-loaded by :func:`_load_funding_profiles`, and the tax
    series it also loaded there is sliced from the law without a query since
    plan step salary:X-at-1 -- and that query belongs to this loader, not to
    the pure module the resolver will fire in.  A pricer prices nothing until asked, so an account
    whose feed no consumer reads costs that construction and no paycheck --
    and a set
    equal to the rows costs nothing at all, because ``for_profile`` keys its
    memo on the canonical set and answers the pricer the rows already built.

    Args:
        wiring: The :class:`PayrollWiring`, loaded once.
        paychecks: The read pass's
            :class:`~app.services.income_service.PaycheckPricing`.
        terms_for: ``profile -> raise set`` naming the terms each funding
            profile is priced under (a plan point's
            :meth:`~app.services.retirement_plan.PlanPoint.terms_for`), or
            ``None`` for each profile's own rows -- the same contract
            ``for_profile``'s ``raise_terms`` states, one call up.

    Returns:
        ``{account_id: AccountPayrollFeed}``, TOTAL over the wiring's
        ``account_ids`` -- see :func:`load_payroll_feeds`.
    """
    return build_payroll_feeds(wiring, {
        profile_id: paychecks.for_profile(
            profile, None if terms_for is None else terms_for(profile),
        )
        for profile_id, profile in wiring.profiles.items()
    })


def build_payroll_feeds(
    wiring: PayrollWiring, pricers: "dict[int, ProfilePaychecks]",
) -> "dict[int, AccountPayrollFeed]":
    """Build each wired account's feed over *pricers*, issuing no query.

    The pure half of :func:`load_payroll_feeds`.  It reads the wiring for
    WHICH profile prices each half of each account and *pricers* for the
    paychecks, so the same wiring priced under two raise sets is two calls
    here over two pricer maps and one :func:`load_payroll_wiring`.

    Args:
        wiring: The :class:`PayrollWiring`.
        pricers: ``{profile_id: ProfilePaychecks}`` covering every id in
            ``wiring.profiles`` -- the composed door builds them off the
            pass's pricer under the rows; a what-if builds them under its
            terms.

    Returns:
        ``{account_id: AccountPayrollFeed}``, TOTAL over the wiring's
        ``account_ids`` -- see :func:`load_payroll_feeds`.

    Raises:
        ValueError: *pricers* lacks a profile the wiring names.  **A
            precondition of a public door, not a guard for an impossible
            state**: the resolvers below answer a missing pricer two
            different ways -- the employer half reads ``pricers.get`` and
            would report the account as funding NO employer money (the
            answer reserved for an absent, archived or foreign profile, which
            the wiring has already filtered out), while the employee half
            would ``KeyError`` on the first period asked -- so a caller that
            built its map off the wrong set is refused here, once, by name.
    """
    missing = set(wiring.profiles) - set(pricers)
    if missing:
        raise ValueError(
            f"build_payroll_feeds was handed no pricer for salary profile(s) "
            f"{sorted(missing)}, which the wiring names as funding one of "
            f"accounts {list(wiring.account_ids)}; every profile in "
            f"wiring.profiles must be priced, or the employer half would "
            f"silently read as unfunded."
        )
    return {
        account_id: AccountPayrollFeed(
            employee=_employee_resolver(
                account_id, wiring.deductions_by_account.get(account_id, []),
                pricers,
            ),
            gross=_gross_resolver(
                wiring.params_by_account.get(account_id), pricers,
            ),
        )
        for account_id in wiring.account_ids
    }


def _load_funding_profiles(
    user_id: int, profile_ids: "set[int]",
) -> "dict[int, SalaryProfile]":
    """Return the ACTIVE salary profiles among *profile_ids* this owner holds.

    The one place the three ways a funding profile can be unknown collapse
    into one answer (plan step **salary:R14-b**): the id is absent, the
    profile is archived, or the profile belongs to someone else.  Filtering
    here rather than at each reader is what makes "no funding profile" a
    single state the feed can report, instead of three branches each caller
    would have to remember.

    Args:
        user_id: The owner.  Scopes the query, so a ``salary_profile_id``
            pointing at a stranger's profile resolves to nothing.
        profile_ids: The profile ids named by the deductions and the accounts'
            params.  Empty returns an empty map without a query.

    Returns:
        ``{profile_id: SalaryProfile}`` for the ids that are this owner's and
        active, with the relationships the paycheck engine reads eager-loaded.
    """
    if not profile_ids:
        return {}
    rows = (
        db.session.query(SalaryProfile)
        .options(
            subqueryload(SalaryProfile.raises),
            subqueryload(SalaryProfile.lines),
            subqueryload(SalaryProfile.pay_entries),
            subqueryload(SalaryProfile.pay_stubs),
        )
        .filter(
            SalaryProfile.id.in_(profile_ids),
            SalaryProfile.user_id == user_id,
            SalaryProfile.is_active.is_(True),
        )
        .all()
    )
    return {profile.id: profile for profile in rows}


def _employee_resolver(
    account_id: int,
    deductions: list[PaycheckLine],
    pricers: dict[int, ProfilePaychecks],
) -> Callable[[DerivedPeriod], Decimal] | None:
    """Build ONE account's ``period -> employee amount`` resolver, or ``None``.

    The resolver reads the amount off the
    :class:`~app.services.paycheck_calculator.PricedLine`\\ s of the
    paycheck the engine prices for the period it is asked about -- raise-aware,
    inflation-escalated, cadence-placed and clamped to the line's own
    calendar-year cap -- rather than pricing anything itself.  Pre- and
    post-tax lines both count: what an account RECEIVES does not depend on
    which side of the tax line the deduction sits.

    Args:
        account_id: The account whose lines to keep.
        deductions: The account's active deductions, read only for WHICH
            profiles fund it; the amounts come off the paychecks.
        pricers: ``{profile_id: ProfilePaychecks}`` for every profile that
            funds any account in the batch.

    Returns:
        The resolver, or ``None`` when no active profile of this owner's
        funds the account -- which is what makes
        :attr:`~app.services.investment_projection.AccountPayrollFeed
        .is_payroll_linked` ``False`` there.
    """
    # DISTINCT profiles, keyed by id: an account funded by three of one
    # profile's deductions must read that profile's paycheck ONCE, or every
    # line on it would be counted as many times as the account has
    # deductions.  The lines themselves are then filtered by
    # ``target_account_id`` below, which is what keeps a sibling deduction
    # feeding a DIFFERENT account out of this sum.
    # No membership guard: ``_active_deductions_query`` already joins each
    # deduction to an ACTIVE profile of this owner, and
    # :func:`_load_funding_profiles` applies exactly those two filters over a
    # SUPERSET of these ids -- so a deduction whose profile is missing from
    # the map is not a state either query can produce.  A guard here would be
    # one that cannot fire, which ``CLAUDE.md`` rule 1 forbids shipping.
    funding_ids = {ded.salary_profile_id for ded in deductions}
    if not funding_ids:
        return None
    funding = [pricers[pid] for pid in funding_ids]

    def _employee(period: DerivedPeriod) -> Decimal:
        total = ZERO
        for pricer in funding:
            deductions_priced = pricer.at(period).deductions
            for line in deductions_priced.pre_tax + deductions_priced.post_tax:
                if line.target_account_id == account_id:
                    total += line.amount
        return total

    return _employee


def _gross_resolver(
    params: InvestmentParams | None,
    pricers: dict[int, ProfilePaychecks],
) -> Callable[[DerivedPeriod], Decimal] | None:
    """Build the FUNDING profile's ``period -> gross`` resolver, or ``None``.

    The employer contribution's basis (**R-SAL5**): the BASE pay of the
    paycheck the profile named by ``budget.investment_params.salary_profile_id``
    is paid on the period asked about -- ``earnings.base_biweekly``, the
    salary rate, which ruling **R-SAL38** (plan step salary:R18-b) makes the
    base of every percentage figure in payroll: an employer match is a
    percentage of the salary, not of the gross a taxable allowance joins, and
    reading the base here is what keeps this feed and the engine's own
    percentage lines on one figure.  (``gross_biweekly`` until R18-b, which
    WAS the base rate then.)  ``None`` when that profile is unknown --
    absent, archived, or not this owner's, the three states
    :func:`_load_funding_profiles` has already collapsed into "not in the
    map" -- which is the developer's 2026-09-04 ruling that such an account
    models no employer money at all, and what makes
    :attr:`~app.services.investment_projection.AccountPayrollFeed
    .funds_employer` ``False`` there.

    Args:
        params: The account's :class:`InvestmentParams`, or ``None``.
        pricers: ``{profile_id: ProfilePaychecks}`` for every profile that
            funds any account in the batch.

    Returns:
        The resolver, or ``None``.
    """
    pricer = pricers.get(getattr(params, "salary_profile_id", None))
    if pricer is None:
        return None

    def _gross(period: DerivedPeriod) -> Decimal:
        return pricer.at(period).earnings.base_biweekly

    return _gross


def build_investment_projection_inputs(
    params: InvestmentParams,
    feed: "AccountPayrollFeed",
    contributions: list,
    current_period,
) -> InvestmentInputs:
    """Build :class:`InvestmentInputs` for one account.

    The single home for the keyword splat into
    :func:`~app.services.investment_projection.calculate_investment_inputs`
    that was duplicated across the investment / retirement / savings /
    year-end services pre-Commit-18.  Centralising the splat removes
    the R0801 duplicate and means a future signature change to
    ``calculate_investment_inputs`` only needs to update one site.

    **The owner's period LIST left this signature at plan step C2-f2c**, and
    the ``too-many-arguments`` disable that justified six went with it.  The
    list served the wrapped function's two YTD windows alone, as a lookup from
    a contribution's pay period to that period's payday;
    :func:`~app.services.recorded_contributions.load_shadow_income_contributions_for_accounts`
    dates each
    contribution now, so there is nothing left to look up.

    Callers supply the ``feed`` (from :func:`load_payroll_feeds`) and
    ``contributions`` because the per-consumer contribution-loading
    queries differ in scenario / status filters (savings + year-end
    apply ``balance_excluded_status_ids`` + scenario scoping;
    investment dashboard does not).  Forcing a one-size query inside
    this helper would silently change the per-period contribution
    average those consumers compute; passing pre-loaded data
    preserves each surface's existing filter contract.

    **A verification gate stated here matched NOTHING and was deleted** (plan
    step C2-f3a, ledger row **P52**); the lesson is kept because it is about
    gates and not about the argument that has since gone.  It named a ``grep``
    for the kwarg-self-binding splat below as a duplicate-canary and concluded
    "the gate passes when only this helper site has it".  ``grep`` is
    LINE-based and the one call site carrying the pattern closed on the NEXT
    line, so the expression matched zero lines: the gate passed because
    nothing matched, which is indistinguishable from passing because one thing
    matched -- a safety that is not a predicate, which the planning
    conventions called worse than no safety at all.  The
    duplication it claimed to police IS real -- the splat sat in four services
    before Commit 18 -- and it is really policed, by pylint's
    ``duplicate-code``, which CI enforces as a hard gate.

    Args:
        params: :class:`InvestmentParams` row for the account.
        feed: The account's
            :class:`~app.services.investment_projection.AccountPayrollFeed`
            from :func:`load_payroll_feeds` -- what its payroll puts in per
            payday, priced by the paycheck engine.
        contributions: List of
            :class:`~app.services.investment_projection.PricedContribution`
            records already filtered to this account.
        current_period: The
            :class:`~app.services.pay_calendar.DerivedPeriod` covering the read
            pass's clock, or ``None``.  **Both callers pass that type** since
            pay-calendar plan step C2-f2d-3; this said "an ORM ``PayPeriod`` on
            ``/retirement``, a ``DerivedPeriod`` on ``/investment``" until then,
            and only ``start_date`` is read either way.

    Returns:
        :class:`InvestmentInputs` carrying the current period's contribution,
        employer params, annual contribution limit and YTD contributions the
        growth engine and the per-period cards need.
    """
    return calculate_investment_inputs(
        investment_params=params,
        feed=feed,
        all_contributions=contributions,
        current_period=current_period,
    )


# Public API -- re-exported types for callers that only import from
# this module so they do not also need to reach into
# ``app.services.investment_projection`` for the DTO.
__all__ = [
    "Account",
    "AccountPayrollFeed",
    "InvestmentInputs",
    "InvestmentParams",
    "PayrollWiring",
    "build_investment_projection_inputs",
    "build_payroll_feeds",
    "load_active_deductions_for_account",
    "load_active_deductions_for_accounts",
    "load_investment_params_for_accounts",
    "load_payroll_feeds",
    "load_payroll_wiring",
]
