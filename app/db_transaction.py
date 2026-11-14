"""
Shekel Budget App -- what a request's transaction IS (plan step balance:X-i3)

**A request is a QUERY or a COMMAND, and its transaction says which.**  A query
runs at ``REPEATABLE READ, READ ONLY`` -- one snapshot of the database for every
statement it issues, and no write possible inside it.  A command runs at
PostgreSQL's default ``READ COMMITTED``, writable, exactly as every request did
before this module existed.

**Why a query needs one snapshot** (finding **N-353**).  Every figure a render
publishes is assembled from many statements, and under ``READ COMMITTED``
*each statement gets its own snapshot*.  So a pass can read the owner's pay
calendar in one statement and the rows filed against it forty statements later,
and the two can disagree: ``/grid``'s rolling top-up appends a pay period,
repopulates it from every active template and commits mid-render, which leaves a
concurrent render holding rows filed in a period its calendar never saw.  Nine
sites in ``app/`` reasoned about that class in their own words and four were
independent discoveries with four different local accommodations.  Measured
here, against this application's own session: two counts across one concurrent
committed append answer ``(1, 2)`` under ``READ COMMITTED`` and ``(1, 1)`` under
this module's query mode.

**Why a command may NOT have one snapshot, which is the harder half.**  The
posting-ledger reconciles are read-modify-write under the owner's write lock
(:func:`take_owner_write_lock`, below): hold the lock, re-read what is posted,
write the difference.  The waiting transaction's re-read is
*required* to see the winner's just-committed postings, and only ``READ
COMMITTED`` shows them -- under one snapshot the waiter would re-read its own
pre-lock picture and reconcile to a stale target, which is the divergence ruling
**R-EN** measured at ``$4,000.00`` asserted against ``$1,000.00`` settled, with
the trial balance still ``$0.00`` because the anchor-equity leg mirrors the
error.  A snapshot is therefore not a strictly better setting to run everything
at; it is right for reads and wrong for writes, and the line between them is
what this module draws.

**Why ``READ ONLY`` and not merely ``REPEATABLE READ``.**  It costs nothing and
it converts "a render wrote" from a thing a reviewer must notice into a thing
PostgreSQL refuses, which is what makes the boundary structural rather than a
convention: a GET that writes fails loudly on its first write statement instead
of quietly costing its own render the snapshot.

**What ``READ ONLY`` does NOT refuse is ONE mechanism, and naming it exactly is
the point.**  Measured statement by statement, each in its own transaction:
``pg_advisory_xact_lock`` SUCCEEDS inside a read-only transaction and assigns no
transaction id, while every row-lock strength is refused -- ``FOR UPDATE``,
``FOR SHARE``, ``FOR NO KEY UPDATE`` and ``FOR KEY SHARE`` all raise ``cannot
execute ... in a read-only transaction``, which covers the bank-line lock
``statement_match._resolve`` takes with ``with_for_update(key_share=True)``.
*The example here was ``credit_workflow``'s C-19 payback lock, the same
strength, until plan step ``balance:X-bn`` deleted it.*

So the gap is advisory locks alone: a render that took the owner's write lock
would block every writer for that owner for the length of the page and then
read from a frozen snapshot, with nothing raising.  **Closed by construction
since plan step ``balance:X-bn``**: the owner's write lock is taken in two
functions of this module, and both refuse a query's transaction --
:func:`_bind_transaction_mode` returns before its COMMAND arm, and
:func:`_lock_the_open_transaction` (the command transaction already open
when the owner becomes known) checks the mode first -- so no render can take
it outside a :func:`write_transaction` block, which is a command.
``tests/test_arch/test_the_owner_lock_has_one_home.py`` refuses any other
module that names the lock's statement, its key, or a function that takes it;
``tests/test_services/test_user_write_lock.py`` measures both arms on real
requests, a render's taking no lock among them.  *Until that step this
paragraph said the hole was closed only by a census of which functions a GET
could reach.*

*This paragraph claimed ``FOR UPDATE`` was allowed too, until a peer session
re-measured it.  The first measurement taken here had it right and an
adversarial review contradicted it; the review was believed over the
measurement, which is the failure this project's own rule names -- if you
cannot cite it you cannot claim it, and a citation is the statement you ran,
not the report you were handed.*

**Why the mode is bound at ``after_begin`` and not in a before-request hook.**
``SET TRANSACTION`` must precede the transaction's first statement, and
``before_request`` cannot guarantee that: ``_attach_request_id`` reads
``current_user``, which loads the user row, and Flask runs before-request hooks
in registration order.  Binding at the session's own ``after_begin`` fires
between ``BEGIN`` and the statement that caused it, whoever issued it, so the
rule cannot be defeated by an import order or a hook registered later.

**Why the mode is DECIDED on ``request_started`` and not in a before-request
hook either, which is the same argument one tier up and was the defect this
module shipped with.**  The decision has to precede the request's first
statement, and a ``before_request`` hook cannot: Flask calls them in
registration order, ``setup_logging``'s ``_attach_request_id`` is registered
first, and it reads ``current_user`` -- so the user row was loaded, and a
transaction opened for it, before the boundary below had recorded anything.
That transaction was bound as a COMMAND (no mode was recorded yet), the
boundary then rolled it back, and the render's real transaction was the
SECOND one.  Measured on one authenticated ``GET /settings``, statement by
statement: the page's own work was **eight** statements either way, and the
machinery around it was **seven** and is now **two** -- one ``SET
TRANSACTION`` and one user load.  (Eleven rather than ten in total for the
first GET after a POST, on both sides, because that one inherits an open
transaction and pays the refusal probe; the saving is the same.)  Since
plan#2171 a signed-in request pays a third, the check of whether its sign-in
record is live (:func:`app.services.sign_in_service.is_live`): three, measured
2026-11-14 on the same ``GET /settings``.  With the rate limiter on, a
signed-in browser that sends its device cookie pays one more, the limits'
read of its record's id (:mod:`app.rate_limits`).
``request_started`` is sent by ``Flask.full_dispatch_request``
immediately before ``preprocess_request``, which is what runs the
before-request hooks, so a receiver there precedes every one of them by
Flask's own dispatch order rather than by a registration order this module
would have to police.

**Why there is ALSO a request boundary** (:func:`register_transaction_boundary`)
-- a TEST-FIDELITY property, and now genuinely one.  In production a request
gets its own app context and so its own session, so nothing is open when
``request_started`` fires and the boundary returns without issuing a statement;
the request's first statement then opens the one transaction the whole render
runs in.  *That sentence was FALSE while the decision lived in a
before-request hook -- ``_attach_request_id`` had already opened one, in
production as much as in the suite -- and it is true now because nothing runs
between the context push and this signal.*  Under the test client the outer
``app.app_context()`` is reused, so the test body and the request share one
session: measured over the whole suite, **2,302 of 2,978 GET requests arrived
on a transaction that was already open**, which would have left the guarantee
bound for under a quarter of them and CI blind to a render that writes.  The
boundary ends that transaction so the request opens its own.

**What it refuses, and why that is a precondition rather than a fence.**  A
query request whose transaction has ALREADY WRITTEN cannot be given its own
snapshot without discarding work that is not this module's to discard.  In
production the state is unreachable -- a request's session is its own -- so the
refusal names a caller that built uncommitted state and then issued a request
against it, which is a test that is unfaithful to the thing it is testing.
Measured at **36 of 2,978** GET requests before those fixtures were made to
commit, and at **0** carrying unflushed ORM state.

**And it detects a FLUSHED write, not pending ORM state**, which is the honest
limit of the test it can afford: PostgreSQL assigns a transaction id at the
first statement that writes, so a caller holding an unflushed ``session.add()``
is not refused -- the rollback below expunges it, silently.  The zero measured
above is what makes that acceptable today rather than a property anything
enforces; a caller that stages a row and then issues a request is already
describing a state no request can see.

**Who is ACTING is bound here too** (:func:`bind_request_actor`), because it is
a property of a TRANSACTION and this module is what decides a transaction's
kind.  The audit triggers read a transaction-scoped GUC
(:func:`app.audit_infrastructure.bind_audit_actor`), and a request no longer
runs in one transaction, so every transaction that can write has to be told.
**And only those**: ``READ ONLY`` refuses every write to an audited table, so
no trigger in a query's transaction can fire and there is no actor for it to
be told about.  Binding one there is not a cheap safety margin -- it is a
round trip per transaction buying a value nothing can read.

**The owner's write lock is bound here too, for the reason the actor is**
(plan step ``balance:X-bn``, rulings **R-CC106**, **R-CC114**, **R-CC115**):
it is a property of a writing TRANSACTION, and this module is what decides a
transaction's kind.  Every COMMAND transaction a signed-in request opens takes
the advisory lock of the user whose data the request acts on (a companion's
linked owner, :attr:`app.models.user.User.data_owner_id`) before it reads any
of that owner's data, so two of one owner's writes never overlap: the second
waits for the first to commit, and every statement it then runs sees the
first's rows.  What is read before it, and why none of it is a row a write
decides on, is listed once, in :func:`_lock_the_open_transaction`.
**The request's first transaction takes it after the form-token check and
the app-wide rate limit** (ruling
**R-CC122**), in a before-request hook registered after theirs
(:func:`_take_the_request_owner_s_lock`), so a request either one refuses
never waits for it or holds it; a route's own ``@limiter.limit`` runs inside
the view, after every hook, and so after the lock.
That is what makes "this lock must be the FIRST lock a transaction takes"
(:mod:`app.services.user_write_lock`) true of every request path at once,
where it used to be a property each write door had to hold for itself -- a
census over the whole suite found 27 endpoints that locked a row first and
then asked for this lock (2026-09-24).  **Per TRANSACTION, not per request**,
which matters in three places: a route that commits and goes on writing opens a
second command transaction, which takes the lock again; a
:func:`write_transaction` block inside a query request opens a command
transaction, which takes it (R-CC114: a page load's write takes the lock
like any save); and a save that commits and then draws its answer, or a
refusal that rolls back and then redraws, does that drawing in a new
transaction, so another request queued on the lock commits BETWEEN the two
-- a Delete there makes the answer a server error where it re-reads a one-off
row the Delete removed, a bare "not found" for a refused Mark Paid, or, on a
recurring row, its cell or purchase list drawn as if the row were live,
money correct (finding BAL-565, ruled to ship so, **R-BAL156**; plan step
``balance:X-dc`` makes each save one transaction).  A lock taken at the
REQUEST's start would not survive
either: it is transaction-scoped, and :func:`write_transaction` rolls the
query's snapshot back before its command, releasing anything taken earlier.
So plan step ``balance:X-i5``, which moves every save into a
:func:`write_transaction` block, keeps this lock with no change here -- and
owes one thing of its own: a save's READS that decide its write must sit
inside its block, because a read in the query's snapshot before the block is
a read taken before the lock.  Sign-up takes no lock: its user is not
committed, so no other transaction can touch it.  **Sign-in takes the
signing-in person's owner lock itself** (:func:`bind_sign_in_owner`): it
writes a committed user's rows before anyone is signed in, which no hook above
can see.  The code step takes it right after finding the account and before
reading its codes, and a known browser's password step before its check
(ruling **R-CC121**); a stranger's password step takes it only once its check
has committed and found the password right, on the way to signing in (ruling
plan#3110), so no stranger's try waits on it while bcrypt runs, and the wait
is no clue to which emails have accounts.  A CLI script, a deploy reconcile
and Alembic hold no request, so nothing here locks for them: the three deploy
reconciles lock every owner at their own start
(:func:`app.services.user_write_lock.lock_every_user_writes`), and a script
that writes an existing owner's data must take that owner's lock at its own
start in the same way, as the two-factor reset does (ruling plan#3823) --
three operator scripts do not yet (:mod:`app.services.user_write_lock` names
them).

**What this module does NOT do**, said here because the boundary is worth
knowing rather than discovering: a COMMAND's own re-render still reads at
``READ COMMITTED`` -- in the transaction its writes are in when it draws before
committing, and in a NEW command transaction when it draws after its commit or
rollback, which takes the owner's lock again at its start (the Per TRANSACTION
paragraph above).  That is
finding **N-358** and it has its own owner; closing it means the mutation routes
adopting :func:`write_transaction` so their render falls outside the command,
which moves the transaction boundary of every write door in the application and
is not this step's to take.

Non-request callers -- the CLI scripts, the deploy reconciles, Alembic -- never
reach the hook that records a mode, so :func:`_is_query_request` answers
``False`` for them and nothing about their transactions changes.  So does a
request CONTEXT that was never dispatched, which is not a pedantic distinction:
``pytest-flask`` pushes one around every test in this suite.
"""

from collections.abc import Iterator
from contextlib import contextmanager

from flask import Flask, Response, g, has_app_context, request, request_started
from sqlalchemy import event
from sqlalchemy.orm import Session

from app.audit_infrastructure import bind_audit_actor
from app.extensions import db, login_manager
from app.models.user import User
from app.services.user_write_lock import take_owner_write_lock
from app.utils.session_helpers import sign_in_is_live

# HTTP's safe methods, as this application serves them.  ``OPTIONS`` is left
# out, and the reason first given here was measurably WRONG: it said Flask
# answers OPTIONS from the URL map without dispatching a view "so it issues no
# statement for a mode to bind".  Flask runs ``preprocess_request`` -- every
# before-request hook -- BEFORE ``dispatch_request`` reaches its automatic
# OPTIONS branch, so an authenticated ``OPTIONS`` issued three statements
# (measured before plan step ``balance:X-bn``): the user load, and the actor
# bind on the writable transaction it opens.  Being a signed-in command, it
# now takes the owner's write lock on that transaction as well.  What is true
# is narrower and is why it stays out: OPTIONS runs no
# view, so nothing it can reach is a render whose figures need one snapshot.
# Whether a method that can never write should nonetheless be a QUERY is a
# behaviour question this step did not take.
_QUERY_METHODS = frozenset({"GET", "HEAD"})

# Where the DISPATCH records what it decided this request is.  The mode is
# decided ONCE, by :func:`_open_this_request_s_own_transaction` on
# ``request_started``, and read from here afterwards -- it
# is deliberately not re-derived from ``request.method`` at each transaction,
# for two reasons.  A derived value read in many places and stored in none is
# this arc's own root cause, and more concretely: a request CONTEXT is not a
# dispatched request.  ``pytest-flask`` pushes a synthetic
# ``app.test_request_context()`` around every test in this suite -- method
# ``GET``, no dispatch, no hooks -- so a rule that read the method would make
# every test BODY a query and refuse its fixtures.  A context nobody dispatched
# carries no mode, and no mode means command.
_MODE_KEY = "shekel_transaction_mode"
_QUERY = "query"
_COMMAND = "command"

# Where the request records WHO is acting, for the same reason it records the
# mode: the audit triggers read a TRANSACTION-scoped setting
# (:func:`app.audit_infrastructure.bind_audit_actor`), and a request no longer
# runs in one transaction.  Written by :func:`bind_request_actor` from a
# before-request hook rather than by the dispatch, because resolving the actor
# takes a statement and the dispatch runs before any is allowed.
#
# Private, and reached only through :func:`bind_request_actor`.  It was written
# by ``logging_config`` as a bare ``g.shekel_audit_actor = ...`` and read here
# through this constant, which is one key with two spellings in two modules --
# the shape this arc calls a root cause, on the value that decides whose name
# an audit row carries.
_ACTOR_KEY = "shekel_audit_actor"

# Where the request records WHOSE DATA it acts on, for the same reason and by
# the same door as the actor (:func:`bind_request_actor`): every command
# transaction the request opens takes this owner's write lock, and the
# listener that binds each one cannot run a statement to find out who that is.
# The ACTOR and the OWNER differ for a companion, who acts on the budget of
# the user they are linked to -- and every lock this replaced was keyed on the
# ROW's owner, so keying on the actor would take a second key beside it.
_OWNER_KEY = "shekel_write_owner"

# Where the request records its owner between the hook that RESOLVES it
# (:func:`bind_request_actor`, the first before-request hook) and the hook that
# LOCKS it (:func:`_take_the_request_owner_s_lock`, registered after the
# form-token and rate-limit hooks, ruling **R-CC122**).  A second key rather
# than ``_OWNER_KEY`` set early, because the listener reads ``_OWNER_KEY``:
# armed early, any transaction begun before the refusal checks would take the
# lock, and "no refusal check touches the database" would be the only thing
# keeping a refused request from holding it.
_PENDING_OWNER_KEY = "shekel_pending_write_owner"

# Issued between ``BEGIN`` and the statement that caused it.  One statement,
# both halves: the isolation level is what gives the pass one snapshot, and
# ``READ ONLY`` is what makes a write inside it the database's refusal.
_QUERY_TRANSACTION_SQL = (
    "SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY"
)

# PostgreSQL assigns a transaction id at a transaction's FIRST write and never
# before, so this answers "has this transaction written" exactly -- which is
# also "would ending it lose work".  The ``_if_assigned`` form is the one that
# does not assign one by asking.
_ASSIGNED_XID_SQL = "SELECT pg_current_xact_id_if_assigned()"


class UncommittedWriteAtRequestStart(RuntimeError):
    """A query request began on a transaction that had already written.

    Not a domain error and so not in :mod:`app.exceptions`: it reports a state
    the application cannot produce, because a request's session is its own.
    What it names in practice is a test that stages rows without committing
    them and then issues a request expecting to see them -- which no request can
    do, so the fixture is unfaithful before this module refuses it.

    Deliberately NOT handled in :mod:`app.error_handlers`.  A caught and
    rendered version of this would be the accommodation the step exists to
    remove; the honest disposition is the 500 an unreachable state deserves.
    """


def _is_query_request() -> bool:
    """Return whether the code running now belongs to a QUERY.

    It READS the mode the dispatch recorded rather than deciding one, so every
    transaction a request opens is bound the same way and the decision has a
    single site (:func:`_open_this_request_s_own_transaction`).  Absent means
    command:
    a CLI script, a deploy reconcile, Alembic, and a request context nobody
    dispatched all take the transaction they have always had.

    Returns:
        ``True`` when the current transaction should be a one-snapshot,
        read-only one.
    """
    return has_app_context() and getattr(g, _MODE_KEY, None) == _QUERY


@event.listens_for(Session, "after_begin")
def _bind_transaction_mode(session, transaction, connection) -> None:
    """Bind the mode of every transaction this process opens.

    Registered on the ``Session`` CLASS at import, which is what makes it
    apply to every session in the process exactly once -- registering it inside
    ``create_app`` would add a listener per application built, and the test
    suite builds several.

    ``after_begin`` fires between ``BEGIN`` and the statement that caused it, so
    the ``SET`` lands where PostgreSQL requires it (before the transaction's
    first statement) no matter which module issued that statement.

    Args:
        session: The :class:`~sqlalchemy.orm.Session` beginning a transaction.
        transaction: Its ``SessionTransaction``.
        connection: The connection the transaction was begun on.
    """
    # Pylint: ``unused-argument`` -- ``session`` / ``transaction`` are part of
    # SQLAlchemy's ``after_begin`` signature; only the connection is needed.
    # pylint: disable=unused-argument
    if transaction.nested:
        # A SAVEPOINT, not a transaction: PostgreSQL refuses ``SET TRANSACTION``
        # once a statement has run (``25001``), and the actor is already bound
        # on the enclosing transaction.  ``after_begin`` fires for a nested
        # transaction too, which the first draft of this listener did not
        # allow for.
        return
    if not has_app_context():
        return
    # ``SET TRANSACTION`` must be this transaction's FIRST statement --
    # PostgreSQL raises ``25001`` once any other has run, and binding the actor
    # IS a statement -- so nothing may be issued above this line.
    if _is_query_request():
        connection.exec_driver_sql(_QUERY_TRANSACTION_SQL)
        # And NOTHING after it either.  This transaction is ``READ ONLY``, so
        # PostgreSQL refuses every write to an audited table inside it, so no
        # audit trigger in it can fire, so there is no actor for it to read.
        # The bind below used to run here as well: a round trip per render
        # transaction, writing a value nothing could ever look at.
        return
    actor = g.get(_ACTOR_KEY)
    if actor is not None:
        bind_audit_actor(connection, actor)
    # The owner's write lock, on the listener's CONNECTION rather than through
    # the session: a session statement autoflushes, so a transaction begun with
    # rows already staged would write them before its first lock (plan step
    # ``balance:X-bn``; the module docstring has why every command
    # transaction takes it).
    owner = g.get(_OWNER_KEY)
    if owner is not None:
        take_owner_write_lock(connection, owner)


def _open_this_request_s_own_transaction(sender: Flask, **extra) -> None:
    """Decide this request's mode, then give a query its own transaction.

    **The ONE site that reads ``request.method``**, which is why it records the
    answer on ``g`` rather than leaving every later reader to re-derive it -- a
    derived value read in many places and stored in none is this arc's own root
    cause.

    **A ``request_started`` receiver rather than a ``before_request`` hook**,
    and the module docstring carries the measurement: before-request hooks run
    in registration order, ``_attach_request_id`` is registered first and reads
    ``current_user``, so the request's first statement -- and the transaction
    that carried it -- happened before this ran.  ``request_started`` is sent
    from ``Flask.full_dispatch_request`` immediately before
    ``preprocess_request``, so this precedes every before-request hook by
    Flask's dispatch order rather than by an ordering this module polices.

    **A module-level function rather than a closure**, because blinker holds
    its receivers WEAKLY: a function defined inside
    :func:`register_transaction_boundary` would be collected the moment that
    call returned and the signal would silently stop firing.

    **The ordering this buys is against BEFORE-REQUEST HOOKS and not against
    other receivers**, which is the residual and is stated rather than left to
    be discovered: blinker's ``send`` iterates a ``set`` and documents receiver
    order as undefined.  Today this application connects exactly one receiver
    to ``request_started`` -- pinned by
    ``test_the_boundary_is_the_only_request_started_receiver`` -- so there is no
    order to be undefined.  A second receiver that touched the database would
    reopen the defect this function exists to close, which is why the arm
    grades the count rather than trusting the census.

    **The query mode is recorded LAST, after the refusal**, and the order is
    what makes the refusal's sentence true: a request that is turned away never
    gets a mode, so the teardown ends nothing, and the uncommitted writes this
    declines to discard are still there when the caller looks.  Recording first
    and refusing second would have the boundary destroy them a moment later.

    Args:
        sender: The Flask application the signal was sent from.  Unused: the
            mode comes from the REQUEST, and the connection is per-app anyway
            (:func:`register_transaction_boundary`), so the sender is always
            the application this receiver was registered for.
        **extra: Blinker's forward-compatibility keywords.

    Raises:
        UncommittedWriteAtRequestStart: A query request arrived on a
            transaction that has already written.
    """
    # Pylint: ``unused-argument`` -- ``sender`` / ``**extra`` are blinker's
    # receiver signature; the mode is read off the request, not off the sender.
    # pylint: disable=unused-argument
    if request.method not in _QUERY_METHODS:
        setattr(g, _MODE_KEY, _COMMAND)
        return
    session = db.session()
    if session.in_transaction():
        # Asked through the CONNECTION rather than through
        # ``Session.execute``, which autoflushes: a probe that flushed the
        # session's pending state would perform the very write it is asking
        # about and then report it.  Measured at zero pending rows across
        # the suite, so it changes no answer today -- but a probe whose
        # correctness depends on that being true is the shape this step
        # exists to remove.  It also keeps the boundary off the ORM
        # entirely, which is the tier it operates below.
        connection = session.connection()
        if connection.exec_driver_sql(_ASSIGNED_XID_SQL).scalar() is not None:
            raise UncommittedWriteAtRequestStart(
                f"{request.method} {request.path} began on a transaction "
                f"that has already written, so it cannot be given the one "
                f"snapshot a render is entitled to -- and the writes are "
                f"not this boundary's to discard, so they are left where "
                f"they are. No request can reach this state (a request's "
                f"session is its own), so what it names is staged rows "
                f"that were never committed; commit them, which is what "
                f"the door under test does, and the request sees them."
            )
        session.rollback()
    setattr(g, _MODE_KEY, _QUERY)


def register_transaction_boundary(app: Flask) -> None:
    """Give every query request a transaction of its OWN, and end every dispatched request's.

    See the module docstring for why this exists and what it measured.  Two
    halves rather than one, and the closing half is not symmetry for its own
    sake: a snapshot that outlives its request is a snapshot the NEXT caller
    inherits, and read-only is inherited with it.  Measured the first time the
    suite ran without it -- 7,009 errors, every one of them
    ``ReadOnlySqlTransaction`` raised in a test body that had merely issued a
    request earlier and then tried to write.  Under the test client the app
    context is shared, so the session survives the request; ending the
    transaction where the request ends is what makes the boundary a boundary
    rather than a starting gun.  Since ruling **R-CC123** the closing half
    ends a COMMAND's transaction too, because the owner's write lock rides it
    (the teardown below carries the measurement).

    In production the opening half is a no-op on every request -- nothing is
    open when ``request_started`` fires, so it returns before issuing a
    statement -- and the closing half ends a transaction Flask-SQLAlchemy's own
    app-context teardown would otherwise end a moment later.  Neither is
    load-bearing there, and saying so is the point: what they buy is that the
    suite exercises in CI the guarantee production gets from its request
    lifecycle.

    **Connected per APP rather than to every sender**, so an application that
    never called this keeps the transactions it always had -- the same standing
    a CLI script has.  Blinker keeps a weak reference to the sender, so the
    connection dies with the app rather than outliving it.

    Args:
        app: The Flask application to register the boundary on.
    """
    request_started.connect(_open_this_request_s_own_transaction, app)
    # Registered HERE, and this function is called after ``csrf.init_app`` and
    # ``limiter.init_app`` (:func:`app._bind_extensions`), so Flask runs it
    # after their before-request hooks: a request they refuse never reaches
    # the lock (ruling **R-CC122**).
    app.before_request(_take_the_request_owner_s_lock)

    @app.teardown_request
    def _close_this_request_s_own_transaction(exc) -> None:
        """End the request's transaction and retire the request's mode.

        **Both kinds of request, for two different reasons** (ruling
        **R-CC123**).  A QUERY's transaction is ``READ ONLY``, so it holds no
        work and the rollback only releases its snapshot.  A COMMAND's route
        has committed its work or not by the time this runs, so the rollback
        discards only what the route left uncommitted -- which is exactly what
        Flask-SQLAlchemy's app-context teardown (``session.remove()``)
        discards a moment later in production.  So neither arm changes
        anything in production; the command arm exists only for test fidelity.
        Under the test client the app context is shared and outlives the
        request, and without that arm a command's open transaction outlived it
        too -- holding the owner's write lock (plan step ``balance:X-bn``): a
        correct-password sign-in commits nothing, so the next save a test made
        for that owner on a second session waited on a lock nothing would
        release (486 tests in the full suite, measured 2026-09-25: 481 timed
        out on the lock and 5 two-thread race tests hung behind it).  Two more
        things follow under the test client: a save route that forgets to
        commit now fails a test that checks the saved row, as production would
        lose the save; and a save request that saves nothing drops the test
        body's own uncommitted setup with it (no test relied on that when this
        was written).  *Until R-CC123 this said a command's transaction
        belonged to its route and that ending it here "would decide a
        mutation's outcome from a lifecycle hook"; by teardown the route has
        already decided.*

        **A request refused on ``request_started`` is left alone**: it never
        got a mode, so nothing is ended, and the uncommitted writes the refusal
        declined to discard are still there when the caller looks.  **The
        command arm stays until planned step ``balance:X-di``** (ruling
        **R-BAL200**).  Since plan step ``balance:X-cr`` each test-client
        request runs on a ``flask.g`` of its own but still inside the test's
        app context, and so on the test's database session, where a command's
        open transaction would otherwise outlive its request.  Once each test
        request runs in an app context and a session of its own, that
        context's teardown ends the transaction, as in production, and this
        arm is deleted.

        **The mode is retired either way, and the ACTOR and the OWNER with
        it.**  A test-client request's ``g`` is discarded with the request
        since ``balance:X-cr``, so for those this retires nothing that would
        survive; it is load-bearing for a request context a test pushes
        itself (``app.test_request_context()``), whose teardown runs on the
        test's own ``g``, where either value left behind would govern the test
        body's later transactions.  For the actor that means a row the test
        body writes afterwards would be attributed to that user, which is
        neither what production does -- ``g`` dies with the request there --
        nor what the ``SET LOCAL`` this replaced did, since a transaction-scoped
        GUC died with the request's transaction.  For the owner it would mean
        every later transaction of the test body taking that owner's write
        lock, which a test racing two connections would then wait on from its
        own main one.

        Args:
            exc: The unhandled exception Flask is tearing down for, if any.
                Unused: the disposition is the same either way.
        """
        # Pylint: ``unused-argument`` -- ``exc`` is Flask's ``teardown_request``
        # signature; the transaction is ended identically whether the request
        # succeeded or raised, as production's app-context teardown ends it.
        # pylint: disable=unused-argument
        if not has_app_context():
            return
        try:
            if getattr(g, _MODE_KEY, None) is not None:
                db.session.rollback()
        finally:
            # Retired even when the rollback raises (a dropped connection):
            # under a request context a test pushes itself ``g`` is the
            # test's own, and a mode, actor or owner left behind would govern
            # every later transaction of the test body.
            g.pop(_MODE_KEY, None)
            g.pop(_ACTOR_KEY, None)
            g.pop(_PENDING_OWNER_KEY, None)
            g.pop(_OWNER_KEY, None)


def bind_request_actor(user_id: int, owner_id: "int | None") -> None:
    """Record who is acting and whose data, and bind the transaction ALREADY open.

    **The one door to the audit actor**, so the key that decides whose name an
    audit row carries has one writer and one spelling.  It was two: this module
    read ``_ACTOR_KEY`` and ``setup_logging`` wrote the same string as a literal.

    **And where a request's OWNER becomes known, which is not where its lock is
    taken** (plan step ``balance:X-bn``, ruling **R-CC122**).  The owner is the
    user whose data the actor acts on -- the actor, or a companion's linked
    owner -- which the caller reads off the signed-in row by the one rule that
    says so (:attr:`app.models.user.User.data_owner_id`), a property of columns
    already loaded, so resolving it issues no statement.  It is recorded as
    PENDING and locked later, by :func:`_take_the_request_owner_s_lock`, the
    hook :func:`register_transaction_boundary` registers after the form-token
    and rate-limit hooks: this call runs in the FIRST before-request hook, and
    a lock taken here was held -- and waited for -- by every request those
    checks then refused.

    Two halves, and the second is the one a caller cannot do for itself.
    Recording the actor on ``g`` is what lets :func:`_bind_transaction_mode`
    bind every transaction the request opens FROM here on.  The transaction
    already open when this is called began before the actor was known -- and it
    is the one the request's writes will land in -- so it is bound here
    directly.  That is not a redundancy: resolving ``current_user`` is what
    opened it, so no hook can know the actor before that transaction exists.

    **A QUERY binds nothing and this returns without a statement.**  Its
    transactions are ``READ ONLY``, so no write to an audited table can happen
    in one and no trigger can read the GUC.  A query request that must write
    says so with :func:`write_transaction`, whose transaction is a command's
    and is bound by the listener like any other.

    **And a command binds nothing when no transaction is open yet**, which is
    the difference between "tell the one already running" and "open one to
    have something to tell".  ``Session.connection()`` BEGINS a transaction
    when none is open, so an unconditional bind would start one from a
    logging hook and then bind it twice -- once through the listener, which
    ``g`` now satisfies, and once here.  Reachable whenever ``current_user``
    resolves without a statement, i.e. wherever the row is already loaded and
    unexpired.  Binding only an open transaction is also the honest reading of
    this function's own first line.

    **``g`` is recorded LAST for the same reason**: the explicit bind covers
    the transaction that began before the actor was known, and every
    transaction after this one is the listener's, so setting ``g`` first would
    make the listener and this line bind the same transaction.

    Args:
        user_id: The acting ``auth.users.id``.
        owner_id: The id of the user whose data the actor acts on
            (:attr:`app.models.user.User.data_owner_id`); ``None`` for a
            companion with no linked owner, who has no budget to write and
            takes no lock.
    """
    session = db.session()
    if not _is_query_request() and session.in_transaction():
        bind_audit_actor(session.connection(), user_id)
    setattr(g, _ACTOR_KEY, user_id)
    setattr(g, _PENDING_OWNER_KEY, owner_id)


def _take_the_request_owner_s_lock() -> Response | None:
    """Take the signed-in request's owner lock, then ask for its sign-in again under it.

    A before-request hook, registered by :func:`register_transaction_boundary`,
    which :func:`app._bind_extensions` calls AFTER ``csrf.init_app`` and
    ``limiter.init_app`` -- so Flask runs this after the form-token check and
    the app-wide rate limit, and a request that either one refuses never waits
    for the lock or holds it (ruling **R-CC122**).  A route's OWN limit
    (``@limiter.limit``) is checked inside the view's wrapper, after every
    before-request hook, so a request that one refuses still waits here.
    Nothing to do for an anonymous request or ``/health``, where
    :func:`bind_request_actor` was never called and no owner is pending.

    **Once the lock is held, the request's sign-in is asked for again** (ruling
    plan#4070).  ``load_user`` asked before the lock, as it must, since the
    lock's key is the signed-in user's owner; but the lock can be waited on,
    and a save queued behind a sign-out of its own sign-in (a copy of the
    browser's cookie pressing Save as the browser signs out, or "Log out all
    other sessions" pressed meanwhile) found its record live and would save
    once after it.  Every sign-out a request makes takes this lock, so under
    it the record is either still there, its sign-out not begun, or gone.  A
    request whose sign-in is gone is answered as a signed-out request is
    (:meth:`~flask_login.LoginManager.unauthorized`): neither the login gate
    nor the view runs, and its transaction, holding the lock, ends at the
    request's teardown with nothing written.  The user's row, read before the
    lock too, is read again through its expiry instead
    (:func:`_lock_the_open_transaction`), and the gate turns away a user
    deactivated during the wait.

    **Asked only when the lock was taken on the transaction open now**, which
    is every signed-in command whose user has an owner: asking for the record
    began that transaction.  A query takes no lock and asks once, as its
    transaction is one snapshot and a second ask could only repeat the first.
    A render's write block (the grid's and the dashboard's rolling top-up,
    ``/mfa/setup``'s pending key) takes the lock later, in a transaction of
    its own, and is not asked again: the top-up appends what the owner's next
    render would append, and a pending key does nothing until a confirmation,
    which is a command and is asked.  ``scripts/rotate_sessions.py`` takes no
    lock (:mod:`app.services.user_write_lock` names it), so its delete can
    land after the second ask, as it could after the first.

    Returns:
        ``None`` so Flask carries on to the next hook, or the sign-in page's
        redirect for a request whose sign-in ended while it waited.
    """
    if not has_app_context() or not hasattr(g, _PENDING_OWNER_KEY):
        return None
    owner_id = g.pop(_PENDING_OWNER_KEY)
    user_id = g.get(_ACTOR_KEY)
    if _lock_the_open_transaction(user_id, owner_id) and not sign_in_is_live(user_id):
        return login_manager.unauthorized()
    return None


def bind_sign_in_owner(user_id: int, owner_id: "int | None") -> None:
    """Take the lock of the owner whose data a SIGN-IN is about to write.

    Sign-in is the one writer of a committed user's rows that no signed-in
    request covers: the code step (``backup_codes``, ``last_totp_timestep``,
    and a backup code's sign-out everywhere), a known browser's count on its
    record, and the completion's records run before anyone is signed in, so
    :func:`bind_request_actor` never saw an owner.  Unlocked, a backup-code
    sign-in racing "Regenerate backup codes" wrote the old codes back over the
    new ones, one backup or authenticator code signed in two devices, and a
    known browser's two wrong passwords counted once.  So the code step takes
    it right after finding the account, before it reads the codes, and a known
    browser's password step before its check (ruling **R-CC121**, "Lock, then
    check").  A stranger's password step takes it only once its check has
    committed and found the password right, and re-reads the account under it
    (ruling plan#3110): a stranger's count is the record of the email typed
    (``auth.guess_records``), whose own row lock serialises it, and a wait on
    this lock through the check took twice as long for two tries at a real
    email as at one no account has.

    When the transaction that found the account is still open, the lock is
    taken on it now; when a commit has ended it, the lock is the next
    transaction's first act (:func:`_lock_the_open_transaction`).

    Records no ACTOR: the audit rows a sign-in writes carry none today, and who
    they are attributed to is not this ruling's to change.

    Args:
        user_id: The ``auth.users.id`` being signed in, found by email or by
            the pending-MFA session key.
        owner_id: That user's :attr:`~app.models.user.User.data_owner_id`;
            ``None`` for a companion with no linked owner, who takes no lock.
    """
    _lock_the_open_transaction(user_id, owner_id)


def _lock_the_open_transaction(user_id: "int | None", owner_id: "int | None") -> bool:
    """Take *owner_id*'s lock on the command transaction open now, and arm every later one.

    The half :func:`_bind_transaction_mode` cannot do: the transaction open now
    began before the owner was known -- finding the user opened it, or at
    ``/login`` the question before it of which users the browser is known to
    -- so the listener never saw an owner for it.  It is locked here directly,
    and ``g`` is armed LAST, so the listener takes the lock on every
    transaction that begins after this one and never on this one twice.

    The only rows that transaction has read before the lock are *user_id*'s
    own, loaded to sign the request in or to find the account, and the role
    row joined to it (``User.role``, ``lazy="joined"``: the same statement);
    wherever the session names a user, whether its sign-in record is live,
    asked through ``load_user`` as one EXISTS
    (:func:`app.services.sign_in_service.is_live`), so no record is loaded
    (ruling plan#4068); at ``/login``, also which users the browser's cookies
    name known records of, read as user ids alone
    (:func:`app.services.known_device_service.known_to`), so no record is
    loaded; and on a signed-in request, for the rate limits, which record the
    browser's cookie names, read as its id alone
    (:func:`app.services.known_device_service.known_browser_id`), so none is
    loaded there either.  A sign-in whose check committed holds no open transaction
    here, so the lock is the next one's first act and nothing precedes it.
    The password, MFA and settings doors write the user's row, so it is
    EXPIRED once the lock is held, and its next use reloads it under the lock.
    Only that row, and only if it is loaded (looked up in the identity map, so
    no statement is issued to find it): under the test client this session is
    also the test body's, whose other loaded state is not this request's to
    discard.  The role row is a reference row no request writes.  And a
    signed-in request asks for its sign-in's record again once the lock is
    held (:func:`_take_the_request_owner_s_lock`, ruling plan#4070).  So
    nothing a write decides on was read before the lock.

    A query binds nothing, for the reason :func:`bind_request_actor` gives.

    Args:
        user_id: The user whose row was read before the lock, or ``None``.
        owner_id: The owner to lock; ``None`` takes no lock and arms nothing.

    Returns:
        Whether the lock was taken on the transaction open now: ``False`` for
        no owner, for a query and for no open transaction, whose lock is the
        next transaction's first act.
    """
    if owner_id is None:
        return False
    session = db.session()
    took_it_now = not _is_query_request() and session.in_transaction()
    if took_it_now:
        take_owner_write_lock(session.connection(), owner_id)
        if user_id is not None:
            loaded = session.identity_map.get(session.identity_key(User, user_id))
            if loaded is not None:
                session.expire(loaded)
    setattr(g, _OWNER_KEY, owner_id)
    return took_it_now


@contextmanager
def write_transaction() -> Iterator[None]:
    """Run this block as a COMMAND inside a query request.

    The one way a render says it must write, and the reason it is a block
    rather than a flag is that the writes must be COMMITTED before the render
    resumes: the pass that follows then takes its snapshot of a database that
    already holds them.  That ordering is what ``/grid`` and ``/dashboard``
    already depended on informally -- their rolling top-up commits and the read
    pass is built after -- and what this makes structural.

    The block owns its transaction end to end: it ends the query's snapshot
    first (which has written nothing, so nothing is lost), commits on the way
    out, and rolls back if the body raises.  A caller that wants a failure to
    leave the render running must catch it inside the block; one that lets it
    escape gets the rollback and the exception.

    **Not available to a command request**, which is already writable end to
    end.  Making a command's own render fall outside its write is finding
    **N-358**'s work and wants its own trace; opening a nested transaction
    here would commit the route's staged writes early.

    Yields:
        ``None`` -- the block writes through ``db.session`` as any command does.

    Raises:
        RuntimeError: Used outside a query request, or nested inside another
            :func:`write_transaction` block.
    """
    mode = getattr(g, _MODE_KEY, None) if has_app_context() else None
    if mode is None:
        raise RuntimeError(
            "write_transaction() is for a QUERY request that must write, and "
            "no dispatched request is running here. Outside one there is "
            "nothing to leave and return to: a CLI script, a deploy reconcile "
            "and a migration all hold the writable transaction they always "
            "had, and had no snapshot taken from them."
        )
    if mode == _COMMAND:
        raise RuntimeError(
            "write_transaction() found a COMMAND transaction already running. "
            "Either this is a mutation route, which is writable end to end "
            "and needs no block (making a command's own re-render fall "
            "outside its write is finding N-358's work), or a block is "
            "already open on this request and nesting one would commit the "
            "outer block's staged writes at the inner block's end."
        )
    session = db.session
    # End the snapshot before the mode flips, so the transaction this opens is
    # bound as a command.  It has written nothing -- READ ONLY guaranteed that
    # -- so the rollback discards no work; what it does discard is the identity
    # map's loaded state, which reloads inside the command.
    session.rollback()
    setattr(g, _MODE_KEY, _COMMAND)
    committed = False
    try:
        yield
        session.commit()
        committed = True
    finally:
        # The mode is restored BEFORE the rollback, not after: a rollback can
        # itself raise (a dropped connection, a failover), and a mode left on
        # COMMAND would leave the rest of the render writable and
        # un-snapshotted.
        setattr(g, _MODE_KEY, _QUERY)
        if not committed:
            session.rollback()
