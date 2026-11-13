"""A paycheck's banner: the changes to its pay it is the first to show, as values.

Task plan#3168 (findings plan#1585, SAL-581, and plan#3118; ruling
**R-SAL85**).  :meth:`~app.services.payroll_basis.PayrollBasis.banner_on`
returns a :class:`PayBanner`, and every surface reads it: the paycheck
anatomy's banners, the projection ledger's row and badges, the "Next raise"
chip on the cockpit and the projection, and the net-pay chart's raise marks.

**Each change carries its own direction, and no reader reads it off the
words.**  The banner was a display string from the walk to the template until
this task, ``"PAY -$70.00"`` or ``"MERIT +2.5000%"``, and two parsers read it
back: ``is_pay_cut`` chose the amber cut style from its first five
characters, and ``clean_raise_label`` re-cased and trimmed it by splitting on
``" +"`` and ``" -"``.  A change to the label's wording would have turned a
cut's amber banner green, and a label of a shape the cleaner did not list
passed through uncleaned.  Each change here states its direction
(:attr:`RecordedChange.is_cut`, :attr:`ForecastRaise.is_cut`) and its words
(``label``) from its own figures, once.

**A banner names EVERY change its paycheck is the first to show** (finding
plan#3118): a recorded pay entry's change, when it changes the pay, and each
forecast raise the paycheck is the first to pay a day of.  The banner
answered with the recorded change alone until this task, so a raise landing
inside the pay period of a paycheck where an entry is recorded was never
announced.  The recorded change comes first, because the entry opens on the
paycheck's first day and a raise it does not hold lands after.

Pure: values and string composition, no Flask, no database, no ``float``.
"""
from dataclasses import dataclass
from decimal import Decimal

from app.services.salary_raises import RaiseTerms
from app.utils.money import HUNDRED, ZERO

#: What separates two changes named in one banner, as each surface shows it.
_SEPARATOR = ", "


def _percent_text(fraction: Decimal) -> str:
    """Return *fraction* as a percentage's digits, trailing zeros trimmed.

    The column holds four places (``Numeric(5, 4)``), so ``0.0250`` is
    ``2.5000`` percent and shows as ``2.5``; ``0.0300`` shows as ``3``.  Only
    zeros AFTER the point are trimmed: ``0.1000`` is ``10``, never ``1``.

    Args:
        fraction: The raise as a fraction, ``Decimal("0.0250")`` for 2.5%.

    Returns:
        The percentage's digits, without a ``%``.
    """
    digits = f"{Decimal(str(fraction)) * HUNDRED:f}"
    if "." in digits:
        digits = digits.rstrip("0").rstrip(".")
    return digits


@dataclass(frozen=True)
class RecordedChange:
    """What a recorded pay entry changes: its pay against the pay before it.

    Rulings **R-SAL84** ("Banner the pay change"), **R-SAL89** ("Yearly pay
    across a seam") and **plan#3091** (pay with pay): the entry's pay against
    the pay in force the day before its paycheck opens, per paycheck, or a
    year where the two are paid at different counts.  An entry changing
    nothing is no change, and its paycheck badges none
    (:meth:`~app.services.payroll_basis.PayrollBasis.banner_on`).

    Attributes:
        change: The entry's pay less the pay before it, cent-exact and
            signed; never zero.  A yearly figure when :attr:`yearly`.
        yearly: Whether the change is measured in yearly pay, across a
            change of how many paychecks a year are paid.
    """

    change: Decimal
    yearly: bool

    @property
    def is_cut(self) -> bool:
        """Whether the entry lowers the pay: amber, a down arrow and "Pay cut" (R-SAL85)."""
        return self.change < ZERO

    @property
    def label(self) -> str:
        """Return the change's words: ``"Pay +$70.00"``, ``"Pay -$70.00 a year"``."""
        sign = "-" if self.is_cut else "+"
        unit = " a year" if self.yearly else ""
        return f"Pay {sign}${abs(self.change):,.2f}{unit}"


@dataclass(frozen=True)
class ForecastRaise:
    """A forecast raise its paycheck is the first to pay a day of (ruling **plan#3092**).

    Attributes:
        terms: The raise, as the walk applied it.
    """

    terms: RaiseTerms

    @property
    def is_cut(self) -> bool:
        """Never: a raise's percentage or yearly dollars are above zero.

        ``ck_salary_raises_positive_pct`` and ``ck_salary_raises_positive_flat``
        hold every stored raise to it, and a what-if's terms are a stored
        raise's with only its end year changed (ruling **R-SAL20**).
        """
        return False

    @property
    def label(self) -> str:
        """Return the raise's words: ``"Merit +2.5%"`` or ``"Cola +$2,000.00"``.

        The type's name title-cased, then the percentage trimmed
        (:func:`_percent_text`), or the yearly dollars to the cent.  A raise
        is exactly one of the two (``ck_salary_raises_one_method``).
        """
        name = self.terms.raise_type_name.title()
        if self.terms.percentage is not None:
            return f"{name} +{_percent_text(self.terms.percentage)}%"
        return f"{name} +${self.terms.flat_amount:,.2f}"


#: One change a banner names.
PayEvent = RecordedChange | ForecastRaise


@dataclass(frozen=True)
class PayBanner:
    """Every change to its pay a paycheck is the first to show, in the order they apply.

    The empty banner, ``PayBanner()``, is a paycheck that shows none.

    Attributes:
        events: The changes: the recorded one first, when there is one, then
            each forecast raise in the order the walk applied them.
    """

    events: "tuple[PayEvent, ...]" = ()

    @property
    def cuts(self) -> "PayBanner":
        """Return the banner of the changes that lower the pay: the amber banner and badge."""
        return PayBanner(tuple(event for event in self.events if event.is_cut))

    @property
    def increases(self) -> "PayBanner":
        """Return the banner of the changes that raise the pay: the green banner and badge."""
        return PayBanner(tuple(event for event in self.events if not event.is_cut))

    @property
    def text(self) -> str:
        """Return every change's words, comma-joined in order, or ``""`` for none."""
        return _SEPARATOR.join(event.label for event in self.events)


__all__ = ["ForecastRaise", "PayBanner", "PayEvent", "RecordedChange"]
