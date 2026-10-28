"""
Shekel Budget App -- A transfer's two LEGS, derived from its parent.

The PLAN half of ruling **R-BAL13** (plan step **X-bi-6a**), and since leaf
``X-bi-6-1`` (ruling **R-BAL87**) the GRID's view of a transfer as well: a
transfer's two legs are not rows in ``budget.transactions``.  A still-projected transfer is
ONE economic event, and what each account it touches will see is a
PROJECTION of the parent row -- the from-side an expense, the to-side an
income, each worth exactly what the parent resolves to.  This module states
that projection ONCE, as a value (:class:`TransferLeg`) and the loader
that produces it (:func:`planned_transfer_legs`), so every reader of an
account's plan derives the same pair from the same parent.

**What it replaces.**  Until this step every balance reader loaded the two
SHADOW rows the transfer service wrote beside each parent -- an expense
``Transaction`` on the from-account and an income ``Transaction`` on the
to-account -- and priced each through amount rule 5 (*a shadow is worth its
parent*).  From this step no reader FOLDING a projection read one; the
shadows stayed, maintained by the transfer service for the settle doors and
the screens that rendered them, until plan step ``balance:X-bi-6-4d-2``
stopped keeping them in step, and plan step ``balance:X-bi-6-4d-2`` deleted
them, making a transfer one row (ruling **R-BAL258**).  The settled half
is each side's covering MOVEMENT since plan step ``balance:X-bi-4a`` (filed
under the side's shadow until plan step ``balance:X-bi-6-4d-2``, under the
transfer by a side link since), so the maintenance contract that kept a
shadow equal to its parent (Transfer Invariant 3) has no shadow left to
keep in step.

**A leg is planned exactly while its transfer is Projected** (plan step
``balance:X-bi-6-5``'s third leaf).  The settled half reads a leg as the
dated covering movement filed under the transfer's side on that account,
and from plan step ``balance:X-bi-4a`` until that leaf the MOVEMENT decided
here too, per side (ruling **R-BAL79**; ledger row **BAL-500**): a
transfer whose parent was still Projected while one side's movement had
been dated was emitted for the OTHER side alone, so no leg was counted by
both halves.  Through ``X-bi-3e`` both legs were emitted off the parent's
status and that state read ``-$500.00`` on a `$250.00` transfer.  The
status-band rule refuses that state at commit since plan step
``balance:X-bi-6-4d-2`` (below) -- a record is dated exactly while its
transfer is Paid or Received -- so the status and the movement give one
answer, and the plan half reads the status alone.

**A leg is DERIVED and carries its parent, deliberately.**  It stores no
figure, no period and no date of its own: :attr:`~TransferLeg.due_date`
and :attr:`~TransferLeg.pay_period_id` read the parent's columns, and
what the leg is WORTH is
:func:`app.services.cash_ledger.resolve_transfer_amount` over the parent --
the ONE producer ruling **R-BAL10** put a transfer's amount on.  A leg that
held a copy of any of those would be a shadow row in memory, the very shape
this step exists to stop reading.

**The same value is what the GRID draws in a transfer's cell** (leaf
``X-bi-6-1``, ruling **R-BAL87**): a leg's identity on a grid is the pair
``(transfer id, the account it is on)`` -- :attr:`~TransferLeg.cell_key` --
and its cell's doors are the transfer's own routes.  A leg answers one more
thing: its RECORD, the covering movement the status seam wrote when the
transfer settled (:attr:`~TransferLeg.record`), read off the transfer's list
for that side since ruling **plan#1984** -- a loader whose readers read
records pre-loads them in one statement (:func:`preload_side_records`; both
sides of one transfer, :func:`transfer_sides`), and one whose readers read
none loads none: the grid's draws legs and reads no record, nor does the
fold's plan half (ruling **plan#2136**).
**R-BAL87's text says "its dated covering movement"; the record here is
that movement dated OR kept un-dated across a revert** (ruling **R-BAL61**),
one word wider than the ruling's, because a side's list holds its record
whatever its day.  Since plan step
``balance:X-bi-6-4d-2`` no reader of this load uses an un-dated record: it
gives the popover's day box no day, as no record does, and the withdrawal
caption (``routes/transfers/forms._payment_withdrawal``), with the lines it
names, renders only for a settled transfer
(``transfers/_transfer_full_edit.html``'s ``zero_caption``), whose records
are dated.  A reverted leg's
"marking paid records $X" caption reads its TRANSFER's kept figure
(``transaction_service.retained_amounts_by_key``), never the record,
which holds no figure; a settled leg's record is dated, so the ruling's
case is unchanged.  That movement hangs off
the transfer by its side link since plan step ``balance:X-bi-6-4d-2`` (off
the transfer's shadow row on that account until then), and
:func:`preload_side_records` reaches it through ONE join -- the join
:func:`planned_transfer_legs` used to decide which relation a leg is in,
until plan step ``balance:X-bi-6-5``'s third leaf left it the status alone
-- which that step moved once.

**One fold PREDICATE did change at 6-1, and it was pinned rather than
denied**: sharing the join gave :func:`planned_transfer_legs`' ``dated_leg``
test (deleted at plan step ``balance:X-bi-6-5``'s third leaf) the term
``the shadow is live`` (``Transaction.is_deleted IS FALSE``) that it did
not carry before -- the term the settled half's
``balance_contributing_clause`` applied to the same movement.  On every
door-written state the two predicates agreed (no door soft-deleted one shadow
alone); on the double drift -- a dated movement under a shadow deleted around
the service, the parent still Projected -- the leg had vanished from both
halves, and from 6-1 it was counted once, by the plan (R-JA: the parent
decides).  ``tests/test_services/test_transfer_legs/test_the_plan_half.py``'s
drift class pinned it, red under the old predicate, until plan step
``balance:X-bi-6-4d-2`` moved the join onto the side links.  That step
deleted the term (:func:`_records._side_records` says why no liveness test is
left) and refused at commit the hide of a shadow holding a movement (the
deleted-row rule's row arm, ruling **R-BAL167**'s class 2), so from then its
case pinned the storable neighbour, a movement under a LIVE shadow, until plan
step ``balance:X-bi-6-4d-2`` made a transfer one row (ruling **R-BAL258**) and
that case went with the shadows (ruling **R-BAL313**).  **The double drift is
structural now**: no shadow can be stored to hide
(``tests/test_models/test_a_transfer_is_one_row/test_the_upgrade.py``'s
``TestTheUpgrade::test_the_database_refuses_a_twin``), and a DATED record
under a still-Projected transfer is refused at commit by the status-band rule
(``tests/test_models/test_a_transfer_side_s_record_follows_its_status.py``'s
``test_a_dated_record_under_a_projected_transfer_is_refused``).

**A leg's LABEL is composed here** (:func:`leg_label`), from the endpoints'
CURRENT names: "Transfer to <to-account>" on the from-side, "Transfer from
<from-account>" on the to-side.  It is the one composition the grid's row
label reads, and the shadow rows' name writer
(``transfer_service._create.shadow_names``) read it too until plan step
``balance:X-bi-6-4d-2`` deleted the shadows and its leaf 3b that writer, so a
renamed account re-labels every leg it touches where a shadow's stored
``name`` went stale -- one of the two visible changes 6-1 makes.  The other:
a leg's ``notes`` are its PARENT's (a shadow was written with none and no
writer mirrored them), so a transfer's notes show on its grid cell's title
where the shadow's showed nothing.

**Why a leaf module, below both readers.**  The cash ledger needs every leg an
account is on (both sides, for its cash fold); the loan loaders need the legs
INTO a loan (its projected payments); and ``cash_ledger`` imports
``loan_loaders`` for its loan term primitives, so the loader cannot live in
the cash ledger without closing that cycle -- ``cyclic-import`` traces a
call-time import too.  A leaf both tiers reach is the shape
:mod:`app.services.row_valuation` and :mod:`app.utils.amount_relationships`
already take, for the same reason.  It names models, the shared status
predicates, the reference cache and the date arithmetic, and no service.

**The SETTLED half reads its legs here too since leaf ``X-bi-6-4a``** (ruling
**R-BAL106**): :func:`transfer_movement_rows` / :func:`recorded_transfer_legs`
hand the cash fold, the ledger oracle and the savings metric each paid
transfer's covering movement with its transfer and side, and since that
leaf's second half the posting WRITER books every transfer movement under its
LEG (:func:`movement_parent`, :func:`transfer_family_movements`).  For
THOSE readers and the writer this
module is the one place a transfer's movement is reached (through a shadow
row until plan step ``balance:X-bi-6-4d-2``, by its side link since), and so
it is since leaf ``X-bi-6-4c-2`` for the reconcile panel
(:func:`offerable_transfer_legs`) and the recurrence engine's records
predicate (:func:`transfers_holding_records`), and since leaf
``X-bi-6-4a-3`` (rulings **R-BAL125**, **R-BAL157**) for every door that asks
whether a transfer holds a payment or purchase -- the pay-period lock, the
reset gate, "Remove earlier paychecks" and the three archive or delete doors
(:func:`transfer_holds_a_movement`, :func:`held_transfer_entries`), and
since leaf ``X-bi-6-4c-1`` (rulings **R-BAL158**, **R-BAL159**) for
statement match, which offers a still-planned transfer as its LEG
(:func:`offerable_transfer_legs`), a paid one's movement as its leg's record
(:func:`transfer_movement_rows` / :func:`recorded_transfer_legs`) and values
an accepted member through :func:`movement_parent` (loaded by
:func:`movement_parent_loads`) -- and since leaf ``X-bi-6-4c-4`` (ruling
**R-BAL160**) for the purchase doors' payment-record refusal and the match
withdrawal's event, which name a movement's parent through
:func:`movement_parent`, and for the one removal act, which takes a movement
out of the list :func:`parent_entries` answers, and since leaf
``X-bi-6-4c-3`` (ruling **R-BAL142**, ledger row **BAL-568**) for "Remove
earlier paychecks"' dated-money arm and the transfer popover's two day boxes,
which read each side's own day off its movement
(:func:`transfer_movement_rows`, and the popover's sides' records through
:func:`preload_side_records` since ruling **plan#1984**), and
since leaf ``X-bi-6-4d-1`` for the transfer service's own reads of a side's
leg (:func:`transfer_side_leg`, keyed by side) -- the expense leg the settle
and the update read the transfer's figures through (the retained correction,
the echo comparison), each off the leg's TRANSFER and never its record since
plan step ``balance:X-bi-6-4d-2``, and since that step the side's record
itself for the seam pass, an endpoint move and the statement link -- for the
transfer's hard delete, which hands
the removal act every entry its transfer holds
(:func:`held_transfer_entries`), and for the loan posting probe
(``loan_posting_service._sync``, ledger row **BAL-579**), which loads the
movements it resolves through :func:`movement_parent_loads` where it spelled
that chain inline.  The loan walk's refusal of an un-dated settle
(``loan_ledger._visible.payment_visible_on``) stopped reaching a movement
through a shadow at that leaf too: it named the shadow's ``transaction_id``
and then the transfer, until plan step ``balance:X-bi-6-5``'s third leaf
deleted it, the status-band rule refusing a settled side's un-dated record
at commit.
**The last readers that reached a transfer's movement themselves moved at
plan step ``balance:X-bi-6-4d-2``**, with the join: the integrity sweep's one
raw-SQL leg join (``scripts/_integrity_core.TRANSFER_LEG_RECORDS_JOIN``,
which BA-06's leg arm reads, and DC-11's did until plan step
``balance:X-bi-6-4d-2``) spells :func:`_records._movement_link`
again over the side links, since raw SQL cannot call it; statement match's
``_acts.named_rows`` names a payment's parent by :func:`cell_key` over
:func:`movement_parent` (a transfer side's record its LEG) where it read the
record's ``transaction_id`` as a row id, and ``_candidates._claimed_rows``
asks for a row parent, a side's record naming none.  (A third reader of that
id, the accept door's refusal of an envelope named beside its own purchase,
was deleted at ``credit_card:CC-5-4a-5``'s leaf 5c-2c-1, finding
**CC-386**.)

**A database VIEW for this pair was refuted at the ruling**: a derive-mode loan
payment's leg cannot be priced without the amortization engine, so the pair
is Python.

**A PACKAGE since the X-bi-6-4 lineage brought the module to 970 of pylint's
1000 lines** (finding **N-152**'s answer to the same ceiling: a package, never
prose shaved off a measured claim), cut along the seam the module already had
and moved without a byte changed in any definition.  :mod:`._leg` is the value,
built from its parent -- :class:`TransferLeg`, its label
(:func:`leg_label`), its identity (:func:`cell_key`, :func:`key_order`,
:func:`is_leg_key`), its parent (:func:`parent_of`, the one answer to which
row prices it, ruling **plan#1983**), its construction
(:func:`transfer_side_leg`, :func:`leg_of`, :func:`side_legs`) and, since
ruling **plan#1984**,
which of the transfer's lists holds a side's record
(:func:`side_movements`), the list its :attr:`~TransferLeg.record` reads.
:mod:`._records` holds the package's code that knows where a movement hangs
(off a shadow row until plan step ``balance:X-bi-6-4d-2``, off the transfer by
a side link since): the one join
from a transfer to a leg's covering movement, its expressions, every loader in this package
built on it (:func:`planned_transfer_legs` and the grid's
:func:`grid_transfer_legs` included, since each reads the join), and
:func:`movement_parent`, the join's Python twin over one loaded movement, with
the two added after the split that read the same parent,
:func:`movement_parent_loads` (its loader options) and :func:`parent_entries`
(the list its parent loaded it into) -- the half of this package whose code
``X-bi-6-4d-2`` rewrote when the join moved off the shadows.  ``_records``
imports ``_leg`` and never the reverse.  Every public name is re-exported
here, so no import statement changed.

Services-boundary discipline (``CLAUDE.md`` Architecture / B6-01): no Flask
symbol, no writes, no clock.  It takes and hands back ORM rows, queries and
frozen dataclasses, and one export hands back a live COLLECTION a caller
edits: :func:`parent_entries` returns the parent's loaded list, which the
removal act takes a movement out of.  That edit is the act's; this package
stages no write of its own (a query or lazy load it runs may autoflush
writes the session already holds).
"""

from app.services.transfer_legs._leg import (
    TRANSFER_FROM_PREFIX,
    TRANSFER_TO_PREFIX,
    PlanItem,
    PlanParent,
    TransferLeg,
    cell_key,
    expense_legs,
    is_leg_key,
    key_order,
    leg_label,
    leg_of,
    parent_of,
    side_legs,
    side_movements,
    transfer_side_leg,
)
from app.services.transfer_legs._records import (
    grid_transfer_legs,
    held_transfer_entries,
    movement_parent,
    movement_parent_loads,
    offerable_transfer_legs,
    parent_entries,
    planned_transfer_legs,
    preload_side_records,
    recorded_transfer_legs,
    transfer_family_movements,
    transfer_holds_a_movement,
    transfer_movement_rows,
    transfer_sides,
    transfers_holding_records,
)

__all__ = [
    "PlanItem",
    "PlanParent",
    "TRANSFER_FROM_PREFIX",
    "TRANSFER_TO_PREFIX",
    "TransferLeg",
    "cell_key",
    "expense_legs",
    "grid_transfer_legs",
    "held_transfer_entries",
    "is_leg_key",
    "key_order",
    "leg_label",
    "leg_of",
    "movement_parent",
    "movement_parent_loads",
    "offerable_transfer_legs",
    "parent_entries",
    "parent_of",
    "planned_transfer_legs",
    "preload_side_records",
    "recorded_transfer_legs",
    "side_legs",
    "side_movements",
    "transfer_family_movements",
    "transfer_holds_a_movement",
    "transfer_movement_rows",
    "transfer_side_leg",
    "transfer_sides",
    "transfers_holding_records",
]
