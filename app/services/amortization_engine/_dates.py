"""A loan payment's THREE dates, as ONE value.

Plan step **balance:X-bl-2b** (finding **N-432**).  Until this module the same
three dates were spelled three times -- once in
:class:`~app.services.amortization_engine.PaymentRecord` (as
``payment_date`` / ``due_date`` / ``settled_on``), once in
:class:`~app.services.loan_ledger.PaymentInstallment` (as ``period_start`` /
``due_date`` / ``settled_on``) and once in ``rate_period_engine
.ConfirmedPayment`` (the replay's input, deleted by that step) -- so the FUNDING
basis carried two names for one fact and every hand-off between the three cost a
projection.  ``CLAUDE.md`` rule 14: two spellings that agree today are still two
spellings.

**What that duplication cost, concretely.**  The schedule replay reads these
three dates and NO amount, but its only feed was the priced ``PaymentRecord``,
so the reconciliation oracle's reference loaded the whole amount model -- **101
modules where its own arithmetic needs 45** (measured 2026-09-09 on ``dev``
``2625963a``; the metric is stated in
:mod:`app.services.loan_ledger._installments`) -- and an ``AmountUnresolvable``
broke a control that reads no figure.  With the dates as a value the priced
record and the loader's installment both COMPOSE them, so the replay's feed is
an attribute read from either side rather than a re-projection.

Lowest leaf of the package: stdlib only, no sibling import, so every tier that
holds a payment can name its dates without the tier that prices one in scope.
"""

from dataclasses import dataclass
from datetime import date


@dataclass(frozen=True)
class PaymentDates:
    """A loan payment's three dates -- the RECORD half of a payment.

    A loan payment carries THREE dates with DISTINCT jobs, and conflating any
    two of them is a financial-correctness bug:

    * ``settled_on`` -- the CASH day: when the money actually moved.  It is the
      ONE answer to "has this payment already happened?", shared with the
      posted ledger, which counts a payment's principal from exactly this day
      (:func:`app.services.loan_ledger.payment_visible_on`, the fold's clock).
      ``None`` for a payment that has not happened -- see :attr:`is_confirmed`.
    * ``period_start`` -- the FUNDING basis: which pay period the payment is
      booked in, i.e. which paycheck pays for it.  Drives the replay's rate
      lookup, and it is the plan date of a payment whose cash has not moved.
    * ``due_date`` -- the INSTALLMENT basis: the payment's own due date in
      contract time.  The contractual installment it pays is the one this date
      falls in (:func:`app.services.installment_calendar.installment_paid_by`,
      ruling **R-R104**) -- this date itself for a payment due on the loan's
      contractual day; a payment due before the first installment pays none
      and keeps this date.  Drives the anchor boundary, the replayed row's
      date, and ``next_pay_date``.

    The funding basis and the installment basis differ whenever a payment is
    settled LATE (past its due date, into the next biweekly pay period --
    routine over a weekend or holiday).  Deriving the due date FROM the pay
    period (the pre-fix behaviour) then reports the NEXT month's installment,
    mis-dating the row and desyncing the replay from the genesis walk.

    The CASH day differs from the funding basis in BOTH directions, and using
    the funding basis for "has it happened" was finding **N-187** (plan step
    **X-an**): a payment settled BEFORE its pay period begins was history to
    the ledger and a forward projection to the resolver, so the same
    installment was counted twice; a payment settled AFTER an evaluation date
    inside its own pay period was history to the resolver and not to the
    ledger, so it vanished from both the balance and the plan.

    *The funding basis was called* ``payment_date`` *on* ``PaymentRecord`` *and*
    ``period_start`` *on* ``PaymentInstallment`` *until plan step*
    **balance:X-bl-2b** *unified them here.  One fact, one name.*

    Attributes:
        period_start: The start of the pay period funding the payment (the
            FUNDING basis above).  Matched to the schedule by year-month, not
            exact day, so biweekly dates (e.g. 2026-03-06) map to the monthly
            schedule period (2026-03).
        due_date: The payment's own due date in contract time (the
            INSTALLMENT basis above; the installment it pays is the one this
            date falls in).  Supplied by
            :func:`app.services.loan_loaders.loan_payment_due_date` -- the one
            derivation the genesis write walk uses too, so the posted ledger
            and the replay can never drift on a payment's due date.  A feed
            that has been through
            :func:`~app.services.amortization_engine.slotted_dates` carries the
            SCHEDULE SLOT here instead, which for a payment whose due month is
            uncontested is that same date.
        settled_on: The civil day the payment's cash moved (the CASH basis
            above), or ``None`` when it has not moved.  Supplied by
            :func:`app.services.loan_ledger.payment_visible_on`, the same
            derivation the fold dates the payment's principal by.
    """

    period_start: date
    due_date: date
    settled_on: date | None

    @property
    def is_confirmed(self) -> bool:
        """Return whether this payment is historical fact rather than a plan.

        **Derived, never stored** (plan step **X-an**).  A payment is confirmed
        if and only if it carries the day its money moved -- the same
        settled-iff-dated invariant the status seam holds on the record this
        value is built from (a settled transfer's loan-side covering movement
        since plan step balance:X-bi-6-4b; a ``$0.00`` close holds none and
        is dated by the installment it skips, its interval's -- rulings
        **R-BAL139** and **R-R107**), and
        :func:`app.utils.balance_predicates.require_settled_day` refuses to
        break (through :func:`app.services.loan_ledger.payment_visible_on`;
        through ``settled_day``, naming the shadow row, until leaf
        balance:X-bi-6-4d-1).
        Storing the boolean beside the day would be a second copy of one fact,
        free to disagree with it inside a value every consumer reads.

        **What that removes, precisely.**  Not the disagreement in the
        DATABASE: there is deliberately no ``CHECK`` constraint (the predicate
        lives in ``ref.statuses`` and a constraint cannot join), so a bulk
        ``query.update`` bypassing the seam can still leave a settled day on a
        Projected row.  What it removes is the disagreement in the VALUE, by
        arbitrating at ONE producer --
        :func:`app.services.loan_ledger.payment_installments`, which reads a
        row's cash day only when
        :func:`app.services.loan_loaders.income_legs` placed it in the
        SETTLED half.  So the resolver and the ledger cannot classify a payment
        differently even on a row a bypass has broken.

        **The schedule replay does NOT read this, and that is deliberate.**
        :func:`app.services.rate_period_engine.replay_schedule` decides what it
        replays through :func:`~app.services.rate_period_engine
        .is_confirmed_payment_eligible`, whose ``has_settled_by`` term is
        ``False`` for a missing day by that predicate's own contract -- so an
        unsettled payment is excluded STRUCTURALLY rather than by a filter its
        caller has to remember.  Plan step **balance:X-bl-2b** deleted that
        caller-side filter (and the ``ConfirmedPayment`` type that existed to
        assert what it had removed) once it measured the two to be the same
        predicate.  This property is the answer for the readers that ask the
        question directly -- the loan card's "has this loan ever been paid?"
        (``balance_at._loan_figures``), whether the payment history prices a
        payment off its record or its plan
        (:func:`app.services.loan_payment_service.get_payment_history`), and
        the suite.

        Returns:
            ``True`` for a payment from the settled half of
            :func:`app.services.loan_loaders.income_legs` -- a settled
            transfer, or since ruling **R-BAL140** a Projected one whose
            money has moved -- and ``False`` for a planned one.
        """
        return self.settled_on is not None

    def __post_init__(self):
        """Validate the three dates at construction time.

        Catches invalid data immediately rather than producing wrong results
        deep in a schedule loop -- the same guard
        :class:`~app.services.amortization_engine.PaymentRecord` carried for
        these fields before they moved here.

        Raises:
            TypeError: If ``period_start`` or ``due_date`` is not a date, or a
                non-``None`` ``settled_on`` is not a date.
        """
        if not isinstance(self.period_start, date):
            raise TypeError(
                "period_start must be a date, got "
                f"{type(self.period_start).__name__}"
            )
        if not isinstance(self.due_date, date):
            raise TypeError(
                f"due_date must be a date, got {type(self.due_date).__name__}"
            )
        if self.settled_on is not None and not isinstance(self.settled_on, date):
            raise TypeError(
                "settled_on must be a date or None, got "
                f"{type(self.settled_on).__name__}"
            )
