"""The ONE replay of a loan's event stream: charges accumulate, payments clear.

Plan step **X-au-g-2c-3b-2**.  Every walk over a loan answers the same question
in the same way -- *time charges the loan, cash covers what is charged, and the
remainder pays the debt down* -- and until this step the application stated that
rule TWICE:

* the SETTLED walk (:func:`.._walk.walk_loan_ledger`), which replays a loan's
  anchors and its real payments, and
* the FORWARD fold (``balance_at._plan_fold._split_plan``), which replays the
  projected plan.

They were not copies of each other by choice.  ``balance_at`` has an import
closure of 50 modules and reaches ``loan_ledger`` (closure 23) -- both figures
measured 2026-09-02, and worth re-measuring before either is quoted again -- so
the arrow runs one way and the seam could not hand its fold down.  *The DIRECTION
is what the argument needs and it is checkable in a line:* no module under
``loan_ledger`` imports ``balance_at``, and ``_plan_fold`` imports
``loan_ledger``.  **That is the third time this
arc has found one rule duplicated because it sat on the wrong side of the import
graph** -- the ALLOCATION at plan step X-au-g-2c-3a, the CHARGE CALENDAR at
X-au-g-2c-3b-1, and now the REPLAY that consumes them both -- which is what
argues the shape is structural rather than incidental.  The remedy is the same
one each time: put the rule at the tier every walk can reach.

## The rule, in full

```text
balance := seed
standing := (interest 0.00, escrow 0.00, extra 0.00)

CHARGE   (a period began)  on or before the loan's first tracking start,
                                            clear standing first (R-R117)
                           balance <= 0  -> nothing accrues, nothing impounds
                           otherwise     -> standing += (accrue(balance, rate),
                                                         escrow, extra_per_period)
PAYMENT  (cash arrived)    allocate cash + standing.extra against standing,
                           advance the balance, and clear standing
RESET    (an assertion)    record the balance just before, overwrite it, and
                           clear standing (R-R72 part (2)) -- save the loan's
                           first balance when that balance is a tracking
                           start, with no Record balance on its day and the
                           standing installment's own payment still to walk,
                           which charges that installment again on the
                           balance it states (R-R118)
```

**Fused, the CHARGE and the PAYMENT made the payment COUNT the clock** (plan step
R16-a): while a month's interest was charged inside the per-payment step, N
payments inside one month charged N months.  Measured on a production clone, 30
payments of ``$531.94`` fourteen days apart charged the identical ``$1,096.34``
as 30 a month apart, split for split.  Separated, a second payment inside one
accrual period arrives with nothing standing and pays pure principal.

**A RESET clears whatever charges stand before it** (ruling **R-R72** part (2),
applied at plan step **recurrence:R16-c-1**): "the balance on this date is X"
supersedes every earlier month, interest owed included, so the standing
interest, escrow and hypothetical extra are zeroed with the balance overwritten.
The arm was UNREACHABLE until plan step **recurrence:R16-c-2**, when every
contractual installment from origination began to be charged
(:func:`.._charges.contract_charges`): a month nobody paid now leaves its
charge standing when a later assertion arrives -- a pre-tracking month of a
loan configured mid-life, cleared by its tracking start (when that start is
the loan's first balance, only the last month before it still stands when it
lands: see the next paragraph).

**A loan's FIRST tracking start clears the months before it one by one**
(ruling **R-R117**).  Clearing only when the reset walks is too late for a
payment the start holds -- one due before it and marked paid after it: that
payment walks first, by its due date, and paid every unrecorded month since
origination, arrears first, as its interest.  So when the loan's first
balance after its opening is a tracking start, each charge dated on or before
it REPLACES what stands (:func:`_first_tracking_start`): a month before the
start is paid for only by a payment of its own, and the months after it are
charged as ever.  A true-up (a Record balance), and a tracking start dated
after another balance, keep the reset's own clearing alone.

**That start charges the month it lands in on its OWN figure when that
month's payment walks after it** (ruling **R-R118**).  A payment due off the
contractual day, after the start but inside the interval of the installment
standing when the start walks, walks after the start; the start's own
clearing alone would leave it nothing to pay, so it would pay pure principal
(finding **REC-555**).  The ruling's "lender's day" design (the REC-552
study's option A*) walks the start before that installment's charge, so the
installment accrues on the start's figure.  So when no payment has walked
since the standing charge, and the next event after the start that is a
charge or a payment is a payment (:func:`_flag_recharging_starts`), the
start re-charges that installment on the balance it states
(:func:`_accrued`, the charge arm's own accrual).  The start's displaced
balance does not move, since a charge never touches the balance.  A second
payment inside the interval, after one walked before the start, clears
nothing fresh and pays pure principal, as any second payment does.  Any
other balance keeps R-R72 part (2) ("a Record balance in the same place
keeps your Sept 11 rule", in the ruling's words), and the start re-charges
nothing when a true-up shares its day, whichever of the two the loader
orders first: the design walks a Record balance at its own date, after the
standing charge, so it clears what stands, and the interval's payment
after it pays pure principal, as the ruling's design (A*, the REC-552
study's design.md s.3) does.  **One residual on that day, not this
ruling's:**
the design also walks the start BEFORE the charge, so the Record balance
always closes the day, where the loader closes it on whichever statement
was recorded later (plan step X-an-b's chronology, finding N-196,
:func:`~app.services.loan_loaders.load_loan_anchor_facts`; unchanged).  A
Record balance of $25,000.00 recorded before a start of $24,604.17 on the
same day leaves $24,104.17 after the example's $500.00; the design would
leave $24,500.00.

**One stream, PAST and FUTURE** (plan step **recurrence:R16-c-1**, rulings
**R-R90** and **R-R72**).  The stream carries the loan's recorded FACTS -- its
assertions and its settled payments -- and, after them, its PROJECTION: the
payments it has not yet made (:attr:`LoanEventStream.projections`).  Since plan
step **recurrence:R16-c-2** it carries ONE list of charges, the contract's
installments from origination through its LAST event
(:func:`with_contract_charges`, ruling **R-R100**); until then the plan raised
a second list of its own against its projections.  **A projected payment is
never placed before a recorded fact.**  Its walk key is ``max(on_date,
boundary)`` where the boundary is the day after the loan's latest recorded
payment or assertion (:func:`projection_boundary`); payments pushed to the
boundary keep their order among themselves by their OWN date, and no event's
``on_date`` is rewritten.  A charge is never pushed: an installment dated
before the boundary is part of the facts' own calendar, so a month skipped
behind a later settled payment is cleared by THAT payment -- arrears first,
exactly what the servicer's books say, save a month on or before the loan's
first tracking start, which that start drops (ruling R-R117, above) -- and
the overdue catch-up behind it
pays what then stands.  So the splits of the recorded facts are a function of
the facts ALONE, whether or not a projection follows them.  That is what lets
the posted ledger (which replays the facts and nothing else) and a screen
(which replays the facts and then the plan) agree to the cent on every
recorded payment: the fact prefix of both replays is the same list in the same
order.  The hypothetical ``extra_per_period`` joins the cash at the charges on
or after the boundary only -- "an extra $100 a month" is money the owner has
not paid yet, so it never reprices a recorded month.

Pure: plain data in, plain values out.  No I/O, no clock, no Flask.  All money is
:class:`~decimal.Decimal`.
"""

from collections.abc import Sequence
from dataclasses import dataclass, field, replace
from datetime import date, timedelta
from decimal import Decimal

from app.services.rate_period_engine import RatePeriod, period_for_date
from app.utils.money import (
    PaymentCashSplit,
    apply_payment_cash,
    installment_interest,
)

from ._charges import AccrualCharge, LoanCalendar, contract_charges

_ZERO_MONEY = Decimal("0.00")
_ONE_DAY = timedelta(days=1)


@dataclass(frozen=True)
class _Due:
    """What stands against the loan until cash clears it.

    The rule's ``standing`` triple (see the module docstring); in
    :func:`replay_loan_events` the name ``standing`` is the CHARGE the
    triple stands under, and this value is ``due``.

    Attributes:
        interest: The interest charged and not yet cleared.
        escrow: The escrow impounded and not yet cleared.
        extra: The hypothetical ``extra_per_period`` joined so far, which the
            next payment's cash carries (never a real charge).
    """

    interest: Decimal
    escrow: Decimal
    extra: Decimal

    def plus(self, other: "_Due") -> "_Due":
        """Return this standing with *other* added to it, part by part."""
        return _Due(
            self.interest + other.interest,
            self.escrow + other.escrow,
            self.extra + other.extra,
        )


_NOTHING_DUE = _Due(_ZERO_MONEY, _ZERO_MONEY, _ZERO_MONEY)

# The kind order applied WITHIN one date, and the whole of what this module
# decides about ordering.  A CHARGE lands before the payments that clear it,
# because interest accrues on a balance no payment has yet reduced; a RESET lands
# last, because an assertion about the balance owed is made after the day's
# money has moved (the settled walk's own rule since step E1c: a payment due
# exactly on an anchor's date is walked, then overwritten by that anchor).  A
# PROJECTED payment ranks as its recorded twin -- the two kinds are distinct
# only so the fold can flag an outcome as projected.
_CHARGE, _PAYMENT, _RESET, _PROJECTION = 0, 1, 2, 3
_KIND_ORDER = {
    _CHARGE: 0,
    _PAYMENT: 1, _PROJECTION: 1,
    _RESET: 2,
}


@dataclass(frozen=True)
class LoanCashEvent:
    """Cash arriving against a loan, at its due date in contract time.

    One payment as the replay sees it: WHEN it lands in contract time, HOW MUCH
    cash it moved, and an opaque *source* echoed back on its outcome so the
    caller can rejoin it to the record it came from.  The replay reads no other
    property, which is what lets a settled transfer shadow, a projected shadow at
    its live price and a synthesized contractual installment all replay alike.

    Attributes:
        on_date: The payment's due date in CONTRACT time, never the day the
            cash settled (ruling R-A).  It orders the event against the
            charges; the charge a payment walked on this date clears is the
            installment whose interval the date falls in (ruling R-R89), which
            is this date itself only for a payment due on the contractual day.
        cash: The cash this payment moved.  Never negative: the caller reads a
            record's own figure, and the allocation surfaces an underpayment as
            NEGATIVE principal rather than as negative cash (plan D5).
        source: The caller's record, carried through untouched.  The settled
            transfer's :class:`~app.services.transfer_legs.TransferLeg` for a
            recorded payment, a ``PlannedPayment`` for a projected one.
        visible_on: The day this cash COUNTS from in a balance read -- the ONE
            clock (:mod:`._visible`): a recorded payment's SETTLED day, a
            projection's effective day (``max(due, as_of + 1d)``, ruling D1).
            The replay never reads it; it is computed once by the stream's
            builder and carried onto the outcome so the two consumers that
            re-key the walk by visibility (:func:`.._walk.dated_deltas`, the
            confirmed view) read one statement of it rather than each
            deriving their own from the source.
    """

    on_date: date
    cash: Decimal
    source: object
    visible_on: date


@dataclass(frozen=True)
class LoanResetEvent:
    """An asserted balance that OVERWRITES the running balance at its date.

    A loan's opening and every user true-up: an authoritative statement of what
    is owed, which the replay applies as a reset rather than as a payment (see
    :func:`.._walk.walk_loan_ledger` for why resetting at EVERY assertion, not
    only the latest, is what keeps a from-origination sum of postings equal to
    the resolver's balance).

    Attributes:
        on_date: The date the assertion is made ABOUT.
        balance: The balance owed the assertion states.
        source: The caller's record, carried through untouched (a
            :class:`~app.services.loan_loaders.LoanAnchorFact` for the settled
            walk).
        is_opening: ``True`` for the loan's ORIGINATION assertion -- the
            balance it opens at, synthesized from the immutable params -- and
            ``False`` for every later assertion (a true-up, a tracking start).
            The replay reads it in one place, to find the loan's first balance
            AFTER its opening (:func:`_first_tracking_start`); the flag is
            otherwise for the readers that must name the opening: the pass's
            visibility bound keeps it whatever its date, and the target-date
            search bounds its doubling by it for a loan not yet originated.
        is_tracking_start: ``True`` for a ``tracking_start`` assertion -- the
            balance stated at setup or with the dashboard's tracking-start
            door (:attr:`~app.services.loan_loaders.LoanAnchorFact
            .is_tracking_start`, which the stream's builder copies) -- and
            ``False`` for the opening and every true-up.  Read beside
            ``is_opening`` in :func:`_first_tracking_start`: when the loan's
            first balance after its opening is a tracking start, the months
            before it are cleared month by month (ruling **R-R117**).
            **Both kinds are keyword-only and REQUIRED**, so a constructor
            that forgot one raises instead of silently switching R-R117 off:
            a start read as a true-up is never the first tracking start, and
            an opening read as a later balance becomes the "first balance"
            itself.
    """

    on_date: date
    balance: Decimal
    source: object
    is_opening: bool = field(kw_only=True)
    is_tracking_start: bool = field(kw_only=True)


@dataclass(frozen=True)
class LoanEventStream:
    """The three kinds of fact a loan replay folds, each PRE-ORDERED by its caller.

    Three lists rather than one merged list, because the merge is exactly what
    the two walks were each stating for themselves.  :func:`replay_loan_events`
    owns the order BETWEEN kinds (charge, then payment, then reset, ascending by
    date) and adds **no tie-break WITHIN a kind** -- it sorts stably, so each
    list's own order survives.

    That division is plan step X-an-b's, applied to a third caller: the loader is
    the one home of its own chronology, and a second statement of it here would
    be both duplication and, as X-an-b measured, an INCOMPLETE one.  So the
    settled walk hands payments in ``(pay_period.start_date, id)`` order and
    anchors in ``(anchor_date, created_at, event_id)`` order, and the forward fold
    hands payments in ``(due_date, effective_date)`` order; all three orders are
    preserved without this module knowing any of them.

    Attributes:
        charges: The loan's :class:`~._charges.AccrualCharge` list, ascending
            by ``on_date``: every contractual installment from origination
            through the stream's last event, for a production stream
            (:func:`with_contract_charges`, ruling **R-R100**); a hand-built
            stream states its own.
        payments: The RECORDED cash events -- settled payments -- in the
            caller's own within-date order.
        resets: The asserted balances, in the caller's own within-date order.
        projections: The cash events that have NOT happened -- the forward
            plan's projected rows and estimated occurrences -- in the caller's
            own order.  Walked after every recorded fact (see the module
            docstring); empty for the ledger's replay, which books
            facts and nothing else.
        calendar: The loan's :class:`~._charges.LoanCalendar` the charges were
            built from (:func:`with_contract_charges`), or ``None`` for a
            hand-built stream that states its charges itself.  Its rate
            periods are read for a payment NO charge stands over -- one dated
            before the loan's first installment, ruling R-C's early extra --
            so its outcome still names the period governing it.  **The stream
            carries the calendar it was charged on** (plan step
            recurrence:R16-c-2, rule 14), so the read pass's forward plan
            reads the loan's contract terms off its facts walk rather than
            building them a second time (:func:`app.services.balance_at._plan
            .loan_plan`), and a screen's timeline is charged on the same
            calendar object as the facts it extends.
    """

    charges: Sequence[AccrualCharge]
    payments: Sequence[LoanCashEvent]
    resets: Sequence[LoanResetEvent] = field(default_factory=tuple)
    projections: Sequence[LoanCashEvent] = field(default_factory=tuple)
    calendar: LoanCalendar | None = None


def with_contract_charges(
    stream: LoanEventStream, calendar: LoanCalendar,
) -> LoanEventStream:
    """Return *stream* charged every contractual installment through its LAST event.

    **The ONE composer of a production stream's charges** (ruling **R-R100**,
    plan step recurrence:R16-c-2).  The ledger's stream (its recorded
    facts) and a screen's (the same facts, then the forward plan's payments)
    both reach their charges here, so the two cannot be charged on two
    calendars: every installment from the loan's first
    (:func:`.._charges.contract_charges`) through the latest date any of the
    stream's events -- a payment, an assertion or a projection -- is due on.
    That reach is the whole of what a replay needs: a charge after the last
    payment is cleared by nothing and moves no outcome, so charging further is
    arithmetic nobody reads, and charging less would leave a late payment's
    own installment uncharged, so that month's interest would never be
    charged at all.

    Args:
        stream: The loan's events; its own ``charges`` and ``calendar`` are
            replaced.
        calendar: The loan's :class:`~._charges.LoanCalendar`.

    Returns:
        A copy of *stream* carrying *calendar* and its charges; no charge at
        all when the stream holds no event.
    """
    last = max(
        (
            event.on_date
            for event in (*stream.payments, *stream.resets, *stream.projections)
        ),
        default=None,
    )
    return replace(
        stream,
        charges=() if last is None else contract_charges(calendar, last),
        calendar=calendar,
    )


@dataclass(frozen=True)
class PaymentOutcome:
    """What one payment's cash did: the event, the charge it faced, and the split.

    Attributes:
        event: The :class:`LoanCashEvent` this outcome answers -- carrying the
            caller's own ``source`` record back to it.
        charge: The :class:`~._charges.AccrualCharge` standing over this payment
            -- the most recent one the walk applied.  For a payment walked on
            its own date that is the installment whose INTERVAL it falls in:
            the latest one due on or before it (ruling **R-R89**, "B: contract
            interval"; plan step recurrence:R16-c-2), since every installment
            is charged and a charge walks before a payment sharing its date.
            An ad-hoc extra between two installments reads the installment it
            sits after.  For an overdue projection pushed to the projection
            boundary it is the last installment walked before the boundary --
            the one the latest recorded payment already cleared, so the
            catch-up pays what stands (ruling R-R89's reading: a payment's
            period is the charge the replay hands it).  **The replay returns
            it because it already knows it**, and a caller that re-derived the
            pairing would be stating a SECOND association rule beside this
            one.  ``None`` for a payment walked before any charge: ruling R-C's
            early extra, a payment after origination and before the first
            installment, which pays what stands (nothing) and reads its
            ``period`` from the stream's calendar.
        split: The :class:`~app.utils.money.PaymentCashSplit` the ONE allocation
            produced, including the running ``balance_after``.  A payment that
            FOLLOWS another inside one accrual period faces the same ``charge``
            and clears nothing: its ``interest`` and ``escrow`` are ``0.00``.
            The four parts are read HERE, off the allocation's own value: the
            per-field copy the walk used to make of them (``LoanPaymentSplit``,
            deleted at plan step recurrence:R16-c-1) was a second home for one
            value.
        period: The :class:`~app.services.rate_period_engine.RatePeriod`
            governing this payment: the standing charge's -- resolved ONCE, on
            the accrual period the charge IS, so for a payment clearing one
            charge the displayed rate is provably the rate its interest accrued
            at (plan step X-au-g-2c-3b-2); one clearing arrears across a rate
            change displays its latest charge's -- or,
            for a payment no charge stands over, the period the loan's calendar
            puts its installment in (:func:`~app.services.rate_period_engine
            .period_for_date` over the stream's ``periods``).
        is_projected: ``True`` for an event from
            :attr:`LoanEventStream.projections` -- a payment that has not
            happened -- ``False`` for a recorded one.  The kind travels as a
            flag set by the replay from the list the event came from, never
            recovered from the ``source``'s type.
    """

    event: LoanCashEvent
    charge: AccrualCharge | None
    split: PaymentCashSplit
    period: RatePeriod
    is_projected: bool

    # The parts every reader wants, read THROUGH the two values that hold them.
    # Delegation, not a copy: each name below has exactly one home, and the
    # flat spelling is the one idiom (the confirmed view, the tax reader and
    # the loan page all read ``outcome.principal``, and the worked-out ledger
    # each non-principal component).

    @property
    def source(self) -> object:
        """The record this payment was read off (:attr:`LoanCashEvent.source`)."""
        return self.event.source

    @property
    def due_date(self) -> date:
        """The payment's own due date (:attr:`LoanCashEvent.on_date`), its walk-order key.

        The installment it PAYS -- the one the loan page names it by -- is the
        installment this date falls in
        (:func:`~app.services.installment_calendar.installment_paid_by`, rulings
        **R-R104**, **R-R108**); the two are one date only for a payment due on
        the loan's contractual day.
        """
        return self.event.on_date

    @property
    def visible_on(self) -> date:
        """The day this cash counts from (:attr:`LoanCashEvent.visible_on`)."""
        return self.event.visible_on

    @property
    def cash(self) -> Decimal:
        """The cash this payment moved (:attr:`LoanCashEvent.cash`)."""
        return self.event.cash

    @property
    def interest(self) -> Decimal:
        """The interest charge this payment cleared (``>= 0``)."""
        return self.split.interest

    @property
    def escrow(self) -> Decimal:
        """The escrow charge this payment cleared (``>= 0``)."""
        return self.split.escrow

    @property
    def principal(self) -> Decimal:
        """The debt this payment paid down; NEGATIVE for an underpayment (plan D5)."""
        return self.split.principal

    @property
    def excess(self) -> Decimal:
        """Cash beyond what closes the loan, routed to a refund (plan D4)."""
        return self.split.excess

    @property
    def balance_after(self) -> Decimal:
        """The running balance owed after this payment."""
        return self.split.balance_after

    @property
    def charge_date(self) -> date | None:
        """The standing charge's date -- the accrual period's identity -- or ``None``."""
        return None if self.charge is None else self.charge.on_date


@dataclass(frozen=True)
class ResetOutcome:
    """One asserted balance and the running balance it displaced.

    Attributes:
        event: The :class:`LoanResetEvent` this outcome answers.
        balance_before: The running balance JUST BEFORE the reset -- the value a
            correction is booked against, so that the ledger's implied owed moves
            from it to the asserted balance and the two legs sum to zero.
    """

    event: LoanResetEvent
    balance_before: Decimal


@dataclass(frozen=True)
class LoanReplay:
    """A replay's full output: every payment's split and every reset's displacement.

    Both lists are in the order the replay APPLIED them, which is chronological,
    so a caller reading either one reads the loan's own sequence.

    Attributes:
        payments: One :class:`PaymentOutcome` per :class:`LoanCashEvent`, the
            recorded payments and then the projections -- ONE list, in the order
            the replay applied them; ``is_projected`` tells them apart.
        resets: One :class:`ResetOutcome` per :class:`LoanResetEvent` (empty when
            the stream asserts nothing).
    """

    payments: list[PaymentOutcome]
    resets: list[ResetOutcome]


def latest_recorded_on(stream: LoanEventStream) -> date | None:
    """Return the date of *stream*'s latest RECORDED fact, or ``None`` when it records none.

    The loan record's LAST EVENT: its last settled payment or its last
    assertion, in contract time -- a payment's own due date
    (:attr:`LoanCashEvent.on_date`), an assertion's own date.  Charges and
    projections are not records of anything having happened, so neither moves
    it.  Two readers ask it, and this is its one statement: the day after it
    is where projections may walk (:func:`projection_boundary`), and the
    installment after it is where the contract's remaining installments begin
    (the confirmed view's ``last_recorded_on``,
    :func:`app.services.balance_at.confirmed_view`; ruling plan#3382).

    Args:
        stream: The loan's :class:`LoanEventStream`.

    Returns:
        The latest recorded ``on_date``, or ``None`` for a stream with no
        recorded fact.
    """
    return max(
        (event.on_date for event in (*stream.payments, *stream.resets)),
        default=None,
    )


def projection_boundary(stream: LoanEventStream) -> date | None:
    """Return the first day on which *stream*'s projections may be walked.

    The day after the loan's latest RECORDED fact
    (:func:`latest_recorded_on`), or ``None`` when the stream records
    nothing.  A projection whose own date is earlier is keyed here
    instead (:func:`_ordered`), so no projection is ever replayed ahead of a
    fact, and the ``extra_per_period`` what-if accrues only at charges on or
    after it (:func:`replay_loan_events`).  Charges do not move it: a charge is
    the calendar, not a record of anything having happened.

    Public so a test can state the day it expects a projection to land on;
    the seam composes a stream and never re-dates an event against it.

    Args:
        stream: The loan's :class:`LoanEventStream`.

    Returns:
        The boundary ``date``, or ``None`` for a stream with no recorded fact.
    """
    latest = latest_recorded_on(stream)
    return None if latest is None else latest + _ONE_DAY


def _ordered(
    stream: LoanEventStream, boundary: date | None,
) -> list[tuple[int, object]]:
    """Return *stream*'s lists merged into ONE walk order.

    Every event is keyed ``(walk_date, on_date, kind rank)``: a charge's or a
    recorded event's walk date is its own date; a projection's is
    ``max(on_date, boundary)``, so no projected payment precedes a recorded
    fact.  The second key keeps the projections pushed to the boundary in
    CONTRACT order among themselves -- April's catch-up before May's --
    exactly as they would have walked had nothing pushed them, and ahead of an
    installment falling on the boundary day itself; for every other event it
    equals the first key and changes nothing.  A charge is never pushed: the
    installments before the boundary are the recorded facts' own calendar
    (plan step recurrence:R16-c-2).  The rank orders one date: charge ->
    payment -> reset (see :data:`_CHARGE`), a projection ranking as a
    payment.  The sort is STABLE, so each input list's own within-date order
    survives untouched -- the contract :class:`LoanEventStream` states -- and
    a projection listed after the recorded payments walks after them at an
    equal key.

    Args:
        stream: The caller's pre-ordered event lists.
        boundary: The stream's :func:`projection_boundary`, computed once by
            the replay.

    Returns:
        ``[(kind, event), ...]`` in walk order, the kind travelling as the
        discriminant rather than being recovered with an ``isinstance`` at the
        fold, so an event class that gained a sibling field could not silently
        change arms.
    """

    def _pushed(on_date: date) -> date:
        """The walk date of a projection dated *on_date*."""
        return on_date if boundary is None else max(on_date, boundary)

    tagged = (
        [(charge.on_date, _CHARGE, charge) for charge in stream.charges]
        + [(payment.on_date, _PAYMENT, payment) for payment in stream.payments]
        + [(reset.on_date, _RESET, reset) for reset in stream.resets]
        + [
            (_pushed(payment.on_date), _PROJECTION, payment)
            for payment in stream.projections
        ]
    )
    tagged.sort(
        key=lambda event: (event[0], event[2].on_date, _KIND_ORDER[event[1]]),
    )
    return [(kind, event) for _when, kind, event in tagged]


def _first_tracking_start(resets: Sequence[LoanResetEvent]) -> date | None:
    """Return the date of the loan's first balance, when that balance is a tracking start.

    The day before which ruling **R-R117** clears a loan's months one by one:
    the date of its earliest-dated assertion other than the opening, provided
    a TRACKING START stands on that date -- the balance stated at setup, or
    with the dashboard's tracking-start door, as the first thing the app
    records about the loan after its origination.  ``None`` when the loan
    asserts nothing after its opening, and when its first such balance is a
    true-up alone (a Record balance): R-R117 leaves those, and a tracking
    start dated after another balance, to R-R72 part (2)'s clearing at the
    reset itself (review 6 of the REC-552 study, finding M3, measured the
    month-by-month rule misfiring on both).  A true-up SHARING the earliest
    date does not disqualify the start: the ruling leaves untouched a start
    dated AFTER another balance, and one on the same day is not.

    **Not the loan's recorded start.**
    :attr:`app.services.balance_at._resolution.ResolvedLoan.recorded_start`
    is the date of the loan's first tracking start WHATEVER precedes it, the
    day its record begins (rulings R-R111, R-R114); this is that start only
    when no other balance is dated before it.  A loan with a Record balance
    and then a tracking start has a recorded start and no R-R117 date.

    Args:
        resets: The stream's assertions, in any order.

    Returns:
        The first tracking start's date, or ``None``.
    """
    first = min(
        (reset.on_date for reset in resets if not reset.is_opening),
        default=None,
    )
    if first is None or not any(
        reset.is_tracking_start and reset.on_date == first for reset in resets
    ):
        return None
    return first


def _flag_recharging_starts(
    order: list[tuple[int, object]], first_start: date | None,
) -> list[tuple[int, object, bool]]:
    """Return *order* with each event flagged ``True`` where its reset re-charges.

    The whole of ruling **R-R118**'s trigger, decided over the walk order
    before the walk: a reset re-charges the installment standing when it walks
    (:func:`replay_loan_events`) when ALL of these hold --

    * it is a tracking start on the loan's first tracking start's date
      (*first_start*, :func:`_first_tracking_start`), so a true-up and a
      later start keep ruling R-R72 part (2)'s clearing;
    * no true-up (a Record balance) shares that date.  The ruling's design
      walks a Record balance at its own date, after the standing charge, so
      it clears the installment whichever of the two the loader orders
      first.  The day's closing FIGURE still follows the loader's order (the
      later statement's), as it did before the ruling, where the design
      would close the day on the Record balance: the residual the module
      docstring states.  R-R117's clearing is not disqualified by such a
      true-up (:func:`_first_tracking_start`); this re-charge is;
    * a charge has walked before it and no payment has walked since that
      charge -- the standing installment has had no payment of its own, so a
      payment after the start is that installment's FIRST, not a second one
      that pays pure principal; and
    * the next event after it that is a charge or a payment is a payment,
      recorded or projected (:func:`_cash_before_the_next_charge`), so the
      installment's own payment walks after the start.  Without one, the
      start clears the installment as it always has: nothing pays it, and a
      charge left standing would be paid first by the NEXT installment's
      payment, as arrears.

    A reset leaves "no payment since the charge" as it was, so two tracking
    starts sharing *first_start* both re-charge, the later on its own
    figure.

    Args:
        order: The walk order (:func:`_ordered`).
        first_start: The loan's first tracking start's date, or ``None``.

    Returns:
        ``[(kind, event, recharges), ...]`` in *order*'s order.
    """
    recharge_on = None if any(
        kind == _RESET
        and not event.is_opening
        and not event.is_tracking_start
        and event.on_date == first_start
        for kind, event in order
    ) else first_start
    flagged: list[tuple[int, object, bool]] = []
    unpaid_charge = False
    for index, (kind, event) in enumerate(order):
        if kind == _CHARGE:
            unpaid_charge = True
        elif kind in (_PAYMENT, _PROJECTION):
            unpaid_charge = False
        flagged.append((
            kind,
            event,
            kind == _RESET
            and unpaid_charge
            and event.is_tracking_start
            and event.on_date == recharge_on
            and _cash_before_the_next_charge(order, index),
        ))
    return flagged


def _cash_before_the_next_charge(
    order: list[tuple[int, object]], index: int,
) -> bool:
    """Return whether a payment walks after ``order[index]`` before any charge.

    Resets between are passed over: one after the start clears what stands
    by its own arm whatever the start did.

    Args:
        order: The walk order (:func:`_ordered`).
        index: The position to look past.

    Returns:
        ``True`` when the first charge or payment after *index* is a payment
        (recorded or projected); ``False`` when it is a charge or there is
        none.
    """
    for kind, _event in order[index + 1:]:
        if kind == _CHARGE:
            return False
        if kind in (_PAYMENT, _PROJECTION):
            return True
    return False


def _accrued(
    charge: AccrualCharge,
    balance: Decimal,
    extra_per_period: Decimal,
    boundary: date | None,
) -> _Due:
    """Return what *charge* adds to what stands, accrued on *balance*.

    **The ONE accrual of an installment in the replay**: the CHARGE arm adds
    it to what stands, and the loan's first tracking start re-charges its
    standing installment with it on the balance it states (ruling
    **R-R118**), so one installment cannot be accrued two ways.  A CLOSED
    loan (``balance <= 0``) accrues nothing and impounds nothing (see
    :func:`replay_loan_events` for why that skip is kept); the hypothetical
    *extra_per_period* joins at a charge on or after the projection
    *boundary* only.

    Args:
        charge: The installment.
        balance: The balance its interest accrues on.
        extra_per_period: The what-if extra (``0.00`` for every real read).
        boundary: The stream's :func:`projection_boundary`.

    Returns:
        The :class:`_Due` the installment adds.
    """
    if balance <= _ZERO_MONEY:
        return _NOTHING_DUE
    return _Due(
        interest=installment_interest(
            balance, charge.period.annual_rate, charge.opens_on,
            charge.on_date, charge.period.convention,
        ),
        escrow=charge.escrow,
        extra=(
            extra_per_period
            if boundary is None or charge.on_date >= boundary
            else _ZERO_MONEY
        ),
    )


def replay_loan_events(
    seed: Decimal,
    stream: LoanEventStream,
    extra_per_period: Decimal = _ZERO_MONEY,
) -> LoanReplay:
    """Replay *stream* from *seed* into every payment's split and every reset's jump.

    **The ONE loan replay in the application** (see the module docstring for the
    rule and for why it is one).  It walks the stream in contract order
    (:func:`_ordered`) -- the recorded facts, then the projections behind them
    -- accumulating each accrual period's charge and allocating each payment's
    cash against whatever stands
    (:func:`~app.utils.money.apply_payment_cash`, the ONE allocation, over
    :func:`~app.utils.money.installment_interest`, the ONE accrual).

    A CLOSED loan (``balance <= 0``) accrues nothing and impounds nothing
    (:func:`_accrued`), so its charge adds nothing to what stands; a payment
    that follows is a refund in full, which the allocator's own closed-loan
    arm answers.

    **That skip is a TRANSCRIPTION of the fold it replaced, and no current test
    distinguishes it** -- an adversarial review deleted the skip and all 101
    tests in the loan files stayed green, which is worth recording rather than
    hiding.  The reason it cannot be observed today: a balance reaches ``<= 0``
    only at exactly ``0.00`` (principal caps at the balance), the interest
    on a zero balance is ``0.00`` anyway, and whatever escrow the arm would
    accumulate is discarded by the allocator's own closed-loan arm at the next
    payment, or cleared by the next balance statement first.  It is kept
    because it is not unobservable in PRINCIPLE: without it,
    ``extra_per_period`` would join a
    closed loan's refund, so a what-if search would report an ``excess`` inflated
    by money the owner never pays.  No reader consumes ``excess`` on the forward
    path today, which is the whole of why no test can see it.

    Args:
        seed: The balance the replay starts from.  ``0.00`` for every
            production stream, whose first event is the loan's opening
            assertion -- the ONE seed since plan step recurrence:R16-c-1, when
            the forward fold stopped starting from a balance a separate fold
            had resolved and joined this replay behind the recorded facts.
        stream: The loan's :class:`LoanEventStream`.
        extra_per_period: A HYPOTHETICAL extra added to the cash once per ACCRUAL
            PERIOD, for the target-date search (``_plan_fold.required_extra``).
            ``0.00`` -- the default, and what every real read passes -- replays
            the stream as it stands.  Per PERIOD rather than per PAYMENT since
            plan step R16-a: added per record, "an extra $100 a month" was $2,600
            a year for a definition paying every fortnight.  Accrues at the
            charges on or after the :func:`projection_boundary` only: it is
            money not yet paid, so it never reprices a recorded month -- and a
            month skipped behind a later settled payment is a RECORDED month
            since plan step recurrence:R16-c-2 (its charge walks with the
            facts), so an overdue catch-up carries no hypothetical extra.

    Returns:
        The :class:`LoanReplay`.
    """
    balance = seed
    due = _NOTHING_DUE
    standing: AccrualCharge | None = None
    payments: list[PaymentOutcome] = []
    resets: list[ResetOutcome] = []
    boundary = projection_boundary(stream)
    first_start = _first_tracking_start(stream.resets)
    for kind, event, recharges in _flag_recharging_starts(
        _ordered(stream, boundary), first_start,
    ):
        if kind == _CHARGE:
            # Recorded BEFORE the closed-loan test, so a payment always carries
            # its own accrual period's charge -- what governs its rate is a fact
            # about the period, not about whether the period accrued anything.
            standing = event
            if first_start is not None and event.on_date <= first_start:
                # Ruling R-R117: up to the loan's first tracking start, a
                # month's charge REPLACES what stands rather than adding to
                # it, so a month before the start that no payment of its own
                # cleared is dropped -- even when a payment the start holds
                # walks after it and would otherwise pay it, arrears first.
                # On or before, not before: a payment due on the start's own
                # day walks between that day's charge and the start, and it
                # pays that month alone.
                due = _NOTHING_DUE
            due = due.plus(_accrued(event, balance, extra_per_period, boundary))
        elif kind in (_PAYMENT, _PROJECTION):
            split = apply_payment_cash(
                event.cash + due.extra, balance, due.interest, due.escrow,
            )
            balance = split.balance_after
            due = _NOTHING_DUE
            payments.append(PaymentOutcome(
                event=event,
                charge=standing,
                split=split,
                period=(
                    period_for_date(
                        () if stream.calendar is None
                        else stream.calendar.periods,
                        event.on_date,
                    )
                    if standing is None else standing.period
                ),
                is_projected=kind == _PROJECTION,
            ))
        else:
            resets.append(
                ResetOutcome(event=event, balance_before=balance)
            )
            balance = event.balance
            # The assertion supersedes every charge standing before it
            # (ruling R-R72 part (2); reachable since plan step
            # recurrence:R16-c-2 -- see the module docstring) -- save the
            # loan's first tracking start when the standing installment's
            # own payment walks after it: that installment is charged again
            # on the balance the start states (ruling R-R118).
            due = (
                _accrued(standing, balance, extra_per_period, boundary)
                if recharges else _NOTHING_DUE
            )
    return LoanReplay(payments=payments, resets=resets)
