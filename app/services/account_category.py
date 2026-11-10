"""
Shekel Budget App -- the account-type CATEGORY classifier.

The one home of the account-category vocabulary every NET-WORTH and cockpit
surface classifies through.  Every category question those surfaces ask derives
from :func:`account_category`'s single answer:

* :func:`is_liability_account` -- the asset-vs-liability rule the net-worth
  reduction, the liability band, the danger ink and the revolving-debt figure
  all classify through, reached by every cockpit surface as
  :attr:`~app.services.savings_dashboard_service._types.AccountProjection.is_liability`;
* :func:`~app.services.savings_dashboard_service._display.category_key`
  -- the five-key DISPLAY vocabulary (the grid's group cards, the net-worth
  composition bands, the Horizon's bands), which NAMES this answer through a
  mapping that lives with the display order it belongs to.

**Those two were independent id comparisons until plan step X-z** (ruling
R-CP, finding N-118), equivalent by READING and by nothing else.  The Horizon
is where that cost the most: its three band producers must partition the
account set exactly once and they selected with BOTH spellings, so an account
the two classified differently would be counted twice with opposite signs --
net worth wrong by double its balance -- or not at all.  Since plan step X-z7
the answer is resolved ONCE per account onto
:attr:`~app.services.savings_dashboard_service._types.AccountProjection.category`
and both questions read that member, so there is no second derivation left.

**The module was ``net_worth_account_data`` until the same step** (ruling
R-CQ).  It was named for a ``to_net_worth_account_data`` adapter that paired
each account's dense period map with a STORED copy of the liability flag; plan
step X-w put the dense map on the projection that derives the flag, the adapter
had nothing left to adapt, and the name outlived it by three steps.

It is PUBLIC and lives at the service layer rather than inside the savings
cockpit package, although that package holds every consumer today: the
classification RULE is account metadata, not a display decision, so the next
consumer reaches a public module instead of importing a private one (the
package-boundary lesson the W9910 checker enforces).  The rule does NOT belong
in the ``balance_at`` seam either: it is account metadata, not a balance, and
the seam's contract is balances only.

Boundary discipline (``CLAUDE.md``: services are isolated from Flask): no
Flask import, no database writes.  Classification uses the cached
reference-table ids (IDs for logic, never a ``.name`` string).
"""

from app import ref_cache
from app.enums import AcctCategoryEnum


def account_category(account) -> AcctCategoryEnum | None:
    """Return the :class:`~app.enums.AcctCategoryEnum` for *account*'s type.

    The canonical account-category classifier (IDs for logic, never a ``.name``
    string).  Every category question the savings cockpit and the net-worth
    surfaces ask derives from this answer, so no two of THEM can disagree about
    one account.

    **It is not the only place a ``category_id`` meets a cached id, and the
    claim that it was is corrected here** (plan step X-z8, ruling R-CU; both of
    plan step X-z's adversarial reviews found the overclaim independently).  The
    survivor that matters is
    :func:`app.services.ledger_lines.linked_ledger_class`,
    which asks THIS function's asset-vs-liability question a second time for the
    LEDGER -- it decides which class a real account's own ledger line carries,
    in the worked-out chart key (and in the stored chart row until plan task
    plan#3133 deleted its writer; it was
    ``ledger_account_service.ledger_class_id_for_category`` until plan#2523).
    The two agree by READING, which is finding N-118's own condition;
    **finding N-122 owns it and plan step X-ab is where it is merged**, because
    a re-class moves what an account's postings book against and that is not a
    refactor's business.  Four more sites classify
    RETIREMENT-or-INVESTMENT rather than liability (``account_service``'s
    projection query, the cash-detail route guard, and two templates); they
    answer a different question and are out of this rule's scope.

    Returns ``None`` -- "this account has no category the app models" -- in
    exactly two states, neither of which a healthy persisted row reaches:

    * ``account.account_type`` is ``None``.  ``budget.accounts.account_type_id``
      is ``NOT NULL`` and the relationship is ``lazy="joined"``, so a PERSISTED
      account always has a type; this is the transient / partially-built object
      (the shape a unit test stands in with).
    * the type's ``category_id`` names a ``ref.account_type_categories`` row
      that is not one of the four :class:`~app.enums.AcctCategoryEnum` members.
      ``ref.account_types.category_id`` is ``NOT NULL``, so this needs a FIFTH
      category row inserted outside the application: ``ref_cache.init`` requires
      the four named rows to exist and does not forbid others.  Both databases
      carry exactly four (verified 2026-07-30).

    Callers decide what an uncategorised account means for them: the display
    vocabulary buckets it as ``"other"`` and :func:`is_liability_account`
    answers ``False``.  That is the safe direction -- an unmodelled category
    counted as an asset is a balance in the wrong chart band, where counting it
    as a liability would SUBTRACT it from net worth.

    **It is ONE dict read** (plan step X-z6, ruling R-CV).  It opened as a scan
    over :class:`~app.enums.AcctCategoryEnum` asking ``ref_cache`` once per
    member, which measured 2.3x-4.5x the cost of the single comparison it
    replaced (``0.135 -> 0.307`` us for an ASSET, ``0.139 -> 0.624`` us for an
    INVESTMENT, best of 200k iterations) -- so the step that unified the rule
    made every read of it slower.
    :func:`app.ref_cache.acct_category_member` inverts the cached map once at
    startup instead.

    Args:
        account: The :class:`~app.models.account.Account` to classify, with its
            ``account_type`` relationship loaded (this issues no query).

    Returns:
        The account type's :class:`~app.enums.AcctCategoryEnum` member, or
        ``None`` when it has no category this application models.
    """
    return account_type_category(account.account_type)


def account_type_category(acct_type) -> AcctCategoryEnum | None:
    """Return the :class:`~app.enums.AcctCategoryEnum` for an account TYPE.

    The rule :func:`account_category` answers for an account, asked of the type
    itself.  Plan step credit_card:CC-5-5b needed it for the create form, whose
    balance box is typed against the account type the owner picked before the
    account exists (ruling **R-CC52** stores a liability's figure in the held
    sign), and every balance door reaches it since, through
    :func:`app.services.liability_sign.asks_owed` -- the labels, the crossing
    and the stale-form guard all ask it of ``account.account_type``.
    :func:`account_category` is this function applied to
    ``account.account_type``, so the two cannot disagree: one dict read,
    reached through two entrances.

    Args:
        acct_type: A :class:`~app.models.ref.AccountType`, or ``None`` -- the
            transient-account state :func:`account_category` documents.

    Returns:
        The type's category member, or ``None`` for no type or for a category
        this application does not model.
    """
    if acct_type is None:
        return None
    return ref_cache.acct_category_member(acct_type.category_id)


def is_liability_account(account) -> bool:
    """Return whether an account's type is in the LIABILITY category.

    The asset-vs-liability rule the net-worth figures depend on: net worth is
    the plain sum of every balance (each HELD, negative when owed; ruling
    R-CC47), and the liability total is what the liabilities OWE,
    :func:`app.services.liability_sign.owed` summed.  Every net-worth surface
    classifies through this one home -- the cockpit's today figures, its trend,
    its Horizon band, the no-payoff-date debt figure and the per-cell danger ink
    -- so an account can never count as an asset on one surface and a
    liability on another.

    DERIVED from :func:`account_category`'s one read since plan step X-z
    (ruling R-CP, finding N-118), where it was a second, independent comparison
    of the same column against the same cached id.  It reads
    :func:`is_liability_type`, which since plan step credit_card:CC-5-5d asks
    :func:`is_liability_category` of the type's category id -- the ONE
    comparison against the cached LIABILITY member -- while
    :func:`account_category` reaches the same cached map through
    :func:`account_type_category`.  An account with no modelled category (see
    :func:`account_category` for the two states that produces) is not a
    liability.

    Args:
        account: The :class:`~app.models.account.Account` to classify.

    Returns:
        ``True`` when the account's type's category is LIABILITY,
        ``False`` otherwise.
    """
    return is_liability_type(account.account_type)


def is_liability_type(acct_type) -> bool:
    """Return whether an account TYPE is in the LIABILITY category.

    :func:`is_liability_account` asked of the type -- what
    :func:`app.services.liability_sign.asks_owed` reads for every balance door,
    the create form's included (see :func:`account_type_category`).
    :func:`is_liability_account` reads this, so an account and its type cannot
    be classified two ways.

    Args:
        acct_type: A :class:`~app.models.ref.AccountType`, or ``None`` -- not a
            liability, the safe direction :func:`account_category` names.

    Returns:
        ``True`` when the type's category is LIABILITY, ``False`` otherwise.
    """
    return acct_type is not None and is_liability_category(acct_type.category_id)


def is_liability_category(category_id) -> bool:
    """Return whether a ``ref.account_type_categories`` id is the LIABILITY one.

    The rule asked of a bare category id, which is what a CUSTOM account type's
    in-place edit submits before any type carries it -- the one caller that
    has no type to ask (plan step credit_card:CC-5-5d: an edit moving a type's
    accounts between savings and debt is refused while a goal is on one of
    them, ruling R-CC87).  :func:`is_liability_type` reads this, so the
    comparison against the cached LIABILITY member is written once.

    Args:
        category_id: A ``ref.account_type_categories`` id.

    Returns:
        ``True`` when it names the LIABILITY category; ``False`` for any other,
        including an id the application does not model (the safe direction
        :func:`account_category` names).
    """
    return ref_cache.acct_category_member(category_id) is AcctCategoryEnum.LIABILITY
