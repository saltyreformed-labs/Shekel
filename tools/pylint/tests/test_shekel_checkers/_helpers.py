"""The helpers and constants every part of this package shares: the module-level helpers of
``tools/pylint/tests/test_shekel_checkers.py``, moved verbatim by ``tools/split``.
"""

from pathlib import Path

from astroid import nodes

from shekel_checkers import _is_public_export_surface

from _repo_root import REPO_ROOT


def _fenced_module_sources(module_name: str) -> list[Path]:
    """Return every source file a fenced *module_name* scope covers.

    A flat module (``app.services.account_projection``) is one file.  A PACKAGE
    (``app.services.loan_ledger``) is every ``.py`` file inside it --
    because the checker scopes a package by prefix, so a producer born in ANY
    submodule (``_walk``, ``_visible``, ``_events``) is in scope and must be
    classified.  Enumerating them here is what makes the registry-vs-reality
    test see the same surface the checker does.

    Reads the source with astroid rather than importing ``app``, keeping the
    checker's unit tests free of the Flask/SQLAlchemy import graph.
    """
    relative = Path(*module_name.split("."))
    flat = REPO_ROOT / relative.with_suffix(".py")
    if flat.is_file():
        return [flat]
    package = REPO_ROOT / relative
    assert package.is_dir(), (
        f"fenced module {module_name} is neither a module nor a package -- the "
        f"scope names something that does not exist"
    )
    return sorted(package.glob("*.py"))


def _public_export_names(tree: nodes.Module) -> list[str]:
    """Return every public name a consumer outside *tree*'s module can call.

    The registry-vs-reality mirror of the checker's own
    ``_is_public_export_surface``, and it MUST agree with it on what "the export
    surface" means -- otherwise this guard stops pinning the thing it claims to
    pin. That is not hypothetical: while both looked only at ``tree.body``, a
    public ``BalanceContext.loan`` handed routes a ``ResolvedLoan`` unseen by
    either. So rather than re-implement the rule (a second copy that can drift),
    this walks every FunctionDef in the tree and asks the CHECKER'S OWN predicate.

    Args:
        tree: The parsed module.

    Returns:
        The public export names, in source order.
    """
    return [
        node.name for node in tree.nodes_of_class(nodes.FunctionDef)
        if _is_public_export_surface(node)
    ]
