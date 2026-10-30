"""
Shekel Budget App -- Baseline Scenario Resolver

Single source of truth for "load the user's baseline scenario."
Every analytics service (calendar, spending_trend, dashboard,
retirement_dashboard, savings_dashboard, year_end_summary)
needs the user's baseline scenario as a starting point for queries
that must NOT spill into what-if scenarios.

Centralising the lookup in one helper closes the DRY violation that
pylint R0801 flagged across those services (Issue 1 of the
``shekel-audits/security-2026-04-15/c-38-followups.md`` audit) and
gives a single place to extend the resolution logic (e.g. honouring
a future ``UserSettings.default_scenario_id`` override) without
chasing seven copies.
"""

from app.exceptions import BaselineMissingError
from app.extensions import db
from app.models.scenario import Scenario


def get_baseline_scenario(user_id: int) -> Scenario | None:
    """Return the user's baseline scenario, or ``None`` if absent.

    Every user has exactly one baseline scenario (enforced by a
    partial unique index ``uq_scenarios_one_baseline`` that scopes the
    constraint to ``is_baseline IS TRUE``); the
    ``.filter_by(is_baseline=True).first()`` shape is safe.

    **A caller that cannot ANSWER without a scenario takes
    :func:`require_baseline_scenario` instead** (plan step X-v2, ruling R-BX).
    This nullable form is for the callers whose rule has a defined answer for
    absence -- a WRITE that legitimately no-ops, or a check of whether one
    exists at all.  It is the same split
    :attr:`~app.services.balance_at.BalanceContext.scenario_id` and
    ``scenario_id_or_none`` make one tier up, in the same direction: the
    obvious name fails loud, and reaching for the nullable reads as a decision.

    In production every OWNER has a baseline scenario created by
    ``registration_service.register_user`` at sign-up, nothing in ``app/`` or
    ``scripts/`` deletes one or clears ``is_baseline``, and no path promotes a
    companion to owner -- so ``None`` means a COMPANION (who owns no budget
    rows by design; ``scripts/integrity_check`` DC-08 excludes that role for
    exactly this reason), a test fixture that did not seed one, or data changed
    outside the application.

    Args:
        user_id: The user whose baseline scenario should be loaded.

    Returns:
        The baseline :class:`Scenario` instance, or ``None`` if the
        user has no baseline scenario.
    """
    return (
        db.session.query(Scenario)
        .filter_by(user_id=user_id, is_baseline=True)
        .first()
    )


def require_baseline_scenario(user_id: int) -> Scenario:
    """Return the user's baseline scenario, or raise the named exception.

    The form every caller takes when its answer is UNDEFINED without a
    scenario (plan step X-v2, ruling R-BW): one application-level handler
    catches :class:`~app.exceptions.BaselineMissingError` and renders the
    setup-recovery page, so the caller neither invents a degraded figure nor
    500s.

    **It exists because two financial statements were inventing one.**  The
    balance sheet and the income statement each resolved this nullable, saw
    ``None``, and returned an EMPTY report -- which for the balance sheet meant
    assets ``$0.00``, liabilities ``$0.00``, equity ``$0.00`` and
    ``tie_out.in_balance = True``: the app asserting a user's books balance
    over a ledger it could not read.  Found by plan step X-v2's adversarial
    correctness review, and it is finding N-113's class exactly.

    Args:
        user_id: The user whose baseline scenario is required.

    Returns:
        The baseline :class:`Scenario`.

    Raises:
        BaselineMissingError: When the user has no baseline scenario.  A
            ``ValueError`` subclass; its message names the repair.
    """
    scenario = get_baseline_scenario(user_id)
    if scenario is None:
        raise BaselineMissingError(
            f"user {user_id} has no baseline scenario, so this cannot be "
            f"answered for them. Every owner gets one at registration "
            f"(registration_service.register_user) and nothing deletes one, so "
            f"reaching this means the data was changed outside the app, or "
            f"the user is a companion (who owns no budget rows by design): "
            f"POST /grid/create-baseline repairs it, together with both "
            f"posting ledgers",
            user_id=user_id,
        )
    return scenario
