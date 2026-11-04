"""WHAT A PAYCHECK MAY HOLD (R-PC109's filed half), one case per kind, the STORE RE-FILED
(R-PC114): an account's and a loan's opening filed in an added paycheck go back, a
self-cancelling pair goes, and an entry no re-sync rebuilds goes too, unrefused (plan#2517), and
that NO WORKED-OUT TOTAL MOVES (plan#2517), which is why nothing is left to refuse.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from app import ref_cache
from app.enums import PostingSourceEnum, StatusEnum
from app.extensions import db as _db
from app.models.journal_entry import JournalEntry, Posting
from app.models.ledger_account import LedgerAccount
from app.models.pay_stub import PayStub
from app.models.transaction import Transaction
from app.models.transfer import Transfer
from app.services import (
    account_posting_service,
    anchor_service,
    loan_posting_service,
    pay_period_admin,
    posting_service,
    status_seam,
    transfer_service,
)
from app.services.ledger_lines import linked_key
from app.models.amount_ownership import AmountOwnership
from tests._ledger_parity import (
    assert_ledger_parity,
    ledger_parity_differences,
    owner_worked_out_lines,
    worked_out_totals,
)
from tests._test_helpers import (
    add_entry,
    add_txn,
    create_loan_account,
    create_savings_account,
    make_balanced_entry,
    make_salary_profile,
    refused_by_database_rule,
    restate_account_opening,
    settle_cash_row,
    transfer_side_record,
)

from ._helpers import (
    _paydays,
    _stored_eras,
    _unchanged,
    _added_head,
    _monthly_bill_from,
    _monthly_transfer_from,
    _entries_in,
    _held,
    _stored_totals,
)


def _ledger_ids_of(account):
    """Return *account*'s ledger accounts, ascending (its own row first)."""
    return [
        row[0] for row in _db.session.query(LedgerAccount.id)
        .filter(LedgerAccount.account_id == account.id)
        .order_by(LedgerAccount.id)
    ]


def _source_nets_by_period(account, source):
    """Return ``{period_id: net}`` of *source*'s entries on *account*'s own ledger row.

    Nets that are zero are left out: a re-sync that re-files an entry leaves
    the old one and its reversal in the paycheck they were filed in.
    """
    own = _ledger_ids_of(account)[0]
    return {
        period_id: net
        for period_id, net in _db.session.query(
            JournalEntry.pay_period_id, _db.func.sum(Posting.amount),
        )
        .join(Posting, Posting.journal_entry_id == JournalEntry.id)
        .filter(
            Posting.ledger_account_id == own,
            JournalEntry.source_kind_id == ref_cache.posting_source_id(source),
        )
        .group_by(JournalEntry.pay_period_id)
        if net != 0
    }


def _two_ledger_ids(seed_user):
    """Return two ledger accounts the seeded owner's Checking is paired with.

    The account's own row and the chart row resolved for its opening -- any
    two distinct ledger accounts serve an entry and its reversal.
    """
    ids = [
        row[0] for row in _db.session.query(LedgerAccount.id)
        .filter(LedgerAccount.account_id == seed_user["account"].id)
        .order_by(LedgerAccount.id)
    ]
    assert len(ids) >= 2, "the seeded Checking pairs with two ledger accounts"
    return ids[0], ids[1]


class TestWhatAPaycheckMayHold:
    """R-PC109's filed half: anything but an untouched template row refuses.

    Each case adds two paychecks before the seeded owner's 2026-01-02 (so
    2025-12-05 and 2025-12-19), puts one kind of row into the 12-19 one, and
    starts from 01-02.  Every refusal writes nothing.
    """

    def _world(self, seed_user):
        """The seeded owner with two paychecks added before 01-02."""
        user_id = seed_user["user"].id
        head = _added_head(user_id, 2)
        return user_id, head

    def test_a_typed_row_is_refused_in_the_ruled_words(
        self, app, db, seed_user, seed_periods,
    ):
        """The ruling's own example: one row the owner typed, named."""
        with app.app_context():
            user_id, head = self._world(seed_user)
            add_txn(db.session, seed_user, head[1], "Groceries", "45.00")
            db.session.commit()
            paydays, eras = _paydays(user_id), _stored_eras(user_id)

            message = _held(user_id, seed_periods[0])

            assert message == (
                "The 2025-12-19 paycheck holds 1 item you entered or changed "
                "(Groceries). Delete or move it first."
            )
            _unchanged(user_id, paydays, eras)

    def test_items_in_two_paychecks_are_each_named(
        self, app, db, seed_user, seed_periods,
    ):
        """One sentence per paycheck, then one remedy in the plural."""
        with app.app_context():
            user_id, head = self._world(seed_user)
            add_txn(db.session, seed_user, head[0], "Gas", "30.00")
            add_txn(db.session, seed_user, head[1], "Groceries", "45.00")
            add_txn(db.session, seed_user, head[1], "Books", "12.00")
            db.session.commit()

            assert _held(user_id, seed_periods[0]) == (
                "The 2025-12-05 paycheck holds 1 item you entered or changed "
                "(Gas). The 2025-12-19 paycheck holds 2 items you entered or "
                "changed (Books, Groceries). Delete or move them first."
            )

    @pytest.mark.parametrize("status", [
        StatusEnum.DONE, StatusEnum.CREDIT, StatusEnum.CANCELLED,
    ])
    def test_a_template_row_the_owner_marked_is_refused(
        self, app, db, seed_user, seed_periods, status,
    ):
        """A template row paid, put on a card or cancelled is the owner's act."""
        with app.app_context():
            user_id = seed_user["user"].id
            _monthly_bill_from(seed_user, date(2025, 12, 20))
            db.session.commit()
            head = _added_head(user_id, 2)
            row = db.session.query(Transaction).filter_by(
                pay_period_id=head[1].id,
            ).one()
            if status == StatusEnum.DONE:
                settle_cash_row(row, settled_on=head[1].start_date)
            else:
                row.status_id = ref_cache.status_id(status)
            db.session.commit()

            assert "(Rent)" in _held(user_id, seed_periods[0])

    def test_a_template_row_the_owner_changed_is_refused(
        self, app, db, seed_user, seed_periods,
    ):
        """An override is the owner's figure, not the template's."""
        with app.app_context():
            user_id = seed_user["user"].id
            _monthly_bill_from(seed_user, date(2025, 12, 20))
            db.session.commit()
            head = _added_head(user_id, 2)
            db.session.query(Transaction).filter_by(
                pay_period_id=head[1].id,
            ).one().is_override = True
            db.session.commit()

            assert "(Rent)" in _held(user_id, seed_periods[0])

    def test_a_template_row_holding_a_purchase_is_refused(
        self, app, db, seed_user, seed_periods,
    ):
        """Untouched but for a purchase: the purchase is the owner's money."""
        with app.app_context():
            user_id = seed_user["user"].id
            _monthly_bill_from(seed_user, date(2025, 12, 20), is_envelope=True)
            db.session.commit()
            head = _added_head(user_id, 2)
            row = db.session.query(Transaction).filter_by(
                pay_period_id=head[1].id,
            ).one()
            add_entry(db.session, seed_user, row, Decimal("20.00"), head[1].start_date)
            db.session.commit()

            assert "(Rent)" in _held(user_id, seed_periods[0])

    def test_a_transfer_made_by_hand_is_refused(
        self, app, db, seed_user, seed_periods,
    ):
        """A one-time transfer refuses the removal, counted as one item."""
        with app.app_context():
            user_id = seed_user["user"].id
            savings = create_savings_account(
                seed_user, db.session, "Savings", Decimal("500.00"),
            )
            db.session.commit()
            head = _added_head(user_id, 2)
            transfer_service.create_transfer(transfer_service.TransferSpec(
                user_id=user_id,
                from_account_id=seed_user["account"].id,
                to_account_id=savings.id,
                pay_period_id=head[1].id,
                scenario_id=seed_user["scenario"].id,
                amount_ownership=AmountOwnership.own(Decimal("150.00")),
                status_id=ref_cache.status_id(StatusEnum.PROJECTED),
                category_id=None,
            ))
            db.session.commit()

            message = _held(user_id, seed_periods[0])

            assert message.startswith("The 2025-12-19 paycheck holds 1 item ")

    def test_a_pay_stub_on_a_removed_payday_is_refused(
        self, app, db, seed_user, seed_periods,
    ):
        """A stub sits on a paycheck the app holds (R-SAL49)."""
        with app.app_context():
            user_id, head = self._world(seed_user)
            profile = make_salary_profile(seed_user, db.session)
            db.session.flush()
            db.session.add(PayStub(
                salary_profile_id=profile.id, payday=head[0].start_date,
                base_pay=Decimal("2884.62"),
            ))
            db.session.commit()

            assert _held(user_id, seed_periods[0]) == (
                "A pay stub is saved for 2025-12-05. Delete it first, or start "
                "from 2025-12-05 or earlier."
            )

    def test_a_template_row_paid_then_set_back_is_refused(
        self, app, db, seed_user, seed_periods,
    ):
        """The revert keeps the payment on the row: history, refused (R-PC115).

        Since plan step balance:X-bi-3e-2 a revert keeps the settlement mark,
        un-dated, under the Projected row (``Transaction.purchases`` excludes
        it, R-BAL68), and since credit_card:CC-5-4a-4 no row's delete takes
        its movements (R-CC54).  Ruling **R-PC115** (developer 2026-09-29,
        "Refuse, like every door") refuses it in the ruled words, naming the
        row, and keeps the payment; deleting the row is the remedy.  *Until
        that ruling this case asserted the row went with its paycheck, the
        ledger pair netting to $0.00 -- which, once CC-5-4a-4 met C21, raised
        the database's refusal instead (measured at X-bn's dev merge).*
        """
        with app.app_context():
            user_id = seed_user["user"].id
            _monthly_bill_from(seed_user, date(2025, 12, 20))
            db.session.commit()
            head = _added_head(user_id, 2)
            row = db.session.query(Transaction).filter_by(
                pay_period_id=head[1].id,
            ).one()
            settle_cash_row(row, settled_on=head[1].start_date)
            db.session.commit()
            # Set back as the route does it: the seam, then the posting
            # writer, which reverses the leg in the same paycheck and day.
            status_seam.apply_status_change(
                row, ref_cache.status_id(StatusEnum.PROJECTED),
            )
            posting_service.sync_transaction_postings(row)
            db.session.commit()
            assert row.entries and not row.purchases, (
                "the revert must leave the covering mark and no purchase"
            )
            assert len(_entries_in({head[1].id})) == 2, (
                "the paid leg and its reversal must both sit in the paycheck"
            )
            row_id = row.id
            paydays, eras = _paydays(user_id), _stored_eras(user_id)

            assert _held(user_id, seed_periods[0]) == (
                "The 2025-12-19 paycheck holds 1 item you entered or changed "
                "(Rent). Delete or move it first."
            )
            _unchanged(user_id, paydays, eras)
            assert _db.session.get(Transaction, row_id).entries, (
                "the refusal must keep the row's payment"
            )

    @pytest.mark.parametrize("deleted", [False, True])
    def test_a_transfer_done_then_set_back_is_refused_once(
        self, app, db, seed_user, seed_periods, deleted,
    ):
        """Both sides keep their payment; the transfer is named once (R-PC115).

        Measured at X-bn's dev merge, before the ruling: after the revert
        each leg holds one entry and no purchase, and the removal raised the
        database's refusal.  Each SIDE keeps its record since plan step
        ``balance:X-bi-6-4d-2`` (ruling **R-BAL88**; ruling **R-BAL167**
        class 1).  With ``deleted`` the occurrence was then deleted as the
        grid's delete did it (``delete_transfer(soft=True)``): its legs
        hidden and STILL holding their payments -- finding **BAL-532**.  That
        step ended it: the delete takes the payments off (ruling **R-CC75**)
        and the database refuses a transfer hidden holding one, so the hide
        is asserted refused (class 2) and rolled back, and the live transfer
        it leaves, both records kept, is still named once.  The case also hid
        both twins around the service, before the refused hide and again
        after it, and read the legs' purchases and delete flags, until plan
        step ``balance:X-bi-6-4d-2`` made a transfer one row (ruling
        **R-BAL258**): each update touched no row and the legs were an empty
        list on both params (measured), so they went.
        """
        with app.app_context():
            user_id = seed_user["user"].id
            savings = create_savings_account(
                seed_user, db.session, "Savings", Decimal("500.00"),
            )
            # Savings' books must reach the added paychecks, or the books
            # bound (pay_calendar:C18-a) keeps the transfer out of them.
            restate_account_opening(db.session, savings, date(2023, 6, 1))
            _monthly_transfer_from(seed_user, savings, date(2025, 12, 20))
            db.session.commit()
            head = _added_head(user_id, 2)
            transfer = db.session.query(Transfer).filter_by(
                pay_period_id=head[1].id,
            ).one()
            transfer_service.settle_transfer(transfer.id, user_id)
            db.session.commit()
            transfer_service.update_transfer(
                transfer.id, user_id,
                status_id=ref_cache.status_id(StatusEnum.PROJECTED),
            )
            db.session.commit()
            if deleted:
                db.session.execute(
                    _db.text(
                        "UPDATE budget.transfers SET is_deleted = TRUE "
                        "WHERE id = :t"
                    ),
                    {"t": transfer.id},
                )
                with refused_by_database_rule(
                    "was deleted while it still holds a recorded payment",
                ):
                    db.session.commit()
                db.session.rollback()
            assert [
                transfer_side_record(db.session, transfer.id, account_id)
                is not None
                for account_id in (
                    transfer.from_account_id, transfer.to_account_id,
                )
            ] == [True, True], (
                "each side must keep its payment through the revert"
            )
            paydays, eras = _paydays(user_id), _stored_eras(user_id)

            assert _held(user_id, seed_periods[0]) == (
                "The 2025-12-19 paycheck holds 1 item you entered or changed "
                "(To Savings). Delete or move it first."
            )
            _unchanged(user_id, paydays, eras)

    def test_template_rows_alone_go_with_the_paycheck(
        self, app, db, seed_user, seed_periods,
    ):
        """The control: the same world with nothing the owner made is removed."""
        with app.app_context():
            user_id = seed_user["user"].id
            _monthly_bill_from(seed_user, date(2025, 12, 6))
            db.session.commit()
            head = _added_head(user_id, 2)
            held = {period.id for period in head}
            assert db.session.query(Transaction).filter(
                Transaction.pay_period_id.in_(held),
            ).count() == 1

            assert pay_period_admin.remove_earlier_pay_periods(
                user_id, seed_periods[0].id,
            ) == 2
            db.session.commit()

            assert db.session.query(Transaction).filter(
                Transaction.pay_period_id.in_(held),
            ).count() == 0


class TestTheStoreIsReFiled:
    """R-PC114 (amending R-PC109): booked entries are re-filed, the store's upkeep.

    The ledger files an entry dated before the first paycheck in the
    EARLIEST one (R-PC53), and every re-sync re-files there -- so once a
    paycheck is added below the record, the books' or a loan's opening sits
    in it.  The removal re-files through Reset's two re-syncs.  An entry no
    re-sync rebuilds, which no door books, no longer refuses it (ruling
    plan#2517): it goes with its paycheck, and it was never a worked-out line.
    """

    def test_an_account_opening_filed_in_an_added_paycheck_goes_back(
        self, app, db, seed_user, seed_periods,
    ):
        """Review 1's first probe: a re-sync after the add, then the removal."""
        with app.app_context():
            user_id = seed_user["user"].id
            head = _added_head(user_id, 2)
            account_posting_service.resync_user_account_anchor_postings(user_id)
            db.session.commit()
            opening = ref_cache.posting_source_id(PostingSourceEnum.ACCOUNT_OPENING)
            assert opening in {kind for _id, kind in _entries_in({head[0].id})}
            nets = _source_nets_by_period(
                seed_user["account"], PostingSourceEnum.ACCOUNT_OPENING,
            )
            added_first = head[0].id
            assert set(nets) == {added_first}, "the opening's money sits in the head"
            totals = _stored_totals(user_id)

            assert pay_period_admin.remove_earlier_pay_periods(
                user_id, seed_periods[0].id,
            ) == 2
            db.session.commit()

            # Re-filed INTO THE NEW FIRST PAYCHECK (R-PC114's words), and
            # nowhere else, with every total as it was.
            assert _source_nets_by_period(
                seed_user["account"], PostingSourceEnum.ACCOUNT_OPENING,
            ) == {seed_periods[0].id: nets[added_first]}
            assert _stored_totals(user_id) == totals

    def test_a_loans_opening_filed_in_an_added_paycheck_goes_back(
        self, app, db, seed_user, seed_periods,
    ):
        """Review 1's second probe: a loan from 2020, the loan re-sync after the add."""
        with app.app_context():
            user_id = seed_user["user"].id
            loan = create_loan_account(
                seed_user, db.session, name="Car Loan",
                principal=Decimal("20000.00"), rate=Decimal("0.05"),
                origination_date=date(2020, 1, 1),
            )
            db.session.commit()
            head = _added_head(user_id, 2)
            loan_posting_service.resync_user_loan_postings(user_id)
            db.session.commit()
            loan_opening = ref_cache.posting_source_id(PostingSourceEnum.LOAN_OPENING)
            assert loan_opening in {kind for _id, kind in _entries_in({head[0].id})}
            nets = _source_nets_by_period(loan, PostingSourceEnum.LOAN_OPENING)
            added_first = head[0].id
            assert set(nets) == {added_first}, "the loan opening's money sits in the head"
            totals = _stored_totals(user_id)

            assert pay_period_admin.remove_earlier_pay_periods(
                user_id, seed_periods[0].id,
            ) == 2
            db.session.commit()

            assert _source_nets_by_period(loan, PostingSourceEnum.LOAN_OPENING) == {
                seed_periods[0].id: nets[added_first],
            }
            assert _stored_totals(user_id) == totals

    def test_a_self_cancelling_pair_goes_with_its_paycheck(
        self, app, db, seed_user, seed_periods,
    ):
        """An entry and its reversal net to $0.00 on each ledger account."""
        with app.app_context():
            user_id = seed_user["user"].id
            head = _added_head(user_id, 2)
            checking, other = _two_ledger_ids(seed_user)
            make_balanced_entry(
                db.session, seed_user, from_ledger_id=checking,
                to_ledger_id=other, period_id=head[1].id,
            )
            make_balanced_entry(
                db.session, seed_user, from_ledger_id=other,
                to_ledger_id=checking, period_id=head[1].id,
            )
            totals = _stored_totals(user_id)

            assert pay_period_admin.remove_earlier_pay_periods(
                user_id, seed_periods[0].id,
            ) == 2
            db.session.commit()

            assert _stored_totals(user_id) == totals

    def test_an_entry_no_re_sync_rebuilds_goes_with_its_paycheck(
        self, app, db, seed_user, seed_periods,
    ):
        """One entry in an added paycheck that no record rebuilds: no longer refused.

        Built by hand, because no door books money into a paycheck with
        nothing in it: the deleted refusal's own firing case (ruling
        plan#2517).  The store loses the entry's $100.00, and the worked-out
        ledger, which reads the records, never held it.
        """
        with app.app_context():
            user_id = seed_user["user"].id
            head = _added_head(user_id, 2)
            checking, other = _two_ledger_ids(seed_user)
            make_balanced_entry(
                db.session, seed_user, from_ledger_id=checking,
                to_ledger_id=other, period_id=head[1].id,
            )
            scenario = seed_user["scenario"].id
            stored, worked_out = _stored_totals(user_id), worked_out_totals(_db.session, user_id)

            assert pay_period_admin.remove_earlier_pay_periods(
                user_id, seed_periods[0].id,
            ) == 2
            db.session.commit()

            # The entry booked -$100.00 on Checking and +$100.00 on the other
            # row, so losing it moves each back by that.
            after = _stored_totals(user_id)
            assert after[(scenario, checking)] == (
                stored[(scenario, checking)] + Decimal("100.00")
            )
            assert after[(scenario, other)] == (
                stored[(scenario, other)] - Decimal("100.00")
            )
            assert worked_out_totals(_db.session, user_id) == worked_out

    def test_a_legacy_entry_between_two_accounts_leaves_the_store_equal_to_the_records(
        self, app, db, seed_user, seed_periods,
    ):
        """A sourceless Checking to Savings entry, both trued up later: no longer refused.

        Review 2's firing case for the deleted refusal.  Each later true-up
        is worked from the RECORDS (ruling plan#2572), which hold no row the
        entry came from, so the entry rides on top of each account's asserted
        balance in the store: $900.00 - $100.00 = $800.00 on Checking and
        $600.00 + $100.00 = $700.00 on Savings.  The worked-out ledger holds
        the asserted balances, before the removal and after, and the removal
        takes the entry with its paycheck, so the store comes to equal it.
        """
        with app.app_context():
            user_id = seed_user["user"].id
            checking = seed_user["account"]
            savings = create_savings_account(
                seed_user, db.session, "Savings", Decimal("500.00"),
            )
            db.session.commit()
            head = _added_head(user_id, 2)
            make_balanced_entry(
                db.session, seed_user,
                from_ledger_id=_ledger_ids_of(checking)[0],
                to_ledger_id=_ledger_ids_of(savings)[0],
                period_id=head[1].id,
            )
            anchor_service.apply_anchor_true_up(
                account=checking, new_balance=Decimal("900.00"),
            )
            anchor_service.apply_anchor_true_up(
                account=savings, new_balance=Decimal("600.00"),
            )
            db.session.commit()
            scenario = seed_user["scenario"].id
            stored = _stored_totals(user_id)
            assert stored[(scenario, _ledger_ids_of(checking)[0])] == Decimal("800.00")
            assert stored[(scenario, _ledger_ids_of(savings)[0])] == Decimal("700.00")
            assert ledger_parity_differences(db.session, scenario), (
                "the entry must part the store from the records"
            )
            worked_out = worked_out_totals(_db.session, user_id)
            assert worked_out[(scenario, linked_key(checking))] == Decimal("900.00")
            assert worked_out[(scenario, linked_key(savings))] == Decimal("600.00")

            assert pay_period_admin.remove_earlier_pay_periods(
                user_id, seed_periods[0].id,
            ) == 2
            db.session.commit()

            after = _stored_totals(user_id)
            assert after[(scenario, _ledger_ids_of(checking)[0])] == Decimal("900.00")
            assert after[(scenario, _ledger_ids_of(savings)[0])] == Decimal("600.00")
            assert worked_out_totals(_db.session, user_id) == worked_out
            assert_ledger_parity(db.session, scenario)


class TestNoWorkedOutTotalMoves:
    """Ruling plan#2517: nothing is left for the deleted booked-total check to catch.

    A worked-out total moves only if a record holding money goes, and the
    gate refuses every row or transfer holding money the delete could take.
    What the head does hold is an opening filed in it, which names no
    paycheck, and the plan's rows, which the ledger's walk does not read.
    """

    def test_a_loans_and_an_accounts_openings_in_the_head_move_no_total(
        self, app, db, seed_user, seed_periods,
    ):
        """Openings and a loan's planned payments in the added paychecks; every total equal.

        A loan from 2020 and a monthly transfer into it from 2025-11-24, whose
        11-24 and 12-24 occurrences fall in the 11-21 and 12-19 paychecks the
        add records: a planned payment the delete takes, which must not move
        the loan's corrections.  Each opening files in the earliest paycheck,
        so in the head before the removal and in the first kept one after.
        """
        with app.app_context():
            user_id = seed_user["user"].id
            checking = seed_user["account"]
            loan = create_loan_account(
                seed_user, db.session, name="Car Loan",
                principal=Decimal("20000.00"), rate=Decimal("0.05"),
                origination_date=date(2020, 1, 1),
            )
            _monthly_transfer_from(seed_user, loan, date(2025, 11, 24))
            db.session.commit()
            head = _added_head(user_id, 3)
            held = {period.id for period in head}
            loan_posting_service.resync_user_loan_postings(user_id)
            account_posting_service.resync_user_account_anchor_postings(user_id)
            db.session.commit()
            assert _db.session.query(Transfer).filter(
                Transfer.pay_period_id.in_(held),
            ).count() == 2, "the head must hold the loan's planned payments"
            openings = {
                ref_cache.posting_source_id(PostingSourceEnum.ACCOUNT_OPENING),
                ref_cache.posting_source_id(PostingSourceEnum.LOAN_OPENING),
            }

            def _filed_openings():
                """Return the kinds of opening the worked-out ledger files, per paycheck."""
                filed = {}
                for _scenario_id, line in owner_worked_out_lines(_db.session, user_id):
                    if line.source_kind_id in openings:
                        filed.setdefault(line.pay_period_id, set()).add(line.source_kind_id)
                return filed

            assert _filed_openings() == {head[0].id: openings}
            scenario = seed_user["scenario"].id
            before = worked_out_totals(_db.session, user_id)
            # What each opening books, debit-positive: Checking's stated
            # $1,000.00, and the loan's principal owed, a liability's credit.
            assert before[(scenario, linked_key(checking))] == Decimal("1000.00")
            assert before[(scenario, linked_key(loan))] == Decimal("-20000.00")

            assert pay_period_admin.remove_earlier_pay_periods(
                user_id, seed_periods[0].id,
            ) == 3
            db.session.commit()

            assert worked_out_totals(_db.session, user_id) == before
            assert _filed_openings() == {seed_periods[0].id: openings}
            assert_ledger_parity(db.session, scenario)
