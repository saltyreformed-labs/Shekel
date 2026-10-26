"""The loan walk: ONE running-balance replay over ONE event stream -- FACTS.

The single chronological walk a loan's whole architecture derives from.  It
seeds the balance at zero and folds the loan's three kinds of fact
(:mod:`._events`) through the ONE replay (:mod:`._replay`): each accrual period
CHARGES the loan, each settled payment's cash ALLOCATES against what stands, and
each anchor RESETS the balance -- so the loan's opening, every true-up, and every
payment split come from ONE running balance and can never disagree on the balance
interest accrued on, the way three independent walks could.

**The walk yields FACTS, not a balance-at-T.**  Its output is a
:class:`LoanLedgerWalk` -- one :class:`~._replay.PaymentOutcome` per payment
and one :class:`LoanAnchorCorrection` per anchor, in CONTRACT-time order.
Turning those facts into "what is owed on date D" is the FOLD -- re-key each event
by the date it becomes VISIBLE (:func:`dated_deltas`, here since plan step E1a
because BOTH sides consume it), then prefix-sum the paydowns -- and the prefix-sum
lives in the balance seam (:mod:`app.services.balance_at._fold`) as of plan step
**D-fold**, not here.  A consumer holding a walk therefore cannot reach a balance
from a public leaf name, which is why the walk needs no fence (the sampling that
would turn the dated facts into money is seam-private).

**Two consumers, one walk.**  The posting writer
(:mod:`app.services.loan_posting_service`) projects this walk into the balanced
corrections it reconciles onto the general ledger; the seam's read pass folds it
into a balance at a date.  The walk is the leaf both depend on, which is what
makes the posted ledger a re-derivable projection of the loan's facts rather
than a second opinion about them.

**Takes no as-of, and reads no clock**: it walks the loan's FACTS and records
every one of them, whatever their date.  Its output is therefore a function of
the loan's data ALONE -- which is what makes it re-derivable -- and deciding
which facts have HAPPENED as of a date belongs to a reader (the seam's fold).  The
cash ledger's walk
(:func:`app.services.account_posting_service.walk_account_ledger`) has taken no
as-of since Step 3; the loan half caught up at step A3 (``4e46a0a8``).

**The same walk carries the loan's PROJECTIONS when the seam appends them**
(plan step **recurrence:R16-c-1**, ruling **R-R90**): :func:`replay_loan_stream`
is the pure replay over any :class:`~._replay.LoanEventStream`, and a stream
holding the forward plan's payments behind the recorded facts yields the loan's
WHOLE timeline from one seed, the origination.  Nothing above changes for the
posted ledger -- :func:`walk_loan_ledger` builds no projection, so its walk is
still the facts alone -- and a projection cannot alter a recorded payment's
split, because the replay never places one ahead of a fact
(:func:`._replay.projection_boundary`).

Reads the loan's rows; no writes, no commit.
"""

from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from app.services import loan_loaders
from app.services.loan_loaders import LoanAnchorFact

from ._calendars import LoanCalendars
from ._events import confirmed_shadows_through, loan_event_stream
from ._replay import LoanEventStream, PaymentOutcome, replay_loan_events
from ._visible import anchor_visible_on

_ZERO_MONEY = Decimal("0.00")


@dataclass(frozen=True)
class LoanAnchorCorrection:
    """One anchor's genesis balance correction: an opening or a true-up.

    The per-anchor result of the fold's walk (:func:`walk_loan_ledger`).  Every
    anchor a loan carries -- the one opening and any ``user_trueup`` events --
    drives the running balance to the anchor's verified value at the anchor's
    date, and the walk amortizes forward from there.  The posting writer turns
    each of these into a balanced correction: the opening anchor's is the loan's
    OPENING (its ``owed_before`` is zero, so it books ``-original_principal``
    onto the loan and ``+original_principal`` onto the loan's opening-equity
    account); a ``user_trueup`` anchor's is the append-only TRUE-UP that
    reproduces the resolver's balance jump without editing any prior posting.

    The correction's loan-linked leg is ``owed_before - anchor_balance`` (its
    equity leg the negative), so the two sum to zero and the ledger's implied
    ``owed`` moves from ``owed_before`` to ``anchor_balance``.  A correction whose
    ``owed_before`` already equals the anchor balance (a true-up that matches the
    walked balance) books nothing.

    Attributes:
        anchor: The :class:`~app.services.loan_loaders.LoanAnchorFact` this
            correction books for -- supplies ``anchor_balance`` (the verified
            value the balance resets to), ``anchor_date`` (the correction's civil
            entry date), ``is_opening`` (origination vs. user-trueup, which tags
            the leg and journal-entry kinds), and ``account_id``.
        owed_before: The walk's running balance JUST BEFORE this anchor resets it
            -- ``Decimal("0.00")`` for the opening anchor (always the first
            event), the amortized balance carried down from the prior anchor for a
            user-trueup.  The linked-ledger correction is
            ``owed_before - anchor_balance``.
    """

    anchor: LoanAnchorFact
    owed_before: Decimal


@dataclass(frozen=True)
class LoanLedgerWalk:
    """A loan's full walk output for one scenario: every split, every correction.

    The complete output of the single chronological running-balance walk
    (:func:`replay_loan_stream`): the per-payment outcomes AND the per-anchor
    corrections, both in chronological order, sharing ONE running balance so the
    opening, every true-up, and every payment split are guaranteed mutually
    consistent.

    Carries no as-of, because the walk takes none: it is the loan's stream
    replayed, whole, and a reader bounds it to a date by each event's
    ``visible_on`` (see :func:`walk_loan_ledger`).  **Since plan step
    recurrence:R16-c-1 the stream may carry the loan's PROJECTIONS behind its
    recorded facts** (:attr:`~._replay.LoanEventStream.projections`), and this
    is then the loan's whole timeline -- the posted ledger's walk holds the
    facts alone; the seam's holds facts and plan -- and a reader that wants one
    half takes :attr:`settled_splits` or :attr:`projected_splits`.

    Attributes:
        payment_splits: One :class:`~._replay.PaymentOutcome` per payment the
            stream carried -- every settled payment, including one whose pay
            period has not yet begun (settlement is the confirming event; the
            readers' period bound governs display), then every projection --
            chronological in walk order.
        anchor_corrections: One :class:`LoanAnchorCorrection` per anchor the loan
            carries (its opening + every user-trueup), whatever its date,
            chronological.  A reader that shows them applies its own display
            bound.
        stream: The :class:`~._replay.LoanEventStream` this walk replayed.
            Carried so a caller holding the walk can replay the SAME events
            again under a what-if (:func:`replay_loan_stream` with an extra) or
            append projections to a copy of it, without loading the loan's rows
            a second time.
    """

    payment_splits: list[PaymentOutcome]
    anchor_corrections: list[LoanAnchorCorrection]
    stream: LoanEventStream

    @property
    def settled_splits(self) -> list[PaymentOutcome]:
        """The outcomes of the RECORDED payments -- the posted ledger's half."""
        return [
            outcome for outcome in self.payment_splits
            if not outcome.is_projected
        ]

    @property
    def projected_splits(self) -> list[PaymentOutcome]:
        """The outcomes of the payments that have NOT happened -- the plan's half."""
        return [
            outcome for outcome in self.payment_splits if outcome.is_projected
        ]


def replay_loan_stream(
    stream: LoanEventStream, extra_per_period: Decimal = _ZERO_MONEY,
) -> LoanLedgerWalk:
    """Replay a loan's event stream into its walk: every split, every correction.

    The ONE walk, pure: it hands *stream* to
    :func:`._replay.replay_loan_events` -- the ONE rule, seeded at zero because a
    loan's first event is always its opening assertion -- and maps each reset's
    outcome onto the :class:`LoanAnchorCorrection` the posting writer books.  The
    payment outcomes are read as the replay produced them; the per-field copy
    the walk used to make of each (``LoanPaymentSplit``) was a second home for
    the allocation's value and is deleted at plan step recurrence:R16-c-1.

    **Each payment's accrual-period charge comes off its own outcome**, because
    the replay already knows which charge stood over it.  Re-deriving that pairing
    here -- by matching an installment slot between charge and payment, which is
    how this was first built -- states a SECOND association rule that agrees with
    the replay's only by construction.  An adversarial review measured that, and
    the remedy was to return the association rather than recompute it.

    Public since plan step recurrence:R16-c-1, when the seam's forward readers
    stopped running a replay of their own: they take a walk holding the loan's
    facts AND its projections, and a what-if re-runs this over the same
    ``walk.stream`` with the extra.

    Args:
        stream: The loan's :class:`._replay.LoanEventStream`
            (:func:`._events.loan_event_stream`, with or without projections).
        extra_per_period: The hypothetical per-period extra the replay takes;
            ``0.00`` -- the default -- walks the stream as it stands.

    Returns:
        The :class:`LoanLedgerWalk`.
    """
    replay = replay_loan_events(
        _ZERO_MONEY, stream, extra_per_period=extra_per_period,
    )
    return LoanLedgerWalk(
        payment_splits=replay.payments,
        anchor_corrections=[
            LoanAnchorCorrection(
                anchor=outcome.event.source,
                owed_before=outcome.balance_before,
            )
            for outcome in replay.resets
        ],
        stream=stream,
    )


def load_loan_stream(
    loan_account_id: int,
    scenario_id: int,
    terms: LoanCalendars,
    *,
    visible_by: date | None = None,
) -> LoanEventStream:
    """Load a loan's recorded facts into its :class:`~._replay.LoanEventStream`.

    The LOAD half of :func:`walk_loan_ledger`, public since plan step
    recurrence:R16-c-1 so the seam's per-pass walk can take the facts VISIBLE
    by its ``as_of`` (ruling **R-R91**) and replay them; the ledger's walk
    takes every fact.  Nothing here reads the clock.

    **The visibility bound, stated once.**  With *visible_by* a date, a
    payment enters iff its cash had moved by it -- its SETTLED day, the one
    clock (:func:`._events.confirmed_shadows_through`, the same set the
    balance readers count as confirmed) -- and an assertion iff its own date
    has arrived (:func:`._visible.anchor_visible_on`), EXCEPT the opening,
    which always enters: a loan's origination assertion is a term of the
    note -- the balance it will owe the day it closes, synthesized from the
    immutable params -- not an observation made on a day, and a loan
    configured to close next month projects its debt from it today.  That is
    the fork the retired forward seed made ("the fold correctly reports
    ``0.00`` owed; the projection still has to know what it will owe the day
    it closes"): the opening enters the stream, the fold still reads ``0.00``
    before its date (its correction is keyed at that date), and the
    projection behind it starts from it.  A fact dated after *visible_by*
    has not happened for that pass, so a pass pinned to an earlier day
    answers what the loan looked like on that day rather than what was
    recorded after it (plan step ``recurrence:R7d-h``: the pass decides which
    crossing answers).  The charge calendar is the contract's through the
    last fact that enters (:func:`._replay.with_contract_charges`, ruling
    R-R100), so a dropped payment leaves its installment charged and unpaid
    exactly as a never-recorded one would.  For a pass whose
    ``as_of`` is on or after every recorded fact -- every production pass,
    and the only shape the write doors admit (ruling R-EJ refuses a future
    settle day; the anchor doors bound their date) -- the bound drops nothing
    and this is the ledger's own stream.

    **"Every production pass" is a CENSUS, not a construction** (2026-09-30,
    plan step recurrence:R16-c-2's ruling R-R117): this function has two
    callers, :func:`walk_loan_ledger` (no bound) and the seam's per-pass walk
    (``BalanceContext.loan_walk``, bounded by the pass's ``as_of``), and every
    production pass is built at today.  The census rests on what no code
    enforces: ``BalanceContext.build`` accepts any ``as_of``; the tax report's
    display-timezone today equals the server's only while production pins its
    timezone; and a write request built just before midnight could meet a
    tracking start committed just after it (untraced).

    It matters beyond the balance.  A pass pinned BEFORE a loan's first
    tracking start does not see that start, so a payment due before the start
    whose money moved before the pass's day splits there as it did before
    R-R117 (every unrecorded month since origination), while the ledger's
    walk splits it by R-R117.  Such a payment needs money that moved before
    the start, which the tracking-start doors refuse when they write it
    (rulings R-R114, R-R115); it arises only from a settle day moved or
    recorded earlier than the start after the start was written, or from a
    row held before those refusals existed.

    **The loan's params and calendar come from the caller's pass**
    (:class:`._calendars.LoanCalendars`, plan step recurrence:R25), so a pass
    that also prices the loan's payments reads the SAME calendar its charges
    are built from (ruling **R-R105**); the ledger's own walk hands a fresh
    one.

    Args:
        loan_account_id: The loan account whose facts to load.
        scenario_id: The budget scenario the payments live in.
        terms: The read pass's :class:`._calendars.LoanCalendars`.
        visible_by: ``None`` for every recorded fact (the ledger's walk); a
            date for the facts a pass reading on that day has seen.

    Returns:
        The loan's recorded stream -- EMPTY (no charge, no payment, no reset)
        when the loan has no :class:`~app.models.loan_params.LoanParams`, the
        N1 guard; a configured loan always has at least its origination
        assertion, which is synthesized.
    """
    params = terms.loan_params_of(loan_account_id)
    if params is None:
        # Not a configured loan yet (e.g. a payment settled before its
        # LoanParams was created); nothing to walk until it is resolvable.
        return LoanEventStream(charges=(), payments=(), resets=())
    # The origination anchor is SYNTHESIZED from the immutable params, so a
    # configured loan ALWAYS has at least one fact -- the old "no anchor
    # events" degenerate-fixture guard is structurally unreachable now.
    anchor_facts = [
        fact for fact in loan_loaders.load_loan_anchor_facts(params)
        if visible_by is None
        or fact.is_opening
        or anchor_visible_on(fact.anchor_date) <= visible_by
    ]

    # The loan's ONE calendar (ruling R-R100), read before the payments load
    # because the visibility bound reads it too: a ``$0.00`` close is visible
    # from the installment it skips (ruling R-BAL139), its interval's on this
    # calendar (ruling R-R107) -- the installment the replay charges it
    # against.  Its escrow lines carry their full version history, so each
    # accrual period's escrow is resolved on the period's own date and a later
    # escrow change never re-splits a past payment (plan Section 2 / D3).
    calendar = terms.loan_calendar_of(loan_account_id)
    # The stream reads each settled payment's LEG: its parent's due date and
    # pay period (the producer loads the period as its sort key) and its
    # RECORD, the covering movement the producer's one join attaches (plan
    # step balance:X-bi-6-4b).  So it states no load: it traverses no pricing
    # relationship (plan step balance:X-bl-2a), and the shadow's ENTRIES it
    # loaded while the shadow was the payment are not read.  The name
    # ``shadows`` is the shadow era's.
    shadows = (
        loan_loaders.settled_income_legs(
            loan_account_id, scenario_id, options=(),
        )
        if visible_by is None
        else confirmed_shadows_through(
            loan_account_id, scenario_id, visible_by,
            calendar.origination_date, calendar.payment_day,
        )
    )
    return loan_event_stream(anchor_facts, shadows, calendar)


def walk_loan_ledger(
    loan_account_id: int, scenario_id: int,
) -> LoanLedgerWalk:
    """Replay a loan's anchors and settled payments into one running balance.

    The SINGLE chronological running-balance walk the whole loan architecture
    derives from.  Seeds the running balance at zero and folds the loan's event
    stream (:func:`._events.loan_event_stream`) through the ONE replay
    (:func:`._replay.replay_loan_events`), which applies three kinds of fact in
    contract order:

    * At a CHARGE (an installment fell due): accrue the period's interest on
      the running balance and impound its escrow, so both stand against the
      loan until cash clears them.  **One charge per CONTRACTUAL installment
      from origination, not one per payment** (plan step recurrence:R16-c-2,
      rulings R-R72 and R-R100; one per occupied period since plan step
      X-au-g-2c-3b-2): a month nobody paid is charged and the next payment
      clears it first, and a second payment inside one installment's interval
      clears no fresh charge and pays pure principal.  A month on or before the
      loan's first balance, when that balance is a tracking start, is dropped
      instead unless a payment of its own clears it (ruling R-R117).
    * At a settled PAYMENT -- INCLUDING one whose pay period has not yet begun
      (settlement is the confirming event; see
      :func:`~app.services.loan_loaders.settled_income_legs`) -- allocate its
      ACTUAL cash against whatever stands, into interest / escrow / principal /
      excess, then advance the balance.
    * At an ANCHOR (opening or user-trueup): record a
      :class:`LoanAnchorCorrection` carrying the balance JUST BEFORE the reset,
      then reset the running balance to the anchor's verified value.  The opening
      anchor is always the first event, so its ``owed_before`` is zero.

    Resetting at EVERY anchor -- rather than seeding from the latest anchor only,
    as the resolver does -- is what lets a from-origination sum-of-postings
    reproduce the resolver penny-for-penny on a TRUED-UP loan: the pre-anchor
    payment corrections cancel against the anchor correction, leaving
    ``verified - sum(post-anchor principal)`` (the resolver's own value), while
    the pre-anchor payments' interest still lands in the interest ledger.  On a
    single-origination-anchor loan the reset is a no-op and the walk equals the
    from-origination replay.

    **Takes no as-of, and reads no clock** (see the module docstring).  Each
    accrual period's rate and escrow are those IN EFFECT ON the period's own date
    -- its contractual installment (effective-dated, NO inflation, ruling D5's
    contract time) -- so a later escrow or rate change never re-splits a past
    payment.  Reads only (no writes, no commit).

    Args:
        loan_account_id: The loan account whose ledger to walk.
        scenario_id: The budget scenario the payments live in.

    Returns:
        A :class:`LoanLedgerWalk` (payment splits + anchor corrections, both
        chronological).  Both lists are empty when the loan has no
        :class:`~app.models.loan_params.LoanParams` (not yet resolvable -- the N1
        guard); a configured loan always walks, since its origination anchor is
        synthesized.
    """
    return replay_loan_stream(
        load_loan_stream(loan_account_id, scenario_id, LoanCalendars()),
    )


def compute_loan_payment_splits(
    loan_account_id: int, scenario_id: int,
) -> list[PaymentOutcome]:
    """Return the real split of a loan's settled payments from origination.

    The payment-split view of :func:`walk_loan_ledger`: one
    :class:`~._replay.PaymentOutcome` per settled payment (whatever its pay
    period), in chronological order, each dividing its ACTUAL cash into interest /
    escrow / principal / excess on the reset-aware running balance (see
    :func:`._replay.replay_loan_events` for that arithmetic).  Because
    principal is ``cash - interest - escrow``, an extra or short payment lands in
    principal automatically -- the cash is the authority, where the resolver's
    contractual replay discards it and needs an anchor true-up.

    Unlike the resolver it does NOT stop at payoff: every Step-2 cash entry gets a
    matching correction, with post-payoff cash routed to Refund, so the ledger
    stays complete.  Reads only (no writes, no commit).

    Args:
        loan_account_id: The loan account whose settled payments to split.
        scenario_id: The budget scenario the payments live in.

    Returns:
        One :class:`~._replay.PaymentOutcome` per settled payment, in
        chronological (pay-period-start) order.  Empty (``[]``) when the loan has
        no :class:`~app.models.loan_params.LoanParams` (not yet resolvable -- the
        N1 guard) or no settled payment.
    """
    return walk_loan_ledger(loan_account_id, scenario_id).settled_splits


def dated_deltas(walk: LoanLedgerWalk) -> list[tuple[date, Decimal]]:
    """Return the walk's ``(visible_on, delta)`` steps, ascending by visible date.

    The bridge from the walk (events ordered by when they HAPPENED, in CONTRACT
    time) to the day each event COUNTS from (step C2's one clock).  Each event
    contributes the amount it moved the running owed balance by:

    * an anchor: ``anchor_balance - owed_before`` -- the jump its reset booked;
    * a payment: ``-principal`` -- the debt its cash actually paid down.

    Negated, these are exactly the amounts the posting writer books onto the
    loan's linked ledger (the debit-positive convention), each at this same
    visible date (the ``entry_date`` its entry carries).  TWO consumers, ONE
    derivation -- the reason this lives on the leaf (plan step E1a) rather than
    in either consumer:

    * the balance seam's fold prefix-sums the deltas and samples a date
      (:func:`app.services.balance_at._fold.fold_from_walk`);
    * the posting writer's checked-projection assert compares the posted
      per-date linked nets against them after every reconcile
      (:func:`app.services.loan_posting_service.sync_loan_postings`).

    A third statement of "which day does this event count from, and for how
    much" is exactly how the fold and the posted ledger would drift apart --
    the divergence the assert exists to catch -- so the assert must not carry
    its own copy of the rule.

    **The deltas are computed in EVENT (contract) order and then re-keyed by
    VISIBLE date, and that is deliberate.**  A payment's split depends on the
    balance at its installment, so the walk runs in due-date order
    (:func:`._replay.replay_loan_events`, over
    :func:`._events.loan_event_stream`); the ledger stores those
    amounts, and a reader counts whichever are visible.  Under the one clock a
    payment's visible date is its SETTLED date and an anchor's is its own date
    (:mod:`._visible`) -- the same day each posting carries in ``entry_date``
    -- so a late-settled payment's principal is shown from the day its cash
    moved, while its split stays fixed to the installment it paid.  A
    PROJECTION's is its effective day (``max(due, as_of + 1d)``, ruling D1),
    carried on its event by the seam that built it, so a walk holding the
    loan's plan dates its future paydowns here by the same rule and one
    prefix-sum values the whole timeline (plan step recurrence:R16-c-1).

    **Dated FACTS, not a balance-at-T** (the fence ruling): each pair says
    what ONE event contributed and when it counts -- amounts already readable
    off the public splits and corrections.  Turning them into "what is owed on
    date D" is the prefix-sum, which stays seam-private
    (:func:`app.services.balance_at._fold.sample_cumulative`).

    Args:
        walk: The loan's :class:`LoanLedgerWalk` (:func:`walk_loan_ledger`).

    Returns:
        ``[(visible_on, delta), ...]`` ascending by ``(visible_on, tag)``,
        where a PAYMENT tags before an ANCHOR on a shared date -- mirroring the
        walk's own tie-break (:func:`._replay.replay_loan_events`), so
        reading the list shows the same chronology the walk applied.  The
        order within a date is immaterial to a prefix sum (addition commutes);
        mirroring it keeps the two chronologies reading identically.
    """
    if not walk.anchor_corrections:
        # No LoanParams -> no facts at all (walk_loan_ledger's N1 guard; a
        # configured loan ALWAYS has its opening fact, per
        # ``load_loan_anchor_facts``).  Nothing to date.
        return []
    # Tag 0 = payment, 1 = anchor: the same tie-break the walk applies, so a
    # payment sharing an anchor's date reads before it here too.
    tagged: list[tuple[date, int, Decimal]] = [
        (
            anchor_visible_on(correction.anchor.anchor_date),
            1,
            correction.anchor.anchor_balance - correction.owed_before,
        )
        for correction in walk.anchor_corrections
    ] + [
        (outcome.visible_on, 0, -outcome.principal)
        for outcome in walk.payment_splits
    ]
    tagged.sort(key=lambda step: (step[0], step[1]))
    return [(visible_on, delta) for visible_on, _tag, delta in tagged]
