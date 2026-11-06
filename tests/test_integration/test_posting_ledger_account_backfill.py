"""Tests for the Build-Order Step 5 historical account anchor-posting backfill (C7).

Commit C7 posts every NON-loan account's opening / true-up anchor corrections
that predate the C6 go-forward wiring, so the ledger is complete on real
historical data and the trial balance closes app-wide.  Because an anchor
correction is a DAY-granular walk of the account's assertions against its
linked ledger -- not a one-line SQL formula -- the backfill cannot be raw SQL
like the Step-2 / Step-3 cash backfills; it reuses the go-forward per-account
sync (:func:`account_posting_service.sync_account_anchor_postings_all_scenarios`)
so a backfilled correction is IDENTICAL to a go-forward one by construction.  It
therefore does not run inside the Alembic migration (the migration host has no
``ref_cache``); it runs in the post-migration deploy hook
(``scripts/init_database.py``) and is exercised here through the app-layer entry
point :func:`account_posting_service.backfill_all_account_anchor_postings`.

Manufacturing the "historical" state: post-C6, creating an account (or truing up
its anchor) auto-posts its correction.  To reproduce an account whose opening was
asserted BEFORE the wiring existed (which carries no correction), each test
creates the account, then clears its corrections with the boundary migration's
own raw-SQL teardown (:func:`_MIGRATION._remove_account_anchor_postings`) --
exactly the pre-C6 state -- and asserts the backfill restores them.

The migration's executable downgrade/upgrade round-trip through Alembic runs
cleanly (verified on the freshly-built template); the downgrade's data removal is
checked behaviorally here (``_remove_account_anchor_postings`` is DELETE-based, so
it runs on the shared test session) plus a source-level guard.  The deploy hook
that runs the backfill in production
(``scripts/init_database.py``) is covered by a commit-contract test that observes
the persisted correction from a separate database connection (a mere flush would
be invisible to it).
"""
from __future__ import annotations

import pathlib
from datetime import date
from decimal import Decimal

from sqlalchemy import text

from app import ref_cache
from app.enums import LedgerAccountKindEnum, PostingSourceEnum
from app.extensions import db as _db
from app.models.journal_entry import JournalEntry, Posting
from app.models.scenario import Scenario
from app.services import (
    account_posting_service,
    loan_posting_service,
    posting_service,
)
from tests._stored_ledger import (
    find_loan_ledger_account,
    ledger_account_of_kind,
    ledger_accounts_for_account,
    linked_ledger_account,
    stored_ledger_net,
    transfer_family_journal_filter,
)
from tests._test_helpers import (
    add_anchor_history,
    create_account_of_type,
    create_loan_with_trueup,
    create_settled_transfer,
    freeze_today,
    load_init_database_module,
    load_migration_module,
)


# ---------------------------------------------------------------------------
# Migration module under test (migrations/versions has no __init__)
# ---------------------------------------------------------------------------

_MIGRATIONS_DIR = (
    pathlib.Path(__file__).resolve().parents[2] / "migrations" / "versions"
)
_MIGRATION_FILENAME = "c9f2e6a4b1d8_account_anchor_postings_data_boundary.py"
_MIGRATION = load_migration_module(_MIGRATION_FILENAME)

_INIT_DB = load_init_database_module()


# ---------------------------------------------------------------------------
# Fixture constants
# ---------------------------------------------------------------------------

# A fresh non-loan account's opening posts exactly its anchor balance (a fresh
# account has no source facts before the opening instant, so ledger_before = 0).
_ANCHOR = Decimal("500.00")

# A settled Checking -> account transfer's cash effect on the receiving account.
_CASH_IN = Decimal("250.00")

# seed_periods index whose start post-dates the seed bootstrap period, so a
# settled transfer in it is real go-forward activity.
_P1 = 1

# A configured loan (the EXCLUSION case): amortizing, so the account backfill's
# non-loan enumerator must skip it and mint no anchor_equity account for it.
_LOAN_ORIGINATION_PRINCIPAL = Decimal("250000.00")
_LOAN_ANCHOR_BALANCE = Decimal("100000.00")
_LOAN_ANCHOR_DATE = date(2026, 1, 10)
_LOAN_ORIGINATION_DATE = date(2025, 1, 1)
_LOAN_RATE = Decimal("0.06000")
_TODAY = date(2026, 5, 15)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _clear_account_corrections():
    """Remove every account opening / true-up correction + anchor_equity account.

    Reproduces the pre-C6 historical state -- an account whose anchor was
    asserted before the go-forward wiring shipped, carrying no correction -- the
    exact gap the backfill fills.  Runs the boundary migration's own raw-SQL
    teardown (:func:`_MIGRATION._remove_account_anchor_postings`), then commits.
    """
    _MIGRATION._remove_account_anchor_postings(_db.session)
    _db.session.commit()


def _backfill():
    """Run the app-layer historical backfill and commit; return the account ids."""
    posted = account_posting_service.backfill_all_account_anchor_postings()
    _db.session.commit()
    return posted


def _anchor_equity_account(db_session, account_id):
    """Return the account's anchor-equity ledger row, or None if not minted."""
    return ledger_account_of_kind(
        db_session, account_id, LedgerAccountKindEnum.ANCHOR_EQUITY,
    )


def _correction_entries(db_session, account_id, scenario_id, source_enum):
    """Return an account's correction entries of one source in a scenario.

    The ``budget.journal_entries`` rows the account posting service books with a
    leg on the account's LINKED ledger under *source_enum* (``account_opening``
    or ``account_trueup``) in *scenario_id*, ascending by id -- the way the
    reconcile scopes corrections to one account.
    """
    linked = linked_ledger_account(db_session, account_id)
    entry_ids = (
        db_session.query(Posting.journal_entry_id)
        .filter(Posting.ledger_account_id == linked.id)
    )
    return (
        db_session.query(JournalEntry)
        .filter(
            JournalEntry.scenario_id == scenario_id,
            JournalEntry.source_kind_id == ref_cache.posting_source_id(
                source_enum,
            ),
            JournalEntry.id.in_(entry_ids),
        )
        .order_by(JournalEntry.id)
        .all()
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


def _make_loan(seed_user, db_session, name="Exclusion Loan"):
    """Create a fully-configured amortizing loan (the backfill must skip it)."""
    return create_loan_with_trueup(
        seed_user, db_session,
        origination_principal=_LOAN_ORIGINATION_PRINCIPAL,
        anchor_balance=_LOAN_ANCHOR_BALANCE, anchor_date=_LOAN_ANCHOR_DATE,
        rate=_LOAN_RATE, origination_date=_LOAN_ORIGINATION_DATE, name=name,
    )


# ---------------------------------------------------------------------------
# The core: the backfill posts a correction absent from history
# ---------------------------------------------------------------------------


class TestBackfillPostsHistoricalCorrection:
    """The backfill posts the opening correction for an account lacking one."""

    def test_posts_opening_for_account_lacking_one(self, app, db, seed_user):
        """A $500 Savings with no correction backfills its opening + equity twin.

        A fresh account's opening posts exactly its anchor (ledger_before = 0):
        the linked ledger nets +500, the minted anchor_equity twin -500, so the
        entry balances.  Clearing drops both the entry AND the twin ledger row;
        the backfill re-mints them, restoring the linked total to 500.00 and the
        equity net to -500.00.
        """
        with app.app_context():
            scenario_id = seed_user["scenario"].id
            savings = create_account_of_type(
                seed_user, db.session, "Savings", "Backfill Savings",
                anchor_balance=_ANCHOR,
            )
            db.session.commit()
            # The go-forward opening posted on create.
            assert posting_service.account_posting_total(
                savings.id, scenario_id,
            ) == _ANCHOR

            # Reproduce the pre-C6 historical state: no correction, no twin.
            _clear_account_corrections()
            assert posting_service.account_posting_total(
                savings.id, scenario_id,
            ) == Decimal("0.00")
            assert _anchor_equity_account(db.session, savings.id) is None

            posted = _backfill()

            assert savings.id in posted
            assert len(
                _correction_entries(
                    db.session, savings.id, scenario_id,
                    PostingSourceEnum.ACCOUNT_OPENING,
                )
            ) == 1
            assert posting_service.account_posting_total(
                savings.id, scenario_id,
            ) == _ANCHOR
            equity = _anchor_equity_account(db.session, savings.id)
            assert equity is not None
            assert stored_ledger_net(
                db.session, equity.id, scenario_id,
            ) == -_ANCHOR

    def test_backfill_restores_opening_leaving_settled_cash_intact(
        self, app, db, seed_user, seed_periods,
    ):
        """Clearing then backfilling restores the opening and never touches cash.

        A settled Checking -> Savings transfer posts a Step-2 cash entry
        (``transfer_id`` set) on the Savings linked ledger ALONGSIDE the opening
        correction.  The boundary teardown removes ONLY the anchor correction
        (source-scoped), so the cash entry survives and the linked total drops by
        the opening; the backfill restores the opening exactly (backfill ==
        go-forward) and leaves the cash entry untouched throughout.
        """
        with app.app_context():
            scenario_id = seed_user["scenario"].id
            savings = create_account_of_type(
                seed_user, db.session, "Savings", "Cash Savings",
                anchor_balance=_ANCHOR,
            )
            db.session.commit()
            xfer = create_settled_transfer(
                seed_user, db.session, seed_user["account"], savings,
                seed_periods[_P1], amount=_CASH_IN,
            )
            db.session.commit()
            total_goforward = posting_service.account_posting_total(
                savings.id, scenario_id,
            )

            _clear_account_corrections()

            # The cash entries (the transfer's two per-movement entries, plan
            # step ``balance:X-bi-6-3``) survive; the opening is gone, so the
            # total dropped from the go-forward figure.
            assert db.session.query(JournalEntry).filter(
                transfer_family_journal_filter(xfer.id),
            ).count() == 2
            total_cleared = posting_service.account_posting_total(
                savings.id, scenario_id,
            )
            assert total_cleared != total_goforward

            _backfill()

            # backfill == go-forward: the opening is restored exactly, cash intact.
            assert posting_service.account_posting_total(
                savings.id, scenario_id,
            ) == total_goforward
            assert db.session.query(JournalEntry).filter(
                transfer_family_journal_filter(xfer.id),
            ).count() == 2

    def test_backfill_restores_opening_and_trueup_for_multi_anchor_account(
        self, app, db, seed_user,
    ):
        """A trued-up account backfills BOTH its opening AND its true-up correction.

        An account with two anchor assertions -- the $500 opening plus a later
        $600 true-up -- carries an ``account_opening`` AND an ``account_trueup``
        correction.  Clearing drops both; the backfill re-derives both from the
        anchor history, so the ``account_trueup`` source path is exercised
        behaviorally (not just the opening).  Afterward the linked ledger nets the
        LATEST asserted balance: opening $500 + true-up delta $100 == $600.00.
        """
        with app.app_context():
            scenario_id = seed_user["scenario"].id
            savings = create_account_of_type(
                seed_user, db.session, "Savings", "Trueup Savings",
                anchor_balance=_ANCHOR,
            )
            # A second, later anchor assertion (the true-up to $600).  Its
            # created_at post-dates the opening's, so the walk orders it second.
            add_anchor_history(
                db.session, savings,
                Decimal("600.00"),
            )
            db.session.commit()

            _clear_account_corrections()
            _backfill()

            assert len(_correction_entries(
                db.session, savings.id, scenario_id,
                PostingSourceEnum.ACCOUNT_OPENING,
            )) == 1
            assert len(_correction_entries(
                db.session, savings.id, scenario_id,
                PostingSourceEnum.ACCOUNT_TRUEUP,
            )) == 1
            assert posting_service.account_posting_total(
                savings.id, scenario_id,
            ) == Decimal("600.00")

    def test_zero_anchor_account_books_nothing(self, app, db, seed_user):
        """A $0-anchor account backfills to NOTHING -- no entry, no equity twin.

        A fresh $0-anchor account's opening delta is $0, so the reconcile books
        nothing: no correction entry and no ``anchor_equity`` ledger row are
        minted (the account stays hard-deletable).  The backfill is a no-op on
        it -- the account is still enumerated (returned in ``posted``), but it
        writes no journal entry, matching the ``backfill_all_account_anchor_postings``
        docstring's $0 guarantee.
        """
        with app.app_context():
            scenario_id = seed_user["scenario"].id
            zero = create_account_of_type(
                seed_user, db.session, "Savings", "Zero Savings",
                anchor_balance=Decimal("0.00"),
            )
            db.session.commit()
            entries_before = db.session.query(JournalEntry).count()

            posted = _backfill()

            assert zero.id in posted  # enumerated...
            # ...but nothing booked: no entry, no equity twin, zero total.
            assert db.session.query(JournalEntry).count() == entries_before
            assert _correction_entries(
                db.session, zero.id, scenario_id,
                PostingSourceEnum.ACCOUNT_OPENING,
            ) == []
            assert _anchor_equity_account(db.session, zero.id) is None
            assert posting_service.account_posting_total(
                zero.id, scenario_id,
            ) == Decimal("0.00")


# ---------------------------------------------------------------------------
# Idempotency + no double-post against the go-forward correction
# ---------------------------------------------------------------------------


class TestBackfillIdempotentNoDoublePost:
    """The backfill never double-posts a go-forward correction and is idempotent."""

    def test_no_double_post_after_goforward(self, app, db, seed_user):
        """An account already carrying its opening backfills to nothing new.

        Creating the account posts the opening go-forward.  The backfill
        reconcile-to-target sees it already at target, so it writes NO new
        journal entry -- the total entry count is unchanged and the linked ledger
        still nets the anchor.
        """
        with app.app_context():
            scenario_id = seed_user["scenario"].id
            savings = create_account_of_type(
                seed_user, db.session, "Savings", "Idempotent Savings",
                anchor_balance=_ANCHOR,
            )
            db.session.commit()
            entries_before = db.session.query(JournalEntry).count()

            _backfill()

            assert db.session.query(JournalEntry).count() == entries_before
            assert posting_service.account_posting_total(
                savings.id, scenario_id,
            ) == _ANCHOR

    def test_backfill_twice_posts_once(self, app, db, seed_user):
        """Running the backfill twice leaves exactly one opening correction.

        After clearing the go-forward opening, the first backfill posts it and
        the second is a reconcile-to-target no-op -- the account carries exactly
        one correction, not two.
        """
        with app.app_context():
            scenario_id = seed_user["scenario"].id
            savings = create_account_of_type(
                seed_user, db.session, "Savings", "Twice Savings",
                anchor_balance=_ANCHOR,
            )
            db.session.commit()

            _clear_account_corrections()
            _backfill()
            entries_after_first = db.session.query(JournalEntry).count()
            _backfill()

            assert db.session.query(JournalEntry).count() == entries_after_first
            assert len(
                _correction_entries(
                    db.session, savings.id, scenario_id,
                    PostingSourceEnum.ACCOUNT_OPENING,
                )
            ) == 1


# ---------------------------------------------------------------------------
# Coverage: every account, every owner, every scenario; loans excluded
# ---------------------------------------------------------------------------


class TestBackfillCoverage:
    """The backfill reconciles every non-loan account across owners and scenarios."""

    def test_backfill_returns_all_non_loan_accounts_all_owners(
        self, app, db, seed_user, seed_second_user, monkeypatch,
    ):
        """The sweep returns every non-loan account across owners, loans excluded.

        The backfill iterates all non-loan accounts across all owners (its
        non-user-scoped enumeration is what makes the sweep production-wide); a
        second owner's Checking appears alongside the first owner's Checking and
        Savings, while a configured (amortizing) loan is skipped structurally --
        it books its genesis through the loan package, on a disjoint chart.
        """
        with app.app_context():
            freeze_today(monkeypatch, _TODAY)
            savings = create_account_of_type(
                seed_user, db.session, "Savings", "Owner1 Savings",
                anchor_balance=_ANCHOR,
            )
            db.session.commit()
            loan = _make_loan(seed_user, db.session)

            posted = _backfill()

            expected = sorted([
                seed_user["account"].id,
                savings.id,
                seed_second_user["account"].id,
            ])
            assert posted == expected
            assert loan.id not in posted
            # Disjoint charts: no anchor_equity account is minted for the loan.
            assert _anchor_equity_account(db.session, loan.id) is None

    def test_backfills_every_account(self, app, db, seed_user):
        """Two cleared accounts both get their opening restored in one backfill.

        Proves the sweep loops every account, not just the first: with the
        go-forward openings cleared, one backfill restores a correction under
        each account.
        """
        with app.app_context():
            scenario_id = seed_user["scenario"].id
            s1 = create_account_of_type(
                seed_user, db.session, "Savings", "Savings A",
                anchor_balance=_ANCHOR,
            )
            s2 = create_account_of_type(
                seed_user, db.session, "Savings", "Savings B",
                anchor_balance=Decimal("750.00"),
            )
            db.session.commit()

            _clear_account_corrections()
            posted = _backfill()

            assert s1.id in posted and s2.id in posted
            assert len(
                _correction_entries(
                    db.session, s1.id, scenario_id,
                    PostingSourceEnum.ACCOUNT_OPENING,
                )
            ) == 1
            assert len(
                _correction_entries(
                    db.session, s2.id, scenario_id,
                    PostingSourceEnum.ACCOUNT_OPENING,
                )
            ) == 1

    def test_backfills_every_scenario(self, app, db, seed_user, seed_periods):
        """An account live in two scenarios backfills its opening in BOTH.

        A settled transfer into the account in a non-baseline scenario puts a
        cash posting on the account's linked ledger there, so the account is
        "live" in that scenario; the all-scenarios sweep the backfill delegates
        to posts the opening in the baseline AND the what-if scenario.
        """
        with app.app_context():
            baseline = seed_user["scenario"]
            whatif = Scenario(
                user_id=seed_user["user"].id, name="What-if", is_baseline=False,
            )
            db.session.add(whatif)
            db.session.commit()

            savings = create_account_of_type(
                seed_user, db.session, "Savings", "Scenario Savings",
                anchor_balance=_ANCHOR,
            )
            db.session.commit()
            create_settled_transfer(
                seed_user, db.session, seed_user["account"], savings,
                seed_periods[_P1], amount=_CASH_IN, scenario=whatif,
            )
            db.session.commit()

            _clear_account_corrections()
            _backfill()

            assert len(
                _correction_entries(
                    db.session, savings.id, baseline.id,
                    PostingSourceEnum.ACCOUNT_OPENING,
                )
            ) == 1
            assert len(
                _correction_entries(
                    db.session, savings.id, whatif.id,
                    PostingSourceEnum.ACCOUNT_OPENING,
                )
            ) == 1


# ---------------------------------------------------------------------------
# The production deploy posts AND commits the backfill
# ---------------------------------------------------------------------------


class TestDeployHookCommitsBackfill:
    """The deploy runs the backfill hook and commits it durably, in ONE commit."""

    def test_hook_posts_and_commits_via_separate_connection(
        self, app, db, seed_user,
    ):
        """The deploy restores a missing opening AND commits it durably.

        Reproduces the production deploy: an account whose opening was asserted
        before the wiring (its correction cleared) is backfilled by the hook
        ``backfill_all_account_anchor_postings_after_migration``, which the
        deploy (``initialise_database``) runs inside its ONE transaction with
        the migrations and the other two hooks (plan step balance:X-cv).  A
        SEPARATE database connection -- which under READ COMMITTED sees only
        COMMITTED rows -- must observe the restored correction, proving the
        deploy's one commit ran: a deploy that merely flushed
        would leave the correction invisible to that connection, so this fails
        loud if the commit is ever dropped (the silent-persistence-loss failure
        mode).  Until X-cv the hook committed on its own and this test called it
        alone; the developer confirmed the re-expression (rule 5, 2026-09-22).
        """
        with app.app_context():
            savings = create_account_of_type(
                seed_user, db.session, "Savings", "Hook Savings",
                anchor_balance=_ANCHOR,
            )
            db.session.commit()
            linked_id = linked_ledger_account(db.session, savings.id).id
            opening_source_id = ref_cache.posting_source_id(
                PostingSourceEnum.ACCOUNT_OPENING,
            )

            # Reproduce the pre-C6 historical state (asserted, no correction),
            # committed so a separate connection can see the starting point.
            _clear_account_corrections()
            assert _correction_entries(
                db.session, savings.id, seed_user["scenario"].id,
                PostingSourceEnum.ACCOUNT_OPENING,
            ) == []

            _INIT_DB.initialise_database()

            # A fresh connection sees only COMMITTED rows: the correction is
            # visible only if the deploy committed (not merely flushed).
            with db.engine.connect() as conn:
                committed = conn.execute(
                    text(
                        "SELECT count(DISTINCT je.id) "
                        "FROM budget.journal_entries je "
                        "JOIN budget.account_postings ap "
                        "  ON ap.journal_entry_id = je.id "
                        "WHERE ap.ledger_account_id = :linked "
                        "  AND je.source_kind_id = :src"
                    ),
                    {"linked": linked_id, "src": opening_source_id},
                ).scalar()
            assert committed == 1


# ---------------------------------------------------------------------------
# Migration revision pair + downgrade teardown
# ---------------------------------------------------------------------------


class TestMigrationRevisionPair:
    """The migration chains off the C3 index re-key as the new head."""

    def test_revision_pair(self):
        """revision / down_revision pin the migration into the Step-5 chain."""
        assert _MIGRATION.revision == "c9f2e6a4b1d8"
        assert _MIGRATION.down_revision == "b7d9f3a1c5e8"


class TestDowngradeReversible:
    """downgrade() removes the account corrections, keeps cash and loan genesis.

    A behavioral check (``_remove_account_anchor_postings`` is DELETE-based, so
    it runs cleanly on the shared test session) plus a source-level guard against
    a future edit silently re-routing the teardown past one of the two artifacts
    it must remove.  The executable up/down round-trip is verified against the
    rebuilt template.
    """

    def test_downgrade_removes_account_corrections_keeps_cash(
        self, app, db, seed_user, seed_periods,
    ):
        """Teardown deletes account corrections + anchor_equity accounts only.

        A settled transfer into a $500 Savings posts BOTH a Step-2 cash entry
        (``transfer_id`` set, linked ledgers) and the opening correction (its
        anchor_equity twin).  The teardown deletes the correction and the twin,
        while leaving the Step-2 cash entry and the linked ledger intact -- the
        exact reverse of the go-forward booking.
        """
        with app.app_context():
            savings = create_account_of_type(
                seed_user, db.session, "Savings", "Downgrade Savings",
                anchor_balance=_ANCHOR,
            )
            db.session.commit()
            xfer = create_settled_transfer(
                seed_user, db.session, seed_user["account"], savings,
                seed_periods[_P1], amount=_CASH_IN,
            )
            db.session.commit()

            # Go-forward: opening correction + anchor_equity twin + the
            # transfer's two cash entries (plan step ``balance:X-bi-6-3``).
            assert _entry_count_for_source(PostingSourceEnum.ACCOUNT_OPENING) >= 1
            assert _anchor_equity_account(db.session, savings.id) is not None
            assert db.session.query(JournalEntry).filter(
                transfer_family_journal_filter(xfer.id),
            ).count() == 2
            # Two ledger rows for the account: linked + anchor_equity.
            assert len(ledger_accounts_for_account(db.session, savings.id)) == 2

            _MIGRATION._remove_account_anchor_postings(db.session)
            db.session.commit()

            # Account corrections + anchor_equity accounts removed.
            assert _entry_count_for_source(PostingSourceEnum.ACCOUNT_OPENING) == 0
            assert _entry_count_for_source(PostingSourceEnum.ACCOUNT_TRUEUP) == 0
            assert _anchor_equity_account(db.session, savings.id) is None
            # Only the linked ledger row remains for the account.
            assert len(ledger_accounts_for_account(db.session, savings.id)) == 1
            assert linked_ledger_account(db.session, savings.id) is not None
            # The transfer's two cash entries survive untouched.
            assert db.session.query(JournalEntry).filter(
                transfer_family_journal_filter(xfer.id),
            ).count() == 2

    def test_downgrade_leaves_loan_genesis_intact(
        self, app, db, seed_user, monkeypatch,
    ):
        """The account teardown is source-scoped: loan genesis is untouched.

        A configured loan books its opening / true-up on a per-loan
        ``equity_opening`` account (source ``loan_opening`` / ``loan_trueup``) --
        a DISJOINT chart from the non-loan ``account_opening`` / ``account_trueup``
        family.  Removing the account corrections must leave the loan's genesis
        entries and its ``equity_opening`` account entirely intact.
        """
        with app.app_context():
            freeze_today(monkeypatch, _TODAY)
            loan = _make_loan(seed_user, db.session)
            # Post the loan genesis (opening + true-up on the per-loan
            # equity_opening account) via the loan backfill, so the account
            # teardown below has a disjoint family to (not) touch.
            loan_posting_service.backfill_all_loan_postings()
            db.session.commit()

            assert _entry_count_for_source(PostingSourceEnum.LOAN_OPENING) == 1
            assert find_loan_ledger_account(
                db.session, loan.id, LedgerAccountKindEnum.EQUITY_OPENING,
            ) is not None

            _MIGRATION._remove_account_anchor_postings(db.session)
            db.session.commit()

            # Loan genesis survives the account-family teardown.
            assert _entry_count_for_source(PostingSourceEnum.LOAN_OPENING) == 1
            assert find_loan_ledger_account(
                db.session, loan.id, LedgerAccountKindEnum.EQUITY_OPENING,
            ) is not None

    def test_downgrade_source_removes_entries_and_anchor_equity_accounts(self):
        """The downgrade source deletes account corrections + anchor_equity accounts."""
        source = (_MIGRATIONS_DIR / _MIGRATION_FILENAME).read_text()
        assert (
            "DELETE FROM budget.journal_entries WHERE source_kind_id IN" in source
        )
        assert "DELETE FROM budget.ledger_accounts WHERE kind_id" in source
