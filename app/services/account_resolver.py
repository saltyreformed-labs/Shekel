"""
Shekel Budget App -- Account Resolvers

Shared helpers to deterministically pick the account used by the
budget grid and the analytics services for balance and reporting
queries.

* ``resolve_grid_account`` -- 4-step fallback chain used by the
  budget grid (every step refuses an AMORTIZING account -- ruling D4
  / step A1; a loan's balance is not a transaction sum, see
  :func:`is_cash_flow_account`):

    1. override_account_id  (query param -- future URL-based override)
    2. user_settings.default_grid_account_id  (if set and still active)
    3. First active checking account  (by sort_order, id)
    4. First active grid-eligible account  (by sort_order, id)
    5. None

* ``resolve_cash_flow_set`` -- the grid resolver's answer widened to
  "checking and its cards" (ruling ``credit_card:R-CC16``, plan step
  CC-4-1): the primary grid account plus the owner's active revolving
  accounts, as one :class:`~app.services.cash_flow_set.CashFlowSet`
  whose balance line is the primary or an override within the set.
  Read by the grid, the dashboard and the spending report.

  What a payment or a purchase may NAME is not this set since plan task
  ``credit_card:plan#3232`` (ruling plan#3227: every account that holds
  cash, and the cards for Credit and the CC tick):
  ``movement_account.owner_accounts`` reads it, so the set's OWNER half,
  which those doors read with no override (``resolve_owner_and_view``,
  ``resolve_owner_cash_flow_set``, plan step CC-5-2), was deleted with its
  last reader.

* ``resolve_analytics_cash_flow_set`` -- the SAME set for the calendar,
  which takes an explicit ``account_id`` as a question about THAT
  account: an override the admission test refuses answers ``None`` (the
  route's 404) where the grid resolver falls through to the primary.
  It replaced ``resolve_analytics_account`` at plan step CC-4-3, whose
  "first active checking, no settings layer" default was a second
  spelling of the primary; every analytics surface now reads the one
  primary the grid reads.

One admission test (``_admissible_grid_account``: the owner's, active,
a cash-flow kind) sits behind every explicit id and every saved default,
so neither resolver can hand a loan to a surface that renders a
cash-flow balance (ruling D4 / step A1; plan step X-a1 closed the
calendar door finding N-38 measured open).
"""

from app import ref_cache
from app.enums import AcctCategoryEnum, AcctTypeEnum
from app.extensions import db
from app.models.account import Account
from app.services import account_service
from app.services.cash_flow_set import CashFlowSet


def is_cash_flow_account(account: Account) -> bool:
    """Return True when a CASH-FLOW surface may render *account*.

    A cash-flow surface renders a balance beside the account's OWN transaction
    rows, so the two have to reconcile on screen.  This predicate gates the
    surfaces reached through the resolvers in this module: the budget
    grid, the dashboard hero and pulse, and the analytics calendar's
    end-of-day line and month-end figure.

    **What such a surface READS is no longer one answer** (plan step X-g3b).
    The dashboard and the calendar read the seam's cash entries
    (``cash_balance_map`` / ``cash_balance_at`` / ``cash_daily_balance_series``
    -- a pure running sum from the anchor); the grid reads
    ``grid_balance_view``, which renders the MODELLED balance with the accrual
    and contribution rows that explain it (ruling R-W).  Both are cash-flow
    surfaces in the sense this gate cares about, and the gate itself is
    unchanged by that split: a LOAN is refused from all of them.  The remaining
    consumer, the cash detail page, carries its own wider gate
    (``routes.accounts._cash_page.cash_detail_wrong_type``, which also
    refuses appreciating and retirement / investment kinds), so it is
    not routed through here.  An AMORTIZING loan's balance is not
    a transaction sum -- payment transfers INTO the loan read as inflows,
    so the grid rendered the real Mortgage RISING by the full PITI every
    month (plan-of-record finding B-3, ruling D4: the grid refuses an
    amortizing account; step A1).  Branches on the ``has_amortization``
    boolean column, never a type-name string, matching
    :func:`app.services.account_projection.classify_account`.

    **One predicate, both resolvers, because it is one question.**  It was
    ``is_grid_account`` and gated the grid path alone, which read as a
    grid preference rather than the kind rule it is -- and the calendar
    reached the same producers through ``resolve_analytics_account`` (its
    resolver until plan step CC-4-3) with no gate at all.  Measured on a
    dev clone 2026-07-25, before plan
    step X-a1 closed it: ``/analytics?view=month&account_id=3`` rendered
    the Mortgage at ``$178,103.41`` and ``account_id=8`` rendered the Van
    Loan at ``$531.94``, where the loans owed ``$177,277.97`` and
    ``$15,663.59`` -- finding B-3 itself, on the one cash-flow surface
    ruling D4's enumeration missed (finding N-38).

    Args:
        account: An :class:`Account` with its ``account_type``
            relationship reachable (lazy load is fine; the resolver
            operates inside a request session).

    Returns:
        True for every non-amortizing kind (an account with no loaded
        type row classifies PLAIN and stays eligible, matching the
        classifier's degenerate branch); False for a loan.
    """
    acct_type = account.account_type
    return acct_type is None or not acct_type.has_amortization


def serves_cash_detail(account: Account) -> bool:
    """Return True when the CASH DETAIL page may render *account*.

    The wider gate :func:`is_cash_flow_account`'s docstring names.  A cash
    detail surface refuses an amortizing loan like every cash-flow surface
    does, and ALSO refuses appreciating physical assets and retirement /
    investment accounts, which keep their own screens.

    **It lives here rather than beside the page it gates because a SECOND
    surface now asks it**: the grid's bank statement control links into that
    page, so a control rendered for an account the page 404s is a door onto
    an error.  ``routes.accounts._cash_page`` is a private module of another
    route package (``shekel-private-module-import``), so restating the rule
    in the grid was the only alternative to sharing it -- and a rule stated
    twice is a rule that drifts once.  One predicate, because it is one
    question: the reason :func:`is_cash_flow_account` is one.

    Branches on the type row's boolean columns and integer category id only,
    never a ref-table ``name`` string (the IDs-for-logic invariant).

    Args:
        account: An :class:`Account` with its ``account_type`` relationship
            reachable (lazy load is fine; callers operate inside a request
            session).

    Returns:
        True for Checking, the ``has_interest`` kinds (HYSA / Money Market /
        CD / HSA) and plain cash kinds (Savings, Credit Card, plain custom);
        False for loans, physical assets and retirement / investment
        accounts.  An account with no ``account_type`` (degenerate /
        partially loaded) is served, matching
        :func:`~app.services.account_projection.classify_account`'s
        None-is-PLAIN branch.
    """
    acct_type = account.account_type
    if acct_type is None:
        return True
    return not (
        acct_type.has_amortization
        or acct_type.has_appreciation
        or acct_type.category_id in (
            ref_cache.acct_category_id(AcctCategoryEnum.RETIREMENT),
            ref_cache.acct_category_id(AcctCategoryEnum.INVESTMENT),
        )
    )


def list_grid_accounts(user_id: int) -> list[Account]:
    """Return the user's active grid-eligible accounts, picker-ordered.

    The option list behind the settings page's "Default Grid Account"
    picker.  Applies the same amortizing-kind exclusion as
    :func:`resolve_grid_account` (ruling D4 / step A1) so the picker can
    never offer an account the resolver would refuse, and keeps
    ``account_service.list_active_accounts``'s ``(sort_order, name)``
    ordering so the dropdown matches every other account picker.

    Args:
        user_id: ``auth.users.id`` of the owner whose accounts to list.

    Returns:
        The owner's active non-amortizing :class:`Account` rows, ordered
        by ``sort_order`` then ``name``.
    """
    return (
        account_service.active_accounts_query(user_id, amortizing=False)
        .order_by(Account.sort_order, Account.name)
        .all()
    )


def _first_active_checking_account(user_id) -> Account | None:
    """Return the user's canonical checking account, or ``None``.

    The single definition of "which account is this user's checking
    account": the first active account of the CHECKING type, ordered by
    ``sort_order`` then ``id``.  :func:`resolve_grid_account`'s step 3 falls
    back to this, and since plan step CC-4-3 every cash-flow surface reaches
    it through that one chain (the analytics resolver read it directly
    before), so the grid and analytics surfaces always pick the same account
    for a user; a change to the selection rule (a new tiebreaker, a primary
    flag) lives here once.

    Args:
        user_id: The current user's id.

    Returns:
        The first active checking :class:`Account`, or ``None`` when the
        user has no active checking account.
    """
    checking_type_id = ref_cache.acct_type_id(AcctTypeEnum.CHECKING)
    return (
        db.session.query(Account)
        .filter_by(
            user_id=user_id,
            is_active=True,
            account_type_id=checking_type_id,
        )
        .order_by(Account.sort_order, Account.id)
        .first()
    )


def _admissible_grid_account(user_id, account_id) -> Account | None:
    """Return the account *account_id* names if the grid may render it, else ``None``.

    The ONE admission test behind :func:`resolve_grid_account`'s first two
    steps -- an override and a saved default are refused for the same three
    reasons (not the owner's, archived, an amortizing loan: ruling D4 / step
    A1) -- and behind :func:`resolve_cash_flow_set`'s override arm, which
    needs the answer WITHOUT the fallback chain the grid resolver runs after a
    refusal.  It was written inline twice in the resolver and would have been
    written a third time; a rule stated once cannot drift.

    Args:
        user_id: The current user's id.
        account_id: The candidate ``budget.accounts.id``, or ``None``.

    Returns:
        The :class:`Account`, or ``None`` when there is no such row, it is
        another owner's, it is inactive, or it is not a cash-flow kind.
    """
    if account_id is None:
        return None
    acct = db.session.get(Account, account_id)
    if (
        acct and acct.user_id == user_id and acct.is_active
        and is_cash_flow_account(acct)
    ):
        return acct
    return None


def resolve_grid_account(user_id, user_settings=None, override_account_id=None):
    """Return the Account to use for grid balance display.

    Every step applies the amortizing-kind gate (ruling D4 / step A1):
    an override or saved default naming a loan behaves exactly like one
    naming a missing or archived account -- it falls through to the next
    step -- and the any-type fallback skips loans in SQL.  The grid (and
    the dashboard hero, which shares this resolver) can therefore never
    land on an amortizing account.

    Args:
        user_id: The current user's id.
        user_settings: The user's UserSettings object (or None).
        override_account_id: Explicit account id from a query param.

    Returns:
        An Account instance, or None if no active grid-eligible
        accounts exist.
    """
    # 1. Override from query param.
    acct = _admissible_grid_account(user_id, override_account_id)
    if acct is not None:
        return acct

    # 2. User setting.
    if user_settings:
        acct = _admissible_grid_account(
            user_id, user_settings.default_grid_account_id,
        )
        if acct is not None:
            return acct

    # 3. First active checking account.
    acct = _first_active_checking_account(user_id)
    if acct:
        return acct

    # 4. First active account of any grid-eligible type.
    return (
        account_service.active_accounts_query(user_id, amortizing=False)
        .order_by(Account.sort_order, Account.id)
        .first()
    )


def _active_revolving_accounts(user_id) -> list[Account]:
    """Return the owner's active credit cards, picker-ordered.

    The card half of the cash-flow set: every active account whose type
    carries ``has_revolving_credit`` (plan step CC-1's flag), ordered the way
    :func:`resolve_grid_account`'s fallback orders accounts so the set's member
    order is stable across renders.

    Args:
        user_id: ``auth.users.id`` of the owner.

    Returns:
        The active revolving :class:`Account` rows, by ``sort_order`` then
        ``id``.  Empty for an owner with no card, which is every owner before
        the credit-card arc's release.
    """
    return (
        account_service.active_accounts_query(
            user_id, amortizing=False, revolving=True,
        )
        .order_by(Account.sort_order, Account.id)
        .all()
    )


def _cash_flow_view(
    user_id, user_settings, balance: Account | None,
) -> CashFlowSet | None:
    """Build the owner's cash-flow set, seen from behind *balance*.

    The ONE walk both public resolvers share: the primary is
    :func:`resolve_grid_account`'s answer with no override, the members are
    the primary plus every active revolving account, and the view is the
    set :meth:`~app.services.cash_flow_set.CashFlowSet.seen_from` *balance*
    when the caller admitted one -- a member keeps the set's rows behind its
    own line; an owned cash-flow account outside the set collapses the view
    to itself -- else the set itself.  What differs between the two callers
    is only what they do with an override the admission test refuses, and
    that policy is theirs; the set is built here once.

    Args:
        user_id: The current user's id.
        user_settings: The user's ``UserSettings`` row (or ``None``).
        balance: An account :func:`_admissible_grid_account` ADMITTED, or
            ``None`` for the primary.  Never an unadmitted row: the callers
            run the admission test first, which is what keeps this walk from
            restating it.

    Returns:
        The view, or ``None`` when the owner has no grid-eligible account at
        all.
    """
    primary = resolve_grid_account(user_id, user_settings)
    if primary is None:
        return None
    owner = CashFlowSet(
        balance=primary,
        members=(
            primary,
            *[
                card for card in _active_revolving_accounts(user_id)
                if card.id != primary.id
            ],
        ),
    )
    if balance is None:
        return owner
    return owner.seen_from(balance)


def resolve_cash_flow_set(
    user_id, user_settings=None, override_account_id=None,
) -> CashFlowSet | None:
    """Return the owner's cash-flow set: checking and its cards.

    The "checking and its cards" predicate of developer ruling
    ``credit_card:R-CC16`` (design ``docs/design/credit_card_from_scratch.md``
    3.3, plan step CC-4-1): the accounts a paycheck's plan items live on, read
    as ONE SET by the budget grid and by every other plan-item reader, with the
    balance line still ONE account's.

    **The PRIMARY is :func:`resolve_grid_account`'s own answer with no
    override** -- the owner's ``default_grid_account_id``, else their first
    active checking account, else their first grid-eligible account -- and the
    set is that account plus every active revolving account
    (:func:`_active_revolving_accounts`).  ONE definition of the primary for
    every reader: the grid and the dashboard read it here, and since plan step
    CC-4-3 so do the spending report and the calendar's default
    (:func:`resolve_analytics_cash_flow_set`), which until then took
    ``resolve_analytics_account``'s "first active checking, no settings layer"
    -- a second spelling of one question, which on the 2026-09-18 production
    population named the same account (the one owner's
    ``default_grid_account_id`` IS their first active checking account).

    **The BALANCE line is the primary, or the override the request named.**
    The grid has always taken a ``?account_id=`` override, and it keeps its
    meaning under the set:

    * an override naming a MEMBER (a card) puts that member's balance on the
      line and keeps the set's rows -- the same paycheck, seen from the card;
    * an override naming an owned, active, cash-flow account OUTSIDE the set
      (a savings account) keeps that account's single-account view exactly as
      before this step: the set collapses to that one account, so nothing on
      the screen changes for it;
    * an override the grid resolver refuses (another owner's, archived, a
      loan, unknown) falls through to the primary, as it always has -- through
      the ONE admission test :func:`_admissible_grid_account`, so the primary
      chain runs once per request whatever the override.

    Args:
        user_id: The current user's id.
        user_settings: The user's ``UserSettings`` row (or ``None``).
        override_account_id: Explicit account id from a query param.

    Returns:
        The :class:`~app.services.cash_flow_set.CashFlowSet`, or ``None`` when
        the owner has no grid-eligible account at all (the same state
        :func:`resolve_grid_account` answers ``None`` for).
    """
    return _cash_flow_view(
        user_id, user_settings,
        _admissible_grid_account(user_id, override_account_id),
    )


def resolve_analytics_cash_flow_set(
    user_id, user_settings, account_id: int | None,
) -> CashFlowSet | None:
    """Return the cash-flow set for an analytics surface, or ``None`` for a refused id.

    The calendar's twin of :func:`resolve_cash_flow_set` (plan step
    ``credit_card:CC-4-3``): the SAME set, through the same walk
    (:func:`_cash_flow_view`), with one difference in what an explicit
    ``account_id`` means.  The grid resolver treats a refused override as
    absent and falls through to the primary; here an explicit id is a
    question about THAT account, so one the admission test refuses --
    another owner's, archived, a loan, unknown -- answers ``None`` and the
    calendar turns it into the project-standard 404
    (:class:`~app.services.calendar_service.CalendarAccountNotResolvableError`;
    "404 for both 'not found' and 'not yours'").  Answering with a different
    account's balance would be a wrong answer rather than a missing one --
    the policy ``resolve_analytics_account`` carried since plan step X-a1
    (finding N-38: ``?account_id=<Van Loan>`` rendered ``$531.94`` for a loan
    owing ``$15,663.59`` before the gate), and the one thing of it that
    survives this step.

    An admitted id is the override :func:`resolve_cash_flow_set` describes: a
    member puts its balance on the line behind the set's rows; an owned
    cash-flow account outside the set is that account's single-account view.
    ``None`` is the primary.

    Args:
        user_id: The current user's id.
        user_settings: The user's ``UserSettings`` row (or ``None``) -- the
            saved-default layer the primary reads, which the analytics
            surfaces did not consult before this step.
        account_id: The explicit account id from the request, or ``None``.

    Returns:
        The :class:`~app.services.cash_flow_set.CashFlowSet`; ``None`` when an
        explicit id is refused, or when the owner has no grid-eligible
        account at all.
    """
    if account_id is None:
        return _cash_flow_view(user_id, user_settings, None)
    balance = _admissible_grid_account(user_id, account_id)
    if balance is None:
        return None
    return _cash_flow_view(user_id, user_settings, balance)
