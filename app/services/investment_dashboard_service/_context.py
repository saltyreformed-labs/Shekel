"""Investment dashboard -- the shared per-account projection feed.

The loaders and the one bundle (:class:`_ProjectionContext`) every surface of
this package reads: the dashboard's cards, its growth chart, and the balance
hero cell.  Collapsing the duplicated salary-profile / deduction /
contribution / projection-inputs loading into one shared feed is what Commit 28
(S6-01 / MED-01) extracted from the route bodies, and it is what keeps the two
public entries from loading the same account twice.

Boundary discipline (``CLAUDE.md``): no Flask symbol, all money is
:class:`~decimal.Decimal`.
"""

from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal

from app.extensions import db
from app.models.account import Account
from app.models.investment_params import InvestmentParams
from app.models.paycheck_line import PaycheckLine
from app.models.salary_profile import SalaryProfile
from app.models.user import UserSettings
from app.services import (
    balance_at,
    cash_ledger,
)
from app.services.balance_at import BalanceContext
from app.services.investment_projection import (
    AccountPayrollFeed,
    InvestmentInputs,
    PricedContribution,
)
from app.services.pay_calendar import DerivedPeriod
from app.services.projection_inputs import (
    build_investment_projection_inputs,
    load_active_deductions_for_account,
    load_payroll_feeds,
)
from app.services.recorded_contributions import (
    load_transfer_contributions_for_account,
)

@dataclass(frozen=True)
class _ProjectionContext:  # pylint: disable=too-many-instance-attributes
    """Every per-account input the dashboard + growth-chart both consume.

    Pylint: ``too-many-instance-attributes`` (13/7) -- a cohesive load-once
    *feed*, not a god-object: every field is a per-request projection input
    resolved once by :func:`_load_projection_context` and fanned out to
    different consumers (``contributions`` -> the growth projection;
    ``deductions`` / ``active_profile`` -> the contribution prompt;
    ``balance_ctx`` / ``current_period`` -> the history chart and anchor
    caption, so they read the SAME inputs the headline resolved against).
    Bundling them removes the parallel-load duplication the dashboard and chart
    fragment each carried inline (S6-01).  The annual contribution limit is
    reachable two ways (``params.annual_contribution_limit`` /
    ``inputs.annual_contribution_limit``, copied in
    ``calculate_investment_inputs``); read it from one place.

    **The owner's period SET is deliberately NOT a field** (plan step C2-f2c).
    It is :meth:`~app.services.balance_at.BalanceContext.reported_periods` off
    :attr:`balance_ctx`, which is memoized on the calendar
    (:meth:`~app.services.pay_calendar.PayCalendar.saved`), so a field here
    would be a memo of a memo -- the shape an adversarial review of plan step
    C2-c already refused one tier down.  ``current_period`` IS a field because
    nothing memoizes it: it is a bisect over that window at
    ``balance_ctx.as_of``, and holding one answer for the render is what stops
    two of this package's readers pairing one clock with another's calendar.

    Attributes:
        params: The account's :class:`InvestmentParams` row, or ``None``
            when the user has not configured the account.  ``None`` is a
            valid dashboard state (the projection and chart degrade to
            empty containers); the growth-chart fragment guards it out
            earlier and never reaches a context with ``params is None``.
        current_balance: The model-from-anchor END-of-current-period
            balance from the :mod:`app.services.balance_at` seam -- the
            displayed "current balance" tile, which agrees to the cent with
            the /savings net-worth tile, the year-end asset aggregate, and
            the net-worth trend (an anchor-in-past investment shows its
            modeled market value, not the flat cash-basis contribution
            total).  It is read at the CURRENT PERIOD's end, which is the
            convention every net-worth surface uses; the projection seeds from
            the same curve one day before its own window opens (see
            ``projection_start`` / ``projection_seed``).
        projection_start: The day the projection's window OPENS -- the day
            after the history line's last valued point
            (:func:`_projection_start`, ruling R-AF).
        projection_ytd: The year-to-date employee contribution the growth
            engine's annual-limit accounting must START from
            (:func:`_projection_ytd`).  It is the THROUGH-current total on this
            surface, because :attr:`projection_start` puts the current pay
            period outside the projection window.
        projection_seed: The account's MODELLED balance on the day before
            :attr:`projection_start`, so it IS the history line's last point and
            the two lines meet (:func:`_resolve_seed_balance`).  Nothing is
            filtered out of it and nothing is subtracted from it: the window
            opens strictly after its date, so the growth engine can neither
            re-grow a day it already grew nor re-apply a contribution it already
            holds (findings N-80 / N-84).  It is also the base of the chart's
            cumulative-contribution series.
        inputs: The :class:`InvestmentInputs` the growth engine needs
            (periodic contribution, employer params, annual contribution
            limit, YTD contributions).
        transfer_contributions: The account's priced, dated transfer
            contributions -- the RECORDED half of the feed.  **It was the
            assembled per-period timeline until plan step salary:R14-b**, and
            it could not stay one: a timeline's domain is the axis it will be
            projected over, and that axis is a function of the horizon the
            REQUEST names, so building it here fixed it to the saved window
            and left every period past it to the engine's
            ``periodic_contribution`` fallback -- the raise-blind scalar that
            step deleted.  :func:`._chart._run_growth_projection` assembles it
            now, where the axis is known.
        feed: The account's :class:`AccountPayrollFeed` -- what its payroll
            puts in per payday and what gross funds its employer contribution,
            both priced by the paycheck engine (plan step **salary:R14-b**).
            A field because the chart needs it directly: the growth engine's
            per-period employer basis is :meth:`AccountPayrollFeed.gross_at`,
            where it was one frozen ``employer_params["gross_biweekly"]``.
        deductions: The raw :class:`PaycheckLine` rows targeting
            this account; drives the contribution-prompt decision.
        salary_profiles: The user's active :class:`SalaryProfile` rows,
            primary first.  Two readers: the funding-job selector on the
            parameters form (**R-SAL5** -- which job's paycheck funds this
            account's employer contribution) and the deduction-path
            salary-profile link, which reads the first.
        balance_ctx: The read pass's ``BalanceContext``; the history chart and
            anchor caption read it so both agree with the headline balance, and
            its :meth:`~app.services.balance_at.BalanceContext.reported_periods`
            is this package's whole period domain.
        anchor_as_of: Display-tz date of the account's latest anchor event
            (C1 hero caption), or ``None`` when no baseline scenario exists.
        planned_retirement_date: The owner's planned retirement date, or
            ``None`` when unset.  Resolved ONCE here because two consumers read
            it -- the horizon slider's default (:func:`._cards
            ._compute_default_horizon`) and the chart's retirement marker
            (:func:`._chart._build_chart_markers`) -- and each loading it
            privately meant two ``user_settings`` queries per dashboard render
            for one value.
        current_period: The SAVED pay period covering ``balance_ctx.as_of``, or
            ``None`` when the pass's clock falls outside the owner's schedule
            -- before their first payday or past their horizon.  ``None`` is a
            real answer FOUR readers here branch on, so it is preserved rather
            than clamped: the headline falls back to a date-precise seam read,
            the history series is empty, the growth chip is hidden, and the
            suggested contribution spreads from the pass's clock instead.  The
            fourth is the one that matters most for this attribute: unlike the
            other three it changes a DOLLAR figure -- the amount the
            contribution-transfer form arrives pre-filled with.
    """

    params: InvestmentParams | None
    current_balance: Decimal
    projection_start: date
    projection_ytd: Decimal
    projection_seed: Decimal
    inputs: InvestmentInputs
    transfer_contributions: list[PricedContribution]
    feed: AccountPayrollFeed
    deductions: list[PaycheckLine]
    salary_profiles: list[SalaryProfile]
    balance_ctx: BalanceContext
    anchor_as_of: date | None
    planned_retirement_date: date | None
    current_period: DerivedPeriod | None


def _load_active_salary_profiles(user_id: int) -> list[SalaryProfile]:
    """Return the user's active salary profiles, PRIMARY first.

    Two consumers, which is why the list replaced a ``.first()`` at plan step
    **salary:R14-b**: the funding-job selector on the parameters form, which
    needs every profile the owner could name (**R-SAL5**), and the
    contribution prompt's salary-profile link, which needs one.

    Ordered ``(sort_order, name)`` -- the same ORDER
    :func:`app.services.projection_inputs.load_active_salary_profiles` uses,
    where "primary" is defined.  **Not the same FILTER**: that sibling is
    scoped to one scenario and this is not, so the funding-job selector offers
    every active profile the owner holds.  That is deliberate and matches the
    two sites the selector feeds -- ``projection_inputs._load_funding_profiles``
    reads the named profile without a scenario filter, and ``salary:R14-a``'s
    backfill resolved one the same way -- but the two functions are not
    interchangeable and a reader should not infer that they are.

    The single-profile read it replaced was an
    UNORDERED ``.first()``, so a two-job owner's prompt could link to either
    job across two renders.  That link moves no money, which is why it is
    noted rather than claimed as this step's subject; the money half of the
    same shape is what **R-SAL5** rules.

    Args:
        user_id: The owner.

    Returns:
        The active profiles, possibly empty.
    """
    return (
        db.session.query(SalaryProfile)
        .filter_by(user_id=user_id, is_active=True)
        .order_by(SalaryProfile.sort_order, SalaryProfile.name)
        .all()
    )


def _load_investment_params(account_id: int) -> InvestmentParams | None:
    """Return :class:`InvestmentParams` for *account_id* or ``None``."""
    return (
        db.session.query(InvestmentParams)
        .filter_by(account_id=account_id)
        .first()
    )


def _current_period(balance_ctx: BalanceContext) -> "DerivedPeriod | None":
    """Return the SAVED pay period covering this pass's clock, or ``None``.

    **The one place this package asks "which paycheck is it"** (plan step
    C2-f2c).  It was ``pay_period_service.get_current_period(user_id)`` at three
    sites -- the dashboard, the growth-chart fragment and the balance hero cell
    -- each of which issued its own SQL against its own ``date.today()``, so one
    render could hold two answers and neither was the one the balance seam
    beside it was reporting over.  The calendar is memoized on the pass and the
    clock is pinned on it, so both halves of the question come off the same
    object and this issues no query.

    ``period_containing`` rather than ``span_containing``: ``None`` outside the
    schedule is a real answer three readers here branch on (see
    :attr:`_ProjectionContext.current_period`), and the TOTAL search would
    hand them a projected period whose ``period_id`` is ``None`` -- which
    :func:`_resolve_current_balance` would then use to index a map keyed by
    ``budget.pay_periods.id``.  That is ledger row **P19**'s warning, taken
    here: preserve the ``None``.

    Args:
        balance_ctx: The read pass's ``BalanceContext``.

    Returns:
        The covering :class:`~app.services.pay_calendar.DerivedPeriod`, whose
        ``period_id`` is never ``None``, or ``None`` when the clock precedes
        the owner's first payday or lies past their horizon.
    """
    return balance_ctx.calendar().period_containing(balance_ctx.as_of)


def _resolve_anchor_as_of(account: Account) -> date | None:
    """Return the day the account's latest balance ASSERTION was true for (C1).

    Dates the hero's "anchored <date>" caption against the dated anchor SoT
    (the latest :class:`AccountAnchorHistory` row via
    :func:`~app.services.cash_ledger.resolve_anchor`, the same accessor
    the cockpit "as of" uses), NOT the anchor period's ``start_date`` and not
    the recording instant.

    The ``return None`` for a user with no baseline went at plan step X-v2
    (ruling R-BW): a missing baseline raises and one application-level handler
    answers, so a hidden caption is no longer this function's way of saying
    "the app cannot compute anything for you".  **It takes no
    ``BalanceContext``** since plan step X-f1c3a: the resolver reads a stored
    fact about the account and never scoped anything by scenario.
    """
    anchor = cash_ledger.resolve_anchor(account)
    # The day the balance was TRUE, not the day it was typed (ruling R-DH,
    # plan step 2).  This caption sits beside a "growth since" figure whose
    # accrual window ``balance_at._asset_fold`` opens on exactly this day, so
    # reading ``created_at`` would put the caption and the figure on different
    # days for any back-dated opening.
    return anchor.observed_on


def _resolve_current_balance(
    account: Account,
    balance_ctx: BalanceContext,
    current_period,
) -> Decimal:
    """Return the model-from-anchor "current balance" headline for *account*.

    The displayed tile, read from the :mod:`app.services.balance_at` seam's
    :func:`~app.services.balance_at.balance_map` at the current period so it
    agrees to the cent with /savings and the net-worth trend (an investment
    anchored in the past shows its modeled market value, not the flat cash
    basis).  The projection seeds from the SAME curve, read one day before its
    own window opens (:func:`_resolve_seed_balance`) -- it used to seed from a
    flat cash basis, which discarded every cent earned since the last assertion
    (finding N-80).

    **Every fallback to a stored balance is now GONE**, in two passes.  Plan
    step X-v2 (ruling R-CA) deleted the no-baseline arm: it presented the raw
    ``current_anchor_balance`` CACHE COLUMN as this account's *current balance*
    -- a figure the app cannot know in that state, which is finding N-103's
    complaint one screen over and the same class as the ``$0.00`` net-worth hero
    deleted in the same pass.  Plan step X-f1c3a (ruling R-EM) deleted the other
    two: the no-current-period arm now reads the seam at ``ctx.as_of`` (the seam
    takes a DATE and never needed a period to answer one), and the ``balances is
    None`` arm went with the column that made it reachable (finding N-73).  The
    map is TOTAL over the pass's reported periods, so the current period's
    column is INDEXED rather than defaulted -- ruling R-CA's own argument.
    **It stopped taking the period list at plan step C2-c**: the seam reads the
    owner's whole calendar off ``balance_ctx`` now, so there is no set for a
    caller to get wrong.  **The period it DOES take became a
    :class:`~app.services.pay_calendar.DerivedPeriod` at plan step C2-f2c**,
    which is the same value the map below is keyed by: ``balance_map`` reports
    over ``balance_ctx.reported_periods()``, so indexing it with a period taken
    off that same calendar cannot miss, where the ORM row this used to take
    came from a separate query on a separate clock.

    Args:
        account: The investment account.
        balance_ctx: The read pass's ``BalanceContext``.
        current_period: The saved period covering the pass's clock
            (:func:`_current_period`), or ``None``.

    Returns:
        The headline balance -- read at the current period's END, or at the
        pass's own ``as_of`` when no saved period covers it.
    """
    if current_period is None:
        return balance_at.balance_at(account, balance_ctx, balance_ctx.as_of)
    return balance_at.balance_map(account, balance_ctx)[current_period.period_id]


def _projection_start(balance_ctx: BalanceContext) -> date:
    """Return the day the projection's window opens (ruling R-AF).

    The day AFTER the history line's last valued point.
    ``_chart._build_history_series`` plots one point per real pay period
    through the current one, each valued at its ``end_date``, so a projection
    that opens the next day CONTINUES that line -- and MEETS it, because
    :func:`_resolve_seed_balance` reads the seed on that same last day.

    It used to be ``date.today()``, 10-13 days short of the history line's last
    point, so the chart carried a step at the Today marker whose size and sign
    nobody had chosen: measured -$301.96 on the real Empower 401(k) and
    +$105.26 on the Roth IRA.  Verified on both real databases, opening here
    makes the first projected step ($105.66) indistinguishable from the second
    ($106.07).  A biweekly axis from here also lands on the user's REAL
    pay-period boundaries, so the engine's dated contribution lookup matches its
    records over the near horizon instead of falling back to a flat average
    (the near half of finding N-79).

    **It reads the CALENDAR rather than the current period's stored
    ``end_date``** (plan step C2-e), and that is a correctness fix rather than
    tidying.  It was ``current_period.end_date + 1``, falling back to
    ``date.today()`` when no saved period covered today -- and once the axis
    became the owner's real paychecks, that fallback put the window's first
    period up to a CADENCE before the day the seed is valued on, so the engine
    re-grew days ``balance_at`` had already grown.  Measured by an adversarial
    code review on a lapsed schedule: **$57.24** of phantom growth at the head
    of a $102,686.18 balance, compounded over the whole slider horizon.
    :meth:`~app.services.pay_calendar.PayCalendar.span_containing` is TOTAL past
    the schedule, so the projected period covering the clock answers where the
    saved set has run out, and the window opens the day after IT ends -- which
    is where the axis opens, by construction rather than by two derivations
    agreeing.

    Args:
        balance_ctx: The read pass's :class:`BalanceContext` -- its ``as_of`` is
            the clock and its memoized calendar the paydays, so this issues no
            query.

    Returns:
        The day after the span covering ``as_of`` ends; the owner's first payday
        when ``as_of`` precedes their whole schedule (nothing is projected
        backwards, so that is the earliest day a projection can open); and
        ``as_of`` itself for an owner with no paydays at all, whose axis is
        empty and whose chart is the empty one.
    """
    calendar = balance_ctx.calendar()
    covering = calendar.span_containing(balance_ctx.as_of)
    if covering is not None:
        return covering.end_date + timedelta(days=1)
    opening = calendar.opening_bound()
    return opening if opening is not None else balance_ctx.as_of


def _resolve_seed_balance(
    account: Account,
    balance_ctx: BalanceContext,
    projection_start: date,
) -> Decimal:
    """Return the balance the forward growth projection seeds from.

    The account's MODELLED balance on the day before *projection_start*,
    read through the seam's date-precise scalar -- the same number the history
    line's last point renders (rulings R-AB / R-AE).

    Nothing is filtered out of it and nothing is subtracted from it: the window
    opens strictly after its date, so the engine can neither re-grow a day it
    already grew nor re-apply a contribution it already holds.  Both
    compensators this function used to carry corrected an overlap that existed
    only because the seed was read at the current period's END while the window
    opened at TODAY -- and the modelled-growth filter, had it survived the date
    change, would have started the projection line BELOW the history line by
    every cent earned since the last balance assertion (findings N-80 / N-84).

    **It TAKES the window's opening day rather than deriving it a second
    time** (plan step C2-f2c).  It called :func:`_projection_start` itself while
    :func:`_load_projection_context` called it again two lines later for the
    field the CHART reads, so the seed's date and the axis's opening day were
    two evaluations that had to agree -- which is the shape this function's own
    third paragraph records costing 10-13 days once already.  They are one
    value now, resolved once and threaded, so the identity is structural
    instead of arithmetic.

    Args:
        account: The investment account.
        balance_ctx: The read pass's ``BalanceContext``.
        projection_start: The day the projection window opens
            (:func:`_projection_start`).  The seed is valued the day BEFORE it.

    Returns:
        The seed balance, always from the seam.  It used to fall back to
        :attr:`Account.current_anchor_balance` for an account with no anchor
        period -- a state the schema forbade and the column no longer exists to
        express (finding N-73, plan step X-f1c3a).  The no-baseline arm of that
        fallback went one step earlier at X-v2 (ruling R-CA), for the reason
        :func:`_resolve_current_balance` above carries: it seeded a projection
        CHART from a cache column, so the whole forward line was drawn from a
        figure the app could not know.
    """
    return balance_at.balance_at(
        account, balance_ctx, projection_start - timedelta(days=1),
    )


def _projection_ytd(inputs: InvestmentInputs) -> Decimal:
    """Return the YTD contribution the growth engine's limit walk starts from.

    The annual limit is consumed by contributions the engine does NOT project
    plus the ones it does, so the seed must hold exactly the periods outside the
    window: ``ytd_contributions_seed`` is the total STRICTLY BEFORE the current
    period, ``ytd_contributions`` the total THROUGH it, and which is right
    depends on whether the window contains the current period.

    **On this surface it does not** (ruling R-AF), and that is a change.  The
    axis used to open at ``date.today()``, so its first fabricated period stood
    in for the rest of the current period and the engine applied that period's
    contribution itself -- which is precisely why the seed excluded it
    (deep-quality-hunt #10).  The axis now opens the day AFTER the current
    period ends, so the engine never applies it, and seeding the strictly-before
    total leaves the annual limit one period's contribution too roomy: on a
    $23,500 limit with $1,000 a period and today in the year's 15th period, the
    engine would price $9,500 of remaining room where $8,500 is left, project an
    extra contribution inside the calendar year, and compound it for the whole
    horizon.  It would also disagree with the limit CARD on the same page, which
    has always read the through-current total.

    Derived HERE, beside the window it depends on, rather than chosen at each
    ``project_balance`` call: two call sites picking between two YTD fields is
    the argument-a-caller-can-get-wrong shape the balance plan's Section 8 rules
    a defect rather than a contract.  ``retirement_projection`` keeps the
    strictly-before seed and is right to: its axis opens at or inside the
    period covering the clock, so its engine does apply that period.

    **The BRANCH went at plan step C2-e, because it could no longer answer
    anything.**  It read ``projection_start <= current_period.end_date`` -- the
    STORED column -- to ask whether the window contained the current period,
    and :func:`_projection_start` now derives the window's opening day from the
    calendar as the day after the span covering the clock ENDS, so the answer is
    structurally no.  Its other arm, "there is no current period", was a no-op:
    ``investment_projection`` returns ``ZERO`` for BOTH totals in that state, so
    the two branches returned the same figure.  A branch that cannot change the
    answer is one ``CLAUDE.md`` rule 1 forbids shipping.

    Args:
        inputs: The account's :class:`InvestmentInputs`.

    Returns:
        The ``Decimal`` YTD to seed the engine's limit accounting with.
    """
    return inputs.ytd_contributions


def _load_projection_context(
    user_id: int,
    account: Account,
    params: InvestmentParams | None,
) -> _ProjectionContext:
    """Load every per-account input the dashboard + chart fragment share.

    Centralises the projection feed both surfaces need: the canonical
    current balance, the salary-profile-derived projection inputs, the
    deductions targeting this account, the transfer contribution
    stream, and the per-period contribution timeline.  Both the
    entries-aware balance resolution and the timeline build previously
    sat near-verbatim in ``compute_dashboard_data`` and
    ``compute_growth_chart_data`` (the S6-01 duplication this collapses);
    bundling the result in :class:`_ProjectionContext` keeps the two
    public entry points thin.

    *params* is supplied by the caller (loaded once for its own guard)
    rather than re-queried here, so neither surface issues a second
    :class:`InvestmentParams` lookup.

    **The owner's PERIODS stopped being arguments at plan step C2-f2c.**  Both
    public entries used to resolve them with their own
    ``pay_period_service.get_all_periods`` / ``get_current_period`` pair and
    hand them down -- two queries per render on a clock of their own, beside a
    read pass that had already derived the same calendar for the balance seam.
    They come off ``balance_ctx`` now (:meth:`~app.services.balance_at
    .BalanceContext.reported_periods` and :func:`_current_period`), so there is
    one calendar and one clock per render by construction and neither entry can
    fill an argument wrongly.

    Args:
        user_id: ID of the authenticated user.
        account: The pre-ownership-checked account instance.
        params: The account's :class:`InvestmentParams`, or ``None``.

    Returns:
        A :class:`_ProjectionContext` carrying every per-request value the
        projection primitives and card builders consume.
    """
    balance_ctx = BalanceContext.build(user_id)
    periods = balance_ctx.reported_periods()
    current_period = _current_period(balance_ctx)
    projection_start = _projection_start(balance_ctx)
    salary_profiles = _load_active_salary_profiles(user_id)
    deductions = load_active_deductions_for_account(user_id, account.id)
    # **The paycheck ENGINE prices this account's payroll feed** (plan step
    # salary:R14-b, ruling R-SAL2).  Two inputs stood here instead: one
    # ``income_service.get_current_gross_biweekly`` scalar -- the raise-aware
    # gross of TODAY's paycheck, applied to every period of a 40-year
    # projection -- and the deduction rows flattened by an ``adapt_deductions``
    # adapter this package's consumer then re-priced off the profile's stored
    # annual salary, raise-blind (finding D45).  Both are one figure standing
    # in for a series.  The feed answers per payday, so nothing here computes
    # a dollar and the ``if deductions`` guard that fed the adapter has
    # nothing left to guard.
    # The PASS's paycheck pricer, which this render's balance-seam reads fill
    # and read too.  Without it this page ran the engine over the owner's
    # whole saved window twice -- once here and once through the seam --
    # which is the shape an adversarial review measured at ``salary:R14-b``
    # and which this argument exists to close.
    feed = load_payroll_feeds(
        balance_ctx.paychecks(), [account.id],
        {account.id: params} if params is not None else {},
    ).get(account.id, AccountPayrollFeed.absent())
    acct_contributions = load_transfer_contributions_for_account(
        balance_ctx.amounts(),
        account.id, [period.period_id for period in periods],
    ).records
    # Seed for the forward projection: the account's MODELLED balance on the
    # day before the window opens, which is the history line's own last point
    # (rulings R-AB / R-AE).  Nothing is filtered out of it and nothing is
    # subtracted from it -- the window opens strictly after the seed's date, so
    # there is no overlap for a compensator to correct.
    projection_seed = _resolve_seed_balance(
        account, balance_ctx, projection_start,
    )
    inputs = build_investment_projection_inputs(
        params, feed, acct_contributions, current_period,
    )
    return _ProjectionContext(
        params=params,
        # The headline tile shows the model-from-anchor balance at the current
        # period's END (so it agrees with /savings and the net-worth trend);
        # the projection seeds from the same curve one day before its own
        # window opens, so the two are read at different DATES rather than off
        # different bases.  Inlined to stay under the locals limit.
        current_balance=_resolve_current_balance(
            account, balance_ctx, current_period,
        ),
        projection_start=projection_start,
        projection_ytd=_projection_ytd(inputs),
        projection_seed=projection_seed,
        inputs=inputs,
        transfer_contributions=acct_contributions,
        feed=feed,
        deductions=deductions,
        salary_profiles=salary_profiles,
        balance_ctx=balance_ctx,
        # Two per-user lookups, inlined to stay under the locals limit.
        anchor_as_of=_resolve_anchor_as_of(account),
        planned_retirement_date=_load_planned_retirement_date(user_id),
        current_period=current_period,
    )


def _load_planned_retirement_date(user_id: int) -> date | None:
    """Return the user's planned retirement date, or ``None`` if unset (C2).

    **One caller since plan step C2-f2c**: :func:`_load_projection_context`,
    which puts the answer on the shared feed.  It was called directly by both
    :func:`._cards._compute_default_horizon` and
    :func:`._chart._build_chart_markers`, so rendering the dashboard queried
    ``user_settings`` twice for one value -- the redundant-producer shape a
    read pass exists to remove.
    """
    settings = (
        db.session.query(UserSettings)
        .filter_by(user_id=user_id)
        .first()
    )
    return settings.planned_retirement_date if settings else None
