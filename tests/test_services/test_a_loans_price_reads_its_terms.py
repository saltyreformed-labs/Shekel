"""A loan's price reads its TERMS: never its own payment rows, and once per read pass.

Two controls over the loan pricing tier -- the pass's
:class:`~app.services.loan_ledger.LoanCalendars` and the amount model's
:meth:`~app.services.cash_ledger.LoanPricing.derive_cash` over it -- moved here
from ``test_loan_payment_service/test_the_contractual_payment.py`` when plan
task plan#3388 deleted that package's payment list and, with it, the test
package (ruling **R-R93**).  Neither control ever graded the deleted code: the
first used the list only to show the payment rows existed before it deleted
them, and asks the rows directly now.
"""

from contextlib import contextmanager
from datetime import date
from decimal import Decimal

from sqlalchemy import event
from sqlalchemy.engine import Engine

from app.extensions import db
from app.models.loan_params import LoanParams
from app.models.ref import AccountType
from app.models.transfer import Transfer
from app.services import account_service
from app.services.balance_at import BalanceContext
from app.services.loan_ledger import LoanCalendars
from tests._test_helpers import (
    create_transfer,
    insert_origination_rate,
    open_books_before_the_first_assertion,
)


#: The mortgage this file builds closes here.  Named because the books-boundary
#: bound below reads it as well as ``LoanParams``, and two literals that must
#: agree are one a caller can split.
_ORIGINATION = date(2024, 1, 1)


def _create_loan_account(seed_user):
    """Create a mortgage account with LoanParams for the test user.

    A LOCAL factory rather than ``tests._test_helpers.create_loan_account``,
    moved here with the two classes below.  What tells the two factories apart
    is that this one does NOT open the loan's genesis posting ledger -- the
    shared one always does -- and moving these controls onto the shared
    factory would change what the producers they grade read, not tidy them,
    so it is left as it was.

    Returns:
        Account: the mortgage account.
    """
    loan_type = db.session.query(AccountType).filter_by(name="Mortgage").one()
    account = account_service.create_account(
        account_service.AccountSpec(
            user_id=seed_user["user"].id,
            account_type_id=loan_type.id,
            name="Test Mortgage",
            anchor_balance=Decimal("200000.00"),
        ),
    )
    db.session.add(account)
    db.session.flush()

    # **Its books open before the loan does** (plan step X-f3c-2b, ruling
    # **R-HG**): ``create_account`` puts them on the assertion's own day, which
    # is the frozen today, and the loan originates before that.
    open_books_before_the_first_assertion(
        db.session, account, also_before=_ORIGINATION,
    )

    params = LoanParams(
        account_id=account.id,
        original_principal=Decimal("250000.00"),
        term_months=360,
        origination_date=_ORIGINATION,
        payment_day=1,
    )
    db.session.add(params)
    db.session.flush()
    # DH-#56: the loan's base rate lives in the origination RateHistory row,
    # and the resolver raises on an empty rate-change feed, so seed it.
    insert_origination_rate(params, Decimal("0.06500"))
    return account


def _transfers_into(loan):
    """Return how many transfers pay into *loan*: the loan's payment rows.

    A loan's payments are ONE relation, ``budget.transfers``, since plan step
    balance:X-bi-6-4d-2 (ruling **R-BAL258**), so this count is the whole of
    what a pricing tier that read the loan's own payments could read.
    """
    return db.session.query(Transfer).filter(
        Transfer.to_account_id == loan.id,
    ).count()


@contextmanager
def _statements_issued():
    """Record every SQL statement the engine executes inside the block.

    The same probe ``tests/test_arch`` uses, at the Engine level rather than a
    session's, so it sees what the PROCESS issued whichever session issued it.
    It cannot see ``BEGIN`` / ``COMMIT`` / ``ROLLBACK`` -- psycopg issues those
    through the connection rather than a cursor -- which is why the assertions
    below name a TABLE rather than counting a total.

    Yields:
        The list of normalised SQL strings, appended to as the block runs.
    """
    seen = []

    def _record(conn, cursor, statement, params, context, executemany):  # noqa: ARG001
        seen.append(" ".join(statement.split()))

    event.listen(Engine, "before_cursor_execute", _record)
    try:
        yield seen
    finally:
        event.remove(Engine, "before_cursor_execute", _record)


class TestALoansPriceDoesNotReadItsOwnPayments:
    """A loan's monthly P&I is its TERMS, so its payment rows cannot move it.

    The cycle these tests exist to keep deleted: the loan's pricing terms
    (``_resolve_loan_basis`` until plan step recurrence:R25, the pass's
    :class:`~app.services.loan_ledger.LoanCalendars` since) used to run
    ``loan_payment_service.load_loan_context`` -- and, while it loaded one,
    the loan's payment list -- purely to read
    ``resolve_loan(...).monthly_payment`` back out, which put the loan's own
    payment rows on the path that PRICES those rows.

    **Two tests and not one, because they fail on different reintroductions.**
    The value test catches a producer that reads the rows and lets them change
    the answer; the statement test catches one that reads them and happens to
    agree today, which is the shape that returns silently.  The second is the
    one that would have caught the original coupling, since that producer also
    answered the right number.

    Measured on a production clone 2026-08-31 by
    ``tests/manual/verify_loan_pricing_ignores_payment_feed.py``: each live
    loan answers one figure across its FULL record feed, an EMPTY one, its
    confirmed records alone, and the full feed DOUBLED.  *That harness
    fed the deleted producer each feed through* ``LoanInputs.payments``*, and
    went with that field at plan step recurrence:R16-e: the resolver takes no
    payment feed at all now.*

    *The first test proved the rows existed, and then that they were gone,
    through the payment list* (``get_payment_history``) *until plan task
    plan#3388 deleted the list; it counts the transfers into the loan
    directly since, which is the relation the list was read from.*
    """

    def test_the_price_is_unchanged_when_every_payment_row_is_deleted(
        self, app, db, seed_user, seed_periods,
    ):
        """Deleting every payment row into the loan does not move its terms.

        The rows are removed by DELETE rather than by soft-deleting or
        cancelling: those two are read as statements about whether a row counts,
        and a producer could honour them while still reading the rows.  Removing
        the rows leaves nothing for a coupled producer to read at all.

        **The rows are ONE relation, ``budget.transfers``, since plan step
        balance:X-bi-6-4d-2**, so the transfers into the loan are what is
        deleted.  The count is asserted three before the delete and zero after
        it, which is the non-vacuity: a delete that removed nothing would leave
        the comparison grading two reads of an unchanged table.
        """
        with app.app_context():
            loan = _create_loan_account(seed_user)
            for period in seed_periods[:3]:
                create_transfer(
                    seed_user, db.session, seed_user["account"], loan, period,
                    Decimal("1500.00"),
                )
            db.session.commit()

            before = LoanCalendars().loan_calendar_of(loan.id)
            assert before is not None
            assert _transfers_into(loan) == 3, (
                "the loan must hold payment rows for deleting them to mean anything"
            )

            db.session.query(Transfer).filter(
                Transfer.to_account_id == loan.id,
            ).delete(synchronize_session=False)
            db.session.commit()
            assert _transfers_into(loan) == 0, (
                "a transfer into the loan survived the delete"
            )

            after = LoanCalendars().loan_calendar_of(loan.id)
            assert after is not None
            # The whole TERM SET, period by period -- not one resolved figure.
            # A producer that read the rows could agree on the period governing
            # one date while differing on another; comparing the set closes
            # that.  (The basis holds periods rather than a scalar since plan
            # step X-au-g-2b, ruling R-IJ.)
            assert [
                (p.start_date, p.annual_rate, p.period_pi) for p in after.periods
            ] == [
                (p.start_date, p.annual_rate, p.period_pi) for p in before.periods
            ]
            assert after.payment_day == before.payment_day

    def test_pricing_a_loan_issues_no_statement_against_the_payment_rows(
        self, app, db, seed_user, seed_periods,
    ):
        """The pricing path queries neither ``budget.transactions`` nor ``budget.transfers``.

        The direction that matters: a producer reading the rows and agreeing
        with them is indistinguishable from one that never read them, by value
        alone.  Naming the TABLE rather than counting statements is what makes
        this survive an eager load, which folds into a parent query and moves no
        count.  ``budget.transfers`` holds a loan's payments, and it is also
        the table ``_loan_pricing._load_live_payment_configs`` once read -- the
        coupling this control exists to keep out.
        """
        with app.app_context():
            loan = _create_loan_account(seed_user)
            for period in seed_periods[:3]:
                create_transfer(
                    seed_user, db.session, seed_user["account"], loan, period,
                    Decimal("1500.00"),
                )
            db.session.commit()
            db.session.expire_all()

            with _statements_issued() as seen:
                basis = LoanCalendars().loan_calendar_of(loan.id)

            assert basis is not None
            assert seen, "the probe recorded nothing, so it graded nothing"
            touching = [
                sql for sql in seen
                if "budget.transactions" in sql or "budget.transfers" in sql
            ]
            assert not touching, (
                "pricing a loan read its own payment rows: "
                f"{touching}"
            )


#: The tables a loan's CONTRACT TERMS load from: its params, its rate history
#: and its escrow lines with their versions.
_TERMS_TABLES = (
    "budget.loan_params", "budget.rate_history",
    "budget.escrow_lines", "budget.escrow_component_versions",
)


class TestOnePassReadsOneLoanCalendar:
    """A loan's charges and its payments' price read ONE calendar per read pass.

    Plan step recurrence:R25 (ruling **R-R105**: "one bundle that pricing and
    charging both read"; finding **REC-545**).  Pricing kept a second bundle of
    a loan's terms beside the calendar the walk's charges are built from, so a
    pass that walked a derive-mode loan and priced its payments loaded the
    terms twice.  Both now read the pass's one
    :class:`~app.services.loan_ledger.LoanCalendars`.
    """

    def test_pricing_after_the_walk_loads_no_terms_again(
        self, app, db, seed_user, seed_periods,
    ):
        """The walk loads the terms; the pass's pricer then issues no terms query."""
        with app.app_context():
            loan = _create_loan_account(seed_user)
            db.session.commit()
            ctx = BalanceContext.build(seed_user["user"].id)
            ctx.loan_walk(loan)

            with _statements_issued() as seen:
                cash = ctx.amounts().loans.derive_cash(
                    date(2026, 3, 1), date(2026, 2, 20), loan.id, Decimal("0.00"),
                )

            assert cash is not None
            touching = [
                sql for sql in seen if any(table in sql for table in _TERMS_TABLES)
            ]
            assert not touching, (
                f"the pricer loaded the loan's terms a second time: {touching}"
            )

    def test_the_probe_sees_the_terms_load_without_the_walk(
        self, app, db, seed_user, seed_periods,
    ):
        """The control: with no walk first, the same price DOES load the terms.

        Without it the case above could pass on a probe that sees nothing.
        """
        with app.app_context():
            loan = _create_loan_account(seed_user)
            db.session.commit()
            ctx = BalanceContext.build(seed_user["user"].id)

            with _statements_issued() as seen:
                ctx.amounts().loans.derive_cash(
                    date(2026, 3, 1), date(2026, 2, 20), loan.id, Decimal("0.00"),
                )

            assert any("budget.loan_params" in sql for sql in seen), seen
            assert any("budget.rate_history" in sql for sql in seen), seen
