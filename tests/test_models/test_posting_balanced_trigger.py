"""Tests for the deferred balanced-journal constraint trigger (Commit 3).

``ck_account_postings_balanced`` (in :mod:`app.posting_infrastructure`) is the
one genuinely new DB mechanism in the posting-ledger work: a DEFERRABLE
INITIALLY DEFERRED constraint trigger that enforces, at COMMIT, that every
journal entry's posting legs ``SUM(amount) = 0`` and number ``>= 2``.  These
tests pin its load-bearing properties via raw SQL (so the database trigger,
not a Python guard, is the surface):

  * a single-leg entry is rejected AT COMMIT (the ``COUNT >= 2`` half);
  * an unbalanced two-leg entry is rejected AT COMMIT (the ``SUM = 0`` half);
  * a balanced two-leg entry commits;
  * the check is DEFERRED -- the first leg's INSERT does not raise
    mid-transaction (an immediate trigger would);
  * a raw-SQL UPDATE that unbalances an entry is rejected at COMMIT, while a
    balanced UPDATE passes (the trigger fires AFTER UPDATE too);
  * a CASCADE delete of an entry does NOT fire the trigger (it is AFTER
    INSERT OR UPDATE only, so the disposal path is not aborted on a transient
    ``COUNT < 2``).

Plus the apply/remove idempotency contract the migration,
``scripts/init_database``, and ``scripts/build_test_template`` all rely on.
"""
# pylint: disable=redefined-outer-name
# Rationale: ``redefined-outer-name`` is the canonical pytest fixture
# pattern; bodies bind fixtures by name.
from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from app import ref_cache
from app.enums import PostingKindEnum, PostingSourceEnum
from app.extensions import db as _db
from app.posting_infrastructure import (
    apply_posting_infrastructure,
    remove_posting_infrastructure,
)
from tests._test_helpers import (
    create_account_of_type,
    ledger_accounts_for_account,
    refused_by_database_rule,
)


# ---------------------------------------------------------------------------
# Raw-SQL building blocks (bypass the ORM so the DB trigger is the surface)
# ---------------------------------------------------------------------------


def _insert_entry(session, seed_user, period_id):
    """Insert a bare journal entry (no legs) via raw SQL; return its id."""
    return session.execute(_db.text(
        "INSERT INTO budget.journal_entries "
        "  (user_id, scenario_id, pay_period_id, entry_date, "
        "   source_kind_id, description) "
        "VALUES (:u, :s, :p, :d, :src, 'trigger test') RETURNING id"
    ), {
        "u": seed_user["user"].id,
        "s": seed_user["scenario"].id,
        "p": period_id,
        "d": date(2026, 1, 15),
        "src": ref_cache.posting_source_id(PostingSourceEnum.TRANSFER_MOVEMENT),
    }).scalar()


def _insert_leg(session, entry_id, ledger_id, amount):
    """Insert one posting leg via raw SQL (signed amount as Decimal)."""
    session.execute(_db.text(
        "INSERT INTO budget.account_postings "
        "  (journal_entry_id, ledger_account_id, amount, posting_kind_id) "
        "VALUES (:e, :l, :a, :k)"
    ), {
        "e": entry_id, "l": ledger_id, "a": amount,
        "k": ref_cache.posting_kind_id(PostingKindEnum.TRANSFER),
    })


def _balanced_trigger_exists(session):
    """Return True iff the ``ck_account_postings_balanced`` trigger exists."""
    return session.execute(_db.text(
        "SELECT EXISTS (SELECT 1 FROM pg_trigger "
        " WHERE tgname = 'ck_account_postings_balanced')"
    )).scalar()


def _balanced_function_exists(session):
    """Return True iff ``budget.assert_journal_entry_balanced`` exists."""
    return session.execute(_db.text(
        "SELECT EXISTS (SELECT 1 FROM pg_proc p "
        " JOIN pg_namespace n ON n.oid = p.pronamespace "
        " WHERE n.nspname = 'budget' "
        "   AND p.proname = 'assert_journal_entry_balanced')"
    )).scalar()


@pytest.fixture()
def ledgers(app, db, seed_user):  # pylint: disable=unused-argument
    """Return (checking_ledger_id, savings_ledger_id, period_id).

    Runs in the ``db`` fixture's app context (no nested context) and returns
    plain ints.
    """
    savings = create_account_of_type(
        seed_user, _db.session, "Savings", "Trigger Savings",
    )
    _db.session.commit()
    checking_ledger = ledger_accounts_for_account(
        _db.session, seed_user["account"].id,
    )[0].id
    savings_ledger = ledger_accounts_for_account(
        _db.session, savings.id,
    )[0].id
    return checking_ledger, savings_ledger, seed_user["bootstrap_period"].id


@pytest.fixture()
def session_executor(db):  # pylint: disable=unused-argument
    """Return a callable suitable as ``apply_posting_infrastructure(executor)``.

    The closure runs each statement on the test session; tests commit
    explicitly when they want the change to persist.
    """
    return lambda sql: _db.session.execute(_db.text(sql))


# ---------------------------------------------------------------------------
# The deferred constraint trigger
# ---------------------------------------------------------------------------


class TestBalancedTrigger:
    """Reject single-leg / unbalanced; accept balanced; prove deferral."""

    def test_single_leg_entry_rejected_at_commit(self, app, db, seed_user, ledgers):
        """An entry with one leg trips ``COUNT >= 2`` at COMMIT.

        A transfer moves money between two accounts, so a one-legged entry is
        always malformed.  The single leg's ``amount`` is non-zero (so the
        per-row CHECK passes); the rejection therefore exercises the
        cross-row COUNT half of the deferred trigger.
        """
        checking_ledger, _savings, period_id = ledgers
        with app.app_context():
            entry_id = _insert_entry(_db.session, seed_user, period_id)
            _insert_leg(_db.session, entry_id, checking_ledger, Decimal("100.00"))
            with refused_by_database_rule(r"posting\(s\)"):
                _db.session.commit()
            _db.session.rollback()

    def test_unbalanced_two_legs_rejected_at_commit(self, app, db, seed_user, ledgers):
        """Two legs that do not sum to zero trip ``SUM = 0`` at COMMIT.

        Arithmetic: +100.00 and +50.00 sum to +150.00 (not zero), so the
        deferred trigger rejects the entry even though it has two non-zero
        legs.  This is the half that catches a mis-signed or mistyped leg.
        """
        checking_ledger, savings_ledger, period_id = ledgers
        with app.app_context():
            entry_id = _insert_entry(_db.session, seed_user, period_id)
            _insert_leg(_db.session, entry_id, checking_ledger, Decimal("100.00"))
            _insert_leg(_db.session, entry_id, savings_ledger, Decimal("50.00"))
            with refused_by_database_rule("sum to"):
                _db.session.commit()
            _db.session.rollback()

    def test_balanced_two_legs_accepted(self, app, db, seed_user, ledgers):
        """A balanced two-leg entry (-100 / +100) commits cleanly.

        Arithmetic: -100.00 + 100.00 = 0.00 and COUNT = 2, so both halves of
        the trigger pass.
        """
        checking_ledger, savings_ledger, period_id = ledgers
        with app.app_context():
            entry_id = _insert_entry(_db.session, seed_user, period_id)
            _insert_leg(_db.session, entry_id, checking_ledger, Decimal("-100.00"))
            _insert_leg(_db.session, entry_id, savings_ledger, Decimal("100.00"))
            _db.session.commit()
            total = _db.session.execute(_db.text(
                "SELECT SUM(amount) FROM budget.account_postings "
                " WHERE journal_entry_id = :e"
            ), {"e": entry_id}).scalar()
            assert total == Decimal("0.00")

    def test_check_is_deferred_first_leg_flush_does_not_raise(
        self, app, db, seed_user, ledgers,
    ):
        """The first leg's INSERT does not raise mid-transaction.

        Proves the trigger is DEFERRED, not immediate: after inserting the
        entry and only its first leg, a ``flush`` (which emits the INSERT SQL)
        must NOT raise -- an immediate ``COUNT >= 2`` trigger would fire right
        after that statement.  Completing the balanced pair then commits
        cleanly, confirming the check ran at COMMIT and passed.
        """
        checking_ledger, savings_ledger, period_id = ledgers
        with app.app_context():
            entry_id = _insert_entry(_db.session, seed_user, period_id)
            _insert_leg(_db.session, entry_id, checking_ledger, Decimal("-100.00"))
            # The proof: this flush sends the single-leg INSERT and must NOT
            # raise (deferred to commit, not fired after the statement).
            _db.session.flush()
            # Complete the pair and commit -- now balanced, so it passes.
            _insert_leg(_db.session, entry_id, savings_ledger, Decimal("100.00"))
            _db.session.commit()
            leg_count = _db.session.execute(_db.text(
                "SELECT count(*) FROM budget.account_postings "
                " WHERE journal_entry_id = :e"
            ), {"e": entry_id}).scalar()
            assert leg_count == 2


class TestBalancedTriggerOnUpdate:
    """The trigger fires AFTER UPDATE: an unbalancing raw edit is caught."""

    def test_unbalancing_update_rejected_at_commit(self, app, db, seed_user, ledgers):
        """A raw UPDATE that unbalances a committed entry is rejected.

        No legitimate UPDATE path exists (postings are append-only and the
        ORM guard blocks edits), but a raw-SQL amount edit that breaks the
        sum-to-zero invariant must still be caught.  Arithmetic: starting
        from -100/+100 (sum 0), bumping the positive leg by +50 yields
        -100/+150 (sum +50), which the AFTER UPDATE trigger rejects at commit.
        """
        checking_ledger, savings_ledger, period_id = ledgers
        with app.app_context():
            entry_id = _insert_entry(_db.session, seed_user, period_id)
            _insert_leg(_db.session, entry_id, checking_ledger, Decimal("-100.00"))
            _insert_leg(_db.session, entry_id, savings_ledger, Decimal("100.00"))
            _db.session.commit()

            _db.session.execute(_db.text(
                "UPDATE budget.account_postings SET amount = amount + 50 "
                " WHERE journal_entry_id = :e AND amount > 0"
            ), {"e": entry_id})
            with refused_by_database_rule("sum to"):
                _db.session.commit()
            _db.session.rollback()

    def test_balanced_update_accepted(self, app, db, seed_user, ledgers):
        """A raw UPDATE that keeps the entry balanced passes.

        Arithmetic: doubling both legs of -100/+100 gives -200/+200 (sum
        still 0), so the AFTER UPDATE trigger admits it.  Confirms the
        trigger gates on the invariant, not on the mere occurrence of an
        UPDATE.
        """
        checking_ledger, savings_ledger, period_id = ledgers
        with app.app_context():
            entry_id = _insert_entry(_db.session, seed_user, period_id)
            _insert_leg(_db.session, entry_id, checking_ledger, Decimal("-100.00"))
            _insert_leg(_db.session, entry_id, savings_ledger, Decimal("100.00"))
            _db.session.commit()

            _db.session.execute(_db.text(
                "UPDATE budget.account_postings SET amount = amount * 2 "
                " WHERE journal_entry_id = :e"
            ), {"e": entry_id})
            _db.session.commit()
            total = _db.session.execute(_db.text(
                "SELECT SUM(amount) FROM budget.account_postings "
                " WHERE journal_entry_id = :e"
            ), {"e": entry_id}).scalar()
            assert total == Decimal("0.00")


class TestBalancedTriggerNotOnDelete:
    """A CASCADE delete of an entry does NOT fire the trigger."""

    def test_cascade_delete_of_entry_does_not_abort(self, app, db, seed_user, ledgers):
        """Deleting an entry cascades its legs without tripping the trigger.

        The trigger is ``AFTER INSERT OR UPDATE`` only.  Deleting the
        ``journal_entries`` row cascades its ``account_postings`` away; were
        the trigger to fire on DELETE it would observe a transient
        ``COUNT < 2`` and abort a legitimate disposal.  The raw-SQL delete
        (the ORM guard blocks an ORM delete) must commit cleanly and leave no
        legs behind.
        """
        checking_ledger, savings_ledger, period_id = ledgers
        with app.app_context():
            entry_id = _insert_entry(_db.session, seed_user, period_id)
            _insert_leg(_db.session, entry_id, checking_ledger, Decimal("-100.00"))
            _insert_leg(_db.session, entry_id, savings_ledger, Decimal("100.00"))
            _db.session.commit()

            _db.session.execute(_db.text(
                "DELETE FROM budget.journal_entries WHERE id = :e"
            ), {"e": entry_id})
            _db.session.commit()  # must NOT raise
            remaining = _db.session.execute(_db.text(
                "SELECT count(*) FROM budget.account_postings "
                " WHERE journal_entry_id = :e"
            ), {"e": entry_id}).scalar()
            assert remaining == 0


# ---------------------------------------------------------------------------
# apply / remove idempotency
# ---------------------------------------------------------------------------


class TestApplyRemoveIdempotency:
    """``apply``/``remove`` are idempotent; the round trip restores state.

    The contract the migration, ``init_database``, and
    ``build_test_template`` all depend on.  The per-test ``db`` fixture
    re-clones the template (which already applied the infrastructure), so the
    trigger and function exist at the start of every test here.
    """

    def test_second_apply_does_not_raise(self, db, session_executor):
        """Re-applying over an already-applied infrastructure is a no-op."""
        apply_posting_infrastructure(session_executor)
        _db.session.commit()
        assert _balanced_trigger_exists(_db.session)
        assert _balanced_function_exists(_db.session)

    def test_remove_drops_trigger_and_function(self, db, session_executor):
        """``remove`` drops both the trigger and its function."""
        try:
            remove_posting_infrastructure(session_executor)
            _db.session.commit()
            assert not _balanced_trigger_exists(_db.session)
            assert not _balanced_function_exists(_db.session)
        finally:
            apply_posting_infrastructure(session_executor)
            _db.session.commit()

    def test_remove_then_apply_restores_state(self, db, session_executor):
        """Re-applying after a remove restores the trigger and function."""
        remove_posting_infrastructure(session_executor)
        _db.session.commit()
        apply_posting_infrastructure(session_executor)
        _db.session.commit()
        assert _balanced_trigger_exists(_db.session)
        assert _balanced_function_exists(_db.session)

    def test_remove_is_idempotent(self, db, session_executor):
        """Calling ``remove`` twice (IF EXISTS) does not raise."""
        try:
            remove_posting_infrastructure(session_executor)
            _db.session.commit()
            remove_posting_infrastructure(session_executor)
            _db.session.commit()
            assert not _balanced_trigger_exists(_db.session)
        finally:
            apply_posting_infrastructure(session_executor)
            _db.session.commit()

    def test_restored_trigger_still_enforces(self, app, db, seed_user, ledgers, session_executor):
        """After remove + apply, the trigger still rejects an unbalanced entry.

        Guards against a round trip that leaves the function in a degraded
        state where INSERTs succeed but the invariant is no longer enforced.
        """
        _checking, _savings, _period = ledgers
        with app.app_context():
            remove_posting_infrastructure(session_executor)
            _db.session.commit()
            apply_posting_infrastructure(session_executor)
            _db.session.commit()

            checking_ledger, savings_ledger, period_id = ledgers
            entry_id = _insert_entry(_db.session, seed_user, period_id)
            _insert_leg(_db.session, entry_id, checking_ledger, Decimal("100.00"))
            _insert_leg(_db.session, entry_id, savings_ledger, Decimal("50.00"))
            with refused_by_database_rule("sum to"):
                _db.session.commit()
            _db.session.rollback()
