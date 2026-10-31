"""
Shekel Budget App -- shared test helper utilities.

Underscore-prefixed module name keeps pytest from collecting it as a
test file.  Import functions from here in test modules that need them.
"""

import importlib.util
import os
import pathlib
import re
import sys
import weakref

from collections import namedtuple
from contextlib import contextmanager
from datetime import (
    date as _real_date,
    datetime as _real_datetime,
    timedelta as _real_timedelta,
    timezone as _real_timezone,
)
from decimal import Decimal
from html.parser import HTMLParser

import pytest
from sqlalchemy import exc as sa_exc

from app.enums import BusinessDayShiftEnum, FilingStatusEnum, TaxTypeEnum
from app.models.amount_ownership import AmountOwnership
from app.models.user import MfaConfig
from app.services import (
    mfa_service,
    pay_era_write,
    pay_rhythm,
    pay_schedule_service,
)
from app.tax_law import (
    FederalRules,
    FicaRules,
    StateYearLaw,
    TaxLaw,
    TaxYearLaw,
    ladder,
)
from app.utils.db_errors import sqlstate_of


#: The ``app`` package's directory, read off the package's own import origin,
#: for a test that reads a file under ``app/`` (a template, a static asset, a
#: route module).  A read under ``app/`` never counts up from a test file's own
#: ``__file__``, this module's included: a test module moves (a split puts each
#: part one directory deeper), and a path counted from where it sat then names
#: another directory.  This module's two reads OUTSIDE ``app/`` (``migrations/``
#: and ``scripts/``, below) still count up from it, and wait for its own split.
APP_PACKAGE_DIR = pathlib.Path(importlib.util.find_spec("app").origin).parent


# The synthetic split-loan fixture shared verbatim by the three parallel
# loan-posting suites (unit / reconciliation-oracle / wiring): a $250,000 loan
# originated 2025-01-01 at 6%, trued up to $100,000 on 2026-01-10.  The trueup
# balance deliberately differs from origination so a correct interest figure
# proves the walk's anchor reset (a payment before the trueup accrues on
# $250,000, one after on $100,000).  ``p1`` / ``p2`` / ``p3`` are the
# ``seed_periods`` indices whose monthly due dates (payment_day=1) land in
# distinct months after the anchor (02-01 / 03-01 / 04-01).  Single-sourced here
# so a fixture change touches one place, not three; each suite unpacks it into
# its own module-level names.
_SplitLoan = namedtuple(
    "_SplitLoan",
    "origination_principal origination_date rate anchor_balance anchor_date "
    "p1 p2 p3",
)
SPLIT_LOAN = _SplitLoan(
    origination_principal=Decimal("250000.00"),
    origination_date=_real_date(2025, 1, 1),
    rate=Decimal("0.06000"),
    anchor_balance=Decimal("100000.00"),
    anchor_date=_real_date(2026, 1, 10),
    p1=1, p2=3, p3=5,
)


def select_option_values(html: str, select_key: str) -> list[str]:
    """Return the ``value`` attributes of every ``<option>`` inside a named ``<select>``.

    Locates the ``<select>`` element identified by ``select_key``
    (matched against either its ``id`` or its ``name`` attribute --
    Shekel's templates are inconsistent: some forms set only
    ``name=`` while others set both ``id=`` and ``name=``) and
    returns the ``value="..."`` of each ``<option>`` child it
    contains, in document order.  Returns an empty list when no
    matching select is present or it carries no option children with
    value attributes.

    Use this helper to assert dropdown contents without falsely
    matching ``value="N"`` attributes from unrelated elements
    elsewhere in the page (transaction-type IDs, pay-period IDs,
    hardcoded month numbers, recurrence-pattern IDs, etc.).  A naive
    ``f'value="{model.id}" not in html`` check fails when ``model.id``
    happens to collide with any of those siblings -- a deterministic
    bug masquerading as a flake until the colliding sequence values
    align.

    Args:
        html: The full HTML response body to search.
        select_key: The ``id`` or ``name`` attribute of the
            ``<select>`` element to scope the search to.
            Case-sensitive, matched against the literal attribute
            value.

    Returns:
        Ordered list of ``value`` strings from the named select's
        ``<option>`` children.  Empty list when the select is not
        present in ``html``.  Returned values are the raw attribute
        strings (e.g. ``"2"`` not ``2``) so callers compare against
        ``str(model.id)`` rather than the int.
    """
    select_block = re.search(
        r'<select[^>]*\b(?:id|name)="'
        + re.escape(select_key)
        + r'"[^>]*>(.*?)</select>',
        html,
        re.DOTALL,
    )
    if select_block is None:
        return []
    return re.findall(
        r'<option\b[^>]*\bvalue="([^"]*)"',
        select_block.group(1),
    )


def field_is_disabled(html: str, field_name: str) -> bool:
    """Return True if the ``<input>``/``<select>`` named ``field_name`` is disabled.

    Slices from the ``name="<field_name>"`` attribute to the end of that
    opening tag (the next ``>``) and reports whether the ``disabled``
    attribute appears there.  The grid edit popovers append ``disabled``
    after ``name=`` on a finalised row's locked money / period / category
    / due-date fields (#26), so this distinguishes a locked field from the
    still-editable Status dropdown and Notes input in the same form.

    Args:
        html: The full HTML response body to search.
        field_name: The ``name`` attribute of the input/select to inspect.

    Returns:
        True when the named field's opening tag carries ``disabled``.

    Raises:
        AssertionError: The field is absent, so a typo'd name fails loud
            rather than silently reporting an editable field as locked.
    """
    marker = f'name="{field_name}"'
    idx = html.find(marker)
    assert idx != -1, f"{marker} not found in rendered HTML"
    tag_end = html.find(">", idx)
    return "disabled" in html[idx:tag_end]


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
            that imported ``date`` inline, and ``tests.conftest``
            itself, ensuring all layers see the same frozen "today".
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


def insert_origination_rate(loan_params, interest_rate):
    """Append the origination :class:`RateHistory` row for a loan.

    Mirrors the production-code pattern in
    :func:`app.routes.loan.create_params` (DH-#56): the loan's base /
    period-0 rate lives in the :class:`RateHistory` row effective at
    origination, not the retired ``LoanParams.interest_rate`` column.
    The resolver raises ``ValueError`` when a loan's rate-change feed is
    empty (no origination row), so every fixture that builds
    :class:`LoanParams` directly and then resolves it (loan dashboard,
    debt strategy, /savings debt card, year-end liability, contractual
    P&I) MUST call this helper after inserting :class:`LoanParams`.

    Args:
        loan_params: The :class:`LoanParams` ORM instance, already
            flushed (``loan_params.account_id`` populated).
        interest_rate: The origination annual rate as a Decimal fraction
            (e.g. ``Decimal("0.06875")`` for 6.875%).

    Returns:
        The newly added :class:`RateHistory` instance,
        ``db.session.add()``'d but not committed.  The caller's existing
        ``db.session.commit()`` carries the row into the same transaction.
    """
    # pylint: disable=import-outside-toplevel  -- avoid module-load
    # circular deps via models package; tests/_test_helpers loads
    # early enough that an unconditional top-level import would
    # snowball into ref_cache / Flask app bootstrapping.
    from app.extensions import db
    from app.models.loan_features import RateHistory

    row = RateHistory(
        account_id=loan_params.account_id,
        effective_date=loan_params.origination_date,
        interest_rate=interest_rate,
        monthly_pi=None,
    )
    db.session.add(row)
    return row


def _sync_loan_ledger(loan_account_id):
    """Reconcile a loan's genesis ledger, exactly as every production writer does.

    The shared write-through step of the three loan fixtures below
    (:func:`create_loan_account`, :func:`insert_trueup_event`,
    :func:`insert_tracking_start_event`).  Every production path that creates or
    re-bases a loan's anchors reconciles the genesis postings in the SAME
    transaction as the source row:

    * ``loan.create_params`` / ``loan.update_params`` call
      :func:`~app.services.loan_posting_service.sync_loan_postings_all_scenarios`
      (``app/routes/loan/params.py:125`` and ``:177``).
    * The balance true-up and the tracking-start opening both route through
      :func:`app.services.loan_anchor_service._append_loan_anchor_and_sync`, which
      appends the event, re-syncs, and commits (``anchor_service.py:390``).

    The property that matters, and the one this helper reproduces, is that the
    event and its postings land in the SAME transaction -- never an event alone.
    This helper flushes rather than commits, keeping the existing fixture contract
    (the caller owns the transaction and commits with the rest of its setup).

    Without this, a fixture-built loan carries anchor rows but NO opening
    posting -- a state production cannot produce, and the one every loan built
    through these helpers used to be in (so it exercised the no-ledger fallback
    while production always took the ledger path).

    **Scope, stated plainly so nobody over-trusts it.**  This reaches only loans
    built through :func:`create_loan_account` / :func:`create_loan_with_trueup` /
    :func:`insert_trueup_event` / :func:`insert_tracking_start_event`.  Roughly
    twenty test modules still hand-roll a loan (a bare ``LoanParams(...)`` insert;
    e.g. ``tests/test_services/test_balance_at/_helpers.py::_make_mortgage``, a near-copy of
    :func:`create_loan_account`).  Such a loan gets a ledger only if one of the
    anchor helpers above happens to be called on it afterwards, and otherwise has
    none at all -- ``test_year_end_summary_service.py``'s hand-built loan is in
    exactly that state.  That coupling is invisible at the call site and is a real
    trap; routing the hand-rolled builders through this factory is a follow-up
    (and a prerequisite for the fail-loud read seam, which turns a ledger-less loan
    into a raise).

    Deliberately calls the PLAIN sync rather than production's
    :func:`~app.services.loan_posting_service.sync_all_scenarios_or_duplicate`:
    the duplicate-translating wrapper exists to turn a user's double-click into
    idempotent success, and it does so by ROLLING BACK.  In a fixture that would
    silently discard the test's setup; a duplicate anchor written by a fixture is
    a bug in the fixture and must fail loud.

    Flushes but does NOT commit -- the caller owns the transaction boundary, the
    same contract the production chokepoints keep.

    **No future-anchor guard, because there is nothing left to guard.**  This
    helper used to assert that no user-asserted anchor was dated after the sync's
    as-of, because the walk DROPPED such an anchor and left the loan half-opened
    (opening present, true-up missing) -- a state that looked ledger-backed and
    was not.  The walk no longer reads a clock: it records every anchor the loan
    carries, whatever its date, and the readers decide what has happened
    (``loan_ledger.walk_loan_ledger``).  So a fixture's anchor
    posts whatever its date, and a fixture can no longer build the half-opened
    loan by accident.

    Args:
        loan_account_id: The loan whose genesis ledger to reconcile.
    """
    # Pylint: ``import-outside-toplevel`` -- same circular-dep avoidance as every
    # other helper in this module: the services package must not load at
    # tests/_test_helpers import time.
    # pylint: disable=import-outside-toplevel
    from app.extensions import db
    from app.services import loan_posting_service

    loan_posting_service.sync_loan_postings_all_scenarios(loan_account_id)
    db.session.flush()


def clear_loan_ledger(loan_account_id):
    """Delete a loan's genesis postings -- the BROKEN state production cannot make.

    The exact inverse of :func:`_sync_loan_ledger`: removes the journal entries
    the loan sync produces (``loan_opening``, ``loan_trueup``, and the
    ``loan_payment`` split corrections) on any of the loan's own ledger accounts,
    leaving the Step-2/3 CASH entries untouched.  The posting ledger is
    append-only (the ORM blocks deletes on ``budget.journal_entries`` /
    ``budget.account_postings``), so it clears them via raw SQL.

    A configured loan with no opening posting is NOT a legitimate state: the
    opening is written in the same transaction as the ``LoanParams``
    (``app/routes/loan/params.py:125``), the Step-4 migration backfilled every
    pre-existing loan, and ``pay_period_admin.reset_pay_periods`` re-syncs.  This
    helper exists so the handful of tests that pin behaviour ON that broken state
    -- the readers' ``None`` contract, and the seam's fail-loud raise -- can
    construct it EXPLICITLY and say so at the call site.

    It is deliberately the only way to build a ledger-less loan THROUGH THESE
    HELPERS (a hand-rolled ``LoanParams`` insert still yields one by omission --
    see :func:`_sync_loan_ledger`).  A boolean ``open_ledger=False`` knob on
    :func:`create_loan_account` would leave a casual escape hatch back onto the
    fallback path this arc exists to delete; a call to something named
    ``clear_loan_ledger`` cannot be made by accident.

    Commits.

    Args:
        loan_account_id: The loan whose genesis ledger to remove.
    """
    # Pylint: ``import-outside-toplevel`` -- same lazy-app-import convention every
    # helper in this module follows.
    # pylint: disable=import-outside-toplevel
    from app import ref_cache
    from app.enums import PostingSourceEnum
    from app.extensions import db

    # ID-based, never a name-string compare (the project's ref-table rule): the
    # three source kinds the loan sync emits.
    genesis_source_ids = [
        ref_cache.posting_source_id(source)
        for source in (
            PostingSourceEnum.LOAN_OPENING,
            PostingSourceEnum.LOAN_TRUEUP,
            PostingSourceEnum.LOAN_PAYMENT,
        )
    ]
    # A loan owns two shapes of ledger account: the LINKED one (``account_id``)
    # and its derived opening-equity / interest / escrow / refund ones
    # (``loan_account_id``).  Scope to entries of a genesis kind touching either.
    #
    # The entry ids are resolved to a Python list FIRST, deliberately: the
    # predicate reaches the entry THROUGH its postings, so deleting the postings
    # would empty the predicate and strand every journal-entry header behind it.
    entry_ids = [
        row[0] for row in db.session.execute(db.text("""
            SELECT DISTINCT je.id
            FROM budget.journal_entries je
            JOIN budget.account_postings p ON p.journal_entry_id = je.id
            JOIN budget.ledger_accounts la ON la.id = p.ledger_account_id
            WHERE je.source_kind_id = ANY(:src)
              AND (la.account_id = :a OR la.loan_account_id = :a)
        """), {"a": loan_account_id, "src": genesis_source_ids}).all()
    ]
    if not entry_ids:
        return
    # Legs before the header, for explicitness -- the FK CASCADE would do it
    # either way, and the ordering carries no safety property: the balanced-entry
    # constraint trigger fires AFTER INSERT OR UPDATE only
    # (``app/posting_infrastructure.py:151``), never on DELETE, so an entry cannot
    # be caught mid-delete with too few legs.
    db.session.execute(db.text(
        "DELETE FROM budget.account_postings "
        "WHERE journal_entry_id = ANY(:ids)"
    ), {"ids": entry_ids})
    db.session.execute(db.text(
        "DELETE FROM budget.journal_entries WHERE id = ANY(:ids)"
    ), {"ids": entry_ids})
    db.session.commit()


def insert_trueup_event(loan_params, anchor_balance, anchor_date=None):
    """Append a user-trueup :class:`LoanAnchorEvent` asserting a balance.

    Mirrors the production balance-trueup path
    (:func:`app.services.loan_anchor_service.apply_loan_anchor_true_up`,
    E-18 / Commit 16): the operator asserts a new dated balance and the
    resolver replays forward from this latest event.  Under the
    contractual-schedule balance model, a cash overpayment does NOT
    auto-reduce the balance, so a fixture that needs a loan in a known
    state -- in particular paid off (``anchor_balance`` of
    ``Decimal("0.00")``) -- records it as the explicit operator action
    it now is: a balance true-up, exactly as the user does after making
    an extra or lump-sum payment.

    Like production, the event is RECONCILED INTO POSTINGS in the same
    transaction (:func:`_sync_loan_ledger`): ``apply_loan_anchor_true_up`` appends
    the row and re-syncs every scenario
    (``loan_anchor_service._append_loan_anchor_and_sync``, which then commits; this
    helper leaves the commit to its caller).  An un-reconciled true-up
    does not exist as far as the ledger is concerned -- and the ledger is what
    every loan surface now reads -- so a fixture that only wrote the event left
    the loan reporting its pre-true-up balance on every page.

    Args:
        loan_params: The :class:`LoanParams` ORM instance, already
            flushed (``account_id`` populated).
        anchor_balance: The asserted balance (Decimal); ``0.00`` marks
            the loan paid off.
        anchor_date: The date the balance was asserted.  Defaults to
            ``origination_date + 1 day`` so it sorts strictly after the
            origination event and becomes the resolver's latest anchor.

    Returns:
        The newly added :class:`LoanAnchorEvent` (added and reconciled into
        postings, not committed).
    """
    # pylint: disable=import-outside-toplevel  -- same circular-dep
    # avoidance as insert_origination_rate above.
    from datetime import timedelta
    from app import ref_cache
    from app.enums import LoanAnchorSourceEnum
    from app.extensions import db
    from app.models.loan_anchor_event import LoanAnchorEvent

    if anchor_date is None:
        anchor_date = loan_params.origination_date + timedelta(days=1)
    event = LoanAnchorEvent(
        account_id=loan_params.account_id,
        anchor_date=anchor_date,
        anchor_balance=anchor_balance,
        source_id=ref_cache.loan_anchor_source_id(
            LoanAnchorSourceEnum.USER_TRUEUP,
        ),
    )
    db.session.add(event)
    _sync_loan_ledger(loan_params.account_id)
    return event


def insert_tracking_start_event(loan_params, anchor_balance, anchor_date):
    """Append a ``tracking_start`` :class:`LoanAnchorEvent` (mid-life import).

    Mirrors the production tracking-start paths (the setup door's
    :func:`app.services.loan_anchor_service.stage_loan_tracking_start` since plan
    step ``recurrence:R20``, and the dashboard's
    :func:`app.services.loan_anchor_service.record_loan_tracking_start`): the
    operator began tracking an already-amortizing loan and asserts its real
    balance as of a date.  It is loaded as an ordinary
    ``is_opening=False`` balance ASSERTION
    (:func:`app.services.loan_loaders.load_loan_anchor_facts`) that RESETS the
    genesis walk's running balance at its own date -- the loan still opens at its
    origination (step C1), so a date at/after the tracking-start reads this
    asserted balance and a date before it reads the origination opening held flat.

    Like production, the event is RECONCILED INTO POSTINGS in the same
    transaction (:func:`_sync_loan_ledger`): ``record_loan_tracking_start``
    appends the row and re-syncs every scenario (it shares
    ``loan_anchor_service._append_loan_anchor_and_sync`` with the true-up, which then
    commits; this helper leaves the commit to its caller).

    Args:
        loan_params: The :class:`LoanParams` ORM instance, already flushed.
        anchor_balance: The asserted balance (Decimal).
        anchor_date: The date the balance was asserted.

    Returns:
        The newly added :class:`LoanAnchorEvent` (added and reconciled into
        postings, not committed).
    """
    # pylint: disable=import-outside-toplevel  -- same circular-dep
    # avoidance as insert_trueup_event above.
    from app import ref_cache
    from app.enums import LoanAnchorSourceEnum
    from app.extensions import db
    from app.models.loan_anchor_event import LoanAnchorEvent

    event = LoanAnchorEvent(
        account_id=loan_params.account_id,
        anchor_date=anchor_date,
        anchor_balance=anchor_balance,
        source_id=ref_cache.loan_anchor_source_id(
            LoanAnchorSourceEnum.TRACKING_START,
        ),
    )
    db.session.add(event)
    _sync_loan_ledger(loan_params.account_id)
    return event


def create_loan_account(
    seed_user, db_session, name="Test Loan",
    principal=None, rate=None, term=24,
    origination_date=None, payment_day=1,
    *, account_type=None, anchor_balance=None,
    tracked_balance=None, tracked_from=None,
):
    """Create a loan account with LoanParams, origination event, and rate.

    The ONE shared loan-account builder for every DB-backed loan in the suite.
    Routes the account through the canonical ``account_service.create_account``
    factory (so it gets its origination ``AccountAnchorHistory`` row), inserts a
    ``LoanParams`` row, seeds the origination ``RateHistory`` the loan resolver
    requires, records the balance stated at setup as the ``tracking_start``
    assertion the setup door writes (when a caller states one), and OPENS the
    genesis posting ledger -- so a caller never has to repeat that dance, and
    cannot accidentally omit a step.

    Seventeen suites used to hand-roll this block (a ``LoanParams(...)`` insert
    beside a copy of the account-factory call).  Every one of them omitted the
    ledger open, so their loans silently exercised the no-ledger FALLBACK that
    production never takes -- which is exactly how a $3,455.79 divergence between
    the two balance producers stayed invisible to the whole suite.  They differed
    from this factory in only two respects, which are the two knobs below:
    the account TYPE and the anchor PERIOD.  Everything else was duplication.

    **Always OPENS the loan's genesis ledger** (:func:`_sync_loan_ledger`), which
    is what ``loan.create_params`` does in the same transaction as the
    ``LoanParams`` insert (``app/routes/loan/params.py:125``).  There is no
    opt-out: a configured loan without an opening posting is a state production
    cannot produce, and a suite built on it exercises the no-ledger fallback that
    production never takes.  A test that needs that broken loan on purpose builds
    it explicitly with :func:`clear_loan_ledger`.

    The sync runs after the ``RateHistory`` insert because the genesis walk
    resolves the loan's rate periods, and the resolver raises on an empty
    rate-change feed.

    Commits before returning so the loan is fully resolvable.

    **It writes what ``loan.create_params`` writes, and no more** (plan step
    ``recurrence:R20``, ruling **R-R72** part 3).  The setup form asks for the
    balance "as of" a date, and the door appends a ``tracking_start``
    :class:`LoanAnchorEvent` for it in the same transaction as the params
    whenever the loan originated BEFORE that date -- the mid-life-import
    shape, a loan whose balance today is not what amortizing its origination
    would say.  ``tracked_balance`` / ``tracked_from`` are that pair; given,
    the assertion is appended through :func:`insert_tracking_start_event`
    (the row the door writes, reconciled into the ledger the way the door
    does) before the ledger opens.  Omitted, the loan carries only its
    synthesized origination assertion -- the shape of a loan set up on its
    origination day -- and under ruling R-R71 every contractual month after
    origination that a test leaves unrecorded is CHARGED, so a fixture that
    means "a loan in good standing, mid-life" states its balance.  Until R20
    this factory seeded ``LoanParams.current_principal`` to ``principal``, a
    column the door required and nothing read (finding **REC-519**).

    Args:
        seed_user: The ``seed_user`` fixture dict.
        db_session: The test ``db.session``.
        name: The account name.
        principal: The original principal (and the account anchor).
            Defaults to ``Decimal("1000.00")``.
        rate: The origination annual rate as a Decimal fraction.  Defaults
            to ``Decimal("0.05000")`` (5%).
        term: The loan term in months (default 24).
        origination_date: The loan origination date (default
            ``date(2026, 1, 1)``).
        payment_day: The day-of-month payment day (default 1).
        account_type: The :class:`~app.enums.AcctTypeEnum` member to create the
            account as; defaults to ``AUTO_LOAN``.  Keyword-only.  Resolved to its
            id through :mod:`app.ref_cache` -- the project's ref-table rule is IDs
            for logic, name strings for display only, so this takes the enum and
            never a name string.  Any amortizing type works (``MORTGAGE`` is the
            other one the suite uses).
        (It took an ``anchor_period`` until plan step X-f1c3c.  **It was LIVE,
        not vestigial** -- it reached ``AccountSpec.anchor_period_id``, which
        ``account_service.create_account`` wrote to BOTH
        ``accounts.current_anchor_period_id`` and the origination assertion's
        own ``pay_period_id``.  Rulings R-EH and R-EO deleted both
        destinations, so the parameter had nowhere left to reach.  A loan's
        balance is ledger-derived from dated ``LoanAnchorEvent`` rows
        regardless, which is why no caller loses an assertion by dropping it.
        *A first version of this note said the parameter had "zero loads in its
        body", which was measured AFTER its use had already been deleted in the
        same pass -- a measurement of a state the author had just created, and
        the reason a claims audit caught it.*)
        anchor_balance: The ACCOUNT's anchor balance, when it must differ from the
            loan's ``principal``; defaults to *principal*.  Keyword-only.  These
            are genuinely two different facts, and production can set them apart:
            the account is created first (with a user-entered anchor) and the loan
            params are configured afterwards, through a separate route.  A test
            that needs to prove the loan path is driving a balance -- rather than
            the generic account-anchor path -- makes them differ, so the anchor is
            a distinguishable decoy rather than the same number twice.
        tracked_balance: The balance the owner states at setup (Decimal), to be
            recorded as a ``tracking_start`` dated *tracked_from*.  Keyword-only;
            the two go together.
        tracked_from: The date that balance is stated for.  Must fall strictly
            AFTER *origination_date*, the one case the door writes for; a
            fixture asking for an assertion on or before origination is asking
            for a row the door never writes, and is refused rather than
            silently given no row.

    Returns:
        The created loan :class:`~app.models.account.Account`.  Its
        :class:`LoanParams` row is reachable via :func:`loan_params_for` when a
        caller needs to append an anchor event to it.

    Raises:
        ValueError: When exactly one of *tracked_balance* / *tracked_from* is
            given, or *tracked_from* is not after *origination_date*.
    """
    # pylint: disable=import-outside-toplevel  -- same circular-dep
    # avoidance as the loan helpers above; these pull the models/services
    # package, which must not load at tests/_test_helpers import time.
    from app import ref_cache
    from app.enums import AcctTypeEnum
    from app.models.loan_params import LoanParams
    from app.services import account_service

    if principal is None:
        principal = Decimal("1000.00")
    if rate is None:
        rate = Decimal("0.05000")
    if origination_date is None:
        origination_date = _real_date(2026, 1, 1)
    if account_type is None:
        account_type = AcctTypeEnum.AUTO_LOAN
    if anchor_balance is None:
        anchor_balance = principal
    if (tracked_balance is None) != (tracked_from is None):
        raise ValueError(
            "tracked_balance and tracked_from state one assertion; give both "
            "or neither"
        )
    if tracked_from is not None and tracked_from <= origination_date:
        raise ValueError(
            f"tracked_from {tracked_from} is not after origination "
            f"{origination_date}: the setup door writes no assertion for a "
            "loan originating on or after the stated date"
        )

    account = account_service.create_account(
        account_service.AccountSpec(
            user_id=seed_user["user"].id,
            account_type_id=ref_cache.acct_type_id(account_type),
            name=name,
            anchor_balance=anchor_balance,
            # ``observed_on`` is left to the factory (today).  A loan's balance
            # is ledger-derived from dated ``LoanAnchorEvent`` rows, not from
            # this cash assertion, so the civil-day partition R-DH governs does
            # not reach it -- and the suites that DO care assert their own day.
            # Recorded rather than "fixed": a blanket day-before
            # default here trips the create-time floor for a fixture whose pay
            # periods start today or later.
        ),
    )
    db_session.add(account)
    db_session.flush()
    # **Its books open before the loan ORIGINATES *and* before the owner's
    # calendar** (plan step X-f3c-2b, ruling **R-HG**).  The comment above
    # explains why this factory leaves ``observed_on`` at the wall clock: a
    # loan's BALANCE is ledger-derived from dated ``LoanAnchorEvent`` rows, so
    # the cash assertion's day decides no figure for it.  Its PAYMENTS are
    # ordinary settled rows on the cash side, though, and they run from the
    # origination forward -- which is before the wall clock in every fixture
    # here.
    #
    # Origination alone was measured too tight.  An installment may be settled
    # EARLY, before the loan closes, and the developer ruled that legitimate:
    # ``test_balance_at.test_loan_origination.TestLoanNotYetOriginated`` pays a
    # 2026-05-01 installment on 2026-03-10 against a loan originating
    # 2026-04-15, which is the ONE shape in which the origination guard is the
    # guard doing the work.  So the bound is the earliest of the origination,
    # the assertion and the owner's calendar, which is what the shared helper
    # already computes.
    open_books_before_the_first_assertion(
        db_session, account, also_before=origination_date,
    )

    params = LoanParams(
        account_id=account.id,
        original_principal=principal,
        term_months=term,
        origination_date=origination_date,
        payment_day=payment_day,
    )
    db_session.add(params)
    db_session.flush()
    insert_origination_rate(params, rate)
    if tracked_from is not None:
        # The door's own order: the assertion is staged before the one ledger
        # open, and this helper's re-sync IS that open.
        insert_tracking_start_event(params, tracked_balance, tracked_from)
    else:
        _sync_loan_ledger(account.id)
    db_session.commit()
    return account


def loan_params_for(db_session, account_id):
    """Return a loan account's :class:`LoanParams` row.

    :func:`create_loan_account` returns the ACCOUNT, but the anchor helpers
    (:func:`insert_trueup_event`, :func:`insert_tracking_start_event`) take its
    params, so callers were each re-writing the same one-line query.  Holds it in
    one place.

    Args:
        db_session: The test ``db.session``.
        account_id: The loan account whose params to load.

    Returns:
        The loan's :class:`~app.models.loan_params.LoanParams` row.

    Raises:
        NoResultFound: If the account has no ``LoanParams`` (not a configured
            loan) -- a fixture bug, surfaced rather than papered over with None.
    """
    # pylint: disable=import-outside-toplevel  -- same lazy-app-import
    # convention every helper in this module follows.
    from app.models.loan_params import LoanParams

    return db_session.query(LoanParams).filter_by(account_id=account_id).one()


def add_escrow_line(
    db_session, account_id, name, annual_amount, *,
    effective_date=None, inflation_rate=None,
):
    """Create an escrow LINE with one opening version (supersession model).

    The shared escrow-setup builder for tests that exercise app paths reading
    escrow (the loan-payment split, PITI, savings-dashboard PITI, the loan-card
    breakdown).  Inserts the ``EscrowLine`` + ``EscrowComponentVersion`` pair the
    app reads (the supersession escrow model).  When
    ``effective_date`` is omitted it defaults to the loan's ``origination_date``,
    so the escrow is active for the whole loan life (the standing-charge case
    every escrow test intends); pass an explicit date to build a version that
    supersedes an earlier one.  Flushes so ids are assigned; the caller commits.

    Args:
        db_session: The test ``db.session``.
        account_id: The loan account the escrow belongs to.
        name: The escrow line display name.
        annual_amount: The opening version's stored annual amount (Decimal).
        effective_date: The version's effective date; defaults to the loan's
            origination date when omitted.
        inflation_rate: Optional decimal-fraction inflation rate for the version.

    Returns:
        The created :class:`~app.models.escrow_line.EscrowComponentVersion` (its
        ``line`` relationship carries the parent line), so a caller can mutate
        ``annual_amount`` to exercise a live escrow change.
    """
    # pylint: disable=import-outside-toplevel  -- same lazy-app-import
    # convention every helper in this module follows.
    from app.models.escrow_line import EscrowComponentVersion, EscrowLine
    from app.models.loan_params import LoanParams

    if effective_date is None:
        params = (
            db_session.query(LoanParams).filter_by(account_id=account_id).one()
        )
        effective_date = params.origination_date

    line = EscrowLine(account_id=account_id, name=name)
    db_session.add(line)
    db_session.flush()
    version = EscrowComponentVersion(
        line_id=line.id, effective_date=effective_date,
        annual_amount=annual_amount, inflation_rate=inflation_rate,
    )
    db_session.add(version)
    db_session.flush()
    return version


def create_loan_with_trueup(
    seed_user, db_session, *, origination_principal, anchor_balance,
    anchor_date, rate, origination_date, name="Split Loan", term=360,
    payment_day=1, escrow_annual=None, account_type=None,
):
    """Create an amortizing loan carrying an origination AND a user-trueup anchor.

    The shared "resolvable loan with a controlled latest anchor" builder for the
    Build-Order Step 4 split-service and wiring suites (a duplicate-code finding
    otherwise): routes through :func:`create_loan_account` (origination anchor +
    rate), appends a ``user_trueup`` :class:`LoanAnchorEvent` at
    *anchor_balance* / *anchor_date* -- the LATEST event, so both the split walk
    and the resolver seed their running balance from it -- and an optional
    escrow component effective from origination (so every post-anchor payment's
    date falls in its active range).  Commits, so the loan is fully resolvable.

    Keeping ``origination_principal`` distinct from ``anchor_balance`` lets a
    caller prove the split seeds from the trueup anchor, not origination.

    Args:
        seed_user: The ``seed_user`` fixture dict.
        db_session: The test ``db.session``.
        origination_principal: The original principal (the origination anchor).
        anchor_balance: The user-trueup balance the split walk seeds from.
        anchor_date: The user-trueup anchor date (the post-anchor lower bound).
        rate: The origination annual rate as a Decimal fraction.
        origination_date: The loan origination date.
        name: The account name (default ``"Split Loan"``).
        term: The loan term in months (default 360).
        payment_day: The day-of-month payment day (default 1).
        escrow_annual: Optional annual escrow amount (Decimal); when given, one
            escrow component effective from ``origination_date`` is added.
        account_type: The :class:`~app.enums.AcctTypeEnum` member to create the
            account as; defaults to :func:`create_loan_account`'s own default
            (``AUTO_LOAN``).  Pass ``MORTGAGE`` when the test's assertions depend
            on the loan's KIND rather than just its amortization -- Schedule A's
            mortgage-interest deduction is the case that does, and a fixture that
            takes the default silently pins an auto loan's interest into a
            home-mortgage figure.

    Returns:
        The created loan :class:`~app.models.account.Account`.
    """
    # Pylint: ``import-outside-toplevel`` -- same circular-dep avoidance as the
    # loan helpers above; these pull the models/services package, which must not
    # load at tests/_test_helpers import time.
    from app.models.loan_params import LoanParams  # pylint: disable=import-outside-toplevel

    loan = create_loan_account(
        seed_user, db_session, name=name, principal=origination_principal,
        rate=rate, term=term, origination_date=origination_date,
        payment_day=payment_day, account_type=account_type,
    )
    params = (
        db_session.query(LoanParams).filter_by(account_id=loan.id).one()
    )
    insert_trueup_event(params, anchor_balance, anchor_date)
    if escrow_annual is not None:
        add_escrow_line(
            db_session, loan.id, "Tax & Insurance", escrow_annual,
            effective_date=origination_date,
        )
    db_session.commit()
    return loan


def create_savings_account(
    seed_user, db_session, name, anchor_balance, observed_on=None,
):
    """Create a Savings account via the canonical factory (flushed, uncommitted).

    The shared liquid-account builder for goal-track / savings tests, so
    the stereotyped ``AccountSpec`` + ``create_account`` + ``flush`` block
    is not copied per suite (a duplicate-code finding).  The caller
    commits with its own goal/transaction inserts.

    Args:
        seed_user: The ``seed_user`` fixture dict.
        db_session: The test ``db.session``.
        name: The account name.
        anchor_balance: The opening anchor balance (Decimal).
        observed_on: The civil day the opening balance is asserted for, or
            ``None`` for the factory's default (today).  **A suite whose money
            moves BEFORE today states this** (plan step X-f3c-2c): the
            origination assertion is append-only, so an account opened "today"
            and then handed movements dated in January carries an assertion
            that governs every one of them.  Re-stamping the row afterwards was
            how that used to be repaired, and there is no such act.

    **It took an ``anchor_period_id`` until plan step X-f1c3c** (ruling R-EH):
    an account no longer references a pay period at all, so callers that pinned
    one now pin the assertion's DAY or take the factory's default (today).

    Returns:
        The created Savings :class:`~app.models.account.Account`.
    """
    # pylint: disable=import-outside-toplevel  -- same circular-dep
    # avoidance as the loan helpers above.
    from app.models.ref import AccountType
    from app.services import account_service

    savings_type = (
        db_session.query(AccountType).filter_by(name="Savings").one()
    )
    spec_kwargs = {
        "user_id": seed_user["user"].id,
        "account_type_id": savings_type.id,
        "name": name,
        "anchor_balance": anchor_balance,
        # ``observed_on`` is left to the factory (today) unless the caller
        # names a day.  See ``create_loan_account`` for why this helper does
        # not take ``create_account_of_type``'s day-before default.
    }
    if observed_on is not None:
        spec_kwargs["observed_on"] = observed_on
    account = account_service.create_account(
        account_service.AccountSpec(**spec_kwargs),
    )
    db_session.add(account)
    db_session.flush()
    # **Its books open the day BEFORE its origination assertion** (plan step
    # X-f3c-2b, ruling **R-HG**).  ``create_account`` defaults ``observed_on``
    # to ``display_today()`` and the settle door defaults a settle day to the
    # SAME ``display_today()``, so a fixture meaning "this account existed,
    # then money moved on it" lands the movement on the very day the books
    # open -- and an opening equity is that day's CLOSING balance, so the
    # movement is inside it and is refused.  Moves no figure: the origination
    # assertion still clears whatever settled on its own day.
    open_books_before_the_first_assertion(db_session, account)
    return account


def create_hysa_account(  # pylint: disable=too-many-arguments,too-many-positional-arguments
    seed_user, db_session, anchor_period, balance,
    apy=Decimal("0.05000"), name="HYSA", compounding=None,
):
    """Create an HYSA account (INTEREST) with InterestParams (default 5% APY daily).

    The shared interest-bearing-account builder (promoted from the
    balance-seam suite's per-file ``_make_hysa`` copy) so the dashboard,
    seam, and net-worth suites build an INTEREST account through one home
    rather than each re-inlining the ``AccountType`` lookup +
    ``create_account`` + ``InterestParams`` block.  Routes the account
    through the canonical ``account_service.create_account`` factory (so it
    gets its origination ``AccountAnchorHistory`` row), then attaches the
    ``InterestParams`` row (APY + daily compounding) that makes the account
    classify INTEREST.  Commits before returning so the account is fully
    resolvable.

    **The opening assertion is DATED at the anchor period's first day, and the
    factory supplies that day to ``create_account`` rather than moving the row
    afterwards** (plan step X-f3c-2c).  Since plan step X-c2a, modelled
    interest accrues only forward of an account's latest balance assertion
    (ruling R-L), and ``create_account`` defaults ``observed_on`` to today.  A
    suite that freezes ``today`` inside its own seeded period range --
    ``tests/test_services`` freezes it to 2026-03-20 -- would otherwise build
    an account asserted months AFTER its own last pay period, a state
    production cannot reach (a true-up files against ``get_current_period``)
    and one in which the account accrues nothing anywhere.  Dating the
    origination at the period's own start makes the fixture deterministic,
    reachable (an account opened on day 1 of its period), and
    clock-independent, and it keeps every hand-computed interest figure in the
    suites valid: the accrual window is then the full anchor period, exactly
    what it was before the rule existed.  A test that needs a MID-period
    assertion (the shape the rule exists for) appends its own.

    **The day now goes through the DOOR, which BOUNDS it** -- an assertion may
    not be dated in the future (``anchor_service.resolve_observation_day``), so
    a caller handing this factory a pay period that starts after today is
    refused, where the old re-stamp wrote that state silently.  That is the
    point rather than a cost: one route suite was building exactly it (an
    account anchored two periods AHEAD of today) and grading a shape no owner
    can produce.

    Args:
        seed_user: The ``seed_user`` fixture dict.
        db_session: The test ``db.session``.
        anchor_period: The :class:`~app.models.pay_period.PayPeriod` to
            anchor the account against; its ``id`` becomes the account's
            ``current_anchor_period_id``.
        balance: The opening anchor balance (Decimal -- construct from a
            string per the coding standard).
        apy: The annual percentage yield as a Decimal fraction (default
            ``Decimal("0.05000")`` for 5%).
        name: The account name (default ``"HYSA"``).
        compounding: The :class:`~app.enums.CompoundingFrequencyEnum` member,
            or ``None`` for DAILY.  Parameterised at plan step X-g4b: the
            helper hardcoded DAILY, so no test anywhere ran a MONTHLY or
            QUARTERLY account through a balance PRODUCER -- and the real Money
            Market compounds MONTHLY, so a regression hardcoding the frequency
            in the replay's rate resolver would have misspriced a live account
            with the whole suite green.

    Returns:
        The created HYSA :class:`~app.models.account.Account`.

    Pylint: ``too-many-arguments`` (7/5) / ``too-many-positional-arguments``
    (7/5) -- a test FACTORY whose parameters are the account's independent
    configurable facts (owner, session, anchor period, balance, rate,
    name, compounding frequency); bundling them would put a
    parameter object between every suite and its fixture.
    """
    # Pylint: ``import-outside-toplevel`` -- this module imports no app
    # symbols at top level (its collection-time-safety convention); load
    # the models lazily, the same way the loan / investment helpers do.
    # pylint: disable=import-outside-toplevel
    from app import ref_cache
    from app.enums import CompoundingFrequencyEnum
    from app.models.interest_params import InterestParams
    from app.models.ref import AccountType
    from app.services import account_service

    hysa_type = db_session.query(AccountType).filter_by(name="HYSA").one()
    account = account_service.create_account(
        account_service.AccountSpec(
            user_id=seed_user["user"].id,
            account_type_id=hysa_type.id,
            name=name,
            anchor_balance=balance,
            observed_on=anchor_period.start_date,
        ),
    )
    db_session.add(account)
    db_session.flush()
    db_session.add(InterestParams(
        account_id=account.id,
        apy=apy,
        compounding_frequency_id=ref_cache.compounding_frequency_id(
            compounding or CompoundingFrequencyEnum.DAILY,
        ),
    ))
    # **And then before every day a row could land on** (plan step X-f3c-2b,
    # ruling **R-HG**).  ``create_account`` opens the books on the day it was
    # handed, which is right when the anchor period is the earliest -- and
    # these factories are routinely handed a LATER period while the suite
    # records movements in an earlier one.  Backward-only, so it never undoes
    # the day stated above.
    open_books_before_the_first_assertion(db_session, account)
    db_session.commit()
    return account


# Default opening anchor balance for ledger-account-suite accounts.  The
# Build-Order Step 2 suites never assert on a balance (Commit 2 touches no
# balance math), so a single fixed value keeps the shared factory at four
# parameters and the call sites free of an irrelevant amount.
_LEDGER_SUITE_ANCHOR_BALANCE = Decimal("100.00")


def create_account_via_service(
    seed_user, db_session, type_name, name, anchor_balance=None,
    observed_on=None,
):
    """Call ``account_service.create_account`` for *type_name*, and NOTHING else.

    **The primitive, for the suites whose SUBJECT is what that service
    records** -- ``tests/test_services/test_account_opening.py`` grades the one
    ``budget.account_openings`` row the factory writes, so a fixture that
    appended a second would be grading itself.  Every other caller wants
    :func:`create_account_of_type`, which is this plus the books an account
    that has already existed would have.

    Splitting the two is plan step X-f3c-2b's own correction.  The books move
    was first written as an ``if observed_on is None`` branch inside the shared
    factory, which bound two unrelated things together: a caller naming the
    ASSERTION's day got, as a side effect, books that forbade the settles it
    was about to record.  The twelve callers that pass ``observed_on`` were
    exactly the ones left broken by it.

    Args:
        seed_user: The ``seed_user`` fixture dict.
        db_session: The test ``db.session``, used for the ``AccountType``
            lookup below.
        type_name: The ``ref.account_types`` name (e.g. ``"Checking"``).
        name: The account name.
        anchor_balance: Optional opening anchor balance (``Decimal``);
            ``None`` uses the ledger-suite sentinel.
        observed_on: Optional civil day the opening balance was TRUE.
            Defaults to the day before the frozen today.

    Returns:
        The created :class:`~app.models.account.Account` (flushed,
        uncommitted).
    """
    # Pylint: ``import-outside-toplevel`` -- this module imports no app
    # symbols at top level (its collection-time-safety convention); load
    # the models / service lazily, the same way the factory helpers above do.
    # pylint: disable=import-outside-toplevel
    from datetime import timedelta
    from app.models.ref import AccountType
    from app.services import account_service
    from app.utils.dates import display_today

    acct_type = (
        db_session.query(AccountType).filter_by(name=type_name).one()
    )
    return account_service.create_account(
        account_service.AccountSpec(
            user_id=seed_user["user"].id,
            account_type_id=acct_type.id,
            name=name,
            anchor_balance=(
                _LEDGER_SUITE_ANCHOR_BALANCE if anchor_balance is None
                else anchor_balance
            ),
            observed_on=(
                display_today() - timedelta(days=1)
                if observed_on is None else observed_on
            ),
        ),
    )


def create_account_of_type(
    seed_user, db_session, type_name, name, anchor_balance=None,
    observed_on=None,
):
    """Create an account of any built-in type via the canonical factory.

    The shared "build an account of type X" helper for the ledger-account
    (Build-Order Step 2) suites, so the stereotyped ``AccountType`` lookup +
    ``AccountSpec`` + ``create_account`` block is not copied per file (a
    duplicate-code finding).  ``create_account`` fires the Step-2
    ledger-account sync hook, so the returned account already carries its
    paired ``budget.ledger_accounts`` row.  The opening anchor balance
    defaults to a fixed sentinel (the ledger-pairing suites assert on
    pairing, never on balance); the Step-5 account-anchor suites pass an
    explicit ``anchor_balance`` because the opening correction posts exactly
    that value.  The anchor period is resolved by the factory from the day the
    opening is observed on.

    **What it returns is an account that has ALREADY EXISTED**, and both halves
    of that are deliberate.

    * The origination ASSERTION defaults to the day BEFORE today (ruling
      R-DH (a), finding N-133 / F1).  An assertion is the CLOSING balance for
      its civil day, so a settle dated that same day is INSIDE it -- and the
      ordinary settle idiom in these suites is the seam's own ``settled_on =
      display_today()``, which under a frozen clock is TODAY.  An account
      asserted "today" therefore swallows every settle the test then records,
      and the fixture stops exercising the thing it names.
    * The BOOKS are then opened before anything this fixture could date, always
      (plan step X-f3c-2b, ruling **R-HG**).  An opening equity is the closing
      balance for its OWN day, so a movement dated on or before it is not
      absorbed but REFUSED.  ``observed_on`` does not switch this off: it says
      where the assertion goes, which is a different question.

    **A suite whose subject is the record ``create_account`` writes wants
    :func:`create_account_via_service` instead** -- this one appends a second
    ``budget.account_openings`` row, which is the production shape after
    migration ``d3b6f1c8a274`` and is not what a test of the factory means.

    Args:
        seed_user: The ``seed_user`` fixture dict.
        db_session: The test ``db.session``.
        type_name: The ``ref.account_types`` name (e.g. ``"Checking"``,
            ``"Mortgage"``, ``"401(k)"``).
        name: The account name.
        anchor_balance: Optional opening anchor balance (``Decimal``);
            ``None`` uses the ledger-suite sentinel.
        observed_on: Optional civil day the opening balance was TRUE.
            Defaults to the DAY BEFORE the frozen today -- see below.

    Returns:
        The created :class:`~app.models.account.Account` (flushed,
        uncommitted).
    """
    account = create_account_via_service(
        seed_user, db_session, type_name, name, anchor_balance, observed_on,
    )
    # **Its BOOKS open before anything a fixture can date, ALWAYS** (plan step
    # X-f3c-2b, ruling **R-HG**).  ``create_account`` puts the opening record
    # and the origination assertion on ONE day, so a caller settling a row on
    # or before that day is building a state the app refuses -- and the default
    # idiom here settles on the frozen today or earlier.  Unconditional, and
    # not keyed on whether the caller named ``observed_on``: that argument says
    # where the ASSERTION goes, which is a different question, and binding the
    # two left every caller that wanted a dated assertion with books it could
    # not record against.
    open_books_before_the_first_assertion(db_session, account)
    return account


def ledger_accounts_for_account(db_session, account_id):
    """Return every ``LedgerAccount`` linked to *account_id*.

    Shared by the ledger-account model / service / backfill suites so the
    one-line lookup is not re-inlined per file (a duplicate-code finding).

    Args:
        db_session: The test ``db.session``.
        account_id: The real account's id whose linked ledger accounts to
            fetch.

    Returns:
        list[:class:`~app.models.ledger_account.LedgerAccount`] -- every
        row carrying this ``account_id``.  Since Step 5's
        ``uq_ledger_accounts_account_kind`` re-key, TWO rows are normal
        once an account has anchor corrections: the ``linked`` row plus
        its ``anchor_equity`` twin (one per account-linked kind).  Callers
        asserting counts or shapes must say WHICH kinds they expect.
    """
    # Pylint: ``import-outside-toplevel`` -- same collection-time-safety
    # convention as the helpers above (no app symbols imported at module
    # load); load the model lazily here.
    # pylint: disable=import-outside-toplevel
    from app.models.ledger_account import LedgerAccount

    return (
        db_session.query(LedgerAccount)
        .filter_by(account_id=account_id)
        .all()
    )


def ledger_account_of_kind(db_session, account_id, kind_enum):
    """Return an account's ledger row of a given kind (linked / anchor_equity), or None.

    The shared kind-scoped lookup behind :func:`linked_ledger_account` and the
    account-anchor suites' anchor-equity lookups.  Since Step 5's
    ``(account_id, kind_id)`` re-key an account can carry TWO rows sharing its
    ``account_id`` (the ``linked`` row plus its ``anchor_equity`` equity twin),
    so any "the account's ledger account" lookup must say WHICH kind -- a bare
    ``[0]`` on :func:`ledger_accounts_for_account` is insertion-order-dependent.
    Holding the ``(account_id, kind_id)`` query in one place keeps the several
    kind-scoped lookups the posting suites use from drifting (a duplicate-code
    finding otherwise).

    Args:
        db_session: The test ``db.session``.
        account_id: The real account's id.
        kind_enum: The :class:`~app.enums.LedgerAccountKindEnum` member to
            resolve (e.g. ``LINKED`` or ``ANCHOR_EQUITY``).

    Returns:
        The :class:`~app.models.ledger_account.LedgerAccount` of that kind for
        the account, or ``None`` when the account has no such row yet.
    """
    # pylint: disable=import-outside-toplevel  -- same collection-time-safety
    # convention every helper in this module follows (no app symbols at module
    # load).
    from app import ref_cache
    from app.models.ledger_account import LedgerAccount

    return (
        db_session.query(LedgerAccount)
        .filter_by(
            account_id=account_id,
            kind_id=ref_cache.ledger_account_kind_id(kind_enum),
        )
        .one_or_none()
    )


def transit_ledger_account(db_session, user_id):
    """Return the owner's Transfers-in-transit ledger row, or ``None``.

    A LOOKUP, never a create -- the writer mints the ``transit`` owner bucket
    on the owner's first settled transfer
    (``ledger_account_service.get_or_create_transit_ledger_account``, plan
    step ``balance:X-bi-6-3``, ruling **R-BAL99**), so a suite that finds
    none has caught the writer not booking against it.  Keyed exactly as
    ``uq_ledger_accounts_owner_bucket`` is: the owner, the ``transit`` kind
    and the flag; the class is a function of the kind for a bucket.

    Args:
        db_session: The test ``db.session``.
        user_id: The owner's id.

    Returns:
        The ``transit`` :class:`~app.models.ledger_account.LedgerAccount`, or
        ``None`` when no transfer of the owner's has posted yet.
    """
    # pylint: disable=import-outside-toplevel  -- same collection-time-safety
    # convention as the helpers above (no app symbols at module load).
    from app import ref_cache
    from app.enums import LedgerAccountKindEnum
    from app.models.ledger_account import LedgerAccount

    return (
        db_session.query(LedgerAccount)
        .filter_by(
            user_id=user_id,
            kind_id=ref_cache.ledger_account_kind_id(
                LedgerAccountKindEnum.TRANSIT,
            ),
            is_owner_bucket=True,
        )
        .one_or_none()
    )


def linked_ledger_account(db_session, account_id):
    """Return the account's LINKED ledger row, never its anchor-equity twin.

    Since Step 5's ``(account_id, kind_id)`` re-key an account with posted
    anchor corrections carries TWO ledger rows sharing its ``account_id``
    (the ``linked`` row plus the ``anchor_equity`` equity twin), so any
    helper that grabs "the account's ledger account" must say WHICH kind --
    a bare ``[0]`` on :func:`ledger_accounts_for_account` is
    insertion-order-dependent.  This is the shared linked-kind lookup the
    posting suites key their cash-leg assertions off (the ``LINKED`` case of
    :func:`ledger_account_of_kind`).

    Args:
        db_session: The test ``db.session``.
        account_id: The real account's id.

    Returns:
        The LINKED :class:`~app.models.ledger_account.LedgerAccount`, or
        ``None`` when the account has no pairing yet.
    """
    # pylint: disable=import-outside-toplevel  -- same collection-time-safety
    # convention as the helpers above (no app symbols at module load).
    from app.enums import LedgerAccountKindEnum

    return ledger_account_of_kind(
        db_session, account_id, LedgerAccountKindEnum.LINKED,
    )


def make_balanced_entry(  # pylint: disable=too-many-arguments
    session, seed_user, *, from_ledger_id, to_ledger_id,
    amount=Decimal("100.00"), transaction_entry_id=None, source_kind=None,
    posting_kind=None,
    period_id=None,
):
    """Create and commit one balanced journal entry (two legs summing to zero).

    The from leg is ``-amount`` (credit), the to leg is ``+amount`` (debit),
    so the deferred balanced trigger passes at commit.  Returns the committed
    :class:`~app.models.journal_entry.JournalEntry`.  Shared by the
    journal-entry model suite and the ledger append-only role-privilege suite
    (previously module-local to the former -- a duplicate-code finding once
    the R4 tests needed the same shape).

    ``source_kind`` / ``posting_kind`` default to a transfer movement's
    shape with no movement linked (``transfer_movement`` / ``transfer``):
    the one-entry ``transfer`` source this default named until plan step
    ``balance:X-bi-6-5``'s second leaf (card plan#1845) is gone, with the
    ``transfer_id`` / ``transaction_id`` links it and the row's own leg
    carried.  A movement-sourced entry passes ``transaction_entry_id=<id>``.
    ``source_kind`` / ``posting_kind`` default to ``None`` (resolved lazily)
    so the enums are not imported at module load, matching this module's
    collection-time-safety convention.

    Args:
        session: The test ``db.session``.
        seed_user: The ``seed_user`` fixture dict (supplies the user,
            scenario, and bootstrap period).
        from_ledger_id: Ledger account id for the ``-amount`` leg.
        to_ledger_id: Ledger account id for the ``+amount`` leg.
        amount: The leg magnitude (Decimal).
        transaction_entry_id: Optional ``budget.transaction_entries``
            back-link (a movement's).
        source_kind: :class:`~app.enums.PostingSourceEnum` member, or
            ``None`` for TRANSFER_MOVEMENT.
        posting_kind: :class:`~app.enums.PostingKindEnum` member, or
            ``None`` for TRANSFER.
        period_id: Pay period id, or ``None`` for the bootstrap period.

    Returns:
        The committed :class:`~app.models.journal_entry.JournalEntry`.
    """
    # Pylint: ``import-outside-toplevel`` -- same collection-time-safety
    # convention as the helpers above (no app symbols imported at module
    # load); load the enums, cache, and models lazily here.
    # pylint: disable=import-outside-toplevel
    from app import ref_cache
    from app.enums import PostingKindEnum, PostingSourceEnum
    from app.models.journal_entry import JournalEntry, Posting

    if source_kind is None:
        source_kind = PostingSourceEnum.TRANSFER_MOVEMENT
    if posting_kind is None:
        posting_kind = PostingKindEnum.TRANSFER
    if period_id is None:
        period_id = seed_user["bootstrap_period"].id
    entry = JournalEntry(
        user_id=seed_user["user"].id,
        scenario_id=seed_user["scenario"].id,
        pay_period_id=period_id,
        entry_date=_real_date(2026, 1, 15),
        source_kind_id=ref_cache.posting_source_id(source_kind),
        transaction_entry_id=transaction_entry_id,
        description="Test entry",
    )
    session.add(entry)
    session.flush()
    kind_id = ref_cache.posting_kind_id(posting_kind)
    session.add(Posting(
        journal_entry_id=entry.id, ledger_account_id=from_ledger_id,
        amount=-amount, posting_kind_id=kind_id,
    ))
    session.add(Posting(
        journal_entry_id=entry.id, ledger_account_id=to_ledger_id,
        amount=amount, posting_kind_id=kind_id,
    ))
    session.commit()
    return entry


def loan_correction_entries(db_session, transfer_id, loan_account_id):
    """Return the loan_payment corrections booked at a payment's KEY.

    The ``budget.journal_entries`` rows the loan posting package books for one
    settled payment's split, ordered by id.  **Re-expressed at plan step
    ``balance:X-bi-6-3`` (ruling R-BAL102, a rule-5 class (a) re-expression
    Josh confirmed 2026-09-21):** the split is a DERIVATION keyed
    ``(loan_payment kind, the payment's pay period, its visible day)`` on the
    loan's own chart rows and links NO row, so "the corrections under this
    shadow" is now "the corrections at this payment's key" -- selected by
    source kind, the payment's pay period, its settled day and a leg on the
    loan's chart rows.  Through that step the split carried the shadow's
    ``transaction_id`` and this read it by that link.  **The key is read off
    the TRANSFER and the loan side's RECORD since plan step
    ``balance:X-bi-6-4d-2``** (ruling **R-BAL167** class 4): the record hangs
    off the transfer there and the twins' status and day are no longer kept,
    so the shadow it was passed named only which transfer and which side; it
    read the shadow's own period, scenario and day until then (the loan walk
    has read the record's day since ``X-bi-6-4b``).  **It is keyed by the
    transfer and the loan account since plan step ``balance:X-bi-6-4d-2``**
    (ruling **R-BAL167** class 4): a transfer is one row there and no
    shadow exists to name the side.  A side with no dated record
    has no key and is REFUSED rather than answered ``[]`` (the leaf-2
    adversarial review: an empty answer for a reverted payment would let
    "after the revert the split is gone" pass without reading the ledger);
    a test that holds a reverted or deleted payment reads
    :func:`loan_correction_entries_at` with the key it captured before the
    act.  Shared by the split-service, wiring, backfill and reconciliation
    suites so all read a payment's corrections the same way.

    Args:
        db_session: The test ``db.session``.
        transfer_id: The payment's ``budget.transfers`` id.
        loan_account_id: The loan (to-)account whose side holds the record
            and whose chart rows scope the read.

    Returns:
        list[:class:`~app.models.journal_entry.JournalEntry`] -- the correction
        entries at the payment's key, ascending by id (empty when none).

    Raises:
        ValueError: If the loan side holds no dated record (it has no key).
    """
    # Pylint: import-outside-toplevel -- same lazy-app-import convention
    # every helper in this module follows.
    # pylint: disable=import-outside-toplevel
    from app.models.transfer import Transfer

    record = transfer_side_record(db_session, transfer_id, loan_account_id)
    if record is None or record.settled_on is None:
        raise ValueError(
            f"loan_correction_entries: transfer {transfer_id}'s side "
            f"on account {loan_account_id} holds no dated record and so no "
            f"split key; read loan_correction_entries_at with the key "
            f"captured before the act that released the day."
        )
    transfer = db_session.get(Transfer, transfer_id)
    return loan_correction_entries_at(
        db_session, loan_account_id, transfer.scenario_id,
        transfer.pay_period_id, record.settled_on,
    )


def loan_correction_entries_at(
    db_session, loan_account_id, scenario_id, period_id, day,
):
    """Return the loan_payment corrections at ONE key on a loan's chart rows.

    The key form of :func:`loan_correction_entries`, for a test that holds a
    key's period and day but no dated loan-side record to read the key off --
    after a revert (the side keeps its record with its day
    released, and the correction and the reversal the next reconcile posts
    both sit at the old key), after a hard delete (the transfer is gone, its
    sides' records taken off before it), after a ``$0.00`` close or a
    correction to ``$0.00`` (the side holds no record, and the split is dated
    by the installment the payment skips, ruling **R-BAL139**), or at a key
    the walk prices nothing (a residue planted there).  The ``loan_payment``
    journal entries in *period_id* dated *day* with a leg on any of the loan's
    own chart rows
    (:func:`app.services._posting_reconcile.account_chart_row_ids`: its linked
    row and its per-loan interest / escrow / refund / opening-equity rows) in
    *scenario_id* (postings are scenario-scoped, and a loan paid in two
    scenarios holds one split per scenario at the same key), ascending by id.

    Args:
        db_session: The test ``db.session``.
        loan_account_id: The loan whose chart rows scope the read.
        scenario_id: The budget scenario the split was posted in.
        period_id: The key's ``pay_period_id``.
        day: The key's ``entry_date``.

    Returns:
        list[:class:`~app.models.journal_entry.JournalEntry`], ascending by id
        (empty when nothing is posted at the key).
    """
    # pylint: disable=import-outside-toplevel  -- same lazy-app-import
    # convention every helper in this module follows.
    from app import ref_cache
    from app.enums import PostingSourceEnum
    from app.models.journal_entry import JournalEntry, Posting
    from app.services._posting_reconcile import account_chart_row_ids

    on_loan_rows = (
        db_session.query(Posting.journal_entry_id)
        .filter(Posting.ledger_account_id.in_(
            account_chart_row_ids(loan_account_id),
        ))
    )
    return (
        db_session.query(JournalEntry)
        .filter(
            JournalEntry.source_kind_id == ref_cache.posting_source_id(
                PostingSourceEnum.LOAN_PAYMENT
            ),
            JournalEntry.scenario_id == scenario_id,
            JournalEntry.pay_period_id == period_id,
            JournalEntry.entry_date == day,
            JournalEntry.id.in_(on_loan_rows),
        )
        .order_by(JournalEntry.id)
        .all()
    )


def ledger_net(db_session, ledger_account_id, scenario_id):
    """Return the net of every posting leg on a ledger account in a scenario.

    Sums ``budget.account_postings.amount`` over *ledger_account_id* for journal
    entries in *scenario_id*.  Shared by the Step-4 split-service and wiring
    suites so both read a ledger's net the same way.

    Args:
        db_session: The test ``db.session``.
        ledger_account_id: The ledger account whose legs to sum.
        scenario_id: The scenario to scope to.

    Returns:
        The signed net as a ``Decimal`` (``Decimal("0")`` when none posted).
    """
    # pylint: disable=import-outside-toplevel  -- same lazy-app-import
    # convention every helper in this module follows.
    from app.extensions import db
    from app.models.journal_entry import JournalEntry, Posting

    return (
        db_session.query(
            db.func.coalesce(db.func.sum(Posting.amount), Decimal("0"))
        )
        .join(JournalEntry, Posting.journal_entry_id == JournalEntry.id)
        .filter(
            Posting.ledger_account_id == ledger_account_id,
            JournalEntry.scenario_id == scenario_id,
        )
        .scalar()
    )


def correction_net_in_period(
    db_session, ledger_account_id, scenario_id, source_enum, period_id,
):
    """Return one source kind's posted net on a ledger account in ONE pay period.

    The per-PERIOD reading of the ledger the R2 attribution rule is about (plan
    step X-ai-r): a reversal must net the period whose postings it corrects back
    to what the surviving facts say, rather than leaving that period overstated
    and filing the correction against a different one.  ``ledger_net`` is the
    scalar-total counterpart and ``linked_net_by_date`` the per-DATE one; this is
    the third axis, and it is the one an anchor correction is attributed on.

    Scoped the way the reconcile scopes itself -- one source kind, one ledger
    account, one scenario -- so the figure asserted here is the figure the
    reconcile reasons about.  Shared by the cash and loan anchor suites, whose
    only real difference is where an assertion's period comes from.

    Args:
        db_session: The test ``db.session``.
        ledger_account_id: The ledger account whose legs to sum (the account's
            LINKED ledger, for an anchor correction).
        scenario_id: The scenario to scope to.
        source_enum: The :class:`~app.enums.PostingSourceEnum` member naming the
            correction kind (``ACCOUNT_TRUEUP``, ``LOAN_TRUEUP``, ...).
        period_id: The pay period to scope to.

    Returns:
        The signed net as a ``Decimal`` (``Decimal("0.00")`` when the period
        holds no leg of that kind -- which is what a fully reversed period reads
        as, and the assertion most of these tests make).
    """
    # pylint: disable=import-outside-toplevel  -- same lazy-app-import
    # convention every helper in this module follows.
    from app import ref_cache
    from app.extensions import db
    from app.models.journal_entry import JournalEntry, Posting

    return (
        db_session.query(
            db.func.coalesce(db.func.sum(Posting.amount), Decimal("0.00"))
        )
        .join(JournalEntry, Posting.journal_entry_id == JournalEntry.id)
        .filter(
            Posting.ledger_account_id == ledger_account_id,
            JournalEntry.scenario_id == scenario_id,
            JournalEntry.pay_period_id == period_id,
            JournalEntry.source_kind_id == ref_cache.posting_source_id(
                source_enum,
            ),
        )
        .scalar()
    )


def linked_net_by_date(db_session, ledger_account_id, scenario_id):
    """Return a ledger account's posted net keyed by journal-entry ``entry_date``.

    Sums ``budget.account_postings.amount`` over *ledger_account_id* for journal
    entries in *scenario_id*, grouped by each entry's ``entry_date``.  A date whose
    legs net to zero (a reversed forgery) is still present, at ``Decimal("0.00")``.
    This is an INDEPENDENT re-implementation of the grouped read the
    checked-projection assert performs (production groups via ``_visible_nets``), so
    a suite comparing against it keeps teeth if that production query drifts.  Shared
    by the posting-service checked-projection suite and the loan-route escrow-sync
    suite so both read a ledger's per-date net the same way (``ledger_net`` is the
    scalar-total counterpart).

    Args:
        db_session: The test ``db.session``.
        ledger_account_id: The ledger account whose legs to sum.
        scenario_id: The scenario to scope to.

    Returns:
        A ``{entry_date: Decimal}`` mapping (empty when none posted).
    """
    # pylint: disable=import-outside-toplevel  -- same lazy-app-import
    # convention every helper in this module follows.
    from app.extensions import db
    from app.models.journal_entry import JournalEntry, Posting

    rows = (
        db_session.query(
            JournalEntry.entry_date,
            db.func.sum(Posting.amount),
        )
        .join(JournalEntry, Posting.journal_entry_id == JournalEntry.id)
        .filter(
            Posting.ledger_account_id == ledger_account_id,
            JournalEntry.scenario_id == scenario_id,
        )
        .group_by(JournalEntry.entry_date)
        .all()
    )
    return dict(rows)


def _posted_loan_linked_ledger(loan_account_id, scenario_id):
    """Return a loan's linked ledger row, or None when it has no OPENING posting.

    The entry guard the posting window rests on, held in one place so the
    scalar and the per-period form cannot disagree about which loans the ledger
    can answer for.  A loan gets exactly one OPENING-kind leg on its linked
    ledger per scenario (the origination anchor correction, whose linked leg is
    ``-original_principal``), so its absence means the loan is not configured in
    this scenario -- an unconfigured loan, or a what-if the opening was never
    posted into.

    Args:
        loan_account_id: The loan account whose linked ledger to resolve.
        scenario_id: The budget scenario to scope to.

    Returns:
        The linked :class:`~app.models.ledger_account.LedgerAccount`, or ``None``
        when no OPENING leg is posted on it in *scenario_id*.

    Raises:
        PostingError: If the loan account has no linked ledger account at all (a
            broken chart-of-accounts pairing).
    """
    # pylint: disable=import-outside-toplevel  -- same lazy-app-import
    # convention every helper in this module follows.
    from app import ref_cache
    from app.enums import PostingKindEnum
    from app.extensions import db
    from app.models.journal_entry import JournalEntry, Posting
    from app.services.posting_reads import _ledger_account_for

    linked = _ledger_account_for(loan_account_id)
    has_opening = db.session.query(
        db.session.query(Posting.id)
        .join(JournalEntry, Posting.journal_entry_id == JournalEntry.id)
        .filter(
            Posting.ledger_account_id == linked.id,
            Posting.posting_kind_id == ref_cache.posting_kind_id(
                PostingKindEnum.OPENING,
            ),
            JournalEntry.scenario_id == scenario_id,
        )
        .exists()
    ).scalar()
    return linked if has_opening else None


def posted_loan_balance_at(loan_account_id, scenario_id, as_of):
    """Return what the POSTED ledger says a loan owed on a date, or None.

    ``owed(as_of) = round_money(-(sum of the loan's linked-ledger postings,
    scenario-scoped, whose ``entry_date`` is on or before *as_of*))`` -- the
    opening (``-original_principal``), every settled payment's net principal (the
    Step-2 cash leg plus the Step-4 split correction), and every true-up.

    **This is the test suite's own window onto the general ledger, and that is
    deliberate.**  Plan step E1e deleted the production readers this replaces
    (``loan_posting_service.confirmed_loan_balance_at`` / ``_map``): the balance
    seam answers a loan from the event FOLD (steps C3b1 / C3b3 / E1d-b) and the
    balance sheet reads the postings through
    :mod:`app.services.ledger_report_service`, so the sum-of-postings
    balance-at-T had no production caller left -- its only remaining job was to be
    the counterparty the fold and the resolver are graded against.  An oracle's
    window belongs on the oracle's side: the reconciliation suite already states
    the rule (``_independent_loan_linked_net`` reads through a DIFFERENT join
    shape than the production readers "so the two cannot share a lookup bug"), and
    a window living here cannot be re-exported into a screen.

    Two deliberate differences from the deleted reader, both stated so a caller is
    not surprised:

    * it answers ANY date, where the reader raised for ``as_of > today``.  That
      raise was a DOMAIN guard forcing production callers to route a future date
      to the projection; there are no such callers now.  A future date sums the
      same postings (every posted entry is dated today or earlier), so it carries
      the confirmed balance FLAT -- which is exactly what the deleted per-period
      reader did, and what the early-settle parallel run reads at period ends
      beyond today.
    * the per-period form (:func:`posted_loan_balance_map`) is a plain
      per-period call, not a prefix-sum over one grouped load.  The batching
      existed for production read cost; an oracle wants the simplest derivation
      that can be checked by eye.

    Args:
        loan_account_id: The loan account whose posted balance to read.
        scenario_id: The budget scenario to scope to (postings are
            scenario-scoped via ``journal_entries.scenario_id``).
        as_of: The evaluation date.  Only postings whose ``entry_date`` is on or
            before it are summed.

    Returns:
        The posted balance owed as a cent-quantized ``Decimal``, or ``None`` when
        the loan has no OPENING posting in the scenario.
    """
    # pylint: disable=import-outside-toplevel  -- same lazy-app-import
    # convention every helper in this module follows.
    from app.extensions import db
    from app.models.journal_entry import JournalEntry, Posting
    from app.utils.money import round_money

    linked = _posted_loan_linked_ledger(loan_account_id, scenario_id)
    if linked is None:
        return None
    net = (
        db.session.query(
            db.func.coalesce(db.func.sum(Posting.amount), Decimal("0.00"))
        )
        .join(JournalEntry, Posting.journal_entry_id == JournalEntry.id)
        .filter(
            Posting.ledger_account_id == linked.id,
            JournalEntry.scenario_id == scenario_id,
            JournalEntry.entry_date <= as_of,
        )
        .scalar()
    )
    # Debit-positive ledger: the linked net is -(owed).  Written ``0 - net``
    # rather than ``-net`` so a zero net (a loan read before its opening's date)
    # yields ``0.00``, never ``-0.00``.
    return round_money(Decimal("0.00") - net)


def posted_loan_balance_map(loan_account_id, scenario_id, periods):
    """Return each period's posted loan balance, keyed by period id, or None.

    The per-period form of :func:`posted_loan_balance_at`, evaluated at each
    period's END date.  A posting can fall mid-period under the one clock (step
    C2), so a payment settled during period P must count in P's balance, which
    ``entry_date <= <the period's last covered day>`` selects.

    **It DERIVES that day rather than reading it off the row** (plan step
    ``pay_calendar:C4-c``, which dropped the column it used to read).  The
    owner's calendar is resolved once and each requested period's derived twin
    supplies the bound, which is :func:`period_window`'s shape and it is here
    for the same reason: a period's end is the day before the NEXT payday, so
    it is a property of the whole payday set rather than of one row.

    **It is the scalar per period, not an independent derivation.**  Comparing
    this against :func:`posted_loan_balance_at` at the same date is therefore
    ``f(x) == f(x)`` -- pin a VALUE, never that identity.  Nor is it directly
    comparable to the production map
    (:func:`app.services.balance_at.positions_period_map`) except over ELAPSED
    periods: that map clamps a BEGUN period to ``min(period.end, ctx.as_of)``
    and answers a FUTURE period from the forward plan, where this window carries
    the confirmed sum flat.  The keys line up; the values only do in the past.

    Returns ``None`` -- not a map of zeros -- when the loan has no OPENING
    posting in the scenario, so a caller can tell "the ledger cannot answer for
    this loan" from "the ledger says zero".

    Args:
        loan_account_id: The loan account whose per-period balances to read.
        scenario_id: The budget scenario to scope to.
        periods: The ``PayPeriod`` rows to key by, in any order and all
            belonging to one user.  Must be non-empty -- an empty request has
            no owner to resolve a calendar for.  The result keys by
            ``period.id`` in PAYDAY order, which is the calendar's.  Postings
            in periods outside this list are still counted -- each period's
            value is a cumulative, not a slice.

    Returns:
        A ``{period.id: Decimal}`` mapping, or ``None`` when the loan has no
        OPENING posting in the scenario.
    """
    from app.services.pay_calendar import (  # pylint: disable=import-outside-toplevel
        calendar_for,
    )

    if _posted_loan_linked_ledger(loan_account_id, scenario_id) is None:
        return None
    # Materialised once: the owner is read off the first row and the id set off
    # all of them, so a generator argument would be half consumed by the time
    # the owner is asked for.  Every caller passes a list today; this is what
    # keeps that from being a precondition nobody states.
    rows = tuple(periods)
    assert rows, "posted_loan_balance_map needs at least one period to key by"
    owners = {period.user_id for period in rows}
    assert len(owners) == 1, (
        f"posted_loan_balance_map resolves ONE owner's calendar and was given "
        f"periods from {sorted(owners)}; the rest would be silently dropped"
    )
    wanted = {period.id for period in rows}
    calendar = calendar_for(rows[0].user_id)
    answer = {
        period.period_id: posted_loan_balance_at(
            loan_account_id, scenario_id, period.end_date,
        )
        for period in calendar.saved()
        if period.period_id in wanted
    }
    # **One key per REQUESTED period, which is the contract the stored-column
    # version had for free.**  Selecting from the calendar means a period the
    # calendar does not hold -- unflushed, or another owner's -- would simply
    # vanish from the result, and only two of this helper's callers assert the
    # length.  A short map is a caller reading a KeyError several lines later
    # about a period it did pass in.
    assert set(answer) == wanted, (
        f"periods {sorted(wanted - set(answer))} are not in the owner's saved "
        f"calendar, so they were never flushed or belong to someone else"
    )
    return answer


def find_loan_ledger_account(db_session, loan_account_id, kind):
    """Return the per-loan ledger account of *kind*, or None if not created.

    The per-loan interest / escrow / refund ledger account
    (``loan_account_id`` + the ``kind`` discriminator), lazily minted by the
    Step-4 chart resolver on first use.  Shared by the Step-4 split-service and
    wiring suites.

    Args:
        db_session: The test ``db.session``.
        loan_account_id: The loan whose per-loan ledger account to find.
        kind: The :class:`~app.enums.LedgerAccountKindEnum` member
            (``LOAN_INTEREST`` / ``LOAN_ESCROW`` / ``LOAN_REFUND``).

    Returns:
        The :class:`~app.models.ledger_account.LedgerAccount`, or ``None`` when
        the resolver has not minted it yet.
    """
    # pylint: disable=import-outside-toplevel  -- same lazy-app-import
    # convention every helper in this module follows.
    from app import ref_cache
    from app.models.ledger_account import LedgerAccount

    return (
        db_session.query(LedgerAccount)
        .filter_by(
            loan_account_id=loan_account_id,
            kind_id=ref_cache.ledger_account_kind_id(kind),
        )
        .one_or_none()
    )


#: Plan step ``pay_calendar:C4-c``'s revision, whose ``downgrade()`` is the one
#: statement in this repository that re-creates ``budget.pay_periods.end_date``
#: and ``budget.pay_periods.period_index``.
_C4C_REVISION_FILE = "b7a41e2c9d63_a_pay_period_is_one_fact.py"
_C17A_REVISION_FILE = "6fc77e86d76f_a_pay_schedule_is_a_sequence_of_eras.py"
#: Plan step ``pay_calendar:C17-d-2``'s revision, whose ``downgrade()`` puts
#: ``budget.pay_eras.kind_id`` and ``ref.pay_cadence_kinds`` back -- the
#: objects ``C17-a``'s downgrade drops, so it runs FIRST in the rewind.
_C17D2_REVISION_FILE = "3ec5291ca4e2_a_pay_eras_kind_is_which_columns_it_carries.py"
#: Plan step ``salary:R18-a``'s revision, the RENAME of ``salary.paycheck_
#: deductions`` to ``salary.paycheck_lines`` (with ``ref.deduction_timings``
#: to ``ref.paycheck_line_kinds`` and the owning arm's column); its
#: ``downgrade()`` is the one statement that puts the old names back.
_R18A_REVISION_FILE = "0a4d2c3e89f8_a_paycheck_is_a_list_of_lines.py"
#: Plan step ``salary:R18-b``'s revision, which seeds the two EARNING kinds
#: whose names the pre-rename column cannot hold; its ``downgrade()`` runs
#: FIRST in the rewind (Alembic's newest-first order) and its ``upgrade()``
#: LAST in the replay.
_R18B_REVISION_FILE = "6c15d2a97b78_a_paycheck_has_earning_lines.py"


def restore_pay_schedule_rhythm_columns(db_session):
    """Put the rhythm columns back on ``budget.pay_schedule``, for one older statement.

    :func:`restore_pay_period_derived_columns`' sibling, for the same reason.
    Plan step ``pay_calendar:C17-a`` moved ``cadence_days``, ``shift_id`` and
    ``nominal_anchor`` off the schedule row and onto ``budget.pay_eras``; four
    older migrations read or write the first of those on the row --
    ``af8254074bef``'s backfill, ``f1c8b3d5e920``'s, ``b7a41e2c9d63``'s
    downgrade and ``f2b7c40d918e``'s -- and a test that drives one of their
    callables against the test database meets ``UndefinedColumn`` where it
    used to find the schema it expected.

    **It adds the columns and fills them from each owner's LATEST era, and it
    does NOT run C17-a's ``downgrade()``, which is the difference
    :func:`relax_pay_schedule_shift_not_null` already argued for one column
    over.**  That downgrade also DROPS ``budget.pay_eras``, and every reader
    the head mapper has -- ``get_schedule``, ``calendar_for``, every fixture
    that derives a span -- selects that table; a test whose subject is an old
    statement reading the row's cadence would then break on every read it
    makes afterwards.  What the old statement needs is the COLUMN, so that is
    what comes back: three nullable columns, filled by the migration's own
    ``_RESTORE_RHYTHM_SQL`` (the shipped statement, not a copy), with the era
    table left standing and no key, CHECK or NOT NULL re-added.  A test that
    then deletes a schedule row must retire the owner's eras first, exactly
    as it must delete their paydays first.

    Idempotent (``ADD COLUMN IF NOT EXISTS``), because a round-trip case
    replays an older revision's ``downgrade`` twice.  Everything the sibling
    helper says about scope, locks and the absence of a restore applies
    unchanged, including ledger row ``balance:P79``.

    Args:
        db_session: The test ``db.session``, in the scope that currently holds
            the schedule table's locks.
    """
    # pylint: disable=import-outside-toplevel
    from sqlalchemy import text

    db_session.commit()
    if _pay_schedule_carries_a_cadence(db_session):
        # Already restored -- by an earlier call, or by
        # :func:`rewind_pay_schedule_rhythm`, after which the era table this
        # would fill from is gone.
        return
    # Pylint: ``protected-access`` -- the migration's own restore statement,
    # so this helper cannot drift from what the shipped downgrade writes.
    restore_sql = load_migration_module(
        _C17A_REVISION_FILE,
    )._RESTORE_RHYTHM_SQL  # pylint: disable=protected-access
    db_session.execute(text(
        "ALTER TABLE budget.pay_schedule "
        "ADD COLUMN cadence_days INTEGER, "
        "ADD COLUMN shift_id INTEGER, "
        "ADD COLUMN nominal_anchor DATE"
    ))
    db_session.execute(text(restore_sql))
    db_session.commit()


def rewind_pay_schedule_rhythm(db_session):
    """Run plan step ``pay_calendar:C17-a``'s own ``downgrade()``.

    The WHOLE rewind, for the few cases whose subject is an older revision's
    statement against the schema exactly as that revision's chain leaves it:
    the rhythm columns back on ``budget.pay_schedule`` with their ``NOT
    NULL``, ``ck_pay_schedule_cadence_range`` and ``fk_pay_schedule_shift_id``
    re-added, and ``budget.pay_eras`` DROPPED with ``ref.pay_cadence_kinds``.
    A case that needs the CHECK to fire, or that drops ``budget.pay_schedule``
    itself, needs this; a case that only reads the cadence off the row wants
    :func:`restore_pay_schedule_rhythm_columns`, which leaves every head
    reader working.  Call it FIRST: that helper skips once the columns exist,
    and the restore statement it would run reads the table this drops.

    **It runs ``C17-d-2``'s downgrade before ``C17-a``'s** (plan step
    ``pay_calendar:C17-d-2``), Alembic's newest-first order: the head has no
    ``kind_id`` and no ``ref.pay_cadence_kinds`` for ``C17-a``'s downgrade
    to drop, and the later revision's downgrade is what puts them back.  It
    refuses a day-of-month era, as that downgrade does, and a schedule row
    holding no era, as ``C17-a``'s does.

    Args:
        db_session: The test ``db.session``, in the scope that currently holds
            the schedule table's locks.
    """
    run_migration_callable(
        load_migration_module(_C17D2_REVISION_FILE).downgrade, db_session,
    )
    run_migration_callable(
        load_migration_module(_C17A_REVISION_FILE).downgrade, db_session,
    )


def _pay_schedule_carries_a_cadence(db_session):
    """Return whether ``budget.pay_schedule.cadence_days`` exists right now."""
    # pylint: disable=import-outside-toplevel
    from sqlalchemy import text

    return bool(db_session.execute(text(
        "SELECT 1 FROM information_schema.columns "
        " WHERE table_schema = 'budget' AND table_name = 'pay_schedule' "
        "   AND column_name = 'cadence_days'"
    )).scalar())


def rewind_paycheck_lines_rename(db_session):
    """Run plan step ``salary:R18-a``'s own ``downgrade()``: the old table names back.

    **For a test whose subject is an EARLIER revision's shipped SQL**, which
    names ``salary.paycheck_deductions``, ``ref.deduction_timings`` or
    ``budget.recurrence_rules.paycheck_deduction_id`` -- ``salary:R15-b``'s
    cadence migration, the C-42 index repair, ``salary:R14-a``'s funding
    backfill.  Head renamed all three (ruling **R-SAL38**), so those callables
    meet ``UndefinedTable`` where they used to find the schema they expected:
    :func:`rewind_pay_schedule_rhythm`'s shape, Alembic's newest-first order.

    What it leaves is head's schema with the three names restored -- every
    constraint, index, sequence and trigger under its old name, the two ref
    rows named ``pre_tax`` / ``post_tax`` -- and NOT any particular revision.
    The ORM models on this tree map the NEW names, so an ORM read of a line
    between this and :func:`replay_paycheck_lines_rename` finds no table;
    seed through the ORM first, rewind, drive the old statement in SQL, and
    replay before reading a line back through the model.

    **It runs R18-b's downgrade before R18-a's**: head carries the two
    earning-kind rows R18-b seeded, and R18-a's downgrade REFUSES to narrow
    the name column while a name longer than ten characters stands, which is
    the refusal ``test_r18a_paycheck_lines_rename`` grades; R18-b's downgrade
    in turn refuses while any line CARRIES an earning kind, so a case that
    seeded one must delete it before rewinding.

    Args:
        db_session: The test ``db.session``, in the scope that holds the
            tables' locks (see :func:`run_migration_callable`).
    """
    run_migration_callable(
        load_migration_module(_R18B_REVISION_FILE).downgrade, db_session,
    )
    run_migration_callable(
        load_migration_module(_R18A_REVISION_FILE).downgrade, db_session,
    )


def replay_paycheck_lines_rename(db_session):
    """Run plan step ``salary:R18-a``'s own ``upgrade()`` after a rewind, then R18-b's.

    :func:`rewind_paycheck_lines_rename`'s inverse: the head names back, so a
    test that drove an older revision's statement can read the result through
    the ORM models this tree maps.  Not needed for isolation -- the ``db``
    fixture re-clones the per-worker database for every test -- only for a
    case that reads lines back after the older statement ran.

    Args:
        db_session: The test ``db.session``, in the scope that holds the
            tables' locks.
    """
    run_migration_callable(
        load_migration_module(_R18A_REVISION_FILE).upgrade, db_session,
    )
    run_migration_callable(
        load_migration_module(_R18B_REVISION_FILE).upgrade, db_session,
    )


#: Plan step ``recurrence:R5-a``'s revision, which dropped the rule's due day.
_R5A_REVISION_FILE = "1c569c51b449_a_rules_day_is_its_due_day.py"


def restore_rule_due_day_column(db_session):
    """Re-create ``budget.recurrence_rules.due_day_of_month``, which R5-a dropped.

    **For a test whose subject is an EARLIER revision's shipped SQL**, the
    reason :func:`restore_pay_period_derived_columns` exists, and built the
    same way: it runs plan step ``recurrence:R5-a``'s own ``downgrade()``
    (ruling R-R96) rather than issuing DDL of its own, so the column comes back
    with its CHECK exactly as the shipped statement rebuilds it -- nullable and
    empty.  Revision ``542c61e48ee8`` (plan step ``salary:R15-b``) reads the
    column in its own SQL, so a test driving that revision meets
    ``UndefinedColumn`` at head without it.  Alembic undoes the NEWEST revision
    first, so a caller rewinding further runs this before its older rewinds.

    It does not put the database at any particular revision.  The ORM model on
    this tree does not map the column, so an ORM insert leaves it ``NULL``.

    Args:
        db_session: The test ``db.session``, in the scope that holds the
            table's locks (see :func:`run_migration_callable`).
    """
    run_migration_callable(
        load_migration_module(_R5A_REVISION_FILE).downgrade, db_session,
    )


def restore_pay_period_derived_columns(db_session):
    """Re-create the two ``budget.pay_periods`` columns C4-c dropped.

    **For a test whose subject is an EARLIER revision's shipped SQL**, which is
    the only reason this exists.  Plan step ``pay_calendar:C4-c`` dropped
    ``end_date`` and ``period_index``; several migrations older than it read
    those columns, and a test that drives one of their callables against the
    test database therefore meets ``UndefinedColumn`` where it used to find the
    schema it expected.

    It runs that revision's own ``downgrade()`` rather than issuing DDL of its
    own, so the columns come back with the constraints, the NOT NULLs and --
    for every row already in the table -- the VALUES the shipped statement
    rebuilds.  A hand-written ``ADD COLUMN`` here would be a second statement of
    the schema that could drift from the migration without failing anything.

    **What it does NOT do, stated because the difference is the whole risk.**
    It does not put the database at any particular revision: everything else
    stays at head, so this is head's schema plus two restored columns.  That is
    enough for a test whose subject is a STATEMENT reading those two columns,
    and it is not enough for one whose subject is the whole schema at its own
    revision -- for that, every revision after it has to be undone in order,
    which is what Alembic does and what no helper here pretends to.  *The
    general shape -- migration tests in this suite drive their callables at
    HEAD rather than at the revision's own parent -- is a latent category error
    that C4-c is simply the first step to make fire; it is recorded as ledger
    row ``balance:P79`` rather than fixed inside this step.*

    No restore is needed afterwards: the ``db`` fixture drops and re-clones the
    per-worker database for every test, so a schema this leaves off head cannot
    reach the next one.

    **Call it on the session whose transaction is the one holding locks**, and
    in practice that means BEFORE entering a nested ``app.app_context()`` rather
    than inside one.  ``ADD COLUMN`` takes ACCESS EXCLUSIVE; Flask-SQLAlchemy
    scopes its session to the app context and a test already runs inside one,
    so a NESTED context gets a second session on a second connection while the
    outer one sits idle-in-transaction holding ACCESS SHARE on
    ``budget.pay_periods`` from whatever its fixtures last read.  The two
    conflict and the DDL dies on the cluster's 10-second ``lock_timeout`` --
    measured rather than predicted, and a confusing failure to meet cold.  The
    commit below ends the handed-in session's own transaction, which is every
    conflict this function can reach; a session in another scope is not
    addressable from here.

    Args:
        db_session: The test ``db.session``, in the scope that currently holds
            this table's locks.
    """
    # C4-c's downgrade rebuilds the stored end from
    # ``budget.pay_schedule.cadence_days``, which plan step
    # ``pay_calendar:C17-a`` moved onto the era relation -- so the schedule's
    # rhythm columns come back FIRST, or the statement this replays cannot.
    restore_pay_schedule_rhythm_columns(db_session)
    run_migration_callable(
        load_migration_module(_C4C_REVISION_FILE).downgrade, db_session,
    )


def relax_pay_schedule_shift_not_null(db_session):
    """Let ``budget.pay_schedule.shift_id`` be omitted, for one test.

    :func:`restore_pay_period_derived_columns`' MIRROR, and the mirror is the
    point.  That helper exists because a later revision REMOVED columns an
    older statement reads; this one exists because plan step
    ``pay_calendar:C14-b`` ADDED a ``NOT NULL`` column an older statement does
    not write.  Both break the same assumption -- that head's schema is a
    superset an old statement can still run against -- and it holds for READS
    and not for an insert into a table that has since gained a required
    column.

    Concretely: ``af8254074bef``'s backfill is
    ``INSERT INTO budget.pay_schedule (user_id, cadence_days) SELECT ...``, and
    at head PostgreSQL refuses that row with a ``NotNullViolation`` naming a
    column the statement predates by 95 revisions.

    **It drops the NOT NULL rather than running C14-b's ``downgrade()``, and
    the difference was measured rather than reasoned.**  The downgrade removes
    the COLUMN, and every reader in the test then breaks instead: the mapper at
    head still selects ``shift_id``, so the assertions that read the backfilled
    row back through the ORM die on ``UndefinedColumn``.  What the old
    statement needs is not the column's absence but permission to omit it, and
    that is the smaller change -- it leaves the column, the foreign key and
    every ORM reader intact.  Hand-written DDL here therefore duplicates no
    migration statement: it relaxes one constraint for the length of a test
    rather than restating a schema change.

    Everything :func:`restore_pay_period_derived_columns` says about scope
    applies unchanged -- the database is left at head minus one constraint
    rather than at any particular revision, no restore is needed because the
    ``db`` fixture re-clones per test, and the call belongs on the session
    holding the table's locks.  This is the same latent category error ledger
    row ``balance:P79`` records, met from the other direction.

    Args:
        db_session: The test ``db.session``, in the scope that currently holds
            this table's locks.
    """
    # pylint: disable=import-outside-toplevel
    from sqlalchemy import text

    db_session.commit()
    db_session.execute(text(
        "ALTER TABLE budget.pay_schedule ALTER COLUMN shift_id DROP NOT NULL"
    ))
    db_session.commit()


def run_migration_callable(callable_, db_session):
    """Run one migration ``upgrade``/``downgrade`` against the test connection.

    **The one statement of this bootstrap.**  Eighteen files under
    ``tests/test_models/`` hand-copy a ``MigrationContext`` / ``Operations`` /
    ``patch.object(op, "get_bind")`` block; consolidating all of them is ledger
    row **P79**'s territory rather than a column drop's, so this is the copy
    the files that plan step ``pay_calendar:C4-c`` touches share, and it exists
    because that step had otherwise written a second one three lines from
    :func:`restore_pay_period_derived_columns`'s (adversarial review,
    2026-09-01).  The two had already drifted: one committed before configuring
    the context and the other relied on every caller having done so.

    It commits FIRST, and that is the lock rather than tidiness: DDL takes
    ACCESS EXCLUSIVE, and a session left idle-in-transaction holding ACCESS
    SHARE on the table blocks it until the cluster's ``lock_timeout``.
    Committing ends the handed-in session's own transaction, which is every
    conflict this function can reach; a session in another app-context scope is
    not addressable from here, so a caller inside a NESTED ``app.app_context()``
    must have committed the outer one.

    Args:
        callable_: The migration module's ``upgrade`` or ``downgrade``.
        db_session: The test ``db.session``, in the scope that holds the
            table's locks.
    """
    # Pylint: ``import-outside-toplevel`` -- alembic's operation plumbing is
    # needed by this one helper, and importing it at module scope would put it
    # in the import path of every test that touches this file.
    from alembic import op  # pylint: disable=import-outside-toplevel
    from alembic.operations import (  # pylint: disable=import-outside-toplevel
        Operations,
    )
    from alembic.runtime.migration import (  # pylint: disable=import-outside-toplevel
        MigrationContext,
    )
    from unittest.mock import patch  # pylint: disable=import-outside-toplevel

    db_session.commit()
    connection = db_session.connection()
    ctx = MigrationContext.configure(connection=connection)
    with Operations.context(ctx):
        with patch.object(op, "get_bind", return_value=connection):
            callable_()
    db_session.commit()


#: PostgreSQL's SQLSTATE for ``raise_exception``: what a PL/pgSQL ``RAISE
#: EXCEPTION`` with no ``ERRCODE`` reports, which is how every trigger and
#: rule function this schema installs refuses a write.
RAISE_EXCEPTION_SQLSTATE = "P0001"


@contextmanager
def refused_by_database_rule(match):
    """Expect the block to be refused by one of the DATABASE's own rules.

    The suite's one statement of "a trigger refused this" (ruling
    balance:R-BAL210, "Check the error code"): the refusal must carry
    PostgreSQL's SQLSTATE :data:`RAISE_EXCEPTION_SQLSTATE` and a message
    matching *match*.  It asserts the code rather than the DB-API exception
    CLASS because the class is the driver's choice and it moved with the
    driver: a ``RAISE`` is an ``InternalError`` under psycopg2 and a
    ``ProgrammingError`` under psycopg 3 (plan step balance:X-dj).  The code
    is the server's own answer, read through
    :func:`app.utils.db_errors.sqlstate_of`.

    Args:
        match: A regular expression the refusal's message must match
            (``pytest.raises``' ``match``).  Required: the code says a rule
            refused, the message says WHICH.

    Yields:
        The ``pytest.ExceptionInfo``, populated once the block has raised.
    """
    with pytest.raises(sa_exc.DBAPIError, match=match) as excinfo:
        yield excinfo
    assert sqlstate_of(excinfo.value) == RAISE_EXCEPTION_SQLSTATE, (
        f"refused with SQLSTATE {sqlstate_of(excinfo.value)!r}, not a "
        f"database rule's {RAISE_EXCEPTION_SQLSTATE}: {excinfo.value}"
    )


def constraint_name_from(exc):
    """Return the named constraint reported on an :class:`IntegrityError`.

    Reads ``exc.orig.diag.constraint_name`` -- the structured field psycopg
    surfaces from the PostgreSQL error packet -- so a test asserting WHICH
    CHECK fired does not depend on the brittle prose of the error message.

    Shared because it was written twice: the C-24 range/CHECK sweep and the
    ``salary:S3-b`` terminal-year suite carried byte-identical copies, and
    ``pylint app/`` never sees ``tests/``, so ``duplicate-code`` could not
    find it.

    Args:
        exc: The :class:`sqlalchemy.exc.IntegrityError` a refused write
            raised.

    Returns:
        The constraint name, or ``None`` when the driver reported none --
        which is itself a useful answer, since a NOT NULL violation and a
        named CHECK violation are different refusals.
    """
    orig = getattr(exc, "orig", None)
    if orig is None:
        return None
    diag = getattr(orig, "diag", None)
    if diag is None:
        return None
    return getattr(diag, "constraint_name", None)


def load_migration_module(filename):
    """Load an Alembic migration module by filename via importlib.

    ``migrations/versions`` has no ``__init__``, so a migration cannot be
    imported as an ordinary package member.  This loads one by absolute path so
    a test can invoke a migration's module-level helpers directly (e.g. the
    posting-ledger backfill's ``_backfill_settled_transfers``).  Shared by the
    posting-ledger backfill suite and the Commit-6 reconciliation oracle so the
    importlib boilerplate lives in one place (a duplicate-code finding).

    Args:
        filename: The migration module's filename (e.g.
            ``"db239773c2fd_create_journal_entries_account_postings_.py"``),
            resolved under ``<repo>/migrations/versions``.

    Returns:
        The loaded migration module object.
    """
    versions_dir = (
        pathlib.Path(__file__).resolve().parents[1] / "migrations" / "versions"
    )
    path = versions_dir / filename
    spec = importlib.util.spec_from_file_location(path.stem, str(path))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def enable_mfa(db_session, user_id, backup_codes=()):
    """Turn two-factor on for *user_id* with a fresh secret, and commit.

    A FRESH secret per call, so two users (or two tests) never share a
    replay-prevention step.  Nine files carried a private copy of this before
    plan#2173 wrote it here; they still do.

    Args:
        db_session: The test ``db.session``.
        user_id: The ``auth.users.id`` to enrol.
        backup_codes: Plaintext backup codes to store hashed; none by default.

    Returns:
        The plaintext base32 secret, for ``pyotp.TOTP(secret)`` codes.
    """
    secret = mfa_service.generate_totp_secret()
    db_session.add(MfaConfig(
        user_id=user_id,
        is_enabled=True,
        totp_secret_encrypted=mfa_service.encrypt_secret(secret),
        backup_codes=mfa_service.hash_backup_codes(list(backup_codes), rounds=4),
        confirmed_at=_real_datetime.now(_real_timezone.utc),
    ))
    db_session.commit()
    return secret


def step_down_through_the_journal_link_drop(db_session):
    """Run revision ``1712c506ee99``'s downgrade: the journal entries' two old links come back.

    **The ONE statement of what a migration case below plan step
    ``balance:X-bi-6-5``'s second leaf does first** (card plan#1845).  The
    suite's database is built at head, where ``budget.journal_entries`` has no
    ``transfer_id`` or ``transaction_id`` and ``ref.posting_sources`` no
    ``transfer`` or ``transaction`` row, so a lower revision whose upgrade or
    downgrade names either -- the posting backfills, the shape-C split's
    unlink -- fails on it.  This puts back both columns (EMPTY: the revision
    deleted every entry that carried one), their keys, their two partial
    indexes and both reference rows, and the case then walks its own revisions
    exactly as it did before the drop.  The deleted entries are not re-created
    (ruling plan#1867).

    **It does not come back up**, deliberately: each case's database is its
    own and is dropped after it, and the upgrade would delete the legacy
    entries a revision below had written for the case to read.

    Args:
        db_session: The test ``db.session``.
    """
    run_migration_callable(
        load_migration_module(
            "1712c506ee99_a_ledger_entry_links_only_its_movement.py",
        ).downgrade,
        db_session,
    )


def step_down_through_the_transfer_column_drop(db_session):
    """Run revisions ``1712c506ee99``'s and ``c956c1d83108``'s downgrades, in that order.

    **The ONE statement of what a migration case below plan step
    ``balance:X-bi-6-5``'s column drop does first** (card plan#1844).  The
    suite's database is built at head, where ``budget.transactions`` has no
    ``transfer_id``, so a lower revision whose upgrade or downgrade names the
    column -- ``82f7b7ed4332``'s twins, ``c4a4e7d1b9f2``'s row-only deleted-row
    rule, ``596408fab6f1``'s pricing CHECK -- fails on it.  Every such case is
    below ``1712c506ee99`` too, so this steps down through it first
    (:func:`step_down_through_the_journal_link_drop` says what that restores),
    then through ``c956c1d83108``: that puts back the column (EMPTY: no row
    names a transfer), its key, its two indexes, its two CHECKs and the
    ``parent_transfer`` source, and the case then walks its own revisions
    exactly as it did before the drop.  The twin copy that revision dropped is
    not re-created (ruling plan#1851), and ``82f7b7ed4332``'s downgrade reads a
    missing copy as an empty one.

    **It does not come back up**, deliberately: each case's database is its
    own and is dropped after it, and the upgrade would drop a twin copy a
    revision below had kept for the case to read.

    Args:
        db_session: The test ``db.session``.
    """
    step_down_through_the_journal_link_drop(db_session)
    run_migration_callable(
        load_migration_module(
            "c956c1d83108_a_transaction_names_no_transfer.py",
        ).downgrade,
        db_session,
    )


def load_init_database_module():
    """Load ``scripts/init_database.py`` by path (it is not a package member).

    ``scripts`` has no ``__init__``, so the deploy host is loaded by absolute
    path -- the same importlib idiom :func:`load_migration_module` uses -- so a
    test can run its one-transaction deploy (``initialise_database``) or one of
    its pieces directly.  The script
    mutates ``DATABASE_URL_APP`` to ``""`` at import time (its deploy-host
    owner-role override, which must run BEFORE the ``app`` import), a
    process-global side effect this restores around the load so it never leaks
    into the test session.  Shared by the loan and account backfill suites so
    the importlib + env-restore boilerplate lives in one place (a duplicate-code
    finding otherwise).

    Returns:
        The loaded ``init_database`` module object, exposing
        ``initialise_database`` (entrypoint step 3, ONE transaction since plan
        step ``balance:X-cv``) and the pieces it runs.
    """
    script_path = (
        pathlib.Path(__file__).resolve().parents[1] / "scripts" / "init_database.py"
    )
    saved = os.environ.get("DATABASE_URL_APP")
    spec = importlib.util.spec_from_file_location(
        script_path.stem, str(script_path),
    )
    module = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(module)
    finally:
        if saved is None:
            os.environ.pop("DATABASE_URL_APP", None)
        else:
            os.environ["DATABASE_URL_APP"] = saved
    return module


_UNSET_SETTLED_ON = object()


def settle_instant_on(day):
    """Return a deterministic event instant on a given civil date (noon UTC).

    A test-side helper for pinning an ASSERTION's recording instant to a
    specific day without a wall-clock read -- :func:`reassert_balance_on` takes
    one.  Noon UTC is the same civil day in the display zone (Eastern), so a
    day pinned this way reads back as that day.

    **It no longer serves a SETTLE, and that is plan step X-f1** (ruling R-EC).
    A settled transaction stores its civil day in ``transactions.settled_on``,
    so :func:`create_settled_transfer` and
    :func:`create_settled_cash_transaction` take the DAY directly and a caller
    that used to wrap it here passes it plain.  What remains is the assertion
    side, where ``created_at`` is genuinely an instant.

    Args:
        day: The civil :class:`datetime.date` to place the event on.

    Returns:
        A timezone-aware :class:`datetime.datetime` at noon UTC on *day*.
    """
    from datetime import time, timezone  # pylint: disable=import-outside-toplevel
    return _real_datetime.combine(day, time(12, 0), tzinfo=timezone.utc)


def register_form_data(**overrides):
    """Return a complete, valid POST body for ``/register``.

    **One place that knows what the registration form requires**, added at plan
    step X-ad-a when that grew from four fields to seven: the form now asks for
    the owner's real pay schedule because registration stopped inventing one
    (ruling **R-DB**, finding **N-123**).  Twenty-odd call sites across four
    test modules post this body, and a field added to
    ``RegisterSchema`` without a home here would otherwise be twenty-odd edits
    -- which is the shape of test churn that ends in a suite where half the
    posts are subtly different for no stated reason.

    ``last_payday`` is the USER's today (``display_today``, ruling R-DH (b)),
    never ``date.today()``: the service bounds it against the same clock, and a
    process pinned to UTC is already tomorrow at 8pm Eastern -- so a
    process-clock default would be a FUTURE payday, refused, for four hours
    every day.  Today is the safest point in the accepted window
    ``[today - cadence + 1, today]`` because it is valid at every cadence.

    Args:
        **overrides: Field values to replace or add.  Pass ``email`` and
            ``display_name`` per test; pass a field explicitly to exercise its
            refusal (e.g. ``last_payday=""`` for the missing-date path).

    Returns:
        A ``dict`` suitable for ``client.post("/register", data=...)``.
    """
    # pylint: disable=import-outside-toplevel
    from app import ref_cache
    from app.config import BaseConfig
    from app.utils.dates import display_today
    body = {
        "email": "newuser@example.com",
        "display_name": "New User",
        "password": "securepass123",
        "confirm_password": "securepass123",
        "last_payday": display_today().isoformat(),
        # The cadence-kind control's three radio arms and every arm's boxes
        # (plan step pay_calendar:C17-d-3, ruling R-PC84): a browser posts
        # the checked arm's token AND every rendered box, the unchosen arms'
        # blank.  Spelled by ``cadence_form_values`` so this body and the
        # settings doors' cases state the wire one way.
        **cadence_form_values(),
        # The payday convention the form's <select> renders (plan step
        # pay_calendar:C14-b), as the ``ref.business_day_shifts`` id that
        # control's chosen <option> carries -- a browser posts an id, not an
        # enum member, and a body that posted anything else would exercise a
        # payload no browser can produce.  ``none`` is what the page
        # preselects for a new owner.
        "shift": str(ref_cache.business_day_shift_id(BusinessDayShiftEnum.NONE)),
        "num_periods": str(BaseConfig.DEFAULT_PAY_PERIOD_HORIZON),
        # EMPTY, because that is what a browser submits for the optional
        # pay-history date nobody touched (plan step balance:X-bh-2): an HTML
        # form posts every control it renders, so a body that omitted the key
        # would exercise a payload no browser can produce.  The schema maps it
        # to ``None``, which means NOT STATED -- the engine counts only the
        # paydays the app records.
        "history_opens_on": "",
    }
    body.update(overrides)
    return body


def cadence_form_values(cadence=None):
    """Return what a schedule form's cadence-kind control and its arms post.

    Plan step ``pay_calendar:C17-d-3``, ruling **R-PC84**: the four doors
    render three RADIO ARMS -- every N days, monthly on a day, twice a month
    on two days -- each holding its own number boxes, and a browser submits
    the checked arm's token under ``cadence_kind`` together with EVERY
    rendered box, the unchosen arms' boxes blank (the every-N-days box
    carries the app's default whichever arm is checked, because the form
    pre-fills it).  A body that posted only the chosen arm would exercise a
    payload no browser can produce, which is the defect class
    :func:`register_form_data`'s own docstring records.

    **The tokens and keys are spelled HERE by hand, not read off the
    schema's wire table**, so a test posting this body grades the
    application's spelling against an independent one: a token or a key
    that drifted in ``_pay_rhythm`` or in the macro would fail here rather
    than agree with itself.

    Args:
        cadence: The :class:`~app.services.pay_rhythm.FixedDays`,
            :class:`~app.services.pay_rhythm.Monthly` or
            :class:`~app.services.pay_rhythm.SemiMonthly` the owner states;
            ``None`` means the form's untouched default, every
            ``DEFAULT_PAY_CADENCE_DAYS`` days.

    Returns:
        dict -- the five wire keys, every value a string as a form posts it.
    """
    # pylint: disable=import-outside-toplevel
    from app.config import BaseConfig
    from app.services.pay_rhythm import FixedDays, Monthly, SemiMonthly
    if cadence is None:
        cadence = FixedDays(BaseConfig.DEFAULT_PAY_CADENCE_DAYS)
    body = {
        "cadence_kind": "fixed_days",
        "cadence_days": str(BaseConfig.DEFAULT_PAY_CADENCE_DAYS),
        "day_of_month": "",
        "first_day_of_month": "",
        "second_day_of_month": "",
    }
    if isinstance(cadence, FixedDays):
        body["cadence_days"] = str(cadence.days)
    elif isinstance(cadence, Monthly):
        body["cadence_kind"] = "monthly"
        body["day_of_month"] = str(cadence.day)
    elif isinstance(cadence, SemiMonthly):
        lower, upper = cadence.days
        body["cadence_kind"] = "semi_monthly"
        body["first_day_of_month"] = str(lower)
        body["second_day_of_month"] = str(upper)
    else:
        raise TypeError(f"cadence_form_values() got {cadence!r}, not a cadence")
    return body


def shift_form_value(shift=BusinessDayShiftEnum.NONE):
    """Return what a schedule form's payday-convention ``<select>`` posts.

    The ``ref.business_day_shifts`` id, as a string, because that is what a
    browser sends for the chosen ``<option>`` -- the four schedule forms render
    the control from ``PAYDAY_SHIFT_OPTIONS``, whose values are ids (plan step
    ``pay_calendar:C14-b``).  A test body that posted a member name or omitted
    the key would exercise a payload no browser can produce, which is the
    defect class ``register_form_data``'s own docstring records.

    Args:
        shift: The convention the control is set to, defaulting to
            :attr:`~app.enums.BusinessDayShiftEnum.NONE` -- what every form
            preselects for an owner who has stated nothing.

    Returns:
        str -- the id to put in a form body.
    """
    return str(shift_id_of(shift))


def shift_id_of(shift=BusinessDayShiftEnum.NONE):
    """Return the ``ref.business_day_shifts.id`` a member is stored as.

    For the handful of tests that build a
    :class:`~app.models.pay_era.PayEra` row DIRECTLY rather than through
    ``pay_era_write.mint_era`` -- constraint cases, which need a row
    the database will accept in every respect except the one under test.
    ``shift_id`` is ``NOT NULL`` with no server default (plan step
    ``pay_calendar:C14-b``; the column moved from the schedule row to the era
    at ``C17-a``), so a row built without it fails on the wrong constraint
    and the case passes for the wrong reason.

    Args:
        shift: The convention, defaulting to
            :attr:`~app.enums.BusinessDayShiftEnum.NONE`.

    Returns:
        int -- the ``ref`` id.
    """
    # pylint: disable=import-outside-toplevel
    from app import ref_cache

    return ref_cache.business_day_shift_id(shift)


def rhythm_of(cadence_days, shift=BusinessDayShiftEnum.NONE):
    """Return a :class:`~app.services.pay_rhythm.Rhythm`.

    Plan step ``pay_calendar:C14-b`` made the pay-schedule writers take the
    cadence and the payday convention as ONE value, because the two carry a
    joint rule and a row written through two statements passes through a state
    neither statement means.  Most tests in this suite are about the paydays
    rather than the convention, so this defaults the second half to ``none`` --
    which is what every schedule holds until an owner answers (**R-PC56**) and
    therefore what those tests were already exercising.

    A helper rather than the constructor spelled out at each of the call sites,
    for the reason ``seed_periods`` gives of the batch it wraps: the default is
    stated ONCE, so the day a test needs a real convention it passes one and
    every other case keeps reading as a cadence.

    **It builds a FIXED-DAYS rhythm**, which since plan step
    ``pay_calendar:C17-d-1`` is one kind of cadence value
    (:class:`~app.services.pay_rhythm.FixedDays`) rather than a bare day
    count; a test about a day-of-month kind (``C17-d-2``) states its
    :class:`~app.services.pay_rhythm.Rhythm` outright.  ``None`` builds
    ``FixedDays(None)`` -- a fixed-days cadence with NO day count, which is
    what the "absent cadence" refusal cases mean by absence; a bare ``None``
    where the VALUE should be is a different state with its own test
    (``test_pay_calendar_derivation.TestTheCadenceIsAValueOfAKnownKind``).

    Args:
        cadence_days: Days between the paydays.
        shift: The convention, defaulting to
            :attr:`~app.enums.BusinessDayShiftEnum.NONE`.  Pass a displacing
            member only in a test that is ABOUT the convention -- the write
            door refuses one on a cadence below
            :func:`~app.utils.business_days.shortest_collision_free_cadence`.

    Returns:
        The rhythm.
    """
    return pay_rhythm.Rhythm(
        cadence=pay_rhythm.FixedDays(cadence_days), shift=shift,
    )


def era_of(effective_from, cadence_days, shift=BusinessDayShiftEnum.NONE):
    """Return a fixed-days :class:`~app.services.pay_rhythm.Era`.

    :func:`rhythm_of`'s sibling for the era relation (plan step
    ``pay_calendar:C17-a``): the rhythm plus the day it took effect, which is
    also the grid's phase.  The kind is the cadence value's type since plan
    step ``C17-d-1``, so :func:`rhythm_of` states it.

    Args:
        effective_from: The era's first nominal payday.
        cadence_days: Days between the paydays.
        shift: The convention, defaulting to
            :attr:`~app.enums.BusinessDayShiftEnum.NONE` for
            :func:`rhythm_of`'s reason.

    Returns:
        The era.
    """
    return pay_rhythm.Era(
        effective_from=effective_from,
        rhythm=rhythm_of(cadence_days, shift),
    )


#: The phase :func:`eras_of` gives an EMPTY payday set's era.  An empty
#: calendar reads no phase -- there is no last period to close and nothing to
#: project past -- so the day is arbitrary, and it is named rather than drawn
#: from a clock so the value is the same on every run.
EMPTY_CALENDAR_PHASE = _real_date(2026, 1, 2)


def eras_of(paydays, cadence_days, shift=BusinessDayShiftEnum.NONE):
    """Return ONE era phased on the earliest of *paydays*, as a sequence.

    The pure calendar's era argument since plan step ``pay_calendar:C17-b-2``
    (:meth:`PayCalendar.from_paydays` takes the owner's eras where it took a
    :class:`~app.services.pay_rhythm.Rhythm`).  A test that states paydays and
    a cadence means "this owner has been paid every *cadence_days* since
    their first payday", which is exactly the era the migration backfilled
    for every existing owner -- so phasing the era on the earliest payday
    keeps every regular fixture's derivation what it was, and moves exactly
    the last end of a fixture whose last payday is OFF that grid, which is
    what that step does to a real owner.  A test ABOUT eras states them
    outright with :func:`era_of`.

    Args:
        paydays: The ``(period_id, payday)`` pairs the calendar is built
            from, in any order; may be empty, in which case the era's phase
            is :data:`EMPTY_CALENDAR_PHASE`.
        cadence_days: Days between the paydays.
        shift: The convention, defaulting to
            :attr:`~app.enums.BusinessDayShiftEnum.NONE` for
            :func:`rhythm_of`'s reason.

    Returns:
        A one-element tuple holding the era.
    """
    days = [payday for _period_id, payday in paydays]
    return (era_of(min(days) if days else EMPTY_CALENDAR_PHASE, cadence_days, shift),)


def mint_fixture_era(user_id, effective_from, cadence_days,
                     shift=BusinessDayShiftEnum.NONE):
    """Give *user_id* a schedule row and ONE era, without recording a payday.

    The state every payday-holding owner has, for a fixture that then writes
    its ``budget.pay_periods`` rows BY HAND -- a corrupt shape a checker must
    catch.  (A calendar-monthly schedule was the other reason until plan
    step ``pay_calendar:C17-d-2`` gave it a door, ledger row **P78**.)
    ``fk_pay_periods_schedule`` needs the row and a calendar needs the era;
    the ordinary fixtures go through ``record_paydays``, which does both,
    and this is the one line for the fixtures that cannot.

    Plan step ``pay_calendar:C17-a``: it replaces the
    ``upsert_schedule(user_id, rhythm, None)`` line those fixtures carried,
    and it takes the era's day because an era has one.  The extend door steps
    from that day and the era rule tests a batch's first payday against its
    grid, so a fixture that later EXTENDS or RECORDS through the writer must
    state a day on the grid it means -- and since plan step ``C17-b-2`` so
    must a fixture that is only READ: the derivation closes the last period
    and projects past it on the era's grid, so a hand-built schedule whose
    paydays are off the day stated here derives a last end on the grid, not
    one cadence past its last row.

    Args:
        user_id: The owner.
        effective_from: The era's first nominal payday.
        cadence_days: Days between the paydays.
        shift: The convention, defaulting to ``none``.

    Returns:
        The minted :class:`~app.models.pay_era.PayEra` row, flushed.
    """
    pay_schedule_service.ensure_schedule_row(user_id)
    return pay_era_write.mint_era(
        user_id, era_of(effective_from, cadence_days, shift),
    )


def restate_fixture_era(user_id, effective_from, cadence_days,
                        shift=BusinessDayShiftEnum.NONE):
    """Replace every era *user_id* holds with ONE, without touching a payday.

    The fixture form of "force the owner's stored cadence" for a case that
    proves a reader takes the RHYTHM from storage rather than inferring it
    from the rows: the paydays stay exactly where they are and only the era
    moves.  Before plan step ``pay_calendar:C17-a`` that was one
    ``upsert_schedule`` line; an era is a row per rhythm, so the honest
    equivalent retires the owner's eras and mints the one the case states.

    Args:
        user_id: The owner.
        effective_from: The era's first nominal payday.
        cadence_days: Days between the paydays.
        shift: The convention, defaulting to ``none``.

    Returns:
        The minted :class:`~app.models.pay_era.PayEra` row, flushed.
    """
    pay_schedule_service.ensure_schedule_row(user_id)
    pay_era_write.retire_eras(user_id, ())
    return pay_era_write.mint_era(
        user_id, era_of(effective_from, cadence_days, shift),
    )


def strip_owner_schedule(db_session, user_id):
    """Leave *user_id* as an owner who has never generated a schedule.

    Every payday, every era and the ``budget.pay_schedule`` row go, in the
    only order the keys admit: ``fk_pay_periods_schedule`` and, since plan
    step ``pay_calendar:C17-a``, ``fk_pay_eras_schedule`` are both
    ``ON DELETE RESTRICT``, so the children go before the parent.  Six cases
    each spelled the first two deletes by hand before the era relation added a
    third child; this is the one statement of the order so the seventh
    cannot copy it wrongly.

    Bulk deletes with ``synchronize_session=False``, so an instance the case
    already loaded is not touched; the case commits or reads back as it
    needs to.

    Args:
        db_session: The test ``db.session``.
        user_id: The owner to strip.
    """
    # pylint: disable=import-outside-toplevel
    from app.models.pay_era import PayEra
    from app.models.pay_period import PayPeriod
    from app.models.pay_schedule import PaySchedule

    for model in (PayPeriod, PayEra, PaySchedule):
        db_session.query(model).filter_by(user_id=user_id).delete(
            synchronize_session=False,
        )


def registration_spec(**overrides):
    """Return a complete, valid :class:`RegistrationSpec` for service tests.

    The service-tier twin of :func:`register_form_data`, and it exists for the
    same reason: ``registration_service.register_user`` takes one value object whose
    pay-calendar half arrived at plan step X-ad-a, and the tests that call it
    directly should not each restate what a valid sign-up looks like.

    **``cadence_days`` and ``shift`` stay spellable as overrides**, though the
    spec itself carries the pair as one
    :class:`~app.services.pay_rhythm.Rhythm` since plan step
    ``pay_calendar:C14-b``.  A case about the cadence is not a case about the
    convention, and making every such case name both halves would have put the
    default in each of them; assembled here, the default is stated once.  Pass
    ``rhythm=`` directly to state the pair outright.

    Args:
        **overrides: Spec fields to replace.  ``first_payday`` defaults to the
            user's today -- see :func:`register_form_data` for why the clock
            matters.  ``cadence_days`` and ``shift`` are accepted as halves of
            the rhythm and may not be combined with an explicit ``rhythm``.

    Returns:
        The :class:`~app.services.registration_service.RegistrationSpec`.

    Raises:
        TypeError: Both ``rhythm`` and one of its halves were given, which
            would leave which one wins to the order this function happens to
            apply them in.
    """
    # pylint: disable=import-outside-toplevel
    from app.config import BaseConfig
    from app.services.registration_service import RegistrationSpec
    from app.utils.dates import display_today
    halves = {
        key: overrides.pop(key)
        for key in ("cadence_days", "shift") if key in overrides
    }
    if halves and "rhythm" in overrides:
        raise TypeError(
            f"registration_spec() got rhythm and {sorted(halves)}; state the "
            f"pair one way or the other."
        )
    fields = {
        "email": "newuser@example.com",
        "password": "securepass123",
        "display_name": "New User",
        "first_payday": display_today(),
        "rhythm": rhythm_of(
            halves.get("cadence_days", BaseConfig.DEFAULT_PAY_CADENCE_DAYS),
            halves.get("shift", BusinessDayShiftEnum.NONE),
        ),
        "num_periods": BaseConfig.DEFAULT_PAY_PERIOD_HORIZON,
        # The column's own default (plan step balance:X-bh-2): a sign-up that
        # skips the question has stated NOTHING, and the engine counts only
        # that owner's recorded paydays.  A case about the field passes its
        # own value.
        "history_opens_on": None,
    }
    fields.update(overrides)
    return RegistrationSpec(**fields)


def seam_confirmed_view(loan_account_id, scenario_id, as_of):
    """Return a loan's seam CONFIRMED view at an explicit scenario and as-of.

    The walk-built :class:`~app.services.loan_resolver.ConfirmedLedgerView`
    (:func:`app.services.balance_at.confirmed_view`) -- the balance the loan's
    recorded events fold to, plus its confirmed schedule rows -- which since plan
    step E1d-b is what the resolver's confirmed slice seeds from, replacing the
    deleted posted-ledger reader ``loan_payment_service.confirmed_loan_view``.

    Several suites build an INDEPENDENT ``compute_payoff_scenarios`` reference to
    grade a seam figure against, and every one of them needs this same seed: a
    reference composed WITHOUT it would be the un-seeded anchor replay, which is a
    different trajectory (it is blind to how much cash a payment actually moved).
    Sharing one helper is what keeps those references honest and identical.

    Pins the scenario and the as-of EXPLICITLY rather than through
    ``BalanceContext.build``, because the callers are frozen-clock or
    named-scenario tests whose baseline lookup must not be re-derived.

    Args:
        loan_account_id: The loan account whose confirmed view to build.
        scenario_id: The scenario to scope the walk to.
        as_of: The evaluation date (the resolver's NOW).

    Returns:
        The ``ConfirmedLedgerView``, or ``None`` when the seam withholds one (not
        a configured loan, no scenario, a future date, or not yet originated).
    """
    # Pylint: import-outside-toplevel -- helper-local, matching this module's
    # convention of importing app symbols inside the helper that needs them.
    from app.extensions import db  # pylint: disable=import-outside-toplevel
    from app.models.account import Account  # pylint: disable=import-outside-toplevel
    from app.models.scenario import Scenario  # pylint: disable=import-outside-toplevel
    from app.services import balance_at  # pylint: disable=import-outside-toplevel

    loan = db.session.get(Account, loan_account_id)
    return balance_at.confirmed_view(loan, balance_at.BalanceContext(
        user_id=loan.user_id,
        scenario=db.session.get(Scenario, scenario_id),
        as_of=as_of,
    ))


def contract_forward_references(loan_params, scenario_id, as_of, extra):
    """Return ``(scenarios, with_extra)``: the contract, and the contract plus *extra*.

    The INDEPENDENT reference the loan-page and payoff-oracle suites grade the
    balance seam's forward fold against.  ``scenarios`` is the composer's
    :class:`~app.services.loan_resolver.PayoffScenarios` -- the ledger-derived
    confirmed history and ``project_forward`` over the CONTRACT from the
    replayed starting state, a walk that amortizes month by month and reads no
    payment record.  ``with_extra`` is that same engine walk with *extra* a
    month on top (``project_forward``'s own what-if ``extra_monthly``), over
    the same starting state: the seam's confirmed view for the balance and the
    contractual slice for the starting date and the remaining month count, so
    the reference reads nothing off the fold it grades.

    *The composer produced this reference itself -- its ACCELERATED slice,
    ``compute_payoff_scenarios(extra_monthly=...)`` -- until plan step R7d-g-3
    deleted the composer's planned slices (ruling **R-R88**); the engine's
    contract-plus-extra projection is the same arithmetic, called directly.*

    Args:
        loan_params: The loan's :class:`LoanParams`.
        scenario_id: The scenario to scope the feed and the confirmed view to.
        as_of: The evaluation date (the replay / projection boundary).
        extra: The what-if extra per month; ``0.00`` makes ``with_extra`` the
            contract's own rows.

    Returns:
        ``(scenarios, with_extra)``: the composer's result and the engine's
        contract-plus-extra rows (``AmortizationRow``, in date order).
    """
    # Pylint: import-outside-toplevel -- helper-local, matching this module's
    # convention of importing app symbols inside the helper that needs them.
    from app.services import (  # pylint: disable=import-outside-toplevel
        loan_loaders, loan_payment_service, loan_resolver,
    )
    from app.services.amortization_engine import (  # pylint: disable=import-outside-toplevel
        ProjectionInputs, project_forward,
    )

    ctx_loan = loan_payment_service.load_loan_context(
        loan_params.account_id, amount_basis_for_scenario(scenario_id),
        loan_params,
    )
    confirmed = seam_confirmed_view(loan_params.account_id, scenario_id, as_of)
    scenarios = loan_resolver.compute_payoff_scenarios(
        loan_inputs=loan_resolver.LoanInputs(
            loan_params, loan_loaders.load_loan_anchor_facts(loan_params),
            ctx_loan.payments, ctx_loan.rate_changes,
        ),
        as_of=as_of,
        confirmed_view=confirmed,
    )
    contract = scenarios.original_forward
    with_extra = project_forward(
        ProjectionInputs(
            starting_balance=confirmed.balance,
            starting_date=contract[0].payment_date,
            remaining_months=len(contract),
            payment_day=loan_params.payment_day,
            terms_schedule=loan_resolver.engine_terms(
                loan_params, ctx_loan.rate_changes,
            ),
        ),
        extra_monthly=extra,
    )
    return scenarios, with_extra


def seam_cash_balance_at(account, scenario_id, as_of):
    """Return what the SEAM says an account's cash balance is on a date.

    :func:`app.services.balance_at.cash_balance_at` -- the cash FOLD sampled at
    one date, and since plan step X-c2b2 the figure every cash-flow surface
    renders.  Shared by the five pay-period CRUD suites, which need a balance
    PROBE rather than a balance test: what they assert is that extending,
    truncating, regenerating, resetting or topping up a schedule leaves the
    projection marching correctly, and the balance is how they read that.

    They probed ``_cash_engine.balance_as_of_date`` until plan step X-c2b3
    deleted it.  Routing them here rather than at a replacement producer is the
    point: a schedule-mutation suite should read the figure a SCREEN shows, so a
    mutation that corrupts the projection fails these tests instead of passing
    them against a producer no screen reads.

    **The reader's NOW and the valuation date are DIFFERENT dates here, and
    conflating them would silently zero every probe.**  ``cash_balance_at``
    takes both: ``ctx.as_of`` is the reader's now -- the floor a still-projected
    row's landing day is clamped UP to, because a plan cannot already have
    happened (ruling R-G) -- while the trailing argument is the date being
    valued.  Passing the valuation date as both would clamp every projected row
    to ``valuation + 1`` and answer with the assertions alone.  So the now comes
    from :meth:`BalanceContext.build`, which is the app's own default
    (``date.today()`` inside ``balance_at._context``) and therefore the date a
    ``freeze_today`` suite has patched -- a helper-local ``date.today()`` would
    read the WALL clock instead, which is the N-65 / N-8 trap.

    ``scenario_id`` is load-bearing rather than decorative: the context resolves
    the BASELINE, so a caller probing any other scenario is refused here instead
    of being answered about a scenario it did not write to.

    Args:
        account: The cash account to value (session-attached).
        scenario_id: The scenario the caller wrote its rows under.  Must be the
            user's baseline, which is what every current caller uses.
        as_of: The calendar date to value at, a civil ``date``.

    Returns:
        The cent-quantized ``Decimal`` cash-flow balance at *as_of*.
    """
    # Pylint: import-outside-toplevel -- helper-local, matching this module's
    # convention of importing app symbols inside the helper that needs them.
    from app.services import balance_at  # pylint: disable=import-outside-toplevel

    ctx = balance_at.BalanceContext.build(account.user_id)
    assert ctx.scenario_id == scenario_id, (
        f"seam_cash_balance_at probes the BASELINE scenario "
        f"({ctx.scenario_id}), but was asked for {scenario_id}"
    )
    return balance_at.cash_balance_at(account, ctx, as_of)


def create_transfer(
    seed_user, db_session, from_account, to_account, period,
    amount=Decimal("100.00"), *, due_date=None, name=None, scenario=None,
):
    """Create a PROJECTED transfer via the real service; it is one row.

    It came with two shadow rows until plan step ``balance:X-bi-6-4d-2`` made a
    transfer one row (ruling **R-BAL258**).

    The create half of :func:`create_settled_transfer`, which now builds on this
    one -- so both helpers route through ``transfer_service.create_transfer``,
    the sole transfer-creation chokepoint, and a test cannot accidentally
    construct a transfer that bypasses a write guard production enforces.

    Exposed separately because a test may need a transfer that is NOT settled
    (a projection), or one carrying an explicit ``due_date``: on a LOAN payment
    the due date is the installment the payment satisfies, which decides whether
    the write is even allowed (ruling R-C, plan step C9b) and which installment
    the fold splits it against.

    Flushes via the service; the caller commits.

    Args:
        seed_user: The ``seed_user`` fixture dict (supplies ``user_id`` and the
            baseline scenario).
        db_session: The test ``db.session`` (unused directly -- the service owns
            the session -- but accepted so call sites read uniformly).
        from_account: The account money leaves (the from-side).
        to_account: The account money enters (the to-side).
        period: The :class:`~app.models.pay_period.PayPeriod` to place the
            transfer in.
        amount: The transfer amount (Decimal).  Defaults to
            ``Decimal("100.00")``.
        due_date: Optional due date stored on the transfer.  ``None`` (the
            default) leaves it unset, which is what the
            ad-hoc transfer form produces; a loan payment's installment then
            falls back to the loan's first payment day on or after its
            pay-period start
            (:func:`app.services.installment_calendar.monthly_due_date`).
        name: Optional transfer display name.
        scenario: The :class:`~app.models.scenario.Scenario` to place the
            transfer in.  Defaults to the seed user's baseline.

    Returns:
        The created (Projected) :class:`~app.models.transfer.Transfer`.
    """
    # pylint: disable=import-outside-toplevel  -- same lazy-app-import
    # convention every helper in this module follows.
    from app import ref_cache
    from app.enums import StatusEnum
    from app.services import transfer_service

    scenario_id = (
        seed_user["scenario"].id if scenario is None else scenario.id
    )
    return transfer_service.create_transfer(
        transfer_service.TransferSpec(
            user_id=seed_user["user"].id,
            from_account_id=from_account.id,
            to_account_id=to_account.id,
            pay_period_id=period.id,
            scenario_id=scenario_id,
            amount_ownership=AmountOwnership.own(amount),
            status_id=ref_cache.status_id(StatusEnum.PROJECTED),
            category_id=None,
            name=name,
            due_date=due_date,
        ),
    )


def create_settled_transfer(
    seed_user, db_session, from_account, to_account, period,
    amount=Decimal("100.00"), settled_amount=None,
    settled_on=None, name=None, scenario=None, due_date=None,
):
    """Create an ad-hoc transfer and settle it (Paid), returning the parent.

    The shared settled-transfer builder for the posting-ledger (Build-Order
    Step 2) backfill / lifecycle suites.  Routes the whole thing through
    ``transfer_service`` -- the sole transfer writer -- so the transfer and
    each side's payment record obey every transfer invariant, exactly as
    production produces them.  The transfer is created Projected, then
    transitioned to Paid via ``update_transfer`` (the same ``mark_done``
    chokepoint the route uses).  It built "a settled transfer with two real
    shadows" until plan step ``balance:X-bi-6-4d-2`` made a transfer one row
    (ruling **R-BAL258**).

    **It PRESSES Paid, and a Paid press states no day** (ruling **R-BAL163**,
    plan step ``balance:X-bi-6-4c-3``): both sides BORROW the day it was
    pressed, so both read ``borrowed`` on *settled_on*.  It stated that day as
    ``entered`` on the pair until that step, which is what the one-box popover
    recorded; a test whose subject is a day the owner TYPED states it per side
    through the verb (``side_days``) rather than through this builder.

    **The press lands on *settled_on* by pinning the writer's ONE clock read**
    -- the day a pair with no evidence borrows
    (``transfer_service._status``'s ``display_today``) -- for the length of the
    settle, and nothing else.  Not ``time_machine``: a suite running under
    :func:`freeze_today` has replaced every module's ``datetime`` with a class
    whose ``now`` a traveller cannot reach, and those are exactly the loan
    suites that pin a past day.  So the build CHECKS its own result: both sides
    must read ``borrowed`` on *settled_on* afterwards, and a pin that stopped
    reaching the writer (the read moved) fails here, naming itself, instead of
    settling every fixture on today in silence.

    Flushes via the service; the caller commits.

    Args:
        seed_user: The ``seed_user`` fixture dict (supplies ``user_id`` and
            the baseline scenario).
        db_session: The test ``db.session``.  The service owns the writes;
            this reads the two sides back for the check above.
        from_account: The :class:`~app.models.account.Account` money leaves
            (the from-side).
        to_account: The account money enters (the to-side).
        period: The :class:`~app.models.pay_period.PayPeriod` to place the
            transfer in.
        amount: The transfer amount (Decimal).  Defaults to
            ``Decimal("100.00")``.
        settled_amount: When not ``None``, the figure the owner typed as what
            moved, passed to the settle as a person's typed figure
            (:func:`typed`).  Defaults to ``None``: the transfer settles at
            *amount*.
        settled_on: The civil DAY Paid is pressed on, which both sides then
            borrow.  ``None`` (the default) presses it on the owner's today.
            A loan test reading a PAST balance must pin this to the day it
            wants the payment visible from -- balance step C2 keys visibility
            on the SETTLED date -- typically the period's ``start_date``.  It
            took an INSTANT until plan step X-f1 and callers wrapped their day
            in ``settle_instant_on``; the column stores the day now, so the
            day is passed directly.  A fixture that genuinely needs the broken
            settled-with-no-day row builds it with the bare :func:`add_txn`,
            which is what that builder is for: no door writes one (a
            :class:`~app.services.transfer_service.SideDay` cannot wrap
            ``None``).
        name: Optional transfer display name.
        due_date: Optional due date stored on the transfer, passed straight
            through to :func:`create_transfer`.
            ``None`` (the default) leaves it unset, and a loan payment's
            installment then falls back to the loan's first payment day on or
            after its pay-period start
            (:func:`app.services.installment_calendar.monthly_due_date`).  Pass
            it when a test needs two settled loan payments in ONE accrual
            period at DIFFERENT installments -- that fallback resolves both to
            the same date, so it cannot express that shape.
        scenario: The :class:`~app.models.scenario.Scenario` to place the
            transfer in.  Defaults to ``None``, which uses
            the seed user's baseline scenario (``seed_user["scenario"]``);
            pass a non-baseline scenario to exercise multi-scenario isolation
            (the Commit-6 reconciliation oracle).

    Returns:
        The settled (Paid) parent :class:`~app.models.transfer.Transfer`.
    """
    # pylint: disable=import-outside-toplevel  -- same lazy-app-import
    # convention every helper in this module follows.
    from unittest import mock

    from app import ref_cache
    from app.enums import SettledDayBasisEnum, StatusEnum
    from app.models.transaction_entry import TransactionEntry
    from app.services import transfer_service
    from app.services.settle_day import SettleDay, recorded_settle_day
    from app.services.transfer_service import _status as transfer_status

    transfer = create_transfer(
        seed_user, db_session, from_account, to_account, period,
        amount=amount, name=name, scenario=scenario, due_date=due_date,
    )
    update_kwargs = {"status_id": ref_cache.status_id(StatusEnum.DONE)}
    if settled_amount is not None:
        # The service's key is the VALUE -- the figure and who wrote it, a
        # person's here as the popover's would be (plan step X-bi-3e-1) --
        # under the name the door reads; ``update_transfer`` ignores a key it
        # does not know, so the old column-named key would settle at the plan
        # and say nothing.
        update_kwargs["figure"] = typed(settled_amount)
    if settled_on is None:
        transfer_service.update_transfer(
            transfer.id, seed_user["user"].id, **update_kwargs
        )
        return transfer
    with mock.patch.object(
        transfer_status, "display_today", return_value=settled_on,
    ):
        transfer_service.update_transfer(
            transfer.id, seed_user["user"].id, **update_kwargs
        )
    pressed = SettleDay(day=settled_on, basis=SettledDayBasisEnum.BORROWED)
    # Each side's day is its RECORD's since plan step ``balance:X-bi-6-4d-2``
    # (R-BAL167 class 4: read by its own SQL over the side links, not through
    # the producer under test); a ``$0.00`` close keeps no record on either
    # side (R-BAL141), so it has no day to read.
    sides = [
        recorded_settle_day(record)
        for record in db_session.query(TransactionEntry).filter(
            (TransactionEntry.expense_transfer_id == transfer.id)
            | (TransactionEntry.income_transfer_id == transfer.id),
        )
    ]
    expected = (
        [] if settled_amount is not None and not settled_amount
        else [pressed, pressed]
    )
    assert sides == expected, (
        f"create_settled_transfer pressed Paid on {settled_on} but the sides "
        f"read {sides}: the pin on the writer's clock read no longer reaches "
        "it (see this builder's docstring)"
    )
    return transfer


def create_settled_cash_transaction(  # pylint: disable=too-many-arguments
    seed_user, db_session, period, amount,
    *, account=None, scenario=None, is_income=False,
    category=None, settled_amount=None, name="Cash Txn",
    settled_on=_UNSET_SETTLED_ON,
):
    """Create an ordinary (non-transfer) transaction and settle it go-forward.

    The cash analog of :func:`create_settled_transfer` for the Build-Order
    Step 3 posting-ledger oracle: a ONE-OFF placed through
    :func:`one_off_row_of` (plan step balance:X-bi-7c; it built a link-less
    row owning its figure until then, the shape the cutover deleted), then
    settled through :func:`settle_cash_row` -- the two REAL go-forward
    production primitives, the status seam and the posting builder, in the
    order the mark-done route applies them.  So the returned transaction is
    genuinely settled (``status.is_settled``, ``settled_on`` stamped) AND its
    confirmed cash effect is posted to the double-entry ledger, exactly as
    production produces it when a user marks a transaction Paid / Received.

    Flushes via the builder; the caller commits.

    Args:
        seed_user: The ``seed_user`` (or ``seed_second_user``) fixture dict --
            supplies the default account / scenario and the owning ``user_id``.
        db_session: The test ``db.session``.
        period: The :class:`~app.models.pay_period.PayPeriod` to place the
            transaction in.
        amount: The definition's price (Decimal) -- the confirmed effect when
            no *settled_amount* is given.
        account: The :class:`~app.models.account.Account` the cash leg lands on;
            defaults to ``seed_user["account"]`` (the Checking account).
        scenario: The :class:`~app.models.scenario.Scenario` to place the
            transaction in; defaults to the seed user's baseline scenario.  Pass
            a non-baseline scenario to exercise multi-scenario isolation.
        is_income: When True, an Income transaction settling to Received (cash
            leg positive); otherwise an Expense settling to Paid (cash leg
            negative).
        category: The :class:`~app.models.category.Category` the counter leg
            files under, or ``None``.
        settled_amount: The figure the owner typed at the settle, or ``None``
            for the row's own (see :func:`settle_cash_row`).
        name: The transaction name.
        settled_on: See :func:`settle_cash_row`.

    Returns:
        The settled :class:`~app.models.transaction.Transaction`, flushed.
    """
    # pylint: disable=import-outside-toplevel  -- the module convention.
    from app import ref_cache
    from app.enums import TxnTypeEnum

    account = seed_user["account"] if account is None else account
    scenario = seed_user["scenario"] if scenario is None else scenario
    type_id = ref_cache.txn_type_id(
        TxnTypeEnum.INCOME if is_income else TxnTypeEnum.EXPENSE
    )
    return settle_cash_row(
        one_off_row_of(
            period, name=name, amount=amount,
            user_id=period.user_id, account_id=account.id,
            scenario_id=scenario.id, transaction_type_id=type_id,
            category_id=None if category is None else category.id,
        ),
        is_income=is_income, settled_amount=settled_amount,
        settled_on=settled_on,
    )


def settle_cash_row(
    txn, *, is_income=False, settled_amount=None, settled_on=_UNSET_SETTLED_ON,
):
    """Settle the Projected row *txn* go-forward and post its cash effect.

    The settle half of :func:`create_settled_cash_transaction`, its own
    function since plan step balance:X-bi-7c so a case that builds its row
    another way settles it through the same two primitives without
    re-spelling either (ruling **R-BAL59**; the legacy link-less builder
    was its first other caller, until the cutover ``balance:X-bi-7d-2``
    deleted that shape).  Income settles to Received and expenses to Paid
    (Done) --
    the same split the mark-done route applies (``mutations.py``).  A plain
    transaction carries no entries, so its effect is its resolved figure;
    callers needing the envelope debit-only effect attach credit entries
    separately (the backfill / lifecycle suites cover that path).

    Args:
        txn: The Projected :class:`~app.models.transaction.Transaction`,
            flushed.
        is_income: When True the row settles to Received, else to Paid.
        settled_amount: The figure the owner typed, or ``None`` for the
            row's own -- which :func:`settlement_if_settling` records as the
            panel's prefill coming back untouched (``derived``).
        settled_on: The civil day the money moved.  Omit it for the seam's
            own stamp; pass a day to pin the cash somewhere specific, through
            the app's own pair writer and the seam's mirror (see the comment).
            Passing ``None`` explicitly leaves the seam's stamp too.

    Returns:
        *txn*, settled and posted, flushed.
    """
    # pylint: disable=import-outside-toplevel  -- the module convention.
    from app import ref_cache
    from app.enums import SettledDayBasisEnum, StatusEnum
    from app.services import posting_service, status_seam
    from app.services.settle_day import SettleDay, record_settle_day
    from app.services.status_seam._covering import sync_covering_movement

    # Settle through the real go-forward path: the seam flips the status and
    # stamps settled_on, the optional manual actual is applied AFTER (as the route
    # does), and the builder reconciles the ledger to the confirmed effect last.
    settled_status = StatusEnum.RECEIVED if is_income else StatusEnum.DONE
    # A settle RECORDS what moved (plan step X-au-c3), and this goes through
    # :func:`settlement_if_settling` rather than calling
    # ``Settlement.from_settle`` directly so the ECHO rule applies here too: a
    # ``settled_amount`` equal to the row's own figure is the panel's prefill
    # coming back untouched, which the app records as ``derived``.  Calling
    # ``from_settle`` with the raw submission recorded ``corrected`` for it --
    # two fixture doors answering one rule two ways, which is the drift this
    # helper exists to prevent (found by adversarial review, 2026-08-17).
    status_seam.apply_status_change(
        txn, ref_cache.status_id(settled_status),
        settlement=settlement_if_settling(
            txn, ref_cache.status_id(settled_status), settled_amount,
        ),
    )
    if settled_on is not _UNSET_SETTLED_ON:
        # Through the app's OWN pair writer (plan step **X-az**), never as a
        # bare column assignment: the day and its basis are welded by
        # ``ck_transactions_settle_day_basis_pairing``, and a fixture that moved
        # one and left the other would build a row the app cannot write.  The
        # seam above already stamped ``entered``; re-stating it keeps the pair
        # written in one act whichever day wins.
        #
        # **Pinned AROUND the seam on purpose, and the seam's mirror is then
        # asked to follow** (plan step **X-bi-3a**).  The seam refuses a day
        # that has not happened (ruling R-EJ), and the anchor-reconciliation
        # suites build their civil-day partitions on a server-now origin with
        # events a day or three after it -- so the pin stays a bare one, as it
        # always was.  What may NOT stay bare is the row's covering movement:
        # the seam wrote it dated with its own stamp, and a row moved to
        # another day while its movement kept today's is a state no door can
        # produce (the ledger partitioned one spend on two sides of an anchor,
        # measured on five cases 2026-09-15).  So the pin is followed by the
        # seam's own mirror -- one rule, not a fixture's restatement of it.
        record_settle_day(
            txn,
            SettleDay(day=settled_on, basis=SettledDayBasisEnum.ENTERED),
        )
        sync_covering_movement(txn, now_settled=True, settlement=None)
    posting_service.sync_transaction_postings(txn)
    return txn


def set_default_grid_account(db_session, user_id, account_id):
    """Point a user's default grid account at *account_id* (re-queried, flushed).

    Sets ``UserSettings.default_grid_account_id`` so
    ``account_resolver.resolve_grid_account``'s tier-1 picks this account --
    the way a test makes the dashboard / grid render a NON-checking account.
    Re-queries the live ``UserSettings`` row rather than mutating the
    ``seed_user["settings"]`` object directly: the account factories above
    (:func:`create_hysa_account`, :func:`make_investment_account`, ...) commit
    internally, which detaches the fixture's settings instance, so a write to
    it would be silently dropped.  Flushes so the change is visible to the
    producer under test in the same session.

    Args:
        db_session: The test ``db.session``.
        user_id: The user whose default grid account to set.
        account_id: The account id to point the default at.

    Returns:
        The live :class:`~app.models.user.UserSettings` row, updated.
    """
    # pylint: disable=import-outside-toplevel  -- same lazy-app-import
    # convention the factory helpers above follow.
    from app.models.user import UserSettings

    settings = (
        db_session.query(UserSettings).filter_by(user_id=user_id).first()
    )
    settings.default_grid_account_id = account_id
    db_session.flush()
    return settings


def start_test_pay_list(profile, pay, payday=None):
    """Give a test salary profile its FIRST pay entry, through the create door's writer.

    A profile holds at least one pay entry from plan step salary:X-av-3a on
    (ruling **R-SAL68**), and the create door writes it through
    :func:`~app.services.pay_list_service.start_pay_list` -- so a fixture
    writes it there too, payday check included, rather than constructing the
    row around the door.

    Args:
        profile: The profile, added to the session; flushed here so the
            entry can reference it.
        pay: What one paycheck pays (``Decimal``).
        payday: The payday it pays from.  Defaults to the owner's first
            SAVED payday, which is where migration ``70680a4a7405`` put every
            existing profile's one entry.

    Returns:
        The flushed :class:`~app.models.salary_pay_entry.SalaryPayEntry`.
    """
    # pylint: disable=import-outside-toplevel  -- same circular-dep
    # avoidance as the loan helpers above.
    from app.extensions import db
    from app.services import pay_list_service
    from app.services.balance_at import BalanceContext
    from app.utils.dates import display_today

    db.session.flush()
    ctx = BalanceContext.build(profile.user_id)
    if payday is None:
        payday = ctx.calendar().saved()[0].start_date
    return pay_list_service.start_pay_list(
        profile, ctx, pay, payday, display_today(),
    )


#: The pay :func:`make_salary_profile` gives a profile when a test names
#: none: ``$75,000.00`` a year at 26 paychecks, rounded to the cent -- the
#: figure migration ``70680a4a7405`` writes for the yearly salary this
#: builder defaulted to until plan step salary:X-av-3a.
DEFAULT_TEST_PAY = Decimal("2884.62")


def make_salary_profile(
    seed_user, db_session, name="Test Salary",
    pay=None, state_code="NC", pay_from=None,
):
    """Build and add an active SalaryProfile for the seed user, with its pay list (uncommitted).

    The shared salary-profile builder so the stereotyped
    ``FilingStatus`` lookup + ``SalaryProfile`` construction block is not
    copied per suite (a duplicate-code finding).  Flushes, because the first
    pay entry references the profile; the caller commits.

    Args:
        seed_user: The ``seed_user`` fixture dict.
        db_session: The test ``db.session``.
        name: The profile name.
        pay: What one paycheck pays (``Decimal``); defaults to
            :data:`DEFAULT_TEST_PAY`.
        state_code: The state code (default ``"NC"``).
        pay_from: The payday the pay is paid from; defaults to the owner's
            first saved payday (:func:`start_test_pay_list`).

    Returns:
        The added :class:`~app.models.salary_profile.SalaryProfile`.
    """
    # pylint: disable=import-outside-toplevel  -- same circular-dep
    # avoidance as the loan helpers above.
    from app.models.ref import FilingStatus
    from app.models.salary_profile import SalaryProfile

    filing = db_session.query(FilingStatus).first()
    profile = SalaryProfile(
        user_id=seed_user["user"].id,
        scenario_id=seed_user["scenario"].id,
        filing_status_id=filing.id,
        name=name,
        state_code=state_code,
    )
    db_session.add(profile)
    start_test_pay_list(
        profile, DEFAULT_TEST_PAY if pay is None else pay, pay_from,
    )
    return profile


def make_recurring_raise(
    profile_id, db_session, *, effective_year, effective_month=1,
    percentage=None, terminal_year=None,
):
    """Build and add ONE recurring percentage raise on a profile (uncommitted).

    The shared raise builder for the cases that grade a recurring raise's END
    YEAR reaching a figure (plan step salary:S3-f-2b): the plan point, the
    ``/retirement`` rail's probe and the pricer-count gate each seed the same
    row, and three inline ``SalaryRaise(...)`` blocks were an adversarial
    review's finding.  The caller commits.

    Args:
        profile_id: The :class:`~app.models.salary_profile.SalaryProfile` id.
        db_session: The test ``db.session``.
        effective_year: The year the raise first applies.
        effective_month: The month within that year (default January).
        percentage: The fractional raise (Decimal); defaults to
            ``Decimal("0.0500")``, a 5% merit raise.
        terminal_year: The last year it is believed, ``None`` for no end.

    Returns:
        The added :class:`~app.models.salary_raise.SalaryRaise`.
    """
    # pylint: disable=import-outside-toplevel  -- same circular-dep
    # avoidance as the loan helpers above.
    from app import ref_cache
    from app.enums import RaiseTypeEnum
    from app.models.salary_raise import SalaryRaise

    row = SalaryRaise(
        salary_profile_id=profile_id,
        raise_type_id=ref_cache.raise_type_id(RaiseTypeEnum.MERIT),
        effective_month=effective_month,
        effective_year=effective_year,
        percentage=Decimal("0.0500") if percentage is None else percentage,
        is_recurring=True,
        terminal_year=terminal_year,
    )
    db_session.add(row)
    return row


def create_envelope_txn(seed_user, db_session, period, name, estimated):
    """Create an entry-tracked (is_envelope) projected expense (flushed).

    Builds a minimal Every-Period envelope template and lets the engine
    generate its Projected row in ``period`` (:func:`generate_row_of`), so the
    stereotyped template + instance construction is not copied per suite (a
    duplicate-code finding) and the row is the one the application makes
    rather than a hand-built copy of it (plan step balance:X-cf).  The
    envelope's budget is the template's stated price, which the derived row
    reads; the caller attaches entries via :func:`add_entry` and commits.

    Args:
        seed_user: The ``seed_user`` fixture dict.
        db_session: The test ``db.session``.
        period: The :class:`~app.models.pay_period.PayPeriod` to place the
            instance in.
        name: The template / transaction name.
        estimated: The envelope's estimated (budget) amount (Decimal).

    Returns:
        The generated :class:`~app.models.transaction.Transaction` (flushed).
    """
    template = make_expense_template(
        db_session, seed_user, amount=str(estimated),
        name=name, category_key="Groceries", is_envelope=True,
    )
    return generate_row_of(template, period)


def add_entry(  # pylint: disable=too-many-arguments,too-many-positional-arguments
    db_session, seed_user, txn, amount, purchased_on,
    *, is_credit=False, settled_on=None, description="purchase",
):
    """Attach one :class:`TransactionEntry` of ``amount`` to ``txn``.

    The shared entry builder so the stereotyped entry construction is not
    copied per suite (a duplicate-code finding).  Flushes; the caller commits.

    **The three bucket flags are parameters (plan step X-c2c2a).**  It built a
    DEBIT-only, outstanding entry until the per-row valuation tests moved to
    ``test_cash_amounts.py``, which needs all three buckets of
    ``cash_ledger._amounts._entry_checking_impact`` -- settled debit,
    outstanding debit, and credit.  Narrowness is why the helper had not
    displaced the hand-rolled copies it exists to prevent: seven suites still
    define their own ``_add_entry`` because this one could not express their
    shape.  Widening it is additive; those suites are not touched here (scope).

    **The reconciled bucket is a DATE, not a flag** (plan step S1-c, ruling
    R-DH (d)).  The stored ``is_cleared`` boolean this replaced was written by
    a bulk UPDATE at anchor true-up, so which bucket a purchase fell in
    depended on the order two buttons were pressed.  A fixture that wants the
    "already inside the asserted balance" bucket therefore passes a
    ``settled_on`` on or before the account's latest ``observed_on`` -- which
    makes the precondition it depends on VISIBLE at the call site instead of
    hidden behind a boolean.

    Args:
        db_session: The test ``db.session``.
        seed_user: The ``seed_user`` fixture dict (supplies ``user_id``).
        txn: The parent :class:`~app.models.transaction.Transaction`.
        amount: The entry amount (Decimal).
        purchased_on: The day the purchase was made.  Never after the user's
            today, which the service refuses (ruling R-M).
        is_credit: True for a credit-card purchase, which never hits checking
            directly (it leaves via the CC Payback sibling) and so only
            REDUCES the reservation whatever its dates say.
        settled_on: The day the bank took the money, or ``None`` (the default)
            for a purchase not yet seen on a statement.  A purchase whose
            ``settled_on`` is at or before the account's latest asserted day is
            already inside that balance and is SUBTRACTED from the reservation;
            every other purchase acts as its floor.  Must not precede
            *purchased_on* (``ck_transaction_entries_settled_not_before_purchase``).
        description: The entry description; the default suits a test that
            does not assert on it.
    """
    # pylint: disable=import-outside-toplevel  -- same circular-dep
    # avoidance as the loan helpers above.
    from app.models.transaction_entry import TransactionEntry

    db_session.add(TransactionEntry(
        transaction_id=txn.id, account_id=txn.account_id, owner_id=txn.user_id,
        user_id=seed_user["user"].id,
        amount=amount,
        description=description,
        purchased_on=purchased_on,
        **settle_day_columns(settled_on),
        **figure_source_columns(),
        is_credit=is_credit,
    ))
    db_session.flush()


def payback_row_of(db_session, seed_user, envelope, amount, purchased_on):
    """Return the CC PAYBACK the app mints for a credit purchase under *envelope*.

    **The suite's builder for a payback row, on the app's own producer**
    (plan step ``balance:X-bi-7c``, ruling **R-BAL59**).  A payback
    is not a one-off: it carries ``credit_payback_for_id`` as its ONE
    pricing link (``ck_transactions_one_pricing_link``) and is priced by
    the row it repays, and the application has exactly one constructor for
    it -- ``credit_workflow.create_cc_payback_transaction``, which writes it
    into the paycheck AFTER its source's (the one each door hands it) --
    reached by two doors:
    ``credit_workflow.mark_as_credit`` and the one this calls,
    ``entry_credit_workflow.sync_entry_payback``, run after every entry
    mutation.  Four cases built a row through a one-off builder and then set the link
    on it, which the CHECK refuses once that row carries a definition; this
    records the credit purchase through :func:`add_entry` and lets the
    producer answer.

    Args:
        db_session: The test ``db.session``.
        seed_user: The ``seed_user`` fixture dict (the owner).
        envelope: The purchase-tracking
            :class:`~app.models.transaction.Transaction` the card was used
            for; flushed, in a paycheck that has a NEXT one.
        amount: The credit purchase's figure (:class:`~decimal.Decimal`),
            which is what the payback repays.
        purchased_on: The day of the purchase; never after the owner's
            today (ruling R-M).

    Returns:
        The Projected payback :class:`~app.models.transaction.Transaction`,
        flushed, in the paycheck after *envelope*'s.
    """
    # pylint: disable=import-outside-toplevel  -- the module convention.
    from app.services.entry_credit_workflow import sync_entry_payback

    add_entry(
        db_session, seed_user, envelope, amount, purchased_on, is_credit=True,
        description="card purchase",
    )
    payback = sync_entry_payback(envelope.id, seed_user["user"].id)
    assert payback is not None and payback.credit_payback_for_id == envelope.id
    return payback


def mark_purchase_settled(db_session, account, entry, settled_on=None):
    """Record the day the bank took *entry*, and CHECK the anchor covers it.

    The successor to ``add_entry(..., is_cleared=True)`` (plan step S1-c,
    ruling R-DH (d)).  The retired boolean asserted "this purchase is already
    inside the anchor balance" unconditionally, so a fixture could set it on an
    account whose only assertion PREDATED the purchase -- a state production
    cannot reach, because the way a purchase gets inside a declared balance is
    that the user declared the balance after it posted.  That is finding
    N-132 / R8's shape: a fixture asserting a state the app cannot produce
    passes for years and stops discriminating the case it names.

    So this helper does what the flag did AND states the precondition the flag
    let fixtures skip.  It fails loudly rather than silently building an
    unreachable row, and the failure message names both days.

    **The bound is TWO-SIDED, and the upper half was added after the guard's
    first pass let an unreachable row through anyway.**  Checking only
    ``settled_on <= observed_on`` invites the obvious escape: move the
    ASSERTION forward until it covers the purchase.  But an assertion is itself
    bounded -- ``anchor_service.resolve_observation_day`` refuses an
    ``observed_on`` after the user's today, because a balance you have not read
    yet is not an observation -- so an assertion dated into the app's future is
    exactly as unreachable as the anchor-months-earlier row the guard was
    written to stop.  A one-sided guard turns finding N-132's defect into a
    different N-132 defect, which is what the S1-c conversion's own review
    measured on two ``test_cash_fold`` fixtures.  Both halves are checked here
    so neither escape is open.

    The clock is :func:`app.utils.dates.display_today` -- the same one the
    service guard reads, and the one a suite's ``freeze_today`` moves -- never
    ``date.today()``, which would judge a fixture on the process timezone.

    Args:
        db_session: The test ``db.session``.
        account: The :class:`~app.models.account.Account` the purchase's parent
            transaction belongs to -- the account whose assertions decide
            whether the purchase is inside a declared balance.
        entry: The :class:`~app.models.transaction_entry.TransactionEntry` to
            settle.
        settled_on: The day the bank took it.  Defaults to the entry's own
            ``purchased_on`` -- "it posted the day I bought it", the shape a
            same-day debit has.

    Returns:
        The updated entry (flushed).

    Raises:
        AssertionError: When the account has never asserted a balance, when its
            latest assertion is for a day BEFORE *settled_on*, or when that
            assertion is dated after the user's today.  In the first two the
            purchase reads as OUTSTANDING however this helper is spelled; in
            the third the account is in a state its own write door refuses.
            Either way a fixture expecting the settled bucket is asking for
            something production cannot produce.
    """
    # pylint: disable=import-outside-toplevel  -- same circular-dep
    # avoidance as the loan helpers above.
    from app.services.cash_ledger import reconciled_through
    from app.utils.dates import display_today

    settled_on = settled_on or entry.purchased_on
    # The guard asks the PRODUCTION rule, not a lookalike: if this helper
    # spelled the comparison itself it could come to disagree with the
    # reservation it exists to set up, which is the whole shape plan step X-f
    # is about.
    boundary = reconciled_through(account.id)
    observed_on = boundary.observed_day
    assert observed_on is not None, (
        f"account id={account.id} has asserted no balance, so no purchase can "
        f"be inside one; give it an assertion before settling entry "
        f"id={entry.id}"
    )
    assert boundary.covers(settled_on), (
        f"entry id={entry.id} settled {settled_on} is AFTER account "
        f"id={account.id}'s latest asserted day {observed_on}, so the "
        f"projection reads it as outstanding.  Move the account's assertion to "
        f"{settled_on} or later, or the fixture is asserting a state "
        f"production cannot produce."
    )
    today = display_today()
    assert observed_on <= today, (
        f"account id={account.id}'s latest assertion is dated {observed_on}, "
        f"which is AFTER the user's today ({today}); "
        f"anchor_service.resolve_observation_day refuses that at the "
        f"write door, so no production account can be in this state.  Move the "
        f"assertion (and entry id={entry.id}'s dates) back inside the clock "
        f"this suite runs on rather than forward past it."
    )
    # **The pair, through the app's OWN pair writer** (plan step **X-az**): a day
    # written without the basis that says how it is known is unstorable, and a
    # fixture that states half of one builds a row no door could write.
    #
    # **``entered``, and NOT ``asserted``, which a first version wrote** (found
    # by adversarial review 2026-08-22).  The temptation is that this helper's
    # precondition is about an assertion COVERING the day -- but the app's only
    # ``asserted`` writer is ``reconcile_service._purchases.record_settled_days``,
    # which in ONE statement writes the anchor's own ``observed_on`` AND the link
    # naming it.  This helper writes neither: its day is the caller's and may be
    # strictly earlier than the assertion, and it sets no link.  Calling that
    # ``asserted`` builds a row the app cannot reach -- the migration's own
    # backfill classifies exactly that row ``entered`` -- and the matcher would
    # then hand it the BOUND branch of ``expected_window``, a span, for a row
    # production would call a point.  That is finding **N-132**'s shape inside
    # the helper written to prevent N-132.  A caller that wants the panel's own
    # state ticks through the panel.
    # pylint: disable-next=import-outside-toplevel
    from app.services.settle_day import record_settle_day

    record_settle_day(entry, an_entered_day(settled_on))
    db_session.flush()
    return entry


def default_settle_day(period, status_id):
    """Return the settle day a BARE-built fixture row takes for *status_id*.

    **One statement of the rule, for the two bare ``Transaction`` builders that
    need it** (:func:`add_txn` and ``test_calendar_service``'s module-local
    twin).  A row built in a settled status must carry a settle day -- a settled
    row without one is the state
    :func:`app.utils.balance_predicates.settled_day` refuses -- and the day is
    the pay period's ``start_date``, because that is precisely what every reader
    derived for such a row before plan step X-f1: these builders never set
    ``paid_at``, so ``to_display_civil_date(None, period.start_date)`` answered
    the period start.  Stamping ``display_today()`` instead would silently move
    that cash to today and re-date whatever figure the fixture was built to
    grade.

    Args:
        period: The :class:`~app.models.pay_period.PayPeriod` the row sits in.
        status_id: The ``ref.statuses.id`` the row is built in.

    Returns:
        ``period.start_date`` for a settled status, ``None`` for any other.
    """
    # pylint: disable=import-outside-toplevel  -- same circular-dep
    # avoidance as the loan helpers above.
    from app.utils.balance_predicates import settled_status_ids

    return period.start_date if status_id in settled_status_ids() else None


def typed(amount):
    """Return *amount* as a figure a PERSON stated.

    The :class:`~app.services.stated_figure.StatedFigure` every human door
    builds (plan step **X-bi-3e-1**, ruling **R-BAL69**), for a fixture that
    plays one: the add-purchase form, the entry PATCH, the popovers, the
    reconcile panel.  A test that means the BANK stated the figure says
    :func:`observed`; the two names keep the writer visible at every call
    site, which is the fact the step exists to record.

    Args:
        amount: The figure, a ``Decimal``.

    Returns:
        The ``typed`` :class:`~app.services.stated_figure.StatedFigure`.
    """
    from app.enums import MovementFigureSourceEnum
    from app.services.stated_figure import StatedFigure
    return StatedFigure(amount=amount, source=MovementFigureSourceEnum.TYPED)


def observed(amount):
    """Return *amount* as a figure the BANK's line stated.

    :func:`typed`'s twin for a fixture that plays the statement matcher, the
    one writer that states ``observed`` (plan step **X-bi-3e-1**, ruling
    **R-BAL61**).

    Args:
        amount: The figure, a ``Decimal``.

    Returns:
        The ``observed`` :class:`~app.services.stated_figure.StatedFigure`.
    """
    from app.enums import MovementFigureSourceEnum
    from app.services.stated_figure import StatedFigure
    return StatedFigure(
        amount=amount, source=MovementFigureSourceEnum.OBSERVED,
    )


def settlement_if_settling(txn, new_status_id, submitted=None):
    """Return the :class:`Settlement` a fixture owes the seam, or ``None``.

    A fixture that drives ``status_seam.apply_status_change`` directly is
    settling a row the long way round, and since plan step X-au-c3 a settle
    states WHAT MOVED as well as when.  This answers what such a fixture means
    -- and it answers ARM FOR ARM the way the real verbs do, because a fixture
    producing a record no door can write builds a row the app could never have
    created, and every test over that row grades the wrong branch in silence.

    The three arms, mirroring
    ``transaction_service._settle.settle_transaction`` and
    ``transfer_service._settle.settle``:

      * an ENVELOPE (``settles_from_entries``) records the ``purchases`` basis
        and stores NO figure -- its own entries state it.  Taking the
        ``derived`` arm here stored the PLAN instead, so
        ``row_valuation.settled_figure`` answered a stored column for every
        fixture-built settled envelope where the app answers from its children;
      * a RETAINED ``corrected`` record outlives the settle that made it and a
        re-settle honours it, so it is threaded into
        :meth:`~app.services.status_seam.Settlement.from_settle`;
      * the ECHO rule (finding **N-231**): a *submitted* figure equal to what
        the row would book anyway is not a correction, so it records
        ``derived`` rather than manufacturing a human's figure nobody typed.

    ``None`` for a move that does not ENTER the settled band, which the seam
    requires: a record offered beside a Projected / Credit / Cancelled status is
    refused, and leaving the band releases the ASSERTION while KEEPING the
    record.

    **What the settle BOOKS is the app's own published rule**
    (``transaction_service.settle_amount`` over a basis built the way the verb
    builds one), not the row's ``estimated_amount`` column.  It WAS that column
    until plan step balance:X-au-e, on the stated ground that "such rows own
    their plan, so the two agree" -- true while a generated row stored a figure
    and false the moment one stopped.  A generated row is DERIVED now and its
    column is NULL, so the old spelling handed the seam
    ``Settlement(amount=None, source=None)`` and every fixture that settles a
    generated row died on the record's own refusal: *a 'derived' settlement
    must state the figure that moved*.  Reading the rule instead is what makes
    this helper's promise -- that it answers ARM FOR ARM the way the real verbs
    do -- true again rather than true of the rows fixtures happened to build.

    Args:
        txn: The row being moved, at its PRE-move status.
        new_status_id: The status the fixture is moving it to.
        submitted: A figure a human typed, when the fixture is exercising a
            correction.

    Returns:
        The ``Settlement`` to hand the seam, or ``None``.
    """
    # pylint: disable=import-outside-toplevel  -- the lazy-app-import
    # convention every helper in this module follows.
    from app.services.status_seam import Settlement, recorded_settlement
    from app.services.cash_ledger import derived_amount_basis
    from app.services.transaction_service import (
        settle_amount,
        settles_from_entries,
    )
    from app.utils.balance_predicates import enters_settled_band

    if not enters_settled_band(txn, new_status_id):
        return None
    if settles_from_entries(txn):
        return Settlement(amount=None, source=None)
    # ONE basis for the whole act, exactly as ``settle_transaction`` builds it,
    # and ``settle_amount``'s second arm IS the retained correction -- so this
    # asks the same rule once rather than re-branching on
    # ``honoured_correction`` beside it.
    booked = settle_amount(
        txn, derived_amount_basis(txn.account.user_id, txn.scenario_id),
    )
    # A fixture's figure is a PERSON's, stated as such (plan step X-bi-3e-1,
    # ruling R-BAL69): the verbs take the figure and its writer as one value.
    correction = (
        typed(submitted) if submitted is not None and submitted != booked
        else None
    )
    return Settlement.from_settle(booked, correction, recorded_settlement(txn))


def settled_day_basis_id(basis):
    """Return one ``ref.settled_day_bases`` id, for a fixture that states it.

    The narrow companion of :func:`settle_day_columns` (plan step **X-az**),
    for a fixture writing the pair through raw SQL -- a simulated concurrent
    ``UPDATE`` -- where there is no model instance for the pair writer to
    take.

    Resolved through ``ref_cache`` like every other ref value, so a fixture
    names the BASIS and never an id.

    Args:
        basis: The :class:`app.enums.SettledDayBasisEnum` member to resolve.

    Returns:
        The ``ref.settled_day_bases.id``.
    """
    # pylint: disable=import-outside-toplevel  -- the lazy-app-import
    # convention every helper in this module follows.
    from app import ref_cache

    return ref_cache.settled_day_basis_id(basis)


def an_entered_day(day):
    """Return *day* as a settle day the OWNER stated -- the ``entered`` basis.

    The three ``*_day`` builders below are what a test hands a settle door now
    that the door takes the PAIR rather than a bare ``date`` (plan step
    **X-az**): the day, and how that day is known.  They exist so a call site
    reads as the fact it is asserting instead of as a two-line construction, and
    so the BASIS is visible at the site -- which is the whole subject of the
    step, and would be invisible if a helper defaulted it.

    ``entered`` is what a manual Mark Paid on an ordinary row, a full-edit date
    box and every fixture standing in for one records: the owner's own day,
    with no bank document behind it.  (A Paid press on a TRANSFER records
    ``borrowed`` on both sides since plan step ``balance:X-bi-6-4c-3``: it
    states no day, see :func:`create_settled_transfer`.)

    Args:
        day: The civil ``date``.

    Returns:
        Its :class:`app.services.settle_day.SettleDay` on the ``entered`` basis.
    """
    # pylint: disable=import-outside-toplevel  -- same lazy-app-import
    # convention every helper in this module follows.
    from app.enums import SettledDayBasisEnum
    from app.services.settle_day import SettleDay

    return SettleDay(day=day, basis=SettledDayBasisEnum.ENTERED)


def on_both_sides(from_account_id, to_account_id, settle_day):
    """Return ``side_days`` stating ONE day for BOTH sides of a transfer.

    What the one-box transfer popover meant before plan step
    ``balance:X-bi-6-4c-3``: one day, stated for the whole transfer, on one
    basis (ruling **R-BAL165** reads a pre-step typed day as "typed on both
    ends").  A test written against that door re-expresses its pair day
    through this, and so leaves every stored row exactly as the old door wrote
    it; a test whose subject is ONE side's day states a single
    :class:`~app.services.transfer_service.SideDay` instead, and the other side
    borrows it.

    Args:
        from_account_id: The account the money leaves (the expense side).
        to_account_id: The account it arrives at (the income side).
        settle_day: The :class:`~app.services.settle_day.SettleDay` both sides
            state -- :func:`an_entered_day` and its two siblings build one.

    Returns:
        A two-item tuple for a transfer verb's ``side_days``.
    """
    # pylint: disable=import-outside-toplevel  -- same lazy-app-import
    # convention every helper in this module follows.
    from app.services.transfer_service import SideDay

    return (
        SideDay(from_account_id, settle_day), SideDay(to_account_id, settle_day),
    )


def an_asserted_day(day):
    """Return *day* as the day a BALANCE was asserted for -- an upper BOUND.

    What the reconcile panel records: the owner asserted a balance for this day
    and this money was inside it, so the true posting day is on or BEFORE it.
    :func:`an_entered_day` carries why these builders exist.

    Args:
        day: The civil ``date`` the balance was asserted for.

    Returns:
        Its :class:`app.services.settle_day.SettleDay` on the ``asserted``
        basis.
    """
    # pylint: disable=import-outside-toplevel  -- same lazy-app-import
    # convention every helper in this module follows.
    from app.enums import SettledDayBasisEnum
    from app.services.settle_day import SettleDay

    return SettleDay(day=day, basis=SettledDayBasisEnum.ASSERTED)


def an_observed_day(day):
    """Return *day* as a day a BANK STATEMENT showed the money post.

    What the statement matcher records.  :func:`an_entered_day` carries why
    these builders exist.

    Args:
        day: The civil ``date`` the bank posted the movement.

    Returns:
        Its :class:`app.services.settle_day.SettleDay` on the ``observed``
        basis.
    """
    # pylint: disable=import-outside-toplevel  -- same lazy-app-import
    # convention every helper in this module follows.
    from app.enums import SettledDayBasisEnum
    from app.services.settle_day import SettleDay

    return SettleDay(day=day, basis=SettledDayBasisEnum.OBSERVED)


def settle_day_columns(settled_on, basis=None):
    """Return the settle-day COLUMN PAIR a bare-built fixture row owes.

    Plan step **X-az**: ``settled_on`` and ``settled_day_basis_id`` are one
    fact in two columns, welded by each table's
    ``ck_*_settle_day_basis_pairing`` BICONDITIONAL, so a bare
    ``Transaction(settled_on=...)`` that names only the day is an
    ``IntegrityError`` at flush rather than a row.  (Its twin for the RECORD,
    ``settlement_columns``, laid the row's own ``settled_amount`` /
    ``settled_basis_id`` until plan step ``balance:X-bi-4b-2`` deleted those
    columns; the record is the covering movement
    :func:`cover_bare_settled_row` writes.)

    Every bare builder goes through this rather than spelling the pair, for the
    reason the app has ONE writer for it
    (:func:`app.services.settle_day.record_settle_day`): a fixture that states
    half of a pair builds a row no door could have written, and every test over
    that row grades a state the app cannot reach.

    **The default basis is ``entered``**, which is what a bare-built settled row
    MEANS: nobody imported a statement and nobody reconciled a balance to make
    it, so the day is the fixture's own assertion -- exactly what a manual Mark
    Paid records.  A suite grading the matcher's window rule passes
    ``SettledDayBasisEnum.ASSERTED`` or ``.OBSERVED`` explicitly, because there
    the KIND of day is the subject.

    Args:
        settled_on: The civil day, or ``None`` for a row that carries none.
        basis: The :class:`~app.enums.SettledDayBasisEnum` member, or ``None``
            for the ``entered`` default.  Ignored when *settled_on* is ``None``,
            where the pair is both-NULL.

    Returns:
        ``{"settled_on": ..., "settled_day_basis_id": ...}``, ready to splat
        into a model constructor.
    """
    # pylint: disable=import-outside-toplevel  -- same lazy-app-import
    # convention every helper in this module follows.
    from app import ref_cache
    from app.enums import SettledDayBasisEnum

    if settled_on is None:
        return {"settled_on": None, "settled_day_basis_id": None}
    member = SettledDayBasisEnum.ENTERED if basis is None else basis
    return {
        "settled_on": settled_on,
        "settled_day_basis_id": ref_cache.settled_day_basis_id(member),
    }


def figure_source_columns(member=None):
    """Return the FIGURE-SOURCE column a bare-built purchase owes.

    The figure's twin of :func:`settle_day_columns` (plan step **X-bi-3a**,
    ruling **R-BAL39**): ``transaction_entries.figure_source_id`` is NOT NULL
    with no default, because both of the app's writers of a movement state who
    wrote its figure and a stored guess is the shape ruling R-IY deletes.  A
    bare ``TransactionEntry(...)`` that says nothing is therefore an
    ``IntegrityError`` at flush rather than a row, and every bare builder goes
    through this rather than spelling the id.

    **The default is ``typed``**, which is what a bare-built purchase MEANS:
    nobody imported a statement line and no settle resolved it, so a person
    stated the figure -- exactly what the entry form records.  A suite grading
    the source itself passes ``MovementFigureSourceEnum.OBSERVED`` or
    ``.RESOLVED`` explicitly, because there the source is the subject.

    Args:
        member: The :class:`~app.enums.MovementFigureSourceEnum` member, or
            ``None`` for the ``typed`` default.

    Returns:
        ``{"figure_source_id": ...}``, ready to splat into the constructor.
    """
    # pylint: disable=import-outside-toplevel  -- same lazy-app-import
    # convention every helper in this module follows.
    from app import ref_cache
    from app.enums import MovementFigureSourceEnum

    chosen = MovementFigureSourceEnum.TYPED if member is None else member
    return {"figure_source_id": ref_cache.movement_figure_source_id(chosen)}


def transfer_family_journal_filter(transfer_id):
    """Return the SQL clause selecting the journal entries of a TRANSFER's family.

    **A settled transfer's money is posted under its two covering movements
    since plan step ``balance:X-bi-6-3``** (rulings **R-BAL45** and
    **R-BAL101**): one entry per side, linked by
    ``journal_entries.transaction_entry_id`` to the movement covering that
    side (the side's record; it hung off the side's shadow until the
    re-parent below), each against the owner's Transfers-in-transit account.  A
    suite read "the transfer's postings" by ``JournalEntry.transfer_id`` until
    plan step ``balance:X-bi-6-5``'s second leaf (card plan#1845) deleted that
    link with the LEGACY one-entry shape it named, which no go-forward settle
    wrote.  This is the ONE spelling of the transfer family read -- the
    records of BOTH sides of the transfer -- so the cases the developer
    confirmed when the shape moved widen their subject the same way and no
    figure per real account moves.

    **Independent SQL over the side links** (plan step
    ``balance:X-bi-6-4d-2``, ruling **R-BAL167** class 4): a record hangs off
    the TRANSFER by ``expense_transfer_id`` / ``income_transfer_id`` since
    that step, and this reads those columns itself rather than through
    ``transfer_legs``, the producer the suite grades -- a helper selecting
    money through the producer under test cannot catch that producer's
    fault.  Until then it read the movements under every shadow of the
    transfer; a record keeps its id across the re-parent, so a journal entry
    that linked it still does.

    Args:
        transfer_id: The ``budget.transfers`` id.

    Returns:
        A SQLAlchemy boolean clause over ``JournalEntry``.
    """
    # pylint: disable=import-outside-toplevel -- same lazy-app-import
    # convention every helper in this module follows.
    from app.extensions import db
    from app.models.journal_entry import JournalEntry
    from app.models.transaction_entry import TransactionEntry

    movement_ids = db.session.query(TransactionEntry.id).filter(
        db.or_(
            TransactionEntry.expense_transfer_id == transfer_id,
            TransactionEntry.income_transfer_id == transfer_id,
        ),
    )
    return JournalEntry.transaction_entry_id.in_(movement_ids)


def transfer_side_journal_filter(transfer_id, account_id):
    """Return the SQL clause selecting ONE side's entries of a transfer's family.

    The entries linked to the record of the transfer's side on *account_id*
    -- the loan-side cash entry of a loan payment, or either side of a cash
    transfer -- since plan step ``balance:X-bi-6-3`` (a side is an entry of
    its own, rulings **R-BAL45** and **R-BAL101**).  The per-side twin of
    :func:`transfer_family_journal_filter`, and independent SQL over the side
    links for its reason: a side's record is on its endpoint (the side keys),
    so the side on *account_id* is the linked record on that account.

    Args:
        transfer_id: The ``budget.transfers`` id.
        account_id: The account the side is on.

    Returns:
        A SQLAlchemy boolean clause over ``JournalEntry``.
    """
    # pylint: disable=import-outside-toplevel -- same lazy-app-import
    # convention every helper in this module follows.
    from app.extensions import db
    from app.models.journal_entry import JournalEntry
    from app.models.transaction_entry import TransactionEntry

    movement_ids = db.session.query(TransactionEntry.id).filter(
        db.or_(
            TransactionEntry.expense_transfer_id == transfer_id,
            TransactionEntry.income_transfer_id == transfer_id,
        ),
        TransactionEntry.account_id == account_id,
    )
    return JournalEntry.transaction_entry_id.in_(movement_ids)


def transfer_side_record(db_session, transfer_id, account_id):
    """Return the record of a transfer's side on *account_id*, or ``None``.

    The movement filed under that side (plan step ``balance:X-bi-6-4d-2``,
    ruling **R-BAL88**): it hangs off the TRANSFER by ``expense_transfer_id``
    / ``income_transfer_id``, on its endpoint, at most one per side.  It hung
    off the side's shadow row until then (``shadow.covering_movements``).
    The row-shaped twin of :func:`transfer_side_journal_filter`, and
    independent SQL over the side links for that helper's reason (ruling
    **R-BAL167** class 4): a test reading a side's record through
    ``transfer_legs``, the producer it grades, could not catch its fault.

    Args:
        db_session: The test ``db.session``.
        transfer_id: The ``budget.transfers`` id.
        account_id: The account the side is on.

    Returns:
        The side's :class:`~app.models.transaction_entry.TransactionEntry`,
        or ``None`` when the side holds none (an unsettled side never paid,
        or a ``$0.00`` close, ruling **R-BAL141**).
    """
    # pylint: disable=import-outside-toplevel -- same lazy-app-import
    # convention every helper in this module follows.
    from app.extensions import db
    from app.models.transaction_entry import TransactionEntry

    return (
        db_session.query(TransactionEntry)
        .filter(
            db.or_(
                TransactionEntry.expense_transfer_id == transfer_id,
                TransactionEntry.income_transfer_id == transfer_id,
            ),
            TransactionEntry.account_id == account_id,
        )
        .one_or_none()
    )


def side_records_by_key(xfer):
    """Return ``{(transfer id, account id): record}`` for each side of *xfer* holding one.

    The map ``transfer_legs.covering_movements_by_leg`` answered until ruling
    **plan#1984** deleted it, built from what replaced it: each side's leg
    reads its record off the transfer's list for that side
    (``TransferLeg.record``), both lists loaded in one statement
    (``transfer_legs.transfer_sides``).  The cases that asserted that
    map assert it unchanged through this, keyed by the side's ``cell_key``,
    which is the record's account (a record's account IS its side's
    endpoint, by the side keys).

    Args:
        xfer: The transfer, loaded.

    Returns:
        The map, holding a key only for a side that holds a record.
    """
    # pylint: disable=import-outside-toplevel -- same lazy-app-import
    # convention every helper in this module follows.
    from app.services import transfer_legs

    return {
        leg.cell_key: leg.record
        for leg in transfer_legs.transfer_sides(xfer)
        if leg.record is not None
    }


def transfer_side_figure(db_session, transfer_id, account_id):
    """Return what a transfer's side on *account_id* RECORDS, or ``None``.

    The side-shaped twin of ``row_valuation.settled_figure(shadow)``, which a
    test asked of the side's shadow until plan step ``balance:X-bi-6-4d-2``
    stopped keeping a shadow's status (ruling **R-BAL167** class 4): ``None``
    while the TRANSFER is not settled, and once it is, the figure the
    TRANSFER stores -- its one home since that step's 4d-4 half (rulings
    **R-BAL365**, **R-BAL366**), ``0.00`` for a ``$0.00`` close.  Until then
    it read the side's record's ``amount`` and inferred ``0.00`` from a side
    holding none, the inference ruling **R-BAL368** deletes.  The answers the
    shadow gave while its status mirrored its transfer's, so a re-pointed
    assertion keeps its expected figure.  Read off the stored column, never
    through the producers a test grades.

    Args:
        db_session: The test ``db.session``.
        transfer_id: The ``budget.transfers`` id.
        account_id: The account the side is on: one of the transfer's two
            endpoints (a transfer's figure is one, so the side names which
            account the caller is asserting about, and a wrong one fails).

    Returns:
        The stored ``Decimal``, or ``None`` when the transfer is not settled.
    """
    # pylint: disable=import-outside-toplevel -- same lazy-app-import
    # convention every helper in this module follows.
    from app.models.transfer import Transfer

    transfer = db_session.get(Transfer, transfer_id)
    assert account_id in (transfer.from_account_id, transfer.to_account_id), (
        f"account {account_id} is neither side of transfer {transfer_id}"
    )
    if not transfer.status.is_settled:
        return None
    return transfer.settled_figure


def transfer_side_settle_day(db_session, transfer_id, account_id):
    """Return the settle day a transfer's side on *account_id* records, or ``None``.

    The side-shaped twin of ``settle_day.recorded_settle_day(shadow)`` (ruling
    **R-BAL167** class 4, plan step ``balance:X-bi-6-4d-2``): the
    :class:`~app.services.settle_day.SettleDay` on the side's record, or
    ``None`` when the side holds no record or its record carries no day (a
    side never paid, a revert's kept record, a ``$0.00`` close).

    **Decoded by its own query, never the app's decoder** (the X-bi-6-4d-2
    leaf review's L-7): it read the record through
    ``settle_day.recorded_settle_day``, the column decoder the writer's own
    day resolution uses, so a decoder that mis-mapped a basis would have
    agreed with itself here.  It reads the day and the basis's ``ref`` NAME
    straight off the tables, and :class:`~app.enums.SettledDayBasisEnum`'s
    values ARE those names, as :func:`transfer_side_record` and
    :func:`transfer_side_figure` are already independent SQL.

    Args:
        db_session: The test ``db.session``.
        transfer_id: The ``budget.transfers`` id.
        account_id: The account the side is on.

    Returns:
        The side's ``SettleDay``, or ``None``.
    """
    # pylint: disable=import-outside-toplevel -- same lazy-app-import
    # convention every helper in this module follows.
    from app.enums import SettledDayBasisEnum
    from app.extensions import db
    from app.models.ref import SettledDayBasis
    from app.models.transaction_entry import TransactionEntry
    from app.services.settle_day import SettleDay

    stored = (
        db_session.query(TransactionEntry.settled_on, SettledDayBasis.name)
        .outerjoin(
            SettledDayBasis,
            SettledDayBasis.id == TransactionEntry.settled_day_basis_id,
        )
        .filter(
            db.or_(
                TransactionEntry.expense_transfer_id == transfer_id,
                TransactionEntry.income_transfer_id == transfer_id,
            ),
            TransactionEntry.account_id == account_id,
        )
        .one_or_none()
    )
    if stored is None or stored.settled_on is None:
        return None
    return SettleDay(
        day=stored.settled_on, basis=SettledDayBasisEnum(stored.name),
    )


def family_journal_filter(txn):
    """Return the SQL clause selecting the journal entries of *txn*'s FAMILY.

    **A settled bill's money is posted under its covering movement since plan
    step X-bi-3a** (ruling **R-BAL39**): the status seam mirrors the
    settlement as one ``transaction_entries`` row, the bill's own leg reads
    zero, and the posting writer files the money under
    ``journal_entries.transaction_entry_id`` (the purchase source) rather than
    under ``transaction_id``, a link plan step ``balance:X-bi-6-5``'s second
    leaf (card plan#1845) deleted with the row's own ``transaction`` leg.  A
    suite that read "the row's postings" by ``JournalEntry.transaction_id``
    alone read an empty ledger for a covered bill.  This is the ONE spelling
    of the family read, so the 38 cases the developer confirmed on 2026-09-15
    widen their subject the same way and no figure moves: the row's
    movements' entries.

    The movements are read through ``Transaction.entries`` -- EVERY movement
    of the row since plan step ``balance:X-bi-4a`` (ruling **R-BAL80**: the
    fold and the writer read a row's money as its movements alone, a dated
    purchase's leg as much as the covering movement's; through ``X-bi-3e``
    this read the covering movements only, while the row's own leg carried
    an envelope's un-dated purchases).  A revert KEEPS the mirror, un-dated
    (plan step **X-bi-3e-2**, ruling **R-BAL61**), so a reverted row's family
    includes its survivor's linked postings -- a net-zero pair once the
    door's reconcile has reversed them -- where through 3e-1 the mirror was
    deleted and the pair stood unlinked.  Every reader of this filter sums a
    net or scopes by period, so the pair changes no figure.

    Args:
        txn: The :class:`~app.models.transaction.Transaction`, or its id.

    Returns:
        A SQLAlchemy boolean clause over ``JournalEntry``.
    """
    # pylint: disable=import-outside-toplevel  -- same lazy-app-import
    # convention every helper in this module follows.
    from app.extensions import db
    from app.models.journal_entry import JournalEntry
    from app.models.transaction import Transaction

    row = txn if isinstance(txn, Transaction) else db.session.get(Transaction, txn)
    if row is None:
        # A hard-deleted row: its movements' postings were reversed, then the
        # delete took the movements off the books (``movement_removal``) and
        # each entry's link SET-NULLed as its movement went, so no entry names
        # the family any more -- nothing, which is the claim such a case
        # makes.
        return db.false()
    return JournalEntry.transaction_entry_id.in_(
        [movement.id for movement in row.entries],
    )


def purchases_of(txn):
    """Return *txn*'s entries that are PURCHASES: its rows less the seam's mirror.

    Plan step **X-bi-3a**: a settled bill or an envelope closed empty holds
    one covering movement, written by the status seam and never by a person,
    so "this row took no purchase" is graded over the entries that are not
    that mirror -- the app's own reading, ``Transaction.purchases`` (plan
    step **X-bi-3e-2**, ruling **R-BAL68**), which this helper hand-rolled
    until that reading existed.  A refused purchase still leaves the row with
    exactly the movement it had.

    Args:
        txn: The :class:`~app.models.transaction.Transaction`, or its id.

    Returns:
        The purchases, in ``entries`` order.
    """
    # pylint: disable=import-outside-toplevel  -- same lazy-app-import
    # convention every helper in this module follows.
    from app.extensions import db
    from app.models.transaction import Transaction

    row = txn if isinstance(txn, Transaction) else db.session.get(Transaction, txn)
    return row.purchases


def add_txn(  # pylint: disable=too-many-arguments,too-many-positional-arguments
    db_session, seed_user, period, name, amount,
    status_enum=None, is_income=False,
    due_date=None, category_key=None, is_deleted=False,
    settled_amount=None, account=None, scenario=None,
    settled_on=_UNSET_SETTLED_ON,
):
    """Create a projected (default) transaction on the seed user's account.

    The shared bare-Transaction builder for the dashboard suites (the
    route, shared-helper, and pulse-producer tests all built an identical
    ``_add_txn`` -- a duplicate-code finding).  Builds an income or expense
    row with optional actual amount, due date, category, soft-delete flag,
    and status.  Flushes; the caller commits.

    **A row built in a SETTLED status gets a settle day here, and the default
    is the pay period's ``start_date``** (plan step X-f1).  It is deliberately
    the period start and not today: this builder never set ``paid_at``, so
    every settled row it produced used to reach the readers as
    ``to_display_civil_date(None, period.start_date)`` -- the period start --
    which is the day every hand-computed figure in the suites that consume it
    was written against.  It is also the day migration ``a3f7c8e21b64``'s
    backfill gave the real rows in the same shape.  Stamping ``display_today()``
    instead would silently move that cash to today and re-date whatever
    balance, period subtotal or ledger entry the fixture was built to grade.

    It stays a BARE builder -- it does not route through
    ``status_seam.apply_status_change`` -- because that is its purpose: several
    suites need "a settled row the posting writer has never seen", and the seam
    would additionally refuse ``Projected -> Settled``, which is not a legal
    transaction transition but is a legitimate STARTING state for a fixture.
    What it may not produce is an INCOHERENT row: a settled row with no settle
    day is the state ``balance_predicates.settled_day`` refuses, and every
    reader of it would raise -- **and since plan step ``balance:X-bi-4a`` a
    settled row with no COVERING MOVEMENT is one too** (ruling **R-BAL80**):
    the fold and the posting writer read a settled row's money as its
    movements alone, so a bare row carrying only the record columns would be
    money no reader can see, the pre-``X-bi-3d`` shape the cutover migration
    exists to end.  So a settled row built here also carries the covering
    movement the seam would have written for it, through the seam's own
    writer (``sync_covering_movement``, the ONE producer of the mirror) and
    not a fixture's restatement; the writer has still never seen it, which is
    what the suites that need that state rely on.

    Args:
        db_session: The test ``db.session``.
        seed_user: The ``seed_user`` fixture dict (supplies the account,
            scenario, and categories).
        period: The :class:`~app.models.pay_period.PayPeriod` to place the
            transaction in.
        name: The transaction name.
        amount: The estimated amount (str or Decimal-coercible).
        status_enum: The :class:`~app.enums.StatusEnum` member; defaults to
            ``StatusEnum.PROJECTED`` (resolved lazily to avoid importing
            the enum at module-load time).
        is_income: When True, an income row; otherwise an expense row.
        due_date: The transaction's due date, or ``None``.
        category_key: A key into ``seed_user["categories"]`` to set the
            category, or ``None`` for no category.
        is_deleted: The soft-delete flag.
        actual_amount: The actual (tier-3) amount, or ``None``.
        account: The :class:`~app.models.account.Account` the row lands on;
            defaults to ``seed_user["account"]`` (the Checking account).
        scenario: The :class:`~app.models.scenario.Scenario` the row lives in;
            defaults to the seed user's baseline.  Pass a non-baseline scenario
            to exercise multi-scenario isolation.
        settled_on: The civil day the row's money moved.  Omit it for the
            default described above (``period.start_date`` for a settled
            status, ``None`` for any other).  Pass a day to place the cash
            somewhere specific -- the shape a fixture needs when it grades WHICH
            day a balance steps on.  Passing ``None`` explicitly builds the
            broken settled-with-no-day row on purpose, which is what a negative
            control for :func:`app.utils.balance_predicates.settled_day` needs.

    Returns:
        The created :class:`~app.models.transaction.Transaction` (flushed).
    """
    # pylint: disable=import-outside-toplevel  -- same circular-dep
    # avoidance as the loan helpers above.
    from app import ref_cache
    from app.enums import StatusEnum, TxnTypeEnum
    if status_enum is None:
        status_enum = StatusEnum.PROJECTED
    account = seed_user["account"] if account is None else account
    scenario = seed_user["scenario"] if scenario is None else scenario
    status_id = ref_cache.status_id(status_enum)
    if settled_on is _UNSET_SETTLED_ON:
        settled_on = default_settle_day(period, status_id)
    type_id = (
        ref_cache.txn_type_id(TxnTypeEnum.INCOME)
        if is_income
        else ref_cache.txn_type_id(TxnTypeEnum.EXPENSE)
    )
    cat_id = None
    if category_key and category_key in seed_user["categories"]:
        cat_id = seed_user["categories"][category_key].id

    # The row is the PRODUCER's (plan step balance:X-bi-7c): a rule-less
    # definition priced at *amount* plus its placed row, dated on the
    # paycheck's start unless *due_date* says otherwise, exactly what the grid
    # writes for a one-off -- where this built a link-less row owning its
    # figure, the shape the cutover (X-bi-7d-2) deleted.  What stays BARE is the
    # state laid on top: the status, the soft-delete flag and the settle
    # columns, which is this builder's purpose (see above).
    txn = one_off_row_of(
        period, name=name, amount=amount,
        user_id=period.user_id, account_id=account.id,
        scenario_id=scenario.id, transaction_type_id=type_id,
        category_id=cat_id, due_date=due_date,
    )
    bare_state = dict(
        status_id=status_id,
        is_deleted=is_deleted,
        # The settle DAY and the basis that says how it is known, as one pair
        # (plan step **X-az**).  ``entered`` is what a bare-built settled row
        # means: no statement was imported and no balance reconciled to make it.
        **settle_day_columns(settled_on),
    )
    for column, value in bare_state.items():
        setattr(txn, column, value)
    db_session.flush()
    # The settlement RECORD (plan step X-au-c3; its one home the covering
    # movement since ``balance:X-bi-4b-2``).  A settled row states what moved
    # -- the typed figure when the caller gave one, else the row's own -- and
    # a row built WITHOUT a settle day records nothing, as the seam writes
    # nothing for a row outside the band.  (A row that carries the record
    # with no day is legal -- it is the RETAINED state -- but no factory
    # MEANS that; a fixture wanting it settles a row and then reverts it, as
    # the app does.)
    if settled_on is not None:
        cover_bare_settled_row(db_session, txn, amount, settled_amount)
    return txn


def independent_settled_figure():
    """Return a SQL expression for a row's settled figure, spelled independently.

    **An oracle's spelling of the record, for the integration suites that
    reconcile the posted ledger against the transaction source of truth**
    (``test_posting_ledger_*_reconciliation``).  The record is the row's
    entries since plan step ``balance:X-bi-4b-2`` deleted the row's own
    ``settled_amount`` (ruling **R-BAL80**), which those oracles read through
    ``COALESCE(settled_amount, estimated_amount)`` before; the app's own SQL
    twin is ``posting_reads.settled_figure_clause``, and an oracle that
    called it would grade one producer against itself, so this is a second
    spelling ON PURPOSE -- a correlated sum over ``budget.transaction_entries``
    written here, in the suite, and nowhere in ``app/``.  ``COALESCE`` to
    zero: a settled row with no entry is the ``$0.00`` record (R-BAL82).

    Returns:
        A correlated scalar subquery over :class:`TransactionEntry`, to be
        used inside a query whose FROM clause holds ``Transaction``.
    """
    # pylint: disable=import-outside-toplevel  -- the module convention.
    from sqlalchemy import func, select

    from app.models.transaction import Transaction
    from app.models.transaction_entry import TransactionEntry

    total = (
        select(func.sum(TransactionEntry.amount))
        .where(TransactionEntry.transaction_id == Transaction.id)
        .correlate(Transaction)
        .scalar_subquery()
    )
    return func.coalesce(total, Decimal("0"))


def cover_bare_settled_row(db_session, txn, amount, submitted=None):
    """Write the covering movement a bare-built settled row owes (R-BAL80).

    **The one door a bare-built settled row's RECORD goes through** (plan
    step ``balance:X-bi-4a``; the record's only home since ``X-bi-4b-2``
    deleted the row's own figure columns): the seam's own writer, over the
    record a settle would make, the plan's figure ``resolved`` when nobody
    typed one, else the typed figure ``typed`` (the same two arms
    ``Settlement.from_settle`` takes for a live settle).  A ``$0.00`` figure
    writes none, as the seam writes none -- the row's entries are its record
    (ruling **R-BAL82**).  :func:`add_txn` calls it; a suite that lays a
    settled row bare itself calls it after its flush, or every reader values
    the row as a close of nothing.

    Args:
        db_session: The test session; flushed after the write.
        txn: The settled row, flushed, its settle day laid.
        amount: The row's plan figure.
        submitted: The figure a fixture recorded as a human's correction, or
            ``None``.
    """
    # pylint: disable=import-outside-toplevel  -- the module convention.
    from app.enums import MovementFigureSourceEnum
    from app.services.status_seam import Settlement
    from app.services.status_seam._covering import sync_covering_movement

    if submitted is None:
        settlement = Settlement(
            amount=Decimal(str(amount)),
            source=MovementFigureSourceEnum.RESOLVED,
        )
    else:
        settlement = Settlement(
            amount=Decimal(str(submitted)),
            source=MovementFigureSourceEnum.TYPED,
        )
    sync_covering_movement(txn, now_settled=True, settlement=settlement)
    db_session.flush()


def generate_row_of(template, period):
    """Generate the ONE row of *template* in *period* through the engine.

    **The suite's one builder for a row of a definition** (plan step
    balance:X-cf, developer ruling 2026-09-11, the row-level twin of
    :func:`state_template_price`'s lesson).  A row that names a recurring
    definition has exactly one constructor in the application:
    ``recurrence_engine._generate._new_row``, which splats
    :class:`~app.services.recurrence_engine.DerivedRowFields` -- the account,
    name, category, type, amount OWNERSHIP and due date the definition derives
    -- onto the identity columns, with ``occurs_on`` from the cadence walk.
    This helper does not restate that line; it CALLS it, through
    :func:`~app.services.recurrence_engine.generate_for_template` with a
    window of exactly one paycheck (``GenerationSchedule.for_period_ids``,
    the shape carry-forward's generate branch uses), and hands back what the
    engine wrote.  So a fixture built here cannot disagree with the engine
    about what such a row IS, and a column the engine derives tomorrow lands
    on every fixture the day it lands on ``DerivedRowFields``.

    **What the hand-built rows were.**  Sixty-eight sites in thirty-nine files
    constructed ``Transaction(template_id=..., ...)`` by hand: undated (the
    state the CHECK plan step balance:X-bv-2 binds refuses), and most of
    them OWNING a figure with ``is_override=False`` -- the pre-X-au-e
    shape, which no producer has written since a generated row became derived.
    A control that hand-builds its subject stops grading the producer, and
    every one of those sites was grading a row the application cannot make.

    Two consequences follow from getting the engine's row, and each is the
    point rather than a cost:

    * **It is DERIVED.**  It stores no figure; its definition's price series
      answers for it on its own due date, so a fixture states the figure it
      expects through :func:`state_template_price` on the template.  A reader
      that asks the raw ``estimated_amount`` column gets ``None`` here exactly
      as it does on production, which is what such a reader deserves.
    * **It answers an OCCURRENCE** (``occurs_on``), so
      ``idx_transactions_template_scenario_occurrence`` holds over it, and a
      second ask for the same paycheck writes nothing: the engine's claim
      predicate (``_recurrence_common.OccurrenceClaims``) sees the first row
      before the index ever could, which is the 0-rows refusal below.

    A fixture wanting the OWNER's row -- a figure the human authored -- takes
    ownership of the generated row the way the re-price door does
    (:func:`repriced_by_the_owner`), and one wanting a row the owner MOVED
    moves this one the way the move door does (:func:`moved_by_the_owner`),
    rather than building one.  A fixture wanting a SETTLED row settles this
    one, as the app does; the engine only ever writes Projected.

    The row is generated into the owner's BASELINE scenario, which is what
    every door that generates passes; a fixture for another scenario is a
    scenario test's subject and authors the pass itself.  Because the
    baseline is the template owner's by construction, the engine's
    cross-user scenario gate cannot be reached from here.

    Args:
        template: The flushed
            :class:`~app.models.transaction_template.TransactionTemplate`.  It
            must carry a cadence (``recurrence_rule``): a definition with rows
            is one that repeats, and :func:`make_every_period_rule` is the
            ordinary way a fixture gives it one.
        period: The :class:`~app.models.pay_period.PayPeriod` row the
            generated row is funded in.  Must be one of the owner's saved
            periods.

    Returns:
        The :class:`~app.models.transaction.Transaction` the engine created,
        flushed.

    Raises:
        ValueError: *template* has no cadence, or the engine wrote no row in
            *period* -- its rule names no occurrence in that paycheck, or a
            row already claims it -- or wrote more than one, which a cadence
            firing several times inside one paycheck does and which is a
            generation test's subject rather than a fixture's.  **The 2+
            refusal leaves the rows it names FLUSHED**: the engine had
            already written them when the count was taken.
        RecurrenceWindowError: *period* is not one of the owner's saved
            periods (``GenerationSchedule.for_period_ids``), which is how
            another owner's paycheck is refused before any INSERT.
        BaselineMissingError: The owner has no baseline scenario
            (``BalanceContext.scenario_id``).
    """
    # Pylint: ``import-outside-toplevel`` -- this module imports no app
    # symbols at top level (its collection-time-safety convention).
    # pylint: disable=import-outside-toplevel
    from app.services import recurrence_engine
    return _the_one_row_the_engine_writes(recurrence_engine, template, period)


def one_off_row_of(  # pylint: disable=too-many-arguments
    period, *, name, amount, user_id, account_id, scenario_id,
    transaction_type_id, category_id=None, is_envelope=False,
    companion_visible=False, due_date=None,
):
    """Place the ONE row of a new one-off in *period* through the producer.

    **The suite's one builder for a ONE-OFF's row** (plan step
    balance:X-bi-7c), the twin of :func:`generate_row_of` for the other
    shape a plan item's row can take.  A one-off is a RULE-LESS DEFINITION
    plus its placed row (ruling **R-BAL20**; plan step ``balance:X-bi-7b``),
    and the application has exactly one constructor for it:
    :func:`app.services.one_off.place_one_off`, which mints the definition,
    opens its one-version price series dated on the row's due date
    (**R-BAL21**) and places the row through
    :func:`~app.services.one_off.place_row_of` -- both grid create doors,
    ``mint_uncategorized`` and bank import's envelope mint call it.  This
    helper does not restate any of that; it CALLS it, with the paycheck
    resolved through the OWNER's real calendar (``pay_calendar.calendar_for
    (user_id).period_by_id``, the read :func:`derived_span` makes for a
    period's own owner), and hands back what the producer wrote.  So a fixture built here cannot
    disagree with the app about what a one-off IS, and a column the
    producer derives tomorrow lands on every fixture the day it lands on
    ``unruled_row_fields``.

    **What the hand-built rows were.**  A link-less ``Transaction(...)`` --
    ``template_id`` NULL, OWNING its figure, its flags in its own sealed
    cells, undated in most cases -- is the shape the cutover
    (``balance:X-bi-7d-2``, migration ``596408fab6f1``) deleted: it minted
    every such production row a definition (34 on the 2026-09-18 restore),
    dated the undated on their paycheck's start and declared each
    ``TEMPLATE``-priced, then re-cut ``ck_transactions_one_pricing_link`` to
    ``= 1`` so the shape is unstorable.  Every hand-built site moved onto
    this builder before that (7c) so the CHECK binds on rows the app's own
    door wrote; the sites whose SUBJECT was the legacy shape itself -- the
    accessors' own-cell arm, the frozen flag cells -- built it by hand on
    ``legacy_link_less_row_of`` until 7d-2 retired them with the arm.  The
    one link-less row shape left is a CC payback, which has a builder of
    its own (:func:`payback_row_of`): a transfer's shadows were the other
    until plan step ``balance:X-bi-6-4d-2`` deleted them.

    Two consequences follow from getting the producer's row, each the point
    rather than a cost, exactly as for :func:`generate_row_of`:

    * **It is DERIVED.**  It stores no figure; its definition's one version
      answers for it on its own due date (amount rule 3), so *amount* here
      is what that version says.  A reader that asks the raw
      ``estimated_amount`` column gets ``None``, as it does on production
      for every row the grid has made since 7b-1.  A fixture wanting the
      OWNER's figure on the row takes ownership as the re-price door does
      (:func:`repriced_by_the_owner`); one wanting the definition's price
      corrected restates it (``one_off.restate_price``).
    * **It is DATED, and answers that day** (``due_date`` and ``occurs_on``,
      rulings **R-BAL22** / **R-BAL25**): the paycheck's start unless
      *due_date* says otherwise, so ``idx_transactions_template_scenario_
      occurrence`` holds over it.  A fixture that wants a row on the
      "anytime this period" shelf wants a row no definition prices -- a CC
      payback (:func:`payback_row_of`; the producer dates none), the one
      undated row shape left since the cutover and plan step
      ``balance:X-bi-6-4d-2``, which deleted the transfer shadows (a
      transfer with no ``due_date`` is not a row).  On the 2026-09-18
      production restore the paybacks were undated (31 of 31) and no
      shadow was.

    The row is placed PROJECTED, which is the only state the producer
    writes.  A fixture wanting a SETTLED row settles this one as the app
    does (``status_seam.apply_status_change``; :func:`add_txn` lays a bare
    settled state on for the suites that need a row the posting writer has
    never seen), and one wanting it soft-deleted or annotated sets
    ``is_deleted`` / ``notes`` on the returned row: those are the row's own
    columns, not the definition's.

    Args:
        period: The :class:`~app.models.pay_period.PayPeriod` row the one-off
            is placed in.  Must be one of the owner's saved periods.
        name: What the plan item is called (the definition's name, which the
            row reads).
        amount: What it is planned to cost, as a :class:`~decimal.Decimal`
            or anything ``Decimal(str(...))`` reads; ``0.00`` is legal.
        user_id: The owner.
        account_id: The account the money moves through.
        scenario_id: The scenario the row is placed in.
        transaction_type_id: Expense or income (``ref_cache.txn_type_id``).
        category_id: What the money is, or ``None`` (the bank's
            uncategorised row; every grid door requires one).
        is_envelope: Whether the definition's rows take purchase entries.
        companion_visible: Whether a companion may see its rows.
        due_date: The day it falls, or ``None`` for the paycheck's start.

    Returns:
        The placed :class:`~app.models.transaction.Transaction`, flushed,
        with ``row.template`` its rule-less definition.

    Raises:
        AssertionError: *period* is not in *user_id*'s calendar -- another
            owner's paycheck, or one never flushed -- refused BEFORE the
            definition or the row is written, one tier above the composite
            key ``fk_transactions_owner_period`` that would refuse the row's
            INSERT after the definition had already been minted.
    """
    # pylint: disable=import-outside-toplevel  -- the module convention.
    from app.services.one_off import OneOffToPlace, place_one_off
    from app.services.pay_calendar import calendar_for

    placed_in = calendar_for(user_id).period_by_id(period.id)
    assert placed_in is not None, (
        f"pay period {period.id} is not in user {user_id}'s calendar -- it "
        f"was never flushed, or it belongs to someone else"
    )
    return place_one_off(
        OneOffToPlace(
            user_id=user_id,
            account_id=account_id,
            transaction_type_id=transaction_type_id,
            name=name,
            amount=Decimal(str(amount)),
            category_id=category_id,
            is_envelope=is_envelope,
            companion_visible=companion_visible,
        ),
        placed_in,
        scenario_id=scenario_id,
        due_date=due_date,
    )


def generate_transfer_of(template, period):
    """Generate the ONE transfer of *template* in *period* through the engine.

    **The suite's one builder for a transfer of a definition** -- plan step
    balance:X-ch, ruling R-BAL17 applied to the twin table, finding BAL-488
    -- and :func:`generate_row_of` one table over.  A transfer that names a
    recurring definition has exactly one constructor in the application:
    ``transfer_recurrence._create_from_definition``, reached from the generate
    pass's ``_new_row`` and from the maintain pass's create arm alike, which
    splats ``DerivedTransferFields`` (the two accounts, name, category, amount
    OWNERSHIP and due date the definition derives) into a
    ``transfer_service.TransferSpec`` and so gets the transfer from the one
    door that writes ``budget.transfers`` (with its two shadow legs, until
    plan step ``balance:X-bi-6-4d-2`` deleted the shadows).  This
    helper CALLS the generate pass, with a window of exactly one paycheck,
    and hands back what the engine wrote; every promise
    :func:`generate_row_of` makes and every refusal the shared private body
    fires is the same here.

    **What the hand-built transfers were** (BAL-488, measured 2026-09-12 on
    the full suite when X-bv-2's twin CHECK was bound): twenty-three sites in
    eleven files constructed ``Transfer(transfer_template_id=...)`` or a
    ``TransferSpec`` naming a template by hand -- undated, and OWNING a
    figure beside the link, the pre-X-au-f shape no producer writes.
    Forty-four tests grade transfers the application cannot make.

    What follows from the engine's transfer, and each is the point:

    * **It is DERIVED.**  It stores no figure; its
      definition's series answers for it on its own due date, so a fixture
      states the figure it expects on the template (:func:`state_template_price`;
      :func:`make_transfer_template` already does).  A reader that asks the
      raw ``amount`` column gets ``None``, as on production.
    * **It answers an OCCURRENCE** (``occurs_on``), so
      ``idx_transfers_template_scenario_occurrence`` holds over it and a
      second ask for the same paycheck writes nothing (the 0-rows refusal).
    * **It is Projected**; the engine writes nothing else.  A fixture wanting
      a SETTLED transfer of a definition settles this one through
      ``transfer_service.update_transfer`` (a settling ``status_id``, the
      shape :func:`create_settled_transfer` uses), and one wanting the OWNER's
      transfer takes ownership through the same door with ``is_override=True``
      beside the figure -- the two acts ``routes/transfers/mutations``
      performs -- because every write of a transfer goes through that service
      (``CLAUDE.md``, Architecture), which also kept a transfer's three rows
      equal while it had shadows.

    Args:
        template: The flushed
            :class:`~app.models.transfer_template.TransferTemplate`.  It must
            carry a cadence (``recurrence_rule``) and, to be worth anything,
            a stated price; :func:`make_transfer_template` gives it both.
        period: The :class:`~app.models.pay_period.PayPeriod` row the
            generated transfer is funded in.  Must be one of the owner's
            saved periods.

    Returns:
        The :class:`~app.models.transfer.Transfer` the engine created,
        flushed.

    Raises:
        ValueError: As :func:`generate_row_of` -- no cadence; the engine
            wrote no transfer in *period* (its rule names no occurrence
            there, or one already claims it) or wrote more than one.
        RecurrenceWindowError: *period* is not one of the owner's saved
            periods.
        BaselineMissingError: The owner has no baseline scenario.
        ValidationError: From ``transfer_service.create_transfer``, which the
            transaction engine has no counterpart of -- the definition's
            endpoints are refused (an amortizing loan as the SOURCE, the same
            account at both ends), or the occurrence precedes the
            destination loan's origination (ruling R-C).
        NotFoundError: From the same door -- an endpoint or the template
            itself is not the owner's.
    """
    # pylint: disable=import-outside-toplevel  -- see generate_row_of
    from app.services import transfer_recurrence
    return _the_one_row_the_engine_writes(transfer_recurrence, template, period)


def _the_one_row_the_engine_writes(engine, template, period):
    """The body :func:`generate_row_of` and :func:`generate_transfer_of` share.

    The two engines expose the same ``generate_for_template(template,
    schedule, scenario_id)`` and the two definitions the same
    ``recurrence_rule``, so what differs between a row of a definition and a
    transfer of one is the engine handed in and nothing else -- one statement
    of the window, the refusals and the count, rather than two that agree
    today (``CLAUDE.md`` rule 14).

    Args:
        engine: ``app.services.recurrence_engine`` or
            ``app.services.transfer_recurrence``.
        template: The flushed definition, carrying a cadence.
        period: The owner's saved :class:`~app.models.pay_period.PayPeriod`.

    Returns:
        The one row the engine wrote in *period*.

    Raises:
        ValueError: No cadence, or the engine wrote a number of rows other
            than one.  See the public builders for the causes.
        RecurrenceWindowError: *period* is not one of the owner's saved periods.
        BaselineMissingError: The owner has no baseline scenario.
    """
    # pylint: disable=import-outside-toplevel  -- see generate_row_of
    from app.services.balance_at import BalanceContext
    from app.services.generation_schedule import GenerationSchedule
    if template.recurrence_rule is None:
        raise ValueError(
            f"template {template.id} ({template.name!r}) has no cadence, so "
            "the engine generates nothing from it; give it one "
            "(make_every_period_rule) before asking for its row"
        )
    ctx = BalanceContext.build(template.user_id)
    created = engine.generate_for_template(
        template, GenerationSchedule.for_period_ids(ctx, [period.id]),
        ctx.scenario_id,
    )
    if len(created) != 1:
        raise ValueError(
            f"the engine wrote {len(created)} rows of template {template.id} "
            f"({template.name!r}) in pay period {period.id}, not one: its "
            "rule names no occurrence in that paycheck or a row already "
            "claims it (0), or it fires more than once there (2+)"
        )
    return created[0]


def repriced_by_the_owner(row, figure):
    """Re-price *row* the way the edit door does, and hand it back.

    **The OWNER's row, stated once** (plan step balance:X-cf-3b, ruling
    R-BAL17): a fixture wanting a row a human re-priced takes the engine's
    row (:func:`generate_row_of`) and performs the two acts
    ``routes/transactions/mutations`` performs on a typed figure -- the
    figure through ``amount_ownership.state_own_amount``, which stores it and
    releases the relation that priced the row in one attribute, and
    ``is_override = True`` beside it, because the row is the owner's now
    rather than the rule's.  The pair was spelled at every site that wanted
    it before this helper, which is rule 14's tell; a site that performs one
    act without the other builds a row the write doors cannot produce.

    Args:
        row: The flushed :class:`~app.models.transaction.Transaction`, the
            engine's.
        figure: The figure the owner typed (str or Decimal-coercible).

    Returns:
        *row*, flushed, owning *figure*.
    """
    # Pylint: ``import-outside-toplevel`` -- this module imports no app
    # symbols at top level (its collection-time-safety convention).
    # pylint: disable=import-outside-toplevel
    from app.extensions import db
    from app.services.amount_ownership import state_own_amount

    state_own_amount(row, Decimal(str(figure)))
    row.is_override = True
    db.session.flush()
    return row


def transfer_repriced_by_the_owner(xfer, figure):
    """Re-price the transfer *xfer* the way the transfer edit door does.

    :func:`repriced_by_the_owner` one table over (plan step balance:X-ch,
    ruling R-BAL17 on the twin), through the twin table's own door: every
    write of a transfer goes through ``transfer_service``, so the two acts
    ``routes/transfers/mutations`` performs on a typed figure for a recurring
    transfer go through ``transfer_service.update_transfer``: the figure as
    OWNERSHIP, and ``is_override=True`` beside it because the transfer is
    the owner's now rather than the rule's.  The PLANNED figure lives on the
    transfer alone, and both legs are drawn off it: it lived on THREE rows
    -- the parent and its two shadow legs, which that door moved together
    -- until plan step ``balance:X-bi-6-4d-2`` made a transfer one row.

    Args:
        xfer: The flushed :class:`~app.models.transfer.Transfer`, the
            engine's (:func:`generate_transfer_of`).
        figure: The figure the owner typed (str or Decimal-coercible).

    Returns:
        *xfer*, flushed, owning *figure*.
    """
    # Pylint: ``import-outside-toplevel`` -- this module imports no app
    # symbols at top level (its collection-time-safety convention).
    # pylint: disable=import-outside-toplevel
    from app.services import transfer_service

    transfer_service.update_transfer(
        xfer.id, xfer.user_id,
        amount_ownership=AmountOwnership.own(Decimal(str(figure))),
        is_override=True,
    )
    return xfer


def moved_by_the_owner(row, *, into):
    """Move *row* into the paycheck *into* the way the move door does.

    The other of the edit door's two acts on a row of a definition (plan
    step balance:X-cf-3b): the period, and ``is_override = True`` beside it
    -- a moved row is the owner's, and the flag is what keeps the maintain
    pass and the generate pass off it.  The row keeps the occurrence it was
    generated for, so it never collides with the target paycheck's own
    canonical on the occurrence index; it is one of the two ways the
    application puts an override beside a canonical, the other being
    carry-forward.  A fixture wanting "an override sibling in paycheck N"
    generates the definition's row in ANOTHER paycheck and moves it here.

    Args:
        row: The flushed :class:`~app.models.transaction.Transaction`, the
            engine's.
        into: The :class:`~app.models.pay_period.PayPeriod` the owner moved
            it into.

    Returns:
        *row*, flushed, in *into*.
    """
    # Pylint: ``import-outside-toplevel`` -- this module imports no app
    # symbols at top level (its collection-time-safety convention).
    # pylint: disable=import-outside-toplevel
    from app.extensions import db

    row.pay_period_id = into.id
    row.is_override = True
    db.session.flush()
    return row


def a_deleted_occurrence(db_session, owner, period, **definition):
    """*owner*'s every-period expense, its row in *period* DELETED the way the delete door does.

    A row the owner deleted, built and deleted as the application does both:
    :func:`make_expense_template` given *definition* (only the keywords a
    caller passes, so every other default stays that builder's own), its row
    from :func:`generate_row_of`, committed, then
    ``transaction_service.delete_transaction`` and a second commit.  A
    recurring row's delete HIDES it rather than removing it
    (``transaction_service._delete._leaves_the_books``: ``soft = txn.recurs``,
    ``app/services/transaction_service/_delete.py:237``; the soft arm sets
    ``is_deleted`` at ``:446-447``), so the row stays in the table, deleted and
    carrying its name: the row a transaction door names only to a requester
    who may reach it (ruling **R-CC104**).  Added at plan step
    ``balance:X-bi-6-4d-2`` for the stranger's and the companion's probes of
    such a row, and the two route cases that built it by hand.

    Args:
        db_session: The test's SQLAlchemy session; committed twice.
        owner: The owning user's fixture dict (``seed_user``'s shape).
        period: The owner's :class:`~app.models.pay_period.PayPeriod` the row
            is generated in.
        **definition: Keywords for :func:`make_expense_template` (``name``,
            ``amount``, ``category_key``, ``companion_visible``, ...).

    Returns:
        The deleted :class:`~app.models.transaction.Transaction`, committed.
    """
    # Pylint: ``import-outside-toplevel`` -- the lazy-app-import convention, as
    # :func:`settlement_if_settling` imports the transaction service.
    # pylint: disable=import-outside-toplevel
    from app.services import transaction_service

    template = make_expense_template(db_session, owner, **definition)
    row = generate_row_of(template, period)
    db_session.commit()
    transaction_service.delete_transaction(row, owner["user"].id)
    db_session.commit()
    return row


def definition_firing_twice_in_a_paycheck(db_session, seed_user, *, name):
    """Pin a 60-day calendar and author a MONTHLY definition that names ONE
    occurrence in its first paycheck and TWO in its second; return
    ``(template, first paycheck, second paycheck)``.

    The one fixture for "a paycheck holding two rows of one definition" -- the
    state plan step R17 made storable -- so the cases that need it (the
    builder's 2+ refusal in ``test_fixture_validation``, the R17 downgrade
    guard, DC-06's acceptance of the pair, carry-forward's earliest-occurrence
    tie-break) state it once and the calendar arithmetic is argued once.
    :func:`generate_row_of` REFUSES this definition in the second paycheck by
    design; the cases that want the pair written take it from
    :func:`populate_in_a_fresh_pass`.  In the FIRST paycheck it answers the
    definition's one row there, which is what a case rolling a leftover INTO
    the pair needs as its source (plan step balance:X-cf-3b).

    **The calendar is pinned to a stated date rather than derived from today,
    and the reason was measured, not argued.**  A 60-day paycheck that opens
    in January or February of a non-leap year spans THREE firsts of the month
    (Jan+Feb and Feb+Mar are 59 days each), and ``seed_schedule_at_cadence(60)``
    opens its second paycheck 180-186 days before today -- so the same cases
    read three rows for about two months of every non-leap year, from late
    June into early September (2026-06-29..08-30, 2027-07-05..08-29,
    2029-07-02..09-02, 2030-07-01..09-01; none in 2028).  Found by
    adversarial review 2026-09-11 under ``SHEKEL_FAKE_TODAY=2026-07-15``,
    which is the way to see it.  The first paycheck here opens 2026-03-02 and
    closes 2026-04-30, the second opens 2026-05-01 and closes 2026-06-29:
    the rule's first occurrence is 2026-04-01, so the first paycheck holds
    that one and no other (the 1st of March precedes the rule); May and June
    are 61 days, so the second holds the 1st of May and the 1st of June and no
    third, on every clock.

    Args:
        db_session: The test session.
        seed_user: The seed user fixture dict; its calendar is REBUILT.
        name: The definition's name.

    Returns:
        ``(template, first, second)``: the flushed
        :class:`~app.models.transaction_template.TransactionTemplate` carrying
        the monthly rule, the :class:`~app.models.pay_period.PayPeriod` it
        fires once in, and the one it fires twice in.
    """
    # Pylint: ``import-outside-toplevel`` -- a tests-package import kept
    # local so this module's import graph stays as it was.
    # pylint: disable=import-outside-toplevel
    from tests.oracles.recurrence_baseline import MONTHLY

    periods = rebuild_calendar(
        seed_user["user"].id, _real_date(2026, 3, 2), 6, 60,
    )
    template = bare_expense_template(db_session, seed_user, name=name)
    make_cadence_rule(template, MONTHLY, starts_on=_real_date(2026, 4, 1))
    return template, periods[0], periods[1]


def require_assertion_instant(at):
    """Return *at*, refusing a plain ``date`` where an INSTANT is required.

    **The mirror of the settle column's type guard, and it exists because the
    X-f1 conversion tripped over exactly this** (finding **N-179**'s other
    direction).  A settle records a civil DAY; an ASSERTION records the instant
    it was typed (``AccountAnchorHistory.created_at``, a ``timestamptz``) and
    DERIVES its business day from it.  A bulk conversion that turned settle
    instants into days caught five assertion sites in the same pass, and each
    then failed deep inside ``to_display_tz`` with ``'date' object has no
    attribute 'tzinfo'`` -- or, had it reached the column, would have been
    stored as MIDNIGHT UTC, which is the previous Eastern evening and therefore
    the previous business day.

    Every assertion builder in this module routes through it, so handing one a
    day fails at the call site with a message that says what to pass.

    Args:
        at: The candidate recording instant.

    Returns:
        *at* unchanged when it is a ``datetime``.

    Raises:
        TypeError: When *at* is a ``date`` that is not a ``datetime``.
    """
    if isinstance(at, _real_date) and not isinstance(at, _real_datetime):
        raise TypeError(
            f"An assertion is pinned by the INSTANT it was recorded at, got "
            f"the civil day {at!r}.  Its business day (``observed_on``) is "
            "DERIVED from that instant in the display timezone, and a bare "
            "date reaches the timestamptz column as midnight UTC -- the "
            "PREVIOUS Eastern evening, so the assertion would be filed a day "
            "early.  Pass an aware-UTC datetime: the suite's own "
            "``_instant(...)`` helper, or ``settle_instant_on(day)``."
        )
    return at


def observed_day_of(instant):
    """Return the civil day an assertion stamped at ``instant`` is ABOUT.

    The ONE place the test helpers state how a pinned recording instant maps to
    an ``AccountAnchorHistory.observed_on``, and every anchor builder below
    routes through it.

    **Why the helpers derive it rather than take it.**  ``observed_on`` is a
    stored, user-supplied column since plan step 2 (ruling R-DH), so a fixture
    CAN set the two independently -- and a handful deliberately should, because
    "the user corrected an anchor's date" is now a reachable state.  But every
    fixture written before the column existed meant "an assertion recorded at
    instant X, about the day X falls on", and each of their hand-computed
    figures depends on that.  Deriving keeps all of them valid unchanged;
    a fixture that wants the two APART says so explicitly, which is exactly the
    right way round for a state that used to be unreachable.

    It is the display-timezone day, not the UTC one (ruling R-DH (b)): midnight
    UTC is the previous EVENING in Eastern, and a fixture that took the UTC day
    would file a late-evening assertion in the next pay period -- the N-132
    shape this suite has now been bitten by five times.

    Args:
        instant: The aware-UTC instant the assertion is stamped at.

    Returns:
        The :class:`datetime.date` the assertion is the closing balance for.
    """
    # pylint: disable=import-outside-toplevel  -- same circular-dep
    # avoidance as the loan helpers above.
    from app.utils.dates import to_display_date

    return to_display_date(instant)


def add_anchor_history(db_session, account, balance, days_ago=0):
    """Append an :class:`AccountAnchorHistory` row ``days_ago`` before now.

    The shared anchor-history builder for the dashboard route and
    pulse-producer suites (both built an identical ``_add_anchor_history``
    -- a duplicate-code finding).  The ``created_at`` is set to
    ``datetime.now(UTC) - days_ago``; under ``freeze_today`` the patched
    ``datetime.now`` returns the frozen today, so a positive ``days_ago``
    is that many days before the frozen reference.  Flushes; the caller
    commits.

    Args:
        db_session: The test ``db.session``.
        account: The :class:`~app.models.account.Account` the anchor
            belongs to.
        balance: The anchor balance (str or Decimal-coercible).
        days_ago: How many days before now to date the row (default 0).

    Returns:
        The created :class:`AccountAnchorHistory` row (flushed).
    """
    # pylint: disable=import-outside-toplevel  -- same circular-dep
    # avoidance as the loan helpers above.
    from datetime import datetime, timedelta, timezone
    from app.models.account import AccountAnchorHistory

    created = datetime.now(timezone.utc) - timedelta(days=days_ago)
    entry = AccountAnchorHistory(
        account_id=account.id,
        anchor_balance=Decimal(str(balance)),
        created_at=created,
        observed_on=observed_day_of(created),
        # The entered day is the pinned instant's, not today's (**N-299**):
        # the column default is a plain ``display_today`` and a historical row
        # must say what it means rather than inherit the wall clock.
        recorded_on=observed_day_of(created),
    )
    db_session.add(entry)
    db_session.flush()
    return entry


def override_anchor(db_session, account, period, balance, *, at=None):
    """Replace ``account``'s current anchor with ``balance`` on ``period``.

    Appends an :class:`AccountAnchorHistory` row -- the source of truth, and
    since ruling R-EH the ONLY place an asserted balance lives.  It used to
    sync the ``accounts.current_anchor_*`` cache columns alongside, so the
    resolver's cache-reconciliation log would not fire; there is no cache and
    no such log.  The shared form of the
    ``_override_anchor`` helper five suites had each written for themselves --
    a duplicate-code cluster that mattered once the fold made the row's INSTANT
    load-bearing, because a fix applied to one copy would have left the other
    four asserting against a state production cannot reach.

    **The instant defaults to the period's first day IN THE USER'S ZONE**, and
    that default is the point (plan step X-c2b2, the N-8 / X-c2a fixture shape a
    third time; the zone is ruling R-DH (b), 2026-07-31).
    ``AccountAnchorHistory.created_at`` server-defaults to the WALL CLOCK,
    while ``tests/test_services`` freezes today to 2026-03-20 -- so a row
    written by a helper that did not stamp it was asserted MONTHS after its own
    pay period, a state production cannot reach (a true-up files against
    ``get_current_period``).  The shipping producers never noticed: they read
    the LATEST row and ignored its date.  The fold replays every assertion on
    the day it was made, so such a row lands past the end of the window and no
    period ever sees the balance the test asserted.  Pinning it to the period's
    own start makes the fixture deterministic, clock-independent, and shaped
    like a true-up filed on day one of its period -- which keeps every
    hand-computed figure in the suites valid unchanged.

    A test that needs a MID-period assertion (the shape ruling R-L and the
    walk's instant partition exist for) passes ``at`` explicitly.

    Args:
        db_session: The test ``db.session``.
        account: The :class:`~app.models.account.Account` to re-anchor.
        period: The :class:`~app.models.pay_period.PayPeriod` the new anchor is
            recorded against; its ``start_date`` is the default instant.
        balance: The new anchor balance (Decimal -- construct from a string).
        at: Optional aware-UTC instant to stamp the row with, overriding the
            period-start default.

    **It took a required ``notes`` label until ruling R-ES** (plan step
    X-f1e2), so a failing suite could tell which fixture wrote the assertion it
    was looking at.  The column is deleted -- nothing in ``app/`` read it -- and
    the diagnostic it served is answered better by the row's own ``id``,
    ``observed_on`` and ``created_at``, which the fixture controls and the
    engine actually orders on.

    Returns:
        The created :class:`AccountAnchorHistory` row (flushed, not committed
        -- the caller owns the transaction boundary).
    """
    # Pylint: ``import-outside-toplevel`` -- this module imports no app
    # symbols at top level (its collection-time-safety convention).
    # pylint: disable=import-outside-toplevel
    from datetime import datetime, time, timezone
    from app.models.account import AccountAnchorHistory
    from app.utils.dates import DISPLAY_TIMEZONE

    # Day one of the period IN THE USER'S ZONE, converted to UTC for storage.
    # Midnight UTC is the previous EVENING in Eastern, so since ruling R-DH (b)
    # -- which reads an instant's display civil day -- it would file this
    # true-up in the PREVIOUS period and empty the anchor column's remainder of
    # the very assertion the fixture just made.
    asserted_at = at if at is not None else datetime.combine(
        period.start_date, time.min, tzinfo=DISPLAY_TIMEZONE,
    ).astimezone(timezone.utc)
    history = AccountAnchorHistory(
        account_id=account.id,
        anchor_balance=balance,
        created_at=asserted_at,
        observed_on=observed_day_of(asserted_at),
        # The entered day is the pinned instant's (**N-299**), as above.
        recorded_on=observed_day_of(asserted_at),
    )
    db_session.add(history)
    db_session.flush()
    return history


def reassert_balance_on(db_session, account, at):
    """Assert *account*'s balance AGAIN, at the instant ``at``.

    **The fixture act that replaced three re-stamping helpers** (plan step
    X-f3c-2c).  ``restamp_opening_assertion`` / ``restamp_latest_assertion``
    UPDATEd a stored :class:`~app.models.account.AccountAnchorHistory` row to
    move its business day and its recording instant onto a day the test had
    chosen.  ``budget.account_anchor_history`` is append-only, so no such act
    exists: an assertion records what a bank said on a day and the only way to
    say something else is to say it again.  This helper is that -- one more
    assertion, carrying the balance that governs unless the caller names
    another, dated on ``at``'s civil day.

    **Why the state it builds is production's own and not a fixture's.**  An
    owner who reconciles twice files two assertions; production's Checking
    account carries 2-3 on each of three days.  The two rows this leaves --
    the origination one ``account_service.create_account`` wrote and this one --
    are exactly what "I opened the account, and later I confirmed the balance
    again" looks like, and the balance every producer reads on every day at or
    after ``at`` is the same figure the re-stamp used to produce, because
    nothing is dated between them.

    **What it does NOT do is move the books.**  ``budget.account_openings`` is
    the account's own opening record and this writes no row there; a caller
    that needs the books earlier than they stand calls
    :func:`open_books_before_the_first_assertion`, which is backward-only and
    says so.  The re-stamp helpers restated the opening as a side effect, which
    made "place this assertion" and "move the books" one act that no door
    performs together.

    Args:
        db_session: The test ``db.session``.
        account: The :class:`~app.models.account.Account` asserting.
        at: The aware-UTC instant to record the assertion at.  Its display
            civil day becomes ``observed_on`` -- the day the balance was TRUE
            -- and the instant itself becomes ``created_at``, which orders two
            assertions that share a day.

    Returns:
        The appended :class:`AccountAnchorHistory` row (flushed).

    Raises:
        AssertionError: When *account* carries no assertion to repeat.
            Reachable -- :func:`account_never_asserted` builds exactly such an
            account -- and raised rather than defaulted because a fabricated
            balance is a figure no test author asked for.

    **It took a ``balance`` argument until the adversarial review of this
    step**, so a caller could assert a DIFFERENT figure.  Not one of the 21
    call sites passed one: every caller means "say the same thing again on
    another day", which is the whole act.  A test that wants a different figure
    has :func:`append_balance_assertion`, which takes one and always did.
    """
    # pylint: disable=import-outside-toplevel  -- same circular-dep
    # avoidance as the loan helpers above.
    from app.models.account import AccountAnchorHistory

    require_assertion_instant(at)
    # FLUSHES first rather than relying on autoflush: a caller that has just
    # staged an assertion in the session would otherwise read the one before
    # it, and a fixture quietly repeating the wrong balance is worse than one
    # that fails.
    db_session.flush()
    # ``.first()`` and not ``.scalar()``: an account may already carry several
    # assertions, and ``Query.scalar`` is ``one()`` underneath -- it raises
    # ``MultipleResultsFound`` on exactly the accounts this helper exists to
    # add one more to.
    governing = (
        db_session.query(AccountAnchorHistory.anchor_balance)
        .filter_by(account_id=account.id)
        .order_by(
            AccountAnchorHistory.observed_on.desc(),
            AccountAnchorHistory.created_at.desc(),
            AccountAnchorHistory.id.desc(),
        )
        .first()
    )
    assert governing is not None, (
        f"account {account.id} carries no assertion to repeat; use "
        "append_balance_assertion to state one"
    )
    balance = governing[0]
    row = AccountAnchorHistory(
        account_id=account.id,
        anchor_balance=balance,
        created_at=at,
        observed_on=observed_day_of(at),
        # The entered day is the pinned instant's, never the wall clock
        # (finding **N-299**): a historical row must say what it means rather
        # than inherit ``display_today()`` from the column default.
        recorded_on=observed_day_of(at),
    )
    db_session.add(row)
    db_session.flush()
    return row


@contextmanager
def append_only_guard_lifted(db_session, table):
    """Lift every append-only arm on *table* for the block's duration.

    **This exists to grade the control UNDERNEATH, and nothing else.**  Since
    plan step X-f3c-2c, ``budget.refuse_append_only_change`` refuses every
    UPDATE, every DELETE and (since X-f3c-2d) every TRUNCATE on the three
    account-history tables, whoever asks --
    which is what makes the rule true, and which also means it SHADOWS the
    controls it sits on top of.  Three of those still matter and would
    otherwise go ungraded:

    * ``fk_transactions_reconciled_by``'s ``ON DELETE RESTRICT``, which refuses
      to un-clear a line by removing the statement it names (ruling **R-FL**);
    * ``ck_books_open_before_movements``' UPDATE and DELETE arms, which exist
      precisely for "a raw ``UPDATE`` moving the governing row's ``opened_on``
      forward, and a raw ``DELETE`` of the governing row" (plan step X-f3c-2b);
    * the posted-only reversal branch of ``account_posting_service``, defensive
      against a history row that vanished from under its journal entry.

    A control nobody can reach is a control nobody can trust, and deleting
    those three because a newer guard happens to stand in front of them would
    trade a measured refusal for an argument.  So a case that grades one lifts
    the outer guard for exactly its own statement and says so.

    **It is not an escape hatch for building fixtures.**  A fixture that wants
    an account with no assertion has :func:`account_never_asserted`; one that
    wants a different asserted day has :func:`reassert_balance_on`.  Neither
    needs this, and both were written so that neither would.

    Restores the trigger in a ``finally``, so a failing assertion inside the
    block cannot leave the rest of the test's transaction unguarded.

    Args:
        db_session: The test ``db.session``.
        table: The schema-qualified table, e.g.
            ``"budget.account_anchor_history"``.  Must be one of
            :data:`app.append_only_infrastructure.APPEND_ONLY_TABLES`; a typo
            would otherwise lift nothing and the case would grade the outer
            guard while claiming to grade the inner one.

    Yields:
        ``None``.
    """
    # pylint: disable=import-outside-toplevel  -- same circular-dep
    # avoidance as the loan helpers above.
    from app.append_only_infrastructure import (
        APPEND_ONLY_TABLES,
        APPEND_ONLY_TRIGGERS,
    )
    from app.extensions import db

    assert table in APPEND_ONLY_TABLES, (
        f"{table!r} carries no append-only trigger to lift; "
        f"expected one of {APPEND_ONLY_TABLES}"
    )
    # EVERY arm, not just the update one: since X-f3c-2d the guard is three
    # triggers with three timings, and lifting one would leave a case that
    # means to reach the control underneath still refused by another arm.
    enable = [
        db.text(f"ALTER TABLE {table} ENABLE TRIGGER {name}")
        for name in APPEND_ONLY_TRIGGERS
    ]
    # Disabling FIRST is load-bearing rather than tidy: the delete arm is a
    # deferred constraint trigger, so a transaction that has already deleted
    # from this table holds pending trigger events and PostgreSQL then refuses
    # ``ALTER TABLE`` on it outright.
    for name in APPEND_ONLY_TRIGGERS:
        db_session.execute(db.text(
            f"ALTER TABLE {table} DISABLE TRIGGER {name}"
        ))
    try:
        yield
    finally:
        try:
            for statement in enable:
                db_session.execute(statement)
        except (sa_exc.InvalidRequestError, sa_exc.DBAPIError):
            # A case that expects the INNER control to refuse leaves no way to
            # emit further SQL, in one of two shapes: SQLAlchemy refuses
            # ('prepared' after a failed COMMIT, pending-rollback after a
            # failed flush), or PostgreSQL does (``InFailedSqlTransaction``
            # after a constraint aborted the transaction).  Both are caught by
            # name rather than broadly.  Rolling back is what such a case does
            # next anyway; doing it here is what makes the guard come back
            # whether the block passed or raised, which a bare re-enable would
            # not.
            db_session.rollback()
            for statement in enable:
                db_session.execute(statement)


def account_never_asserted(
    seed_user, db_session, name="Unasserted", type_name="Checking",
    opening_equity=None,
):
    """Build an account the assertion factory never touched.

    **The only honest way to reach "this account has asserted nothing" since
    plan step X-f3c-2c**, and the reason is that the state is genuinely
    unreachable through a door.  ``account_service.create_account`` writes an
    origination assertion and CHECKS that it landed -- it is the E-19 / CRIT-01
    invariant -- and ``budget.account_anchor_history`` is append-only at the
    database tier, so nothing may delete that row while the account stands.
    Every suite that needs the state used to reach it by creating an account
    and then deleting its assertions; that act does not exist.

    So this builds the row directly and stops there.  What it produces is an
    account that was never created, which is exactly the premise: the branches
    it reaches -- ``cash_ledger.resolve_anchor``'s ``RuntimeError``,
    ``_asset_fold``'s fail-loud window, ``balance_at.cash_anchor_history``'s
    empty log, ``integrity_check``'s BA-01 -- are the ones that exist because a
    reader must not fabricate a level for an account whose books it cannot
    read.  They are defensive arms for an impossible state, and a fixture that
    could not build one would leave every one of them ungraded.

    **It PAIRS the ledger account**, because that pairing is not part of what
    the fixture is withholding: a posted-ledger reader asked about an account
    with no chart row raises for a different reason, and a case meaning "no
    assertion" would then be graded by the wrong refusal.

    Args:
        seed_user: The ``seed_user`` fixture dict, for the owner.
        db_session: The test ``db.session``.
        name: The account name, unique per owner.
        type_name: The ``ref.account_types`` name.
        opening_equity: When given, an :class:`AccountOpening` row is written
            for the day before today carrying this equity -- for a case whose
            subject is the ASSERTION's absence and which needs the books to be
            readable.  ``None``, the default, withholds that too, which is what
            a case about a missing OPENING record wants.

    Returns:
        The created :class:`~app.models.account.Account`, flushed.
    """
    # pylint: disable=import-outside-toplevel  -- same circular-dep
    # avoidance as the loan helpers above.
    from datetime import timedelta as _td

    from app import ref_cache
    from app.enums import AccountOpeningSourceEnum
    from app.models.account import Account
    from app.models.account_opening import AccountOpening
    from app.models.ref import AccountType
    from app.services import ledger_account_service
    from app.utils.dates import display_today

    account_type = (
        db_session.query(AccountType).filter_by(name=type_name).one()
    )
    account = Account(
        user_id=seed_user["user"].id,
        account_type_id=account_type.id,
        name=name,
    )
    db_session.add(account)
    db_session.flush()
    if opening_equity is not None:
        db_session.add(AccountOpening(
            account_id=account.id,
            opened_on=display_today() - _td(days=1),
            opening_equity=opening_equity,
            source_id=ref_cache.account_opening_source_id(
                AccountOpeningSourceEnum.USER_DECLARED,
            ),
        ))
    ledger_account_service.create_ledger_account_for_account(account)
    db_session.flush()
    return account


def open_books_before_the_first_assertion(
    db_session, account, also_before=None,
):
    """Open *account*'s books before ANY day a fixture could date a row on.

    **The factory shape plan step X-f3c-2b requires** (ruling **R-HG**).
    ``account_service.create_account`` writes the opening record and the
    origination assertion on ONE day, which is right in production -- the owner
    types one balance and it is both -- and it means no movement may be dated on
    or before that day, because an opening equity is the CLOSING balance for its
    own day.  A fixture that then settles a row on or before the account's own
    creation day is building a state the app refuses, and on the frozen suite
    clock that is the ordinary case: ``create_account``'s default ``observed_on``
    and the settle door's default day are the SAME ``display_today()``.

    **It bounds on every day a row could land on and takes the earliest,
    because "the day before the assertion" was measured too tight.**  Route
    suites routinely settle a row days BEFORE the account was created -- a
    correction to money that moved last week -- so an opening one day back
    still refuses them.  The bounds:

    * the account's earliest ASSERTION, which is where ``create_account`` put
      the books;
    * the owner's earliest PAY PERIOD, which is the floor the settle door itself
      applies (``pay_period_service.earliest_recordable_day``, ruling R-EL), so
      nothing a door accepts can precede it;
    * the earliest day the account ALREADY records money moving, so a helper
      called after the rows exist cannot strand them;
    **A matched BANK LINE bounds the books too and is deliberately NOT a term
    here, because it cannot change the answer** (plan step
    balance:X-f3c-2b-2b).  It was added as one and then removed on a proof
    rather than on taste: this helper only ever moves the books BACKWARD (it
    clamps to the standing opening), and the database constraint already
    guarantees the standing opening sits strictly below every line the account
    has matched.  So ``min(matched) - 1`` is never smaller than the clamp, and
    the term is dead in every state the boundary permits.  A defensive term
    that cannot fire is the *born dead* shape ``lessons.md`` names, and it
    would have read as protection nobody had.
    * *also_before*, when the caller knows a day of its own -- a loan's
      ORIGINATION is the one that exists, because its payment schedule runs
      from there and the owner's calendar may start later.

    One day before the earliest of those is the latest day that leaves every
    recordable day recordable.  It is still a real production shape -- books
    that opened before the budget did is exactly what finding **N-368**'s import
    will create for the developer's own Checking account -- and it moves NO
    figure in a fixture: the origination assertion still clears whatever settled
    on its own day, so every correction is what it was.

    **BACKWARD only, and that is a rule rather than an accident.**  The books
    never move forward here, so calling this twice, or calling it on an account
    some other helper already opened earlier, cannot strand a row that was
    legal a moment ago.  ``tests/conftest``'s own calendar reset learned the
    same rule the same way one table over, and plan step
    ``pay_calendar:C4-b-1`` then measured that its backward-only restatement
    could not move a day in any world that file builds and deleted it; what
    enforces the rule there now is a refusal, not a restatement.

    A no-op for an account carrying no assertion, and equally for one carrying
    no opening ROW -- both production-unreachable, both states a raw-model
    fixture can build.  Each answers ``None`` rather than a day nothing wrote.

    Args:
        db_session: The test ``db.session``.
        account: The :class:`~app.models.account.Account` whose books to move.
        also_before: An extra civil day the books must precede, or ``None``.

    Returns:
        The civil day the books now open on, or ``None`` when the account
        carries no assertion to place them before.
    """
    # pylint: disable=import-outside-toplevel  -- same circular-dep avoidance
    # as the loan helpers above.
    from app.extensions import db
    from app.models.account import Account, AccountAnchorHistory
    from app.models.pay_period import PayPeriod
    from app.models.transaction import Transaction
    from app.models.transaction_entry import TransactionEntry
    from app.services import account_posting_service

    db_session.flush()
    earliest_assertion = (
        db_session.query(db.func.min(AccountAnchorHistory.observed_on))
        .filter(AccountAnchorHistory.account_id == account.id)
        .scalar()
    )
    if earliest_assertion is None:
        return None
    owner_id = db_session.query(Account.user_id).filter(
        Account.id == account.id,
    ).scalar()
    candidates = [earliest_assertion]
    for value in (
        also_before,
        db_session.query(db.func.min(PayPeriod.start_date))
        .filter(PayPeriod.user_id == owner_id).scalar(),
        db_session.query(db.func.min(Transaction.settled_on))
        .filter(Transaction.account_id == account.id).scalar(),
        db_session.query(db.func.min(TransactionEntry.settled_on))
        .filter(TransactionEntry.account_id == account.id).scalar(),
    ):
        if value is not None:
            candidates.append(value)
    # Clamped against the books as they STAND, so this only ever moves them
    # backward -- see the docstring's rule.  ``_governing_opening_day`` answers
    # ``date.max`` for an account carrying no opening row, which makes the
    # clamp a no-op there rather than a special case.
    standing = _governing_opening_day(db_session, account)
    if standing == _real_date.max:
        # No opening ROW at all, so there is nothing to restate and nothing to
        # return: ``restate_account_opening`` carries the equity forward from
        # the governing row and has none to read.  Answering the COMPUTED day
        # here would name a day the books do not open on, which is worse than
        # answering nothing -- a raw-model fixture can build this state, and
        # the docstring's contract is the day the books NOW open.
        return None
    opened_on = min(min(candidates) - _real_timedelta(days=1), standing)
    if opened_on == standing:
        return opened_on
    restate_account_opening(db_session, account, opened_on)
    # **The posted ledger follows the books** (plan step X-f3c-2b).
    # ``create_account`` has already booked the ``account_opening`` journal
    # entry keyed on the day it asserted, so a restatement afterwards leaves
    # that entry on a day the books no longer open -- the stale-key-plus-
    # reversal shape ``tests/conftest.py``'s own factory comment warns about.
    # The reconcile is idempotent and self-healing, and re-running it is what
    # PRODUCTION does after the same restatement (the deploy's
    # ``backfill_all_account_anchor_postings``), so the fixture takes the same
    # path rather than a shortcut.
    account_posting_service.sync_account_anchor_postings_all_scenarios(
        account.id,
    )
    return opened_on


def governing_opening_row(db_session, account):
    """Return the ``budget.account_openings`` row that GOVERNS *account*.

    **The one place the test tree spells "which restatement is in force"**
    (plan step X-f3c-2b).  The table is append-only and the latest RECORDING
    instant governs (ruling **R-HE**), ``id`` breaking a same-instant tie --
    the same order :func:`app.services.cash_ledger.account_opening_fact` reads
    in Python and ``budget.account_books_opened_on`` reads in SQL, and
    ``TestTheTwoGoverningLookupsElectTheSameRow`` in
    ``tests/test_services/test_books_boundary.py`` is what holds all three to
    it.

    It answers with the ROW rather than through
    :func:`~app.services.cash_ledger.account_opening_fact` because that loader
    RAISES on an account carrying no opening, and the fixture helpers here have
    to tolerate one: a raw-model fixture can build an account the canonical
    factory never touched.

    Args:
        db_session: The test ``db.session``.
        account: The :class:`~app.models.account.Account` to read.

    Returns:
        The governing :class:`~app.models.account_opening.AccountOpening`, or
        ``None`` when the account carries none.
    """
    # pylint: disable=import-outside-toplevel  -- same circular-dep avoidance
    # as the loan helpers above.
    from app.models.account_opening import AccountOpening

    return (
        db_session.query(AccountOpening)
        .filter_by(account_id=account.id)
        .order_by(AccountOpening.created_at.desc(), AccountOpening.id.desc())
        .first()
    )


def _governing_opening_day(db_session, account):
    """Return the civil day *account*'s books currently open on.

    Args:
        db_session: The test ``db.session``.
        account: The :class:`~app.models.account.Account` to read.

    Returns:
        The governing row's ``opened_on``, or ``date.max`` when the account
        carries none -- a value that makes a ``min()`` against it a no-op,
        which is what a caller comparing against "the books as they stand"
        means for an account with no books yet.
    """
    governing = governing_opening_row(db_session, account)
    return _real_date.max if governing is None else governing.opened_on


def restate_account_opening(db_session, account, opened_on):
    """Append an opening record restating WHEN the account's books opened.

    ``budget.account_openings`` is append-only and the latest recorded row
    governs, so moving the day means inserting a row rather than updating one.
    The EQUITY is carried forward unchanged: a restatement places the opening
    in time and says nothing about how much the books opened with.

    **It writes the row DIRECTLY rather than going through a service, and that
    is the point** (plan step X-f3c-2b).  ``account_service.create_account``
    bounds its ``observed_on`` at ``pay_period_service.earliest_recordable_day``
    -- the calendar's own first day -- so a factory cannot ask it to open books
    before the calendar starts, which is exactly where a fixture needs them.
    The floor is a rule about ASSERTIONS (ruling R-ER), not about openings, and
    production's own restatement is written by migration ``d3b6f1c8a274`` the
    same unbounded way.

    Args:
        db_session: The test ``db.session``.
        account: The :class:`~app.models.account.Account` whose books to
            re-date.
        opened_on: The civil day the books now open on.
    """
    # pylint: disable=import-outside-toplevel  -- same circular-dep avoidance
    # as the loan helpers above.
    from app.models.account_opening import AccountOpening

    governing = governing_opening_row(db_session, account)
    if governing is None or governing.opened_on == opened_on:
        return
    db_session.add(AccountOpening(
        account_id=account.id,
        opened_on=opened_on,
        opening_equity=governing.opening_equity,
        source_id=governing.source_id,
    ))
    db_session.flush()


def append_balance_assertion(
    db_session, account, balance, at, recorded_at=None,
):
    """Append one balance ASSERTION (a true-up) at a pinned instant.

    The instant-precise true-up builder the cash-ledger suites share.  See
    :func:`reassert_balance_on` for why the instant (not the day) is the
    thing being pinned.

    **Every column is stated at INSERT** (plan step X-f3c-2c).  It used to
    insert and then re-stamp, on the ground that ``created_at`` carries a
    server default the INSERT would otherwise fill with the wall clock -- which
    is not so: a value supplied to the constructor appears in the INSERT and no
    server default is reached, which is what ``override_anchor`` and
    ``add_anchor_history`` beside it have always relied on.  The table is
    append-only, so the re-stamp had to go; measuring the reason first is what
    made it cost no caller anything.

    **It takes no pay period, because an assertion is filed under none** (ruling
    R-EO deleted ``pay_period_id`` from the row).  It carried a ``period``
    parameter its body never read until 2026-09-03, and 43 call sites
    computed one to pass it -- the shape ruling R-EH deleted from
    ``resolve_anchor``'s ``scenario_id``: a parameter that scopes nothing tells
    its caller the row is filed under something (finding N-393).

    **The row carries TWO clocks and this helper can now separate them**
    (*recorded_at*).  ``observed_on`` is the BUSINESS day the balance was true
    for and ``created_at`` is when it was typed; since plan step 2 made
    ``observed_on`` user-supplied they can disagree, which is precisely the
    shape the retired third statement of "the latest assertion" got wrong
    (finding N-133 / F4).  Defaulting *recorded_at* to *at* keeps every
    existing caller's two clocks equal -- and an adversarial review found that
    default silently disarming the one test written to exercise the
    disagreement, which is why the parameter exists.

    Args:
        db_session: The test ``db.session``.
        account: The :class:`~app.models.account.Account` asserting.
        balance: The asserted balance (str or Decimal-coercible).
        at: The aware-UTC instant whose display-timezone day becomes the
            BUSINESS day (``observed_on``).
        recorded_at: The aware-UTC instant to stamp as ``created_at`` -- when
            the row was TYPED.  Defaults to *at*, which makes the two clocks
            agree; pass it to build a back-dated assertion, where the business
            day precedes the recording instant.

    Returns:
        The inserted :class:`AccountAnchorHistory` row (flushed).
    """
    # pylint: disable=import-outside-toplevel  -- same circular-dep
    # avoidance as the loan helpers above.
    from app.models.account import AccountAnchorHistory

    require_assertion_instant(at)
    require_assertion_instant(at if recorded_at is None else recorded_at)
    typed_at = at if recorded_at is None else recorded_at
    row = AccountAnchorHistory(
        account_id=account.id,
        anchor_balance=Decimal(str(balance)),
        observed_on=observed_day_of(at),
        created_at=typed_at,
        recorded_on=observed_day_of(typed_at),
    )
    db_session.add(row)
    db_session.flush()
    return row


def assert_pay_period_invariants(db_session, user_id):
    """Assert a user's pay-period structure is not corrupt (Discipline 1).

    The single source of truth for "this user's period structure is
    sound," called after EVERY pay-period mutation test (extend /
    truncate / regenerate / top-up / reset).  A pay period is the spine
    of every financial number in Shekel, and period corruption produces
    a silently wrong balance rather than an error, so this helper exists
    to make that corruption impossible to ship undetected.  See
    ``docs/plans/implementation_plan_pay_period_crud.md`` (Test plan,
    Discipline 1).

    Raises ``AssertionError`` (with a diagnostic) on the first violated
    invariant:

      1. Every account carries at least one balance ASSERTION, so a producer
         can resolve a balance for it.
      2. Every transfer is in one of the user's (still-existing) periods.
      3. No transaction references a pay period that no longer exists.

    **FOUR invariants were DELETED at plan step ``pay_calendar:C4-c``, and
    none of them was replaced.**  They asserted that ``period_index`` was
    unique per user, that ordinal order matched payday order on BOTH
    ``start_date`` and ``end_date``, that the ordinals were contiguous, and
    that no two periods' spans overlapped -- the same functional dependency
    ``integrity_check`` BA-03 / BA-04 policed in weekly SQL and
    ``recurrence._calendar.PeriodCalendar.__post_init__`` policed at the value
    boundary.  All four read columns that no longer exist: a period's ordinal
    is now its position in payday order and its end is the day before the next
    payday, so a duplicate ordinal, an ordinal gap, an order disagreement and
    an overlap are not states this database can hold.  ``uq_pay_periods_user_
    start`` is what remains, and it is a KEY rather than an assertion.

    *This helper carried the WRITE DOOR's exact blind spot while it existed*
    (finding **P5**): it asserted ``cur.start_date > prev.end_date`` and
    nothing about contiguity, so no case in this suite could have failed on a
    gapped write -- which is why finding **P2** was found by reading rather
    than by the suite.  Deleting the four is not a loss of that coverage; it is
    the removal of the state they were the wrong instrument for.

    Args:
        db_session: The test ``db.session``.
        user_id: The user whose pay-period structure to validate.
    """
    # Pylint: ``import-outside-toplevel`` -- this module imports no app
    # symbols at top level (its collection-time-safety convention); load
    # the models lazily, the same way every helper above does.
    # pylint: disable=import-outside-toplevel
    from app.models.account import Account, AccountAnchorHistory
    from app.models.pay_period import PayPeriod
    from app.models.transaction import Transaction
    from app.models.transfer import Transfer

    period_ids = {
        row.id for row in
        db_session.query(PayPeriod.id).filter_by(user_id=user_id)
    }

    # 1. Anchor integrity: every account carries at least one balance
    #    ASSERTION (E-19 / Commit 3).  It used to assert that the account's
    #    ``current_anchor_period_id`` named one of the user's live periods;
    #    rulings R-EH and R-EO deleted both that column and the assertion's own
    #    pay period, so what survives is the invariant those columns existed to
    #    serve -- an account the resolver can answer for.  A period wipe can no
    #    longer strand an anchor, which is the whole point of the deletion.
    for account in db_session.query(Account).filter_by(user_id=user_id):
        assert db_session.query(AccountAnchorHistory).filter_by(
            account_id=account.id,
        ).count() > 0, (
            f"user {user_id}: account {account.id} carries no balance "
            "assertion, so no producer can resolve a balance for it"
        )

    # 2. Every transfer is in one of the user's own (surviving) periods.  It
    #    also counted two shadows per transfer, each in the transfer's period,
    #    until plan step balance:X-bi-6-4d-2 (ruling R-BAL167 class 4): a
    #    transfer is one row there (ck_transactions_names_no_transfer refused
    #    a row naming one until plan step balance:X-bi-6-5 dropped the
    #    column), so its legs are drawn off the transfer itself and have no
    #    period of their own to disagree with.
    for transfer in db_session.query(Transfer).filter_by(user_id=user_id):
        assert transfer.pay_period_id in period_ids, (
            f"user {user_id}: transfer {transfer.id} is in period "
            f"{transfer.pay_period_id}, not among the user's periods"
        )

    # 3. No transaction (scoped via its account) references a period that
    #    no longer exists -- the CASCADE FK enforces this; re-checking
    #    catches an ORM bypass after a bulk delete.
    orphans = (
        db_session.query(Transaction.id)
        .join(Account, Transaction.account_id == Account.id)
        .outerjoin(PayPeriod, Transaction.pay_period_id == PayPeriod.id)
        .filter(Account.user_id == user_id, PayPeriod.id.is_(None))
        .count()
    )
    assert orphans == 0, (
        f"user {user_id}: {orphans} transaction(s) reference a deleted "
        f"pay period"
    )


def resolved_amount(txn):
    """Return what the APP says *txn*'s amount is, through the one resolver.

    **The assertion a generated row needs since plan step balance:X-au-e**,
    where it used to be ``txn.estimated_amount``.  That column is NULL on a
    derived row -- the whole point of the cutover -- so a test reading it
    asserts the absence of a cache rather than the presence of money, and
    ``assert None == Decimal("1200.00")`` is what a correct app now produces.

    Reading the resolver instead keeps the assertion about the FIGURE, which is
    what the test was always for: it goes through
    ``cash_ledger.resolve_transaction_amount`` over a basis pinned the way every
    production reader pins one, so a test asserts the number a screen would
    show.  Use it for any row whose amount is DERIVED; a row that owns its
    figure (an ad-hoc one, or one a human re-priced) answers the same through
    this function, so it is safe everywhere and needed only where the column
    went away.

    Args:
        txn: The transaction to price.

    Returns:
        The row's amount as a ``Decimal``.

    Raises:
        AmountUnresolvable: When no rule can price the row -- which is a real
            finding, not a fixture inconvenience.  See
            :func:`state_template_price` for the fixture shape that causes it.
    """
    # Pylint: ``import-outside-toplevel`` -- this module imports no app
    # symbols at top level (its collection-time-safety convention).
    # pylint: disable=import-outside-toplevel
    from app.services.cash_ledger import derived_amount_basis, resolve_transaction_amount

    return resolve_transaction_amount(
        txn, derived_amount_basis(txn.account.user_id, txn.scenario_id),
    )


def state_template_price(template, amount=None, *, effective_on=None):
    """State *template*'s price through the app's ONE write door.

    **A fixture that constructs a template and stops has built a definition the
    application cannot build**, and since plan step balance:X-au-e that
    difference is fatal rather than cosmetic -- for a TRANSFER template since
    balance:X-au-f, which is the twin obligation this helper was written
    anticipating.  Both doors that create a
    transaction template -- ``routes/templates/crud.create_template`` and
    ``routes/salary/profiles._salary_template`` -- call
    ``template_amount_service.set_amount`` immediately after the flush, so every
    real definition has a price SERIES.  A generated row stores no figure now
    and is priced by that series on its own due date, and
    ``cash_ledger._amount_source._stated_amount`` REFUSES a row whose series is
    empty rather than falling back to ``default_amount`` -- which is X-au-a's
    ruling, not an oversight: the scalar has no time dimension, so reading it
    would price a March row at June's figure.

    So a bare-constructed template generates rows nothing can price, and the
    failure surfaces as ``AmountUnresolvable`` in whatever the test was actually
    about.  Call this straight after the flush wherever a fixture builds a
    template by hand.

    Zero production templates have an empty series (measured on the 2026-09-03
    production clone, all 525 declared rows resolved), so this restores the
    fixture to the shape the data actually has.

    Args:
        template: The flushed transaction or transfer template.
        amount: The price to state; ``template.default_amount`` when omitted,
            which is what both create doors pass.
        effective_on: The date it takes effect; the owner's today when omitted,
            as both doors pass.  One version is enough for any due date --
            ``amount_as_of`` holds FLAT before the earliest -- so a fixture
            needs a date only when it is testing the series itself.

    Returns:
        None.  The caller flushes or commits as it already does.
    """
    # Pylint: ``import-outside-toplevel`` -- this module imports no app
    # symbols at top level (its collection-time-safety convention).
    # pylint: disable=import-outside-toplevel
    from app.services import template_amount_service
    from app.utils.dates import display_today

    template_amount_service.set_amount(
        template,
        template.default_amount if amount is None else amount,
        effective_on=display_today() if effective_on is None else effective_on,
    )


def bare_expense_template(
    db_session, seed_user, name="Cadence Under Test", *, account=None,
    is_envelope=False,
):
    """Create and flush an expense template carrying NO cadence.

    The definition a test authors a rule onto when the rule is the subject and
    the template is only there to own it -- which plan step R-F6 made
    necessary: ``ck_recurrence_rules_one_owner`` refuses a rule belonging to
    nothing, so ``author_rule`` takes an owner and there is no such thing as a
    free-standing rule any more.

    **And the PRICING LINK a bare CHECK builder's row carries** since plan step
    ``balance:X-bi-7d-1``.  The builders in ``tests/test_models`` whose
    subject is a CHECK or key of ``budget.transactions`` construct their rows
    by hand on purpose -- a control that reached the row through a door would
    grade the door -- and the family's cutover (``X-bi-7d-2``) re-cuts
    ``ck_transactions_one_pricing_link`` to ``= 1``, so a bare row must name
    a definition to be storable at all.  A rule-less definition of the
    owner's is the one such a row would have had the grid mint it
    (**R-BAL20**), and this is the shared builder of that shape (the audit
    trigger's workloads keep a local one, ``tests._audit_trigger_workloads
    ._rule_less_definition``); each such row takes its OWN definition,
    because two undated rows of one definition in one paycheck collide on
    ``idx_transactions_template_scenario_undated`` and two dated ones on the
    occurrence index.

    Distinct from :func:`make_expense_template`, which already gives its
    template an every-paycheck rule -- authoring a second onto that one is
    refused by ``uq_recurrence_rules_transaction_template_id``, and refused
    correctly: a definition has one cadence.

    Args:
        db_session: The test session.
        seed_user: The seed user fixture dict.
        name: Display name; distinct per call when a test needs two.
        account: The account the definition names, or ``None`` for the seed
            user's.  Stated by a case that has DELETED the seeded account
            before it mints (``transaction_templates.account_id`` is
            ``ON DELETE RESTRICT``, so a definition minted on it beforehand
            would refuse the very act under test, and one minted afterwards
            has no account to name).
        is_envelope: Whether the definition's rows take purchase entries --
            the DEFINITION's setting, which ``Transaction.tracks_purchases``
            reads for every row that names one.

    Returns:
        The flushed :class:`~app.models.transaction_template.TransactionTemplate`,
        with ``recurrence_rule`` still ``None``.
    """
    # Pylint: ``import-outside-toplevel`` -- this module imports no app
    # symbols at top level (its collection-time-safety convention).
    # pylint: disable=import-outside-toplevel
    from app import ref_cache
    from app.enums import TxnTypeEnum
    from app.models.transaction_template import TransactionTemplate

    template = TransactionTemplate(
        user_id=seed_user["user"].id,
        account_id=(seed_user["account"] if account is None else account).id,
        category_id=seed_user["categories"]["Rent"].id,
        transaction_type_id=ref_cache.txn_type_id(TxnTypeEnum.EXPENSE),
        name=name,
        default_amount=Decimal("100.00"),
        is_envelope=is_envelope,
    )
    db_session.add(template)
    db_session.flush()
    state_template_price(template)
    return template


def sole_rule_owned_by(user_id):
    """Return the ONE recurrence rule *user_id* owns, through its definition.

    ``budget.recurrence_rules`` carries no ``user_id`` column since plan step
    R-F6 -- the owner is the definition holding the rule, and
    :attr:`~app.models.recurrence_rule.RecurrenceRule.user_id` reads through to
    it -- so a Python property cannot be a SQL filter and
    ``filter_by(user_id=...)`` no longer names a column.  This is the join that
    replaces it, over both arms of the owning arc.

    It is a TEST helper rather than an application one deliberately: no
    production reader asks "which rules does this user own", and adding a query
    nothing calls is the speculative surface coding-standards rule 13 refuses.

    Args:
        user_id: The owner.

    Returns:
        The single :class:`~app.models.recurrence_rule.RecurrenceRule`.

    Raises:
        AssertionError: The owner has no rule, or more than one -- the same
            two failures ``Query.one()`` reports, named.
    """
    # Pylint: ``import-outside-toplevel`` -- this module imports no app
    # symbols at top level (its collection-time-safety convention).
    # pylint: disable=import-outside-toplevel
    from app.extensions import db
    from app.models.recurrence_rule import RecurrenceRule
    from app.models.transaction_template import TransactionTemplate
    from app.models.transfer_template import TransferTemplate

    rules = []
    for template, arm in (
        (TransactionTemplate, RecurrenceRule.transaction_template_id),
        (TransferTemplate, RecurrenceRule.transfer_template_id),
    ):
        rules.extend(
            db.session.query(RecurrenceRule)
            .join(template, arm == template.id)
            .filter(template.user_id == user_id)
            .all()
        )
    assert len(rules) == 1, (
        f"expected user {user_id} to own exactly one recurrence rule, "
        f"found {len(rules)}: {[rule.id for rule in rules]}"
    )
    return rules[0]


def make_every_period_rule(db_session, owner):  # pylint: disable=unused-argument
    """Author an every-paycheck recurrence ONTO *owner*, and flush it.

    The shared rule builder for every fixture that needs a template to repeat,
    so no test re-derives it.

    **It takes the OWNING DEFINITION rather than a user id, since plan step
    R-F6**, because that is what a rule now needs to exist at all: the owning
    FK is on ``budget.recurrence_rules`` under
    ``ck_recurrence_rules_one_owner``, so a rule with no template is not a row
    the database accepts.  A fixture therefore builds its template first and
    makes it repeat second, which is the order production runs in.

    **Authored through the WRITE DOOR since plan step R7c-b**, and the change is
    a real improvement rather than plumbing.  It used to construct a
    ``RecurrenceRule`` directly with a ``pattern_id`` and nothing else, which
    built a row ``resolve`` would refuse: no first occurrence, no unit, no
    placement.  That was invisible while the two-axis columns were nullable and
    unread; it is a ``NOT NULL`` violation now, and the door is what a
    production rule goes through anyway.

    ``starts_on`` is the schedule's OPENING payday, which is what an
    unbounded rule resolved to before the column was authored -- so every
    fixture built on this generates into the same periods it always did.

    Args:
        db_session: The test session.  Unused since the door flushes into it
            through ``app.extensions.db``; kept because every caller passes it
            and a signature change would touch two dozen call sites for no
            behaviour.
        owner: The ``TransactionTemplate`` or ``TransferTemplate`` the
            recurrence belongs to.  Mutated: its ``recurrence_rule`` is set.

    Returns:
        The flushed :class:`~app.models.recurrence_rule.RecurrenceRule`.
    """
    # Pylint: ``import-outside-toplevel`` -- this module imports no app
    # symbols at top level (its collection-time-safety convention).
    # pylint: disable=import-outside-toplevel
    from app.enums import RecurrenceUnitEnum
    from app.services.pay_calendar import calendar_for
    from app.services.recurrence import RecurrenceSpec, author_rule

    calendar = calendar_for(owner.user_id)
    return author_rule(
        RecurrenceSpec(
            user_id=owner.user_id,
            unit=RecurrenceUnitEnum.PERIOD,
            starts_on=calendar.opening_bound(),
        ),
        calendar,
        owner,
    )


def make_line_cadence_rule(db_session, deduction, per_year):  # pylint: disable=unused-argument
    """Author the rule migration ``542c61e48ee8`` writes for a 24 / 12 line.

    **The shared cadence builder for every fixture that gave a deduction a
    ``deductions_per_year`` of 24 or 12** before plan step salary:R15-b
    deleted the column (rulings R-SAL3, R-SAL29, R-SAL30): the same two
    shapes the migration writes, authored through the write door onto the
    deduction as its owner, so a fixture's line is taken on exactly the
    paydays the migrated production line is.

    Args:
        db_session: The test session; unused for the reason
            :func:`make_every_period_rule` gives.
        deduction: A flushed ``PaycheckLine`` on a profile whose owner
            has pay periods.  Mutated: its ``recurrence_rule`` is set.
        per_year: ``24`` -- every paycheck, at most 2 a month, from the
            owner's opening payday -- or ``12`` -- monthly, on the first
            paycheck on or after the 1st, from the first of the opening
            payday's month.  A 26 line has NO rule; do not call this for one.

    Returns:
        The flushed :class:`~app.models.recurrence_rule.RecurrenceRule`.

    Raises:
        ValueError: *per_year* is neither 24 nor 12.
    """
    # pylint: disable=import-outside-toplevel
    from app.enums import PeriodPlacementEnum, RecurrenceUnitEnum
    from app.services.pay_calendar import calendar_for
    from app.services.recurrence import RecurrenceSpec, author_rule

    calendar = calendar_for(deduction.user_id)
    opening = calendar.opening_bound()
    if per_year == 24:
        spec = RecurrenceSpec(
            user_id=deduction.user_id,
            unit=RecurrenceUnitEnum.PERIOD,
            starts_on=opening,
            max_per_month=2,
        )
    elif per_year == 12:
        spec = RecurrenceSpec(
            user_id=deduction.user_id,
            unit=RecurrenceUnitEnum.MONTH,
            placement=PeriodPlacementEnum.PERIOD_STARTING_ON_OR_AFTER,
            starts_on=opening.replace(day=1),
        )
    else:
        raise ValueError(
            f"per_year must be 24 or 12 (a 26 line carries no rule); got {per_year!r}"
        )
    return author_rule(spec, calendar, deduction)


def first_occurrence_on_day(user_id, fires_on_day, fires_in_month=None):
    """Return the first date matching a calendar cadence the schedule reaches.

    **A TEST-AUTHORING translation, and deliberately not a production one.**
    Plan step R7c-b made a rule's first occurrence the AUTHORED value (ruling
    R-R16) and deleted the resolver's month-ordinal search that used to
    reconstruct it from ``(month_of_year, day_of_month)``.  A form now asks the
    user for the date outright, so nothing in ``app/`` derives one.

    What survives is a test that describes its subject as "a monthly bill on
    the 15th" and whose assertions were hand-computed against the dates that
    description produced.  Restating each as a literal would mean recomputing
    two dozen of them against a fixture schedule, so the description is
    translated instead -- ONCE, here, answering exactly what the retired
    derivation answered: the first date in the cadence's own residue class on
    or after the schedule's opening.

    Args:
        user_id: The owner, whose schedule sets the floor.
        fires_on_day: Day of the month the cadence fires on, 1-31.  Clamped by
            :func:`~app.services.recurrence._months.clamped_day` in a month too
            short to hold it, which is where a ``nominal_day`` comes from.
        fires_in_month: Month of the year, for the annual and semi-annual
            cadences.  ``None`` for a monthly one, which fires in every month.

    Returns:
        The :class:`datetime.date` to author as ``starts_on``.
    """
    # Pylint: ``import-outside-toplevel`` -- this module imports no app
    # symbols at top level (its collection-time-safety convention).
    # pylint: disable=import-outside-toplevel
    from app.services.pay_calendar import calendar_for
    from app.services.recurrence._months import (
        MONTHS_PER_YEAR,
        clamped_day,
        month_ordinal,
    )

    opening = calendar_for(user_id).opening_bound()
    ordinal = month_ordinal(opening)
    if fires_in_month is not None:
        # Step to the named month, then forward a year if it has already
        # passed -- the residue class the old ``_calendar_anchor`` walked.
        ordinal += (fires_in_month - opening.month) % MONTHS_PER_YEAR
    candidate = clamped_day(ordinal, fires_on_day)
    if candidate >= opening:
        return candidate
    step = MONTHS_PER_YEAR if fires_in_month is not None else 1
    return clamped_day(ordinal + step, fires_on_day)


def _default_first_occurrence(user_id, calendar, unit, placement):
    """Return what the retired anchor derivation answered for a day-less rule.

    :func:`make_cadence_rule`'s ``starts_on`` default, split out because it has
    THREE answers and each is a different retired derivation.  A fixture that
    states no date is describing a cadence, not a date, so what it must get is
    the date that description produced before plan step R7c-b -- otherwise
    every hand-computed assertion built on it moves.

    * ``PERIOD`` -- the schedule's opening payday, which is what
      ``_phased_period_anchor`` answered for a rule with no stated bound.
    * ``MONTHLY_FIRST`` -- the FIRST of the opening's own month.
      ``_first_of_month_anchor`` scanned for the earliest month whose own first
      payday was not before the effective start and took ``date_trunc('month')``
      of it; the opening's month always qualifies, because the opening IS the
      schedule's earliest payday.  **It can therefore fall BELOW the opening**,
      and that is not a defect: the occurrence is a marker the placement reads,
      and clamping it up to the opening drops the opening month's own paycheck.
      Measured: the fixture schedule opening 2026-01-02 owes 5 rows and a
      clamped default produced 4.
    * every other calendar cadence -- the first day-1 at or after the opening,
      because ``_calendar_anchor`` defaulted an unstated day to 1 and walked
      forward.

    The MONTHLY_FIRST arm is selected by
    :func:`~app.services.recurrence.fires_on_day_of_month`, the application's
    own projection of which cadences anchor on the calendar, rather than by a
    ``(unit, placement)`` pair written out here -- a second statement of that
    mapping is one that can disagree with it.

    Args:
        user_id: The owner, whose schedule sets the floor.
        calendar: That owner's :class:`~app.services.pay_calendar.PayCalendar`.
        unit: The cadence unit.
        placement: Which pay period funds an occurrence.

    Returns:
        The :class:`datetime.date` to author as ``starts_on``.
    """
    # Pylint: ``import-outside-toplevel`` -- this module imports no app
    # symbols at top level (its collection-time-safety convention).
    # pylint: disable=import-outside-toplevel
    from app.enums import RecurrenceUnitEnum
    from app.services.recurrence import fires_on_day_of_month

    if unit is RecurrenceUnitEnum.PERIOD:
        return calendar.opening_bound()
    if not fires_on_day_of_month(unit, placement):
        return calendar.opening_bound().replace(day=1)
    return first_occurrence_on_day(user_id, 1)


def transient_cadence_rule(user_id, cadence, **kwargs):
    """Author one rule of a stated CADENCE without adding it to the session.

    :func:`make_cadence_rule`'s unsaved twin, for the tests that hand a real
    rule to a route helper and never persist it -- the recurrence-form helpers,
    which re-point a rule in place and are called on a bare
    ``test_request_context``.

    It exists for the same reason its sibling does: plan step R7c-b made the
    two-axis columns ``NOT NULL``, so a hand-built rule stating its cadence in
    one field is a shape production cannot produce, and a helper reading it
    would resolve something the application never stores.

    **It writes through ``build_transient_rule`` DIRECTLY since plan step
    R-F6**, rather than through its sibling under a private ``_flush=False``
    flag.  The two stopped being one function with a switch: an authored rule
    takes the definition that owns it and an unsaved one has none, so the flag
    would have had to mean "and also skip the owner", which is two functions
    wearing one name.  What they still share -- the cadence-to-spec
    translation -- is :func:`_cadence_spec`, which both call.

    Args:
        user_id: Whose pay calendar the cadence resolves against, and whose
            UNSAVED definition the rule is built on.  Stated rather than taken
            as an owner object because the caller does not want a definition;
            it wants a rule it can hand to a pure producer.
        cadence: A :class:`~tests.oracles.recurrence_baseline.ShapeCadence`.
        **kwargs: Every other argument :func:`_cadence_spec` takes.

    Returns:
        The unsaved :class:`~app.models.recurrence_rule.RecurrenceRule`.
    """
    # Pylint: ``import-outside-toplevel`` -- this module imports no app
    # symbols at top level (its collection-time-safety convention).
    # pylint: disable=import-outside-toplevel
    from app.services.pay_calendar import calendar_for
    from app.services.recurrence import build_transient_rule

    calendar = calendar_for(user_id)
    # The door builds the unsaved owner itself from the spec's ``user_id``
    # (plan step R-F6), so ``rule.user_id`` has an answer and nothing reaches
    # the session.
    return build_transient_rule(
        _cadence_spec(user_id, cadence, calendar, **kwargs), calendar,
    )


def _cadence_spec(
    user_id,
    cadence,
    calendar,
    *,
    starts_on=None,
    fires_on_day=None,
    fires_in_month=None,
    interval_n=1,
    nominal_day=None,
    end_date=None,
    max_per_month=None,
):
    """Translate a stated CADENCE into the spec the write door takes.

    The shared half of :func:`make_cadence_rule` and
    :func:`transient_cadence_rule`, which differ only in whether the resulting
    rule is written onto a definition or left unsaved.  Split out at plan step
    R-F6, when the authored path grew an owner argument the transient one
    cannot have.

    Args:
        user_id: Whose calendar the cadence resolves against.
        cadence: The :class:`~tests.oracles.recurrence_baseline.ShapeCadence`
            to author.
        calendar: That owner's :class:`~app.services.pay_calendar.PayCalendar`,
            passed in because both callers already hold one.
        starts_on: See :func:`make_cadence_rule`.
        fires_on_day: See :func:`make_cadence_rule`.
        fires_in_month: See :func:`make_cadence_rule`.
        interval_n: See :func:`make_cadence_rule`.
        nominal_day: See :func:`make_cadence_rule`.
        end_date: See :func:`make_cadence_rule`.
        max_per_month: See :func:`make_cadence_rule`.

    Returns:
        The :class:`~app.services.recurrence.RecurrenceSpec`.
    """
    # Pylint: ``import-outside-toplevel`` -- this module imports no app
    # symbols at top level (its collection-time-safety convention).
    # pylint: disable=import-outside-toplevel
    from app.services.recurrence import (
        NEVER_ENDS,
        EndsOnDate,
        RecurrenceSpec,
    )
    from tests.oracles.recurrence_baseline import ShapeCadence

    if starts_on is not None and fires_on_day is not None:
        raise ValueError(
            "make_cadence_rule takes starts_on OR fires_on_day, not both: "
            "they are two statements of the same fact and only one can be "
            f"authored (got {starts_on!r} and day {fires_on_day!r})",
        )
    # The TYPE is checked at the door because every other read of *cadence*
    # below is an attribute access, so a caller still passing plan step R9's
    # retired shorthand -- the string "Monthly", or a member of the deleted
    # ``RecurrencePatternEnum`` -- would otherwise surface as an
    # ``AttributeError`` from three frames down naming ``interval_n``.
    if not isinstance(cadence, ShapeCadence):
        raise TypeError(
            f"make_cadence_rule takes a ShapeCadence, not {cadence!r}.  Plan "
            f"step R9 retired the closed pattern set's display names with the "
            f"table they came from; state the two axes instead, as one of "
            f"tests.oracles.recurrence_baseline's cadence constants.",
        )
    resolved_interval = (
        interval_n if cadence.interval_n is None else cadence.interval_n
    )
    if starts_on is None and fires_on_day is not None:
        starts_on = first_occurrence_on_day(
            user_id, fires_on_day, fires_in_month,
        )
    if starts_on is None:
        starts_on = _default_first_occurrence(
            user_id, calendar, cadence.unit, cadence.placement,
        )
    return RecurrenceSpec(
        user_id=user_id,
        unit=cadence.unit,
        placement=cadence.placement,
        interval_n=resolved_interval,
        starts_on=(
            starts_on if starts_on is not None
            else calendar.opening_bound()
        ),
        nominal_day=nominal_day,
        end_bound=(
            NEVER_ENDS if end_date is None else EndsOnDate(end_date)
        ),
        max_per_month=max_per_month,
    )


def make_cadence_rule(owner, cadence, **kwargs):
    """Author one rule of a stated CADENCE ONTO *owner*, through the write door.

    :func:`make_every_period_rule`'s general sibling, for the tests that need a
    cadence other than every-paycheck.  Both exist because a rule may not be
    constructed field by field any more: plan step R7c-b made ``unit_id``,
    ``placement_id``, ``shift_id`` and ``starts_on`` ``NOT NULL``, so a
    hand-built ``RecurrenceRule`` stating a cadence in one field is a
    constraint violation rather than a shortcut.

    **It took a closed-set NAME until plan step R9**, resolved through a
    ``CADENCE_BY_LEGACY_NAME`` table in the baseline oracle.  R9 drops
    ``ref.recurrence_patterns`` and ``RecurrencePatternEnum``, and the table
    of names went with them rather than outliving both: a caller states the
    two axes the row actually holds, as a
    :class:`~tests.oracles.recurrence_baseline.ShapeCadence` constant beside
    the frozen shapes -- so a fixture asking for the quarterly cadence and a
    captured shape labelled ``quarterly`` still cannot come to mean different
    things, and a mistyped one is a ``NameError`` at import.

    **It takes the OWNING DEFINITION rather than a user id, since plan step
    R-F6**: the owning FK is on ``budget.recurrence_rules`` under
    ``ck_recurrence_rules_one_owner``, so a rule with no template is not a row
    the database accepts.  A fixture builds its template first and makes it
    repeat second, which is the order production runs in.

    Args:
        owner: The ``TransactionTemplate`` or ``TransferTemplate`` the
            recurrence belongs to.  Mutated: its ``recurrence_rule`` is set.
            Its ``user_id`` is whose calendar the cadence resolves against.
        cadence: The :class:`~tests.oracles.recurrence_baseline.ShapeCadence`
            to author -- one of that module's seven constants, or any other
            ``(interval_n, unit, placement)`` a test needs.
        starts_on: The rule's FIRST OCCURRENCE (ruling R-R16).  Defaults to
            what the retired derivation answered for a rule stating no day:
            the schedule's opening payday for a PAYCHECK-space cadence, and
            the first 1st of a month the schedule reaches for a calendar
            one -- so a fixture that states nothing generates where it
            always did, and a MONTHLY_FIRST rule does not read as firing on
            whichever day of the month the schedule happened to open on.
        fires_on_day: An alternative to *starts_on* for a test that describes
            its subject as a cadence rather than as a date -- "a monthly bill
            on the 15th".  Translated by :func:`first_occurrence_on_day`; see
            it for why the translation lives in the tests and not in ``app/``.
            Mutually exclusive with *starts_on*.
        fires_in_month: The month half of that description, for the annual and
            semi-annual cadences.
        interval_n: Read only when *cadence* fixes none of its own -- exactly
            ``EVERY_N_PERIODS``; every other constant carries one and this
            argument is discarded.  (The COLUMN takes any positive interval
            for any unit since plan step R7c-c -- what is narrow here is the
            constant, not the model.)
        nominal_day: The day the rule MEANS when *starts_on*'s month clamped
            it (ruling R-R3).
        end_date: The rule's closing bound.  ``None`` never ends.
        max_per_month: The per-month ceiling (plan step salary:R15-a), or
            ``None`` for none.

    Returns:
        The flushed :class:`~app.models.recurrence_rule.RecurrenceRule`.
    """
    # Pylint: ``import-outside-toplevel`` -- this module imports no app
    # symbols at top level (its collection-time-safety convention).
    # pylint: disable=import-outside-toplevel
    from app.services.pay_calendar import calendar_for
    from app.services.recurrence import author_rule

    calendar = calendar_for(owner.user_id)
    return author_rule(
        _cadence_spec(owner.user_id, cadence, calendar, **kwargs),
        calendar,
        owner,
    )


def make_expense_template(
    db_session, seed_user, amount="1200.00", is_active=True, *,
    name="Rent", category_key="Rent", is_envelope=False,
    companion_visible=False, account=None, category=None,
):
    """Create and flush an every-period expense template on the seed account.

    Shared by the pay-period CRUD test suites so the
    ``RecurrenceRule`` + ``TransactionTemplate`` construction block is
    defined once.  The caller commits.  **It states the definition's price
    and its cadence**, so what it returns is a definition the engine can
    generate from -- the shape every real one has (:func:`state_template_price`
    says why a bare template is not) -- and :func:`generate_row_of` is the
    next line for a fixture that wants the definition's row.
    :func:`make_income_template` is the same builder for an income
    definition; the two share one body.

    Args:
        db_session: The test session.
        seed_user: The seed user fixture dict (or the second user's).
        amount: The stated price, as a string.
        is_active: Whether the definition is live.
        name: The definition's name.  Keyword-only, with the name every
            existing caller relied on, so widening this helper for the shared
            fixtures (plan step balance:X-cf) changed no caller.
        category_key: A key into ``seed_user["categories"]``.
        is_envelope: Whether the definition's rows track purchases.
        companion_visible: Whether a companion of the owner may see its rows.
            The second of the two flags a generated row RESOLVES through its
            definition (``Transaction.visible_to_companion``), widened here
            for the same reason as ``is_envelope`` and defaulting to the
            column's own default.
        account: The :class:`~app.models.account.Account` the definition
            moves money through; the seed user's checking account when
            omitted.  Widened at plan step balance:X-cf-3 for the fixtures
            whose row lives on an HYSA or a 401(k): the engine puts a row on
            its DEFINITION's account (``DerivedRowFields.account_id``), so
            that is the only place a fixture can say where the row goes.
        category: The :class:`~app.models.category.Category` the definition
            files under, when it is not one of the seed's -- a test that
            authors its own category passes the row here and *category_key*
            is not read.  Widened at plan step balance:X-cf-4 for the same
            reason as *account*: the engine files a row under its
            DEFINITION's category (``DerivedRowFields.category_id``), so the
            definition is the only place a fixture can say so.

    Returns:
        The flushed :class:`~app.models.transaction_template.TransactionTemplate`,
        priced and carrying an every-paycheck rule.
    """
    # Pylint: ``import-outside-toplevel`` -- this module imports no app
    # symbols at top level (its collection-time-safety convention).
    # pylint: disable=import-outside-toplevel
    from app.enums import TxnTypeEnum

    return _priced_repeating_template(
        db_session, seed_user, TxnTypeEnum.EXPENSE, amount, is_active,
        name=name, category_key=category_key, is_envelope=is_envelope,
        companion_visible=companion_visible, account=account,
        category=category,
    )


def make_income_template(
    db_session, seed_user, amount="2000.00", is_active=True, *,
    name="Paycheck", category_key="Salary", is_envelope=False,
    companion_visible=False, account=None, category=None,
):
    """Create and flush an every-period INCOME template on the seed account.

    :func:`make_expense_template`'s twin for a definition that pays the owner
    rather than bills them, sharing that helper's body so a fixture wanting an
    income definition's row (plan step balance:X-cf) does not restate the
    template stanza with one column changed.  Same contract: priced, carrying
    an every-paycheck rule, and :func:`generate_row_of` is the next line.

    Args:
        db_session: The test session.
        seed_user: The seed user fixture dict (or the second user's).
        amount: The stated price, as a string.
        is_active: Whether the definition is live.
        name: The definition's name.
        category_key: A key into ``seed_user["categories"]``.
        is_envelope: Whether the definition's rows track purchases.
        companion_visible: Whether a companion of the owner may see its rows.
        account: The account the definition pays into; the seed user's
            checking account when omitted.  See :func:`make_expense_template`.
        category: The category the definition files under, when it is not
            one of the seed's.  See :func:`make_expense_template`.

    Returns:
        The flushed :class:`~app.models.transaction_template.TransactionTemplate`,
        priced and carrying an every-paycheck rule.
    """
    # Pylint: ``import-outside-toplevel`` -- this module imports no app
    # symbols at top level (its collection-time-safety convention).
    # pylint: disable=import-outside-toplevel
    from app.enums import TxnTypeEnum

    return _priced_repeating_template(
        db_session, seed_user, TxnTypeEnum.INCOME, amount, is_active,
        name=name, category_key=category_key, is_envelope=is_envelope,
        companion_visible=companion_visible, account=account,
        category=category,
    )



def make_projected_envelope_expense(
    db_session, *, seed_user, pay_period, estimated, account=None,
    name="Groceries",
):
    """Create a Projected envelope expense + its definition in ``pay_period``.

    The engine's own row of a priced, every-paycheck ``is_envelope=True``
    definition (:func:`make_expense_template` then :func:`generate_row_of`,
    plan step balance:X-cf), which is what entries attach to.  Uses the seed
    user's Groceries category so the row matches the symptom #1 / #5 worked
    example.  ``account`` defaults to the seed user's checking account; pass
    the account when the row should live elsewhere -- the engine puts a row
    on its DEFINITION's account, so that is where the choice is made.

    **One definition** (plan ledger row BAL-490, closed at balance:X-ch): the
    accounts route suite and the savings dashboard suite each carried a
    private copy, and by the time they were folded here the copies had
    already drifted in signature (one required ``account``, one defaulted
    it), which is the drift a fixture spelled twice invites.

    Args:
        db_session: The test session.
        seed_user: The seed user fixture dict.
        pay_period: The period the row lands in.
        estimated: The definition's stated price, as a string.
        account: The account the definition (and so the row) lives on;
            ``None`` means the seed user's checking account.
        name: The definition's name.

    Returns:
        The generated Transaction row, flushed.
    """
    template = make_expense_template(
        db_session, seed_user, amount=estimated,
        name=name, category_key="Groceries", is_envelope=True,
        account=account,
    )
    return generate_row_of(template, pay_period)

def _priced_repeating_template(
    db_session, seed_user, txn_type, amount, is_active, *,
    name, category_key, is_envelope, companion_visible, account, category,
):
    """The one body behind :func:`make_expense_template` and its income twin.

    Args:
        db_session: The test session.
        seed_user: The seed user fixture dict (or the second user's).
        txn_type: The :class:`~app.enums.TxnTypeEnum` member the definition
            is of.
        amount: The stated price, as a string.
        is_active: Whether the definition is live.
        name: The definition's name.
        category_key: A key into ``seed_user["categories"]``.
        is_envelope: Whether the definition's rows track purchases.
        companion_visible: Whether a companion of the owner may see its rows.
        account: The account the definition is on, or ``None`` for
            ``seed_user["account"]``.
        category: The category the definition files under, or ``None`` for
            ``seed_user["categories"][category_key]``.

    Returns:
        The flushed :class:`~app.models.transaction_template.TransactionTemplate`,
        priced and carrying an every-paycheck rule.
    """
    # Pylint: ``import-outside-toplevel`` -- this module imports no app
    # symbols at top level (its collection-time-safety convention).
    # pylint: disable=import-outside-toplevel
    from app import ref_cache
    from app.models.transaction_template import TransactionTemplate

    template = TransactionTemplate(
        user_id=seed_user["user"].id,
        account_id=(seed_user["account"] if account is None else account).id,
        category_id=(
            seed_user["categories"][category_key] if category is None
            else category
        ).id,
        transaction_type_id=ref_cache.txn_type_id(txn_type),
        name=name,
        default_amount=Decimal(amount),
        is_active=is_active,
        is_envelope=is_envelope,
        companion_visible=companion_visible,
    )
    db_session.add(template)
    db_session.flush()
    state_template_price(template)
    # The definition first, then the cadence onto it (plan step R-F6).
    make_every_period_rule(db_session, template)
    return template


def populate_in_a_fresh_pass(user_id, period_ids):
    """Open a read pass over the CURRENT state and populate *period_ids*.

    The producer takes a :class:`~app.services.balance_at.BalanceContext` and
    builds none (ruling **R-R38**), so every caller owes it a pass -- and owes
    it AFTER the write that created the periods, because a pass resolved
    earlier holds a calendar that does not contain them and
    ``GenerationSchedule.__post_init__`` refuses the window.  Route code says
    that once, in ``app.routes._period_population.populate_new_periods``, which
    takes the PayPeriod rows a door returned; the suite says it here, because
    what a test usually holds is a set of ids.

    Args:
        user_id: The owning user's id.
        period_ids: The ``budget.pay_periods.id`` values to populate.

    Returns:
        The number of template-linked records created.
    """
    # Pylint: ``import-outside-toplevel`` -- this module imports no app
    # symbols at top level (its collection-time-safety convention).
    # pylint: disable=import-outside-toplevel
    from app.services.balance_at import BalanceContext
    from app.services.period_population import (
        populate_periods_from_active_templates,
    )

    return populate_periods_from_active_templates(
        BalanceContext.build(user_id), period_ids,
    )


def make_transfer_template(db_session, seed_user, to_account, amount="200.00"):
    """Create and flush an every-period transfer template (checking -> to).

    Shared by the pay-period CRUD test suites so the
    ``RecurrenceRule`` + ``TransferTemplate`` construction block is
    defined once.  The caller commits.
    """
    # Pylint: ``import-outside-toplevel`` -- this module imports no app
    # symbols at top level (its collection-time-safety convention).
    # pylint: disable=import-outside-toplevel
    from app.models.transfer_template import TransferTemplate

    template = TransferTemplate(
        user_id=seed_user["user"].id,
        from_account_id=seed_user["account"].id,
        to_account_id=to_account.id,
        name="To Savings",
        default_amount=Decimal(amount),
    )
    db_session.add(template)
    db_session.flush()
    # A definition STATES its price, exactly as ``routes/transfers/templates``
    # does on every create.  Since plan step X-au-f a generated TRANSFER stores
    # no figure either, so a bare-constructed transfer template generates rows
    # nothing can price -- the transaction twin's obligation, one table over.
    state_template_price(template)
    # The definition first, then the cadence onto it (plan step R-F6).
    make_every_period_rule(db_session, template)
    return template


def bind_rule_to_loan(rule, account_id):
    """Bound a fixture's NEWLY built recurrence rule's START to its loan's contract.

    **A test helper since plan step R7d-g-3, moved here from
    ``loan_recurrence_sync``** (plan ledger row **REC-526**): its last
    application caller, the loan dashboard's create door, built its rule
    from :func:`~app.services.loan_recurrence_sync.loan_cadence_start` and
    then called this on the same rule -- the same pair re-derived through the
    same producer, a no-op by construction -- and the generic transfer form's
    call went at R7d-g-2 (ruling **R-R85**).  What fixtures still need is the
    composition the app's doors perform in two steps: author a rule with a
    fixture day, then put the loan's own first contractual installment onto
    it.  Re-stated here over the PUBLIC producers -- ``loan_cadence_spec`` for
    the wanted spec, ``reauthor_rule`` for the write -- with the production
    writer's two idempotency stages (``_sync_loan_cadence``, which the doors
    run at every point a definition becomes the standing payment, compares
    the authored spec and then the resolved one before it writes); its
    refusal translation and its audit event are the doors', not a fixture's.
    A no-op unless *account_id* is a configured loan, so a fixture may call
    it for ANY destination without a type check.

    Takes the rule DIRECTLY rather than looking it up from the account, which
    the production sync must do: that lookup returns the account's OLDEST
    active recurring template, so a fixture building a second recurring
    payment into the same loan would leave the NEW rule unbound while
    re-binding the old one.

    Args:
        rule: The just-built ``RecurrenceRule``, before generation.
        account_id: The transfer's destination account (any kind).

    Raises:
        EmptyAuthoredWindowError: The re-derived first installment falls after
            a stop the rule's owner authored -- the write door's own refusal,
            untranslated: the ``ValidationError`` sentence naming the transfer
            is ``_sync_loan_cadence``'s and belongs to the doors that run it.
    """
    # Pylint: ``import-outside-toplevel`` -- this module imports no app
    # symbols at top level (its collection-time-safety convention).
    # pylint: disable=import-outside-toplevel
    from app.services.loan_loaders import load_loan_params
    from app.services.loan_recurrence_sync import loan_cadence_spec
    from app.services.pay_calendar import calendar_for
    from app.services.recurrence import (
        reauthor_rule, recurrence_spec, resolve,
    )
    params = load_loan_params(account_id)
    if params is None:
        return
    current = recurrence_spec(rule)
    wanted = loan_cadence_spec(current, params)
    if wanted == current:
        return
    calendar = calendar_for(rule.user_id)
    if resolve(wanted, calendar) == resolve(current, calendar):
        return
    reauthor_rule(rule, wanted, calendar)


def make_loan_payment_template(
    db_session, seed_user, loan_account, amount="200.00", *,
    derive_from_loan=True, extra_principal="0.00", cadence=None,
    fires_on_day=None,
):
    """Create the recurring transfer a LOAN payment actually is.

    :func:`make_transfer_template`'s loan-shaped sibling, and the difference is
    the ``budget.loan_payment_settings`` row.  ``routes/loan/payment_transfer.py``
    writes one on every loan payment IT creates -- it is what carries the MODE
    (``derive_from_loan``) and the standing overpayment -- so a fixture pointing
    the generic builder at a loan builds a definition the LOAN's own door would
    never leave behind.

    **A settings-less recurring transfer into a loan is still REACHABLE**, and
    an adversarial review of plan step R7d-a corrected an earlier version of
    this paragraph that said otherwise: ``POST /transfers`` offers every active
    account as a destination and attaches no settings row
    (``routes/transfers/templates.py``), and production holds ZERO
    ``loan_payment_settings`` rows, so it is the state the developer's own two
    loans are in.  What is unreachable is that state arising from the loan
    dashboard's create-transfer button, which is the door these fixtures stand
    in for.

    **It went unnoticed while nothing read the definition for an unmaterialised
    installment.**  R7d-a made the forward plan price every installment no row
    covers from the loan's own standing payment, so the generic builder's
    arbitrary base -- ``$200.00``, named "To Savings" -- started meaning "this
    is what the owner pays the mortgage", and six tests whose docstrings state
    the CONTRACTUAL figure began asserting against a loan being paid a fifth of
    it.  The numbers were right; the fixture was not.

    Defaults to DERIVE mode, which is what the loan dashboard's own
    create-transfer button writes: the payment IS the loan's P&I plus escrow
    plus any standing extra, so the *amount* is a snapshot and no test has to
    keep it in step with the loan's terms.  Pass ``derive_from_loan=False`` to
    build the MANUAL payment an owner types a figure for.

    Args:
        db_session: The test ``db.session``.
        seed_user: The ``seed_user`` fixture dict (supplies the owner and the
            checking account the payment leaves from).
        loan_account: The destination loan account.
        amount: The template's stored ``default_amount``.  In derive mode this
            is the snapshot a generated row carries and the live derivation
            supersedes; in manual mode it is the figure the owner stated.
        derive_from_loan: The settings row's mode.
        extra_principal: The settings row's standing monthly overpayment.
        cadence: The :class:`~tests.oracles.recurrence_baseline.ShapeCadence`
            to author.  ``None`` -- the default -- authors what the loan door
            (``routes/loan/payment_transfer.py``) authors: a MONTHLY rule on
            the loan's own ``payment_day``, its start bound to the first
            contractual installment through ``bind_rule_to_loan``.  **It was
            the every-paycheck rule :func:`make_transfer_template` authors
            until plan step R16-b-2**, inherited from the fixtures this
            replaced, and the difference was invisible while the forward plan
            synthesized one contractual slot a month whatever the definition's
            cadence said (finding **D48**).  The plan sums each definition on
            its OWN cadence now, so an every-paycheck DERIVE-mode payment pays
            a full P&I twenty-six times a year and retires a 24-month loan in
            sixteen -- which is what that definition says, and not what 46
            fixtures meaning "the loan's own payment" said.  A test about an
            every-paycheck payment states ``EVERY_PERIOD`` here.
        fires_on_day: The day of the month a calendar *cadence* first fires on,
            forwarded to :func:`make_cadence_rule`.  Unread for the default,
            whose start the loan door's sync writes.

    Returns:
        The flushed ``TransferTemplate``, its ``recurrence_rule`` set.
    """
    # Pylint: ``import-outside-toplevel`` -- this module imports no app
    # symbols at top level (its collection-time-safety convention).
    # pylint: disable=import-outside-toplevel
    from app.models.loan_payment_settings import LoanPaymentSettings
    from app.models.transfer_template import TransferTemplate

    template = TransferTemplate(
        user_id=seed_user["user"].id,
        from_account_id=seed_user["account"].id,
        to_account_id=loan_account.id,
        name=f"Loan Payment {loan_account.id}",
        default_amount=Decimal(amount),
    )
    db_session.add(template)
    db_session.flush()
    # **Priced BEFORE the settings row is attached, and the order is the same
    # one ``track_payment`` takes.**  ``template_amount_service.owns_its_amount``
    # is False for a DERIVE-mode loan payment, so the write door would refuse a
    # version stated after the flip -- and the versions a template already holds
    # stay as the record of what was stated while it was manual, which is what
    # a MANUAL payment's base is read from since plan step X-au-f.
    state_template_price(template)
    # Attached through the relationship so it flushes with the template, the
    # way the route attaches it.
    template.settings = LoanPaymentSettings(
        derive_from_loan=derive_from_loan,
        extra_principal=Decimal(extra_principal),
    )
    db_session.flush()
    # The definition first, then the cadence onto it (plan step R-F6), then
    # -- for the door's own shape -- the loan's start onto the cadence
    # (``bind_rule_to_loan``, plan step C9a), the order ``track_payment``
    # takes.
    if cadence is None:
        from app.services.loan_loaders import load_loan_params
        from tests.oracles.recurrence_baseline import MONTHLY

        # An amortizing account with NO params is not a loan the door can
        # bind to (``bind_rule_to_loan`` is a no-op for it); the rule is
        # monthly on the 1st and starts where the schedule's first 1st is.
        params = load_loan_params(loan_account.id)
        rule = make_cadence_rule(
            template, MONTHLY,
            fires_on_day=1 if params is None else params.payment_day,
        )
        bind_rule_to_loan(rule, loan_account.id)
        db_session.flush()
    else:
        make_cadence_rule(template, cadence, fires_on_day=fires_on_day)
    return template


def make_retired_loan_payment(
    db_session, seed_user, *, origination_date, cleared_on, payment_day=1,
    name="Retired Loan",
):
    """Create a MONTHLY loan payment whose loan was trued to ZERO on *cleared_on*.

    The fixture the three surfaces that read a definition's stop share since
    plan step R7d-e -- the Recurring surface, the obligations aggregator and
    the ``/savings`` floor -- because each asserts the same money fact: a
    payment against a loan that is finished leaves the committed totals on the
    day the loan closed.  One builder, so the three cannot describe three
    different loans.

    The loan is ``$12,000.00`` at 5% over 24 months, originating
    *origination_date* with the contractual *payment_day*; its first
    installment is that day of the month AFTER origination
    (``installment_calendar.first_installment_date``: a loan closed 2026-05-01
    with a ``payment_day`` of 1 owes first on 2026-06-01, not on the day it
    closed).  The true-up to ``$0.00`` on *cleared_on* retires it, so its
    closing date is
    *cleared_on* -- the day it LAST became closed (plan step ``recurrence:R7d-h``)
    -- and the definition has fired once wherever *cleared_on* follows the
    first installment, so the derived stop is a date and not "never runs".

    The payment is bound to the loan through the production door for the
    OPENING bound (``bind_rule_to_loan``), so ``starts_on`` is the contract's
    first installment rather than a fixture day.  **Its ``end_date`` column is
    left NULL**: nothing stored could supply the stop, so whatever a reader
    names about it came from the derivation.

    Args:
        db_session: The test ``db.session``.
        seed_user: The ``seed_user`` fixture dict (the owner and the checking
            account the payment leaves from).
        origination_date: The loan's origination date.
        cleared_on: The day the balance is trued to zero.
        payment_day: The contractual day of the month (default 1).
        name: The loan account's name.

    Returns:
        ``(loan, template)``, committed.
    """
    # Pylint: ``import-outside-toplevel`` -- this module imports no app
    # symbols at top level (its collection-time-safety convention).
    # pylint: disable=import-outside-toplevel
    from tests.oracles.recurrence_baseline import MONTHLY

    loan = create_loan_account(
        seed_user, db_session, name=name,
        principal=Decimal("12000.00"), rate=Decimal("0.05000"), term=24,
        origination_date=origination_date, payment_day=payment_day,
    )
    insert_trueup_event(
        loan_params_for(db_session, loan.id), Decimal("0.00"),
        anchor_date=cleared_on,
    )
    template = make_loan_payment_template(
        db_session, seed_user, loan, cadence=MONTHLY, fires_on_day=payment_day,
    )
    bind_rule_to_loan(template.recurrence_rule, loan.id)
    db_session.commit()
    return loan, template


def make_appreciating_account(seed_user, db_session, anchor_period, balance, rate):
    """Create a Property account (APPRECIATING) with AssetAppreciationParams.

    The shared appreciating-asset builder for the balance-seam parity
    suite and the cross-page balance-equality lock (promoted from the
    per-suite ``_make_property`` copies).  Routes the account through the
    canonical ``account_service.create_account`` factory (so it gets its
    origination ``AccountAnchorHistory`` row), then attaches the
    ``AssetAppreciationParams`` row that carries the annual appreciation
    rate so the account classifies APPRECIATING.  Commits before
    returning so the account is fully resolvable.

    **The opening assertion is stamped at the anchor period's first day**
    (via :func:`reassert_balance_on`) -- finding N-77, fixed at plan step
    X-g2a for the reason :func:`create_hysa_account` was at X-c2a, and read
    there for the full argument.  ``account_service.create_account`` writes that
    row with the WALL CLOCK, and from plan step X-g2b a Property's appreciation
    accrues only forward of its LATEST assertion (ruling R-Y), so an unpinned
    opening is the newest assertion, lands past the suite's seeded horizon, and
    the account then appreciates NOTHING anywhere -- a state production cannot
    reach.  A test that needs a MID-period or later assertion appends its own,
    exactly as the interest suites do.

    Args:
        seed_user: The ``seed_user`` fixture dict.
        db_session: The test ``db.session``.
        anchor_period: The :class:`~app.models.pay_period.PayPeriod` to
            anchor the account against; its ``id`` becomes the account's
            ``current_anchor_period_id``.
        balance: The user-set market value, used as the anchor balance
            (Decimal -- construct from a string per the coding standard).
        rate: The annual appreciation rate as a Decimal fraction (e.g.
            ``Decimal("0.03000")`` for 3%).

    Returns:
        The created Property :class:`~app.models.account.Account`.
    """
    # Pylint: ``import-outside-toplevel`` -- this module imports no app
    # symbols at top level (its collection-time-safety convention); load
    # the models lazily, the same way the loan helpers above do.
    # pylint: disable=import-outside-toplevel
    from app.models.asset_appreciation_params import AssetAppreciationParams
    from app.models.ref import AccountType
    from app.services import account_service

    property_type = (
        db_session.query(AccountType).filter_by(name="Property").one()
    )
    account = account_service.create_account(
        account_service.AccountSpec(
            user_id=seed_user["user"].id,
            account_type_id=property_type.id,
            name="House",
            anchor_balance=balance,
            observed_on=anchor_period.start_date,
        ),
    )
    db_session.add(account)
    db_session.flush()
    db_session.add(AssetAppreciationParams(
        account_id=account.id, annual_appreciation_rate=rate,
    ))
    # **And then before every day a row could land on** (plan step X-f3c-2b,
    # ruling **R-HG**).  ``create_account`` opens the books on the day it was
    # handed, which is right when the anchor period is the earliest -- and
    # these factories are routinely handed a LATER period while the suite
    # records movements in an earlier one.  Backward-only, so it never undoes
    # the day stated above.
    open_books_before_the_first_assertion(db_session, account)
    db_session.commit()
    return account


def make_investment_account(
    seed_user, db_session, anchor_period, balance, name="401k",
    employer_type="none", match_pct=None, match_cap_pct=None,
):
    """Create a 401(k) account (INVESTMENT) with InvestmentParams (7% return).

    The shared investment-account builder for the balance-seam parity
    suite and the cross-page balance-equality lock (promoted from the
    per-suite ``_make_401k`` copies).  Routes the account through the
    canonical ``account_service.create_account`` factory, then attaches an
    ``InvestmentParams`` row (7% assumed annual return) so the account
    classifies INVESTMENT.  Commits before returning so the account is
    fully resolvable.

    **The opening assertion is stamped at the anchor period's first day**
    (via :func:`reassert_balance_on`) -- finding N-77, fixed at plan step
    X-g2a; see :func:`make_appreciating_account` beside it and
    :func:`create_hysa_account` for the full argument.  From plan step X-g2b an
    investment's growth accrues only forward of its LATEST assertion (ruling
    R-Y), and the factory writes that row with the WALL CLOCK, so an unpinned
    opening leaves the account growing nowhere.

    Args:
        seed_user: The ``seed_user`` fixture dict.
        db_session: The test ``db.session``.
        anchor_period: The :class:`~app.models.pay_period.PayPeriod` to
            anchor the account against; its ``id`` becomes the account's
            ``current_anchor_period_id``.
        balance: The opening anchor balance (Decimal -- construct from a
            string per the coding standard).
        name: The account name (default ``"401k"``); parameterised so a
            caller can seed two investment accounts for one user without
            colliding on the ``(user_id, name)`` unique constraint.
        employer_type: The :class:`~app.enums.EmployerContributionTypeEnum`
            value (default ``"none"`` -- no employer contribution).
        match_pct: Employer match percentage (Decimal) or ``None``.
        match_cap_pct: Employer match cap percentage (Decimal) or ``None``.

    Returns:
        The created 401(k) :class:`~app.models.account.Account`.
    """
    # Pylint: ``import-outside-toplevel`` -- this module imports no app
    # symbols at top level (its collection-time-safety convention); load
    # the models lazily, the same way the loan helpers above do.
    # pylint: disable=import-outside-toplevel
    from app import ref_cache
    from app.enums import EmployerContributionTypeEnum
    from app.models.investment_params import InvestmentParams
    from app.models.ref import AccountType
    from app.services import account_service

    inv_type = db_session.query(AccountType).filter_by(name="401(k)").one()
    account = account_service.create_account(
        account_service.AccountSpec(
            user_id=seed_user["user"].id,
            account_type_id=inv_type.id,
            name=name,
            anchor_balance=balance,
            observed_on=anchor_period.start_date,
        ),
    )
    db_session.add(account)
    db_session.flush()
    db_session.add(InvestmentParams(
        account_id=account.id,
        assumed_annual_return=Decimal("0.07000"),
        employer_contribution_type_id=ref_cache.employer_contribution_type_id(
            EmployerContributionTypeEnum(employer_type),
        ),
        employer_match_percentage=match_pct,
        employer_match_cap_percentage=match_cap_pct,
    ))
    # **And then before every day a row could land on** (plan step X-f3c-2b,
    # ruling **R-HG**).  ``create_account`` opens the books on the day it was
    # handed, which is right when the anchor period is the earliest -- and
    # these factories are routinely handed a LATER period while the suite
    # records movements in an earlier one.  Backward-only, so it never undoes
    # the day stated above.
    open_books_before_the_first_assertion(db_session, account)
    db_session.commit()
    return account


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
        "update the anchors in tests/_test_helpers.py"
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
        "tests/_test_helpers.py"
    )
    monkeypatch.setattr(
        migration_module, "_CREATE_FALLBACK_LEDGER_ACCOUNTS_SQL",
        fallback_sql.replace(_FROZEN_FALLBACK_CONFLICT, _HEAD_FALLBACK_CONFLICT),
    )
    backfill_sql = migration_module._SETTLED_TRANSACTION_BACKFILL_SQL
    assert _FROZEN_FALLBACK_JOIN in backfill_sql, (
        "the frozen 7d63 Pass-B fallback join changed; update the anchor in "
        "tests/_test_helpers.py"
    )
    monkeypatch.setattr(
        migration_module, "_SETTLED_TRANSACTION_BACKFILL_SQL",
        backfill_sql.replace(_FROZEN_FALLBACK_JOIN, _HEAD_FALLBACK_JOIN),
    )


def net_posted_by_day(filter_clause):
    """Return ``{entry_date: net posted magnitude}`` for one source row.

    The LEDGER half of every settle-day assertion in the X-f1c suites, shared
    because the transaction and transfer versions differed only in their filter
    clause (``JournalEntry.transaction_id == id`` vs ``.transfer_id == id``
    then; both links went at plan step ``balance:X-bi-6-5``'s second leaf,
    card plan#1845, and a caller passes a family read now) -- identical
    ``group_by``, identical reduction, identical rationale.  ``tests/``
    is outside R0801's reach, so that duplication had to be found by reading;
    it was, by a neutral review.

    **The NET is what separates "re-dated" from "posted twice".**  A settle-day
    correction leaves the original day's entries in place as history and adds a
    reversal (the R2 attribution rule), so the raw list of ``entry_date`` values
    still contains the old day and a membership test grades nothing.  Summed per
    ``(day, ledger account)`` the reconciled-away day collapses to ``$0.00`` and
    only the live day carries the effect.

    The reduction is per LEDGER ACCOUNT, not per day: netting across accounts
    within a day would collapse to ``$0.00`` for ANY balanced entry, which is
    every entry this project writes -- an assertion that could never fail.  The
    per-day value returned is the LARGEST per-account absolute residue, not a
    sum across accounts; every leg of a balanced entry carries the same
    magnitude, so it reads as "the size of this row's effect on that day".  An
    earlier draft of this paragraph said "the sum", which the code never did.

    Args:
        filter_clause: The SQLAlchemy clause selecting the journal entries of
            one source row, e.g. ``family_journal_filter(txn)``.

    Returns:
        ``{entry_date: Decimal}`` for the days that still carry a non-zero net
        posted effect.  A day whose entries net to zero is DROPPED, so the
        mapping reads as "where this row's money is posted right now" and an
        exact-dict assertion grades both halves of a correction at once.
    """
    # Imported here rather than at module scope: this helper module is imported
    # by conftest-adjacent code before the app's models are configured in some
    # collection orders, and the ledger models are needed only by this function.
    from app.extensions import db  # pylint: disable=import-outside-toplevel
    from app.models.journal_entry import (  # pylint: disable=import-outside-toplevel
        JournalEntry, Posting,
    )

    rows = (
        db.session.query(
            JournalEntry.entry_date,
            Posting.ledger_account_id,
            db.func.sum(Posting.amount),
        )
        .join(Posting, Posting.journal_entry_id == JournalEntry.id)
        .filter(filter_clause)
        .group_by(JournalEntry.entry_date, Posting.ledger_account_id)
        .all()
    )
    net = {}
    for entry_date, _ledger_account_id, total in rows:
        residue = abs(Decimal(str(total)))
        net[entry_date] = max(net.get(entry_date, Decimal("0.00")), residue)
    return {day: amount for day, amount in net.items() if amount != 0}


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


def linked_ledger_total(account_id):
    """Return the summed posting amount on one account's LINKED ledger account.

    The account's ledger-native balance as the double-entry ledger holds it:
    the anchor reconcile's whole job is to keep this equal to the account's
    resolved balance assertion plus the settled movements recorded after it.
    Two concurrent reconciles that both computed their delta against the same
    posted state leave the two permanently apart, and this is the left-hand
    side of the assertion that catches it.

    **NOT scenario-scoped, and its one caller depends on that being harmless.**
    It sums every posting on the linked ledger across ALL scenarios, so the
    invariant "linked ledger total == resolved assertion" holds only while the
    database has a single scenario -- which is true today (production creates
    baselines only) and stops being true the moment scenario clone ships.  Said
    here rather than discovered later, because the failure would look like a
    concurrency regression and would not be one.

    Args:
        account_id: The account whose linked ledger to total.

    Returns:
        The sum of every posting on that ledger account as a
        :class:`~decimal.Decimal` (``Decimal("0.00")`` when nothing is posted).

    Raises:
        PostingError: When the account has no linked ledger account.
    """
    from app.extensions import db  # pylint: disable=import-outside-toplevel
    from app.models.journal_entry import (  # pylint: disable=import-outside-toplevel
        Posting,
    )
    from app.services.posting_reads import (  # pylint: disable=import-outside-toplevel
        _ledger_account_for,
    )

    # ``_ledger_account_for`` RAISES ``PostingError`` for an unpaired account;
    # it never returns ``None``.  An earlier version of this helper guarded for
    # ``None`` and returned ``$0.00``, which was dead code that would also have
    # reported "nothing posted" for a broken chart of accounts.
    linked = _ledger_account_for(account_id)
    rows = (
        db.session.query(Posting.amount)
        .filter(Posting.ledger_account_id == linked.id)
        .all()
    )
    return sum((amount for (amount,) in rows), Decimal("0.00"))


# --- The tax law a test prices under (plan step salary:X-at-1) --------------
#
# The law lives once, in :mod:`app.tax_law`, and every test prices under it
# unless the test installs another through the ``tax_law`` fixture in
# ``tests/conftest.py`` (ruling salary:R-SAL80, "Real law, tests may swap").
# Until X-at-1 a test wrote its law as per-user tax ROWS through three seeders
# here; the builders below make the same figures as a law, so a test that
# seeded those rows installs exactly what it priced before and no expected
# figure moves.  A law must pass :mod:`app.tax_law._types`' checks, which the
# rows never had to, and each builder's docstring says where that shaped it.

#: A made-up law's one source line: a test's figures cite no document.
MADE_UP_SOURCES = ("A test's made-up figures, not a published law",)

#: No year at all: every kind resolves no rules.  It lists no state either,
#: so since plan step salary:X-at-3 it prices no PROFILE -- the engine refuses
#: a state the law does not list (ruling salary:R-SAL78) -- and is what the
#: tax-law alarms' tests judge.  A test pricing a profile with no tax installs
#: :data:`NO_TAX_LAW`.
EMPTY_TAX_LAW = TaxLaw(years=())


def zero_federal():
    """Return federal rules that price $0.00 at every income, with no credits.

    What a made-up year states for its federal rules when the test's rows held
    no bracket set: a year cannot lack federal rules, and a single 0% rung
    prices what no row priced on a paycheck.  The two differ in two places no
    test installing this reaches: the withholding adds the W-4's extra
    withholding to a bracket figure, and the annual liability REFUSES a missing
    bracket set where it prices these rules at $0.00.

    Returns:
        FederalRules: Zero deduction, zero credits, one 0% rung from $0.
    """
    return FederalRules(
        standard_deduction=Decimal("0.00"),
        child_credit_amount=Decimal("0.00"),
        other_dependent_credit_amount=Decimal("0.00"),
        child_credit_refundable_cap=Decimal("0.00"),
        brackets=ladder(("0.00", None, "0.0000")),
    )


def made_up_federal():
    """Return the made-up federal rules ``seed_tax_bracket_set`` wrote as rows.

    The same deduction, credits and two rungs.  The rows' ladder STOPPED at
    $47,150, which a law refuses (income above a closed top would be taxed by
    nothing), so a 0% open rung carries it on: the tax on every income is the
    rows' tax, and only :func:`app.services.tax_calculator.marginal_rate_for`
    above $47,150 reads 0% where the rows read 12%.  The refundable credit cap
    is the column default the rows took, $0.00.

    Returns:
        FederalRules: The made-up single filer's rules.
    """
    return FederalRules(
        standard_deduction=Decimal("14600.00"),
        child_credit_amount=Decimal("2000.00"),
        other_dependent_credit_amount=Decimal("500.00"),
        child_credit_refundable_cap=Decimal("0.00"),
        brackets=ladder(
            ("0.00", "11600.00", "0.1000"),
            ("11600.00", "47150.00", "0.1200"),
            ("47150.00", None, "0.0000"),
        ),
    )


def zero_fica():
    """Return FICA rules that price $0.00 Social Security and Medicare.

    What a made-up year states when the test's rows held no FICA row: a year
    cannot lack FICA, and zero rates price what no row priced on a paycheck.
    The wage base and threshold must be above zero, so they carry
    ``made_up_fica``'s -- which caps the Taxes tab's W-2 preview of Social
    Security wages at that base where no row left it uncapped; no test
    installing this renders that preview.

    Returns:
        FicaRules: Every rate 0.
    """
    return FicaRules(
        ss_rate=Decimal("0.0000"),
        ss_wage_base=Decimal("176100.00"),
        medicare_rate=Decimal("0.0000"),
        medicare_surtax_rate=Decimal("0.0000"),
        medicare_surtax_threshold=Decimal("200000.00"),
    )


def made_up_fica():
    """Return the made-up FICA rules ``seed_fica_config`` wrote as a row.

    Returns:
        FicaRules: 6.2% on a $176,100 base, 1.45%, 0.9% over $200,000.
    """
    return FicaRules(
        ss_rate=Decimal("0.0620"),
        ss_wage_base=Decimal("176100.00"),
        medicare_rate=Decimal("0.0145"),
        medicare_surtax_rate=Decimal("0.0090"),
        medicare_surtax_threshold=Decimal("200000.00"),
    )


def made_up_state(rate, standard_deduction=Decimal("25500.00")):
    """Return a made-up flat-rate state, as ``seed_state_tax_config`` wrote it.

    The row carried ONE filing status (single); a law states every status, so
    each carries the row's deduction.  The row held no child deduction tiers.

    Args:
        rate: The flat rate as a fraction (``Decimal("0.0399")``).
        standard_deduction: Every status's standard deduction.

    Returns:
        StateYearLaw: The made-up state.
    """
    return StateYearLaw(
        tax_type=TaxTypeEnum.FLAT,
        flat_rate=rate,
        standard_deduction={status: standard_deduction for status in FilingStatusEnum},
        child_deduction_tiers={status: () for status in FilingStatusEnum},
    )


def no_income_tax_state():
    """Return a state with no income tax: the explicit ``$0.00`` entry (ruling salary:R-SAL78).

    Tax type ``NONE``, no rate, a ``$0.00`` standard deduction and no child
    deduction for every filing status.  Since plan step salary:X-at-3 the law
    must LIST a profile's state or the engine refuses the profile, so a test
    whose figures carry no state tax lists the profile's state this way rather
    than leaving it out.

    Returns:
        StateYearLaw: The made-up untaxed state.
    """
    return StateYearLaw(
        tax_type=TaxTypeEnum.NONE,
        flat_rate=None,
        standard_deduction={status: Decimal("0.00") for status in FilingStatusEnum},
        child_deduction_tiers={status: () for status in FilingStatusEnum},
    )


def made_up_year(tax_year=2026, *, federal=None, fica=None, states=None):
    """Return one made-up tax year, one set of federal rules for every status.

    The rows these years replace carried ONE status (single); every status
    carries the same rules here, which a single-filer test cannot see.

    Args:
        tax_year: The year.
        federal: The federal rules every filing status carries
            (default :func:`zero_federal`).
        fica: The year's FICA rules (default :func:`zero_fica`).
        states: ``{state_code: StateYearLaw}`` (default none).

    Returns:
        TaxYearLaw: The made-up year.
    """
    rules = federal if federal is not None else zero_federal()
    return TaxYearLaw(
        tax_year=tax_year,
        sources=MADE_UP_SOURCES,
        federal={status: rules for status in FilingStatusEnum},
        fica=fica if fica is not None else zero_fica(),
        states=states if states is not None else {},
    )


def bracket_set_law(*tax_years):
    """Return the law ``seed_tax_bracket_set`` wrote, once per year asked for.

    The rows held a bracket set and nothing else, so each year carries
    :func:`made_up_federal` for every status, zero FICA and no state.

    Args:
        *tax_years: The years the rows were written for (default 2026 alone).

    Returns:
        TaxLaw: Those years, oldest first.
    """
    return TaxLaw(years=tuple(
        made_up_year(year, federal=made_up_federal())
        for year in sorted(tax_years or (2026,))
    ))


#: No tax on any line, and North Carolina listed: one made-up year whose
#: federal rules and FICA price $0.00 (:func:`zero_federal`,
#: :func:`zero_fica`) and whose one state has no income tax
#: (:func:`no_income_tax_state`).  What a test pricing a profile with no tax
#: installs since plan step salary:X-at-3, where it installed
#: :data:`EMPTY_TAX_LAW` until the engine began refusing a state the law does
#: not list.  It prices every paycheck line at $0.00 as the empty law did,
#: with the two differences :func:`zero_federal` names -- a W-4's extra
#: withholding is added to federal, and the annual liability prices $0.00
#: where it refused -- and a third: the tax-law banner, which the empty law
#: shows on every page, shows under this one only from November 1, 2026,
#: when 2027 falls due.  It also lists NC on the Settings tax-law card where
#: the empty law listed nothing.  None of the tests that moved to it asserts
#: on any of these (each passed unchanged on it, 2026-10-08).
NO_TAX_LAW = TaxLaw(years=(made_up_year(2026, states={"NC": no_income_tax_state()}),))


def fica_only_law(tax_year=2026):
    """Return the law ``seed_fica_config`` alone wrote: made-up FICA, and NC untaxed.

    Federal prices $0.00 on the zero rules a year cannot lack.  The row held
    no state, which priced no state tax; since plan step salary:X-at-3 the
    engine refuses a state the law does not list (ruling salary:R-SAL78), so
    North Carolina -- every caller's profile's state -- is listed with no
    income tax (:func:`no_income_tax_state`), which prices the same $0.00.

    Args:
        tax_year: The one year the row was written for.

    Returns:
        TaxLaw: That one year.
    """
    return TaxLaw(years=(
        made_up_year(
            tax_year, fica=made_up_fica(), states={"NC": no_income_tax_state()},
        ),
    ))


def state_and_fica_law(rate=Decimal("0.0399"), tax_year=2026):
    """Return the law the ``seed_state_tax_config`` + ``seed_fica_config`` pair wrote.

    The commonest setup the rows had: a North Carolina flat rate and the
    made-up FICA, and no bracket set, so federal prices $0.00.

    Args:
        rate: The North Carolina flat rate.
        tax_year: The one year the rows were written for.

    Returns:
        TaxLaw: That one year.
    """
    return TaxLaw(years=(
        made_up_year(
            tax_year, fica=made_up_fica(), states={"NC": made_up_state(rate)},
        ),
    ))


def made_up_law(*years):
    """Return a law of consecutive made-up years, each ``(tax_year, {state: rate})``.

    The tax-law alarms' tests (plan step salary:X-at-4) vary WHICH years and
    states the law lists, never the figures, so each year states
    :func:`made_up_year`'s defaults (:func:`zero_federal` and
    :func:`zero_fica`) and a made-up flat rate per state it lists (``{}`` for
    a year that lists none).

    Args:
        *years: ``(tax_year, {state_code: rate_string})`` per year, oldest
            first and consecutive (:class:`~app.tax_law.TaxLaw` refuses a
            skipped year).

    Returns:
        TaxLaw: Those years.
    """
    return TaxLaw(years=tuple(
        made_up_year(tax_year, states={
            state: made_up_state(Decimal(rate)) for state, rate in states.items()
        })
        for tax_year, states in years
    ))


# ── Recurrence cadence payloads (plan step R7b-2) ─────────────────
#
# The two AXES a recurrence form authors, in one place.  Before R7b-2 a test
# wrote ``{"recurrence_pattern": str(monthly.id)}`` and every suite carried its
# own spelling of that; the form now states a unit, an interval and a
# placement, and plan step R7c changes the wire shape again when the authored
# columns land.  One producer is one edit then -- and it is also what stops a
# test authoring a cadence the application cannot store, because it states the
# axes and lets the encoder choose the pattern, exactly as the form does.
#
# TWO shapes, because the cadence crosses two boundaries and looks different at
# each.  A BROWSER posts ``ref`` ids spelled as strings; the Marshmallow field
# (``_helpers.RecurrenceUnitField`` / ``PeriodPlacementField``) deserializes
# each to its enum MEMBER, and that is what the route helpers in
# ``app.routes._recurrence_form_helpers`` receive.  A test that drives a route
# through the client wants the first; one that calls a helper directly wants
# the second, and handing it strings would test a payload no schema produces.
# :func:`cadence_payload` is built from :func:`validated_cadence` so the key
# names and the defaults are stated once for both.


def validated_cadence(
    unit=None, interval_n=1, placement=None, starts_on=None, nominal_day=None,
    states_a_start=True, max_per_month=None,
):
    """Return one cadence as a SCHEMA hands it to a route helper.

    The post-``load()`` shape: enum members, a real ``int`` and a real
    ``date``, which is what
    :func:`app.routes._recurrence_form_helpers.recurrence_spec_from_form`
    and its siblings read.

    **``starts_on`` rides WITH the cadence since plan step R7c-b**, and it is
    not optional garnish: the rule's first occurrence is authored (ruling
    R-R16), ``budget.recurrence_rules.starts_on`` is ``NOT NULL``, and
    ``RecurrenceFormFieldsMixin.validate_recurrence_states_a_start`` refuses a
    submission that names a cadence without one.  A helper that produced the
    cadence alone would produce a payload no form can post.

    Args:
        unit: A :class:`~app.enums.RecurrenceUnitEnum` member.  Defaults to
            ``PERIOD``, the every-paycheck cadence most fixtures want.
        interval_n: How many units pass between occurrences.
        placement: A :class:`~app.enums.PeriodPlacementEnum` member.  Defaults
            to ``CONTAINING_DATE``, the placement every unit offers.
        starts_on: The rule's FIRST OCCURRENCE.  Defaults to whatever a create
            form opens on, taken from that form's own producer rather than
            restated here, so a fixture starts where a user would.
        nominal_day: The day the rule MEANS when *starts_on*'s month was too
            short to hold it (ruling R-R3), and ``None`` -- the ordinary case
            -- when the date holds the day.  Omitted from the payload entirely
            when ``None``, because the control that posts it renders only where
            the chosen date leaves the question open.
        states_a_start: ``False`` leaves ``starts_on`` OUT of the payload
            entirely, which is a different request from stating a date and is
            what a locked control produces -- a loan payment's, whose bound the
            app derives, renders ``disabled`` and so posts nothing.  An UPDATE
            reads the absence as "leave the stored one alone"; a CREATE refuses
            it.  Absence and presence being distinguishable is the whole point
            of the ruling of 2026-08-15, so a helper that could only produce
            one of them could not exercise it.
        max_per_month: The per-month ceiling (plan step salary:R15-a), or
            ``None`` to leave the key out -- what a DISABLED control posts.

    Returns:
        A dict of deserialized payload values, ready to splat into the ``data``
        a helper is called with.
    """
    # Imported inside the function rather than at module scope: this helpers
    # module is imported by tests that run before the Flask app, and therefore
    # the ref cache, exists.
    # Pylint: ``import-outside-toplevel`` is the point, not an oversight.
    from app.enums import (  # pylint: disable=import-outside-toplevel
        PeriodPlacementEnum,
        RecurrenceUnitEnum,
    )
    from app.routes._recurrence_form_render import (  # pylint: disable=import-outside-toplevel
        create_form_default_starts_on,
    )

    payload = {
        "recurrence_unit": (
            unit if unit is not None else RecurrenceUnitEnum.PERIOD
        ),
        "interval_n": interval_n,
        "recurrence_placement": (
            placement if placement is not None
            else PeriodPlacementEnum.CONTAINING_DATE
        ),
    }
    if states_a_start:
        payload["starts_on"] = (
            starts_on if starts_on is not None
            else create_form_default_starts_on()
        )
    if nominal_day is not None:
        payload["nominal_day"] = nominal_day
    if max_per_month is not None:
        payload["max_per_month"] = max_per_month
    return payload


def end_bound_payload(bound=None):
    """Return the form keys that state one closing bound, as a BROWSER posts them.

    The bound half of :func:`cadence_payload`, for plan step R7b-3's "Ends"
    control.  The control is a mode ``<select>`` and two value inputs of which
    exactly one is ever ENABLED, so a real submission carries the mode plus at
    most one value -- and that is what this produces.  A helper that always
    sent both would exercise a payload the form cannot make.

    Args:
        bound: An :class:`~app.services.recurrence.EndBound`.  Defaults to the
            unbounded shape, which is what a create form starts on.

    Returns:
        A dict of form values -- strings, as an HTML form submits them.
    """
    # Pylint: ``import-outside-toplevel`` -- see :func:`validated_cadence`.
    from app.services.recurrence import (  # pylint: disable=import-outside-toplevel
        NEVER_ENDS,
    )

    stated = NEVER_ENDS if bound is None else bound
    payload = {"recurrence_end_mode": stated.token}
    if stated.end_date is not None:
        payload["end_date"] = stated.end_date.isoformat()
    if stated.max_occurrences is not None:
        payload["max_occurrences"] = str(stated.max_occurrences)
    return payload


def cadence_payload(
    unit=None, interval_n=1, placement=None, starts_on=None, nominal_day=None,
    states_a_start=True, max_per_month=None,
):
    """Return the form keys that author one cadence, as a BROWSER posts them.

    :func:`validated_cadence`'s wire form: each enum member resolved to its
    ``ref`` row id, every value a string.  Derived from that function rather
    than written beside it, so the key names and the defaults have one
    statement and plan step R7c moved both together.

    ``starts_on`` goes out ISO-formatted, which is what ``<input type="date">``
    posts and what ``fields.Date`` reads.

    Args:
        unit: A :class:`~app.enums.RecurrenceUnitEnum` member.  Defaults to
            ``PERIOD``.
        interval_n: How many units pass between occurrences.
        placement: A :class:`~app.enums.PeriodPlacementEnum` member.  Defaults
            to ``CONTAINING_DATE``.
        starts_on: The rule's first occurrence; see :func:`validated_cadence`.
        nominal_day: The clamped day the rule means; see
            :func:`validated_cadence`.  Absent from the returned dict when
            ``None``, matching the control's own conditional rendering.
        states_a_start: ``False`` omits ``starts_on``, which is what a locked
            (``disabled``) control posts; see :func:`validated_cadence`.
        max_per_month: The per-month ceiling (plan step salary:R15-a).  Absent
            from the returned dict when ``None``, which is what the control
            posts DISABLED beside a calendar-month unit; a test modelling an
            enabled, empty box adds ``"max_per_month": ""`` itself.

    Returns:
        A dict of form values -- strings, as an HTML form submits them -- ready
        to splat into a POST payload.
    """
    # Pylint: ``import-outside-toplevel`` -- see :func:`validated_cadence`.
    from app import ref_cache  # pylint: disable=import-outside-toplevel

    loaded = validated_cadence(
        unit, interval_n, placement, starts_on, nominal_day, states_a_start,
        max_per_month,
    )
    payload = {
        "recurrence_unit": str(
            ref_cache.recurrence_unit_id(loaded["recurrence_unit"]),
        ),
        "interval_n": str(loaded["interval_n"]),
        "recurrence_placement": str(
            ref_cache.period_placement_id(loaded["recurrence_placement"]),
        ),
    }
    if "starts_on" in loaded:
        payload["starts_on"] = loaded["starts_on"].isoformat()
    if "nominal_day" in loaded:
        payload["nominal_day"] = str(loaded["nominal_day"])
    if "max_per_month" in loaded:
        payload["max_per_month"] = str(loaded["max_per_month"])
    return payload


def payroll_basis(profile, periods, cadence_days=14, history_opens_on=None):
    """Return the :class:`PayrollBasis` for *profile* over *periods*.

    **The ONE test-side door onto the paycheck engine's owner input**, added at
    plan step **R-F16** when ``salary.salary_profiles.pay_periods_per_year``
    was dropped and the count became a derivation off
    ``budget.pay_schedule.cadence_days``.  One shared helper rather than a
    ``for_test`` constructor on the production type or a defaulted ``cadence=``
    argument on the engine, which is the ruling ledger row **P54** set for the
    same question about ``BalanceContext``: a production API with only test
    callers is the speculative shape ``CLAUDE.md`` rule 13 forbids, and a
    defaulted cadence would let the engine assume biweekly for an owner who is
    not.

    The default matches the suite's fixtures and
    ``BaseConfig.DEFAULT_PAY_CADENCE_DAYS``, so a test that does not care about
    the rhythm says nothing and gets 26 paychecks a year.  A test that DOES
    care states its cadence and gets the count that follows -- 7 gives 52, 15
    gives 24, 365 gives 1.

    **It takes the owner's PERIODS since plan step balance:X-bh-1**, because
    the basis now carries the whole
    :class:`~app.services.pay_calendar.PayCalendar` rather than a bare cadence:
    the engine's four calendar judgements -- third-paycheck detection, the
    first-paycheck-of-month deduction cadence, the FICA wage-base cumulative
    and a deduction's annual cap -- count paydays off it, where they used to
    read an ``all_periods`` argument a caller supplied beside the basis.  A
    case passes here exactly what it used to pass there.

    **It is REQUIRED and has no default, which an adversarial review of this
    step is why.**  A defaulted empty *periods* builds a paydayless calendar
    that still answers the paycheck COUNT from *cadence_days*, so a case
    reading only a gross goes on passing -- while every month and year count
    over it is ZERO, which INVERTS two arms: a 12-per-year deduction stops
    applying where the old empty ``all_periods`` list made it apply
    vacuously, and a 24-per-year one starts applying on a third paycheck.
    Five oracles were measured still taking the default, agreeing with their
    producers only because no fixture behind them carries a deduction or
    reaches the wage base.  An argument a caller can silently omit is the
    same defect this step removed from the engine, one layer up.

    Args:
        profile: The ``SalaryProfile`` (or a duck-typed stand-in) to price.
            Its ``user_id`` owns the calendar built here, because a rule is
            refused against another owner's schedule
            (``_require_owner`` in ``app/services/recurrence/_resolution.py``),
            so a stand-in carries one too.
        periods: The owner's WHOLE schedule as
            :class:`~app.services.pay_calendar.DerivedPeriod` values, in any
            order.  Only their paydays are read; the calendar re-derives every
            end and ordinal, so a case may hand over the same values it prices.
        cadence_days: Days between the owner's paydays, 1..365.
        history_opens_on: How far back this owner's paychecks reach, or
            ``None`` -- **the DEFAULT, and it is the column's own** (plan step
            **balance:X-bh-2**).  ``budget.pay_schedule.history_opens_on`` is
            nullable and null means NOT STATED since ruling
            **balance:R-IA**'s 2026-08-31 amendment, so a case that says
            nothing here gets what an owner nobody asked gets: the engine
            counts *periods* and projects nothing below them.  **That makes
            the default the SAFE one** -- forgetting it cannot silently invent
            paychecks, which is the direction a defaulted argument should fail
            in.  A case about the BACKWARD rhythm states a day here, and a
            case about an owner who genuinely started at the record's opening
            states that opening.

    Returns:
        The :class:`~app.services.payroll_basis.PayrollBasis`.

    Raises:
        PayCalendarError: *cadence_days* falls outside 1..365, which is what
            ``ck_pay_schedule_cadence_range`` refuses in the database, or two
            of *periods* share a payday.
    """
    from app.services.payroll_basis import (  # pylint: disable=import-outside-toplevel
        PayrollBasis,
    )

    return PayrollBasis(profile, derived_calendar(
        [period.start_date for period in periods], cadence_days=cadence_days,
        user_id=profile.user_id, history_opens_on=history_opens_on,
    ))


def pricing_over(calendar):
    """Return a :class:`~app.services.income_service.PaycheckPricing` over *calendar*.

    **The test-side door for a calendar already in hand**, and ONE copy of it
    (ruling P54's shape: a production API with only test callers is the
    speculative shape ``CLAUDE.md`` rule 13 forbids, so the production
    constructor that took a calendar was moved here at plan step salary:C12,
    when its last production caller went).  Production holds two doors and
    both take a SOURCE -- the read pass hands its calendar memo, a pass-less
    producer hands ``calendar_for`` bound to the owner -- so a test that has
    derived a calendar for itself hands it back as the source, the owner read
    off the same value.  Every property the pricer has in production holds
    here: nothing is derived at construction, and the source answers once.

    Args:
        calendar: The owner's :class:`~app.services.pay_calendar.PayCalendar`.

    Returns:
        The empty pricer.
    """
    # Pylint: ``import-outside-toplevel`` -- this module imports no app or ORM
    # symbols at top level (its collection-time-safety convention).
    # pylint: disable=import-outside-toplevel
    from app.services.income_service import paycheck_pricing

    return paycheck_pricing(calendar.user_id, lambda: calendar)


def derived_calendar(
    paydays, cadence_days=14, user_id=1, history_opens_on=None,
):
    """Return a :class:`PayCalendar` over *paydays*, derived rather than built.

    The calendar-shaped sibling of :func:`derived_window`, for a producer that
    takes the whole calendar rather than a slice of it -- which
    :meth:`app.services.income_service.SalaryPricing._breakdown_by_period` does,
    because the paycheck engine needs the owner's paycheck COUNT as well as
    their periods and both must come off one derivation (plan step **R-F16**).

    *This sentence named ``recurrence_engine._amounts`` until plan step
    balance:X-au-d, and a mechanical rename left it naming
    ``_generated_amount_ownership``, which takes a template and reads no
    calendar at all.  The requirement did not go away when generation stopped
    pricing -- it MOVED, to the derivation above.  Caught by an adversarial
    review of that step.*

    Args:
        paydays: The paydays opening each period, in any order.
        cadence_days: Days between paydays, 1..365.
        user_id: The owner the calendar belongs to.
        history_opens_on: How far back the owner's paychecks reach, or ``None``
            -- the default, which is the nullable column's own; see
            :func:`payroll_basis` for what saying nothing means.

    Returns:
        The :class:`~app.services.pay_calendar.PayCalendar`.
    """
    from app.services.pay_calendar import (  # pylint: disable=import-outside-toplevel
        PayCalendar,
    )

    pairs = [(index + 1, payday) for index, payday in enumerate(sorted(paydays))]
    return PayCalendar.from_paydays(
        pairs,
        eras_of(pairs, cadence_days),
        user_id=user_id,
        history_opens_on=history_opens_on,
    )


def derived_window(paydays, cadence_days):
    """Return a :class:`PeriodWindow` over *paydays*, derived rather than built.

    The PURE test-side door onto the pay calendar, added at plan step **C2-e**,
    when ``growth_engine`` stopped accepting a hand-built list of period-shaped
    objects.  :func:`period_window` above is its database-backed sibling: use
    that one when the test has real ``budget.pay_periods`` rows, and this one
    for the unit tests that have no database at all.

    **It derives; it does not assemble.**  The window comes out of a real
    :class:`~app.services.pay_calendar.PayCalendar`, so its ends are the ones
    the derivation computes (each period ends the day before the next payday,
    and the last the day before the next payday of a grid phased on the
    FIRST payday at *cadence_days* -- :func:`eras_of`), its ordinals run in
    payday order, and its periods TILE.  A test therefore cannot hand the
    growth engine a shape production could not produce -- a gap between two
    periods, an ordinal out of date order, an end below its own start -- which
    is the whole reason the engine's parameter is a window rather than a list.

    Args:
        paydays: The paydays opening each period, in any order.  A test that
            wants a plain biweekly run passes ``[d, d + 14, d + 28, ...]``.
        cadence_days: Days between paydays, 1..365.  It sets the LAST period's
            end and nothing else, so a run of evenly spaced paydays should
            pass its own spacing; off it, the last end lands on the grid
            rather than one cadence past the last payday.

    Returns:
        The :class:`~app.services.pay_calendar.PeriodWindow` over every derived
        period, ``start_date`` ascending.

    Raises:
        PayCalendarError: Anything the derivation refuses -- a duplicate
            payday, a cadence outside 1..365, a payday that is not a plain
            ``date``.
    """
    from app.services.pay_calendar import (  # pylint: disable=import-outside-toplevel
        PayCalendar,
        PeriodWindow,
    )

    pairs = [
        (index + 1, payday) for index, payday in enumerate(sorted(paydays))
    ]
    calendar = PayCalendar.from_paydays(
        pairs,
        eras_of(pairs, cadence_days),
        user_id=1,
        history_opens_on=None,
    )
    return PeriodWindow(periods=calendar.periods)


def biweekly_window(first_payday, count):
    """Return a :class:`PeriodWindow` of *count* 14-day periods from *first_payday*.

    The shorthand for the commonest shape in the unit tests -- an evenly
    spaced biweekly run -- over :func:`derived_window`, which it defers every
    guarantee to.

    Args:
        first_payday: The payday opening the first period.
        count: How many periods to derive.

    Returns:
        The :class:`~app.services.pay_calendar.PeriodWindow`.
    """
    from datetime import timedelta  # pylint: disable=import-outside-toplevel

    return derived_window(
        [first_payday + timedelta(days=14 * step) for step in range(count)],
        14,
    )


def window_head(window, count):
    """Return the first *count* periods of *window* as a window of their own.

    The test-side stand-in for slicing, which
    :meth:`~app.services.pay_calendar.PeriodWindow.__getitem__` REFUSES (plan
    step C2-e): no consumer in ``app/`` slices a window, and the branch that
    once allowed it returned ``window[::-1]`` silently re-sorted into payday
    order rather than reversed.  A leading run of a tiling tiles, so this
    cannot build a window the type would refuse -- the constructor still
    checks.

    Args:
        window: The :class:`~app.services.pay_calendar.PeriodWindow` to take
            from.
        count: How many periods to keep, from the window's own start.

    Returns:
        The leading :class:`~app.services.pay_calendar.PeriodWindow`.
    """
    from app.services.pay_calendar import (  # pylint: disable=import-outside-toplevel
        PeriodWindow,
    )

    return PeriodWindow(periods=window.periods[:count])


def period_window(periods):
    """Return the :class:`PeriodWindow` the seam reports over for *periods*.

    The test-side door onto the pay calendar, added at plan step **C2-c**,
    when the balance seam stopped taking a list of ORM ``PayPeriod`` rows and
    started reading its reporting domain off the pay calendar
    (``app.services.balance_at.BalanceContext.reported_periods``).  A handful
    of seam producers still take a window explicitly -- the ones that take a
    ``scenario_id`` and an ``as_of`` rather than a context -- and this is how a
    test names the SUBSET of an owner's schedule it wants those to report.

    It derives the owner's whole calendar and then selects the requested
    periods out of it, rather than building period bounds from the ORM rows:
    a window carries the ends the WHOLE calendar computed, which is the
    property ledger row **P14** is about, and taking the ends off the stored
    columns here would let a test pass against bounds production no longer
    reads.

    Args:
        periods: The ``PayPeriod`` rows to report, in any order and all
            belonging to one user.  Must be non-empty -- an empty request has
            no owner to resolve a calendar for, and the seam entries take
            ``PeriodWindow(periods=())`` directly for that case.

    Returns:
        The :class:`~app.services.pay_calendar.PeriodWindow` over exactly those
        periods.

    Raises:
        PayCalendarError: The requested periods do not form an unbroken span,
            which the window type refuses (plan finding **P32**).  That is a
            real answer, not a helper limitation: a gapped column set renders
            a balance row that does not add up.
    """
    from app.services.pay_calendar import (  # pylint: disable=import-outside-toplevel
        PeriodWindow,
        calendar_for,
    )

    wanted = {period.id for period in periods}
    calendar = calendar_for(next(iter(periods)).user_id)
    return PeriodWindow(
        periods=tuple(
            period for period in calendar.saved()
            if period.period_id in wanted
        ),
    )


def dashboard_section(user_id, as_of=None):
    """Return what ``dashboard.page`` resolves before it calls a producer.

    The test-side door onto the budget dashboard's producers, added at
    pay-calendar plan step **C2-f2e**, when
    ``dashboard_service.compute_pulse_section`` and
    ``compute_balance_section`` stopped taking a ``user_id`` and started taking
    a :class:`~app.services.dashboard_service.DashboardSection` -- the account,
    the settings and the read pass, resolved ONCE by the route.

    It performs the route's two steps and nothing else, deliberately: a helper
    that CONSTRUCTED a section from parts would let a test exercise a
    combination the route cannot produce (an account of one owner beside
    another's pass), which is exactly the "test infrastructure that bypasses
    the production door" shape ``docs/plans/lessons.md`` records.

    Args:
        user_id: The owner whose dashboard to resolve.
        as_of: The read pass's pinned day.  Defaults to the pass's own default
            (``date.today()``); supply one to pin a render to a day the test
            controls, which is the same knob every other calendar-sensitive
            fixture uses.

    Returns:
        The :class:`~app.services.dashboard_service.DashboardSection`, or
        ``None`` when the owner has no resolvable grid account -- the same
        ``None`` the route passes straight through to the producers.
    """
    # pylint: disable=import-outside-toplevel  -- same circular-dependency
    # avoidance as the factories above.
    from app.services import dashboard_service
    from app.services.balance_at import BalanceContext

    return dashboard_service.resolve_section(
        BalanceContext.build(user_id, as_of=as_of),
    )


def current_pay_period(user_id, as_of=None):
    """Return the ORM :class:`PayPeriod` row covering *as_of* for *user_id*.

    **The ONE place the suite asks "which paycheck is this owner in", and it
    asks the APPLICATION** (plan step C2-f3a).  It replaced
    ``pay_period_service.get_current_period``, which that step deleted after
    moving its three ``app/`` call sites onto
    :meth:`~app.services.pay_calendar.PayCalendar.period_containing` -- and the
    two defects it was deleted FOR are exactly the two a hand-rolled test
    helper would have reproduced.  Its ``.first()`` carried no ``ORDER BY``
    (ledger row **P19**), and it read the process clock rather than the
    owner's civil day (row **P49**).

    So this does not re-implement the search.  It runs the same derivation the
    application runs and then resolves the row, which is what keeps the suite
    from being able to disagree with the app about which period is current --
    a test that seeds state into "the current period" and a page that renders
    "the current period" must mean one period, or the assertion grades nothing.

    **It returns the ORM ROW deliberately**, where ``app/`` now holds
    :class:`~app.services.pay_calendar.DerivedPeriod` values.  A test needs a
    row because the factories take one (``make_investment_account``,
    ``create_loan_account``, every ``Transaction(pay_period_id=...)`` seed) and
    because ``tests/`` legitimately writes this table where ``app/`` may not
    (see ``pay_period_write``'s ``TestThereIsOneWriter``).  The identity comes
    from the derivation either way, so the row and the derived value name the
    same paycheck by construction rather than by two searches agreeing.

    Args:
        user_id: The owner whose schedule to search.
        as_of: The civil day to place.  Defaults to
            :func:`~app.utils.dates.display_today`, the owner's own day and
            the clock ``seed_periods_today`` builds its schedule around, so
            the default lands inside the seeded window rather than one UTC
            midnight past it.

    Returns:
        The covering :class:`~app.models.pay_period.PayPeriod`, or ``None``
        when no SAVED period covers *as_of* -- which is a real answer and the
        one three routes still branch on.
    """
    # pylint: disable=import-outside-toplevel  -- same circular-dependency
    # avoidance as the factories above.
    from app.extensions import db as _db
    from app.models.pay_period import PayPeriod as _PayPeriod
    from app.services.pay_calendar import calendar_for
    from app.utils.dates import display_today

    period = calendar_for(user_id).period_containing(
        display_today() if as_of is None else as_of,
    )
    if period is None:
        return None
    return _db.session.get(_PayPeriod, period.period_id)


def read_pass_over_paydays(
    paydays, cadence_days, as_of, user_id=1, history_opens_on=None,
):
    """Return a :class:`BalanceContext` whose pay calendar is *paydays*.

    **The ONE place a test seeds the read pass's pay-calendar memo**, and that
    is the whole point of it existing (plan finding **P54**, ruled by the
    developer 2026-08-16).

    :class:`~app.services.balance_at.BalanceContext` derives the owner's
    calendar lazily into a field its own module docstring declares PRIVATE,
    because that module owns the derivation.  A unit test with no database
    cannot let it derive -- ``calendar_for`` would query -- so it must seed the
    memo, which means naming that private field.  Three sites wanted to by plan
    step ``C2-f2d``, and N sites reaching into one private field is how a memo
    becomes a de-facto public seam.

    **The two alternatives were weighed and refused.**  A named constructor on
    the seam (``BalanceContext.for_test(calendar=...)``) ships a production
    entry point whose only caller is this suite, which is ``CLAUDE.md`` rule
    13's speculative shape.  An optional ``calendar=`` on the real
    :meth:`~app.services.balance_at.BalanceContext.build` hands EVERY
    production caller a way to supply a calendar the module did not derive,
    with nothing checking it even belongs to that owner -- a new way to hold
    contradictory state, to solve a test-ergonomics problem.  Making the
    calendar EAGER on the pass was refused on measurement: deriving one can
    raise for an owner with no pay schedule, so every render would begin
    failing over a fact most of them never read.

    **Every type here is the real one** -- a real
    :class:`~app.services.pay_calendar.PayCalendar` derived from real paydays
    (:func:`derived_window`'s own discipline: the ends and ordinals are the
    derivation's, so a test cannot express a schedule production could not),
    and a real frozen :class:`BalanceContext`.  It cannot fail silently either:
    rename the memo field and the seed misses, the pass falls through to
    ``calendar_for``, and the query raises outside an app context.

    Args:
        paydays: The paydays opening each period, in any order.
        cadence_days: Days between paydays, 1..365.  It sets the LAST period's
            end and nothing else.
        as_of: The day this read pass is pinned to.
        user_id: The owning user (default ``1``).  The calendar is derived for
            the SAME id the pass carries, so the two cannot disagree -- which
            is the pairing the seeded memo could otherwise express.
        history_opens_on: How far back the owner's paychecks reach, or ``None``
            -- the default, which is the nullable column's own; see
            :func:`payroll_basis` for what saying nothing means.

    Returns:
        A :class:`~app.services.balance_at.BalanceContext` with no baseline
        scenario and its calendar memo pre-filled.  ``scenario`` is ``None``
        because a pure unit case has no database to resolve one from; a test
        that reaches a scenario-scoped seam entry will get that entry's own
        named refusal rather than a fake.

    Raises:
        PayCalendarError: Anything the derivation refuses -- a duplicate
            payday, a cadence outside 1..365, a payday that is not a plain
            ``date``.
    """
    from app.services.balance_at import (  # pylint: disable=import-outside-toplevel
        BalanceContext,
    )
    from app.services.pay_calendar import (  # pylint: disable=import-outside-toplevel
        PayCalendar,
    )

    pairs = [
        (index + 1, payday) for index, payday in enumerate(sorted(paydays))
    ]
    calendar = PayCalendar.from_paydays(
        pairs,
        eras_of(pairs, cadence_days),
        user_id=user_id,
        history_opens_on=history_opens_on,
    )
    return BalanceContext(
        user_id=user_id,
        scenario=None,
        as_of=as_of,
        _calendars={user_id: calendar},
    )


def read_pass(account, scenario, as_of):
    """Return a :class:`BalanceContext` for *account*'s OWNER, pinned at *as_of*.

    **The DB-backed twin of :func:`read_pass_over_paydays`**, and what a test
    holds where it used to build a bare
    :class:`~app.services.cash_ledger.AmountBasis` with :func:`basis_for` and
    hand it to the cash fold alongside an account and a date.  Plan step
    **X-i4** made that triple unstateable: the fold comes out of the pass
    (``assembled_fold(account, ctx)``), which memoizes it and refuses
    an account the pass does not own.

    **It takes the ACCOUNT rather than a user id, deliberately**, which is the
    same discipline :func:`read_pass_over_paydays` states for the calendar one
    tier over: the pass is built for the owner of the very account it is about
    to be asked for, so a test cannot express the mis-pairing production can no
    longer express either.  A test that WANTS the mismatch -- the ones grading
    :class:`~app.exceptions.ForeignAccountError` -- constructs the two halves by
    hand and says so at the call.

    It builds the context DIRECTLY rather than through
    :meth:`~app.services.balance_at.BalanceContext.build`, because the caller
    already holds the scenario its fixture seeded and ``build`` would re-resolve
    the owner's baseline from the database -- a second answer to a question the
    test has already answered, and one that would silently pick a different
    scenario for a fixture holding more than one.  It is therefore invisible to
    :func:`counting_read_passes`, exactly as ``read_pass_over_paydays`` is.

    Args:
        account: Any account of the owner the pass is for; ``user_id`` is read
            off it.
        scenario: The scenario whose rows the pass values.
        as_of: The reader's NOW -- ruling R-G's clamp floor, and the date each
            loan resolves at.  Required, because a fold pinned to the ambient
            clock is the fixture-depends-on-the-calendar shape
            ``.claude/rules/testing.md`` names.

    Returns:
        A real frozen :class:`~app.services.balance_at.BalanceContext` with
        empty memos.
    """
    from app.services.balance_at import (  # pylint: disable=import-outside-toplevel
        BalanceContext,
    )

    return BalanceContext(
        user_id=account.user_id, scenario=scenario, as_of=as_of,
    )


@contextmanager
def counting_calls(*targets):
    """Count calls to each ``(module path, attribute name)`` in *targets*.

    **The ONE instrument for "how many times did this render run that"**,
    shared by the architecture gate
    (``tests/test_arch/test_one_read_pass_per_render.py``) and the render
    harness (``tests/manual/verify_retirement_render.py``) -- the same sharing
    :func:`counting_read_passes` below states its own case for, and for the
    same reason: two copies of a measuring instrument is the shape where one is
    later taught something the other is not, and the one that was not keeps
    grading the old question.

    Patched on the OWNING module and on every ``app.services`` module that
    imported the name directly, because a producer that holds its own reference
    would otherwise go uncounted -- and an undercount reads exactly like the
    improvement it is supposed to be measuring.

    Args:
        targets: ``(module path, attribute name)`` pairs, e.g.
            ``("app.services.retirement_projection", "load_projection_batch")``.

    Yields:
        A ``{name: int}`` dict whose values are the counts so far; read it
        after the block exits.
    """
    # pylint: disable=import-outside-toplevel
    import importlib
    import sys

    counts = {name: 0 for _, name in targets}
    restore = []
    for module_path, name in targets:
        real = getattr(importlib.import_module(module_path), name)

        def counting(*args, _real=real, _name=name, **kwargs):
            counts[_name] += 1
            return _real(*args, **kwargs)

        for module in list(sys.modules.values()):
            if getattr(module, "__name__", "").startswith("app.") and (
                getattr(module, name, None) is real
            ):
                restore.append((module, name, real))
                setattr(module, name, counting)
    try:
        yield counts
    finally:
        for module, name, real in restore:
            setattr(module, name, real)


@contextmanager
def counting_read_passes():
    """Count every ``BalanceContext.build`` while the block runs.

    **The ONE instrument for "how many read passes did this open"**, shared by
    the architecture gate (``tests/test_arch/test_one_read_pass_per_render.py``)
    and the cutover harness (``tests/manual/verify_retirement_pass_cutover.py``)
    -- which is what plan step ``C2-f2d-1``'s adversarial code review asked for
    after both shipped their own copy of it. Two copies of a measuring
    instrument is the shape where one is later taught something the other is
    not, and the one that was not keeps grading the old question.

    Patched on the CLASS rather than on any module's imported name: several
    producers hold their own reference to ``BalanceContext``, so patching a
    single module's attribute would count some builds and miss others -- and a
    counter that undercounts reads exactly like a gate that passes.

    **It counts ``build``, not construction.** A direct
    ``BalanceContext(user_id=..., scenario=..., as_of=...)`` is invisible to it.
    Nothing in ``app/`` does that today, but :func:`read_pass_over_paydays`
    above does, so the path is live in this repository and a producer that took
    it would go uncounted.

    Yields:
        A ``{"n": int}`` dict whose ``n`` is the count so far; read it after
        the block exits.
    """
    # pylint: disable=import-outside-toplevel
    from app.services.balance_at import BalanceContext

    counter = {"n": 0}
    real = BalanceContext.build.__func__

    def counting(cls, user_id, as_of=None):
        counter["n"] += 1
        return real(cls, user_id, as_of)

    BalanceContext.build = classmethod(counting)
    try:
        yield counter
    finally:
        BalanceContext.build = classmethod(real)


class PlantedPricing:
    """A stand-in for one of the amount model's live DERIVATIONS.

    Plan step X-au-c2b made :class:`~app.services.cash_ledger.AmountBasis` hold
    the two derivations behind a row's live figure -- the owner's salary
    projection and the scenario's loan resolutions -- rather than the
    ``{transaction_id: Decimal}`` maps they used to produce.  Tests whose
    subject is a VALUATION rule ("when the basis says X, what does this
    reduction return") need to plant an answer without seeding a salary profile
    or a loan, and this is what they plant it with.

    It satisfies the seam: :meth:`net_for` is what
    ``income_service.salary_net_for`` asks.

    **It answered a LOAN half too until plan step X-au-g-2c-2**, and planting
    was keyed by TRANSACTION id because that half (``LoanPricing.live_cash``)
    took a row.  That method is deleted -- a transfer shadow was DERIVED from
    that step and had no stored figure for a read-time override to supersede,
    and plan step ``balance:X-bi-6-4d-2`` deleted the shadows -- so the seam is
    salary alone and the key is the pair the salary derivation is keyed on.
    Answering the old key would have been worse than failing: the loan half was
    asked FIRST, so every planted figure landed there, and a helper that kept
    answering by row id after the arm was deleted would have gone quietly inert.

    Use it ONLY where the derivation is an input to the rule under test.  A test
    OF the amount model builds a real basis (``amount_basis``) and seeds the
    profile, because a planted map cannot grade a derivation.
    """

    def __init__(self, overrides=None):
        """Plant ``{(template_id, pay_period_id): Decimal}`` as the answers.

        Args:
            overrides: The live figures to answer with, keyed the way the
                salary derivation is keyed, or ``None`` for a derivation that
                answers for nothing -- the common case, and what a row set with
                no salary row gets.
        """
        self._overrides = dict(overrides or {})

    def net_for(self, template_id, pay_period_id):
        """Return the planted net for one template and period, or ``None``.

        Args:
            template_id: The row's definition.
            pay_period_id: The row's pay period.

        Returns:
            The planted ``Decimal``, or ``None`` when nothing was planted for
            that pair.
        """
        return self._overrides.get((template_id, pay_period_id))

    def derive_cash(self, due_date, period_start, loan_account_id, extra):
        """REFUSE: a planted basis cannot price a loan.

        :func:`planted_basis` puts this same object in ``AmountBasis.loans``,
        where the amount model's rule 4 calls ``derive_cash``.  Without this the
        call is an ``AttributeError`` on a class that simply has no such method
        -- a failure that names the DOUBLE rather than the mistake, and one an
        adversarial review of plan step X-au-g-2c-2 asked to be made legible.

        The class docstring's "use it ONLY where the derivation is an input to
        the rule under test" was the whole of the guarantee before this; a
        promise a test can break silently is not one.

        **The signature took a payment SHADOW until plan step X-au-f-2**, where
        ruling **R-BAL10** re-typed the real producer onto the two dating VALUES
        so a parent TRANSFER could answer it.  A double whose signature has
        drifted from its subject stops standing in for it, so this mirrors the
        real one exactly.

        Args:
            due_date: Ignored.
            period_start: Ignored.
            loan_account_id: Ignored.
            extra: Ignored.

        Raises:
            AssertionError: Always.
        """
        raise AssertionError(
            "A planted basis cannot price a loan payment: PlantedPricing "
            "stands in for the SALARY derivation only. This transfer reached "
            "AmountRule.LOAN_PAYMENT, so the case needs a real basis "
            "(amount_basis) over a seeded loan -- a planted map cannot grade "
            "a derivation."
        )


def planted_basis(*rows, overrides=None):
    """An :class:`~app.services.cash_ledger.AmountBasis` answering *overrides*.

    The basis a producer would hand a valuation rule, with the live salary
    derivation planted (:class:`PlantedPricing`) so no test of a reduction needs
    a salary profile to reach the override seam.  The ``loans`` field is planted
    with the same object, which answers nothing there: since plan step
    X-au-g-2c-2 the only thing a caller asks it is ``derive_cash``, and a
    reduction under test never does.

    Args:
        rows: The rows this basis will price.  A basis was built OVER a row set
            until plan step X-au-c2b, and every call site named its rows; they
            are kept because the first row still decides one thing -- the
            SCENARIO the basis declares.  ``resolve_transaction_amount`` refuses
            a row from another scenario, so a double that named the wrong one
            would grade that refusal instead of the rule under test.
        overrides: ``{(template_id, pay_period_id): Decimal}`` live figures to
            plant on the salary derivation.

    Returns:
        The :class:`~app.services.cash_ledger.AmountBasis`.
    """
    from app.services.cash_ledger import (  # pylint: disable=import-outside-toplevel
        AmountBasis,
    )

    planted = PlantedPricing(overrides)
    return AmountBasis(
        user_id=0,
        scenario_id=getattr(rows[0], "scenario_id", 0) if rows else 0,
        salary=planted,
        loans=planted,
    )


def transfer_amount(xfer):
    """What one parent TRANSFER is worth -- its amount, resolved.

    **Written when plan step X-au-f moved a generated transfer's figure from a
    column to a value**, the last per-kind cutover: X-au-g-2c-2 had done the
    same for each transfer shadow, which a helper of its own priced (deleted at
    plan step ``balance:X-bi-6-4d-2``, which deleted the shadows), and X-au-d
    (salary) and X-au-e (ordinary recurring rows) came between.  A case
    asserting ``xfer.amount == Decimal(...)`` on a generated transfer would
    compare a figure against ``None``; what those cases were ABOUT -- what the
    transfer is worth -- is asked of the amount model here.

    An AD-HOC transfer still owns its figure, so this answers the same column it
    always did for one; the helper is right for both kinds, which is what stops
    a case having to know which it holds.

    A FRESH basis per call: a case that edits a transfer and re-reads must see
    the new figure, and a basis memoizes for the length of a read pass.

    Args:
        xfer: The transfer to price.

    Returns:
        The ``Decimal`` the transfer resolves to.

    Raises:
        AmountUnresolvable: From the amount model, for a transfer whose rule
            cannot price it -- a refusal is never a fallback.
    """
    # Pylint: ``import-outside-toplevel`` -- this module imports no app
    # symbols at top level (its collection-time-safety convention).
    # pylint: disable=import-outside-toplevel
    from app.services.cash_ledger import derived_amount_basis, resolve_transfer_amount

    return resolve_transfer_amount(
        xfer, derived_amount_basis(xfer.user_id, xfer.scenario_id),
    )


def basis_for(account, scenario):
    """The read pass's :class:`~app.services.cash_ledger.AmountBasis`.

    What a route or a top-level producer holds and threads into the cash fold
    since plan step X-au-c2b -- pinned to an owner and a scenario, never to a
    row set, and lazy, so building one in a fixture costs nothing until a rule
    asks.  The seam entries take one where they took a bare ``scenario_id``,
    because the scenario a row set is loaded under and the derivations it is
    priced through are one decision rather than two a caller could get apart.

    Args:
        account: Any account of the owner; only ``user_id`` is read.
        scenario: The scenario whose rows are being valued.

    Returns:
        The unresolved :class:`~app.services.cash_ledger.AmountBasis`.
    """
    from app.services.cash_ledger import (  # pylint: disable=import-outside-toplevel
        derived_amount_basis,
    )

    return derived_amount_basis(account.user_id, scenario.id)


def all_periods(user_id):
    """Return every one of *user_id*'s pay periods as ORM rows, payday order.

    **A TEST helper because the application has no such reader any more.**
    ``pay_period_service.get_all_periods`` was the last of that module's six
    ``get_*`` readers, and pay-calendar plan step C2-f3c deleted it with its
    last caller: the recurrence generation seam now takes a
    :class:`~app.services.pay_calendar.PayCalendar`, which IS the owner's whole
    schedule, so nothing in ``app/`` asks a separate reader for the rows.
    Keeping a production function alive for test callers alone is the
    speculative shape ``CLAUDE.md`` rule 13 forbids, and ruling **P54** already
    settled where the replacement lives: one shared helper here, never a
    ``for_test`` door on the real API.

    **Ordered by ``start_date``, and since plan step ``pay_calendar:C4-c``
    there is no other order to ask for**: ``period_index`` was a column until
    that step dropped it, and a row is now the payday alone.

    A row carries no SPAN either.  A caller that wants one asks
    :func:`derived_span`, which is the calendar's answer for that row.

    Args:
        user_id: The owning user.

    Returns:
        The owner's :class:`~app.models.pay_period.PayPeriod` rows, earliest
        payday first.  Empty for an owner with no schedule.
    """
    from app.extensions import db  # pylint: disable=import-outside-toplevel
    from app.models.pay_period import (  # pylint: disable=import-outside-toplevel
        PayPeriod,
    )

    return (
        db.session.query(PayPeriod)
        .filter_by(user_id=user_id)
        .order_by(PayPeriod.start_date)
        .all()
    )


def derived_span(period):
    """Return the :class:`DerivedPeriod` the application answers for *period*.

    **The suite's one door onto "how far does this paycheck run", and plan step
    ``pay_calendar:C4-c`` is why it has to be a door at all.**  A test read
    ``period.end_date`` and ``period.period_index`` off the ORM row until that
    step dropped both columns.  Neither is a property of a row: the ordinal is
    the payday's position in the owner's sorted set and the end is the day
    before the NEXT payday, so both are answers about the WHOLE payday set and
    ``pay_calendar.calendar_for`` is what computes them.

    Resolving through the owner's real calendar rather than constructing a
    :class:`DerivedPeriod` by hand is what keeps a case from asserting against
    bounds the application never computes -- the property
    :func:`period_window` exists for one level up.

    Args:
        period: A saved :class:`~app.models.pay_period.PayPeriod` row.

    Returns:
        Its :class:`~app.services.pay_calendar.DerivedPeriod` -- ``start_date``,
        ``end_date``, ``period_index`` and ``end_is_projected``.

    Raises:
        AssertionError: *period* is not in its own owner's calendar, which
            means it was never flushed or belongs to another owner.  A silent
            ``None`` here would make every assertion downstream vacuous.
    """
    from app.services.pay_calendar import (  # pylint: disable=import-outside-toplevel
        calendar_for,
    )

    resolved = calendar_for(period.user_id).period_by_id(period.id)
    assert resolved is not None, (
        f"pay period {period.id} is not in user {period.user_id}'s own "
        f"calendar -- it was never flushed, or it belongs to someone else"
    )
    return resolved


def last_covered_day(period):
    """Return the last day *period* covers, DERIVED.

    ``period.end_date`` until plan step ``pay_calendar:C4-c`` dropped that
    column; :func:`derived_span` carries why it is a question about the whole
    payday set.  This spelling exists because most callers want the DAY and
    reading ``derived_span(p).end_date`` at every one of them puts the word
    ``end_date`` back on a page where the column no longer exists.

    Args:
        period: A saved :class:`~app.models.pay_period.PayPeriod` row.

    Returns:
        The inclusive last day of its span.
    """
    return derived_span(period).end_date


def open_owner_calendar(user_id, first_payday, num_periods=1, cadence_days=14):
    """Open *user_id*'s pay calendar through the writer that owns the table.

    **Plan step ``pay_calendar:C4-b-1``.**  For a test that hand-builds a
    ``User`` and needs a pay period before ``account_service.create_account``,
    which refuses an owner who has none.  The sites this replaced each carried
    the same five lines -- a bare ``PayPeriod(...)`` setting ``end_date`` and
    ``period_index``, the two values ``pay_period_write._write_derivation``
    DERIVES, and no ``budget.pay_schedule`` row beside them.  *No count is
    stated here: two drafts of this docstring and the plan's own sentence gave
    three different ones, which is what a number nothing recomputes does.*

    That pairing is one no application door can produce: ``record_paydays``
    upserts the owner's cadence in the same call that records a payday (the
    cadence rule, plan step C3-b), and ``registration_service.register_user`` reaches
    the table only through it.  So every one of those sites built the single
    owner shape production does not have -- pay-calendar finding **P8** -- and
    the derived columns they typed were free to disagree with the derivation
    that reads them.

    Use :func:`rebuild_calendar` instead when the owner ALREADY has periods:
    this writes, that resets.

    Args:
        user_id: The owning user's id, already flushed.
        first_payday: The opening payday.
        num_periods: How many periods to record from it, default one.
        cadence_days: Days between them, persisted as the owner's cadence.

    Returns:
        The created :class:`~app.models.pay_period.PayPeriod` rows, payday
        ascending -- ``period_index`` 0..n-1 and each end derived, so a caller
        that wants one period may index ``[0]``.  **EMPTY when every requested
        payday is already on the table**, which is ``record_paydays``' own
        contract for a batch that records nothing: an ``[0]`` on that answer is
        an ``IndexError``.  No caller re-requests an existing payday today, and
        the day one does it should read the answer rather than index it.
    """
    from app.services import (  # pylint: disable=import-outside-toplevel
        pay_period_write,
    )

    return pay_period_write.record_paydays(
        user_id=user_id,
        first_payday=first_payday,
        num_periods=num_periods,
        rhythm=rhythm_of(cadence_days),
    )


def record_paydays_across_a_hole(user_id, first_payday, num_periods, rhythm):
    """Record a batch the writer REFUSES for skipping a paycheck of the plan.

    **Plan step ``pay_calendar:C17-c-2a`` (rulings R-PC67, R-PC76).**  A
    batch whose first new payday falls at or past the SECOND payday the
    owner's plan projects after their record leaves a whole paycheck
    missing, and ``pay_period_write.record_paydays`` refuses it through
    ``pay_period_batch.reject_skipped_paycheck`` -- from every door, which is
    the point of asking it in the one writer.  So a hole is now a state no
    application door can write, and :func:`rebuild_calendar`'s predicate
    says where such a state is built: here, by hand, in one shared helper.

    **Who needs it.**  ``seed_user`` opens its calendar at
    :data:`~tests.conftest.SEED_USER_BOOTSTRAP_START` (2024-01-05) so the
    seeded account has a paycheck to open in, and a case that then wants a
    2026 block beside it -- two years of planned paychecks skipped -- was
    writing it through the writer until this step.  Every such case is about
    the 2026 rows, not the hole, and this keeps the rows, the ordinals and
    the eras exactly as the writer used to leave them.  A case that means to
    GRADE the refusal calls ``record_paydays`` and expects the
    ``ValidationError``; a fixture whose batch the writer would ACCEPT is
    refused here, so this helper cannot quietly become the default door.

    **It composes the writer's own producers in the writer's order and skips
    ONE refusal.**  The days are ``pay_period_batch.requested_paydays``'
    (displaced under the rhythm, as the writer records them); the era rule is
    the writer's (``pay_era_write.eras_describing`` / ``era_to_mint``), so a
    batch stating a rhythm the covering era does not hold mints an era at its
    first payday and one continuing the era mints none; the floor is asked of
    the same list the writer hands it (the minted era's first payday in
    front); the statements run in ``_apply``'s order (the schedule row, the
    retire, the mint, the insert) and the session is expired when an era was
    retired or minted.  What it does not ask is the ceiling, and it asserts
    that the ceiling WOULD have fired.  What it does NOT reproduce: the
    ``pay_periods_generated`` log event; the writer's own
    ``reject_undatable_payday``, ``reject_out_of_range_batch_size`` and
    ``reject_out_of_range_cadence`` (``mint_era`` re-asks the last for a
    minting batch); and ``reject_shift_on_short_cadence`` at the writer
    (``mint_era`` asks it too) -- a case grading any of them belongs on
    ``record_paydays``.

    Args:
        user_id: The owning user's id.
        first_payday: The batch's first NOMINAL payday.
        num_periods: How many paydays the batch covers, existing ones included.
        rhythm: The batch's :class:`~app.services.pay_rhythm.Rhythm`.

    Returns:
        The created :class:`~app.models.pay_period.PayPeriod` rows, payday
        ascending and flushed -- ``record_paydays``' own answer shape.

    Raises:
        ValidationError: The floor refuses the batch (a payday inside a
            paycheck the owner already has), exactly as the writer would.
        AssertionError: The writer would have ACCEPTED this batch -- the
            case should call ``record_paydays``.
    """
    from app.exceptions import ValidationError  # pylint: disable=import-outside-toplevel
    from app.extensions import db  # pylint: disable=import-outside-toplevel
    from app.models.pay_period import PayPeriod  # pylint: disable=import-outside-toplevel
    from app.services import pay_period_batch  # pylint: disable=import-outside-toplevel

    held = {period.start_date for period in all_periods(user_id)}
    requested = pay_period_batch.requested_paydays(first_payday, num_periods, rhythm)
    new_paydays = [payday for payday in requested if payday not in held]
    stored = pay_schedule_service.resolve_schedule(user_id)
    standing = pay_era_write.eras_describing(stored, held)
    era = None
    if new_paydays:
        era = pay_era_write.era_to_mint(standing, first_payday, rhythm)
    pay_period_batch.reject_backward_payday(
        held, new_paydays if era is None else [requested[0], *new_paydays],
        None if stored is None else stored.eras,
    )
    try:
        pay_period_batch.reject_skipped_paycheck(held, new_paydays, standing)
    except ValidationError:
        pass
    else:
        raise AssertionError(
            f"record_paydays would accept a batch from {first_payday} x "
            f"{num_periods} paid {rhythm.cadence.phrase} for user {user_id}; "
            f"this helper is only for a batch that skips a paycheck of the "
            f"plan.  Call pay_period_write.record_paydays instead."
        )
    pay_schedule_service.ensure_schedule_row(user_id)
    retired = pay_era_write.retire_eras(
        user_id, tuple(e.effective_from for e in standing),
    )
    if era is not None:
        pay_era_write.mint_era(user_id, era)
    created = [PayPeriod(user_id=user_id, start_date=payday) for payday in new_paydays]
    db.session.add_all(created)
    db.session.flush()
    if retired or era is not None:
        db.session.expire_all()
    return created


def rebuild_calendar(user_id, first_payday, num_periods, cadence_days):
    """Rebuild *user_id*'s WHOLE schedule on a fixed-days rhythm, through the reset door.

    :func:`rebuild_calendar_on`'s fixed-days spelling, which is what every
    caller before plan step ``pay_calendar:C17-d-2`` meant by a cadence;
    that function carries the argument and the books bound.

    Args:
        user_id: The owning user's id.
        first_payday: The opening payday of the rebuilt schedule.
        num_periods: How many periods to build from it.
        cadence_days: Days between them, persisted as the owner's cadence by
            the writer (the cadence rule, plan step C3-b).

    Returns:
        :func:`rebuild_calendar_on`'s answer.
    """
    return rebuild_calendar_on(
        user_id, first_payday, num_periods, rhythm_of(cadence_days),
    )


def rebuild_calendar_on(user_id, first_payday, num_periods, rhythm):
    """Rebuild *user_id*'s WHOLE pay-period schedule through the reset door.

    **Plan step ``pay_calendar:C4-b-1``.**  The one place the test tree says
    "give this owner that calendar", and it says it by calling
    ``pay_period_admin.reset_pay_periods`` -- the settings-page correction at
    ``POST /pay-periods/reset``, which wipes every period, records the new
    batch through the writer and re-syncs the loan genesis postings and the
    account anchor corrections onto what it built.

    **A test that builds a pay period by hand builds a row no owner can
    have**, and that is not a style point.  ``end_date`` and ``period_index``
    are DERIVED from the payday set; a hand-built row sets them to whatever
    the author typed, and it wrote no ``budget.pay_schedule`` row, so
    ``pay_schedule_service.resolve_schedule`` fell back to INFERRING the
    cadence from that same hand-typed ``end_date``.  Eight cases in
    ``test_recurrence_engine.test_dates.TestDueDateGeneration`` passed only through that
    loop: they wrote a 28-day February period, the fallback read 28 back as
    the owner's cadence, and the derived span therefore matched the typed one.
    Give the owner a real cadence and the same rows derive a 14-day span and
    generate nothing.  Ruling **P54**'s shape: one shared helper here, never a
    ``for_test`` door on the real API.

    **The LOOP is now unbuildable and the reason to come here is stronger for
    it** (plan step ``pay_calendar:C4-b-2``).  ``fk_pay_periods_schedule``
    refuses a pay period whose owner has no ``budget.pay_schedule`` row, so a
    hand-built row for a schedule-less owner is an ``IntegrityError`` rather
    than a silently self-confirming assertion.

    **What that does NOT do is make every remaining hand-built site wrong, and
    this paragraph states a PREDICATE rather than a list** -- an adversarial
    review measured a first draft's list as a set defined by subtraction,
    naming two categories where there are at least three.  The predicate: a
    hand-built ``PayPeriod(...)`` is legitimate exactly when the row it needs
    is one no application door can write -- a corrupt ordinal, a gap, an
    overlapping span (take ``bare_user_with_cadence``) -- or when nothing
    reaches a database at all.  A site that merely finds it convenient is one
    this helper should have.  ``grep -rn 'PayPeriod($' tests/`` answered 38
    across 19 files on 2026-09-01, and the third category the draft missed is
    real: rows hand-built for an owner who already carries a schedule row from
    a fixture, which the key admits and which nothing here classifies.  Re-run
    the grep; do not trust the number.

    Args:
        user_id: The owning user's id.
        first_payday: The opening payday of the rebuilt schedule, a day on
            *rhythm*'s grid.
        num_periods: How many periods to build from it.
        rhythm: The :class:`~app.services.pay_rhythm.Rhythm` the schedule
            runs on, persisted as the owner's era by the writer.  A
            day-of-month cadence here (plan step ``pay_calendar:C17-d-2``)
            is what lets the cross-page fixtures state a calendar-monthly
            schedule through the door rather than by hand (ledger row
            **P78**).

    Returns:
        The owner's periods, payday ascending -- :func:`all_periods`' answer,
        for the reason that function gives.

    Raises:
        PayPeriodResetBlocked: The owner has a settled transaction, which the
            reset door refuses; build the calendar before settling anything.
    """
    from app.extensions import db  # pylint: disable=import-outside-toplevel
    from app.models.account import Account  # pylint: disable=import-outside-toplevel
    from app.services import (  # pylint: disable=import-outside-toplevel
        pay_period_admin,
    )

    # **The BOOKS bound the opening payday, and since plan step
    # ``pay_calendar:C4-b-1`` nothing else does.**  Two accidental protections
    # went when ``conftest._drop_seed_user_bootstrap`` did, and an adversarial
    # review of that step found both: the hand-rolled version APPENDED beside
    # the owner's existing paydays, so ``reject_backward_payday`` refused any
    # first payday earlier than one cadence after the latest -- and where that
    # let something through, a backward-only restatement moved the books to
    # meet it.  The reset door retires every surviving payday in the SAME call
    # that records the new batch, so that refusal returns early on an empty
    # surviving set and every opening day became legal.
    #
    # A pay period opening at or before an account's books contradicts ruling
    # ``balance:R-HG`` -- an opening equity is the CLOSING balance for its own
    # day -- so it is refused here, loudly, rather than built and left for
    # whichever balance case notices.  **On this door rather than on one of its
    # callers**: a second adversarial review found the first placement was on
    # ``conftest._reset_seed_calendar``, which is one of three callers, and the
    # one a new case is least likely to use --
    # ``rebuild_calendar_from_spans`` reaches this directly and already types
    # arbitrary years.
    #
    # **Per account it is the GOVERNING row, not the latest DAY**, and the
    # difference is not academic: ``budget.account_openings`` is append-only
    # and the governing row is the one recorded LAST (ruling ``balance:R-HE``),
    # so an account restated BACKWARD carries a row with a later ``opened_on``
    # that no longer governs.  The seeded Checking is exactly that -- 2024-01-05
    # from the factory, then 2024-01-04 from
    # ``open_books_before_the_first_assertion`` -- and a first cut of this
    # guard reduced with ``max(opened_on)``, read 2024-01-05, and refused the
    # legal opening day.  ``test_it_admits_the_first_day_it_legally_can``, the
    # second direction of this guard's own control, is what measured it.
    books = max(
        (
            day for day in (
                _governing_opening_day(db.session, account)
                for account in db.session.query(Account)
                .filter(Account.user_id == user_id).all()
            )
            if day is not _real_date.max
        ),
        default=None,
    )
    assert books is None or first_payday > books, (
        f"user {user_id} has an account whose books open {books}, and this "
        f"asks for a calendar opening {first_payday}, which is on or before "
        f"them.  An opening equity is the CLOSING balance for its own day "
        f"(ruling balance:R-HG), so no pay period may open at or before one.  "
        f"Ask for a later first payday, or open that account's books earlier."
    )
    pay_period_admin.reset_pay_periods(
        user_id, first_payday, num_periods, rhythm,
    )
    # The door is one transaction its ROUTE commits, so a test caller commits
    # it, and expires: the caller may hold rows the wipe deleted.
    db.session.commit()
    db.session.expire_all()
    return all_periods(user_id)


def rebuild_calendar_from_spans(user_id, spans):
    """Rebuild *user_id*'s calendar so its periods OPEN on each span's start.

    :func:`rebuild_calendar` for a case that wants several periods the writer
    cannot space evenly -- "January, April, July and October", which is four
    paydays 90, 91 and 92 days apart.  One reset opens the schedule and each
    later payday is appended by the same writer, so every row is still the
    derivation's.

    **What a span's END is worth here, stated because it is NOT what a
    hand-built row gave.**  Every period's end is the day BEFORE the next
    payday, so an interior span's stated end is honoured only when the next
    span opens the day after it; where the caller asks for gapped spans the
    interior ends run WIDER than asked, because a gap is not expressible in a
    derived calendar (``docs/plans/implementation_plan_pay_calendar.md``
    section 3).  The LAST span is the one end the derivation projects, and it
    is exact: the owner's cadence is that span's length.  Callers here assert
    on the occurrence's own day, which lands in the same period under either
    width.

    Args:
        user_id: The owning user's id.
        spans: ``[(start, end), ...]``, ascending and non-overlapping, at
            least one.  Each ``start`` becomes a payday.

    Returns:
        The owner's periods, payday ascending -- one per span.

    **Every interior span is a HOLE the writer refuses** (plan step
    ``pay_calendar:C17-c-2a``, ruling R-PC67): "January, April, July and
    October" at a 31-day cadence skips two planned paychecks between each
    pair, so the appends go through :func:`record_paydays_across_a_hole`,
    the tree's one door for the state no application door writes.  Spans
    the writer WOULD accept belong in :func:`rebuild_calendar`; that helper
    says so by refusing them.

    Raises:
        ValidationError: Two spans open closer together than the last span's
            length, which is the forward-only rule
            ``pay_period_batch.reject_backward_payday`` states.
        AssertionError: Two spans open within one planned paycheck of each
            other, a batch the writer accepts -- use :func:`rebuild_calendar`.
    """
    from app.extensions import db  # pylint: disable=import-outside-toplevel

    last_start, last_end = spans[-1]
    cadence_days = (last_end - last_start).days + 1
    rebuild_calendar(user_id, spans[0][0], 1, cadence_days)
    for start, _end in spans[1:]:
        record_paydays_across_a_hole(
            user_id, start, 1, rhythm_of(cadence_days),
        )
    # COMMIT the appends too.  :func:`rebuild_calendar` commits the opening
    # payday because the door it wraps leaves the transaction to its caller,
    # and ``record_paydays`` only FLUSHES -- so without this the calendar was
    # half committed and half pending.  Nothing broke (a test client reuses the
    # live app context, so the same session sees both), but a half-committed
    # calendar is an asymmetry no reader would predict.
    db.session.commit()
    return all_periods(user_id)


@contextmanager
def pay_periods_hydrated():
    """Count the ``PayPeriod`` ORM entities LOADED inside this block.

    The measurement a statement count cannot make: an eager load rides inside
    another query as a JOIN and issues no statement of its own, and a
    ``db.session.get`` served from the identity map issues none either.  Both
    hydrate an entity, which is what this counts.

    **The event and not the identity map**, because the map holds WEAK
    references: a probe that counted survivors after the call read zero for a
    producer that had hydrated twelve, since nothing outside the producer
    holds a ``Transaction`` to keep its ``pay_period`` back-reference alive.
    That was measured -- a first cut of this helper read the map and could not
    fire.  ``loaded_as_persistent`` fires per load and cannot be collected
    away.

    **A caller must EXPUNGE first, and this is the one thing about the probe
    that is not obvious.**  ``loaded_as_persistent`` fires on a LOAD, so an
    instance already in the session's identity map is returned by
    ``db.session.get`` without firing anything: a fixture that hands back live
    ORM rows makes this read ZERO for a producer that is hydrating on every
    call, and the guard then passes on exactly the code it exists to refuse.
    Measured at plan step C2-f3e -- with ten periods preloaded, a
    ``db.session.get(PayPeriod, ...)`` inside this block records nothing.  An
    earlier wording of this paragraph claimed the probe counts such a call and
    it does not.

    Shared here rather than per-suite because two SUITES now assert the same
    structural property -- no ORM ``PayPeriod`` on this path.  Written for plan
    step C2-f3d (``test_spending_report_service``), moved here whole by plan
    step C2-f3e (``test_transaction_auth``); both are pay-calendar steps, and
    an earlier wording of this sentence said "two arcs".

    Yields:
        The list of hydrated :class:`~app.models.pay_period.PayPeriod`
        instances, appended to as the block runs.
    """
    # Pylint: ``import-outside-toplevel`` -- this module imports no app or ORM
    # symbols at top level (its collection-time-safety convention).
    # pylint: disable=import-outside-toplevel
    from sqlalchemy import event
    from sqlalchemy.orm import Session
    from app.models.pay_period import PayPeriod

    loaded = []

    def _record(_session, instance):
        if isinstance(instance, PayPeriod):
            loaded.append(instance)

    event.listen(Session, "loaded_as_persistent", _record)
    try:
        yield loaded
    finally:
        event.remove(Session, "loaded_as_persistent", _record)


def rendered_transfer_amount(xfer) -> str:
    """Return what the transfer FORM would have rendered in its amount box.

    **What a route test must post as ``amount_as_rendered``**, and the ONE
    spelling of it, because getting this wrong is how a route test stops
    exercising the door it names.  The three transfer fragments read
    ``budgets[xfer.id]`` off ``routes._render_helpers.transfer_budgets`` since
    plan step balance:X-au-f-1 -- the amount model's answer, not the
    ``budget.transfers.amount`` COLUMN -- so a payload built from that column
    stopped being what a browser sends the moment X-au-f emptied it for a
    generated transfer.

    Sixteen call sites posted ``str(xfer.amount)`` until X-au-f, which was the
    same string while every transfer owned its figure and became the literal
    ``"None"`` the day the cutover landed.  ``TransferUpdateSchema`` then
    refuses the field and the PATCH returns 422, so every one of those cases
    would have been asserting against a rejected request rather than against
    the authorship rule it was written for -- the shape
    ``feedback_a_route_test_must_post_what_the_template_emits`` names.

    Args:
        xfer: The transfer whose form is being simulated.

    Returns:
        The rendered figure as the form's string, ready to post.
    """
    from app.services.cash_ledger import (  # pylint: disable=import-outside-toplevel
        derived_amount_basis, resolve_transfer_amount,
    )

    return str(resolve_transfer_amount(
        xfer, derived_amount_basis(xfer.user_id, xfer.scenario_id),
    ))


def amount_basis_for(row):
    """Return the :class:`AmountBasis` that prices ONE row, for a test.

    **Named for the thing it builds, because ``basis_for`` was already taken**
    by the ``ProjectedBasis`` helper above -- appending a second definition of
    that name silently shadowed it and took 63 tests down with a
    ``TypeError``.  Two different "bases" live in this suite and neither may
    answer to the bare word.

    **The single-row form of what a read pass holds** (plan step X-au-j).  Both
    ``settle_amount`` twins take their basis as a REQUIRED parameter, because
    their two live callers are PASSES and an optional one would leave the
    expensive shape as what a caller gets by saying nothing -- which is how
    findings **N-295** and **N-309** grew back one tier up after plan step
    X-au-c2b closed the same cost within a call.

    A test valuing one row is the caller that legitimately holds no pass, so it
    builds one for that row and says so.  Shared here rather than spelled at
    each of the sites that need it, which is this project's DRY rule; it stays
    EXPLICIT at every call (``settle_amount(txn, amount_basis_for(txn))``), so the
    derivation being built is still visible where it is paid for.

    Args:
        row: The :class:`~app.models.transaction.Transaction` being valued.
            Its ``account.user_id`` and ``scenario_id`` are what a basis is
            pinned to.

    Returns:
        The :class:`~app.services.cash_ledger.AmountBasis` for that row's owner
        and scenario.
    """
    # Pylint: ``import-outside-toplevel`` -- this module imports no app or ORM
    # symbols at top level (its collection-time-safety convention).
    # pylint: disable=import-outside-toplevel
    from app.services.cash_ledger import derived_amount_basis

    return derived_amount_basis(row.account.user_id, row.scenario_id)


def amount_basis_for_scenario(scenario_id):
    """Return the amount basis for *scenario_id*, deriving its owner from the scenario.

    **The ONE spelling for a test helper that holds a scenario id and no
    owner** (plan step balance:X-au-g-2c).  Routing
    ``loan_payment_service.get_payment_history`` through the amount model gave
    ``load_loan_context`` an :class:`~app.services.cash_ledger.AmountBasis`
    where it took a bare ``scenario_id``, and a dozen replay helpers in this
    suite hold exactly that id: they are built from a loan and a scenario,
    never from a request with a ``current_user``.

    A scenario belongs to exactly one owner (``budget.scenarios.user_id``), so
    the owner is DERIVED here rather than taken beside the id.  Spelling both
    at a dozen call sites would invite a pair naming one owner and another's
    scenario, which is the mismatch
    :func:`~app.services.cash_ledger.resolve_transaction_amount` refuses a row
    for -- and a test is exactly where such a pair would be written by hand.

    **The sibling of :func:`amount_basis_for` above, keyed differently.**  That
    one takes a ROW and reads its account's owner and its own scenario; this
    takes the SCENARIO because its callers are replay helpers built from a loan
    id and a scenario id, with no row in hand at all.  Two keys, one
    derivation, and neither may answer to the other's name.

    Ruling **P54** is why this is a shared helper here rather than a
    convenience on the production constructor: a production API with only test
    callers is the speculative shape ``CLAUDE.md`` rule 13 forbids.
    **Production has the same asymmetry and it is filed, not fixed here**
    (finding **N-432**): ``derived_amount_basis`` takes an owner AND a
    scenario and nothing checks they agree, where the scenario already states
    its owner.  (``amount_basis`` itself reads the owner off the pass's pricer
    since plan step salary:C12, so the pass-side door no longer has the pair.)

    Args:
        scenario_id: The scenario the rows being priced belong to.

    Returns:
        The unresolved :class:`~app.services.cash_ledger.AmountBasis` for that
        scenario's owner and that scenario.

    Raises:
        NoResultFound: When no scenario carries that id -- loud, because a
            basis built for a scenario that does not exist would price every
            row it is handed as belonging to another.
    """
    # Pylint: ``import-outside-toplevel`` -- this module imports no app or ORM
    # symbols at top level (its collection-time-safety convention).
    # pylint: disable=import-outside-toplevel
    from app.extensions import db
    from app.models.scenario import Scenario
    from app.services.cash_ledger import derived_amount_basis

    scenario = db.session.query(Scenario).filter(
        Scenario.id == scenario_id,
    ).one()
    return derived_amount_basis(scenario.user_id, scenario_id)


def count_amount_bases(monkeypatch):
    """Return a list that records every ``AmountBasis`` CONSTRUCTION.

    **It counts the producer, not the factory, and that difference is the
    whole instrument** (plan step X-au-j).  A first version of this control
    patched ``amount_basis`` on the modules that import it and PASSED against a
    planted defect: every module does ``from app.services.cash_ledger import
    amount_basis``, which binds the function by value at import time, so
    patching a name in one module says nothing about the copy another module
    already holds -- and the arm rebuilding its caller's basis was invisible.

    ``amount_basis`` calls ``income_service.salary_pricing`` unconditionally on
    every construction, through a module ATTRIBUTE resolved at call time, so a
    patch here is seen no matter which module reached the factory -- and
    ``derived_amount_basis``, the pass-less constructor since plan step
    salary:C12, reaches it through ``amount_basis``, so both doors count.  One
    entry per basis built, by anybody.  The owner is read off the pricer the
    basis is built over, which costs no derivation.

    Args:
        monkeypatch: The test's ``monkeypatch`` fixture.

    Returns:
        The list the counter appends ``(user_id, scenario_id)`` to.
    """
    # Pylint: ``import-outside-toplevel`` -- this module imports no app or ORM
    # symbols at top level (its collection-time-safety convention).
    # pylint: disable=import-outside-toplevel
    from app.services import income_service

    built = []
    real = income_service.salary_pricing

    def _counted(scenario_id, paychecks):
        built.append((paychecks.user_id, scenario_id))
        return real(scenario_id, paychecks)

    monkeypatch.setattr(income_service, "salary_pricing", _counted)
    return built


#: One import, two bank lines it sighted (plan step ``bank_import:X-f6b-1``:
#: a line is held by its sightings, and the wording is the sighting's) and
#: one match naming BOTH, in the shape ``statement_match._accept.record_match``
#: leaves: the group's EARLIEST line posts before the row that explains it
#: settles.
#:
#: **Raw SQL, and ONE copy of it.**  It lived in three test modules
#: byte-identically until an adversarial test-quality review counted them --
#: and the query has to agree with
#: :data:`app.opening_infrastructure.MATCHED_LINE_DAYS_SQL`'s row set, so
#: three copies were three places for that to drift silently.  That is this
#: arc's own stated root cause, in its own test suite.
#:
#: It is raw because what the cases using it grade IS a database trigger and a
#: raw-SQL reader; a case grading a DOOR should build its match through
#: ``accept_match`` instead -- what the DOOR leaves is not what this
#: builds, and a case grading a door has never seen the row shape a
#: door produces if it uses this.
_A_MATCHED_GROUP = """
    WITH import_row AS (
        INSERT INTO budget.statement_imports
               (account_id, user_id, source_id, file_name, file_digest,
                declared_start, declared_end)
        SELECT :a, :u,
               (SELECT id FROM ref.statement_sources ORDER BY id LIMIT 1),
               'books-boundary-probe.csv', :digest, :early, :late
        RETURNING id
    ), line_rows AS (
        INSERT INTO budget.bank_statement_lines
               (account_id, posted_on, amount, sequence_in_group)
        SELECT :a, day.posted_on, -15.96, 0
          FROM (VALUES (:early), (:late)) AS day(posted_on)
        RETURNING id
    ), sighting_rows AS (
        INSERT INTO budget.statement_line_sightings
               (account_id, line_id, import_id, description)
        SELECT :a, line_rows.id, import_row.id, 'PROBE'
          FROM import_row, line_rows
    ), match_row AS (
        INSERT INTO budget.statement_matches
               (account_id, user_id, applied_by_rule)
        VALUES (:a, :u, false)
        RETURNING id
    )
    INSERT INTO budget.statement_match_members
           (match_id, account_id, bank_statement_line_id)
    SELECT match_row.id, :a, line_rows.id FROM match_row, line_rows
"""


def match_two_lines(db_session, account, owner_id, early, late):
    """Match two bank lines on *account*, posted *early* and *late*.

    The state plan step **balance:X-f3c-2b-2b**'s matched-line bound is about:
    a group whose earliest line posts strictly before the row explaining it
    settles, which is every multi-day match.

    Args:
        db_session: The test ``db.session``.
        account: The :class:`~app.models.account.Account` to match on.
        owner_id: Its owner.
        early: The earlier line's posting day.
        late: The later one's.
    """
    # pylint: disable=import-outside-toplevel  -- same circular-dep avoidance
    # as the loan helpers above.
    import sqlalchemy as _sa

    db_session.execute(_sa.text(_A_MATCHED_GROUP), {
        "a": account.id,
        "u": owner_id,
        "digest": f"probe-{account.id}-{early}",
        "early": early,
        "late": late,
    })
    db_session.commit()


def write_past_the_amount_seam(row, value):
    """Write *row*'s figure column DIRECTLY, bypassing ``amount_ownership``.

    **The only supported way for a test to construct a state the mapping
    refuses** (plan step **X-au-k**), and it exists because the obvious
    spelling is a trap.  ``Transaction.__estimated_amount`` and
    ``Transfer.__amount`` are double-underscored, so Python mangles them and a
    hand-written ``row._estimated_amount = x`` binds a plain instance attribute
    that reaches no column -- a SILENT no-op, which is the right failure for a
    typo in application code and the wrong one for a control that means to
    write.

    Its callers (read 2026-10-20): in ``test_amount_source.py``'s
    ``TestTheRulesDoNotReadTheColumnTheyReplace``, the two cases that prove a
    derived answer is invariant under a rival figure (a definition's row
    under its own column, a generated transfer's leg under its parent's) and
    the one that proves the database refuses a declared row carrying one; in
    ``test_a_transfers_amount_is_declared/test_the_declaration.py``,
    ``test_a_leg_cannot_hold_a_figure_while_it_declares_a_parent``, which
    proves the database refuses a transfer's twin holding a figure; and the
    hand-run ``tests/manual/verify_amount_resolver.py``.  Each is constructing
    the shape :class:`~app.models.amount_ownership.AmountOwnership` has no
    member for, so none of them can go through the seam.

    Args:
        row: A :class:`~app.models.transaction.Transaction` or
            :class:`~app.models.transfer.Transfer`.
        value: The figure to write, or ``None`` to empty the column.

    Raises:
        AttributeError: When *row*'s model maps no such private column, which
            is what stops this helper from silently binding a new attribute if
            a later step renames one.
    """
    attr = f"_{type(row).__name__}__{_figure_column_of(row)}"
    if attr not in type(row).__mapper__.attrs:
        raise AttributeError(
            f"{type(row).__name__} maps no {attr!r}; the amount seam's private "
            "column was renamed and this helper would have bound a plain "
            "attribute that writes nothing"
        )
    setattr(row, attr, value)


def _figure_column_of(row) -> str:
    """Return the column *row*'s table stores an owned figure in.

    Args:
        row: A ``Transaction`` or ``Transfer``.

    Returns:
        ``"estimated_amount"`` or ``"amount"``.

    Raises:
        AttributeError: When *row* is neither.
    """
    for column in ("estimated_amount", "amount"):
        if column in type(row).__table__.c:
            return column
    raise AttributeError(f"{type(row).__name__} carries no figure column")


def unseeded_replay_balance(loan_id, scenario_id, as_of):
    """Return a loan's UN-SEEDED schedule-replay balance -- the pre-switch value.

    **The suite's ONE assembly of the anchor replay** (plan step
    **balance:X-bl-2b**).  The balance the resolver derives from its anchor
    replay ALONE -- no genesis seed, no ledger -- which is what a loan's scalar
    surfaces showed BEFORE the read switch, and what five controls across four
    files pin so they can assert the ledger DIVERGES from it off-schedule.

    Each of those five spelled the four-step assembly itself, and this step made
    the block longer rather than shorter, so they are one call now.  What that
    removes is not lines but a CONTRACT stated five times: the replay's feed must
    carry its schedule SLOT
    (:func:`~app.services.amortization_engine.slotted_dates`), because the
    forward override plans by that same slot and a feed the two disagree on
    drops a planned payment.  Stated once, a caller cannot forget it.

    **The reconciliation oracle deliberately does NOT call this**, and that is
    the one duplication left standing.  ``test_posting_ledger_loan_reconciliation
    ._helpers._resolver_balance`` keeps its own body because
    ``test_the_roots_cover_what_the_reference_calls`` reads the modules the
    reference depends on off THAT FUNCTION'S OWN AST -- a delegation to a helper
    in this file would make the closure check grade an empty root set, which is
    the vacuity that control exists to prevent.  The reason is written at that
    function too.

    **It is AMOUNT-FREE.**  The replay reads three dates per payment and no
    figure, so the feed is ``loan_ledger.payment_installments`` and not the
    priced ``load_loan_context``: a pricing refusal cannot break a control that
    reads no amount, and the import closure is 46 modules rather than 102
    (measured 2026-09-09; the metric is stated in
    ``app.services.loan_ledger._installments``).

    Args:
        loan_id: The loan account to replay.
        scenario_id: The budget scenario scoping its payment feed.
        as_of: The evaluation date; the replay stops at the latest payment whose
            CASH had moved by it.

    Returns:
        The replayed balance owed, rounded to the cent exactly as the deleted
        ``LoanState.current_balance`` was.
    """
    # Pylint: ``import-outside-toplevel`` -- this module imports no app or ORM
    # symbols at top level (its collection-time-safety convention).
    # pylint: disable=import-outside-toplevel
    from app.services import loan_ledger, loan_loaders, loan_resolver
    from app.services.amortization_engine import slotted_dates
    from app.services.loan_resolver._periods import _replay_from_anchor
    from app.utils.money import round_money

    params = loan_loaders.load_loan_params(loan_id)
    installments = loan_ledger.payment_installments(
        loan_id, scenario_id, params, options=(), leg_options=(),
    )
    return round_money(
        _replay_from_anchor(
            anchor_events=loan_loaders.load_loan_anchor_facts(params),
            periods=loan_resolver.resolve_periods(
                params, loan_loaders.load_rate_changes(loan_id),
            ),
            payments=slotted_dates(
                [installment.dates for installment in installments],
                params.payment_day,
            ),
            payment_day=params.payment_day,
            as_of=as_of,
        ).balance_as_of
    )


def make_flat_paycheck_line(profile, name, amount, kind):
    """Build, add and flush one FLAT paycheck line of *kind* on *profile*.

    Args:
        profile: The owning :class:`~app.models.salary_profile.SalaryProfile`.
        name: The line's name.
        amount: Its per-paycheck amount, as a string.
        kind: A :class:`~app.enums.PaycheckLineKindEnum` member.

    Returns:
        The flushed :class:`~app.models.paycheck_line.PaycheckLine`.
    """
    # pylint: disable=import-outside-toplevel  -- same circular-dep
    # avoidance as the loan helpers above.
    from app import ref_cache
    from app.enums import CalcMethodEnum
    from app.extensions import db
    from app.models.paycheck_line import PaycheckLine

    line = PaycheckLine(
        salary_profile=profile,
        paycheck_line_kind_id=ref_cache.paycheck_line_kind_id(kind),
        calc_method_id=ref_cache.calc_method_id(CalcMethodEnum.FLAT),
        name=name,
        amount=Decimal(amount),
    )
    db.session.add(line)
    db.session.flush()
    return line


def build_pay_stub_world(owner):
    """The pay stub entry door's worked example (plan step salary:S11-b), committed.

    A ``$75,000.00`` profile of *owner* (base pay ``$2,884.62`` at 26 a year)
    with five flat lines: Health Insurance ``$280.00`` and Vision ``$12.00``
    (pre-tax, every paycheck), Dental ``$35.00`` (pre-tax, 12 a year: a
    month's first paycheck), Roth IRA ``$100.00`` (post-tax) and Phone
    Allowance ``$60.00`` (taxable earning).  Both of the door's suites
    (``test_pay_stub_service.py``, ``test_salary_stubs.py``) price their
    worked example over it; the owner needs pay periods for the Dental rule.

    Args:
        owner: The ``seed_user``-shaped fixture dict.

    Returns:
        ``(profile, {"health", "vision", "dental", "roth", "phone": line})``.
    """
    # pylint: disable=import-outside-toplevel  -- same circular-dep
    # avoidance as the loan helpers above.
    from app.enums import PaycheckLineKindEnum
    from app.extensions import db

    profile = make_salary_profile(owner, db.session, name="Day Job")
    db.session.flush()
    pre_tax = PaycheckLineKindEnum.PRE_TAX_DEDUCTION
    lines = {
        "health": make_flat_paycheck_line(profile, "Health Insurance", "280.00", pre_tax),
        "vision": make_flat_paycheck_line(profile, "Vision", "12.00", pre_tax),
        "dental": make_flat_paycheck_line(profile, "Dental", "35.00", pre_tax),
        "roth": make_flat_paycheck_line(
            profile, "Roth IRA", "100.00", PaycheckLineKindEnum.POST_TAX_DEDUCTION,
        ),
        "phone": make_flat_paycheck_line(
            profile, "Phone Allowance", "60.00", PaycheckLineKindEnum.TAXABLE_EARNING,
        ),
    }
    make_line_cadence_rule(db.session, lines["dental"], 12)
    db.session.commit()
    return profile, lines


class _FormControls(HTMLParser):
    """Every control of the ONE form posting to *action*, as a browser submits it.

    Repeated names (the stub form's one-off rows) keep every value in order;
    an ``<input>`` submits its ``value`` and a ``<select>`` its ``selected``
    option, else its first, and a select with no option submits nothing.  A
    control without a name submits nothing.  It reads only those two shapes:
    any other control inside the form -- an input of any type but hidden,
    text, number or date (a checkbox or radio, say), a ``<textarea>``, a named
    button, a disabled input, a disabled or multiple select, a disabled
    option or an option without a ``value`` in a named select (a browser
    posts such an option's text), or a disabled ``<fieldset>`` or
    ``<optgroup>`` -- fails the reading loudly rather than being posted as a
    browser would not post it.  A disabled fieldset fails whole, even a
    control in its first legend, which a browser does post: the safe side.
    The reader of the pay stub form's and the stub setting's route suites
    (plan step salary:S11-c-2b, its delta review's LOW-3 and LOW-4).
    """

    #: Input types a browser posts by their ``value`` alone.
    _PLAIN_INPUTS = frozenset({"hidden", "text", "number", "date"})

    def __init__(self, action):
        super().__init__()
        self.action = action
        self.controls: "dict[str, list[str]]" = {}
        self._inside = False
        self._select = None
        self._chosen = None

    def handle_starttag(self, tag, attrs):
        """Open the form, record an input, or read a select's options."""
        attributes = dict(attrs)
        if tag == "form":
            self._inside = attributes.get("action") == self.action
        elif self._inside and tag in ("textarea", "button") and attributes.get("name"):
            raise AssertionError(f"the reader does not post a named <{tag}>")
        elif self._inside and tag in ("fieldset", "optgroup") and "disabled" in attributes:
            raise AssertionError(f"the reader does not post a disabled <{tag}>")
        elif self._inside and tag == "input" and attributes.get("name"):
            plain = attributes.get("type", "text") in self._PLAIN_INPUTS
            if not plain or "disabled" in attributes:
                raise AssertionError(f"the reader does not post {attributes}")
            self.controls.setdefault(attributes["name"], []).append(attributes.get("value") or "")
        elif self._inside and tag == "select":
            if "disabled" in attributes or "multiple" in attributes:
                raise AssertionError(f"the reader does not post {attributes}")
            self._select, self._chosen = attributes.get("name"), None
        elif self._select is not None and tag == "option":
            if "value" not in attributes or "disabled" in attributes:
                raise AssertionError(f"the reader does not post the option {attributes}")
            if self._chosen is None or "selected" in attributes:
                self._chosen = attributes["value"]

    def handle_endtag(self, tag):
        """Close a select, submitting its choice, or close the form."""
        if tag == "select" and self._select is not None:
            if self._chosen is not None:
                self.controls.setdefault(self._select, []).append(self._chosen)
            self._select = None
        elif tag == "form":
            self._inside = False


def rendered_form_controls(html, action):
    """What the form posting to *action* submits, read off the rendered *html*.

    A route test posts this rather than a hand-picked payload, so a control
    the template renders is posted as a browser posts it.

    Args:
        html: A rendered page.
        action: The form's ``action``, as the page renders it.

    Returns:
        ``{name: [value, ...]}``, a repeated name's values in order.
    """
    reader = _FormControls(action)
    reader.feed(html)
    assert reader.controls, f"no form posts to {action}"
    return reader.controls


def add_test_pay_stub(profile, payday, base_pay, taxes, lines=()):
    """Seed and flush one transcribed pay stub of *profile* (plan step salary:S11-c-2c).

    The paycheck engine prices a paycheck's four taxes from a switched-on stub
    on or before it (``paycheck_calculator._stubs``); this writes the rows that
    pricing reads, around the entry door, so a case can seed a stub the door
    would refuse (one missing a tax) and states every figure itself.  A case
    wanting a one-off or the switch off sets it on the returned stub.

    Args:
        profile: The owning :class:`~app.models.salary_profile.SalaryProfile`.
        payday: The stub's date.
        base_pay: Its base pay, as a string.
        taxes: ``{WithholdingKindEnum member: amount string}``; a member left
            out records no figure for that tax.
        lines: ``(paycheck_line, amount string)`` pairs, each recorded under
            the line's own kind, or ``(paycheck_line, amount string, kind
            member)`` to record it under another (ruling **R-SAL58**).

    Returns:
        The flushed :class:`~app.models.pay_stub.PayStub`, switched on.  The
        profile's ``pay_stubs`` is expired, so the next read sees it.
    """
    # Pylint: ``import-outside-toplevel`` -- the circular-dependency avoidance
    # the loan helpers above state: this module loads before the app's models.
    # pylint: disable=import-outside-toplevel
    from app import ref_cache
    from app.extensions import db
    from app.models.pay_stub import PayStub, PayStubWithholding

    stub = PayStub(
        salary_profile_id=profile.id, payday=payday, base_pay=Decimal(base_pay),
        withholdings=[
            PayStubWithholding(
                withholding_kind_id=ref_cache.withholding_kind_id(member),
                amount=Decimal(amount),
            )
            for member, amount in taxes.items()
        ],
        line_amounts=[_stub_line_amount(entry) for entry in lines],
    )
    db.session.add(stub)
    db.session.flush()
    db.session.expire(profile, ["pay_stubs"])
    return stub


def _stub_line_amount(entry):
    """One :func:`add_test_pay_stub` line entry as its ``PayStubLineAmount`` row."""
    # Pylint: ``import-outside-toplevel`` -- as :func:`add_test_pay_stub`.
    # pylint: disable=import-outside-toplevel
    from app import ref_cache
    from app.models.pay_stub import PayStubLineAmount

    line, amount = entry[0], entry[1]
    return PayStubLineAmount(
        paycheck_line_id=line.id,
        paycheck_line_kind_id=(
            ref_cache.paycheck_line_kind_id(entry[2]) if len(entry) > 2
            else line.paycheck_line_kind_id
        ),
        amount=Decimal(amount),
    )
