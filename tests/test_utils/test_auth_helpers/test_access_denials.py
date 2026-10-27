"""Auth helpers tests: the denial each ownership door logs (F-144), another owner's deleted row
probed at the transaction door, and require_owner failing closed.
"""

import logging
from datetime import date
from decimal import Decimal

import pytest
from flask_login import current_user, login_user
from sqlalchemy import text

from app import ref_cache
from app.enums import RoleEnum
from app.extensions import db
from app.models.account import Account
from app.models.ref import TransactionType
from app.models.transaction import Transaction
from app.utils.auth_helpers import (
    get_accessible_transaction,
    get_accessible_transaction_or_deleted,
    get_or_404,
    get_owned_via_parent,
    require_owner,
)
from app.utils.log_events import (
    ACCESS,
    EVT_ACCESS_DENIED_CROSS_USER,
    EVT_ACCESS_DENIED_OWNER_ONLY,
    EVT_RESOURCE_NOT_FOUND,
)
from tests._test_helpers import rhythm_of, one_off_row_of
from tests._test_helpers import record_paydays_across_a_hole
from tests._test_helpers import a_deleted_occurrence
from tests._test_helpers import create_account_of_type

from ._helpers import _AuthHelpersLogCapture


def _the_next_row_takes_id(sequence, row_id):
    """Restart *sequence* so the next row built from it takes *row_id*.

    A denial's ``pk`` read grades a door only while the probed row's id differs from every
    other id the denying branch holds: the requester, the owner it acts for, the row's owner.
    Until the test template started each non-ref table's ids in a range of its own (ruling
    ``balance:R-BAL356``, ``scripts/build_test_template.py``), ids of different kinds
    coincided in the order the fixtures build in: the first transaction a case built and
    ``seed_user`` were both id 1, and ``second_user``'s account and ``second_user`` were both
    id 2, so a door logging one of those user ids as the ``pk`` passed.  The ranges keep them
    apart now; the restart states the probed row's id rather than leaning on them, and every
    caller's *row_id* (9,001) is below every range, so it differs from each user id with the
    ranges or without them.  The builders take no id, hence the restart.  A rollback does not
    undo ``setval``, but the autouse ``db`` fixture re-clones the database before every test,
    so the restart reaches no other case.  Each caller asserts its row took *row_id*, and that
    the ids its denial could name are pairwise distinct.

    Args:
        sequence: The id sequence to restart, schema-qualified
            (``"budget.transactions_id_seq"``).
        row_id: The id the next row built from *sequence* takes.
    """
    db.session.execute(
        text("SELECT setval(CAST(:seq AS regclass), :v, false)"),
        {"seq": sequence, "v": row_id},
    )


class TestAccessDeniedLogging:
    """Audit finding F-144 / commit C-14.

    The ownership helpers must emit structured events on every denial
    branch.  Pre-C-14 they returned ``None`` silently, so a probing
    companion or a cross-user attacker left no application-tier
    trace.
    """

    def test_get_or_404_emits_resource_not_found_for_missing_pk(
        self, app, db, seed_user,
    ):
        """A missing-PK denial emits ``resource_not_found`` at INFO level."""
        with app.test_request_context("/some/path"):
            login_user(seed_user["user"])
            with _AuthHelpersLogCapture() as cap:
                result = get_or_404(Account, 999_999)

        assert result is None
        record = cap.find(EVT_RESOURCE_NOT_FOUND)
        assert record is not None, (
            "Missing-PK branch did not emit ``resource_not_found``; "
            f"records observed: "
            f"{[(r.levelname, getattr(r, 'event', None)) for r in cap.records]}"
        )
        assert record.levelno == logging.INFO
        assert record.category == ACCESS
        assert record.user_id == seed_user["user"].id
        assert record.model == "Account"
        assert record.pk == 999_999
        assert record.path == "/some/path"

    def test_get_or_404_emits_cross_user_for_other_users_record(
        self, app, db, seed_user, second_user,
    ):
        """A cross-user denial emits ``access_denied_cross_user`` at WARNING level.

        This is the IDOR signal SOC tooling alerts on, so the level
        is WARNING (not INFO like the missing-PK case).
        """
        with app.test_request_context("/some/path"):
            # The probed account is built here, after a stated sequence restart
            # (``_the_next_row_takes_id`` says why), not taken from ``second_user``: the
            # fixture's account and its owner were both id 2 until the template's id ranges
            # (ruling ``balance:R-BAL356``).
            probed_account_id = 9_001
            _the_next_row_takes_id("budget.accounts_id_seq", probed_account_id)
            theirs = create_account_of_type(
                second_user, db.session, "Checking", "Probed Checking",
            )
            requester = seed_user["user"]
            ids = {
                "the probed account": theirs.id,
                "its owner": theirs.user_id,
                "the requester": requester.id,
            }
            assert theirs.id == probed_account_id, f"The sequence restart did not take: {ids}"
            assert len(set(ids.values())) == len(ids), (
                f"The ids the denial could name are not pairwise distinct: {ids}"
            )
            login_user(requester)
            with _AuthHelpersLogCapture() as cap:
                result = get_or_404(Account, theirs.id)

        assert result is None
        record = cap.find(EVT_ACCESS_DENIED_CROSS_USER)
        assert record is not None, (
            "Cross-user branch did not emit ``access_denied_cross_user``; "
            f"records observed: "
            f"{[(r.levelname, getattr(r, 'event', None)) for r in cap.records]}"
        )
        assert record.levelno == logging.WARNING
        assert record.category == ACCESS
        assert record.user_id == seed_user["user"].id
        assert record.model == "Account"
        assert record.pk == theirs.id
        assert record.owner_id == second_user["user"].id
        assert record.path == "/some/path"

    def test_get_or_404_silent_on_success(self, app, db, seed_user):
        """A successful load emits NO ACCESS event.

        Otherwise every page load would write multiple records and
        drown the failure signal.  Only the per-request summary in
        logging_config covers the success path.
        """
        with app.test_request_context("/some/path"):
            login_user(seed_user["user"])
            with _AuthHelpersLogCapture() as cap:
                result = get_or_404(Account, seed_user["account"].id)

        assert result is not None
        access_records = [
            r for r in cap.records if getattr(r, "category", None) == ACCESS
        ]
        assert not access_records, (
            "Successful get_or_404 emitted ACCESS events; the helpers "
            "must stay silent on the happy path.  Records: "
            f"{[(r.levelname, getattr(r, 'event', None)) for r in cap.records]}"
        )

    def test_get_owned_via_parent_emits_resource_not_found_for_missing_pk(
        self, app, db, seed_user, seed_periods,
    ):
        """A missing-PK denial through the parent path emits ``resource_not_found``."""
        with app.test_request_context("/some/path"):
            login_user(seed_user["user"])
            with _AuthHelpersLogCapture() as cap:
                result = get_owned_via_parent(Transaction, 999_999, "pay_period")

        assert result is None
        record = cap.find(EVT_RESOURCE_NOT_FOUND)
        assert record is not None
        assert record.levelno == logging.INFO
        assert record.model == "Transaction"
        assert record.pk == 999_999

    def test_get_owned_via_parent_emits_cross_user_for_other_users_child(
        self, app, db, seed_user, second_user, seed_periods,
    ):
        """A cross-user denial through the parent path emits ``access_denied_cross_user``."""
        # Build a transaction owned by the second user.
        periods2 = record_paydays_across_a_hole(
            user_id=second_user["user"].id,
            first_payday=date(2026, 3, 1),
            num_periods=2,
            rhythm=rhythm_of(14),
        )
        db.session.flush()

        expense_type = db.session.query(TransactionType).filter_by(
            name="Expense"
        ).one()
        txn2 = one_off_row_of(
            periods2[0],
            name="Other User Expense",
            amount=Decimal("99.00"),
            user_id=periods2[0].user_id,
            account_id=second_user["account"].id,
            scenario_id=second_user["scenario"].id,
            transaction_type_id=expense_type.id,
            category_id=second_user["categories"]["Rent"].id,
        )
        db.session.flush()

        with app.test_request_context("/some/path"):
            login_user(seed_user["user"])
            with _AuthHelpersLogCapture() as cap:
                result = get_owned_via_parent(
                    Transaction, txn2.id, "pay_period",
                )

        assert result is None
        record = cap.find(EVT_ACCESS_DENIED_CROSS_USER)
        assert record is not None
        assert record.levelno == logging.WARNING
        assert record.user_id == seed_user["user"].id
        assert record.owner_id == second_user["user"].id
        assert record.parent_attr == "pay_period"

    def test_require_owner_emits_access_denied_for_companion(
        self, app, db, seed_user,
    ):
        """A non-owner hitting an owner-only route emits ``access_denied_owner_only``.

        The decorator must abort(404) AFTER the log_event call so the
        forensic event lands even though the response shape mirrors
        a true 404.
        """
        # Build a companion user attached to the seed user.
        from app.models.user import User  # noqa: WPS433
        companion_role_id = ref_cache.role_id(RoleEnum.COMPANION)
        companion = User(
            email="companion-access-test@example.com",
            password_hash="$2b$12$dummyhashfortestpurposesonlyx",
            display_name="Test Companion",
            role_id=companion_role_id,
            linked_owner_id=seed_user["user"].id,
        )
        db.session.add(companion)
        db.session.flush()

        # Define a fake decorated view in isolation so the test does
        # not exercise an unrelated production route.
        @require_owner
        def owner_only_view():
            """Stub view that should never run for a companion."""
            return "should-not-reach"

        with app.test_request_context("/owner-only", method="POST"):
            login_user(companion)
            with _AuthHelpersLogCapture() as cap:
                # require_owner aborts via abort(404) -- exercise that.
                from werkzeug.exceptions import NotFound  # noqa: WPS433
                with pytest.raises(NotFound):
                    owner_only_view()

        record = cap.find(EVT_ACCESS_DENIED_OWNER_ONLY)
        assert record is not None, (
            "Companion access did not emit ``access_denied_owner_only``; "
            f"records observed: "
            f"{[(r.levelname, getattr(r, 'event', None)) for r in cap.records]}"
        )
        assert record.levelno == logging.WARNING
        assert record.category == ACCESS
        assert record.user_id == companion.id
        assert record.role_id == companion_role_id
        assert record.path == "/owner-only"
        assert record.method == "POST"

    def test_require_owner_silent_for_owner(self, app, db, seed_user):
        """An owner hitting an owner-only route emits NO ACCESS event."""
        @require_owner
        def owner_only_view():
            """Stub view returning a sentinel string."""
            return "owner-ok"

        with app.test_request_context("/owner-only"):
            login_user(seed_user["user"])
            with _AuthHelpersLogCapture() as cap:
                result = owner_only_view()

        assert result == "owner-ok"
        access_records = [
            r for r in cap.records if getattr(r, "category", None) == ACCESS
        ]
        assert not access_records, (
            "Successful require_owner emitted ACCESS events; the "
            "decorator must stay silent on the happy path."
        )

    # ── get_accessible_transaction F-144 logging (deep-hunt #85) ──────

    @staticmethod
    def _make_owned_txn(db, owner, period):
        """Create a one-off, non-companion-visible expense owned by *owner*.

        A rule-less definition's placed row (plan step balance:X-bi-7c), so
        ``visible_to_companion`` reads the DEFINITION's ``companion_visible``,
        which the producer leaves False -- a companion of the owner is
        therefore denied.
        """
        expense_type = (
            db.session.query(TransactionType).filter_by(name="Expense").one()
        )
        txn = one_off_row_of(
            period,
            name="Access-log test expense",
            amount=Decimal("50.00"),
            user_id=period.user_id,
            account_id=owner["account"].id,
            scenario_id=owner["scenario"].id,
            transaction_type_id=expense_type.id,
            category_id=next(iter(owner["categories"].values())).id,
        )
        db.session.flush()
        return txn

    def test_get_accessible_transaction_emits_resource_not_found(
        self, app, db, seed_user,
    ):
        """A missing-PK denial emits ``resource_not_found`` at INFO (deep-hunt #85)."""
        with app.test_request_context("/some/path"):
            login_user(seed_user["user"])
            with _AuthHelpersLogCapture() as cap:
                result = get_accessible_transaction(999_999)

        assert result is None
        record = cap.find(EVT_RESOURCE_NOT_FOUND)
        assert record is not None, (
            "Missing-PK branch did not emit ``resource_not_found``; "
            f"records: {[(r.levelname, getattr(r, 'event', None)) for r in cap.records]}"
        )
        assert record.levelno == logging.INFO
        assert record.category == ACCESS
        assert record.user_id == seed_user["user"].id
        assert record.model == "Transaction"
        assert record.pk == 999_999
        assert record.path == "/some/path"

    def test_get_accessible_transaction_emits_cross_user_for_other_owner(
        self, app, db, seed_user, second_user,
    ):
        """Owner A reaching owner B's transaction emits ``access_denied_cross_user``.

        deep-hunt #85: the owner-path denial was silent before.  WARNING
        level matches the sibling get_or_404 cross-user event.
        """
        with app.test_request_context("/some/path"):
            login_user(seed_user["user"])
            periods2 = record_paydays_across_a_hole(
                user_id=second_user["user"].id,
                first_payday=date(2026, 3, 1),
                num_periods=2,
                rhythm=rhythm_of(14),
            )
            db.session.flush()
            probed_row_id = 9_001
            _the_next_row_takes_id("budget.transactions_id_seq", probed_row_id)
            txn = self._make_owned_txn(db, second_user, periods2[0])
            requester = seed_user["user"]
            # Precondition: the requester is an OWNER (its ``role_id``, the one read the
            # door branches on), so the owner branch refuses the row, and an owner acts on
            # its own data (``User.data_owner_id``, the door's ``acts_on``), so the owner it
            # acts for IS the requester and the ids below hold that id once.
            assert requester.role_id != ref_cache.role_id(RoleEnum.COMPANION)
            assert requester.data_owner_id == requester.id
            ids = {
                "the probed row": txn.id,
                "the row's owner": txn.user_id,
                "the requester, the owner it acts for": requester.id,
            }
            assert txn.id == probed_row_id, f"The sequence restart did not take: {ids}"
            assert len(set(ids.values())) == len(ids), (
                f"The ids the denial could name are not pairwise distinct: {ids}"
            )

            with _AuthHelpersLogCapture() as cap:
                result = get_accessible_transaction(txn.id)

        assert result is None
        record = cap.find(EVT_ACCESS_DENIED_CROSS_USER)
        assert record is not None, (
            "Owner cross-user branch did not emit ``access_denied_cross_user``; "
            f"records: {[(r.levelname, getattr(r, 'event', None)) for r in cap.records]}"
        )
        assert record.levelno == logging.WARNING
        assert record.category == ACCESS
        assert record.user_id == seed_user["user"].id
        assert record.model == "Transaction"
        assert record.pk == txn.id
        assert record.owner_id == second_user["user"].id
        assert record.path == "/some/path"

    def test_get_accessible_transaction_emits_cross_user_for_companion_denied(
        self, app, db, seed_user, seed_periods, seed_companion,
    ):
        """A companion denied by visibility emits ``access_denied_cross_user``.

        deep-hunt #85 headline: the companion-visibility denial was the
        one ownership-failure branch in auth_helpers that stayed silent.
        The companion is linked to seed_user (owner_id == linked_owner_id),
        so this isolates the ``not visible_to_companion`` denial.
        """
        with app.test_request_context("/some/path"):
            probed_row_id = 9_001
            _the_next_row_takes_id("budget.transactions_id_seq", probed_row_id)
            # Ad-hoc txn owned by seed_user -> companion_visible False.
            txn = self._make_owned_txn(db, seed_user, seed_periods[0])
            assert txn.visible_to_companion is False
            companion = seed_companion["user"]
            # Precondition: the requester is a COMPANION (its ``role_id``, the one read the
            # door branches on) acting for the row's own owner (``User.data_owner_id``, the
            # door's ``acts_on``), so only the visibility arm refuses the row, and the owner
            # it acts for IS the row's owner: the ids below hold that id once.
            assert companion.role_id == ref_cache.role_id(RoleEnum.COMPANION)
            assert companion.data_owner_id == txn.user_id
            ids = {
                "the probed row": txn.id,
                "the row's owner, the owner the companion acts for": txn.user_id,
                "the companion": companion.id,
            }
            assert txn.id == probed_row_id, f"The sequence restart did not take: {ids}"
            assert len(set(ids.values())) == len(ids), (
                f"The ids the denial could name are not pairwise distinct: {ids}"
            )
            login_user(companion)
            with _AuthHelpersLogCapture() as cap:
                result = get_accessible_transaction(txn.id)

        assert result is None
        record = cap.find(EVT_ACCESS_DENIED_CROSS_USER)
        assert record is not None, (
            "Companion-visibility branch did not emit "
            "``access_denied_cross_user``; "
            f"records: {[(r.levelname, getattr(r, 'event', None)) for r in cap.records]}"
        )
        assert record.levelno == logging.WARNING
        assert record.category == ACCESS
        assert record.user_id == seed_companion["user"].id
        assert record.model == "Transaction"
        assert record.pk == txn.id
        # owner_id is the txn's actual owner (== the companion's linked owner).
        assert record.owner_id == seed_user["user"].id
        assert record.path == "/some/path"

    def test_get_accessible_transaction_silent_on_success(
        self, app, db, seed_user, seed_periods,
    ):
        """A successful owner load emits NO ACCESS event (deep-hunt #85)."""
        with app.test_request_context("/some/path"):
            login_user(seed_user["user"])
            txn = self._make_owned_txn(db, seed_user, seed_periods[0])
            with _AuthHelpersLogCapture() as cap:
                result = get_accessible_transaction(txn.id)

        assert result is not None
        assert result.id == txn.id
        access_records = [
            r for r in cap.records if getattr(r, "category", None) == ACCESS
        ]
        assert not access_records, (
            "Successful get_accessible_transaction emitted ACCESS events; "
            "the helper must stay silent on the happy path.  Records: "
            f"{[(r.levelname, getattr(r, 'event', None)) for r in cap.records]}"
        )

    def test_get_accessible_transaction_denies_an_anonymous_principal(
        self, app, db, seed_user, seed_periods,
    ):
        """No session: the door answers ``None`` and logs the probe, never raising.

        Plan step ``balance:X-bn`` reads both access branches off ONE
        ``User.data_owner_id``, through a ``getattr`` whose only purpose is
        this principal: Flask-Login's ``AnonymousUserMixin`` has no such
        attribute and reads ``None``, and ``transactions.user_id`` is NOT
        NULL, so no row's owner equals it.  Reachable only where the login
        gate is off, which is where a second layer must hold.
        ``current_user`` is asserted anonymous BEFORE the call, for the reason
        :class:`TestRequireOwnerFailsClosed` gives: a login earlier in the
        same app context would leave the case measuring an owner.
        """
        with app.test_request_context("/some/path"):
            txn = self._make_owned_txn(db, seed_user, seed_periods[0])
            assert not current_user.is_authenticated
            with _AuthHelpersLogCapture() as cap:
                result = get_accessible_transaction(txn.id)

        assert result is None
        record = cap.find(EVT_ACCESS_DENIED_CROSS_USER)
        assert record is not None, (
            "An anonymous principal's denial did not emit "
            "``access_denied_cross_user``; "
            f"records: {[(r.levelname, getattr(r, 'event', None)) for r in cap.records]}"
        )
        assert record.levelno == logging.WARNING
        assert record.user_id is None
        assert record.pk == txn.id
        assert record.owner_id == seed_user["user"].id


# ``TestATransferShadowRowAtTheTransactionDoor`` stood here until plan step
# balance:X-bi-6-4d-2 (ruling **R-BAL300**, developer 2026-10-15: "Delete all
# 27").  Its two cases pinned WHERE plan step X-bi-6-1's shadow fence
# (``is_transfer_shadow``) sits in ``get_accessible_transaction``: after both
# access branches, so the owner's own shadow logged ``resource_not_found`` at
# INFO with its transfer named, and a stranger's probe of one logged
# ``access_denied_cross_user`` at WARNING, first.  No shadow can exist now:
# no transaction has a transfer column since plan step X-bi-6-5, and
# ``ck_transactions_names_no_transfer`` refused a row naming one until then
# (ruling R-BAL258, pinned once, below that drop, by
# ``TestTheUpgrade::test_the_database_refuses_a_twin`` in
# ``tests/test_models/test_a_transfer_is_one_row/test_the_upgrade.py``).
# The fence was reachable by no row, and it went at that step.  The order its
# stranger's case graded is graded below, on the row the shared predicate can
# still refuse: a DELETED one, probed by a stranger and by a companion.


class TestAProbeOfAnotherOwnersDeletedRowIsADenial:
    """Deleted-row probes, one case per denial arm: owner, companion visibility, companion owner.

    Each case names another owner's DELETED row, and a different arm of
    ``get_accessible_transaction_or_deleted`` refuses it: the owner check refuses a stranger
    (``owner_id != acts_on``, ``test_a_strangers_probe``); the companion check refuses the
    owner's companion a HIDDEN row (``not txn.visible_to_companion``,
    ``test_a_companions_probe``) and another household's companion a companion-VISIBLE row
    (its ``owner_id != acts_on``, ``test_a_companions_probe_of_another_household``).
    The door asks the not-found predicate
    (``is_not_found_to_transaction_doors``) AFTER both access branches, and its
    comment says why: every such probe is an access denial first (``access_denied_cross_user``
    at WARNING, the F-144 contract) and "not found" second, so the predicate never runs
    and logs nothing.  Measured at plan step ``balance:X-bi-6-4d-2`` (ruling **R-BAL300**):
    evaluating the predicate ahead of the access branches, every return unchanged, logged
    the row as ``resource_not_found`` before the denial and failed no test that captures a
    log, for either requester (for the companion, at the deletion's parent too).  These
    cases are that grade.  Moving the whole block ahead also hands the requester the row's
    NAME: every case fails on that too, and ``TestNothingLeaksThroughTheName``
    (``tests/test_routes/test_cc5_4a4_hidden_row_doors.py``) grades the name at every
    surface.
    """

    @staticmethod
    def _probe(requester, row):
        """*requester* asks the door for *row*: ``(its answer, the auth_helpers capture)``."""
        login_user(requester)
        with _AuthHelpersLogCapture() as cap:
            answer = get_accessible_transaction_or_deleted(row.id)
        return answer, cap

    @staticmethod
    def _is_the_denial_alone(requester, answer, cap):
        """Assert the door answered None, denied *requester* once at WARNING, logged no not-found.

        ``None`` is also the no-name assertion: a HiddenRow is the only refusal that carries a
        row's name.  The denial's ``user_id`` tells the requester from an anonymous principal,
        which a login that silently failed would leave probing, and which logs the same records.
        Returns the denial, for a case that reads more of it.
        """
        seen = [(r.levelname, getattr(r, "event", None)) for r in cap.records]
        assert answer is None, (
            f"The requester was answered {answer!r}, not None; records: {seen}"
        )
        denials = [
            r for r in cap.records
            if getattr(r, "event", None) == EVT_ACCESS_DENIED_CROSS_USER
        ]
        assert [r.levelno for r in denials] == [logging.WARNING], (
            f"Expected exactly one WARNING access denial; records: {seen}"
        )
        assert denials[0].user_id == requester.id, (
            f"The denial names user {denials[0].user_id!r}, not the requester "
            f"{requester.id}; records: {seen}"
        )
        assert cap.find(EVT_RESOURCE_NOT_FOUND) is None, (
            f"The requester's probe logged the row as not found; records: {seen}"
        )
        return denials[0]

    def test_a_strangers_probe(self, app, seed_user, seed_second_user):
        """seed_user names another user's deleted $45.00 Pool: one WARNING, no "not found"."""
        with app.test_request_context("/some/path"):
            theirs = a_deleted_occurrence(
                db.session, seed_second_user, seed_second_user["bootstrap_period"],
                amount="45.00", name="Pool",
            )
            # Precondition, read back after the builder's commit: a DELETED
            # row of another user's, and a requester that is not a companion
            # (its ``role_id``, the one read the door branches on; the owner
            # check is the other branch), so the probe below is a stranger's.
            assert theirs.is_deleted is True
            assert theirs.user_id == seed_second_user["user"].id
            assert theirs.user_id != seed_user["user"].id
            assert seed_user["user"].role_id != ref_cache.role_id(RoleEnum.COMPANION)
            answer, cap = self._probe(seed_user["user"], theirs)

        self._is_the_denial_alone(seed_user["user"], answer, cap)

    def test_a_companions_probe(self, app, seed_user, seed_companion):
        """The owner's deleted $70.00 Massage, never shown to the companion: the same denial."""
        with app.test_request_context("/some/path"):
            row = a_deleted_occurrence(
                db.session, seed_user, seed_user["bootstrap_period"],
                amount="70.00", name="Massage", companion_visible=False,
            )
            companion = seed_companion["user"]
            # Precondition, read back after the builder's commit: the requester
            # is the owner's COMPANION (not the owner, yet acting on the owner's
            # data, ``User.data_owner_id``), and the row is hidden from it and DELETED.
            assert companion.id != row.user_id
            assert companion.data_owner_id == row.user_id == seed_user["user"].id
            assert row.visible_to_companion is False
            assert row.is_deleted is True
            answer, cap = self._probe(companion, row)

        self._is_the_denial_alone(companion, answer, cap)

    def test_a_companions_probe_of_another_household(
        self, app, seed_user, seed_second_user, seed_companion,
    ):
        """seed_user's companion names another owner's deleted $45.00 Pool, shown to companions."""
        with app.test_request_context("/some/path"):
            # The probed row's id is set by a stated sequence restart, not left to the
            # order the fixtures build in (``_the_next_row_takes_id`` says why): until the
            # template's id ranges (ruling ``balance:R-BAL356``), the row and the owner this
            # companion acts for were both id 1.
            probed_row_id = 9_001
            _the_next_row_takes_id("budget.transactions_id_seq", probed_row_id)
            theirs = a_deleted_occurrence(
                db.session, seed_second_user, seed_second_user["bootstrap_period"],
                amount="45.00", name="Pool", companion_visible=True,
            )
            companion = seed_companion["user"]
            # Precondition, read back after the builder's commit: the requester is a
            # COMPANION (its ``role_id``, the one read the door branches on), the row is
            # companion-VISIBLE and DELETED, and its owner is not the owner this
            # companion acts for (``User.data_owner_id``), so only the companion check's
            # owner arm refuses it.
            assert companion.role_id == ref_cache.role_id(RoleEnum.COMPANION)
            assert companion.data_owner_id == seed_user["user"].id
            assert companion.data_owner_id != theirs.user_id
            assert theirs.visible_to_companion is True
            assert theirs.is_deleted is True
            # And the restart took: the row holds the id it set, apart from all three users'.
            ids = {
                "the probed row": theirs.id,
                "the row's owner": theirs.user_id,
                "the owner the companion acts for": companion.data_owner_id,
                "the companion": companion.id,
            }
            assert theirs.id == probed_row_id, f"The sequence restart did not take: {ids}"
            assert len(set(ids.values())) == len(ids), (
                f"The ids the denial could name are not pairwise distinct: {ids}"
            )
            answer, cap = self._probe(companion, theirs)

        denial = self._is_the_denial_alone(companion, answer, cap)
        # The F-144 trail names the household whose row was probed, and the row: the
        # owner the denial logs is the row's, not the one this companion acts for.
        assert denial.owner_id == theirs.user_id, (
            f"The denial names owner {denial.owner_id!r}, not the probed row's owner "
            f"{theirs.user_id}"
        )
        assert denial.pk == theirs.id, (
            f"The denial names row {denial.pk!r}, not the probed row {theirs.id}"
        )


class TestRequireOwnerFailsClosed:
    """Plan step ``bank_import:X-gs`` (ledger row **BI-486**).

    ``require_owner`` read ``getattr(current_user, "role_id", owner_id)``,
    so a principal with no ``role_id`` at all -- Flask-Login's
    ``AnonymousUserMixin`` -- was defaulted to the OWNER's id and ran the
    view.  Unreachable behind the login gate, which bounces every
    anonymous request first; reachable exactly where the gate is off
    (``LOGIN_DISABLED``, or an app built without it), which is where a
    second layer is supposed to hold.  The read is outright now, and this
    case is the one the owner-id default admitted.
    """

    def test_an_anonymous_principal_never_reaches_the_view(self, app):
        """No session, so no ``role_id``: the wrapped view does not run.

        A direct call on a stub view, so the case grades the decorator and
        not the gate: through the test client the gate answers first and the
        decorator is never consulted.  ``current_user`` is asserted anonymous
        BEFORE the call because Flask-Login caches the loaded user on ``g``
        for the life of the app context the autouse ``db`` fixture holds; a login
        earlier in the same context would leave this case measuring an owner
        and passing for the wrong reason.
        """
        reached = []

        @require_owner
        def owner_only_view():
            """Stub view that must never run without a session."""
            reached.append(True)
            return "should-not-reach"

        with app.test_request_context("/owner-only"):
            assert not current_user.is_authenticated
            with pytest.raises(AttributeError, match="role_id"):
                owner_only_view()

        assert not reached
