"""
Shekel Budget App -- The reconcile panel's refusal of a ticked $0.00 box

Ruling **R-CC125**'s refusal, and the two sentences it says (ruling
**R-CC136**), asked by :func:`~._rows.record_settled` of each settle arm's ticked
items before that arm settles any.  It moved here from :mod:`._rows`, verbatim
but for its name, when that module neared pylint's 1,000-line ceiling (plan
step ``credit_card:CC-6``, its task plan#2958): it is the one part of that
module plan step ``balance:X-db`` deletes whole, once the status seam refuses a
``$0.00`` payment at every button.

Architecture (``CLAUDE.md``):
  - No Flask imports.  Plain data in; it raises or returns nothing.
"""

from decimal import Decimal

from app.exceptions import ValidationError


#: What the panel says to a ticked item whose amount box posted ``$0.00``
#: when its money goes OUT -- a bill, an envelope, a transfer's paying side
#: (ruling **R-CC125**, developer 2026-09-30, "Refuse it now", verbatim as
#: picked).  It names the ROW, never an id (ruling **R-CC98**); ``{name}`` is
#: the item's own name -- a row's, or a leg's label.
ZERO_PAYMENT_REFUSAL = (
    "{name}: a payment can't be $0.00. If it wasn't paid, leave it unticked "
    "or cancel it on the grid."
)

#: The same refusal when the item's money comes IN -- a paycheck, a transfer's
#: receiving side (ruling **R-CC136**, developer 2026-10-04, "Deposit
#: wording", verbatim as picked): *"Bills and money going out keep your
#: sentence. Money coming in reads 'Paycheck: a deposit can't be $0.00. If it
#: wasn't received, leave it unticked or cancel it on the grid.'"*
ZERO_DEPOSIT_REFUSAL = (
    "{name}: a deposit can't be $0.00. If it wasn't received, leave it "
    "unticked or cancel it on the grid."
)


def refuse_a_zero_payment(items: dict, corrections: "dict[int, Decimal]") -> None:
    """Refuse the press when a TICKED item's amount box posted ``$0.00``.

    Ruling **R-CC125** (developer 2026-09-30, "Refuse it now", amending
    **R-CC76**'s ``$0.00`` half at this panel): *"The panel refuses a $0.00
    payment today, as X-db will at every button ... Nothing changes"*.  A
    ``$0.00`` record takes a kept payment off the books
    (``status_seam._covering._withdraw``), and ruling **R-BAL155** refuses
    ``$0.00`` payments everywhere once plan step ``balance:X-db`` moves the
    refusal into the status seam and deletes this copy.  Until then the
    popovers keep their ``$0.00`` and its caption (rulings **R-CC56**,
    **R-CC59**, **R-CC129**) and this door refuses.

    **Asked of the posted box, on a ticked item only**: an unticked item's box
    is never read, and a row settling FROM its purchases carries no box
    (ruling **R-FF**), so an envelope's close over its purchases is untouched
    (R-BAL155's carry-over); an empty envelope settles from its figure and
    has a box, refused at ``$0.00`` like a bill's.  It refuses BEFORE this
    arm settles anything; an EARLIER arm of the same press has already
    settled and logged, and the route's ``except`` arm rolls the whole press
    back before ``reconcile._refusal`` redraws the panel, so nothing saves
    (the log lines stay -- the shape every refusal raised mid-press has).  A
    row whose own figure is ``$0.00`` ticked with its prefilled box CLEARED
    posts no figure and is not refused here: it records ``$0.00``, which
    takes a kept payment off the books (refused as out of date when that
    payment is matched, the page having named nothing; deleted unannounced
    when it is not).  Production held no such row by its stored inputs on
    2026-10-04 (482 Projected live rows holding no purchase, none with a
    ``$0.00`` stated or template figure; a figure an engine derives -- a
    paycheck, a derive-mode loan payment -- was not resolved), and the hole
    is ``balance:X-db``'s to close at every door at once (ledger row
    **BAL-596**).

    **The sentence follows the item's DIRECTION** (ruling **R-CC136**): money
    going out is refused as a payment (:data:`ZERO_PAYMENT_REFUSAL`), money
    coming in as a deposit (:data:`ZERO_DEPOSIT_REFUSAL`), asked of the item's
    own ``is_income`` -- a row's, or a leg's (a transfer's receiving side).

    Args:
        items: ``{tick id: item}``, what the arm loaded for the ticked ids --
            each with a ``name`` and an ``is_income`` (a row, or a leg).
        corrections: ``{tick id: amount}`` from the arm's amount boxes.

    Raises:
        ValidationError: Naming the first such item, in the arm's order.
    """
    for tick_id, item in items.items():
        amount = corrections.get(tick_id)
        if amount is not None and amount == 0:
            refusal = (
                ZERO_DEPOSIT_REFUSAL if item.is_income else ZERO_PAYMENT_REFUSAL
            )
            raise ValidationError(refusal.format(name=item.name))
