"""
Shekel Budget App -- Loan route package: parameter management.

Initial loan-parameter creation (which records the balance stated at setup
as the ``tracking_start`` :class:`LoanAnchorEvent` it is, plan step
``recurrence:R20``), parameter updates, and the dated balance true-up and
tracking-start doors (each an append-only :class:`LoanAnchorEvent`; the
origination event write is retired -- the origination anchor is synthesized
from the immutable :class:`LoanParams`).  All are redirect-style POST
handlers that flash and return to the dashboard.
"""

import logging
from datetime import date, timedelta
from decimal import Decimal

from flask import abort, flash, redirect, request, url_for
from flask_login import current_user

from app import ref_cache
from app.enums import InterestAccrualConventionEnum
from app.exceptions import TrackingStartRefused
from app.extensions import db
from app.models.account import Account
from app.models.loan_features import RateHistory
from app.models.loan_params import LoanParams
from app.models.ref import AccountType
from app.routes._standing_payment import sync_loan_payment_start_or_refuse
from app.routes._redirect_target import RedirectTarget
from app.routes.loan._bp import loan_bp
from app.routes.loan._helpers import (
    _PARAM_FIELDS,
    _create_schema,
    _require_configured_loan,
    _trueup_schema,
    _update_schema,
    render_loan_setup,
)
from app.services import loan_anchor_service
from app.services.anchor_service import AnchorTrueUpOutcome
from app.utils.account_validation import (
    INVALID_COLLATERAL_LINK,
    _validate_collateral_link,
)
from app.utils.auth_helpers import get_or_404, require_owner
from app.utils.dates import display_today
from app.utils.digit_strings import parse_row_id

logger = logging.getLogger(__name__)


@loan_bp.route("/accounts/<int:account_id>/loan/setup", methods=["POST"])
@require_owner
def create_params(account_id):
    """Create initial loan parameters, recording the stated balance as an assertion.

    The loan's genesis, in ONE transaction: the :class:`LoanParams` row, the
    origination :class:`RateHistory` row, the ``tracking_start``
    :class:`LoanAnchorEvent` for the balance the owner states (when the loan
    originated before the date it is stated for), and the standing payment's
    start brought onto the contract -- then the single commit.  (The genesis
    posting ledger was reconciled over all of it too, until plan task plan#3076
    deleted the stored ledger's syncs; the worked-out ledger reads the loan's
    terms and statements as they stand.)  A refusal anywhere ahead of the
    commit rolls the whole write back.

    **The balance stated at setup IS an assertion and is recorded as one**
    (plan step ``recurrence:R20``, ruling **R-R72** part 3, finding
    **REC-519**).  The form asks for the balance "as of" a date the owner
    picks (the setup date by default) and this door appends a
    ``tracking_start`` for it through
    :func:`app.services.loan_anchor_service.stage_loan_tracking_start` -- the same
    row the dashboard's tracking-start door writes, so a loan configured
    mid-life reads its stated balance from that day: the contract's calendar
    charges every month from origination (plan step recurrence:R16-c-2), and
    that statement clears every month before it (rulings R-R71, R-R72).  Until R20 the
    form REQUIRED that balance and stored it in ``LoanParams.current_principal``,
    which nothing read: the loan's only assertion was its synthesized
    origination, and every unrecorded month since read as unpaid.  The date
    is bounded ``[origination_date, today]``.  The future half is the
    schema's; the origination half is refused HERE, the way the two dashboard
    doors refuse a pre-origination date, and it applies only to a loan that
    HAS originated: for one originating after today no date on or before
    today could satisfy it, and there is no balance to assert yet.  A loan
    originating ON the stated date asserts nothing either -- its origination
    IS the assertion (``original_principal`` on ``origination_date``), and a
    second row saying so would be the synthesized opening's twin.

    **For a loan that originated before the stated day, a stated day on or
    after the day a payment into the account moved money is refused whole**
    (ruling **R-R115**, "Same rule at setup": the dashboard door's R-R114 at
    this door, and R-BAL155's day): payments can be recorded into an account
    before its loan is set up, and the form re-renders as typed, asking for a
    day after the origination and before the payment's, or for the
    origination day itself where no such day exists
    (:func:`_stage_stated_balance_or_refuse`).  That
    refusal rolls the write back and THEN re-renders, as the schedule doors
    do (``routes/pay_periods.py``).
    """
    account = get_or_404(Account, account_id)
    if account is None:
        abort(404)

    account_type = db.session.get(AccountType, account.account_type_id)
    if account_type is None or not account_type.has_amortization:
        flash("This account type does not support loan parameters.", "warning")
        return redirect(url_for("savings.dashboard"))

    # Check if params already exist.
    existing = db.session.query(LoanParams).filter_by(account_id=account.id).first()
    if existing:
        flash("Loan parameters already configured.", "info")
        return redirect(url_for("loan.dashboard", account_id=account_id))

    errors = _create_schema.validate(request.form)
    if errors:
        flash("Please correct the highlighted errors and try again.", "danger")
        return render_loan_setup(account, account_type, request.form)

    data = _create_schema.load(request.form)

    refusal = _setup_refusal(data, account_type)
    if refusal is not None:
        flash(refusal, "danger")
        return render_loan_setup(account, account_type, request.form)

    # The stated balance and its date are the assertion's, not the params'
    # (plan step R20): pop them before constructing LoanParams.
    anchor_balance = Decimal(str(data.pop("anchor_balance")))
    anchor_date = data.pop("anchor_date")

    # DH-#56: ``interest_rate`` is no longer a LoanParams column -- it
    # seeds the loan's origination RateHistory row (the resolver's
    # base / period-0 rate).  Pop it before constructing LoanParams.
    # The schema's ``@pre_load`` (E-28 / HIGH-06) already divided the
    # form percent by 100, so the value is the storage-domain fraction
    # the ``rate_history.interest_rate`` CHECK (0..1) accepts.
    origination_rate = data.pop("interest_rate")

    # How the note accrues interest (plan step recurrence:R16-d).  The list
    # holds one convention, ``monthly_1_12``, until a member's first consumer
    # adds the next (ruling plan#3354), so the door states it rather than the
    # form; the field that lets the owner choose arrives with plan#3355.  An
    # FK id is not a static literal, so it is stated here, never defaulted.
    params = LoanParams(
        account_id=account.id,
        interest_accrual_convention_id=ref_cache.interest_accrual_convention_id(
            InterestAccrualConventionEnum.MONTHLY_1_12,
        ),
        **data,
    )
    db.session.add(params)
    db.session.flush()

    # Origination RateHistory row (DH-#56): every loan carries a rate
    # row effective at origination so the resolver derives its
    # period-0 / base rate from RateHistory rather than a stored scalar.
    # ``monthly_pi=None`` lets the rate-period engine derive the
    # origination P&I from the original principal and term (exact for an
    # on-schedule loan).
    db.session.add(RateHistory(
        account_id=account.id,
        effective_date=params.origination_date,
        interest_rate=origination_rate,
        monthly_pi=None,
    ))

    # NO origination LoanAnchorEvent is written (the read switch's final
    # commit retired it): the origination anchor is a verbatim copy of the
    # immutable LoanParams fields, so every consumer -- the loan walk
    # (``loan_ledger.walk_loan_ledger``) and the resolver's latest-assertion
    # start of a view-less contract line -- SYNTHESIZES it from the params via
    # ``loan_loaders.load_loan_anchor_facts``.  The balance the
    # owner states at setup is a different fact with no other home (plan
    # step R20): a ``tracking_start`` assertion, staged in this same
    # transaction whenever the loan originated BEFORE the day it is stated
    # for, and refused as the dashboard's door refuses one (ruling R-R115).
    # Its row is constructed by the anchor service, the one place a loan
    # anchor is written; the commit below is the door's, as it was.
    refused = _stage_stated_balance_or_refuse(
        account, account_type, params, anchor_balance, anchor_date,
    )
    if refused is None:
        # The standing-payment helper below is called once the door's write
        # is FLUSHED (its contract), so the origination rate and the stated
        # balance land here, outside its refusal's catch.  The stored ledger's
        # genesis sync stood here and flushed them until plan task plan#3076.
        db.session.flush()
        # A recurring transfer that already pays into this account is the
        # loan's standing payment from this moment, and its start is the
        # contract's (ruling **R-R81**; plan step R7d-g).  Until that step the
        # next chokepoint of any kind healed it; this door is where it becomes
        # one, through the entry helper every such door calls (ruling
        # **R-R85**).
        refused = sync_loan_payment_start_or_refuse(
            account.id,
            redirect=RedirectTarget("loan.dashboard", {"account_id": account.id}),
        )
    if refused is not None:
        return refused
    db.session.commit()

    logger.info("Created loan params for account %d", account.id)
    flash("Loan parameters configured.", "success")
    return redirect(url_for("loan.dashboard", account_id=account_id))


def _stage_stated_balance_or_refuse(
    account: Account,
    account_type: AccountType,
    params: LoanParams,
    anchor_balance: Decimal,
    anchor_date: date,
) -> str | None:
    """Stage the balance stated at setup as a tracking start, or refuse the setup whole.

    The loan is asserted from the stated day only when it originated BEFORE
    that day (plan step ``recurrence:R20``); a loan originating on or after it
    asserts nothing, since its origination IS the assertion.  The row is the
    anchor service's to construct
    (:func:`app.services.loan_anchor_service.stage_loan_tracking_start`), and
    so is the refusal (ruling **R-R115**): a stated day on or after the day a
    payment into the account moved money would start the loan's record after
    a payment it holds (rulings R-R114, R-BAL155).  The whole write is rolled
    back, the params and their rate row included, and the form re-renders
    asking for a day AFTER the origination and before the payment's -- both
    bounds, because the origination day satisfies "before" alone and this
    door records nothing on it.

    **Where no day falls strictly between the origination and that payment's
    day, no stated day both follows the origination and precedes the
    payment** -- the payment moved money on the origination day, on the day
    after it, or before it -- so the sentence asks for the origination day
    itself, which this door accepts and which asserts nothing beyond the
    loan's original amount, and names Record balance for the correction once
    the loan is set up.  (Asking for "a date before" the payment's day there
    would send the owner to the origination day without saying that the
    stated balance is then dropped.)  The dashboard's door has no such gap:
    it records a tracking start ON the origination day, so any payment day
    after the origination leaves a date it accepts.

    Args:
        account: The loan :class:`Account` being configured.
        account_type: Its :class:`AccountType` row, for the re-render.
        params: The flushed :class:`LoanParams` row.
        anchor_balance: The stated balance, a :class:`Decimal`.
        anchor_date: The day it is stated for.

    Returns:
        ``None`` when the balance was staged or needed no staging; else the
        re-rendered setup page, with the write rolled back and the refusal
        flashed.
    """
    if params.origination_date >= anchor_date:
        return None
    try:
        loan_anchor_service.stage_loan_tracking_start(
            account=account,
            anchor_balance=anchor_balance,
            anchor_date=anchor_date,
        )
    except TrackingStartRefused as refused:
        moved_on = refused.moved_on.strftime("%b %-d, %Y")
        origination = params.origination_date.strftime("%b %-d, %Y")
        # Both bounds are named: the origination day satisfies "before" alone,
        # and this door records nothing on it.
        remedy = (
            f"Enter the balance as of a date after {origination} and before "
            f"{moved_on}."
            if refused.moved_on > params.origination_date + timedelta(days=1)
            else
            f"No date after the loan's origination ({origination}) comes "
            f"before that payment: enter {origination} as the date, which "
            f"states only the loan's original amount, then correct the "
            f"balance with Record balance on the loan's page."
        )
        # The sentence is composed before the rollback expires the rows it
        # reads.
        db.session.rollback()
        flash(f"{refused}  {remedy}", "danger")
        return render_loan_setup(account, account_type, request.form)
    return None


def _setup_refusal(data, account_type):
    """Return the sentence refusing a schema-valid setup submission, or ``None``.

    The two rules the schema cannot state because each reads something beyond
    the fields themselves:

    * the account TYPE's term cap (``ref.account_types.max_term_months``; the
      schema's universal 600 is the outer bound);
    * the ORIGINATION half of the stated balance's date bound (plan step
      ``recurrence:R20``).  ``anchor_date`` may not precede
      ``origination_date`` -- a loan has no balance before it exists, the
      refusal the two dashboard doors make in the same words -- but only for a
      loan that HAS originated: one originating after today has no date on or
      before today that could satisfy it, and asserts nothing.  Today is the
      DISPLAY day, the civil day the owner is typing on, the same day the
      form's date defaulted to.

    Args:
        data: The schema-loaded setup form.
        account_type: The account's :class:`AccountType` row.

    Returns:
        The flash sentence, or ``None`` when the submission stands.
    """
    max_term = account_type.max_term_months
    if max_term and data.get("term_months", 0) > max_term:
        return f"Term cannot exceed {max_term} months for {account_type.name}."
    origination_date = data["origination_date"]
    if data["anchor_date"] < origination_date <= display_today():
        return (
            "Balance date cannot be before the loan's origination "
            f"date ({origination_date.isoformat()})."
        )
    return None


@loan_bp.route("/accounts/<int:account_id>/loan/params", methods=["POST"])
@require_owner
def update_params(account_id):
    """Update loan parameters."""
    account, params, account_type = _require_configured_loan(account_id)

    errors = _update_schema.validate(request.form)
    if errors:
        flash("Please correct the highlighted errors and try again.", "danger")
        return redirect(url_for("loan.dashboard", account_id=account_id))

    data = _update_schema.load(request.form)

    # Type-specific term validation.
    max_term = account_type.max_term_months
    if max_term and data.get("term_months", 0) > max_term:
        flash(
            f"Term cannot exceed {max_term} months for {account_type.name}.",
            "danger",
        )
        return redirect(url_for("loan.dashboard", account_id=account_id))

    # DH-#56: ``interest_rate`` is no longer a LoanParams column; when
    # submitted it edits the loan's ORIGINATION rate -- upsert the
    # RateHistory row effective at origination (the resolver's period-0
    # rate).  The schema's ``@pre_load`` (E-28 / HIGH-06) already
    # converted the form percent to the storage-domain fraction.  The
    # remaining params flow through the ``_PARAM_FIELDS`` setattr loop
    # (``interest_rate`` is no longer a member).
    if "interest_rate" in data:
        _upsert_origination_rate(params, data["interest_rate"])

    for field, value in data.items():
        if field in _PARAM_FIELDS:
            setattr(params, field, value)

    # A params edit can change the origination rate (via
    # ``_upsert_origination_rate`` above) OR the ``payment_day``, and both
    # move the confirmed-payment split and any true-up's ``owed_before``; the
    # worked-out ledger reads them as they stand.  The edit is FLUSHED before
    # the standing-payment helper below, by that helper's contract, and outside
    # its refusal's catch.  (The stored ledger's genesis re-sync stood here
    # and flushed it, until plan task plan#3076.)
    db.session.flush()
    # Re-derive the standing payment's OPENING bound before committing: a
    # PAYMENT-DAY change moves the loan's first contractual installment
    # (plan step C9a), the one stored bound on that rule a params edit can
    # shift (ruling **R-R29**).  The one pair the window CHECK refuses out of
    # this edit -- the moved start passing a stop the payment's owner
    # authored (ruling **R-R82**) -- is refused there, whole, naming the
    # transfer (ruling **R-R85**: the helper is every such door's).
    refused = sync_loan_payment_start_or_refuse(
        account.id,
        redirect=RedirectTarget("loan.dashboard", {"account_id": account.id}),
    )
    if refused is not None:
        return refused
    db.session.commit()
    logger.info("Updated loan params for account %d", account.id)
    flash("Loan parameters updated.", "success")
    return redirect(url_for("loan.dashboard", account_id=account_id))


def _upsert_origination_rate(params, rate):
    """Set the loan's origination (period-0) rate to ``rate``.

    DH-#56 retired ``LoanParams.interest_rate``; the loan's base /
    period-0 rate now lives in the :class:`RateHistory` row effective at
    ``origination_date``.  The "Loan Parameters" form's rate field edits
    that origination rate, so this updates the existing origination row
    (the common case -- ``create_params`` and the DH-#56 migration both
    seed one for every loan) or inserts it if somehow absent (defensive).
    Does not commit; the caller commits with the rest of the update.

    Args:
        params: The loan's :class:`LoanParams` row.
        rate: The new origination rate as a storage-domain fraction.
    """
    origination_row = (
        db.session.query(RateHistory)
        .filter_by(
            account_id=params.account_id,
            effective_date=params.origination_date,
        )
        .first()
    )
    if origination_row is not None:
        origination_row.interest_rate = rate
    else:
        db.session.add(RateHistory(
            account_id=params.account_id,
            effective_date=params.origination_date,
            interest_rate=rate,
            monthly_pi=None,
        ))


@loan_bp.route("/accounts/<int:account_id>/loan/trueup", methods=["POST"])
@require_owner
def true_up_balance(account_id):
    """Append a dated balance true-up :class:`LoanAnchorEvent` (E-18 D-C / Commit 16).

    Mirrors the checking-account anchor true-up UX (see
    :func:`app.routes.accounts.true_up`) for loan accounts.  The user
    asserts "the lender reports my balance is $X as of date D"; the
    handler appends a single ``user_trueup`` event, and every loan-touching
    display surface walks the loan from it on its next read (the resolver,
    :func:`app.services.loan_resolver.resolve_loan`, replayed confirmed
    payments forward from it until plan step recurrence:R16-e).  The table is
    structurally
    append-only -- a correction is expressed as another append, never
    an edit -- so the new event becomes the active anchor without
    mutating any prior row.

    Validation chain:

      1. ``_load_loan_account`` rejects cross-owner / non-loan
         accounts with the project's "404 for not-found and not-yours"
         response.
      2. :class:`LoanAnchorTrueupSchema` enforces ``anchor_balance >= 0``
         and ``anchor_date <= today`` -- a future trueup is not a
         historical assertion and is rejected before any DB work.
      3. The route enforces ``anchor_date >= params.origination_date``
         here rather than in the schema because the schema does not
         have access to the loan's origination date; folding the
         check into the schema would require coupling
         :class:`LoanParams` into the schemas module.  A
         pre-origination trueup is rejected with a flash and a
         redirect; no event is written.

    Outcomes (mirroring the checking semantics):

      * COMMITTED: a new ``LoanAnchorEvent`` row is written and
        committed; the user is redirected back to the dashboard with
        a success flash.
      * UNCHANGED: the submission asserts the ``(date, balance)`` the
        governing ``user_trueup`` already asserts (the user double-clicked
        or a network retry replayed the submission), so nothing was
        written; the route treats it as idempotent success and redirects
        with an informational flash.  **It was a unique-index rejection
        until ruling R-EQ** (plan step X-f1c4b), which could not tell that
        retry from a deliberate re-assertion and refused both.

    The function does NOT mutate :class:`LoanParams`: the balance has no
    column there (the E-18 / Commit 15 ``current_principal`` seed was
    dropped at plan step ``recurrence:R20``) and the seam reads the event
    log.
    """
    account, params, _ = _require_configured_loan(account_id)

    errors = _trueup_schema.validate(request.form)
    if errors:
        flash(
            "Please correct the highlighted errors and try again.",
            "danger",
        )
        return redirect(url_for("loan.dashboard", account_id=account_id))

    data = _trueup_schema.load(request.form)
    anchor_date = data["anchor_date"]
    # Schema returns ``anchor_balance`` as Decimal because the field
    # is declared with ``places=2`` (marshmallow's Decimal field
    # constructs from a string internally); explicit reconstruction
    # via ``Decimal(str(...))`` is defensive against future schema
    # tweaks that might return a different numeric type.
    anchor_balance = Decimal(str(data["anchor_balance"]))

    if anchor_date < params.origination_date:
        flash(
            "Anchor date cannot be before the loan's origination "
            f"date ({params.origination_date.isoformat()}).",
            "danger",
        )
        return redirect(url_for("loan.dashboard", account_id=account_id))

    outcome = loan_anchor_service.apply_loan_anchor_true_up(
        account=account,
        anchor_balance=anchor_balance,
        anchor_date=anchor_date,
    )

    if outcome is AnchorTrueUpOutcome.UNCHANGED:
        # Ruling R-EQ idempotent success path: the governing true-up already
        # asserts this (date, balance).  No new row, and the on-display value
        # is already what was submitted; flash an informational message and
        # redirect.
        flash(
            "Loan balance already recorded for that date.",
            "info",
        )
        return redirect(url_for("loan.dashboard", account_id=account_id))

    # ``apply_loan_anchor_true_up`` committed the event (and the posting
    # re-sync, until plan task plan#3076).  The recurring payment's closing
    # bound is not written here or anywhere (plan step R7d-g): the payoff this
    # true-up moves is derived on every read through the composed door.

    logger.info(
        "Loan trueup: account %d set to $%s as of %s",
        account.id, anchor_balance, anchor_date,
    )
    flash(
        f"Recorded loan balance of ${anchor_balance:,.2f} "
        f"as of {anchor_date.strftime('%b %-d, %Y')}.",
        "success",
    )
    return redirect(url_for("loan.dashboard", account_id=account_id))


@loan_bp.route("/accounts/<int:account_id>/loan/tracking-start", methods=["POST"])
@require_owner
def record_tracking_start(account_id):
    """Record a mid-life-import tracking-start (a ``tracking_start`` event).

    For an already-amortizing loan the operator began tracking mid-life: the user
    asserts "when I started tracking, my real balance was $X as of date D."  The
    handler appends a ``tracking_start`` :class:`LoanAnchorEvent`, which the
    genesis walk loads as an ordinary ``is_opening=False`` balance ASSERTION
    (:func:`app.services.loan_loaders.load_loan_anchor_facts`) that RESETS the
    running balance at its own date -- so a date at or after it reads the
    operator's real balance rather than an amortized guess, while a date before
    it reads the origination opening held flat.  The origination fields on
    :class:`LoanParams` are untouched (they still drive the amortization schedule
    / projection).

    *It does NOT become the loan's opening, and this docstring said it did until
    plan step X-an-b*, citing ``loan_loaders._opening_anchor_fact`` -- a function
    step C1 deleted along with that behaviour.  Origination is the opening
    ALWAYS: opening at a mid-life tracking-start read the loan out of existence
    for its whole pre-tracking window (the false pre-opening zero, finding B-11).

    **Since plan step ``recurrence:R20`` the setup door writes this same row
    for the balance the owner states at setup**, so the common mid-life import
    never reaches this door at all; it remains for a tracking-start recorded
    after the fact.  **It refuses a date on or before the day a payment into
    the loan moved money** (ruling **R-R114**, with ruling **R-BAL155**'s
    day; the service's :class:`~app.exceptions.TrackingStartRefused`): a
    tracking start says where the app's record of the loan STARTS, which is
    what the loan's recorded start reads, and a payment whose money moved on
    or before the date says it started earlier -- so the statement is a
    balance correction, and the flash offers an earlier date or the Record
    balance control above, which records one.  Where the payment's day is on
    or before the origination, no date this door accepts comes before it, so
    the flash offers Record balance alone.  *The route refused a date not
    STRICTLY BEFORE the earliest recorded payment's due date until R20*
    (ruling R-R72 part 3), on the ground that the payment "would sort before
    the opening in the walk and be subsumed" -- the opening claim step C1 had
    already retired, and R20 deleted that refusal.  R-R114's rests on the
    label instead, and reads the day each payment's money moved rather than
    its due date.

    Validation chain (mirrors :func:`true_up_balance`):

      1. ``_require_configured_loan`` rejects cross-owner / non-loan / unconfigured
         accounts.
      2. :class:`LoanAnchorTrueupSchema` (reused -- identical fields) enforces
         ``anchor_balance >= 0`` and ``anchor_date <= today``.
      3. The route enforces ``anchor_date >= params.origination_date`` (a loan
         cannot be tracked before it existed), route-level because the schema
         has no access to the loan.
      4. The service refuses a date on or before the day a payment moved
         money (rulings R-R114 and R-BAL155, above), ahead of the duplicate
         rule, so a resubmit that a payment now contradicts is refused too:
         the write is rolled back, then a danger flash and a redirect.

    Outcomes mirror the true-up: COMMITTED (success flash + redirect) or
    UNCHANGED (idempotent success when the governing ``tracking_start`` already
    asserts this ``(date, balance)``).  The comparison is scoped to the
    ``tracking_start`` source, so a re-submitted tracking-start is recognised
    even after true-ups have been recorded on later dates.
    """
    account, params, _ = _require_configured_loan(account_id)

    errors = _trueup_schema.validate(request.form)
    if errors:
        flash(
            "Please correct the highlighted errors and try again.",
            "danger",
        )
        return redirect(url_for("loan.dashboard", account_id=account_id))

    data = _trueup_schema.load(request.form)
    anchor_date = data["anchor_date"]
    anchor_balance = Decimal(str(data["anchor_balance"]))

    if anchor_date < params.origination_date:
        flash(
            "Tracking-start date cannot be before the loan's origination "
            f"date ({params.origination_date.isoformat()}).",
            "danger",
        )
        return redirect(url_for("loan.dashboard", account_id=account_id))

    try:
        outcome = loan_anchor_service.record_loan_tracking_start(
            account=account,
            anchor_balance=anchor_balance,
            anchor_date=anchor_date,
        )
    except TrackingStartRefused as refused:
        # Rulings R-R114 / R-BAL155: nothing was staged.  The remedy is this
        # door's (the setup form has no Record balance), composed before the
        # rollback expires the params it reads.
        moved_on = refused.moved_on.strftime("%b %-d, %Y")
        remedy = (
            f"Choose a date before {moved_on}, or use Record balance to "
            f"correct the loan's balance instead."
            if refused.moved_on > params.origination_date else
            f"No tracking start can come before that payment, since the loan "
            f"originated on {params.origination_date.strftime('%b %-d, %Y')}; "
            f"use Record balance to correct the loan's balance instead."
        )
        db.session.rollback()
        flash(f"{refused}  {remedy}", "danger")
        return redirect(url_for("loan.dashboard", account_id=account_id))

    if outcome is AnchorTrueUpOutcome.UNCHANGED:
        flash(
            "Tracking-start balance already recorded for that date.",
            "info",
        )
        return redirect(url_for("loan.dashboard", account_id=account_id))

    # ``record_loan_tracking_start`` committed the event (and the posting
    # re-sync, until plan task plan#3076); the payoff it moves is derived on
    # every read (plan step R7d-g), as at the true-up route above.

    logger.info(
        "Loan tracking-start: account %d set to $%s as of %s",
        account.id, anchor_balance, anchor_date,
    )
    flash(
        f"Recorded tracking-start balance of ${anchor_balance:,.2f} "
        f"as of {anchor_date.strftime('%b %-d, %Y')}.",
        "success",
    )
    return redirect(url_for("loan.dashboard", account_id=account_id))


@loan_bp.route("/accounts/<int:account_id>/loan/collateral", methods=["POST"])
@require_owner
def update_collateral(account_id):
    """Set or clear the asset that secures this loan (home-equity link).

    Writes the nullable ``collateral_account_id`` self-link on the loan
    account so a mortgage / HELOC can be grouped with the Property it is
    secured by and equity rendered.  The link is presentation only -- the
    emergent net-worth math never reads it.

    **Exactly ``""`` clears the link; anything else -- including the field
    being ABSENT -- is refused** (plan step X-ae).  Those are different inputs
    and this route used to answer them all the same way: ``""`` is the picker's
    own blank option, a deliberate "nothing secures this loan", while any other
    value, or no field at all, cannot come from the picker.  A browser
    rendering this form always submits the ``<select>``, so an absent field is
    a forged or truncated POST, not a choice.  Clearing on the second meant a
    forged field silently destroyed a real link under a success flash -- and,
    on 128 of the characters ``str.isdigit()`` accepts, raised into an
    unhandled 500 before it got that far (finding N-136).  A value that names
    no id now gets the same
    :data:`~app.utils.account_validation.INVALID_COLLATERAL_LINK` answer as an
    id naming no account, and nothing is written.

    **The submission is NOT stripped, and that is the ruling rather than an
    omission.**  A ``.strip()`` here re-opened the hole twice over: it maps
    every Unicode space to ``""`` -- ``"\\xa0".strip()`` is ``""`` -- so a
    forged non-breaking space took the CLEAR path under a success flash, the
    exact behaviour the paragraph above says is closed; and it normalised a
    value before applying the shared rule, so ``" 2 "`` linked here while the
    reconcile and companion doors refused it, leaving four doors with three
    behaviours.  Both were found by adversarial review of the first build.

    Every value that DOES name an id is validated by
    :func:`app.utils.account_validation._validate_collateral_link`
    (same-owner Asset target, no self-link, source is an amortizing
    liability) before it is written.
    """
    account = get_or_404(Account, account_id)
    if account is None:
        abort(404)

    # The picker submits an Asset account id or "" (clear); the guard takes
    # everything else, so the validator below sees only ``None`` or a real id.
    # ``None`` (the field absent entirely) is NOT the blank option and does not
    # clear: a browser rendering this form always submits the select, so an
    # absent field is the same forged or truncated POST the guard refuses --
    # defaulting it to "" put it back on the clear path, which an adversarial
    # review caught destroying a real link under a success flash.
    submitted = request.form.get("collateral_account_id")
    collateral_account_id = None
    if submitted != "":
        collateral_account_id = parse_row_id(submitted)
        if collateral_account_id is None:
            flash(*INVALID_COLLATERAL_LINK)
            return redirect(url_for("loan.dashboard", account_id=account_id))

    failure = _validate_collateral_link(
        collateral_account_id, account, current_user.id,
    )
    if failure is not None:
        flash(failure[0], failure[1])
        return redirect(url_for("loan.dashboard", account_id=account_id))

    account.collateral_account_id = collateral_account_id
    db.session.commit()
    logger.info("Updated collateral link for account %d", account.id)
    flash("Secured-by link updated.", "success")
    return redirect(url_for("loan.dashboard", account_id=account_id))
