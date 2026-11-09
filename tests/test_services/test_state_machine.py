"""
Shekel Budget App -- State Machine Unit Tests

Direct tests of the two ``app.services.state_machine`` helpers:
``verify_transition`` -- governs every legal transition for both
Transaction.status_id and Transfer.status_id -- and
``finalised_edit_rejection`` -- the companion that locks money / period
/ category / due-date field edits on a finalised (is_immutable) row
(#26).

Audit reference: F-046 / F-047 / F-161 / commit C-21 of the
2026-04-15 security remediation plan.

Coverage matrix
---------------

For every (current, new) pair where the helper either accepts or
rejects, we have one positive and one negative test.  Identity
transitions (current == new) are covered for every status because the
HTMX UI's idempotent re-submission relies on them silently
succeeding.

The helper does not consult the database -- it reads ref_cache, which
is initialised by the ``app`` fixture's create_app() flow.  All tests
therefore use ``app.app_context()`` so ``ref_cache.status_id(...)``
resolves to the live integer PKs.
"""

import pytest

from app import ref_cache
from app.enums import StatusEnum
from app.exceptions import ValidationError
from app.models.ref import Status
from app.models.transaction import Transaction
from app.models.transfer import Transfer
from app.services.state_machine import (
    _build_transitions,
    allowed_transitions,
    finalised_edit_rejection,
    verify_transition,
)


# ── Helpers ─────────────────────────────────────────────────────────


def _ids(app):
    """Resolve all StatusEnum members to their integer PKs.

    Returned as a dict so individual tests can read ``ids["projected"]``
    etc. without re-typing ref_cache calls.
    """
    with app.app_context():
        return {
            "projected": ref_cache.status_id(StatusEnum.PROJECTED),
            "done": ref_cache.status_id(StatusEnum.DONE),
            "received": ref_cache.status_id(StatusEnum.RECEIVED),
            "credit": ref_cache.status_id(StatusEnum.CREDIT),
            "cancelled": ref_cache.status_id(StatusEnum.CANCELLED),
        }


def _txn(status_id):
    """Return an unsaved Transaction carrying *status_id*.

    The state machine reads two things off the row -- its class, which selects
    the workflow, and its ``status_id``, which is the current state -- so an
    unsaved instance is a complete input and no session is needed.  Replaces
    the entity-label string these tests used to pass beside a bare id (plan
    step X-aj1, ruling R-DN): the workflow is the row's own now, so a test can
    no longer assert a transaction id against the transfer map or the reverse.

    **Not a row** -- never added to a session -- so it is outside plan step
    balance:X-bi-7c's move of the suite's hand-built rows onto the one-off
    producer: an instance used as a type token names no link because it
    names no identity columns, and the cutover's CHECK never sees it.
    """
    return Transaction(status_id=status_id)


def _xfer(status_id):
    """Return an unsaved Transfer carrying *status_id*.

    The transfer-workflow twin of :func:`_txn`; see there.
    """
    return Transfer(status_id=status_id)


# ── Legal transitions from Projected ────────────────────────────────


class TestLegalTransitionsFromProjected:
    """Projected is the workflow's entry point; every active state is
    reachable from it, plus the identity edge for idempotent resubmits.
    Credit is not active: the old rows keep it, and nothing enters it (plan
    task plan#3057)."""

    def test_projected_to_done(self, app):
        """Mark expense paid -- the most common transition."""
        ids = _ids(app)
        with app.app_context():
            verify_transition(_txn(ids["projected"]), ids["done"])

    def test_projected_to_received(self, app):
        """Income deposited -- mirrors projected -> done for expenses."""
        ids = _ids(app)
        with app.app_context():
            verify_transition(_txn(ids["projected"]), ids["received"])

    def test_projected_to_credit_rejected(self, app):
        """No row enters Credit: the Credit press settles it as Paid on the card.

        Plan task plan#3057, finding plan#2133: this edge was legal and the
        popover's Status dropdown offered it, so a Save picking Credit set the
        bare status, with no payback and no movement, and the row's money left
        checking's projection.  Credit puts a row on the card as Paid since
        plan task plan#2929 (rulings plan#2887, plan#2888), so nothing is left
        to enter the stored status, which the old rows keep.  It was
        ``test_projected_to_credit``, asserting the edge legal.
        """
        ids = _ids(app)
        with app.app_context():
            with pytest.raises(ValidationError, match="from Projected .* to Credit"):
                verify_transition(_txn(ids["projected"]), ids["credit"])

    def test_projected_to_cancelled(self, app):
        """Cancel a projected item -- never paid."""
        ids = _ids(app)
        with app.app_context():
            verify_transition(_txn(ids["projected"]), ids["cancelled"])

    def test_projected_to_projected_identity(self, app):
        """Idempotent re-submit of "set projected" must succeed silently."""
        ids = _ids(app)
        with app.app_context():
            verify_transition(_txn(ids["projected"]), ids["projected"])


# ── Illegal transitions from Projected ──────────────────────────────


class TestProjectedReachesEveryStatus:
    """Projected is the OPEN state: every status but Credit is a legal successor.

    This class used to pin the one move Projected could NOT make -- direct
    ``projected -> settled``, which bypassed the Done/Received audit row.  Plan
    step **X-am** deleted the ARCHIVE, and with it the last status Projected
    could not reach, so a test naming one illegal move would have nothing to
    name.

    Asserting the WHOLE successor set instead is strictly stronger, and it is
    what ``lessons.md`` asks of a narrowing: grade what is left, not only what
    was removed.  A future status added to the enum without a decision about
    whether Projected may reach it fails HERE rather than shipping as whichever
    the map's author defaulted to.
    """

    def test_projected_reaches_every_seeded_status(self, app):
        """The successor set is every status but Credit, identity included.

        Every status until plan task plan#3057, which decided that Projected
        may not reach Credit (:meth:`TestLegalTransitionsFromProjected.
        test_projected_to_credit_rejected` says why): the decision this
        class's docstring asks each status to have.
        """
        with app.app_context():
            reachable = allowed_transitions(
                _txn(ref_cache.status_id(StatusEnum.PROJECTED))
            )
            every = {
                ref_cache.status_id(m) for m in StatusEnum
                if m is not StatusEnum.CREDIT
            }
            names = {ref_cache.status_id(m): m.value for m in StatusEnum}
            assert reachable == every, (
                "Projected no longer reaches every status but Credit: missing "
                f"{sorted(names[i] for i in every - reachable)}, "
                f"unexpected {sorted(names.get(i, i) for i in reachable - every)}"
            )


# ── Legal transitions from Done / Received ──────────────────────────


class TestLegalTransitionsFromDoneReceived:
    """Done and Received share the same successor set: projected (revert)
    and the identity edge.

    It held a third member, ``settled`` -- the archive -- until plan step
    **X-am**."""

    def test_done_and_received_successors_are_exactly_revert_and_identity(
        self, app,
    ):
        """Each settled status reaches its own identity and Projected, nothing else.

        Both sets held a THIRD member until plan step **X-am**: ``Settled``, the
        archive.  Pinning the exact set rather than each edge is what catches a
        re-added one -- an edge test can only fail for an edge somebody thought
        to write.
        """
        ids = _ids(app)
        names = {v: k for k, v in ids.items()}
        with app.app_context():
            for current in ("done", "received"):
                reachable = allowed_transitions(_txn(ids[current]))
                assert reachable == {ids[current], ids["projected"]}, (
                    f"{current} reaches "
                    f"{sorted(names.get(i, i) for i in reachable)}"
                )

    def test_done_to_projected_revert(self, app):
        """Revert a mistakenly-marked Paid expense back to Projected."""
        ids = _ids(app)
        with app.app_context():
            verify_transition(_txn(ids["done"]), ids["projected"])

    def test_received_to_projected_revert(self, app):
        """Revert a mistakenly-marked Received income back to Projected."""
        ids = _ids(app)
        with app.app_context():
            verify_transition(_txn(ids["received"]), ids["projected"])

    def test_done_to_done_identity(self, app):
        """Re-marking Paid is idempotent."""
        ids = _ids(app)
        with app.app_context():
            verify_transition(_txn(ids["done"]), ids["done"])


# ── Illegal transitions from Done / Received ────────────────────────


class TestIllegalTransitionsFromDoneReceived:
    """Done and Received cannot jump sideways to Credit or Cancelled --
    the user must revert to Projected first so the audit log records
    both legs of the change."""

    def test_done_to_credit_rejected(self, app):
        """Cannot reclassify a paid expense as credit without reverting first."""
        ids = _ids(app)
        with app.app_context():
            with pytest.raises(ValidationError):
                verify_transition(_txn(ids["done"]), ids["credit"])

    def test_done_to_cancelled_rejected(self, app):
        """Cannot cancel a paid expense -- the payment already happened."""
        ids = _ids(app)
        with app.app_context():
            with pytest.raises(ValidationError):
                verify_transition(_txn(ids["done"]), ids["cancelled"])

    def test_received_to_credit_rejected(self, app):
        """Income cannot become credit -- credit is expense-only."""
        ids = _ids(app)
        with app.app_context():
            with pytest.raises(ValidationError):
                verify_transition(_txn(ids["received"]), ids["credit"])

    def test_received_to_cancelled_rejected(self, app):
        """Cannot cancel received income -- the deposit already happened."""
        ids = _ids(app)
        with app.app_context():
            with pytest.raises(ValidationError):
                verify_transition(_txn(ids["received"]), ids["cancelled"])


# ── Credit / Cancelled successors ───────────────────────────────────


class TestCreditAndCancelledSuccessors:
    """Credit and Cancelled both have a single non-identity successor:
    Projected.  This keeps the carry-forward and audit invariants intact
    -- a row reactivated from either state is observably identical to
    a freshly projected row."""

    def test_credit_to_projected(self, app):
        """unmark_credit transitions credit -> projected."""
        ids = _ids(app)
        with app.app_context():
            verify_transition(_txn(ids["credit"]), ids["projected"])

    def test_cancelled_to_projected(self, app):
        """Reactivate a cancelled item back to projected."""
        ids = _ids(app)
        with app.app_context():
            verify_transition(_txn(ids["cancelled"]), ids["projected"])

    def test_credit_to_done_rejected(self, app):
        """Credit -> Done would skip the auto-payback cleanup workflow."""
        ids = _ids(app)
        with app.app_context():
            with pytest.raises(ValidationError):
                verify_transition(_txn(ids["credit"]), ids["done"])

    def test_cancelled_to_done_rejected(self, app):
        """Cancelled -> Done would resurrect a row without the projected step."""
        ids = _ids(app)
        with app.app_context():
            with pytest.raises(ValidationError):
                verify_transition(_txn(ids["cancelled"]), ids["done"])


# ── Neither map has an absorbing state ──────────────────────────────


class TestNoStateIsBothReachableAndAbsorbing:
    """The law plan step **X-am** establishes, and it is a CONJUNCTION.

    ``Settled`` -- the ARCHIVE -- was ABSORBING (no outgoing edge but identity)
    and also REACHABLE (from ``done`` and ``received``, offered by the full-edit
    popover's Status dropdown).  That pair is the trapdoor: a row could be put
    somewhere it could never be corrected from, while the delete control on the
    same card still removed it and reversed its postings
    (``transaction_service.deletion_refusal`` names no status).

    **It is deliberately NOT "no terminal states".**  An absorbing state nothing
    can ENTER is a retired vocabulary item, and this project has already ruled
    for one: ``credit_card`` locked ruling 5 (2026-07-19, *do not reopen*) makes
    ``Credit`` terminal at step ``CC3b`` while ``projected`` loses its edge to
    it in the same commit.  A control banning terminal states outright would
    have been in contradiction with a locked ruling on the day it was written,
    and would have failed CC3b for doing exactly what the developer decided.
    Grading the conjunction admits that shape and still refuses this one.

    Walks the maps themselves rather than a list of statuses somebody
    remembered to write down, so a future trapdoor fails here and has to be a
    decision instead of an edit.
    """

    @staticmethod
    def _trapdoors(transitions):
        """Return the states in *transitions* that are reachable AND absorbing.

        Reachable: some OTHER state has an edge into it.  Absorbing: it has no
        outgoing edge except to itself.
        """
        trapped = []
        for state, moves in transitions.items():
            absorbing = not (moves - {state})
            reachable = any(
                state in other_moves
                for other, other_moves in transitions.items()
                if other != state
            )
            if absorbing and reachable:
                trapped.append(state)
        return trapped

    def test_the_transaction_map_holds_no_trapdoor(self, app):
        """No transaction status can be entered and not left."""
        with app.app_context():
            self._assert_no_trapdoor("transaction")

    def test_the_transfer_map_holds_no_trapdoor(self, app):
        """No transfer status can be entered and not left."""
        with app.app_context():
            self._assert_no_trapdoor("transfer")

    @staticmethod
    def _cannot_reach_projected(transitions, projected):
        """Return the states with no PATH to ``projected``, by closure.

        A state need not be ABSORBING to be a dead end.  A CYCLE that never
        reaches Projected traps every row in it while each of its states has an
        outgoing edge, so ``_trapdoors`` -- which asks about one edge -- cannot
        see it, and this closure can.

        **Measured rather than argued, because the obvious example is wrong.**
        ``cancelled: {cancelled, done}`` looks like a dead end and is not:
        ``done`` reaches ``projected``, so the row gets out in two steps and
        BOTH arms pass, correctly.  What does strand is a closed cycle --
        planting ``credit: {credit, cancelled}`` with ``cancelled: {cancelled,
        credit}`` fails THIS arm with ``['Cancelled', 'Credit']`` and leaves
        the trapdoor arm green.  That is the evidence the two arms are not one
        assertion written twice.
        """
        reaching = {projected}
        changed = True
        while changed:
            changed = False
            for state, moves in transitions.items():
                if state not in reaching and moves & reaching:
                    reaching.add(state)
                    changed = True
        return sorted(set(transitions) - reaching)

    def _assert_no_trapdoor(self, context):
        """Assert *context*'s map is well-formed, closed, and trapdoor-free.

        THREE arms, because the first two each pass on a shape the other
        catches and neither can see an empty map:

        1. the map holds exactly the seeded statuses -- without it every
           assertion below is vacuous on a map that dropped a state, and
           ``_trapdoors({})`` really does answer ``[]`` (measured);
        2. no state is both REACHABLE and ABSORBING -- the trapdoor;
        3. every state has a PATH to Projected -- which is what
           ``entry_service.removal_refusal`` rests its single *set the row back
           to Projected* remedy on, and what arm 2 alone does NOT grade:
           ``cancelled: {cancelled, done}`` passes arm 2 and fails this.
        """
        # pylint: disable-next=protected-access
        transitions = _build_transitions()[context]
        names = {ref_cache.status_id(m): m.value for m in StatusEnum}

        expected = {
            ref_cache.status_id(member) for member in StatusEnum
        } if context == "transaction" else {
            ref_cache.status_id(member) for member in (
                StatusEnum.PROJECTED, StatusEnum.DONE, StatusEnum.CANCELLED,
            )
        }
        assert set(transitions) == expected, (
            f"the {context} map's states are "
            f"{sorted(names.get(s, s) for s in transitions)}, expected "
            f"{sorted(names[s] for s in expected)} -- every assertion below "
            "is vacuous on a map that dropped one"
        )

        trapped = sorted(
            names.get(state, state)
            for state in self._trapdoors(transitions)
        )
        assert not trapped, (
            f"{context} statuses a row can enter and never leave: {trapped}. "
            "A row there can be destroyed but not corrected -- see this "
            "class's docstring."
        )

        stranded = [
            names.get(state, state)
            for state in self._cannot_reach_projected(
                transitions, ref_cache.status_id(StatusEnum.PROJECTED),
            )
        ]
        assert not stranded, (
            f"{context} statuses with no path back to Projected: {stranded}. "
            "Every refusal that tells the owner to revert the row names a "
            "repair those rows cannot take."
        )

    def test_it_REFUSES_the_archive_shape(self, app):
        """The predicate fires on the map ``Settled`` actually had.

        Reconstructed rather than described: ``done -> settled`` and
        ``settled: {settled}``.  Without this the class could be reading a
        predicate that never fires and would print the same green
        (``lessons.md``: a passing gate may have measured nothing).
        """
        with app.app_context():
            projected = ref_cache.status_id(StatusEnum.PROJECTED)
            done = ref_cache.status_id(StatusEnum.DONE)
            archive = -1  # the deleted status, standing in for its id
            archive_shape = {
                projected: {projected, done},
                done: {done, projected, archive},
                archive: {archive},
            }
            assert self._trapdoors(archive_shape) == [archive]
            assert self._cannot_reach_projected(archive_shape, projected) == [
                archive,
            ]

    def test_it_ADMITS_a_retired_vocabulary_item(self, app):
        """The predicate passes the shape ``credit_card`` CC3b will build.

        ``credit: {credit}`` terminal WITH ``projected`` losing its edge to it,
        which is locked ruling 5 read as a map.  This is the half that makes
        the control a conjunction rather than a ban, and it fails the moment
        somebody re-broadens it -- which is what would break CC3b.
        """
        with app.app_context():
            projected = ref_cache.status_id(StatusEnum.PROJECTED)
            done = ref_cache.status_id(StatusEnum.DONE)
            credit = ref_cache.status_id(StatusEnum.CREDIT)
            retired = {
                projected: {projected, done},
                done: {done, projected},
                credit: {credit},
            }
            assert self._trapdoors(retired) == []
            assert self._cannot_reach_projected(retired, projected) == [
                credit,
            ], (
                "a retired vocabulary item IS stranded from Projected, and "
                "that is legal precisely because nothing can enter it -- the "
                "two arms answer differently here, which is why the class "
                "asserts the live maps against BOTH rather than either"
            )


# ── Defensive: corrupt or unknown current state ─────────────────────


class TestCorruptCurrentStateRejected:
    """A row whose current status_id is not a recognised StatusEnum
    member must fail loudly -- silently accepting the transition would
    let the corrupt row drift into a worse state without an audit log
    entry pointing at the source."""

    def test_unknown_current_status_rejected(self, app):
        """An invented current_status_id is rejected with a clear message."""
        ids = _ids(app)
        # Choose a value guaranteed to fall outside the StatusEnum PK
        # range (PostgreSQL identity columns start at 1; -1 is never
        # legitimately assigned).
        with app.app_context():
            with pytest.raises(ValidationError) as excinfo:
                verify_transition(_txn(-1), ids["projected"])
            # Message must name the unknown ID so an operator can
            # locate the offending row in the audit log.
            assert "-1" in str(excinfo.value)


# ── Transfer context: the smaller status vocabulary ─────────────────


class TestTransferTransitions:
    """Transfers get their own transition map: Credit is excluded (the
    credit/auto-payback workflow is expense-only and refuses transfers,
    so a Credit transfer would be balance-excluded on both accounts
    with no compensating payback) and Received is excluded (a display
    convention for regular income rows; transfers settle with Done).
    Before the split the shared map let a crafted PATCH push a transfer
    plus both shadows into either state."""

    def test_projected_to_credit_rejected_for_transfer(self, app):
        """The vanishing-transfer hole: projected -> credit must be refused."""
        ids = _ids(app)
        with app.app_context():
            with pytest.raises(ValidationError) as excinfo:
                verify_transition(_xfer(ids["projected"]), ids["credit"])
            assert "transfer" in str(excinfo.value)

    def test_projected_to_received_rejected_for_transfer(self, app):
        """Received is a regular-income display convention, not a transfer state."""
        ids = _ids(app)
        with app.app_context():
            with pytest.raises(ValidationError):
                verify_transition(_xfer(ids["projected"]), ids["received"])

    def test_projected_to_done_allowed_for_transfer(self, app):
        """Mark a transfer done -- the normal settle path for both shadows."""
        ids = _ids(app)
        with app.app_context():
            verify_transition(_xfer(ids["projected"]), ids["done"])

    def test_projected_to_cancelled_allowed_for_transfer(self, app):
        """Cancel a projected transfer."""
        ids = _ids(app)
        with app.app_context():
            verify_transition(_xfer(ids["projected"]), ids["cancelled"])

    def test_done_to_projected_revert_allowed_for_transfer(self, app):
        """Revert a mistakenly-marked transfer back to Projected."""
        ids = _ids(app)
        with app.app_context():
            verify_transition(_xfer(ids["done"]), ids["projected"])

    def test_cancelled_to_projected_allowed_for_transfer(self, app):
        """Reactivate a cancelled transfer."""
        ids = _ids(app)
        with app.app_context():
            verify_transition(_xfer(ids["cancelled"]), ids["projected"])

    def test_credit_as_current_status_rejected_for_transfer(self, app):
        """A transfer already sitting in Credit is corrupt for this entity;
        the refusal message names the context so the operator knows the
        status is transaction-only rather than entirely unknown."""
        ids = _ids(app)
        with app.app_context():
            with pytest.raises(ValidationError) as excinfo:
                verify_transition(_xfer(ids["credit"]), ids["projected"])
            assert "transfer" in str(excinfo.value)

    def test_every_transfer_legal_move_is_also_transaction_legal(self, app):
        """The transfer map must stay a SUBSET of the transaction map.

        Two shipped docstrings rest on this and neither could see it before:
        ``transfer_service._status.apply_status_to_all_three`` says the shadow
        verifications "pass by construction for any transfer whose own
        transition was legal", and ``restore_transfer``'s repair says its
        transition check "cannot raise here".  Both are true only while every
        move the TRANSFER map permits is one the TRANSACTION map also permits,
        because a transfer's status is mirrored onto two ``Transaction`` rows
        that are graded against the transaction map.

        Widening the transfer map alone -- say adding ``cancelled -> done`` --
        would satisfy every other test in this file and then 400 every real
        transfer edit at the shadow step.  This is the control that fails
        instead.
        """
        with app.app_context():
            # pylint: disable-next=protected-access
            maps = _build_transitions()
            txn_map, xfer_map = maps["transaction"], maps["transfer"]
            offending = {
                current: sorted(moves - txn_map.get(current, set()))
                for current, moves in xfer_map.items()
                if moves - txn_map.get(current, set())
            }
            assert not offending, (
                f"transfer-legal moves the transaction map refuses: {offending}"
            )

    def test_the_subset_control_is_not_vacuous(self, app):
        """The reverse does NOT hold -- so the check above is a real constraint.

        Without this, ``test_every_transfer_legal_move_is_also_transaction_legal``
        would still pass if the two maps were made identical, and the asymmetry
        it protects (transfers exclude Credit and Received, deliberately, for
        the money reason in this module's docstring) would be gone unnoticed.
        """
        with app.app_context():
            # pylint: disable-next=protected-access
            maps = _build_transitions()
            txn_map, xfer_map = maps["transaction"], maps["transfer"]
            extra = {
                current: sorted(moves - xfer_map.get(current, set()))
                for current, moves in txn_map.items()
                if moves - xfer_map.get(current, set())
            }
            assert extra, (
                "the transaction map no longer permits anything the transfer "
                "map forbids -- the two workflows have collapsed into one"
            )

    def test_a_row_that_is_not_status_bearing_raises_type_error(self, app):
        """A non-status-bearing row is a programming error -- fail loud, never
        silently fall back to either entity's map.

        This asserted a typo'd ``context`` string until plan step X-aj1 (ruling
        R-DN).  That error is now UNREPRESENTABLE -- the workflow comes from the
        row's own class, so there is no label to misspell -- and the class of
        mistake that remains is handing the state machine something that is not
        a status-bearing row at all.  The refusal matters in the same way the
        old one did: the transaction map is a strict SUPERSET of the transfer
        map, so a silent default would widen whatever was passed.
        """
        with app.app_context():
            with pytest.raises(TypeError):
                verify_transition(object(), ref_cache.status_id(StatusEnum.DONE))


# ── Context label propagates to the exception message ───────────────


class TestContextLabelPropagation:
    """The context label ("transaction" / "transfer") must appear in the
    raised ValidationError so the route layer can surface a precise
    message to the user without parsing strings."""

    def test_transaction_context_label(self, app):
        """A failing transaction transition mentions "transaction"."""
        ids = _ids(app)
        with app.app_context():
            with pytest.raises(ValidationError) as excinfo:
                verify_transition(_txn(ids["cancelled"]), ids["done"])
            assert "transaction" in str(excinfo.value)

    def test_transfer_context_label(self, app):
        """A failing transfer transition mentions "transfer"."""
        ids = _ids(app)
        with app.app_context():
            with pytest.raises(ValidationError) as excinfo:
                verify_transition(_xfer(ids["cancelled"]), ids["done"])
            assert "transfer" in str(excinfo.value)


# ── finalised_edit_rejection: the field-edit lock ───────────────────


class TestFinalisedEditRejection:
    """``finalised_edit_rejection`` locks money / period / category /
    due-date edits on a finalised (``is_immutable``) row unless the same
    request reverts it to a mutable status.  Pure policy + message: it
    reads only ``Status.is_immutable`` / ``Status.name``, so the tests
    construct in-memory ``Status`` rows (no DB) to pin every branch."""

    # In-memory Status fixtures mirroring app/ref_seeds.py: Projected is
    # the only mutable status; the rest are finalised.
    _PROJECTED = Status(name="Projected", is_immutable=False)
    _PAID = Status(name="Paid", is_immutable=True)
    _CANCELLED = Status(name="Cancelled", is_immutable=True)

    def test_mutable_row_allows_edit(self):
        """A Projected row is never locked, regardless of new status."""
        assert finalised_edit_rejection(self._PROJECTED, None) is None

    def test_mutable_row_allows_edit_even_marking_done(self):
        """Projected -> Paid in one request (settle at an amount) is allowed:
        the row is mutable at edit time."""
        assert finalised_edit_rejection(self._PROJECTED, self._PAID) is None

    def test_finalised_row_blocks_edit(self):
        """A Paid row with no status change is locked; the message names the
        status, the entity, and the revert remedy."""
        message = finalised_edit_rejection(self._PAID, None, context="transaction")
        assert message is not None
        assert "Paid" in message
        assert "transaction" in message
        assert "Revert" in message

    def test_finalised_row_unlocked_by_revert_to_projected(self):
        """Paid -> Projected in one request lifts the lock so a 'revert and
        correct' edit is atomic."""
        assert finalised_edit_rejection(self._PAID, self._PROJECTED) is None

    def test_finalised_row_blocked_when_new_status_also_immutable(self):
        """An immutable -> immutable move does NOT lift the lock -- the row
        stays finalised, so a concurrent amount rewrite is still refused.

        Written over ``Paid -> Settled`` (the archive) until plan step
        **X-am**.  The rule is about ``is_immutable`` on both ends and never
        about which statuses those are, so it is restated over a pair that
        still exists rather than deleted with the one that does not."""
        assert finalised_edit_rejection(self._PAID, self._CANCELLED) is not None

    def test_an_immutable_row_re_submitting_its_own_status_blocks_edit(self):
        """A finalised row re-stating its own status is still locked.

        The identity case, which the popover performs on every Save: the form
        posts the whole row, so an untouched status box arrives as
        ``Cancelled -> Cancelled`` and must not read as a revert."""
        assert finalised_edit_rejection(
            self._CANCELLED, self._CANCELLED,
        ) is not None

    def test_none_current_status_fails_open(self):
        """A missing current status is treated as mutable -- the transition
        guard owns the corrupt-status case, so this fails open."""
        assert finalised_edit_rejection(None, None) is None

    def test_context_label_in_message(self):
        """The context label ("transfer") appears in the rejection message."""
        message = finalised_edit_rejection(self._PAID, None, context="transfer")
        assert message is not None
        assert "transfer" in message
