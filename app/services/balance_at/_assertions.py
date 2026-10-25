"""Balance-at-T seam -- what a balance ASSERTION does to a running total.

Plan step **X-f3c-1** (shipped at ``2dad8512``).
The replay this module owns lived inside
:func:`app.services.cash_ledger.walk_cash_ledger` until then, and moving it here
is not a tidy-up: it is what makes the CUTOVER expressible.

**An assertion's effect on a balance is a POLICY, and the policy differs by
account kind.**  Ruling **R-FO** keeps the RESET for the modelled kinds -- an
IRA has no record of a price movement to discard, so the user's declaration is
the only fact and re-deriving the correction on every read IS mark-to-market --
while plan step **X-f3c** deletes it for the PLAIN ones, where the recorded
movements are the fact and an assertion is a CHECK against them (ruling
**R-FN**).  Two answers, one question.

The walk could state neither.  Ruling **R-J** makes it kind-blind, so it cannot
branch; and while it hardcoded the reset it imposed the modelled kinds' answer
on the cash ones, which is exactly the coupling that made the cutover a rewrite
of a leaf instead of a deletion in a fold.  So the replay lives HERE, beside the
running total it is a policy about, and each fold applies what its own kind
needs: :mod:`._cash_fold` for PLAIN, :mod:`._asset_fold` for the modelled kinds.

**What plan step X-d must decide, named here rather than discovered there.**
That step wires the POSTING writer onto the cash walk, and each of these
corrections is what it books as a balanced journal entry.  It can no longer
reach them: ``account_posting_service`` importing ``balance_at._assertions`` is
a private-module import in every spelling (W9910,
:mod:`tools.pylint.shekel_checkers.package_privacy` -- no allowlist, and
``TYPE_CHECKING`` is not exempt).  So X-d chooses between giving the seam a
PUBLIC entry the writer takes, and having the writer re-implement the replay --
which would recreate the two-independent-statements drift the walk's own
"one walk closes it by construction" exists to remove.  The choice is smaller
than it looks after plan step X-f3c-5: a PLAIN account books no
``account_trueup`` at all there, so what survives is the modelled kinds' half.
The RESIDUE decision two paragraphs down in :mod:`app.services.cash_ledger._walk`
is named for the same reason (``CLAUDE.md`` rule 8).

**It is a prefix sum, which is the other reason it is here.**
:attr:`CashAnchorCorrection.balance_before` is the account's balance an instant
before an assertion -- a balance-at-T by any reading -- and the cash ledger's
producer set is EMPTY and stays empty (the W9909 registry's own words).  Stating
it in a private ``balance_at`` submodule is the placement that classification
exists to force, rather than a non-producer ruling that would have to argue a
running total is not a balance.

Boundary discipline (``CLAUDE.md``): no Flask symbol, no query, no write; all
money is :class:`~decimal.Decimal`.
"""

from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from app.services.cash_ledger import CashAnchorFact, CashLedgerWalk

_ZERO_MONEY = Decimal("0.00")


@dataclass(frozen=True)
class CashAnchorCorrection:
    """One assertion's balance correction: an opening or a true-up.

    The per-assertion result of :func:`assertion_corrections`.  The correction's
    delta is ``anchor_balance - balance_before``: the jump the user's assertion
    booked over what the recorded facts alone would have produced.  A correction
    whose ``balance_before`` already equals the asserted balance books nothing,
    which is the healthy steady state -- an account whose every movement is
    recorded needs no correction at all.

    On the write side (plan step X-d) each of these becomes a balanced journal
    entry, the opening tagged ``account_opening`` and every later one
    ``account_trueup``, exactly as
    :class:`app.services.account_posting_service.AccountAnchorCorrection` is
    today -- subject to the reachability decision the module docstring names.

    Attributes:
        anchor: The :class:`~app.services.cash_ledger.CashAnchorFact` this
            correction books for.
        balance_before: The replay's running balance JUST BEFORE this assertion
            resets it -- the account's OPENING EQUITY plus every source this
            assertion or an earlier one cleared, on top of the prior
            assertions.  It starts from the stored opening rather than from
            zero since plan step **X-f3c-2a**, which is what makes the FIRST
            assertion an ordinary correction: for an account whose books opened
            at the level its records imply, the opening's ``delta`` is
            ``0.00``, where it used to be the whole opening equity that the
            fold then had to back-project.
    """

    anchor: CashAnchorFact
    balance_before: Decimal

    @property
    def observed_on(self) -> date:
        """Return the civil day this correction is the closing balance FOR.

        The assertion twin of
        :attr:`~app.services.cash_ledger.CashSourceFact.settled_on`, and the
        same rule: the day is resolved ONCE on the fact this correction wraps
        (ruling R-DH) and every consumer reads that one field rather than
        re-deriving one from a timestamp.

        Returns:
            The civil day of the assertion this correction books.
        """
        return self.anchor.observed_on

    @property
    def delta(self) -> Decimal:
        """Return the jump this assertion's RESET booked over the records.

        ``anchor_balance - balance_before``: what the user's declaration moved
        the running balance by, on top of what the recorded facts alone had
        produced.  ``0.00`` for the healthy steady state (an account whose every
        movement is recorded needs no correction).

        **Stated here so it is stated ONCE.**  Three readers need this pair --
        the fold's step list (``_cash_fold._actual_steps``), its R-I seed, and
        the period view's assertion component
        (``_cash_periods.period_view_of``, plan step X-c1) -- and until X-c1 the
        fold RE-DERIVED it, which its own docstring had to pin with a test.  A
        property on the record retires that re-derivation, exactly as
        :class:`~app.services.cash_ledger.CashSourceFact` already carries its
        own ``settled_on`` / ``delta``.

        Returns:
            The signed correction as a ``Decimal``, in the same LEDGER-NATIVE
            sign as the asserted balance.
        """
        return self.anchor.anchor_balance - self.balance_before


def assertion_corrections(
    walk: CashLedgerWalk,
) -> list[CashAnchorCorrection]:
    """Replay *walk*'s facts under the RESET, returning one correction each.

    Seeds the running balance at the account's stored OPENING EQUITY
    (:attr:`~app.services.cash_ledger.CashLedgerWalk.opening`) and, per
    assertion in business-date order, advances it by every settled source that
    assertion CLEARED
    (:meth:`app.services.cash_ledger.StatementCoverage.clearing_anchor_id`),
    records what the balance was JUST BEFORE, then RESETS it to the asserted
    value.

    **The seed is a RECORDED FACT since plan step X-f3c-2a, and that is what
    makes every assertion the same kind of thing.**  This replay started at
    zero, so the first assertion's correction came out as the whole of what the
    account held before its records began -- a quantity the fold then had to
    move into its own seed and cancel with an equal-and-opposite step (ruling
    **R-I**), and which was silently re-elected whenever an assertion was
    BACK-DATED, because "the opening" was decided by sort position.  Reading
    the level from ``budget.account_openings`` deletes the special case rather
    than relocating it: ``corrections[0]`` is now an ordinary correction whose
    delta is the difference between what the owner declared and what the books
    say, ``$0.00`` on every production account the migration seeded.

    **Resetting at EVERY assertion -- not seeding from the latest one -- is what
    makes the past the assertion history.**  The shipping projection read only
    the newest anchor (:func:`app.services.cash_ledger.resolve_anchor`) and
    carried it BACKWARD over the past, which is why a pre-anchor date read
    today's balance rather than the balance the user actually asserted then
    (finding B-18 / cash D3: measured on production 2026-07-25, the scalar
    answers ``$2,932.41`` for 2026-06-03 while the period map omits those 8
    periods entirely).  Replaying all of them -- **61 on the real Checking
    account, measured on a production clone 2026-08-27** against the 52 over 119
    days ``._events.cash_anchor_facts`` records for 2026-07-25 -- means the past
    is what the user recorded rather than a back-projection of the present.

    **A settled source attributed AFTER the latest assertion rides on top of it,
    and that is the money the app currently loses.**  Today's shipping
    projection excludes every settled row (the anchor is assumed to reflect
    them) and the anchor predates them, so they are counted by NO producer until
    the user re-asserts the balance: measured on production 2026-07-25,
    ``$2,108.15`` invisible at that instant, and ``$53,880.81`` gross across 130
    rows over 45 assertion gaps historically (finding cash D1).  Here such a
    source is in no bucket at all and the fold's own step list carries it.

    **Which assertion clears a source is ASSIGNED, not scanned, and that is
    ruling R-FL** (plan step X-f3a-1).  This advanced a monotonic ``absorbed``
    pointer through the day-sorted sources while the current assertion
    ``covers`` them, which was correct only while "cleared by this assertion"
    was monotone in the day.  A RECORDED clearing fact is not: statement B
    legitimately clears a line dated before one of statement A's, and a pointer
    meeting that line halts and silently shorts every later assertion.  So
    :class:`~app.services.cash_ledger.StatementCoverage` answers per source,
    this loop groups by the answer, and the loaders' ordering stops being a
    precondition of the money being right.  Two assertions about ONE day are
    still not a conflict: an unlinked source lands on the first of them, the
    second absorbs nothing, and the LAST is that day's closing balance -- which
    is what a user re-reading their bank later the same day means.

    **The loaders' ordering is still a contract, for a smaller reason.**
    ``cash_anchor_facts`` returns assertions ascending by
    ``(observed_on, created_at, id)`` -- BUSINESS date first, which is what the
    coverage rule bisects and what makes "the FIRST is the opening" true -- and
    ``settled_cash_facts`` returns sources ascending by
    ``(settled_on, entry_id)``, which keeps the replay
    reproducible.  Neither is load-bearing for WHICH assertion absorbs what.

    **It is not the whole of what an assertion means, and plan step X-f3c is
    why.**  A caller applying these deltas to its step list has chosen the RESET
    (ruling R-S, "an assertion always wins"), which is correct for the modelled
    kinds and is what the cutover deletes for the PLAIN ones.  A PLAIN caller
    after that step still asks for these corrections -- the difference between
    what the user declared and what the records hold is the RESIDUAL it
    displays -- and simply does not book them as steps.

    Args:
        walk: The account's :class:`~app.services.cash_ledger.CashLedgerWalk`.

    Returns:
        One :class:`CashAnchorCorrection` per assertion, in the walk's
        business-date order, so ``[0]`` is the OPENING.  Empty for a walk with
        no assertions.
    """
    coverage = walk.coverage
    cleared: dict[int, Decimal] = {}
    for source in walk.source_facts:
        anchor_id = coverage.clearing_anchor_id(source)
        if anchor_id is not None:
            cleared[anchor_id] = cleared.get(anchor_id, _ZERO_MONEY) + source.delta

    corrections: list[CashAnchorCorrection] = []
    # The books start at what the account HELD before its records begin, a
    # recorded fact since plan step X-f3c-2a rather than a constant this replay
    # backed out of its own first assertion.  Seeding at zero is what made the
    # opening's correction the whole opening equity and forced the fold to
    # back-project it (ruling R-I); from the real level every assertion is one
    # kind of thing, and the FIRST is no longer special.
    running = walk.opening.opening_equity
    for anchor in walk.anchor_facts:
        running += cleared.get(anchor.anchor_id, _ZERO_MONEY)
        corrections.append(
            CashAnchorCorrection(anchor=anchor, balance_before=running)
        )
        # The assertion resets the walked total to the asserted balance --
        # the user's declaration outranks the recorded facts before it.
        running = anchor.anchor_balance
    return corrections
