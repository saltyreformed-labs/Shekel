"""
Shekel Budget App -- a card's statement due dates, walked in date order

Plan step **credit_card:CC-6**, its task plan#2673 (ruling plan#2671: a card
payment's dates are its repeat, and a new repeat is each statement's due
date).  :class:`~app.services.card_statement.DueDateSchedule` is what that
repeat walks; these cases pin what it yields:

* every statement's due date, each under the version in force at its close
  (:func:`~app.services.card_statement.due_date_for`);
* in DATE order where close order differs (a short transitional cycle falling
  due before the long one ahead of it), and one date where two statements
  fall due on one day;
* the same dates as a brute-force reading of
  :func:`~app.services.card_statement.statement_sequence`, over every close
  day and due day and across a change of both, which is what grades the
  walk's start (``_LONGEST_DUE_AFTER_CLOSE``) and its holding of dates;
* stopping at the calendar's last day, and refusing an empty schedule.

Pure: the versions are plain namespaces, as the module's own tests build them.
"""

from datetime import date, timedelta
from itertools import islice
from types import SimpleNamespace

import pytest

from app.services.card_statement import (
    DueDateSchedule,
    DueDateTerms,
    due_date_for,
    statement_sequence,
)
from app.utils.dates import CALENDAR_DATE_MAX


def _version(effective_date, close_day, due_day):
    """One terms version carrying the columns the due dates read."""
    return SimpleNamespace(
        effective_date=effective_date,
        statement_close_day=close_day,
        payment_due_day=due_day,
    )


def _brute_force(versions, day, last):
    """Every distinct due date in ``[day, last]``, from the statement sequence.

    Reads every statement closing from a year before *day*, which is far
    wider than any statement's due date can trail its close, so this cannot
    miss one; it is the answer the lazy walk must reproduce.
    """
    windows = statement_sequence(
        versions, day - timedelta(days=366), last,
    )
    return sorted({
        due for due in (due_date_for(versions, w.closes) for w in windows)
        if day <= due <= last
    })


class TestTheDueDatesInDateOrder:
    """What one card's schedule yields."""

    def test_each_statement_falls_due_on_the_due_day_after_its_close(self):
        """Close 20, due 15: each statement closing the 20th is due next month's 15th.

        Statements close Nov 20, Dec 20, Jan 20, so from Dec 1 the due dates
        are Dec 15, Jan 15 and Feb 15 (R-CC26: the first due day strictly
        after the close).
        """
        schedule = DueDateSchedule.of([_version(date(2026, 1, 1), 20, 15)])

        assert list(islice(schedule.on_or_after(date(2026, 12, 1)), 3)) == [
            date(2026, 12, 15), date(2027, 1, 15), date(2027, 2, 15),
        ]

    def test_a_short_transitional_cycle_falling_due_first_is_yielded_first(self):
        """Close 20 due 19, then close 5 due 6 from Mar 1: Mar 6 before Mar 19.

        The Feb 20 statement (old terms) is due Mar 19; the Mar 5 statement
        (new terms, a 13-day transitional cycle) is due Mar 6.  In close
        order that is Mar 19 then Mar 6; the walk is ascending, as every
        recurrence walk must be (``_occurrence._bounded`` stops at the first
        refused date), so Mar 6 comes first.
        """
        schedule = DueDateSchedule.of([
            _version(date(2026, 1, 1), 20, 19),
            _version(date(2027, 3, 1), 5, 6),
        ])

        assert list(islice(schedule.on_or_after(date(2027, 2, 1)), 4)) == [
            date(2027, 2, 19), date(2027, 3, 6), date(2027, 3, 19),
            date(2027, 4, 6),
        ]

    def test_two_statements_due_on_one_day_are_one_date(self):
        """Close 20 due 19, then close 5 due 19 from Mar 1: Mar 19 once.

        The Feb 20 statement is due Mar 19 under the old terms, and the Mar 5
        statement is due Mar 19 under the new ones.  One payment answers
        both (the later statement's balance holds the earlier one's), and two
        occurrences on one date would be two rows the generation index keys
        apart by nothing.
        """
        schedule = DueDateSchedule.of([
            _version(date(2026, 1, 1), 20, 19),
            _version(date(2027, 3, 1), 5, 19),
        ])

        assert list(islice(schedule.on_or_after(date(2027, 3, 1)), 3)) == [
            date(2027, 3, 19), date(2027, 4, 19), date(2027, 5, 19),
        ]

    def test_a_corrected_due_day_moves_every_due_date_from_the_start(self):
        """Due day 25 corrected to 15: from Dec 4 the first due date is Dec 15.

        The case plan#2673's ``starts_on`` reading was measured on: the
        schedule is read again from the corrected terms, so the statement
        closing Nov 20 falls due Dec 15 and a walk from Dec 4 keeps it.
        """
        typed = DueDateSchedule.of([_version(date(2026, 1, 1), 20, 25)])
        corrected = DueDateSchedule.of([_version(date(2026, 1, 1), 20, 15)])

        assert next(typed.on_or_after(date(2026, 12, 4))) == date(2026, 12, 25)
        assert next(corrected.on_or_after(date(2026, 12, 4))) == date(
            2026, 12, 15,
        )

    def test_a_due_date_on_the_day_asked_from_is_yielded(self):
        """On or after: a walk from Dec 15 yields Dec 15 itself."""
        schedule = DueDateSchedule.of([_version(date(2026, 1, 1), 20, 15)])

        assert next(schedule.on_or_after(date(2026, 12, 15))) == date(
            2026, 12, 15,
        )
        assert next(schedule.on_or_after(date(2026, 12, 16))) == date(
            2027, 1, 15,
        )


class TestTheWalkIsTheStatementSequence:
    """The lazy walk against the brute-force reading of the same terms."""

    @pytest.mark.parametrize("close_day", range(1, 32))
    def test_every_close_day_and_due_day_agrees(self, close_day):
        """Every (close, due) pair, from several start days, over two years.

        A start day mid-month and on a month's last day, so a statement whose
        due date trails its close by the most (a close on the 31st, due on
        the 31st of the next month) is reached from both sides of the start.
        """
        for due_day in range(1, 32):
            versions = [_version(date(2025, 1, 1), close_day, due_day)]
            schedule = DueDateSchedule.of(versions)
            for day in (date(2026, 1, 31), date(2026, 2, 14), date(2026, 3, 1)):
                last = day + timedelta(days=730)
                walked = list(_take_through(schedule.on_or_after(day), last))

                assert walked == _brute_force(versions, day, last), (
                    close_day, due_day, day,
                )

    @pytest.mark.parametrize(
        ("before", "after"),
        [((20, 19), (5, 6)), ((31, 30), (1, 2)), ((5, 28), (28, 5)),
         ((15, 14), (16, 15)), ((28, 1), (2, 31))],
    )
    def test_a_change_of_close_and_due_day_agrees(self, before, after):
        """Across a change of both days, from start days around the change.

        Each pair moves the close day far enough to cut a short or a long
        transitional cycle, which is where close order and date order part.
        """
        versions = [
            _version(date(2025, 1, 1), *before),
            _version(date(2026, 3, 1), *after),
        ]
        schedule = DueDateSchedule.of(versions)
        for day in (date(2026, 1, 15), date(2026, 2, 28), date(2026, 3, 3)):
            last = date(2026, 12, 31)

            assert list(_take_through(schedule.on_or_after(day), last)) == (
                _brute_force(versions, day, last)
            ), (before, after, day)

    def test_the_walk_stops_at_the_calendars_last_day(self):
        """Nothing past ``CALENDAR_DATE_MAX``, and the last date before it is kept."""
        schedule = DueDateSchedule.of([_version(date(2026, 1, 1), 20, 15)])

        tail = list(schedule.on_or_after(date(2100, 10, 1)))

        assert tail == [date(2100, 10, 15), date(2100, 11, 15),
                        date(2100, 12, 15)]
        assert all(due <= CALENDAR_DATE_MAX for due in tail)


class TestTheScheduleIsAValue:
    """What lets the recurrence walk key a memo on it."""

    def test_two_reads_of_one_terms_series_are_equal_in_any_order(self):
        """Equal and hashed alike, whatever order the versions arrive in."""
        first = _version(date(2026, 1, 1), 20, 15)
        second = _version(date(2026, 6, 1), 5, 28)

        one = DueDateSchedule.of([first, second])
        other = DueDateSchedule.of([second, first])

        assert one == other
        assert hash(one) == hash(other)
        assert one.terms == (
            DueDateTerms(date(2026, 1, 1), 20, 15),
            DueDateTerms(date(2026, 6, 1), 5, 28),
        )

    def test_a_change_of_terms_is_a_different_value(self):
        """A corrected due day reads as a different schedule."""
        assert DueDateSchedule.of([_version(date(2026, 1, 1), 20, 15)]) != (
            DueDateSchedule.of([_version(date(2026, 1, 1), 20, 25)])
        )

    def test_an_empty_schedule_is_refused(self):
        """A dormant card has no statements, so it has no schedule."""
        with pytest.raises(ValueError, match="dormant"):
            DueDateSchedule.of([])


def _take_through(dates, last):
    """The dates of an ascending iterator up to and including *last*."""
    for due in dates:
        if due > last:
            return
        yield due
