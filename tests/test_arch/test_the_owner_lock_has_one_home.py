"""Architecture test: the owner's write lock is taken where a writing transaction BEGINS, and nowhere else.

Plan step ``balance:X-bn``, rulings **credit_card:R-CC106** ("one writer per
owner: every save takes your write lock before it reads anything, in the one
module that opens each request's database transaction"), **R-CC114** (page
loads' writes take it too), **R-CC115** ("The lock is taken only where
database work starts ... The older calls go, and a test fails the build if
anything else takes the lock") and **R-CC121** (a sign-in takes it right after
finding the account).  This is that test.

Why a second home is a defect and not a harmless repeat
-------------------------------------------------------

Before the step, nineteen calls in ten modules took the lock at their own
point in a transaction: sixteen ``lock_user_writes`` calls in nine modules,
and three that plan step ``credit_card:CC-5-4a-4`` put inside
``row_write_lock``.  Each was a claim that ITS read was the one that needed
serialising, and a census over the whole suite found 27 endpoints that had
already locked a row by the time they reached one -- the order that deadlocks
against a door holding the owner's lock and waiting on that row.  Taken where
the transaction begins, the lock precedes every read of the owner's data and
every row lock by construction, so a later acquisition can only be a repeat --
and a repeat reads as load-bearing to the next author, who then "fixes" a
path by adding another at the wrong depth.

What this test enforces
-----------------------

Over every Python module under ``app/``, ``scripts/`` and ``migrations/``, and
the repository root's own (``gunicorn.conf.py``, ``run.py``):

* Each name that takes the lock, or reaches a function that does, is
  referenced only by its homes (:data:`_HOMES`): the one statement of the lock
  (``take_owner_write_lock``) by ``app/db_transaction.py``, its definer
  ``app/services/user_write_lock.py`` calling it from nowhere; the lock's KEY
  (``_USER_WRITE_LOCK_NAMESPACE``)
  by ``user_write_lock.py`` alone, so no module can take the same lock under
  its own statement; ``bind_request_actor`` (where a request's owner becomes
  known) by the logging hook that signs the request in;
  ``bind_sign_in_owner`` by the two sign-in routes; the two private
  functions that do the locking by ``db_transaction`` itself; and the two
  ``g`` keys that arm the listener for an owner -- ``_OWNER_KEY`` and
  ``_PENDING_OWNER_KEY`` reached as ``db_transaction``'s attributes or
  imported from it, and their string values as a string or as a ``g``
  attribute -- by nothing outside ``db_transaction``.
* Every PostgreSQL advisory-lock function -- any name or attribute spelled
  ``pg_advisory...`` or ``pg_try_advisory...``, and any non-docstring string
  that names one, which is a raw SQL statement -- appears only in
  ``user_write_lock.py``.
* The names ``lock_user_writes`` -- the deleted per-service form -- and
  ``lock_every_user_writes`` -- the deleted every-owner form, which the
  deploy's three ledger reconciles took until plan task plan#3077 deleted
  them -- are bound nowhere.
* **The homes cannot grow a new door.**  ``user_write_lock.py`` binds
  exactly its namespace and the statement at module level -- no lambda,
  class or alias -- so a helper wrapping the statement under a new name (the
  ``lock_user_writes`` shape again) fails here rather than passing as
  "inside a home".  And inside EVERY home, the functions allowed to
  reference each locking name are pinned by name
  (:data:`_PINNED_CALLERS`): ``db_transaction``'s listener and locker, and
  no function of ``user_write_lock`` -- so a loop there taking every owner's
  lock (the every-owner form again, the deadlock its ascending order existed
  to prevent if two requests took it) fails too.

**What it does not see, stated rather than implied**: a lock reached through
``getattr``, or a statement assembled from pieces at run time, which nothing
catches and no honest module writes; and a reference in a decorator or a
default argument is attributed to the function it decorates.  It also cannot prove the lock is taken,
or taken FIRST, on a real request; that is :mod:`app.db_transaction`'s
construction, measured on real requests by
``tests/test_services/test_user_write_lock.py`` (the command arm takes it, a
render takes none, a ``write_transaction`` block does, a companion takes its
owner's key) and ``tests/test_routes/test_xbn_sign_in_lock.py`` (sign-in, the
refusal checks, the request's end).

Why AST, not grep
-----------------

Every one of these names appears in prose -- the docstrings that explain the
lock say where it used to be taken -- and a grep would flag the sentences that
state the rule.  Name, attribute and import nodes cannot be prose, and a
string is counted only when it is not a docstring.
"""

import ast
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

_DB_TRANSACTION = "app/db_transaction.py"
_USER_WRITE_LOCK = "app/services/user_write_lock.py"

_PRIMITIVE = "take_owner_write_lock"
_EVERY_OWNER = "lock_every_user_writes"
_NAMESPACE = "_USER_WRITE_LOCK_NAMESPACE"
_REQUEST_OWNER = "bind_request_actor"
_SIGN_IN_OWNER = "bind_sign_in_owner"
_LOCK_OPEN = "_lock_the_open_transaction"
_REQUEST_HOOK = "_take_the_request_owner_s_lock"
_ARMED_OWNER = "_OWNER_KEY"
_PENDING_OWNER = "_PENDING_OWNER_KEY"
_DELETED = "lock_user_writes"

#: The one entry that stands for the two ``g`` keys' STRING values, so
#: ``setattr(g, "shekel_write_owner", ...)`` is a reference like the name.
_OWNER_KEY_STRING = "owner-key string"
_OWNER_KEY_VALUES = frozenset({"shekel_write_owner", "shekel_pending_write_owner"})

#: Names counted only as an attribute or an import, never as a bare name.
_OUTSIDE_ONLY = frozenset({_ARMED_OWNER, _PENDING_OWNER})

#: The one entry that stands for EVERY advisory-lock spelling, so the home
#: table reads the statement's rule in the same place as the names'.
_STATEMENT = "pg_*advisory*"

#: Any PostgreSQL advisory-lock function: ``pg_advisory_xact_lock``,
#: ``pg_advisory_lock``, ``pg_try_advisory_xact_lock`` and the rest.
_STATEMENT_PATTERN = re.compile(r"\bpg_(?:try_)?advisory", re.IGNORECASE)

#: Each watched name, and the modules that may reference it.
_HOMES: dict[str, frozenset[str]] = {
    _PRIMITIVE: frozenset({_DB_TRANSACTION}),
    # Deleted at plan task plan#3077 with its only callers, the deploy's three
    # ledger reconciles: like ``lock_user_writes`` below, no module may bind it.
    _EVERY_OWNER: frozenset(),
    _NAMESPACE: frozenset({_USER_WRITE_LOCK}),
    _REQUEST_OWNER: frozenset({"app/utils/logging_config.py"}),
    _SIGN_IN_OWNER: frozenset({
        "app/routes/auth/credentials.py",
        "app/routes/auth/mfa.py",
    }),
    _LOCK_OPEN: frozenset({_DB_TRANSACTION}),
    _REQUEST_HOOK: frozenset({_DB_TRANSACTION}),
    # Reached from OUTSIDE db_transaction only (an attribute of it, or an
    # import from it): a bare name is any module's own constant -- the 4a-4
    # migration has an unrelated ``_OWNER_KEY`` -- and db_transaction's own
    # uses are its string values' home below.
    _ARMED_OWNER: frozenset(),
    _PENDING_OWNER: frozenset(),
    _OWNER_KEY_STRING: frozenset({_DB_TRANSACTION}),
    _STATEMENT: frozenset({_USER_WRITE_LOCK}),
    _DELETED: frozenset(),
}

#: Every name ``user_write_lock.py`` may bind at module level: the lock's key
#: and the statement (the every-owner form built on it went at plan task
#: plan#3077).
_USER_WRITE_LOCK_BINDINGS = frozenset({_NAMESPACE, _PRIMITIVE})

#: Inside each home: which functions may reference each locking name.  In
#: ``db_transaction`` the listener's COMMAND arm and the open-transaction lock
#: take the statement, the before-request hook and the sign-in door call the
#: open-transaction lock, and only the boundary registers the hook; in
#: ``user_write_lock`` no function calls the statement (the every-owner form
#: did, until plan task plan#3077 deleted it with the deploy's reconciles).
_PINNED_CALLERS: dict[str, dict[str, frozenset[str]]] = {
    _DB_TRANSACTION: {
        _PRIMITIVE: frozenset({"_bind_transaction_mode", _LOCK_OPEN}),
        _LOCK_OPEN: frozenset({_REQUEST_HOOK, _SIGN_IN_OWNER}),
        _REQUEST_HOOK: frozenset({"register_transaction_boundary"}),
    },
    _USER_WRITE_LOCK: {_PRIMITIVE: frozenset()},
}


def _docstring_nodes(tree: ast.AST) -> set[int]:
    """Return the ids of every string node that is a statement on its own.

    A module, class or function docstring is such a statement, and so is any
    bare string -- neither can be executed as SQL, so neither is a reference.

    Args:
        tree: The parsed module.

    Returns:
        ``id()`` of each such :class:`ast.Constant`.
    """
    return {
        id(node.value)
        for node in ast.walk(tree)
        if isinstance(node, ast.Expr)
        and isinstance(node.value, ast.Constant)
        and isinstance(node.value.value, str)
    }


def _watched(name: str) -> str | None:
    """Return the :data:`_HOMES` key *name* is counted under, or ``None``."""
    if name in _HOMES:
        return name
    if _STATEMENT_PATTERN.search(name):
        return _STATEMENT
    return None


def _references(source: str, filename: str) -> list[tuple[str, int]]:
    """Return ``(key, line)`` for every reference to a watched name in *source*.

    A reference is a bare name, an attribute read off anything (the
    ``user_write_lock.take_owner_write_lock`` and ``func.pg_advisory_xact_lock``
    spellings), a name an import binds -- so an import that is never called
    still counts, which is the point: binding the lock is the start of taking
    it -- or a non-docstring string naming an advisory-lock function, which is
    a raw statement.

    Args:
        source: The module's text.
        filename: Passed to the parser so a syntax error names the file.

    Returns:
        One pair per reference, in line order (``ast.walk`` is breadth-first,
        so its own order is not the file's).
    """
    tree = ast.parse(source, filename=filename)
    docstrings = _docstring_nodes(tree)
    found: list[tuple[str, int]] = []
    for node in ast.walk(tree):
        names: list[str] = []
        if isinstance(node, ast.Name) and node.id not in _OUTSIDE_ONLY:
            names = [node.id]
        elif isinstance(node, ast.Attribute):
            names = [node.attr]
            if node.attr in _OWNER_KEY_VALUES:
                found.append((_OWNER_KEY_STRING, node.lineno))
        elif isinstance(node, ast.ImportFrom):
            names = [alias.name for alias in node.names]
        elif (
            isinstance(node, ast.Constant)
            and isinstance(node.value, str)
            and id(node) not in docstrings
        ):
            if _STATEMENT_PATTERN.search(node.value):
                found.append((_STATEMENT, node.lineno))
            if node.value in _OWNER_KEY_VALUES:
                found.append((_OWNER_KEY_STRING, node.lineno))
        for name in names:
            key = _watched(name)
            if key is not None:
                found.append((key, node.lineno))
    return sorted(found, key=lambda pair: (pair[1], pair[0]))


def _violations(relative: str, source: str) -> list[str]:
    """Return one message per reference *relative* is not a home for.

    Args:
        relative: The module's path relative to the repository root, with
            forward slashes -- the key the home sets are written in.
        source: The module's text.

    Returns:
        Empty when every reference sits in a home for its name.
    """
    return [
        f"{relative}:{line} {key}"
        for key, line in _references(source, relative)
        if relative not in _HOMES[key]
    ]


def _module_bindings(source: str) -> set[str]:
    """Return every name *source* binds at module level, imports aside.

    A function, a class, and an assignment's or annotated assignment's plain
    name targets -- so a lambda or an alias bound at the top of a module is
    counted like a ``def``.
    """
    bound: set[str] = set()
    for node in ast.parse(source).body:
        if isinstance(
            node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef),
        ):
            bound.add(node.name)
        elif isinstance(node, (ast.Assign, ast.AnnAssign)):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            bound.update(
                target.id for target in targets if isinstance(target, ast.Name)
            )
    return bound


def _callers(source: str, name: str) -> set[str]:
    """Return every function whose OWN body references *name*.

    Owned by the innermost enclosing function, so a hook defined inside
    ``register_transaction_boundary`` is its own caller rather than its
    parent's.  A reference outside any function is reported as
    ``"<module>"``.

    Args:
        source: The module's text.
        name: The name or attribute to look for.

    Returns:
        The enclosing function names.
    """
    owners: set[str] = set()

    def visit(node: ast.AST, owner: str) -> None:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            owner = node.name
        elif (isinstance(node, ast.Name) and node.id == name) or (
            isinstance(node, ast.Attribute) and node.attr == name
        ):
            owners.add(owner)
        for child in ast.iter_child_nodes(node):
            visit(child, owner)

    visit(ast.parse(source), "<module>")
    return owners


def _surface_violations(sources: dict[str, str]) -> list[str]:
    """Return one message per new door inside a home module.

    Args:
        sources: Each pinned home's text, keyed by its path (every key of
            :data:`_PINNED_CALLERS`).

    Returns:
        Empty when ``user_write_lock`` binds exactly its two names and
        every home references each locking name from exactly the functions
        pinned above.
    """
    found: list[str] = []
    bound = _module_bindings(sources[_USER_WRITE_LOCK])
    if bound != _USER_WRITE_LOCK_BINDINGS:
        found.append(
            f"{_USER_WRITE_LOCK} binds {sorted(bound)}, "
            f"not {sorted(_USER_WRITE_LOCK_BINDINGS)}"
        )
    for relative, pins in _PINNED_CALLERS.items():
        for name, allowed in pins.items():
            callers = _callers(sources[relative], name)
            if callers != allowed:
                found.append(
                    f"{relative}: {name} is referenced from "
                    f"{sorted(callers)}, not {sorted(allowed)}"
                )
    return found


def _home_sources() -> dict[str, str]:
    """Every pinned home's text on the real tree, keyed by its path."""
    return {
        relative: (ROOT / relative).read_text(encoding="utf-8")
        for relative in _PINNED_CALLERS
    }


def _scanned_modules() -> list[Path]:
    """Every Python module under ``app/``, ``scripts/``, ``migrations/`` and at the root, ``__pycache__`` excluded."""
    return sorted(
        [
            path
            for top in ("app", "scripts", "migrations")
            for path in (ROOT / top).rglob("*.py")
            if "__pycache__" not in path.parts
        ] + list(ROOT.glob("*.py"))
    )


class TestTheOwnerLockHasOneHome:
    """The rule on the real tree, and the scanner's own firing arms."""

    def test_the_scan_reaches_every_home(self) -> None:
        """A scan that misses a home would pass for nothing; pin its reach."""
        scanned = {
            path.relative_to(ROOT).as_posix() for path in _scanned_modules()
        }
        missing = set().union(*_HOMES.values()) - scanned
        assert not missing, f"homes the scan does not reach: {sorted(missing)}"
        assert "scripts/init_database.py" in scanned
        assert "migrations/env.py" in scanned
        assert "gunicorn.conf.py" in scanned
        assert any(path.startswith("migrations/versions/") for path in scanned)

    def test_each_home_still_holds_its_name(self) -> None:
        """A home that no longer references its name is a stale allowance.

        Every allowance below is one place the lock may be taken; an entry
        whose module stopped taking it would let a new module of that path
        take it unseen.
        """
        stale: list[str] = []
        for key, homes in _HOMES.items():
            for relative in sorted(homes):
                source = (ROOT / relative).read_text(encoding="utf-8")
                if not any(
                    found == key for found, _ in _references(source, relative)
                ):
                    stale.append(f"{relative} no longer references {key}")
        assert not stale, "\n".join(stale)

    def test_nothing_else_takes_the_owner_lock(self) -> None:
        """Censused at plan step ``balance:X-bn``: nineteen calls in ten modules were deleted."""
        violations: list[str] = []
        for path in _scanned_modules():
            relative = path.relative_to(ROOT).as_posix()
            violations.extend(
                _violations(relative, path.read_text(encoding="utf-8"))
            )
        assert not violations, (
            "The owner's write lock is taken where a writing transaction "
            "BEGINS (app/db_transaction.py) and at a sign-in, and nowhere "
            "else (rulings "
            "R-CC106, R-CC115, R-CC121).  An acquisition deeper in a "
            "transaction can only repeat a lock already held -- or, off a "
            "request, be the only one, which is a writer that must take it "
            "at its own start instead.  Sites:\n" + "\n".join(violations)
        )

    def test_the_homes_grow_no_new_door(self) -> None:
        """Each home defines, and calls the lock from, exactly the pinned functions."""
        violations = _surface_violations(_home_sources())
        assert not violations, "\n".join(violations)

    def test_the_scanner_catches_each_spelling(self, tmp_path: Path) -> None:
        """Every watched name, each advisory spelling, a raw statement, and the deleted name."""
        scratch = tmp_path / "takes_it_again.py"
        scratch.write_text(
            "from app.services.user_write_lock import take_owner_write_lock\n"
            "from app.services import user_write_lock\n"
            "from sqlalchemy import func, select, text\n"
            "user_write_lock.lock_every_user_writes()\n"
            "select(func.pg_advisory_xact_lock(1, 2))\n"
            "lock_user_writes(3)\n"
            "select(func.pg_try_advisory_xact_lock(user_write_lock._USER_WRITE_LOCK_NAMESPACE, 4))\n"
            "text('SELECT pg_advisory_lock(5)')\n"
            "from app.db_transaction import bind_request_actor, bind_sign_in_owner\n"
            "from app.db_transaction import _lock_the_open_transaction\n"
            "app.before_request(_take_the_request_owner_s_lock)\n"
            "setattr(g, db_transaction._OWNER_KEY, 12)\n"
            "g.shekel_pending_write_owner = 13\n"
            "setattr(g, 'shekel_write_owner', 14)\n",
            encoding="utf-8",
        )
        assert _violations(
            "app/services/some_service.py",
            scratch.read_text(encoding="utf-8"),
        ) == [
            "app/services/some_service.py:1 take_owner_write_lock",
            "app/services/some_service.py:4 lock_every_user_writes",
            "app/services/some_service.py:5 pg_*advisory*",
            "app/services/some_service.py:6 lock_user_writes",
            "app/services/some_service.py:7 _USER_WRITE_LOCK_NAMESPACE",
            "app/services/some_service.py:7 pg_*advisory*",
            "app/services/some_service.py:8 pg_*advisory*",
            "app/services/some_service.py:9 bind_request_actor",
            "app/services/some_service.py:9 bind_sign_in_owner",
            "app/services/some_service.py:10 _lock_the_open_transaction",
            "app/services/some_service.py:11 _take_the_request_owner_s_lock",
            "app/services/some_service.py:12 _OWNER_KEY",
            "app/services/some_service.py:13 owner-key string",
            "app/services/some_service.py:14 owner-key string",
        ]

    def test_a_home_is_a_home_for_its_own_name_only(self, tmp_path: Path) -> None:
        """``db_transaction`` may take the primitive, not the every-owner form."""
        scratch = tmp_path / "request_boundary.py"
        scratch.write_text(
            "from app.services.user_write_lock import take_owner_write_lock\n"
            "from app.services.user_write_lock import lock_every_user_writes\n",
            encoding="utf-8",
        )
        assert _violations(
            _DB_TRANSACTION, scratch.read_text(encoding="utf-8"),
        ) == ["app/db_transaction.py:2 lock_every_user_writes"]

    def test_a_wrapper_inside_a_home_is_a_new_door(self) -> None:
        """The ``lock_user_writes`` shape under a new name, in any home, fails.

        The four shapes a neutral review measured passing an earlier cut of
        this test (2026-09-29): a lambda and a class method wrapping the
        statement in ``user_write_lock``, a second caller of the
        open-transaction locker in ``db_transaction``, and a new
        request-path function in ``posting_service`` taking every owner's
        lock.  The fourth's home went at plan task plan#3077 with the
        every-owner form, so it is measured here as that form's own shape
        reborn: a loop in ``user_write_lock`` calling the statement per owner.
        """
        real = _home_sources()
        assert _surface_violations(real) == []

        def mutated(relative: str, tail: str) -> list[str]:
            sources = dict(real)
            sources[relative] = sources[relative] + tail
            return _surface_violations(sources)

        lam = "\n\nlock_owner = lambda owner_id: take_owner_write_lock(None, owner_id)\n"
        assert mutated(_USER_WRITE_LOCK, lam) == [
            f"{_USER_WRITE_LOCK} binds "
            f"{sorted(_USER_WRITE_LOCK_BINDINGS | {'lock_owner'})}, "
            f"not {sorted(_USER_WRITE_LOCK_BINDINGS)}",
            f"{_USER_WRITE_LOCK}: {_PRIMITIVE} is referenced from "
            f"{['<module>']}, not []",
        ]
        klass = (
            "\n\nclass OwnerLock:\n"
            "    def take(self, owner_id):\n"
            "        take_owner_write_lock(None, owner_id)\n"
        )
        assert mutated(_USER_WRITE_LOCK, klass) == [
            f"{_USER_WRITE_LOCK} binds "
            f"{sorted(_USER_WRITE_LOCK_BINDINGS | {'OwnerLock'})}, "
            f"not {sorted(_USER_WRITE_LOCK_BINDINGS)}",
            f"{_USER_WRITE_LOCK}: {_PRIMITIVE} is referenced from "
            f"{['take']}, not []",
        ]
        second_caller = (
            "\n\ndef lock_again(owner_id):\n"
            "    _lock_the_open_transaction(None, owner_id)\n"
        )
        assert mutated(_DB_TRANSACTION, second_caller) == [
            f"{_DB_TRANSACTION}: {_LOCK_OPEN} is referenced from "
            f"{sorted({_REQUEST_HOOK, _SIGN_IN_OWNER, 'lock_again'})}, "
            f"not {sorted({_REQUEST_HOOK, _SIGN_IN_OWNER})}",
        ]
        every_owner = (
            "\n\ndef lock_every_owner(connection, owner_ids):\n"
            "    for owner_id in owner_ids:\n"
            "        take_owner_write_lock(connection, owner_id)\n"
        )
        assert mutated(_USER_WRITE_LOCK, every_owner) == [
            f"{_USER_WRITE_LOCK} binds "
            f"{sorted(_USER_WRITE_LOCK_BINDINGS | {'lock_every_owner'})}, "
            f"not {sorted(_USER_WRITE_LOCK_BINDINGS)}",
            f"{_USER_WRITE_LOCK}: {_PRIMITIVE} is referenced from "
            f"{['lock_every_owner']}, not []",
        ]

    def test_prose_is_not_a_reference(self, tmp_path: Path) -> None:
        """The docstrings that say where the lock used to be taken stay legal."""
        scratch = tmp_path / "explains_it.py"
        scratch.write_text(
            '"""It called ``lock_user_writes`` and took ``pg_advisory_xact_lock``."""\n'
            "# take_owner_write_lock is db_transaction's\n"
            "def f():\n"
            '    """Once took ``pg_advisory_xact_lock`` itself."""\n',
            encoding="utf-8",
        )
        assert not _violations(
            "app/services/some_service.py",
            scratch.read_text(encoding="utf-8"),
        )
