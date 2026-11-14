"""
Shekel Budget App -- Net-Worth Kernel (shared per-account balance chain).

The single, Flask-free home for the per-account balance-map projection
chain the ``balance_at`` seam dispatches through.  Promoted out of
``year_end_summary_service._balances`` (Loop B Phase 1) when the year-end
summary and the savings cockpit computed net worth from two copies of the
same math that could drift; the year-end consumer has since been deleted
(plan step F2).

**There is no per-kind dispatch left here** (plan step X-g2b, ruling R-AD).
Every non-loan account's map is ONE event replay
(:func:`app.services.balance_at._asset_fold.asset_period_view`): an account
that models a return gets its ACCRUAL and CONTRIBUTION tiers, and an account
that models none IS its cash fold, which is the same statement rather than a
fall-through.  The ladder this module used to hold -- INVESTMENT to the growth
engine, APPRECIATING to the appreciation curve, INTEREST to the accrual layer,
everything else to the cash fold -- had four branches for one question, and
each branch answered a period rather than a date (finding N-71) and spliced
three sources by a preference order (findings N-43 / N-74).  Verified before
the ladder was deleted: on both real databases the replay reproduces the PLAIN
fall-through to the cent on every one of 60 columns, for all three real plain
accounts.

AMORTIZING loans are NOT dispatched here at all: the seam reads its own
``positions()``-based map for them (plan step C3b3), because that producer sits
above this module.  An AMORTIZING account with no ``LoanParams`` does reach
here, and models no return, so it is its cash fold -- the same degrade the
seam's scalar makes.

The cockpit's forward net-worth trend PROJECTS investment and retirement
growth forward, and that forward WHAT-IF keeps ``growth_engine`` (ruling R-U);
what moved here is the balance-at-T half.  The loan SCHEDULE bundle
(``DebtSchedule``, with ``generate_debt_schedules`` and the rows accessor
``debt_schedule_rows``) lived here for that trend's history gate until plan
step recurrence:R16-c-2, where ruling R-R112 gates every loan from its
recorded start instead; no schedule is read there now, so the three went
with their last caller.

Boundary discipline (``CLAUDE.md``: "services are isolated from Flask"): this
module imports no Flask symbol and performs no database writes.  Since plan
step X-c2b3 it issues no QUERY either -- every row it dispatches over is loaded
by the leaf or the fold below it.  All money is :class:`~decimal.Decimal`;
``float`` belongs only at a route's Chart.js serialization boundary, never
here.

Its public producers take ONE per-account bundle
(:class:`~app.services.balance_at._asset_contributions.ContributionInputs`)
rather than the three loose parameters the growth-engine dispatch needed, and
the wider seam bundle is sliced into it by :mod:`._inputs`, where that bundle
is defined -- so this module no longer duck-types a value object it must not
import (plan step X-g2b; the slice used to live here as
``account_balance_map_from_inputs``).
"""

from collections import OrderedDict
from decimal import Decimal

from app.models.account import Account

from ._asset_contributions import ContributionInputs
from ._context import BalanceContext
from . import _asset_fold


def _modelled_columns(
    account: Account,
    ctx: "BalanceContext",
    inputs: ContributionInputs,
) -> "OrderedDict[int, _asset_fold.AssetPeriodFigures]":
    """Resolve *account*'s modelled column for each period -- ONE replay.

    The single home for "replay this account's event stream and read it at
    every period end", shared by the two public readers below: the BALANCE
    map (:func:`build_account_balance_map`, which keeps
    :attr:`~app.services.balance_at._asset_fold.AssetPeriodFigures.balance`)
    and the modelled-return accessors
    (:func:`interest_projection_for_account` /
    :func:`interest_by_period_for_account`, which also keep
    :attr:`~app.services.balance_at._asset_fold.AssetPeriodFigures.accrual`).
    Folding the two into one helper is what keeps the balance a screen renders
    and the accrual figure beside it from being two walks -- the reason ruling
    R-L had to move both readers at once (finding N-47), preserved here now
    that both read the replay instead of the layered accrual.

    Args:
        account: The account to project.  Its kind is consulted only by the
            replay, to decide whether it models a return at all.
        ctx: The read pass's :class:`~app.services.balance_at.BalanceContext`
            (its scenario scopes the fold and the contribution feed; its
            ``as_of`` is ruling R-G's clamp floor, and since plan step C2-c
            its :meth:`~app.services.balance_at.BalanceContext.reported_periods`
            is the output domain).
        inputs: The account's
            :class:`~app.services.balance_at._asset_contributions.ContributionInputs`.

    Returns:
        ``OrderedDict`` period id ->
        :class:`~app.services.balance_at._asset_fold.AssetPeriodFigures`, one
        per reported period.
    """
    return _asset_fold.asset_period_view(account, ctx, inputs)


def interest_projection_for_account(
    account: Account,
    ctx: "BalanceContext",
) -> "tuple[OrderedDict[int, Decimal], dict[int, Decimal]]":
    """Return an interest account's BALANCES and its earned interest, together.

    The account-detail page renders both -- the balance chart / hero and the
    "Interest, next 12 mo" chip -- and they must be the same walk or the chip
    would explain a balance change the page does not show.  Reading them
    through one call is what makes that structural instead of a claim: the
    alternative it replaced was ``balance_map`` followed by
    :func:`interest_by_period_for_account`, each of which discarded the half
    the other wanted and each of which ran a FULL fold -- two walks, two plan
    loads and two live-override builds (the ~90 ms salary / loan recompute) for
    one render.

    **It takes no ``interest_params`` argument any more** (plan step X-g2b).
    The replay reads the account's own accrual rule through the ONE resolver
    :func:`app.services.balance_at._asset_return.modelled_return`, so the rate
    can no longer arrive from a caller that loaded a different row than the one
    the account carries -- the argument-a-caller-can-get-wrong shape the plan's
    Section 8 rules a defect rather than a contract.

    Args:
        account: The interest-bearing account.
        ctx: The read pass's :class:`~app.services.balance_at.BalanceContext`
            (its scenario scopes the fold; its ``as_of`` is the reader's NOW;
            its ``reported_periods()`` is the walk domain, and the caller
            filters to the periods it wants).

    Returns:
        ``(balances, interest_by_period)`` -- the interest-accrued end balance
        per period id and the interest earned in each.  **Never the empty pair
        as a degradation**: it answered ``(OrderedDict(), {})`` for an account
        with ``current_anchor_period_id IS NULL``, a state the schema forbade
        and the column no longer exists to express (finding N-73, plan step
        X-f1c3a).

    Raises:
        PayCalendarError: The owner's paydays cannot define a calendar, which
            since plan step C2-c is reachable from every per-period seam entry
            rather than only from the recurrence pages -- see
            :meth:`~app.services.balance_at.BalanceContext.calendar`, where the
            reporting domain is derived, for the one state that produces it and
            the step that removes it.
    """
    columns = _modelled_columns(
        account, ctx, ContributionInputs.absent(),
    )
    return (
        OrderedDict(
            (period_id, column.balance)
            for period_id, column in columns.items()
        ),
        {
            period_id: column.accrual
            for period_id, column in columns.items()
        },
    )


def interest_by_period_for_account(
    account: Account,
    ctx: "BalanceContext",
) -> dict[int, Decimal]:
    """Return period_id -> interest earned for an interest-bearing account.

    The seam accessor for a consumer that wants the interest EARNED without the
    balances: interest earned is rich projection detail, not a balance-at-T
    figure, so it is not a ``balance_at`` view -- yet it must be the SAME walk
    the balance came from, which is what keeping it beside
    :func:`interest_projection_for_account` on one shared
    :func:`_modelled_columns` guarantees.

    A None-anchor account earns no projectable interest, returned as the empty
    map so a caller's windowed sum is ``Decimal("0")``.

    **It has no production caller today** -- the account-detail page reads
    :func:`interest_projection_for_account` for both halves (finding N-64) --
    so this entry survives on its own tests, which is the dead-code-alive-for-
    its-own-tests shape plan steps C3b4 / D2a / F2 / E1e each deleted.  It is
    kept here rather than deleted because plan step X-g2b's contract is to move
    producers onto the replay, not to prune the seam's surface (rule 6);
    finding **N-85** records it for the deletion step.

    Args:
        account: The interest-bearing account.
        ctx: The read pass's :class:`~app.services.balance_at.BalanceContext`
            (its scenario scopes the fold; its ``as_of`` is the reader's NOW;
            its ``reported_periods()`` is the walk domain, and the caller
            filters to the periods whose interest it wants).

    Returns:
        ``dict`` mapping period_id to the ``Decimal`` interest earned in
        that period, one entry per reported period.  **Never ``{}`` as a
        degradation**: it short-circuited for an account with
        ``current_anchor_period_id IS NULL``, a state the schema forbade and
        the column no longer exists to express (finding N-73, plan step
        X-f1c3a).

    Raises:
        PayCalendarError: The owner's paydays cannot define a calendar, which
            since plan step C2-c is reachable from every per-period seam entry
            rather than only from the recurrence pages -- see
            :meth:`~app.services.balance_at.BalanceContext.calendar`, where the
            reporting domain is derived, for the one state that produces it and
            the step that removes it.
    """
    _, interest_by_period = interest_projection_for_account(account, ctx)
    return interest_by_period


def build_account_balance_map(
    account: Account,
    ctx: "BalanceContext",
    inputs: ContributionInputs,
) -> "OrderedDict[int, Decimal]":
    """Compute period_id -> balance for one NON-loan account.

    The net-worth path for every kind EXCEPT amortizing loans, and since plan
    step X-g2b it dispatches on NOTHING: the account's balance is its event
    replay, whose ACCRUAL tier exists only if the account models a return and
    whose CONTRIBUTION tier exists only if its payroll funds it.  An INTEREST
    account, an INVESTMENT, a Property and a plain checking account are one
    question asked once (ruling R-AD).

    **What that replaced, and why the branches were the defect rather than the
    structure.**  The ladder here routed INVESTMENT to
    ``growth_engine.project_balance`` spliced over a cash base by a preference
    order, APPRECIATING to an appreciation curve over a flat anchor carry, and
    INTEREST to a second pass over a finished base map.  The splice overrode 12
    of the 15 balance assertions the three modelled accounts actually carry
    (findings N-43 / N-74), the second pass accrued on a period's END balance
    while the curve grew its START (two conventions for one question), and all
    three answered a PERIOD where the caller asked for a DATE (finding N-71).
    One replay has no join to get wrong, no boundary convention to pick, and a
    step for every day.

    **AMORTIZING loans are dispatched by the seam, not here** (plan step C3b3):
    :func:`app.services.balance_at._inputs._account_balance_map` reads its own
    positions()-based per-period map for a configured loan, because that
    producer sits ABOVE this kernel and the kernel cannot import it back.  An
    AMORTIZING account with NO ``LoanParams`` does arrive here, models no
    return, and is therefore its cash fold -- the same degrade the seam's
    scalar makes for it.

    Args:
        account: The account to project.
        ctx: The read pass's :class:`~app.services.balance_at.BalanceContext`
            (its scenario scopes the fold and the contribution feed; its
            ``as_of`` is ruling R-G's clamp floor, and its
            ``reported_periods()`` is the output domain).
        inputs: This account's
            :class:`~app.services.balance_at._asset_contributions.ContributionInputs`
            -- its investment params, its deductions and the engine
            gross-biweekly, loaded by
            :func:`app.services.balance_at._inputs._contribution_inputs_for_accounts`.
            Its ``absent()`` constructor is the explicit token for an account
            that cannot have a contribution feed.

    Returns:
        OrderedDict mapping period_id to Decimal balance.  **Never ``None``**:
        it answered ``None`` for an account with ``current_anchor_period_id IS
        NULL``, a state the schema forbade and the column no longer exists to
        express (finding N-73, plan step X-f1c3a).

    Raises:
        PayCalendarError: The owner's paydays cannot define a calendar, which
            since plan step C2-c is reachable from every per-period seam entry
            rather than only from the recurrence pages -- see
            :meth:`~app.services.balance_at.BalanceContext.calendar`, where the
            reporting domain is derived, for the one state that produces it and
            the step that removes it.
    """
    return OrderedDict(
        (period_id, column.balance)
        for period_id, column in _modelled_columns(
            account, ctx, inputs,
        ).items()
    )
