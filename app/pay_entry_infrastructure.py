"""Shared definitions for the pay list's database backstop: a profile holds an entry.

**A salary profile holds at least one pay entry** (ruling **R-SAL68**; the
database half is task plan#3167 under plan step ``salary:X-av-3b``, from the
card's ``X-ez`` amendment, plan#2025).  Every paycheck is priced from the
profile's pay list (:class:`~app.models.salary_pay_entry.SalaryPayEntry`), and
the engine reads the latest entry on or before a day or else the first, so a
profile with none has nothing to price from.  The doors keep the rule in
words -- the create door writes the first entry in the unit of work that makes
the profile, and Remove refuses the only one
(:func:`app.services.pay_list_service.remove_entry`) -- and this module is
what makes a raw ``DELETE``, a bulk statement, a psql session or a writer
nobody enumerated unable to store the state anyway.  It is the backstop and
never the check: no door reaches it, so its words are for whoever wrote SQL.

**The rule is stated ONCE, as one predicate, and attached TWICE**, because the
state can be reached from either table:

* ``salary.pay_entries``: a ``DELETE`` of a profile's last entry, or an
  ``UPDATE OF salary_profile_id`` moving it to another profile.  The profile
  checked is the entry's OLD one, the one the event may have emptied.
* ``salary.salary_profiles``: an ``INSERT`` of a profile with none, or an
  ``UPDATE OF id`` renumbering one that has none.  The profile checked is the
  NEW row.  The second event is reachable only for a profile with no entry,
  since an entry's key refuses renumbering its profile; it is attached because
  a deferred trigger receives the row as it stood at the event, so the INSERT
  arm alone would look for the old number, find no profile, and pass.

**Deferred constraint triggers, validating at COMMIT**, and the deferral is
the rule rather than an implementation detail.  The create door inserts the
profile, flushes, and only then writes its first entry; an immediate trigger
would refuse that by statement order.  A profile's own disposal is the other
reason: the ORM's ``delete-orphan`` cascade deletes the entries BEFORE the
profile, and the database's ``ON DELETE CASCADE``
(``fk_pay_entries_salary_profile_id``) after it, and either way the predicate
runs once the profile is gone, finds no profile, and passes.  A delete of a
profile is never refused here.

**The predicate takes the profile's row lock, ``FOR NO KEY UPDATE``, before it
counts**, and that is what makes the rule hold for two writers at once.
Without it, two transactions each removing one of a profile's last two
entries would each count at commit under its own snapshot, each see the
other's entry still there, and both commit.  With it, the second blocks until
the first ends, and then counts on a fresh statement snapshot that holds the
first one's delete (PostgreSQL's ``READ COMMITTED``, the mode every command
request runs at, :mod:`app.db_transaction`).  The strength is the weakest that
conflicts with itself and not with the ``FOR KEY SHARE`` an entry's INSERT
takes on its profile, so recording pay never waits on it.  The doors were
already serialised by the owner's write lock (:mod:`app.db_transaction`); this
covers the writer that takes none.

**Three limits, named rather than left to be assumed:**

* A writer at ``REPEATABLE READ`` keeps one snapshot, so the lock alone does
  not show it the other transaction's delete, and two such writers can still
  empty a profile.  ``SERIALIZABLE`` refuses one of them by its own conflict
  detection; ``READ COMMITTED`` is closed by the lock.  The app's command
  transactions run at ``READ COMMITTED``, and its query transactions are read
  only.
* ``TRUNCATE`` fires no row trigger, so ``TRUNCATE salary.pay_entries`` alone
  empties every profile.  The runtime role ``shekel_app`` holds no
  ``TRUNCATE`` (``scripts/init_db_role.sql`` grants it ``SELECT, INSERT,
  UPDATE, DELETE``), so only the tables' owner can.
* ``session_replication_role = replica`` (which ``pg_restore
  --disable-triggers`` sets) fires no constraint trigger, and the owner or a
  superuser can drop the triggers.  The guarantee is against every ROUTINE
  writer, as :mod:`app.pay_stub_infrastructure` states for its own family.

**A deferred trigger makes DDL on its table illegal until its events drain**:
PostgreSQL refuses ``ALTER TABLE`` on a table with pending trigger events, so
a transaction that inserts a profile or deletes an entry and then alters
either table fails with ``ObjectInUse``.  The remedy is ``SET CONSTRAINTS ALL
IMMEDIATE`` before the DDL, or the DDL first;
:mod:`app.opening_infrastructure` states the same of its own tables.

**Three callers must produce identical infrastructure**, the contract
:mod:`app.append_only_infrastructure` states: the Alembic revision that
installs it (``c3a7e5d91f42``, which first refuses any profile already holding
none), ``scripts/init_database.py`` (a fresh database, no migration chain) and
``scripts/build_test_template.py`` (re-applied idempotently so the latest
in-code definition wins).  ``scripts/build_test_db_image.py`` counts
:data:`PAY_ENTRY_TRIGGERS` on the baked image so a template missing an arm is
rebuilt rather than trusted.

**Caller contract: both tables must already exist.**  ``CREATE TRIGGER`` needs
its table; the PL/pgSQL bodies resolve the tables they read only when they
first run, so applying early would surface as a missing relation at COMMIT.
"""

from __future__ import annotations

from typing import Callable

_PROFILES = "salary.salary_profiles"
_ENTRIES = "salary.pay_entries"

#: The rule, stated once: a profile that still exists holds an entry.
_PREDICATE_FUNCTION = "salary.assert_profile_holds_a_pay_entry"

#: The two thin trigger functions, one per attached table, each naming the
#: profile its event could have left empty and asking the predicate.
_ENTRY_TRIGGER_FUNCTION = "salary.refuse_pay_entry_leaving_its_profile_empty"
_PROFILE_TRIGGER_FUNCTION = "salary.refuse_profile_saved_without_pay"

_ENTRY_TRIGGER = "ck_profile_keeps_a_pay_entry"
_PROFILE_TRIGGER = "ck_profile_starts_with_a_pay_entry"

#: ``(trigger name, schema-qualified table)`` for each attachment.  Exported so
#: the image check can count them and a lift can name them.
PAY_ENTRY_TRIGGERS: tuple[tuple[str, str], ...] = (
    (_ENTRY_TRIGGER, _ENTRIES),
    (_PROFILE_TRIGGER, _PROFILES),
)

_CREATE_PREDICATE_SQL = f"""
CREATE OR REPLACE FUNCTION {_PREDICATE_FUNCTION}(p_profile_id INTEGER)
RETURNS VOID AS $$
BEGIN
    -- The profile's row lock first, so a second writer emptying the same
    -- profile waits here for the first to end, and the count below, a
    -- statement of its own, sees what the first committed.  No row: the
    -- profile is gone, deleted in this transaction (its entries cascading
    -- with it) or by one that committed while this waited, and a profile
    -- that no longer exists is owed no entry.
    PERFORM 1 FROM {_PROFILES} WHERE id = p_profile_id FOR NO KEY UPDATE;
    IF NOT FOUND THEN
        RETURN;
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM {_ENTRIES} WHERE salary_profile_id = p_profile_id
    ) THEN
        RAISE EXCEPTION
            'salary profile % would hold no pay entry: every paycheck is '
            'priced from its pay list, so a profile keeps at least one '
            '(ruling R-SAL68). Write a new profile''s first entry in the '
            'transaction that creates it, record another entry before '
            'removing the last, or delete the profile itself.',
            p_profile_id;
    END IF;
END;
$$ LANGUAGE plpgsql
"""

_CREATE_ENTRY_TRIGGER_FUNCTION_SQL = f"""
CREATE OR REPLACE FUNCTION {_ENTRY_TRIGGER_FUNCTION}()
RETURNS TRIGGER AS $$
BEGIN
    -- A DELETE, or an UPDATE OF salary_profile_id: either can leave the
    -- entry's OLD profile empty.  An UPDATE naming the column but keeping its
    -- value asks about a profile that still holds this entry, and passes.
    PERFORM {_PREDICATE_FUNCTION}(OLD.salary_profile_id);
    RETURN NULL;
END;
$$ LANGUAGE plpgsql
"""

_CREATE_PROFILE_TRIGGER_FUNCTION_SQL = f"""
CREATE OR REPLACE FUNCTION {_PROFILE_TRIGGER_FUNCTION}()
RETURNS TRIGGER AS $$
BEGIN
    -- An INSERT, or an UPDATE OF id: the profile as the event left it.
    PERFORM {_PREDICATE_FUNCTION}(NEW.id);
    RETURN NULL;
END;
$$ LANGUAGE plpgsql
"""


def _create_trigger_sql() -> tuple[str, ...]:
    """Return the two ``CREATE CONSTRAINT TRIGGER`` statements, one per attachment.

    Neither carries a ``WHEN`` clause: PostgreSQL refuses one naming ``NEW`` on
    a trigger that fires on ``DELETE``, or ``OLD`` on one that fires on
    ``INSERT``, and each attachment fires on both kinds.  The ``UPDATE OF``
    column lists are what narrow them: an ``UPDATE`` fires only when its
    ``SET`` names the column, and no door's does.

    Returns:
        The entry-side attachment, then the profile-side one.
    """
    return (
        f"CREATE CONSTRAINT TRIGGER {_ENTRY_TRIGGER} "
        f"AFTER DELETE OR UPDATE OF salary_profile_id ON {_ENTRIES} "
        "DEFERRABLE INITIALLY DEFERRED "
        f"FOR EACH ROW EXECUTE FUNCTION {_ENTRY_TRIGGER_FUNCTION}()",
        f"CREATE CONSTRAINT TRIGGER {_PROFILE_TRIGGER} "
        f"AFTER INSERT OR UPDATE OF id ON {_PROFILES} "
        "DEFERRABLE INITIALLY DEFERRED "
        f"FOR EACH ROW EXECUTE FUNCTION {_PROFILE_TRIGGER_FUNCTION}()",
    )


def _drop_trigger_sql() -> tuple[str, ...]:
    """Return a guarded ``DROP TRIGGER`` for each attachment.

    PostgreSQL has no ``CREATE CONSTRAINT TRIGGER IF NOT EXISTS``, so every
    apply pairs these with a fresh create to stay idempotent -- the pattern the
    sibling infrastructure modules use.

    Returns:
        One ``DROP TRIGGER IF EXISTS`` per entry in :data:`PAY_ENTRY_TRIGGERS`.
    """
    return tuple(
        f"DROP TRIGGER IF EXISTS {name} ON {table}"
        for name, table in PAY_ENTRY_TRIGGERS
    )


def apply_pay_entry_infrastructure(executor: Callable[[str], object]) -> None:
    """Idempotently install the at-least-one-entry rule on both tables.

    Creates or replaces the predicate and the two trigger functions, then
    drops and re-creates each attachment.  Every statement is idempotent, so a
    second run is indistinguishable from the first.

    **The caller must have checked the rows already there**: a constraint
    trigger validates writes, not existing rows, so a profile already holding
    no entry would pass this and refuse the next write that touches it.  The
    installing revision refuses to run while one exists.

    Args:
        executor: Single-argument callable that accepts a SQL string and runs
            it.  Pass ``op.execute`` from inside an Alembic migration; pass
            ``lambda s: session.execute(text(s))`` from inside a SQLAlchemy
            session.  Errors propagate -- the caller owns the outer
            transaction.
    """
    for statement in (
        _CREATE_PREDICATE_SQL,
        _CREATE_ENTRY_TRIGGER_FUNCTION_SQL,
        _CREATE_PROFILE_TRIGGER_FUNCTION_SQL,
        *_drop_trigger_sql(),
        *_create_trigger_sql(),
    ):
        executor(statement)


def remove_pay_entry_infrastructure(executor: Callable[[str], object]) -> None:
    """Inverse of :func:`apply_pay_entry_infrastructure`.

    Drops both attachments, then the two trigger functions, then the predicate
    they call, so nothing is dropped while something still references it.
    Every statement uses ``IF EXISTS``, so this is idempotent and a clean
    no-op on a database that never carried the infrastructure.

    Args:
        executor: Same contract as :func:`apply_pay_entry_infrastructure`.
    """
    for statement in (
        *_drop_trigger_sql(),
        f"DROP FUNCTION IF EXISTS {_ENTRY_TRIGGER_FUNCTION}()",
        f"DROP FUNCTION IF EXISTS {_PROFILE_TRIGGER_FUNCTION}()",
        f"DROP FUNCTION IF EXISTS {_PREDICATE_FUNCTION}(INTEGER)",
    ):
        executor(statement)
