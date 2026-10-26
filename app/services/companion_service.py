"""
Shekel Budget App -- Companion Service

Data access layer for the companion view.  Answers only the linked owner's
transactions a companion may see -- those whose
``Transaction.visible_to_companion`` is True -- for one pay period.

This is the security boundary for all companion data access.  Every
function validates that the requesting user is a companion with a
valid ``linked_owner_id`` before touching any owner data.

**``get_companion_periods`` was DELETED at plan step C2-f2b.**  It answered
"every pay period of the companion's linked owner", built for a period-nav
``<select>`` that ``companion/index.html`` now renders from its own prev / next
links, and an AST census that day found it with ZERO callers in ``app/`` -- so
it was a live ``get_all_periods`` reader that no request could reach.  Stated
here rather than silently dropped because its tests went with it.

Architecture:
  - No Flask imports.  Receives plain data, and returns ORM transactions
    inside a :class:`CompanionPageRead` whose three period fields are
    ``pay_calendar`` VALUE objects (plan step C2-f2b), or raises.
  - Flushes to the session but does NOT commit.  The caller owns the
    database transaction boundary.
"""

import logging
from typing import NamedTuple

from sqlalchemy.orm import selectinload

from app.extensions import db
from app import ref_cache
from app.enums import RoleEnum
from app.exceptions import NotFoundError
from app.models.transaction import Transaction
from app.models.user import User
from app.services.pay_calendar import DerivedPeriod, calendar_for
from app.services.scenario_resolver import require_baseline_scenario
from app.utils.dates import display_today

logger = logging.getLogger(__name__)


class CompanionPageRead(NamedTuple):
    """Everything one companion page render needs from the owner's schedule.

    What :func:`get_visible_transactions` answers.  The companion page asks the
    linked owner's calendar THREE questions -- which paycheck is on screen,
    what opened before it, what opens after it -- and
    :func:`~app.services.pay_calendar.calendar_for` memoizes nothing, so a
    return of the period alone would have the route derive that whole calendar
    a second time to answer the other two.

    **So this carries the ANSWERS and not the calendar that produced them**,
    which an adversarial design review of plan step C2-f2b corrected: a first
    cut returned the ``PayCalendar`` itself, and its only consumer was one
    route helper asking exactly these two searches.  Handing a whole schedule
    across this module's security boundary so a presentation layer can search
    it is the shape the arc already refused once, when ruling **R-PC19** ("How
    the CONTRIBUTION tier learns its periods") rejected an OWNER value riding
    on a per-account record.  The neighbours are computed here, beside the
    validation that decided whose calendar it is.

    Attributes:
        transactions: The companion-visible rows in :attr:`period`, with
            ``entries`` and ``template`` eager-loaded.
        period: The paycheck on screen.  Its ``period_id`` is never ``None``
            -- both branches that produce it answer SAVED periods only.
        previous: The paycheck opening before :attr:`period`, or ``None`` at
            the head of the saved schedule -- which is what hides the back link.
        next_period: The paycheck opening after it, or ``None`` at the tail.
            Named around the ``next`` builtin rather than shadowing it.
    """

    transactions: list[Transaction]
    period: DerivedPeriod
    previous: "DerivedPeriod | None"
    next_period: "DerivedPeriod | None"


def _validate_companion(user_id: int) -> User:
    """Load and validate a companion user.

    Verifies the user exists, has the companion role, and has a
    non-null ``linked_owner_id``.  Returns the User object on
    success.

    Args:
        user_id: The ID of the user to validate.

    Returns:
        User object that passed all companion checks.

    Raises:
        NotFoundError: If the user does not exist, is not a
            companion, or has no linked owner.
    """
    user = db.session.get(User, user_id)
    if user is None:
        raise NotFoundError("User not found.")

    companion_role_id = ref_cache.role_id(RoleEnum.COMPANION)
    if user.role_id != companion_role_id:
        raise NotFoundError("User is not a companion.")

    if user.data_owner_id is None:
        raise NotFoundError(
            f"Companion user {user_id} has no linked owner. "
            "This is a data integrity issue -- contact the administrator."
        )

    return user


def get_visible_transactions(
    companion_user_id: int,
    period_id: int | None = None,
) -> CompanionPageRead:
    """Get transactions visible to a companion user for a pay period.

    Loads the linked owner's live rows for the period in the baseline
    scenario and keeps those whose :attr:`Transaction.visible_to_companion`
    answers True -- a row naming a definition by that definition's flag, and
    a row naming none (a CC payback) never.  Eager-loads entries for progress
    computation.

    **The visibility rule is asked of each row, never restated in SQL**
    (plan step ``balance:X-bi-1b``, ruling **R-BAL19**, finding
    **BAL-482**).  This query carried ``TransactionTemplate.companion_visible
    OR (template_id IS NULL AND Transaction.companion_visible)`` -- the
    property's resolution rule spelled a second time, in another language,
    on the predicate that decides what a companion may see -- and it was the
    one reader that kept the row's ``companion_visible`` cell a public
    column when ``is_envelope`` was sealed at X-bi-1.  The clause is gone,
    the cell is sealed, and a query keyed on either name refuses to build;
    what a companion sees is decided in exactly one place, the same one
    ``auth_helpers.get_accessible_transaction`` asks.  The cost is loading
    the rows the filter drops: measured 2026-09-12 on a production restore,
    the baseline scenario's 951 live rows across its 64 saved paychecks are
    what the 64 companion pages load, 232 of them are shown, and the 719
    dropped carry 5 purchase entries between them.

    Defense-in-depth: verifies the user is a companion with a valid
    ``linked_owner_id`` before querying.

    **The period comes off the OWNER's pay calendar since plan step C2-f2b**,
    which is what let the companion page and the owner's mobile grid keep
    sharing ``grid/_mobile_this_period.html``: that partial reads a period's
    identity, and once the grid's periods became
    :class:`~app.services.pay_calendar.DerivedPeriod` values a partial serving
    both an ORM row and a derived one would have been the two-types-in-one-
    template shape the ``C2-f`` decomposition ruling exists to refuse.

    **It also makes the cross-owner refusal STRUCTURAL.**  This function
    resolved the submitted id with ``db.session.get(PayPeriod, ...)`` and then
    compared ``period.user_id`` against the linked owner -- the security
    boundary this module's docstring claims, written out by hand in one place.
    :meth:`~app.services.pay_calendar.PayCalendar.period_by_id` searches ONE
    owner's calendar, so a period belonging to anyone else is absent rather
    than rejected and the comparison no longer exists to be dropped by a later
    edit.  ``NotFoundError`` still carries one message for both "no such
    period" and "not yours", which is the project's security response rule.

    Args:
        companion_user_id: The companion user's ID.
        period_id: Optional period filter.  If None, returns the
            current period's transactions.

    Returns:
        The :class:`CompanionPageRead` -- the rows, the paycheck they sit in,
        and the paychecks either side of it for the page's navigation.  The
        period's ``period_id`` is never ``None``:
        :meth:`~app.services.pay_calendar.PayCalendar.period_by_id` answers
        only saved periods, and the containment search below is the SAVED one,
        so no projected span can reach the transaction filter.

    Raises:
        NotFoundError: User is not a companion, has no linked owner,
            period not found, or period belongs to a different owner.
        PayCalendarError: The owner's paydays cannot define a calendar.  *The
            practical route was a cadence outside 1..365 inferred for an owner
            with no ``budget.pay_schedule`` row (plan findings **P8** /
            **P35**); plan step C4-b-2 made that owner unstorable and deleted
            the inference.*  Loud rather than defaulted, for the reason
            :func:`~app.services.pay_calendar.calendar_for` gives.
    """
    user = _validate_companion(companion_user_id)
    owner_id = user.data_owner_id
    calendar = calendar_for(owner_id)

    if period_id is None:
        # SAVED containment, not ``span_containing``: the filter below needs a
        # period a ``transactions.pay_period_id`` can point at, and a projected
        # span past the owner's horizon carries no id at all.
        #
        # The USER's civil day, never the process's (plan step C2-f2b).  This
        # read ``date.today()`` -- as ``get_current_period`` did before it --
        # while the page it feeds defaults its add-purchase form to
        # ``display_today()``, so on any process not pinned to
        # ``America/New_York`` the two disagreed for the last hours of every
        # day: the companion would be shown the NEW paycheck while the form
        # defaulted to a date inside the PREVIOUS one, and every purchase they
        # added landed out of period.  One clock for the whole render.  A
        # no-op in production, which pins ``TZ: America/New_York`` in both
        # compose files; live in CI, which runs ``TZ=Pacific/Kiritimati``
        # precisely to catch this shape.  Ledger row **P49** owns the same
        # correction at the reader's remaining call sites.
        period = calendar.period_containing(display_today())
        if period is None:
            raise NotFoundError("No current pay period found for owner.")
    else:
        period = calendar.period_by_id(period_id)
        if period is None:
            raise NotFoundError("Period not found.")

    period_rows = (
        db.session.query(Transaction)
        # Eager-load both the entries (for progress / pct totals) and
        # the template -- which the visibility filter below reads on every
        # row naming a definition, and which ``txn.template.name`` /
        # ``txn.tracks_purchases`` read again from the shared
        # ``render_row_card`` macro and ``grid_view_service.build_row_keys``
        # (mobile-first v3 plan Commit 13).  Without it each such row would
        # lazy-load its template individually, one SELECT per row.
        .options(
            selectinload(Transaction.entries),
            selectinload(Transaction.template),
        )
        .filter(
            Transaction.pay_period_id == period.period_id,
            # The owner's BASELINE scenario, and only it (plan step X-au-c2b,
            # after an adversarial review).  This filter was absent, so a
            # what-if scenario's rows appeared on the companion's screen beside
            # the real plan with nothing distinguishing them -- and the amount
            # model made that worse than cosmetic: the route prices these rows
            # through ONE basis, so a row from another scenario would be priced
            # by the baseline's salary profile and the baseline's loans once a
            # per-kind cutover declares its kind derived.  Scoping the QUERY is
            # the fix rather than a guard at the pricing door: the companion is
            # a read of the owner's plan, and the plan is one scenario.
            Transaction.scenario_id == require_baseline_scenario(owner_id).id,
            Transaction.is_deleted.is_(False),
        )
        # By name, and by id within a name: the cards render in this order,
        # and ``ORDER BY name`` alone left two rows of one definition in one
        # period (a carried leftover beside its canonical) to the query plan,
        # which the join's removal at X-bi-1b flipped on one production page.
        .order_by(Transaction.name, Transaction.id)
        .all()
    )
    # The ONE spelling of what a companion may see, asked of each row (plan
    # step balance:X-bi-1b).  The order is the query's: a filter keeps it.
    transactions = [txn for txn in period_rows if txn.visible_to_companion]

    # The two navigation searches, resolved HERE off the calendar this
    # function already derived rather than handed out for the route to search.
    # They were their own ``period_index +/- 1`` queries until plan step C2-f1
    # -- ``pay_period_service.get_next_period`` and this module's own
    # ``get_previous_period``, one rule written twice on the stored ordinal --
    # so stepping forward and back again was two answers nothing held equal.
    return CompanionPageRead(
        transactions=transactions,
        period=period,
        previous=calendar.period_starting_before(period.start_date),
        next_period=calendar.period_starting_after(period.start_date),
    )
