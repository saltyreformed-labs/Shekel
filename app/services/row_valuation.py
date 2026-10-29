"""
Shekel Budget App -- What ONE row is worth, without asking any producer.

The pure half of the cash valuation: the arms that answer from the row itself
-- a status that excludes it, a soft delete, the SETTLEMENT it recorded, and
the figure the row OWNS.  Nothing here consults the amount model's live
producers, which is the whole reason it is a module of its own.

**The settlement arm is what makes the two halves total between them** (plan
step X-au-c3).  A row is a PLAN until its money moves and a RECORD of what moved
once it has, and the two never share a column: :func:`settled_figure` answers
from the record, the amount model answers from the plan, and a row is in
exactly one of the two states.  *The plan half used to be named here too, and
NOTHING of it survives.  It was an ``owned_amount`` accessor reading the
``estimated_amount`` column, which plan step X-bu deleted as a second spelling
of what :func:`~app.services.cash_ledger.resolve_transaction_amount` answers --
the two parted on a row whose plan a cutover had declared DERIVED (finding
BAL-462).  Its leaf, ``own_figure``, outlived it by one step, because
:func:`settled_contribution` below -- then named ``owned_contribution`` --
spelled the same column read at its
fall-through; plan step X-bx deleted that spelling too and moved the leaf into
``cash_ledger._amount_source``, where its only two callers are, as the private
``_own_figure`` (finding BAL-465).  This module states what a row RECORDED and
never what it PLANS.*  Before that split a settled row's record was
optional, so most settled rows fell through to the plan -- and since the plan is
a derivation, the plan had to be frozen at settle to stop a later price change
moving a figure the bank had already taken.

**It was made a LEAF because the loan stack could not NAME the cash-ledger
package, and the gate that forbade it was pylint's** (plan step X-au-c2).
:mod:`~app.services.cash_ledger._amount_source` reached UP into
``loan_payment_service`` for amount rule 4's producer (``LoanPricing``).  That
import was DEFERRED to call time, so no ``import`` STATEMENT closed a loop --
but ``cyclic-import`` (R0401) traces function-level imports too, so a
module-level ``from app.services.cash_ledger import ...`` anywhere reachable
from ``loan_payment_service`` closed the cycle for pylint.

*This paragraph used to add that "importing ``app.services.cash_ledger``
pulled in no loan service at all -- that much an adversarial review measured".
It is DELETED rather than tensed, because it is false and was false when it
was written: ``app/__init__.py`` is the application factory and imports the
tree, so ``import app.services.cash_ledger`` puts 264 app modules in
``sys.modules`` -- ``loan_payment_service`` among them -- on the pre-move tree
and the post-move one alike (measured 2026-08-31, identical both sides).  What
was true is the narrower claim the sentence above now makes: no import
statement in this package NAMED a loan service at module level.*

**THE REACH IS DELETED as of plan step X-au-g-2a, and this paragraph is kept in
the PAST TENSE rather than left standing as a live reason.**  That step moved
rule 4's producer DOWN into ``cash_ledger`` (``_loan_installment`` /
``_loan_pricing``), which is the unwind the paragraph below has always said
``X-au-g`` owes: the amount model is the lower tier and was asking a loan
service to price a row.  What that package names now are loan TERM primitives
-- ``loan_loaders``, ``loan_resolver``, ``escrow_calculator``,
``recurring_transfer_query`` -- none of which names ``cash_ledger``, so the
loan READING stack is free to import it, and plan step X-au-g-2c SPENT that
freedom: ``loan_payment_service.get_payment_history`` prices its feed through
``cash_ledger.contributions_by_key`` now, not through the accessor below.

**So this module's split is no longer FORCED, and saying so is the point of
keeping the history.  What survives is the SEAM; its ADDRESS is now
arbitrary.**  The arms here answer from the row alone and consult no producer,
which is a real distinction whichever tier can reach it -- but that argues for
a seam, not for a separate top-level module: ``cash_ledger._amounts`` and
``cash_ledger._amount_source`` are already two modules for exactly this
producer / no-producer split, inside one package.  Its registry scope stands
on separate ground
(``shekel_checkers/_fence_rulings._ROW_VALUATION_MODULES`` -- extracting a
fenced module's contents into an unfenced neighbour is finding N-28's shape,
which never depended on the cycle).  Whether it should FOLD BACK into
``cash_ledger`` now that it can is a question X-au-g-2a deliberately did not
take -- a second change, with its own diff and its own review -- and silence
here is not an answer to it.  There is still exactly ONE definition of each
rule, which is the claim :mod:`app.services.cash_ledger._amounts` exists to
make; it simply lives in a file both tiers can reach.

**Every rule here is a question about a PARENT** (ruling **plan#1983**,
"price the parent, draw the side"): a plan row, or a transfer.  A page draws
the SIDES a transfer has on its two accounts and values each by its parent
(:func:`~app.services.transfer_legs.parent_of`), so both legs of one transfer
are read off one stored figure.  The one fact that lives in two tables, what a
settled parent RECORDS, is :func:`_recorded_figure`, the module's one branch on
the parent's table; the status gate, the exclusion arm and the settled-only
refusal above it are each written once.  Until that ruling each had a leg twin
(``leg_settled_figure``, ``leg_fixed_contribution``,
``leg_settled_contribution``, ``leg_settled_amounts_by_key``) restating it arm
for arm over a leg's parent.

Services-boundary discipline (``CLAUDE.md`` Architecture / B6-01): ORM rows in,
``Decimal`` out; no Flask import, no writes, and no query this module ISSUES.
:func:`settled_figure` reads the ``entries`` relationship for EVERY settled
row since plan step ``balance:X-bi-4b-1``, which lazy-loads if a caller did
not eager-load it -- the same access ``status_seam.covered_cash_leg`` makes
for a settled row, and the fold's own loader, the grid's and the companion's
issue ``selectinload(Transaction.entries)`` so no routed caller pays for it
per row.
"""

from collections.abc import Iterable
from decimal import Decimal

from app.exceptions import AmountUnresolvable
from app.models.transfer import Transfer
from app.services.transfer_legs import PlanItem, PlanParent, cell_key, parent_of
from app.utils.balance_predicates import (
    is_balance_contributing,
    settled_status_ids,
)


def purchases_total(entries) -> Decimal:
    """Return the sum of a row's purchases -- ALL of them, debit and credit.

    The figure an envelope's close BOOKS (``transaction_service.
    settle_from_entries``), and the one statement of it; since plan step
    ``balance:X-bi-4b-1`` :func:`settled_figure` below is the same reduction
    over the row's whole family, so the two cannot part on a row that holds
    purchases alone.  Both kinds of entry count: the credit portion leaves
    the account through its CC Payback sibling rather than through this row:
    the fold and the ledger read it as a movement that moves nothing
    (:func:`~app.services.cash_ledger.movement_cash_leg`) and the reconcile
    panel subtracts it (:func:`~app.services.cash_ledger.off_statement_sum`),
    so removing it here would take it out twice.

    **It lives HERE rather than in ``entry_service``, where it was
    ``compute_actual_from_entries``** (plan step X-au-c3).  Two reasons, and the
    first is mechanical: :func:`settled_figure` below needs it, this module sits
    under both the cash and loan tiers, and ``entry_service`` reaches down
    through ``posting_service`` into ``cash_ledger`` -- so importing it here
    would close a cycle.  The second is that the old name referred to
    ``actual_amount``, a column this step removed; a helper named for a column
    that no longer exists is a citation a reader cannot follow.

    Pure: no database access, no ORM attribute beyond each entry's ``amount``.

    Args:
        entries: The row's :class:`~app.models.transaction_entry.TransactionEntry`
            rows, in any order.

    Returns:
        Their summed ``amount`` as a ``Decimal`` -- ``Decimal("0")`` for an
        empty sequence, which is the "no purchases recorded" answer rather than
        a missing one.
    """
    return sum((entry.amount for entry in entries), Decimal("0"))


def _recorded_figure(parent: PlanParent) -> "Decimal | None":
    """Return what a SETTLED *parent* records as having moved, read off its own table.

    **The recorded-figure fact, and its ONE branch on the parent's table**
    (ruling **plan#1983**).  A plan row's record is its entries and the
    figure is their sum (:func:`settled_figure` says why); a transfer stores
    its figure once, on itself
    (:attr:`~app.models.transfer.Transfer.settled_figure`, rulings
    **R-BAL365**, **R-BAL366**), so both its legs read the one figure.  Every
    rule above this -- the status gate, the exclusion arm, the settled-only
    refusal -- is written once against a parent and branches on nothing.

    Args:
        parent: A :class:`~app.models.transaction.Transaction` (its
            ``entries`` read) or a :class:`~app.models.transfer.Transfer`.

    Returns:
        The row's entry sum (``Decimal("0")`` for none), or the transfer's
        stored figure (``0.00`` for a close of nothing, ``None`` when it
        stores none).
    """
    if isinstance(parent, Transfer):
        return parent.settled_figure
    return purchases_total(parent.entries)


def settled_figure(parent: PlanParent) -> "Decimal | None":
    """Return what *parent* RECORDS as having moved, or ``None`` if it has not settled.

    **The one accessor for the settlement record** (plan step X-au-c3), and the
    reason the amount model needs no freeze.  A row is a PLAN --
    ``estimated_amount`` priced by ``amount_source_id`` -- until its money moves,
    and once it has, it also carries a RECORD.  Every money reader of a settled
    row asks this and never the plan, so a definition's price series may gain
    a version dated into the past -- which it legitimately does, because a
    series records what a price WAS -- without moving a figure the bank
    already took.

    **THE RECORD IS THE ROW'S ENTRIES, and the figure is their sum** (plan
    step ``balance:X-bi-4b-1``, ruling **R-BAL80**; the readers move here,
    the columns go at ``X-bi-4b-2``).  A bill's or a paycheck's money is its
    one covering movement (``status_seam._covering``, ruling **R-BAL39**; a
    transfer's figure is the transfer's own, :func:`_recorded_figure`'s other
    arm); an envelope's is its purchases; a close of nothing
    holds no entry and records ``$0.00`` (ruling **R-BAL82**: a movement of
    nothing is not one, so a ``$0.00`` close is a close with no entries).  One
    kind-blind reduction, the same one :func:`purchases_total` performs for
    the close that books it and ``posting_reads.settled_figure_clause``
    performs in SQL, over the same rows the fold books one by one on their
    own days (``cash_ledger._events.settled_cash_facts`` reads the movements
    and nothing of the row, ruling **R-BAL80**; a card purchase is a row of
    the record that moves no cash of its own, :func:`purchases_total` says
    why) -- so the figure a screen shows and the money the balance counts
    are read off ONE home.  Through ``X-bi-4a`` this read the
    row's own ``settled_amount`` / ``settled_basis_id`` -- a ``derived`` or
    ``corrected`` record's stored figure, a ``purchases`` record's entry sum
    -- and REFUSED a settled row recording nothing; the columns were the
    covering movement's stale cache (rule 14), and the refusal guarded a
    state that has no spelling here: a settled row with no entries IS the
    ``$0.00`` record.

    **THE STATUS DECIDES, NOT THE RECORD** (plan step X-au-c3, developer
    2026-08-17).  A row that has been reverted out of the settled band still
    CARRIES what it recorded -- a revert releases the assertion (``settled_on``
    and the clearing link) and keeps what moved (the covering movement,
    un-dated, ruling **R-BAL61**), so the revert / edit / re-settle round trip
    the full-edit popover instructs the user to perform does not destroy a
    figure they read off a statement (``status_seam.apply_status_change``).
    What keeps that retained figure out of every balance is this gate: a row
    that is not settled is worth its PLAN, whatever it still remembers.
    Reading the record's presence here instead would be the same question
    answered by the wrong fact -- and it is the reason a first version of
    X-au-c3 had to DESTROY the figure on a revert.

    Args:
        parent: The row or transfer being asked (a leg asks its
            :func:`~app.services.transfer_legs.parent_of`).  ``status_id`` is
            read first, so a parent that has not settled costs one frozenset
            membership test; a row's ``entries`` relationship is read for
            every SETTLED row.  Callers that value
            many rows eager-load it through
            :func:`app.utils.amount_relationships.settlement_load_options`
            (alone for a settled-only reader -- the loan ledger's walk and
            confirmed history, the asset and investment contribution passes
            -- or inside ``valuation_load_options`` for a pass that prices
            too: the fold's loader, the grid's, the companion's); the
            reconcile panel, the matcher, ``spending_analysis`` and
            ``savings_dashboard_service._metrics`` spell the same
            ``selectinload(Transaction.entries)``.

    Returns:
        The recorded figure (:func:`_recorded_figure`) -- for a row the sum of
        its entries, ``Decimal("0")`` for a settled row holding none -- or
        ``None`` when *parent* is not settled.
    """
    if parent.status_id not in settled_status_ids():
        return None
    return _recorded_figure(parent)


def settled_amounts_by_key(
    items: Iterable[PlanItem],
) -> "dict[int | tuple[int, int], Decimal | None]":
    """Return ``{cell_key(item): what its parent RECORDED as having moved}``.

    The batch every SCREEN reads to show a settled item's figure (plan step
    X-au-c3), keyed by the side and valued by its parent (ruling
    **plan#1983**): a row by its id and its own record, a transfer leg by its
    ``(transfer id, account id)`` and its transfer's one figure, so the
    ``settled`` map a page publishes holds both shapes and both legs of one
    transfer read the same figure.  ``settled_amounts_by_id`` and its leg
    twin ``leg_settled_amounts_by_key`` (leaf ``X-bi-6-1``) were the two
    halves of it until that ruling.  It is the sibling of
    :func:`~app.services.cash_ledger.amounts_by_key`
    rather than a second spelling of it: that map answers what a row's amount
    IS -- its plan -- and this one answers what its money DID.  A screen shows
    the second where there is one and the first otherwise, which is the same
    precedence the balance uses (:func:`fixed_contribution`).

    **It lives HERE rather than beside its sibling in
    ``cash_ledger._amounts``, and the reason is the split this module IS**: the
    budget map takes an ``AmountBasis`` because a plan may be DERIVED and need a
    producer to answer it, while a settlement RECORD is the row's own and needs
    none.  The producer-free half belongs in the producer-free module -- and
    ``cash_ledger`` re-exports it exactly as it re-exports
    :func:`settled_contribution`, so no consumer names two modules.  It was
    written into ``_amounts`` and moved here in the same step, when that
    module's 1,000-line cap refused it: the cap did its job.

    ``None`` for a row that has not settled, which is what a template branches
    on.  It is published as a MAP rather than read off the row because a
    settled row's figure is a reduction over its entries, which a Jinja
    template has no business performing -- and because the popovers'
    Actual prefill (``routes/_render_helpers``) reads the SAME map since plan
    step ``balance:X-bi-4b-1``.  That prefill had a TOTAL twin of its own,
    ``recorded_amounts_by_id`` over ``recorded_figure``, whose one clause was
    "``None`` for a settled row that RECORDS NOTHING" (finding **N-181**'s
    legacy shape, which the box existed to repair) where this map raised;
    a settled row with no entries is the ``$0.00`` record now (ruling
    **R-BAL82**), so the state the twin answered differently has no
    spelling, and two names for one reduction is rule 14's two spellings.
    The twin is deleted; every reader of the record asks this.

    Args:
        items: The rows and legs a surface is about to render.

    Returns:
        ``{cell_key: Decimal | None}`` covering every item.
    """
    return {cell_key(item): settled_figure(parent_of(item)) for item in items}


def fixed_contribution(parent: PlanParent) -> "Decimal | None":
    """Return what *parent* is worth WITHOUT resolving its amount, or ``None``.

    The one statement of the two arms that answer before the amount model is
    consulted at all, so every valuation built on it -- the batch, the one-row
    form, and the cheap accessor -- cannot come to disagree about them:

      * a row that does not contribute -- soft-deleted, Credit or Cancelled --
        is worth ``0``, whatever prices it; and
      * a row whose money has MOVED is worth what it recorded
        (:func:`settled_figure`), because a record of what left the account is a
        fact and a plan is a forecast.

    ``None`` means neither applies -- the row has not settled -- and its own
    amount decides, which is the resolver's question.

    **The second arm was ``txn.actual_amount`` until plan step X-au-c3**, and the
    difference is the whole step.  That column was only populated when a human
    typed a correction, so a settled row that had no correction answered ``None``
    here and fell through to its PLAN -- and because a plan is a derivation, the
    plan then had to be frozen at settle so a later price change could not move a
    recorded past.  A settled row now always answers, so no settled row's balance
    reads its plan and there is nothing to freeze.

    **The first arm is why the status gate sits ABOVE the resolver** (plan step
    X-au-c2).  ``Transaction.effective_amount`` answered ``$0.00`` for an
    excluded row from inside the valuation, where the resolver of the day would
    REFUSE the same row: both live producers filtered to Projected rows, so a
    Cancelled salary row was absent from the map and had no derived answer at
    all.  *NEITHER producer survives -- ``cash_ledger.LoanPricing.live_cash``
    went at plan step X-au-g-2c-2 and ``income_service.live_projected_net`` at
    **X-au-d** -- and the rules that replaced them read no status, so rule 2 and
    rule 4 now price a Cancelled row like any other.*  The ordering is kept
    anyway and is not merely vestigial: asking what a row is WORTH before
    asking what it is PRICED at is what stops an excluded row paying for a
    derivation nobody is counting, and it is what makes the gate a property of
    this module rather than a precondition each producer must restate.

    **Order matters between the two arms, and it is unchanged**: an excluded row
    is worth ``0`` even if it settled first and was cancelled after, because
    ``excludes_from_balance`` is a statement about whether the row counts at all.

    **A leg is worth what its parent is** (ruling **plan#1983**): a caller
    holding a transfer leg asks its
    :func:`~app.services.transfer_legs.parent_of`, so an excluded transfer is
    worth ``0`` and a settled one its stored figure on both legs alike.
    ``leg_fixed_contribution`` restated these two arms over a leg's parent
    until that ruling.

    Args:
        parent: The row or transfer being valued.  ``is_deleted`` and the
            ``status`` relationship are read (``status`` is
            ``lazy="joined"``), then the settlement record.

    Returns:
        The parent's worth when it needs no resolution, else ``None``.
    """
    if not is_balance_contributing(parent):
        return Decimal("0")
    return settled_figure(parent)


def settled_contribution(parent: PlanParent) -> Decimal:
    """Return what a parent that has SETTLED contributes, refusing one that has not.

    The cheap accessor for a reader whose rows have ALL settled -- the loan
    replay, the loan posting sync and its confirmed history, the settled-spend
    metric and the spending report.  **All six load their rows with
    ``status_id.in_(settled_status_ids())`` in SQL**, so building an amount
    basis for them would run the paycheck engine to re-derive a figure the
    row already recorded.  A seventh, ``cash_ledger.settled_cash_leg`` -- the
    statement matcher's pricing of a settled row, which issued no query of its
    own and branched on ``txn.status.is_settled`` at its one caller -- is
    deleted at plan step ``balance:X-bi-4a`` (ruling **R-BAL81**: a settled
    row is worth what its covering movement moves, a stored figure).

    **Three readers once admitted a row of ANY status and CAUGHT this
    refusal**, and they are gone with the seventh.  ``statement_match``'s
    ``_accepted_view._accepted_row``, ``_release._subject_removal`` and
    ``._container_removal`` reached here through ``settled_cash_leg`` and
    caught the refusal by name, because they render the review page and a
    raise there would make the screen permanently unreachable for the account
    with no in-app repair (finding **N-302**); for them the refusal was an
    ANSWER.  Since ruling **R-BAL81** all three read
    ``status_seam.covered_cash_leg``, which raises nothing, so every reader
    left is settled-only by its own query -- a claim the refusal states
    rather than a docstring asserts, and nowhere does it reach a user as a
    500.

    **It is :func:`~app.services.cash_ledger.contribution_of`'s PARTIAL twin,
    and the two share every line but the last** (plan step X-bx).  Both gate an
    excluded row to ``0`` and answer a settled row from its SETTLEMENT, through
    the one :func:`fixed_contribution` below.  Where a row has NOT settled, that
    one resolves the row's plan and this REFUSES -- so the two can never
    disagree about a figure, only about whether there is one to give.

    **The refusal is the step, and what it replaced was a SECOND SPELLING of
    the plan** (finding **BAL-465**).  This fell through to
    ``own_figure(txn.estimated_amount, ...)``: the plan column read by hand,
    where ``cash_ledger.resolve_transaction_amount`` is the one producer of a
    row's plan.  That is the defect ``CLAUDE.md`` rule 14 names, and the one
    plan step X-bu deleted from the BUDGET side (``owned_amount``, finding
    **BAL-462**); it was unexposed here only because :func:`fixed_contribution`
    answers a settled row first, which is a head start and not a guarantee.

    **Routing that fall-through to the RESOLVER was the other option, and it is
    the wrong answer for these readers** (developer, 2026-09-06).  Their
    question is what a row's money DID, and a row that has not settled has done
    nothing: a settled row's record means CONFIRMED cash effect, so pricing
    a projected row's forecast there would publish a plan as a fact.  That is the
    substitution plan step X-au-c3 exists to remove, and :func:`settled_figure`
    refuses to perform it for exactly the same reason.  Resolving would also
    have made today's LOUD failure silent: a derived projected row raises here
    now, and the amount model would have priced it.

    **THE SEVEN-CALLER CENSUS THIS DOCSTRING CARRIED IS DELETED, AND THAT IS THE
    POINT OF THE STEP.**  It listed every call site and had to be RE-RUN at each
    per-kind cutover that widened the derived class, because a wider derived
    class widened what this accessor could be handed and the fall-through would
    price it from a column that may not exist.  There is nothing left to widen
    into: the precondition is stated by the call rather than asserted about it.

    *A mutation probe measured the change before it was made (2026-09-06): with
    this fall-through spliced to a refusal the full suite ran **13,338 passed,
    9 failed**, and all nine were TESTS calling the accessor directly on a
    Projected row, with no app frame in any traceback.  **That number bounds
    LESS than it appears to, and two adversarial reviews said so
    independently.**  Three ``statement_match`` readers catch
    :class:`~app.exceptions.AmountUnresolvable` by name and answer around it, so
    on those paths the refusal changes an ANSWER rather than failing a test --
    a green suite cannot tell "never reached" from "reached and re-answered"
    there.  What the probe does establish is the narrower claim: no path turns
    this into a 500.  The three catchers are graded by cases added with this
    step, because before it the suite entered none of them.*

    **The NAME changed with the body** -- it was ``owned_contribution``, and it
    asserted that the row owns its figure.  That is not what this function is
    about and was never what its readers meant, and the stale assertion was
    load-bearing rather than cosmetic: a :class:`~app.models.transfer.Transfer`
    was handed to it (``tests/test_routes/test_transfers/``), and a
    ``Transfer`` defines no ``estimated_amount`` at all, so the Cancelled row's
    ``0`` was the only thing between that call and an ``AttributeError``.

    **The history the deleted census recorded is kept in ONE sentence, because
    it is a reason and not a list.**  Two readers were once not settled-only --
    ``loan_payment_service.get_payment_history`` (finding **N-266**(a)) and
    ``balance_at._plan._planned_from_shadows`` -- and plan step X-au-g-2c-1
    routed both onto :func:`~app.services.cash_ledger.contributions_by_key`,
    which is where a reader that may see a PROJECTED row still belongs.  The
    two drafts of that paragraph were wrong in opposite directions, one claiming
    every caller was settled-only while a survivor stood and the next still
    naming the survivor after it had moved; a claim quoted as a REASON decays
    invisibly, because nobody re-checks a premise.  Neither draft could be wrong
    now: the refusal states the precondition instead of a docstring asserting it.

    **A leg has SETTLED when its parent has** (ruling **plan#1983**): the
    loan and contribution readers read the settled half of
    :func:`app.services.loan_loaders.income_legs`, the Spending report
    narrows to settled transfers in SQL and the savings metric to DATED
    records (plan step ``balance:X-bi-6-4d-2``, finding **BAL-534**: equal,
    since a side's record is dated exactly while its transfer is settled),
    and each asks this of the leg's
    :func:`~app.services.transfer_legs.parent_of`.  ``leg_settled_contribution``
    restated this refusal over a leg's parent until that ruling.

    Args:
        parent: The row or transfer being valued.  Its caller's query has
            already restricted it to a settled status; ``is_deleted`` and the
            ``status`` relationship are read first (``status`` is
            ``lazy="joined"``), then the settlement record.

    Returns:
        ``0`` for a parent that contributes nothing -- soft-deleted, Credit or
        Cancelled -- else the figure it RECORDED as having moved (a
        transfer's ``0.00`` for a close of nothing, ruling **R-BAL365**).

    Raises:
        AmountUnresolvable: When the parent has NOT settled, so it recorded
            nothing and this reader was handed one outside its own scope.
    """
    fixed = fixed_contribution(parent)
    if fixed is None:
        raise AmountUnresolvable(
            f"{type(parent).__name__} {parent.id} has not settled, so it "
            "recorded nothing as having moved. This accessor answers what a "
            "parent's money DID. There is deliberately no fall back to its "
            "PLAN: a plan is a forecast, and answering one here would report "
            "money as having moved when it has not. A caller that means the "
            "plan asks the amount model (cash_ledger.contribution_of), which "
            "resolves it. Reaching this means a settled-only reader was handed "
            "a row or transfer outside its scope."
        )
    return fixed
