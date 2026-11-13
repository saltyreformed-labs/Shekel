"""What the mark-done, inline-edit PATCH and cancel doors move on the worked-out ledger: a settle
books its cash split, each PATCH field moves what it names, and a cancel moves nothing.
"""

from __future__ import annotations

from decimal import Decimal

from app import ref_cache
from app.enums import LedgerAccountClassEnum, StatusEnum
from app.extensions import db
from app.services.ledger_lines import linked_key
from tests._ledger_reads import account_net
from tests._test_helpers import add_txn

from ._helpers import _category_chart_key, _ledger_total, _family_nets, _assert_reconciles


# ---------------------------------------------------------------------------
# mark-done route: settling books the confirmed effect, actuals included
# ---------------------------------------------------------------------------


class TestMarkDoneRoutePostsTransaction:
    """A transaction the mark-done route settles books its cash split."""

    def test_plain_expense_mark_done_posts(
        self, app, auth_client, seed_user, seed_periods,
    ):
        """A $50 Groceries expense, marked Paid, posts -50 / +50.

        Arithmetic (plan Section 1): a plain expense has no entries, so the
        effect is ``effective_amount`` (50) with the expense sign -- cash leg
        -50.00 (money leaving Checking, a credit) and Groceries-Expense leg
        +50.00 (the expense lands, a debit).  -50 + 50 = 0.
        """
        with app.app_context():
            scenario_id = seed_user["scenario"].id
            checking = seed_user["account"]
            txn = add_txn(
                db.session, seed_user, seed_periods[0], "Groceries", "50.00",
                category_key="Groceries",
            )
            db.session.commit()
            txn_id = txn.id

            resp = auth_client.post(f"/transactions/{txn_id}/mark-done")
            assert resp.status_code == 200

            assert _family_nets(txn_id, scenario_id) == {seed_periods[0].id: {
                linked_key(checking): Decimal("-50.00"),
                _category_chart_key(
                    seed_user, "Groceries", LedgerAccountClassEnum.EXPENSE,
                ): Decimal("50.00"),
            }}
            _assert_reconciles(scenario_id, checking)

    def test_income_mark_done_posts(
        self, app, auth_client, seed_user, seed_periods,
    ):
        """A $2000 Salary income, marked Received, posts +2000 / -2000.

        Arithmetic (plan Section 1): income has no entries, so the effect is
        ``effective_amount`` (2000) with the INCOME sign -- cash leg +2000.00
        (money entering Checking, a debit) and Salary-Income leg -2000.00 (a
        credit).  The sign follows the transaction TYPE, never the account class.
        """
        with app.app_context():
            scenario_id = seed_user["scenario"].id
            checking = seed_user["account"]
            txn = add_txn(
                db.session, seed_user, seed_periods[0], "Salary", "2000.00",
                is_income=True, category_key="Salary",
            )
            db.session.commit()
            txn_id = txn.id

            resp = auth_client.post(f"/transactions/{txn_id}/mark-done")
            assert resp.status_code == 200

            assert _family_nets(txn_id, scenario_id) == {seed_periods[0].id: {
                linked_key(checking): Decimal("2000.00"),
                _category_chart_key(
                    seed_user, "Salary", LedgerAccountClassEnum.INCOME,
                ): Decimal("-2000.00"),
            }}
            _assert_reconciles(scenario_id, checking)

    def test_mark_done_manual_actual_posts_actual_not_estimate(
        self, app, auth_client, seed_user, seed_periods,
    ):
        """A manual ``actual_amount`` posts the ACTUAL, not the estimate (2.8b HIGH).

        Arithmetic: the row is estimated at 50 but settled with a manual actual
        of 75, and the settle records its covering movement at that actual:
        cash leg -75.00, not -50.00.  (The store's reconcile ran AFTER
        ``actual_amount`` was applied, until plan task plan#3097, so that it was
        not fired at the status flip, which would have booked the stale 50.)
        """
        with app.app_context():
            checking = seed_user["account"]
            txn = add_txn(
                db.session, seed_user, seed_periods[0], "Groceries", "50.00",
                category_key="Groceries",
            )
            db.session.commit()
            txn_id = txn.id

            resp = auth_client.post(
                f"/transactions/{txn_id}/mark-done",
                data={"settled_amount": "75.00"},
            )
            assert resp.status_code == 200

            assert _family_nets(txn_id, seed_user["scenario"].id) == {seed_periods[0].id: {
                linked_key(checking): Decimal("-75.00"),
                _category_chart_key(
                    seed_user, "Groceries", LedgerAccountClassEnum.EXPENSE,
                ): Decimal("75.00"),
            }}
            _assert_reconciles(seed_user["scenario"].id, checking)


# ---------------------------------------------------------------------------
# PATCH route: settle / recategorize / revert / metadata-only
# ---------------------------------------------------------------------------


class TestPatchPostingLifecycle:
    """What the inline-edit PATCH route's fields move on the ledger."""

    def test_settle_and_recategorize_patch_posts_new_category(
        self, app, auth_client, seed_user, seed_periods,
    ):
        """A settle+recategorize PATCH books the NEW category, not the old one.

        A Projected Groceries expense is settled and recategorized to Rent in
        ONE PATCH.  ``category_id`` is applied in the setattr loop and
        ``status_id`` through the seam, and the read books the row's current
        ``category_id`` = Rent: cash -50.00 / Rent-Expense +50.00, with the
        Groceries ledger never touched.  (While the store's reconcile ran LAST,
        until plan task plan#3097, its ordering proof for a field applied
        *after* the seam was the manual-``actual_amount`` test above;
        ``category_id`` here is set pre-seam, so this pins the recategorize-on-
        settle OUTCOME rather than isolating that ordering.)
        """
        with app.app_context():
            checking = seed_user["account"]
            done_id = ref_cache.status_id(StatusEnum.DONE)
            rent_id = seed_user["categories"]["Rent"].id
            txn = add_txn(
                db.session, seed_user, seed_periods[0], "Groceries", "50.00",
                category_key="Groceries",
            )
            db.session.commit()
            txn_id = txn.id

            resp = auth_client.patch(
                f"/transactions/{txn_id}",
                data={"status_id": str(done_id), "category_id": str(rent_id)},
            )
            assert resp.status_code == 200

            assert _ledger_total(seed_user, _category_chart_key(
                seed_user, "Rent", LedgerAccountClassEnum.EXPENSE,
            )) == Decimal("50.00")
            assert _ledger_total(seed_user, _category_chart_key(
                seed_user, "Groceries", LedgerAccountClassEnum.EXPENSE,
            )) == Decimal("0.00")
            assert account_net(
                db.session, checking.id, seed_user["scenario"].id,
            ) == Decimal("950.00")
            _assert_reconciles(seed_user["scenario"].id, checking)

    def test_revert_and_recategorize_reconciles(
        self, app, auth_client, seed_user, seed_periods,
    ):
        """Revert+recategorize clears the OLD category, not the new one (2.8 CRITICAL).

        A Paid Groceries expense (posted -50 / +50 Groceries) is reverted to
        Projected AND recategorized to Rent in ONE PATCH (the lock lifts on the
        revert).  Groceries nets back to zero (+50 -> 0), Rent stays untouched,
        and Checking nets back to its $1,000.00 opening.  A later re-settle then
        posts cleanly to Rent.
        """
        with app.app_context():
            checking = seed_user["account"]
            scenario_id = seed_user["scenario"].id
            done_id = ref_cache.status_id(StatusEnum.DONE)
            projected_id = ref_cache.status_id(StatusEnum.PROJECTED)
            rent_id = seed_user["categories"]["Rent"].id
            txn = add_txn(
                db.session, seed_user, seed_periods[0], "Groceries", "50.00",
                category_key="Groceries",
            )
            db.session.commit()
            txn_id = txn.id
            auth_client.post(f"/transactions/{txn_id}/mark-done")
            groceries_ledger = _category_chart_key(
                seed_user, "Groceries", LedgerAccountClassEnum.EXPENSE,
            )
            assert _ledger_total(seed_user, groceries_ledger) == Decimal("50.00")

            # Revert + recategorize in one PATCH (the lock lifts on the revert).
            resp = auth_client.patch(
                f"/transactions/{txn_id}",
                data={
                    "status_id": str(projected_id),
                    "category_id": str(rent_id),
                },
            )
            assert resp.status_code == 200
            rent_ledger = _category_chart_key(
                seed_user, "Rent", LedgerAccountClassEnum.EXPENSE,
            )
            # OLD category reversed to zero; NEW category never touched yet.
            assert _ledger_total(seed_user, groceries_ledger) == Decimal("0.00")
            assert _ledger_total(seed_user, rent_ledger) == Decimal("0.00")
            assert account_net(
                db.session, checking.id, scenario_id,
            ) == Decimal("1000.00")
            _assert_reconciles(scenario_id, checking)

            # Re-settle: now it posts to Rent (the current category).
            resp = auth_client.patch(
                f"/transactions/{txn_id}",
                data={"status_id": str(done_id)},
            )
            assert resp.status_code == 200
            assert _ledger_total(seed_user, rent_ledger) == Decimal("50.00")
            assert _ledger_total(seed_user, groceries_ledger) == Decimal("0.00")
            _assert_reconciles(scenario_id, checking)

    def test_notes_only_edit_posts_nothing(
        self, app, auth_client, seed_user, seed_periods,
    ):
        """A metadata-only PATCH on a Paid row leaves the ledger untouched.

        ``notes`` moves no record the worked-out ledger reads, so Checking
        still nets $950.00.  The PATCH door gated its store reconcile on
        ``_POSTING_RELEVANT_FIELDS`` until plan task plan#3097 deleted both;
        what this grades is the ledger, which never depended on the gate.
        """
        with app.app_context():
            checking = seed_user["account"]
            txn = add_txn(
                db.session, seed_user, seed_periods[0], "Groceries", "50.00",
                category_key="Groceries",
            )
            db.session.commit()
            txn_id = txn.id
            auth_client.post(f"/transactions/{txn_id}/mark-done")

            resp = auth_client.patch(
                f"/transactions/{txn_id}",
                data={"notes": "reconciled at the bank"},
            )
            assert resp.status_code == 200
            assert account_net(
                db.session, checking.id, seed_user["scenario"].id,
            ) == Decimal("950.00")


# ---------------------------------------------------------------------------
# cancel route: never crosses the settled boundary -> posts nothing
# ---------------------------------------------------------------------------


class TestCancelPostsNothing:
    """Cancelling a Projected transaction is an idempotent ledger no-op."""

    def test_cancel_projected_posts_nothing(
        self, app, auth_client, seed_user, seed_periods,
    ):
        """Cancelling a never-posted Projected expense books nothing.

        Cancelled is non-settled and reachable only from Projected, so the row
        moves no money: Checking stays at its $1,000.00 opening.
        """
        with app.app_context():
            txn = add_txn(
                db.session, seed_user, seed_periods[0], "Groceries", "50.00",
                category_key="Groceries",
            )
            db.session.commit()
            txn_id = txn.id

            resp = auth_client.post(f"/transactions/{txn_id}/cancel")
            assert resp.status_code == 200
            assert account_net(
                db.session, seed_user["account"].id, seed_user["scenario"].id,
            ) == Decimal("1000.00")
