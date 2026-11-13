"""
Shekel Budget App -- Account Validation Helpers

Shared validation helpers and Marshmallow schema singletons for the
``app.routes.accounts`` package.  Extracted in Commit 21 of the
financial-calculation follow-up remediation (F-1) when the monolithic
``app/routes/accounts.py`` was split into per-sub-domain modules.

The helpers fall into two groups:

* **Multi-tenant ``ref.account_types`` ownership** (commit C-28 /
  F-044).  ``_visible_account_types`` enumerates the seeded built-ins
  plus the caller's own custom types; ``_owned_account_type`` looks
  up a single owned row for per-type mutation routes;
  ``_account_type_is_visible`` is the route-layer guard that prevents
  a forged form from re-parenting an account onto another owner's
  custom type.

* **``Account`` update validation** (consolidates the four early-
  return gates of the ``update_account`` route into a single helper).
  ``_validate_update_account`` returns a ``(data, failure)`` tuple
  so the route owns the flash + redirect composition while this
  module owns the business validation.

* **The loan-boundary type guard** (ruling plan#2785).  A re-type of an
  account (``_validate_account_type_change``) or an in-place edit of a custom
  type's ``has_amortization`` (``_validate_account_type_boundary_edit``) that
  crosses between a loan type and a non-loan type is refused while the
  affected account(s) hold a line of the worked-out ledger -- see
  ``_crosses_amortizing_boundary`` for why.  Crossing between an asset and a
  liability is not refused: a line is classed by its account's current type,
  as it is labeled by its current name, and an account's past true-ups are
  re-read under that type too, past income included (ruling plan#2880).  Both
  also refuse moving an account between savings and debt while an active
  savings goal is on it, or between a loan type and a card-style debt (plan
  step credit_card:CC-5-5d, rulings R-CC87 and R-CC91):
  ``_refuse_goal_crossing``.  A re-type also keeps a card that a worked-out
  card payment names a card (plan step credit_card:CC-6).

The Marshmallow schema singletons (``_anchor_schema``,
``_create_schema``, ``_update_schema``, ``_type_create_schema``,
``_type_update_schema``, ``_interest_params_schema``,
``_opening_schema``) are also kept
here so every sub-domain module in the accounts package imports the
same instance -- preserving the pre-split behaviour where each
schema was constructed exactly once at module load.

Services boundary: this module is a route-layer helper, not a
service.  It imports the SQLAlchemy ``db`` proxy and reads the
SQLAlchemy ``Account`` / ``AccountType`` rows directly because it
exists to keep route bodies thin.  No Flask request globals are
touched -- callers pass the current user id in explicitly.
"""

from app import ref_cache
from app.enums import AcctCategoryEnum
from app.extensions import db
from app.models.account import Account
from app.models.ref import AccountType
from app.models.savings_goal import SavingsGoal
from app.schemas.validation import (
    AccountCreateSchema,
    AccountTypeCreateSchema,
    AccountTypeUpdateSchema,
    AccountUpdateSchema,
    AnchorUpdateSchema,
    OpeningRestatementSchema,
    AppreciationParamsUpdateSchema,
    InterestParamsUpdateSchema,
)
from app.services.account_category import is_liability_category
from app.services.card_payoff import card_payment_on
from app.services.ledger_lines import accounts_hold_lines, linked_ledger_class
from app.services.recurring_transfer_query import due_date_repeats_into


# Marshmallow schema singletons.  Constructed once per process and
# imported by every sub-module of ``app.routes.accounts`` so the pre-
# split behaviour ("one schema instance shared across every endpoint
# that consumes it") is preserved.
_anchor_schema = AnchorUpdateSchema()
_appreciation_params_schema = AppreciationParamsUpdateSchema()
_create_schema = AccountCreateSchema()
_update_schema = AccountUpdateSchema()
_type_create_schema = AccountTypeCreateSchema()
_type_update_schema = AccountTypeUpdateSchema()
_interest_params_schema = InterestParamsUpdateSchema()
_opening_schema = OpeningRestatementSchema()


#: THE answer to "this collateral submission does not name an account you
#: own", as a ready-to-``flash`` ``(message, category)`` pair.  Not-found,
#: not-yours and not-an-id-at-all deliberately collapse into one response
#: -- the project's "404 for both not-found and not-yours" rule expressed
#: as a flash -- so the picker cannot be used to probe for another owner's
#: account ids.  Named rather than inlined because :func:`_validate_collateral_link`
#: is no longer its only emitter: the route rejects a value that names no id
#: at all before this validator is reached (plan step X-ae), and two spellings
#: of one answer is how the two paths would come to differ.
INVALID_COLLATERAL_LINK = ("Invalid linked account.", "danger")

#: The refusal of a re-type that would leave a worked-out card payment on an
#: account that is not a card (plan step credit_card:CC-6): only a credit
#: card's balance, statement and minimum exist to work its payment out from.
#: Formatted with the card's name and the payment's.  The way out it names is
#: always open: deleting the payment can be refused for history (it is then
#: archived, and its settings stay), but a new account never is.
CARD_PAYMENT_KEEPS_ITS_CARD = (
    "'{card}' has a card payment, '{payment}', that works out what to pay "
    "from the card's balance, so it must stay a credit card. To use another "
    "type, create a new account of that type."
)


#: The refusal of a re-type that would leave a repeat on a card's due dates
#: paying no card (plan step credit_card:CC-6).  A plain transfer can use that
#: repeat and carries no settings row, so
#: :data:`CARD_PAYMENT_KEEPS_ITS_CARD` does not reach it; its door to change is
#: the transfer's own form, so the sentence names that first.
DUE_DATE_REPEAT_KEEPS_ITS_CARD = (
    "'{payment}' repeats on each statement due date of '{card}', so the "
    "account must stay a credit card. Change how '{payment}' repeats first, "
    "or create a new account of the other type."
)


def _visible_account_types(user_id):
    """Return the account types this user is allowed to see.

    Built-in types (``user_id IS NULL``) are visible to every owner;
    a user's own custom types are visible only to them.  Other
    owners' custom types are excluded so the settings page and the
    account-form dropdown cannot leak the existence of one user's
    custom catalogue to another user (commit C-28 / F-044).

    Args:
        user_id: ``auth.users.id`` of the current owner.

    Returns:
        list[AccountType] -- ordered by ``name`` for stable rendering.
        Includes the seeded built-ins (each ``AcctTypeEnum`` member)
        plus every row whose ``user_id`` matches the caller.
    """
    return (
        db.session.query(AccountType)
        .filter(db.or_(
            AccountType.user_id.is_(None),
            AccountType.user_id == user_id,
        ))
        .order_by(AccountType.name)
        .all()
    )


def _owned_account_type(type_id, user_id):
    """Return the account type if owned by this user, else ``None``.

    Used by the per-type mutation routes (``update``, ``delete``) to
    enforce the C-28 ownership guard.  A ``None`` return collapses
    the three "type does not exist", "type belongs to another owner",
    and "type is a seeded built-in" cases into a single
    indistinguishable response, matching the project's
    "404 for both 'not found' and 'not yours'" security rule.

    Args:
        type_id: Primary key of the candidate ``ref.account_types`` row.
        user_id: ``auth.users.id`` of the current owner.

    Returns:
        AccountType when the row exists and ``user_id`` matches;
        ``None`` otherwise.
    """
    account_type = db.session.get(AccountType, type_id)
    if account_type is None or account_type.user_id != user_id:
        return None
    return account_type


def _account_type_is_visible(type_id, user_id):
    """Return True iff ``type_id`` references a seeded or owned type.

    Account create/update accept ``account_type_id`` from the form.
    Before C-28 every type was global, so the FK constraint alone
    sufficed; afterwards an owner forging a POST could attach their
    new account to another owner's custom type, leaking that type's
    existence and producing a cross-user FK reference that C-29's
    re-parenting guard does not cover for the account row itself.
    This helper is the route-layer guard that pairs with the new
    multi-tenant ownership rule on ``ref.account_types``: the
    ``account_type_id`` must point at a seeded built-in
    (``user_id IS NULL``) or at one of the caller's own types.

    Args:
        type_id: Submitted ``ref.account_types.id`` value.
        user_id: ``auth.users.id`` of the current owner.

    Returns:
        bool -- True when the type exists and is either seeded or
        owned by *user_id*; False otherwise.  Identical False for
        "does not exist" and "owned by another user" so the
        response cannot be used to enumerate other owners' types.
    """
    return _visible_account_type(type_id, user_id) is not None


def _visible_account_type(type_id, user_id):
    """Return the account type ``type_id`` names if this user may use it, else ``None``.

    :func:`_account_type_is_visible`'s rule, answering with the ROW -- for the
    create route, which needs the type itself to cross the balance the owner
    typed (plan step credit_card:CC-5-5b, ruling R-CC52).  Asking the boolean
    and then ``db.session.get`` again was a second read of one row: the
    session's identity map holds weak references, so the first lookup's row
    was gone by the second and a probe counted the extra SELECT (the CC-5-5b
    adversarial review, finding L1).

    Args:
        type_id: Submitted ``ref.account_types.id`` value.
        user_id: ``auth.users.id`` of the current owner.

    Returns:
        The :class:`AccountType` when it exists and is seeded or owned by
        *user_id*; ``None`` otherwise, for both "does not exist" and "owned
        by another user" -- the same indistinguishable answer.
    """
    account_type = db.session.get(AccountType, type_id)
    if account_type is None:
        return None
    if account_type.user_id is None or account_type.user_id == user_id:
        return account_type
    return None


def _crosses_amortizing_boundary(old_type: AccountType, new_amortization: bool) -> bool:
    """Return True iff a type change moves an account between loan and non-loan.

    The kind decides which records an account's history is worked out from: a
    plain account's balance records, or a loan's terms and statements, so
    re-typing Checking to a loan type with no loan terms would drop its openings
    and true-ups from every statement (ruling plan#2785).  Step plan#2786 makes
    that re-type the loan's setup, and deletes the refusal this feeds.

    Args:
        old_type: The account's current :class:`~app.models.ref.AccountType`.
        new_amortization: The proposed ``has_amortization`` value.

    Returns:
        bool -- True when the flag flips.
    """
    return bool(old_type.has_amortization) != bool(new_amortization)


def _crosses_posting_boundary(
    old_type: AccountType, new_amortization: bool, new_category_id: int,
) -> bool:
    """Return True iff a type edit leaves the stored chart to bring back in step.

    The upkeep of the stored ledger, which X-ew's third leaf (plan#2516)
    deletes, and refuses nothing: an amortizing crossing swaps which family of
    corrections the account books, and an Asset/Liability crossing flips the
    class its stored linked row was stamped with (per
    :func:`app.services.ledger_lines.linked_ledger_class`).  A flag that changes
    the account's kind alone, such as ``has_interest``, is neither: the worked-out
    ledger re-reads it at once (ruling plan#2880), and the stored one holds the old
    reading until the account's next balance (finding plan#2883).

    Args:
        old_type: The account's current :class:`~app.models.ref.AccountType`.
        new_amortization: The proposed ``has_amortization`` value.
        new_category_id: The proposed ``category_id`` value.

    Returns:
        bool -- True when either is crossed.
    """
    if _crosses_amortizing_boundary(old_type, new_amortization):
        return True
    return (
        linked_ledger_class(old_type.category_id)
        is not linked_ledger_class(new_category_id)
    )


#: Rulings R-CC87 / R-CC91's refusal, as a ready-to-``flash``
#: ``(message, category)`` pair, for re-typing an account -- or editing its
#: custom type -- between savings and debt, or between a loan type and a
#: card-style debt, while an active goal is on it.
GOAL_BLOCKS_DEBT_CROSSING = (
    "A goal is on this account, and its type cannot change between savings "
    "and debt, or between a loan type and a card-style debt, while the goal "
    "is on it.  Delete the goal first, then change the type.",
    "warning",
)


def _goal_kind(category_id, has_amortization):
    """Return what a goal on an account of this type IS: ``(is_debt, is_loan_type)``.

    The two facts a goal's meaning hangs on (plan step credit_card:CC-5-5d):
    whether it is a milestone to get under at all (a liability, ruling R-CC69),
    and, for a debt, whether its start is re-read from the books (a loan type,
    whose tile reads the day) or recorded when the goal was created (any
    other debt, whose tile reads the pay period's end; ruling R-CC91).  A
    savings type's amortization flag is not part of it.

    Args:
        category_id: The type's ``ref.account_type_categories`` id.
        has_amortization: The type's ``has_amortization`` flag.

    Returns:
        ``(is_debt, is_loan_type)``, the second ``False`` for a savings type.
    """
    is_debt = is_liability_category(category_id)
    return is_debt, is_debt and bool(has_amortization)


def _refuse_goal_crossing(account_ids, was, becomes):
    """Refuse changing what a goal on these accounts IS while a goal is on one.

    Rulings **R-CC87** ("Re-typing an account between savings and debt is also
    refused while a goal is on it") and **R-CC91** ("Changing an account
    between a card-style and a loan-style type is then refused while a goal is
    on it, so a saved start can never land on a loan"), plan step
    credit_card:CC-5-5d.  A savings goal has no start; a loan goal re-reads its
    start from the books on its creation day; a card-style goal RECORDED its
    start when it was created.  A goal whose account changed kind under it
    would read a start it does not have, or one from a rule that was not its
    own.  The goal door refuses the same move when the GOAL is edited
    (:mod:`app.services.savings_goal_door`); this is the other door to it,
    through the account's type.

    Only an ACTIVE goal refuses: a deleted goal is deactivated, and no save
    re-activates one (its update form carries no ``is_active``).

    Args:
        account_ids: The accounts whose type would change.
        was: :func:`_goal_kind` of their type now.
        becomes: :func:`_goal_kind` of their type after the change.

    Returns:
        :data:`GOAL_BLOCKS_DEBT_CROSSING` when the change alters what a goal
        on them is and an active goal is on one of the accounts; ``None``
        otherwise.
    """
    if was == becomes or not account_ids:
        return None
    goal_on_them = (
        db.session.query(SavingsGoal.id)
        .filter(
            SavingsGoal.account_id.in_(account_ids),
            SavingsGoal.is_active.is_(True),
        )
        .first()
    )
    return None if goal_on_them is None else GOAL_BLOCKS_DEBT_CROSSING


def _validate_account_type_change(account, new_type_id):
    """Refuse a re-type across the loan boundary of an account with history.

    An ``account_type_id`` change between a loan type and a non-loan type
    (:func:`_crosses_amortizing_boundary`) is refused while the account holds
    a line of the worked-out ledger (:func:`accounts_hold_lines`, ruling
    plan#2785).  Any other change passes, an Asset/Liability crossing with
    history included, and so does a crossing on an account holding no line (a
    $0-anchor account); the route then re-classes its stored linked row and
    re-syncs the corrections.

    **A re-type to a type that is not revolving is refused while a worked-out
    card payment names the account** (:data:`CARD_PAYMENT_KEEPS_ITS_CARD`, plan
    step credit_card:CC-6): the payment's amount is the card's to work out, so
    the account must stay a card, the rule
    :mod:`app.card_payment_infrastructure` backstops.  Asked of the target
    type alone, so a move between two revolving types passes.  **The same for a
    transfer repeating on the account's due dates**
    (:data:`DUE_DATE_REPEAT_KEEPS_ITS_CARD`): its dates are the card's terms',
    and the read pass refuses every walk of one paying no card.

    **A change of what a goal on the account IS -- savings to debt, or a loan
    type to a card-style debt -- is refused FIRST while an active goal is on
    it** (:func:`_refuse_goal_crossing`, rulings R-CC87 and R-CC91), history
    or not: that rule is about what the goal means, not about the ledger.

    Args:
        account: The :class:`~app.models.account.Account` being re-typed
            (its ``account_type`` is the CURRENT type).
        new_type_id: The submitted ``account_type_id``.  The caller has
            already proven it visible (``_account_type_is_visible``), so
            it resolves.

    Returns:
        ``None`` when the change is allowed; otherwise a
        ``(message, category)`` tuple ready for :func:`flask.flash`.
    """
    new_type = db.session.get(AccountType, new_type_id)
    goal_refusal = _refuse_goal_crossing(
        [account.id],
        _goal_kind(
            account.account_type.category_id,
            account.account_type.has_amortization,
        ),
        _goal_kind(new_type.category_id, new_type.has_amortization),
    )
    if goal_refusal is not None:
        return goal_refusal
    if not new_type.has_revolving_credit:
        card_refusal = _refuse_leaving_the_card_kind(account)
        if card_refusal is not None:
            return card_refusal
    if not _crosses_amortizing_boundary(
        account.account_type, new_type.has_amortization,
    ):
        return None
    if accounts_hold_lines([account]):
        return (
            "This account's type cannot change between a loan type and a "
            "non-loan type because it has recorded history.  Archive it and "
            "create a new account of the desired type instead.",
            "warning",
        )
    return None


def _refuse_leaving_the_card_kind(account):
    """Return why *account* must stay a credit card, or ``None`` when it need not.

    Args:
        account: The account being re-typed to a type that is not revolving,
            owner-checked by the route.

    Returns:
        ``(message, category)`` when a worked-out card payment or a repeat on
        its due dates names the account, else ``None``.
    """
    payment = card_payment_on(account.id)
    if payment is not None:
        return (
            CARD_PAYMENT_KEEPS_ITS_CARD.format(
                card=account.name, payment=payment.template.name,
            ),
            "warning",
        )
    repeats = due_date_repeats_into(account.id)
    if repeats:
        return (
            DUE_DATE_REPEAT_KEEPS_ITS_CARD.format(
                card=account.name, payment=repeats[0].name,
            ),
            "warning",
        )
    return None


def _validate_account_type_boundary_edit(account_type, data):
    """Refuse an edit across the loan boundary of a type whose accounts have history.

    The second crossing vector (C4 adversarial review M2): editing a CUSTOM
    type's ``has_amortization`` IN PLACE crosses the same boundary as re-typing
    an account (:func:`_crosses_amortizing_boundary`) -- with no
    ``account_type_id`` change for :func:`_validate_account_type_change` to
    see.  The edit is refused while ANY account of this type holds a line of
    the worked-out ledger (:func:`accounts_hold_lines`; the type row is one
    row -- it cannot change for some of its accounts and not others).  Any
    other edit passes, an Asset/Liability crossing with history included; the
    route then re-classes those accounts' stored linked rows and re-syncs
    their corrections.

    **An edit changing what a goal on the type's accounts IS (its category
    between savings and debt, or a debt's amortization flag) is refused first
    while an active goal is on any of them** (:func:`_refuse_goal_crossing`,
    rulings R-CC87 and R-CC91), for the reason
    :func:`_validate_account_type_change` gives.

    Args:
        account_type: The owner's custom :class:`~app.models.ref.AccountType`
            being edited.
        data: The schema-loaded update payload (absent keys mean "field
            unchanged").
    Returns:
        ``None`` when the edit is allowed; otherwise a
        ``(message, category)`` tuple ready for :func:`flask.flash`.
    The account sweep is deliberately NOT owner-scoped: post C-28 only the
    owner can hold accounts of their custom type, but the question is "does
    ANY account of this type hold a line", so the unscoped query is free
    defense-in-depth against pre-C-28 legacy cross-user rows, each account
    asked in its own owner's ledger.
    """
    new_category_id = data.get("category_id", account_type.category_id)
    new_amortization = data.get(
        "has_amortization", account_type.has_amortization,
    )
    was = _goal_kind(account_type.category_id, account_type.has_amortization)
    becomes = _goal_kind(new_category_id, new_amortization)
    crosses_loan_boundary = _crosses_amortizing_boundary(
        account_type, new_amortization,
    )
    if not crosses_loan_boundary and was == becomes:
        return None
    accounts = (
        db.session.query(Account)
        .filter_by(account_type_id=account_type.id)
        .all()
    )
    goal_refusal = _refuse_goal_crossing(
        [account.id for account in accounts], was, becomes,
    )
    if goal_refusal is not None:
        return goal_refusal
    if crosses_loan_boundary and accounts_hold_lines(accounts):
        return (
            "This change moves accounts between a loan type and a non-loan "
            "type, and at least one account of this type has recorded "
            "history.  Create a new account type instead.",
            "warning",
        )
    return None


def _validate_collateral_link(collateral_account_id, source_account, user_id):
    """Validate a submitted ``collateral_account_id`` for a secured loan.

    The collateral link points a secured liability (the *source_account*,
    a loan) at the Asset account it is secured by, so a Property and its
    mortgage / HELOC can be grouped and equity rendered.  ``None`` clears
    the link and always validates.

    Checks, each a flash-ready rejection:

      * No self-link -- an account cannot secure itself.
      * The target exists and belongs to *user_id*.  Not-found and
        not-yours collapse into one :data:`INVALID_COLLATERAL_LINK`
        response so the field cannot probe for another owner's account
        ids (the project's "404 for both not-found and not-yours" rule).
        The route emits that same constant for a submission that names no
        id at all, so all three are one answer.
      * The target is in the Asset category (a home, not another loan).
      * The source is an amortizing liability (defensive -- the loan
        routes only call this for loan accounts; an asset/liability
        partition makes multi-node cycles impossible, so the self-link
        check is the only cycle guard needed).

    Args:
        collateral_account_id: Submitted target account id, or ``None`` to
            clear the link.
        source_account: The loan :class:`~app.models.account.Account` the
            link is being set on.
        user_id: ``auth.users.id`` of the current owner.

    Returns:
        ``None`` when the link is valid (or cleared); otherwise a
        ``(message, category)`` tuple ready for :func:`flask.flash`.
    """
    if collateral_account_id is None:
        return None
    if collateral_account_id == source_account.id:
        return "An account cannot secure itself.", "danger"
    target = db.session.get(Account, collateral_account_id)
    if target is None or target.user_id != user_id:
        return INVALID_COLLATERAL_LINK
    asset_category_id = ref_cache.acct_category_id(AcctCategoryEnum.ASSET)
    if (
        target.account_type is None
        or target.account_type.category_id != asset_category_id
    ):
        return "The securing account must be an asset.", "danger"
    if (
        source_account.account_type is None
        or not source_account.account_type.has_amortization
    ):
        return "Only a loan can be secured by an asset.", "danger"
    return None


def _account_type_gates(account, data, user_id):
    """Run both ``account_type_id`` gates for ``_validate_update_account``.

    Combines the C-28 multi-tenant visibility check and the
    loan-boundary re-type check behind ONE return site in the parent,
    which reached Pylint's return-statement ceiling when the A1
    amortizing-anchor gate was added.  Both fire only when the form
    submitted an ``account_type_id``; order is preserved (visibility
    first, so the boundary check only ever sees a resolvable type id).

    Args:
        account: The ``Account`` row about to be mutated.
        data: The schema-loaded form payload.
        user_id: ``auth.users.id`` of the current owner.

    Returns:
        The ``(message, category)`` failure tuple from whichever gate
        rejected, or ``None`` when both pass (or the field was not
        submitted).
    """
    if "account_type_id" not in data:
        return None
    if not _account_type_is_visible(data["account_type_id"], user_id):
        return ("Invalid account type.", "danger")
    if data["account_type_id"] != account.account_type_id:
        return _validate_account_type_change(account, data["account_type_id"])
    return None


def _validate_update_account(account, form, user_id):
    """Run every non-mutating gate for ``update_account`` in one place.

    The route grew enough early-return guards (schema validation,
    C-28 multi-tenant ``account_type_id`` check, stale-form
    ``version_id`` check, duplicate-name check) to trip Pylint's
    ``too-many-return-statements`` after C-28 added one more.
    Consolidating the gates into a single helper that returns a
    ``(data, failure)`` tuple lets the route have one validation
    early return instead of four, without losing the per-condition
    flash distinctions.

    Args:
        account: The ``Account`` row about to be mutated.
        form: The submitted ``request.form`` mapping.
        user_id: ``auth.users.id`` of the current owner (passed
            explicitly so this helper does not depend on Flask
            request globals -- matches the project's Routes-pass-
            primitives-into-services style).

    Returns:
        A two-tuple ``(data, failure)``.  When validation passes,
        ``data`` is the schema-loaded payload (with ``version_id``
        already popped) and ``failure`` is ``None``.  When any gate
        rejects, ``data`` is an empty dict and ``failure`` is a
        ``(message, category)`` tuple ready to feed to
        :func:`flask.flash`.  The two-tuple form keeps the helper
        a pure function -- it never touches the response layer.
    """
    if _update_schema.validate(form):
        return {}, (
            "Please correct the highlighted errors and try again.",
            "danger",
        )

    data = _update_schema.load(form)

    # Both account_type_id gates (the C-28 multi-tenant visibility
    # check and the loan-boundary re-type check) live in
    # ``_account_type_gates`` behind one return site.
    type_failure = _account_type_gates(account, data, user_id)
    if type_failure is not None:
        return {}, type_failure

    # **The amortizing-kind anchor gate left with the field it guarded** (plan
    # step X-f1e, finding N-195).  It refused a CASH assertion against a loan
    # (ruling D4 / step A1, finding B-15: a loan's balance is ledger-derived,
    # and the real Mortgage's cache column was once set to $1.00 with an HTTP
    # 200 while the ledger said $177,277.97).  That rule is not weakened by its
    # removal here -- it is enforced where the assertion is WRITTEN rather than
    # at one of the doors reaching it: ``anchor_service.apply_anchor_true_up``
    # raises ``AmortizingAccountAnchorError`` on the kind, and this route no
    # longer reaches any anchor writer at all.  ``AccountUpdateSchema`` now
    # discards ``anchor_balance``, so there is no submitted value left for a
    # gate here to read.

    # Stale-form check.  Performed before any mutation so the audit trail
    # (the ``audit_log`` triggers) records only successful edits.  It named
    # ``AccountAnchorHistory`` first until plan step X-f1e: this route wrote an
    # assertion then, and now writes only ``accounts`` columns.  The check is
    # conditional on the form having
    # submitted a version (clients that omit it fall through to the
    # SQLAlchemy-tier check at flush time).
    submitted_version = data.pop("version_id", None)
    if submitted_version is not None and submitted_version != account.version_id:
        return {}, (
            "This account was changed by another action while you "
            "were editing.  Please reload and try again.",
            "warning",
        )

    # Duplicate-name guard (if name is changing).
    if "name" in data and data["name"] != account.name:
        existing = (
            db.session.query(Account)
            .filter_by(user_id=user_id, name=data["name"])
            .first()
        )
        if existing:
            return {}, (
                "An account with that name already exists.",
                "warning",
            )

    return data, None
