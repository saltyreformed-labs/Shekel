"""What one row MOVES through its cash account, as against what it is worth.

The confirmed-cash-effect family, extracted from :mod:`._amounts` at plan step
``bank_import:X-f6a-2`` when a second consumer arrived and pushed that module
past its 1,000-line ceiling.  **The cut is a subject rather than a size**: its
neighbour answers *what is this row worth* -- a valuation composing an amount
with an entered actual, an excluded status, a soft delete and an envelope's
purchases -- and these answer the narrower question *how much of it actually
crosses this bank account*.

**A MOVEMENT's leg is the one the balance and the ledger read, and since plan
step ``balance:X-bi-3b`` it is stated here ONCE** (:func:`movement_cash_leg`).
A purchase recorded against an envelope, and the covering movement a settle
writes for a bill, a paycheck or a transfer leg, moves its whole figure
through its account in the parent's direction -- ruling **R-BAL35**: a
movement's category, type and scenario are its plan row's, read through
``transaction_id`` and never copied, so its direction is read the same way.
Six readers spelled *a purchase is money leaving* for themselves before that
step (the walk's fact producer, the ledger writer's target, the seam's family
valuation, the statement matcher's offer, its accepted register and its undo
dialog), every one of them correct for a purchase and every one of them
``-figure`` for a covered paycheck, which is wrong by twice the figure.  The
sign follows the transaction TYPE, never the account class, so the leg is
correct whether the cash account is an asset (Checking) or a liability (a
direct charge on a Credit Card account).

**A SETTLED ROW's own leg has NO reader since plan step ``balance:X-bi-4a``**
(rulings **R-BAL77**, **R-BAL80**, **R-BAL81**).  The cash walk and the
posting writer read a settled row's money as its MOVEMENTS and nothing else,
and the statement matcher prices a settled row at what its covering movement
moves (``status_seam.covered_cash_leg``, ruling **R-BAL81**).  Through
``X-bi-3e`` all of them read the row's leg, one rule in one expression --
``gross - Sigma(card entries) - Sigma(already-dated purchases)``, signed by
type -- which was zero for every covered bill and paycheck by ruling
**R-FM**'s identity and, for a ``purchases``-basis envelope, its un-dated
purchases booked on the close day; the matcher kept that spelling
(``settled_cash_leg``) for one step longer, offering an envelope ROW priced
at its un-dated purchases beside the purchases themselves, and accepting the
row dated a day the fold no longer read.  Those purchases are movements in
flight now, the row books nothing, and the function is deleted.  What
remains of the row-leg family here is :func:`cash_leg_of` -- the matcher's
pricing of a PROJECTED row that holds no purchases, what the bank would see
if the row it names settled -- and :func:`off_statement_sum` with its three
terms, the reconcile panel's cash figure beside the booked one and the
matcher's corrected figure for a line, all ``bank_import``'s questions.

**The subtracted terms are why the row-leg family existed at all.**  A
card purchase leaves later through its own CC Payback sibling, and a purchase
carrying a recorded bank posting day is already a cash movement of its own on
its own day (ruling **R-FM**, plan step ``balance:X-f3b``) -- so an
envelope's close booked only the remainder, or the same dollars left the
account twice.  That was measured: entry 89 (`$12.79`, taken by the bank on
2026-08-12) was being taken a second time by its envelope's 08-13 close,
reading the whole of that day `$12.79` low (finding **N-274**).  The third
term arrived with the first door that files a purchase on another account
than its row's (plan step ``credit_card:CC-5-2``): a card swipe in a
checking envelope is a movement on the CARD, and checking's statement never
shows it, flag or no flag.

**Where ``gross`` comes from is the CALLER'S, and it must be.**  A projected
row is worth what settling it would book, which is
``transaction_service.settle_amount`` for an ordinary row and
``transfer_service.leg_settle_amount`` for a transfer leg.  Asking this module to
choose between them would put the settle verbs' own partition in a third place,
and it cannot import either without a cycle.

Services-boundary discipline: pure per-row reads.  No query, no clock, no Flask
import, no write.
"""

from __future__ import annotations

from decimal import Decimal

from app.models.transaction import Transaction
from app.utils.balance_predicates import is_balance_contributing


def credit_entry_sum(txn: Transaction) -> Decimal:
    """Return the sum of a transaction's credit (credit-card) entry amounts.

    The ``Sigma(credit entry amounts)`` term of the confirmed-cash-effect
    formula: an envelope's credit purchases are excluded from the checking
    outflow because each posts its own CC Payback when that payback settles
    (``credit_workflow``), so counting them here would double-count against the
    payback.  A plain transaction has no entries, so this is ``Decimal("0")``
    and the effect collapses to ``effective_amount``.

    **PUBLIC since plan step X-f2-c3, for the reconcile panel** (finding
    **N-226**).  That panel offers an envelope at what a tick would BOOK, which
    is ``sum(entries)`` over every entry INCLUDING the card ones -- against a
    statement that shows only the debit half.  The panel therefore prints the
    cash figure beside the booked one, and it takes this term rather than
    writing ``entry.is_credit`` a second time: the two would then be one rule
    in two places, on the screen a user reads beside a paper statement.

    **It reads the row's PURCHASES, not its family** (ruling **R-BAL68**):
    a card purchase is something a person recorded against the row, and
    ``Transaction.purchases`` is the one reading of that.  The seam's covering
    movement is never a card entry, so this term read the same over
    ``entries``; it is spelled over the purchases because that is what it
    means, and because its twin below was not the same over both.

    Args:
        txn: The transaction whose credit purchases to sum.

    Returns:
        The sum of ``amount`` over the transaction's ``is_credit`` purchases,
        as a ``Decimal`` (``Decimal("0")`` when there are none).
    """
    return sum(
        (entry.amount for entry in txn.purchases if entry.is_credit),
        Decimal("0"),
    )


def posted_purchase_sum(txn: Transaction) -> Decimal:
    """Return the sum of a transaction's purchases that have ALREADY posted.

    The ``Sigma(posted debit purchases)`` term ruling **R-FM** adds to the
    confirmed cash effect (plan step X-f3b).  A purchase carrying a recorded
    bank posting day books its OWN cash leg on its OWN day
    (``_posting_purchases.emit_purchase_deltas``), so a row's leg priced
    beside it must leave it out or the same dollars are counted twice.

    A DEBIT purchase only: a card purchase never touches checking at all, and
    :func:`credit_entry_sum` is the term that removes it.  The two are disjoint
    by construction (``is_credit`` partitions the entries), so subtracting both
    subtracts nothing twice.  A plain transaction has no entries, so this is
    ``Decimal("0")`` and the effect collapses to ``effective_amount``.

    **PUBLIC for the same reason** :func:`credit_entry_sum` **is**: the reconcile
    panel prints what the STATEMENT will show for a tick beside what the tick
    BOOKS, and those differ by exactly these two terms.  It takes this one rather
    than writing ``entry.settled_on is not None`` a second time, so a change to
    what "already posted" means cannot leave the panel saying the old thing.

    **It reads the row's PURCHASES, not its family, and through plan step
    ``balance:X-bi-4a``'s first cut it did not** (ruling **R-BAL68**: a
    reader that means the purchases asks for them; the seam's covering
    movement is not one).  Over ``entries`` this summed a settled bill's own
    covering movement -- dated, debit, and exactly the row's figure -- as a
    purchase that had posted.  Through ``X-bi-3e`` that was load-bearing:
    ``settled_cash_leg`` subtracted it from the recorded figure, which is how
    a covered bill's own leg read zero (ruling **R-FM**'s identity) and the
    reason R-BAL68's re-point passed this module by.  With that leg deleted
    (ruling **R-BAL81**) the term had one reader left that could meet a
    settled row -- the matcher's corrected figure for a near-miss line,
    ``_landing.corrected_figure`` = ``|bank| + off_statement_sum`` -- and
    there it doubled: a `$178.32` bill covered on 01-09, matched to the
    bank's `-178.29`, booked `356.61` onto its own movement and the accept
    door's post-apply check refused the act as one that moved its own row
    (finding **BAL-523**; measured 2026-09-18 on production's own release
    through the settle door, and confirmed by the developer on the reconcile
    screen the same evening).  That reader's term is deleted with the fix;
    this sum reads the purchases for the one reader it has left.

    **And on the ROW's account** (plan step ``credit_card:CC-5-2``).  A
    purchase whose money moved through another account is
    :func:`elsewhere_purchase_sum`'s whatever its posting day says: dated or
    not, its cash never crossed this account, and counting a posted card
    swipe here as well would take it out of the reconcile panel's cash figure
    twice.  The three terms of :func:`off_statement_sum` partition the row's
    purchases by construction -- the flag, the account, then the day -- so
    no purchase is in two of them.

    Args:
        txn: The transaction whose posted purchases to sum.

    Returns:
        The sum of ``amount`` over the transaction's debit purchases on its
        own account carrying a ``settled_on``, as a ``Decimal``
        (``Decimal("0")`` when there are none).
    """
    return sum(
        (
            entry.amount for entry in txn.purchases
            if not _moves_elsewhere(txn, entry) and entry.settled_on is not None
        ),
        Decimal("0"),
    )


def elsewhere_purchase_sum(txn: Transaction) -> Decimal:
    """Return the sum of a transaction's purchases whose money moved ELSEWHERE.

    The account-keyed arm beside the flag's (plan step ``credit_card:CC-5-2``,
    rulings **R-CC15** and **R-BAL75**): a purchase naming an account other
    than its row's -- a card swipe recorded in a checking envelope -- is a
    movement on THAT account.  Its row's statement never shows it, exactly as
    it never shows a flag-marked purchase, so the reconcile panel's cash
    figure leaves it out for the same reason :func:`credit_entry_sum` leaves
    the flagged ones out.  The flag is the CHEAT's spelling of this fact for
    the legacy lines that carry it with no account of their own; ``CC-7``
    converts those lines and deletes the flag, its arm, and the
    ``not is_credit`` clause below, which then partitions nothing.

    **A purchase carrying BOTH is unwritable at both purchase doors**
    (``entry_service._refusals._reject_flag_beside_another_account``), and
    this arm still excludes the flag so that :func:`off_statement_sum`'s
    three terms partition the purchases whatever a row holds -- a rule that
    rests on a door alone is one refactor from resting on nothing.

    **It reads the row's PURCHASES, not its family** (ruling **R-BAL68**), as
    its two siblings do.  The seam writes a covering movement on the TENDER
    since ``CC-5-3`` (the row's own account unless the owner named another,
    ruling **R-CC15**), which is never a purchase, and no reconcile offer
    holds a settled row with a covering movement anyway.

    Args:
        txn: The transaction whose purchases to sum.

    Returns:
        The sum of ``amount`` over the transaction's unflagged purchases whose
        ``account_id`` is not the row's, as a ``Decimal`` (``Decimal("0")``
        when there are none).
    """
    return sum(
        (
            entry.amount for entry in txn.purchases
            if not entry.is_credit and entry.account_id != txn.account_id
        ),
        Decimal("0"),
    )


def _moves_elsewhere(txn: Transaction, entry) -> bool:
    """Return whether *entry*'s money is NOT on its row's account.

    The ONE predicate the three off-statement terms partition on: the flag
    (the cheat's spelling, until ``CC-7``) or an account of the purchase's
    own that differs from the row's (plan step ``credit_card:CC-5-2``).
    :func:`credit_entry_sum` and :func:`elsewhere_purchase_sum` are its two
    halves stated separately, because the flag's half is deleted at ``CC-7``
    and the account's half is what remains; :func:`posted_purchase_sum` reads
    it whole so the third term is exactly the complement.
    """
    return entry.is_credit or entry.account_id != txn.account_id


def off_statement_sum(txn) -> Decimal:
    """Return what *txn* BOOKS but does not move through its cash account.

    The three terms a row can carry that never reach this account's
    statement, stated once for the reconcile panel's cash figure beside the
    booked one (``reconcile_service._transactions``), its one reader since
    the matcher's corrected figure dropped it (finding **BAL-523**; the
    row-leg family read it through ``X-bi-3e``):

    * a CARD purchase marked by the flag, which leaves later through its own
      CC Payback sibling;
    * a purchase whose money moved through ANOTHER account than the row's
      (plan step ``credit_card:CC-5-2``, ruling **R-CC15**) -- a card swipe
      recorded in a checking envelope, which the card's statement shows and
      checking's never will;
    * a purchase on this account whose bank posting day is already recorded,
      whose cash left on its own day and is a movement of its own in the
      ledger (ruling **R-FM**, plan step ``balance:X-f3b``).

    **The three PARTITION the row's purchases** (:func:`_moves_elsewhere`):
    the flag, else the account, else the day -- so a posted card swipe is
    counted once, by the account arm, and the sum cannot double a purchase
    whatever combination a row holds.

    All three are read over the row's PURCHASES (ruling **R-BAL68**), so a
    settled row's covering movement is in none; and no row the statement
    matcher prices holds a purchase (ruling **R-BAL78** makes a stated figure
    beside purchases unrepresentable, and a row settling from its purchases
    is worth ``0`` to the offer under ruling **R-BAL81**), so for the
    matcher's corrected figure this is ``0.00`` by construction.  The
    reconcile panel, which offers a Projected envelope holding purchases, is
    the reader it is not zero for.

    Args:
        txn: The row, with ``entries`` loaded.

    Returns:
        Their sum, ``0.00`` for the ordinary row that carries none of the
        three.
    """
    return (
        credit_entry_sum(txn) + elsewhere_purchase_sum(txn)
        + posted_purchase_sum(txn)
    )


def cash_leg_of(txn, gross: Decimal) -> Decimal:
    """Return the signed cash a PROJECTED *txn* would move settling at *gross*.

    The one sign rule for a ROW -- *gross, signed by the transaction TYPE*
    (``+`` income, ``-`` expense) -- behind the same TOTAL contributing gate
    :func:`movement_cash_leg` applies to a movement: a non-contributing row is
    worth ``0.00`` whatever *gross* says.  Its one caller is the statement
    matcher (``statement_match._valuation.transaction_price``, plan step
    ``bank_import:X-f6a-2``),
    which must compare a bank line against what the app would move if the
    Projected row it names settled -- and which asks this ONLY of a row that
    does not settle from its purchases (``transaction_service.
    settles_from_entries``, the verb's own predicate): such a row's purchases
    are the candidates and the row is worth ``0`` to the offer (ruling
    **R-BAL81**).  A row that reaches here therefore holds no purchase, so
    the two off-statement terms :func:`off_statement_sum` states are ``0``
    for it by construction; this subtracted them anyway through ``X-bi-4a``'s
    first cut, a term no caller could observe, and was
    ``settled_cash_leg`` with its first term supplied until that function
    lost its last reader.

    Args:
        txn: The row being valued.
        gross: What the row would book.

    Returns:
        The signed cash effect, ``0.00`` for a non-contributing row.
    """
    if not is_balance_contributing(txn):
        return Decimal("0.00")
    return _signed_by_type(txn, gross)


def movement_cash_leg(txn: Transaction, entry) -> Decimal:
    """Return the signed cash ONE movement moves through its OWN account.

    The movement's twin of :func:`cash_leg_of` (plan step
    ``balance:X-bi-3b``, ruling **R-BAL35**): a purchase against an envelope
    or the covering movement a settle wrote moves its whole stored figure in
    its PARENT's direction -- ``+`` under an income row, ``-`` under an
    expense -- through the ONE sign rule the parent's own leg reads.  A
    purchase has no type of its own, exactly as it has no category of its
    own: both are the plan row's.  WHICH account it moves through is the
    movement's own ``account_id`` (ruling **R-BAL75**; the fold reads it
    there since ``balance:X-bi-4``, and a door writes one other than the
    row's since ``credit_card:CC-5-2``) -- this function answers how much
    and which way, and takes no position on where.

    **TOTAL over the two facts that make a movement move nothing here**, so
    a caller that forgets to pre-filter still reads the right figure:

    * a CARD purchase leaves through its own CC Payback sibling and never
      touches this account (:func:`credit_entry_sum` is the term that keeps
      it out of the parent's leg for the same reason);
    * a movement under a NON-CONTRIBUTING parent -- soft-deleted, Credit or
      Cancelled -- moves nothing, the family's zero :func:`cash_leg_of`
      states for the parent (ruling **R-FM**).

    It does NOT read ``settled_on``: whether a movement has POSTED is a
    question about the event stream and the ledger (``_events.
    settled_cash_facts``, ``_posting_purchases.purchase_posts``), while the
    plan holds an UNPOSTED one in flight at the same figure
    (``_events.in_flight_movements``) and the statement matcher prices it at
    what the bank would show for it.  The one figure all three want is this.

    Args:
        txn: The movement's parent, contributing or not: a plan row, or a
            :class:`~app.services.transfer_legs.TransferLeg` whose record the
            movement is (leaf ``X-bi-6-4a``), which answers ``is_income``,
            ``is_deleted`` and ``status`` off its transfer and side.
        entry: One of its ``budget.transaction_entries`` rows.

    Returns:
        The signed ``Decimal``: positive INTO the account, ``0.00`` for a card
        purchase or a movement under a non-contributing parent.  A stored
        REFUND (a negative purchase, ruling **bank_import:R-II**) passes
        through as money coming back, because this is arithmetic and not a
        case analysis.
    """
    if entry.is_credit or not is_balance_contributing(txn):
        return Decimal("0.00")
    return _signed_by_type(txn, _moved_figure(txn, entry))


def _moved_figure(txn, entry) -> Decimal:
    """Return the figure ONE movement moved: the one accessor every kind-blind reader asks.

    A row's movement -- a purchase, or the covering movement a row's settle
    wrote -- states its own ``amount``.  A transfer side's record does NOT
    own its figure: a transfer's money is one figure, stored once on the
    TRANSFER (plan step ``balance:X-bi-6-4d-2``, rulings **R-BAL365**,
    **R-BAL366**: :attr:`~app.models.transfer.Transfer.settled_figure`), and
    its two records say where and when it left and arrived.  A side record
    is the one movement with no parent row (``transaction_id IS NULL``,
    ``ck_transaction_entries_one_parent``), and its parent here is its
    :class:`~app.services.transfer_legs.TransferLeg`.  The branch is the
    single place the two kinds part, until the movement normalization arc
    ruling **R-BAL366** queued gives them one shape.

    Args:
        txn: The movement's parent, as :func:`movement_cash_leg` takes it.
        entry: The movement.

    Returns:
        The unsigned figure the movement moved.
    """
    if entry.transaction_id is None:
        return txn.transfer.settled_figure
    return entry.amount


def movement_figure_for(txn: Transaction, cash: Decimal) -> Decimal:
    """Return the figure a movement under *txn* must STORE to move *cash*.

    The inverse of :func:`movement_cash_leg`'s signed arm, for the two doors
    that write a movement's figure FROM a bank line's cash rather than read
    the cash from a figure: the statement matcher minting a purchase from a
    line (``_create._born_purchase``) and re-pricing one to a line
    (``_landing.corrected_figure``).  Both spelled ``-cash`` for themselves
    until plan step ``balance:X-bi-3b`` -- the expense arm of the rule, and
    a second spelling of it even while every purchase they could reach sat
    under an expense row.  The sign rule is an involution, so this IS
    :func:`_signed_by_type` and the inverse costs no second expression.

    Args:
        txn: The parent the movement records money for.
        cash: The signed cash the bank states, positive INTO the account.

    Returns:
        The stored figure: ``-cash`` under an expense (an outflow of
        ``-28.29`` is a purchase of ``28.29``, an inflow a REFUND of
        ``-28.29`` by the same expression, ruling **bank_import:R-II**),
        ``+cash`` under an income row.
    """
    return _signed_by_type(txn, cash)


def _signed_by_type(txn: Transaction, magnitude: Decimal) -> Decimal:
    """Return *magnitude* signed by *txn*'s TYPE: ``+`` income, ``-`` expense.

    The one expression of the direction rule, private so its three readers
    -- a row's leg (:func:`cash_leg_of`), a movement's
    (:func:`movement_cash_leg`) and the movement's figure from its cash
    (:func:`movement_figure_for`, the same involution read the other way)
    -- are the whole surface.
    """
    return magnitude if txn.is_income else -magnitude
