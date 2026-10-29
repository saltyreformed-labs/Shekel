"""The helpers and constants every part of this package shares: the module-level helpers of
``tests/test_services/test_statement_match/test_residual.py``, moved verbatim by
``tools/split``.
"""

from dataclasses import dataclass
from decimal import Decimal

from app import ref_cache
from app.extensions import db
from app.models.journal_entry import Posting
from app.models.ledger_account import LedgerAccount
from app.models.transaction import Transaction
from app.services import statement_match
from app.services.statement_match import RowKind

from tests.test_services.test_statement_match._builders import (
    a_bank_line,
    a_later_period,
    a_scope,
    a_submission,
    a_transaction,
    an_import,
)
from tests._test_helpers import payback_row_of


def _submit(
    seed_user, lines=(), transactions=(), entries=(), residual=None,
    attributed=None, transfers=(),
):
    """Accept a match naming exactly these subjects.

    Derived per call, so every submission sees the rows the test has staged,
    and the SAME scope builds the submission and applies it -- the two-moment
    flow the screen has (finding **N-336**).

    Args:
        seed_user: The seeded user bundle.
        lines: Bank line rows.
        transactions: Transaction rows.
        entries: Purchase rows.
        residual: The difference the screen showed and the owner ticked.
        attributed: Which member carries that difference, as a
            ``(kind, orm_row)`` pair, or ``None``.
        transfers: Transfer rows, each ticked as its leg on this account
            (leaf ``balance:X-bi-6-4c-1``).

    Returns:
        The :class:`~app.services.statement_match.AcceptedMatch`.
    """
    scope = a_scope(seed_user)
    return statement_match.accept_match(
        a_submission(
            scope, lines=lines, transactions=transactions, entries=entries,
            residual=residual, attributed=attributed, transfers=transfers,
        ),
        scope,
    )


def _minted(seed_user):
    """Return every uncategorized row on the seeded owner's checking account.

    **Keyed on ``category_id IS NULL`` rather than on the name**, because the
    name is a display decision this step also makes and a test that asserted
    both through one predicate would pass a rename it should have caught.

    **No transfer term.**  A transfer came with two shadow rows until plan
    step ``balance:X-bi-6-4d-2``, and the ``create_transfer`` fixture left a
    shadow's category NULL, so this helper excluded shadows by
    ``transfer_id IS NULL`` (without that term it reported one as a row this
    door minted, in a first version of the group cases).  A transfer is one
    row since that step (ruling **R-BAL258**) and the database refused a row
    naming one (``ck_transactions_names_no_transfer``, until plan step
    balance:X-bi-6-5 dropped the column), so the term went at
    that step's vacuous-residue checkpoint: at each of the helper's 47
    calls in a run of the package, no row named a transfer (measured).

    Args:
        seed_user: The seeded user bundle.

    Returns:
        The rows, ascending by id.
    """
    return (
        db.session.query(Transaction)
        .filter(
            Transaction.account_id == seed_user["account"].id,
            Transaction.category_id.is_(None),
        )
        .order_by(Transaction.id)
        .all()
    )


def _uncategorized_net(seed_user, ledger_class):
    """Return the debit-positive net of the per-owner Uncategorized account.

    **The proof is over the LEDGER and not over the row**, because the row
    carrying no category is only half the claim: what makes ruling **R-FN**'s
    mechanism real is that ``posting_service`` routes such a row's counter leg
    to the fallback account.  Measured 2026-08-23 on a production clone, no
    fallback account existed and no transaction carried a NULL category, so
    this door is the first writer of both and nothing else in the app would
    notice if the routing were wrong.

    Args:
        seed_user: The seeded user bundle.
        ledger_class: Income or Expense
            (:class:`~app.enums.LedgerAccountClassEnum`).

    Returns:
        The account's net, or ``None`` when no such account exists yet.
    """
    account = (
        db.session.query(LedgerAccount)
        .filter(
            LedgerAccount.user_id == seed_user["user"].id,
            LedgerAccount.is_owner_bucket.is_(True),
            LedgerAccount.class_id == ref_cache.ledger_account_class_id(
                ledger_class,
            ),
        )
        .one_or_none()
    )
    if account is None:
        return None
    return db.session.query(
        db.func.coalesce(db.func.sum(Posting.amount), Decimal("0.00")),
    ).filter(Posting.ledger_account_id == account.id).scalar()


@dataclass(frozen=True)
class _ALine:
    """A bank line, as :meth:`MatchSides.of` structurally types one.

    A value rather than an ORM row, because the point of the cases using it is
    a figure ``bank_statement_lines.amount`` is too narrow to hold.
    """

    amount: Decimal


@dataclass(frozen=True)
class _ARow:
    """An app row, as :meth:`MatchSides.of` structurally types one.

    **``MatchSides`` reads a cash figure and nothing else**, so this is the
    whole of what that class types.  A case about a LANDING needs
    :class:`_ANamedRow` below instead: attributing a difference is a statement
    about one member of a set, so it reads the subject too -- and a landing
    handed one of these raises ``AttributeError`` rather than answering
    wrongly, which is how seven cases here found out.
    """

    cash_amount: Decimal


@dataclass(frozen=True)
class _ANamedRow:
    """An app row as :class:`DifferenceLanding` types one: a SUBJECT and a cash.

    ``_ARow`` above carries only what ``MatchSides`` reads.  A landing also
    asks WHICH row this is, because attributing a difference is a statement
    about one member of a set -- so it needs the ``(kind, row_id)`` pair every
    subject in this package is keyed on.

    A value rather than an ORM row for the reason ``_ARow`` is one: the cases
    below are about the arithmetic and the key, and staging a database row
    would only narrow which figures and which pairs can be covered.
    """

    kind: RowKind
    row_id: int
    cash_amount: Decimal


def a_named_row(kind, row_id, cash_amount):
    """Return a subject-bearing stub row for the landing cases.

    Args:
        kind: Its :class:`~app.services.statement_match.RowKind`.
        row_id: Its primary key within that kind's table.
        cash_amount: Its signed cash effect, as a string.

    Returns:
        The :class:`_ANamedRow`.
    """
    return _ANamedRow(
        kind=kind, row_id=row_id, cash_amount=Decimal(cash_amount),
    )


def _a_transfer(seed_user, *, amount="100.00"):
    """Return a Projected transfer off the seeded checking account, to a new Savings.

    Built through ``transfer_service.create_transfer``, the sole creation
    chokepoint, so the transfer and its invariants are the real ones.  A
    case ticks it as its leg on checking, the EXPENSE side.  It returned
    that side's shadow row until plan step ``balance:X-bi-6-4d-2`` (ruling
    **R-BAL167** class 4): no shadow can be stored since revision
    ``82f7b7ed4332`` deleted them (ruling **R-BAL258**), and its one caller
    read the shadow's transfer.

    Args:
        seed_user: The seeded user bundle.
        amount: The transfer amount.

    Returns:
        The :class:`~app.models.transfer.Transfer`.
    """
    # Pylint: ``import-outside-toplevel`` -- one caller, and the transfer
    # service is not otherwise part of this module's subject.
    # pylint: disable=import-outside-toplevel
    from app.services import account_service
    from tests._test_helpers import create_transfer

    destination = account_service.create_account(
        account_service.AccountSpec(
            user_id=seed_user["user"].id,
            account_type_id=seed_user["account"].account_type_id,
            name="Savings",
            anchor_balance=Decimal("100.00"),
            observed_on=seed_user["bootstrap_period"].start_date,
        )
    )
    db.session.flush()
    transfer = create_transfer(
        seed_user, db.session, seed_user["account"], destination,
        seed_user["bootstrap_period"], amount=Decimal(amount),
    )
    db.session.flush()
    return transfer


def _a_cc_payback(seed_user, *, amount="180.00"):
    """Return the Projected CC payback the app mints for one card purchase.

    **The one row shape whose figure is not its own to state and which the
    matcher still OFFERS** (ruling **R-BAL81**): an envelope holding
    purchases -- the other member of that census -- is worth ``0`` to the
    offer and is never a candidate, so a case grading
    ``_reject_uncorrectable_row``'s "no figure of its own" clause on a group
    stages a payback.  Through the app's own producer
    (:func:`tests._test_helpers.payback_row_of`, ruling **R-BAL59**): a card
    purchase of *amount* under an envelope of its own, and
    ``sync_entry_payback`` minting the payback in the paycheck after it --
    a hand-built payback with no card spend behind it is the row that
    producer DELETES.

    Args:
        seed_user: The seeded user bundle.
        amount: The card purchase's figure, as a string; the payback repays
            exactly it.

    Returns:
        The minted :class:`~app.models.transaction.Transaction`.
    """
    envelope = a_transaction(
        seed_user, name="Card Spend", amount="300.00", is_envelope=True,
    )
    a_later_period(seed_user)
    return payback_row_of(
        db.session, seed_user, envelope, Decimal(amount),
        seed_user["bootstrap_period"].start_date,
    )


def _payroll(seed_user, bank="2573.43"):
    """Stage finding N-239's shape: a deposit the app's rows fall short of.

    Args:
        seed_user: The seeded user bundle.
        bank: What the bank actually paid.

    Returns:
        ``(line, salary, allowance)``.
    """
    statement = an_import(seed_user)
    line = a_bank_line(
        seed_user, statement, amount=bank,
        posted_on=seed_user["bootstrap_period"].start_date,
    )
    salary = a_transaction(
        seed_user, name="Salary", amount="2473.38", income=True,
    )
    allowance = a_transaction(
        seed_user, name="Allowance", amount="100.00", income=True,
    )
    return line, salary, allowance
