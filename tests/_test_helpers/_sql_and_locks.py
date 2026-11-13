"""SQL and locks: the cash backfill's frozen SQL re-homed for a test, captured statements, and the
per-owner advisory write lock.
"""


# The frozen 7d63 counter-account column list, and the same list with the
# Step-4 ``kind_id`` added AND the bucket flag under the name plan step
# ``balance:X-bi-6-3`` gave it (``is_fallback`` -> ``is_owner_bucket``, ruling
# R-BAL99).  Module-level so :func:`inject_cash_backfill_kind_id` reuses a
# single source for both Pass-A INSERTs.
_FROZEN_COUNTER_COLUMNS = "(user_id, class_id, category_id, is_fallback, name) "


_KIND_INJECTED_COUNTER_COLUMNS = (
    "(user_id, class_id, category_id, is_owner_bucket, name, kind_id) "
)


# The frozen fallback INSERT's conflict target and Pass B's fallback join, each
# re-homed onto the HEAD schema's owner-bucket key by the same injector: the
# singleton is ``(user_id, class_id, kind_id) WHERE is_owner_bucket`` since
# ``X-bi-6-3``, and an ``ON CONFLICT`` must name an index that exists.
_FROZEN_FALLBACK_CONFLICT = "ON CONFLICT (user_id, class_id) WHERE is_fallback DO NOTHING"


_HEAD_FALLBACK_CONFLICT = (
    "ON CONFLICT (user_id, class_id, kind_id) WHERE is_owner_bucket DO NOTHING"
)


_FROZEN_FALLBACK_JOIN = "AND counter_ledger.is_fallback = TRUE) ) "


_HEAD_FALLBACK_JOIN = "AND counter_ledger.is_owner_bucket = TRUE) ) "


def _inject_pass_a_kind(frozen_sql, name_expr_tail, kind_name):
    """Add the kind_id column + its name-resolving subquery to a Pass-A INSERT.

    Reuses the frozen, immutable shipped SQL as the single source of every other
    column; appends the kind subquery to the end of the SELECT list (right after
    ``name_expr_tail`` -- the frozen text closing that INSERT's ``name``
    expression).  Asserts the transform fired so a future change to the shipped
    constant fails loudly here rather than silently emitting kind-less SQL that
    trips the NOT NULL at insert.

    Args:
        frozen_sql: The frozen Pass-A INSERT ... SELECT statement.
        name_expr_tail: The exact frozen text that closes the SELECT's ``name``
            expression (the anchor the kind subquery is appended after).
        kind_name: The ``ref.ledger_account_kinds`` name this INSERT's rows take
            (``"category"`` or ``"fallback"``).

    Returns:
        str -- the INSERT with ``kind_id`` added to the column list and the kind
        subquery added to the SELECT list.
    """
    subquery = (
        f"(SELECT id FROM ref.ledger_account_kinds WHERE name = '{kind_name}')"
    )
    injected = (
        frozen_sql
        .replace(_FROZEN_COUNTER_COLUMNS, _KIND_INJECTED_COUNTER_COLUMNS)
        .replace(name_expr_tail, f"{name_expr_tail.rstrip()}, {subquery} ")
    )
    assert _KIND_INJECTED_COUNTER_COLUMNS in injected and subquery in injected, (
        "kind_id injection did not fire -- the frozen 7d63 Pass-A SQL changed; "
        "update the anchors in tests/_test_helpers/_sql_and_locks.py"
    )
    return injected


def inject_cash_backfill_kind_id(monkeypatch, migration_module):
    """Re-home a 7d63 migration's frozen SQL onto the HEAD chart schema.

    Step 4, Commit 2 (``efca4315bf81``) added a NOT NULL
    ``budget.ledger_accounts.kind_id``, so the frozen 7d63 Pass-A INSERTs --
    which predate that column and omit it -- can no longer run standalone at
    HEAD.  In production 7d63 ran at its own revision (before ``kind_id``
    existed) and the Step-4 migration then backfilled each row's kind from its
    column shape (category rows -> ``category``, fallback rows -> ``fallback``);
    at HEAD the two are fused because ``kind_id`` is already NOT NULL.  Plan
    step ``balance:X-bi-6-3`` (ruling **R-BAL99**) moved the schema under the
    frozen text a second time -- ``is_fallback`` became ``is_owner_bucket`` and
    the fallback singleton ``(user_id, class_id) WHERE is_fallback`` became
    ``(user_id, class_id, kind_id) WHERE is_owner_bucket`` -- so the same
    injector re-homes the column list, the fallback INSERT's ``ON CONFLICT``
    target and Pass B's fallback join too, each anchored on the frozen text so a
    change to the shipped constant fails loudly here.

    This swaps the two frozen, immutable Pass-A SQL constants on
    *migration_module* for kind-injected equivalents -- reusing the shipped
    mapping SQL as the single source -- and Pass B's backfill SQL for one whose
    fallback join reads the head owner-bucket flag, so the migration's real
    ``_backfill_settled_transactions`` orchestration runs unchanged at HEAD with
    Pass A carrying the kind the Step-4 backfill would assign.  Its one caller
    is the cash-backfill suite (``tests/test_models/test_posting_cash_backfill.py``).
    ``monkeypatch`` auto-reverts the patched constants after each test.

    Args:
        monkeypatch: The test's ``monkeypatch`` fixture.
        migration_module: The loaded 7d63 migration module (the suite loads its
            own via :func:`load_migration_module`, and patches that copy).
    """
    monkeypatch.setattr(
        migration_module, "_CREATE_CATEGORY_LEDGER_ACCOUNTS_SQL",
        _inject_pass_a_kind(
            migration_module._CREATE_CATEGORY_LEDGER_ACCOUNTS_SQL,
            "LEFT(c.group_name || ': ' || c.item_name, 100) ",
            "category",
        ),
    )
    fallback_sql = _inject_pass_a_kind(
        migration_module._CREATE_FALLBACK_LEDGER_ACCOUNTS_SQL,
        "ELSE 'Uncategorized Expense' END ",
        "fallback",
    )
    assert _FROZEN_FALLBACK_CONFLICT in fallback_sql, (
        "the frozen 7d63 fallback ON CONFLICT changed; update the anchor in "
        "tests/_test_helpers/_sql_and_locks.py"
    )
    monkeypatch.setattr(
        migration_module, "_CREATE_FALLBACK_LEDGER_ACCOUNTS_SQL",
        fallback_sql.replace(_FROZEN_FALLBACK_CONFLICT, _HEAD_FALLBACK_CONFLICT),
    )
    backfill_sql = migration_module._SETTLED_TRANSACTION_BACKFILL_SQL
    assert _FROZEN_FALLBACK_JOIN in backfill_sql, (
        "the frozen 7d63 Pass-B fallback join changed; update the anchor in "
        "tests/_test_helpers/_sql_and_locks.py"
    )
    monkeypatch.setattr(
        migration_module, "_SETTLED_TRANSACTION_BACKFILL_SQL",
        backfill_sql.replace(_FROZEN_FALLBACK_JOIN, _HEAD_FALLBACK_JOIN),
    )


def capture_sql_statements(fn):
    """Run ``fn`` while recording every SQL statement the engine emits.

    The statement-capture idiom the advisory-lock suites grade against: it is
    the only way to assert, at the unit level, that a given path DOES or does
    NOT take a lock, and -- since plan step X-f1c3c -- that it takes it BEFORE
    the read it protects.  Ordering is the half a presence check cannot see: a
    lock taken after the reconcile has already read what is posted serialises
    nothing.

    It lived as a private ``_capture_statements`` in
    ``tests/test_services/test_pay_period_topup.py`` until the ledger reconciles
    needed the same assertion; ``tests/`` is outside R0801's reach, so a second
    copy would not have been flagged.

    **It captures PARAMETERS as well as text, and that is load-bearing.**
    SQLAlchemy BINDS the lock key, so every acquisition emits the byte-identical
    string ``SELECT pg_advisory_xact_lock(%(pg_advisory_xact_lock_2)s,
    %(pg_advisory_xact_lock_3)s)`` whatever it is locking.  A test that graded
    statement text alone could not tell a per-user lock from a per-account one,
    a per-scenario one, or a constant -- which is exactly what two independent
    adversarial reviews caught it doing.  See :func:`advisory_lock_keys`.

    Args:
        fn: A zero-argument callable to run under capture.

    Returns:
        ``(result, statements)`` -- *fn*'s return value and a list of
        ``(statement, parameters)`` pairs in emission order.
    """
    # Imported here rather than at module scope: this helper module is imported
    # before the app's extensions are configured in some collection orders.
    from app.extensions import db  # pylint: disable=import-outside-toplevel
    from sqlalchemy import event  # pylint: disable=import-outside-toplevel

    statements: list[tuple] = []

    def _cap(_conn, _cursor, statement, parameters, *_args, **_kwargs):
        statements.append((statement, parameters))

    event.listen(db.engine, "before_cursor_execute", _cap)
    try:
        result = fn()
    finally:
        event.remove(db.engine, "before_cursor_execute", _cap)
    return result, statements


def took_advisory_lock(statements):
    """Return whether any captured statement acquired the per-user write lock.

    Args:
        statements: The ``(statement, parameters)`` list from
            :func:`capture_sql_statements`.

    Returns:
        ``True`` when at least one statement called ``pg_advisory_xact_lock``.
    """
    return any("pg_advisory_xact_lock" in text for text, _params in statements)


def advisory_lock_keys(statements):
    """Return the ``(namespace, key)`` pairs the captured run actually locked.

    The half :func:`took_advisory_lock` cannot see.  The two-argument
    ``pg_advisory_xact_lock(namespace, key)`` form binds BOTH arguments, so the
    emitted SQL is identical for every key and only the parameters distinguish
    "this locked user 7" from "this locked account 7" or "this locked a
    constant".  Every assertion about WHAT is locked has to come through here.

    The bind names carry SQLAlchemy's positional suffixes
    (``pg_advisory_xact_lock_2`` / ``_3``), so the two values are ordered by
    that trailing integer rather than by dict iteration order.

    Args:
        statements: The ``(statement, parameters)`` list from
            :func:`capture_sql_statements`.

    Returns:
        A list of ``(namespace, key)`` tuples, one per acquisition, in
        emission order (duplicates kept -- a re-entrant double-take is two
        entries, which is itself worth asserting).
    """
    def _suffix(name):
        tail = str(name).rsplit("_", 1)[-1]
        return int(tail) if tail.isdigit() else 0

    keys = []
    for text, params in statements:
        if "pg_advisory_xact_lock" not in text:
            continue
        if isinstance(params, dict):
            values = [params[name] for name in sorted(params, key=_suffix)]
        else:
            values = list(params or ())
        keys.append(tuple(values))
    return keys


def advisory_lock_precedes(statements, *table_names):
    """Return whether the write lock was taken BEFORE any of *table_names* was read.

    The ordering half of the lock assertion.  A reconcile that takes its lock
    after reading what is posted has serialised nothing: the second transaction
    has already read the same pre-state and will compute the same delta against
    it.  Grading presence alone would pass such a build.

    Args:
        statements: The statement list from :func:`capture_sql_statements`.
        *table_names: Substrings identifying the tables whose first read must
            come after the lock, e.g. ``"journal_entries"``.  Matched as
            substrings of the emitted SQL, so a bare table name is enough and a
            schema-qualified one also works.

    Returns:
        ``True`` when a ``pg_advisory_xact_lock`` statement is emitted and every
        named table's FIRST appearance is at a later index; ``False`` when the
        lock is absent, or when any named table was read first.  A table that
        is never read does not falsify the ordering -- there is nothing to
        order against -- so callers assert the read happened separately.
    """
    texts = [text for text, _params in statements]
    lock_at = next(
        (i for i, text in enumerate(texts) if "pg_advisory_xact_lock" in text),
        None,
    )
    if lock_at is None:
        return False
    for table in table_names:
        read_at = next(
            (i for i, text in enumerate(texts) if table in text), None,
        )
        if read_at is not None and read_at < lock_at:
            return False
    return True


#: How long an acquisition the test EXPECTS to be blocked waits before
#: PostgreSQL cancels it -- long enough that the free-key controls never trip
#: it on a loaded host, short enough that a genuinely blocked statement fails
#: the test in under a second.
OWNER_LOCK_BLOCK_TIMEOUT_MS = 750


def owner_lock_key(owner_id):
    """Return the ``(namespace, key)`` pair the owner's write lock is taken on.

    Args:
        owner_id: The owning user's id.

    Returns:
        The pair :func:`advisory_lock_keys` reports for that owner.
    """
    # Deferred like the imports above: this module can be imported before the
    # app's extensions are configured.
    # pylint: disable-next=import-outside-toplevel
    from app.services.user_write_lock import _USER_WRITE_LOCK_NAMESPACE
    return (_USER_WRITE_LOCK_NAMESPACE, owner_id)


class HeldOwnerKey:
    """A second connection holding one owner's write lock until the block ends.

    The pattern every test that proves the lock SERIALISES uses: a transaction
    on its own connection takes the owner's key, the code under test runs and
    must wait, and leaving the block rolls the holder back -- releasing the
    key -- even when an assertion inside it failed.  ``connection`` is open
    for the block, so a test can also write through the holder (committed by
    nothing; rolled back with it).
    """

    def __init__(self, owner_id):
        """Remember whose key to hold.

        Args:
            owner_id: The owner whose write lock the connection takes.
        """
        self.owner_id = owner_id
        self.connection = None

    def __enter__(self):
        """Open the connection and take the key inside its transaction."""
        # pylint: disable-next=import-outside-toplevel
        from sqlalchemy import text
        # pylint: disable-next=import-outside-toplevel
        from app.extensions import db
        namespace, key = owner_lock_key(self.owner_id)
        self.connection = db.engine.connect()
        self.connection.execute(
            text("SELECT pg_advisory_xact_lock(:ns, :owner)"),
            {"ns": namespace, "owner": key},
        )
        return self

    def __exit__(self, *exc_info):
        """Release the key by ending the holder's transaction, then close."""
        self.connection.rollback()
        self.connection.close()


def bound_lock_waits():
    """Bound this session's transaction's lock waits to :data:`OWNER_LOCK_BLOCK_TIMEOUT_MS`.

    Issued on the test's own session.  Under the test client a COMMAND
    request runs in the transaction already open on that session (the app
    context is shared, and :mod:`app.db_transaction` gives only a QUERY its
    own transaction), so the request's acquisition inherits the bound.  It
    only SHORTENS a wait: pair it with :func:`assert_timed_out_on_owner_key`,
    because a timeout on any other lock reads the same.
    """
    # pylint: disable-next=import-outside-toplevel
    from sqlalchemy import text
    # pylint: disable-next=import-outside-toplevel
    from app.extensions import db
    db.session.execute(
        text(f"SET LOCAL lock_timeout = '{OWNER_LOCK_BLOCK_TIMEOUT_MS}ms'"),
    )


def assert_timed_out_on_owner_key(error, owner_id):
    """Assert *error* is a lock timeout on *owner_id*'s write lock, not another.

    A timeout on any other lock -- a row the test holds, or the holder's own
    acquisition -- reads the same in its message, so the statement that timed
    out and its bound key are what is graded.

    Args:
        error: The :class:`~sqlalchemy.exc.OperationalError` raised.
        owner_id: The owner whose key was held.
    """
    assert "lock timeout" in str(error).lower(), error
    assert advisory_lock_keys([(error.statement, error.params)]) == [
        owner_lock_key(owner_id),
    ], error.statement
