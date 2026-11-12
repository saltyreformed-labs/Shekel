"""The frozen clock: ``freeze_today``, and the database clock it freezes with it, which stamps one
strictly increasing instant on every clock-defaulted write.
"""

import re
import sys
import weakref

from contextlib import contextmanager
from datetime import (
    date as _real_date,
    datetime as _real_datetime,
    timedelta as _real_timedelta,
    timezone as _real_timezone,
)


def freeze_today(monkeypatch, target_date, modules=None, at_time=None):
    """Patch ``date.today()`` and ``datetime.now()`` to ``target_date``.

    Production services import ``date`` (and sometimes ``datetime``) at
    module load time (e.g. ``from datetime import date``), so
    monkeypatching ``datetime.date`` globally does not affect them.
    Each module that uses ``date.today()`` or ``datetime.now()`` must
    be patched individually.  This helper hides that boilerplate.

    Patches BOTH ``date`` and ``datetime`` so that test helpers using
    ``datetime.now() - timedelta(days=N)`` align with production code
    using ``date.today()``.  ``datetime.now()`` returns NOON of
    ``target_date`` (timezone-aware when ``tz`` is passed).

    **Noon, not midnight, and the DATABASE half of this freeze has always
    said so.**  :func:`_freeze_db_clock` pins PostgreSQL's clock with
    :func:`settle_instant_on` -- noon UTC -- precisely "so the frozen date the
    test reads from ``date.today()`` and the date the app RENDERS for these
    rows are the same date".  This half used MIDNIGHT UTC, which is the
    previous EVENING in ``America/New_York``: under ruling R-DH (b) every
    display-timezone reader (``app.utils.dates.display_today``, the analytics
    tax year, the loan YTD chips, and since plan step 2 the day an anchor
    assertion is filed under) then answered ``target_date - 1``.  Two halves of
    one freeze, twelve hours and one civil date apart, with the rule stated
    correctly in one of them.  It is finding N-132's shape a sixth time --
    midnight UTC used to MEAN a civil day -- and it is fixed here rather than
    worked around at each reader.

    **Both halves now take the SAME instant, ``at_time`` included** (plan step
    X-f1b).  The database half derived its own noon from ``target_date`` until
    then, so a caller that named an EVENING instant -- the only kind that can
    tell a display-timezone rule apart from a UTC one, which is what finding
    **N-182** needed -- re-opened the same split the paragraph above closed, in
    the direction nothing had exercised.  Nothing moves at the default: noon UTC
    on ``target_date`` is exactly what the database half computed for itself.

    Args:
        monkeypatch:
            pytest's ``monkeypatch`` fixture.  Required so the patch is
            torn down at end of test automatically.
        target_date:
            The ``datetime.date`` instance that ``date.today()`` should
            return inside the patched modules.
        modules:
            Iterable of dotted module paths whose ``date`` and
            ``datetime`` symbols to replace.  When omitted, every
            loaded module that has a top-level ``date`` or ``datetime``
            symbol bound to the real class is patched -- this covers
            production services (``app.services.*``), test modules
            that imported ``date`` inline, and the modules
            ``tests/conftest.py`` registers its fixtures from that
            import ``date``, ensuring all layers see the same frozen
            "today".
            Pass an explicit tuple to patch only specific modules.
        at_time:
            Optional wall time on ``target_date`` for the frozen ``now``,
            overriding the noon default.  For a test that means a specific
            INSTANT rather than a day -- the New Year boundary cases, where
            ``time.min`` (midnight UTC) is deliberately the previous EVENING in
            the display timezone.  Saying it beats relying on the default: the
            boundary tests read midnight out of this helper as an undocumented
            implementation detail, and one of their own siblings already hand-
            rolled a stub because the helper offered no way to ask.
    """
    # Custom metaclass so ``isinstance(real_date_obj, _FrozenDate)``
    # returns True.  Without this, production code that does
    # ``isinstance(start_date, date)`` -- where ``date`` has been
    # replaced by ``_FrozenDate`` -- rejects real ``datetime.date``
    # instances and raises spurious ValidationError.
    class _DateMeta(type(_real_date)):
        """Metaclass that treats real dates as _FrozenDate instances."""

        def __instancecheck__(cls, instance):
            """Real ``datetime.date`` objects pass ``isinstance`` checks."""
            return isinstance(instance, _real_date)

    class _FrozenDate(_real_date, metaclass=_DateMeta):
        """Date subclass with a fixed ``today()`` for test isolation."""

        @classmethod
        def today(cls):
            """Return the frozen date instead of the wall-clock date."""
            return target_date

    # Noon by default, so this half agrees with ``_freeze_db_clock``'s -- see
    # the docstring.  Naive here because ``now(tz=None)`` must stay naive; the
    # ``tz``-aware branch below stamps it without shifting the wall time, which
    # is the same instant ``settle_instant_on`` builds for a UTC caller.
    target_datetime = _real_datetime.combine(
        target_date,
        at_time if at_time is not None
        else _real_datetime.min.time().replace(hour=12),
    )

    class _DateTimeMeta(type(_real_datetime)):
        """Metaclass that treats real datetimes as _FrozenDateTime instances."""

        def __instancecheck__(cls, instance):
            return isinstance(instance, _real_datetime)

    class _FrozenDateTime(_real_datetime, metaclass=_DateTimeMeta):
        """Datetime subclass with a fixed ``now()`` aligned to target_date."""

        @classmethod
        def now(cls, tz=None):
            """Return noon of target_date (with tz if provided)."""
            if tz is None:
                return target_datetime
            return target_datetime.replace(tzinfo=tz)

        @classmethod
        def utcnow(cls):
            """Return noon UTC of target_date (naive, like real utcnow)."""
            return target_datetime

        @classmethod
        def today(cls):
            """Return noon of target_date."""
            return target_datetime

    date_modules = None
    datetime_modules = None

    if modules is None:
        # Auto-discover every loaded module whose top-level ``date`` or
        # ``datetime`` symbol is the real class OR a previous frozen
        # subclass left over from an earlier ``freeze_today``
        # invocation.  Including the subclass case lets a later
        # ``freeze_today`` call (e.g. a file-level autouse) override an
        # earlier one (e.g. a conftest-level soak fixture) without
        # leaving any module holding the stale frozen value.
        date_modules = []
        datetime_modules = []
        for module_name, module in list(sys.modules.items()):
            if module is None:
                continue
            try:
                mod_date = getattr(module, "date", None)
            except (ImportError, AttributeError):
                mod_date = None
            try:
                mod_dt = getattr(module, "datetime", None)
            except (ImportError, AttributeError):
                mod_dt = None
            if mod_date is _real_date or (
                isinstance(mod_date, type)
                and mod_date is not _real_date
                and issubclass(mod_date, _real_date)
            ):
                date_modules.append(module_name)
            if mod_dt is _real_datetime or (
                isinstance(mod_dt, type)
                and mod_dt is not _real_datetime
                and issubclass(mod_dt, _real_datetime)
            ):
                datetime_modules.append(module_name)
    else:
        # Caller passed an explicit module list -- patch both date and
        # datetime in each so callers don't need to maintain two lists.
        date_modules = list(modules)
        datetime_modules = list(modules)

    for module_path in date_modules:
        try:
            monkeypatch.setattr(f"{module_path}.date", _FrozenDate)
        except (AttributeError, TypeError):
            # Module may have been unloaded or the attribute may have
            # changed shape since enumeration.  Best-effort patching.
            pass

    for module_path in datetime_modules:
        try:
            monkeypatch.setattr(f"{module_path}.datetime", _FrozenDateTime)
        except (AttributeError, TypeError):
            pass

    # BOTH halves of the freeze take the SAME instant, including when the caller
    # named one with ``at_time``.  Handing this half ``target_date`` (and letting
    # it re-derive noon) is the exact defect the docstring above records: two
    # halves of one freeze, hours and a civil date apart.  With the default
    # ``at_time`` this value IS ``settle_instant_on(target_date)``, so nothing
    # moves for a test that does not ask for an instant.
    _freeze_db_clock(
        monkeypatch, target_datetime.replace(tzinfo=_real_timezone.utc),
    )


# ── the DATABASE half of the frozen clock (finding N-65) ──────────────

#: The frozen instant every database-clock value is issued from, or ``None``
#: when no test has frozen its clock.  Set through ``monkeypatch`` by
#: :func:`freeze_today`, so it reverts with the test that set it, and read by
#: the two listeners below -- which are therefore inert outside a frozen test
#: and never need removing.
_FROZEN_DB_CLOCK = None


#: Monotonic counter for :meth:`_FrozenDbClock.stamp`, held at MODULE scope so
#: it survives a re-freeze.  It used to live on the clock instance, and
#: ``freeze_today`` builds a new instance every call -- measured across the
#: suite: 133 re-freezes, 27 of them after stamps had already been issued.  No
#: two of those re-froze to the same civil date, so nothing tied; a test that
#: did would have produced exactly the ``ORDER BY created_at DESC`` coin flip
#: the microsecond step exists to prevent.
_DB_CLOCK_ISSUED = 0


#: How many :func:`same_instant_writes` blocks are open.  While it is non-zero
#: :meth:`_FrozenDbClock.stamp` stops advancing, so every clock-defaulted write
#: inside shares ONE instant -- the shape a real transaction produces, which the
#: microsecond step above otherwise makes unreachable (ledger row **N-209**).
#: A DEPTH rather than a flag because the blocks nest.
_DB_CLOCK_TIE_DEPTH = 0


#: ``mapper class -> (attribute name, stamp kind)`` for every column whose
#: INSERT value comes from the database clock.  DERIVED from the mapped columns
#: rather than listed.  Populated lazily and never invalidated: a model's
#: column defaults do not change at runtime.
_DB_CLOCK_INSERT_ATTRS = {}


#: Guard so the ``Session`` listener is attached exactly once per process.
_DB_CLOCK_LISTENERS_INSTALLED = False


#: Engines the statement rewriter is bound to.  Weak, so a discarded engine is
#: not pinned in memory.
_DB_CLOCK_BOUND_ENGINES = weakref.WeakSet()


#: A statement asking PostgreSQL what time it is, in each spelling this schema
#: can produce, so the rule tests the QUESTION and not one way of writing it.
#: ``now()`` and ``current_timestamp`` yield an instant; ``current_date`` yields
#: a DATE and must be answered with one (``transaction_entries.purchased_on``).
#:
#: Each alternative carries only the word boundary it can actually have.  The
#: first draft wrapped the group in ``\b...\b`` and matched nothing at all: the
#: group's last character is ``)``, and the next character in real SQL is ``,``
#: or a space -- two non-word characters, so the trailing ``\b`` can never
#: assert.  It was a rule that could not fire, and it was caught only because
#: it was MADE to fire on demand rather than reasoned about.
_DB_CLOCK_CALL_RX = re.compile(
    r"\bnow\s*\(\s*\)|\bcurrent_timestamp\b|\bcurrent_date\b", re.IGNORECASE,
)


#: The same question as :data:`_DB_CLOCK_CALL_RX`, asked of a ``server_default``
#: written as raw SQL text rather than as a SQLAlchemy function.
#: ``transaction_entries.purchased_on`` is ``db.text("CURRENT_DATE")``, which is a
#: ``TextClause`` and therefore invisible to an ``isinstance(..., now)`` test --
#: the gap this arm closes (found by plan step X-h's adversarial review, which
#: caught the row landing on the real wall date while its siblings were frozen).
_DB_CLOCK_TEXT_DEFAULT_RX = _DB_CLOCK_CALL_RX


#: Statement kinds the rewriter leaves alone.  DDL legitimately mentions
#: ``now()`` -- it is how the NOT NULL defaults are DECLARED -- and rewriting a
#: ``CREATE TABLE`` would bake a frozen instant into the SCHEMA.  ``DO`` is here
#: because this app's audit and posting infrastructure ships ``DO $$ ... $$``
#: blocks (``audit_infrastructure.py``, ``posting_infrastructure.py``).
_DB_CLOCK_EXEMPT_VERBS = frozenset(
    {"CREATE", "ALTER", "DROP", "COMMENT", "SET", "GRANT", "REVOKE", "TRUNCATE",
     "DO"},
)


class _FrozenDbClock:
    """The frozen instant, issued strictly increasing so write ORDER survives.

    A flat instant would give every row in a test the identical timestamp, and
    the app resolves an account's current anchor by ``ORDER BY created_at
    DESC`` -- ties there are broken arbitrarily by PostgreSQL, which would turn
    a deterministic fixture into a coin flip.  Each stamp is therefore one
    microsecond past the last, which preserves the order rows were written in
    while leaving every one of them on the frozen civil DATE.

    The counter is :data:`_DB_CLOCK_ISSUED`, at module scope, so a re-freeze
    inside one test cannot reissue an instant this process has already used.
    """

    def __init__(self, instant):
        """Store the base instant every stamp is measured from."""
        self._instant = instant

    def stamp(self):
        """Return the next instant: the frozen one, plus one microsecond.

        **Unless a :func:`same_instant_writes` block is open**, in which case
        every stamp inside it is the SAME instant -- see that function for the
        production state this exists to reproduce (ledger row N-209).
        """
        global _DB_CLOCK_ISSUED  # pylint: disable=global-statement
        if _DB_CLOCK_TIE_DEPTH:
            return self._instant + _real_timedelta(
                microseconds=_DB_CLOCK_ISSUED,
            )
        _DB_CLOCK_ISSUED += 1
        return self._instant + _real_timedelta(microseconds=_DB_CLOCK_ISSUED)


@contextmanager
def same_instant_writes():
    """Make every clock-defaulted write inside the block share ONE instant.

    **The state production produces routinely and the suite could not build**
    (ledger row **N-209**).  PostgreSQL's ``now()`` is TRANSACTION START, so
    every row a backfill or a multi-row service call writes in one transaction
    carries the identical ``created_at``: ``shekel-prod-db`` holds four such
    rows (ids 1-4, all ``2026-05-22 02:41:22.187019+00``).  The frozen clock
    issues each stamp one microsecond past the last -- deliberately, so an
    ``ORDER BY created_at DESC`` tie is not a coin flip and a fixture stays
    deterministic -- and that determinism also made the whole tie-break class
    UNREACHABLE from a test.  Finding **N-196** could not have been found by
    the suite, and was not: it was found by reading.

    So the tie gets a door rather than each test setting ``created_at`` by
    hand, which is what plan step X-an-b had to do.  Determinism stays the
    DEFAULT everywhere outside the block: a test that does not open one cannot
    accidentally produce a tie.

    Nests, because a helper that opens one may be called from a test that has
    already opened one, and the inner exit must not re-arm the counter early.

    Yields:
        ``None``; the block is the whole interface.
    """
    global _DB_CLOCK_TIE_DEPTH  # pylint: disable=global-statement
    _DB_CLOCK_TIE_DEPTH += 1
    try:
        yield
    finally:
        _DB_CLOCK_TIE_DEPTH -= 1


def _db_clock_insert_attrs(model_class):
    """Return ``(attribute, is_date)`` for every clock-defaulted column.

    DERIVED from SQLAlchemy's own mapper rather than listed by hand, which is
    the whole point: the app has 61 columns whose INSERT value comes from a
    ``NOW()`` server default plus one from a ``CURRENT_DATE`` text default
    (measured 2026-07-28), and a hand-written list of 62 entries is a copy that
    goes stale the first time someone adds a model.  A bundle hand-synchronised
    with the thing it mirrors is the same defect plan step X-r deleted from
    ``_projections``.

    **Both spellings of a default are read**, because the app uses both: a
    SQLAlchemy ``now()`` function, and raw SQL text (``db.text("CURRENT_DATE")``
    on ``transaction_entries.purchased_on``).  An ``isinstance(..., now)`` test
    alone is blind to the second, which is how that column kept landing on the
    real wall date while every timestamp beside it was frozen.

    ``is_date`` carries the column's TYPE, because a ``DATE`` column must be
    answered with a date: handing it an instant is a different value, not a
    formatting detail.

    Args:
        model_class: A mapped model class taken off a session's unit of work.

    Returns:
        A tuple of ``(attribute name, is_date)`` pairs PostgreSQL would fill if
        the INSERT omitted them.
    """
    cached = _DB_CLOCK_INSERT_ATTRS.get(model_class)
    if cached is not None:
        return cached
    # Pylint: ``import-outside-toplevel`` -- this module imports no app or ORM
    # symbols at top level (its collection-time-safety convention).
    # pylint: disable=import-outside-toplevel
    from sqlalchemy import Column, Date, DateTime, inspect as sa_inspect
    from sqlalchemy.sql.elements import TextClause
    from sqlalchemy.sql.functions import now as sa_now

    resolved = []
    for prop in sa_inspect(model_class).column_attrs:
        column = prop.columns[0]
        # A ``column_property`` is a column attribute over an EXPRESSION, not
        # a table column -- ``BankStatementLine``'s two merchant projections
        # (plan step ``bank_import:X-f6b-1b``) are scalar subqueries -- and
        # an INSERT omits nothing for it, so there is no default to read.
        if not isinstance(column, Column):
            continue
        default = column.server_default
        if default is None:
            continue
        arg = getattr(default, "arg", None)
        is_clock = isinstance(arg, sa_now) or (
            isinstance(arg, TextClause)
            and _DB_CLOCK_TEXT_DEFAULT_RX.search(str(arg)) is not None
        )
        if not is_clock:
            continue
        is_date = isinstance(column.type, Date) and not isinstance(
            column.type, DateTime,
        )
        resolved.append((prop.key, is_date))
    resolved = tuple(resolved)
    _DB_CLOCK_INSERT_ATTRS[model_class] = resolved
    return resolved


def _stamp_omitted_db_defaults(session, _flush_context, _instances):
    """Fill the clock-defaulted columns an INSERT would otherwise omit.

    The first of finding N-65's two mechanisms, and the one the statement
    rewriter below CANNOT cover: a column left unset is simply absent from the
    INSERT, so PostgreSQL applies its ``DEFAULT NOW()`` (or ``DEFAULT
    CURRENT_DATE``) and the clock call never appears in any SQL text to
    rewrite.  ``AccountAnchorHistory.created_at`` -- the column N-65 names --
    is exactly this shape.

    Every clock column of one row takes the SAME stamp, so ``created_at ==
    updated_at`` on insert, exactly as the database would have written them.

    Args:
        session: The flushing :class:`~sqlalchemy.orm.Session`.
        _flush_context: SQLAlchemy's internal flush context (unused).
        _instances: The deprecated instances argument (unused).
    """
    if _FROZEN_DB_CLOCK is None:
        return
    for obj in list(session.new):
        attrs = _db_clock_insert_attrs(type(obj))
        if not attrs:
            continue
        stamp = _FROZEN_DB_CLOCK.stamp()
        for attr, is_date in attrs:
            # ``__dict__`` rather than ``getattr``: an unset column is simply
            # absent from it, and reading through the instrumented attribute
            # would emit a load for a value that does not exist yet.
            if obj.__dict__.get(attr) is None:
                setattr(obj, attr, stamp.date() if is_date else stamp)


def _db_clock_literal(match, stamp):
    """Return the SQL literal that answers one matched clock call.

    A ``current_date`` call yields a DATE and is answered with one; ``now()``
    and ``current_timestamp`` yield an instant.  Answering a date question with
    a timestamp would change the value's TYPE in the statement, which is a
    different defect from the one being fixed.

    Args:
        match: The regex match for the clock call.
        stamp: The frozen instant to answer with.

    Returns:
        A PostgreSQL literal, typed to match the call it replaces.
    """
    if match.group(0).lower().startswith("current_date"):
        return f"DATE '{stamp.date().isoformat()}'"
    return f"TIMESTAMPTZ '{stamp.isoformat()}'"


def _rewrite_db_clock_calls(
    _conn, _cursor, statement, parameters, _context, _executemany,
):
    """Answer every rendered clock call with the frozen instant.

    The second of finding N-65's two mechanisms, and it is one rule covering
    what would otherwise be several.  A clock call reaches the database more
    than one way, and only some of them are column defaults:

    * a modified row's ``onupdate=NOW()`` columns render ``updated_at=now()``
      into the UPDATE;
    * a BULK update (``carry_forward_service``'s ``query.update(...)``) renders
      the same thing while bypassing the ORM unit of work entirely, so no
      session-level listener can reach it.

    All three render the call into the SQL, so rewriting the SQL is the ONE
    place that answers all three.  An earlier draft stamped the first two on
    the mapped objects instead and left the third: the full suite reported 41
    failures, every one of them a bulk UPDATE, which is how the third was found
    rather than reasoned about.

    Every occurrence in one statement takes the same instant, so a row's
    ``created_at`` and ``updated_at`` do not disagree by a microsecond.

    DDL is exempt: DECLARING a ``DEFAULT now()`` column is not asking the time,
    and rewriting a ``CREATE TABLE`` would bake a frozen instant into the
    SCHEMA.  Bound parameters are untouched -- the rewrite is on the statement
    TEXT, and a value that happens to read ``now()`` travels as a parameter,
    never as SQL.

    Args:
        _conn: The DBAPI connection wrapper (unused).
        _cursor: The DBAPI cursor (unused).
        statement: The SQL about to be executed.
        parameters: Bound parameters, returned unchanged.
        _context: The execution context (unused).
        _executemany: Whether this is an executemany (unused).

    Returns:
        ``(statement, parameters)`` -- the statement with every clock call
        replaced by a typed literal, or the original pair when there is nothing
        to rewrite.
    """
    if _FROZEN_DB_CLOCK is None:
        return statement, parameters
    verb = statement.split(None, 1)
    if verb and verb[0].upper() in _DB_CLOCK_EXEMPT_VERBS:
        return statement, parameters
    if not _DB_CLOCK_CALL_RX.search(statement):
        return statement, parameters
    stamp = _FROZEN_DB_CLOCK.stamp()
    return (
        _DB_CLOCK_CALL_RX.sub(
            lambda match: _db_clock_literal(match, stamp), statement,
        ),
        parameters,
    )


def bind_db_clock_rewriter(engine):
    """Attach the statement rewriter to *engine*, once per engine.

    **Called from the session-scoped ``setup_database`` fixture, before any
    test runs**, and that timing is the whole point.  It used to be called
    lazily from the flush listener, which made the rewriter's INSTALLATION
    depend on some earlier ORM flush having happened under a frozen clock in
    the same worker process -- so a frozen test whose only writes were bulk
    ``query.update(...)`` never bound it and silently got the real wall clock.
    Measured by plan step X-h's adversarial review, same test, same assertion:

        fresh process                     -> updated_at 2026-07-28 (WRONG)
        after another test had flushed     -> updated_at 2026-03-20 (right)

    A fail-OPEN gate whose result depends on test ORDER, which is the exact
    class the sibling commit deleted from the checker suite (finding N-45).
    Binding eagerly removes the dependency: the listener is always attached and
    reads the frozen clock at call time, so it is inert until a test freezes.

    A class-level ``Engine`` listener is NOT an alternative: it does not reach
    an engine that already exists -- measured, ``event.contains(Engine, ...)``
    reported True while the live engine's own dispatch did not hold the
    listener and it never ran.

    Args:
        engine: The :class:`~sqlalchemy.engine.Engine` the suite writes through.
    """
    # Pylint: ``import-outside-toplevel`` -- see :func:`_db_clock_insert_attrs`.
    # pylint: disable=import-outside-toplevel
    from sqlalchemy import event

    if engine in _DB_CLOCK_BOUND_ENGINES:
        return
    event.listen(
        engine, "before_cursor_execute", _rewrite_db_clock_calls, retval=True,
    )
    _DB_CLOCK_BOUND_ENGINES.add(engine)


def _freeze_db_clock(monkeypatch, frozen_instant):
    """Make the DATABASE's clock agree with the clock the test just froze.

    The structural half of finding N-65 (balance plan step X-h).  Freezing
    ``date.today()`` alone leaves PostgreSQL's clock untouched, and the
    database answers it in several places: 61 columns take their INSERT value
    from a ``NOW()`` server default, one from a ``CURRENT_DATE`` text default,
    and 23 of them re-stamp on UPDATE.  So a fixture that settled a row "now"
    stamped it months outside the pay periods the test seeded, and the balance
    fold -- which dates every event -- replayed it outside the window entirely.
    Nothing noticed while the shipping producers read the LATEST anchor row and
    ignored its date; the fold made the instant load-bearing.

    **One of the FOUR reaches this was written for is gone, closed
    structurally rather than contained** (plan step X-f1, ruling R-EC).
    ``status_seam`` assigned ``db.func.now()`` to ``Transaction.settled_on``
    outright -- the one reach that was not a schema derivation, so nothing but
    this rewriter could see it.  The seam stamps ``display_today()`` into a
    ``DATE`` column now, which is a Python value the ``date.today()`` freeze
    already governs.  The rewriter stays for the other reaches, which are
    schema-level and not going anywhere.

    Two mechanisms cover it, and each covers what the other structurally
    cannot: :func:`_stamp_omitted_db_defaults` for a default that never appears
    in any SQL, and :func:`_rewrite_db_clock_calls` for every call that does --
    including the bulk updates no session listener can see.  The rewriter is
    bound eagerly by the ``setup_database`` fixture
    (:func:`bind_db_clock_rewriter`), never lazily, so neither mechanism
    depends on the other having run.

    The instant is the one :func:`freeze_today` froze ``datetime.now()`` to, so
    the two halves cannot disagree about WHEN "now" is.  By default that is noon
    UTC -- :func:`settle_instant_on`'s value, this suite's existing primitive for
    "a deterministic instant on this civil day", the same civil day in the
    display timezone, so the frozen date the test reads from ``date.today()`` and
    the date the app RENDERS for these rows are the same date.  A caller that
    names an instant with ``at_time`` gets THAT instant here too: an
    evening-Eastern freeze (the only kind that can tell a display-timezone rule
    apart from a UTC one) would otherwise leave the database eleven hours and one
    civil date away from the app, which is the split this docstring's sibling
    paragraph in :func:`freeze_today` was written about.

    Production is unchanged, and deliberately so.  The database is the right
    authority for a production timestamp -- two app workers with skewed clocks
    would stamp inconsistent instants -- so the fix belongs in the harness, not
    in the models.

    Stated boundary: a timestamp written by a database TRIGGER (the
    ``system.audit_log`` rows) still comes from the real clock, because the
    trigger's own clock call is never rendered into a statement this sees.  No
    balance producer reads those rows, and freezing them would take a
    ``search_path`` override that fails OPEN the moment anything resets the
    path.

    Args:
        monkeypatch: pytest's ``monkeypatch`` fixture, so the frozen clock
            reverts with the test that set it.
        frozen_instant: The aware-UTC instant :func:`freeze_today` froze
            ``datetime.now()`` to -- noon UTC on the target date unless the
            caller named another with ``at_time``.
    """
    global _DB_CLOCK_LISTENERS_INSTALLED  # pylint: disable=global-statement
    # Pylint: ``import-outside-toplevel`` -- see :func:`_db_clock_insert_attrs`.
    # pylint: disable=import-outside-toplevel
    from sqlalchemy import event
    from sqlalchemy.orm import Session

    if not _DB_CLOCK_LISTENERS_INSTALLED:
        event.listen(Session, "before_flush", _stamp_omitted_db_defaults)
        _DB_CLOCK_LISTENERS_INSTALLED = True
    monkeypatch.setattr(
        f"{__name__}._FROZEN_DB_CLOCK",
        _FrozenDbClock(frozen_instant),
    )
