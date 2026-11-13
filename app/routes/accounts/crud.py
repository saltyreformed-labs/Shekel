"""
Shekel Budget App -- Account CRUD Routes

Account create / read / update endpoints.
Split out of the historical monolithic ``app/routes/accounts.py``
in Commit 21 of the financial-calculation audit follow-up (F-1);
behaviour preserved verbatim from the pre-split file.  The archive,
unarchive and hard-delete doors moved to
:mod:`app.routes.accounts.lifecycle` (task plan#3697).

The optimistic-lock contract (commit C-17 / F-009) operates at two
tiers in this module's update route: a
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
from sqlalchemy.orm.exc import StaleDataError

from app.exceptions import ValidationError
from app.extensions import db
from app.models.account import Account
from app.models.pay_period import PayPeriod
from app.models.ref import AccountType
from app.routes._commit_helpers import (
    StaleConflictContext,
    commit_or_handle_stale,
    handle_stale_conflict,
)
from app.routes._redirect_target import RedirectTarget
from app.routes._transfer_lifecycle import archive_transfer_definition
from app.routes.accounts._bp import accounts_bp
from app.routes.accounts.opening import books_opening_context
from app.services import (
    account_posting_service,
    account_service,
    card_payoff,
    ledger_account_service,
    liability_sign,
    pay_period_service,
)
from app.services.account_params import ensure_type_params
from app.services.account_projection import (
    AccountProjectionKind,
    classify_account,
    is_revolving,
    is_revolving_type,
)
from app.services.account_resolver import resolve_grid_account
from app.services.balance_at import BalanceContext
from app.utils.account_validation import (
    _create_schema,
    _validate_update_account,
    _visible_account_type,
    _visible_account_types,
)
from app.utils.auth_helpers import get_or_404, require_owner
from app.utils.dates import display_today

logger = logging.getLogger(__name__)

# Field allowlist for the account update route: which submitted form
# fields may be written back to the Account via setattr.  ``is_active`` is not
# one (task plan#3697): the archive and unarchive doors
# (:mod:`app.routes.accounts.lifecycle`) are the one writer of it, so a card's
# payment is archived and restored with the card however the flag moves, and
# the archive's refusal of an account active recurring transfers name is
# never bypassed.  No form here renders the field; only a forged POST sent it.
_ACCOUNT_UPDATE_FIELDS = {"name", "account_type_id", "sort_order"}


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


def _settle_card_payoff(account_type, name, *, becoming=None):
    """Return the payment a door bringing a card into being makes, settled before its write.

    Every card has one payment (task plan#2933), and the two doors that bring
    a card into being both call this: account creation, and a re-type into a
    revolving type.  The refusals that payment can meet are asked here, before
    any write, so a card nothing can pay is refused with nothing left behind.
    The payer is the owner's primary, resolved before the type changes, so the
    resolver reads the accounts as they are; the account becoming a card may
    be that primary, which :func:`app.services.card_payoff.plan_payoff`
    refuses.

    Args:
        account_type: The type the account will have, owner-checked.
        name: The account's name as it will be.
        becoming: For a re-type, the account whose type changes; ``None`` for
            a card not yet made.  An account that is a card already is not
            brought into being, so a save that keeps a card a card makes
            nothing.

    Returns:
        The :class:`~app.services.card_payoff.Payoff` to make once the card
        exists, or ``None`` when no card comes into being.

    Raises:
        ValidationError: From :func:`app.services.card_payoff.plan_payoff`,
            with the sentence the owner reads.
    """
    if not is_revolving_type(account_type):
        return None
    if becoming is not None and is_revolving(becoming):
        return None
    return card_payoff.plan_payoff(
        current_user.id, name,
        resolve_grid_account(current_user.id, current_user.settings),
        card_id=None if becoming is None else becoming.id,
    )


def _settle_retype_payoff(account, data):
    """Return the payment a re-type into a card makes, settled before the re-type.

    :func:`_settle_card_payoff` for the update door, asked of what the save
    will leave: the submitted type and name, or the account's own where the
    form sent none.

    Args:
        account: The owned account being saved, its type the current one.
        data: The update payload, every gate passed
            (``_validate_update_account``), so a submitted type is visible to
            the owner.

    Returns:
        The :class:`~app.services.card_payoff.Payoff` to make after the
        re-type, or ``None`` when the save brings no card into being.

    Raises:
        ValidationError: From :func:`app.services.card_payoff.plan_payoff`,
            with the sentence the owner reads.
    """
    return _settle_card_payoff(
        db.session.get(
            AccountType, data.get("account_type_id", account.account_type_id),
        ),
        data.get("name", account.name),
        becoming=account,
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
        payoff = _settle_card_payoff(account_type, name)
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
        # Two destinations: the user has NO pay periods (send them to generate
        # some), or every other refusal -- the "balance as of" day out of
        # bounds, or a card nothing can pay (task plan#2933) -- goes back to
        # the form with the service's own sentence.
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
    # The card's one payment, settled above; its read pass is built now that
    # the card exists.
    if payoff is not None:
        card_payoff.make_payoff(
            account, payoff, BalanceContext.build(current_user.id),
        )
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

    # A re-type into a card brings a card into being, so it makes the card's
    # one payment (task plan#2933): its refusals are asked here, before the
    # first setattr, so a card nothing can pay leaves the account untouched.
    try:
        payoff = _settle_retype_payoff(account, data)
    except ValidationError as exc:
        flash(str(exc), "warning")
        return redirect(url_for("accounts.edit_account", account_id=account_id))

    old_type_id = account.account_type_id
    for field, value in data.items():
        if field in _ACCOUNT_UPDATE_FIELDS:
            setattr(account, field, value)
    type_changed = account.account_type_id != old_type_id

    # The side effects run inside a stale-race guard, as the commit does,
    # because the resync below flushes the pending Account mutation -- the
    # version-pinned WHERE clause is checked at flush time, so
    # ``StaleDataError`` would otherwise escape outside the catch.
    # See the matching comment in :func:`true_up`.

    def _reconcile_type_effects():
        """Reconcile the type-change side effects (in-transaction step).

        Re-class the stored linked ledger row when the Asset/Liability
        boundary was crossed, history or not (ruling plan#2785) -- the
        worked-out ledger classes each line by the account's current type when
        it reads it -- and re-sync the account's Step-5 anchor corrections so
        an amortizing-boundary crossing, which ``_validate_update_account``
        allows only on an account holding no line, swaps correction families
        instead of stranding one (the sync structurally no-ops for the loan
        side).

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

        **It makes the card's one payment when the re-type brings a card into
        being** (task plan#2933), settled before the setattr loop, through the
        same :func:`app.services.card_payoff.make_payoff` account creation
        calls.  Last, after the re-class and the re-sync, and on a read pass
        built then, so the pass reads the account as the card it now is.  The
        first row follows from when the account's books opened (rulings
        R-PC85, R-PC86): an account whose books opened before this paycheck's
        payday is paid from that payday (ruling plan#2669), as a payment set up
        by hand is, and one opened later is paid from the next.

        Returns:
            The payment it made, or ``None`` when the save brought no card
            into being.
        """
        if not type_changed:
            return None
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
        if payoff is None:
            return None
        return card_payoff.make_payoff(
            account, payoff, BalanceContext.build(current_user.id),
        )

    ctx = StaleConflictContext(
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
    )
    # The reconcile step must run inside the same stale-race guard as
    # the commit (it flushes and can itself raise StaleDataError).
    try:
        made = _reconcile_type_effects()
    except StaleDataError:
        return handle_stale_conflict(ctx)
    # **A re-type of an ARCHIVED account into a card archives the payment it
    # just made** (task plan#3697), through the transfers' own archive act,
    # the one the card's own archive door performs: an archived card's
    # payment is archived however the card came to be archived, and an
    # active payment on an archived card is the state that door refuses to
    # leave.  Unarchiving the card brings it back.
    if made is None or account.is_active:
        conflict = commit_or_handle_stale(ctx)
        if conflict is not None:
            return conflict
    else:
        # The act commits the re-type with the payment's archive.
        archived = archive_transfer_definition(made, ctx)
        if not isinstance(archived, tuple):
            return archived

    logger.info("Updated account: %s (id=%d)", account.name, account.id)
    flash(f"Account '{account.name}' updated.", "success")
    return redirect(url_for("savings.dashboard"))
