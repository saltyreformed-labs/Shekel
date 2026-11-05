"""Naming invariant regression tests for budget.interest_params.

Locks the post-rename state of every database artifact attached to
``budget.interest_params``: PK constraint, FK constraint, backing
sequence, indexes, and audit trigger.  All five must carry the new
``interest_params`` prefix; none of the legacy ``hysa_params``
artifacts must remain.

Without the M-1 fix migration
(``44893a9dbcc3_finish_hysa_to_interest_params_rename.py``), a
fresh-from-migrations DB carried both the renamed table and a
legacy ``audit_hysa_params`` trigger that the rebuild migration
never dropped, double-firing into ``system.audit_log`` on every
``interest_params`` write.  This test guards against:

  * Future migrations re-introducing legacy names (a new ALTER
    that uses ``hysa_params_pkey`` would fail loudly here).
  * Future ``apply_audit_infrastructure`` changes accidentally
    creating a second trigger alongside the canonical one (the
    audit-trigger assertion below counts exactly one
    ``audit_*`` trigger on the table).

Audit reference: M-1 of
shekel-audits/security-2026-04-15/model-migration-drift.md.
"""
# pylint: disable=redefined-outer-name  -- pytest fixture pattern
from __future__ import annotations

from sqlalchemy import text

from app.extensions import db


class TestInterestParamsNaming:
    """Every artifact on budget.interest_params carries the new name."""

    def test_pk_constraint_named_interest_params_pkey(self, app, db):
        """The PK constraint is named ``interest_params_pkey``.

        Asserts the new name is present and the legacy
        ``hysa_params_pkey`` is absent.  Two separate queries so a
        partial rename (one present, one absent) is reported
        independently.
        """
        with app.app_context():
            new = db.session.execute(text(
                "SELECT 1 FROM pg_constraint c "
                "JOIN pg_namespace n ON c.connamespace = n.oid "
                "WHERE c.conname = 'interest_params_pkey' "
                "AND n.nspname = 'budget'"
            )).scalar()
            old = db.session.execute(text(
                "SELECT 1 FROM pg_constraint c "
                "JOIN pg_namespace n ON c.connamespace = n.oid "
                "WHERE c.conname = 'hysa_params_pkey' "
                "AND n.nspname = 'budget'"
            )).scalar()
            assert new == 1, "interest_params_pkey is missing"
            assert old is None, "legacy hysa_params_pkey still present"

    def test_fk_constraint_named_fk_interest_params_account(
        self, app, db
    ):
        """The account_id FK is named ``fk_interest_params_account``.

        Migration ``c42b1d9a4e8f`` advanced this constraint through
        two renames -- the original ``hysa_params_account_id_fkey``
        (from the hysa_params CREATE TABLE), then
        ``interest_params_account_id_fkey`` (from the 44893a9dbcc3
        finish-rename), and finally ``fk_interest_params_account``
        (the project's ``fk_*`` convention).  This test asserts the
        post-C-42 state and refuses any of the three prior names.
        """
        with app.app_context():
            new = db.session.execute(text(
                "SELECT 1 FROM pg_constraint c "
                "JOIN pg_namespace n ON c.connamespace = n.oid "
                "WHERE c.conname = 'fk_interest_params_account' "
                "AND n.nspname = 'budget'"
            )).scalar()
            mid = db.session.execute(text(
                "SELECT 1 FROM pg_constraint c "
                "JOIN pg_namespace n ON c.connamespace = n.oid "
                "WHERE c.conname = 'interest_params_account_id_fkey' "
                "AND n.nspname = 'budget'"
            )).scalar()
            old = db.session.execute(text(
                "SELECT 1 FROM pg_constraint c "
                "JOIN pg_namespace n ON c.connamespace = n.oid "
                "WHERE c.conname = 'hysa_params_account_id_fkey' "
                "AND n.nspname = 'budget'"
            )).scalar()
            assert new == 1, "fk_interest_params_account is missing"
            assert mid is None, (
                "intermediate interest_params_account_id_fkey still "
                "present -- C-42 rename did not run"
            )
            assert old is None, "legacy hysa_params_account_id_fkey still present"

    def test_sequence_named_interest_params_id_seq(self, app, db):
        """The backing sequence is named ``interest_params_id_seq``."""
        with app.app_context():
            new = db.session.execute(text(
                "SELECT 1 FROM pg_class c "
                "JOIN pg_namespace n ON c.relnamespace = n.oid "
                "WHERE c.relname = 'interest_params_id_seq' "
                "AND n.nspname = 'budget' AND c.relkind = 'S'"
            )).scalar()
            old = db.session.execute(text(
                "SELECT 1 FROM pg_class c "
                "JOIN pg_namespace n ON c.relnamespace = n.oid "
                "WHERE c.relname = 'hysa_params_id_seq' "
                "AND n.nspname = 'budget' AND c.relkind = 'S'"
            )).scalar()
            assert new == 1, "interest_params_id_seq is missing"
            assert old is None, "legacy hysa_params_id_seq still present"

    def test_no_legacy_idx_hysa_params_account(self, app, db):
        """The legacy idx_hysa_params_account index is absent.

        The unique index ``interest_params_account_id_key`` (created
        by the unique=True on the column) covers account_id, so the
        separate non-unique idx is redundant and was dropped.
        """
        with app.app_context():
            old = db.session.execute(text(
                "SELECT 1 FROM pg_class c "
                "JOIN pg_namespace n ON c.relnamespace = n.oid "
                "WHERE c.relname = 'idx_hysa_params_account' "
                "AND n.nspname = 'budget' AND c.relkind = 'i'"
            )).scalar()
            assert old is None, "legacy idx_hysa_params_account still present"

            unique = db.session.execute(text(
                "SELECT 1 FROM pg_class c "
                "JOIN pg_namespace n ON c.relnamespace = n.oid "
                "WHERE c.relname = 'interest_params_account_id_key' "
                "AND n.nspname = 'budget' AND c.relkind = 'i'"
            )).scalar()
            assert unique == 1, (
                "interest_params_account_id_key (the unique index "
                "covering account_id) is missing"
            )

    def test_exactly_one_audit_trigger_on_interest_params(self, app, db):
        """Exactly one audit trigger fires on budget.interest_params writes.

        The legacy ``audit_hysa_params`` trigger created by
        a8b1c2d3e4f5 stayed attached to the renamed table after
        b4a6bb55f78b.  The rebuild migration a5be2a99ea14 then
        created ``audit_interest_params`` via
        apply_audit_infrastructure WITHOUT dropping the orphan, so
        every write double-recorded into system.audit_log.  M-1
        fixes this; the test enforces the post-fix invariant -- one
        and only one audit trigger on the table.
        """
        with app.app_context():
            triggers = db.session.execute(text(
                "SELECT tgname FROM pg_trigger t "
                "JOIN pg_class c ON t.tgrelid = c.oid "
                "JOIN pg_namespace n ON c.relnamespace = n.oid "
                "WHERE c.relname = 'interest_params' "
                "AND n.nspname = 'budget' "
                "AND tgname LIKE 'audit_%' "
                "AND NOT tgisinternal "
                "ORDER BY tgname"
            )).all()
            trigger_names = [row[0] for row in triggers]
            assert trigger_names == ["audit_interest_params"], (
                f"Expected exactly one audit trigger "
                f"['audit_interest_params'] on budget.interest_params, "
                f"got {trigger_names}.  If this regression names "
                f"audit_hysa_params alongside audit_interest_params, "
                f"the M-1 finish-rename migration is being undone."
            )
