"""
Shekel Budget App -- Loan route package: escrow + rate-history management.

The HTMX partial routes that add a rate-history entry and add / remove escrow
lines.  Both escrow routes rebuild the escrow list through the shared
:func:`_render_escrow_list` tail (resolve today's active lines, recompute monthly
escrow + total payment, emit the OOB payment-summary swap), so that parallel
render lives in exactly one place.
"""

import logging
from datetime import date

from flask import flash, render_template, request
from flask_login import current_user
from sqlalchemy.exc import IntegrityError

from app.extensions import db
from app.models.escrow_line import EscrowComponentVersion, EscrowLine
from app.models.loan_features import (
    RATE_HISTORY_UNIQUE_CONSTRAINT,
    RateHistory,
)
from app.routes.loan._bp import loan_bp
from app.routes.loan._helpers import (
    _compute_total_payment,
    _escrow_merge_schema,
    _escrow_rename_schema,
    _escrow_schema,
    _escrow_version_schema,
    _forward_boundary,
    _load_loan_account,
    _loan_terms_now,
    _rate_schema,
    build_loan_band_chart,
)
from app.services import (
    escrow_calculator,
    loan_loaders,
)
from app.services.scenario_resolver import get_baseline_scenario
from app.utils.auth_helpers import require_owner
from app.utils.db_errors import is_unique_violation
from app.utils.money import ZERO

logger = logging.getLogger(__name__)


def _render_rate_history(account, params, band_chart=None):
    """Re-query and render the rate-history partial for a loan account.

    The shared reload + render used by both the duplicate-submit
    (IntegrityError) and the success paths of :func:`add_rate_change`, so
    the descending-effective-date ordering and the template kwargs live in
    exactly one place.

    Args:
        account: ORM :class:`Account` instance for the loan.
        params: ORM :class:`LoanParams` instance.
        band_chart: The freshly recomputed band-chart dict
            (:func:`._helpers.build_loan_band_chart`) on the SUCCESS path only,
            so the partial can emit the hidden refresh carrier ``loan_detail.js``
            reads to rebuild the now-visible balance band (a rate change
            re-amortizes the loan).  ``None`` on the duplicate-submit path, where
            nothing re-amortized -- the template then omits the carrier and the
            band (and any active payoff preview) is left untouched.

    Returns:
        The rendered ``loan/_rate_history.html`` partial.
    """
    rate_history = (
        db.session.query(RateHistory)
        .filter_by(account_id=account.id)
        .order_by(RateHistory.effective_date.desc())
        .all()
    )
    # DH-#56: the OOB swap shows the seam's current rate (the rate in effect
    # today after the just-committed rate history), not the retired
    # ``LoanParams.interest_rate`` column.  Read the seam figure here so the
    # swapped Overview "Interest Rate" reflects the new change.
    return render_template(
        "loan/_rate_history.html",
        account=account,
        params=params,
        rate_history=rate_history,
        current_rate=_loan_terms_now(account).current_rate,
        band_chart=band_chart,
        oob_swaps=True,
    )


@loan_bp.route("/accounts/<int:account_id>/loan/rate", methods=["POST"])
@require_owner
def add_rate_change(account_id):
    """Record a variable-rate change (HTMX)."""
    account, params, _account_type = _load_loan_account(account_id)
    if account is None or params is None:
        return "Account not found", 404

    errors = _rate_schema.validate(request.form)
    if errors:
        return "Please correct the highlighted errors and try again.", 400

    data = _rate_schema.load(request.form)

    # A rate change cannot predate origination: period 0 starts at
    # ``origination_date`` and the origination RateHistory row (DH-#56)
    # is the loan's base / period-0 rate.  A pre-origination row would
    # become the earliest entry, displacing the true origination row in
    # the dashboard's ``origination_rate`` derivation
    # (``rate_history[-1]``) and in ``_origination_rate``'s ``min()``.
    # Enforced in the route (not the schema) because the schema has no
    # access to the loan's origination date -- mirroring the
    # ``anchor_date >= origination_date`` guard in
    # :func:`true_up_balance`.  ``effective_date == origination_date``
    # itself collides with the seeded origination row and is rejected by
    # the existing same-date unique-constraint path below.
    if data["effective_date"] < params.origination_date:
        return (
            "Rate change effective date cannot be before the loan's "
            f"origination date ({params.origination_date.isoformat()}).",
            400,
        )

    # E-28 / HIGH-06 (Commit 24): the schema's ``@pre_load`` already
    # converted the form percent to the storage-domain fraction.

    entry = RateHistory(
        account_id=account.id,
        effective_date=data["effective_date"],
        interest_rate=data["interest_rate"],
        monthly_pi=data.get("monthly_pi"),
        notes=data.get("notes"),
    )
    db.session.add(entry)

    # DH-#56: the prior ``params.interest_rate = data["interest_rate"]``
    # mirror-write is gone.  The retired column drifted to the latest
    # rate on every change (corrupting the resolver's period-0/base rate
    # for a backdated or out-of-order change); RateHistory is now the
    # sole source of truth and the resolver derives the current rate from
    # it, so no scalar needs maintaining here.
    # A rate change moves the interest split of every confirmed post-anchor
    # payment, which the worked-out ledger reads as it stands.  The flush
    # surfaces a same-effective-date duplicate HERE, inside the one ``try``,
    # where it becomes the idempotent re-render below rather than a 500 at the
    # unguarded commit; a non-rate ``IntegrityError`` propagates (the correct
    # 500 disposition).  ``uq_rate_history_account_effective_date`` states a
    # real business rule: one rate per effective date, corrected by editing
    # the row.  *Until plan task plan#3076 the flush was the tail of the
    # stored ledger's re-sync helper
    # (``loan_posting_service.sync_all_scenarios_or_duplicate``), whose
    # translation this is.*
    try:
        db.session.flush()
    except IntegrityError as exc:
        db.session.rollback()
        if not is_unique_violation(exc, RATE_HISTORY_UNIQUE_CONSTRAINT):
            raise
        # Same-effective-date double-submit (F-104 / C-22): the composite
        # unique ``uq_rate_history_account_effective_date`` rejected the second
        # INSERT when the user clicked Save twice in a row.  Flash a clear
        # message and re-render the rate history without the proposed
        # duplicate.  A legitimate same-day correction is expressed by editing
        # the existing row, not by appending another.
        logger.info(
            "Duplicate rate-history entry prevented for account %d on %s",
            account.id, data["effective_date"],
        )
        flash(
            "A rate change with that effective date already exists. "
            "Edit the existing entry to correct it.",
            "warning",
        )
        return _render_rate_history(account, params)

    # A rate change re-amortizes the loan and moves the projected payoff; the
    # recurring payment's closing bound is derived from it on every read
    # (plan step R7d-g), so there is nothing to re-bound before committing.
    db.session.commit()
    logger.info("Recorded rate change for loan %d: %s", account.id, data["interest_rate"])
    # The re-amortization moves the whole balance trajectory, but this HTMX swap
    # replaces only the rate-history card (+ the OOB rate chip); the always-
    # visible balance band would otherwise show the pre-change line until a full
    # reload.  Recompute the band and hand it to the partial's refresh carrier so
    # ``loan_detail.js`` rebuilds ``#loan-balance-chart`` from it on this swap.
    return _render_rate_history(
        account, params, build_loan_band_chart(account, params),
    )


def _active_line_with_name(account_id, name):
    """Return the account's ACTIVE escrow line with ``name``, or None.

    Decision C (route-enforced active-name uniqueness): a line is active iff its
    latest version -- greatest ``effective_date`` -- is not a removal tombstone.
    That predicate depends on a line's child versions, so it cannot be a raw
    partial unique index; the ``ix_escrow_lines_account_name`` index serves this
    lookup.  A line whose latest version is a tombstone is removed, so its name
    is free to reuse under a new line.

    Args:
        account_id: The loan account to search.
        name: The candidate escrow line name.

    Returns:
        The matching active :class:`~app.models.escrow_line.EscrowLine`, or
        ``None`` when no active line carries that name.
    """
    for line in loan_loaders.load_escrow_lines(account_id):
        if line.name != name:
            continue
        latest = max(
            line.versions, key=lambda v: v.effective_date, default=None,
        )
        if latest is not None and not latest.is_removed:
            return line
    return None


def _baseline_scenario_id():
    """Return the current user's baseline scenario id, or ``None``.

    The scenario the loan's recorded payments live in -- the scope the
    forward-only guard resolves the latest settled payment against.  ``None`` when
    the user has no baseline scenario, in which case there are no recorded payments
    and the guard has no boundary.
    """
    scenario = get_baseline_scenario(current_user.id)
    return scenario.id if scenario else None


def _reject_effective_date(effective_date, params, boundary):
    """Return an actionable error message if an escrow effective date is out of bounds.

    Two bounds protect the ledger, mirroring the rate-history and tracking-start
    guards:

    * It cannot predate the loan's origination -- a version before the loan existed
      is meaningless (skipped when ``params`` is ``None``, an unconfigured loan).
    * It must fall STRICTLY AFTER ``boundary`` (the latest settled payment's DUE
      date, :func:`_forward_boundary` -- on or after the installment its split
      resolves escrow on, ruling D5 as ruling R-R104 amends it), or it could
      retroactively move an already-settled payment's escrow split and desync
      it from the cash frozen at settlement (spec Sec. 4.2).  For a payment
      due off the loan's day the bound is wider than it needs to be (finding
      REC-544), and the message names it as the payment's due date rather
      than an installment (ruling **R-R108**: the loan page names a payment's
      installment as the one it pays).

    Args:
        effective_date: The candidate version effective date.
        params: The loan's :class:`LoanParams`, or ``None`` when unconfigured.
        boundary: The forward-only boundary from :func:`_forward_boundary`.

    Returns:
        An actionable error string naming the violated boundary, or ``None`` when
        the date is allowed.
    """
    if params is not None and effective_date < params.origination_date:
        return (
            "An escrow effective date cannot be before the loan's origination "
            f"date ({params.origination_date.isoformat()})."
        )
    if boundary is not None and effective_date <= boundary:
        return (
            "An escrow change must take effect after your latest recorded payment "
            f"(due {boundary.strftime('%b %-d, %Y')})."
        )
    return None


def _add_version(line, effective_date, annual_amount, inflation_rate):
    """Add a version to an existing line, or return an error on a same-date collision.

    Rejects a second version on a date the line already carries -- the
    ``(line_id, effective_date)`` unique forbids it -- with a guide-to-edit message
    (mirroring the rate-history same-date rejection), since a same-date correction
    edits the existing version rather than appending a duplicate.  Otherwise stages
    the new version (the caller commits).

    Args:
        line: The :class:`~app.models.escrow_line.EscrowLine` to amend.
        effective_date: The new version's effective date.
        annual_amount: The new version's stored annual amount.
        inflation_rate: The new version's decimal-fraction inflation rate, or None.

    Returns:
        ``None`` on success, or an error string on a same-date collision.
    """
    if any(v.effective_date == effective_date for v in line.versions):
        return (
            f"This line already has a version effective "
            f"{effective_date.isoformat()}. Edit that version instead."
        )
    db.session.add(EscrowComponentVersion(
        line_id=line.id, effective_date=effective_date,
        annual_amount=annual_amount, inflation_rate=inflation_rate,
    ))
    return None


def _tombstone_line_today(line):
    """Mark an escrow line removed as of today (idempotent).

    Appends a removal tombstone version (``is_removed``, $0) at today's date, so
    the line resolves to 0 from today forward while its history stays intact --
    the supersession analogue of the old ``end_date`` stamp.  No-op when the line
    already resolves to inactive today (a repeat delete).  When a version already
    exists AT today (a same-day add or amount change), that version is converted
    to the tombstone in place rather than appending a second: the
    ``(line_id, effective_date)`` unique forbids two versions on one date, and a
    line added and removed the same day never contributed (matching the legacy
    zero-length range).

    Args:
        line: The :class:`~app.models.escrow_line.EscrowLine` to remove.
    """
    if not escrow_calculator.resolve_active_lines([line], date.today()):
        return  # already inactive as of today
    today_version = next(
        (v for v in line.versions if v.effective_date == date.today()), None,
    )
    if today_version is not None:
        today_version.is_removed = True
        today_version.annual_amount = ZERO
        return
    db.session.add(EscrowComponentVersion(
        line_id=line.id, effective_date=date.today(),
        annual_amount=ZERO, is_removed=True,
    ))


def _render_escrow_list(account, params):
    """Reload the escrow lines and render the card partial with the OOB tail.

    The shared reload every escrow mutation ends on: load the account's lines,
    resolve them to today's active set for the monthly-escrow badge + total
    payment, build the version-drawer card model
    (:func:`~app.services.escrow_calculator.build_escrow_card`, keyed by the
    forward-only boundary so each version row's edit / delete controls reflect the
    guard), and emit the out-of-band payment-summary swap -- so that render logic
    lives in one place and the swapped card is byte-identical to the inline one.

    Args:
        account: ORM :class:`~app.models.account.Account` for the loan.
        params: ORM :class:`~app.models.loan_params.LoanParams`, or None.

    Returns:
        The rendered ``loan/_escrow_list.html`` partial.
    """
    today = date.today()
    boundary = _forward_boundary(account.id, _baseline_scenario_id())
    escrow_lines = loan_loaders.load_escrow_lines(account.id)
    resolved = escrow_calculator.resolve_active_lines(escrow_lines, today)
    monthly_escrow = escrow_calculator.calculate_monthly_escrow(resolved)
    total_payment = _compute_total_payment(account, params, resolved)
    return render_template(
        "loan/_escrow_list.html",
        account=account,
        escrow_components=escrow_calculator.build_escrow_card(
            escrow_lines, today, boundary,
        ),
        merge_candidates=escrow_calculator.build_merge_candidates(escrow_lines),
        monthly_escrow=monthly_escrow,
        total_payment=total_payment,
        today_iso=today.isoformat(),
        oob_swaps=True,
    )


@loan_bp.route("/accounts/<int:account_id>/loan/escrow", methods=["POST"])
@require_owner
def add_escrow(account_id):
    """Add an escrow line (HTMX): a new line plus its opening dated version."""
    account, params, _account_type = _load_loan_account(account_id)
    if account is None:
        return "Account not found", 404

    errors = _escrow_schema.validate(request.form)
    if errors:
        return "Please correct the highlighted errors and try again.", 400

    data = _escrow_schema.load(request.form)

    # E-28 / HIGH-06 (Commit 24): the schema's ``@pre_load`` converted the form
    # percent to the storage-domain fraction before validation, so
    # ``data["inflation_rate"]`` is stored verbatim.

    # Reject a duplicate ACTIVE line name (decision C); a removed line's name is
    # reusable, so only an active collision blocks the add.
    if _active_line_with_name(account.id, data["name"]) is not None:
        return "An escrow component with that name already exists.", 400

    # Effective date defaults to today (the common case unchanged); a supplied
    # date schedules the opening version forward.  A back-dated opening would let
    # the new line contribute escrow to an already-settled payment (its split
    # resolves escrow on each payment's DUE date), so the forward-only guard
    # applies to a NEW line too, not only to an amend.
    effective_date = data.get("effective_date") or date.today()
    boundary = _forward_boundary(account.id, _baseline_scenario_id())
    guard_error = _reject_effective_date(effective_date, params, boundary)
    if guard_error is not None:
        return guard_error, 400

    line = EscrowLine(account_id=account.id, name=data["name"])
    db.session.add(line)
    db.session.flush()  # assign line.id for the version FK
    db.session.add(EscrowComponentVersion(
        line_id=line.id,
        effective_date=effective_date,
        annual_amount=data["annual_amount"],
        inflation_rate=data.get("inflation_rate"),
    ))
    db.session.commit()

    logger.info("Added escrow line '%s' to loan %d", data["name"], account.id)
    return _render_escrow_list(account, params)


@loan_bp.route(
    "/accounts/<int:account_id>/loan/escrow/<int:line_id>/delete",
    methods=["POST"],
)
@require_owner
def delete_escrow(account_id, line_id):
    """Remove an escrow line (HTMX): append a removal tombstone as of today.

    The tombstone's effective date is today, so it too must clear the forward-only
    boundary: when an EARLY-settled payment (settled before its pay period begins)
    puts the boundary on/after today, a removal-as-of-today would zero the line for
    that already-settled payment and desync its split from the frozen cash.  Guard
    on today via the shared :func:`_reject_effective_date`; the operator can
    schedule the removal after the boundary once it passes.
    """
    account, params, _account_type = _load_loan_account(account_id)
    if account is None:
        return "Account not found", 404

    line = _owned_line(account, line_id)
    if line is None:
        return "Component not found", 404

    boundary = _forward_boundary(account.id, _baseline_scenario_id())
    guard_error = _reject_effective_date(date.today(), params, boundary)
    if guard_error is not None:
        return guard_error, 400

    _tombstone_line_today(line)
    db.session.commit()
    logger.info("Removed escrow line %d from loan %d", line_id, account.id)
    return _render_escrow_list(account, params)


def _owned_line(account, line_id):
    """Return the account's escrow line ``line_id``, or None (404 for not-yours).

    The shared ownership lookup for the per-line escrow routes: 404 for both a
    missing line and another user's line (no existence oracle), matching the
    security response rule.
    """
    line = db.session.get(EscrowLine, line_id)
    if line is None or line.account_id != account.id:
        return None
    return line


def _owned_version(account, version_id):
    """Return the account's escrow version ``version_id``, or None (404 for not-yours).

    Resolves ownership through the version's parent line
    (``version.line.account_id``); 404 for both a missing version and another
    user's, as with :func:`_owned_line`.
    """
    version = db.session.get(EscrowComponentVersion, version_id)
    if version is None or version.line.account_id != account.id:
        return None
    return version


@loan_bp.route(
    "/accounts/<int:account_id>/loan/escrow/<int:line_id>/version",
    methods=["POST"],
)
@require_owner
def add_escrow_version(account_id, line_id):
    """Schedule a change to an existing escrow line (HTMX): add a dated version.

    The per-line "schedule a change" action: append a new effective-dated version
    under the line so a future analysis letter ("$X effective Jan 1") is recorded
    now, without waiting for the date to arrive.  The forward-only guard keeps the
    date strictly after the latest settled payment (so it cannot move a settled
    split), and a same-date collision is guided to the edit action.
    """
    account, params, _account_type = _load_loan_account(account_id)
    if account is None:
        return "Account not found", 404
    line = _owned_line(account, line_id)
    if line is None:
        return "Component not found", 404

    errors = _escrow_version_schema.validate(request.form)
    if errors:
        return "Please correct the highlighted errors and try again.", 400
    data = _escrow_version_schema.load(request.form)

    effective_date = data.get("effective_date") or date.today()
    boundary = _forward_boundary(account.id, _baseline_scenario_id())
    guard_error = _reject_effective_date(effective_date, params, boundary)
    if guard_error is not None:
        return guard_error, 400

    collision = _add_version(
        line, effective_date, data["annual_amount"], data.get("inflation_rate"),
    )
    if collision is not None:
        return collision, 400
    db.session.commit()
    logger.info(
        "Scheduled escrow change on line %d (loan %d) effective %s",
        line_id, account.id, effective_date.isoformat(),
    )
    return _render_escrow_list(account, params)


def _reject_version_edit(version, params, boundary, new_date):
    """Return an error message if an escrow-version edit is disallowed, else None.

    Collects the edit guards so the route stays a flat guard-clause chain:

    * a removal tombstone is not amount-editable (delete it and reschedule);
    * a version whose CURRENT date is at or before ``boundary`` underpins a settled
      split and is read-only (correct a wrong past figure with a loan true-up, spec
      Sec. 4.3);
    * the NEW date must clear both effective-date bounds
      (:func:`_reject_effective_date`);
    * moving onto a date another version already holds is a same-date collision.

    Args:
        version: The :class:`~app.models.escrow_line.EscrowComponentVersion`.
        params: The loan's :class:`LoanParams`, or ``None``.
        boundary: The forward-only boundary (:func:`_forward_boundary`).
        new_date: The proposed new effective date.

    Returns:
        An error string, or ``None`` when the edit is allowed.
    """
    if version.is_removed:
        return "A scheduled removal can't be edited; delete it and reschedule."
    if boundary is not None and version.effective_date <= boundary:
        return (
            "This escrow version affects a settled payment and can't be edited. "
            "Schedule a new change instead."
        )
    date_error = _reject_effective_date(new_date, params, boundary)
    if date_error is not None:
        return date_error
    if new_date != version.effective_date and any(
        v.effective_date == new_date and v.id != version.id
        for v in version.line.versions
    ):
        return (
            f"This line already has a version effective {new_date.isoformat()}. "
            "Edit that version instead."
        )
    return None


@loan_bp.route(
    "/accounts/<int:account_id>/loan/escrow/version/<int:version_id>/edit",
    methods=["POST"],
)
@require_owner
def edit_escrow_version(account_id, version_id):
    """Edit a not-yet-settled escrow version (HTMX): amount / effective date / inflation.

    Only a version STRICTLY AFTER the latest settled payment is editable; the edit
    guards live in :func:`_reject_version_edit`.  On success the version's amount,
    effective date, and inflation are updated in place (a same-date correction, not
    a new version), and the escrow card re-renders.
    """
    account, params, _account_type = _load_loan_account(account_id)
    if account is None:
        return "Account not found", 404
    version = _owned_version(account, version_id)
    if version is None:
        return "Component not found", 404

    errors = _escrow_version_schema.validate(request.form)
    if errors:
        return "Please correct the highlighted errors and try again.", 400
    data = _escrow_version_schema.load(request.form)
    new_date = data.get("effective_date") or version.effective_date
    boundary = _forward_boundary(account.id, _baseline_scenario_id())

    reject = _reject_version_edit(version, params, boundary, new_date)
    if reject is not None:
        return reject, 400

    version.effective_date = new_date
    version.annual_amount = data["annual_amount"]
    version.inflation_rate = data.get("inflation_rate")
    db.session.commit()
    logger.info(
        "Edited escrow version %d (loan %d) -> %s effective %s",
        version_id, account.id, data["annual_amount"], new_date.isoformat(),
    )
    return _render_escrow_list(account, params)


@loan_bp.route(
    "/accounts/<int:account_id>/loan/escrow/version/<int:version_id>/delete",
    methods=["POST"],
)
@require_owner
def delete_escrow_version(account_id, version_id):
    """Delete a SCHEDULED escrow version (HTMX): undo a queued future change.

    Hard-deletes a single scheduled (future-dated) version so a mis-entered change
    (wrong amount or date) can be undone.  Two guards, both enforced server-side
    (the hidden delete button is only an affordance):

    * The version must be STRICTLY AFTER the forward-only boundary (the latest
      settled payment's DUE date).  This is NOT implied by "after today": a
      payment settled before its installment falls due -- the ordinary case for
      the days between settling and the due date, and the whole pay period ahead
      for a paid-ahead one -- puts the boundary in the FUTURE, so a version in
      the ``today < date <= boundary`` gap is at/before a settled payment's
      installment and deleting it would move that settled payment's escrow split
      off the cash frozen at settlement.
    * It must be a scheduled (future) change -- a current / past amount is
      corrected by editing it, and a whole line is removed via the line's Remove.

    If removing it leaves the line with no versions (an upcoming-only line whose
    sole scheduled version this was), the now-empty line is dropped so it does not
    linger invisibly.
    """
    account, params, _account_type = _load_loan_account(account_id)
    if account is None:
        return "Account not found", 404
    version = _owned_version(account, version_id)
    if version is None:
        return "Component not found", 404

    boundary = _forward_boundary(account.id, _baseline_scenario_id())
    if boundary is not None and version.effective_date <= boundary:
        return (
            "This escrow version affects a settled payment and can't be deleted."
        ), 400
    if version.effective_date <= date.today():
        return (
            "Only a scheduled future change can be deleted. Edit a current amount, "
            "or remove the whole line."
        ), 400

    line = version.line
    db.session.delete(version)
    db.session.flush()
    remaining = (
        db.session.query(EscrowComponentVersion)
        .filter_by(line_id=line.id).count()
    )
    if remaining == 0:
        db.session.delete(line)
    db.session.commit()
    logger.info("Deleted scheduled escrow version %d from loan %d", version_id, account.id)
    return _render_escrow_list(account, params)


@loan_bp.route(
    "/accounts/<int:account_id>/loan/escrow/<int:line_id>/rename",
    methods=["POST"],
)
@require_owner
def rename_escrow_line(account_id, line_id):
    """Rename an escrow line's display label in place (HTMX).

    Display-only: the label is edited on the line, which provably cannot move a
    cent of any posted split (the split reads a version's amount + date, never the
    name).  A rename onto another ACTIVE line's name is rejected (decision C) -- the
    same active-name uniqueness the add enforces; renaming a line to its own current
    name is a no-op and allowed.
    """
    account, params, _account_type = _load_loan_account(account_id)
    if account is None:
        return "Account not found", 404
    line = _owned_line(account, line_id)
    if line is None:
        return "Component not found", 404

    errors = _escrow_rename_schema.validate(request.form)
    if errors:
        return "Please correct the highlighted errors and try again.", 400
    data = _escrow_rename_schema.load(request.form)

    existing = _active_line_with_name(account.id, data["name"])
    if existing is not None and existing.id != line.id:
        return "An escrow component with that name already exists.", 400

    line.name = data["name"]
    db.session.commit()
    logger.info(
        "Renamed escrow line %d (loan %d) to '%s'", line_id, account.id, data["name"],
    )
    return _render_escrow_list(account, params)


def _resolve_merge_source(account, target):
    """Validate the merge form and resolve the source line, or return an error.

    Consolidates the merge route's input guards so it stays a flat guard-clause
    chain: schema validation, source-line ownership (404 for a missing / another
    user's line, per the security-response rule), and the self-merge rejection.

    Args:
        account: The owning :class:`~app.models.account.Account`.
        target: The surviving :class:`~app.models.escrow_line.EscrowLine` (the
            route's URL line), so a merge into itself can be rejected.

    Returns:
        ``(source_line, None)`` on success, or ``(None, (message, status))`` when a
        guard fails.
    """
    errors = _escrow_merge_schema.validate(request.form)
    if errors:
        return None, ("Please correct the highlighted errors and try again.", 400)
    data = _escrow_merge_schema.load(request.form)
    source = _owned_line(account, data["source_line_id"])
    if source is None:
        return None, ("Component not found", 404)
    if source.id == target.id:
        return None, ("A line can't be merged into itself.", 400)
    return source, None


@loan_bp.route(
    "/accounts/<int:account_id>/loan/escrow/<int:line_id>/merge",
    methods=["POST"],
)
@require_owner
def merge_escrow_line(account_id, line_id):
    """Merge another escrow line's history INTO this one (HTMX).

    Reunifies a line whose history split across two lines -- a legacy backfill (one
    line per historical name) or a Remove+Add: the drawer's line (``line_id``) is
    the surviving TARGET, and the posted ``source_line_id`` line is folded in and
    deleted.  Ownership of BOTH lines is checked (404 for a missing or another
    user's line).  The merge is allowed only when it preserves the escrow resolved
    on every date (:func:`~app.services.escrow_calculator.plan_escrow_line_merge`),
    so it can neither move a settled split nor drop a concurrent charge; it is
    rejected otherwise with an actionable message.  A merge moves no split (escrow
    per date is byte-identical, and the split stores the escrow amount, never a line
    id).  *Every escrow write ended on the stored ledger's posting sync
    (``_commit_escrow_change``, E1b) until plan task plan#3076, a no-op for a
    merge; each now commits.*
    """
    account, params, _account_type = _load_loan_account(account_id)
    if account is None:
        return "Account not found", 404
    target = _owned_line(account, line_id)
    if target is None:
        return "Component not found", 404

    source, error = _resolve_merge_source(account, target)
    if error is not None:
        return error
    plan = escrow_calculator.plan_escrow_line_merge(source, target)
    if plan.error is not None:
        return plan.error, 400

    # Repoint the surviving source versions onto the target and flush so their new
    # line_id persists BEFORE the source line is deleted; deleting the source then
    # cascade-removes exactly the versions the target already covers on the same
    # date (``plan.versions_to_drop``), leaving escrow-per-date unchanged.
    for version in plan.versions_to_move:
        version.line = target
    db.session.flush()
    db.session.delete(source)
    db.session.commit()
    logger.info(
        "Merged escrow line %d into %d (loan %d)", source.id, target.id, account.id,
    )
    return _render_escrow_list(account, params)
