"""Balance-at-T seam -- the FORWARD readers over a loan's one timeline.

Plan step **R16-a** split this module out of :mod:`._plan` when that module went
past pylint's 1000-line ceiling; plan step **recurrence:R16-c-1** emptied it of
its fold.  It held ``_split_plan``, a second replay of the loan's plan from a
SEED the settled fold had resolved, and four readers over that replay; the
seam's ONE timeline (:mod:`._loan_stream`) now carries the plan's outcomes
behind the recorded facts, so the balance is :func:`._fold.fold_from_walk` over
the timeline exactly as the past always was, and what survives here is the two
readings of the future that are not a balance at a date:

* :func:`timeline_payoff_date` -- the date the balance first reaches zero.
* :func:`required_extra` -- what must be added per month to clear it by a
  target date.
* :func:`is_retired` -- the predicate both guard on: borrowed, and now owing
  nothing.  THE one definition, shared with :mod:`._loan_figures` (it lived
  there until this step, and the two forward readers restated its arithmetic
  as ``seed <= 0``).

Both read the SAME timeline :func:`~app.services.balance_at.positions` folds,
so a loan's projected balance, its derived payoff, its required extra, its
projected interest (:mod:`._loan_interest`) and its rendered schedule cannot
disagree about what a future payment pays.  ``fold_forward``, ``_split_plan``,
``plan_interest_in_year`` and the ``PlannedInstallment`` record are DELETED:
the timeline's :class:`~app.services.loan_ledger.PaymentOutcome` is the record
for a projected payment as it is for a settled one.

Boundary discipline (``CLAUDE.md``): no Flask symbol, no writes; all money is
:class:`~decimal.Decimal`.  Seam-PRIVATE -- W9910 refuses an import of it from
outside :mod:`app.services.balance_at`.
"""

from collections.abc import Callable
from datetime import date
from decimal import ROUND_CEILING, Decimal

from app.services.loan_ledger import LoanLedgerWalk, PaymentOutcome

from ._fold import fold_from_walk

_ZERO_MONEY = Decimal("0.00")

# :func:`required_extra`'s bisection stops once the bracket is this narrow,
# and the answer is the upper end rounded UP to the cent -- so the reported extra
# is at most a cent above the true threshold and never below it.
_EXTRA_SEARCH_TOLERANCE = Decimal("0.001")
_CENTS = Decimal("0.01")
# How many times that search may DOUBLE its upper bound looking for an extra that
# reaches the target before declaring the target unreachable.  Twenty doublings
# is a factor of a million on the loan's own balance; a target that survives it is
# not a rounding matter but a date no payment schedule can reach.
_EXTRA_SEARCH_DOUBLINGS = 20


def first_installment_at_most(
    installments: list[PaymentOutcome],
    owed_at_most: Decimal,
) -> PaymentOutcome | None:
    """Return the first installment leaving at most *owed_at_most* owed, or ``None``.

    The ONE statement of "when does this trajectory get the loan down to X",
    whose DUE date is the payoff at ``X = 0.00`` (:func:`installments_payoff`)
    and whose VISIBLE date is a debt goal's projected day (plan step
    credit_card:CC-5-5d, rulings R-CC73 and R-CC88: the day the loan's tile,
    which folds on the visible date, first shows the balance at or under the
    goal's target).  One walk, one predicate, two readings of the installment
    it finds -- the two dates differ only for an installment that is overdue
    and still projected, whose visible date ruling D1 clamps to the day after
    the pass's.  Public through :mod:`app.services.balance_at`.

    Args:
        installments: A timeline's projected outcomes
            (:attr:`~app.services.loan_ledger.LoanLedgerWalk.projected_splits`),
            in walk order.
        owed_at_most: The balance the trajectory must reach.

    Returns:
        The first installment whose ``balance_after`` is ``<= owed_at_most``, or
        ``None`` when none is (the plan never gets there, or there is none).
    """
    for installment in installments:
        if installment.balance_after <= owed_at_most:
            return installment
    return None


def installments_payoff(installments: list[PaymentOutcome]) -> date | None:
    """Return the DUE date of the first installment whose balance reaches zero.

    The ONE statement of "when does this trajectory clear the loan", read by
    :func:`timeline_payoff_date` over the pass's timeline and by a surface
    already holding the timeline's projected outcomes (the loan page's
    pay-off-sooner lever, plan step R7d-g-3), so a caller with the split in
    hand does not replay the loan a second time to learn what it already holds.
    Public through :mod:`app.services.balance_at`.  It is
    :func:`first_installment_at_most` at ``0.00``, read on its due date
    (contract time, the month the payoff has always keyed on).

    Args:
        installments: A timeline's projected outcomes
            (:attr:`~app.services.loan_ledger.LoanLedgerWalk.projected_splits`),
            in walk order.

    Returns:
        The DUE date the balance first reaches ``<= 0``, or ``None`` when no
        installment does (the plan never clears the loan, or there is none).
    """
    installment = first_installment_at_most(installments, _ZERO_MONEY)
    return None if installment is None else installment.due_date


def owed_at(walk: LoanLedgerWalk, as_of: date) -> Decimal:
    """Return the balance *walk* owes on *as_of*.

    :func:`._fold.fold_from_walk` at one date, named because :func:`is_retired`
    and :func:`required_extra`'s search bound both ask it, and the figure is the
    one :func:`~app.services.balance_at.positions` shows for that day.

    Args:
        walk: The loan's walk -- its timeline (:func:`._loan_stream.loan_timeline`)
            or its facts alone; a projection is visible after the read day, so
            the two answer *as_of* alike.
        as_of: The read pass's as-of.

    Returns:
        The cent-quantized balance owed on *as_of*.
    """
    return fold_from_walk(walk, [as_of])[as_of]


def is_originated(owed_from: date, as_of: date) -> bool:
    """Return whether a loan originating *owed_from* exists by *as_of*.

    THE one definition of "does this loan exist yet", read by
    :func:`is_retired` and, through ``_loan_figures``, by
    :attr:`~app.services.balance_at.LoanFigures.is_originated`.  It lived in
    ``_loan_figures`` alone until plan step recurrence:R16-c-1 moved the retired
    predicate here beside the forward readers that guard on it.

    Args:
        owed_from: The loan's ``origination_date``.
        as_of: The read pass's as-of.

    Returns:
        ``True`` when the origination date has arrived.
    """
    return owed_from <= as_of


def is_retired(walk: LoanLedgerWalk, owed_from: date, as_of: date) -> bool:
    """Return whether the loan is DONE at *as_of* -- borrowed, and now owing nothing.

    THE one definition of "this loan has no debt line left", shared by
    :attr:`~app.services.balance_at.LoanFigures.is_retired` (through
    :mod:`._loan_figures`), the payoff, the required extra and the loan page's
    installment list, so the seam cannot answer it two ways.  The owed figure is
    the fold of the walk at the pass's ``as_of`` -- the SAME derivation
    :func:`~app.services.balance_at.positions` reads through, so this predicate
    and the balance rendered beside it cannot disagree.

    **A loan that has not ORIGINATED is not retired; it has not been taken out.**
    That guard is load-bearing, not defensive: the fold correctly answers
    ``0.00`` for a loan configured before it closes, so without it an unclosed
    mortgage reads as DONE -- dropped from the debt card, gone from the Horizon's
    liabilities, erased from the property equity chart, and (since plan step
    recurrence:R16-c-1, when the forward readers stopped starting from a seed
    that was the opening balance for such a loan) with no payoff to date.

    Args:
        walk: The loan's walk (see :func:`owed_at`).
        owed_from: The loan's ``origination_date``.
        as_of: The read pass's as-of.

    Returns:
        ``True`` when the loan has originated by *as_of* and its folded events
        say nothing is owed on it.
    """
    return is_originated(owed_from, as_of) and owed_at(walk, as_of) <= _ZERO_MONEY


def timeline_payoff_date(
    walk: LoanLedgerWalk, owed_from: date, as_of: date,
) -> date | None:
    """Return the DUE date *walk*'s plan drives its balance to zero on, or ``None``.

    The loan's derived payoff date: the DUE date of the FIRST projected payment
    whose running balance reaches ``<= 0`` -- the installment that pays the loan
    off (:func:`installments_payoff`).  This is a fold-to-zero, NOT the plan's
    last date: a recurring payment's plan runs PAST the contractual payoff (its
    occurrences' window, ``_plan._PAYOFF_EXTENSION_MONTHS`` installments past the
    contract's last), so a loan paying extra reaches zero at an EARLIER
    installment (the date the engine's own contract-plus-extra projection
    reaches, ``project_forward(extra_monthly=...)``) and an underpaying one at a
    LATER installment in the window (a real date, where the contract's
    projection forces its last date via ``is_last_month``).  A loan with no
    recurring payment is planned as its contract line, which closes it on the
    term's last installment (ruling **plan#3982**).

    Two ``None`` cases, kept distinct from a real payoff date so the caller can
    tell "already done" from "never pays off" (both differ from "pays off on date
    D"):

    * **Already retired** (:func:`is_retired`: originated, and owing nothing at
      *as_of*): there is no FORWARD crossing to date.  The caller reads
      :attr:`~app.services.balance_at.LoanFigures.is_retired` for the paid-off
      state; this does not invent a future payoff for a loan already at zero
      (the first planned payment would otherwise look like a "payoff").  A loan
      NOT yet originated is not retired: its whole plan lies ahead and this
      dates it.
    * **Never reaches zero within the plan**: negative amortization (a payment
      below the period interest, so the balance grows), an underpayment so
      severe the balance is still positive after the window past the contract,
      or a loan with no recurring payment that its contract no longer closes
      (its record past the term, or a projected transfer on the term's last
      installment leaving it owing).  A MILDER underpayment is NOT here -- the
      window lets it clear a few months past the contractual date, and this
      returns that later date.
      Recurrence stays indefinite; the ``None`` the retired case and this share
      is disambiguated by ``is_retired`` (retired here is False).

    The DUE date (contract time), not the visible date, is returned so the
    payoff month is the installment's own -- matching the contract's
    ``original_forward[-1].payment_date`` the payoff has always keyed on, and,
    for a normal future loan, equal to the visible date anyway (they differ only
    for an overdue-but-projected installment, which almost never clears a loan).

    Args:
        walk: The loan's timeline (:func:`._loan_stream.loan_timeline`, or a
            what-if replay of the same stream).
        owed_from: The loan's ``origination_date``.
        as_of: The read pass's as-of -- the day the retired test values.

    Returns:
        The DUE date the balance first reaches ``<= 0``, or ``None`` when the loan
        is already retired at *as_of* or never pays off.
    """
    if is_retired(walk, owed_from, as_of):
        return None
    return installments_payoff(walk.projected_splits)


def required_extra(
    walk: LoanLedgerWalk,
    owed_from: date,
    as_of: date,
    target_date: date,
    replay_with: Callable[[Decimal], LoanLedgerWalk],
) -> Decimal | None:
    """Return the extra PER MONTH that clears *walk*'s balance by *target_date*.

    The target-date calculator's answer, searched over the SAME timeline
    :func:`timeline_payoff_date` and :func:`~app.services.balance_at.positions`
    read (plan step C8f).  It answers "what must I add each month to be done by
    then" -- per ACCRUAL PERIOD since plan step R16-a, which is what the panel
    has always printed (``loan/_payoff_results.html`` renders it ``/mo``) and
    what a fortnightly payer was never given: added per RECORD it was 26
    helpings of "a month".  Where "done" is the date the BALANCE reaches zero --
    so the figure and the payoff chip beside it rest on one forward model.

    **Why it is not the schedule search it replaced.**  The retired
    ``loan_resolver.target_date_outlook`` binary-searched
    ``amortization_engine.project_forward``, which amortizes one contractual
    installment per month whether or not a payment stands behind it (finding
    B-9).  For a delinquent or drifted loan that walk retires the debt EARLIER
    than the fold does, so it could report "no extra needed" for a target the
    loan does not actually reach -- contradicting the payoff chip on the same
    screen, which folds.  Searching the fold removes the second model rather than
    relabelling its answer.

    Monotone, so a binary search is sound: every added cent is pure principal
    (:func:`~app.utils.money.apply_payment_cash` subtracts the standing interest
    and escrow first), so more extra can only move the zero-crossing earlier or
    leave it where it is.

    **Every comparison here is on the VISIBLE date, not the due date.**  The
    payoff DATE this seam reports is the clearing installment's DUE date (contract
    time, matching what the loan card has always shown -- see
    :func:`timeline_payoff_date`), but "will I be clear by X" is a question about
    when the money MOVES, and those differ for an overdue-but-still-projected
    payment: ruling D1 clamps its visible date to ``as_of + 1d`` while its due
    date stays in the past.  Comparing due dates let a target in the PAST look
    reachable -- the fold "cleared" the loan on a past due date, so the search
    happily returned a six-figure extra for a date the user cannot pay on any
    more.

    Args:
        walk: The loan's timeline as it stands (:func:`._loan_stream.loan_timeline`).
            Its projections' cash already carries the loan's STANDING
            ``extra_principal``, so the result is the amount needed ON TOP of
            the user's current plan.
        owed_from: The loan's ``origination_date``.
        as_of: The read pass's as-of -- the day the retired test values.
        target_date: The date the user wants to be done by.
        replay_with: The what-if replay of the SAME stream under a candidate
            extra (:func:`._loan_stream.what_if_timeline` bound to the loan,
            or the leaf's :func:`~app.services.loan_ledger.replay_loan_stream`
            over ``walk.stream``); each probe of the search calls it once.

    Returns:
        ``Decimal("0.00")`` when the plan ALREADY clears the loan by
        *target_date* (including a retired loan), the searched
        per-month extra when one exists, or ``None`` for a target no extra
        reaches.  That last has two causes: no planned payment has even HAPPENED
        by then (a target in the past, or before the next installment lands), or
        -- the termination backstop below -- the search exhausted its doublings,
        which past the first guard means the split arithmetic stopped responding
        to more principal rather than that the date is genuinely out of reach.
    """
    if is_retired(walk, owed_from, as_of):
        return _ZERO_MONEY

    def _clears_by(extra: Decimal) -> bool:
        """Whether *extra* a month puts the balance at zero by the target.

        Keyed on the clearing payment's VISIBLE date -- when its cash actually
        moves -- so a past due date can never stand in for a payment that has not
        happened (see the note above).
        """
        for installment in replay_with(extra).projected_splits:
            if installment.balance_after <= _ZERO_MONEY:
                return installment.visible_on <= target_date
        return False

    if _clears_by(_ZERO_MONEY):
        return _ZERO_MONEY
    if not any(
        payment.visible_on <= target_date for payment in walk.projected_splits
    ):
        # No planned payment has even happened by then, so no extra lands in
        # time: the target is in the past, or before the next installment.
        return None

    # An UPPER BOUND has to be found, not assumed.  The balance owed at the
    # read looks like one -- pay it all as extra and the first installment
    # clears it -- but it is not: the allocation
    # (:func:`~app.utils.money.apply_payment_cash`) takes the standing interest
    # and escrow out of the cash FIRST, so on a loan whose period interest
    # exceeds its payment cash even that leaves a residue.  Double until the
    # bound genuinely reaches the target, so the bisection below starts from an
    # invariant that HOLDS rather than one that looked obvious.  For a loan not
    # yet originated the read-day balance is 0.00 and the bound starts from
    # what it will owe the day it closes -- its opening assertion, the first
    # reset in its stream (the same fork the retired seed made).
    #
    # The cap is a termination backstop, not an expected outcome: past the
    # no-payment guard above, some extra always clears the loan at the first
    # payment that lands by the target (the extra is unbounded principal), so the
    # ``else`` is not the "unreachable target" case -- that one already returned.
    # The cap exists so a future change to the split can never turn this into a
    # hang.
    seed = owed_at(walk, as_of)
    low, high = _ZERO_MONEY, (
        seed if seed > _ZERO_MONEY
        else next(
            reset.balance for reset in walk.stream.resets if reset.is_opening
        )
    )
    for _ in range(_EXTRA_SEARCH_DOUBLINGS):
        if _clears_by(high):
            break
        low, high = high, high * 2
    else:
        return None

    # Now the invariant the bisection needs holds: ``low`` misses, ``high``
    # reaches.  Narrow to well under a cent.
    while high - low > _EXTRA_SEARCH_TOLERANCE:
        mid = (low + high) / 2
        if _clears_by(mid):
            high = mid
        else:
            low = mid

    # Round UP to the cent, never to nearest.  The threshold sits between ``low``
    # and ``high``, so rounding to NEAREST can land below it -- and a fraction of
    # a cent short at the payoff boundary leaves a positive balance, which pushes
    # the payoff a whole INSTALLMENT past the target.  Measured on a randomized
    # sweep: 99 of 300 generated loans returned an extra that missed its target
    # by one month under half-up rounding.
    #
    # Both ends are tried so the answer is the EXACTLY minimal cent, not merely a
    # cent that works: when the true threshold falls on a whole cent, ``high`` is
    # a hair above it and its ceiling would overcharge the user by a cent.
    # ``ceil(low)`` is either that minimal cent or one below it, so testing it
    # first (one more fold) decides which.
    candidate = low.quantize(_CENTS, rounding=ROUND_CEILING)
    if _clears_by(candidate):
        return candidate
    return high.quantize(_CENTS, rounding=ROUND_CEILING)
