"""Balance-at-T seam -- the modelled CONTRIBUTION tier of the asset replay.

Plan step **X-g2a** (``docs/audits/balance_architecture/README.md`` Section 3.2).
Split out of :mod:`app.services.balance_at._asset_fold` at its module-size
ceiling, on the cohesion line plan step D1c drew for ``cash_ledger``: that module
answers "what is this asset worth on each day", and this one answers the one
question it has to ask a second package to resolve -- "what does this account's
payroll put IN, and when".

**Ruling R-R partitions a contribution by SOURCE**, and this module is the
modelled half of that partition.  A recorded transfer HAS a transaction row, so
it is already an ACTUAL / PLANNED event in the cash fold underneath the replay; a
payroll deduction never has one, so it is the modelled CONTRIBUTION event
:func:`contribution_events` dates.  The two feeds are therefore disjoint BY
CONSTRUCTION and there is no de-dup rule to get wrong -- which is why nothing
here reads ``investment_projection._inputs._average_transfer_contribution``,
the scalar
that folds both feeds into one number and so makes them indistinguishable.  The
recorded feed IS read here, for the two things a cash delta cannot say: how much
of the calendar year's contribution limit is already consumed, and what employee
total the employer match sizes off (ruling R-R consequence (a)).

Boundary discipline (``CLAUDE.md``): no Flask symbol, no writes; all money is
:class:`~decimal.Decimal`.
"""

from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal

from app.models.account import Account
from app.models.investment_params import InvestmentParams
from app.services import growth_engine
from app.services.cash_ledger import (
    AmountBasis,
    ReconciledThrough,
    planned_leg_contribution,
    transfer_pricing_load_options,
)
from app.services.account_projection import (
    AccountProjectionKind,
    classify_account,
)
from app.services.investment_projection import (
    AccountPayrollFeed,
    employer_contribution_params,
)
from app.services.loan_loaders import (
    projected_income_legs,
    settled_income_legs,
)
from app.services.pay_calendar import DerivedPeriod, PeriodWindow
from app.services.row_valuation import leg_settled_contribution

_ZERO = Decimal("0")


@dataclass(frozen=True)
class ContributionInputs:
    """One account's modelled-contribution feed, as the caller batch-loaded it.

    The three per-account projection inputs the replay needs to model a
    contribution, bundled because a cohesive named concept finally emerged for
    them (plan step X-g2a).  Plan step X-g1 deliberately did NOT bundle them --
    it had one caller, and its own docstring recorded that re-wrapping three
    independent inputs "in a bundle no other caller shares would be the stamp
    coupling the standards reject".  X-g2b is what changed that: seven readers
    hand them over there, several of which cannot have a contribution feed at
    all.  Spelling "nothing" as three separate literals at each such site is the
    argument-a-caller-can-get-wrong shape the plan's Section 8 rules a defect
    rather than a contract; :meth:`absent` makes it one token that cannot be
    half-written.

    They stay a caller's argument rather than something this module loads,
    deliberately: the seam batch-loads them ONCE per account SET
    (``_inputs._contribution_inputs_for_accounts`` -- one investment-params
    query, one deductions query, one raise-aware gross fetch), and loading them
    per account here would reinstate the N+1 that loader exists to avoid.

    Attributes:
        investment_params: The account's
            :class:`~app.models.investment_params.InvestmentParams`, or ``None``
            when it is not a parameterized investment account.  It also carries
            the assumed return the ACCRUAL tier reads, which is why the replay
            takes this bundle rather than the deduction feed alone.
        feed: The account's
            :class:`~app.services.investment_projection.AccountPayrollFeed` --
            what its payroll puts in on each period's paycheck, and what
            gross funds its employer contribution, both priced by the
            PAYCHECK ENGINE (plan step **salary:R14-b**, ruling **R-SAL2**)
            for whichever period is asked (plan step **salary:S3-e-2**,
            ruling **R-SAL15**).  It replaced two fields: the adapted
            deduction rows, which this tier re-priced off the profile's
            stored annual salary and so read RAISE-BLIND (finding **D45**),
            and one ``salary_gross_biweekly`` scalar that sized every
            period's employer contribution at today's gross.  Both were one
            figure standing in for a series; a paycheck is what makes either
            fact true, so both are answered per period now.
    """

    investment_params: InvestmentParams | None = None
    feed: AccountPayrollFeed = field(default_factory=AccountPayrollFeed.absent)

    @classmethod
    def absent(cls) -> "ContributionInputs":
        """Return the inputs of an account that models NO contribution feed.

        The explicit token for "this reader cannot have contributions".  Named
        rather than spelled out so a call site that means it says so, and so a
        reader never has to decide whether an empty deduction list beside a zero
        gross was meant or forgotten.

        **Its production audience is down to ONE** -- the modelled-return
        accessor in :mod:`._kernel` -- because plan step X-g3b deleted the
        grid's kind gate and the grid now loads a REAL feed
        (:func:`._inputs._contribution_inputs_for_account`).  The constructor
        stays: it is what a reader that genuinely has no feed should say, and
        the tests that build a bundle by hand use it.

        Returns:
            The empty :class:`ContributionInputs`.
        """
        return cls()


@dataclass(frozen=True)
class _ContributionPlan:
    """What an INVESTMENT account's modelled contributions are made of.

    A cohesive assembly record (:func:`_plan_for`): the modelled payroll feed,
    the employer configuration, the annual limit, and the RECORDED
    contributions per pay period -- which are read for the limit and the
    match base and never contributed again (ruling R-R).

    Attributes:
        feed: The account's
            :class:`~app.services.investment_projection.AccountPayrollFeed`,
            asked per period for the employee amount and for the gross the
            employer contribution is a percentage of.  **It was one
            ``per_period`` scalar until plan step salary:R14-b**, which is
            finding **D45**: a deduction's amount and its paycheck's gross
            both move with every raise, and one figure applied to all 63 of
            the developer's saved paydays could only be right for the periods
            that happened to share it.
        employer_params: The employer-contribution configuration
            (:func:`~app.services.investment_projection.employer_contribution_params`),
            or ``None`` when the account has none.  It no longer embeds a
            gross; the period's own comes off :attr:`feed`.
        annual_limit: The account's annual employee-contribution ceiling, or
            ``None`` for an account with no IRS limit.
        recorded_by_period: pay_period_id -> the summed CONTRIBUTION of the
            transfer-linked rows actually recorded in that period.
    """

    feed: AccountPayrollFeed
    employer_params: dict | None
    annual_limit: Decimal | None
    recorded_by_period: dict[int, Decimal]


def _recorded_contributions(
    basis: AmountBasis, account_id: int,
) -> dict[int, Decimal]:
    """Return the transfer-linked contributions recorded per pay period.

    The RECORDED half of ruling R-R's partition, loaded through the loan
    loaders' ONE settled/projected partition -- the app's one definition of "a
    contribution into this account" (a transfer INTO it, excluding soft-deleted
    and balance-excluded ones), which the YTD and limit accounting already
    read.  **Both halves are legs of their transfers since plan step
    balance:X-bi-6-4b**, divided by one test (ruling **R-BAL140**): a
    contribution that has SETTLED is read by
    :func:`~app.services.loan_loaders.settled_income_legs` and worth what
    its record moved
    (:func:`~app.services.row_valuation.leg_settled_contribution`); one still
    PROJECTED is read by
    :func:`~app.services.loan_loaders.projected_income_legs` and worth what
    the parent resolves to
    (:func:`~app.services.cash_ledger.planned_leg_contribution`).  The settled
    half was the income-shadow row, read from ``budget.transactions``, from
    plan step balance:X-bi-6a until then.  Both file under their transfer's
    period, the one the fold lands them in.

    These rows are NOT contributed by this module: they are ordinary ACTUAL /
    PLANNED events in the cash fold underneath it, which is exactly why the
    partition needs no de-dup rule.  They are read here for the two things the
    fold cannot know from a cash delta alone -- how much of the year's
    contribution limit is already consumed, and what employee total the employer
    match sizes off (ruling R-R consequence (a)).

    Unwindowed by pay period, deliberately: the limit is a CALENDAR-YEAR
    recurrence, so a windowed feed would under-count the year a projection
    starts in.  It is windowed by account and scenario, which is the whole
    domain.

    **The settled half states no load and the projected half states the
    transfer's own.**  A settled leg is valued from its record, the covering
    movement the producer's one join attaches, so that path loads nothing it
    does not already carry (it took
    :func:`~app.utils.amount_relationships.settlement_load_options` for the
    shadow's ENTRIES until plan step balance:X-bi-6-4b); a projected leg's
    parent is priced through its definition, so that path takes
    :func:`~app.utils.amount_relationships.transfer_pricing_load_options`.
    *It passed ``pricing_load_options()`` over ONE mixed row set until plan
    step balance:X-bi-6a, because the projected shadows in it walked to their
    parents.*

    Args:
        basis: The read pass's
            :class:`~app.services.cash_ledger.AmountBasis`, carrying the owner
            and the scenario the rows live in.
        account_id: The account receiving the contributions.

    Returns:
        ``{pay_period_id: total}`` over what each contribution is WORTH -- the
        realized actual for a settled leg, the parent's resolved amount for
        a projected one.  ``{}`` for an account with none.
    """
    totals: dict[int, Decimal] = {}
    for leg in settled_income_legs(
        account_id, basis.scenario_id, options=(),
    ):
        totals[leg.pay_period_id] = (
            totals.get(leg.pay_period_id, _ZERO) + leg_settled_contribution(leg)
        )
    for leg in projected_income_legs(
        account_id, basis.scenario_id,
        options=transfer_pricing_load_options(),
    ):
        totals[leg.pay_period_id] = (
            totals.get(leg.pay_period_id, _ZERO)
            + planned_leg_contribution(leg, basis)
        )
    return totals


def _plan_for(
    account: Account, basis: AmountBasis, inputs: ContributionInputs,
) -> "_ContributionPlan | None":
    """Assemble *account*'s modelled contribution plan, or ``None`` if it has none.

    ``None`` when nothing is modelled at all -- no active deduction NAMES
    this account AND no employer contribution can be sized -- which is what
    keeps a plain IRA from paying for a period-calendar load it has no use
    for.  The employee half was *no deduction PAYS this account on any
    priced payday* until plan step **salary:S3-e-1**, which is a question
    about a window; the comment on the test below says what happened to
    the window and why the answer is unmoved.  Note that an employer FLAT
    percentage models money with a zero employee feed (the real Empower
    401(k) shape: 5% of a paycheck's own
    gross, `$181.59` rising to `$202.40` across the paydays this walk actually
    reaches -- the earlier `$176.30` paydays fall at or before the account's
    assertion and ruling **R-Z** excludes them; see :func:`_dated_events`),
    so the test is on both halves, not on the employee amount alone.

    **The employer half's test gained a second conjunct at plan step
    salary:R14-b**: a configured employer contribution models money only when
    a funding profile is KNOWN (``budget.investment_params.salary_profile_id``
    -- ruling **R-SAL5**, and the developer's 2026-09-04 ruling that an
    unknown one models nothing rather than being priced off whichever profile
    a reader resolved).  Without it this would build a plan whose every
    employer figure is ``$0.00``, which is the same money and a worse
    explanation of it.

    Args:
        account: The investment account.
        basis: The read pass's amount basis, carrying the scenario the recorded
            contributions live in and the derivations pricing them.
        inputs: The account's :class:`ContributionInputs`.  Its
            ``investment_params`` is not ``None`` -- :func:`contribution_events`
            guards that.

    Returns:
        The :class:`_ContributionPlan`, or ``None``.
    """
    employer_params = employer_contribution_params(inputs.investment_params)
    models_employer = (
        employer_params is not None and inputs.feed.funds_employer
    )
    # PRESENCE, the same question :func:`build_contribution_timeline` asks,
    # and plan step **salary:S3-e-1** made the two one.  This asked a PRICE
    # question -- *did any priced payday pay this account* -- on the argument
    # that a linked deduction pricing $0.00 on every payday of the window
    # models nothing.  It survived only while there WAS a window: since plan
    # step **salary:S3-e-2** a feed answers whatever period it is asked, so
    # "priced" would mean "whatever this caller asked for" and the gate would
    # answer differently for two readers of one account.  Deleting it moved
    # no figure -- :func:`contribution_events` emits an event only for a
    # NON-ZERO amount, and every payday of such an account contributes
    # ``employee = 0`` (:func:`~app.services.growth_engine
    # .cap_contribution_at_limit` of zero is zero) plus ``employer = 0``
    # (:func:`~app.services.growth_engine.calculate_employer_contribution`
    # returns ``ZERO`` for the ``None`` params ``models_employer`` False
    # supplies below) -- and it costs the recorded-contribution queries for an
    # account that models nothing.
    if not inputs.feed.is_payroll_linked and not models_employer:
        return None
    return _ContributionPlan(
        feed=inputs.feed,
        employer_params=employer_params if models_employer else None,
        annual_limit=inputs.investment_params.annual_contribution_limit,
        recorded_by_period=_recorded_contributions(basis, account.id),
    )


def contribution_events(
    account: Account,
    basis: AmountBasis,
    inputs: ContributionInputs,
    reconciled_through: ReconciledThrough,
    periods: PeriodWindow,
) -> "list[tuple[date, Decimal]]":
    """Return *account*'s modelled CONTRIBUTION events, dated.

    The tier's ONE entry, so the replay asks "what does this account's payroll
    put in, and when" and never has to know that answering it needs the
    recorded-contribution feed or the annual limit's year boundary.

    Empty -- with no query issued -- for every account that cannot have a
    modelled feed: any kind but INVESTMENT (an INTEREST account's payroll does
    not fund it, and a Property's certainly does not), and an INVESTMENT with
    no :class:`~app.models.investment_params.InvestmentParams` to carry the
    limit and the employer configuration.

    **The pay periods are the READ PASS'S, handed in** (plan step **C2-f2a**,
    ledger row **P37**).  This function issued its own
    ``pay_period_service.get_all_periods`` until then -- a SECOND read of the
    owner's schedule inside a pass that already holds it, and the last reader
    of that module anywhere under :mod:`app.services.balance_at`.  Its ONE
    caller, :func:`._asset_fold.resolve`, now derives the window from the
    pass's own :class:`~app.services.pay_calendar.PayCalendar` -- the same
    derivation every other per-period seam entry reports over, so the
    contribution tier and the cash columns beside it cannot be resolved
    against two readings of one schedule, and no caller states a window at all.

    **The explicit sort went with the query, and the ORDER did not become an
    assumption -- it became a type.**  This door used to sort by ``start_date``
    because ``get_all_periods`` orders by the stored ``period_index``, an
    equation nothing in the schema enforces and one
    :meth:`~app.services.pay_calendar.PayCalendar.filing_period` measures
    parting company with date order on 800 of 872 probed days.  A
    :class:`~app.services.pay_calendar.PeriodWindow` sorts at construction and
    is frozen, so payday order is a property of the value rather than a step
    at this door that a second door could forget.  :func:`_dated_events` stays
    ORDER-SENSITIVE by design -- a calendar-year limit is an accumulation --
    which is why it takes the looser sequence type and this entry takes the
    window.

    Args:
        account: The account to resolve.  Its ``account_type`` drives the
            classifier and its ``user_id`` scopes the RECORDED feed -- which
            is the pass owner's too, since plan step C2-f2a took the AXIS off
            the context's calendar rather than off this account (the seam's
            standing contract makes them one owner).
        basis: The read pass's amount basis, carrying the scenario the
            recorded contributions live in and the derivations pricing them.
        inputs: The account's :class:`ContributionInputs`.
        reconciled_through: The account's coverage boundary -- the assertion
            :func:`_dated_events` admits an event strictly after (ruling R-Z).
        periods: The owner's SAVED pay periods, as
            :meth:`~app.services.pay_calendar.PayCalendar.saved` yields them.
            The WHOLE schedule, never a slice: the year-boundary reset and the
            limit accounting are wrong over one (see :func:`_dated_events`).
            That is a precondition no window type can carry, which is why
            :func:`._asset_fold.resolve` takes the CALENDAR and derives this
            rather than accepting one.

    Returns:
        ``[(payday, amount), ...]`` in payday order, one entry per period that
        contributes a non-zero amount.  ``[]`` when the account models none.
    """
    if (classify_account(account) is not AccountProjectionKind.INVESTMENT
            or inputs.investment_params is None):
        return []
    plan = _plan_for(account, basis, inputs)
    if plan is None:
        return []
    return _dated_events(plan, periods, reconciled_through)


def _dated_events(
    plan: _ContributionPlan,
    periods: "Iterable[DerivedPeriod]",
    reconciled_through: ReconciledThrough,
) -> "list[tuple[date, Decimal]]":
    """Resolve the plan into dated events, one per paying period.

    **A contribution lands on its pay period's ``start_date``, because that is
    the payday** -- the :class:`~app.models.pay_period.PayPeriod` model says so
    in its own docstring ("start_date (payday)"), and it is already the date
    ``investment_projection.build_contribution_timeline`` stamps on every
    ``ContributionRecord``.  So the money is in the account from the payday and
    earns a full period of return, where the growth engine adds a period's
    contribution AFTER its growth and so earns none in its own period.

    **It stops at the latest assertion, and the boundary is STRICT** (ruling
    R-Z): an event exists only when the assertion does NOT already reconcile
    the payday.  A contribution on a payday at or before the assertion is money
    the asserted balance already contains, and modelling it again double counts
    -- an over-count that looks exactly like real growth and so cannot be
    detected later.

    **That is ruling R-DH's question, so it is ruling R-DH's implementation**
    (:meth:`app.services.cash_ledger.ReconciledThrough.covers`).  It was a bare
    ``period.start_date <= accrual_start`` until the one-partition step, which
    an adversarial review caught: the same rule, the same units and the same
    inclusivity as the cash walks, reached as a loose date and therefore
    invisible to the census that step ran.  Re-rule the boundary and this feed
    would silently have stayed put while every cash consumer moved.

    The ACCRUAL rule beside it is inclusive at its own start for a reason that
    does NOT transfer and is deliberately NOT routed here: a day count has to
    tile the calendar with no gap, while a contribution is a discrete event
    that either is or is not inside the assertion.

    **The annual limit is a calendar-year recurrence over BOTH feeds.**  Every
    period's RECORDED contributions consume the year's limit whether or not the
    period is modelled -- they happened, so they are never capped or dropped --
    and the modelled amount is then capped against what is left, through the
    same :func:`~app.services.growth_engine.cap_contribution_at_limit` the
    growth engine applies.  The employer amount is sized off the RESOLVED
    employee total for the period, recorded plus modelled (ruling R-R
    consequence (a)), and is not itself charged against the employee limit --
    the growth engine's own rule.

    **Both figures are now the PERIOD's own** (plan step **salary:R14-b**,
    ruling **R-SAL2**).  The employee amount was one scalar repeated across
    every period and the employer gross was another, so this walk applied one
    paycheck's answer to a schedule the paycheck engine prices individually --
    finding **D45**.

    **Measured on the developer's own data 2026-09-04, and the WINDOW is part
    of the figure**: through ``balance_at.grid_balance_view`` -- the door the
    grid itself reads -- the Empower 401(k)'s modelled contribution over the
    63 saved paydays goes `$10,169.04` -> `$10,621.46`, **`+$452.42`**, and
    its balance at the last period `$49,960.31` -> `$50,442.34`.  Per payday
    that is a flat `$181.59` becoming `$181.59` / `$186.13` / `$191.71` /
    `$196.50` / `$202.40` as each raise lands.

    **56 of the 63 paydays pay, and the other 7 are why an earlier draft of
    this paragraph said `+$415.39` with early periods at `$176.30`.**  That
    figure summed the employer events over ALL 63 paydays with no
    ``reconciled_through``, so it included the seven at or before the
    account's balance assertion -- which ruling **R-Z**, three paragraphs up,
    excludes from this walk entirely.  Those seven are the only ones where
    R14-b models LESS than the old feed did (`$176.30` against `$181.59`), so
    counting them netted a real understatement against periods the app never
    reaches.  The window this walk covers is the owner's SAVED schedule minus
    what the assertion already contains.  *Until plan step salary:S3-e-2 that
    was also exactly the domain the feed had priced up front, so its hold
    rule never engaged here; the feed prices any period on demand now and
    there is no hold rule to engage.*

    Args:
        plan: The account's :class:`_ContributionPlan`.
        periods: The user's pay periods, CHRONOLOGICAL -- ordered by
            ``start_date``, the PAYDAY, which is the only fact in the row.
            **This read "ordered by ``period_index``" until plan step C2-c**,
            an equation nothing in the schema enforces and which
            ``PayCalendar.filing_period`` measures parting company with date
            order on 800 of 872 probed days; C2-c made the caller sort, and
            plan step **C2-f2a** replaced the sort with the ordered type --
            :func:`contribution_events` takes a
            :class:`~app.services.pay_calendar.PeriodWindow`, which sorts at
            construction.  **This parameter stays the looser sequence, and
            that is deliberate**: the walk below is order-SENSITIVE, and a
            control that shows it answering differently for the same plan in a
            different order is what proves the door's guarantee is
            load-bearing: only a window can express it, and only a list can
            violate it.  The whole schedule rather than a slice, too -- the
            year-boundary reset and the limit accounting are wrong over one.
        reconciled_through: The account's coverage boundary -- the latest
            assertion, as the rule that decides what it already contains.

    Returns:
        ``[(payday, amount), ...]`` in period order, one entry per period that
        contributes a non-zero amount.
    """
    events: "list[tuple[date, Decimal]]" = []
    ytd = _ZERO
    prev_year: int | None = None
    for period in periods:
        period_year = period.start_date.year
        if prev_year is not None and period_year != prev_year:
            ytd = _ZERO
        prev_year = period_year

        recorded = plan.recorded_by_period.get(period.period_id, _ZERO)
        ytd += recorded
        if reconciled_through.covers(period.start_date):
            continue

        employee = growth_engine.cap_contribution_at_limit(
            plan.feed.employee_at(period), plan.annual_limit, ytd,
        )
        employer = growth_engine.calculate_employer_contribution(
            plan.employer_params, recorded + employee,
            plan.feed.gross_at(period),
        )
        ytd += employee
        amount = employee + employer
        if amount != _ZERO:
            events.append((period.start_date, amount))
    return events
