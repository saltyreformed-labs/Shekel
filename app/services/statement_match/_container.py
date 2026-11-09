"""WHICH budget line holds a purchase a bank line became, and when it CLOSES.

Split out of :mod:`._create` at plan step ``bank_import:X-ge``, when that
module passed this project's 1,000-line bound.  **The seam is a SUBJECT and not
a line count** -- the same cut :mod:`._rules` / :mod:`._stating` was made on,
and :mod:`._creations` before it.  :mod:`._create` answers *what is this bank
line, as a purchase*: which line may be recorded, which days it carries, what
the act brought into existence.  Everything here answers the other question the
door has to settle before it can write anything: **which budget line CONTAINS
it**, and what closing that container means.

Two subjects, two reasons to change.  The container's rules move when the
budget's shape does -- ruling **R-FX**'s settled-row clause, finding
**N-327**'s one-envelope-per-answer-per-period registry, ruling **R-ED**'s
"the seam owns the day" -- and the purchase's move when the bank's record does.
Nothing changed on the way across.

**The offer set is the SCREEN's, and that is the property everything here
rests on.**  A destination this module will accept is one
:func:`~._destinations.destinations_for` returns, narrowed to the rows that
still take a purchase as the acting pass has left them
(:func:`~._destinations.current_destinations`) -- so a row the screen may not
offer cannot be reached by crafting a request, and a row it does offer cannot
be refused a tier deeper.

Services-boundary discipline (``CLAUDE.md`` Architecture): plain data in, no
Flask import, no clock read.  It MUTATES and does NOT commit -- the route owns
the unit of work.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from app import ref_cache
from app.enums import TxnTypeEnum
from app.exceptions import ValidationError
from app.extensions import db
from app.models.category import Category
from app.models.transaction import Transaction
from app.models.transaction_template import TransactionTemplate
from app.services import (
    transaction_service,
)
from app.services.one_off import (
    OneOffToPlace,
    another_row_answers,
    due_date_for,
    holds_a_row_in,
    place_one_off,
    place_row_of,
)
from app.services.pay_calendar import DerivedPeriod
from app.services.scenario_resolver import require_baseline_scenario
from app.services.settle_day import SettleDay

from ._creations import (
    CreatedPurchase,
    NewEnvelope,
    PurchaseCreation,
    envelope_answer_key,
)
from ._destinations import current_destinations
from ._scope import ReviewScope


#: What a created envelope BUDGETS.  Zero, because nothing budgeted it: the
#: line is spending the plan did not anticipate, and inventing a budget equal to
#: the spend would make every unplanned purchase look planned.  A projected
#: envelope holds back ``max(estimated - posted - credit, unposted)``, so with a
#: purchase that already carries its posting day the reservation is ``0.00`` and
#: the purchase books its own cash -- the row states what happened and reserves
#: nothing.
_NO_BUDGET = Decimal("0.00")


@dataclass
class MintedEnvelopes:
    """What ONE request has already minted or placed, so a press converges.

    Plan step ``bank_import:X-f6a-4``, finding **N-327**, developer ruling
    2026-08-20.  A merchant rule answering *a new envelope called X* used to
    mint one PER LINE: measured on the developer's own statement, a ``Lowe's``
    answer places 4 lines over 3 pay periods, so one press made 4 envelopes,
    two of them in the SAME period.  No figure was wrong -- each closes at its
    own purchases -- and what fragmented was the budget.

    **Keyed on the DEFINITION since leaf 7b-3 of ``balance:X-bi-7b``**
    (ruling **R-BAL24**, finding **N-328**).  A new-envelope answer mints ONE
    rule-less definition the first time it fires in a press -- ``by_answer``
    remembers which, per ``(name, category_id)`` -- and every later line of
    that answer, or of a TEMPLATE answer naming a rule-less definition, is a
    ROW of that definition in the line's own paycheck: ``by_row`` remembers
    the row placed per ``(template_id, pay_period_id)``, so two lines in one
    paycheck share a row and two paychecks get one row each.  It was keyed on
    ``(name, category_id, pay_period_id)`` -> row, which converged within a
    press by a string compare and across statements by nothing.

    **Scoped to ONE REQUEST, and that scope is the design rather than a
    limitation.**  The cross-STATEMENT half is the definition itself: the
    merchant's answer NAMES it after the first mint, and
    :func:`~._placement._template_placement` finds or places its row in any
    later paycheck.  Only the within-one-press half needs a write-side
    registry at all, because at render time the definition this press is
    about to mint does not yet exist for any rule to name.

    **A refused item leaves nothing here, and the CALLER is what makes that
    true.**  :func:`~._batch.apply_reviewed` remembers after the act that made
    it has RETURNED, so a creation rolled back inside its own SAVEPOINT
    (ruling **R-FZ**) leaves no entry pointing at a row that no longer exists.
    A first implementation remembered inside the create door, one line above
    the refusal that kills the item -- and the very next line of the sweep
    then looked up an id the rollback had taken, and died on ``NoneType``.
    The registry cannot be written where the write is not yet known to have
    survived.

    Attributes:
        by_answer: ``{(name, category_id): template_id}`` -- the definition
            each new-envelope answer minted in this request.
        by_row: ``{(template_id, pay_period_id): transaction_id}`` -- the row
            this request minted or placed for each definition per paycheck.
    """

    by_answer: "dict[tuple[str, int], int]"
    by_row: "dict[tuple[int, int], int]"

    @classmethod
    def none_yet(cls) -> "MintedEnvelopes":
        """Return the empty registry one request starts with."""
        return cls(by_answer={}, by_row={})

    def definition_for(self, new_envelope: NewEnvelope) -> "int | None":
        """Return the definition this request already minted for that answer.

        Args:
            new_envelope: The answer the owner stated.

        Returns:
            The ``template_id``, or ``None`` when this request has minted none.
        """
        return self.by_answer.get(envelope_answer_key(new_envelope))

    def row_for(self, template_id: int, pay_period_id: int) -> "int | None":
        """Return the row this request already holds for that definition there.

        Args:
            template_id: The definition.
            pay_period_id: The period the purchase is budgeted in.

        Returns:
            The transaction id, or ``None`` when this request placed none.
        """
        return self.by_row.get((template_id, pay_period_id))

    def remember(
        self, creation: PurchaseCreation, created: "CreatedPurchase",
    ) -> None:
        """Record what this request minted or placed for *creation*.

        **Called by the BATCH after the act RETURNED**, never by the door that
        creates -- see the class docstring for what a first version cost.

        **Called for EVERY recorded purchase, not only one that created its
        envelope** (found by both of 7b-3's adversarial reviews): a
        new-envelope answer's first firing that CONVERGED on a placed
        envelope of its name created nothing, and a batch that remembered
        only creations then let the next line of that answer, in another
        paycheck of the same press, mint a second definition and re-flip the
        rule onto it.  The answer such a line made true rides out on
        :attr:`~._creations.CreatedPurchase.answer_named`.

        Args:
            creation: The act the caller submitted, which it still holds --
                its new-envelope answer keys ``by_answer``.  Taken as an
                argument rather than carried out through *created*, because a
                value threaded through a return only so its own caller can
                read it back is a round trip.
            created: What the act did: the envelope, its definition, its
                period, and the stored answer it made TEMPLATE.
        """
        answered = creation.new_envelope or created.answer_named
        if answered is not None:
            self.by_answer[envelope_answer_key(answered)] = created.template_id
        self.by_row[
            (created.template_id, created.pay_period_id)
        ] = created.transaction_id


def reject_ambiguous_destination(creation: PurchaseCreation) -> None:
    """Refuse a submission naming two destinations or none.

    The arms are exclusive by construction: a purchase has exactly one parent,
    so "put it in this envelope", "make an envelope for it" and "place a row
    of this definition for it" cannot two of them be the answer.  Stated as a
    refusal rather than a precedence rule -- a door that silently preferred
    one arm would record something the owner did not ask for.  THREE arms
    since leaf 7b-3 of ``balance:X-bi-7b`` (ruling **R-BAL24**).

    Args:
        creation: What the owner submitted.

    Raises:
        ValidationError: When more than one arm or none is named.
    """
    named = sum((
        creation.transaction_id is not None,
        creation.new_envelope is not None,
        creation.template_id is not None,
    ))
    if named != 1:
        raise ValidationError(
            "Choose exactly one place for this purchase: an envelope you "
            "already have, a new one, or a one-off envelope to place here.  "
            "Nothing was changed."
        )


def reject_incomplete_new_envelope(creation: PurchaseCreation) -> None:
    """Refuse a NEW envelope stated by halves.

    A budget line needs a name AND a category: ``transactions.category_id`` is
    what every spending report groups by, and a row created without one would
    be invisible to the very analysis the purchase exists to feed.  The name is
    ``transactions.name``, which is NOT NULL.

    **It is the DOOR's refusal since plan step X-f6a-3c-2, not the schema's.**
    It was a ``@validates_schema`` rule on ``StatementPurchaseSchema``, which
    was right while one POST was one act: a nested schema error refuses the
    WHOLE payload, and once a POST carries a whole reviewed pass that means an
    owner who picked "a new envelope" on one line and left its category on the
    form's own default lost every other act they had ticked -- 124 proposals
    and 90 creations on the developer's own statement.  The ruled failure
    policy is that a refused item costs only itself, and a rule that can only
    refuse the whole submission cannot honour it.  Found by adversarial
    financial review 2026-08-19.

    It fires BEFORE :func:`_owned_category`, which would otherwise answer a
    missing category with "that category is not one of yours" -- a true
    sentence about the wrong problem.

    Args:
        creation: What the owner submitted.

    Raises:
        ValidationError: When the new-envelope arm is named without both of
            its own fields.
    """
    new = creation.new_envelope
    if new is None:
        return
    if new.name is None or new.category_id is None:
        raise ValidationError(
            "A new envelope needs both a name and a category.  Nothing was "
            "changed."
        )


def _existing_envelope(
    creation: PurchaseCreation,
    pay_period_id: int,
    scope: ReviewScope,
) -> Transaction:
    """Return the chosen envelope, refusing one the screen could not offer.

    **Resolved against the pass's own destination set rather than queried
    directly**, so the set this door may write into is exactly the set the
    screen may offer -- the same one-scope-for-reader-and-writer property
    :func:`~._resolve.resolve_rows` rests on.  An envelope belonging to another
    user, another account, a cancelled or archived row, or a settled row whose
    figure is a stored number is not a destination and cannot be reached by
    crafting a request.

    **What the pass has moved is asked of the rows as they stand NOW, not of
    the scope** (plan step ``bank_import:X-f6a-3c-2``;
    :func:`~._destinations.current_destinations`), and on the developer's own
    data that is 15 lines rather than a hypothetical: 4 envelopes are both
    named by a proposal and offered as a destination, so a pass that accepts
    the proposals first leaves 15 creatable lines aimed at an envelope a match
    has just settled at the bank's figure.  Asking here is what gives those
    15 the sentence about the envelope being gone rather than a refusal from
    a tier deeper.  **A match that merely NAMES the envelope refuses nothing
    since plan step ``credit_card:CC-5-4a-5``** (leaf 5c-2b, finding
    **CC-385**, ruling **R-CC141**): a Projected envelope's payment a match
    names is one its revert kept un-dated, which counts nothing.

    **The PERIOD is part of that set and a first version left it out**, so the
    screen offered the line's own period and the door accepted any of them --
    which let a crafted request file a swipe into a Groceries envelope
    eighteen months forward, or raise a closed past envelope's recorded cost in
    a period the line has nothing to do with.  It also made the two arms
    disagree with each other: :func:`_create_envelope` has always placed a new
    row by the day the purchase was MADE.  Found by adversarial security review
    2026-08-19.

    Args:
        creation: What the owner submitted.
        pay_period_id: The period holding the day the purchase was made, which
            is the only one whose envelopes the screen offers for this line.
        scope: The pass's derived offer set (:class:`~._scope.ReviewScope`).

    Returns:
        The envelope.

    Raises:
        ValidationError: When the id names nothing the screen could have
            offered for this line.
    """
    offered = {
        destination.transaction_id
        for destination in current_destinations(scope.destinations)
        if destination.period.period_id == pay_period_id
    }
    if creation.transaction_id not in offered:
        raise ValidationError(
            "That envelope is not one this purchase can go into -- it may "
            "have been deleted or cancelled, it may already be matched to a "
            "statement line, or it may have closed at a fixed figure that a "
            "new purchase cannot change.  Reload the page and pick another.  "
            "Nothing was changed."
        )
    return db.session.get(Transaction, creation.transaction_id)


def _owned_category(
    creation: PurchaseCreation, scope: ReviewScope,
) -> Category:
    """Return the category the new envelope will carry, refusing another's.

    The IDOR probe every create route in this project performs before a write:
    a foreign ``category_id`` satisfies the foreign key -- the row exists -- and
    would link another user's category onto this owner's budget line.

    Args:
        creation: What the owner submitted.
        scope: The pass, which is the ONE statement of whose categories may be
            reached.

    Returns:
        The category.

    Raises:
        ValidationError: When the id names no category of this owner's.
    """
    category = (
        db.session.query(Category)
        .filter(
            Category.id == creation.new_envelope.category_id,
            Category.user_id == scope.owner_id,
            # ARCHIVED categories are not selectable targets for a new row --
            # ``category_service.list_active_categories`` is what the picker
            # renders and it filters on this, so accepting one here would be
            # the offer-versus-accept drift this door exists to close.
            Category.is_active.is_(True),
        )
        .one_or_none()
    )
    if category is None:
        raise ValidationError(
            "That category is not one of yours.  Reload the page and pick "
            "another.  Nothing was changed."
        )
    return category


def _create_envelope(
    creation: PurchaseCreation,
    category: Category,
    period: DerivedPeriod,
    scope: ReviewScope,
) -> Transaction:
    """Mint a new, empty envelope for this line's period, as a one-off.

    **Through the ONE producer of a one-off** (``one_off.place_one_off``,
    plan step ``balance:X-bi-7b`` leaf 7b-3, ruling **R-BAL24**): a
    rule-less DEFINITION carrying the answer's name, category and the
    envelope flag, its series opened at ``$0.00`` on the row's due date, and
    ONE row placed in the line's period -- dated at that paycheck's start,
    answering it, priced by the definition.  It wrote a bare link-less
    ``Transaction`` until this leaf, so the container a merchant answer files
    into had no identity beyond its NAME (finding **N-328**); the definition
    IS that identity now, and the merchant's answer comes to name it
    (:func:`~._naming.name_the_filed_definition`).

    **Born Projected, budgeting nothing**, which is the two facts a budget line
    created from a statement can honestly state.  Projected because
    ``status_seam.apply_status_change`` is the ONE door to a settled status and
    a row may not be born in one (plan step ``balance:X-aj2`` is where that
    becomes structural); nothing because the spending was unplanned, and a
    budget equal to the spend would make it look planned.

    **It is an ENVELOPE (``is_envelope=True``), and that is the whole point of
    the arm.**  A plain row would be a budget line that is also its own single
    payment, so the next statement line for the same merchant would have
    nowhere to go; an envelope can hold that one too, and the row's cost stays
    the sum of what the bank actually showed.

    Args:
        creation: What the owner submitted, for the envelope's name.
        category: The category the owner picked, already proved theirs.
        period: The paycheck holding the day the purchase was made, as the
            pass derived it.
        scope: The pass, which is the ONE statement of whose account this row
            belongs to.

    Returns:
        The placed, flushed :class:`~app.models.transaction.Transaction`.
    """
    return place_one_off(
        OneOffToPlace(
            # The pass is the ONE statement of whose row this is (plan step
            # ``pay_calendar:C13-a``), the same source ``account_id`` and the
            # baseline scenario below already read.
            user_id=scope.owner_id,
            account_id=scope.account_id,
            transaction_type_id=ref_cache.txn_type_id(TxnTypeEnum.EXPENSE),
            name=creation.new_envelope.name,
            amount=_NO_BUDGET,
            category_id=category.id,
            is_envelope=True,
        ),
        period,
        # **The BASELINE scenario, unconditionally.**  A what-if scenario is a
        # hypothesis about money that has not moved, and a bank statement is
        # the opposite of one: this row records something that already
        # happened.  Reading the scenario off whatever row the period happened
        # to hold would file a real movement under a hypothesis the first time
        # the what-if work lands, which is exactly the class of silent
        # misplacement ``_candidates`` declines to guess at.
        scenario_id=require_baseline_scenario(scope.owner_id).id,
    )


def _placeable_definition(
    creation: PurchaseCreation, placeable: "frozenset[int]",
) -> TransactionTemplate:
    """Return the rule-less definition a PLACE creation names, refusing any other.

    The same offer-versus-accept discipline :func:`_existing_envelope` keeps,
    resolved against the SAME set the screen offered from: the pass's
    :attr:`~._rules.RuleView.placeable_templates`, which is
    :func:`~._rules._offerable_template_rows` (this account's active envelope
    definitions, income excluded) narrowed to those with no rule.  A
    definition outside that set is refused here whatever the submission says
    -- a foreign one, a recurring one, one that is not an envelope, is
    archived, or is income's -- and the account scoping is the one read the
    view makes rather than a second query spelling its predicate (a first
    build re-spelled it and omitted the income clause; found by adversarial
    review).

    Args:
        creation: What the owner submitted, its ``template_id`` set.
        placeable: The pass's placeable definitions.

    Returns:
        The definition.

    Raises:
        ValidationError: When the id names no rule-less envelope definition
            the pass could offer.
    """
    if creation.template_id not in placeable:
        raise ValidationError(
            "That is not a one-off envelope of this account's that a row can "
            "be placed for.  Reload the page and pick another.  Nothing was "
            "changed."
        )
    return db.session.get(TransactionTemplate, creation.template_id)


def _placed_or_held(
    definition: TransactionTemplate,
    period: DerivedPeriod,
    scope: ReviewScope,
    minted: MintedEnvelopes,
) -> "tuple[Transaction, bool]":
    """Return *definition*'s row for this period, placing one only if needed.

    **One press places one row per definition per pay period** (finding
    **N-327**'s rule, re-keyed on the definition at leaf 7b-3): a second line
    reaching the same definition in the same period records into the row the
    first line placed.  A row the definition ALREADY held here is not this
    function's to find -- :func:`~._placement._template_placement` offers it
    as ``RECORD_IN`` and the creation then names it by id -- so what arrives
    here is a definition with no offerable row in this paycheck.

    **Placed only where the paycheck holds NO row of the definition and no
    row of it answers the paycheck's start** (``one_off.holds_a_row_in``,
    ``one_off.another_row_answers``: R-BAL24's one row per paycheck and the
    occurrence index's own predicate, asked at the door as the placement
    asks them at the screen).  A cancelled, credited, match-claimed or
    fixed-figure-closed row is the paycheck's row of the definition though
    it is not offerable, and a first build placed a second beside it and
    met the index as a bare ``IntegrityError`` that failed the whole press
    (found by both of 7b-3's adversarial reviews).  A stale page is the
    reachable case now; the refusal is a designed 400 and costs one item.

    Args:
        definition: The rule-less definition.
        period: The paycheck the purchase is budgeted in.
        scope: The pass, for the baseline scenario.
        minted: What this REQUEST has already placed.

    Returns:
        ``(row, created)`` -- the row, and whether this act placed it.

    Raises:
        ValidationError: When the paycheck already holds a row of the
            definition, or a row of it answers the day the placed row would.
    """
    already = minted.row_for(definition.id, period.period_id)
    if already is not None:
        return db.session.get(Transaction, already), False
    scenario_id = require_baseline_scenario(scope.owner_id).id
    if holds_a_row_in(
        definition.id, scenario_id, period.period_id,
    ) or another_row_answers(
        definition.id, scenario_id, due_date_for(None, period),
    ):
        raise ValidationError(
            f"This pay period already holds a row of {definition.name} that "
            f"cannot take a purchase, and an item holds one row per "
            f"paycheck.  Reload the page and pick another place.  Nothing "
            f"was changed."
        )
    return place_row_of(definition, period, scenario_id=scenario_id), True


def _minted_or_new(
    creation: PurchaseCreation,
    category: Category,
    period: DerivedPeriod,
    scope: ReviewScope,
    minted: MintedEnvelopes,
) -> "tuple[Transaction, bool]":
    """Return the envelope this purchase goes in, minting a definition only if needed.

    **One press mints one DEFINITION per answer, and one row of it per pay
    period** (finding **N-327**; re-keyed on the definition at leaf 7b-3 of
    ``balance:X-bi-7b``, ruling **R-BAL24**).  A second line reaching the
    same answer in the same period records into the row the first line
    made; one in another period gets a row of the SAME definition placed
    there, rather than a second definition beside it.

    **Recording into it is the act that already ships**, not a new one: it is
    exactly what :func:`_existing_envelope` does for an envelope the screen
    offered, including a settled one -- ruling **R-FX** admits a new purchase
    on a settled row when its recorded figure IS its purchases and the purchase
    states the day the bank took it, and both hold for a row this door created.
    Measured 2026-08-20 on a two-line sweep: the envelope's cash leg is
    ``0.00`` before and after the second purchase, because each purchase
    carries its own posting day and is its own cash movement.

    **It does NOT re-settle the envelope, and that matches the shipped path.**
    Recording into an already-settled envelope leaves its close day alone
    today, and doing otherwise here would give one arm of this door a re-dating
    rule the other arm does not have.

    Args:
        creation: What the owner submitted, for the envelope's name.
        category: The category they picked, already proved theirs.
        period: The paycheck holding the day the purchase was made.
        scope: The pass, which is the ONE statement of whose account this is.
        minted: What this REQUEST has already created.

    Returns:
        ``(envelope, created)`` -- the row, and whether this act made it.
    """
    template_id = minted.definition_for(creation.new_envelope)
    if template_id is not None:
        # The definition this press already minted or converged on for the
        # answer: its row here, or one placed here -- the PLACE arm's own
        # path, which is why a second line of one answer in another paycheck
        # never mints a second definition.
        return _placed_or_held(
            db.session.get(TransactionTemplate, template_id), period, scope,
            minted,
        )
    return _create_envelope(creation, category, period, scope), True


def _close_day(
    creation: PurchaseCreation,
    observed: SettleDay,
    envelope: Transaction,
    created: bool,
) -> "SettleDay | None":
    """Return the day this envelope should CLOSE on, or ``None`` to leave it.

    Three cases, and the middle one is the correction adversarial review found
    2026-08-20.

    * a container this act CREATED closes on the day the bank took the money it
      holds;
    * a container an EARLIER LINE OF THIS SAME PRESS created closes on the
      LATEST of those days.  Measured before this arm existed: two lines in one
      pay period submitted 01-05 then 01-09 left the envelope recording that it
      closed on **2024-01-05 while holding `$45.00` the bank did not take until
      01-09** -- and submitting them the other way round recorded 01-09, so the
      close day was whichever line happened to be filed first.  No figure moved
      (each purchase carries its own posting day, so the envelope's own cash leg
      is `0.00` either way), but a row may not record closing before money it
      holds.  **The LATEST is this arc's own rule for a group** -- a match's day
      is ``max(posted_on)`` over its lines (``MatchDays.of``) -- applied to the
      group a press files;
    * a container that ALREADY EXISTED keeps its own close day.  That is the
      shipped behaviour of the destination arm and it is deliberate: that row's
      close is a record the OWNER made, and re-dating it would edit their
      record rather than complete this press's own.

    Args:
        creation: What the owner submitted, which says which arm this is.
        observed: The bank line's own posting day, as a day a statement SHOWED
            (:func:`~._create._observed`).  **The DAY rather than the line, and
            that is what the split at plan step ``bank_import:X-ge`` made
            structural**: this decision needs one fact about the bank line and
            nothing else, so taking the ORM row would be a container module
            reaching into what a purchase is -- and it is what let the two
            modules cut apart without an import cycle.  It is built once per
            act by the door, which is also what stopped this function and the
            purchase it sits beside constructing the same value twice.
        envelope: The container the purchase goes in.
        created: Whether THIS act created it.

    Returns:
        The day to close on and HOW that day is known
        (:class:`app.services.settle_day.SettleDay`), or ``None`` when the close
        is not this act's to write.  The basis is always ``observed`` (plan step
        **X-az**): every day this function can return is a bank line's own
        posting day, so a statement is what showed it.
    """
    if created:
        return observed
    if creation.transaction_id is not None:
        return None
    # A line joining the row an earlier line of this press minted or placed
    # -- the new-envelope arm's, or the PLACE arm's, second line in one
    # paycheck (leaf 7b-3 of balance:X-bi-7b).
    if envelope.settled_on is not None and observed.day <= envelope.settled_on:
        return None
    return observed


def close_container(
    creation: PurchaseCreation,
    observed: SettleDay,
    envelope: Transaction,
    created: bool,
) -> None:
    """Close the container on the day the bank took the money it now holds.

    :func:`_close_day` decides WHETHER and WHICH day; this applies it, through
    whichever verb the container's own state calls for.  Extracted from
    :func:`create_purchase_from_line` at plan step ``bank_import:X-ga``, when
    ruling **R-GJ**'s bar took that function past ``max-locals``: the two
    honest answers to a design limit are to decompose or to disable, and this
    block was already one coherent act with one subject -- everything a
    container's CLOSE needs, and nothing the purchase needs.

    Args:
        creation: What the owner submitted, which says which arm this is.
        observed: The bank line's own posting day, as a day a statement SHOWED,
            for the reason :func:`_close_day` states.
        envelope: The container the purchase went in.
        created: Whether THIS act created it.
    """
    close_on = _close_day(creation, observed, envelope, created)
    if close_on is not None and not created:
        # **A container an EARLIER LINE OF THIS PRESS made, closing again on a
        # later day.**  ``settle_from_entries`` refuses an already-settled row
        # by design -- "the seam owns the day, and a caller that genuinely
        # means *this settled on a different day* corrects it on the row
        # afterwards" (ruling **R-ED**) -- so the correction goes through the
        # same identity transition ``_moving._apply_day`` uses for a settled
        # row a match re-dates: the row's OWN status, a new day.  One verb for
        # "a settled row's day moved", not a second one here.
        transaction_service.apply_requested_status(
            envelope, envelope.status_id, settle_day=close_on,
        )
    elif close_on is not None:
        # **A row created to hold something that has already happened says
        # so.**  A first version justified this by calling a Projected `$0.00`
        # row "carry-forward bait that would roll a NEGATIVE leftover", which is
        # measurably FALSE -- ``carry_forward_service`` clamps its leftover with
        # ``max(Decimal("0"), budget - entries)`` in both its preview and its
        # execute arm.  Found by adversarial design review 2026-08-19.
        #
        # The real reason is narrower and holds: this row records money that has
        # ALREADY left the account, and the app's vocabulary for that is the
        # settled band -- left Projected it would read on the grid as an unpaid
        # item for money already gone.  Carry-forward would settle it through
        # this very verb anyway; doing it here dates the close on the day the
        # bank posted, where carry-forward would date it whenever it next ran.
        transaction_service.settle_from_entries(
            envelope, settle_day=close_on,
        )


@dataclass(frozen=True)
class ActReads:
    """What ONE create act resolves its destination against.

    Three reads the door already holds, bundled because the resolver is
    PUBLIC and its arms need all three (this project's remedy for a public
    function over the argument bound).  Each is derived once and threaded,
    which is the rule :class:`MintedEnvelopes` states for itself.

    Attributes:
        scope: The pass (:class:`~._scope.ReviewScope`).
        minted: What this REQUEST has already minted or placed.
        placeable: The rule-less definitions a PLACE creation may name
            (:attr:`~._rules.RuleView.placeable_templates`).
    """

    scope: ReviewScope
    minted: MintedEnvelopes
    placeable: "frozenset[int]"


def resolve_destination(
    creation: PurchaseCreation,
    period: DerivedPeriod,
    act: ActReads,
) -> "tuple[Transaction, bool]":
    """Return the budget line this purchase goes in, and whether we made it.

    The arms of ruling **R-FX**, resolved in one place: an envelope the owner
    picked from the set the screen offers, one this door creates for the
    line, or -- since leaf 7b-3 of ``balance:X-bi-7b`` (ruling **R-BAL24**)
    -- a row of a rule-less definition PLACED in the line's period.
    :func:`reject_ambiguous_destination` has already refused a submission
    naming two arms or none, so the branch below is a dispatch rather than a
    preference.

    Args:
        creation: What the owner submitted.
        period: The paycheck holding the day the purchase was made, as the
            pass derived it -- the DERIVED period rather than its id since
            leaf 7b-3, because the one-off producer places a row by the
            paycheck's start and re-resolving that from an id would be a
            second derivation in one request.
        act: The three reads this act resolves against (:class:`ActReads`).

    Returns:
        ``(envelope, created)`` -- the row, and whether this act made it.

    Raises:
        ValidationError: When the named envelope is not one the screen could
            have offered, the named category is not this owner's, the named
            definition is not one the pass could offer to place, or the
            paycheck already holds its row.
    """
    if creation.transaction_id is not None:
        return (
            _existing_envelope(creation, period.period_id, act.scope),
            False,
        )
    if creation.template_id is not None:
        return _placed_or_held(
            _placeable_definition(creation, act.placeable), period,
            act.scope, act.minted,
        )
    return _minted_or_new(
        creation, _owned_category(creation, act.scope), period, act.scope,
        act.minted,
    )
