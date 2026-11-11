"""What ONE calendar day is worth, and the three ways its amount is read.

Plan step ``bank_import:X-gj-2b-3``.  ``calendar_day_flows`` holds a day's
display ORDER, its (income, expense) FOLD and its "+N more" RESIDUAL, and all
three read the same :attr:`DayEntry.amount`.  Two read it as a FIGURE and one
as a RANKING, and getting that difference wrong is a money defect rather than a
style one.

**Every case here is a pure function over constructed records**, which is the
point of the module existing: these three rules were private inside a
database-heavy service and could only be reached through
``get_month_detail``, so the boundary that matters -- a NEGATIVE amount on an
expense row -- had no cheap case.  ``TestARefundedDayOnTheRealCalendar`` in
``test_calendar_service`` drives the same rules through the producer chain, so
the chain and the arithmetic are graded separately.
"""

from datetime import date
from decimal import Decimal

from app.models.account import Account
from app.services.calendar_day_flows import (
    MAX_VISIBLE_DAY_FLOWS,
    DayEntry,
    day_overflow,
    fold_income_expense,
    order_for_display,
)


def _entry(amount, *, is_income=False, name="A row", other_member=None):
    """One day entry worth *amount*, with every display field held constant.

    On the balance account unless *other_member* names the member it is on.
    """
    return DayEntry(
        item_key=abs(hash(name)) % 100000,
        name=name,
        amount=Decimal(amount),
        is_income=is_income,
        is_paid=True,
        is_large=False,
        is_infrequent=False,
        category_group=None,
        category_item=None,
        due_date=date(2026, 3, 2),
        other_member=other_member,
    )


class TestTheFoldReadsTheAMOUNTSSIGN:
    """``fold_income_expense`` -- the figure the month headline is summed from.

    **It took ``abs()`` on the expense leg and that was a MONEY DEFECT** once
    ruling **bank_import:R-II** let a settled envelope be worth a negative
    figure.  The same defect, on the same producer chain, that plan step
    ``bank_import:X-gj-2b`` fixed at ``_breakdown._totals_by_category`` and
    ``_window._spent_total``; this was the third instance and it reached
    ``MonthSummary.total_expenses``, ``.net``, ``YearOverview.annual_expenses``
    and ``annual_net``.
    """

    def test_an_ordinary_day_is_unchanged(self):
        """The control: dropping ``abs()`` must move no already-correct day."""
        assert fold_income_expense([
            _entry("3000.00", is_income=True, name="Salary"),
            _entry("400.00", name="Groceries"),
        ]) == (Decimal("3000.00"), Decimal("400.00"))

    def test_a_REFUNDED_expense_row_LOWERS_the_expense_leg(self):
        """The case ``abs()`` inverted.

        A settled envelope whose refunds exceeded its purchases is worth a
        negative figure, and a refund REDUCES what the day cost.  Under the
        defect this read ``+86.67``.
        """
        assert fold_income_expense([
            _entry("-86.67", name="Amazon"),
        ]) == (Decimal("0"), Decimal("-86.67"))

    def test_a_MIXED_day_nets_rather_than_grossing_up(self):
        """The shape that hides the defect if it is the only case staged.

        `$100.00` spent and `$86.67` refunded is `$13.33` of cost.  Under the
        defect it read `$186.67` -- a `$173.34` error, and the month ``net``
        moved by the same amount because ``net`` is
        ``total_income - total_expenses``.
        """
        income, expense = fold_income_expense([
            _entry("100.00", name="Groceries"),
            _entry("-86.67", name="Amazon"),
        ])

        assert (income, expense) == (Decimal("0"), Decimal("13.33"))
        # The headline arithmetic, stated here because it is what the owner
        # reads and it is where the $173.34 landed.
        assert income - expense == Decimal("-13.33")

    def test_a_refund_never_lands_on_the_INCOME_leg(self):
        """An expense that came back is not revenue.

        Booking it as income would gross up both sides of the month, which is
        the misfiling ruling **bank_import:R-II** exists to prevent, and it is
        the rule ``balance_at._cash_periods`` states one surface over.
        """
        income, expense = fold_income_expense([_entry("-49.00")])

        assert income == Decimal("0")
        assert expense == Decimal("-49.00")

    def test_both_legs_are_Decimal_on_an_empty_day(self):
        """Money is always Decimal, never an int zero from ``sum``."""
        income, expense = fold_income_expense([])

        assert isinstance(income, Decimal)
        assert isinstance(expense, Decimal)


class TestTheResidualAgreesWithTheFold:
    """``day_overflow`` -- the "+N more" line the cell shows INSTEAD of rows.

    It took ``-abs()`` where the fold took ``abs()``, so both halves of one day
    cell reported a hidden refund as spending.  They are corrected together
    because a disagreement between them is one cell contradicting itself.
    """

    def _five_and_one(self, tail):
        """Five ordinary rows then *tail*, ordered as the cell orders them."""
        entries = [
            _entry("1.00", name=f"Row {i}") for i in range(5)
        ] + [tail]
        order_for_display(entries)
        return entries

    def test_a_hidden_REFUND_RAISES_the_residual(self):
        """The case ``-abs()`` inverted.

        The refund is the largest movement, so ``order_for_display`` puts it
        FIRST among the expenses and it is visible -- this stages it as the
        hidden tail directly, so the residual is what is graded.
        """
        entries = [_entry("1.00", name=f"Row {i}") for i in range(3)]
        entries.append(_entry("-86.67", name="Amazon"))

        residual = day_overflow(entries)

        assert residual.count == 1
        assert residual.net == Decimal("86.67"), (
            "a hidden refund RAISES the day's residual -- under -abs() it "
            "lowered it by the same amount, a $173.34 swing"
        )

    def test_a_hidden_ordinary_expense_still_LOWERS_it(self):
        """The control, so the sign is derived and not flipped wholesale."""
        entries = [_entry("1.00", name=f"Row {i}") for i in range(3)]
        entries.append(_entry("42.00", name="Groceries"))

        assert day_overflow(entries).net == Decimal("-42.00")

    def test_hidden_income_is_added_as_it_always_was(self):
        """The income term is untouched by this correction."""
        entries = [_entry("1.00", name=f"Row {i}") for i in range(3)]
        entries.append(_entry("500.00", is_income=True, name="Bonus"))

        assert day_overflow(entries).net == Decimal("500.00")

    def test_only_the_rows_past_the_cap_are_counted(self):
        """The cap itself, so the two cases above are about the tail.

        **The LITERAL, not the constant** (plan step
        ``bank_import:X-gj-2b-3``, found by adversarial test-quality review).
        It read ``len(entries) - MAX_VISIBLE_DAY_FLOWS`` while ``day_overflow``
        computes ``len(entries[MAX_VISIBLE_DAY_FLOWS:])`` -- both sides of the
        assertion came from one constant, so raising the cap from 3 to 4 left
        this green while the calendar cell drew a fourth row and the "+N more"
        chip beside it under-counted by one.  The module's own header says the
        locked calendar anatomy fixes it at three; three is what grades it.
        """
        entries = self._five_and_one(_entry("9.00", name="Sixth"))

        assert MAX_VISIBLE_DAY_FLOWS == 3, (
            "the locked anatomy fixes the visible row count at three"
        )
        assert day_overflow(entries).count == len(entries) - 3


class TestTheDISPLAYORDERKeepsItsMagnitude:
    """``order_for_display`` -- a RANKING, which is why it still takes ``abs``.

    The two folds above were corrected to read the sign; this one deliberately
    was not.  It answers *how big is this movement*, so a large refund belongs
    near the top of its day -- the same reading
    ``spending_report_service._surprises`` takes with ``-abs(delta)``.  Stated
    as its own case so a later reader does not "fix" it into agreement with the
    folds and quietly bury the day's biggest movement.
    """

    def test_income_comes_first_whatever_the_magnitudes(self):
        """The primary key, unchanged."""
        entries = [_entry("900.00", name="Rent"),
                   _entry("5.00", is_income=True, name="Interest")]

        order_for_display(entries)

        assert [e.name for e in entries] == ["Interest", "Rent"]

    def test_a_large_REFUND_outranks_a_smaller_charge(self):
        """The case that would break if the sort followed the folds.

        Sorted by the SIGNED amount, ``-500.00`` would sort last and the day's
        largest movement would collapse into "+N more".
        """
        entries = [_entry("400.00", name="Bill"),
                   _entry("-500.00", name="Big refund")]

        order_for_display(entries)

        assert [e.name for e in entries] == ["Big refund", "Bill"]


class TestAnEntryOnAnotherMemberIsDrawnAndNotCounted:
    """Ruling plan#3526 (plan task plan#3529): the figures are the balance account's.

    A day draws every member's items, and one on another member -- a purchase
    on the card seen from checking -- adds nothing to either figure: the card's
    payment out of checking is what counts for it.  Before that task the fold
    added every entry, so one purchase was counted on its own day and again on
    the payment's.
    """

    _VISA = Account(name="Visa")

    def test_the_fold_leaves_out_an_expense_and_an_income_on_another_member(self):
        """$300.00 rent on checking, $800.00 groceries and a $50.00 refund-in on the card.

        Income 0.00 and expense 300.00: neither card entry is checking's money.
        """
        assert fold_income_expense([
            _entry("300.00", name="Rent"),
            _entry("800.00", name="Groceries", other_member=self._VISA),
            _entry("50.00", is_income=True, name="Into the Visa",
                   other_member=self._VISA),
        ]) == (Decimal("0"), Decimal("300.00"))

    def test_a_day_of_the_cards_alone_folds_to_zero(self):
        """The Jan 3 cell of plan#2932's fixture: $800.00 on the card and nothing else."""
        assert fold_income_expense([
            _entry("800.00", name="Groceries", other_member=self._VISA),
        ]) == (Decimal("0"), Decimal("0"))

    def test_the_residual_counts_a_hidden_card_line_and_nets_without_it(self):
        """Three $1.00 rows shown, then $42.00 on checking and $800.00 on the card hidden.

        Two lines are hidden, so the count is 2; their net is checking's
        -42.00, the card's $800.00 adding nothing.  Staged as the hidden tail
        directly, as the refund case above is: sorted, the $800.00 line would
        be the day's first and shown.
        """
        entries = [_entry("1.00", name=f"Row {i}") for i in range(3)]
        entries.append(_entry("800.00", name="Groceries", other_member=self._VISA))
        entries.append(_entry("42.00", name="Gas"))

        residual = day_overflow(entries)

        assert residual.count == 2
        assert residual.net == Decimal("-42.00")
