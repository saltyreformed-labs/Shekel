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
* stopping at the calendar's last day, and refusing an empty schedule;
* which statement a payment dated on a day pays
  (:func:`~app.services.card_statement.close_of_statement_paid_on`, plan#2879),
  each case by hand and then against a brute-force reading of the same terms.

Pure: the versions are plain namespaces, as the module's own tests build them.
"""

from datetime import date, timedelta
from itertools import islice
from types import SimpleNamespace

import pytest

from app.services.card_statement import (
    DueDateSchedule,
    DueDateTerms,
    close_of_statement_paid_on,
    cycle_containing,
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


class TestTheStatementAPaymentPays:
    """The statement a payment dated on a day pays, each case by hand."""

    def test_a_due_date_and_the_next_close_day_pay_one_statement(self):
        """Close 5 due 27: Jan 27 pays Jan 5, and Feb 5 itself still pays Jan 5."""
        versions = [_version(date(2026, 1, 1), 5, 27)]

        assert close_of_statement_paid_on(versions, date(2026, 1, 27)) == date(
            2026, 1, 5,
        )
        assert close_of_statement_paid_on(versions, date(2026, 2, 5)) == date(
            2026, 1, 5,
        )

    def test_a_due_date_in_the_next_month(self):
        """Close 20 due 15: Feb 15 pays Jan 20, not Dec 20 (due Jan 15, past)."""
        versions = [_version(date(2025, 11, 1), 20, 15)]

        assert close_of_statement_paid_on(versions, date(2026, 2, 15)) == date(
            2026, 1, 20,
        )

    def test_a_due_day_equal_to_the_close_day_pays_the_statement_before(self):
        """Close 5 due 5: Feb 5 is a close and a due date, and pays Jan 5."""
        versions = [_version(date(2025, 11, 1), 5, 5)]

        assert close_of_statement_paid_on(versions, date(2026, 2, 5)) == date(
            2026, 1, 5,
        )

    def test_a_transitional_cycle_leaves_the_earlier_statement_due(self):
        """Close 5 due 27, then close 20 due 15 from Jan 10.

        Jan 5 falls due Jan 27 and Jan 20 falls due Feb 15, so Jan 27 pays Jan 5
        though Jan 20 has closed since, and Feb 15 pays Jan 20.
        """
        versions = [
            _version(date(2025, 11, 1), 5, 27),
            _version(date(2026, 1, 10), 20, 15),
        ]

        assert close_of_statement_paid_on(versions, date(2026, 1, 27)) == date(
            2026, 1, 5,
        )
        assert close_of_statement_paid_on(versions, date(2026, 2, 15)) == date(
            2026, 1, 20,
        )

    def test_a_short_cycle_falling_due_first_holds_the_earlier_statement(self):
        """Close 20 due 19, then close 5 due 6 from Mar 1: due dates out of close order.

        Feb 20 falls due Mar 19 and Mar 5 falls due Mar 6.  Mar 6 pays Mar 5, the
        statement due that day, though the Feb 20 statement before it is not yet
        past due.  Mar 7 and Mar 19 pay Mar 5 too: it closed later and has fallen
        due, so its balance holds all of Feb 20's.
        """
        versions = [
            _version(date(2025, 11, 1), 20, 19),
            _version(date(2026, 3, 1), 5, 6),
        ]

        for day in (date(2026, 3, 6), date(2026, 3, 7), date(2026, 3, 19)):
            assert close_of_statement_paid_on(versions, day) == date(2026, 3, 5), day

    def test_before_the_short_cycle_closes_the_earlier_statement_stands(self):
        """The same terms on Mar 5: the short cycle has not closed, so Feb 20, due next."""
        versions = [
            _version(date(2025, 11, 1), 20, 19),
            _version(date(2026, 3, 1), 5, 6),
        ]

        assert close_of_statement_paid_on(versions, date(2026, 3, 5)) == date(
            2026, 2, 20,
        )

    def test_an_earlier_statement_still_falling_due_after_a_later_one_fell_due(self):
        """Close 5 due 27, then close 20 due 21 from Jan 10: Jan 20 falls due Jan 21.

        Jan 5 is not due until Jan 27.  A payment moved to Jan 23 falls between, and
        pays Jan 20, overdue and holding all of Jan 5's.
        """
        versions = [
            _version(date(2025, 11, 1), 5, 27),
            _version(date(2026, 1, 10), 20, 21),
        ]

        assert close_of_statement_paid_on(versions, date(2026, 1, 23)) == date(
            2026, 1, 20,
        )

    def test_two_statements_due_on_one_day_pay_the_later(self):
        """Close 28 due 29: Feb 28 and Mar 28 both fall due Mar 29, which pays Mar 28.

        February has no 29th in 2026, so the Feb 28 statement's first due day
        strictly after its close is Mar 29, the Mar 28 statement's too.  The later
        balance holds the earlier one's unpaid balance, so one payment answers both.
        """
        versions = [_version(date(2025, 11, 1), 28, 29)]

        assert due_date_for(versions, date(2026, 2, 28)) == date(2026, 3, 29)
        assert due_date_for(versions, date(2026, 3, 28)) == date(2026, 3, 29)
        assert close_of_statement_paid_on(versions, date(2026, 3, 29)) == date(
            2026, 3, 28,
        )

    def test_a_day_no_statement_falls_due_pays_the_next_to_fall_due(self):
        """Close 5 due 27, then close 20 due 15 from Jan 10: a payment moved to Jan 26.

        Jan 5 falls due the next day and Jan 20 not until Feb 15, so it pays Jan 5.
        """
        versions = [
            _version(date(2025, 11, 1), 5, 27),
            _version(date(2026, 1, 10), 20, 15),
        ]

        assert close_of_statement_paid_on(versions, date(2026, 1, 26)) == date(
            2026, 1, 5,
        )

    def test_with_every_statement_past_due_it_pays_the_last_to_close(self):
        """Close 5 due 27: Feb 1 is past Jan 27, and nothing closed since Jan 5."""
        versions = [_version(date(2025, 11, 1), 5, 27)]

        assert close_of_statement_paid_on(versions, date(2026, 2, 1)) == date(
            2026, 1, 5,
        )

    def test_a_long_cycle_reaches_back_past_the_longest_due_date(self):
        """Close 5 due 27, then close 20 due 15 from Jan 21: nothing closes until Feb 20.

        Feb 19 is 45 days after the Jan 5 close, which fell due Jan 27.  No
        statement is ahead, so it pays the last to close, Jan 5.
        """
        versions = [
            _version(date(2025, 11, 1), 5, 27),
            _version(date(2026, 1, 21), 20, 15),
        ]

        assert close_of_statement_paid_on(versions, date(2026, 2, 19)) == date(
            2026, 1, 5,
        )

    def test_a_statement_falling_due_the_longest_time_after_its_close(self):
        """Close 31 due 31, then close 20 due 5 from Jan 10: Dec 31 is due 31 days on.

        Dec 31 falls due Jan 31 and Jan 20 falls due Feb 5, so Jan 31 pays Dec 31,
        at the very edge of how far back a statement can still be due.
        """
        versions = [
            _version(date(2025, 11, 1), 31, 31),
            _version(date(2026, 1, 10), 20, 5),
        ]

        assert due_date_for(versions, date(2025, 12, 31)) == date(2026, 1, 31)
        assert close_of_statement_paid_on(versions, date(2026, 1, 31)) == date(
            2025, 12, 31,
        )

    def test_an_empty_series_is_refused(self):
        """A dormant card has no statement to pay."""
        with pytest.raises(ValueError, match="no terms version"):
            close_of_statement_paid_on([], date(2026, 1, 27))


def _paid_by_brute_force(versions, day):
    """Which statement *day* pays, reading every close in the 100 days before it.

    Wider than any cycle a change of terms can cut (two months) and than any
    statement's due date can trail its close, so it cannot miss one.  It states the
    same rule over every close, so it grades the look-back and not the rule, which
    the hand cases above and the money in ``test_card_payment_dates`` grade.
    """
    closes = [
        window.closes
        for window in statement_sequence(
            versions, day - timedelta(days=100), day - timedelta(days=1),
        )
    ]
    ahead = [
        due_date_for(versions, c) for c in closes if due_date_for(versions, c) >= day
    ]
    if not ahead:
        return closes[-1]
    return max(c for c in closes if due_date_for(versions, c) <= min(ahead))


class TestTheStatementAPaymentPaysByBruteForce:
    """Every day against the brute-force reading of the same terms."""

    @pytest.mark.parametrize("close_day", range(1, 32))
    def test_every_close_day_and_due_day_agrees(self, close_day):
        """Every (close, due) pair, each day of a stretch holding a short February."""
        for due_day in range(1, 32):
            versions = [_version(date(2025, 1, 1), close_day, due_day)]
            for offset in range(75):
                day = date(2026, 1, 25) + timedelta(days=offset)

                assert close_of_statement_paid_on(
                    versions, day,
                ) == _paid_by_brute_force(versions, day), (
                    close_day, due_day, day,
                )

    @pytest.mark.parametrize("close_day", range(1, 32))
    def test_with_one_terms_version_it_is_always_the_last_close(self, close_day):
        """Terms that never change never move the answer off the last close before it."""
        for due_day in range(1, 32):
            versions = [_version(date(2025, 1, 1), close_day, due_day)]
            for offset in range(75):
                day = date(2026, 1, 25) + timedelta(days=offset)

                assert close_of_statement_paid_on(versions, day) == cycle_containing(
                    versions, day - timedelta(days=1),
                ).opens, (close_day, due_day, day)

    @pytest.mark.parametrize(
        ("before", "after"),
        [((20, 19), (5, 6)), ((31, 30), (1, 2)), ((5, 28), (28, 5)),
         ((15, 14), (16, 15)), ((28, 1), (2, 31)), ((5, 27), (20, 15)),
         ((31, 31), (20, 5))],
    )
    def test_a_change_of_close_and_due_day_agrees(self, before, after):
        """Across a change of both days, from every day around the change."""
        for changes_on in (date(2026, 1, 10), date(2026, 1, 21), date(2026, 3, 1)):
            versions = [
                _version(date(2025, 1, 1), *before),
                _version(changes_on, *after),
            ]
            for offset in range(150):
                day = date(2025, 12, 15) + timedelta(days=offset)

                assert close_of_statement_paid_on(
                    versions, day,
                ) == _paid_by_brute_force(versions, day), (
                    before, after, changes_on, day,
                )

    @pytest.mark.parametrize(
        ("before", "after"),
        [((20, 19), (5, 6)), ((31, 30), (1, 2)), ((5, 28), (28, 5)),
         ((15, 14), (16, 15)), ((28, 29), (28, 29)), ((5, 27), (20, 15))],
    )
    def test_every_due_date_leaves_no_statement_due_by_it_uncovered(self, before, after):
        """Each date the schedule yields pays the last to close of all due by that date.

        So a statement fallen due holds nothing the row does not ask for, and the row
        asks for no statement still to fall due.
        """
        versions = [
            _version(date(2025, 1, 1), *before),
            _version(date(2026, 3, 1), *after),
        ]
        schedule = DueDateSchedule.of(versions)
        days = _take_through(
            schedule.on_or_after(date(2025, 6, 1)), date(2027, 6, 1),
        )

        for day in days:
            paid = close_of_statement_paid_on(versions, day)
            due_by_then = [
                window.closes
                for window in statement_sequence(
                    versions, day - timedelta(days=100), day - timedelta(days=1),
                )
                if due_date_for(versions, window.closes) <= day
            ]

            assert paid == max(due_by_then), (before, after, day)
