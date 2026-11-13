"""The helpers and constants every part of this package shares: the module-level helpers of
``tests/test_services/test_cash_walk.py``, moved verbatim by ``tools/split``.
"""

from datetime import datetime, timezone
from decimal import Decimal

from app.extensions import db
from app.services.cash_ledger import dated_deltas, walk_cash_ledger
from app.services.balance_at._assertions import assertion_corrections
from app.services.balance_at._cash_fold import assembled_fold, balances_at
from app.utils.dates import DISPLAY_TIMEZONE
from tests._test_helpers import (
    append_balance_assertion,
    open_books_before_the_first_assertion,
    read_pass,
    reassert_balance_on,
    restate_account_opening,
)


def _instant(year, month, day, hour=0, minute=0, second=0):
    """Return the aware-UTC instant of a wall-clock moment on the USER's day.

    The arguments are read as the DISPLAY timezone -- the clock the user is
    actually looking at -- and converted to UTC for storage, which is the
    direction production runs in: the settle day and ``created_at`` are stamped
    when the user acts and stored UTC.

    **It read them as UTC until ruling R-DH (b)** (2026-07-31), and the default
    ``hour=0`` then meant midnight UTC -- 7pm or 8pm the PREVIOUS Eastern day.
    So a fixture writing ``date(2026, 1, 15)`` to mean "this settled on the
    15th" pinned an event the fold correctly places on the 14th, and five tests
    in this class asserted figures for a day their own setup had not built.
    Reading the arguments as Eastern makes the helper mean what every call site
    already said it meant, and it preserves same-day ORDERING exactly: two
    moments on one day shift by the same offset.
    """
    return datetime(
        year, month, day, hour, minute, second, tzinfo=DISPLAY_TIMEZONE,
    ).astimezone(timezone.utc)


def _opened_at(account, at, books_open_on=None):
    """Open the account's books and assert its balance on *at*'s day (builder).

    **TWO acts, and they are spelled as two** (plan step X-f3c-2c).  It used to
    be one, because ``restamp_opening_assertion`` re-dated the stored
    origination assertion and restated the opening as a side effect.  An
    assertion is append-only, so a fixture has the pair a production owner has:
    say what the balance was on a day, and say when the books opened.

    **The books move BACKWARD BY DEFAULT, and the first draft of this helper
    moved them forward unconditionally.**  It restated them to the day before
    *at* whatever else stood, which shoved them two years FORWARD past the
    seeded account's own origination at ~25 sites that had asked for no such
    thing.  Found by the adversarial review of this step.

    Args:
        account: The account to open.
        at: The aware-UTC instant of the assertion.
        books_open_on: The civil day the books open.  ``None``, the default,
            bounds them before every day a row could land on
            (``open_books_before_the_first_assertion``), which is
            backward-only.  A case whose SUBJECT is the books' own day names
            it -- the two in ``test_the_sources.py``'s
            ``TestASourceCannotPredateTheBooks`` that grade a movement dated on or before them
            do, because the day is the thing under test rather than a
            by-product.

    Returns:
        The appended assertion row.
    """
    row = reassert_balance_on(db.session, account, at)
    if books_open_on is None:
        open_books_before_the_first_assertion(db.session, account)
    else:
        restate_account_opening(db.session, account, books_open_on)
    return row


def _assert_balance(account, balance, at, recorded_at=None):
    """Append one balance ASSERTION (true-up) at a pinned instant (shared).

    *recorded_at* separates the two clocks: *at* supplies the BUSINESS day,
    *recorded_at* the moment it was typed.  They are equal unless a test is
    exercising a back-dated assertion.
    """
    return append_balance_assertion(
        db.session, account, balance, at, recorded_at=recorded_at,
    )


def _money_facts(facts):
    """Return the facts that CARRY money -- a covered row's own fact is zero.

    Plan step **X-bi-3a**: a settled bill's money sits on the covering
    movement the seam writes for it, so the fold emits the bill's own fact at
    ``0.00`` beside the movement's fact carrying the figure on the same day.
    Every day and every delta a case in this package asserts is unchanged; what the
    shape assertions read is the steps that move the balance.
    ``balance:X-bi-4`` re-points the fold onto movements and the zero step
    goes with the row's leg.
    """
    return [fact for fact in facts if fact.delta]


def _money_steps(walk):
    """The walk's ``(day, delta)`` steps that carry money -- see :func:`_money_facts`."""
    return [(day, delta) for day, delta in dated_deltas(walk) if delta]


def _corrections(account, scenario):
    """Return ``{asserted_at: (balance_before, delta)}`` for readable asserts.

    Through ``balance_at._assertions.assertion_corrections``, which owns the
    RESET replay since plan step X-f3c-1 -- the walk states the facts and the
    fold states what an assertion does to a running total (ruling R-FO gives
    the modelled kinds one answer and plan step X-f3c gives the PLAIN ones
    another, so a kind-blind walk could state neither).  Every figure this
    package's cases assert is unchanged by that move, which is what makes it a move.
    """
    walk = walk_cash_ledger(account.id, scenario.id)
    return {
        correction.anchor.asserted_at: (
            correction.balance_before,
            correction.anchor.anchor_balance - correction.balance_before,
        )
        for correction in assertion_corrections(walk)
    }


def _running_balance(account, scenario):
    """Return the replay's final running balance from the fold's own three tiers.

    The account's stored OPENING EQUITY, plus ``dated_deltas`` (the SOURCE
    facts), plus the assertion corrections -- which is exactly what
    ``_cash_fold._actual_steps`` merges.  It was ``dated_deltas`` alone until
    plan step X-f3c-1, when the assertion steps left the leaf because applying
    them is a policy an account's KIND decides (see :func:`_corrections`).

    **The SEED term arrived at plan step X-f3c-2a and its absence before then
    was not an omission**: the fold seeded at the opening's own correction and
    booked an equal-and-opposite compensator, so the two cancelled and the
    total equalled the zero-seeded walk.  With opening equity a stored fact
    there is no compensator, so the seed is a real term and a reconstruction
    that drops it is short by the whole of it.

    **This total is INVARIANT in the opening equity, and saying so is the
    honest version of a claim an earlier draft got wrong.**  The first
    assertion's delta is ``A - E - cleared``, so the leading ``E`` cancels
    against the ``-E`` inside it: perturb the stored equity by any amount and
    every figure here is unchanged.  That is a real property of the REPLAY --
    an assertion resets the total, so what preceded it cannot survive -- and it
    is exactly why "the figures did not move" is NOT evidence that this step
    moved no money.  A control on the SEED has to read a date BEFORE the first
    assertion, which is what
    ``test_cash_fold/test_the_opening_equity_is_the_seed.py``'s
    :class:`TestTheOpeningEquityIsTheSeed` does.

    :class:`TestTheStepsReconstructTheReplay` proves this equals the replay's
    own terminal balance by reconstructing that balance INDEPENDENTLY from the
    last assertion FACT + the post-assertion ``source_facts``, so this helper
    is a convenience rather than the only statement of the total.
    """
    walk = walk_cash_ledger(account.id, scenario.id)
    return walk.opening.opening_equity + sum(
        (delta for _day, delta in dated_deltas(walk)),
        Decimal("0.00"),
    ) + sum(
        (correction.delta for correction in assertion_corrections(walk)),
        Decimal("0.00"),
    )


def _fold_at(account, scenario, day):
    """Return the balance a SCREEN renders for *account* on *day*.

    The real producer (the pass's own ``assembled_fold``, sampled by
    ``balances_at``), not a hand-rolled prefix sum, because the property under
    test is what a user sees
    and a replay written beside the thing it grades can agree with a wrong
    answer.

    **This module's other helpers read the WALK, and that difference is what an
    adversarial review caught.**  ``balance_before`` is an INPUT to a
    correction; the fold prefix-sums ``dated_deltas``, which emits each source
    at its OWN settle day.  A first version of the clearing-link tests graded
    the corrections alone, and was green while the rendered balance on an
    assertion's own day was ``$500.00`` short of what the user had asserted.

    Args:
        account: The account to value.
        scenario: Its scenario.
        day: The valuation date, passed as the reader's as-of too -- the
            ordinary case, and it keeps the PLANNED tier out of the way (ruling
            R-G clamps a plan to ``as_of + 1``).

    Returns:
        The folded balance as a ``Decimal``.
    """
    ctx = read_pass(account, scenario, day)
    return balances_at(assembled_fold(account, ctx), [day])[day]


def _replay_terminal_balance(account, scenario):
    """Reconstruct the walk's end balance WITHOUT :func:`dated_deltas`.

    The independent reference: take the LAST assertion's asserted balance and
    add every source dated strictly AFTER the day that assertion closes.  That
    is the replay's definition (ruling R-DH (a)) read off
    :class:`CashLedgerWalk`'s two FACT lists directly -- no correction, no step
    list -- so comparing it against the summed steps is a real cross-check
    rather than a producer graded on itself.

    The comparison is ``>`` on the civil DAY, not on an instant: a source
    sharing the assertion's day is inside the closing balance, so only a
    strictly later day rides on top.
    """
    walk = walk_cash_ledger(account.id, scenario.id)
    if not walk.anchor_facts:
        return Decimal("0.00")
    last = walk.anchor_facts[-1]
    return last.anchor_balance + sum(
        (
            fact.delta for fact in walk.source_facts
            if fact.settled_on > last.observed_on
        ),
        Decimal("0.00"),
    )
