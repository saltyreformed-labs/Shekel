"""
Shekel Budget App -- Template routes: what a submitted template form may SAY.

:func:`validate_template_form` -- the refusals both the create and the update
route apply before anything is written -- and the per-question predicates behind
it.

**It is a module of its own since plan step balance:X-au-d**, which added the
salary-type refusal and took ``crud.py`` past pylint's 1,000-line ceiling
(ruling **R-IR**: the session that breaks a module splits it).  The cut is a
SUBJECT rather than a size: everything here answers *may this form say this*,
and reads nothing about what a write then does.  ``crud.py`` keeps the routes
and the writes.

Boundary discipline: these are ROUTE-tier checks, so they read ``current_user``
and return flash text.  They issue queries and no writes.
"""

import logging

from flask import flash
from flask_login import current_user

from app import ref_cache
from app.enums import TxnTypeEnum
from app.extensions import db
from app.models.account import Account
from app.models.category import Category
from app.services import template_amount_service
from app.services.account_projection import (
    AccountProjectionKind,
    classify_account,
)
from app.utils import archive_helpers

logger = logging.getLogger(__name__)


def _is_tracking_on_non_expense(data, template=None):
    """Check whether tracking is being set on a non-expense template.

    Defense-in-depth fallback for the cross-field schema validator
    ``validate_envelope_only_on_expense``.  The schema validator catches
    the bug whenever both ``is_envelope`` and ``transaction_type_id``
    appear in the deserialized payload (the normal HTML form path); this
    helper closes the gap on partial updates that omit one field by
    falling back to the existing template's stored value.

    Args:
        data: Deserialized form data from Marshmallow schema.
        template: Existing TransactionTemplate (for updates) or None (for creates).

    Returns:
        True if the combination is invalid (tracking on non-expense), False otherwise.
    """
    track = data.get(
        "is_envelope",
        getattr(template, "is_envelope", False),
    )
    if not track:
        return False
    type_id = data.get(
        "transaction_type_id",
        getattr(template, "transaction_type_id", None),
    )
    return type_id != ref_cache.txn_type_id(TxnTypeEnum.EXPENSE)


def _account_refusal(data, template) -> "str | None":
    """Return why *data*'s account is not a legal target, or ``None``.

    The THREE account questions as one predicate: the account is the user's, it
    is not an amortizing loan, and moving to it does not orphan a standing
    merchant rule.  They are grouped because they are one subject -- *may this
    template point at this account* -- and because keeping them inline put
    :func:`validate_template_form` at seven returns against pylint's six
    (plan step balance:X-au-d, which added a fifth check).

    Args:
        data: The deserialized form data, carrying ``account_id``.
        template: The template being updated, or ``None`` on a create.

    Returns:
        The flash text for the first failed question, or ``None``.
    """
    acct = db.session.get(Account, data["account_id"])
    if not acct or acct.user_id != current_user.id:
        return "Invalid account."
    if classify_account(acct) is AccountProjectionKind.AMORTIZING:
        # N-11 / ruling D4: a loan's balance is ledger-derived, not a
        # transaction sum.  A template targeting a loan would have the
        # recurrence engine generate raw transactions onto the loan
        # account (``recurrence_engine`` copies ``template.account_id``),
        # posting a bare cash leg the fold cannot see -- the same shape
        # the create routes refuse (``_reject_transaction_on_loan``) and
        # the transfer service forbids for a transfer out of a loan (R6).
        return (
            "A loan's balance is not a transaction sum, so a template "
            "cannot target a loan account. Record loan payments as "
            "transfers."
        )
    # **MOVING a template between accounts is refused while a standing
    # merchant rule names it** (plan step ``bank_import:X-gd-2``).
    # ``fk_merchant_rules_template_account`` is composite over
    # ``(template_id, account_id)`` with no ``ON UPDATE``, so the move
    # orphans the rule and PostgreSQL raises -- an IntegrityError that
    # ``commit_or_handle_stale`` does not catch and no handler renders,
    # i.e. an unhandled 500 with a logged traceback.  Cascading the
    # account onto the rule instead would be worse than the error: the
    # rule's merchant belongs to the OLD account
    # (``fk_merchant_rules_merchant_account``), so the row would move to
    # an account whose statements never showed that merchant.
    #
    # The owner's route out is to restate the rule first, which the
    # sentence says.  **That route used to be a withdrawal**, which ruling
    # R-GS removed in this same step -- so the 500 was pre-existing and
    # this step is what made it unrecoverable.  Found by an adversarial
    # security review 2026-08-26 and measured on the developer's own data:
    # template 19 is one edit away from it.
    if (
        template is not None
        and acct.id != template.account_id
        and archive_helpers.template_has_standing_rule(template.id)
    ):
        return (
            f"'{template.name}' is where a merchant's bank spending goes "
            "on its current account, so it cannot be moved to another "
            "one. Change that merchant's answer on the statement review "
            "screen first."
        )
    return None


def validate_template_form(data, on_invalid, template=None):
    """Validate submitted template data against ownership and tracking rules.

    Shared by :func:`create_template` and :func:`update_template`, whose
    create-vs-update difference is only that the create schema makes
    ``account_id`` / ``category_id`` required (always present) while the
    update schema makes them optional -- so guarding each check with
    ``in data`` is correct for both paths.  Checks, in order:

      1. ``account_id`` (when present) names an Account the user owns.
      2. ``account_id`` (when present) is NOT an amortizing loan -- a
         template on a loan would have the recurrence engine generate raw
         transactions onto it (finding N-11 / ruling D4; the create routes
         refuse the same shape via
         :func:`app.routes.transactions.create._reject_transaction_on_loan`).
      3. ``category_id`` (when present) names a Category the user owns.
      4. The resulting envelope-tracking state is expense-only
         (:func:`_is_tracking_on_non_expense`).

    Args:
        data: Deserialized form data (post ``schema.load``).
        on_invalid: Redirect destination for the first failed check.
        template: Existing TransactionTemplate for an update, or ``None``
            for a create, so the tracking check can fall back to the
            stored value on a partial update.

    Returns:
        A redirect ``Response`` for the first failed check, or ``None``
        when every check passes.
    """
    if "account_id" in data:
        refusal = _account_refusal(data, template)
        if refusal is not None:
            flash(refusal, "danger")
            return on_invalid.to_response()
    if "category_id" in data:
        cat = db.session.get(Category, data["category_id"])
        if not cat or cat.user_id != current_user.id:
            flash("Invalid category.", "danger")
            return on_invalid.to_response()
    if _is_tracking_on_non_expense(data, template):
        flash("Purchase tracking is only available for expense templates.", "danger")
        return on_invalid.to_response()
    # **A SALARY-LINKED definition stays INCOME** (finding **N-253**, plan step
    # balance:X-au-d).  A salary profile states a NET PAY, so
    # ``income_service.salary_net_for`` answers for an income row and nothing
    # else -- while ``cash_ledger._amount_source._rule_within_definition``
    # classifies by the DEFINITION and would still place the row under amount
    # rule 2.  The classifier and its producer would then disagree, and after
    # X-au-d that disagreement is a REFUSAL on a row that stores no figure:
    # ``regenerate_or_conflict_chooser`` never asks about a salary-linked
    # template (its edit writes the price series nothing, plan step X-bp),
    # so this save silently flips every projected paycheck in the
    # write window to Expense, and ``routes/grid/page`` prices every row it
    # loads with no status gate and no ``try`` -- a 500 on the primary screen
    # with no in-app repair.  Found by an adversarial review of this step.
    #
    # It is refused HERE rather than answered in the resolver because no rule
    # CAN price such a row: the profile states an income figure and the
    # template's own series is dormant while it is salary-linked
    # (``template_amount_service.owns_its_amount``).  The honest act is to
    # refuse the edit that creates it.
    if _changes_type_of_a_salary_template(data, template):
        flash(
            "This is a paycheck template -- a salary profile computes what "
            "its rows are worth, and a salary profile states income. Archive "
            "the salary profile first if this is no longer a paycheck.",
            "danger",
        )
        return on_invalid.to_response()
    return None


def _changes_type_of_a_salary_template(data, template) -> bool:
    """Return whether this edit would change a salary-linked template's TYPE.

    Args:
        data: The deserialized form data.
        template: The template being updated, or ``None`` on a create -- a
            template cannot be salary-linked before it exists, so a create is
            always ``False``.

    Returns:
        ``True`` when an ACTIVE salary profile names *template* and the
        submitted ``transaction_type_id`` differs from the stored one.
    """
    if template is None or "transaction_type_id" not in data:
        return False
    return (
        data["transaction_type_id"] != template.transaction_type_id
        and template_amount_service.is_salary_linked_template(template)
    )
