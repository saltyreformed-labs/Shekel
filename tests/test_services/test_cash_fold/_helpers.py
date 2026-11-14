"""The helpers and constants every part of this package shares: the module-level helpers of
``tests/test_services/test_cash_fold.py``, moved verbatim by ``tools/split``.
"""

from datetime import date, datetime, timezone

from app.utils.dates import DISPLAY_TIMEZONE
from app.extensions import db
from app.services.balance_at._cash_fold import assembled_fold, balances_at
from tests._test_helpers import (
    open_books_before_the_first_assertion,
    read_pass,
    reassert_balance_on,
    restate_account_opening,
)


# An as-of far past every valuation date these ACTUAL-tier tests read, so the
# PLANNED tier (which clamps to ``as_of + 1``) cannot reach them.  The tiers are
# graded separately on purpose: a test that mixed them could pass with either
# one wrong.
_LATE_AS_OF = date(2026, 12, 31)


def _instant(year, month, day, hour=0, minute=0, second=0):
    """Return the aware-UTC instant of a wall-clock moment on the USER's day.

    The arguments are read as the DISPLAY timezone -- the clock the user is
    actually looking at -- and converted to UTC for storage, which is the
    direction production runs in: the settle day and ``created_at`` are stamped
    when the user acts and stored UTC.

    **It read them as UTC until ruling R-DH (b)** (2026-07-31), and the default
    ``hour=0`` then meant midnight UTC -- 7pm or 8pm the PREVIOUS Eastern day.
    So a fixture writing ``date(2026, 1, 15)`` to mean "this settled on the
    15th" pinned an event the fold correctly places on the 14th, and five tests
    in this class asserted figures for a day their own setup had not built.
    Reading the arguments as Eastern makes the helper mean what every call site
    already said it meant, and it preserves same-day ORDERING exactly: two
    moments on one day shift by the same offset.
    """
    return datetime(
        year, month, day, hour, minute, second, tzinfo=DISPLAY_TIMEZONE,
    ).astimezone(timezone.utc)


def _fold(account, scenario, days, as_of=_LATE_AS_OF):
    """Fold *account* at each of *days*, returning ``{date: Decimal}``."""
    ctx = read_pass(account, scenario, as_of)
    return balances_at(assembled_fold(account, ctx), list(days))


#: The day the books open for the two streams (in
#: ``test_the_opening_equity_is_the_seed.py`` and ``test_scope_and_totality.py``)
#: that carry a record dated
#: before their opening ASSERTION.  Chosen BEFORE the 2026-01-15 record they
#: carry, because ruling **R-HG** (plan step X-f3c-2b) makes a movement dated
#: on or before the books unstorable -- and a record sitting between the books
#: and the FIRST assertion is the production shape this fold has to get right
#: (Checking's books open 2026-03-26 and its first assertion is 2026-03-27).
#:
#: **It is NOT the default, and that is this constant's whole discipline.**  A
#: blanket default here re-dated the books of every fixture in the file: it
#: moved the two real-shape cases 80-odd days off the day their own docstrings
#: name, and at the ~15 sites asserting 2026-01-01 it moved the books FORWARD,
#: to a fortnight AFTER the opening assertion -- a state production cannot
#: reach by any door, and one those fixtures cannot detect because their seed
#: equity and their opening assertion are the same $1,000.00.  Found by
#: adversarial review, 2026-08-28.
_BOOKS_OPEN_ON = date(2026, 1, 14)


def _opened_at(account, at, books_open_on=None):
    """Open the account's books and assert its balance on *at*'s day (builder).

    **TWO acts, and they are spelled as two** (plan step X-f3c-2c).  It used to
    be one, because ``restamp_opening_assertion`` re-dated the stored
    origination assertion and restated the opening as a side effect.  An
    assertion is append-only, so what a fixture has instead is the pair a
    production owner has: say what the balance was on a day
    (``reassert_balance_on``), and say when the books opened
    (``restate_account_opening`` -- the door plan step X-f3c-2b-2a builds).

    **The default moves the books BACKWARD ONLY, and the first draft of this
    helper did not.**  It restated them to the day before *at* unconditionally,
    which is right when *at* is the account's earliest assertion and wrong the
    moment it is not: the seeded account originates on the owner's bootstrap
    day, so ``_opened_at(account, _instant(2026, 1, 1))`` shoved the books two
    years FORWARD, past an assertion that already stood.  Nothing in ``app/``
    can produce that -- ``account_service.create_account`` is the only
    ``AccountOpening`` writer and it dates books and assertion together, and
    ``open_books_before_the_first_assertion`` exists to keep the books ahead of
    everything -- so ~55 fixtures were carrying a state the app forbids, and
    the first producer to grade "no assertion precedes the books" would have
    failed all of them at once, about nothing they were testing.  Found by the
    adversarial review of this step.

    Args:
        account: The account to open.
        at: The aware-UTC instant of the assertion.  Its display civil day is
            the day the asserted balance was TRUE.
        books_open_on: The civil day the account's BOOKS open.  ``None``, the
            default, bounds them before every day a row could land on
            (``open_books_before_the_first_assertion``), which is backward-only
            and therefore cannot strand a row that was legal a moment ago.
            Pass a day only when the case needs the SPAN between the books and
            an assertion, and pass the day that case names rather than a shared
            one -- and know that it is NOT bounded: a day later than an
            existing assertion builds a state no door can.

    Returns:
        The appended assertion row.
    """
    row = reassert_balance_on(db.session, account, at)
    if books_open_on is None:
        open_books_before_the_first_assertion(db.session, account)
    else:
        restate_account_opening(db.session, account, books_open_on)
    return row
