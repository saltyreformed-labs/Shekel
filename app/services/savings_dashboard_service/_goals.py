"""
Shekel Budget App -- Savings Dashboard: savings-goal progress.

Computes per-goal progress, the committed monthly contribution from
recurring transfer templates, and the projected completion trajectory.

**A goal on a DEBT is a milestone to get under** (plan step
credit_card:CC-5-5d, rulings R-CC69..R-CC73, R-CC88, R-CC91, R-CC93): its
progress runs from what the debt owed when the goal was set to its target
(R-CC70); that start is RECORDED at creation for a card or other non-loan debt
(R-CC91) and re-read from the books for a configured loan (R-CC71); and its
projected date is the day the debt first owes at most the target (R-CC73).
Every one of those figures is what the debt's /savings TILE shows on that day
(:func:`._tile.tile_balance_on`, ruling R-CC88), so a goal and the tile above
it cannot disagree.  A goal is a debt goal by its account's category, asked of
the account; nothing on the goal row says so.

No Flask imports.
"""

from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from app import ref_cache
from app.enums import GoalModeEnum
from app.extensions import db
from app.models.savings_goal import SavingsGoal
from app.models.transfer_template import TransferTemplate
from app.services import balance_at, obligations_aggregator, savings_goal_service
from app.services.account_category import is_liability_account
from app.services.balance_at import BalanceContext
from app.services.cash_ledger import transfer_template_pricing_load_options
from app.services.liability_sign import owed, shown_figure
from app.services.pay_calendar import PayCalendar, PeriodWindow
from app.services.savings_dashboard_service._tile import (
    first_tile_day_owing_at_most,
    is_configured_loan,
    tile_balance_on,
)
from app.services.savings_dashboard_service._metrics import CurrentPay
from app.services.savings_dashboard_service._types import AccountProjection
from app.services.savings_goal_service import GoalTargetSpec, GoalTrajectory
from app.utils.dates import to_display_date
from app.utils.money import percent_complete

#: A debt goal the debt has already reached reads complete.  Stated rather than
#: left to :func:`~app.utils.money.percent_complete`'s ceiling, which a met goal
#: does not always reach: a start the books have since corrected to at or
#: below the target (ruling R-CC71 re-reads it) leaves no span to divide.
_DEBT_GOAL_MET = Decimal("100.00")


@dataclass(frozen=True)
class _GoalInputs:
    """The read-pass facts every goal datum in one build shares.

    A parameter object introduced at plan step R7a-2a, and forced by that
    step's third field: :func:`_compute_goal_progress` and
    :func:`_build_goal_datum` each took five positional arguments, and the
    owner's pay cadence -- which the contribution floor and the income-relative
    target both need -- would have been a sixth on both.  A raised ``max-args``
    is not the project's answer; grouping is, and these three ARE one thing:
    the per-owner facts resolved once for the whole build that no individual
    goal changes.

    Loaded rows and the read pass rather than the whole
    :class:`~.._types._DashboardCoreData`, so a helper here never reads an
    account list it was not built for.

    **It carries the READ PASS since plan step R7d-e, where it carried three
    of the pass's derivations as scalars.**  The committed-contribution filter
    now asks what stops a definition through the composed door -- the rule's
    own bound AND its destination's derived stop -- and the door needs the
    pass to fold a loan.  Keeping the scalars beside the pass would have made
    ``inputs.as_of`` and ``inputs.balance_ctx.as_of`` two spellings of one day
    (``CLAUDE.md`` rule 14), and the same for the calendar and the reported
    window, so all three are DERIVED from the pass below instead -- each off a
    memo the pass already holds, so a property costs nothing -- and every
    reader keeps the name it had.

    Attributes:
        current_pay: What the owner's active profiles pay this period and the
            rhythm it was priced at (:class:`~._metrics.CurrentPay`), or
            ``None`` when they have no salary configured -- which
            :attr:`net_biweekly_pay` reads as ``$0.00`` and
            :attr:`GoalProgress.has_salary_data` reports.  **The whole value
            since plan step salary:X-av-2** (ruling **R-SAL70**), where it
            carried the net alone and the calendar's LATEST rhythm turned it
            into a month: a "months of salary" goal converts at the rhythm the
            net was priced at, and only this value holds that.
        balance_ctx: The render's read pass.  Its calendar is the WHOLE
            schedule ``obligations_aggregator`` needs to tell whether a
            contribution template bounded "after N occurrences" has spent its
            count (plan step R7b-3); its reported
            window is what the periods-until-target count walks, the same
            window the account balances beside it were reported over
            (pay-calendar plan step C2-f2d-3); and its ``as_of`` is the
            build's ONE clock (that step, ledger row **P55**): the
            committed-contribution filter and the periods-until-target count
            both resolve against a day, and reading it twice let one goal
            card answer from two.
    """

    current_pay: CurrentPay | None
    balance_ctx: BalanceContext

    @property
    def net_biweekly_pay(self) -> Decimal:
        """The net pay for one paycheck, ``$0.00`` when there is no salary data."""
        if self.current_pay is None:
            return Decimal("0.00")
        return self.current_pay.net_biweekly

    @property
    def all_periods(self) -> PeriodWindow:
        """The owner's saved schedule, off the pass's reported window."""
        return self.balance_ctx.reported_periods()

    @property
    def calendar(self) -> PayCalendar:
        """The owner's whole pay-period schedule, off the pass's memo."""
        return self.balance_ctx.calendar()

    @property
    def as_of(self) -> date:
        """The read pass's day, the build's one clock."""
        return self.balance_ctx.as_of


@dataclass(frozen=True)
class GoalProgress:  # pylint: disable=too-many-instance-attributes
    """One savings goal's progress: where it stands and when it lands.

    The per-goal record the ``/savings`` goal cards and the budget dashboard's
    savings track both reduce over -- two templates, through two packages.  A
    frozen value object since plan step X-w4 (ruling R-CI); it was an ELEVEN-key
    untyped dict, the largest record container on this read path, and its
    contract was stated only in two ``Returns:`` blocks that had to be kept in
    step with each other by hand.

    **One of those eleven keys was read by NOTHING, and it did not become a
    field** -- eleven keys in, ten fields out.  (This said "a TWELFTH key" until
    plan step X-w6's adversarial review recounted the dict it describes, which
    is the "a count in a docstring is a claim" class inside the paragraph that
    deletes a field for being one.)
    ``goal_mode_id`` was a straight copy of ``goal.goal_mode_id`` -- one fact
    under two keys, on a record that already carries the goal -- and an AST
    census over ``app/`` and ``tests/`` found ZERO readers of the copy: every
    ``goal_mode_id`` site in the tree is either the ref-cache accessor
    (``ref_cache.goal_mode_id``), the ORM column, the goal FORM's own field, or
    the create/update schema's payload.  That is finding N-100's defect
    (a published key with no consumer) in a container being typed, so it went
    with the dict rather than being carried into the record.

    Pylint: ``too-many-instance-attributes`` (11/7) -- suppressed because this
    is a cohesive per-goal display record read flat by two templates, not an
    object accumulating state.  Every field has a named reader (each was
    counted before the record was written), and there is no cohesive sub-group
    to nest: the goal's own columns are read THROUGH ``goal`` rather than
    copied, and the trajectory is already one nested value.  Grouping any of
    the rest would invent a concept to satisfy a count.  Mirrors
    :class:`~.._types.AccountProjection`, whose 8/7 carries the same rationale.

    Attributes:
        goal: The :class:`~app.models.savings_goal.SavingsGoal` row.  Every
            field the cards read off the goal itself -- its name, account,
            target date, manual per-period contribution, and its mode -- is
            read HERE rather than copied onto this record.
        current_balance: The backing account's balance today, taken from that
            account's :class:`~.._types.AccountProjection` so the goal card and
            the account tile cannot report different balances.  HELD, like the
            projection's: a debt's is negative when it owes, and the cards
            render :attr:`shown_balance`.  A debt goal whose account is ARCHIVED
            has no projection and reads the tile's rule directly
            (:func:`.._tile.tile_balance_on`), so it never reads ``$0.00`` owed
            and "met" for a debt that still owes (plan step CC-5-5d).
        progress_pct: Completion percent through the canonical
            :func:`app.utils.money.percent_complete` contract (ROUND_HALF_UP,
            clamped ``[0, 100]``), so this card, the budget-dashboard card and
            the companion entry view report one number for one goal.  A DEBT
            goal's runs from :attr:`start_owed` down to the target (ruling
            R-CC70): what has been paid down since the goal was set over what
            the goal set out to pay down.
        remaining_periods: Pay periods from today to the target date, or
            ``None`` when the goal has no target date.
        required_contribution: The per-period contribution needed to land on
            the target date, or ``None`` when there is no actionable target or
            the date has passed (the card renders "Past target date" for the
            second).  ALWAYS ``None`` for a debt goal, which has no per-period
            figure (ruling R-CC73); the debt card never renders that line.
        resolved_target: The dollar target -- ``target_amount`` for a FIXED
            goal, the income-relative computation for the other mode.
        income_descriptor: The income-relative caption ("3 months of salary"),
            or ``None`` for a fixed goal.  Presentation microcopy composed in
            the service because templates display and never compute.
        has_salary_data: Whether the engine produced a positive net biweekly
            pay.  STORED rather than derived: the pay figure itself is not
            carried here, and an income-relative goal with no salary profile is
            exactly the state the card warns about.
        trajectory: The goal's
            :class:`~app.services.savings_goal_service.GoalTrajectory` -- when
            it lands at the current rate, and how that reads against its target
            date.  **NEVER absent**: its producer has three returns and every
            one fills all four fields.  Plan step X-w4 typed this as
            ``dict | None`` and the goal card guarded it with a truthiness
            test; both were unreachable, and plan step X-aa deleted them with
            the dict (ruling R-CO).  A nullable that cannot be null is ruling
            R-CA's defect; a guard that cannot be false is not a guard.
        monthly_contribution: The committed monthly inflow discovered from the
            recurring transfer templates targeting the goal's account, through
            the one canonical obligations aggregator.  ``None`` for a DEBT goal:
            its date reads the debt's own plan and its card renders no
            contribution (ruling R-CC73), so the figure would have no reader.
        start_owed: For a DEBT goal, what the debt owed when the goal was set,
            as its tile showed it.  A CARD or other non-loan debt's is the
            figure the goal door RECORDED at creation (ruling R-CC91: its tile
            reads the pay period's END, so a later re-read would take in the
            rest of that period); a CONFIGURED loan's is re-read from the books
            on every build (ruling R-CC71), so a back-dated correction moves it.
            ``None`` for a savings goal, which has no start: its progress is
            its balance over its target.
    """

    goal: SavingsGoal
    current_balance: Decimal
    progress_pct: Decimal
    remaining_periods: int | None
    required_contribution: Decimal | None
    resolved_target: Decimal
    income_descriptor: str | None
    has_salary_data: bool
    trajectory: GoalTrajectory
    monthly_contribution: Decimal | None
    start_owed: Decimal | None = None

    @property
    def is_debt(self) -> bool:
        """Whether this is a goal on a DEBT -- its account's category is Liability.

        DERIVED from the account through the canonical id-based classifier
        (:func:`app.services.account_category.is_liability_account`), the rule
        every liability surface asks, so the goal's kind cannot disagree with
        its account's.

        Returns:
            ``True`` for a debt goal.
        """
        return is_liability_account(self.goal.account)

    @property
    def shown_balance(self) -> Decimal:
        """The figure the goal cards SHOW: what a debt owes, else the balance.

        :func:`app.services.liability_sign.shown_figure`, the crossing the
        account's own tile speaks (:attr:`~.._types.AccountProjection.shown_balance`),
        so a debt goal reads ``$14,745.51`` owed where :attr:`current_balance`
        holds ``-14,745.51``.

        Returns:
            What is owed for a debt goal; the balance for a savings goal.
        """
        return shown_figure(self.goal.account.account_type, self.current_balance)


def _load_active_goals(user_id):
    """Load the user's active savings goals.

    The single active-goal loader shared by both savings-dashboard entry
    points: the narrow ``compute_goal_progress`` (which also needs the
    goals up front to restrict the projected accounts to those backing a
    goal) and the full ``compute_dashboard_data``.  Centralizing the
    query here means :func:`_compute_goal_progress` no longer re-runs the
    identical ``is_active`` query its caller already issued.

    Args:
        user_id: Integer ID of the current user.

    Returns:
        List of active :class:`SavingsGoal` instances.
    """
    return (
        db.session.query(SavingsGoal)
        .filter_by(user_id=user_id, is_active=True)
        .all()
    )


def _load_goal_templates(user_id, goals):
    """Batch-load active recurring transfer templates targeting goal accounts.

    Avoids an N+1 query in the per-goal loop.  The aggregator that
    consumes the result (``obligations_aggregator.committed_monthly``)
    handles per-pattern normalization to monthly equivalents and the
    shared skip-non-repeating / skip-expired filter, and prices each
    template on the pass's day (``obligations_aggregator.todays_price``,
    ruling plan#2693), so the query loads what pricing reads with the
    templates: without it the page's statements grew with every template
    (plan#2733).

    Args:
        user_id: Integer ID of the current user.
        goals: List of active SavingsGoal instances.

    Returns:
        Dict mapping account_id to a list of TransferTemplate targeting
        that account.
    """
    goal_account_ids = [goal.account_id for goal in goals]
    if goal_account_ids:
        to_account_templates = (
            db.session.query(TransferTemplate)
            .options(*transfer_template_pricing_load_options())
            .filter(
                TransferTemplate.user_id == user_id,
                TransferTemplate.to_account_id.in_(goal_account_ids),
                TransferTemplate.is_active.is_(True),
            )
            .all()
        )
    else:
        to_account_templates = []

    templates_by_account = {}
    for tmpl in to_account_templates:
        templates_by_account.setdefault(tmpl.to_account_id, []).append(tmpl)
    return templates_by_account


@dataclass(frozen=True)
class _GoalBasics:
    """The four figures a goal's record carries whatever kind of goal it is.

    Shared by the savings and the debt builder (plan step credit_card:CC-5-5d)
    so each is written once: the goal door keeps a debt goal Fixed, but a build
    must still answer for every row the table can hold, and one resolver
    answers both.

    Attributes:
        resolved_target: The dollar target -- ``target_amount`` for a FIXED
            goal, the income-relative computation for the other mode.
        income_descriptor: The income-relative caption, ``None`` for Fixed.
        remaining_periods: Paydays from the pass's day to the target date, or
            ``None`` without a target date.
        has_salary_data: Whether the engine produced a positive net pay.
    """

    resolved_target: Decimal
    income_descriptor: str | None
    remaining_periods: int | None
    has_salary_data: bool


def _goal_basics(goal, inputs: _GoalInputs) -> _GoalBasics:
    """Resolve the figures every goal's record carries (:class:`_GoalBasics`).

    For income-relative goals the resolved target is calculated from the
    user's net pay, their pay cadence and the goal's multiplier/unit; for fixed
    goals the stored target_amount is used directly.

    Args:
        goal: The SavingsGoal instance.
        inputs: The build's shared per-owner facts (:class:`_GoalInputs`).

    Returns:
        The goal's :class:`_GoalBasics`.
    """
    resolved_target = savings_goal_service.resolve_goal_target(
        GoalTargetSpec(
            goal_mode_id=goal.goal_mode_id,
            target_amount=goal.target_amount,
            income_unit_id=goal.income_unit_id,
            income_multiplier=goal.income_multiplier,
        ),
        inputs.net_biweekly_pay,
        # The rhythm the net was priced at (ruling R-SAL70).  With no current
        # pay the net is $0.00, which is $0.00 a month at any count, so the
        # calendar's own cadence only fills the argument there.
        (
            inputs.current_pay.cadence if inputs.current_pay is not None
            else inputs.calendar.cadence
        ),
    )
    if goal.goal_mode_id == ref_cache.goal_mode_id(GoalModeEnum.FIXED):
        income_descriptor = None
    else:
        unit_name = (
            goal.income_unit.name.lower()
            if goal.income_unit else "units"
        )
        income_descriptor = f"{goal.income_multiplier} {unit_name} of salary"
    return _GoalBasics(
        resolved_target=resolved_target,
        income_descriptor=income_descriptor,
        remaining_periods=savings_goal_service.count_periods_until(
            goal.target_date, inputs.all_periods, inputs.as_of,
        ),
        has_salary_data=inputs.net_biweekly_pay > Decimal("0.00"),
    )


def _build_goal_datum(
    goal, acct_balance, monthly_contribution, inputs: _GoalInputs,
) -> GoalProgress:
    """Build the per-goal progress record for one SAVINGS goal.

    The target, caption, period count and salary flag come from
    :func:`_goal_basics`.  Computes progress percent, required contribution
    and the projected trajectory.  A goal on a debt is
    :func:`_build_debt_goal_datum`'s.

    Args:
        goal: The SavingsGoal instance.
        acct_balance: Current balance of the goal's backing account.
        monthly_contribution: Committed monthly contribution into the
            account, from the canonical obligations aggregator.
        inputs: The build's shared per-owner facts (:class:`_GoalInputs`) --
            the pay periods, the net pay for one paycheck, and the pay
            cadence.

    Returns:
        The goal's :class:`GoalProgress` (an eleven-key dict until plan step
        X-w4, ruling R-CI).
    """
    basics = _goal_basics(goal, inputs)
    resolved_target = basics.resolved_target

    required = savings_goal_service.calculate_required_contribution(
        acct_balance, resolved_target, basics.remaining_periods,
    ) if resolved_target and resolved_target > 0 else None

    # Progress percent via the canonical money.percent_complete contract
    # (ROUND_HALF_UP, clamped [0, 100], Decimal) so this savings card, the
    # budget-dashboard savings-goal card (dashboard_service), and the companion
    # entry view (entry_service) all report the same number for the same goal,
    # and a negative projected balance renders 0%, not a negative-width bar
    # (deep-quality-hunt #20).
    progress_pct = Decimal("0")
    if resolved_target and resolved_target > Decimal("0.00"):
        progress_pct = percent_complete(acct_balance, resolved_target)

    # Trajectory: projected completion date and pace indicator.
    trajectory = savings_goal_service.calculate_trajectory(
        current_balance=acct_balance,
        target_amount=resolved_target,
        monthly_contribution=monthly_contribution,
        target_date=goal.target_date,
    )

    # ``goal_mode_id`` is NOT carried (plan step X-w4).  It was a copy of
    # ``goal.goal_mode_id`` on a record that carries the goal, and an AST
    # census found the copy had zero readers anywhere -- finding N-100's
    # published-key-with-no-consumer, inside the container this step typed.
    return GoalProgress(
        goal=goal,
        current_balance=acct_balance,
        progress_pct=progress_pct,
        remaining_periods=basics.remaining_periods,
        required_contribution=required,
        resolved_target=resolved_target,
        income_descriptor=basics.income_descriptor,
        has_salary_data=basics.has_salary_data,
        trajectory=trajectory,
        monthly_contribution=monthly_contribution,
    )


def _build_debt_goal_datum(
    goal, projection: AccountProjection | None, inputs: _GoalInputs,
) -> GoalProgress:
    """Build the per-goal progress record for one goal on a DEBT.

    Every figure is what the debt's tile shows (ruling R-CC88):

    * **today** -- the projection's own balance, which the tile was drawn
      from; an ARCHIVED debt has no projection, so the tile's rule is read
      for it directly and it never falls back to ``$0.00`` owed;
    * **the start** -- what the tile showed when the goal was set.  For a card
      or other non-loan debt the goal door RECORDED it at creation (ruling
      R-CC91): that tile reads the pay period's END, and a re-read with
      today's books would take in every payment or purchase recorded in the
      rest of that period.  For a CONFIGURED loan it is re-read from the books
      on the goal's creation day (ruling R-CC71): a loan's tile reads the day,
      which the books reproduce, so a back-dated correction moves it.  That
      day is ``created_at`` in the display timezone, and it is the pass's own
      "today" only because the process runs in that timezone (``TZ`` is pinned
      to America/New_York in both compose files; CI runs UTC, where a goal set
      within hours of midnight can read its start one day off);
    * **the projected date** -- the first day the tile shows the debt owing at
      most the target (ruling R-CC73; asked only while it owes more).

    The percentage runs from the start down to the target (ruling R-CC70),
    and a debt already at or under its target reads complete.  There is no
    per-period figure (ruling R-CC73), so there is no contribution either.

    Args:
        goal: The SavingsGoal instance, on a liability.
        projection: The debt's :class:`~.._types.AccountProjection`, or
            ``None`` when the account is archived and was not projected.
        inputs: The build's shared per-owner facts (:class:`_GoalInputs`).

    Returns:
        The goal's :class:`GoalProgress`, with :attr:`GoalProgress.start_owed`
        set.
    """
    ctx = inputs.balance_ctx
    account = goal.account
    if projection is not None:
        is_loan = projection.loan is not None
        balances = projection.balances
        held_now = projection.current_balance
    else:
        # One fold for the reads below, rather than one each.
        is_loan = is_configured_loan(account, ctx)
        balances = None if is_loan else balance_at.balance_map(account, ctx)
        held_now = tile_balance_on(
            account, ctx, ctx.as_of, is_loan=is_loan, balances=balances,
        )
    if goal.start_owed is not None:
        start_owed = goal.start_owed
    else:
        start_owed = owed(tile_balance_on(
            account, ctx, to_display_date(goal.created_at),
            is_loan=is_loan, balances=balances,
        ))
    owed_now = owed(held_now)
    basics = _goal_basics(goal, inputs)
    resolved_target = basics.resolved_target

    is_met = owed_now <= resolved_target
    crossing = None if is_met else first_tile_day_owing_at_most(
        account, ctx, resolved_target, is_loan=is_loan, balances=balances,
    )
    return GoalProgress(
        goal=goal,
        current_balance=held_now,
        progress_pct=_DEBT_GOAL_MET if is_met else percent_complete(
            start_owed - owed_now, start_owed - resolved_target,
        ),
        remaining_periods=basics.remaining_periods,
        required_contribution=None,
        resolved_target=resolved_target,
        income_descriptor=basics.income_descriptor,
        has_salary_data=basics.has_salary_data,
        trajectory=savings_goal_service.calculate_debt_trajectory(
            owed_now=owed_now,
            target_amount=resolved_target,
            crossing_date=crossing,
            target_date=goal.target_date,
            as_of=inputs.as_of,
        ),
        monthly_contribution=None,
        start_owed=start_owed,
    )


def _compute_goal_progress(
    user_id, account_data, inputs: _GoalInputs, goals,
) -> list[GoalProgress]:
    """Compute savings goal progress, contributions, and trajectory.

    For income-relative goals, the resolved target is calculated from
    the user's net pay, their pay cadence and the goal's multiplier/unit.  For
    fixed goals, the stored target_amount is used directly.

    Trajectory is computed for each SAVINGS goal by discovering the monthly
    contribution from recurring transfer templates targeting the goal's
    account, then projecting the completion date and pace.  A goal on a DEBT
    reads its debt's own plan instead (:func:`_build_debt_goal_datum`).

    Args:
        user_id: Integer ID of the current user.
        account_data: The per-account projections from
            _compute_account_projections.
        inputs: The build's shared per-owner facts (:class:`_GoalInputs`) --
            the pay periods, the net pay for one paycheck, and the pay
            cadence every monthly equivalent here is measured against.
        goals: The user's active :class:`SavingsGoal` instances, already
            loaded by the caller via :func:`_load_active_goals`.  Passed
            in rather than re-queried so the active-goal lookup runs once
            per request, not twice (both entry points already load it).

    Returns:
        One :class:`GoalProgress` per active goal, in *goals* order.
    """
    savings_goals = [
        goal for goal in goals if not is_liability_account(goal.account)
    ]
    templates_by_account = _load_goal_templates(user_id, savings_goals)
    projections = {ad.account.id: ad for ad in account_data}

    goal_data = []
    for goal in goals:
        projection = projections.get(goal.account_id)
        if is_liability_account(goal.account):
            goal_data.append(
                _build_debt_goal_datum(goal, projection, inputs),
            )
            continue

        # Monthly contribution from recurring transfers into this account.
        # Routed through the one canonical aggregator (E-24 / HIGH-05) so
        # the same skip-non-repeating / skip-expired filter applies that the
        # /obligations page applies; pre-Commit-23 this loop omitted the
        # expired-rule guard and inflated per-goal floors indefinitely.
        acct_templates = templates_by_account.get(goal.account_id, [])
        # The PASS, whole (plan step R7d-e), where it was the pass's day and
        # calendar as two scalars.  ``committed_monthly`` decides whether a
        # bounded template still commits anything AS OF the pass's day and
        # through the composed door, which needs the pass to fold a loan;
        # reading ``date.today()`` here instead put this figure on a
        # different day from the balances beside it on the same card across a
        # midnight render -- and from the emergency-fund floor, which asks the
        # same producer the same question (pay-calendar plan step C2-f2d-3,
        # ledger row **P55**).
        monthly_contribution = obligations_aggregator.committed_monthly(
            acct_templates, inputs.balance_ctx,
        )
        # A savings goal whose account is ARCHIVED has no projection and reads
        # ``$0.00`` saved -- ledger row CC-372 (owner operator), a known
        # defect this step does not fix; its debt twin is handled above.
        acct_balance = (
            projection.current_balance if projection is not None
            else Decimal("0.00")
        )
        goal_data.append(_build_goal_datum(
            goal, acct_balance, monthly_contribution, inputs,
        ))

    return goal_data
