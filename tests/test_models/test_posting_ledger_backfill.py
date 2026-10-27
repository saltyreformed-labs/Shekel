"""Tests for the db239773c2fd historical settled-transfer backfill (Commit 3).

The Commit-3 migration creates the ledger tables and then backfills one
balanced journal entry per historical settled, non-deleted transfer.  The
migration is already at HEAD when these tests run (the template builder
upgraded base->head against an EMPTY ``budget.transfers`` table, so the
in-chain backfill was a no-op).  Each test therefore engineers a transfer
through ``transfer_service`` (the sole writer, so the shadows are real) and
invokes the migration's idempotent
:func:`_backfill_settled_transfers` directly -- the same pattern the
``ledger_accounts`` / ``loan_anchor_events`` backfill tests use.

Since Commit 5 wired the transfer service to auto-post on settle, each
settled-transfer test first cleared those go-forward postings (raw SQL, via the
shared ``clear_postings_for_transfer`` helper -- the ORM blocks deletes on the
append-only tables) to reproduce the pre-ledger historical state the backfill
targets; otherwise the backfill's ``NOT EXISTS`` guard would no-op and the test
would assert on the auto-posted entry instead of the backfilled one.  The
helper keyed on ``journal_entries.transfer_id`` and had no caller left when plan
step ``balance:X-bi-6-5``'s second leaf (card plan#1845) dropped that column,
so it went with it.

**THE EXECUTABLE HALF OF THIS SUITE WAS DELETED AT PLAN STEP X-f1** (developer
ruling, 2026-08-03), and what survives is the revision pair and the
source-level downgrade check.  The ten deleted tests drove
``_backfill_settled_transfers``'s frozen raw SQL, which reads
``sf.paid_at`` -- a column migration ``a3f7c8e21b64`` DROPS.  They graded these
invariants: one balanced entry per settled transfer (asset->asset and
asset->liability), the leg amount taken from the shadow's ``effective_amount``,
the entry date derived from the shadow's ``paid_at``, the four exclusions
(Projected / Cancelled / soft-deleted / zero-effective), and idempotency.

**The path they graded is unreachable with data, permanently, and that is why
they went rather than being fed a resurrected column.**  This migration runs at
its OWN point in the chain, long before the drop, so a real ``base -> head``
upgrade is unaffected -- but it runs there over an EMPTY ``budget.transfers``,
and on any database already past it it never runs again.  ``a3f7c8e21b64``'s
downgrade REFUSES, so Alembic cannot rewind past the drop either.  Keeping them
runnable would have meant a per-revision template fixture that this suite does
not have -- ``alembic upgrade base -> <that revision>`` gives exactly the
application's schema at that point and is not blocked, so the honest statement is
that the harness has no such fixture, NOT that the schema could not be
reproduced.  (An earlier draft said the latter; a neutral review corrected it.)

**One of the deleted ten was NOT redundant, and naming it is the point.**  The
zero-effective-transfer exclusion has no survivor anywhere.  Nor do the
``TestBackfillAndGoForwardAgree`` cases deleted from the two live reconciliation
oracles in the same pass: the two surviving "backfill == go-forward" tests
(``test_posting_ledger_account_backfill.py``, ``test_loan_posting_backfill.py``)
reuse the go-forward builder, so they are true by construction rather than an
independent second opinion.  The ledger these entries built is graded instead by the live
reconciliation oracles (``tests/test_integration/test_posting_ledger_*.py``),
which assert the stronger property: that the posted ledger reconciles to the
source rows, whatever wrote it.

The executable migration up/down round-trip was verified manually against the
prod-clone dev DB during development (downgrade dropped both tables, the
balanced trigger, and its function; re-upgrade regenerated 13 balanced
entries / 26 legs reconciling to the settled-transfer shadows); the downgrade
is checked at source level here, matching the ``ledger_accounts`` backfill
suite's rationale (a ``DROP TABLE`` needs an ACCESS EXCLUSIVE lock that
conflicts with the session-scoped ``ref_cache`` refresh in an xdist worker).
"""
# pylint: disable=redefined-outer-name
# Rationale: ``redefined-outer-name`` is the canonical pytest fixture
# pattern; bodies bind fixtures by name.
from __future__ import annotations

import pathlib

import pytest

from app.extensions import db as _db
from app.models.journal_entry import JournalEntry, Posting
from tests._test_helpers import (
    create_account_of_type,
    ledger_accounts_for_account,
    load_migration_module,
)


# ---------------------------------------------------------------------------
# Migration module under test (migrations/versions has no __init__)
# ---------------------------------------------------------------------------


# ``_MIGRATIONS_DIR`` / ``_MIGRATION_FILENAME`` are retained for the
# source-level downgrade check (``TestDowngradeReversible``), which reads the
# migration file's TEXT rather than loading it as a module.  Loading the module
# (to invoke its backfill) goes through the shared ``load_migration_module``.
_MIGRATIONS_DIR = (
    pathlib.Path(__file__).resolve().parents[2] / "migrations" / "versions"
)
_MIGRATION_FILENAME = (
    "db239773c2fd_create_journal_entries_account_postings_.py"
)
_MIGRATION = load_migration_module(_MIGRATION_FILENAME)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _run_backfill():
    """Execute the migration's idempotent backfill on the test session."""
    posted = _MIGRATION._backfill_settled_transfers(_db.session)
    _db.session.commit()
    return posted


def _entry_for_transfer(transfer_id):
    """Return the single journal entry for *transfer_id*, or None."""
    return (
        _db.session.query(JournalEntry)
        .filter_by(transfer_id=transfer_id)
        .one_or_none()
    )


def _legs_by_ledger(entry_id):
    """Return ``{ledger_account_id: amount}`` for an entry's legs."""
    return {
        leg.ledger_account_id: leg.amount
        for leg in _db.session.query(Posting).filter_by(
            journal_entry_id=entry_id,
        ).all()
    }


def _ledger_id(account):
    """Return the linked ledger account id for *account*."""
    return ledger_accounts_for_account(_db.session, account.id)[0].id


@pytest.fixture()
def savings(app, db, seed_user):  # pylint: disable=unused-argument
    """A second (Savings) account so transfers have a destination.

    Created in the ``db`` fixture's app context (no nested context) so the
    returned :class:`Account` stays bound to the live session the test runs
    in -- the same pattern ``seed_user`` uses.  A nested context here would
    pop on return and detach the object.
    """
    acct = create_account_of_type(
        seed_user, _db.session, "Savings", "Backfill Savings",
    )
    _db.session.commit()
    return acct


# ---------------------------------------------------------------------------
# Backfill: one balanced entry per settled transfer
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Migration revision pair + downgrade source check
# ---------------------------------------------------------------------------


class TestMigrationRevisionPair:
    """The migration chains off the Commit-2 head."""

    def test_revision_pair(self):
        """revision / down_revision pin the migration into the chain."""
        assert _MIGRATION.revision == "db239773c2fd"
        assert _MIGRATION.down_revision == "b82538084d24"


class TestDowngradeReversible:
    """downgrade() removes the infrastructure and drops both tables.

    A source-level check (the executable round-trip is out of scope for the
    xdist worker -- see the module docstring) guards against a future edit
    silently re-routing the downgrade past one of the artefacts the upgrade
    materialises.
    """

    def test_downgrade_source_removes_infra_and_drops_tables(self):
        """The downgrade source removes posting infra and drops both tables."""
        source = (_MIGRATIONS_DIR / _MIGRATION_FILENAME).read_text()
        assert "remove_posting_infrastructure(op.execute)" in source
        assert (
            'drop_table("account_postings"' in source
            or "drop_table('account_postings'" in source
        ), "downgrade() never drops budget.account_postings"
        assert (
            'drop_table("journal_entries"' in source
            or "drop_table('journal_entries'" in source
        ), "downgrade() never drops budget.journal_entries"
