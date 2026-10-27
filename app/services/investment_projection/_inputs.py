"""
Shekel Budget App -- investment contribution inputs and the growth timeline.

The readers of the priced payroll feed: the per-account contribution facts a
card shows (:func:`calculate_investment_inputs`), the employer-contribution
parameter block (:func:`employer_contribution_params`), and the dated
:class:`~app.services.growth_engine.ContributionRecord` series the growth
engine replays (:func:`build_contribution_timeline`).

The feed itself is :mod:`app.services.investment_projection._feed`; the
package docstring carries the argument for why the deduction half arrives
priced rather than computed here.
"""

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Optional

from app import ref_cache
from app.enums import EmployerContributionTypeEnum
from app.services.growth_engine import ContributionRecord
from app.services.investment_projection._feed import AccountPayrollFeed
from app.utils.money import ZERO, round_money


@dataclass(frozen=True)
class PricedContribution:
    """ONE transfer contribution, already valued, screened and DATED.

    The boundary record this module consumes in place of a
    :class:`~app.models.transaction.Transaction` (plan step X-au-c2).  It
    carries exactly the four facts the readers here need, so a row's amount is
    resolved once by the loader that has a session rather than four times by
    functions that do not.

    **Non-contributing rows are ABSENT rather than zero, and that is load
    bearing rather than tidy.**  :func:`_average_transfer_contribution` divides
    by the number of DISTINCT paydays it sees, so a Cancelled contribution
    carried as ``$0.00`` would enlarge that denominator and quietly reduce the
    average -- where the ``status_contributes_to_balance`` screen it replaces
    dropped the row before the count.  The loader applies that screen and omits
    what fails it.

    **It carries the PAYDAY rather than the ``pay_period_id`` since plan step
    C2-f2c**, and that is what let the period LIST leave this module's public
    surface.  Every reader here bucketed on the id and then needed a lookup
    table to find out WHEN that period was: the YTD windows built a set of ids
    filtered by each period's ``start_date``, and the timeline built an
    id-keyed map to stamp each record with one.  So the period list was an
    argument three functions took to answer one question the loader can answer
    once, where the session is -- the same move plan step X-au-c2 made for the
    amount and the status.  It also ended a shape collision: this module is
    shared with ``/retirement``, which holds ORM rows spelling that key ``id``,
    while ``/investment`` now holds
    :class:`~app.services.pay_calendar.DerivedPeriod`\\ s spelling it
    ``period_id``.

    A payday identifies a period as exactly as the id does: paydays are unique
    per owner (``uq_pay_periods_user_start``) and every batch the loader builds
    is scoped to one.

    Attributes:
        account_id: The investment / retirement account the contribution
            landed in.  Read by the cross-account consumers, which load one
            batch and partition it per account.
        payday: The ``start_date`` of the pay period the contribution belongs
            to -- the day every average, YTD sum and timeline record here dates
            it at.  It is a PAYDAY rather than a posting date on purpose: the
            growth engine matches a contribution to a period by that period's
            opening day.
        amount: What the to-side leg CONTRIBUTES: the figure its transfer
            stores for a leg that has SETTLED
            (:func:`app.services.row_valuation.leg_settled_contribution`), else
            what the transfer resolves to
            (:func:`app.services.cash_ledger.planned_leg_contribution`).
        is_confirmed: Whether the contribution actually happened, as opposed
            to being still projected: WHICH half of
            :func:`app.services.loan_loaders.income_legs` the leg came from.
            The growth engine's
            :class:`~app.services.growth_engine.ContributionRecord` takes it
            verbatim.
    """

    account_id: int
    payday: date
    amount: Decimal
    is_confirmed: bool


@dataclass(frozen=True)
class TransferContributions:
    """A batch of priced contributions, and WHICH accounts had any at all.

    Two facts that must travel together, because screening the first destroys
    the second and one consumer needs each (plan step X-au-c2).

    **The second field exists because an adversarial review caught the screen
    silently answering a different question.**  ``retirement_projection``'s
    ``none_linked`` is a PRESENCE test -- *is anything linked to fund this
    account?* -- and it read the loader's list length.  Before the screen moved
    to the boundary that list carried Cancelled and Credit rows, so an account
    whose contributions were all cancelled reported ``you $0.00 / employer
    $0.00``; screening them out at the loader would have flipped it to the
    "nothing linked yet" call-to-action, telling the owner to link a
    contribution that already exists.

    Separating them is the correct design rather than a compatibility shim:
    *what does this account receive* and *is anything wired up to it* are
    different questions, and conflating them is exactly what let one change
    answer the second while only meaning to change the first.

    Attributes:
        records: The contributions that COUNT -- screened by
            ``status_contributes_to_balance`` and priced through the amount
            model.  Cancelled and Credit rows are absent (see
            :class:`PricedContribution` on why absent rather than zero).
        linked_account_ids: Every account id that had a live transfer INTO
            it in the window, WHATEVER its status.  A cancelled contribution is
            still a link.
    """

    records: list[PricedContribution]
    linked_account_ids: frozenset[int]


@dataclass
class InvestmentInputs:
    """All inputs needed for growth_engine.project_balance().

    ``ytd_contributions`` and ``ytd_contributions_seed`` are two YTD views
    of the same contribution stream that differ only on the current period
    (deep-quality-hunt #10):

    * ``ytd_contributions`` -- contributions this calendar year *through*
      the current period (``<=``).  This is the displayed limit-card value.
    * ``ytd_contributions_seed`` -- contributions this calendar year
      *strictly before* the current period (``<``).  This is the
      ``ytd_contributions_start`` handed to the growth engine, whose own
      per-period walk then applies and counts the current period's
      contribution against the limit.  Seeding the through-current value
      instead would charge the current period against the annual limit
      twice.  The two views converge at the engine's current-period row.

    ``periodic_contribution`` is the CURRENT period's employee amount rather
    than a figure held for all time (plan step **salary:R14-b**).  It was
    ``annual_salary / <paycheck count>`` -- one number for every period the
    projection reached, which is finding **D45** -- and the forward walk now
    reads a dated record per period (:func:`build_contribution_timeline`), so
    what is left for this field to answer is the per-period CARD the
    investment and retirement dashboards render.  ``gross_biweekly`` left with
    the same cutover: a gross is a fact about a PAYDAY, so it lives on the
    :class:`AccountPayrollFeed` keyed by one, and no consumer read this copy.
    """
    periodic_contribution: Decimal
    employer_params: Optional[dict]
    annual_contribution_limit: Optional[Decimal]
    ytd_contributions: Decimal
    ytd_contributions_seed: Decimal



def _average_transfer_contribution(all_contributions):
    """Average per-period contribution from priced transfer contributions.

    ``all_contributions`` are :class:`PricedContribution` records already
    filtered to one account by the caller.  Cancelled / Credit rows never reach
    here: the loader that priced them applied the
    ``status_contributes_to_balance`` screen and omitted what failed it, so this
    module holds no copy of that rule (plan step X-au-c2).  The screen still
    shares ONE definition with the SQL filters in ``year_end_summary_service`` /
    ``savings_dashboard_service`` -- it just lives at the boundary now.

    Contributions are summed on the record's :attr:`~PricedContribution.amount`
    -- the figure its transfer stores when the leg has settled, else what the
    transfer RESOLVES to -- which is the same figure the per-period timeline reads off
    the same records, so this average and the YTD/limit accounting cannot
    disagree with the engine on a settled transfer whose actual differs from its
    estimate (deep-quality-hunt #11).

    Args:
        all_contributions: List of :class:`PricedContribution` records.

    Returns:
        The per-period average contribution (Decimal), or ZERO when no
        contributions exist.
    """
    if not all_contributions:
        return ZERO

    total_contrib = sum(c.amount for c in all_contributions)
    # DISTINCT PAYDAYS, which is distinct periods: a payday is unique per owner
    # (``uq_pay_periods_user_start``) and one batch is one owner's.  It read
    # ``pay_period_id`` until plan step C2-f2c moved the period key onto the
    # record's own date; the denominator is the same set either way.
    num_periods_with_contrib = len(
        set(c.payday for c in all_contributions)
    )
    if num_periods_with_contrib > 0:
        return round_money(total_contrib / num_periods_with_contrib)
    return ZERO


def employer_contribution_params(investment_params) -> "dict | None":
    """Build the employer-contribution params dict, or None.

    Public because the balance seam's modelled asset fold
    (``balance_at._asset_fold``) sizes the employer amount per pay period
    off the RESOLVED employee total for that period (plan ruling R-R
    consequence (a)), so it needs this dict without the transfer-averaged
    ``periodic_contribution`` :func:`calculate_investment_inputs` bundles
    it with.  It is the only shape
    :func:`~app.services.growth_engine.calculate_employer_contribution`
    accepts, so building it anywhere else would be a second statement of
    the same mapping.

    **It no longer embeds a gross, since plan step salary:R14-b.**  The dict
    carried ``gross_biweekly`` -- ONE figure sizing every period's employer
    contribution for the life of a projection, which on the developer's own
    data froze a 5% match at today's `$3,631.74` while the engine's gross
    walked `$3,525.96` -> `$4,047.97` across the same 63 paydays.  A gross is
    a fact about a PAYDAY (**R-SAL2**), so it is
    :meth:`AccountPayrollFeed.gross_at`'s to answer and every caller supplies
    the period's own; there is no constant left for one to be resolved
    against.

    Args:
        investment_params: Object with ``employer_contribution_type_id``
                           and the ``employer_*_percentage`` fields.

    Returns:
        A dict describing the employer contribution, or None when the
        account has no employer contribution configured.  The dict
        carries the employer-type ref id under ``type_id`` (#38) so the
        growth engine branches on the id, not a string.
    """
    emp_type_id = getattr(investment_params, "employer_contribution_type_id", None)
    none_id = ref_cache.employer_contribution_type_id(
        EmployerContributionTypeEnum.NONE
    )
    if emp_type_id is None or emp_type_id == none_id:
        return None
    return {
        "type_id": emp_type_id,
        "flat_percentage": getattr(
            investment_params, "employer_flat_percentage", None) or ZERO,
        "match_percentage": getattr(
            investment_params, "employer_match_percentage", None) or ZERO,
        "match_cap_percentage": getattr(
            investment_params, "employer_match_cap_percentage", None) or ZERO,
    }


def _ytd_contributions(all_contributions, current_period, *, inclusive):
    """Sum this calendar year's contributions up to the current period.

    ``inclusive`` controls the current period itself: ``True`` keeps it
    (``<=``, the through-current YTD shown on the limit card); ``False``
    drops it (``<``, the strictly-before seed handed to the growth engine,
    whose per-period walk then applies and counts the current period's own
    contribution against the annual limit -- seeding the through-current value
    there would charge that period twice, deep-quality-hunt #10).  ONE
    expression for both keeps them from drifting.

    Both bounds read the current period's own ``start_date``, so the year and
    the boundary are one fact rather than two: a contribution counts when its
    PAYDAY falls in that period's calendar year at or before that period's
    payday.  **It used to build a set of period IDS and match each record's
    ``pay_period_id`` against it**, which needed the owner's whole period list
    as an argument; the record carries its payday since plan step C2-f2c, so
    the list has nothing left to answer (see :class:`PricedContribution`).  The
    two select identically -- every record the loader returns belongs to a
    period in that list, because the list is what scoped the query.

    Every record here has already passed the boundary's
    ``status_contributes_to_balance`` screen, so this sums what it is given.
    :attr:`~PricedContribution.amount` -- the figure its transfer stores when
    the leg has settled, else what the transfer resolves to -- is the ONE answer
    to what a leg contributes, so this YTD/limit accounting agrees with the
    per-period timeline (:func:`build_contribution_timeline`, reading the same
    records) once a transfer settles at a figure that differs from its estimate
    (deep-quality-hunt #11).  Summing ``estimated_amount`` here previously let
    the cap/limit math read a different dollar than the engine actually
    applied; the prior "F-027 S18 contract-safe" rationale assumed a shadow's
    ``actual_amount`` was always ``None``, which was untrue once a settle set
    it.  The settled figure has lived on the transfer itself since plan step
    balance:X-bi-6-4d-2 deleted the shadows.

    Args:
        all_contributions: :class:`PricedContribution` records for one account.
        current_period:    The current period object -- anything carrying a
                           ``start_date`` -- or None.
        inclusive:         Keyword-only; include the current period or not.

    Returns:
        The contribution total (Decimal); ZERO when ``current_period`` is None,
        the state in which there is no year and no boundary to ask about.
    """
    if current_period is None:
        return ZERO
    boundary = current_period.start_date
    return sum(
        (
            c.amount for c in all_contributions
            if c.payday.year == boundary.year
            and (c.payday <= boundary if inclusive else c.payday < boundary)
        ),
        ZERO,
    )


def calculate_investment_inputs(
    investment_params,
    feed: AccountPayrollFeed,
    all_contributions,
    current_period,
):
    """Compute projection inputs for an investment account.

    **It stopped taking the owner's period LIST at plan step C2-f2c.**  The
    list served the two YTD windows alone, as a lookup from a contribution's
    ``pay_period_id`` to that period's payday; the loader that prices a
    contribution now dates it too, so there is no lookup left to do and one
    argument fewer for a caller to get wrong.  That also retired this
    function's ``too-many-arguments`` disable rather than re-justifying it.

    **``periodic_contribution`` is the CURRENT period's figure since plan step
    salary:R14-b, and it is no longer the forward walk's input.**  It was one
    raise-blind scalar the whole projection ran on (finding **D45**); the walk
    reads a dated record per period now
    (:func:`build_contribution_timeline`), so what this answers is the
    per-period CARD both dashboards render -- *what does a paycheck put in*,
    asked of the paycheck the owner is actually being paid.  The ``deductions``
    and ``salary_gross_biweekly`` arguments left with it: the feed carries both
    facts, keyed by the payday that makes each one true.

    Args:
        investment_params:     Object with employer fields and
                               ``annual_contribution_limit``.
        feed:                  The account's :class:`AccountPayrollFeed` --
                               what its payroll puts in per payday, priced by
                               the paycheck engine at the boundary.
        all_contributions:     List of :class:`PricedContribution` records
                               for this account -- to-side legs already
                               valued, screened and dated at the boundary.
        current_period:        The current
                               :class:`~app.services.pay_calendar.DerivedPeriod`,
                               or None.  It is the paycheck the per-period
                               figures above are read off -- the feed hands
                               it to the engine, since plan step
                               salary:S3-e-2 (ruling **R-SAL19**), so an ORM
                               row no longer serves here; ``None`` leaves them
                               at ``$0.00``, the same state the two YTD windows
                               already answer zero for.

    Returns:
        InvestmentInputs dataclass.
    """
    periodic_contribution = (
        feed.employee_at(current_period)
        if current_period is not None else ZERO
    )
    periodic_contribution += _average_transfer_contribution(all_contributions)

    return InvestmentInputs(
        periodic_contribution=periodic_contribution,
        # An employer contribution with no KNOWN funding profile models
        # nothing (developer, 2026-09-04): there is no gross to take a
        # percentage OF, so the params are withheld rather than paired with a
        # basis of zero.  The two states stay distinguishable for the
        # surfaces -- ``employer_params is None`` says no money, and
        # ``feed.funds_employer`` says WHY -- which is the half of that ruling
        # reading "and say so".
        employer_params=(
            employer_contribution_params(investment_params)
            if feed.funds_employer else None
        ),
        annual_contribution_limit=getattr(
            investment_params, "annual_contribution_limit", None),
        ytd_contributions=_ytd_contributions(
            all_contributions, current_period, inclusive=True),
        ytd_contributions_seed=_ytd_contributions(
            all_contributions, current_period, inclusive=False),
    )


def build_contribution_timeline(
    feed: AccountPayrollFeed,
    contribution_transactions,
    periods,
    as_of,
):
    """Build ContributionRecords from the payroll feed and transfer legs.

    Combines two contribution paths into a unified per-period timeline
    for the growth engine:

    Path 1 -- Paycheck deductions: what the paycheck engine says this
    account's deductions took from each period's paycheck
    (:meth:`AccountPayrollFeed.employee_at`) -- raise-aware,
    inflation-escalated, cadence-placed and clamped to each line's own
    calendar-year ``annual_cap`` -- for EVERY period of the domain, saved or
    projected.  **Past the owner's saved schedule it used to be the feed's
    HOLD**, a complete year's average that was none of those four things
    and, on a 40-year chart, most of the periods; plan step
    **salary:S3-e-2** deleted the hold and the feed prices a projected
    payday through the engine like any other (ruling **R-SAL15**).
    Confirmation is date-based (past period = confirmed) because there is
    no per-period transaction record for deductions.

    Path 2 -- Transfer-based contributions: Per-record amounts from the priced
    transfer contributions.  Confirmation is settlement-based
    (:attr:`PricedContribution.is_confirmed`, decided at the boundary by which
    half of the loan loaders' partition the leg came from) -- factual from the
    transfer workflow.

    The growth engine handles same-date aggregation (summing amounts,
    conservative is_confirmed rule) via its lookup dict.

    **The path-1 gate is PRESENCE, not price** (an adversarial review of plan
    step salary:R14-b moved it).  A first build asked *did any priced payday
    pay this account*, which a genuinely configured deduction answers ``no``
    to: one fully consumed by its ``annual_cap`` across the whole priced
    window prices to ``$0.00`` on every payday, and so does a 12-per-year
    deduction on a saved window shorter than a month holding no ordinal-1
    payday -- reachable because ``_month_ordinal`` counts over the owner's
    RHYTHM, not the window, and ``PERIOD_BATCH_MIN`` is 1.  That fed the
    engine no records at all, so its
    ``periodic_contribution`` fallback applied the TRANSFER AVERAGE to periods
    that should contribute nothing.  :attr:`AccountPayrollFeed
    .is_payroll_linked` is the question the gate means -- *is a deduction
    wired to this account* -- and plan step **salary:S3-e-1** made it the
    only one either consumer asks, for the reason that field states.

    **Path 1 stopped computing anything at plan step salary:R14-b.**  It ran
    each deduction's amount off the profile's stored annual salary and then
    re-applied the calendar-year cap through a private year-state walk
    (``_deduction_contribution_records`` / ``_period_capped_total``) -- a
    second and third answer to a question the paycheck engine answers when it
    prices the paycheck.  A record is still emitted for EVERY period, a fully
    capped ``$0`` included, because a missing record is what makes the growth
    engine fall back to ``periodic_contribution``: it is the difference
    between *this paycheck contributed nothing* and *nobody said*.

    **It reads no clock since plan step C2-f2c**, and no period IDENTITY
    since the same step: the confirmation split took ``date.today()``, so a
    render that straddled midnight could date this timeline one day and the
    pass around it another; it takes the read pass's own ``as_of`` now.  And
    path 2 resolved each contribution's date through an id-keyed map of
    *periods*, which forced this function to know how the caller's period
    type spells its primary key -- ``id`` on an ORM row, ``period_id`` on a
    :class:`~app.services.pay_calendar.DerivedPeriod`.  A contribution
    carries its own payday now, and the one boundary this function still
    needs is asked of the period by NAME
    (:attr:`~app.services.pay_calendar.DerivedPeriod.is_projected`), so it
    still does not know that spelling.  *The period is a*
    :class:`~app.services.pay_calendar.DerivedPeriod` *all the same*: the
    feed hands it to the paycheck engine since plan step salary:S3-e-2
    (ruling **R-SAL19**), and an ORM row cannot reach the engine, so the
    "both types serve" clause of **R-SAL18** is revised by that ruling.

    Args:
        feed:                       The account's :class:`AccountPayrollFeed`
                                    -- what its payroll puts in per period.
        contribution_transactions:  List of :class:`PricedContribution`
                                    records -- to-side legs already
                                    valued, screened and dated at the boundary.
        periods:                    The timeline's DOMAIN:
                                    :class:`~app.services.pay_calendar
                                    .DerivedPeriod` values, one record
                                    emitted per period for the deduction path
                                    and any contribution outside them dropped.
                                    It may run PAST the owner's saved schedule
                                    -- the 40-year chart's axis does -- and
                                    each such period says so itself.
        as_of:                      The read pass's clock; a period opening
                                    strictly before it is confirmed.

    Returns:
        list[ContributionRecord] sorted by contribution_date.  Empty
        list when the account has no payroll feed and no qualifying
        contribution.
    """
    records = []

    # Path 1: Paycheck deductions -- the engine's own figure for each period,
    # PLUS the transfer average on a period the schedule does not reach.
    #
    # **That second term is a RESTORE, and leaving it out was a measured
    # regression salary:R14-b's own adversarial review caught.**  The growth
    # engine's rule is that a dated record REPLACES the periodic fallback, and
    # ``periodic_contribution`` WAS the only carrier of
    # :func:`_average_transfer_contribution` before that step -- the term
    # below is the second, which is the whole of the restore.  Before it this
    # timeline's domain was the owner's SAVED window, so every period past it
    # had no record and fell back to *deduction + average*; widening the
    # domain to the projection axis without carrying the average would have
    # dropped an account's whole recurring-transfer stream out of the forward
    # walk, for the entire horizon, for every account funded by BOTH a
    # deduction and transfers.
    #
    # **The asymmetry is inherited, not chosen**: the average applies only
    # PAST the saved window and not inside it, where a period without a
    # recorded transfer contributes the deduction alone.  Nobody designed
    # that -- it falls out of the fallback rule meeting the old domain -- and
    # a step ruled about what a DEDUCTION is priced from may not quietly
    # re-rule what a TRANSFER projects to.  The developer ruled it a defect
    # (plan#1786): it is finding plan#1788, which step X-m (plan#1046) owns.
    #
    # **The boundary is the PERIOD's own** (ruling **R-SAL18**, plan step
    # salary:S3-e-2).  The question was never about pricing: it is *has the
    # owner's schedule reached this day*, because that decides whether a
    # dated transfer record could exist for it.  It was ``feed.prices()``
    # until S3-e-1, which answered the same days only while the feed was
    # built over ``calendar.saved()``; then a ``saved_through`` date the
    # CALLER read off a calendar that had to be the one the axis came from,
    # a pairing an AST census fenced.  A period past the schedule already
    # carries the fact -- it is a projection with no row -- so asking it
    # deletes the parameter, the wiring and the census, and a mismatched
    # pair is not a state anyone can express.  The question is one-sided by
    # construction: ``projection_axis`` RAISES its opening day to
    # ``opening_bound`` and ``axis`` refuses below it, so no axis holds a
    # period that predates the schedule.
    if feed.is_payroll_linked:
        beyond = _average_transfer_contribution(contribution_transactions)
        records.extend(
            ContributionRecord(
                contribution_date=period.start_date,
                amount=(
                    feed.employee_at(period)
                    + (beyond if period.is_projected else ZERO)
                ),
                # Past periods are confirmed (the deduction was taken from the
                # paycheck); future periods are projected.
                is_confirmed=period.start_date < as_of,
            )
            for period in sorted(periods, key=lambda p: p.start_date)
        )

    # Path 2: Transfer-based contributions -- per-transaction amounts.
    paydays = {p.start_date for p in periods}
    for contribution in contribution_transactions:
        if contribution.payday not in paydays:
            # Contribution in a period outside this timeline's domain.
            continue
        records.append(ContributionRecord(
            contribution_date=contribution.payday,
            amount=contribution.amount,
            # Transfer-based: determined by the row's settlement status
            # (Paid/Received=True, Projected=False), resolved at the boundary.
            is_confirmed=contribution.is_confirmed,
        ))

    records.sort(key=lambda r: r.contribution_date)
    return records
