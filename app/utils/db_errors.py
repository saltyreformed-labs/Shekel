"""
Shekel Budget App -- Database Error Helpers

Utilities for inspecting the database errors SQLAlchemy wraps
(:class:`sqlalchemy.exc.IntegrityError` and its ``DBAPIError`` siblings)
without resorting to substring matching of the underlying driver's
error message.  String matching is brittle: PostgreSQL's
``UniqueViolation`` text format is documented but not guaranteed to
remain identical across versions, and a constraint name that happens
to appear inside another part of the message would produce a false
positive.

psycopg instead exposes the structured PostgreSQL error packet via
``exception.diag`` -- ``diag.constraint_name``, ``diag.sqlstate`` and
the rest of the ``Diagnostic`` interface.  This module wraps those
lookups so a caller can answer "did the IntegrityError fire on the
named constraint?" or "is this table missing?" with a single call.

**Why the SQLSTATE and not the exception CLASS.**  Which DB-API class a
SQLSTATE maps to is the DRIVER's choice, and it moved when the
project changed drivers (plan step balance:X-dj): a PL/pgSQL ``RAISE``
(``P0001``) is an ``InternalError`` under psycopg2 and a
``ProgrammingError`` under psycopg 3.  So ``except ProgrammingError``
meant "a table is missing, or a query is malformed, or a privilege is
absent" under one driver and also "a trigger refused the write" under
the next.  The five-character SQLSTATE is the server's own answer and
does not move with the driver.

The helper is the load-bearing piece of the C-19 idempotency
backstop: when ``credit_workflow.mark_as_credit`` or
``entry_credit_workflow.sync_entry_payback`` is bypassed by a future
caller and a duplicate CC Payback insert reaches PostgreSQL, the
partial unique index ``uq_transactions_credit_payback_unique``
rejects it, the route layer recognises the constraint name through
this helper, and the user sees idempotent success instead of an HTTP
500.
"""

from sqlalchemy.exc import DBAPIError, IntegrityError

#: PostgreSQL's SQLSTATEs for a schema that is BEHIND the code reading it:
#: ``undefined_table`` ("relation ... does not exist") and
#: ``undefined_column`` ("column ... does not exist") -- what a pending
#: migration that creates a table, or adds a column to one, leaves a reader
#: meeting until it runs.  The set is a fence around an ordering defect:
#: an entry point that migrates builds the app BEFORE the migration it
#: exists to run, and building it runs the development/testing ref seed and
#: (unless ``init_ref_cache=False``, as ``scripts/init_database.py`` passes)
#: the ref-cache load.  Plan step balance:X-dl removes the cause (such an
#: entry point builds the app with neither), and with it both tolerances
#: and this set.
UNDEFINED_TABLE = "42P01"
UNDEFINED_COLUMN = "42703"
SCHEMA_BEHIND_CODE = frozenset({UNDEFINED_TABLE, UNDEFINED_COLUMN})


def is_unique_violation(exc: IntegrityError, constraint_name: str) -> bool:
    """Return True when the IntegrityError fired on the named constraint.

    The check inspects ``exc.orig.diag.constraint_name``, the
    structured PostgreSQL error field that psycopg surfaces verbatim
    from the server's ``ErrorResponse`` packet.  This is exact and
    avoids the false-positive risk of a substring match on the
    free-form error message.

    Args:
        exc: The IntegrityError caught by the calling code.  The
            underlying driver exception is read from ``exc.orig``;
            if absent, the helper conservatively returns ``False``.
        constraint_name: The expected constraint or index name (e.g.
            ``"uq_transactions_credit_payback_unique"``).  Compared
            for exact equality.

    Returns:
        ``True`` if the underlying error reported the named
        constraint, ``False`` otherwise (including the case where
        the driver did not populate ``diag.constraint_name`` -- some
        non-uniqueness violations leave it blank).

    Notes:
        The driver sets ``diag.constraint_name`` for every
        ``UniqueViolation`` -- the field is part of the wire protocol
        and is not stripped.  psycopg2 named it identically, so the
        change of driver at plan step balance:X-dj left this helper
        unmodified.
    """
    orig = getattr(exc, "orig", None)
    if orig is None:
        return False
    diag = getattr(orig, "diag", None)
    if diag is None:
        return False
    actual = getattr(diag, "constraint_name", None)
    return actual == constraint_name


def sqlstate_of(exc: DBAPIError) -> str | None:
    """Return the SQLSTATE the server reported for *exc*, or ``None``.

    The one reader of the five-character code (see the module docstring
    for why the code and not the class), shared by
    :func:`is_schema_behind_code` and the suite's refusal assertions.

    Args:
        exc: The database error SQLAlchemy raised.  The driver's
            exception is read from ``exc.orig``.

    Returns:
        ``exc.orig.diag.sqlstate``, or ``None`` when there is no driver
        exception or it carries no diagnostic.
    """
    diag = getattr(getattr(exc, "orig", None), "diag", None)
    return getattr(diag, "sqlstate", None)


def is_schema_behind_code(exc: DBAPIError) -> bool:
    """Return True when the database refused a statement because its schema is behind the code.

    The one test the two bootstrap tolerances apply
    (``app._seed_ref_tables`` and ``app.ref_cache._state._load_rows``),
    which can run before a pending migration has created a table or
    added a column the models already name -- ``flask db upgrade`` builds
    the app before it migrates.
    A table or column the schema does not have yet is the quirk they
    exist to absorb; every OTHER refusal -- a malformed query, an absent
    privilege, a trigger's ``RAISE`` -- is a real failure that must
    propagate.  Each used to spell that test as ``except
    ProgrammingError``, which caught all of them (see the module
    docstring for how the driver change widened it further).

    Args:
        exc: The database error SQLAlchemy raised.  The driver's
            exception is read from ``exc.orig``; when it is absent, or
            carries no ``diag``, the answer is ``False`` -- an error
            that cannot be read as a schema behind its code is not
            treated as one.

    Returns:
        ``True`` when the server reported a SQLSTATE in
        :data:`SCHEMA_BEHIND_CODE`.
    """
    return sqlstate_of(exc) in SCHEMA_BEHIND_CODE
