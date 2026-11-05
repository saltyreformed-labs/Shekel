"""Balance-at-T seam -- the CASH-FLOW view (no per-kind dispatch).

The single-account cash-flow surfaces -- the budget grid, the dashboard pulse,
the analytics calendar, the cash detail page -- read an account's pure
transaction running-balance, NOT its kind-correct balance (see the package
docstring's "Two views, one seam").  These three entries are the seam's only
way to obtain that view.

**All three are one fold read at three grains** (plan step X-c2b2).  A period
map, a scalar at a date, and a day-by-day series are the SAME running total
(:mod:`app.services.balance_at._cash_fold`) sampled at period ends, at one
date, and at every date -- so they cannot disagree.  Before the cutover they
were three producers: the map carried an anchor forward over still-Projected
rows only, the scalar re-walked to a date with its own entry-date window, and
the daily series distributed the same rows over days -- and on the real
Checking account the scalar and the series stood ``$15.96`` apart on the day
before this commit (``$246.36`` at the worst day of the current period, finding
cash D2), while both dropped every row settled after the last balance assertion
(``$2,108.15`` invisible at that instant, finding cash D1) and answered a
pre-anchor date by fabricating today's balance or omitting the period entirely
(finding cash D3 / B-18).  One total fold subsumes all three.
"""

from collections import OrderedDict
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from decimal import Decimal

from app import ref_cache
from app.enums import AccountOpeningSourceEnum
from app.models.account import Account
from app.services.account_projection import (
    AccountProjectionKind,
    classify_account,
)
from app.services.cash_ledger import CashOpeningFact
from app.utils.dates import days_in_range, to_display_date
from app.utils.money import round_money

from ._context import BalanceContext

from . import _cash_fold
from ._inputs import _require_scenario

_ONE_DAY = timedelta(days=1)


def _require_civil_date(entry: str, **dates: object) -> None:
    """Refuse anything that is not exactly a civil :class:`datetime.date`.

    **A ``datetime`` is refused, and that is the whole reason this is a
    function.**  ``datetime`` SUBCLASSES ``date``, so the obvious
    ``isinstance(value, date)`` accepts one -- the guard reads like a type
    check and is not one.  The fold's step boundaries are civil dates, so a
    ``datetime`` reaching them dies inside ``bisect_right`` with
    ``'<' not supported between instances of 'datetime.datetime' and
    'datetime.date'``: a real failure, but one whose traceback names a
    bisect rather than the caller's argument, three layers from the mistake.

    It is expressed as "a date that is NOT a datetime" rather than an exact
    type test, because the suite's ``freeze_today`` clock hands the producers
    its own ``date`` SUBCLASS -- a legitimate civil date that an exact test
    would reject, turning a guard against one wrong type into a guard against
    every subclass.

    Saying the type this precisely also states the contract these entries
    hold: a balance is asked for on a DAY.  An instant is the walk's concern
    (the assertion partition), never a valuation date's.

    Args:
        entry: The seam entry's name, for the message.
        **dates: The argument name -> value pairs to check, in order.

    Raises:
        TypeError: On the first value that is not exactly a ``date``.
    """
    for label, value in dates.items():
        if not isinstance(value, date) or isinstance(value, datetime):
            raise TypeError(
                f"{entry} expects a civil datetime.date for {label}, "
                f"got {type(value).__name__} {value!r}"
            )


def cash_balance_map(
    account: Account, ctx: BalanceContext,
) -> "OrderedDict[int, Decimal]":
    """Return one account's cash-flow running balance per pay period.

    The cash-flow view: the account's projected end balance per period as a
    pure transaction running-balance, with NO per-kind dispatch.  This is what
    the single-account cash-flow surfaces show -- the dashboard pulse chart and
    the cash detail page -- where the balance row must reconcile with the
    account's own transaction rows and subtotal row on the same screen.

    **The budget grid was on this list until plan step X-g3b and is not any
    more** (ruling R-W): it reads :func:`~app.services.balance_at.grid_balance_view`,
    which answers a modelled account its MODELLED balance.  A reader that wants
    "what does the grid show" must call that entry -- for a modelled kind the
    two now answer differently by design, and this one would look right while
    proving nothing.

    Contrast with :func:`~app.services.balance_at.balance_map`, the
    KIND-CORRECT view: for an interest-bearing (HYSA), loan, investment, or
    property account ``balance_map`` dispatches to that kind's engine (accruing
    interest, walking an amortization schedule, compounding growth /
    appreciation) -- which is what the net-worth surfaces want, and what a
    cash-flow surface can only carry if the modelled movement is EXPLAINED on
    screen beside it.  The reason this entry once gave -- "accruing interest
    into the grid's balance row while its subtotal row stays transaction-based
    would leave a balance change the rows on screen cannot explain" -- was
    answered rather than overruled: ruling R-K put the explaining rows there
    (the accrual and the contribution), so the grid moved to the modelled
    balance at plan step X-g3b.  The surfaces still on THIS entry have no such
    rows, so they ask for the cash-flow balance of whatever account they are
    pointed at, regardless of its kind.

    **The one kind they are never pointed at is AMORTIZING, and that is a
    gate rather than a coincidence.**  A loan's balance is not a
    transaction sum (finding B-3), so every resolver feeding these entries
    refuses one at the source: ``resolve_grid_account`` since ruling D4 /
    plan step A1 (grid, dashboard, pulse), the analytics resolver
    (``resolve_analytics_cash_flow_set``; ``resolve_analytics_account`` until
    plan step CC-4-3) since plan step X-a1 (the calendar -- finding N-38), and the cash
    detail page's own ``_cash_page.cash_detail_wrong_type`` 404.  These producers
    therefore stay TOTAL and kind-blind by design, and no screen can ask
    them a question only ``balance_at.balance_at`` can answer.

    **EVERY requested period is in the result** (plan step X-c2b2).  The
    retired producer projected forward from the anchor and omitted every
    pre-anchor period, so a caller had to treat a missing key as "no balance";
    the fold replays every assertion, so a past period answers with the balance
    in force THEN.  Callers that skipped missing keys are unaffected -- there
    are none left to skip.

    Args:
        account: The account whose cash-flow balance to project.  Its
            ``user_id`` scopes the live salary override; its kind is NOT
            consulted (ruling R-J).
        ctx: The read pass's :class:`~app.services.balance_at.BalanceContext`.
            Its ``as_of`` is the reader's NOW -- what decides a still-projected
            row cannot already have happened (ruling R-G) -- NOT a valuation
            date; each period is valued at its own ``end_date``.  **It is also
            where the periods come from** (plan step C2-c): the domain is
            :meth:`~app.services.balance_at.BalanceContext.reported_periods`,
            the owner's whole saved calendar, and it is no longer an argument
            -- every caller passed exactly that, so the only thing the argument
            could express was a mistake.

    Returns:
        ``OrderedDict`` period_id -> cent-quantized ``Decimal``, in payday
        order.  Empty for an owner with no pay periods.

    Raises:
        BaselineMissingError: When ``scenario`` is None.  A ``ValueError``
            subclass; ONE application-level handler answers it (plan step
            X-v2, ruling R-BW), so no caller pre-checks.
        PayCalendarError: The owner's paydays cannot define a calendar, which
            since plan step C2-c is reachable from every per-period seam entry
            rather than only from the recurrence pages -- see
            :meth:`~app.services.balance_at.BalanceContext.calendar`, where the
            reporting domain is derived, for the one state that produces it and
            the step that removes it.
    """
    _require_scenario(ctx)
    return _cash_fold.period_balances(
        _cash_fold.assembled_fold(account, ctx), ctx.reported_periods(),
    )


def cash_balance_at(
    account: Account, ctx: BalanceContext, as_of: date,
) -> Decimal:
    """Return one account's cash-flow balance as of a calendar date *as_of*.

    The scalar cash-flow view -- the date-precise counterpart of
    :func:`cash_balance_map`, and literally the same fold read at one date, so
    ``cash_balance_at(account, ctx, P.end_date)`` equals
    ``cash_balance_map(account, ctx)[P.id]`` whenever *P*'s stored
    ``end_date`` is the one its owner's paydays derive -- which plan step C2-c
    made the qualifier it is.  It read "by construction" until then, and the
    map now samples the DERIVED end while a caller reading ``P.end_date`` off
    an ORM row supplies the stored one; plan step C4-c dropped that column and
    makes the sentence unconditional again.  Measured 0 of 62 and 0 of 61
    disagreements on the two production-shaped databases.

    Used by the calendar's month-end balance, which must reconcile with the day
    cells it renders for the same month.

    Like :func:`cash_balance_map`, this does NOT dispatch by kind: it is
    the cash-flow balance of whatever account the surface points at (the
    calendar's account can be any kind via an explicit ``account_id``).
    The KIND-CORRECT scalar is :func:`~app.services.balance_at.balance_at`.

    **Two dates, deliberately distinct.**  ``ctx.as_of`` is the reader's NOW --
    the floor ruling R-G clamps a still-projected row's landing day up to --
    while *as_of* is the VALUATION date, which may be long past (a historical
    read) or far future (a projection).  A past valuation date now answers with
    the balance the account really held then, replayed from its assertions,
    rather than with today's balance fabricated backwards (finding B-18).

    Args:
        account: The account to value.  Its kind is NOT consulted.
        ctx: The read pass's :class:`~app.services.balance_at.BalanceContext`
            (its scenario scopes the fold; its ``as_of`` is the reader's NOW).
        as_of: The calendar date to value the account at.

    Returns:
        The cent-quantized ``Decimal`` cash-flow balance at *as_of*.

    Raises:
        BaselineMissingError: When ``scenario`` is None.  A ``ValueError``
            subclass; ONE application-level handler answers it (plan step
            X-v2, ruling R-BW), so no caller pre-checks.
        TypeError: When ``as_of`` is not a civil :class:`datetime.date` -- a
            ``datetime`` INCLUDED (see :func:`_require_civil_date`).
    """
    _require_scenario(ctx)
    _require_civil_date("cash_balance_at", as_of=as_of)
    return _cash_fold.balances_at(_cash_fold.assembled_fold(account, ctx), [as_of])[as_of]


def cash_daily_balance_series(
    account: Account,
    ctx: BalanceContext,
    first_day: date,
    last_day: date,
) -> "OrderedDict[date, Decimal]":
    """Return one account's projected end-of-day cash-flow balance per day.

    The daily-granularity cash-flow view -- the same fold as
    :func:`cash_balance_at`, sampled at every day of the range instead of one,
    which is what makes the calendar's running-balance line reconcile with the
    other CASH-basis surfaces at every period end (the grid left that set at
    plan step X-g3b, and the calendar can be pointed at a modelled account by
    explicit ``account_id`` -- finding N-87 records the divergence)::

        series[P.end_date] == cash_balance_at(account, ctx, P.end_date)

    That identity used to be a claim two producers had to keep true (the series
    distributed a period's still-Projected rows over their attribution days
    while the scalar re-walked to the date through a different entry-date
    window, and they measured ``$15.96`` apart on the real Checking account);
    it is now a property of reading one running total twice.

    Like :func:`cash_balance_at` this does NOT dispatch by kind: it is the
    cash-flow balance of whatever account the surface points at (the
    calendar's account can be any kind via an explicit ``account_id``).  Used
    by the analytics calendar's flow strip and day-cell end-of-day balances.

    Args:
        account: The account to project.  Its kind is NOT consulted; must be
            session-attached.
        ctx: The read pass's :class:`~app.services.balance_at.BalanceContext`
            (its scenario scopes the fold; its ``as_of`` is the reader's NOW).
        first_day: Inclusive first calendar day of the range.
        last_day: Inclusive last calendar day of the range.

    Returns:
        An ``OrderedDict`` mapping each calendar ``date`` in the inclusive
        range (ascending) to its projected end-of-day cash-flow balance,
        quantized to cents.  An inverted range yields an empty map.

    Raises:
        BaselineMissingError: When ``scenario`` is None.  A ``ValueError``
            subclass; ONE application-level handler answers it (plan step
            X-v2, ruling R-BW), so no caller pre-checks.
        TypeError: When ``first_day`` / ``last_day`` are not civil
            :class:`datetime.date` values -- a ``datetime`` INCLUDED (see
            :func:`_require_civil_date`).
    """
    _require_scenario(ctx)
    _require_civil_date(
        "cash_daily_balance_series", first_day=first_day, last_day=last_day,
    )
    if last_day < first_day:
        return OrderedDict()

    days = days_in_range(first_day, last_day)

    folded = _cash_fold.balances_at(_cash_fold.assembled_fold(account, ctx), days)
    return OrderedDict((day, folded[day]) for day in days)


def cash_daily_facts_series(
    account: Account,
    ctx: BalanceContext,
    first_day: date,
    last_day: date,
) -> "_cash_fold.CashDaySeries":
    """Return each day's cash-flow balance beside the three tiers that moved it.

    :func:`cash_daily_balance_series` with the day's MOVEMENT split out, for a
    reader comparing the account against a record kept OUTSIDE the app -- the
    bank's own statement (plan step ``bank_import:X-f6e-2``).  Such a reader
    needs the split because a balance difference alone cannot tell the three
    apart, and they mean different things: money the app's rows say moved, a
    balance the owner ASSERTED, and a plan that has not happened.

    **Measured, which is why it exists rather than a difference column**: on
    the developer's own Checking account, over the 149 days his statement and
    his records both cover, 35 days carry a real disagreement between his rows
    and the bank's lines -- and 11 of those read as EXACT agreement in the
    balance difference, because a same-day assertion cancels the error to the
    cent.  One of the eleven is the ``$943.41`` of card paybacks finding
    **N-337** names.

    ``balance`` here is the same figure :func:`cash_daily_balance_series`
    reports for the same day, off the same running total, so a surface may show
    both without qualifying either.

    Args:
        account: The account to project.  Its kind is NOT consulted (see
            :func:`cash_daily_balance_series`); must be session-attached.
        ctx: The read pass's :class:`~app.services.balance_at.BalanceContext`.
        first_day: Inclusive first calendar day of the range.
        last_day: Inclusive last calendar day of the range.

    Returns:
        The :class:`~app.services.balance_at.CashDaySeries`, whose ``facts`` is
        an ``OrderedDict`` mapping each calendar ``date`` in the inclusive
        range (ascending) to its
        :class:`~app.services.balance_at.CashDayFacts`, and whose
        ``opened_on`` is the day this account's books opened.  An inverted
        range yields an empty map and that same day.

    Raises:
        BaselineMissingError: When ``ctx`` carries no scenario.
        TypeError: When ``first_day`` / ``last_day`` are not civil
            :class:`datetime.date` values -- a ``datetime`` INCLUDED.
    """
    _require_scenario(ctx)
    _require_civil_date(
        "cash_daily_facts_series", first_day=first_day, last_day=last_day,
    )
    days = days_in_range(first_day, last_day)

    series = _cash_fold.day_facts(_cash_fold.assembled_fold(account, ctx), days)
    # Re-keyed into range order, which a dict comprehension over an unordered
    # sample list does not promise.  An inverted range yields no days and still
    # reports the day the books opened -- the account HAS an opening whether or
    # not the caller asked about a day it falls in.
    return _cash_fold.CashDaySeries(
        facts=OrderedDict((day, series.facts[day]) for day in days),
        opened_on=series.opened_on,
    )


def cash_last_record_on(
    account: Account, ctx: BalanceContext,
) -> "date | None":
    """Return the latest day, on or before the reader's NOW, the books hold a record.

    A RECORD is a settled source fact (a row or a transfer leg marked done, a
    purchase dated against one) or a balance assertion (a true-up), read by
    its own day and never by what it moved.  A source fact always moves money
    (a movement is never ``$0.00``, ``ck_transaction_entries_positive_amount``,
    and a transfer closed at ``$0.00`` records no side); a true-up may move
    none, and one agreeing with the records to the cent is still a record,
    because it states a balance for its day.  Ruling plan#2400 put the
    true-up in: it speaks for the books on a day a statement must price
    before the Reconcile page may read Done (ruling plan#2346, finding
    **BAL-651**).  :func:`app.services.bank_agreement.bank_agreement`
    draws its comparison through this day, so the days after the last
    statement line that the books describe are shown and counted unpriced
    rather than never drawn (ruling plan#2399).

    **A reading of the pass's one assembled fold**, as
    :func:`records_balance_at` is: the walk already holds both kinds of fact,
    so this issues no query of its own.

    Args:
        account: The account to read.  Caller owns the ownership check.
        ctx: The read pass's :class:`~app.services.balance_at.BalanceContext`,
            whose ``as_of`` bounds the answer: a fact dated after NOW is not a
            record yet.

    Returns:
        The day, or ``None`` when the account's books hold no record on or
        before NOW.

    Raises:
        BaselineMissingError: When ``ctx`` carries no scenario.
    """
    _require_scenario(ctx)
    walk = _cash_fold.assembled_fold(account, ctx).walk
    days = [fact.settled_on for fact in walk.source_facts]
    days.extend(fact.observed_on for fact in walk.anchor_facts)
    return max((day for day in days if day <= ctx.as_of), default=None)


def records_balance_at(
    account: Account, ctx: BalanceContext, as_of: date,
) -> "Decimal | None":
    """Return what an account's RECORDS produce at *as_of*, before its own
    assertion for that day overrides them.

    The figure ruling **R-EU** asks the true-up form for (plan step X-f2-a):
    *"Shekel has $X"*, where X is what the recorded transactions add up to --
    NOT what the app currently reports, which after a balance is recorded for a
    day IS that recorded balance.

    **The distinction exists because the fold applies an assertion as a RESET**
    (``balance_at._assertions``: ``running = anchor.anchor_balance``, merged
    into the step list by ``_cash_fold._actual_steps`` for EVERY account kind
    today -- plan step X-f3c-5 is what stops merging it for the PLAIN ones).
    So
    :func:`cash_balance_at` on a day that already carries an assertion answers
    with that assertion, and a difference measured against it is zero by
    construction -- or worse, on a CORRECTION it is the gap between the user's
    two successive guesses, which can carry the opposite sign to the real one.
    Measured on production Checking, 2026-04-15, where three balances were
    recorded in one day with no transaction between them: against the previous
    entry the third reads ``-$45.86``; against the records it reads
    ``-$92.29``.

    **It is the replay's OWN field, not arithmetic over the fold.**
    :attr:`~._assertions.CashAnchorCorrection.balance_before` is
    documented as the running balance *just before this assertion resets it*,
    which is exactly this question, already public and already the figure the
    LOAN side's drift card renders (``loan_posting_service._display``:
    ``computed``).  Subtracting correction deltas out of
    :func:`cash_balance_at` instead would be a second statement of the fold's
    step rule, and this field is the rule's own output.  *It was also WRONG
    until plan step X-f3c-2a, for a reason that has since been deleted: the
    fold moved the OPENING correction into its seed and booked a compensator
    for it, so the opening's delta was not a step anyone could subtract.*

    Two branches, and they are one rule rather than a special case:

    * an assertion is dated *as_of* -- take the FIRST one's ``balance_before``.
      First, not last: a later assertion on the same day carries the earlier
      one's reset inside it, so only the first sees the records alone.
    * no assertion is dated *as_of* -- there is nothing to see past, and
      :func:`cash_balance_at` already IS the records' balance.

    **``None`` is a SCOPE statement, not a failure.**  An account whose balance
    carries a MODELLED tier -- an HYSA accruing interest, a brokerage
    compounding, a Property appreciating -- is not answering "does my bank agree
    with my book".  Its recorded cash is a fraction of what its screens show, so
    a reconciliation against it would caption a model-vs-market difference as
    untracked spend.  Production bears the split out: 57 of the 78 non-loan
    assertions are on Checking, the one account carrying no modelled tier.
    Widening this to the modelled kinds is a different question with different
    copy and wants its own ruling (finding **N-213**).

    Args:
        account: The account to value.  Caller owns the ownership check.
        ctx: The read pass's :class:`~app.services.balance_at.BalanceContext`.
        as_of: The civil day to answer for.

    Returns:
        The cent-quantized records-only ``Decimal``, or ``None`` when the
        account models a return and the question does not apply.

    Raises:
        BaselineMissingError: When ``ctx`` carries no scenario.
        TypeError: When *as_of* is not a civil :class:`datetime.date`.
    """
    _require_scenario(ctx)
    _require_civil_date("records_balance_at", as_of=as_of)
    if classify_account(account) is not AccountProjectionKind.PLAIN:
        # The app's OWN name for "no modelled tier rides on this balance"
        # (``account_projection``, whose ladder every seam producer already
        # dispatches on).  Asked through the classifier rather than by
        # re-testing ``has_interest`` / ``has_appreciation`` / an
        # ``InvestmentParams`` row, which would be a fourth statement of a
        # precedence this codebase has already been bitten by stating twice
        # (S6-03).
        return None
    for correction in _cash_fold.assembled_fold(account, ctx).corrections:
        if correction.observed_on == as_of:
            return correction.balance_before
    return cash_balance_at(account, ctx, as_of)


@dataclass(frozen=True)
class CashAnchorRow:
    """One recorded balance, beside what the ledger held just before it.

    The display row of :func:`cash_anchor_history`, and the cash twin of
    :class:`app.services.loan_posting_service.LoanAnchorDrift`.

    **:attr:`ledger` and :attr:`correction` are OPTIONAL, and their absence is
    a statement rather than a gap.**  Both are ``None`` exactly where the pair
    has no agreed meaning, which since plan step **X-f3c-2a** is ONE case:

    * **an account whose balance carries a MODELLED tier.**  The walk sees
      recorded CASH only; an HYSA's accrued interest and a brokerage's growth
      never were transactions, so the figure would name a model-vs-market gap
      as untracked spend.  Measured on production: the HYSA's one row would
      read ``$4,863.56`` against a ``$5,363.56`` balance (91 percent of it),
      and the Money Market's two true-ups ``$15.01`` / ``$15.24``, which are
      interest.  This is the scope :func:`records_balance_at` already states
      for the X-f2-a preview, and widening it is finding **N-213**.

    Attributes:
        observed_on: The civil day this balance was declared TRUE for
            (``AccountAnchorHistory.observed_on``) -- the day the whole walk
            partitions on.
        recorded_at: The RECORDING instant, aware-UTC
            (:attr:`~app.services.cash_ledger.CashAnchorFact.asserted_at`
            unchanged).  It is what a reader RANKS by, and
            :attr:`recorded_on` is what a reader SHOWS -- two jobs a single
            field cannot do, which a first build of this row got wrong.  A day
            has no resolution to rank with: every assertion typed in one
            sitting shares one recording DAY, so "the most recently recorded
            twelve" degenerated to "the twelve newest ``observed_on``" and
            buried the back-dated row the pair exists to surface.  That is not
            a rare tie -- it is what a bookkeeping session looks like.
        recorded_on: The civil day this assertion was ENTERED, in the user's
            zone.  **A stored fact carried through from
            :attr:`~app.services.cash_ledger.CashAnchorFact.recorded_on`, not
            derived from :attr:`recorded_at`** (finding **N-299**, developer
            ruling 2026-08-25).

            It exists so a BACK-DATED row identifies itself.  Sorted by
            :attr:`observed_on` a balance recorded today for a past day lands
            at that past day's position -- 40 rows down on the real Checking
            account -- so ordering alone does not give a back-dated write the
            retrieval path finding **N-205** is about.  It equals
            :attr:`observed_on` for the ordinary same-day true-up, which is why
            the card shows the caption only when the two differ.

            **Splitting rank from show was right and stopped one step short.**
            Both halves were still computed from ``created_at``, which
            PostgreSQL stamps (``server_default=db.func.now()``), while
            :attr:`observed_on` comes from the application's
            ``display_today()``.  So the caption gated on
            ``recorded_on != observed_on`` compared two clocks: red on every
            calendar-sweep matrix date since 2026-08-10, because
            ``time_machine`` fakes one of them and cannot fake the other, and
            wrong in production for a true-up submitted in a civil day's last
            second.  The shown value now has its own application-written
            column and :attr:`recorded_at` keeps the ranking job alone.
        recorded: The balance the user asserted, cent-quantized.  A fact for
            every account kind, which is why it is never ``None``.
        ledger: What the running balance held immediately before this assertion
            RESET it
            (:attr:`~._assertions.CashAnchorCorrection.balance_before`),
            or ``None`` in the two cases above.
        correction: ``recorded - ledger``
            (:attr:`~._assertions.CashAnchorCorrection.delta`) --
            the jump THIS assertion booked, or ``None`` alongside
            :attr:`ledger`.

            **It is deliberately NOT the difference the true-up form previews,
            and the name is what keeps them apart.**  On a day carrying more
            than one assertion the two are different numbers for one row:
            :func:`records_balance_at` answers what the RECORDS produced for
            the day, taking the FIRST assertion's ``balance_before``, while
            this is measured against whatever the running balance held just
            before -- which for a second or third entry is the previous ENTRY.
            Measured on production Checking 2026-04-15, where three balances
            were recorded with no transaction between them: the third's
            correction is ``-$45.86`` and the preview's difference for the same
            row is ``-$92.29``.  Both are right about their own question, and
            one word covering both is the defect: a review of this step found
            the card and the form publishing those two figures under the label
            "Difference" on one screen.

            This is the LEDGER's arithmetic and the row shows its whole
            working: ``recorded - ledger`` reconciles on the row, and a day's
            corrections telescope to the day's total (``-7.46 + -38.97 +
            -45.86 = -92.29``, the gap the preview names).
    **It carried an ``is_opening`` flag until plan step X-f3c-2b and no longer
    does.**  The flag badged whichever assertion fell on the day
    ``budget.account_openings`` said the books opened -- which was true only
    while the two shared a day.  Ruling **R-HG** made the opening equity the
    CLOSING balance for ``opened_on``, so no movement and no assertion of the
    records' own era can share that day on an account whose books were
    back-dated to reach its earliest record: the badge went from 9 of 9
    production accounts to 4, measured.  An opening is not an assertion, and
    the card renders it as :class:`CashOpeningRow` instead -- the fact itself,
    with its own provenance, rather than a label borrowed by a neighbouring
    row.
    """

    observed_on: date
    recorded_at: datetime
    recorded_on: date
    recorded: Decimal
    ledger: "Decimal | None"
    correction: "Decimal | None"


@dataclass(frozen=True)
class CashOpeningRow:
    """What an account's books OPENED with, as the history card's foundation row.

    The :class:`~app.models.account_opening.AccountOpening` record in force,
    rendered as the oldest row of the balance-history card (plan step
    **X-f3c-2b**).  It is not an assertion and is deliberately not one of
    :attr:`CashAnchorHistory.rows`: nobody told the account it held this, the
    books simply opened there, and every balance above it is this figure plus
    what the records say happened since.

    **Its ledger / correction cells are empty by construction rather than by
    suppression**, which is the distinction plan step X-f3c-2a drew and this
    keeps: there is nothing before an opening for a correction to be measured
    against, where an assertion always has the records that preceded it.

    Attributes:
        opened_on: The civil day the books opened -- the day this equity was
            the whole of what the account held, at its CLOSE (ruling
            **R-HG**).
        recorded_on: The civil day the record itself was written, from its
            ``created_at``.  It equals :attr:`opened_on` for an account whose
            owner declared its opening at creation and differs for one whose
            books were restated later, which is the same "entered" caption the
            assertion rows carry and it means the same thing.
        equity: The capital the books opened with, cent-quantized and
            LEDGER-NATIVE -- negative for a liability, exactly as an
            assertion's own figure is.
        declared: ``True`` when a human stated this figure
            (:attr:`~app.enums.AccountOpeningSourceEnum.USER_DECLARED`),
            ``False`` when the app computed it.  The card says which, because
            a derived opening is the pre-X-f3c-2a inference frozen and may be
            WRONG -- findings **N-275** and **N-379** measure two of the seven
            production figures wrong against the owner's own bank -- and a
            surface that cannot tell them apart presents a guess and a fact
            identically.
    """

    opened_on: date
    recorded_on: date
    equity: Decimal
    declared: bool


@dataclass(frozen=True)
class CashAnchorHistory:
    """An account's balance-assertion log, as the history card renders it.

    Attributes:
        rows: One :class:`CashAnchorRow` per assertion the account carries,
            NEWEST first.  The producer's order is the reverse of the walk's,
            which is the order the card reads in; it is not the order the
            figures were computed in, and it cannot change them (each row's
            ``ledger`` is the running total of the events BEFORE it).
        opening: The account's :class:`CashOpeningRow` -- the foundation every
            row above rests on.  **Always present**, and separate from
            :attr:`rows` because it is a different kind of fact: a balance the
            owner was told, versus the level the books started at.  Not
            optional, because the fold cannot answer at all without it
            (:func:`app.services.cash_ledger.account_opening_fact` raises), so
            a caller holding a history holds an opening.
        reconcilable: ``True`` when this account's balance is recorded cash
            alone, so the :attr:`CashAnchorRow.ledger` /
            :attr:`~CashAnchorRow.correction` pair means something and the card
            shows those columns.

            **It cannot be derived from the rows, and that is a trap worth
            naming.**  ``any(row.correction is not None)`` looks equivalent and
            is wrong for a PLAIN account whose every assertion agrees with its
            books: every ``correction`` is then ``0.00`` -- not ``None``, but a
            derived flag written as ``any(row.correction)`` would hide the
            columns on the healthiest account there is.  A PLAIN account
            carrying exactly ONE assertion was the older statement of this trap
            and it was sharper still: until plan step X-f3c-2a that single row
            was the OPENING, whose pair was suppressed outright, so the derived
            flag hid the columns on an account that reconciles perfectly well.
            It is read from
            :func:`~app.services.account_projection.classify_account`, stated
            once, here.
    """

    rows: "list[CashAnchorRow]"
    opening: CashOpeningRow
    reconcilable: bool


#: Which opening-equity provenances count as a HUMAN saying so, for the
#: history card's caption.  A map over the whole enum rather than a positive
#: test, so a member added later cannot default to "derived" unnoticed -- the
#: lookup raises instead.  The question the card asks is the one
#: :class:`~app.enums.AccountOpeningSourceEnum`'s own docstring poses: did a
#: human state this figure, or did the app compute it?
_OPENING_WAS_DECLARED: "dict[AccountOpeningSourceEnum, bool]" = {
    AccountOpeningSourceEnum.USER_DECLARED: True,
    AccountOpeningSourceEnum.MIGRATION_DERIVED: False,
}


def _opening_row(opening: CashOpeningFact) -> CashOpeningRow:
    """Return the history card's foundation row for *opening*.

    The one place ``budget.account_openings``' stored provenance becomes the
    boolean the card renders.  It is a MAP over
    :class:`~app.enums.AccountOpeningSourceEnum` rather than
    ``source is USER_DECLARED``, and the difference is what happens when a
    third member is added: a positive test reads any new source as "not
    declared" silently, where a ``KeyError`` here forces whoever adds it to
    say which side of the question it falls on.  The same construction
    :func:`app.services.settle_day.settle_day_from_columns` uses on the
    settled-day bases, and for the same reason.

    Args:
        opening: The account's governing
            :class:`~app.services.cash_ledger.CashOpeningFact`, off the walk
            the caller already holds.

    Returns:
        The :class:`CashOpeningRow` the card renders as its oldest row.

    Raises:
        KeyError: When the stored ``source_id`` names no
            :class:`~app.enums.AccountOpeningSourceEnum` member, or names one
            this map has no answer for.  Unreachable through the foreign key,
            which admits only the seeded rows.
    """
    member = {
        ref_cache.account_opening_source_id(source): source
        for source in AccountOpeningSourceEnum
    }[opening.source_id]
    return CashOpeningRow(
        opened_on=opening.opened_on,
        # The RECORDING day, in the user's timezone -- the same two-clock
        # split every assertion row carries (``recorded_on`` beside
        # ``observed_on``), so the card's "entered" caption means one thing
        # on every row it appears on.
        recorded_on=to_display_date(opening.recorded_at),
        equity=round_money(opening.opening_equity),
        declared=_OPENING_WAS_DECLARED[member],
    )


def cash_anchor_history(
    account: Account, ctx: BalanceContext,
) -> CashAnchorHistory:
    """Return every balance an account has been told it held, newest first.

    Ruling **R-EV** (plan step X-f2-b): the DURABLE record of a balance
    assertion, which the app did not have.  Until this entry the only evidence
    that a back-dated assertion landed was an 8-second toast, and an AST pass
    over :class:`~app.models.account.AccountAnchorHistory` found no read path
    into any template except the GOVERNING assertion's "as of" caption -- which
    by definition is not the back-dated row (finding **N-205**).

    **It needs no new producer and adds no new rule.**  Every column is a field
    the fold's assertion replay already publishes:
    :class:`~._assertions.CashAnchorCorrection` carries ``observed_on``, its
    anchor's ``anchor_balance`` and ``asserted_at``, ``balance_before`` and
    ``delta``.  Re-deriving any of them here would be a second statement of that
    replay's arithmetic, which is the shape plan step X-f2-a corrected in the
    difference preview.

    **It takes no valuation date, and that is exact rather than an omission.**
    The loan twin
    (:func:`app.services.loan_posting_service.loan_balance_anchor_history`)
    filters its anchors to ``anchor_date <= as_of`` because a loan anchor can
    be dated ahead of a display date.  A cash assertion cannot:
    :func:`app.services.anchor_service.resolve_observation_day` refuses a day
    in the future at BOTH write doors, so every assertion has already happened
    and a bound would be a no-op that a later reader would have to re-justify.
    ``ctx`` is still required -- it scopes the settled rows the ``ledger``
    column folds -- but its ``as_of`` decides nothing here.

    Reads only -- no writes, no commit.

    Args:
        account: The account whose assertion log to read.  Caller owns the
            ownership check.  Its KIND is consulted once, for
            :attr:`CashAnchorHistory.reconcilable`.
        ctx: The read pass's :class:`~app.services.balance_at.BalanceContext`.
            Its scenario scopes the settled rows the walk folds; assertions
            themselves are per-ACCOUNT and replay in every scenario.

    Returns:
        The :class:`CashAnchorHistory`.  Its ``rows`` are empty only for an
        account carrying no assertion at all -- production-unreachable
        (``account_service.create_account`` and migration ``cfb15e782f86``
        guarantee an opening row), and answered rather than raised because a
        log of nothing is honestly empty.

    Raises:
        BaselineMissingError: When ``ctx`` carries no scenario.
    """
    _require_scenario(ctx)
    reconcilable = classify_account(account) is AccountProjectionKind.PLAIN
    # Through the pass rather than ``walk_cash_ledger`` directly, which is the
    # spelling :func:`records_balance_at` one function up already uses.  Two
    # spellings of one dependency inside one module is how a memo on the fold
    # would collapse ONE of this module's walks and quietly leave the other --
    # and plan step **X-i4** put that memo on the pass, so every reading here
    # shares the one assembly it holds.
    folded = _cash_fold.assembled_fold(account, ctx)
    corrections = folded.corrections
    # **The opening is a ROW of its own, not a badge on a neighbour** (plan
    # step X-f3c-2b).  It was ``CashAnchorFact.is_opening`` -- the earliest
    # assertion by position -- until X-f3c-2a moved it onto "the assertion
    # falling on the day ``budget.account_openings`` names".  Ruling **R-HG**
    # then made the opening equity the CLOSING balance for that day, so an
    # account whose books were moved back to reach its earliest record has no
    # assertion on it at all: the badge disappeared from 5 of 9 production
    # accounts, measured.  Publishing the record itself is what the badge was
    # trying to say in the first place, and it can carry the one thing a
    # borrowed label never could -- whether a human stated the figure.
    opening = _opening_row(folded.walk.opening)
    # ``booked`` rather than ``correction``: the walk's record and the row's
    # field would otherwise share a name inside one expression, and
    # ``correction=correction.delta`` reads as a self-reference.
    rows = [
        CashAnchorRow(
            observed_on=booked.observed_on,
            recorded_at=booked.anchor.asserted_at,
            recorded_on=booked.anchor.recorded_on,
            recorded=booked.anchor.anchor_balance,
            # ONE condition now, where there were two (plan step X-f3c-2a):
            # the OPENING row is no longer suppressed.  Its pair was hidden
            # because the fold swallowed the opening's correction into its seed,
            # so ``balance_before`` was the records summed from zero and the gap
            # between them was opening EQUITY rather than a correction --
            # publishing it would have said "the records were off by $689.16 the
            # day the account opened", which is false.  With the equity a stored
            # fact the pair means what the column headers say on every row: what
            # the books held, and what the owner's declaration moved it by.
            ledger=booked.balance_before if reconcilable else None,
            correction=booked.delta if reconcilable else None,
        )
        for booked in reversed(corrections)
    ]
    return CashAnchorHistory(
        rows=rows, opening=opening, reconcilable=reconcilable,
    )
