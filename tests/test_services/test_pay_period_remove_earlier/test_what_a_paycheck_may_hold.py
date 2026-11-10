"""WHAT A PAYCHECK MAY HOLD (R-PC109's filed half), one case per kind, the OPENINGS RE-FILED
(R-PC114): an account's and a loan's opening filed in an added paycheck go back, and that NO
WORKED-OUT TOTAL MOVES (plan#2517), which is why nothing is left to refuse.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from app import ref_cache
from app.enums import PostingSourceEnum, StatusEnum
from app.extensions import db as _db
from app.models.pay_stub import PayStub
from app.models.transaction import Transaction
from app.models.transfer import Transfer
from app.services import pay_period_admin, status_seam, transfer_service
from app.services.ledger_lines import linked_key
from app.models.amount_ownership import AmountOwnership
from tests._ledger_reads import (
    filed_period_id,
    owner_worked_out_lines,
    source_lines,
    worked_out_totals,
)
from tests._test_helpers import (
    add_entry,
    add_txn,
    create_loan_account,
    create_savings_account,
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
    _held,
)


def _source_nets_by_period(scenario_id, account, source):
    """Return ``{period_id: net}`` of *source*'s lines on *account*'s own line.

    Nets that are zero are left out: a paycheck holding none of the source's
    money is not an answer.
    """
    nets = {}
    for line in source_lines(_db.session, linked_key(account), scenario_id, source):
        filed = filed_period_id(line, account.user_id)
        nets[filed] = nets.get(filed, Decimal("0.00")) + line.amount
    return {period_id: net for period_id, net in nets.items() if net != 0}


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
            # Set back through the seam, as the route's status verb does it.
            status_seam.apply_status_change(
                row, ref_cache.status_id(StatusEnum.PROJECTED),
            )
            db.session.commit()
            assert row.entries and not row.purchases, (
                "the revert must leave the covering mark and no purchase"
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


class TestAnOpeningIsReFiled:
    """R-PC114 (amending R-PC109): an opening filed in an added paycheck is re-filed.

    The ledger files a line dated before the first paycheck in the EARLIEST
    one (R-PC53) -- so once a paycheck is added below the record, the books'
    or a loan's opening sits in it, and after the removal in the new first
    paycheck.
    """

    def test_an_account_opening_filed_in_an_added_paycheck_goes_back(
        self, app, db, seed_user, seed_periods,
    ):
        """Review 1's first probe: the add, then the removal."""
        with app.app_context():
            user_id = seed_user["user"].id
            head = _added_head(user_id, 2)
            db.session.commit()
            scenario = seed_user["scenario"].id
            nets = _source_nets_by_period(
                scenario, seed_user["account"], PostingSourceEnum.ACCOUNT_OPENING,
            )
            added_first = head[0].id
            assert set(nets) == {added_first}, "the opening's money sits in the head"
            totals = worked_out_totals(_db.session, user_id)

            assert pay_period_admin.remove_earlier_pay_periods(
                user_id, seed_periods[0].id,
            ) == 2
            db.session.commit()

            # Re-filed INTO THE NEW FIRST PAYCHECK (R-PC114's words), and
            # nowhere else, with every total as it was.
            assert _source_nets_by_period(
                scenario, seed_user["account"], PostingSourceEnum.ACCOUNT_OPENING,
            ) == {seed_periods[0].id: nets[added_first]}
            assert worked_out_totals(_db.session, user_id) == totals

    def test_a_loans_opening_filed_in_an_added_paycheck_goes_back(
        self, app, db, seed_user, seed_periods,
    ):
        """Review 1's second probe: a loan from 2020, then the add and the removal."""
        with app.app_context():
            user_id = seed_user["user"].id
            loan = create_loan_account(
                seed_user, db.session, name="Car Loan",
                principal=Decimal("20000.00"), rate=Decimal("0.05"),
                origination_date=date(2020, 1, 1),
            )
            db.session.commit()
            head = _added_head(user_id, 2)
            db.session.commit()
            scenario = seed_user["scenario"].id
            nets = _source_nets_by_period(scenario, loan, PostingSourceEnum.LOAN_OPENING)
            added_first = head[0].id
            assert set(nets) == {added_first}, "the loan opening's money sits in the head"
            totals = worked_out_totals(_db.session, user_id)

            assert pay_period_admin.remove_earlier_pay_periods(
                user_id, seed_periods[0].id,
            ) == 2
            db.session.commit()

            assert _source_nets_by_period(scenario, loan, PostingSourceEnum.LOAN_OPENING) == {
                seed_periods[0].id: nets[added_first],
            }
            assert worked_out_totals(_db.session, user_id) == totals


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
                        filed.setdefault(
                            filed_period_id(line, user_id), set(),
                        ).add(line.source_kind_id)
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
