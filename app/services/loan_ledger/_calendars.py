"""A loan's CONTRACT terms, loaded once per read pass for every reader in it.

The ONE producer of a loan's :class:`._charges.LoanCalendar` from its stored
rows (:func:`build_loan_calendar`), and the per-pass memo that hands the SAME
instance to the two readers a pass has (:class:`LoanCalendars`): the walk,
whose charges are built from it (:func:`._walk.load_loan_stream`), and the cash
price of a derive-mode loan payment
(:class:`app.services.cash_ledger.LoanPricing`).

**Pricing kept a second bundle of these terms until plan step
recurrence:R25** (ruling **R-R105**: "one bundle that pricing and charging
both read"; finding **REC-545**).  ``cash_ledger._loan_installment``'s
``_LoanCashBasis`` held the rate periods, the due day and the origination, and
``LoanPricing`` the escrow lines beside it -- the identical four facts this
module's calendar holds, each loaded by its own path, so a pass that walked a
derive-mode loan and priced its payments loaded the loan's terms once for each
(the walk's and the pricer's loads; the loan resolver's bundle loads them a
third time, below).  The bundle is deleted; both readers take this memo's
calendar.

**The memo holds the PARAMS too**, because the walk needs them beyond the
calendar (its opening assertion is synthesized from them,
:func:`app.services.loan_loaders.load_loan_anchor_facts`), and loading them
once per pass for both is what keeps the walk at the one params load it made
before.  The loan resolver's bundle and the payoff calculator still load the
params and resolve the terms on their own paths inside one pass; that is
finding **REC-559**, owned by plan step recurrence:R16-f.

**A memo on the PASS, not a cache**: a write path that changes a loan's terms
and then re-renders builds a fresh pass, so there is no invalidation question
-- the convention :class:`app.services.balance_at.BalanceContext` states for
every memo it holds.  A reader holding no pass builds a fresh
:class:`LoanCalendars` for its one use.
"""

from app.models.loan_params import LoanParams
from app.services import loan_loaders, loan_resolver

from ._charges import LoanCalendar


def build_loan_calendar(params: LoanParams) -> LoanCalendar:
    """Return the loan's :class:`._charges.LoanCalendar`, loaded from its rows.

    The note's origination and due day off *params*, its rate periods
    (:func:`app.services.loan_resolver.resolve_periods` over the loan's rate
    history) and its escrow lines with their full version history
    (:func:`app.services.loan_loaders.load_escrow_lines`).  It reads the
    loan's TERMS and never its payment rows, so pricing a payment cannot read
    the payments it prices (``test_loan_payment_service
    .test_the_contractual_payment.TestALoansPriceDoesNotReadItsOwnPayments``).

    Args:
        params: The loan's :class:`~app.models.loan_params.LoanParams`.

    Returns:
        The loan's contract terms.
    """
    return LoanCalendar(
        origination_date=params.origination_date,
        payment_day=params.payment_day,
        periods=loan_resolver.resolve_periods(
            params, loan_loaders.load_rate_changes(params.account_id),
        ),
        escrow_lines=loan_loaders.load_escrow_lines(params.account_id),
    )


class LoanCalendars:
    """One read pass's loan params and calendars, each loaded at most once.

    Keyed by the loan account's id.  Membership, never truthiness, decides
    whether a loan is loaded: ``None`` is a legitimate memoized answer for an
    account carrying no ``LoanParams``, and a truthiness check would reload
    it on every ask.
    """

    def __init__(self) -> None:
        """Load nothing; each loan is loaded on first use and kept."""
        self._params: "dict[int, LoanParams | None]" = {}
        self._calendars: "dict[int, LoanCalendar | None]" = {}

    def loan_params_of(self, loan_account_id: int) -> "LoanParams | None":
        """Return the loan's params, or ``None`` when it is not a configured loan.

        Args:
            loan_account_id: The loan account.

        Returns:
            Its :class:`~app.models.loan_params.LoanParams`, or ``None``.
        """
        if loan_account_id not in self._params:
            self._params[loan_account_id] = loan_loaders.load_loan_params(
                loan_account_id,
            )
        return self._params[loan_account_id]

    def loan_calendar_of(self, loan_account_id: int) -> "LoanCalendar | None":
        """Return the loan's calendar, or ``None`` when it is not a configured loan.

        Built over :meth:`loan_params_of`, so a pass loads the params row
        once for both.

        Args:
            loan_account_id: The loan account.

        Returns:
            Its :class:`._charges.LoanCalendar` (:func:`build_loan_calendar`),
            or ``None`` when the account carries no ``LoanParams``.
        """
        if loan_account_id not in self._calendars:
            params = self.loan_params_of(loan_account_id)
            self._calendars[loan_account_id] = (
                None if params is None else build_loan_calendar(params)
            )
        return self._calendars[loan_account_id]
