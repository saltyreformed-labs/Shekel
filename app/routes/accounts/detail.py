"""
Shekel Budget App -- Per-Account Detail Pages

Detail / projection pages for cash and physical-asset accounts.  Split
out of the historical monolithic ``app/routes/accounts.py`` in Commit 21
of the financial-calculation audit follow-up (F-1); rebuilt for the
Fable 5 UI/UX overhaul (``docs/design/account_detail_audit.md``, "Rebuild
decisions").

The overhaul merged the former ``checking_detail`` and ``interest_detail``
pages into ONE :func:`cash_detail` page that serves EVERY cash account
kind: Checking, the ``has_interest`` types (HYSA / Money Market / CD /
HSA), and the previously page-less plain types (Savings, Credit Card, and
plain custom types).  Loans (``has_amortization``), physical assets
(``has_appreciation``), and retirement / investment accounts are NOT
served here -- they keep their own screens
(:func:`loan.dashboard`, :func:`property_detail`,
:func:`investment.dashboard`).  ``checking_detail`` and
``interest_detail`` survive only as thin redirect stubs to
:func:`cash_detail` so external bookmarks and the not-yet-updated cockpit
``detail_endpoint`` macro still resolve.

Balance production routes every figure through the balance-at seam
(Level-1 Commit 8).  The Fable 5 merge was a PRESENTATION rebuild and moved no
producer; plan step X-c2b2 then moved BOTH branches onto the cash fold, so what
the page shows changed there and not here:

* interest-bearing accounts via ``balance_at.interest_projection_for_account``,
  which returns the interest-accrued balances AND the per-period earned
  interest from ONE cash fold (plan step X-c2b2: the page read the
  kind-correct ``balance_map`` and the earned-interest accessor separately
  until the accrual's base became that fold, at which point the pair folded
  the same account TWICE per render -- finding N-64), and
* plain cash accounts via the cash-flow entry
  ``balance_at.cash_balance_map``.

Both seam entries delegate to the canonical entries-aware producers, so the
silent-degrade seam fixed by CRIT-01 / F-009 cannot reappear here.  The F-6
static guard in :mod:`tests.test_routes.test_accounts.test_checking_detail`
pins this contract by asserting that the seam (``balance_at.``) is used and
that the whole-account kind-correct ``balance_map`` is NOT called beside the
interest map; that guard reads this file directly.  Its third arm forbade the
bare entries-blind ``calculate_balances``, and plan step X-g4b deleted the arm
with the producer: a negative arm naming a function that no longer exists is a
sentence that can never fail.
"""

from __future__ import annotations

import json
import logging
from decimal import Decimal
from typing import TYPE_CHECKING

from flask import abort, flash, redirect, render_template, request, url_for
from flask_login import current_user

from app import ref_cache
from app.enums import CompoundingFrequencyEnum
from app.exceptions import RequiredRecordMissing
from app.extensions import db
from app.models.account import Account
from app.models.asset_appreciation_params import AssetAppreciationParams
from app.models.interest_params import InterestParams
from app.models.ref import CompoundingFrequency
from app.routes.accounts._bp import accounts_bp
from app.routes.accounts._cash_page import cash_detail_wrong_type, load_cash_account_or_404
from app.routes.accounts.history import balance_history_context
from app.routes.accounts.outstanding import outstanding_context
from app.routes.accounts.reconcile import (
    panel_id,
    reconcile_context,
    reconcile_statement,
)
from app.services import (
    balance_at,
    card_apr,
    cash_ledger,
    home_equity_service,
    property_equity_chart,
)
from app.services.account_projection import is_revolving
from app.services.balance_at import BalanceContext
from app.services.card_terms import load_card_terms
from app.utils.account_validation import (
    _appreciation_params_schema,
    _interest_params_schema,
)
from app.utils.auth_helpers import get_or_404, require_owner
from app.utils.period_projections import (
    ONE_YEAR_MONTHS,
    horizon_offsets,
    project_balance_horizons,
)

if TYPE_CHECKING:
    # Typing-only imports for the per-page helper signatures (lazy strings
    # via ``from __future__ import annotations``; no runtime cost).
    from app.services.cash_ledger import AnchorPoint
    from app.services.pay_calendar import (
        DerivedPeriod,
        PayCadence,
        PayCalendar,
        PeriodWindow,
    )

logger = logging.getLogger(__name__)


# Chart.js x-axis label format for the balance-projection trend: month
# abbreviation plus un-padded day (e.g. "Jun 5").  The SAME convention as
# the savings cockpit's ``_serialize_net_worth_chart`` so the two trend
# charts read identically.
_CHART_LABEL_FORMAT = "%b %-d"


# ── Shared detail-page helpers ────────────────────────────────────


def _current_period_balance(
    balances: dict[int, Decimal],
    current_period: DerivedPeriod | None,
    anchor: AnchorPoint | None,
) -> Decimal | None:
    """Return the current-period projected balance, else the anchor balance.

    The page hero figure: the projected balance at the current period
    when one exists, otherwise the resolved anchor balance (E-19),
    otherwise ``None``.
    """
    current_bal = (
        balances.get(current_period.period_id) if current_period else None
    )
    if current_bal is None and anchor is not None:
        current_bal = anchor.balance
    return current_bal


def _interest_params(account: Account) -> InterestParams:
    """Return the account's :class:`InterestParams`, refusing if it has none.

    **It used to auto-create the row, and plan step balance:X-i3 deleted that
    branch rather than declaring it.**  A render that repairs data is a write
    inside a read: it cost this page the one snapshot every figure on it is
    computed against, and it hid the door that should have written the row.
    The gap was real, and WIDER than the step's own first draft said: the
    seeder had a single caller, account CREATION, while **THREE doors can make
    an account interest-bearing** -- creating it, re-classing it
    (``crud.update_account``), and editing the TYPE itself
    (``types.update_account_type``, which may flip ``has_interest`` on an
    owner's own custom type and so change every account already on it, with no
    account row touched at all).  All three hold the rule now, through the one
    statement of it in :mod:`app.routes.accounts._type_params`.

    So a missing row means the data was changed outside the application, and
    the honest answer is to refuse rather than manufacture a zero-rate row that
    reads on screen exactly like a rate the owner configured.  **NOT the answer
    :func:`require_scenario` gives**, which an earlier draft of this paragraph
    cited and which is the opposite disposition: a missing baseline has a
    handler and a repair page (ruling R-BW) because the owner can fix it from a
    screen the app can still render.  This state has no door that produces it,
    so there is no screen to send anyone to.

    Measured before the branch was deleted: **0 of 9 production accounts** of a
    parameterised kind were missing their satellite row, on either table.

    Args:
        account: The interest-bearing cash account being rendered.

    Returns:
        The account's :class:`InterestParams`.

    Raises:
        RequiredRecordMissing: The account carries no params row.
    """
    params = (
        db.session.query(InterestParams)
        .filter_by(account_id=account.id)
        .first()
    )
    if params is None:
        raise RequiredRecordMissing(
            f"account {account.id} is interest-bearing and carries no "
            f"budget.interest_params row, so there is no APY or compounding "
            f"basis to project it at. All three doors that can make an "
            f"account interest-bearing write one -- creating it, re-classing "
            f"it, and editing the account TYPE -- so reaching this means the "
            f"row was removed outside the application. To rebuild it, change "
            f"the account's type to another kind and change it back: the "
            f"re-class door seeds on a CHANGE, so re-saving the same type is a "
            f"no-op."
        )
    return params


def _card_context(
    account: Account, ctx: BalanceContext, is_card: bool,
) -> dict:
    """The Card terms card's context: the terms row and, once it exists, the APR.

    Three states under one key shape.  A non-card carries the keys empty
    (the partial is not included; the template's context has one shape).  A
    dormant card carries its ``None`` terms and nothing else: the partial
    renders the terms form blank and no APR section, so the APR series is
    not read -- whatever rows the account holds are unread while it is
    dormant, and the doors are gated the same way (plan step
    credit_card:CC-3, developer ruling **R-CC28**).  A configured card
    carries its terms, its APR series newest first, and the rate in effect
    on the read pass's own day (``ctx.as_of``, the one clock this page
    holds).

    A missing terms row is the opposite disposition to
    :func:`_interest_params`, and deliberately so (plan step credit_card:CC-2,
    design 3.4): an interest-bearing account's params row is written by every
    door that makes it interest-bearing, so a missing one is corrupt data and
    the page refuses; a card's row is written by the OWNER alone, through the
    terms door, so a missing one is the ordinary state of a card whose terms
    are not yet stated.

    Args:
        account: The account being rendered.
        ctx: The page's read pass.
        is_card: The ONE card predicate's answer for *account*, decided once
            by the caller and also handed to the template.

    Returns:
        ``card_terms``, ``card_aprs`` and ``card_apr_today``.
    """
    terms = load_card_terms(account.id) if is_card else None
    if terms is None:
        return {"card_terms": None, "card_aprs": [], "card_apr_today": None}
    aprs = card_apr.load_card_aprs(account.id)
    return {
        "card_terms": terms,
        "card_aprs": aprs,
        "card_apr_today": card_apr.apr_in_effect(aprs, ctx.as_of),
    }




def _build_horizons(
    current_balance: Decimal | None,
    current_period: DerivedPeriod | None,
    all_periods: PeriodWindow,
    balances: dict[int, Decimal],
    calendar: PayCalendar,
) -> list[dict]:
    """Build the forward horizon chip rows for the template.

    One row per horizon that has a projected balance, in the shared
    :data:`~app.utils.period_projections.HORIZON_MONTHS` order.  Each row
    carries the horizon ``label`` ("3 months" / "6 months" / "1 year"),
    its projected ``value``, and the ``delta`` from the current balance
    (``value - current_balance``), all ``Decimal``.  Returns an empty list
    when there is no current balance to project or delta from.

    **It takes the CALENDAR rather than a resolved cadence** (plan step
    **R-F17**).  How many pay periods a horizon named in months reaches is a
    function of the owner's cadence.  *This paragraph used to say the calendar
    is what made the read SAFE, because
    :attr:`~app.services.pay_calendar.PayCalendar.cadence` REFUSED a calendar
    holding no cadence and a current period was the proof there was one; plan
    step ``pay_calendar:C4-d`` (ruling R-PC45) made that property total, so a
    calendar in hand always answers and nothing here can raise.  What remains
    true is the reason the CALENDAR travels rather than a loose number:*
    :func:`~app.services.pay_calendar.derive_periods` derives every span from
    one owner's own paydays, so a non-empty calendar always carries
    one, and it cannot be paired with another owner's rhythm.

    Args:
        current_balance: The page hero's figure, which every ``delta`` is
            measured from.  ``None`` yields no rows.
        current_period: The pay period covering the read pass's clock, or
            ``None`` -- which also yields no rows, since no horizon is
            measurable from nowhere.
        all_periods: The pass's reporting window, searched by ``period_index``.
        balances: The seam's ``{period_id: balance}`` map for this account.
        calendar: The read pass's own
            :class:`~app.services.pay_calendar.PayCalendar`, read for the
            owner's cadence alone.

    Returns:
        The chip rows, in horizon order.
    """
    if current_balance is None or current_period is None:
        return []
    projected = project_balance_horizons(
        current_period, all_periods, balances,
        horizon_offsets(calendar.cadence),
    )
    return [
        {"label": label, "value": value, "delta": value - current_balance}
        for label, value in projected.items()
    ]


def _interest_next_year(
    interest_by_period: dict[int, Decimal],
    current_period: DerivedPeriod,
    all_periods: PeriodWindow,
    cadence: PayCadence,
) -> Decimal:
    """Sum the interest earned over the owner's next year of paychecks.

    The health-chip figure: the ``Decimal`` sum of ``interest_by_period``
    for every period whose ``period_index`` falls in
    ``[current + 1, current + paychecks_within(12)]`` -- the next full year
    of the owner's OWN paychecks after the current one.  ``Decimal("0.00")``
    is a legitimate result (a zero-APY account, or a horizon with no
    projected interest), NOT a "missing" sentinel; the caller only invokes
    this for an interest-bearing account with a current period.

    **The window width was a hardcoded ``_ONE_YEAR_PERIODS = 26``** whose own
    comment said it matched the "1 year" balance chip beside it -- an agreement
    held by a sentence, and wrong for both chips at any cadence but biweekly
    (ledger row **F-17**).  Both now resolve
    ``paychecks_within(ONE_YEAR_MONTHS)``, so the interest chip covers exactly
    the span the balance chip beside it reaches, by construction.  The count is
    :attr:`~app.services.pay_calendar.PayCadence.periods_per_year` for a
    twelve-month span, so it is at least 1 and the window is never empty.

    Args:
        interest_by_period: The seam's per-period earned interest for this
            account.
        current_period: The pay period covering the read pass's clock.
        all_periods: The pass's reporting window.
        cadence: The owner's :class:`~app.services.pay_calendar.PayCadence`.

    Returns:
        The summed interest, a ``Decimal``.
    """
    lo = current_period.period_index + 1
    hi = current_period.period_index + cadence.paychecks_within(ONE_YEAR_MONTHS)
    total = Decimal("0.00")
    for period in all_periods:
        if lo <= period.period_index <= hi:
            total += interest_by_period.get(period.period_id, Decimal("0.00"))
    return total


def _build_chart(
    all_periods: PeriodWindow,
    balances: dict[int, Decimal],
    current_period: DerivedPeriod | None,
) -> tuple[str, bool]:
    """Serialize the balance-projection trend to a Chart.js JSON string.

    The single Chart.js serialization boundary for this page (coding
    standards: ``float`` lives only here, never in a calculation).  The
    series is every period that HAS a projected balance, in
    ``period_index`` order; ``labels`` uses the same ``%b %-d`` convention
    as the cockpit's ``_serialize_net_worth_chart``, ``balance`` is the
    parallel ``float`` array, and ``current_index`` is the position of the
    current period within the series (the solid/dashed "today" boundary,
    an int in ``[0, len(series)]``, mirroring the cockpit serializer).
    Defaults ``current_index`` to ``0`` when the current period is not in
    the series (no current period, or it precedes the anchor).

    Returns:
        ``(chart_json, has_chart)`` -- the JSON string and whether the
        series is non-empty.
    """
    # The window is ALREADY in payday order, which is ``period_index`` order
    # by the derivation's own construction, so the explicit sort this carried
    # is gone (pay-calendar plan step C2-f2d-3): a
    # :class:`~app.services.pay_calendar.PeriodWindow` sorts at construction
    # and the ordinal IS the position in that order.
    series = [p for p in all_periods if p.period_id in balances]

    current_index = 0
    if current_period is not None:
        for i, period in enumerate(series):
            if period.period_id == current_period.period_id:
                current_index = i
                break

    chart_json = json.dumps({
        "labels": [p.end_date.strftime(_CHART_LABEL_FORMAT) for p in series],
        "balance": [float(balances[p.period_id]) for p in series],
        "current_index": current_index,
    })
    return chart_json, bool(series)


def _cash_projection(
    account: Account,
    is_interest: bool,
    balance_ctx: BalanceContext,
    all_periods: PeriodWindow,
) -> "tuple[dict[int, Decimal], dict[int, Decimal], AnchorPoint | None]":
    """Produce the per-period balances (and interest) for a cash account.

    The single balance-production site (Level-1 Commit 8), on the cash FOLD
    for both branches since plan step X-c2b2:

    * interest-bearing accounts read
      ``balance_at.interest_projection_for_account`` -- the interest-accrued
      balances AND the per-period earned interest, from ONE fold (N-64).  It
      no longer takes the account's ``InterestParams``: since plan step X-g2b
      the replay reads the account's own accrual rule through the seam's ONE
      predicate, so this route cannot hand it a rate loaded from somewhere
      else, and
    * plain cash accounts read the cash-flow ``balance_at.cash_balance_map``
      (pure transaction running-balance).

    The anchor is resolved via the dated ``AccountAnchorHistory`` SoT for the
    hero caption and the current-period fallback.  Returns empty maps and a
    ``None`` anchor for a plain account with no pay periods, so the template
    renders cleanly.

    **The no-baseline arm of both guards is gone** (plan step X-v2, ruling
    R-BW): ``balance_ctx.scenario_id`` raises and one application-level handler
    answers, so this helper no longer decides what a user whose balances the
    app cannot compute sees -- it returned empty maps, where `/savings`
    returned a fabricated ``$0.00`` and the loan page returned a 500.

    Returns:
        ``(balances, interest_by_period, anchor)``.  ``interest_by_period``
        is always empty for a plain account.
    """
    balances: dict[int, Decimal] = {}
    interest_by_period: dict[int, Decimal] = {}
    anchor: AnchorPoint | None = None
    if is_interest:
        anchor = cash_ledger.resolve_anchor(account)
        # ONE walk for both figures.  Asking the seam twice (the balance
        # map, then the interest map) folded the account's whole cash
        # event stream twice per render once the accrual's base became
        # that fold -- and the two halves are one projection, so the page
        # would also have had two chances to disagree with itself.
        balances, interest_by_period = (
            balance_at.interest_projection_for_account(account, balance_ctx)
        )
    elif all_periods:
        balances = balance_at.cash_balance_map(account, balance_ctx)
        anchor = cash_ledger.resolve_anchor(account)
    return balances, interest_by_period, anchor


def _cash_detail_context(account: Account, ctx: BalanceContext) -> dict:
    """Assemble the cash detail template context (page AND band fragments).

    Extracted from :func:`cash_detail` when the D14 click-to-edit port
    added the band-refresh fragment (:func:`cash_band`): both render
    paths must compute the hero balance, horizon chips, interest chip,
    and chart from the same producers or a band refresh could disagree
    with the page render.

    Balance production: interest accounts read
    ``balance_at.interest_projection_for_account`` (both halves of ONE fold);
    plain cash accounts read the cash-flow ``balance_at.cash_balance_map``.
    Both seam entries are the cash FOLD sampled at period ends (plan step
    X-c2b2), so this module calls no balance producer directly.  The
    anchor is resolved via the dated ``AccountAnchorHistory`` SoT (E-19,
    Commit 4) for the hero caption and the current-period fallback; the ``no
    pay periods`` empty-state guard is kept (a freshly-registered user with no
    generated periods) and the templates render cleanly when ``balances`` is
    empty.  The ``scenario is None`` guard beside it went at plan step X-v2 --
    that state is answered above every route now, in one place (ruling R-BW).
    """
    is_interest = bool(
        account.account_type and account.account_type.has_interest
    )

    # BOTH questions off the pass's ONE memoized calendar (pay-calendar plan
    # step C2-f2d-3).  They were two SQL readers on two clocks -- the second
    # defaulting to its own ``date.today()`` rather than the pass's ``as_of``,
    # so a render begun before midnight and reaching this line after it placed
    # the hero in one paycheck and the seam's columns in another.
    calendar = ctx.calendar()
    all_periods = ctx.reported_periods()
    current_period = calendar.period_containing(ctx.as_of)

    # The params row is READ before any projection so the parameters card
    # always renders for an interest-bearing account.  Plain accounts carry no
    # params / compounding list.  It is read rather than repaired since plan
    # step balance:X-i3 -- see :func:`_interest_params`.
    params = _interest_params(account) if is_interest else None
    compounding_frequencies = (
        CompoundingFrequency.query.order_by(CompoundingFrequency.id).all()
        if is_interest else []
    )

    balances, interest_by_period, anchor = _cash_projection(
        account, is_interest, ctx, all_periods,
    )

    current_balance = _current_period_balance(balances, current_period, anchor)
    chart_json, has_chart = _build_chart(all_periods, balances, current_period)

    return {
        "account": account,
        "is_interest": is_interest,
        "current_balance": current_balance,
        "current_period": current_period,
        # ``anchor_as_of`` is the day the asserted balance was TRUE
        # (``AnchorPoint.observed_on``), NOT the anchor period's start date --
        # fixing the audit's finding #2 (a mid-period true-up used to show the
        # period start instead of the true-up date).
        #
        # It reads the BUSINESS day, not the recording instant (ruling R-DH,
        # plan step 2).  The two were the same day by construction until
        # ``observed_on`` became user-supplied; now an account back-dated to
        # 2026-01-01 would caption "anchored Jul 31" off ``created_at`` while
        # the engine treats the balance as Jan 1's closing balance.  The
        # template renders a plain date, so no timezone conversion applies --
        # the day is already the user's.
        "anchor_as_of": anchor.observed_on if anchor is not None else None,
        "horizons": _build_horizons(
            current_balance, current_period, all_periods, balances, calendar,
        ),
        # The next-year interest chip is interest-only; a plain account
        # carries ``None`` (the template omits the chip).  ``Decimal("0.00")``
        # is a legitimate value for a zero-APY interest account.
        #
        # ``calendar.cadence`` sits INSIDE the true branch, and since plan step
        # ``pay_calendar:C4-d`` (ruling R-PC45) that is no longer load-bearing
        # for the cadence: that property is total now, so evaluating it
        # unguarded would raise nothing.  The GUARD is still required, for its
        # other reason -- ``_interest_next_year`` dereferences
        # ``current_period.period_index``, so a lapsed schedule with no current
        # period is an ``AttributeError`` without it.  See
        # :func:`_build_horizons`.
        "interest_next_year": (
            _interest_next_year(
                interest_by_period, current_period, all_periods,
                calendar.cadence,
            )
            if is_interest and current_period is not None else None
        ),
        "params": params,
        "compounding_frequencies": compounding_frequencies,
        "chart_json": chart_json,
        "has_chart": has_chart,
        # The outstanding list (plan step S1-c, widened at X-f2-c), built by
        # the SAME helper the post-true-up prompt uses so the page and the
        # modal cannot come to disagree about what is still unreconciled.
        # The statement is resolved once and handed in (finding N-222), and the
        # CALENDAR inside it is this pass's own memoized one -- the same value
        # the horizon chips and the current-period caption above were built
        # from, so the panel dates its offers by the paydays the rest of this
        # page already reported (pay-calendar plan step C4-a-2).
        **reconcile_context(
            account, panel=panel_id(account.id),
            statement=reconcile_statement(account, calendar),
        ),
    }


@accounts_bp.route("/accounts/<int:account_id>/details")
@require_owner
def cash_detail(account_id):
    """Unified cash-account detail page (checking / interest / plain cash).

    Shows the account's balance hero (the D14 click-to-edit anchor
    control) with an honest anchored caption, the 3 / 6 / 12-month
    horizon chips, and a trend chart; interest-bearing accounts
    additionally get an APY / compounding parameters card, a "next 12
    months" projected-interest chip, and their interest-accrued
    balances.  Serves every cash account kind (see
    :func:`~app.routes.accounts._cash_page.cash_detail_wrong_type` for the
    type gate); loans, physical assets, and retirement / investment accounts
    404 out.  The balance production contract lives on
    :func:`_cash_detail_context` (shared with the band fragment).

    The Balance history card below the reconcile panel (plan step X-f2-b) is
    composed separately -- see the comment on the call.
    """
    account = load_cash_account_or_404(account_id)
    # ONE read pass for the whole page.  Both builders below fold this
    # account, and handing each its own context would be two resolutions of
    # one question inside one request.  **The WALK ran twice as well -- the
    # band's fold and the history's -- until plan step X-i4** put the cash
    # fold on the pass beside the loan derivations it already memoized, so
    # both builders now share one assembly and this route holds BOTH halves
    # to one.  (This comment named X-i1 as that half's owner; X-i4 reached it
    # first, by making the pass hand out the fold.)
    ctx = BalanceContext.build(current_user.id)
    is_card = is_revolving(account)
    return render_template(
        "accounts/cash_detail.html",
        # Under ONE name rather than splatted: ``reconcile_context`` inside
        # ``_cash_detail_context`` already publishes ``account`` and
        # ``panel_id``, so splatting a second builder's keys over it would
        # silently re-root the reconcile panel at the history card's DOM id
        # and break that panel's POST target.  And it is composed HERE rather
        # than inside ``_cash_detail_context`` because that builder also
        # serves the BAND fragment, which re-renders on every
        # ``balanceChanged`` and has no use for an assertion log -- folding it
        # in would walk the account's whole event stream again for a card the
        # response does not carry.
        history=balance_history_context(account, ctx),
        # The same nesting, for the same reason, and off the same pass (plan
        # step balance:X-f3c-3).  Both builders read the fold ``ctx`` already
        # memoizes, so the outstanding difference costs this page no second
        # walk -- only the bank comparison its verdict rests on.  It is out of
        # ``_cash_detail_context`` for the reason ``history`` is: that builder
        # also serves the BAND fragment, which re-renders on every
        # ``balanceChanged`` and carries neither card.
        books_difference=outstanding_context(account, ctx),
        # The Card terms card (plan step credit_card:CC-2, ruling R-CC25),
        # composed here for the reason the two above are: the band fragment
        # ``_cash_detail_context`` also serves never renders it.  Gated by the
        # ONE card predicate (CC-1); the row is read only for a card, and
        # ``None`` is the dormant state the partial renders blank.
        is_card=is_card,
        **_card_context(account, ctx, is_card),
        **_cash_detail_context(account, ctx),
    )


@accounts_bp.route("/accounts/<int:account_id>/details/band")
@require_owner
def cash_band(account_id):
    """HTMX partial: re-render the cash detail band (D14 click-to-edit port).

    The ``balanceChanged`` refresh target: after an anchor save through
    the hero's inline editor, the page's ``#cash-band-region`` re-fetches
    this fragment so the hero, the horizon chips, the interest chip, AND
    the trend chart all recompute from the new anchor together -- a
    hero-only refresh would leave the chips and chart disagreeing with
    it (``account_detail.js`` re-creates the chart when the canvas
    returns).  Renders ``accounts/_cash_band.html`` with the same
    context builder the page uses, so the swapped-in band reads the
    identical contract.

    Non-HTMX requests redirect to the detail page (the band is a
    fragment, not a standalone page).
    """
    if not request.headers.get("HX-Request"):
        return redirect(url_for("accounts.cash_detail", account_id=account_id))
    account = load_cash_account_or_404(account_id)
    return render_template(
        "accounts/_cash_band.html",
        **_cash_detail_context(account, BalanceContext.build(current_user.id)),
    )


def render_cash_balance_hero(account: Account) -> str | None:
    """Draw the cash balance hero cell, or ``None`` if the cash page hides it.

    The cash page's DRAW (rulings R-CC74 / R-CC77, finding CC-365): the ONE
    function behind :func:`cash_balance_hero` -- the Cancel / Escape target
    ``accounts.anchor._anchor_revert_url`` maps ``revert=cash`` to -- and
    behind the anchor save opened from the hero, which answered with the
    grid's cell until then.  Renders ``accounts/_cash_balance_hero.html``
    with the resolver current-period balance the detail headline shows, off
    the page's own context builder and a read pass this opens, as the GET
    always has.  It never aborts: the save calls it after its write has
    committed.

    Args:
        account: The loaded, ownership-checked account.

    Returns:
        The rendered cell, or ``None`` for a kind this page does not serve
        (:func:`cash_detail_wrong_type`) -- which the save answers with an
        empty cell, and which the GET's own gate 404s before drawing.
    """
    if cash_detail_wrong_type(account):
        return None
    context = _cash_detail_context(
        account, BalanceContext.build(current_user.id),
    )
    return render_template(
        "accounts/_cash_balance_hero.html",
        account=account,
        current_balance=context["current_balance"],
    )


@accounts_bp.route("/accounts/<int:account_id>/details/balance-hero")
@require_owner
def cash_balance_hero(account_id):
    """HTMX partial: the cash balance hero cell (D14 click-to-edit port).

    The Cancel / Escape revert target for the cash detail page's
    click-to-edit anchor editor: ``accounts._anchor_revert_url`` maps
    ``revert=cash`` here.  The cell is :func:`render_cash_balance_hero`'s --
    the draw a save opened from the hero answers with too, before its
    ``balanceChanged`` re-renders the whole band via :func:`cash_band`.
    The gate 404s every kind that draw declines, so it cannot answer
    ``None`` here.  Non-HTMX requests redirect to the detail page.
    """
    if not request.headers.get("HX-Request"):
        return redirect(url_for("accounts.cash_detail", account_id=account_id))
    return render_cash_balance_hero(load_cash_account_or_404(account_id))


def _redirect_to_cash_detail(account_id):
    """Redirect a legacy detail URL to :func:`cash_detail`, preserving setup=1.

    The shared body of the ``checking_detail`` / ``interest_detail``
    redirect stubs.  Resolves per-resource ownership FIRST via the
    established ``get_or_404`` + ``abort(404)`` pattern the sibling routes
    (:func:`cash_detail`, :func:`update_interest_params`) use, then
    redirects.  ``@require_owner`` only gates the owner-vs-companion ROLE,
    not per-account ownership; without this check a cross-user "not yours"
    probe would leak resource existence as a 302 redirect instead of the
    security response rule's 404 (a 404 for both "not found" and "not
    yours").  Forwards the ``setup=1`` onboarding query arg when present so
    a post-create redirect still lands on the wizard banner.
    """
    account = get_or_404(Account, account_id)
    if account is None:
        abort(404)

    if request.args.get("setup") == "1":
        return redirect(
            url_for("accounts.cash_detail", account_id=account_id, setup=1),
        )
    return redirect(url_for("accounts.cash_detail", account_id=account_id))


@accounts_bp.route("/accounts/<int:account_id>/checking")
@require_owner
def checking_detail(account_id):
    """Deprecated alias: redirect to the unified :func:`cash_detail` page.

    The Fable 5 overhaul merged the checking detail page into
    :func:`cash_detail`.  Kept as a redirect (not deleted) so external
    bookmarks and the not-yet-updated cockpit ``detail_endpoint`` macro
    still resolve.
    """
    return _redirect_to_cash_detail(account_id)


# ── Interest Params ──────────────────────────────────────────────


@accounts_bp.route("/accounts/<int:account_id>/interest")
@require_owner
def interest_detail(account_id):
    """Deprecated alias: redirect to the unified :func:`cash_detail` page.

    The Fable 5 overhaul merged the interest detail page into
    :func:`cash_detail`.  Kept as a redirect (not deleted) so external
    bookmarks and the not-yet-updated cockpit ``detail_endpoint`` macro
    still resolve; ``setup=1`` is preserved for the post-create wizard.
    """
    return _redirect_to_cash_detail(account_id)


@accounts_bp.route("/accounts/<int:account_id>/interest/params", methods=["POST"])
@require_owner
def update_interest_params(account_id):
    """Update interest parameters (APY, compounding frequency).

    URL and behaviour are unchanged from before the cash-detail merge; only
    the success / validation redirect target moved to
    :func:`cash_detail` (the merged page that now hosts the parameters
    card).
    """
    account = get_or_404(Account, account_id)
    if account is None:
        abort(404)

    if not account.account_type or not account.account_type.has_interest:
        flash("This account type does not support interest parameters.", "warning")
        return redirect(url_for("savings.dashboard"))

    errors = _interest_params_schema.validate(request.form)
    if errors:
        flash("Please correct the highlighted errors and try again.", "danger")
        return redirect(url_for("accounts.cash_detail", account_id=account_id))

    data = _interest_params_schema.load(request.form)

    params = (
        db.session.query(InterestParams)
        .filter_by(account_id=account.id)
        .first()
    )
    if not params:
        # HIGH-06 / Commit 24: a first-save that omits ``apy`` would
        # pre-fix have silently materialised the column
        # ``server_default="0.04500"`` (4.5% rate the user never
        # configured).  The defaults are gone (see
        # :class:`~app.models.interest_params.InterestParams`); a
        # first save that omits ``apy`` is now an explicit user
        # error -- flash and redirect instead of constructing a row
        # that would fail ``NotNullViolation`` at commit.  The
        # account-create flow auto-creates the row with
        # ``apy=Decimal("0")`` so this branch only fires when an
        # InterestParams row was somehow lost (data loss, manual
        # delete) and the user is reconfiguring; requiring an
        # explicit ``apy`` keeps the failure visible.
        if "apy" not in data:
            flash(
                "An APY value is required when configuring "
                "interest parameters for the first time.",
                "danger",
            )
            return redirect(
                url_for("accounts.cash_detail", account_id=account_id),
            )
        # #38: recreate with the DAILY ref id so the NOT NULL FK is
        # satisfied even when the update payload omits the frequency.
        params = InterestParams(
            account_id=account.id,
            compounding_frequency_id=ref_cache.compounding_frequency_id(
                CompoundingFrequencyEnum.DAILY,
            ),
        )
        db.session.add(params)

    if "apy" in data:
        # E-28 / HIGH-06 (Commit 24): the schema's ``@pre_load``
        # already divided the form's user-facing percent by 100, so
        # ``data["apy"]`` is the storage-domain decimal fraction the
        # DB CHECK ``apy >= 0 AND apy <= 1`` enforces.  The route
        # stores it verbatim; no second divide.
        params.apy = data["apy"]
    if "compounding_frequency_id" in data:
        params.compounding_frequency_id = data["compounding_frequency_id"]

    db.session.commit()
    logger.info("Updated interest params for account %d", account.id)
    flash("Interest parameters updated.", "success")
    return redirect(url_for("accounts.cash_detail", account_id=account_id))


# ── Property (physical-asset) Detail & Params ─────────────────────


def _property_chart_context(
    params: AssetAppreciationParams,
    equity: home_equity_service.HomeEquity,
    property_account: Account,
    balance_ctx: BalanceContext,
) -> dict[str, object]:
    """Serialize the property equity-over-time chart for the detail band.

    The single Chart.js serialization boundary for the property page (coding
    standards: ``float`` lives only here, never in a money calculation).
    ``has_equity_chart`` is ``False`` -- and the band shows the "set a market
    value" empty state instead of a chart -- only when there is no positive
    market value to anchor the appreciation arc on (a freshly-created Property
    whose value has not been set yet).  Otherwise the market-value /
    secured-debt / equity series come from
    :func:`app.services.property_equity_chart.build_property_equity_chart`
    (fed by the seam's :func:`app.services.balance_at.secured_loan_series`, which
    FOLDS each loan's per-month debt off the read pass's ONE memoized resolution --
    the same one the equity hero reads, so the chart and the hero cannot disagree,
    and this route never holds a resolver bundle), floated here into the ``data-chart``
    JSON the template hands to ``property_detail.js``; ``chart_state`` drives the caption
    variant (``standard`` / ``zero_rate`` / ``no_loans``), ``today_index`` the
    Today boundary, and ``debt_tier`` the per-month estimated / confirmed /
    projected styling.  ``has_estimated_debt`` -- ``True`` when any month is the
    ``estimated`` contractual back-projection tier -- gates the caption's dotted
    pre-tracking clause so the copy names a texture only when the chart draws it
    (the "a figure and its caption never disagree" design principle).

    Args:
        params: The Property's :class:`AssetAppreciationParams` (the rate).
        equity: The :class:`~app.services.home_equity_service.HomeEquity`
            snapshot (its ``market_value`` gates ``has_equity_chart`` and is the
            chart's anchor).
        property_account: The Property account; the seam reads its
            ``secured_loans`` to pack the chart's debt series.
        balance_ctx: The read pass's
            :class:`~app.services.balance_at.BalanceContext`; its ``as_of``
            is the chart's compounding origin.

    Returns:
        The ``has_equity_chart`` / ``chart_json`` / ``chart_state`` context the
        ``property_detail.html`` band reads.
    """
    if equity.market_value <= Decimal("0"):
        return {
            "has_equity_chart": False,
            "chart_json": json.dumps({
                "labels": [], "value": [], "debt": [], "equity": [],
                "today_index": 0, "debt_tier": [],
            }),
            "chart_state": property_equity_chart.CHART_STATE_NO_LOANS,
            "has_estimated_debt": False,
        }
    chart = property_equity_chart.build_property_equity_chart(
        balance_at.secured_loan_series(property_account, balance_ctx),
        equity.market_value,
        params.annual_appreciation_rate,
        balance_ctx.as_of,
    )
    return {
        "has_equity_chart": True,
        "chart_json": json.dumps({
            "labels": chart.labels,
            "value": [float(value) for value in chart.value],
            "debt": [float(debt) for debt in chart.debt],
            "equity": [float(equity_pt) for equity_pt in chart.equity],
            "today_index": chart.today_index,
            "debt_tier": chart.debt_tier,
        }),
        "chart_state": chart.chart_state,
        "has_estimated_debt": (
            property_equity_chart.TIER_ESTIMATED in chart.debt_tier
        ),
    }


@accounts_bp.route("/accounts/<int:account_id>/property")
@require_owner
def property_detail(account_id):
    """Property detail page: market value, appreciation rate, equity, LTV.

    The durable home for the home-equity display this sprint (the savings
    cockpit equity card lands in the Net Worth Cockpit rebuild, reusing the
    same :mod:`app.services.home_equity_service` producer).  Equity nets the
    Property's user-set market value against the resolver-derived balances
    of the loans it secures, so the mortgage figure here equals the debt
    card and the net-worth liability.
    """
    account = get_or_404(Account, account_id)
    if account is None:
        abort(404)

    # Verify this is an appreciating physical-asset account type.
    if not account.account_type or not account.account_type.has_appreciation:
        flash("This account type does not track appreciation.", "warning")
        return redirect(url_for("savings.dashboard"))

    params = (
        db.session.query(AssetAppreciationParams)
        .filter_by(account_id=account.id)
        .first()
    )
    if params is None:
        # The appreciation twin of :func:`_interest_params`, deleted as an
        # auto-create by the same step and for the same reason: this is a
        # render, and a render that writes is a render without a snapshot.
        raise RequiredRecordMissing(
            f"account {account.id} is an appreciating asset and carries no "
            f"budget.asset_appreciation_params row, so there is no rate to "
            f"project its value at. Every door that can make an account "
            f"appreciating writes one -- creating it and re-classing it; "
            f"``has_appreciation`` is not editable on a type -- so reaching "
            f"this means the row was removed outside the application. To "
            f"rebuild it, change the account's type to another kind and change "
            f"it back: the re-class door seeds on a CHANGE, so re-saving the "
            f"same type is a no-op."
        )

    balance_ctx = BalanceContext.build(current_user.id)

    # Every secured loan is read from the read pass's ONE memoized resolution,
    # so the equity hero, the equity chart, and the /savings cockpit's equity
    # card all read the same mortgage balance -- this page used to resolve them
    # itself and hand the pure ``compute_home_equity`` its own figures, a second
    # equity path parallel to ``resolve_home_equity``.
    equity = home_equity_service.resolve_home_equity(account, balance_ctx)

    return render_template(
        "accounts/property_detail.html",
        account=account,
        params=params,
        equity=equity,
        secured_loans=account.secured_loans,
        **_property_chart_context(params, equity, account, balance_ctx),
    )


@accounts_bp.route("/accounts/<int:account_id>/property/params", methods=["POST"])
@require_owner
def update_appreciation_params(account_id):
    """Update a Property's annual appreciation rate."""
    account = get_or_404(Account, account_id)
    if account is None:
        abort(404)

    if not account.account_type or not account.account_type.has_appreciation:
        flash("This account type does not track appreciation.", "warning")
        return redirect(url_for("savings.dashboard"))

    errors = _appreciation_params_schema.validate(request.form)
    if errors:
        flash("Please correct the highlighted errors and try again.", "danger")
        return redirect(url_for("accounts.property_detail", account_id=account_id))

    data = _appreciation_params_schema.load(request.form)

    params = (
        db.session.query(AssetAppreciationParams)
        .filter_by(account_id=account.id)
        .first()
    )
    if params is None:
        params = AssetAppreciationParams(
            account_id=account.id,
            annual_appreciation_rate=data["appreciation_rate"],
        )
        db.session.add(params)
    else:
        params.annual_appreciation_rate = data["appreciation_rate"]

    db.session.commit()
    logger.info("Updated appreciation params for account %d", account.id)
    flash("Appreciation rate updated.", "success")
    return redirect(url_for("accounts.property_detail", account_id=account_id))
