"""Where It Went, and the By-change lens: one category total, read two ways.

Both the grouped breakdown and the flat change rows reduce the SAME
``{category_id: _CategoryTotal}`` pair -- the chosen window's and its prior
window's -- which is what makes a group's delta and its By-change row the same
number rather than two computations that agree.  The caller totals each window
once (:func:`_totals_by_category`) and hands both maps to both builders.

Boundary discipline: no Flask import, no query -- these reduce rows the window
module already loaded.  All money is ``Decimal``; the templates do no math.
"""

from collections import defaultdict
from decimal import Decimal

from app.services import spending_analysis
from app.services.row_valuation import settled_contribution
from app.services.transfer_legs import PlanItem, parent_of
from app.utils.money import ZERO

from ._types import (
    ChangeRow,
    SpendingGroupRow,
    SpendingItemRow,
    _CategoryTotal,
)


def _totals_by_category(txns: list[PlanItem]) -> dict[int, _CategoryTotal]:
    """Sum settled spend per category id, carrying the display labels.

    Category id ``0`` is the Uncategorized bucket (rows with no category),
    so an uncategorized row never collides with a real category.  Labels
    come from the first row seen for the id: a real category id maps to
    exactly one ``(group, item)`` pair, and the Uncategorized bucket's
    labels are fixed by :func:`spending_analysis.category_names`.

    Args:
        txns: One window's settled expense items -- rows and transfer legs
            (leaf ``balance:X-bi-6-1b``), every one SETTLED, which is what
            :func:`~app.services.row_valuation.settled_contribution` of its
            parent requires and refuses without.

    Returns:
        ``category_id -> _CategoryTotal`` (labels + summed spend).  **A total
        may be NEGATIVE**, for a category whose refunds exceeded its purchases
        in this window (ruling **bank_import:R-II**); that is a real answer and
        not a bound to clamp.
    """
    amounts: dict[int, Decimal] = defaultdict(lambda: ZERO)
    labels: dict[int, tuple[str, str]] = {}
    for txn in txns:
        cat_id = txn.category_id if txn.category_id is not None else 0
        # **SIGNED, and the ``abs()`` that stood here was a MONEY defect**
        # (ruling **bank_import:R-II**, plan step ``bank_import:X-gj-2b``).
        # It was a provable no-op while ``ck_transaction_entries_positive_amount``
        # said ``amount > 0``: a settled envelope's figure is the sum of its
        # purchases, so an expense's contribution could not be negative.  That
        # step relaxed the CHECK to ``amount <> 0`` so a merchant refund files
        # as a NEGATIVE purchase, and the no-op became a SIGN FLIP -- measured
        # at ``-86.67 -> +86.67``, reporting `$86.67` of spending for a period
        # in which the account RECEIVED `$86.67`.
        #
        # A refund REDUCES a category's cost, which is what "did I stay in
        # budget" has to mean, so the signed contribution is the figure.  A
        # non-negative DISPLAY, if one is ever wanted, belongs at the render:
        # clamping here destroys the arithmetic the whole report is built on
        # (:func:`_share`'s denominator, every ``delta``, both group totals).
        amounts[cat_id] += settled_contribution(parent_of(txn))
        if cat_id not in labels:
            labels[cat_id] = spending_analysis.category_names(txn)
    return {
        cat_id: _CategoryTotal(
            group_name=labels[cat_id][0],
            item_name=labels[cat_id][1],
            amount=amount,
        )
        for cat_id, amount in amounts.items()
    }


def _build_breakdown(
    current_by_cat: dict[int, _CategoryTotal],
    prior_by_cat: dict[int, _CategoryTotal],
) -> list[SpendingGroupRow]:
    """Build the amount-descending 'Where It Went' group rows.

    Groups the chosen window's per-category totals by group name, computes
    every row's share of what the window MOVED (:func:`_share_base`), and
    attaches the signed window-over-window delta per item and per group (the
    D7 change basis).  A group's prior side sums EVERY prior-window category
    in that group -- including categories with no current spend -- so a
    stopped bill still moves its group's delta.

    Args:
        current_by_cat: The chosen window's per-category totals.
        prior_by_cat: The prior window's per-category totals.

    Returns:
        The group rows, amount-descending, each with amount-descending items.
    """
    share_base = _share_base(current_by_cat)

    items_by_group: dict[str, list[SpendingItemRow]] = defaultdict(list)
    for cat_id, cat in current_by_cat.items():
        prior = prior_by_cat.get(cat_id)
        prior_amount = prior.amount if prior is not None else ZERO
        items_by_group[cat.group_name].append(SpendingItemRow(
            category_id=cat_id,
            item_name=cat.item_name,
            amount=cat.amount,
            share=_share(cat.amount, share_base),
            delta=cat.amount - prior_amount,
            # **PRESENCE, not a zero total** (plan step
            # ``bank_import:X-gj-2b-3``).  ``prior_amount == ZERO`` asks the
            # wrong question: a category the prior window HELD can total zero
            # -- an envelope settled with no purchases contributes ``0`` and is
            # in that map, and since ruling **bank_import:R-II** a category
            # whose refunds cancelled its purchases does too -- and either one
            # then rendered the "new" badge on a category that is not new.  The
            # map's KEYS record which categories the window held, so absence is
            # the fact this wants and the value never was.
            is_new=prior is None,
        ))

    # A PLAIN dict rather than a ``defaultdict``, because the group rows below
    # read ABSENCE off it (``.get`` answering ``None``) for the same reason the
    # item rows read it off ``prior_by_cat``, and a default would answer
    # ``ZERO`` for a group the prior window never held.
    prior_group_totals: dict[str, Decimal] = {}
    for cat in prior_by_cat.values():
        prior_group_totals[cat.group_name] = (
            prior_group_totals.get(cat.group_name, ZERO) + cat.amount
        )

    rows = [
        _group_row(
            group_name, items, share_base,
            prior_group_totals.get(group_name),
        )
        for group_name, items in items_by_group.items()
    ]
    # **A ranking's key must TOTALLY order its input** (finding **P74**,
    # developer ruling 2026-08-25).  Keyed on ``amount`` alone, two groups that
    # spent the same are separated by nothing: ``items_by_group`` is built in
    # the query's row order, dicts preserve insertion order, and ``list.sort``
    # is stable -- so the database's arbitrary order decided the screen, and an
    # edit to either SELECT list could silently reorder it.  ``group_name`` is
    # unique across groups by construction (it is the dict key above), so
    # appending it makes the order a function of the DATA.
    rows.sort(key=lambda row: (-row.amount, row.group_name))
    return rows


def _group_row(
    group_name: str,
    items: list[SpendingItemRow],
    share_base: Decimal,
    prior_group_amount: "Decimal | None",
) -> SpendingGroupRow:
    """Assemble one group row from its (to-be-sorted) item rows.

    Args:
        group_name: The category group label.
        items: The group's item rows (sorted in place, amount-descending).
        share_base: The window's share denominator (:func:`_share_base`).
        prior_group_amount: The group's prior-window spend across ALL its
            categories, or ``None`` when the prior window held NO category in
            this group.  **The two are different facts and a zero cannot carry
            both** (plan step ``bank_import:X-gj-2b-3``): a group the prior
            window held can total zero, so reading ``== ZERO`` as "the group is
            new" put the badge on a group that had merely broken even.  The
            delta reads it as zero and :attr:`SpendingGroupRow.is_new` reads it
            as absence, which is why one argument states both.

    Returns:
        The :class:`SpendingGroupRow`.
    """
    # Total order, for the reason stated on the group sort above (**P74**),
    # and it ends in the row's IDENTITY rather than its name.
    #
    # **A first version of this ended at ``item_name`` on the argument that
    # ``uq_categories_user_group_item`` makes it unique within a group and the
    # Uncategorized bucket is alone in its own. The second half is enforced
    # NOWHERE**: that bucket is synthesised by
    # ``spending_analysis.category_names`` for a null-category row and is not a
    # table row at all, so the constraint cannot see it -- and
    # ``CategoryCreateSchema`` bounds ``group_name`` only by length, so an owner
    # may create a real ``Uncategorized: Uncategorized`` and sit two rows with
    # one key in one group. ``category_id`` is the identity and closes it
    # outright; ``item_name`` stays ahead of it so the visible order is still
    # alphabetical, which is what a reader wants from a tie.
    items.sort(key=lambda row: (-row.amount, row.item_name.lower(), row.category_id))
    group_amount = sum((row.amount for row in items), ZERO)
    return SpendingGroupRow(
        group_name=group_name,
        amount=group_amount,
        share=_share(group_amount, share_base),
        delta=group_amount - (
            ZERO if prior_group_amount is None else prior_group_amount
        ),
        is_new=prior_group_amount is None,
        items=items,
    )


def _share_base(by_cat: dict[int, _CategoryTotal]) -> Decimal:
    """Return the window's share denominator: what MOVED, ignoring direction.

    ``sum(abs(category total))`` over the window, and it is the denominator
    both :func:`_build_breakdown` and :func:`_group_row` divide by.

    **The NET window total stood here and it was unbounded** (developer ruling
    2026-09-01, plan step ``bank_import:X-gj-2b-3``).  While every category
    total was non-negative the net WAS this figure, so nothing changes for a
    window holding no refunds -- which is the property the ruling turned on.
    Once ruling **bank_import:R-II** let a category's refunds carry it below
    zero, the net denominator shrinks toward zero while a numerator does not:
    measured on ``Groceries 600.00`` beside ``Electronics -500.00``, a net of
    ``100.00`` rendered shares of **600%** and **-500%** at
    ``analytics/_spending.html``, over bars that looked right because
    ``analytics_view._bar_pct`` scales to the largest row and floors at zero.

    Summing MAGNITUDES bounds every share in ``[-1, 1]`` outright rather than
    narrowing the cases that break it: ``|group amount|`` is at most the sum of
    its own categories' magnitudes, which is at most this. The rejected
    alternative -- the positive categories only -- is unbounded for the same
    reason the net is, at ``$10.00`` of purchases beside a ``-$500.00`` refund.

    Args:
        by_cat: The window's per-category totals.

    Returns:
        The summed magnitudes as a ``Decimal``; ``Decimal("0")`` only for a
        window whose every category total is exactly zero (an empty window
        included), since a sum of magnitudes cannot be negative.
    """
    return sum((abs(cat.amount) for cat in by_cat.values()), ZERO)


def _share(amount: Decimal, base: Decimal) -> Decimal:
    """Return ``amount / base`` as a full-precision fraction, or zero.

    Args:
        amount: The row's spend, signed.
        base: The window's share denominator (:func:`_share_base`).

    Returns:
        ``amount / base`` -- a fraction in ``[-1, 1]`` -- or ``Decimal("0")``
        when *base* is zero, which is the only value it can take that has no
        shares to compute.  The guard tests EQUALITY rather than ``<=``: the
        base is a sum of magnitudes, so a negative one is unconstructible and
        a ``<=`` would be a branch no input can take.
    """
    if base == ZERO:
        return ZERO
    return amount / base


def _build_changes(
    current_by_cat: dict[int, _CategoryTotal],
    prior_by_cat: dict[int, _CategoryTotal],
) -> list[ChangeRow]:
    """Build the By-change rows over the union of both windows' categories.

    Every category with settled spend in either window gets a row, so a
    category that stopped (prior spend, zero current -- the D7 zero-month
    rider) is as visible as one that grew.  Labels prefer the chosen
    window's rows (a rename shows its current name); a zero-current row
    falls back to the prior window's labels.

    Args:
        current_by_cat: The chosen window's per-category totals.
        prior_by_cat: The prior window's per-category totals.

    Returns:
        The :class:`ChangeRow` list sorted by ``abs(delta)`` descending,
        ties broken by current spend descending, then item name.
    """
    rows: list[ChangeRow] = []
    for cat_id in current_by_cat.keys() | prior_by_cat.keys():
        current = current_by_cat.get(cat_id)
        prior = prior_by_cat.get(cat_id)
        labels = current if current is not None else prior
        current_amount = current.amount if current is not None else ZERO
        prior_amount = prior.amount if prior is not None else ZERO
        rows.append(ChangeRow(
            category_id=cat_id,
            group_name=labels.group_name,
            item_name=labels.item_name,
            current=current_amount,
            prior=prior_amount,
            delta=current_amount - prior_amount,
            # PRESENCE on BOTH sides (plan step ``bank_import:X-gj-2b-3``),
            # for the reason :func:`_build_breakdown` states: this row exists
            # because one of the two windows held the category, so "the prior
            # window did not" is the whole of what makes it new.  The old
            # ``current_amount > ZERO`` half was carrying the second test --
            # and it withheld the badge from a category that appeared this
            # window and broke even.
            is_new=prior is None,
        ))
    # **This key was cited as the immune exemplar and it was not immune**
    # (**P74**, found by adversarial review 2026-08-25). It carries neither the
    # group nor the id, and this list is FLAT across every category in both
    # windows -- so ``Home: Insurance`` and ``Auto: Insurance``, which
    # ``uq_categories_user_group_item`` permits by design, collide outright
    # whenever they share a current and a delta. That is MORE reachable than
    # the two cases this pass set out to fix: it needs no name collision, only
    # one label used under two groups. ``category_id`` is the identity.
    rows.sort(key=lambda r: (
        -abs(r.delta), -r.current, r.item_name.lower(), r.category_id,
    ))
    return rows
