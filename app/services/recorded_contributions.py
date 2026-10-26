"""
Shekel Budget App -- The RECORDED contributions into investment accounts.

The transfer-linked contribution feed the investment, retirement and savings
projections read, PRICED and DATED at this boundary (plan step X-au-c2, a
developer ruling of 2026-08-12; pay-calendar plan step C2-f2c).  It lived in
:mod:`app.services.projection_inputs` until plan step **balance:X-bi-6a**
pushed that module past the 1000-line ceiling, and it moved rather than that
module shaving: a feed with a valuation rule of its own lives beside the
orchestration that assembles the engine's inputs, not inside it -- the same
argument ``transfer_service._posting_sync`` was split out under.

**The loan loaders' ONE partition since plan step balance:X-bi-6-4b**
(ruling **R-BAL140**).  A contribution is the to-side leg of a transfer into
the account: one that has SETTLED is worth what its record moved
(:func:`app.services.row_valuation.leg_settled_contribution`), one still
PROJECTED what the parent resolves to
(:func:`app.services.cash_ledger.planned_leg_contribution`), and WHICH half a
leg is in is :func:`app.services.loan_loaders.income_legs`' answer -- the
same one the balance seam's contribution events read -- so ``is_confirmed``
is decided by the partition and never by a second reading of the status
column.  From X-bi-6a (ruling **R-BAL13**) until then this module ran a
partition of its own: settled income-SHADOW rows beside every still-Projected
transfer, which counted a status drift no door writes TWICE (a settled shadow
under a Projected parent) where the one partition counts it once.

Boundary discipline (``CLAUDE.md``: "services are isolated from Flask"): no
Flask symbol; plain data in, the
:class:`~app.services.investment_projection.ShadowContributions` DTO out.  It
is here and not in :mod:`app.services.investment_projection` because that
module promises no database access, and this one is a session read.
"""

from app.extensions import db
from app.models.transfer import Transfer
from app.services.cash_ledger import (
    AmountBasis,
    planned_leg_contribution,
    transfer_pricing_load_options,
)
from app.services.investment_projection import (
    PricedContribution,
    ShadowContributions,
)
from app.services.loan_loaders import income_legs
from app.services.row_valuation import leg_settled_contribution


def load_shadow_income_contributions_for_accounts(
    basis: AmountBasis,
    account_ids: list[int], period_ids: list[int],
) -> ShadowContributions:
    """Return PRICED shadow-income contributions across many accounts.

    Batch variant used by services that classify many accounts in one
    pass.  Returned records carry their original ``account_id`` so callers
    can group / partition downstream.  Returns an empty list when
    either ``account_ids`` or ``period_ids`` is empty so callers do
    not issue ``IN ()`` queries against PostgreSQL.

    **This is the BOUNDARY where a contribution is valued** (plan step
    X-au-c2, a developer ruling of 2026-08-12).  It used to return ORM rows and
    four readers in :mod:`app.services.investment_projection` each asked them
    for ``effective_amount`` behind its own copy of the
    ``status_contributes_to_balance`` screen.  That property cannot answer for
    a row whose amount is DERIVED -- such a row stores no figure -- and a module
    whose docstring promises no database access can never resolve one.  So the
    resolution happens HERE, where the session is, against ONE basis for the
    whole cross-account set, which is also one paycheck-engine run rather than
    one per account (finding **N-228**, and what re-keying the basis on the
    OWNER rather than an ``Account`` bought: the basis memoizes each
    derivation, so a per-leg pricing over it runs the engine once).

    **It is also where a contribution is DATED, since plan step C2-f2c**, and
    that is the same argument applied to the same record's other derived fact.
    Every reader downstream buckets contributions by pay period and then needs
    that period's PAYDAY -- the YTD windows to compare it against the current
    period's, the timeline to stamp it on a
    :class:`~app.services.growth_engine.ContributionRecord`.  Carrying the id
    alone made each of them take the owner's whole period list as a lookup
    table, so three public signatures held a join this loader can do.  The
    payday is the transfer's period's ``start_date`` -- the paydays' own column
    and the one plan step **C4** keeps, so this reads a fact rather than a
    derivation -- and the partition's producers load that period with each
    leg, because they sort on it.

    **Rows that contribute nothing are DROPPED rather than priced at zero.**
    :func:`~app.services.investment_projection._inputs._average_transfer_contribution`
    divides by the number of distinct pay periods it sees, so a Cancelled
    contribution carried through as ``$0.00`` would enlarge that denominator and
    silently lower the average.  The screen is applied before the pricing for
    the same reason the valuation gates before it resolves: an excluded row has
    no derived answer to give.

    **It reads the loan loaders' ONE partition since plan step
    balance:X-bi-6-4b** (ruling **R-BAL140**), one account at a time, and the
    screen above is that partition's narrowing.  A contribution that has
    SETTLED -- its transfer settled, or its to-side money moved -- is worth
    what its record moved
    (:func:`~app.services.row_valuation.leg_settled_contribution`); one still
    PROJECTED is worth what the parent resolves to
    (:func:`~app.services.cash_ledger.planned_leg_contribution`).  Neither
    half admits a Credit or Cancelled transfer, so the Python screen this used
    to apply has nothing left to drop; ``is_confirmed`` is decided by WHICH
    half a leg came from rather than by a second reading of the status
    column.  The partition is unwindowed, so the period window is applied to
    each half here.  The LINK set is a separate question over every live
    transfer in the window in ANY status, and it is asked by a query of its
    own.

    The ``eager_status`` switch is gone with them.  It defaulted to ``False``
    while every consumer needed the status, so the retirement chain lazy-loaded
    it per row; the status is now read exactly once here, under a ``joinedload``
    that is no longer optional.

    Args:
        basis: The read pass's
            :class:`~app.services.cash_ledger.AmountBasis` -- the owner and the
            scenario these amounts resolve under, and the derivations they
            resolve through.  Taken rather than built here since plan step
            X-au-c2b, so a caller that also prices its own rows pays for the
            paycheck engine once (findings **N-268**, **N-269**).
        account_ids: Investment / retirement account ids to scope to.
        period_ids: Pay-period ids to scope the contribution window
            against.

    Returns:
        A :class:`~app.services.investment_projection.ShadowContributions` --
        the priced ``records`` (callers partition by ``account_id``
        themselves, typically a comprehension inside a per-account loop) and
        the ``linked_account_ids`` of every account that has a transfer INTO
        it in the window WHATEVER its status.  The second field is not
        decoration: an adversarial review found that screening the records
        alone flipped ``retirement_projection``'s ``none_linked`` for an
        account whose contributions were all Cancelled, telling the owner to
        link a contribution that already exists.  It is read off the PARENT
        transfers since plan step balance:X-bi-6a -- every transfer has its
        income shadow and every income shadow its transfer (Transfer
        Invariants 1 and 2), so the two readings are one set, and the parent
        is the one that survives ``X-bi-6``.

    Raises:
        AmountUnresolvable: From the amount model, for a contribution whose
            rule cannot price it.  A refusal is never a fallback.
        ValueError: From :func:`app.services.loan_loaders.income_legs`, for
            a transfer whose status the partition cannot place.
    """
    if not account_ids or not period_ids:
        return ShadowContributions(records=[], linked_account_ids=frozenset())
    window = set(period_ids)
    records: list[PricedContribution] = []
    # One account at a time through the ONE partition: the same producer the
    # balance seam's contribution events read, so the two cannot place a
    # payment in different halves.  ``dict.fromkeys`` keeps the caller's order
    # and reads a repeated id once, as the ``IN`` it replaced did.
    for account_id in dict.fromkeys(account_ids):
        payments = income_legs(
            account_id, basis.scenario_id,
            options=(), leg_options=transfer_pricing_load_options(),
        )
        records += [
            PricedContribution(
                account_id=leg.account_id,
                payday=leg.pay_period.start_date,
                amount=leg_settled_contribution(leg),
                is_confirmed=True,
            )
            for leg in payments.settled
            if leg.pay_period_id in window
        ]
        records += [
            PricedContribution(
                account_id=leg.account_id,
                payday=leg.pay_period.start_date,
                amount=planned_leg_contribution(leg, basis),
                is_confirmed=False,
            )
            for leg in payments.projected
            if leg.pay_period_id in window
        ]
    # The LINK set: every live transfer into these accounts in the window,
    # WHATEVER its status.  A Cancelled contribution counts nothing but is
    # still a LINK, and the consumer asking whether an account has one is
    # asking a different question from the consumers that sum amounts.
    linked = (
        db.session.query(Transfer.to_account_id)
        .filter(
            Transfer.to_account_id.in_(account_ids),
            Transfer.pay_period_id.in_(period_ids),
            Transfer.scenario_id == basis.scenario_id,
            Transfer.is_deleted.is_(False),
        )
        .distinct()
        .all()
    )
    return ShadowContributions(
        records=records,
        linked_account_ids=frozenset(row[0] for row in linked),
    )


def load_shadow_income_contributions_for_account(
    basis: AmountBasis,
    account_id: int, period_ids: list[int],
) -> ShadowContributions:
    """Return PRICED shadow-income contributions into a single account.

    Used by the investment-detail dashboard.  Filters to the contributions
    into the account -- the to-side legs of transfers into it -- in the
    supplied period window so :func:`calculate_investment_inputs` can derive
    the YTD contribution total and the contribution timeline can layer
    historical receipts.  Returns an empty feed when ``period_ids`` is empty
    so callers do not issue an ``IN ()`` query against PostgreSQL.

    Args:
        basis: The read pass's amount basis (see the batch variant).
        account_id: ID of the investment / retirement account.
        period_ids: Pay-period ids to scope the contribution window
            against.

    Returns:
        The :class:`~app.services.investment_projection.ShadowContributions`
        for the account (see the batch variant for what pricing at this
        boundary buys).
    """
    return load_shadow_income_contributions_for_accounts(
        basis, [account_id], period_ids,
    )
