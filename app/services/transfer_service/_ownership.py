"""
Shekel Budget App -- Transfer Service ownership loaders

The owned-entity lookup helpers for :mod:`app.services.transfer_service`:
each loads one entity a transfer references (account, pay period, scenario,
category, transfer template) and verifies it belongs to the acting user,
raising :class:`~app.exceptions.NotFoundError` with an identical message for
both "missing" and "not yours" (the project security-response rule -- no
existence oracle).

**The PAY PERIOD one does not compare at all**, since plan step
``pay_calendar:C13-b``: it asks the owner's derived calendar, where an id
another user holds is simply ABSENT, so the two answers the rule wants
collapsed are one answer by construction rather than by two branches meeting.
See :func:`_get_owned_period`.  The other four still fetch and compare,
because no derivation owns their tables.

Extracted from ``transfer_service`` so that module stays under the 1000-line
module limit as the Build-Order Step 2 posting-ledger wiring lands.  These
five helpers are a cohesive, transfer-service-private cluster (single
responsibility: load-and-verify-ownership) with no dependency on the rest of
the service, mirroring the ``app/routes/transfers/_helpers.py`` split on the
route side.  Flask-isolated like the parent service: plain data in, ORM
objects out, no ``request`` / ``session`` imports.
"""

from app.extensions import db
from app.models.account import Account
from app.models.category import Category
from app.models.scenario import Scenario
from app.models.transfer_template import TransferTemplate
from app.services.pay_calendar import calendar_for
from app.exceptions import NotFoundError


def _get_owned_account(account_id, user_id, label="Account"):
    """Load an Account and verify ownership.

    Args:
        account_id: The primary key.
        user_id:    The expected owner.
        label:      Human-readable label for error messages.

    Returns:
        The Account object.

    Raises:
        NotFoundError: If the account does not exist or belongs to
            another user.  The message is identical in both cases
            (security response rule).
    """
    acct = db.session.get(Account, account_id)
    if acct is None or acct.user_id != user_id:
        raise NotFoundError(f"{label} {account_id} not found.")
    return acct


def _get_owned_period(pay_period_id, user_id):
    """Answer *user_id*'s calendar for *pay_period_id*, or refuse.

    **The ONE place the transfer family asks whether a submitted paycheck is
    the acting user's**, and since plan step ``pay_calendar:C13-b`` it asks
    the derivation rather than the table.  It was
    ``db.session.get(PayPeriod, ...)`` followed by
    ``period.user_id != user_id`` -- one of the EIGHT primary-key refetches
    finding **P75** counts, and the one the other four on this side collapsed
    into.  A calendar holds ONE owner's whole schedule, so a foreign id is
    ABSENT rather than present-and-rejected: there is no comparison left for a
    later edit to drop, which is the property C2-f3e ruled for the READ doors
    and this step extends to the write doors.

    **It returns a
    :class:`~app.services.pay_calendar.DerivedPeriod`, not a ``PayPeriod``**,
    and that is what lets its callers stop re-reading the row:
    :func:`~._loan_posting._reject_payment_before_origination` took the id and
    fetched the same row a second time in the same request, and now takes this
    value.

    **The four route-boundary copies are GONE** (developer, 2026-09-03).
    ``routes/transfers/mutations.py`` asked it twice, ``templates.py`` once
    and ``_instances.py`` once, each duplicating this call; commit C-27 /
    F-043 added them as defence in depth when there was no single producer to
    point at, and :func:`~._update._reject_unowned_references`' docstring
    records that reason.  The rule the developer ruled this step to is one walk
    and one answer, and the caller that cannot skip this one is the service
    tier.  **``routes/transfers/templates`` still resolves a period, and that
    resolution is a LIVE ownership control rather than a convenience.**  It
    does two jobs: it supplies the ``start_date`` the one-time branch needs as
    a ``due_date``, and its ``None`` arm REFUSES a period the owner's calendar
    does not hold.  On a RECURRING cadence the refusal is the whole of it --
    ``materialize_initial_transfers`` ignores ``start_period`` for a rule and
    the fan-out uses the calendar's own periods, so the submitted id never
    reaches this function and that route resolution is the ONLY thing standing
    between a crafted POST and a silently-ignored foreign period.  Graded by
    ``test_transfers.test_one_time.TestOneTimeTransfer.test_recurring_transfer_idor_period``.
    *A first version of this paragraph called it "not to check anything"*,
    which would have invited a later reader to delete the control plan step
    R7b-4 moved here to close.  It threads the resolved period to
    :func:`~app.routes.transfers._instances.materialize_initial_transfers`,
    which re-fetches nothing.

    **It is asked once per CALL and not once per REQUEST**, which matters
    because ``transfer_recurrence`` calls ``create_transfer`` once per
    generated occurrence and this derives a whole calendar each time, where
    the primary-key fetch it replaced hit the identity map.  Measured
    2026-09-03 on ``POST /transfers`` for a recurring template generating six
    transfers: **82 statements before this step, 88 after -- one per generated
    row**, against the ~13 per row that loop already costs.  The caller HAS the
    resolved period (``transfer_recurrence._new_row`` is handed a
    ``DerivedPeriod`` and passes only its id), so the fix is to thread it
    through :class:`~._create.TransferSpec` rather than to re-derive; that is a
    signature change across 84 construction sites and is not this step's.

    Args:
        pay_period_id: A submitted ``budget.pay_periods.id``.
        user_id: The owner every referenced row must belong to.

    Returns:
        The :class:`~app.services.pay_calendar.DerivedPeriod` carrying
        *pay_period_id*.

    Raises:
        NotFoundError: If the owner's calendar does not hold *pay_period_id*
            -- which is "no such period" and "not yours" in one answer.
        PayCalendarError: If the owner holds no pay schedule at all.  Uncaught
            here as it is at every other caller: a user submitting a transfer
            against a paycheck has a schedule, and the application-level
            handler answers the case where they do not (ruling **R-PC42**).
    """
    period = calendar_for(user_id).period_by_id(pay_period_id)
    if period is None:
        raise NotFoundError(f"Pay period {pay_period_id} not found.")
    return period


def _get_owned_scenario(scenario_id, user_id):
    """Load a Scenario and verify ownership.

    Raises:
        NotFoundError: If the scenario does not exist or belongs to
            another user.
    """
    scenario = db.session.get(Scenario, scenario_id)
    if scenario is None or scenario.user_id != user_id:
        raise NotFoundError(f"Scenario {scenario_id} not found.")
    return scenario


def _get_owned_category(category_id, user_id):
    """Load a Category and verify ownership.

    Returns None if *category_id* is None (caller explicitly passed
    no category).

    Raises:
        NotFoundError: If the category does not exist or belongs to
            another user.
    """
    if category_id is None:
        return None
    cat = db.session.get(Category, category_id)
    if cat is None or cat.user_id != user_id:
        raise NotFoundError(f"Category {category_id} not found.")
    return cat


def _get_owned_transfer_template(template_id, user_id):
    """Load a TransferTemplate and verify ownership.

    Returns None if *template_id* is None.

    Raises:
        NotFoundError: If the template does not exist or belongs to
            another user.
    """
    if template_id is None:
        return None
    tpl = db.session.get(TransferTemplate, template_id)
    if tpl is None or tpl.user_id != user_id:
        raise NotFoundError(f"Transfer template {template_id} not found.")
    return tpl
