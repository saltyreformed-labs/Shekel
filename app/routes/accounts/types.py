"""
Shekel Budget App -- Account Type CRUD Routes

Account-type CRUD for the per-user custom catalogue
(``ref.account_types``).  Split out of the historical monolithic
``app/routes/accounts.py`` in Commit 21 of the financial-calculation
audit follow-up (F-1); behaviour preserved verbatim from the
pre-split file.

The C-28 / F-044 multi-tenant ownership rule applies throughout:
seeded built-ins carry ``user_id IS NULL`` and are read-only to
every owner; custom rows carry the creating owner's ``user_id`` and
are invisible to other owners.  Cross-owner mutations collapse into
the same 404 response as a non-existent row, matching the project's
"404 for both 'not found' and 'not yours'" security rule.
"""

import logging

from flask import abort, flash, redirect, request, url_for
from flask_login import current_user

from app.extensions import db
from app.models.account import Account
from app.models.ref import AccountType
from app.routes.accounts._bp import accounts_bp
from app.services.account_params import ensure_type_params
from app.services.account_projection import classify_account
from app.utils.account_validation import (
    _owned_account_type,
    _type_create_schema,
    _type_update_schema,
    _validate_account_type_boundary_edit,
)
from app.utils.auth_helpers import require_owner

logger = logging.getLogger(__name__)


# ── Account Type CRUD ──────────────────────────────────────────────


@accounts_bp.route("/accounts/types", methods=["POST"])
@require_owner
def create_account_type():
    """Create a new account type owned by the current user.

    The new row carries ``user_id = current_user.id`` (commit C-28 /
    F-044).  Seeded built-ins (``user_id IS NULL``) are only created
    by ``app.ref_seeds.seed_reference_data`` and are read-only to every
    owner; this route never inserts a built-in.

    The duplicate-name check is scoped to the caller's own types so
    that an owner may legitimately create a custom type with the
    same name as a seeded built-in (per the C-28 acceptance
    criteria) and so that two different owners can both have a
    custom "Crypto" without conflict.  The matching partial unique
    index ``uq_account_types_user_name`` is the storage-tier
    backstop if a concurrent request slips past this check.
    """
    errors = _type_create_schema.validate(request.form)
    if errors:
        flash("Please correct the highlighted errors and try again.", "danger")
        return redirect(url_for("settings.show", section="account-types"))

    data = _type_create_schema.load(request.form)

    # Per-user duplicate name guard.  Only conflicts with the
    # caller's own custom types should reject the create -- a name
    # that exists only as a seeded built-in is allowed (the user is
    # making a per-user copy) and a name that exists only as a
    # different owner's custom type is invisible from here, so it
    # cannot collide.
    existing = (
        db.session.query(AccountType)
        .filter_by(name=data["name"], user_id=current_user.id)
        .first()
    )
    if existing:
        flash("An account type with that name already exists.", "warning")
        return redirect(url_for("settings.show", section="account-types"))

    account_type = AccountType(user_id=current_user.id, **data)
    db.session.add(account_type)
    db.session.commit()

    logger.info(
        "Created account type: %s (id=%d, user_id=%d)",
        account_type.name, account_type.id, current_user.id,
    )
    flash(f"Account type '{account_type.name}' created.", "success")
    return redirect(url_for("settings.show", section="account-types"))


@accounts_bp.route("/accounts/types/<int:type_id>", methods=["POST"])
@require_owner
def update_account_type(type_id):
    """Update one of the current user's own account types.

    Ownership guard (commit C-28 / F-044): the row must exist and
    its ``user_id`` must match the caller.  Seeded built-ins
    (``user_id IS NULL``) and other owners' custom types are
    indistinguishable from a non-existent row in the response, per
    the project's "404 for both 'not found' and 'not yours'" rule.
    The flash + redirect behaviour matches the rest of the form-POST
    handlers in this file (this route is not HTMX-driven).
    """
    account_type = _owned_account_type(type_id, current_user.id)
    if account_type is None:
        abort(404)

    errors = _type_update_schema.validate(request.form)
    if errors:
        flash("Please correct the highlighted errors and try again.", "danger")
        return redirect(url_for("settings.show", section="account-types"))

    data = _type_update_schema.load(request.form)

    # Per-user duplicate-name guard on rename.  Identical scoping to
    # ``create_account_type`` -- the conflict universe is the
    # caller's own custom types only.  ``id != type_id`` excludes
    # the row being renamed (a no-op rename must not flag itself).
    if "name" in data:
        existing = (
            db.session.query(AccountType)
            .filter(
                AccountType.name == data["name"],
                AccountType.id != type_id,
                AccountType.user_id == current_user.id,
            )
            .first()
        )
        if existing:
            flash("An account type with that name already exists.", "warning")
            return redirect(url_for("settings.show", section="account-types"))

    # The loan-boundary guard (ruling plan#2785): an in-place edit of
    # ``has_amortization`` that crosses between a loan type and a non-loan
    # type is refused while any account of this type holds a line of the
    # worked-out ledger -- the second crossing vector (C4 adversarial review
    # M2), with no ``account_type_id`` change for the account-update guard to
    # see.
    failure = _validate_account_type_boundary_edit(account_type, data)
    if failure is not None:
        flash(failure[0], failure[1])
        return redirect(url_for("settings.show", section="account-types"))

    for field in ("name", "category_id", "has_parameters", "has_amortization",
                  "has_interest", "is_pretax", "is_liquid", "icon_class",
                  "max_term_months"):
        if field in data:
            setattr(account_type, field, data[field])

    # **This is the THIRD door into an account's projection KIND** (plan step
    # balance:X-i3): ``has_interest`` and ``has_parameters`` are both editable
    # here, so one form post can turn every account already on this type into
    # an interest or investment account -- with no ``account_type_id`` change
    # for the account-update door to see, and no account row touched at all.
    # Until this loop the params rows those kinds require were written by the
    # CREATE and RE-CLASS doors only, and two detail pages repaired the gap on
    # a GET.
    #
    # Run over EVERY account of the type rather than over the ones whose kind
    # changed, and the seeder's idempotence is what makes that right: deciding
    # which changed would mean a second copy of ``classify_account``'s branch
    # order, which is the second answer to one question this package deletes.
    # The query autoflushes the dirtied type, and ``classify_account`` reads
    # each account's ``account_type`` relationship -- that same session-held
    # row -- so it sees the new flags.
    affected_accounts = (
        db.session.query(Account)
        .filter_by(account_type_id=type_id, user_id=current_user.id)
        .all()
    )
    for account in affected_accounts:
        ensure_type_params(account, classify_account(account))

    # An ALLOWED crossing (an Asset/Liability one, or a loan one holding no
    # line, per the guard above) needs nothing more: the worked-out ledger
    # classes each line by its account's current type, and reads a kind flag
    # such as ``has_interest`` the same way.  *Until plan task plan#3076 a
    # crossing re-classed each account's stored linked row and re-synced its
    # stored anchor corrections here, and a kind flag re-synced nothing, so the
    # store held the old reading (finding plan#2883); no door writes the store
    # now.*
    db.session.commit()

    logger.info(
        "Updated account type: %s (id=%d, user_id=%d)",
        account_type.name, account_type.id, current_user.id,
    )
    flash(f"Account type '{account_type.name}' updated.", "success")
    return redirect(url_for("settings.show", section="account-types"))


@accounts_bp.route("/accounts/types/<int:type_id>/delete", methods=["POST"])
@require_owner
def delete_account_type(type_id):
    """Delete one of the current user's own account types.

    Two guards apply, in order:

      1. Ownership (commit C-28 / F-044) -- only the row's owner may
         delete it.  Seeded built-ins are read-only; cross-owner
         deletes return the same response as a non-existent row.
      2. In-use check -- a custom type referenced by any of this
         owner's accounts blocks the delete because the
         ``budget.accounts.account_type_id`` FK would otherwise
         dangle.  The check is scoped to ``user_id = current_user.id``
         for clarity; after C-28 only the owner can have accounts
         referencing their custom type, so the unscoped query would
         return the same set, but the scoped form makes the intent
         explicit.
    """
    account_type = _owned_account_type(type_id, current_user.id)
    if account_type is None:
        abort(404)

    in_use = (
        db.session.query(Account)
        .filter_by(account_type_id=type_id, user_id=current_user.id)
        .first()
    )
    if in_use:
        flash(
            "Cannot delete this account type -- it is in use by one or more accounts.",
            "warning",
        )
        return redirect(url_for("settings.show", section="account-types"))

    db.session.delete(account_type)
    db.session.commit()

    logger.info(
        "Deleted account type: %s (id=%d, user_id=%d)",
        account_type.name, type_id, current_user.id,
    )
    flash(f"Account type '{account_type.name}' deleted.", "info")
    return redirect(url_for("settings.show", section="account-types"))
