"""
Shekel Budget App -- Account CRUD Routes

Account create / read / update / archive / hard-delete endpoints.
Split out of the historical monolithic ``app/routes/accounts.py``
in Commit 21 of the financial-calculation audit follow-up (F-1);
behaviour preserved verbatim from the pre-split file.

The optimistic-lock contract (commit C-17 / F-009) operates at two
tiers in this module's update / archive / hard-delete routes: a
pre-flush ``version_id`` comparison against the form value (catches
the sequential Tab-1 / Tab-2 race) and the SQLAlchemy
``version_id_col`` ``WHERE version_id = ?`` at flush time (catches
the truly-concurrent interleaving the form-side check cannot see).
Both layers convert ``StaleDataError`` into a flash + redirect so
the user can retry against fresh row state.

The C-28 / F-044 multi-tenant ownership guard for
``ref.account_types`` lives in :mod:`app.utils.account_validation`
(``_visible_account_type``, ``_visible_account_types``); routes
in this file call those helpers rather than inlining the guard.
"""

import logging
from decimal import Decimal

from flask import abort, flash, redirect, render_template, request, url_for
from flask_login import current_user

from app.exceptions import ValidationError
from app.extensions import db
from app.models.account import Account
from app.models.asset_appreciation_params import AssetAppreciationParams
from app.models.escrow_line import EscrowLine
from app.models.interest_params import InterestParams
from app.models.investment_params import InvestmentParams
from app.models.loan_features import RateHistory
from app.models.loan_params import LoanParams
from app.models.pay_period import PayPeriod
from app.models.savings_goal import SavingsGoal
from app.models.transaction import Transaction
from app.models.transaction_template import TransactionTemplate
from app.models.transfer import Transfer
from app.models.transfer_template import TransferTemplate
from app.routes._commit_helpers import (
    StaleConflictContext,
    commit_or_handle_stale,
    regenerate_and_commit_or_stale,
)
from app.routes._redirect_target import RedirectTarget
from app.routes.accounts._bp import accounts_bp
from app.routes.accounts.opening import books_opening_context
from app.services import (
    account_posting_service,
    account_service,
    definition_delete,
    ledger_account_service,
    liability_sign,
    pay_period_service,
    transfer_service,
)
from app.services.account_params import ensure_type_params
from app.services.account_projection import AccountProjectionKind, classify_account
from app.utils import archive_helpers
from app.utils.account_validation import (
    _create_schema,
    _validate_update_account,
    _visible_account_type,
    _visible_account_types,
)
from app.utils.auth_helpers import fresh_login_required, get_or_404, require_owner
from app.utils.dates import display_today

logger = logging.getLogger(__name__)

# Field allowlist for the account update route: which submitted form
# fields may be written back to the Account via setattr.
_ACCOUNT_UPDATE_FIELDS = {"name", "account_type_id", "sort_order", "is_active"}


# ── Account CRUD ───────────────────────────────────────────────────


@accounts_bp.route("/accounts")
@require_owner
def list_accounts():
    """Redirect the retired ``/accounts`` table to the unified cockpit.

    Loop B P4 retired the standalone management table: the Net Worth
    Cockpit (``savings.dashboard``) now both displays AND manages
    accounts -- inline click-to-edit balances, a per-card kebab carrying
    Edit and Archive, and hard-delete relocated to the shared edit
    form's danger zone (developer ruling 2026-06-25, audit decision 12).
    The endpoint is kept as a permanent redirect, not deleted, so that
    external bookmarks of ``/accounts`` still resolve and the
    unauthenticated-redirect contract in
    ``tests/test_routes/test_auth_required.py`` stays green; every
    in-app caller was repointed directly at ``savings.dashboard``.
    """
    return redirect(url_for("savings.dashboard"))


@accounts_bp.route("/accounts/new", methods=["GET"])
@require_owner
def new_account():
    """Display the account creation form.

    The type dropdown is scoped to seeded built-ins plus the current
    owner's custom types (commit C-28 / F-044).

    ``owed_type_ids`` marks the types whose balance box asks for the amount
    owed (plan step credit_card:CC-5-5b, ruling R-CC58), and
    ``js/account_form.js`` relabels the box from this mark.  Classified by
    :func:`app.services.liability_sign.asks_owed`, the SAME rule the create
    route crosses the typed figure by -- asked here at render and there at
    POST, so a custom type whose category is edited in another tab between the
    two is read by the new category (see :func:`create_account`).
    """
    account_types = _visible_account_types(current_user.id)
    return render_template(
        "accounts/form.html",
        account=None,
        account_types=account_types,
        owed_type_ids={
            acct_type.id for acct_type in account_types
            if liability_sign.asks_owed(acct_type)
        },
        # The "balance as of" field's default and its two bounds, mirroring
        # ``anchor_service.resolve_observation_day`` so the browser refuses
        # what the service would refuse rather than round-tripping a rejection.
        # ``display_today()`` rather than ``date.today()``: the process clock is
        # pinned to the display zone in the deployed container, but a script or
        # CI run is not, and the form must not offer a day the service then
        # rejects (ruling R-DH (b)).  The floor is
        # ``pay_period_service.earliest_recordable_day`` DIRECTLY, as
        # ``routes/transactions/forms.py`` already reads it: it went through an
        # ``account_service`` alias until ruling R-ER moved the guard out from
        # under that alias, leaving a pass-through with no behaviour and two
        # spellings of one form bound at the route layer.
        today=display_today(),
        observed_on_min=pay_period_service.earliest_recordable_day(
            current_user.id,
        ),
    )


def _setup_redirect_url(account, kind):
    """Return the post-create redirect URL for a new account.

    Parameterized accounts go to their type-specific setup page (the
    established setup-page pattern); everything else returns to the unified
    accounts cockpit (``savings.dashboard``, the retired ``/accounts`` table's
    successor).  Resolving the URL here keeps :func:`create_account` within
    Pylint's branch and return-count limits.

    Args:
        account: The freshly-created :class:`~app.models.account.Account`.
        kind: The account's :class:`AccountProjectionKind`.

    Returns:
        str: The ``url_for`` target to redirect to.
    """
    if kind is AccountProjectionKind.INTEREST:
        return url_for("accounts.cash_detail", account_id=account.id, setup=1)
    if kind is AccountProjectionKind.AMORTIZING:
        return url_for("loan.dashboard", account_id=account.id, setup=1)
    if kind is AccountProjectionKind.INVESTMENT:
        return url_for("investment.dashboard", account_id=account.id, setup=1)
    if kind is AccountProjectionKind.APPRECIATING:
        return url_for("accounts.property_detail", account_id=account.id, setup=1)
    return url_for("savings.dashboard")


@accounts_bp.route("/accounts", methods=["POST"])
@require_owner
def create_account():
    """Create a new account."""
    errors = _create_schema.validate(request.form)
    if errors:
        flash("Please correct the highlighted errors and try again.", "danger")
        return redirect(url_for("accounts.new_account"))

    data = _create_schema.load(request.form)

    # Multi-tenant guard (commit C-28 / F-044): the submitted
    # account_type_id must reference a seeded built-in or one of
    # this owner's own custom types.  A forged post that points at
    # another owner's custom type is collapsed into the same
    # "Invalid account type." response as a non-existent FK so the
    # response cannot be used to probe for the existence of other
    # owners' catalogues.
    account_type = _visible_account_type(
        data["account_type_id"], current_user.id,
    )
    if account_type is None:
        flash("Invalid account type.", "danger")
        return redirect(url_for("accounts.new_account"))

    # Check for duplicate name.
    existing = (
        db.session.query(Account)
        .filter_by(user_id=current_user.id, name=data["name"])
        .first()
    )
    if existing:
        flash("An account with that name already exists.", "warning")
        return redirect(url_for("accounts.new_account"))

    # ``anchor_balance`` is an optional Decimal field; the schema's
    # ``@pre_load`` strips empty submissions, so a missing key -- not a
    # falsy zero -- means "no opening balance".  Branch on presence
    # (``is None``), never on Decimal truthiness: a legitimately-entered
    # zero opening balance is a value, not a missing balance.
    raw_anchor = data.pop("anchor_balance", None)
    entered = (
        Decimal(str(raw_anchor)) if raw_anchor is not None else Decimal("0")
    )
    # **The box asks a liability for the amount OWED, and this is the ONE
    # crossing to the held sign the account stores** (plan step
    # credit_card:CC-5-5b, rulings R-CC52 / R-CC58).  Typing 5,000.00 on a car
    # loan stores -5,000.00, which reads $5,000.00 owed; an asset's figure is
    # stored as typed.  Classified by the TYPE the owner picked, because the
    # account does not exist yet -- the row the visibility check above returned
    # -- and here, at the route, because ruling R-J keeps account class out of
    # the schema tier and the service's contract is the held balance every
    # other caller already passes.
    #
    # **Not guarded against a stale form, and that is ruling R-CC61's stated
    # exception.**  The anchor editor and the books-opening card refuse a save
    # whose account crossed asset <-> liability since the form opened; this
    # form picks its type in the same submission, so the only race left is a
    # CUSTOM type whose category is edited in another tab between render and
    # POST (permitted while none of its accounts has postings) -- the figure is
    # then crossed by the category the type has NOW.
    anchor_balance = liability_sign.held_balance(account_type, entered)

    # E-19 (Commit 3): the canonical factory in
    # ``app.services.account_service.create_account`` materializes
    # the account row AND a matching origination AccountAnchorHistory
    # row, and resolves the anchor period from the user's pay-period
    # inventory.  If the user has zero pay periods, the factory
    # raises ``ValidationError``; this route converts that into a
    # redirect to ``/pay-periods/generate`` so the user can fix the
    # missing-periods state and retry.
    # ``account_type_id`` and ``name`` are required ``AccountCreateSchema``
    # fields, so they are always present in ``data``; lift them out into
    # the ``AccountSpec`` and forward any remaining validated columns
    # through ``**data`` (the same passthrough the prior ``**data`` splat
    # provided).
    account_type_id = data.pop("account_type_id")
    name = data.pop("name")
    # The civil day the entered balance was TRUE (ruling R-DH, plan step 2).
    # Popped out of ``data`` like the two above because what remains is
    # splatted onto the ``Account`` constructor, and this is a column on the
    # assertion, not on the account.  Absent (the field left blank) means
    # today, which the factory applies -- stated there rather than here so the
    # non-route callers get the same default.
    observed_on = data.pop("observed_on", None)
    try:
        account = account_service.create_account(
            account_service.AccountSpec(
                user_id=current_user.id,
                account_type_id=account_type_id,
                name=name,
                anchor_balance=anchor_balance,
                observed_on=observed_on,
            ),
            **data,
        )
    except ValidationError as exc:
        # Two shapes reach here and they need different destinations: the user
        # has NO pay periods (send them to generate some), or the "balance as
        # of" day is out of bounds (send them back to the form with the
        # service's own message, which names the day and the bound it broke).
        #
        # Discriminated by asking the database, not by re-deriving the
        # service's own predicate: re-evaluating "is this date in the future"
        # here would read the clock a second time and could disagree with the
        # read that raised.
        has_periods = db.session.query(
            db.session.query(PayPeriod)
            .filter_by(user_id=current_user.id).exists()
        ).scalar()
        if has_periods:
            flash(str(exc), "warning")
            return redirect(url_for("accounts.new_account"))
        # The reason is the OPENING's posting correction, not anchoring: an
        # assertion carries no pay period (ruling R-EO deleted the column), so
        # "the account balance has a period to anchor against" -- what this
        # string said until plan step X-f1c4c -- named a fact that no longer
        # exists.  It is the only user-facing copy stating the rule, and it was
        # left behind when the service's own wording was corrected.
        flash(
            "Generate pay periods before creating an account: its opening "
            "balance posts into the pay period containing the day it is true "
            "for, and an empty calendar has no such period.",
            "warning",
        )
        return redirect(url_for("pay_periods.generate_form"))

    # Auto-create the type-specific params row and resolve the setup-page
    # redirect by projection kind.  The canonical classifier owns the
    # taxonomy, so a parameterised physical asset (Property -> APPRECIATING)
    # is never mistaken for an investment -- the bug a bare ``has_parameters
    # and not has_interest and not has_amortization`` predicate introduces.
    kind = classify_account(account)
    ensure_type_params(account, kind)
    db.session.commit()

    logger.info("Created account: %s (id=%d)", account.name, account.id)
    flash(f"Account '{account.name}' created.", "success")

    return redirect(_setup_redirect_url(account, kind))


@accounts_bp.route("/accounts/<int:account_id>/edit", methods=["GET"])
@require_owner
def edit_account(account_id):
    """Display the account edit form.

    **It carries the books-opening card since plan step X-f3c-2b-2a**, built by
    :func:`app.routes.accounts.opening.books_opening_context`.  This page is the
    one surface EVERY account kind reaches (the cockpit card's kebab -> Edit),
    which is why the restatement door lives here rather than only on the cash
    detail page whose balance-history card is the opening's sole display --
    that page serves three of the developer's nine accounts, and four of the
    other six carry a ``migration_derived`` opening the balance fold reads.

    The context is ``None`` for an AMORTIZING account and the template renders
    nothing then: a loan's opening is its original principal, so a card here
    would be a dead-end affordance.
    """
    account = get_or_404(Account, account_id)
    if account is None:
        abort(404)

    return render_template(
        "accounts/form.html",
        account=account,
        account_types=_visible_account_types(current_user.id),
        books_opening=books_opening_context(account),
    )


@accounts_bp.route("/accounts/<int:account_id>", methods=["POST"])
@require_owner
def update_account(account_id):
    """Update an account's name, type and active flag.

    **It does NOT assert a balance, and that is plan step X-f1e** (finding
    **N-195**).  It accepted an ``anchor_balance`` and staged an assertion from
    it, which made this the app's SECOND balance-assertion door -- and the two
    doors answered the same submission differently.  This form PRE-FILLS the
    current figure, so saving a rename re-submitted it unchanged; this door read
    that as "no change" while the write door (:func:`stage_anchor_true_up`, and
    ruling R-EQ) reads a submission as new when it changes what GOVERNS, which
    includes the day.  Two neutral reviews of plan step X-f1c4b recommended
    aligning this door's gate with that rule; both missed that the pre-fill makes
    it worse, because a rename would then silently assert today's balance and
    absorb purchases the user never reconciled.  The gate was the right rule for
    a form that is not a balance-reading surface -- so the SURFACE went, not the
    gate.  Asserting a balance is the one-click editor's job
    (``accounts.true_up``), on every screen that shows a balance.

    Optimistic locking (commit C-17 / F-009) operates in two layers:

      1. Stale-form check: the edit form ships ``version_id`` as a
         hidden input set to the row's counter at render time.  When
         the submitted value differs from the current
         ``Account.version_id``, the handler short-circuits with a
         flash + redirect (renders well in a non-HTMX flow) and
         records nothing.  This catches the sequential Tab-1/Tab-2
         race documented in the C-17 manual verification.

      2. SQLAlchemy ``version_id_col``: any concurrent flush that
         races past the stale-form check is still narrowed by
         ``WHERE version_id = ?`` at the database tier; the loser
         raises ``StaleDataError`` which the handler converts into
         the same flash + redirect path.  The two layers together
         close every interleaving the optimistic-lock contract is
         meant to cover.
    """
    account = get_or_404(Account, account_id)
    if account is None:
        abort(404)

    # Validation phase.  Delegates to a helper that returns either
    # ``(data, None)`` (proceed) or ``({}, (message, category))``
    # (reject).  Folding every non-mutating check into a single
    # gateway keeps the route's return count below Pylint's R0911
    # limit after the C-28 multi-tenant guard was added.
    data, failure = _validate_update_account(account, request.form, current_user.id)
    if failure is not None:
        flash(failure[0], failure[1])
        return redirect(url_for("accounts.edit_account", account_id=account_id))

    old_type_id = account.account_type_id
    for field, value in data.items():
        if field in _ACCOUNT_UPDATE_FIELDS:
            setattr(account, field, value)
    type_changed = account.account_type_id != old_type_id

    # The side effects and the commit live inside the same try/except
    # because the resync below flushes the pending Account mutation -- the
    # version-pinned WHERE clause is checked at flush time, so
    # ``StaleDataError`` would otherwise escape outside the catch.
    # See the matching comment in :func:`true_up`.

    def _reconcile_type_effects():
        """Reconcile the type-change side effects (in-transaction step).

        Re-class the (empty) linked ledger row when the Asset/Liability
        boundary was crossed -- ``_validate_update_account`` already refused a
        crossing on a posted account -- and re-sync the account's Step-5 anchor
        corrections so an amortizing-boundary crossing swaps correction
        families instead of stranding one (the sync structurally no-ops for the
        loan side).

        **It reconciles nothing for an ANCHOR any more, because this route no
        longer asserts one** (plan step X-f1e, finding N-195).  It ran the same
        re-sync on ``anchor_changed or type_changed``; with the anchor branch
        deleted, a type change is the only thing here that can move a posted
        correction, so the whole body is one condition.  A balance assertion
        re-syncs through its own door (``anchor_service.apply_anchor_true_up``),
        which is where the assertion is now written.

        **It stopped touching entries at plan step S1-c** (ruling R-DH (d)).  An
        anchor change here used to bulk-flip ``is_cleared``, which made "is this
        purchase already inside the balance the user typed" an answer decided by
        recording order.  That reasoning is what this step finished: an account
        EDIT is not the surface a balance reading is entered on.

        **It SEEDS the new kind's params row since plan step balance:X-i3**, and
        that it did not is what made two detail pages repair the row on a GET.
        :mod:`app.services.account_params` had exactly ONE caller --
        account CREATION -- so re-classing Checking into a ``has_interest``
        type left an interest-bearing account with no
        :class:`~app.models.interest_params.InterestParams`, and the cash and
        property detail pages each carried an auto-create for the row this door
        should have written.  Reached through the shared seeder rather than a
        second copy of it: two doors establishing one invariant two ways is how
        they come to disagree.
        """
        if not type_changed:
            return
        # Flush the new FK and expire the stale ``account_type``
        # relationship so the re-class and the resync's classifier both
        # read the NEW type (the relationship attribute is not refreshed
        # by the setattr alone).
        db.session.flush()
        db.session.expire(account, ["account_type"])
        ensure_type_params(account, classify_account(account))
        ledger_account_service.sync_linked_ledger_class(account)
        account_posting_service.sync_account_anchor_postings_all_scenarios(
            account.id,
        )

    # The reconcile step must run inside the same stale-race guard as
    # the commit (it flushes and can itself raise StaleDataError).
    conflict = regenerate_and_commit_or_stale(
        _reconcile_type_effects,
        ctx=StaleConflictContext(
            logger=logger,
            log_label="update_account",
            log_id=account_id,
            flash_message=(
                "This account was changed by another action while you were "
                "editing.  Please reload and try again."
            ),
            redirect=RedirectTarget(
                "accounts.edit_account",
                {"account_id": account_id},
            ),
        ),
    )
    if conflict is not None:
        return conflict

    logger.info("Updated account: %s (id=%d)", account.name, account.id)
    flash(f"Account '{account.name}' updated.", "success")
    return redirect(url_for("savings.dashboard"))


@accounts_bp.route("/accounts/<int:account_id>/archive", methods=["POST"])
@require_owner
def archive_account(account_id):
    """Archive an account (soft delete).

    The Account model carries a ``version_id_col`` (commit C-17),
    so a concurrent mutation interleaving with this archive will
    raise ``StaleDataError`` at flush time.  The handler converts
    it into a flash + redirect so the user can retry against the
    fresh row state instead of seeing a 500.
    """
    account = get_or_404(Account, account_id)
    if account is None:
        abort(404)

    # Guard: prevent archiving if active transfer templates reference this account.

    active_transfers = (
        db.session.query(TransferTemplate)
        .filter(
            TransferTemplate.user_id == current_user.id,
            TransferTemplate.is_active.is_(True),
            db.or_(
                TransferTemplate.from_account_id == account_id,
                TransferTemplate.to_account_id == account_id,
            ),
        )
        .first()
    )
    if active_transfers:
        flash(
            "Cannot archive this account -- it is used by active recurring transfers. "
            "Archive those recurring transfers first.",
            "warning",
        )
        return redirect(url_for("savings.dashboard"))

    account.is_active = False
    conflict = commit_or_handle_stale(StaleConflictContext(
        logger=logger,
        log_label="archive_account",
        log_id=account_id,
        flash_message=(
            "This account was changed by another action.  Please reload "
            "the page and try again."
        ),
        redirect=RedirectTarget("savings.dashboard"),
    ))
    if conflict is not None:
        return conflict
    logger.info("Archived account: %s (id=%d)", account.name, account.id)
    flash(f"Account '{account.name}' archived.", "info")
    return redirect(url_for("savings.dashboard"))


@accounts_bp.route("/accounts/<int:account_id>/unarchive", methods=["POST"])
@require_owner
def unarchive_account(account_id):
    """Unarchive an account.

    See :func:`archive_account` for the optimistic-lock contract.
    """
    account = get_or_404(Account, account_id)
    if account is None:
        abort(404)

    account.is_active = True
    conflict = commit_or_handle_stale(StaleConflictContext(
        logger=logger,
        log_label="unarchive_account",
        log_id=account_id,
        flash_message=(
            "This account was changed by another action.  Please reload "
            "the page and try again."
        ),
        redirect=RedirectTarget("savings.dashboard"),
    ))
    if conflict is not None:
        return conflict
    logger.info("Unarchived account: %s (id=%d)", account.name, account.id)
    flash(f"Account '{account.name}' unarchived.", "success")
    return redirect(url_for("savings.dashboard"))


def _archive_instead_of_delete(account, account_id, reason):
    """Archive an account with history worth preserving, instead of deleting.

    The shared outcome of the hard-delete history guards (transaction history
    and posting-ledger history): flash *reason*, flip ``is_active`` to False
    idempotently, and commit under the optimistic lock.  Returns the Flask
    response the caller returns directly.

    Args:
        account: The owned Account being archived.
        account_id: Its id (for the stale-conflict log label and redirect).
        reason: The user-facing flash message explaining the archive.

    Returns:
        A Flask redirect response, or the stale-conflict response when a
        concurrent update bumped the row's version.
    """
    flash(reason, "warning")
    if account.is_active:
        account.is_active = False
        conflict = commit_or_handle_stale(StaleConflictContext(
            logger=logger,
            log_label="hard_delete_account archive-fallback",
            log_id=account_id,
            flash_message=(
                "This account was changed by another action.  "
                "Please reload the page and try again."
            ),
            redirect=RedirectTarget("savings.dashboard"),
        ))
        if conflict is not None:
            return conflict
    return redirect(url_for("savings.dashboard"))


def _history_refusal(account, definitions) -> "str | None":
    """Return why *account*'s history forbids a permanent delete, or ``None``.

    Guard 4 of :func:`hard_delete_account`, its two arms asked in order and
    each answering with the sentence the archive flash prints:

    * **rows** -- any non-deleted transaction on the account or under one of
      its definitions, and any SETTLED row under one of its definitions
      whatever its ``is_deleted`` (the definition door's own refusal, ruling
      **R-JE**; an adversarial review of plan step ``balance:X-bi-7a``
      reproduced the disposal nulling such a row's link);
    * **movements on it** (plan step ``credit_card:CC-5-2``) -- money that
      moved through this account from rows that are NOT its own: a card
      holding swipes recorded in checking envelopes (ruling **R-CC15**).
      The arm above does not see them (the rows are checking's) and the
      door's cleanup disposes of none, so ``fk_transaction_entries_account_id``'s
      RESTRICT (ruling **R-CC32**) met the DELETE as a 500.  **A stated
      behaviour change**: such an account ARCHIVES, as every other kind of
      history does (``archive_helpers.account_holds_other_rows_movements``).
      *A card whose only movements sat under its own ghost rows still
      deleted, with them, until plan step ``credit_card:CC-5-4a-4``, whose
      arm here archived on a payment or purchase under a HIDDEN row or
      transfer the cleanup would remove.  That arm went at plan step
      ``balance:X-bi-6-4d-2``, because the database refuses a hidden row or
      transfer holding one (rulings **R-CC89**, **R-CC92**;
      :mod:`app.deleted_row_infrastructure`): a row the cleanup removes holds
      no movement.*

    Args:
        account: The owned :class:`~app.models.account.Account`.
        definitions: Its ``TransactionTemplate`` rows, already loaded by the
            door for guard 3.

    Returns:
        The user-facing reason, or ``None`` when neither arm finds history.
    """
    if archive_helpers.account_has_history(account.id) or any(
        archive_helpers.template_has_paid_history(definition.id)
        for definition in definitions
    ):
        return (
            f"'{account.name}' has transaction history and cannot be "
            "permanently deleted. It has been archived instead."
        )
    if archive_helpers.account_holds_other_rows_movements(account.id):
        return (
            f"'{account.name}' has purchases recorded on it from other "
            "accounts' rows and cannot be permanently deleted. It has been "
            "archived instead."
        )
    return None


@accounts_bp.route("/accounts/<int:account_id>/hard-delete", methods=["POST"])
@require_owner
@fresh_login_required()
def hard_delete_account(account_id):
    """Permanently delete an account if it has no blocking dependents.

    Guard chain (checked in order):
      1. Ownership -- account exists and belongs to current user.
      2. Transfer template guard -- any TransferTemplate (active or
         archived) referencing this account blocks deletion because the
         FK is ON DELETE RESTRICT.
      3. Recurring-definition guard -- any TransactionTemplate (active or
         archived) on this account that RECURS blocks deletion for the same
         FK reason.  A rule-less definition is a ONE-OFF's (plan step
         ``balance:X-bi-7a``, ruling **R-BAL23**: the refusal counts
         definitions with a rule) and is disposed of below, because the
         Recurring list is where the owner would go to "delete those
         recurring transactions first" and a one-off is not listed there.
      4. History check -- any non-deleted Transaction on this account, or
         under one of its definitions, triggers archive-instead-of-delete;
         so a rule-less definition still holding a live row anywhere
         archives the account exactly as an ad-hoc row does.  So does a
         SETTLED row under one of them whatever its ``is_deleted`` -- the
         definition door's own refusal (``template_has_paid_history``,
         ruling **R-JE**): the disposal below would otherwise leave that
         row TEMPLATE-priced with its link nulled, the state finding
         **N-440** names.  And so does a MOVEMENT on this account under a
         row that is neither on it nor under one of its definitions
         (``account_holds_other_rows_movements``, plan step
         ``credit_card:CC-5-2``): a card holding swipes recorded in
         checking envelopes is history the row arm cannot see, and the
         cleanup below cannot dispose of, so ``fk_transaction_entries_
         account_id``'s RESTRICT (ruling **R-CC32**) would refuse step 4
         as a 500.  A row or transfer the cleanup below removes holds no
         payment or purchase: a live one is history above, and the
         database refuses a hidden one holding one (rulings **R-CC89**,
         **R-CC92**).
      5. Posting-ledger check -- any posting on ANY of this account's
         ledger accounts (a settled transfer's immutable entries, which
         survive a transfer delete, and its anchor corrections' counter
         legs) triggers archive-instead-of-delete, even when no transaction
         history remains.

    Permanent delete cleanup:
      After all guards pass, remaining RESTRICT-FK rows must be
      explicitly removed before the account row can be deleted:
        - Transfer rows (soft-deleted or ghost ad-hoc) referencing this
          account, deleted through transfer_service, which every write of
          a transfer goes through (CLAUDE.md, Architecture).
        - Transaction rows (soft-deleted ghosts) referencing this
          account.
        - The rule-less TransactionTemplates on this account (the only
          kind guard 3 lets through), each holding no live and no settled
          row (guard 4): a definition with no rule and no row defines
          nothing, and its RESTRICT key would refuse the account otherwise.
          Disposed of through ``definition_delete.permanently_delete_definition``,
          the SAME act the definition's own hard-delete performs, so the
          soft-deleted ghosts it still names (on this account or another)
          are deleted rather than unlinked -- none holding a movement, which
          the database refuses of a hidden row.  Their series rows cascade
          with them; the merchant rules that could name one cascade with the
          ACCOUNT already (``fk_merchant_rules_owner``).
      CASCADE-FK dependents (LoanParams, InterestParams,
      InvestmentParams, AccountAnchorHistory, AnchorRelease, SavingsGoal,
      LoanFeatures) are auto-deleted by PostgreSQL when the account row is
      removed.
    """
    account = get_or_404(Account, account_id)
    if account is None:
        abort(404)

    # Guard 2: transfer templates with RESTRICT FK.
    blocking_xfer_template = (
        db.session.query(TransferTemplate)
        .filter(
            TransferTemplate.user_id == current_user.id,
            db.or_(
                TransferTemplate.from_account_id == account_id,
                TransferTemplate.to_account_id == account_id,
            ),
        )
        .first()
    )
    if blocking_xfer_template:
        flash(
            "Cannot delete this account -- it is used by recurring transfers. "
            "Delete those recurring transfers first.",
            "warning",
        )
        return redirect(url_for("savings.dashboard"))

    # Guard 3: RECURRING transaction definitions with RESTRICT FK.  Loaded
    # and asked rather than filtered: ``recurs`` is a per-row derivation with
    # one spelling (``TransactionTemplate.recurs``), and a query keyed on it
    # refuses to build.  An account holds a handful of definitions.
    definitions = (
        db.session.query(TransactionTemplate)
        .filter_by(account_id=account_id, user_id=current_user.id)
        .all()
    )
    if any(definition.recurs for definition in definitions):
        flash(
            "Cannot delete this account -- it has recurring transactions. "
            "Delete those recurring transactions first.",
            "warning",
        )
        return redirect(url_for("savings.dashboard"))

    # Guard 4: transaction history, in its two arms (:func:`_history_refusal`).
    history_refusal = _history_refusal(account, definitions)
    if history_refusal is not None:
        return _archive_instead_of_delete(account, account_id, history_refusal)

    # Guard 5: posting-ledger history (Build-Order Step 2).  A settled
    # transfer wrote balanced journal entries onto this account's linked
    # ledger account; they are immutable history that survives a transfer
    # delete (it removes the transfer's movements, which SET-NULLs their
    # entries' ``journal_entries.transaction_entry_id``), so the account can
    # still hold posting legs after its transactions are gone (e.g. its ad-hoc
    # transfer was hard-deleted).  The check spans every KIND the account
    # carries, which is what covers an anchor correction re-pointed between
    # two counter rows (ruling R-FO): it has no linked leg at all.
    # Hard-deleting it would CASCADE-delete only
    # its own legs and strand the paired legs as unbalanced single-leg entries
    # (the balanced trigger does not fire on DELETE), so archive instead --
    # restoring the LedgerAccount cascade-imbalance impossibility premise
    # (``app/models/ledger_account.py``).
    if archive_helpers.account_has_ledger_postings(account.id):
        return _archive_instead_of_delete(
            account, account_id,
            f"'{account.name}' has posting-ledger history and cannot be "
            "permanently deleted. It has been archived instead.",
        )

    # All guards passed -- permanently delete.
    # Step 1: delete remaining Transfer rows (soft-deleted or ghost
    # ad-hoc) through the transfer service, which every write of a transfer
    # goes through (CLAUDE.md, Architecture).
    remaining_transfers = (
        db.session.query(Transfer)
        .filter(db.or_(
            Transfer.from_account_id == account_id,
            Transfer.to_account_id == account_id,
        ))
        .all()
    )
    for xfer in remaining_transfers:
        transfer_service.delete_transfer(xfer.id, current_user.id, soft=False)

    # Step 2: delete remaining Transaction rows (soft-deleted ghosts
    # whose RESTRICT FK would block the account deletion).
    db.session.query(Transaction).filter(
        Transaction.account_id == account_id,
    ).delete(synchronize_session="fetch")

    # Step 2b: dispose of the rule-less definitions guard 3 let through.
    # Every one of them holds no live row and no settled row (guard 4
    # archived the account otherwise), so each defines nothing; the
    # soft-deleted, non-settled ghosts it still names -- on this account,
    # already gone at step 2, or on another -- are deleted, never unlinked,
    # because it is the SAME act the definition's own hard-delete performs.
    # None of them, and none of steps 1-2's rows or transfers, holds a
    # payment or purchase: guard 4 archived the account on a live one, and
    # the database refuses a hidden one holding one (rulings R-CC89,
    # R-CC92), so step 1's removal act and these deletes take no movement.
    # Only a state written past the database's triggers holds one, and the
    # two halves meet it differently: step 1 deletes a hidden transfer for
    # good through ``delete_transfer``, whose removal act takes its record
    # off the books first (they stay balanced), while step 2's delete of a
    # hidden row holding one meets ``fk_transaction_entries_transaction_id``.
    for definition in definitions:
        definition_delete.permanently_delete_definition(definition)

    # Step 3: explicitly delete CASCADE-FK dependents that lack ORM
    # relationships on Account.  Without explicit relationships,
    # SQLAlchemy's unit of work tries to SET NULL on their account_id
    # column before the DB-level CASCADE fires, violating NOT NULL.
    db.session.query(LoanParams).filter_by(account_id=account_id).delete()
    db.session.query(InterestParams).filter_by(account_id=account_id).delete()
    db.session.query(InvestmentParams).filter_by(account_id=account_id).delete()
    db.session.query(AssetAppreciationParams).filter_by(
        account_id=account_id,
    ).delete()
    # Escrow lines cascade their versions at the DB tier (ondelete=CASCADE on
    # ``line_id``); the bulk delete here clears the parent lines whose
    # ``Account.escrow_lines`` relationship would otherwise have the unit of work
    # attempt a NOT-NULL-violating SET NULL before the account delete.
    db.session.query(EscrowLine).filter_by(account_id=account_id).delete()
    db.session.query(RateHistory).filter_by(account_id=account_id).delete()
    db.session.query(SavingsGoal).filter_by(account_id=account_id).delete()

    # Step 4: delete the account.  AccountAnchorHistory, AccountOpening and
    # LoanAnchorEvent are append-only (plan step X-f3c-2c), so the ORM must
    # NOT touch them: ``Account.anchor_history`` carries
    # ``passive_deletes="all"`` and no cascade, and PostgreSQL's own
    # ON DELETE CASCADE disposes of all three.  Their audit triggers conserve
    # every destroyed row in ``system.audit_log`` first.
    # The DELETE narrows by version_id thanks to the optimistic-lock
    # contract; a concurrent UPDATE that bumped the version since
    # this request loaded the row raises StaleDataError, which the
    # handler converts into a flash + redirect rather than a 500.
    account_name = account.name
    db.session.delete(account)
    conflict = commit_or_handle_stale(StaleConflictContext(
        logger=logger,
        log_label="hard_delete_account",
        log_id=account_id,
        flash_message=(
            "This account was changed by another action.  Please reload "
            "the page and try again."
        ),
        redirect=RedirectTarget("savings.dashboard"),
    ))
    if conflict is not None:
        return conflict

    flash(f"Account '{account_name}' permanently deleted.", "info")
    return redirect(url_for("savings.dashboard"))
