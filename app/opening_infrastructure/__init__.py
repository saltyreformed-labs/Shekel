"""Shared definitions for the account-books boundary's database-tier constraint.

**An account's opening equity is the balance at the CLOSE of its
``opened_on``** (plan step X-f3c-2b, ruling **balance:R-HG**) -- exactly the
rule ``account_anchor_history.observed_on`` states for a balance assertion
(ruling R-DH (a)).  A cash movement dated ON OR BEFORE that day is therefore
already inside the figure, and recording it counts the money twice: the fold
seeds at the opening and ``cash_ledger.dated_deltas`` emits every source at its
own day, so the running total carries it a second time until the next assertion
resets it -- and on a MODELLED account (ruling **R-FO**) the correction that
resets it books to ``unrealized_change``, turning a transfer into market
performance that never unwinds (finding **N-378**).

**This module is what makes that state UNSTORABLE BY ANY SINGLE TRANSACTION,
rather than merely refused.**  Two limits are worth naming rather than leaving
a reader to assume more:

* Under READ COMMITTED two CONCURRENT transactions -- one recording a movement,
  one restating the books past it -- each see a snapshot without the other's
  uncommitted row, so both predicates pass.  **Neither trigger takes a lock,
  and what closes the window instead is that both DOORS run under the
  owner's** write lock (:mod:`app.services.user_write_lock`), which since plan
  step ``balance:X-bn`` every command transaction a signed-in request opens
  takes before reading any of the owner's data -- the movement side and
  :func:`app.services.opening_service.stage_account_opening` alike, where each
  used to take it for itself.  An origination at registration runs under
  none, and needs none: its account is uncommitted, so nothing can race it.
  The loser blocks until the winner's transaction ENDS, and a deferred
  constraint trigger runs at COMMIT -- after that block, on a fresh READ
  COMMITTED snapshot -- so it sees the winner's committed row and refuses.
  *Measured 2026-08-31 on a production clone, before plan step
  ``balance:X-bn``: the settle path emitted ``pg_advisory_xact_lock`` at
  statement 11 of the 13 an ordinary settle ran, inside
  ``account_posting_service.self_heal_anchor_corrections``, and that self-heal
  returned BEFORE its lock when the source emitted no posting delta, so a
  movement whose legs all net to zero raced a restatement unserialised -- the
  residue this paragraph named.*  **Since that step the lock is taken where
  every signed-in request's writing transaction BEGINS**
  (:mod:`app.db_transaction`), whatever the transaction goes on to post, so
  the zero-net movement holds it too and the residue is closed on every
  request path by construction.
* ``session_replication_role = replica`` disables constraint triggers outright,
  which is what ``pg_restore --disable-triggers`` sets.  The prod-to-dev clone
  is a documented workflow here, so a restore can land rows this module would
  have refused; the CONSTRAINT is not re-validated afterwards, only future
  writes are.

The invariant spans FIVE tables and two directions, so PostgreSQL cannot
state it as a row-level CHECK:

* a MOVEMENT may not move back past its account's opening
  (``budget.transactions`` and ``budget.transaction_entries``, both of which
  carry ``account_id`` and ``settled_on``);
* a MATCHED BANK LINE may not be dated on or before its account's opening
  (plan step **balance:X-f3c-2b-2b**).  **This is not implied by the movement
  rule and the difference is a money defect**: a match settles every member on
  the LATEST of its bank days, so a group holding one pre-opening line and one
  later line settles after the books open and passes the movement arm
  untouched -- while the earlier line's money is inside the opening equity and
  inside a settled row at once.  Measured on a restored production clone
  2026-08-31: lines of 2026-03-26 (``-$15.96``) and 2026-08-17 (``-$64.04``)
  matched to one ``$80.00`` envelope against books opening 2026-03-26 at
  ``$689.16``, accepted.

  **It takes TWO attachments, and the second is what makes the claim above
  true.**  The FACT lives on ``budget.statement_match_members`` and the DAY on
  ``budget.bank_statement_lines``, so either can move without the other -- and
  a trigger on the members table alone left
  ``UPDATE budget.bank_statement_lines SET posted_on = '2020-01-01'`` on an
  already-matched line committing cleanly into the forbidden state.  Found by
  adversarial design review 2026-08-31, against the very paragraph above that
  claims no client can store it.  The movement arm has no such hole because
  ``settled_on`` sits on the table its trigger is attached to;
* an account's GOVERNING opening may not sit on or after a movement OR a
  matched line that already exists (``budget.account_openings``) -- stated
  over the rows that survive an event rather than over the row written, so
  restating forward, raw-updating the governing row and DELETING it (which
  promotes an older restatement) are one rule rather than three arms.  **Its
  two predicates are separate functions**, because the two states have
  different repairs -- re-date the movement, or undo the match -- and a
  refusal that names the wrong one sends the owner to a door that will not
  help.

**A FOURTH rule is enforced at the DOOR and deliberately NOT here**, and the
reason is worth stating because this module's whole claim is structural: a
restatement may not move the books past a day the owner has ASSERTED a balance
for (:func:`app.services.cash_ledger.reject_books_open_after_an_assertion`,
plan step X-f3c-2b-2a).  It was written as a fourth trigger arm first, and the
suite refused it -- 12 failures and 22 errors, every one raising out of
``assert_account_books_hold_its_movements``.  **The state it forbids is
ROUTINE, and not only in fixtures**: nothing bounds an assertion against its
account's opening, because ``anchor_service.resolve_observation_day`` bounds
``observed_on`` at ``earliest_recordable_day`` and at today and never at
``opened_on`` -- so an owner may back-date an assertion below their own books
through ``accounts.true_up``.  A constraint refusing what existing rows already
hold does not enforce an invariant; it breaks every write on the accounts that
hold it.  Making the STATE illegal needs the assertion door bounded too and the
existing rows legalised, which is a step and not a clause (finding **N-400**).

All of them are cross-table facts, so they live in **deferred constraint
triggers** validating at COMMIT.  Deferral is not incidental: restating an account's
opening forward is legitimate when the movements in the way are being re-dated
or removed in the SAME transaction, which is exactly what the account-10 repair
(**N-379**) does.  An immediate trigger would refuse that by statement order.

**The service layer states the same rule in WORDS, and that pairing is the
established shape here** -- ``ck_transaction_entries_positive_amount`` beside
``entry_service``'s refusal of a purchase worth nothing (ruling
``bank_import:R-II``).
:func:`app.services.cash_ledger.reject_movement_before_books_open` is the
sentence a date box gets; this is why a bulk ``UPDATE``, a raw statement, a
psql session or a writer nobody enumerated cannot produce the state anyway.
The bulk writer is not hypothetical: ``reconcile_service.record_settled_days``
stamps a statement's day onto ticked purchases with ``query.update()`` and has
no ORM instance for the service rule to fire on.

FIVE call sites in four files use these builders, exactly as
:mod:`app.audit_infrastructure` and :mod:`app.posting_infrastructure` are used
-- but they do **not** all produce identical infrastructure, and that
difference is deliberate:

1. Each Alembic revision that installs an ARM, naming the arms it declared and
   censused: ``d3b6f1c8a274`` passes ``arms=(MOVEMENT_ARM,)`` AFTER its
   restatement, and ``d1f6a83c9e47`` passes both arms after its own census.  A
   revision names a LITERAL tuple, never :data:`ALL_ARMS` -- see that constant
   for what letting the module choose was measured to cost.
2. A revision that installs NO arm and only re-states function BODIES:
   ``c9f4b1e78d02`` calls :func:`apply_opening_functions` with
   ``arms=(MOVEMENT_ARM,)``, which is the arm set of its own point in history.
   It is the revision :func:`apply_opening_functions` was split out for, and an
   earlier draft of this list omitted it entirely by describing item 1 as
   "each migration that installs an arm".
3. ``scripts/init_database.py``, whose fresh-database path builds the schema
   with ``db.create_all()`` + an Alembic ``stamp`` and so never runs the
   migration chain.  It materialises HEAD, so it passes :data:`ALL_ARMS`.
4. ``scripts/build_test_template.py``, which runs the chain and then RE-applies
   :data:`ALL_ARMS` idempotently so the latest in-code definition wins over
   migration-frozen state.

**The two SCRIPTS build a database at head; a REVISION builds the database its
own point in history describes.**  Conflating those is what let the
matched-line arm go live five revisions before the census that validates it --
see :data:`ALL_ARMS` for the measurement.

**So a from-scratch database and a migrated one agree only while the NEWEST
revision's arm tuple equals** :data:`ALL_ARMS`, because the scripts install the
latter and the chain installs the former.  Nothing about the alembic stamp can
see a disagreement -- it records which revision is head, never which arms ran --
so it is asserted instead, by
``tests/test_models/test_books_boundary_arms.py``.

**A deferred constraint trigger makes DDL on its table illegal until its events
drain, and that is worth knowing before writing the next migration.**
PostgreSQL refuses ``CREATE INDEX`` (and other DDL) on a table carrying pending
trigger events -- "cannot CREATE INDEX ... because it has pending trigger
events" -- so a transaction that WRITES a movement row and then alters that
table fails.  The remedy is ``SET CONSTRAINTS ALL IMMEDIATE`` before the DDL,
which runs the checks in place rather than deferring them; it is what
``tests/test_scripts/test_integrity_check/test_the_consistency_checks.py`` and
``tests/test_models/test_clearing_link_schema.py`` do before re-creating an
index and dropping a NOT NULL.

**THE COMMON CASE OF THIS IS GONE, and how it was closed is the point.**  The
movement trigger carries ``WHEN (NEW.settled_on IS NOT NULL)``, which is the
guard that used to be its function's first line.  An early ``RETURN`` in the
body cannot stop an event being QUEUED -- the queue happens at statement time
and the body runs at COMMIT -- so every Projected row this app writes used to
reserve ``budget.transactions`` against DDL for the rest of its transaction.
It cost two CI failures before it was found: ``recurrence:R17``'s
``test_dc06_detects_two_rows_answering_one_occurrence`` on the merge, and two
audit-trigger benchmarks in ``tests/test_performance`` -- a directory
``pytest.ini`` excludes from the ordinary run, so no local suite can see it.
**What remains is a row that really does carry a settle day**, which is the
only kind the rule was ever going to check.

**The case a reader will NOT think of is a MIGRATION**, and it is the norm here
rather than the exception: this project puts one-time backfills in the Alembic
revision that changes the schema, so an revision that alters
``budget.transactions`` or ``budget.transaction_entries`` AND writes movement
rows in the same transaction is the ordinary shape -- and it fails with
``ObjectInUse`` naming the table, not with the message
:func:`app.services.cash_ledger.reject_movement_before_books_open` gives.
Order the DDL first, or drain with ``SET CONSTRAINTS ALL IMMEDIATE``.  The same property has
been true of ``ck_account_postings_balanced`` since it shipped; this module
states it because a second deferred trigger makes it twice as likely to be met.

**Caller contract: all FIVE tables must already exist**, and that now includes
``budget.bank_statement_lines`` -- ``apply_opening_functions`` creates a body
naming it.

**The contract is MOSTLY the CALLER's to keep, and the claim that the server
kept it was false.**  This paragraph used to say ``check_function_bodies``
validates the table references at ``CREATE FUNCTION`` time, so applying early
"fails loudly".  That is true only for ``LANGUAGE sql`` bodies.  PL/pgSQL's
validator parses its body but does not resolve the table and function
references inside its statements; those resolve on first EXECUTION.  Measured
2026-08-31 with ``check_function_bodies`` ON, the two spellings differ:
``CREATE FUNCTION f() RETURNS void AS $$ BEGIN PERFORM 1 FROM
budget.no_such_table; END $$ LANGUAGE plpgsql`` **succeeds**, while the same
reference in a ``LANGUAGE sql`` body **errors** with ``relation ... does not
exist``.

So the five tables split into two groups, and saying which is the point:

* ``budget.account_openings`` IS checked.  :data:`_CREATE_OPENED_ON_SQL` is
  ``LANGUAGE sql STABLE`` and selects from it, so applying this module without
  that table fails loudly at the second statement -- the right signal.
* ``budget.transactions``, ``budget.transaction_entries``,
  ``budget.statement_match_members`` and ``budget.bank_statement_lines`` are
  NOT.  Every body naming them is ``plpgsql``, so applying early succeeds
  silently and surfaces later as a missing relation raised out of a deferred
  trigger at COMMIT -- the failure mode furthest from its cause, and the one
  this module argues twice that it exists to avoid.

*The correction above needed correcting: its first draft said every body here
is plpgsql "except ``budget.books_hold``, which names no table", which
overlooked that ``budget.account_books_opened_on`` is also ``LANGUAGE sql`` AND
names a table.  A correction that is itself wrong is the defect it was
replacing.*

The statement ORDER inside :func:`apply_opening_functions` (a predicate before
the dispatcher that ``PERFORM``s it) is held by this sentence alone.  Since
plpgsql does not resolve a ``PERFORM`` target at ``CREATE`` time either, both
orders in fact produce a working database today; the order is kept because a
reader should not have to know that, and because it is the order that would
survive either function being rewritten in ``LANGUAGE sql``.
:mod:`app.posting_infrastructure` and :mod:`app.append_only_infrastructure`
carry the same belief in their own docstrings and are out of this step's scope
(``CLAUDE.md`` rule 6) -- reported, not fixed.
"""


from __future__ import annotations

from typing import Callable

from ._base import (
    ALL_ARMS,
    MATCHED_LINE_ARM,
    MOVEMENT_ARM,
    GOVERNING_ORDER_SQL,
    _BOOKS_HOLD_FUNCTION,
    _CREATE_BOOKS_HOLD_SQL,
    _CREATE_OPENED_ON_SQL,
    _OPENED_ON_FUNCTION,
    _OPENINGS_TABLE,
    _drop_trigger_sql,
)
from ._matched_line import (
    MATCHED_LINE_DAYS_SQL,
    _BANK_LINES_TABLE,
    _CREATE_LINE_DAY_FUNC_SQL,
    _CREATE_LINE_DAY_TRIGGER_SQL,
    _CREATE_MATCH_MEMBER_FUNC_SQL,
    _CREATE_MATCH_MEMBER_TRIGGER_SQL,
    _CREATE_MATCHED_LINE_PREDICATE_SQL,
    _LINE_DAY_FUNCTION,
    _LINE_DAY_TRIGGER,
    _MATCH_MEMBER_FUNCTION,
    _MATCH_MEMBER_TRIGGER,
    _MATCH_MEMBERS_TABLE,
    _MATCHED_LINE_PREDICATE,
)
from ._movement import (
    SETTLED_MOVEMENTS_SQL,
    _CREATE_MOVEMENT_FUNC_SQL,
    _MOVEMENT_FUNCTION,
    _MOVEMENT_TABLES,
    _MOVEMENT_TRIGGER,
    _create_movement_trigger_sql,
)
from ._openings import (
    _CREATE_MATCHED_LINES_PREDICATE_SQL,
    _CREATE_OPENING_PREDICATE_SQL,
    _CREATE_OPENING_TRIGGER_SQL,
    _MATCHED_LINES_PREDICATE_FUNCTION,
    _OPENING_FUNCTION,
    _OPENING_PREDICATE_FUNCTION,
    _OPENING_TRIGGER,
    _create_opening_func_sql,
)

__all__ = [
    "ALL_ARMS",
    "GOVERNING_ORDER_SQL",
    "MATCHED_LINE_ARM",
    "MATCHED_LINE_DAYS_SQL",
    "MOVEMENT_ARM",
    "SETTLED_MOVEMENTS_SQL",
    "apply_opening_functions",
    "apply_opening_infrastructure",
    "remove_opening_infrastructure",
]


def apply_opening_infrastructure(
    executor: Callable[[str], object], *, arms: "tuple[str, ...]",
) -> None:
    """Make the account-books boundary equal exactly *arms*.

    **It states the boundary TOTALLY rather than additively**, and that is the
    property the rest of this design rests on: an arm the caller does not name
    is DROPPED -- its triggers, then its functions -- not merely left alone.
    So one call materialises a database's whole boundary, and a downgrade that
    removes an arm is the same call naming one fewer.  ``d1f6a83c9e47``'s
    downgrade is exactly that, and it is why that revision keeps no frozen copy
    of the previous function bodies: there is nothing to keep in step by hand,
    because the bodies are regenerated from this module for the arms that
    remain.

    Executes, in order: the two BASE functions and the declared arms' functions
    (:func:`apply_opening_functions`), then each declared arm's constraint
    triggers, then the openings trigger, then the drops for every arm NOT
    declared.  Every statement is idempotent (``CREATE OR REPLACE FUNCTION``
    swaps the body; ``DROP TRIGGER IF EXISTS`` + ``CREATE`` re-pins each
    trigger; every drop is ``IF EXISTS``), so a second run with the same arms
    is indistinguishable from the first.

    **Triggers are dropped before the functions they name.**  Every arm's
    ``DROP TRIGGER`` runs above, in the same pass, before its
    ``DROP FUNCTION`` -- and the openings dispatcher is REGENERATED without the
    withdrawn arm's predicate (by :func:`apply_opening_functions`) before that
    predicate is dropped.  Dropping a routine a stored function still names is
    legal in PostgreSQL and fails at CALL time, which would leave every
    restatement raising ``function does not exist`` on a database that reported
    a clean apply.

    **The caller must have LEGALISED the data for the arms it names.**  Applying
    an arm to a database whose existing rows violate it does not fail here -- a
    constraint trigger validates writes, not existing rows -- but the next
    write touching such a row aborts at COMMIT.  Each revision therefore
    censuses (and, where there is a safe automatic repair, restates) BEFORE
    naming the arm here.

    Args:
        executor: Single-argument callable that accepts a SQL string and runs
            it.  Pass ``op.execute`` from inside an Alembic migration; pass
            ``lambda s: session.execute(text(s))`` from inside a SQLAlchemy
            session.  Errors propagate -- the caller owns the outer
            transaction.
        arms: The arms this database's boundary consists of.  A MIGRATION
            passes a literal tuple naming what it declared and censused; the
            two scripts that materialise a HEAD database pass
            :data:`ALL_ARMS`.  See :data:`ALL_ARMS` for what passing it from a
            revision was measured to cost.

    Raises:
        ValueError: When *arms* is empty, or names an arm this module does not
            build.  An empty set would generate an openings dispatcher with an
            empty ``IF`` block, which is not valid PL/pgSQL; a database with no
            boundary at all is :func:`remove_opening_infrastructure`.
    """
    apply_opening_functions(executor, arms=arms)
    for table in _MOVEMENT_TABLES:
        executor(_drop_trigger_sql(_MOVEMENT_TRIGGER, table))
        if MOVEMENT_ARM in arms:
            executor(_create_movement_trigger_sql(table))
    executor(_drop_trigger_sql(_MATCH_MEMBER_TRIGGER, _MATCH_MEMBERS_TABLE))
    executor(_drop_trigger_sql(_LINE_DAY_TRIGGER, _BANK_LINES_TABLE))
    if MATCHED_LINE_ARM in arms:
        executor(_CREATE_MATCH_MEMBER_TRIGGER_SQL)
        executor(_CREATE_LINE_DAY_TRIGGER_SQL)
    executor(_drop_trigger_sql(_OPENING_TRIGGER, _OPENINGS_TABLE))
    executor(_CREATE_OPENING_TRIGGER_SQL)
    if MOVEMENT_ARM not in arms:
        _drop_movement_functions(executor)
    if MATCHED_LINE_ARM not in arms:
        _drop_matched_line_functions(executor)


def apply_opening_functions(
    executor: Callable[[str], object], *, arms: "tuple[str, ...]",
) -> None:
    """Materialise the BASE functions and *arms*' functions, and no trigger.

    The half of :func:`apply_opening_infrastructure` that is pure
    ``CREATE OR REPLACE FUNCTION``.  Split out at plan step
    **balance:X-f3c-2b-2a** so a revision that changes only a function BODY can
    execute only function bodies.

    **The reason is a review finding rather than tidiness.**  That step's
    revision (``c9f4b1e78d02``) changes the governing-day lookup and nothing
    else, and its docstring said so: "this alters no table, no column and no
    constraint".  Calling the whole apply would have made that FALSE, because
    the trigger half issues ``DROP TRIGGER IF EXISTS`` plus
    ``CREATE CONSTRAINT TRIGGER``, which is the drop-and-recreate shape
    ``.claude/rules/database.md`` requires a ``Review:`` line for.  Restating
    the claim as a caveat was the alternative; removing the act is better,
    because a revision that does not touch a constraint cannot be wrong about
    whether it touched one.

    **The BASE is ``budget.books_hold`` and
    ``budget.account_books_opened_on``, and it is arm-independent** -- every
    arm's predicate asks both, so they are installed whatever is declared and
    survive an arm's withdrawal.  That is what makes a downgrade behaviourally
    exact rather than byte-exact: the movement bodies restored by withdrawing
    the matched-line arm still ask ``books_hold``, which is the same rule
    ruling **R-HG** states, spelled in the one place this tier spells it.

    **Order is load-bearing.**  Each openings-side PREDICATE is created before
    the dispatcher that ``PERFORM``s it -- see
    :func:`_create_opening_func_sql`, whose body names exactly the declared
    arms' predicates.

    Args:
        executor: Single-argument callable that accepts a SQL string and runs
            it.  Same contract as :func:`apply_opening_infrastructure`.
        arms: The arms whose functions to create.  See :data:`ALL_ARMS`.

    Raises:
        ValueError: When *arms* is empty or names an arm this module does not
            build.
    """
    unknown = tuple(arm for arm in arms if arm not in ALL_ARMS)
    if unknown:
        raise ValueError(
            f"apply_opening_functions: unknown books-boundary arm(s) "
            f"{unknown}; this module builds {ALL_ARMS}."
        )
    if not arms:
        raise ValueError(
            "apply_opening_functions: at least one arm is required.  An empty "
            "set would generate an openings dispatcher with an empty IF "
            "block, which is not valid PL/pgSQL; to leave a database with no "
            "books boundary at all, call remove_opening_infrastructure."
        )
    executor(_CREATE_BOOKS_HOLD_SQL)
    executor(_CREATE_OPENED_ON_SQL)
    if MOVEMENT_ARM in arms:
        executor(_CREATE_MOVEMENT_FUNC_SQL)
        executor(_CREATE_OPENING_PREDICATE_SQL)
    if MATCHED_LINE_ARM in arms:
        executor(_CREATE_MATCHED_LINE_PREDICATE_SQL)
        executor(_CREATE_MATCH_MEMBER_FUNC_SQL)
        executor(_CREATE_LINE_DAY_FUNC_SQL)
        executor(_CREATE_MATCHED_LINES_PREDICATE_SQL)
    executor(_create_opening_func_sql(arms))


def _drop_movement_functions(executor: Callable[[str], object]) -> None:
    """Drop the MOVEMENT arm's functions, innermost caller last.

    Args:
        executor: Same contract as :func:`apply_opening_infrastructure`.
    """
    executor(f"DROP FUNCTION IF EXISTS {_MOVEMENT_FUNCTION}()")
    executor(
        f"DROP FUNCTION IF EXISTS {_OPENING_PREDICATE_FUNCTION}(INTEGER)"
    )


def _drop_matched_line_functions(executor: Callable[[str], object]) -> None:
    """Drop the MATCHED-LINE arm's functions, innermost caller last.

    The two thin trigger functions first, then the openings-side predicate,
    then the per-line predicate both of the others reach.

    Args:
        executor: Same contract as :func:`apply_opening_infrastructure`.
    """
    executor(f"DROP FUNCTION IF EXISTS {_MATCH_MEMBER_FUNCTION}()")
    executor(f"DROP FUNCTION IF EXISTS {_LINE_DAY_FUNCTION}()")
    executor(
        "DROP FUNCTION IF EXISTS "
        f"{_MATCHED_LINES_PREDICATE_FUNCTION}(INTEGER)"
    )
    executor(f"DROP FUNCTION IF EXISTS {_MATCHED_LINE_PREDICATE}(INTEGER)")


def remove_opening_infrastructure(executor: Callable[[str], object]) -> None:
    """Inverse of :func:`apply_opening_infrastructure` for a migration downgrade.

    Drops the five triggers first and then the nine functions, innermost
    caller last, so nothing is dropped while something still references it.
    **The dependency chain, stated because the ORDER is the only thing this
    function has to get right:** the five triggers name FOUR trigger functions
    (one per movement attachment pair, one per matched-line attachment, and the
    openings dispatcher); the dispatcher ``PERFORM``s the TWO openings-side
    predicates; the matched-line trigger functions both ``PERFORM`` the
    per-line predicate; all four predicates read
    ``budget.account_books_opened_on``; and every one of them asks
    ``budget.books_hold``, which is why it is dropped LAST.  Every statement
    uses ``IF EXISTS``, so this is idempotent and a clean no-op on a database
    that never carried the infrastructure.

    Args:
        executor: Single-argument callable that accepts a SQL string and runs
            it.  Same contract as :func:`apply_opening_infrastructure`.
    """
    for table in _MOVEMENT_TABLES:
        executor(_drop_trigger_sql(_MOVEMENT_TRIGGER, table))
    executor(_drop_trigger_sql(_MATCH_MEMBER_TRIGGER, _MATCH_MEMBERS_TABLE))
    executor(_drop_trigger_sql(_LINE_DAY_TRIGGER, _BANK_LINES_TABLE))
    executor(_drop_trigger_sql(_OPENING_TRIGGER, _OPENINGS_TABLE))
    executor(f"DROP FUNCTION IF EXISTS {_OPENING_FUNCTION}()")
    _drop_movement_functions(executor)
    _drop_matched_line_functions(executor)
    executor(f"DROP FUNCTION IF EXISTS {_OPENED_ON_FUNCTION}(INTEGER)")
    executor(f"DROP FUNCTION IF EXISTS {_BOOKS_HOLD_FUNCTION}(DATE, DATE)")
