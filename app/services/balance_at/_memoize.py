"""
Shekel Budget App -- the balance seam's two read-pass PRIMITIVES.

The store-once rule behind every account-keyed derivation a read pass holds
(:func:`_memoize_once`) and the fail-loud no-baseline guard at every seam
entry's door (:func:`require_scenario`).  Both take the pass
(:class:`~app.services.balance_at._context.BalanceContext`) as an argument
and neither is a method of it, which is what let them move here whole.

**They lived at the bottom of ``_context.py`` until plan step
recurrence:R7d-f-2** (ruling **R-R75**, developer 2026-09-12).  That module
stood at exactly pylint's 1,000-line cap (plan ledger row **BAL-483**), and
the next memo the pass needed -- the occurrence WALK of a resolved recurrence,
ruled onto the pass by rule 14's ONE WALK -- could not land under it.  The
row's remedy is a SPLIT and never a trim, and this is the cut the developer
chose over the one the row named (the memo methods -- ``calendar``,
``amounts``, ``paychecks``, ``resolved_recurrence_of`` -- into a ``_memos``
leaf): these two were already free functions with six sibling importers, so
moving them is a pure move with no class surgery, while a frozen dataclass's
methods cannot leave it without inheritance.  Nothing changed in the move: the two
bodies and their docstrings are the ones the functions carried, with the
cross-references re-pointed at the sibling they now describe from outside.

Imports ``_context`` for TYPE CHECKING only, so the seam's internal DAG keeps
the arrow one way: ``_context`` imports this module at runtime (its
``loan_walk`` and ``scenario_id`` call the two primitives), and this module
imports no sibling at runtime -- it joins ``_fold`` and
``_asset_contributions`` at the floor.

Boundary discipline (``CLAUDE.md``): no Flask symbol, no writes.
"""

from collections.abc import Callable
from typing import TYPE_CHECKING, TypeVar

from app.exceptions import BaselineMissingError, ForeignAccountError
from app.models.account import Account

if TYPE_CHECKING:
    # Type-only: ``_context`` imports THIS module at runtime, so a runtime
    # edge back would close a cycle inside the seam (the shape finding N-25
    # names).
    from ._context import BalanceContext

# What a memo cache's derivation yields.  The five account-keyed caches
# (:attr:`~._context.BalanceContext.loans` / :attr:`~._context.BalanceContext.plans` /
# :attr:`~._context.BalanceContext.payoffs`, and the private ``_walks`` / ``_cash_folds``)
# differ only in this type, so :func:`_memoize_once` is generic over it and
# there is ONE store-once mechanism rather than a copy per cache.
_Derived = TypeVar("_Derived")


def _memoize_once(
    ctx: "BalanceContext",
    cache: "dict[int, _Derived]",
    account: Account,
    build: "Callable[[], _Derived]",
) -> "_Derived":
    """Return ``cache[account.id]``, computing it via ``build()`` at most once.

    The ONE store-once rule behind every account-keyed derivation a read pass
    holds (:func:`~app.services.balance_at._resolution.resolved_loan` fills
    :attr:`~._context.BalanceContext.loans`;
    :func:`~app.services.balance_at._plan.memoized_plan` fills
    :attr:`~._context.BalanceContext.plans`;
    :func:`~app.services.balance_at._positions.memoized_payoff` fills
    :attr:`~._context.BalanceContext.payoffs`;
    :func:`~app.services.balance_at._cash_fold.assembled_fold` fills
    the private ``_cash_folds``; and :meth:`~._context.BalanceContext.loan_walk`
    fills its own private ``_walks``).  They share this rather than each carrying
    a copy of the same three lines -- a copy is where two memos drift on the very
    property they exist to guarantee.

    **It BINDS the account to the pass, and that is plan step X-i4** (finding
    **N-354**).  It takes the ``account`` rather than a bare id precisely so it
    can refuse one this pass does not own, and it does so BEFORE the membership
    test, so a foreign account is refused on a cache hit exactly as on a miss.
    Putting the refusal here rather than at each funnel is what makes it a
    precondition rather than a fence: creating per-account state on a context is
    the thing that has to be bound, this is the only way to create it, and a
    funnel added later cannot forget a rule it never had to remember.  The
    seam's five funnels each had their own chance to get the pairing wrong until
    this took the argument away from them.  **Scoped to the ACCOUNT-keyed
    caches, and that scope is exact**: :meth:`~._context.BalanceContext.calendar` and
    :meth:`~._context.BalanceContext.amounts` beside them open-code the same three lines
    against a ``user_id`` and a ``scenario_id``, which is a residue this step
    did not remove -- taking the ``Account`` narrowed the primitive, so those
    two can no longer adopt it.  Neither is per-account, so neither is a
    pairing a caller can state at all.  **One per-loan memo is not created
    here**: the loan calendars the pass's walk and its amount basis share
    (plan step recurrence:R25), keyed by a bare loan id as the pricer's own
    memo was before it, holding contract terms and no balance; the context's
    docstring says how each of its two readers is bound.

    **Membership, never truthiness.**  The check is ``account.id not in cache``, not a
    truthiness test on the value, because a derivation may have a legitimately
    falsy answer: a ``None`` resolution (not a configured loan) and a ``None``
    payoff (a loan that never clears).  A truthiness check would re-derive those on
    EVERY read of every pass -- unbounded, and green under every test that happens
    to use a configured loan that clears.

    **The PLAN was a third example until plan step R16-a, and how it stopped being
    one is the better argument for the rule.**  ``loan_plan`` answered ``[]`` for a
    not-yet-configured or fully-retired loan; it now answers a
    ``LoanForwardPlan(payments=[], calendar=None)``, which is unconditionally TRUTHY.
    The cache is no longer at risk there -- but a CONSUMER was, and silently:
    ``_secured_debt._debt_span_upper`` tested ``if not plan`` and took the
    wrong branch the moment the value stopped being a list, until it became
    ``if not plan.payments``.  Membership is the rule here for the same reason
    ``.payments`` is the test there: what these values MEAN is never what
    ``bool()`` says about them.  *The WALK and the CASH FOLD are dataclass
    instances and never falsy either, so neither would have caught it --
    which is why the property is pinned on the primitive rather than on
    whichever cache a test happened to use.*

    **It is not an ownership gate**; whether the requester may see the account
    was decided upstream, and this cannot know that.  What it answers is whether
    the account and the pass describe ONE read -- a question no route can ask,
    because no route knows a context exists.  See
    :class:`~app.exceptions.ForeignAccountError`.

    **A raising build is not cached.**  ``cache[account.id]`` is assigned only
    from a returned value, so a fail-loud guard inside *build* (the seam's
    ``require_scenario``) fires on every call rather than being swallowed after
    the first.

    See :class:`~._context.BalanceContext` for why four of these caches are PUBLIC
    pass-through state the seam fills, while the WALK memo beside them is a
    private method (the dependency arrow, finding N-25).

    Args:
        ctx: The read pass the derivation is being memoized on -- the owner
            *account* is bound against.
        cache: The read pass's per-account cache to fill, keyed by
            ``account.id``.
        account: The account this derivation is memoized under and bound to.
        build: The zero-argument derivation, called at most once per account.

    Returns:
        The value stored for ``account.id`` (freshly built on the first call,
        replayed after).

    Raises:
        ForeignAccountError: When *account* does not belong to ``ctx.user_id``.
    """
    if account.user_id != ctx.user_id:
        raise ForeignAccountError(
            f"read pass for user {ctx.user_id} was handed account "
            f"{account.id}, which belongs to user {account.user_id}. The "
            f"balance seam takes the account and the pass as two arguments and "
            f"they must describe one read: the pass's scenario scopes the rows, "
            f"its as-of clamps the plan and its calendar supplies the columns, "
            f"while balance assertions are per-ACCOUNT and would replay "
            f"whatever it was handed. Build the context for the account's own "
            f"owner, or resolve the account through this owner's resolver "
            f"(app.services.account_resolver)"
        )
    if account.id not in cache:
        cache[account.id] = build()
    return cache[account.id]


def require_scenario(ctx: "BalanceContext") -> None:
    """Raise :class:`~app.exceptions.BaselineMissingError` when *ctx* has no baseline.

    Every balance the seam produces is scoped to a baseline scenario, so a
    context without one cannot answer anything -- the fail-loud guard at each
    seam entry's door, stated once so the contract and its message are
    single-sourced.

    **It raises a NAMED exception, and that name is the no-baseline policy**
    (plan step X-v1, ruling R-BW).  One application-level handler catches
    :class:`~app.exceptions.BaselineMissingError` and answers it in ONE way --
    the setup-recovery page for a full request, ``204 No Content`` for an HTMX
    fragment (so a live DOM is never replaced by a setup card), and an ERROR log
    event either way.  The exception subclasses ``ValueError``, so this
    function's long-documented contract is unchanged for anything that catches
    the broader type; the handler catches the narrow one, because catching
    ``ValueError`` at the application tier would swallow every unrelated
    conversion failure in the request.

    **There are no caller pre-checks left on the balance path, and that is the
    point** (plan step X-v2, rulings R-BY and R-BZ).  Every caller used to ask
    this question itself, and between them they answered it several different
    ways -- the census and the full list live at
    :func:`app.error_handlers.register_error_handlers`'s handler, which is the
    one place that now decides.  Plan step X-t2 had already tried
    single-sourcing the PREDICATE (a ``has_baseline`` property, finding
    N-107); that made the callers agree on the QUESTION while they still
    disagreed on the ANSWER, so the property is gone with them.

    **The callers that keep their own handling say why at the guard**
    (ruling R-BY):

    * ``loan_recurrence_sync.sync_recurring_payment_bounds`` -- a WRITER,
      running mid-mutation, UNTIL plan step R7d-g.  A raise there would have
      rolled back the user's just-flushed loan-params edit and answered with a
      setup card, losing the write; it instead wrote the contract-derived
      START bound and skipped only the scenario-scoped END bound, which was
      plan step C8e's rule ("a loan's contract terms are not scenario-scoped")
      applied to a write.  R7d-g deleted the END half with the column's
      writers, and the START-only sync that survives
      (:func:`app.services.loan_recurrence_sync.sync_loan_payment_start`)
      builds no pass, so this carve-out is spent.
    * :func:`app.services.balance_at.liability_owed_at_dates` -- the ONE seam
      entry that does not run this guard at all, because a missing baseline
      there is not an error but the degenerate case of its own rule (no loan is
      resolvable and no plan can be folded, so every liability holds flat); its
      docstring owns that rationale.

    Args:
        ctx: The read pass's :class:`~._context.BalanceContext`.

    Raises:
        BaselineMissingError: When ``ctx.scenario`` is ``None``.  A
            ``ValueError`` subclass.
    """
    if ctx.scenario is None:
        raise BaselineMissingError(
            "the balance_at seam requires a baseline scenario; this user has "
            "none, so no balance can be answered for them. Every owner gets one "
            "at registration (registration_service.register_user) and nothing deletes "
            "one, so reaching this means the data was changed outside the app: "
            "POST /grid/create-baseline repairs it",
            user_id=ctx.user_id,
        )
