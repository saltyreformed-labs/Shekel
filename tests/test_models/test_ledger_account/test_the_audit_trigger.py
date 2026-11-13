"""ledger_accounts is registered for audit, and its trigger fires."""

from __future__ import annotations

from decimal import Decimal

from app.audit_infrastructure import AUDITED_TABLES
from app.extensions import db as _db
from tests._stored_ledger import chart_row_or_plant
from tests._test_helpers import create_account_of_type


class TestAuditTableRegistration:
    """``ledger_accounts`` is audited and its trigger fires.

    Per the coding standard "Every new table in auth, budget, or salary
    MUST be added to AUDITED_TABLES."  ``EXPECTED_TRIGGER_COUNT =
    len(AUDITED_TABLES)`` drives the entrypoint health check, so a missing
    entry would also fail the container start gate.
    """

    def test_table_registered(self):
        """Static check: ('budget', 'ledger_accounts') is in the list."""
        assert ("budget", "ledger_accounts") in AUDITED_TABLES

    def test_audit_trigger_attached_in_db(self, db):
        """Live check: the named trigger exists on the table."""
        count = _db.session.execute(_db.text(
            "SELECT count(*) FROM pg_trigger t "
            " JOIN pg_class c ON c.oid = t.tgrelid "
            " JOIN pg_namespace n ON n.oid = c.relnamespace "
            "WHERE t.tgname = 'audit_ledger_accounts' "
            "  AND n.nspname = 'budget' "
            "  AND c.relname = 'ledger_accounts' "
            "  AND NOT t.tgisinternal"
        )).scalar()
        assert count == 1, (
            "audit_ledger_accounts trigger missing -- the entrypoint "
            "trigger-count health check would refuse to start the container."
        )

    def test_audit_log_captures_inserts(self, app, db, seed_user):
        """Creating a ledger account materialises an INSERT audit row.

        Arithmetic: one planted linked row -> one ledger_accounts INSERT ->
        exactly one audit_log row tagged table_schema='budget',
        table_name='ledger_accounts', operation='INSERT' (creating the
        account itself inserts no chart row since plan task plan#3133).  A
        trigger pointed at the wrong function would silently no-op, so the
        count delta proves the trail is intact.
        """
        with app.app_context():
            baseline = _db.session.execute(_db.text(
                "SELECT count(*) FROM system.audit_log "
                " WHERE table_schema = 'budget' "
                "   AND table_name = 'ledger_accounts' "
                "   AND operation = 'INSERT'"
            )).scalar()

            account = create_account_of_type(
                seed_user, _db.session, "Checking", "Audited Account",
                anchor_balance=Decimal("0.00"),
            )
            chart_row_or_plant(_db.session, account, "linked", "Asset")

            after = _db.session.execute(_db.text(
                "SELECT count(*) FROM system.audit_log "
                " WHERE table_schema = 'budget' "
                "   AND table_name = 'ledger_accounts' "
                "   AND operation = 'INSERT'"
            )).scalar()
            assert after - baseline == 1, (
                "audit_ledger_accounts trigger did not materialise an "
                "INSERT row -- forensic trail is broken for this table."
            )
