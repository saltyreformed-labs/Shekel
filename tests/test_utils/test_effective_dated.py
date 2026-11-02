"""Unit tests for the effective-dated leaf's hold-flat span.

Pure-function tests (no database) for
:func:`app.utils.effective_dated.span_in_effect_or_earliest` (plan#2331's
review, plan#2576): the row :func:`~app.utils.effective_dated.in_effect_or_earliest`
names for a day, and the span ``[start, end)`` of days that walk gives it.
What is pinned: an empty series is ``None``; the earliest row's span opens at
the beginning of time (``None``) because the series holds flat backwards, on a
day before it and on its own days alike; a later row's span opens at its own
date; every span but the latest's ends at the next row's date; and the answer
does not depend on the input order.
"""

from datetime import date
from types import SimpleNamespace

from app.utils.effective_dated import in_effect_or_earliest, span_in_effect_or_earliest

JAN = SimpleNamespace(effective_date=date(2026, 1, 1))
MAR = SimpleNamespace(effective_date=date(2026, 3, 1))
JUN = SimpleNamespace(effective_date=date(2026, 6, 1))


class TestTheSpanOfTheRowInEffect:
    """Which row governs a day, and from when until when it governs."""

    def test_an_empty_series_has_no_row(self):
        """No row, no span: ``None``, as the walk itself answers."""
        assert span_in_effect_or_earliest([], date(2026, 2, 1)) is None

    def test_the_only_row_governs_every_day(self):
        """One row: unbounded both ways, before its date as on and after it."""
        for day in (date(2025, 1, 1), date(2026, 1, 1), date(2030, 1, 1)):
            assert span_in_effect_or_earliest([JAN], day) == (JAN, None, None)

    def test_the_earliest_row_spans_from_the_beginning_to_the_next_date(self):
        """Before January's date and on its days alike: open start, end at Mar 1."""
        series = [JAN, MAR, JUN]
        for day in (date(2025, 12, 31), date(2026, 1, 1), date(2026, 2, 28)):
            assert span_in_effect_or_earliest(series, day) == (
                JAN, None, date(2026, 3, 1),
            )

    def test_a_middle_row_spans_its_own_date_to_the_next(self):
        """March's days, Mar 1 through May 31, read ``[Mar 1, Jun 1)``."""
        series = [JAN, MAR, JUN]
        for day in (date(2026, 3, 1), date(2026, 5, 31)):
            assert span_in_effect_or_earliest(series, day) == (
                MAR, date(2026, 3, 1), date(2026, 6, 1),
            )

    def test_the_latest_row_spans_its_own_date_onward(self):
        """June's days read ``[Jun 1, forever)``."""
        series = [JAN, MAR, JUN]
        for day in (date(2026, 6, 1), date(2040, 1, 1)):
            assert span_in_effect_or_earliest(series, day) == (
                JUN, date(2026, 6, 1), None,
            )

    def test_the_input_order_does_not_matter(self):
        """Shuffled rows give the same span, and the row the walk names."""
        day = date(2026, 4, 15)
        span = span_in_effect_or_earliest([JUN, JAN, MAR], day)
        assert span == (MAR, date(2026, 3, 1), date(2026, 6, 1))
        assert span[0] is in_effect_or_earliest([JAN, MAR, JUN], day)
