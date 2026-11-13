"""
Shekel Budget App -- Account Projection Dispatcher (MED-01 / S6-03 / S6-04)

One flag-driven classification of which projection engine drives an
account's period balances, consumed by every dashboard that branches
on account type.  Collapses the two divergent branch ladders that
lived in :func:`savings_dashboard_service._compute_account_projections`
and :func:`year_end_summary_service._get_account_balance_map`
(S6-03 in ``docs/audits/financial_calculations/06_dry_solid.md``)
into a single classifier whose flag-driven order is the project's
canonical answer to "which engine for this account?".  Also centralises
the payroll-deduction funding decision that previously lived as a
hardcoded enum-frozenset literal in ``app/routes/investment.py`` (S6-04;
see :func:`is_payroll_deduction_funded` for the replacement).  Since plan
step credit_card:CC-1 it also holds :func:`is_revolving`, the ONE predicate
every credit-card feature gates on: a schema flag on the account's type,
which is the shape the payroll-funding comment below asks for rather than a
copy of its enum tuple, and since plan task plan#3530 :func:`pays_a_card`,
that predicate asked of the account a transfer or its definition pays into.

Pure functions over Account / AccountType / Transfer / TransferTemplate /
int.  No Flask imports
(the service-boundary rule from ``CLAUDE.md``).  Caller-supplied
``ref_cache_module`` keeps this module free of circular import
worries with :mod:`app.ref_cache` while preserving the IDs-for-logic
standard (``docs/coding-standards.md:174-178``).

**Kind metadata only: this module answers "what KIND is this account?",
never "what is it worth on date T?".**  It came off the balance-producer
call allowlist at plan step D1b -- it calls no balance producer, so the
exemption only permitted a bypass -- and that allowlist has since been
deleted whole (plan step E1e).  It REMAINS on the W9909 completeness
registry, so a new public function here must be classified as a producer
or a non-producer before it will lint.  Why that half
stayed (it defined the loan forward-projection producers until one day
before D1b, and :func:`classify_account` takes a live
:class:`~app.models.account.Account`) is recorded once, at
``shekel_checkers.balance_seam._KIND_CLASSIFIER_MODULES``.
"""

from enum import Enum

from app.enums import AcctTypeEnum
from app.models.transfer import Transfer
from app.models.transfer_template import TransferTemplate


class AccountProjectionKind(Enum):
    """Which projection engine drives an account's period balances.

    The order reflects the canonical precedence the dual dispatchers
    expressed inconsistently pre-Commit-28 (S6-03):

    1. :data:`AMORTIZING` -- loan amortization engine
       (:func:`app.services.rate_period_engine.replay_schedule`
       + :func:`app.services.amortization_engine.project_forward`, fed
       by :func:`app.services.loan_resolver.resolve_loan` via the
       :func:`app.services.loan_resolver.compute_payoff_scenarios`
       composer).
    2. :data:`INTEREST` -- a modelled accrual over the account's folded
       cash balance, resolved by the seam's ONE event replay
       (:mod:`app.services.balance_at._asset_fold`).
    3. :data:`APPRECIATING` -- the growth engine run as pure compound
       appreciation with no contributions
       (:func:`app.services.growth_engine.project_balance`, fed by
       :class:`~app.models.asset_appreciation_params.AssetAppreciationParams`).
       A physical asset (Property) whose user-set market value compounds
       forward; pre-anchor periods hold the value flat (a manually-set
       valuation is not back-cast).  Checked BEFORE :data:`INVESTMENT`
       because a Property carries ``has_parameters=True`` too.
    4. :data:`INVESTMENT` -- growth engine
       (:func:`app.services.growth_engine.project_balance`).
    5. :data:`PLAIN` -- the generic entries-aware producer
       (:func:`app.services.balance_at._cash_fold.period_balances`).
    """

    AMORTIZING = "amortizing"
    INTEREST = "interest"
    APPRECIATING = "appreciating"
    INVESTMENT = "investment"
    PLAIN = "plain"


def classify_account(account) -> AccountProjectionKind:
    """Return the :class:`AccountProjectionKind` for *account*.

    Branches solely on the boolean columns on the linked
    :class:`~app.models.ref.AccountType`
    (``has_amortization`` / ``has_interest`` / ``has_appreciation`` /
    ``has_parameters``): no enum-name comparisons, no name strings --
    consistent with the IDs-for-logic standard.  ``has_appreciation`` is
    checked before ``has_parameters`` so a parameterised physical asset
    (Property) classifies as :data:`APPRECIATING`, not :data:`INVESTMENT`.
    An account with no ``account_type``
    (degenerate / partially loaded) classifies as :data:`PLAIN`
    so the canonical balance resolver still produces a sensible
    output rather than the caller raising on ``None.has_amortization``.

    The order matters: an :class:`~app.models.ref.AccountType` for
    which both ``has_amortization`` and ``has_interest`` are True
    (no such row exists in the seed catalog today, but the schema
    permits it) classifies as :data:`AMORTIZING` because the
    amortization engine consumes the schedule and the interest
    calculator's layered interest accrual is irrelevant for a
    liability balance.

    Args:
        account: An :class:`~app.models.account.Account` with its
            ``account_type`` relationship eager-loaded (the consumer
            is expected to ``joinedload`` it; the classifier does
            not issue queries).

    Returns:
        The :class:`AccountProjectionKind` for this account.
    """
    acct_type = account.account_type
    if acct_type is None:
        return AccountProjectionKind.PLAIN
    if acct_type.has_amortization:
        return AccountProjectionKind.AMORTIZING
    if acct_type.has_interest:
        return AccountProjectionKind.INTEREST
    if acct_type.has_appreciation:
        return AccountProjectionKind.APPRECIATING
    if acct_type.has_parameters:
        return AccountProjectionKind.INVESTMENT
    return AccountProjectionKind.PLAIN


def is_revolving(account) -> bool:
    """Return True iff *account* is a revolving credit line (a Credit Card).

    The ONE predicate every credit-card feature gates on (design
    ``docs/design/credit_card_from_scratch.md`` 3.1, ruling ``credit_card:R-CC14``,
    plan step CC-1): the statement cycle, the payment rule, the refusals at the
    transfer doors, the "checking and its cards" plan-item set.  It reads the
    schema flag :attr:`~app.models.ref.AccountType.has_revolving_credit`, which
    only the seed sets, so it is the shape the comment under
    :func:`classify_account` asks for -- a flag the type carries -- rather than
    a second enum tuple beside :data:`_PAYROLL_DEDUCTION_FUNDED_TYPES`.

    **It is NOT a projection kind, and :func:`classify_account` never reads it.**
    A card's balance is ``opening + SUM(movements)``, the cash fold every
    non-loan account already rides, so a card classifies PLAIN.  The balance seam
    asks this flag only of what a card alone has: the day a plan row counts from
    (``balance_at._cash_plan``, ruling plan#2926), a repeat on its due dates
    (``balance_at._definition_due_dates``) and its unmarked rows
    (``balance_at._cash_flow``).  The 2026-07-19 plan's sixth
    kind (``REVOLVING``) existed to send the card to a fold of its own, which
    ruling R-CC14 dissolved; ``ck_account_types_revolving_is_plain`` makes a
    type that is revolving AND amortizing / interest / appreciating /
    parameterised unrepresentable, so there is no precedence to decide here.

    Args:
        account: An :class:`~app.models.account.Account` with its
            ``account_type`` relationship loaded (the same contract as
            :func:`classify_account`; this predicate issues no queries).

    Returns:
        True when the account's type carries ``has_revolving_credit``; False
        for every other type and for an account with no ``account_type``
        (degenerate / partially loaded -- the same PLAIN default the classifier
        takes, so a half-loaded account is never mistaken for a card).
    """
    return is_revolving_type(account.account_type)


def is_revolving_type(acct_type) -> bool:
    """Return True iff *acct_type* makes its accounts revolving credit lines.

    :func:`is_revolving` asked of a TYPE, for a door that holds the type before
    the account exists: account creation settles a new card's payment before
    the card's own row is written (``card_payoff.plan_payoff``, task plan#2933).
    :func:`is_revolving` delegates here, so the flag is read in one place.

    Args:
        acct_type: An :class:`~app.models.ref.AccountType`, or ``None``.

    Returns:
        True when the type carries ``has_revolving_credit``; False for every
        other type and for ``None``.
    """
    if acct_type is None:
        return False
    return acct_type.has_revolving_credit


def pays_a_card(transfer: Transfer | TransferTemplate) -> bool:
    """Return True iff *transfer*'s money goes INTO a credit card.

    The ONE test of "this transfer pays a card's bills off rather than moving
    money anywhere else", asked of a transfer and of the definition that makes
    its rows alike, since both name the account the money goes to
    (``to_account``).  Three readers leave such a payment out of a total because
    the card's bills are already in it, so counting the payment too counted
    one purchase twice: the Spending report, of a settled leg
    (``spending_analysis._pays_a_card``, plan task plan#2932, ruling
    plan#2926), the Recurring page, of a definition
    (``recurring_view``, plan task plan#3530, ruling plan#3527), and the
    emergency-fund floor on ``/savings``, which counts instead the bills of
    each card whose one payment leaves checking
    (``savings_dashboard_service._metrics._committed_expense_floor``, ruling
    plan#3896).  A fixed amount sent to the card is such a payment too, which
    is the cost ruling plan#3527 states.

    Args:
        transfer: A :class:`~app.models.transfer.Transfer` or a
            :class:`~app.models.transfer_template.TransferTemplate`, its
            ``to_account`` and that account's type loaded or loadable.

    Returns:
        True when the account the money goes to is revolving
        (:func:`is_revolving`).
    """
    return is_revolving(transfer.to_account)


# Payroll-deduction-funded account types.  The schema does not
# currently carry a metadata flag for "this account type is funded
# by employer payroll deduction" (S6-04 in ``06_dry_solid.md``
# records this as report-only -- the audit explicitly does not invent
# the flag).  The enum tuple is the single source of this decision:
# when a new employer-sponsored type is added (403(b), Roth 403(b),
# TSP, SIMPLE IRA), extend this tuple or, ideally, replace the helper
# with a schema flag introduced by a follow-up migration.  Either
# change touches one site.
_PAYROLL_DEDUCTION_FUNDED_TYPES = (
    AcctTypeEnum.K401,
    AcctTypeEnum.ROTH_401K,
)


def is_payroll_deduction_funded(
    account_type_id: int,
    ref_cache_module,
) -> bool:
    """Return True iff *account_type_id* designates a payroll-funded type.

    Used by the investment dashboard to choose between the
    employer-sponsored-plan prompt (link to the salary profile's
    deductions tab) and the individual-contribution prompt (create a
    recurring transfer).  Pre-Commit-28 the decision lived as a
    hardcoded enum-frozenset literal in
    ``app/routes/investment.py:60`` enumerating K401 and ROTH_401K;
    centralising it here closes the OCP smell S6-04 names (a new
    payroll-funded type required editing the route, not just adding
    a seed row).

    Args:
        account_type_id: The account's ``account_type_id``.
        ref_cache_module: The :mod:`app.ref_cache` module
            (parameter-injected to avoid an import cycle between
            this service and the cache layer -- both are imported by
            multiple route layers).

    Returns:
        True when the type is in the project's payroll-deduction
        catalog, False otherwise.
    """
    funded_ids = {
        ref_cache_module.acct_type_id(t)
        for t in _PAYROLL_DEDUCTION_FUNDED_TYPES
    }
    return account_type_id in funded_ids
