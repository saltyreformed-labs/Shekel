"""Tests for the E-19 / Commit 3 account-anchor invariant.

Migration ``cfb15e782f86`` made ``budget.accounts.current_anchor_balance``
and ``budget.accounts.current_anchor_period_id`` NOT NULL after
backfilling existing rows, and seeded a matching
``budget.account_anchor_history`` row for each.  **Ruling R-EH deleted both
columns at plan step X-f1c3c** -- they were a denormalized copy of that
history row -- so what remains of E-19 is the half that was always the fact:
every account carries an origination ASSERTION from the moment it exists, and
:func:`app.services.cash_ledger.resolve_anchor` can answer for it.  That is
what deletes CRIT-01's four NULL-anchor forks now.

The tests exercise three layers of the contract:

  1. **Migration backfill** (C3-4) -- load the migration module dynamically
     and assert its ``DIAGNOSTIC_SELECT`` names the columns an operator needs
     when the backfill cannot resolve a row.

     **The two DATA-PATH cases (C3-1 / C3-2) were DELETED at plan step
     X-f1c3b**, under the precedent the developer set on 2026-08-03 for the 22
     historical-migration tests that migration ``a3f7c8e21b64`` stranded.  They
     ran ``cfb15e782f86``'s frozen ``INSERT_HISTORY_SQL`` verbatim against a
     database at HEAD, and that string inserts
     ``account_anchor_history.pay_period_id``, which ruling R-EO deletes.  The
     same three measured facts carry the deletion here: a real ``base -> head``
     upgrade is unaffected (``cfb15e782f86`` runs ~30 revisions before the
     drop, against a schema that still has the column -- verified by a clean
     template rebuild); on any database already past it the migration never
     runs again; and rewinding to it requires downgrading through
     ``a3f7c8e21b64``, whose downgrade REFUSES once any row carries a settle
     day.  **The backfill's data path is unreachable with data, permanently.**
     The alternative -- a fixture that ADDS the dropped column back to
     reconstruct a historical schema -- is the shape that ruling declined, and
     it is a larger reconstruction than the ``DROP NOT NULL`` the deleted
     fixture performed.

  2. **Model rejection** (C3-6) -- DELETED at plan step X-f1c3c.  It flushed
     an ``Account`` with NULL anchor columns and asserted ``IntegrityError``;
     ruling R-EH deleted the columns, so the state it refused is not
     expressible and the ``NOT NULL`` it graded does not exist.  The invariant
     that survives -- every account has a resolvable balance -- is now the
     ORIGINATION ASSERTION, graded by the creation-path cases below and by
     ``scripts/integrity_check.py``'s re-pointed BA-01.

  3. **Creation paths** (C3-5) -- the ``registration_service.register_user``
     signup path and the ``/accounts`` POST route both write the
     origination ``AccountAnchorHistory`` row at the moment the account
     exists.  Locks the spec contract "always create the origination
     assertion".  It also asserted the ``current_anchor_*`` columns until
     ruling R-EH deleted them, and the assertion's own pay period until ruling
     R-EO deleted that; what is asserted now is the balance and the DAY.

**A WHOLE SUITE was deleted at plan step X-f1c3c and the record belongs here**,
because this file owns what remains of the contract it graded.
``tests/test_models/test_anchor_fk_deferrable.py`` (264 lines) asserted that
``accounts.current_anchor_period_id``'s foreign key was
``NO ACTION DEFERRABLE INITIALLY IMMEDIATE`` -- the catalog state, the mapper
declaration, the ``SET CONSTRAINTS ... DEFERRED`` round-trip, and the
``IntegrityError`` a period delete raised while an account still pointed at it.
**Every one of those is a property of a column that no longer exists.**  The FK,
its deferrability and the ``_DEFER_ANCHOR_FK_SQL`` the pay-period reset used are
deleted with it (rulings R-EH and R-EO), so there is nothing left to assert: not
a weakened rule, an absent one.  What the deferral existed to PROTECT -- a
balance surviving a schedule rebuild -- is graded by
``tests/test_services/test_pay_period_reset/test_the_reset.py``'s
``test_wipes_all_and_the_balance_survives`` and
``test_the_reset_preserves_every_balance_assertion``, and the migration's own
restore of the FK is graded by
``tests/test_models/test_anchor_cache_downgrade.py``.
"""
# pylint: disable=redefined-outer-name
# Rationale: ``redefined-outer-name`` is the canonical pytest fixture
# pattern; the test bodies receive fixtures via name binding.
from __future__ import annotations

import importlib.util
import pathlib
import re
from datetime import timedelta
from decimal import Decimal


from app.extensions import db as _db
from app.models.account import Account, AccountAnchorHistory
from app.models.pay_period import PayPeriod
from app.models.ref import AccountType
from app.models.scenario import Scenario
from app.models.user import User, UserSettings
from app.services import cash_ledger
from app.services import registration_service
from app.services.auth_service import hash_password
from app.utils.dates import display_today
from tests._test_helpers import (
    current_pay_period,
    last_covered_day,
    registration_spec,
)


# ---------------------------------------------------------------------------
# Migration module loader (mirrors the pattern in test_c40_account_id_backfill)
# ---------------------------------------------------------------------------


_MIGRATIONS_DIR = (
    pathlib.Path(__file__).resolve().parents[2] / "migrations" / "versions"
)


def _load_migration(filename: str):
    """Load an Alembic migration file by path via importlib.

    The ``migrations/versions`` directory has no ``__init__.py``, so a
    regular import does not work.  We mirror Alembic's own loader so
    the test can read module-level constants (``BACKFILL_BALANCE_SQL``,
    ``BACKFILL_PERIOD_SQL``, ``INSERT_HISTORY_SQL``, ``DIAGNOSTIC_SELECT``)
    directly and run the exact text the production migration runs.
    """
    path = _MIGRATIONS_DIR / filename
    spec = importlib.util.spec_from_file_location(path.stem, str(path))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_M_ANCHOR_BACKFILL = _load_migration(
    "cfb15e782f86_backfill_account_anchor_and_tighten_.py"
)


# ---------------------------------------------------------------------------
# Per-test fixture: re-widen the anchor columns to nullable so we can
# insert engineered NULL rows, then restore NOT NULL on teardown.
# The CHECK constraint must also be dropped/recreated; PG raises if
# the column is widened to nullable while a CHECK on IS NOT NULL is
# attached.
# ---------------------------------------------------------------------------




# ---------------------------------------------------------------------------
# C3-6 -- Model-level NOT NULL enforcement.  This test does NOT require
# the nullable_anchor_columns fixture because we expect the IntegrityError
# at flush time (the constraint should fire).
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# C3-1 / C3-2 / C3-3 -- Migration backfill behaviour.  These tests
# re-widen the columns to nullable, insert engineered rows with NULL
# anchors, run the backfill SQL the migration uses, and assert the
# resolved state.
# ---------------------------------------------------------------------------


class TestMigrationBackfill:
    """C3-1/2/3: migration backfill resolves NULLs or raises clearly."""



    def test_diagnostic_select_contains_unresolved_columns(self):
        """C3-3: DIAGNOSTIC_SELECT names the offending account columns.

        The migration's diagnostic SELECT must be valid SQL that
        enumerates account_id, user_id, name, and both anchor columns
        for any row still NULL.  Verified textually (the migration's
        SQL is also exercised by the backfill tests above).  The
        check guards against an operator deleting the diagnostic
        without realising it is part of the RuntimeError message.
        """
        sql = _M_ANCHOR_BACKFILL.DIAGNOSTIC_SELECT
        assert "account_id" in sql
        assert "user_id" in sql
        # The migration's frozen SQL names the columns it ran against; the
        # columns are gone (ruling R-EH) and a historical migration is never
        # edited to match a newer schema, so this asserts the DIAGNOSTIC still
        # names what an operator needed at the time.
        assert "current_anchor_balance" in sql
        assert "current_anchor_period_id" in sql
        assert "IS NULL" in sql


# ---------------------------------------------------------------------------
# C3-5 -- Creation paths always write a non-NULL anchor and a matching
# AccountAnchorHistory row.
# ---------------------------------------------------------------------------


class TestCreationPathsWriteAnchor:
    """C3-5: register_user and POST /accounts always set anchor + history."""

    def test_register_user_creates_anchor_and_history(self, app, db):
        """The registration_service.register_user signup path builds the owner's REAL
        pay calendar, anchors the default Checking account with a
        Decimal("0.00") balance, and writes an origination
        AccountAnchorHistory row.

        Arithmetic: the owner states they were last paid 6 days ago at a
        14-day cadence, so period 0 spans ``[today - 6, today + 7]`` and
        CONTAINS sign-up day.  The Checking account's origination ASSERTION
        carries ``anchor_balance=Decimal("0.00")`` observed on sign-up day --
        which is no longer the same date as the period's start, and that
        distinction is the point of the step.

        **This assertion changed at plan step X-ad-a, on ruling R-DB, and the
        old one is recorded here rather than deleted.**  It read
        ``period.start_date == signup_day`` and
        ``period.end_date == signup_day + 13`` because registration FABRICATED
        a pay period covering ``[today, today + 13]`` -- and that invented
        payday is exactly what finding **N-123** traces: it made thirteen of
        the fourteen following paydays unenterable and left a permanent
        calendar hole for any later one.  Registration now asks for the day the
        owner was last paid, so the schedule opens on a real payday in the
        past and the opening assertion is dated today.

        **"Today" here is the USER's, not the process's** (finding R2,
        ``anchor_settle_partition.md`` Section 9).  ``register_user`` reads
        :func:`~app.utils.dates.display_today` ONCE and uses it for both the
        payday bound and the origination assertion's day.  Asserting
        ``date.today()`` here pinned the PROCESS zone instead: it passes in a
        dev shell running Eastern and FAILS in CI, which runs UTC, for the four
        hours a day the two calendars disagree -- which is exactly how it
        failed the merge gate at 03:56 UTC on 2026-08-01, reading
        ``2026-07-31 != 2026-08-01``.
        """
        from app.services import auth_service

        signup_day = display_today()
        last_payday = signup_day - timedelta(days=6)
        with app.app_context():
            user = registration_service.register_user(registration_spec(
                email="c3-5@example.com",
                password="strong-pass-12345",
                display_name="C3-5 Tester",
                first_payday=last_payday,
                cadence_days=14,
                num_periods=3,
            ))
            db.session.commit()

            account = db.session.query(Account).filter_by(
                user_id=user.id, name="Checking",
            ).one()
            # Period 0 opens on the stated payday and runs one cadence, so it
            # spans [today - 6, today + 7] and contains sign-up day.  Three
            # periods were asked for and three exist -- registration builds a
            # whole schedule now, not one placeholder.
            periods = db.session.query(PayPeriod).filter_by(
                user_id=user.id,
            ).order_by(PayPeriod.start_date).all()
            assert len(periods) == 3
            assert periods[0].start_date == last_payday
            assert last_covered_day(periods[0]) == last_payday + timedelta(days=13)
            assert periods[0].start_date <= signup_day <= last_covered_day(periods[0])

            histories = db.session.query(AccountAnchorHistory).filter_by(
                account_id=account.id,
            ).all()
            assert len(histories) == 1
            # The account's whole anchor state IS this row (rulings R-EH and
            # R-EO): a balance and the day it was true, with no period beside
            # it and no column mirroring it.  The day is SIGN-UP day -- the
            # owner is asserting what their account holds now, not what it held
            # on their last payday -- and it falls inside period 0 above, which
            # is what ties the two halves of the signup path to one clock.
            assert histories[0].observed_on == signup_day
            assert histories[0].anchor_balance == Decimal("0.00")
            # Nothing about PROVENANCE is asserted on the row: ruling R-ES
            # deleted the ``notes`` column this line used to read
            # (``"origination" in histories[0].notes``).  The assertion is
            # (account, day, balance); which door wrote it is
            # ``system.audit_log``'s to answer, and that this path writes it
            # through the one door is graded structurally by
            # ``TestTheAssertionTableHasOneWriter`` below.
            assert cash_ledger.resolve_anchor(account).balance == Decimal("0.00")

    def test_create_account_route_writes_anchor_and_history(
        self, app, db, auth_client, seed_user, seed_periods_today
    ):
        """POST /accounts creates an account with the anchor period
        set to the current pay period and writes a matching
        AccountAnchorHistory row.

        Arithmetic: ``seed_periods_today`` places today in period 4
        of the seed_user's period set.  The route resolves the period
        CONTAINING the owner's day and uses it as the anchor.  The submitted anchor_balance is
        ``$1500.00`` and must appear verbatim on both the column
        and the history row.
        """

        with app.app_context():
            savings_type = (
                db.session.query(AccountType).filter_by(name="Savings").one()
            )
            current_period = current_pay_period(
                seed_user["user"].id
            )
            assert current_period is not None

            resp = auth_client.post("/accounts", data={
                "name": "C3-5 Savings",
                "account_type_id": str(savings_type.id),
                "anchor_balance": "1500.00",
            })
            assert resp.status_code in (302, 303), resp.data[:200]

            account = db.session.query(Account).filter_by(
                user_id=seed_user["user"].id, name="C3-5 Savings",
            ).one()
            assert cash_ledger.resolve_anchor(account).balance == Decimal("1500.00")

            history = db.session.query(AccountAnchorHistory).filter_by(
                account_id=account.id,
            ).one()
            # The assertion is a DAY and a balance (ruling R-EO); the period
            # asserted above is the account's cache column, resolved from it.
            assert (
                current_period.start_date
                <= history.observed_on
                <= last_covered_day(current_period)
            )
            assert history.anchor_balance == Decimal("1500.00")


# ---------------------------------------------------------------------------
# X-f1e2 / ruling R-ES -- the assertion table has ONE writer, and the row
# carries no provenance label.
# ---------------------------------------------------------------------------


_REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]

#: The trees a production writer could live in.  ``scripts/`` is in scope
#: because those run against the real database.
_PRODUCTION_TREES = ("app", "scripts")

#: The modules and functions permitted to write an ``AccountAnchorHistory``
#: row, as ``(path relative to the repository root, enclosing def)`` -- ONE
#: per kind of row the level relation holds (plan step ``balance:X-bj-1``,
#: rulings **R-IS** / **R-JN**).  The owner's declaration is
#: ``stage_anchor_true_up`` (ruling **R-ES**: the owner's write lock, the
#: R-EQ did-this-change compare and the shared log line are properties of
#: that door); the bank's placement is ``record_statement``, which solves the
#: day from the file's lines and writes the level naming the import, keyed to
#: the file's own claim.  A third writer is what this gate exists to catch.
_WRITERS = frozenset({
    ("app/services/anchor_service.py", "stage_anchor_true_up"),
    ("app/services/statement_import/_record.py", "record_statement"),
})

_MODEL = "AccountAnchorHistory"
_TABLE = "account_anchor_history"

#: SQLAlchemy helpers that write rows without constructing the ORM object.
_BULK_WRITERS = frozenset({"bulk_insert_mappings", "bulk_save_objects"})

#: SQL verbs that MUTATE, each ADJACENT to the table's name: a literal is a
#: write when a verb governs this table, not when a string that mentions the
#: table somewhere also mentions a verb somewhere else.  The distinction was
#: measured at plan step ``balance:X-bj-1``: the append-only trigger's body
#: names the table in a ``SELECT ... WHERE id = OLD.anchor_id`` and says
#: "UPDATE rejected" in a message, and the any-verb-anywhere form reported
#: that SELECT as a writer -- the exact false positive the census's own
#: docstring says a SELECT must not be.
_SQL_WRITE_PATTERN = re.compile(
    r"(insert\s+into|update|delete\s+from)\s+(budget\.)?" + _TABLE + r"\b",
)


def _enclosing_defs(tree) -> dict[int, str]:
    """Map each AST node's id to the name of its INNERMOST enclosing ``def``.

    Recursive descent rather than :func:`ast.walk` + ``setdefault``.  ``walk``
    is breadth-first, so an outer function is visited before the inner one it
    contains and ``setdefault`` keeps the OUTER name -- a construction hidden in
    a nested helper would then be reported at the enclosing function, which is
    the wrong address to send a reader to.  Caught by an adversarial review of
    this gate's first draft.

    Args:
        tree: A parsed module.

    Returns:
        ``{id(node): enclosing def name}`` for every node inside some ``def``.
    """
    import ast  # pylint: disable=import-outside-toplevel

    owner: dict[int, str] = {}

    def descend(node, current: str | None):
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                descend(child, child.name)
            else:
                if current is not None:
                    owner[id(child)] = current
                descend(child, current)

    descend(tree, None)
    return owner


def _docstring_nodes(tree) -> set[int]:
    """Return ``id()`` of every string Constant that is a DOCSTRING.

    A module, class or function docstring is the first statement of its body
    and is an ``ast.Expr`` wrapping a string ``Constant``.  Identified by
    position rather than by content, so a string that merely LOOKS like prose
    is still censused and a SQL literal that happens to sit first in a function
    is still counted as one.

    Args:
        tree: The parsed module.

    Returns:
        The set of ``id()`` values of the docstring Constant nodes.
    """
    import ast  # pylint: disable=import-outside-toplevel

    holders = (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)
    docstrings: set[int] = set()
    for node in ast.walk(tree):
        if not isinstance(node, holders) or not node.body:
            continue
        first = node.body[0]
        if (
            isinstance(first, ast.Expr)
            and isinstance(first.value, ast.Constant)
            and isinstance(first.value.value, str)
        ):
            docstrings.add(id(first.value))
    return docstrings


def _anchor_history_writers(root: pathlib.Path) -> list[tuple[str, str, int]]:
    """Return every site under *root* that could WRITE an assertion row.

    An AST walk rather than a grep, because a grep cannot tell a construction
    from a docstring naming the class -- and the modules involved name it dozens
    of times in prose.  It catches four shapes, because an adversarial review
    planted each one against the first draft and the draft saw none of them:

    * the ORM constructor, **through an import alias too** -- ``from ... import
      AccountAnchorHistory as AAH`` then ``AAH(...)``.  The alias is resolved
      per module from its own ``ImportFrom`` nodes;
    * ``insert(AccountAnchorHistory)`` / ``insert(AccountAnchorHistory.__table__)``
      in any Core statement;
    * ``bulk_insert_mappings`` / ``bulk_save_objects`` naming the model;
    * a raw-SQL string literal that names the TABLE under a write verb.

    A ``SELECT`` literal naming the table is NOT a writer and is not reported
    (``scripts/integrity_check.py`` has two, legitimately).

    **Nor is a DOCSTRING, and that arm had the exact hole this census's first
    line says it was built to avoid** (plan step X-f3c-2c).  The reason given
    for walking the AST rather than grepping is that "a grep cannot tell a
    construction from a docstring naming the class" -- and the raw-SQL arm then
    read every string constant, docstrings included.  It fired on
    ``app/append_only_infrastructure.py``, whose module docstring explains why
    a bulk ``UPDATE`` on ``budget.account_anchor_history`` is refused: prose
    ABOUT a write, reported as one.  Docstrings are excluded by IDENTITY
    (``ast.get_docstring`` semantics: the first statement of a module, class or
    function, when it is a string) rather than by pattern, so a genuine SQL
    literal that happens to sit first in a function is still counted.

    Args:
        root: The directory to census.  Parameterised so the negative control
            can point this exact function at a planted tree -- a control that
            re-implements the census grades a copy, which is what the first
            draft did.

    Returns:
        ``(path relative to the repository root when it is inside it, enclosing
        def, line)`` for each writing site, sorted.  ``"<module>"`` is the
        enclosing name for module scope.
    """
    import ast  # pylint: disable=import-outside-toplevel

    def label(path: pathlib.Path) -> str:
        try:
            return str(path.relative_to(_REPO_ROOT))
        except ValueError:
            return path.name

    found: list[tuple[str, str, int]] = []
    for path in sorted(root.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        owner = _enclosing_defs(tree)
        docstrings = _docstring_nodes(tree)
        names = {_MODEL}
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                for alias in node.names:
                    if alias.name == _MODEL and alias.asname:
                        names.add(alias.asname)

        def mentions_model(node, local_names=names) -> bool:
            import ast as _ast  # pylint: disable=import-outside-toplevel

            return any(
                (isinstance(inner, _ast.Name) and inner.id in local_names)
                or (
                    isinstance(inner, _ast.Attribute)
                    and inner.attr in local_names
                )
                for inner in _ast.walk(node)
            )

        for node in ast.walk(tree):
            hit = False
            if isinstance(node, ast.Call):
                called = (
                    node.func.id if isinstance(node.func, ast.Name)
                    else getattr(node.func, "attr", None)
                )
                if called in names:
                    hit = True
                elif called in _BULK_WRITERS and mentions_model(node):
                    hit = True
                elif called in {"insert", "Insert"} and mentions_model(node):
                    hit = True
            elif (
                isinstance(node, ast.Constant)
                and isinstance(node.value, str)
                and id(node) not in docstrings
            ):
                hit = _SQL_WRITE_PATTERN.search(node.value.lower()) is not None
            if hit:
                found.append((
                    label(path), owner.get(id(node), "<module>"), node.lineno,
                ))
    return sorted(found)


class TestTheAssertionTableHasOneWriter:
    """X-f1e2 / ruling R-ES: one function appends an assertion, and no other.

    **A structural gate, not a style preference.**  Until this step
    ``account_service.create_account`` constructed the origination row itself,
    which meant that one assertion in the whole app was written with no owner
    write lock, no ruling R-EQ did-this-change compare and no shared log line.
    Nothing in the code said so -- the two constructions simply looked alike --
    and this is what says it.

    **Since plan step ``balance:X-bj-1`` the table is the LEVEL RELATION and
    holds TWO kinds of row, so the census names two writers, one per kind**
    (:data:`_WRITERS`): the owner's door and the import door.  The gate's
    claim is unchanged in what matters -- every writer is NAMED, with the
    reason it may write -- and a third one still fails it by name.
    """

    def test_exactly_one_place_writes_an_assertion(self):
        """One function per ROW KIND in ``app/`` + ``scripts/`` writes the table.

        Fails the moment an unnamed writer appears, naming it and its line,
        which is the failure mode the ruling exists to prevent rather than a
        count for its own sake.
        """
        writers_found: list[tuple[str, str, int]] = []
        for tree in _PRODUCTION_TREES:
            writers_found.extend(_anchor_history_writers(_REPO_ROOT / tree))
        assert writers_found, (
            "the AST census found NO writer of the assertion table in "
            f"{list(_PRODUCTION_TREES)} -- the census is broken, not the code "
            "(an assertion has to be written somewhere)"
        )
        writers = {(path, func) for path, func, _line in writers_found}
        assert writers == _WRITERS, (
            "budget.account_anchor_history has exactly two writers, one per "
            f"kind of level ({sorted(_WRITERS)}; rulings R-ES and R-IS).  "
            "Found: " + ", ".join(
                f"{path}:{line} in {func}()"
                for path, func, line in writers_found
            )
        )

    def test_the_census_sees_every_write_shape_it_claims_to(self, tmp_path):
        """The census's negative control, run against the census itself.

        **Five shapes, because an adversarial review planted each one against
        the first draft and the draft reported CLEAN on four of them** -- an
        import alias, a Core ``insert()``, ``bulk_insert_mappings``, and raw
        SQL.  A gate that sees only the canonical constructor claims "one
        writer" while proving "one use of one spelling".

        It calls :func:`_anchor_history_writers` rather than re-implementing
        it, which the first draft did: a control that grades a copy leaves the
        real function's alias arm, SQL arm and enclosing-``def`` attribution
        ungraded.
        """
        (tmp_path / "prose_only.py").write_text(
            '"""Mentions AccountAnchorHistory and account_anchor_history."""\n'
            "def reads_it():\n"
            '    """SELECT id FROM budget.account_anchor_history."""\n'
            '    return "SELECT id FROM budget.account_anchor_history"\n',
            encoding="utf-8",
        )
        (tmp_path / "writers.py").write_text(
            "from app.models.account import AccountAnchorHistory as AAH\n"
            "from sqlalchemy import insert\n"
            "def by_alias():\n"
            "    return AAH(account_id=1)\n"
            "def by_core_insert(session):\n"
            "    session.execute(insert(AAH.__table__), [{}])\n"
            "def by_bulk(session):\n"
            "    session.bulk_insert_mappings(AAH, [{}])\n"
            "def by_raw_sql(session):\n"
            '    session.execute("INSERT INTO budget.account_anchor_history "\n'
            '                    "(account_id) VALUES (1)")\n'
            "def outer():\n"
            "    def nested_writer():\n"
            "        return AAH(account_id=2)\n"
            "    return nested_writer\n",
            encoding="utf-8",
        )

        found = _anchor_history_writers(tmp_path)
        by_function = {func for _path, func, _line in found}

        assert by_function == {
            "by_alias", "by_core_insert", "by_bulk", "by_raw_sql",
            "nested_writer",
        }, (
            "the census must report all five planted write shapes and neither "
            f"prose mention nor the SELECT; it reported {sorted(by_function)}"
        )
        assert not any(path == "prose_only.py" for path, _f, _l in found), (
            "the census reported a prose mention or a SELECT as a writer"
        )
        assert "outer" not in by_function, (
            "a construction inside a NESTED def was attributed to the outer "
            "function, which sends a reader to the wrong address"
        )


class TestAnAssertionCarriesNoProvenanceColumn:
    """X-f1e2 / ruling R-ES: ``account_anchor_history.notes`` is gone.

    Graded against the live catalog rather than the model, so it is the
    MIGRATION under test.  A model attribute deleted without the migration
    leaves a column the ORM cannot see and a downgrade cannot round-trip.
    """

    def test_the_notes_column_does_not_exist(self, app):
        """``information_schema`` shows no ``notes`` on the assertion table.

        And it still shows the three columns an assertion IS, so a probe that
        silently pointed at the wrong table (and therefore found no ``notes``
        for the wrong reason) fails here instead of passing.
        """
        from sqlalchemy import text  # pylint: disable=import-outside-toplevel

        with app.app_context():
            columns = {
                row[0] for row in _db.session.execute(text(
                    "SELECT column_name FROM information_schema.columns "
                    "WHERE table_schema = 'budget' "
                    "AND table_name = 'account_anchor_history'"
                ))
            }
        assert "notes" not in columns, (
            "budget.account_anchor_history.notes still exists; ruling R-ES "
            "drops it in migration b5e3d9c1a7f2"
        )
        assert {"account_id", "anchor_balance", "observed_on"} <= columns, (
            "the probe found no assertion columns at all, so its absence of "
            f"'notes' proves nothing.  Saw: {sorted(columns)}"
        )


class TestTheFactoryRefusesToLeaveAnAccountAnchorless:
    """X-f1e2: ``create_account`` raises when the write door declines.

    **An adversarial review's surviving mutant.**  Swallowing
    :func:`~app.services.anchor_service.stage_anchor_true_up`'s ``False`` passed
    a 693-test control set, so the branch that upholds this module's whole
    reason for existing -- E-19 / CRIT-01, "every account carries an assertion
    from the moment it exists" -- shipped ungraded.  The state is structurally
    unreachable today (a just-flushed account has no assertion for the door to
    find), which is exactly why nothing else reaches it and why this test has to
    manufacture it.
    """

    def test_a_declining_write_door_raises_instead_of_returning(
        self, app, db, monkeypatch, seed_user, seed_periods_today,
    ):
        """A ``staged=False`` report from the stager is a refusal, not a silent success.

        Forces the decline by patching the write door, which is the only way in:
        the compare it makes cannot find a governing assertion for an account
        created two statements earlier.  Asserts the raise AND that no account
        row survives the caller's rollback, because a raise that left a
        committed anchorless account would be the defect wearing an exception.
        """
        import pytest  # pylint: disable=import-outside-toplevel

        from app.services import account_service  # pylint: disable=import-outside-toplevel
        from app.services import anchor_service  # pylint: disable=import-outside-toplevel

        assert seed_periods_today
        with app.app_context():
            monkeypatch.setattr(
                anchor_service, "stage_anchor_true_up",
                lambda **_kwargs: anchor_service.AnchorStageReport(
                    staged=False, latest=None,
                ),
            )
            checking_type_id = _db.session.query(AccountType).filter_by(
                name="Checking",
            ).one().id

            with pytest.raises(RuntimeError) as exc:
                account_service.create_account(
                    account_service.AccountSpec(
                        user_id=seed_user["user"].id,
                        account_type_id=checking_type_id,
                        name="Anchorless Probe",
                        anchor_balance=Decimal("1234.56"),
                    ),
                )

            assert "E-19 / CRIT-01" in str(exc.value), (
                "the refusal must name the invariant it protects, or a reader "
                f"cannot tell what broke: {exc.value}"
            )
            _db.session.rollback()
            assert _db.session.query(Account).filter_by(
                user_id=seed_user["user"].id, name="Anchorless Probe",
            ).one_or_none() is None, (
                "an account with no assertion survived the refusal"
            )
