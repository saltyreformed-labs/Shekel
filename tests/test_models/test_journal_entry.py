"""Tests for the JournalEntry + Posting models (Build-Order Step 2, Commit 3;
the ``transaction_id`` source link added Step 3, Commit 2).

``budget.journal_entries`` (event headers) and ``budget.account_postings``
(signed, debit-positive legs) are the append-only double-entry ledger.  These
tests pin the storage-tier and ORM invariants the models and their migration
jointly guarantee:

  * both tables are structurally append-only -- an ORM-mediated UPDATE or
    DELETE raises the model's named immutable error;
  * a posting leg's ``amount`` may not be zero (``ck_account_postings_amount_nonzero``);
  * NOT NULL columns are enforced at the storage tier;
  * ``transfer_id`` (and, Step 3, ``transaction_id``) is SET NULL on a
    source delete (the immutable posted fact survives), while the tenancy
    CASCADE (a deleted pay period) disposes of the entry AND its legs -- a
    database-level cascade that runs outside the ORM and so is NOT blocked
    by the immutability guards;
  * ``posting_kind_id`` / ``source_kind_id`` are RESTRICT (the seeded ref
    rows are non-removable invariants);
  * both tables are registered for auditing and their triggers fire.

The deferred balanced-journal constraint trigger has its own suite
(``test_posting_balanced_trigger.py``); the historical backfill has
``test_posting_ledger_backfill.py``.
"""
# pylint: disable=redefined-outer-name
# Rationale: ``redefined-outer-name`` is the canonical pytest fixture
# pattern; bodies bind fixtures by name.
from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy.exc import IntegrityError

from app import ref_cache
from app.audit_infrastructure import AUDITED_TABLES
from app.enums import PostingKindEnum, PostingSourceEnum
from app.extensions import db as _db
from app.models.journal_entry import (
    JournalEntry,
    JournalEntryImmutableError,
    Posting,
    PostingImmutableError,
)
from app.models.pay_period import PayPeriod
from tests._test_helpers import (
    add_txn,
    create_account_of_type,
    create_settled_transfer,
    ledger_accounts_for_account,
    make_balanced_entry,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _ledger_id(session, account):
    """Return the id of the single linked ledger account for *account*."""
    return ledger_accounts_for_account(session, account.id)[0].id


# The balanced-entry builder now lives in ``tests._test_helpers``
# (``make_balanced_entry``): the R4 ledger role-privilege suite needs the
# identical shape, so the module-local helper was promoted rather than
# duplicated.


@pytest.fixture()
def two_ledgers(app, db, seed_user):  # pylint: disable=unused-argument
    """Return (checking_ledger_id, savings_ledger_id) for two real accounts.

    The seed Checking already carries a ledger account from the Commit-2 sync
    hook; this fixture adds a Savings (also auto-paired) so a balanced
    two-leg entry has two distinct ledger accounts to post into.  Runs in the
    ``db`` fixture's app context (no nested context) and returns plain ints.
    """
    savings = create_account_of_type(
        seed_user, _db.session, "Savings", "Posting Savings",
    )
    _db.session.commit()
    return (
        _ledger_id(_db.session, seed_user["account"]),
        _ledger_id(_db.session, savings),
    )


# ---------------------------------------------------------------------------
# Append-only enforcement (the model's before_update / before_delete guards)
# ---------------------------------------------------------------------------


class TestAppendOnlyEnforcement:
    """ORM UPDATE/DELETE on either table raises the named immutable error.

    Catches a future regression where a route "fixes" a posted entry in
    place rather than appending a reversing entry -- which would silently
    corrupt the forensic ledger.
    """

    def test_journal_entry_orm_update_blocked(self, app, db, seed_user, two_ledgers):
        """Editing a flushed JournalEntry raises before the UPDATE fires."""
        from_ledger, to_ledger = two_ledgers
        with app.app_context():
            entry = make_balanced_entry(
                _db.session, seed_user,
                from_ledger_id=from_ledger, to_ledger_id=to_ledger,
            )
            entry_id = entry.id
            entry.description = "tampered"
            with pytest.raises(JournalEntryImmutableError):
                _db.session.flush()
            _db.session.rollback()
            stored = _db.session.get(JournalEntry, entry_id)
            assert stored.description == "Test entry"

    def test_journal_entry_orm_delete_blocked(self, app, db, seed_user, two_ledgers):
        """Deleting a JournalEntry via the ORM is rejected."""
        from_ledger, to_ledger = two_ledgers
        with app.app_context():
            entry = make_balanced_entry(
                _db.session, seed_user,
                from_ledger_id=from_ledger, to_ledger_id=to_ledger,
            )
            entry_id = entry.id
            _db.session.delete(entry)
            with pytest.raises(JournalEntryImmutableError):
                _db.session.flush()
            _db.session.rollback()
            assert _db.session.get(JournalEntry, entry_id) is not None

    def test_posting_orm_update_blocked(self, app, db, seed_user, two_ledgers):
        """Editing a flushed Posting raises before the UPDATE fires."""
        from_ledger, to_ledger = two_ledgers
        with app.app_context():
            entry = make_balanced_entry(
                _db.session, seed_user,
                from_ledger_id=from_ledger, to_ledger_id=to_ledger,
            )
            posting = (
                _db.session.query(Posting)
                .filter_by(journal_entry_id=entry.id)
                .first()
            )
            posting_id = posting.id
            original = posting.amount
            posting.amount = Decimal("-999.00")
            with pytest.raises(PostingImmutableError):
                _db.session.flush()
            _db.session.rollback()
            stored = _db.session.get(Posting, posting_id)
            assert stored.amount == original

    def test_posting_orm_delete_blocked(self, app, db, seed_user, two_ledgers):
        """Deleting a Posting via the ORM is rejected."""
        from_ledger, to_ledger = two_ledgers
        with app.app_context():
            entry = make_balanced_entry(
                _db.session, seed_user,
                from_ledger_id=from_ledger, to_ledger_id=to_ledger,
            )
            posting = (
                _db.session.query(Posting)
                .filter_by(journal_entry_id=entry.id)
                .first()
            )
            posting_id = posting.id
            _db.session.delete(posting)
            with pytest.raises(PostingImmutableError):
                _db.session.flush()
            _db.session.rollback()
            assert _db.session.get(Posting, posting_id) is not None


# ---------------------------------------------------------------------------
# Schema-tier constraints
# ---------------------------------------------------------------------------


class TestSchemaConstraints:
    """NOT NULL columns and the ``amount <> 0`` CHECK are storage-enforced."""

    def test_zero_posting_amount_rejected(self, app, db, seed_user, two_ledgers):
        """A zero-amount leg trips ``ck_account_postings_amount_nonzero``.

        A zero leg carries no movement and would let a "balanced" entry hide
        a missing leg, so the storage tier refuses it.  Reached via raw SQL
        so the CHECK constraint -- not a Python guard -- is the test surface.
        """
        from_ledger, _to_ledger = two_ledgers
        with app.app_context():
            entry = JournalEntry(
                user_id=seed_user["user"].id,
                scenario_id=seed_user["scenario"].id,
                pay_period_id=seed_user["bootstrap_period"].id,
                entry_date=date(2026, 1, 15),
                source_kind_id=ref_cache.posting_source_id(
                    PostingSourceEnum.TRANSFER,
                ),
                description="zero-amount probe",
            )
            _db.session.add(entry)
            _db.session.flush()
            with pytest.raises(IntegrityError):
                _db.session.execute(_db.text(
                    "INSERT INTO budget.account_postings "
                    "  (journal_entry_id, ledger_account_id, amount, "
                    "   posting_kind_id) "
                    "VALUES (:e, :l, 0, :k)"
                ), {
                    "e": entry.id, "l": from_ledger,
                    "k": ref_cache.posting_kind_id(PostingKindEnum.TRANSFER),
                })
                _db.session.flush()
            _db.session.rollback()

    def test_description_not_nullable(self, app, db, seed_user):
        """A NULL ``description`` trips the column NOT NULL constraint.

        Reaches the storage tier via raw SQL so the database column
        constraint, not the model annotation, is the test surface.
        """
        with app.app_context():
            with pytest.raises(IntegrityError):
                _db.session.execute(_db.text(
                    "INSERT INTO budget.journal_entries "
                    "  (user_id, scenario_id, pay_period_id, entry_date, "
                    "   source_kind_id, description) "
                    "VALUES (:u, :s, :p, :d, :src, NULL)"
                ), {
                    "u": seed_user["user"].id,
                    "s": seed_user["scenario"].id,
                    "p": seed_user["bootstrap_period"].id,
                    "d": date(2026, 1, 15),
                    "src": ref_cache.posting_source_id(
                        PostingSourceEnum.TRANSFER,
                    ),
                })
                _db.session.flush()
            _db.session.rollback()


# ---------------------------------------------------------------------------
# Foreign-key actions
# ---------------------------------------------------------------------------


class TestForeignKeyActions:
    """SET NULL on source delete; CASCADE disposal; RESTRICT on ref rows."""

    def test_transfer_delete_sets_transfer_id_null(
        self, app, db, seed_user, two_ledgers,
    ):
        """Deleting a source transfer SET-NULLs the entry's ``transfer_id``.

        The posted fact is immutable history: a hard-deleted transfer must
        leave its journal entry intact with a NULLed back-link, not cascade
        the entry away.  The transfer's shadows cascade off (their
        ``transfer_id`` FK is CASCADE); the entry survives.
        """
        from_ledger, to_ledger = two_ledgers
        with app.app_context():
            savings = (
                _db.session.query(type(seed_user["account"]))
                .filter_by(name="Posting Savings").one()
            )
            transfer = create_settled_transfer(
                seed_user, _db.session, seed_user["account"], savings,
                seed_user["bootstrap_period"], amount=Decimal("100.00"),
            )
            _db.session.commit()
            entry = make_balanced_entry(
                _db.session, seed_user,
                from_ledger_id=from_ledger, to_ledger_id=to_ledger,
                transfer_id=transfer.id,
            )
            entry_id = entry.id
            transfer_id = transfer.id

            # Delete the transfer at the storage tier (its shadows cascade).
            # Its payments first, in the same raw SQL: a row holding one is no longer
            # deleted with it (R-CC54; rule-5 re-expression, developer-confirmed
            # 2026-09-23).  The payments hang off the TRANSFER by its side keys
            # since plan step balance:X-bi-6-4d-2, which are NO ACTION on
            # delete, so they are found by those links -- they were found under
            # the shadows until then, which hold none now, and the transfer's
            # delete is refused while one survives (ruling R-BAL167 class 2).
            _db.session.execute(_db.text(
                "DELETE FROM budget.transaction_entries "
                "WHERE expense_transfer_id = :t OR income_transfer_id = :t"
            ), {"t": transfer_id})
            _db.session.execute(_db.text(
                "DELETE FROM budget.transfers WHERE id = :t"
            ), {"t": transfer_id})
            _db.session.commit()
            _db.session.expire_all()

            survived = _db.session.get(JournalEntry, entry_id)
            assert survived is not None, (
                "the immutable journal entry must survive a source-transfer "
                "delete"
            )
            assert survived.transfer_id is None, (
                "the entry's transfer back-link must be SET NULL, not cascaded"
            )
            # Both legs survive too.
            assert (
                _db.session.query(Posting)
                .filter_by(journal_entry_id=entry_id)
                .count() == 2
            )

    def test_transaction_delete_sets_transaction_id_null(
        self, app, db, seed_user, two_ledgers,
    ):
        """Deleting a source transaction SET-NULLs the entry's transaction_id.

        The Step-3 analog of the transfer SET NULL above: an ordinary
        transaction's posted journal entry is immutable history and must
        survive a hard delete of its source transaction with only the
        back-link cleared.  The FK SET NULL is a database-level action run
        outside the ORM, so the ``before_update`` immutability guard does not
        fire (the same mechanism the transfer case relies on).
        """
        from_ledger, to_ledger = two_ledgers
        with app.app_context():
            txn = add_txn(
                _db.session, seed_user, seed_user["bootstrap_period"],
                "Posted expense", "50.00", category_key="Groceries",
            )
            _db.session.commit()
            txn_id = txn.id
            entry = make_balanced_entry(
                _db.session, seed_user,
                from_ledger_id=from_ledger, to_ledger_id=to_ledger,
                source_kind=PostingSourceEnum.TRANSACTION,
                transaction_id=txn_id,
                posting_kind=PostingKindEnum.EXPENSE,
            )
            entry_id = entry.id

            # Delete the source transaction at the storage tier.
            _db.session.execute(_db.text(
                "DELETE FROM budget.transactions WHERE id = :t"
            ), {"t": txn_id})
            _db.session.commit()
            _db.session.expire_all()

            survived = _db.session.get(JournalEntry, entry_id)
            assert survived is not None, (
                "the immutable journal entry must survive a source-"
                "transaction delete"
            )
            assert survived.transaction_id is None, (
                "the entry's transaction back-link must be SET NULL, not "
                "cascaded"
            )
            # Both legs survive too.
            assert (
                _db.session.query(Posting)
                .filter_by(journal_entry_id=entry_id)
                .count() == 2
            )

    def test_pay_period_delete_cascades_entry_and_postings(
        self, app, db, seed_user, two_ledgers,
    ):
        """A tenancy CASCADE (period delete) disposes of the entry AND legs.

        The database-level CASCADE runs outside the ORM session, so the
        ``before_delete`` immutability guards on BOTH tables do NOT fire and
        do NOT block it -- this is the documented disposal path.  A fresh,
        unanchored period is used so no account-anchor FK blocks the delete.
        """
        from_ledger, to_ledger = two_ledgers
        with app.app_context():
            fresh_period = PayPeriod(
                user_id=seed_user["user"].id,
                start_date=date(2027, 6, 4),
            )
            _db.session.add(fresh_period)
            _db.session.flush()
            period_id = fresh_period.id
            entry = make_balanced_entry(
                _db.session, seed_user,
                from_ledger_id=from_ledger, to_ledger_id=to_ledger,
                period_id=period_id,
            )
            entry_id = entry.id

            _db.session.execute(_db.text(
                "DELETE FROM budget.pay_periods WHERE id = :p"
            ), {"p": period_id})
            _db.session.commit()
            _db.session.expire_all()

            assert _db.session.get(JournalEntry, entry_id) is None
            assert (
                _db.session.query(Posting)
                .filter_by(journal_entry_id=entry_id)
                .count() == 0
            )

    def test_referenced_source_kind_delete_restricted(self, app, db, seed_user):
        """Deleting a referenced ``ref.posting_sources`` row is refused.

        RESTRICT: a successful delete would strand every entry tagged with
        that source kind.  An entry references the ``transfer`` source; the
        raw DELETE on that row must raise.
        """
        with app.app_context():
            source_id = ref_cache.posting_source_id(PostingSourceEnum.TRANSFER)
            entry = JournalEntry(
                user_id=seed_user["user"].id,
                scenario_id=seed_user["scenario"].id,
                pay_period_id=seed_user["bootstrap_period"].id,
                entry_date=date(2026, 1, 15),
                source_kind_id=source_id,
                description="restrict probe",
            )
            _db.session.add(entry)
            _db.session.flush()
            with pytest.raises(IntegrityError):
                _db.session.execute(_db.text(
                    "DELETE FROM ref.posting_sources WHERE id = :s"
                ), {"s": source_id})
                _db.session.commit()
            _db.session.rollback()

    def test_referenced_posting_kind_delete_restricted(
        self, app, db, seed_user, two_ledgers,
    ):
        """Deleting a referenced ``ref.posting_kinds`` row is refused.

        RESTRICT: a posting leg references the ``transfer`` kind, so the raw
        DELETE on that ref row must raise.
        """
        from_ledger, to_ledger = two_ledgers
        with app.app_context():
            make_balanced_entry(
                _db.session, seed_user,
                from_ledger_id=from_ledger, to_ledger_id=to_ledger,
            )
            kind_id = ref_cache.posting_kind_id(PostingKindEnum.TRANSFER)
            with pytest.raises(IntegrityError):
                _db.session.execute(_db.text(
                    "DELETE FROM ref.posting_kinds WHERE id = :k"
                ), {"k": kind_id})
                _db.session.commit()
            _db.session.rollback()


# ---------------------------------------------------------------------------
# Audit registration
# ---------------------------------------------------------------------------


class TestAuditTableRegistration:
    """Both ledger tables are audited and their triggers fire.

    Per the coding standard "Every new table in auth, budget, or salary MUST
    be added to AUDITED_TABLES."  ``EXPECTED_TRIGGER_COUNT = len(AUDITED_TABLES)``
    drives the entrypoint health check, so a missing entry would also fail
    the container start gate.
    """

    def test_tables_registered(self):
        """Static check: both budget tables are in the audited list."""
        assert ("budget", "journal_entries") in AUDITED_TABLES
        assert ("budget", "account_postings") in AUDITED_TABLES

    def test_audit_triggers_attached_in_db(self, db):
        """Live check: both named audit triggers exist on their tables."""
        count = _db.session.execute(_db.text(
            "SELECT count(*) FROM pg_trigger t "
            " JOIN pg_class c ON c.oid = t.tgrelid "
            " JOIN pg_namespace n ON n.oid = c.relnamespace "
            "WHERE n.nspname = 'budget' "
            "  AND t.tgname IN ('audit_journal_entries', "
            "                   'audit_account_postings') "
            "  AND NOT t.tgisinternal"
        )).scalar()
        assert count == 2, (
            "an audit trigger is missing -- the entrypoint trigger-count "
            "health check would refuse to start the container."
        )

    def test_audit_log_captures_posting_insert(
        self, app, db, seed_user, two_ledgers,
    ):
        """A new entry + legs materialise INSERT audit rows for both tables.

        Arithmetic: one balanced entry writes one ``journal_entries`` INSERT
        and two ``account_postings`` INSERTs, so the audit log gains exactly
        one and two rows respectively.  A trigger pointed at the wrong
        function would silently no-op, so the count deltas prove the trail.
        """
        from_ledger, to_ledger = two_ledgers
        with app.app_context():
            def _count(table):
                return _db.session.execute(_db.text(
                    "SELECT count(*) FROM system.audit_log "
                    " WHERE table_schema = 'budget' "
                    "   AND table_name = :t AND operation = 'INSERT'"
                ), {"t": table}).scalar()

            je_before = _count("journal_entries")
            ap_before = _count("account_postings")

            make_balanced_entry(
                _db.session, seed_user,
                from_ledger_id=from_ledger, to_ledger_id=to_ledger,
            )

            assert _count("journal_entries") - je_before == 1
            assert _count("account_postings") - ap_before == 2
