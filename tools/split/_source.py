"""A source test module read as the split tool moves it: its docstring, its import block, and
its top-level nodes, each as a BLOCK of source lines that carries its leading comments.

**A block is the unit of a move.**  Its lines run from the first non-blank line after the
previous node (or after the import block) through the node's last line, so a section banner
or a comment above a test travels with the node below it and no comment is left without a
home.  The writer copies a block's lines verbatim and the prover compares them verbatim; an
``ast.dump`` comparison alone cannot see a dropped comment.

**What this module refuses to read**, because a move could change its meaning or lose it:
a line before the module docstring; a comment anywhere in the import block (it has no node
to travel with); an import after the first non-import statement; a relative or star import;
a module-level statement other than an import, a ``def``, a ``class`` or a single-name
assignment; a name bound twice at module level; anything after the last node.
"""
from __future__ import annotations

import ast
import configparser
import fnmatch
import io
import symtable
import tokenize
from dataclasses import dataclass
from enum import Enum
from pathlib import Path


class Refusal(RuntimeError):
    """The tool cannot PROVE a move of this source under this plan, so it makes none."""


class Kind(Enum):
    """What a top-level node is to pytest, which decides where it may go."""

    TEST = "test"
    FIXTURE = "fixture"
    HELPER = "helper"


@dataclass(frozen=True)
class CollectRules:
    """``pytest.ini``'s ``python_files`` / ``python_classes`` / ``python_functions`` globs,
    read from the repository rather than restated here."""

    files: tuple[str, ...]
    classes: tuple[str, ...]
    functions: tuple[str, ...]

    def is_test_file(self, name: str) -> bool:
        """Whether pytest collects a module file called ``name``."""
        return any(fnmatch.fnmatchcase(name, pattern) for pattern in self.files)

    def is_test_class(self, name: str) -> bool:
        """Whether pytest collects a class called ``name``."""
        return any(fnmatch.fnmatchcase(name, pattern) for pattern in self.classes)

    def is_test_function(self, name: str) -> bool:
        """Whether pytest collects a function or method called ``name``."""
        return any(fnmatch.fnmatchcase(name, pattern) for pattern in self.functions)

    def is_test_name(self, name: str) -> bool:
        """Whether a name pytest would collect if a module bound it."""
        return self.is_test_class(name) or self.is_test_function(name)


def collect_rules(root: Path) -> CollectRules:
    """Read the collection globs from ``<root>/pytest.ini``; refuse when it holds none."""
    parser = configparser.ConfigParser(interpolation=None)
    if not parser.read(root / "pytest.ini", encoding="utf-8") or "pytest" not in parser:
        raise Refusal(f"no [pytest] section in {root / 'pytest.ini'}")
    section = parser["pytest"]
    try:
        return CollectRules(
            files=tuple(section["python_files"].split()),
            classes=tuple(section["python_classes"].split()),
            functions=tuple(section["python_functions"].split()),
        )
    except KeyError as exc:
        raise Refusal(f"pytest.ini names no {exc.args[0]}; the tool will not assume one") from exc


@dataclass(frozen=True)
class Block:
    """One node and the lines it moves with: ``first`` is its first leading comment (or the
    node's own first line), ``start`` the node's first line counting decorators."""

    name: str
    node: ast.stmt
    first: int
    start: int
    end: int

    def text(self, lines: tuple[str, ...]) -> tuple[str, ...]:
        """The block's lines, verbatim."""
        return lines[self.first - 1:self.end]


@dataclass(frozen=True)
class Unit:
    """A top-level block and what it is to pytest."""

    block: Block
    kind: Kind

    @property
    def name(self) -> str:
        """The name the node binds at module level."""
        return self.block.name


@dataclass(frozen=True)
class ImportStmt:
    """One top-level import statement, the names it binds, and its blank-line group."""

    node: ast.Import | ast.ImportFrom
    group: int

    @property
    def is_future(self) -> bool:
        """Whether this is a ``from __future__`` import, which every output repeats."""
        return isinstance(self.node, ast.ImportFrom) and self.node.module == "__future__"

    def bindings(self) -> dict[str, ast.alias]:
        """Each module-level name this statement binds, to the alias that binds it."""
        return {bound_name(alias, self.node): alias for alias in self.node.names}


@dataclass(frozen=True)
class SourceModule:
    """A parsed source module: its lines, docstring end, imports and top-level units."""

    path: Path
    lines: tuple[str, ...]
    docstring_end: int
    imports: tuple[ImportStmt, ...]
    units: tuple[Unit, ...]

    def unit(self, name: str) -> Unit | None:
        """The top-level unit binding ``name``, if any."""
        return next((u for u in self.units if u.name == name), None)

    def import_bindings(self) -> dict[str, ImportStmt]:
        """Every name the import block binds, to the statement binding it."""
        return {name: stmt for stmt in self.imports if not stmt.is_future
                for name in stmt.bindings()}


def bound_name(alias: ast.alias, node: ast.Import | ast.ImportFrom) -> str:
    """The module-level name an import alias binds (``import a.b`` binds ``a``)."""
    if alias.asname:
        return alias.asname
    return alias.name.split(".")[0] if isinstance(node, ast.Import) else alias.name


def is_docstring(node: ast.stmt) -> bool:
    """Whether ``node`` is a string-literal expression statement."""
    return (isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant)
            and isinstance(node.value.value, str))


def fixture_decorator(node: ast.stmt) -> ast.expr | None:
    """The ``@pytest.fixture`` / ``@fixture`` decorator on a function, or None."""
    if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
        return None
    for decorator in node.decorator_list:
        target = decorator.func if isinstance(decorator, ast.Call) else decorator
        if (isinstance(target, ast.Attribute) and target.attr == "fixture"
                and isinstance(target.value, ast.Name) and target.value.id == "pytest"):
            return decorator
        if isinstance(target, ast.Name) and target.id == "fixture":
            return decorator
    return None


def fixture_scope(decorator: ast.expr) -> str | None:
    """A fixture decorator's scope, ``"function"`` when unstated; None when it cannot be
    read statically (a positional argument or a computed scope)."""
    if not isinstance(decorator, ast.Call):
        return "function"
    if decorator.args:
        return None
    for keyword in decorator.keywords:
        if keyword.arg == "scope":
            value = keyword.value
            return value.value if isinstance(value, ast.Constant) and isinstance(
                value.value, str) else None
    return "function"


def _comment_lines(text: str) -> frozenset[int]:
    """The line of every comment token in ``text``."""
    tokens = tokenize.generate_tokens(io.StringIO(text).readline)
    return frozenset(tok.start[0] for tok in tokens if tok.type == tokenize.COMMENT)


def _binding_name(node: ast.stmt, where: str) -> str:
    """The one name a top-level node binds, or a refusal for any other statement."""
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
        return node.name
    if isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(
            node.targets[0], ast.Name):
        return node.targets[0].id
    if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
        return node.target.id
    if isinstance(node, (ast.Import, ast.ImportFrom)):
        raise Refusal(f"{where}: an import after the first non-import statement")
    raise Refusal(f"{where}: a module-level {type(node).__name__} has no single name to "
                  "move by, and running it elsewhere could change its effect")


def blocks(nodes: list[ast.stmt], after: int, lines: tuple[str, ...], where: str
           ) -> list[Block]:
    """Cut ``nodes`` (siblings, in order) into blocks, the first starting after line
    ``after``; each block takes the non-blank lines above it back to its predecessor."""
    found = []
    previous_end = after
    for node in nodes:
        start = min([node.lineno] + [d.lineno for d in getattr(node, "decorator_list", [])])
        first = next((n for n in range(previous_end + 1, start) if lines[n - 1].strip()),
                     start)
        name = _binding_name(node, f"{where}:{node.lineno}")
        found.append(Block(name=name, node=node, first=first, start=start,
                           end=node.end_lineno))
        previous_end = node.end_lineno
    return found


def _kind(node: ast.stmt, rules: CollectRules) -> Kind:
    """Whether pytest collects ``node``, runs it as a fixture, or neither."""
    if isinstance(node, ast.ClassDef) and rules.is_test_class(node.name):
        return Kind.TEST
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
        if rules.is_test_function(node.name):
            return Kind.TEST
        if fixture_decorator(node) is not None:
            return Kind.FIXTURE
    return Kind.HELPER


def _imports(body: list[ast.stmt], lines: tuple[str, ...], where: str,
             relative: str | None) -> list[ImportStmt]:
    """Pop the leading import statements off ``body``, each with its blank-line group; the
    one relative import allowed is ``from .<relative> import ...``."""
    found: list[ImportStmt] = []
    group = 0
    while body and isinstance(body[0], (ast.Import, ast.ImportFrom)):
        node = body.pop(0)
        if isinstance(node, ast.ImportFrom) and not (
                relative is not None and node.level == 1 and node.module == relative) and (
                node.level or [a.name for a in node.names] == ["*"]):
            raise Refusal(f"{where}:{node.lineno}: a relative or star import cannot be "
                          "re-derived for a part one directory deeper")
        if found and not lines[found[-1].node.end_lineno].strip():
            group += 1
        found.append(ImportStmt(node=node, group=group))
    return found


def _check_header(lines: tuple[str, ...], text: str, docstring_end: int, header_end: int,
                  where: str) -> None:
    """Refuse a comment in the import block: it has no node to travel with."""
    stray = sorted(n for n in _comment_lines(text) if docstring_end < n <= header_end)
    if stray:
        raise Refusal(f"{where}: comment line(s) {stray} in the import block would be lost; "
                      f"first: {lines[stray[0] - 1].strip()!r}")


def _check_bindings(imports: list[ImportStmt], found: list[Block], where: str) -> None:
    """Refuse a name bound twice at module level: which binding a part sees would move."""
    names = [n for stmt in imports for n in stmt.bindings()] + [b.name for b in found]
    twice = sorted({n for n in names if names.count(n) > 1})
    if twice:
        raise Refusal(f"{where}: names bound twice at module level: {twice}")


def read_module(path: Path, rules: CollectRules, relative: str | None = None) -> SourceModule:
    """Read ``path`` into a :class:`SourceModule`, refusing what a move could not keep.

    ``relative`` names the one sibling module a relative import may read: none for a
    source, ``_helpers`` for an output the tool wrote.
    """
    text = path.read_text(encoding="utf-8")
    lines = tuple(text.splitlines())
    where = str(path)
    body = list(ast.parse(text, filename=where).body)
    docstring_end = 0
    if body and is_docstring(body[0]):
        if body[0].lineno != 1:
            raise Refusal(f"{where}: lines before the module docstring have no home")
        docstring_end = body.pop(0).end_lineno
    imports = _imports(body, lines, where, relative)
    header_end = imports[-1].node.end_lineno if imports else docstring_end
    _check_header(lines, text, docstring_end, header_end, where)
    found = blocks(body, header_end, lines, where)
    last = found[-1].end if found else header_end
    if any(line.strip() for line in lines[last:]):
        raise Refusal(f"{where}: lines after the last node have no node to travel with")
    _check_bindings(imports, found, where)
    units = tuple(Unit(block=b, kind=_kind(b.node, rules)) for b in found)
    return SourceModule(path=path, lines=lines, docstring_end=docstring_end,
                        imports=tuple(imports), units=units)


#: The names Python's compiler adds to a scope, which symtable reports as a module-level
#: read: 3.14 adds ``__conditional_annotations__`` to a module holding a module-level
#: variable annotation, and to a class not nested in a class holding one inside a compound
#: statement.  Where the source never writes the name it is no read of the source's, so no
#: output imports it (:func:`_scopes`); one the source does write is kept, and the layout
#: refuses it as bound nowhere, as it does a name the compiler adds that is not listed.
#: A class member is read inside a stand-in class, where 3.14.8 reports the name free,
#: never global, so :func:`member_free_names` does not meet it there.
_COMPILER_NAMES = frozenset({"__conditional_annotations__"})


def _table_globals(table: symtable.SymbolTable) -> set[str]:
    """Every global name ``table`` or a scope nested in it reads."""
    found = {s.get_name() for s in table.get_symbols() if s.is_global() and s.is_referenced()}
    for child in table.get_children():
        found |= _table_globals(child)
    return found


#: The statements whose bodies are a scope of their own, so an annotation inside one is not
#: its enclosing function's.
_SCOPES = (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)


def _local_annotations(function: ast.FunctionDef | ast.AsyncFunctionDef) -> list[ast.expr]:
    """The annotation of each annotated assignment in ``function``'s own body, not in a
    function or class nested in it."""
    found = []
    pending = list(function.body)
    while pending:
        node = pending.pop()
        if isinstance(node, ast.AnnAssign):
            found.append(node.annotation)
        elif not isinstance(node, _SCOPES):
            pending.extend(ast.iter_child_nodes(node))
    return found


def _scopes(text: str, name: str) -> tuple[symtable.SymbolTable, frozenset[str]]:
    """``text``'s symbol table, each local variable's annotation read as a statement of
    its function; and the :data:`_COMPILER_NAMES` ``text`` never writes as a name, which a
    reading of the table drops.

    Python never evaluates a local variable's annotation (PEP 526), so :mod:`symtable`
    records no name one reads; a reader and pylint each read them, and a part that
    dropped such a name's import fails pylint's ``undefined-variable``.  Each annotation
    is appended to its function's body as an expression statement, so symtable resolves
    its names as Python would were the annotation evaluated there.  It goes last, after
    every ``global`` or ``nonlocal`` declaration (a read before one is a SyntaxError);
    symtable's classification is otherwise flow-insensitive, so the position changes no
    name's scope.  A string annotation (``made: "Pattern"``) is a constant to symtable and
    is not parsed here, so the names in one are not counted.

    A block with no local annotation is read as written, and one with any is restated by
    :func:`ast.unparse`, which recurses in Python: a block nested deeper than it can
    recurse is refused, not misread.
    """
    tree = ast.parse(text, filename=name)
    unwritten = _COMPILER_NAMES - {node.id for node in ast.walk(tree)
                                   if isinstance(node, ast.Name)}
    found = [(node, _local_annotations(node)) for node in ast.walk(tree)
             if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))]
    if not any(annotations for _, annotations in found):
        return symtable.symtable(text, name, "exec"), unwritten
    for function, annotations in found:
        function.body.extend(ast.Expr(value=annotation) for annotation in annotations)
    try:
        restated = ast.unparse(tree)
    except RecursionError as exc:
        holders = sorted({function.name for function, annotations in found if annotations})
        raise Refusal(f"the local annotations in {holders} cannot be read: the block nests "
                      "deeper than ast.unparse recurses") from exc
    return symtable.symtable(restated, name, "exec"), unwritten


def free_names(text: str) -> frozenset[str]:
    """The module-level names a block of top-level source reads and does not bind itself.

    Resolved by :mod:`symtable`, so a local, a parameter or a comprehension variable that
    shares a module-level name is NOT counted: the scopes are Python's, not a guess.  A
    local variable's annotation counts as a read (:func:`_scopes`), though Python never
    evaluates one: an output's imports need it, and the checks of what a node reaches at
    run time (a ``globals()`` key's readers, a class member reaching its own class) count
    it too, so they refuse where only an annotation names the key or the class.
    """
    top, unwritten = _scopes(text, "<block>")
    found = {s.get_name() for s in top.get_symbols()
             if s.is_referenced() and not s.is_assigned() and not s.is_imported()}
    for child in top.get_children():
        found |= _table_globals(child)
    return frozenset(found - unwritten)


def member_free_names(member_lines: tuple[str, ...]) -> frozenset[str]:
    """:func:`free_names` for a class member's lines, read inside a stand-in class so the
    class scope is Python's own (a method does not see a class attribute by bare name)."""
    probe = "class _SplitProbe:\n" + "\n".join(member_lines) + "\n"
    top, unwritten = _scopes(probe, "<member>")
    found: set[str] = set()
    for child in top.get_children():
        found |= _table_globals(child)
    return frozenset(found - unwritten)
