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

**So every path takes the same control: inside it, no side's records are
read alone.**  :func:`_sides_read_alone` collects each statement whose
criteria name one side's link and not the other's -- the lazy load one
:attr:`~app.services.transfer_legs.TransferLeg.record` read runs on a list
nobody loaded, and an explicit query by one side's column -- and each case
below asserts it collects none while its fixture holds sides the path builds.
The batched pre-load names both sides and the fill issues no statement, so
neither is collected.  Zero holds whatever rows the fixture builds, where a
total statement count pinned today's figure would break on unrelated work.
Until the review of plan#2086 (finding M3) it collected relationship lazy
loads alone, so a per-side query passed every control; and every page
fixture held Paid transfers alone, whose lists the cash fold fills, so a
page's read of a planned side's record passed too (finding M2).  Each page
case holds planned transfers since.

**What makes each control fire is named in its docstring, and was measured**
by deleting that pre-load or fill (or, for a path that reads no record,
adding a read) at plan#2090's commit, and for the planned sides and the
per-side query at plan#2144's: the case then collects the sides the fixture
puts in its path.  The last two classes hold the ruling's other halves: an
act that reads no record fetches none (plan#2138), and a batch act fetches
its transfers' records in one pre-load for all of them while its reader
fetches none (finding L1).  :func:`_side_record_fetches` counts those.
"""

from contextlib import contextmanager
from datetime import timedelta
from decimal import Decimal

import pytest
from sqlalchemy import ColumnElement, Join, Select, event, inspect
from sqlalchemy.orm import Session, with_parent
from sqlalchemy.sql import visitors

from app import ref_cache
from app.enums import StatusEnum
from app.extensions import db
from app.models.transaction_entry import TransactionEntry
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


def _side_links():
    """Return ``{link column: is_income}``: the column each side's records are filed by.

    Read off the relationship :func:`side_movements_key` names for each side
    -- the one statement of which list is a side's -- as the column its join
    keys on the record (its remote side), so the instrument states no second
    spelling of which column is which side's.
    """
    transfer_mapper = inspect(Transfer)
    return {
        column: is_income
        for is_income in (False, True)
        for column in transfer_mapper.relationships[
            side_movements_key(is_income=is_income)
        ].remote_side
    }


def _sides_named(statement) -> set[bool]:
    """Return the sides whose link column *statement*'s criteria name.

    The criteria are a SELECT's WHERE clause and the ON clause of every join
    in its FROM list (``get_final_froms``, which computes the joins an eager
    load adds too), and never its columns: a SELECT of the records names both
    link columns whatever narrows it.  A column counts by lineage
    (``shares_lineage``), so the ORM's annotated spelling of a link and an
    aliased one both name it.

    Args:
        statement: The statement a session is about to execute.

    Returns:
        ``set()`` for a statement naming neither link (or no SELECT),
        ``{False, True}`` for one narrowed by both -- the shape every query of
        side records ``app/`` writes takes (``transfer_legs._records._movement_link``
        and ``_links_any``) -- and one side alone for a per-side read.
    """
    if not isinstance(statement, Select):
        return set()
    criteria = [] if statement.whereclause is None else [statement.whereclause]
    criteria.extend(
        node.onclause
        for source in statement.get_final_froms()
        for node in visitors.iterate(source)
        if isinstance(node, Join) and node.onclause is not None
    )
    links = _side_links()
    return {
        is_income
        for criterion in criteria
        for node in visitors.iterate(criterion)
        if isinstance(node, ColumnElement)
        for link, is_income in links.items()
        if link.shares_lineage(node)
    }


@contextmanager
def _collecting(collect):
    """Collect what *collect* answers for each statement a session executes inside this block.

    Args:
        collect: ``ORMExecuteState -> item or None``; ``None`` collects nothing.

    Yields:
        The items, appended in execution order as the block runs.
    """
    found = []

    def _record(orm_state):
        item = collect(orm_state)
        if item is not None:
            found.append(item)

    event.listen(Session, "do_orm_execute", _record)
    try:
        yield found
    finally:
        event.remove(Session, "do_orm_execute", _record)


def _sides_read_alone():
    """Collect each statement that reads ONE side's records, not both, inside this block.

    Every query of side records ``app/`` writes narrows by both sides'
    links at once (:func:`_sides_named`), so a statement naming one side's
    link and not the other's is a side read on its own: the lazy load one
    :attr:`~app.services.transfer_legs.TransferLeg.record` read runs on a list
    nobody loaded (the relationship's own SELECT, keyed on that side's link),
    and an explicit query by one side's column, the shape ruling
    **plan#2136** removed from ``transfer_sides``.  A statement count could
    not tell either from any other SELECT on ``budget.transaction_entries``.

    Returns:
        A context manager yielding ``[(transfer id, is_income), ...]``, one
        per such statement in execution order: the transfer is the one a lazy
        load read the list of (``lazy_loaded_from``), and ``None`` for a query
        of its own, which names no one parent.
    """
    def _alone(orm_state):
        sides = _sides_named(orm_state.statement)
        if len(sides) != 1:
            return None
        parent = orm_state.lazy_loaded_from
        return (None if parent is None else parent.identity[0], *sides)

    return _collecting(_alone)


def _side_record_fetches():
    """Collect each statement that fetches transfer sides' records inside this block.

    Any statement whose criteria name a side's link (:func:`_sides_named`):
    a batched pre-load, a loader's join, a lazy load, a per-side query.

    Returns:
        A context manager yielding one ``set`` of the sides named per such
        statement, in execution order.
    """
    return _collecting(lambda orm_state: _sides_named(orm_state.statement) or None)


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
    """:func:`_sides_read_alone` collects a side read alone, lazily or by a query, not the pre-load."""

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

    @pytest.mark.parametrize("is_income", [False, True])
    def test_a_query_of_one_sides_records_is_collected(
        self, app, seed_user, seed_periods_today, is_income,
    ):
        """A query narrowed by one side's link is ONE side read alone, naming no transfer.

        The shape ruling **plan#2136** removed from ``transfer_sides``: the
        side's records fetched by a query of their own rather than with the
        other side's.  No list is read, so no relationship load runs and only
        the statement's criteria show it.
        """
        with app.app_context():
            paid = _paid(
                seed_user, seed_user["account"], _a_savings_account(seed_user),
                seed_periods_today[3], "120.00",
            )
            db.session.commit()

            with _sides_read_alone() as alone:
                records = db.session.query(TransactionEntry).filter(with_parent(
                    paid, getattr(Transfer, side_movements_key(is_income=is_income)),
                )).all()

            assert len(records) == 1
            assert alone == [(None, is_income)]

    def test_the_batched_preload_is_one_fetch_and_not_collected(
        self, app, seed_user, seed_periods_today,
    ):
        """Two transfers' four sides loaded by the pre-load, then read: ONE fetch, nothing alone.

        :func:`_side_record_fetches` counts the pre-load as one statement
        naming both sides, and the four reads after it fetch nothing.
        """
        with app.app_context():
            checking = seed_user["account"]
            savings = _a_savings_account(seed_user)
            paid = [
                _paid(seed_user, checking, savings, seed_periods_today[index], "120.00")
                for index in (2, 3)
            ]
            db.session.commit()
            db.session.expire_all()

            with _sides_read_alone() as alone, _side_record_fetches() as fetches:
                preload_side_records(paid)
                records = [
                    transfer_side_leg(xfer, is_income=flag).record
                    for xfer in paid for flag in (False, True)
                ]

            assert all(record is not None for record in records)
            assert alone == []
            assert fetches == [{False, True}]


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

        Two more out of Checking are still planned, in today's period and
        the next, because no loader fills a planned side's list: a page that
        reads a planned side's record reads it alone, where with only Paid
        transfers the fold's fill hid that read (the review of plan#2086,
        finding M2).
        """
        with app.app_context():
            checking = seed_user["account"]
            savings = _a_savings_account(seed_user)
            for index, amount in ((2, "120.00"), (3, "130.00")):
                _paid(seed_user, checking, savings, seed_periods_today[index], amount)
            for index, amount in ((4, "140.00"), (5, "150.00")):
                create_transfer(
                    seed_user, db.session, checking, savings,
                    seed_periods_today[index], amount=Decimal(amount),
                )
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
        record, which ``spending_analysis.query_settled_expenses`` asks the
        one plan-item loader to pre-load (``side_records``); without that
        pre-load it reads them alone.  Two more due that day are still
        planned, which the report does not draw today (it loads settled
        money only, ``settled_status_ids``); they stand in the fixture so a
        read of a planned side's record, whose list nothing fills, would be
        seen here as on every other page (finding M2).
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
            for amount in ("140.00", "150.00"):
                create_transfer(
                    seed_user, db.session, checking, savings,
                    seed_periods_today[3], amount=Decimal(amount), due_date=due,
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
        the loan side of each alone.  Two later payments are still planned,
        whose lists nothing fills, so a read of a planned side's record is
        seen too (finding M2).
        """
        with app.app_context():
            loan = _loan(seed_user)
            for index in (3, 4, 5):
                period = seed_periods[index]
                _paid(
                    seed_user, seed_user["account"], loan, period, "1000.00",
                    settled_on=period.start_date,
                )
            for index in (6, 7):
                create_transfer(
                    seed_user, db.session, seed_user["account"], loan,
                    seed_periods[index], amount=Decimal("1000.00"),
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

        The writer pre-loads every ticked transfer's records in one
        statement (the transfer arm's loader, ``reconcile_service._transfers``),
        then each tick settles its transfer through the status applier
        (``transfer_sides``, whose own pre-load finds both lists loaded) and
        records which statement showed its leg
        (``transfer_service.record_leg_clearing``), reading the record that
        settle wrote.  Either pre-load alone keeps a tick from reading a side
        alone; without both, each tick reads each side alone.
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


class TestAReconcileFetchesItsTransfersRecordsOnce:
    """Ruling plan#2136's "one query for all of them" for a batch act, and none for its reader."""

    @staticmethod
    def _three_planned(seed_user, seed_periods_today):
        """Three transfers out of Checking still planned, in periods started before today."""
        checking = seed_user["account"]
        savings = _a_savings_account(seed_user)
        ticked = {
            create_transfer(
                seed_user, db.session, checking, savings,
                seed_periods_today[index], amount=Decimal(amount),
            ).id
            for index, amount in ((1, "130.00"), (2, "140.00"), (3, "150.00"))
        }
        db.session.commit()
        db.session.expire_all()
        return ticked

    def test_a_reconcile_of_three_transfers_pre_loads_their_records_in_one_statement(
        self, app, seed_user, seed_periods_today,
    ):
        """Ticking three transfers fetches all their records in one pre-load, not one per settle.

        The writer's narrowing (the transfer arm's loader given the ticked
        ids, ``reconcile_service._transfers``) pre-loads every ticked
        transfer's records in ONE statement, so each settle's pair
        (``transfer_legs.transfer_sides``) finds both lists loaded and
        fetches nothing.  The act's other side-record fetches are each
        settle's posting walk (``transfer_legs.transfer_family_movements``),
        one per transfer, which re-fetches what the pre-load loaded (finding
        **plan#2147**).  So ``1 + 3``; without the pre-load each settle
        fetches its own pair, ``3 + 3``.
        """
        with app.app_context():
            ticked = self._three_planned(seed_user, seed_periods_today)

            with _sides_read_alone() as alone, _side_record_fetches() as fetches:
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
            assert len(fetches) == 1 + len(ticked)

    def test_the_reconcile_panel_fetches_no_record_of_the_transfers_it_offers(
        self, app, seed_user, seed_periods_today,
    ):
        """The panel offers the three transfers and fetches none of their records.

        Its reader (the same loader with no narrowing) prices each leg off
        its transfer (``transaction_service.settle_amount``) and reads no
        side's record, so it fetches none.
        """
        with app.app_context():
            ticked = self._three_planned(seed_user, seed_periods_today)
            checking_id = seed_user["account"].id

            with _side_record_fetches() as fetches:
                groups = reconcile_service.outstanding_set(
                    _reconciled(seed_user, observed_on=display_today()),
                ).groups

            assert {
                group.key for group in groups
                if group.settle.kind is reconcile_service.OfferKind.TRANSFER
            } == {(xfer_id, checking_id) for xfer_id in ticked}
            assert fetches == []
