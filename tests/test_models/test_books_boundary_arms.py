"""The books boundary is built ARM BY ARM, and a revision declares its own.

Plan step **balance:X-f3c-2b-2b**.  These cases exist because the shared
builders in :mod:`app.opening_infrastructure` are imported LIVE from ``app/``
by migrations that shipped long before the arms they now install.  Nothing
stopped the module's growth reaching backwards, and it was measured doing
exactly that: with the matched-line arm added and ``d3b6f1c8a274`` still
calling the builder unqualified, that revision installed
``ck_matched_line_after_books_open`` and ``ck_line_day_after_books_open`` five
revisions before ``d1f6a83c9e47`` runs the census that decides whether the rows
already there can satisfy them.  Observed on a clone of the developer's
database stopped at ``c9f4b1e78d02``, which came up carrying both triggers
LIVE.  A constraint installed ahead
of its census is what finding **N-400** is made of, reached from the migration
side.

Three properties are pinned here, and the first two are what let the third
exist:

* the builders take an explicit ``arms`` set and install exactly it;
* the statement is TOTAL rather than additive -- an arm the caller does not
  name is dropped, triggers first and then functions;
* so ``d1f6a83c9e47``'s downgrade is one call naming one fewer arm, and keeps
  NO frozen copy of the previous function bodies.  The copy it used to keep
  was correct when written and pinned by nothing; the next edit to any of the
  three bodies would have rotted it silently.
"""

from __future__ import annotations

import ast
import pathlib
from datetime import timedelta
from decimal import Decimal

import pytest

from app import ref_cache
from app.enums import AccountOpeningSourceEnum
from app.models.account_opening import AccountOpening

from app.opening_infrastructure import (
    ALL_ARMS,
    LOAN_ORIGINATION_ARM,
    MATCHED_LINE_ARM,
    MOVEMENT_ARM,
    apply_opening_functions,
    apply_opening_infrastructure,
    remove_opening_infrastructure,
)

_MIGRATIONS = (
    pathlib.Path(__file__).resolve().parents[2] / "migrations" / "versions"
)

#: The matched-line arm's own object names, as they appear in emitted SQL.
_MATCHED_LINE_OBJECTS = (
    "budget.assert_matched_line_holds_books",
    "budget.assert_match_member_after_books_open",
    "budget.assert_line_day_after_books_open",
    "budget.assert_account_books_hold_its_matched_lines",
    "ck_matched_line_after_books_open",
    "ck_line_day_after_books_open",
)

#: The movement arm's own object names.
_MOVEMENT_OBJECTS = (
    "budget.assert_movement_after_books_open",
    "budget.assert_account_books_hold_its_movements",
    "ck_movement_after_books_open",
)

#: The loan-origination arm's own object names (plan task plan#3960).  Its
#: ``budget.loan_params`` trigger runs the openings dispatcher, which is no
#: arm's, so that function is not here.
_LOAN_ORIGINATION_OBJECTS = (
    "budget.assert_books_hold_after_account_retype",
    "budget.assert_books_hold_after_type_retype",
    "ck_books_open_after_loan_terms",
    "ck_books_open_after_account_retype",
    "ck_books_open_after_type_retype",
)

#: The revision that declares every arm the module knows, which the two
#: scripts' :data:`ALL_ARMS` must equal.
_HEAD_ARMS_REVISION = "e5b2c8a41f73_a_set_up_loans_books_open_on_its_origination.py"

#: The books day, as the CREATE that installs its body names it.
_BOOKS_DAY_CREATE = (
    "CREATE OR REPLACE FUNCTION budget.account_books_opened_on("
)

#: The BASE, which belongs to no arm and must survive every arm set.
_BASE_OBJECTS = (
    "budget.books_hold",
    "budget.account_books_opened_on",
)


def _builder_name(func):
    """Return the called name for a plain or dotted call target.

    ``arms=ALL_ARMS`` is an :class:`ast.Name`; ``opening_infrastructure
    .ALL_ARMS`` is an :class:`ast.Attribute`, and a check that matched only the
    first would be evaded by the second -- including in the collector, which
    would then make the whole class vacuous rather than merely incomplete.

    Args:
        func: The ``.func`` of an :class:`ast.Call`.

    Returns:
        The bare name, or ``""`` when the target is neither form.
    """
    if isinstance(func, ast.Name):
        return func.id
    if isinstance(func, ast.Attribute):
        return func.attr
    return ""


def _statements(arms):
    """Return the SQL ``apply_opening_infrastructure`` emits for *arms*.

    Args:
        arms: The arm tuple to build.

    Returns:
        The emitted statements, in order.
    """
    emitted = []
    apply_opening_infrastructure(emitted.append, arms=arms)
    return emitted


def _creates(statements):
    """Return only the statements that CREATE something.

    Args:
        statements: Emitted SQL.

    Returns:
        The subset that creates a function or a trigger.
    """
    return [s for s in statements if "CREATE" in s]


def _books_day_body(statements):
    """Return the one ``budget.account_books_opened_on`` body *statements* create.

    Args:
        statements: Emitted SQL.

    Returns:
        The single CREATE statement for the books day.
    """
    bodies = [s for s in statements if _BOOKS_DAY_CREATE in s]
    assert len(bodies) == 1, f"{len(bodies)} books-day bodies emitted"
    return bodies[0]


def _drops(statements):
    """Return only the DROP FUNCTION statements.

    Args:
        statements: Emitted SQL.

    Returns:
        The subset that drops a function.
    """
    return [s for s in statements if "DROP FUNCTION" in s]


class TestAnArmSetInstallsExactlyItself:
    """What a caller names is what the database gets."""

    def test_the_movement_arm_alone_creates_no_matched_line_object(self):
        """FIRING CONTROL: the five-revision window, closed.

        This is the case that fails if ``apply_opening_infrastructure`` ever
        goes back to installing whatever the module currently holds -- which
        is the state a clone at ``c9f4b1e78d02`` was measured in.
        """
        created = " ".join(_creates(_statements((MOVEMENT_ARM,))))

        for name in _MATCHED_LINE_OBJECTS:
            assert name not in created, (
                f"{name} was created by a MOVEMENT-only install"
            )

    def test_the_movement_arm_alone_still_creates_its_own_objects(self):
        """The other direction, so the case above cannot pass by creating
        nothing at all."""
        created = " ".join(_creates(_statements((MOVEMENT_ARM,))))

        for name in _MOVEMENT_OBJECTS:
            assert name in created, f"{name} missing from a MOVEMENT install"

    def test_the_BASE_is_created_for_EVERY_arm_set(self):
        """FIRING CONTROL for the property the downgrade rests on.

        ``budget.books_hold`` belongs to no arm: every arm's predicate calls
        it, so it must be installed whatever is declared.  Move
        ``_CREATE_BOOKS_HOLD_SQL`` inside ``if MOVEMENT_ARM in arms:`` and a
        matched-line-only install emits four bodies calling a function that
        does not exist -- "function does not exist" on every settle and every
        restatement.  Nothing else in this file would have failed.
        """
        for arms in ((MOVEMENT_ARM,), (MATCHED_LINE_ARM,), ALL_ARMS):
            created = " ".join(_creates(_statements(arms)))
            for name in _BASE_OBJECTS:
                assert name in created, f"{name} missing for arms={arms}"

    def test_withdrawing_an_arm_never_drops_the_BASE(self):
        """The other half: a withdrawal must not take the shared floor with it.

        ``d1f6a83c9e47``'s downgrade leaves movement bodies standing that call
        ``budget.books_hold``.  Dropping it there would be the "function does
        not exist" failure the frozen-copy removal traded away.
        """
        for arms in ((MOVEMENT_ARM,), (MATCHED_LINE_ARM,)):
            dropped = " ".join(_drops(_statements(arms)))
            for name in _BASE_OBJECTS:
                assert name not in dropped, (
                    f"{name} is BASE and was dropped for arms={arms}"
                )

    def test_every_arm_creates_every_arms_objects(self):
        """The head configuration the two scripts materialise."""
        created = " ".join(_creates(_statements(ALL_ARMS)))

        for name in (
            _MOVEMENT_OBJECTS + _MATCHED_LINE_OBJECTS
            + _LOAN_ORIGINATION_OBJECTS
        ):
            assert name in created, f"{name} missing from a full install"

    def test_without_the_loan_arm_the_books_day_reads_no_loan_terms(self):
        """FIRING CONTROL for the BODY axis on the books day (plan#3960).

        Every revision before ``e5b2c8a41f73`` names its arms without
        :data:`LOAN_ORIGINATION_ARM`, so each must install the day its own
        point in history had: the governing opening record, and no loan
        object at all.  Generate the loan-aware body unconditionally and
        ``d3b6f1c8a274`` would judge loans by their origination four revisions
        before any census asked whether the rows allowed it.
        """
        statements = _statements((MOVEMENT_ARM, MATCHED_LINE_ARM))
        created = " ".join(_creates(statements))
        day = _books_day_body(statements)

        for name in _LOAN_ORIGINATION_OBJECTS:
            assert name not in created, f"{name} created without its arm"
        assert "budget.loan_params" not in day
        assert "ORDER BY id DESC" in day

    def test_with_the_loan_arm_a_configured_loan_opens_on_its_origination(
        self,
    ):
        """The other direction: the day reads the loan's terms, BOTH halves.

        ``has_amortization`` is half of ``configured_loan``'s test: a
        ``loan_params`` row on an account whose type does not amortize is a
        data defect the balance refuses to amortize, so the boundary must not
        either.  The governing lookup stays, for every other account.
        """
        day = _books_day_body(_statements(ALL_ARMS))

        assert "params.origination_date" in day
        assert "types.has_amortization" in day
        assert "ORDER BY id DESC" in day

    def test_the_dispatcher_names_only_the_installed_predicates(self):
        """The openings trigger function is GENERATED from the arm set.

        A body that ``PERFORM``s a predicate its revision never created is
        accepted at ``CREATE`` time by PL/pgSQL and fails at COMMIT, which is
        the failure furthest from its cause.
        """
        # The FUNCTION, not the trigger that executes it -- both name it.
        dispatcher = [
            s for s in _statements((MOVEMENT_ARM,))
            if "CREATE OR REPLACE FUNCTION "
            "budget.assert_books_open_before_books_movements()" in s
        ]

        assert len(dispatcher) == 1
        assert "assert_account_books_hold_its_movements" in dispatcher[0]
        assert (
            "assert_account_books_hold_its_matched_lines"
            not in dispatcher[0]
        ), "the movement-only dispatcher performs a predicate it never created"


class TestTheStatementIsTotalRatherThanAdditive:
    """An arm the caller does not name is REMOVED, not left alone.

    This is the property that lets ``d1f6a83c9e47``'s downgrade be one call
    instead of a hand-frozen copy of three PL/pgSQL bodies.
    """

    def test_withdrawing_an_arm_drops_its_functions(self):
        """FIRING CONTROL for the downgrade.

        If this is additive rather than total, the downgrade silently leaves
        the matched-line arm installed on a database that reported a clean
        revert -- and every later write on a violating account aborts at
        COMMIT against a constraint no revision claims to have installed.
        """
        dropped = " ".join(_drops(_statements((MOVEMENT_ARM,))))

        for name in (
            "budget.assert_match_member_after_books_open",
            "budget.assert_line_day_after_books_open",
            "budget.assert_account_books_hold_its_matched_lines",
            "budget.assert_matched_line_holds_books",
        ):
            assert name in dropped, f"withdrawing the arm left {name} behind"

    def test_withdrawing_an_arm_drops_its_triggers_before_its_functions(self):
        """Order is the only thing this has to get right.

        Dropping a routine a stored function still names is legal in
        PostgreSQL and fails at CALL time, which would leave every
        restatement raising ``function does not exist`` on a database that
        reported a clean downgrade.
        """
        statements = _statements((MOVEMENT_ARM,))
        trigger_drop = next(
            i for i, s in enumerate(statements)
            if "DROP TRIGGER" in s and "ck_line_day_after_books_open" in s
        )
        function_drop = next(
            i for i, s in enumerate(statements)
            if "DROP FUNCTION" in s
            and "budget.assert_line_day_after_books_open" in s
        )

        assert trigger_drop < function_drop

    def test_the_dispatcher_is_regenerated_before_its_predicate_is_dropped(
        self,
    ):
        """The subtler ordering, and the one a reader would not guess.

        The openings dispatcher ``PERFORM``s the matched-line predicate.
        Withdrawing that arm has to REPLACE the dispatcher body first, or the
        drop leaves a stored function naming a routine that no longer exists.
        """
        statements = _statements((MOVEMENT_ARM,))
        dispatcher_create = next(
            i for i, s in enumerate(statements)
            if "CREATE OR REPLACE FUNCTION "
            "budget.assert_books_open_before_books_movements()" in s
        )
        predicate_drop = next(
            i for i, s in enumerate(statements)
            if "DROP FUNCTION" in s
            and "assert_account_books_hold_its_matched_lines" in s
        )

        assert dispatcher_create < predicate_drop

    def test_each_predicate_is_created_BEFORE_the_dispatcher(self):
        """The create-side ordering ``apply_opening_functions`` calls
        load-bearing.

        Only the DROP side was pinned.  PL/pgSQL does not resolve a ``PERFORM``
        target at ``CREATE`` time, so both orders happen to work today -- which
        is exactly why a reader would not notice the order being lost, and why
        it is asserted rather than trusted.
        """
        statements = _statements(ALL_ARMS)
        dispatcher = next(
            i for i, s in enumerate(statements)
            if "CREATE OR REPLACE FUNCTION "
            "budget.assert_books_open_before_books_movements()" in s
        )
        for predicate in (
            "budget.assert_account_books_hold_its_movements",
            "budget.assert_account_books_hold_its_matched_lines",
        ):
            created = next(
                i for i, s in enumerate(statements)
                if f"CREATE OR REPLACE FUNCTION {predicate}" in s
            )
            assert created < dispatcher, (
                f"{predicate} is created after the dispatcher that calls it"
            )

    def test_withdrawing_the_loan_arm_drops_its_objects_in_order(self):
        """``e5b2c8a41f73``'s downgrade: triggers, then the books day without
        the loan term, then the two re-type dispatchers.

        Dropping a dispatcher before the trigger that executes it leaves every
        loan edit raising ``function does not exist``, and leaving the
        loan-aware day standing would keep judging loans by their origination
        on a database that reported the arm withdrawn.
        """
        statements = _statements((MOVEMENT_ARM, MATCHED_LINE_ARM))
        dropped = " ".join(_drops(statements))

        for name in (
            "budget.assert_books_hold_after_account_retype",
            "budget.assert_books_hold_after_type_retype",
        ):
            assert name in dropped, f"withdrawing the arm left {name} behind"
        trigger_drop = next(
            i for i, s in enumerate(statements)
            if "DROP TRIGGER" in s
            and "ck_books_open_after_account_retype" in s
        )
        function_drop = next(
            i for i, s in enumerate(statements)
            if "DROP FUNCTION" in s
            and "budget.assert_books_hold_after_account_retype" in s
        )
        assert trigger_drop < function_drop
        assert not any(
            "CREATE CONSTRAINT TRIGGER ck_books_open_after" in s
            for s in statements
        )

    def test_a_full_install_drops_no_function(self):
        """Naming every arm withdraws none of them."""
        assert _drops(_statements(ALL_ARMS)) == []


class TestTheArmSetIsValidated:
    """A bad arm set is refused, rather than generating broken DDL."""

    def test_an_empty_arm_set_is_refused(self):
        """It would generate an openings dispatcher with an empty ``IF``
        block, which is not valid PL/pgSQL."""
        with pytest.raises(ValueError, match="at least one arm"):
            apply_opening_functions(lambda _: None, arms=())

    def test_the_loan_arm_alone_is_refused(self):
        """It adds no row set, so alone it would ask nothing, and the openings
        dispatcher it shares would have an empty ``IF`` block."""
        with pytest.raises(ValueError, match="at least one arm with a row set"):
            apply_opening_functions(
                lambda _: None, arms=(LOAN_ORIGINATION_ARM,),
            )

    def test_an_unknown_arm_is_refused(self):
        """A typo would otherwise install a boundary quietly missing an arm."""
        with pytest.raises(ValueError, match="unknown books-boundary arm"):
            apply_opening_functions(lambda _: None, arms=("mvoement",))

    def test_the_refusal_names_what_the_module_does_build(self):
        """An actionable message (coding standards): it names the valid set."""
        with pytest.raises(ValueError) as caught:
            apply_opening_functions(lambda _: None, arms=("nope",))

        assert MOVEMENT_ARM in str(caught.value)
        assert MATCHED_LINE_ARM in str(caught.value)


class TestEveryRevisionDeclaresItsOwnArms:
    """The structural guard, stated as a property of the migrations.

    ``ALL_ARMS`` means *whatever this module has grown into*, which is right
    for the two scripts that materialise a database at HEAD and wrong for a
    revision, whose job is to build the database its own point in history
    describes.  A revision reaching for it is the exact defect this step
    closed, so it is asserted rather than left to review.
    """

    def _revisions_calling_the_builders(self):
        """Return each migration that CALLS a books-boundary builder.

        Parsed rather than grepped, and that is not fastidiousness: every
        revision here explains in PROSE why it names its arms literally, so a
        text search for ``ALL_ARMS`` matches the explanation and reports the
        revision as the very offender the explanation says it is not.  Both
        cases below failed that way when first written.

        Returns:
            A list of ``(path, tree)`` pairs, one per revision whose CODE
            calls ``apply_opening_infrastructure`` or
            ``apply_opening_functions``.
        """
        found = []
        for path in sorted(_MIGRATIONS.glob("*.py")):
            tree = ast.parse(path.read_text())
            calls = [
                node for node in ast.walk(tree)
                if isinstance(node, ast.Call)
                and _builder_name(node.func) in (
                    "apply_opening_infrastructure", "apply_opening_functions",
                )
            ]
            if calls:
                found.append((path, tree))
        return found

    def test_some_revision_actually_calls_a_builder(self):
        """Guards the two cases below against passing vacuously."""
        assert len(self._revisions_calling_the_builders()) >= 2

    def test_no_revision_installs_ALL_ARMS(self):
        """FIRING CONTROL: a revision must name a literal arm tuple.

        The whole failure this step fixed is a revision letting the module
        choose.  ``ALL_ARMS`` is the way to do that by accident.
        """
        offenders = [
            path.name
            for path, tree in self._revisions_calling_the_builders()
            if any(
                (isinstance(node, ast.Name) and node.id == "ALL_ARMS")
                or (isinstance(node, ast.Attribute) and node.attr == "ALL_ARMS")
                for node in ast.walk(tree)
            )
        ]

        assert offenders == [], (
            f"{offenders} install ALL_ARMS; a revision names the arms it "
            "declared and censused, so the module can grow without reaching "
            "backwards into shipped history"
        )

    def test_every_calling_revision_passes_arms_explicitly(self):
        """A bare call would take the parameter's default -- there is none,
        so this also pins that the signature keeps requiring it."""
        for path, tree in self._revisions_calling_the_builders():
            for node in ast.walk(tree):
                if not (
                    isinstance(node, ast.Call)
                    and _builder_name(node.func) in (
                        "apply_opening_infrastructure",
                        "apply_opening_functions",
                    )
                ):
                    continue
                assert any(kw.arg == "arms" for kw in node.keywords), (
                    f"{path.name} line {node.lineno} calls a books-boundary "
                    "builder without naming its arms"
                )


class TestAFromScratchDatabaseMatchesAMigratedOne:
    """The two BUILD PATHS install the same arms, or they silently diverge.

    ``scripts/init_database.py`` and ``scripts/build_test_template.py`` pass
    :data:`ALL_ARMS`; the migration chain installs whatever the NEWEST revision
    declares.  If a future arm is added to the module and the head revision is
    not updated, a fresh database gets it and a migrated one does not -- **at
    the same alembic revision**, which cannot tell them apart, because the
    stamp records which revision is head and never which arms ran.

    Raised by the coordinating session while reviewing this change, and it is
    the same trap ``project_shared_test_template_collision`` records: a stamp
    is not a schema.

    **This case and the builder's TOTALITY are one mechanism, not two.**
    Asserting that HEAD's tuple equals :data:`ALL_ARMS` is sufficient ONLY
    because an arm a caller does not name is DROPPED, which makes the last call
    in the chain authoritative and collapses the chain's cumulative effect to
    whatever HEAD names.  Make ``apply_opening_infrastructure`` ADDITIVE --
    installing what it is given without removing what it is not, which is the
    shape someone reaches for "to be safe" -- and the chain's effect becomes
    the UNION of every revision's tuple, which can exceed :data:`ALL_ARMS`
    without HEAD changing.  This assertion would keep passing and would be
    measuring nothing.  So the class above
    (``TestTheStatementIsTotalRatherThanAdditive``) is not a neighbour of this
    one; it is its premise.
    """

    def test_the_NEWEST_revision_installs_every_arm_the_module_knows(self):
        """FIRING CONTROL for a divergence no stamp can see."""
        head = (_MIGRATIONS / _HEAD_ARMS_REVISION).read_text()
        tree = ast.parse(head)
        declared = None
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Assign)
                and any(
                    isinstance(t, ast.Name) and t.id == "_ARMS_AFTER"
                    for t in node.targets
                )
                and isinstance(node.value, ast.Tuple)
            ):
                declared = {
                    e.id for e in node.value.elts if isinstance(e, ast.Name)
                }
        assert declared is not None, (
            "the head revision no longer declares _ARMS_AFTER; this case "
            "cannot grade what it names"
        )
        assert declared == {
            "MOVEMENT_ARM", "MATCHED_LINE_ARM", "LOAN_ORIGINATION_ARM",
        }, declared
        assert len(declared) == len(ALL_ARMS), (
            f"the head revision installs {len(declared)} arm(s) while the "
            f"module knows {len(ALL_ARMS)} -- a database built by the SCRIPTS "
            "(ALL_ARMS) and one built by the CHAIN would hold different "
            "schemas at the same alembic revision"
        )


class TestTheDowngradeNeedsNoFrozenBodies:
    """``d1f6a83c9e47`` keeps no copy of the previous function bodies.

    It kept three, taken verbatim from ``git show HEAD``.  They were correct
    when written and pinned by nothing: the next edit to
    ``_CREATE_MOVEMENT_FUNC_SQL``, ``_CREATE_OPENING_PREDICATE_SQL`` or the
    dispatcher would have rotted the copy, and the downgrade would then
    install a body that never existed on any database -- with a green suite.
    """

    def test_the_revision_inlines_no_create_function_body(self):
        """FIRING CONTROL: the frozen copy, and its return.

        A downgrade that spells PL/pgSQL is keeping a second definition of
        something this module already defines.
        """
        tree = ast.parse((
            _MIGRATIONS
            / "d1f6a83c9e47_a_matched_bank_line_cannot_predate_the_books.py"
        ).read_text())
        # STRING CONSTANTS, not the file's text: the revision's own docstring
        # explains that ``CREATE OR REPLACE FUNCTION`` is idempotent, and a
        # text search reports that sentence as the inlined body it is warning
        # about.  This case failed that way when first written.
        docstrings = {
            id(node.body[0].value)
            for node in ast.walk(tree)
            if isinstance(node, (ast.Module, ast.FunctionDef, ast.ClassDef))
            and node.body
            and isinstance(node.body[0], ast.Expr)
            and isinstance(node.body[0].value, ast.Constant)
            and isinstance(node.body[0].value.value, str)
        }
        literals = [
            node.value for node in ast.walk(tree)
            if isinstance(node, ast.Constant)
            and isinstance(node.value, str)
            and id(node) not in docstrings
        ]

        for text in literals:
            assert "CREATE OR REPLACE FUNCTION" not in text, (
                "the revision inlines a function body again; the "
                "arm-explicit builder exists so it does not have to"
            )
            assert "LANGUAGE plpgsql" not in text

    def test_its_downgrade_withdraws_exactly_the_matched_line_arm(self):
        """The one call, and the arm it names."""
        source = (
            _MIGRATIONS
            / "d1f6a83c9e47_a_matched_bank_line_cannot_predate_the_books.py"
        ).read_text()
        tree = ast.parse(source)
        downgrade = next(
            node for node in tree.body
            if isinstance(node, ast.FunctionDef) and node.name == "downgrade"
        )
        calls = [
            node for node in ast.walk(downgrade)
            if isinstance(node, ast.Call)
            and _builder_name(node.func) == "apply_opening_infrastructure"
        ]

        assert len(calls) == 1, "the downgrade should be ONE call"
        arms = next(kw for kw in calls[0].keywords if kw.arg == "arms")
        named = [e.id for e in arms.value.elts if isinstance(e, ast.Name)]
        # Parsed, not grepped: the previous spelling searched the raw source
        # after ``def downgrade()`` and passed only because the docstring
        # happens to hyphenate "matched-line".  Any future prose using the
        # underscore would have failed it spuriously.
        assert named == ["MOVEMENT_ARM"], named


class TestRemoveTakesEverything:
    """``remove_opening_infrastructure`` is the no-boundary-at-all door."""

    def test_it_drops_both_arms_and_the_base(self):
        """``d3b6f1c8a274``'s downgrade, which withdraws the whole thing."""
        emitted = []
        remove_opening_infrastructure(emitted.append)
        text = " ".join(emitted)

        for name in (
            _MOVEMENT_OBJECTS + _MATCHED_LINE_OBJECTS
            + _LOAN_ORIGINATION_OBJECTS
        ):
            assert name in text, f"{name} survives a full removal"
        assert "budget.books_hold" in text
        assert "budget.account_books_opened_on" in text

    def test_it_drops_books_hold_last(self):
        """Every other body asks it, so it is the innermost dependency."""
        emitted = []
        remove_opening_infrastructure(emitted.append)
        drops = [s for s in emitted if "DROP FUNCTION" in s]

        assert "budget.books_hold" in drops[-1]


class TestTheCensusRefusesAnUpgradeItCannotMake:
    """``d1f6a83c9e47`` counts the violating members BEFORE installing the arm.

    A constraint trigger validates WRITES, not existing rows, so applying an
    arm over a database whose rows already break it succeeds -- and then aborts
    the next COMMIT touching one of those accounts, which is the failure mode
    furthest from where it can be diagnosed.  That is what finding **N-400**
    records about the assertion arm this same module had to withdraw.

    Neither arm of this was exercised: the census is new code that can stop a
    production deploy, and a typo in its ``WHERE`` would have installed the
    constraint over a violating database while reporting success.  Found by
    adversarial test review 2026-08-31.
    """

    def _revision(self):
        """Load the revision module.

        ``migrations/versions`` has no ``__init__.py``, so it is loaded the way
        alembic loads it and the way this repo's other migration tests do.

        Returns:
            The imported module.
        """
        import importlib.util

        path = (
            _MIGRATIONS
            / "d1f6a83c9e47_a_matched_bank_line_cannot_predate_the_books.py"
        )
        spec = importlib.util.spec_from_file_location(path.stem, str(path))
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    def test_a_clean_database_is_allowed_through(self, app, db, seed_user):
        """The census runs its real SQL against the real schema.

        Not a formality: it is the arm that catches a census whose query does
        not PARSE or whose columns have moved, which would otherwise surface
        as a failed deploy.
        """
        with app.app_context():
            assert self._revision()._reject_existing_violations(
                db.session.connection(),
            ) is None

    def test_a_violating_member_stops_the_upgrade_and_is_NAMED(
        self, app, db, seed_user, seed_periods,
    ):  # pylint: disable=unused-argument
        """FIRING CONTROL: the refusal, and what it must tell an operator.

        The state is built by lifting the very constraint the census exists to
        precede -- which is the only way to reach it, and is exactly the state
        a database upgrading from below ``d1f6a83c9e47`` can already be in.
        """
        from tests._test_helpers import (
            account_never_asserted,
            append_only_guard_lifted,
            match_two_lines,
        )

        with app.app_context():
            account = account_never_asserted(
                seed_user, db.session, name="Census Offender",
            )
            db.session.flush()
            opened = seed_periods[0].start_date
            db.session.add(AccountOpening(
                account_id=account.id,
                opened_on=opened,
                opening_equity=Decimal("10.00"),
                source_id=ref_cache.account_opening_source_id(
                    AccountOpeningSourceEnum.USER_DECLARED,
                ),
            ))
            db.session.commit()

            early = opened + timedelta(days=10)
            match_two_lines(
                db.session, account, seed_user["user"].id,
                early, early + timedelta(days=10),
            )

            # Restate the books PAST the matched line.  Both guards have to
            # come off: the append-only arms refuse the write, and the books
            # boundary refuses the state.
            db.session.execute(db.text(
                "ALTER TABLE budget.account_openings "
                "DISABLE TRIGGER ck_books_open_before_movements"
            ))
            with append_only_guard_lifted(
                db.session, "budget.account_openings",
            ):
                db.session.add(AccountOpening(
                    account_id=account.id,
                    opened_on=early + timedelta(days=1),
                    opening_equity=Decimal("10.00"),
                    source_id=ref_cache.account_opening_source_id(
                        AccountOpeningSourceEnum.USER_DECLARED,
                    ),
                ))
                db.session.commit()
            db.session.execute(db.text(
                "ALTER TABLE budget.account_openings "
                "ENABLE TRIGGER ck_books_open_before_movements"
            ))
            db.session.commit()

            with pytest.raises(RuntimeError) as caught:
                self._revision()._reject_existing_violations(
                    db.session.connection(),
                )

            message = str(caught.value)
            assert "cannot install the matched-line books boundary" in message
            # The operator needs the ACCOUNT, its books day and the offending
            # line -- the two ways out are theirs to choose between.
            assert f"account {account.id}" in message
            assert "Census Offender" in message
            assert early.isoformat() in message

    def test_the_refusal_hands_over_a_query_that_still_RUNS(
        self, app, db, seed_user,
    ):
        """FIRING CONTROL for a diagnostic that dies with its transaction.

        ``migrations/env.py`` wraps the whole chain in ONE transaction, so the
        ``RuntimeError`` rolls back the ``CREATE FUNCTION budget.books_hold``
        this revision issued moments earlier -- on every retry.  The message
        therefore may not name that function: an operator pasting it into psql
        would get ``function budget.books_hold(date, date) does not exist``,
        which is a dead end dressed as a diagnostic.
        """
        source = (
            _MIGRATIONS
            / "d1f6a83c9e47_a_matched_bank_line_cannot_predate_the_books.py"
        ).read_text()
        raise_block = source[source.index("raise RuntimeError("):]
        message_text = raise_block[:raise_block.index("\n    )")]

        assert "budget.books_hold" not in message_text, (
            "the refusal hands the operator a query calling a function its "
            "own rollback removes"
        )
        assert "posted_on <=" in message_text

