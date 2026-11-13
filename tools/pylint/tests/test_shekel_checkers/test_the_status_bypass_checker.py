"""The transaction status bypass checker."""

import astroid
from astroid import nodes
from pylint.testutils import CheckerTestCase, MessageTest

from shekel_checkers import _STATUS_SEAM_MODULES, ShekelTransactionStatusBypassChecker


class TestShekelTransactionStatusBypassChecker(CheckerTestCase):
    """``shekel-transaction-status-bypass``: status_id written only via the seam.

    Every non-transfer ``Transaction.status_id`` change must route through
    ``status_seam.apply_status_change``; only that module
    (``app.services.status_seam``) and ``transfer_service._create`` (which
    CONSTRUCTS a transfer and its two shadow rows) may write it.  Four write forms are
    fenced (H3/R3 of the 2026-07-02 review closed the last three): direct
    assignment, the literal ``setattr`` form, a ``status_id`` payload in a bulk
    ``.update()`` / ``.values()`` call, and a born-settled ``Transaction`` /
    ``Transfer`` constructor kwarg.  Like every module-scoped fence here the
    rule keys off the ENCLOSING module (``node.root().name``), so each snippet is
    parsed inside a named module via :func:`astroid.parse` (``module_name=``).
    Every flagged form is paired with the conforming form that must NOT fire, and
    register-bound loops assert every allowlisted module is exempt from every
    form.
    """

    CHECKER_CLASS = ShekelTransactionStatusBypassChecker

    @staticmethod
    def _status_assign(assign_source: str, module_name: str) -> nodes.AssignAttr:
        """Return the AssignAttr target of *assign_source* parsed in *module_name*.

        The enclosing module's name drives the allowlist check, so it is set
        explicitly.  The snippet is a single attribute assignment, so the module
        body's one statement is an ``Assign`` whose first target is the
        store-context ``AssignAttr`` the checker visits.
        """
        module = astroid.parse(f"{assign_source}\n", module_name=module_name)
        return module.body[0].targets[0]

    def test_flags_status_id_assignment_from_route(self) -> None:
        """A bare status_id assignment in a route module is flagged."""
        node = self._status_assign(
            "txn.status_id = new_status_id",
            "app.routes.transactions.mutations",
        )
        with self.assertAddsMessages(
            MessageTest("shekel-transaction-status-bypass", node=node),
            ignore_position=True,
        ):
            self.checker.visit_assignattr(node)

    def test_flags_status_id_assignment_from_credit_workflow(self) -> None:
        """A status_id assignment in credit_workflow is flagged (must use the seam)."""
        node = self._status_assign(
            "txn.status_id = credit_id", "app.services.credit_workflow",
        )
        with self.assertAddsMessages(
            MessageTest("shekel-transaction-status-bypass", node=node),
            ignore_position=True,
        ):
            self.checker.visit_assignattr(node)

    def test_allows_status_id_assignment_in_status_seam(self) -> None:
        """The seam's own module (status_seam) may assign status_id; not flagged."""
        node = self._status_assign(
            "txn.status_id = new_status_id", "app.services.status_seam._seam",
        )
        with self.assertNoMessages():
            self.checker.visit_assignattr(node)

    def test_allows_status_id_assignment_in_transfer_create_leaf(self) -> None:
        """The transfer CREATE leaf may assign status_id; not flagged.

        A name-based checker cannot distinguish a shadow ``Transaction``'s
        ``status_id`` from a real transaction's, so the leaf that constructs the
        pair is allowlisted alongside the seam (2.8b MEDIUM).
        """
        node = self._status_assign(
            "expense_shadow.status_id = new_status_id",
            "app.services.transfer_service._create",
        )
        with self.assertNoMessages():
            self.checker.visit_assignattr(node)

    def test_flags_status_id_assignment_in_a_sibling_transfer_leaf(self) -> None:
        """A transfer-service leaf that is NOT ``_create`` is still fenced.

        The regression guard for plan step X-f2-c3's package move.  The
        allowlist matches a package PREFIX, so an entry naming
        ``app.services.transfer_service`` would have exempted all eight leaves
        of the new package the moment it stopped being one module -- a fence
        widened by a refactor rather than by a decision.  Narrowing the entry to
        the ``_create`` leaf is what this asserts, from the failing direction:
        the status applier reaches the column through the seam and must have no
        exemption of its own.
        """
        node = self._status_assign(
            "expense_shadow.status_id = new_status_id",
            "app.services.transfer_service._status",
        )
        with self.assertAddsMessages(
            MessageTest("shekel-transaction-status-bypass", node=node),
            ignore_position=True,
        ):
            self.checker.visit_assignattr(node)

    def test_allows_every_seam_module(self) -> None:
        """EVERY allowlisted module may assign status_id directly.

        Binds the test to :data:`_STATUS_SEAM_MODULES` itself, so narrowing the
        set would surface here rather than as a surprise W9907 on a seam module.
        """
        for module_name in sorted(_STATUS_SEAM_MODULES):
            node = self._status_assign(
                "txn.status_id = new_status_id", module_name,
            )
            with self.assertNoMessages():
                self.checker.visit_assignattr(node)

    def test_flags_a_SIBLING_leaf_of_the_seam_package(self) -> None:
        """A seam leaf that is not the allowlisted one is NOT exempt.

        The narrowing of plan step X-au-c3, stated as a test.  ``status_seam``
        became a package there, and the entry named the package until the same
        widening this file already records for ``transfer_service`` was noticed
        a second time: :func:`_module_in_allowlist` matches a package PREFIX, so
        naming the package exempts every leaf inside it from the status-write
        fence -- an allowlist widening nobody decided on.

        The entry names ``_seam``, the one leaf holding ``apply_status_change``.
        ``_record`` (the settlement value and its reads) and ``_refusals`` (the
        invariants as guards) assign nothing, so a ``status_id`` write appearing
        in either is a write that went around the seam and must be flagged.
        """
        for leaf in (
            "app.services.status_seam._record",
            "app.services.status_seam._refusals",
        ):
            node = self._status_assign("txn.status_id = new_status_id", leaf)
            with self.assertAddsMessages(
                MessageTest("shekel-transaction-status-bypass", node=node),
                ignore_position=True,
            ):
                self.checker.visit_assignattr(node)

    def test_allows_a_submodule_of_an_ALLOWLISTED_module(self) -> None:
        """The package-prefix match itself still holds, for the named entry.

        The control for the test above: prefix matching is not broken, it is
        merely pointed at a leaf.  A submodule of an allowlisted module stays
        exempt, which is what lets an entry survive a further split of the
        module it names.
        """
        node = self._status_assign(
            "txn.status_id = new_status_id",
            "app.services.status_seam._seam._mechanics",
        )
        with self.assertNoMessages():
            self.checker.visit_assignattr(node)

    def test_flags_same_basename_in_another_package(self) -> None:
        """A same-named module in another package is NOT exempted (no collision bypass).

        The allowlist keys off the FULL module path, so a hypothetical
        ``app/routes/status_seam.py`` is still flagged.
        """
        node = self._status_assign(
            "txn.status_id = x", "app.routes.status_seam",
        )
        with self.assertAddsMessages(
            MessageTest("shekel-transaction-status-bypass", node=node),
            ignore_position=True,
        ):
            self.checker.visit_assignattr(node)

    def test_ignores_other_attribute_assignment(self) -> None:
        """Assigning a non-status_id attribute is not this checker's concern."""
        node = self._status_assign(
            "txn.estimated_amount = amount",
            "app.routes.transactions.mutations",
        )
        with self.assertNoMessages():
            self.checker.visit_assignattr(node)

    def test_ignores_filing_status_id_assignment(self) -> None:
        """filing_status_id (a different attribute on the tax tables) is not matched."""
        node = self._status_assign(
            "profile.filing_status_id = fs_id",
            "app.services.tax_config_service",
        )
        with self.assertNoMessages():
            self.checker.visit_assignattr(node)

    def test_ignores_status_id_read_in_assigned_value(self) -> None:
        """A status_id READ on the value side is never flagged; only the target counts.

        ``txn.notes = (other.status_id == 5)`` assigns ``notes`` (the
        ``AssignAttr`` the checker visits); the ``other.status_id`` on the value
        side is a load-context ``Attribute`` the visitor never receives, so no
        message fires -- the structural reason a ``status_id ==`` comparison is
        immune.
        """
        node = self._status_assign(
            "txn.notes = (other.status_id == 5)",
            "app.routes.transactions.mutations",
        )
        with self.assertNoMessages():
            self.checker.visit_assignattr(node)

    # ── the call-shaped write forms (H3/R3: ctor, setattr, bulk) ──────

    @staticmethod
    def _status_call(call_source: str, module_name: str) -> nodes.Call:
        """Return the Call node of *call_source* parsed in *module_name*.

        Mirrors the seam checker's ``_producer_call``: the snippet is a single
        statement (a plain expression or an assignment), so the module body's
        one statement carries the OUTER call under test as its ``value`` --
        exactly the node a real run dispatches to ``visit_call`` for the
        method-call forms (``.update`` / ``.values``), whose inner calls are
        separate nodes.
        """
        module = astroid.parse(f"{call_source}\n", module_name=module_name)
        return module.body[0].value

    def test_flags_born_settled_ctor(self) -> None:
        """Transaction(status_id=done_id) constructs a born-settled row; flagged.

        A born-settled row would carry NULL paid_at, skip verify_transition,
        and emit no ledger posting -- the H3 failure mode.
        """
        node = self._status_call(
            "txn = Transaction(status_id=done_id, name=name)",
            "app.services.some_import_service",
        )
        with self.assertAddsMessages(
            MessageTest("shekel-transaction-status-bypass", node=node),
            ignore_position=True,
        ):
            self.checker.visit_call(node)

    def test_flags_ctor_with_settled_ref_cache_lookup(self) -> None:
        """A ref-cache lookup of a NON-Projected member in a ctor is flagged."""
        node = self._status_call(
            "txn = Transaction(status_id=ref_cache.status_id(StatusEnum.PAID))",
            "app.services.some_import_service",
        )
        with self.assertAddsMessages(
            MessageTest("shekel-transaction-status-bypass", node=node),
            ignore_position=True,
        ):
            self.checker.visit_call(node)

    def test_flags_ctor_with_opaque_status_variable(self) -> None:
        """An unrecognizable ctor status value fails closed and is flagged.

        ``Transaction(status_id=some_status)`` cannot be statically proven
        Projected; the fence's dangerous mode is the false negative, so the
        author must spell the value in a canonical Projected form (the
        ref-cache PROJECTED lookup or a projected_id name) or settle through
        the seam after creation.
        """
        node = self._status_call(
            "txn = Transaction(status_id=some_status)",
            "app.routes.transactions.create",
        )
        with self.assertAddsMessages(
            MessageTest("shekel-transaction-status-bypass", node=node),
            ignore_position=True,
        ):
            self.checker.visit_call(node)

    def test_flags_transfer_ctor_born_settled(self) -> None:
        """Transfer(status_id=done_id) outside the seam modules is flagged too."""
        node = self._status_call(
            "xfer = Transfer(status_id=done_id)",
            "app.routes.transfers.mutations",
        )
        with self.assertAddsMessages(
            MessageTest("shekel-transaction-status-bypass", node=node),
            ignore_position=True,
        ):
            self.checker.visit_call(node)

    def test_flags_attribute_form_model_ctor(self) -> None:
        """models.Transaction(status_id=done_id) -- the qualified ctor -- is flagged."""
        node = self._status_call(
            "txn = models.Transaction(status_id=done_id)",
            "app.services.some_import_service",
        )
        with self.assertAddsMessages(
            MessageTest("shekel-transaction-status-bypass", node=node),
            ignore_position=True,
        ):
            self.checker.visit_call(node)

    def test_allows_ctor_born_projected_ref_cache_lookup(self) -> None:
        """Transaction(status_id=ref_cache.status_id(StatusEnum.PROJECTED)) is the rule; not flagged.

        The credit-workflow / carry-forward create form.
        """
        node = self._status_call(
            "payback = Transaction("
            "status_id=ref_cache.status_id(StatusEnum.PROJECTED), name=name)",
            "app.services.credit_workflow",
        )
        with self.assertNoMessages():
            self.checker.visit_call(node)

    def test_allows_ctor_born_projected_plan_attribute(self) -> None:
        """Transaction(status_id=plan.projected_id) -- the recurrence-engine form -- is not flagged."""
        node = self._status_call(
            "txn = Transaction(status_id=plan.projected_id)",
            "app.services.recurrence_engine",
        )
        with self.assertNoMessages():
            self.checker.visit_call(node)

    def test_allows_ctor_born_projected_bare_name(self) -> None:
        """A bare projected_id local as the ctor status is recognized; not flagged.

        (A Transfer constructed outside transfer_service would violate the
        transfer INVARIANTS -- but that is a different rule with its own
        guards, not the status fence's concern.)
        """
        node = self._status_call(
            "xfer = Transfer(status_id=projected_id)",
            "app.routes.transfers.mutations",
        )
        with self.assertNoMessages():
            self.checker.visit_call(node)

    def test_allows_ctor_splat_kwargs(self) -> None:
        """Transaction(**data) carries no named status kwarg; not flagged.

        The create-route form: statically invisible, governed by the
        documented convention (the schema omits status_id and the route
        assigns Projected into the dict unconditionally).
        """
        node = self._status_call(
            "txn = Transaction(**data)",
            "app.routes.transactions.create",
        )
        with self.assertNoMessages():
            self.checker.visit_call(node)

    def test_allows_ctor_without_status_kwarg(self) -> None:
        """A model ctor with no status_id kwarg is not this fence's concern."""
        node = self._status_call(
            "txn = Transaction(name=name, estimated_amount=amount)",
            "app.services.some_import_service",
        )
        with self.assertNoMessages():
            self.checker.visit_call(node)

    def test_allows_transfer_spec_with_any_status(self) -> None:
        """TransferSpec(status_id=...) is a service spec, not a model ctor; not flagged.

        The spec is consumed by transfer_service.create_transfer, which owns
        the actual model construction inside the allowlist.
        """
        node = self._status_call(
            "spec = transfer_service.TransferSpec(status_id=done_id)",
            "app.routes.transfers.mutations",
        )
        with self.assertNoMessages():
            self.checker.visit_call(node)

    def test_allows_service_call_with_status_kwarg(self) -> None:
        """update_transfer(..., status_id=...) is the sanctioned service path; not flagged."""
        node = self._status_call(
            "transfer_service.update_transfer("
            "xfer_id, user_id, status_id=cancelled_id)",
            "app.routes.transactions.mutations",
        )
        with self.assertNoMessages():
            self.checker.visit_call(node)

    def test_allows_born_settled_ctor_in_transfer_service(self) -> None:
        """transfer_service constructs shadows with the parent's status; exempt.

        Shadow rows are born with spec.status_id / the parent transfer's
        status -- not necessarily Projected -- and transfer_service is the
        sanctioned owner of that construction.
        """
        node = self._status_call(
            "shadow = Transaction(status_id=spec.status_id)",
            "app.services.transfer_service._create",
        )
        with self.assertNoMessages():
            self.checker.visit_call(node)

    def test_flags_setattr_status_literal(self) -> None:
        """setattr(txn, \"status_id\", value) writes the column past the AssignAttr visitor; flagged."""
        node = self._status_call(
            'setattr(txn, "status_id", value)',
            "app.routes.transactions.mutations",
        )
        with self.assertAddsMessages(
            MessageTest("shekel-transaction-status-bypass", node=node),
            ignore_position=True,
        ):
            self.checker.visit_call(node)

    def test_allows_setattr_status_literal_in_seam(self) -> None:
        """The seam module may use any write form it owns; not flagged."""
        node = self._status_call(
            'setattr(txn, "status_id", new_status_id)',
            "app.services.status_seam._seam",
        )
        with self.assertNoMessages():
            self.checker.visit_call(node)

    def test_allows_setattr_other_literal_field(self) -> None:
        """setattr of a different literal field is not this fence's concern."""
        node = self._status_call(
            'setattr(txn, "notes", value)',
            "app.routes.transactions.mutations",
        )
        with self.assertNoMessages():
            self.checker.visit_call(node)

    def test_allows_setattr_dynamic_field_loop(self) -> None:
        """setattr(txn, field, value) -- the schema-loop form -- is not matched.

        Statically invisible: the mutations route's loop excludes status_id
        with a ``continue`` and routes it through the seam; that guard stays
        with review and the route tests, as documented on the checker.
        """
        node = self._status_call(
            "setattr(txn, field, value)",
            "app.routes.transactions.mutations",
        )
        with self.assertNoMessages():
            self.checker.visit_call(node)

    def test_flags_query_update_with_status_string_key(self) -> None:
        """A bulk Query.update carrying a \"status_id\" key bypasses the seam; flagged."""
        node = self._status_call(
            "db.session.query(Transaction).update("
            '{"status_id": done_id}, synchronize_session="fetch")',
            "app.routes.templates",
        )
        with self.assertAddsMessages(
            MessageTest("shekel-transaction-status-bypass", node=node),
            ignore_position=True,
        ):
            self.checker.visit_call(node)

    def test_flags_update_with_column_attribute_key(self) -> None:
        """The column-object key form ({Transaction.status_id: ...}) is flagged too."""
        node = self._status_call(
            "query.update({Transaction.status_id: done_id})",
            "app.services.some_import_service",
        )
        with self.assertAddsMessages(
            MessageTest("shekel-transaction-status-bypass", node=node),
            ignore_position=True,
        ):
            self.checker.visit_call(node)

    def test_flags_values_with_status_keyword(self) -> None:
        """update(Transaction).values(status_id=...) -- the Core form -- is flagged."""
        node = self._status_call(
            "update(Transaction).values(status_id=done_id)",
            "app.services.some_import_service",
        )
        with self.assertAddsMessages(
            MessageTest("shekel-transaction-status-bypass", node=node),
            ignore_position=True,
        ):
            self.checker.visit_call(node)

    def test_flags_update_with_dict_builder(self) -> None:
        """query.update(dict(status_id=...)) -- the dict() builder -- is flagged."""
        node = self._status_call(
            "query.update(dict(status_id=done_id))",
            "app.services.some_import_service",
        )
        with self.assertAddsMessages(
            MessageTest("shekel-transaction-status-bypass", node=node),
            ignore_position=True,
        ):
            self.checker.visit_call(node)

    def test_allows_update_without_status_key(self) -> None:
        """The in-tree bulk soft-delete form (is_deleted only) is not flagged."""
        node = self._status_call(
            'query.update({"is_deleted": True}, synchronize_session="fetch")',
            "app.routes.templates",
        )
        with self.assertNoMessages():
            self.checker.visit_call(node)

    def test_allows_update_with_filing_status_key(self) -> None:
        """filing_status_id (the tax tables) is a different key; not matched."""
        node = self._status_call(
            'query.update({"filing_status_id": fs_id})',
            "app.services.tax_config_service",
        )
        with self.assertNoMessages():
            self.checker.visit_call(node)

    def test_allows_dict_update_with_non_literal_payload(self) -> None:
        """context.update(extra) -- a payload passed by name -- is not statically visible.

        The in-tree plain-dict merge form; a bulk-write payload built away
        from the call is a documented residual, not a false positive.
        """
        node = self._status_call(
            "context.update(extra_context)",
            "app.routes.settings",
        )
        with self.assertNoMessages():
            self.checker.visit_call(node)

    def test_allows_every_seam_module_for_call_forms(self) -> None:
        """EVERY allowlisted module is exempt from the call-shaped forms too.

        The companion to test_allows_every_seam_module: binds the call-form
        exemption to :data:`_STATUS_SEAM_MODULES` itself, using the strictest
        form (a born-settled ctor).
        """
        for module_name in sorted(_STATUS_SEAM_MODULES):
            node = self._status_call(
                "txn = Transaction(status_id=done_id)", module_name,
            )
            with self.assertNoMessages():
                self.checker.visit_call(node)
