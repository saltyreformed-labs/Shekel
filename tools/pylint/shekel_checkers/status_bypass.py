"""Transaction status-seam fence: ``shekel-transaction-status-bypass`` (W9907).

Flags writing ``status_id`` outside the transaction status seam. Every
non-transfer ``Transaction.status_id`` change must funnel through
``status_seam.apply_status_change`` (the seam that verifies the transition,
maintains ``paid_at``, and refreshes the eager ``status`` relationship) so the
settled-state boundary is uniform and a confirmed settle can never be emitted
twice or skipped (Build-Order Step 3's named highest risk). The status analog
of the balance-seam fence.
"""

from astroid import nodes

from pylint.checkers import BaseChecker

from ._common import _called_name_in, _is_string_const, _module_in_allowlist

# Status seam (W9907): the single attribute the transaction status seam owns.
# Every non-transfer ``Transaction.status_id`` assignment must go through
# ``status_seam.apply_status_change``; only the seam's own module and the ONE
# transfer-service leaf that CONSTRUCTS a transfer may write it directly.
# Matched syntactically by attribute
# name -- precise because ``Transaction`` / ``Transfer`` are the only models with
# a ``status_id`` column (``filing_status_id`` on the tax tables is a different
# attribute and is not matched). Listed by fully-qualified module name, matched
# exactly or as a package prefix by :func:`_module_in_allowlist`.
#
# The transfer entry NARROWED from ``app.services.transfer_service`` to that
# package's ``_create`` leaf at plan step X-f2-c3, and the narrowing is the
# point rather than a tidy-up: the module became a PACKAGE there (findings
# N-152 / N-156), and prefix matching would have silently extended this
# exemption over all eight of its leaves -- an allowlist widening nobody
# decided on, which is the failure mode a fence exists to prevent. ``_create``
# holds the one write that needs it (``Transfer(status_id=...)`` in
# ``create_transfer``; the shadow constructor went with the shadows at plan
# step X-bi-6-4d-2);
# every other leaf reaches the column through the seam. Plan step X-aj2 makes
# the write door structural and deletes the entry.
#
# The SEAM entry narrowed the same way and for the same reason at plan step
# X-au-c3, when ``status_seam`` became a package: ``_seam`` holds
# ``apply_status_change``, the one function here that assigns the column, while
# ``_record`` (the settlement value and its reads) and ``_refusals`` (the
# invariants as guards) write nothing at all. Naming the package would have
# re-run the widening this comment already records one entry down.
_STATUS_ID_ATTR = "status_id"
_STATUS_SEAM_MODULES = frozenset({
    "app.services.status_seam._seam",
    "app.services.transfer_service._create",
})
# The two models carrying a ``status_id`` column.  A constructor call to one of
# these with a ``status_id=`` kwarg is a status WRITE the born-Projected create
# rule governs; service specs (``transfer_service.TransferSpec``) and service
# calls (``update_transfer(status_id=...)``) are different names and are never
# matched.
_STATUS_BEARING_MODELS = frozenset({"Transaction", "Transfer"})
# The one enum member a status-bearing model may be born with, and the two
# expression shapes the codebase spells it in: the ref-cache lookup
# ``ref_cache.status_id(StatusEnum.PROJECTED)`` and a resolved
# ``projected_id`` name (bare local or ``plan.projected_id`` attribute).  Any
# other constructor status value fails closed and is flagged -- the safe
# direction for a fence.
_PROJECTED_MEMBER = "PROJECTED"
_PROJECTED_ID_NAME = "projected_id"
# The ref-cache status lookup shares the column's name: ``status_id(...)``
# bare or ``ref_cache.status_id(...)`` qualified.
_STATUS_LOOKUP_NAMES = frozenset({_STATUS_ID_ATTR})
# The dynamic-attribute builtin whose literal-string form writes a column the
# AssignAttr visitor never sees.
_SETATTR_BUILTIN = "setattr"
# Bulk-write method names: the SQLAlchemy ``Query.update({...})`` /
# ``Update.values(...)`` forms that write columns without ever producing an
# AssignAttr.  Matched syntactically by method name, so a plain-dict
# ``.update(status_id=...)`` in future code would also be flagged; the fix is
# the subscript form (``payload["status_id"] = ...``), which stays visible to
# review at the model boundary -- an acceptable nudge for a fence whose
# dangerous failure mode is the false negative.
_BULK_WRITE_METHODS = frozenset({"update", "values"})
_DICT_BUILTIN = "dict"


def _is_projected_status_expr(node: nodes.NodeNG) -> bool:
    """Return True if ``node`` is a recognizably born-Projected status value.

    The three shapes the codebase spells "the Projected status id" in are
    recognized: the ref-cache lookup ``ref_cache.status_id(StatusEnum.PROJECTED)``
    (bare ``status_id(...)`` included), a bare ``projected_id`` name, and a
    ``projected_id`` attribute (``plan.projected_id``).  Anything else -- a
    settled lookup, an arbitrary variable, a subscript -- returns ``False``, so
    the constructor check fails closed: an unrecognized status value must either
    be rewritten in a canonical Projected form or go through the status seam.
    A deliberately mislabeled alias (``projected_id = paid_id``) can evade a
    syntactic rule; the fence guards against mistakes, not sabotage.
    """
    if isinstance(node, nodes.Name):
        return node.name == _PROJECTED_ID_NAME
    if isinstance(node, nodes.Attribute):
        return node.attrname == _PROJECTED_ID_NAME
    if not isinstance(node, nodes.Call) or not node.args:
        return False
    if _called_name_in(node, _STATUS_LOOKUP_NAMES) is None:
        return False
    member = node.args[0]
    if isinstance(member, nodes.Attribute):
        return member.attrname == _PROJECTED_MEMBER
    return isinstance(member, nodes.Name) and member.name == _PROJECTED_MEMBER


def _is_status_id_key(node: nodes.NodeNG) -> bool:
    """Return True if ``node`` is a ``status_id`` key in a bulk-write dict.

    Matches the three key spellings a SQLAlchemy update dict admits: the string
    literal ``"status_id"``, the column attribute ``Transaction.status_id``,
    and a bare ``status_id`` name holding the column.  ``filing_status_id`` (the
    tax tables) is a different string/attribute and is never matched.
    """
    if _is_string_const(node):
        return node.value == _STATUS_ID_ATTR
    if isinstance(node, nodes.Attribute):
        return node.attrname == _STATUS_ID_ATTR
    return isinstance(node, nodes.Name) and node.name == _STATUS_ID_ATTR


def _is_born_settled_ctor(node: nodes.Call) -> bool:
    """Return True for a model constructor whose ``status_id=`` is not Projected.

    Matches ``Transaction(...)`` / ``Transfer(...)`` (bare or attribute form)
    carrying a ``status_id=`` kwarg whose value fails
    :func:`_is_projected_status_expr`.  A splatted ``Transaction(**data)`` has
    no named kwarg and is not matched -- that residual is governed by the
    create-route convention (schemas omit ``status_id``; the route assigns
    Projected into the dict unconditionally) and stays with review.
    """
    if _called_name_in(node, _STATUS_BEARING_MODELS) is None:
        return False
    return any(
        keyword.arg == _STATUS_ID_ATTR
        and not _is_projected_status_expr(keyword.value)
        for keyword in node.keywords or []
    )


def _is_setattr_status_write(node: nodes.Call) -> bool:
    """Return True for ``setattr(<x>, "status_id", ...)`` with the literal name.

    Only the literal-string form is statically visible; a dynamic field loop
    (``setattr(txn, field, value)``) is not matched and remains governed by the
    loop's own ``status_id`` exclusion (the mutations-route pattern).
    """
    if not (
        isinstance(node.func, nodes.Name)
        and node.func.name == _SETATTR_BUILTIN
        and len(node.args) >= 2
    ):
        return False
    target_attr = node.args[1]
    return _is_string_const(target_attr) and target_attr.value == _STATUS_ID_ATTR


def _is_bulk_status_write(node: nodes.Call) -> bool:
    """Return True for a ``.update(...)`` / ``.values(...)`` writing ``status_id``.

    Matches the three statically visible payload shapes: a ``status_id=``
    keyword (``.values(status_id=...)``), a dict literal carrying a
    ``status_id`` key (string, column attribute, or bare name), and a
    ``dict(status_id=...)`` builder call.  A payload dict built elsewhere and
    passed by name is not statically visible and is not matched.
    """
    if not (
        isinstance(node.func, nodes.Attribute)
        and node.func.attrname in _BULK_WRITE_METHODS
    ):
        return False
    if any(keyword.arg == _STATUS_ID_ATTR for keyword in node.keywords or []):
        return True
    for arg in node.args:
        if isinstance(arg, nodes.Dict) and any(
            _is_status_id_key(key) for key, _value in arg.items
        ):
            return True
        if (
            isinstance(arg, nodes.Call)
            and isinstance(arg.func, nodes.Name)
            and arg.func.name == _DICT_BUILTIN
            and any(
                keyword.arg == _STATUS_ID_ATTR for keyword in arg.keywords or []
            )
        ):
            return True
    return False


class ShekelTransactionStatusBypassChecker(BaseChecker):
    """Forbid writing ``status_id`` outside the transaction status seam.

    Every non-transfer ``Transaction.status_id`` change must run through
    ``status_seam.apply_status_change`` -- the seam that verifies the
    state-machine transition, maintains ``paid_at``, and refreshes the eagerly
    joined ``status`` relationship in ONE place, so a confirmed settle can never
    be emitted twice or skipped by a forgotten site (the lifecycle-completeness
    risk Build-Order Step 3 names as its highest).  Only the seam's own module
    (``app.services.status_seam``) and ``transfer_service`` may write it -- the
    latter for its ONE constructor write only (``Transfer(status_id=...)`` in
    ``_create``; the shadow constructor went with the shadows at plan step
    ``balance:X-bi-6-4d-2``), since plan step X-aj1 deleted the
    module's own copy of the seam and it now assigns no ``status_id`` attribute
    at all (see the note on :data:`_STATUS_SEAM_MODULES`).  The
    status analog of the balance-seam fence.

    Four write forms are matched (the 2026-07-02 adversarial review's H3/R3
    closed the last three, which were conventions with no machine rule):

    * direct assignment ``<x>.status_id = ...`` (:meth:`visit_assignattr`);
    * the literal dynamic form ``setattr(<x>, "status_id", ...)``;
    * a ``status_id`` key or keyword in a bulk ``.update(...)`` /
      ``.values(...)`` call -- bulk writes bypass the seam AND the ORM event
      surface, so they are flagged regardless of the value;
    * a ``Transaction(...)`` / ``Transfer(...)`` constructor ``status_id=``
      kwarg whose value is not recognizably born-Projected
      (:func:`_is_projected_status_expr`) -- a born-settled row would carry
      NULL ``paid_at``, skip ``verify_transition``, and emit no ledger
      posting, the exact failure mode Step 3's review called its highest
      risk.

    Statically invisible residuals, deliberately out of scope and governed by
    convention + review: a splatted ``Transaction(**data)`` (the create routes
    assign Projected into ``data`` unconditionally), a dynamic
    ``setattr(txn, field, value)`` loop (the mutations route excludes
    ``status_id`` and routes it through the seam), and a bulk-write payload
    dict built away from the call.
    """

    name = "shekel-transaction-status-bypass"
    msgs = {
        "W9907": (
            "status_id written outside the transaction status seam; create "
            "rows born-Projected and route status changes through "
            "status_seam.apply_status_change instead",
            "shekel-transaction-status-bypass",
            "Every Transaction.status_id / Transfer.status_id change must go through "
            "app.services.status_seam.apply_status_change -- the single seam "
            "that runs the state-machine transition check, maintains paid_at, "
            "and refreshes the eagerly-joined status relationship. Centralizing "
            "it makes the settled-state boundary uniform and impossible to skip, "
            "which is what lets the posting ledger emit a confirmed settle "
            "exactly once (Build-Order Step 3). Only app.services.status_seam "
            "(the seam) and transfer_service (whose ONE constructor write "
            "builds a transfer; plan step X-aj1 deleted that "
            "module's own copy of the seam, so it assigns no status_id "
            "attribute any more) may write status_id directly; "
            "Transaction and Transfer are the only models carrying "
            "that column, so the syntactic match is precise. Four write forms "
            "are matched: direct assignment, the literal setattr form, a "
            "status_id key/keyword in a bulk .update()/.values() call, and a "
            "Transaction/Transfer constructor status_id= kwarg whose value is "
            "not recognizably born-Projected (the ref-cache PROJECTED lookup "
            "or a projected_id name/attribute) -- construction with any other "
            "status is the born-settled failure mode the Step-3 review named "
            "its highest risk.",
        ),
    }

    def visit_assignattr(self, node: nodes.AssignAttr) -> None:
        """Flag ``<x>.status_id = ...`` made outside the seam-owner allowlist.

        ``node`` is every attribute-assignment TARGET (astroid's store-context
        ``AssignAttr``); a ``status_id ==`` comparison is a ``Compare`` over a
        load-context ``Attribute`` and is dispatched elsewhere, so it is never
        seen here.  Only a ``status_id`` target whose enclosing module is outside
        :data:`_STATUS_SEAM_MODULES` is reported.
        """
        if node.attrname != _STATUS_ID_ATTR:
            return
        if _module_in_allowlist(node, _STATUS_SEAM_MODULES):
            return
        self.add_message("shekel-transaction-status-bypass", node=node)

    def visit_call(self, node: nodes.Call) -> None:
        """Flag the three call-shaped ``status_id`` writes outside the seam.

        ``node`` is every call expression; the cheap shape checks (frozenset
        name lookups) run first and the module-identity walk runs only for an
        actual status write, mirroring the balance fence's ordering.  The three
        shapes -- a born-settled ``Transaction`` / ``Transfer`` constructor
        kwarg, a literal ``setattr(<x>, "status_id", ...)``, and a
        ``status_id`` payload in a bulk ``.update(...)`` / ``.values(...)``
        -- are disjoint (a call is at most one of them), so the first match
        reports and returns.
        """
        if not (
            _is_born_settled_ctor(node)
            or _is_setattr_status_write(node)
            or _is_bulk_status_write(node)
        ):
            return
        if _module_in_allowlist(node, _STATUS_SEAM_MODULES):
            return
        self.add_message("shekel-transaction-status-bypass", node=node)
