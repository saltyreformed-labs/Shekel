"""Tests for the CRIT-02 / Commit 12 LoanAnchorEvent model (E-18).

The event table is the storage half of the event-derived loan
resolver introduced in Commit 13: every loan account carries an
``origination`` event materialised from immutable
:class:`LoanParams` fields, and may carry additional ``user_trueup``
events appended by the dashboard balance-edit flow (Commit 16).

This module exercises three layers of the model contract:

  1. **Schema shape** -- NOT NULL columns, the ``anchor_balance >= 0``
     CHECK constraint, FK CASCADE to ``budget.accounts``, FK RESTRICT
     to ``ref.loan_anchor_sources``, and the DELIBERATE ABSENCE of a
     uniqueness guard (ruling **R-EQ**, plan step X-f1c4b): a literal
     duplicate is refused at the write door, which can compare against what
     governs, and not by an index over the row's own values, which cannot
     tell a retry from a correction.

  2. **Append-only programmatic enforcement** -- ORM-mediated UPDATE
     or DELETE raises :class:`LoanAnchorEventImmutableError` rather
     than silently mutating the forensic record.

  3. **Audit + ref registration** -- the table appears in
     ``AUDITED_TABLES`` and the new ref enum members resolve via
     ``ref_cache.loan_anchor_source_id``.
"""
# pylint: disable=redefined-outer-name
# Rationale: ``redefined-outer-name`` is the canonical pytest
# fixture pattern; bodies bind fixtures by name.
from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy.exc import IntegrityError

from app import ref_cache
from app.audit_infrastructure import AUDITED_TABLES
from app.enums import InterestAccrualConventionEnum, LoanAnchorSourceEnum
from app.extensions import db as _db
from app.models.account import Account
from app.models.loan_anchor_event import (
    LoanAnchorEvent,
    LoanAnchorEventImmutableError,
)
from app.models.loan_params import LoanParams
from app.models.ref import AccountType, LoanAnchorSource
from app.services import account_service
from tests._test_helpers import insert_origination_rate


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _create_loan_account(seed_user, db_session, *,
                         original_principal=Decimal("250000.00"),
                         anchor_balance=Decimal("200000.00"),
                         rate=Decimal("0.06500"),
                         term_months=360,
                         origination_date=date(2024, 1, 1),
                         payment_day=1,
                         is_arm=False,
                         name="Test Mortgage"):
    """Create a loan account + LoanParams for the seed_user.

    Used by every test in this module so the loan-shaped scaffolding
    is consistent.  ``account_service.create_account`` writes the
    Account + origination AccountAnchorHistory (E-19); this helper
    layers the LoanParams row on top.

    Deliberately NOT routed through ``_test_helpers.create_loan_account``, unlike
    every other loan builder in the suite.  These are MODEL tests (the append-only
    ``before_update`` / ``before_delete`` listeners and the same-day unique
    index).  They never read a balance through the ``balance_at`` seam, so what
    the factory sets up for a balance read is irrelevant to them -- they assert
    on table rows, not on money.  (A second reason, a ``current_principal``
    DIFFERING from ``original_principal``, died with that column at plan step
    R20; the account's cash anchor still differs, as a decoy.)

    Keep it hand-rolled.  It is not an oversight.
    """
    loan_type = db_session.query(AccountType).filter_by(name="Mortgage").one()
    account = account_service.create_account(
        account_service.AccountSpec(
            user_id=seed_user["user"].id,
            account_type_id=loan_type.id,
            name=name,
            anchor_balance=anchor_balance,
        ),
    )
    db_session.flush()
    params = LoanParams(
        account_id=account.id,
        original_principal=original_principal,
        interest_accrual_convention_id=ref_cache.interest_accrual_convention_id(
            InterestAccrualConventionEnum.MONTHLY_1_12,
        ),
        term_months=term_months,
        origination_date=origination_date,
        payment_day=payment_day,
        is_arm=is_arm,
    )
    db_session.add(params)
    db_session.flush()
    # DH-#56: the loan's rate now lives in an origination RateHistory
    # row (effective at origination_date), not the dropped
    # LoanParams.interest_rate column.
    insert_origination_rate(params, rate)
    db_session.commit()
    return account, params


def _origination_source_id(db_session):
    """Look up the integer ID of the ``origination`` source row."""
    return db_session.query(LoanAnchorSource).filter_by(
        name="origination",
    ).one().id


# ---------------------------------------------------------------------------
# C12-1: schema shape
# ---------------------------------------------------------------------------


class TestSchemaShape:
    """C12-1: NOT NULL columns, CHECK >= 0, unique-index dedup."""

    def test_required_columns_are_not_nullable(self, app, db, seed_user):
        """Inserting a row with NULL account_id raises IntegrityError.

        Reaches the storage tier via raw SQL so the model's own
        NOT NULL annotations are not the test surface; the database
        column constraint is.
        """
        with app.app_context():
            with pytest.raises(IntegrityError):
                _db.session.execute(_db.text(
                    "INSERT INTO budget.loan_anchor_events "
                    "  (account_id, anchor_date, anchor_balance, source_id) "
                    "VALUES (NULL, :d, 100.00, :s)"
                ), {
                    "d": date(2024, 1, 1),
                    "s": _origination_source_id(_db.session),
                })
                _db.session.flush()
            _db.session.rollback()

    def test_negative_balance_rejected_by_check_constraint(
        self, app, db, seed_user,
    ):
        """A negative ``anchor_balance`` trips ``ck_loan_anchor_events_balance_nonneg``.

        The model column is Numeric(12,2) so the value coerces
        cleanly; the rejection therefore exercises the named CHECK
        constraint, not a Marshmallow validator.
        """
        with app.app_context():
            account, _ = _create_loan_account(seed_user, _db.session)
            with pytest.raises(IntegrityError):
                _db.session.execute(_db.text(
                    "INSERT INTO budget.loan_anchor_events "
                    "  (account_id, anchor_date, anchor_balance, source_id) "
                    "VALUES (:a, :d, -1.00, :s)"
                ), {
                    "a": account.id,
                    "d": date(2024, 1, 1),
                    "s": _origination_source_id(_db.session),
                })
                _db.session.flush()
            _db.session.rollback()

    def test_zero_balance_is_accepted(self, app, db, seed_user):
        """Zero is a value, not "missing" -- the CHECK permits it.

        Arithmetic: ``CHECK (anchor_balance >= 0)`` admits zero by
        the >= boundary.  E-12 / coding standard "zero is a value":
        a fully paid-off loan asserting a $0 balance is the canonical
        terminal state and must round-trip cleanly.
        """
        with app.app_context():
            account, _ = _create_loan_account(seed_user, _db.session)
            evt = LoanAnchorEvent(
                account_id=account.id,
                anchor_date=date(2024, 1, 1),
                anchor_balance=Decimal("0.00"),
                source_id=_origination_source_id(_db.session),
            )
            _db.session.add(evt)
            _db.session.commit()
            assert evt.id is not None
            assert evt.anchor_balance == Decimal("0.00")

    def test_same_day_literal_duplicate_is_permitted_by_the_storage_tier(
        self, app, db, seed_user,
    ):
        """The table DELIBERATELY admits a literal duplicate (ruling R-EQ).

        **This test asserted the opposite until plan step X-f1c4b**, when
        migration ``a3f6c1d84b90`` dropped
        ``uq_loan_anchor_events_acct_date_bal_day``.  What that guard cost is
        worth stating rather than silently losing: it made a network retry or a
        double-click unable to write two identical rows AT THE STORAGE TIER.

        It could not do that job without also refusing a correction, because a
        retry and a deliberate re-assertion carry identical values -- so the
        rule moved to the write door
        (``loan_anchor_service._append_loan_anchor_and_commit``), which compares the
        submission against the event that GOVERNS and can tell them apart.  The
        door's coverage is
        ``test_anchor_service/test_the_loan_true_up.py``'s
        ``TestApplyLoanAnchorTrueUpUnchanged``.

        Kept as a POSITIVE assertion rather than deleted so the permission is
        recorded as a decision: an ``IntegrityError`` reappearing here would
        mean the index had been re-added behind the door's rule, which is the
        state ``test_anchor_service/test_the_cash_true_up.py``'s
        ``TestApplyAnchorTrueUpModuleContract`` grades against ``pg_index``.
        """
        with app.app_context():
            account, _ = _create_loan_account(seed_user, _db.session)
            source_id = _origination_source_id(_db.session)
            for _ in range(2):
                _db.session.add(LoanAnchorEvent(
                    account_id=account.id,
                    anchor_date=date(2024, 1, 1),
                    anchor_balance=Decimal("250000.00"),
                    source_id=source_id,
                ))
            _db.session.commit()

            rows = (
                _db.session.query(LoanAnchorEvent)
                .filter_by(
                    account_id=account.id,
                    anchor_date=date(2024, 1, 1),
                    anchor_balance=Decimal("250000.00"),
                )
                .all()
            )
            assert len(rows) == 2, (
                f"The storage tier must admit both rows: uniqueness is the "
                f"write door's rule now, not an index's.  Found {len(rows)}."
            )

    def test_same_day_different_balance_permitted(self, app, db, seed_user):
        """Two trueups on the same day with different balances succeed.

        The operator's intended workflow (saw a typo, re-saved with the
        corrected number) is preserved.  Arithmetic: two distinct ``Decimal``
        values -- 250000.00 vs 249000.00 -- on the same ``anchor_date`` and
        same ``account_id``, both insertable and both read back.
        """
        with app.app_context():
            account, _ = _create_loan_account(seed_user, _db.session)
            source_id = _origination_source_id(_db.session)
            today = date(2024, 1, 1)
            _db.session.add(LoanAnchorEvent(
                account_id=account.id,
                anchor_date=today,
                anchor_balance=Decimal("250000.00"),
                source_id=source_id,
            ))
            _db.session.add(LoanAnchorEvent(
                account_id=account.id,
                anchor_date=today,
                anchor_balance=Decimal("249000.00"),
                source_id=source_id,
            ))
            _db.session.commit()
            rows = (
                _db.session.query(LoanAnchorEvent)
                .filter_by(account_id=account.id, anchor_date=today)
                .order_by(LoanAnchorEvent.anchor_balance.desc())
                .all()
            )
            assert len(rows) == 2
            assert rows[0].anchor_balance == Decimal("250000.00")
            assert rows[1].anchor_balance == Decimal("249000.00")

    def test_account_cascade_delete_removes_events(self, app, db, seed_user):
        """Deleting the parent Account cascades to its anchor events.

        The FK declares ``ondelete=CASCADE`` so a future account-
        purge path leaves no orphan events.  Cascade is database-
        level so the ORM ``before_delete`` event guard is NOT
        triggered (that path is for ORM-mediated DELETEs); the
        sibling ``test_orm_delete_blocked`` covers the ORM path.
        """
        with app.app_context():
            account, _ = _create_loan_account(seed_user, _db.session)
            # Capture the PK to a local before any DELETE, since
            # SQLAlchemy will refuse to lazy-reload ``account.id``
            # once the row has been removed under it.
            account_id = account.id
            _db.session.add(LoanAnchorEvent(
                account_id=account_id,
                anchor_date=date(2024, 1, 1),
                anchor_balance=Decimal("250000.00"),
                source_id=_origination_source_id(_db.session),
            ))
            _db.session.commit()
            assert _db.session.query(LoanAnchorEvent).filter_by(
                account_id=account_id,
            ).count() == 1
            # Bypass the ORM so the ORM event listener does not fire;
            # the database CASCADE is what we want to exercise.
            _db.session.expunge(account)
            _db.session.execute(_db.text(
                "DELETE FROM budget.accounts WHERE id = :a"
            ), {"a": account_id})
            _db.session.commit()
            assert _db.session.query(LoanAnchorEvent).filter_by(
                account_id=account_id,
            ).count() == 0


# ---------------------------------------------------------------------------
# C12-7: source FK is ID-based, never name-based
# ---------------------------------------------------------------------------


class TestSourceIsIdBased:
    """C12-7: ``source_id`` is an FK to ref, looked up via the cache.

    Project convention: IDs for logic, strings for display only.
    The ref_cache resolves enum members to integer IDs at startup;
    application code must never compare against ``LoanAnchorSource.name``.
    """

    def test_ref_cache_resolves_enum_to_id(self, app):
        """``loan_anchor_source_id`` returns an integer PK for each member.

        Arithmetic: there are exactly two enum members and exactly
        two seeded rows, so the cache must produce two distinct
        positive integers.
        """
        with app.app_context():
            orig = ref_cache.loan_anchor_source_id(
                LoanAnchorSourceEnum.ORIGINATION,
            )
            trueup = ref_cache.loan_anchor_source_id(
                LoanAnchorSourceEnum.USER_TRUEUP,
            )
            assert isinstance(orig, int) and orig > 0
            assert isinstance(trueup, int) and trueup > 0
            assert orig != trueup

    def test_no_string_name_comparison_in_application_code(self):
        """No application-code path compares against ``source.name``.

        Surface check via grep -- the model itself uses ``source_id``,
        the resolver (Commit 13) will read events by source_id, and
        the trueup flow (Commit 16) will write source_id from the
        cache.  Catching a future regression where a fast hack adds
        a string compare in a non-display path.
        """
        import pathlib
        import re
        offending = []
        scan_root = pathlib.Path(__file__).resolve().parents[2] / "app"
        # Match patterns like ``source.name == "origination"`` or
        # ``LoanAnchorSource.name == "user_trueup"``.  Display-only
        # paths in templates are exempt because templates are listed
        # under ``app/templates`` and shipped via |safe-free renders.
        name_compare = re.compile(
            r"loan_anchor_source[s]?\.name\s*(==|!=)|"
            r"\.name\s*(==|!=)\s*['\"]origination['\"]|"
            r"\.name\s*(==|!=)\s*['\"]user_trueup['\"]"
        )
        for py_file in scan_root.rglob("*.py"):
            text = py_file.read_text()
            if "loan_anchor" not in text and "LoanAnchor" not in text:
                continue
            for line_no, line in enumerate(text.splitlines(), start=1):
                if name_compare.search(line):
                    offending.append(f"{py_file}:{line_no}: {line.strip()}")
        assert not offending, (
            "Loan anchor source compared by string name in application "
            "code -- violates the IDs-for-logic invariant.  Use "
            "ref_cache.loan_anchor_source_id() instead.\n\n  "
            + "\n  ".join(offending)
        )


# ---------------------------------------------------------------------------
# C12-1 (continued): programmatic append-only enforcement
# ---------------------------------------------------------------------------


class TestAppendOnlyEnforcement:
    """C12 gate: the model exposes no UPDATE/DELETE API.

    Programmatic enforcement via SQLAlchemy ``before_update`` /
    ``before_delete`` event listeners.  Catches a future regression
    where a route is added to "fix" an anchor event's balance in
    place rather than appending a corrective trueup row -- which
    would silently destroy the forensic record.
    """

    def test_orm_update_blocked(self, app, db, seed_user):
        """Mutating a flushed LoanAnchorEvent raises before the UPDATE fires.

        Arithmetic: the test loads one event, edits ``anchor_balance``,
        and tries to flush.  The ``before_update`` listener raises
        :class:`LoanAnchorEventImmutableError` BEFORE SQLAlchemy
        emits the SQL, so the row in the database remains unchanged
        and the session rolls back cleanly.
        """
        with app.app_context():
            account, _ = _create_loan_account(seed_user, _db.session)
            evt = LoanAnchorEvent(
                account_id=account.id,
                anchor_date=date(2024, 1, 1),
                anchor_balance=Decimal("250000.00"),
                source_id=_origination_source_id(_db.session),
            )
            _db.session.add(evt)
            _db.session.commit()
            evt_id = evt.id
            assert evt_id is not None

            evt.anchor_balance = Decimal("999999.99")
            with pytest.raises(LoanAnchorEventImmutableError):
                _db.session.flush()
            _db.session.rollback()

            # Verify the DB row is unchanged after rollback.
            stored = (
                _db.session.query(LoanAnchorEvent).filter_by(id=evt_id).one()
            )
            assert stored.anchor_balance == Decimal("250000.00")

    def test_orm_delete_blocked(self, app, db, seed_user):
        """Deleting a loaded LoanAnchorEvent via the ORM is rejected.

        The ``before_delete`` listener raises
        :class:`LoanAnchorEventImmutableError`.  Note that database-
        level CASCADE deletes from ``budget.accounts`` still flow
        through because they happen outside the ORM session (the
        sibling ``test_account_cascade_delete_removes_events`` test
        exercises that path).
        """
        with app.app_context():
            account, _ = _create_loan_account(seed_user, _db.session)
            evt = LoanAnchorEvent(
                account_id=account.id,
                anchor_date=date(2024, 1, 1),
                anchor_balance=Decimal("250000.00"),
                source_id=_origination_source_id(_db.session),
            )
            _db.session.add(evt)
            _db.session.commit()

            _db.session.delete(evt)
            with pytest.raises(LoanAnchorEventImmutableError):
                _db.session.flush()
            _db.session.rollback()

            # Verify the row is still present after rollback.
            assert (
                _db.session.query(LoanAnchorEvent)
                .filter_by(account_id=account.id)
                .count() == 1
            )


# ---------------------------------------------------------------------------
# C12-5: AUDITED_TABLES registration
# ---------------------------------------------------------------------------


class TestAuditTableRegistration:
    """C12-5: ``loan_anchor_events`` is in ``AUDITED_TABLES``.

    The table holds financial state mutations; per the coding
    standard "Every new table in auth, budget, or salary MUST be
    added to AUDITED_TABLES."  The trigger-count health check is
    derived from this constant via ``EXPECTED_TRIGGER_COUNT =
    len(AUDITED_TABLES)``, so a missing entry would also fail the
    entrypoint health gate at container start.
    """

    def test_table_registered(self):
        """Static check: ('budget', 'loan_anchor_events') in the list."""
        assert ("budget", "loan_anchor_events") in AUDITED_TABLES

    def test_audit_trigger_attached_in_db(self, db):
        """Live check: the named trigger exists on the table.

        The migration attaches ``audit_loan_anchor_events`` via the
        same DROP IF EXISTS + CREATE TRIGGER pattern as every other
        audited table.  pg_trigger ought to contain it.
        """
        count = _db.session.execute(_db.text(
            "SELECT count(*) FROM pg_trigger t "
            " JOIN pg_class c ON c.oid = t.tgrelid "
            " JOIN pg_namespace n ON n.oid = c.relnamespace "
            "WHERE t.tgname = 'audit_loan_anchor_events' "
            "  AND n.nspname = 'budget' "
            "  AND c.relname = 'loan_anchor_events' "
            "  AND NOT t.tgisinternal"
        )).scalar()
        assert count == 1, (
            "audit_loan_anchor_events trigger missing -- the entrypoint "
            "trigger-count health check would refuse to start the container."
        )

    def test_audit_log_captures_inserts(self, app, db, seed_user):
        """Inserting an event materialises a row in ``system.audit_log``.

        Arithmetic: one INSERT through the trigger writes exactly
        one audit row tagged with table_schema='budget',
        table_name='loan_anchor_events', operation='INSERT'.
        Verifies the trigger function actually runs against the
        new table -- a syntactically-attached trigger pointed at the
        wrong function would silently no-op.
        """
        with app.app_context():
            # Capture the baseline audit row count for this test's table.
            baseline = _db.session.execute(_db.text(
                "SELECT count(*) FROM system.audit_log "
                " WHERE table_schema = 'budget' "
                "   AND table_name = 'loan_anchor_events'"
            )).scalar()

            account, _ = _create_loan_account(seed_user, _db.session)
            _db.session.add(LoanAnchorEvent(
                account_id=account.id,
                anchor_date=date(2024, 1, 1),
                anchor_balance=Decimal("250000.00"),
                source_id=_origination_source_id(_db.session),
            ))
            _db.session.commit()

            after = _db.session.execute(_db.text(
                "SELECT count(*) FROM system.audit_log "
                " WHERE table_schema = 'budget' "
                "   AND table_name = 'loan_anchor_events' "
                "   AND operation = 'INSERT'"
            )).scalar()
            assert after - baseline == 1, (
                "audit_loan_anchor_events trigger did not materialise an "
                "INSERT row in system.audit_log -- forensic trail is "
                "broken for this table."
            )
