"""Census every ``Transaction(...)`` construction under ``tests/`` by what it links.

Plan step **balance:X-bi-7c**'s instrument.  The step moved every LINK-LESS
hand-built row onto the suite's one-off builder (``tests._test_helpers.
one_off_row_of``, on ``one_off.place_one_off``) so the cutover's CHECK
(``ck_transactions_one_pricing_link`` at ``= 1``, plan step ``X-bi-7d-2``)
binds on rows the app's own door wrote -- and *which* constructions name no
link is,
as the step's own row says, **an AST walk's answer and never a total stated in
a document**.  The step names a grep census (code lines matching
``\\bTransaction\\(``); this walk is the partition
of those calls the grep cannot make.

Four classes, over every ``ast.Call`` whose callee is the model
``Transaction`` (imported from ``app.models.transaction``, or attribute-called):

* ``LINKED`` -- names ``template_id``, ``transfer_id`` or
  ``credit_payback_for_id`` with a value other than the literal ``None``.
  The engine's rows, transfer shadows and CC paybacks; not this step's.
* ``LINKLESS`` -- names none of the three, or names them as ``None``.  7c's
  population, each moved onto ``one_off_row_of``; the cases whose SUBJECT
  was the legacy shape itself (the accessors' own-cell arm, the frozen flag
  cells) sat on its one transitional home ``legacy_link_less_row_of`` until
  the cutover (7d-2) deleted the builder and retired them.  What is left in
  this class after 7d-2 is a construction that never reaches the database
  as a bare row (an unsaved type token, a constructor-kwarg case, a NOT NULL
  probe), since the schema refuses one.
* ``SPLAT`` -- passes ``**kwargs`` (or ``*args``), so the keywords cannot be
  read here; each is opened by hand.
* ``OTHER`` -- a bare ``Transaction(`` where the name is not the model.

Run from the repository root::

    python tests/manual/census_hand_built_rows.py            # the counts
    python tests/manual/census_hand_built_rows.py --sites    # every site, tab-separated

Measured 2026-09-16 at the leaf's base (dev ``b0c6df9d``): 289 calls --
228 LINKLESS, 51 SPLAT, 10 LINKED, 0 OTHER.  The figure is a QUOTE the moment
it is read; re-run the walk.
"""

from __future__ import annotations

import ast
import collections
import pathlib
import sys

LINKS = frozenset({"template_id", "transfer_id", "credit_payback_for_id"})


def _model_names(tree: ast.Module) -> set[str]:
    """Return the names ``app.models.transaction.Transaction`` is bound to."""
    names: set[str] = set()
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.ImportFrom)
            and node.module
            and node.module.endswith("transaction")
        ):
            for alias in node.names:
                if alias.name == "Transaction":
                    names.add(alias.asname or "Transaction")
    return names


def _classify(call: ast.Call, model_names: set[str]) -> str:
    """Return the class of one ``Transaction(...)`` call."""
    func = call.func
    if isinstance(func, ast.Name) and func.id not in model_names:
        return "OTHER"
    keywords = {k.arg: k.value for k in call.keywords if k.arg is not None}
    splat = any(k.arg is None for k in call.keywords) or any(
        isinstance(a, ast.Starred) for a in call.args
    )
    linked = any(
        name in keywords
        and not (
            isinstance(keywords[name], ast.Constant)
            and keywords[name].value is None
        )
        for name in LINKS
    )
    if linked:
        return "LINKED"
    if splat:
        return "SPLAT"
    return "LINKLESS"


def census(root: pathlib.Path) -> list[tuple[str, str, int]]:
    """Return ``(class, path, line)`` for every ``Transaction(`` call under *root*."""
    rows: list[tuple[str, str, int]] = []
    for path in sorted(root.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        model_names = _model_names(tree)
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            callee = (
                func.id if isinstance(func, ast.Name)
                else func.attr if isinstance(func, ast.Attribute)
                else None
            )
            if callee != "Transaction":
                continue
            rows.append((_classify(node, model_names), str(path), node.lineno))
    return rows


def main(argv: list[str]) -> int:
    """Print the counts, or every site with ``--sites``."""
    rows = census(pathlib.Path("tests"))
    if "--sites" in argv:
        for cls, path, line in rows:
            print(f"{cls}\t{path}:{line}")
        return 0
    counts = collections.Counter(cls for cls, _path, _line in rows)
    print(f"{len(rows)} Transaction( calls under tests/")
    for cls in ("LINKLESS", "SPLAT", "LINKED", "OTHER"):
        print(f"  {counts.get(cls, 0):4d} {cls}")
    per_file = collections.Counter(
        path for cls, path, _line in rows if cls == "LINKLESS"
    )
    print("LINKLESS by file:")
    for path, count in sorted(per_file.items(), key=lambda item: (-item[1], item[0])):
        print(f"  {count:4d} {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
