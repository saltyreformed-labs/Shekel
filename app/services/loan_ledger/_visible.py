"""WHEN a loan event becomes visible to a balance read -- the ONE honest clock.

The fold reads a loan's SOURCE events, but the balance every screen shows is a
sum of POSTINGS, so the fold must count each event on the SAME day the posted
ledger does or the two would diverge (step B2's parallel run is an EQUALITY).
That day is the day the event HAPPENED, and it is already the day the posting
carries in ``journal_entries.entry_date``:

* a **PAYMENT** is visible from its **settled date** -- the STORED
  ``settled_on`` of the loan-side covering movement its leg carries as its
  record (plan step ``balance:X-bi-6-4b``; the shadow's own column until
  then, which the status seam keeps equal to its movement), read through the
  leg's own :attr:`~app.services.transfer_legs.TransferLeg.settled_on` and
  refused when missing by the one refusal of a missing settle day,
  :func:`app.utils.balance_predicates.require_settled_day`, since leaf
  ``balance:X-bi-6-4d-1`` (through the row accessor
  :func:`~app.utils.balance_predicates.settled_day` until then).  It is the day
  the posting writer files the loan-side entry under (plan step
  ``balance:X-bi-6-3``) and the day the cash walk folds that movement on.  It
  is the LOAN side's own day (plan step ``balance:X-bi-6-4c-3``, ruling
  **R-BAL142**): the same date the checking outflow moves on while the loan
  side borrows checking's day, which is every payment with no evidence of its
  own on the loan side, so the loan and checking move together (ruling R-A);
  a loan side that holds its own day moves on that day, and the ledger holds
  the difference in Transfers-in-transit.  A payment that moved NO money -- a ``$0.00``
  close, whose leg carries no record -- is visible from the installment it
  skips (ruling **R-BAL139**): its INTERVAL's installment (ruling
  **R-R107**), the one the replay charges it against.

  **A payment that moved money has no derivation and no fallback left here,
  and that is plan step X-f1** (ruling R-EC).  It WAS the display-timezone
  civil date of the shadow's ``paid_at``, falling back to its pay period's
  ``start_date`` when the instant was NULL (the developer ruling of
  2026-07-17); the day is a stored fact now,
  and a settled payment whose record carries none is REFUSED rather than
  dated by a fallback.  R-BAL139's installment is not a fallback for a
  missing day: it is the day of a payment that has no movement to carry one
  (and one due before the loan's first installment keeps its own due date,
  having no installment to skip).

  **The zone moved from UTC to ``America/New_York`` at ruling R-DH (b)**
  (2026-07-31), together with the cash half, because a split zone is what pulls a
  transfer's two legs onto different days: a payment recorded at 20:38 Eastern is
  00:38 the NEXT day in UTC, so the checking outflow moved on the user's Monday
  while the loan principal fell on Tuesday.  That ruling is what the stored
  column now records directly.  Measured on production 2026-07-31, when the day
  was still derived: of 9 settled payment shadows exactly ONE was affected -- a
  ``$1,910.95`` mortgage payment stamped 2026-07-02 00:38:53 UTC, the evening of
  2026-07-01 Eastern and the last day of that pay period.
* an **ANCHOR** is visible from its **own civil date** (``anchor_date``) -- the one
  date it ever asserts, and the ``entry_date`` the anchor correction is posted at
  (:func:`app.services._posting_reconcile.emit_correction_entry`).

**This is step C2 -- the one clock that replaced the two boundary predicates the
old rule used.**  Before it, a payment counted from its pay period's ``start_date``
and an anchor from ``LEAST(anchor_date, containing period.start)`` -- both a
boundary predicate standing in for an instant, this codebase's signature defect
(``docs/audits/balance_architecture/README.md`` Section 8).  The anchor ``LEAST``
in particular made an anchor visible days EARLY, so a loan originating 2026-03-25
read its full $200,000.00 principal on 2026-03-20 (finding N-10).  Counting each
event on its own date closes that at the source: an anchor dated in the future is
simply not yet visible, and the four ``origination_date`` guards that contained
the leak are retired (N-10).

**The CALENDAR left this module at plan step C2-d, and with it the package's
last query.**  Three names lived here -- ``owner_pay_periods`` (the owner's
period list), ``find_period_containing_date`` (which period a date falls in)
and ``resolve_anchor_pay_period`` (the three-branch chain the anchor-posting
writers filed against, built on the other two).  Ruling **D5**'s one clock had
already taken the FOLD off them -- an anchor counts from its own date and needs
no calendar -- so the two posting writers were the only consumers left, and a
chronology rule neither of them shares with anything in this package was being
reached by an import from the CASH posting package into the LOAN package
(finding **N-169**).  The 2026-08-10 pay-calendar ruling replaced the chain
with one clamp on
:meth:`app.services.pay_calendar.PayCalendar.filing_period`, both writers now
take it through :func:`app.services._posting_reconcile.filing_calendar_for`,
and all three names are deleted.

**Chronology only, and now PURE.**  The two names left here each return a
``date``; neither yields a balance-at-T, which is why both are ruled
non-producers of the balance fence.  No clock, no writes and -- since C2-d
removed the one calendar query -- no database session anywhere in this package.
That also retires a caveat this paragraph used to carry: a returned
``PayPeriod`` was an ORM row, so money was reachable from it by relationship,
and the fence ruling had to say that a period is not an account's balance
rather than that a figure was unreachable.  Nothing here returns an ORM row any
more, so the stronger claim is now true by construction -- which is the shape to
prefer, because claiming it before it was true is how
``LoanState.current_balance`` shipped.
"""

from datetime import date

from app.services.installment_calendar import installment_paid_by
from app.services.loan_loaders import loan_payment_due_date
from app.services.transfer_legs import TransferLeg
from app.utils.balance_predicates import require_settled_day


def anchor_visible_on(anchor_date: date) -> date:
    """Return the date an anchor's balance correction becomes visible to a read.

    The anchor's OWN civil date (step C2): an assertion happens on the date it
    asserts, which is the ``entry_date`` its correction is posted at
    (:func:`app.services._posting_reconcile.emit_correction_entry`).  It no
    longer needs the owner's calendar -- the pre-C2 rule
    ``LEAST(anchor_date, containing period.start)`` did, only to reach the pay
    period the anchor had to be FILED under -- a requirement of the per-period
    readers, which ``pay_period_id``'s ``NOT NULL`` expresses rather than
    creates (ruling **pay_calendar:R-PC53**) -- and that
    ``LEAST`` is exactly what made a future-dated anchor visible early (N-10).

    Kept as a named one-liner rather than inlined so the fold reads with the same
    vocabulary as :func:`payment_visible_on` (the two halves of the one clock).

    Args:
        anchor_date: The date the anchor asserts its balance on.

    Returns:
        The date from which a balance read counts this anchor -- ``anchor_date``.
    """
    return anchor_date


def payment_visible_on(
    leg: TransferLeg, origination_date: date, payment_day: int,
) -> date:
    """Return the date a settled payment's principal becomes visible to a read.

    Its **settled date** (step C2, ruling R-A): the STORED ``settled_on`` of
    the leg's record -- the loan-side covering movement -- read through the
    leg's own :attr:`~app.services.transfer_legs.TransferLeg.settled_on`.
    That movement is
    what the cash fold counts AND what the posting writer files the loan-side
    entry under (plan step ``balance:X-bi-6-3``), so the day the fold counts
    this payment and the day the sum-of-postings reader counts it cannot
    drift.  It is the LOAN side's own day (plan step ``balance:X-bi-6-4c-3``,
    ruling **R-BAL142**) -- the checking outflow's day while the loan side
    borrows it, its own day when it holds one.  It read the SHADOW's
    ``settled_on`` until plan step balance:X-bi-6-4b; the status seam keeps
    each shadow equal to its own movement (measured equal on all 20 settled
    income shadows of the 2026-09-23 21:17 production dump).

    **A leg with NO record is a ``$0.00`` close, and it is dated by the
    installment it skips** (ruling **R-BAL139**, extending **R-BAL90**: no
    transfer stores a day).  That installment is its INTERVAL's (ruling
    **R-R107**, amending R-BAL139): the latest installment due on or before the
    payment's own due date
    (:func:`~app.services.installment_calendar.installment_paid_by`), the same
    one answer to "which installment does this payment pay" that its charge,
    its cash price, the forward plan (ruling **R-R104**) and the loan page
    (rulings **R-R108**, **R-R109**) read.  Nothing moved on
    any day, so this is the day the debt grows by the charge the payment did
    not clear, and the ledger books that correction on it.  For a payment due
    ON the contractual day the interval's installment IS its due date
    (:func:`app.services.loan_loaders.loan_payment_due_date`); one due before
    the loan's first installment skips none and keeps its own due date.  Until
    X-bi-6-4b it was the settle day stated on the close, read off the shadow
    row that ``X-bi-6-4d`` deletes; from X-bi-6-4b until R-R107 it was the
    payment's own due date, which parts from its interval's installment for a
    payment due off the contractual day.

    **It DERIVED the day from ``paid_at`` until plan step X-f1** (ruling R-EC)
    -- a display-timezone conversion of the click instant with the pay period's
    ``start_date`` as a NULL fallback.  The column stores the day now, so this
    reads a fact; a record carrying none under a settled transfer is refused
    rather than dated by a fallback.  No door writes that state: a revert
    un-dates the movement and leaves the transfer unsettled, and a ``$0.00``
    re-close withdraws it (``status_seam._covering``).

    **The split MATH is untouched by the zone, and that is what bounds this
    rule's blast radius to one day of VISIBILITY.**  The interest / principal /
    escrow split, the governing rate version, and the anchor-versus-payment
    ordering all key on the DUE date
    (:func:`app.services.loan_ledger.loan_event_stream`,
    :mod:`app.services.loan_ledger._replay`) -- never on ``paid_at`` -- so a late or
    out-of-order settlement, and equally a re-zoned one, changes only WHEN the
    paid-down principal is shown, never HOW the payment splits, at what rate, or
    against which anchor.

    Args:
        leg: The settled payment's to-side
            :class:`~app.services.transfer_legs.TransferLeg`, its record
            attached (:func:`app.services.loan_loaders.settled_income_legs`
            attaches it).  A record-less leg's ``due_date`` and ``pay_period``
            are read, so its parent's period must be loaded -- the producer
            loads it as its sort key.
        origination_date: The loan's origination
            (:attr:`app.models.loan_params.LoanParams.origination_date`), where
            its installment grid starts; read only by R-BAL139's arm.
        payment_day: The loan's contractual day-of-month due day
            (:attr:`app.models.loan_params.LoanParams.payment_day`), read only
            by R-BAL139's arm: the day the grid falls on, and the fallback for
            a transfer storing no ``due_date``.

    Returns:
        The date from which a balance read counts this payment's principal.

    Raises:
        UndatedSettleError: When the leg's record carries no ``settled_on``
            (ruling **R-BAL147**: refused, never dated by a guess, and
            corrected through the app), from
            :func:`~app.utils.balance_predicates.require_settled_day`.  The
            refusal names the payment as its owner finds it in the app -- the
            transfer's own name and its two accounts, the figure, the
            paycheck and the due date, the ids beside them
            (:func:`_undated_payment_subject`) -- since leaf
            ``balance:X-bi-6-4d-1``, where ruling R-BAL147's "you fix the row
            through the app" was read onto the TRANSFER, the thing its owner
            edits once the movement re-parents.  It named the row the movement
            hangs off (``transaction_id``, the shadow, which no screen shows)
            through :func:`~app.utils.balance_predicates.settled_day` until
            then, an id ``X-bi-6-4d`` empties.  A log line only: no route
            translates the error and the 500 page renders none of its text;
            the loan's own page and the dashboard refuse on this state too,
            which is the refusal's known surface.
    """
    if leg.record is None:
        return installment_paid_by(
            origination_date, payment_day,
            loan_payment_due_date(leg, payment_day),
        )
    return require_settled_day(
        leg.settled_on,
        subject=lambda: _undated_payment_subject(leg),
        cause=_UNDATED_PAYMENT_CAUSE,
    )


#: Why a settled transfer's loan-side payment can carry no day, and the repair
#: its owner makes through the app: ``payment_visible_on``'s cause sentence
#: for :func:`~app.utils.balance_predicates.require_settled_day`.  Each claim
#: about the transfer's door is pinned through that door
#: (``test_loan_settled_legs.test_the_undated_refusal
#: .TestTheRefusalsRepairWorksThroughTheApp``): a day
#: typed into a side's box dates that side, and a revert then Mark Paid dates
#: the payment to the day of the click.  It asks for EACH account's day
#: because, today, a side whose day is only a guess follows the other side's
#: (ruling R-BAL142), so typing the loan's alone moves Checking's guessed day
#: too; ruling R-R116 (plan step X-cu) reverses that for a loan, and the
#: sentence is true under both.
_UNDATED_PAYMENT_CAUSE = (
    "Its transfer is settled, and a settle dates each side through the status "
    "seam, so this was written around the transfer service.  Correct it in "
    "place through the app (ruling R-BAL147): open the transfer from its "
    "source account's grid, in that paycheck's column, and type the day each "
    "account's money moved into that account's \"Money moved on\" box.  "
    "Reverting the transfer and marking it Paid again would instead date the "
    "payment to the day of that click"
)


def _undated_payment_subject(leg: TransferLeg) -> str:
    """Name a settled payment whose record carries no day, as its owner finds it.

    The words a reader of the log needs to FIND the transfer in the app
    (ruling **R-BAL147**: refused, and corrected through the app): the
    transfer's own name, which its popover is titled with; its two accounts
    -- the source account's grid draws it in the row named for the
    destination (``grid_view_service._short_display_name`` strips the leg
    label's prefix), the loan's own page refusing on this state too -- the
    figure, the paycheck whose column it sits in and its due date, which
    tell apart a loan's many payments between one pair of accounts; then the
    ids beside them.  Called only on the refusal
    (:func:`~app.utils.balance_predicates.require_settled_day`), so the
    relationships it reads cost the happy path nothing.

    Args:
        leg: The settled payment's to-side leg, its record attached.

    Returns:
        The subject words, e.g. ``The loan-side payment (movement 2) of
        transfer 3 "Mortgage" (Checking to Mortgage, $1,000.00 in the
        paycheck of 2026-02-27, due 2026-03-01)``; a transfer storing no
        name (the create door always writes one) omits the quoted name.
    """
    transfer = leg.transfer
    named = "" if transfer.name is None else f' "{transfer.name}"'
    due = "no due date" if leg.due_date is None else f"due {leg.due_date}"
    return (
        f"The loan-side payment (movement {leg.record.id}) of transfer "
        f"{transfer.id}{named} ({transfer.from_account.name} to "
        f"{transfer.to_account.name}, ${transfer.settled_figure:,.2f} in the "
        f"paycheck of {leg.pay_period.start_date}, {due})"
    )
