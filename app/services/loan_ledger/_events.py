"""The loan fold's EVENT STREAM: what happened to a loan, in the order it happened.

A loan's balance is a fold over its event stream, and this module builds that
stream.  THREE kinds of fact enter it, and nothing else:

* an **ASSERTION** -- the loan's opening (its origination, ALWAYS) plus every
  balance assertion made after it: a mid-life ``tracking_start`` and every user
  balance true-up, all loaded as
  :class:`~app.services.loan_loaders.LoanAnchorFact` and all RESETTING the running
  balance at their own date (a ``tracking_start`` is never the opening -- step C1).
  The kind travels onto the event, because a loan whose first balance is a
  tracking start has the months before it cleared one by one (ruling
  **R-R117**, :func:`.._replay.replay_loan_events`);
* a **PAYMENT** -- the to-side leg of a settled transfer into the loan
  (:func:`~app.services.loan_loaders.settled_income_legs`): its cash is the
  figure the transfer stores, and its covering movement, which a ``$0.00``
  close does not hold, records the day that cash moved;
* a **CHARGE** -- what an accrual period cost the loan
  (:func:`.._charges.contract_charges`), one per CONTRACTUAL installment from
  origination (plan step recurrence:R16-c-2, ruling **R-R100**).  It joined the
  stream at plan step **X-au-g-2c-3b-2**, and it is the fact that stops the
  payment COUNT being the clock: while a month's interest and escrow rode on
  the payment RECORD, N payments inside one month charged N months.

**Every fact enters the stream, whatever its date, and nothing here reads the
clock.**  A loan's anchors are FACTS -- the origination is a verbatim copy of the
immutable :class:`~app.models.loan_params.LoanParams`, a true-up is the
operator's dated assertion -- and this module RECORDS them; deciding which have
HAPPENED as of a given date is a READER's job.  Dropping a future-dated anchor --
what the walk did while it took an ``as_of`` -- made the persisted ledger a
function of the wall clock at the moment the sync happened to run, which is a
corruption generator, not a cache (step A3, ``4e46a0a8``).

**What this module does NOT decide is the ORDER**, and that is plan step
X-au-g-2c-3b-2's division.  ``merge_anchor_and_payment_events`` lived here and
sorted the stream itself; the forward fold sorted its own, and the two were one
rule stated twice.  The order between KINDS is now
:func:`.._replay.replay_loan_events`'s alone, and the order WITHIN a kind is each
loader's, preserved by a stable sort.  What survives here is the mapping: which
rows are facts, which date governs each, and what figure each carries.

The one exception is :func:`confirmed_shadows_through`, which IS a reader's
bound and lives here only because it is the same settled-payment set narrowed:
see its docstring.  Since plan step recurrence:R16-c-1 it is also the payment
half of the pass's visibility bound (:func:`.._walk.load_loan_stream`).
"""

from datetime import date

from app.services import loan_loaders
from app.services.loan_loaders import LoanAnchorFact
from app.services.row_valuation import leg_settled_contribution
from app.services.transfer_legs import TransferLeg

from ._charges import LoanCalendar
from ._replay import (
    LoanCashEvent,
    LoanEventStream,
    LoanResetEvent,
    with_contract_charges,
)
from ._visible import payment_visible_on


def confirmed_shadows_through(
    loan_account_id: int,
    scenario_id: int,
    as_of: date,
    origination_date: date,
    payment_day: int,
) -> list[TransferLeg]:
    """Return the settled payments whose CASH had moved by ``as_of``.

    The DISPLAY subset of
    :func:`~app.services.loan_loaders.settled_income_legs`: the payments the
    balance readers count as confirmed history at ``as_of`` (their shared
    visible-on bound).  It returns LEGS since plan step balance:X-bi-6-4b and
    keeps its shadow-era name until ``X-bi-6-4d``.  The posted ledger's
    payment-history table
    (:func:`app.services.loan_posting_service.confirmed_loan_payment_history`)
    consumed this until plan step ``balance:X-bi-6-3``, when it began reading
    the walk's outcomes by the same ``visible_on`` bound, and since plan
    step recurrence:R16-c-1 it is the payment half of a read pass's visibility
    bound (:func:`.._walk.load_loan_stream`'s ``visible_by``, ruling R-R91);
    the LEDGER's walk deliberately does NOT take it (it splits every settled
    payment -- see :func:`~app.services.loan_loaders.settled_income_legs`
    for why).

    A payment's visible-on date is its SETTLED date (step C2, ruling R-A) --
    or, for a ``$0.00`` close that moved nothing, the installment it skips
    (ruling **R-BAL139**), its interval's (ruling **R-R107**) -- read through
    the SAME
    :func:`._visible.payment_visible_on` the fold uses, so the history rows and
    the fold cannot key a payment on two different days.  The SQL reader that
    must agree with this (:func:`app.services.loan_posting_service`) bounds the
    same postings by their ``entry_date``, which the writer stamps with that
    identical day.

    Args:
        loan_account_id: The loan account whose payments to load.
        scenario_id: The budget scenario to scope to.
        as_of: The display boundary; a payment whose settled date has not arrived
            by it is a forward projection, excluded.
        origination_date: The loan's origination, where its installment
            grid starts, for R-BAL139's day (see
            :func:`._visible.payment_visible_on`).
        payment_day: The loan's contractual day-of-month due day, the day
            that grid falls on and the fallback for a payment storing no
            ``due_date``.

    Returns:
        The settled payments' legs through ``as_of``, ascending by pay-period
        start then transfer id.
    """
    return [
        leg
        # No load stated: a leg's record rides the producer's one join, and
        # this reads ``payment_visible_on`` -- the record's ``settled_on``, or
        # the parent's due date and period -- while its callers read the same
        # legs.  The shadow's ENTRIES were loaded here while the shadow was the
        # payment (plan step balance:X-bl-2a to X-bi-6-4b).
        for leg in loan_loaders.settled_income_legs(
            loan_account_id, scenario_id, options=(),
        )
        if payment_visible_on(leg, origination_date, payment_day) <= as_of
    ]


def loan_event_stream(
    anchor_facts: list[LoanAnchorFact],
    shadows: list[TransferLeg],
    calendar: LoanCalendar,
) -> LoanEventStream:
    """Map a loan's anchors, settled payments and charges into ONE event stream.

    The settled walk's half of the replay: it says WHICH rows are facts, WHICH
    date governs each, and WHAT figure each carries.  The replay
    (:func:`.._replay.replay_loan_events`) then decides the order between kinds
    and folds them.

    **This is CONTRACT time, not cash time.**  A payment is dated by its DUE
    date (:func:`app.services.loan_loaders.loan_payment_due_date`; the
    installment it pays is the one that date falls in,
    :func:`~app.services.installment_calendar.installment_paid_by`), never by when its
    cash settled, so a late or out-of-order settlement can never reorder
    installments or re-split one (ruling R-A).  That derivation is threaded onto
    the event rather than recomputed downstream (plan step E1c), and it is the
    SAME strict ``anchor_date < due_date`` post-anchor boundary the resolver's
    replay uses (:func:`is_confirmed_payment_eligible`, fed the same derivation
    via :attr:`~app.services.amortization_engine.PaymentDates.due_date`) -- the
    two MUST stay on one derivation,
    or the posted ledger and the replayed balance drift on which payments a given
    anchor subsumes.

    **The CHARGES are the CONTRACT's**, every installment from origination
    through the stream's last fact (:func:`.._replay.with_contract_charges`,
    plan step recurrence:R16-c-2, rulings **R-R72** and **R-R100**) -- which
    keeps plan step R16-a's rule, the count of charges cannot depend on the
    count of payments, and ends D53's exception to it: a month nobody paid is
    charged, and the next payment clears those arrears before it reaches
    principal -- except a month on or before the loan's first balance when
    that balance is a tracking start, which the replay drops (ruling
    **R-R117**), save the installment that start lands in, which it charges
    again on its own figure when that installment's own payment walks after
    it and no Record balance shares its day (ruling **R-R118**).  A period
    holding two payments is charged once, and the second
    payment clears nothing fresh and pays pure principal.

    **EVERY input arrives PRE-ORDERED by its own loader, and nothing here or in
    the replay adds a TIE-BREAK WITHIN A KIND** (plan step X-an-b, closing finding
    N-196; the same shape finding N-133 / R1 ruled on the cash side).  A stable
    sort in the replay preserves each loader's key within a shared date -- anchors
    keep :func:`~app.services.loan_loaders.load_loan_anchor_facts`'
    ``(anchor_date, created_at, event_id)``, payments keep
    :func:`~app.services.loan_loaders.settled_income_legs`'
    ``(pay_period.start_date, transfer id)``.  The stream was re-sorted on ``(anchor_date,
    created_at)`` until X-an-b, which was a SECOND statement of a rule the loader
    is now the one home of, and an incomplete one: ``created_at`` is evaluated at
    TRANSACTION START, so two anchors written together shared an instant, the
    re-sort left them in whatever order PostgreSQL returned, and the walk reset on
    the LAST of the tie while the resolver's ``max()`` seeded from the FIRST.

    Args:
        anchor_facts: The loan's :class:`~app.services.loan_loaders.LoanAnchorFact`
            list, PRE-ORDERED by ``(anchor_date, created_at, event_id)``
            (:func:`~app.services.loan_loaders.load_loan_anchor_facts`, which is
            where that order is decided).
        shadows: The settled payments' legs, PRE-SORTED by
            ``(pay_period.start_date, transfer id)``
            (:func:`~app.services.loan_loaders.settled_income_legs`; this
            parameter's name is the shadow era's).  Each leg's
            cash is read through
            :func:`~app.services.row_valuation.leg_settled_contribution` -- the
            accessor whose NAME asserts the payment has SETTLED -- rather than
            a resolver, because every leg here has, so it answers from the
            figure its TRANSFER stores as having moved
            (``leg.transfer.settled_figure``) and there is no plan to reach;
            a ``$0.00`` close stores ``0.00`` (ruling **R-BAL365**), a
            payment of nothing, never a fallback to a forecast, and a leg
            that has not settled at all REFUSES, which is
            what makes the loader's partition a precondition this replay
            states rather than merely relies on.
        calendar: The loan's contract terms
            (:class:`~.._charges.LoanCalendar`): its due day is the fallback
            coordinate for a payment whose transfer stores no ``due_date``,
            its origination and due day place a ``$0.00`` close's visible-on
            day on its interval's installment (rulings **R-BAL139**,
            **R-R107**), and each charge carries the rate period and the
            escrow in force on its own installment date.

    Returns:
        The loan's :class:`~._replay.LoanEventStream` -- its RECORDED facts.
        It carries no projection: the seam appends the forward plan's to a
        copy of this stream (``balance_at._loan_stream``) for a read, and the
        posted ledger replays it as it is.
    """
    payments = [
        LoanCashEvent(
            on_date=loan_loaders.loan_payment_due_date(
                leg, calendar.payment_day,
            ),
            cash=leg_settled_contribution(leg),
            source=leg,
            # The ONE clock, read once here: the settled day the posting
            # writer stamps the entry with, and the day the fold counts the
            # principal from (plan step recurrence:R16-c-1 moved the read
            # from ``dated_deltas`` onto the event, so the projections the
            # seam appends carry their own day under the same name).
            visible_on=payment_visible_on(
                leg, calendar.origination_date, calendar.payment_day,
            ),
        )
        for leg in shadows
    ]
    return with_contract_charges(
        LoanEventStream(
            charges=(),
            payments=payments,
            resets=[
                LoanResetEvent(
                    on_date=anchor.anchor_date,
                    balance=anchor.anchor_balance,
                    source=anchor,
                    is_opening=anchor.is_opening,
                    is_tracking_start=anchor.is_tracking_start,
                )
                for anchor in anchor_facts
            ],
        ),
        calendar,
    )
