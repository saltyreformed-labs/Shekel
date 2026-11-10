"""
Shekel Budget App -- Anchor True-up Service

Single authoritative implementation of the anchor true-up mutation
for every account kind that carries an append-only anchor history:
checking accounts via :class:`AccountAnchorHistory` here, and loan
accounts via :class:`LoanAnchorEvent` in
:mod:`app.services.loan_anchor_service` (E-18 / Commit 16, decision
D-C; the loan half moved there at plan step ``recurrence:R20`` when
its third door crossed the module line ceiling -- the seam this
docstring always drew).  Both call sites switch on the same
:class:`AnchorTrueUpOutcome` enum so the route layer's response
composition is uniform; the cash door carries it inside an
:class:`AnchorTrueUpReport` (ruling R-CC79).

The checking-anchor path -- :func:`apply_anchor_true_up` -- backs the
grid and Net Worth Cockpit HTMX anchor-edit endpoint (``true_up``).
Its transactional core:

  1. Append an ``AccountAnchorHistory`` row -- an account, a balance, and
     the day it was true.
  2. Commit.

**Step 3 used to be a bulk-clear of entry flags, and its deletion is ruling
R-DH (d)** (``docs/audits/balance_architecture/archive/anchor_settle_partition.md``,
plan step S1-c).  A true-up flipped ``is_cleared = TRUE`` on every entry dated
on or before the SERVER's today, so whether a purchase counted as already
inside the asserted balance depended on the order two buttons were pressed:
record the purchase then true up and it cleared, true up then record and it
never did.  There is no flag to write now -- reconciliation is derived from the
purchase's own recorded posting day -- so the true-up appends one assertion
and nothing else.  It mutated ``accounts`` too until ruling R-EH deleted the
anchor cache columns (plan step X-f1c3c); the assertion IS the state now.
Confirming which outstanding purchases the
statement showed is a SEPARATE, user-driven step
(``reconcile_service.record_settled_days``) that the route offers after this commit
lands; keeping it out of this transaction is deliberate, because a same-day
re-assert is swallowed here as idempotent success and any reconciliation riding
along would be silently rolled back with it.

The loan-anchor path --
:func:`app.services.loan_anchor_service.apply_loan_anchor_true_up` -- backs
the loan dashboard's "Record loan balance as of date D" form.  It
shares the enum contract but operates on a different model and a
different mutation set:

  1. Append a ``user_trueup`` :class:`LoanAnchorEvent` row (the
     table is structurally append-only; no UPDATE/DELETE).
  2. Commit.

**Both paths are APPEND-ONLY now, and the checking path's optimistic lock
went with the columns** (ruling R-EN, plan step X-f1c3c).  The C-17
``version_id_col`` on ``Account`` raised ``StaleDataError`` when a concurrent
commit bumped the counter between a route's SELECT and its UPDATE -- but a
true-up no longer UPDATEs ``accounts`` at all, so there is no such flush and no
``STALE_CONFLICT`` outcome on either path.  No ASSERTION is lost by that: two
assertions of different balances are two FACTS, the later-observed one is
current, and neither is overwritten.  This is the contract the loan path has
documented since Commit 16, now shared.

**The posted LEDGER was a separate question, and the shared contract never
answered it.**  *History since plan task plan#3076, which deleted both paths'
re-syncs (the worked-out ledger reads the assertions as they stand, and no door
writes the stored ledger):* both paths re-synced the ledger after appending,
and a re-sync is a reconcile-to-target: read what is posted, subtract, INSERT
the difference.
Two of those interleaved both subtract the same posted state.  The cash side
had been serialised by accident (the deleted ``version_id`` UPDATE autoflushed
and took a row lock before the walk) and the loan side never was at all.  Plan
step X-f1c3c made the reconcile take a per-owner advisory lock for itself
(:mod:`app.services.user_write_lock`); **since plan step ``balance:X-bn`` that
lock is taken by every COMMAND transaction a signed-in request opens, before
it reads any of the owner's data** (:mod:`app.db_transaction`, ruling
**R-CC115**), so the reconcile's read is serialised before it runs and takes
no lock of its own.  That is not every transaction that reaches this module:
registration takes none (``account_service.create_account`` stages the
origination for a user who is not committed yet, so no other transaction can
see it), and a script that writes an existing owner's data takes none
(:mod:`app.services.user_write_lock` names the four operator scripts that
write such rows; the deploy's reconciles took every owner's at their start
until plan task plan#3077 deleted them).

**An assertion is refused only when it CHANGES NOTHING, and that rule is this
module's** (ruling **R-EQ**, plan step X-f1c4b).  Both doors, under the owner's
write lock their signed-in request's transaction already holds (above), read
the assertion that currently GOVERNS what the submission would govern, and
append only when the submission differs from it.  An identical submission
writes nothing and reports
``UNCHANGED``, which the routes render as success -- so a double-click, a
network retry and a back-and-resubmit are absorbed, while a correction never
is.

**Both doors carried a content-keyed UNIQUE INDEX for this until X-f1c4b, and
the index could not express the rule.**  ``uq_anchor_history_account_period_balance_day``
covered ``(account_id, anchor_balance, observed_on)`` and
``uq_loan_anchor_events_acct_date_bal_day`` covered ``(account_id, anchor_date,
anchor_balance, ((created_at AT TIME ZONE 'UTC')::date))``; each write door
translated the violation into idempotent success.  **A transport retry and a
deliberate re-assertion are byte-identical by construction**, so a key over the
row's own values must mis-classify one of them -- and it mis-classified the
correction: assert ``$500`` for a day, correct it to ``$600``, then re-assert
``$500`` for that day, and the index rejected the third write while the app
reported it saved and kept rendering ``$600``.  Comparing against the governing
row instead is exact in both directions, because "did this change anything" is a
question about STATE, which the row's contents alone cannot answer.

Two consequences worth stating, both measured before the indexes were dropped:

  * **The remaining exposure is a surplus audit row, not money.**  Two truly
    concurrent identical submissions could each pass the compare -- except they
    cannot, because the compare runs under the per-owner write lock
    (:mod:`app.services.user_write_lock`), which the submitting request's
    transaction took before reading any of the owner's data (plan step
    ``balance:X-bn``), so the waiter reads the winner's row.  Even without
    it the cost was
    ``$0.00``: a duplicate assertion's correction delta is zero, a zero delta
    books no line (``ledger_lines.account_correction_targets``), and same-day
    corrections merge on one key.
  * *History, superseded by plan step ``balance:X-bn``, which takes the lock
    where each signed-in request's command transaction begins and deleted
    every call below:*
    **The lock moved EARLIER, not merely inward, and the "first lock" property
    belonged to the CALLER.**  It was taken inside the reconcile, several
    statements in; plan step X-f1c4b had both doors take it before their first
    read.  That was only the invariant :mod:`app.services.user_write_lock`
    states ("this lock must be the FIRST lock a transaction takes") when
    nothing the caller did earlier had already taken a row lock -- and
    ``lock_user_writes`` ran through ``db.session.execute``, which AUTOFLUSHES,
    so a caller that assigned to an ORM row before calling here emitted that
    ``UPDATE`` first and inverted the order silently.  The three HTMX/loan
    doors did only reads beforehand.
    ``routes/accounts/crud.update_account`` took the lock at its own top for the
    same reason, against a deadlock between two of its OWN branches reproduced
    against a real database; **plan step X-f1e deleted the branch that raced**,
    so that route stopped reaching this module at all and kept the lock purely
    to hold the invariant on its type-change path.  None of that closed
    finding **N-193**, whose cycle is settle-versus-truncate: plan step
    ``balance:X-bn`` owns it, and closes it by taking the lock where every
    signed-in request's command transaction begins.

Pre-Commit-16 this consolidation eliminates two byte-identical
``try/except`` blocks in ``app/routes/accounts.py``; the loan
principal true-up (E-18) introduced by Commit 16 will extend this
service rather than paste a third copy.

Services boundary: no Flask imports, no ``request``/``session``/
``current_app``/``render_template``.  The route owns the response
rendering; this module returns what it decided -- an outcome enum, and on the
cash door the governing assertion either side of the write -- which the route
translates into its template/header pair.  The session itself is the project's
SQLAlchemy ``db.session`` proxy, which IS Flask-bound -- consistent
with every other service in ``app/services/`` (e.g. ``entry_service``,
``balance_resolver``).

**A cash balance is asserted at exactly ONE door, and that is plan step X-f1e**
(finding **N-195**).  ``routes/accounts/crud.update_account`` -- the full-form
account edit -- used to be a second one: it accepted an ``anchor_balance`` and
staged an assertion through :func:`stage_anchor_true_up`, sharing the definition
but not the DECISION.  The two answered the same submission differently, because
that form PRE-FILLS the current balance: saving a rename re-submitted it
unchanged, which the route read as "no change" while ruling R-EQ's rule here
reads a submission as new when it changes what GOVERNS, the day included.
Aligning the route on this module's rule would have been worse -- a rename would
then assert today's balance and absorb purchases the user never reconciled -- so
the SURFACE was deleted rather than the gate.  What remains is
:func:`apply_anchor_true_up`, reached from ``accounts.true_up`` on every screen
that shows a balance.

**And the TABLE now has one writer too, which is a different claim** (ruling
**R-ES**, plan step X-f1e2).  One door means one place a USER asserts a balance;
one writer means one place a ROW is appended, and until X-f1e2
``account_service.create_account`` was the second -- it constructed the
origination assertion itself, so that one row was written with no owner write
lock, no ruling R-EQ compare and no shared log line.  It calls
:func:`stage_anchor_true_up` now.  The ``notes`` column those two writers
existed to be told apart in went with the ruling: nothing in ``app/`` read it,
and it was a second definition of "the opening" beside the positional one
:func:`app.services.cash_ledger.cash_anchor_facts` sets.

One consequence is worth stating where the rule lives: **the amortizing-kind
refusal is no longer duplicated at a route validator.**
``_validate_update_account`` carried its own copy because that door reached the
stager without passing :func:`apply_anchor_true_up`'s gate; with the door gone,
:class:`AmortizingAccountAnchorError` is raised in one place.
"""

from __future__ import annotations

import enum
import logging
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from app.exceptions import ValidationError
from app.extensions import db
from app.models.account import Account, AccountAnchorHistory
from app.services import (
    cash_ledger,
    pay_period_service,
)
from app.utils.dates import display_today


logger = logging.getLogger(__name__)


class AmortizingAccountAnchorError(ValueError):
    """Raised when a CASH anchor true-up targets an amortizing loan.

    A loan's balance is never ``accounts.current_anchor_balance`` -- it is
    ledger-derived, and its true-up path is
    :func:`app.services.loan_anchor_service.apply_loan_anchor_true_up` (an append-only
    :class:`LoanAnchorEvent`).  Writing the cash
    column instead creates a second, stored, never-reconciled loan balance
    (plan-of-record finding B-15: the real Mortgage's column was set to
    $1.00 with an HTTP 200 while the ledger said $177,277.97, and the grid
    then rendered the $1.00).  The cash entry point refuses the kind so
    that cannot recur; routes translate this into a client error naming
    the loan path (ruling D4, step A1).
    """


class AnchorTrueUpOutcome(enum.Enum):
    """What a true-up door decided: to write, or that nothing changed.

    Returned by the loan door
    (:func:`app.services.loan_anchor_service.apply_loan_anchor_true_up`) and
    carried by the cash door's :class:`AnchorTrueUpReport`.  The route picks a
    partial template + status code + headers from this; the service never
    touches the response layer.

    Members:
        COMMITTED: The new ``AccountAnchorHistory`` row was written
            and the commit succeeded.  Route renders the success
            partial (200) and, where relevant, the OOB swap + HX-Trigger.
        UNCHANGED: The submission matched the assertion that already
            GOVERNS, so nothing was written and the session was rolled
            back (ruling R-EQ).  Route treats this as idempotent success
            -- the state the caller asked for is the state that stands --
            and renders the success partial without re-issuing the commit.
            **It was ``DUPLICATE_SAME_DAY`` until plan step X-f1c4b**, when
            it named a unique-index violation rather than a decision: the
            name said "you sent this twice", which is exactly the thing a
            content key cannot know.  This one says what is true.
    """

    COMMITTED = "committed"
    UNCHANGED = "unchanged"


@dataclass(frozen=True)
class AnchorTrueUpReport:
    """What :func:`apply_anchor_true_up` decided, and what governs either side.

    **The door reports what it knows, so nothing around it re-reads the ledger
    to learn it** (ruling **R-CC79**, developer 2026-09-23).  It returned the
    bare :class:`AnchorTrueUpOutcome` until then, so the route asked the ledger
    for the governing assertion three more times per grid save: once BEFORE the
    write and outside the owner's lock, for its acknowledgement test, and twice
    after it -- once for that test, the day it names and the grid's "as of"
    caption, and once more inside the grid's own draw.  Both governing
    assertions are read inside the lock that serialises the write, so a second
    tab's save cannot land between them and the acknowledgement compares two
    figures one transaction saw.

    **The before is the stager's own read, handed back** (ruling **R-CC85**,
    developer 2026-09-23).  :func:`stage_anchor_true_up` reads the latest
    assertion once, for ruling R-EQ's compare, and returns it as
    :attr:`AnchorStageReport.latest`.  The door read the same record itself
    until then, one statement before the stager, and on a save for today the
    two reads returned the same row.  So a save that writes reads the governing
    assertion TWICE when it is dated on or after the latest assertion's day, as
    a save for today always is -- the stager's latest, which is then also the
    record governing the submitted day, and the door's after -- and THREE when
    it is dated BEFORE that day: the stager reads the submitted day's own
    record between them, because the latest is then a later record and a
    different fact.  An ``UNCHANGED`` save reads no after, so one fewer.

    "Governs today" is the account's latest owner-declared assertion
    (:func:`app.services.cash_ledger.governing_anchor`, the non-raising twin of
    :func:`~app.services.cash_ledger.resolve_anchor`, one query between them).
    It reads no clock and needs none: both write doors refuse a future day
    (:func:`resolve_observation_day`), so the latest assertion is the one that
    governs today.

    Attributes:
        outcome: Whether the door wrote (``COMMITTED``) or found the submission
            already governing and rolled back (``UNCHANGED``, ruling R-EQ).
        governing_before: The assertion that governed today BEFORE the write:
            the stager's read of the latest assertion
            (:attr:`AnchorStageReport.latest`, ruling R-CC85), taken under the
            owner's lock and before anything was staged; ``None`` for an
            account carrying no assertion at all (fixture-only in production --
            ``account_service.create_account`` writes an opening).
        governing_after: The assertion that governs today AFTER the write, read
            under the same lock, before the commit releases it.  A back-dated
            write leaves it equal to :attr:`governing_before`, because an
            assertion for an earlier day does not govern today.  On
            ``UNCHANGED`` it IS :attr:`governing_before`, never re-read: nothing
            was written.  Never ``None``: a ``COMMITTED`` door has just appended
            an assertion, and an ``UNCHANGED`` one found an assertion governing
            the submitted day, which is at or before today.
    """

    outcome: AnchorTrueUpOutcome
    governing_before: cash_ledger.AnchorPoint | None
    governing_after: cash_ledger.AnchorPoint


@dataclass(frozen=True)
class AnchorStageReport:
    """What :func:`stage_anchor_true_up` decided, and the latest assertion it read.

    **The stager hands back the one read it makes of the latest assertion**
    (ruling **R-CC85**, developer 2026-09-23).  It returned a bare ``bool``
    until then, and the cash door asked the ledger for the same record itself
    one statement earlier, to report what governed today before its write: on
    a save dated on or after the latest assertion's day, as a save for today
    always is, two reads of one fact under one lock.  The door reports this
    field instead.  The account factory reads only :attr:`staged`, since an
    account it flushed a few statements earlier carries no assertion for
    :attr:`latest` to name.

    Attributes:
        staged: ``True`` when an assertion was appended to the session;
            ``False`` when the submission matched the assertion governing the
            submitted day and nothing was staged (ruling R-EQ).  Each caller
            decides what a decline means for ITS transaction.
        latest: The account's latest owner-declared assertion
            (:func:`app.services.cash_ledger.governing_anchor`), read under the
            owner's lock where the transaction holds one (a signed-in
            request's does; registration's takes none, and its new account
            carries no assertion to read) and BEFORE this assertion was
            staged, so it is what
            governed today before this write (both write doors refuse a future
            day).
            ``None`` for an account carrying no assertion at all, which is the
            state the account factory calls in.
    """

    staged: bool
    latest: cash_ledger.AnchorPoint | None


@dataclass(frozen=True)
class ObservationDay:
    """A civil day that has passed both of an assertion's bounds.

    **The bound is applied ONCE per write, and this type is what makes "once"
    structural** (plan step X-f1e2, ``ReconciledThrough``'s precedent one
    question over).  :func:`resolve_observation_day` alone mints one and
    :func:`stage_anchor_true_up` accepts nothing else.

    **Twice is not free, because the rule is CLOCK-DEPENDENT.**  The floor is
    ``min(earliest pay period start, today)``, so for an owner whose schedule is
    entirely in the future it moves FORWARD at midnight and a second application
    refuses the day the first produced -- and its refusal landed after the
    account row was flushed.  That is ``resolve then guard`` reading the clock
    twice, the defect ruling **R-ER** deletes, one layer up.

    **The attribute is NOT called ``day``, and that is not cosmetic.**
    ``datetime.date`` already has a ``.day`` -- the day of the MONTH -- so a raw
    date slipping past the annotation would satisfy the accessor and put an
    integer into an SQL bound.  A name a ``date`` cannot answer makes that an
    ``AttributeError`` at the first access instead.

    Attributes:
        civil_day: The bounded civil day, in the user's timezone (R-DH (b)).
    """

    civil_day: date


def resolve_observation_day(
    user_id: int, observed_on: date | None,
) -> ObservationDay:
    """Return the civil day an assertion is dated at, refusing an undatable one.

    **The ONE rule both writers of :class:`AccountAnchorHistory` ask** (ruling
    **R-ER**, plan step X-f1c4c): the origination assertion
    (``account_service.create_account``) and every later one
    (:func:`stage_anchor_true_up`).  It lives in THIS module because this module
    owns what an assertion is.  It was ``account_service``'s private
    ``_reject_undatable_observation`` while the factory was its only caller, and
    a second module reaching a private name is finding **N-33**'s shape rather
    than a way to share a rule.

    **It RESOLVES and refuses in one call, which closes the DEFAULT's half of a
    clock race and does not pretend to close the other half.**  Both callers
    previously defaulted an absent day to ``display_today()`` and then handed
    the result to a guard that read the clock AGAIN -- so a midnight tick
    between the two lines could refuse the function's own default.  Reachable,
    if barely: for a user whose earliest pay period starts tomorrow the floor
    becomes tomorrow the instant the day rolls, and the default (today) is then
    below it.  ``account_service``'s note about that race considered only the
    future arm, where the ``>`` test is indeed forgiving, and missed the floor.
    An absent day now returns *today* directly, so that case is unrepresentable:
    today is assertable by construction -- it is not in the future, and the
    floor is ``min(earliest, today)``.

    *A first version of this paragraph claimed the fusion made the race
    unrepresentable outright.  A neutral review refuted it and the correction is
    kept here rather than dropped, because the residue is a real window:* both
    forms PREFILL today into their date box (``routes/accounts/anchor``'s
    ``observed_on_value``, ``templates/accounts/form.html``), so the ordinary
    path submits a SUPPLIED day and takes the branch below.  For that branch the
    input's bound was computed at RENDER time and this floor is read at SUBMIT
    time, so the window is minutes or hours rather than two adjacent statements.
    It bites only a schedule that is entirely in the future, it errs toward
    refusing rather than accepting, and the refusal is rendered in place
    (``accounts._anchor_editor_error``) rather than swallowed.

    ``observed_on`` is USER-SUPPLIED and it is not merely a label: it opens the
    modelled-return window (``balance_at._asset_fold._AccrualWindow``, which
    materialises EVERY calendar day from it to the reader's horizon) and it is
    the first period a payroll contribution can be modelled into
    (``_asset_contributions``).  An unbounded value is therefore both a
    correctness defect and a work amplifier: a Property or 401(k) asserted "as
    of" year 1 would fabricate contribution history for every past period and
    fold over three quarters of a million days on every dashboard render.

    Two bounds, and each refuses for its own reason:

    * **Not in the future.**  A balance cannot have been observed on a day the
      user has not seen.  The loan door states the same rule on a different
      clock, which is finding **N-197**.
    * **Not before the earlier of the schedule's start and today.**  The
      accrual-window reason above; the floor takes the EARLIER of the two so a
      user whose periods are all still in the future can nonetheless assert what
      they hold today.  The bound is
      :func:`app.services.pay_period_service.earliest_recordable_day`, the SAME
      floor ruling R-EL gave the settle door -- one implementation, so the
      anchor doors and the settle doors cannot drift apart on where recordable
      history begins.  **That is a claim about the FLOOR only.**  "Not in the
      future" is still stated in three modules with three messages and two
      clocks -- here, ``status_seam.reject_future_settle_day``, and
      ``schemas/validation/loans.LoanAnchorTrueupSchema`` on ``date.today()``
      (finding **N-197**) -- so this function did not reduce that count.

    **It does NOT refuse an owner with no pay periods, and that split is ruling
    R-ER.**  The rule it replaced did, on the stated ground that "the account's
    anchor has a period to reference" -- which ruling R-EO falsified by deleting
    ``account_anchor_history.pay_period_id``.  The live reason belongs to
    ACCOUNT CREATION rather than to a day (the worked-out ledger files the
    opening's correction in the pay period containing its day, finding
    **N-192**; the stored ledger's reconcile did until plan task plan#3076), so
    it stays there as ``account_service``'s own precondition.
    Asking it here would have re-imposed on the true-up door exactly the refusal
    ruling R-EO deleted from it -- a balance the user typed, refused for want of
    a budgeting artifact that has nothing to do with what their bank holds --
    and would have answered a true-up with a message about creating an account.

    **The clock is the USER's** (ruling R-DH (b)).  ``display_today()``, never
    ``date.today()``: the process's UTC day is already tomorrow at 8pm Eastern,
    so the server's clock would refuse an assertion the user is making right
    now, and would default one made this evening to tomorrow.

    Args:
        user_id: The owner whose pay-period schedule sets the floor.
        observed_on: The candidate civil day, or ``None`` to take the default.
            ``None`` is what an omitted form field and a caller with no opinion
            both mean: "the balance I am asserting is true now".

    Returns:
        The :class:`ObservationDay` the assertion carries -- *observed_on* when
        one was supplied and passed both bounds, else the user's today.  **A
        TYPE rather than a bare date, so the bound cannot be applied twice**:
        see :class:`ObservationDay` for the clock-roll and concurrent-rebuild
        windows a second application opens.

    Raises:
        ValidationError: When the day is in the future or precedes the owner's
            recorded history.  A 400 rather than a programming error: both are
            ordinary input from a date box, and each message names the offending
            value and the bound it broke so the surface can render it verbatim.
    """
    today = display_today()
    if observed_on is None:
        return ObservationDay(today)
    if observed_on > today:
        raise ValidationError(
            f"Cannot assert a balance for {observed_on.isoformat()}: that day "
            f"has not happened yet (today is {today.isoformat()}).  A balance "
            "states what an account held on a day you have already seen."
        )
    floor = pay_period_service.earliest_recordable_day(user_id)
    if observed_on < floor:
        raise ValidationError(
            f"Cannot assert a balance for {observed_on.isoformat()}: your "
            f"recorded history starts on {floor.isoformat()}.  Use a day on or "
            "after that, or generate earlier pay periods first."
        )
    return ObservationDay(observed_on)


def stage_anchor_true_up(
    *,
    account: Account,
    new_balance: Decimal,
    observed_on: ObservationDay,
) -> AnchorStageReport:
    """Append a dated balance ASSERTION for ``account`` without committing.

    The flush-only in-memory core of :func:`apply_anchor_true_up`.  It does NOT
    clear past-dated entries and does NOT commit -- the caller owns the
    transaction.

    **It is the ONE writer of :class:`AccountAnchorHistory`, and that is ruling
    R-ES** (plan step X-f1e2).  Its two callers are the ``apply`` wrapper
    immediately below (every later assertion, from every screen that shows a
    balance) and :func:`app.services.account_service.create_account` (the
    origination).  The account factory used to construct the row itself, which
    made the origination the one assertion in the app written without the
    owner's write lock, without ruling R-EQ's did-this-change compare and
    without the shared log line; routing it here is what makes those rules
    properties of the TABLE rather than of whichever function did the INSERT.
    *The lock is no longer one of them: since plan step ``balance:X-bn`` it is
    the signed-in request's transaction's, not this function's, and an
    origination at registration is written with none.*

    *The history is worth one sentence because it inverts twice.*  The split
    existed to be SHARED with ``routes/accounts/crud.update_account``; plan step
    X-f1e1 deleted that door (finding **N-195**) and left this function with a
    single caller and a callerless ``notes`` parameter (finding **N-198**).
    Ruling R-ES then deleted the ``notes`` COLUMN -- unread by anything in
    ``app/``, and a second definition of "the opening" beside the positional one
    :func:`app.services.cash_ledger.cash_anchor_facts` already sets -- and gave
    the function its second caller back on better ground: not two SURFACES
    sharing a definition, but two EVENTS sharing a write door.

    **It decides whether there is anything to append, and that decision is
    ruling R-EQ.**  Under the owner's write lock (on a signed-in request, held
    since before its transaction read any of the owner's data, plan step
    ``balance:X-bn``), it reads which assertion governs the
    submitted day, and appends only when the submission differs
    from it.  Three properties are load-bearing and each is here rather than in
    a caller:

    * **The lock precedes the read.**  A compare-then-append is a
      read-modify-write, so an unserialised one lets two concurrent submissions
      each read the pre-state and both append.  *Since plan step
      ``balance:X-bn`` (ruling **R-CC115**) this function takes no lock.  On
      every signed-in path to it -- the true-up door, and the account factory
      reached from ``routes/accounts/crud.create_account`` -- the request's
      command transaction took the lock before reading any of the owner's
      data (:mod:`app.db_transaction`).  Registration reaches it through the
      account factory with no lock and needs none: the account is
      uncommitted, so no other transaction can submit against it.  The rest
      of this bullet is the history of the acquisition that step deleted.*
      **From ruling R-CC85 it was
      the cash door's only acquisition above that read**: the door
      (:func:`apply_anchor_true_up`) had taken its own a few statements
      earlier (ruling **R-CC79**) to guard a read of the latest assertion that
      this function now makes and hands back.  The
      lock is transaction-scoped, so it covers the door's after-read too,
      which precedes the commit that releases it.  It was NOT a
      guarantee that the advisory lock was the transaction's FIRST lock:
      ``lock_user_writes`` executed a statement and therefore AUTOFLUSHED, so
      a caller holding a dirty ORM row emitted that ``UPDATE`` -- and took its
      row lock -- before it.  That ordering was the CALLER's to keep (finding
      **N-193**), and it is why ``routes/accounts/crud.update_account`` took
      the same re-entrant lock at its own top even after plan step X-f1e
      stopped it reaching this function at all.
    * **The governing assertion is asked for, never re-derived, and asked
      ONCE when once answers it** (ruling **R-CC85**).  The first read is
      :func:`app.services.cash_ledger.governing_anchor`, the account's latest
      assertion, which the cash door also reports.  Only when that record is
      dated AFTER the submitted day is
      :func:`app.services.cash_ledger.governing_anchor_on` asked for the day's
      own.  Both are ONE query, ``cash_ledger._facts._governing_row`` --
      one ordering, ``(observed_on, created_at, id)`` DESC, matching the walk's
      replay -- and differ only in its horizon.  A local ``MAX``/``first()``
      here would be a second statement of that rule, which is the defect class
      this module's own history is made of.  The read that is skipped is
      PROVED redundant, from that query's filter and ordering alone:

      ``_governing_row`` returns the first row, in one TOTAL order (``id`` is
      unique, so no two rows tie), of one set S -- the account's
      owner-declared assertions -- and for a day D the first row of S_D, the
      members of S with ``observed_on <= D``.  Let L be the first row of S.
      If ``L.observed_on <= D``, L is a member of S_D; S_D is a subset of S,
      so no member of S_D precedes L; so L is the first row of S_D, and the
      second read would return L.  If S is empty, S_D is empty too and both
      reads return ``None``.  Only when ``L.observed_on > D`` is L outside
      S_D, and then the day's record is a different row, read for itself.  The
      proof needs nothing of the order but that both reads share it, and
      nothing of the horizon but that it is membership by ``observed_on <=
      D``.  Both would run in one transaction, before this assertion is
      staged, and no other can append to S between them: this function is
      the one writer of an owner-declared assertion (ruling R-ES), and every
      transaction that reaches it either holds the owner's lock (a signed-in
      request's, plan step ``balance:X-bn``) or writes an account no other
      transaction can see (registration's).  So they would see one S.  Reads
      per call: ONE
      when the day is on or after the latest assertion's (a save for today, an
      origination), TWO for a back-dated day.
    * **The comparison is against the row governing the SUBMITTED DAY, not the
      account's latest row.**  Two things follow, and both were measured.  The
      deleted unique index asked "does an identical row exist anywhere", so
      re-asserting a balance that had since been superseded was refused and
      reported as saved.  But comparing against the LATEST row instead has the
      mirror-image fault: a submission for an EARLIER day can never equal it, so
      a double-click on a back-dated correction appends every time -- reproduced
      on the loan door by two independent reviews of this step.  A submission
      for day D can only change what is true at or after D, so D is the horizon.
      **Plan step X-f1c4c is what made that reachable here**, by giving the cash
      door the date field the loan door has carried since Commit 16; the rule was
      installed one leaf earlier, deliberately, so a user-typed day never met the
      content-keyed index it replaced.

    **The day arrives ALREADY BOUNDED, and it is a TYPE that says so** (ruling
    **R-ER** for the rule, plan step X-f1e2 for the type).
    :func:`resolve_observation_day` supplies the default and enforces both
    bounds, so the same two rules govern the origination assertion
    ``account_service.create_account`` writes and every later one written here.
    This function took a raw ``date | None`` and re-resolved it, which read as a
    writer declining to trust its caller and was really the clock being read
    twice: the floor is time-dependent, so a midnight roll -- or a schedule
    rebuild committing -- between a caller's resolve and this one refuses the day
    the caller just produced.  Both doors resolve exactly once now, before
    anything is staged, and the resolver takes no lock of its own (one
    aggregate SELECT over pay periods).  *Until plan step ``balance:X-bn`` each
    resolved BEFORE the lock, so that a refused submission never took the
    owner's write lock; that step takes the lock where a signed-in request's
    command transaction begins, above both doors, so a refused submission on
    that path holds it until its transaction ends.*

    **What it stages shrank twice, and both shrinks are the same ruling
    applied one table apart.**  It used to re-point ``current_anchor_period_id``
    and write ``current_anchor_balance`` before appending the row; ruling R-EH
    deleted those columns as a denormalized copy of the row itself.  It used to
    file the row against a pay period; ruling R-EO deleted THAT, because a
    balance assertion is a fact about a bank and a schedule operation must not
    be able to destroy it.  What is left is the assertion: an account, a
    balance, and the day it was true.

    The amortizing-kind gate (:class:`AmortizingAccountAnchorError`) lives on
    :func:`apply_anchor_true_up`, deliberately NOT here.  It was placed there so
    the second door -- which refused the kind at its own route validator -- was
    not gated twice; plan step X-f1e deleted that door and its duplicate gate,
    so the rule is now stated exactly once, on the only public entry point that
    asserts a cash balance.

    Args:
        account: An attached :class:`Account` row.  Caller owns the
            ownership check.
        new_balance: The validated :class:`Decimal` balance being asserted.
        observed_on: The :class:`ObservationDay` the balance is asserted TRUE
            for (ruling **R-DH**).  Only :func:`resolve_observation_day` mints
            one, so an unbounded day cannot reach this line.

    Returns:
        An :class:`AnchorStageReport`.  Its ``staged`` is ``True`` when an
        assertion was appended to the session and ``False`` when the submission
        matched the governing assertion and nothing was staged; the caller
        decides what that means for ITS transaction
        (:func:`apply_anchor_true_up` rolls back and reports ``UNCHANGED``, the
        account factory raises).  Its ``latest`` is the account's latest
        assertion as read before this assertion was staged, which the cash door
        reports as what governed today (ruling R-CC85).

    **It raises NOTHING, and saying so is a correction.**  It documented a
    ``RuntimeError`` "when the account carries no assertion at all, from
    ``cash_ledger.resolve_anchor``" -- but it does not call ``resolve_anchor``.
    It calls :func:`app.services.cash_ledger.governing_anchor` and, for a
    back-dated day, :func:`app.services.cash_ledger.governing_anchor_on`, and
    each returns ``None`` on an account with no history precisely because that
    is an honest answer to a WRITER where it is a broken invariant to a
    reader.  The claim
    was true of an earlier draft and load-bearing in the wrong direction: an
    account with no assertions is exactly the state
    ``account_service.create_account`` is in when it calls here.  The
    ``ValidationError`` the day bounds raise now belongs to each door's own
    :func:`resolve_observation_day` call, above this function.
    """
    day = observed_on.civil_day
    # Ruling R-EQ: the owner's write lock precedes the READS the decision
    # below is made from -- on a signed-in request, held since before its
    # transaction read any of the owner's data (plan step ``balance:X-bn``,
    # :mod:`app.db_transaction`); registration's uncommitted account needs none.
    # Ruling R-CC85: the latest assertion, read ONCE and handed back.  Dated on
    # or before the submitted day, it IS the one governing that day (the proof
    # is in the docstring), so a second read would return the same row.  Dated
    # after it, the day's own record is a different fact and is read for itself.
    latest = cash_ledger.governing_anchor(account.id)
    if latest is None or latest.observed_on <= day:
        governing = latest
    else:
        governing = cash_ledger.governing_anchor_on(account.id, day)
    if governing is not None and (
        (governing.observed_on, governing.balance) == (day, new_balance)
    ):
        return AnchorStageReport(staged=False, latest=latest)

    db.session.add(AccountAnchorHistory(
        account_id=account.id,
        anchor_balance=new_balance,
        observed_on=day,
    ))
    # Both write doors reach this line, so the audit trail is uniform whichever
    # event wrote the assertion -- an origination or a later true-up.  The day
    # is the fact plan step X-f1c4c exists to record.
    logger.info(
        "Anchor assertion staged: account %d at $%s as of %s",
        account.id, new_balance, day.isoformat(),
    )
    return AnchorStageReport(staged=True, latest=latest)


def apply_anchor_true_up(
    *,
    account: Account,
    new_balance: Decimal,
    observed_on: date | None = None,
) -> AnchorTrueUpReport:
    """Append a balance assertion for ``account`` and commit.

    Stages the assertion via :func:`stage_anchor_true_up` and commits.  Returns
    an :class:`AnchorTrueUpReport` the caller translates into its rendered
    response.  *It re-based the account's posted anchor corrections in the
    stored ledger between the two until plan task plan#3076; the worked-out
    ledger reads the new assertion as it stands.*

    **It reports the assertion that governs today on both sides of the write,
    and reads both under the owner's lock** (ruling **R-CC79**, developer
    2026-09-23).  It reported only the outcome until then, so the route read
    the governing assertion before calling -- outside the lock -- and again
    after, which let two tabs saving at once show the wrong acknowledgement:
    the "before" figure it compared against could be one a concurrent save had
    already replaced.  **The before is the stager's read** (ruling **R-CC85**):
    :func:`stage_anchor_true_up` reads the latest assertion once for its own
    compare, under the lock the request's transaction already holds, and
    hands it back, so this door reads nothing before the write (the stager
    took the lock itself until plan step ``balance:X-bn``).  The after-read
    precedes the commit that releases the lock.  Reads of the governing
    assertion per call: TWO for a save dated on
    or after the latest assertion's day, as a save for today always is (the
    stager's latest, which is then also the record governing the submitted
    day, and the after); THREE for one dated before that day (the stager
    reads the submitted day's record too).  An ``UNCHANGED`` submission wrote
    nothing, so its after IS its before and is not read at all.

    **The C-17 optimistic lock left this path at plan step X-f1c3c** (ruling
    R-EN), and the reason is that the path stopped writing the row the lock
    guarded.  ``version_id`` increments when the ORM UPDATEs ``accounts`` and
    on nothing else; once ruling R-EH deleted the anchor cache columns a
    true-up only INSERTs a history row, so ``StaleDataError`` became
    structurally unreachable here.  **Measured** against the dev database and
    rolled back: adding an ``AccountAnchorHistory`` row and flushing leaves
    ``version_id`` at 33, and the very next line writing
    ``current_anchor_balance`` takes it to 34.

    The step accepted what that means rather than working around it.  **An
    assertion history is append-only, so a second tab overwrites no
    ASSERTION**: two assertions of different balances are two facts, the
    later-observed one is current, and neither is lost -- verbatim the contract
    :func:`app.services.loan_anchor_service.apply_loan_anchor_true_up` has
    documented since Commit 16.  This makes the cash half the same shape rather
    than the exception.  Bumping
    ``version_id`` deliberately to keep the lock alive was rejected: a write to
    ``accounts`` whose only purpose is to keep a lock alive is a mechanism with
    no fact under it.

    **That is a property of ONE table in a transaction that mutated three, and
    a first version of this paragraph stated it as though it covered the whole
    call.**  *History since plan task plan#3076, which deleted the re-sync this
    paragraph is about; the call now writes the one table.*  The
    ``sync_account_anchor_postings_all_scenarios`` that stood below was a
    RECONCILE-TO-TARGET: it read what was posted, subtracted that from what the
    assertions said, and INSERTed the difference into ``budget.journal_entries``
    / ``budget.account_postings``.  Nothing about append-only makes a
    read-modify-write safe, and the deleted ``version_id`` UPDATE had been
    serialising it by accident -- it autoflushed and took a row lock before the
    walk.  Measured with the interleave forced at the reconcile's read: two
    concurrent true-ups on an account reconciled at ``$4,000.00`` both answer
    200, both assertions survive, the resolver returns one of them -- and the
    linked ledger settles at ``$1,000.00`` against a resolved ``$2,000.00``,
    with the trial balance still ``$0.00`` because the anchor-equity leg
    mirrors the error.  The serialisation was made EXPLICIT at plan step
    X-f1c3c, a per-owner advisory lock taken inside the sync; **since plan step
    ``balance:X-bn`` it is taken by every command transaction a signed-in
    request opens, before it reads any of the owner's data**
    (:mod:`app.db_transaction`), so every door into that same window (the
    settle self-heal, the pay-period resync) was covered before its first read
    and the sync took none of its own.  *This list also named "the direct
    anchor edit", the full account form's balance field, which plan step X-f1e
    deleted.*
    The waiting transaction re-reads under READ COMMITTED, which ruling `balance:R-GU`
    guarantees for a WRITER (its override is also ``READ ONLY``), so it saw
    the winner's postings and reconciled to the true merged target.
    *History until plan step ``balance:X-bn``, which deleted every acquisition
    this paragraph names:* **since plan step X-f1c4b the SAME lock was taken
    one layer up**, in :func:`stage_anchor_true_up`, because ruling R-EQ's
    compare-then-append is itself a read-modify-write.  Ruling R-CC79 took it
    once more, HERE, above a read of what governs today that this door then
    made; ruling R-CC85 moved that read into the stager, below the stager's own
    acquisition, and this door's acquisition went with it.  It was re-entrant
    and transaction-scoped, so the reconcile's repeat cost nothing and the
    after-read below still held it.  On THIS path it was also the transaction's
    first lock -- the route did only reads before calling (measured, statement
    by statement, by a neutral concurrency review) -- but that was a property
    of the route, not of the lock, and finding **N-193** stayed open for the
    settle paths regardless.

    **It touches no entry, and that is ruling R-DH (d).**  It used to bulk-flip
    ``is_cleared`` on every entry dated on or before the server's today, which
    made "is this purchase already inside the balance the user just typed"
    an answer decided by the order two buttons were pressed.  The flag is
    gone; reconciliation is derived from each purchase's own recorded posting
    day.  Which outstanding purchases the statement actually showed is a
    separate step the route offers AFTER this commit succeeds
    (``reconcile_service.record_settled_days``) -- and it is deliberately not
    folded in here, because an UNCHANGED submission rolls this transaction
    back, so a reconciliation riding in it would be silently discarded while
    the UI reported a save.

    **The ``try`` / ``except IntegrityError`` around the re-sync left with the
    index** (ruling R-EQ, plan step X-f1c4b).  It existed to catch the F-103
    unique violation that the re-sync's autoflush surfaced and translate it into
    an outcome; with the decision made BEFORE anything is staged, an
    ``IntegrityError`` here is an unexpected constraint failure and its correct
    disposition is the 500 it now gets.

    Args:
        account: An attached :class:`Account` row.  Caller is
            responsible for the ownership check (route uses 404 for
            cross-owner access).
        new_balance: The validated :class:`Decimal` balance being asserted.
            Caller is responsible for constructing this from
            schema-validated form data via ``Decimal(str(...))``.
        observed_on: The civil day the balance is asserted TRUE for, or ``None``
            for the user's today.  Bounded HERE, by the shared
            :func:`resolve_observation_day` -- this function adds no rule of its
            own about the day and must not, or the two write doors would answer
            a back-dated submission differently.  It bounded it inside
            :func:`stage_anchor_true_up` until plan step X-f1e2; the resolve
            moved out to the doors so the account factory, which must refuse
            before it creates a row, does not make it the second application of
            a clock-dependent rule.

    Returns:
        The :class:`AnchorTrueUpReport`: the outcome -- ``UNCHANGED`` when the
        submission matched the governing assertion, in which case this
        function has rolled the session back and written nothing -- and the
        assertion governing today before and after the write, both read under
        the owner's lock.

    Raises:
        ValidationError: When *observed_on* is in the future or precedes the
            owner's recorded history (:func:`resolve_observation_day`).  Raised
            before anything is staged, so the session is clean; the owner's
            write lock the request's transaction already holds (plan step
            ``balance:X-bn``) is released when the request ends that
            transaction.  The route renders it as a designed 400 fragment.
        AmortizingAccountAnchorError: When ``account`` is an amortizing
            loan (``account_type.has_amortization``).  A loan's balance
            is ledger-derived and asserted through
            :func:`app.services.loan_anchor_service.apply_loan_anchor_true_up`;
            the cash column must not become a second stored loan balance
            (B-15 / ruling D4).
            Raised BEFORE anything is staged, so the session is clean.
        IntegrityError: When the commit, or the after-read's autoflush of the
            staged assertion, trips a constraint (the posting re-sync's
            autoflush did, until plan task plan#3076).  No longer caught
            here -- ruling R-EQ made the only reachable one (the deleted
            unique index) impossible -- so it
            propagates as the 500 an unexpected DB-level failure deserves.
            Flask's teardown removes the session, which rolls the transaction
            back and releases the advisory lock.
    """
    acct_type = account.account_type
    if acct_type is not None and acct_type.has_amortization:
        raise AmortizingAccountAnchorError(
            f"account {account.id} is an amortizing loan; assert its "
            "balance through loan_anchor_service.apply_loan_anchor_true_up, "
            "never as a "
            "cash anchor"
        )

    # Bounded ONCE, here, before anything is staged (plan step X-f1e2); it was
    # above the lock too until plan step ``balance:X-bn``, which takes the lock
    # before the route runs.  The kind gate runs first so an
    # amortizing account is refused for what it IS before its day is judged.
    day = resolve_observation_day(account.user_id, observed_on)

    # Ruling R-CC85: the stager reads the latest assertion once, under the
    # owner's lock the request's transaction already holds, and hands it back
    # -- what governed today, read inside the same serialisation as the write,
    # so a concurrent save cannot land between it and the after-read below.
    # This door reads nothing first.
    staging = stage_anchor_true_up(
        account=account, new_balance=new_balance, observed_on=day,
    )
    governing_before = staging.latest

    if not staging.staged:
        # Ruling R-EQ: the submission IS the governing assertion, so there is
        # nothing to append and nothing for the reconcile to move.  Roll back
        # rather than returning on an open transaction -- the transaction holds
        # the owner's write lock (taken before it read any of the owner's data,
        # plan step ``balance:X-bn``), and only a commit or a rollback
        # releases it.
        # Read the id BEFORE the rollback: afterwards the instance is expired
        # and touching an attribute opens a fresh transaction purely to recover
        # a value already in hand.
        account_id = account.id
        db.session.rollback()
        logger.info(
            "Anchor true-up for account %d asserts the balance that already "
            "stands; nothing written (idempotent success)",
            account_id,
        )
        # Nothing was written, so what governs today is what governed before:
        # reported, not re-read.  Never ``None`` here -- the stager declines
        # only when an assertion governs the submitted day, at or before today.
        return AnchorTrueUpReport(
            outcome=AnchorTrueUpOutcome.UNCHANGED,
            governing_before=governing_before,
            governing_after=governing_before,
        )

    # After the write and BEFORE the commit, because the commit releases the
    # lock: read after it, a second tab's save could already govern.  Its
    # query autoflushes the staged assertion, so it reads the write it follows.
    # (The stored ledger's anchor re-sync stood here until plan task
    # plan#3076; the worked-out ledger reads the assertion as it stands.)
    governing_after = cash_ledger.governing_anchor(account.id)
    db.session.commit()
    return AnchorTrueUpReport(
        outcome=AnchorTrueUpOutcome.COMMITTED,
        governing_before=governing_before,
        governing_after=governing_after,
    )
