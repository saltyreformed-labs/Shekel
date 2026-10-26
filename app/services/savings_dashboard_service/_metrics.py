"""
Shekel Budget App -- Savings Dashboard: emergency-fund and debt metrics.

Average monthly expenses (the higher of recent settled expenses and the
committed-template floor), the DTI band the aggregate debt summary carries
(the summary itself is :mod:`._debt_summary`'s), the canonical current-pay
producer (the owner's active profiles' net and gross for the current period,
summed), and the liquid balance sum that feeds the emergency fund.  No Flask
imports.
"""

from dataclasses import dataclass
from decimal import Decimal, ROUND_HALF_UP

from sqlalchemy.orm import selectinload

from app import ref_cache
from app.enums import AcctTypeEnum, TxnTypeEnum
from app.extensions import db
from app.models.salary_profile import SalaryProfile
from app.models.transaction import Transaction
from app.models.transaction_entry import TransactionEntry
from app.models.transaction_template import TransactionTemplate
from app.models.transfer import Transfer
from app.models.transfer_template import TransferTemplate
from app.services import (
    obligations_aggregator,
)
from app.services.balance_at import BalanceContext
from app.services.pay_calendar import PayCadence, PayCalendar, PeriodWindow
from app.services.row_valuation import (
    leg_settled_contribution,
    settled_contribution,
)
from app.services.transfer_legs import expense_legs, recorded_transfer_legs
from app.services.savings_dashboard_service._types import (
    AccountProjection,
    _DashboardCoreData,
)
from app.utils.balance_predicates import settled_status_ids

_DTI_HEALTHY_THRESHOLD = Decimal("36")
_DTI_HIGH_THRESHOLD = Decimal("43")


@dataclass(frozen=True)
class CurrentPay:
    """What the owner's ACTIVE salary profiles pay this period, summed.

    The two figures this page reads off a paycheck and nothing else (plan step
    **salary:C12-b**): the net an income-relative goal is stated in multiples
    of, and the gross the debt-to-income denominator is a month of.  A
    :class:`~app.services.paycheck_calculator.PaycheckBreakdown` was carried
    here until C12-b, one profile's, chosen by an unordered ``.first()``; a
    two-job owner's "current paycheck" is the SUM over their profiles
    (ruling **R-SAL26**), and two breakdowns do not add, so the value is the
    two totals the page consumes.  Absent as a whole (``None`` from
    :func:`_current_pay`) when there is no current period or no active
    profile -- absence of an income source is not a ``$0.00`` income (E-12).

    **It carries the rhythm the paycheck was priced at** (ruling
    **R-SAL70**, plan step salary:X-av-2), because both readers leave paycheck
    space -- the debt-to-income denominator is a MONTH of gross, a goal stated
    in months of income a month of net -- and the conversion must use the
    count the figures were divided by.  They converted at ``cadence_for``,
    the LATEST era's rhythm, which agreed with the engine only while the
    engine divided every paycheck by that same count: once X-av-2 priced a
    payday at its own era's count, an owner paid monthly today with a
    biweekly rhythm recorded to start later would have read a ``$5,000.00``
    paycheck as ``$10,833.33`` a month (made-up figures).

    Attributes:
        net_biweekly: The summed net pay for one paycheck, off the pass's
            pricer, each profile's taxes priced from its own pay stubs (plan
            step salary:S11-c-2c; its calibration until then).
        gross_biweekly: The summed gross for the same paycheck.
        cadence: The rhythm that paycheck was priced at, off the priced
            paychecks' own :attr:`~app.services.paycheck_calculator.PeriodInfo
            .cadence` -- one value for every profile summed, because each is
            priced by the pass's one pricer on the pass's one calendar for
            the one payday.
    """

    net_biweekly: Decimal
    gross_biweekly: Decimal
    cadence: PayCadence


@dataclass(frozen=True)
class DtiMetrics:
    """The debt-to-income block -- present as a whole, or absent as a whole.

    ONE nullable field replacing three parallel ones (plan step X-s3, ruling
    R-BD, finding N-106).  ``dti_ratio``, ``dti_label`` and
    ``gross_monthly_income`` were three keys written together in one branch and
    set to ``None`` together in the other, so "the user has no income data" was
    a state spelled three ways -- and read as three predicates across two
    templates (the cockpit footer tested ``dti_ratio``, the dashboard debt
    track tested ``dti_ratio`` for its tooltip and ``dti_label`` for its
    badge).  A ``DtiMetrics | None`` makes the half-populated combination
    unrepresentable and leaves the consumers ONE thing to ask.

    **ONE stored fact**, and the band label is a :attr:`label` property over
    it.  Storing a value that is a pure function of another field is how a
    summary comes to contradict itself, and this package already had the
    answer -- see that property.

    It carried ``gross_monthly_income`` too, the engine-derived denominator,
    and X-s3's own adversarial review found it had ZERO ``app/`` readers
    (AST-verified; no template renders it either) -- an input already spent
    computing :attr:`ratio`, which is byte-for-byte the reason plan step X-s1
    refused to carry a milestone's ``date`` into the chart payload.  Applying
    this step's own thesis to this step's own new code deleted it (developer
    ruling, 2026-07-28).  The biweekly -> monthly conversion it used to pin
    directly stays pinned through the ratio: :attr:`ratio` is
    ``total_monthly_payments / gross_monthly``, and the numerator is asserted
    on its own, so a wrong gross still shows.

    Attributes:
        ratio: Monthly debt payments as a PERCENT of gross monthly income,
            quantized to one decimal place (e.g. ``Decimal("34.2")``).
    """

    ratio: Decimal

    @property
    def label(self) -> str:
        """The health band this ratio falls in: healthy / moderate / high.

        DERIVED, not stored, so it cannot contradict :attr:`ratio` -- the same
        reason :attr:`~.._debt_line.LoanPayoffOutlook.is_loan_free` is a
        property ("derived rather than stored so it cannot contradict the other
        two"), and the same reason ruling R-AZ deleted the Horizon's stored copy
        of that value one step earlier.  Storing it would let a future edit set
        a ratio of 50% beside a 'healthy' badge, and nothing would catch it.

        Returns:
            ``'healthy'``, ``'moderate'`` or ``'high'`` per
            :func:`_get_dti_label`'s thresholds.
        """
        return _get_dti_label(self.ratio)


def _sum_liquid_balances(account_data: list[AccountProjection]) -> Decimal:
    """Sum the current balances of liquid accounts for the emergency fund.

    Args:
        account_data: The per-account projections from
            ``_compute_account_projections``.

    Returns:
        The total liquid balance as a Decimal.
    """
    total_savings = Decimal("0.00")
    for ad in account_data:
        acct_type = ad.account.account_type
        if acct_type is not None and acct_type.is_liquid:
            total_savings += ad.current_balance
    return total_savings


def _current_pay(balance_ctx, current_period):
    """Return what the owner's active profiles pay for the current period.

    The single income producer this module uses for any engine-derived
    income figure (MED-06 / F-032): both consumers -- the savings-goal
    trajectory's net biweekly pay and the DTI denominator's gross monthly
    income -- route through here so the page cannot silently disagree with
    the paycheck engine on the same period.  Pre-Commit-26 the DTI
    denominator read an off-engine ``annual_salary / pay_periods`` recompute
    that dropped applicable ``SalaryRaise`` rows.

    **Priced through the PASS's pricer, CALIBRATED, since plan step
    salary:C12-b** (ruling **R-SAL25**; ledger row **P62**'s last site).
    It was a direct ``calculate_paycheck`` that loaded the tax configs itself
    and passed NO ``calibration=`` -- the door **R-SAL21** measured on
    ``/retirement`` -- while every other surface priced the same payday with
    the profile's calibration: on the developer's 2026-09-10 paycheck net
    ``$2,541.49`` by this door against ``$2,572.78`` by the pricer, and a
    3-months-of-salary goal target of ``$16,519.69`` against ``$16,723.07``.
    The DTI denominator is gross-based and did not move.  Ledger row P62 had
    recorded the implementations as agreeing; that was never measured across
    the calibration.  (The pricer prices from the profile's pay stubs since
    plan step salary:S11-c-2c, which deleted the calibration.)

    **SUMMED over the owner's active profiles** (ruling **R-SAL26**), where
    it priced ONE profile chosen by an unordered ``.first()`` -- the shape
    ``recurrence:R-F16``'s adversarial review measured at a 39% swing between
    renders on a two-job owner, and which C12-b's own control caught picking
    the SECOND of two.  A goal stated in months of salary and a debt-to-income
    ratio are about total income, as the grid counts both templates' rows for
    two profiles on two templates.  One edge, inherited and ruled here: a
    profile whose template is gone (``SET NULL`` on delete) has no grid rows
    and is summed.  A second edge, two active profiles on ONE template in one
    scenario (ledger row **N-294**), summed here while the amount model priced
    the template by one of them, is unstorable since plan step salary:X-av-1
    (``uq_salary_profiles_scenario_template``, rulings **R-SAL63** and
    **R-SAL69**); across scenarios it is this query's scenario-blindness,
    below.  Ordered by id so the walk is deterministic; the sum makes the
    order immaterial.  ``$0.00`` on the developer's data.

    **The query is scenario-blind, as the old door's was** (and as
    ``retirement_dashboard_service.load_gap_inputs`` is).  Reported rather
    than changed: ``projection_inputs.load_active_salary_profiles`` is the
    scenario-scoped home, and moving onto it decides what a non-baseline
    profile means to this page, a state no scenario writer today produces.

    **It takes the read PASS rather than an owner id** (plan step R-F16, on
    the ruling ``pay_calendar:C2-f2d-1`` set): the pricer is the pass's, so
    the paydays a paycheck is counted over and the paydays the rest of the
    render measures against are one derivation.  A profile the pricer has
    already priced for this payday -- the payroll feed's funding profile --
    costs no second engine run.

    Args:
        balance_ctx: The read pass.  Its ``user_id`` scopes the profile query
            and its pricer prices the period.
        current_period: The current
            :class:`~app.services.pay_calendar.DerivedPeriod`, or ``None``.

    Returns:
        The :class:`CurrentPay` for the current period under the owner's
        active salary profiles, or ``None`` if ``current_period`` is ``None``
        or no active profile exists.  Callers treat ``None`` as "no income
        data on the page" rather than as a zero amount, since absence of an
        income source is structurally different from a real zero (E-12).
    """
    if current_period is None:
        return None

    profiles = (
        db.session.query(SalaryProfile)
        .filter_by(user_id=balance_ctx.user_id, is_active=True)
        .order_by(SalaryProfile.id)
        .all()
    )
    if not profiles:
        return None

    paychecks = balance_ctx.paychecks()
    priced = [
        paychecks.for_profile(profile).at(current_period)
        for profile in profiles
    ]
    return CurrentPay(
        net_biweekly=sum((p.earnings.net_pay for p in priced), Decimal("0.00")),
        gross_biweekly=sum(
            (p.earnings.gross_biweekly for p in priced), Decimal("0.00"),
        ),
        # The paycheck's own rhythm, read off what was priced rather than
        # asked of the calendar again (ruling R-SAL70).  Every profile here is
        # priced for the one payday on the pass's one calendar, so any
        # element's cadence is every element's.
        cadence=priced[0].period.cadence,
    )


def _checking_account_ids(accounts):
    """IDs of the user's checking accounts.

    The single source for the checking-account scope shared by the two
    operands of :func:`_compute_avg_monthly_expenses` (DH-#29): both the
    committed-template floor and the recent-settled-expenses average
    measure outflow from these accounts, so the set is derived once here
    and threaded into both.  Resolved by the CHECKING ref-type id (IDs
    for logic), not a name string.

    Args:
        accounts: List of Account model instances.

    Returns:
        List of integer account IDs whose type is the CHECKING ref type.
    """
    checking_type_id = ref_cache.acct_type_id(AcctTypeEnum.CHECKING)
    return [
        acct.id for acct in accounts
        if acct.account_type_id == checking_type_id
    ]


def _recent_settled_expenses_monthly(
    checking_ids: list[int],
    all_periods: PeriodWindow,
    current_period,
    scenario_id: int,
    pay_cadence: PayCadence,
) -> Decimal:
    """Average monthly settled checking expenses over the last 6 periods.

    Sums settled expense transactions on the user's checking accounts
    across the most recent 6 periods (at or before the current period)
    and converts the per-period average to a monthly figure at the OWNER's
    pay cadence (a hardcoded 26/12 until plan step R7a-2a, which reported a
    weekly-paid owner's spending at half its true monthly rate and so
    understated the emergency fund they need).  Scoped to the same
    checking-account set
    as :func:`_committed_expense_floor` (DH-#29) so the two operands of
    :func:`_compute_avg_monthly_expenses`'s ``max()`` measure the same
    "outflow from checking" universe -- a settled expense on a
    non-checking account (e.g. a transfer out of a savings/HSA source) is
    excluded here just as it is from the floor, rather than inflating only
    the historical operand.  A transfer OUT of checking is one of its
    expenses, read as its transfer's from-side LEG since leaf
    ``balance:X-bi-6-4a`` (ruling **R-BAL106**), never as its shadow row.

    Args:
        checking_ids: IDs of the user's checking accounts (the
            :func:`_checking_account_ids` set the floor also uses).
        all_periods: The owner's saved schedule as a
            :class:`~app.services.pay_calendar.PeriodWindow`.
        current_period: The current
            :class:`~app.services.pay_calendar.DerivedPeriod`, or ``None``.
        scenario_id: The baseline scenario's id, from the read pass's
            raising accessor -- never a nullable (plan step X-v2).
        pay_cadence: How often the owner is paid
            (:class:`~app.services.pay_calendar.PayCadence`), which is what
            turns a per-PERIOD average into a per-MONTH one.

    Returns:
        The monthly average as a Decimal.  ``Decimal("0.00")`` when
        there is no current period, no checking account, or no recent
        periods.

    **This function took the nullable SCENARIO OBJECT and answered
    ``Decimal("0.00")`` for a user with no baseline** (a fabricated expense
    feeding the runway; the THIRD guard where ruling R-BY allows two).  X-v's
    AST census missed it because the predicate arrives as a PARAMETER (N-112).
    """
    if current_period is None or not checking_ids:
        return Decimal("0.00")

    recent_periods = [
        p for p in all_periods
        if p.period_index <= current_period.period_index
    ][-6:]
    if not recent_periods:
        return Decimal("0.00")

    recent_period_ids = [p.period_id for p in recent_periods]
    # "Settled checking EXPENSE" is asked in the queries (a leg's side by
    # ``expense_legs``), not a later ``if`` beside the sum (X-au-c2), so each
    # accessor below sees only what SETTLED and answers from its RECORD (X-au-c3).
    # ``settled_status_ids()`` is the ``is_settled`` set (Paid, Received).
    recent_txns = (
        db.session.query(Transaction)
        .filter(
            Transaction.pay_period_id.in_(recent_period_ids),
            Transaction.account_id.in_(checking_ids),
            Transaction.scenario_id == scenario_id,
            Transaction.is_deleted.is_(False),
            Transaction.transfer_id.is_(None),
            Transaction.transaction_type_id == ref_cache.txn_type_id(
                TxnTypeEnum.EXPENSE,
            ),
            Transaction.status_id.in_(settled_status_ids()),
        )
        # ``settled_contribution`` resolves through
        # ``row_valuation.settled_figure``, which sums EVERY settled row's
        # entries rather than reading a stored copy (plan step X-au-c3 for
        # an envelope; balance:X-bi-4b-1 for every row, ruling R-BAL80).
        # Without this the metric issues one SELECT per settled row where it
        # used to read a column.
        .options(selectinload(Transaction.entries))
        .all()
    )
    legs = expense_legs(recorded_transfer_legs(
        Transfer.from_account_id.in_(checking_ids),
        Transfer.pay_period_id.in_(recent_period_ids),
        Transfer.scenario_id == scenario_id,
        # A leg's money that MOVED is its DATED record (ruling R-BAL80); a
        # revert keeps an un-dated one, which is not spending.  The
        # transfer's live and settled filters this stated went at plan step
        # balance:X-bi-6-4d-2 (finding BAL-534): a dated side record is
        # unstorable under a deleted transfer (the deleted-row rule's
        # transfer arm) and under one not Paid or Received (the side band
        # rule), so this one clause is both.
        TransactionEntry.settled_on.isnot(None),
    ))

    total_expenses = sum(
        (settled_contribution(txn) for txn in recent_txns), Decimal("0.00"),
    ) + sum((leg_settled_contribution(leg) for leg in legs), Decimal("0.00"))

    per_period = total_expenses / len(recent_periods)
    return pay_cadence.per_paycheck_to_monthly(per_period)


def _committed_expense_floor(
    checking_ids: list[int], ctx: BalanceContext,
) -> Decimal:
    """Committed monthly expense floor from active checking templates.

    Sums the monthly-normalized commitment of active expense templates
    and active outgoing transfer templates on the user's checking
    accounts, via the canonical obligations aggregator (E-24 / HIGH-05)
    -- so the same skip-non-repeating / skip-expired filter the
    Recurring surface applies governs the emergency-fund baseline.

    **It hands the aggregator the READ PASS** (plan step R7d-e), where it
    threaded the pass's calendar and day separately until then.  The expired
    filter now asks what stops a definition through the composed door --
    the rule's own bound AND its destination's derived stop -- so a loan
    payment leaving from checking drops out of this floor on the day its loan
    is finished rather than on the day a chokepoint last rewrote the cached
    bound; the door needs the pass to fold the loan, and a pass carries the
    schedule and the day together so the two cannot disagree.  What the
    calendar and the day bought here is unchanged: a paycheck-space
    template's monthly equivalent is measured against the owner's real
    rhythm, a count-bounded template that has spent its count leaves the
    baseline (plan step R7b-3), and the day is the pass's own rather than a
    bare clock read (pay-calendar plan step C2-f2d-3, ledger row **P55**), so
    this floor sits on the same day as the settled-expense operand it is
    compared against by ``max()``.  The owner is the pass's, not a second
    argument beside it (developer ruling 2026-08-16: an id beside a required
    pass is two spellings of the owner that nothing checks agree).

    Args:
        checking_ids: IDs of the owner's checking accounts (the
            :func:`_checking_account_ids` set the historical operand
            also uses).
        ctx: The render's read pass; its ``user_id`` scopes both queries.

    Returns:
        The committed monthly floor as a Decimal.  ``Decimal("0.00")``
        when the user has no checking account.
    """
    if not checking_ids:
        return Decimal("0.00")

    expense_type_id = ref_cache.txn_type_id(TxnTypeEnum.EXPENSE)
    active_expense_templates = (
        db.session.query(TransactionTemplate)
        .filter(
            TransactionTemplate.user_id == ctx.user_id,
            TransactionTemplate.account_id.in_(checking_ids),
            TransactionTemplate.transaction_type_id == expense_type_id,
            TransactionTemplate.is_active.is_(True),
        )
        .all()
    )
    active_transfer_templates = (
        db.session.query(TransferTemplate)
        .filter(
            TransferTemplate.user_id == ctx.user_id,
            TransferTemplate.from_account_id.in_(checking_ids),
            TransferTemplate.is_active.is_(True),
        )
        .all()
    )
    return obligations_aggregator.committed_monthly(
        list(active_expense_templates) + list(active_transfer_templates),
        ctx,
    )


def _compute_avg_monthly_expenses(
    core: _DashboardCoreData, calendar: PayCalendar,
) -> Decimal:
    """Compute average monthly expenses for emergency fund coverage.

    Uses the higher of: historical settled expenses from the last 6
    periods, or the committed monthly baseline from active templates.
    Both operands are scoped to the user's checking accounts (DH-#29)
    so the ``max()`` compares like with like -- the "outflow from
    checking" universe the committed floor (E-24) defines -- rather than
    pairing an all-accounts historical figure against a checking-only
    floor.

    **Takes the read-pass bundle rather than four of its fields** (plan step
    R7a-2a).  It had unpacked ``accounts`` / ``all_periods`` /
    ``current_period`` / ``scenario_id`` at the call site and threaded them
    through, and the owner's pay cadence -- which BOTH operands need -- would
    have been a fifth.  All four are reachable from
    :class:`~.._types._DashboardCoreData` (the period SET through its
    ``balance_ctx`` since pay-calendar plan step C2-f2d-3), so the bundle is
    the honest argument and the caller stops restating its contents; the
    cadence does NOT, and is passed separately for the reason that class's
    docstring gives.

    **It took the owner's id beside the bundle until plan step R7d-e**, whose
    only use was to hand it on to the committed floor; that floor reads the
    owner off the pass now (developer ruling 2026-08-16: an id beside a
    required pass is two spellings of the owner), so the argument had no
    reader left and went.

    Args:
        core: The read pass's loaded bundle -- its accounts scope the checking
            set, and its pass's owner, reported periods and scenario scope
            both operands.
        calendar: The owner's whole pay-period schedule.  ``calendar.cadence``
            converts BOTH operands into month space -- one value for both, so
            the ``max()`` cannot compare figures measured against two rhythms
            -- and the committed operand needs the whole schedule to tell
            whether a count-bounded template still commits anything.

    Returns:
        The higher of the two monthly figures, as a Decimal.
    """
    checking_ids = _checking_account_ids(core.accounts)
    historical = _recent_settled_expenses_monthly(
        checking_ids, core.balance_ctx.reported_periods(), core.current_period,
        core.balance_ctx.scenario_id, calendar.cadence,
    )
    floor = _committed_expense_floor(checking_ids, core.balance_ctx)
    return max(historical, floor)


def _get_dti_label(dti_pct: Decimal) -> str:
    """Return the DTI health label based on conventional thresholds.

    Boundaries: < 36% is healthy, 36%--43% is moderate, > 43% is high.
    36.0% is moderate (not healthy).  43.0% is moderate (not high).

    Args:
        dti_pct: DTI as a percentage (e.g. Decimal("34.2")).

    Returns:
        'healthy', 'moderate', or 'high'.
    """
    if dti_pct < _DTI_HEALTHY_THRESHOLD:
        return "healthy"
    if dti_pct > _DTI_HIGH_THRESHOLD:
        return "high"
    return "moderate"


def _dti_metrics(
    total_monthly_payments: Decimal, gross_monthly: Decimal,
) -> DtiMetrics | None:
    """Derive the DTI block from monthly debt payments and gross biweekly pay.

    A PURE function returning the whole block or ``None`` (plan step X-s3,
    ruling R-BD).  It used to MUTATE a debt-summary dict, writing three keys in
    one branch and three ``None`` s in the other -- so the "no income data"
    state was spelled three times and read as three predicates by two
    templates, and the summary object was never fully constructed in any one
    place.

    **It takes the MONTHLY gross, and the paycheck-to-monthly conversion moved
    OUT at plan step R7a-2a.**  This function used to take the per-paycheck
    gross and convert it here against a hardcoded
    ``PAY_PERIODS_PER_YEAR / MONTHS_PER_YEAR``, justified as "a structural
    property of the 26-period pay schedule (Shekel is a biweekly app)" -- a
    justification that was false of the schema it described, since
    ``cadence_days`` is user-selectable 1..365.  Giving it the owner's cadence
    instead was the obvious repair and it was the wrong shape twice over: a DTI
    is a ratio of two MONTHLY figures, so the unit conversion was never this
    function's job; and resolving a cadence for it meant reading the owner's
    schedule on a page that answers ``None`` here whenever no salary is
    configured, which put a 500 in front of an owner with a mortgage and no
    salary profile.  The caller converts, behind the one condition that decides
    whether there is anything to convert.

    Args:
        total_monthly_payments: The debt summary's PITI total.
        gross_monthly: Engine-derived gross MONTHLY income, already converted
            from the owner's paycheck at their own cadence and rounded to
            cents by the caller.  ``0.00`` when no salary is configured.

    Returns:
        The :class:`DtiMetrics`, or ``None`` when ``gross_monthly`` is zero --
        no income source, which a consumer must distinguish from a real zero
        ratio (E-12).
    """
    if gross_monthly <= Decimal("0.00"):
        return None
    return DtiMetrics(ratio=(
        total_monthly_payments / gross_monthly * Decimal("100")
    ).quantize(Decimal("0.1"), rounding=ROUND_HALF_UP))
