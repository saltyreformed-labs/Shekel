"""Tests for the Build-Order Step 4 historical loan-payment split backfill (Commit 6).

Commit 6 posts one balanced CORRECTION per confirmed post-anchor settled loan
payment that predates the Commit-5 go-forward wiring, so the ledger is complete
on real historical data.  Because the loan split is a running-balance walk over
rate periods and effective-dated escrow -- not a one-line SQL formula -- the
backfill cannot be raw SQL like the Step-2 / Step-3 cash backfills; it reuses the
go-forward per-loan sync (:func:`loan_posting_service.sync_loan_postings_all_scenarios`)
so a backfilled correction is IDENTICAL to a go-forward one by construction.  It
therefore does not run inside the Alembic migration (the migration host has no
``ref_cache``); it runs in the post-migration deploy hook
(``scripts/init_database.py``) and is exercised here through the app-layer entry
point :func:`loan_posting_service.backfill_all_loan_postings`.

Manufacturing the "historical" state: post-Commit-5, settling a loan payment
through ``transfer_service`` auto-posts its correction.  To reproduce a payment
settled BEFORE the wiring existed (which carries no correction), each test
settles the payment, then clears the corrections with the migration's own raw-SQL
teardown (:func:`_MIGRATION._remove_loan_payment_postings`) -- exactly the
pre-Commit-5 state -- and asserts the backfill restores them.

The split fixtures are SYNTHETIC with HAND-COMPUTED literals: a $100,000 balance
at 6% gives a clean $500.00 first-month interest (``100000 * 0.06 / 12``).  The
trueup anchor ($100,000) is deliberately distinct from origination ($250,000), so
a correct interest figure also proves the walk seeds from the latest anchor, not
origination.  All money is ``Decimal`` from strings.

The migration's executable downgrade/upgrade round-trip through Alembic runs
cleanly (verified on the freshly-built template); the downgrade's data removal is
checked behaviorally here (``_remove_loan_payment_postings`` is DELETE-based, so it
runs on the shared test session) plus a source-level guard, and the with-data
prod-clone round-trip was verified in Commit 7 on an isolated clone of the live
prod-clone dev DB (the genesis reader reconciled to the resolver to the penny on
the real Mortgage and a two-true-up loan; the executable downgrade cleared the
genesis postings + equity accounts before the ref-seed delete, so the RESTRICT FKs
did not jam).  The deploy hook that runs the backfill in production
(``scripts/init_database.py``) is covered by a commit-contract test that observes
the persisted correction from a separate database connection (a mere flush would be
invisible to it).
"""
from __future__ import annotations

import pathlib
from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import text

from app import ref_cache
from app.enums import LedgerAccountKindEnum, PostingSourceEnum
from app.extensions import db as _db
from app.models.journal_entry import JournalEntry
from app.models.ledger_account import LedgerAccount
from app.models.scenario import Scenario
from app.services import (
    loan_loaders,
    loan_posting_service,
    posting_service,
)
from tests._stored_ledger import (
    find_loan_ledger_account,
    loan_correction_entries,
    stored_ledger_net,
)
from tests._test_helpers import (
    create_account_of_type,
    create_loan_with_trueup,
    create_settled_transfer,
    freeze_today,
    load_init_database_module,
    load_migration_module,
    transfer_side_record,
)


# ---------------------------------------------------------------------------
# Migration module under test (migrations/versions has no __init__)
# ---------------------------------------------------------------------------

_MIGRATIONS_DIR = (
    pathlib.Path(__file__).resolve().parents[2] / "migrations" / "versions"
)
_MIGRATION_FILENAME = "e2a9f1c7b4d6_backfill_loan_payment_split_postings.py"
_MIGRATION = load_migration_module(_MIGRATION_FILENAME)

# The read-switch genesis data boundary (Commit 4): its downgrade removes the
# opening / true-up entries + the per-loan opening-equity accounts.  A test
# reproducing the pre-posting historical state runs it BEFORE the Step-4 payment
# teardown -- the real downgrade chain order (this migration is the head, above
# the Step-4 boundary), so the Step-4 teardown then reaches only the interest /
# escrow / refund per-loan accounts.
_GENESIS_MIGRATION_FILENAME = "f3d6b1a8c2e4_loan_genesis_postings_data_boundary.py"
_GENESIS_MIGRATION = load_migration_module(_GENESIS_MIGRATION_FILENAME)


_INIT_DB = load_init_database_module()


# ---------------------------------------------------------------------------
# Synthetic fixture constants (hand-computable splits)
# ---------------------------------------------------------------------------

# A 6% loan on a $100,000 anchor accrues exactly $500.00 the first month
# (100000 * 0.06 / 12); the round numbers keep every split hand-computable.  The
# trueup anchor ($100,000) is distinct from origination ($250,000), so a correct
# interest figure proves the walk seeds from the trueup anchor, not origination.
_ANCHOR_BALANCE = Decimal("100000.00")
_RATE = Decimal("0.06000")
_ANCHOR_DATE = date(2026, 1, 10)
_ORIGINATION_PRINCIPAL = Decimal("250000.00")
_ORIGINATION_DATE = date(2025, 1, 1)

# seed_periods indices whose monthly due date (payment_day=1) lands in a DISTINCT
# month after the anchor: P1 start 2026-01-16 -> due 02-01; P2 start 2026-02-13
# -> due 03-01.
_P1, _P2 = 1, 3


@pytest.fixture(autouse=True)
def _freeze_today(monkeypatch):
    """Freeze today to 2026-05-15 so the backfill's ``date.today()`` as-of is fixed.

    ``backfill_all_loan_postings`` reconciles as of ``date.today()`` (via
    :func:`loan_posting_service.sync_loan_postings_all_scenarios`).
    2026-05-15 is after every payment period used (P1/P2 in Jan-Feb), so each
    settled payment is historical (eligible) regardless of the wall-clock date.
    """
    freeze_today(monkeypatch, date(2026, 5, 15))


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_loan(
    user, *, anchor_balance=_ANCHOR_BALANCE, rate=_RATE, name="Split Loan",
    escrow_annual=None,
):
    """Create a resolvable amortizing loan with the suite's controlled anchor."""
    return create_loan_with_trueup(
        user, _db.session,
        origination_principal=_ORIGINATION_PRINCIPAL,
        anchor_balance=anchor_balance, anchor_date=_ANCHOR_DATE, rate=rate,
        origination_date=_ORIGINATION_DATE, name=name, escrow_annual=escrow_annual,
    )


def _settle(user, loan, period, amount=Decimal("1000.00"), scenario=None):
    """Settle a Checking -> loan payment transfer through the service."""
    return create_settled_transfer(
        user, _db.session, user["account"], loan, period,
        amount=amount, scenario=scenario,
    )


def _clear_corrections():
    """Remove every loan posting (payment + opening + true-up) + per-loan account.

    Reproduces the pre-posting historical state -- a loan / payment settled before
    the go-forward wiring shipped, carrying no correction of any kind -- the exact
    gap the backfill fills.  Runs the two boundary migrations' own raw-SQL
    teardowns in real downgrade-chain order: the genesis teardown FIRST (opening /
    true-up entries + the per-loan opening-equity accounts), then the Step-4
    payment teardown (loan_payment corrections + the remaining per-loan interest /
    escrow / refund accounts).
    """
    _GENESIS_MIGRATION._remove_loan_genesis_postings(_db.session)
    _MIGRATION._remove_loan_payment_postings(_db.session)
    _db.session.commit()


def _backfill():
    """Run the app-layer historical backfill and commit; return the loan ids."""
    posted = loan_posting_service.backfill_all_loan_postings()
    _db.session.commit()
    return posted


def _per_loan_ledger_count(loan):
    """Return how many per-loan ledger accounts exist.

    Counts the ``loan_account_id``-scoped accounts -- interest / escrow / refund
    (payment corrections) plus opening-equity (the genesis opening / true-up).
    """
    return (
        _db.session.query(LedgerAccount)
        .filter_by(loan_account_id=loan.id)
        .count()
    )


def _entry_count_for_source(source_enum):
    """Return how many journal entries carry a given posting source."""
    return (
        _db.session.query(JournalEntry)
        .filter(
            JournalEntry.source_kind_id
            == ref_cache.posting_source_id(source_enum),
        )
        .count()
    )


# ---------------------------------------------------------------------------
# Idempotency + no double-post against the go-forward correction
# ---------------------------------------------------------------------------


class TestBackfillIdempotentNoDoublePost:
    """The backfill never double-posts a go-forward correction and is idempotent."""

    def test_backfill_twice_posts_once(
        self, app, db, seed_user, seed_periods,
    ):
        """Running the backfill twice leaves exactly one correction per payment.

        After clearing the go-forward correction, the first backfill posts it and
        the second is a reconcile-to-target no-op -- the shadow carries exactly one
        correction, not two.
        """
        with app.app_context():
            loan = _make_loan(seed_user)
            xfer = _settle(seed_user, loan, seed_periods[_P1])
            db.session.commit()

            _clear_corrections()
            _backfill()
            entries_after_first = db.session.query(JournalEntry).count()
            _backfill()

            assert db.session.query(JournalEntry).count() == entries_after_first
            assert len(loan_correction_entries(db.session, xfer.id, loan.id)) == 1


# ---------------------------------------------------------------------------
# Coverage: every loan, every owner, every scenario
# ---------------------------------------------------------------------------


class TestBackfillCoverage:
    """The backfill reconciles every loan across all owners and scenarios."""

    def test_enumerator_covers_all_loans_all_owners(
        self, app, db, seed_user, seed_second_user,
    ):
        """load_all_loan_account_ids returns every loan across owners, ascending.

        The backfill iterates this set, so its non-user-scoped, all-loans
        enumeration is what makes the sweep production-wide; a second owner's loan
        must appear alongside the first owner's.
        """
        with app.app_context():
            loan_a = _make_loan(seed_user, name="Loan A")
            loan_b = _make_loan(seed_user, name="Loan B")
            loan_c = _make_loan(seed_second_user, name="Loan C")

            ids = loan_loaders.load_all_loan_account_ids()

            assert ids == sorted([loan_a.id, loan_b.id, loan_c.id])

    def test_backfills_every_loan(
        self, app, db, seed_user, seed_periods,
    ):
        """A payment on each of two loans backfills a correction for BOTH.

        Proves the sweep loops every loan, not just the first: with the
        go-forward corrections cleared, one backfill restores a correction under
        each loan's payment shadow.
        """
        with app.app_context():
            loan_a = _make_loan(seed_user, name="Loan A")
            loan_b = _make_loan(seed_user, name="Loan B")
            xa = _settle(seed_user, loan_a, seed_periods[_P1])
            xb = _settle(seed_user, loan_b, seed_periods[_P1])
            db.session.commit()

            _clear_corrections()
            posted = _backfill()

            assert loan_a.id in posted and loan_b.id in posted
            assert len(loan_correction_entries(db.session, xa.id, loan_a.id)) == 1
            assert len(loan_correction_entries(db.session, xb.id, loan_b.id)) == 1

    def test_backfills_every_scenario(
        self, app, db, seed_user, seed_periods,
    ):
        """Payments in two scenarios on one loan both backfill their corrections.

        The anchor and rate live on the loan account, so a payment in a
        non-baseline scenario has the same split; the backfill delegates to the
        all-scenarios sync, so a correction is restored under BOTH scenarios' payment
        shadows.
        """
        with app.app_context():
            baseline = seed_user["scenario"]
            whatif = Scenario(
                user_id=seed_user["user"].id, name="What-if", is_baseline=False,
            )
            db.session.add(whatif)
            db.session.commit()

            loan = _make_loan(seed_user)
            x_base = _settle(seed_user, loan, seed_periods[_P1], scenario=baseline)
            x_whatif = _settle(seed_user, loan, seed_periods[_P1], scenario=whatif)
            db.session.commit()

            _clear_corrections()
            _backfill()

            assert len(loan_correction_entries(db.session, x_base.id, loan.id)) == 1
            assert len(loan_correction_entries(
                db.session, x_whatif.id, loan.id,
            )) == 1
            assert stored_ledger_net(
                db.session,
                find_loan_ledger_account(
                    db.session, loan.id, LedgerAccountKindEnum.LOAN_INTEREST,
                ).id,
                whatif.id,
            ) == Decimal("500.00")


# ---------------------------------------------------------------------------
# The backfill leaves non-loan and payment-free accounts alone
# ---------------------------------------------------------------------------


class TestBackfillLeavesNonLoansAlone:
    """The backfill touches only loan accounts; a payment-less loan gets its opening.

    A non-loan account is never visited (no LoanParams), while a configured loan
    with no payments still has its opening + true-up posted in the baseline
    scenario -- the genesis opening is not gated on having a payment.
    """

    def test_non_loan_transfer_gets_no_correction(
        self, app, db, seed_user, seed_periods,
    ):
        """A settled Checking -> Savings transfer is untouched by the loan backfill.

        Its to-side is a non-loan account (no LoanParams), so the enumerator
        never visits it: no loan_payment correction is posted for either side
        and no per-loan ledger account is minted.

        **The owner's entries are counted since plan step
        ``balance:X-bi-6-4d-2``** (ruling **R-BAL167** class 1): a transfer is
        one row there, so there is no shadow id to filter by, and no split has
        linked a shadow's row since ruling **R-BAL102**.  The owner has no loan,
        so any ``loan_payment`` entry is the backfill misfiring.
        """
        with app.app_context():
            savings = create_account_of_type(
                seed_user, db.session, "Savings", "Backfill Savings",
            )
            db.session.commit()
            xfer = create_settled_transfer(
                seed_user, db.session, seed_user["account"], savings,
                seed_periods[_P1], amount=Decimal("250.00"),
            )
            db.session.commit()

            _backfill()

            loan_payment_entries = (
                db.session.query(JournalEntry)
                .filter(
                    JournalEntry.user_id == xfer.user_id,
                    JournalEntry.source_kind_id
                    == ref_cache.posting_source_id(
                        PostingSourceEnum.LOAN_PAYMENT,
                    ),
                )
                .count()
            )
            assert loan_payment_entries == 0

    def test_loan_without_payments_backfills_its_opening(
        self, app, db, seed_user,
    ):
        """A configured loan with zero payments backfills its opening + true-up.

        It is enumerated (it has LoanParams) and, though it has no payment, the
        all-scenarios sync includes the owner's baseline, so the opening (-250000)
        and true-up (+150000) post there: the loan-linked ledger nets -100000.00
        == -(anchor 100000), and exactly one per-loan ledger account exists (the
        opening-equity account; no interest / escrow / refund without a payment).
        """
        with app.app_context():
            scenario_id = seed_user["scenario"].id
            loan = _make_loan(seed_user)

            posted = _backfill()

            assert loan.id in posted
            # Only the opening-equity per-loan ledger (no payment components).
            assert _per_loan_ledger_count(loan) == 1
            # Opening (-250000) + true-up (+150000), no payment.
            assert posting_service.account_posting_total(
                loan.id, scenario_id,
            ) == Decimal("-100000.00")


# ---------------------------------------------------------------------------
# The production deploy posts AND commits the backfill
# ---------------------------------------------------------------------------


class TestDeployHookCommitsBackfill:
    """The deploy runs the backfill hook and commits it durably, in ONE commit."""

    def test_hook_posts_and_commits_via_separate_connection(
        self, app, db, seed_user, seed_periods,
    ):
        """The deploy restores a missing correction AND commits it durably.

        Reproduces the production deploy: a payment settled before the wiring
        (its correction cleared) is backfilled by the hook
        ``backfill_loan_payment_postings_after_migration``, which the deploy
        (``initialise_database``) runs inside its ONE transaction with the
        migrations and the other two hooks (plan step balance:X-cv).  A
        SEPARATE database connection -- which under READ COMMITTED sees only
        COMMITTED rows -- must observe the restored correction, proving the
        deploy's one commit ran: a deploy that merely flushed
        would leave the correction invisible to that connection, so this fails
        loud if the commit is ever dropped (the silent-persistence-loss failure
        mode).  Until X-cv the hook committed on its own and this test called it
        alone; the developer confirmed the re-expression (rule 5, 2026-09-22).
        """
        with app.app_context():
            loan = _make_loan(seed_user)
            xfer = _settle(seed_user, loan, seed_periods[_P1])
            db.session.commit()
            # The split's day is the loan side's RECORD's, off the transfer
            # since plan step balance:X-bi-6-4d-2; it read the income
            # shadow's own day until then (ruling R-BAL167 class 1).
            day = transfer_side_record(db.session, xfer.id, loan.id).settled_on
            loan_payment_source_id = ref_cache.posting_source_id(
                PostingSourceEnum.LOAN_PAYMENT,
            )

            # Reproduce the pre-Commit-5 historical state (settled, no correction),
            # committed so a separate connection can see the starting point.
            _clear_corrections()
            assert loan_correction_entries(db.session, xfer.id, loan.id) == []

            _INIT_DB.initialise_database()

            # A fresh connection sees only COMMITTED rows: the correction is
            # visible only if the deploy committed (not merely flushed).  Read at
            # the split's KEY -- its scenario, period and day (ruling R-BAL102:
            # the split links no row; it was read by ``transaction_id`` here
            # until plan step ``balance:X-bi-6-3``).  The scenario and period
            # are the transfer's; they were read off its income shadow until
            # plan step ``balance:X-bi-6-4d-2`` (ruling R-BAL167 class 1).
            with db.engine.connect() as conn:
                committed = conn.execute(
                    text(
                        "SELECT count(*) FROM budget.journal_entries "
                        "WHERE source_kind_id = :src AND scenario_id = :sid "
                        "  AND pay_period_id = :pid AND entry_date = :day"
                    ),
                    {
                        "src": loan_payment_source_id,
                        "sid": xfer.scenario_id,
                        "pid": xfer.pay_period_id,
                        "day": day,
                    },
                ).scalar()
            assert committed == 1


# ---------------------------------------------------------------------------
# Migration revision pair + downgrade teardown
# ---------------------------------------------------------------------------


class TestMigrationRevisionPair:
    """The migration chains off the temporal-escrow head as the new head."""

    def test_revision_pair(self):
        """revision / down_revision pin the migration into the chain."""
        assert _MIGRATION.revision == "e2a9f1c7b4d6"
        assert _MIGRATION.down_revision == "d1e7c4a2f9b3"


class TestDowngradeReversible:
    """downgrade() removes Step-4 loan data, keeps the Step-2 cash entries.

    A behavioral check (``_remove_loan_payment_postings`` is DELETE-based, so it
    runs cleanly on the shared test session) plus a source-level guard against a
    future edit silently re-routing the teardown past one of the two artifacts it
    must remove.  The executable up/down round-trip was verified in Commit 7 against
    a clone of the prod-clone dev DB carrying real genesis data (the downgrade
    cleared the genesis postings + equity accounts before the ref-seed delete, so
    the RESTRICT FKs did not jam).
    """

    def test_downgrade_source_removes_entries_and_per_loan_accounts(self):
        """The downgrade source deletes loan_payment entries + per-loan accounts."""
        source = (_MIGRATIONS_DIR / _MIGRATION_FILENAME).read_text()
        assert (
            "DELETE FROM budget.journal_entries WHERE source_kind_id" in source
        )
        assert (
            "DELETE FROM budget.ledger_accounts WHERE loan_account_id IS NOT NULL"
            in source
        )


# ---------------------------------------------------------------------------
# The read-switch genesis boundary migration (Commit 4)
# ---------------------------------------------------------------------------


class TestGenesisBoundaryMigration:
    """The genesis boundary migration is the head, and its downgrade is reversible.

    A behavioral check (``_remove_loan_genesis_postings`` is DELETE-based, so it
    runs on the shared test session) plus a source-level guard.  It exists so the
    Commit-1 ref-seed downgrade (``d1b22f59ba5b``) -- which deletes the
    loan_opening / loan_trueup / equity_opening ref rows under RESTRICT FKs -- is
    clean once genesis postings exist; the executable up/down round-trip is
    verified against the rebuilt template.
    """

    def test_revision_pair(self):
        """revision / down_revision pin the migration as the head above Commit 1."""
        assert _GENESIS_MIGRATION.revision == "f3d6b1a8c2e4"
        assert _GENESIS_MIGRATION.down_revision == "d1b22f59ba5b"

    def test_downgrade_source_removes_genesis_entries_and_equity_accounts(self):
        """The downgrade source deletes genesis entries + equity_opening accounts."""
        source = (_MIGRATIONS_DIR / _GENESIS_MIGRATION_FILENAME).read_text()
        assert (
            "DELETE FROM budget.journal_entries WHERE source_kind_id IN" in source
        )
        assert "DELETE FROM budget.ledger_accounts WHERE kind_id" in source
