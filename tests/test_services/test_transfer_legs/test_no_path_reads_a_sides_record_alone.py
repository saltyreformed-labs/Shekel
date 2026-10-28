"""No page or act reads a transfer side's record on its own.

Finding **plan#2067**: ruling **plan#1984** made a transfer side's record a
read off its transfer's list for that side, and said a forgotten pre-load
costs a query per side, never a wrong answer, which statement-count tests
guard.  Only the popover (``test_transfer_popover_loads_its_records_once``)
and the two mechanisms themselves (``test_a_sides_record_is_read_off_its_transfer``)
had one, so a pre-load deleted from any other path passed the suite.  Ruling
**plan#2136** (developer 2026-10-28) then placed every fetch: a transfer's
records are loaded only where something reads them, both sides and every
transfer in ONE statement (``transfer_legs.preload_side_records``), or filled
at no cost by a loader that built the side FROM its record
(``recorded_transfer_legs``, ``movement_parent``) -- never one side at a time.

**So every path takes the same control: inside it, no side's list is loaded
alone.**  :func:`_sides_read_alone` collects each such load -- the lazy load
one :attr:`~app.services.transfer_legs.TransferLeg.record` read runs on a list
nobody loaded, one statement per side -- and each case below asserts it
collects none while its fixture holds sides the path builds.  The batched
pre-load is a plain query and the fill issues none, so neither is collected.
Zero holds whatever rows the fixture builds, where a total statement count
pinned today's figure would break on unrelated work.

**What makes each control fire is named in its docstring, and was measured**
by deleting that pre-load or fill (or, for a path that reads no record,
adding a read) at plan#2090's commit: the case then collects the sides the
fixture puts in its path.  The last class holds the ruling's other half: an
act that reads no record fetches none (plan#2138).
"""

from contextlib import contextmanager
from datetime import timedelta
from decimal import Decimal

import pytest
from sqlalchemy import event, inspect
from sqlalchemy.orm import Session

from app import ref_cache
from app.enums import StatusEnum
from app.extensions import db
from app.models.transfer import Transfer
from app.services import (
    pay_calendar,
    posting_service,
    reconcile_service,
    statement_match,
    transfer_service,
)
from app.services.cash_ledger import settled_cash_facts
from app.services.loan_posting_service import sync_loan_postings
from app.services.statement_match import RowKind, accepted_register
from app.services.statement_match._valuation import repriced
from app.services.transfer_legs import preload_side_records, transfer_side_leg
from app.services.transfer_legs._leg import side_movements_key
from app.utils.dates import display_today
from tests._test_helpers import (
    create_settled_transfer,
    create_transfer,
    open_books_before_the_first_assertion,
)
from tests.test_services.test_loan_settled_legs._helpers import _loan
from tests.test_services.test_reconcile_service._helpers import _reconciled
from tests.test_services.test_statement_match._builders import (
    a_bank_line,
    a_basis,
    a_scope,
    a_submission,
    an_import,
)

from ._helpers import _savings


#: The header an HTMX swap sends, which the grid's transfer doors are reached
#: with.  Without it the pulse redirects to the dashboard and an Analytics tab
#: answers with its page shell, so neither would reach the section it draws.
_HTMX = {"HX-Request": "true"}


@contextmanager
def _sides_read_alone():
    """Collect each transfer side whose record list is loaded ON ITS OWN inside this block.

    A read of a side's record off a list nobody loaded runs the
    relationship's lazy load: one SELECT for that one side.  SQLAlchemy fires
    ``do_orm_execute`` for it with ``is_relationship_load`` set and
    ``lazy_loaded_from`` naming the transfer, and its loader path ends at the
    side's relationship, which :func:`side_movements_key` names -- the one
    statement of which list is a side's.  A statement count could not tell
    this load from any other SELECT on ``budget.transaction_entries``.

    Yields:
        ``[(transfer id, is_income), ...]``, one per side loaded alone,
        appended in load order as the block runs.
    """
    sides = {side_movements_key(is_income=flag): flag for flag in (False, True)}
    transfer_mapper = inspect(Transfer)
    alone = []

    def _record(orm_state):
        parent = orm_state.lazy_loaded_from
        if not orm_state.is_relationship_load or parent is None:
            return
        relationship = orm_state.loader_strategy_path.path[-1]
        if relationship.parent is transfer_mapper and relationship.key in sides:
            alone.append((parent.identity[0], sides[relationship.key]))

    event.listen(Session, "do_orm_execute", _record)
    try:
        yield alone
    finally:
        event.remove(Session, "do_orm_execute", _record)


def _a_savings_account(seed_user):
    """A savings account whose books open before any day these fixtures date."""
    savings = _savings(seed_user)
    open_books_before_the_first_assertion(db.session, savings)
    return savings


def _paid(seed_user, from_account, to_account, period, amount, **kwargs):
    """Settle a transfer three days ago through the writer: each side holds its record."""
    return create_settled_transfer(
        seed_user, db.session, from_account, to_account, period,
        amount=Decimal(amount),
        settled_on=kwargs.pop("settled_on", display_today() - timedelta(days=3)),
        **kwargs,
    )


class TestTheInstrument:
    """:func:`_sides_read_alone` collects a side read alone, and not the batched pre-load."""

    def test_a_side_read_off_an_unloaded_list_is_collected(
        self, app, seed_user, seed_periods_today,
    ):
        """Reading one side's record after the session forgot it is ONE side loaded alone."""
        with app.app_context():
            checking = seed_user["account"]
            paid = _paid(
                seed_user, checking, _a_savings_account(seed_user),
                seed_periods_today[3], "120.00",
            )
            db.session.commit()
            db.session.expire_all()

            with _sides_read_alone() as alone:
                record = transfer_side_leg(paid, is_income=False).record

            assert record is not None
            assert alone == [(paid.id, False)]

    def test_the_batched_preload_is_not_collected(
        self, app, seed_user, seed_periods_today,
    ):
        """Two transfers' four sides loaded by the pre-load, then read: nothing collected."""
        with app.app_context():
            checking = seed_user["account"]
            savings = _a_savings_account(seed_user)
            paid = [
                _paid(seed_user, checking, savings, seed_periods_today[index], "120.00")
                for index in (2, 3)
            ]
            db.session.commit()
            db.session.expire_all()

            with _sides_read_alone() as alone:
                preload_side_records(paid)
                records = [
                    transfer_side_leg(xfer, is_income=flag).record
                    for xfer in paid for flag in (False, True)
                ]

            assert all(record is not None for record in records)
            assert alone == []


class TestNoPageReadsASideAlone:
    """The pages that draw transfer sides read none of their records one side at a time."""

    @pytest.mark.parametrize("page", [
        ("/grid", {}),
        ("/dashboard", {}),
        ("/dashboard/pulse", _HTMX),
        ("/analytics/calendar", _HTMX),
        ("/savings", {}),
    ], ids=lambda page: page[0])
    def test_a_page_whose_cash_fold_reads_the_settled_sides(
        self, app, auth_client, seed_user, seed_periods_today, page,
    ):
        """The grid, the dashboard's bills and pulse, the calendar and the savings metric.

        Each draws its legs off their transfers (``grid_transfer_legs``
        loads no record, ruling **plan#2136**), and the records it meets are
        its cash fold's: two transfers Paid out of Checking, each side's
        record read off the list the fold's settled half filled when it built
        the side from that record (``recorded_transfer_legs``).  Without the
        fill each page reads Checking's two sides alone, and ``/savings``,
        whose metric folds Savings too, four.
        """
        with app.app_context():
            checking = seed_user["account"]
            savings = _a_savings_account(seed_user)
            for index, amount in ((2, "120.00"), (3, "130.00")):
                _paid(seed_user, checking, savings, seed_periods_today[index], amount)
            db.session.commit()
            db.session.expire_all()

            url, headers = page
            with _sides_read_alone() as alone:
                response = auth_client.get(url, headers=headers)

            assert response.status_code == 200
            assert alone == []

    def test_the_spending_report_reads_its_settled_legs_records_together(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """The Spending report's payment timing reads its settled legs' records in one statement.

        Two transfers out of Checking, each due and Paid on the same day, so
        both fall in that day's month (a transfer is attributed to its due
        day).  The report's timeliness metric reads each expense leg's
        record, which ``spending_analysis._settled_items`` pre-loads; without
        that pre-load it reads them alone.
        """
        with app.app_context():
            checking = seed_user["account"]
            savings = _a_savings_account(seed_user)
            due = display_today() - timedelta(days=3)
            for amount in ("120.00", "130.00"):
                _paid(
                    seed_user, checking, savings, seed_periods_today[3], amount,
                    due_date=due, settled_on=due,
                )
            db.session.commit()
            db.session.expire_all()

            with _sides_read_alone() as alone:
                response = auth_client.get(
                    f"/analytics/spending?month={due.month}&year={due.year}",
                    headers=_HTMX,
                )

            assert response.status_code == 200
            assert alone == []

    def test_the_loan_page_reads_its_settled_payments_records_together(
        self, app, auth_client, seed_user, seed_periods,
    ):
        """The loan page reads each settled payment's record, loaded in one statement.

        Three payments into the loan, each Paid on its period's first day.
        The page's settled payments are ``loan_loaders.settled_income_legs``,
        which pre-loads their records; without that pre-load the page reads
        the loan side of each alone.
        """
        with app.app_context():
            loan = _loan(seed_user)
            for index in (3, 4, 5):
                period = seed_periods[index]
                _paid(
                    seed_user, seed_user["account"], loan, period, "1000.00",
                    settled_on=period.start_date,
                )
            db.session.commit()
            db.session.expire_all()

            with _sides_read_alone() as alone:
                response = auth_client.get(f"/accounts/{loan.id}/loan")

            assert response.status_code == 200
            assert alone == []


class TestNoReaderReadsASideAlone:
    """The services that build sides for a reader of their records load those records together."""

    def test_the_cash_fold_reads_the_records_it_built_its_sides_from(
        self, app, seed_user, seed_periods_today,
    ):
        """The cash fold's settled half fills each side's list with the record it was built from.

        Two transfers Paid out of Checking: the fold over Checking
        (``cash_ledger.settled_cash_facts``) reads each side's record off the
        list ``recorded_transfer_legs`` filled; without the fill it reads
        both alone.
        """
        with app.app_context():
            checking = seed_user["account"]
            savings = _a_savings_account(seed_user)
            for index, amount in ((2, "120.00"), (3, "130.00")):
                _paid(seed_user, checking, savings, seed_periods_today[index], amount)
            db.session.commit()
            db.session.expire_all()

            with _sides_read_alone() as alone:
                facts = settled_cash_facts(checking.id, seed_user["scenario"].id)

            assert facts
            assert alone == []

    def test_the_loan_posting_probe_reads_the_payments_records_together(
        self, app, seed_user, seed_periods,
    ):
        """The loan's posting probe reads each settled payment's record, loaded in one statement.

        The fixture of the loan page's case.  ``sync_loan_postings`` walks
        the same ``loan_loaders.settled_income_legs``; without its pre-load
        the probe reads the loan side of each payment alone.
        """
        with app.app_context():
            loan = _loan(seed_user)
            for index in (3, 4, 5):
                period = seed_periods[index]
                _paid(
                    seed_user, seed_user["account"], loan, period, "1000.00",
                    settled_on=period.start_date,
                )
            db.session.commit()
            db.session.expire_all()

            with _sides_read_alone() as alone:
                sync_loan_postings(loan.id, seed_user["scenario"].id)

            assert alone == []

    def test_the_statement_match_candidates_read_their_legs_records_together(
        self, app, seed_user, seed_periods_today,
    ):
        """The match candidates read offered and settled legs' records without a load per side.

        Two transfers out of Checking still planned in started periods (the
        offers, whose records ``statement_match._leg_valuation.offered_legs``
        pre-loads) and two Paid (the settlement candidates, each side built
        from its record by ``recorded_transfer_legs``, which fills its list).
        Without the pre-load the candidates read the two offers' Checking
        sides alone; without the fill, the two Paid ones'.
        """
        with app.app_context():
            checking = seed_user["account"]
            savings = _a_savings_account(seed_user)
            for index, amount in ((2, "140.00"), (3, "150.00")):
                create_transfer(
                    seed_user, db.session, checking, savings,
                    seed_periods_today[index], amount=Decimal(amount),
                )
                _paid(seed_user, checking, savings, seed_periods_today[index], "1" + amount)
            db.session.commit()
            db.session.expire_all()

            with _sides_read_alone() as alone:
                rows = statement_match.candidates_for(
                    checking.id, pay_calendar.calendar_for(seed_user["user"].id),
                    a_basis(seed_user),
                ).rows

            assert {row.kind for row in rows} >= {RowKind.LEG, RowKind.SETTLEMENT}
            assert alone == []

    @pytest.mark.parametrize("kind", [RowKind.LEG, RowKind.SETTLEMENT])
    def test_a_re_price_reads_its_legs_record_with_no_load_of_its_own(
        self, app, seed_user, seed_periods_today, kind,
    ):
        """Re-pricing an offered or a settled leg reads its record without loading its side alone.

        The candidates' fixture.  ``statement_match``'s re-price
        (``_valuation.repriced``, which the accept and the resolve call on
        every ticked row) re-reads each row: an offered leg through
        ``offered_legs``, whose pre-load it shares, and a settled one through
        ``recorded_transfer_legs``, whose fill it shares.  Without the one or
        the other, each re-price of that kind reads its side alone.
        """
        with app.app_context():
            checking = seed_user["account"]
            savings = _a_savings_account(seed_user)
            for index, amount in ((2, "140.00"), (3, "150.00")):
                create_transfer(
                    seed_user, db.session, checking, savings,
                    seed_periods_today[index], amount=Decimal(amount),
                )
                _paid(seed_user, checking, savings, seed_periods_today[index], "1" + amount)
            db.session.commit()
            scope = a_scope(seed_user)
            rows = [row for row in scope.candidates.rows if row.kind is kind]
            db.session.expire_all()

            with _sides_read_alone() as alone:
                repriced_rows = [
                    repriced(row, scope.calendar, scope.basis, checking.id)
                    for row in rows
                ]

            assert len(rows) == 2
            assert all(row is not None for row in repriced_rows)
            assert alone == []

    def test_the_register_reads_the_accepted_legs_records_it_built_them_from(
        self, app, seed_user, seed_periods_today,
    ):
        """The statement register resolves each accepted movement's leg and fills its list with it.

        Two planned transfers out of Checking, each accepted against a bank
        line.  The register (``statement_match.accepted_register``) resolves
        each matched record's parent by ``transfer_legs.movement_parent``,
        which fills the side's list with that record; without the fill the
        register reads each accepted side alone.
        """
        with app.app_context():
            checking = seed_user["account"]
            savings = _a_savings_account(seed_user)
            statement = an_import(seed_user, declared=(
                seed_periods_today[0].start_date, display_today(),
            ))
            for index, amount in ((2, "140.00"), (3, "150.00")):
                planned = create_transfer(
                    seed_user, db.session, checking, savings,
                    seed_periods_today[index], amount=Decimal(amount),
                )
                db.session.commit()
                line = a_bank_line(
                    seed_user, statement, amount=f"-{amount}",
                    posted_on=planned.pay_period.start_date + timedelta(days=1),
                )
                scope = a_scope(seed_user)
                statement_match.accept_match(
                    a_submission(scope, lines=[line], transfers=[planned]), scope,
                )
                db.session.commit()
            db.session.expire_all()

            with _sides_read_alone() as alone:
                register = accepted_register(
                    seed_user["user"].id, checking.id, limit=None,
                )

            assert register
            assert alone == []

    def test_the_posting_writer_reads_no_record_off_a_side(
        self, app, seed_user, seed_periods_today,
    ):
        """The posting writer books a Paid transfer's records and reads none off its sides.

        ``posting_service.sync_transfer_postings`` walks the records
        ``transfer_legs.transfer_family_movements`` finds, each with the leg
        ``movement_parent`` builds from it (filling its list), and reads no
        side's record off its list.  No pre-load or fill guards it, so this
        control fires when the writer is made to read a side's record off a
        list it did not fill.
        """
        with app.app_context():
            paid = _paid(
                seed_user, seed_user["account"], _a_savings_account(seed_user),
                seed_periods_today[3], "120.00",
            )
            db.session.commit()
            db.session.expire_all()
            xfer = db.session.get(Transfer, paid.id)

            with _sides_read_alone() as alone:
                posting_service.sync_transfer_postings(xfer)

            assert alone == []


class TestNoActReadsASideAlone:
    """The transfer writer's acts read both sides' records in one statement."""

    def test_mark_paid_on_a_leg_reads_both_sides_together(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """Mark Paid on a grid leg: the seam pass reads both sides' records in one statement.

        The status applier (``transfer_service._status``) builds the pair
        through ``transfer_legs.transfer_sides``, which pre-loads both lists;
        without that pre-load the settle reads each side alone.  The leg's
        cell it renders after reads none.
        """
        with app.app_context():
            checking = seed_user["account"]
            planned = create_transfer(
                seed_user, db.session, checking, _a_savings_account(seed_user),
                seed_periods_today[4], amount=Decimal("120.00"),
            )
            db.session.commit()
            db.session.expire_all()

            with _sides_read_alone() as alone:
                response = auth_client.post(
                    f"/transfers/instance/{planned.id}/mark-done",
                    data={"leg_account_id": checking.id},
                    headers=_HTMX,
                )

            assert response.status_code == 200
            assert db.session.get(Transfer, planned.id).status.is_settled
            assert alone == []

    def test_cancel_on_a_leg_and_its_cell_read_no_record_alone(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """Cancel on a grid leg, and the leg's cell it renders, read no side's record alone.

        A cancel moves a planned transfer outside the settled band, where
        the seam writes no record, and the leg's cell
        (``routes/transfers/_helpers._render_leg_surface``) reads none
        (ruling **plan#2136**), so nothing on this path needs a pre-load.
        This control fires when the cell is made to read its leg's record.
        """
        with app.app_context():
            checking = seed_user["account"]
            planned = create_transfer(
                seed_user, db.session, checking, _a_savings_account(seed_user),
                seed_periods_today[4], amount=Decimal("120.00"),
            )
            db.session.commit()
            db.session.expire_all()

            with _sides_read_alone() as alone:
                response = auth_client.post(
                    f"/transfers/instance/{planned.id}/cancel",
                    data={"leg_account_id": checking.id},
                    headers=_HTMX,
                )

            assert response.status_code == 200
            assert db.session.get(Transfer, planned.id).status_id == (
                ref_cache.status_id(StatusEnum.CANCELLED)
            )
            assert alone == []

    def test_moving_a_paid_transfers_endpoint_reads_both_sides_together(
        self, app, seed_user, seed_periods_today,
    ):
        """Moving a Paid transfer's to-side reads both sides' records, loaded in one statement.

        The endpoint move (``transfer_service._endpoints``) builds the pair
        through ``transfer_legs.transfer_sides``; without its pre-load the
        move reads each side alone.
        """
        with app.app_context():
            checking = seed_user["account"]
            third = _a_savings_account(seed_user)
            paid = _paid(
                seed_user, checking, _savings(seed_user, "Second"),
                seed_periods_today[3], "120.00",
            )
            db.session.commit()
            db.session.expire_all()

            with _sides_read_alone() as alone:
                transfer_service.update_transfer(
                    paid.id, seed_user["user"].id, to_account_id=third.id,
                )

            assert db.session.get(Transfer, paid.id).to_account_id == third.id
            assert alone == []

    def test_a_reconcile_tick_and_its_statement_link_read_both_sides_together(
        self, app, seed_user, seed_periods_today,
    ):
        """A reconcile tick settles two transfers and links the statement, reading no side alone.

        Each tick settles its transfer through the status applier
        (``transfer_sides``' pre-load) and then records which statement
        showed its leg (``transfer_service.record_leg_clearing``), reading
        the record that settle wrote.  Without the pre-load each tick reads
        each side alone.
        """
        with app.app_context():
            checking = seed_user["account"]
            savings = _a_savings_account(seed_user)
            ticked = {
                create_transfer(
                    seed_user, db.session, checking, savings,
                    seed_periods_today[index], amount=Decimal(amount),
                ).id
                for index, amount in ((2, "140.00"), (3, "150.00"))
            }
            db.session.commit()
            db.session.expire_all()

            with _sides_read_alone() as alone:
                reconcile_service.record_reconciliation(
                    reconcile_service.ReconcileSubmission(
                        statement=_reconciled(seed_user, observed_on=display_today()),
                        entry_ids=set(), transaction_ids=set(), corrections={},
                        transfer_ids=ticked, transfer_corrections={},
                    ),
                )

            assert all(
                db.session.get(Transfer, xfer_id).status.is_settled
                for xfer_id in ticked
            )
            assert alone == []

    def test_a_patch_reads_the_day_boxes_records_together(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """A Save of a Paid transfer's popover reads both day boxes' records in one statement.

        The PATCH (``routes/transfers/mutations``) grades each side's day box
        against its record, which it pre-loads for both sides; without that
        pre-load it reads each side alone.
        """
        with app.app_context():
            paid = _paid(
                seed_user, seed_user["account"], _a_savings_account(seed_user),
                seed_periods_today[3], "120.00",
            )
            db.session.commit()
            version = paid.version_id
            db.session.expire_all()

            with _sides_read_alone() as alone:
                response = auth_client.patch(
                    f"/transfers/instance/{paid.id}",
                    data={"notes": "Moved to savings", "version_id": version},
                    headers=_HTMX,
                )

            assert response.status_code == 200
            assert db.session.get(Transfer, paid.id).notes == "Moved to savings"
            assert alone == []


class TestAMoveThatReadsNoRecordFetchesNone:
    """Ruling plan#2136's other half: a records fetch runs only where something reads it."""

    def test_a_cancel_loads_neither_sides_records(
        self, app, seed_user, seed_periods_today,
    ):
        """Cancelling a planned transfer leaves both its sides' lists unloaded.

        A cancel moves the transfer between two statuses outside the settled
        band, so it moves and reads no record
        (``balance_predicates.touches_settled_band``), and the status writer
        builds the pair without its records (``transfer_legs.side_legs``)
        where it loaded both lists for every move (plan#2138).  The act still
        runs the seam's refusals; only the fetch is gone.
        """
        with app.app_context():
            checking = seed_user["account"]
            planned = create_transfer(
                seed_user, db.session, checking, _a_savings_account(seed_user),
                seed_periods_today[4], amount=Decimal("120.00"),
            )
            db.session.commit()
            db.session.expire_all()
            xfer = db.session.get(Transfer, planned.id)

            transfer_service.update_transfer(
                xfer.id, seed_user["user"].id,
                status_id=ref_cache.status_id(StatusEnum.CANCELLED),
            )

            assert xfer.status_id == ref_cache.status_id(StatusEnum.CANCELLED)
            assert {
                side_movements_key(is_income=flag) for flag in (False, True)
            } <= inspect(xfer).unloaded
            db.session.rollback()
