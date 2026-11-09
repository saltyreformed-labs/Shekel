"""
Shekel Budget App -- The per-user write lock

ONE transaction-scoped PostgreSQL advisory lock, keyed on the owning user, so
two writes of one user's data that both take it never overlap (the writers
that take none are named below).  **Since plan task plan#3077 it is taken
from exactly one module** (rulings **R-CC106**, **R-CC114**, **R-CC115**,
**R-CC121**): :mod:`app.db_transaction`, at the start of every command
transaction a signed-in request opens -- before it reads any of the owner's
data; the signed-in user's own row and the role row joined to it, read to
sign the request in, are the only rows read first, and the user's row is
re-read under the lock -- and at a sign-in right after the account is found.
*From plan step ``balance:X-bn`` until that task this module's
``lock_every_user_writes`` took every owner's too, at the start of each of the
deploy's three ledger reconciles, its only callers, which went with the
stored ledger's writes (plan step plan#2914).*  No service takes it for
itself, and ``tests/test_arch/test_the_owner_lock_has_one_home.py`` refuses
one that tries.  **The writers that take none, each named so it is not
mistaken for covered**: registration (``/register`` and
``scripts/seed_user.py``) writes a user that is not committed, so no other
transaction can reach it; the deploy (``scripts/init_database.py``) runs
before ``entrypoint.sh`` hands over to gunicorn, so no request is there to
race it; and every script that writes an EXISTING user's rows holds no
request, so nothing takes the lock for it.
Four operator scripts write rows the lock covers and take none --
``scripts/reset_mfa.py`` (``mfa_configs``), ``scripts/rotate_totp_key.py``
(every ``totp_secret_encrypted``), ``scripts/rotate_sessions.py`` (every
``users.session_invalidated_at``) and ``scripts/seed_companion.py``'s
existing-user branch (``role_id`` and ``linked_owner_id``, the lock's key) --
so each can race a sign-in or an MFA door that holds the lock (reported at
plan step ``balance:X-bn``'s checkpoint 4 review, 2026-09-29).  *It was
taken by sixteen calls in nine modules inside the write paths that needed it
most, plus three that plan step ``credit_card:CC-5-4a-4`` put inside
``row_write_lock`` (the row locks' module, deleted with them at plan step
``balance:X-bn``), each taking it at its own point in the transaction, which
is what made the deadlock below reachable.*

Two families of write are why the lock exists at all, and they need the SAME
lock because the second reads the first's output:

* **The structural pay-period mutations** -- top-up / extend / truncate, and so
  regenerate and reset, which are compositions of those.  Each counts or
  classifies the user's periods and then appends or deletes against that count.
* **Every posting-ledger RECONCILE** -- the cash anchor reconcile
  (:mod:`app.services.account_posting_service`) and the loan reconcile
  (:mod:`app.services.loan_posting_service`).  Each reads what the ledger has
  posted, subtracts it from what the account's facts say it should hold, and
  writes the difference.

**Why one lock and not two.**  A reconcile does not only read the account's
ledger: it derives each correction's pay period from the OWNER'S CALENDAR
(:meth:`app.services.pay_calendar.PayCalendar.filing_period`, loaded through
:func:`app.services._posting_reconcile.filing_calendar_for`), and
``journal_entries.pay_period_id`` is an ``ON DELETE CASCADE`` FK.  So a
concurrent truncate can delete the very period a reconcile is filing under, and
the correction it just wrote goes with it.  The consistency boundary of a
reconcile is therefore the user's ledger AND the calendar it keys on -- the
union, which is exactly this lock's subject.  It is also one KEY per user, so
no transaction has to order two of them against each other.

**Deadlock: what one key does and does not buy, corrected on evidence.**  An
earlier version of this docstring said deadlock was "structurally impossible on
every request path".  **That was FALSE, and a neutral adversarial review
reproduced the cycle**, because the argument considered only
advisory-vs-advisory ordering while this lock was taken in transactions that
also held ROW locks.  The cycle, as it stood until plan step ``balance:X-bn``:

* A settle took row locks FIRST.  ``update_transfer`` UPDATEd the transfer and
  both shadow transactions, those flushed, and only then did the posting sync
  reach this lock -- measured at statements 2-4 and 19 of one loan-payment
  settle.
* A truncate or reset took this lock first and then bulk-DELETEd pay periods,
  which CASCADE to ``budget.transactions`` and so took row locks on exactly
  the rows a concurrent settle might hold.

Two such transactions, same user, opposite orders: PostgreSQL detected it and
aborted one with ``DeadlockDetected``.  **No money was corrupted** -- the loser
rolled back atomically -- but the victim was an unhandled 500 on a money route.
It needed a settle and a schedule rebuild for one user to overlap, which is two
browser tabs, and it did not exist before this lock did.

**The real invariant, stated so the next author can hold it: this lock must be
the FIRST lock a transaction takes.**  *Until plan step ``balance:X-bn`` it was
a property each write door had to hold for itself, and most did not: the
settle paths took row locks first (finding **N-193**), plan step
``credit_card:CC-5-4a-4`` (ruling **R-CC100**) added the lock ahead of the row
locks it introduced, and a census over the whole suite (2026-09-24) still
found 27 endpoints that locked a row and then asked for this lock in the same
request -- the popover's Save, a purchase's edit and delete, carry-forward and
the reconcile tick among them.*  **It is now structural on every request
path**: the lock is taken where each command transaction a signed-in request
opens begins (:mod:`app.db_transaction`), so the only earlier statement is
the signed-in user's own row, and no row lock rides on it.  *The deploy was
the one place it was not first until plan task plan#3077*: its ledger
reconciles took every owner's lock in the transaction that had already run
the migrations and the infrastructure DDL, with their table locks, safe only
because ``entrypoint.sh`` runs ``init_database.py`` before gunicorn serves.
The deploy takes no owner's lock since that task deleted those reconciles.

Shipping the lock with a detected-and-rolled-back deadlock was strictly better
than shipping the silent ledger divergence it replaced; shipping it with a
docstring claiming the deadlock impossible was not, which is why this
paragraph said so until the step that made it so.

**Why a lock at all, rather than a constraint.**  A reconcile emits the
DIFFERENCE between target and posted, and repeated deltas under one key are the
design (a correction whose basis moves is adjusted by a further entry, never
edited -- the ledger is append-only).  No unique index can distinguish a
legitimate second delta from a racing duplicate.  Nor can the read take a row
lock: when nothing is posted yet there are no rows to lock, which is the
classic phantom.  A predicate lock is what is wanted, and in PostgreSQL that is
either SERIALIZABLE isolation with a retry loop in every route, or an advisory
lock.  This is the advisory lock.

**Why the reconcile ever appeared to be safe.**  Until plan step X-f1c3c a cash
true-up also UPDATEd ``accounts``, and that UPDATE autoflushed and took a row
lock BEFORE the walk -- serialising the reconcile by accident, through a column
that had nothing to do with it.  Ruling R-EN deleted the column and with it the
accident.  Measured, with the interleave forced at the reconcile's read: two
concurrent true-ups on an account reconciled at ``$4,000.00`` both answer 200,
and the account's linked ledger settles at ``$1,000.00`` while its resolved
assertion reads ``$2,000.00`` -- both sides wrong, trial balance still
``$0.00`` because the anchor-equity leg mirrors the error, so nothing fails
loudly.  The LOAN reconcile never had even the accident: it is fed by an
append-only event table, so it has carried the same race since Commit 16, and
ruling R-EN cited it as the precedent to copy.

Transaction-scoped: PostgreSQL releases the lock at COMMIT or ROLLBACK, so it
cannot leak -- and a request that commits and goes on writing takes it again
in its next transaction (:mod:`app.db_transaction`).  A request's transaction
takes it once: at its start, or, at a sign-in, right after the account is
found.  *The deploy's three reconciles each took every owner's lock again in
its one transaction, relying on the lock being re-entrant, until plan task
plan#3077 deleted them; nothing takes it twice in one transaction since.*

Flask-isolated -- takes and returns plain data, never imports ``request`` /
``session``.  Takes no transaction of its own: the caller owns the boundary,
and the lock lives exactly as long as that transaction.
"""

from sqlalchemy import Connection, func, select

# Advisory-lock namespace for the per-user write lock.  The two-argument
# ``pg_advisory_xact_lock(namespace, user_id)`` form keys the lock on
# ``(this constant, user_id)``, so it can never collide with some other
# advisory lock that happens to use the same ``user_id`` as a single key.  The
# value is arbitrary but FIXED -- "SHKL" in ASCII -- and fits a signed int4
# (< 2**31 - 1).
#
# It is deliberately the SAME VALUE this constant held while it lived in
# ``pay_schedule_service`` as ``_PAY_SCHEDULE_LOCK_NAMESPACE``.  Changing it
# during the move would mean old and new code taking DIFFERENT keys for the
# same pay-period mutation, so a rolling deploy would run a window with no
# schedule lock at all.
_USER_WRITE_LOCK_NAMESPACE = 0x53484B4C  # 1397246796
# The decimal above read 1397705036 in this constant's OLD home and was
# carried over with it; the hex is the value PostgreSQL actually keys on
# (confirmed in a real `deadlock detected` DETAIL line: advisory lock
# [.., 1397246796, ..]), so only the comment was ever wrong.


def take_owner_write_lock(connection: Connection, owner_id: int) -> None:
    """Take *owner_id*'s write lock on *connection* for the rest of its transaction.

    The one statement of the lock, taken by one caller:
    :mod:`app.db_transaction`, at the start of every command transaction a
    signed-in request opens and at a sign-in (the module docstring; the
    deploy's every-owner form called it too until plan task plan#3077).
    Blocks until any other transaction
    holding the same key commits or rolls back; PostgreSQL releases it
    automatically at this transaction's end.

    **On a CONNECTION, never through the session**: a session statement
    autoflushes, so a transaction begun with rows already staged would write
    them -- and take their row locks -- before this one, which is the order
    the module docstring's invariant forbids.  *It was
    ``lock_user_writes(user_id)``, a session statement, until plan step
    ``balance:X-bn``; that step deleted every caller it had.*

    The lock is not a substitute for the constraints underneath it: a duplicate
    PAYDAY is still forbidden by ``uq_pay_periods_user_start``.  The lock is
    what a writer has instead of such a constraint when the quantity it must
    protect is something it READ rather than a row it is about to write -- a
    posted SUM for the reconciles, and since ruling **R-EQ** (plan step
    X-f1c4b) the governing assertion for the two anchor doors, whose duplicate
    rule moved out of a unique index for exactly that reason.

    Args:
        connection: The connection whose transaction takes the lock.
        owner_id: The id of the user whose data the transaction writes, used
            as the lock's second key.
    """
    connection.execute(
        select(func.pg_advisory_xact_lock(_USER_WRITE_LOCK_NAMESPACE, owner_id))
    )
