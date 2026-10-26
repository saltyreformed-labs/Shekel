"""Where every node of a source module goes under a plan, and what each output must import.

The layout is a pure function of the source and the plan.  It places each top-level node
exactly once: a test where the plan names it (or its slices, for a split class), every
fixture in ``conftest.py``, every helper no part names in ``_helpers.py``.  Then it resolves
each output's free names, read by :mod:`symtable`, against the source's own bindings: an
import alias is re-imported from the statement that bound it, a node placed in
``_helpers.py`` is imported from there, a builtin needs nothing.  Anything else is refused;
the tool never guesses where a name comes from.

**Refused here, beyond what the source reader and the class splitter refuse:** a test the
plan does not place, a fixture a part names, a name placed twice; a module-level fixture of
any scope but ``function`` (a package ``conftest.py`` would run it once per part); a
module-level ``pytest_*`` hook, ``pytestmark``, ``pytest_plugins`` or dunder (a conftest
widens a hook's reach, and a part would not carry the others); ``global``; a no-argument
``vars()``; an ``inspect`` read of a moved node (``getsource`` of a node that moves whole
aside: it returns the node's own text, which a verbatim move keeps), or a moved node's
``__module__``; a ``request`` attribute naming the requesting module or file; a free name
naming the module itself (``__file__``, ``__name__``, ...: a part sits one directory
deeper); a node in one part reading a node placed in another; ``globals()`` unless every
node reading each key it writes lands in the same output; and an import alias nothing reads
that the plan does not place.
"""
from __future__ import annotations

import ast
import builtins
from dataclasses import dataclass, field

from tools.split._classes import ClassShape, check_split, class_shape, mixin_name
from tools.split._model import CONFTEST, HELPERS, ClassSlice, Part, Plan
from tools.split._source import (
    CollectRules,
    Kind,
    Refusal,
    SourceModule,
    fixture_decorator,
    fixture_scope,
    free_names,
    member_free_names,
)

#: Module-level names that refer to the module itself, so their meaning moves with the code.
MODULE_DUNDERS = frozenset({
    "__file__", "__name__", "__spec__", "__loader__", "__package__", "__doc__",
    "__cached__", "__builtins__", "__annotations__", "__dict__", "__path__",
})

#: What a name may resolve to without an import.
BUILTINS = frozenset(dir(builtins)) - MODULE_DUNDERS

#: Module-level names pytest reads off a module, which a part would not carry.
_PYTEST_MODULE_NAMES = frozenset({"pytestmark", "pytest_plugins"})

#: Attributes of a fixture's ``request`` that name the requesting module or its file.
_REQUEST_LOCATION = frozenset({"module", "fspath", "path", "nodeid"})

#: The one :mod:`inspect` read of a moved node that means the same in any part: the text of
#: a node that moves whole, which a verbatim move keeps.  ``getsourcelines`` also returns the
#: node's first line number, which a move changes, so it is refused with the rest.
_INSPECT_TEXT = "getsource"

#: The marker a split class's name is homed at: it is bound in several parts, so no other
#: output may reach it by name.
SPLIT_HOME = "<split across parts>"


@dataclass(frozen=True)
class Piece:
    """One thing an output holds, keyed by the source line its block started at: a whole
    unit (no ``shape``), one slice of a split class, or a split class's mixin (``shape``
    without ``slice``)."""

    position: int
    binds: str
    free: frozenset[str]
    nodes: tuple[ast.AST, ...]
    shape: ClassShape | None = None
    slice: ClassSlice | None = None

    @property
    def first_slice(self) -> bool:
        """Whether this is its class's first slice, which carries the class's leading
        comments."""
        return bool(self.slice and self.shape
                    and self.slice.tests[0] == self.shape.test_order()[0])


@dataclass
class Output:
    """One module the source becomes: its pieces in source order and what it imports."""

    module: str
    docstring: str
    pieces: list[Piece]
    names: set[str] = field(default_factory=set)
    helpers: list[str] = field(default_factory=list)

    def binds(self) -> set[str]:
        """Every module-level name the output's own pieces bind."""
        return {p.binds for p in self.pieces}

    def free(self) -> set[str]:
        """Every module-level name its pieces read and do not bind themselves."""
        return set().union(*(p.free for p in self.pieces)) - self.binds()


@dataclass(frozen=True)
class Layout:
    """The resolved split: every output, every name's home, and the split classes."""

    source: SourceModule
    plan: Plan
    outputs: dict[str, Output]
    home: dict[str, str]
    split: dict[str, ClassShape]

    def module_of(self, top: str, method: str | None) -> str:
        """The output a collected test lands in, from its top-level name and, for a split
        class, its method name."""
        if top in self.split:
            for output in self.outputs.values():
                for piece in output.pieces:
                    if piece.slice and piece.slice.class_name == top and method in (
                            piece.slice.tests):
                        return output.module
            raise Refusal(f"{top}::{method} is in no slice")
        return self.home[top]


def _check_module_level(source: SourceModule) -> None:
    """Refuse module-level names pytest reads off the module, and fixtures scoped beyond
    one test."""
    for unit in source.units:
        name = unit.name
        if name.startswith("pytest_") or name in _PYTEST_MODULE_NAMES or (
                name.startswith("__") and name.endswith("__")):
            raise Refusal(f"{name}: pytest reads it off the module; moved into a part or a "
                          "conftest its reach would change")
        if unit.kind is Kind.FIXTURE:
            scope = fixture_scope(fixture_decorator(unit.block.node))
            if scope != "function":
                raise Refusal(f"fixture {name}: scope {scope!r} would run once per part")


def _placements(source: SourceModule, plan: Plan
                ) -> tuple[dict[str, str], dict[str, list[tuple[str, ClassSlice]]]]:
    """Each whole-placed name's module, and each split class's slices in plan order."""
    placed: dict[str, str] = {}
    slices: dict[str, list[tuple[str, ClassSlice]]] = {}
    for part in plan.parts:
        _place_part(source, part, placed, slices)
    for name, runs in slices.items():
        unit = source.unit(name)
        if unit is None or unit.kind is not Kind.TEST or name in placed:
            raise Refusal(f"{name}: only a test class placed nowhere whole can be sliced")
        modules = [module for module, _ in runs]
        if len(set(modules)) != len(modules):
            raise Refusal(f"{name}: two slices in one part; make them one slice")
    for unit in source.units:
        if unit.name in placed or unit.name in slices:
            continue
        if unit.kind is Kind.TEST:
            raise Refusal(f"test {unit.name} is placed in no part")
        placed[unit.name] = CONFTEST if unit.kind is Kind.FIXTURE else HELPERS
    return placed, slices


def _place_part(source: SourceModule, part: Part, placed: dict[str, str],
                slices: dict[str, list[tuple[str, ClassSlice]]]) -> None:
    """Record one part's placements, refusing an unknown, doubled or fixture name."""
    for node in part.nodes:
        if isinstance(node, ClassSlice):
            slices.setdefault(node.class_name, []).append((part.module, node))
            continue
        unit = source.unit(node)
        if unit is None:
            raise Refusal(f"part {part.module}: no top-level {node!r} in the source")
        if unit.kind is Kind.FIXTURE:
            raise Refusal(f"part {part.module}: fixture {node} belongs in conftest.py")
        if node in placed:
            raise Refusal(f"{node}: placed in both {placed[node]} and {part.module}")
        placed[node] = part.module


def _whole_piece(source: SourceModule, name: str) -> Piece:
    """The piece a whole top-level unit is."""
    block = source.unit(name).block
    text = "\n".join(block.text(source.lines)) + "\n"
    return Piece(position=block.first, binds=name, free=free_names(text), nodes=(block.node,))


def _split_pieces(shape: ClassShape, runs: list[tuple[str, ClassSlice]],
                  lines: tuple[str, ...]) -> list[tuple[str, Piece]]:
    """A split class's pieces, each with the module it goes to: one per slice, and the
    mixin in ``_helpers.py``."""
    by_name = {m.name: m for m in shape.members}
    mixin = mixin_name(shape.name)
    found = []
    for module, run in runs:
        members = [by_name[t] for t in run.tests]
        free = set().union(*(member_free_names(m.text(lines)) for m in members))
        found.append((module, Piece(position=shape.unit.block.first, binds=shape.name,
                                    free=frozenset(free | {mixin}),
                                    nodes=tuple(m.node for m in members), shape=shape,
                                    slice=run)))
    rest = shape.non_tests()
    free = set().union(*(member_free_names(m.text(lines)) for m in rest))
    found.append((HELPERS, Piece(position=shape.unit.block.first, binds=mixin,
                                 free=frozenset(free), nodes=tuple(m.node for m in rest),
                                 shape=shape)))
    return found


def _pieces(source: SourceModule, plan: Plan, rules: CollectRules
            ) -> tuple[dict[str, list[Piece]], dict[str, str], dict[str, ClassShape]]:
    """Every output's pieces, every name's home, and the split classes' shapes."""
    placed, slices = _placements(source, plan)
    pieces: dict[str, list[Piece]] = {}
    for name, module in placed.items():
        pieces.setdefault(module, []).append(_whole_piece(source, name))
    home = dict(placed)
    shapes = {}
    for name, runs in slices.items():
        shape = class_shape(source.unit(name), source.lines, rules)
        check_split(shape, [s for _, s in runs], source.lines)
        shapes[name] = shape
        home[name] = SPLIT_HOME
        home[mixin_name(name)] = HELPERS
        for module, piece in _split_pieces(shape, runs, source.lines):
            pieces.setdefault(module, []).append(piece)
    return pieces, home, shapes


def _is_globals_call(node: ast.AST) -> bool:
    """Whether ``node`` is a call of ``globals()``."""
    return (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
            and node.func.id == "globals" and not node.args)


def _string(node: ast.AST) -> str | None:
    """A string literal's value, or None."""
    return node.value if isinstance(node, ast.Constant) and isinstance(node.value, str) else None


def _patch_key(node: ast.AST) -> str | None:
    """The key ``node`` patches when it is ``<x>.setitem(globals(), "<key>", ...)`` or
    ``<x>.delitem(globals(), "<key>")`` (a patch its fixture undoes); else None."""
    if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)):
        return None
    if node.func.attr in {"setitem", "delitem"} and len(node.args) >= 2 and _is_globals_call(
            node.args[0]):
        return _string(node.args[1])
    return None


def globals_keys(nodes: tuple[ast.AST, ...]) -> set[str] | None:
    """The keys the ``globals()`` calls in ``nodes`` patch, or None when any call is used in
    another shape: a store or read through the dict (a store nothing undoes; a read of a key
    a part may not bind), a computed key, or the dict itself."""
    walked = [sub for top in nodes for sub in ast.walk(top)]
    calls = sum(1 for sub in walked if _is_globals_call(sub))
    patched = [key for key in map(_patch_key, walked) if key is not None]
    return set(patched) if len(patched) == calls else None


def _root_name(node: ast.AST) -> str | None:
    """The name an attribute or subscript chain starts from."""
    while isinstance(node, (ast.Attribute, ast.Subscript, ast.Call)):
        node = node.func if isinstance(node, ast.Call) else node.value
    return node.id if isinstance(node, ast.Name) else None


def _inspect_names(source: SourceModule) -> dict[str, str | None]:
    """The names that call into :mod:`inspect`, each with the function it names: the module
    itself (None: the attribute called names it), and any function the import block binds
    from it."""
    found: dict[str, str | None] = {"inspect": None}
    for name, stmt in source.import_bindings().items():
        if isinstance(stmt.node, ast.ImportFrom) and stmt.node.module == "inspect":
            found[name] = next(alias.name for alias in stmt.node.names
                               if (alias.asname or alias.name) == name)
    return found


def _inspect_read(call: ast.Call, source_names: set[str], split_names: set[str],
                  inspect_names: dict[str, str | None]) -> str | None:
    """Why an :mod:`inspect` call on a moved node could change meaning once moved, or None.

    ``getsource`` of a node that moves whole returns that node's own text, which a verbatim
    move keeps, so it is accepted.  Every other read of a moved node is refused: its module
    or file (``getfile``, ``getmodule``, ...), its line number (``getsourcelines``), or the
    text of a split class, which each part holds only a slice of."""
    func = call.func
    callee = func.value if isinstance(func, ast.Attribute) else func
    if not (isinstance(callee, ast.Name) and callee.id in inspect_names):
        return None
    read = {_root_name(a) for a in call.args} & source_names
    if not read:
        return None
    if (func.attr if func is not callee else inspect_names[callee.id]) != _INSPECT_TEXT:
        return "inspect reads a moved node's module or file"
    if read & split_names:
        return "inspect.getsource reads a split class, which each part holds a slice of"
    return None


def _check_node(sub: ast.AST, source_names: set[str], split_names: set[str],
                inspect_names: dict[str, str | None]) -> str | None:
    """Why one AST node could change meaning once moved, or None."""
    if isinstance(sub, ast.Global):
        return f"global {', '.join(sub.names)}"
    if isinstance(sub, ast.Call):
        func = sub.func
        if isinstance(func, ast.Name) and func.id == "vars" and not sub.args:
            return "vars() reads the module it runs in"
        return _inspect_read(sub, source_names, split_names, inspect_names)
    if isinstance(sub, ast.Attribute):
        if sub.attr == "__module__" and _root_name(sub.value) in source_names:
            return f"{_root_name(sub.value)}.__module__ names its module"
        if sub.attr in _REQUEST_LOCATION and _root_name(sub.value) == "request":
            return f"request...{sub.attr} names the requesting module or file"
    return None


def _check_nodes(output: Output, source: SourceModule, split_names: set[str]) -> None:
    """Refuse a construct in ``output`` whose meaning depends on the module it runs in;
    ``split_names`` are the split classes."""
    source_names = {u.name for u in source.units}
    inspect_names = _inspect_names(source)
    for piece in output.pieces:
        for sub in (s for top in piece.nodes for s in ast.walk(top)):
            why = _check_node(sub, source_names, split_names, inspect_names)
            if why:
                raise Refusal(f"{piece.binds} (to {output.module}): {why}")


def _patches_outside_a_test_body(unit_node: ast.AST, rules: CollectRules) -> bool:
    """Whether a ``globals()`` patch in ``unit_node`` sits anywhere but directly in a test
    function's body: the test function itself, or a test method of a test class (a patch
    in a fixture is live for every test that requests it; one in a nested function runs
    whenever that function does)."""
    bodies: list[ast.AST] = []
    if isinstance(unit_node, (ast.FunctionDef, ast.AsyncFunctionDef)):
        bodies = [unit_node]
    elif isinstance(unit_node, ast.ClassDef):
        bodies = [m for m in unit_node.body
                  if isinstance(m, (ast.FunctionDef, ast.AsyncFunctionDef))]
    inside = set()
    for body in bodies:
        if not rules.is_test_function(body.name):
            continue
        nested = {id(s) for n in ast.walk(body) if n is not body and isinstance(
            n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda, ast.ClassDef))
            for s in ast.walk(n)}
        inside |= {id(s) for s in ast.walk(body) if id(s) not in nested}
    return any(_patch_key(s) is not None and id(s) not in inside for s in ast.walk(unit_node))


def _reach(start: frozenset[str], frees: dict[str, set[str]], fixtures: set[str]) -> set[str]:
    """Every source node reached from the names in ``start`` by name, transitively (a name
    read as a value counts as much as a call), plus every module-level fixture."""
    reached: set[str] = set()
    pending = (set(start) | fixtures) & set(frees)
    while pending:
        name = pending.pop()
        reached.add(name)
        pending |= (frees[name] & set(frees)) - reached
    return reached


def _check_globals(outputs: dict[str, Output], source: SourceModule, rules: CollectRules
                   ) -> None:
    """Refuse a ``globals()`` patch whose reach the split could break (the reach rule, the
    coordinator's correction to Fork 3 item 6).

    ``<x>.setitem(globals(), k, v)`` rebinds ``k`` in the dict of the module that DEFINES
    the calling test, only while that test runs (one test at a time per process; undone at
    teardown).  The only code that reads that dict's ``k`` is code defined in the same
    module and run during the test: everything the test reaches by name, transitively, and
    (conservatively) every module-level fixture.  So each reached node that reads ``k`` must
    land in the test's part; one landing anywhere else, ``_helpers.py`` and ``conftest.py``
    included, would read its own module's ``k`` and miss the patch.  The patch must sit
    directly in a test function's body, ``k`` must be an import alias, and a ``globals()``
    used in any other shape is refused.
    """
    bindings = source.import_bindings()
    frees: dict[str, set[str]] = {}
    homes: dict[str, set[str]] = {}
    for output in outputs.values():
        for piece in output.pieces:
            name = piece.shape.name if piece.shape else piece.binds
            frees.setdefault(name, set()).update(piece.free)
            homes.setdefault(name, set()).add(output.module)
    fixtures = {u.name for u in source.units if u.kind is Kind.FIXTURE}
    for output in outputs.values():
        for piece in output.pieces:
            if not any(_is_globals_call(s) for top in piece.nodes for s in ast.walk(top)):
                continue
            keys = globals_keys(piece.nodes)
            if keys is None or piece.shape is not None:
                raise Refusal(f"{piece.binds}: globals() in a shape the tool cannot prove a "
                              "split keeps (only <x>.setitem / <x>.delitem with a literal "
                              "key, outside a split class)")
            if any(_patches_outside_a_test_body(n, rules) for n in piece.nodes):
                raise Refusal(f"{piece.binds}: patches globals() outside a test function's "
                              "body, where its reach is every test that runs that code")
            reached = _reach(piece.free, frees, fixtures) - {piece.binds}
            for key in sorted(keys):
                if key not in bindings:
                    raise Refusal(f"{piece.binds}: globals() key {key!r} is not an import "
                                  "alias, so a patch would miss a copy of it")
                apart = sorted(f"{name} ({', '.join(sorted(homes[name]))})" for name in reached
                               if key in frees[name] and homes[name] != {output.module})
                if apart:
                    raise Refusal(f"{piece.binds} patches globals()[{key!r}], which "
                                  f"{apart} read within its reach: place them in "
                                  f"{output.module}")


def _resolve(outputs: dict[str, Output], source: SourceModule, home: dict[str, str]) -> None:
    """Fill each output's imports from its free names; refuse any name with no source."""
    bindings = source.import_bindings()
    positions = {p.binds: p.position for o in outputs.values() for p in o.pieces}
    for output in outputs.values():
        helpers = []
        for name in sorted(output.free()):
            if name in bindings:
                output.names.add(name)
            elif home.get(name) == HELPERS and output.module != HELPERS:
                helpers.append(name)
            elif name in home:
                raise Refusal(f"{output.module} reads {name}, placed in {home[name]}; "
                              "place it in _helpers.py")
            elif name in MODULE_DUNDERS:
                raise Refusal(f"{output.module} reads {name}, which names its own module "
                              "and file; a part sits one directory deeper")
            elif name not in BUILTINS:
                raise Refusal(f"{output.module} reads {name}, bound nowhere in the source")
        output.helpers = sorted(helpers, key=positions.__getitem__)


def _place_unused(outputs: dict[str, Output], source: SourceModule, plan: Plan) -> None:
    """Place each import alias no output reads where the plan says; refuse an unplaced one,
    or a placement of an alias that is read or does not exist."""
    used = set().union(*(o.names for o in outputs.values()))
    bindings = source.import_bindings()
    for alias, module in plan.unused_imports.items():
        if alias not in bindings or alias in used or module not in outputs:
            raise Refusal(f"unused_imports: {alias!r} -> {module!r} is not an unread "
                          "alias placed in an output that exists")
        outputs[module].names.add(alias)
    unplaced = sorted(set(bindings) - used - set(plan.unused_imports))
    if unplaced:
        raise Refusal(f"imports nothing reads, placed nowhere: {unplaced}; name each in "
                      "the plan's unused_imports (dropping one is its finding's act)")


def resolve(source: SourceModule, plan: Plan, rules: CollectRules) -> Layout:
    """The layout of ``source`` under ``plan``, or a :class:`Refusal` naming why not."""
    _check_module_level(source)
    pieces, home, shapes = _pieces(source, plan, rules)
    outputs: dict[str, Output] = {}
    for part in plan.parts:
        outputs[part.module] = Output(part.module, part.docstring, pieces.get(part.module, []))
    for module, docstring in ((CONFTEST, conftest_docstring(plan)),
                              (HELPERS, helpers_docstring(plan))):
        if pieces.get(module):
            outputs[module] = Output(module, docstring, pieces[module])
    for output in outputs.values():
        output.pieces.sort(key=lambda p: p.position)
        _check_nodes(output, source, set(shapes))
    _check_globals(outputs, source, rules)
    _resolve(outputs, source, home)
    _place_unused(outputs, source, plan)
    return Layout(source=source, plan=plan, outputs=outputs, home=home, split=shapes)


def conftest_docstring(plan: Plan) -> str:
    """The docstring the tool writes on a split package's ``conftest.py``: one paragraph,
    wrapped to the line limit when it is rendered."""
    return (f"The fixtures every part of this package shares: the module-level fixtures of "
            f"``{plan.source}``, moved verbatim by ``tools/split``.  A fixture here reaches "
            "exactly the tests that module held, at the scope it declared.")


def helpers_docstring(plan: Plan) -> str:
    """The docstring the tool writes on a split package's ``_helpers.py``: one paragraph,
    wrapped to the line limit when it is rendered."""
    return (f"The helpers and constants every part of this package shares: the module-level "
            f"helpers of ``{plan.source}``, moved verbatim by ``tools/split``.")
