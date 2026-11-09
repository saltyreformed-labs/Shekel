"""
Shekel Budget App -- Mark-Paid Entry Integration and Status Guard Tests

Tests for Commit 5: auto-populating actual_amount from entries on
mark-paid, Credit status guard on entry-capable transactions, and
post-paid entry mutation actual_amount updates.

Each test verifies exact Decimal values.  Arithmetic is documented
inline so a reviewer can verify by hand.
"""

from datetime import date
from decimal import Decimal

import pytest

from app.extensions import db
from app.models.ref import TransactionType
from app.models.transaction import Transaction
from app.models.transaction_entry import TransactionEntry
from app import ref_cache
from app.enums import MovementFigureSourceEnum, StatusEnum
from app.exceptions import ValidationError

from app.services.credit_workflow import get_active_payback
from app.services.row_valuation import settled_figure
from tests._test_helpers import (
    typed,
    an_entered_day,
    figure_source_columns,
    freeze_today,
    generate_row_of,
    make_expense_template,
    one_off_row_of,
    purchases_of,
)


@pytest.fixture(autouse=True)
def _freeze_today_inside_seed_range(monkeypatch):
    """Freeze today to date(2026, 3, 20) so seed_periods tests pass past 2026-05-22.

    Mark-paid entry tests use hardcoded purchase dates like
    date(2026, 1, 5) and date(2026, 1, 10) that must fall inside the
    calendar-anchored seed_periods range.  Freezing today inside the
    seeded range keeps "which paycheck contains today" deterministic without
    disturbing those calendar values.
    """
    freeze_today(monkeypatch, date(2026, 3, 20))
from app.services import entry_service, status_seam


# ── Helpers ──────────────────────────────────────────────────────


def _make_entry(txn_id, user_id, amount="50.00", description="Kroger",
                purchased_on=None, is_credit=False):
    """Create an entry directly via ORM (bypasses service validation).

    Uses IDs rather than ORM objects to avoid session detachment
    issues when combined with auth_client HTTP requests.
    """
    # The parent, resolved here rather than taken as an argument: this
    # helper's whole point is that a caller passes IDS.  The entry takes its
    # account (the parent's, because no door here says otherwise -- it is the
    # movement's OWN since plan step credit_card:CC-5-1) and its owner (the
    # parent's, which ``fk_transaction_entries_owner_transaction`` refuses to
    # see any other way).
    parent = db.session.get(Transaction, txn_id)
    entry = TransactionEntry(
        **figure_source_columns(),
        transaction_id=txn_id,
        account_id=parent.account_id,
        owner_id=parent.user_id,
        user_id=user_id,
        amount=Decimal(amount),
        description=description,
        purchased_on=purchased_on or date(2026, 1, 5),
        is_credit=is_credit,
    )
    db.session.add(entry)
    db.session.flush()
    return entry


def _create_tracked_txn(seed_user, seed_periods):
    """Create a tracked expense transaction with template.

    A priced ``is_envelope=True`` definition at $500.00 and its
    engine-generated row in the first paycheck (:func:`generate_row_of`,
    plan step balance:X-cf-4): the row is DERIVED, so what it plans is the
    definition's stated $500.00 and its own column is ``None``.
    """
    template = make_expense_template(
        db.session, seed_user, amount="500.00", name="Tracked Groceries",
        category_key="Groceries", is_envelope=True,
    )
    txn = generate_row_of(template, seed_periods[0])
    db.session.commit()
    return txn


def _create_non_tracked_txn(seed_user, seed_periods):
    """Create a regular expense transaction without entry tracking."""
    expense_type = (
        db.session.query(TransactionType).filter_by(name="Expense").one()
    )

    txn = one_off_row_of(
        seed_periods[0],
        name="Non-Tracked Expense",
        amount=Decimal("200.00"),
        user_id=seed_periods[0].user_id,
        account_id=seed_user["account"].id,
        scenario_id=seed_user["scenario"].id,
        transaction_type_id=expense_type.id,
        category_id=seed_user["categories"]["Groceries"].id,
    )
    db.session.commit()
    return txn


# ── Mark-Paid with Entries ───────────────────────────────────────


class TestMarkPaidRecordsThePurchases:
    """Mark-paid on a tracked row records the ``purchases`` basis.

    Its figure is not stored: the row's own entries state it, and
    ``row_valuation.settled_figure`` is the accessor (plan step X-au-c3).  The
    expected NUMBERS are unchanged from when this class read the column --
    what moved is where the answer comes from.
    """

    def test_mark_done_auto_populates_actual(
        self, app, auth_client, seed_user, seed_periods,
    ):
        """Mark-paid on tracked transaction with entries sets actual to entry sum.

        Setup: Two debit entries of $150 and $250.
        Expected actual: 150 + 250 = $400.
        """
        with app.app_context():
            txn = _create_tracked_txn(seed_user, seed_periods)
            txn_id = txn.id
            user_id = seed_user["user"].id

            _make_entry(txn_id, user_id, amount="150.00", description="Kroger")
            _make_entry(txn_id, user_id, amount="250.00", description="Target")
            db.session.commit()

            resp = auth_client.post(f"/transactions/{txn_id}/mark-done")
            assert resp.status_code == 200

            txn = db.session.get(Transaction, txn_id)
            assert settled_figure(txn) == Decimal("400.00")
            assert txn.status_id == ref_cache.status_id(StatusEnum.DONE)

    def test_mark_done_actual_includes_credit_entries(
        self, app, auth_client, seed_user, seed_periods,
    ):
        """Actual includes both debit and credit entries.

        Setup: $300 debit + $100 credit.
        Expected actual: 300 + 100 = $400 (credit is included for analytics).
        """
        with app.app_context():
            txn = _create_tracked_txn(seed_user, seed_periods)
            txn_id = txn.id
            user_id = seed_user["user"].id

            _make_entry(txn_id, user_id, amount="300.00", description="Kroger")
            _make_entry(txn_id, user_id, amount="100.00",
                        description="Amazon", is_credit=True)
            db.session.commit()

            resp = auth_client.post(f"/transactions/{txn_id}/mark-done")
            assert resp.status_code == 200

            txn = db.session.get(Transaction, txn_id)
            # 300 + 100 = 400 total spending.
            assert settled_figure(txn) == Decimal("400.00")

    def test_mark_done_no_entries_manual_actual(
        self, app, auth_client, seed_user, seed_periods,
    ):
        """Tracked transaction with no entries accepts manual actual from form.

        Setup: Entry-capable template, no entries created.
        Expected: manual settled_amount=350 is accepted (fall-through).
        """
        with app.app_context():
            txn = _create_tracked_txn(seed_user, seed_periods)
            txn_id = txn.id

            resp = auth_client.post(
                f"/transactions/{txn_id}/mark-done",
                data={"settled_amount": "350.00"},
            )
            assert resp.status_code == 200

            txn = db.session.get(Transaction, txn_id)
            assert settled_figure(txn) == Decimal("350.00")

    def test_mark_done_no_entries_records_the_derived_figure(
        self, app, auth_client, seed_user, seed_periods,
    ):
        """A tracked row with no entries records what it BOOKED, not nothing.

        Setup: Entry-capable template, no entries, no form data.

        **This asserted ``actual_amount is None`` until plan step X-au-c3.**  An
        envelope with no entries takes the MANUAL branch (ruling **R-FJ**: a
        door has relocated nothing, so pressing Paid books the budget rather
        than ``$0.00``), and before this step that branch recorded no figure at
        all -- so the row read back its PLAN.  It now records the figure it
        booked, on the ``derived`` basis because the app resolved it and nobody
        typed a correction.  The figure is the same ``$500.00`` the old
        fall-back answered.
        """
        with app.app_context():
            txn = _create_tracked_txn(seed_user, seed_periods)
            txn_id = txn.id

            resp = auth_client.post(f"/transactions/{txn_id}/mark-done")
            assert resp.status_code == 200

            txn = db.session.get(Transaction, txn_id)
            assert status_seam.recorded_settlement(txn).source is (
                MovementFigureSourceEnum.RESOLVED
            )
            # The definition's stated $500.00, which is what the DERIVED row
            # planned; its own column is None, so the plan is named here.
            assert settled_figure(txn) == Decimal("500.00")

    def test_mark_done_entries_override_form_actual(
        self, app, auth_client, seed_user, seed_periods,
    ):
        """Entry sum overrides manual actual_amount from the form.

        Setup: Entries sum to $400, form submits settled_amount=999.
        Expected: actual_amount = $400 (entries win).
        """
        with app.app_context():
            txn = _create_tracked_txn(seed_user, seed_periods)
            txn_id = txn.id
            user_id = seed_user["user"].id

            _make_entry(txn_id, user_id, amount="200.00", description="Kroger")
            _make_entry(txn_id, user_id, amount="200.00", description="Target")
            db.session.commit()

            resp = auth_client.post(
                f"/transactions/{txn_id}/mark-done",
                data={"settled_amount": "999.00"},
            )
            assert resp.status_code == 200

            txn = db.session.get(Transaction, txn_id)
            # Entry sum (200 + 200 = 400) overrides form value (999).
            assert settled_figure(txn) == Decimal("400.00")

    def test_non_tracked_mark_done_unchanged(
        self, app, auth_client, seed_user, seed_periods,
    ):
        """Non-tracked transaction mark-done uses manual actual.

        Setup: Non-tracked expense (its definition's tracking flag off).
        Expected: manual actual_amount from form is accepted.
        """
        with app.app_context():
            txn = _create_non_tracked_txn(seed_user, seed_periods)
            txn_id = txn.id

            resp = auth_client.post(
                f"/transactions/{txn_id}/mark-done",
                data={"settled_amount": "175.00"},
            )
            assert resp.status_code == 200

            txn = db.session.get(Transaction, txn_id)
            assert settled_figure(txn) == Decimal("175.00")
            assert txn.status_id == ref_cache.status_id(StatusEnum.DONE)


# ── Credit Status Guard ──────────────────────────────────────────


class TestCreditStatusGuard:
    """Tests for blocking Credit status on entry-capable transactions."""

    def test_update_rejects_credit_status_tracked(
        self, app, auth_client, seed_user, seed_periods,
    ):
        """PATCH with status_id=CREDIT on tracked transaction returns 400.

        The Credit status conflicts with entry-level credit handling, and
        the refusal says so (since plan task plan#3057 in the status door's
        words, ``transaction_service.verify_requested_status``: no row enters
        Credit, and this one puts each purchase on the card by its CC tick).
        """
        with app.app_context():
            txn = _create_tracked_txn(seed_user, seed_periods)
            txn_id = txn.id
            credit_id = ref_cache.status_id(StatusEnum.CREDIT)

            resp = auth_client.patch(
                f"/transactions/{txn_id}",
                data={"status_id": str(credit_id)},
            )
            assert resp.status_code == 400
            assert b"tracks its purchases" in resp.data
            assert b"each purchase goes on the card by its own CC tick" in resp.data

            # Transaction status must be unchanged.
            txn = db.session.get(Transaction, txn_id)
            assert txn.status_id != credit_id

    def test_update_refuses_credit_status_non_tracked(
        self, app, auth_client, seed_user, seed_periods,
    ):
        """PATCH with status_id=CREDIT on non-tracked transaction is refused too.

        It succeeded until plan task plan#3057 -- finding plan#2133: the Save
        set the bare Credit status, with no payback and no movement, so the
        row's money left checking's projection -- and this test, named
        ``test_update_allows_credit_status_non_tracked``, pinned that.  No row
        enters Credit now: the Credit button puts a row on the card (rulings
        plan#2887, plan#2888), the refusal points there, and the row is
        untouched.
        """
        with app.app_context():
            txn = _create_non_tracked_txn(seed_user, seed_periods)
            txn_id = txn.id
            credit_id = ref_cache.status_id(StatusEnum.CREDIT)

            resp = auth_client.patch(
                f"/transactions/{txn_id}",
                data={"status_id": str(credit_id)},
            )
            assert resp.status_code == 400
            assert b"open it and press Credit instead" in resp.data

            txn = db.session.get(Transaction, txn_id)
            assert txn.status_id == ref_cache.status_id(StatusEnum.PROJECTED)
            assert not txn.entries
            assert get_active_payback(txn_id) is None


# ── Post-Paid Entry Mutations ────────────────────────────────────


class TestPostPaidEntryMutation:
    """A Paid row's purchases are closed to RE-PRICING and REMOVAL (X-au-c3).

    **This class asserted the opposite until plan step X-au-c3** -- deleting or
    re-pricing a purchase on a Paid envelope re-derived its ``actual_amount``
    from the new entry sum.  That is withdrawn (developer ruling, 2026-08-17):
    re-deriving moves money in the OPTIMISTIC direction with no human act, and
    carry-forward has already rolled the envelope's unspent remainder into the
    next period's row, so shrinking the source's cost counts the same dollars
    twice.

    **ADDING one is a separate rule since plan step ``bank_import:X-f6a-3b``**
    (developer ruling, 2026-08-18), and the two are not the same question.
    Removing a purchase shrinks a recorded cost on nothing but the owner's
    second thoughts.  Adding one to a row whose figure IS its purchases raises
    that cost by exactly the figure a bank statement just showed -- the
    PESSIMISTIC direction, and the whole of what the statement importer does.
    A row closed at a STORED figure still refuses, because its gross cannot
    rise and the purchase's own movement would be counted beside the covering
    movement that already carries the whole close (ruling **R-BAL80**).

    The service-level controls live in
    ``test_entry_service.TestASettledRowsPurchasesAreClosed`` and
    ``TestASettledRowMayStillGAINAPurchase``; these grade the same rules on a
    row closed through the real ``mark-done`` route, and assert the second half
    of each -- what the RECORDED figure does.
    """

    @staticmethod
    def _paid_envelope_at_300(auth_client, seed_user, seed_periods):
        """Return (txn_id, entry_ids) for a tracked row closed at $300."""
        txn = _create_tracked_txn(seed_user, seed_periods)
        txn_id = txn.id
        user_id = seed_user["user"].id
        first = _make_entry(
            txn_id, user_id, amount="150.00", description="Kroger",
        )
        second = _make_entry(
            txn_id, user_id, amount="150.00", description="Target",
        )
        entry_ids = (first.id, second.id)
        db.session.commit()

        auth_client.post(f"/transactions/{txn_id}/mark-done")
        txn = db.session.get(Transaction, txn_id)
        assert settled_figure(txn) == Decimal("300.00")
        return txn_id, entry_ids

    def test_entry_added_after_paid_GROWS_what_the_row_records(
        self, app, auth_client, seed_user, seed_periods,
    ):
        """A late POSTED purchase on a purchases-basis close is ADMITTED.

        **This asserted a refusal until plan step ``bank_import:X-f6a-3b``**
        (developer ruling, 2026-08-18).  The row was closed through the real
        ``mark-done`` route with two entries, so ``settles_from_entries`` took
        the ``purchases`` branch and the row stores NO figure -- its cost IS
        ``Sigma(entries)``.  A new purchase therefore RAISES what it cost by
        exactly its own amount, which is what a bank statement showing a swipe
        the records never named is evidence of.

        `$300.00` becomes `$350.00`: the pessimistic direction, and no stored
        figure is overwritten because there is none.

        **It must state the day the bank took it** (developer ruling
        2026-08-19).  Without that day the amount comes out of the envelope's
        own leg on the day the row closed instead of booking its own dated
        cash, which is a movement on a past day with no evidence -- so the
        undated form stays refused, graded by
        ``test_entry_service.TestASettledRowMayStillGAINAPurchase
        .test_an_UNDATED_purchase_is_refused_on_a_closed_row``.
        """
        with app.app_context():
            txn_id, _ = self._paid_envelope_at_300(
                auth_client, seed_user, seed_periods,
            )

            entry_service.create_entry(
                transaction_id=txn_id,
                user_id=seed_user["user"].id,
                details=entry_service.EntryDetails(
                    figure=typed(Decimal("50.00")),
                    description="Late purchase",
                    purchased_on=date(2026, 1, 10),
                    settle_day=an_entered_day(date(2026, 1, 12)),
                ),
            )
            db.session.flush()

            txn = db.session.get(Transaction, txn_id)
            assert txn.covering_movements == []
            assert settled_figure(txn) == Decimal("350.00")
            assert db.session.query(TransactionEntry).filter_by(
                transaction_id=txn_id,
            ).count() == 3

    def test_entry_added_to_a_STORED_FIGURE_close_is_still_refused(
        self, app, auth_client, seed_user, seed_periods,
    ):
        """The arm the narrowing did NOT open, graded at the route tier.

        An envelope closed with NO entries takes ``mark-done``'s ``derived``
        branch and stores its figure, so nothing a new purchase does can raise
        it -- and the purchase's own movement would be counted beside the
        covering movement that already carries the close (ruling
        **R-BAL80**).  Measured on a production clone under the row-leg fold
        of the time: `-163.95` became `+203.67`, an expense row publishing an inflow,
        while the anchor true-up moved `$0.00` so the spending was never
        recorded at all.
        """
        with app.app_context():
            txn = _create_tracked_txn(seed_user, seed_periods)
            txn_id = txn.id
            db.session.commit()
            auth_client.post(f"/transactions/{txn_id}/mark-done")
            txn = db.session.get(Transaction, txn_id)
            assert settled_figure(txn) == Decimal("500.00")

            with pytest.raises(ValidationError, match="records a fixed figure"):
                entry_service.create_entry(
                    transaction_id=txn_id,
                    user_id=seed_user["user"].id,
                    details=entry_service.EntryDetails(
                        figure=typed(Decimal("600.00")),
                        description="Late purchase",
                        purchased_on=date(2026, 1, 10),
                        settle_day=an_entered_day(date(2026, 1, 12)),
                    ),
                )

            txn = db.session.get(Transaction, txn_id)
            assert settled_figure(txn) == Decimal("500.00")
            # No purchase landed; the covering movement is not one (X-bi-3a).
            assert purchases_of(txn) == []

    def test_entry_deleted_after_paid_is_admitted_and_re_prices_the_record(
        self, app, auth_client, seed_user, seed_periods,
    ):
        """Removing an un-dated purchase from a Paid ``purchases`` row is admitted.

        Refused through plan step ``X-bi-3e`` because the row's own leg
        booked the un-dated purchase on the close day; since ``balance:X-bi-4a``
        the purchase is a movement in flight and the row books nothing of its
        own (rulings **R-BAL77**, **R-BAL80**), so the removal removes that
        and the record -- the sum of what is left -- follows.
        """
        with app.app_context():
            txn_id, entry_ids = self._paid_envelope_at_300(
                auth_client, seed_user, seed_periods,
            )

            entry_service.delete_entry(
                entry_id=entry_ids[0], user_id=seed_user["user"].id,
            )
            db.session.flush()

            txn = db.session.get(Transaction, txn_id)
            assert settled_figure(txn) == Decimal("150.00")
            assert db.session.get(TransactionEntry, entry_ids[0]) is None

    def test_entry_updated_after_paid_is_refused(
        self, app, auth_client, seed_user, seed_periods,
    ):
        """Re-pricing a purchase on a Paid row is refused and changes nothing."""
        with app.app_context():
            txn_id, entry_ids = self._paid_envelope_at_300(
                auth_client, seed_user, seed_periods,
            )

            with pytest.raises(ValidationError, match="has settled"):
                entry_service.update_entry(
                    entry_id=entry_ids[0],
                    user_id=seed_user["user"].id,
                    figure=typed(Decimal("250.00")),
                )

            txn = db.session.get(Transaction, txn_id)
            assert settled_figure(txn) == Decimal("300.00")
            assert db.session.get(
                TransactionEntry, entry_ids[0],
            ).amount == Decimal("150.00")

    def test_projected_entry_mutation_does_not_set_actual(
        self, app, auth_client, seed_user, seed_periods,
    ):
        """Entry mutations on projected transactions do not touch actual_amount.

        The entry-mutation hook (``_resync_settled_envelope`` since plan
        step X-ap) fires only for a row in the settled band.
        """
        with app.app_context():
            txn = _create_tracked_txn(seed_user, seed_periods)
            txn_id = txn.id
            user_id = seed_user["user"].id

            # Transaction is in PROJECTED status.
            assert txn.status_id == ref_cache.status_id(StatusEnum.PROJECTED)
            assert status_seam.recorded_settlement(txn) is None

            entry_service.create_entry(
                transaction_id=txn_id,
                user_id=user_id,
                details=entry_service.EntryDetails(
                    figure=typed(Decimal("100.00")),
                    description="Projected period purchase",
                    purchased_on=date(2026, 1, 5),
                ),
            )
            db.session.commit()

            txn = db.session.get(Transaction, txn_id)
            # A projected transaction records nothing.
            assert status_seam.recorded_settlement(txn) is None
