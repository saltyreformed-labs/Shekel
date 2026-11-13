"""The helpers and constants every part of this package shares: the module-level helpers of
``tests/test_services/test_cash_period_view.py``, moved verbatim by ``tools/split``.
"""

from datetime import date, timedelta

from app.services.balance_at._cash_fold import assembled_fold, balances_at
from app.services.balance_at._cash_periods import period_view_of
from tests._test_helpers import period_window, read_pass


_ONE_DAY = timedelta(days=1)


#: The day the books open for the two shapes in this package that carry a record dated
#: before their opening ASSERTION.  Ruling **R-HG** (plan step X-f3c-2b) makes
#: a movement on or before the books unstorable, so the record has to sit
#: BETWEEN the books and the first assertion -- which is the production shape
#: after that step's migration, and is what leaves the assertion's correction
#: non-zero.  Every other shape in this package inherits the seeded account's own
#: origination assertion, whose books
#: already precede its records.
_BOOKS_OPEN_ON = date(2025, 12, 31)


# An as-of before every fixture date in this package, so ruling R-G's clamp is a no-op
# except in the tests that exist for it.  The tiers are graded separately on
# purpose: a test that mixed them could pass with either one wrong.
_EARLY_AS_OF = date(2026, 1, 20)


def _view(account, scenario, periods, as_of=_EARLY_AS_OF):
    """Return the view's per-period COLUMNS, keyed by period id.

    The producer returns a :class:`CashPeriodView` -- the columns plus the live
    override map the projection was valued through (ruling R-Q, wired at plan
    step X-c2b2, so the grid's cells and its balance row cannot be priced off
    two different maps).  These tests grade the columns, so the helper unwraps
    them; ``TestTheViewCarriesTheBasisItWasValuedOn`` grades the map.
    """
    return period_view_of(
        assembled_fold(account, read_pass(account, scenario, as_of)),
        period_window(periods),
    ).columns


def _identity_holds(account, scenario, periods, as_of=_EARLY_AS_OF):
    """Return ``[(period, balance_delta, explained)]`` per period.

    ``explained`` is ``net + period_timing + book_vs_bank`` -- R-DH (f) split
    the single remainder in two, and the identity is stated over BOTH halves
    rather than over a combined accessor.  No such accessor survives on the
    dataclass: leaving one would invite a surface to render the sum again,
    which is the figure the ruling exists to delete, so the sum is composed
    here at the one place that genuinely needs it.

    The balance delta is sampled from the INDEPENDENT fold at the boundary
    dates; the right-hand side is the view's own grouping.  The caller asserts
    the pairs are equal -- and reads the same pairs to check the run was not
    vacuous.

    **The boundaries come from the WINDOW, not from the ORM rows** (plan step
    C2-c).  They used to be ``period.start_date`` / ``period.end_date`` off the
    stored columns, which sampled the fold on a day the reported column may not
    actually close on -- so a schedule whose stored ends had drifted made this
    oracle disagree with the producer for a reason that was the ORACLE's.  Both
    sides now read the one calendar, which is what the identity is a property
    OF.  The tuple's first element is a
    :class:`~app.services.pay_calendar.DerivedPeriod` for the same reason.
    """
    window = period_window(periods)
    figures = _view(account, scenario, periods, as_of=as_of)
    boundaries = [period.start_date - _ONE_DAY for period in window]
    boundaries += [period.end_date for period in window]
    folded = balances_at(
        assembled_fold(account, read_pass(account, scenario, as_of)), boundaries,
    )
    return [
        (
            period,
            folded[period.end_date] - folded[period.start_date - _ONE_DAY],
            figures[period.period_id].net
            + figures[period.period_id].period_timing
            + figures[period.period_id].book_vs_bank,
        )
        for period in window
    ]
