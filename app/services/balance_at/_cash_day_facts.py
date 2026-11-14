"""Balance-at-T seam -- a cash fold read DAY by day, and what moved each day.

The cash fold's fourth reading (plan step ``bank_import:X-f6e-2``): each day's
folded balance beside the tiers that moved it (:class:`CashDayFacts`), for a
reader comparing the app against an outside record (``bank_agreement``).

**Split out of** :mod:`._cash_fold` **at plan step** ``pay_calendar:C9``
(task plan#3941), when that module stood at the 1,000-line ceiling and the
estimated tier was about to join the running total.  The split is by subject:
:mod:`._cash_fold` assembles the running total, and this module reads it back
split by tier.  It imports the fold and the fold imports nothing of it, the
direction :mod:`._cash_periods` already takes.

Boundary discipline (``CLAUDE.md``): no Flask symbol, no writes; all money is
:class:`~decimal.Decimal`.  Seam-PRIVATE.
"""

from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from ._cash_fold import AssembledCashFold
from ._fold import sample_cumulative

_ZERO_MONEY = Decimal("0.00")


@dataclass(frozen=True)
class CashDayFacts:
    """One day's folded balance, and the FOUR things that moved it.

    **The decomposition lives in the seam, one module from
    :func:`~._cash_fold._running_steps`, because it is that assembly read
    back.**  A consumer deriving it -- "whatever the balance moved by, less the
    rows I can see, must be a true-up" -- would be correct today and silently
    WRONG the day a fourth tier joins the running total, labelling it as
    something it is not.  Stating the split beside the fold that builds the
    steps means a new tier is a change to this class rather than a mislabelled
    number on a report.  *It read THREE until plan step* ``pay_calendar:C9``
    *added* :attr:`estimated`, *which is that sentence's case arriving.*

    Attributes:
        balance: The account's cash-flow balance at the END of the day, cent
            quantized -- the same figure :func:`~._cash_fold.balances_at` samples,
            from the same running total.
        recorded: What the account's OWN settled rows moved that day
            (:attr:`~app.services.cash_ledger.CashSourceFact.delta`, summed).
            The only one of the three that is money the app believes actually
            changed hands on that day.
        asserted: What BALANCE ASSERTIONS moved that day -- the jump each reset
            booked, **every assertion included, the first one too** (plan step
            X-f3c-2a).  The opening used to contribute nothing by construction,
            because the fold swallowed its correction into the seed and
            cancelled it on its own day (ruling R-I).  The seed is a stored fact
            now, so the first assertion books what it actually is: the
            difference between what the owner declared and what the books held,
            which is ``0.00`` wherever the two agree and a real movement
            wherever a BACK-DATED assertion disagrees with the recorded
            opening.
        planned: What still-Projected rows contribute that day, each landing at
            ``max(its attribution date, as_of + 1)`` (ruling R-G).  Zero for
            every day at or before *as_of*, which is what lets a reader
            comparing PAST days against an outside record ignore it.
        estimated: What the recurring definitions contribute that day past
            the last saved paycheck, where no row exists (plan step
            ``pay_calendar:C9``, :mod:`._cash_estimate`).  Zero for every
            day at or before both the last saved paycheck's end and *as_of*,
            for the reason ``planned`` is.
    """

    balance: Decimal
    recorded: Decimal
    asserted: Decimal
    planned: Decimal
    estimated: Decimal


@dataclass(frozen=True)
class CashDaySeries:
    """A day-grain reading of one account's fold, and the day its books OPENED.

    Attributes:
        facts: One :class:`CashDayFacts` per requested day.
        opened_on: The day the account's books opened -- the governing
            :attr:`~app.services.cash_ledger.CashOpeningFact.opened_on` off
            the walk this fold was built from.  Never ``None``: the walk cannot
            be loaded without an opening.

    **``opened_on`` travels with the facts because the walk already holds it.**
    A reader comparing the app against an outside record has to tell "we
    disagree here" from "the books do not know this day", and the books'
    boundary is read two ways (:mod:`app.utils.books_boundary`, ruling
    **R-HG**): a balance from ``opened_on`` on, a movement only after it.  The
    facts stay TOTAL -- the flat opening level included -- because what a
    day's figures MEAN beside an outside record is that reader's question
    (:mod:`app.services.bank_agreement`, plan step ``balance:X-f3c-2b-2c``).
    *Until that step this carried the account's first CASH FACT instead*
    (finding **BAL-616**), which counted the opening day's lines as
    disagreements wherever an assertion fell on it and compared a quiet books
    day only once something (a row or a back-dated balance) was recorded on
    or before it.
    """

    facts: "dict[date, CashDayFacts]"
    opened_on: date


def _day_sums(
    pairs: "list[tuple[date, Decimal]]",
) -> "dict[date, Decimal]":
    """Return ``{day: total}`` for ``(day, amount)`` pairs.

    Args:
        pairs: The dated amounts to reduce.

    Returns:
        One entry per DISTINCT day present, summed.
    """
    sums: "dict[date, Decimal]" = {}
    for day, amount in pairs:
        sums[day] = sums.get(day, _ZERO_MONEY) + amount
    return sums


def day_facts(
    folded: AssembledCashFold, days: list[date],
) -> "CashDaySeries":
    """Return each day's folded balance beside the four tiers that moved it.

    The fourth reading of the ONE assembled row set (plan step
    ``bank_import:X-f6e-2``), and a reading rather than a second producer: the
    balance it reports is :func:`sample_cumulative` over the very
    ``(seed, steps)`` :func:`~._cash_fold.balances_at` samples, through the
    latest of *days* (:meth:`~._cash_fold.AssembledCashFold.through`), so the
    two cannot disagree about a day's balance.  What it adds is the SPLIT of that day's
    movement, which no other reader needs and a consumer must not re-derive
    (see :class:`CashDayFacts`).

    Args:
        folded: The account's :class:`AssembledCashFold`
            (:func:`~._cash_fold.assembled_fold`).
        days: The days to answer, in any order.  Duplicates collapse.

    Returns:
        The :class:`CashDaySeries` -- one :class:`CashDayFacts` per distinct
        requested day, and the day the account's books opened.

    **The four components sum to the day's change in the running total**, and
    that is arithmetic rather than a claim: ``_running_steps`` assembles exactly
    ``dated_deltas`` (the source facts) plus this fold's assertion corrections
    plus the planned nets, and
    :meth:`~._cash_fold.AssembledCashFold.through` adds the estimated nets it
    returns beside the steps, so a day's steps ARE these four sums.  The opening
    equity is in the SEED, not in any day's steps, so it moves no day (plan step
    X-f3c-2a; before it a compensator had to cancel the opening correction on
    its own day to achieve the same thing).  ``balance`` is quantized where the
    components are exact, so a
    reader wanting the identity to the cent must compare the components rather
    than differencing two rounded balances.
    """
    if not days:
        return CashDaySeries(facts={}, opened_on=folded.walk.opening.opened_on)
    running = folded.through(max(days))
    balances = sample_cumulative(folded.seed, running.steps, days)
    recorded = _day_sums(
        [(fact.settled_on, fact.delta) for fact in folded.walk.source_facts]
    )
    # EVERY assertion's correction, the first included (plan step X-f3c-2a).
    # The opening's used to be excluded because the fold swallowed it into its
    # seed and cancelled it on its own day; with the seed a stored fact the
    # first assertion is an ordinary correction -- ``0.00`` where the books
    # opened at the level the records imply, and a real movement where a
    # BACK-DATED assertion disagrees with the recorded opening, which is
    # precisely the day a reader needs to see it.
    asserted = _day_sums(
        [
            (correction.observed_on, correction.delta)
            for correction in folded.corrections
        ]
    )
    return CashDaySeries(
        facts={
            day: CashDayFacts(
                balance=balance,
                recorded=recorded.get(day, _ZERO_MONEY),
                asserted=asserted.get(day, _ZERO_MONEY),
                planned=folded.day_nets.get(day, _ZERO_MONEY),
                estimated=running.estimated.get(day, _ZERO_MONEY),
            )
            for day, balance in balances.items()
        },
        opened_on=folded.walk.opening.opened_on,
    )


__all__ = ["CashDayFacts", "CashDaySeries", "day_facts"]
