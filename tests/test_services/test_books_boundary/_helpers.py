"""The helpers and constants every part of this package shares: the module-level helpers of
``tests/test_services/test_books_boundary.py``, moved verbatim by ``tools/split``.
"""

from datetime import timedelta
from decimal import Decimal

import sqlalchemy as sa

from app.enums import AccountOpeningSourceEnum, SettledDayBasisEnum

from app.extensions import db as _db
from app.models.account_opening import AccountOpening
from app import ref_cache
from app.services.cash_ledger import account_opening_fact
from app.services.settle_day import SettleDay
from tests._test_helpers import account_never_asserted, match_two_lines


_ONE_DAY = timedelta(days=1)


def _books_open_on(account):
    """Return the civil day *account*'s books open, through the app's loader."""
    return account_opening_fact(account.id).opened_on


def _entered(day):
    """Return the ``entered``-basis :class:`SettleDay` for *day*."""
    return SettleDay(day=day, basis=SettledDayBasisEnum.ENTERED)


#: Removing an opening row is not an ORM act -- ``AccountOpening._block_delete``
#: refuses one -- so the cases that need it go around the ORM, which is the same
#: reason the arm being graded lives in the database rather than in a service.
_REMOVE_ONE_OPENING = "DELETE FROM budget.account_openings WHERE id = :i"


#: Counts an account's opening rows, for the cascade case
#: (``test_the_governing_row.py``'s
#: ``test_disposing_of_the_ACCOUNT_cascades_its_books_away``): the point
#: there is that SEVERAL are disposed of together, so the number is read
#: rather than assumed.
_COUNT_OPENINGS = (
    "SELECT count(*) FROM budget.account_openings WHERE account_id = :a"
)


#: The SQL half of "which restatement governs", asked directly.  Going through
#: the function rather than re-writing its ``ORDER BY`` here is the point: a
#: hand-written query in the test would agree with the Python loader while the
#: function the CONSTRAINT calls disagreed with both.
_OPENED_ON_IN_SQL = "SELECT budget.account_books_opened_on(:a)"


#: Two restatements in one statement, with ``created_at`` supplied so the
#: RECORDING order is the test's to choose rather than the wall clock's.
_RECORD_TWO_RESTATEMENTS = """
    INSERT INTO budget.account_openings
           (account_id, opened_on, opening_equity, source_id, created_at)
    VALUES (:a, :first, 1000.00, :s, now() + :first_offset),
           (:a, :second, 1000.00, :s, now() + :second_offset)
"""


def _record_two_restatements(account, first, second, *, same_instant):
    """Append two opening restatements to *account*, oldest recorded first.

    Args:
        account: The :class:`~app.models.account.Account` to restate.
        first: The civil day the first-RECORDED restatement names.
        second: The civil day the second-RECORDED one names.
        same_instant: When true both rows share one ``created_at``, so only
            ``id`` can break the tie; when false the second is recorded a
            second later.
    """
    _db.session.execute(sa.text(_RECORD_TWO_RESTATEMENTS), {
        "a": account.id,
        "s": account_opening_fact(account.id).source_id,
        "first": first,
        "second": second,
        "first_offset": timedelta(seconds=1),
        "second_offset": timedelta(seconds=1 if same_instant else 2),
    })
    _db.session.commit()


def _an_account_with_no_movement(seed_user, name, opened_on):
    """Return an account whose books open on *opened_on* and record nothing.

    Every case in ``test_a_matched_line.py``'s
    :class:`TestAMatchedLineBoundsTheBooksToo` needs one: the matched-line arm is a window
    INSIDE the movement bound, so an account carrying a movement would have
    the bound beside it answer first and the cases would pass against an arm
    that does nothing.

    Args:
        seed_user: The seeded user bundle.
        name: The account name, unique per owner.
        opened_on: The civil day its books open.

    Returns:
        The committed :class:`~app.models.account.Account`.
    """
    account = account_never_asserted(seed_user, _db.session, name=name)
    _db.session.flush()
    _db.session.add(AccountOpening(
        account_id=account.id,
        opened_on=opened_on,
        opening_equity=Decimal("10.00"),
        source_id=ref_cache.account_opening_source_id(
            AccountOpeningSourceEnum.USER_DECLARED,
        ),
    ))
    _db.session.commit()
    return account


def _match_a_group(account, owner_id, early, late):
    """Match two bank lines on *account*, posted *early* and *late*.

    **The SQL lives in ``tests/_test_helpers/_amount_bases_and_stubs.py``,
    once.**  It was copied byte-identically into three test modules until
    an adversarial test-quality review counted them -- and the query must
    agree with :data:`app.opening_infrastructure.MATCHED_LINE_DAYS_SQL`'s
    row set, so three copies were three places for that to drift silently.

    Args:
        account: The :class:`~app.models.account.Account` to match on.
        owner_id: Its owner.
        early: The earlier line's posting day.
        late: The later one's.
    """
    match_two_lines(_db.session, account, owner_id, early, late)
