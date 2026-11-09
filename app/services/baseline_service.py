"""
Shekel Budget App -- Baseline Scenario Creation + Posting-Ledger Recovery

Creating a user's missing baseline scenario is not a one-row insert, because a
posting ledger is SCENARIO-scoped (``journal_entries.scenario_id``): everything
that wanted to post while the user had no baseline had nowhere to put it and was
skipped.  Minting the scenario is therefore only half the repair; the other half
is re-deriving those skipped corrections from the source facts that outlived the
gap.  This module owns both halves as one transaction, so a caller cannot do the
first and forget the second (which is exactly what
``routes.grid.create_baseline`` did to the loan ledger).

**Why its own module.**  It sits ABOVE both posting packages and
:mod:`app.services.scenario_resolver`, and depends on all three.  It cannot live
in ``scenario_resolver`` -- both posting packages import that module for
``get_baseline_scenario``, so putting this there inverts the layering and pylint
reports the cycle (R0401).  A leaf stays a leaf; the composition lives here.

Flask-isolated: takes a user id, returns a model or ``None``; no ``request`` /
``session``.
"""

from app.extensions import db
from app.models.scenario import Scenario
from app.services import account_posting_service, loan_posting_service
from app.services.scenario_resolver import get_baseline_scenario


def create_baseline_scenario(user_id: int) -> Scenario | None:
    """Create a user's missing baseline scenario, recovering BOTH posting ledgers.

    The recovery path behind ``routes.grid.create_baseline``.  Both per-user
    resyncs run in the SAME transaction as the new scenario, each re-deriving
    its corrections from source facts that outlived the baseline-less gap:

    * **Loans** (:func:`app.services.loan_posting_service.resync_user_loan_postings`)
      -- a loan's opening posts per scenario at params-create
      (``loan_posting_service.sync_loan_postings_all_scenarios`` iterates the
      scenarios the loan is displayed in, and a baseline-less owner has none), so
      a loan configured while the baseline was gone carries no OPENING posting.
      The balance seam still ANSWERS such a loan correctly -- it folds the loan's
      SOURCE facts (origination, anchors, settled payments), so a cold posting
      cache is a repairable inconsistency, not a read outage (plan steps
      C3b1/C3b3; before them the seam raised and every loan surface 500'd until
      this ran).  What this restores is the POSTING ledger itself, so it
      reconciles again; no screen reads it since plan task plan#2771, and the
      checked-projection assert that graded it against the fold went at plan
      task plan#3093.  Its ``LoanParams`` and
      ``user_trueup`` rows carry no ``pay_period_id`` and survive, so the openings
      and true-ups re-derive exactly.
    * **Non-loan accounts**
      (:func:`app.services.account_posting_service.resync_user_account_anchor_postings`)
      -- an account created without a baseline has its anchor correction skipped
      with a loud log (``account_service.create_account``); its
      ``AccountAnchorHistory`` rows survive, so its opening re-derives too.

    The two are separate calls, not one merged sweep: they answer the same
    question ("re-derive what could not post") from DIFFERENT source facts, and
    each package owns the derivation of its own.

    Idempotent: a user who already has a baseline is left untouched and gets
    ``None``, so a double-submitted form cannot mint a second one (the partial
    unique index ``uq_scenarios_one_baseline`` is the storage-tier backstop).

    Commits -- this is a whole recovery, and a half-applied one would leave the
    loan ledger in exactly the broken state it exists to repair.

    Args:
        user_id: The user whose baseline to create.

    Returns:
        The new :class:`~app.models.scenario.Scenario`, or ``None`` when the user
        already had one (nothing was created and nothing was resynced).
    """
    if get_baseline_scenario(user_id) is not None:
        return None
    scenario = Scenario(user_id=user_id, name="Baseline", is_baseline=True)
    db.session.add(scenario)
    loan_posting_service.resync_user_loan_postings(user_id)
    account_posting_service.resync_user_account_anchor_postings(user_id)
    db.session.commit()
    return scenario
