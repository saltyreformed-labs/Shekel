"""Balance-at-T seam -- the MODELLED RETURN: what a modelled account earns per day.

The ACCRUAL kind of :mod:`._asset_fold`'s event stream, stated on its own: the
per-day rule an INTEREST account, an INVESTMENT and an APPRECIATING asset each
accrue by (:class:`InterestAccrual`, :class:`CompoundAccrual`), the window the
return runs over (:class:`AccrualWindow`), and the one question "does this
account model a return, and at what rate?" (:func:`modelled_return`).

**Split out of** :mod:`._asset_fold` **at plan step** ``pay_calendar:C9``
(task plan#3941), when that module stood at the 1,000-line ceiling and the cash
fold's estimated tier had to reach its replay.  The split is by subject: this
module says what a day's return IS, and the fold says how the return is
resolved onto the running balance (``_asset_fold._resolve``) and read.  The
fold imports this and nothing here reaches back.

Boundary discipline (``CLAUDE.md``): no Flask symbol, no writes; all money is
:class:`~decimal.Decimal`.  Seam-PRIVATE.
"""

from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal

from app.models.account import Account
from app.models.investment_params import InvestmentParams
from app.services import growth_engine
from app.services.account_projection import (
    AccountProjectionKind,
    classify_account,
)
from app.services.interest_projection import accrued_interest

_ZERO = Decimal("0")
_ONE_DAY = timedelta(days=1)

# A compound return's per-day rate is DATE-INDEPENDENT: it is a function of the
# span's LENGTH alone, and that length is 1 for every single day of every year --
# it carries no leap-day or month-length switch (unlike the interest day count,
# which carries both).  So it is resolved ONCE per rate, from the day count
# itself.  It used to be resolved off a fabricated one-day
# ``growth_engine.SyntheticPeriod`` at ``date.min``, because the rate function
# took a duck-typed "period"; plan step C2-e gave that function a day count
# (:func:`~app.services.growth_engine.growth_rate_for_days`) and the placeholder
# period had nothing left to stand in for.
_ONE_DAY_SPAN_LENGTH = 1


@dataclass(frozen=True)
class InterestAccrual:
    """One day of an INTEREST account's modelled accrual.

    The rate is NOT date-independent: the daily divisor switches to 366 for a
    window containing Feb 29, the monthly divisor is the calendar month's own
    length, and the quarterly divisor is the quarter's -- so each day resolves
    its own.

    That is also what the daily grain BUYS on the monthly and quarterly
    frequencies, and it is measurable: a 14-day pay period straddling a month
    boundary prices every one of its days against the FIRST month's length,
    because a per-PERIOD reader resolves ``monthrange(period_start)`` once for
    the whole window.  On $10,000 at 3.29% APY over 2026-01-29 .. 2026-02-11 --
    three January days and eleven February ones -- that is **$12.38** against a
    day-by-day **$13.42**, so one period is short by **$1.04** (8.4%).  It is
    the same class as the "13 days of a 14-day period" note the interest path
    already carries, on the two frequencies that note did not reach; the real
    Money Market compounds MONTHLY.

    Attributes:
        apy: The account's annual percentage yield.
        compounding_frequency_id: ``ref.compounding_frequencies.id``.
    """

    apy: Decimal
    compounding_frequency_id: int

    def one_day(self, day: date, balance: Decimal) -> Decimal:
        """Return the UNROUNDED interest *balance* accrues on *day*.

        Args:
            day: The calendar day accruing.
            balance: The balance held on it.

        Returns:
            The full-precision ``Decimal`` accrual; ``0`` for a non-positive
            balance or a non-positive APY (the shared rule's own guard).
        """
        return accrued_interest(
            balance=balance,
            apy=self.apy,
            compounding_frequency_id=self.compounding_frequency_id,
            period_start=day,
            period_end=day + _ONE_DAY,
        )


@dataclass(frozen=True)
class CompoundAccrual:
    """One day of an INVESTMENT's growth or an APPRECIATING asset's appreciation.

    Built by :func:`_compound_accrual`, which resolves the per-day rate once
    through the shared
    :func:`~app.services.growth_engine.growth_rate_for_days`, so this module
    states no growth formula of its own.

    Attributes:
        daily_rate: The compound rate for ONE calendar day,
            ``(1 + annual) ** (1 / 365) - 1``.  Negative for a depreciating
            asset, which the schema permits (``annual_appreciation_rate > -1``).
    """

    daily_rate: Decimal

    def one_day(self, _day: date, balance: Decimal) -> Decimal:
        """Return the UNROUNDED growth *balance* accrues in one day.

        The leading underscore on the day parameter is the point: a compound
        rate is date-INDEPENDENT (see :data:`_ONE_DAY_SPAN_LENGTH`), where an
        interest rate is not.  The parameter is still taken so this and
        :meth:`InterestAccrual.one_day` answer one question through one
        signature, which is what lets :func:`._asset_fold._resolve` carry no branch on the
        account's kind.

        Args:
            balance: The balance held on the day.

        Returns:
            The full-precision ``Decimal`` accrual; ``0`` for a non-positive
            balance, matching the interest rule's own guard, so a modelled
            return can never drive a balance further below zero.
        """
        if balance <= _ZERO:
            return _ZERO
        return balance * self.daily_rate


@dataclass(frozen=True)
class AccrualWindow:
    """When an account's modelled return runs, and at what rate.

    One optional object rather than three separately-optional fields: an
    account either models a return over a window or it does not, and splitting
    that into a nullable rule beside a nullable start day is how a reader ends
    up checking one and not the other.  ``None`` in place of the whole window is
    the "models nothing" state -- an unconfigured HYSA, an ``InvestmentParams``-
    less brokerage, a Property whose rate is unset -- and the fold is then the
    cash fold unchanged.

    Attributes:
        rule: The per-day accrual rule (:class:`InterestAccrual` or
            :class:`CompoundAccrual`).
        start: The first day the return may accrue on -- the LATEST balance
            assertion's ``observed_on``, INCLUSIVE (ruling R-L as sharpened at
            plan step X-c2a: the assertion's own day accrues; ruling R-Y
            extends that from INTEREST to all three modelled kinds).
        end: The last day to resolve -- the caller's furthest requested date.
            Sampling BEYOND it would read a balance that had stopped accruing,
            so every entry derives it from its own request rather than from a
            horizon constant.
    """

    rule: "InterestAccrual | CompoundAccrual"
    start: date
    end: date

    def days(self) -> list[date]:
        """Return every day this window accrues on, ascending.

        Returns:
            The inclusive day list ``[start .. end]``; empty when the window is
            inverted (a read valued entirely before the account's latest
            assertion, which accrues nothing).
        """
        accruing: list[date] = []
        day = self.start
        while day <= self.end:
            accruing.append(day)
            day += _ONE_DAY
        return accruing

    def accrues_on(self, day: date) -> bool:
        """Return whether *day* falls inside this window.

        Args:
            day: The calendar day to test.

        Returns:
            ``True`` when ``start <= day <= end``.
        """
        return self.start <= day <= self.end


def _compound_accrual(annual_rate) -> CompoundAccrual:
    """Return the per-day compound accrual for *annual_rate*.

    Args:
        annual_rate: The configured annual return / appreciation rate.

    Returns:
        The :class:`CompoundAccrual` whose ``daily_rate`` is the shared growth
        formula evaluated over a one-day span.
    """
    return CompoundAccrual(
        daily_rate=growth_engine.growth_rate_for_days(
            Decimal(str(annual_rate)), _ONE_DAY_SPAN_LENGTH,
        ),
    )


def modelled_return(
    account: Account, investment_params: InvestmentParams | None,
) -> "InterestAccrual | CompoundAccrual | None":
    """Return *account*'s modelled per-day return, or ``None`` if it models none.

    "Does this account model a return, and at what rate?" asked ONCE, for all
    three modelled kinds, and answered in ONE shape per kind: resolve that
    kind's params row, and model nothing when it is absent.

    **INTEREST used to answer through a second function** (``_interest``'s
    ``accrual_params``, deleted at plan step X-g4b with the module).  That
    function re-ran :func:`~app.services.account_projection.classify_account`
    inside a branch this one has ALREADY classified -- one predicate stated
    twice, and the branch below is what the second statement existed to serve.
    Folding it here is the same substitution ruling R-AD made one level up when
    it deleted the per-kind ladder: the three arms differ only in which params
    row they read.

    An account whose parameters are absent models NOTHING and is its cash fold:
    an INTEREST-kinded account with no params row is an HYSA the user has not
    configured, an INVESTMENT with no ``InvestmentParams`` is the state
    ``build_account_balance_map`` already falls through on, and a Property with
    no appreciation row is one whose rate is not set.  Inventing a rate for any
    of them would put growth on a screen the account has never earned.

    Args:
        account: The account to test.  Its ``account_type`` drives the
            classifier.  The INTEREST arm's ``getattr`` covers a non-ORM test
            fake carrying no ``interest_params`` attribute at all, which the
            balance paths have always tolerated.
        investment_params: The account's
            :class:`~app.models.investment_params.InvestmentParams`, or ``None``
            -- supplied by the caller's batch-loaded bundle rather than
            re-queried here, exactly as the kernel's dispatcher receives it.

    Returns:
        The per-day accrual rule, or ``None``.
    """
    kind = classify_account(account)
    if kind is AccountProjectionKind.INTEREST:
        interest_params = getattr(account, "interest_params", None) or None
        return None if interest_params is None else InterestAccrual(
            apy=interest_params.apy,
            compounding_frequency_id=interest_params.compounding_frequency_id,
        )
    if kind is AccountProjectionKind.INVESTMENT:
        return None if investment_params is None else _compound_accrual(
            investment_params.assumed_annual_return,
        )
    if kind is AccountProjectionKind.APPRECIATING:
        appreciation_params = account.asset_appreciation_params
        return None if appreciation_params is None else _compound_accrual(
            appreciation_params.annual_appreciation_rate,
        )
    return None


__all__ = [
    "AccrualWindow",
    "CompoundAccrual",
    "InterestAccrual",
    "modelled_return",
]
