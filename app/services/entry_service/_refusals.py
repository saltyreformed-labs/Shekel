"""
Shekel Budget App -- Entry service: what a PURCHASE WRITE may not do.

**Split out of :mod:`._doors` at plan step X-au-j**, whose N-323 predicate took
that module past ``max-module-lines`` -- it sat at exactly 1000 of 1000, which
this project's own rule calls a cap reached rather than headroom.  The cut is by
what each function DECIDES rather than by size, the same seam
:mod:`app.services.status_seam._refusals` was cut on: *"they are gathered here
because they are one subject"*.

The subject here is what a purchase write may not do.  Nothing in this module
writes, flushes, or resolves a figure: each function reads the state a door is
about to change and RAISES, or returns.  A door composes them ahead of any
mutation, so a refused call leaves the session untouched.

:data:`_COST_BEARING_FIELDS` lives here because it is the same statement in
data: it names the fields that change what a row COST, which is exactly what a
settled parent refuses.

Boundary discipline (``CLAUDE.md`` Architecture): ORM rows and plain data in, a
raise or ``None`` out; no Flask import, no writes.
"""

from datetime import date
from decimal import Decimal

from app.enums import MovementFigureSourceEnum
from app.exceptions import ValidationError
from app.models.transaction import Transaction
from app.models.transaction_entry import TransactionEntry
from app.services import transfer_legs
from app.utils.dates import display_today
from app.utils.hidden_row import HiddenRow

#: The purchase facts that change what its PARENT ROW COST, named by what
#: actually reads them.
#:
#: **It was ``_UPDATABLE_FIELDS - {"settled_on"}`` until plan step
#: ``bank_import:X-f6a-3b``, and defining a set by what it EXCLUDES is how it
#: came to claim two fields that change no figure.**  A census of every reader
#: in ``app/`` settles each one:
#:
#: * ``amount`` -- :func:`app.services.row_valuation.purchases_total`,
#:   ``cash_ledger._entry_checking_impact``, ``posted_purchase_sum``,
#:   ``credit_entry_sum``.  COST-BEARING;
#: * ``on_card`` and ``card_account_id``, the CC tick and its pick -- which
#:   account the purchase's money moved on, so which balance it moves:
#:   ``cash_ledger._entry_checking_impact`` and the fold read the purchase's
#:   ``account_id``, which the tick MOVES (plan task plan#3142), and on an old
#:   flagged line the tick takes off the CC flag, which ``credit_entry_sum``
#:   and ``sync_entry_payback`` read.  COST-BEARING.  They replaced
#:   ``is_credit`` here at that task: the edit door wrote the flag until then,
#:   and writes no such field now;
#: * ``purchased_on`` -- ``Transaction.entries``' ordering, the out-of-period
#:   WARNING (:func:`._sums.entry_list_view`), the reconcile panel's sort and
#:   its two OFFER predicates
#:   (``reconcile_service._rows.lands_on_or_before``,
#:   ``_purchases._outstanding_scope``), the matcher's
#:   ``CandidateRow.purchased_on`` (its ``expected_on`` derives from it, and
#:   the Reconcile MATCH pane prints it as the *purchased* fact since plan
#:   step ``bank_import:X-gz``), three
#:   template fields.  **No valuation reads it.**  ``_outstanding_scope`` does
#:   admit a SETTLED parent, so this day gates which purchases the reconcile
#:   panel offers and a tick there writes ``settled_on`` -- admitted knowingly:
#:   ruling **R-FW** makes correcting the day a purchase was MADE the point, and
#:   it only ever moves the day EARLIER, which makes a row more offerable.
#:   Named by adversarial financial review 2026-08-19;
#: * ``description`` -- templates only.
#:
#: **What the miscount cost is measured.**  Ruling **R-FW**'s purchase-day
#: correction submits ``purchased_on`` beside ``settled_on`` in ONE call
#: (``statement_match._moving._apply_day``), so on the developer's own
#: statement 13 of the 15 corrections the review screen offered were refused by
#: a guard about what a row COST, for a field that changes no cost -- and the
#: 13 include the whole 2026-04-29 bookkeeping session ruling R-FW exists for.
#: The screen rendered an Accept button that could never succeed.
#:
#: ``settled_on`` was already outside the set and its reason is unchanged: it
#: is the day the BANK took this purchase, an OBSERVATION rather than a
#: restatement of what was spent.  Recording it moves that purchase's cash out
#: of its envelope's close and onto its own day (ruling **R-FM**; since plan
#: step ``balance:X-bi-4a`` an un-dated purchase is in flight and a dated one
#: a movement on its day, the row itself booking nothing), and the two
#: always sum to the same total.  That is the SAME split plan step X-au-c3 is
#: built on -- ``settled_on`` / ``reconciled_by_id`` are the ASSERTION and the
#: figure is WHAT MOVED (the covering movement's, since plan step
#: ``balance:X-bi-4b-1``; the row's own two columns through ``X-bi-4a``) --
#: read one level down, on the purchase instead of on the row.
_COST_BEARING_FIELDS = frozenset({"figure", "on_card", "card_account_id"})


def cost_fields_changing(valid_updates: dict) -> "frozenset[str]":
    """Return the fields :func:`_reject_settled_parent` must weigh for one call.

    **Ruling R-GE (2026-08-22): a bank statement's evidence justifies re-costing
    a settled purchase, and the EVIDENCE IS IN THE CALL.**
    :data:`_COST_BEARING_FIELDS` exists for a human's second thoughts -- a typed
    figure with nothing behind it -- and a statement is the opposite of one.  So
    ``figure`` leaves the refused set exactly when the figure submitted says
    the bank's line wrote it.

    **What bounds the permission is the figure's own stated SOURCE, not a flag
    a caller asserts**, and that is the whole design: ``observed`` means *the
    bank's line stated this figure*, and the statement matcher is its only
    writer -- the entry PATCH door states ``typed`` and no other door writes a
    figure here at all.  A caller holding an ``observed`` figure HAS the
    evidence, so the rule needs no second channel and no ordinary edit form
    can reach it.  Until plan step **X-bi-3e-1** the evidence was read off the
    DAY beside the figure (an ``observed`` settle day in the same call); the
    figure now states its writer itself (ruling **R-BAL69**), and the day is
    left to say what it is about, which is when the money moved.

    The CC tick is NOT released with it: which account a purchase's money
    moved on is not a figure a statement states.

    Args:
        valid_updates: The submission, already narrowed to updatable fields.

    Returns:
        The field names to weigh -- every changing field, less ``figure`` where
        the figure carries the bank's own statement.
    """
    changing = frozenset(valid_updates)
    evidence = valid_updates.get("figure")
    if getattr(evidence, "source", None) is MovementFigureSourceEnum.OBSERVED:
        return changing - {"figure"}
    return changing


def _reject_settled_parent(
    txn: Transaction, changing: "frozenset[str]",
) -> None:
    """Refuse an entry mutation that RE-COSTS a row whose money has MOVED.

    **Finding N-229's door half, widened to the settled BAND at plan step
    X-au-c3** (developer ruling, 2026-08-17).  A settled envelope's purchases
    are closed: the user has said this money moved, and re-pricing one or
    removing one would move it again -- so both refuse, on Paid and on
    Received, which since plan step X-am are the whole band.

    **ADDING one is a separate rule since plan step ``bank_import:X-f6a-3b``
    and REMOVING one since ``bank_import:X-f6f``**, in
    :func:`_reject_settled_addition` and :func:`_reject_settled_removal`.  The
    three are not the same question, and this one is now only about an EDIT:
    re-pricing a purchase, or flipping which side of the card it sat on, is a
    figure the user supplies with nothing behind it but their own second
    thoughts.  What the other two weigh is whether the write can move what the
    row's own close BOOKED, which is arithmetic rather than evidence, and
    keeping all three in one function meant an optimistic direction and two
    neutral ones shared a refusal.

    **The reason it is the BAND and not the archive is carry-forward, and it is
    the argument that decides this.**  ``carry_forward_service`` rolls an
    envelope's UNSPENT remainder (``estimated - Sigma(entries)``) into the next
    period's row and then settles the source at what was spent.  So the moment
    an envelope closes, its leftover has already moved on and is sitting in a
    LATER row.  A purchase recorded against the closed source afterwards would
    raise its cost while that later row still holds the rolled-forward money --
    the same dollars counted twice, in two periods, with nothing to reconcile
    them.  A forgotten purchase belongs in the period that now holds the money.

    **What a user does instead**: put the row back to Projected, record the
    purchase, and close it again.  That is the same act the refusal names, and
    it is honest about what happened -- the close was premature, so the settle
    day it stamped and the statement it was reconciled against were premature
    too.  Leaving the settled band releases the ASSERTION -- the settle day and
    the statement link, in ``status_seam.apply_status_change`` -- and KEEPS what
    the row recorded, which is correct rather than a cost: the purchases are
    what the figure is made of, so a re-close restates it from them on the day
    the money really moved.

    **The rejected alternative, and why**: letting a settled envelope re-derive
    its figure from a late purchase is what the deleted
    ``_update_actual_if_paid`` did (``actual_amount = Sigma(entries)`` on any
    settled row with entries).  It moves money in the OPTIMISTIC direction
    without a human act -- one ``$50`` purchase back-filled into a ``$500``
    close crashes the recorded cost to ``$50`` and hands ``$450`` of
    already-spent money back to the projection.  That is precisely the failure
    :func:`~app.services.status_seam.reject_future_settle_day` exists to
    prevent, and the reason ``TransactionEntry.settled_on`` deliberately bounds
    only from below: where the app must guess, it keeps the balance LOW.

    **It is FIELD-AWARE, and what it admits is every field that changes no
    figure** (developer ruling, 2026-08-17; the set corrected at plan step
    ``bank_import:X-f6a-3b``).  Everything above is an argument about what the
    row COST, so :data:`_COST_BEARING_FIELDS` is now named by what reads a
    figure rather than by subtracting the one field somebody remembered --
    which is what let it refuse ``purchased_on`` and ``description``, neither
    of which any money rule reads.

    ``settled_on`` is the field that argument was written for.  Recording it
    changes no total -- it moves that purchase's cash from IN FLIGHT onto its
    own day (ruling **R-BAL77**: the row books nothing, and an un-dated
    purchase is a reservation until it is dated), so the two always sum to the
    row's whole debit total.  Refusing it would leave already-spent money dated on the day the
    envelope happened to be closed with no door to correct it: measured on the
    2026-08-17 production dump, 28 closed envelopes hold 61 debit purchases
    with no posting day recorded, totalling ``$4,360.07``.

    That split is this step's own three-lifetime model read one level down.  A
    purchase's amount is WHAT MOVED and its posting day is an ASSERTION about
    when -- the same two facts a covering movement's ``amount`` and the
    parent's ``settled_on`` are for a bill, with the same answer: the
    assertion may be recorded, corrected and withdrawn long after the figure
    is final.

    ``Status.is_settled`` is the band, and since plan step **X-am** the band is
    exactly Paid and Received.  It carried a third member, the terminal
    ``Settled`` archive, which ``balance_predicates.is_archived`` named alone so
    that this rule could read the band while its siblings read that one status;
    both the status and that predicate are deleted, so the band is the only
    reading left and no caller has to choose between two.

    Args:
        txn: The parent transaction the entry belongs (or would belong) to.
            Its ``status`` relationship is read (``lazy="joined"``).
        changing: The purchase facts this act writes.  The UPDATE door is its
            only caller and passes the fields it was actually given, which is
            what lets a posting-day or purchase-day edit through where a
            re-price is refused.  The CREATE and DELETE doors do not call this
            at all: :func:`_reject_settled_addition` and
            :func:`_reject_settled_removal` are their rules, and the DELETE
            door passed :data:`_COST_BEARING_FIELDS` here until plan step
            ``bank_import:X-f6f`` -- which refused every removal, including the
            exact inverse of an addition the ADD rule admits.

    Raises:
        ValidationError: When *txn* is in a settled status and *changing*
            touches any cost-bearing field.
    """
    if txn.status is None or not txn.status.is_settled:
        return
    if not changing & _COST_BEARING_FIELDS:
        return
    raise ValidationError(
        f"{txn.name} has settled; its purchases are closed and "
        "cannot be removed or re-priced. Doing so would change what "
        "the row cost after its money moved -- and a carry-forward has "
        "already rolled its leftover into a later period, so the same dollars "
        "would be counted twice. Set the row back to Projected to change a "
        "purchase, then mark it paid again. Recording the day your bank took "
        "a purchase, or the day you made it, is still allowed: those say when "
        "this money moved, not how much of it did."
    )


def _reject_settled_addition(txn: Transaction) -> None:
    """Refuse a NEW purchase against a settled row that cannot record one.

    **The rule is about the row's FIGURE, not its status** (plan step
    ``bank_import:X-f6a-3b``, on measurement), and the figure is the row's
    ENTRIES (plan step ``balance:X-bi-4b-1``, ruling **R-BAL80**): a settled
    row records what moved either as its purchases or as ONE covering
    movement the seam wrote for it (``status_seam._covering``), and a new
    purchase means opposite things to the two:

    * a row holding NO covering movement records its cost as
      ``Sigma(entries)`` (:func:`app.services.row_valuation.settled_figure`),
      so a new purchase raises that cost by its own amount and is a movement
      of its own: a DATED one books its cash on its own day, an UN-DATED one
      is in flight in the projection (ruling **R-BAL77**, plan step
      ``balance:X-bi-4a``), and the row itself books nothing either way.
      Measured 2026-08-18, under the row-leg fold of the time: adding
      `$18.64` to the 2026-05-21 Groceries close shrank that day's anchor
      true-up by exactly `$18.64`.  A close of NOTHING is such a row too
      (ruling **R-BAL82**: a ``$0.00`` close holds no entry, since a movement
      of nothing is not one), so it ADMITS a later purchase and then records
      exactly that purchase, counted once -- where the column read this
      replaced refused it as "a fixed figure";
    * a row holding a covering movement carries its whole close on it, fixed
      before the purchase existed, so the purchase's movement would post
      BESIDE the movement that already carries the close -- money counted
      twice (ruling **R-BAL80**).  Measured through ``X-bi-3e``, when the
      row's own leg subtracted the purchase from a gross that never held it:
      adding `$367.62` to a `$163.95` close moved that leg to **`+203.67`**
      -- an EXPENSE row publishing an inflow -- while the true-up moved
      `$0.00`.  Both legs still net to `-163.95`, which is why the balance
      instrument is BLIND to it.

    **Nothing else refuses that addition**: no constraint refuses a purchase
    beside a covering movement
    (``uq_transaction_entries_one_settlement_record`` counts settlement
    records alone), and the ledger and the cash fold book each movement at
    its own figure (``ledger_lines.purchase_posts``,
    :func:`app.services.cash_ledger.movement_cash_leg`), so THIS function is
    the guarantee.  Finding **N-318** named a second exposure, the row's own
    leg turning that state into the inflow measured above; no reader has read
    that leg since plan step ``balance:X-bi-4a``, and plan step plan#2513
    closes the finding, re-measured through the worked-out read.
    Production holds no instance -- measured 2026-08-19, 137
    settled rows carry a stored-figure settlement (8 of them envelopes on the
    developer's checking account) and every one holds ZERO purchases; 0 rows
    hold a covering movement beside a purchase on the 2026-09-19 restore --
    and a row that HAS purchases always settles on them, because
    ``settles_from_entries`` is ``bool(txn.purchases)`` (the flag's half
    dropped under ruling **R-BAL78** at plan step ``balance:X-bi-4a``, which
    also refuses a stated figure over purchases at the seam, and the seam
    WITHDRAWS a kept movement when a ``purchases`` record lands) and
    ``carry_forward``'s direct call writes that record unconditionally.
    **That held one click at a time, and not under a race** (plan step
    ``credit_card:CC-5-4a-4``, ruling **R-CC99** (a), measured 2026-09-23): a
    purchase and Mark Paid racing on one empty $300.00 Groceries envelope
    ended Paid holding the $300.00 payment AND the $12.34 purchase in either
    order -- Mark Paid chose its figure from a read taken before the purchase
    committed, and this refusal read a ``status`` the locking read handed back
    as ``None`` after its wait.  Since plan step ``balance:X-bn`` (ruling
    **R-CC106**) both clicks' transactions take the owner's write lock before
    they read any of the owner's data (:mod:`app.db_transaction`), so they
    run one after the other and the second click either settles at the
    purchases or meets this sentence, naming the row (ruling **R-CC98**).
    Until that step each door locked the row itself before it read it.
    It read the row's ``settled_basis_id`` through ``X-bi-4a``; that column
    is the movement's stale cache, deleted at ``X-bi-4b-2``.

    **The purchase no longer has to state the day the BANK TOOK IT** (ruling
    **R-BAL77**, plan step ``balance:X-bi-4a``, lifting the developer's
    2026-08-19 arm).  That arm existed because the row's own leg booked an
    un-dated purchase **on the row's original settle day** -- measured,
    `-50.00` to `-80.00`, on a past day with no external evidence.  A plan row
    books nothing of its own now; an un-dated purchase under a closed
    envelope is a movement in flight, held by the projection from tomorrow
    and absent from the actual until the bank is seen to take it, exactly as
    one under an open envelope is.  So the add-purchase form, which has no
    posting-day field, reaches a closed envelope again, and the reconcile
    panel offers the purchase it adds.

    **It does NOT answer the carry-forward double count, and it does not have
    to** (finding **N-249**, owner ``balance:X-ax``).  Every carried-forward
    source settles on this very basis, so a purchase added afterwards leaves
    the target period holding a leftover computed from the old sum.  The
    developer RULED that remedy on 2026-08-12 -- reconcile the rollover --
    and explicitly REJECTED refusing late purchases on a rolled-forward
    source, which is the reason :func:`_reject_settled_parent`'s message still
    gives for a refusal this rule lifts.

    **It carried a THIRD arm until plan step X-am** (ruling **balance:R-HA**):
    the terminal ``Settled`` ARCHIVE was refused whatever its basis, because an
    archived row's purchases were history (finding **N-229**).  The status is
    deleted, so the arm has no subject and the rule reads as the two facts it
    was always about -- has this row settled, and does it record a fixed
    figure.  ``statement_match._candidates`` dropped its matching clause in the
    same commit, so the two doors still agree.

    **What a user does when this refuses**: put the row back to Projected,
    record the purchase, and close it again -- which restates the figure from
    the records, exactly as :func:`_reject_settled_parent` describes.

    Args:
        txn: The parent transaction the new entry would belong to.  Its
            ``status`` relationship is read (``lazy="joined"``) and then its
            ``entries``.

    Raises:
        ValidationError: When *txn* has settled and holds a covering movement.
    """
    if txn.status is None or not txn.status.is_settled:
        return
    if not txn.covering_movements:
        return
    raise ValidationError(
        f"{txn.name} has settled and records a fixed figure, so a "
        "new purchase cannot be added to it: the row's cost would not grow by "
        "the purchase, and the purchase's own cash would be counted beside a "
        "figure that already covers it. Set the row back to Projected, add "
        "the purchase, and mark it paid again -- that restates what it cost "
        "from the purchases themselves."
    )

def removal_refusal(txn: Transaction) -> "str | None":
    """Return why a purchase may not be REMOVED from *txn*, or ``None``.

    :func:`_reject_settled_addition`'s mirror, and it exists because the door
    it replaces had no mirror at all (plan step ``bank_import:X-f6f``, ruling
    **R-GG**).  ``delete_entry`` asked :func:`_reject_settled_parent` with
    every cost-bearing field at once, so a settled parent refused every
    removal -- including the exact inverse of an addition ruling **R-FX**
    already ADMITS on the same row.  A door that can only go one way is what
    finding **N-333** measures: 103 purchases a statement pass created in
    error, every one under a settled ``purchases``-basis parent, and no door
    in ``app/`` that removes one.

    **The rule is the ARITHMETIC, and it was measured before it was written.**
    For a ``purchases`` settlement the figure IS ``Sigma(entries)`` and every
    purchase is a movement of its own, so removing one moves exactly what
    that purchase moved and nothing else: a DATED one's leg, reversed on its
    own day, or an UN-DATED one's place in flight (ruling **R-BAL77**, plan
    step ``balance:X-bi-4a``); the row's own close books nothing before or
    after.  Measured under the row-leg fold of the time on an envelope closed
    at ``$120.00 + $57.96``, both posted: the row's leg read ``0.00`` before
    and ``0.00`` after, and the account's posted total moved ``822.04 ->
    880.00`` -- the removed purchase's own ``$57.96``, reversed on its own
    day, and nothing else.

    **An UNPOSTED DEBIT purchase was the case that stayed refused, and ruling
    R-BAL77 lifts it.**  Under the row-leg fold it sat in the figure and in no
    subtracted term, so the close booked it; on the same fixture with the
    ``$57.96`` left unposted the row's leg read ``-57.96`` before and ``0.00``
    after -- the envelope's recorded close shrinking on a past day with no
    external evidence, which is what :func:`_reject_settled_parent` was
    written about.  A plan row books nothing of its own now: an un-dated
    purchase under a closed envelope is held in the projection from tomorrow,
    and removing it removes a movement in flight, which is the ordinary
    direction of every other removal here.

    **A settled row holding a COVERING MOVEMENT is refused whatever the
    purchase is**, for :func:`_reject_settled_addition`'s own reason: that
    movement carries a figure fixed before this purchase was weighed, so the
    purchase's own movement would be counted beside it (ruling **R-BAL80**).
    The row's ``settled_basis_id`` said the same through ``X-bi-4a``; the
    predicate reads the movement since plan step ``balance:X-bi-4b-1``, and
    a close of nothing -- which holds no movement (ruling **R-BAL82**) --
    admits the removal exactly as it admits the addition.

    **It was TWO sentences until plan step X-am** (ruling **balance:R-HA**).
    The terminal ``Settled`` ARCHIVE got one of its own, because that message's
    remedy -- *set the row back to Projected* -- was a transition the state
    machine refused for it, and a refusal naming a repair the app cannot
    perform is finding **N-302**'s shape.  Deleting the status is what made the
    two sentences one: no state a row can ENTER is absorbing any more, so
    *revert it and correct it* is a repair every settled row can actually take,
    and the remaining message's remedy is true for every row that reaches it.

    **This is the half of ruling `bank_import:R-GG` (d) that X-am makes
    VACUOUS, and the other half stands.**  That ruling (2026-08-24) says *the
    archive and a stored-figure settlement stay refused for R-FX's own
    reasons*.  The stored-figure refusal is the one below and is untouched.
    The archive refusal is not RELAXED -- there is no longer a state for it to
    refuse -- which is the same shape ``pay_period_locks`` records for the two
    lock reasons that left its set by becoming unreachable rather than by being
    argued down.  R-HA says so explicitly so no later reader has to infer it.

    **It does NOT answer the carry-forward staleness** (finding **N-249**,
    owner ``balance:X-ax``), and it WIDENS the door that reaches it -- which is
    exactly what plan step ``bank_import:X-f6a-3b`` did to the same row from
    the other side, and the row records that widening rather than changing its
    remedy.  **The direction is the OPTIMISTIC one and a first version of this
    paragraph had it backwards**, which matters because the inverted claim was
    being offered as the safety argument.  A rollover writes ``max(0, budget -
    Sigma(entries))`` into the NEXT period's row as an EXPENSE
    (``carry_forward_service._execute._settle_source_and_roll_leftover``).
    Removing a purchase RAISES the true leftover, so the already-written target
    is too SMALL -- less spending projected, and the balance reads HIGH.
    Measured on a `$500.00` envelope carried forward at `$442.04` after one
    `$57.96` purchase: removing that purchase leaves `$0.00` recorded in the
    source period and `$442.04` in the target, against `$500.00` budgeted, so
    `$57.96` of budget has left the projection.  The ADDITION direction is the
    conservative one, which is why N-249 tolerated it.
    **What the developer already ruled stands**: RECONCILE the rollover, and
    *refusing late purchases on a rolled-forward source* is rejected, because
    it also refuses a genuinely forgotten one -- and here it would refuse
    exactly the errant rows this step exists to remove.  Found by adversarial
    financial review 2026-08-24.

    **It ANSWERS where its sibling refusals RAISE, and that shape is what a
    second reader needs** (plan step ``bank_import:X-f6f``).  The statement
    matcher's undo removes purchases it created, and it must know BEFORE it
    writes anything whether each removal will be admitted -- the package's own
    promise is that a refused act leaves the database exactly as it was without
    depending on the rollback, and a door that discovered the refusal halfway
    through would break it and would let the review screen promise a removal
    the button then refuses.  Measured on that build: the panel offered *"Undo
    removes 1 row"* over an ARCHIVED container and the release raised with the
    act already deleted from the session -- so the shape was found on the very
    status plan step X-am has since deleted, and the answering rule it forced
    is what still protects the STORED-figure case.  The rule ANSWERS, one
    place, and :func:`_reject_settled_removal` is the arm that raises it.

    Args:
        txn: The parent transaction the entry belongs to.  Its ``status``
            relationship is read (``lazy="joined"``) and then its ``entries``.

    Returns:
        The sentence explaining the refusal, or ``None`` where the removal is
        admitted.
    """
    if txn.status is None or not txn.status.is_settled:
        return None
    # The ARCHIVE branch that stood here is deleted with its status (plan step
    # X-am); the docstring carries why.  What is left is the STORED-figure
    # refusal, whose *set the row back to Projected* remedy is now reachable
    # from every status a row can hold when it gets here.
    if txn.covering_movements:
        return (
            f"{txn.name} has settled and records a fixed figure, so "
            "a purchase cannot be removed from it: the row's cost would not "
            "fall by the purchase, and the figure it records would go on "
            "counting cash the purchase no longer explains. Set the "
            "row back to Projected, remove the purchase, and mark it paid "
            "again -- that restates what it cost from the purchases "
            "themselves."
        )
    return None


def _reject_settled_removal(txn: Transaction) -> None:
    """Raise :func:`removal_refusal`'s answer, for the door that writes.

    The two are split so one rule can serve a caller that must ASK before it
    writes and a caller that simply refuses; see :func:`removal_refusal` for
    the rule and for what the split is worth.  Both took the purchase being
    removed until ruling **R-BAL77** lifted the one arm that read it.

    Args:
        txn: The parent transaction the entry belongs to.

    Raises:
        ValidationError: When :func:`removal_refusal` names a reason.
    """
    refusal = removal_refusal(txn)
    if refusal is not None:
        raise ValidationError(refusal)


def deleted_row_purchase_refusal(gone: HiddenRow) -> str:
    """Return the sentence a purchase on a hidden row is refused with.

    **One sentence for the two places that refuse it**: the purchase door
    (:func:`app.services.entry_service.create_entry`, ruling **R-CC89**'s
    third layer), and the add-purchase route, which shows it where the
    purchase list stood for a row that is gone (rulings **R-CC101**,
    **R-CC103**, **R-CC104**).  The status seam's
    ``deleted_row_payment_refusal`` is its twin for a payment.  Named, never
    numbered (ruling **R-CC98**), in the words ruling **R-CC96** quotes:
    *"Groceries was deleted: a purchase cannot be recorded under it"* -- or
    "was archived" where the row's recurring item is (ruling **R-CC107**).

    It takes a :class:`~app.utils.hidden_row.HiddenRow` rather than the row
    because a one-off row's delete removes the row from the table: the route
    read the name while the row was live, and after the delete there is no row
    left to hand over.  Whoever builds the value asks the one read it needs;
    this module reads nothing.

    Args:
        gone: The hidden row's name, and whether its recurring item is
            archived.

    Returns:
        The refusal, naming the row.
    """
    return (
        f"{gone.name} {gone.went}: a purchase cannot be recorded under it.  "
        "Reload the page."
    )


def _reject_zero_amount(amount: Decimal | None) -> None:
    """Refuse a purchase worth nothing -- ruling **bank_import:R-II**.

    **The table's own invariant, stated where every caller meets it.**
    ``ck_transaction_entries_positive_amount`` is ``amount <> 0`` since
    migration ``b8e4c1f7a903``, and a constraint is a backstop rather than a
    door: a zero reaching the database arrives as an ``IntegrityError``, which
    the route layer renders as *Something went wrong* over a traceback for what
    is an ordinary bad input.  Both write doors ask this instead, so the
    refusal is a ``ValidationError`` naming the field.

    **It is the NON-ZERO rule and deliberately not the positive one.**  R-II
    splits the two by tier and the split is the ruling's point: *a purchase
    worth nothing is not a purchase* is a fact about the row, so it belongs
    here, on the path the bank-import door takes as well as the form; *a typed
    negative is a typo* is a fact about a hand-entry FORM composing a new
    purchase, so it stays on that form (``EntryCreateSchema``, the
    add-purchase input's ``min``).  Stating positivity here would refuse the
    refund this whole plan step exists to record.

    Args:
        amount: The submitted amount, or ``None`` where the caller is not
            setting one -- a partial update that leaves ``amount`` alone must
            not be refused for a value it is not writing, which is the same
            narrowing :func:`_reject_future_purchase_date` gets at its update
            call site.

    Raises:
        ValidationError: When *amount* is exactly zero.
    """
    if amount is not None and amount == 0:
        raise ValidationError(
            "A purchase amount cannot be zero. Enter what the purchase cost, "
            "or what a refund returned as a negative amount."
        )


def _reject_future_purchase_date(purchased_on: date) -> None:
    """Refuse a purchase dated after the user's today (ruling R-M).

    The ONE statement of "a purchase entry records a purchase that HAPPENED",
    shared by both write doors (:func:`create_entry` and :func:`update_entry`)
    so the boundary cannot hold on one and not the other -- the same
    both-doors-one-derivation shape ruling R-C's origination guard uses.

    **Why the source and not the reader** (ruling R-M, whose work shipped at
    plan step S1-c, so it is recorded in
    ``docs/audits/balance_architecture/archive/phase_x_as_built_2026-08-04.md``
    Section 3 rather than in the live README).  A future
    purchase is not merely odd data: it moves a rendered balance.  The
    projection holds back
    ``max(estimated - settled_debit - credit, outstanding_debit)`` for a
    still-projected envelope, so an entry dated ahead changes today's balance in
    EITHER direction depending only on its credit flag -- measured on the live
    Groceries envelope (``$780.00`` budgeted, ``$60.55`` held back): a
    ``$150.00`` future debit takes the reservation to ``$150.00`` through the
    ``max()`` floor (``-$89.45`` on the balance), while the same amount ticked
    CC takes it to ``$0.00`` (``+$60.55``).  Refusing it here is what lets the
    reservation's ``as_of`` window -- the parameter the calendar passed and the
    grid did not, which was the divergence itself -- stay DELETED at plan step
    X-c2 rather than ruled.

    **This guard survived the 2026-08-01 re-ruling of R-M, and it survived by
    moving onto the column it was always about.**  R-M and ruling R-DH (e) had
    been defining ONE column two ways -- "the day the purchase happened, never
    in the future" and "the day the money hit the account, one to two days
    later for a debit card".  The column split rather than the guard bending:
    ``purchased_on`` keeps this boundary intact and ``settled_on`` carries the
    posting day, so the forward case the developer needs to express has its own
    field and this one no longer has to admit a forecast.  Widening THIS bound
    was rejected: the column would then mean "purchase day" on some rows and
    "posting day" on others, with nothing in the schema recording which, and
    the remaining-budget figure and the out-of-period warning both read it as
    the purchase day.

    Backdating stays fully allowed, and is used: a purchase logged days after it
    happened, or one dated into the previous pay period, is ordinary (the real
    2026-05-21 Groceries row carries entries from 05-18).  A purchase you have
    not made yet is the envelope's remaining BUDGET, which the row already
    models.

    The comparison is against :func:`~app.utils.dates.display_today` -- the
    user's wall-clock date, not the server's UTC one -- because
    ``purchased_on`` is a civil date the user types on their own clock.
    Judging it in UTC would refuse a legitimate same-day entry for the hours
    the two frames disagree.

    Args:
        purchased_on: The civil date the caller wants the entry to carry.

    Raises:
        ValidationError: When *purchased_on* is after the user's today.  The
            message carries both dates so the surface can show what was
            rejected and what the boundary was.
    """
    today = display_today()
    if purchased_on > today:
        raise ValidationError(
            f"A purchase entry records a purchase that has already happened, "
            f"so its date cannot be in the future: {purchased_on.isoformat()} "
            f"is after today ({today.isoformat()}).  Log the purchase when it "
            f"happens; money you have not spent yet is already held back by "
            f"this row's remaining budget.  If the purchase is made but has "
            f"not reached your bank yet, that is what the posting date is for."
        )


def _reject_future_posting_day(settled_on: "date | None") -> None:
    """Refuse a bank posting day after the user's today -- ruling **R-FM**.

    The purchase twin of
    :func:`app.services.status_seam.reject_future_settle_day`, and it arrived at
    plan step X-f3b because that step INVERTED the reason this column had no
    upper bound: a forward day was conservative while a purchase was not a cash
    movement, and now RELEASES the reservation today while booking the cash
    later.  The argument is stated once, at the rule it is about
    (``cash_ledger._amounts._entry_aware_amount``), rather than a second time
    here.  A purchase the bank has not taken yet leaves ``settled_on`` NULL,
    which is what that state has always meant, so nothing expressible is lost;
    measured before the bound was added, ZERO of 91 production purchases carried
    a forward day.

    The clock is the USER's (:func:`~app.utils.dates.display_today`), for the
    reason :func:`_reject_future_purchase_date` beside it states.

    Args:
        settled_on: The posting day the caller wants the entry to carry, or
            ``None`` to clear it (always allowed -- it is the outstanding
            state).

    Raises:
        ValidationError: When *settled_on* is after the user's today.
    """
    if settled_on is None:
        return
    today = display_today()
    if settled_on > today:
        raise ValidationError(
            f"A posting date records the day your bank TOOK the money, so it "
            f"cannot be in the future: {settled_on.isoformat()} is after today "
            f"({today.isoformat()}).  Leave the posting date empty until you "
            f"see the purchase on a statement -- an unposted purchase already "
            f"holds its whole budget back."
        )


def _reject_settled_before_purchase(
    purchased_on: date, settled_on: date | None,
) -> None:
    """Refuse a posting day earlier than the purchase it belongs to.

    Money cannot leave the account before it was spent.  The database carries
    the same rule as ``ck_transaction_entries_settled_not_before_purchase`` and
    that constraint is the backstop; this is the door, so the user gets a
    message naming both dates instead of a 500 from an ``IntegrityError``.

    It is checked against the RESULTING pair rather than the submitted one,
    because either side can move: editing a purchase's date backwards past a
    posting day already recorded breaks the invariant just as surely as
    entering an early posting day does.

    The UPPER bound is :func:`_reject_future_posting_day`'s and it arrived at
    plan step X-f3b (ruling **R-FM**), which inverted the reason there was
    none: a recorded posting day is now the moment the money leaves the book,
    so a forward one takes already-spent money out of today's projection instead
    of holding it conservatively.  The two bounds are separate functions because
    they are separate rules -- this one is about a PAIR of the row's own dates
    and that one is about the clock -- and both are checked on the RESULT of an
    update rather than on its submission.

    Args:
        purchased_on: The day the purchase was made, after any pending update.
        settled_on: The day the bank took it, after any pending update, or
            ``None`` when it has not been observed.

    Raises:
        ValidationError: When *settled_on* precedes *purchased_on*.
    """
    if settled_on is not None and settled_on < purchased_on:
        raise ValidationError(
            f"A purchase cannot reach your bank before you make it: "
            f"{settled_on.isoformat()} is earlier than the purchase date "
            f"({purchased_on.isoformat()}).  Correct whichever of the two is "
            f"wrong."
        )


def _reject_flag_beside_another_account(
    is_credit: bool, account_id: int, row_account_id: int,
) -> None:
    """Refuse the CC tick, or the old ``CC`` flag, beside an account other than the row's.

    Plan step ``credit_card:CC-5-2``, ruling **R-CC15**: a card swipe in a
    checking envelope is a movement ON the card, named by the purchase's own
    ``account_id``.  The ``is_credit`` flag is the CHEAT's spelling of the
    same fact -- *this purchase's money is not on this row's account* -- and
    it survives to ``CC-7`` only because the legacy lines that carry it have
    no account of their own to say it with.  A purchase carrying BOTH would
    be one fact in two homes (CLAUDE.md rule 14), and the flag's arm in
    ``cash_ledger.movement_cash_leg`` would zero a movement the card's fold
    must read.  Refused at the add door, where the CC tick and "Paid from"
    are both drawn and would each say where the money went (plan task
    plan#2930); the edit door draws no "Paid from", its tick MOVES the
    purchase, and no door writes the flag on (plan task plan#3142), so the
    combination is unwritable rather than reconciled.
    ``cash_ledger.off_statement_sum`` still partitions its terms by both
    facts, so the sum would hold even for a row that reached the table
    around the doors.

    Args:
        is_credit: The flag the write would leave on the purchase.
        account_id: The account the write would leave the purchase on.
        row_account_id: Its parent row's account.

    Raises:
        ValidationError: When *is_credit* is set and the two accounts differ.
    """
    if is_credit and account_id != row_account_id:
        raise ValidationError(
            "A purchase on another account than its row's already says how it "
            "was paid, so the CC box does not apply to it: pick the card as "
            "the purchase's account, or tick CC and leave the account as the "
            "row's, not both."
        )


def _reject_settlement_record(entry: TransactionEntry) -> None:
    """Refuse an entry-door write to a row's own SETTLEMENT RECORD.

    Plan step **X-bi-3a**, ruling **R-BAL39**: a bill (or an envelope closed
    empty) settling on the MANUAL branch has its figure mirrored as ONE
    covering movement, written and kept in step by the status seam alone --
    the settle re-prices it, a revert un-dates it and keeps it (plan step
    **X-bi-3e-2**, ruling **R-BAL61**), a day correction on the row re-dates
    it, and only a ``$0.00`` or a ``purchases`` record withdraws it.  Since
    that step no purchase list renders it (every purchase-meaning reader
    asks ``Transaction.purchases``, ruling **R-BAL68**), so a door reaches it
    by a crafted id alone; an edit or delete would still move the row's
    money out from under its own record: the day the money moved is the
    ROW's to state (its settle day), and the figure is the settle's.
    Refused by name, with the door that owns the act.

    **The name is the movement's PARENT's, asked of**
    :func:`app.services.transfer_legs.movement_parent` **since plan step
    ``balance:X-bi-6-4c-4``** (ruling **R-BAL160**): a plan row's own name,
    or for a transfer's payment its LEG's label -- the one the grid shows,
    composed from the endpoints' current names -- where it read the shadow
    row's stored copy of that label.  Byte-identical wherever no account was
    renamed since its transfer was written (0 of 40 transfer payments differ
    on the 2026-09-30 00:11 production dump), and the one read of a
    movement's parent ``X-bi-6-4d`` re-points when a transfer's payment has
    no row at all.

    **The advice is the PARENT's too since leaf ``X-bi-6-4d-1``**: a
    transfer's payment is corrected on the TRANSFER -- its "Money moved on"
    box for that side's account and its Actual, the labels its popover shows
    while the transfer is marked paid -- and a transfer has no purchases to
    be closed from, so the row's advice ("edit the row's settle day ...
    closing the row from its own purchases") named acts no transfer has.  It
    says only where the record is changed, not every way it can leave the
    books (a hard delete of the transfer also takes it).  Both sentences
    start with the parent's name, so a reader matching "This is the payment
    record of <name>," reads either.

    Args:
        entry: The purchase the door was asked to write.

    Raises:
        ValidationError: When *entry* is its parent's settlement record.
    """
    if not entry.covers_settlement:
        return
    parent = transfer_legs.movement_parent(entry)
    if isinstance(parent, transfer_legs.TransferLeg):
        raise ValidationError(
            f"This is the payment record of {parent.name}, written when "
            "that transfer was marked paid. It is not a purchase: change it "
            "on the transfer itself, through its \"Money moved on\" day "
            f"for {parent.account.name} and its Actual, which the transfer "
            "shows while it is marked paid."
        )
    raise ValidationError(
        f"This is the payment record of {parent.name}, "
        "written when that row was marked paid. "
        "It is not a purchase: to change the day its money moved, edit "
        "the row's settle day; to change the figure, correct the row's "
        "actual or revert it and mark it paid again. It is withdrawn "
        "only by a $0.00 actual, or by closing the row from its own "
        "purchases."
    )
