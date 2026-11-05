"""
Shekel Budget App -- A ONE-OFF is a rule-less definition plus its placed rows

**The ONE producer of a one-off** (plan step ``balance:X-bi-7b``, ruling
**R-BAL20**: every plan item has exactly one definition).  A one-off budget
line used to be a bare ``budget.transactions`` row carrying its own name,
category, flags and figure, with ``template_id IS NULL`` as how the code told
it from a generated row.  It is a DEFINITION now -- a
``budget.transaction_templates`` row with no recurrence rule -- holding the
name, category, flags and price, plus the row(s) it PLACES: one for a grid
one-off, one per paycheck for a bank-born envelope once ``X-f6c`` names the
definition from a merchant rule (**R-BAL24**).  The row holds what is the
row's: its paycheck, its status, its due date.

Four writers made a link-less plan item, and all four call
:func:`place_one_off` since the family's third leaf: the grid's two create
doors (``routes/transactions/create.py``), the row a bank line's money
requires (``statement_match._uncategorized.mint_uncategorized``) and the
envelope a NEW-ENVELOPE merchant answer mints the first time it fires
(``statement_match._container._create_envelope``, ``X-f6c``'s shape).  They
differ only in what they put in :class:`OneOffToPlace`.  :func:`place_row_of`
has three callers of its own: that producer, the bank door placing a row of
an existing definition where a paycheck holds none, and carry-forward
placing the row a rule-less envelope's leftover rolls into (**R-BAL44**).

**What the producer states, and the ruling each clause is.**

* **The price lives on the definition, and a one-off's series holds ONE
  version dated on the row's due date** (**R-BAL21**).  The row states no
  figure: it is priced through amount rule 3
  (``cash_ledger._amount_source``) by its definition's series as of its own
  due date, exactly as ``X-au-f`` ruled for the one-time transfer.  With one
  version the series is flat on every date, so the row's date can move
  freely and prices nothing differently.
* **A one-off is due on its placed paycheck's START unless the owner states
  a date** (**R-BAL22**) -- the rule the transfer twin states for its own row
  (``routes/transfers/_instances._materialize_one_time_transfer``; ``X-ci``
  is where the two spellings meet), balance-neutral by construction because
  ``DerivedPeriod.attribution_day`` already budgets an undated item to its
  paycheck's start.  (The spec also named
  ``carry_forward_service._leftover_due_date``; its rule-less arm was
  deleted at 7a and it reads the cadence now.)
* **The row records its own due date as the occurrence it answers**
  (``occurs_on = due_date``, **R-BAL25**), so no row answers no occurrence
  and ``recurrence:R19-b``'s NOT NULL binds on it.  Every later writer of
  a placed row's date goes through :func:`state_due_date`, so the two
  columns cannot part.
* **A period move RE-PLACES the row** (**R-BAL33**, leaf 7b-2): moved to
  another paycheck it takes that paycheck's start as its due date unless
  its date was not its old paycheck's start -- an owner-stated day, read by
  position -- which is R-BAL22's default following the placement
  (:func:`due_date_after_move`).  Both doors that move a row take it:
  carry-forward's move-whole arm and the popover's period move.
* **A typed figure on a one-off's ONLY row is RESTATED on the definition,
  in place** (**R-BAL29**, leaf 7b-2): the version the row's due date reads
  takes the figure, the series stays one version, and the row stays priced
  by it (:func:`restate_price`).  A row the interim between the family's
  first two leaves detached (OWN with ``is_override``) is re-attached by the
  same act (**R-BAL37**).  **A typed figure on a row whose definition holds
  ANOTHER row is that row's OWN** (**R-BAL43**, leaf 7b-3): one occurrence
  among many, exactly as a recurring definition's row re-priced at the
  card, and the definition's standing price is edited on its own form.  The
  fork is the PATCH door's (``routes/transactions/_field_updates``), because
  its two arms sit on either side of the definition's propagation.
* **Two rows of one definition never answer one day, and never share a
  paycheck** (**R-BAL24**, **R-BAL25**; :func:`another_row_answers`,
  :func:`holds_a_row_in`): every writer that dates or places a row asks
  before writing, so the occurrence index is a backstop and not a door.
* **The row carries no flag.**  ``tracks_purchases`` and
  ``visible_to_companion`` read the definition for every template-linked
  row, so the sealed cells on the row are never written here; the cutover
  leaf deletes them.

:func:`place_row_of` is the placing half alone -- a row of an EXISTING
rule-less definition in a paycheck -- split out because a bank-born envelope
definition places one row per paycheck it is filed into (**R-BAL24**), and
because it states the five definition-derived columns through the same
:func:`~app.services.recurrence_engine.unruled_row_fields` the maintain twin
re-declares them with, so a row as born and a row as re-declared cannot
differ.

**The definition carries no scenario.**  Definitions never have; a one-off
created in a non-baseline scenario mints one that carries none and places its
row in the submitted scenario, noted rather than forked while one scenario
exists and the selector is a stub (**R-BAL26**).

Boundary discipline (``CLAUDE.md`` Architecture): plain data in, ORM rows
out, no Flask import, no clock read.  It MUTATES and FLUSHES -- the row's
foreign keys need the definition's id -- and does NOT commit; the caller
owns the unit of work.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from app import ref_cache
from app.enums import AmountSourceEnum, StatusEnum
from app.extensions import db
from app.models.transaction import Transaction
from app.models.transaction_template import TransactionTemplate
from app.services import template_amount_service
from app.services.amount_ownership import declare_derived
from app.services.pay_calendar import DerivedPeriod
from app.services.recurrence_engine import unruled_row_fields


@dataclass(frozen=True)
class OneOffToPlace:  # pylint: disable=too-many-instance-attributes
    """What a one-off's DEFINITION says, before it exists.

    Pylint: ``too-many-instance-attributes`` (8/7) -- the eight ARE one
    fact, and there is one per column of ``budget.transaction_templates`` an
    owner STATES about a plan item (``is_active`` and ``sort_order`` take
    their defaults; a one-off is born live and unordered): what the owner (or
    the bank) said this plan item IS.  What is NOT here is where its row goes -- the paycheck,
    the scenario, the due date -- which :func:`place_one_off` takes as the
    same placing arguments :func:`place_row_of` does, because a one-off is a
    definition PLUS a placed row (**R-BAL20**) and the object mirrors that
    split rather than hiding it.  Decomposing the eight further would split
    one table's row across two values.

    **A parameter object because the producer is PUBLIC and reached from
    three modules** (this project's remedy for a public function over the
    argument bound): a signature that cannot be read at a call site is read
    wrong at each of them.

    Attributes:
        user_id: The OWNER, which is a column on both rows (plan step
            ``pay_calendar:C13-a``).  The caller has proved the account, the
            category, the scenario and the paycheck are all this user's; the
            two composite keys refuse the row otherwise.
        account_id: The account the money moves through.
        transaction_type_id: Expense or income.
        name: What the plan item is called.  ``budget.transaction_templates
            .name`` is ``String(200)`` and NOT NULL, as the row's is; the
            bank door cuts its merchant string to fit before building this.
        amount: What it is planned to cost, which becomes the definition's
            ONE version dated on the row's due date (**R-BAL21**).  ``0.00``
            is legal -- a bank-born envelope budgets nothing, because
            nothing budgeted it.
        category_id: What the money IS, or ``None`` where nothing can say so
            -- the bank door's row for money it could not categorise
            (**R-FN**), which is why ``transaction_templates.category_id``
            is nullable since migration ``9c1e4b7a2d3f``.  Every grid door
            requires one at its schema.
        is_envelope: Whether the definition's rows take purchase entries --
            the DEFINITION's setting, read for every row by
            ``Transaction.tracks_purchases``.
        companion_visible: Whether a companion of the owner may see its rows
            -- likewise the definition's.
    """

    user_id: int
    account_id: int
    transaction_type_id: int
    name: str
    amount: Decimal
    category_id: "int | None"
    is_envelope: bool = False
    companion_visible: bool = False


def due_date_for(stated: "date | None", period: DerivedPeriod) -> date:
    """Return the day a one-off placed in *period* is due (**R-BAL22**).

    The one statement of the rule for a row this module places: the owner's
    stated day, else the paycheck's START.  Balance-neutral by construction
    -- ``DerivedPeriod.attribution_day`` already budgets an undated item to
    its paycheck's start -- but on a card, where a row counts from this date
    (ruling plan#2926), and the day amount rule 3 prices the row on.

    Args:
        stated: The day the owner (or the bank) said it falls, or ``None``.
        period: The paycheck the row is placed in.

    Returns:
        The due date.
    """
    return stated if stated is not None else period.start_date


def due_date_after_move(
    due_date: date, *, source_start: date, target: DerivedPeriod,
) -> date:
    """Return the day a placed row due on *due_date* is due once moved to *target*.

    **A period move re-places a one-off** (**R-BAL33**): :func:`due_date_for`
    applied after the move, so R-BAL22's default -- the paycheck's start --
    follows the placement, and an owner-stated day stays.  Which of the two
    the row carries is read BY POSITION: a date other than its source
    paycheck's start is the owner's, since **R-BAL25** rejected a stored
    marker; the miss (an owner who typed exactly the start day is moved to
    the new start) is benign.  Both doors that move a row call this --
    carry-forward's move-whole arm and the popover's period move -- so the
    date a rolled-forward one-off reads on the dashboard pulse and in
    payment timeliness is the same whichever door moved it.

    Worked: Kayla's Kindle, born in the paycheck starting 2026-09-04 (due
    09-04 by default), carried forward to the 09-18 paycheck and paid 09-20
    -- due 09-18 and two days late, where the date the move left behind
    read fourteen days overdue.  A typed 09-10 stays 09-10 and reads overdue
    honestly.  The balance is identical under either date: a one-off's
    series is flat (**R-BAL21**) and ``attribution_day`` budgets the row to
    its paycheck.

    Args:
        due_date: The row's date before the move.
        source_start: The start of the paycheck it is leaving.
        target: The paycheck it is moving to.

    Returns:
        The row's due date after the move.
    """
    return due_date_for(
        None if due_date == source_start else due_date, target,
    )


def another_row_answers(
    definition_id: int, scenario_id: int, day: date, *,
    except_row_id: "int | None" = None,
) -> bool:
    """Whether a live row of *definition_id* other than *except_row_id* already answers *day*.

    **The occurrence index's own predicate, stated once in the ORM**
    (``idx_transactions_template_scenario_occurrence``: unique on
    ``(template_id, scenario_id, occurs_on)`` over live rows, no status
    term, ``is_override`` NOT excluded since ``X-au-h``).  Every writer that
    dates a placed row asks it before writing, so a collision is a designed
    refusal and never an ``IntegrityError`` a route renders as *Invalid
    reference* or a batch dies on: the popover's date and period moves, the
    bank door placing a row, carry-forward's re-placing and its leftover
    placement.  Found by 7b-3's adversarial reviews at four of those writers.

    Args:
        definition_id: The rule-less definition.
        scenario_id: The scenario the rows are in.
        day: The occurrence a row would answer (``occurs_on = due_date``,
            **R-BAL25**).
        except_row_id: The row being moved, which may answer *day* already.

    Returns:
        ``True`` when the write would collide.
    """
    query = db.session.query(Transaction.id).filter(
        Transaction.template_id == definition_id,
        Transaction.scenario_id == scenario_id,
        Transaction.occurs_on == day,
        Transaction.is_deleted.is_(False),
    )
    if except_row_id is not None:
        query = query.filter(Transaction.id != except_row_id)
    return query.first() is not None


def holds_a_row_in(
    definition_id: int, scenario_id: int, period_id: int, *,
    except_row_id: "int | None" = None,
) -> bool:
    """Whether a live row of *definition_id* other than *except_row_id* sits in *period_id*.

    **R-BAL24's rule, stated once**: a rule-less definition holds ONE placed
    row per paycheck, whatever that row's status -- a cancelled, credited,
    match-claimed or fixed-figure-closed row is still the paycheck's row of
    it, though none of those is OFFERABLE as a place to file.  The bank
    door's PLACE arm and the popover's period move ask this; the occurrence
    index (:func:`another_row_answers`) is the narrower fact those writers
    also have to respect, and the two are asked together where a row is
    placed.  Found by 7b-3's adversarial reviews: the PLACE arm was offered
    on "no offerable row" and met the index on a cancelled one.

    Args:
        definition_id: The rule-less definition.
        scenario_id: The scenario the rows are in.
        period_id: The paycheck.
        except_row_id: The row being moved.

    Returns:
        ``True`` when the paycheck already holds a row of the definition.
    """
    query = db.session.query(Transaction.id).filter(
        Transaction.template_id == definition_id,
        Transaction.scenario_id == scenario_id,
        Transaction.pay_period_id == period_id,
        Transaction.is_deleted.is_(False),
    )
    if except_row_id is not None:
        query = query.filter(Transaction.id != except_row_id)
    return query.first() is not None


def state_due_date(row: Transaction, due: date) -> None:
    """Write *due* onto *row* as its due date AND the occurrence it answers.

    **The one ORM writer of a placed row's date** (**R-BAL25**: a one-off's
    row records its own due date as the occurrence it answers, so
    ``occurs_on = due_date`` is one fact in two columns).  :func:`place_row_of`
    states both at birth from one local; every edit that moves the date
    afterwards -- the popover's date input, a period move's re-placing --
    goes through here so the pair cannot part.  (Carry-forward's bulk
    UPDATE writes the pair in SQL, the one spelling that cannot reach an
    ORM writer; it states the same two columns.)

    Args:
        row: A placed :class:`~app.models.transaction.Transaction`.
        due: The day it is due, which is the occurrence it answers.
    """
    row.due_date = due
    row.occurs_on = due


def restate_price(row: Transaction, amount: Decimal) -> None:
    """State *amount* as the price of *row*'s definition, and make *row* read it.

    **A typed figure on a one-off's only row corrects its definition's price
    in place** (**R-BAL21**, **R-BAL29**): a grid one-off's only occurrence
    IS the definition, so the figure is a statement about the definition and
    not about one occurrence among many -- which is why a RECURRING row's
    typed figure detaches that row (OWN, ``is_override``) and this one never
    does.  ``template_amount_service.restate_in_effect`` corrects the version
    the row's due date reads (asked AFTER whatever the same edit did to the
    date), so the series stays one version.  **For a placed row that is its
    definition's ONLY row** (**R-BAL43**): the PATCH door asks
    :func:`~app.services.definition_delete.is_last_row_of_its_definition`
    and sends a row of a MANY-row definition the other way, OWN with the
    flag, one occurrence among many; called on such a row this would
    re-price every row of the definition (finding **BAL-499**).

    **Then the row is declared TEMPLATE-priced and unflagged, whatever it
    was** (**R-BAL37**, developer 2026-09-15).  Between the family's first
    leaf and this one a typed figure landed OWN on the row with
    ``is_override`` beside it (R-BAL28's stated interim), leaving the
    definition at the old price -- rule 14's two homes.  Restating the
    definition alone would leave such a row on its own stale figure and the
    typed one invisible; keeping it detached would leave the Recurring page
    stating a price the grid does not use.  Declaring the ruled shape here
    -- priced by the definition, carrying no flag, which is what
    :func:`place_row_of` writes at birth -- is idempotent on a row never
    detached and heals a detached one on its next TYPED FIGURE (a rename, a
    flag or a date edit alone leaves it detached).  A residue row nobody
    re-priced by the cutover was re-attached by its migration
    (``X-bi-7d-2``, ruling **R-BAL37**; 0 on the 2026-09-18 restore).

    Args:
        row: A placed :class:`~app.models.transaction.Transaction`, its
            ``template`` loaded or loadable.
        amount: The figure the owner typed.
    """
    template_amount_service.restate_in_effect(
        row.template, amount, on=row.due_date,
    )
    declare_derived(row, AmountSourceEnum.TEMPLATE)
    row.is_override = False


def place_row_of(
    definition: TransactionTemplate,
    period: DerivedPeriod,
    *,
    scenario_id: int,
    due_date: "date | None" = None,
) -> Transaction:
    """Place a row of the rule-less *definition* in *period*.

    The placing half of :func:`place_one_off`, and the whole act for a
    definition that already exists -- a bank-born envelope's row in a later
    paycheck (**R-BAL24**).  The five definition-derived columns come from
    :func:`~app.services.recurrence_engine.unruled_row_fields`, the ONE
    statement the maintain twin re-declares them with; what is stated here
    is only what the row IS: whose, which definition's, which paycheck's,
    which occurrence (its own due date, **R-BAL25**), which scenario, born
    Projected and the owner's to move without a flag
    (``is_override = False``: a rule-less definition runs no pass to keep off
    the row, **R-BAL28**).

    Does NOT commit.  FLUSHES, so the caller reads a row with an id -- the
    settle verb the bank door applies next needs one.

    Args:
        definition: The rule-less
            :class:`~app.models.transaction_template.TransactionTemplate`.
            Flushed, so ``definition.id`` is set.
        period: The paycheck to place the row in, as the owner's calendar
            derived it.
        scenario_id: The scenario the row is placed in.
        due_date: The owner's stated day, or ``None`` for the paycheck's
            start (:func:`due_date_for`).

    Returns:
        The placed, flushed :class:`~app.models.transaction.Transaction`.
    """
    due = due_date_for(due_date, period)
    row = Transaction(
        **unruled_row_fields(definition, due)._asdict(),
        user_id=definition.user_id,
        template_id=definition.id,
        pay_period_id=period.period_id,
        occurs_on=due,
        scenario_id=scenario_id,
        status_id=ref_cache.status_id(StatusEnum.PROJECTED),
        is_override=False,
        is_deleted=False,
    )
    db.session.add(row)
    db.session.flush()
    return row


def place_one_off(
    spec: OneOffToPlace,
    period: DerivedPeriod,
    *,
    scenario_id: int,
    due_date: "date | None" = None,
) -> Transaction:
    """Mint a one-off's definition and place its row in *period*.

    Three acts, in this order: the definition is written and flushed (the
    row's key and the series' key both need its id); its series is opened
    with ONE version dated on the row's due date through the series' one
    write door (``template_amount_service.set_amount``, **R-BAL21**) --
    the definition's only price, since plan step X-bp dropped the
    ``default_amount`` copy the constructor had to carry while the column
    was NOT NULL; and the row is placed by :func:`place_row_of`,
    with the same placing arguments this function takes.

    Does NOT commit -- the route or the bank door owns the unit of work, so
    a refusal downstream (an ``IntegrityError`` on a foreign key, a settle
    day that has not happened) takes the definition back with the row.

    Args:
        spec: What the one-off's definition says (:class:`OneOffToPlace`).
        period: The paycheck to place its row in, as the owner's calendar
            derived it -- the :class:`~app.services.pay_calendar.DerivedPeriod`
            the caller already resolved, so the paycheck's start is read off
            one derivation rather than re-resolved from an id.
        scenario_id: The scenario the ROW is placed in -- the submitted one
            at the grid, the baseline for a bank-born row.  The definition
            carries none (**R-BAL26**).
        due_date: The day the owner said it falls, or ``None`` for the
            paycheck's start (**R-BAL22**).

    Returns:
        The placed, flushed :class:`~app.models.transaction.Transaction`;
        its ``template`` is the minted definition.
    """
    definition = TransactionTemplate(
        user_id=spec.user_id,
        account_id=spec.account_id,
        category_id=spec.category_id,
        transaction_type_id=spec.transaction_type_id,
        name=spec.name,
        is_envelope=spec.is_envelope,
        companion_visible=spec.companion_visible,
    )
    db.session.add(definition)
    db.session.flush()
    due = due_date_for(due_date, period)
    template_amount_service.set_amount(definition, spec.amount, effective_on=due)
    return place_row_of(definition, period, scenario_id=scenario_id, due_date=due)


__all__ = [
    "OneOffToPlace",
    "another_row_answers",
    "due_date_after_move",
    "due_date_for",
    "holds_a_row_in",
    "place_one_off",
    "place_row_of",
    "restate_price",
    "state_due_date",
]
