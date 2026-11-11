"""Balance-at-T seam -- a loan's RICH figures, with the balance deliberately absent.

The seam's fourth shape.  A loan tile wants more than a balance: the monthly
payment, the current rate, the payoff date, and whether the loan is retired.
Those are rich projection detail, not a balance-at-T, and the seam has always
been happy for a consumer to hold them.

What it must NOT hand out is the BALANCE.  ``LoanState.current_balance`` was a
balance-at-today, and the name-keyed fence of the day bound on function NAMES --
it could not see an attribute read.  So for as long as consumers held a
``LoanState``, the loan's
displayed balance reached the screen without passing the seam: the /savings loan
tile, the net-worth hero that reduces over it, the debt card, the Horizon's
index-0 liability point, and the property-equity card's mortgage leg were ALL
produced outside the one tested place, and the fence was structurally incapable
of noticing.  They agreed with the seam only because both paths happened to
bottom out in the same genesis ledger -- agreement by luck, not by construction,
which is the exact failure signature of the whole balance-bug family
(``docs/audits/balance_architecture/``).  Plan step D2a deleted that field
outright -- the bundle now carries NO balance for any holder to leak -- and the
retired predicates below fold the loan's recorded events instead.

:class:`LoanFigures` closes that by CONSTRUCTION rather than by policing: it
carries no balance, so a consumer holding one cannot render a wrong balance even
by accident.  A consumer that wants a loan's balance has exactly one way to get
it -- :func:`~app.services.balance_at.balance_at` -- which is the point.  This is
the same "do not hand ``current_balance`` to out-of-cluster callers" move that
``debt_schedule_rows`` made for the ``DebtSchedule`` bundle
(``followup_debt_schedule_attribute_fence.md``), until plan step
recurrence:R16-c-2 deleted both with their last caller.

``is_paid_off`` lives here, not in a consumer, for the same reason: it is a
LEDGER-derived predicate over the loan's confirmed balance, so it belongs beside
the balance rules rather than in a dashboard module that would have to reach for
the balance to compute it.
"""

from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from app.enums import InterestAccrualConventionEnum
from app.models.account import Account
from app.services import loan_resolver
from ._context import BalanceContext
from ._fold import last_closed_on
from . import _plan_fold

# The payoff derivation this module INJECTS into the read pass's memo (see
# :func:`loan_figures`).  ``_positions`` does not import this module, so the
# seam's internal graph stays a DAG.
from ._positions import memoized_payoff
from ._resolution import ResolvedLoan, resolved_loan

ZERO_MONEY = Decimal("0.00")


@dataclass(frozen=True)
class LoanTerms:
    """A loan's CONTRACT facts -- everything derivable without a scenario.

    The scenario-INDEPENDENT half of what a loan surface reads.  Every field here
    comes from the loan's params, its rate history evaluated at the pass's
    ``as_of`` and its recorded balance assertions: no payment, no ledger walk,
    no baseline scenario.  (Plan step recurrence:R16-c-2's checkpoint 10 made
    :attr:`recorded_start` read the settled payments too, the one
    scenario-scoped input on this type; ruling **R-R114** moved that decision
    to the tracking-start door and the field reads assertions alone again.)

    **Why this is its own type (plan step C8e).**  :class:`LoanFigures` used to
    carry the first four of these fields alongside the scenario-scoped ones, and
    that mixture was invisible while every field happened to be
    scenario-independent.  Step C8d
    added the first scenario-scoped field (the DERIVED ``payoff_date``, which folds
    the loan's projected payments), and the mismatch surfaced immediately as an
    outage: the escrow editor, the rate-history swap, and the recurring-payment
    amount read ONLY ``monthly_payment`` / ``current_rate``, never a balance, yet
    they began raising the seam's ``require_scenario`` for a user with no baseline
    -- a state ``baseline_service`` exists to repair, and one a loan can outlive
    ("a loan configured while the baseline was gone").

    Splitting the two along the dependency they actually have is what fixes that at
    the root: a consumer takes the narrower value when the narrower value is what it
    needs, so fail-loud stays exactly where a scenario is genuinely required and
    nothing degrades into a guessed answer.  No balance here either, for the same
    reason :class:`LoanFigures` carries none.

    Attributes:
        monthly_payment: The loan's P&I payment as of the context's ``as_of``
            (the governing rate period's level payment).
        current_rate: The annual interest rate in effect on ``as_of``, as a
            decimal fraction -- the resolver-derived source of truth that
            replaced the retired ``LoanParams.interest_rate`` column.
        is_originated: Whether the loan EXISTS yet -- whether its
            ``origination_date`` has arrived by the read pass's ``as_of``.

            The seam publishes this because ``balance_at`` correctly answers
            ``$0.00`` for a loan that has not been borrowed yet, and a consumer
            that reads a zero balance as "this debt is gone" then reports the
            opposite of the truth.  Three did: the dashboard's debt track counted
            an unclosed mortgage's whole principal as REPAID (66.67% paid, on a
            borrower who had paid nothing), the property equity chart dropped a
            mortgage closing in 26 days and drew ten years of debt-free equity, and
            the year-end panel reported -$198,049.28 of principal "paid".  A zero
            balance means "owes nothing"; it does NOT mean "has no debt ahead of
            it", and only this flag separates the two.
        is_arm: Whether the loan is an adjustable-rate mortgage
            (``LoanParams.is_arm``).  Projection detail, not a balance: it tells
            /debt-strategy to caption its projection as rate-assumption-bound (the
            strategy holds the CURRENT rate fixed and does not re-apply future ARM
            adjustments).  It is here so that consumer no longer needs the whole
            ``ResolvedLoan`` -- it reached for ``resolved_loan(account, ctx).params``
            to read this ONE boolean, and a route holding a ``ResolvedLoan`` is a
            route one attribute read away from an unfenced loan balance.
        recorded_start: The day the app's record of the loan starts
            (:attr:`~app.services.balance_at._resolution.ResolvedLoan.recorded_start`):
            its ``tracking_start`` assertion's date for a loan imported
            mid-life, else its origination (rulings **R-R111**, **R-R114**);
            no payment moves it.  A fact of the loan's recorded assertions
            rather than of its params or rates, and as scenario-independent.
            The loan chart's first month (ruling **R-R110**) and the
            net-worth trend's gate (ruling **R-R112**) read it here, where
            each read a schedule row's date until plan step
            recurrence:R16-c-2.
        convention: How the loan's note accrues interest
            (:class:`~app.enums.InterestAccrualConventionEnum`), through the
            one reading of the stored id
            (:func:`app.services.loan_resolver.accrual_convention`, plan step
            recurrence:R16-d).  A contract fact like the rate beside it:
            /debt-strategy charges its payoff projection under it, so that
            page needs no ``ResolvedLoan`` to learn it.
    """

    monthly_payment: Decimal
    current_rate: Decimal
    is_originated: bool
    is_arm: bool
    recorded_start: date
    convention: InterestAccrualConventionEnum


@dataclass(frozen=True)
class LoanFigures:
    """A loan's SCENARIO-SCOPED figures -- deliberately WITHOUT its balance.

    Every field is projection detail a loan tile renders beside the balance; the
    balance itself is not here, and its absence is the point (see the module
    docstring).

    **It COMPOSES :class:`LoanTerms` rather than re-declaring its fields** (plan
    step C8e).  Everything here needs the pass's baseline SCENARIO -- the payoff
    folds the loan's projected payments, the retired predicates read its
    scenario-scoped ledger walk -- while a loan's contract terms need none, so the
    two are separate values and a consumer takes the one it actually needs.
    Composing rather than copying is the same ruling
    :class:`~app.services.savings_dashboard_service._types._LoanAccountResult`
    carries: it used to copy these fields, and the copy "silently went stale the
    moment the seam grew ``is_originated``" -- a bundle hand-synchronised with the
    one it mirrors is the seam's fence with a hole in it.

    Attributes:
        terms: The loan's scenario-independent :class:`LoanTerms` (payment, rate,
            originated, ARM, recorded start).  Read through here so there is ONE
            derivation of them, whichever value a consumer holds.
        payoff_date: The DERIVED payoff -- the date the loan's balance folds to
            zero (:func:`~app.services.balance_at.loan_payoff_date`, plan step
            C8), read off the pass's memo so every surface that shows a payoff
            shows the one the BALANCE reaches zero on.

            It used to be ``LoanState.payoff_date``: the last row of the
            resolver's committed schedule walk, which amortizes one contractual
            installment per month whether or not a payment stands behind it, and
            which forces a final row at the contractual date for a loan paying
            short (``is_last_month``) -- a phantom payment.  So the chip could
            disagree with the equity chart and the balance beside it about when
            the debt ends.  Now the payoff IS the balance reaching zero, from the
            same seed and the same plan :func:`~app.services.balance_at.positions`
            folds, and they cannot disagree.

            ``None`` in two cases the consumer must tell apart, and
            :attr:`is_retired` is what tells them apart: the loan is already
            RETIRED (no forward crossing left to date -- badge it), or it never
            pays off within its plan (negative amortization, or an underpayment
            too severe to clear the post-contractual extension -- say so; do not
            hide the chip).
        closing_date: The loan's ONE closing date, past AND future
            (:func:`loan_closing_date`, plan step ``recurrence:R7d-h``) -- the
            forward crossing while it still owes, the date it LAST became closed
            once it does not.  ``None`` now means only *never pays off*, so
            unlike :attr:`payoff_date` it needs no companion flag to be read.

            :attr:`payoff_date` answers half its own question: it folds FORWARD
            from the confirmed present, so a retired loan returns ``None`` for
            *not my half* and every consumer invented a meaning for it -- the
            recurrence bound substituted the READ CLOCK, so a finished loan's
            stop moved with the day it was rendered and its admitted occurrence
            set GREW one per cadence period.  Read this for "when does this loan
            end"; read :attr:`payoff_date` only for "what does the forward plan
            project".
        is_retired: Whether the loan is DONE -- it has ORIGINATED and the fold
            of its recorded events answers ``<= 0``.  It has no debt line left:
            no balance now, and nothing scheduled ahead.

            THE single definition of "this loan has no debt to draw", which the
            property equity chart drops on.  A loan that has NOT been borrowed yet
            also owes ``$0.00`` and is emphatically not retired -- its whole debt
            line is ahead of it -- which is what the origination half of this test
            separates, and what kept a mortgage closing in 26 days on the chart.
        is_paid_off: Whether the loan is retired AND the ledger can show at least
            one confirmed payment for it.  **Strictly narrower than
            :attr:`is_retired`, and built on it, so the two cannot drift.**

            The extra confirmed-payment guard is a BADGING rule, not a debt rule:
            it keeps a brand-new loan with a degenerate ``$0`` opening anchor -- a
            misconfiguration, not an achievement -- from being badged "paid off" on
            the /savings tile and dropped from the debt card.

            Use :attr:`is_retired` to decide whether a loan has a debt line; use
            this to decide whether to CONGRATULATE the user.  Collapsing the two
            was measured: a mortgage paid off by a lump sum recorded as a balance
            true-up (no payment rows) reads ``is_paid_off=False``, so the equity
            chart charted it -- and since a zero-balance loan has an EMPTY
            schedule, the back-projection clip admitted its whole 360-row
            contractual walk and drew **$197,049.32** of debt the borrower does not
            owe, on the same page as an equity hero reporting ``$0.00``.
    """

    terms: LoanTerms
    payoff_date: date | None
    closing_date: date | None
    is_retired: bool
    is_paid_off: bool


def loan_terms(
    account: Account, ctx: BalanceContext,
) -> LoanTerms | None:
    """Return *account*'s CONTRACT terms, or ``None`` if it is not a loan.

    The scenario-INDEPENDENT read (plan step C8e): payment, rate, originated, ARM
    and the recorded start, all off the read pass's ONE memoized resolution
    (:func:`~app.services.balance_at._resolution.resolved_loan`).  It
    needs no baseline scenario and derives no balance, so the loan's non-balance
    WRITE surfaces -- the escrow editor, the rate-history swap, the recurring
    payment's amount -- take this rather than the scenario-scoped
    :class:`LoanFigures` they used to.

    Those surfaces are why this exists.  They read only the payment and the rate,
    yet holding the wider bundle coupled them to a baseline scenario the moment
    step C8d gave it a scenario-scoped field, turning an escrow edit into a 500 for
    a user whose baseline is missing.  Narrowing the value narrows the dependency;
    the fail-loud stays on :func:`loan_figures`, where a scenario genuinely IS
    required.

    Args:
        account: The account to read.  A non-loan (no ``LoanParams``) returns
            ``None``.
        ctx: The read pass's :class:`~app.services.balance_at.BalanceContext`.
            Its ``scenario`` may be ``None``.

    Returns:
        The :class:`LoanTerms`, or ``None`` when *account* is not a configured
        loan.
    """
    resolved = resolved_loan(account, ctx)
    if resolved is None:
        return None
    return _terms_from(resolved, ctx.as_of)


def _terms_from(resolved: ResolvedLoan, as_of: date) -> LoanTerms:
    """Build a resolved loan's :class:`LoanTerms`.

    THE one assembly, shared by :func:`loan_terms` and :func:`loan_figures`, so
    the terms a consumer reads are the same values whichever of the two values it
    holds -- the composition the wider bundle rests on.

    Args:
        resolved: The loan's
            :class:`~app.services.balance_at._resolution.ResolvedLoan`.
        as_of: The read pass's as-of.

    Returns:
        The loan's :class:`LoanTerms`.
    """
    return LoanTerms(
        monthly_payment=resolved.state.monthly_payment,
        current_rate=resolved.state.current_rate,
        is_originated=_is_originated(resolved, as_of),
        is_arm=bool(resolved.params.is_arm),
        recorded_start=resolved.recorded_start,
        convention=loan_resolver.accrual_convention(resolved.params),
    )


def loan_figures(
    account: Account, ctx: BalanceContext,
) -> LoanFigures | None:
    """Return *account*'s scenario-scoped loan figures, or ``None`` if not a loan.

    Reads the read pass's ONE memoized resolution
    (:func:`~app.services.balance_at._resolution.resolved_loan`), so
    these figures and the balance the same consumer reads from
    :func:`~app.services.balance_at.balance_at` come from the SAME resolution --
    identical by construction, not by two producers agreeing.  The payoff is the
    pass's ONE memoized derivation for the same reason: it reads through the single
    seam funnel every payoff consumer shares
    (:func:`~app.services.balance_at._positions.memoized_payoff`), which fills the
    pass's public payoff cache from
    :func:`~app.services.balance_at.loan_payoff_date` -- the seam owns the
    derivation, the context owns the storage (plan step D-ctx-b), so the context
    never reaches up into the seam that imports it.

    **A CONFIGURED loan needs a baseline scenario here**, and that is the whole
    point of the :class:`LoanTerms` split (step C8e): every field on this value is
    scenario-scoped, so requiring one is honest rather than incidental.  A consumer
    that needs only the contract terms takes :func:`loan_terms` and is not bound by
    the guard at all.  The not-a-loan test still runs BEFORE the guard, so a caller
    using this purely as "is this a configured loan?" for a user with no baseline
    (:func:`app.services.home_equity_service.resolve_home_equity`) keeps answering.

    Args:
        account: The account to read.  A non-loan (no ``LoanParams``) returns
            ``None``; the caller renders its non-loan tile.
        ctx: The read pass's :class:`~app.services.balance_at.BalanceContext`.

    Returns:
        The :class:`LoanFigures`, or ``None`` when *account* is not a configured
        loan.

    Raises:
        ValueError: When *account* IS a configured loan and ``ctx`` has no
            baseline scenario (the payoff derivation's ``require_scenario``).
    """
    resolved = resolved_loan(account, ctx)
    if resolved is None:
        return None
    # Derived ONCE and threaded: :func:`_is_retired` folds the walk at
    # ``as_of``, and the WALK is memoized on the pass while the FOLD is not, so
    # each extra call re-sorts the dated deltas and re-runs the prefix sum.
    # ``/savings`` builds this bundle per loan.
    retired = _is_retired(resolved, account, ctx)
    return LoanFigures(
        terms=_terms_from(resolved, ctx.as_of),
        payoff_date=memoized_payoff(account, ctx),
        closing_date=_closing_date(account, ctx, retired),
        is_retired=retired,
        is_paid_off=_is_paid_off(resolved, account, ctx),
    )


def loan_closing_date(
    account: Account, ctx: BalanceContext,
) -> date | None:
    """Return *account*'s ONE closing date -- past AND future.

    The whole of "when does this loan end", where
    :func:`~app.services.balance_at.loan_payoff_date` answers only half of it
    (plan step ``recurrence:R7d-h``).  That producer folds FORWARD from the
    confirmed present seed, so a loan already at zero has no crossing left and
    returns ``None`` -- which does not mean *never*, it means *not my half*.
    Three consumers each invented a meaning for that ``None``, and the recurrence
    bound's invention was a defect: it substituted the read pass's own ``as_of``,
    so a finished loan's stop tracked the day the page was loaded and its
    admitted occurrence set grew one occurrence per cadence period.

    Composed from the two producers that already own its halves, so it spells
    neither again:

    * **Closed at ``ctx.as_of``** (:func:`_is_retired`, THE seam's one definition
      of "no debt line left") -- the day it LAST became closed, the BACKWARD
      crossing over the recorded events
      (:func:`~app.services.balance_at._fold.last_closed_on`).
    * **Still owing** -- the FORWARD crossing, off the pass's memoized payoff
      (:func:`~app.services.balance_at._positions.memoized_payoff`), so this and
      the payoff chip cannot disagree about a loan that has not finished.

    ``None`` therefore means one thing only: the loan never pays off (negative
    amortization, or an underpayment too severe to clear the post-contractual
    extension).  A caller needs no companion flag to read it -- which is the
    point of the step, since needing one is what produced three inventions.

    Args:
        account: The amortizing loan account (the caller owns the ownership
            check).  Fails loud for a non-configured account, exactly as
            :func:`~app.services.balance_at.loan_payoff_date` does -- returning
            ``None`` there would re-introduce the ambiguity this producer exists
            to delete.
        ctx: The read pass's :class:`~app.services.balance_at.BalanceContext`.

    Returns:
        The loan's closing date, or ``None`` when it never pays off.

    Raises:
        BaselineMissingError: When ``ctx.scenario`` is None.  A ``ValueError``
            subclass; ONE application-level handler answers it (plan step
            X-v2, ruling R-BW), so no caller pre-checks.
        ValueError: When *account* is not a configured loan.
    """
    resolved = resolved_loan(account, ctx)
    if resolved is None:
        raise ValueError(
            f"loan_closing_date() requires a configured loan; account "
            f"{account.id} ({account.name!r}) has no LoanParams. A consumer "
            f"that must tell a non-loan apart takes loan_figures(), which "
            f"returns None for one."
        )
    return _closing_date(account, ctx, _is_retired(resolved, account, ctx))


def _closing_date(
    account: Account, ctx: BalanceContext, is_retired: bool,
) -> date | None:
    """Return the closing date, given the loan's ALREADY-DERIVED retired state.

    The body of :func:`loan_closing_date`, taking the predicate as a parameter
    rather than deriving it, so :func:`loan_figures` -- which needs the same
    boolean for its own field -- folds the walk once and fills both from it.

    Args:
        account: The loan account, threaded to the backward crossing's walk.
        ctx: The read pass's :class:`~app.services.balance_at.BalanceContext`.
        is_retired: Whether the loan is closed at ``ctx.as_of``
            (:func:`_is_retired`), which selects the crossing to answer with.

    Returns:
        The loan's closing date, or ``None`` when it never pays off.
    """
    if is_retired:
        return last_closed_on(ctx.loan_walk(account), ctx.as_of)
    return memoized_payoff(account, ctx)


def _is_originated(resolved: ResolvedLoan, as_of: date) -> bool:
    """Return whether the loan has come into existence by *as_of*.

    :func:`._plan_fold.is_originated` over the loan's origination -- THE one
    definition of "does this loan exist yet", shared by
    :attr:`LoanFigures.is_originated`, :func:`_is_paid_off` and the forward
    readers' retired guard so the seam cannot answer it two ways.  See
    :attr:`LoanFigures.is_originated` for why the seam publishes it at all.

    Args:
        resolved: The loan's
            :class:`~app.services.balance_at._resolution.ResolvedLoan`.
        as_of: The read pass's as-of.

    Returns:
        ``True`` when the loan's ``origination_date`` has arrived.
    """
    return _plan_fold.is_originated(resolved.params.origination_date, as_of)


def _is_retired(
    resolved: ResolvedLoan, account: Account, ctx: BalanceContext,
) -> bool:
    """Return whether the loan is DONE -- borrowed, and now owing nothing.

    :func:`._plan_fold.is_retired` over the pass's memoized walk -- THE one
    definition of "this loan has no debt line left" since plan step
    recurrence:R16-c-1 moved it there, where the payoff, the required extra
    and the loan page's installment list guard on it too; shared here by
    :attr:`LoanFigures.is_retired` and :func:`_is_paid_off` (which is this plus
    a badging guard), so the seam cannot answer it two ways.

    The owed figure is the FOLD of the loan's recorded events at the pass's
    ``as_of`` (:func:`~app.services.balance_at._fold.fold_from_walk` over the
    read pass's memoized walk) -- the SAME derivation
    :func:`app.services.balance_at.positions` reads the past through, so this
    predicate and the balance rendered beside it cannot disagree.  It replaced
    ``LoanState.current_balance`` (plan step D2a), which for a loan whose
    posting ledger cannot answer fell back to the money-blind anchor replay --
    the one place in the seam that could still contradict the folded balance on
    the same page.

    **A loan that has not ORIGINATED is not retired; it has not been taken out.**
    That guard is load-bearing, not defensive: the fold correctly answers
    ``0.00`` for a loan configured before it closes, so without it an unclosed
    mortgage reads as DONE -- dropped from the debt card, gone from the Horizon's
    liabilities, and erased from the property equity chart, which then draws ten
    years of debt-free equity on a house about to carry a mortgage.

    Args:
        resolved: The loan's
            :class:`~app.services.balance_at._resolution.ResolvedLoan`.
        account: The loan account, for the pass's memoized walk.
        ctx: The read pass's :class:`~app.services.balance_at.BalanceContext`
            (its ``as_of`` is what the origination and the fold are tested
            against).

    Returns:
        ``True`` when the loan has originated and its folded events say nothing
        is owed.
    """
    return _plan_fold.is_retired(
        ctx.loan_walk(account), resolved.params.origination_date, ctx.as_of,
    )


def _is_paid_off(
    resolved: ResolvedLoan, account: Account, ctx: BalanceContext,
) -> bool:
    """Return whether the loan is retired AND has a confirmed payment behind it.

    Strictly narrower than :func:`_is_retired`, and BUILT ON IT so the two cannot
    drift.  The extra guard is a BADGING rule, not a debt rule: it keeps a
    brand-new loan with a degenerate ``$0`` opening anchor -- a misconfiguration,
    not an achievement -- from being badged "paid off" on the /savings tile.

    This replaced a ``resolve_loan(inputs, date.max)`` probe that could not have
    consulted the ledger even in principle -- the confirmed view returns
    ``None`` for any ``as_of`` after today -- and so answered from the
    pre-read-switch anchor replay, which is BLIND TO MONEY: it advances one
    SCHEDULED step per confirmed payment and discards the cash.  A loan retired
    by one lump-sum payment read as still-owing (no badge, still active debt on
    the Horizon), and a loan paid SHORT could read as retired and VANISH from the
    debt card's total.  Both are regression-tested
    (``followup_redundant_loan_resolution.md``).

    **Do NOT use this to decide whether a loan has a debt line** -- that is
    :func:`_is_retired`.  A mortgage paid off by a LUMP SUM recorded as a balance
    true-up has no payment rows, so it reads ``False`` here while owing ``$0.00``.
    Charting on this predicate drew **$197,049.32** of phantom debt for such a
    loan (its zero balance means an EMPTY schedule, so the equity chart's
    back-projection clip admitted its entire contractual walk), beside an equity
    hero correctly reporting ``$0.00`` on the same page.

    Args:
        resolved: The loan's
            :class:`~app.services.balance_at._resolution.ResolvedLoan`.
        account: The loan account, threaded to :func:`_is_retired`'s fold.
        ctx: The read pass's :class:`~app.services.balance_at.BalanceContext`.

    Returns:
        ``True`` when the loan is retired and at least one payment is confirmed.
    """
    if not _is_retired(resolved, account, ctx):
        return False
    return any(p.dates.is_confirmed for p in resolved.context.payments)
