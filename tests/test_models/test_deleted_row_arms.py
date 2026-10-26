"""The deleted-row rule is built ARM BY ARM, and a revision declares its own.

Plan step **balance:X-bi-6-4d-2**.  :mod:`app.deleted_row_infrastructure` is
imported LIVE by ``c4a4e7d1b9f2``, which shipped before the transfer arm's link
columns existed; without a declared arm set a chain replay would install, at
that revision, a trigger naming columns the re-parent's revision adds, and the
test template (which replays the chain) would fail to build.  The construction
is :mod:`app.opening_infrastructure`'s, and so are the properties pinned here:

* the builder takes an explicit ``arms`` set and installs exactly it, the row
  arm's bodies byte for byte what ``c4a4e7d1b9f2`` shipped;
* the statement is TOTAL -- an arm the caller does not name is dropped, which
  is what makes the newest revision's tuple the whole of a migrated database's
  rule;
* every revision names a literal tuple, ``c4a4e7d1b9f2`` the row arm alone and
  the newest calling revision every arm the module knows, so a database built
  by the chain and one built from scratch (``scripts/init_database.py``,
  ``scripts/build_test_template.py``, which pass :data:`ALL_ARMS`) agree.
"""

from __future__ import annotations

import ast
import hashlib
import pathlib

import pytest
from sqlalchemy import text

from app import deleted_row_infrastructure as infrastructure
from app.deleted_row_infrastructure import (
    ALL_ARMS,
    DELETED_ROW_TRIGGERS,
    ROW_ARM,
    TRANSFER_ARM,
    apply_deleted_row_infrastructure,
)
from app.extensions import db as _db

_MIGRATIONS = (
    pathlib.Path(__file__).resolve().parents[2] / "migrations" / "versions"
)

#: The builder whose calls a revision must declare.
_BUILDER = "apply_deleted_row_infrastructure"

#: SHA-256 of the row arm's four CREATE statements, newline-joined, as
#: ``c4a4e7d1b9f2`` emitted them at ``4c9456778`` (taken 2026-10-08 from
#: ``git show 4c9456778:app/deleted_row_infrastructure.py``).
_SHIPPED_ROW_ARM_SHA256 = (
    "73592549b4f236492467bc2fb9ecb998f15a7bdc070bbfdfcb30eec25d533900"
)

#: The transfer arm's own objects, as they appear in emitted SQL.
_TRANSFER_OBJECTS = (
    "budget.refuse_hiding_a_transfer_holding_money",
    "ck_hidden_transfer_holds_nothing",
    "budget.transfers",
)


def _statements(arms):
    """Return the SQL :func:`apply_deleted_row_infrastructure` emits for *arms*."""
    emitted = []
    apply_deleted_row_infrastructure(emitted.append, arms=arms)
    return emitted


def _creates(statements):
    """Return the statements that CREATE a function or a trigger."""
    return [s for s in statements if "CREATE" in s]


def _arrival_attachment(statements):
    """Return the one statement that attaches the arrival trigger."""
    (attach,) = [
        s for s in statements
        if s.startswith("CREATE TRIGGER ck_movement_row_not_deleted")
    ]
    return attach


class TestAnArmSetInstallsExactlyItself:
    """What a caller names is what the database gets."""

    def test_the_row_arm_alone_creates_no_transfer_object(self):
        """FIRING CONTROL: ``c4a4e7d1b9f2``'s replay names no column it predates.

        The case that fails if the builder goes back to installing whatever
        the module currently holds: the arrival trigger would watch
        ``expense_transfer_id``, which that revision's table does not have.
        """
        statements = _statements((ROW_ARM,))
        created = " ".join(_creates(statements))

        for name in _TRANSFER_OBJECTS:
            assert name not in created, f"{name} created by a row-only install"
        assert "transfer_id, expense" not in _arrival_attachment(statements)
        assert "expense_transfer_id" not in created

    def test_the_row_arm_alone_still_creates_its_two_attachments(self):
        """The other direction, so the case above cannot pass by creating nothing."""
        created = " ".join(_creates(_statements((ROW_ARM,))))

        for name, _table in DELETED_ROW_TRIGGERS[:2]:
            assert name in created

    def test_the_row_arms_bodies_are_what_c4a4e7d1b9f2_shipped(self):
        """A revision builds the database ITS point in history describes.

        The row arm's four CREATE statements -- both functions, both
        attachments -- hashed against what ``c4a4e7d1b9f2`` emitted at
        ``4c9456778``, the commit before the arms existed (measured equal
        statement by statement when the hash was taken).  A row-only install
        that emitted a newer body would make a replay at that revision install
        a function naming columns that do not exist yet: a plpgsql body
        resolves them only when it runs, so the replay would pass and the
        first movement written would fail.  And an edit to the row arm that
        later steps owe (the hiding arm's transfer carve-out goes with the
        transfer arm) must land in the TRANSFER arm's bodies, never in these.
        """
        row_only = _creates(_statements((ROW_ARM,)))

        assert len(row_only) == 4
        assert hashlib.sha256("\n".join(row_only).encode()).hexdigest() == (
            _SHIPPED_ROW_ARM_SHA256
        )
        both = " ".join(_statements(ALL_ARMS))
        assert "IS NOT DISTINCT FROM OLD.income_transfer_id" in both
        assert "FROM budget.transfers" in both

    def test_every_arm_creates_every_attachment(self):
        """The head configuration the two scripts materialise."""
        statements = _statements(ALL_ARMS)
        created = " ".join(_creates(statements))

        for name, _table in DELETED_ROW_TRIGGERS:
            assert name in created
        assert (
            "UPDATE OF transaction_id, expense_transfer_id, income_transfer_id"
            in _arrival_attachment(statements)
        )

    @pytest.mark.parametrize("arms, refusal", [
        ((), "omits 'row'"),
        ((TRANSFER_ARM,), "omits 'row'"),
        ((ROW_ARM, "card"), r"unknown arm\(s\) \('card',\)"),
    ])
    def test_an_arm_set_the_module_cannot_build_is_refused(self, arms, refusal):
        """No arm set without the row arm, and no arm the module does not know.

        The transfer arm extends the row arm's arrival function and attaches
        nothing that refuses a movement on its own; an empty set is the
        removal, which has its own function.
        """
        with pytest.raises(ValueError, match=refusal):
            _statements(arms)


class TestTheStatementIsTotalRatherThanAdditive:
    """An arm the caller does not name is withdrawn, triggers before functions.

    The premise of the newest-revision case below: only a TOTAL statement makes
    the last call in the chain the whole of the database's rule.
    """

    def test_a_row_only_install_drops_the_transfer_attachment_then_its_function(self):
        """FIRING CONTROL: make the builder additive and this fails."""
        statements = _statements((ROW_ARM,))
        detach = next(
            i for i, s in enumerate(statements)
            if s.startswith("DROP TRIGGER IF EXISTS ck_hidden_transfer_holds_nothing")
        )
        drop = next(
            i for i, s in enumerate(statements)
            if "DROP FUNCTION IF EXISTS budget.refuse_hiding_a_transfer" in s
        )
        assert detach < drop

    def test_withdrawing_and_restoring_the_transfer_arm_against_the_database(
        self, app, db,
    ):
        """The emitted SQL, run: a row-only apply leaves two attachments and no
        transfer function, and every arm restores all three.

        Ends at every arm, which is the state the suite's template carries.
        """
        del app

        def run(arms):
            apply_deleted_row_infrastructure(
                lambda sql: db.session.execute(text(sql)), arms=arms,
            )
            db.session.commit()

        def attached():
            return sorted(
                tuple(row) for row in db.session.execute(text(
                    "SELECT t.tgname, c.relnamespace::regnamespace || '.' "
                    "|| c.relname FROM pg_trigger t "
                    "JOIN pg_class c ON c.oid = t.tgrelid "
                    "WHERE t.tgname = ANY(:names)"
                ), {"names": [name for name, _table in DELETED_ROW_TRIGGERS]})
            )

        def transfer_function_exists():
            return db.session.execute(text(
                "SELECT count(*) FROM pg_proc "
                "WHERE proname = 'refuse_hiding_a_transfer_holding_money'"
            )).scalar_one() == 1

        try:
            run((ROW_ARM,))
            assert attached() == sorted(DELETED_ROW_TRIGGERS[:2])
            assert not transfer_function_exists()
        finally:
            run(ALL_ARMS)
        assert attached() == sorted(DELETED_ROW_TRIGGERS)
        assert transfer_function_exists()


def _string_constant(node):
    """Return a module-level string assignment's value, or ``None``."""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    return None


def _chain():
    """Return every revision file in chain order, newest first.

    Read from each file's own ``revision`` / ``down_revision`` assignments, so
    "newest" is the chain's answer rather than a file a test names: a revision
    that adds an arm later meets this case without anyone editing it.

    Returns:
        ``[(path, tree)]`` from the head back to the root.
    """
    by_revision = {}
    down_of = {}
    for path in _MIGRATIONS.glob("*.py"):
        tree = ast.parse(path.read_text())
        fields = {}
        for node in tree.body:
            # ``revision = "..."`` and the annotated ``revision: str = "..."``
            # both occur in the chain.
            if isinstance(node, ast.Assign) and len(node.targets) == 1:
                target = node.targets[0]
            elif isinstance(node, ast.AnnAssign):
                target = node.target
            else:
                continue
            if isinstance(target, ast.Name) and target.id in (
                "revision", "down_revision",
            ):
                fields[target.id] = _string_constant(node.value)
        by_revision[fields["revision"]] = (path, tree)
        down_of[fields["revision"]] = fields.get("down_revision")
    (head,) = set(down_of) - set(down_of.values())
    ordered = []
    revision = head
    while revision is not None:
        ordered.append(by_revision[revision])
        revision = down_of[revision]
    return ordered


def _builder_calls(tree, function=None):
    """Return the builder's calls in *tree*, or in its function *function*."""
    scope = tree
    if function is not None:
        scope = next(
            node for node in tree.body
            if isinstance(node, ast.FunctionDef) and node.name == function
        )
    return [
        node for node in ast.walk(scope)
        if isinstance(node, ast.Call)
        and (
            (isinstance(node.func, ast.Name) and node.func.id == _BUILDER)
            or (isinstance(node.func, ast.Attribute) and node.func.attr == _BUILDER)
        )
    ]


def _declared_arms(call):
    """Return the arm values a call's literal ``arms=`` tuple names.

    Raises:
        AssertionError: When the call names no ``arms``, or names them by
            anything but a literal tuple of the module's arm constants.
    """
    (keyword,) = [kw for kw in call.keywords if kw.arg == "arms"]
    assert isinstance(keyword.value, ast.Tuple), (
        f"line {call.lineno}: arms must be a LITERAL tuple"
    )
    return tuple(
        getattr(infrastructure, element.id) for element in keyword.value.elts
    )


class TestEveryRevisionDeclaresItsOwnArms:
    """The structural guard, stated as a property of the migrations."""

    def _calling(self):
        """Return ``[(path, tree)]`` for each revision whose CODE calls the builder."""
        return [
            (path, tree) for path, tree in _chain() if _builder_calls(tree)
        ]

    def test_two_revisions_call_the_builder(self):
        """Guards the cases below against passing vacuously."""
        assert len(self._calling()) >= 2

    def test_every_call_names_a_literal_arm_tuple(self):
        """FIRING CONTROL: no revision lets the module choose its arms."""
        for path, tree in self._calling():
            for call in _builder_calls(tree):
                assert _declared_arms(call), path.name

    def test_c4a4e7d1b9f2_installs_the_row_arm_alone(self):
        """The revision that shipped before the side links names only its own arm."""
        (path, tree), = [
            (path, tree) for path, tree in self._calling()
            if path.name.startswith("c4a4e7d1b9f2")
        ]
        assert [_declared_arms(c) for c in _builder_calls(tree, "upgrade")] == [
            (ROW_ARM,),
        ], path.name

    def test_the_newest_calling_revision_installs_every_arm(self):
        """FIRING CONTROL for a divergence no alembic stamp can see.

        A from-scratch database gets :data:`ALL_ARMS`; a migrated one gets
        whatever the newest calling revision's upgrade declares (the statement
        is total).  Add an arm to the module without a revision installing it
        and this fails.
        """
        path, tree = self._calling()[0]
        assert [_declared_arms(c) for c in _builder_calls(tree, "upgrade")] == [
            ALL_ARMS,
        ], path.name
