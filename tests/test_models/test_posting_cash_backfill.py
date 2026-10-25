"""Tests for the 7d63529e4300 historical settled cash-transaction backfill (Commit 7).

The Commit-7 migration creates the per-category Income/Expense chart-of-accounts
rows (Pass A) and backfills one balanced journal entry per historical settled,
non-deleted, non-transfer transaction with a nonzero confirmed cash effect
(Pass B).  The migration is already at HEAD when these tests run (the template
builder upgraded base->head against an EMPTY ``budget.transactions`` table, so
the in-chain backfill was a no-op).  Each test therefore engineers settled
transactions directly and invokes the migration's idempotent
:func:`_backfill_settled_transactions` -- the same pattern the
settled-transfer backfill suite uses.

Why direct ORM construction reproduces the "historical" state: the go-forward
poster (Commits 4-6) only writes a journal entry when a transaction crosses
into a settled status THROUGH THE STATUS SEAM (a route / service path).  Tests
build settled rows via the ``add_txn`` constructor helper (status passed as a
constructor kwarg, never a post-hoc ``status_id`` assignment, so the W9907 seam
checker is satisfied and no go-forward post fires).  A settled row built this
way carries no posting -- exactly the pre-ledger state the backfill targets.

**Post-Step-4 adaptation (``kind_id``).**  Step 4, Commit 2 (``efca4315bf81``)
added a NOT NULL ``ledger_accounts.kind_id``, so the frozen 7d63 Pass-A INSERTs
-- which predate that column and omit it -- can no longer run standalone at
HEAD.  In production 7d63 ran at its own revision (before ``kind_id`` existed)
and the Step-4 migration then backfilled each row's ``kind_id`` from its shape
(category rows -> ``category``, fallback rows -> ``fallback``); at HEAD the two
are fused because ``kind_id`` is already NOT NULL.  The ``_inject_kind_into_pass_a``
autouse fixture reproduces exactly that fusion by swapping the two frozen,
immutable Pass-A SQL constants for kind-injected equivalents.  Since plan step
``balance:X-bi-6-3`` renamed ``is_fallback`` to ``is_owner_bucket`` (ruling
R-BAL99), the same swap also re-homes Pass A's column list, the fallback
INSERT's ``ON CONFLICT`` target and Pass B's fallback join onto that name.  The
rest of the migration's REAL :func:`_backfill_settled_transactions`
orchestration (the settled-cash guard, ref-id resolution, and the rest of Pass
B's text) runs as shipped.  The injection reuses the frozen mapping SQL as its
single source.

**THE PASS-B HALF OF THIS SUITE WAS DELETED AT PLAN STEP X-f1** (developer
ruling, 2026-08-03).  What survives is the set of tests that never reach the
frozen SQL -- the three EXCLUSION cases, which are answered by
``_has_settled_cash_transactions``'s early return before any statement is
built -- plus the revision pair and the source-level downgrade check.

The twelve deleted tests drove ``_backfill_settled_transactions``'s frozen raw
SQL, which reads ``t.paid_at`` -- a column migration ``a3f7c8e21b64`` DROPS.
They graded: one balanced entry per settled plain expense / income (signed cash
leg against the resolved category leg, source kind ``transaction``), the
confirmed effect ``COALESCE(actual, estimated) - SUM(credit entries)``, counter
-account creation and reuse (per-category and the Uncategorized fallback), the
entry date derived from ``paid_at`` with the pay-period-start fallback, and
idempotency.

**The path they graded is unreachable with data, permanently, and that is why
they went rather than being fed a resurrected column.**  This migration runs at
its OWN point in the chain, long before the drop, so a real ``base -> head``
upgrade is unaffected -- but it runs there over an EMPTY ``budget.transactions``
and returns early; on any database already past it it never runs again; and
``a3f7c8e21b64``'s downgrade REFUSES, so Alembic cannot rewind past the drop
either.  Keeping them runnable would have meant a per-revision template fixture that this
suite does not have -- ``alembic upgrade base -> <that revision>`` gives exactly
the application's schema at that point and is not blocked, so the honest
statement is that the harness has no such fixture, NOT that the schema could not
be reproduced.  (An earlier draft of this paragraph said the latter; a neutral
review corrected it.)

**Two of the deleted twelve were NOT redundant, and naming them is the point.**
``TestDowngradeReversible::test_downgrade_removes_step3_artifacts_keeps_step2``
EXECUTED the downgrade; the survivor beside it is a ``read_text()`` regex over
the migration SOURCE and cannot catch a downgrade that deletes the wrong rows.
And the counter-account creation / reuse cases were the only place an
INDEPENDENT implementation of the sign, amount and date rules was compared
against the go-forward builder.  The live oracles assert a stronger property
about the ledger's CONTENT -- that it reconciles to the source rows -- but they
do not re-derive it a second way.  The ledger these entries built is graded instead
by the live reconciliation oracles
(``tests/test_integration/test_posting_ledger_*.py``), which assert the stronger
property: that the posted ledger reconciles to the source rows, whatever wrote
them.

The executable migration up/down round-trip was verified manually against the
prod-clone dev DB during development (the downgrade removed every
transaction-sourced entry and counter ledger account, leaving the Step-2
transfer entries and linked accounts intact, and a re-upgrade regenerated them
identically reconciling to the settled transactions); the downgrade is checked
at source level here, matching the settled-transfer backfill suite's rationale.
"""
# pylint: disable=redefined-outer-name
# Rationale: ``redefined-outer-name`` is the canonical pytest fixture
# pattern; bodies bind fixtures by name.
from __future__ import annotations

import pathlib
from decimal import Decimal

import pytest

from app.enums import (
    StatusEnum,
)
from app.extensions import db as _db
from app.models.journal_entry import JournalEntry
from app.services import match_withdrawal, movement_removal
from tests._test_helpers import (
    add_txn,
    create_account_of_type,
    create_settled_transfer,
    inject_cash_backfill_kind_id,
    load_migration_module,
    run_migration_callable,
)


_MIGRATIONS_DIR = (
    pathlib.Path(__file__).resolve().parents[2] / "migrations" / "versions"
)
_MIGRATION_FILENAME = "7d63529e4300_backfill_historical_cash_postings.py"
_MIGRATION = load_migration_module(_MIGRATION_FILENAME)

#: Plan step ``balance:X-bi-6-4d-2``'s revision, which deletes every transfer
#: shadow and refuses a new one; its downgrade builds a transfer's two, which
#: the shadow exclusion case needs.
_TWINS_DELETED = load_migration_module("82f7b7ed4332_a_transfer_is_one_row.py")


@pytest.fixture(autouse=True)
def _inject_kind_into_pass_a(monkeypatch):
    """Re-home the frozen 7d63 SQL onto the head chart schema for every test.

    Autouse so every ``_run_backfill()`` runs the migration's real orchestration
    with Pass A carrying the Step-4 ``kind_id`` and the owner-bucket flag under
    its head name (see the module docstring's "Post-Step-4 adaptation").
    Delegates to the shared :func:`inject_cash_backfill_kind_id` helper;
    ``monkeypatch`` auto-reverts the module constants after each test.
    """
    inject_cash_backfill_kind_id(monkeypatch, _MIGRATION)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _run_backfill():
    """Execute the migration's idempotent backfill on the test session."""
    posted = _MIGRATION._backfill_settled_transactions(_db.session)
    _db.session.commit()
    return posted


def _entry_for_transaction(transaction_id):
    """Return the single journal entry for *transaction_id*, or None."""
    return (
        _db.session.query(JournalEntry)
        .filter_by(transaction_id=transaction_id)
        .one_or_none()
    )


@pytest.fixture()
def savings(app, db, seed_user):  # pylint: disable=unused-argument
    """A second (Savings) account so a transfer-shadow exclusion test has a target.

    Created in the ``db`` fixture's app context (no nested context) so the
    returned :class:`Account` stays bound to the live session the test runs in.
    """
    acct = create_account_of_type(
        seed_user, _db.session, "Savings", "Backfill Savings",
    )
    _db.session.commit()
    return acct


# ---------------------------------------------------------------------------
# Exclusions
# ---------------------------------------------------------------------------


class TestBackfillExclusions:
    """Projected / Cancelled / Credit / soft-deleted / shadow rows are excluded."""

    @pytest.mark.parametrize("status_enum", [
        StatusEnum.PROJECTED,
        StatusEnum.CANCELLED,
        StatusEnum.CREDIT,
    ])
    def test_non_settled_status_not_backfilled(
        self, app, db, seed_user, status_enum,
    ):
        """A Projected / Cancelled / Credit transaction produces no entry.

        Only ``is_settled`` rows post.  Projected has not happened; Cancelled
        and Credit are is_settled FALSE (Credit's checking effect comes via its
        CC Payback, not the source row).
        """
        with app.app_context():
            period = seed_user["bootstrap_period"]
            txn = add_txn(
                _db.session, seed_user, period, "NotSettled", "50.00",
                status_enum=status_enum, category_key="Groceries",
            )
            _db.session.commit()
            assert _run_backfill() == []
            assert _entry_for_transaction(txn.id) is None

    def test_soft_deleted_settled_not_backfilled(self, app, db, seed_user):
        """A settled-but-soft-deleted transaction produces no entry.

        Its payment is off the books and it is hidden, so it moves no money,
        and the backfill's ``is_deleted = FALSE`` filter excludes it.
        Built as the delete door leaves it (ruling R-CC75): settled, its
        payment taken off through the one removal act, then hidden -- the
        database refuses a payment under a hidden row (R-CC89) and a row
        hidden holding one (R-CC92).  The row is still SETTLED, so the
        existence check's ``is_deleted`` filter is what answers.  Rule-5
        re-expression, developer-confirmed 2026-09-23.
        """
        with app.app_context():
            period = seed_user["bootstrap_period"]
            txn = add_txn(
                _db.session, seed_user, period, "Deleted", "50.00",
                status_enum=StatusEnum.DONE, category_key="Groceries",
            )
            movement_removal.remove_movements(
                list(txn.entries), seed_user["user"].id,
                because=match_withdrawal.LEFT_THE_BOOKS,
                press=None,
            )
            txn.is_deleted = True
            _db.session.commit()
            assert _run_backfill() == []
            assert _entry_for_transaction(txn.id) is None

    def test_transfer_shadow_not_backfilled_as_transaction(
        self, app, db, seed_user, savings,
    ):
        """A settled transfer's shadows are excluded (Step 2 owns them).

        The shadows carry ``transfer_id``, so the existence check's
        ``transfer_id IS NULL`` term excludes them and the backfill returns
        early, posting nothing.  Past that check, Pass B, the one writer
        left, reads columns this schema dropped and raises inside the frozen
        SQL, so a regression in the term fails here as that crash, not as a
        read naming the exclusion.

        No transfer has a shadow since plan step ``balance:X-bi-6-4d-2``
        (``82f7b7ed4332`` deletes them and refuses a new one), so the case
        first runs that revision's downgrade, which builds the transfer's two,
        each settled and live as the transfer is.  That is asserted: a
        Projected or hidden shadow is excluded by the status or ``is_deleted``
        filter, which would answer in place of the ``transfer_id`` one.
        """
        with app.app_context():
            transfer = create_settled_transfer(
                seed_user, _db.session, seed_user["account"], savings,
                seed_user["bootstrap_period"], amount=Decimal("100.00"),
            )
            _db.session.commit()
            run_migration_callable(_TWINS_DELETED.downgrade, _db.session)
            shadows = transfer.shadow_transactions
            assert [(s.status.is_settled, s.is_deleted) for s in shadows] == [
                (True, False), (True, False),
            ]

            assert _run_backfill() == []


# ---------------------------------------------------------------------------
# Migration revision pair + downgrade source check
# ---------------------------------------------------------------------------


class TestMigrationRevisionPair:
    """The migration chains off the Commit-2 schema head."""

    def test_revision_pair(self):
        """revision / down_revision pin the migration into the chain."""
        assert _MIGRATION.revision == "7d63529e4300"
        assert _MIGRATION.down_revision == "bdde62675c9b"


class TestDowngradeReversible:
    """downgrade() removes Step-3 entries + counter accounts, keeps Step-2.

    A source-level guard against a future edit silently re-routing the
    downgrade past one of the two artefacts it must remove.  The behavioral
    case that EXECUTED the downgrade went with the Pass-B half at plan step
    X-f1 (see the module docstring).  The executable up/down round-trip was
    also verified manually against the prod-clone dev DB (see the module
    docstring).
    """

    def test_downgrade_source_removes_entries_and_counter_accounts(self):
        """The downgrade source deletes transaction entries + counter accounts."""
        source = (_MIGRATIONS_DIR / _MIGRATION_FILENAME).read_text()
        assert "DELETE FROM budget.journal_entries WHERE source_kind_id" in source
        assert (
            "DELETE FROM budget.ledger_accounts WHERE account_id IS NULL"
            in source
        )
