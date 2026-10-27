"""
Shekel Budget App - Authorization Helpers

Reusable functions for verifying resource ownership. Used by route
handlers to ensure the current user can only access their own data.

Pattern A (direct user_id): Use get_or_404() for models with a
user_id column (Account, TransactionTemplate, SavingsGoal, etc.).

Pattern B (indirect via parent): Use get_owned_via_parent() for
models scoped through a FK parent (SalaryRaise via SalaryProfile,
PaycheckLine via SalaryProfile).  It named Transaction via
PayPeriod as its first example until plan step ``pay_calendar:C13-b``;
``budget.transactions`` carries its own ``user_id`` now, so that row is
Pattern A and the example was pointing new code at the wrong one.

Pattern C (role-based): Use @require_owner on routes restricted
to the owner role. Companions receive 404 to avoid revealing
route existence.

Pattern D (re-auth recency): Use @fresh_login_required() on routes
that perform high-value operations (anchor balance changes,
companion management, tax-config edits, account hard-delete).
The decorator redirects to /reauth when the user's last password
verification is older than ``FRESH_LOGIN_MAX_AGE_MINUTES``.  See
audit finding F-045 / commit C-10.

Every ownership-failure branch in this module emits a structured
``log_event`` so probing patterns surface in dashboards and SOC
alerting (audit finding F-144 / commit C-14).  Successful loads stay
silent: the per-request summary in ``logging_config`` already covers
the audit-trail "who hit which path" question, and emitting a record
on every successful ``get_or_404`` would drown the failure signal in
volume.
"""

import logging
from datetime import datetime, timedelta, timezone
from functools import wraps

from flask import (
    abort, current_app, make_response, redirect, request,
    session as flask_session, url_for,
)
from flask_login import current_user

from app import ref_cache
from app.enums import RoleEnum
from app.extensions import db
from app.models.transaction import Transaction
from app.utils.hidden_row import HiddenRow
from app.utils.log_events import (
    ACCESS,
    EVT_ACCESS_DENIED_CROSS_USER,
    EVT_ACCESS_DENIED_OWNER_ONLY,
    EVT_RESOURCE_NOT_FOUND,
    log_event,
)
from app.utils.session_helpers import FRESH_LOGIN_AT_KEY

logger = logging.getLogger(__name__)


def _safe_user_id():
    """Return ``current_user.id`` if authenticated, else ``None``.

    The ownership helpers run after the application's login gate
    (``app/login_gate.py``, a ``before_request`` hook that refuses every
    anonymous request not declared public) so ``current_user`` is always
    authenticated, but the ownership readers below are also reachable in
    adversarial paths (an app built without the gate, ``LOGIN_DISABLED``
    set on a throwaway app, or test scaffolding that hits the helper
    directly).
    Returning ``None`` rather than raising on the
    ``AttributeError`` from an anonymous user keeps the audit log
    informative on every branch -- a missing ``user_id`` on a
    ``resource_not_found`` event signals "anonymous probe" rather than
    "the helper crashed".  :func:`require_owner` is not one of those
    readers: it reads the role outright and raises on such a principal
    before it logs anything (plan step ``bank_import:X-gs``).
    """
    return getattr(current_user, "id", None)


def require_owner(f):
    """Restrict a route to owner-role users only.

    Runs after the application's login gate (``app/login_gate.py``),
    which is what guarantees ``current_user`` is authenticated here: no
    view carries its own ``@login_required`` since plan step
    ``bank_import:X-gi-4`` (ruling **R-BI4**).
    Companions receive 404 (not 403) per the project security
    response rule: "404 for both 'not found' and 'not yours.'"

    The role is read off ``current_user`` outright, as the role checks in
    ``app/__init__`` and ``app/routes`` read it.  A principal with no
    ``role_id`` at all (Flask-Login's ``AnonymousUserMixin``) reaches a
    view only where the gate is off -- ``LOGIN_DISABLED``, an app built
    without ``register_login_gate``, or test scaffolding that calls the
    decorated view directly -- and raises ``AttributeError`` here rather
    than running the view: the gate is the authentication check, and this
    helper decides ROLE alone.  Until plan step ``bank_import:X-gs``
    (ledger row **BI-486**) a ``getattr`` defaulted that read to the
    OWNER's id "for test fixtures that do not set it", and the one
    principal the default could ever admit was an anonymous one: a
    ``User`` instance always HAS the attribute, because ``role_id`` is a
    mapped column -- unset it reads ``None``, which the comparison
    refuses, and persisted it reads the server default -- so no fixture
    ever reached the default, and the second layer opened on exactly the
    axis it existed for.  The one sibling that keeps a ``getattr`` on the
    role, :func:`get_accessible_transaction`, defaults to ``None`` and so
    fails closed.

    On the deny branch, emits ``access_denied_owner_only`` (ACCESS
    category, WARNING level) with the offending user's id, role id,
    and the request path.  Without this event a probing companion
    would leave no application-tier trace; the event is what F-144
    closed.

    Decorator order::

        @bp.route("/example")
        @require_owner       # the login gate has already run -- checks role
        def example():
            ...
    """
    @wraps(f)
    def decorated(*args, **kwargs):
        owner_id = ref_cache.role_id(RoleEnum.OWNER)
        actual_role_id = current_user.role_id
        if actual_role_id != owner_id:
            log_event(
                logger, logging.WARNING,
                EVT_ACCESS_DENIED_OWNER_ONLY, ACCESS,
                "Non-owner blocked from owner-only route",
                user_id=_safe_user_id(),
                role_id=actual_role_id,
                path=request.path,
                method=request.method,
            )
            abort(404)
        return f(*args, **kwargs)
    return decorated


def get_or_404(model, pk, user_id_field="user_id"):
    """Load a record by primary key and verify it belongs to the current user.

    Uses the model's direct ``user_id`` column (or a custom column
    name via *user_id_field*) to check ownership.

    Both denial branches emit a structured log event so cross-user
    probing leaves a forensic trail (audit finding F-144 / commit
    C-14):

      * Missing PK -> ``resource_not_found`` at INFO.  Common in
        normal use (a deleted resource the user navigates to) so the
        level is INFO, not WARNING.
      * Cross-user PK -> ``access_denied_cross_user`` at WARNING.
        This is the IDOR signal SOC tooling alerts on; the event
        records the requesting user's id and the owning user's id so
        an analyst can correlate probes against a target account.

    Args:
        model: The SQLAlchemy model class to query.
        pk: The primary key value to look up.
        user_id_field: The column name on the model that holds the
            owning user's ID.  Defaults to ``"user_id"``.

    Returns:
        The model instance if found and owned by ``current_user``,
        or ``None`` otherwise.

    Note:
        The caller is responsible for handling a ``None`` return --
        typically by returning a 404 response or redirecting.
    """
    record = db.session.get(model, pk)
    if record is None:
        log_event(
            logger, logging.INFO,
            EVT_RESOURCE_NOT_FOUND, ACCESS,
            "Ownership check on a non-existent primary key",
            user_id=_safe_user_id(),
            model=model.__name__,
            pk=pk,
            path=request.path,
        )
        return None
    owner_id = getattr(record, user_id_field, None)
    if owner_id != _safe_user_id():
        log_event(
            logger, logging.WARNING,
            EVT_ACCESS_DENIED_CROSS_USER, ACCESS,
            "Cross-user resource access blocked",
            user_id=_safe_user_id(),
            model=model.__name__,
            pk=pk,
            owner_id=owner_id,
            path=request.path,
        )
        return None
    return record



def require_owned_fk(model, data, key):
    """Abort 404 if a submitted FK in *data* names a row the user does not own.

    **The ONE spelling of "a form payload's foreign key must be the
    requester's own"**, extracted at plan step **salary:R14-b** when a third
    door needed it and pylint's ``duplicate-code`` gate said so.  Its three
    callers are the links between a user's own rows that a schema cannot
    check, because a schema sees an integer and not an owner:

    * ``routes/retirement.create_pension`` / ``update_pension`` -- which
      salary profile a pension's projected salary comes from.
    * ``routes/investment.update_params`` -- which salary profile FUNDS an
      account's employer contribution (**R-SAL5**), whose paychecks
      ``salary:R14-b`` then prices that contribution off.
    * ``routes/salary/items`` -- which account a payroll deduction FEEDS
      (ledger row **N-534**, ruling **R-SAL8**, closed at ``salary:R14-a``).
      **R-SAL8 is cited rather than the date it was ruled on**, which is that
      ruling's own point: the guard this replaced carried "a developer ruling
      of 2026-09-04" in its docstring, and a date is not a key anybody can
      look up.

    Each dropdown lists only the requester's own rows, so a foreign or
    non-existent id in the submission is a forged FK (IDOR).  Before the first
    of these guards the schema checked only that the value was a positive
    integer (``RowId``) and the database FK checked only that the row EXISTED,
    so one owner could point at another owner's row and read their salary --
    or their raise history -- into their own figures.

    :func:`get_or_404` verifies ownership and emits the cross-user denial
    audit event; its ``None`` result maps to a 404 per the security response
    rule (404 for both "not found" and "not yours").  An absent or ``None``
    id means the link is simply not set, which is a legal state at all three
    doors, so it passes through to the normal create/update path.

    Args:
        model: The SQLAlchemy model the id must name a row of.
        data: The schema-loaded form payload.
        key: The payload key holding the id.
    """
    row_id = data.get(key)
    if row_id is None:
        return
    if get_or_404(model, row_id) is None:
        abort(404)


def log_refused_lookup(model_name: str, pk) -> None:
    """Log a refused id lookup where the two denial states are indistinguishable.

    **The forensic half of an owner-scoped lookup** (pay-calendar plan step
    C2-f2d-3's fix pass).  :func:`get_or_404` reads the global table and
    compares the owner afterwards, so it can tell "no such row" from "not
    yours" and emits a different event for each.  A lookup against a value that
    holds ONE owner's data -- ``PayCalendar.period_by_id`` -- cannot: an id
    belonging to somebody else is simply absent, which is the stronger
    security property and is why those call sites moved.

    What it is NOT is a reason to stop logging.  Cross-user id probing left a
    trail through ``get_or_404`` and briefly left none through the calendar;
    this restores it, at the one severity the ambiguity supports.

    ``EVT_ACCESS_DENIED_CROSS_USER`` is deliberately NOT used: it asserts that
    the row exists and belongs to another user, which this caller cannot know,
    and an event that over-claims is worse for an investigation than one that
    says less.  The pk and the path are enough to correlate a probe.

    Args:
        model_name: What was being looked up, e.g. ``"PayPeriod"``.
        pk: The primary key the request supplied.
    """
    log_event(
        logger, logging.INFO,
        EVT_RESOURCE_NOT_FOUND, ACCESS,
        "Owner-scoped lookup found no such record for this user",
        user_id=_safe_user_id(),
        model=model_name,
        pk=pk,
        path=request.path,
    )


def get_owned_via_parent(model, pk, parent_attr,
                         parent_user_id_attr="user_id"):
    """Load a record by PK and verify ownership through its parent.

    For models that lack a direct ``user_id`` column but are scoped
    through a FK parent (SalaryRaise -> SalaryProfile), this function
    lazy-loads the parent and checks the parent's user_id.

    **Transaction is NOT one of them any more** (plan step
    ``pay_calendar:C13-b``).  It was this docstring's leading example, and
    ``budget.transactions`` has carried a ``user_id`` since ``C13-a`` -- so a
    door reaching for this helper with a ``Transaction`` would pay a
    relationship load to read a column.  Every live caller is a salary row.

    Mirrors :func:`get_or_404`'s logging contract: the missing-PK
    branch emits ``resource_not_found`` at INFO, the cross-user
    branch emits ``access_denied_cross_user`` at WARNING.  A missing
    parent attribute or missing parent ``user_id`` (e.g. orphaned
    relationship) also emits ``access_denied_cross_user`` because the
    failure mode is observationally identical to "this row belongs to
    someone else" from the requester's perspective.

    Args:
        model: The SQLAlchemy model class to query.
        pk: The primary key value to look up.
        parent_attr: The SQLAlchemy relationship attribute name on the
            model that points to the parent (e.g. ``"pay_period"``).
        parent_user_id_attr: The column name on the *parent* model
            that holds the owning user's ID.  Defaults to
            ``"user_id"``.

    Returns:
        The model instance if found and owned (via parent) by
        ``current_user``, or ``None`` otherwise.

    Examples::

        # SalaryRaise -> SalaryProfile.user_id
        get_owned_via_parent(SalaryRaise, raise_id, "salary_profile")

        # PaycheckLine -> SalaryProfile.user_id
        get_owned_via_parent(PaycheckLine, ded_id, "salary_profile")
    """
    record = db.session.get(model, pk)
    if record is None:
        log_event(
            logger, logging.INFO,
            EVT_RESOURCE_NOT_FOUND, ACCESS,
            "Ownership check on a non-existent primary key",
            user_id=_safe_user_id(),
            model=model.__name__,
            pk=pk,
            path=request.path,
        )
        return None
    parent = getattr(record, parent_attr, None)
    if parent is None:
        # Orphaned relationship or wrong parent_attr -- observationally
        # identical to "not yours" from the requester's perspective.
        # Emit the cross-user event so SOC tooling sees the probe;
        # owner_id is None to signal the orphaned-relationship case.
        log_event(
            logger, logging.WARNING,
            EVT_ACCESS_DENIED_CROSS_USER, ACCESS,
            "Cross-user resource access blocked (parent unloadable)",
            user_id=_safe_user_id(),
            model=model.__name__,
            pk=pk,
            parent_attr=parent_attr,
            owner_id=None,
            path=request.path,
        )
        return None
    parent_owner_id = getattr(parent, parent_user_id_attr, None)
    if parent_owner_id != _safe_user_id():
        log_event(
            logger, logging.WARNING,
            EVT_ACCESS_DENIED_CROSS_USER, ACCESS,
            "Cross-user resource access blocked",
            user_id=_safe_user_id(),
            model=model.__name__,
            pk=pk,
            parent_attr=parent_attr,
            owner_id=parent_owner_id,
            path=request.path,
        )
        return None
    return record


def is_not_found_to_transaction_doors(txn) -> bool:
    """Return whether a transaction door answers *txn* as "not found", logging why.

    **The ONE predicate behind both transaction ownership doors**,
    :func:`get_accessible_transaction` and
    ``routes/transactions/_helpers._get_owned_transaction``: each asks it once,
    after its own access check, so the two cannot drift on which rows they
    serve.  It refuses **a deleted row** (plan step ``credit_card:CC-5-4a-4``,
    ruling **R-CC89**, developer 2026-09-23: *"Every page and button treats a
    deleted row as not found: the stale tab's save and the stale Mark Credit
    get 'not found', exactly as for another user's row."*).  Measured by the
    step's third review: a stale second tab's popover on a deleted Paid
    $120.00 Hotel saved an Actual of $125.00 into a payment record under the
    hidden row, and a stale Mark Credit on a deleted $80.00 occurrence turned
    it Credit and created a live $80.00 card payback the owner could never
    trace.  No route serves a deleted row on purpose: a deleted row leaves
    the grid, and the un-archive that brings one back is a template door.
    What the page is TOLD at three doors is ruling **R-CC104**'s amendment
    (developer 2026-09-23): Mark Paid, the popover's Save and add purchase
    name the row -- "Hotel was deleted: ..." -- through
    :func:`get_accessible_transaction_or_deleted`, and say "was archived"
    where its recurring item is (ruling **R-CC107**); every other door still
    answers "not found".  It is a column read.

    It refused a second row until plan step ``balance:X-bi-6-5``: a transfer's
    SHADOW (leaf ``balance:X-bi-6-1``, ruling **R-BAL87**), the interval's
    fence while a transfer had twin rows, which went with the ``transfer_id``
    column that named one.

    Args:
        txn: A loaded :class:`Transaction` the requester may otherwise access.

    Returns:
        ``True`` when the door must answer 404.
    """
    if not txn.is_deleted:
        return False
    log_event(
        logger, logging.INFO,
        EVT_RESOURCE_NOT_FOUND, ACCESS,
        "Transaction door asked about a deleted row; a deleted row takes no "
        "money and leaves the grid",
        user_id=_safe_user_id(),
        model=Transaction.__name__,
        pk=txn.id,
        path=request.path,
    )
    return True


def get_accessible_transaction(txn_id):
    """Load a transaction the current user may access (owner or companion).

    The shared companion-aware access check behind the entry-CRUD
    (:mod:`app.routes.entries`) and status-change
    (:mod:`app.routes.transactions`) transaction routes.  Owners reach
    transactions they OWN; companions reach their linked owner's
    transactions restricted to companion-visible rows (a row whose
    DEFINITION is flagged ``companion_visible`` -- resolved by
    ``Transaction.visible_to_companion``, which answers ``False`` for a
    transfer shadow or a CC payback, ruling **R-BAL73**).

    **The owner is read off the ROW, and this door is where plan step
    ``pay_calendar:C13-b`` started.**  It was ``txn.pay_period.user_id`` -- a
    relationship walk issued to learn a fact the row itself did not carry --
    and it is the canonical route-boundary door, reached from eight route
    sites, so it was the largest single consumer of finding **P75**'s census.
    ``budget.transactions.user_id`` carries the same value since ``C13-a``,
    held equal to the paycheck's by ``fk_transactions_owner_period``, so the
    two cannot disagree and the walk bought a query rather than an answer.

    Follows the project security response rule: returns ``None`` for both
    "not found" and "not accessible" so the caller returns 404 in either
    case.

    **A DELETED row is "not found" here** (ruling **R-CC89**): a deleted row
    takes no money, and a stale page naming one is refused rather than
    admitted to a door that would write a payment or a card payback under a
    row no screen shows.  The rule is :func:`is_not_found_to_transaction_doors`,
    the one predicate this door and ``_get_owned_transaction`` share.  (A
    transfer's SHADOW row was "not found" too, leaf ``balance:X-bi-6-1``'s
    interval fence, until plan step ``balance:X-bi-6-5`` dropped the
    ``transfer_id`` column that named one.)

    Mirrors :func:`get_or_404`'s F-144 logging contract (deep-hunt #85):
    a missing PK emits ``resource_not_found`` at INFO; an ownership or
    companion-visibility denial emits ``access_denied_cross_user`` at
    WARNING.  The requesting user's id is read through
    :func:`_safe_user_id` and the role / data-owner reads use
    ``getattr`` fallbacks, so an anonymous or misordered-decorator call
    denies-and-logs (``user_id`` ``None`` => "anonymous probe") rather
    than raising ``AttributeError`` -- exactly like the sibling helpers.

    **Its body is :func:`get_accessible_transaction_or_deleted`**, and this is
    that door's answer for every caller that serves a live row alone: the row,
    or ``None`` for everything else, a deleted row included.  One walk, so the
    two cannot come to disagree about which rows they serve.

    Args:
        txn_id: Integer primary key of the transaction.

    Returns:
        The :class:`Transaction` if found and accessible, else ``None``.
    """
    answer = get_accessible_transaction_or_deleted(txn_id)
    return answer if isinstance(answer, Transaction) else None


def get_accessible_transaction_or_deleted(txn_id):
    """The transaction door, telling a deleted row the requester could reach by its NAME.

    :func:`get_accessible_transaction`'s access rules, logging and "not found"
    predicate exactly (its docstring states them); what differs is the answer
    for a row the predicate refuses because it is DELETED.  That row is a
    :class:`~app.utils.hidden_row.HiddenRow` rather than ``None``, so the
    three doors ruling **R-CC101** names -- Mark Paid, the popover's Save, add
    purchase -- can say "Hotel was deleted: ..." where the page acted on a row
    deleted minutes ago (plan step ``credit_card:CC-5-4a-4``, ruling
    **R-CC104**, developer 2026-09-23: *"Case (2) shows the same sentence in
    the same place. A recurring row is only hidden, so its name is known"*),
    or "Gym was archived: ..." where the row's recurring item is archived
    (ruling **R-CC107**).  It carries the row's name and whether its
    recurring item is archived, and no row, so a door holding one has no row
    to write money under.
    Every other refusal stays ``None``: a missing id, another user's row, a
    companion's hidden row, and a one-off row whose delete removed it from
    the table, which is a missing id by then.  That
    is what keeps the uniform 404 uniform: only a row the requester may
    already reach is ever named, and the rest share
    :data:`app.utils.error_fragments.ROW_NO_LONGER_EXISTS_MSG`.

    Args:
        txn_id: Integer primary key of the transaction.

    Returns:
        The :class:`Transaction` when found, accessible and served; a
        :class:`~app.utils.hidden_row.HiddenRow` when found and accessible
        but deleted; else ``None``.
    """
    txn = db.session.get(Transaction, txn_id)
    if txn is None:
        log_event(
            logger, logging.INFO,
            EVT_RESOURCE_NOT_FOUND, ACCESS,
            "Ownership check on a non-existent primary key",
            user_id=_safe_user_id(),
            model=Transaction.__name__,
            pk=txn_id,
            path=request.path,
        )
        return None
    requester_id = _safe_user_id()
    owner_id = txn.user_id
    # Whose data the requester acts on, read ONCE for both branches off the one
    # statement of that rule, :attr:`app.models.user.User.data_owner_id` (plan
    # step ``balance:X-bn``): an owner's own id, a companion's linked owner.
    # The ``getattr`` is for the anonymous principal alone, which has no such
    # attribute and reads ``None`` -- never a row's owner, since
    # ``transactions.user_id`` is NOT NULL -- so both branches refuse it.
    acts_on = getattr(current_user, "data_owner_id", None)
    companion_role_id = ref_cache.role_id(RoleEnum.COMPANION)
    if getattr(current_user, "role_id", None) == companion_role_id:
        # Companion path: linked owner's data + companion-visible
        # (resolved from the template, or the row's own flag for ad-hoc).
        if owner_id != acts_on or not txn.visible_to_companion:
            log_event(
                logger, logging.WARNING,
                EVT_ACCESS_DENIED_CROSS_USER, ACCESS,
                "Companion transaction access blocked",
                user_id=requester_id,
                model=Transaction.__name__,
                pk=txn_id,
                owner_id=owner_id,
                path=request.path,
            )
            return None
    else:
        # Owner path: the owner's own data.
        if owner_id != acts_on:
            log_event(
                logger, logging.WARNING,
                EVT_ACCESS_DENIED_CROSS_USER, ACCESS,
                "Cross-user transaction access blocked",
                user_id=requester_id,
                model=Transaction.__name__,
                pk=txn_id,
                owner_id=owner_id,
                path=request.path,
            )
            return None
    # AFTER both access branches, deliberately: a stranger's or a companion's
    # probe naming another owner's deleted row is an access denial first
    # (WARNING, the F-144 contract above) and "not found" second, exactly as
    # ``routes/transactions/_helpers._get_owned_transaction`` orders it --
    # which is also why a HiddenRow is only ever handed to a requester who
    # may reach the row.
    if is_not_found_to_transaction_doors(txn):
        return HiddenRow.of(txn)
    return txn


def _fresh_login_is_within(threshold: timedelta) -> bool:
    """Return True iff ``_fresh_login_at`` is within ``threshold``.

    Fail-closed for unparseable input, matching the pattern used by
    ``_idle_session_is_fresh`` (commit C-10):

      * Missing key -- the user has never re-authenticated on this
        session, or the cookie was constructed without the field.
        Reject so the only path to ``_fresh_login_at`` is via
        :func:`~app.utils.session_helpers.stamp_login_session` /
        :func:`~app.utils.session_helpers.stamp_reauth_session`.

      * Malformed (non-ISO-8601) value -- a tampered cookie that
        ``fromisoformat`` would otherwise raise on.

      * Naive timestamp -- would raise ``TypeError`` on the
        timezone-aware subtraction.

      * Age exceeds ``threshold`` -- the legitimate "step-up window
        expired" case the decorator exists to enforce.

    Future-dated timestamps (``elapsed < 0``) are treated as FRESH
    rather than rejected, mirroring ``_idle_session_is_fresh``: a
    backwards clock jump or NTP correction must not silently demote
    every active session into "needs reauth".  An attacker who
    forged a future-dated value would already need ``SECRET_KEY``,
    at which point the future-date check adds no defensive value.

    Args:
        threshold: Maximum allowed age, as a :class:`~datetime.timedelta`.

    Returns:
        True iff the session may proceed past ``fresh_login_required``;
        False if the user must be redirected to ``/reauth``.
    """
    raw = flask_session.get(FRESH_LOGIN_AT_KEY)
    if raw is None:
        return False
    try:
        fresh_at = datetime.fromisoformat(raw)
    except (TypeError, ValueError):
        return False
    if fresh_at.tzinfo is None:
        return False
    elapsed = datetime.now(timezone.utc) - fresh_at
    if elapsed < timedelta(0):
        # Future-dated fresh-login is from a backwards clock jump
        # (NTP correction, manual adjustment, VM resume).  Treat as
        # fresh -- see docstring for the threat-model rationale.
        return True
    return elapsed <= threshold


def fresh_login_required(max_age_minutes: int | None = None):
    """Require a recent re-authentication for high-value operations.

    Wraps a route handler and redirects to ``/reauth`` (with
    ``?next=`` set to the original URL) when the user's last
    password verification is older than ``max_age_minutes``.  HTMX
    requests receive a ``204`` (no body) with an ``HX-Redirect``
    header instead of a 302, because htmx swaps response bodies
    rather than following redirects -- a plain redirect would render
    the /reauth HTML inside whatever fragment slot the original
    request targeted.

    Decorator order::

        @bp.route("/example", methods=["POST"])
        @require_owner            # the login gate has already run; owner role
        @fresh_login_required()   # then: must be recently re-authed
        def example():
            ...

    The decorator MUST be called with parentheses
    (``@fresh_login_required()``), not as a bare reference, so the
    optional ``max_age_minutes`` keyword can be supplied.  Bare
    ``@fresh_login_required`` would pass the wrapped function into
    ``max_age_minutes`` and silently break.

    Args:
        max_age_minutes: Override for the default
            ``FRESH_LOGIN_MAX_AGE_MINUTES`` from app config.  Use
            ``None`` (the default) to inherit the config value at
            request time -- this is the right choice for almost every
            call site so that a single env-var bump propagates to
            every decorated route.  Pass an explicit integer only
            for routes that need a different window than the global
            default (e.g. a particularly destructive operation that
            should always require re-auth in the past minute).

    Returns:
        A decorator that, when applied to a Flask view function,
        returns the view's response if the freshness check passes
        and a /reauth redirect otherwise.

    Audit references: F-045 (commit C-10).
    """
    # Catch the @fresh_login_required (no-parens) misuse at import
    # time rather than letting it silently bind the view function as
    # ``max_age_minutes``.  Without this guard, a misused decorator
    # would pass a function into ``timedelta(minutes=...)`` on the
    # first request, surfacing as a confusing TypeError far from the
    # actual mistake.
    if callable(max_age_minutes):
        raise TypeError(
            "fresh_login_required must be called with parentheses: "
            "use @fresh_login_required() (or "
            "@fresh_login_required(max_age_minutes=N)), not bare "
            "@fresh_login_required."
        )

    def decorator(f):
        """Bind the freshness-checked wrapper to the view function."""
        @wraps(f)
        def wrapper(*args, **kwargs):
            """Verify _fresh_login_at then call the wrapped view."""
            configured = (
                max_age_minutes
                if max_age_minutes is not None
                else current_app.config["FRESH_LOGIN_MAX_AGE_MINUTES"]
            )
            threshold = timedelta(minutes=configured)
            if _fresh_login_is_within(threshold):
                return f(*args, **kwargs)
            # The user must re-authenticate.  Build a /reauth URL
            # carrying the current request URL as ``next`` so the
            # redirect after a successful re-auth lands them back on
            # the original action target.  ``request.url`` is the
            # full URL (including query string); the /reauth handler
            # validates it through the same ``_is_safe_redirect``
            # helper used by /login, so an attacker who somehow seeds
            # a malicious ``next`` (e.g. via an open redirect on a
            # third-party site that links into a decorated route)
            # cannot redirect the user off-origin after re-auth.
            reauth_url = url_for("auth.reauth", next=request.url)
            if request.headers.get("HX-Request"):
                # HTMX swaps response bodies; a 302 would render
                # /reauth's HTML into whatever fragment slot the
                # original request targeted.  Use ``HX-Redirect``
                # with a 204 (No Content) so htmx does a full-page
                # navigation instead.  Status 204 keeps the body
                # empty so no swap happens regardless of the
                # ``hx-target``/``hx-swap`` configuration.
                response = make_response("", 204)
                response.headers["HX-Redirect"] = reauth_url
                return response
            return redirect(reauth_url)
        return wrapper
    return decorator
