"""
Shekel Budget App -- Baseline Scenario Creation

Creating a user's missing baseline scenario: the recovery path behind
``routes.grid.create_baseline``.  The worked-out ledger reads whichever scenario
it is asked for, from the records, so the new scenario needs nothing posted.

*History, until plan task plan#3076.*  Creating the scenario was not a one-row
insert while the app wrote a posting ledger, because that ledger is
SCENARIO-scoped (``journal_entries.scenario_id``): everything that wanted to
post while the user had no baseline had nowhere to put it and was skipped, so
this module re-derived those skipped corrections from the source facts that
outlived the gap, in the same transaction (the two per-user re-syncs,
``loan_posting_service.resync_user_loan_postings`` and
``account_posting_service.resync_user_account_anchor_postings``).  It had its
own module because it sat ABOVE both posting packages and
:mod:`app.services.scenario_resolver`, which both posting packages import for
``get_baseline_scenario``; the re-syncs went with the stored ledger's doors.

Flask-isolated: takes a user id, returns a model or ``None``; no ``request`` /
``session``.
"""

from app.extensions import db
from app.models.scenario import Scenario
from app.services.scenario_resolver import get_baseline_scenario


def create_baseline_scenario(user_id: int) -> Scenario | None:
    """Create a user's missing baseline scenario, and commit.

    The recovery path behind ``routes.grid.create_baseline``.  *Until plan task
    plan#3076 it also re-synced both posting ledgers in the same transaction,
    re-deriving the corrections the baseline-less gap had skipped; the
    worked-out ledger needs none of it.*

    Idempotent: a user who already has a baseline is left untouched and gets
    ``None``, so a double-submitted form cannot mint a second one (the partial
    unique index ``uq_scenarios_one_baseline`` is the storage-tier backstop).

    Args:
        user_id: The user whose baseline to create.

    Returns:
        The new :class:`~app.models.scenario.Scenario`, or ``None`` when the user
        already had one (nothing was created).
    """
    if get_baseline_scenario(user_id) is not None:
        return None
    scenario = Scenario(user_id=user_id, name="Baseline", is_baseline=True)
    db.session.add(scenario)
    db.session.commit()
    return scenario
