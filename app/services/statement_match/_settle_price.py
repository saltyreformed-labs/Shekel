"""What settling a still-planned plan item would book, signed: the offer's one price.

**One price for a row and a transfer leg** (ruling **plan#1983**, "price the
parent, draw the side").  A Projected row and a still-planned leg are offered
at what their settle verb would book, and both verbs book
``transaction_service.settle_amount`` of the item's PARENT -- the row itself,
or the leg's transfer -- so the figure is asked of the parent and signed for
the item's account.  ``_valuation.transaction_price`` and
``_leg_valuation.leg_price`` were two prices until that ruling's settle fold,
each routing to its own table's settle twin; the twins are one composition
now, and so is the price.

Its own leaf because both of its readers import it: :mod:`._valuation` imports
:mod:`._leg_valuation` and never the reverse, so the one price can live in
neither.

Services-boundary discipline (``CLAUDE.md`` Architecture): reads only, plain
data in, a ``Decimal`` out, no Flask import, no clock read.
"""

from __future__ import annotations

from decimal import Decimal

from app.exceptions import AmountUnresolvable
from app.services import cash_ledger, transaction_service, transfer_legs
from app.services.transfer_legs import PlanItem


def settle_price(
    item: PlanItem, basis: "cash_ledger.AmountBasis",
) -> "Decimal | None":
    """Return what settling the still-planned *item* would book, signed, or ``None``.

    The one branch in this function, and it is the verb's own partition rather
    than a money rule of its own:

    * a row that settles from its purchases
      (``transaction_service.settles_from_entries``, the verb's own
      predicate) is worth ``0`` to the offer: the bank line it could be is one
      of its purchases, each a candidate of its own (ruling **R-BAL78**).  A
      transfer holds no purchase, so a leg never takes this arm;
    * any other item is worth what settling its parent would book,
      ``transaction_service.settle_amount``, signed for the item's account by
      :func:`~app.services.cash_ledger.cash_leg_of`, which asks a row or a leg
      its direction and contributing gate.  A retained STATED record (a
      reverted row's kept figure, ruling **R-BAL61**, or a transfer's) is
      honoured there, so a kept movement's candidate is priced at what its
      re-settle books and not at a stale column.

    **It prices a still-planned item only** (plan step ``credit_card:CC-5-4a-1``,
    ruling **R-CC43**).  Through ``CC-5-3`` the row price carried a settled
    arm -- ``status_seam.covered_cash_leg`` on the screen's account (rulings
    **R-BAL81**, **R-CC40**) -- because a settled row was the candidate and
    its movement the price.  The movement is the candidate now
    (``_valuation.settlement_candidate`` reads it directly), so a settled item
    has no price here and this function is not asked for one:
    ``_candidates._transaction_candidates`` scopes on ``Projected``, a leg's
    loader on its transfer being Projected
    (``transfer_legs.offerable_transfer_legs``), and the re-price declines an
    item that has since settled.  A reverted row's kept movement re-settles
    through the row's door, so ``settlement_candidate``'s un-dated arm asks
    this too.

    **``settle_amount`` cannot refuse an item this module's scope admits**, and
    that is why there is no guard against one here.  It refuses exactly a
    soft-deleted parent; ``_candidates._transaction_candidates`` excludes a
    deleted row through ``balance_contributing_clause``, and the leg loaders
    offer live transfers only.  A ``try`` around it would be a guard nothing
    could ever observe, which this project has twice measured as worse than
    none; one arriving here is a defect to surface, not a row to count.

    **``AmountUnresolvable`` is a different thing and is REPORTED rather than
    swallowed or raised.**  It means the amount model had no rule for the
    parent.  A matcher cannot offer an item it cannot price without guessing,
    so the item leaves the candidate set; but a reader that dropped it
    silently would hide a broken row, and one that raised would make the whole
    review screen unreachable for one bad row with no in-app repair -- which
    is finding **N-302**'s shape.  :class:`~._subjects.Candidates` carries the
    count instead and the screen says so.

    Args:
        item: The Projected row (``entries`` loaded) or the still-planned leg
            (its transfer loaded by ``_leg_valuation.offered_legs``) to price.
        basis: The PASS's
            :class:`~app.services.cash_ledger.AmountBasis`, built once by
            :meth:`~._scope.ReviewScope.build` and threaded (plan step
            X-au-j).  Every offered row built its own until then, which finding
            **N-309** measured at **609 salary-pricing and 609 loan-pricing
            constructions** over 825 candidates and `4.7 s` to render -- and
            ``amount_basis``'s own docstring had already named calling the
            derivations per row as finding **N-228**.

    Returns:
        Its signed cash effect on the account its settle books on, or ``None``
        when the amount model cannot answer for its parent.
    """
    parent = transfer_legs.parent_of(item)
    if transaction_service.settles_from_entries(parent):
        # Its purchases ARE the figure (ruling **R-BAL78**) and each is a
        # candidate of its own, so the row is worth nothing to the offer.
        return Decimal("0")
    try:
        return cash_ledger.cash_leg_of(
            item, transaction_service.settle_amount(parent, basis),
        )
    except AmountUnresolvable:
        return None
