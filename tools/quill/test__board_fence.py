"""The GraphQL mutations quill's App sends, as far as its source shows them (What it checks,
What it cannot see), go through one of its two doors: :meth:`_tracker.Board._write`, or
:meth:`_suggestion.Suggestions._write` (ruling plan#3257).

``Board._write`` names the place's board as ``$p`` and no other, the fence that keeps a
write aimed at one tracker off another's board (``_tracker``'s module docstring).  It was
a fence by CONVENTION: a raw ``mutation`` string sent from :class:`_tracker.Tracker`, or
from a method of the board that bypasses ``_write``, passed every test (review of X-cx L7
A2, L2).  This test reads the source instead.  ``Suggestions._write`` names one card's node
id as ``$issue``: GitHub writes a card's suggested Priority only through GraphQL (measured
2026-11-10), and a card is the tracker repository's, which the App's token is narrowed to,
where the board is the organization's (``_suggestion``'s module docstring).

**What it checks is a mutation spelled as a string literal that begins with
``mutation``** (any case), and its scope is the App's writes.  ``_tracker`` and
``_suggestion`` are the modules that send the tracker the App's GraphQL writes (:data:`_DOORS`),
so every such literal each holds must be the value of a module-level constant (assigned
plainly or with a type) whose every use in it is the first argument of ``self._write`` inside
its door's class, :class:`_tracker.Board` or :class:`_suggestion.Suggestions`, and no call
there hands ``graphql`` or ``graphql_lookup`` one; every other module of the tool holds none.
And no module hands ``graphql`` or ``graphql_lookup`` a door's constant reached through an
import (:func:`_imported_sends`; review of plan#2798, finding 3): by its name, or as an
attribute of the door module imported.  ``_recorded`` imports them to admit them in a replay,
never to send them, which is no breach.  ``setup_tracker`` is the one module outside that
scope: it configures the tracker as the DEVELOPER (``main`` connects with ``user_token()``),
so its mutations are his login's writes, which this fence was never about (``_github``'s
module docstring: the two identities).

**What it cannot see**, which a reviewer of a new write must (review of A2b): a mutation
ASSEMBLED rather than spelled -- a concatenation that splits the keyword
(``"mut" + "ation ..."``), an f-string with no leading literal, ``"".join(...)`` -- and
one reached through an attribute or a local rather than a module-level constant
(``self.github.graphql(self.query)``); a door's constant imported, then re-bound to a local or
sent by another route than ``graphql``'s or ``graphql_lookup``'s first argument (REST's body, a
helper's argument); a door's own ``_write``, which is the door, so a ``Suggestions._write``
naming another ``$issue`` than its card's node id, or a ``Board._write`` another ``$p``, passes
(:func:`test_a_doors_own_write_is_read_as_the_door`); and any module below ``tools/quill``'s top
level, since :func:`_modules` reads ``tools/quill/*.py`` alone (the tool has no subpackage).
"""
from __future__ import annotations

import ast
import functools
import re
from pathlib import Path

QUILL = Path(__file__).resolve().parent
#: A GraphQL mutation's text: the operation keyword first, in any case.
_MUTATION = re.compile(r"^\s*mutation\b", re.IGNORECASE)
#: The modules that send the App's GraphQL writes, the one that writes as the developer, and
#: each sending module's door, the class whose ``self._write`` sends them (ruling plan#3257).
_TRACKER, _SUGGESTION, _DEVELOPERS = "_tracker.py", "_suggestion.py", "setup_tracker.py"
_DOORS = {_TRACKER: "Board", _SUGGESTION: "Suggestions"}
_SENDS = {"graphql", "graphql_lookup"}
#: The tool's package, as an absolute import names it.
_PACKAGE = "tools.quill"


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
    """Each way module ``name`` sends, or could send, a mutation around its door's ``_write``
    (:data:`_DOORS`).

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
    if name not in _DOORS:
        return [f"{name}:{node.lineno} holds a mutation outside {' and '.join(_DOORS)}"
                for node in constants] + _imported_sends(name, tree)
    door = _DOORS[name]
    named = set(_constants(tree))
    breaches = [f"{name}:{node.lineno} holds a mutation that is no module-level constant"
                for node in constants
                if not isinstance(parents.get(_statement(node, parents)), ast.Module)]
    for node in ast.walk(tree):
        if (isinstance(node, ast.Name) and node.id in named and isinstance(node.ctx, ast.Load)
                and not _door_writes(node, parents, door)):
            breaches.append(f"{name}:{node.lineno} uses the mutation {node.id} other than "
                            f"as self._write's first argument inside {door}")
        if _sends_mutation(node, named):
            breaches.append(f"{name}:{node.lineno} hands {node.func.attr} a mutation directly")
    return breaches + _imported_sends(name, tree)


def _door_writes(name: ast.Name, parents: dict, door: str) -> bool:
    """Whether ``name`` is the first argument of a ``self._write(...)`` call inside the class
    ``door``."""
    call = parents.get(name)
    if not (isinstance(call, ast.Call) and call.args and call.args[0] is name):
        return False
    method = call.func
    return (isinstance(method, ast.Attribute) and method.attr == "_write"
            and isinstance(method.value, ast.Name) and method.value.id == "self"
            and _inside(name, parents, ast.ClassDef, door))


def _query_sent(node: ast.AST) -> ast.expr | None:
    """The query ``node`` hands ``graphql`` or ``graphql_lookup``, its first argument; None
    when it is no such call."""
    if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
            and node.func.attr in _SENDS and node.args):
        return node.args[0]
    return None


def _sends_mutation(node: ast.AST, named: set[str]) -> bool:
    """Whether ``node`` calls ``graphql`` or ``graphql_lookup`` with a mutation, written out
    or by the name of a module-level one, as its query."""
    if (query := _query_sent(node)) is None:
        return False
    return _is_mutation(query) or (isinstance(query, ast.Name) and query.id in named)


@functools.cache
def _door_constants() -> dict[str, frozenset[str]]:
    """Each door module's mutation constants (:func:`_constants`), by its module's name
    without ``.py``, read from the tool as committed: what an import from a door can bring."""
    return {Path(door).stem: frozenset(_constants(ast.parse(
        (QUILL / door).read_text(encoding="utf-8")))) for door in _DOORS}


def _door_of(module: str | None) -> str | None:
    """The door module an absolute module name names (``tools.quill._suggestion``:
    ``_suggestion``); None for any other."""
    stem = (module or "").removeprefix(f"{_PACKAGE}.")
    return stem if module == f"{_PACKAGE}.{stem}" and stem in _door_constants() else None


def _absolute(node: ast.ImportFrom) -> str | None:
    """The module a ``from ... import`` reads from, absolute (``from ._suggestion``:
    ``tools.quill._suggestion``, ``from .``: ``tools.quill``); None above the package."""
    if node.level == 0:
        return node.module
    if node.level == 1:
        return _PACKAGE if node.module is None else f"{_PACKAGE}.{node.module}"
    return None


def _imported(tree: ast.Module) -> dict[str, str]:
    """Each dotted name by which ``tree`` reaches a door's mutation constant through an
    import, and the constant it reaches, ``door.NAME``: ``from tools.quill._suggestion import
    SUGGEST`` binds ``SUGGEST`` (or its ``as`` name); ``from tools.quill import _suggestion``
    and ``import tools.quill._suggestion as s`` bind ``_suggestion.SUGGEST`` and
    ``s.SUGGEST``; a plain ``import tools.quill._suggestion`` binds
    ``tools.quill._suggestion.SUGGEST``; a relative import is read as its absolute one."""
    doors, reached = _door_constants(), {}
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and (source := _absolute(node)) is not None:
            for alias in node.names:
                local = alias.asname or alias.name
                if (door := _door_of(source)) and alias.name in doors[door]:
                    reached[local] = f"{door}.{alias.name}"
                elif source == _PACKAGE and (door := _door_of(f"{_PACKAGE}.{alias.name}")):
                    reached |= {f"{local}.{const}": f"{door}.{const}" for const in doors[door]}
        elif isinstance(node, ast.Import):
            for alias in node.names:
                if door := _door_of(alias.name):
                    reached |= {f"{alias.asname or alias.name}.{const}": f"{door}.{const}"
                                for const in doors[door]}
    return reached


def _dotted(node: ast.AST | None) -> str | None:
    """The dotted name ``node`` is written as (``SUGGEST``, ``_suggestion.SUGGEST``); None for
    any other expression, or none."""
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute) and (base := _dotted(node.value)) is not None:
        return f"{base}.{node.attr}"
    return None


def _imported_sends(name: str, tree: ast.Module) -> list[str]:
    """Each call in module ``name`` that hands ``graphql`` or ``graphql_lookup`` a door's
    mutation constant reached through an import (:func:`_imported`) as its query: a write
    sent around its door (review of plan#2798, finding 3).  A constant imported for another
    use, as ``_recorded`` admits one in a replay, is no breach."""
    reached = _imported(tree)
    return [f"{name}:{node.lineno} hands {node.func.attr} {reached[query]}, imported, around "
            "its door" for node in ast.walk(tree)
            if (query := _dotted(_query_sent(node))) in reached]


def _statement(node: ast.AST, parents: dict) -> ast.AST:
    """The statement ``node`` belongs to."""
    while not isinstance(node, ast.stmt):
        node = parents[node]
    return node


def test_every_mutation_the_app_sends_goes_through_one_of_its_two_doors():
    """The tool as committed keeps the fence, and it HAS mutations to keep it on: the
    board's six, its four moves and its two Status writes (rulings plan#1873, plan#1905),
    each a module-level constant of ``_tracker``, and the suggestion's two, its put and its
    take-back (ruling plan#3257), each one of ``_suggestion``."""
    modules = _modules()
    assert {name: fence_breaches(name, tree) for name, tree in modules.items()
            if fence_breaches(name, tree)} == {}
    assert sorted(_constants(modules[_TRACKER])) == [
        "BOARD_ADD", "BOARD_AFTER", "BOARD_REMOVE", "BOARD_STATUS_CLEAR", "BOARD_STATUS_SET",
        "BOARD_TOP"]
    assert sorted(_constants(modules[_SUGGESTION])) == ["SUGGEST", "WITHDRAW"]


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
        "quill.py:1 holds a mutation outside _tracker.py and _suggestion.py"]


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
        "quill.py:1 holds a mutation outside _tracker.py and _suggestion.py"]


_CARD_DOOR = '''
SUGGEST = "mutation($issue: ID!) { setIssueFieldValue(input: {issueId: $issue}) { x } }"

class Suggestions:
    def _write(self, mutation, card, **variables):
        return self.github.graphql(mutation, issue=card.node_id, **variables)

    def put(self, card):
        self._write(SUGGEST, card)

class Board:
    def _write(self, mutation, **variables):
        return self.github.graphql(mutation, p=self.board_id, **variables)

    def suggest(self, card):
        return self._write(SUGGEST, card)
'''


def test_the_card_door_holds_its_own_writes_and_no_other_class_sends_them():
    """The second door (ruling plan#3257), proven on planted source: in ``_suggestion`` a
    mutation sent through ``Suggestions._write`` is no breach, and the same constant sent
    through another class's ``_write`` is one, as a board write sent from outside ``Board`` is
    in ``_tracker``; and ``_tracker``'s door is ``Board`` alone, so a constant sent through
    ``Suggestions._write`` there is a breach."""
    assert fence_breaches(_SUGGESTION, ast.parse(_CARD_DOOR)) == [
        "_suggestion.py:16 uses the mutation SUGGEST other than as self._write's first "
        "argument inside Suggestions"]
    assert fence_breaches(_TRACKER, ast.parse(_CARD_DOOR)) == [
        "_tracker.py:9 uses the mutation SUGGEST other than as self._write's first argument "
        "inside Board"]


_IMPORTED = '''
from tools.quill._suggestion import SUGGEST
from tools.quill._suggestion import WITHDRAW as TAKE_BACK
from tools.quill import _tracker as board
import tools.quill._suggestion
import tools.quill._tracker as tracker
from ._tracker import BOARD_TOP
from . import _suggestion

ADMITTED = {SUGGEST, TAKE_BACK, board.BOARD_ADD}

def send(github):
    github.graphql(SUGGEST, issue="I_another")
    github.graphql_lookup(TAKE_BACK, issue="I_another")
    github.graphql(board.BOARD_REMOVE, p="PVT_other")
    github.graphql(tools.quill._suggestion.WITHDRAW, issue="I_another")
    github.graphql(tracker.BOARD_AFTER, p="PVT_other")
    github.graphql(BOARD_TOP, p="PVT_other")
    github.graphql(_suggestion.SUGGEST, issue="I_another")

def admit(query):
    return query == SUGGEST or query in ADMITTED
'''


def test_a_doors_mutation_imported_and_sent_elsewhere_is_a_breach():
    """Review of plan#2798, finding 3, planted: a door's constant imported into another module
    and handed to ``graphql`` or ``graphql_lookup`` is sent around its door, each of the two
    doors' and each way of importing one, the review's probe (``SUGGEST`` imported by name)
    first; a constant imported to be compared, as ``_recorded`` admits one in a replay, is
    none.  In a door, the other door's constant sent so is a breach too."""
    assert fence_breaches("_filing.py", ast.parse(_IMPORTED)) == [
        "_filing.py:13 hands graphql _suggestion.SUGGEST, imported, around its door",
        "_filing.py:14 hands graphql_lookup _suggestion.WITHDRAW, imported, around its door",
        "_filing.py:15 hands graphql _tracker.BOARD_REMOVE, imported, around its door",
        "_filing.py:16 hands graphql _suggestion.WITHDRAW, imported, around its door",
        "_filing.py:17 hands graphql _tracker.BOARD_AFTER, imported, around its door",
        "_filing.py:18 hands graphql _tracker.BOARD_TOP, imported, around its door",
        "_filing.py:19 hands graphql _suggestion.SUGGEST, imported, around its door",
    ]
    assert fence_breaches(_TRACKER, ast.parse(
        "from tools.quill._suggestion import SUGGEST\n\nclass Board:\n"
        "    def _write(self, mutation):\n"
        "        return self.github.graphql(SUGGEST, p=self.board_id)\n")) == [
            "_tracker.py:5 hands graphql _suggestion.SUGGEST, imported, around its door"]


def test_a_doors_own_write_is_read_as_the_door():
    """What the fence cannot see (its docstring), planted as the review of plan#2798 probed
    it: a door's ``_write`` is the door, so one naming another ``$issue`` than its card's node
    id passes as its committed shape does.  A reviewer of a change to a door reads it."""
    aimed_elsewhere = _CARD_DOOR.replace("issue=card.node_id", 'issue="I_another"')
    assert aimed_elsewhere != _CARD_DOOR
    assert (fence_breaches(_SUGGESTION, ast.parse(aimed_elsewhere))
            == fence_breaches(_SUGGESTION, ast.parse(_CARD_DOOR)))
