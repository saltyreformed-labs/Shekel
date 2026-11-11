"""
Shekel Budget App -- Grid route package: what the page and its partials share.

What BOTH halves of this package share, and nothing else.  The budget grid
renders once as a full page (:mod:`~app.routes.grid.page`) and then re-renders
three fragments of itself on HTMX events (:mod:`~app.routes.grid.partials`) --
and the fragments must agree with the page they are replacing, figure for
figure.  These are the producers that make that agreement structural rather
than a claim two modules keep: :func:`_resolve_visible_window` is the ONE
answer to "which paychecks is this request looking at",
:func:`_build_grid_view` is the ONE seam call every surface reads its columns
from (ruling R-K), :func:`_accrual_row_label` is the ONE word all three
surfaces name the modelled-return row with (ruling R-AI / R-P), and
:func:`_resolve_low_balance_threshold` is the ONE cut-off the yellow balance
cells test against.

A helper used by exactly one half lives in that half instead; this module is
the intersection, not a junk drawer.
"""

from flask_login import current_user

from app.models.account import Account
from app.services import balance_at, cash_ledger
from app.services.account_projection import (
    AccountProjectionKind,
    classify_account,
)
from app.services.balance_at import BalanceContext
from app.services.cash_flow_set import CashFlowSet
from app.services.pay_calendar import DerivedPeriod, PeriodWindow


def _resolve_visible_window(
    ctx: BalanceContext, num_periods: int, start_offset: int,
) -> tuple[DerivedPeriod, PeriodWindow] | None:
    """Return the paycheck this request opens on and the columns it shows.

    **The one rule the page and its self-refresh fragments must not disagree
    about**, and the reason this module exists.  ``/grid`` renders a window;
    ``/grid/balance-row`` and ``/grid/subtotal-rows`` then recompute their
    summaries for *the same* window on a ``balanceChanged`` event.  If the two
    resolved it separately, a refresh could patch a footer that describes
    different paychecks from the columns above it -- and nothing on screen
    would say so, because both halves would render consistently within
    themselves.  Written twice, in both modules, until an adversarial design
    review of plan step **C2-f2b** pointed out that the step which created this
    module had extracted everything but the rule that needed it.

    Both period reads come off the pass's own calendar (that step), so a
    request resolves ONE derivation and reads the clock ONCE.

    Args:
        ctx: The read pass's :class:`~app.services.balance_at.BalanceContext`,
            which carries both the pinned ``as_of`` and the owner's calendar.
        num_periods: How many columns to show.  The CALLER supplies it, because
            the two differ legitimately: the page falls back to the owner's
            ``grid_default_periods`` setting and the fragments to a literal 6,
            the fragments' own URLs always carrying an explicit ``periods=``.
        start_offset: Columns to shift the window by, relative to the current
            paycheck.  A user-supplied query parameter, so a value below the
            schedule's start is ordinary; the window then comes back SHORT
            rather than re-based (:meth:`~app.services.pay_calendar.PayCalendar.window`).

    Returns:
        ``(current_period, window)``, or ``None`` when no SAVED paycheck covers
        the pass's ``as_of`` -- the state the page answers with
        ``no_periods.html`` and the fragments with ``204``.  Saved containment
        rather than :meth:`~app.services.pay_calendar.PayCalendar.span_containing`
        is deliberate: every column is keyed by the id a transaction points at,
        so a projected period past the horizon would anchor the grid on a
        paycheck no row can belong to.
    """
    calendar = ctx.calendar()
    current_period = calendar.period_containing(ctx.as_of)
    if current_period is None:
        return None
    # A VIEW over the whole calendar, so each column keeps the end the complete
    # payday set dictated rather than one re-derived from the periods on screen
    # (ledger row P14, $150,000.00 on the sibling shape).
    return current_period, calendar.window(
        current_period.period_index + start_offset, num_periods,
    )


# Whole-dollar threshold used to flag a projected balance as "low"
# (the dashed line on the dashboard chart, the yellow balance cells on
# the grid) when the requesting user has no ``UserSettings`` row at all.
# Mirrors ``UserSettings.low_balance_threshold``'s NOT NULL server
# default of 500: with a settings row the column always carries a
# concrete value, so this constant only covers the row-absent edge
# case (an owner whose registration-time settings row is somehow
# missing) -- never the previous "value is NULL" branch, which the
# column constraint now forbids.
_DEFAULT_LOW_BALANCE_THRESHOLD = 500


def _resolve_low_balance_threshold() -> int:
    """Return the current user's low-balance threshold for grid rendering.

    Reads ``current_user.settings.low_balance_threshold`` -- a NOT NULL
    integer column, so it always carries a value when a settings row
    exists.  Falls back to :data:`_DEFAULT_LOW_BALANCE_THRESHOLD` only
    when the user has no settings row at all, so the grid's
    ``bal < low_balance_threshold`` cell comparison always has a
    concrete integer to test against.  Shared by :func:`index` and
    :func:`balance_row`, which render the same threshold-aware balance
    cells.
    """
    settings = current_user.settings
    if settings is None:
        return _DEFAULT_LOW_BALANCE_THRESHOLD
    return settings.low_balance_threshold


def _build_grid_view(cash_flow: CashFlowSet | None, balance_ctx):
    """Compute the grid's per-period column set and its latest anchor assertion.

    Routes through the balance-at seam's kind-aware grid view
    :func:`app.services.balance_at.grid_balance_view`, which returns ONE
    :class:`~app.services.balance_at.GridColumn` per period carrying every
    figure the grid renders for it -- the projected end balance, the income /
    expense / net subtotals, ruling R-K's two remainders ("Period timing" and
    "Book vs bank", split at ruling R-DH (f)), and
    the two modelled tiers (the contribution and the accrual, rendered as their
    own conditional rows and labelled per kind by :func:`_accrual_row_label`).

    **Every figure is the set's BALANCE account's** (developer ruling
    plan#2926; plan task plan#2932): the balance, the anchor and the
    subtotals alike, so Total Expenses is what leaves that account and a bill
    on a card counts once, through the card's payment.  The set is what every
    caller holds, so it is what this takes; its other members decide which
    ROWS the grid draws (:func:`~app.routes.grid._items.load_grid_items`), and
    none of them reaches the seam.  From plan step CC-4-1 the seam took the
    set and summed every member's rows into the subtotals beside an "On other
    accounts" term, which counted a purchase on the card twice once the card's
    payment paid it off from checking.

    **One producer pass, not three** (plan step X-c2b1, finding N-48).  The
    route used to call the balance producer once and the subtotal producer
    twice (the visible window and the Plan window), building the live override
    map itself and threading it into all three -- and the invariant binding the
    balance row to the subtotal row was then a claim two producers had to keep
    true rather than a property of one row set.  Asking the seam once for the
    whole anchor-forward set and SLICING it per window is both cheaper and the
    shape ruling R-K's identity needs: measured on the prod-shape clone
    2026-07-26 (real Checking, 60 periods, 5 runs) the full render's producer
    work drops ``219.9 ms -> 127.6 ms``, the whole saving being the second
    override build this route no longer does.

    The grid keeps its own ``all_transactions`` query (in
    :func:`~app.routes.grid._items.load_grid_items`) for display purposes: the route needs the
    ``template`` eager-load for row-key generation and the same entries for
    ``entry_sums`` / cell rendering, neither of which is in the producer's
    remit.  It no longer builds its own live override map -- the view carries
    the one the projection was computed with (ruling R-Q), so a cell and the
    balance row cannot price the same row differently.

    Args:
        cash_flow: The owner's :class:`~app.services.cash_flow_set.CashFlowSet`
            (:func:`~app.services.account_resolver.resolve_cash_flow_set`), or
            ``None`` for the user-with-zero-accounts edge case.
        balance_ctx: The read pass's
            :class:`~app.services.balance_at.BalanceContext`.  It carries the
            projection domain too since plan step C2-c: the seam reads the
            owner's whole pay calendar off it
            (``reported_periods()``) rather than taking a list of ORM rows,
            whose ``end_date`` and ``period_index`` are the two derived columns
            plan step C4-c dropped.  Every period is answered -- there is no
            anchor-forward restriction to respect since plan step X-c2b2 --
            and each is valued off its OWN span, so each render window is a
            slice of the result rather than a re-based projection.

    Returns:
        ``(grid_view, anchor)`` -- the
        :class:`~app.services.balance_at.GridBalanceView` and the account's
        latest balance ASSERTION (a separate concern: the header's starting
        figure and the day it was true for, not part of the projection).
        No-account state returns the seam's empty view and ``None``, and the
        grid template renders empty cells cleanly.  **The header's figure and
        its "as of" caption come off ONE object** (rulings R-EH / R-EP): they
        were ``current_anchor_balance`` and ``updated_at``, two different facts
        -- the caption moved on any account edit and stopped moving entirely
        once a true-up no longer wrote the row.  An AnchorPoint cannot split.
    """
    if cash_flow is None:
        return balance_at.empty_grid_view(), None
    return (
        balance_at.grid_balance_view(cash_flow.balance, balance_ctx),
        cash_ledger.resolve_anchor(cash_flow.balance),
    )


# The word each account kind uses for the return it models, for the grid's
# conditional accrual row (ruling R-AI).  It is not a new vocabulary: the app
# already speaks all three, each on that kind's own page -- "Interest, next
# 12 mo" (``accounts/_cash_band.html``), "Growth since Jun 23"
# (``investment/dashboard.html``) and "appreciation" / "at 3.0%/yr"
# (``accounts/property_detail.html``).  Those are PHRASES with their own
# windows baked in rather than instances of one string, so this map is the
# canonical source for the GRID's row and not a fourth copy of any of them.
#
# It is TOTAL over ``AccountProjectionKind`` and subscripted, never ``.get``
# with a default: a kind added to the enum without a word here must fail at the
# render rather than label a new kind silently and wrongly, which is the same
# reason this codebase refuses a bare ``except``.  PLAIN and AMORTIZING can
# never reach the row -- neither resolves an ACCRUAL tier, so
# ``row_flags.accrual`` is False for both -- but they are members because the
# map is a function of the enum, not of what happens to render today.
#
# The two unreachable kinds still carry the word their row WOULD honestly use,
# because a knowingly-wrong entry is the very thing the subscript above exists
# to prevent.  **AMORTIZING therefore reads "Interest", not the generic word:**
# a loan's accrual is interest CHARGED, and naming a liability's accrual after
# an asset's growth reads as the opposite of what it is.  PLAIN models no
# return at all, so no word is truthful for it and it carries the kind-neutral
# one -- which is also what a ``None`` account resolves to.
#
# The placeholder is its OWN constant even though it spells the same word an
# INVESTMENT's row carries.  Those are two decisions, not one: "Growth" is the
# vocabulary ruling R-AI chose for a 401(k), to match what ``/investment``
# already renders.  Binding them together would let a later edit to the
# placeholder silently rename the row the ruling named.
_UNMODELLED_ACCRUAL_LABEL = "Growth"

_ACCRUAL_ROW_LABELS = {
    AccountProjectionKind.INTEREST: "Interest",
    AccountProjectionKind.INVESTMENT: "Growth",
    AccountProjectionKind.APPRECIATING: "Appreciation",
    AccountProjectionKind.AMORTIZING: "Interest",
    AccountProjectionKind.PLAIN: _UNMODELLED_ACCRUAL_LABEL,
}


def _accrual_row_label(account: Account | None) -> str:
    """Return the label the grid's modelled-return row carries for *account*.

    Ruling R-AI: the row is "Interest" on an HYSA, "Growth" on a 401(k) and
    "Appreciation" on a house.  Resolved in the presentation layer because
    :mod:`app.services.balance_at._grid` carries no display strings -- its
    ``GridRowFlags`` earns its place in the seam by carrying no money and
    deciding no figure, and a label decides no figure but is not a balance rule
    either, so it belongs where the render entries already build their context.

    **It is TOTAL over ``Account | None``, not a three-entry subscript.**
    :func:`~app.services.account_projection.classify_account` returns FIVE
    values and :func:`_build_grid_view` legitimately carries ``account=None``
    for the zero-accounts user, so a three-key lookup would ``KeyError`` on the
    PLAIN checking account every default ``/grid`` render resolves to, and
    ``classify_account(None)`` would ``AttributeError`` beside it.

    Args:
        account: The grid account, or ``None`` for the user-with-zero-accounts
            edge case (which has no columns, so no row ever renders).

    Returns:
        The row's label.
    """
    if account is None:
        return _UNMODELLED_ACCRUAL_LABEL
    return _ACCRUAL_ROW_LABELS[classify_account(account)]
