"""Every GraphQL mutation quill's App sends goes through :meth:`_tracker.Board._write`.

``Board._write`` names the place's board as ``$p`` and no other, the fence that keeps a
write aimed at one tracker off another's board (``_tracker``'s module docstring).  It was
a fence by CONVENTION: a raw ``mutation`` string sent from :class:`_tracker.Tracker`, or
from a method of the board that bypasses ``_write``, passed every test (review of X-cx L7
A2, L2).  This test reads the source instead.

**What it checks is a mutation spelled as a string literal that begins with
``mutation``** (any case), and its scope is the App's writes.  ``_tracker`` is the one
module that speaks to the tracker as the App, so every such literal it holds must be the
value of a module-level constant (assigned plainly or with a type) whose every use in it
is the first argument of ``self._write`` inside :class:`_tracker.Board`, and no call there
hands ``graphql`` or ``graphql_lookup`` one; every other module of the tool holds none.
``setup_tracker`` is the one module outside that scope: it configures the tracker as the
DEVELOPER (``main`` connects with ``user_token()``), so its mutations are his login's
writes, which this fence was never about (``_github``'s module docstring: the two
identities).

**What it cannot see**, which a reviewer of a new write must (review of A2b): a mutation
ASSEMBLED rather than spelled -- a concatenation that splits the keyword
(``"mut" + "ation ..."``), an f-string with no leading literal, ``"".join(...)`` -- and
one reached through an attribute or a local rather than a module-level constant
(``self.github.graphql(self.query)``); and any module below ``tools/quill``'s top level,
since :func:`_modules` reads ``tools/quill/*.py`` alone (the tool has no subpackage).
"""
from __future__ import annotations

import ast
import re
from pathlib import Path

QUILL = Path(__file__).resolve().parent
#: A GraphQL mutation's text: the operation keyword first, in any case.
_MUTATION = re.compile(r"^\s*mutation\b", re.IGNORECASE)
#: The module that sends the App's writes, and the one that writes as the developer.
_TRACKER, _DEVELOPERS = "_tracker.py", "setup_tracker.py"
_SENDS = {"graphql", "graphql_lookup"}


def _modules() -> dict[str, ast.Module]:
    """Every module of the tool but its tests, parsed, by file name."""
    return {path.name: ast.parse(path.read_text(encoding="utf-8"))
            for path in sorted(QUILL.glob("*.py"))
            if not path.name.startswith("test_") and path != QUILL / "conftest.py"}


def _is_mutation(node: ast.AST) -> bool:
    """Whether ``node`` is, or is built from, a string constant that is a mutation."""
    return any(isinstance(part, ast.Constant) and isinstance(part.value, str)
               and _MUTATION.match(part.value) for part in ast.walk(node))


def _constants(tree: ast.Module) -> list[str]:
    """The module-level names a mutation is assigned to, plainly or with a type
    (``BOARD_ADD = "mutation ..."``, ``BOARD_ADD: str = "mutation ..."``)."""
    names = []
    for statement in tree.body:
        if isinstance(statement, ast.Assign) and _is_mutation(statement.value):
            names += [target.id for target in statement.targets if isinstance(target, ast.Name)]
        elif (isinstance(statement, ast.AnnAssign) and statement.value is not None
              and _is_mutation(statement.value) and isinstance(statement.target, ast.Name)):
            names.append(statement.target.id)
    return names


def _parents(tree: ast.Module) -> dict[ast.AST, ast.AST]:
    """Each node's parent."""
    return {child: node for node in ast.walk(tree) for child in ast.iter_child_nodes(node)}


def _inside(node: ast.AST, parents: dict, kind: type, name: str) -> bool:
    """Whether ``node`` sits inside a ``kind`` (class or function) named ``name``."""
    while node in parents:
        node = parents[node]
        if isinstance(node, kind) and node.name == name:
            return True
    return False


def fence_breaches(name: str, tree: ast.Module) -> list[str]:
    """Each way module ``name`` sends, or could send, a mutation around ``Board._write``.

    Args:
        name: The module's file name.
        tree: Its parsed source.

    Returns:
        One line per breach, naming its source line; empty when the module keeps the
        fence.
    """
    if name == _DEVELOPERS:
        return []
    parents = _parents(tree)
    constants = [node for node in ast.walk(tree)
                 if isinstance(node, ast.Constant) and _is_mutation(node)]
    if name != _TRACKER:
        return [f"{name}:{node.lineno} holds a mutation outside {_TRACKER}" for node in constants]
    named = set(_constants(tree))
    breaches = [f"{name}:{node.lineno} holds a mutation that is no module-level constant"
                for node in constants
                if not isinstance(parents.get(_statement(node, parents)), ast.Module)]
    for node in ast.walk(tree):
        if (isinstance(node, ast.Name) and node.id in named and isinstance(node.ctx, ast.Load)
                and not _board_writes(node, parents)):
            breaches.append(f"{name}:{node.lineno} uses the mutation {node.id} other than "
                            "as self._write's first argument inside Board")
        if _sends_mutation(node, named):
            breaches.append(f"{name}:{node.lineno} hands {node.func.attr} a mutation directly")
    return breaches


def _board_writes(name: ast.Name, parents: dict) -> bool:
    """Whether ``name`` is the first argument of a ``self._write(...)`` call inside ``Board``."""
    call = parents.get(name)
    if not (isinstance(call, ast.Call) and call.args and call.args[0] is name):
        return False
    method = call.func
    return (isinstance(method, ast.Attribute) and method.attr == "_write"
            and isinstance(method.value, ast.Name) and method.value.id == "self"
            and _inside(name, parents, ast.ClassDef, "Board"))


def _sends_mutation(node: ast.AST, named: set[str]) -> bool:
    """Whether ``node`` calls ``graphql`` or ``graphql_lookup`` with a mutation, written out
    or by the name of a module-level one, as its query."""
    if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
            and node.func.attr in _SENDS and node.args):
        return False
    query = node.args[0]
    return _is_mutation(query) or (isinstance(query, ast.Name) and query.id in named)


def _statement(node: ast.AST, parents: dict) -> ast.AST:
    """The statement ``node`` belongs to."""
    while not isinstance(node, ast.stmt):
        node = parents[node]
    return node


def test_every_mutation_the_app_sends_goes_through_board_write():
    """The tool as committed keeps the fence, and it HAS mutations to keep it on: the
    board's six, its four moves and its two Status writes (rulings plan#1873, plan#1905),
    each a module-level constant of ``_tracker``."""
    modules = _modules()
    assert {name: fence_breaches(name, tree) for name, tree in modules.items()
            if fence_breaches(name, tree)} == {}
    assert sorted(_constants(modules[_TRACKER])) == [
        "BOARD_ADD", "BOARD_AFTER", "BOARD_REMOVE", "BOARD_STATUS_CLEAR", "BOARD_STATUS_SET",
        "BOARD_TOP"]


_PLANTED = '''
BOARD_ADD = "mutation($p: ID!) { addProjectV2ItemById(input: {projectId: $p}) { item { id } } }"

class Board:
    def _write(self, mutation, **variables):
        return self.github.graphql(mutation, p=self.board_id, **variables)

    def add(self, card):
        return self._write(BOARD_ADD, c=card)

class Tracker:
    def close_board(self):
        self.github.graphql("mutation($id: ID!) { deleteProjectV2(input: {projectId: $id}) "
                            "{ clientMutationId } }", id="PVT_other")

    def add_twice(self, card):
        return self.board._write(BOARD_ADD, c=card)

    def raw(self):
        self.github.rest("POST", "/graphql", {"query": "mutation { x }"})
'''


def test_a_planted_raw_mutation_is_a_breach_and_board_write_is_not():
    """The scanner, proven on planted source: a raw mutation sent from ``Tracker``, one sent
    through REST, and a board write reached from outside ``Board`` are each a breach; the
    board's own ``_write`` of its constant is not.  Outside ``_tracker`` any mutation is."""
    assert fence_breaches(_TRACKER, ast.parse(_PLANTED)) == [
        "_tracker.py:13 holds a mutation that is no module-level constant",
        "_tracker.py:20 holds a mutation that is no module-level constant",
        "_tracker.py:13 hands graphql a mutation directly",
        "_tracker.py:17 uses the mutation BOARD_ADD other than as self._write's first argument "
        "inside Board",
    ]
    assert fence_breaches("quill.py", ast.parse('Q = "mutation { x }"')) == [
        "quill.py:1 holds a mutation outside _tracker.py"]


_TYPED = '''
DROP: str = "mutation($id: ID!) { deleteProjectV2(input: {projectId: $id}) { clientMutationId } }"

class Tracker:
    def drop(self):
        self.github.graphql(DROP, id="PVT_other")
'''


def test_a_typed_constant_or_an_upper_case_keyword_is_seen_too():
    """A module-level constant written with a type (``DROP: str = "mutation ..."``), the
    shape this repository's constants often take, is a constant the fence holds like any
    other: sent around ``Board._write``, it is a breach (review of A2b: it passed).  The
    keyword is matched in any case."""
    assert fence_breaches(_TRACKER, ast.parse(_TYPED)) == [
        "_tracker.py:6 hands graphql a mutation directly",
        "_tracker.py:6 uses the mutation DROP other than as self._write's first argument "
        "inside Board",
    ]
    assert fence_breaches("quill.py", ast.parse('Q = "MUTATION { x }"')) == [
        "quill.py:1 holds a mutation outside _tracker.py"]
