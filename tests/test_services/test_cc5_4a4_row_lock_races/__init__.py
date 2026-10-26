"""Plan step ``credit_card:CC-5-4a-4``: the deleted-row rule holds when two clicks race.

Ruling **R-CC96** (developer 2026-09-23, "Lock in both"): *"the database check
locks the row, so no deleted row can hold money from any writer, listed or not.
The app's two money writers (add purchase, Mark Paid) and two hiders (Delete,
Archive) also take that lock first, so whichever click lands second gets a
sentence.  Delete first: the companion sees 'Groceries was deleted: a purchase
cannot be recorded under it' and nothing is written.  Purchase first: your
delete waits a moment, then removes the occurrence and its $12.34 purchase, as
if it was there when you pressed Delete."*  Built "with a two-connection race
test for each order", which is this module -- for these pairs: purchase x
Delete, purchase x Archive and purchase x Mark Paid in both orders, Mark Paid x
Delete and Mark Credit x Delete in both orders; the popover's Actual
correction x Delete in the delete-first order only; Mark Paid x Archive in the
archive-first order only, for the words ruling **R-CC107** gives it.  NOT
raced: Delete x Archive, and any pair on a ONE-OFF row, whose delete removes
it from the table (the step's fifth review, L5 and M3).

The step's fourth review measured the race the ruling closes: a recurring $300
Groceries occurrence deleted in one session while another added a $12.34 KROGER
purchase ended ``(hidden, held) = (True, 1)`` -- the money out of every balance,
the period locked, no screen able to reach the row -- and the other order
answered the owner's Delete with a 500.

**Two layers, graded apart.**  :class:`TestTheDatabaseLocksTheRow` races raw
statements, the writer nobody enumerated, where only
:mod:`app.deleted_row_infrastructure`'s arrival arm stands.  Every other class
races the app's own doors as two REAL REQUESTS, because since plan step
``balance:X-bn`` (rulings **R-CC106**, **R-CC114**) the serialisation the doors
rely on is the REQUEST's: every command transaction a signed-in request opens
takes its owner's write lock before it reads any of the owner's data
(:mod:`app.db_transaction`), so the second click waits there, before its door
has read the row, and then reads the first click's committed work.
:class:`TestTheOwnersLockComesFirst` grades that order door by door.

**How a request race is staged** (:func:`_race`).  Each click is a request on
a thread of its own with no app context pushed, so the request pushes its own
and has its own session, exactly as production gives each request.  The
FIRST request runs until its COMMIT and is held there -- a ``before_commit``
hook on that thread alone -- still holding everything it locked.  The SECOND
starts, and the test waits until PostgreSQL shows it waiting for the owner's
write lock, keyed on that owner and in this test's own database (``pg_locks``,
never a sleep and never a lock anywhere on the cluster), or until the harness
gives up.  Then the first commits and the second runs to its answer.  **Every
race asserts the second click waited on the owner's key**, and that is each
race's control: with the owner's lock removed the second request does not wait
there, and every race below fails.

*Until plan step ``balance:X-bn`` the door races called the SERVICES directly
on two sessions, and each door took the row's own write lock
(``app.services.row_write_lock``, deleted at that step).  Three cases went with
it, each grading a moment the request's owner lock makes impossible: a Delete
whose caller had read the row's movements before its lock (the door's
movements re-read), and review 5's two deadlock pairs, which needed a door
holding a row lock while it asked for the owner's lock -- the owner's lock is
now a request's first lock, graded per door below and suite-wide by the lock
census (no endpoint takes a row lock and then the owner's lock).*
"""
