"""A paycheck's banner as a value: each change's words and direction (task plan#3168).

:mod:`app.services.pay_banner` states every change a banner names, its
direction (``is_cut``) and its words (``label``), from its own figures.  These
cases carry the claims ``clean_raise_label``'s tests made of the display
string before this task (title-cased type, trimmed percentage, money to the
cent, comma-joined), now made of the value itself, and add the one the string
could not hold: a change's direction is a fact of its figures, not of its
first characters (finding plan#1585).  Every figure is made up.
"""

from datetime import date
from decimal import Decimal

import pytest

from app.services.pay_banner import ForecastRaise, PayBanner, RecordedChange
from app.services.salary_raises import RaiseTerms


def _raise(type_name, *, percentage=None, flat_amount=None):
    """A one-time raise of *type_name*, a percentage or yearly dollars (strings)."""
    return ForecastRaise(RaiseTerms(
        effective_date=date(2027, 3, 3), is_recurring=False,
        percentage=None if percentage is None else Decimal(percentage),
        flat_amount=None if flat_amount is None else Decimal(flat_amount),
        terminal_year=None, raise_type_name=type_name,
    ))


class TestARaisesWords:
    """A forecast raise's label: its type title-cased, then its percentage or dollars."""

    @pytest.mark.parametrize(("fraction", "label"), [
        # 0.0250 x 100 = 2.5000: trailing zeros trimmed.
        ("0.0250", "Merit +2.5%"),
        # 0.0300 x 100 = 3.0000: the point goes with the zeros.
        ("0.0300", "Merit +3%"),
        # 0.03 x 100 = 3.00: the trim is the same at two places.
        ("0.03", "Merit +3%"),
        # 0.1000 x 100 = 10.0000: only zeros after the point go, never 10's.
        ("0.1000", "Merit +10%"),
        # 0.0125 x 100 = 1.2500.
        ("0.0125", "Merit +1.25%"),
    ])
    def test_a_percentage_raise_trims_its_trailing_zeros(self, fraction, label):
        """The stored four places show as few as the figure needs."""
        assert _raise("merit", percentage=fraction).label == label

    def test_a_flat_raise_keeps_its_dollars_to_the_cent(self):
        """$2,000.00 a year: "Cola +$2,000.00", the thousands comma kept."""
        assert _raise("cola", flat_amount="2000.00").label == "Cola +$2,000.00"

    def test_a_raise_is_never_a_cut(self):
        """A raise's amount is above zero by CHECK, so its direction is up."""
        assert not _raise("merit", percentage="0.0250").is_cut
        assert not _raise("custom", flat_amount="26000.00").is_cut


class TestARecordedChangesWords:
    """A recorded change's label and direction come from its signed figure."""

    def test_an_increase(self):
        """+70.00 a paycheck: "Pay +$70.00", not a cut."""
        change = RecordedChange(Decimal("70.00"), yearly=False)
        assert change.label == "Pay +$70.00"
        assert not change.is_cut

    def test_a_cut(self):
        """-70.00 a paycheck: "Pay -$70.00", a cut."""
        change = RecordedChange(Decimal("-70.00"), yearly=False)
        assert change.label == "Pay -$70.00"
        assert change.is_cut

    def test_a_yearly_change_says_a_year(self):
        """-1,030.00 a year across a change of rhythm: "Pay -$1,030.00 a year", a cut."""
        change = RecordedChange(Decimal("-1030.00"), yearly=True)
        assert change.label == "Pay -$1,030.00 a year"
        assert change.is_cut


class TestABannersGroups:
    """A banner splits by direction, keeping order, and names every change in order."""

    def test_a_cut_and_a_raise_split_by_direction(self):
        """'Pay -$100.00' then 'Merit +5%': the cut alone amber, the raise alone green."""
        cut = RecordedChange(Decimal("-100.00"), yearly=False)
        merit = _raise("merit", percentage="0.0500")
        banner = PayBanner((cut, merit))
        assert banner.text == "Pay -$100.00, Merit +5%"
        assert banner.cuts == PayBanner((cut,))
        assert banner.increases == PayBanner((merit,))
        assert banner.cuts.text == "Pay -$100.00"
        assert banner.increases.text == "Merit +5%"

    def test_two_raises_share_one_group_in_order(self):
        """A merit then a cola reaching one paycheck: "Merit +5%, Cola +$2,600.00"."""
        merit = _raise("merit", percentage="0.0500")
        cola = _raise("cola", flat_amount="2600.00")
        banner = PayBanner((merit, cola))
        assert banner.increases == banner
        assert banner.cuts == PayBanner()
        assert banner.text == "Merit +5%, Cola +$2,600.00"

    def test_the_empty_banner_names_nothing(self):
        """``PayBanner()``: no change, no words, no groups."""
        assert PayBanner().text == ""
        assert PayBanner().cuts == PayBanner()
        assert PayBanner().increases == PayBanner()
