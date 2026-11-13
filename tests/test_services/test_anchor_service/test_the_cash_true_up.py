"""The cash true-up door: what it commits, ruling R-EQ's unchanged re-submission, an IntegrityError
that propagates, the module's public surface and its anchor tables' no-content-key rule, and the
amortizing-kind guard.
"""

from decimal import Decimal
from unittest.mock import patch

import pytest
import sqlalchemy as sa
from sqlalchemy.exc import IntegrityError

from app.models.account import Account, AccountAnchorHistory
from app.models.ref import AccountType
from app.models.transaction_entry import TransactionEntry
from app.services import account_service, anchor_service
from app.services.anchor_service import AnchorTrueUpOutcome, apply_anchor_true_up
from tests._test_helpers import current_pay_period
from app.services import cash_ledger

from ._helpers import _make_savings_account, _make_projected_expense_with_past_dated_entry


class TestApplyAnchorTrueUpCommitted:
    """COMMITTED outcome: helper writes balance + history and commits."""

    def test_savings_true_up_commits_without_touching_entries(
        self, app, db, seed_user, seed_periods_today,
    ):
        """Non-checking account: helper commits without touching entries.

        Setup: a Savings account anchored at periods[0]; one
        projected checking-account expense with an unobserved past-
        dated debit entry (created on the seed_user checking account,
        NOT on the savings account being trued up).

        Hand-check: after ``apply_anchor_true_up`` on the savings
        account, the outcome is COMMITTED, the savings anchor balance
        is the new value, exactly one new history row exists for the
        savings account, and the unrelated checking purchase still has
        NO recorded posting day.
        """
        with app.app_context():
            savings = _make_savings_account(
                seed_user, anchor_balance="500.00",
            )
            db.session.commit()

            current_period = current_pay_period(
                seed_user["user"].id
            )
            # An unrelated past-dated checking debit -- must stay
            # uncleared because the savings true-up does not run the
            # entry reconcile.
            entry = _make_projected_expense_with_past_dated_entry(
                seed_user, current_period, amount="50.00",
            )
            history_count_before = (
                db.session.query(AccountAnchorHistory)
                .filter_by(account_id=savings.id)
                .count()
            )

            outcome = apply_anchor_true_up(
                account=savings,
                new_balance=Decimal("750.00"),
            ).outcome

            assert outcome is AnchorTrueUpOutcome.COMMITTED

            db.session.expire_all()
            reloaded = db.session.get(Account, savings.id)
            assert cash_ledger.resolve_anchor(reloaded).balance == Decimal("750.00")

            history_count_after = (
                db.session.query(AccountAnchorHistory)
                .filter_by(account_id=savings.id)
                .count()
            )
            assert history_count_after == history_count_before + 1, (
                "Savings true-up must append exactly one history row."
            )

            # The purchase is untouched.  This was true before plan step S1-c
            # too -- the bulk clear was checking-only -- so this arm is a
            # rename rather than a re-ruling; the checking test beside it is
            # the one whose expectation INVERTED.
            entry_after = db.session.get(TransactionEntry, entry.id)
            assert entry_after.settled_on is None, (
                "A true-up records no posting day on any account's "
                "purchases (ruling R-DH (d))."
            )

    def test_checking_true_up_records_no_posting_day(
        self, app, db, seed_user, seed_periods_today,
    ):
        """RE-RULED: a checking true-up touches NO entry (ruling R-DH (d)).

        Setup: the seed_user account is Checking; one projected
        envelope expense with one unobserved past-dated debit entry of
        ``$50.00`` on the current period.

        Hand-check: after ``apply_anchor_true_up`` on the seed_user
        checking account, the outcome is COMMITTED, the anchor
        balance is the new value, exactly one new history row exists,
        AND the purchase still has NO recorded posting day.

        **This test asserted the OPPOSITE until plan step S1-c**, and the
        inversion is the point of the step.  A bulk ``UPDATE`` flipped
        ``is_cleared = TRUE`` on every entry dated on or before the SERVER's
        today at every true-up, so whether a purchase counted as reconciled
        was decided by the order two buttons were pressed: record then true up
        and it cleared, true up then record and it never did.  The engine now
        never guesses a posting day; the user supplies it by ticking the
        purchase off a statement (``reconcile_service.record_settled_days``, whose
        own tests live with that service and whose ROUTE is graded in
        ``test_routes/test_accounts/``'s three ``test_reconcile_*.py`` parts).

        That reconcile step is deliberately a SEPARATE request: folding it in
        here would put it inside the transaction an UNCHANGED submission rolls
        back and reports as idempotent success -- so a re-assert of the
        governing balance would silently discard every reconciliation just made
        while the UI said it saved.

        Re-fetches the account via ``db.session.get`` so it is
        attached to the current scoped session.  The conftest's ``db``
        fixture removes/disposes the session at the start of each
        test, so the cached ``seed_user["account"]`` reference is in
        a stale session and would not flush correctly otherwise.  This
        mirrors the route pattern (every route opens with
        ``db.session.get(Account, account_id)``).
        """
        with app.app_context():
            account = db.session.get(Account, seed_user["account"].id)
            current_period = current_pay_period(
                seed_user["user"].id
            )
            entry = _make_projected_expense_with_past_dated_entry(
                seed_user, current_period, amount="50.00",
            )
            history_count_before = (
                db.session.query(AccountAnchorHistory)
                .filter_by(account_id=account.id)
                .count()
            )

            outcome = apply_anchor_true_up(
                account=account,
                new_balance=Decimal("2500.00"),
            ).outcome

            assert outcome is AnchorTrueUpOutcome.COMMITTED

            db.session.expire_all()
            reloaded = db.session.get(Account, account.id)
            assert cash_ledger.resolve_anchor(reloaded).balance == Decimal("2500.00")

            history_count_after = (
                db.session.query(AccountAnchorHistory)
                .filter_by(account_id=account.id)
                .count()
            )
            assert history_count_after == history_count_before + 1

            entry_after = db.session.get(TransactionEntry, entry.id)
            assert entry_after.settled_on is None, (
                "A true-up must record NO posting day: whether the bank has "
                "taken a purchase is not derivable from a balance reading "
                "(ruling R-DH (d) / plan step S1-c)."
            )


class TestApplyAnchorTrueUpUnchanged:
    """UNCHANGED outcome: ruling R-EQ's idempotent success.

    An assertion is refused only when it changes nothing.  The write door
    compares the submission against the assertion that currently GOVERNS
    (``cash_ledger.resolve_anchor``) and appends only when they differ.

    **This class graded a UNIQUE INDEX until plan step X-f1c4b**
    (``uq_anchor_history_account_period_balance_day``, dropped by migration
    ``a3f6c1d84b90``).  The index answered the first two cases below the same
    way; it answered the THIRD wrongly, and no test had ever asked it.
    """

    def test_double_call_same_balance_returns_unchanged(
        self, app, db, seed_user, seed_periods_today,
    ):
        """Two identical helper calls produce exactly one history row.

        The retry case the rule exists for.  ``apply_anchor_true_up`` called
        twice with the same balance on the same civil day returns ``COMMITTED``
        then ``UNCHANGED``, and the on-disk history shows exactly one row at
        that balance (plus whatever origination/prior history the fixture
        wrote).

        Account re-fetched via ``db.session.get`` for current-session
        attachment (see sibling-class rationale).
        """
        with app.app_context():
            account = db.session.get(Account, seed_user["account"].id)

            outcome_first = apply_anchor_true_up(
                account=account,
                new_balance=Decimal("1234.56"),
            ).outcome
            assert outcome_first is AnchorTrueUpOutcome.COMMITTED

            # Second call: the same balance for the same civil day is now what
            # governs, so there is nothing to append.
            outcome_second = apply_anchor_true_up(
                account=account,
                new_balance=Decimal("1234.56"),
            ).outcome
            assert outcome_second is AnchorTrueUpOutcome.UNCHANGED

            db.session.expire_all()
            rows_at_duplicate_balance = (
                db.session.query(AccountAnchorHistory)
                .filter_by(
                    account_id=account.id,
                    anchor_balance=Decimal("1234.56"),
                )
                .all()
            )
            assert len(rows_at_duplicate_balance) == 1, (
                f"A repeated submission must append nothing: expected exactly "
                f"one history row at that balance, found "
                f"{len(rows_at_duplicate_balance)}."
            )

    def test_same_day_different_balance_both_commit(
        self, app, db, seed_user, seed_periods_today,
    ):
        """Same-day true-ups with different balances both succeed.

        A legitimate same-day correction (the user noticed an error and
        re-trued at a different amount) MUST NOT be blocked.  Both calls
        return ``COMMITTED``.

        Account re-fetched via ``db.session.get`` for current-session
        attachment (see sibling-class rationale).
        """
        with app.app_context():
            account = db.session.get(Account, seed_user["account"].id)

            outcome_a = apply_anchor_true_up(
                account=account,
                new_balance=Decimal("1000.00"),
            ).outcome
            outcome_b = apply_anchor_true_up(
                account=account,
                new_balance=Decimal("1100.00"),
            ).outcome
            assert outcome_a is AnchorTrueUpOutcome.COMMITTED
            assert outcome_b is AnchorTrueUpOutcome.COMMITTED

            db.session.expire_all()
            balances = {
                row.anchor_balance for row in
                db.session.query(AccountAnchorHistory)
                .filter_by(account_id=account.id)
                .all()
            }
            # The fixture's origination row already sits at $1000.00 for an
            # earlier day, so the first call above appends nothing new to this
            # SET while the $1100.00 row is added.  Assert both balances are
            # represented.
            assert Decimal("1100.00") in balances, (
                "$1100.00 same-day correction must commit a history row."
            )
            assert Decimal("1000.00") in balances, (
                "$1000.00 history row must survive the same-day double."
            )

    def test_reasserting_a_superseded_balance_is_recorded(
        self, app, db, seed_user, seed_periods_today,
    ):
        """Re-asserting a balance that was corrected away is a real change.

        **The defect ruling R-EQ exists for, and the deleted unique index's
        negative control.**  Three submissions on one civil day:

          1. ``$500.00`` -- committed.
          2. ``$600.00`` -- committed (the user corrects themselves).
          3. ``$500.00`` -- the user decides the first reading was right.

        The third changes what governs from ``$600.00`` to ``$500.00``, so it
        must be recorded and the resolver must answer ``$500.00``.  Under
        ``uq_anchor_history_account_period_balance_day`` it collided with row 1,
        was swallowed as idempotent success, and every surface kept rendering
        ``$600.00`` while the app reported the correction saved.  Account 1 on
        the 2026-08-04 production clone carries 2-3 assertions on 3 of its 50
        assertion days, so this sequence is an ordinary bookkeeping session.
        """
        with app.app_context():
            account = db.session.get(Account, seed_user["account"].id)

            assert apply_anchor_true_up(
                account=account, new_balance=Decimal("500.00"),
            ).outcome is AnchorTrueUpOutcome.COMMITTED
            assert apply_anchor_true_up(
                account=account, new_balance=Decimal("600.00"),
            ).outcome is AnchorTrueUpOutcome.COMMITTED
            assert apply_anchor_true_up(
                account=account, new_balance=Decimal("500.00"),
            ).outcome is AnchorTrueUpOutcome.COMMITTED, (
                "Re-asserting a superseded balance CHANGES what governs "
                "($600.00 -> $500.00), so it is a fact and must be recorded."
            )

            db.session.expire_all()
            reloaded = db.session.get(Account, account.id)
            assert cash_ledger.resolve_anchor(reloaded).balance == Decimal(
                "500.00"
            ), (
                "The correction must be what governs.  A content-keyed unique "
                "index answered $600.00 here while reporting success."
            )
            rows_at_500 = (
                db.session.query(AccountAnchorHistory)
                .filter_by(
                    account_id=account.id, anchor_balance=Decimal("500.00"),
                )
                .all()
            )
            assert len(rows_at_500) == 2, (
                f"Both $500.00 assertions are facts -- the original and the "
                f"correction back to it -- so the append-only history holds "
                f"two, not {len(rows_at_500)}."
            )


class TestApplyAnchorTrueUpReraisesUnknownIntegrityError:
    """An unexpected ``IntegrityError`` propagates rather than being swallowed.

    **The ``except IntegrityError`` block this graded was DELETED at plan step
    X-f1c4b** with the index that produced it.  The contract survives the
    deletion and is what this now grades directly: nothing on the true-up path
    may convert a database-level failure into an outcome the route renders as
    success.  Kept rather than deleted because the cost of re-growing such a
    handler is a silent write loss reported as a save, which is the exact defect
    ruling R-EQ was ruled on.
    """

    def test_integrity_error_from_the_commit_is_not_swallowed(
        self, app, db, seed_user, seed_periods_today,
    ):
        """An ``IntegrityError`` raised by the door's commit reaches the caller.

        Engineered at the commit rather than at a real constraint because
        every constraint the true-up can now violate is a programming error
        with no reachable input: the decision that used to end in a unique
        violation is made before anything is staged.  Patching the call where
        a deferred constraint would raise keeps the contract testable without
        asserting on a specific constraint.  It was engineered at the posting
        re-sync, the door's other call into the database, until plan#3076 deleted it.
        """
        with app.app_context():
            account = db.session.get(Account, seed_user["account"].id)

            with patch.object(
                db.session, "commit",
                side_effect=IntegrityError("stmt", {}, Exception("boom")),
            ):
                with pytest.raises(IntegrityError):
                    apply_anchor_true_up(
                        account=account,
                        new_balance=Decimal("2222.22"),
                    )

            db.session.rollback()
            reloaded = db.session.get(Account, account.id)
            assert cash_ledger.resolve_anchor(reloaded).balance == Decimal(
                "1000.00"
            ), (
                "The failed true-up must not have committed: the seed "
                "origination's $1,000.00 still governs, because the $2,222.22 "
                "assertion was staged in the transaction the IntegrityError "
                "aborted.  Asserting the SURVIVING figure, not merely 'not the "
                "new one' -- a negative passes for any wrong value."
            )


# ``TestApplyAnchorTrueUpStaleConflict.test_helper_returns_stale_conflict_and_rolls_back``
# was DELETED at plan step X-f1c3c (ruling R-EN).  It graded
# ``AnchorTrueUpOutcome.STALE_CONFLICT``, which the enum no longer has: a
# true-up appends an assertion and never UPDATEs the ``accounts`` row that
# ``version_id`` guards, so ``StaleDataError`` is structurally unreachable on
# this path.  The append-only contract that replaced it is graded by
# ``test_accounts.test_stale_data.TestTrueUpIsAppendOnly`` (a stale form still
# records) and by ``test_race_conditions.TestConcurrentAnchorUpdate`` (both
# concurrent requests 200, both assertions survive).


#: Every unique index on either anchor table, with the columns it keys on --
#: the catalog query behind :func:`_content_keys_on_anchor_tables`.  Read from
#: ``pg_index`` rather than ``pg_indexes.indexdef`` because the question is
#: about the COLUMN SET and a text ``LIKE`` over a rendered definition cannot
#: ask it: ``indexdef`` for ``(account_id, id)`` and for
#: ``(account_id, anchor_balance)`` differ only in words.
_ANCHOR_UNIQUE_INDEX_SQL = """
    SELECT c.relname AS tablename,
           i.relname AS indexname,
           array_agg(a.attname) AS columns
    FROM pg_index x
    JOIN pg_class c ON c.oid = x.indrelid
    JOIN pg_class i ON i.oid = x.indexrelid
    JOIN pg_namespace n ON n.oid = c.relnamespace
    JOIN pg_attribute a ON a.attrelid = c.oid AND a.attnum = ANY(x.indkey)
    WHERE n.nspname = 'budget'
      AND c.relname IN ('account_anchor_history', 'loan_anchor_events')
      AND x.indisunique
      AND NOT x.indisprimary
    GROUP BY c.relname, i.relname
"""


#: The columns an ASSERTION is made of -- ruling R-ES's ``(account, day,
#: balance)`` -- which are the values a transport retry and a deliberate
#: re-assertion SHARE, and so the only columns a unique key may not be built
#: over.  The loan twin's are its own three.
_OBSERVATION_COLUMNS = frozenset({
    "account_id", "observed_on", "anchor_balance",
    "anchor_date",
})


def _content_keys_on_anchor_tables(session):
    """Return the anchor tables' unique indexes that key on OBSERVATION values.

    Ruling **R-EQ**'s predicate, and the whole of it: an index over the
    values a transport retry and a deliberate re-assertion SHARE can refuse
    the correction, because the two are byte-identical by construction.  Two
    shapes are therefore NOT content keys, and each is named rather than
    excused: one that CONTAINS the primary key cannot refuse anything (the
    clearing links' target ``uq_anchor_history_account_id``), and one over a
    foreign key to the row's SOURCE keys an identity rather than a value --
    ``uq_anchor_history_statement_import`` says an import places its figure
    on at most one day, and a re-import is a NEW import row with its own id,
    so no legitimate act can collide on it (plan step ``balance:X-bj-1``,
    developer ruling 2026-09-16).  What R-EQ forbids is exactly a key built
    from :data:`_OBSERVATION_COLUMNS` and nothing else.

    Args:
        session: The test session, inside an app context.

    Returns:
        ``{(tablename, indexname), ...}`` for every offending index -- empty
        when the schema holds R-EQ.
    """
    return {
        (row.tablename, row.indexname)
        for row in session.execute(sa.text(_ANCHOR_UNIQUE_INDEX_SQL))
        if set(row.columns) <= _OBSERVATION_COLUMNS
    }


class TestApplyAnchorTrueUpModuleContract:
    """Pins the public surface of the module so renames are caught."""

    def test_outcome_enum_has_exactly_two_members(self):
        """The outcome enum is the route's switch discriminant; pin its size.

        Adding a new outcome would require route-side handling for every
        consumer; this fails loud if the enum grows without a coordinated
        route update.

        **It pinned THREE members until plan step X-f1c3c** and was briefly
        deleted there, on the mistaken ground that it graded
        ``STALE_CONFLICT`` -- which ruling R-EN did delete.  It does not: it
        grades the member SET, and that contract is fully alive with two
        members.  Restored after an adversarial review caught the deletion,
        because "the enum shrank" and "nobody is watching the enum any more"
        are not distinguishable from a green suite.
        """
        assert {m.name for m in AnchorTrueUpOutcome} == {
            "COMMITTED", "UNCHANGED",
        }

    def test_neither_anchor_table_carries_a_uniqueness_guard(self, app, db):
        """Ruling R-EQ's structural claim, asserted against the LIVE schema.

        The duplicate rule is a write-door comparison, and re-adding a unique
        index over either anchor table's own values would silently restore the
        false refusal ``TestApplyAnchorTrueUpUnchanged`` grades -- the door's
        compare would pass, the INSERT would collide, and the caller would see
        an ``IntegrityError`` on a legitimate correction.

        Asserted against the LIVE catalog rather than ``__table_args__`` because
        the model and the database can disagree: a model-only assertion passes
        on a database whose index was never dropped, which is the exact state
        migration ``a3f6c1d84b90`` exists to leave behind.

        **It grades a CONTENT key rather than "any unique index", and that
        narrowing is plan step X-f3a-1's** (developer, 2026-08-14).  This
        asserted the EMPTY SET until ``uq_anchor_history_account_id`` -- the
        ``(account_id, id)`` superkey the clearing links' composite foreign keys
        must target -- and the blanket form would have refused it while R-EQ has
        nothing to say about it: an index containing the primary key can reject
        NO row, so it cannot refuse a re-assertion.  What R-EQ forbids is a key
        over values a transport retry and a deliberate correction SHARE, which
        is exactly a key that OMITS the primary key.
        :func:`_content_keys_on_anchor_tables` is that predicate, and the test
        below plants the deleted index to show it still fires.  **A second
        narrowing at plan step ``balance:X-bj-1``** (developer, 2026-09-16):
        a key over a foreign key to the row's SOURCE
        (``uq_anchor_history_statement_import``) is identity, not content --
        a re-import is a new import row -- so the predicate names the
        observation columns R-EQ is about rather than "anything without the
        primary key".
        """
        with app.app_context():
            offenders = _content_keys_on_anchor_tables(db.session)
            assert offenders == set(), (
                f"An anchor table has regrown a uniqueness guard: "
                f"{sorted(offenders)}.  Ruling R-EQ puts the duplicate "
                f"rule at the write door, which can compare against what "
                f"governs; an index over the row's values cannot, and refuses "
                f"a legitimate re-assertion."
            )

    def test_the_content_key_predicate_fires_on_the_index_r_eq_deleted(
        self, app, db,
    ):
        """Plant the index R-EQ deleted; see the predicate report it.

        The firing control for the test above.  A predicate that answers "no
        content key" is worth exactly what its ability to FIND one is worth, and
        the sharpened form deliberately admits superkeys -- so the case that
        matters is whether the index ruling R-EQ actually deleted would still be
        caught.

        The planted key is that index's shape:
        ``(account_id, anchor_balance, observed_on)``, the three values a
        network retry and a deliberate re-assertion of an earlier figure share
        by construction.  Dropped in a ``finally`` so the per-test database is
        handed back unchanged whatever the assertion does.
        """
        with app.app_context():
            db.session.execute(sa.text(
                "CREATE UNIQUE INDEX uq_anchor_history_content_key_probe "
                "ON budget.account_anchor_history "
                "(account_id, anchor_balance, observed_on)"
            ))
            try:
                offenders = _content_keys_on_anchor_tables(db.session)
                assert (
                    "account_anchor_history",
                    "uq_anchor_history_content_key_probe",
                ) in offenders, (
                    "The content-key predicate did not report a planted key "
                    "over (account_id, anchor_balance, observed_on) -- the "
                    "exact index ruling R-EQ deleted.  A green "
                    "test_neither_anchor_table_carries_a_uniqueness_guard "
                    "would then be worth nothing."
                )
            finally:
                db.session.execute(sa.text(
                    "DROP INDEX budget.uq_anchor_history_content_key_probe"
                ))


class TestApplyAnchorTrueUpKindGate:
    """The D4 / A1 amortizing-kind guard on the CASH true-up entry point."""

    def test_refuses_amortizing_loan_before_staging(
        self, app, db, seed_user, seed_periods_today,
    ):
        """An amortizing account raises, with NOTHING staged or written.

        Finding B-15: this entry point wrote
        ``accounts.current_anchor_balance`` for a loan -- a second,
        stored, never-reconciled loan balance.  That COLUMN is deleted (ruling
        R-EH, plan step X-f1c3c) and the sentence above is historical, naming
        what the guard was written against; a mechanical rename over this file
        had rewritten it to say the guard prevented writing
        ``cash_ledger.resolve_anchor(accounts).balance``, which is a read and
        was never what B-15 was about.

        The guard fires before ``stage_anchor_true_up``, so the session holds
        no pending mutation and no history row: the RESOLVED balance, the
        history count, and the session's dirty/new sets are all unchanged.
        """
        with app.app_context():
            mortgage_type = db.session.query(AccountType).filter_by(
                name="Mortgage",
            ).one()
            loan = account_service.create_account(
                account_service.AccountSpec(
                    user_id=seed_user["user"].id,
                    account_type_id=mortgage_type.id,
                    name="Kind Gate Loan",
                    anchor_balance=Decimal("0"),
                ),
            )
            db.session.commit()
            resolved_before = cash_ledger.resolve_anchor(loan).balance
            history_before = (
                db.session.query(AccountAnchorHistory)
                .filter_by(account_id=loan.id).count()
            )

            with pytest.raises(
                anchor_service.AmortizingAccountAnchorError,
            ) as excinfo:
                apply_anchor_true_up(
                    account=loan,
                    new_balance=Decimal("1.00"),
                )

            # The message names the correct path for the caller.
            assert "apply_loan_anchor_true_up" in str(excinfo.value)
            # Raised BEFORE staging: session clean, nothing written.
            assert not db.session.dirty
            assert not db.session.new
            assert cash_ledger.resolve_anchor(loan).balance == resolved_before
            assert (
                db.session.query(AccountAnchorHistory)
                .filter_by(account_id=loan.id).count()
            ) == history_before
