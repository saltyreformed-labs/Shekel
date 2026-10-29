"""Shared definitions for the rule that a deleted row holds no money.

**A payment or purchase is never under a deleted row** (plan step
``credit_card:CC-5-4a-4``).  Deleting a row takes its movements off the books
(ruling **R-CC75**) and an archive hides only rows holding nothing
(**R-CC63**), so a hidden row holds no money -- unless a door writes some under
it afterwards, or hides it while it still holds some.  The first happened,
measured by the step's third review: a recurring $120.00 Hotel marked Paid and
then deleted still answered a stale second tab's popover, and saving an Actual
of $125.00 wrote a dated $125.00 payment record under the hidden row.  Its pay
period then locked ("holds a recorded payment or purchase") and Reset was
refused for good, with no screen able to reach the row.

**The rule has two directions, so it takes two attachments**, the lesson
:mod:`app.level_infrastructure` and :mod:`app.opening_infrastructure` record:
the FACT lives on ``budget.transaction_entries`` (which row a movement is
under) and the STATE on ``budget.transactions`` (whether that row is deleted),
so either can move without the other.

* **A movement may not ARRIVE under a deleted row** (ruling **R-CC89**,
  developer 2026-09-23: *"The database refuses any payment or purchase written
  under a deleted row, so no door, now or later, can do it."*).  It arrives by
  ``INSERT``, or by an ``UPDATE`` that points it at another row.  An
  ``UPDATE`` that leaves ``transaction_id`` where it was is not an arrival, so
  it passes, and so do the ``SET NULL`` updates other tables' keys make to a
  movement's other columns.  Every deleted row counts, and under
  :data:`TRANSFER_ARM` every deleted transfer.
* **A row may not be HIDDEN while it holds one** (ruling **R-CC92**, which
  extends R-CC89, developer 2026-09-23, "Refuse both ways": *"The database
  also refuses hiding a row that still holds a payment or purchase, except a
  transfer's half (BAL-532's, until X-bi-6-4). The check runs when the change
  is saved, so a door that takes the money off and hides the row in the same
  save still works."*).  **A transfer's leg was excepted, and the exception
  was a FENCE with an owner**: the transfer's soft delete hid a leg holding
  its kept payment -- finding **balance:BAL-532**, owned by plan step
  ``balance:X-bi-6-4`` -- and refusing it would have turned that door into an
  error.  **Deleting the ``transfer_id IS NULL`` clause alone could not
  retire the fence**, because plan step ``balance:X-bi-6-4d-2`` re-parented a
  transfer's movements onto ``budget.transfers`` (ruling **R-BAL88**: two
  side links, ``transaction_id`` NULL under an exactly-one-parent check):
  after it the legs held nothing, a movement arriving under a deleted
  TRANSFER names no row the row arm reads, and a transfer's soft delete hides
  a ``budget.transfers`` row the row arm does not watch -- so R-CC92's "until
  X-bi-6-4" would have become permanent without anyone deciding it.  What the
  re-parent owed the rule is :data:`TRANSFER_ARM`, three changes together:
  the arrival arm also refuses a movement whose TRANSFER parent is deleted,
  reading that parent's ``is_deleted`` under the same :data:`ROW_WRITE_LOCK`
  on ``budget.transfers`` (a read without it reopens, for transfers, the race
  ruling R-CC96 closed below); the hiding arm gains an attachment on
  ``budget.transfers``, excepting nothing; and the ``transfer_id IS NULL``
  clause goes from the row arm's ``WHEN`` and its function.  **The third
  landed with the transfer delete that takes a side's records off first**
  (design D7, finding BAL-532), at the leaf's second checkpoint, so under
  :data:`TRANSFER_ARM` the row arm excepts nothing either, and since plan
  step ``balance:X-bi-6-4d-2`` no row is a transfer's leg at all (ruling
  **R-BAL258**; plan step ``balance:X-bi-6-5`` dropped the column one named
  its transfer by).  The
  row-only text a revision without :data:`TRANSFER_ARM` installs keeps the
  clause, and with it
  ``transfer_id`` watched as well as ``is_deleted``, so a raw ``UPDATE``
  re-pointing a hidden leg away from its transfer cannot walk out of the
  exception there.

**This module is the rule's database half.**  The arrival arm's words are the
two writers' own refusals -- :func:`app.services.entry_service.create_entry`
for a purchase and
:func:`app.services.status_seam._refusals.reject_settlement_on_a_deleted_row`
for a payment record -- and the pages' half is the two ownership doors
answering "not found" for a deleted row (``get_accessible_transaction`` and
``routes/transactions/_helpers._get_owned_transaction``).  The hiding arm has
no words of its own because no door reaches it in either order of a race: the
row delete takes the movements off first and the archive leaves a holding row
alone, and each reads what the row holds after its request's owner lock (plan
step ``balance:X-bn``), so no other click of the owner's can add a movement
between that read and the hide.  Both arms are what make the rule hold for a bulk statement, a psql
session and a writer nobody enumerated, the same pairing
``ck_transaction_entries_positive_amount`` has with ``entry_service``'s refusal
of a purchase worth nothing.

**The rule holds under concurrency because the arrival arm LOCKS the row**
(ruling **R-CC96**, developer 2026-09-23, "Lock in both": *"the database
check locks the row, so no deleted row can hold money from any writer, listed
or not"*).  Under ``READ COMMITTED`` each arm reads committed data only, and a
row's soft delete takes ``FOR NO KEY UPDATE`` while a movement ``INSERT``'s
foreign-key check takes ``FOR KEY SHARE`` -- two locks that do not conflict.
So an arm without a lock of its own read the row as live while its delete was
open, the delete's deferred check found no movement because the purchase had
not committed, and both committed: the step's fourth review measured a $12.34
purchase added by one session while another deleted its Groceries row, ending
``(hidden, held) = (True, 1)``, the state this module exists to forbid.  The
arm now reads ``is_deleted`` under :data:`ROW_WRITE_LOCK`, so the two
serialise in either order: a hide already open makes the arrival wait, and
refuses it once the hide commits; an arrival already open makes the hide wait
until the movement has committed, when the hiding arm sees it and refuses the
hide.

**Why ``FOR NO KEY UPDATE`` and not the weaker ``FOR SHARE``.**  Either
conflicts with a hide; the difference is what the same transaction does NEXT.
A writer that inserts a movement and then writes the row -- a record's
insert followed by its status ``UPDATE``, or a purchase followed by the
payback sync's lock -- would have to upgrade a ``FOR SHARE`` this arm took,
and two such writers each holding it then wait on each other.  Measured
2026-09-23 on two raw connections, ``FOR SHARE`` then ``FOR NO KEY UPDATE`` on
one row: ``DeadlockDetected`` for one of the two, where taking ``FOR NO KEY
UPDATE`` first let both commit.  The app's own doors never reach that shape:
each runs after its request's owner write lock (plan step ``balance:X-bn``,
ruling **R-CC106**), so two of one owner's clicks never hold the row at once,
and the second click of a race meets a door's sentence instead of this arm's
raw error.  Until that step each door took this same row lock itself, BEFORE
its insert (rulings R-CC96 and R-CC100).  So the strength is graded by the raw
measurement alone: with ``FOR SHARE`` here the app's race module still passed
(the step's fifth review, measured while the doors took the row lock).  It is
the RAW writer's guarantee.

**The arrival arm is an immediate ``BEFORE`` row trigger**, as
:mod:`app.level_infrastructure` chose and for a sharper reason here.  A
trigger's ``WHEN`` can read only the movement's own columns, never its row's
``is_deleted``, so a deferred trigger would queue an event on EVERY movement
written, and a queued event makes DDL on ``budget.transaction_entries`` illegal
until the queue is drained -- the hazard
:mod:`app.opening_infrastructure` records costing two CI failures.  Immediate is
also correct for the one ordering that matters: SQLAlchemy's unit of work
writes a row before its children, so an un-delete and a write in one flush
pass, and a delete and a write in one flush are refused.

**The hiding arm is a DEFERRED constraint trigger, checked at COMMIT**, because
the ruling says so -- *"The check runs when the change is saved, so a door
that takes the money off and hides the row in the same save still works."* --
and because the ORDER of the two writes inside a save is not the rule's to
grade.  ``transaction_service.delete_transaction`` stages the movements'
``DELETE`` through the one removal act and then the row's ``is_deleted``;
**measured 2026-09-23 with this arm made immediate, that door still passed**,
because a read between the two (``credit_workflow``'s payback lookup)
autoflushes the ``DELETE`` first -- while the same two statements written row
first were refused.  An immediate check would make a correct door depend on an
incidental autoflush.  Its ``WHEN`` reads the row's own columns, so an
event is queued only when a non-transfer row is hidden, never on an ordinary
write -- but **a transaction that hides such a row and then runs DDL on
``budget.transactions`` must drain first** (``SET CONSTRAINTS ALL
IMMEDIATE``), the pattern :mod:`app.opening_infrastructure` describes.  The
transfer arm's hiding attachment is the same kind of trigger with a wider
``WHEN`` (``NEW.is_deleted``: a transfer has no exempt kind), so it queues an
event on EVERY transfer soft delete, and a transaction that hides a transfer
and then runs DDL on ``budget.transfers`` must drain first too.

**The rule is built ARM BY ARM, and a revision declares its own**, the
construction :mod:`app.opening_infrastructure` adopted after measuring the
alternative: a migration imports this module LIVE, so without a declared arm
set ``c4a4e7d1b9f2`` (CC-5-4a-4's) would install, on a chain replay, an arm
naming link columns a later revision adds -- and the test template, which
replays the chain, would fail to build.  So each revision passes a LITERAL
tuple -- ``c4a4e7d1b9f2`` passes ``(ROW_ARM,)`` and the re-parent's revision
(X-bi-6-4d-2's) both arms -- and the two scripts that build a database at
head, ``scripts/init_database.py`` (fresh database, no migration chain) and
``scripts/build_test_template.py`` (re-applied idempotently so the latest
in-code definition wins), pass :data:`ALL_ARMS`.  A from-scratch database and
a migrated one therefore agree only while the NEWEST revision's tuple equals
:data:`ALL_ARMS`, which ``tests/test_models/test_deleted_row_arms.py`` asserts.
The statement is TOTAL: an arm the caller does not name is dropped, so a
downgrade that withdraws an arm is the same call naming one fewer.
``scripts/build_test_db_image.py`` counts :data:`DELETED_ROW_TRIGGERS` on the
baked image so a template missing an attachment is rebuilt rather than
trusted.

**Caller contract: every table an arm names must already exist**
(``budget.transactions`` and ``budget.transaction_entries``; with
:data:`TRANSFER_ARM`, ``budget.transfers`` and the entries' two side links).
``CREATE TRIGGER`` needs its table and columns, and each function body names
the other tables.  **The ROW-only text also names
``budget.transactions.transfer_id``**, which exists only below migration
``c956c1d83108`` (plan step ``balance:X-bi-6-5`` dropped it), so only a
revision below that one may apply the row arm without :data:`TRANSFER_ARM`:
``c4a4e7d1b9f2``'s upgrade and ``e616adf7fe22``'s downgrade, the two that do.
"""

from __future__ import annotations

from typing import Callable

#: The ROW arm (plan step ``credit_card:CC-5-4a-4``, migration
#: ``c4a4e7d1b9f2``): a movement may not arrive under a deleted
#: ``budget.transactions`` row, and a row may not be hidden while it holds one,
#: a transfer's leg excepted in the row-only text (finding BAL-532) and
#: nothing excepted beside :data:`TRANSFER_ARM`.
ROW_ARM = "row"

#: The TRANSFER arm (plan step ``balance:X-bi-6-4d-2``, ruling **R-BAL88**): a
#: transfer side's payment names its transfer by a side link rather than a
#: row, so the arrival arm also reads the TRANSFER's ``is_deleted`` under
#: :data:`ROW_WRITE_LOCK`, and a ``budget.transfers`` row may not be hidden
#: while a side links one.  It extends the row arm and means nothing without
#: it.
TRANSFER_ARM = "transfer"

#: Every arm, in the order they were added.  What the two scripts that build a
#: HEAD database pass; a revision passes a literal tuple instead (the module
#: docstring says why).
ALL_ARMS: tuple[str, ...] = (ROW_ARM, TRANSFER_ARM)

#: The arrival arm's function: refuse a movement arriving under a deleted row.
_ARRIVAL_FUNCTION = "budget.refuse_movement_under_deleted_row"

#: The hiding arm's function: refuse a non-transfer row hidden holding one.
_HIDING_FUNCTION = "budget.refuse_hiding_a_row_holding_money"

#: The transfer arm's hiding function: refuse a transfer hidden holding one.
_TRANSFER_HIDING_FUNCTION = "budget.refuse_hiding_a_transfer_holding_money"

#: ``(trigger name, schema-qualified table)`` for each attachment at HEAD, the
#: arrival arm first.  Exported so the image check can count them and a lift
#: can name them.
DELETED_ROW_TRIGGERS: tuple[tuple[str, str], ...] = (
    ("ck_movement_row_not_deleted", "budget.transaction_entries"),
    ("ck_hidden_row_holds_nothing", "budget.transactions"),
    ("ck_hidden_transfer_holds_nothing", "budget.transfers"),
)

#: The row lock the arrival arm takes on the movement's row, whatever state the
#: row is in (ruling **R-CC96**).  The module docstring's two concurrency
#: paragraphs say why this strength.
ROW_WRITE_LOCK = "FOR NO KEY UPDATE"

#: The row arm's arrival function, byte for byte what ``c4a4e7d1b9f2`` shipped.
_CREATE_ROW_ARRIVAL_FUNCTION_SQL = f"""
CREATE OR REPLACE FUNCTION {_ARRIVAL_FUNCTION}()
RETURNS TRIGGER AS $$
DECLARE
    v_hidden BOOLEAN;
BEGIN
    -- Re-saving a movement where it already is brings nothing to its row.
    IF TG_OP = 'UPDATE' AND NEW.transaction_id = OLD.transaction_id THEN
        RETURN NEW;
    END IF;
    -- Locked whatever its state: a filter on is_deleted would lock nothing
    -- while the row is live, and a hide committing after this read would
    -- then leave the movement under a hidden row.
    SELECT is_deleted INTO v_hidden FROM budget.transactions
    WHERE id = NEW.transaction_id {ROW_WRITE_LOCK};
    IF v_hidden THEN
        RAISE EXCEPTION
            'transaction % was deleted: a payment or purchase cannot be '
            'recorded under it (rule {_ARRIVAL_FUNCTION}, ruling R-CC89)',
            NEW.transaction_id;
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql
"""

#: The arrival function with the transfer arm: a movement has exactly one
#: parent (``ck_transaction_entries_one_parent``), a row or a transfer side,
#: and whichever it names is read under the same lock.  The early return
#: compares all three links with ``IS NOT DISTINCT FROM``, because a NULL
#: link -- every movement has two -- never equals anything under ``=``.
_CREATE_TRANSFER_ARRIVAL_FUNCTION_SQL = f"""
CREATE OR REPLACE FUNCTION {_ARRIVAL_FUNCTION}()
RETURNS TRIGGER AS $$
DECLARE
    v_hidden BOOLEAN;
    v_transfer_id INTEGER;
BEGIN
    -- Re-saving a movement under the parent it already has brings nothing to
    -- that parent, and neither does the account a side key's ON UPDATE
    -- CASCADE re-writes beside an unchanged link.
    IF TG_OP = 'UPDATE'
       AND NEW.transaction_id IS NOT DISTINCT FROM OLD.transaction_id
       AND NEW.expense_transfer_id IS NOT DISTINCT FROM OLD.expense_transfer_id
       AND NEW.income_transfer_id IS NOT DISTINCT FROM OLD.income_transfer_id
    THEN
        RETURN NEW;
    END IF;
    -- Locked whatever its state: a filter on is_deleted would lock nothing
    -- while the parent is live, and a hide committing after this read would
    -- then leave the movement under a hidden parent.
    IF NEW.transaction_id IS NOT NULL THEN
        SELECT is_deleted INTO v_hidden FROM budget.transactions
        WHERE id = NEW.transaction_id {ROW_WRITE_LOCK};
        IF v_hidden THEN
            RAISE EXCEPTION
                'transaction % was deleted: a payment or purchase cannot be '
                'recorded under it (rule {_ARRIVAL_FUNCTION}, ruling R-CC89)',
                NEW.transaction_id;
        END IF;
    END IF;
    v_transfer_id := coalesce(NEW.expense_transfer_id, NEW.income_transfer_id);
    IF v_transfer_id IS NOT NULL THEN
        SELECT is_deleted INTO v_hidden FROM budget.transfers
        WHERE id = v_transfer_id {ROW_WRITE_LOCK};
        IF v_hidden THEN
            RAISE EXCEPTION
                'transfer % was deleted: a payment cannot be recorded under '
                'it (rule {_ARRIVAL_FUNCTION}, ruling R-CC89)',
                v_transfer_id;
        END IF;
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql
"""

#: Asked of the row as it stands at COMMIT, not as the event saw it: a row
#: hidden and then restored, or re-parented to a transfer, in the same
#: transaction holds nothing this rule forbids.  The ROW-ONLY text, with R-CC92's
#: carve-out for a transfer's leg (``t.transfer_id IS NULL``), exactly as
#: ``c4a4e7d1b9f2`` installed it; :data:`TRANSFER_ARM` installs
#: :data:`_CREATE_TRANSFER_ERA_HIDING_FUNCTION_SQL` instead.
_CREATE_HIDING_FUNCTION_SQL = f"""
CREATE OR REPLACE FUNCTION {_HIDING_FUNCTION}()
RETURNS TRIGGER AS $$
BEGIN
    IF EXISTS (
        SELECT 1 FROM budget.transactions t
        WHERE t.id = NEW.id
          AND t.is_deleted
          AND t.transfer_id IS NULL
          AND EXISTS (
              SELECT 1 FROM budget.transaction_entries e
              WHERE e.transaction_id = t.id
          )
    ) THEN
        RAISE EXCEPTION
            'transaction % was deleted while it still holds a recorded '
            'payment or purchase: take them off the books first (rule '
            '{_HIDING_FUNCTION}, ruling R-CC92)',
            NEW.id;
    END IF;
    RETURN NULL;
END;
$$ LANGUAGE plpgsql
"""

#: The row hiding function under :data:`TRANSFER_ARM`: the row-only text with
#: the carve-out's one line gone (design D7 of plan step
#: ``balance:X-bi-6-4d-2``).  Derived from it rather than restated, so the two
#: cannot part anywhere else.
_CREATE_TRANSFER_ERA_HIDING_FUNCTION_SQL = _CREATE_HIDING_FUNCTION_SQL.replace(
    "          AND t.transfer_id IS NULL\n", "",
)

#: The transfer arm's hiding function, asked at COMMIT for the row arm's
#: reason: a transfer delete that takes its sides' records off and hides the
#: transfer in one save holds nothing this rule forbids.
_CREATE_TRANSFER_HIDING_FUNCTION_SQL = f"""
CREATE OR REPLACE FUNCTION {_TRANSFER_HIDING_FUNCTION}()
RETURNS TRIGGER AS $$
BEGIN
    IF EXISTS (
        SELECT 1 FROM budget.transfers x
        WHERE x.id = NEW.id
          AND x.is_deleted
          AND EXISTS (
              SELECT 1 FROM budget.transaction_entries e
              WHERE e.expense_transfer_id = x.id
                 OR e.income_transfer_id = x.id
          )
    ) THEN
        RAISE EXCEPTION
            'transfer % was deleted while it still holds a recorded payment: '
            'take it off the books first (rule {_TRANSFER_HIDING_FUNCTION}, '
            'ruling R-CC92)',
            NEW.id;
    END IF;
    RETURN NULL;
END;
$$ LANGUAGE plpgsql
"""


def _arms_rows(arms: tuple[str, ...]) -> tuple[tuple[str, str, str, str, str, str], ...]:
    """Return each attachment as ``(arm, trigger name, table, function, kind, firing clause)``.

    The firing clause is everything ``CREATE <kind>`` says between the trigger's
    name and ``EXECUTE FUNCTION``.  One row per attachment, so an attachment's
    name, table, function and firing rule cannot be edited apart.  The arrival
    attachment's clause depends on *arms*: with :data:`TRANSFER_ARM` it watches
    the two side links as well, which exist only from that arm's revision on.
    """
    arrival_columns = (
        "transaction_id, expense_transfer_id, income_transfer_id"
        if TRANSFER_ARM in arms else "transaction_id"
    )
    # The row hiding attachment excepts a transfer's leg only before the
    # transfer arm (R-CC92's carve-out; design D7).
    row_hiding_clause = (
        "AFTER UPDATE OF is_deleted ON {table} "
        "DEFERRABLE INITIALLY DEFERRED FOR EACH ROW "
        "WHEN (NEW.is_deleted)"
        if TRANSFER_ARM in arms else
        "AFTER UPDATE OF is_deleted, transfer_id ON {table} "
        "DEFERRABLE INITIALLY DEFERRED FOR EACH ROW "
        "WHEN (NEW.is_deleted AND NEW.transfer_id IS NULL)"
    )
    return (
        (
            ROW_ARM, *DELETED_ROW_TRIGGERS[0], _ARRIVAL_FUNCTION, "TRIGGER",
            f"BEFORE INSERT OR UPDATE OF {arrival_columns} ON {{table}} "
            "FOR EACH ROW",
        ),
        (
            ROW_ARM, *DELETED_ROW_TRIGGERS[1], _HIDING_FUNCTION,
            "CONSTRAINT TRIGGER", row_hiding_clause,
        ),
        (
            TRANSFER_ARM, *DELETED_ROW_TRIGGERS[2], _TRANSFER_HIDING_FUNCTION,
            "CONSTRAINT TRIGGER",
            "AFTER UPDATE OF is_deleted ON {table} "
            "DEFERRABLE INITIALLY DEFERRED FOR EACH ROW "
            "WHEN (NEW.is_deleted)",
        ),
    )


def _detach_sql(name: str, table: str) -> str:
    """Return the guarded ``DROP TRIGGER`` for one attachment."""
    return f"DROP TRIGGER IF EXISTS {name} ON {table}"


def _attach_sql(name: str, table: str, function: str, kind: str, clause: str) -> str:
    """Return one attachment's ``CREATE TRIGGER`` (or ``CONSTRAINT TRIGGER``)."""
    return (
        f"CREATE {kind} {name} {clause.format(table=table)} "
        f"EXECUTE FUNCTION {function}()"
    )


def _reject_unknown_arms(arms: tuple[str, ...]) -> None:
    """Refuse an arm set this module cannot build.

    Raises:
        ValueError: When *arms* names an arm this module does not build, or
            omits :data:`ROW_ARM` -- which every arm set needs: the transfer
            arm extends the row arm's arrival function and attaches nothing
            that refuses a movement on its own.
    """
    unknown = tuple(arm for arm in arms if arm not in ALL_ARMS)
    if unknown:
        raise ValueError(
            f"apply_deleted_row_infrastructure: unknown arm(s) {unknown}; "
            f"this module builds {ALL_ARMS}."
        )
    if ROW_ARM not in arms:
        raise ValueError(
            f"apply_deleted_row_infrastructure: {arms} omits {ROW_ARM!r}, "
            "which every arm set extends; a database with no deleted-row rule "
            "is remove_deleted_row_infrastructure."
        )


def apply_deleted_row_infrastructure(
    executor: Callable[[str], object], *, arms: tuple[str, ...],
) -> None:
    """Make the deleted-row rule equal exactly *arms*.

    Each named arm's functions are created or replaced, then every attachment
    is dropped and the named arms' made again -- PostgreSQL has no ``CREATE
    TRIGGER IF NOT EXISTS`` -- and a function no named arm uses is dropped, so
    a second run leaves exactly what the first did and an arm the caller does
    not name is withdrawn, triggers before functions.

    **Nothing to legalise first**: the arrival arm grades a movement as it
    arrives and each hiding arm a parent as it is hidden, never a row already
    stored, so neither refuses what the database holds when it is first
    applied.  The revision that installs each arm refuses to run while a
    hidden parent holds one (ruling **R-CC82**), which is what makes the
    hiding rule true of the stored rows too: the re-parent's revision
    (``e616adf7fe22``) refuses while a movement sits under a deleted twin
    or a deleted transfer.

    Args:
        executor: Single-argument callable that accepts a SQL string and runs
            it.  Pass ``op.execute`` from inside an Alembic migration; pass
            ``lambda s: session.execute(text(s))`` from inside a SQLAlchemy
            session.  Errors propagate -- the caller owns the outer
            transaction.
        arms: The arms this database's rule consists of.  A MIGRATION passes a
            literal tuple naming what it declared and censused; the two scripts
            that materialise a HEAD database pass :data:`ALL_ARMS`.

    Raises:
        ValueError: From :func:`_reject_unknown_arms`.
    """
    _reject_unknown_arms(arms)
    executor(
        _CREATE_TRANSFER_ARRIVAL_FUNCTION_SQL if TRANSFER_ARM in arms
        else _CREATE_ROW_ARRIVAL_FUNCTION_SQL
    )
    executor(
        _CREATE_TRANSFER_ERA_HIDING_FUNCTION_SQL if TRANSFER_ARM in arms
        else _CREATE_HIDING_FUNCTION_SQL
    )
    if TRANSFER_ARM in arms:
        executor(_CREATE_TRANSFER_HIDING_FUNCTION_SQL)
    for arm, name, table, function, kind, clause in _arms_rows(arms):
        executor(_detach_sql(name, table))
        if arm in arms:
            executor(_attach_sql(name, table, function, kind, clause))
    if TRANSFER_ARM not in arms:
        executor(f"DROP FUNCTION IF EXISTS {_TRANSFER_HIDING_FUNCTION}()")


def remove_deleted_row_infrastructure(executor: Callable[[str], object]) -> None:
    """Undo :func:`apply_deleted_row_infrastructure`: attachments first, then functions.

    A function a trigger still names cannot be dropped, hence the order.  Each
    statement is guarded with ``IF EXISTS``, so running this on a database
    that never had the rule, or twice, or only some of its arms, changes
    nothing it should not.

    Args:
        executor: The callable :func:`apply_deleted_row_infrastructure` takes.
    """
    rows = _arms_rows(ALL_ARMS)
    for _arm, name, table, _function, _kind, _clause in rows:
        executor(_detach_sql(name, table))
    for _arm, _name, _table, function, _kind, _clause in rows:
        executor(f"DROP FUNCTION IF EXISTS {function}()")
